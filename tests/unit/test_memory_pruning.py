"""M6: harmful entry auto-pruning at L2 injection time."""

from __future__ import annotations

from src.memory.hit_tracker import MemoryHitTracker
from src.memory.layered_loader import LayeredMemoryLoader
from src.memory.models import MemoryEntry, MemoryEntryType
from src.memory.store import MemoryStore


# ---------------------------------------------------------------------------
# Tracker-level: harmful_entry_ids() threshold semantics
# ---------------------------------------------------------------------------


def test_harmful_requires_min_observations() -> None:
    tracker = MemoryHitTracker()
    tracker.record_injection(["a.py"], ["entry-1"])
    tracker.record_outcome("a.py", success=False)
    assert tracker.harmful_entry_ids() == frozenset()


def test_harmful_marks_entry_below_threshold() -> None:
    tracker = MemoryHitTracker()
    tracker.record_injection(["a.py"], ["bad"])
    tracker.record_outcome("a.py", success=False)
    tracker.record_outcome("a.py", success=False)
    assert "bad" in tracker.harmful_entry_ids()


def test_harmful_excludes_helpful_entry() -> None:
    tracker = MemoryHitTracker()
    tracker.record_injection(["a.py"], ["good"])
    tracker.record_outcome("a.py", success=True)
    tracker.record_outcome("a.py", success=True)
    assert tracker.harmful_entry_ids() == frozenset()


def test_harmful_excludes_borderline_entry() -> None:
    """1 pass + 2 fails -> score -1/3, above default -0.5 threshold."""
    tracker = MemoryHitTracker()
    tracker.record_injection(["a.py"], ["meh"])
    tracker.record_outcome("a.py", success=True)
    tracker.record_outcome("a.py", success=False)
    tracker.record_outcome("a.py", success=False)
    assert "meh" not in tracker.harmful_entry_ids()


def test_harmful_threshold_is_inclusive() -> None:
    tracker = MemoryHitTracker()
    tracker.record_injection(["a.py"], ["edge"])
    tracker.record_outcome("a.py", success=True)
    tracker.record_outcome("a.py", success=False)
    tracker.record_outcome("a.py", success=False)
    tracker.record_outcome("a.py", success=False)
    assert "edge" in tracker.harmful_entry_ids(threshold=-0.5)


def test_harmful_custom_threshold() -> None:
    tracker = MemoryHitTracker()
    tracker.record_injection(["a.py"], ["mild-bad"])
    tracker.record_outcome("a.py", success=True)
    tracker.record_outcome("a.py", success=False)
    tracker.record_outcome("a.py", success=False)
    assert "mild-bad" not in tracker.harmful_entry_ids(threshold=-0.5)
    assert "mild-bad" in tracker.harmful_entry_ids(threshold=-0.2)


# ---------------------------------------------------------------------------
# Loader-level: harmful entries are skipped at L2 injection
# ---------------------------------------------------------------------------


def _build_store_with_two_entries() -> MemoryStore:
    store = MemoryStore()
    e_good = MemoryEntry(
        entry_id="good",
        entry_type=MemoryEntryType.PATTERN,
        phase="auto_merge",
        content="GOOD entry",
        file_paths=["models/x/llm.py"],
        tags=["t1"],
        confidence=0.9,
    )
    e_bad = MemoryEntry(
        entry_id="bad",
        entry_type=MemoryEntryType.PATTERN,
        phase="auto_merge",
        content="BAD entry",
        file_paths=["models/x/llm.py"],
        tags=["t1"],
        confidence=0.9,
    )
    return store.add_entry(e_good).add_entry(e_bad)


def test_loader_skips_harmful_entry() -> None:
    store = _build_store_with_two_entries()
    tracker = MemoryHitTracker()
    tracker.record_injection(["models/x/llm.py"], ["bad"])
    tracker.record_outcome("models/x/llm.py", success=False)
    tracker.record_outcome("models/x/llm.py", success=False)

    loader = LayeredMemoryLoader(store, tracker)
    text = loader.load_for_agent("auto_merge", file_paths=["models/x/llm.py"])
    assert "GOOD entry" in text
    assert "BAD entry" not in text


def test_loader_keeps_entries_when_no_harmful_history() -> None:
    store = _build_store_with_two_entries()
    tracker = MemoryHitTracker()
    loader = LayeredMemoryLoader(store, tracker)

    text = loader.load_for_agent("auto_merge", file_paths=["models/x/llm.py"])
    assert "GOOD entry" in text
    assert "BAD entry" in text


def test_loader_without_tracker_does_not_filter() -> None:
    store = _build_store_with_two_entries()
    loader = LayeredMemoryLoader(store)
    text = loader.load_for_agent("auto_merge", file_paths=["models/x/llm.py"])
    assert "GOOD entry" in text
    assert "BAD entry" in text
