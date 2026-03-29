import json
import pytest

from src.llm.client import ParseError
from src.llm.response_parser import (
    _extract_json,
    _validate_confidence,
    _validate_enum,
    _severity_order,
    parse_plan_judge_verdict,
    parse_conflict_analysis,
    parse_judge_verdict,
    parse_merge_result,
    parse_file_review_issues,
)
from src.models.plan_judge import PlanJudgeResult, PlanJudgeVerdict
from src.models.conflict import ConflictType, ConflictAnalysis
from src.models.decision import MergeDecision
from src.models.judge import JudgeVerdict, JudgeIssue, VerdictType, IssueSeverity
from src.models.diff import RiskLevel


class TestExtractJson:
    def test_returns_dict_unchanged(self):
        data = {"key": "value"}
        result = _extract_json(data)
        assert result == data

    def test_parses_plain_json_string(self):
        raw = '{"name": "test", "count": 5}'
        result = _extract_json(raw)
        assert result == {"name": "test", "count": 5}

    def test_strips_markdown_json_fence(self):
        raw = '```json\n{"key": "val"}\n```'
        result = _extract_json(raw)
        assert result == {"key": "val"}

    def test_strips_plain_code_fence(self):
        raw = '```\n{"key": "val"}\n```'
        result = _extract_json(raw)
        assert result == {"key": "val"}

    def test_strips_code_fence_without_closing(self):
        raw = '```json\n{"key": "val"}'
        result = _extract_json(raw)
        assert result == {"key": "val"}

    def test_extracts_json_from_surrounding_text(self):
        raw = 'Here is the result: {"answer": 42} and some more text'
        result = _extract_json(raw)
        assert result == {"answer": 42}

    def test_raises_parse_error_on_invalid_json(self):
        with pytest.raises(ParseError, match="Cannot extract JSON"):
            _extract_json("this is not json at all")

    def test_raises_parse_error_on_no_braces(self):
        with pytest.raises(ParseError):
            _extract_json("just plain text no braces")

    def test_handles_nested_json(self):
        raw = '{"outer": {"inner": [1, 2, 3]}}'
        result = _extract_json(raw)
        assert result["outer"]["inner"] == [1, 2, 3]

    def test_strips_whitespace_before_parsing(self):
        raw = '  \n  {"key": "trimmed"}  \n  '
        result = _extract_json(raw)
        assert result == {"key": "trimmed"}


class TestValidateConfidence:
    def test_accepts_zero(self):
        assert _validate_confidence(0.0) == 0.0

    def test_accepts_one(self):
        assert _validate_confidence(1.0) == 1.0

    def test_accepts_middle_value(self):
        assert _validate_confidence(0.5) == 0.5

    def test_returns_float(self):
        result = _validate_confidence(1)
        assert isinstance(result, float)

    def test_raises_on_value_above_one(self):
        with pytest.raises(ParseError, match="Confidence must be in"):
            _validate_confidence(1.1)

    def test_raises_on_negative_value(self):
        with pytest.raises(ParseError, match="Confidence must be in"):
            _validate_confidence(-0.1)

    def test_raises_on_non_numeric(self):
        with pytest.raises(ParseError, match="Confidence must be a number"):
            _validate_confidence("high")  # type: ignore[arg-type]


class TestValidateEnum:
    def test_accepts_valid_value(self):
        result = _validate_enum("approved", PlanJudgeResult, "result")
        assert result == "approved"

    def test_accepts_all_risk_level_values(self):
        for level in RiskLevel:
            result = _validate_enum(level.value, RiskLevel, "risk_level")
            assert result == level.value

    def test_raises_on_invalid_value(self):
        with pytest.raises(ParseError, match="Invalid result value"):
            _validate_enum("invalid_value", PlanJudgeResult, "result")

    def test_error_message_contains_field_name(self):
        with pytest.raises(ParseError, match="my_field"):
            _validate_enum("bad", VerdictType, "my_field")

    def test_error_message_contains_invalid_value(self):
        with pytest.raises(ParseError, match="bad_val"):
            _validate_enum("bad_val", VerdictType, "verdict")


class TestSeverityOrder:
    def test_info_is_lowest(self):
        assert _severity_order(IssueSeverity.INFO) == 0

    def test_critical_is_highest(self):
        assert _severity_order(IssueSeverity.CRITICAL) == 4

    def test_ordering_is_correct(self):
        assert (
            _severity_order(IssueSeverity.INFO)
            < _severity_order(IssueSeverity.LOW)
            < _severity_order(IssueSeverity.MEDIUM)
            < _severity_order(IssueSeverity.HIGH)
            < _severity_order(IssueSeverity.CRITICAL)
        )


class TestParsePlanJudgeVerdict:
    def _approved_payload(self) -> dict:
        return {
            "result": "approved",
            "issues": [],
            "approved_files_count": 10,
            "flagged_files_count": 0,
            "summary": "All good",
        }

    def test_parses_approved_result(self):
        verdict = parse_plan_judge_verdict(self._approved_payload())
        assert verdict.result == PlanJudgeResult.APPROVED

    def test_parses_revision_needed(self):
        payload = {**self._approved_payload(), "result": "revision_needed"}
        verdict = parse_plan_judge_verdict(payload)
        assert verdict.result == PlanJudgeResult.REVISION_NEEDED

    def test_parses_critical_replan(self):
        payload = {**self._approved_payload(), "result": "critical_replan"}
        verdict = parse_plan_judge_verdict(payload)
        assert verdict.result == PlanJudgeResult.CRITICAL_REPLAN

    def test_approved_files_count(self):
        verdict = parse_plan_judge_verdict(self._approved_payload())
        assert verdict.approved_files_count == 10

    def test_summary_is_set(self):
        verdict = parse_plan_judge_verdict(self._approved_payload())
        assert verdict.summary == "All good"

    def test_judge_model_passed_through(self):
        verdict = parse_plan_judge_verdict(
            self._approved_payload(), judge_model="gpt-4o"
        )
        assert verdict.judge_model == "gpt-4o"

    def test_revision_round_passed_through(self):
        verdict = parse_plan_judge_verdict(self._approved_payload(), revision_round=2)
        assert verdict.revision_round == 2

    def test_parses_issues(self):
        payload = {
            "result": "revision_needed",
            "issues": [
                {
                    "file_path": "src/auth.py",
                    "current_classification": "auto_safe",
                    "suggested_classification": "human_required",
                    "reason": "Contains sensitive logic",
                    "issue_type": "risk_underestimated",
                }
            ],
            "summary": "Issues found",
        }
        verdict = parse_plan_judge_verdict(payload)
        assert len(verdict.issues) == 1
        assert verdict.issues[0].file_path == "src/auth.py"
        assert verdict.issues[0].current_classification == RiskLevel.AUTO_SAFE
        assert verdict.issues[0].suggested_classification == RiskLevel.HUMAN_REQUIRED

    def test_flagged_files_count_defaults_to_issues_length(self):
        payload = {
            "result": "revision_needed",
            "issues": [
                {
                    "file_path": "a.py",
                    "current_classification": "auto_safe",
                    "suggested_classification": "human_required",
                    "reason": "risky",
                    "issue_type": "risk_underestimated",
                }
            ],
            "summary": "check",
        }
        verdict = parse_plan_judge_verdict(payload)
        assert verdict.flagged_files_count == 1

    def test_parses_from_json_string(self):
        raw = json.dumps(self._approved_payload())
        verdict = parse_plan_judge_verdict(raw)
        assert isinstance(verdict, PlanJudgeVerdict)

    def test_parses_from_markdown_fenced_json(self):
        raw = "```json\n" + json.dumps(self._approved_payload()) + "\n```"
        verdict = parse_plan_judge_verdict(raw)
        assert verdict.result == PlanJudgeResult.APPROVED

    def test_raises_on_invalid_result_value(self):
        payload = {**self._approved_payload(), "result": "invalid_result"}
        with pytest.raises(ParseError):
            parse_plan_judge_verdict(payload)

    def test_empty_issues_list(self):
        verdict = parse_plan_judge_verdict(self._approved_payload())
        assert verdict.issues == []

    def test_timestamp_is_set(self):
        from datetime import datetime

        verdict = parse_plan_judge_verdict(self._approved_payload())
        assert isinstance(verdict.timestamp, datetime)


class TestParseConflictAnalysis:
    def _base_payload(self) -> dict:
        return {
            "conflict_type": "concurrent_modification",
            "recommended_strategy": "semantic_merge",
            "confidence": 0.8,
            "upstream_intent": {
                "description": "Adds caching",
                "intent_type": "optimization",
                "confidence": 0.9,
            },
            "fork_intent": {
                "description": "Adds logging",
                "intent_type": "feature",
                "confidence": 0.85,
            },
            "can_coexist": True,
            "is_security_sensitive": False,
            "rationale": "Both changes target different concerns",
        }

    def test_returns_conflict_analysis(self):
        result = parse_conflict_analysis(self._base_payload(), "src/app.py")
        assert isinstance(result, ConflictAnalysis)

    def test_file_path_is_set(self):
        result = parse_conflict_analysis(self._base_payload(), "src/app.py")
        assert result.file_path == "src/app.py"

    def test_conflict_type_parsed(self):
        result = parse_conflict_analysis(self._base_payload(), "src/app.py")
        assert result.conflict_type == ConflictType.CONCURRENT_MODIFICATION

    def test_recommended_strategy_parsed(self):
        result = parse_conflict_analysis(self._base_payload(), "src/app.py")
        assert result.recommended_strategy == MergeDecision.SEMANTIC_MERGE

    def test_confidence_parsed(self):
        result = parse_conflict_analysis(self._base_payload(), "src/app.py")
        assert result.confidence == 0.8

    def test_can_coexist_true(self):
        result = parse_conflict_analysis(self._base_payload(), "src/app.py")
        assert result.can_coexist is True

    def test_is_security_sensitive_false(self):
        result = parse_conflict_analysis(self._base_payload(), "src/app.py")
        assert result.is_security_sensitive is False

    def test_is_security_sensitive_true(self):
        payload = {**self._base_payload(), "is_security_sensitive": True}
        result = parse_conflict_analysis(payload, "src/auth.py")
        assert result.is_security_sensitive is True

    def test_conflict_point_created(self):
        result = parse_conflict_analysis(self._base_payload(), "src/app.py")
        assert len(result.conflict_points) == 1

    def test_upstream_intent_description(self):
        result = parse_conflict_analysis(self._base_payload(), "src/app.py")
        assert result.conflict_points[0].upstream_intent.description == "Adds caching"

    def test_fork_intent_description(self):
        result = parse_conflict_analysis(self._base_payload(), "src/app.py")
        assert result.conflict_points[0].fork_intent.description == "Adds logging"

    def test_unknown_conflict_type_falls_back(self):
        payload = {**self._base_payload(), "conflict_type": "nonexistent_type"}
        result = parse_conflict_analysis(payload, "src/app.py")
        assert result.conflict_type == ConflictType.UNKNOWN

    def test_invalid_strategy_falls_back_to_escalate_human(self):
        payload = {**self._base_payload(), "recommended_strategy": "bad_strategy"}
        result = parse_conflict_analysis(payload, "src/app.py")
        assert result.recommended_strategy == MergeDecision.ESCALATE_HUMAN

    def test_model_param_not_stored_but_does_not_raise(self):
        result = parse_conflict_analysis(
            self._base_payload(), "src/app.py", model="gpt-4o"
        )
        assert result is not None

    def test_parses_from_json_string(self):
        raw = json.dumps(self._base_payload())
        result = parse_conflict_analysis(raw, "src/app.py")
        assert isinstance(result, ConflictAnalysis)


class TestParseJudgeVerdict:
    def _base_payload(self) -> dict:
        return {
            "verdict": "pass",
            "confidence": 0.9,
            "summary": "Everything looks good",
            "blocking_issues": [],
        }

    def test_returns_judge_verdict(self):
        result = parse_judge_verdict(self._base_payload(), ["src/a.py"])
        assert isinstance(result, JudgeVerdict)

    def test_verdict_pass(self):
        result = parse_judge_verdict(self._base_payload(), ["src/a.py"])
        assert result.verdict == VerdictType.PASS

    def test_verdict_fail(self):
        payload = {**self._base_payload(), "verdict": "fail"}
        result = parse_judge_verdict(payload, ["src/a.py"])
        assert result.verdict == VerdictType.FAIL

    def test_verdict_conditional(self):
        payload = {**self._base_payload(), "verdict": "conditional"}
        result = parse_judge_verdict(payload, ["src/a.py"])
        assert result.verdict == VerdictType.CONDITIONAL

    def test_invalid_verdict_falls_back_to_conditional(self):
        payload = {**self._base_payload(), "verdict": "bogus_verdict"}
        result = parse_judge_verdict(payload, ["src/a.py"])
        assert result.verdict == VerdictType.CONDITIONAL

    def test_reviewed_files_count(self):
        result = parse_judge_verdict(self._base_payload(), ["a.py", "b.py", "c.py"])
        assert result.reviewed_files_count == 3

    def test_passed_files_with_no_issues(self):
        result = parse_judge_verdict(self._base_payload(), ["clean.py"])
        assert "clean.py" in result.passed_files

    def test_failed_files_with_critical_issue(self):
        issue = JudgeIssue(
            file_path="risky.py",
            issue_level=IssueSeverity.CRITICAL,
            issue_type="security",
            description="SQL injection",
        )
        result = parse_judge_verdict(
            self._base_payload(), ["risky.py"], all_issues=[issue]
        )
        assert "risky.py" in result.failed_files

    def test_failed_files_with_high_issue(self):
        issue = JudgeIssue(
            file_path="risky.py",
            issue_level=IssueSeverity.HIGH,
            issue_type="logic",
            description="Logic error",
        )
        result = parse_judge_verdict(
            self._base_payload(), ["risky.py"], all_issues=[issue]
        )
        assert "risky.py" in result.failed_files

    def test_conditional_files_with_medium_issue(self):
        issue = JudgeIssue(
            file_path="maybe.py",
            issue_level=IssueSeverity.MEDIUM,
            issue_type="style",
            description="Code smell",
        )
        result = parse_judge_verdict(
            self._base_payload(), ["maybe.py"], all_issues=[issue]
        )
        assert "maybe.py" in result.conditional_files

    def test_conditional_files_with_low_issue(self):
        issue = JudgeIssue(
            file_path="minor.py",
            issue_level=IssueSeverity.LOW,
            issue_type="style",
            description="Minor style",
        )
        result = parse_judge_verdict(
            self._base_payload(), ["minor.py"], all_issues=[issue]
        )
        assert "minor.py" in result.conditional_files

    def test_critical_issues_count(self):
        issues = [
            JudgeIssue(
                file_path="a.py",
                issue_level=IssueSeverity.CRITICAL,
                issue_type="security",
                description="Critical",
            ),
            JudgeIssue(
                file_path="b.py",
                issue_level=IssueSeverity.CRITICAL,
                issue_type="security",
                description="Critical 2",
            ),
        ]
        result = parse_judge_verdict(
            self._base_payload(), ["a.py", "b.py"], all_issues=issues
        )
        assert result.critical_issues_count == 2

    def test_high_issues_count(self):
        issues = [
            JudgeIssue(
                file_path="a.py",
                issue_level=IssueSeverity.HIGH,
                issue_type="logic",
                description="High 1",
            ),
        ]
        result = parse_judge_verdict(self._base_payload(), ["a.py"], all_issues=issues)
        assert result.high_issues_count == 1

    def test_judge_model_passed_through(self):
        result = parse_judge_verdict(
            self._base_payload(), ["a.py"], judge_model="claude-opus"
        )
        assert result.judge_model == "claude-opus"

    def test_summary_from_payload(self):
        result = parse_judge_verdict(self._base_payload(), ["a.py"])
        assert result.summary == "Everything looks good"

    def test_summary_falls_back_to_overall_assessment(self):
        payload = {
            "verdict": "pass",
            "confidence": 0.9,
            "overall_assessment": "Looks fine",
            "blocking_issues": [],
        }
        result = parse_judge_verdict(payload, ["a.py"])
        assert result.summary == "Looks fine"

    def test_worst_severity_wins_for_file(self):
        issues = [
            JudgeIssue(
                file_path="shared.py",
                issue_level=IssueSeverity.LOW,
                issue_type="style",
                description="Minor",
            ),
            JudgeIssue(
                file_path="shared.py",
                issue_level=IssueSeverity.CRITICAL,
                issue_type="security",
                description="Critical",
            ),
        ]
        result = parse_judge_verdict(
            self._base_payload(), ["shared.py"], all_issues=issues
        )
        assert "shared.py" in result.failed_files

    def test_no_issues_defaults_to_empty_list(self):
        result = parse_judge_verdict(self._base_payload(), ["a.py"])
        assert result.issues == []

    def test_parses_from_json_string(self):
        raw = json.dumps(self._base_payload())
        result = parse_judge_verdict(raw, ["a.py"])
        assert isinstance(result, JudgeVerdict)

    def test_blocking_issues_list(self):
        payload = {**self._base_payload(), "blocking_issues": ["issue1", "issue2"]}
        result = parse_judge_verdict(payload, ["a.py"])
        assert result.blocking_issues == ["issue1", "issue2"]


class TestParseMergeResult:
    def test_returns_string_from_dict(self):
        data = {"content": "merged code here"}
        result = parse_merge_result(data)
        assert result == "merged code here"

    def test_empty_content_key_returns_empty_string(self):
        data = {}
        result = parse_merge_result(data)
        assert result == ""

    def test_returns_plain_text_string(self):
        result = parse_merge_result("plain text content")
        assert result == "plain text content"

    def test_strips_markdown_code_fence(self):
        raw = "```python\ndef hello():\n    pass\n```"
        result = parse_merge_result(raw)
        assert result == "def hello():\n    pass"

    def test_strips_plain_code_fence(self):
        raw = "```\nsome content\n```"
        result = parse_merge_result(raw)
        assert result == "some content"

    def test_handles_code_fence_without_closing(self):
        raw = "```\nno closing fence"
        result = parse_merge_result(raw)
        assert "no closing fence" in result

    def test_strips_leading_whitespace(self):
        result = parse_merge_result("   trimmed content   ")
        assert result == "trimmed content"

    def test_multiline_content_preserved(self):
        raw = "line1\nline2\nline3"
        result = parse_merge_result(raw)
        assert result == "line1\nline2\nline3"


class TestParseFileReviewIssues:
    def _issue_payload(self, level: str = "high") -> dict:
        return {
            "issues": [
                {
                    "file_path": "src/main.py",
                    "issue_level": level,
                    "issue_type": "security",
                    "description": "Found a problem",
                    "affected_lines": [10, 11],
                    "suggested_fix": "Do this instead",
                    "must_fix_before_merge": True,
                }
            ]
        }

    def test_returns_list_of_judge_issues(self):
        result = parse_file_review_issues(self._issue_payload(), "default.py")
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], JudgeIssue)

    def test_file_path_from_issue(self):
        result = parse_file_review_issues(self._issue_payload(), "default.py")
        assert result[0].file_path == "src/main.py"

    def test_falls_back_to_default_file_path(self):
        payload = {
            "issues": [
                {
                    "issue_level": "medium",
                    "issue_type": "style",
                    "description": "Style issue",
                }
            ]
        }
        result = parse_file_review_issues(payload, "default.py")
        assert result[0].file_path == "default.py"

    def test_parses_severity_critical(self):
        result = parse_file_review_issues(self._issue_payload("critical"), "f.py")
        assert result[0].issue_level == IssueSeverity.CRITICAL

    def test_parses_severity_high(self):
        result = parse_file_review_issues(self._issue_payload("high"), "f.py")
        assert result[0].issue_level == IssueSeverity.HIGH

    def test_parses_severity_medium(self):
        result = parse_file_review_issues(self._issue_payload("medium"), "f.py")
        assert result[0].issue_level == IssueSeverity.MEDIUM

    def test_parses_severity_low(self):
        result = parse_file_review_issues(self._issue_payload("low"), "f.py")
        assert result[0].issue_level == IssueSeverity.LOW

    def test_parses_severity_info(self):
        result = parse_file_review_issues(self._issue_payload("info"), "f.py")
        assert result[0].issue_level == IssueSeverity.INFO

    def test_invalid_severity_defaults_to_medium(self):
        payload = {
            "issues": [
                {
                    "file_path": "x.py",
                    "issue_level": "super_critical",
                    "issue_type": "other",
                    "description": "Unknown severity",
                }
            ]
        }
        result = parse_file_review_issues(payload, "default.py")
        assert result[0].issue_level == IssueSeverity.MEDIUM

    def test_affected_lines_parsed(self):
        result = parse_file_review_issues(self._issue_payload(), "f.py")
        assert result[0].affected_lines == [10, 11]

    def test_suggested_fix_parsed(self):
        result = parse_file_review_issues(self._issue_payload(), "f.py")
        assert result[0].suggested_fix == "Do this instead"

    def test_must_fix_before_merge_true(self):
        result = parse_file_review_issues(self._issue_payload(), "f.py")
        assert result[0].must_fix_before_merge is True

    def test_empty_issues_list(self):
        result = parse_file_review_issues({"issues": []}, "default.py")
        assert result == []

    def test_missing_issues_key_returns_empty(self):
        result = parse_file_review_issues({}, "default.py")
        assert result == []

    def test_multiple_issues(self):
        payload = {
            "issues": [
                {
                    "file_path": "a.py",
                    "issue_level": "high",
                    "issue_type": "security",
                    "description": "Issue 1",
                },
                {
                    "file_path": "b.py",
                    "issue_level": "low",
                    "issue_type": "style",
                    "description": "Issue 2",
                },
            ]
        }
        result = parse_file_review_issues(payload, "default.py")
        assert len(result) == 2
        assert result[0].file_path == "a.py"
        assert result[1].file_path == "b.py"

    def test_parses_from_json_string(self):
        raw = json.dumps(self._issue_payload())
        result = parse_file_review_issues(raw, "default.py")
        assert len(result) == 1

    def test_parses_from_fenced_json_string(self):
        raw = "```json\n" + json.dumps(self._issue_payload()) + "\n```"
        result = parse_file_review_issues(raw, "default.py")
        assert len(result) == 1
