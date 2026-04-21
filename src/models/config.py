from datetime import datetime
from pydantic import BaseModel, Field, field_validator
from typing import Any, Literal


class CompressionConfig(BaseModel):
    """B2: Per-agent context compression tunables."""

    protect_head_tokens: int = Field(default=4000, ge=0)
    protect_tail_tokens: int = Field(default=20000, ge=0)
    stale_output_threshold: int = Field(default=200, ge=0)
    summary_budget_ratio: float = Field(default=0.05, ge=0.0, le=1.0)


class AgentLLMConfig(BaseModel):
    provider: Literal["anthropic", "openai"] = "anthropic"
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
        default=60,
        ge=5,
        description="Per-request HTTP timeout in seconds passed to the LLM SDK.",
    )
    compression: CompressionConfig = Field(default_factory=CompressionConfig)

    @property
    def api_key_env_list(self) -> list[str]:
        """Normalize api_key_env to a list for credential pool support."""
        if isinstance(self.api_key_env, list):
            return self.api_key_env
        return [self.api_key_env]


class AgentsLLMConfig(BaseModel):
    planner: AgentLLMConfig = Field(
        default_factory=lambda: AgentLLMConfig(
            provider="anthropic",
            model="claude-opus-4-6",
            api_key_env="ANTHROPIC_API_KEY",
        )
    )
    planner_judge: AgentLLMConfig = Field(
        default_factory=lambda: AgentLLMConfig(
            provider="openai", model="gpt-4o", api_key_env="OPENAI_API_KEY"
        )
    )
    conflict_analyst: AgentLLMConfig = Field(
        default_factory=lambda: AgentLLMConfig(
            provider="anthropic",
            model="claude-sonnet-4-6",
            api_key_env="ANTHROPIC_API_KEY",
        )
    )
    executor: AgentLLMConfig = Field(
        default_factory=lambda: AgentLLMConfig(
            provider="openai",
            model="gpt-4o",
            temperature=0.1,
            api_key_env="OPENAI_API_KEY",
        )
    )
    judge: AgentLLMConfig = Field(
        default_factory=lambda: AgentLLMConfig(
            provider="anthropic",
            model="claude-opus-4-6",
            temperature=0.1,
            api_key_env="ANTHROPIC_API_KEY",
        )
    )
    human_interface: AgentLLMConfig = Field(
        default_factory=lambda: AgentLLMConfig(
            provider="anthropic",
            model="claude-haiku-4-5-20251001",
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


class SecuritySensitiveConfig(BaseModel):
    patterns: list[str] = Field(
        default_factory=lambda: [
            "**/auth/**",
            "**/security/**",
            "**/*secret*",
            "**/*credential*",
            "**/*password*",
            "**/*.pem",
            "**/*.key",
        ]
    )
    always_require_human: bool = True


class FileClassifierConfig(BaseModel):
    excluded_patterns: list[str] = Field(
        default_factory=lambda: ["**/*.lock", "**/node_modules/**", "**/.git/**"]
    )
    binary_extensions: list[str] = Field(
        default_factory=lambda: [".png", ".jpg", ".pdf", ".zip", ".tar", ".whl"]
    )
    always_take_target_patterns: list[str] = Field(default_factory=list)
    always_take_current_patterns: list[str] = Field(default_factory=list)
    security_sensitive: SecuritySensitiveConfig = Field(
        default_factory=SecuritySensitiveConfig
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


class LLMRiskScoringConfig(BaseModel):
    enabled: bool = False
    gray_zone_low: float = Field(default=0.25, ge=0.0, le=1.0)
    gray_zone_high: float = Field(default=0.65, ge=0.0, le=1.0)
    rule_weight: float = Field(default=0.6, ge=0.0, le=1.0)


class GitHubConfig(BaseModel):
    enabled: bool = False
    token_env: str = "GITHUB_TOKEN"
    repo: str = ""
    pr_number: int | None = None


class MergeLayerConfig(BaseModel):
    enabled: bool = True
    custom_layers: list[dict[str, Any]] = Field(default_factory=list)


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

    enabled: bool = False
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
    llm_extraction: bool = False
    max_insights_per_phase: int = Field(default=5, ge=1, le=20)
    min_judge_repair_rounds: int = Field(default=2, ge=1)


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
    llm: LLMConfig = Field(default_factory=LLMConfig)
    agents: AgentsLLMConfig = Field(default_factory=AgentsLLMConfig)
    thresholds: ThresholdConfig = Field(default_factory=ThresholdConfig)
    file_classifier: FileClassifierConfig = Field(default_factory=FileClassifierConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    syntax_check: SyntaxCheckConfig = Field(default_factory=SyntaxCheckConfig)
    llm_risk_scoring: LLMRiskScoringConfig = Field(default_factory=LLMRiskScoringConfig)
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    layer_config: MergeLayerConfig = Field(default_factory=MergeLayerConfig)
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
    max_judge_repair_rounds: int = Field(default=3, ge=1, le=10)
    memory: MemoryExtractionConfig = Field(default_factory=MemoryExtractionConfig)
    max_dispute_rounds: int = Field(default=2, ge=1, le=5)
    max_batch_repair_rounds: int = Field(default=1, ge=1, le=3)
    parallel_file_concurrency: int | None = Field(
        default=None,
        ge=1,
        description="Max concurrent per-file LLM calls in ConflictAnalyst and Judge. "
        "None = auto-detect from the number of active API keys for each agent.",
    )

    @field_validator("upstream_ref", "fork_ref")
    @classmethod
    def ref_must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Git ref cannot be empty")
        return v.strip()
