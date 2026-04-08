from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field
from src.models.plan_judge import PlanJudgeResult


class PlanHumanDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    MODIFY = "modify"


class PlanReviewRound(BaseModel):
    round_number: int
    verdict_result: PlanJudgeResult
    verdict_summary: str
    issues_count: int
    issues_detail: list[dict[str, str]] = Field(default_factory=list)
    planner_revision_summary: str | None = None
    timestamp: datetime = Field(default_factory=datetime.now)


class PlanHumanReview(BaseModel):
    decision: PlanHumanDecision
    reviewer_name: str | None = None
    reviewer_notes: str | None = None
    decided_at: datetime = Field(default_factory=datetime.now)
