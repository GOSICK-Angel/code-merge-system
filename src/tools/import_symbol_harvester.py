"""Extract symbols exposed by namespace imports in a TS/JS file.

PR-D-B: ``conflict_analyst`` previously had no view of what symbols an
imported module actually exposed, so the LLM pattern-completed names
that did not exist (zod's ``core._isoWeek`` was inferred from seeing
``core._isoDate / _isoTime / _isoDuration``). This harvester gives the
analyst prompt a concrete map of ``base.symbol`` surfaces it may
legitimately reference, closing that fabrication path at the source.

Scope is deliberately narrow:

- Only ``import * as <name> from "<path>"`` (namespace imports) — the
  only form that produces qualified ``base.member`` refs where
  fabrication has been observed.
- TypeScript / JavaScript only (and any text where ``export <kind>
  <name>`` syntax is meaningful — Flow, Svelte ``.ts`` blocks, etc.).
- Resolution is delegated via a callback so this module has no git or
  filesystem dependency.
"""

from __future__ import annotations

import posixpath
import re
from typing import Any, Callable

# import * as NAME from "PATH"  — allow single or double quotes and
# trailing semicolons / extra whitespace.
_NAMESPACE_IMPORT = re.compile(
    r'^\s*import\s+\*\s+as\s+[A-Za-z_$][\w$]*\s+from\s+["\']([^"\']+)["\']',
    re.MULTILINE,
)

# export (async)? function | const | let | var | class | interface | type | enum  NAME
_EXPORTED = re.compile(
    r"^\s*export\s+"
    r"(?:async\s+)?"
    r"(?:function|const|let|var|class|interface|type|enum)\s+"
    r"([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)


# TS / JS source files the resolver may try, in order. ``""`` is the
# verbatim path the LLM-visible import string uses; the rest are the
# common rewrites observed in real projects (e.g. zod imports ``.js``
# but the file on disk is ``.ts``).
_EXTENSION_CANDIDATES: tuple[str, ...] = (
    "",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mts",
    ".cts",
    "/index.ts",
    "/index.tsx",
    "/index.js",
)


def _strip_known_ext(path: str) -> str:
    for ext in (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"):
        if path.endswith(ext):
            return path[: -len(ext)]
    return path


def harvest_imports_for_file(
    source_path: str,
    source_content: str,
    ref: str,
    git_tool: Any,
) -> dict[str, list[str]]:
    """Resolve and harvest each namespace import in ``source_content``.

    Path resolution is best-effort and TS/JS specific: relative imports
    (``./foo``, ``../foo``) are joined to ``source_path``'s directory,
    the trailing ``.js``/``.ts``/etc. is stripped, and a small set of
    extension candidates is tried until ``git_tool.get_file_content``
    returns content. Bare-module imports (``"zod"``, ``"react"``) are
    not resolvable inside this repo and are silently skipped.

    Any failure path — missing git_tool, unresolvable module, parse
    glitch — degrades to a missing entry. The harvester is prompt
    context, never a hard dependency.
    """
    if not source_content or git_tool is None:
        return {}
    base_dir = posixpath.dirname(source_path)
    out: dict[str, list[str]] = {}
    for raw_path in _NAMESPACE_IMPORT.findall(source_content):
        if raw_path in out:
            continue
        if not (raw_path.startswith("./") or raw_path.startswith("../")):
            continue  # bare-module imports are external; skip
        joined = posixpath.normpath(posixpath.join(base_dir, raw_path))
        stem = _strip_known_ext(joined)
        content: str | None = None
        for ext in _EXTENSION_CANDIDATES:
            candidate = stem + ext if ext else joined
            try:
                content = git_tool.get_file_content(ref, candidate)
            except Exception:
                content = None
            if content:
                break
        if content is None:
            continue
        out[raw_path] = [m.group(1) for m in _EXPORTED.finditer(content)]
    return out


def harvest_imported_symbols(
    source: str,
    resolver: Callable[[str], str | None],
) -> dict[str, list[str]]:
    """For each namespace import in ``source``, return its exported names.

    Modules the resolver cannot find are dropped silently — this is
    best-effort prompt context, never a hard dependency. Modules with
    no exports record an empty list so the caller can still see the
    import was resolved.
    """
    if not source:
        return {}
    out: dict[str, list[str]] = {}
    for path in _NAMESPACE_IMPORT.findall(source):
        if path in out:
            continue
        content = resolver(path)
        if content is None:
            continue
        out[path] = [m.group(1) for m in _EXPORTED.finditer(content)]
    return out
