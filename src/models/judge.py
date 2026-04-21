from datetime import datetime
from enum import Enum
from typing import Annotated, Any
from uuid import uuid4
from pydantic import BaseModel, BeforeValidator, Field


def _coerce_line_list(v: Any) -> list[int]:
    """Accept a list of ints or int-coercible values; silently drop non-numeric sentinels."""
    if not isinstance(v, list):
        return []
    result: list[int] = []
    for item in v:
        if isinstance(item, int):
            result.append(item)
        elif isinstance(item, str):
            try:
                result.append(int(item))
            except ValueError:
                pass
    return result


_LineList = Annotated[list[int], BeforeValidator(_coerce_line_list)]


class VerdictType(str, Enum):
    PASS = "pass"
    CONDITIONAL = "conditional"
    FAIL = "fail"


class IssueSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


IssueLevel = IssueSeverity


class JudgeIssue(BaseModel):
    issue_id: str = Field(default_factory=lambda: str(uuid4()))
    file_path: str
    issue_level: IssueSeverity
    issue_type: str
    description: str
    affected_lines: _LineList = Field(default_factory=list)
    suggested_fix: str | None = None
    must_fix_before_merge: bool = False
    veto_condition: str | None = None


class RepairInstruction(BaseModel):
    file_path: str
    instruction: str
    severity: IssueSeverity = IssueSeverity.HIGH
    is_repairable: bool = True
    source_issue_id: str | None = None


class CustomizationViolation(BaseModel):
    customization_name: str
    verification_type: str
    expected_pattern: str
    checked_files: list[str] = Field(default_factory=list)
    match_count: int = 0


VETO_CONDITIONS: list[str] = [
    "B-class file differs from upstream",
    "D-missing file not present in HEAD",
    "Customization disappeared without annotation",
    "Upstream function block (>20 lines) missing in merged",
    "TODO [merge] count exceeds phase limit",
    "Unannotated TODO [check] exists",
    "Top-level invocation/decorator lost after merge",
    "Customization grep count below baseline",
    "Customization line retention below required ratio",
    "Shadow-path conflict unresolved",
    "Cross-layer assertion keys missing",
    "Reverse-impact unhandled for upstream interface change",
    "Smoke test failed",
    "Sentinel hit in AUTO_SAFE file unacknowledged",
    "Config retention required line missing",
]


ISSUE_TYPES_NEW: set[str] = {
    "top_level_invocation_lost",
    "customization_grep_below_baseline",
    "customization_line_retention_below_ratio",
    "shadow_conflict_unresolved",
    "cross_layer_assertion_missing",
    "reverse_impact_unhandled",
    "smoke_test_failed",
    "sentinel_hit_unacknowledged",
    "config_retention_violation",
}


class JudgeVerdict(BaseModel):
    verdict_id: str = Field(default_factory=lambda: str(uuid4()))
    verdict: VerdictType
    reviewed_files_count: int
    passed_files: list[str]
    failed_files: list[str]
    conditional_files: list[str]
    issues: list[JudgeIssue]
    critical_issues_count: int
    high_issues_count: int
    overall_confidence: float = Field(ge=0.0, le=1.0)
    summary: str
    blocking_issues: list[str]
    timestamp: datetime
    judge_model: str
    veto_triggered: bool = False
    veto_reason: str | None = None
    repair_instructions: list[RepairInstruction] = Field(default_factory=list)
    customization_violations: list[CustomizationViolation] = Field(default_factory=list)


class BatchVerdict(BaseModel):
    layer_id: int | None = None
    approved: bool
    needs_repair: bool = False
    issues: list[JudgeIssue] = Field(default_factory=list)
    repair_instructions: list[RepairInstruction] = Field(default_factory=list)
    reviewed_files: list[str] = Field(default_factory=list)
    round_num: int = 0


class DisputePoint(BaseModel):
    issue_id: str
    counter_evidence: str
    accepts: bool = False


class ExecutorRebuttal(BaseModel):
    accepts_all: bool
    dispute_points: list[DisputePoint] = Field(default_factory=list)
    repair_instructions: list[RepairInstruction] = Field(default_factory=list)
    overall_rationale: str = ""
