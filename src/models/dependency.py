"""File dependency graph models for merge ordering and impact analysis."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class DependencyKind(StrEnum):
    IMPORTS = "imports"
    INHERITS = "inherits"
    CALLS = "calls"
    USES_TYPE = "uses_type"


class ConfidenceLabel(StrEnum):
    EXTRACTED = "extracted"
    INFERRED = "inferred"
    AMBIGUOUS = "ambiguous"


class DependencyEdge(BaseModel, frozen=True):
    source_file: str
    target_file: str
    kind: DependencyKind
    source_symbol: str = ""
    target_symbol: str = ""
    confidence: ConfidenceLabel = ConfidenceLabel.EXTRACTED


class DependencyImpactHint(BaseModel, frozen=True):
    """Per-file blast-radius summary consumed by conflict_analyst / executor.

    Built by ``FileDependencyGraph.impact_hint``; carries the direct dependent
    count, the transitive impact-radius size, and a God Node flag (direct
    dependents above a configurable threshold). Used only to *raise* caution in
    LLM prompts (risk monotonicity, plan §5) — never to lower risk."""

    direct_dependents: int = 0
    impact_radius: int = 0
    is_god_node: bool = False

    @property
    def has_signal(self) -> bool:
        return self.direct_dependents > 0 or self.impact_radius > 0


class FileDependencyGraph(BaseModel, frozen=True):
    edges: tuple[DependencyEdge, ...] = ()
    file_count: int = 0

    def dependents_of(self, file_path: str) -> list[str]:
        return list({e.source_file for e in self.edges if e.target_file == file_path})

    def dependencies_of(self, file_path: str) -> list[str]:
        return list({e.target_file for e in self.edges if e.source_file == file_path})

    def referenced_symbols(self, file_path: str) -> frozenset[str]:
        """Symbols defined in ``file_path`` that other files import/use.

        Collected from the ``target_symbol`` of every inbound edge
        (``target_file == file_path``). Empty symbols (module-level imports,
        side-effect imports, languages that don't expose named imports) are
        skipped. Relevance scoring boosts chunks whose name appears here, so a
        public symbol stays FULL under staged compression even when it sits
        outside the diff."""
        return frozenset(
            e.target_symbol
            for e in self.edges
            if e.target_file == file_path and e.target_symbol
        )

    def symbol_fanin(self, file_path: str) -> dict[str, int]:
        """Per-symbol importer count for symbols defined in ``file_path``.

        For each symbol ``file_path`` defines that other files import (inbound
        edges with ``target_file == file_path`` and a non-empty
        ``target_symbol``), counts the distinct importing source files. Feeds
        degree-weighted relevance scoring (OPP-10) so a high-fan-in public
        symbol stays FULL under staged compression while a leaf helper does
        not. Empty graph -> empty dict (safe degrade)."""
        importers: dict[str, set[str]] = {}
        for e in self.edges:
            if e.target_file == file_path and e.target_symbol:
                importers.setdefault(e.target_symbol, set()).add(e.source_file)
        return {symbol: len(srcs) for symbol, srcs in importers.items()}

    def topological_order(self, files: list[str]) -> list[str]:
        file_set = set(files)
        in_degree: dict[str, int] = {f: 0 for f in files}
        adj: dict[str, list[str]] = {f: [] for f in files}

        for edge in self.edges:
            if edge.source_file in file_set and edge.target_file in file_set:
                adj[edge.target_file].append(edge.source_file)
                in_degree[edge.source_file] = in_degree.get(edge.source_file, 0) + 1

        queue = sorted(f for f in files if in_degree[f] == 0)
        result: list[str] = []
        while queue:
            node = queue.pop(0)
            result.append(node)
            for neighbor in adj.get(node, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
            queue.sort()

        remaining = sorted(f for f in files if f not in set(result))
        return result + remaining

    def impact_radius(self, file_path: str, max_depth: int = 3) -> set[str]:
        visited: set[str] = set()
        frontier = {file_path}
        for _ in range(max_depth):
            next_frontier: set[str] = set()
            for f in frontier:
                for dep in self.dependents_of(f):
                    if dep not in visited and dep != file_path:
                        next_frontier.add(dep)
            visited.update(next_frontier)
            frontier = next_frontier
            if not frontier:
                break
        return visited

    def impact_hint(
        self,
        file_path: str,
        *,
        max_depth: int = 3,
        god_node_min_dependents: int = 8,
    ) -> DependencyImpactHint:
        """Summarise ``file_path``'s blast radius for prompt-level caution.

        ``direct_dependents`` counts files importing it directly;
        ``impact_radius`` is the transitive dependent set size bounded by
        ``max_depth``; ``is_god_node`` is True when direct dependents reach
        ``god_node_min_dependents``. An empty graph yields a zeroed hint whose
        ``has_signal`` is False, so consumers inject nothing (safe degrade)."""
        direct = len(self.dependents_of(file_path))
        radius = len(self.impact_radius(file_path, max_depth=max_depth))
        return DependencyImpactHint(
            direct_dependents=direct,
            impact_radius=radius,
            is_god_node=direct >= god_node_min_dependents,
        )
