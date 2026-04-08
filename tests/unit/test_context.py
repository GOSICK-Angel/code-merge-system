"""Tests for LLM context management."""

import pytest

from src.llm.context import (
    ContextAssembler,
    ContextPriority,
    ContextSection,
    TokenBudget,
    _truncate_text,
    estimate_tokens,
    get_context_window,
)
from src.llm.prompt_builders import AgentPromptBuilder


class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_known_text(self):
        text = "a" * 350
        tokens = estimate_tokens(text)
        assert 90 <= tokens <= 110

    def test_code_snippet(self):
        code = "def foo(x: int) -> int:\n    return x * 2\n"
        tokens = estimate_tokens(code)
        assert tokens > 0


class TestGetContextWindow:
    def test_known_model(self):
        assert get_context_window("claude-opus-4-6") == 200_000

    def test_openai_model(self):
        assert get_context_window("gpt-4o") == 128_000

    def test_unknown_model_fallback(self):
        assert get_context_window("unknown-model-xyz") == 8_000

    def test_prefix_match(self):
        assert get_context_window("claude-opus-4-6-latest") == 200_000


class TestTokenBudget:
    def test_available_calculation(self):
        budget = TokenBudget(
            model="gpt-4o",
            context_window=128_000,
            reserved_for_output=8_192,
        )
        assert budget.available > 0
        assert budget.available < 128_000

    def test_consume_immutable(self):
        budget = TokenBudget(
            model="gpt-4o",
            context_window=128_000,
            reserved_for_output=8_192,
        )
        new_budget = budget.consume(1000)
        assert new_budget.used == 1000
        assert budget.used == 0

    def test_can_fit(self):
        budget = TokenBudget(
            model="gpt-4o",
            context_window=10_000,
            reserved_for_output=2_000,
        )
        assert budget.can_fit(5_000)
        assert not budget.can_fit(100_000)

    def test_frozen(self):
        budget = TokenBudget(
            model="gpt-4o",
            context_window=10_000,
            reserved_for_output=2_000,
        )
        with pytest.raises(Exception):
            budget.used = 500


class TestTruncateText:
    def test_no_truncation_needed(self):
        text = "short text"
        assert _truncate_text(text, 100, "tail") == text

    def test_tail_truncation(self):
        text = "a" * 1000
        result = _truncate_text(text, 200, "tail")
        assert len(result) <= 200
        assert "[truncated]" in result

    def test_head_truncation(self):
        text = "a" * 1000
        result = _truncate_text(text, 200, "head")
        assert "[truncated]" in result
        assert result.endswith("a")

    def test_middle_truncation(self):
        text = "A" * 500 + "B" * 500
        result = _truncate_text(text, 200, "middle")
        assert "[truncated]" in result
        assert result.startswith("A")
        assert result.endswith("B")


class TestContextAssembler:
    def test_all_sections_fit(self):
        budget = TokenBudget(
            model="claude-opus-4-6",
            context_window=200_000,
            reserved_for_output=8_192,
        )
        assembler = ContextAssembler(budget)
        assembler.add_section(
            ContextSection(
                name="system",
                content="You are a helpful AI.",
                priority=ContextPriority.CRITICAL,
            )
        )
        assembler.add_section(
            ContextSection(
                name="user", content="Hello world", priority=ContextPriority.HIGH
            )
        )
        result, final_budget = assembler.build()
        assert "You are a helpful AI." in result
        assert "Hello world" in result
        assert final_budget.used > 0

    def test_low_priority_dropped_first(self):
        budget = TokenBudget(
            model="gpt-4",
            context_window=100,
            reserved_for_output=10,
        )
        assembler = ContextAssembler(budget)
        assembler.add_section(
            ContextSection(
                name="critical",
                content="MUST KEEP",
                priority=ContextPriority.CRITICAL,
            )
        )
        assembler.add_section(
            ContextSection(
                name="optional",
                content="x" * 500,
                priority=ContextPriority.OPTIONAL,
                can_truncate=True,
            )
        )
        result, _ = assembler.build()
        assert "MUST KEEP" in result

    def test_priority_ordering(self):
        budget = TokenBudget(
            model="claude-opus-4-6",
            context_window=200_000,
            reserved_for_output=8_192,
        )
        assembler = ContextAssembler(budget)
        assembler.add_section(
            ContextSection(name="low", content="LOW", priority=ContextPriority.LOW)
        )
        assembler.add_section(
            ContextSection(
                name="critical", content="CRITICAL", priority=ContextPriority.CRITICAL
            )
        )
        assembler.add_section(
            ContextSection(name="high", content="HIGH", priority=ContextPriority.HIGH)
        )
        result, _ = assembler.build()
        crit_pos = result.index("CRITICAL")
        high_pos = result.index("HIGH")
        low_pos = result.index("LOW")
        assert crit_pos < high_pos < low_pos

    def test_empty_assembler(self):
        budget = TokenBudget(
            model="gpt-4o",
            context_window=10_000,
            reserved_for_output=2_000,
        )
        assembler = ContextAssembler(budget)
        result, final_budget = assembler.build()
        assert result == ""
        assert final_budget.used == 0

    def test_truncation_strategy_applied(self):
        budget = TokenBudget(
            model="gpt-4",
            context_window=50,
            reserved_for_output=5,
        )
        assembler = ContextAssembler(budget)
        assembler.add_section(
            ContextSection(
                name="critical",
                content="OK",
                priority=ContextPriority.CRITICAL,
            )
        )
        assembler.add_section(
            ContextSection(
                name="big",
                content="X" * 500,
                priority=ContextPriority.LOW,
                can_truncate=True,
                truncation_strategy="middle",
            )
        )
        result, _ = assembler.build()
        assert "OK" in result


class TestBuildStagedContent:
    """Tests for AgentPromptBuilder.build_staged_content integration."""

    def _make_builder(self):
        from unittest.mock import MagicMock

        from src.models.config import AgentLLMConfig

        config = AgentLLMConfig(
            provider="anthropic",
            model="claude-opus-4-6",
            api_key_env="ANTHROPIC_API_KEY",
            max_tokens=4096,
        )
        return AgentPromptBuilder(config, memory_store=None)

    def test_build_staged_content_small_file_passthrough(self):
        from src.llm.prompt_builders import AgentPromptBuilder

        builder = self._make_builder()
        small_content = "def foo():\n    return 1\n"
        result = builder.build_staged_content(
            content=small_content,
            file_path="test.py",
            diff_ranges=[(1, 2)],
            budget_tokens=10000,
        )
        assert result == small_content

    def test_build_staged_content_large_file_uses_staging(self):
        from src.llm.prompt_builders import AgentPromptBuilder

        builder = self._make_builder()
        lines = [f"def func_{i}():\n    return {i}\n" for i in range(300)]
        large_content = "\n".join(lines)
        result = builder.build_staged_content(
            content=large_content,
            file_path="big_module.py",
            diff_ranges=[(1, 10)],
            budget_tokens=500,
        )
        assert len(result) < len(large_content)

    def test_build_staged_content_respects_char_threshold(self):
        from src.llm.prompt_builders import AgentPromptBuilder

        builder = self._make_builder()
        content = "x = 1\n" * 100
        assert len(content) < 15000
        assert content.count("\n") < 500
        result = builder.build_staged_content(
            content=content,
            file_path="small.py",
            diff_ranges=[(1, 5)],
            budget_tokens=50000,
        )
        assert result == content
