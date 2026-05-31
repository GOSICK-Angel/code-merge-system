"""P1-A: persistent soft-delete (suppress) across both stores + orchestrator固化.

The O-M6 harmful filter is read-time and tracker-dependent — a lost sidecar
resurrects pruned entries. P1-A persists the prune as an auditable
``suppressed`` flag so it survives tracker loss, and blocks suppressed entries
from injection AND consolidation.
"""

from __future__ import annotations

import pytest

from src.memory.models import MemoryEntry, MemoryEntryType
from src.memory.sqlite_store import SQLiteMemoryStore
from src.memory.store import MemoryStore, _consolidate_entries


def _entry(content: str, file_paths: list[str], confidence: float = 0.8) -> MemoryEntry:
    return MemoryEntry(
        entry_type=MemoryEntryType.DECISION,
        phase="conflict_analysis",
        content=content,
        file_paths=file_paths,
        confidence=confidence,
    )


# --- model ------------------------------------------------------------------


def test_suppressed_defaults_false_and_hash_unchanged():
    a = _entry("x", ["a.py"])
    # suppressing must not change dedup identity (content_hash excludes the flag)
    b = a.model_copy(update={"suppressed": True, "suppressed_reason": "harmful"})
    assert a.suppressed is False and a.suppressed_reason is None
    assert b.suppressed is True and b.suppressed_reason == "harmful"
    assert a.content_hash == b.content_hash


# --- MemoryStore ------------------------------------------------------------


def test_memstore_suppress_is_immutable_and_marks_flag():
    e = _entry("bad", ["a.py"])
    store = MemoryStore().add_entry(e)
    new = store.suppress_entry(e.entry_id, "stably harmful")
    assert new is not store  # new instance
    assert store.to_memory().entries[0].suppressed is False  # original untouched
    marked = new.to_memory().entries[0]
    assert marked.suppressed is True
    assert marked.suppressed_reason == "stably harmful"


def test_memstore_suppress_unknown_and_double_are_noops():
    e = _entry("bad", ["a.py"])
    store = MemoryStore().add_entry(e)
    assert store.suppress_entry("nope", "x") is store
    once = store.suppress_entry(e.entry_id, "r")
    assert once.suppress_entry(e.entry_id, "r2") is once  # already suppressed


def test_memstore_suppressed_excluded_from_relevant():
    e = _entry("bad", ["a.py"])
    store = MemoryStore().add_entry(e)
    assert store.get_relevant_context(["a.py"])  # visible before
    suppressed = store.suppress_entry(e.entry_id, "harmful")
    assert suppressed.get_relevant_context(["a.py"]) == []


# --- consolidation ----------------------------------------------------------


def test_consolidation_passes_suppressed_through_untouched():
    # 3 same-group live entries would merge; a suppressed sibling must survive
    # standalone (not merged, not dropped) to keep the audit trail.
    live = [_entry(f"c{i}", ["pkg/x/a.py"]) for i in range(3)]
    suppressed = _entry("harmful", ["pkg/x/a.py"]).model_copy(
        update={"suppressed": True, "suppressed_reason": "r"}
    )
    out = _consolidate_entries([*live, suppressed])
    surviving = [e for e in out if e.suppressed]
    assert len(surviving) == 1
    assert surviving[0].suppressed_reason == "r"
    # the 3 live ones collapsed into a single consolidated blob
    assert sum(1 for e in out if not e.suppressed) == 1


# --- SQLiteMemoryStore ------------------------------------------------------


def test_sqlite_suppress_persists_and_excludes(tmp_path):
    db = tmp_path / "m.db"
    store = SQLiteMemoryStore.open(db)
    e = _entry("bad", ["a.py"])
    store.add_entry(e)
    assert store.get_relevant_context(["a.py"])
    store.suppress_entry(e.entry_id, "harmful")
    assert store.get_relevant_context(["a.py"]) == []
    # reopen: flag persisted on disk
    reopened = SQLiteMemoryStore.open(db)
    row = next(x for x in reopened.to_memory().entries if x.entry_id == e.entry_id)
    assert row.suppressed is True and row.suppressed_reason == "harmful"


def test_sqlite_migration_adds_columns_to_legacy_db(tmp_path):
    """A pre-P1-A schema (no suppressed columns) must migrate on open without
    data loss, defaulting existing rows to suppressed=False."""
    import sqlite3

    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE memory_entries (
            entry_id TEXT PRIMARY KEY, entry_type TEXT NOT NULL, phase TEXT NOT NULL,
            content TEXT NOT NULL, file_paths TEXT NOT NULL, tags TEXT NOT NULL,
            confidence REAL NOT NULL, confidence_level TEXT NOT NULL,
            content_hash TEXT NOT NULL, created_at TEXT NOT NULL
        );
        INSERT INTO memory_entries VALUES
            ('id1','decision','planning','legacy','["a.py"]','[]',0.8,
             'inferred','hash1','2026-01-01T00:00:00');
        """
    )
    conn.commit()
    conn.close()

    store = SQLiteMemoryStore.open(db)  # migration runs here
    entries = store.to_memory().entries
    assert len(entries) == 1
    assert entries[0].suppressed is False
    # and suppression now works on the migrated row
    store.suppress_entry("id1", "harmful")
    assert store.get_relevant_context(["a.py"]) == []


def test_sqlite_suppress_unknown_is_noop(tmp_path):
    store = SQLiteMemoryStore.open(tmp_path / "m.db")
    e = _entry("ok", ["a.py"])
    store.add_entry(e)
    store.suppress_entry("nope", "x")  # must not raise
    assert store.get_relevant_context(["a.py"])  # untouched


# --- parity -----------------------------------------------------------------


def test_both_stores_agree_suppressed_is_hidden(tmp_path):
    e = _entry("bad", ["a.py"])
    mem = MemoryStore().add_entry(e).suppress_entry(e.entry_id, "r")
    sq = SQLiteMemoryStore.open(tmp_path / "m.db")
    sq.add_entry(e)
    sq.suppress_entry(e.entry_id, "r")
    assert mem.get_relevant_context(["a.py"]) == sq.get_relevant_context(["a.py"]) == []


# --- orchestrator固化 (_apply_suppress_harmful_entries) ----------------------


def _track_fails(tracker, entry_id: str, n: int) -> None:
    for i in range(n):
        f = f"{entry_id}-obs{i}"
        tracker.record_injection([f], [entry_id])
        tracker.record_outcome(f, success=False)


def _orch(persist: bool, min_obs: int = 3):
    from types import SimpleNamespace

    from src.core.orchestrator import Orchestrator
    from src.memory.hit_tracker import MemoryHitTracker
    from src.models.config import MemoryExtractionConfig

    orch = Orchestrator.__new__(Orchestrator)
    orch._memory_hit_tracker = MemoryHitTracker()
    orch._memory_store = MemoryStore()
    orch.config = SimpleNamespace(
        memory=MemoryExtractionConfig(
            persist_suppress=persist, suppress_min_observations=min_obs
        )
    )
    return orch


def test_persist_suppress_off_by_default():
    from src.models.config import MemoryExtractionConfig

    assert MemoryExtractionConfig().persist_suppress is False

    from types import SimpleNamespace

    orch = _orch(persist=False)
    e = _entry("harm", ["src/a.py"])
    orch._memory_store = orch._memory_store.add_entry(e)
    _track_fails(orch._memory_hit_tracker, e.entry_id, 3)
    orch._apply_suppress_harmful_entries(SimpleNamespace(file_decision_records={}))
    assert orch._memory_store.to_memory().entries[0].suppressed is False


def test_persist_suppress_marks_stable_harmful_skips_human_and_bootstrap():
    from types import SimpleNamespace

    from src.models.decision import DecisionSource

    orch = _orch(persist=True, min_obs=3)
    harmful = _entry("harm", ["src/a.py"])
    human = _entry("human", ["src/secret.py"])
    boot = _entry("boot", []).model_copy(update={"tags": ["bootstrap"]})
    store = orch._memory_store
    for e in (harmful, human, boot):
        store = store.add_entry(e)
        _track_fails(orch._memory_hit_tracker, e.entry_id, 6)  # >= min_fail_count
    orch._memory_store = store

    state = SimpleNamespace(
        judge_verdict=None,
        file_decision_records={
            "src/secret.py": SimpleNamespace(decision_source=DecisionSource.HUMAN)
        },
    )
    orch._apply_suppress_harmful_entries(state)

    by_id = {e.entry_id: e for e in orch._memory_store.to_memory().entries}
    assert by_id[harmful.entry_id].suppressed is True
    assert by_id[human.entry_id].suppressed is False  # human-decided exempt
    assert by_id[boot.entry_id].suppressed is False  # bootstrap exempt


def test_persist_suppress_respects_min_observations():
    from types import SimpleNamespace

    orch = _orch(persist=True, min_obs=3)
    e = _entry("harm", ["src/a.py"])
    orch._memory_store = orch._memory_store.add_entry(e)
    _track_fails(orch._memory_hit_tracker, e.entry_id, 2)  # below min_observations
    orch._apply_suppress_harmful_entries(
        SimpleNamespace(judge_verdict=None, file_decision_records={})
    )
    assert orch._memory_store.to_memory().entries[0].suppressed is False


# --- P1-A固化: stricter persistent-suppress criterion (PR-0d false-positive) -


def _track_mixed(tracker, entry_id: str, *, passes: int, fails: int) -> None:
    for i in range(passes):
        f = f"{entry_id}-p{i}"
        tracker.record_injection([f], [entry_id])
        tracker.record_outcome(f, success=True)
    for i in range(fails):
        f = f"{entry_id}-f{i}"
        tracker.record_injection([f], [entry_id])
        tracker.record_outcome(f, success=False)


def test_suppress_needs_min_fail_count():
    # score -1.0 but only 4 fails (< default 5) → too thin for a durable prune.
    from types import SimpleNamespace

    orch = _orch(persist=True, min_obs=3)
    e = _entry("harm", ["src/a.py"])
    orch._memory_store = orch._memory_store.add_entry(e)
    _track_fails(orch._memory_hit_tracker, e.entry_id, 4)
    orch._apply_suppress_harmful_entries(
        SimpleNamespace(judge_verdict=None, file_decision_records={})
    )
    assert orch._memory_store.to_memory().entries[0].suppressed is False


def test_suppress_needs_strict_threshold():
    # 3 pass / 7 fail → score -0.4, above the -0.8 persistent bar (would pass the
    # loose read-time -0.5 but not the durable suppress threshold).
    from types import SimpleNamespace

    orch = _orch(persist=True, min_obs=3)
    e = _entry("harm", ["src/a.py"])
    orch._memory_store = orch._memory_store.add_entry(e)
    _track_mixed(orch._memory_hit_tracker, e.entry_id, passes=3, fails=7)
    orch._apply_suppress_harmful_entries(
        SimpleNamespace(judge_verdict=None, file_decision_records={})
    )
    assert orch._memory_store.to_memory().entries[0].suppressed is False


def test_deterministic_confound_guard_skips_veto_only_entry():
    # The PR-0d case: entry tied ONLY to a file that failed via a deterministic
    # veto → its "harm" is correlational; persistent suppress must skip it.
    from types import SimpleNamespace

    from src.models.judge import IssueSeverity, JudgeIssue

    orch = _orch(persist=True, min_obs=3)
    e = _entry("harm", ["auth/oauth.go", "auth"])
    orch._memory_store = orch._memory_store.add_entry(e)
    _track_fails(orch._memory_hit_tracker, e.entry_id, 6)  # strongly harmful by ratio

    veto_issue = JudgeIssue(
        file_path="auth/oauth.go",
        issue_level=IssueSeverity.CRITICAL,
        issue_type="reverse_impact_unhandled",
        description="reverse impact",
        veto_condition="reverse impact unhandled",
    )
    verdict = SimpleNamespace(
        passed_files=[], failed_files=["auth/oauth.go"], issues=[veto_issue]
    )
    orch._apply_suppress_harmful_entries(
        SimpleNamespace(judge_verdict=verdict, file_decision_records={})
    )
    assert orch._memory_store.to_memory().entries[0].suppressed is False


def test_confound_guard_does_not_shield_entry_touching_passed_file():
    # An entry that also touches a PASSED file is not purely confounded — a
    # strongly-harmful ratio still suppresses it.
    from types import SimpleNamespace

    from src.models.judge import IssueSeverity, JudgeIssue

    orch = _orch(persist=True, min_obs=3)
    e = _entry("harm", ["auth/oauth.go", "auth/ok.go"])
    orch._memory_store = orch._memory_store.add_entry(e)
    _track_fails(orch._memory_hit_tracker, e.entry_id, 6)

    veto_issue = JudgeIssue(
        file_path="auth/oauth.go",
        issue_level=IssueSeverity.CRITICAL,
        issue_type="reverse_impact_unhandled",
        description="reverse impact",
        veto_condition="reverse impact unhandled",
    )
    verdict = SimpleNamespace(
        passed_files=["auth/ok.go"],
        failed_files=["auth/oauth.go"],
        issues=[veto_issue],
    )
    orch._apply_suppress_harmful_entries(
        SimpleNamespace(judge_verdict=verdict, file_decision_records={})
    )
    assert orch._memory_store.to_memory().entries[0].suppressed is True
