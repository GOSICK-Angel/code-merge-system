"""P4 (Wave 4): config preflight advisories.

The preflight surfaces high-sensitivity config that silently degrades merge
behavior — undersized max_tokens (chunked merges self-truncate-and-escalate),
reasoning-model budget floor, and a missing compile gate (the always-on syntax
gate is balance-only). These tests pin: it stays silent on a well-provisioned
config and fires exactly on each risky shape.
"""

from __future__ import annotations

from src.cli.preflight import config_preflight_warnings
from src.models.config import (
    AgentLLMConfig,
    BuildCheckConfig,
    GateCommandConfig,
    GateConfig,
    MergeConfig,
)


def _cfg(**overrides) -> MergeConfig:
    return MergeConfig(
        upstream_ref="upstream/main", fork_ref="feature/fork", **overrides
    )


def _has(warnings: list[str], needle: str) -> bool:
    return any(needle in w for w in warnings)


# --------------------------------------------------------------------------- #
# chunk / max_tokens self-truncation
# --------------------------------------------------------------------------- #
class TestChunkTruncationWarning:
    def test_default_executor_budget_does_not_warn(self) -> None:
        # default executor max_tokens=32768, chunk_size_chars=20000:
        # 20000 < 1.4*32768 → ample headroom → no chunk warning.
        warnings = config_preflight_warnings(_cfg())
        assert not _has(warnings, "chunk_size_chars=")

    def test_small_executor_max_tokens_warns(self) -> None:
        cfg = _cfg(chunk_size_chars=20000)
        # a deepseek-style proxy executor at 8192: 20000 >= 1.4*8192 (=11468)
        cfg.agents.executor = AgentLLMConfig(
            provider="openai_compatible",
            model="deepseek-v4-pro",
            max_tokens=8192,
            api_key_env="OPENAI_API_KEY",
        )
        warnings = config_preflight_warnings(cfg)
        assert _has(warnings, "chunk_size_chars=20000")
        assert _has(warnings, "max_tokens")

    def test_small_max_tokens_but_small_chunk_does_not_warn(self) -> None:
        cfg = _cfg(chunk_size_chars=6000)
        cfg.agents.executor = AgentLLMConfig(
            provider="openai_compatible",
            model="deepseek-v4-pro",
            max_tokens=8192,
            api_key_env="OPENAI_API_KEY",
        )
        # 6000 < 1.4*8192 (=11468) → the configured chunk stands with headroom.
        warnings = config_preflight_warnings(cfg)
        assert not _has(warnings, "chunk_size_chars=")

    def test_tiny_analyst_max_tokens_warns(self) -> None:
        cfg = _cfg()
        cfg.agents.conflict_analyst = AgentLLMConfig(
            provider="anthropic",
            model="claude-haiku-4-5",
            max_tokens=1024,
            api_key_env="ANTHROPIC_API_KEY",
        )
        warnings = config_preflight_warnings(cfg)
        assert _has(warnings, "conflict_analyst.max_tokens=1024")


# --------------------------------------------------------------------------- #
# reasoning-model floor
# --------------------------------------------------------------------------- #
class TestReasoningFloorWarning:
    def test_reasoning_model_below_floor_warns(self) -> None:
        cfg = _cfg()
        cfg.agents.planner = AgentLLMConfig(
            provider="openai",
            model="o3-mini",
            max_tokens=8192,
            api_key_env="OPENAI_API_KEY",
        )
        warnings = config_preflight_warnings(cfg)
        assert _has(warnings, "reasoning model 'o3-mini'")
        assert _has(warnings, "32768")

    def test_reasoning_model_at_floor_does_not_warn(self) -> None:
        cfg = _cfg()
        cfg.agents.planner = AgentLLMConfig(
            provider="openai",
            model="o3-mini",
            max_tokens=32768,
            api_key_env="OPENAI_API_KEY",
        )
        warnings = config_preflight_warnings(cfg)
        assert not _has(warnings, "reasoning model 'o3-mini'")

    def test_non_reasoning_model_does_not_warn(self) -> None:
        cfg = _cfg()
        cfg.agents.judge = AgentLLMConfig(
            provider="anthropic",
            model="claude-opus-4-6",
            max_tokens=2048,
            api_key_env="ANTHROPIC_API_KEY",
        )
        warnings = config_preflight_warnings(cfg)
        assert not _has(warnings, "reasoning model 'claude-opus-4-6'")


# --------------------------------------------------------------------------- #
# compile gate advisory
# --------------------------------------------------------------------------- #
class TestCompileGateWarning:
    def test_no_gate_warns(self) -> None:
        warnings = config_preflight_warnings(_cfg())
        assert _has(warnings, "no build_check or gate command")

    def test_build_check_configured_suppresses(self) -> None:
        cfg = _cfg(build_check=BuildCheckConfig(enabled=True, command="tsc --noEmit"))
        warnings = config_preflight_warnings(cfg)
        assert not _has(warnings, "no build_check or gate command")

    def test_gate_command_suppresses(self) -> None:
        cfg = _cfg(
            gate=GateConfig(
                enabled=True,
                commands=[
                    GateCommandConfig(
                        name="typecheck",
                        command="tsc --noEmit",
                        baseline_parser="tsc_errors",
                    )
                ],
            )
        )
        warnings = config_preflight_warnings(cfg)
        assert not _has(warnings, "no build_check or gate command")

    def test_empty_gate_command_does_not_suppress(self) -> None:
        cfg = _cfg(
            gate=GateConfig(
                enabled=True,
                commands=[GateCommandConfig(name="noop", command="  ")],
            )
        )
        warnings = config_preflight_warnings(cfg)
        assert _has(warnings, "no build_check or gate command")
