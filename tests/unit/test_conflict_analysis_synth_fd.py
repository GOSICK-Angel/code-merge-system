"""Tests for the A-fix (synthesized FileDiff for pending_conflict_files)
and C-fix (deadlock guard in human_review._unanalyzed_conflict_files).

Regression: files surfaced by auto_merge (B-class drift / fork preservation
losses) sit in state.pending_conflict_files without a state.file_diffs
entry. Conflict_analysis used to skip them at the strategy execution
step (fd is None), which left them undecided and caused
AWAITING_HUMAN ⇄ ANALYZING_CONFLICTS ping-pong.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.phases.base import PhaseContext
from src.core.phases.conflict_analysis import (
    ConflictAnalysisPhase,
    _synthesize_minimal_filediff,
)
from src.core.phases.human_review import _unanalyzed_conflict_files
from src.core.checkpoint import Checkpoint
from src.core.state_machine import StateMachine
from src.memory.store import MemoryStore
from src.memory.summarizer import PhaseSummarizer
from src.models.conflict import ConflictAnalysis, ConflictType
from src.models.config import MergeConfig, OutputConfig
from src.models.decision import (
    DecisionSource,
    FileDecisionRecord,
    MergeDecision,
)
from src.models.diff import FileDiff, FileStatus, RiskLevel
from src.models.state import MergeState, SystemStatus


def _make_config(tmp_path) -> MergeConfig:
    return MergeConfig(
        upstream_ref="upstream/main",
        fork_ref="feature/fork",
        output=OutputConfig(directory=str(tmp_path)),
    )


def _make_state(config: MergeConfig) -> MergeState:
    state = MergeState(config=config)
    state.merge_base_commit = "abc123"
    return state


def _make_analysis(
    file_path: str,
    strategy: MergeDecision = MergeDecision.TAKE_TARGET,
    confidence: float = 0.95,
    conflict_type: ConflictType = ConflictType.SEMANTIC_EQUIVALENT,
    can_coexist: bool = False,
) -> ConflictAnalysis:
    return ConflictAnalysis(
        file_path=file_path,
        conflict_points=[],
        overall_confidence=confidence,
        recommended_strategy=strategy,
        conflict_type=conflict_type,
        can_coexist=can_coexist,
        confidence=confidence,
    )


def _make_ctx(config, **overrides) -> PhaseContext:
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


class TestSynthesizeMinimalFileDiff:
    def test_returns_modified_when_both_refs_have_file(self):
        git_tool = MagicMock()
        git_tool.get_file_content.return_value = "content"
        fd = _synthesize_minimal_filediff(
            "x.py", git_tool, "upstream/main", "feature/fork"
        )
        assert fd.file_path == "x.py"
        assert fd.file_status == FileStatus.MODIFIED
        assert fd.risk_level == RiskLevel.AUTO_RISKY
        assert "synthesized_from_pending_conflict_files" in fd.risk_factors

    def test_added_when_only_upstream_has_file(self):
        git_tool = MagicMock()
        git_tool.get_file_content.side_effect = lambda ref, _: (
            "content" if ref == "upstream/main" else None
        )
        fd = _synthesize_minimal_filediff(
            "x.py", git_tool, "upstream/main", "feature/fork"
        )
        assert fd.file_status == FileStatus.ADDED

    def test_deleted_when_only_fork_has_file(self):
        git_tool = MagicMock()
        git_tool.get_file_content.side_effect = lambda ref, _: (
            "content" if ref == "feature/fork" else None
        )
        fd = _synthesize_minimal_filediff(
            "x.py", git_tool, "upstream/main", "feature/fork"
        )
        assert fd.file_status == FileStatus.DELETED

    def test_falls_back_to_modified_when_git_tool_raises(self):
        git_tool = MagicMock()
        git_tool.get_file_content.side_effect = RuntimeError("boom")
        fd = _synthesize_minimal_filediff(
            "x.py", git_tool, "upstream/main", "feature/fork"
        )
        assert fd.file_status == FileStatus.MODIFIED

    def test_no_git_tool_defaults_to_modified(self):
        fd = _synthesize_minimal_filediff("x.py", None, "upstream/main", "feature/fork")
        assert fd.file_status == FileStatus.MODIFIED


class TestConflictAnalysisSynthesizesFd:
    @pytest.mark.asyncio
    async def test_pending_file_without_filediff_gets_decision_record(self, tmp_path):
        """File in pending_conflict_files + conflict_analyses but not in
        file_diffs must still produce a file_decision_record — previously
        it was silently skipped, causing the AWAITING_HUMAN ⇄ ANALYZING
        deadlock."""
        config = _make_config(tmp_path)
        state = _make_state(config)
        state.status = SystemStatus.ANALYZING_CONFLICTS
        state.pending_conflict_files = ["surfaced/by/auto_merge.py"]
        state.conflict_analyses["surfaced/by/auto_merge.py"] = _make_analysis(
            "surfaced/by/auto_merge.py",
            strategy=MergeDecision.TAKE_TARGET,
            confidence=0.95,
        )

        executor = MagicMock()
        executor.execute_auto_merge = AsyncMock(
            return_value=FileDecisionRecord(
                file_path="surfaced/by/auto_merge.py",
                file_status=FileStatus.MODIFIED,
                decision=MergeDecision.TAKE_TARGET,
                decision_source=DecisionSource.AUTO_EXECUTOR,
                rationale="ok",
            )
        )
        analyst = MagicMock()
        analyst.consecutive_failures = 0
        analyst.run = AsyncMock()

        ctx = _make_ctx(
            config,
            agents={"conflict_analyst": analyst, "executor": executor},
        )

        phase = ConflictAnalysisPhase()
        await phase.execute(state, ctx)

        assert "surfaced/by/auto_merge.py" in state.file_decision_records
        assert state.status == SystemStatus.JUDGE_REVIEWING
        # executor was called with a synthesized FileDiff
        assert executor.execute_auto_merge.await_count == 1
        call_args = executor.execute_auto_merge.await_args.args
        called_fd = call_args[0]
        assert called_fd.file_path == "surfaced/by/auto_merge.py"
        assert "synthesized_from_pending_conflict_files" in called_fd.risk_factors


class TestUnanalyzedDeadlockGuard:
    def test_stuck_files_get_auto_escalated(self, tmp_path):
        """C-fix: files in conflict_analyses but missing from
        file_decision_records are auto-marked ESCALATE_HUMAN so the loop
        breaks."""
        config = _make_config(tmp_path)
        state = _make_state(config)
        state.pending_conflict_files = ["a.py", "b.py", "c.py"]
        state.conflict_analyses["a.py"] = _make_analysis("a.py")
        state.conflict_analyses["b.py"] = _make_analysis("b.py")
        # c.py has no analysis → genuinely still pending
        # a.py + b.py have analyses but no decision records → "stuck"

        pending = _unanalyzed_conflict_files(state)

        assert "a.py" not in pending
        assert "b.py" not in pending
        assert "c.py" in pending
        assert (
            state.file_decision_records["a.py"].decision == MergeDecision.ESCALATE_HUMAN
        )
        assert (
            state.file_decision_records["b.py"].decision == MergeDecision.ESCALATE_HUMAN
        )
        assert (
            state.file_decision_records["a.py"].decision_source
            == DecisionSource.AUTO_EXECUTOR
        )
        assert "C-fix" in state.file_decision_records["a.py"].rationale

    def test_no_stuck_files_returns_pending_as_usual(self, tmp_path):
        """When no files are 'analyzed but undecided', the function returns
        the regular pending list and does not mutate file_decision_records."""
        config = _make_config(tmp_path)
        state = _make_state(config)
        state.pending_conflict_files = ["a.py"]
        # no conflict_analyses, no file_decision_records

        pending = _unanalyzed_conflict_files(state)

        assert pending == ["a.py"]
        assert state.file_decision_records == {}

    def test_decided_files_are_excluded(self, tmp_path):
        """Files with existing decision records are not re-routed."""
        config = _make_config(tmp_path)
        state = _make_state(config)
        state.pending_conflict_files = ["a.py", "b.py"]
        state.conflict_analyses["a.py"] = _make_analysis("a.py")
        state.file_decision_records["a.py"] = FileDecisionRecord(
            file_path="a.py",
            file_status=FileStatus.MODIFIED,
            decision=MergeDecision.TAKE_TARGET,
            decision_source=DecisionSource.AUTO_EXECUTOR,
            rationale="already decided",
        )

        pending = _unanalyzed_conflict_files(state)

        assert pending == ["b.py"]
