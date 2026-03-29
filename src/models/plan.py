from datetime import datetime
from enum import Enum
from uuid import uuid4
from pydantic import BaseModel, Field
from src.models.diff import RiskLevel


class MergePhase(str, Enum):
    ANALYSIS = "analysis"
    PLAN_REVIEW = "plan_review"
    PLAN_REVISING = "plan_revising"
    AUTO_MERGE = "auto_merge"
    CONFLICT_ANALYSIS = "conflict_analysis"
    HUMAN_REVIEW = "human_review"
    JUDGE_REVIEW = "judge_review"
    REPORT = "report"


class PhaseFileBatch(BaseModel):
    batch_id: str
    phase: MergePhase
    file_paths: list[str]
    risk_level: RiskLevel
    estimated_duration_minutes: float | None = None
    can_parallelize: bool = True


class RiskSummary(BaseModel):
    total_files: int
    auto_safe_count: int
    auto_risky_count: int
    human_required_count: int
    deleted_only_count: int
    binary_count: int
    excluded_count: int
    estimated_auto_merge_rate: float = Field(ge=0.0, le=1.0)
    top_risk_files: list[str] = Field(default_factory=list)


class MergePlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: str(uuid4()))
    created_at: datetime
    upstream_ref: str
    fork_ref: str
    merge_base_commit: str
    phases: list[PhaseFileBatch]
    risk_summary: RiskSummary
    project_context_summary: str
    special_instructions: list[str] = Field(default_factory=list)
    version: str = "1.0"
