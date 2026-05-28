"""Deterministic post-merge artifact verification (LLM-free).

Aggregates the static checks in :mod:`src.tools.duplicate_symbol_check` and
:mod:`src.tools.feature_preservation` over a merge's changed files into a flat
list of findings. The report-generation phase runs this before finalizing so
that the failure modes observed in the zod merge test — an uncompilable
duplicated declaration block, a silently dropped additive fork export — surface
as explicit findings instead of a green ``COMPLETED``.

Pure and synchronous: callers supply the file contents (read from git), this
module owns no I/O.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from pydantic import BaseModel

from src.tools.duplicate_symbol_check import find_duplicate_symbols
from src.tools.feature_preservation import added_exported_symbols, missing_symbols


class GitFileReader(Protocol):
    """Minimal structural type for reading a file's content at a git ref.

    Satisfied by :class:`src.tools.git_tool.GitTool`; declared as a Protocol so
    callers (and tests) can supply any object exposing this one method.
    """

    def get_file_content(self, ref: str, file_path: str) -> str | None: ...


class FileVerificationInput(BaseModel):
    """One changed file's contents for verification.

    ``base_content`` and ``fork_content`` are optional; when either is absent
    the additive-export check is skipped (it cannot be computed without both),
    while the duplicate-symbol check always runs on ``merged_content``.
    """

    file_path: str
    merged_content: str
    base_content: str | None = None
    fork_content: str | None = None


class VerificationFinding(BaseModel):
    """A single deterministic problem found in a merged file."""

    file_path: str
    check: str  # "duplicate_symbol" | "missing_additive_export"
    severity: str  # "high"
    detail: str


def verify_merge_artifacts(
    files: list[FileVerificationInput],
) -> list[VerificationFinding]:
    """Return deterministic findings across all *files*.

    Findings are ordered by input file, duplicate-symbol checks before
    additive-export checks, so output is stable.
    """
    findings: list[VerificationFinding] = []

    for f in files:
        for dup in find_duplicate_symbols(f.merged_content, f.file_path):
            findings.append(
                VerificationFinding(
                    file_path=f.file_path,
                    check="duplicate_symbol",
                    severity="high",
                    detail=(
                        f"{dup.kind} '{dup.name}' declared {dup.count}x at "
                        f"top level (lines {dup.lines}); "
                        f"cannot redeclare — likely a chunk-merge duplication"
                    ),
                )
            )

        if f.base_content is not None and f.fork_content is not None:
            added = added_exported_symbols(f.base_content, f.fork_content, f.file_path)
            for sym in sorted(missing_symbols(f.merged_content, added, f.file_path)):
                findings.append(
                    VerificationFinding(
                        file_path=f.file_path,
                        check="missing_additive_export",
                        severity="high",
                        detail=(
                            f"fork-added export '{sym}' is absent from the "
                            f"merged file — additive customization dropped"
                        ),
                    )
                )

    return findings


def gather_findings_from_git(
    git: GitFileReader,
    file_paths: Iterable[str],
    *,
    base_ref: str | None,
    fork_ref: str | None,
    merged_ref: str,
) -> list[VerificationFinding]:
    """Read each path's content at the three refs and verify the merge result.

    ``merged_ref`` is the post-merge ref (e.g. ``"HEAD"`` of the working
    branch); ``base_ref`` / ``fork_ref`` feed the additive-export check and may
    be ``None`` (then only the duplicate-symbol check runs). A path absent from
    ``merged_ref`` (deleted by the merge) is skipped — there is nothing to
    verify.
    """
    inputs: list[FileVerificationInput] = []
    for path in file_paths:
        merged = git.get_file_content(merged_ref, path)
        if merged is None:
            continue
        base = git.get_file_content(base_ref, path) if base_ref else None
        fork = git.get_file_content(fork_ref, path) if fork_ref else None
        inputs.append(
            FileVerificationInput(
                file_path=path,
                merged_content=merged,
                base_content=base,
                fork_content=fork,
            )
        )
    return verify_merge_artifacts(inputs)
