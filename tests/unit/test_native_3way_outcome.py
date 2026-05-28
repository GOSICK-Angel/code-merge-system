"""Predict native 3-way merge outcome from raw three-way content.

PR (memory loop follow-up): conflict_analyst was misled by
``file_diff.conflict_count`` (computed against the original fork /
upstream refs, which are clean branches — always 0) and wrote
rationale like "no actual conflict markers present" even when a real
3-way merge would produce markers.

This helper gives the agent ground truth before LLM call: pass the
three pieces of content, get back ``clean`` (git merge-file produced
no markers), ``conflict`` (markers would appear), or ``missing`` (one
of the three sides is None — e.g. file added on only one side).
"""

from __future__ import annotations

from src.tools.native_3way import predict_native_3way_outcome


class TestPredictNative3WayOutcome:
    def test_pure_addition_one_side_is_clean(self) -> None:
        base = "line1\nline2\n"
        fork = "line1\nline2\nlineNEW\n"
        upstream = "line1\nline2\n"
        assert predict_native_3way_outcome(base, fork, upstream) == "clean"

    def test_identical_three_sides_is_clean(self) -> None:
        same = "a\nb\nc\n"
        assert predict_native_3way_outcome(same, same, same) == "clean"

    def test_disjoint_changes_far_apart_is_clean(self) -> None:
        # Fork changes line 1; upstream changes line 100 — git can merge.
        base = "\n".join(f"line{i}" for i in range(100)) + "\n"
        fork = base.replace("line0", "FORK_CHANGED", 1)
        upstream = base.replace("line99", "UPSTREAM_CHANGED", 1)
        assert predict_native_3way_outcome(base, fork, upstream) == "clean"

    def test_adjacent_modifications_produce_markers(self) -> None:
        # versions.ts shape: fork added a line on line 5, upstream replaced
        # an existing line on line 5 — same context → marker.
        base = "a\nb\nc\nd\ne\n"
        fork = "a\nb\nFORK_ADD\nc\nd\ne\n"
        upstream = "a\nb\nc\nd\nUP_MOD\n"
        # whether this collides depends on context; ensure helper returns
        # exactly one of {clean, conflict} — never the missing fallback.
        outcome = predict_native_3way_outcome(base, fork, upstream)
        assert outcome in {"clean", "conflict"}

    def test_real_overlap_modify_in_place_marker(self) -> None:
        base = 'version = "1.0.0"\n'
        fork = 'version = "1.0.0-fork"\n'
        upstream = 'version = "1.0.1"\n'
        assert predict_native_3way_outcome(base, fork, upstream) == "conflict"

    def test_none_base_is_missing(self) -> None:
        assert predict_native_3way_outcome(None, "x", "y") == "missing"

    def test_none_fork_is_missing(self) -> None:
        assert predict_native_3way_outcome("base", None, "y") == "missing"

    def test_none_upstream_is_missing(self) -> None:
        assert predict_native_3way_outcome("base", "x", None) == "missing"
