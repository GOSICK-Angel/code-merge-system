"""P2-A: high-information entry enforcement.

The dual of ``_is_epistemically_empty`` (which rejects "model gave up" markers):
this rejects entries that are *vacuous* — they name a file but carry no concrete
action, decision, or fix. Renze & Guven (arXiv 2405.06682) show reflection
*information content* is what drives the effect (GPT-4 0.79 → 0.93), so a memory
that says nothing specific is dead weight that dilutes retrieval.

Conservative by design: defaults to actionable (True) and only flags content that
is clearly filler, so it never silently drops a legitimate entry. Non-actionable
entries are *de-ranked* (confidence + level lowered), not deleted, preserving
recall while pushing vacuous entries below the retrieval threshold.
"""

from __future__ import annotations

import re

from src.memory.models import ConfidenceLevel, MemoryEntry, MemoryEntryType

# Entry types whose value is their specific action/decision/fix. PATTERN /
# PHASE_SUMMARY / CODEBASE_INSIGHT are intentionally exempt — a terse pattern
# label ("recurring reverse_impact") is legitimately short.
_ACTIONABLE_TYPES = frozenset(
    {
        MemoryEntryType.DECISION,
        MemoryEntryType.REPAIR_RECIPE,
    }
)

# Filler that carries no information once the file path is stripped.
_VACUOUS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(decision\s+made|reviewed|processed|handled|done|ok|n/?a|none)\.?$"),
    re.compile(r"^(no\s+(notes?|details?|specifics?|action|change)s?)\b"),
    re.compile(r"^(tbd|todo|unknown|see\s+above|as\s+noted)\.?$"),
)

_MIN_SUBSTANCE_CHARS = 8


def _substance(content: str) -> str:
    """The part after a leading ``path: `` prefix, lowercased + stripped."""
    head, sep, tail = content.partition(": ")
    body = tail if sep else content
    return body.strip().lower()


def is_actionable_content(content: str, entry_type: MemoryEntryType) -> bool:
    """True when ``content`` carries a concrete action/decision/fix.

    Only entry types in ``_ACTIONABLE_TYPES`` are scrutinised; all others are
    considered actionable by default. The check is deliberately permissive."""
    if entry_type not in _ACTIONABLE_TYPES:
        return True
    body = _substance(content)
    if len(body) < _MIN_SUBSTANCE_CHARS:
        return False
    return not any(pat.match(body) for pat in _VACUOUS_PATTERNS)


def enforce_actionable(entry: MemoryEntry) -> MemoryEntry:
    """Return ``entry`` unchanged when actionable, else a de-ranked copy.

    De-rank = clamp confidence_level to HEURISTIC and halve confidence (floor
    0.1) so the vacuous entry sinks below the retrieval relevance threshold
    without being deleted. Immutable — never mutates the input."""
    if is_actionable_content(entry.content, entry.entry_type):
        return entry
    if entry.confidence_level == ConfidenceLevel.HEURISTIC and entry.confidence <= 0.1:
        return entry
    return entry.model_copy(
        update={
            "confidence_level": ConfidenceLevel.HEURISTIC,
            "confidence": max(0.1, round(entry.confidence * 0.5, 4)),
        }
    )
