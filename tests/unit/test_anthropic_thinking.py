"""P3-2: Anthropic extended-thinking is a per-agent opt-in config knob.

``AgentLLMConfig.thinking_budget_tokens`` wires through the factory into
``AnthropicClient``, which enables interleaved thinking and forces
temperature=1.0 (an Anthropic API constraint). Every default agent leaves it
``None`` so the capability is purely additive — no run changes behaviour
unless a config explicitly opts in.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from src.llm.client import AnthropicClient, LLMClientFactory
from src.models.config import AgentLLMConfig, AgentsLLMConfig


def _make_client(thinking_budget_tokens: int | None) -> AnthropicClient:
    with patch("anthropic.AsyncAnthropic"):
        return AnthropicClient(
            model="claude-opus-4-6",
            api_key="test-key",
            temperature=0.2,
            max_tokens=8192,
            max_retries=3,
            thinking_budget_tokens=thinking_budget_tokens,
        )


def _mock_response(text: str = "ok") -> MagicMock:
    content = MagicMock()
    content.text = text
    content.type = "text"
    response = MagicMock()
    response.content = [content]
    response.stop_reason = "end_turn"
    return response


class TestThinkingBudgetValidation:
    def test_below_minimum_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must be >= 1024"):
            AgentLLMConfig(thinking_budget_tokens=512, max_tokens=8192)

    def test_budget_not_less_than_max_tokens_rejected(self) -> None:
        with pytest.raises(ValidationError, match="strictly less than max_tokens"):
            AgentLLMConfig(thinking_budget_tokens=8192, max_tokens=8192)

    def test_valid_budget_accepted(self) -> None:
        cfg = AgentLLMConfig(thinking_budget_tokens=4096, max_tokens=8192)
        assert cfg.thinking_budget_tokens == 4096

    def test_none_is_default(self) -> None:
        assert AgentLLMConfig().thinking_budget_tokens is None


class TestThinkingRequestShape:
    async def test_thinking_enabled_when_budget_set(self) -> None:
        client = _make_client(thinking_budget_tokens=4096)
        client._client.messages.create = AsyncMock(return_value=_mock_response())

        await client.complete([{"role": "user", "content": "hi"}])

        kwargs = client._client.messages.create.call_args.kwargs
        assert kwargs["thinking"] == {"type": "enabled", "budget_tokens": 4096}
        # Anthropic requires temperature=1.0 with extended thinking.
        assert kwargs["temperature"] == 1.0

    async def test_no_thinking_when_budget_none(self) -> None:
        client = _make_client(thinking_budget_tokens=None)
        client._client.messages.create = AsyncMock(return_value=_mock_response())

        await client.complete([{"role": "user", "content": "hi"}])

        kwargs = client._client.messages.create.call_args.kwargs
        assert "thinking" not in kwargs
        # Configured temperature is untouched when thinking is off.
        assert kwargs["temperature"] == 0.2


class TestFactoryWiring:
    def test_factory_passes_budget_to_client(self) -> None:
        cfg = AgentLLMConfig(
            provider="anthropic",
            model="claude-opus-4-6",
            max_tokens=8192,
            thinking_budget_tokens=2048,
            api_key_env="ANTHROPIC_API_KEY",
        )
        with patch("anthropic.AsyncAnthropic"):
            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
                client = LLMClientFactory.create(cfg)
        assert isinstance(client, AnthropicClient)
        assert client.thinking_budget_tokens == 2048


class TestDefaultsUnchanged:
    def test_every_default_agent_has_thinking_off(self) -> None:
        agents = AgentsLLMConfig()
        for name in (
            "planner",
            "planner_judge",
            "conflict_analyst",
            "executor",
            "judge",
            "human_interface",
            "memory_extractor",
        ):
            cfg = getattr(agents, name)
            assert cfg.thinking_budget_tokens is None, name
