# Memory System Design

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Design Goals](#2-design-goals)
3. [Architecture Overview](#3-architecture-overview)
4. [Data Models](#4-data-models)
5. [MemoryStore API](#5-memorystore-api)
6. [Phase Summarizer](#6-phase-summarizer)
7. [Orchestrator Integration](#7-orchestrator-integration)
8. [Agent Integration](#8-agent-integration)
9. [Persistence](#9-persistence)
10. [Capacity Control](#10-capacity-control)

---

## 1. Problem Statement

In a large-scale merge (10,000+ files, 6+ phases, multiple Judge repair rounds), each LLM call is fully stateless. This creates three concrete problems:

| Problem | Impact |
|---------|--------|
| No cross-phase knowledge transfer | Judge in Phase 5 cannot leverage patterns discovered by Executor in Phase 2 |
| Repeated pattern re-discovery | When 500 files in `vendor/` are all B-class, the system re-evaluates each one independently |
| No codebase understanding accumulation | Agents cannot build a profile of the target project (language, framework, conventions) |

The memory system addresses these by providing a persistent, queryable store of cross-phase learnings that agents can read from and write to.

---

## 2. Design Goals

| Goal | Mechanism |
|------|-----------|
| Cross-phase learning | Phase-end summarization extracts patterns; later agents query them |
| Compact storage | Summaries and patterns, not raw file content |
| Queryable | By file path (prefix matching), tags, entry type, or relevance scoring |
| Checkpoint-compatible | Serialized alongside `MergeState` via Pydantic `model_dump()` |
| Optional | All agents work identically without memory (backward compatible) |
| Immutable API | `add_entry()` returns a new `MemoryStore` instance |

---

## 3. Architecture Overview

```
                         MemoryStore (in-memory)
                              |
          +-------------------+-------------------+
          |                   |                   |
    PhaseSummarizer     Agent queries        Checkpoint
    (write path)        (read path)          (persistence)
          |                   |                   |
    After each phase    During LLM call     state.memory
    completes           via Builder         (MergeMemory)
```

### File Layout

```
src/memory/
  __init__.py
  models.py          # MemoryEntry, PhaseSummary, MergeMemory
  store.py           # MemoryStore (immutable, queryable)
  summarizer.py      # PhaseSummarizer (phase-end extraction)
```

---

## 4. Data Models

Location: `src/memory/models.py`

### MemoryEntryType

```python
class MemoryEntryType(str, Enum):
    PATTERN = "pattern"             # "api/models/ has 80% C-class files"
    DECISION = "decision"           # "vendor/ files all used TAKE_TARGET"
    RELATIONSHIP = "relationship"   # "auth.py depends on user_model.py"
    PHASE_SUMMARY = "phase_summary" # condensed output from a completed phase
    CODEBASE_INSIGHT = "codebase_insight"  # "Python 3.11 + Pydantic v2 project"
```

### MemoryEntry

```python
class MemoryEntry(BaseModel, frozen=True):
    entry_id: str                      # UUID, auto-generated
    entry_type: MemoryEntryType
    phase: str                         # which phase produced this entry
    content: str                       # the insight (compact text, not raw data)
    file_paths: list[str] = []         # associated files for path-based query
    tags: list[str] = []               # e.g. ["import_conflict", "api/models"]
    confidence: float = 0.8            # 0.0-1.0, used for eviction ranking
    created_at: datetime               # auto-set
```

Key design choices:
- `frozen=True` enforces immutability at the Pydantic level
- `confidence` is the primary sort key for eviction when the store reaches capacity
- `file_paths` supports prefix-based query matching

### PhaseSummary

```python
class PhaseSummary(BaseModel, frozen=True):
    phase: str                                    # "planning", "auto_merge", etc.
    files_processed: int = 0
    key_decisions: list[str] = []                 # max 10, one-liners
    patterns_discovered: list[str] = []           # max 10
    error_summary: str = ""
    statistics: dict[str, int | float] = {}       # counts, rates
```

### MergeMemory

Top-level container, stored as a field on `MergeState`:

```python
class MergeMemory(BaseModel):
    entries: list[MemoryEntry] = []
    phase_summaries: dict[str, PhaseSummary] = {}
    codebase_profile: dict[str, str] = {}         # key-value cache
```

---

## 5. MemoryStore API

Location: `src/memory/store.py`

All mutation methods return a new `MemoryStore` instance (immutable pattern).

### Write Operations

| Method | Signature | Description |
|--------|-----------|-------------|
| `add_entry` | `(entry: MemoryEntry) -> MemoryStore` | Add a single entry; triggers eviction if > 500 |
| `record_phase_summary` | `(summary: PhaseSummary) -> MemoryStore` | Record or overwrite a phase summary |
| `set_codebase_profile` | `(key: str, value: str) -> MemoryStore` | Set a codebase profile key-value pair |

### Query Operations

| Method | Signature | Description |
|--------|-----------|-------------|
| `query_by_path` | `(file_path: str, limit=5) -> list[MemoryEntry]` | Prefix matching on `entry.file_paths` |
| `query_by_tags` | `(tags: list[str], limit=5) -> list[MemoryEntry]` | Union match on tags |
| `query_by_type` | `(entry_type, limit=10) -> list[MemoryEntry]` | Filter by `MemoryEntryType` |
| `get_phase_summary` | `(phase: str) -> PhaseSummary \| None` | Get summary for a specific phase |
| `get_relevant_context` | `(file_paths, max_entries=10) -> list[MemoryEntry]` | Ranked by path relevance + confidence |

### Relevance Scoring (`get_relevant_context`)

```
relevance = path_score * 0.5 + confidence * 0.5

path_score:
  1.0  — exact path match
  0.x  — prefix overlap ratio (common_prefix_length / max_path_length)
  0.1  — entry has no file_paths (global insight)
  0.0  — no match
```

### Serialization

| Method | Description |
|--------|-------------|
| `to_memory() -> MergeMemory` | Deep-copy export for checkpoint storage |
| `from_memory(memory) -> MemoryStore` | Static factory, deep-copy import |

---

## 6. Phase Summarizer

Location: `src/memory/summarizer.py`

`PhaseSummarizer` runs at the end of each phase and produces:
1. A `PhaseSummary` (compact statistics and key decisions)
2. A list of `MemoryEntry` objects (discovered patterns)

### Summarization Methods

| Method | Trigger | What It Extracts |
|--------|---------|-----------------|
| `summarize_planning` | After Phase 1 | ABCDE category distribution; C-class file concentration by directory |
| `summarize_auto_merge` | After Phase 2 | Decision strategy distribution; directory-level dominance patterns (e.g., "vendor/: 90% TAKE_TARGET") |
| `summarize_conflict_analysis` | After Phase 3 | Conflict type frequency; recurring conflict types by directory |
| `summarize_judge_review` | After Phase 5 | Repair round count; recurring judge issue types |

### Pattern Detection Rules

**Directory Dominance** (auto_merge):
- Group `FileDecisionRecord` by 2-level directory prefix
- If a directory has 3+ files AND one strategy accounts for >= 70% of decisions, emit a `PATTERN` entry

**C-class Concentration** (planning):
- Count C-class files by 2-level directory prefix
- If a directory has 3+ C-class files, emit a `PATTERN` entry

**Recurring Conflict Type** (conflict_analysis):
- Count `ConflictType` occurrences across all analyses
- If a type appears 3+ times, emit a `PATTERN` entry with affected file paths

**Recurring Judge Issue** (judge_review):
- Count `issue_type` occurrences across all verdict logs
- If an issue type appears 2+ times, emit a `PATTERN` entry

---

## 7. Orchestrator Integration

Location: `src/core/orchestrator.py`

### Lifecycle

```
run(state)
  |
  +-- Initialize MemoryStore from state.memory (supports resume from checkpoint)
  +-- Inject memory into all agents
  |
  +-- Phase 1: PLANNING
  |     +-- _update_memory("planning", state)
  |     +-- _inject_memory()
  |
  +-- Phase 2: AUTO_MERGE
  |     +-- _update_memory("auto_merge", state)
  |     +-- _inject_memory()
  |
  +-- Phase 3: CONFLICT_ANALYSIS
  |     +-- _update_memory("conflict_analysis", state)
  |     +-- _inject_memory()
  |
  +-- Phase 5: JUDGE_REVIEW
  |     +-- _update_memory("judge_review", state)
  |
  +-- Phase 6: REPORT
```

### Key Methods

```python
def _update_memory(self, phase: str, state: MergeState) -> None:
    method = getattr(self._summarizer, f"summarize_{phase}", None)
    if method is None:
        return
    phase_summary, entries = method(state)
    store = self._memory_store.record_phase_summary(phase_summary)
    for entry in entries:
        store = store.add_entry(entry)
    self._memory_store = store
    state.memory = store.to_memory()  # persist for checkpoint

def _inject_memory(self) -> None:
    for agent in self._all_agents:
        agent.set_memory_store(self._memory_store)
```

---

## 8. Agent Integration

### BaseAgent

Location: `src/agents/base_agent.py`

```python
class BaseAgent(ABC):
    _memory_store: MemoryStore | None = None

    def set_memory_store(self, store: MemoryStore) -> None:
        self._memory_store = store
```

All agents inherit `_memory_store`. Memory injection is optional — if `None`, agents work identically to before.

### JudgeAgent

Location: `src/agents/judge_agent.py`

In `review_file()`:
1. Query memory for file-related patterns
2. Compute dynamic content budget (replaces hardcoded `[:5000]`)
3. Inject memory context into prompt via `memory_context` parameter

```python
if self._memory_store:
    builder = AgentPromptBuilder(self.llm_config, self._memory_store)
    memory_context = builder.build_memory_context_text([file_path])
    max_content_chars = builder.compute_content_budget(JUDGE_SYSTEM + memory_context)
```

### ConflictAnalystAgent & ExecutorAgent

Both agents inject memory context into the `project_context` parameter of their prompt builders:

```python
if self._memory_store:
    builder = AgentPromptBuilder(self.llm_config, self._memory_store)
    memory_text = builder.build_memory_context_text([file_diff.file_path])
    if memory_text:
        enriched_context = f"{project_context}\n\n{memory_text}"
```

### Memory Context Format in Prompts

```
# Prior Knowledge
## Relevant Patterns
- vendor/lib/: 5/5 files used 'take_target' strategy
- 3 files with both-side changes in api/models/
## Phase Insights
  planning: 3 C-class files in api/models/
  auto_merge: vendor/lib/: 5/5 files used 'take_target' strategy
```

---

## 9. Persistence

Memory is persisted as part of `MergeState`:

```python
class MergeState(BaseModel):
    memory: MergeMemory = Field(default_factory=MergeMemory)
```

- Auto-serialized via `state.model_dump(mode="json")` in `Checkpoint.save()`
- Auto-restored via `MergeState.model_validate()` in `Checkpoint.load()`
- Survives `merge resume --run-id <id>` seamlessly

---

## 10. Capacity Control

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| `MAX_ENTRIES` | 500 | Prevents memory bloat on 10K+ file runs |
| Eviction strategy | Drop lowest `confidence` entries | High-confidence patterns survive |
| `key_decisions` per summary | Max 10 | Compact, not raw data |
| `patterns_discovered` per summary | Max 10 | Compact |
| `DIR_DOMINANCE_THRESHOLD` | 0.70 | Only emit pattern if strategy is dominant |
| Memory context in prompt | Max 8 entries | Bounded prompt injection |
