"""Cost tracker for LLM calls (C3).

Records per-call token usage and computes USD cost using a built-in
pricing table.  Provides aggregation by agent and phase for the final
run report (C5).

All state is append-only and immutable — new entries produce a new list
rather than mutating in place.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class CostEntry:
    agent: str
    phase: str
    model: str
    provider: str
    usage: TokenUsage
    cost_usd: float
    elapsed_seconds: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class PricingEntry:
    """Per-million-token pricing."""

    input_per_m: float
    output_per_m: float
    cache_read_per_m: float = 0.0
    cache_write_per_m: float = 0.0


PRICING_TABLE: dict[str, PricingEntry] = {
    "claude-opus-4-6": PricingEntry(
        input_per_m=15.0,
        output_per_m=75.0,
        cache_read_per_m=1.5,
        cache_write_per_m=18.75,
    ),
    "claude-sonnet-4-6": PricingEntry(
        input_per_m=3.0,
        output_per_m=15.0,
        cache_read_per_m=0.3,
        cache_write_per_m=3.75,
    ),
    "claude-haiku-4-5-20251001": PricingEntry(
        input_per_m=0.80,
        output_per_m=4.0,
        cache_read_per_m=0.08,
        cache_write_per_m=1.0,
    ),
    "gpt-4o": PricingEntry(input_per_m=2.50, output_per_m=10.0),
    "gpt-4o-mini": PricingEntry(input_per_m=0.15, output_per_m=0.60),
    "gpt-4.1": PricingEntry(input_per_m=2.0, output_per_m=8.0),
    "gpt-5.4": PricingEntry(input_per_m=10.0, output_per_m=30.0),
}


def _calculate_cost(usage: TokenUsage, pricing: PricingEntry) -> float:
    cost = usage.input_tokens * pricing.input_per_m / 1_000_000
    cost += usage.output_tokens * pricing.output_per_m / 1_000_000
    cost += usage.cache_read_tokens * pricing.cache_read_per_m / 1_000_000
    cost += usage.cache_write_tokens * pricing.cache_write_per_m / 1_000_000
    return round(cost, 6)


class CostTracker:
    """Thread-safe accumulator for LLM call costs.

    Usage::

        tracker = CostTracker()
        tracker.record("planner", "planning", "claude-opus-4-6", "anthropic",
                        TokenUsage(input_tokens=5000, output_tokens=1200), 3.2)
        print(tracker.summary())
    """

    def __init__(
        self,
        pricing: dict[str, PricingEntry] | None = None,
    ) -> None:
        self._pricing = pricing or PRICING_TABLE
        self._entries: list[CostEntry] = []
        self._lock = threading.Lock()

    def record(
        self,
        agent: str,
        phase: str,
        model: str,
        provider: str,
        usage: TokenUsage,
        elapsed_seconds: float = 0.0,
    ) -> CostEntry:
        pricing = self._pricing.get(
            model, PricingEntry(input_per_m=0.0, output_per_m=0.0)
        )
        cost = _calculate_cost(usage, pricing)
        entry = CostEntry(
            agent=agent,
            phase=phase,
            model=model,
            provider=provider,
            usage=usage,
            cost_usd=cost,
            elapsed_seconds=elapsed_seconds,
        )
        with self._lock:
            self._entries = [*self._entries, entry]
        return entry

    @property
    def entries(self) -> list[CostEntry]:
        with self._lock:
            return list(self._entries)

    @property
    def total_cost_usd(self) -> float:
        with self._lock:
            return round(sum(e.cost_usd for e in self._entries), 6)

    @property
    def total_calls(self) -> int:
        with self._lock:
            return len(self._entries)

    def summary(self) -> dict[str, Any]:
        """Aggregate summary for reporting (C5)."""
        with self._lock:
            entries = list(self._entries)

        if not entries:
            return {
                "total_cost_usd": 0.0,
                "total_calls": 0,
                "total_tokens": {"input": 0, "output": 0},
                "by_agent": {},
                "by_phase": {},
                "by_model": {},
            }

        total_input = sum(e.usage.input_tokens for e in entries)
        total_output = sum(e.usage.output_tokens for e in entries)
        total_cache_read = sum(e.usage.cache_read_tokens for e in entries)
        total_cache_write = sum(e.usage.cache_write_tokens for e in entries)

        by_agent: dict[str, dict[str, Any]] = {}
        for e in entries:
            agg = by_agent.setdefault(
                e.agent, {"calls": 0, "cost_usd": 0.0, "tokens": 0}
            )
            agg["calls"] += 1
            agg["cost_usd"] = round(agg["cost_usd"] + e.cost_usd, 6)
            agg["tokens"] += e.usage.total_tokens

        by_phase: dict[str, dict[str, Any]] = {}
        for e in entries:
            agg = by_phase.setdefault(e.phase, {"calls": 0, "cost_usd": 0.0})
            agg["calls"] += 1
            agg["cost_usd"] = round(agg["cost_usd"] + e.cost_usd, 6)

        by_model: dict[str, dict[str, Any]] = {}
        for e in entries:
            agg = by_model.setdefault(e.model, {"calls": 0, "cost_usd": 0.0})
            agg["calls"] += 1
            agg["cost_usd"] = round(agg["cost_usd"] + e.cost_usd, 6)

        total_elapsed = sum(e.elapsed_seconds for e in entries)
        avg_latency = total_elapsed / len(entries) if entries else 0.0

        return {
            "total_cost_usd": round(sum(e.cost_usd for e in entries), 4),
            "total_calls": len(entries),
            "total_tokens": {
                "input": total_input,
                "output": total_output,
                "cache_read": total_cache_read,
                "cache_write": total_cache_write,
            },
            "avg_latency_s": round(avg_latency, 2),
            "by_agent": by_agent,
            "by_phase": by_phase,
            "by_model": by_model,
        }
