"""Tests for the 5 context management improvements:
1. Proactive pressure mitigation (BaseAgent)
2. Memory consolidation (MemoryStore)
3. Circuit breaker (BaseAgent)
4. Context utilization telemetry (TraceLogger)
5. Stale memory cleanup (MemoryStore)
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.base_agent import (
    CIRCUIT_BREAKER_THRESHOLD,
    BaseAgent,
    CircuitBreakerOpen,
)
from src.llm.context import TokenBudget, _CHARS_PER_TOKEN
from src.memory.models import MemoryEntry, MemoryEntryType
from src.memory.store import (
    CONSOLIDATION_THRESHOLD,
    MemoryStore,
    _consolidate_entries,
    _merge_entry_group,
)
from src.models.config import AgentLLMConfig
from src.models.message import AgentMessage, AgentType
from src.models.state import MergeState
from src.tools.trace_logger import AgentUtilizationStats, TraceLogger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubAgent(BaseAgent):
    agent_type = AgentType.PLANNER

    async def run(self, state: Any) -> AgentMessage:
        raise NotImplementedError

    def can_handle(self, state: MergeState) -> bool:
        return True


def _make_agent(**overrides: Any) -> _StubAgent:
    defaults = dict(
        provider="anthropic",
        model="claude-sonnet-4-6",
        api_key_env="ANTHROPIC_API_KEY",
        max_tokens=4096,
        max_retries=2,
    )
    defaults.update(overrides)
    cfg = AgentLLMConfig(**defaults)
    agent = _StubAgent(cfg)
    agent.llm = MagicMock()
    return agent


def _make_entry(
    phase: str = "planning",
    content: str = "test",
    file_paths: list[str] | None = None,
    tags: list[str] | None = None,
    confidence: float = 0.8,
    entry_type: MemoryEntryType = MemoryEntryType.PATTERN,
) -> MemoryEntry:
    return MemoryEntry(
        entry_type=entry_type,
        phase=phase,
        content=content,
        file_paths=file_paths or [],
        tags=tags or [],
        confidence=confidence,
    )


# ===========================================================================
# 1. Proactive Pressure Mitigation
# ===========================================================================


class TestPressureMitigation:
    def test_no_mitigation_when_within_budget(self):
        agent = _make_agent()
        budget = TokenBudget(
            model="claude-sonnet-4-6",
            context_window=200_000,
            reserved_for_output=4096,
        )
        messages = [{"role": "user", "content": "Hello, short prompt."}]
        result = agent._mitigate_context_pressure(messages, budget)
        assert result[0]["content"] == "Hello, short prompt."

    def test_truncates_longest_message_when_over_budget(self):
        agent = _make_agent()
        budget = TokenBudget(
            model="claude-sonnet-4-6",
            context_window=200_000,
            reserved_for_output=4096,
        )
        huge_content = "x" * int(budget.available * _CHARS_PER_TOKEN * 2)
        messages = [
            {"role": "system", "content": "short system"},
            {"role": "user", "content": huge_content},
        ]
        result = agent._mitigate_context_pressure(messages, budget)
        assert len(result[1]["content"]) < len(huge_content)
        assert "auto-truncated" in result[1]["content"]

    def test_preserves_short_messages(self):
        agent = _make_agent()
        budget = TokenBudget(
            model="claude-sonnet-4-6",
            context_window=200_000,
            reserved_for_output=4096,
        )
        huge = "x" * int(budget.available * _CHARS_PER_TOKEN * 2)
        messages = [
            {"role": "system", "content": "keep me"},
            {"role": "user", "content": huge},
        ]
        result = agent._mitigate_context_pressure(messages, budget)
        assert result[0]["content"] == "keep me"

    async def test_mitigation_called_in_retry_loop(self):
        agent = _make_agent()
        budget = agent._get_token_budget()
        huge = "x" * int(budget.available * _CHARS_PER_TOKEN * 2)
        messages = [{"role": "user", "content": huge}]

        agent.llm.complete = AsyncMock(return_value="ok")
        result = await agent._call_llm_with_retry(messages)
        assert result == "ok"
        call_args = agent.llm.complete.call_args
        actual_content = call_args[0][0][0]["content"]
        assert len(actual_content) < len(huge)


# ===========================================================================
# 2. Memory Consolidation
# ===========================================================================


class TestMemoryConsolidation:
    def test_consolidate_merges_similar_entries(self):
        entries = [
            _make_entry(
                phase="planning",
                content=f"pattern {i}",
                tags=["api"],
                confidence=0.7,
            )
            for i in range(10)
        ]
        consolidated = _consolidate_entries(entries)
        assert len(consolidated) < len(entries)

    def test_consolidate_preserves_small_groups(self):
        entries = [
            _make_entry(phase="planning", content="a", tags=["x"]),
            _make_entry(phase="planning", content="b", tags=["y"]),
        ]
        consolidated = _consolidate_entries(entries)
        assert len(consolidated) == 2

    def test_merge_group_combines_content(self):
        group = [
            _make_entry(content="pattern A", file_paths=["a.py"], confidence=0.7),
            _make_entry(content="pattern B", file_paths=["b.py"], confidence=0.8),
            _make_entry(content="pattern C", file_paths=["c.py"], confidence=0.6),
        ]
        merged = _merge_entry_group(group)
        assert "pattern A" in merged.content
        assert "pattern B" in merged.content
        assert "pattern C" in merged.content
        assert merged.confidence > 0.8
        assert len(merged.file_paths) == 3

    def test_merge_group_deduplicates_content(self):
        group = [
            _make_entry(content="same", confidence=0.7),
            _make_entry(content="same", confidence=0.8),
            _make_entry(content="same", confidence=0.6),
        ]
        merged = _merge_entry_group(group)
        assert merged.content == "same"

    def test_merge_group_caps_confidence(self):
        group = [
            _make_entry(confidence=0.95),
            _make_entry(confidence=0.95),
            _make_entry(confidence=0.95),
        ]
        merged = _merge_entry_group(group)
        assert merged.confidence <= 0.98

    def test_auto_consolidation_on_threshold(self):
        store = MemoryStore()
        for i in range(CONSOLIDATION_THRESHOLD):
            store = store.add_entry(
                _make_entry(
                    content=f"dup {i % 3}",
                    tags=["same_tag"],
                    confidence=0.5 + (i % 5) * 0.05,
                )
            )
        count_before = store.entry_count
        store = store.add_entry(_make_entry(content="trigger", tags=["same_tag"]))
        assert store.entry_count <= count_before

    def test_explicit_consolidate(self):
        store = MemoryStore()
        for i in range(20):
            store = store.add_entry(
                _make_entry(content=f"p{i}", tags=["t"], confidence=0.7)
            )
        consolidated_store = store.consolidate()
        assert consolidated_store.entry_count <= store.entry_count

    def test_merge_collects_all_file_paths(self):
        group = [
            _make_entry(file_paths=["a.py", "b.py"]),
            _make_entry(file_paths=["b.py", "c.py"]),
            _make_entry(file_paths=["d.py"]),
        ]
        merged = _merge_entry_group(group)
        assert set(merged.file_paths) == {"a.py", "b.py", "c.py", "d.py"}


# ===========================================================================
# 3. Circuit Breaker
# ===========================================================================


class TestCircuitBreaker:
    def test_initial_state(self):
        agent = _make_agent()
        assert agent.consecutive_failures == 0

    async def test_success_resets_counter(self):
        agent = _make_agent()
        agent._consecutive_failures = 2
        agent.llm.complete = AsyncMock(return_value="ok")

        result = await agent._call_llm_with_retry([{"role": "user", "content": "test"}])
        assert result == "ok"
        assert agent.consecutive_failures == 0

    async def test_failure_increments_counter(self):
        agent = _make_agent(max_retries=1)
        agent.llm.complete = AsyncMock(side_effect=RuntimeError("fail"))

        with pytest.raises(RuntimeError, match="failed after 1 attempts"):
            await agent._call_llm_with_retry([{"role": "user", "content": "test"}])
        assert agent.consecutive_failures == 1

    async def test_circuit_breaker_opens(self):
        agent = _make_agent()
        agent._consecutive_failures = CIRCUIT_BREAKER_THRESHOLD

        with pytest.raises(CircuitBreakerOpen):
            await agent._call_llm_with_retry([{"role": "user", "content": "test"}])

    async def test_circuit_breaker_just_below_threshold(self):
        agent = _make_agent()
        agent._consecutive_failures = CIRCUIT_BREAKER_THRESHOLD - 1
        agent.llm.complete = AsyncMock(return_value="ok")

        result = await agent._call_llm_with_retry([{"role": "user", "content": "test"}])
        assert result == "ok"
        assert agent.consecutive_failures == 0

    def test_manual_reset(self):
        agent = _make_agent()
        agent._consecutive_failures = 5
        agent.reset_circuit_breaker()
        assert agent.consecutive_failures == 0

    def test_threshold_value(self):
        assert CIRCUIT_BREAKER_THRESHOLD == 3


# ===========================================================================
# 4. Context Utilization Telemetry
# ===========================================================================


class TestUtilizationTelemetry:
    def test_record_with_utilization_fields(self, tmp_path):
        tl = TraceLogger(str(tmp_path), "test_run")
        tl.record(
            agent="planner",
            model="claude-sonnet-4-6",
            provider="anthropic",
            prompt_chars=1000,
            response_chars=500,
            elapsed_seconds=1.5,
            attempt=1,
            max_attempts=3,
            success=True,
            estimated_tokens=285,
            budget_available=180000,
            utilization=0.0014,
        )
        import json

        lines = tl.path.read_text().strip().split("\n")
        entry = json.loads(lines[0])
        assert entry["estimated_tokens"] == 285
        assert entry["budget_available"] == 180000
        assert entry["utilization"] == 0.0014

    def test_agent_stats_tracked(self, tmp_path):
        tl = TraceLogger(str(tmp_path), "test_run")
        tl.record(
            agent="planner",
            model="m",
            provider="p",
            prompt_chars=100,
            response_chars=50,
            elapsed_seconds=1.0,
            attempt=1,
            max_attempts=3,
            success=True,
            estimated_tokens=30,
            budget_available=100,
            utilization=0.3,
        )
        tl.record(
            agent="planner",
            model="m",
            provider="p",
            prompt_chars=200,
            response_chars=0,
            elapsed_seconds=0.5,
            attempt=1,
            max_attempts=3,
            success=False,
            estimated_tokens=60,
            budget_available=100,
            utilization=0.6,
        )
        stats = tl.get_agent_stats("planner")
        assert stats is not None
        assert stats.total_calls == 2
        assert stats.successful_calls == 1
        assert stats.failed_calls == 1
        assert stats.total_prompt_tokens == 90
        assert stats.peak_utilization == 0.6

    def test_stats_none_for_unknown_agent(self, tmp_path):
        tl = TraceLogger(str(tmp_path), "test_run")
        assert tl.get_agent_stats("nonexistent") is None

    def test_utilization_summary(self, tmp_path):
        tl = TraceLogger(str(tmp_path), "test_run")
        tl.record(
            agent="judge",
            model="m",
            provider="p",
            prompt_chars=100,
            response_chars=50,
            elapsed_seconds=2.0,
            attempt=1,
            max_attempts=1,
            success=True,
            estimated_tokens=100,
            budget_available=200,
            utilization=0.5,
        )
        summary = tl.get_utilization_summary()
        assert "judge" in summary
        assert summary["judge"]["total_calls"] == 1
        assert summary["judge"]["peak_utilization"] == 0.5

    def test_avg_utilization(self):
        stats = AgentUtilizationStats(
            total_prompt_tokens=150,
            total_budget_tokens=300,
        )
        assert stats.avg_utilization == 0.5

    def test_avg_utilization_zero_budget(self):
        stats = AgentUtilizationStats()
        assert stats.avg_utilization == 0.0

    async def test_telemetry_in_llm_call(self, tmp_path):
        agent = _make_agent()
        tl = TraceLogger(str(tmp_path), "test_run")
        agent.set_trace_logger(tl)
        agent.llm.complete = AsyncMock(return_value="done")

        await agent._call_llm_with_retry([{"role": "user", "content": "test prompt"}])
        stats = tl.get_agent_stats("planner")
        assert stats is not None
        assert stats.successful_calls == 1
        assert stats.total_prompt_tokens > 0


# ===========================================================================
# 5. Stale Memory Cleanup
# ===========================================================================


class TestStaleMemoryCleanup:
    def test_remove_superseded_basic(self):
        store = MemoryStore()
        store = store.add_entry(
            _make_entry(
                phase="planning",
                content="old plan",
                file_paths=["a.py"],
            )
        )
        store = store.add_entry(
            _make_entry(
                phase="auto_merge",
                content="merge result",
                file_paths=["a.py"],
            )
        )
        assert store.entry_count == 2
        cleaned = store.remove_superseded("auto_merge")
        assert cleaned.entry_count == 1
        entries = cleaned.query_by_type(MemoryEntryType.PATTERN, limit=10)
        assert entries[0].content == "merge result"

    def test_no_removal_for_first_phase(self):
        store = MemoryStore()
        store = store.add_entry(_make_entry(phase="planning", file_paths=["a.py"]))
        cleaned = store.remove_superseded("planning")
        assert cleaned.entry_count == 1

    def test_preserves_entries_with_different_paths(self):
        store = MemoryStore()
        store = store.add_entry(
            _make_entry(
                phase="planning",
                content="plan for b",
                file_paths=["b.py"],
            )
        )
        store = store.add_entry(
            _make_entry(
                phase="auto_merge",
                content="merge a",
                file_paths=["a.py"],
            )
        )
        cleaned = store.remove_superseded("auto_merge")
        assert cleaned.entry_count == 2

    def test_preserves_entries_without_file_paths(self):
        store = MemoryStore()
        store = store.add_entry(
            _make_entry(
                phase="planning",
                content="global insight",
                file_paths=[],
            )
        )
        store = store.add_entry(
            _make_entry(
                phase="auto_merge",
                content="merge a",
                file_paths=["a.py"],
            )
        )
        cleaned = store.remove_superseded("auto_merge")
        assert cleaned.entry_count == 2

    def test_multi_phase_supersession(self):
        store = MemoryStore()
        store = store.add_entry(
            _make_entry(phase="planning", content="plan", file_paths=["x.py"])
        )
        store = store.add_entry(
            _make_entry(phase="auto_merge", content="merge", file_paths=["x.py"])
        )
        store = store.add_entry(
            _make_entry(
                phase="conflict_analysis",
                content="conflict",
                file_paths=["x.py"],
            )
        )
        cleaned = store.remove_superseded("conflict_analysis")
        assert cleaned.entry_count == 1
        entries = cleaned.query_by_type(MemoryEntryType.PATTERN, limit=10)
        assert entries[0].content == "conflict"

    def test_unknown_phase_no_removal(self):
        store = MemoryStore()
        store = store.add_entry(_make_entry(phase="planning", file_paths=["a.py"]))
        cleaned = store.remove_superseded("unknown_phase")
        assert cleaned.entry_count == 1

    def test_partial_path_overlap_preserved(self):
        """Only fully-subsumed path sets are removed."""
        store = MemoryStore()
        store = store.add_entry(
            _make_entry(
                phase="planning",
                content="plan",
                file_paths=["a.py", "b.py"],
            )
        )
        store = store.add_entry(
            _make_entry(
                phase="auto_merge",
                content="merge",
                file_paths=["a.py"],
            )
        )
        cleaned = store.remove_superseded("auto_merge")
        assert cleaned.entry_count == 2
