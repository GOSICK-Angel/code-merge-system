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

import re
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


# Retention bands for rewritten_modules policy selection.
#
#  - retention < REWRITE_RETENTION_HARD            → escalate_human
#  - REWRITE_RETENTION_HARD..REWRITE_RETENTION_LIGHT → semantic_merge_with_alert
#  - >= REWRITE_RETENTION_LIGHT (commit-churn only) → take_current_with_diff_note
REWRITE_RETENTION_HARD: float = 0.30
REWRITE_RETENTION_LIGHT: float = 0.80


# Default minimum prefix depth for auto-clustering. Top-level directories
# (``tools/``, ``models/``, ``tests/``) are almost never wholesale dropped
# or rewritten — clustering at depth=1 produces over-broad globs that
# mass-rewrite the merge plan. Callers that *do* want a single ``foo/**``
# rollup pass ``min_depth=1`` explicitly.
DEFAULT_CLUSTER_MIN_DEPTH: int = 2


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
    # Best-effort ``owner/repo`` derived from the fork's git remotes
    # (``upstream`` preferred, then ``origin``). Empty string when no
    # remote is configured or the URL doesn't match the standard pattern;
    # the renderer falls back to a TODO marker in that case.
    fork_upstream: str = ""
    # Cluster globs that **looked** fork-deleted by tree-diff but had
    # no findable deletion commit in the fork-only range — almost always
    # the result of base drift (a prior upstream merge advanced
    # merge-base past additions that fork HEAD never had). Surfaced as
    # a yaml header comment so the maintainer can audit without being
    # forced to author entries for them.
    removed_filtered: tuple[str, ...] = ()


def _auto_min_files(total: int) -> int:
    """Adaptive cluster threshold from §3.5: ``max(3, total / 20)``."""
    return max(3, total // 20)


def cluster_paths(
    paths: Sequence[str],
    min_files: int | None = None,
    *,
    min_depth: int = DEFAULT_CLUSTER_MIN_DEPTH,
) -> tuple[ClusterEntry, ...]:
    """Fold a path list into deepest-prefix clusters.

    Algorithm (from §3.5):
      1. For every ancestor directory of every input path, count the
         paths beneath it.
      2. Visit candidates deepest-first, picking any whose remaining
         file count is ``>= min_files`` and prefix depth is
         ``>= min_depth``; emit ``<prefix>/**``.
      3. Files not consumed by any cluster pass through as orphans
         with ``glob == path``.

    With ``min_files=None`` the threshold is ``max(3, len/20)``.

    ``min_depth=2`` (the default) suppresses single-segment rollups like
    ``tools/**`` / ``models/**`` that almost always over-classify a fork's
    intent. Pass ``min_depth=1`` to keep the legacy "any depth" behaviour
    (used by tests and by callers that want a flat top-level rollup).
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
        # Depth = number of path segments. ``prefix.count("/") + 1`` is
        # the segment count; require >= ``min_depth``.
        if prefix.count("/") + 1 < min_depth:
            continue
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


def _glob_segments(glob: str) -> list[str]:
    """Return the directory segments of a cluster glob (``**`` stripped)."""
    base = glob
    if base.endswith("/**"):
        base = base[:-3]
    base = base.rstrip("/")
    if not base:
        return []
    return [s for s in base.split("/") if s]


def _uniquify_domain_names(
    items: tuple[DraftedRemovedDomain, ...],
) -> tuple[DraftedRemovedDomain, ...]:
    """Disambiguate duplicate ``name`` values across cluster outputs.

    Two ``tools/**``-derived clusters both name themselves ``tools``;
    walk up the path to ``tools-comfyui``, ``tools-comfyui-tools`` and
    so on until the name is unique. Final fallback is a numeric suffix
    so the function is total even on pathological inputs.
    """
    if not items:
        return items
    used: set[str] = set()
    out: list[DraftedRemovedDomain] = []
    for entry in items:
        first_path = entry.paths[0] if entry.paths else ""
        segments = _glob_segments(first_path) or [entry.name]
        candidate = segments[-1] or "domain"
        i = 2
        while candidate in used and i <= len(segments):
            candidate = "-".join(segments[-i:])
            i += 1
        suffix = 2
        base = candidate
        while candidate in used:
            candidate = f"{base}-{suffix}"
            suffix += 1
        used.add(candidate)
        out.append(
            DraftedRemovedDomain(
                name=candidate,
                paths=entry.paths,
                reason=entry.reason,
                removed_in=entry.removed_in,
            )
        )
    return tuple(out)


def _glob_prefix(glob: str) -> str:
    """Return the directory body of ``foo/bar/**`` (= ``foo/bar``).

    For an orphan path (``a/b.py``) returns the path unchanged so the
    overlap check still works on file-level entries.
    """
    if glob.endswith("/**"):
        return glob[:-3]
    return glob


def _globs_overlap(a: str, b: str) -> bool:
    """True when two cluster globs target overlapping path subtrees.

    ``models/**`` overlaps ``models/azure_openai/**``; ``a/b.py``
    overlaps ``a/**``. Used to suppress the ``removed_domains`` entry
    when a stricter ``rewritten_modules`` rule already covers the area.
    """
    pa = _glob_prefix(a)
    pb = _glob_prefix(b)
    if not pa or not pb:
        return False
    if pa == pb:
        return True
    return pa.startswith(pb + "/") or pb.startswith(pa + "/")


_REMOTE_OWNER_REPO_RE = re.compile(r"[/:]([^/:\s]+)/([^/\s]+?)(?:\.git)?/?$")


def extract_owner_repo(url: str) -> str | None:
    """Return ``owner/repo`` from a git remote URL, or ``None`` on no match.

    Accepts both forms commonly produced by ``git remote -v``::

        git@github.com:acme/forked-app.git           → acme/forked-app
        https://github.com/acme/forked-app.git       → acme/forked-app
        https://gitlab.example.com/group/proj        → group/proj
    """
    if not url:
        return None
    m = _REMOTE_OWNER_REPO_RE.search(url.strip())
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    if not owner or not repo:
        return None
    return f"{owner}/{repo}"


def _detect_upstream_url(git_tool: GitTool) -> str | None:
    """Best-effort lookup of the fork's upstream-or-origin remote URL.

    Tries ``upstream`` first (the conventional name for "where this fork
    pulls from") then ``origin``. Any remote-resolution failure returns
    ``None`` so the drafter never raises on detached worktrees or repos
    without remotes.
    """
    try:
        repo = git_tool.repo
    except Exception:
        return None
    for name in ("upstream", "origin"):
        try:
            remote = repo.remotes[name]
        except Exception:
            continue
        try:
            urls = list(remote.urls)
        except Exception:
            urls = []
        for url in urls:
            if url:
                return str(url)
    return None


def draft_fork_only_features(
    divergence_map: dict[str, ForkDivergence],
    *,
    cluster_min_files: int | None = None,
    cluster_min_depth: int = DEFAULT_CLUSTER_MIN_DEPTH,
) -> tuple[DraftedForkOnlyFeature, ...]:
    """FORK_ONLY paths, clustered. ``note`` left as TODO."""
    paths = sorted(
        p for p, d in divergence_map.items() if d == ForkDivergence.FORK_ONLY
    )
    return tuple(
        DraftedForkOnlyFeature(path=c.glob, note="")
        for c in cluster_paths(paths, cluster_min_files, min_depth=cluster_min_depth)
    )


def draft_removed_domains(
    divergence_map: dict[str, ForkDivergence],
    *,
    delete_commit_lookup: Callable[[str], tuple[str, str] | None] | None = None,
    cluster_min_files: int | None = None,
    cluster_min_depth: int = DEFAULT_CLUSTER_MIN_DEPTH,
    require_commit_evidence: bool = True,
) -> tuple[tuple[DraftedRemovedDomain, ...], tuple[str, ...]]:
    """FORK_DELETED paths, clustered with ``removed_in`` evidence.

    Returns ``(kept_domains, filtered_globs)`` so the orchestrator can
    surface the filtered list in the rendered yaml header without
    silently swallowing it.

    ``delete_commit_lookup(path)`` returns ``(sha, subject)`` for the
    earliest commit that removed ``path`` in the fork-only range, or
    ``None`` if not findable. ``None`` callable disables the lookup.

    Evidence-based filtering (the *real* fix for tree-diff-induced yaml
    bloat): when ``require_commit_evidence`` is True (default) and
    a lookup callable is provided, clusters where **no** member has a
    findable delete-commit are dropped from ``kept_domains`` and reported
    via ``filtered_globs``. The motivating case is base drift — a fork
    that historically merged a newer upstream picks up a merge-base ahead
    of where the maintainer reasons about ``base``; files added by
    upstream after that drift point but absent from fork HEAD look like
    "fork deleted them" purely by tree-diff, even though there is no
    deletion commit anywhere in the fork's own history. Without evidence
    these are almost always false positives and shouldn't bloat the
    yaml.

    When ``require_commit_evidence`` is False **or** no lookup is given,
    every cluster is kept (legacy behaviour) and ``filtered_globs`` is
    empty.
    """
    paths = sorted(
        p for p, d in divergence_map.items() if d == ForkDivergence.FORK_DELETED
    )
    kept: list[DraftedRemovedDomain] = []
    filtered: list[str] = []
    for cluster in cluster_paths(paths, cluster_min_files, min_depth=cluster_min_depth):
        evidence: tuple[str, str] | None = None
        if delete_commit_lookup is not None:
            for member in cluster.paths:
                evidence = delete_commit_lookup(member)
                if evidence is not None:
                    break
        if (
            require_commit_evidence
            and delete_commit_lookup is not None
            and evidence is None
        ):
            filtered.append(cluster.glob)
            continue
        sha, subject = evidence if evidence else ("", "")
        reason = f"auto-detected from commit {sha[:7]}: '{subject}'" if subject else ""
        kept.append(
            DraftedRemovedDomain(
                name=_glob_to_domain_name(cluster.glob),
                paths=(cluster.glob,),
                reason=reason,
                removed_in=sha,
            )
        )
    return _uniquify_domain_names(tuple(kept)), tuple(filtered)


def _policy_for_retention(avg_retention: float) -> RewriteMergePolicy:
    """Map an aggregate retention ratio to a rewrite-merge policy.

    True rewrites (low retention) escalate to human; mixed-modification
    clusters get a semantic-merge alert so the analyst LLM stays in the
    loop without forcing manual intervention; near-pristine clusters
    that only qualify by commit-churn drop to ``take_current_with_diff_note``
    so the fork side is preserved with the upstream delta stashed for
    later integration.
    """
    if avg_retention < REWRITE_RETENTION_HARD:
        return RewriteMergePolicy.ESCALATE_HUMAN
    if avg_retention < REWRITE_RETENTION_LIGHT:
        return RewriteMergePolicy.SEMANTIC_MERGE_WITH_ALERT
    return RewriteMergePolicy.TAKE_CURRENT_WITH_DIFF_NOTE


def draft_rewritten_modules(
    retention: Sequence[RetentionInfo],
    *,
    retention_threshold: float = REWRITE_RETENTION_HARD,
    min_lines: int = 50,
    min_fork_commits: int = 5,
    cluster_min_files: int | None = None,
    cluster_min_depth: int = DEFAULT_CLUSTER_MIN_DEPTH,
) -> tuple[DraftedRewrittenModule, ...]:
    """Filter FORK_MODIFIED files by the §3.3 heuristic, then cluster.

    A file qualifies when EITHER:
      - retention < threshold AND lines_changed >= min_lines, OR
      - fork_only_commits >= min_fork_commits AND lines_changed > 0

    The ``lines_changed > 0`` clause on the commit-churn branch keeps
    files that fork commits *touched* but never actually modified
    (retention == 100%, e.g. ``.gitignore`` re-saved without diff) out
    of ``rewritten_modules`` — there is nothing to "rewrite" if no line
    moved, even when many commits brushed past the path.

    Policy is selected per-cluster based on the **average** retention of
    its members (see :func:`_policy_for_retention`):

      - retention <  ``REWRITE_RETENTION_HARD``  → ``escalate_human``
      - retention <  ``REWRITE_RETENTION_LIGHT`` → ``semantic_merge_with_alert``
      - retention >= ``REWRITE_RETENTION_LIGHT`` → ``take_current_with_diff_note``

    Conservative on purpose — false positives are cheaper than false
    negatives because the lightest policy still stashes the upstream
    diff for human review.
    """
    candidates: dict[str, RetentionInfo] = {}
    for r in retention:
        below_threshold = (
            r.lines_at_base > 0
            and r.retention < retention_threshold
            and r.lines_changed >= min_lines
        )
        many_commits = r.fork_only_commits >= min_fork_commits and r.lines_changed > 0
        if below_threshold or many_commits:
            candidates[r.path] = r

    if not candidates:
        return ()

    out: list[DraftedRewrittenModule] = []
    for cluster in cluster_paths(
        sorted(candidates), cluster_min_files, min_depth=cluster_min_depth
    ):
        members = [candidates[p] for p in cluster.paths if p in candidates]
        avg_retention = (
            sum(m.retention for m in members) / len(members) if members else 0.0
        )
        policy = _policy_for_retention(avg_retention)
        note = (
            f"fork retains {avg_retention * 100:.0f}% of merge-base lines "
            f"(rewrite threshold {int(retention_threshold * 100)}%, "
            f"{cluster.count} file(s))"
        )
        out.append(DraftedRewrittenModule(path=cluster.glob, policy=policy, note=note))
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


def _suppress_removed_overlapping_rewritten(
    removed: tuple[DraftedRemovedDomain, ...],
    rewritten: tuple[DraftedRewrittenModule, ...],
) -> tuple[DraftedRemovedDomain, ...]:
    """Drop ``removed_domains`` entries whose globs overlap a rewritten one.

    A path can be either *removed* or *rewritten*, never both. The drafter
    derives them from independent inputs (FORK_DELETED vs FORK_MODIFIED),
    but the cluster algorithm can produce overlapping globs (``models/**``
    in both buckets) when the fork has both deleted and modified files
    under the same subtree. Rewritten wins because it expresses "still
    here, just different" — the stricter policy.
    """
    if not removed or not rewritten:
        return removed
    rewritten_globs = [m.path for m in rewritten]
    return tuple(
        entry
        for entry in removed
        if not any(_globs_overlap(p, rg) for p in entry.paths for rg in rewritten_globs)
    )


def draft_profile(
    git_tool: GitTool,
    *,
    upstream_ref: str,
    fork_ref: str,
    merge_base: str,
    rewrite_retention_threshold: float = REWRITE_RETENTION_HARD,
    rewrite_min_lines: int = 50,
    rewrite_min_fork_commits: int = 5,
    migration_globs: Sequence[str] | None = None,
    cluster_min_files: int | None = None,
    cluster_min_depth: int = DEFAULT_CLUSTER_MIN_DEPTH,
) -> DraftedProfile:
    """End-to-end orchestrator used by ``merge forks-profile init``."""
    divergence = compute_fork_divergence_map(
        merge_base=merge_base,
        head_ref=fork_ref,
        upstream_ref=upstream_ref,
        git_tool=git_tool,
    )

    fork_only_features = draft_fork_only_features(
        divergence,
        cluster_min_files=cluster_min_files,
        cluster_min_depth=cluster_min_depth,
    )
    removed_domains, removed_filtered = draft_removed_domains(
        divergence,
        delete_commit_lookup=lambda p: _find_first_delete_commit(
            git_tool, merge_base=merge_base, fork_ref=fork_ref, path=p
        ),
        cluster_min_files=cluster_min_files,
        cluster_min_depth=cluster_min_depth,
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
        cluster_min_depth=cluster_min_depth,
    )

    removed_domains = _suppress_removed_overlapping_rewritten(
        removed_domains, rewritten_modules
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
        "removed-filtered": len(removed_filtered),
    }

    upstream_url = _detect_upstream_url(git_tool)
    fork_upstream = extract_owner_repo(upstream_url) if upstream_url else None

    return DraftedProfile(
        upstream_ref=upstream_ref,
        fork_ref=fork_ref,
        merge_base=merge_base,
        fork_only_features=fork_only_features,
        removed_domains=removed_domains,
        rewritten_modules=rewritten_modules,
        migration_policy=migration_policy,
        stats=stats,
        fork_upstream=fork_upstream or "",
        removed_filtered=removed_filtered,
    )


def _yaml_quote(s: str) -> str:
    """Conservative double-quoted scalar suitable for the draft body."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


_POLICY_HINT: dict[RewriteMergePolicy, str] = {
    RewriteMergePolicy.ESCALATE_HUMAN: (
        "# heuristic: low retention → true rewrite, requires human review"
    ),
    RewriteMergePolicy.SEMANTIC_MERGE_WITH_ALERT: (
        "# heuristic: partial rewrite — analyst LLM stays in the loop"
    ),
    RewriteMergePolicy.TAKE_CURRENT_WITH_DIFF_NOTE: (
        "# heuristic: high retention + commit churn — fork side wins, "
        "upstream diff stashed"
    ),
}


def render_profile_yaml(drafted: DraftedProfile, *, today: str) -> str:
    """Emit the reviewer-friendly yaml shown in §5 of the design doc.

    String templating (rather than ruamel/pyyaml) so inline ``# TODO``
    comments survive verbatim and the output is a stable diff target
    when a maintainer re-runs ``init``.
    """
    stats_str = ", ".join(f"{k}={v}" for k, v in drafted.stats.items())
    base_short = drafted.merge_base[:7] if drafted.merge_base else "unknown"

    if drafted.fork_upstream:
        upstream_line = (
            f"  upstream: {_yaml_quote(drafted.fork_upstream)}  "
            "# auto-detected from git remote"
        )
    else:
        upstream_line = '  upstream: ""          # TODO: e.g. owner/repo'

    lines: list[str] = [
        f"# Auto-drafted by `merge forks-profile init` on {today}",
        f"# Inputs: {drafted.upstream_ref}..{drafted.fork_ref} "
        f"(merge-base {base_short})",
        f"# Stats: {stats_str}",
        "#",
        "# Review every entry below before committing.",
        "# - `removed_domains` are likely close — verify each entry and",
        "#   fill in `reason` (left blank when no delete commit was found).",
        "# - `rewritten_modules` policies are auto-tiered by retention:",
        "#     escalate_human            (true rewrite, retention < 30%)",
        "#     semantic_merge_with_alert (mixed,         30–80%)",
        "#     take_current_with_diff_note (light edit,  >= 80%)",
        "#   adjust each policy after reading the actual diff.",
        "# - Delete entries that were over-classified (e.g. an unrelated test",
        '#   cleanup misread as a "removed domain").',
        "#",
        "# `fork_only_features` and `migration_policy` are NOT user-authored:",
        "# they are auto-computed from git divergence on every run and the",
        "# loader rejects them here as deprecated yaml fields.",
        "",
        "version: 1",
        "",
        "fork:",
        '  name: ""              # TODO: name your fork',
        upstream_line,
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
            if d.reason:
                lines.append(f"    reason: {_yaml_quote(d.reason)}")
            else:
                lines.append('    reason: ""           # TODO: why was this dropped?')
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
            hint = _POLICY_HINT.get(m.policy, "")
            policy_line = f"    policy: {m.policy.value}"
            if hint:
                policy_line = f"{policy_line}   {hint}"
            lines.append(policy_line)
            lines.append(f"    note: {_yaml_quote(m.note)}")
        lines.append("")
    else:
        lines.append("rewritten_modules: []")
        lines.append("")

    if drafted.removed_filtered:
        # Surface base-drift suspects so the maintainer can audit without
        # having to write out yaml entries for them. Truncate to the
        # first ~10 to keep the header skimmable; the full list lives on
        # ``DraftedProfile.removed_filtered`` if anyone needs it.
        head = list(drafted.removed_filtered[:10])
        more = len(drafted.removed_filtered) - len(head)
        suffix = f" (+{more} more)" if more > 0 else ""
        lines.append(
            f"# Skipped {len(drafted.removed_filtered)} fork-deleted "
            "cluster(s) with no findable delete-commit (likely base drift, "
            "not a fork policy decision):"
        )
        for glob in head:
            lines.append(f"#   - {glob}")
        if more > 0:
            lines.append(f"#   ...{suffix}")
        lines.append(
            "# If any of these *are* deliberate fork removals, add them "
            "to `removed_domains` manually."
        )
    if drafted.fork_only_features:
        feature_paths = ", ".join(f.path for f in drafted.fork_only_features)
        lines.append(
            f"# fork_only_features (auto-computed at runtime): {feature_paths}"
        )
    if drafted.migration_policy is not None:
        mp = drafted.migration_policy
        globs = ", ".join(mp.path_globs)
        lines.append(
            f"# migration_policy (auto-computed at runtime): "
            f"globs=[{globs}], "
            f"fork_owns_numbers_above={mp.fork_owns_numbers_above}, "
            f"upstream_take_target_max={mp.upstream_take_target_max}, "
            f"on_collision={mp.on_collision}"
        )

    return "\n".join(lines).rstrip() + "\n"


__all__ = [
    "ClusterEntry",
    "DraftedForkOnlyFeature",
    "DraftedMigrationPolicy",
    "DraftedProfile",
    "DraftedRemovedDomain",
    "DraftedRewrittenModule",
    "DEFAULT_CLUSTER_MIN_DEPTH",
    "DEFAULT_MIGRATION_GLOBS",
    "REWRITE_RETENTION_HARD",
    "REWRITE_RETENTION_LIGHT",
    "RetentionInfo",
    "cluster_paths",
    "draft_fork_only_features",
    "draft_migration_policy",
    "draft_profile",
    "draft_removed_domains",
    "draft_rewritten_modules",
    "extract_owner_repo",
    "render_profile_yaml",
]
