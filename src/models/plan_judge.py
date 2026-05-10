from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import uuid4
from pydantic import BaseModel, Field
from src.models.diff import RiskLevel


class PlanJudgeResult(str, Enum):
    APPROVED = "approved"
    REVISION_NEEDED = "revision_needed"
    CRITICAL_REPLAN = "critical_replan"
    LLM_UNAVAILABLE = "llm_unavailable"


# P2-7: provenance tag — distinguishes deterministic precheck issues
# from LLM-Judge issues. The Planner-Judge interaction log surfaces
# this so operators can tell at a glance who flagged what; the conflict
# detector in plan_review.py uses it to spot cases where the two
# sources disagree on the same file.
IssueSource = Literal["llm", "precheck"]


class PlanIssue(BaseModel):
    issue_id: str = Field(default_factory=lambda: str(uuid4()))
    file_path: str
    # ``None`` indicates NOT-BATCHED: the file is absent from every
    # batch in the plan, so there is no current classification to cite.
    # Producers that have a real classification continue to set this
    # field; consumers that render it must handle the ``None`` branch.
    current_classification: RiskLevel | None = None
    suggested_classification: RiskLevel
    reason: str
    issue_type: str
    source: IssueSource = Field(
        default="llm",
        description="Where the issue came from. ``llm`` (default for "
        "back-compat) means the Plan-Judge LLM raised it; ``precheck`` "
        "means the deterministic integrity check did.",
    )


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
