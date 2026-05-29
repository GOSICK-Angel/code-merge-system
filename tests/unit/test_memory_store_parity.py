"""Parity between MemoryStore and SQLiteMemoryStore retrieval ranking.

OPP-1: both stores must rank ``get_relevant_context`` identically. The
in-memory store blended path-Jaccard into its score while the production
SQLite store did not, so sibling-directory recall silently degraded on every
real run. These tests pin the two stores to the same shared
``score_path_overlap`` and fail if they ever drift again.
"""

from __future__ import annotations

from src.memory.models import MemoryEntry, MemoryEntryType
from src.memory.sqlite_store import SQLiteMemoryStore
from src.memory.store import MemoryStore, score_path_overlap


def _make_entry(content: str, file_paths: list[str], confidence: float) -> MemoryEntry:
    return MemoryEntry(
        entry_type=MemoryEntryType.DECISION,
        phase="conflict_analysis",
        content=content,
        file_paths=file_paths,
        confidence=confidence,
    )


def _ranked_ids(store: object, query: list[str]) -> list[str]:
    entries = store.get_relevant_context(query, max_entries=10)  # type: ignore[attr-defined]
    return [e.entry_id for e in entries]


def test_sibling_path_jaccard_flips_ranking(tmp_path):
    """A sibling-directory entry must out-rank an unrelated higher-confidence
    one once Jaccard is honoured — and both stores must agree."""
    sibling = _make_entry(
        "runtime decision", ["pkg/plugin_runtime/runtime.go"], confidence=0.5
    )
    unrelated = _make_entry(
        "unrelated decision", ["unrelated/other/thing.py"], confidence=0.7
    )
    query = ["pkg/plugin_manager/manager.go"]

    mem = MemoryStore().add_entry(sibling).add_entry(unrelated)
    sql = SQLiteMemoryStore.open(tmp_path / "mem.db")
    sql.add_entry(sibling)
    sql.add_entry(unrelated)

    mem_ranked = _ranked_ids(mem, query)
    sql_ranked = _ranked_ids(sql, query)

    assert mem_ranked == sql_ranked
    assert mem_ranked[0] == sibling.entry_id


def test_store_ranking_parity_mixed_entries(tmp_path):
    entries = [
        _make_entry("exact", ["src/auth/handler.py"], confidence=0.6),
        _make_entry("prefix", ["src/auth"], confidence=0.55),
        _make_entry("sibling", ["src/authz/guard.py"], confidence=0.8),
        _make_entry("global", [], confidence=0.9),
        _make_entry("far", ["docs/readme.md"], confidence=0.95),
    ]
    query = ["src/auth/handler.py"]

    mem = MemoryStore()
    sql = SQLiteMemoryStore.open(tmp_path / "mem.db")
    for e in entries:
        mem = mem.add_entry(e)
        sql.add_entry(e)

    assert _ranked_ids(mem, query) == _ranked_ids(sql, query)


def test_score_path_overlap_signals():
    # exact match
    assert score_path_overlap(["a/b.py"], ["a/b.py"]) == 1.0
    # no file paths -> small floor so global insights still surface
    assert score_path_overlap(["a/b.py"], []) == 0.1
    # sibling via Jaccard but no common prefix -> positive, below exact
    sibling = score_path_overlap(
        ["pkg/plugin_manager/manager.go"], ["pkg/plugin_runtime/runtime.go"]
    )
    assert 0.0 < sibling < 1.0
    # genuinely unrelated -> zero
    assert score_path_overlap(["pkg/a/x.go"], ["zzz/q/y.rb"]) == 0.0
