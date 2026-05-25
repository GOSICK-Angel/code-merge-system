"""Regression tests for two defects found in run 957ed474.

Bug A — semantic merge fidelity guard: the LLM hallucinated inside a base64
certificate literal, injecting a fullwidth comma (U+FF0C) absent from both
sources and breaking the Go string literal. ``_foreign_chars`` flags non-ASCII
glyphs the merge invented so the executor escalates instead of committing
corruption.

Bug B — human override must beat a stale auto record: a file auto-merged in a
rerun's auto_merge phase (SEMANTIC_MERGE / AUTO_EXECUTOR) and then escalated to
the operator had its human take_target decision silently dropped, because
``HumanReviewPhase`` skipped any file already present in
``file_decision_records``. The override must now execute and overwrite the
auto record.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.executor_agent import _foreign_chars
from src.core.phases.human_review import HumanReviewPhase
from src.models.config import MergeConfig
from src.models.decision import DecisionSource, FileDecisionRecord, MergeDecision
from src.models.diff import FileStatus
from src.models.human import HumanDecisionRequest
from src.models.plan_review import PlanHumanDecision, PlanHumanReview
from src.models.state import MergePhase, MergeState, SystemStatus


# --------------------------------------------------------------------------- #
# Bug A — merge fidelity guard
# --------------------------------------------------------------------------- #


def test_foreign_chars_allows_pure_ascii_recombination() -> None:
    fork = "func Foo() {}\n"
    upstream = "func Bar() {}\n"
    merged = "func Foo() {}\nfunc Bar() {}\n"
    assert _foreign_chars(merged, fork, upstream) is None


def test_foreign_chars_flags_injected_fullwidth_comma() -> None:
    # The real corruption: a base64 blob where the LLM injected U+FF0C.
    fork = 'cert := "AQAB/hiPGhXJ"\n'
    upstream = 'cert := "AQAB/hiPGhXJ"\n'
    merged = 'cert := "AQAB-kiPGhXJ"，\n'  # note the fullwidth comma
    sample = _foreign_chars(merged, fork, upstream)
    assert sample is not None
    assert "，" in sample


def test_foreign_chars_allows_non_ascii_present_in_a_source() -> None:
    # CJK comment already in upstream → its glyphs are in the union → allowed.
    fork = "// English comment\nx := 1\n"
    upstream = "// 中文注释\nx := 1\n"
    merged = "// 中文注释\nx := 1\n"
    assert _foreign_chars(merged, fork, upstream) is None


# --------------------------------------------------------------------------- #
# Bug B — human override beats a stale auto record
# --------------------------------------------------------------------------- #


def _human_req(file_path: str, decision: MergeDecision) -> HumanDecisionRequest:
    return HumanDecisionRequest(
        file_path=file_path,
        priority=5,
        conflict_points=[],
        context_summary="",
        upstream_change_summary="",
        fork_change_summary="",
        analyst_recommendation=MergeDecision.ESCALATE_HUMAN,
        analyst_confidence=0.0,
        analyst_rationale="",
        options=[],
        created_at=datetime.now(),
        human_decision=decision,
    )


def _auto_record(file_path: str) -> FileDecisionRecord:
    return FileDecisionRecord(
        file_path=file_path,
        file_status=FileStatus.MODIFIED,
        decision=MergeDecision.SEMANTIC_MERGE,
        decision_source=DecisionSource.AUTO_EXECUTOR,
        confidence=0.65,
        rationale="rerun auto_merge",
        phase="auto_merge",
        agent="executor",
    )


def _human_record(file_path: str) -> FileDecisionRecord:
    return FileDecisionRecord(
        file_path=file_path,
        file_status=FileStatus.MODIFIED,
        decision=MergeDecision.TAKE_TARGET,
        decision_source=DecisionSource.HUMAN,
        confidence=1.0,
        rationale="operator override",
        phase="human_review",
        agent="executor",
    )


@pytest.mark.asyncio
async def test_human_override_executes_over_stale_auto_record() -> None:
    state = MergeState(config=MergeConfig(upstream_ref="upstream", fork_ref="fork"))
    state.plan_human_review = PlanHumanReview(
        decision=PlanHumanDecision.APPROVE, reviewer_name="tester"
    )
    state.current_phase = MergePhase.HUMAN_REVIEW
    state.judge_resolution = None
    state.merge_plan = None

    # "auto.go" was auto-merged in the rerun (stale AUTO_EXECUTOR record), then
    # escalated; the operator picked take_target. "already.go" is already
    # resolved by a HUMAN record and must NOT be re-executed.
    state.human_decision_requests = {
        "auto.go": _human_req("auto.go", MergeDecision.TAKE_TARGET),
        "already.go": _human_req("already.go", MergeDecision.TAKE_TARGET),
    }
    state.file_decision_records = {
        "auto.go": _auto_record("auto.go"),
        "already.go": _human_record("already.go"),
    }

    exec_mock = MagicMock()
    exec_mock.execute_human_decision = AsyncMock(
        side_effect=lambda req, st: _human_record(req.file_path)
    )

    ctx = MagicMock()
    ctx.config.history.enabled = False
    ctx.agents = {"executor": exec_mock}
    ctx.state_machine.transition = MagicMock()

    outcome = await HumanReviewPhase().execute(state, ctx)

    # The stale-auto file must have been re-executed; the already-human file
    # must have been skipped.
    called_paths = [
        c.args[0].file_path for c in exec_mock.execute_human_decision.call_args_list
    ]
    assert called_paths == ["auto.go"]
    assert (
        state.file_decision_records["auto.go"].decision_source == DecisionSource.HUMAN
    )
    assert state.file_decision_records["auto.go"].decision == MergeDecision.TAKE_TARGET
    assert outcome.target_status == SystemStatus.JUDGE_REVIEWING
