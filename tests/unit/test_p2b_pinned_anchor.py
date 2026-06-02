"""P2-B: pin key invariants so consolidation cannot drift them (F1 guard)."""

from __future__ import annotations

from datetime import datetime

from src.memory.models import ConfidenceLevel, MemoryEntry, MemoryEntryType
from src.memory.sqlite_store import SQLiteMemoryStore
from src.memory.store import _consolidate_entries
from src.memory.summarizer import PhaseSummarizer
from src.models.decision import (
    DecisionSource,
    FileDecisionRecord,
    MergeDecision,
)
from src.models.diff import FileStatus
from src.models.judge import IssueSeverity, JudgeIssue, JudgeVerdict, VerdictType


def _entry(content: str, *, pinned: bool = False, tag: str = "t") -> MemoryEntry:
    return MemoryEntry(
        entry_type=MemoryEntryType.DECISION,
        phase="conflict_analysis",
        content=content,
        file_paths=["pkg/x/a.py"],
        tags=[tag],
        confidence=0.8,
        confidence_level=ConfidenceLevel.EXTRACTED,
        pinned=pinned,
    )


# --- model ------------------------------------------------------------------


def test_pinned_defaults_false():
    assert _entry("x").pinned is False


# --- consolidation F1 guard -------------------------------------------------


def test_pinned_entry_survives_consolidation_verbatim():
    # 3 same-group live entries would merge into one lossy blob; a pinned
    # sibling in the same group must pass through with content intact.
    live = [_entry(f"c{i}") for i in range(3)]
    pinned = _entry("CRITICAL: take_current on auth — never drift", pinned=True)
    out = _consolidate_entries([*live, pinned])
    survivors = [e for e in out if e.pinned]
    assert len(survivors) == 1
    assert survivors[0].content == "CRITICAL: take_current on auth — never drift"
    # the 3 live ones still collapsed
    assert sum(1 for e in out if not e.pinned) == 1


def test_sqlite_pinned_persists_and_survives_consolidation(tmp_path):
    store = SQLiteMemoryStore.open(tmp_path / "m.db")
    pinned = _entry("pinned recipe", pinned=True)
    store.add_entry(pinned)
    reopened = SQLiteMemoryStore.open(tmp_path / "m.db")
    row = next(e for e in reopened.to_memory().entries if e.entry_id == pinned.entry_id)
    assert row.pinned is True


def test_sqlite_legacy_db_migrates_pinned_column(tmp_path):
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
    assert entries[0].pinned is False


# --- summarizer pinning -----------------------------------------------------


class _State:
    def __init__(self, verdict, records):
        self.judge_verdict = verdict
        self.applied_repairs = []
        self.judge_verdicts_log = []
        self.judge_repair_rounds = 0
        self.file_decision_records = records


def _verdict(passed, failed):
    issues = [
        JudgeIssue(
            file_path=f,
            issue_level=IssueSeverity.HIGH,
            issue_type="reverse_impact_unhandled",
            description="x",
        )
        for f in failed
    ]
    return JudgeVerdict(
        verdict=VerdictType.FAIL if failed else VerdictType.PASS,
        reviewed_files_count=len(passed) + len(failed),
        passed_files=list(passed),
        failed_files=list(failed),
        conditional_files=[],
        issues=issues,
        critical_issues_count=0,
        high_issues_count=len(failed),
        overall_confidence=0.9,
        summary="x",
        blocking_issues=[],
        timestamp=datetime(2026, 1, 1),
        judge_model="m",
    )


def _human_record(fp: str) -> FileDecisionRecord:
    return FileDecisionRecord(
        file_path=fp,
        file_status=FileStatus.MODIFIED,
        decision=MergeDecision.TAKE_CURRENT,
        decision_source=DecisionSource.HUMAN,
        rationale="operator kept fork auth on this security-sensitive file",
    )


def test_repair_recipe_entries_are_pinned():
    state = _State(_verdict(["pkg/x/a.go"], []), {})
    state.applied_repairs = [
        {
            "file_path": "pkg/x/a.go",
            "operator": "dedup_top_level_symbols",
            "error_class": "duplicate_top_level_symbol",
        }
    ]
    _, entries = PhaseSummarizer().summarize_judge_review(state)  # type: ignore[arg-type]
    recipes = [e for e in entries if e.entry_type == MemoryEntryType.REPAIR_RECIPE]
    assert recipes and all(r.pinned for r in recipes)


def test_human_decided_judge_fail_entry_is_pinned():
    fp = "pkg/x/secret.py"
    state = _State(_verdict([], [fp]), {fp: _human_record(fp)})
    _, entries = PhaseSummarizer().summarize_judge_review(state)  # type: ignore[arg-type]
    decisions = [
        e
        for e in entries
        if e.entry_type == MemoryEntryType.DECISION and fp in e.file_paths
    ]
    assert decisions and all(d.pinned for d in decisions)


def test_non_human_judge_fail_entry_not_pinned():
    fp = "pkg/x/auto.py"
    state = _State(_verdict([], [fp]), {})  # no human record
    _, entries = PhaseSummarizer().summarize_judge_review(state)  # type: ignore[arg-type]
    decisions = [
        e
        for e in entries
        if e.entry_type == MemoryEntryType.DECISION and fp in e.file_paths
    ]
    assert decisions and not any(d.pinned for d in decisions)
