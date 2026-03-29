from datetime import datetime
from uuid import uuid4
from pydantic import BaseModel, Field
from src.models.diff import RiskLevel


class PlanDisputeRequest(BaseModel):
    dispute_id: str = Field(default_factory=lambda: str(uuid4()))
    raised_by: str = "executor"
    phase: str
    disputed_files: list[str]
    dispute_reason: str
    suggested_reclassification: dict[str, RiskLevel]
    impact_assessment: str
    evidence: str | None = None
    timestamp: datetime = Field(default_factory=datetime.now)
    resolved: bool = False
    resolution_summary: str | None = None
