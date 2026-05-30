"""P2 (Wave 4): truncation fail-closed on the still-blind consumers.

#3A hardened only the Judge *batch* review. The default per-file Judge review,
the commit-round analysis, and the analyst still parsed a truncated/malformed LLM
response as "no issues"/empty — a broken file silently rolled into a PASS. P2
mirrors the proven #3A ``strict_json`` shape at the parser layer (orthogonal to
the structured-output path) and converts the per-file Judge except into a
blocking veto.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.llm.client import ParseError
from src.llm.response_parser import (
    parse_commit_round_analyses,
    parse_file_review_issues,
)
from src.models.config import AgentLLMConfig
from src.models.decision import DecisionSource, FileDecisionRecord, MergeDecision
from src.models.diff import FileChangeCategory, FileDiff, FileStatus, RiskLevel
from src.models.judge import IssueSeverity


# --------------------------------------------------------------------------- #
# parser-level strict_json (mirrors #3A)
# --------------------------------------------------------------------------- #
class TestParserStrictJson:
    def test_file_review_strict_raises_on_unparseable(self) -> None:
        with pytest.raises(ParseError):
            parse_file_review_issues("totally not json {{{", "a.ts", strict_json=True)

    def test_file_review_lenient_returns_empty(self) -> None:
        assert parse_file_review_issues("totally not json {{{", "a.ts") == []

    def test_commit_round_strict_raises_on_unparseable(self) -> None:
        with pytest.raises(ParseError):
            parse_commit_round_analyses("<<garbage>>", ["a.ts"], strict_json=True)

    def test_commit_round_lenient_returns_empty(self) -> None:
        assert parse_commit_round_analyses("<<garbage>>", ["a.ts"]) == {}

    def test_valid_json_still_parses_under_strict(self) -> None:
        # well-formed empty review → no issues, no raise
        assert (
            parse_file_review_issues('{"issues": []}', "a.ts", strict_json=True) == []
        )


# --------------------------------------------------------------------------- #
# judge review_file fails CLOSED (the real fail-open this closes)
# --------------------------------------------------------------------------- #
class TestReviewFileFailsClosed:
    def _judge(self, tmp_path):
        from src.agents.judge_agent import JudgeAgent

        git = MagicMock()
        git.repo_path = tmp_path
        git.get_file_content.return_value = "x = 1\n"
        with patch("src.llm.client.LLMClientFactory.create", return_value=MagicMock()):
            return JudgeAgent(llm_config=AgentLLMConfig(), git_tool=git)

    def _record(self) -> FileDecisionRecord:
        return FileDecisionRecord(
            file_path="a.py",
            file_status=FileStatus.MODIFIED,
            decision=MergeDecision.SEMANTIC_MERGE,
            decision_source=DecisionSource.AUTO_EXECUTOR,
            confidence=0.9,
            rationale="t",
            timestamp=datetime.now(),
        )

    def _fd(self) -> FileDiff:
        return FileDiff(
            file_path="a.py",
            file_status=FileStatus.MODIFIED,
            risk_level=RiskLevel.AUTO_SAFE,
            risk_score=0.1,
            change_category=FileChangeCategory.C,
            lines_added=10,
            lines_deleted=2,
        )

    async def test_truncated_review_synthesizes_veto(self, tmp_path) -> None:
        judge = self._judge(tmp_path)
        # syntactically-clean merged content so the only issue can be the veto
        judge._call_llm_with_retry = AsyncMock(return_value="NOT JSON AT ALL {{{")

        issues = await judge.review_file(
            "a.py",
            "x = 1\n",
            self._record(),
            self._fd(),
        )

        vetoes = [i for i in issues if i.issue_type == "review_unavailable"]
        assert len(vetoes) == 1
        assert vetoes[0].issue_level == IssueSeverity.CRITICAL
        assert vetoes[0].veto_condition
        assert vetoes[0].must_fix_before_merge

    async def test_clean_review_has_no_veto(self, tmp_path) -> None:
        judge = self._judge(tmp_path)
        judge._call_llm_with_retry = AsyncMock(return_value='{"issues": []}')

        issues = await judge.review_file(
            "a.py",
            "x = 1\n",
            self._record(),
            self._fd(),
        )
        assert [i for i in issues if i.issue_type == "review_unavailable"] == []
