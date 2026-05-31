"""Tests for P-γ-1.5-B: fork-deleted files must not be restored from upstream.

Repro (R2 r2-0001 v4): ``pkg/plugin_packager/decoder/helper_test.go``
existed in base, fork explicitly removed it in fork.patch, upstream
untouched. ``classify_three_way`` returns ``D_MISSING`` (head=None,
upstream!=None) without distinguishing genuine D_MISSING from
FORK_DELETED. Pre-fix, executor copied the file back from upstream,
silently re-introducing code the fork meant to drop.
"""

from __future__ import annotations

import pytest

from src.models.diff import ForkDivergence
from src.models.decision import MergeDecision
from src.tools.file_classifier import _fork_deleted_skip_record, is_fork_deleted


class _FakeState:
    """Minimal stand-in for MergeState — only ``fork_divergence_map`` matters."""

    def __init__(self, div_map: dict[str, str]) -> None:
        self.fork_divergence_map = div_map


class TestIsForkDeleted:
    def test_fork_deleted_returns_true(self) -> None:
        state = _FakeState({"a.py": ForkDivergence.FORK_DELETED.value})
        assert is_fork_deleted(state, "a.py") is True

    def test_other_divergence_returns_false(self) -> None:
        state = _FakeState(
            {
                "a.py": ForkDivergence.FORK_MODIFIED.value,
                "b.py": ForkDivergence.UPSTREAM_ADDED.value,
                "c.py": ForkDivergence.FORK_ONLY.value,
                "d.py": ForkDivergence.UPSTREAM_ONLY_CHANGE.value,
            }
        )
        for fp in ("a.py", "b.py", "c.py", "d.py"):
            assert is_fork_deleted(state, fp) is False, fp

    def test_genuine_d_missing_returns_false(self) -> None:
        """A file the planner sees as D_MISSING but that is actually a
        new upstream file (not in base) is marked UPSTREAM_ADDED in the
        divergence map — must NOT trigger the fork-delete skip."""
        state = _FakeState({"new.py": ForkDivergence.UPSTREAM_ADDED.value})
        assert is_fork_deleted(state, "new.py") is False

    def test_missing_path_returns_false(self) -> None:
        state = _FakeState({"a.py": ForkDivergence.FORK_DELETED.value})
        assert is_fork_deleted(state, "absent.py") is False

    def test_missing_map_returns_false(self) -> None:
        """State without ``fork_divergence_map`` (e.g. early init) yields
        False without raising — the caller fallback (copy from upstream)
        is no worse than today's behaviour."""

        class _NoMap:
            pass

        assert is_fork_deleted(_NoMap(), "a.py") is False
        assert is_fork_deleted(_FakeState({}), "a.py") is False


class TestForkDeletedSkipRecord:
    def test_decision_is_skip(self) -> None:
        rec = _fork_deleted_skip_record("pkg/foo/helper_test.go")
        assert rec.file_path == "pkg/foo/helper_test.go"
        assert rec.decision == MergeDecision.SKIP

    def test_rationale_mentions_fork_deleted(self) -> None:
        rec = _fork_deleted_skip_record("a.py")
        assert "fork" in rec.rationale.lower()
        assert "delete" in rec.rationale.lower()

    def test_agent_is_fork_delete_preserver(self) -> None:
        rec = _fork_deleted_skip_record("a.py")
        assert rec.agent == "fork_delete_preserver"


# NOTE: executor-level integration coverage is provided by the v5 R2
# end-to-end run (P-γ-1.5-B verification). The 8 tests above pin the
# helper + record construction; spanning the executor's batch loop with
# pure unit mocks proved disproportionately complex relative to value.
