"""Compare a checked-in ``forks-profile.yaml`` against a fresh heuristic draft.

Powers ``merge forks-profile diff`` (§4.2). The contract:

  - The yaml is the **single source of truth**. Heuristics never
    auto-rewrite the yaml — they only surface three classes of drift:

      📋 ``unmatched_declarations``    — yaml entry has no matching
                                          heuristic finding (likely
                                          deletable / over-declared)
      ➕ ``unmatched_heuristics``      — heuristic candidate not yet
                                          declared (may be a new
                                          rewrite / removal / fork-only
                                          subtree)
      🔄 ``classification_mismatches`` — same path lives in both, but
                                          policy / category disagrees
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.models.forks_profile import (
    ForksProfile,
    RemovedDomain,
    RewriteMergePolicy,
)
from src.tools.file_classifier import matches_any_pattern
from src.tools.forks_profile_drafter import (
    DraftedProfile,
    DraftedRemovedDomain,
)


@dataclass(frozen=True)
class DiffEntry:
    """One line item shown to the reviewer."""

    category: str
    identifier: str
    rationale: str


@dataclass(frozen=True)
class ProfileDiff:
    unmatched_declarations: tuple[DiffEntry, ...] = field(default_factory=tuple)
    unmatched_heuristics: tuple[DiffEntry, ...] = field(default_factory=tuple)
    classification_mismatches: tuple[DiffEntry, ...] = field(default_factory=tuple)

    def is_empty(self) -> bool:
        return not (
            self.unmatched_declarations
            or self.unmatched_heuristics
            or self.classification_mismatches
        )


def _heuristic_paths_from_removed(
    d: tuple[DraftedRemovedDomain, ...],
) -> list[str]:
    out: list[str] = []
    for entry in d:
        out.extend(entry.paths)
    return out


def _yaml_paths_from_removed(d: list[RemovedDomain]) -> list[str]:
    out: list[str] = []
    for entry in d:
        out.extend(entry.paths)
    return out


def _matches_any_in(needle_globs: list[str], hay_paths: list[str]) -> bool:
    """True iff at least one path in ``hay_paths`` matches any needle glob.

    Used to compare a yaml-declared glob with a heuristic file/cluster:
    declarations are typically broad (``svc/auth/**``) while heuristic
    output may be narrower clusters or orphan paths, so a directional
    contains-match is the right join key.
    """
    return any(matches_any_pattern(p, needle_globs) for p in hay_paths)


def _heuristic_glob_already_covered(candidate: str, declared_globs: list[str]) -> bool:
    """True iff a yaml declaration already covers the heuristic's cluster glob."""
    if candidate in declared_globs:
        return True
    if candidate.endswith("/**"):
        stripped = candidate[: -len("/**")]
        if matches_any_pattern(stripped, declared_globs):
            return True
    return False


def diff_profile_vs_heuristic(
    profile: ForksProfile | None, drafted: DraftedProfile
) -> ProfileDiff:
    """Surface the three drift categories from §4.2."""
    unmatched_declarations: list[DiffEntry] = []
    unmatched_heuristics: list[DiffEntry] = []
    classification_mismatches: list[DiffEntry] = []

    yaml_removed = list(profile.removed_domains) if profile else []
    yaml_rewritten = list(profile.rewritten_modules) if profile else []

    drafted_rewritten_paths = [m.path for m in drafted.rewritten_modules]
    drafted_removed_paths = _heuristic_paths_from_removed(drafted.removed_domains)

    for entry in yaml_removed:
        if entry.paths and not _matches_any_in(entry.paths, drafted_removed_paths):
            unmatched_declarations.append(
                DiffEntry(
                    category="removed_domain",
                    identifier=entry.name or ",".join(entry.paths),
                    rationale=(
                        "no FORK_DELETED files match path_globs anymore; "
                        "domain may have been re-introduced or upstream also "
                        "deleted it (now A-class, no declaration needed)"
                    ),
                )
            )
    for module in yaml_rewritten:
        if not _matches_any_in([module.path], drafted_rewritten_paths):
            unmatched_declarations.append(
                DiffEntry(
                    category="rewritten_module",
                    identifier=module.path,
                    rationale=(
                        "no FORK_MODIFIED files under this glob still pass the "
                        "rewrite heuristic (retention recovered or fork stopped "
                        "diverging); consider downgrade or removal"
                    ),
                )
            )

    yaml_removed_globs = _yaml_paths_from_removed(yaml_removed)
    yaml_rewritten_globs = [m.path for m in yaml_rewritten]

    for d_entry in drafted.removed_domains:
        if not _matches_any_in(list(d_entry.paths), yaml_removed_globs):
            unmatched_heuristics.append(
                DiffEntry(
                    category="removed_domain",
                    identifier=d_entry.name + " (" + ", ".join(d_entry.paths) + ")",
                    rationale=(
                        "FORK_DELETED files detected but no removed_domain "
                        "declares this glob; add to yaml or confirm intentional"
                    ),
                )
            )
    for d_module in drafted.rewritten_modules:
        if not _heuristic_glob_already_covered(d_module.path, yaml_rewritten_globs):
            unmatched_heuristics.append(
                DiffEntry(
                    category="rewritten_module",
                    identifier=d_module.path,
                    rationale=d_module.note,
                )
            )

    for module in yaml_rewritten:
        for d_module in drafted.rewritten_modules:
            covered = module.path == d_module.path or _heuristic_glob_already_covered(
                d_module.path, [module.path]
            )
            if not covered:
                continue
            if (
                module.policy != RewriteMergePolicy.ESCALATE_HUMAN
                and d_module.policy == RewriteMergePolicy.ESCALATE_HUMAN
            ):
                classification_mismatches.append(
                    DiffEntry(
                        category="rewritten_module",
                        identifier=module.path,
                        rationale=(
                            f"yaml policy={module.policy.value} but "
                            f"heuristic retention still triggers "
                            f"escalate_human ({d_module.note})"
                        ),
                    )
                )
            break

    return ProfileDiff(
        unmatched_declarations=tuple(unmatched_declarations),
        unmatched_heuristics=tuple(unmatched_heuristics),
        classification_mismatches=tuple(classification_mismatches),
    )


def format_profile_diff(diff: ProfileDiff) -> str:
    """Human-readable rendering used by the CLI."""
    if diff.is_empty():
        return "✓ profile and heuristic agree — no drift detected.\n"

    chunks: list[str] = []
    if diff.unmatched_declarations:
        chunks.append("📋 已声明但启发式不再检出 (可能可删):")
        for e in diff.unmatched_declarations:
            chunks.append(f"  - {e.category}[{e.identifier}]")
            chunks.append(f"    rationale: {e.rationale}")
        chunks.append("")

    if diff.unmatched_heuristics:
        chunks.append("➕ 启发式新检出但 yaml 未声明:")
        for e in diff.unmatched_heuristics:
            chunks.append(f"  - {e.category} candidate: {e.identifier}")
            chunks.append(f"    {e.rationale}")
        chunks.append("")

    if diff.classification_mismatches:
        chunks.append("🔄 yaml 与启发式分类不一致:")
        for e in diff.classification_mismatches:
            chunks.append(f"  - {e.category}[{e.identifier}]")
            chunks.append(f"    {e.rationale}")
        chunks.append("")

    return "\n".join(chunks).rstrip() + "\n"


__all__ = [
    "DiffEntry",
    "ProfileDiff",
    "diff_profile_vs_heuristic",
    "format_profile_diff",
]
