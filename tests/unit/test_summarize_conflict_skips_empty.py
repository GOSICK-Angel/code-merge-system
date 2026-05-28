"""summarize_conflict_analysis must skip epistemically empty entries.

Companion to test_memory_epistemic_filter.py: that proves the predicate
classifies rationales correctly; this proves the write site honours it.
"""

from __future__ import annotations

from uuid import uuid4

from src.memory.summarizer import PhaseSummarizer
from src.models.config import MergeConfig
from src.models.conflict import ConflictAnalysis, ConflictType
from src.models.decision import MergeDecision
from src.models.state import MergeState


def _state(rationale: str) -> MergeState:
    """Build a minimal MergeState carrying one conflict analysis."""
    state = MergeState(
        run_id=str(uuid4()),
        config=MergeConfig(upstream_ref="upstream", fork_ref="fork"),
    )
    fp = "packages/zod/src/v4/core/versions.ts"
    state.conflict_analyses = {
        fp: ConflictAnalysis(
            file_path=fp,
            conflict_points=[],
            overall_confidence=0.5,
            recommended_strategy=MergeDecision.SEMANTIC_MERGE,
            conflict_type=ConflictType.CONCURRENT_MODIFICATION,
            rationale=rationale,
            confidence=0.5,
        )
    }
    return state


def _decision_entries(entries):
    return [
        e
        for e in entries
        if e.phase == "conflict_analysis"
        and "versions.ts" in (e.file_paths[0] if e.file_paths else "")
    ]


class TestSummarizeSkipsEpistemicallyEmpty:
    def test_skips_when_rationale_says_without_seeing(self) -> None:
        state = _state(
            "Without seeing actual file content, both sides made small changes."
        )
        _, entries = PhaseSummarizer().summarize_conflict_analysis(state)
        assert _decision_entries(entries) == []

    def test_skips_no_actual_diff_content_marker(self) -> None:
        state = _state("No actual diff content available. Recommending semantic_merge.")
        _, entries = PhaseSummarizer().summarize_conflict_analysis(state)
        assert _decision_entries(entries) == []

    def test_skips_based_on_prior_pattern_marker(self) -> None:
        state = _state(
            "Based on prior pattern decisions for this exact file, "
            "semantic_merge is appropriate."
        )
        _, entries = PhaseSummarizer().summarize_conflict_analysis(state)
        assert _decision_entries(entries) == []


class TestSummarizeKeepsSubstantiveRationale:
    def test_keeps_specific_rationale(self) -> None:
        state = _state(
            "Upstream modified the cidrv6 regex to a more permissive form; "
            "fork added cidrv6Mapped export immediately below it."
        )
        _, entries = PhaseSummarizer().summarize_conflict_analysis(state)
        assert len(_decision_entries(entries)) == 1
