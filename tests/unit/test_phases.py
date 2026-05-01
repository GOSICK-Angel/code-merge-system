"""Tests for extracted Phase classes.

Each test verifies that the Phase produces the correct PhaseOutcome
and makes the expected state transitions via mocked dependencies.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.phases.base import PhaseContext, PhaseOutcome
from src.core.phases.initialize import InitializePhase
from src.core.phases.planning import PlanningPhase
from src.core.phases.plan_review import PlanReviewPhase
from src.core.phases.auto_merge import AutoMergePhase
from src.core.phases.conflict_analysis import (
    ConflictAnalysisPhase,
    _select_merge_strategy,
)
from src.core.phases.human_review import HumanReviewPhase
from src.core.phases.judge_review import JudgeReviewPhase
from src.core.phases.report_generation import ReportGenerationPhase
from src.models.config import MergeConfig, ThresholdConfig
from src.models.decision import MergeDecision
from src.models.plan import (
    MergePlan,
    MergePhase,
    PhaseFileBatch,
    RiskSummary,
)
from src.models.state import MergeState, SystemStatus


def _make_config(**overrides):
    defaults = {"upstream_ref": "upstream/main", "fork_ref": "fork/main"}
    defaults.update(overrides)
    return MergeConfig(**defaults)


def _make_state(**overrides):
    config = overrides.pop("config", _make_config())
    state = MergeState(config=config, **overrides)
    return state


def _make_plan(**overrides):
    defaults = {
        "created_at": datetime.now(),
        "upstream_ref": "upstream/main",
        "fork_ref": "fork/main",
        "merge_base_commit": "abc123",
        "phases": [
            PhaseFileBatch(
                batch_id="b1",
                phase=MergePhase.ANALYSIS,
                file_paths=["a.py"],
                risk_level="auto_safe",
            )
        ],
        "risk_summary": RiskSummary(
            total_files=1,
            auto_safe_count=1,
            auto_risky_count=0,
            human_required_count=0,
            deleted_only_count=0,
            binary_count=0,
            excluded_count=0,
            estimated_auto_merge_rate=1.0,
        ),
        "project_context_summary": "test project",
    }
    defaults.update(overrides)
    return MergePlan(**defaults)


def _make_ctx(**overrides):
    defaults = {
        "config": _make_config(),
        "git_tool": MagicMock(),
        "gate_runner": MagicMock(),
        "state_machine": MagicMock(),
        "message_bus": MagicMock(),
        "checkpoint": MagicMock(),
        "phase_runner": MagicMock(),
        "memory_store": MagicMock(),
        "summarizer": MagicMock(),
    }
    defaults.update(overrides)
    return PhaseContext(**defaults)


# ---------------------------------------------------------------------------
# PlanningPhase
# ---------------------------------------------------------------------------


class TestPlanningPhase:
    @pytest.mark.asyncio
    async def test_planning_success(self):
        planner = AsyncMock()
        planner.run = AsyncMock()
        ctx = _make_ctx(agents={"planner": planner})
        state = _make_state(status=SystemStatus.PLANNING)

        phase = PlanningPhase()
        outcome = await phase.execute(state, ctx)

        planner.run.assert_awaited_once_with(state)
        ctx.state_machine.transition.assert_called_once_with(
            state, SystemStatus.PLAN_REVIEWING, "phase 1 complete"
        )
        assert outcome.target_status == SystemStatus.PLAN_REVIEWING
        assert outcome.should_checkpoint
        assert outcome.checkpoint_tag == "after_phase1"
        assert outcome.memory_phase == "planning"

    @pytest.mark.asyncio
    async def test_planning_failure_propagates(self):
        planner = AsyncMock()
        planner.run = AsyncMock(side_effect=RuntimeError("LLM failed"))
        ctx = _make_ctx(agents={"planner": planner})
        state = _make_state(status=SystemStatus.PLANNING)

        phase = PlanningPhase()
        with pytest.raises(RuntimeError, match="LLM failed"):
            await phase.execute(state, ctx)

        assert state.phase_results["analysis"].status == "failed"


# ---------------------------------------------------------------------------
# PlanReviewPhase
# ---------------------------------------------------------------------------


class TestPlanReviewPhase:
    @pytest.mark.asyncio
    async def test_approved_no_human_required_skips_await(self):
        """When plan is approved with no HUMAN_REQUIRED files, go straight to AUTO_MERGING."""
        from src.models.plan_judge import PlanJudgeResult, PlanJudgeVerdict

        verdict = PlanJudgeVerdict(
            result=PlanJudgeResult.APPROVED,
            issues=[],
            approved_files_count=5,
            flagged_files_count=0,
            summary="All good",
            judge_model="gpt-4o",
            timestamp=datetime.now(),
        )
        planner_judge = MagicMock()
        planner_judge.review_plan = AsyncMock(return_value=verdict)

        state = _make_state(status=SystemStatus.PLAN_REVIEWING)
        state.merge_plan = _make_plan()  # only auto_safe files

        ctx = _make_ctx(agents={"planner": MagicMock(), "planner_judge": planner_judge})

        phase = PlanReviewPhase()
        with patch("src.core.phases.plan_review.write_plan_review_report"):
            outcome = await phase.execute(state, ctx)

        assert outcome.target_status == SystemStatus.AUTO_MERGING
        assert outcome.reason == "plan approved, no human decisions needed"
        assert outcome.should_checkpoint

    @pytest.mark.asyncio
    async def test_approved_with_human_required_awaits(self):
        """When plan is approved but has HUMAN_REQUIRED files, go to AWAITING_HUMAN."""
        from src.models.plan_judge import PlanJudgeResult, PlanJudgeVerdict
        from src.models.plan import PhaseFileBatch
        from src.models.diff import RiskLevel

        verdict = PlanJudgeVerdict(
            result=PlanJudgeResult.APPROVED,
            issues=[],
            approved_files_count=1,
            flagged_files_count=1,
            summary="One file needs human review",
            judge_model="gpt-4o",
            timestamp=datetime.now(),
        )
        planner_judge = MagicMock()
        planner_judge.review_plan = AsyncMock(return_value=verdict)

        state = _make_state(status=SystemStatus.PLAN_REVIEWING)
        plan = _make_plan()
        plan.phases.append(
            PhaseFileBatch(
                batch_id="b_human",
                phase=MergePhase.CONFLICT_ANALYSIS,
                file_paths=["sensitive.py"],
                risk_level=RiskLevel.HUMAN_REQUIRED,
            )
        )
        state.merge_plan = plan

        ctx = _make_ctx(agents={"planner": MagicMock(), "planner_judge": planner_judge})

        phase = PlanReviewPhase()
        with patch("src.core.phases.plan_review.write_plan_review_report"):
            outcome = await phase.execute(state, ctx)

        assert outcome.target_status == SystemStatus.AWAITING_HUMAN
        assert outcome.reason == "plan approved by both agents"
        assert outcome.should_checkpoint

    @pytest.mark.asyncio
    async def test_llm_failure_skips_review(self):
        from src.models.plan_judge import PlanJudgeResult, PlanJudgeVerdict

        verdict = PlanJudgeVerdict(
            result=PlanJudgeResult.REVISION_NEEDED,
            issues=[],
            approved_files_count=0,
            flagged_files_count=0,
            summary="Parse failed: invalid JSON",
            judge_model="gpt-4o",
            timestamp=datetime.now(),
        )
        planner_judge = MagicMock()
        planner_judge.review_plan = AsyncMock(return_value=verdict)

        state = _make_state(status=SystemStatus.PLAN_REVIEWING)
        state.merge_plan = _make_plan()

        ctx = _make_ctx(agents={"planner": MagicMock(), "planner_judge": planner_judge})

        phase = PlanReviewPhase()
        with patch("src.core.phases.plan_review.write_plan_review_report"):
            outcome = await phase.execute(state, ctx)

        assert outcome.target_status == SystemStatus.AWAITING_HUMAN
        assert "unavailable" in outcome.reason


# ---------------------------------------------------------------------------
# HumanReviewPhase
# ---------------------------------------------------------------------------


class TestHumanReviewPhase:
    @pytest.mark.asyncio
    async def test_no_human_review_pauses(self):
        state = _make_state(status=SystemStatus.AWAITING_HUMAN)
        state.merge_plan = _make_plan()

        ctx = _make_ctx()

        phase = HumanReviewPhase()
        with patch("src.core.phases.human_review.write_merge_plan_report"):
            outcome = await phase.execute(state, ctx)

        assert outcome.target_status == SystemStatus.AWAITING_HUMAN
        assert outcome.extra.get("paused") is True
        assert outcome.should_checkpoint

    @pytest.mark.asyncio
    async def test_approve_transitions_to_auto_merging(self):
        from src.models.plan_review import PlanHumanDecision, PlanHumanReview

        state = _make_state(status=SystemStatus.AWAITING_HUMAN)
        state.plan_human_review = PlanHumanReview(
            decision=PlanHumanDecision.APPROVE,
            reviewer="human",
        )

        ctx = _make_ctx()

        phase = HumanReviewPhase()
        with patch("src.core.phases.human_review.write_plan_review_report"):
            outcome = await phase.execute(state, ctx)

        assert outcome.target_status == SystemStatus.AUTO_MERGING
        ctx.state_machine.transition.assert_called_once_with(
            state, SystemStatus.AUTO_MERGING, "plan approved by human reviewer"
        )

    @pytest.mark.asyncio
    async def test_reject_transitions_to_failed(self):
        from src.models.plan_review import PlanHumanDecision, PlanHumanReview

        state = _make_state(status=SystemStatus.AWAITING_HUMAN)
        state.plan_human_review = PlanHumanReview(
            decision=PlanHumanDecision.REJECT,
            reviewer="human",
        )

        ctx = _make_ctx()

        phase = HumanReviewPhase()
        with patch("src.core.phases.human_review.write_plan_review_report"):
            outcome = await phase.execute(state, ctx)

        assert outcome.target_status == SystemStatus.FAILED

    @pytest.mark.asyncio
    async def test_halts_when_judge_verdict_pending_resolution(self):
        """O-L1 regression: after judge FAIL + dispute exhaustion the phase
        must stay in AWAITING_HUMAN instead of re-transitioning to
        JUDGE_REVIEWING via the stale conflict decisions from earlier phases.
        """
        from src.models.human import HumanDecisionRequest, DecisionOption
        from src.models.decision import MergeDecision as MD
        from src.models.judge import JudgeVerdict, VerdictType

        state = _make_state(status=SystemStatus.AWAITING_HUMAN)
        state.current_phase = MergePhase.JUDGE_REVIEW
        state.judge_verdict = JudgeVerdict(
            verdict=VerdictType.FAIL,
            reviewed_files_count=1,
            passed_files=[],
            failed_files=["a.py"],
            conditional_files=[],
            issues=[],
            critical_issues_count=1,
            high_issues_count=0,
            overall_confidence=0.9,
            summary="fail",
            blocking_issues=[],
            timestamp=datetime.now(),
            judge_model="test",
        )
        state.judge_resolution = None
        resolved_req = HumanDecisionRequest(
            file_path="a.py",
            priority=5,
            conflict_points=[],
            context_summary="",
            upstream_change_summary="",
            fork_change_summary="",
            analyst_recommendation=MD.TAKE_TARGET,
            analyst_confidence=0.8,
            analyst_rationale="",
            options=[
                DecisionOption(
                    option_key="take_target",
                    decision=MD.TAKE_TARGET,
                    description="take upstream",
                )
            ],
            created_at=datetime.now(),
            human_decision=MD.TAKE_TARGET,
        )
        state.human_decision_requests = {"a.py": resolved_req}

        ctx = _make_ctx()

        phase = HumanReviewPhase()
        outcome = await phase.execute(state, ctx)

        assert outcome.target_status == SystemStatus.AWAITING_HUMAN
        assert outcome.checkpoint_tag == "judge_resolution_required"
        assert outcome.extra.get("paused") is True
        ctx.state_machine.transition.assert_not_called()

    @pytest.mark.asyncio
    async def test_accept_judge_resolution_transitions_to_report(self):
        """Complement to O-L1: when judge_resolution=='accept' is present, the
        phase must transition to GENERATING_REPORT without re-looping through
        Case 1's conflict-decisions branch."""
        from src.models.human import HumanDecisionRequest, DecisionOption
        from src.models.decision import MergeDecision as MD
        from src.models.judge import JudgeVerdict, VerdictType

        state = _make_state(status=SystemStatus.AWAITING_HUMAN)
        state.current_phase = MergePhase.JUDGE_REVIEW
        state.judge_verdict = JudgeVerdict(
            verdict=VerdictType.FAIL,
            reviewed_files_count=1,
            passed_files=[],
            failed_files=["a.py"],
            conditional_files=[],
            issues=[],
            critical_issues_count=1,
            high_issues_count=0,
            overall_confidence=0.9,
            summary="fail",
            blocking_issues=[],
            timestamp=datetime.now(),
            judge_model="test",
        )
        state.judge_resolution = "accept"
        resolved_req = HumanDecisionRequest(
            file_path="a.py",
            priority=5,
            conflict_points=[],
            context_summary="",
            upstream_change_summary="",
            fork_change_summary="",
            analyst_recommendation=MD.TAKE_TARGET,
            analyst_confidence=0.8,
            analyst_rationale="",
            options=[
                DecisionOption(
                    option_key="take_target",
                    decision=MD.TAKE_TARGET,
                    description="take upstream",
                )
            ],
            created_at=datetime.now(),
            human_decision=MD.TAKE_TARGET,
        )
        state.human_decision_requests = {"a.py": resolved_req}

        ctx = _make_ctx()

        phase = HumanReviewPhase()
        outcome = await phase.execute(state, ctx)

        assert outcome.target_status == SystemStatus.GENERATING_REPORT
        assert outcome.checkpoint_tag == "judge_accepted"

    @pytest.mark.asyncio
    async def test_b_d_missing_catchup_after_resume(self, tmp_path):
        """O-B4-e2e-gap regression: when AUTO_MERGE is skipped on resume
        (state was already AWAITING_HUMAN with conflict decisions all
        resolved), B-class and D-missing text files in the merge_plan
        that have no file_decision_record must be caught up via
        TAKE_TARGET in the human_review phase. Without this, layer 1+
        files stay at fork content and Judge fails them as "differs from
        upstream after merge" / "not present in HEAD after merge".
        """
        from src.models.human import HumanDecisionRequest, DecisionOption
        from src.models.decision import (
            FileDecisionRecord,
            MergeDecision as MD,
            DecisionSource,
        )
        from src.models.diff import FileChangeCategory, FileStatus

        b_file = "models/foo/manifest.yaml"
        d_file = "models/bar/new.yaml"
        already_decided_file = "tools/baz/conflict.py"

        state = _make_state(status=SystemStatus.AWAITING_HUMAN)
        state.current_phase = MergePhase.AUTO_MERGE
        state.merge_base_commit = "deadbeef"
        state.file_categories = {
            b_file: FileChangeCategory.B,
            d_file: FileChangeCategory.D_MISSING,
            already_decided_file: FileChangeCategory.C,
        }
        state.merge_plan = _make_plan(
            phases=[
                PhaseFileBatch(
                    batch_id="b1",
                    phase=MergePhase.AUTO_MERGE,
                    file_paths=[b_file, d_file, already_decided_file],
                    risk_level="auto_safe",
                )
            ]
        )
        # Already-decided file should NOT be re-applied (skip via
        # file_decision_records guard).
        state.file_decision_records[already_decided_file] = FileDecisionRecord(
            file_path=already_decided_file,
            file_status=FileStatus.MODIFIED,
            decision=MD.TAKE_TARGET,
            decision_source=DecisionSource.AUTO_EXECUTOR,
            confidence=0.9,
            rationale="prior decision",
            phase="auto_merge",
            agent="executor",
            timestamp=datetime.now(),
        )
        decided_req = HumanDecisionRequest(
            file_path=already_decided_file,
            priority=5,
            conflict_points=[],
            context_summary="",
            upstream_change_summary="",
            fork_change_summary="",
            analyst_recommendation=MD.TAKE_TARGET,
            analyst_confidence=0.8,
            analyst_rationale="",
            options=[
                DecisionOption(
                    option_key="take_target",
                    decision=MD.TAKE_TARGET,
                    description="take upstream",
                )
            ],
            created_at=datetime.now(),
            human_decision=MD.TAKE_TARGET,
        )
        state.human_decision_requests = {already_decided_file: decided_req}

        # Mock git_tool to serve upstream content for catch-up files.
        from src.tools.patch_applier import _git_blob_sha

        git_tool = MagicMock()
        git_tool.repo_path = tmp_path
        git_tool.get_file_content.side_effect = lambda ref, fp: {
            b_file: "upstream-B-content\n",
            d_file: "upstream-D-content\n",
        }.get(fp)
        git_tool.get_unmerged_files.return_value = []
        git_tool.get_worktree_blob_sha.side_effect = lambda fp: _git_blob_sha(
            (tmp_path / fp).read_bytes()
        )

        executor = AsyncMock()
        executor.execute_human_decision = AsyncMock(
            return_value=FileDecisionRecord(
                file_path=already_decided_file,
                file_status=FileStatus.MODIFIED,
                decision=MD.TAKE_TARGET,
                decision_source=DecisionSource.AUTO_EXECUTOR,
                confidence=0.9,
                rationale="executed",
                phase="human_review",
                agent="executor",
                timestamp=datetime.now(),
            )
        )

        ctx = _make_ctx(git_tool=git_tool, agents={"executor": executor})
        # Disable post-phase commit so we only assert catch-up writes.
        ctx.config.history.enabled = False

        # Force is_binary_asset to False so all text files go through the
        # text catch-up path.
        with patch("src.tools.binary_assets.is_binary_asset", return_value=False):
            phase = HumanReviewPhase()
            outcome = await phase.execute(state, ctx)

        # Working tree must contain upstream content for both B and
        # D-missing files.
        b_path = tmp_path / b_file
        d_path = tmp_path / d_file
        assert b_path.exists(), "B-class file should be written to working tree"
        assert d_path.exists(), "D-missing file should be written to working tree"
        assert b_path.read_text() == "upstream-B-content\n"
        assert d_path.read_text() == "upstream-D-content\n"

        # file_decision_records must include catch-up entries.
        assert b_file in state.file_decision_records
        assert d_file in state.file_decision_records
        assert state.file_decision_records[b_file].agent == "b_d_text_catchup"
        assert state.file_decision_records[d_file].agent == "b_d_text_catchup"
        assert state.file_decision_records[b_file].decision == MD.TAKE_TARGET
        assert state.file_decision_records[d_file].decision == MD.TAKE_TARGET

        # Already-decided file must NOT be touched by catch-up.
        assert (
            state.file_decision_records[already_decided_file].agent
            != "b_d_text_catchup"
        )

        # Phase advances toward judge review.
        assert outcome.target_status == SystemStatus.JUDGE_REVIEWING


# ---------------------------------------------------------------------------
# ReportGenerationPhase
# ---------------------------------------------------------------------------


class TestReportGenerationPhase:
    @pytest.mark.asyncio
    async def test_report_success(self):
        state = _make_state(status=SystemStatus.GENERATING_REPORT)

        ctx = _make_ctx()

        phase = ReportGenerationPhase()
        with (
            patch("src.core.phases.report_generation.write_json_report"),
            patch("src.core.phases.report_generation.write_markdown_report"),
            patch("src.core.phases.report_generation.write_living_plan_report"),
        ):
            outcome = await phase.execute(state, ctx)

        assert outcome.target_status == SystemStatus.COMPLETED
        assert outcome.checkpoint_tag == "completed"
        ctx.state_machine.transition.assert_called_once_with(
            state, SystemStatus.COMPLETED, "reports generated"
        )

    @pytest.mark.asyncio
    async def test_report_failure_still_completes(self):
        state = _make_state(status=SystemStatus.GENERATING_REPORT)

        ctx = _make_ctx()

        phase = ReportGenerationPhase()
        with (
            patch(
                "src.core.phases.report_generation.write_json_report",
                side_effect=OSError("disk full"),
            ),
            patch("src.core.phases.report_generation.write_markdown_report"),
            patch("src.core.phases.report_generation.write_living_plan_report"),
        ):
            outcome = await phase.execute(state, ctx)

        assert outcome.target_status == SystemStatus.COMPLETED
        assert len(state.errors) == 1
        assert "disk full" in state.errors[0]["message"]


# ---------------------------------------------------------------------------
# _select_merge_strategy (conflict_analysis helper)
# ---------------------------------------------------------------------------


class TestSelectMergeStrategy:
    def _make_analysis(self, **overrides):
        from src.models.conflict import ConflictAnalysis, ConflictType

        defaults = {
            "file_path": "a.py",
            "conflict_points": [],
            "overall_confidence": 0.5,
            "conflict_type": ConflictType.CONCURRENT_MODIFICATION,
            "confidence": 0.5,
            "can_coexist": False,
            "is_security_sensitive": False,
            "recommended_strategy": MergeDecision.TAKE_TARGET,
            "rationale": "test",
        }
        defaults.update(overrides)
        return ConflictAnalysis(**defaults)

    def test_low_confidence_escalates(self):
        analysis = self._make_analysis(confidence=0.3)
        thresholds = ThresholdConfig()
        result = _select_merge_strategy(analysis, thresholds)
        assert result == MergeDecision.ESCALATE_HUMAN

    def test_security_sensitive_escalates(self):
        analysis = self._make_analysis(confidence=0.9, is_security_sensitive=True)
        thresholds = ThresholdConfig()
        result = _select_merge_strategy(analysis, thresholds)
        assert result == MergeDecision.ESCALATE_HUMAN

    def test_high_confidence_coexist_semantic_merge(self):
        analysis = self._make_analysis(confidence=0.95, can_coexist=True)
        thresholds = ThresholdConfig()
        result = _select_merge_strategy(analysis, thresholds)
        assert result == MergeDecision.SEMANTIC_MERGE


# ---------------------------------------------------------------------------
# Lifecycle: before → execute → after via run()
# ---------------------------------------------------------------------------


class TestPhaseLifecycleIntegration:
    @pytest.mark.asyncio
    async def test_planning_via_run(self):
        planner = AsyncMock()
        planner.run = AsyncMock()
        ctx = _make_ctx(agents={"planner": planner})
        state = _make_state(status=SystemStatus.PLANNING)

        phase = PlanningPhase()
        outcome = await phase.run(state, ctx)

        assert outcome.target_status == SystemStatus.PLAN_REVIEWING

    @pytest.mark.asyncio
    async def test_report_via_run(self):
        state = _make_state(status=SystemStatus.GENERATING_REPORT)
        ctx = _make_ctx()

        phase = ReportGenerationPhase()
        with (
            patch("src.core.phases.report_generation.write_json_report"),
            patch("src.core.phases.report_generation.write_markdown_report"),
            patch("src.core.phases.report_generation.write_living_plan_report"),
        ):
            outcome = await phase.run(state, ctx)

        assert outcome.target_status == SystemStatus.COMPLETED
