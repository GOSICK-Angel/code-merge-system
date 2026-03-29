from datetime import datetime
from enum import Enum
from uuid import uuid4
from pydantic import BaseModel, Field
from src.models.diff import FileStatus


class MergeDecision(str, Enum):
    TAKE_CURRENT = "take_current"
    TAKE_TARGET = "take_target"
    SEMANTIC_MERGE = "semantic_merge"
    MANUAL_PATCH = "manual_patch"
    ESCALATE_HUMAN = "escalate_human"
    SKIP = "skip"


class DecisionSource(str, Enum):
    AUTO_PLANNER = "auto_planner"
    AUTO_EXECUTOR = "auto_executor"
    HUMAN = "human"
    BATCH_HUMAN = "batch_human"


class FileDecisionRecord(BaseModel):
    record_id: str = Field(default_factory=lambda: str(uuid4()))
    file_path: str
    file_status: FileStatus
    decision: MergeDecision
    decision_source: DecisionSource
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    rationale: str
    applied_patch: str | None = None
    original_snapshot: str | None = None
    merged_content_preview: str | None = None
    discarded_content: str | None = None
    discard_reason: str | None = None
    conflict_points_resolved: list[str] = Field(default_factory=list)
    human_notes: str | None = None
    phase: str = ""
    agent: str = ""
    timestamp: datetime = Field(default_factory=datetime.now)
    is_rolled_back: bool = False
    rollback_reason: str | None = None
