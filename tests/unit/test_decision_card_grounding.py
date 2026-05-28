"""PR-A Slice 4 (backend): _build_human_decision_request propagates
``grounding_warnings`` from the ConflictAnalysis to the
HumanDecisionRequest the reviewer UI receives.
"""

from __future__ import annotations

from src.core.phases.conflict_analysis import _build_human_decision_request
from src.models.conflict import ConflictAnalysis, ConflictType
from src.models.decision import MergeDecision
from src.models.diff import FileDiff, FileStatus, RiskLevel


def _fd(path: str = "hub.ts") -> FileDiff:
    return FileDiff(
        file_path=path,
        file_status=FileStatus.MODIFIED,
        risk_level=RiskLevel.AUTO_RISKY,
        risk_score=0.4,
        lines_added=5,
        lines_deleted=2,
        lines_changed=5,
    )


def _analysis(grounding_warnings: list[str] | None = None) -> ConflictAnalysis:
    return ConflictAnalysis(
        file_path="hub.ts",
        conflict_points=[],
        overall_confidence=0.7,
        recommended_strategy=MergeDecision.SEMANTIC_MERGE,
        conflict_type=ConflictType.REFACTOR_VS_FEATURE,
        rationale="use core._isoWeek if available",
        confidence=0.7,
        grounding_warnings=grounding_warnings or [],
    )


def test_decision_card_propagates_grounding_warnings() -> None:
    req = _build_human_decision_request(
        _fd(), _analysis(grounding_warnings=["core._isoWeek"])
    )
    assert req.grounding_warnings == ["core._isoWeek"]


def test_decision_card_default_empty_when_no_warnings() -> None:
    req = _build_human_decision_request(_fd(), _analysis())
    assert req.grounding_warnings == []
