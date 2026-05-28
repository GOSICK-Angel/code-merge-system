"""Unit tests for AutoMergePhase._filter_phantom_files.

Regression: a non-replayable upstream commit may name a file that was added
then deleted within the same upstream window and the fork never adopted it.
Routing such a path to conflict_analysis synthesizes a two-sided-None
FileDiff; the analyst hallucinates "no changes / take_target", executor
escalates with "Could not fetch target content", and the file ends up
stuck at the human gate.

The filter removes these paths upfront and emits a SKIP decision so the
gate stays clean.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.core.phases.auto_merge import AutoMergePhase
from src.models.config import MergeConfig, OutputConfig
from src.models.decision import DecisionSource, MergeDecision
from src.models.diff import FileStatus
from src.models.state import MergeState


def _make_state(tmp_path) -> MergeState:
    config = MergeConfig(
        upstream_ref="test/upstream",
        fork_ref="test/fork",
        output=OutputConfig(directory=str(tmp_path)),
    )
    return MergeState(config=config)


def _make_ctx(exists_map: dict[tuple[str, str], bool]):
    git_tool = MagicMock()

    def _exists(ref: str, fp: str) -> bool:
        return exists_map.get((ref, fp), False)

    git_tool.file_exists_at_ref.side_effect = _exists
    ctx = MagicMock()
    ctx.git_tool = git_tool
    return ctx


class TestFilterPhantomFiles:
    def test_double_missing_path_gets_skipped(self, tmp_path):
        state = _make_state(tmp_path)
        ctx = _make_ctx(exists_map={})  # neither ref has the file

        kept = AutoMergePhase()._filter_phantom_files(
            ["packages/phantom/test.ts"], state, ctx
        )

        assert kept == []
        record = state.file_decision_records["packages/phantom/test.ts"]
        assert record.decision == MergeDecision.SKIP
        assert record.decision_source == DecisionSource.AUTO_EXECUTOR
        assert record.file_status == FileStatus.DELETED
        assert record.confidence == 1.0
        assert record.agent == "phantom_filter"
        assert record.phase == "auto_merge"
        assert "no-op" in record.rationale

    def test_only_upstream_has_file_is_kept(self, tmp_path):
        state = _make_state(tmp_path)
        ctx = _make_ctx(exists_map={("test/upstream", "real.py"): True})

        kept = AutoMergePhase()._filter_phantom_files(["real.py"], state, ctx)

        assert kept == ["real.py"]
        assert "real.py" not in state.file_decision_records

    def test_only_fork_has_file_is_kept(self, tmp_path):
        state = _make_state(tmp_path)
        ctx = _make_ctx(exists_map={("test/fork", "fork_only.py"): True})

        kept = AutoMergePhase()._filter_phantom_files(["fork_only.py"], state, ctx)

        assert kept == ["fork_only.py"]
        assert "fork_only.py" not in state.file_decision_records

    def test_both_sides_have_file_is_kept(self, tmp_path):
        state = _make_state(tmp_path)
        ctx = _make_ctx(
            exists_map={
                ("test/upstream", "shared.py"): True,
                ("test/fork", "shared.py"): True,
            }
        )

        kept = AutoMergePhase()._filter_phantom_files(["shared.py"], state, ctx)

        assert kept == ["shared.py"]
        assert "shared.py" not in state.file_decision_records

    def test_mixed_batch_only_phantoms_dropped(self, tmp_path):
        state = _make_state(tmp_path)
        ctx = _make_ctx(
            exists_map={
                ("test/upstream", "real_upstream.py"): True,
                ("test/fork", "real_fork.py"): True,
                ("test/upstream", "both.py"): True,
                ("test/fork", "both.py"): True,
            }
        )

        kept = AutoMergePhase()._filter_phantom_files(
            [
                "real_upstream.py",
                "phantom1.py",
                "real_fork.py",
                "phantom2.py",
                "both.py",
            ],
            state,
            ctx,
        )

        assert kept == ["real_upstream.py", "real_fork.py", "both.py"]
        assert set(state.file_decision_records.keys()) == {"phantom1.py", "phantom2.py"}
        for fp in ("phantom1.py", "phantom2.py"):
            assert state.file_decision_records[fp].decision == MergeDecision.SKIP

    def test_no_git_tool_passes_through_unchanged(self, tmp_path):
        state = _make_state(tmp_path)
        ctx = MagicMock()
        ctx.git_tool = None

        kept = AutoMergePhase()._filter_phantom_files(
            ["whatever.py", "other.py"], state, ctx
        )

        assert kept == ["whatever.py", "other.py"]
        assert state.file_decision_records == {}

    def test_empty_input_returns_empty(self, tmp_path):
        state = _make_state(tmp_path)
        ctx = _make_ctx(exists_map={})

        kept = AutoMergePhase()._filter_phantom_files([], state, ctx)

        assert kept == []
        assert state.file_decision_records == {}
