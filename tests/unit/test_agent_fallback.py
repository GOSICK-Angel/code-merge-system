"""Verify the BaseAgent fallback hook and planner_judge surfacing fix.

Regression coverage for the bug where:
  1. ``BaseAgent._on_fallback_needed`` was a stub returning False, so a
     401/AUTH_PERMANENT primary error never actually switched to the
     configured fallback provider.
  2. ``PlannerJudgeAgent._call_judge_llm`` swallowed ``AgentError`` into a
     ``REVISION_NEEDED`` verdict; combined with the
     ``REVISION_NEEDED + 0 issues -> APPROVED`` upgrade in plan_review, this
     surfaced as "approved (0 issues)" in the UI even though the LLM call
     never succeeded.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.base_agent import AgentError, AgentExhaustedError, BaseAgent
from src.agents.planner_judge_agent import PlannerJudgeAgent
from src.llm.error_classifier import ClassifiedError, ErrorCategory, classify_error
from src.models.config import AgentLLMConfig
from src.models.message import AgentMessage, AgentType
from src.models.plan_judge import PlanJudgeResult
from src.models.state import MergeState


class _StubAgent(BaseAgent):
    agent_type = AgentType.PLANNER

    async def run(self, state: Any) -> AgentMessage:  # pragma: no cover
        raise NotImplementedError

    def can_handle(self, state: MergeState) -> bool:
        return True


class _HTTPError(Exception):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def _config_with_fallback() -> AgentLLMConfig:
    return AgentLLMConfig(
        provider="openai",
        model="gpt-5.4-mini",
        api_key_env="OPENAI_API_KEY",
        max_tokens=4096,
        max_retries=2,
        fallback=AgentLLMConfig(
            provider="anthropic",
            model="claude-opus-4-7",
            api_key_env="ANTHROPIC_API_KEY",
            max_tokens=4096,
            max_retries=2,
        ),
    )


def _make_stub_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[_StubAgent, MagicMock, MagicMock]:
    """Build a stub agent whose primary + fallback LLMs are MagicMocks.

    Patches ``LLMClientFactory.create`` so we don't need real API keys.
    Returns ``(agent, primary_llm, fallback_llm)``.
    """
    primary_llm = MagicMock(name="primary_llm")
    fallback_llm = MagicMock(name="fallback_llm")
    created: list[MagicMock] = [primary_llm, fallback_llm]

    def fake_create(_cfg: AgentLLMConfig) -> MagicMock:
        return created.pop(0)

    from src.agents import base_agent as base_agent_mod

    monkeypatch.setattr(
        base_agent_mod.LLMClientFactory, "create", staticmethod(fake_create)
    )

    agent = _StubAgent(_config_with_fallback())
    return agent, primary_llm, fallback_llm


class TestOnFallbackNeeded:
    def test_returns_false_when_no_fallback_configured(self):
        cfg = AgentLLMConfig(
            provider="anthropic",
            model="claude-sonnet-4-6",
            api_key_env="ANTHROPIC_API_KEY",
        )
        agent = _StubAgent.__new__(_StubAgent)
        agent.llm_config = cfg
        agent.llm = MagicMock()
        agent._fallback_llm = None
        agent._using_fallback = False
        agent.logger = MagicMock()

        classified = ClassifiedError(
            category=ErrorCategory.AUTH_PERMANENT,
            retryable=False,
            should_compress=False,
            should_rotate=True,
            should_fallback=True,
            cooldown_seconds=0,
            message="401",
        )
        assert agent._on_fallback_needed(classified) is False

    def test_swaps_to_fallback_when_configured(self, monkeypatch: pytest.MonkeyPatch):
        agent, primary, fallback = _make_stub_agent(monkeypatch)
        assert agent.llm is primary

        classified = ClassifiedError(
            category=ErrorCategory.AUTH_PERMANENT,
            retryable=False,
            should_compress=False,
            should_rotate=True,
            should_fallback=True,
            cooldown_seconds=0,
            message="401",
        )
        result = agent._on_fallback_needed(classified)
        assert result is True
        assert agent.llm is fallback
        assert agent._using_fallback is True
        assert agent.llm_config.provider == "anthropic"
        assert agent.llm_config.model == "claude-opus-4-7"
        assert agent.consecutive_failures == 0

    def test_returns_false_when_already_on_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        agent, _primary, _fallback = _make_stub_agent(monkeypatch)
        classified = ClassifiedError(
            category=ErrorCategory.AUTH_PERMANENT,
            retryable=False,
            should_compress=False,
            should_rotate=True,
            should_fallback=True,
            cooldown_seconds=0,
            message="401",
        )
        assert agent._on_fallback_needed(classified) is True
        # second call must short-circuit (no double swap)
        assert agent._on_fallback_needed(classified) is False


def _make_simple_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[_StubAgent, MagicMock]:
    """Stub agent with a single (no-fallback) MagicMock LLM."""
    llm = MagicMock(name="llm")

    from src.agents import base_agent as base_agent_mod

    monkeypatch.setattr(
        base_agent_mod.LLMClientFactory, "create", staticmethod(lambda _cfg: llm)
    )
    cfg = AgentLLMConfig(
        provider="openai",
        model="gpt-5.4-mini",
        api_key_env="OPENAI_API_KEY",
        max_tokens=4096,
        max_retries=1,
    )
    return _StubAgent(cfg), llm


class TestPerAgentActivityEmission:
    """BaseAgent emits per-agent start + terminal (complete/error) activity
    around each LLM call so the topology can show genuine live run state."""

    @pytest.mark.asyncio
    async def test_emits_start_and_complete_on_success(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        agent, llm = _make_simple_agent(monkeypatch)
        llm.complete = AsyncMock(return_value="ok")

        events: list[Any] = []
        agent.set_activity_callback(events.append)
        agent._current_phase = "auto_merge"

        result = await agent._call_llm_with_retry([{"role": "user", "content": "hi"}])
        assert result == "ok"

        pairs = [(e.agent, e.event_type) for e in events]
        assert ("planner", "start") in pairs
        assert ("planner", "complete") in pairs
        complete = [e for e in events if e.event_type == "complete"][-1]
        assert complete.elapsed is not None
        assert complete.target is None

    @pytest.mark.asyncio
    async def test_emits_error_on_nonretryable_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        agent, llm = _make_simple_agent(monkeypatch)
        llm.complete = AsyncMock(side_effect=_HTTPError("Invalid API key", 401))

        events: list[Any] = []
        agent.set_activity_callback(events.append)

        with pytest.raises(AgentError):
            await agent._call_llm_with_retry([{"role": "user", "content": "hi"}])

        pairs = [(e.agent, e.event_type) for e in events]
        assert ("planner", "start") in pairs
        assert ("planner", "error") in pairs


class TestRetryLoopSwapsOn401:
    """End-to-end: a 401 from the primary LLM should trigger fallback and
    return its response, instead of raising AgentError."""

    @pytest.mark.asyncio
    async def test_401_routes_to_fallback(self, monkeypatch: pytest.MonkeyPatch):
        agent, primary, fallback = _make_stub_agent(monkeypatch)
        # sanity: 401 must be classified as AUTH_PERMANENT + should_fallback
        cls = classify_error(_HTTPError("Invalid API key", 401), provider="openai")
        assert cls.category == ErrorCategory.AUTH_PERMANENT
        assert cls.should_fallback is True

        primary.complete = AsyncMock(side_effect=_HTTPError("Invalid API key", 401))
        fallback.complete = AsyncMock(return_value="ok-from-fallback")

        result = await agent._call_llm_with_retry([{"role": "user", "content": "ping"}])
        assert result == "ok-from-fallback"
        assert agent.llm is fallback
        assert agent._using_fallback is True
        primary.complete.assert_awaited_once()
        fallback.complete.assert_awaited_once()


class TestRetryLoopSwapsOnOverload:
    """A sustained 503/overload (retryable) from the primary must, after the
    primary's local retries are exhausted, route to the configured fallback
    provider instead of raising AgentExhaustedError.

    Regression for the planner_judge bug: a single-call agent hit by a 503 on
    every attempt never reached the immediate-category / sliding-window /
    circuit-breaker fallback triggers, so it failed straight to
    LLM_UNAVAILABLE even though a healthy fallback was configured.
    """

    def test_overload_marked_should_fallback(self):
        cls = classify_error(
            _HTTPError("Service temporarily unavailable", 503), provider="openai"
        )
        assert cls.category == ErrorCategory.OVERLOAD
        assert cls.retryable is True
        assert cls.should_fallback is True

    @pytest.mark.asyncio
    async def test_sustained_503_routes_to_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        agent, primary, fallback = _make_stub_agent(monkeypatch)

        primary.complete = AsyncMock(
            side_effect=_HTTPError("Service temporarily unavailable", 503)
        )
        fallback.complete = AsyncMock(return_value="ok-from-fallback")

        # max_retries=1: primary fails once → budget exhausted → switch to
        # fallback (no backoff sleep on the exhaustion path).
        result = await agent._call_llm_with_retry(
            [{"role": "user", "content": "ping"}], max_retries=1
        )

        assert result == "ok-from-fallback"
        assert agent.llm is fallback
        assert agent._using_fallback is True
        primary.complete.assert_awaited_once()
        fallback.complete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_both_providers_503_raises_after_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        agent, primary, fallback = _make_stub_agent(monkeypatch)

        primary.complete = AsyncMock(
            side_effect=_HTTPError("Service temporarily unavailable", 503)
        )
        fallback.complete = AsyncMock(
            side_effect=_HTTPError("Service temporarily unavailable", 503)
        )

        with pytest.raises(AgentExhaustedError):
            await agent._call_llm_with_retry(
                [{"role": "user", "content": "ping"}], max_retries=1
            )

        # fallback was actually attempted before giving up
        assert agent._using_fallback is True
        primary.complete.assert_awaited_once()
        fallback.complete.assert_awaited_once()


class TestPlannerJudgeSurfacesAgentError:
    """planner_judge must NOT silently downgrade AgentError to a verdict
    that the plan_review phase then upgrades to APPROVED."""

    @pytest.mark.asyncio
    async def test_agent_error_classified_as_llm_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        primary_llm = MagicMock(name="pj_primary")
        from src.agents import base_agent as base_agent_mod

        monkeypatch.setattr(
            base_agent_mod.LLMClientFactory,
            "create",
            staticmethod(lambda _cfg: primary_llm),
        )

        cfg = AgentLLMConfig(
            provider="openai",
            model="gpt-5.4-mini",
            api_key_env="OPENAI_API_KEY",
        )
        agent = PlannerJudgeAgent(cfg)

        classified = ClassifiedError(
            category=ErrorCategory.AUTH_PERMANENT,
            retryable=False,
            should_compress=False,
            should_rotate=True,
            should_fallback=True,
            cooldown_seconds=0,
            message="Authentication failed (openai): Invalid API key",
        )

        async def _boom(*_args: Any, **_kwargs: Any) -> str:
            raise AgentError(classified.message, classified)

        monkeypatch.setattr(agent, "_call_llm_with_retry", _boom)

        verdict, telemetry = await agent._call_judge_llm(
            prompt="dummy", system="dummy", revision_round=0
        )

        assert verdict.result == PlanJudgeResult.LLM_UNAVAILABLE
        assert "Plan Judge LLM unavailable" in verdict.summary
        # telemetry still populated so cost tracking doesn't crash
        assert telemetry["tokens_in"] is not None
        assert telemetry["tokens_out"] is None
