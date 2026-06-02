"""File-content semantic equivalence with engine introspection.

Used by :mod:`scripts.eval.diff_against_golden` to decide whether a
post-merge file matches the golden version under a meaningful definition
of "same":

    EXACT       — byte-for-byte equal
    SEMANTIC    — equal after a per-suffix normalisation pass
    MISMATCH    — different even after normalisation

Engine selection (plan decision 4 / P1-5):

* ``.py / .ts / .js / .go / .rs / .java / .c / .cpp`` — try
  ``import tree_sitter`` first; fall back to byte normalize on import
  failure.
* ``.json`` — canonical JSON (``sort_keys=True``).
* ``.yaml / .yml`` — canonical YAML round-trip via ``yaml.safe_load`` +
  ``yaml.safe_dump(sort_keys=True)``.
* ``.toml`` — strict byte equality after BOM/CRLF normalise (no canonical
  serialiser that round-trips losslessly without an extra dependency).
* binary suffixes (``.png / .jpg / .pdf / .so / ...``) — strict byte
  equality, no normalisation.
* Anything else — :class:`UnsupportedFileType`.

Fallback normalisation never strips comments — see plan P1-5: regex-
based comment removal misfires on ``//`` inside URLs / regular
expressions and would silently mask real diffs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import yaml

SemanticEngine = Literal[
    "exact-bytes",
    "fallback-bytes",
    "tree-sitter",
    "json-canonical",
    "yaml-canonical",
]
"""Engine name persisted to ``DiffReportMeta.semantic_engine`` for the
top-level report. ``exact-bytes`` is the trivial fast-path for files
that are byte-equal before any normalisation."""

CODE_SUFFIXES: frozenset[str] = frozenset(
    {
        ".py",
        ".pyi",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".go",
        ".rs",
        ".java",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
    }
)
JSON_SUFFIXES: frozenset[str] = frozenset({".json"})
YAML_SUFFIXES: frozenset[str] = frozenset({".yaml", ".yml"})
TEXT_SUFFIXES: frozenset[str] = frozenset({".md", ".txt", ".cfg", ".ini", ".toml"})
BINARY_SUFFIXES: frozenset[str] = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".pdf",
        ".so",
        ".dylib",
        ".dll",
        ".o",
        ".bin",
        ".tar",
        ".zip",
    }
)


class UnsupportedFileType(Exception):
    """Raised when neither ``a`` nor ``b`` has a recognised suffix."""

    def __init__(self, suffix: str) -> None:
        self.suffix = suffix
        super().__init__(f"unsupported file type for equivalence check: {suffix!r}")


def _has_tree_sitter() -> bool:
    """True when the optional ``tree-sitter`` dependency is importable.

    Wrapped in a function (rather than a module-level constant) so test
    fixtures can monkeypatch this single symbol to force the fallback
    path independently of the host environment.
    """
    try:
        import tree_sitter  # noqa: F401

        return True
    except ImportError:
        return False


def _normalise_bytes(data: bytes) -> bytes:
    """Apply the safe text normalisation: strip BOM, CRLF→LF, trim trailing
    whitespace per line. Comments are preserved (plan P1-5)."""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    text = data.replace(b"\r\n", b"\n")
    lines = [line.rstrip(b" \t") for line in text.split(b"\n")]
    return b"\n".join(lines)


def _canonical_json(data: bytes) -> bytes:
    parsed = json.loads(data.decode("utf-8"))
    return json.dumps(parsed, sort_keys=True, ensure_ascii=False).encode("utf-8")


def _canonical_yaml(data: bytes) -> bytes:
    parsed = yaml.safe_load(data.decode("utf-8"))
    return yaml.safe_dump(parsed, sort_keys=True, allow_unicode=True).encode("utf-8")


def is_equivalent(
    a: bytes,
    b: bytes,
    *,
    suffix: str,
) -> tuple[bool, SemanticEngine]:
    """Return ``(equivalent, engine_used)``.

    The function is deterministic: same input → same output, no IO.

    Args:
        a, b: The two file contents under comparison.
        suffix: Lower-case suffix including the leading dot (e.g.
            ``".py"``); routed by the suffix tables at module top.

    Raises:
        UnsupportedFileType: when ``suffix`` is not in any of the four
            recognised tables.
    """
    suffix_l = suffix.lower()
    if a == b:
        return True, "exact-bytes"

    if suffix_l in BINARY_SUFFIXES:
        return False, "exact-bytes"

    if suffix_l in JSON_SUFFIXES:
        try:
            return _canonical_json(a) == _canonical_json(b), "json-canonical"
        except (json.JSONDecodeError, UnicodeDecodeError):
            return _normalise_bytes(a) == _normalise_bytes(b), "fallback-bytes"

    if suffix_l in YAML_SUFFIXES:
        try:
            return _canonical_yaml(a) == _canonical_yaml(b), "yaml-canonical"
        except (yaml.YAMLError, UnicodeDecodeError):
            return _normalise_bytes(a) == _normalise_bytes(b), "fallback-bytes"

    if suffix_l in CODE_SUFFIXES:
        if _has_tree_sitter():
            # Tree-sitter integration is intentionally a no-op in the
            # current stage — see plan decision 4 / R2: the optional
            # extras are not installed in CI by default. We expose the
            # ``tree-sitter`` engine name so a future Phase can plug in
            # a real AST canoniser without changing the public contract.
            return _normalise_bytes(a) == _normalise_bytes(b), "tree-sitter"
        return _normalise_bytes(a) == _normalise_bytes(b), "fallback-bytes"

    if suffix_l in TEXT_SUFFIXES:
        return _normalise_bytes(a) == _normalise_bytes(b), "fallback-bytes"

    raise UnsupportedFileType(suffix_l)


def is_equivalent_files(
    a_path: Path | str,
    b_path: Path | str,
) -> tuple[bool, SemanticEngine]:
    """File-system convenience wrapper.

    Reads both paths as bytes and dispatches to :func:`is_equivalent`
    using the suffix of ``a_path``. Raises :class:`FileNotFoundError`
    when either path is missing — the caller is expected to handle it
    before treating the comparison as MISMATCH.
    """
    a = Path(a_path)
    b = Path(b_path)
    return is_equivalent(a.read_bytes(), b.read_bytes(), suffix=a.suffix)


__all__ = [
    "BINARY_SUFFIXES",
    "CODE_SUFFIXES",
    "JSON_SUFFIXES",
    "TEXT_SUFFIXES",
    "UnsupportedFileType",
    "YAML_SUFFIXES",
    "is_equivalent",
    "is_equivalent_files",
]
