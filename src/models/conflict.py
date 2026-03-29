from enum import Enum
from uuid import uuid4
from pydantic import BaseModel, Field
from src.models.decision import MergeDecision


class ConflictType(str, Enum):
    CONCURRENT_MODIFICATION = "concurrent_modification"
    LOGIC_CONTRADICTION = "logic_contradiction"
    SEMANTIC_EQUIVALENT = "semantic_equivalent"
    DEPENDENCY_UPDATE = "dependency_update"
    INTERFACE_CHANGE = "interface_change"
    DELETION_VS_MODIFICATION = "deletion_vs_modification"
    REFACTOR_VS_FEATURE = "refactor_vs_feature"
    CONFIGURATION = "configuration"
    UNKNOWN = "unknown"


class ChangeIntent(BaseModel):
    description: str
    intent_type: str
    confidence: float = Field(ge=0.0, le=1.0)


class ConflictPoint(BaseModel):
    conflict_id: str = Field(default_factory=lambda: str(uuid4()))
    file_path: str
    hunk_id: str
    conflict_type: ConflictType
    upstream_intent: ChangeIntent
    fork_intent: ChangeIntent
    can_coexist: bool
    suggested_decision: MergeDecision
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    risk_factors: list[str] = Field(default_factory=list)
    similar_conflicts: list[str] = Field(default_factory=list)


class ConflictAnalysis(BaseModel):
    analysis_id: str = Field(default_factory=lambda: str(uuid4()))
    file_path: str
    conflict_points: list[ConflictPoint]
    overall_confidence: float = Field(ge=0.0, le=1.0)
    recommended_strategy: MergeDecision
    conflict_type: ConflictType = ConflictType.UNKNOWN
    can_coexist: bool = False
    is_security_sensitive: bool = False
    rationale: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    analysis_notes: str = ""
