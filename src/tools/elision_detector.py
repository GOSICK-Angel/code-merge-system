"""P0-2 / P0-bis: merge-output quality gate.

When the chunker stages a large file for the LLM (see ``src/llm/chunker.py``
``render_file_staged``), it injects markers like::

    # ... (3 sections omitted)

LLMs sometimes echo these markers back into their merge output instead of
producing a complete file. Writing such truncated content to disk silently
deletes hundreds of lines of fork code. This module provides the detectors
used by ``parse_merge_result`` and ``apply_with_snapshot`` to refuse content
that looks elided, prose-prefixed, or hard-truncated.

Three detection families live here, each independently testable:

1. ``has_elision``   — explicit elision markers ("# ... (3 sections omitted)").
2. ``has_prose_preamble`` — chain-of-thought leak ("Looking at the current
   content, I'll merge them...") that ends up at the top of a code file
   when the LLM ignored the prompt's "return ONLY the merged file content"
   instruction.
3. ``looks_truncated`` — output ends in the middle of a token / statement
   (heuristic, paired with a length sanity check against the inputs).

Design:
- Only flag patterns that are highly LLM-specific. Production code can
  legitimately start with comments or open with strings; the detectors
  here look for narrative phrases that *no real source file* would use,
  paired with a "code-fence absent" guard so we don't flag a clean
  fenced block whose contents start with a string literal.
- The truncation detector returns ``(False, None)`` whenever the input
  and merged sizes don't both look small enough to be suspicious; this
  keeps false positives away from clean LLM output that legitimately
  ends with a closing brace on the last meaningful line.
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


# Narrative openers that strongly suggest a chain-of-thought leak rather
# than a real source file. Matched case-insensitively against the first
# non-empty line, anchored to start-of-line so a doc comment that merely
# *mentions* "looking at this" elsewhere isn't flagged.
#
# The patterns target phrases an LLM uses to address the user, never
# phrases a programmer would write at the top of a file. Tested against
# Go, Python, JS, TS, HTML, YAML, Markdown — none have legitimate
# top-of-file lines matching these.
_PROSE_PREAMBLE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"^\s*looking at (?:the |this |that )?(?:current |existing )?", re.IGNORECASE
    ),
    re.compile(
        r"^\s*here(?:'s| is| are)(?: the)? (?:merged|merge|combined|the)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*let me (?:merge|combine|reconcile|review|analyze)\b", re.IGNORECASE
    ),
    re.compile(
        r"^\s*i(?:'ll| will| have| am going to)\s+(?:merge|merged|combine|combined|reconcile|reconciled|produce|produced|generate|generated|output|write)",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*sure[,.!]?\s+here", re.IGNORECASE),
    re.compile(r"^\s*okay[,.!]?\s+(?:here|let)", re.IGNORECASE),
    re.compile(
        r"^\s*based on (?:the |my |this )?(?:analysis|comparison|diff|conflict)",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*after (?:analyzing|comparing|reviewing|examining) (?:the |both )",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*the merged (?:file|content|version|result)\b", re.IGNORECASE),
    re.compile(r"^\s*to merge (?:these|both|the two)\b", re.IGNORECASE),
)


# A code fence opener at the very start means the LLM correctly wrapped
# its output; the fence stripper in parse_merge_result will handle it.
# We only run prose detection on un-fenced output.
_FENCE_START = re.compile(r"^\s*```")


def has_prose_preamble(content: str) -> tuple[bool, str | None]:
    """Detect chain-of-thought / narrative preamble at the start of merged output.

    Returns ``(True, offending_line)`` when the first non-empty line
    looks like an LLM addressing the user. Returns ``(False, None)``
    when content is empty, fenced (caller will unfence first), or
    starts with a plausible source-file line.

    Tuned to be conservative — only matches patterns that are exclusively
    conversational and would never appear at the top of legitimate code.
    """
    if not content:
        return False, None
    if _FENCE_START.match(content):
        # Fence-opened; defer to the fence stripper. Prose between
        # fence and content is a different bug we don't try to catch
        # here (and it's extremely rare in practice).
        return False, None
    for line in content.splitlines():
        if not line.strip():
            continue
        for pattern in _PROSE_PREAMBLE_PATTERNS:
            if pattern.match(line):
                return True, line.strip()[:160]
        # Only consider the first non-empty line — preamble at the
        # bottom or middle is something else entirely.
        return False, None
    return False, None


# Tokens we expect at the end of legitimately-complete source / config
# files. The set is deliberately broad: closing braces / brackets /
# parens for C-family + Lisp, end-of-block keywords for Ruby/Python,
# template terminators, and ``;`` for SQL / one-liner statements.
# An EOF inside a string literal would also be suspicious, but we
# can't catch that without a parser — the length sanity check below
# is the second line of defence.
# Below this smaller-input size (chars) we don't run the length-shortfall
# truncation check — a legitimately short merge of two short files would
# otherwise misfire (e.g. a 50-char file merging to 28 chars).
_MIN_SIZE_FOR_TRUNCATION_CHECK = 200

_HEALTHY_ENDINGS = (
    "}",
    "]",
    ")",
    ";",
    ">",
    "*/",
    "-->",
    "%}",
    "%}",
    "end",
    "fi",
    "done",
    "esac",
)


def looks_truncated(
    content: str,
    *,
    current_size: int | None = None,
    target_size: int | None = None,
) -> tuple[bool, str | None]:
    """Heuristic detector for ``finish_reason=length``-style truncation.

    Two-part check, both must fire to flag:

    1. **Tail looks unfinished**: last non-empty line does not end in any
       of ``_HEALTHY_ENDINGS`` and is not a comment / pure whitespace.
       This rules out files that legitimately end with a single brace.
    2. **Length is suspiciously short**: the merged content is < 60% of
       ``min(current_size, target_size)``. Without the length guard a
       file ending in an unusual character (an ``@`` in a Jinja2
       template tail, say) would be flagged on every merge.

    Both ``current_size`` and ``target_size`` are optional — when either
    is missing the length guard is skipped and we fall back to "tail
    looks unfinished" only when it ends mid-identifier (no whitespace,
    no terminator, no symbol at all on the last line).

    Returns ``(True, tail_excerpt)`` when the heuristic fires, else
    ``(False, None)``.
    """
    if not content:
        return False, None

    lines = content.splitlines()
    # Walk back to the last non-empty line — trailing whitespace is fine.
    tail = ""
    for line in reversed(lines):
        if line.strip():
            tail = line.rstrip()
            break
    if not tail:
        return False, None

    tail_stripped = tail.rstrip()
    # A line that ends with whitespace was already stripped above.
    ends_healthy = any(tail_stripped.endswith(suffix) for suffix in _HEALTHY_ENDINGS)

    # Length guard (independent of the tail) — fire when the merged output is
    # dramatically shorter than both inputs, EVEN IF the tail ends on a healthy
    # token. This is the key fix for clean mid-file elision: an LLM that drops a
    # function but closes the file with ``}`` was previously waved through by the
    # ``ends_healthy`` short-circuit regardless of how much it deleted (a 4000→
    # 750 char drop passed clean). A real merge recombines existing lines, so a
    # &lt;60%-of-the-smaller-input result is a strong drop/truncation signal. We
    # still REFUSE to run without size hints (no principled baseline) and guard
    # tiny files (a legitimately short merge of two short files must not misfire).
    if current_size is not None and target_size is not None:
        smaller = min(current_size, target_size)
        if smaller > _MIN_SIZE_FOR_TRUNCATION_CHECK:
            floor = int(smaller * 0.6)
            if floor > 0 and len(content) < floor:
                return True, tail_stripped[-160:]

    # No dramatic shortfall (or no size hints): a healthy tail is clean.
    if ends_healthy:
        return False, None

    # Unhealthy tail without a dramatic shortfall is left alone — the
    # fallback-only "tail looks unfinished" heuristic produced unacceptable
    # false positives on legitimate one-line files / templates ending on a
    # non-bracket token, so we require the length signal above to flag.
    return False, None
