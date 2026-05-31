"""Tests for the meta-review degraded path (P2).

Regression: when ``judge.meta_review`` raised (e.g. mimo 429 rate limit),
``_run_judge_meta_review`` swallowed the exception with a warning and the
caller still set the AWAITING_HUMAN reason to "escalated to meta-review",
leaving operators staring at an empty ``coordinator_directives`` while
the system claimed it had run a meta-review.

The fix:
1. ``_run_judge_meta_review`` returns ``True`` on success, ``False`` on
   degraded (exception caught).
2. On degraded path, a sentinel MetaReviewResult is appended to
   ``state.coordinator_directives`` so the operator sees the failure
   mode rather than nothing.
3. The outer caller picks an honest AWAITING_HUMAN reason based on the
   bool, with the exception summary truncated to ≤120 chars.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.phases.base import PhaseContext
from src.core.phases.judge_review import JudgeReviewPhase
from src.core.state_machine import StateMachine
from src.memory.store import MemoryStore
from src.memory.summarizer import PhaseSummarizer
from src.models.config import MergeConfig
from src.models.judge import (
    IssueSeverity,
    JudgeIssue,
    JudgeVerdict,
    VerdictType,
)
from src.models.state import MergeState


def _make_ctx(config, **overrides):
    defaults = dict(
        config=config,
        git_tool=MagicMock(),
        gate_runner=MagicMock(),
        state_machine=StateMachine(),
        checkpoint=MagicMock(),
        memory_store=MemoryStore(),
        summarizer=PhaseSummarizer(),
        trace_logger=None,
        emit=None,
        agents={},
    )
    defaults.update(overrides)
    return PhaseContext(**defaults)


def _make_state(config) -> MergeState:
    state = MergeState(config=config)
    state.judge_verdict = JudgeVerdict(
        verdict=VerdictType.FAIL,
        reviewed_files_count=1,
        passed_files=[],
        failed_files=["x.py"],
        conditional_files=[],
        issues=[
            JudgeIssue(
                file_path="x.py",
                issue_level=IssueSeverity.CRITICAL,
                issue_type="missing_logic",
                description="d",
                must_fix_before_merge=True,
            )
        ],
        critical_issues_count=1,
        high_issues_count=0,
        overall_confidence=0.4,
        summary="fail",
        blocking_issues=[],
        timestamp=datetime.now(),
        judge_model="test",
    )
    return state


class TestMetaReviewReturnValue:
    @pytest.mark.asyncio
    async def test_returns_false_when_meta_review_raises(self, tmp_path):
        config = MergeConfig(
            upstream_ref="upstream/main",
            fork_ref="feature/fork",
            output=MergeConfig.model_fields["output"].default_factory(),
        )
        config.output.directory = str(tmp_path)
        config.output.debug_directory = str(tmp_path / "debug")

        mock_judge = MagicMock()
        mock_judge.meta_review = AsyncMock(
            side_effect=RuntimeError("mimo 429 router_queue_limitation")
        )
        ctx = _make_ctx(config, agents={"judge": mock_judge})

        state = _make_state(config)
        ok = await JudgeReviewPhase()._run_judge_meta_review(
            state, ctx, "judge stalled after 2 rounds"
        )
        assert ok is False

    @pytest.mark.asyncio
    async def test_returns_true_on_success(self, tmp_path):
        from src.core.coordinator import Coordinator

        config = MergeConfig(
            upstream_ref="upstream/main",
            fork_ref="feature/fork",
            output=MergeConfig.model_fields["output"].default_factory(),
        )
        config.output.directory = str(tmp_path)
        config.output.debug_directory = str(tmp_path / "debug")

        mock_judge = MagicMock()
        mock_judge.meta_review = AsyncMock(
            return_value={"assessment": "root cause X", "recommendation": "do Y"}
        )
        ctx = _make_ctx(
            config, agents={"judge": mock_judge}, coordinator=Coordinator(config)
        )

        state = _make_state(config)
        ok = await JudgeReviewPhase()._run_judge_meta_review(
            state, ctx, "judge stalled after 2 rounds"
        )
        assert ok is True
        assert len(state.coordinator_directives) == 1
        assert state.coordinator_directives[0].assessment == "root cause X"

    @pytest.mark.asyncio
    async def test_failure_appends_degraded_sentinel(self, tmp_path):
        config = MergeConfig(
            upstream_ref="upstream/main",
            fork_ref="feature/fork",
            output=MergeConfig.model_fields["output"].default_factory(),
        )
        config.output.directory = str(tmp_path)
        config.output.debug_directory = str(tmp_path / "debug")

        mock_judge = MagicMock()
        mock_judge.meta_review = AsyncMock(
            side_effect=RuntimeError("mimo 429 router_queue_limitation")
        )
        ctx = _make_ctx(config, agents={"judge": mock_judge})

        state = _make_state(config)
        await JudgeReviewPhase()._run_judge_meta_review(
            state, ctx, "judge stalled after 2 rounds"
        )

        assert len(state.coordinator_directives) == 1
        sentinel = state.coordinator_directives[0]
        assert "meta-review failed" in sentinel.assessment.lower()
        assert "mimo 429 router_queue_limitation" in sentinel.assessment
        assert sentinel.trigger == "judge_stall"
        assert sentinel.recommendation  # non-empty operator hint

    @pytest.mark.asyncio
    async def test_failure_exception_summary_truncated(self, tmp_path):
        config = MergeConfig(
            upstream_ref="upstream/main",
            fork_ref="feature/fork",
            output=MergeConfig.model_fields["output"].default_factory(),
        )
        config.output.directory = str(tmp_path)
        config.output.debug_directory = str(tmp_path / "debug")

        huge = "x" * 3000  # simulate a 3 KB 429 body
        mock_judge = MagicMock()
        mock_judge.meta_review = AsyncMock(side_effect=RuntimeError(huge))
        ctx = _make_ctx(config, agents={"judge": mock_judge})

        state = _make_state(config)
        await JudgeReviewPhase()._run_judge_meta_review(
            state, ctx, "judge stalled after 2 rounds"
        )

        sentinel = state.coordinator_directives[0]
        # MetaReviewResult.assessment cap is 200 chars; the embedded
        # exception payload must be truncated well below that so the
        # "meta-review failed: " prefix still fits.
        assert len(sentinel.assessment) <= 200
        # raw exception body should NOT be embedded verbatim
        assert huge not in sentinel.assessment


class TestOuterPhaseHonestReason:
    @pytest.mark.asyncio
    async def test_phase_reason_says_meta_review_unavailable_on_failure(self, tmp_path):
        """When meta-review raises, the AWAITING_HUMAN PhaseOutcome reason
        must NOT claim 'escalated to meta-review' — it should say the
        meta-review was unavailable so operators are not misled."""
        from src.core.coordinator import Coordinator
        from src.models.judge import ExecutorRebuttal, RepairInstruction
        from src.models.state import SystemStatus

        config = MergeConfig(
            upstream_ref="upstream/main",
            fork_ref="feature/fork",
            output=MergeConfig.model_fields["output"].default_factory(),
        )
        config.output.directory = str(tmp_path)
        config.output.debug_directory = str(tmp_path / "debug")

        fp = "x.py"
        fail_verdict = JudgeVerdict(
            verdict=VerdictType.FAIL,
            reviewed_files_count=1,
            passed_files=[],
            failed_files=[fp],
            conditional_files=[],
            issues=[
                JudgeIssue(
                    file_path=fp,
                    issue_level=IssueSeverity.CRITICAL,
                    issue_type="missing_logic",
                    description="d",
                    must_fix_before_merge=True,
                )
            ],
            critical_issues_count=1,
            high_issues_count=0,
            overall_confidence=0.4,
            summary="fail",
            blocking_issues=[],
            timestamp=datetime.now(),
            judge_model="test",
        )
        msg = MagicMock()
        msg.payload = {"verdict": fail_verdict.model_dump(mode="json")}

        mock_judge = MagicMock()
        mock_judge.run = AsyncMock(return_value=msg)
        mock_judge.verify_customizations = MagicMock(return_value=[])
        mock_judge.meta_review = AsyncMock(
            side_effect=RuntimeError("mimo 429 router_queue_limitation")
        )

        # rebuttal disputes the issue so we DON'T hit the HUMAN-locked
        # short-circuit path; we want to reach the coordinator stall route.
        mock_executor = MagicMock()
        mock_executor.reset_circuit_breaker = MagicMock()
        mock_executor.build_rebuttal = AsyncMock(
            return_value=ExecutorRebuttal(
                accepts_all=False,
                repair_instructions=[
                    RepairInstruction(file_path=fp, instruction="x", is_repairable=True)
                ],
            )
        )

        # Judge re_evaluate also returns FAIL so we proceed to stall path.
        from src.models.judge import BatchVerdict

        mock_judge.re_evaluate = AsyncMock(
            return_value=BatchVerdict(
                layer_id=None,
                approved=False,
                issues=fail_verdict.issues,
                repair_instructions=[],
                reviewed_files=[fp],
                round_num=0,
            )
        )

        ctx = _make_ctx(
            config,
            agents={"judge": mock_judge, "executor": mock_executor},
            coordinator=Coordinator(config),
        )

        state = MergeState(config=config)
        state.status = SystemStatus.JUDGE_REVIEWING

        phase = JudgeReviewPhase()
        outcome = await phase.execute(state, ctx)

        assert outcome.target_status == SystemStatus.AWAITING_HUMAN
        assert "escalated to meta-review" not in outcome.reason
        assert "meta-review unavailable" in outcome.reason
        # sentinel is in coordinator_directives
        assert len(state.coordinator_directives) == 1
        assert (
            "meta-review failed" in state.coordinator_directives[0].assessment.lower()
        )
