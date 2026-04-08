import logging
import time
from abc import ABC, abstractmethod
from typing import Any
import asyncio
from pydantic import BaseModel
from src.models.config import AgentLLMConfig
from src.models.message import AgentType, AgentMessage
from src.models.state import MergeState
from src.llm.client import LLMClient, LLMClientFactory, ParseError
from src.llm.context import (
    TokenBudget,
    _CHARS_PER_TOKEN,
    estimate_tokens,
    get_context_window,
)
from src.memory.store import MemoryStore
from src.tools.trace_logger import TraceLogger

CIRCUIT_BREAKER_THRESHOLD = 3


class CircuitBreakerOpen(RuntimeError):
    """Raised when the circuit breaker trips after too many consecutive failures."""


class BaseAgent(ABC):
    agent_type: AgentType

    def __init__(self, llm_config: AgentLLMConfig):
        self.llm_config = llm_config
        self.llm: LLMClient = LLMClientFactory.create(llm_config)
        self.logger = logging.getLogger(f"agent.{self.agent_type.value}")
        self._trace_logger: TraceLogger | None = None
        self._memory_store: MemoryStore | None = None
        self._consecutive_failures: int = 0

    def set_trace_logger(self, trace_logger: TraceLogger) -> None:
        self._trace_logger = trace_logger

    def set_memory_store(self, store: MemoryStore) -> None:
        self._memory_store = store

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def reset_circuit_breaker(self) -> None:
        self._consecutive_failures = 0

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

    def _mitigate_context_pressure(
        self,
        messages: list[dict[str, Any]],
        budget: TokenBudget,
    ) -> list[dict[str, Any]]:
        """Truncate the longest message content to fit within budget."""
        estimated = estimate_tokens("".join(m.get("content", "") for m in messages))
        if budget.can_fit(estimated):
            return messages

        excess_tokens = estimated - budget.available
        excess_chars = int(excess_tokens * _CHARS_PER_TOKEN)
        self.logger.info(
            "Mitigating context pressure: %d excess tokens, truncating messages",
            excess_tokens,
        )

        mitigated = list(messages)
        content_sizes = [
            (i, len(m.get("content", ""))) for i, m in enumerate(mitigated)
        ]
        content_sizes.sort(key=lambda x: x[1], reverse=True)

        chars_to_cut = excess_chars + int(500 * _CHARS_PER_TOKEN)
        for idx, size in content_sizes:
            if chars_to_cut <= 0:
                break
            content = mitigated[idx].get("content", "")
            if not content:
                continue
            cut = min(chars_to_cut, size // 2)
            if cut < 100:
                continue
            truncated = (
                content[: size - cut]
                + "\n\n... [auto-truncated to fit context window] ...\n"
            )
            mitigated[idx] = {**mitigated[idx], "content": truncated}
            chars_to_cut -= cut
            self.logger.debug(
                "Truncated message[%d]: %d -> %d chars", idx, size, len(truncated)
            )

        return mitigated

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
        last_error: Exception | None = None

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

        for attempt in range(retries):
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
                    attempt + 1,
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
                        attempt=attempt + 1,
                        max_attempts=retries,
                        success=True,
                        prompt_preview=prompt_preview,
                        response_preview=resp_str[:300],
                        estimated_tokens=estimated_tokens,
                        budget_available=budget.available,
                        utilization=round(utilization, 4),
                    )
                return llm_result
            except ParseError as e:
                last_error = e
                elapsed = time.monotonic() - t0
                self.logger.warning(
                    "Parse error on attempt %d/%d (%.1fs): %s",
                    attempt + 1,
                    retries,
                    elapsed,
                    e,
                )
                if self._trace_logger:
                    self._trace_logger.record(
                        agent=self.agent_type.value,
                        model=self.llm_config.model,
                        provider=self.llm_config.provider,
                        prompt_chars=prompt_chars,
                        response_chars=0,
                        elapsed_seconds=elapsed,
                        attempt=attempt + 1,
                        max_attempts=retries,
                        success=False,
                        error=str(e)[:200],
                        prompt_preview=prompt_preview,
                        estimated_tokens=estimated_tokens,
                        budget_available=budget.available,
                        utilization=round(utilization, 4),
                    )
                if attempt + 1 < retries:
                    await asyncio.sleep(2**attempt)
            except Exception as e:
                last_error = e
                elapsed = time.monotonic() - t0
                self.logger.warning(
                    "LLM error on attempt %d/%d (%.1fs): %s",
                    attempt + 1,
                    retries,
                    elapsed,
                    e,
                )
                if self._trace_logger:
                    self._trace_logger.record(
                        agent=self.agent_type.value,
                        model=self.llm_config.model,
                        provider=self.llm_config.provider,
                        prompt_chars=prompt_chars,
                        response_chars=0,
                        elapsed_seconds=elapsed,
                        attempt=attempt + 1,
                        max_attempts=retries,
                        success=False,
                        error=str(e)[:200],
                        prompt_preview=prompt_preview,
                        estimated_tokens=estimated_tokens,
                        budget_available=budget.available,
                        utilization=round(utilization, 4),
                    )
                if attempt + 1 < retries:
                    await asyncio.sleep(2**attempt)

        self._consecutive_failures += 1
        raise RuntimeError(
            f"LLM call failed after {retries} attempts: {last_error}"
        ) from last_error
