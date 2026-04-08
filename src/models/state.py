from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4
from pydantic import BaseModel, Field
from src.models.config import MergeConfig
from src.models.plan import MergePlan, MergePhase
from src.models.diff import RiskLevel, FileChangeCategory
from src.models.decision import MergeDecision, FileDecisionRecord
from src.models.judge import JudgeVerdict
from src.models.human import HumanDecisionRequest
from src.models.plan_judge import PlanJudgeVerdict
from src.models.plan_review import PlanReviewRound, PlanHumanReview
from src.memory.models import MergeMemory
from src.models.dispute import PlanDisputeRequest
from src.models.conflict import ConflictAnalysis

if TYPE_CHECKING:
    from src.tools.config_drift_detector import ConfigDriftReport
    from src.tools.pollution_auditor import PollutionAuditReport


class SystemStatus(str, Enum):
    INITIALIZED = "initialized"
    PLANNING = "planning"
    PLAN_REVIEWING = "plan_reviewing"
    PLAN_REVISING = "plan_revising"
    AUTO_MERGING = "auto_merging"
    PLAN_DISPUTE_PENDING = "plan_dispute_pending"
    ANALYZING_CONFLICTS = "analyzing_conflicts"
    AWAITING_HUMAN = "awaiting_human"
    JUDGE_REVIEWING = "judge_reviewing"
    GENERATING_REPORT = "generating_report"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"


class PhaseResult(BaseModel):
    phase: MergePhase
    status: Literal["pending", "running", "completed", "failed", "skipped"]
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None


class MergeState(BaseModel):
    run_id: str = Field(default_factory=lambda: str(uuid4()))
    config: MergeConfig
    status: SystemStatus = SystemStatus.INITIALIZED
    current_phase: MergePhase = MergePhase.ANALYSIS
    phase_results: dict[str, PhaseResult] = Field(default_factory=dict)

    merge_plan: MergePlan | None = None
    file_classifications: dict[str, RiskLevel] = Field(default_factory=dict)
    file_categories: dict[str, FileChangeCategory] = Field(default_factory=dict)
    merge_base_commit: str = ""
    plan_revision_rounds: int = 0

    plan_judge_verdict: PlanJudgeVerdict | None = None
    plan_review_log: list[PlanReviewRound] = Field(default_factory=list)
    plan_human_review: PlanHumanReview | None = None

    file_decision_records: dict[str, FileDecisionRecord] = Field(default_factory=dict)
    applied_patches: list[str] = Field(default_factory=list)
    plan_disputes: list[PlanDisputeRequest] = Field(default_factory=list)

    conflict_analyses: dict[str, ConflictAnalysis] = Field(default_factory=dict)

    human_decision_requests: dict[str, HumanDecisionRequest] = Field(
        default_factory=dict
    )
    human_decisions: dict[str, MergeDecision] = Field(default_factory=dict)

    judge_verdict: JudgeVerdict | None = None
    judge_repair_rounds: int = 0
    judge_verdicts_log: list[dict[str, Any]] = Field(default_factory=list)

    gate_baselines: dict[str, str] = Field(
        default_factory=dict,
        description="gate_name -> stdout_tail baseline output",
    )
    gate_history: list[dict[str, Any]] = Field(default_factory=list)
    consecutive_gate_failures: int = 0

    pollution_audit: PollutionAuditReport | None = Field(
        default=None,
        description="PollutionAuditReport from Phase 0 pre-check",
    )
    config_drifts: ConfigDriftReport | None = Field(
        default=None,
        description="ConfigDriftReport from drift detection",
    )

    memory: MergeMemory = Field(default_factory=MergeMemory)

    errors: list[dict[str, Any]] = Field(default_factory=list)
    messages: list[dict[str, Any]] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    checkpoint_path: str | None = None

    model_config = {"use_enum_values": False}


def _rebuild_state_model() -> None:
    from src.tools.config_drift_detector import ConfigDriftReport
    from src.tools.pollution_auditor import PollutionAuditReport

    MergeState.model_rebuild(
        _types_namespace={
            "ConfigDriftReport": ConfigDriftReport,
            "PollutionAuditReport": PollutionAuditReport,
        }
    )


_rebuild_state_model()
