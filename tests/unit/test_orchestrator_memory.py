"""OPP-6: LLM-extracted memory entries must survive the in-memory store path.

``Orchestrator._update_memory`` appended LLM-extracted entries via
``self._memory_store.add_entry(entry)`` without reassigning the return value.
``MemoryStore.add_entry`` is immutable (returns a NEW instance), so on any
deployment using the in-memory store every LLM-extracted insight was silently
dropped. This pins the fix.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.core.orchestrator import Orchestrator
from src.memory.models import MemoryEntry, MemoryEntryType, PhaseSummary
from src.memory.store import MemoryStore
from src.models.config import MemoryExtractionConfig


class _FakeSummarizer:
    def summarize_planning(self, state: object) -> tuple[PhaseSummary, list]:
        return PhaseSummary(phase="planning"), []


class _FakeExtractor:
    def __init__(self, entries: list[MemoryEntry]) -> None:
        self._entries = entries

    async def extract(self, phase: str, state: object) -> list[MemoryEntry]:
        return self._entries


async def test_llm_extracted_entries_land_on_in_memory_store():
    llm_entries = [
        MemoryEntry(
            entry_type=MemoryEntryType.CODEBASE_INSIGHT,
            phase="planning",
            content="insight A",
        ),
        MemoryEntry(
            entry_type=MemoryEntryType.CODEBASE_INSIGHT,
            phase="planning",
            content="insight B",
        ),
    ]

    orch = Orchestrator.__new__(Orchestrator)
    orch._summarizer = _FakeSummarizer()
    orch._memory_store = MemoryStore()
    orch._phases_since_last_extract = 0
    orch.memory_extractor = _FakeExtractor(llm_entries)
    orch.config = SimpleNamespace(memory=MemoryExtractionConfig(llm_extraction=True))

    # state.errors non-empty forces _should_llm_extract -> True regardless of phase
    state = SimpleNamespace(errors=["boom"])

    await orch._update_memory("planning", state)

    contents = {e.content for e in orch._memory_store.to_memory().entries}
    assert contents == {"insight A", "insight B"}
    assert orch._phases_since_last_extract == 0
