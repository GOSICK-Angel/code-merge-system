"""方案5: judge's deterministic duplicate-top-level-symbol veto.

A chunked semantic merge can emit the same top-level declaration twice (the
zod failure: ZodNumberFormat declared 2x → uncompilable). The judge now flags
this deterministically with a ``veto_condition`` CRITICAL issue, which (a)
excludes the file from the O-J1 high-confidence skip and (b) forces FAIL
without being downgradable — duplicate declarations are uncompilable
regardless of fork intent.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.agents.judge_agent import JudgeAgent
from src.models.config import AgentLLMConfig
from src.models.judge import IssueSeverity


def _judge_with_repo(repo: Path) -> JudgeAgent:
    git_tool = MagicMock()
    git_tool.repo_path = repo
    with patch("src.llm.client.LLMClientFactory.create"):
        return JudgeAgent(AgentLLMConfig(), git_tool=git_tool)


def _state(paths: list[str]) -> SimpleNamespace:
    # Only the keys of file_decision_records are read by the check.
    return SimpleNamespace(file_decision_records={p: object() for p in paths})


class TestCheckDuplicateSymbols:
    def test_duplicate_const_vetoes(self, tmp_path: Path) -> None:
        (tmp_path / "schemas.ts").write_text(
            "export const ZodNumberFormat = a();\n"
            "export const ZodNumberFormat = a();\n",
            encoding="utf-8",
        )
        agent = _judge_with_repo(tmp_path)
        issues = agent._check_duplicate_symbols(_state(["schemas.ts"]))
        assert len(issues) == 1
        assert issues[0].issue_level == IssueSeverity.CRITICAL
        assert issues[0].issue_type == "duplicate_top_level_symbol"
        assert issues[0].veto_condition
        assert issues[0].must_fix_before_merge
        assert "ZodNumberFormat" in issues[0].description

    def test_clean_file_no_issue(self, tmp_path: Path) -> None:
        (tmp_path / "ok.ts").write_text(
            "export const a = 1;\nexport const b = 2;\n", encoding="utf-8"
        )
        agent = _judge_with_repo(tmp_path)
        assert agent._check_duplicate_symbols(_state(["ok.ts"])) == []

    def test_missing_file_skipped(self, tmp_path: Path) -> None:
        agent = _judge_with_repo(tmp_path)
        assert agent._check_duplicate_symbols(_state(["gone.ts"])) == []

    def test_no_git_tool_returns_empty(self) -> None:
        with patch("src.llm.client.LLMClientFactory.create"):
            agent = JudgeAgent(AgentLLMConfig(), git_tool=None)
        assert agent._check_duplicate_symbols(_state(["x.ts"])) == []
