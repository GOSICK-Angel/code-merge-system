"""Auto-draft a `forks-profile.yaml` from observable git divergence.

Implements `merge forks-profile init`. Given a (merge_base, fork_ref,
upstream_ref) triple this module produces a conservative, reviewable
draft of the four entry types defined in
``src.models.forks_profile.ForksProfile``:

  - ``fork_only_features``  — directly from FORK_ONLY paths
  - ``removed_domains``     — directly from FORK_DELETED paths
  - ``rewritten_modules``   — from FORK_MODIFIED paths passing a
                              line-retention / volume / commit-count
                              heuristic
  - ``migration_policy``    — when fork-only migrations occupy a
                              numeric range above the upstream max

The draft is **always** printed with ``# TODO: review`` markers and
permissive policies (``escalate_human``) so a fork maintainer's only
risk on review is over-classification, never silent under-classification.

Pure functions (``draft_fork_only_features``, ``draft_removed_domains``,
``draft_rewritten_modules``, ``draft_migration_policy``) take prepared
inputs and return frozen dataclasses; ``draft_profile`` orchestrates a
``GitTool`` to feed them. Tests can call the pure layer with synthetic
divergence maps to exercise every branch without touching disk.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from src.models.diff import ForkDivergence
from src.models.forks_profile import RewriteMergePolicy
from src.tools.file_classifier import compute_fork_divergence_map, matches_any_pattern
from src.tools.forks_profile_loader import extract_migration_number
from src.tools.git_tool import GitTool


DEFAULT_MIGRATION_GLOBS: tuple[str, ...] = (
    "**/migrations/*.sql",
    "**/migrations/*.py",
    "**/db/migrate/*.sql",
    "**/db/migrate/*.py",
    "**/db/migrate/*.rb",
    "**/alembic/versions/*.py",
)


@dataclass(frozen=True)
class ClusterEntry:
    """One trie-folded subtree (or orphan path)."""

    glob: str
    count: int
    paths: tuple[str, ...]


@dataclass(frozen=True)
class DraftedRemovedDomain:
    name: str
    paths: tuple[str, ...]
    reason: str
    removed_in: str


@dataclass(frozen=True)
class DraftedRewrittenModule:
    path: str
    policy: RewriteMergePolicy
    note: str


@dataclass(frozen=True)
class DraftedForkOnlyFeature:
    path: str
    note: str


@dataclass(frozen=True)
class DraftedMigrationPolicy:
    path_globs: tuple[str, ...]
    fork_owns_numbers_above: int
    upstream_take_target_max: int
    on_collision: str  # always "escalate_human" for drafted output


@dataclass(frozen=True)
class RetentionInfo:
    """Per-file rewrite-heuristic inputs for one FORK_MODIFIED path."""

    path: str
    lines_at_base: int
    lines_changed: int
    retention: float
    fork_only_commits: int


@dataclass(frozen=True)
class DraftedProfile:
    upstream_ref: str
    fork_ref: str
    merge_base: str
    fork_only_features: tuple[DraftedForkOnlyFeature, ...]
    removed_domains: tuple[DraftedRemovedDomain, ...]
    rewritten_modules: tuple[DraftedRewrittenModule, ...]
    migration_policy: DraftedMigrationPolicy | None
    stats: dict[str, int]


def _auto_min_files(total: int) -> int:
    """Adaptive cluster threshold from §3.5: ``max(3, total / 20)``."""
    return max(3, total // 20)


def cluster_paths(
    paths: Sequence[str], min_files: int | None = None
) -> tuple[ClusterEntry, ...]:
    """Fold a path list into deepest-prefix clusters.

    Algorithm (from §3.5):
      1. For every ancestor directory of every input path, count the
         paths beneath it.
      2. Visit candidates deepest-first, picking any whose remaining
         file count is ``>= min_files``; emit ``<prefix>/**``.
      3. Files not consumed by any cluster pass through as orphans
         with ``glob == path``.

    With ``min_files=None`` the threshold is ``max(3, len/20)``.
    """
    if not paths:
        return ()
    if min_files is None:
        min_files = _auto_min_files(len(paths))

    prefix_to_paths: dict[str, set[str]] = defaultdict(set)
    for p in paths:
        parts = p.split("/")
        for i in range(1, len(parts)):
            prefix_to_paths["/".join(parts[:i])].add(p)

    candidates = sorted(
        prefix_to_paths.items(),
        key=lambda kv: (-kv[0].count("/"), -len(kv[1]), kv[0]),
    )

    remaining = set(paths)
    clusters: list[ClusterEntry] = []
    for prefix, members in candidates:
        eligible = members & remaining
        if len(eligible) >= min_files:
            clusters.append(
                ClusterEntry(
                    glob=f"{prefix}/**",
                    count=len(eligible),
                    paths=tuple(sorted(eligible)),
                )
            )
            remaining -= eligible

    for p in sorted(remaining):
        clusters.append(ClusterEntry(glob=p, count=1, paths=(p,)))

    clusters.sort(key=lambda c: c.glob)
    return tuple(clusters)


def _glob_to_domain_name(glob: str) -> str:
    """Pick a readable identifier from a cluster glob.

    ``backend/services/payments/**`` → ``payments``;
    ``a.py`` → ``a``. Falls back to ``domain`` if nothing extractable.
    """
    base = glob
    if base.endswith("/**"):
        base = base[:-3]
    base = base.rstrip("/")
    if not base:
        return "domain"
    last = base.rsplit("/", 1)[-1]
    if not last:
        return "domain"
    stem = Path(last).stem
    return stem or "domain"


def draft_fork_only_features(
    divergence_map: dict[str, ForkDivergence],
    *,
    cluster_min_files: int | None = None,
) -> tuple[DraftedForkOnlyFeature, ...]:
    """FORK_ONLY paths, clustered. ``note`` left as TODO."""
    paths = sorted(
        p for p, d in divergence_map.items() if d == ForkDivergence.FORK_ONLY
    )
    return tuple(
        DraftedForkOnlyFeature(path=c.glob, note="")
        for c in cluster_paths(paths, cluster_min_files)
    )


def draft_removed_domains(
    divergence_map: dict[str, ForkDivergence],
    *,
    delete_commit_lookup: Callable[[str], tuple[str, str] | None] | None = None,
    cluster_min_files: int | None = None,
) -> tuple[DraftedRemovedDomain, ...]:
    """FORK_DELETED paths, clustered with ``removed_in`` evidence.

    ``delete_commit_lookup(path)`` returns ``(sha, subject)`` for the
    earliest commit that removed ``path`` in the fork-only range, or
    ``None`` if not findable. ``None`` callable disables the lookup.
    """
    paths = sorted(
        p for p, d in divergence_map.items() if d == ForkDivergence.FORK_DELETED
    )
    out: list[DraftedRemovedDomain] = []
    for cluster in cluster_paths(paths, cluster_min_files):
        evidence: tuple[str, str] | None = None
        if delete_commit_lookup is not None:
            for member in cluster.paths:
                evidence = delete_commit_lookup(member)
                if evidence is not None:
                    break
        sha, subject = evidence if evidence else ("", "")
        reason = "TODO: why was this dropped?"
        if subject:
            reason += f" (auto-detected from commit {sha[:7]}: '{subject}')"
        out.append(
            DraftedRemovedDomain(
                name=_glob_to_domain_name(cluster.glob),
                paths=(cluster.glob,),
                reason=reason,
                removed_in=sha,
            )
        )
    return tuple(out)


def draft_rewritten_modules(
    retention: Sequence[RetentionInfo],
    *,
    retention_threshold: float = 0.30,
    min_lines: int = 50,
    min_fork_commits: int = 5,
    cluster_min_files: int | None = None,
) -> tuple[DraftedRewrittenModule, ...]:
    """Filter FORK_MODIFIED files by the §3.3 heuristic, then cluster.

    A file qualifies when EITHER:
      - retention < threshold AND lines_changed >= min_lines, OR
      - fork_only_commits >= min_fork_commits

    Conservative on purpose — false positives are cheaper than false
    negatives because the policy defaults to ``escalate_human``.
    """
    candidates: dict[str, RetentionInfo] = {}
    for r in retention:
        below_threshold = (
            r.lines_at_base > 0
            and r.retention < retention_threshold
            and r.lines_changed >= min_lines
        )
        many_commits = r.fork_only_commits >= min_fork_commits
        if below_threshold or many_commits:
            candidates[r.path] = r

    if not candidates:
        return ()

    out: list[DraftedRewrittenModule] = []
    for cluster in cluster_paths(sorted(candidates), cluster_min_files):
        members = [candidates[p] for p in cluster.paths if p in candidates]
        avg_retention = (
            sum(m.retention for m in members) / len(members) if members else 0.0
        )
        note = (
            f"fork retains {avg_retention * 100:.0f}% of merge-base lines "
            f"(rewrite threshold {int(retention_threshold * 100)}%, "
            f"{cluster.count} file(s))"
        )
        out.append(
            DraftedRewrittenModule(
                path=cluster.glob,
                policy=RewriteMergePolicy.ESCALATE_HUMAN,
                note=note,
            )
        )
    return tuple(out)


def draft_migration_policy(
    *,
    base_files: Sequence[str],
    fork_files: Sequence[str],
    fork_only_files: Sequence[str],
    path_globs: Sequence[str],
) -> DraftedMigrationPolicy | None:
    """Return a policy only when fork_only migrations occupy numbers above
    upstream's max.

    ``base_files`` / ``fork_files`` are filtered against ``path_globs``
    using the same matcher the runtime uses (``matches_any_pattern``),
    so glob behaviour is consistent across draft and routing time.
    """
    if not path_globs:
        return None
    globs = [g for g in path_globs if g.strip()]
    if not globs:
        return None

    def matches(p: str) -> bool:
        return matches_any_pattern(p, list(globs))

    base_numbers = [
        n
        for n in (extract_migration_number(p) for p in base_files if matches(p))
        if n is not None
    ]
    fork_only_numbers = [
        n
        for n in (extract_migration_number(p) for p in fork_only_files if matches(p))
        if n is not None
    ]
    if not fork_only_numbers:
        return None
    upstream_max = max(base_numbers) if base_numbers else 0
    fork_max = max(fork_only_numbers)
    if fork_max <= upstream_max:
        return None
    return DraftedMigrationPolicy(
        path_globs=tuple(globs),
        fork_owns_numbers_above=upstream_max,
        upstream_take_target_max=upstream_max,
        on_collision="escalate_human",
    )


def _compute_retention_info(
    git_tool: GitTool,
    *,
    merge_base: str,
    fork_ref: str,
    paths: Sequence[str],
) -> list[RetentionInfo]:
    """Run ``git diff --numstat`` once and ``git rev-list --count`` per path."""
    if not paths:
        return []

    repo = git_tool.repo
    numstat: dict[str, tuple[int, int]] = {}
    try:
        numstat_out = repo.git.diff(
            "--numstat", f"{merge_base}..{fork_ref}", "--", *paths
        )
    except Exception:
        numstat_out = ""
    for line in str(numstat_out).splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added_s, deleted_s, path = parts[0], parts[1], parts[2]
        if added_s == "-" or deleted_s == "-":
            continue
        try:
            numstat[path] = (int(added_s), int(deleted_s))
        except ValueError:
            continue

    out: list[RetentionInfo] = []
    for path in paths:
        added, deleted = numstat.get(path, (0, 0))
        base_blob = git_tool.get_file_bytes(merge_base, path)
        if base_blob:
            lines_at_base = base_blob.count(b"\n") + (
                0 if base_blob.endswith(b"\n") else 1
            )
        else:
            lines_at_base = 0
        retained = max(0, lines_at_base - deleted)
        retention = retained / lines_at_base if lines_at_base > 0 else 0.0
        try:
            log_out = repo.git.rev_list(
                "--count", f"{merge_base}..{fork_ref}", "--", path
            )
            commits = int(str(log_out).strip() or "0")
        except Exception:
            commits = 0
        out.append(
            RetentionInfo(
                path=path,
                lines_at_base=lines_at_base,
                lines_changed=added + deleted,
                retention=retention,
                fork_only_commits=commits,
            )
        )
    return out


def _find_first_delete_commit(
    git_tool: GitTool,
    *,
    merge_base: str,
    fork_ref: str,
    path: str,
) -> tuple[str, str] | None:
    """Return ``(sha, subject)`` of the earliest commit deleting ``path``."""
    try:
        out = git_tool.repo.git.log(
            "--diff-filter=D",
            "--reverse",
            "--format=%H%x09%s",
            f"{merge_base}..{fork_ref}",
            "--",
            path,
        )
    except Exception:
        return None
    for line in str(out).splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        return parts[0].strip(), parts[1].strip()
    return None


def draft_profile(
    git_tool: GitTool,
    *,
    upstream_ref: str,
    fork_ref: str,
    merge_base: str,
    rewrite_retention_threshold: float = 0.30,
    rewrite_min_lines: int = 50,
    rewrite_min_fork_commits: int = 5,
    migration_globs: Sequence[str] | None = None,
    cluster_min_files: int | None = None,
) -> DraftedProfile:
    """End-to-end orchestrator used by ``merge forks-profile init``."""
    divergence = compute_fork_divergence_map(
        merge_base=merge_base,
        head_ref=fork_ref,
        upstream_ref=upstream_ref,
        git_tool=git_tool,
    )

    fork_only_features = draft_fork_only_features(
        divergence, cluster_min_files=cluster_min_files
    )
    removed_domains = draft_removed_domains(
        divergence,
        delete_commit_lookup=lambda p: _find_first_delete_commit(
            git_tool, merge_base=merge_base, fork_ref=fork_ref, path=p
        ),
        cluster_min_files=cluster_min_files,
    )

    fork_modified_paths = sorted(
        p for p, d in divergence.items() if d == ForkDivergence.FORK_MODIFIED
    )
    retention = _compute_retention_info(
        git_tool, merge_base=merge_base, fork_ref=fork_ref, paths=fork_modified_paths
    )
    rewritten_modules = draft_rewritten_modules(
        retention,
        retention_threshold=rewrite_retention_threshold,
        min_lines=rewrite_min_lines,
        min_fork_commits=rewrite_min_fork_commits,
        cluster_min_files=cluster_min_files,
    )

    globs = list(migration_globs) if migration_globs else list(DEFAULT_MIGRATION_GLOBS)
    base_files = git_tool.list_files(merge_base) if merge_base else []
    fork_files = git_tool.list_files(fork_ref)
    fork_only_paths = [
        p for p, d in divergence.items() if d == ForkDivergence.FORK_ONLY
    ]
    migration_policy = draft_migration_policy(
        base_files=base_files,
        fork_files=fork_files,
        fork_only_files=fork_only_paths,
        path_globs=globs,
    )

    stats = {
        "D_MISSING": sum(
            1 for d in divergence.values() if d == ForkDivergence.FORK_DELETED
        ),
        "D_EXTRA": sum(1 for d in divergence.values() if d == ForkDivergence.FORK_ONLY),
        "B-rewritten": len(rewritten_modules),
        "migration-collisions": 0,
    }

    return DraftedProfile(
        upstream_ref=upstream_ref,
        fork_ref=fork_ref,
        merge_base=merge_base,
        fork_only_features=fork_only_features,
        removed_domains=removed_domains,
        rewritten_modules=rewritten_modules,
        migration_policy=migration_policy,
        stats=stats,
    )


def _yaml_quote(s: str) -> str:
    """Conservative double-quoted scalar suitable for the draft body."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_profile_yaml(drafted: DraftedProfile, *, today: str) -> str:
    """Emit the reviewer-friendly yaml shown in §5 of the design doc.

    String templating (rather than ruamel/pyyaml) so inline ``# TODO``
    comments survive verbatim and the output is a stable diff target
    when a maintainer re-runs ``init``.
    """
    stats_str = ", ".join(f"{k}={v}" for k, v in drafted.stats.items())
    base_short = drafted.merge_base[:7] if drafted.merge_base else "unknown"

    lines: list[str] = [
        f"# Auto-drafted by `merge forks-profile init` on {today}",
        f"# Inputs: {drafted.upstream_ref}..{drafted.fork_ref} "
        f"(merge-base {base_short})",
        f"# Stats: {stats_str}",
        "#",
        "# Review every entry below before committing.",
        "# - The `removed_domains` are likely close — verify the `reason` text.",
        "# - The `rewritten_modules` policy is set to escalate_human (safest);",
        "#   downgrade to take_current_with_diff_note or semantic_merge_with_alert",
        "#   only after reading the actual diff.",
        "# - Delete entries that were over-classified (e.g. an unrelated test",
        '#   cleanup misread as a "removed domain").',
        "",
        "version: 1",
        "",
        "fork:",
        '  name: ""              # TODO: name your fork',
        '  upstream: ""          # TODO: e.g. owner/repo',
        '  positioning: ""       # TODO: one-line description',
        "",
    ]

    if drafted.removed_domains:
        lines.append("removed_domains:")
        for d in drafted.removed_domains:
            lines.append(f"  - name: {d.name}      # TODO: confirm domain name")
            lines.append("    paths:")
            for p in d.paths:
                lines.append(f"      - {_yaml_quote(p)}")
            lines.append(f"    reason: {_yaml_quote(d.reason)}")
            if d.removed_in:
                lines.append(f"    removed_in: {_yaml_quote(d.removed_in)}")
        lines.append("")
    else:
        lines.append("removed_domains: []")
        lines.append("")

    if drafted.rewritten_modules:
        lines.append("rewritten_modules:")
        for m in drafted.rewritten_modules:
            lines.append(f"  - path: {_yaml_quote(m.path)}")
            lines.append(
                f"    policy: {m.policy.value}   "
                "# TODO: consider take_current_with_diff_note / "
                "semantic_merge_with_alert"
            )
            lines.append(f"    note: {_yaml_quote(m.note)}")
        lines.append("")
    else:
        lines.append("rewritten_modules: []")
        lines.append("")

    if drafted.fork_only_features:
        lines.append("fork_only_features:")
        for f in drafted.fork_only_features:
            lines.append(f"  - path: {_yaml_quote(f.path)}")
            lines.append('    note: ""             # TODO: describe')
        lines.append("")
    else:
        lines.append("fork_only_features: []")
        lines.append("")

    if drafted.migration_policy is not None:
        mp = drafted.migration_policy
        lines.append("migration_policy:")
        lines.append("  path_globs:")
        for g in mp.path_globs:
            lines.append(f"    - {_yaml_quote(g)}")
        lines.append(f"  fork_owns_numbers_above: {mp.fork_owns_numbers_above}")
        lines.append(f"  upstream_take_target_max: {mp.upstream_take_target_max}")
        lines.append("  on_collision:")
        lines.append(f"    action: {mp.on_collision}")
    else:
        lines.append("# migration_policy:      # uncomment if relevant")
        lines.append('#   path_globs: ["backend/db/migrations/*.sql"]')
        lines.append("#   fork_owns_numbers_above: 0")
        lines.append("#   upstream_take_target_max: 0")
        lines.append("#   on_collision:")
        lines.append("#     action: escalate_human")

    return "\n".join(lines).rstrip() + "\n"


__all__ = [
    "ClusterEntry",
    "DraftedForkOnlyFeature",
    "DraftedMigrationPolicy",
    "DraftedProfile",
    "DraftedRemovedDomain",
    "DraftedRewrittenModule",
    "DEFAULT_MIGRATION_GLOBS",
    "RetentionInfo",
    "cluster_paths",
    "draft_fork_only_features",
    "draft_migration_policy",
    "draft_profile",
    "draft_removed_domains",
    "draft_rewritten_modules",
    "render_profile_yaml",
]
