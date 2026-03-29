from datetime import datetime
from uuid import uuid4
from pydantic import BaseModel, Field
from src.models.decision import MergeDecision
from src.models.conflict import ConflictPoint


class DecisionOption(BaseModel):
    option_key: str
    decision: MergeDecision
    description: str
    preview_content: str | None = None
    risk_warning: str | None = None


class HumanDecisionRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid4()))
    file_path: str
    priority: int = Field(ge=1, le=10)
    conflict_points: list[ConflictPoint]
    context_summary: str
    upstream_change_summary: str
    fork_change_summary: str
    analyst_recommendation: MergeDecision
    analyst_confidence: float
    analyst_rationale: str
    options: list[DecisionOption]
    related_files: list[str] = Field(default_factory=list)
    deadline: datetime | None = None
    created_at: datetime
    human_decision: MergeDecision | None = None
    custom_content: str | None = None
    reviewer_name: str | None = None
    reviewer_notes: str | None = None
    decided_at: datetime | None = None
    is_batch_decision: bool = False
