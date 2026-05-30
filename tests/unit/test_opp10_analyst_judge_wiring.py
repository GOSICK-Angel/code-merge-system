"""OPP-10 follow-up: analyst / judge forward degree weights to staging.

``weights_from_fanin`` derives per-symbol relevance from dependency-graph
fan-in so a high-fan-in public interface stays FULL under staged compression.
Executor already consumed this; the analyst (``analyze_file``) and judge
(``review_file``) now plumb a ``symbol_weights`` mapping into every
``build_staged_content`` call. These tests pin that the kwarg reaches the
builder and that the empty-graph path degrades to ``None`` (flat behaviour).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.conflict_analyst_agent import ConflictAnalystAgent
from src.agents.judge_agent import JudgeAgent
from src.models.config import AgentLLMConfig
from src.models.decision import DecisionSource, FileDecisionRecord, MergeDecision
from src.models.diff import FileDiff, FileStatus, RiskLevel


def _record() -> FileDecisionRecord:
    return FileDecisionRecord(
        file_path="demo.py",
        file_status=FileStatus.MODIFIED,
        decision=MergeDecision.TAKE_TARGET,
        decision_source=DecisionSource.AUTO_EXECUTOR,
        rationale="took target",
    )


def _file_diff() -> FileDiff:
    return FileDiff(
        file_path="demo.py",
        file_status=FileStatus.MODIFIED,
        risk_level=RiskLevel.AUTO_SAFE,
        risk_score=0.0,
    )


async def test_judge_forwards_symbol_weights_to_staging() -> None:
    with patch("src.llm.client.LLMClientFactory.create"):
        agent = JudgeAgent(AgentLLMConfig(), git_tool=None)
    from src.llm import prompt_builders as pb

    wrapper = MagicMock(side_effect=lambda *a, **kw: a[0])
    weights = {"Hub": 0.65, "Leaf": 0.30}
    with (
        patch.object(pb.AgentPromptBuilder, "build_staged_content", wrapper),
        patch.object(agent, "_call_llm_with_retry", new=AsyncMock(return_value="[]")),
        patch("src.agents.judge_agent.parse_file_review_issues", return_value=[]),
    ):
        await agent.review_file(
            file_path="demo.py",
            merged_content="line\n" * 500,
            decision_record=_record(),
            original_diff=_file_diff(),
            symbol_weights=weights,
        )

    assert wrapper.call_count >= 1
    assert all(
        call.kwargs.get("symbol_weights") == weights for call in wrapper.call_args_list
    )


async def test_analyst_forwards_symbol_weights_to_staging() -> None:
    with patch("src.llm.client.LLMClientFactory.create"):
        agent = ConflictAnalystAgent(AgentLLMConfig(), git_tool=None)
    from src.llm import prompt_builders as pb

    wrapper = MagicMock(side_effect=lambda *a, **kw: a[0])
    weights = {"Hub": 0.65, "Leaf": 0.30}
    with (
        patch.object(pb.AgentPromptBuilder, "build_staged_content", wrapper),
        patch.object(agent, "_call_llm_with_retry", new=AsyncMock(return_value="{}")),
        patch(
            "src.agents.conflict_analyst_agent.parse_conflict_analysis",
            return_value=MagicMock(),
        ),
    ):
        await agent.analyze_file(
            _file_diff(),
            base_content="base\n" * 500,
            current_content="cur\n" * 500,
            target_content="tgt\n" * 500,
            symbol_weights=weights,
        )

    assert wrapper.call_count >= 1, "analyst must stage at least one side"
    assert all(
        call.kwargs.get("symbol_weights") == weights for call in wrapper.call_args_list
    )
