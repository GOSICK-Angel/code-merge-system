"""PR-B Slice 4: incompatible semantic_compatibility forces escalate_human.

When the analyst flags two changes as semantically incompatible, the
phase must override any directional recommendation (take_target /
take_current / semantic_merge) and route the file to a human. This
beats every other branch in `_select_merge_strategy`.

We do NOT gate on confidence here: a low-confidence "incompatible" is
still a no-auto-merge signal, and a high-confidence "incompatible"
means the LLM is sure the changes contradict — both must escalate.
"""

from __future__ import annotations

import pytest

from src.core.phases.conflict_analysis import _select_merge_strategy
from src.models.config import ThresholdConfig
from src.models.conflict import ConflictAnalysis, ConflictType
from src.models.decision import MergeDecision


def _ca(
    *,
    semantic_compatibility: str | None,
    recommended: MergeDecision,
    confidence: float = 0.95,
    can_coexist: bool = False,
    conflict_type: ConflictType = ConflictType.CONCURRENT_MODIFICATION,
) -> ConflictAnalysis:
    return ConflictAnalysis(
        file_path="x.ts",
        conflict_points=[],
        overall_confidence=confidence,
        recommended_strategy=recommended,
        conflict_type=conflict_type,
        can_coexist=can_coexist,
        rationale="r",
        confidence=confidence,
        semantic_compatibility=semantic_compatibility,  # type: ignore[arg-type]
    )


@pytest.fixture
def thresholds() -> ThresholdConfig:
    return ThresholdConfig()


class TestIncompatibleForcesEscalateHuman:
    @pytest.mark.parametrize(
        "recommended",
        [
            MergeDecision.TAKE_TARGET,
            MergeDecision.TAKE_CURRENT,
            MergeDecision.SEMANTIC_MERGE,
            MergeDecision.ESCALATE_HUMAN,
        ],
    )
    def test_high_confidence_incompatible_always_escalates(
        self, thresholds: ThresholdConfig, recommended: MergeDecision
    ) -> None:
        ca = _ca(
            semantic_compatibility="incompatible",
            recommended=recommended,
            confidence=0.95,
        )
        assert _select_merge_strategy(ca, thresholds) == MergeDecision.ESCALATE_HUMAN

    def test_low_confidence_incompatible_still_escalates(
        self, thresholds: ThresholdConfig
    ) -> None:
        ca = _ca(
            semantic_compatibility="incompatible",
            recommended=MergeDecision.TAKE_TARGET,
            confidence=0.3,
        )
        assert _select_merge_strategy(ca, thresholds) == MergeDecision.ESCALATE_HUMAN

    def test_can_coexist_does_not_override_incompatible(
        self, thresholds: ThresholdConfig
    ) -> None:
        ca = _ca(
            semantic_compatibility="incompatible",
            recommended=MergeDecision.SEMANTIC_MERGE,
            confidence=0.95,
            can_coexist=True,
        )
        assert _select_merge_strategy(ca, thresholds) == MergeDecision.ESCALATE_HUMAN


class TestCompatibleAndOrthogonalAreNeutral:
    """compatible / orthogonal must NOT shortcut to escalate — those are
    the "auto-mergeable" lanes; existing strategy logic still applies.
    """

    def test_compatible_keeps_existing_decision(
        self, thresholds: ThresholdConfig
    ) -> None:
        ca = _ca(
            semantic_compatibility="compatible",
            recommended=MergeDecision.TAKE_TARGET,
            confidence=0.95,
        )
        assert _select_merge_strategy(ca, thresholds) == MergeDecision.TAKE_TARGET

    def test_orthogonal_keeps_existing_decision(
        self, thresholds: ThresholdConfig
    ) -> None:
        ca = _ca(
            semantic_compatibility="orthogonal",
            recommended=MergeDecision.TAKE_CURRENT,
            confidence=0.95,
        )
        assert _select_merge_strategy(ca, thresholds) == MergeDecision.TAKE_CURRENT

    def test_none_keeps_existing_decision(self, thresholds: ThresholdConfig) -> None:
        ca = _ca(
            semantic_compatibility=None,
            recommended=MergeDecision.TAKE_TARGET,
            confidence=0.95,
        )
        assert _select_merge_strategy(ca, thresholds) == MergeDecision.TAKE_TARGET
