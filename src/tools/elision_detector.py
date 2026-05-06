"""P0-2: Elision-marker detector for LLM-generated merge content.

When the chunker stages a large file for the LLM (see ``src/llm/chunker.py``
``render_file_staged``), it injects markers like::

    # ... (3 sections omitted)

LLMs sometimes echo these markers back into their merge output instead of
producing a complete file. Writing such truncated content to disk silently
deletes hundreds of lines of fork code. This module provides a single
detector used by ``parse_merge_result`` and ``apply_with_snapshot`` to refuse
content that looks elided.

Design:
- Match the marker family produced by our own chunker AND common LLM
  hallucinations (HTML-style ``<... omitted ...>``, ``# ... (elided)``).
- Do NOT flag bare ``# ...`` (placeholder used legitimately in stub code or
  routine comments) — only flag when paired with the words "omitted" or
  "elided" so we never reject clean LLM output.
"""

from __future__ import annotations

import re

_ELISION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"#\s*\.\.\.\s*\(\s*\d+\s+sections?\s+omitted\s*\)", re.IGNORECASE),
    re.compile(r"//\s*\.\.\.\s*\(\s*\d+\s+sections?\s+omitted\s*\)", re.IGNORECASE),
    re.compile(
        r"/\*\s*\.\.\.\s*\(\s*\d+\s+sections?\s+omitted\s*\)\s*\*/", re.IGNORECASE
    ),
    re.compile(
        r"<\s*!--\s*\.\.\.\s*\(\s*\d+\s+sections?\s+omitted\s*\)\s*-->",
        re.IGNORECASE,
    ),
    re.compile(r"#\s*\.\.\.\s*\(\s*elided\s*\)", re.IGNORECASE),
    re.compile(r"//\s*\.\.\.\s*\(\s*elided\s*\)", re.IGNORECASE),
    re.compile(r"<\.\.\.\s*omitted\s*\.\.\.>", re.IGNORECASE),
    re.compile(
        r"<\s*\.\.\.\s*\d+\s+(?:lines|sections?)\s+omitted\s*\.\.\.>", re.IGNORECASE
    ),
)


def has_elision(content: str) -> tuple[bool, str | None]:
    """Return (hit, sample_line) if *content* contains an elision marker.

    The sample line is the matched text (without surrounding whitespace), used
    by callers to build a human-readable rationale. Returns ``(False, None)``
    on clean content or empty input.
    """
    if not content:
        return False, None
    for line in content.splitlines():
        for pattern in _ELISION_PATTERNS:
            if pattern.search(line):
                return True, line.strip()
    return False, None
