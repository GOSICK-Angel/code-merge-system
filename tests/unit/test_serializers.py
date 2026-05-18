"""Tests for ``src.web.serializers`` (Phase 1 §P2-1 extraction).

Covers:
- byte-for-byte stability of the snapshot shape for the v1 fields (no
  silent drift from the pre-extraction ws_bridge implementation)
- additive Phase 1 fields: ``costSummary``, ``phaseElapsed``,
  ``decisionRecordCounts``
- truncate helpers (project summary + special instructions)
- per-helper branches for serialize_plan / serialize_human_request /
  serialize_judge_verdict / serialize_review_round / read_memory_snapshot
  (use SimpleNamespace stubs so we don't depend on the full pydantic
  models — those are exercised separately by their own model tests).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from src.models.config import MergeConfig
from src.models.decision import (
    DecisionSource,
    FileDecisionRecord,
    MergeDecision,
)
from src.models.diff import FileStatus
from src.models.state import MergePhase, MergeState, PhaseResult, SystemStatus
from src.web.serializers import (
    _decision_record_counts,
    _phase_elapsed,
    read_memory_snapshot,
    serialize_conflict_point,
    serialize_human_request,
    serialize_judge_verdict,
    serialize_plan,
    serialize_review_conclusion,
    serialize_review_round,
    serialize_state,
    truncate_instructions,
    truncate_project_summary,
)


@pytest.fixture
def minimal_state() -> MergeState:
    cfg = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/x")
    return MergeState(config=cfg)


class TestTruncateHelpers:
    def test_project_summary_short_input_unchanged(self) -> None:
        text = "one\ntwo"
        assert truncate_project_summary(text) == text

    def test_project_summary_caps_lines(self) -> None:
        text = "\n".join(f"line {i}" for i in range(20))
        out = truncate_project_summary(text)
        assert out.endswith("…")
        assert out.count("\n") <= 4  # 4 lines + ellipsis line

    def test_truncate_instructions_marks_overflow(self) -> None:
        long = "\n".join(f"l{i}" for i in range(30))
        out = truncate_instructions([long])
        assert out[0].endswith("… (truncated — see plan report)")

    def test_truncate_instructions_preserves_short(self) -> None:
        short = "line a\nline b"
        out = truncate_instructions([short])
        assert out == [short]


class TestSerializeStateBaseline:
    """v1 fields — must remain present and stable after extraction."""

    def test_baseline_fields_present(self, minimal_state: MergeState) -> None:
        snap = serialize_state(minimal_state)
        for key in (
            "runId",
            "status",
            "currentPhase",
            "phaseResults",
            "mergePlan",
            "fileClassifications",
            "fileDiffs",
            "fileDecisionRecords",
            "humanDecisionRequests",
            "humanDecisions",
            "judgeVerdict",
            "judgeRepairRounds",
            "planReviewLog",
            "reviewConclusion",
            "pendingUserDecisions",
            "gateHistory",
            "errors",
            "messages",
            "memory",
            "createdAt",
        ):
            assert key in snap, f"missing baseline field {key!r}"

    def test_status_enum_serialized_to_value(self, minimal_state: MergeState) -> None:
        minimal_state.status = SystemStatus.ANALYZING_CONFLICTS
        snap = serialize_state(minimal_state)
        assert snap["status"] == "analyzing_conflicts"

    def test_empty_state_safe(self, minimal_state: MergeState) -> None:
        snap = serialize_state(minimal_state)
        assert snap["mergePlan"] is None
        assert snap["judgeVerdict"] is None
        assert snap["reviewConclusion"] is None
        assert snap["pendingUserDecisions"] == []


class TestSerializeStateAdditiveFields:
    """v1.1 §2.3 additive fields — must always be present (None / empty dict
    when no data, never missing)."""

    def test_additive_fields_present_on_empty(self, minimal_state: MergeState) -> None:
        snap = serialize_state(minimal_state)
        assert "costSummary" in snap
        assert "phaseElapsed" in snap
        assert "decisionRecordCounts" in snap
        # No data yet → None / empty
        assert snap["costSummary"] is None
        assert snap["phaseElapsed"] == {}
        assert snap["decisionRecordCounts"] == {}

    def test_cost_summary_enriched_with_budget_knobs(
        self, minimal_state: MergeState
    ) -> None:
        """U-P2.11: cost_summary serialization adds limit_usd + warn_pct
        from config so the dashboard can render the budget progress bar."""
        minimal_state.cost_summary = {
            "total_cost_usd": 1.23,
            "total_tokens": 4567,
        }
        snap = serialize_state(minimal_state)
        assert snap["costSummary"]["total_cost_usd"] == 1.23
        assert snap["costSummary"]["total_tokens"] == 4567
        # config defaults: max_cost_usd=5.0, per_run_cost_warn_pct=0.8.
        assert snap["costSummary"]["limit_usd"] == 5.0
        assert snap["costSummary"]["warn_pct"] == 0.8

    def test_cost_summary_limit_usd_none_when_disabled(
        self, minimal_state: MergeState
    ) -> None:
        """Disabled cap (max_cost_usd=None) propagates as limit_usd=None
        so the front-end can hide the progress bar."""
        minimal_state.cost_summary = {"total_cost_usd": 0.5}
        minimal_state.config = minimal_state.config.model_copy(
            update={"max_cost_usd": None}
        )
        snap = serialize_state(minimal_state)
        assert snap["costSummary"]["limit_usd"] is None
        assert snap["costSummary"]["warn_pct"] == 0.8

    def test_phase_elapsed_computes_seconds(self, minimal_state: MergeState) -> None:
        start = datetime(2026, 5, 14, 12, 0, 0)
        end = start + timedelta(seconds=5)
        minimal_state.phase_results["analysis"] = PhaseResult(
            phase=MergePhase.ANALYSIS,
            status="completed",
            started_at=start,
            completed_at=end,
        )
        minimal_state.phase_results["plan_review"] = PhaseResult(
            phase=MergePhase.PLAN_REVIEW,
            status="running",
            started_at=start,
        )
        elapsed = _phase_elapsed(minimal_state)
        assert elapsed["analysis"] == 5.0
        assert elapsed["plan_review"] is None  # not finished yet
        snap = serialize_state(minimal_state)
        assert snap["phaseElapsed"]["analysis"] == 5.0

    def test_decision_record_counts_aggregates_by_source(
        self, minimal_state: MergeState
    ) -> None:
        minimal_state.file_decision_records = {
            "a.py": FileDecisionRecord(
                file_path="a.py",
                file_status=FileStatus.MODIFIED,
                decision=MergeDecision.TAKE_CURRENT,
                decision_source=DecisionSource.AUTO_PLANNER,
                rationale="r",
            ),
            "b.py": FileDecisionRecord(
                file_path="b.py",
                file_status=FileStatus.MODIFIED,
                decision=MergeDecision.SEMANTIC_MERGE,
                decision_source=DecisionSource.AUTO_PLANNER,
                rationale="r",
            ),
            "c.py": FileDecisionRecord(
                file_path="c.py",
                file_status=FileStatus.MODIFIED,
                decision=MergeDecision.MANUAL_PATCH,
                decision_source=DecisionSource.HUMAN,
                rationale="r",
            ),
        }
        counts = _decision_record_counts(minimal_state)
        assert counts == {"auto_planner": 2, "human": 1}
        snap = serialize_state(minimal_state)
        assert snap["decisionRecordCounts"] == {"auto_planner": 2, "human": 1}


class TestHelperBranches:
    """Smaller, focused checks against ``SimpleNamespace`` stubs so we cover
    the serializer branches without depending on every pydantic model
    constructor. Production code uses ``getattr`` / ``hasattr`` heavily so
    stub-based input is realistic enough."""

    def test_serialize_plan_returns_none_when_missing(
        self, minimal_state: MergeState
    ) -> None:
        assert serialize_plan(minimal_state) is None

    def test_serialize_plan_full(self) -> None:
        plan = SimpleNamespace(
            plan_id="p1",
            created_at=datetime(2026, 1, 1),
            upstream_ref="upstream/main",
            fork_ref="feat/x",
            merge_base_commit="abc",
            phases=[
                SimpleNamespace(
                    batch_id="b1",
                    phase=SimpleNamespace(value="analysis"),
                    file_paths=["a.py"],
                    risk_level=SimpleNamespace(value="low"),
                    layer_id=0,
                    change_category=SimpleNamespace(value="cosmetic"),
                )
            ],
            risk_summary=SimpleNamespace(model_dump=lambda mode: {"low": 1}),
            category_summary=SimpleNamespace(model_dump=lambda mode: {"cosmetic": 1}),
            layers=[
                SimpleNamespace(layer_id=0, name="L0", description="d", depends_on=[])
            ],
            project_context_summary="ctx\nline 2",
            special_instructions=["instr 1", "instr 2"],
        )
        state = SimpleNamespace(merge_plan=plan)
        out = serialize_plan(state)  # type: ignore[arg-type]
        assert out is not None
        assert out["plan_id"] == "p1"
        assert out["phases"][0]["phase"] == "analysis"
        assert out["risk_summary"] == {"low": 1}
        assert out["category_summary"] == {"cosmetic": 1}
        assert out["layers"][0]["layer_id"] == 0
        assert out["special_instructions"] == ["instr 1", "instr 2"]

    def test_serialize_plan_no_category_summary(self) -> None:
        plan = SimpleNamespace(
            plan_id="p1",
            created_at=None,
            upstream_ref="u",
            fork_ref="f",
            merge_base_commit="b",
            phases=[],
            risk_summary=SimpleNamespace(model_dump=lambda mode: {}),
            category_summary=None,
            layers=[],
            project_context_summary="",
            special_instructions=[],
        )
        out = serialize_plan(SimpleNamespace(merge_plan=plan))  # type: ignore[arg-type]
        assert out is not None
        assert out["created_at"] is None
        assert out["category_summary"] is None

    def test_serialize_human_request_buckets_severity(self) -> None:
        req = SimpleNamespace(
            file_path="a.py",
            priority=1,
            conflict_points=[
                SimpleNamespace(
                    conflict_type=SimpleNamespace(value="semantic"),
                    rationale="r",
                    confidence=0.9,
                    line_range="10-20",
                ),
                SimpleNamespace(
                    conflict_type=SimpleNamespace(value="syntax"),
                    rationale="r",
                    confidence=0.5,
                    line_range="",
                ),
                SimpleNamespace(
                    conflict_type=SimpleNamespace(value="trivial"),
                    rationale="r",
                    confidence=0.1,
                    line_range="",
                ),
            ],
            context_summary="ctx",
            upstream_change_summary="u",
            fork_change_summary="f",
            analyst_recommendation=SimpleNamespace(value="take_target"),
            analyst_confidence=0.8,
            analyst_rationale="ra",
            options=[
                SimpleNamespace(
                    option_key="opt1",
                    decision=SimpleNamespace(value="take_current"),
                    description="d",
                    risk_warning=None,
                )
            ],
            human_decision=SimpleNamespace(value="take_target"),
        )
        out = serialize_human_request(req)
        severities = [cp["severity"] for cp in out["conflict_points"]]
        assert severities == ["high", "medium", "low"]
        assert out["analyst_recommendation"] == "take_target"
        assert out["human_decision"] == "take_target"

    def test_serialize_human_request_string_recommendation(self) -> None:
        req = SimpleNamespace(
            file_path="a.py",
            priority=1,
            conflict_points=[],
            context_summary="",
            upstream_change_summary="",
            fork_change_summary="",
            analyst_recommendation="raw-string",
            analyst_confidence=None,
            analyst_rationale="",
            options=[],
            human_decision=None,
        )
        out = serialize_human_request(req)
        assert out["analyst_recommendation"] == "raw-string"
        assert out["human_decision"] is None

    def test_serialize_judge_verdict_returns_none_when_missing(
        self, minimal_state: MergeState
    ) -> None:
        assert serialize_judge_verdict(minimal_state) is None

    def test_serialize_judge_verdict_full(self) -> None:
        verdict = SimpleNamespace(
            verdict=SimpleNamespace(value="approve"),
            summary="ok",
            issues=[
                SimpleNamespace(
                    file_path="a.py",
                    issue_type="logic",
                    severity="high",
                    description="oops",
                )
            ],
            veto_triggered=True,
            veto_reason="bad",
            repair_instructions=[
                SimpleNamespace(instruction="fix it", is_repairable=True)
            ],
        )
        out = serialize_judge_verdict(SimpleNamespace(judge_verdict=verdict))  # type: ignore[arg-type]
        assert out is not None
        assert out["verdict"] == "approve"
        assert out["issues"][0]["severity"] == "high"
        assert out["repair_instructions"][0]["is_repairable"] is True

    def test_serialize_review_round_full(self) -> None:
        ts = datetime(2026, 1, 1)
        r = SimpleNamespace(
            round_number=1,
            verdict_result=SimpleNamespace(value="approved"),
            verdict_summary="ok",
            issues_count=0,
            issues_detail=[],
            planner_revision_summary="",
            planner_responses=[
                SimpleNamespace(
                    issue_id="i1",
                    file_path="a.py",
                    action=SimpleNamespace(value="agree"),
                    reason="r",
                    counter_proposal=None,
                )
            ],
            plan_diff=[
                SimpleNamespace(file_path="a.py", old_risk="high", new_risk="low")
            ],
            negotiation_messages=[
                SimpleNamespace(
                    sender="planner",
                    round_number=1,
                    content="msg",
                    timestamp=ts,
                )
            ],
            timestamp=ts,
        )
        out = serialize_review_round(r)
        assert out["round_number"] == 1
        assert out["verdict_result"] == "approved"
        assert out["planner_responses"][0]["action"] == "agree"
        assert out["plan_diff"][0]["new_risk"] == "low"
        assert out["negotiation_messages"][0]["sender"] == "planner"
        assert out["timestamp"] == "2026-01-01T00:00:00"

    def test_serialize_review_round_handles_none_lists(self) -> None:
        ts = datetime(2026, 1, 1)
        r = SimpleNamespace(
            round_number=2,
            verdict_result="raw-string",  # exercise else branch
            verdict_summary="",
            issues_count=0,
            issues_detail=[],
            planner_revision_summary="",
            planner_responses=None,
            plan_diff=None,
            negotiation_messages=None,
            timestamp=ts,
        )
        out = serialize_review_round(r)
        assert out["verdict_result"] == "raw-string"
        assert out["planner_responses"] == []
        assert out["plan_diff"] == []
        assert out["negotiation_messages"] == []

    def test_serialize_review_conclusion_returns_none_when_missing(
        self, minimal_state: MergeState
    ) -> None:
        assert serialize_review_conclusion(minimal_state) is None

    def test_serialize_review_conclusion_full(self) -> None:
        rc = SimpleNamespace(
            reason=SimpleNamespace(value="converged"),
            final_round=3,
            total_rounds=3,
            max_rounds=5,
            summary="ok",
            pending_decisions_count=0,
            rejection_details=None,
        )
        out = serialize_review_conclusion(SimpleNamespace(review_conclusion=rc))  # type: ignore[arg-type]
        assert out is not None
        assert out["reason"] == "converged"
        assert out["final_round"] == 3

    def test_read_memory_snapshot_empty_when_no_db_path(
        self, minimal_state: MergeState
    ) -> None:
        assert read_memory_snapshot(minimal_state) == {
            "phase_summaries": {},
            "entries": [],
        }

    def test_read_memory_snapshot_empty_when_db_missing(
        self, minimal_state: MergeState, tmp_path: pytest.TempPathFactory
    ) -> None:
        # type: ignore[assignment]
        minimal_state.memory_db_path = str(tmp_path / "no-such.db")  # type: ignore[arg-type]
        assert read_memory_snapshot(minimal_state) == {
            "phase_summaries": {},
            "entries": [],
        }


class TestSerializeConflictPoint:
    """L3 (Phase 2) — ConflictPoint needs the full intent / risk_factors /
    hunk_id payload so the diff marker overlay can render them on hover."""

    def _intent(
        self,
        description: str = "intent text",
        intent_type: str = "feature",
        confidence: float = 0.8,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            description=description,
            intent_type=intent_type,
            confidence=confidence,
        )

    def test_full_payload_includes_intents_and_risk_factors(self) -> None:
        cp = SimpleNamespace(
            conflict_id="cp-1",
            hunk_id="h-1",
            conflict_type=SimpleNamespace(value="logic_contradiction"),
            upstream_intent=self._intent("upstream adds retries", "feature", 0.9),
            fork_intent=self._intent("fork removes retries", "refactor", 0.6),
            can_coexist=False,
            suggested_decision=SimpleNamespace(value="take_target"),
            confidence=0.85,
            rationale="Direct conflict on retry policy",
            risk_factors=["regression-risk", "behavioral-shift"],
            line_range="42-58",
        )
        out = serialize_conflict_point(cp)
        assert out["conflict_id"] == "cp-1"
        assert out["hunk_id"] == "h-1"
        assert out["conflict_type"] == "logic_contradiction"
        assert (
            out["description"] == "logic_contradiction: Direct conflict on retry policy"
        )
        assert out["severity"] == "high"
        assert out["upstream_intent"]["description"] == "upstream adds retries"
        assert out["upstream_intent"]["confidence"] == 0.9
        assert out["fork_intent"]["intent_type"] == "refactor"
        assert out["can_coexist"] is False
        assert out["suggested_decision"] == "take_target"
        assert out["risk_factors"] == ["regression-risk", "behavioral-shift"]
        assert out["line_range"] == "42-58"

    def test_handles_none_intents_and_missing_optional_fields(self) -> None:
        cp = SimpleNamespace(
            conflict_type=SimpleNamespace(value="unknown"),
            upstream_intent=None,
            fork_intent=None,
            confidence=0.2,
            rationale="trivial",
            risk_factors=None,
        )
        out = serialize_conflict_point(cp)
        assert out["upstream_intent"] is None
        assert out["fork_intent"] is None
        assert out["risk_factors"] == []
        assert out["severity"] == "low"
        assert out["conflict_id"] is None
        assert out["hunk_id"] is None


class TestSerializeHumanRequestExtended:
    """Phase 2 — L3 needs request_id, custom_content, reviewer_notes,
    preview_content per option, and related_files."""

    def test_extended_fields_present(self) -> None:
        req = SimpleNamespace(
            request_id="req-1",
            file_path="a.py",
            priority=2,
            conflict_points=[],
            context_summary="ctx",
            upstream_change_summary="u",
            fork_change_summary="f",
            analyst_recommendation=SimpleNamespace(value="semantic_merge"),
            analyst_confidence=0.7,
            analyst_rationale="merge intents are compatible",
            options=[
                SimpleNamespace(
                    option_key="opt-1",
                    decision=SimpleNamespace(value="take_current"),
                    description="keep fork edits",
                    preview_content="--- a/x.py\n+++ b/x.py\n@@ ...",
                    risk_warning=None,
                )
            ],
            human_decision=None,
            custom_content="hand-written patch",
            reviewer_notes="not sure about retries",
            related_files=["x.py", "y.py"],
        )
        out = serialize_human_request(req)
        assert out["request_id"] == "req-1"
        assert out["analyst_recommendation"] == "semantic_merge"
        assert out["options"][0]["preview_content"].startswith("--- a/")
        assert out["custom_content"] == "hand-written patch"
        assert out["reviewer_notes"] == "not sure about retries"
        assert out["related_files"] == ["x.py", "y.py"]

    def test_missing_optional_fields_default_to_none(self) -> None:
        req = SimpleNamespace(
            file_path="b.py",
            priority=1,
            conflict_points=[],
            context_summary="",
            upstream_change_summary="",
            fork_change_summary="",
            analyst_recommendation=None,
            analyst_confidence=None,
            analyst_rationale="",
            options=[],
            human_decision=None,
        )
        out = serialize_human_request(req)
        assert out["request_id"] is None
        assert out["custom_content"] is None
        assert out["reviewer_notes"] is None
        assert out["related_files"] == []
        assert out["analyst_recommendation"] is None


class TestSerializeJudgeFieldsExtended:
    """Phase 4 — L4 widget needs failed_files / per-issue severity (from
    JudgeIssue.issue_level) / repair_instructions per file / verdict
    counts. Older test (TestHelperBranches.test_serialize_judge_verdict_full)
    only checked the v1 shape; this case locks the L4 extensions."""

    def test_judge_verdict_extended_payload(self) -> None:
        from src.web.serializers import serialize_judge_verdict

        verdict = SimpleNamespace(
            verdict_id="v-1",
            verdict=SimpleNamespace(value="fail"),
            summary="3 critical issues",
            failed_files=["a.py", "b.py"],
            passed_files=["c.py"],
            conditional_files=[],
            reviewed_files_count=3,
            critical_issues_count=2,
            high_issues_count=1,
            overall_confidence=0.92,
            blocking_issues=["a.py", "b.py"],
            veto_triggered=True,
            veto_reason="Customization grep count below baseline",
            issues=[
                SimpleNamespace(
                    issue_id="iss-1",
                    file_path="a.py",
                    issue_type="customization_grep_below_baseline",
                    issue_level=SimpleNamespace(value="critical"),
                    description="3 customization markers lost",
                    suggested_fix="restore @cvte_decorator on lines 12,45,89",
                    must_fix_before_merge=True,
                    resolvability=SimpleNamespace(value="fixable"),
                    affected_lines=[12, 45, 89],
                ),
                SimpleNamespace(
                    issue_id="iss-2",
                    file_path="b.py",
                    issue_type="smoke_test_failed",
                    issue_level=SimpleNamespace(value="high"),
                    description="auth flow regression",
                    suggested_fix=None,
                    must_fix_before_merge=True,
                    resolvability=SimpleNamespace(value="human_required"),
                    affected_lines=[],
                ),
            ],
            repair_instructions=[
                SimpleNamespace(
                    file_path="a.py",
                    instruction="re-add @cvte_decorator above lines 12/45/89",
                    is_repairable=True,
                    severity=SimpleNamespace(value="critical"),
                    source_issue_id="iss-1",
                ),
            ],
        )
        state = SimpleNamespace(judge_verdict=verdict)
        out = serialize_judge_verdict(state)  # type: ignore[arg-type]
        assert out is not None
        assert out["failed_files"] == ["a.py", "b.py"]
        assert out["passed_files"] == ["c.py"]
        assert out["critical_issues_count"] == 2
        assert out["overall_confidence"] == 0.92
        assert out["veto_triggered"] is True
        assert out["issues"][0]["severity"] == "critical"
        assert out["issues"][0]["must_fix_before_merge"] is True
        assert out["issues"][0]["affected_lines"] == [12, 45, 89]
        assert out["issues"][1]["resolvability"] == "human_required"
        assert out["repair_instructions"][0]["severity"] == "critical"
        assert out["repair_instructions"][0]["source_issue_id"] == "iss-1"


class TestSerializePlanHumanReview:
    """M13 — front-end ``serverDecided`` reads ``planHumanReview``."""

    def test_returns_none_when_not_set(self, minimal_state: MergeState) -> None:
        from src.web.serializers import serialize_plan_human_review

        assert serialize_plan_human_review(minimal_state) is None

    def test_full_payload(self) -> None:
        from src.web.serializers import serialize_plan_human_review

        review = SimpleNamespace(
            decision=SimpleNamespace(value="approve"),
            reviewer_name="web_user",
            reviewer_notes="LGTM",
            decided_at=datetime(2026, 5, 14, 12, 0),
            item_decisions=[SimpleNamespace(), SimpleNamespace(), SimpleNamespace()],
        )
        state = SimpleNamespace(plan_human_review=review)
        out = serialize_plan_human_review(state)  # type: ignore[arg-type]
        assert out == {
            "decision": "approve",
            "reviewer_name": "web_user",
            "reviewer_notes": "LGTM",
            "decided_at": "2026-05-14T12:00:00",
            "item_decisions_count": 3,
        }


class TestSerializeStatePhase4Fields:
    """Phase 4 additive fields on the top-level snapshot."""

    def test_judge_resolution_and_rerun_passthrough(
        self, minimal_state: MergeState
    ) -> None:
        minimal_state.judge_resolution = "rerun"
        minimal_state.rerun_round = 2
        snap = serialize_state(minimal_state)
        assert snap["judgeResolution"] == "rerun"
        assert snap["rerunRound"] == 2
        assert isinstance(snap["maxRerunRounds"], int)

    def test_plan_human_review_passthrough(self, minimal_state: MergeState) -> None:
        from src.models.plan_review import PlanHumanDecision, PlanHumanReview

        minimal_state.plan_human_review = PlanHumanReview(
            decision=PlanHumanDecision.APPROVE,
            reviewer_name="web_user",
            reviewer_notes=None,
            item_decisions=[],
        )
        snap = serialize_state(minimal_state)
        assert snap["planHumanReview"] is not None
        assert snap["planHumanReview"]["decision"] == "approve"

    def test_plan_human_review_is_none_by_default(
        self, minimal_state: MergeState
    ) -> None:
        snap = serialize_state(minimal_state)
        assert snap["planHumanReview"] is None
