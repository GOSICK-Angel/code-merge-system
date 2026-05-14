from unittest.mock import AsyncMock, patch

import pytest

from src.agents.conflict_analyst_agent import ConflictAnalystAgent
from src.models.conflict import ConflictAnalysis, ConflictType
from src.models.config import AgentLLMConfig
from src.models.decision import MergeDecision


def _make_agent() -> ConflictAnalystAgent:
    with patch.dict("os.environ", {"TEST_KEY": "fake-key"}):
        return ConflictAnalystAgent(
            AgentLLMConfig(
                provider="anthropic",
                model="test-model",
                api_key_env="TEST_KEY",
                max_retries=1,
            )
        )


def _round_inputs() -> tuple[list[dict], dict, dict]:
    commits = [{"sha": "abc123", "message": "feat: x", "files": ["a.py", "b.py"]}]
    three_way = {
        "a.py": ("base_a", "fork_a", "upstream_a"),
        "b.py": ("base_b", "fork_b", "upstream_b"),
    }
    langs = {"a.py": "python", "b.py": "python"}
    return commits, three_way, langs


@pytest.mark.asyncio
async def test_commit_round_llm_failure_returns_empty_and_keeps_breaker_counter():
    agent = _make_agent()
    commits, three_way, langs = _round_inputs()

    with patch.object(
        agent,
        "_call_llm_with_retry",
        new=AsyncMock(side_effect=RuntimeError("LLM unreachable")),
    ):
        result = await agent.analyze_commit_round(commits, three_way, langs)

    assert result == {}


@pytest.mark.asyncio
async def test_commit_round_empty_parse_trips_circuit_breaker_counter():
    agent = _make_agent()
    commits, three_way, langs = _round_inputs()

    starting = agent.consecutive_failures
    raw_truncated = '{"files": ['

    with patch.object(
        agent, "_call_llm_with_retry", new=AsyncMock(return_value=raw_truncated)
    ):
        result = await agent.analyze_commit_round(commits, three_way, langs)

    assert result == {}
    assert agent.consecutive_failures == starting + 1


@pytest.mark.asyncio
async def test_commit_round_partial_parse_does_not_trip_breaker():
    agent = _make_agent()
    commits, three_way, langs = _round_inputs()

    starting = agent.consecutive_failures
    partial_response = {
        "a.py": ConflictAnalysis(
            file_path="a.py",
            conflict_points=[],
            overall_confidence=0.8,
            recommended_strategy=MergeDecision.TAKE_TARGET,
            conflict_type=ConflictType.SEMANTIC_EQUIVALENT,
            confidence=0.8,
        )
    }

    with patch.object(agent, "_call_llm_with_retry", new=AsyncMock(return_value="{}")):
        with patch(
            "src.agents.conflict_analyst_agent.parse_commit_round_analyses",
            return_value=partial_response,
        ):
            result = await agent.analyze_commit_round(commits, three_way, langs)

    assert set(result.keys()) == {"a.py"}
    assert agent.consecutive_failures == starting


@pytest.mark.asyncio
async def test_commit_round_full_parse_does_not_trip_breaker():
    agent = _make_agent()
    commits, three_way, langs = _round_inputs()

    starting = agent.consecutive_failures
    full_response = {
        fp: ConflictAnalysis(
            file_path=fp,
            conflict_points=[],
            overall_confidence=0.8,
            recommended_strategy=MergeDecision.TAKE_TARGET,
            conflict_type=ConflictType.SEMANTIC_EQUIVALENT,
            confidence=0.8,
        )
        for fp in three_way
    }

    with patch.object(agent, "_call_llm_with_retry", new=AsyncMock(return_value="{}")):
        with patch(
            "src.agents.conflict_analyst_agent.parse_commit_round_analyses",
            return_value=full_response,
        ):
            result = await agent.analyze_commit_round(commits, three_way, langs)

    assert set(result.keys()) == set(three_way.keys())
    assert agent.consecutive_failures == starting


@pytest.mark.asyncio
async def test_commit_round_empty_file_set_returns_empty_without_llm_call():
    agent = _make_agent()
    starting = agent.consecutive_failures
    call_mock = AsyncMock()

    with patch.object(agent, "_call_llm_with_retry", new=call_mock):
        result = await agent.analyze_commit_round([], {}, {})

    assert result == {}
    assert call_mock.await_count == 0
    assert agent.consecutive_failures == starting
