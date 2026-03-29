from datetime import datetime
from enum import Enum
from uuid import uuid4
from pydantic import BaseModel, Field
from src.models.diff import RiskLevel


class PlanJudgeResult(str, Enum):
    APPROVED = "approved"
    REVISION_NEEDED = "revision_needed"
    CRITICAL_REPLAN = "critical_replan"


class PlanIssue(BaseModel):
    issue_id: str = Field(default_factory=lambda: str(uuid4()))
    file_path: str
    current_classification: RiskLevel
    suggested_classification: RiskLevel
    reason: str
    issue_type: str


class PlanJudgeVerdict(BaseModel):
    verdict_id: str = Field(default_factory=lambda: str(uuid4()))
    result: PlanJudgeResult
    revision_round: int = 0
    issues: list[PlanIssue] = Field(default_factory=list)
    approved_files_count: int = 0
    flagged_files_count: int = 0
    summary: str
    judge_model: str
    timestamp: datetime
