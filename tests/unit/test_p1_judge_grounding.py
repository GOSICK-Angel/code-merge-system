"""P1-3 tests: Judge CRITICAL/HIGH issues must be grounded.

After P0-3 made verdict deterministic from issue counts, the Judge gained
veto power: any CRITICAL/HIGH issue forces a FAIL. dify-plugin-daemon runs
showed the LLM emitting hand-wavy CRITICAL findings without specific
evidence — those should not block the merge.

Contract:
- An issue is "grounded" when ``affected_lines`` is non-empty OR
  ``evidence_excerpt`` is a non-empty string.
- An ungrounded CRITICAL/HIGH issue is auto-downgraded to MEDIUM and its
  ``description`` is suffixed with ``[downgraded: ungrounded]`` so the
  trail is visible in reports.
- MEDIUM/LOW/INFO issues are unaffected by grounding.
- Both single-file (``parse_file_review_issues``) and batch
  (``parse_batch_file_review_issues``) parsers apply the rule identically.
"""

from __future__ import annotations

from src.llm.response_parser import (
    parse_batch_file_review_issues,
    parse_file_review_issues,
)
from src.models.judge import IssueSeverity


def _issue(
    level: str,
    *,
    affected_lines: list[int] | None = None,
    evidence_excerpt: str | None = None,
    file_path: str = "x.py",
    description: str = "synthetic",
) -> dict:
    item: dict = {
        "file_path": file_path,
        "issue_level": level,
        "issue_type": "missing_logic",
        "description": description,
        "must_fix_before_merge": True,
    }
    if affected_lines is not None:
        item["affected_lines"] = affected_lines
    if evidence_excerpt is not None:
        item["evidence_excerpt"] = evidence_excerpt
    return item


class TestSingleFileParser:
    def test_critical_with_affected_lines_stays_critical(self) -> None:
        raw = {"issues": [_issue("critical", affected_lines=[10, 11])]}
        issues = parse_file_review_issues(raw, "x.py")
        assert len(issues) == 1
        assert issues[0].issue_level == IssueSeverity.CRITICAL
        assert "[downgraded" not in issues[0].description

    def test_critical_with_evidence_excerpt_stays_critical(self) -> None:
        raw = {
            "issues": [
                _issue(
                    "critical",
                    evidence_excerpt="return user.role == 'admin'",
                )
            ]
        }
        issues = parse_file_review_issues(raw, "x.py")
        assert issues[0].issue_level == IssueSeverity.CRITICAL
        assert issues[0].evidence_excerpt == "return user.role == 'admin'"

    def test_critical_without_grounding_downgrades_to_medium(self) -> None:
        raw = {"issues": [_issue("critical", description="something is off")]}
        issues = parse_file_review_issues(raw, "x.py")
        assert issues[0].issue_level == IssueSeverity.MEDIUM
        assert "[downgraded: ungrounded]" in issues[0].description

    def test_high_without_grounding_downgrades_to_medium(self) -> None:
        raw = {"issues": [_issue("high")]}
        issues = parse_file_review_issues(raw, "x.py")
        assert issues[0].issue_level == IssueSeverity.MEDIUM
        assert "[downgraded: ungrounded]" in issues[0].description

    def test_high_with_empty_excerpt_string_downgrades(self) -> None:
        raw = {"issues": [_issue("high", evidence_excerpt="")]}
        issues = parse_file_review_issues(raw, "x.py")
        assert issues[0].issue_level == IssueSeverity.MEDIUM

    def test_medium_without_grounding_stays_medium(self) -> None:
        raw = {"issues": [_issue("medium", description="nit")]}
        issues = parse_file_review_issues(raw, "x.py")
        assert issues[0].issue_level == IssueSeverity.MEDIUM
        assert "[downgraded" not in issues[0].description

    def test_low_without_grounding_stays_low(self) -> None:
        raw = {"issues": [_issue("low")]}
        issues = parse_file_review_issues(raw, "x.py")
        assert issues[0].issue_level == IssueSeverity.LOW

    def test_info_without_grounding_stays_info(self) -> None:
        raw = {"issues": [_issue("info")]}
        issues = parse_file_review_issues(raw, "x.py")
        assert issues[0].issue_level == IssueSeverity.INFO

    def test_mixed_issues_only_high_severity_downgraded(self) -> None:
        raw = {
            "issues": [
                _issue("critical", affected_lines=[5]),
                _issue("critical", description="ungrounded crit"),
                _issue("high", evidence_excerpt="some code"),
                _issue("high", description="ungrounded high"),
                _issue("medium", description="nit"),
            ]
        }
        issues = parse_file_review_issues(raw, "x.py")
        levels = [i.issue_level for i in issues]
        assert levels == [
            IssueSeverity.CRITICAL,
            IssueSeverity.MEDIUM,
            IssueSeverity.HIGH,
            IssueSeverity.MEDIUM,
            IssueSeverity.MEDIUM,
        ]
        downgraded = [i for i in issues if "[downgraded" in i.description]
        assert len(downgraded) == 2


class TestBatchParser:
    def test_batch_applies_same_grounding_rule(self) -> None:
        raw = {
            "files": [
                {
                    "file_path": "a.py",
                    "issues": [
                        _issue("critical", affected_lines=[1], file_path="a.py"),
                        _issue("critical", file_path="a.py"),
                    ],
                },
                {
                    "file_path": "b.py",
                    "issues": [
                        _issue(
                            "high",
                            evidence_excerpt="x = 1",
                            file_path="b.py",
                        )
                    ],
                },
            ]
        }
        result = parse_batch_file_review_issues(raw, ["a.py", "b.py"])
        a_levels = [i.issue_level for i in result["a.py"]]
        b_levels = [i.issue_level for i in result["b.py"]]
        assert a_levels == [IssueSeverity.CRITICAL, IssueSeverity.MEDIUM]
        assert b_levels == [IssueSeverity.HIGH]


class TestEvidenceExcerptPersists:
    def test_evidence_excerpt_round_trips_through_parser(self) -> None:
        raw = {
            "issues": [
                _issue(
                    "critical",
                    affected_lines=[5],
                    evidence_excerpt="if user.is_admin:",
                )
            ]
        }
        issues = parse_file_review_issues(raw, "x.py")
        assert issues[0].evidence_excerpt == "if user.is_admin:"

    def test_missing_evidence_excerpt_defaults_to_none(self) -> None:
        raw = {"issues": [_issue("critical", affected_lines=[5])]}
        issues = parse_file_review_issues(raw, "x.py")
        assert issues[0].evidence_excerpt is None
