from datetime import datetime
from enum import Enum
from uuid import uuid4
from pydantic import BaseModel, Field


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
    affected_lines: list[int] = Field(default_factory=list)
    suggested_fix: str | None = None
    must_fix_before_merge: bool = False


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
