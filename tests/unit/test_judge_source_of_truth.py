"""P-γ-4 F-judge-source-of-truth tests.

LLM file-review may hallucinate an ``evidence_excerpt`` that does not actually
appear in the merged_content read from disk. Without grounding to disk, those
hallucinated issues propagate into ``JudgeIssue.evidence_excerpt`` and bleed
into the verdict surface as noise text.

Contract (parse_file_review_issues / parse_batch_file_review_issues):
- New optional kwarg ``merged_content: str | None = None`` (single) /
  ``merged_contents: dict[str, str] | None = None`` (batch).
- When the kwarg is provided AND evidence_excerpt is non-empty after .strip(),
  the parser validates ``stripped_excerpt in merged_content``. On mismatch the
  issue is downgraded to MEDIUM and its description is suffixed with
  ``[downgraded: hallucinated evidence]``.
- When merged_content is None / batch dict lacks the key, the parser falls
  back to the existing ``_apply_grounding_rule`` only (backward compatible).
- When stripped evidence_excerpt is empty, hallucinate check is skipped.
"""

from __future__ import annotations

import pytest

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


class TestSingleFileHallucinationGuard:
    def test_TA_U_01_evidence_in_merged_content_keeps_high(self) -> None:
        raw = {
            "issues": [_issue("high", evidence_excerpt="line_X", affected_lines=[10])]
        }
        result = parse_file_review_issues(
            raw, "x.py", merged_content="prefix\nline_X\nsuffix"
        )
        assert len(result) == 1
        assert result[0].evidence_excerpt == "line_X"
        assert result[0].issue_level == IssueSeverity.HIGH
        assert "hallucinated" not in result[0].description

    def test_TA_U_02_evidence_not_in_merged_content_downgrades(self) -> None:
        raw = {
            "issues": [
                _issue("high", evidence_excerpt="ghost_line", affected_lines=[10])
            ]
        }
        result = parse_file_review_issues(
            raw, "x.py", merged_content="prefix\nline_X\nsuffix"
        )
        assert len(result) == 1
        assert result[0].issue_level == IssueSeverity.MEDIUM
        assert "hallucinated evidence" in result[0].description

    def test_TA_U_03_empty_evidence_skips_new_check(self) -> None:
        raw = {"issues": [_issue("high", evidence_excerpt="", affected_lines=[10])]}
        result = parse_file_review_issues(
            raw, "x.py", merged_content="prefix\nline_X\nsuffix"
        )
        assert len(result) == 1
        assert result[0].issue_level == IssueSeverity.HIGH
        assert "hallucinated" not in result[0].description

    def test_TA_U_04_null_evidence_no_lines_old_rule_downgrades(self) -> None:
        raw = {"issues": [_issue("high", evidence_excerpt=None, affected_lines=[])]}
        result = parse_file_review_issues(raw, "x.py", merged_content="")
        assert len(result) == 1
        assert result[0].issue_level == IssueSeverity.MEDIUM
        assert "ungrounded" in result[0].description
        assert "hallucinated" not in result[0].description

    def test_TA_U_05_strip_whitespace_then_substring_match(self) -> None:
        raw = {
            "issues": [
                _issue(
                    "high",
                    evidence_excerpt="  line_X  ",
                    affected_lines=[10],
                )
            ]
        }
        result = parse_file_review_issues(
            raw, "x.py", merged_content="prefix\nline_X\nsuffix"
        )
        assert result[0].issue_level == IssueSeverity.HIGH
        assert "hallucinated" not in result[0].description

    def test_TA_U_06_multiline_evidence_partial_match_downgrades(self) -> None:
        raw = {
            "issues": [
                _issue(
                    "high",
                    evidence_excerpt="a\nb\nc",
                    affected_lines=[1, 2, 3],
                )
            ]
        }
        result = parse_file_review_issues(raw, "x.py", merged_content="a\nb\nZ")
        assert result[0].issue_level == IssueSeverity.MEDIUM
        assert "hallucinated" in result[0].description

    def test_TA_U_07_merged_content_none_preserves_legacy(self) -> None:
        raw = {
            "issues": [
                _issue(
                    "high",
                    evidence_excerpt="ghost_line",
                    affected_lines=[10],
                )
            ]
        }
        result = parse_file_review_issues(raw, "x.py")
        assert result[0].issue_level == IssueSeverity.HIGH
        assert "hallucinated" not in result[0].description

    def test_TA_U_08_evidence_all_whitespace_skips_new_check(self) -> None:
        raw = {"issues": [_issue("high", evidence_excerpt="   ", affected_lines=[10])]}
        result = parse_file_review_issues(raw, "x.py", merged_content="abc")
        assert "hallucinated" not in result[0].description


class TestBatchHallucinationGuard:
    def test_TA_U_09_all_files_evidence_grounded(self) -> None:
        raw = {
            "files": [
                {
                    "file_path": "a.py",
                    "issues": [
                        _issue(
                            "high",
                            evidence_excerpt="aa",
                            affected_lines=[1],
                            file_path="a.py",
                        )
                    ],
                },
                {
                    "file_path": "b.py",
                    "issues": [
                        _issue(
                            "high",
                            evidence_excerpt="bb",
                            affected_lines=[1],
                            file_path="b.py",
                        )
                    ],
                },
            ]
        }
        result = parse_batch_file_review_issues(
            raw,
            ["a.py", "b.py"],
            merged_contents={"a.py": "xx\naa\n", "b.py": "yy\nbb\n"},
        )
        assert result["a.py"][0].issue_level == IssueSeverity.HIGH
        assert result["b.py"][0].issue_level == IssueSeverity.HIGH

    def test_TA_U_10_mixed_one_hit_one_miss(self) -> None:
        raw = {
            "files": [
                {
                    "file_path": "a.py",
                    "issues": [
                        _issue(
                            "high",
                            evidence_excerpt="aa",
                            affected_lines=[1],
                            file_path="a.py",
                        )
                    ],
                },
                {
                    "file_path": "b.py",
                    "issues": [
                        _issue(
                            "high",
                            evidence_excerpt="ghost",
                            affected_lines=[1],
                            file_path="b.py",
                        )
                    ],
                },
            ]
        }
        result = parse_batch_file_review_issues(
            raw,
            ["a.py", "b.py"],
            merged_contents={"a.py": "xx\naa\n", "b.py": "yy\nzz\n"},
        )
        assert result["a.py"][0].issue_level == IssueSeverity.HIGH
        assert "hallucinated" not in result["a.py"][0].description
        assert result["b.py"][0].issue_level == IssueSeverity.MEDIUM
        assert "hallucinated" in result["b.py"][0].description

    def test_TA_U_11_missing_key_falls_back_to_legacy(self) -> None:
        raw = {
            "files": [
                {
                    "file_path": "a.py",
                    "issues": [
                        _issue(
                            "high",
                            evidence_excerpt="ghost",
                            affected_lines=[1],
                            file_path="a.py",
                        )
                    ],
                },
                {
                    "file_path": "b.py",
                    "issues": [
                        _issue(
                            "high",
                            evidence_excerpt="ghost",
                            affected_lines=[1],
                            file_path="b.py",
                        )
                    ],
                },
            ]
        }
        result = parse_batch_file_review_issues(
            raw, ["a.py", "b.py"], merged_contents={"a.py": "ghost\n"}
        )
        assert result["a.py"][0].issue_level == IssueSeverity.HIGH
        assert result["b.py"][0].issue_level == IssueSeverity.HIGH
        assert "hallucinated" not in result["b.py"][0].description

    def test_TA_U_12_unparseable_raw_returns_empty_dict(self) -> None:
        result = parse_batch_file_review_issues(
            "<<garbage>>",
            ["a.py", "b.py"],
            merged_contents={"a.py": "", "b.py": ""},
        )
        assert result == {"a.py": [], "b.py": []}

    def test_TA_U_13_merged_contents_wrong_type_raises(self) -> None:
        raw = {
            "files": [
                {
                    "file_path": "a.py",
                    "issues": [
                        _issue(
                            "high",
                            evidence_excerpt="x",
                            affected_lines=[1],
                            file_path="a.py",
                        )
                    ],
                }
            ]
        }
        with pytest.raises(TypeError):
            parse_batch_file_review_issues(
                raw,
                ["a.py"],
                merged_contents=["not", "a", "dict"],  # type: ignore[arg-type]
            )


class TestBatchReviewFailClosed:
    """#3A: unparseable batch verdict must fail closed when strict_json=True."""

    def test_unparseable_returns_empty_by_default(self) -> None:
        # Legacy contract preserved: non-strict swallows bad JSON to empty.
        out = parse_batch_file_review_issues("not json at all", ["a.ts"])
        assert out == {"a.ts": []}

    def test_unparseable_raises_when_strict(self) -> None:
        from src.llm.client import ParseError

        with pytest.raises(ParseError):
            parse_batch_file_review_issues(
                "not json at all", ["a.ts"], strict_json=True
            )

    def test_valid_empty_issues_does_not_raise_when_strict(self) -> None:
        out = parse_batch_file_review_issues(
            '{"files": []}', ["a.ts"], strict_json=True
        )
        assert out == {"a.ts": []}
