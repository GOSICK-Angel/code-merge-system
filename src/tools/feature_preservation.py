"""Deterministic additive fork-export preservation.

A fork that *adds* a top-level public symbol (e.g. ``export const
cidrv6Mapped``) expects it to survive an upstream merge. When conflict
resolution takes the upstream side of an otherwise-additive change, the new
symbol silently vanishes — and a ``file_exists`` feature check still passes
because the file is still there.

This module extracts the public top-level symbols a fork added over the merge
base and checks whether they remain in the merged content, so the feature
inventory can report a dropped additive export as FAIL instead of PASS.

Unlike :mod:`src.tools.duplicate_symbol_check`, ``function``/``interface``/
``type`` are included here: the question is "does symbol X still exist
anywhere", not "is it declared twice", so overload-shaped repeats are
irrelevant.
"""

from __future__ import annotations

import re
from pathlib import Path

_NAME = r"(?P<name>[A-Za-z_$][\w$]*)"

# Top-level *public* declarations, captured into the ``name`` group, anchored
# at column 0. JS/TS keys on the ``export`` keyword; Python treats every
# non-underscore top-level def/class as public; Go uses the capitalised-name
# convention; Rust keys on ``pub``.
_JS_EXPORT = [
    re.compile(rf"^export\s+(?:default\s+)?(?:async\s+)?function\s+{_NAME}"),
    re.compile(rf"^export\s+(?:default\s+)?(?:abstract\s+)?class\s+{_NAME}"),
    re.compile(rf"^export\s+(?:const|let|var)\s+{_NAME}"),
    re.compile(rf"^export\s+(?:interface|type|enum)\s+{_NAME}"),
]
_PY_PUBLIC = [
    re.compile(rf"^(?:async\s+)?def\s+{_NAME}"),
    re.compile(rf"^class\s+{_NAME}"),
]
_GO_PUBLIC = [
    re.compile(rf"^func\s+{_NAME}"),
    re.compile(rf"^type\s+{_NAME}"),
    re.compile(rf"^(?:var|const)\s+{_NAME}"),
]
_RUST_PUBLIC = [
    re.compile(rf"^pub\s+(?:async\s+)?fn\s+{_NAME}"),
    re.compile(rf"^pub\s+(?:struct|enum|trait|const|static)\s+{_NAME}"),
]

_PATTERNS_BY_EXT: dict[str, list[re.Pattern[str]]] = {
    ".js": _JS_EXPORT,
    ".jsx": _JS_EXPORT,
    ".ts": _JS_EXPORT,
    ".tsx": _JS_EXPORT,
    ".mjs": _JS_EXPORT,
    ".cjs": _JS_EXPORT,
    ".py": _PY_PUBLIC,
    ".go": _GO_PUBLIC,
    ".rs": _RUST_PUBLIC,
}


def _is_public(name: str, ext: str) -> bool:
    if ext in (".py",):
        return not name.startswith("_")
    if ext in (".go",):
        return name[:1].isupper()
    return True


def extract_exported_symbols(content: str, file_path: str) -> set[str]:
    """Return the set of public top-level symbol names declared in *content*.

    Empty for unsupported file types.
    """
    ext = Path(file_path).suffix.lower()
    patterns = _PATTERNS_BY_EXT.get(ext)
    if not patterns or not content:
        return set()

    found: set[str] = set()
    for line in content.splitlines():
        if not line or line[0].isspace():
            continue
        for pattern in patterns:
            m = pattern.match(line)
            if m:
                name = m.group("name")
                if _is_public(name, ext):
                    found.add(name)
                break
    return found


def added_exported_symbols(
    base_content: str, fork_content: str, file_path: str
) -> set[str]:
    """Public top-level symbols present in *fork* but not in *base*."""
    return extract_exported_symbols(fork_content, file_path) - extract_exported_symbols(
        base_content, file_path
    )


def missing_symbols(
    merged_content: str, expected: set[str], file_path: str
) -> set[str]:
    """Of *expected* symbols, those absent from *merged_content*."""
    if not expected:
        return set()
    return expected - extract_exported_symbols(merged_content, file_path)
