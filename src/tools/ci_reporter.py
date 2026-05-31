import json
from typing import Any

from src.models.decision import DecisionSource, FileDecisionRecord, MergeDecision
from src.models.state import MergeState, SystemStatus
from src.models.judge import VerdictType

_AUTO_SOURCES = frozenset({DecisionSource.AUTO_PLANNER, DecisionSource.AUTO_EXECUTOR})
_HUMAN_SOURCES = frozenset({DecisionSource.HUMAN, DecisionSource.BATCH_HUMAN})


def _outcome(rec: FileDecisionRecord) -> str:
    """Coarse outcome bucket for the escalation-by-category matrix."""
    if rec.decision == MergeDecision.ESCALATE_HUMAN:
        return "escalated"
    if rec.decision_source in _HUMAN_SOURCES:
        return "human"
    if rec.decision_source in _AUTO_SOURCES:
        return "auto"
    return "other"


def _escalation_by_category(state: MergeState) -> dict[str, dict[str, int]]:
    """W5: a ``{category: {auto, escalated, human, other}}`` matrix, joining
    ``state.file_categories`` with ``state.file_decision_records`` on file path.

    Pure local computation over existing state — no new tracking, no network. An
    operator reads it to see the escalation *shape*: escalations concentrated in
    ``both_changed`` (C-class) are expected; any in ``upstream_only`` (B-class)
    are a red flag. Files categorized but never decided (e.g. unchanged) and
    files decided without a category both degrade gracefully (the latter bucket
    under ``"unknown"``).
    """
    matrix: dict[str, dict[str, int]] = {}
    for fp, rec in state.file_decision_records.items():
        cat = state.file_categories.get(fp)
        cat_key = cat.value if cat is not None else "unknown"
        bucket = matrix.setdefault(
            cat_key, {"auto": 0, "escalated": 0, "human": 0, "other": 0}
        )
        bucket[_outcome(rec)] += 1
    return matrix


def build_ci_summary(state: MergeState) -> dict[str, Any]:
    """Build a machine-readable CI summary from merge state."""
    status_map: dict[SystemStatus, str] = {
        SystemStatus.COMPLETED: "success",
        SystemStatus.AWAITING_HUMAN: "needs_human",
        SystemStatus.FAILED: "failed",
    }
    status = status_map.get(state.status, "unknown")

    total_files = 0
    if state.merge_plan and state.merge_plan.risk_summary:
        total_files = state.merge_plan.risk_summary.total_files

    auto_merged = sum(
        1
        for rec in state.file_decision_records.values()
        if rec.decision_source.value in ("auto_planner", "auto_executor")
    )

    # Files awaiting / having a human decision live in two collections:
    #   * pending_user_decisions     — plan-stage HUMAN_REQUIRED + conflict-marker
    #   * human_decision_requests    — conflict_analysis ESCALATE_HUMAN
    # Count by file_path union so a plan-stage halt (no human_decision_requests
    # yet) is not reported as human_required=0, and a file appearing in both
    # stages is not double-counted.
    undecided_paths = {
        item.file_path
        for item in state.pending_user_decisions
        if item.user_choice is None
    } | {
        fp
        for fp, req in state.human_decision_requests.items()
        if req.human_decision is None
    }
    decided_paths = (
        {
            item.file_path
            for item in state.pending_user_decisions
            if item.user_choice is not None
        }
        | {
            fp
            for fp, req in state.human_decision_requests.items()
            if req.human_decision is not None
        }
    ) - undecided_paths
    human_required = len(undecided_paths)
    human_decided = len(decided_paths)
    failed = len(state.errors)

    judge_verdict = "none"
    if state.judge_verdict is not None:
        jv = state.judge_verdict.verdict
        if isinstance(jv, VerdictType):
            judge_verdict = jv.value
        else:
            judge_verdict = str(jv)

    if status == "success" and failed > 0:
        status = "partial_failure"

    return {
        "status": status,
        "run_id": state.run_id,
        "total_files": total_files,
        "auto_merged": auto_merged,
        "human_required": human_required,
        "human_decided": human_decided,
        "failed_count": failed,
        "judge_verdict": judge_verdict,
        "errors": [err.get("message", "") for err in state.errors[-5:]],
        "by_category": _escalation_by_category(state),
    }


def format_ci_summary(summary: dict[str, Any]) -> str:
    """Format CI summary as JSON string."""
    return json.dumps(summary, indent=2, default=str)
