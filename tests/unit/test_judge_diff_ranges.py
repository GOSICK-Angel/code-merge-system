"""④ judge relevance anchors on merged-coordinate diff ranges.

The merged file is what gets chunked, so the changed-line ranges must be in
merged coordinates. Deriving them from the pre-merge snapshot keeps relevance
scoring aligned even when the merge shifts line numbers; the fork-side hunk
ranges drift after such shifts.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from src.agents.judge_agent import JudgeAgent, _merged_content_diff_ranges
from src.models.config import AgentLLMConfig
from src.models.decision import DecisionSource, FileDecisionRecord, MergeDecision
from src.models.diff import FileDiff, FileStatus, RiskLevel


class TestMergedContentDiffRanges:
    def test_ranges_are_in_merged_coordinates(self) -> None:
        # 3 lines inserted at the top, plus a change to a middle line. The
        # changed middle line was at before-line 2 but lands at after-line 5.
        before = "a\nTARGET\nc\nd\n"
        after = "x\ny\nz\na\nCHANGED\nc\nd\n"
        ranges = _merged_content_diff_ranges(before, after)
        assert (1, 3) in ranges  # the inserted block (after coords)
        assert (5, 5) in ranges  # the changed line in after coords, not (2, 2)

    def test_none_snapshot_returns_empty(self) -> None:
        assert _merged_content_diff_ranges(None, "a\nb\n") == []

    def test_identical_returns_empty(self) -> None:
        same = "a\nb\nc\n"
        assert _merged_content_diff_ranges(same, same) == []

    def test_large_file_guard_returns_empty(self) -> None:
        huge = "x\n" * 7000
        assert _merged_content_diff_ranges(huge, huge + "y\n") == []


def _make_judge() -> JudgeAgent:
    with patch("src.llm.client.LLMClientFactory.create"):
        return JudgeAgent(AgentLLMConfig(), git_tool=None)


def _record(snapshot: str | None) -> FileDecisionRecord:
    return FileDecisionRecord(
        file_path="demo.txt",
        file_status=FileStatus.MODIFIED,
        decision=MergeDecision.SEMANTIC_MERGE,
        decision_source=DecisionSource.AUTO_EXECUTOR,
        rationale="merged",
        original_snapshot=snapshot,
    )


async def test_review_file_prefers_snapshot_ranges() -> None:
    """review_file passes merged-coordinate ranges (from the snapshot) to
    build_staged_content, not the fork-side hunk ranges."""
    agent = _make_judge()
    from src.llm import prompt_builders as pb

    captured: dict[str, object] = {}

    def _capture(self, content, file_path, diff_ranges, budget_tokens, **kw):  # type: ignore[no-untyped-def]
        captured["diff_ranges"] = diff_ranges
        return content

    before = "a\nOLD\nc\nd\n"
    merged = "x\ny\nz\na\nNEW\nc\nd\n"

    with (
        patch.object(pb.AgentPromptBuilder, "build_staged_content", _capture),
        patch.object(agent, "_call_llm_with_retry", new=AsyncMock(return_value="[]")),
        patch("src.agents.judge_agent.parse_file_review_issues", return_value=[]),
    ):
        await agent.review_file(
            file_path="demo.txt",
            merged_content=merged,
            decision_record=_record(before),
            original_diff=FileDiff(
                file_path="demo.txt",
                file_status=FileStatus.MODIFIED,
                risk_level=RiskLevel.AUTO_SAFE,
                risk_score=0.0,
            ),
        )

    assert captured["diff_ranges"] == _merged_content_diff_ranges(before, merged)
    assert (5, 5) in captured["diff_ranges"]  # merged coords, not (2, 2)
