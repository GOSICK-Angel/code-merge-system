"""target-side diff ranges for staging target_content.

The fork (current) and upstream (target) versions of a file place the same
logical change at different line numbers. Staging target_content with the
fork-side hunk ranges anchors relevance on the wrong lines. conflict_analyst
and executor must derive target ranges from DiffHunk.start_line_target /
end_line_target when staging the upstream view; base_content keeps the
current-side ranges (DiffHunk carries no base coordinates — known limitation).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from src.agents import conflict_analyst_agent as ca
from src.agents import executor_agent as ex
from src.agents.conflict_analyst_agent import ConflictAnalystAgent
from src.agents.executor_agent import ExecutorAgent
from src.llm import prompt_builders as pb
from src.models.config import AgentLLMConfig, MergeConfig
from src.models.conflict import ConflictAnalysis, ConflictType
from src.models.decision import DecisionSource, FileDecisionRecord, MergeDecision
from src.models.diff import DiffHunk, FileDiff, FileStatus, RiskLevel
from src.models.plan import MergePhase
from src.models.state import MergeState


def _file_diff_with_shifted_hunk() -> FileDiff:
    hunk = DiffHunk(
        hunk_id="h1",
        start_line_current=10,
        end_line_current=12,
        start_line_target=20,
        end_line_target=22,
        content_current="cur",
        content_target="tgt",
        content_base=None,
        has_conflict=False,
    )
    return FileDiff(
        file_path="demo.py",
        file_status=FileStatus.MODIFIED,
        risk_level=RiskLevel.AUTO_RISKY,
        risk_score=0.5,
        lines_added=3,
        lines_deleted=1,
        hunks=[hunk],
    )


def test_conflict_analyst_extract_ranges_by_side() -> None:
    fd = _file_diff_with_shifted_hunk()
    assert ca._extract_diff_ranges(fd) == [(10, 12)]
    assert ca._extract_diff_ranges(fd, side="current") == [(10, 12)]
    assert ca._extract_diff_ranges(fd, side="target") == [(20, 22)]


def test_executor_extract_ranges_by_side() -> None:
    fd = _file_diff_with_shifted_hunk()
    assert ex._extract_diff_ranges(fd) == [(10, 12)]
    assert ex._extract_diff_ranges(fd, side="target") == [(20, 22)]


def _make_analyst() -> ConflictAnalystAgent:
    return ConflictAnalystAgent(
        AgentLLMConfig(
            provider="anthropic",
            model="test-model",
            api_key_env="ANTHROPIC_API_KEY",
            max_retries=1,
        )
    )


async def test_conflict_analyst_stages_target_with_target_ranges() -> None:
    agent = _make_analyst()
    captured: dict[str, list[tuple[int, int]]] = {}

    def _capture(self, content, file_path, diff_ranges, budget_tokens, **kw):  # type: ignore[no-untyped-def]
        captured[content] = diff_ranges
        return content

    fd = _file_diff_with_shifted_hunk()
    with (
        patch.object(pb.AgentPromptBuilder, "build_staged_content", _capture),
        patch.object(agent, "_call_llm_with_retry", new=AsyncMock(return_value="{}")),
        patch(
            "src.agents.conflict_analyst_agent.parse_conflict_analysis",
            return_value=ConflictAnalysis(
                file_path="demo.py",
                conflict_points=[],
                overall_confidence=0.9,
                recommended_strategy=MergeDecision.SEMANTIC_MERGE,
                conflict_type=ConflictType.UNKNOWN,
            ),
        ),
    ):
        await agent.analyze_file(
            file_diff=fd,
            base_content="BASE",
            current_content="CURRENT",
            target_content="TARGET",
        )

    assert captured["CURRENT"] == [(10, 12)]
    assert captured["TARGET"] == [(20, 22)]
    assert captured["BASE"] == [(10, 12)]


async def test_executor_stages_target_with_target_ranges() -> None:
    agent = ExecutorAgent(
        AgentLLMConfig(
            provider="openai",
            model="test-model",
            api_key_env="OPENAI_API_KEY",
            max_retries=1,
        )
    )
    captured: dict[str, list[tuple[int, int]]] = {}

    def _capture(self, content, file_path, diff_ranges, budget_tokens, **kw):  # type: ignore[no-untyped-def]
        captured[content] = diff_ranges
        return content

    mock_git = MagicMock()
    mock_git.get_file_content.side_effect = lambda ref, path: (
        "CURRENT" if ref == "feature/fork" else "TARGET"
    )
    agent.git_tool = mock_git

    state = MergeState(
        config=MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
    )
    state.current_phase = MergePhase.AUTO_MERGE

    fd = _file_diff_with_shifted_hunk()
    analysis = ConflictAnalysis(
        file_path="demo.py",
        conflict_points=[],
        overall_confidence=0.9,
        recommended_strategy=MergeDecision.SEMANTIC_MERGE,
        conflict_type=ConflictType.UNKNOWN,
    )
    merged_record = FileDecisionRecord(
        file_path="demo.py",
        file_status=FileStatus.MODIFIED,
        decision=MergeDecision.SEMANTIC_MERGE,
        decision_source=DecisionSource.AUTO_EXECUTOR,
        rationale="merged",
    )

    with (
        patch.object(pb.AgentPromptBuilder, "build_staged_content", _capture),
        patch.object(agent, "_call_llm_with_retry_meta", new=AsyncMock()) as meta,
        patch(
            "src.agents.executor_agent.parse_merge_result",
            return_value="MERGED",
        ),
        patch("src.agents.executor_agent._foreign_chars", return_value=None),
        patch(
            "src.agents.executor_agent.apply_with_snapshot",
            new=AsyncMock(return_value=merged_record),
        ),
    ):
        meta.return_value = MagicMock(stop_reason="stop")
        await agent.execute_semantic_merge(fd, analysis, state)

    assert captured["CURRENT"] == [(10, 12)]
    assert captured["TARGET"] == [(20, 22)]
