"""conflict_analyst computes native_3way_outcome and threads it through.

Both entry points (analyze_file and analyze_commit_round) must:
  1. Run predict_native_3way_outcome on the actual three-way content
     they were handed
  2. Pass the resulting outcome to the prompt builder so the LLM sees
     ground truth instead of the misleading conflict_count field
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.conflict_analyst_agent import ConflictAnalystAgent
from src.models.config import AgentLLMConfig
from src.models.diff import FileDiff, FileStatus, RiskLevel


def _diff() -> FileDiff:
    return FileDiff(
        file_path="versions.ts",
        file_status=FileStatus.MODIFIED,
        hunks=[],
        lines_added=1,
        lines_deleted=1,
        risk_score=0.5,
        risk_level=RiskLevel.AUTO_RISKY,
    )


@pytest.mark.asyncio
async def test_analyze_file_passes_native_3way_outcome_to_prompt() -> None:
    with patch("src.llm.client.LLMClientFactory.create"):
        agent = ConflictAnalystAgent(AgentLLMConfig())

    captured: dict[str, object] = {}

    def fake_build(file_diff, base, current, target, context, **kwargs) -> str:
        captured["native_3way_outcome"] = kwargs.get("native_3way_outcome")
        return "PROMPT"

    fake_parse = MagicMock(
        return_value=MagicMock(file_path="versions.ts", rationale="r")
    )
    agent._call_llm_with_retry = AsyncMock(return_value="{}")  # type: ignore[method-assign]
    with (
        patch(
            "src.agents.conflict_analyst_agent.build_conflict_analysis_prompt",
            side_effect=fake_build,
        ),
        patch(
            "src.agents.conflict_analyst_agent.parse_conflict_analysis",
            fake_parse,
        ),
    ):
        await agent.analyze_file(
            _diff(),
            base_content='version = "1.0.0"\n',
            current_content='version = "1.0.0-fork"\n',
            target_content='version = "1.0.1"\n',
            project_context="",
        )

    assert captured["native_3way_outcome"] == "conflict"


@pytest.mark.asyncio
async def test_analyze_commit_round_passes_outcome_per_file() -> None:
    with patch("src.llm.client.LLMClientFactory.create"):
        agent = ConflictAnalystAgent(AgentLLMConfig())

    captured: dict[str, object] = {}

    def fake_build(
        round_commits, file_three_way, file_languages, project_context, **kwargs
    ) -> str:
        captured["map"] = kwargs.get("native_3way_outcome_by_file")
        return "PROMPT"

    agent._call_llm_with_retry = AsyncMock(  # type: ignore[method-assign]
        return_value='{"files": []}'
    )
    with patch(
        "src.agents.conflict_analyst_agent.build_commit_round_prompt",
        side_effect=fake_build,
    ):
        await agent.analyze_commit_round(
            round_commits=[
                {"sha": "abc12345", "message": "x", "files": ["versions.ts"]}
            ],
            file_three_way={
                "versions.ts": (
                    'v = "1.0.0"\n',
                    'v = "1.0.0-fork"\n',
                    'v = "1.0.1"\n',
                )
            },
            file_languages={"versions.ts": "typescript"},
            project_context="",
        )

    outcome_map = captured["map"]
    assert isinstance(outcome_map, dict)
    assert outcome_map["versions.ts"] == "conflict"
