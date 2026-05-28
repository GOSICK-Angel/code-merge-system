"""PR-A Slice 2: ConflictAnalysis exposes ``grounding_warnings``.

Reviewer UI needs a structured channel to display fabricated symbols the
analyst's rationale referenced. The default must be the empty list so
existing analyses (and round-trips through model_dump / model_validate)
behave unchanged.
"""

from __future__ import annotations

from src.models.conflict import ConflictAnalysis
from src.models.decision import MergeDecision


def _make_analysis(**overrides: object) -> ConflictAnalysis:
    base = dict(
        file_path="x.ts",
        conflict_points=[],
        overall_confidence=0.5,
        recommended_strategy=MergeDecision.SEMANTIC_MERGE,
    )
    base.update(overrides)
    return ConflictAnalysis(**base)  # type: ignore[arg-type]


class TestGroundingWarningsField:
    def test_default_empty(self) -> None:
        assert _make_analysis().grounding_warnings == []

    def test_accepts_list(self) -> None:
        a = _make_analysis(grounding_warnings=["core._isoWeek"])
        assert a.grounding_warnings == ["core._isoWeek"]

    def test_round_trip_preserves_field(self) -> None:
        a = _make_analysis(grounding_warnings=["core._isoWeek", "lib.bogus"])
        dumped = a.model_dump()
        assert dumped["grounding_warnings"] == ["core._isoWeek", "lib.bogus"]
        restored = ConflictAnalysis.model_validate(dumped)
        assert restored.grounding_warnings == ["core._isoWeek", "lib.bogus"]
