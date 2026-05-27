"""Deterministic detection of duplicated top-level declarations.

Chunked semantic merge (``executor_agent._execute_chunked_semantic_merge``)
merges each chunk pair independently and concatenates the results. Adjacent
chunks that share a boundary structure can each re-emit the same top-level
declaration, yielding a file with two ``export const Foo`` / ``def foo`` at
module scope — a compile error (TS2451 / TS2393) that the executor's fidelity
guard does not catch.

This module finds such duplicates with a language-aware, LLM-free scan so the
judge (and a future verification gate) can flag them deterministically.

Only *value* bindings are reported (``const``/``let``/``var``/``function``/
``class``/``def``/``func``/``struct``/``enum``/``trait``). Type-only constructs
that legally merge — TypeScript ``interface`` and ``type`` — are intentionally
excluded to avoid false positives.

Only column-0 (top-level) declarations are considered; an inner declaration
that shadows a top-level name is not a redeclaration error.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel

_NAME = r"(?P<name>[A-Za-z_$][\w$]*)"

# Per-language: ordered (kind, compiled-pattern) pairs matching a top-level
# value declaration. Patterns are anchored at column 0 (no leading space) and
# capture the declared symbol in the ``name`` group.
#
# JS/TS deliberately omits ``function``: TypeScript/JavaScript permit overload
# signatures and re-declared ``function``, so multiple top-level ``function
# foo`` lines are legal and common (zod declares N overloads + 1 impl) — they
# would be false positives. Only ``const``/``let``/``var``/``class`` cannot be
# re-declared, so a repeat is an unambiguous error; chunk-boundary block
# duplication always drags a ``const``/``class`` along with it, so the
# boundary-duplication signal is still caught.
_JS_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("class", re.compile(rf"^export\s+(?:default\s+)?(?:abstract\s+)?class\s+{_NAME}")),
    ("class", re.compile(rf"^(?:abstract\s+)?class\s+{_NAME}")),
    ("const", re.compile(rf"^export\s+(?:const|let|var)\s+{_NAME}")),
    ("const", re.compile(rf"^(?:const|let|var)\s+{_NAME}\s*=")),
]
_PY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("function", re.compile(rf"^(?:async\s+)?def\s+{_NAME}")),
    ("class", re.compile(rf"^class\s+{_NAME}")),
]
_GO_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("function", re.compile(rf"^func\s+{_NAME}")),
    ("type", re.compile(rf"^type\s+{_NAME}")),
]
_RUST_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("function", re.compile(rf"^(?:pub\s+)?fn\s+{_NAME}")),
    ("struct", re.compile(rf"^(?:pub\s+)?struct\s+{_NAME}")),
    ("enum", re.compile(rf"^(?:pub\s+)?enum\s+{_NAME}")),
    ("trait", re.compile(rf"^(?:pub\s+)?trait\s+{_NAME}")),
]

_PATTERNS_BY_EXT: dict[str, list[tuple[str, re.Pattern[str]]]] = {
    ".js": _JS_PATTERNS,
    ".jsx": _JS_PATTERNS,
    ".ts": _JS_PATTERNS,
    ".tsx": _JS_PATTERNS,
    ".mjs": _JS_PATTERNS,
    ".cjs": _JS_PATTERNS,
    ".py": _PY_PATTERNS,
    ".go": _GO_PATTERNS,
    ".rs": _RUST_PATTERNS,
}


class DuplicateSymbol(BaseModel):
    """A top-level symbol declared more than once in one file."""

    name: str
    kind: str
    count: int
    lines: list[int]


def _match_line(
    line: str, patterns: list[tuple[str, re.Pattern[str]]]
) -> tuple[str, str] | None:
    """Return (kind, name) for the first matching top-level declaration."""
    for kind, pattern in patterns:
        m = pattern.match(line)
        if m:
            return kind, m.group("name")
    return None


def find_duplicate_symbols(content: str, file_path: str) -> list[DuplicateSymbol]:
    """Return top-level value declarations that appear more than once.

    Returns an empty list for unsupported file types or when no symbol is
    declared twice. Results are ordered by first-occurrence line so callers
    get stable output.
    """
    patterns = _PATTERNS_BY_EXT.get(Path(file_path).suffix.lower())
    if not patterns or not content:
        return []

    seen: dict[tuple[str, str], list[int]] = {}
    order: list[tuple[str, str]] = []
    for lineno, line in enumerate(content.splitlines(), start=1):
        if not line or line[0].isspace():
            continue  # only column-0 declarations are top-level
        hit = _match_line(line, patterns)
        if hit is None:
            continue
        if hit not in seen:
            seen[hit] = []
            order.append(hit)
        seen[hit].append(lineno)

    return [
        DuplicateSymbol(
            name=name,
            kind=kind,
            count=len(seen[(kind, name)]),
            lines=seen[(kind, name)],
        )
        for (kind, name) in order
        if len(seen[(kind, name)]) > 1
    ]
