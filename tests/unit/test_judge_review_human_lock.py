"""Tests for the judge_review short-circuit when all repair targets are
HUMAN-decided.

Regression: when every issue the Judge raises lands on a file with
``decision_source=HUMAN``, ``executor.repair`` correctly refuses to
overwrite the operator (commit 3fc35f5). But the round loop kept running,
spending another full Judge LLM pass producing the same issues, then
falling into a meta-review that on rate-limit silently failed. Detect the
condition right after ``build_rebuttal`` and break out of the loop so the
phase falls into AWAITING_HUMAN immediately.
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
from src.models.decision import DecisionSource, FileDecisionRecord, MergeDecision
from src.models.diff import FileStatus
from src.models.judge import (
    ExecutorRebuttal,
    IssueSeverity,
    JudgeIssue,
    JudgeVerdict,
    RepairInstruction,
    VerdictType,
)
from src.models.state import MergeState, SystemStatus


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


def _make_fail_verdict(file_path: str) -> JudgeVerdict:
    return JudgeVerdict(
        verdict=VerdictType.FAIL,
        reviewed_files_count=1,
        passed_files=[],
        failed_files=[file_path],
        conditional_files=[],
        issues=[
            JudgeIssue(
                file_path=file_path,
                issue_level=IssueSeverity.CRITICAL,
                issue_type="missing_logic",
                description="upstream refactor missing",
                must_fix_before_merge=True,
                suggested_fix="apply upstream pattern",
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


def _human_record(file_path: str) -> FileDecisionRecord:
    return FileDecisionRecord(
        file_path=file_path,
        file_status=FileStatus.MODIFIED,
        decision=MergeDecision.SEMANTIC_MERGE,
        decision_source=DecisionSource.HUMAN,
        rationale="human picked this",
    )


@pytest.mark.asyncio
async def test_short_circuits_when_all_repair_targets_human_decided(tmp_path):
    config = MergeConfig(
        upstream_ref="upstream/main",
        fork_ref="feature/fork",
        output=MergeConfig.model_fields["output"].default_factory(),
    )
    config.output.directory = str(tmp_path)
    config.output.debug_directory = str(tmp_path / "debug")

    fp = "packages/zod/src/v4/classic/schemas.ts"
    fail_msg = MagicMock()
    fail_msg.payload = {"verdict": _make_fail_verdict(fp).model_dump(mode="json")}

    mock_judge = MagicMock()
    mock_judge.run = AsyncMock(return_value=fail_msg)
    mock_judge.verify_customizations = MagicMock(return_value=[])

    repair_instr = RepairInstruction(
        file_path=fp, instruction="apply upstream pattern", is_repairable=True
    )
    mock_executor = MagicMock()
    mock_executor.repair = AsyncMock(return_value=[])
    mock_executor.reset_circuit_breaker = MagicMock()
    mock_executor.build_rebuttal = AsyncMock(
        return_value=ExecutorRebuttal(
            accepts_all=True, repair_instructions=[repair_instr]
        )
    )

    ctx = _make_ctx(
        config,
        agents={"judge": mock_judge, "executor": mock_executor},
        coordinator=MagicMock(),
    )

    state = MergeState(config=config)
    state.status = SystemStatus.JUDGE_REVIEWING
    state.file_decision_records[fp] = _human_record(fp)

    phase = JudgeReviewPhase()
    await phase.execute(state, ctx)

    assert mock_judge.run.await_count == 1
    mock_executor.repair.assert_not_awaited()
    assert state.status == SystemStatus.AWAITING_HUMAN
    assert state.judge_verdict is not None
    assert state.judge_verdict.verdict == VerdictType.FAIL


@pytest.mark.asyncio
async def test_repairs_non_human_subset_when_mixed(tmp_path):
    config = MergeConfig(
        upstream_ref="upstream/main",
        fork_ref="feature/fork",
        output=MergeConfig.model_fields["output"].default_factory(),
    )
    config.output.directory = str(tmp_path)
    config.output.debug_directory = str(tmp_path / "debug")

    human_fp = "human_locked.ts"
    auto_fp = "auto_repairable.ts"

    fail_verdict = JudgeVerdict(
        verdict=VerdictType.FAIL,
        reviewed_files_count=2,
        passed_files=[],
        failed_files=[human_fp, auto_fp],
        conditional_files=[],
        issues=[
            JudgeIssue(
                file_path=human_fp,
                issue_level=IssueSeverity.CRITICAL,
                issue_type="missing_logic",
                description="x",
                must_fix_before_merge=True,
            ),
            JudgeIssue(
                file_path=auto_fp,
                issue_level=IssueSeverity.HIGH,
                issue_type="missing_logic",
                description="y",
                must_fix_before_merge=True,
            ),
        ],
        critical_issues_count=1,
        high_issues_count=1,
        overall_confidence=0.4,
        summary="fail",
        blocking_issues=[],
        timestamp=datetime.now(),
        judge_model="test",
    )
    pass_verdict = JudgeVerdict(
        verdict=VerdictType.PASS,
        reviewed_files_count=2,
        passed_files=[human_fp, auto_fp],
        failed_files=[],
        conditional_files=[],
        issues=[],
        critical_issues_count=0,
        high_issues_count=0,
        overall_confidence=0.95,
        summary="ok",
        blocking_issues=[],
        timestamp=datetime.now(),
        judge_model="test",
    )

    msg1, msg2 = MagicMock(), MagicMock()
    msg1.payload = {"verdict": fail_verdict.model_dump(mode="json")}
    msg2.payload = {"verdict": pass_verdict.model_dump(mode="json")}

    mock_judge = MagicMock()
    mock_judge.run = AsyncMock(side_effect=[msg1, msg2])
    mock_judge.verify_customizations = MagicMock(return_value=[])

    mock_executor = MagicMock()
    mock_executor.repair = AsyncMock(return_value=[])
    mock_executor.reset_circuit_breaker = MagicMock()
    mock_executor.build_rebuttal = AsyncMock(
        return_value=ExecutorRebuttal(
            accepts_all=True,
            repair_instructions=[
                RepairInstruction(
                    file_path=human_fp, instruction="a", is_repairable=True
                ),
                RepairInstruction(
                    file_path=auto_fp, instruction="b", is_repairable=True
                ),
            ],
        )
    )

    ctx = _make_ctx(
        config,
        agents={"judge": mock_judge, "executor": mock_executor},
        coordinator=MagicMock(),
    )

    state = MergeState(config=config)
    state.status = SystemStatus.JUDGE_REVIEWING
    state.file_decision_records[human_fp] = _human_record(human_fp)
    state.file_decision_records[auto_fp] = FileDecisionRecord(
        file_path=auto_fp,
        file_status=FileStatus.MODIFIED,
        decision=MergeDecision.TAKE_TARGET,
        decision_source=DecisionSource.AUTO_EXECUTOR,
        rationale="auto",
    )

    phase = JudgeReviewPhase()
    await phase.execute(state, ctx)

    mock_executor.repair.assert_awaited_once()
    repaired = mock_executor.repair.await_args.args[0]
    assert [r.file_path for r in repaired] == [auto_fp]


@pytest.mark.asyncio
async def test_empty_repairable_continues_to_next_round(tmp_path):
    """When the rebuttal has no repairable instructions at all, the existing
    behavior is to continue to the next Judge round (the short-circuit only
    fires when there ARE repair instructions but all are human-locked)."""
    config = MergeConfig(
        upstream_ref="upstream/main",
        fork_ref="feature/fork",
        output=MergeConfig.model_fields["output"].default_factory(),
    )
    config.output.directory = str(tmp_path)
    config.output.debug_directory = str(tmp_path / "debug")

    fp = "x.ts"
    fail_msg = MagicMock()
    fail_msg.payload = {"verdict": _make_fail_verdict(fp).model_dump(mode="json")}

    mock_judge = MagicMock()
    mock_judge.run = AsyncMock(return_value=fail_msg)
    mock_judge.verify_customizations = MagicMock(return_value=[])

    mock_executor = MagicMock()
    mock_executor.repair = AsyncMock(return_value=[])
    mock_executor.reset_circuit_breaker = MagicMock()
    mock_executor.build_rebuttal = AsyncMock(
        return_value=ExecutorRebuttal(accepts_all=True, repair_instructions=[])
    )

    ctx = _make_ctx(
        config,
        agents={"judge": mock_judge, "executor": mock_executor},
        coordinator=MagicMock(),
    )

    state = MergeState(config=config)
    state.status = SystemStatus.JUDGE_REVIEWING
    state.file_decision_records[fp] = _human_record(fp)

    phase = JudgeReviewPhase()
    await phase.execute(state, ctx)

    assert mock_judge.run.await_count >= 2
    mock_executor.repair.assert_not_awaited()
