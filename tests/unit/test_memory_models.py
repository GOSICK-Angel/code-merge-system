"""Tests for memory system data models."""

from datetime import datetime

import pytest

from src.memory.models import (
    MemoryEntry,
    MemoryEntryType,
    MergeMemory,
    PhaseSummary,
)


class TestMemoryEntry:
    def test_create_entry(self):
        entry = MemoryEntry(
            entry_type=MemoryEntryType.PATTERN,
            phase="planning",
            content="C-class files concentrated in api/models/",
        )
        assert entry.entry_type == MemoryEntryType.PATTERN
        assert entry.phase == "planning"
        assert entry.confidence == 0.8
        assert entry.file_paths == []
        assert entry.tags == []
        assert entry.entry_id

    def test_entry_with_file_paths_and_tags(self):
        entry = MemoryEntry(
            entry_type=MemoryEntryType.DECISION,
            phase="auto_merge",
            content="Used TAKE_TARGET for vendor files",
            file_paths=["vendor/lib.py", "vendor/util.py"],
            tags=["vendor", "take_target"],
            confidence=0.95,
        )
        assert len(entry.file_paths) == 2
        assert "vendor" in entry.tags

    def test_entry_is_frozen(self):
        entry = MemoryEntry(
            entry_type=MemoryEntryType.PATTERN,
            phase="planning",
            content="test",
        )
        with pytest.raises(Exception):
            entry.content = "modified"

    def test_entry_serialization_roundtrip(self):
        entry = MemoryEntry(
            entry_type=MemoryEntryType.CODEBASE_INSIGHT,
            phase="planning",
            content="Python 3.11 project with Pydantic v2",
            tags=["python", "pydantic"],
        )
        data = entry.model_dump(mode="json")
        restored = MemoryEntry.model_validate(data)
        assert restored.content == entry.content
        assert restored.entry_type == entry.entry_type

    def test_confidence_range_validation(self):
        with pytest.raises(Exception):
            MemoryEntry(
                entry_type=MemoryEntryType.PATTERN,
                phase="test",
                content="bad",
                confidence=1.5,
            )


class TestPhaseSummary:
    def test_create_summary(self):
        summary = PhaseSummary(
            phase="auto_merge",
            files_processed=100,
            key_decisions=["Processed 100 files: 80 take_target, 20 semantic_merge"],
            patterns_discovered=["vendor/: 90% take_target"],
            statistics={"take_target": 80, "semantic_merge": 20},
        )
        assert summary.phase == "auto_merge"
        assert summary.files_processed == 100
        assert len(summary.key_decisions) == 1

    def test_summary_is_frozen(self):
        summary = PhaseSummary(phase="test")
        with pytest.raises(Exception):
            summary.phase = "modified"


class TestMergeMemory:
    def test_default_empty(self):
        memory = MergeMemory()
        assert memory.entries == []
        assert memory.phase_summaries == {}
        assert memory.codebase_profile == {}

    def test_serialization_roundtrip(self):
        entry = MemoryEntry(
            entry_type=MemoryEntryType.PATTERN,
            phase="planning",
            content="test pattern",
        )
        summary = PhaseSummary(
            phase="planning",
            files_processed=50,
        )
        memory = MergeMemory(
            entries=[entry],
            phase_summaries={"planning": summary},
            codebase_profile={"language": "python"},
        )
        data = memory.model_dump(mode="json")
        restored = MergeMemory.model_validate(data)
        assert len(restored.entries) == 1
        assert restored.entries[0].content == "test pattern"
        assert "planning" in restored.phase_summaries
        assert restored.codebase_profile["language"] == "python"


class TestMergeMemoryInState:
    def test_state_has_memory_field(self):
        from src.models.config import MergeConfig
        from src.models.state import MergeState

        config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
        state = MergeState(config=config)
        assert isinstance(state.memory, MergeMemory)
        assert state.memory.entries == []

    def test_state_memory_serializes(self):
        from src.models.config import MergeConfig
        from src.models.state import MergeState

        config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
        state = MergeState(config=config)
        state.memory = MergeMemory(
            entries=[
                MemoryEntry(
                    entry_type=MemoryEntryType.PATTERN,
                    phase="test",
                    content="test",
                )
            ]
        )
        data = state.model_dump(mode="json")
        assert "memory" in data
        assert len(data["memory"]["entries"]) == 1
