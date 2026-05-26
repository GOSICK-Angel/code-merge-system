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
