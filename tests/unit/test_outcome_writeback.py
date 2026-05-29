"""OPP-5: outcome feedback write-back into persisted memory confidence.

The MemoryHitTracker accumulated per-entry pass/fail across runs but the only
consumer was a binary suppress of harmful entries at L2. This closes the loop:
when enabled (default OFF), the orchestrator nudges each tracked entry's
persisted confidence toward its outcome score so helpful entries rise and
harmful ones fall — while never touching human-decided or bootstrap
(human-authored) entries.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.core.orchestrator import Orchestrator
from src.memory.hit_tracker import MemoryHitTracker
from src.memory.models import MemoryEntry, MemoryEntryType
from src.memory.sqlite_store import SQLiteMemoryStore
from src.memory.store import MemoryStore
from src.models.config import MemoryExtractionConfig
from src.models.decision import DecisionSource


def _entry(
    content: str,
    *,
    file_paths: list[str] | None = None,
    confidence: float = 0.5,
    tags: list[str] | None = None,
) -> MemoryEntry:
    return MemoryEntry(
        entry_type=MemoryEntryType.DECISION,
        phase="conflict_analysis",
        content=content,
        file_paths=file_paths or [],
        confidence=confidence,
        tags=tags or [],
    )


def _track_passes(tracker: MemoryHitTracker, entry_id: str, n: int) -> None:
    for i in range(n):
        f = f"{entry_id}-obs{i}"
        tracker.record_injection([f], [entry_id])
        tracker.record_outcome(f, success=True)


# --- hit_tracker.outcome_scores -------------------------------------------


def test_outcome_scores_requires_min_observations():
    tracker = MemoryHitTracker()
    _track_passes(tracker, "e1", 2)
    assert tracker.outcome_scores(min_observations=3) == {}
    _track_passes(tracker, "e1", 1)  # now 3 total
    scores = tracker.outcome_scores(min_observations=3)
    assert scores["e1"] == 1.0


def test_outcome_scores_negative_for_failures():
    tracker = MemoryHitTracker()
    for i in range(3):
        f = f"bad-{i}"
        tracker.record_injection([f], ["bad"])
        tracker.record_outcome(f, success=False)
    assert tracker.outcome_scores(min_observations=3)["bad"] == -1.0


# --- store.adjust_confidence (both stores) --------------------------------


def test_adjust_confidence_clamps_and_preserves_hash():
    e = _entry("x", confidence=0.95)
    store = MemoryStore().add_entry(e).adjust_confidence({e.entry_id: 0.1})
    out = store.to_memory().entries[0]
    assert out.confidence == 0.98  # 1.05 clamped
    assert out.content_hash == e.content_hash  # identity preserved


def test_adjust_confidence_floor():
    e = _entry("y", confidence=0.1)
    store = MemoryStore().add_entry(e).adjust_confidence({e.entry_id: -0.5})
    assert store.to_memory().entries[0].confidence == 0.05


def test_sqlite_adjust_confidence(tmp_path):
    e = _entry("z", confidence=0.5)
    store = SQLiteMemoryStore.open(tmp_path / "m.db")
    store.add_entry(e)
    store.adjust_confidence({e.entry_id: 0.2})
    out = store.to_memory().entries[0]
    assert abs(out.confidence - 0.7) < 1e-9


# --- orchestrator write-back ----------------------------------------------


def _orch(writeback: bool, k: float = 0.1) -> Orchestrator:
    orch = Orchestrator.__new__(Orchestrator)
    orch._memory_hit_tracker = MemoryHitTracker()
    orch._memory_store = MemoryStore()
    orch.config = SimpleNamespace(
        memory=MemoryExtractionConfig(
            outcome_confidence_writeback=writeback,
            outcome_writeback_k=k,
            outcome_writeback_min_observations=3,
        )
    )
    return orch


def test_writeback_off_by_default():
    cfg = MemoryExtractionConfig()
    assert cfg.outcome_confidence_writeback is False

    orch = _orch(writeback=False)
    e = _entry("p", file_paths=["src/a.py"], confidence=0.5)
    orch._memory_store = orch._memory_store.add_entry(e)
    _track_passes(orch._memory_hit_tracker, e.entry_id, 3)
    state = SimpleNamespace(file_decision_records={})

    orch._apply_outcome_confidence_writeback(state)
    assert orch._memory_store.to_memory().entries[0].confidence == 0.5


def test_writeback_boosts_helpful_skips_human_and_bootstrap():
    orch = _orch(writeback=True, k=0.1)
    helpful = _entry("help", file_paths=["src/a.py"], confidence=0.5)
    human = _entry("human", file_paths=["src/secret.py"], confidence=0.5)
    boot = _entry("boot", file_paths=[], confidence=0.5, tags=["bootstrap"])
    store = orch._memory_store
    for e in (helpful, human, boot):
        store = store.add_entry(e)
        _track_passes(orch._memory_hit_tracker, e.entry_id, 3)
    orch._memory_store = store

    state = SimpleNamespace(
        file_decision_records={
            "src/secret.py": SimpleNamespace(decision_source=DecisionSource.HUMAN)
        }
    )
    orch._apply_outcome_confidence_writeback(state)

    by_id = {e.entry_id: e for e in orch._memory_store.to_memory().entries}
    assert by_id[helpful.entry_id].confidence == 0.6  # 0.5 + 0.1*1.0
    assert by_id[human.entry_id].confidence == 0.5  # human-decided: untouched
    assert by_id[boot.entry_id].confidence == 0.5  # bootstrap: untouched
