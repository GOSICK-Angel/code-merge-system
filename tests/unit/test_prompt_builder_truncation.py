"""OPP-4: structured (head+tail) truncation instead of blind head-chopping.

When the staged renderer produces nothing (a large unparseable file whose
chunks all score below SIGNATURE_THRESHOLD), build_staged_content used to fall
back to ``content[:max_chars]`` — silently dropping the tail. It now routes
through ``_truncate_text(strategy="middle")`` so head AND tail survive with a
``[truncated]`` marker.
"""

from __future__ import annotations

from src.llm.prompt_builders import AgentPromptBuilder
from src.models.config import AgentLLMConfig


def _make_config() -> AgentLLMConfig:
    return AgentLLMConfig(
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        api_key_env="ANTHROPIC_API_KEY",
    )


def test_unanchored_large_file_keeps_head_and_tail():
    builder = AgentPromptBuilder(_make_config(), None)
    # >200 lines forces the staged path; no diff/conflict anchors and an
    # unknown language make every chunk score below SIGNATURE_THRESHOLD, so
    # the renderer emits nothing and the floor truncation fires.
    body = "\n".join(f"padding line number {i}" for i in range(250))
    content = f"FIRST_UNIQUE_LINE\n{body}\nLAST_UNIQUE_LINE\n"

    result = builder.build_staged_content(
        content,
        "config_blob.txt",
        diff_ranges=[],
        budget_tokens=50,
    )

    assert "FIRST_UNIQUE_LINE" in result
    assert "LAST_UNIQUE_LINE" in result
    assert "[truncated]" in result
    assert len(result) < len(content)


def test_small_file_tight_budget_uses_tail_truncation():
    # OPP-4 path 1: a small file (<200 lines, <8000 chars) under a tight budget
    # must route through _truncate_text('tail'), not a bare head slice — pins
    # the marker so a regression back to content[:max_chars] is caught.
    builder = AgentPromptBuilder(_make_config(), None)
    body = "\n".join(f"line {i}" for i in range(50))
    content = f"FIRST_LINE\n{body}\nLAST_LINE\n"
    assert len(content) < 8000 and content.count("\n") < 200

    # budget_tokens=20 -> max_chars=70, comfortably above the truncation
    # marker length so the 'tail' strategy emits the marker (and not a bare
    # head slice), yet far below the ~400-char content so it truncates.
    result = builder.build_staged_content(
        content, "config.txt", diff_ranges=[], budget_tokens=20
    )

    assert "FIRST_LINE" in result
    assert "[truncated]" in result
    assert len(result) < len(content)
