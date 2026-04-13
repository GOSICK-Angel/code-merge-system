import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
import asyncio
from pydantic import BaseModel
from src.models.config import AgentLLMConfig
from src.models.message import AgentType, AgentMessage
from src.models.state import MergeState
from src.llm.client import LLMClient, LLMClientFactory
from src.llm.context import (
    TokenBudget,
    estimate_tokens,
    get_context_window,
)
from src.llm.context_compressor import ContextCompressor
from src.llm.error_classifier import ClassifiedError, ErrorCategory, classify_error
from src.llm.credential_pool import CredentialPool
from src.llm.retry_utils import jittered_backoff
from src.memory.layered_loader import LayeredMemoryLoader
from src.memory.store import MemoryStore
from src.tools.cost_tracker import CostTracker, TokenUsage
from src.tools.trace_logger import TraceLogger

CIRCUIT_BREAKER_THRESHOLD = 3

_CIRCUIT_BREAKER_CATEGORIES: frozenset[ErrorCategory] = frozenset(
    {
        ErrorCategory.AUTH_PERMANENT,
        ErrorCategory.FORMAT,
    }
)

MAX_RATE_LIMIT_WAITS = 5


class CircuitBreakerOpen(RuntimeError):
    """Raised when the circuit breaker trips after too many consecutive failures."""


class AgentError(RuntimeError):
    """Non-retryable LLM error with classification details."""

    def __init__(self, message: str, classification: ClassifiedError) -> None:
        super().__init__(message)
        self.classification = classification


class AgentExhaustedError(RuntimeError):
    """Raised when all retry attempts are exhausted."""

    def __init__(
        self, message: str, last_classification: ClassifiedError | None = None
    ) -> None:
        super().__init__(message)
        self.last_classification = last_classification


@dataclass
class RetryBudget:
    """Tracks retry state across error categories within a single LLM call."""

    max_retries: int
    max_rate_limit_waits: int = MAX_RATE_LIMIT_WAITS
    attempt: int = 0
    rate_limit_waits: int = 0
    category_counts: dict[str, int] = field(default_factory=dict)

    @property
    def retries_exhausted(self) -> bool:
        return self.attempt >= self.max_retries

    @property
    def rate_limit_exhausted(self) -> bool:
        return self.rate_limit_waits >= self.max_rate_limit_waits

    def record(self, category: ErrorCategory) -> None:
        key = category.value
        self.category_counts[key] = self.category_counts.get(key, 0) + 1

    def consume_attempt(self) -> None:
        self.attempt += 1

    def consume_rate_limit_wait(self) -> None:
        self.rate_limit_waits += 1


class BaseAgent(ABC):
    agent_type: AgentType

    def __init__(self, llm_config: AgentLLMConfig):
        self.llm_config = llm_config
        self.llm: LLMClient = LLMClientFactory.create(llm_config)
        self.logger = logging.getLogger(f"agent.{self.agent_type.value}")
        self._trace_logger: TraceLogger | None = None
        self._memory_store: MemoryStore | None = None
        self._consecutive_failures: int = 0
        self._credential_pool: CredentialPool | None = self._init_credential_pool()
        self._cost_tracker: CostTracker | None = None
        self._current_phase: str = ""

    def set_trace_logger(self, trace_logger: TraceLogger) -> None:
        self._trace_logger = trace_logger

    def set_memory_store(self, store: MemoryStore) -> None:
        self._memory_store = store

    def set_cost_tracker(self, tracker: CostTracker, phase: str = "") -> None:
        self._cost_tracker = tracker
        self._current_phase = phase

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def reset_circuit_breaker(self) -> None:
        self._consecutive_failures = 0

    def get_memory_context(
        self,
        current_phase: str,
        file_paths: list[str] | None = None,
    ) -> str:
        if self._memory_store is None:
            return ""
        loader = LayeredMemoryLoader(self._memory_store)
        return loader.load_for_agent(current_phase, file_paths)

    def _get_token_budget(self) -> TokenBudget:
        return TokenBudget(
            model=self.llm_config.model,
            context_window=get_context_window(self.llm_config.model),
            reserved_for_output=self.llm_config.max_tokens,
        )

    @abstractmethod
    async def run(self, state: Any) -> AgentMessage:
        pass

    @abstractmethod
    def can_handle(self, state: MergeState) -> bool:
        pass

    def _init_credential_pool(self) -> CredentialPool | None:
        """Build credential pool from config (C2).

        Only creates a pool when multiple keys are configured.
        """
        env_vars = self.llm_config.api_key_env_list
        if len(env_vars) <= 1:
            return None
        pool = CredentialPool.from_env_vars(env_vars)
        if pool.size <= 1:
            return None
        self.logger.info(
            "Credential pool initialized with %d keys for %s",
            pool.size,
            self.agent_type.value,
        )
        return pool

    def _on_credential_rotation_needed(self, classified: ClassifiedError) -> bool:
        """Rotate to next available credential in the pool (C2).

        Returns True if rotation succeeded and the call should be retried.
        """
        if self._credential_pool is None:
            self.logger.warning(
                "Credential rotation requested but no credential pool configured "
                "(category=%s)",
                classified.category.value,
            )
            return False

        try:
            cooldown_secs = max(30, int(classified.cooldown_seconds))
            current = self._credential_pool.get_active()
            self._credential_pool.cooldown(current, seconds=cooldown_secs)
            next_cred = self._credential_pool.get_active()
            self.llm.update_api_key(next_cred.key)
            self.logger.info(
                "Rotated credential to %s (pool: %d/%d available)",
                next_cred.source,
                self._credential_pool.available_count,
                self._credential_pool.size,
            )
            return True
        except Exception as exc:
            self.logger.warning("Credential rotation failed: %s", exc)
            return False

    def _on_fallback_needed(self, classified: ClassifiedError) -> bool:
        """Hook for provider fallback (extension point for C2 credential pool).

        Subclasses or future multi-provider support can override this to
        switch to a different LLM provider on permanent auth failures.
        Returns True if fallback succeeded.
        """
        self.logger.warning(
            "Provider fallback requested but no fallback provider configured "
            "(category=%s)",
            classified.category.value,
        )
        return False

    def _mitigate_context_pressure(
        self,
        messages: list[dict[str, Any]],
        budget: TokenBudget,
    ) -> list[dict[str, Any]]:
        """Three-stage context compression (B2).

        Delegates to :class:`ContextCompressor` which applies:
        1. Zero-cost stale output pruning
        2. Boundary-aware middle truncation
        3. Middle message dropping (last resort)
        """
        comp_cfg = self.llm_config.compression
        compressor = ContextCompressor(
            budget,
            protect_head=1,
            protect_tail=max(1, int(comp_cfg.protect_tail_tokens / 500)),
            stale_char_threshold=comp_cfg.stale_output_threshold,
        )
        result, stats = compressor.compress(messages)
        if stats.total_saved > 0:
            self.logger.info(
                "Context compressed: %d→%d tokens (P1=%d, P2=%d, P3=%d saved)",
                stats.tokens_before,
                stats.tokens_after,
                stats.phase1_saved,
                stats.phase2_saved,
                stats.phase3_saved,
            )
        return result

    async def _call_llm_with_retry(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        schema: type[BaseModel] | None = None,
        max_retries: int | None = None,
    ) -> str | BaseModel:
        if self._consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
            self.logger.error(
                "Circuit breaker OPEN: %d consecutive failures for %s, refusing call",
                self._consecutive_failures,
                self.agent_type.value,
            )
            raise CircuitBreakerOpen(
                f"Agent {self.agent_type.value} circuit breaker open after "
                f"{self._consecutive_failures} consecutive failures"
            )

        retries = (
            max_retries if max_retries is not None else self.llm_config.max_retries
        )
        budget = self._get_token_budget()
        estimated_tokens = estimate_tokens(
            "".join(m.get("content", "") for m in messages)
        )

        if not budget.can_fit(estimated_tokens):
            self.logger.warning(
                "Prompt (%d est. tokens) exceeds budget (%d available) for %s — attempting mitigation",
                estimated_tokens,
                budget.available,
                self.llm_config.model,
            )
            messages = self._mitigate_context_pressure(messages, budget)
            estimated_tokens = estimate_tokens(
                "".join(m.get("content", "") for m in messages)
            )

        prompt_chars = sum(len(m.get("content", "")) for m in messages)
        prompt_preview = messages[-1].get("content", "")[:300] if messages else ""
        utilization = (
            estimated_tokens / budget.context_window if budget.context_window else 0.0
        )

        self.logger.info(
            "LLM call: model=%s, provider=%s, prompt_chars=%d, est_tokens=%d, "
            "max_tokens=%d, utilization=%.1f%%",
            self.llm_config.model,
            self.llm_config.provider,
            prompt_chars,
            estimated_tokens,
            self.llm_config.max_tokens,
            utilization * 100,
        )

        retry_budget = RetryBudget(max_retries=retries)
        last_error: Exception | None = None
        last_classified: ClassifiedError | None = None

        while True:
            if retry_budget.retries_exhausted:
                break

            t0 = time.monotonic()
            try:
                llm_result: str | BaseModel
                if schema is not None:
                    llm_result = await self.llm.complete_structured(
                        messages, schema, system=system
                    )
                else:
                    llm_result = await self.llm.complete(messages, system=system)
                elapsed = time.monotonic() - t0
                resp_str = str(llm_result)
                resp_len = len(resp_str)
                self.logger.info(
                    "LLM response: attempt=%d/%d, elapsed=%.1fs, response_chars=%d",
                    retry_budget.attempt + 1,
                    retries,
                    elapsed,
                    resp_len,
                )
                self._consecutive_failures = 0
                if self._trace_logger:
                    self._trace_logger.record(
                        agent=self.agent_type.value,
                        model=self.llm_config.model,
                        provider=self.llm_config.provider,
                        prompt_chars=prompt_chars,
                        response_chars=resp_len,
                        elapsed_seconds=elapsed,
                        attempt=retry_budget.attempt + 1,
                        max_attempts=retries,
                        success=True,
                        prompt_preview=prompt_preview,
                        response_preview=resp_str[:300],
                        estimated_tokens=estimated_tokens,
                        budget_available=budget.available,
                        utilization=round(utilization, 4),
                    )
                if self._cost_tracker:
                    output_tokens = estimate_tokens(resp_str)
                    self._cost_tracker.record(
                        agent=self.agent_type.value,
                        phase=self._current_phase,
                        model=self.llm_config.model,
                        provider=self.llm_config.provider,
                        usage=TokenUsage(
                            input_tokens=estimated_tokens,
                            output_tokens=output_tokens,
                        ),
                        elapsed_seconds=elapsed,
                    )
                return llm_result
            except Exception as e:
                last_error = e
                elapsed = time.monotonic() - t0
                classified = classify_error(e, self.llm_config.provider)
                last_classified = classified
                retry_budget.record(classified.category)

                self.logger.warning(
                    "LLM error (%.1fs) [%s]: %s",
                    elapsed,
                    classified.category.value,
                    classified.message,
                )
                if self._trace_logger:
                    self._trace_logger.record(
                        agent=self.agent_type.value,
                        model=self.llm_config.model,
                        provider=self.llm_config.provider,
                        prompt_chars=prompt_chars,
                        response_chars=0,
                        elapsed_seconds=elapsed,
                        attempt=retry_budget.attempt + 1,
                        max_attempts=retries,
                        success=False,
                        error=f"[{classified.category.value}] {str(e)[:180]}",
                        prompt_preview=prompt_preview,
                        estimated_tokens=estimated_tokens,
                        budget_available=budget.available,
                        utilization=round(utilization, 4),
                    )

                if not classified.retryable:
                    if classified.should_fallback:
                        if self._on_fallback_needed(classified):
                            retry_budget.consume_attempt()
                            continue
                    if classified.category in _CIRCUIT_BREAKER_CATEGORIES:
                        self._consecutive_failures += 1
                    raise AgentError(classified.message, classified) from e

                if classified.should_rotate:
                    self._on_credential_rotation_needed(classified)

                if classified.should_compress:
                    self.logger.info(
                        "Context overflow detected — compressing messages before retry"
                    )
                    messages = self._mitigate_context_pressure(messages, budget)
                    estimated_tokens = estimate_tokens(
                        "".join(m.get("content", "") for m in messages)
                    )
                    prompt_chars = sum(len(m.get("content", "")) for m in messages)

                is_rate_limit = classified.category == ErrorCategory.RATE_LIMIT
                if is_rate_limit:
                    retry_budget.consume_rate_limit_wait()
                    if retry_budget.rate_limit_exhausted:
                        self.logger.error(
                            "Rate limit wait budget exhausted (%d waits) for %s",
                            retry_budget.rate_limit_waits,
                            self.agent_type.value,
                        )
                        break
                else:
                    retry_budget.consume_attempt()

                if not retry_budget.retries_exhausted:
                    delay = jittered_backoff(
                        retry_budget.attempt
                        if not is_rate_limit
                        else retry_budget.rate_limit_waits,
                        base=max(1.0, classified.cooldown_seconds),
                    )
                    self.logger.debug(
                        "Backing off %.1fs before retry (category=%s, attempt=%d, rl_waits=%d)",
                        delay,
                        classified.category.value,
                        retry_budget.attempt,
                        retry_budget.rate_limit_waits,
                    )
                    await asyncio.sleep(delay)

        if last_classified and last_classified.category in _CIRCUIT_BREAKER_CATEGORIES:
            self._consecutive_failures += 1
        elif last_classified and last_classified.category not in (
            ErrorCategory.RATE_LIMIT,
            ErrorCategory.TRANSPORT,
        ):
            self._consecutive_failures += 1
        raise AgentExhaustedError(
            f"Agent {self.agent_type.value}: LLM call failed after "
            f"{retry_budget.attempt} attempts "
            f"(+{retry_budget.rate_limit_waits} rate-limit waits): {last_error}",
            last_classified,
        ) from last_error
