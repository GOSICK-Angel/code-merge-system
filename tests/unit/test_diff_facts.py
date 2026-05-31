"""PR-C Slice 1: compute_diff_facts — semantic verb-counts per side.

Pure helper that turns three-way content into deterministic
``{added, removed, modified}`` counts via difflib opcodes. This is
what the prompt will inject as ground truth so the analyst stops
saying "added" when the actual operation was a modify-in-place
(observed on the zod E2E: versions.ts "+1/-1" → rationale claimed
"both sides added entries").

We measure each *side* relative to the merge-base (`base→fork`,
`base→upstream`) — same axes the prompt already uses for line
totals.
"""

from __future__ import annotations

from src.tools.diff_facts import compute_diff_facts


class TestComputeDiffFactsPerSide:
    def test_pure_addition_on_one_side(self) -> None:
        facts = compute_diff_facts(
            base="line1\nline2\n",
            fork="line1\nline2\nlineNEW\n",
            upstream="line1\nline2\n",
        )
        assert facts["fork_side"]["added"] >= 1
        assert facts["fork_side"]["removed"] == 0
        assert facts["fork_side"]["modified"] == 0
        assert facts["upstream_side"] == {"added": 0, "removed": 0, "modified": 0}

    def test_pure_removal(self) -> None:
        facts = compute_diff_facts(
            base="a\nb\nc\n",
            fork="a\nc\n",
            upstream="a\nb\nc\n",
        )
        assert facts["fork_side"]["added"] == 0
        assert facts["fork_side"]["removed"] >= 1
        assert facts["fork_side"]["modified"] == 0

    def test_modify_in_place_is_modified_not_added(self) -> None:
        # versions.ts pattern: same line replaced. The prompt previously
        # showed "+1/-1" which the LLM read as "added + removed"; the
        # correct verb is *modified*.
        facts = compute_diff_facts(
            base='version = "1.0.0"\n',
            fork='version = "1.0.0"\n',
            upstream='version = "1.0.1"\n',
        )
        assert facts["upstream_side"]["modified"] >= 1
        assert facts["upstream_side"]["added"] == 0
        assert facts["upstream_side"]["removed"] == 0

    def test_both_sides_independent_changes(self) -> None:
        facts = compute_diff_facts(
            base="a\nb\nc\n",
            fork="a\nb\nc\nF1\n",
            upstream="a\nb\nc\nU1\n",
        )
        assert facts["fork_side"]["added"] >= 1
        assert facts["upstream_side"]["added"] >= 1


class TestComputeDiffFactsEdgeCases:
    def test_none_base_treated_as_empty(self) -> None:
        facts = compute_diff_facts(base=None, fork="x\n", upstream="x\n")
        # both sides added the same lines vs missing base
        assert facts["fork_side"]["added"] >= 1
        assert facts["upstream_side"]["added"] >= 1

    def test_none_fork_treated_as_empty(self) -> None:
        facts = compute_diff_facts(base="a\nb\n", fork=None, upstream="a\nb\n")
        assert facts["fork_side"]["removed"] >= 1
        assert facts["upstream_side"] == {"added": 0, "removed": 0, "modified": 0}

    def test_all_empty_yields_zeros(self) -> None:
        facts = compute_diff_facts(base="", fork="", upstream="")
        assert facts == {
            "fork_side": {"added": 0, "removed": 0, "modified": 0},
            "upstream_side": {"added": 0, "removed": 0, "modified": 0},
        }

    def test_identical_content_yields_zeros(self) -> None:
        same = "a\nb\nc\n"
        facts = compute_diff_facts(base=same, fork=same, upstream=same)
        assert facts == {
            "fork_side": {"added": 0, "removed": 0, "modified": 0},
            "upstream_side": {"added": 0, "removed": 0, "modified": 0},
        }
