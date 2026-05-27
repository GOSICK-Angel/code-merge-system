"""Tests for MemoryStore."""

from datetime import datetime

from src.memory.models import (
    MemoryEntry,
    MemoryEntryType,
    MergeMemory,
    PhaseSummary,
)
from src.memory.store import MAX_ENTRIES, MemoryStore


class TestMemoryStoreAdd:
    def test_add_entry_immutable(self):
        store = MemoryStore()
        entry = MemoryEntry(
            entry_type=MemoryEntryType.PATTERN,
            phase="planning",
            content="test pattern",
        )
        new_store = store.add_entry(entry)
        assert new_store.entry_count == 1
        assert store.entry_count == 0

    def test_add_multiple_entries(self):
        store = MemoryStore()
        for i in range(5):
            store = store.add_entry(
                MemoryEntry(
                    entry_type=MemoryEntryType.PATTERN,
                    phase="planning",
                    content=f"pattern {i}",
                )
            )
        assert store.entry_count == 5

    def test_eviction_at_max(self):
        store = MemoryStore()
        for i in range(MAX_ENTRIES + 10):
            store = store.add_entry(
                MemoryEntry(
                    entry_type=MemoryEntryType.PATTERN,
                    phase=f"phase_{i % 50}",
                    content=f"pattern {i}",
                    tags=[f"unique_tag_{i}"],
                    confidence=i / (MAX_ENTRIES + 10),
                )
            )
        assert store.entry_count <= MAX_ENTRIES

    def test_eviction_keeps_highest_confidence(self):
        store = MemoryStore()
        store = store.add_entry(
            MemoryEntry(
                entry_type=MemoryEntryType.PATTERN,
                phase="planning",
                content="high confidence",
                confidence=0.99,
            )
        )
        for i in range(MAX_ENTRIES):
            store = store.add_entry(
                MemoryEntry(
                    entry_type=MemoryEntryType.PATTERN,
                    phase="planning",
                    content=f"low {i}",
                    confidence=0.1,
                )
            )
        contents = " ".join(e.content for e in store.to_memory().entries)
        assert "high confidence" in contents


class TestMemoryStoreQuery:
    def _build_store(self) -> MemoryStore:
        store = MemoryStore()
        store = store.add_entry(
            MemoryEntry(
                entry_type=MemoryEntryType.PATTERN,
                phase="planning",
                content="api models pattern",
                file_paths=["api/models/user.py", "api/models/team.py"],
                tags=["api", "models"],
                confidence=0.9,
            )
        )
        store = store.add_entry(
            MemoryEntry(
                entry_type=MemoryEntryType.DECISION,
                phase="auto_merge",
                content="vendor files adopted",
                file_paths=["vendor/lib.py"],
                tags=["vendor", "take_target"],
                confidence=0.95,
            )
        )
        store = store.add_entry(
            MemoryEntry(
                entry_type=MemoryEntryType.CODEBASE_INSIGHT,
                phase="planning",
                content="Python 3.11 project",
                tags=["python"],
                confidence=0.85,
            )
        )
        return store

    def test_get_relevant_context(self):
        store = self._build_store()
        results = store.get_relevant_context(["api/models/user.py"])
        assert len(results) >= 1
        assert results[0].content == "api models pattern"

    def test_get_relevant_context_includes_global(self):
        store = self._build_store()
        results = store.get_relevant_context(["api/models/user.py"], max_entries=10)
        contents = [r.content for r in results]
        assert "Python 3.11 project" in contents


class TestMemoryStorePhaseSummary:
    def test_record_and_get(self):
        store = MemoryStore()
        summary = PhaseSummary(
            phase="auto_merge",
            files_processed=100,
            key_decisions=["processed 100 files"],
        )
        store = store.record_phase_summary(summary)
        result = store.get_phase_summary("auto_merge")
        assert result is not None
        assert result.files_processed == 100

    def test_get_missing_returns_none(self):
        store = MemoryStore()
        assert store.get_phase_summary("nonexistent") is None

    def test_overwrite_summary(self):
        store = MemoryStore()
        store = store.record_phase_summary(
            PhaseSummary(phase="planning", files_processed=50)
        )
        store = store.record_phase_summary(
            PhaseSummary(phase="planning", files_processed=100)
        )
        result = store.get_phase_summary("planning")
        assert result is not None
        assert result.files_processed == 100


class TestMemoryStoreCodebaseProfile:
    def test_set_and_get(self):
        store = MemoryStore()
        store = store.set_codebase_profile("language", "python")
        assert store.codebase_profile["language"] == "python"

    def test_immutable(self):
        store = MemoryStore()
        new_store = store.set_codebase_profile("language", "python")
        assert "language" not in store.codebase_profile
        assert "language" in new_store.codebase_profile


class TestMemoryStoreSerialization:
    def test_roundtrip(self):
        store = MemoryStore()
        store = store.add_entry(
            MemoryEntry(
                entry_type=MemoryEntryType.PATTERN,
                phase="planning",
                content="test",
            )
        )
        store = store.record_phase_summary(
            PhaseSummary(phase="planning", files_processed=10)
        )
        store = store.set_codebase_profile("lang", "py")

        memory = store.to_memory()
        restored = MemoryStore.from_memory(memory)

        assert restored.entry_count == 1
        assert restored.get_phase_summary("planning") is not None
        assert restored.codebase_profile["lang"] == "py"

    def test_to_memory_deep_copy(self):
        store = MemoryStore()
        store = store.add_entry(
            MemoryEntry(
                entry_type=MemoryEntryType.PATTERN,
                phase="test",
                content="original",
            )
        )
        memory1 = store.to_memory()
        memory2 = store.to_memory()
        assert memory1 is not memory2
        assert memory1.entries[0].content == memory2.entries[0].content
