"""P2-A: high-information entry enforcement (dual of epistemic-empty filter)."""

from __future__ import annotations

from src.memory.content_quality import enforce_actionable, is_actionable_content
from src.memory.models import ConfidenceLevel, MemoryEntry, MemoryEntryType


def _entry(
    content: str,
    entry_type: MemoryEntryType = MemoryEntryType.DECISION,
    confidence: float = 0.85,
    level: ConfidenceLevel = ConfidenceLevel.EXTRACTED,
) -> MemoryEntry:
    return MemoryEntry(
        entry_type=entry_type,
        phase="conflict_analysis",
        content=content,
        confidence=confidence,
        confidence_level=level,
    )


# --- is_actionable_content --------------------------------------------------


def test_decision_with_concrete_action_is_actionable():
    assert is_actionable_content(
        "src/a.py: take_target [import_conflict] confidence=0.90 — keep upstream auth",
        MemoryEntryType.DECISION,
    )


def test_decision_vacuous_filler_not_actionable():
    for body in ("src/a.py: decision made", "src/a.py: n/a", "src/a.py: no notes"):
        assert not is_actionable_content(body, MemoryEntryType.DECISION)


def test_decision_too_short_not_actionable():
    assert not is_actionable_content("src/a.py: ok", MemoryEntryType.DECISION)


def test_pattern_type_is_exempt():
    # a terse PATTERN label is legitimately short — never flagged
    assert is_actionable_content("ok", MemoryEntryType.PATTERN)
    assert is_actionable_content(
        "recurring reverse_impact", MemoryEntryType.PHASE_SUMMARY
    )


def test_repair_recipe_scrutinised():
    assert not is_actionable_content("x: n/a", MemoryEntryType.REPAIR_RECIPE)
    assert is_actionable_content(
        "dup_symbol in pkg/x: resolved by dedup, verified by judge PASS",
        MemoryEntryType.REPAIR_RECIPE,
    )


# --- enforce_actionable -----------------------------------------------------


def test_actionable_entry_returned_unchanged():
    e = _entry("src/a.py: semantic_merge — merged both auth handlers cleanly")
    assert enforce_actionable(e) is e


def test_vacuous_entry_is_deranked_not_dropped():
    e = _entry("src/a.py: decision made", confidence=0.85)
    out = enforce_actionable(e)
    assert out is not e
    assert out.confidence_level == ConfidenceLevel.HEURISTIC
    assert out.confidence == 0.425  # 0.85 * 0.5
    assert out.content == e.content  # content preserved, only rank lowered
    assert out.content_hash == e.content_hash  # identity stable


def test_derank_is_idempotent_at_floor():
    e = _entry("src/a.py: n/a", confidence=0.1, level=ConfidenceLevel.HEURISTIC)
    assert enforce_actionable(e) is e  # already at floor → no new object


def test_exempt_type_never_deranked():
    e = _entry("ok", entry_type=MemoryEntryType.PATTERN, confidence=0.8)
    assert enforce_actionable(e) is e
