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
    type: Literal["grep", "file_exists", "function_exists"] = "grep"
    pattern: str = ""
    files: list[str] = Field(default_factory=list)


class CustomizationEntry(BaseModel):
    name: str
    description: str = ""
    files: list[str] = Field(default_factory=list)
    verification: list[CustomizationVerification] = Field(default_factory=list)


class GateCommandConfig(BaseModel):
    name: str
    command: str
    working_dir: str = "."
    timeout_seconds: int = 300
    pass_criteria: Literal["exit_zero", "not_worse_than_baseline"] = "exit_zero"


class GateBaseline(BaseModel):
    gate_name: str
    baseline_value: str = ""
    recorded_at: datetime = Field(default_factory=datetime.now)


class GateConfig(BaseModel):
    enabled: bool = True
    max_consecutive_failures: int = Field(default=3, ge=1)
    commands: list[GateCommandConfig] = Field(default_factory=list)


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
    gate: GateConfig = Field(default_factory=GateConfig)
    max_judge_repair_rounds: int = Field(default=3, ge=1, le=10)

    @field_validator("upstream_ref", "fork_ref")
    @classmethod
    def ref_must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Git ref cannot be empty")
        return v.strip()
