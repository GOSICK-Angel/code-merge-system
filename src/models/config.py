from pydantic import BaseModel, Field, field_validator
from typing import Literal


class AgentLLMConfig(BaseModel):
    provider: Literal["anthropic", "openai"] = "anthropic"
    model: str = "claude-opus-4-6"
    temperature: float = Field(default=0.2, ge=0.0, le=1.0)
    max_tokens: int = Field(default=8192, ge=512, le=200000)
    max_retries: int = Field(default=3, ge=1)
    api_key_env: str = "ANTHROPIC_API_KEY"


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
    formats: list[Literal["json", "markdown"]] = ["json", "markdown"]
    include_raw_diffs: bool = False
    include_llm_traces: bool = False


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
    max_plan_revision_rounds: int = Field(default=2, ge=1, le=5)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    agents: AgentsLLMConfig = Field(default_factory=AgentsLLMConfig)
    thresholds: ThresholdConfig = Field(default_factory=ThresholdConfig)
    file_classifier: FileClassifierConfig = Field(default_factory=FileClassifierConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)

    @field_validator("upstream_ref", "fork_ref")
    @classmethod
    def ref_must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Git ref cannot be empty")
        return v.strip()
