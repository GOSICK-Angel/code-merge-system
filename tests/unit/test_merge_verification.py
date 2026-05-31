"""Unit tests for the deterministic merge-artifact verification aggregator.

Runs the LLM-free checks (duplicate top-level symbols, dropped additive
fork exports) over a merge's changed files and aggregates structured
findings. This is the deterministic core of the post-merge verification gate
that turns the zod failure modes (uncompilable duplicate blocks, silently
dropped ``cidrv6Mapped``) into explicit, reported findings.
"""

from __future__ import annotations

from src.tools.merge_verification import (
    FileVerificationInput,
    gather_findings_from_git,
    verify_merge_artifacts,
)


class _FakeGit:
    """Duck-typed GitTool exposing only get_file_content for tests."""

    def __init__(self, by_ref: dict[str, dict[str, str | None]]):
        self._by_ref = by_ref

    def get_file_content(self, ref: str, file_path: str) -> str | None:
        return self._by_ref.get(ref, {}).get(file_path)


class TestVerifyMergeArtifacts:
    def test_duplicate_symbol_finding(self):
        merged = (
            "export const ZodNumberFormat = a();\nexport const ZodNumberFormat = a();\n"
        )
        findings = verify_merge_artifacts(
            [FileVerificationInput(file_path="schemas.ts", merged_content=merged)]
        )
        assert len(findings) == 1
        assert findings[0].check == "duplicate_symbol"
        assert findings[0].file_path == "schemas.ts"
        assert "ZodNumberFormat" in findings[0].detail
        assert findings[0].severity == "high"

    def test_dropped_additive_export_finding(self):
        base = "export const cidrv6 = /a/;\n"
        fork = "export const cidrv6 = /a/;\nexport const cidrv6Mapped = /b/;\n"
        merged = "export const cidrv6 = /upstream/;\n"  # mapped dropped
        findings = verify_merge_artifacts(
            [
                FileVerificationInput(
                    file_path="regexes.ts",
                    merged_content=merged,
                    base_content=base,
                    fork_content=fork,
                )
            ]
        )
        assert len(findings) == 1
        assert findings[0].check == "missing_additive_export"
        assert "cidrv6Mapped" in findings[0].detail

    def test_clean_file_no_findings(self):
        base = "export const a = 1;\n"
        fork = "export const a = 1;\nexport const b = 2;\n"
        merged = "export const a = 9;\nexport const b = 2;\n"  # b preserved
        findings = verify_merge_artifacts(
            [
                FileVerificationInput(
                    file_path="x.ts",
                    merged_content=merged,
                    base_content=base,
                    fork_content=fork,
                )
            ]
        )
        assert findings == []

    def test_missing_export_skipped_without_base_and_fork(self):
        # No base/fork content → only the duplicate check runs; a missing
        # additive export cannot be computed and must not be invented.
        merged = "export const only = 1;\n"
        findings = verify_merge_artifacts(
            [FileVerificationInput(file_path="x.ts", merged_content=merged)]
        )
        assert findings == []

    def test_aggregates_across_files(self):
        files = [
            FileVerificationInput(
                file_path="dup.ts",
                merged_content="export const X = 1;\nexport const X = 1;\n",
            ),
            FileVerificationInput(
                file_path="drop.ts",
                merged_content="export const a = 1;\n",
                base_content="export const a = 1;\n",
                fork_content="export const a = 1;\nexport const feat = 2;\n",
            ),
        ]
        findings = verify_merge_artifacts(files)
        checks = sorted(f.check for f in findings)
        assert checks == ["duplicate_symbol", "missing_additive_export"]


class TestGatherFindingsFromGit:
    def test_reads_three_refs_and_finds_dropped_export(self):
        git = _FakeGit(
            {
                "base": {"r.ts": "export const cidrv6 = /a/;\n"},
                "fork": {
                    "r.ts": "export const cidrv6 = /a/;\n"
                    "export const cidrv6Mapped = /b/;\n"
                },
                "HEAD": {"r.ts": "export const cidrv6 = /upstream/;\n"},
            }
        )
        findings = gather_findings_from_git(
            git, ["r.ts"], base_ref="base", fork_ref="fork", merged_ref="HEAD"
        )
        assert [f.check for f in findings] == ["missing_additive_export"]
        assert "cidrv6Mapped" in findings[0].detail

    def test_finds_duplicate_symbol_in_merged(self):
        git = _FakeGit(
            {"HEAD": {"s.ts": "export const X = a();\nexport const X = a();\n"}}
        )
        findings = gather_findings_from_git(
            git, ["s.ts"], base_ref="base", fork_ref="fork", merged_ref="HEAD"
        )
        assert [f.check for f in findings] == ["duplicate_symbol"]

    def test_skips_file_absent_from_merged_ref(self):
        # Deleted by the merge → no merged content → nothing to verify.
        git = _FakeGit(
            {
                "base": {"gone.ts": "export const a = 1;\n"},
                "fork": {"gone.ts": "export const a = 1;\nexport const b = 2;\n"},
                "HEAD": {"gone.ts": None},
            }
        )
        findings = gather_findings_from_git(
            git, ["gone.ts"], base_ref="base", fork_ref="fork", merged_ref="HEAD"
        )
        assert findings == []

    def test_missing_base_ref_runs_only_duplicate_check(self):
        # base_ref=None → additive-export check cannot run; duplicate still does.
        git = _FakeGit({"HEAD": {"s.ts": "export const X = 1;\nexport const X = 1;\n"}})
        findings = gather_findings_from_git(
            git, ["s.ts"], base_ref=None, fork_ref="fork", merged_ref="HEAD"
        )
        assert [f.check for f in findings] == ["duplicate_symbol"]

    def test_clean_merge_no_findings(self):
        git = _FakeGit(
            {
                "base": {"x.ts": "export const a = 1;\n"},
                "fork": {"x.ts": "export const a = 1;\nexport const b = 2;\n"},
                "HEAD": {"x.ts": "export const a = 9;\nexport const b = 2;\n"},
            }
        )
        findings = gather_findings_from_git(
            git, ["x.ts"], base_ref="base", fork_ref="fork", merged_ref="HEAD"
        )
        assert findings == []
