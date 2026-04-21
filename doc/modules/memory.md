# 记忆系统（`src/memory/`）

> **版本**：2026-04-21
> 三层记忆系统：L0 代码库画像、L1 阶段要点、L2 文件相关记录。受 MemPalace 与 Graphify 的设计启发，详见 `doc/references/`。

---

## 1. 模块组成

| 文件 | 职责 |
|---|---|
| `models.py` | `MemoryEntry` / `PhaseSummary` / `MergeMemory` 三个 Pydantic 模型 |
| `store.py` | `MemoryStore`：不可变语义的内存操作 + 去重 + 合并 + 超限处理（单进程） |
| `sqlite_store.py` | `SQLiteMemoryStore`：WAL 模式的 SQLite 后端，多进程并发安全，Orchestrator 实际使用 |
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

## 3. `MemoryStore` 不可变语义（单进程）

`MemoryStore` 是原始的内存实现。所有变更方法返回**新 store**，原 store 不被修改：

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

## 4. `SQLiteMemoryStore`（多进程并发安全）

Orchestrator 实际使用的后端。数据库位于 `<run_dir>/memory.db`，WAL 模式允许多个进程并发读、串行写。

### 与 `MemoryStore` 的对比

| 特性 | `MemoryStore` | `SQLiteMemoryStore` |
|---|---|---|
| 持久化 | 仅随 checkpoint.json | 独立 SQLite 文件，每次写立即落盘 |
| 并发 | 单进程，不可共享 | WAL 模式，多进程安全 |
| 可变性 | 不可变，返回新实例 | 可变，方法返回 `self` |
| 去重 | Python 集合检查 | `UNIQUE INDEX` on `content_hash` |
| resume 时数据完整性 | 依赖 checkpoint 粒度 | 每次 `add_entry` 立即落盘，比 checkpoint 更新 |

### 数据库 Schema

```sql
memory_entries (entry_id PK, entry_type, phase, content,
                file_paths JSON, tags JSON, confidence,
                confidence_level, content_hash UNIQUE, created_at)
phase_summaries (phase PK, data JSON)
kv_store        (key PK, value)
```

### 开启方式
由 `Orchestrator.run()` 自动创建，无需手动配置。
`MergeState.memory_db_path` 记录路径，供 resume 时定位。

---

## 5. 三层加载（`LayeredMemoryLoader`）

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

## 6. 完整运行时流程

```
Orchestrator.run(state)
  │
  ├─ [初始化]
  │   SQLiteMemoryStore.open(run_dir / "memory.db")
  │   if state.memory 有旧条目（resume）→ import_from_memory()
  │   state.memory_db_path = str(db_path)
  │   _inject_memory() → 所有 Agent 拿到同一 store 引用
  │
  ├─ [Phase 循环] ─── 每个 Phase 执行后 ────────────────────────────┐
  │   if outcome.should_update_memory:                              │
  │     PhaseSummarizer.summarize_<phase>(state)                    │
  │       → PhaseSummary + list[MemoryEntry]                        │
  │     store.record_phase_summary()                                │
  │     store.add_entry() × N        ← INSERT OR IGNORE（去重）     │
  │     store.remove_superseded()    ← 删被新 Phase 覆盖的旧条目    │
  │     state.memory = store.to_memory()  ← 同步回 checkpoint 副本  │
  │     _inject_memory()             ← Agent 立即感知新条目         │
  │   if outcome.should_checkpoint:                                 │
  │     checkpoint.save(state)       ← 含 memory 副本 + memory_db_path │
  │                                                                 │
  └─ [Agent LLM 调用时] ────────────────────────────────────────────┘
      LayeredMemoryLoader.load_for_agent(phase, file_paths)
        L0: codebase_profile
        L1: 当前/上一 Phase PhaseSummary
        L2: get_relevant_context(file_paths)   ← 路径+置信度双维打分
      → 拼装为 memory_context 字符串注入 prompt
```

---

## 7. 各 Phase 提取内容（确定性规则）

`PhaseSummarizer` 目前全部使用确定性规则提取，无 LLM 参与：

| Phase | 提取的 `MemoryEntry` | 提取的 `PhaseSummary.patterns_discovered` |
|---|---|---|
| `planning` | C-class 文件集中的目录（count ≥ 3）→ `PATTERN` 条目 | 各 C-class 目录及文件数 |
| `auto_merge` | 目录内主导合并策略（占比 ≥ 70%）→ `PATTERN` 条目 | 策略分布 |
| `conflict_analysis` | 出现 ≥ 3 次的冲突类型及位置 → `PATTERN` 条目 | 冲突类型计数 |
| `judge_review` | 出现 ≥ 2 次的 judge issue 类型 → `PATTERN` 条目 | 修复轮次、verdict 序列 |

**当前局限**：规则只能捕捉"什么文件、多少次"，无法提炼"为什么失败"或"下次应该如何避免"这类因果洞察。

---

## 8. 收益分析

### 8.1 跨 Phase 上下文传递

Planning 阶段发现"src/api/ 下有 12 个 C-class 文件"，这条记忆在 ConflictAnalyst 处理 `src/api/` 文件时自动注入 prompt，Agent 无需重新推断目录风险等级。

### 8.2 置信度驱动的 Token 分配

`get_relevant_context()` 按 `path_score × 0.5 + confidence × 0.5` 综合排序，高置信条目优先占据有限 prompt 窗口，低质量历史猜测不消耗位置。

### 8.3 去重防止 prompt 膨胀

`content_hash = sha256(entry_type:phase:content)[:16]`，相同语义的条目只存一次，Judge 多轮反复记录同一问题不会让记忆库线性增长。

### 8.4 及时淘汰过期条目

`remove_superseded()` 在每次 Phase 更新后删除被当前 Phase 覆盖的旧条目（如 planning 阶段对 `a.py` 的判断，在 auto_merge 阶段有了真实决策后失效）。

### 8.5 多进程无损 Resume（SQLiteMemoryStore 改造后）

SQLite WAL 保证每次 `add_entry` 立即落盘；中断重启时从 `memory.db` 恢复比从 `checkpoint.json` 的 `memory` 副本更完整——因为 DB 写入粒度比 checkpoint 保存粒度更细。

---

## 9. LLM 辅助提炼：`MemoryExtractorAgent`

> **目标**：在确定性规则的基础上，对"高信息量事件"（异常、反复修复、Plan 争议）追加一次 LLM 提炼，捕捉当前规则无法表达的因果洞察。

### 9.1 为什么需要独立 Agent 类

早期方案曾考虑将提炼逻辑作为 Orchestrator 私有方法实现。但代码库的反模式约束第 2 条（`CLAUDE.md`）明确禁止在 `BaseAgent._call_llm_with_retry` 之外直接调用 LLM。将逻辑放在 Orchestrator 里意味着绕过 retry / circuit-breaker 层，`test_agent_contracts.py` 的静态扫描会直接报错。

因此 `MemoryExtractorAgent` 是独立的 `BaseAgent` 子类，完整继承 retry、成本追踪、trace 日志、credential rotation 等基础设施，同时声明了专属 contract，边界清晰。

### 9.2 模块位置

| 文件 | 内容 |
|---|---|
| `src/agents/memory_extractor_agent.py` | `MemoryExtractorAgent` 实现 |
| `src/agents/contracts/memory_extractor.yaml` | 输入白名单、gate 声明、forbidden 规则 |
| `src/llm/prompts/memory_extractor_prompts.py` | `MEMORY_EXTRACTOR_SYSTEM` + `build_extraction_prompt()` |
| `src/llm/prompts/gate_registry.py` | 注册 `M-SYSTEM`、`M-EXTRACT-INSIGHT` |

### 9.3 Agent Contract

```yaml
# src/agents/contracts/memory_extractor.yaml
name: memory_extractor
inputs:
  - config
  - errors
  - plan_disputes
  - judge_verdicts_log
  - judge_repair_rounds
output_schema: list[MemoryEntry]
gates:
  - M-SYSTEM
  - M-EXTRACT-INSIGHT
forbidden:
  - writes_state
  - direct_llm_call
collaboration: compute
requires_human_options: false
```

### 9.4 触发条件

`Orchestrator._update_memory()` 在确定性提取完成后检查是否需要调用：

| 条件 | 触发 Phase |
|---|---|
| `state.errors` 非空 | 任意 Phase |
| `state.plan_disputes` 非空 | `planning` 结束后 |
| `state.judge_repair_rounds >= config.memory.min_judge_repair_rounds`（默认 2） | `judge_review` 结束后 |

由 `MergeConfig.memory.llm_extraction: bool`（默认 `false`）全局开关控制，关闭时完全跳过。

### 9.5 入口与调用方式

`MemoryExtractorAgent` 提供专属入口 `extract()`，而非通过 Phase 循环调用：

```python
# 主入口（Orchestrator 调用）
async def extract(self, phase: str, state: MergeState) -> list[MemoryEntry]:
    view = self.restricted_view(state)
    prompt = get_gate("M-EXTRACT-INSIGHT").render(phase, view, self._memory_store)
    system = get_gate("M-SYSTEM").render()
    raw = await self._call_llm_with_retry([{"role": "user", "content": prompt}], system=system)
    return _parse_entries(raw, phase)

# 满足抽象接口，非主路径
async def run(self, state: Any) -> AgentMessage: ...
```

Orchestrator 的 `_update_memory()` 改为 `async`，在确定性提取后追加：

```python
if self._should_llm_extract(phase, state):
    llm_entries = await self.memory_extractor.extract(phase, state)
    for e in llm_entries:
        self._memory_store.add_entry(e)   # content_hash 去重保护
```

### 9.6 输入 / 输出格式

```
输入（prompt 拼装）：
  - 当前 Phase 名称
  - 触发事件原始数据（errors / plan_disputes / judge_verdicts_log）
  - 已有 MemoryEntry 摘要（避免重复提炼）

输出（LLM 返回 JSON 数组，解析为 MemoryEntry）：
  每条包含：
    - entry_type: "decision" | "pattern" | "codebase_insight"
    - content: 一句话因果洞察（≤ 120 字符）
    - confidence: LLM 自评 [0.0, 1.0]
    - confidence_level: "inferred"
    - tags: 触发原因标签（如 "judge_failure", "plan_dispute"）
```

### 9.7 成本控制

| 控制手段 | 具体做法 |
|---|---|
| 全局开关 | `MergeConfig.memory.llm_extraction: bool = false` |
| 触发频率 | 仅在 §9.4 条件满足时，非每 Phase 无条件触发 |
| 输出上限 | `config.memory.max_insights_per_phase`（默认 5） |
| 模型选择 | `AgentsLLMConfig.memory_extractor` 默认 Haiku（`claude-haiku-4-5-20251001`） |
| 去重保护 | `content_hash` 确保重复内容不重复入库 |

### 9.8 与现有架构的兼容性

- 输出仍是标准 `MemoryEntry`，走 `add_entry` / `content_hash` 去重路径
- `confidence_level = "inferred"` 区分于规则提取的 `"extracted"`，查询时可过滤
- 不影响单进程 / 多进程分支：`SQLiteMemoryStore` 统一处理并发写入
- `llm_extraction = false` 时行为与改造前完全一致，零风险引入

---

## 10. Checkpoint 兼容

`MergeState` 有两个持久化记忆的字段：

| 字段 | 作用 |
|---|---|
| `memory: MergeMemory` | checkpoint.json 中的快照副本，与 Phase 粒度同步 |
| `memory_db_path: str \| None` | 指向 `memory.db` 的路径，resume 时优先从 DB 恢复 |

resume 策略：
1. 若 `memory_db_path` 指向的 DB 文件存在 → 直接打开，DB 数据比 JSON 副本更新
2. 若 DB 文件不存在（首次 / 跨机器迁移）→ 从 `state.memory` 导入，建立新 DB

---

## 11. 扩展点

- **新增 entry_type**：扩展 `MemoryEntryType` Literal 并在 `summarize_*` 产生
- **新增 L0 键**：在 `initialize.py` 或 Planner 中 `store.set_codebase_profile(k, v)`
- **自定义加载层**：子类化 `LayeredMemoryLoader` 覆写 `_build_l2` 即可
- **替换相关性算法**：修改 `store.get_relevant_context()` 的打分公式
- **启用 LLM 提炼**：在 `config.yaml` 设置 `memory.llm_extraction: true`（见 §9）

---

## 12. 相关参考

- MemPalace（语义索引 + 图谱）：`doc/references/mempalace-analysis.md`
- Graphify（代码知识图谱压缩）：`doc/references/graphify-analysis.md`
- 增强方案提案：`doc/references/enhanced-context-memory-proposal.md`
- CCGS 分析与优化方案：`doc/references/claude-code-game-studios-analysis.md`
