from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from src.models.diff import FileChangeCategory
from src.tools.git_tool import GitTool

logger = logging.getLogger(__name__)


class PollutedFile(BaseModel):
    file_path: str
    original_category: FileChangeCategory
    corrected_category: FileChangeCategory
    reason: str
    source_commit: str = ""


class PollutionAuditReport(BaseModel):
    prior_merge_commits: list[str] = Field(default_factory=list)
    files_from_prior_merges: list[str] = Field(default_factory=list)
    polluted_files: list[PollutedFile] = Field(default_factory=list)
    total_files_audited: int = 0
    reclassified_count: int = 0
    clean: bool = True

    @property
    def has_pollution(self) -> bool:
        return len(self.polluted_files) > 0


class PollutionAuditor:
    def __init__(self, git_tool: GitTool):
        self.git_tool = git_tool

    def audit(
        self,
        merge_base: str,
        head_ref: str,
        upstream_ref: str,
        file_categories: dict[str, FileChangeCategory],
    ) -> PollutionAuditReport:
        prior_commits = self._find_prior_merge_commits(head_ref)

        if not prior_commits:
            return PollutionAuditReport(
                total_files_audited=len(file_categories),
                clean=True,
            )

        prior_files = self._collect_files_from_commits(prior_commits)

        polluted: list[PollutedFile] = []

        for fp, original_cat in file_categories.items():
            if fp not in prior_files:
                continue

            source_commit = prior_files[fp]
            corrected = self._reclassify(
                fp, original_cat, merge_base, head_ref, upstream_ref, source_commit
            )
            if corrected is not None and corrected != original_cat:
                polluted.append(
                    PollutedFile(
                        file_path=fp,
                        original_category=original_cat,
                        corrected_category=corrected,
                        reason=self._describe_reason(original_cat, corrected),
                        source_commit=source_commit,
                    )
                )

        return PollutionAuditReport(
            prior_merge_commits=prior_commits,
            files_from_prior_merges=sorted(prior_files.keys()),
            polluted_files=polluted,
            total_files_audited=len(file_categories),
            reclassified_count=len(polluted),
            clean=len(polluted) == 0,
        )

    def apply_corrections(
        self,
        file_categories: dict[str, FileChangeCategory],
        report: PollutionAuditReport,
    ) -> dict[str, FileChangeCategory]:
        corrected = dict(file_categories)
        for pf in report.polluted_files:
            corrected[pf.file_path] = pf.corrected_category
        return corrected

    def _find_prior_merge_commits(self, head_ref: str) -> list[str]:
        try:
            output = self.git_tool.repo.git.log(
                "--grep=merge.*upstream\\|Merge.*upstream\\|merge.*release",
                "--regexp-ignore-case",
                "--oneline",
                "-20",
                head_ref,
            )
        except Exception:
            return []

        commits: list[str] = []
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            sha = line.split()[0] if line.split() else ""
            if sha:
                commits.append(sha)
        return commits

    def _collect_files_from_commits(self, commits: list[str]) -> dict[str, str]:
        file_to_commit: dict[str, str] = {}
        for sha in commits:
            try:
                output = self.git_tool.repo.git.diff_tree(
                    "--no-commit-id", "-r", "--name-only", sha
                )
            except Exception:
                continue
            for line in output.splitlines():
                fp = line.strip()
                if fp and fp not in file_to_commit:
                    file_to_commit[fp] = sha
        return file_to_commit

    def _reclassify(
        self,
        file_path: str,
        original: FileChangeCategory,
        merge_base: str,
        head_ref: str,
        upstream_ref: str,
        source_commit: str,
    ) -> FileChangeCategory | None:
        base_hash = self.git_tool.get_file_hash(merge_base, file_path)
        head_hash = self.git_tool.get_file_hash(head_ref, file_path)
        up_hash = self.git_tool.get_file_hash(upstream_ref, file_path)

        if original == FileChangeCategory.A and head_hash != up_hash:
            if head_hash == base_hash:
                return FileChangeCategory.B
            if up_hash == base_hash:
                return FileChangeCategory.E
            return FileChangeCategory.C

        if original == FileChangeCategory.E and up_hash != base_hash:
            if head_hash == up_hash:
                return FileChangeCategory.A
            return FileChangeCategory.C

        if original == FileChangeCategory.B and head_hash != base_hash:
            if head_hash == up_hash:
                return FileChangeCategory.A
            return FileChangeCategory.C

        return None

    def _describe_reason(
        self,
        original: FileChangeCategory,
        corrected: FileChangeCategory,
    ) -> str:
        descriptions = {
            (FileChangeCategory.A, FileChangeCategory.C): (
                "A-overwritten: file was classified as unchanged but prior merge "
                "introduced changes making it a both-changed file"
            ),
            (FileChangeCategory.A, FileChangeCategory.B): (
                "A-overwritten: prior merge residue detected, "
                "file should adopt upstream"
            ),
            (FileChangeCategory.A, FileChangeCategory.E): (
                "A-overwritten: prior merge residue detected, "
                "file has current-only changes"
            ),
            (FileChangeCategory.E, FileChangeCategory.A): (
                "E-residue: file appeared as current-only change but is actually "
                "upstream residue from prior merge"
            ),
            (FileChangeCategory.E, FileChangeCategory.C): (
                "E-residue: file has both current and upstream changes, "
                "prior merge masked upstream delta"
            ),
            (FileChangeCategory.B, FileChangeCategory.A): (
                "B-partial: file already contains upstream code from prior merge, "
                "now identical"
            ),
            (FileChangeCategory.B, FileChangeCategory.C): (
                "B-partial: file already contains partial upstream code, "
                "requires three-way merge"
            ),
        }
        return descriptions.get(
            (original, corrected),
            f"Reclassified from {original.value} to {corrected.value} due to prior merge pollution",
        )
