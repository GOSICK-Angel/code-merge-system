"""Tests for Phase B (B1-B4): Prompt caching, context compression, agent registry, config extension."""

from __future__ import annotations

import copy
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ============================================================================
# B1: Prompt caching
# ============================================================================

from src.llm.prompt_caching import CacheStrategy, apply_cache_markers


class TestCacheStrategy:
    def test_enum_values(self):
        assert CacheStrategy.NONE == "none"
        assert CacheStrategy.SYSTEM_ONLY == "system_only"
        assert CacheStrategy.SYSTEM_AND_RECENT == "system_and_recent"

    def test_string_conversion(self):
        assert CacheStrategy("none") is CacheStrategy.NONE
        assert CacheStrategy("system_and_recent") is CacheStrategy.SYSTEM_AND_RECENT


class TestApplyCacheMarkers:
    def test_none_strategy_returns_unchanged(self):
        msgs = [{"role": "user", "content": "hello"}]
        result_msgs, result_sys = apply_cache_markers(
            msgs, system="sys", strategy=CacheStrategy.NONE
        )
        assert result_msgs is msgs
        assert result_sys == "sys"

    def test_system_only_marks_system(self):
        msgs = [{"role": "user", "content": "hello"}]
        result_msgs, result_sys = apply_cache_markers(
            msgs, system="You are helpful", strategy=CacheStrategy.SYSTEM_ONLY
        )
        assert isinstance(result_sys, list)
        assert result_sys[0]["text"] == "You are helpful"
        assert result_sys[0]["cache_control"] == {"type": "ephemeral"}
        assert result_msgs[0].get("content") == "hello"

    def test_system_only_does_not_mark_messages(self):
        msgs = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
        ]
        result_msgs, _ = apply_cache_markers(
            msgs, system="sys", strategy=CacheStrategy.SYSTEM_ONLY
        )
        for m in result_msgs:
            assert isinstance(m["content"], str)

    def test_system_and_recent_marks_system_and_last_n(self):
        msgs = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
            {"role": "assistant", "content": "d"},
            {"role": "user", "content": "e"},
        ]
        result_msgs, result_sys = apply_cache_markers(
            msgs,
            system="sys",
            strategy=CacheStrategy.SYSTEM_AND_RECENT,
            recent_count=3,
        )
        assert isinstance(result_sys, list)
        assert result_sys[0]["cache_control"] == {"type": "ephemeral"}

        assert isinstance(result_msgs[0]["content"], str)
        assert isinstance(result_msgs[1]["content"], str)

        for m in result_msgs[-3:]:
            content = m["content"]
            assert isinstance(content, list)
            assert content[-1].get("cache_control") == {"type": "ephemeral"}

    def test_no_system_returns_none(self):
        msgs = [{"role": "user", "content": "hello"}]
        _, result_sys = apply_cache_markers(
            msgs, system=None, strategy=CacheStrategy.SYSTEM_AND_RECENT
        )
        assert result_sys is None

    def test_does_not_mutate_original(self):
        msgs = [{"role": "user", "content": "hello"}]
        original = copy.deepcopy(msgs)
        apply_cache_markers(
            msgs, system="sys", strategy=CacheStrategy.SYSTEM_AND_RECENT
        )
        assert msgs == original

    def test_recent_count_zero(self):
        msgs = [{"role": "user", "content": "a"}]
        result_msgs, _ = apply_cache_markers(
            msgs,
            system="sys",
            strategy=CacheStrategy.SYSTEM_AND_RECENT,
            recent_count=0,
        )
        assert isinstance(result_msgs[0]["content"], str)

    def test_list_content_marks_last_block(self):
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "block1"},
                    {"type": "text", "text": "block2"},
                ],
            }
        ]
        result_msgs, _ = apply_cache_markers(
            msgs,
            system=None,
            strategy=CacheStrategy.SYSTEM_AND_RECENT,
            recent_count=1,
        )
        blocks = result_msgs[0]["content"]
        assert "cache_control" not in blocks[0]
        assert blocks[1]["cache_control"] == {"type": "ephemeral"}


# ============================================================================
# B2: Context compressor
# ============================================================================

from src.llm.context import TokenBudget
from src.llm.context_compressor import ContextCompressor, CompressionStats


class TestCompressionStats:
    def test_total_saved(self):
        stats = CompressionStats(
            tokens_before=100,
            tokens_after=60,
            phase1_saved=20,
            phase2_saved=15,
            phase3_saved=5,
        )
        assert stats.total_saved == 40


class TestContextCompressorFitsWithinBudget:
    def test_no_compression_needed(self):
        budget = TokenBudget(
            model="gpt-4o", context_window=128_000, reserved_for_output=8192
        )
        msgs = [{"role": "user", "content": "short message"}]
        compressor = ContextCompressor(budget)
        result, stats = compressor.compress(msgs)
        assert result == msgs
        assert stats.total_saved == 0


class TestContextCompressorPhase1:
    def test_prunes_stale_long_outputs(self):
        budget = TokenBudget(
            model="gpt-4o", context_window=500, reserved_for_output=100
        )
        msgs = [
            {"role": "user", "content": "initial"},
            {"role": "assistant", "content": "x" * 500},
            {"role": "assistant", "content": "y" * 500},
            {"role": "assistant", "content": "z" * 500},
            {"role": "user", "content": "recent1"},
            {"role": "user", "content": "recent2"},
            {"role": "user", "content": "recent3"},
            {"role": "user", "content": "recent4"},
        ]
        compressor = ContextCompressor(
            budget,
            protect_head=1,
            protect_tail=4,
            stale_char_threshold=200,
            stale_age=1,
        )
        result, stats = compressor.compress(msgs)
        assert result[1]["content"] != "x" * 500, "oldest stale output pruned"
        assert result[2]["content"] != "y" * 500, "second stale output pruned"
        assert stats.tokens_after < stats.tokens_before

    def test_preserves_head_and_tail(self):
        budget = TokenBudget(
            model="gpt-4o", context_window=500, reserved_for_output=100
        )
        msgs = [
            {"role": "user", "content": "head_msg"},
            {"role": "assistant", "content": "a" * 1000},
            {"role": "user", "content": "tail_msg"},
        ]
        compressor = ContextCompressor(budget, protect_head=1, protect_tail=1)
        result, _ = compressor.compress(msgs)
        assert result[0]["content"] == "head_msg"
        assert result[-1]["content"] == "tail_msg"


class TestContextCompressorPhase2:
    def test_truncates_middle_messages(self):
        budget = TokenBudget(model="gpt-4o", context_window=300, reserved_for_output=50)
        msgs = [
            {"role": "user", "content": "head"},
            {"role": "user", "content": "a" * 2000},
            {"role": "user", "content": "b" * 2000},
            {"role": "user", "content": "tail"},
        ]
        compressor = ContextCompressor(budget, protect_head=1, protect_tail=1)
        result, stats = compressor.compress(msgs)
        assert result[0]["content"] == "head"
        assert result[-1]["content"] == "tail"
        assert stats.tokens_after < stats.tokens_before


class TestContextCompressorPhase3:
    def test_drops_middle_when_extreme_pressure(self):
        budget = TokenBudget(model="gpt-4o", context_window=100, reserved_for_output=20)
        msgs = [
            {"role": "user", "content": "head"},
            {"role": "user", "content": "x" * 5000},
            {"role": "user", "content": "y" * 5000},
            {"role": "user", "content": "z" * 5000},
            {"role": "user", "content": "tail"},
        ]
        compressor = ContextCompressor(budget, protect_head=1, protect_tail=1)
        result, stats = compressor.compress(msgs)
        assert result[0]["content"] == "head"
        assert result[-1]["content"] == "tail"
        assert stats.phase3_saved > 0


# ============================================================================
# B3: Agent registry
# ============================================================================

from src.agents.registry import AgentRegistry
from src.agents.base_agent import BaseAgent
from src.models.config import AgentLLMConfig, MergeConfig
from src.models.message import AgentType


def _dummy_agent_class(at: AgentType = AgentType.PLANNER) -> type[BaseAgent]:
    class DummyAgent(BaseAgent):
        agent_type = at

        def __init__(self, llm_config: AgentLLMConfig, **kwargs: Any):
            with patch("src.agents.base_agent.LLMClientFactory") as factory:
                factory.create.return_value = MagicMock()
                super().__init__(llm_config)
            self.extra_kwargs = kwargs

        async def run(self, state: Any) -> Any:
            return None

        def can_handle(self, state: Any) -> bool:
            return True

    return DummyAgent


def _ensure_agents_registered() -> None:
    """Force-import all agent modules to trigger self-registration."""
    import src.agents.planner_agent  # noqa: F401
    import src.agents.planner_judge_agent  # noqa: F401
    import src.agents.conflict_analyst_agent  # noqa: F401
    import src.agents.executor_agent  # noqa: F401
    import src.agents.judge_agent  # noqa: F401
    import src.agents.human_interface_agent  # noqa: F401


class TestAgentRegistry:
    def setup_method(self) -> None:
        _ensure_agents_registered()
        self._backup_factories = dict(AgentRegistry._factories)
        self._backup_extra = dict(AgentRegistry._extra_kwargs_map)

    def teardown_method(self) -> None:
        AgentRegistry._factories = self._backup_factories
        AgentRegistry._extra_kwargs_map = self._backup_extra

    def test_register_and_create(self):
        Dummy = _dummy_agent_class()
        AgentRegistry.register("test_agent", Dummy)
        config = AgentLLMConfig(
            provider="openai", model="gpt-4o-mini", api_key_env="OPENAI_API_KEY"
        )
        agent = AgentRegistry.create("test_agent", config)
        assert isinstance(agent, BaseAgent)

    def test_create_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown agent"):
            AgentRegistry.create("nonexistent", AgentLLMConfig())

    def test_registered_names(self):
        _ensure_agents_registered()
        assert "planner" in AgentRegistry.registered_names()
        assert "executor" in AgentRegistry.registered_names()
        assert "judge" in AgentRegistry.registered_names()

    def test_is_registered(self):
        _ensure_agents_registered()
        assert AgentRegistry.is_registered("planner")
        assert not AgentRegistry.is_registered("nonexistent_agent_xyz")

    def test_create_all_with_dummy(self):
        AgentRegistry.clear()
        names = [
            "planner",
            "planner_judge",
            "conflict_analyst",
            "executor",
            "judge",
            "human_interface",
        ]
        for name in names:
            AgentRegistry.register(name, _dummy_agent_class())

        config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/test")
        agents = AgentRegistry.create_all(config)
        assert set(agents.keys()) == set(names)
        for agent in agents.values():
            assert isinstance(agent, BaseAgent)

    def test_extra_kwargs_forwarded(self):
        Dummy = _dummy_agent_class()
        AgentRegistry.register("test_with_kwargs", Dummy, extra_kwargs=["git_tool"])

        config = AgentLLMConfig(
            provider="openai", model="gpt-4o-mini", api_key_env="OPENAI_API_KEY"
        )
        mock_git = MagicMock()
        agent = AgentRegistry.create("test_with_kwargs", config, git_tool=mock_git)
        assert agent.extra_kwargs["git_tool"] is mock_git

    def test_clear_removes_all(self):
        AgentRegistry.register("temp", _dummy_agent_class())
        assert AgentRegistry.is_registered("temp")
        AgentRegistry.clear()
        assert not AgentRegistry.is_registered("temp")


class TestAllAgentsSelfRegistered:
    """Verify that importing agent modules triggers self-registration."""

    def test_all_six_agents_registered(self):
        _ensure_agents_registered()
        expected = {
            "planner",
            "planner_judge",
            "conflict_analyst",
            "executor",
            "judge",
            "human_interface",
        }
        assert expected.issubset(set(AgentRegistry.registered_names()))

    def test_git_tool_agents_have_extra_kwargs(self):
        _ensure_agents_registered()
        for name in ["conflict_analyst", "executor", "judge"]:
            assert "git_tool" in AgentRegistry._extra_kwargs_map.get(name, []), (
                f"{name} should declare git_tool as extra kwarg"
            )

    def test_non_git_agents_have_no_extra_kwargs(self):
        _ensure_agents_registered()
        for name in ["planner", "planner_judge", "human_interface"]:
            assert AgentRegistry._extra_kwargs_map.get(name, []) == [], (
                f"{name} should not have extra kwargs"
            )


# ============================================================================
# B4: AgentLLMConfig extension
# ============================================================================

from src.models.config import CompressionConfig


class TestCompressionConfig:
    def test_defaults(self):
        cc = CompressionConfig()
        assert cc.protect_head_tokens == 4000
        assert cc.protect_tail_tokens == 20000
        assert cc.stale_output_threshold == 200
        assert cc.summary_budget_ratio == 0.05

    def test_custom_values(self):
        cc = CompressionConfig(protect_head_tokens=8000, stale_output_threshold=500)
        assert cc.protect_head_tokens == 8000
        assert cc.stale_output_threshold == 500


class TestAgentLLMConfigExtension:
    def test_cache_strategy_default(self):
        config = AgentLLMConfig()
        assert config.cache_strategy == "system_and_recent"

    def test_cache_strategy_none(self):
        config = AgentLLMConfig(cache_strategy="none")
        assert config.cache_strategy == "none"

    def test_cache_strategy_system_only(self):
        config = AgentLLMConfig(cache_strategy="system_only")
        assert config.cache_strategy == "system_only"

    def test_compression_default(self):
        config = AgentLLMConfig()
        assert isinstance(config.compression, CompressionConfig)
        assert config.compression.protect_head_tokens == 4000

    def test_compression_custom(self):
        config = AgentLLMConfig(
            compression=CompressionConfig(protect_tail_tokens=10000)
        )
        assert config.compression.protect_tail_tokens == 10000

    def test_yaml_round_trip(self):
        config = AgentLLMConfig(
            provider="anthropic",
            model="claude-opus-4-6",
            cache_strategy="system_only",
            compression=CompressionConfig(stale_output_threshold=300),
        )
        data = config.model_dump()
        restored = AgentLLMConfig.model_validate(data)
        assert restored.cache_strategy == "system_only"
        assert restored.compression.stale_output_threshold == 300

    def test_backward_compatible_without_new_fields(self):
        data = {
            "provider": "openai",
            "model": "gpt-4o",
            "api_key_env": "OPENAI_API_KEY",
        }
        config = AgentLLMConfig.model_validate(data)
        assert config.cache_strategy == "system_and_recent"
        assert config.compression.protect_head_tokens == 4000


# ============================================================================
# B3+Orchestrator: registry integration
# ============================================================================


class TestOrchestratorRegistryIntegration:
    def test_orchestrator_accepts_injected_agents(self):
        from src.core.orchestrator import Orchestrator

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
            agent = MagicMock(spec=BaseAgent)
            agent.set_trace_logger = MagicMock()
            agent.set_memory_store = MagicMock()
            mock_agents[name] = agent

        orch = Orchestrator(config, agents=mock_agents)
        assert orch.planner is mock_agents["planner"]
        assert orch.judge is mock_agents["judge"]
        assert orch.human_interface is mock_agents["human_interface"]
