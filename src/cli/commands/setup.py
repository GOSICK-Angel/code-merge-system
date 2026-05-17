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
- ``draft_forks_profile_file(...)`` / ``migrate_merge_record(...)`` —
  one-shot helpers reused by the launcher.

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
    ApiKeyHint,
    SetupContext,
    SetupPayload,
)

console = Console()


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


def _default_config_data(payload: SetupPayload, repo_path: str) -> dict[str, Any]:
    """Build the ``.merge/config.yaml`` dict from a validated payload.

    Mirrors the literal block previously inlined in ``_interactive_setup``
    so the wizard and the Web UI / ``--ci`` fallback all produce
    byte-identical yaml. ``thresholds`` here holds the *defaults*; the
    caller layers ``payload.thresholds`` overrides on top after global
    defaults are merged in (otherwise the global defaults would silently
    beat a user-supplied override).
    """
    return {
        "upstream_ref": payload.target_branch,
        "fork_ref": payload.fork_ref,
        "working_branch": "merge/auto-{timestamp}",
        "enable_working_branch": False,
        "repo_path": repo_path,
        "project_context": payload.project_context,
        "max_files_per_run": 500,
        "max_plan_revision_rounds": 2,
        "agents": {
            "planner": {
                "provider": "anthropic",
                "model": "claude-opus-4-6",
                "api_key_env": "ANTHROPIC_API_KEY",
            },
            "planner_judge": {
                "provider": "openai",
                "model": "gpt-5.4",
                "api_key_env": "OPENAI_API_KEY",
            },
            "conflict_analyst": {
                "provider": "anthropic",
                "model": "claude-opus-4-6",
                "api_key_env": "ANTHROPIC_API_KEY",
            },
            "executor": {
                "provider": "openai",
                "model": "gpt-5.4",
                "temperature": 0.1,
                "api_key_env": "OPENAI_API_KEY",
            },
            "judge": {
                "provider": "anthropic",
                "model": "claude-opus-4-6",
                "temperature": 0.1,
                "api_key_env": "ANTHROPIC_API_KEY",
            },
            "human_interface": {
                "provider": "anthropic",
                "model": "claude-haiku-4-5-20251001",
                "api_key_env": "ANTHROPIC_API_KEY",
            },
        },
        "thresholds": {
            "auto_merge_confidence": 0.85,
            "human_escalation": 0.60,
            "risk_score_low": 0.30,
            "risk_score_high": 0.60,
        },
        "output": {
            "directory": "./outputs",
            "formats": ["json", "markdown"],
        },
    }


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

    if payload.api_keys:
        env_path = get_project_merge_dir(repo_path) / ".env"
        write_env_file(env_path, payload.api_keys)
        for k, v in payload.api_keys.items():
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

    if "GITHUB_TOKEN" in payload.api_keys:
        config_data["github"] = {"enabled": True, "token_env": "GITHUB_TOKEN"}

    config_path = get_config_path(repo_path)
    config_path.write_text(
        yaml.dump(config_data, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )

    return MergeConfig.model_validate(config_data)


def detect_setup_context(repo_path: str = ".") -> SetupContext:
    """Collect pre-fill data the Web UI's Setup view needs in one call.

    All I/O is best-effort: git failures fall back to ``"origin/main"``
    for the suggested target and ``0`` for the divergence count. The
    wizard must never abort because a fresh clone lacks an upstream
    remote — the user fills the missing pieces in the form.
    """
    current_branch = _auto_detect_fork_ref(repo_path)
    suggested_target = _detect_upstream_default(repo_path)
    api_key_hints = _build_api_key_hints(repo_path)

    has_existing_config = get_config_path(repo_path).exists()
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
                }
        except Exception:
            existing_config_summary = None

    divergence = _count_fork_deleted_files(suggested_target, current_branch, repo_path)

    return SetupContext(
        current_branch=current_branch,
        suggested_target=suggested_target,
        api_key_hints=api_key_hints,
        fork_divergence_count=divergence,
        has_existing_config=has_existing_config,
        existing_config_summary=existing_config_summary,
        forks_profile_threshold=FORKS_PROFILE_INIT_THRESHOLD,
    )


def build_default_payload(repo_path: str = ".") -> SetupPayload:
    """Synthesise a SetupPayload for ``merge --ci`` first-run.

    Used when the user runs ``merge --ci`` and no ``.merge/config.yaml``
    exists yet: we cannot prompt, so we pick safe defaults from git +
    env vars and let ``apply_setup_payload`` write them. The caller is
    expected to print the resulting config path so the operator can
    review/tweak before the next run.
    """
    current_branch = _auto_detect_fork_ref(repo_path)
    suggested_target = _detect_upstream_default(repo_path)

    api_keys: dict[str, str] = {}
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GITHUB_TOKEN"):
        val = os.environ.get(name)
        if val:
            api_keys[name] = val

    return SetupPayload(
        target_branch=suggested_target,
        fork_ref=current_branch,
        project_context="",
        api_keys=api_keys,
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


def _build_api_key_hints(repo_path: str) -> list[ApiKeyHint]:
    """Return a masked hint per known API key env var.

    Priority for the ``source`` label matches ``_resolve_api_keys``:
    shell env beats project ``.env`` beats global ``.env``. The
    masked value is built from whichever wins so the UI can show the
    same string the run will actually pick up.
    """
    global_env = get_global_env_path()
    global_entries = read_env_file(global_env) if global_env.exists() else {}

    project_env = get_project_merge_dir(repo_path) / ".env"
    project_entries = read_env_file(project_env) if project_env.exists() else {}

    hints: list[ApiKeyHint] = []
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GITHUB_TOKEN"):
        if os.environ.get(name):
            hints.append(
                ApiKeyHint(
                    name=name,
                    masked=_mask_key(os.environ[name]),
                    source="shell",
                )
            )
        elif name in project_entries:
            hints.append(
                ApiKeyHint(
                    name=name,
                    masked=_mask_key(project_entries[name]),
                    source="project_env",
                )
            )
        elif name in global_entries:
            hints.append(
                ApiKeyHint(
                    name=name,
                    masked=_mask_key(global_entries[name]),
                    source="global_env",
                )
            )
        else:
            hints.append(ApiKeyHint(name=name, masked="", source=""))
    return hints


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


def migrate_merge_record(repo_path: str = ".") -> None:
    """Move MERGE_RECORD/*.md into .merge/plans/ (one-time migration).

    Safe to call repeatedly — skips files that already exist in the
    destination and leaves the source directory untouched afterwards.
    """
    import shutil

    src_dir = Path(repo_path).resolve() / "MERGE_RECORD"
    if not src_dir.is_dir():
        return

    plans_dir = get_project_merge_dir(repo_path) / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)

    moved: list[str] = []
    for md_file in src_dir.glob("*.md"):
        dest = plans_dir / md_file.name
        if dest.exists():
            continue
        shutil.move(str(md_file), str(dest))
        moved.append(md_file.name)

    if moved:
        console.print(
            f"  [green]Migrated {len(moved)} plan file(s)[/green] "
            f"from MERGE_RECORD/ → .merge/plans/"
        )
