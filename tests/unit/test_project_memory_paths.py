"""M1+M2: project-level memory persistence path helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.cli.paths import (
    get_project_hit_stats_path,
    get_project_memory_db_path,
)


@pytest.fixture
def fake_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A tmp dir that is *not* the code-merge-system source tree (= prod mode)."""
    monkeypatch.delenv("MERGE_DEV", raising=False)
    return tmp_path


def test_memory_db_path_under_dot_merge_in_prod(fake_repo: Path) -> None:
    p = get_project_memory_db_path(str(fake_repo))
    assert p == fake_repo / ".merge" / "memory.db"


def test_hit_stats_path_under_dot_merge_in_prod(fake_repo: Path) -> None:
    p = get_project_hit_stats_path(str(fake_repo))
    assert p == fake_repo / ".merge" / "memory_hit_stats.json"


def test_paths_under_outputs_debug_in_dev(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MERGE_DEV", "1")
    db = get_project_memory_db_path(str(tmp_path))
    stats = get_project_hit_stats_path(str(tmp_path))
    assert db == tmp_path / "outputs" / "debug" / "memory.db"
    assert stats == tmp_path / "outputs" / "debug" / "memory_hit_stats.json"


def test_two_runs_share_same_paths(fake_repo: Path) -> None:
    """Cornerstone of M2: two consecutive runs must resolve to one db file."""
    db1 = get_project_memory_db_path(str(fake_repo))
    db2 = get_project_memory_db_path(str(fake_repo))
    assert db1 == db2

    stats1 = get_project_hit_stats_path(str(fake_repo))
    stats2 = get_project_hit_stats_path(str(fake_repo))
    assert stats1 == stats2


def test_hit_tracker_persists_across_instances(fake_repo: Path) -> None:
    """Cornerstone of M1: tracker writes/reads same sidecar across instances."""
    from src.memory.hit_tracker import MemoryHitTracker

    sidecar_dir = fake_repo / ".merge"
    sidecar_dir.mkdir()
    sidecar = get_project_hit_stats_path(str(fake_repo))

    tracker_run1 = MemoryHitTracker(persist_path=sidecar)
    tracker_run1.record_call(
        "planning", {"l0": 1, "l1_patterns": 2, "l1_decisions": 0, "l2": 0}
    )
    assert sidecar.exists()

    tracker_run2 = MemoryHitTracker(persist_path=sidecar)
    summary = tracker_run2.summary()
    assert summary["total_calls"] == 1
    assert summary["hit_calls"] == 1

    tracker_run2.record_call(
        "auto_merge", {"l0": 0, "l1_patterns": 0, "l1_decisions": 0, "l2": 4}
    )
    summary2 = tracker_run2.summary()
    assert summary2["total_calls"] == 2
    assert summary2["by_phase"]["planning"]["calls"] == 1
    assert summary2["by_phase"]["auto_merge"]["calls"] == 1


def test_memory_db_accumulates_entries_across_runs(fake_repo: Path) -> None:
    """Cornerstone of M2: SQLite store at project-level db accumulates entries."""
    from src.memory.models import MemoryEntry, MemoryEntryType
    from src.memory.sqlite_store import SQLiteMemoryStore

    (fake_repo / ".merge").mkdir()
    db = get_project_memory_db_path(str(fake_repo))

    store_run1 = SQLiteMemoryStore.open(db)
    store_run1.add_entry(
        MemoryEntry(
            entry_type=MemoryEntryType.PATTERN,
            phase="planning",
            content="run1 entry",
            file_paths=["a.py"],
            tags=["run1"],
            confidence=0.8,
        )
    )
    assert store_run1.entry_count == 1

    store_run2 = SQLiteMemoryStore.open(db)
    assert store_run2.entry_count == 1, "second run must see the first run's entry"
    store_run2.add_entry(
        MemoryEntry(
            entry_type=MemoryEntryType.PATTERN,
            phase="planning",
            content="run2 entry",
            file_paths=["b.py"],
            tags=["run2"],
            confidence=0.8,
        )
    )
    assert store_run2.entry_count == 2

    contents = sorted(e.content for e in store_run2.query_by_path("a.py", limit=10))
    assert "run1 entry" in contents
