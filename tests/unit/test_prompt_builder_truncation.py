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
