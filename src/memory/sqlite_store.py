from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from src.memory.models import (
    ConfidenceLevel,
    MemoryEntry,
    MemoryEntryType,
    MergeMemory,
    PhaseSummary,
)
from src.memory.store import CONSOLIDATION_THRESHOLD, MAX_ENTRIES, _consolidate_entries

logger = logging.getLogger(__name__)

_CREATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_entries (
    entry_id          TEXT PRIMARY KEY,
    entry_type        TEXT NOT NULL,
    phase             TEXT NOT NULL,
    content           TEXT NOT NULL,
    file_paths        TEXT NOT NULL,
    tags              TEXT NOT NULL,
    confidence        REAL NOT NULL,
    confidence_level  TEXT NOT NULL,
    content_hash      TEXT NOT NULL,
    created_at        TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_content_hash
    ON memory_entries (content_hash);
CREATE TABLE IF NOT EXISTS phase_summaries (
    phase TEXT PRIMARY KEY,
    data  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv_store (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_INSERT_ENTRY = """
INSERT OR IGNORE INTO memory_entries
    (entry_id, entry_type, phase, content, file_paths, tags,
     confidence, confidence_level, content_hash, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_PHASE_ORDER = {
    "planning": 0,
    "auto_merge": 1,
    "conflict_analysis": 2,
    "judge_review": 3,
}


def _entry_to_row(entry: MemoryEntry) -> tuple[str, ...]:
    return (
        entry.entry_id,
        entry.entry_type.value,
        entry.phase,
        entry.content,
        json.dumps(list(entry.file_paths)),
        json.dumps(list(entry.tags)),
        str(entry.confidence),
        entry.confidence_level.value,
        entry.content_hash,
        entry.created_at.isoformat(),
    )


def _row_to_entry(row: sqlite3.Row) -> MemoryEntry:
    return MemoryEntry(
        entry_id=row["entry_id"],
        entry_type=MemoryEntryType(row["entry_type"]),
        phase=row["phase"],
        content=row["content"],
        file_paths=json.loads(row["file_paths"]),
        tags=json.loads(row["tags"]),
        confidence=float(row["confidence"]),
        confidence_level=ConfidenceLevel(row["confidence_level"]),
        content_hash=row["content_hash"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


class SQLiteMemoryStore:
    """Concurrent-safe memory store backed by SQLite in WAL mode.

    Implements the same public API as MemoryStore but is mutable —
    methods return ``self`` rather than new instances. Multiple processes
    sharing the same ``db_path`` can read/write safely; WAL mode allows
    concurrent readers and serialises writers automatically.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._init_db()

    @classmethod
    def open(cls, db_path: Path) -> "SQLiteMemoryStore":
        return cls(db_path)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        # Multiple concurrent SQLiteMemoryStore.open(db_path) callers race to
        # set journal_mode=WAL — the PRAGMA itself needs an exclusive write
        # lock, and sqlite3's `timeout=` does not cover it. Retry a handful of
        # times before giving up.
        import time

        last_exc: sqlite3.OperationalError | None = None
        for attempt in range(10):
            conn = sqlite3.connect(str(self._db_path), timeout=5.0)
            try:
                conn.execute("PRAGMA busy_timeout=5000")
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript(_CREATE_SCHEMA)
                return
            except sqlite3.OperationalError as exc:
                last_exc = exc
                time.sleep(0.05 * (attempt + 1))
            finally:
                conn.close()
        assert last_exc is not None
        raise last_exc

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self._db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _maybe_consolidate(self, conn: sqlite3.Connection) -> None:
        count: int = conn.execute("SELECT COUNT(*) FROM memory_entries").fetchone()[0]
        if count <= CONSOLIDATION_THRESHOLD:
            return
        rows = conn.execute("SELECT * FROM memory_entries").fetchall()
        entries = [_row_to_entry(r) for r in rows]
        consolidated = _consolidate_entries(entries)
        conn.execute("DELETE FROM memory_entries")
        for entry in consolidated:
            conn.execute(_INSERT_ENTRY, _entry_to_row(entry))
        logger.debug(
            "SQLiteMemoryStore consolidated: %d → %d entries", count, len(consolidated)
        )

    # ------------------------------------------------------------------
    # Write API
    # ------------------------------------------------------------------

    def add_entry(self, entry: MemoryEntry) -> "SQLiteMemoryStore":
        with self._conn() as conn:
            conn.execute(_INSERT_ENTRY, _entry_to_row(entry))
            self._maybe_consolidate(conn)
            count: int = conn.execute("SELECT COUNT(*) FROM memory_entries").fetchone()[
                0
            ]
            if count > MAX_ENTRIES:
                conn.execute(
                    """DELETE FROM memory_entries WHERE entry_id IN (
                        SELECT entry_id FROM memory_entries
                        ORDER BY confidence ASC, created_at ASC
                        LIMIT ?
                    )""",
                    (count - MAX_ENTRIES,),
                )
        return self

    def record_phase_summary(self, summary: PhaseSummary) -> "SQLiteMemoryStore":
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO phase_summaries (phase, data) VALUES (?, ?)",
                (summary.phase, summary.model_dump_json()),
            )
        return self

    def set_codebase_profile(self, key: str, value: str) -> "SQLiteMemoryStore":
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)",
                (key, value),
            )
        return self

    def import_from_memory(self, memory: MergeMemory) -> None:
        """Bulk-import a MergeMemory snapshot (INSERT OR IGNORE for dedup)."""
        for entry in memory.entries:
            self.add_entry(entry)
        for summary in memory.phase_summaries.values():
            self.record_phase_summary(summary)
        for key, value in memory.codebase_profile.items():
            self.set_codebase_profile(key, value)

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def query_by_path(self, file_path: str, limit: int = 5) -> list[MemoryEntry]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM memory_entries ORDER BY confidence DESC, created_at DESC"
            ).fetchall()
        results = []
        for row in rows:
            for fp in json.loads(row["file_paths"]):
                if file_path.startswith(fp) or fp.startswith(file_path):
                    results.append(_row_to_entry(row))
                    break
        results.sort(key=lambda e: (e.confidence, e.created_at), reverse=True)
        return results[:limit]

    def query_by_tags(self, tags: list[str], limit: int = 5) -> list[MemoryEntry]:
        tag_set = set(tags)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM memory_entries ORDER BY confidence DESC, created_at DESC"
            ).fetchall()
        results = [
            _row_to_entry(r) for r in rows if tag_set & set(json.loads(r["tags"]))
        ]
        results.sort(key=lambda e: (e.confidence, e.created_at), reverse=True)
        return results[:limit]

    def query_by_type(
        self, entry_type: MemoryEntryType, limit: int = 10
    ) -> list[MemoryEntry]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM memory_entries WHERE entry_type = ? "
                "ORDER BY confidence DESC, created_at DESC LIMIT ?",
                (entry_type.value, limit),
            ).fetchall()
        return [_row_to_entry(r) for r in rows]

    def get_phase_summary(self, phase: str) -> PhaseSummary | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT data FROM phase_summaries WHERE phase = ?", (phase,)
            ).fetchone()
        if row is None:
            return None
        return PhaseSummary.model_validate_json(row["data"])

    def get_relevant_context(
        self, file_paths: list[str], max_entries: int = 10
    ) -> list[MemoryEntry]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM memory_entries").fetchall()

        scored: dict[str, tuple[float, MemoryEntry]] = {}
        for row in rows:
            entry = _row_to_entry(row)
            entry_fps: list[str] = json.loads(row["file_paths"])
            path_score = 0.0
            for fp in file_paths:
                for efp in entry_fps:
                    if fp == efp:
                        path_score = max(path_score, 1.0)
                    elif fp.startswith(efp) or efp.startswith(fp):
                        common = len(_common_prefix(fp, efp))
                        path_score = max(path_score, common / max(len(fp), len(efp)))

            if path_score == 0.0 and not entry_fps:
                path_score = 0.1

            relevance = path_score * 0.5 + entry.confidence * 0.5
            if relevance > 0.0:
                scored[entry.entry_id] = (relevance, entry)

        ranked = sorted(scored.values(), key=lambda x: x[0], reverse=True)
        return [entry for _, entry in ranked[:max_entries]]

    def remove_superseded(self, current_phase: str) -> "SQLiteMemoryStore":
        current_rank = _PHASE_ORDER.get(current_phase, -1)
        if current_rank <= 0:
            return self

        with self._conn() as conn:
            rows = conn.execute(
                "SELECT file_paths FROM memory_entries WHERE phase = ?",
                (current_phase,),
            ).fetchall()
            current_phase_paths: set[str] = set()
            for row in rows:
                current_phase_paths.update(json.loads(row["file_paths"]))

            if not current_phase_paths:
                return self

            earlier_phases = [
                p for p, r in _PHASE_ORDER.items() if 0 <= r < current_rank
            ]
            if not earlier_phases:
                return self

            placeholders = ",".join("?" * len(earlier_phases))
            candidate_rows = conn.execute(
                f"SELECT entry_id, file_paths FROM memory_entries "
                f"WHERE phase IN ({placeholders})",
                earlier_phases,
            ).fetchall()

            to_delete = [
                row["entry_id"]
                for row in candidate_rows
                if (fps := set(json.loads(row["file_paths"])))
                and fps <= current_phase_paths
            ]

            if to_delete:
                del_placeholders = ",".join("?" * len(to_delete))
                conn.execute(
                    f"DELETE FROM memory_entries WHERE entry_id IN ({del_placeholders})",
                    to_delete,
                )
                logger.info(
                    "SQLiteMemoryStore removed %d superseded entries before %s",
                    len(to_delete),
                    current_phase,
                )
        return self

    def consolidate(self) -> "SQLiteMemoryStore":
        with self._conn() as conn:
            self._maybe_consolidate(conn)
        return self

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_memory(self) -> MergeMemory:
        with self._conn() as conn:
            entry_rows = conn.execute(
                "SELECT * FROM memory_entries ORDER BY created_at"
            ).fetchall()
            summary_rows = conn.execute("SELECT * FROM phase_summaries").fetchall()
            kv_rows = conn.execute("SELECT key, value FROM kv_store").fetchall()
        return MergeMemory(
            entries=[_row_to_entry(r) for r in entry_rows],
            phase_summaries={
                r["phase"]: PhaseSummary.model_validate_json(r["data"])
                for r in summary_rows
            },
            codebase_profile={r["key"]: r["value"] for r in kv_rows},
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def entry_count(self) -> int:
        with self._conn() as conn:
            return int(
                conn.execute("SELECT COUNT(*) FROM memory_entries").fetchone()[0]
            )

    @property
    def codebase_profile(self) -> dict[str, str]:
        with self._conn() as conn:
            rows = conn.execute("SELECT key, value FROM kv_store").fetchall()
        return {r["key"]: r["value"] for r in rows}


def _common_prefix(a: str, b: str) -> str:
    prefix_len = 0
    for ca, cb in zip(a, b):
        if ca != cb:
            break
        prefix_len += 1
    return a[:prefix_len]
