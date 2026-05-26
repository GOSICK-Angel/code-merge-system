"""Semantic chunk processor for large-file LLM merging.

Splits source files at language-aware boundaries so each chunk fits within
the LLM context limit.  Chunks from two versions of the same file are then
aligned proportionally so callers can merge them chunk-by-chunk.
"""

from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Language boundary patterns
# A boundary is a line that starts a new top-level semantic unit.
# ---------------------------------------------------------------------------

_PYTHON_BOUNDARY = re.compile(r"^(?:def |class |async def )")
_GO_BOUNDARY = re.compile(r"^func ")
_JS_BOUNDARY = re.compile(
    r"^(?:export |function |class |const\s+\w+\s*=\s*(?:async\s*)?(?:function|\())"
)
_RUST_BOUNDARY = re.compile(
    r"^(?:pub (?:fn|struct|enum|impl|trait)|fn |impl |struct |enum )"
)
_JAVA_BOUNDARY = re.compile(r"^(?:public |private |protected |class |interface |enum )")
_BLANK_LINE = re.compile(r"^\s*$")


def _boundary_pattern(ext: str) -> re.Pattern[str]:
    table: dict[str, re.Pattern[str]] = {
        ".py": _PYTHON_BOUNDARY,
        ".go": _GO_BOUNDARY,
        ".js": _JS_BOUNDARY,
        ".ts": _JS_BOUNDARY,
        ".jsx": _JS_BOUNDARY,
        ".tsx": _JS_BOUNDARY,
        ".rs": _RUST_BOUNDARY,
        ".java": _JAVA_BOUNDARY,
        ".kt": _JAVA_BOUNDARY,
    }
    return table.get(ext, _BLANK_LINE)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def split_by_semantic_boundary(
    content: str,
    file_path: str,
    chunk_size: int,
) -> list[str]:
    """Split *content* into chunks of at most *chunk_size* chars.

    Splits are made at semantic boundaries (function/class definitions for
    code files, blank lines for others).  If no suitable boundary is found
    before *chunk_size* is exceeded the chunk is force-split at the nearest
    blank line, then at an arbitrary position as a last resort.

    Returns a list of non-empty strings that concatenate back to *content*.
    """
    if len(content) <= chunk_size:
        return [content]

    ext = Path(file_path).suffix.lower()
    pattern = _boundary_pattern(ext)
    lines = content.splitlines(keepends=True)
    boundaries = _find_boundaries(lines, pattern)

    return _group_into_chunks(lines, boundaries, chunk_size)


def merge_chunks(chunks: list[str]) -> str:
    """Concatenate chunks back into a single file.

    Chunks are produced by slicing ``splitlines(keepends=True)`` at semantic
    boundaries (``split_by_semantic_boundary``), so every seam between two
    chunks falls between two whole lines. But each chunk is round-tripped
    through the LLM and ``parse_merge_result`` ``.strip()``s the response,
    dropping the chunk's trailing newline. A naive ``"".join`` then glues the
    last line of one chunk onto the first line of the next — and because Go/
    Python/JS boundaries split *before* a ``func``/``def`` line, the seam sits
    between a doc comment and its declaration, producing
    ``// ...reservedfunc IsUsableUsername(...)`` which comments the function
    out and breaks compilation. Re-insert the separating newline whenever a
    non-final chunk lost it so seams stay on line boundaries.
    """
    if not chunks:
        return ""
    parts: list[str] = []
    last = len(chunks) - 1
    for i, chunk in enumerate(chunks):
        parts.append(chunk)
        if i != last and chunk and not chunk.endswith("\n"):
            parts.append("\n")
    return "".join(parts)


def align_chunks(
    chunks_a: list[str],
    chunks_b: list[str],
) -> list[tuple[str, str]]:
    """Pair chunks from two versions of the same file.

    Returns one ``(chunk_from_a, joined_chunks_from_b)`` pair per *chunks_a*
    element, so callers (the chunked semantic merge) cover every fork-side
    chunk in order.

    When the two sides have different chunk counts the alignment assigns each
    *chunks_b* element to the *chunks_a* element whose positional midpoint is
    closest, then joins each group in order. This is a **covering, one-shot**
    assignment: every *chunks_b* element lands in exactly one pair — never
    dropped, never duplicated. Because the midpoints are monotonic, each a
    chunk receives a *contiguous* run of b chunks, so the join reproduces a
    real upstream slice. An a chunk that attracts no b chunk gets an empty
    target (a fork-only region with no upstream counterpart).

    The earlier nearest-b-per-a mapping was many-to-one: unselected upstream
    chunks vanished from the merge (silent loss of upstream changes) and
    multiply-selected ones were merged repeatedly. The executor's fidelity
    guard only catches invented characters, not missing content, so that loss
    was silent — hence the inversion to a b-covering assignment.
    """
    if not chunks_a or not chunks_b:
        return []

    if len(chunks_a) == len(chunks_b):
        return list(zip(chunks_a, chunks_b))

    line_counts_a = [c.count("\n") + 1 for c in chunks_a]
    line_counts_b = [c.count("\n") + 1 for c in chunks_b]
    total_a = sum(line_counts_a)
    total_b = sum(line_counts_b)

    # Positional midpoint ratio of each a chunk.
    cum_a = 0
    midpoints_a: list[float] = []
    for lc in line_counts_a:
        midpoints_a.append((cum_a + lc / 2) / total_a)
        cum_a += lc

    # Assign every b chunk to its closest a midpoint — each b consumed once.
    groups: list[list[str]] = [[] for _ in chunks_a]
    cum_b = 0
    for chunk_b, lc_b in zip(chunks_b, line_counts_b):
        ratio_b = (cum_b + lc_b / 2) / total_b
        cum_b += lc_b
        best_i = min(range(len(chunks_a)), key=lambda i: abs(midpoints_a[i] - ratio_b))
        groups[best_i].append(chunk_b)

    return [(chunk_a, "".join(groups[i])) for i, chunk_a in enumerate(chunks_a)]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _find_boundaries(lines: list[str], pattern: re.Pattern[str]) -> set[int]:
    """Return line indices that start a new semantic unit."""
    return {i for i, line in enumerate(lines) if pattern.match(line)}


def _group_into_chunks(
    lines: list[str],
    boundaries: set[int],
    chunk_size: int,
) -> list[str]:
    """Build split indices then slice — avoids double-processing lines."""
    split_indices: list[int] = [0]
    current_size = 0

    for i, line in enumerate(lines):
        current_size += len(line)
        if current_size < chunk_size:
            continue

        if i in boundaries:
            split_indices.append(i)
            current_size = len(line)
        elif current_size >= chunk_size * 1.5 and _BLANK_LINE.match(line):
            split_indices.append(i)
            current_size = len(line)
        elif current_size >= chunk_size * 2:
            split_indices.append(i)
            current_size = len(line)

    split_indices.append(len(lines))

    chunks: list[str] = []
    for start, end in zip(split_indices, split_indices[1:]):
        chunk = "".join(lines[start:end])
        if chunk:
            chunks.append(chunk)
    return chunks
