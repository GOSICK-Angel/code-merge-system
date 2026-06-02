# 大规模文件处理优化方案

> **定位**：把 CodeMergeSystem 从"1000 文件量级勉强能跑"升级到"对标 SWE-agent / Devin / Cursor 等生产级 agent 系统的稳定性与可观测性"。
>
> **触发**：2026-05-17 forgejo 测试（1822 文件、上游 gitea/main、fork merge/test1）出现 AUTO_MERGE 半小时 0 patches 的卡死状态，根因为 **executor.build_rebuttal 把 200+ JudgeIssue 拼进一个 prompt** 反复 Anthropic transport timeout。
>
> **范围**：本方案给出**完整的**架构优化目标和实施清单。不分中长期；每一个单元都属于"必须做"范畴，依赖图决定先后顺序。
>
> **不在范围**：单一文件内的 AST 切分细节（已由 `src/llm/prompt_builders.py` 的 `build_staged_content` 解决）；prompt 内容微调；模型路由策略。
>
> **写入时间**：2026-05-18（feat/web 分支，HEAD=4826a6e）。

---

## 1. 触发场景：forgejo 1822 文件回归

### 1.1 实测现象

```
2026-05-17 23:15:05  state_transition: plan_reviewing → auto_merging
2026-05-17 23:33:14  judge review_batch: 1594 safe (deterministic), 223 risky → 28 LLM calls
2026-05-17 23:33:14  executor LLM call: prompt_chars=54179, est_tokens=15479
2026-05-17 23:37:47  Transport error: Request timed out (272.6s)        ← Anthropic 长请求上限
2026-05-17 23:42:22  Transport error (272.6s) attempt=2
2026-05-17 23:47:00  Transport error (272.4s) attempt=3 → build_rebuttal failed
                     → 单文件 repair fallback, 也 272s timeout
... 累计 ~48 分钟 stuck，applied_patches=0, file_decision_records=0
```

### 1.2 根因层级

| 层级 | 现象 | 当前方案 |
|---|---|---|
| **L0 单 prompt 过大** | `build_rebuttal` 222 issues × ~270 chars → 54KB prompt + 必须输出 200+ JSON decisions 触 8K max_tokens | ✅ **已修**（commit `844defc`，切到 25 issues/chunk + 并发） |
| **L0 单 prompt 过大** | `planner._classify_batch` 500 files × ~250 chars → 125KB prompt + 输出 500 条 JSON 接近 long-request 阈值 | ✅ **已修**（commit `4826a6e`，切到 100 files/chunk + 并发） |
| **L1 单文件过大** | `conflict_analyst.analyze_file` 大文件路径上 staged_content 挂在 memory_store gate 上，缺 memory 时裸塞；超大 staged 无 chunking fallback | 🚧 **PR2 待做**（本文 §5.1） |
| **L2 失败模式** | LLM transport timeout 后只会 retry 3 次，然后继续往后跑；没有 budget cap，烧钱无上限 | 🚧 **待做**（本文 §5.2） |
| **L3 跨 run 浪费** | 同一 fork + 同一 upstream 第二次 run 重跑全部 LLM 调用，无缓存 | 🚧 **待做**（本文 §5.3） |
| **L4 并发盲点** | `parallel_file_concurrency` 是固定整数，对 provider RPM 无感知；大仓库一旦 fan-out 必踩 429 | 🚧 **待做**（本文 §5.4） |
| **L5 工程实践** | 多 agent 并发改同一 fork_ref；plan 不可逐文件编辑；worktree 隔离默认关闭 | 🚧 **待做**（本文 §5.5 / §5.6 / §5.7） |

---

## 2. 行业对照：10 个生产级 agent 系统画像

### 2.1 详细画像

| 系统 | 核心架构 | 关键数字 | 单一最可借鉴的设计决策 | 主要源 |
|---|---|---|---|---|
| **SWE-agent**（Princeton, NeurIPS'24） | 薄 ACI 暴露 `view/search/edit` 三件套；每 instance 跑在硬美元上限下，超 budget 抛 `CostLimitExceededError` → **autosubmit 部分结果** | 默认 **`per_instance_cost_limit=$3.00`**；可选 `per_instance_call_limit` | 超 budget 后**强制落盘已有进度并自动转人工**，绝不静默 retry 到死 | [config docs](https://swe-agent.com/latest/reference/agent_config/), [NeurIPS paper](https://proceedings.neurips.cc/paper_files/paper/2024/file/5a7c947568c1b1328ccc5230172e1e7c-Paper-Conference.pdf) |
| **Devin / Cognition** | 每 session 跑在独立 microVM；**hypervisor 级 snapshot**（内存 + 进程 + FS）实现"暂停等 CI / 重启字节相同" | 1 ACU ≈ 15min；**默认每 session 10 ACU 硬上限**；$2.00-2.25/ACU；并发 thousands of VMs | **默认上限有立场，不是软警告** | [building cloud agents](https://cognition.ai/blog/what-we-learned-building-cloud-agents), [Devin manages Devins](https://cognition.ai/blog/devin-can-now-manage-devins) |
| **Cursor** | 客户端算 **Merkle tree** of file hashes；只 re-embed 分叉分支；团队 simhash 共享 cache | 跨用户 chunk hash 重叠 **92%**；chunks 按 content 寻址 | **Merkle 差分**——同 commit 二次 run 几乎零成本 | [secure indexing](https://cursor.com/blog/secure-codebase-indexing), [engineer's codex](https://read.engineerscodex.com/p/how-cursor-indexes-codebases-fast) |
| **Continue.dev** | SQLite `tag_catalog` 维护 `(path, branch, artifact_id) → (mtime, hash)`，**delete-add-remove diff** 增量化 | 5MB 文件硬上限；`.gitignore` + `.continueignore` + 敏感模式 denylist | **复合主键缓存**——key = `(path, branch, artifact_id)`，缓存 LLM 结果 | [DeepWiki 3.4](https://deepwiki.com/continuedev/continue/3.4-codebase-indexing), [accuracy limits](https://blog.continue.dev/accuracy-limits-of-codebase-retrieval/) |
| **OpenHands** | 经理 agent 建 subtask 依赖图；每 engineer-agent 拿独立 **git worktree**；branch 完成后 merge 回 | SWE-Bench Verified **72.8%**（Sonnet 4.5）；`MAX_ITERATIONS≈100`, `LLM_NUM_RETRIES≈8` + 硬成本 cutoff 三件套 | **per-agent git worktree** 是并发的物理隔离 | [arXiv 2511.03690](https://arxiv.org/html/2511.03690v1), [async SWE blog](https://www.openhands.dev/blog/asynchronous-software-engineering-agents) |
| **GitHub Copilot Workspace** | NL task → 可编辑 **spec** → 可编辑 **per-file plan**（action + steps） → 顺序生成 diff，queued/in-progress/done 指示器 | 文件选择走 hybrid LLM + 传统 code search | **per-file action-typed plan** 是一等公民产物，人工可改后再执行 | [user manual](https://github.com/githubnext/copilot-workspace-user-manual/blob/main/overview.md) |
| **Claude Code 子 agent** | 父 agent 通过 Task 工具 spawn 子 agent，每个独立 context window；协调走文件系统 | 实际并发上限 **~10**；规则："Parallel only works when agents touch different files" | **文件 disjoint contract** 在 fan-out 前显式校验 | [sub-agents docs](https://code.claude.com/docs/en/sub-agents) |
| **LangGraph** | `Send()` 把一个 node 扇出成 N 个并行实例，自动收敛 merge；`max_concurrency` 可设置 | 50 docs 串行 10min → 并发 **12s**；200 并发对 60 RPM key → 立即 429 | **max_concurrency 绑定 provider RPM/TPM**，不是固定整数 | [map-reduce how-to](https://langchain-ai.github.io/langgraphjs/how-tos/map-reduce/) |
| **Aider** | tree-sitter 提取 symbol → 个性化 PageRank 排序 → 按 `--map-tokens` 截断 | 默认 `--map-tokens 1024`，可调 8k | **map 按需重建 + 按 mtime+hash 缓存**（仅文件级，全图 rebuild 是公开瓶颈） | [repomap blog](https://aider.chat/2023/10/22/repomap.html), [RFC 增量索引](https://github.com/orgs/sheeptechnologies/discussions/4) |
| **Sweep AI** | GitHub issue → plan 作 comment 发出 → 沙箱多步编码 → PR；多 ticket **完全并发** | 公开数字稀缺；工程 blog 已下线 | "plan as reviewable artifact before any code runs"——干预是一等公民 | [blog index](https://blog.sweep.dev/) |

### 2.2 跨系统共识

来自 Addy Osmani 长跑 agent 综述（产业级调研）：

| 共识 | 原文意 | 我们的解读 |
|---|---|---|
| **预算 / 熔断 / 硬上限是自己造的** | "self-implemented across all platforms; not built-in anywhere" | 业内无框架原生提供——做对了能直接对标 SWE-agent / Devin |
| **append-only 事件日志 session** | 新容器调 `wake(sessionId)` 即可重建 | 我们已有 checkpoint，粒度可以再细 |
| **Ralph Loop**（外部 progress.txt / AGENTS.md）每轮重读 | 避免把 context window 当 state | 我们的 MERGE_PLAN.md 已经类似，可以扩展 |
| **Done-condition 文件预先写入**（feature-list.json） | 防止跑到一半重定义目标 | 我们的 plan 已经做到了；编辑时机可以前置 |

源：[Long-running Agents — Addy Osmani](https://addyo.substack.com/p/long-running-agents)

---

## 3. 当前架构 gap 分析

| 维度 | 当前状态 | 行业最佳 | 差距 |
|---|---|---|---|
| 单 prompt 大小（issues） | ✅ 切 25/chunk 并发（已 commit） | SWE-agent: 极少超 8K input | 0 |
| 单 prompt 大小（files） | ✅ 切 100/chunk 并发（已 commit） | 同上 | 0 |
| 单文件超大 staged | ❌ memory_store gate 后裸塞 | Cursor / Aider 严格预算 | **高** |
| Per-run cost cap | ⚠️ 字段存在（`max_cost_usd`），未默认值，未与 budget exceeded 行为关联 | SWE-agent $3, Devin 10 ACU 默认 | **高** |
| Budget exceeded 行为 | 累积 LLM 错误 → eventual fallback | autosubmit + 转 AWAITING_HUMAN | **高** |
| 跨 run 缓存 | ❌ 完全无 | Cursor Merkle, Continue tag_catalog | 中-高 |
| 并发上限 | 固定整数 `parallel_file_concurrency` | RPM/TPM-aware | 中 |
| Worktree 隔离 | ✅ 字段存在；默认 `enable_working_branch: false` | OpenHands 默认开 | 低-中 |
| 文件 disjoint 校验 | ❌ chunk fan-out 不校验 | Claude Code contract | 中 |
| Per-file editable plan | ⚠️ 部分（MERGE_PLAN_*.md 有 risk 无 steps） | Copilot Workspace 完整 | 中 |
| 进度上报 | ✅ Web UI 实时 | Copilot Workspace queued/in-progress/done | 0 |
| Resumability | ✅ 单 rolling checkpoint | OpenHands per-event log | 低 |

**关键洞察**：除了"单 prompt 大小"两项已修，其余 8 项中 5 项被行业普遍重视（高/中-高/中），且**每一项都有明确的对标实现可参考**。

---

## 4. 优化方案概览

### 4.1 单元清单与依赖

7 个单元，按依赖图排序（U2、U4 在 U1 完成后任意顺序）：

```
U1 conflict_analyst chunked analysis  ──┐
                                        ├──► U6 per-file editable plan v2
U7 worktree isolation defaults ─────────┘            │
                                                     │
U2 per-run budget + autosubmit ──────┐               │
                                     ├──► U3 cross-run cache
U4 RPM-aware concurrency ────────────┤               │
                                     │               │
U5 file-disjointness contract  ──────┘               │
                                                     ▼
                                              完整生产化形态
```

**说明**：
- **U1** 是本会话上一轮已设计但未实施的 PR2，逻辑完整
- **U2/U4/U5** 是基础设施层，谁先做都行；建议 U2 优先（用户痛点最显性）
- **U3** 依赖 U2 的 budget tracker 拿到的"已花费"语义来做缓存命中率决策
- **U6** 是用户面优化，依赖 U1（chunked analysis 需要在 plan 中可见）
- **U7** 是独立优化，无依赖

### 4.2 单元尺寸估算

| Unit | 改动文件数 | LOC（净增） | 新测试数 | 预估工时 |
|---|---|---|---|---|
| U1 conflict_analyst chunked | 4 | ~250 | 6-8 | 1 天 |
| U2 budget + autosubmit | 6 | ~200 | 5-7 | 1 天 |
| U3 cross-run cache | 5 | ~300 | 6-8 | 1.5 天 |
| U4 RPM-aware concurrency | 3 | ~150 | 3-5 | 0.5 天 |
| U5 disjointness contract | 2 | ~80 | 3-4 | 0.5 天 |
| U6 per-file editable plan v2 | 5 | ~250 | 4-6 | 1 天 |
| U7 worktree defaults | 3 | ~50 | 2-3 | 0.5 天 |
| **合计** | 28 | ~1280 | 29-41 | **6 天** |

---

## 5. 单元详细设计

### 5.1 U1 — conflict_analyst chunked analysis（含 fast path）

**目的**：单文件超大时不再裸塞 / 截断，而是按 AST 切 chunk → 每 chunk 独立 LLM 分析 → 确定性聚合。

#### 5.1.1 子单元

**U1.A — 解耦 `build_staged_content` 与 `memory_store`**

- `src/agents/conflict_analyst_agent.py:117-172`：拆 `builder = AgentPromptBuilder(...)` 为两件事
- memory 注入仍受 `if self._memory_store:` 控制
- diff-aware staged_content **始终运行**（即使 memory 为 None）
- 同样改 `src/agents/executor_agent.py:392-427` `execute_semantic_merge`

**U1.B — chunked analysis（核心）**

触发条件：`max(len(current_content), len(target_content)) > config.chunk_size_chars * 2`（默认 40KB）

切分：复用 `src/agents/executor_agent.py:1000+` 的 `split_by_semantic_boundary`，每 chunk ~6000 chars（≈1500 tokens；Sweep AI 的 1500 char 太碎）

Map 阶段：每 chunk 独立 `build_conflict_analysis_prompt` + LLM，通过 `ParallelFileRunner.from_api_key_env_list(...)` 并发

Reduce 阶段（确定性，无额外 LLM 调用）：

```python
def _aggregate_chunked_analyses(chunks: list[ConflictAnalysis], state: MergeState) -> ConflictAnalysis:
    # 1. Hard cap
    if len(chunks) > 8 or total_content_bytes > 10 * 1024 * 1024:
        return ConflictAnalysis(
            recommended_strategy=MergeDecision.ESCALATE_HUMAN,
            rationale=f"file too large for safe chunked analysis ({len(chunks)} chunks)",
            confidence=0.3,
            is_chunked=True,
            chunk_count=len(chunks),
            ...
        )

    # 2. Fast path: 所有 chunks 一致策略 + min(conf) ≥ 阈值 + 无 security
    threshold = state.config.thresholds.chunked_aggregation_min_confidence
    strategies = {c.recommended_strategy for c in chunks}
    min_conf = min(c.confidence for c in chunks)
    if (
        len(strategies) == 1
        and min_conf >= threshold
        and not any(c.is_security_sensitive for c in chunks)
    ):
        return ConflictAnalysis(
            recommended_strategy=next(iter(strategies)),
            confidence=min_conf,
            rationale=f"chunked analysis: {len(chunks)} chunks unanimous on {next(iter(strategies)).value}",
            is_chunked=True,
            chunk_count=len(chunks),
            ...
        )

    # 3. Slow path: 保守聚合
    return ConflictAnalysis(
        recommended_strategy=_strategy_precedence(chunks),  # ESCALATE > SEMANTIC > TAKE_*
        conflict_type=_conflict_type_max_severity(chunks),  # LOGIC_CONTRADICTION > INTERFACE_CHANGE > ...
        confidence=min_conf * 0.8,                          # 不确定性惩罚
        is_security_sensitive=any(c.is_security_sensitive for c in chunks),
        can_coexist=all(c.can_coexist for c in chunks),
        rationale=f"[chunked {len(chunks)} parts, disagreement] " + " | ".join(c.rationale for c in chunks),
        is_chunked=True,
        chunk_count=len(chunks),
        ...
    )
```

#### 5.1.2 Schema 改动

`src/models/conflict.py` `ConflictAnalysis`：
```python
is_chunked: bool = Field(default=False, description="True if this analysis was produced by chunked-then-aggregated processing.")
chunk_count: int = Field(default=1, ge=1, description="Number of chunks processed; 1 means single-call path.")
```

`src/models/config.py` `ThresholdConfig`：
```python
chunked_aggregation_min_confidence: float = Field(
    default=0.85,
    ge=0.0,
    le=1.0,
    description="Minimum per-chunk confidence required to take the chunked-analysis fast path. "
                "When all chunks agree on a strategy AND min(confidence) ≥ this threshold AND no chunk "
                "flagged security_sensitive, the file's aggregated verdict is emitted directly. "
                "Below this threshold, conservative aggregation rules apply (strategy-precedence, "
                "confidence × 0.8 penalty). Calibrated against forgejo 1822-file run where staged "
                "content covered 90%+ files; the remaining 10% large files needed multi-chunk analysis.",
)
```

`src/agents/contracts/conflict_analyst.yaml` inputs：增加 `thresholds`（如未列出）。

#### 5.1.3 测试（4-6 个）

| 测试 | 验证 |
|---|---|
| `test_staged_content_runs_without_memory_store` | U1.A 修复：构造 `_memory_store=None` 的 analyst，调用 `analyze_file`，断言 `build_staged_content` 仍被调用、prompt 总长度 < 20KB |
| `test_chunked_path_fast_unanimous` | 3 chunks 都返 `take_target` conf=0.9 → 输出 `take_target`，`is_chunked=True`，`chunk_count=3`；mock 计数器确认无 reducer LLM 额外调用 |
| `test_chunked_path_slow_disagreement` | 3 chunks 返 `[take_target, semantic_merge, take_current]` → 输出 `semantic_merge`（precedence），confidence = min × 0.8 |
| `test_chunked_hard_cap_escalates` | 9-chunk 场景 → `ESCALATE_HUMAN` |
| `test_chunked_security_falls_to_slow_path` | 任一 chunk security=True → 即使 unanimous 也走 slow path |
| `test_chunked_aggregation_chunk_count_tracked` | aggregate 后 `chunk_count == len(chunks)` |

---

### 5.2 U2 — per-run budget + autosubmit（对标 SWE-agent）

**目的**：杜绝"半小时 0 patches 还在烧钱"的失败模式。预算硬上限触发后强制落盘部分结果 + 转 AWAITING_HUMAN，原因 `BUDGET_EXCEEDED`。

#### 5.2.1 Schema 改动

`src/models/config.py`：
```python
per_run_cost_limit_usd: float | None = Field(
    default=5.0,
    ge=0.0,
    description="Hard cap on cumulative LLM cost for one merge run. When cumulative cost reaches this "
                "value, the next LLM call raises RunBudgetExceeded; the orchestrator catches it, writes "
                "a partial-result report, and transitions to AWAITING_HUMAN with reason=BUDGET_EXCEEDED. "
                "Set to None to disable (e.g. CI auto-merge of trusted patterns). Forgejo regression "
                "reference: a single stuck run burned ~$25 of retry traffic before manual kill; "
                "$5 default catches similar incidents at ~1/5 the damage.",
)
per_run_cost_warn_pct: float = Field(
    default=0.8,
    ge=0.1,
    le=1.0,
    description="Cumulative-cost ratio at which to emit a 'budget warning' activity event (Web UI uses it "
                "to color the cost stat orange). No execution change at the warning threshold; "
                "per_run_cost_limit_usd is the hard cap.",
)
```

`src/models/state.py`：新增异常类型
```python
class SystemStatus(str, Enum):
    ...
    # 既有项不变
    
class RunBudgetExceeded(Exception):
    """Raised when cumulative LLM cost reaches per_run_cost_limit_usd.
    Caught by Orchestrator.run() which writes a partial-result report and
    transitions to AWAITING_HUMAN with reason='budget_exceeded'.
    """
    def __init__(self, spent: float, limit: float, phase: str):
        self.spent = spent
        self.limit = limit
        self.phase = phase
        super().__init__(f"Run budget exceeded: ${spent:.4f} >= ${limit:.4f} (phase={phase})")
```

#### 5.2.2 实施点

| 位置 | 改动 |
|---|---|
| `src/agents/base_agent.py` `_call_llm_with_retry` | 调用前查 `state.cost_summary.total_cost_usd >= config.per_run_cost_limit_usd` → 抛 `RunBudgetExceeded`；调用后再查（防 race） |
| `src/agents/base_agent.py` `_call_llm_with_retry` | 软阈值（80% 默认）首次触发时 `ctx.emit(event_type="progress", action="budget_warning", extra={"pct": ...})` |
| `src/core/orchestrator.py:346` `except Exception as e` 之上 | 单独 `except RunBudgetExceeded` 分支：写 partial report、转 AWAITING_HUMAN（reason 含 BUDGET_EXCEEDED）、checkpoint tag "budget_exceeded" |
| `src/web/serializers.py` | 序列化 `cost_summary` 时附加 `limit_usd` + `warn_pct` 让前端能渲染进度条 |
| `web/src/views/RunDashboard.tsx` | Run cost 卡片下加 budget 进度条（绿/橙/红三色） |

#### 5.2.3 测试

| 测试 | 验证 |
|---|---|
| `test_budget_exceeded_at_hard_cap_raises` | 模拟 cost_tracker 累加到 limit → 下一次 `_call_llm_with_retry` 立刻 raise |
| `test_budget_exceeded_transitions_to_awaiting_human` | orchestrator 捕获后 status → AWAITING_HUMAN，reason 含 BUDGET_EXCEEDED，applied_patches 保留 |
| `test_budget_warning_emits_event_at_80pct` | cost 累加到 80% → activity event 出现且仅出现一次 |
| `test_budget_disabled_when_limit_is_none` | `per_run_cost_limit_usd=None` 时跑完所有 phase 不抛 |
| `test_budget_exceeded_writes_partial_report` | AWAITING_HUMAN 时 `.merge/runs/<id>/budget_exceeded_report.md` 存在 + 含已合并文件清单 |

---

### 5.3 U3 — cross-run cache（对标 Cursor + Continue）

**目的**：同一对 `(fork_sha, upstream_sha)` 二次 run 时，已经判过的文件分类 + conflict 分析直接命中缓存，跳过 LLM 调用。

#### 5.3.1 缓存设计

**Key**：`(file_path, fork_sha, upstream_sha, base_sha, agent_contract_version)`
- `fork_sha` / `upstream_sha`：本次 run 的两个 ref 解析后的 commit SHA
- `base_sha`：merge_base_commit
- `agent_contract_version`：从 `src/agents/contracts/<agent>.yaml` 的 `version:` 字段读（需新增此字段）

**存储**：SQLite，`<repo>/.merge/cache.db`
```sql
CREATE TABLE agent_output_cache (
    file_path TEXT NOT NULL,
    fork_sha TEXT NOT NULL,
    upstream_sha TEXT NOT NULL,
    base_sha TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    contract_version INTEGER NOT NULL,
    output_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    cost_usd REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (file_path, fork_sha, upstream_sha, base_sha, agent_name, contract_version)
);
CREATE INDEX idx_agent_output_cache_lookup ON agent_output_cache (agent_name, fork_sha, upstream_sha);
```

**TTL / 容量**：默认 30 天 + 单 repo 上限 10000 entry（LRU）。

#### 5.3.2 命中的 agent

- `file_classifier`（确定性，已 deterministic，但仍可缓存 metadata 避免 git 操作开销）
- `planner._classify_batch` 每文件结果（缓存粒度：单文件）
- `conflict_analyst.analyze_file` 输出
- `judge._review_files_batch_llm` 每文件 issues（粒度：单文件）

**不缓存**：executor（因为同一文件多次 run 需要 fresh merged content）、planner_judge（review log 是 run-specific 的）。

#### 5.3.3 Schema 改动

`src/models/config.py`：
```python
class CacheConfig(BaseModel):
    enabled: bool = Field(default=True)
    ttl_days: int = Field(default=30, ge=1)
    max_entries_per_repo: int = Field(default=10000, ge=100)
    excluded_agents: list[str] = Field(default_factory=list, description="Agent names to skip caching for (debugging).")

class MergeConfig(BaseModel):
    ...
    cache: CacheConfig = Field(default_factory=CacheConfig)
```

`src/agents/contracts/*.yaml`：每个 contract 增加 `version: 1` 字段。改 prompt / 改聚合规则时手动 bump。

#### 5.3.4 实施点

| 位置 | 改动 |
|---|---|
| 新模块 `src/tools/agent_output_cache.py` | `class AgentOutputCache: get(...)`, `put(...)`, `evict_expired()`, `purge_lru()` |
| `src/agents/base_agent.py` | 新增 `_cached_call(...)` helper：先查 cache，命中直接返回；miss 调真实 LLM 后写 cache |
| 各 agent 改造 | classifier / conflict_analyst / judge 在合适位置改调 `_cached_call(...)` |
| `src/core/orchestrator.py` | run 开始时 `cache.evict_expired()` + `cache.purge_lru()` |
| `merge` CLI 新子命令 | `merge cache stats`（输出命中率 / 已节省成本）、`merge cache clear` |
| `src/web/serializers.py` | 新增 `cache_stats` 字段（hit/miss/saved_usd），UI 显示"复用 X 次，节省 $Y" |

#### 5.3.5 测试

| 测试 | 验证 |
|---|---|
| `test_cache_hit_skips_llm` | 同一 (file, fork_sha, upstream_sha) 第二次调用 → mock LLM 未被调用 |
| `test_cache_miss_writes_entry` | 第一次调用后 cache 表有对应 entry |
| `test_cache_invalidated_on_contract_version_bump` | contract version 1 → 2 后旧 entry 无效 |
| `test_cache_invalidated_on_sha_change` | upstream_sha 变了 → cache miss |
| `test_cache_ttl_eviction` | 31 天前的 entry 被 `evict_expired` 清理 |
| `test_cache_lru_purge_on_overflow` | 超 max_entries → LRU 清理 |
| `test_cache_disabled_via_config` | `cache.enabled=False` → 完全跳过 cache 逻辑 |

---

### 5.4 U4 — RPM-aware concurrency（对标 LangGraph）

**目的**：`parallel_file_concurrency` 不再是固定整数；根据当前 provider 的 RPM/TPM 实测推算上限。

#### 5.4.1 设计

**思路**：在 `BaseAgent` 维护 sliding-window RPM 统计（过去 60 秒内的请求计数）；`ParallelFileRunner` 启动并发任务前查询当前窗口剩余配额，按"剩余配额 × 安全系数"动态调 `Semaphore`。

新模块 `src/llm/rate_budget.py`：
```python
class RateBudget:
    """Per-provider sliding-window rate limit tracker.

    Tracks request_count and total_input_tokens over a rolling 60s window.
    Consumers call ``acquire(estimated_tokens)`` which blocks if the next
    request would exceed configured RPM or TPM.

    Provider RPM/TPM limits are loaded from config (default conservative).
    """
    def __init__(self, provider: str, rpm: int, tpm: int):
        ...
    async def acquire(self, estimated_tokens: int) -> None: ...
    def stats(self) -> dict: ...
```

`MergeConfig` 增加：
```python
class RateLimitConfig(BaseModel):
    anthropic_rpm: int = Field(default=50, description="conservative; check your tier")
    anthropic_tpm: int = Field(default=40000)
    openai_rpm: int = Field(default=60)
    openai_tpm: int = Field(default=90000)
    safety_factor: float = Field(default=0.8, ge=0.1, le=1.0)

class MergeConfig(BaseModel):
    ...
    rate_limits: RateLimitConfig = Field(default_factory=RateLimitConfig)
```

**ParallelFileRunner 改造**：构造时接收 `rate_budget: RateBudget | None`；`_bounded(key)` 在拿 semaphore 后再调 `await rate_budget.acquire(estimated_tokens)`。

#### 5.4.2 测试

| 测试 | 验证 |
|---|---|
| `test_rate_budget_blocks_when_rpm_exhausted` | 模拟 50 RPM，第 51 次 acquire 阻塞至窗口滑出 |
| `test_rate_budget_blocks_when_tpm_exhausted` | 模拟 40K TPM，已用 39K 后请求 2K 阻塞 |
| `test_rate_budget_stats_exposes_remaining` | `stats()` 返回剩余 RPM/TPM 和 next-window 时间 |
| `test_parallel_runner_with_rate_budget_does_not_429` | 端到端：50 RPM provider + 100 并发 task → 100% 成功，无 429 |
| `test_rate_budget_disabled_when_config_none` | rate_limits=None 时退化为只受 semaphore 限制 |

---

### 5.5 U5 — file-disjointness contract（对标 Claude Code 子 agent）

**目的**：chunk fan-out 前显式校验各 chunk 文件集 disjoint，避免两个并发分支改同一文件。

#### 5.5.1 实施点

`src/core/parallel_file_runner.py`：新增 helper
```python
def assert_disjoint_file_shards(shards: list[list[str]]) -> None:
    """Raise FileShardOverlap if any two shards share a file path.

    Required precondition for any parallel write path. Catch the
    overlap early and fail loud rather than corrupting the working
    tree by racing two writers.
    """
    seen: dict[str, int] = {}
    for shard_idx, shard in enumerate(shards):
        for fp in shard:
            if fp in seen:
                raise FileShardOverlap(
                    f"file {fp} appears in shards {seen[fp]} and {shard_idx}"
                )
            seen[fp] = shard_idx
```

调用点：
- `executor` 的 `_chunk_issues_by_file` 后（issues 已按 file_path group，应该 disjoint，但 assert 防回归）
- `planner._classify_batch` 切 sub-chunks 后
- `conflict_analyst._chunked_analyze_file` 切 chunks 后（同文件内部 chunks，文件集自然 disjoint，但仍 assert）
- 未来任何新增并发 fan-out 路径

#### 5.5.2 测试

| 测试 | 验证 |
|---|---|
| `test_disjoint_assert_passes_for_clean_shards` | 3 个无重合 shard 通过 |
| `test_disjoint_assert_raises_on_overlap` | 两 shard 都含 `a.py` → raise `FileShardOverlap` |
| `test_executor_chunks_pass_disjoint_assert` | 60 issues 跨 30 文件，rebuttal chunks 全部通过 |
| `test_planner_sub_chunks_pass_disjoint_assert` | 250 files 切 3 chunks 后 disjoint |

---

### 5.6 U6 — per-file editable plan v2（对标 Copilot Workspace）

**目的**：plan 不再只有 `file_path + risk_level`，而是每文件携带 `action + steps + confidence + rationale`，human 在 Web UI 可直接编辑。

#### 5.6.1 Schema 改动

`src/models/plan.py` 新增：
```python
class PerFileAction(str, Enum):
    AUTO_MERGE = "auto_merge"        # 完全交给 executor
    TAKE_UPSTREAM = "take_upstream"  # 强制 take_target
    KEEP_FORK = "keep_fork"          # 强制 take_current
    SEMANTIC_MERGE = "semantic_merge"
    SKIP = "skip"                    # 既不合也不删
    ESCALATE_HUMAN = "escalate_human"

class PerFilePlanEntry(BaseModel):
    file_path: str
    action: PerFileAction
    risk_level: RiskLevel
    confidence: float = Field(ge=0.0, le=1.0)
    steps: list[str] = Field(default_factory=list)
    rationale: str = ""
    edited_by_human: bool = False
    edited_at: datetime | None = None
```

`MergePlan` 增加 `per_file_entries: list[PerFilePlanEntry] = []`。

#### 5.6.2 实施点

- `src/agents/planner_agent.py`：在生成 phase 时同步生成 `PerFilePlanEntry`（action 从 risk_level 推；steps 当前留空，未来 planner prompt 可要求 LLM 给一句话步骤）
- `src/tools/merge_plan_report.py`：渲染时把 per-file entries 作为可读 markdown 表格
- `web/src/views/PlanReview.tsx`：每文件行可点开展开 → 显示 action 下拉 + steps textarea + "edited by you" 标记
- `src/web/ws_bridge.py` 新增 `update_per_file_entry` 消息类型
- `src/core/phases/auto_merge.py`：执行时优先看 `entry.action`，若 human 编辑过则跳过 LLM 重新决策

#### 5.6.3 测试

| 测试 | 验证 |
|---|---|
| `test_planner_emits_per_file_entries` | 1822 文件计划 → entries 数量 = 1822 |
| `test_per_file_entry_action_derived_from_risk` | auto_safe → action=auto_merge; human_required → escalate_human |
| `test_human_edit_marks_entry` | WS update_per_file_entry → entry.edited_by_human=True |
| `test_auto_merge_respects_human_edited_action` | human 改 take_upstream → executor 直接 take_target，无 LLM call |
| `test_per_file_plan_in_merge_plan_report` | MERGE_PLAN_*.md 含 per-file 表格 |

---

### 5.7 U7 — worktree isolation defaults（对标 OpenHands）

**目的**：默认开启 worktree 隔离，避免多 agent 并发直改 fork_ref；并行写不会污染主分支。

#### 5.7.1 Schema 改动

`src/models/config.py`：
```python
enable_working_branch: bool = Field(
    default=True,                                              # ← 改默认值
    description="...",
)
```

注意：这是行为变更，需要 CLAUDE.md 同步更新 + Setup wizard 默认勾选。

#### 5.7.2 实施点

- `src/cli/commands/setup.py`：Setup wizard 的 worktree 复选框默认勾选；description 改为 "推荐：每 run 隔离写入，避免 fork_ref 被半完成状态污染"
- `src/core/orchestrator.py:240`：已有逻辑；只需 default 变化即可生效
- 测试：现有 `working_branch` 相关测试可能需要更新（如果它们依赖 default=False 的隐含语义）

#### 5.7.3 测试

| 测试 | 验证 |
|---|---|
| `test_worktree_enabled_by_default_in_new_state` | `MergeState(config=MergeConfig()).config.enable_working_branch` is True |
| `test_orchestrator_creates_branch_on_run_when_enabled` | run 后 `state.active_branch` 非 None |
| `test_existing_yaml_explicit_false_still_respected` | 用户 yaml 明确写 false → 不被默认 override |

---

## 6. 配置 schema 改动汇总

```yaml
# .merge/config.yaml — 新字段一览（默认值即可省略）
thresholds:
  chunked_aggregation_min_confidence: 0.85    # U1
per_run_cost_limit_usd: 5.0                   # U2
per_run_cost_warn_pct: 0.8                    # U2
cache:                                        # U3
  enabled: true
  ttl_days: 30
  max_entries_per_repo: 10000
  excluded_agents: []
rate_limits:                                  # U4
  anthropic_rpm: 50
  anthropic_tpm: 40000
  openai_rpm: 60
  openai_tpm: 90000
  safety_factor: 0.8
enable_working_branch: true                   # U7 (default 变更)
```

**向后兼容**：所有新字段都有 default；旧 yaml 仍可加载。`enable_working_branch` 默认从 false 改 true 是用户可见行为变更，CHANGELOG 必须显式提示。

---

## 7. 数据模型改动汇总

| 文件 | 改动 | 单元 |
|---|---|---|
| `src/models/conflict.py` ConflictAnalysis | + `is_chunked: bool`<br>+ `chunk_count: int` | U1 |
| `src/models/config.py` ThresholdConfig | + `chunked_aggregation_min_confidence` | U1 |
| `src/models/config.py` MergeConfig | + `per_run_cost_limit_usd`<br>+ `per_run_cost_warn_pct`<br>+ `cache: CacheConfig`<br>+ `rate_limits: RateLimitConfig`<br>~ `enable_working_branch` default | U2/U3/U4/U7 |
| `src/models/state.py` | + `RunBudgetExceeded` exception | U2 |
| `src/models/plan.py` MergePlan | + `per_file_entries: list[PerFilePlanEntry]` | U6 |
| `src/models/plan.py` | + `PerFilePlanEntry`, `PerFileAction` | U6 |
| `src/agents/contracts/*.yaml` | + `version: int` 字段 | U3 |

---

## 8. 测试策略

**总测试数**：29-41 个新单测 + 端到端集成测试 2-3 个。

| 层 | 测试类型 | 关注点 |
|---|---|---|
| 单元（`tests/unit/`） | 纯函数 / 单 agent | 各单元的 schema、边界、错误路径 |
| 集成（`tests/integration/`） | 真实 LLM + git | U1 chunked + U3 cache + U2 budget 三件套联合端到端：在 ~500 文件的 fixture repo 上跑完整 run，验证 cache 命中率 + budget 准确性 |
| 回归 | 现有 2307 全套 | 不允许任何 regression |

**覆盖率门槛**：保持 ≥80%（pyproject.toml `--cov-fail-under=80`）。

**回归基准（性能 + 成本）**：在 forgejo 1822 文件 + Claude Opus 4.6 上：
- 当前主分支：~$25 / run / 跑死或 47%+ 文件未处理
- U1 + U2 完成后：~$25 上限触发 budget cap，部分结果落盘
- U3 完成后：二次 run < $1（90%+ cache 命中）
- 全部完成后：首 run < $20（更精细的 budget 控制 + 更少超时重试）；二次 run < $1

---

## 9. 实施顺序与依赖图

```
Day 1   U1 conflict_analyst chunked          ← 最直接的痛点延续修复
Day 2   U2 per-run budget + autosubmit       ← 兜底机制必须先建立
Day 3   U5 disjointness contract             ← 小且独立，给后续 fan-out 保险
        U7 worktree defaults                 ← 极小，半天搞完
Day 4-5 U3 cross-run cache                   ← 收益最大但工作量也最大
Day 6   U4 RPM-aware concurrency             ← 防止 U3 缓存失效后的 fan-out 踩 429
        U6 per-file editable plan v2         ← 用户面优化，可与 U4 并行
```

**Commit / PR 切分**：每个单元独立 commit，commit message 严格 conventional commits（`perf(agent): ...`、`feat(config): ...`、`fix(plan): ...`），单元间不混提交。每完成一个单元跑 `pytest tests/unit/ -q && mypy src && ruff check src/`，全绿才进下一个。

---

## 10. 验收标准

| 单元 | 验收 |
|---|---|
| U1 | forgejo 1822-file run 中，任何文件 >40KB 都走 chunked 路径；fast-path 命中率 ≥ 60%；hard cap 触发率 < 5% |
| U2 | 故意构造一个会超 budget 的小 run，验证 budget 触发后转 AWAITING_HUMAN + 报告文件存在 |
| U3 | forgejo 二次 run cache 命中率 ≥ 90%（classifier + conflict_analyst） |
| U4 | 200 文件并发 fan-out 对 50 RPM provider → 0 个 429 错误 |
| U5 | 引入故意重合的两 shard → 立刻 raise，不进入并发 |
| U6 | Web UI plan review 页面每文件行可展开编辑 action + steps；改后再 approve，executor 走 human 选择的 action |
| U7 | 新建 fresh repo + `merge` → `git branch` 看到 `merge/auto-*` 而非直改 fork_ref |

**总体验收**：在 forgejo 1822-file repo 上：
1. 首次 run 在 budget 内完成（部分文件可能转人工，但不超 budget）
2. 二次 run 5 分钟内完成（cache 命中）
3. 全程无 429 / 无 transport timeout
4. plan review 页面可逐文件编辑
5. 整套 unit + integration tests 全绿

---

## 附录 A：所有引用源

| 来源 | URL |
|---|---|
| SWE-agent config | https://swe-agent.com/latest/reference/agent_config/ |
| SWE-agent NeurIPS'24 | https://proceedings.neurips.cc/paper_files/paper/2024/file/5a7c947568c1b1328ccc5230172e1e7c-Paper-Conference.pdf |
| Devin building cloud agents | https://cognition.ai/blog/what-we-learned-building-cloud-agents |
| Devin manages Devins | https://cognition.ai/blog/devin-can-now-manage-devins |
| Cursor secure indexing | https://cursor.com/blog/secure-codebase-indexing |
| Cursor index speed | https://read.engineerscodex.com/p/how-cursor-indexes-codebases-fast |
| Continue codebase indexing | https://deepwiki.com/continuedev/continue/3.4-codebase-indexing |
| Continue accuracy | https://blog.continue.dev/accuracy-limits-of-codebase-retrieval/ |
| OpenHands SDK paper | https://arxiv.org/html/2511.03690v1 |
| OpenHands async SWE | https://www.openhands.dev/blog/asynchronous-software-engineering-agents |
| Copilot Workspace manual | https://github.com/githubnext/copilot-workspace-user-manual/blob/main/overview.md |
| Copilot Workspace product | https://githubnext.com/projects/copilot-workspace/ |
| Claude Code sub-agents | https://code.claude.com/docs/en/sub-agents |
| Sub-agent best practices | https://claudefa.st/blog/guide/agents/sub-agent-best-practices |
| LangGraph map-reduce | https://langchain-ai.github.io/langgraphjs/how-tos/map-reduce/ |
| Scaling LangGraph | https://aipractitioner.substack.com/p/scaling-langgraph-agents-parallelization |
| Aider repomap blog | https://aider.chat/2023/10/22/repomap.html |
| Aider repomap deepwiki | https://deepwiki.com/Aider-AI/aider/4.1-repository-mapping-system |
| Sweep blog index | https://blog.sweep.dev/ |
| Sweep chunking notebook | https://github.com/sweepai/sweep/blob/main/notebooks/chunking.ipynb |
| Long-running agents | https://addyo.substack.com/p/long-running-agents |
| Anthropic contextual retrieval | https://www.anthropic.com/news/contextual-retrieval |

---

## 附录 B：已实施的前置修复

以下两项已在本次会话提前实施并 push 到 origin/feat/web，作为本方案的**前置基础**：

| Commit | 内容 | 文件 |
|---|---|---|
| `844defc` | executor.build_rebuttal 切 25 issues/chunk 并发 | `src/agents/executor_agent.py`, `tests/unit/test_p1_quality.py` |
| `4826a6e` | planner._classify_batch 切 100 files/chunk 并发 | `src/agents/planner_agent.py`, `tests/unit/test_agents_extended.py` |

这两项解决了 L0 层（单 prompt 过大）的两个具体表征。本方案在此基础上继续处理 L1-L5 的剩余层级。

---

## 附录 C：术语对齐

| 本文术语 | 含义 |
|---|---|
| run | 一次 `merge` 命令执行；对应一个 run_id 和 `.merge/runs/<id>/` |
| chunk | LLM 调用粒度的输入切片（issue / file / file-content 都可能） |
| shard | 并发 fan-out 时的工作单元；多个 chunks 组成一 shard 一并提交给一个 sub-task |
| budget | 一个 run 累计的 LLM 美元成本 |
| fast path | 当聚合规则允许跳过额外 LLM 调用时的短路路径 |
| disjoint | 多个 shard 的文件集两两无交集 |
| autosubmit | 触发 budget / 异常时强制落盘当前进度并转人工，而非崩溃 |
