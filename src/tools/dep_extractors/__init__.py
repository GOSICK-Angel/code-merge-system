"""Per-language dependency edge extractors.

The orchestrator (:mod:`src.tools.dependency_extractor`) routes each file to
the backend for its language by extension:

* ``.py`` -> :mod:`.python_extractor` (stdlib ``ast``, always available)
* JS / TS / Go -> :mod:`.treesitter_extractor` (tree-sitter, degrades to
  empty when the optional ``[ast]`` extra is not installed)

Languages without a backend (or excluded by config) yield no edges.
"""

from __future__ import annotations

from pathlib import PurePosixPath

# File extension -> canonical language name used across the extractors and
# the ``DependencyGraphConfig.languages`` allow-list.
_EXT_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
}

# Languages the Phase A extraction pipeline can attempt at all.
SUPPORTED_LANGUAGES: tuple[str, ...] = (
    "python",
    "javascript",
    "typescript",
    "tsx",
    "go",
)


def language_for(file_path: str) -> str | None:
    suffix = PurePosixPath(file_path.replace("\\", "/")).suffix.lower()
    return _EXT_LANGUAGE.get(suffix)
