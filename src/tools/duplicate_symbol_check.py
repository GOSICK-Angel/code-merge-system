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


# #10: a top-level JS/TS function IMPLEMENTATION whose signature AND opening
# body brace are on one line (``function foo(...) {``). Overload signatures end
# in ``;`` (no ``{``) so they never match — this is the conservative subset that
# is unambiguously a redeclaration error (TS2451) when it repeats, with no risk
# of confusing a legal overload set. Multi-line-signature impls are not matched
# (missed, but never a false positive). Used for ESCALATION only, never auto-
# deletion: a false positive here would be a safe over-escalation, but auto-
# deleting a function span risks dropping a real overload — corruption.
_JS_FUNCTION_IMPL = re.compile(
    rf"^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*{_NAME}\s*\([^;]*\)"
    r"(?:\s*:\s*[^;{]+)?\s*\{"
)
_JS_FUNCTION_EXTS = frozenset({".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"})


def find_duplicate_function_impls(content: str, file_path: str) -> list[str]:
    """Return JS/TS function names declared as a single-line IMPLEMENTATION
    (``function foo(...) {``) more than once at top level — an unambiguous
    redeclaration error a chunk seam can introduce. Empty for non-JS/TS files.
    """
    if Path(file_path).suffix.lower() not in _JS_FUNCTION_EXTS or not content:
        return []
    counts: dict[str, int] = {}
    order: list[str] = []
    for line in content.splitlines():
        if not line or line[0].isspace():
            continue
        m = _JS_FUNCTION_IMPL.match(line)
        if not m:
            continue
        name = m.group("name")
        if name not in counts:
            counts[name] = 0
            order.append(name)
        counts[name] += 1
    return [n for n in order if counts[n] > 1]


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


def remove_duplicate_top_level_symbols(content: str, file_path: str) -> str:
    """Drop later re-declarations of a top-level symbol, keeping the first.

    方案3.1 seam-dedup: ``merge_chunks`` only concatenates per-chunk LLM output,
    so adjacent chunks that re-emit the same ``const`` / ``class`` produce a
    file with two top-level declarations of one name — uncompilable. Each
    declaration's span runs from its column-0 declaration line to the next
    top-level declaration (or EOF); for a symbol declared more than once the
    first span is kept and the rest removed.

    Keep-*first* rather than the plan's "prefer the upstream-bearing version"
    because ``merge_chunks`` has no diff context to tell which copy carries the
    upstream change; the deterministic choice still yields a compilable file
    and the judge / build gate catch any remaining semantic gap. A no-op
    (returns ``content`` unchanged) for unsupported file types or when nothing
    is declared twice, so clean merges are never touched.
    """
    patterns = _PATTERNS_BY_EXT.get(Path(file_path).suffix.lower())
    if not patterns or not content:
        return content

    dups = find_duplicate_symbols(content, file_path)
    if not dups:
        return content

    drop_starts = {ln for d in dups for ln in d.lines[1:]}  # keep first occurrence

    lines = content.splitlines(keepends=True)
    decl_starts = [
        idx
        for idx, raw in enumerate(lines, start=1)
        if raw and not raw[0].isspace() and _match_line(raw, patterns) is not None
    ]
    span_end = {
        start: (decl_starts[i + 1] if i + 1 < len(decl_starts) else len(lines) + 1)
        for i, start in enumerate(decl_starts)
    }

    dropped: set[int] = set()
    for start in drop_starts:
        dropped.update(range(start, span_end[start]))

    return "".join(raw for idx, raw in enumerate(lines, start=1) if idx not in dropped)
