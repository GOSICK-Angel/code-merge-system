# CodeMergeSystem 上下文与记忆系统增强方案

> 参考 MemPalace 与 Graphify 的设计思想，对当前项目进行增强。  
> 本文档是后续实现的蓝图，包含详细设计、分阶段实施计划和测试方案。

## 目录

1. [现状分析与问题识别](#1-现状分析与问题识别)
2. [增强目标](#2-增强目标)
3. [总体架构](#3-总体架构)
4. [P0: 文件依赖图](#4-p0-文件依赖图)
5. [P1: 记忆分层加载](#5-p1-记忆分层加载)
6. [P2: 置信度语义化](#6-p2-置信度语义化)
7. [P3: 记忆去重](#7-p3-记忆去重)
8. [实施计划](#8-实施计划)
9. [测试方案](#9-测试方案)
10. [风险与缓解](#10-风险与缓解)

---

## 1. 现状分析与问题识别

### 1.1 当前架构概览

系统已有三层上下文管理：

```
Token Budget (src/llm/context.py)
  ├── 模型感知的窗口大小 + 5% 安全边距
  ├── ContextPriority 五级优先装箱 (CRITICAL→OPTIONAL)
  └── tail/head/middle 截断策略

语义分块 (src/llm/chunker.py + relevance.py)
  ├── tree-sitter AST 分块 + indent fallback
  ├── diff/conflict/security/reference 多因子 relevance scoring
  └── FULL/SIGNATURE/DROP 三级渲染

跨阶段记忆 (src/memory/)
  ├── MemoryStore: 不可变 API, 按 path/tags/type 查询
  ├── PhaseSummarizer: 阶段结束时提取 pattern
  └── 300 条合并 + 500 条上限 + superseded 淘汰
```

### 1.2 识别的四个关键问题

| # | 问题 | 影响 | 对应增强 |
|---|------|------|---------|
| P0 | **无文件依赖关系** | Planner 按文件独立分类，不知道 A 继承 B 应先合并 B；Conflict Analyst 不知道冲突波及范围 | 文件依赖图 |
| P1 | **记忆全量注入** | `_inject_memory()` 给每个 agent 广播全部 500 条记忆，浪费 token | 分层加载 |
| P2 | **置信度无语义** | `confidence: float` 裸数字，agent 不知道 0.8 代表"确定性提取"还是"启发式猜测" | 置信度语义化 |
| P3 | **无去重机制** | 同一个 pattern 可能被重复添加（不同文件触发相同规律） | 内容哈希去重 |

### 1.3 参考来源

| 问题 | 参考项目 | 借鉴的设计 |
|------|---------|-----------|
| P0 | Graphify | AST 提取 → 图节点/边 → 社区检测 |
| P1 | MemPalace | L0-L3 四层记忆栈，按需加载 |
| P2 | Graphify | EXTRACTED/INFERRED/AMBIGUOUS 置信度分级 |
| P3 | MemPalace | SHA256 确定性 ID + 去重检查 |

---

## 2. 增强目标

| 目标 | 量化指标 | 约束 |
|------|---------|------|
| 合并顺序感知文件依赖 | Planner 输出的合并批次中，依赖项排在被依赖项之前 | 不改变 ABCDE 分类逻辑 |
| 降低 agent 记忆注入的 token 消耗 | 平均每次 LLM 调用的 memory 部分 token 减少 60%+ | 不牺牲 agent 的决策质量 |
| agent 能区分记忆的可信程度 | prompt 中显示置信度标签 | 向后兼容已有 checkpoint |
| 消除重复记忆条目 | 合并后重复率 < 5% | 不影响不可变 API 风格 |

---

## 3. 总体架构

增强后的系统架构：

```
┌───────────────────────────────────────────────────────────┐
│                    Orchestrator                           │
│                                                           │
│  INIT 阶段 (新增):                                        │
│    ┌──────────────────┐    ┌───────────────────┐          │
│    │ FileClassifier   │    │ DependencyGrapher │ ← P0 新增│
│    │ (ABCDE 分类)      │    │ (AST→依赖图)      │          │
│    └──────────────────┘    └───────────────────┘          │
│                                                           │
│  记忆系统 (增强):                                          │
│    ┌──────────────────────────────────────┐               │
│    │ MemoryStore                          │               │
│    │  ├── LayeredLoader (L0/L1/L2)  ← P1 │               │
│    │  ├── ConfidenceLevel enum      ← P2 │               │
│    │  └── content_hash dedup        ← P3 │               │
│    └──────────────────────────────────────┘               │
│                                                           │
│  Agent 调用:                                              │
│    Planner → 读依赖图决定批次顺序 (P0)                     │
│    Executor → 分层加载相关记忆 (P1)                        │
│    Conflict Analyst → 查依赖图得波及范围 (P0)              │
│    Judge → 按置信度权衡 pattern (P2)                       │
│                                                           │
└───────────────────────────────────────────────────────────┘
```

---

## 4. P0: 文件依赖图

### 4.1 问题详解

当前 Planner 在 INIT 阶段只做 ABCDE 分类（A=仅本地修改, B=仅上游修改, C=双方修改, D=缺失, E=无变化），然后按风险分数排序。但它**不知道文件之间的依赖关系**。

```
当前合并顺序 (仅按风险):
  Batch 1: base_model.py (C-class, risk=0.4)
  Batch 2: user_service.py (C-class, risk=0.6)

实际上 user_service.py 继承了 base_model.py 的类：
  class UserService(BaseModel):  # 来自 base_model.py
      ...

正确顺序应该是:
  Batch 1: base_model.py (被依赖方，先合并)
  Batch 2: user_service.py (依赖方，后合并)
```

### 4.2 设计方案

#### 4.2.1 新增数据模型

文件: `src/models/dependency.py`（新建）

```python
from __future__ import annotations
from enum import StrEnum
from pydantic import BaseModel, Field


class DependencyKind(StrEnum):
    IMPORTS = "imports"
    INHERITS = "inherits"
    CALLS = "calls"
    USES_TYPE = "uses_type"


class ConfidenceLabel(StrEnum):
    EXTRACTED = "extracted"
    INFERRED = "inferred"
    AMBIGUOUS = "ambiguous"


class DependencyEdge(BaseModel, frozen=True):
    source_file: str
    target_file: str
    kind: DependencyKind
    source_symbol: str = ""
    target_symbol: str = ""
    confidence: ConfidenceLabel = ConfidenceLabel.EXTRACTED


class FileDependencyGraph(BaseModel, frozen=True):
    edges: tuple[DependencyEdge, ...] = ()
    file_count: int = 0

    def dependents_of(self, file_path: str) -> list[str]:
        """Return files that depend on the given file."""
        return list(
            {e.source_file for e in self.edges if e.target_file == file_path}
        )

    def dependencies_of(self, file_path: str) -> list[str]:
        """Return files that the given file depends on."""
        return list(
            {e.target_file for e in self.edges if e.source_file == file_path}
        )

    def topological_order(self, files: list[str]) -> list[str]:
        """Return files sorted so dependencies come before dependents."""
        # Kahn's algorithm on the subgraph induced by `files`
        file_set = set(files)
        in_degree: dict[str, int] = {f: 0 for f in files}
        adj: dict[str, list[str]] = {f: [] for f in files}

        for edge in self.edges:
            if edge.source_file in file_set and edge.target_file in file_set:
                adj[edge.target_file].append(edge.source_file)
                in_degree[edge.source_file] = in_degree.get(edge.source_file, 0) + 1

        queue = [f for f in files if in_degree[f] == 0]
        result: list[str] = []
        while queue:
            queue.sort()
            node = queue.pop(0)
            result.append(node)
            for neighbor in adj.get(node, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        # Append remaining files (cycle participants) at the end
        remaining = [f for f in files if f not in result]
        remaining.sort()
        return result + remaining

    def impact_radius(self, file_path: str, max_depth: int = 3) -> set[str]:
        """BFS to find all files within N hops that could be affected."""
        visited: set[str] = set()
        frontier = {file_path}
        for _ in range(max_depth):
            next_frontier: set[str] = set()
            for f in frontier:
                for dep in self.dependents_of(f):
                    if dep not in visited and dep != file_path:
                        next_frontier.add(dep)
            visited.update(next_frontier)
            frontier = next_frontier
            if not frontier:
                break
        return visited
```

#### 4.2.2 依赖提取器

文件: `src/tools/dependency_extractor.py`（新建）

复用已有的 `src/llm/chunker.py` 中的 tree-sitter 基础设施。

**核心逻辑**：

```python
# 两阶段处理

# 阶段 1: 扫描所有文件，建立全局符号表
# symbol_table = { "UserService": "src/models/user.py", ... }

# 阶段 2: 扫描 import 语句，匹配符号表，生成边
# "from src.models.user import UserService"
# → DependencyEdge(source="handler.py", target="user.py",
#                  kind=IMPORTS, confidence=EXTRACTED)
```

**处理步骤**：

1. 遍历 merge 涉及的所有文件（从 `state.file_categories` 获取）
2. 对每个文件调用 `ASTChunker.chunk()` 获取 IMPORT 类型的 chunk
3. 解析 import 路径，在符号表中查找目标文件
4. 如果 import 路径能精确解析 → `EXTRACTED`
5. 如果需要猜测（相对路径、动态导入）→ `INFERRED`
6. 如果完全无法解析 → 跳过（不生成 `AMBIGUOUS` 边，避免噪声）

**语言适配**（初期只支持 Python，后续扩展）：

| 语言 | import 语法 | 解析难度 |
|------|-----------|---------|
| Python | `from x.y import Z` | 中等（相对导入需要 package 上下文） |
| JavaScript/TS | `import { Z } from './y'` | 低（路径明确） |
| Go | `import "pkg/path"` | 低（路径即 package） |
| Java | `import com.x.y.Z` | 中等（需要 classpath 映射） |

#### 4.2.3 与 Orchestrator 集成

修改文件: `src/core/orchestrator.py`

在 INIT 阶段，在文件分类之后、Plan 生成之前：

```python
# 现有代码: classify_all_files(...)
# 新增: 构建依赖图
dep_graph = DependencyExtractor.extract(
    files=list(state.file_categories.keys()),
    repo_root=config.repo.root,
)
state.dependency_graph = dep_graph
```

#### 4.2.4 与 Planner 集成

修改文件: `src/agents/planner_agent.py`

将依赖图信息注入 Planner 的 prompt：

```python
# 在构建 prompt 时，加入依赖摘要
dep_summary = build_dependency_summary(state.dependency_graph, c_class_files)
# 输出类似:
# "以下 C-class 文件存在依赖关系:
#   base_model.py ← user_service.py (inherits)
#   base_model.py ← payment_service.py (imports)
#  建议合并顺序: base_model.py → user_service.py → payment_service.py"
```

#### 4.2.5 与 Conflict Analyst 集成

修改文件: `src/agents/conflict_analyst_agent.py`

在分析冲突时，查询波及范围：

```python
# 当 fileA 有冲突时
impacted = state.dependency_graph.impact_radius(file_path, max_depth=2)
# prompt 中加入:
# "该文件的冲突可能影响以下依赖它的文件: [impacted files]"
```

#### 4.2.6 MergeState 扩展

修改文件: `src/models/state.py`

```python
from src.models.dependency import FileDependencyGraph

class MergeState(BaseModel):
    # ... 现有字段 ...
    dependency_graph: FileDependencyGraph = Field(
        default_factory=FileDependencyGraph
    )
```

---

## 5. P1: 记忆分层加载

### 5.1 问题详解

当前 Orchestrator 在每个阶段结束后调用 `_inject_memory()`，把**全部** MemoryStore 广播给所有 agent：

```python
# src/core/orchestrator.py (现有逻辑)
def _inject_memory(self):
    for agent in self._agents:
        agent.set_memory_store(self._memory_store)  # 全部 500 条
```

假设有 300 条记忆，每条 ~100 tokens，那就是 30K tokens 的 memory 上下文。对于一个只需要处理 `src/utils/cache.py` 的 Executor 调用来说，其中 90% 的记忆是无关的。

### 5.2 设计方案：三层加载

参考 MemPalace 的 L0-L3 分层，简化为三层（L3 深度搜索在 merge 场景不需要）：

```
┌────────────────────────────────────────────┐
│ L0: Project Profile                        │  ~100-200 tokens
│ 始终加载                                    │  来自 codebase_profile
│ 内容: 语言, 框架, 文件数, 目录结构概要        │
├────────────────────────────────────────────┤
│ L1: Phase Essentials                       │  ~300-500 tokens
│ 始终加载                                    │  来自 PhaseSummary
│ 内容: 当前阶段的 top patterns + 上一阶段摘要  │
├────────────────────────────────────────────┤
│ L2: File-Relevant Context                  │  ~200-400 tokens
│ 按需加载                                    │  基于 query_by_path
│ 内容: 当前处理文件路径匹配的记忆条目           │
└────────────────────────────────────────────┘
```

#### 5.2.1 新增类

文件: `src/memory/layered_loader.py`（新建）

```python
from __future__ import annotations

from src.memory.models import MemoryEntry
from src.memory.store import MemoryStore

L0_MAX_TOKENS = 200
L1_MAX_ENTRIES = 10
L2_MAX_ENTRIES = 8


class LayeredMemoryLoader:
    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def load_for_agent(
        self,
        current_phase: str,
        file_paths: list[str] | None = None,
    ) -> str:
        """Build layered memory context string for a single LLM call."""
        sections: list[str] = []

        # L0: Project Profile (always)
        profile = self._store.codebase_profile
        if profile:
            l0 = "## Project Profile\n"
            for k, v in profile.items():
                l0 += f"- {k}: {v}\n"
            sections.append(l0)

        # L1: Phase Essentials (always)
        l1_parts: list[str] = []
        current_summary = self._store.get_phase_summary(current_phase)
        if current_summary and current_summary.patterns_discovered:
            l1_parts.append(
                "Key patterns: "
                + "; ".join(current_summary.patterns_discovered[:5])
            )

        prev_phase = _previous_phase(current_phase)
        if prev_phase:
            prev_summary = self._store.get_phase_summary(prev_phase)
            if prev_summary and prev_summary.key_decisions:
                l1_parts.append(
                    "Prior phase decisions: "
                    + "; ".join(prev_summary.key_decisions[:5])
                )

        if l1_parts:
            sections.append("## Phase Context\n" + "\n".join(l1_parts))

        # L2: File-Relevant (on demand)
        if file_paths:
            relevant = self._store.get_relevant_context(
                file_paths, max_entries=L2_MAX_ENTRIES
            )
            if relevant:
                l2 = "## Relevant Patterns\n"
                for entry in relevant:
                    label = entry.confidence_label.value if hasattr(
                        entry, 'confidence_label'
                    ) else "unknown"
                    l2 += f"- [{label}] {entry.content}\n"
                sections.append(l2)

        return "\n\n".join(sections) if sections else ""


_PHASE_ORDER = ["planning", "auto_merge", "conflict_analysis", "judge_review"]


def _previous_phase(phase: str) -> str | None:
    try:
        idx = _PHASE_ORDER.index(phase)
        return _PHASE_ORDER[idx - 1] if idx > 0 else None
    except ValueError:
        return None
```

#### 5.2.2 Agent 接口变更

修改文件: `src/agents/base_agent.py`

```python
# 新增方法
def get_memory_context(
    self, current_phase: str, file_paths: list[str] | None = None
) -> str:
    """Get layered memory context for the current LLM call."""
    if self._memory_store is None:
        return ""
    loader = LayeredMemoryLoader(self._memory_store)
    return loader.load_for_agent(current_phase, file_paths)
```

#### 5.2.3 Orchestrator 变更

修改文件: `src/core/orchestrator.py`

`_inject_memory()` 仍然广播 store 引用（agent 需要它做查询），但 agent 实际构建 prompt 时调用 `get_memory_context()` 而非全量序列化。

```python
# 无需改 _inject_memory()
# 改的是 agent 内部的 prompt 构建逻辑
```

#### 5.2.4 预估 Token 节省

| 场景 | 当前 (全量) | 分层后 | 节省比例 |
|------|-----------|--------|---------|
| Executor 处理 1 个文件 | ~8,500 tokens | ~900 tokens | 89% |
| Conflict Analyst 分析 3 个文件 | ~8,500 tokens | ~1,800 tokens | 79% |
| Judge 审查全局 | ~8,500 tokens | ~3,200 tokens | 62% |
| 平均 | ~8,500 tokens | ~1,900 tokens | **78%** |

---

## 6. P2: 置信度语义化

### 6.1 问题详解

当前 `MemoryEntry.confidence` 是一个 `float`（0.0-1.0）：

```python
# 当前: agent 看到的是
# [confidence=0.85] "vendor/ 目录以 B-class 为主"
# [confidence=0.80] "auth 相关文件可能有安全风险"
# agent 无法区分: 0.85 是"AST 确认的"还是"估计的"？
```

### 6.2 设计方案

#### 6.2.1 新增枚举

修改文件: `src/memory/models.py`

```python
class ConfidenceLevel(str, Enum):
    EXTRACTED = "extracted"     # AST/diff/git 确定性提取
    INFERRED = "inferred"      # 基于 pattern 统计推断 (>70% 文件符合)
    HEURISTIC = "heuristic"    # 启发式规则估计

# ConfidenceLevel → float 的默认映射
CONFIDENCE_DEFAULTS: dict[ConfidenceLevel, float] = {
    ConfidenceLevel.EXTRACTED: 0.95,
    ConfidenceLevel.INFERRED: 0.80,
    ConfidenceLevel.HEURISTIC: 0.60,
}
```

#### 6.2.2 扩展 MemoryEntry

修改文件: `src/memory/models.py`

```python
class MemoryEntry(BaseModel, frozen=True):
    # ... 现有字段 ...
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    confidence_level: ConfidenceLevel = Field(
        default=ConfidenceLevel.INFERRED
    )
```

**向后兼容**：`confidence_level` 有默认值 `INFERRED`，从旧 checkpoint 加载时不会报错。

#### 6.2.3 Summarizer 适配

修改文件: `src/memory/summarizer.py`

在创建 `MemoryEntry` 时根据来源设置 `confidence_level`：

| 来源 | confidence_level | 理由 |
|------|-----------------|------|
| 文件分类计数（A/B/C/D/E 统计） | EXTRACTED | 来自 git diff 确定性分类 |
| 目录 dominance pattern（>70%） | INFERRED | 基于统计推断 |
| 冲突类型计数 | EXTRACTED | 来自实际解析 |
| Judge 修复 pattern | HEURISTIC | 基于 LLM 判断 |

#### 6.2.4 Prompt 展示

Agent 在 prompt 中展示记忆时加入标签：

```
## Relevant Patterns
- [EXTRACTED] vendor/ 目录 87% 为 B-class，可安全自动合并
- [INFERRED] src/auth/ 目录冲突集中，建议人工审查
- [HEURISTIC] 该项目可能使用 Django ORM，注意 migration 文件
```

---

## 7. P3: 记忆去重

### 7.1 问题详解

当前 `MemoryStore.add_entry()` 不做去重检查：

```python
# 阶段 2 处理 vendor/a.js: 发现 pattern "vendor/ 以 B-class 为主"
# 阶段 2 处理 vendor/b.js: 再次发现 pattern "vendor/ 以 B-class 为主"
# → 两条内容相同的记忆都被存储
```

合并逻辑（`_consolidate_entries`）在超过 300 条时会合并同类条目，但在那之前重复数据已经占用了空间。

### 7.2 设计方案

#### 7.2.1 内容哈希生成

修改文件: `src/memory/models.py`

```python
import hashlib

class MemoryEntry(BaseModel, frozen=True):
    # ... 现有字段 ...
    content_hash: str = Field(default="")

    def model_post_init(self, __context: Any) -> None:
        if not self.content_hash:
            raw = f"{self.entry_type.value}:{self.phase}:{self.content}"
            computed = hashlib.sha256(raw.encode()).hexdigest()[:16]
            object.__setattr__(self, "content_hash", computed)
```

**哈希键的组成**：`entry_type + phase + content`。这意味着：
- 相同类型、相同阶段、相同内容 → 相同哈希（去重）
- 不同阶段的相同内容 → 不同哈希（保留，因为阶段不同有不同意义）

#### 7.2.2 MemoryStore 去重检查

修改文件: `src/memory/store.py`

```python
class MemoryStore:
    def add_entry(self, entry: MemoryEntry) -> MemoryStore:
        # 去重检查
        existing_hashes = {e.content_hash for e in self._memory.entries}
        if entry.content_hash in existing_hashes:
            return self  # 跳过重复，返回自身（不可变）

        entries = list(self._memory.entries) + [entry]
        # ... 后续合并逻辑不变 ...
```

#### 7.2.3 合并时的哈希更新

修改文件: `src/memory/store.py`

`_merge_entry_group()` 生成的合并条目会自动计算新的 `content_hash`（因为 content 变了）。

---

## 8. 实施计划

### 8.1 分阶段排期

```
Phase A: 基础模型扩展 (P2 + P3)                    ✅ DONE (22 tests)
  ├── A1: ConfidenceLevel 枚举 + MemoryEntry 扩展   ✅
  ├── A2: content_hash 字段 + model_post_init       ✅
  ├── A3: MemoryStore.add_entry 去重逻辑            ✅
  ├── A4: PhaseSummarizer 适配 confidence_level     ✅
  └── A5: 单元测试 (models + store)                 ✅

Phase B: 记忆分层加载 (P1)                          ✅ DONE (26 tests)
  ├── B1: LayeredMemoryLoader 类                    ✅
  ├── B2: BaseAgent.get_memory_context() 方法       ✅
  ├── B3: 各 agent prompt builder 适配              ✅
  └── B4: 单元测试 + 集成测试                       ✅

Phase C: 文件依赖图 (P0)                            ✅ DONE (39 tests)
  ├── C1: dependency.py 数据模型                    ✅
  ├── C2: DependencyExtractor (Python import 解析)  ✅
  ├── C3: MergeState 扩展                           ✅
  ├── C4: Planner prompt 注入依赖摘要               ✅ (build_dependency_summary)
  ├── C5: Conflict Analyst 波及范围分析              ✅ (build_impact_summary)
  ├── C6: 单元测试 + 集成测试                       ✅
  └── C7: (可选) JS/TS import 解析扩展              ⏳ 待后续

Phase D: 端到端验证                                  ✅ DONE (16 tests)
  ├── D1: 集成测试 (模拟完整 merge 流程)             ✅
  ├── D2: Token 消耗对比测试                        ✅
  └── D3: 文档更新                                  ✅

Total: 103 new tests, all passing. mypy strict: 0 errors.
```

### 8.2 文件变更清单

| 文件 | 变更类型 | 相关阶段 |
|------|---------|---------|
| `src/models/dependency.py` | **新建** | Phase C |
| `src/tools/dependency_extractor.py` | **新建** | Phase C |
| `src/memory/layered_loader.py` | **新建** | Phase B |
| `src/memory/models.py` | 修改 | Phase A |
| `src/memory/store.py` | 修改 | Phase A |
| `src/memory/summarizer.py` | 修改 | Phase A |
| `src/models/state.py` | 修改 | Phase C |
| `src/agents/base_agent.py` | 修改 | Phase B |
| `src/agents/executor_agent.py` | 修改 | Phase B |
| `src/agents/conflict_analyst_agent.py` | 修改 | Phase B, C |
| `src/agents/judge_agent.py` | 修改 | Phase B |
| `src/agents/planner_agent.py` | 修改 | Phase C |
| `src/core/orchestrator.py` | 修改 | Phase C |
| `tests/unit/test_dependency.py` | **新建** | Phase C |
| `tests/unit/test_layered_loader.py` | **新建** | Phase B |
| `tests/unit/test_memory_dedup.py` | **新建** | Phase A |
| `tests/unit/test_confidence_level.py` | **新建** | Phase A |
| `tests/integration/test_enhanced_pipeline.py` | **新建** | Phase D |

---

## 9. 测试方案

### 9.1 测试策略总览

```
┌──────────────────────────────────────────────┐
│             测试金字塔                        │
│                                              │
│              /\        E2E 测试              │
│             /  \       (1-2 个完整流程)        │
│            /    \                             │
│           /──────\     集成测试               │
│          /        \    (模块间交互)            │
│         /──────────\                          │
│        /            \  单元测试               │
│       /              \ (每个函数/类)           │
│      /────────────────\                       │
│                                              │
│  覆盖率目标: 80%+                             │
└──────────────────────────────────────────────┘
```

### 9.2 Phase A 测试：基础模型扩展

#### 9.2.1 ConfidenceLevel 单元测试

文件: `tests/unit/test_confidence_level.py`

```python
# 测试 1: ConfidenceLevel 枚举值正确
def test_confidence_level_values():
    assert ConfidenceLevel.EXTRACTED == "extracted"
    assert ConfidenceLevel.INFERRED == "inferred"
    assert ConfidenceLevel.HEURISTIC == "heuristic"

# 测试 2: MemoryEntry 默认 confidence_level
def test_memory_entry_default_confidence_level():
    entry = MemoryEntry(
        entry_type=MemoryEntryType.PATTERN,
        phase="planning",
        content="test",
    )
    assert entry.confidence_level == ConfidenceLevel.INFERRED

# 测试 3: 显式设置 confidence_level
def test_memory_entry_explicit_confidence_level():
    entry = MemoryEntry(
        entry_type=MemoryEntryType.PATTERN,
        phase="planning",
        content="test",
        confidence_level=ConfidenceLevel.EXTRACTED,
    )
    assert entry.confidence_level == ConfidenceLevel.EXTRACTED
    assert entry.confidence == 0.8  # float 不受影响

# 测试 4: 旧格式 JSON 反序列化 (向后兼容)
def test_backward_compatible_deserialization():
    old_json = {
        "entry_id": "abc",
        "entry_type": "pattern",
        "phase": "planning",
        "content": "test",
        "confidence": 0.9,
        # 没有 confidence_level 字段
    }
    entry = MemoryEntry.model_validate(old_json)
    assert entry.confidence_level == ConfidenceLevel.INFERRED  # 默认值
```

#### 9.2.2 去重单元测试

文件: `tests/unit/test_memory_dedup.py`

```python
# 测试 1: content_hash 自动生成
def test_content_hash_generated():
    entry = MemoryEntry(
        entry_type=MemoryEntryType.PATTERN,
        phase="planning",
        content="vendor/ is mostly B-class",
    )
    assert len(entry.content_hash) == 16
    assert entry.content_hash != ""

# 测试 2: 相同内容产生相同哈希
def test_same_content_same_hash():
    e1 = MemoryEntry(
        entry_type=MemoryEntryType.PATTERN,
        phase="planning",
        content="vendor/ is mostly B-class",
    )
    e2 = MemoryEntry(
        entry_type=MemoryEntryType.PATTERN,
        phase="planning",
        content="vendor/ is mostly B-class",
    )
    assert e1.content_hash == e2.content_hash

# 测试 3: 不同阶段的相同内容产生不同哈希
def test_different_phase_different_hash():
    e1 = MemoryEntry(
        entry_type=MemoryEntryType.PATTERN,
        phase="planning",
        content="vendor/ is mostly B-class",
    )
    e2 = MemoryEntry(
        entry_type=MemoryEntryType.PATTERN,
        phase="auto_merge",
        content="vendor/ is mostly B-class",
    )
    assert e1.content_hash != e2.content_hash

# 测试 4: MemoryStore 跳过重复条目
def test_store_dedup_skips_duplicate():
    entry = MemoryEntry(
        entry_type=MemoryEntryType.PATTERN,
        phase="planning",
        content="same content",
    )
    store = MemoryStore()
    store = store.add_entry(entry)
    store = store.add_entry(entry)  # 重复
    assert store.entry_count == 1

# 测试 5: 不同内容不被去重
def test_store_keeps_different_entries():
    e1 = MemoryEntry(
        entry_type=MemoryEntryType.PATTERN,
        phase="planning",
        content="pattern A",
    )
    e2 = MemoryEntry(
        entry_type=MemoryEntryType.PATTERN,
        phase="planning",
        content="pattern B",
    )
    store = MemoryStore()
    store = store.add_entry(e1)
    store = store.add_entry(e2)
    assert store.entry_count == 2
```

### 9.3 Phase B 测试：分层加载

文件: `tests/unit/test_layered_loader.py`

```python
# 测试 1: L0 始终包含 codebase_profile
def test_l0_always_included():
    store = MemoryStore()
    store = store.set_codebase_profile("language", "python")
    store = store.set_codebase_profile("framework", "django")

    loader = LayeredMemoryLoader(store)
    result = loader.load_for_agent("planning")

    assert "python" in result
    assert "django" in result

# 测试 2: L1 包含当前阶段的 phase summary
def test_l1_includes_phase_summary():
    store = MemoryStore()
    summary = PhaseSummary(
        phase="planning",
        patterns_discovered=["vendor/ is B-class dominant"],
    )
    store = store.record_phase_summary(summary)

    loader = LayeredMemoryLoader(store)
    result = loader.load_for_agent("planning")

    assert "vendor/" in result

# 测试 3: L2 只在提供 file_paths 时加载
def test_l2_only_with_file_paths():
    entry = MemoryEntry(
        entry_type=MemoryEntryType.PATTERN,
        phase="planning",
        content="auth module pattern",
        file_paths=["src/auth/handler.py"],
    )
    store = MemoryStore()
    store = store.add_entry(entry)

    loader = LayeredMemoryLoader(store)

    # 无 file_paths → 无 L2 内容
    result_no_paths = loader.load_for_agent("auto_merge")
    assert "auth module" not in result_no_paths

    # 有匹配 file_paths → 有 L2 内容
    result_with_paths = loader.load_for_agent(
        "auto_merge", file_paths=["src/auth/handler.py"]
    )
    assert "auth module" in result_with_paths

# 测试 4: 空 store 返回空字符串
def test_empty_store_returns_empty():
    loader = LayeredMemoryLoader(MemoryStore())
    result = loader.load_for_agent("planning")
    assert result == ""

# 测试 5: L1 包含上一阶段的 key_decisions
def test_l1_includes_previous_phase_decisions():
    store = MemoryStore()
    summary = PhaseSummary(
        phase="planning",
        key_decisions=["Plan generated with 3 batches"],
    )
    store = store.record_phase_summary(summary)

    loader = LayeredMemoryLoader(store)
    result = loader.load_for_agent("auto_merge")  # 下一阶段

    assert "3 batches" in result
```

### 9.4 Phase C 测试：文件依赖图

#### 9.4.1 数据模型测试

文件: `tests/unit/test_dependency.py`

```python
# 测试 1: DependencyEdge 不可变
def test_dependency_edge_frozen():
    edge = DependencyEdge(
        source_file="a.py",
        target_file="b.py",
        kind=DependencyKind.IMPORTS,
    )
    with pytest.raises(ValidationError):
        edge.source_file = "c.py"

# 测试 2: dependents_of 返回正确结果
def test_dependents_of():
    graph = FileDependencyGraph(edges=(
        DependencyEdge(
            source_file="handler.py",
            target_file="base.py",
            kind=DependencyKind.INHERITS,
        ),
        DependencyEdge(
            source_file="service.py",
            target_file="base.py",
            kind=DependencyKind.IMPORTS,
        ),
    ))
    deps = graph.dependents_of("base.py")
    assert set(deps) == {"handler.py", "service.py"}

# 测试 3: dependencies_of 返回正确结果
def test_dependencies_of():
    graph = FileDependencyGraph(edges=(
        DependencyEdge(
            source_file="handler.py",
            target_file="base.py",
            kind=DependencyKind.INHERITS,
        ),
        DependencyEdge(
            source_file="handler.py",
            target_file="utils.py",
            kind=DependencyKind.IMPORTS,
        ),
    ))
    deps = graph.dependencies_of("handler.py")
    assert set(deps) == {"base.py", "utils.py"}

# 测试 4: topological_order 基本排序
def test_topological_order_basic():
    graph = FileDependencyGraph(edges=(
        DependencyEdge(
            source_file="c.py",
            target_file="b.py",
            kind=DependencyKind.IMPORTS,
        ),
        DependencyEdge(
            source_file="b.py",
            target_file="a.py",
            kind=DependencyKind.IMPORTS,
        ),
    ))
    order = graph.topological_order(["a.py", "b.py", "c.py"])
    assert order.index("a.py") < order.index("b.py")
    assert order.index("b.py") < order.index("c.py")

# 测试 5: topological_order 处理循环依赖
def test_topological_order_cycle():
    graph = FileDependencyGraph(edges=(
        DependencyEdge(
            source_file="a.py", target_file="b.py",
            kind=DependencyKind.IMPORTS,
        ),
        DependencyEdge(
            source_file="b.py", target_file="a.py",
            kind=DependencyKind.IMPORTS,
        ),
    ))
    order = graph.topological_order(["a.py", "b.py"])
    # 循环依赖时两个文件都应该出现
    assert set(order) == {"a.py", "b.py"}

# 测试 6: impact_radius BFS 深度限制
def test_impact_radius_depth():
    graph = FileDependencyGraph(edges=(
        DependencyEdge(
            source_file="b.py", target_file="a.py",
            kind=DependencyKind.IMPORTS,
        ),
        DependencyEdge(
            source_file="c.py", target_file="b.py",
            kind=DependencyKind.IMPORTS,
        ),
        DependencyEdge(
            source_file="d.py", target_file="c.py",
            kind=DependencyKind.IMPORTS,
        ),
    ))
    # depth=1: 只找直接依赖者
    r1 = graph.impact_radius("a.py", max_depth=1)
    assert r1 == {"b.py"}

    # depth=2: 两跳内
    r2 = graph.impact_radius("a.py", max_depth=2)
    assert r2 == {"b.py", "c.py"}

# 测试 7: 空图正常工作
def test_empty_graph():
    graph = FileDependencyGraph()
    assert graph.dependents_of("any.py") == []
    assert graph.dependencies_of("any.py") == []
    assert graph.topological_order(["a.py"]) == ["a.py"]
    assert graph.impact_radius("a.py") == set()
```

#### 9.4.2 依赖提取器测试

文件: `tests/unit/test_dependency_extractor.py`

```python
# 测试 1: Python 绝对 import 解析
def test_extract_python_absolute_import():
    files = {
        "src/models/user.py": "class UserModel:\n    pass",
        "src/services/user_service.py": (
            "from src.models.user import UserModel\n"
            "\n"
            "class UserService:\n"
            "    def get(self) -> UserModel:\n"
            "        pass"
        ),
    }
    graph = DependencyExtractor.extract_from_sources(files)
    edges = [e for e in graph.edges if e.source_file == "src/services/user_service.py"]
    assert any(
        e.target_file == "src/models/user.py"
        and e.kind == DependencyKind.IMPORTS
        for e in edges
    )

# 测试 2: Python 相对 import 解析
def test_extract_python_relative_import():
    files = {
        "src/utils/helpers.py": "def helper(): pass",
        "src/utils/main.py": "from .helpers import helper",
    }
    graph = DependencyExtractor.extract_from_sources(files)
    assert any(
        e.source_file == "src/utils/main.py"
        and e.target_file == "src/utils/helpers.py"
        for e in graph.edges
    )

# 测试 3: 无法解析的 import 不生成边
def test_unresolvable_import_no_edge():
    files = {
        "main.py": "import nonexistent_package",
    }
    graph = DependencyExtractor.extract_from_sources(files)
    assert len(graph.edges) == 0

# 测试 4: 标准库 import 不生成边
def test_stdlib_import_skipped():
    files = {
        "main.py": "import os\nimport sys\nfrom pathlib import Path",
    }
    graph = DependencyExtractor.extract_from_sources(files)
    assert len(graph.edges) == 0

# 测试 5: 置信度标签正确设置
def test_confidence_labels():
    files = {
        "models.py": "class Base: pass",
        "service.py": "from models import Base",  # 可能精确解析
    }
    graph = DependencyExtractor.extract_from_sources(files)
    for edge in graph.edges:
        assert edge.confidence in (
            ConfidenceLabel.EXTRACTED,
            ConfidenceLabel.INFERRED,
        )
```

### 9.5 Phase D 测试：端到端验证

文件: `tests/integration/test_enhanced_pipeline.py`

```python
# 测试 1: 完整流程 — 依赖图影响合并顺序
def test_dependency_aware_merge_order():
    """Verify that files are merged in dependency order."""
    # 构造: C 依赖 B, B 依赖 A
    config = make_test_config(...)
    state = make_test_state(
        file_categories={
            "a.py": FileChangeCategory.BOTH_CHANGED,
            "b.py": FileChangeCategory.BOTH_CHANGED,
            "c.py": FileChangeCategory.BOTH_CHANGED,
        }
    )
    # 注入依赖关系
    state.dependency_graph = FileDependencyGraph(edges=(
        DependencyEdge(source_file="b.py", target_file="a.py",
                       kind=DependencyKind.IMPORTS),
        DependencyEdge(source_file="c.py", target_file="b.py",
                       kind=DependencyKind.IMPORTS),
    ))

    # 运行 planner
    # 验证生成的 plan 中 a.py 在 b.py 之前, b.py 在 c.py 之前

# 测试 2: 分层加载 token 消耗对比
def test_layered_loading_reduces_tokens():
    """Verify token reduction compared to full memory injection."""
    store = build_store_with_300_entries()

    # 全量: 估算所有 entry 的 token
    full_tokens = estimate_tokens(
        "\n".join(e.content for e in store._memory.entries)
    )

    # 分层: 只加载 L0+L1+L2
    loader = LayeredMemoryLoader(store)
    layered_text = loader.load_for_agent(
        "auto_merge", file_paths=["src/auth/handler.py"]
    )
    layered_tokens = estimate_tokens(layered_text)

    assert layered_tokens < full_tokens * 0.5  # 至少节省 50%

# 测试 3: 去重 + 置信度在完整流程中正常工作
def test_dedup_and_confidence_in_pipeline():
    """Verify dedup and confidence levels through summarizer."""
    state = make_state_with_file_categories(500_files_in_vendor)

    summarizer = PhaseSummarizer()
    summary, entries = summarizer.summarize_planning(state)

    # 验证没有重复
    hashes = [e.content_hash for e in entries]
    assert len(hashes) == len(set(hashes))

    # 验证置信度标签
    for entry in entries:
        assert entry.confidence_level in (
            ConfidenceLevel.EXTRACTED,
            ConfidenceLevel.INFERRED,
        )

# 测试 4: 冲突波及范围正确计算
def test_conflict_impact_radius():
    """When file has conflict, dependents are identified."""
    graph = FileDependencyGraph(edges=(
        DependencyEdge(source_file="b.py", target_file="a.py",
                       kind=DependencyKind.IMPORTS),
        DependencyEdge(source_file="c.py", target_file="a.py",
                       kind=DependencyKind.IMPORTS),
    ))
    impacted = graph.impact_radius("a.py", max_depth=1)
    assert impacted == {"b.py", "c.py"}

# 测试 5: checkpoint 保存/恢复后增强字段完整
def test_checkpoint_preserves_enhanced_fields():
    """Verify new fields survive serialization round-trip."""
    state = MergeState(config=make_test_config())
    state.dependency_graph = FileDependencyGraph(edges=(
        DependencyEdge(
            source_file="a.py", target_file="b.py",
            kind=DependencyKind.IMPORTS,
            confidence=ConfidenceLabel.EXTRACTED,
        ),
    ))
    state.memory.entries = [
        MemoryEntry(
            entry_type=MemoryEntryType.PATTERN,
            phase="planning",
            content="test pattern",
            confidence_level=ConfidenceLevel.EXTRACTED,
        )
    ]

    # serialize → deserialize
    json_data = state.model_dump(mode="json")
    restored = MergeState.model_validate(json_data)

    assert len(restored.dependency_graph.edges) == 1
    assert restored.dependency_graph.edges[0].confidence == ConfidenceLabel.EXTRACTED
    assert restored.memory.entries[0].confidence_level == ConfidenceLevel.EXTRACTED
    assert restored.memory.entries[0].content_hash != ""
```

### 9.6 测试覆盖率目标

| 模块 | 目标覆盖率 | 关键测试点 |
|------|-----------|-----------|
| `src/models/dependency.py` | 95%+ | 所有图操作（拓扑排序、BFS、边界情况） |
| `src/tools/dependency_extractor.py` | 85%+ | Python import 解析、标准库过滤、容错 |
| `src/memory/layered_loader.py` | 90%+ | 三层加载、空 store、边界情况 |
| `src/memory/models.py` (新增部分) | 95%+ | ConfidenceLevel、content_hash、序列化 |
| `src/memory/store.py` (去重部分) | 90%+ | 去重、hash 冲突、性能 |
| **总体** | **80%+** | 与项目标准一致 |

### 9.7 TDD 工作流

每个 Phase 严格遵循 Red-Green-Refactor：

```
Phase A 示例:

1. RED: 写 test_confidence_level_values() → 运行 → 失败 (ConfidenceLevel 不存在)
2. GREEN: 在 models.py 中定义 ConfidenceLevel 枚举 → 运行 → 通过
3. REFACTOR: 确认命名一致性

4. RED: 写 test_content_hash_generated() → 运行 → 失败 (content_hash 字段不存在)
5. GREEN: 在 MemoryEntry 中添加 content_hash + model_post_init → 运行 → 通过
6. REFACTOR: 确认 frozen model 的 object.__setattr__ 用法

7. RED: 写 test_store_dedup_skips_duplicate() → 运行 → 失败 (add_entry 无去重)
8. GREEN: 在 add_entry 中添加哈希检查 → 运行 → 通过
9. REFACTOR: 优化哈希集合的构建方式
```

---

## 10. 风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| tree-sitter 在某些环境不可用 | 中 | 依赖图为空，退化为当前行为 | `DependencyExtractor` 返回空 `FileDependencyGraph`，所有功能优雅降级 |
| Python 相对 import 解析不准确 | 中 | 部分依赖边缺失 | 设置 `INFERRED` 标签，不影响正确性 |
| 循环依赖导致拓扑排序失败 | 低 | 部分文件顺序随机 | `topological_order` 已处理：循环参与者追加到结果末尾 |
| `content_hash` 碰撞 | 极低 | 误跳过不同内容的记忆 | SHA256 前 16 字符碰撞率 ~10^-19，实际不可能 |
| 旧 checkpoint 缺少新字段 | 确定 | 反序列化时字段缺失 | 所有新字段都有 `default` 值，Pydantic 自动填充 |
| 分层加载导致 agent 缺少关键记忆 | 低 | 决策质量下降 | L1 始终包含 top patterns；L2 路径匹配范围足够宽（前缀匹配） |

### 回滚策略

每个 Phase 独立，可以单独回滚：

- **P2/P3 回滚**：删除 `ConfidenceLevel` 枚举和 `content_hash` 字段，`confidence_level` 有默认值不影响旧 checkpoint
- **P1 回滚**：`LayeredMemoryLoader` 是新类，不用就行；agent 回退到全量注入
- **P0 回滚**：`dependency_graph` 字段默认为空 `FileDependencyGraph`，Planner/Analyst 检测到空图时走当前逻辑
