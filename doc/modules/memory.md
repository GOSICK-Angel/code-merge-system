# 记忆系统（`src/memory/`）

> **版本**：2026-04-17
> 三层记忆系统：L0 代码库画像、L1 阶段要点、L2 文件相关记录。受 MemPalace 与 Graphify 的设计启发，详见 `doc/references/`。

---

## 1. 模块组成

| 文件 | 职责 |
|---|---|
| `models.py` | `MemoryEntry` / `PhaseSummary` / `MergeMemory` 三个 Pydantic 模型 |
| `store.py` | `MemoryStore`：不可变数据操作 + 去重 + 合并 + 超限处理 |
| `summarizer.py` | `PhaseSummarizer`：每个 Phase 结束后把 state 汇总成 `PhaseSummary` + `MemoryEntry[]` |
| `layered_loader.py` | `LayeredMemoryLoader`：拼装 L0/L1/L2 三层字符串，注入到 Agent prompt |

---

## 2. 数据模型

### `MemoryEntry`（不可变，frozen=True）

```python
entry_id: str            # UUID
entry_type: Literal[
    "pattern",           # 合并模式（如"前端组件一律 TAKE_CURRENT"）
    "decision",          # 具体文件的合并决策
    "relationship",      # 文件/模块间关系
    "phase_summary",     # 阶段汇总
    "codebase_insight",  # 项目级洞察
]
phase: str               # 产生该条目的 phase 名
content: str             # 文本内容
file_paths: list[str]    # 关联文件
tags: list[str]
confidence: float        # [0, 1]
confidence_level: Literal["extracted", "inferred", "heuristic"]
content_hash: str        # 自动基于 entry_type+phase+content SHA-256 前 16 位
created_at: datetime
```

`content_hash` 用于**去重**：相同哈希不入库。

### `PhaseSummary`（不可变）

```python
phase: str
files_processed: int
key_decisions: list[str]
patterns_discovered: list[str]
error_summary: str
statistics: dict[str, int | float]
```

### `MergeMemory`

```python
entries: list[MemoryEntry]
phase_summaries: dict[str, PhaseSummary]    # phase_name → summary
codebase_profile: dict[str, str]            # "primary_language": "python", ...
```

---

## 3. `MemoryStore` 不可变语义

所有变更方法返回**新 store**，原 store 不被修改：

```python
store = MemoryStore()
store2 = store.add_entry(entry)       # store 未变
store3 = store2.record_phase_summary(ps)
store4 = store3.set_codebase_profile("lang", "python")
```

这让 Orchestrator 可以安全地把 store 注入每个 Agent，而不担心某个 Agent 污染后续 Agent 的记忆。

### 关键常量
- `MAX_ENTRIES = 500` — 硬上限，超过按 confidence 降序保留
- `CONSOLIDATION_THRESHOLD = 300` — 超过这个数先做 `_consolidate_entries()`

### 查询 API
```python
store.query_by_path(file_path, limit=5) -> list[MemoryEntry]
store.query_by_tags(tags, limit=5)       -> list[MemoryEntry]
store.query_by_type(entry_type, limit=10)-> list[MemoryEntry]
store.get_relevant_context(file_paths, max_entries=10)
```

`get_relevant_context` 按 `path_score × 0.5 + confidence × 0.5` 综合排序，是 Agent 调用最多的入口。

### 自动合并与淘汰
- `_consolidate_entries()` 按 `(phase, entry_type, primary_tag)` 分组，组内 ≥ 3 条合并为一条，置信度 + 0.05
- `remove_superseded(current_phase)` 移除早期 Phase 中文件完全被后续 Phase 覆盖的条目。Phase 顺序：`planning < auto_merge < conflict_analysis < judge_review`

---

## 4. 三层加载（`LayeredMemoryLoader`）

注入到 Agent 的文本按三段拼接：

### L0 — Project Profile（全局）
来自 `codebase_profile` dict。例：
```
## Project Profile
- primary_language: python
- framework: Flask + Celery
- ...
```

### L1 — Phase Essentials
- 当前 Phase 的 `PhaseSummary.patterns_discovered`（最多 5 条）
- 上一个 Phase 的 `key_decisions`（最多 5 条）

### L2 — File-Relevant
当 Agent 告诉 loader 它要处理哪些文件：
```python
loader.load_for_agent(current_phase="auto_merge", file_paths=["a.py", "b.py"])
```
则取 `get_relevant_context(file_paths, max_entries=8)` 的 top 8 条。

---

## 5. Orchestrator 如何更新 memory

在 `Orchestrator._update_memory(phase, state)`：

```python
phase_summary, entries = summarizer.summarize_<phase>(state)
store = store.record_phase_summary(phase_summary)
for e in entries:
    store = store.add_entry(e)
store = store.remove_superseded(phase)
self._memory_store = store
state.memory = store.to_memory()
self._inject_memory()    # 把新 store 推给每个 agent
```

`summarizer` 按 phase 名动态分发（`summarize_planning`、`summarize_auto_merge`、……），没有对应方法则跳过。

---

## 6. Checkpoint 兼容

`MergeState.memory: MergeMemory` 是 state 的一部分，随 Checkpoint 一同持久化到 `checkpoint.json`。恢复时 Orchestrator 通过 `MemoryStore.from_memory(state.memory)` 重建 store，再 inject 到 Agent。

---

## 7. 扩展点

- **新增 entry_type**：扩展 `MemoryEntryType` Literal 并在 `summarize_*` 产生
- **新增 L0 键**：在 `initialize.py` 或 Planner 中 `store.set_codebase_profile(k, v)`
- **自定义加载层**：子类化 `LayeredMemoryLoader` 覆写 `_build_l2` 即可
- **替换相关性算法**：修改 `store.get_relevant_context()` 的打分公式

---

## 8. 相关参考

- MemPalace（语义索引 + 图谱）：`doc/references/mempalace-analysis.md`
- Graphify（代码知识图谱压缩）：`doc/references/graphify-analysis.md`
- 增强方案提案：`doc/references/enhanced-context-memory-proposal.md`
