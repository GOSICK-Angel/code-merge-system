"""Tree-sitter backed import extraction for non-Python languages.

Uses the same optional ``[ast]`` grammars as the AST chunker, but loads
parsers via a self-contained loader (the chunker's loader assumes a uniform
``module.language()`` entry point, which the installed
``tree_sitter_typescript`` build does not expose — it ships
``language_typescript()`` / ``language_tsx()`` instead). When tree-sitter or
a grammar binding is unavailable the public entry point degrades to an empty
edge list — a JS/TS/Go repo simply yields no dependency edges rather than
crashing.

Resolution is intentionally conservative: only *static* import strings are
turned into edges (EXTRACTED), and only when they resolve to a file inside
the provided ``path_set``. Bare specifiers (node_modules / third-party / Go
std) produce no edge.
"""

from __future__ import annotations

import importlib
import logging
import posixpath
from collections.abc import Iterable
from typing import Any

from src.models.dependency import (
    ConfidenceLabel,
    DependencyEdge,
    DependencyKind,
)
from src.tools.dep_extractors.alias_resolver import AliasMap

logger = logging.getLogger(__name__)

# language -> grammar module name (subset of the [ast] extra).
_GRAMMAR_MODULE: dict[str, str] = {
    "javascript": "tree_sitter_javascript",
    "typescript": "tree_sitter_typescript",
    "tsx": "tree_sitter_typescript",
    "go": "tree_sitter_go",
}

_PARSER_CACHE: dict[str, Any] = {}

# Tree-sitter node types carrying an import path string, per language.
_IMPORT_NODE_TYPES: dict[str, set[str]] = {
    "javascript": {"import_statement", "export_statement"},
    "typescript": {"import_statement", "export_statement"},
    "tsx": {"import_statement", "export_statement"},
    "go": {"import_spec"},
}

# Candidate extensions tried (in order) when a relative JS/TS import omits
# its extension. ``index.*`` fallbacks are appended at resolution time.
_JS_EXTS: tuple[str, ...] = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


def extract_imports(
    file_path: str,
    source: str,
    language: str,
    path_set: set[str],
    alias_map: AliasMap | None = None,
) -> list[DependencyEdge]:
    """Return static-import edges for ``file_path`` resolvable in ``path_set``.

    Degrades to ``[]`` when tree-sitter is unavailable or the grammar for
    ``language`` cannot be loaded. When ``alias_map`` is supplied (Phase C
    §6.3), aliased / bare specifiers are resolved through it before giving up.
    """
    parser = _load_parser(language)
    if parser is None:
        return []

    try:
        tree = parser.parse(source.encode("utf-8"))
    except Exception:
        logger.debug("tree-sitter parse failed for %s", file_path)
        return []

    imports = _collect_imports(tree.root_node, source, language)
    edges: list[DependencyEdge] = []
    for raw, symbols in imports:
        target = _resolve(file_path, raw, language, path_set, alias_map)
        if not target or target == file_path:
            continue
        if symbols:
            edges.extend(
                DependencyEdge(
                    source_file=file_path,
                    target_file=target,
                    kind=DependencyKind.IMPORTS,
                    target_symbol=sym,
                    confidence=ConfidenceLabel.EXTRACTED,
                )
                for sym in symbols
            )
        else:
            edges.append(
                DependencyEdge(
                    source_file=file_path,
                    target_file=target,
                    kind=DependencyKind.IMPORTS,
                    confidence=ConfidenceLabel.EXTRACTED,
                )
            )
    return edges


def _load_parser(language: str) -> Any | None:
    if language in _PARSER_CACHE:
        return _PARSER_CACHE[language]

    module_name = _GRAMMAR_MODULE.get(language)
    if module_name is None:
        return None

    try:
        import tree_sitter

        grammar = importlib.import_module(module_name)
        lang_obj = tree_sitter.Language(_grammar_language(grammar, language))
        parser = tree_sitter.Parser(lang_obj)
    except Exception:
        logger.debug("tree-sitter parser unavailable for %s", language)
        _PARSER_CACHE[language] = None
        return None

    _PARSER_CACHE[language] = parser
    return parser


def _grammar_language(grammar: Any, language: str) -> Any:
    """Return the PyCapsule for ``language`` across grammar-module API shapes.

    ``tree_sitter_typescript`` exposes ``language_typescript`` /
    ``language_tsx`` rather than a single ``language()``; single-language
    grammars expose ``language()``.
    """
    if language == "typescript" and hasattr(grammar, "language_typescript"):
        return grammar.language_typescript()
    if language == "tsx" and hasattr(grammar, "language_tsx"):
        return grammar.language_tsx()
    return grammar.language()


def _collect_imports(root, source: str, language: str) -> list[tuple[str, list[str]]]:  # type: ignore[no-untyped-def]
    """Return ``(module_path, [named_symbols])`` per static import.

    ``named_symbols`` are the identifiers of named imports/exports
    (``import { foo, bar } from "./m"``) — these match the *exported symbol
    names* in the target file (i.e. chunk names). Default / namespace imports
    bind a local alias that need not equal the target symbol, so they are
    omitted (the edge is still created, just without a symbol)."""
    wanted = _IMPORT_NODE_TYPES.get(language, set())
    if not wanted:
        return []
    results: list[tuple[str, list[str]]] = []
    stack = [root]
    while stack:
        node = stack.pop()
        node_type = getattr(node, "type", "")
        if node_type in wanted:
            literal = _first_string_literal(node, source)
            if literal:
                results.append((literal, _named_import_symbols(node, source)))
        stack.extend(getattr(node, "children", []))
    return results


_SPECIFIER_NODE_TYPES = {"import_specifier", "export_specifier"}


def _named_import_symbols(node, source: str) -> list[str]:  # type: ignore[no-untyped-def]
    """Imported names from ``import_specifier`` / ``export_specifier`` nodes.

    Takes the first identifier of each specifier — the imported name, before
    any ``as`` alias (``foo as f`` → ``foo``)."""
    symbols: list[str] = []
    stack = list(getattr(node, "children", []))
    while stack:
        child = stack.pop()
        ctype = getattr(child, "type", "")
        if ctype in _SPECIFIER_NODE_TYPES:
            for sub in getattr(child, "children", []):
                if getattr(sub, "type", "") in ("identifier", "property_identifier"):
                    symbols.append(_node_text(sub, source))
                    break
        else:
            stack.extend(getattr(child, "children", []))
    return symbols


def _first_string_literal(node, source: str) -> str | None:  # type: ignore[no-untyped-def]
    """Find the first string-literal descendant and return its unquoted text."""
    stack = list(getattr(node, "children", []))
    while stack:
        child = stack.pop(0)
        ctype = getattr(child, "type", "")
        if "string" in ctype:
            text = _node_text(child, source)
            return _strip_quotes(text)
        stack.extend(getattr(child, "children", []))
    return None


def _node_text(node, source: str) -> str:  # type: ignore[no-untyped-def]
    start = getattr(node, "start_byte", 0)
    end = getattr(node, "end_byte", 0)
    return source.encode("utf-8")[start:end].decode("utf-8", errors="replace")


def _strip_quotes(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] in "\"'`" and text[-1] in "\"'`":
        return text[1:-1]
    return text


def _resolve(
    source_file: str,
    spec: str,
    language: str,
    path_set: set[str],
    alias_map: AliasMap | None = None,
) -> str | None:
    if language == "go":
        target = _resolve_go(spec, path_set)
        if target is None and alias_map is not None:
            return alias_map.resolve_go(spec, path_set)
        return target
    target = _resolve_js_relative(source_file, spec, path_set)
    if target is None and alias_map is not None and not spec.startswith("."):
        return alias_map.resolve_js(spec, path_set)
    return target


def _resolve_js_relative(
    source_file: str,
    spec: str,
    path_set: set[str],
) -> str | None:
    # Only relative specifiers map to in-repo files; bare specifiers are deps.
    if not spec.startswith("."):
        return None
    base_dir = posixpath.dirname(source_file.replace("\\", "/"))
    target = posixpath.normpath(posixpath.join(base_dir, spec))

    if target in path_set:
        return target
    for ext in _JS_EXTS:
        cand = target + ext
        if cand in path_set:
            return cand
    for ext in _JS_EXTS:
        cand = posixpath.join(target, "index" + ext)
        if cand in path_set:
            return cand
    return None


def _resolve_go(spec: str, path_set: set[str]) -> str | None:
    # Go import paths are package paths; match a repo file whose directory
    # ends with the import path. Best-effort and conservative.
    spec = spec.strip("/")
    if not spec:
        return None
    for fp in path_set:
        norm = fp.replace("\\", "/")
        if not norm.endswith(".go"):
            continue
        pkg_dir = posixpath.dirname(norm)
        if pkg_dir == spec or pkg_dir.endswith("/" + spec):
            return fp
    return None


def supported_languages() -> Iterable[str]:
    return tuple(_IMPORT_NODE_TYPES.keys())
