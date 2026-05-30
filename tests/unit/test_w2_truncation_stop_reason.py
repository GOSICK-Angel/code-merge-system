"""W5 W2: provider-truncation fail-closed on the legacy-parser consumers.

P2 (Wave 4) re-raised ParseError only when truncation *broke* the JSON. But
``_extract_json`` salvages an earlier balanced object via find('{')/rfind('}'),
so a truncation that still left valid JSON is partial-but-valid and slips past
``strict_json``. The only remaining signal is ``LLMResponse.stop_reason`` — read
today only by the executor's ``parse_merge_result``. W2 threads a stop_reason
gate (mirroring that gate #1) into the per-file Judge, batch Judge, commit-round,
and single/chunked analyst parsers, and routes their 5 call sites through the
``_return_meta`` path so the ``LLMResponse`` (with stop_reason) reaches them.

These tests pin the parser-level gate (truncated-but-parseable ⇒ ParseError under
strict_json; clean stop_reason ⇒ normal parse) and the agent wiring (a truncated
per-file review vetoes; a truncated analyst escalates).
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.llm.client import LLMResponse, ParseError
from src.llm.response_parser import (
    parse_batch_file_review_issues,
    parse_commit_round_analyses,
    parse_conflict_analysis,
    parse_file_review_issues,
)
from src.models.config import AgentLLMConfig
from src.models.decision import DecisionSource, FileDecisionRecord, MergeDecision
from src.models.diff import FileChangeCategory, FileDiff, FileStatus, RiskLevel
from src.models.judge import IssueSeverity

# A perfectly *parseable* JSON object — strict_json alone would NOT raise on it.
# The point: stop_reason="max_tokens" means the model hit the ceiling *after*
# emitting this object, so the result is truncated even though it parses.
ISSUES_JSON = (
    '{"issues": [{"issue_level": "high", "issue_type": "logic_error", '
    '"description": "a real issue", "affected_lines": [1]}]}'
)
COMMIT_JSON = (
    '{"files": [{"file_path": "f.py", "conflict_type": "unknown", '
    '"recommended_strategy": "escalate_human", "confidence": 0.8}]}'
)
CONFLICT_JSON = (
    '{"conflict_type": "unknown", "recommended_strategy": "escalate_human", '
    '"confidence": 0.8, "rationale": "x"}'
)
BATCH_JSON = (
    '{"files": [{"file_path": "f.ts", "issues": [{"issue_level": "high", '
    '"issue_type": "x", "description": "d", "affected_lines": [1]}]}]}'
)


def _resp(text: str, stop: str | None) -> LLMResponse:
    return LLMResponse(text=text, stop_reason=stop)


class TestFileReviewGate:
    def test_max_tokens_raises_under_strict(self) -> None:
        with pytest.raises(ParseError):
            parse_file_review_issues(
                _resp(ISSUES_JSON, "max_tokens"), "f.ts", strict_json=True
            )

    def test_length_raises_under_strict(self) -> None:
        with pytest.raises(ParseError):
            parse_file_review_issues(
                _resp(ISSUES_JSON, "length"), "f.ts", strict_json=True
            )

    def test_clean_stop_reason_parses(self) -> None:
        out = parse_file_review_issues(
            _resp(ISSUES_JSON, "stop"), "f.ts", strict_json=True
        )
        assert isinstance(out, list)

    def test_non_strict_does_not_gate(self) -> None:
        out = parse_file_review_issues(
            _resp(ISSUES_JSON, "max_tokens"), "f.ts", strict_json=False
        )
        assert isinstance(out, list)

    def test_plain_str_unaffected(self) -> None:
        out = parse_file_review_issues(ISSUES_JSON, "f.ts", strict_json=True)
        assert isinstance(out, list)


class TestCommitRoundGate:
    def test_max_tokens_raises(self) -> None:
        with pytest.raises(ParseError):
            parse_commit_round_analyses(
                _resp(COMMIT_JSON, "max_tokens"), ["f.py"], strict_json=True
            )

    def test_clean_parses(self) -> None:
        out = parse_commit_round_analyses(
            _resp(COMMIT_JSON, "stop"), ["f.py"], strict_json=True
        )
        assert "f.py" in out


class TestConflictAnalysisGate:
    def test_max_tokens_raises_under_strict(self) -> None:
        with pytest.raises(ParseError):
            parse_conflict_analysis(
                _resp(CONFLICT_JSON, "max_tokens"), "f.py", strict_json=True
            )

    def test_clean_parses(self) -> None:
        out = parse_conflict_analysis(
            _resp(CONFLICT_JSON, "stop"), "f.py", strict_json=True
        )
        assert out.file_path == "f.py"

    def test_non_strict_legacy_no_gate(self) -> None:
        # default strict_json=False preserves every legacy caller
        out = parse_conflict_analysis(_resp(CONFLICT_JSON, "max_tokens"), "f.py")
        assert out.file_path == "f.py"


class TestBatchReviewGate:
    def test_max_tokens_raises(self) -> None:
        with pytest.raises(ParseError):
            parse_batch_file_review_issues(
                _resp(BATCH_JSON, "max_tokens"), ["f.ts"], strict_json=True
            )

    def test_clean_parses(self) -> None:
        out = parse_batch_file_review_issues(
            _resp(BATCH_JSON, "stop"), ["f.ts"], strict_json=True
        )
        assert "f.ts" in out


# --------------------------------------------------------------------------- #
# agent wiring — the LLMResponse (with stop_reason) reaches the parser
# --------------------------------------------------------------------------- #
class TestJudgeReviewFileWiring:
    def _judge(self):
        from src.agents.judge_agent import JudgeAgent

        with patch("src.llm.client.LLMClientFactory.create", return_value=MagicMock()):
            return JudgeAgent(llm_config=AgentLLMConfig(), git_tool=None)

    def _record(self) -> FileDecisionRecord:
        return FileDecisionRecord(
            file_path="f.ts",
            file_status=FileStatus.MODIFIED,
            decision=MergeDecision.SEMANTIC_MERGE,
            decision_source=DecisionSource.AUTO_EXECUTOR,
            confidence=0.9,
            rationale="t",
            timestamp=datetime.now(),
        )

    def _fd(self) -> FileDiff:
        return FileDiff(
            file_path="f.ts",
            file_status=FileStatus.MODIFIED,
            risk_level=RiskLevel.AUTO_RISKY,
            risk_score=0.5,
            change_category=FileChangeCategory.C,
            lines_added=1,
            lines_deleted=1,
        )

    async def test_truncated_review_synthesizes_veto(self) -> None:
        judge = self._judge()
        with patch.object(
            judge,
            "_call_llm_with_retry",
            new=AsyncMock(return_value=_resp(ISSUES_JSON, "max_tokens")),
        ):
            issues = await judge.review_file(
                "f.ts", "export const x = 1\n", self._record(), self._fd()
            )
        assert any(
            i.issue_type == "review_unavailable"
            and i.issue_level == IssueSeverity.CRITICAL
            for i in issues
        )

    async def test_clean_review_no_veto(self) -> None:
        judge = self._judge()
        with patch.object(
            judge,
            "_call_llm_with_retry",
            new=AsyncMock(return_value=_resp(ISSUES_JSON, "stop")),
        ):
            issues = await judge.review_file(
                "f.ts", "export const x = 1\n", self._record(), self._fd()
            )
        assert not any(i.issue_type == "review_unavailable" for i in issues)


class TestAnalystWiring:
    def _analyst(self):
        from src.agents.conflict_analyst_agent import ConflictAnalystAgent

        with patch("src.llm.client.LLMClientFactory.create", return_value=MagicMock()):
            return ConflictAnalystAgent(llm_config=AgentLLMConfig(), git_tool=None)

    def _fd(self) -> FileDiff:
        return FileDiff(
            file_path="f.py",
            file_status=FileStatus.MODIFIED,
            risk_level=RiskLevel.AUTO_RISKY,
            risk_score=0.5,
            change_category=FileChangeCategory.C,
            lines_added=5,
            lines_deleted=5,
        )

    async def test_truncated_analysis_escalates(self) -> None:
        analyst = self._analyst()
        with patch.object(
            analyst,
            "_call_llm_with_retry",
            new=AsyncMock(return_value=_resp(CONFLICT_JSON, "max_tokens")),
        ):
            out = await analyst.analyze_file(
                self._fd(), "base\n", "fork\n", "upstream\n"
            )
        # the gate's ParseError is caught by analyze_file's except → 0.3 escalation
        assert out.overall_confidence == pytest.approx(0.3)
        assert out.recommended_strategy == MergeDecision.ESCALATE_HUMAN
