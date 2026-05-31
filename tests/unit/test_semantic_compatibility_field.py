"""PR-B Slice 1: semantic_compatibility on ConflictAnalysis / ConflictPoint.

Adds a structured three-state field that distinguishes:
  - "compatible"   → both sides can merge (lean to semantic_merge)
  - "incompatible" → contradiction; phase gate will force escalate_human
  - "orthogonal"   → changes don't interact; either take_* is safe

None is allowed for backward-compat; older runs / parser failures don't
break model construction.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.models.conflict import (
    ChangeIntent,
    ConflictAnalysis,
    ConflictPoint,
    ConflictType,
)
from src.models.decision import MergeDecision


def _intent() -> ChangeIntent:
    return ChangeIntent(description="x", intent_type="feature", confidence=0.5)


class TestSemanticCompatibilityOnConflictAnalysis:
    def test_default_is_none(self) -> None:
        ca = ConflictAnalysis(
            file_path="a.py",
            conflict_points=[],
            overall_confidence=0.5,
            recommended_strategy=MergeDecision.TAKE_TARGET,
        )
        assert ca.semantic_compatibility is None

    @pytest.mark.parametrize("value", ["compatible", "incompatible", "orthogonal"])
    def test_accepts_three_states(self, value: str) -> None:
        ca = ConflictAnalysis(
            file_path="a.py",
            conflict_points=[],
            overall_confidence=0.5,
            recommended_strategy=MergeDecision.TAKE_TARGET,
            semantic_compatibility=value,  # type: ignore[arg-type]
        )
        assert ca.semantic_compatibility == value

    def test_rejects_unknown_value(self) -> None:
        with pytest.raises(ValidationError):
            ConflictAnalysis(
                file_path="a.py",
                conflict_points=[],
                overall_confidence=0.5,
                recommended_strategy=MergeDecision.TAKE_TARGET,
                semantic_compatibility="maybe",  # type: ignore[arg-type]
            )


class TestSemanticCompatibilityOnConflictPoint:
    def test_default_is_none(self) -> None:
        cp = ConflictPoint(
            file_path="a.py",
            hunk_id="h1",
            conflict_type=ConflictType.UNKNOWN,
            upstream_intent=_intent(),
            fork_intent=_intent(),
            can_coexist=False,
            suggested_decision=MergeDecision.TAKE_TARGET,
            confidence=0.5,
            rationale="r",
        )
        assert cp.semantic_compatibility is None

    def test_accepts_three_states(self) -> None:
        cp = ConflictPoint(
            file_path="a.py",
            hunk_id="h1",
            conflict_type=ConflictType.UNKNOWN,
            upstream_intent=_intent(),
            fork_intent=_intent(),
            can_coexist=False,
            suggested_decision=MergeDecision.TAKE_TARGET,
            confidence=0.5,
            rationale="r",
            semantic_compatibility="incompatible",
        )
        assert cp.semantic_compatibility == "incompatible"
