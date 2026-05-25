from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


class CompressionConfig(BaseModel):
    """B2: Per-agent context compression tunables."""

    protect_head_tokens: int = Field(default=4000, ge=0)
    protect_tail_tokens: int = Field(default=20000, ge=0)
    stale_output_threshold: int = Field(default=200, ge=0)
    summary_budget_ratio: float = Field(default=0.05, ge=0.0, le=1.0)


class AgentLLMConfig(BaseModel):
    provider: Literal["anthropic", "openai", "openai_compatible"] = "anthropic"
    model: str = "claude-opus-4-6"
    temperature: float = Field(default=0.2, ge=0.0, le=1.0)
    max_tokens: int = Field(default=8192, ge=512, le=200000)
    max_retries: int = Field(default=3, ge=1)
    api_key_env: str | list[str] = "ANTHROPIC_API_KEY"
    api_base_url_env: str = ""
    cache_strategy: Literal["none", "system_only", "system_and_recent"] = Field(
        default="system_and_recent",
        description="Prompt caching strategy (Anthropic only). "
        "Ignored for OpenAI providers.",
    )
    cheap_model: str | None = Field(
        default=None,
        description="Optional cheaper model for trivial tasks (D1 smart routing). "
        "Set to e.g. 'claude-haiku-4-5-20251001' to auto-route simple queries.",
    )
    request_timeout_seconds: int = Field(
        default=300,
        ge=5,
        description="Per-request HTTP timeout in seconds passed to the LLM SDK.",
    )
    compression: CompressionConfig = Field(default_factory=CompressionConfig)
    repair_max_file_chars: int = Field(
        default=30_000,
        ge=1_000,
        description="O-P1: Executor LLM-repair per-side content size cap. "
        "Files above this threshold are skipped to avoid context-window blowups "
        "and escalated to human review instead.",
    )
    reasoning_effort: str | None = Field(
        default=None,
        description="OpenAI reasoning-model effort hint ('low', 'medium', 'high'). "
        "Only sent when explicitly set — leave None for proxies that do not support it.",
    )
    api_style: Literal["chat", "responses"] = Field(
        default="chat",
        description="OpenAI API surface: 'chat' (chat.completions, default) or "
        "'responses' (the Responses API). Some proxies only expose one; pick "
        "the style your endpoint supports.",
    )
    fallback: Optional[AgentLLMConfig] = Field(
        default=None,
        description="O-1/O-5: Optional fallback provider config activated when the "
        "primary provider's circuit breaker opens after consecutive failures.",
    )

    @property
    def api_key_env_list(self) -> list[str]:
        """Normalize api_key_env to a list for credential pool support."""
        if isinstance(self.api_key_env, list):
            return self.api_key_env
        return [self.api_key_env]


AgentLLMConfig.model_rebuild()


class AgentsLLMConfig(BaseModel):
    planner: AgentLLMConfig = Field(
        default_factory=lambda: AgentLLMConfig(
            provider="anthropic",
            model="claude-opus-4-6",
            max_tokens=8192,
            api_key_env="ANTHROPIC_API_KEY",
        )
    )
    planner_judge: AgentLLMConfig = Field(
        default_factory=lambda: AgentLLMConfig(
            provider="openai",
            model="gpt-5.4",
            max_tokens=32768,
            reasoning_effort="medium",
            api_key_env="OPENAI_API_KEY",
        )
    )
    conflict_analyst: AgentLLMConfig = Field(
        default_factory=lambda: AgentLLMConfig(
            provider="anthropic",
            model="claude-opus-4-6",
            max_tokens=4096,
            api_key_env="ANTHROPIC_API_KEY",
        )
    )
    executor: AgentLLMConfig = Field(
        default_factory=lambda: AgentLLMConfig(
            provider="openai",
            model="gpt-5.4",
            temperature=0.1,
            max_tokens=32768,
            reasoning_effort="medium",
            api_key_env="OPENAI_API_KEY",
        )
    )
    judge: AgentLLMConfig = Field(
        default_factory=lambda: AgentLLMConfig(
            provider="anthropic",
            model="claude-opus-4-6",
            temperature=0.1,
            max_tokens=2048,
            api_key_env="ANTHROPIC_API_KEY",
        )
    )
    human_interface: AgentLLMConfig = Field(
        default_factory=lambda: AgentLLMConfig(
            provider="anthropic",
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            api_key_env="ANTHROPIC_API_KEY",
        )
    )
    memory_extractor: AgentLLMConfig = Field(
        default_factory=lambda: AgentLLMConfig(
            provider="anthropic",
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            api_key_env="ANTHROPIC_API_KEY",
        )
    )


class LLMConfig(BaseModel):
    provider: Literal["anthropic", "openai"] = "anthropic"
    model: str = "claude-opus-4-6"
    fallback_model: str | None = None
    max_tokens: int = Field(default=8192, ge=512, le=200000)
    temperature: float = Field(default=0.2, ge=0.0, le=1.0)
    max_retries: int = Field(default=3, ge=1)


class ThresholdConfig(BaseModel):
    auto_merge_confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    human_escalation: float = Field(default=0.60, ge=0.0, le=1.0)
    risk_score_low: float = Field(default=0.30, ge=0.0, le=1.0)
    risk_score_high: float = Field(default=0.60, ge=0.0, le=1.0)
    chunked_aggregation_min_confidence: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description=(
            "U1: minimum per-chunk confidence required for the chunked "
            "ConflictAnalyst fast path (unanimous + min_conf >= threshold + "
            "no security). Below threshold the reducer falls back to slow "
            "path (precedence + 0.8 penalty). Calibrated against forgejo "
            "1822-file run; tune via .merge/config.yaml."
        ),
    )


class SecuritySensitiveConfig(BaseModel):
    # Strong-signal paths: matching one of these floors the risk_score
    # to 0.8 and, with ``always_require_human``, forces the file into
    # the human-review bucket. Keep this list narrow — only patterns
    # where the *file itself* is almost certainly sensitive material
    # (env files, key/cert files, exact-named credential modules).
    # Wildcard "this filename mentions secrets" globs belong in
    # ``risk_hint_patterns`` instead so test fixtures and pure-logic
    # helpers don't get bricked into manual review.
    patterns: list[str] = Field(
        default_factory=lambda: [
            "**/.env",
            "**/.env.*",
            "**/*.pem",
            "**/*.key",
            "**/credentials.py",
            "**/credentials.ts",
            "**/credentials.go",
            "**/credentials.rb",
            "**/credentials.java",
            "**/secrets.py",
            "**/secrets.ts",
            "**/secrets.go",
            "**/secrets.rb",
            "**/secrets.java",
            "**/auth/credentials/**",
            "**/auth/secrets/**",
        ]
    )
    always_require_human: bool = True

    # Env-template files that match the strict ``patterns`` above (e.g.
    # ``.env.example``) hold placeholders, not real secrets. Treating them
    # as ``human_required`` would block batch merges of plugin-style
    # repositories (where every plugin ships an env template) for no
    # real safety win. Files matching any of these patterns are
    # *demoted* from the strict floor to the weak ``risk_hint`` floor —
    # they still get the bump (so they land in ``auto_risky``), but the
    # 0.8 floor + ``always_require_human`` no longer applies.
    env_template_patterns: list[str] = Field(
        default_factory=lambda: [
            "**/.env.example",
            "**/.env.sample",
            "**/.env.template",
            "**/.env.dist",
            "**/.env.defaults",
        ]
    )

    # Weak-signal paths: matching one of these does NOT floor the score
    # or force human review — the path *might* be sensitive, but it
    # might just be a test, a doc, or a logically-named helper. Hits
    # add ``risk_hint_bump`` to the rule risk score, which usually nudges
    # the file from AUTO_SAFE into AUTO_RISKY so ConflictAnalyst LLM
    # examines the diff. The LLM's blended score (compute_llm_risk_score)
    # then has the final word.
    #
    # The list is intentionally broader than ``patterns``: we want every
    # auth/login/verify/otp/oauth/sign-related file in the tree to clear
    # AUTO_SAFE so PlannerJudge does not have to claw them back via an
    # extra LLM round-trip on every merge. False positives (test fixtures,
    # doc-only files) are tolerated — they merely get a ConflictAnalyst
    # look, which is cheap.
    risk_hint_patterns: list[str] = Field(
        default_factory=lambda: [
            # Directory-style hints
            "**/auth/**",
            "**/security/**",
            # Single-file auth modules (auth.py, auth.ts, auth.go, ...)
            "**/auth.py",
            "**/auth.ts",
            "**/auth.js",
            "**/auth.go",
            "**/auth.java",
            "**/auth.rs",
            "**/auth.rb",
            # Auth flow keywords
            "**/*oauth*",
            "**/*signin*",
            "**/*signup*",
            "**/*login*",
            "**/*logout*",
            "**/*signout*",
            "**/*authorize*",
            "**/*authn*",
            "**/*authz*",
            # Verification / OTP / 2FA / MFA flows
            "**/*otp*",
            "**/*verify*",
            "**/*verification*",
            "**/*2fa*",
            "**/*mfa*",
            "**/*totp*",
            # Signatures / permissions / API keys
            "**/*signature*",
            "**/*sign_*",
            "**/*permission*",
            "**/*acl*",
            "**/*api_key*",
            "**/*apikey*",
            # Existing weak signals
            "**/*secret*",
            "**/*credential*",
            "**/*password*",
            # Auth-token variants only — bare ``*token*`` matched far
            # too many false positives in NLP / ML repositories
            # (``tokenizer.py``, ``tokens.json``, ``bpe_tokens``, ...)
            # and combined with ``risk_hint_bump=0.25`` pushed every
            # tokenizer file out of AUTO_SAFE. Restrict to compound
            # forms that genuinely imply an authentication token.
            "**/*auth_token*",
            "**/*access_token*",
            "**/*api_token*",
            "**/*refresh_token*",
            "**/*bearer_token*",
            "**/*token_auth*",
        ]
    )
    risk_hint_bump: float = Field(default=0.25, ge=0.0, le=0.5)


class FieldSensitivityRule(BaseModel):
    """P1-4: declarative rule that escalates a structured-config file's
    risk level when specific *fields* (not just path) are touched.

    Example yaml:

        field_sensitivity_rules:
          - path_glob: "**/manifest.yaml"
            sensitive_fields: ["oauth.scopes", "permissions.*", "endpoints.*.url"]
            escalate_to: auto_risky

    The rule is generic — paths and field names are pure config. Use
    fnmatch-style globs in both ``path_glob`` and each entry of
    ``sensitive_fields``. Array indices in the file are normalised to
    ``*`` before matching (so ``endpoints[3].url`` is reported as
    ``endpoints.*.url``).
    """

    path_glob: str = Field(
        ..., min_length=1, description="fnmatch glob applied to file path."
    )
    sensitive_fields: list[str] = Field(
        ...,
        min_length=1,
        description="Dot-path field globs (e.g. ``oauth.scopes``, "
        "``permissions.*``). At least one such field must change "
        "between base and target for the rule to fire.",
    )
    escalate_to: Literal["auto_risky", "human_required"] = Field(
        ...,
        description="Risk level the matched file is bumped to. Only "
        "upward escalation is allowed — the rule never weakens an "
        "already-stricter classification.",
    )


class FileClassifierConfig(BaseModel):
    excluded_patterns: list[str] = Field(
        default_factory=lambda: [
            "**/*.lock",
            "**/node_modules/**",
            "**/.git/**",
            ".github/workflows/**",
        ]
    )
    force_auto_safe_patterns: list[str] = Field(
        default_factory=lambda: [
            "**/requirements.txt",
            "**/pyproject.toml",
        ],
        description=(
            "Paths matching these glob patterns are forced to AUTO_SAFE risk level "
            "regardless of their computed risk_score or C-class category, unless "
            "they are marked security_sensitive. Intended for dependency manifests "
            "(requirements.txt, pyproject.toml) that are frequently modified by both "
            "sides but rarely require semantic conflict analysis."
        ),
    )
    binary_extensions: list[str] = Field(
        default_factory=lambda: [".png", ".jpg", ".pdf", ".zip", ".tar", ".whl"]
    )
    always_take_target_patterns: list[str] = Field(
        default_factory=list,
        description=(
            "Legacy alias of always_take_upstream_patterns. Kept for "
            "backward compatibility — when matched, file is forced to "
            "TAKE_TARGET (= take upstream_ref version) via low-risk score."
        ),
    )
    always_take_upstream_patterns: list[str] = Field(
        default_factory=list,
        description=(
            "Paths whose final content must come from upstream_ref. "
            "Matched files are pre-decided as MergeDecision.TAKE_TARGET "
            "in InitializePhase, skip planner/executor/judge entirely, "
            "and have their content force-checked-out from upstream_ref."
        ),
    )
    always_take_current_patterns: list[str] = Field(
        default_factory=list,
        description=(
            "Paths whose final content must come from fork_ref (the "
            "working baseline). Matched files are pre-decided as "
            "MergeDecision.TAKE_CURRENT in InitializePhase, skip the "
            "AI flow entirely. For D_MISSING paths the file remains "
            "absent (not created from upstream)."
        ),
    )
    security_sensitive: SecuritySensitiveConfig = Field(
        default_factory=SecuritySensitiveConfig
    )
    field_sensitivity_rules: list[FieldSensitivityRule] = Field(
        default_factory=list,
        description="P1-4: declarative rules that escalate risk_level "
        "when specific yaml/json fields (not just file paths) change. "
        "Empty by default — opt in per repo to teach the agent about "
        "structured-config files whose individual keys carry risk "
        "(e.g. plugin manifests with OAuth scopes / permissions).",
    )
    migration_dir_patterns: list[str] = Field(
        default_factory=lambda: ["migrations/", "alembic/"],
        description=(
            "Path substrings that identify DB-schema migration directories. "
            "Used to detect ordering dependencies between upstream-new migration "
            "files and fork-conflicted model files in the same top-level package. "
            "Add project-specific patterns (e.g. 'forgejo_migrations/') via "
            "file_classifier.migration_dir_patterns in config.yaml."
        ),
    )
    c_class_risk_floor: float = Field(
        default=0.4,
        ge=0.0,
        le=1.0,
        description=(
            "Minimum risk_score applied to category=both_changed (C-class) "
            "files. Pre-merge, conflict markers do not exist yet, so the "
            "conflict_density dimension of compute_risk_score is always 0 "
            "for C-class — a fork-side rewrite of the same function as "
            "upstream can score as low as ~0.3 with no other signal. "
            "Flooring to 0.4 (default) lifts C-class above auto_safe band "
            "without forcing human_required (≥0.6), giving ConflictAnalyst "
            "a guaranteed look. Strong path-based escalations "
            "(security_sensitive.patterns → 0.8, always_take_target → 0.1) "
            "still take precedence over this floor."
        ),
    )


class OutputConfig(BaseModel):
    directory: str = "./outputs"
    debug_directory: str = "./outputs/debug"
    formats: list[Literal["json", "markdown"]] = ["json", "markdown"]
    include_raw_diffs: bool = False
    include_llm_traces: bool = True
    structured_logs: bool = False
    language: str = "en"
    debug_checkpoints: bool = False


class SyntaxCheckConfig(BaseModel):
    enabled: bool = True
    languages: list[str] = Field(default_factory=lambda: ["python", "json", "yaml"])


class LLMAssistConfig(BaseModel):
    """Controls when the planner spends LLM calls to refine the
    deterministic rule-based plan. ``mode`` is a regime selector, not a
    per-file switch: under ``auto`` the decision of *which* files get an
    LLM look is driven entirely by ``compute_complexity`` falling in the
    uncertainty band (single-file rescore) or above it (batch
    re-classification), capped by ``budget_max_files``. ``off`` keeps the
    plan fully deterministic (CI reproducibility / zero cost); ``always``
    sends every file through at least the rescore tier.
    """

    mode: Literal["off", "auto", "always"] = "auto"
    budget_max_files: int = Field(default=200, ge=0)
    uncertainty_low: float = Field(default=0.30, ge=0.0, le=1.0)
    uncertainty_high: float = Field(default=0.70, ge=0.0, le=1.0)
    rule_weight: float = Field(default=0.6, ge=0.0, le=1.0)


class ComplexityConfig(BaseModel):
    """Weights for ``compute_complexity`` — the signal that decides
    whether a file is worth an LLM look. Distinct from the risk-score
    weights: risk decides which bucket a file lands in, complexity
    decides whether spending an LLM call on it is justified. ``w_fanout``
    measures cross-module spread and stays 0 until module inference is
    wired (its weight is redistributed across the others when absent).
    """

    w_size: float = Field(default=0.25, ge=0.0, le=1.0)
    w_hunks: float = Field(default=0.20, ge=0.0, le=1.0)
    w_conflict: float = Field(default=0.30, ge=0.0, le=1.0)
    w_change_ratio: float = Field(default=0.15, ge=0.0, le=1.0)
    w_fanout: float = Field(default=0.10, ge=0.0, le=1.0)


class GitHubConfig(BaseModel):
    enabled: bool = False
    token_env: str = "GITHUB_TOKEN"
    repo: str = ""
    pr_number: int | None = None


class MergeLayerConfig(BaseModel):
    enabled: bool = True
    custom_layers: list[dict[str, Any]] = Field(default_factory=list)


class ModuleConfig(BaseModel):
    """Groups files into functional modules so the plan can be organised
    by module first and file type/risk second. Target-repo agnostic:
    module boundaries come from explicit globs, the forks-profile
    rewritten-module list, or directory topology — never hardcoded names.
    """

    enabled: bool = True
    mode: Literal["auto", "config", "off"] = "auto"
    container_dirs: list[str] = Field(
        default_factory=lambda: [
            "packages",
            "apps",
            "plugins",
            "services",
            "libs",
            "src",
        ],
        description=(
            "Monorepo container directories whose immediate child names the "
            "module (e.g. packages/<mod>/...). Neutral conventions by default; "
            "override per repo."
        ),
    )
    explicit: dict[str, str] = Field(
        default_factory=dict,
        description="Glob → module-name overrides, highest precedence.",
    )
    module_depends_on: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Module → modules it depends on, used to order modules.",
    )


class CustomizationVerification(BaseModel):
    type: Literal[
        "grep",
        "grep_count_min",
        "grep_count_baseline",
        "file_exists",
        "function_exists",
        "line_retention",
    ] = "grep"
    pattern: str = ""
    files: list[str] = Field(default_factory=list)
    min_count: int | None = Field(
        default=None,
        ge=1,
        description="For grep_count_min: minimum total matches required across files.",
    )
    baseline_ref: str | None = Field(
        default=None,
        description="Git ref used as baseline (e.g. merge-base SHA). "
        "None means auto-resolve to merge_base_commit.",
    )
    retention_ratio: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="For line_retention: required ratio of baseline lines "
        "still present in HEAD (0.9 = keep 90%).",
    )


class CustomizationEntry(BaseModel):
    name: str
    description: str = ""
    files: list[str] = Field(default_factory=list)
    verification: list[CustomizationVerification] = Field(default_factory=list)
    source: Literal["manual", "scar_learned"] = Field(
        default="manual",
        description="P2-1: 'scar_learned' entries are auto-generated by ScarListBuilder.",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="P2-1: confidence score for scar-learned entries (0.7-0.9); "
        "manual entries default to 1.0.",
    )


class ScarLearningConfig(BaseModel):
    """P2-1: configure automatic scar-list learning from git history."""

    enabled: bool = True
    since: str = Field(
        default="1 year ago",
        description="Git date spec for how far back to scan (e.g. '1 year ago', '2025-01-01').",
    )
    grep_patterns: list[str] = Field(
        default_factory=list,
        description="Additional regex patterns beyond the built-in defaults "
        "('restore', 'fix.*compat', 'revert').",
    )
    auto_append_to_customizations: bool = Field(
        default=True,
        description="If True, materialized scar entries are appended to "
        "MergeConfig.customizations at runtime.",
    )


class ConfigRetentionRule(BaseModel):
    """P2-3: one rule asserting that files matching a glob retain required lines."""

    file_glob: str = Field(
        description="Glob pattern relative to repo root, e.g. '.github/workflows/*.yml'."
    )
    required_lines: list[str] = Field(
        default_factory=list,
        description="List of regex patterns; each must match at least one line in "
        "every file matched by file_glob.",
    )
    min_line_count: int = Field(
        default=1,
        ge=1,
        description="Minimum number of required_lines patterns that must match "
        "(defaults to all of them).",
    )


class ConfigRetentionConfig(BaseModel):
    """P2-3: container for all ConfigRetentionRule entries."""

    enabled: bool = True
    rules: list[ConfigRetentionRule] = Field(default_factory=list)


class ShadowRuleConfig(BaseModel):
    """Pairs of extensions (or module-vs-package) that shadow each other in module resolution."""

    exts_a: list[str] = Field(default_factory=list)
    exts_b: list[str] = Field(default_factory=list)
    module_vs_package: bool = Field(
        default=False,
        description="If True: detect m.py vs m/__init__.py style shadow.",
    )
    description: str = ""


class CrossLayerAssertion(BaseModel):
    """Declarative cross-layer key consistency assertion.

    keys_from = "<file>::<regex>" — capture group 1 is the key.
    keys_in   = list of files that must contain each key (as literal substring).
    allow_missing = keys that may be absent without violating.
    """

    name: str
    keys_from: str
    keys_in: list[str] = Field(default_factory=list)
    allow_missing: list[str] = Field(default_factory=list)


class GateCommandConfig(BaseModel):
    name: str
    command: str
    working_dir: str = "."
    timeout_seconds: int = 300
    pass_criteria: Literal[
        "exit_zero",
        "not_worse_than_baseline",
        "no_new_regression",
    ] = "exit_zero"
    baseline_parser: str = Field(
        default="",
        description="P1-2: parser name for structured baseline diff "
        "(pytest_summary, mypy_json, ruff_json, eslint_json, tsc_errors, "
        "go_test_json, cargo_test_json, junit_xml). Empty disables parsing.",
    )


class GateBaseline(BaseModel):
    gate_name: str
    baseline_value: str = Field(
        default="",
        description="Legacy raw stdout_tail baseline output.",
    )
    structured_baseline: dict[str, Any] = Field(
        default_factory=dict,
        description="P1-2: parsed structured baseline — "
        '{"passed": int, "failed": int, "failed_ids": list[str]}',
    )
    parser_name: str = ""
    recorded_at: datetime = Field(default_factory=datetime.now)


class GateConfig(BaseModel):
    enabled: bool = True
    max_consecutive_failures: int = Field(default=3, ge=1)
    commands: list[GateCommandConfig] = Field(default_factory=list)


class ReverseImpactConfig(BaseModel):
    """P1-1: configure reverse-impact scan scope and behavior."""

    enabled: bool = True
    extra_scan_globs: list[str] = Field(
        default_factory=list,
        description="Additional file globs (beyond D_EXTRA and customization.files) "
        "to scan for symbol references.",
    )
    max_files_per_symbol: int = Field(default=100, ge=1)


class SmokeTestCase(BaseModel):
    """A single smoke-test case. Exactly one of cmd/url/tag is used depending on ``kind``."""

    id: str
    cmd: str = ""
    url: str = ""
    method: Literal["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS", "PATCH"] = "GET"
    expect_status: int = 200
    body: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    tag: str = ""
    timeout_seconds: int = Field(default=60, ge=1)


class SmokeTestSuite(BaseModel):
    name: str
    kind: Literal["shell", "http", "playwright"] = "shell"
    working_dir: str = "."
    cases: list[SmokeTestCase] = Field(default_factory=list)


class SmokeTestConfig(BaseModel):
    enabled: bool = False
    suites: list[SmokeTestSuite] = Field(default_factory=list)
    block_on_failure: bool = True
    max_consecutive_failures: int = Field(default=3, ge=1)


class BuildCheckConfig(BaseModel):
    """Optional post-judge compile/build gate.

    Generic by design: the toolchain command is supplied per target via
    config (e.g. ``go build ./...``, ``tsc --noEmit``). A non-zero exit
    downgrades a Judge PASS to FAIL with a veto. Disabled and empty by
    default so the agent stays target-agnostic.
    """

    enabled: bool = False
    command: str = ""
    working_dir: str = "."
    timeout_seconds: int = Field(default=600, ge=1)


class MigrationConfig(BaseModel):
    merge_base_override: str | None = Field(
        default=None,
        description="Override the git merge-base with a specific commit SHA. "
        "Use this when the fork was created via code migration.",
    )
    auto_detect_sync_point: bool = Field(
        default=True,
        description="Automatically detect if upstream commits have already been "
        "migrated into the fork.",
    )
    sync_detection_threshold: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Ratio of synced upstream-changed files that triggers migration "
        "detection. 0.3 means >30% of files must be synced.",
    )
    min_synced_files: int = Field(
        default=5,
        ge=1,
        description="Minimum number of synced files required to trigger detection. "
        "Prevents false positives when few files changed.",
    )


class HistoryPreservationConfig(BaseModel):
    enabled: bool = True
    cherry_pick_clean: bool = True
    commit_after_phase: bool = True


class MemoryExtractionConfig(BaseModel):
    llm_extraction: bool = True
    max_insights_per_phase: int = Field(default=5, ge=1, le=20)
    min_judge_repair_rounds: int = Field(
        default=1,
        ge=1,
        description="O-M2: minimum dispute rounds before memory_extractor fires "
        "on judge_review. Lowered from 2 so dispute-round failure modes get "
        "persisted even when max_dispute_rounds=2 caps judge_repair_rounds at 1.",
    )
    extract_on_meta_review: bool = Field(
        default=True,
        description="O-M2: trigger extraction when Coordinator produces a "
        "meta_review directive (captures failure-mode insights after stalls).",
    )
    relevance_min_score: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="O-M3: minimum relevance score (0..1) for an entry to be "
        "injected when the layered loader applies relevance filtering. "
        "Score combines path overlap and entry confidence.",
    )
    relevance_filter_threshold: int = Field(
        default=100,
        ge=10,
        description="O-M3: when total entries in MemoryStore exceeds this, "
        "the layered loader tightens caps (L0/L1/L2) and applies "
        "relevance_min_score so prompts don't bloat past the context window.",
    )
    periodic_extraction_every_n_phases: int = Field(
        default=0,
        ge=0,
        description="O-M (6.3): when > 0, run memory_extractor every N "
        "completed phases regardless of error/dispute triggers, so L2 "
        "aggregation grows on long happy-path runs. 0 disables.",
    )


class RenameDetectionConfig(BaseModel):
    """P1-5: extra guards on top of git's default similarity-based rename
    detection. Defaults are off so behaviour is unchanged for existing
    deployments — opt in via ``<repo>/.merge/config.yaml``."""

    require_same_parent_dir: bool = Field(
        default=False,
        description="When true, drop rename pairs whose old/new paths "
        "live in different parent directories (``os.path.dirname``). "
        "Useful for forks where files in distinct namespaces happen to "
        "score above git's content-similarity threshold and produce "
        "spurious cross-namespace renames.",
    )
    require_same_prefix_segments: int | None = Field(
        default=None,
        ge=1,
        description="When set to N, drop rename pairs whose old/new "
        "paths do not share the first N path segments. Stricter than "
        "``require_same_parent_dir`` and generalises the same-namespace "
        "intuition without baking specific directory names into source.",
    )


class PlanReviewConfig(BaseModel):
    segment_safelist_patterns: list[str] = Field(
        default_factory=list,
        description="Project-specific glob patterns appended to the built-in "
        "Plan-Judge segment safelist. Files matching one of these (in addition "
        "to lockfiles / LICENSE / .gitignore / etc.) can let an entire "
        "segment skip the LLM review when no other risk signals fire. "
        "Use this to teach the agent about per-repo metadata files "
        "(e.g. plugin manifests, position files, asset directories) "
        "without hardcoding repo-specific knowledge in the agent.",
    )
    safelist_lockfile_max_lines: int = Field(
        default=1000,
        ge=100,
        description="Per-file change-line ceiling for lockfile safelist "
        "skip. A lockfile with lines_added + lines_deleted at or above "
        "this value is forced back through the LLM review path even "
        "when the segment is otherwise trivially safe. Bounds the "
        "supply-chain risk of silently accepting massive dependency "
        "rewrites (e.g. malicious package injection via auto-bumped "
        "package-lock.json).",
    )
    min_rounds_when_segmented: int = Field(
        default=0,
        ge=0,
        le=10,
        description="P2-9: opt-in floor on the effective "
        "``max_plan_revision_rounds`` whenever the plan splits into "
        "more than one LLM-review segment. Default 0 = disabled "
        "(use ``max_plan_revision_rounds`` verbatim). On large forks "
        "the LLM often needs ≥2 rounds to converge per-segment; set "
        "this to 2 or 3 to give the loop room without raising the "
        "cap for everyone. Only ever raises the bound — never "
        "lowers a higher ``max_plan_revision_rounds``.",
    )
    analyst_decision_options_enabled: bool = Field(
        default=False,
        description="Opt-in: when True, ConflictAnalyst proposes 1–3 "
        "file-specific decision options for every HUMAN_REQUIRED file "
        "right before they're surfaced to the reviewer. Costs roughly "
        "one extra LLM call per HUMAN_REQUIRED file. Default off so "
        "small / cost-sensitive runs keep the deterministic base "
        "ladder unchanged; turn on for large fork-vs-upstream merges "
        "where the reviewer would benefit from concrete pre-thought "
        "strategies beyond keep_head / take_target / llm_auto_merge.",
    )


class CoordinatorConfig(BaseModel):
    judge_meta_review_threshold: int = Field(
        default=2,
        ge=1,
        description="Judge repair rounds before Coordinator triggers meta-review.",
    )
    dispute_meta_review_threshold: int = Field(
        default=2,
        ge=1,
        description="Plan dispute count before Coordinator triggers meta-review.",
    )
    context_utilization_ratio: float = Field(
        default=0.6,
        ge=0.1,
        le=0.95,
        description="Fraction of model context window to allocate per batch.",
    )
    max_files_per_batch: int | None = Field(
        default=None,
        description="Hard cap on files per batch. None = auto-computed from token budget.",
    )
    avg_tokens_per_file: int = Field(
        default=2000,
        ge=100,
        description="Token estimate per file used in batch-size calculation.",
    )
    max_tokens_per_batch: int = Field(
        default=50_000,
        ge=1000,
        description=(
            "Hard ceiling on estimated tokens per batch. Token-aware "
            "secondary split kicks in when per-file size hints are supplied "
            "to enforce_batch_limits — prevents single-batch context-window "
            "overflows that the file-count heuristic alone misses."
        ),
    )
    group_batches_by_directory: bool = Field(
        default=True,
        description=(
            "When True, enforce_batch_limits regroups each batch by "
            "top-level directory before applying the file-count cap. "
            "Produces tighter, more cohesive batches — e.g. all "
            "models/auth/* files land in one sub-batch instead of being "
            "interleaved with tests/ and templates/ in the alphabetic "
            "split. Reduces Executor rollback blast radius when a single "
            "file in a large batch fails. Set False to restore the legacy "
            "flat split."
        ),
    )
    meta_review_enabled: bool = True


class MergeConfig(BaseModel):
    upstream_ref: str = Field(
        ..., description="upstream branch ref, e.g. upstream/main"
    )
    fork_ref: str = Field(
        ..., description="downstream branch ref, e.g. feature/my-fork"
    )
    working_branch: str = Field(
        default="merge/auto-{timestamp}",
        description="working branch name template for merge execution",
    )
    repo_path: str = Field(default=".", description="local repository path")
    project_context: str = Field(
        default="",
        description="project background description to help LLM understand code semantics",
    )
    max_files_per_run: int = Field(default=500, ge=1)
    max_plan_revision_rounds: int = Field(default=5, ge=1, le=20)
    commit_round_size: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Max commits per round in commit-stream conflict analysis.",
    )
    commit_round_max_files: int = Field(
        default=60,
        ge=1,
        le=500,
        description=(
            "Max files per commit-stream round. When a round's accumulated "
            "file set hits this cap, the round is closed early even if the "
            "commit count has not reached commit_round_size. Prevents "
            "fork-only migration commits from producing 400+ file mega-rounds "
            "that exceed the LLM context window."
        ),
    )
    commit_round_max_est_tokens: int = Field(
        default=120_000,
        ge=10_000,
        description=(
            "Max estimated prompt tokens per commit-stream round. Estimated "
            "as files * 1000 + commits * 200 (conservative). Closes a round "
            "early when accumulated estimate would exceed this cap."
        ),
    )
    llm: LLMConfig = Field(default_factory=LLMConfig)
    agents: AgentsLLMConfig = Field(default_factory=AgentsLLMConfig)
    thresholds: ThresholdConfig = Field(default_factory=ThresholdConfig)
    file_classifier: FileClassifierConfig = Field(default_factory=FileClassifierConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    syntax_check: SyntaxCheckConfig = Field(default_factory=SyntaxCheckConfig)
    llm_assist: LLMAssistConfig = Field(default_factory=LLMAssistConfig)
    complexity: ComplexityConfig = Field(default_factory=ComplexityConfig)
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    layer_config: MergeLayerConfig = Field(default_factory=MergeLayerConfig)
    module_config: ModuleConfig = Field(default_factory=ModuleConfig)
    customizations: list[CustomizationEntry] = Field(default_factory=list)
    shadow_rules_extra: list[ShadowRuleConfig] = Field(
        default_factory=list,
        description="Additional shadow-conflict rules appended to DEFAULT_SHADOW_RULES.",
    )
    cross_layer_assertions: list[CrossLayerAssertion] = Field(
        default_factory=list,
        description="Declarative cross-layer key consistency assertions (P0-4).",
    )
    gate: GateConfig = Field(default_factory=GateConfig)
    reverse_impact: ReverseImpactConfig = Field(
        default_factory=ReverseImpactConfig,
        description="P1-1: reverse-impact scan configuration.",
    )
    smoke_tests: SmokeTestConfig = Field(
        default_factory=SmokeTestConfig,
        description="P1-3: post-judge smoke test configuration.",
    )
    build_check: BuildCheckConfig = Field(
        default_factory=BuildCheckConfig,
        description="Optional post-judge compile/build gate (config-supplied "
        "command; disabled by default).",
    )
    sentinels_extra: list[str] = Field(
        default_factory=list,
        description="P2-2: project-specific sentinel regex patterns appended to "
        "DEFAULT_SENTINELS (e.g. business terms, SSO markers).",
    )
    config_retention: ConfigRetentionConfig = Field(
        default_factory=ConfigRetentionConfig,
        description="P2-3: rules for required-line retention in CI/env/docker files.",
    )
    scar_learning: ScarLearningConfig = Field(
        default_factory=ScarLearningConfig,
        description="P2-1: configure automatic scar-list learning from git history.",
    )
    migration: MigrationConfig = Field(default_factory=MigrationConfig)
    history: HistoryPreservationConfig = Field(
        default_factory=HistoryPreservationConfig
    )
    memory: MemoryExtractionConfig = Field(default_factory=MemoryExtractionConfig)
    coordinator: CoordinatorConfig = Field(default_factory=CoordinatorConfig)
    plan_review: PlanReviewConfig = Field(
        default_factory=PlanReviewConfig,
        description="Plan-review (PlannerJudge) tuning, including the "
        "per-repo segment safelist extension list.",
    )
    rename_detection: RenameDetectionConfig = Field(
        default_factory=RenameDetectionConfig,
        description="P1-5: optional guards on top of git's similarity-based "
        "rename detection (same-parent-dir / shared-prefix-depth). "
        "Defaults are off; opt in per repo.",
    )
    max_dispute_rounds: int = Field(default=2, ge=1, le=5)
    max_batch_repair_rounds: int = Field(default=1, ge=1, le=3)
    max_rerun_rounds: int = Field(
        default=1,
        ge=0,
        le=5,
        description=(
            "P2-1: cap on rerun rounds the user can trigger after a "
            "non-PASS judge verdict. Default 1 — rerun once, then a "
            "second rerun request is rejected and the run terminates as "
            "FAILED. Prevents the system from looping over Judge FAIL → "
            "rerun → Judge FAIL indefinitely. Set to 0 to disable rerun."
        ),
    )
    judge_skip_high_confidence: bool = Field(
        default=True,
        description="O-J1: skip per-file LLM judge review when record.confidence "
        ">= judge_skip_confidence_threshold and local syntax passes. Reduces "
        "round-0 LLM fan-out for obviously-safe auto_risky files.",
    )
    judge_skip_confidence_threshold: float = Field(
        default=0.9,
        ge=0.0,
        le=1.0,
        description="O-J1: minimum decision-record confidence that lets Judge skip "
        "the LLM call when syntax validates locally.",
    )
    judge_freeze_prior_issues: bool = Field(
        default=True,
        description="O-J2: in dispute rounds, Judge only re-evaluates whether "
        "Executor's repair closed the previously-reported issues and does not "
        "introduce brand-new issues (those roll to meta-review as out-of-scope).",
    )
    judge_skip_take_decisions: bool = Field(
        default=True,
        description="O-J3: skip per-file LLM judge review for take_target / "
        "take_current records by verifying the worktree blob equals the "
        "chosen ref via git hash-object. A drift (worktree != expected ref) "
        "produces a deterministic CRITICAL issue without invoking the LLM. "
        "Security-sensitive files always stay in the LLM path.",
    )
    judge_blocking_levels: list[str] = Field(
        default_factory=lambda: ["critical", "high"],
        description="O-M2: issue severities that block a BatchVerdict from "
        "being approved. Issues at other levels (medium/low/info) are recorded "
        "as advisories and do not prevent consensus. Must be subset of "
        "{critical, high, medium, low, info}.",
    )
    judge_cross_file_signature_check: bool = Field(
        default=True,
        description="Emit a HIGH issue when a symbol whose upstream signature "
        "changed has its definition file and a referencing file decided onto "
        "opposite take_target/take_current sides — a likely cross-file "
        "compilation break the per-file review cannot see. Text-grep based; "
        "semantic_merge files are skipped because their merged direction is "
        "indeterminate.",
    )
    chunk_size_chars: int = Field(
        default=20000,
        ge=5000,
        description="Files larger than this threshold (in chars) are split into "
        "semantic chunks for LLM merge instead of being processed in one call.",
    )
    customization_path_patterns: list[str] = Field(
        default_factory=list,
        description="Glob patterns for files that have local customizations. "
        "Judge uses 'customization_preserved' strategy for these files instead of "
        "'upstream_match'. Example: ['custom/**', '**/local_overrides/**'].",
    )
    parallel_file_concurrency: int | None = Field(
        default=None,
        ge=1,
        description="Max concurrent per-file LLM calls in ConflictAnalyst and Judge. "
        "None = auto-detect from the number of active API keys for each agent.",
    )
    max_cost_usd: float | None = Field(
        default=5.0,
        gt=0,
        description="U2 per-run budget cap (USD). Default 5.0 is a safety net "
        "for runaway spend on large repos; set to None to disable. Two layers "
        "enforce it: BaseAgent._call_llm_with_retry raises RunBudgetExceeded "
        "before/after each LLM call (fine-grained), and Orchestrator checks "
        "between phases as a coarse-grained ceiling fallback. Both transition "
        "the run to AWAITING_HUMAN with a partial budget report. None = "
        "no ceiling (legacy compatibility).",
    )
    per_run_cost_warn_pct: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="U2: emit a `budget_warning` activity event the first time "
        "cumulative cost crosses this fraction of max_cost_usd. Default 0.8 "
        "(80%) gives reviewers ~20% headroom before the hard cap trips. "
        "Has no effect when max_cost_usd is None.",
    )
    enable_working_branch: bool = Field(
        default=True,
        description="When True, the orchestrator creates a new branch from fork_ref "
        "at run start (using the working_branch name template) and operates on it "
        "instead of modifying fork_ref HEAD directly. The branch name supports a "
        "{timestamp} placeholder (e.g. 'merge/auto-{timestamp}'). On resume, the "
        "existing branch is reused via active_branch in the checkpoint. U7: "
        "default flipped to True so a half-finished run never pollutes fork_ref "
        "HEAD; set to False explicitly to restore the legacy in-place behavior.",
    )

    @model_validator(mode="before")
    @classmethod
    def _hoist_top_level_security_sensitive(cls, data: object) -> object:
        """Allow ``security_sensitive:`` at the top level of config.yaml.

        Pydantic ignores unknown top-level keys, so users who write::

            security_sensitive:
              patterns: [...]

        at the root of their config get no error and no effect.  This
        validator intercepts the raw dict *before* field assignment and
        moves the value into ``file_classifier.security_sensitive`` so
        both the top-level shorthand and the fully-qualified form work.
        The fully-qualified form always wins when both are present.
        """
        if not isinstance(data, dict):
            return data
        sec = data.pop("security_sensitive", None)
        if sec and isinstance(sec, dict):
            fc = data.get("file_classifier")
            if fc is None:
                data["file_classifier"] = {"security_sensitive": sec}
            elif isinstance(fc, dict):
                fc.setdefault("security_sensitive", sec)
        return data

    @field_validator("upstream_ref", "fork_ref")
    @classmethod
    def ref_must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Git ref cannot be empty")
        return v.strip()
