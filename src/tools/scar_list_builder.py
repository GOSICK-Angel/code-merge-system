"""P2-1: ScarListBuilder — learn from historical restore/revert/compat-fix commits.

Scans git log for commits whose subject matches configurable patterns
(default: "restore", "fix.*compat", "revert") and converts them into
``CustomizationEntry`` objects so the next merge starts with an
auto-populated protection registry.

Design principles:
- Zero repo-specific knowledge in this file.
- grep_patterns are user-overridable; defaults are language/project-agnostic.
- CustomizationEntry source is tagged "scar_learned" for easy auditing.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import git
from git import InvalidGitRepositoryError
from pydantic import BaseModel

if TYPE_CHECKING:
    from src.models.config import CustomizationEntry


class Scar(BaseModel):
    """A single historical "we had to restore this" data point."""

    commit_sha: str
    commit_subject: str
    files: list[str]
    pattern_kind: Literal["restore", "fix_compat", "revert", "feature"]

    model_config = {"frozen": True}


DEFAULT_GREP_PATTERNS: list[str] = [
    r"restore",
    r"fix[._\-\s]?compat",
    r"revert",
]

DEFAULT_FEATURE_PATTERNS: list[str] = [
    r"^feat[\(:]",
    r"^feature[\(:]",
    r"^add\s",
    r"^implement\s",
]

_KIND_MAP: list[tuple[str, Literal["restore", "fix_compat", "revert"]]] = [
    (r"restore", "restore"),
    (r"fix[._\-\s]?compat", "fix_compat"),
    (r"revert", "revert"),
]


def _classify_kind(
    subject: str,
) -> Literal["restore", "fix_compat", "revert"]:
    lower = subject.lower()
    for pattern, kind in _KIND_MAP:
        if re.search(pattern, lower):
            return kind
    return "restore"


class ScarListBuilder:
    """Build a list of Scars from git history and optionally convert them to
    ``CustomizationEntry`` objects ready to be appended to a merge config.

    Usage::

        builder = ScarListBuilder()
        scars = builder.build(repo_path=Path("/path/to/repo"), since="1 year ago")
        new_entries = builder.materialize_as_customizations(scars, existing=[])
    """

    def build(
        self,
        repo_path: Path,
        since: str = "1 year ago",
        grep_patterns: list[str] | None = None,
    ) -> list[Scar]:
        """Scan git history for matching commits and return Scar objects.

        Args:
            repo_path: Repository root (or any sub-path; GitPython will find root).
            since: Human-readable git date spec ("1 year ago", "2025-01-01", etc.).
            grep_patterns: Regex patterns to match against commit subjects.
                           Defaults to ``DEFAULT_GREP_PATTERNS``.

        Returns:
            Deduplicated list of Scar objects (by commit sha).
        """
        patterns = grep_patterns if grep_patterns is not None else DEFAULT_GREP_PATTERNS
        try:
            repo = git.Repo(str(repo_path), search_parent_directories=True)
        except (InvalidGitRepositoryError, Exception):
            return []

        combined = "|".join(f"(?:{p})" for p in patterns)
        compiled = re.compile(combined, re.IGNORECASE)

        scars: list[Scar] = []
        seen_shas: set[str] = set()

        try:
            log_output: str = repo.git.log(
                f"--since={since}",
                "--format=%H|%s",
                "--no-merges",
            )
        except Exception:
            return []

        for line in log_output.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("|", 1)
            if len(parts) != 2:
                continue
            sha, subject = parts[0].strip(), parts[1].strip()
            if sha in seen_shas:
                continue
            if not compiled.search(subject):
                continue

            files = self._get_commit_files(repo, sha)
            if not files:
                continue

            seen_shas.add(sha)
            scars.append(
                Scar(
                    commit_sha=sha,
                    commit_subject=subject,
                    files=files,
                    pattern_kind=_classify_kind(subject),
                )
            )

        return scars

    def build_from_feature_commits(
        self,
        repo_path: Path,
        fork_ref: str,
        base_ref: str,
        feature_patterns: list[str] | None = None,
    ) -> list[Scar]:
        """P2-1: scan ``base_ref..fork_ref`` for feature-add commits.

        ``build()`` only catches restore/revert/compat-fix. Live forks (e.g.
        cvte's dify plugin daemon) protect customizations through positive
        commits — ``feat: add Cvte SSO``, ``add custom auth``, ``implement
        plugin reload``. Those introduce fork-only files that must be
        registered as ``CustomizationEntry`` so the next merge can verify
        they survived.

        Args:
            repo_path: Repository root.
            fork_ref: Fork branch / sha (HEAD of customizations).
            base_ref: Merge-base / upstream sha (commits reachable from
                here are excluded).
            feature_patterns: Override regex patterns matched against commit
                subjects. Defaults to ``DEFAULT_FEATURE_PATTERNS``.

        Returns:
            Deduplicated list of ``Scar`` objects with ``pattern_kind``
            ``"feature"``.
        """
        patterns = (
            feature_patterns
            if feature_patterns is not None
            else DEFAULT_FEATURE_PATTERNS
        )
        try:
            repo = git.Repo(str(repo_path), search_parent_directories=True)
        except (InvalidGitRepositoryError, Exception):
            return []

        combined = "|".join(f"(?:{p})" for p in patterns)
        compiled = re.compile(combined, re.IGNORECASE)

        try:
            log_output: str = repo.git.log(
                f"{base_ref}..{fork_ref}",
                "--format=%H|%s",
                "--no-merges",
            )
        except Exception:
            return []

        scars: list[Scar] = []
        seen_shas: set[str] = set()
        for line in log_output.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("|", 1)
            if len(parts) != 2:
                continue
            sha, subject = parts[0].strip(), parts[1].strip()
            if sha in seen_shas:
                continue
            if not compiled.search(subject):
                continue

            files = self._get_commit_files(repo, sha)
            if not files:
                continue

            seen_shas.add(sha)
            scars.append(
                Scar(
                    commit_sha=sha,
                    commit_subject=subject,
                    files=files,
                    pattern_kind="feature",
                )
            )

        return scars

    @staticmethod
    def _get_commit_files(repo: git.Repo, sha: str) -> list[str]:
        try:
            output: str = repo.git.diff_tree("--no-commit-id", "--name-only", "-r", sha)
            return [f.strip() for f in output.splitlines() if f.strip()]
        except Exception:
            return []

    def auto_learn(
        self,
        repo_path: Path,
        fork_ref: str,
        base_ref: str,
        *,
        since: str = "1 year ago",
        grep_patterns: list[str] | None = None,
        feature_patterns: list[str] | None = None,
        existing: list[CustomizationEntry] | None = None,
    ) -> list[CustomizationEntry]:
        """P2-3: one-shot orchestration that wires both Scar sources together.

        Combines:
        - ``build()`` (restore / revert / fix-compat over the whole history)
        - ``build_from_feature_commits()`` (feat: / add / implement in the
          ``base_ref..fork_ref`` range)

        Then materializes the union as ``CustomizationEntry`` objects, with
        files already covered by ``existing`` skipped to avoid duplicates.

        Returns ``[]`` when the repo is invalid or when no matching commits
        exist — matches the silent-fallback behaviour of the underlying
        builders so callers can splat the result into config without guards.
        """
        legacy_scars = self.build(
            repo_path=repo_path,
            since=since,
            grep_patterns=grep_patterns,
        )
        feature_scars = self.build_from_feature_commits(
            repo_path=repo_path,
            fork_ref=fork_ref,
            base_ref=base_ref,
            feature_patterns=feature_patterns,
        )
        combined = legacy_scars + feature_scars
        if not combined:
            return []
        return self.materialize_as_customizations(combined, existing or [])

    def materialize_as_customizations(
        self,
        scars: list[Scar],
        existing: list[CustomizationEntry],
    ) -> list[CustomizationEntry]:
        """Convert Scars into new CustomizationEntry objects.

        Only files not already covered by an existing entry are emitted.
        The confidence reflects frequency: files touched by >1 scar get 0.9,
        single-occurrence files get 0.7.

        Args:
            scars: Output of ``build()``.
            existing: Current customizations (to avoid duplicating coverage).

        Returns:
            New CustomizationEntry objects tagged ``source="scar_learned"``.
        """
        from src.models.config import CustomizationEntry, CustomizationVerification

        existing_files: set[str] = set()
        for entry in existing:
            existing_files.update(entry.files)

        file_occurrences: dict[str, int] = {}
        for scar in scars:
            for fp in scar.files:
                file_occurrences[fp] = file_occurrences.get(fp, 0) + 1

        grouped: dict[str, list[Scar]] = {}
        for scar in scars:
            for fp in scar.files:
                if fp in existing_files:
                    continue
                grouped.setdefault(fp, []).append(scar)

        new_entries: list[CustomizationEntry] = []
        for fp, fp_scars in grouped.items():
            count = file_occurrences.get(fp, 1)
            confidence = 0.9 if count > 1 else 0.7

            representative = fp_scars[0]
            name = f"scar:{fp}"
            description = (
                f"Auto-learned from {len(fp_scars)} historical "
                f"{representative.pattern_kind} commit(s). "
                f'Example: "{representative.commit_subject[:80]}"'
            )
            new_entries.append(
                CustomizationEntry(
                    name=name,
                    description=description,
                    files=[fp],
                    verification=[
                        CustomizationVerification(
                            type="file_exists",
                            files=[fp],
                        )
                    ],
                    source="scar_learned",
                    confidence=confidence,
                )
            )

        return new_entries
