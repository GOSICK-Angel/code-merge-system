"""P0-3 tests: Judge verdict must be deterministic from issue counts.

The dify-plugin-daemon report exposed a contradiction: Judge returned
``verdict=pass`` while listing 70 critical + 13 high issues. The cause is
``parse_judge_verdict`` trusting the LLM's free-form ``verdict`` string
instead of recomputing from ``all_issues``.

The new contract:
- If any issue has severity CRITICAL or HIGH  -> verdict = FAIL
- Else if any issue exists                    -> verdict = CONDITIONAL
- Else                                        -> verdict = PASS

The LLM's ``verdict`` field is ignored. To declare failure, the Judge must
produce a structured ``JudgeIssue`` at CRITICAL/HIGH severity.
"""

from __future__ import annotations

from src.llm.response_parser import parse_judge_verdict
from src.models.judge import IssueSeverity, JudgeIssue, VerdictType


def _llm_payload(verdict: str = "pass") -> dict:
    return {
        "verdict": verdict,
        "confidence": 0.9,
        "summary": "synthetic LLM payload",
        "blocking_issues": [],
    }


def _issue(severity: IssueSeverity, file_path: str = "x.py") -> JudgeIssue:
    return JudgeIssue(
        file_path=file_path,
        issue_level=severity,
        issue_type="test",
        description="synthetic issue",
    )


class TestVerdictOverridesLlmField:
    def test_pass_field_with_critical_issue_becomes_fail(self) -> None:
        result = parse_judge_verdict(
            _llm_payload("pass"),
            ["x.py"],
            all_issues=[_issue(IssueSeverity.CRITICAL)],
        )
        assert result.verdict == VerdictType.FAIL
        assert result.critical_issues_count == 1

    def test_pass_field_with_high_issue_becomes_fail(self) -> None:
        result = parse_judge_verdict(
            _llm_payload("pass"),
            ["x.py"],
            all_issues=[_issue(IssueSeverity.HIGH)],
        )
        assert result.verdict == VerdictType.FAIL

    def test_pass_field_with_70_critical_becomes_fail(self) -> None:
        """The exact symptom from the dify-plugin-daemon report."""
        issues = [_issue(IssueSeverity.CRITICAL, f"f{i}.py") for i in range(70)] + [
            _issue(IssueSeverity.HIGH, f"g{i}.py") for i in range(13)
        ]
        result = parse_judge_verdict(
            _llm_payload("pass"), [f"f{i}.py" for i in range(70)], all_issues=issues
        )
        assert result.verdict == VerdictType.FAIL
        assert result.critical_issues_count == 70
        assert result.high_issues_count == 13

    def test_fail_field_with_no_issues_becomes_pass(self) -> None:
        result = parse_judge_verdict(_llm_payload("fail"), ["x.py"], all_issues=[])
        assert result.verdict == VerdictType.PASS

    def test_pass_field_with_only_medium_issues_becomes_conditional(self) -> None:
        result = parse_judge_verdict(
            _llm_payload("pass"),
            ["x.py"],
            all_issues=[_issue(IssueSeverity.MEDIUM)],
        )
        assert result.verdict == VerdictType.CONDITIONAL

    def test_pass_field_with_only_low_issues_becomes_conditional(self) -> None:
        result = parse_judge_verdict(
            _llm_payload("pass"),
            ["x.py"],
            all_issues=[_issue(IssueSeverity.LOW)],
        )
        assert result.verdict == VerdictType.CONDITIONAL

    def test_pass_field_with_only_info_issues_becomes_conditional(self) -> None:
        result = parse_judge_verdict(
            _llm_payload("pass"),
            ["x.py"],
            all_issues=[_issue(IssueSeverity.INFO)],
        )
        assert result.verdict == VerdictType.CONDITIONAL

    def test_no_issues_no_llm_verdict_becomes_pass(self) -> None:
        payload = {"confidence": 0.5, "summary": ""}
        result = parse_judge_verdict(payload, ["x.py"], all_issues=[])
        assert result.verdict == VerdictType.PASS

    def test_invalid_llm_verdict_string_does_not_break_parse(self) -> None:
        result = parse_judge_verdict(
            _llm_payload("garbage_verdict"),
            ["x.py"],
            all_issues=[_issue(IssueSeverity.CRITICAL)],
        )
        assert result.verdict == VerdictType.FAIL
