from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field
from src.models.plan_judge import PlanJudgeResult


class PlanHumanDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    MODIFY = "modify"


class IssueResponseAction(str, Enum):
    ACCEPT = "accept"
    REJECT = "reject"
    DISCUSS = "discuss"


class PlannerIssueResponse(BaseModel):
    issue_id: str
    file_path: str
    action: IssueResponseAction
    reason: str
    counter_proposal: str | None = None


class PlanDiffEntry(BaseModel):
    file_path: str
    old_risk: str
    new_risk: str


class NegotiationMessage(BaseModel):
    sender: str  # "planner" | "planner_judge"
    round_number: int
    content: str
    timestamp: datetime = Field(default_factory=datetime.now)


class DecisionOption(BaseModel):
    key: str
    label: str
    description: str = ""


class UserDecisionItem(BaseModel):
    item_id: str
    file_path: str
    description: str
    risk_context: str = ""
    current_classification: str
    options: list[DecisionOption] = Field(default_factory=list)
    user_choice: str | None = None
    user_input: str | None = None


class SegmentTelemetrySummary(BaseModel):
    """P3-10: per-round aggregate of segment-level review cost.

    All counts are *estimates* — token figures come from the heuristic
    in ``src/llm/context.py``, not from a billing-grade API hook. They
    are accurate enough to tune ``REVIEW_SEGMENT_SIZE`` against
    measured cost.
    """

    llm_segments: int = 0
    cache_hit_segments: int = 0
    safelist_segments: int = 0
    total_latency_s: float = 0.0
    total_tokens_in: int = 0
    total_tokens_out: int = 0


class PlanReviewRound(BaseModel):
    round_number: int
    verdict_result: PlanJudgeResult
    verdict_summary: str
    issues_count: int
    issues_detail: list[dict[str, str]] = Field(default_factory=list)
    planner_revision_summary: str | None = None
    planner_responses: list[PlannerIssueResponse] = Field(default_factory=list)
    plan_diff: list[PlanDiffEntry] = Field(default_factory=list)
    negotiation_messages: list[NegotiationMessage] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=datetime.now)
    segment_telemetry: SegmentTelemetrySummary | None = Field(
        default=None,
        description="P3-10: aggregated segment-level cost for this "
        "round. None for legacy rounds and for short-circuit / "
        "cache-only rounds where no LLM call fired.",
    )


class ReviewConclusionReason(str, Enum):
    APPROVED = "approved"
    MAX_ROUNDS = "max_rounds"
    STALLED = "stalled"
    LLM_FAILURE = "llm_failure"
    CRITICAL_REPLAN = "critical_replan"
    # P2-7: LLM judge and the deterministic precheck disagreed on the
    # *direction* for the same file (one wants escalate, the other
    # wants demote). Re-feeding the plan would just oscillate, so the
    # phase transitions to AWAITING_HUMAN and asks the operator to
    # arbitrate.
    SOURCE_CONFLICT = "source_conflict"


class ReviewConclusion(BaseModel):
    reason: ReviewConclusionReason
    final_round: int
    total_rounds: int
    max_rounds: int
    summary: str
    pending_decisions_count: int = 0
    rejection_details: list[dict[str, str]] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=datetime.now)


class PlanHumanReview(BaseModel):
    decision: PlanHumanDecision
    reviewer_name: str | None = None
    reviewer_notes: str | None = None
    item_decisions: list[UserDecisionItem] = Field(default_factory=list)
    decided_at: datetime = Field(default_factory=datetime.now)
