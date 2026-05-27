"""Pure helpers backing the Web UI Setup view + ``merge --ci`` first-run.

Public entry points:
- ``apply_setup_payload(payload, repo_path)`` — write ``.merge/config.yaml``
  and ``.merge/.env`` from a validated ``SetupPayload`` (Web UI submit
  or ``build_default_payload`` synthetic). Pure I/O — no prompts.
- ``detect_setup_context(repo_path)`` — assemble the data the Web UI
  needs to pre-fill its Setup form (current branch, suggested target,
  masked existing API keys, fork-divergence count, existing config
  summary).
- ``build_default_payload(repo_path)`` — synthesise a ``SetupPayload``
  for ``merge --ci`` when no ``.merge/config.yaml`` exists yet, so CI
  never blocks on prompts.
- ``draft_forks_profile_file(...)`` — one-shot helper reused by the
  launcher.

The previous terminal-interactive wizard (``_interactive_setup`` /
``_repeat_run_flow`` / ``detect_or_setup``) was removed in PR-3 once
the browser took over the first-run flow. ``_ask`` / ``_confirm``
remain because ``init_context.py`` still needs them.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console

from src.cli.env import read_env_file, write_env_file
from src.cli.paths import (
    ensure_merge_dir,
    get_config_path,
    get_forks_profile_path,
    get_global_config_path,
    get_global_env_path,
    get_project_merge_dir,
)
from src.models.config import MergeConfig
from src.models.setup import (
    ApiKeyHintSource,
    ModelParams,
    ProviderConfig,
    ProviderName,
    SetupContext,
    SetupPayload,
)
from src.web.config_schema import build_config_schema

console = Console()


# --- Provider / agent inventory --------------------------------------------

# Ordered list of agent roles the orchestrator drives. The setup form
# renders one row per entry under AGENT OVERRIDES so the user can opt
# any of them off the default provider. Keep in sync with
# ``MergeConfig.agents`` field names.
AGENT_INVENTORY: list[dict[str, str]] = [
    {"name": "planner", "blurb": "produces the merge plan"},
    {"name": "planner_judge", "blurb": "reviews / negotiates the plan"},
    {"name": "conflict_analyst", "blurb": "analyses conflict semantics"},
    {"name": "executor", "blurb": "applies patches"},
    {"name": "judge", "blurb": "post-merge verdict"},
    {"name": "human_interface", "blurb": "summarises human prompts"},
]

PROVIDER_API_KEY_ENV: dict[ProviderName, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}
PROVIDER_BASE_URL_ENV: dict[ProviderName, str] = {
    "anthropic": "ANTHROPIC_BASE_URL",
    "openai": "OPENAI_BASE_URL",
}
# Env var holding a comma-separated list of models the user previously
# saved for each provider. Same resolution chain as API keys (shell >
# project .env > global .env); when present, ``detect_setup_context``
# uses it to seed the UI's "available models" textarea so a
# device-level edit to ``~/.config/code-merge-system/.env`` propagates
# to every project. Falls back to ``PROVIDER_RECOMMENDED_MODELS`` when
# unset.
PROVIDER_MODELS_ENV: dict[ProviderName, str] = {
    "anthropic": "ANTHROPIC_MODELS",
    "openai": "OPENAI_MODELS",
}

# Suggested model dropdown source for each provider — populated into
# ``SetupContext.provider_recommended_models``. Users can also type a
# custom model name; the resolver doesn't validate against this list.
PROVIDER_RECOMMENDED_MODELS: dict[ProviderName, list[str]] = {
    "anthropic": [
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
    ],
    "openai": [
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-4o",
    ],
}

# UI-side pre-fill hints for the AGENT OVERRIDES table. Maps
# ``(provider, agent_name)`` to the model the form should pick when
# pre-populating that row. NOT a runtime fallback — the resolver still
# uses ``default_provider.models[0]`` for unassigned agents. This
# table only tells the UI "if both opus and haiku are configured for
# Anthropic, prefer haiku for human_interface". The UI only consults
# the hint when the recommended model actually appears in the
# configured ``provider.models`` list; otherwise it falls back to
# ``models[0]``.
RECOMMENDED_AGENT_MODELS: dict[tuple[ProviderName, str], str] = {
    ("anthropic", "planner"): "claude-opus-4-7",
    ("anthropic", "planner_judge"): "claude-opus-4-7",
    ("anthropic", "conflict_analyst"): "claude-opus-4-7",
    ("anthropic", "executor"): "claude-opus-4-7",
    ("anthropic", "judge"): "claude-opus-4-7",
    ("anthropic", "human_interface"): "claude-haiku-4-5-20251001",
    ("openai", "planner"): "gpt-5.4",
    ("openai", "planner_judge"): "gpt-5.4",
    ("openai", "conflict_analyst"): "gpt-5.4",
    ("openai", "executor"): "gpt-5.4",
    ("openai", "judge"): "gpt-5.4",
    ("openai", "human_interface"): "gpt-5.4-mini",
}


def _build_recommended_agent_models() -> dict[str, dict[str, str]]:
    """Flatten ``RECOMMENDED_AGENT_MODELS`` into the dict-of-dicts shape
    the SetupContext exposes to the UI."""
    out: dict[str, dict[str, str]] = {}
    for (provider, agent), model in RECOMMENDED_AGENT_MODELS.items():
        out.setdefault(provider, {})[agent] = model
    return out


def recommended_model_params(model: str) -> ModelParams:
    """Recommended per-model LLM tuning, by model-family prefix.

    Mirrors the UI's ``recommendedModelParams`` (``web/src/lib/modelParams.ts``)
    so a model the user never edited resolves to the same values on both
    sides. Calibrated for the current Claude / OpenAI families; the UI lets
    the user override any of these per model. Used only when a model is
    absent from ``payload.model_params`` (the UI normally sends an explicit
    entry for every configured model).
    """
    m = model.lower()
    # OpenAI reasoning-class models need a large completion budget.
    if m.startswith(("gpt-5", "o1", "o3", "o4")):
        return ModelParams(max_tokens=32768, temperature=0.2, max_retries=3)
    if "haiku" in m:
        return ModelParams(max_tokens=4096, temperature=0.2, max_retries=3)
    # Claude opus/sonnet, gpt-4o, and everything else.
    return ModelParams(max_tokens=8192, temperature=0.2, max_retries=3)


def _params_for(payload: SetupPayload, model: str) -> ModelParams:
    """Resolve a model's params: the user-supplied entry when present,
    otherwise the recommended default for that model family."""
    return payload.model_params.get(model) or recommended_model_params(model)


def _auto_detect_fork_ref(repo_path: str) -> str:
    """Return the current git branch name, falling back to 'origin/main'."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        branch = result.stdout.strip()
        if branch and branch != "HEAD":
            return branch
    except Exception:
        pass
    return "origin/main"


def _build_agents_block(payload: SetupPayload) -> dict[str, dict[str, Any]]:
    """Translate per-agent provider/model choices into the config.yaml block.

    Each agent in ``AGENT_INVENTORY`` lands as one entry. Resolution
    per agent:

    - ``payload.agent_choices[name]`` if present — use its
      ``(provider, model)`` directly (validators have already ensured
      the model exists in the provider's ``models`` list).
    - Otherwise inherit ``payload.default_provider`` and use that
      provider's ``models[0]`` as the model. That's why the UI keeps
      user ordering in the textarea: the first line is the implicit
      default for every unassigned agent.
    """
    out: dict[str, dict[str, Any]] = {}
    default_provider = payload.default_provider
    assert default_provider is not None, "payload validator guarantees this"
    default_cfg = (
        payload.anthropic if default_provider == "anthropic" else payload.openai
    )
    default_model = default_cfg.models[0]

    def block_for(prov: ProviderName, model: str) -> dict[str, Any]:
        # Per-model tuning: every agent (and fallback) on a given model
        # shares its (max_tokens, temperature, max_retries) — resolved from
        # the user's model_params, else the recommended default.
        params = _params_for(payload, model)
        return {
            "provider": prov,
            "model": model,
            "api_key_env": PROVIDER_API_KEY_ENV[prov],
            "max_tokens": params.max_tokens,
            "temperature": params.temperature,
            "max_retries": params.max_retries,
        }

    enabled = payload._enabled_providers()
    cross_provider = len(enabled) >= 2

    # Cross-provider circuit-breaker fallback: with both providers enabled,
    # every agent falls back to the *other* provider so a single provider
    # outage (429 storm, downed gateway, deprecated model id) can't stall
    # the run. Agents on the default provider take the user-selected
    # ``payload.fallback`` (defaulting to the non-default provider's first
    # model); agents already on the non-default provider take the reverse
    # direction back to the default. With one provider enabled there is
    # nothing to cross-fall to, so we keep the legacy same-provider model
    # fallback (the only failure a lone provider can recover from).
    primary_fb: dict[str, Any] | None = None
    reverse_fb: dict[str, Any] | None = None
    if cross_provider:
        other_provider = next(p for p in enabled if p != default_provider)
        other_cfg = (
            payload.anthropic if other_provider == "anthropic" else payload.openai
        )
        fb_provider = payload.fallback.provider if payload.fallback else other_provider
        fb_model = payload.fallback.model if payload.fallback else other_cfg.models[0]
        primary_fb = block_for(fb_provider, fb_model)
        reverse_fb = block_for(default_provider, default_model)

    for entry in AGENT_INVENTORY:
        name = entry["name"]
        choice = payload.agent_choices.get(name)
        if choice is not None:
            provider = choice.provider
            model = choice.model
        else:
            provider = default_provider
            model = default_model

        block = block_for(provider, model)

        if cross_provider:
            fb = primary_fb if provider == default_provider else reverse_fb
            # Never attach a self-pointing fallback (same provider + model),
            # which would just retry the broken config.
            if fb is not None and (fb["provider"], fb["model"]) != (provider, model):
                block["fallback"] = dict(fb)
        elif (provider, model) != (default_provider, default_model):
            block["fallback"] = block_for(default_provider, default_model)

        out[name] = block
    return out


ENABLE_WORKING_BRANCH_HINT: str = (
    "推荐：每 run 隔离写入，避免 fork_ref 被半完成状态污染"
    " (worktree isolation; matches MergeConfig.enable_working_branch default)"
)


def _default_config_data(payload: SetupPayload, repo_path: str) -> dict[str, Any]:
    """Build the ``.merge/config.yaml`` dict from a validated payload.

    ``thresholds`` here holds the *defaults*; the caller layers
    ``payload.thresholds`` overrides on top after global defaults are
    merged in (otherwise the global defaults would silently beat a
    user-supplied override).
    """
    data: dict[str, Any] = {
        "upstream_ref": payload.target_branch,
        "fork_ref": payload.fork_ref,
        "working_branch": "merge/auto-{timestamp}",
        # U7: worktree isolation defaults on so a half-finished run never
        # pollutes fork_ref HEAD. Matches MergeConfig.enable_working_branch
        # default; see ENABLE_WORKING_BRANCH_HINT for the user-facing rationale.
        "enable_working_branch": True,
        "repo_path": repo_path,
        "project_context": payload.project_context,
        "max_files_per_run": 500,
        "max_plan_revision_rounds": 2,
        "agents": _build_agents_block(payload),
        "thresholds": {
            "auto_merge_confidence": 0.85,
            "human_escalation": 0.60,
            "risk_score_low": 0.30,
            "risk_score_high": 0.60,
        },
        "llm_assist": {"mode": "auto"},
        "output": {
            "directory": "./outputs",
            "formats": ["json", "markdown"],
        },
    }
    if payload.request_timeout_seconds is not None:
        data["request_timeout_seconds"] = payload.request_timeout_seconds
    return data


def _collect_env_writes(payload: SetupPayload) -> dict[str, str]:
    """Collect the per-provider env vars to merge into ``.merge/.env``.

    Only writes keys the user actually supplied in this submit (blank
    fields mean "keep whatever is on disk / shell"). Includes
    ``*_BASE_URL`` when set so enterprise gateway routing survives a
    reconfigure, and ``GITHUB_TOKEN`` when supplied. Disabled providers
    contribute nothing — the user explicitly turned them off, so we
    don't leave their old key around to confuse the agent selection.
    """
    out: dict[str, str] = {}
    providers: list[tuple[ProviderName, ProviderConfig]] = [
        ("anthropic", payload.anthropic),
        ("openai", payload.openai),
    ]
    for provider, cfg in providers:
        if not cfg.enabled:
            continue
        if cfg.api_key:
            out[PROVIDER_API_KEY_ENV[provider]] = cfg.api_key
        if cfg.base_url:
            out[PROVIDER_BASE_URL_ENV[provider]] = cfg.base_url
        # Persist the user's models list under the same .env chain.
        # Comma-joined so the value stays single-line for plain `.env`
        # parsers; ``detect_setup_context`` splits on both comma and
        # whitespace when reading it back.
        if cfg.models:
            out[PROVIDER_MODELS_ENV[provider]] = ",".join(cfg.models)
    if payload.github_token:
        out["GITHUB_TOKEN"] = payload.github_token
    return out


def _resolve_env_models(provider: ProviderName, repo_path: str) -> list[str] | None:
    """Read ``<PROVIDER>_MODELS`` from the env chain, if set.

    Returns the parsed list (comma- or whitespace-separated, dedup
    preserved-order) when the env var resolves to a non-empty value;
    returns ``None`` otherwise so the caller can fall back to the
    hardcoded ``PROVIDER_RECOMMENDED_MODELS``.
    """
    raw = _resolve_env_value(PROVIDER_MODELS_ENV[provider], repo_path)
    if not raw:
        return None
    seen: set[str] = set()
    parsed: list[str] = []
    for token in raw.replace("\n", ",").split(","):
        name = token.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        parsed.append(name)
    return parsed or None


def apply_setup_payload(payload: SetupPayload, repo_path: str = ".") -> MergeConfig:
    """Persist ``payload`` to ``.merge/config.yaml`` + ``.merge/.env`` and return the validated config.

    Pure I/O — no ``input()``, no Rich prompts. Safe to call from the
    Web UI WS handler and from the ``merge --ci`` first-run fallback in
    ``build_default_payload`` → ``apply_setup_payload``.

    Runtime-only hints on the payload (``dry_run`` / ``workflow`` /
    ``init_forks_profile``) are *not* written to ``config.yaml``; the
    caller (orchestrator launcher) consumes them separately so they
    stay session-scoped.
    """
    ensure_merge_dir(repo_path)

    env_writes = _collect_env_writes(payload)
    if env_writes:
        env_path = get_project_merge_dir(repo_path) / ".env"
        write_env_file(env_path, env_writes)
        for k, v in env_writes.items():
            # ``setdefault`` not ``[k] = v`` so a freshly supplied key
            # doesn't clobber an existing shell env var (the run-time
            # priority chain is shell > project_env > global_env).
            os.environ.setdefault(k, v)

    config_data = _default_config_data(payload, repo_path)

    global_defaults = _load_global_defaults()
    if global_defaults:
        config_data = _deep_merge_dicts(config_data, global_defaults)

    if payload.thresholds is not None:
        explicit = {
            k: v for k, v in payload.thresholds.model_dump().items() if v is not None
        }
        if explicit:
            config_data.setdefault("thresholds", {}).update(explicit)

    if payload.llm_assist_mode is not None:
        config_data.setdefault("llm_assist", {})["mode"] = payload.llm_assist_mode

    # Comprehensive-editor overrides win over the generated skeleton. They
    # carry only non-curated keys (the UI excludes provider/agents/core
    # thresholds), so this never clobbers the curated build above.
    if payload.config_overrides:
        config_data = _deep_merge_dicts(config_data, payload.config_overrides)

    # Validate *before* writing so an out-of-range override surfaces as a
    # ValidationError (→ setup_error in the WS handler) without leaving an
    # invalid config.yaml on disk.
    config = MergeConfig.model_validate(config_data)

    config_path = get_config_path(repo_path)
    config_path.write_text(
        yaml.dump(config_data, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )

    return config


def load_current_config_values(repo_path: str = ".") -> dict[str, Any]:
    """Return the current ``.merge/config.yaml`` as a dict (``{}`` when absent
    or unreadable).

    Feeds the Web UI's comprehensive config editor so it can pre-fill every
    field with the persisted value, falling back to the schema default for
    keys the file omits. Best-effort — a malformed file yields ``{}`` rather
    than aborting the Setup snapshot."""
    path = get_config_path(repo_path)
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def detect_setup_context(repo_path: str = ".") -> SetupContext:
    """Collect pre-fill data the Web UI's Setup view needs in one call.

    All I/O is best-effort: git failures fall back to ``"origin/main"``
    for the suggested target and ``0`` for the divergence count. The
    wizard must never abort because a fresh clone lacks an upstream
    remote — the user fills the missing pieces in the form.
    """
    current_branch = _auto_detect_fork_ref(repo_path)
    suggested_target = _detect_upstream_default(repo_path)
    anthropic_hint = _api_key_hint("ANTHROPIC_API_KEY", repo_path)
    openai_hint = _api_key_hint("OPENAI_API_KEY", repo_path)
    github_hint = _api_key_hint("GITHUB_TOKEN", repo_path)
    anthropic_base = _resolve_env_value("ANTHROPIC_BASE_URL", repo_path)
    openai_base = _resolve_env_value("OPENAI_BASE_URL", repo_path)

    has_existing_config = get_config_path(repo_path).exists()
    has_project_env = (get_project_merge_dir(repo_path) / ".env").exists()
    existing_config_summary: dict[str, Any] | None = None
    if has_existing_config:
        try:
            raw = yaml.safe_load(get_config_path(repo_path).read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                existing_config_summary = {
                    "upstream_ref": raw.get("upstream_ref"),
                    "fork_ref": raw.get("fork_ref"),
                    "project_context": raw.get("project_context", ""),
                    "thresholds": raw.get("thresholds", {}),
                    "agents": raw.get("agents", {}),
                    "request_timeout_seconds": raw.get("request_timeout_seconds"),
                }
        except Exception:
            existing_config_summary = None

    divergence = _count_fork_deleted_files(suggested_target, current_branch, repo_path)
    has_global_env = get_global_env_path().exists()

    # Models follow the same resolution chain as keys / base URLs: when
    # the user has previously saved a list (or hand-edited it into
    # ``~/.config/code-merge-system/.env``) that wins over the
    # hardcoded recommendation so a device-level edit propagates.
    recommended_models: dict[str, list[str]] = {}
    for provider in ("anthropic", "openai"):
        env_models = _resolve_env_models(provider, repo_path)
        recommended_models[provider] = (
            env_models
            if env_models is not None
            else list(PROVIDER_RECOMMENDED_MODELS[provider])
        )

    return SetupContext(
        current_branch=current_branch,
        suggested_target=suggested_target,
        anthropic_key_hint=anthropic_hint,
        openai_key_hint=openai_hint,
        github_token_hint=github_hint,
        anthropic_base_url=anthropic_base,
        openai_base_url=openai_base,
        provider_recommended_models=recommended_models,
        agent_inventory=[dict(entry) for entry in AGENT_INVENTORY],
        recommended_agent_models=_build_recommended_agent_models(),
        fork_divergence_count=divergence,
        has_existing_config=has_existing_config,
        existing_config_summary=existing_config_summary,
        forks_profile_threshold=FORKS_PROFILE_INIT_THRESHOLD,
        has_global_env=has_global_env,
        has_project_env=has_project_env,
        config_schema=build_config_schema().model_dump(mode="json"),
        config_values=load_current_config_values(repo_path),
    )


def build_default_payload(repo_path: str = ".") -> SetupPayload:
    """Synthesise a SetupPayload for ``merge --ci`` first-run.

    Used when the user runs ``merge --ci`` and no ``.merge/config.yaml``
    exists yet: we cannot prompt, so we pick safe defaults from env
    vars + git. Whichever provider has an API key in the env gets
    enabled; if both are present we pick ``anthropic`` as
    ``default_provider``. If neither is present we still enable
    ``anthropic`` so ``apply_setup_payload`` writes a runnable
    config — the operator gets a clear error from the LLM client on
    the next run instead of a confusing setup failure.
    """
    current_branch = _auto_detect_fork_ref(repo_path)
    suggested_target = _detect_upstream_default(repo_path)

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    github_token = os.environ.get("GITHUB_TOKEN", "")

    has_anthropic = bool(anthropic_key)
    has_openai = bool(openai_key)
    if not has_anthropic and not has_openai:
        # Neither key in env — default to enabling anthropic so the
        # payload validates and we produce a config skeleton the user
        # can fix up.
        has_anthropic = True

    default_provider: ProviderName = "anthropic" if has_anthropic else "openai"

    return SetupPayload(
        target_branch=suggested_target,
        fork_ref=current_branch,
        project_context="",
        anthropic=ProviderConfig(
            enabled=has_anthropic,
            api_key=anthropic_key,
            base_url=os.environ.get("ANTHROPIC_BASE_URL") or None,
            # CI first-run pre-seeds the recommended model list so the
            # payload validates ( enabled provider must have ≥1 model )
            # without forcing the operator to edit yaml before the
            # next run.
            models=list(PROVIDER_RECOMMENDED_MODELS["anthropic"])
            if has_anthropic
            else [],
        ),
        openai=ProviderConfig(
            enabled=has_openai,
            api_key=openai_key,
            base_url=os.environ.get("OPENAI_BASE_URL") or None,
            models=list(PROVIDER_RECOMMENDED_MODELS["openai"]) if has_openai else [],
        ),
        github_token=github_token,
        default_provider=default_provider,
        agent_choices={},
        thresholds=None,
        dry_run=False,
        workflow=None,
        init_forks_profile=False,
    )


def _detect_upstream_default(repo_path: str) -> str:
    """Return ``origin/<HEAD branch>`` from ``git remote show``, or ``origin/main``.

    ``git remote show origin`` is the canonical place where the
    remote's default branch (HEAD) is recorded; it survives renames of
    ``main``/``master`` on the remote without requiring a local fetch.
    On any failure (no remote, network-less env, parse error) the
    fallback keeps the wizard moving — the user can edit the field.
    """
    try:
        result = subprocess.run(
            ["git", "remote", "show", "origin"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except Exception:
        return "origin/main"
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("HEAD branch:"):
            branch = line.split(":", 1)[1].strip()
            if branch and branch != "(unknown)":
                return f"origin/{branch}"
    return "origin/main"


def _resolved_env_chain(repo_path: str) -> tuple[dict[str, str], dict[str, str]]:
    """Read project + global ``.env`` files once for ``_api_key_hint`` reuse."""
    global_env = get_global_env_path()
    global_entries = read_env_file(global_env) if global_env.exists() else {}

    project_env = get_project_merge_dir(repo_path) / ".env"
    project_entries = read_env_file(project_env) if project_env.exists() else {}
    return project_entries, global_entries


def _api_key_hint(name: str, repo_path: str) -> ApiKeyHintSource:
    """Return one masked-key hint with the source label.

    Priority: shell env > project ``.env`` > global ``.env``. Identical
    to the run-time loader chain so the UI shows whichever value the
    run will actually pick up.
    """
    project_entries, global_entries = _resolved_env_chain(repo_path)
    if os.environ.get(name):
        return ApiKeyHintSource(
            name=name, masked=_mask_key(os.environ[name]), source="shell"
        )
    if name in project_entries:
        return ApiKeyHintSource(
            name=name, masked=_mask_key(project_entries[name]), source="project_env"
        )
    if name in global_entries:
        return ApiKeyHintSource(
            name=name, masked=_mask_key(global_entries[name]), source="global_env"
        )
    return ApiKeyHintSource(name=name, masked="", source="")


def _resolve_env_value(name: str, repo_path: str) -> str | None:
    """Look up the resolved value of a non-key env var (e.g. ``*_BASE_URL``).

    Same priority chain as ``_api_key_hint`` but returns the literal
    string so the form can pre-fill the input box. Used to round-trip
    ``ANTHROPIC_BASE_URL`` / ``OPENAI_BASE_URL`` through Setup.
    """
    if os.environ.get(name):
        return os.environ[name]
    project_entries, global_entries = _resolved_env_chain(repo_path)
    if name in project_entries:
        return project_entries[name]
    if name in global_entries:
        return global_entries[name]
    return None


def _count_fork_deleted_files(upstream_ref: str, fork_ref: str, repo_path: str) -> int:
    """Cheap signal of fork divergence: # files the fork removed since merge-base.

    Same query the post-wizard forks-profile prompt uses; lifted here
    so the Web UI can decide upfront whether to surface the
    "Initialize forks-profile" checkbox without a second round-trip.
    """
    try:
        merge_base = subprocess.run(
            ["git", "merge-base", upstream_ref, fork_ref],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
        if not merge_base:
            return 0
        deleted = subprocess.run(
            [
                "git",
                "diff",
                "--diff-filter=D",
                "--name-only",
                f"{merge_base}..{fork_ref}",
            ],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
    except Exception:
        return 0
    return sum(1 for line in deleted.splitlines() if line.strip())


def _ask(prompt: str, default: str = "", show_default: bool = True) -> str:
    """Read a line via stdlib ``input()`` so readline tracks prompt width.

    Why not Rich's ``Prompt.ask``: Rich pre-prints the prompt with
    ``console.print(end="")`` and then calls ``input("")``; readline does
    not know the prompt's width, so Ctrl+U / Ctrl+W / cursor redraw
    miscompute "column 0" and erase the visible prompt characters.
    Passing the rendered prompt directly to ``input()`` gives readline
    the width it needs and confines line-editing to the user buffer.

    Visual format mirrors Rich: ``"X (default): "`` when ``show_default``
    is true and ``default`` is non-empty, otherwise ``"X: "``.
    """
    suffix = f" ({default})" if show_default and default else ""
    rendered = f"{prompt}{suffix}: "
    try:
        value = input(rendered)
    except EOFError:
        return default
    return value or default


def _confirm(prompt: str, default: bool = True) -> bool:
    """Yes/no confirmation via stdlib ``input()`` (readline-safe).

    Rendered as ``"X [Y/n]: "`` (default=True) or ``"X [y/N]: "``
    (default=False). Empty input picks the default; a leading ``y``/``Y``
    is true and ``n``/``N`` is false; anything else re-prompts.
    """
    yn = "[Y/n]" if default else "[y/N]"
    rendered = f"{prompt} {yn}: "
    while True:
        try:
            raw = input(rendered).strip().lower()
        except EOFError:
            return default
        if not raw:
            return default
        if raw[0] == "y":
            return True
        if raw[0] == "n":
            return False


def _mask_key(key: str) -> str:
    if len(key) <= 8:
        return "****"
    return key[:4] + "****" + key[-4:]


_GLOBAL_CONFIG_WHITELIST = frozenset(
    {
        "llm",
        "agents",
        "thresholds",
        "max_files_per_run",
        "max_plan_revision_rounds",
        "output",
    }
)


def _load_global_defaults() -> dict[str, Any]:
    """Load whitelisted defaults from ``~/.config/code-merge-system/config.yaml``.

    Returns ``{}`` when the file is missing, malformed, or contains no
    whitelisted keys. Unknown top-level keys are dropped with a warning so
    the wizard never silently honors a typo (e.g. a stray ``fork_ref``)
    that the user expected to take effect.
    """
    path = get_global_config_path()
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as e:
        console.print(
            f"  [yellow]Warning: global config {path} unreadable ({e}); "
            f"skipping.[/yellow]"
        )
        return {}
    if not isinstance(raw, dict):
        return {}

    filtered: dict[str, Any] = {}
    dropped: list[str] = []
    for key, value in raw.items():
        if key in _GLOBAL_CONFIG_WHITELIST:
            filtered[key] = value
        else:
            dropped.append(key)
    if dropped:
        console.print(
            f"  [yellow]Global config: ignoring non-whitelisted keys "
            f"{sorted(dropped)} (allowed: {sorted(_GLOBAL_CONFIG_WHITELIST)}).[/yellow]"
        )
    if filtered:
        console.print(
            f"  [green]Loaded global defaults from:[/green] {path} "
            f"(keys: {sorted(filtered)})"
        )
    return filtered


def _deep_merge_dicts(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursive dict merge — overlay wins, nested dicts are merged not replaced.

    Non-dict values (lists, scalars) at a given key are replaced wholesale
    by the overlay value. New keys from the overlay are added.
    """
    result = dict(base)
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge_dicts(result[key], value)
        else:
            result[key] = value
    return result


FORKS_PROFILE_INIT_THRESHOLD = 30
"""Minimum count of fork-deleted files required before offering an init.

Calibrated against historical merge reports: forks with ~30+ fork-deleted
files materially benefited from a forks-profile.yaml (fewer judge false
positives on removed domains), while forks below that threshold were
adequately served by the auto overlay (PR-A) alone — a yaml there is
just maintenance burden, so the wizard stays silent.
"""


def draft_forks_profile_file(
    target_branch: str,
    fork_ref: str,
    repo_path: str = ".",
) -> Path | None:
    """Non-interactive forks-profile drafter — used by the Web UI launcher.

    Same drafter as ``_draft_and_open_editor`` minus the ``$EDITOR``
    prompt: we run the diff, render the yaml, and write it to
    ``.merge/forks-profile.yaml``. Returns the output path on success,
    ``None`` if a profile already exists (we never overwrite). Any
    git / IO failure raises so the caller can decide whether to
    surface it — the WebUI launcher catches and logs to keep the
    merge run alive.
    """
    import datetime as _dt

    from src.tools.forks_profile_drafter import (
        draft_profile,
        render_profile_yaml,
    )
    from src.tools.git_tool import GitTool

    profile_path = get_forks_profile_path(repo_path)
    if profile_path.exists():
        return None

    git_tool = GitTool(repo_path)
    merge_base = git_tool.get_merge_base(target_branch, fork_ref)
    drafted = draft_profile(
        git_tool,
        upstream_ref=target_branch,
        fork_ref=fork_ref,
        merge_base=merge_base,
    )
    text = render_profile_yaml(drafted, today=_dt.date.today().isoformat())

    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(text, encoding="utf-8")
    return profile_path
