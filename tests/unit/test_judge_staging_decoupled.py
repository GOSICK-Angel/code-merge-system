"""Judge runs build_staged_content regardless of memory_store (U1.A parity).

Regression: judge once gated staging behind ``if self._memory_store``; with
memory off it shipped large merged files raw (head-truncated at the default
cap), risking the forgejo "tokens=309/98789 -> false truncated verdict"
failure. conflict_analyst / executor already decoupled this; judge now matches.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.judge_agent import JudgeAgent
from src.models.config import AgentLLMConfig
from src.models.decision import DecisionSource, FileDecisionRecord, MergeDecision
from src.models.diff import FileDiff, FileStatus, RiskLevel


def _make_judge() -> JudgeAgent:
    with patch("src.llm.client.LLMClientFactory.create"):
        agent = JudgeAgent(AgentLLMConfig(), git_tool=None)
    assert agent._memory_store is None  # default — the gate under test
    return agent


def _record() -> FileDecisionRecord:
    return FileDecisionRecord(
        file_path="demo.txt",
        file_status=FileStatus.MODIFIED,
        decision=MergeDecision.TAKE_TARGET,
        decision_source=DecisionSource.AUTO_EXECUTOR,
        rationale="took target",
    )


async def test_judge_stages_content_without_memory_store() -> None:
    agent = _make_judge()
    from src.llm import prompt_builders as pb

    wrapper = MagicMock(side_effect=lambda *a, **kw: a[0])

    with (
        patch.object(pb.AgentPromptBuilder, "build_staged_content", wrapper),
        patch.object(
            agent,
            "_call_llm_with_retry",
            new=AsyncMock(return_value="[]"),
        ),
        patch(
            "src.agents.judge_agent.parse_file_review_issues",
            return_value=[],
        ),
    ):
        await agent.review_file(
            file_path="demo.txt",
            merged_content="line\n" * 500,
            decision_record=_record(),
            original_diff=FileDiff(
                file_path="demo.txt",
                file_status=FileStatus.MODIFIED,
                risk_level=RiskLevel.AUTO_SAFE,
                risk_score=0.0,
            ),
        )

    assert wrapper.call_count >= 1, (
        "judge must stage content even when memory_store is None"
    )
