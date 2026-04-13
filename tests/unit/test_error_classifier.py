"""Tests for A4/A5: error classifier, jittered backoff, RetryBudget, and classified retry logic."""

from __future__ import annotations

import asyncio
import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.llm.error_classifier import (
    ClassifiedError,
    ErrorCategory,
    classify_error,
    _extract_retry_after,
)
from src.llm.retry_utils import jittered_backoff


# ============================================================================
# 1. ErrorCategory enum
# ============================================================================


class TestErrorCategory:
    def test_all_categories_exist(self):
        expected = {
            "auth_transient",
            "auth_permanent",
            "rate_limit",
            "overload",
            "context_overflow",
            "transport",
            "format",
            "unknown",
        }
        assert {c.value for c in ErrorCategory} == expected


# ============================================================================
# 2. ClassifiedError
# ============================================================================


class TestClassifiedError:
    def test_frozen(self):
        ce = ClassifiedError(
            category=ErrorCategory.RATE_LIMIT,
            retryable=True,
            should_compress=False,
            should_rotate=False,
            should_fallback=False,
            cooldown_seconds=30.0,
            message="rate limited",
        )
        with pytest.raises(AttributeError):
            ce.category = ErrorCategory.UNKNOWN  # type: ignore[misc]

    def test_is_fatal_false_when_retryable(self):
        ce = ClassifiedError(
            category=ErrorCategory.OVERLOAD,
            retryable=True,
            should_compress=False,
            should_rotate=False,
            should_fallback=False,
            cooldown_seconds=5,
            message="overloaded",
        )
        assert ce.is_fatal is False

    def test_is_fatal_true_when_not_retryable(self):
        ce = ClassifiedError(
            category=ErrorCategory.AUTH_PERMANENT,
            retryable=False,
            should_compress=False,
            should_rotate=True,
            should_fallback=True,
            cooldown_seconds=0,
            message="bad key",
        )
        assert ce.is_fatal is True


# ============================================================================
# 3. classify_error — HTTP status code classification
# ============================================================================


def _make_api_error(
    status_code: int,
    message: str = "error",
    headers: dict[str, str] | None = None,
) -> Exception:
    """Create a fake SDK-like exception with status_code and optional headers."""
    err = Exception(message)
    err.status_code = status_code  # type: ignore[attr-defined]
    if headers:
        resp = MagicMock()
        resp.headers = headers
        err.response = resp  # type: ignore[attr-defined]
    err.body = {"message": message}  # type: ignore[attr-defined]
    return err


class TestClassifyByStatusCode:
    def test_401_is_auth_permanent(self):
        result = classify_error(_make_api_error(401, "invalid key"), "anthropic")
        assert result.category == ErrorCategory.AUTH_PERMANENT
        assert result.retryable is False
        assert result.should_rotate is True
        assert result.should_fallback is True

    def test_403_permanent(self):
        result = classify_error(_make_api_error(403, "denied"), "openai")
        assert result.category == ErrorCategory.AUTH_PERMANENT
        assert result.retryable is False

    def test_403_transient_quota(self):
        result = classify_error(
            _make_api_error(403, "quota exceeded temporarily"), "anthropic"
        )
        assert result.category == ErrorCategory.AUTH_TRANSIENT
        assert result.retryable is True
        assert result.should_rotate is True

    def test_429_is_rate_limit(self):
        result = classify_error(
            _make_api_error(429, "too many requests", {"retry-after": "15"}),
            "openai",
        )
        assert result.category == ErrorCategory.RATE_LIMIT
        assert result.retryable is True
        assert result.cooldown_seconds == 15.0

    def test_429_default_cooldown(self):
        result = classify_error(_make_api_error(429, "rate limited"), "anthropic")
        assert result.category == ErrorCategory.RATE_LIMIT
        assert result.cooldown_seconds == 30.0

    def test_400_context_overflow(self):
        result = classify_error(
            _make_api_error(400, "maximum context length exceeded"), "openai"
        )
        assert result.category == ErrorCategory.CONTEXT_OVERFLOW
        assert result.retryable is True
        assert result.should_compress is True

    def test_400_generic_is_format(self):
        result = classify_error(_make_api_error(400, "invalid json"), "openai")
        assert result.category == ErrorCategory.FORMAT
        assert result.retryable is False

    def test_500_is_overload(self):
        result = classify_error(_make_api_error(500, "internal error"), "anthropic")
        assert result.category == ErrorCategory.OVERLOAD
        assert result.retryable is True

    def test_503_overloaded(self):
        result = classify_error(_make_api_error(503, "overloaded"), "anthropic")
        assert result.category == ErrorCategory.OVERLOAD
        assert result.retryable is True
        assert result.cooldown_seconds == 5

    def test_529_server_error(self):
        result = classify_error(_make_api_error(529, "server error"), "anthropic")
        assert result.category == ErrorCategory.OVERLOAD
        assert result.retryable is True


class TestClassifyByExceptionType:
    def test_connection_error(self):
        result = classify_error(ConnectionError("refused"), "openai")
        assert result.category == ErrorCategory.TRANSPORT
        assert result.retryable is True

    def test_timeout_error(self):
        result = classify_error(TimeoutError("timed out"), "anthropic")
        assert result.category == ErrorCategory.TRANSPORT
        assert result.retryable is True

    def test_os_error_is_transport(self):
        result = classify_error(OSError("network unreachable"), "openai")
        assert result.category == ErrorCategory.TRANSPORT

    def test_parse_error(self):
        from src.llm.client import ParseError

        result = classify_error(ParseError("bad json"), "openai")
        assert result.category == ErrorCategory.FORMAT
        assert result.retryable is True

    def test_unknown_error(self):
        result = classify_error(ValueError("unexpected"), "anthropic")
        assert result.category == ErrorCategory.UNKNOWN
        assert result.retryable is True
        assert result.cooldown_seconds == 1


class TestClassifyContextOverflowByMessage:
    @pytest.mark.parametrize(
        "msg",
        [
            "maximum context length exceeded",
            "This model's context window is 200000 tokens",
            "too many tokens: 250000 > 200000",
            "prompt is too long",
            "input is too large for this model",
        ],
    )
    def test_overflow_patterns(self, msg: str):
        err = Exception(msg)
        result = classify_error(err, "openai")
        assert result.category == ErrorCategory.CONTEXT_OVERFLOW
        assert result.should_compress is True


# ============================================================================
# 4. _extract_retry_after
# ============================================================================


class TestExtractRetryAfter:
    def test_from_response_headers(self):
        err = _make_api_error(429, "rate limited", {"retry-after": "42"})
        assert _extract_retry_after(err) == 42.0

    def test_minimum_1_second(self):
        err = _make_api_error(429, "rate limited", {"retry-after": "0.3"})
        assert _extract_retry_after(err) == 1.0

    def test_missing_header_returns_default(self):
        err = _make_api_error(429, "rate limited")
        assert _extract_retry_after(err) == 30.0


# ============================================================================
# 5. jittered_backoff
# ============================================================================


class TestJitteredBackoff:
    def test_returns_positive_float(self):
        delay = jittered_backoff(0, base=1.0, max_delay=60.0)
        assert delay > 0

    def test_exponential_growth_capped(self):
        delay0 = jittered_backoff(0, base=1.0, max_delay=10.0)
        delay5 = jittered_backoff(5, base=1.0, max_delay=10.0)
        assert delay0 <= 1.5 + 1.0
        assert delay5 <= 10.0 + 5.0 + 1.0

    def test_max_delay_bounds(self):
        for attempt in range(20):
            delay = jittered_backoff(attempt, base=1.0, max_delay=10.0)
            assert delay <= 10.0 * 1.5 + 1.0

    def test_decorrelated_across_calls(self):
        delays = [jittered_backoff(0, base=1.0) for _ in range(50)]
        unique = set(round(d, 6) for d in delays)
        assert len(unique) > 1, "Expected jitter to produce different delays"

    def test_zero_base(self):
        delay = jittered_backoff(0, base=0.0, max_delay=60.0)
        assert delay == 0.0


# ============================================================================
# 5b. RetryBudget
# ============================================================================


class TestRetryBudget:
    def test_initial_state(self):
        from src.agents.base_agent import RetryBudget

        rb = RetryBudget(max_retries=3)
        assert rb.attempt == 0
        assert rb.rate_limit_waits == 0
        assert not rb.retries_exhausted
        assert not rb.rate_limit_exhausted
        assert rb.category_counts == {}

    def test_consume_attempt(self):
        from src.agents.base_agent import RetryBudget

        rb = RetryBudget(max_retries=2)
        rb.consume_attempt()
        assert rb.attempt == 1
        assert not rb.retries_exhausted
        rb.consume_attempt()
        assert rb.attempt == 2
        assert rb.retries_exhausted

    def test_consume_rate_limit_wait(self):
        from src.agents.base_agent import RetryBudget

        rb = RetryBudget(max_retries=3, max_rate_limit_waits=2)
        rb.consume_rate_limit_wait()
        assert rb.rate_limit_waits == 1
        assert not rb.rate_limit_exhausted
        rb.consume_rate_limit_wait()
        assert rb.rate_limit_waits == 2
        assert rb.rate_limit_exhausted

    def test_record_category_counts(self):
        from src.agents.base_agent import RetryBudget

        rb = RetryBudget(max_retries=5)
        rb.record(ErrorCategory.OVERLOAD)
        rb.record(ErrorCategory.OVERLOAD)
        rb.record(ErrorCategory.RATE_LIMIT)
        assert rb.category_counts == {"overload": 2, "rate_limit": 1}

    def test_rate_limit_does_not_affect_attempts(self):
        from src.agents.base_agent import RetryBudget

        rb = RetryBudget(max_retries=2, max_rate_limit_waits=10)
        for _ in range(10):
            rb.consume_rate_limit_wait()
        assert rb.attempt == 0
        assert not rb.retries_exhausted


# ============================================================================
# 6. BaseAgent retry integration
# ============================================================================


def _make_test_agent(max_retries: int = 3) -> Any:
    from src.agents.base_agent import BaseAgent
    from src.models.config import AgentLLMConfig
    from src.models.message import AgentType

    config = AgentLLMConfig(
        provider="openai",
        model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
        max_retries=max_retries,
    )

    class _TestAgent(BaseAgent):
        agent_type = AgentType.PLANNER

        async def run(self, state: Any) -> Any:
            return None

        def can_handle(self, state: Any) -> bool:
            return True

    with patch("src.agents.base_agent.LLMClientFactory") as factory:
        factory.create.return_value = MagicMock()
        agent = _TestAgent(config)
    return agent


class TestBaseAgentRetryIntegration:
    @pytest.mark.asyncio
    async def test_non_retryable_error_raises_agent_error(self):
        from src.agents.base_agent import AgentError

        agent = _make_test_agent(max_retries=3)
        err = _make_api_error(401, "invalid key")
        agent.llm.complete = AsyncMock(side_effect=err)

        with pytest.raises(AgentError) as exc_info:
            await agent._call_llm_with_retry([{"role": "user", "content": "hi"}])
        assert exc_info.value.classification.category == ErrorCategory.AUTH_PERMANENT
        assert agent.consecutive_failures == 1

    @pytest.mark.asyncio
    async def test_retryable_error_exhausts_then_raises(self):
        from src.agents.base_agent import AgentExhaustedError

        agent = _make_test_agent(max_retries=2)
        err = _make_api_error(500, "server error")
        agent.llm.complete = AsyncMock(side_effect=err)

        with patch("src.agents.base_agent.jittered_backoff", return_value=0.0):
            with pytest.raises(AgentExhaustedError) as exc_info:
                await agent._call_llm_with_retry([{"role": "user", "content": "hi"}])
        assert exc_info.value.last_classification is not None
        assert exc_info.value.last_classification.category == ErrorCategory.OVERLOAD
        assert agent.consecutive_failures == 1

    @pytest.mark.asyncio
    async def test_context_overflow_triggers_compression(self):
        agent = _make_test_agent(max_retries=2)
        overflow_err = _make_api_error(400, "maximum context length exceeded")
        agent.llm.complete = AsyncMock(side_effect=[overflow_err, "success"])

        with patch("src.agents.base_agent.jittered_backoff", return_value=0.0):
            result = await agent._call_llm_with_retry(
                [{"role": "user", "content": "x" * 10000}]
            )
        assert result == "success"
        assert agent.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_rate_limit_uses_cooldown(self):
        agent = _make_test_agent(max_retries=2)
        rate_err = _make_api_error(429, "rate limited")
        agent.llm.complete = AsyncMock(side_effect=[rate_err, "ok"])

        backoff_calls: list[tuple[int, float]] = []

        def tracking_backoff(
            attempt: int, base: float = 1.0, max_delay: float = 60.0
        ) -> float:
            backoff_calls.append((attempt, base))
            return 0.0

        with patch(
            "src.agents.base_agent.jittered_backoff", side_effect=tracking_backoff
        ):
            result = await agent._call_llm_with_retry(
                [{"role": "user", "content": "hi"}]
            )

        assert result == "ok"
        assert len(backoff_calls) == 1
        assert backoff_calls[0][1] == 30.0

    @pytest.mark.asyncio
    async def test_success_resets_failure_counter(self):
        agent = _make_test_agent()
        agent._consecutive_failures = 2
        agent.llm.complete = AsyncMock(return_value="ok")

        result = await agent._call_llm_with_retry([{"role": "user", "content": "hi"}])
        assert result == "ok"
        assert agent.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_trace_logger_records_classification(self):
        agent = _make_test_agent(max_retries=2)
        agent._trace_logger = MagicMock()
        err = _make_api_error(500, "server error")
        agent.llm.complete = AsyncMock(side_effect=err)

        with patch("src.agents.base_agent.jittered_backoff", return_value=0.0):
            with pytest.raises(Exception):
                await agent._call_llm_with_retry([{"role": "user", "content": "hi"}])

        calls = agent._trace_logger.record.call_args_list
        assert len(calls) == 2
        for call in calls:
            error_str = call.kwargs.get("error", "")
            assert "[overload]" in error_str


# ============================================================================
# 6b. A5: Rate-limit does not consume retry budget
# ============================================================================


class TestRateLimitDoesNotConsumeRetryBudget:
    """Rate-limit (429) errors should use a separate wait counter so
    they don't burn normal retry attempts (spec §4.4)."""

    @pytest.mark.asyncio
    async def test_rate_limits_preserve_retry_budget(self):
        """3 consecutive 429s + 1 success should succeed with max_retries=1,
        because rate-limit waits don't count against the retry counter."""
        agent = _make_test_agent(max_retries=1)
        rate_err = _make_api_error(429, "rate limited")
        agent.llm.complete = AsyncMock(side_effect=[rate_err, rate_err, rate_err, "ok"])

        with patch("src.agents.base_agent.jittered_backoff", return_value=0.0):
            result = await agent._call_llm_with_retry(
                [{"role": "user", "content": "hi"}]
            )
        assert result == "ok"
        assert agent.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_rate_limit_exhaustion_raises(self):
        """When MAX_RATE_LIMIT_WAITS is exceeded, the call should fail."""
        from src.agents.base_agent import AgentExhaustedError, MAX_RATE_LIMIT_WAITS

        agent = _make_test_agent(max_retries=3)
        rate_err = _make_api_error(429, "rate limited")
        agent.llm.complete = AsyncMock(
            side_effect=[rate_err] * (MAX_RATE_LIMIT_WAITS + 1)
        )

        with patch("src.agents.base_agent.jittered_backoff", return_value=0.0):
            with pytest.raises(AgentExhaustedError) as exc_info:
                await agent._call_llm_with_retry([{"role": "user", "content": "hi"}])
        assert exc_info.value.last_classification is not None
        assert exc_info.value.last_classification.category == ErrorCategory.RATE_LIMIT

    @pytest.mark.asyncio
    async def test_mixed_rate_limit_and_overload(self):
        """Rate-limit waits and normal retries are tracked independently."""
        agent = _make_test_agent(max_retries=2)
        rate_err = _make_api_error(429, "rate limited")
        overload_err = _make_api_error(500, "server error")
        agent.llm.complete = AsyncMock(
            side_effect=[rate_err, rate_err, overload_err, "ok"]
        )

        with patch("src.agents.base_agent.jittered_backoff", return_value=0.0):
            result = await agent._call_llm_with_retry(
                [{"role": "user", "content": "hi"}]
            )
        assert result == "ok"


# ============================================================================
# 6c. A5: Category-aware circuit breaker
# ============================================================================


class TestCategoryAwareCircuitBreaker:
    """Circuit breaker should only trip on permanent/systemic failures,
    not on transient transport or rate-limit errors."""

    @pytest.mark.asyncio
    async def test_auth_permanent_increments_circuit_breaker(self):
        from src.agents.base_agent import AgentError

        agent = _make_test_agent(max_retries=3)
        err = _make_api_error(401, "invalid key")
        agent.llm.complete = AsyncMock(side_effect=err)

        with pytest.raises(AgentError):
            await agent._call_llm_with_retry([{"role": "user", "content": "hi"}])
        assert agent.consecutive_failures == 1

    @pytest.mark.asyncio
    async def test_format_error_increments_circuit_breaker(self):
        from src.agents.base_agent import AgentError

        agent = _make_test_agent(max_retries=3)
        err = _make_api_error(400, "invalid json body")
        agent.llm.complete = AsyncMock(side_effect=err)

        with pytest.raises(AgentError):
            await agent._call_llm_with_retry([{"role": "user", "content": "hi"}])
        assert agent.consecutive_failures == 1

    @pytest.mark.asyncio
    async def test_transport_exhaustion_does_not_trip_circuit_breaker(self):
        from src.agents.base_agent import AgentExhaustedError

        agent = _make_test_agent(max_retries=2)
        agent.llm.complete = AsyncMock(side_effect=ConnectionError("refused"))

        with patch("src.agents.base_agent.jittered_backoff", return_value=0.0):
            with pytest.raises(AgentExhaustedError):
                await agent._call_llm_with_retry([{"role": "user", "content": "hi"}])
        assert agent.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_rate_limit_exhaustion_does_not_trip_circuit_breaker(self):
        from src.agents.base_agent import AgentExhaustedError, MAX_RATE_LIMIT_WAITS

        agent = _make_test_agent(max_retries=3)
        rate_err = _make_api_error(429, "rate limited")
        agent.llm.complete = AsyncMock(
            side_effect=[rate_err] * (MAX_RATE_LIMIT_WAITS + 1)
        )

        with patch("src.agents.base_agent.jittered_backoff", return_value=0.0):
            with pytest.raises(AgentExhaustedError):
                await agent._call_llm_with_retry([{"role": "user", "content": "hi"}])
        assert agent.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_overload_exhaustion_trips_circuit_breaker(self):
        from src.agents.base_agent import AgentExhaustedError

        agent = _make_test_agent(max_retries=2)
        err = _make_api_error(500, "internal server error")
        agent.llm.complete = AsyncMock(side_effect=err)

        with patch("src.agents.base_agent.jittered_backoff", return_value=0.0):
            with pytest.raises(AgentExhaustedError):
                await agent._call_llm_with_retry([{"role": "user", "content": "hi"}])
        assert agent.consecutive_failures == 1

    @pytest.mark.asyncio
    async def test_circuit_breaker_blocks_after_threshold(self):
        from src.agents.base_agent import (
            AgentError,
            CircuitBreakerOpen,
            CIRCUIT_BREAKER_THRESHOLD,
        )

        agent = _make_test_agent(max_retries=3)
        agent._consecutive_failures = CIRCUIT_BREAKER_THRESHOLD

        with pytest.raises(CircuitBreakerOpen):
            await agent._call_llm_with_retry([{"role": "user", "content": "hi"}])


# ============================================================================
# 6d. A5: Credential rotation / provider fallback hooks
# ============================================================================


class TestCredentialRotationAndFallbackHooks:
    @pytest.mark.asyncio
    async def test_rotation_hook_called_on_transient_auth(self):
        agent = _make_test_agent(max_retries=2)
        auth_err = _make_api_error(403, "quota exceeded temporarily")
        agent.llm.complete = AsyncMock(side_effect=[auth_err, "ok"])
        agent._on_credential_rotation_needed = MagicMock(return_value=False)

        with patch("src.agents.base_agent.jittered_backoff", return_value=0.0):
            result = await agent._call_llm_with_retry(
                [{"role": "user", "content": "hi"}]
            )
        assert result == "ok"
        agent._on_credential_rotation_needed.assert_called_once()
        classified_arg = agent._on_credential_rotation_needed.call_args[0][0]
        assert classified_arg.should_rotate is True

    @pytest.mark.asyncio
    async def test_fallback_hook_called_on_permanent_auth(self):
        from src.agents.base_agent import AgentError

        agent = _make_test_agent(max_retries=3)
        err = _make_api_error(401, "invalid key")
        agent.llm.complete = AsyncMock(side_effect=err)
        agent._on_fallback_needed = MagicMock(return_value=False)

        with pytest.raises(AgentError):
            await agent._call_llm_with_retry([{"role": "user", "content": "hi"}])
        agent._on_fallback_needed.assert_called_once()

    @pytest.mark.asyncio
    async def test_fallback_hook_success_allows_retry(self):
        agent = _make_test_agent(max_retries=3)
        err = _make_api_error(401, "invalid key")
        agent.llm.complete = AsyncMock(side_effect=[err, "recovered"])
        agent._on_fallback_needed = MagicMock(return_value=True)

        result = await agent._call_llm_with_retry([{"role": "user", "content": "hi"}])
        assert result == "recovered"
        agent._on_fallback_needed.assert_called_once()

    @pytest.mark.asyncio
    async def test_rotation_not_called_when_should_rotate_false(self):
        from src.agents.base_agent import AgentExhaustedError

        agent = _make_test_agent(max_retries=2)
        err = _make_api_error(500, "server error")
        agent.llm.complete = AsyncMock(side_effect=err)
        agent._on_credential_rotation_needed = MagicMock(return_value=False)

        with patch("src.agents.base_agent.jittered_backoff", return_value=0.0):
            with pytest.raises(AgentExhaustedError):
                await agent._call_llm_with_retry([{"role": "user", "content": "hi"}])
        agent._on_credential_rotation_needed.assert_not_called()


# ============================================================================
# 6e. A5: Exhausted error message includes retry budget details
# ============================================================================


class TestExhaustedErrorDetails:
    @pytest.mark.asyncio
    async def test_error_message_includes_rate_limit_waits(self):
        from src.agents.base_agent import AgentExhaustedError, MAX_RATE_LIMIT_WAITS

        agent = _make_test_agent(max_retries=1)
        rate_err = _make_api_error(429, "rate limited")
        overload_err = _make_api_error(500, "server error")
        agent.llm.complete = AsyncMock(side_effect=[rate_err, rate_err, overload_err])

        with patch("src.agents.base_agent.jittered_backoff", return_value=0.0):
            with pytest.raises(AgentExhaustedError) as exc_info:
                await agent._call_llm_with_retry([{"role": "user", "content": "hi"}])
        msg = str(exc_info.value)
        assert "1 attempts" in msg
        assert "2 rate-limit waits" in msg


# ============================================================================
# 7. MessageBus error logging
# ============================================================================


class TestMessageBusErrorLogging:
    def test_subscriber_error_is_logged(self):
        import logging
        from src.core.message_bus import MessageBus
        from src.models.message import AgentMessage, AgentType, MessageType
        from src.models.plan import MergePhase

        bus = MessageBus()

        def bad_callback(msg: AgentMessage) -> None:
            raise ValueError("subscriber boom")

        bus.subscribe(AgentType.PLANNER, bad_callback)

        msg = AgentMessage(
            sender=AgentType.ORCHESTRATOR,
            receiver=AgentType.PLANNER,
            phase=MergePhase.PLAN_REVIEW,
            message_type=MessageType.PHASE_COMPLETED,
            subject="test",
            payload={},
        )

        with patch("src.core.message_bus.logger") as mock_logger:
            bus.publish(msg)
            mock_logger.warning.assert_called_once()
            call_args = mock_logger.warning.call_args
            assert "Subscriber callback error" in call_args[0][0]
