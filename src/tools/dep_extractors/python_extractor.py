"""Python dependency extraction via the stdlib ``ast`` module.

Always available (no tree-sitter required). Moved out of the legacy
``dependency_extractor`` so the orchestrator can route ``.py`` files here
while delegating other languages to the tree-sitter backend.
"""

from __future__ import annotations

import ast
import logging
import os
import sys
from collections.abc import Iterable

from src.models.dependency import (
    ConfidenceLabel,
    DependencyEdge,
    DependencyKind,
)

logger = logging.getLogger(__name__)

_STDLIB_TOP_LEVEL: set[str] | None = None


def _get_stdlib_modules() -> set[str]:
    global _STDLIB_TOP_LEVEL
    if _STDLIB_TOP_LEVEL is not None:
        return _STDLIB_TOP_LEVEL
    _STDLIB_TOP_LEVEL = set(sys.stdlib_module_names)
    return _STDLIB_TOP_LEVEL


def build_module_index(file_paths: Iterable[str]) -> dict[str, str]:
    """Map importable names (stem / dotted / slash forms) -> file path."""
    index: dict[str, str] = {}
    for fp in file_paths:
        if not fp.endswith(".py"):
            continue
        stem = fp[:-3]
        parts = stem.replace(os.sep, "/").split("/")
        index[parts[-1]] = fp
        dotted = ".".join(parts)
        index[dotted] = fp
        slash_path = "/".join(parts)
        if slash_path != parts[-1]:
            index[slash_path] = fp
    return index


def extract_imports(
    file_path: str,
    source: str,
    module_index: dict[str, str],
) -> list[DependencyEdge]:
    """Parse ``source`` and return import edges resolvable within
    ``module_index``. Syntax errors degrade to an empty edge list."""
    try:
        tree = ast.parse(source, filename=file_path)
    except SyntaxError:
        logger.debug("Skipping %s: syntax error", file_path)
        return []

    edges: list[DependencyEdge] = []
    stdlib = _get_stdlib_modules()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in stdlib:
                    continue
                target = _resolve_module(alias.name, module_index)
                if target and target != file_path:
                    edges.append(
                        DependencyEdge(
                            source_file=file_path,
                            target_file=target,
                            kind=DependencyKind.IMPORTS,
                            confidence=ConfidenceLabel.EXTRACTED,
                        )
                    )

        elif isinstance(node, ast.ImportFrom):
            if node.module is None and node.level == 0:
                continue
            module_name = node.module or ""
            top = module_name.split(".")[0] if module_name else ""

            if node.level == 0 and top in stdlib:
                continue

            if node.level > 0:
                target = _resolve_relative_import(
                    file_path, module_name, node.level, module_index
                )
                confidence = ConfidenceLabel.INFERRED
            else:
                target = _resolve_module(module_name, module_index)
                confidence = ConfidenceLabel.EXTRACTED

            if target and target != file_path:
                edges.append(
                    DependencyEdge(
                        source_file=file_path,
                        target_file=target,
                        kind=DependencyKind.IMPORTS,
                        confidence=confidence,
                    )
                )

    return edges


def _resolve_module(
    module_name: str,
    module_index: dict[str, str],
) -> str | None:
    if module_name in module_index:
        return module_index[module_name]

    path_form = module_name.replace(".", "/")
    if path_form in module_index:
        return module_index[path_form]

    parts = module_name.split(".")
    for i in range(len(parts), 0, -1):
        prefix = ".".join(parts[:i])
        if prefix in module_index:
            return module_index[prefix]
        prefix_path = "/".join(parts[:i])
        if prefix_path in module_index:
            return module_index[prefix_path]

    return None


def _resolve_relative_import(
    source_file: str,
    module_name: str,
    level: int,
    module_index: dict[str, str],
) -> str | None:
    source_parts = source_file.replace(os.sep, "/").split("/")
    if len(source_parts) <= level:
        return None

    base_parts = source_parts[:-level]

    if module_name:
        candidate_parts = base_parts + module_name.split(".")
    else:
        return None

    candidate = "/".join(candidate_parts) + ".py"
    if candidate in {v for v in module_index.values()}:
        return candidate

    candidate_slash = "/".join(candidate_parts)
    for key, val in module_index.items():
        if val.replace(os.sep, "/").endswith(candidate):
            return val
        if candidate_slash in key:
            return val

    return None
