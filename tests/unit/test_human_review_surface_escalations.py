"""方案6 part1: human_review surfaces internal escalations into the gate.

Internal ``escalate(0.0)`` records (commit-replay / skipped auto-merge layers /
catch-up fallbacks) only land in ``file_decision_records``; without surfacing
they never appear on the AWAITING_HUMAN screen. ``_surface_internal_escalations``
appends an undecided ``UserDecisionItem`` for each unsurfaced, non-human
escalation so the O-L4 guard can hold the run for a decision.
"""

from __future__ import annotations

from src.core.phases.human_review import _surface_internal_escalations
from src.models.config import MergeConfig
from src.models.decision import DecisionSource, FileDecisionRecord, MergeDecision
from src.models.diff import FileStatus
from src.models.plan_review import UserDecisionItem
from src.models.state import MergeState


def _state() -> MergeState:
    return MergeState(config=MergeConfig(upstream_ref="upstream/main", fork_ref="fork"))


def _escalation(fp: str, source: DecisionSource) -> FileDecisionRecord:
    return FileDecisionRecord(
        file_path=fp,
        file_status=FileStatus.MODIFIED,
        decision=MergeDecision.ESCALATE_HUMAN,
        decision_source=source,
        confidence=0.0,
        rationale="internal escalate",
    )


class TestSurfaceInternalEscalations:
    def test_surfaces_unsurfaced_internal_escalation(self):
        state = _state()
        state.file_decision_records["treeshake/x.ts"] = _escalation(
            "treeshake/x.ts", DecisionSource.AUTO_EXECUTOR
        )
        n = _surface_internal_escalations(state)
        assert n == 1
        items = state.pending_user_decisions
        assert len(items) == 1
        assert items[0].file_path == "treeshake/x.ts"
        assert items[0].item_id == "internal_escalation_treeshake/x.ts"
        assert items[0].user_choice is None
        assert items[0].options  # actionable: take_target / take_current / ...

    def test_already_gated_not_duplicated(self):
        state = _state()
        state.file_decision_records["a.ts"] = _escalation(
            "a.ts", DecisionSource.AUTO_EXECUTOR
        )
        state.pending_user_decisions.append(
            UserDecisionItem(
                item_id="existing",
                file_path="a.ts",
                description="already gated",
                current_classification="human_required",
            )
        )
        assert _surface_internal_escalations(state) == 0
        assert len(state.pending_user_decisions) == 1

    def test_human_resolved_not_surfaced(self):
        state = _state()
        state.file_decision_records["a.ts"] = _escalation("a.ts", DecisionSource.HUMAN)
        assert _surface_internal_escalations(state) == 0
        assert state.pending_user_decisions == []

    def test_non_escalation_record_not_surfaced(self):
        state = _state()
        state.file_decision_records["a.ts"] = FileDecisionRecord(
            file_path="a.ts",
            file_status=FileStatus.MODIFIED,
            decision=MergeDecision.TAKE_TARGET,
            decision_source=DecisionSource.AUTO_EXECUTOR,
            rationale="took target",
        )
        assert _surface_internal_escalations(state) == 0
        assert state.pending_user_decisions == []
