"""Bug-fix (zod validation, 2026-05-28): _validate_evidence_grounded must not
downgrade real Judge findings when the file is the unmodified fork blob.

Scenario: an O-L5 dispatch gap (part1-surfaced item with user_choice=take_target
never actualized) left a file at fork content. The Judge correctly flagged
"upstream not applied" and cited the missing upstream line as evidence_excerpt.
Because that excerpt is legitimately absent from the (fork-blob) merged_content,
the prior rule downgraded the finding as hallucinated. The fix: with
``fork_content`` supplied, treat the evidence as grounded when the file equals
fork blob, or when the excerpt is found in fork content.
"""

from __future__ import annotations

from src.llm.response_parser import (
    _HALLUCINATED_SUFFIX,
    _validate_evidence_grounded,
    parse_batch_file_review_issues,
    parse_file_review_issues,
)
from src.models.judge import IssueSeverity


class TestValidateEvidenceGroundedForkAware:
    def test_excerpt_in_merged_content_not_downgraded(self):
        level, desc = _validate_evidence_grounded(
            IssueSeverity.CRITICAL,
            "needle",
            "haystack containing needle here",
            "issue",
            fork_content="unrelated",
        )
        assert level == IssueSeverity.CRITICAL
        assert _HALLUCINATED_SUFFIX not in desc

    def test_excerpt_absent_no_fork_content_downgrades(self):
        level, desc = _validate_evidence_grounded(
            IssueSeverity.CRITICAL,
            "needle",
            "haystack",
            "issue",
            fork_content=None,
        )
        assert level == IssueSeverity.MEDIUM
        assert _HALLUCINATED_SUFFIX in desc

    def test_file_equals_fork_blob_not_downgraded(self):
        fork_blob = "the original fork content\nline 2"
        level, desc = _validate_evidence_grounded(
            IssueSeverity.HIGH,
            "expected upstream addition",
            fork_blob,
            "Upstream changes have not been applied",
            fork_content=fork_blob,
        )
        assert level == IssueSeverity.HIGH
        assert _HALLUCINATED_SUFFIX not in desc

    def test_excerpt_in_fork_content_not_downgraded(self):
        # Judge cites fork-side content that should have been overwritten.
        level, desc = _validate_evidence_grounded(
            IssueSeverity.CRITICAL,
            "legacy_fork_symbol",
            "some merged content that lacks the symbol",
            "fork content leaked through",
            fork_content="includes legacy_fork_symbol here",
        )
        assert level == IssueSeverity.CRITICAL
        assert _HALLUCINATED_SUFFIX not in desc

    def test_excerpt_in_neither_downgrades(self):
        level, desc = _validate_evidence_grounded(
            IssueSeverity.CRITICAL,
            "completely fabricated",
            "merged",
            "Made-up",
            fork_content="fork",
        )
        assert level == IssueSeverity.MEDIUM
        assert _HALLUCINATED_SUFFIX in desc

    def test_medium_level_not_affected(self):
        # Only CRITICAL/HIGH are subject to the rule.
        level, desc = _validate_evidence_grounded(
            IssueSeverity.MEDIUM,
            "absent",
            "merged",
            "issue",
            fork_content=None,
        )
        assert level == IssueSeverity.MEDIUM
        assert _HALLUCINATED_SUFFIX not in desc


class TestParseFileReviewIssuesForkAware:
    def test_parse_file_issues_with_fork_content(self):
        fork_blob = "fork-only line"
        raw = {
            "issues": [
                {
                    "file_path": "a.ts",
                    "issue_level": "critical",
                    "issue_type": "missing_upstream",
                    "description": "Upstream not applied",
                    "affected_lines": [1],
                    "evidence_excerpt": "fork-only line",
                }
            ]
        }
        issues = parse_file_review_issues(
            raw, "a.ts", merged_content=fork_blob, fork_content=fork_blob
        )
        assert len(issues) == 1
        assert issues[0].issue_level == IssueSeverity.CRITICAL
        assert _HALLUCINATED_SUFFIX not in issues[0].description


class TestParseBatchFileReviewIssuesForkAware:
    def test_parse_batch_with_fork_contents(self):
        fork_blob = "fork-only line"
        raw = {
            "files": [
                {
                    "file_path": "a.ts",
                    "issues": [
                        {
                            "issue_level": "high",
                            "issue_type": "missing_upstream",
                            "description": "Upstream not applied",
                            "affected_lines": [1],
                            "evidence_excerpt": "fork-only line",
                        }
                    ],
                }
            ]
        }
        per_file = parse_batch_file_review_issues(
            raw,
            ["a.ts"],
            merged_contents={"a.ts": fork_blob},
            fork_contents={"a.ts": fork_blob},
        )
        assert len(per_file["a.ts"]) == 1
        assert per_file["a.ts"][0].issue_level == IssueSeverity.HIGH
        assert _HALLUCINATED_SUFFIX not in per_file["a.ts"][0].description

    def test_parse_batch_no_fork_contents_downgrades(self):
        # Regression: legacy behaviour unchanged when fork_contents omitted.
        raw = {
            "files": [
                {
                    "file_path": "a.ts",
                    "issues": [
                        {
                            "issue_level": "high",
                            "issue_type": "x",
                            "description": "bad",
                            "affected_lines": [1],
                            "evidence_excerpt": "absent",
                        }
                    ],
                }
            ]
        }
        per_file = parse_batch_file_review_issues(
            raw,
            ["a.ts"],
            merged_contents={"a.ts": "merged content"},
        )
        assert per_file["a.ts"][0].issue_level == IssueSeverity.MEDIUM
        assert _HALLUCINATED_SUFFIX in per_file["a.ts"][0].description
