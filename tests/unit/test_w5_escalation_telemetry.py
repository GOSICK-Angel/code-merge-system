"""W5 W5-A: local escalation-by-category telemetry in the CI summary.

A pure join of ``state.file_categories`` × ``state.file_decision_records`` at
report time — no new tracking, no network. Lets an operator see whether
escalations cluster in C-class (expected) or leak from B-class (a red flag).
"""

from __future__ import annotations

from datetime import datetime

from src.models.config import MergeConfig
from src.models.decision import DecisionSource, FileDecisionRecord, MergeDecision
from src.models.diff import FileChangeCategory, FileStatus
from src.models.state import MergeState
from src.tools.ci_reporter import build_ci_summary


def _state() -> MergeState:
    return MergeState(config=MergeConfig(upstream_ref="u", fork_ref="f"))


def _decide(
    state: MergeState,
    fp: str,
    decision: MergeDecision,
    source: DecisionSource,
    category: FileChangeCategory,
) -> None:
    state.file_categories[fp] = category
    state.file_decision_records[fp] = FileDecisionRecord(
        file_path=fp,
        file_status=FileStatus.MODIFIED,
        decision=decision,
        decision_source=source,
        confidence=0.9,
        rationale="t",
        timestamp=datetime.now(),
    )


class TestByCategoryTelemetry:
    def test_matrix_present_and_shaped(self) -> None:
        state = _state()
        _decide(
            state,
            "b.py",
            MergeDecision.TAKE_TARGET,
            DecisionSource.AUTO_EXECUTOR,
            FileChangeCategory.B,
        )
        _decide(
            state,
            "c1.py",
            MergeDecision.ESCALATE_HUMAN,
            DecisionSource.AUTO_EXECUTOR,
            FileChangeCategory.C,
        )
        _decide(
            state,
            "c2.py",
            MergeDecision.SEMANTIC_MERGE,
            DecisionSource.HUMAN,
            FileChangeCategory.C,
        )
        bc = build_ci_summary(state)["by_category"]
        assert bc["upstream_only"]["auto"] == 1
        assert bc["both_changed"]["escalated"] == 1
        assert bc["both_changed"]["human"] == 1
        assert bc["both_changed"]["auto"] == 0

    def test_empty_state_empty_matrix(self) -> None:
        assert build_ci_summary(_state())["by_category"] == {}

    def test_unknown_category_bucketed(self) -> None:
        state = _state()
        _decide(
            state,
            "x.py",
            MergeDecision.TAKE_TARGET,
            DecisionSource.AUTO_EXECUTOR,
            FileChangeCategory.B,
        )
        del state.file_categories["x.py"]  # decision without a category mapping
        assert build_ci_summary(state)["by_category"]["unknown"]["auto"] == 1

    def test_no_network_egress_in_reporter(self) -> None:
        import inspect

        import src.tools.ci_reporter as mod

        src = inspect.getsource(mod)
        for bad in (
            "import requests",
            "import urllib",
            "import httpx",
            "import aiohttp",
            "import socket",
            "://",
        ):
            assert bad not in src, f"unexpected network token {bad!r} in ci_reporter"
