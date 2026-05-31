"""Group file paths into functional modules.

Target-repo agnostic: a file's module is decided by, in order of
precedence, an explicit glob override, the fork's declared
rewritten-module paths, or directory topology (a monorepo container's
immediate child, else the top-level directory). No fork or module name
is ever hardcoded — everything flows from :class:`ModuleConfig` and the
forks-profile.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable

from src.models.config import ModuleConfig

DEFAULT_MODULE = "default"

# Phase C §6.4: label-propagation iteration ceiling. Communities stabilise
# well within this on the focused merge subgraph; the cap only guards against
# pathological oscillation.
_LABEL_PROP_MAX_ITERS = 20


def _glob_match(file_path: str, pattern: str) -> bool:
    if fnmatch.fnmatch(file_path, pattern):
        return True
    # A wildcard-free pattern is treated as a directory prefix so a
    # rewritten-module path like ``api/auth`` captures everything beneath
    # it without the author having to append ``/**``.
    if not any(ch in pattern for ch in "*?["):
        prefix = pattern.rstrip("/")
        return file_path == prefix or file_path.startswith(prefix + "/")
    return False


def _module_name_from_pattern(pattern: str) -> str:
    """Derive a stable module name from a rewritten-module glob/path by
    keeping the literal prefix before the first wildcard."""
    stem = pattern.split("*", 1)[0].rstrip("/")
    return stem or DEFAULT_MODULE


def _topology_module(file_path: str, container_dirs: list[str]) -> str:
    parts = file_path.split("/")
    # ``<container>/<module>/...`` — the child of a known container dir
    # names the module; a file sitting directly in the container falls
    # through to the top-level rule below.
    if len(parts) >= 3 and parts[0] in container_dirs:
        return parts[1]
    if len(parts) >= 2:
        return parts[0]
    return DEFAULT_MODULE


def infer_modules(
    file_paths: Iterable[str],
    config: ModuleConfig,
    rewritten_module_paths: list[str] | None = None,
) -> dict[str, str]:
    """Map each path to a module name.

    ``rewritten_module_paths`` is the list of ``RewrittenModule.path``
    globs from the active forks-profile (caller extracts them so this
    tool stays decoupled from the profile model).
    """
    rewritten = rewritten_module_paths or []
    result: dict[str, str] = {}

    for path in file_paths:
        if config.mode == "off":
            result[path] = DEFAULT_MODULE
            continue

        matched: str | None = None
        for pattern, name in config.explicit.items():
            if _glob_match(path, pattern):
                matched = name
                break
        if matched is None:
            for pattern in rewritten:
                if _glob_match(path, pattern):
                    matched = _module_name_from_pattern(pattern)
                    break
        if matched is None:
            matched = (
                _topology_module(path, config.container_dirs)
                if config.mode in ("auto", "graph")
                else DEFAULT_MODULE
            )
        result[path] = matched

    return result


def _modal_module(group: list[str], fallback_modules: dict[str, str]) -> str:
    """Most common fallback (path-topology) module among a community's
    members; ties broken by the lexicographically smallest name so the
    community name is deterministic and human-meaningful."""
    counts: dict[str, int] = {}
    for fp in group:
        name = fallback_modules.get(fp, DEFAULT_MODULE)
        counts[name] = counts.get(name, 0) + 1
    return min(counts, key=lambda name: (-counts[name], name))


def infer_communities(
    edges: Iterable[tuple[str, str]],
    file_paths: Iterable[str],
    fallback_modules: dict[str, str],
    *,
    max_iters: int = _LABEL_PROP_MAX_ITERS,
) -> dict[str, str]:
    """Graph-driven module grouping via deterministic label propagation.

    ``edges`` are undirected (source, target) pairs of the dependency graph
    (caller filters out AMBIGUOUS edges). Each file starts in its own
    community; each round a node adopts the most frequent label among its
    neighbours (ties → lexicographically smallest label, nodes visited in
    sorted order — fully deterministic). A community is then named by the
    modal path-topology module of its members (``fallback_modules``), keeping
    names readable and compatible with ``module_depends_on`` ordering.
    Singletons / no-edge files keep their fallback module — so an empty edge
    set reproduces the path-topology grouping exactly (safe degrade)."""
    paths = list(file_paths)
    path_set = set(paths)
    adj: dict[str, set[str]] = {p: set() for p in paths}
    for src, tgt in edges:
        if src in path_set and tgt in path_set and src != tgt:
            adj[src].add(tgt)
            adj[tgt].add(src)

    label: dict[str, str] = {p: p for p in paths}
    for _ in range(max_iters):
        changed = False
        for node in sorted(paths):
            neighbors = adj[node]
            if not neighbors:
                continue
            counts: dict[str, int] = {}
            for nb in neighbors:
                counts[label[nb]] = counts.get(label[nb], 0) + 1
            best = min(counts, key=lambda lbl: (-counts[lbl], lbl))
            if label[node] != best:
                label[node] = best
                changed = True
        if not changed:
            break

    members: dict[str, list[str]] = {}
    for p in paths:
        members.setdefault(label[p], []).append(p)

    result: dict[str, str] = {}
    for group in members.values():
        if len(group) == 1:
            result[group[0]] = fallback_modules.get(group[0], DEFAULT_MODULE)
            continue
        name = _modal_module(group, fallback_modules)
        for p in group:
            result[p] = name
    return result
