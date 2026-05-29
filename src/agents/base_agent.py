import asyncio
import logging
import re
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal
from pydantic import BaseModel
from src.models.config import AgentLLMConfig
from src.models.message import AgentType, AgentMessage
from src.models.state import MergeState
from src.llm.client import (
    LLMClient,
    LLMClientFactory,
    LLMResponse,
    uses_openai_wire,
)
from src.llm.context import (
    TokenBudget,
    estimate_tokens,
    get_context_window,
)
from src.llm.context_compressor import ContextCompressor
from src.llm.error_classifier import ClassifiedError, ErrorCategory, classify_error
from src.llm.credential_pool import CredentialPool
from src.llm.model_router import select_model
from src.llm.retry_utils import jittered_backoff
from src.memory.hit_tracker import MemoryHitTracker
from src.memory.layered_loader import LayeredMemoryLoader
from src.memory.store import MemoryStore
from src.tools.cost_tracker import CostTracker, TokenUsage
from src.core.hooks import HOOK_LLM_END, HOOK_LLM_START, HookManager
from src.tools.trace_logger import TraceLogger

CIRCUIT_BREAKER_THRESHOLD = 3

# O-F1: sliding-window fallback trigger. Tracks the last N outcomes of
# ``_call_llm_with_retry`` (True=success, False=failure); when the failure
# rate crosses the threshold we flip to the fallback provider without
# requiring *consecutive* failures.
_SLIDING_WINDOW_SIZE = 20
_SLIDING_WINDOW_FAILURE_RATIO = 0.6
_SLIDING_WINDOW_MIN_SAMPLES = 10

# O-F1: certain error categories are known to vanish after a provider swap
# (e.g. Anthropic thinking-block parsing, OpenAI empty-content). Route them
# straight to fallback instead of waiting for the consecutive threshold.
_IMMEDIATE_FALLBACK_CATEGORIES: frozenset[ErrorCategory] = frozenset(
    {
        ErrorCategory.PROVIDER_EMPTY,
    }
)

_CIRCUIT_BREAKER_CATEGORIES: frozenset[ErrorCategory] = frozenset(
    {
        ErrorCategory.AUTH_PERMANENT,
        ErrorCategory.FORMAT,
    }
)

MAX_RATE_LIMIT_WAITS = 5

_FORMAT_ERROR_BODY_MAX_CHARS = 2048
_REDACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{6,}"), "sk-ant-<redacted>"),
    (re.compile(r"sk-[A-Za-z0-9_\-]{20,}"), "sk-<redacted>"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-.~+/=]{12,}"), "Bearer <redacted>"),
    (
        re.compile(
            r"(?i)(api[_-]?key|authorization)\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{12,}"
        ),
        r"\1=<redacted>",
    ),
)


def _redact_secrets(text: str) -> str:
    for pattern, replacement in _REDACT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


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
    contract_name: str | None = None

    def __init__(self, llm_config: AgentLLMConfig):
        self.llm_config = llm_config
        self.llm: LLMClient = LLMClientFactory.create(llm_config)
        self.logger = logging.getLogger(f"agent.{self.agent_type.value}")
        self._trace_logger: TraceLogger | None = None
        self._memory_store: MemoryStore | None = None
        self._memory_hit_tracker: MemoryHitTracker | None = None
        self._memory_config: object | None = None
        self._upstream_ref: str = ""
        self._consecutive_failures: int = 0
        self._sliding_window: deque[bool] = deque(maxlen=_SLIDING_WINDOW_SIZE)
        self._credential_pool: CredentialPool | None = self._init_credential_pool()
        self._cost_tracker: CostTracker | None = None
        self._current_phase: str = ""
        self._hooks: HookManager | None = None
        self._contract: Any | None = None
        # U2 budget cap state.
        self._budget_limit_usd: float | None = None
        self._budget_warn_pct: float = 0.8
        self._budget_warning_emitted: bool = False
        self._on_activity: Any | None = None
        self._fallback_llm: LLMClient | None = (
            LLMClientFactory.create(llm_config.fallback)
            if llm_config.fallback is not None
            else None
        )
        self._using_fallback: bool = False

    @property
    def contract(self) -> Any | None:
        """Lazily load the agent's behavioral contract, if declared.

        Returns None for agents that haven't opted in (contract_name unset).
        Loading failures surface as exceptions on first access.
        """
        if self.contract_name is None:
            return None
        if self._contract is None:
            from src.agents.contract import load_contract

            self._contract = load_contract(self.contract_name)
        return self._contract

    def restricted_view(self, state: Any) -> Any:
        """Wrap *state* with a contract-restricted ReadOnlyStateView.

        Returns *state* unchanged when the agent has no contract.  When the
        agent has a contract:

        * if *state* is already a restricted view matching this contract, it
          is returned unchanged;
        * if *state* is an unrestricted ``ReadOnlyStateView``, it is re-wrapped
          from its underlying state with the contract whitelist applied;
        * otherwise (plain MergeState), it is wrapped with ``restricted()``.

        Reads of attributes not in ``contract.inputs`` raise
        :class:`FieldNotInContract`.
        """
        contract = self.contract
        if contract is None:
            return state
        from src.core.read_only_state_view import ReadOnlyStateView

        allowed = set(contract.inputs)
        if isinstance(state, ReadOnlyStateView):
            existing_allowed = object.__getattribute__(state, "_allowed_fields")
            existing_contract = object.__getattribute__(state, "_contract_name")
            if (
                existing_allowed is not None
                and existing_contract == contract.name
                and set(existing_allowed) == allowed
            ):
                return state
            inner = object.__getattribute__(state, "_state")
            return ReadOnlyStateView.restricted(
                inner,
                allowed_fields=allowed,
                contract_name=contract.name,
            )
        return ReadOnlyStateView.restricted(
            state,
            allowed_fields=allowed,
            contract_name=contract.name,
        )

    def set_trace_logger(self, trace_logger: TraceLogger) -> None:
        self._trace_logger = trace_logger

    def set_memory_store(self, store: MemoryStore) -> None:
        self._memory_store = store

    def set_memory_hit_tracker(self, tracker: MemoryHitTracker | None) -> None:
        self._memory_hit_tracker = tracker

    def set_memory_config(self, cfg: object | None) -> None:
        """Receive ``MemoryExtractionConfig`` so the layered loader can read
        relevance-filter knobs (O-M3). ``object | None`` typing avoids a
        circular import with ``src.models.config``.
        """
        self._memory_config = cfg

    def set_upstream_ref(self, ref: str) -> None:
        self._upstream_ref = ref

    def set_cost_tracker(self, tracker: CostTracker, phase: str = "") -> None:
        self._cost_tracker = tracker
        self._current_phase = phase

    def set_hooks(self, hooks: HookManager) -> None:
        self._hooks = hooks

    def set_budget(self, limit_usd: float | None, warn_pct: float = 0.8) -> None:
        """Configure U2 per-run budget. ``None`` disables the cap.

        When set, ``_call_llm_with_retry`` checks ``cost_tracker.total_cost_usd``
        before and after each LLM call and raises ``RunBudgetExceeded`` once
        the cumulative spend reaches ``limit_usd``. The first crossing of
        ``limit_usd * warn_pct`` emits a ``budget_warning`` activity event.
        """
        self._budget_limit_usd = limit_usd
        self._budget_warn_pct = warn_pct
        self._budget_warning_emitted = False

    def set_activity_callback(self, cb: Any) -> None:
        """Register the Orchestrator's activity callback so the agent can
        emit ``budget_warning`` events directly (rather than only through
        phase boundaries)."""
        self._on_activity = cb

    def _emit_activity(
        self,
        event_type: Literal["start", "progress", "complete", "error"],
        action: str = "",
        elapsed: float | None = None,
    ) -> None:
        """Emit a per-agent run-state event (start/complete/error of an LLM
        call) so the topology can show genuine live agent activity.

        No-op when no activity callback is wired (unit tests / standalone use).
        Consumers fold the stream with "latest event per agent wins", so the
        nesting introduced by retry/fallback recursion is harmless.
        """
        if self._on_activity is None:
            return
        from src.core.phases.base import ActivityEvent

        self._on_activity(
            ActivityEvent(
                agent=self.agent_type.value,
                action=action or self._current_phase,
                phase=self._current_phase,
                event_type=event_type,
                elapsed=elapsed,
            )
        )

    def _check_budget(self) -> None:
        """U2: raise ``RunBudgetExceeded`` when cumulative cost meets limit;
        emit a one-shot warning on first crossing of ``warn_pct``.

        Called twice per LLM call (pre + post) so a single big call that
        pushes spend over the cap is still detected promptly. The warning
        is gated on ``_budget_warning_emitted`` to avoid flooding the event
        stream when the agent stays in the warn band.
        """
        if self._budget_limit_usd is None or self._cost_tracker is None:
            return
        spent = self._cost_tracker.total_cost_usd
        limit = self._budget_limit_usd
        if spent >= limit:
            from src.models.state import RunBudgetExceeded

            raise RunBudgetExceeded(spent=spent, limit=limit, phase=self._current_phase)
        warn_threshold = limit * self._budget_warn_pct
        if spent >= warn_threshold and not self._budget_warning_emitted:
            self._budget_warning_emitted = True
            if self._on_activity is not None:
                from src.core.phases.base import ActivityEvent

                ratio = spent / limit if limit > 0 else 0.0
                self._on_activity(
                    ActivityEvent(
                        agent=self.agent_type.value,
                        action="budget_warning",
                        phase=self._current_phase,
                        event_type="progress",
                        extra={"pct": ratio},
                    )
                )

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def reset_circuit_breaker(self) -> None:
        self._consecutive_failures = 0
        self._sliding_window.clear()

    def _sliding_window_failure_rate(self) -> tuple[int, float]:
        """Return ``(sample_count, failure_ratio)`` of the current window."""
        samples = len(self._sliding_window)
        if samples == 0:
            return 0, 0.0
        failures = sum(1 for ok in self._sliding_window if not ok)
        return samples, failures / samples

    def _should_fallback_by_window(self) -> bool:
        """O-F1: decide whether the sliding-window failure rate warrants a
        switch to the fallback provider even before the consecutive-failure
        circuit breaker trips."""
        samples, ratio = self._sliding_window_failure_rate()
        return (
            samples >= _SLIDING_WINDOW_MIN_SAMPLES
            and ratio >= _SLIDING_WINDOW_FAILURE_RATIO
        )

    def get_memory_context(
        self,
        current_phase: str,
        file_paths: list[str] | None = None,
    ) -> str:
        if self._memory_store is None:
            return ""
        memory_cfg = getattr(self, "_memory_config", None)
        loader = LayeredMemoryLoader(
            self._memory_store,
            self._memory_hit_tracker,
            min_relevance=(
                memory_cfg.relevance_min_score if memory_cfg is not None else 0.0
            ),
            relevance_filter_threshold=(
                memory_cfg.relevance_filter_threshold if memory_cfg is not None else 100
            ),
            upstream_ref=self._upstream_ref,
        )
        text = loader.load_for_agent(current_phase, file_paths)
        if text:
            section_count = text.count("## ")
            self.logger.info(
                "Memory injected: agent=%s phase=%s sections=%d chars=%d files=%d",
                self.agent_type.value,
                current_phase,
                section_count,
                len(text),
                len(file_paths or []),
            )
        return text

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
        """Switch to the configured fallback LLM provider, if any.

        Default implementation: if ``self._fallback_llm`` is wired (yaml has
        ``fallback:``) and we are not already running on it, mutate
        ``self.llm`` / ``self.llm_config`` to the fallback, reset the
        circuit breaker, and return True so the retry loop continues.
        The swap is sticky for the agent's lifetime — primary credentials
        that produce a permanent-auth or empty-content failure don't get
        re-tried on later calls. Subclasses can override for richer policies
        (e.g. credential pool rotation in C2).
        """
        if self._fallback_llm is None or self._using_fallback:
            self.logger.warning(
                "Provider fallback requested but no fallback provider "
                "configured / already on fallback (category=%s)",
                classified.category.value,
            )
            return False
        fallback_cfg = self.llm_config.fallback
        assert fallback_cfg is not None
        self.logger.warning(
            "Switching to fallback provider %s/%s (category=%s)",
            fallback_cfg.provider,
            fallback_cfg.model,
            classified.category.value,
        )
        self.llm = self._fallback_llm
        self.llm_config = fallback_cfg
        self._using_fallback = True
        self._consecutive_failures = 0
        self._sliding_window.clear()
        return True

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

    async def _call_llm_with_retry_meta(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_retries: int | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Variant of ``_call_llm_with_retry`` that returns ``LLMResponse``.

        Use when the caller needs ``stop_reason`` (the output quality
        gate in ``parse_merge_result`` does — a ``stop_reason ==
        "max_tokens"`` means the LLM was truncated and the text must
        not be written to disk). Schema/structured-output is NOT
        supported here on purpose: ``complete_structured`` returns a
        validated pydantic model where truncation either fails parsing
        or surfaces as ``ModelOutputError`` upstream.
        """
        result = await self._call_llm_with_retry(
            messages,
            system=system,
            schema=None,
            max_retries=max_retries,
            json_mode=json_mode,
            _return_meta=True,
        )
        if isinstance(result, LLMResponse):
            return result
        # Defensive: should be unreachable given _return_meta=True with
        # schema=None — but if a subclass / mock returns plain text we
        # wrap it as a stop_reason-less response rather than crashing.
        return LLMResponse(text=str(result), stop_reason=None)

    def _structured_kwargs(self, schema_name: str) -> dict[str, Any]:
        """P2-1: structured-output kwargs for ``_call_llm_with_retry``.

        Returns ``{json_schema, schema_name}`` when this agent has opted into
        ``use_structured_outputs``; an empty dict otherwise (legacy path).
        The returned JSON string is parsed by the agent's existing parser, so
        enabling the flag is shape-only — semantics stay in response_parser.
        """
        if not self.llm_config.use_structured_outputs:
            return {}
        from src.llm.structured_schemas import wire_schema

        return {"json_schema": wire_schema(schema_name), "schema_name": schema_name}

    async def _call_llm_with_retry(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        schema: type[BaseModel] | None = None,
        max_retries: int | None = None,
        json_mode: bool = False,
        json_schema: dict[str, Any] | None = None,
        schema_name: str = "response",
        _return_meta: bool = False,
    ) -> str | BaseModel | LLMResponse:
        # U2: pre-call budget gate. Skipped when limit is None or no
        # cost_tracker is wired (unit tests / standalone usage).
        self._check_budget()

        if self._consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
            if self._fallback_llm is not None and not self._using_fallback:
                self.logger.warning(
                    "Circuit breaker OPEN for %s (%d failures) — switching to fallback provider %s/%s",
                    self.agent_type.value,
                    self._consecutive_failures,
                    self.llm_config.fallback.provider,  # type: ignore[union-attr]
                    self.llm_config.fallback.model,  # type: ignore[union-attr]
                )
                self._using_fallback = True
                saved_llm, saved_config = self.llm, self.llm_config
                self.llm = self._fallback_llm
                self.llm_config = self.llm_config.fallback  # type: ignore[assignment]
                self._consecutive_failures = 0
                try:
                    return await self._call_llm_with_retry(
                        messages,
                        system,
                        schema,
                        max_retries,
                        json_mode,
                        json_schema=json_schema,
                        schema_name=schema_name,
                        _return_meta=_return_meta,
                    )
                finally:
                    self.llm, self.llm_config = saved_llm, saved_config
                    self._using_fallback = False
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

        routed_model = select_model(messages, self.llm_config)
        model_override = self.llm.with_model(routed_model)
        model_override.__enter__()

        self.logger.info(
            "LLM call: model=%s (routed=%s), provider=%s, prompt_chars=%d, est_tokens=%d, "
            "max_tokens=%d, utilization=%.1f%%",
            self.llm_config.model,
            routed_model,
            self.llm_config.provider,
            prompt_chars,
            estimated_tokens,
            self.llm_config.max_tokens,
            utilization * 100,
        )

        t_call_start = time.monotonic()
        if self._hooks:
            await self._hooks.emit(
                HOOK_LLM_START,
                agent=self.agent_type.value,
                model=routed_model,
                provider=self.llm_config.provider,
                prompt_chars=prompt_chars,
                estimated_tokens=estimated_tokens,
                phase=self._current_phase,
            )
        self._emit_activity("start")

        retry_budget = RetryBudget(max_retries=retries)
        last_error: Exception | None = None
        last_classified: ClassifiedError | None = None

        while True:
            if retry_budget.retries_exhausted:
                break

            t0 = time.monotonic()
            try:
                llm_result: str | BaseModel | LLMResponse
                if schema is not None:
                    llm_result = await self.llm.complete_structured(
                        messages, schema, system=system
                    )
                elif json_schema is not None:
                    # P2-1 reliability layer: native Structured Outputs return
                    # a well-formed JSON string that the agent's existing
                    # response_parser still consumes (grounding / sanitisation
                    # / deterministic-verdict logic stays in the parser).
                    llm_result = await self.llm.structured_json(
                        messages,
                        json_schema=json_schema,
                        schema_name=schema_name,
                        system=system,
                    )
                else:
                    extra: dict[str, Any] = {}
                    if json_mode and uses_openai_wire(self.llm_config.provider):
                        from src.llm.client import _is_openai_reasoning_model

                        if self.llm_config.api_style == "responses":
                            extra["response_format"] = {"type": "json_object"}
                        elif not _is_openai_reasoning_model(self.llm_config.model):
                            extra["response_format"] = {"type": "json_object"}
                    if _return_meta:
                        llm_result = await self.llm.complete_meta(
                            messages, system=system, **extra
                        )
                    else:
                        llm_result = await self.llm.complete(
                            messages, system=system, **extra
                        )
                elapsed = time.monotonic() - t0
                # Telemetry strings work off the visible text — when the
                # result is an ``LLMResponse`` the dataclass repr would
                # otherwise leak ``LLMResponse(text=..., stop_reason=...)``
                # into trace logs and cost-tracker counts.
                resp_str = (
                    llm_result.text
                    if isinstance(llm_result, LLMResponse)
                    else str(llm_result)
                )
                resp_len = len(resp_str)
                self.logger.info(
                    "LLM response: attempt=%d/%d, elapsed=%.1fs, response_chars=%d",
                    retry_budget.attempt + 1,
                    retries,
                    elapsed,
                    resp_len,
                )
                self._consecutive_failures = 0
                self._sliding_window.append(True)
                if self._trace_logger:
                    self._trace_logger.record(
                        agent=self.agent_type.value,
                        model=routed_model,
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
                        model=routed_model,
                        provider=self.llm_config.provider,
                        usage=TokenUsage(
                            input_tokens=estimated_tokens,
                            output_tokens=output_tokens,
                        ),
                        elapsed_seconds=elapsed,
                    )
                if self._hooks:
                    await self._hooks.emit(
                        HOOK_LLM_END,
                        agent=self.agent_type.value,
                        model=routed_model,
                        provider=self.llm_config.provider,
                        elapsed=time.monotonic() - t_call_start,
                        success=True,
                        response_chars=resp_len,
                        attempt=retry_budget.attempt + 1,
                    )
                self._emit_activity("complete", elapsed=time.monotonic() - t_call_start)
                model_override.__exit__(None, None, None)
                # U2: post-call budget gate. The call we just made may have
                # pushed cumulative spend over the cap; raise before returning
                # so the orchestrator can transition AWAITING_HUMAN with a
                # partial report rather than letting the next call slip in.
                self._check_budget()
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
                if classified.category == ErrorCategory.FORMAT:
                    body = _redact_secrets(str(e))[:_FORMAT_ERROR_BODY_MAX_CHARS]
                    response_body = getattr(getattr(e, "response", None), "text", None)
                    if response_body:
                        body = _redact_secrets(str(response_body))[
                            :_FORMAT_ERROR_BODY_MAX_CHARS
                        ]
                    self.logger.warning(
                        "FORMAT error body (redacted, first %d chars): %s",
                        _FORMAT_ERROR_BODY_MAX_CHARS,
                        body,
                    )
                if self._trace_logger:
                    self._trace_logger.record(
                        agent=self.agent_type.value,
                        model=routed_model,
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

                self._sliding_window.append(False)

                # O-F1: certain error categories nearly always clear after a
                # provider swap. Don't wait for the consecutive-failure or
                # sliding-window thresholds — flip to fallback immediately.
                if (
                    classified.category in _IMMEDIATE_FALLBACK_CATEGORIES
                    and self._fallback_llm is not None
                    and not self._using_fallback
                ):
                    if self._on_fallback_needed(classified):
                        retry_budget.consume_attempt()
                        continue

                # O-F1: sliding-window error-rate trigger. Even if the
                # classified error is retryable, a sustained high error rate
                # means the provider is unhealthy — route to fallback now.
                if (
                    self._should_fallback_by_window()
                    and self._fallback_llm is not None
                    and not self._using_fallback
                ):
                    samples, ratio = self._sliding_window_failure_rate()
                    self.logger.warning(
                        "Sliding-window failure rate %.2f over %d samples — "
                        "switching to fallback provider",
                        ratio,
                        samples,
                    )
                    if self._on_fallback_needed(classified):
                        retry_budget.consume_attempt()
                        continue

                if not classified.retryable:
                    if classified.should_fallback:
                        if self._on_fallback_needed(classified):
                            retry_budget.consume_attempt()
                            continue
                    if classified.category in _CIRCUIT_BREAKER_CATEGORIES:
                        self._consecutive_failures += 1
                    if self._hooks:
                        await self._hooks.emit(
                            HOOK_LLM_END,
                            agent=self.agent_type.value,
                            model=routed_model,
                            provider=self.llm_config.provider,
                            elapsed=time.monotonic() - t_call_start,
                            success=False,
                            response_chars=0,
                            attempt=retry_budget.attempt + 1,
                        )
                    self._emit_activity(
                        "error", elapsed=time.monotonic() - t_call_start
                    )
                    model_override.__exit__(None, None, None)
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
                elif (
                    classified.should_fallback
                    and self._fallback_llm is not None
                    and not self._using_fallback
                ):
                    # O-F1: the primary provider exhausted its local retries on
                    # a retryable-but-fallback-worthy error (sustained 5xx /
                    # overload). The immediate-category, sliding-window and
                    # circuit-breaker triggers can't catch this for a single-call
                    # agent, so swap to the configured fallback provider and give
                    # it a fresh retry budget before giving up.
                    if self._on_fallback_needed(classified):
                        retry_budget = RetryBudget(max_retries=retries)
                        continue

        if last_classified and last_classified.category in _CIRCUIT_BREAKER_CATEGORIES:
            self._consecutive_failures += 1
        elif last_classified and last_classified.category not in (
            ErrorCategory.RATE_LIMIT,
            ErrorCategory.TRANSPORT,
        ):
            self._consecutive_failures += 1
        if self._hooks:
            await self._hooks.emit(
                HOOK_LLM_END,
                agent=self.agent_type.value,
                model=routed_model,
                provider=self.llm_config.provider,
                elapsed=time.monotonic() - t_call_start,
                success=False,
                response_chars=0,
                attempt=retry_budget.attempt,
            )
        self._emit_activity("error", elapsed=time.monotonic() - t_call_start)
        model_override.__exit__(None, None, None)
        raise AgentExhaustedError(
            f"Agent {self.agent_type.value}: LLM call failed after "
            f"{retry_budget.attempt} attempts "
            f"(+{retry_budget.rate_limit_waits} rate-limit waits): {last_error}",
            last_classified,
        ) from last_error
