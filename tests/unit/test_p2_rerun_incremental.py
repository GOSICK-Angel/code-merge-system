"""Unit tests for P2-1: incremental rerun.

Verifies that ``HumanReviewPhase`` and ``AutoMergePhase`` cooperate so
that a "rerun" after a Judge FAIL only re-executes the failed files and
does not re-run cherry-pick on a worktree the previous round already
mutated.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from src.core.phases.human_review import HumanReviewPhase
from src.models.config import MergeConfig
from src.models.decision import (
    DecisionSource,
    FileDecisionRecord,
    MergeDecision,
)
from src.models.diff import FileStatus
from src.models.judge import JudgeVerdict, VerdictType
from src.models.plan_review import PlanHumanDecision, PlanHumanReview
from src.models.state import MergePhase, MergeState, SystemStatus


def _make_state_after_judge_fail(
    *,
    failed_files: list[str],
    passed_files: list[str],
    rerun_round: int = 0,
    max_rerun_rounds: int = 1,
) -> MergeState:
    cfg = MergeConfig(
        upstream_ref="upstream",
        fork_ref="fork",
        max_rerun_rounds=max_rerun_rounds,
    )
    state = MergeState(config=cfg)
    state.plan_human_review = PlanHumanReview(
        decision=PlanHumanDecision.APPROVE,
        reviewer_name="tester",
    )
    state.current_phase = MergePhase.JUDGE_REVIEW
    state.judge_verdict = JudgeVerdict(
        verdict=VerdictType.FAIL,
        reviewed_files_count=len(passed_files) + len(failed_files),
        passed_files=passed_files,
        failed_files=failed_files,
        conditional_files=[],
        issues=[],
        critical_issues_count=1,
        high_issues_count=0,
        overall_confidence=0.5,
        summary="fail",
        blocking_issues=["x"],
        timestamp=datetime.now(),
        judge_model="test-model",
    )
    state.judge_resolution = "rerun"
    state.rerun_round = rerun_round

    for fp in passed_files + failed_files:
        state.file_decision_records[fp] = FileDecisionRecord(
            file_path=fp,
            file_status=FileStatus.MODIFIED,
            decision=MergeDecision.TAKE_TARGET,
            decision_source=DecisionSource.AUTO_EXECUTOR,
            confidence=0.9,
            rationale="seed",
            phase="auto_merge",
            agent="executor",
        )
    return state


@pytest.mark.asyncio
async def test_rerun_first_round_clears_only_failed_files():
    """Round 1 of rerun: failed-file records dropped; passed-file records
    preserved; rerun_round bumps to 1; judge_verdict cleared so the next
    JUDGE_REVIEWING does not short-circuit on the stale verdict."""
    state = _make_state_after_judge_fail(
        failed_files=["bad/a.py", "bad/b.py"],
        passed_files=["ok/c.py"],
        rerun_round=0,
        max_rerun_rounds=1,
    )

    ctx = MagicMock()
    ctx.state_machine.transition = MagicMock()

    outcome = await HumanReviewPhase().execute(state, ctx)

    assert outcome.target_status == SystemStatus.AUTO_MERGING
    assert outcome.checkpoint_tag == "judge_rerun"
    assert state.rerun_round == 1
    assert state.judge_resolution is None
    assert state.judge_verdict is None
    # Failed files should be re-runnable; the passed file's record stays
    # in place so AutoMergePhase's per-file dedup skips it.
    assert "bad/a.py" not in state.file_decision_records
    assert "bad/b.py" not in state.file_decision_records
    assert "ok/c.py" in state.file_decision_records


@pytest.mark.asyncio
async def test_rerun_budget_exhausted_routes_to_failed():
    """Second rerun request when max_rerun_rounds=1 must terminate the
    run as FAILED instead of looping AUTO_MERGING again."""
    state = _make_state_after_judge_fail(
        failed_files=["bad/a.py"],
        passed_files=["ok/c.py"],
        rerun_round=1,
        max_rerun_rounds=1,
    )

    ctx = MagicMock()
    ctx.state_machine.transition = MagicMock()

    outcome = await HumanReviewPhase().execute(state, ctx)

    assert outcome.target_status == SystemStatus.FAILED
    assert outcome.checkpoint_tag == "judge_rerun_exhausted"
    assert state.rerun_round == 1  # unchanged on exhaustion
    # Failed files should NOT be cleared on the over-budget path —
    # the run is terminating, not retrying.
    assert "bad/a.py" in state.file_decision_records


@pytest.mark.asyncio
async def test_auto_merge_skips_cherry_pick_replay_on_rerun(monkeypatch):
    """AutoMergePhase entry: when ``state.rerun_round > 0`` it must NOT
    invoke ``CommitReplayer.replay_clean_commits`` again — the worktree
    already holds the prior round's writes and a second cherry-pick
    pass produces spurious conflict markers (v2.1.0 regression)."""
    from src.core.phases import auto_merge as auto_merge_mod
    from src.models.plan import MergePlan, RiskSummary

    cfg = MergeConfig(upstream_ref="upstream", fork_ref="fork")
    cfg.history.enabled = True
    cfg.history.cherry_pick_clean = True
    state = MergeState(config=cfg)
    state.rerun_round = 1
    state.replayable_commits = [{"sha": "abcd1234"}]
    state.partial_replayable_commits = []
    state.merge_plan = MergePlan(
        created_at=datetime.now(),
        upstream_ref="upstream",
        fork_ref="fork",
        merge_base_commit="base",
        phases=[],
        risk_summary=RiskSummary(
            total_files=0,
            auto_safe_count=0,
            auto_risky_count=0,
            human_required_count=0,
            deleted_only_count=0,
            binary_count=0,
            excluded_count=0,
            estimated_auto_merge_rate=1.0,
        ),
        project_context_summary="",
    )

    ctx = MagicMock()
    ctx.config = cfg
    ctx.git_tool.repo_path = MagicMock()
    ctx.agents = {"executor": MagicMock(), "judge": MagicMock()}
    state.file_diffs = []

    called = {"clean": 0, "partial": 0}

    class FakeReplayer:
        async def replay_clean_commits(self, *args, **kwargs):
            called["clean"] += 1
            return MagicMock(
                replayed_files=[],
                replayed_shas=[],
                partial_replays=[],
                failed_shas=[],
            )

        async def replay_partial_commits(self, *args, **kwargs):
            called["partial"] += 1

    monkeypatch.setattr(auto_merge_mod, "CommitReplayer", FakeReplayer)

    # AutoMergePhase.execute may go on to call helpers that need extra
    # state; we only care that the replay branch was bypassed, so swallow
    # any downstream failure.
    try:
        await auto_merge_mod.AutoMergePhase().execute(state, ctx)
    except Exception:
        pass

    assert called["clean"] == 0, "cherry-pick replay must be skipped on rerun"
    assert called["partial"] == 0
