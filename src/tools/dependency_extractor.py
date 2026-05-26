"""Build a :class:`FileDependencyGraph` from source files.

Orchestration layer: builds the shared module index, routes each file to
the per-language backend in :mod:`src.tools.dep_extractors`, and merges the
resulting edges. Python uses the stdlib ``ast`` backend (always available);
other languages use the tree-sitter backend, which degrades to no edges
when the optional ``[ast]`` extra is not installed.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from src.models.dependency import (
    DependencyEdge,
    FileDependencyGraph,
)
from src.tools.dep_extractors import (
    SUPPORTED_LANGUAGES,
    language_for,
    python_extractor,
    treesitter_extractor,
)
from src.tools.dep_extractors.alias_resolver import AliasMap

logger = logging.getLogger(__name__)


class DependencyExtractor:
    @staticmethod
    def extract_from_sources(
        files: dict[str, str],
        languages: Iterable[str] | None = None,
        alias_map: AliasMap | None = None,
    ) -> FileDependencyGraph:
        if not files:
            return FileDependencyGraph(file_count=0)

        allowed = set(languages) if languages is not None else set(SUPPORTED_LANGUAGES)

        module_index = python_extractor.build_module_index(files.keys())
        path_set = set(files.keys())

        edges: list[DependencyEdge] = []
        for file_path, source in files.items():
            lang = language_for(file_path)
            if lang is None or lang not in allowed:
                continue
            if lang == "python":
                edges.extend(
                    python_extractor.extract_imports(file_path, source, module_index)
                )
            else:
                edges.extend(
                    treesitter_extractor.extract_imports(
                        file_path, source, lang, path_set, alias_map
                    )
                )

        return FileDependencyGraph(
            edges=tuple(edges),
            file_count=len(files),
        )


def build_dependency_summary(
    graph: FileDependencyGraph,
    target_files: list[str],
) -> str:
    if not target_files or not graph.edges:
        return ""

    file_set = set(target_files)
    relevant = [
        e
        for e in graph.edges
        if e.source_file in file_set and e.target_file in file_set
    ]
    if not relevant:
        return ""

    lines: list[str] = ["## File Dependencies"]
    for edge in relevant:
        lines.append(f"- {edge.target_file} <- {edge.source_file} ({edge.kind.value})")

    order = graph.topological_order(target_files)
    lines.append("")
    lines.append("Suggested merge order: " + " -> ".join(order))

    return "\n".join(lines)


def build_impact_summary(
    graph: FileDependencyGraph,
    file_path: str,
    max_depth: int = 2,
) -> str:
    impacted = graph.impact_radius(file_path, max_depth=max_depth)
    if not impacted:
        return ""

    lines = [f"Files depending on {file_path} (within {max_depth} hops):"]
    for f in sorted(impacted):
        lines.append(f"- {f}")

    return "\n".join(lines)
