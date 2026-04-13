"""Tests for Phase C (C1-C5): Hooks, credential pool, cost tracker, structured logger, report insights."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from functools import partial
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ============================================================================
# C1: Lifecycle hooks
# ============================================================================

from src.core.hooks import HookManager


class TestHookManager:
    def test_on_and_emit(self):
        hm = HookManager()
        results: list[str] = []
        hm.on("phase:before", lambda phase="": results.append(phase))
        asyncio.run(hm.emit("phase:before", phase="planning"))
        assert results == ["planning"]

    def test_async_handler(self):
        hm = HookManager()
        results: list[str] = []

        async def handler(phase: str = "") -> None:
            results.append(phase)

        hm.on("phase:after", handler)
        asyncio.run(hm.emit("phase:after", phase="auto_merge"))
        assert results == ["auto_merge"]

    def test_wildcard_matching(self):
        hm = HookManager()
        results: list[str] = []
        hm.on("phase:*", lambda **kw: results.append(kw.get("phase", "")))
        asyncio.run(hm.emit("phase:before", phase="init"))
        asyncio.run(hm.emit("phase:after", phase="init"))
        assert results == ["init", "init"]

    def test_error_isolation(self):
        hm = HookManager()
        results: list[str] = []

        def failing_handler(**kw: Any) -> None:
            raise RuntimeError("boom")

        hm.on("test", failing_handler)
        hm.on("test", lambda **kw: results.append("ok"))

        ret = asyncio.run(hm.emit("test"))
        assert ret[0] is None
        assert results == ["ok"]

    def test_off_removes_handler(self):
        hm = HookManager()
        results: list[str] = []
        handler = lambda **kw: results.append("called")
        hm.on("evt", handler)
        hm.off("evt", handler)
        asyncio.run(hm.emit("evt"))
        assert results == []

    def test_no_handlers_returns_empty(self):
        hm = HookManager()
        ret = asyncio.run(hm.emit("nonexistent"))
        assert ret == []

    def test_handler_count(self):
        hm = HookManager()
        hm.on("a", lambda: None)
        hm.on("b", lambda: None)
        hm.on("b", lambda: None)
        assert hm.handler_count == 3

    def test_clear(self):
        hm = HookManager()
        hm.on("a", lambda: None)
        hm.clear()
        assert hm.handler_count == 0


class TestHookManagerInPhaseContext:
    def test_phase_context_has_hooks(self):
        from src.core.phases.base import PhaseContext

        ctx = PhaseContext(
            config=MagicMock(),
            git_tool=MagicMock(),
            gate_runner=MagicMock(),
            state_machine=MagicMock(),
            message_bus=MagicMock(),
            checkpoint=MagicMock(),
            phase_runner=MagicMock(),
            memory_store=MagicMock(),
            summarizer=MagicMock(),
        )
        assert isinstance(ctx.hooks, HookManager)


# ============================================================================
# C2: Credential pool
# ============================================================================

from src.llm.credential_pool import (
    AllCredentialsCoolingDown,
    Credential,
    CredentialPool,
)


class TestCredential:
    def test_available_by_default(self):
        cred = Credential(key="sk-test", source="env:TEST")
        assert cred.is_available
        assert cred.remaining_cooldown_seconds == 0.0

    def test_cooldown(self):
        cred = Credential(
            key="sk-test",
            source="env:TEST",
            cooldown_until=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        assert not cred.is_available
        assert cred.remaining_cooldown_seconds > 0

    def test_expired_cooldown(self):
        cred = Credential(
            key="sk-test",
            source="env:TEST",
            cooldown_until=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        assert cred.is_available


class TestCredentialPool:
    def test_get_active_single(self):
        pool = CredentialPool([Credential(key="sk-1", source="env")])
        cred = pool.get_active()
        assert cred.key == "sk-1"

    def test_rotation_after_cooldown(self):
        creds = [
            Credential(key="sk-1", source="env:K1"),
            Credential(key="sk-2", source="env:K2"),
        ]
        pool = CredentialPool(creds)
        first = pool.get_active()
        pool.cooldown(first, seconds=3600)
        second = pool.get_active()
        assert second.key != first.key

    def test_all_cooling_down_raises(self):
        creds = [Credential(key="sk-1", source="env")]
        pool = CredentialPool(creds)
        pool.cooldown(creds[0], seconds=3600)
        with pytest.raises(AllCredentialsCoolingDown):
            pool.get_active()

    def test_empty_pool_raises(self):
        pool = CredentialPool([])
        with pytest.raises(AllCredentialsCoolingDown):
            pool.get_active()

    def test_from_env_vars(self):
        with patch.dict(os.environ, {"TEST_KEY_1": "sk-a", "TEST_KEY_2": "sk-b"}):
            pool = CredentialPool.from_env_vars(["TEST_KEY_1", "TEST_KEY_2"])
            assert pool.size == 2

    def test_from_env_vars_skips_missing(self):
        with patch.dict(os.environ, {"TEST_KEY_1": "sk-a"}, clear=False):
            env = os.environ.copy()
            env.pop("MISSING_KEY", None)
            with patch.dict(os.environ, env, clear=True):
                pool = CredentialPool.from_env_vars(["TEST_KEY_1", "MISSING_KEY"])
                assert pool.size == 1

    def test_available_count(self):
        creds = [
            Credential(key="sk-1", source="env"),
            Credential(key="sk-2", source="env"),
        ]
        pool = CredentialPool(creds)
        assert pool.available_count == 2
        pool.cooldown(creds[0])
        assert pool.available_count == 1

    def test_reset(self):
        cred = Credential(key="sk-1", source="env")
        pool = CredentialPool([cred])
        pool.cooldown(cred, seconds=3600)
        assert not cred.is_available
        pool.reset(cred)
        assert cred.is_available


class TestAgentLLMConfigKeyList:
    def test_string_key_returns_list(self):
        from src.models.config import AgentLLMConfig

        config = AgentLLMConfig(api_key_env="MY_KEY")
        assert config.api_key_env_list == ["MY_KEY"]

    def test_list_key_returns_as_is(self):
        from src.models.config import AgentLLMConfig

        config = AgentLLMConfig(api_key_env=["KEY1", "KEY2"])
        assert config.api_key_env_list == ["KEY1", "KEY2"]

    def test_backward_compat_single_string(self):
        from src.models.config import AgentLLMConfig

        data = {
            "provider": "anthropic",
            "model": "claude-opus-4-6",
            "api_key_env": "ANTHROPIC_API_KEY",
        }
        config = AgentLLMConfig.model_validate(data)
        assert config.api_key_env_list == ["ANTHROPIC_API_KEY"]

    def test_list_yaml_round_trip(self):
        from src.models.config import AgentLLMConfig

        config = AgentLLMConfig(api_key_env=["KEY1", "KEY2"])
        data = config.model_dump()
        restored = AgentLLMConfig.model_validate(data)
        assert restored.api_key_env_list == ["KEY1", "KEY2"]


# ============================================================================
# C3: Cost tracker
# ============================================================================

from src.tools.cost_tracker import (
    CostTracker,
    PricingEntry,
    TokenUsage,
    _calculate_cost,
)


class TestTokenUsage:
    def test_total_tokens(self):
        usage = TokenUsage(input_tokens=100, output_tokens=50)
        assert usage.total_tokens == 150

    def test_defaults(self):
        usage = TokenUsage()
        assert usage.total_tokens == 0
        assert usage.cache_read_tokens == 0


class TestCalculateCost:
    def test_basic_cost(self):
        usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)
        pricing = PricingEntry(input_per_m=15.0, output_per_m=75.0)
        cost = _calculate_cost(usage, pricing)
        assert cost == 90.0

    def test_with_cache(self):
        usage = TokenUsage(
            input_tokens=500_000,
            output_tokens=100_000,
            cache_read_tokens=200_000,
            cache_write_tokens=50_000,
        )
        pricing = PricingEntry(
            input_per_m=15.0,
            output_per_m=75.0,
            cache_read_per_m=1.5,
            cache_write_per_m=18.75,
        )
        cost = _calculate_cost(usage, pricing)
        assert cost > 0

    def test_zero_usage(self):
        usage = TokenUsage()
        pricing = PricingEntry(input_per_m=15.0, output_per_m=75.0)
        assert _calculate_cost(usage, pricing) == 0.0


class TestCostTracker:
    def test_record_and_summary(self):
        tracker = CostTracker()
        tracker.record(
            agent="planner",
            phase="planning",
            model="claude-opus-4-6",
            provider="anthropic",
            usage=TokenUsage(input_tokens=5000, output_tokens=1200),
            elapsed_seconds=3.2,
        )
        assert tracker.total_calls == 1
        assert tracker.total_cost_usd > 0
        summary = tracker.summary()
        assert summary["total_calls"] == 1
        assert "planner" in summary["by_agent"]
        assert "planning" in summary["by_phase"]

    def test_empty_summary(self):
        tracker = CostTracker()
        summary = tracker.summary()
        assert summary["total_calls"] == 0
        assert summary["total_cost_usd"] == 0.0

    def test_multiple_entries(self):
        tracker = CostTracker()
        tracker.record(
            "planner",
            "planning",
            "claude-opus-4-6",
            "anthropic",
            TokenUsage(input_tokens=1000, output_tokens=500),
        )
        tracker.record(
            "judge",
            "judge_review",
            "claude-opus-4-6",
            "anthropic",
            TokenUsage(input_tokens=2000, output_tokens=1000),
        )
        assert tracker.total_calls == 2
        summary = tracker.summary()
        assert len(summary["by_agent"]) == 2

    def test_thread_safety(self):
        tracker = CostTracker()
        errors: list[Exception] = []

        def record_many() -> None:
            try:
                for _ in range(100):
                    tracker.record(
                        "test",
                        "phase",
                        "gpt-4o",
                        "openai",
                        TokenUsage(input_tokens=10, output_tokens=5),
                    )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=record_many) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert tracker.total_calls == 400

    def test_custom_pricing(self):
        custom = {"my-model": PricingEntry(input_per_m=1.0, output_per_m=2.0)}
        tracker = CostTracker(pricing=custom)
        tracker.record(
            "a",
            "p",
            "my-model",
            "custom",
            TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000),
        )
        assert tracker.total_cost_usd == 3.0

    def test_unknown_model_zero_cost(self):
        tracker = CostTracker()
        tracker.record(
            "a",
            "p",
            "unknown-model",
            "test",
            TokenUsage(input_tokens=1000, output_tokens=500),
        )
        assert tracker.total_cost_usd == 0.0


# ============================================================================
# C4: Structured logger
# ============================================================================

from src.tools.structured_logger import StructuredFormatter, create_structured_handler


class TestStructuredFormatter:
    def test_json_output(self):
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="test.module",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="hello %s",
            args=("world",),
            exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert data["level"] == "INFO"
        assert data["message"] == "hello world"
        assert data["logger"] == "test.module"
        assert "ts" in data

    def test_extra_fields(self):
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.DEBUG,
            pathname="test.py",
            lineno=1,
            msg="msg",
            args=(),
            exc_info=None,
        )
        record.extra = {"agent": "planner", "phase": "planning"}  # type: ignore[attr-defined]
        output = formatter.format(record)
        data = json.loads(output)
        assert data["agent"] == "planner"
        assert data["phase"] == "planning"

    def test_exception_included(self):
        import sys

        formatter = StructuredFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="failed",
            args=(),
            exc_info=exc_info,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert "exception" in data
        assert "ValueError" in data["exception"]


class TestCreateStructuredHandler:
    def test_creates_handler(self, tmp_path):
        path = str(tmp_path / "test.jsonl")
        handler = create_structured_handler(path)
        assert isinstance(handler, logging.FileHandler)
        assert isinstance(handler.formatter, StructuredFormatter)
        handler.close()


class TestOutputConfigStructuredLogs:
    def test_default_false(self):
        from src.models.config import OutputConfig

        cfg = OutputConfig()
        assert cfg.structured_logs is False

    def test_enable(self):
        from src.models.config import OutputConfig

        cfg = OutputConfig(structured_logs=True)
        assert cfg.structured_logs is True


# ============================================================================
# C5: Run report insights
# ============================================================================

from src.tools.report_writer import _build_run_insights_lines, write_markdown_report


class TestBuildRunInsightsLines:
    def _sample_summary(self) -> dict[str, Any]:
        return {
            "total_cost_usd": 2.3456,
            "total_calls": 47,
            "total_tokens": {
                "input": 150000,
                "output": 30000,
                "cache_read": 5000,
                "cache_write": 1000,
            },
            "avg_latency_s": 3.2,
            "by_agent": {
                "planner": {"calls": 3, "cost_usd": 0.89, "tokens": 50000},
                "judge": {"calls": 4, "cost_usd": 0.45, "tokens": 30000},
            },
            "by_phase": {},
            "by_model": {},
        }

    def test_generates_lines(self):
        t = partial(lambda lang, key: key, "en")
        lines = _build_run_insights_lines(t, self._sample_summary())
        text = "\n".join(lines)
        assert "Run Insights" in text or "run_insights" in text
        assert "47" in text
        assert "$2.3456" in text
        assert "planner" in text

    def test_empty_summary_returns_empty(self):
        t = partial(lambda lang, key: key, "en")
        lines = _build_run_insights_lines(t, {})
        assert lines == []

    def test_zero_calls_returns_empty(self):
        t = partial(lambda lang, key: key, "en")
        lines = _build_run_insights_lines(t, {"total_calls": 0})
        assert lines == []

    def test_no_agents(self):
        t = partial(lambda lang, key: key, "en")
        summary = {
            "total_cost_usd": 0.0,
            "total_calls": 1,
            "total_tokens": {"input": 100, "output": 50},
            "avg_latency_s": 1.0,
            "by_agent": {},
        }
        lines = _build_run_insights_lines(t, summary)
        assert any("1" in line for line in lines)


class TestWriteMarkdownReportWithCost:
    def test_includes_insights_when_provided(self, tmp_path):
        from src.models.config import MergeConfig

        config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/test")
        state = MagicMock()
        state.run_id = "test-run-123"
        state.status = MagicMock()
        state.status.value = "completed"
        state.created_at = datetime.now()
        state.updated_at = datetime.now()
        state.merge_plan = None
        state.file_decision_records = {}
        state.judge_verdict = None
        state.errors = []
        state.config = config

        cost_summary = {
            "total_cost_usd": 1.5,
            "total_calls": 10,
            "total_tokens": {"input": 5000, "output": 1000},
            "avg_latency_s": 2.0,
            "by_agent": {"planner": {"calls": 5, "cost_usd": 1.0, "tokens": 3000}},
        }

        path = write_markdown_report(state, str(tmp_path), cost_summary=cost_summary)
        content = path.read_text()
        assert "Run Insights" in content
        assert "$1.5" in content

    def test_no_insights_without_cost(self, tmp_path):
        config = MagicMock()
        config.output.language = "en"
        state = MagicMock()
        state.run_id = "test-run-456"
        state.status = MagicMock()
        state.status.value = "completed"
        state.created_at = datetime.now()
        state.updated_at = datetime.now()
        state.merge_plan = None
        state.file_decision_records = {}
        state.judge_verdict = None
        state.errors = []
        state.config = config

        path = write_markdown_report(state, str(tmp_path))
        content = path.read_text()
        assert "Run Insights" not in content


# ============================================================================
# C1+Orchestrator: hooks integration
# ============================================================================


class TestOrchestratorHooksProperty:
    def test_orchestrator_exposes_hooks(self):
        from src.core.orchestrator import Orchestrator
        from src.models.config import MergeConfig

        config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/test")
        mock_agents: dict[str, Any] = {}
        for name in [
            "planner",
            "planner_judge",
            "conflict_analyst",
            "executor",
            "judge",
            "human_interface",
        ]:
            agent = MagicMock()
            agent.set_trace_logger = MagicMock()
            agent.set_memory_store = MagicMock()
            agent.set_cost_tracker = MagicMock()
            mock_agents[name] = agent

        orch = Orchestrator(config, agents=mock_agents)
        assert isinstance(orch.hooks, HookManager)
        assert orch.hooks.handler_count == 0

        called: list[str] = []
        orch.hooks.on("test", lambda: called.append("yes"))
        assert orch.hooks.handler_count == 1


# ============================================================================
# LLMClient.update_api_key
# ============================================================================


class TestLLMClientUpdateApiKey:
    def test_base_class_has_method(self):
        from src.llm.client import LLMClient

        assert hasattr(LLMClient, "update_api_key")

    def test_anthropic_client_updates(self):
        from src.llm.client import AnthropicClient

        client = AnthropicClient(
            model="claude-opus-4-6",
            api_key="old-key",
            temperature=0.2,
            max_tokens=1024,
            max_retries=1,
        )
        client.update_api_key("new-key")
        assert client._client.api_key == "new-key"

    def test_openai_client_updates(self):
        from src.llm.client import OpenAIClient

        client = OpenAIClient(
            model="gpt-4o",
            api_key="old-key",
            temperature=0.2,
            max_tokens=1024,
            max_retries=1,
        )
        client.update_api_key("new-key")
        assert client._client.api_key == "new-key"
