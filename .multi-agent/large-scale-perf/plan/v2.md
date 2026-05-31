# Plan v2 — large-scale-file-processing-optimization

> Planner v2。基于 `facts.md` A-Q 17 节锚点 + `doc/large-scale-file-processing-optimization.md` §1-§10 设计。
> 7 单元 U1-U7 拆为 **Phase 0-7 共 8 个 Phase**。每 Phase 独立 commit、独立验收。
> 严禁绕过 8 项 anti-pattern（facts.md A5）；严禁引入 target-repo 硬编码（facts.md A4）；保持 mypy strict + ≥80% 覆盖率（facts.md A2 / K4）。
>
> **v2 修订摘要**（相对 v1，落实 `v1-review.md` 反馈）：
> - **P0-1**：§3.1 Q1 决策 + Phase 2 交付物显式列出 `tests/unit/test_telemetry_snapshot.py:125` 改动 + 新增 `test_max_cost_usd_can_be_disabled_with_none`。
> - **P0-2**：Phase 3 调用点扩到 6 处具名（5 现存 `from_api_key_env_list` + 1 Phase 1 新增），含 `judge_agent.py:167 / :1473` + `conflict_analyst_agent.py:81`。
> - **P1-1**：§3.3 Q3 决策提前列出 `tests/unit/test_working_branch.py:72-83` 影响清单 + 处理动作；Phase 4 commit message 范本由"5 处"改为"已知 ≥2 处，开工 grep 复核"。
> - **P1-2**：所有 `split_by_semantic_boundary` 引用改为 `src/tools/chunk_processor.split_by_semantic_boundary`（避免循环 import）。
> - **P1-3**：Phase 0 交付物显式加入 `src/agents/contract.py:19 AgentContract` 模型新增 `version: int = 0` 默认字段。
> - **P2-1**：Phase 7 备注新增 `from datetime import datetime` import。
> - **P2-2**：§4 风险表加入 `executor_agent.py` / `config.py` 文件大小约束应急策略。
> - **P2-3**：Phase 5 GO 条件备注 fixture 选择策略推迟到 Verifier。

---

## 1. 架构图

### 1.1 改动文件矩阵（按层 × 单元）

| 层 / 单元 | U1 | U2 | U3 | U4 | U5 | U6 | U7 |
|---|---|---|---|---|---|---|---|
| **models/** | `conflict.py`（+ `is_chunked`, `chunk_count`，facts.md C3） / `config.py` ThresholdConfig（+ `chunked_aggregation_min_confidence`，facts.md I5） | `config.py`（决策见 §3 Q1） / `state.py` RunBudgetExceeded（facts.md H2） | `config.py` `CacheConfig`（doc §5.3.3） / `plan.py`（仅当 U3 加 contract_version 入 plan，否则不动） | `config.py` `RateLimitConfig`（doc §5.4.1） | — | `plan.py` `PerFileAction` / `PerFilePlanEntry`（决策见 §3 Q4） | `config.py` `enable_working_branch` default（facts.md I4） |
| **tools/** | — | `cost_tracker.py` 查询路径（facts.md G3，**只读不改**） | 新增 `agent_output_cache.py`（doc §5.3.4） / `merge_plan_report.py`（U6 也用） | — | — | `merge_plan_report.py` per-file 表格 | — |
| **llm/** | `prompt_builders.py` `build_staged_content` 解耦使用（facts.md D4，**调用方式调整，不改函数体**） | — | — | 新增 `rate_budget.py`（doc §5.4.1） | — | — | — |
| **agents/** | `conflict_analyst_agent.py:106-201`（facts.md C1/C2） / `executor_agent.py:392-427`（U1.A）；contract yaml `conflict_analyst.yaml` 加 `thresholds` 入参（facts.md C4） | `base_agent.py:426` `_call_llm_with_retry`（facts.md G1） | **7 个 contract yaml 全部加 `version: 1`** + `contract.py:19 AgentContract` 加 `version: int = 0` 默认（facts.md A3 / Q2，P1-3 修订） / 各 reader agent（planner / conflict_analyst / judge）走 `_cached_call` | `parallel_file_runner.py` `_bounded`（facts.md F3） | `parallel_file_runner.py` `assert_disjoint_file_shards`（facts.md F1） + **6 个具名调用点**（详见 Phase 3，P0-2 修订） | `planner_agent.py` 生成 entries / `auto_merge.py` 短路（facts.md H3） | — |
| **core/** | — | `orchestrator.py:346` 新增 `except RunBudgetExceeded` 分支（facts.md H2） | `orchestrator.py` run 启动 `cache.evict_expired() + purge_lru()` | `parallel_file_runner.py:__init__` 接 `rate_budget` | `parallel_file_runner.py`（同上） | `phases/auto_merge.py` 短路（facts.md H3） | `orchestrator.py:240-247`（facts.md H1，**仅 default 生效，0 代码改动**） |
| **cli/** | — | — | 新子命令 `merge cache stats / clear` | — | — | — | `commands/setup.py` wizard 默认勾选（doc §5.7.2） |
| **web/** | — | `serializers.py`（+ `limit_usd`, `warn_pct`，facts.md J1） / `views/RunDashboard.tsx`（facts.md J2，budget 进度条） | `serializers.py`（+ `cache_stats`） | — | — | `ws_bridge.py`（+ `update_per_file_entry`，facts.md J1） / `views/PlanReview.tsx`（facts.md J2） | `commands/setup.py` wizard 默认变化（无 web 改动） |

合计净改动文件：~28（与 facts.md N 一致）。

### 1.2 模块依赖图（继承 facts.md M）

```
Phase 0 基础设施
   ├─ RunBudgetExceeded 异常（U2 前置）
   └─ 7 个 contract yaml 加 version 字段（U3 前置；放 Phase 0 而非 Phase 5，避免 reviewer 反复审同批 yaml）

Phase 1  U1 conflict_analyst chunked  ──┐
                                        ├──► Phase 7  U6 per-file editable plan v2
Phase 4  U7 worktree defaults  ─────────┘            ▲
                                                     │
Phase 2  U2 per-run budget + autosubmit ──┐          │
                                          ├──► Phase 5  U3 cross-run cache
Phase 6  U4 RPM-aware concurrency  ───────┤          │
                                          │          │
Phase 3  U5 disjointness contract ────────┘          │
                                                     │
                                       完整生产化形态
```

> Phase 顺序 = doc §9 + facts.md M5 的 Day1-6 排序。Phase 0 是从 U2/U3 中提出的"必须先建"的基础设施，与 facts.md M 不冲突——M 描述 U 之间依赖，Phase 0 描述 U2/U3 自身内部的 pre-step。

---

## 2. Phase 拆分

每 Phase 模板：**估时**（保守，最坏情况）/ **输入依赖** / **交付物**（文件 + 测试 + commit）/ **GO 条件**。

### Phase 0 — 基础设施

**估时**：0.5 天

**输入依赖**：当前 HEAD `4826a6e`（facts.md B3）。无其他 Phase 依赖。

**交付物**：
- `src/models/state.py`：新增 `RunBudgetExceeded(Exception)` 类。**仅定义，未接线**。签名 `__init__(self, spent: float, limit: float, phase: str)`（doc §5.2.1）。注：`phase` 来源为 `base_agent.py:147/235 _current_phase: str`（review 候选追加事实 #8）。
- `src/agents/contract.py:19 AgentContract`（Pydantic model）：新增 `version: int = Field(default=0, ge=0, description="Contract schema version. Bump when prompt/aggregation rules/IO schema changes (see _schema.md Versioning). Default 0 allows future yaml omissions to load without crash; the 7 shipped yaml all declare version=1, so default is never consumed in practice.")`。**P1-3 修订**：明确加载器兼容路径，避免新 schema + 老 yaml 爆 ValidationError。
- `src/agents/contracts/{conflict_analyst,executor,human_interface,judge,memory_extractor,planner,planner_judge}.yaml`：每个文件顶层加 `version: 1`（facts.md A3 / Q2 决策见 §3）。
- `src/agents/contracts/_schema.md`：新增 "Versioning" 段，说明何时 bump version（修改 prompt 内容 / 修改 aggregation 规则 / 修改 input/output schema）。
- 单测 `tests/unit/test_run_budget_exceeded_dataclass.py`：验证异常 message 包含 spent/limit/phase。
- 单测 `tests/unit/test_agent_contracts.py` 扩展：
  - 断言 7 个 contract yaml 都有 `version: int >= 1` 字段；
  - 断言 `AgentContract(name="x", inputs=[], ...).version == 0` 即默认仍可加载（兼容性）。
- commit 范围：1 commit。`chore(infrastructure): RunBudgetExceeded 异常 + AgentContract.version 字段 + 7 contracts version=1`。

**GO 条件**：
- `pytest tests/unit/test_agent_contracts.py tests/unit/test_run_budget_exceeded_dataclass.py -v` 全绿。
- `mypy src` 无新增 error。
- `ruff check src/` 无新增 warning。
- **未引入任何运行时行为变化**（异常未 raise；version 字段未消费）。

---

### Phase 1 — U1 conflict_analyst chunked analysis

**估时**：1.5 天（保守上调；facts.md N 估 1 天，但含 U1.A 解耦改 executor 同形态点 D2，可能触发 executor 现有测试）

**输入依赖**：Phase 0（独立性高，0 依赖也可启动，此处仅按 doc §9 顺序）。

**交付物**：
- `src/agents/conflict_analyst_agent.py`：
  - **U1.A** 解耦：将 lines 117-172 的 staged_content 构造从 `if self._memory_store:` 块内提到外层（facts.md C1）。memory 注入仍由 `if self._memory_store:` 控制，但 `build_staged_content` 调用始终运行（facts.md D4）。
  - **U1.B** 新增 `_chunked_analyze_file(...)` 路径：当 `max(len(current), len(target)) > config.chunk_size_chars * 2`（默认 40KB，facts.md I1）时切 chunks，**复用 `from src.tools.chunk_processor import split_by_semantic_boundary`**（真实定义位置 `src/tools/chunk_processor.py:50`；facts.md D2 描述的 `executor_agent.py:482-491` 是 import + 调用点，**P1-2 修订**避免 conflict_analyst 反向 import executor 导致 agents/ 层循环耦合），通过 `ParallelFileRunner.from_api_key_env_list(...)` 并发（facts.md D1 模板）。
  - 新增 `_aggregate_chunked_analyses(...)`（确定性 reducer，无 LLM 调用，doc §5.1.1 伪码）：hard cap → fast path（unanimous + min_conf ≥ threshold + 无 security）→ slow path（precedence + 0.8 confidence 惩罚）。
- `src/agents/executor_agent.py:392-427` `execute_semantic_merge`：同形态 U1.A 解耦（doc §5.1.1）。
- `src/models/conflict.py:40-51` `ConflictAnalysis`：加 `is_chunked: bool = False`、`chunk_count: int = Field(default=1, ge=1)`（doc §5.1.2）。
- `src/models/config.py` `ThresholdConfig`：加 `chunked_aggregation_min_confidence: float = 0.85`（facts.md I5；doc §5.1.2 description 含 "calibrated against forgejo 1822-file run" 历史说明，符合 facts.md A4 允许形态）。
- `src/agents/contracts/conflict_analyst.yaml`：inputs 加 `thresholds`（facts.md C4）。
- 单测 6 个（doc §5.1.3）：`test_staged_content_runs_without_memory_store` / `test_chunked_path_fast_unanimous` / `test_chunked_path_slow_disagreement` / `test_chunked_hard_cap_escalates` / `test_chunked_security_falls_to_slow_path` / `test_chunked_aggregation_chunk_count_tracked`。
- commit 范围：3 commit。
  - `refactor(conflict_analyst,executor): 解耦 build_staged_content 与 memory_store gate`
  - `feat(models): ConflictAnalysis 增 is_chunked/chunk_count；ThresholdConfig 增 chunked_aggregation_min_confidence`
  - `perf(conflict_analyst): 大文件走 chunked analysis + 确定性聚合`

**GO 条件**：
- 6 个新单测 + 现有 `tests/unit/test_conflict_analyst*` 全绿。
- `tests/unit/test_agent_contracts.py` 通过（contract inputs 校验未破）。
- `mypy src` / `ruff check src/` 零新增 error。
- 总覆盖率不低于当前基线（facts.md K4）。

---

### Phase 2 — U2 per-run budget + autosubmit

**估时**：1.5 天（doc 估 1 天，但 web 改动需要 npm test，外加 partial-report 渲染逻辑需 e2e 验证 fs 写入）

**输入依赖**：Phase 0（消费 `RunBudgetExceeded`）。

**交付物**：
- `src/models/config.py`：加 `per_run_cost_limit_usd`（决策见 §3 Q1）+ `per_run_cost_warn_pct: float = 0.8`（doc §5.2.1）。
- `src/agents/base_agent.py:426` `_call_llm_with_retry`：
  - 调用 LLM 前查询 `self._cost_tracker.total_cost_usd >= config.per_run_cost_limit_usd`（facts.md G3，**绕开 `state.cost_summary` 因为它不是 source-of-truth，G4**）→ raise `RunBudgetExceeded(spent, limit, phase=current_phase)`。
  - 调用后再查一次（防 race）。
  - 首次跨越 `warn_pct` 时调 `ctx.emit(event_type="progress", action="budget_warning", extra={"pct": ratio})`。
  - 必须保持现有 retry / circuit-breaker 模式（facts.md G2），新逻辑包裹在最外层；anti-pattern: 不得绕过本函数（facts.md A5）。
- `src/core/orchestrator.py:346`：在 `except Exception as e` 之上插入 `except RunBudgetExceeded as e` 分支，负责：
  1. 调用 `cost_tracker` snapshot 写入 `state.cost_summary`；
  2. 通过现有 partial-report writer（doc §5.2.2，文件路径 `.merge/runs/<id>/budget_exceeded_report.md`）写部分结果；
  3. transition `AWAITING_HUMAN`（**facts.md H4** 已是合法终止）；
  4. checkpoint tag `"budget_exceeded"`。
  必须协同既有 ceiling check（facts.md G5 / Q1 决策）：两套机制只允许一处真正 transition，避免 double-transition。
- `src/web/serializers.py`：cost_summary 序列化输出加 `limit_usd` + `warn_pct`（facts.md J1）。
- `web/src/views/RunDashboard.tsx`：cost 卡片下加 budget 进度条（绿 / 橙 ≥warn_pct / 红 ≥limit），facts.md J2。
- **P0-1 修订** 现存测试改动（必须随 commit #1 提交）：
  - `tests/unit/test_telemetry_snapshot.py:125 test_max_cost_usd_field_defaults_none` → 断言改为 `config.max_cost_usd == 5.0`；测试方法名改为 `test_max_cost_usd_defaults_to_five_dollars`。
  - 新增 `tests/unit/test_telemetry_snapshot.py::test_max_cost_usd_can_be_disabled_with_none`：显式 `MergeConfig(max_cost_usd=None, ...)` → 验证字段允许 None，且 orchestrator ceiling check 不触发（facts.md G5 现有路径）。
  - `tests/unit/test_telemetry_snapshot.py:140` 处现有 `max_cost_usd=1.0` 显式赋值的测试无需改动（与 default 解耦）。
  - 在 Phase 2 commit #1 之前必须 grep `max_cost_usd` 全测试树复核，若 grep 出未列入的隐式 default 依赖立刻列入清单。
- 单测 5 个（doc §5.2.3）：`test_budget_exceeded_at_hard_cap_raises` / `test_budget_exceeded_transitions_to_awaiting_human` / `test_budget_warning_emits_event_at_80pct` / `test_budget_disabled_when_limit_is_none` / `test_budget_exceeded_writes_partial_report`。
- 新增幂等性单测 `test_budget_double_transition_idempotent`（呼应 §4 风险表）：BaseAgent raise → orchestrator transition AWAITING_HUMAN → 同 run 内 ceiling check（facts.md G5）再触发时不重复 transition。
- web 单测 1-2 个（`RunDashboard.test.tsx` 已存在，facts.md J2，扩展测试预算条三态渲染）。
- commit 范围：3 commit。
  - `feat(config): per_run_cost_limit semantic on max_cost_usd（default 5.0）+ per_run_cost_warn_pct（含 telemetry_snapshot 测试断言迁移 + None 兼容测试）`
  - `feat(base_agent,orchestrator): budget cap → RunBudgetExceeded → AWAITING_HUMAN + partial report + double-transition 幂等`
  - `feat(web): RunDashboard 预算进度条 + serializer limit/warn 字段`

**GO 条件**：
- 5 个新单测 + RunDashboard web 测试全绿。
- `tests/unit/test_agent_contracts.py` 通过（确认 anti-pattern: BaseAgent 仍走 `_call_llm_with_retry` 唯一入口，facts.md A5）。
- `mypy src` 零新增 error。
- 手工冒烟：构造一个 mock `cost_tracker` 累加到 limit，跑一次 orchestrator → 验证终态 = AWAITING_HUMAN + 报告文件存在。
- **回归**：现有 `max_cost_usd` ceiling 路径（facts.md G5）测试仍通过（决策 §3 Q1 保证）。

---

### Phase 3 — U5 disjointness contract

**估时**：0.5 天

**输入依赖**：无（独立小单元）。

**交付物**：
- `src/core/parallel_file_runner.py`：新增 `assert_disjoint_file_shards(shards: list[list[str]]) -> None` 及自定义异常 `FileShardOverlap`（doc §5.5.1 伪码）。
- **P0-2 修订**：调用点 **6 处**（仓库现存 5 处 `ParallelFileRunner.from_api_key_env_list` 全量 + 1 处 Phase 1 新增），逐点接入并附"为何此处校验有意义"理由（facts.md L6 "显式校验"要求每个 fan-out 都过校验）：
  1. `src/agents/conflict_analyst_agent.py:81`（**现存**，multi-file fan-out，传入 file_keys；理由：防 file_diffs 重复 key 漏检）；
  2. `src/agents/executor_agent.py:829`（**现存**，`_chunk_issues_by_file` 后；理由：issues 已按 file_path group 应天然 disjoint，assert 防回归）；
  3. `src/agents/planner_agent.py:645`（**现存**，`_classify_batch` 切 sub-chunks 后，facts.md E1）；
  4. `src/agents/judge_agent.py:167`（**现存**，high-risk per-file fan-out；理由：入参 dict.keys() 名义 disjoint，仍需 assert 防上游传入重复 keys）；
  5. `src/agents/judge_agent.py:1473`（**现存**，judge chunk runner；理由：chunk 拆分逻辑变更时 assert 是回归网）；
  6. `src/agents/conflict_analyst_agent.py` U1 chunked 路径切 chunks 后（**Phase 1 新增**；同文件内 chunks 文件集天然 disjoint，仍 assert 与其他 5 处保持一致合约形态）。
- 单测 4 个（doc §5.5.2） + **2 个 P0-2 新增** = 6 个：
  - `test_disjoint_assert_passes_for_clean_shards`
  - `test_disjoint_assert_raises_on_overlap`
  - `test_executor_chunks_pass_disjoint_assert`
  - `test_planner_sub_chunks_pass_disjoint_assert`
  - `test_judge_per_file_fan_out_passes_disjoint_assert`（P0-2 新增，对应 :167）
  - `test_judge_chunk_runner_passes_disjoint_assert`（P0-2 新增，对应 :1473）
- commit 范围：1 commit。`feat(parallel_runner): file-disjointness assert + 6 处接入点（conflict_analyst×2 + executor + planner + judge×2）`

**GO 条件**：
- 4 个新单测全绿。
- 现有 `tests/unit/test_executor*` / `tests/unit/test_planner*` 不破。
- `mypy src` / `ruff check src/` 零新增。

---

### Phase 4 — U7 worktree defaults

**估时**：1 天（doc 估 0.5 天，保守上调因 facts.md Q3 现有测试影响面未量化）

**输入依赖**：无。

**交付物**：
- `src/models/config.py` `enable_working_branch` default `False → True`（facts.md I4 / Q3 决策见 §3）。
- `src/cli/commands/setup.py` Setup wizard：worktree 复选框默认勾选，description 改为 "推荐：每 run 隔离写入，避免 fork_ref 被半完成状态污染"（doc §5.7.2）。
- **现有测试梳理**（Q3 决策）：在 Phase 4 开工前先 `grep -rn enable_working_branch tests/` + `grep -rn active_branch tests/`，列出受影响测试集，按以下 3 选 1 处理：
  - 测试本意验证 default 行为 → 改断言为 True；
  - 测试本意验证关闭分支的旁路 → 显式 `MergeConfig(enable_working_branch=False)`；
  - 测试无关联 → 不动。
- 新单测 3 个（doc §5.7.3）：`test_worktree_enabled_by_default_in_new_state` / `test_orchestrator_creates_branch_on_run_when_enabled` / `test_existing_yaml_explicit_false_still_respected`。
- CHANGELOG / CLAUDE.md 同步：default 变更属于用户可见行为变化（doc §6 已声明）。
- commit 范围：2 commit。
  - `chore(config,setup): enable_working_branch default → True + wizard 默认勾选`
  - `test: 适配 worktree 默认开启（已知 ≥2 处现有测试，开工 grep 复核）`（**P1-1 修订**：已知 `tests/unit/test_working_branch.py:72-83` 2 处显式断言；最终数字 Phase 4 开工 grep 出来填——见 §3.3 Q3 决策清单）

**GO 条件**：
- 3 个新单测全绿。
- 全套 unit + integration（已有 + 新）全绿（**facts.md K3 严禁 regression**）。
- 手工冒烟：fresh 目录 `merge` → `git -C <fork> branch` 看到 `merge/auto-*`（doc §10 O7）。

---

### Phase 5 — U3 cross-run cache

**估时**：2 天（doc 估 1.5 天，SQLite schema + multi-agent 接入有调试余量）

**输入依赖**：
- Phase 0（7 contract yaml `version: 1` 已落盘，避免 cache key 第三轮）；
- Phase 2（cache 命中决策依赖 `_cost_tracker` 节省成本统计，但功能不阻塞 Phase 5）。

**交付物**：
- `src/models/config.py`：`CacheConfig` 嵌套类（doc §5.3.3）+ `MergeConfig.cache: CacheConfig = Field(default_factory=CacheConfig)`。
- 新模块 `src/tools/agent_output_cache.py`（≤ 400 lines，CLAUDE.md "<800 lines"）：
  - `class AgentOutputCache: get(...)`, `put(...)`, `evict_expired()`, `purge_lru()`；
  - SQLite schema 见 doc §5.3.1（含复合 PK + lookup index）；
  - 数据库路径 `<repo>/.merge/cache.db`，与 `.merge/.env` 等同级（CLAUDE.md `.merge/` 目录约定）。
- `src/agents/base_agent.py`：新增 `_cached_call(...)` helper（**不替代** `_call_llm_with_retry`，而是 wrapper：先查 cache，miss 调 `_call_llm_with_retry`，结果写 cache。facts.md A5 anti-pattern: 仍必须最终通过 `_call_llm_with_retry`）。
- 接入 3 个 reader agent（doc §5.3.2）：
  - `planner_agent._classify_batch` 单文件结果（粒度 per-file）；
  - `conflict_analyst_agent.analyze_file` 输出；
  - `judge._review_files_batch_llm` 每文件 issues；
- **不缓存**：executor / planner_judge（doc §5.3.2 已锁定，run-specific）。
- `src/core/orchestrator.py` run 启动时 `cache.evict_expired()` + `cache.purge_lru()`（doc §5.3.4）。
- CLI 子命令 `merge cache stats` / `merge cache clear`（新 `src/cli/commands/cache.py`）。
- `src/web/serializers.py`：加 `cache_stats: {hit_count, miss_count, saved_usd}` 字段，UI 显示。
- 单测 7 个（doc §5.3.5）：`test_cache_hit_skips_llm` / `test_cache_miss_writes_entry` / `test_cache_invalidated_on_contract_version_bump` / `test_cache_invalidated_on_sha_change` / `test_cache_ttl_eviction` / `test_cache_lru_purge_on_overflow` / `test_cache_disabled_via_config`。
- commit 范围：4 commit。
  - `feat(config): CacheConfig schema + MergeConfig.cache field`
  - `feat(tools): AgentOutputCache SQLite 实现（get/put/evict/purge）`
  - `feat(base_agent,planner,conflict_analyst,judge): _cached_call 接入 3 reader agent`
  - `feat(cli,web): merge cache stats/clear 子命令 + serializer cache_stats`

**GO 条件**：
- 7 个新单测全绿。
- mypy strict 通过——**SQLite 类型推断风险**：用 `sqlite3.Row` factory + 显式 cast；JSON 列用 `str` + Pydantic parse；avoid `Any`（CLAUDE.md "mypy strict mode"）。
- `merge cache stats` CLI 输出格式手工冒烟。
- 集成测试新增 1 个（doc §8）：fixture repo 二次 run 验证 cache 命中率 ≥ 90%（**facts.md O3**）—— **仅在 `tests/integration/` 下**，不进 CI（CLAUDE.md "tests/integration/ … not run in CI"）。**P2-3 备注**：fixture 选择策略 = 复用 `tests/integration/` 现有 fixture；若不存在则用 doc §8 提到的 forgejo 子集（约 500 文件）作为新 fixture；最终决策推迟到 Verifier 设计测试方案时确定。

---

### Phase 6 — U4 RPM-aware concurrency

**估时**：1 天（doc 估 0.5 天，sliding-window 计算 + 多 provider 路由有调试空间）

**输入依赖**：
- 推荐 Phase 5 后（U3 cache 命中后剩余 LLM 调用集中在 fan-out，U4 防 429 的价值才显化，facts.md M4 顺序）；
- 但功能上独立，无强依赖。

**交付物**：
- 新模块 `src/llm/rate_budget.py`（≤ 200 lines）：
  - `class RateBudget(provider: str, rpm: int, tpm: int)`；
  - `async def acquire(self, estimated_tokens: int) -> None`：sliding-window 60s 内 request count + total input tokens，超 cap 时 await 至窗口滑出；
  - `def stats(self) -> dict`：返回剩余 RPM/TPM + next-window time。
- `src/models/config.py`：`RateLimitConfig`（doc §5.4.1）+ `MergeConfig.rate_limits`。
- `src/core/parallel_file_runner.py`：
  - `__init__` 接收 `rate_budget: RateBudget | None`（保持现有 ctor 兼容，facts.md F1 仅 66 行）；
  - `_bounded` 拿 `Semaphore` 后调 `await rate_budget.acquire(estimated_tokens)`（facts.md F3 注入点）；
  - `from_api_key_env_list` factory 兼容旧调用方（rate_budget 默认 None → 退化为现有行为，facts.md E2）。
- 单测 5 个（doc §5.4.2）：`test_rate_budget_blocks_when_rpm_exhausted` / `test_rate_budget_blocks_when_tpm_exhausted` / `test_rate_budget_stats_exposes_remaining` / `test_parallel_runner_with_rate_budget_does_not_429` / `test_rate_budget_disabled_when_config_none`。
- commit 范围：2 commit。
  - `feat(llm): RateBudget sliding-window RPM/TPM tracker`
  - `feat(config,parallel_runner): rate_limits 配置 + ParallelFileRunner 集成 RateBudget`

**GO 条件**：
- 5 个新单测全绿（含 race 模拟用 `asyncio.Event` + monkeypatch `time.time`，facts.md K5 asyncio_mode=auto）。
- 现有 `ParallelFileRunner` 调用方（executor.build_rebuttal / planner._classify_batch / conflict_analyst Phase 1）回归不破。
- mypy strict 通过。
- 端到端冒烟（手工）：在 50 RPM 限制下并发 100 任务 → 全部成功无 429（facts.md O4）。

---

### Phase 7 — U6 per-file editable plan v2

**估时**：1.5 天（doc 估 1 天，跨 backend + web 双端，含 PerFilePlanEntry 序列化路径 + WS bridge round-trip 测试）

**输入依赖**：
- Phase 1（U1 的 `is_chunked` / `chunk_count` 字段可能在 per-file rationale 中引用，facts.md M3）；
- Phase 4 优先（worktree 默认开后，per-file 编辑的并发安全才完整）。

**交付物**：
- `src/models/plan.py`（决策见 §3 Q4：落 `MergePlan`）：
  - `class PerFileAction(str, Enum)`（doc §5.6.1，6 个枚举值）；
  - `class PerFilePlanEntry(BaseModel)`（doc §5.6.1：file_path / action / risk_level / confidence / steps / rationale / edited_by_human / edited_at）；
  - **P2-1 修订**：必须新增 `from datetime import datetime` import（`src/models/plan.py` 当前未 import；`edited_at: datetime | None` 字段所需）；
  - 给 `MergePlan` 加 `per_file_entries: list[PerFilePlanEntry] = Field(default_factory=list)`（`MergePlanLive` 通过继承自动获得）。
- `src/agents/planner_agent.py`：phase 生成时同步生成 `PerFilePlanEntry`（action 从 risk_level 推；steps 默认空，doc §5.6.2）。
- `src/tools/merge_plan_report.py`：MERGE_PLAN_*.md 渲染 per-file 表格。
- `src/web/ws_bridge.py`：新增 `update_per_file_entry` 消息类型（facts.md J1）。
- `web/src/views/PlanReview.tsx`：行可展开 → action 下拉 + steps textarea + "edited by you" 标记（facts.md J2）。
- `src/core/phases/auto_merge.py`：执行前先看 `entry.action`；`edited_by_human=True` → 直接按 entry.action 路由，跳过 LLM 决策（facts.md H3）。
- 单测 5 个（doc §5.6.3）：`test_planner_emits_per_file_entries` / `test_per_file_entry_action_derived_from_risk` / `test_human_edit_marks_entry` / `test_auto_merge_respects_human_edited_action` / `test_per_file_plan_in_merge_plan_report`。
- web 单测扩展 `PlanReview.test.tsx`（已存在，facts.md J2）：行展开 + edit + WS round-trip。
- commit 范围：3 commit。
  - `feat(models): PerFileAction + PerFilePlanEntry + MergePlan(Live) per_file_entries 字段`
  - `feat(planner,merge_plan_report): per-file 计划生成 + MD 渲染`
  - `feat(web,auto_merge): per-file 编辑 UI + ws_bridge update_per_file_entry + auto_merge 短路`

**GO 条件**：
- 5 个新单测 + web 测试全绿。
- 集成冒烟：在 fixture repo 上 plan → 改一行 action → approve → executor 走人类选择（facts.md O6）。
- mypy strict 通过。
- 覆盖率 ≥80%（CLAUDE.md / facts.md K4）。

---

## 3. 关键技术决策

每条带 file:line 锚点（facts.md）+ 决策理由。

### 3.1 Q1: `max_cost_usd` vs `per_run_cost_limit_usd`

**facts.md 锚点**：G5 `src/core/orchestrator.py:262-280` 已实装 `max_cost_usd` ceiling check；I3 `src/models/config.py:949-954`；doc §5.2.1 提议新名。

**决策**：**保留 `max_cost_usd`，不引入新字段；语义升级 + 行为统一**。

**做法**：
1. `MergeConfig.max_cost_usd: float | None` 字段保留，default 从当前 `None` → **`5.0`**（doc §5.2.1 默认值）。
2. 现有 orchestrator ceiling check（facts.md G5）仍保留——它是 phase 边界粗粒度兜底。
3. U2 新增的 `_call_llm_with_retry` 内 per-call check 是细粒度补充：触发时 raise `RunBudgetExceeded` → orchestrator 新增的 `except` 分支处理；同时**确保 G5 的 ceiling 路径在 RunBudgetExceeded 已 transition 后短路**（用 `state.status == AWAITING_HUMAN` 提前 return）。
4. doc §5.2.1 提议的 `per_run_cost_warn_pct` 作为**新字段**加入（无重复），default `0.8`。

**理由**：
- 避免引入两个字段做同一事——facts.md A4 项目通用性约束反对冗余配置。
- 现有 `max_cost_usd` 已被 orchestrator 消费且有测试覆盖（facts.md G5 隐含），改名意味着用户 yaml 迁移成本。
- 默认值从 `None → 5.0` 属于行为变更，CHANGELOG 标记，按 doc §6 "向后兼容" 段说明：`None` 仍合法，但默认更安全。

**风险与对策**：
- **已知受影响测试（P0-1 修订，必须随 Phase 2 commit #1 一起提交）**：
  - `tests/unit/test_telemetry_snapshot.py:125 test_max_cost_usd_field_defaults_none` — 当前断言 `config.max_cost_usd is None`；处理动作 = 断言改 `== 5.0`，方法名同步更名为 `test_max_cost_usd_defaults_to_five_dollars`。
  - 新增 `test_max_cost_usd_can_be_disabled_with_none` — 显式传 `None` 仍合法，且 orchestrator ceiling check 不触发（保留 backward compat 路径）。
  - `tests/unit/test_telemetry_snapshot.py:140` 处 `max_cost_usd=1.0` 显式赋值 → 不受影响。
- **未量化风险**：Phase 2 开工前必须再 grep 一遍 `max_cost_usd` 全 tests/ 树（含 conftest / fixture），若有隐式 default 依赖（如 `MergeConfig()` 后断言 `.max_cost_usd is None`）一并列入清单。

---

### 3.2 Q2: 7 个合约 yaml 加 `version` 字段

**facts.md 锚点**：A3 列出 7 个 yaml；doc §5.3.3 要求 contract version 入 cache key。

**决策**：**统一在 Phase 0 新增 `version: 1` 字段；后续改 prompt / 改聚合规则时手动 bump，约定写入 `_schema.md`**。

**做法**：
1. `src/agents/contracts/_schema.md` 新增章节"Versioning"：说明何时 bump version（修改 prompt 内容 / 修改 aggregation 规则 / 修改 input/output schema）；
2. 7 个 yaml 顶层加 `version: 1`；
3. 加载器（contract 解析代码）兼容旧 yaml 无 version 字段（缺省视作 0，cache 会 miss 一次以拉新），但 `tests/unit/test_agent_contracts.py` 强制存在性。

**理由**：
- 集中在 Phase 0 改 7 个文件减少 reviewer 翻来覆去审同一批 yaml 的成本。
- bump 责任放 `_schema.md` 文档化，避免散落各 agent 注释。
- 缺省视作 0 的兼容是 mypy 友好（`int = 0` 比 `int | None` 更易处理）。

**风险与对策**：bump version 容易漏 → 在 PR template / git pre-commit 加提醒（**不在本计划范围**，记入 P2）。

---

### 3.3 Q3: U7 worktree 现有测试处理

**facts.md 锚点**：Q3 doc §5.7 注；H1 `src/core/orchestrator.py:240-247`；I4 `src/models/config.py:956-963`。

**决策**：**Phase 4 开工第一步先 grep 全量测试集，按"原意"3 选 1 处理（见 Phase 4 交付物）**。

**已知影响清单（P1-1 修订，v1 已完成 grep）**：

| 测试 | 锚点 | 原意 | 处理动作 |
|---|---|---|---|
| `test_enable_working_branch_defaults_false` | `tests/unit/test_working_branch.py:72-75` | 验证 default=False（U7 变更下与新行为冲突） | 重命名为 `test_enable_working_branch_defaults_true`；断言改 `is True` |
| `test_enable_working_branch_can_be_set` | `tests/unit/test_working_branch.py:77-83` | 显式传 True 仍生效 | 无需改动（断言已 True，与新 default 兼容） |
| **新增** `test_enable_working_branch_can_be_disabled_with_explicit_false` | 新增 | 验证显式 False 不被新 default override | 新增测试，覆盖 backward compat 路径 |

**做法**：
- Phase 4 第一个 commit 之前**再次** `grep -rn enable_working_branch tests/ && grep -rn active_branch tests/`，复核上述清单是否完整；新发现条目追加到 `.multi-agent/large-scale-perf/decisions/u7-affected-tests.md`；
- gatekeeper-plan 在 Phase 4 启动时核对清单完整性；
- 修改原则：**优先保留测试原意**，不为通过测试而压低断言强度。

**理由**：
- facts.md K3 严禁 regression，盲改测试是 regression 的种子；
- 清单先于代码改动出现，让 verifier 能独立判断测试改动是否合理。

**风险与对策**：grep 漏掉 `enable_working_branch` 间接消费（例如 fixture 默认 config）→ Phase 4 测试通过 + integration 全跑双重把关。

---

### 3.4 Q4: PerFilePlanEntry 落 `MergePlan` 还是 `MergePlanLive`

**facts.md 锚点**：E3 `src/models/plan.py:215-227` `MergePlan`；E4 `MergePlanLive` 继承 + execution/judge/gate 等运行态字段。

**决策**：**落 `MergePlan`（基类），`MergePlanLive` 通过继承自动获得**。

**做法**：
- `PerFilePlanEntry` 是 plan 的**静态结构**（action / risk / steps / rationale），属于"计划本身"，非"运行态"；
- `edited_by_human` / `edited_at` 是计划被人编辑的元数据，仍然是 plan static metadata，不是 execution state；
- 落 `MergePlan` 意味着：序列化 `MERGE_PLAN_*.md` 时直接拿到；checkpoint resume 时 entries 不丢；Web UI 在 plan_reviewing 阶段就能编辑。

**理由**：
- 真正的"运行时"字段（如 entry 的执行进度 queued/in-progress/done）若未来加入，应放 `MergePlanLive`；当前 v2 不在 doc §5.6.1 范围内，本计划不预判。
- doc §5.6.2 提到 "executor 优先看 entry.action"——executor 读 `MergePlanLive`，由继承自然可见。

**风险与对策**：`MergePlan` 已有 12 个字段（facts.md E3），文件 ≤800 lines 约束（CLAUDE.md）。Phase 7 开工前先 `wc -l src/models/plan.py`，若接近 600 lines 则把 `PerFilePlanEntry` / `PerFileAction` 拆 `src/models/per_file_plan.py` 单独文件。

---

## 4. 风险与对策

| 风险 | 影响 Phase | 对策 |
|---|---|---|
| **mypy strict 类型推断风险**（U3 SQLite schema） | Phase 5 | 用 `sqlite3.Row` factory + 显式 `cast`；JSON column → `str` 存 / Pydantic 解；不用 `Any`；对外接口都用 TypedDict 或 Pydantic model。 |
| **并发引入的 race**（U4 sliding window） | Phase 6 | 用 `asyncio.Lock` 保护 deque pop/push；测试中用 `asyncio.Event` 同步而非 `sleep`；窗口边界用 `time.monotonic()` 不用 `time.time()`（避免系统时钟跳跃）。 |
| **向后兼容**（U7 default 变更 + U2 default 变更） | Phase 2 / Phase 4 | CHANGELOG 显式声明；`merge validate` 输出友好提示；现有 `.merge/config.yaml` 不需要重写（field 缺省即取新默认）；Phase 4 grep 现有测试避免 regression。 |
| **test regression**（cov ≥80% 维持） | 全部 | 每 Phase commit 前 `pytest tests/unit/ --cov=src --cov-report=term-missing` 本地跑；新增模块（如 `agent_output_cache.py` / `rate_budget.py`）必须自带 ≥85% 测试以拉高总均；CLAUDE.md "新增模块覆盖率不能拉低总体"。 |
| **`max_cost_usd` 双路径互锁**（Q1 决策伴随风险） | Phase 2 | orchestrator ceiling check 与 `_call_llm_with_retry` per-call check 必须**幂等**：在 BaseAgent raise RunBudgetExceeded 后，ceiling check 看到 status==AWAITING_HUMAN 短路 return；写 `test_budget_double_transition_idempotent` 验证。 |
| **contract version bump 易漏**（Q2 跟进） | Phase 5 后续维护 | `_schema.md` 文档化；本计划不引入 git hook 自动化（超 doc 范围）。 |
| **7 yaml 一次性改动 PR 体量**（Phase 0） | Phase 0 | 7 个 yaml 仅 +1 line 各；diff 总量 ≤14 lines；gatekeeper 易审。 |
| **U6 跨端调试成本**（Phase 7） | Phase 7 | 先写 backend + 单测全绿，再做前端；前端用 `vitest` mock WS 消息，不依赖真后端启动。 |
| **integration test 真 API key 依赖**（Phase 5 cache 命中率验证） | Phase 5 GO 条件 | 只在 `tests/integration/` 下；CLAUDE.md 已声明不进 CI；手工跑 `pytest tests/integration/test_cache_hit_rate.py -v` 验证。 |
| **`executor_agent.py` / `config.py` 文件大小约束（P2-2 修订）** | Phase 1 / 2 / 5 / 6 | 当前 `executor_agent.py` 1026 行（已超 CLAUDE.md "<800" 软约束）、`config.py` 971 行（接近）。Phase 1 的 U1.A 在 executor 内是搬动非新增，影响可控；Phase 2 / 5 / 6 都向 config 追加 schema。**应急策略**：Phase 5 完成时 `wc -l src/models/config.py`，若 > 1100 行立刻将 `CacheConfig` / `RateLimitConfig` / `ThresholdConfig` 拆 `src/models/config_sections/` 子模块（独立 commit `refactor(config): 拆分 sections 子模块`），保持 `from src.models.config_sections import ...` re-export 不破坏调用方。Phase 1 的 `_aggregate_chunked_analyses` 若导致 executor 超 1100 行，同步拆 `src/agents/conflict_aggregation.py`。U5 helper（Phase 3）inline 到 parallel_file_runner.py（65 行，余量充足），不入 executor。 |

---

## 5. 验收标准（与 doc §10 O1-O8 逐项对齐）

| 编号 | doc §10 描述 | 对应 Phase | 验收方法 |
|---|---|---|---|
| **O1** | forgejo 1822-file run 中任何文件 >40KB 走 chunked；fast-path 命中率 ≥60%；hard cap 触发率 <5% | Phase 1 | 集成测试（手工跑）在 forgejo fixture 上 run，统计 chunked path 触发次数 + fast/slow path 比例。 |
| **O2** | 故意构造超 budget 小 run → 转 AWAITING_HUMAN + 报告文件存在 | Phase 2 | `test_budget_exceeded_writes_partial_report` + 手工 mock 超 cap 场景。 |
| **O3** | forgejo 二次 run cache 命中率 ≥90%（classifier + conflict_analyst） | Phase 5 | 集成测试 `tests/integration/test_cache_hit_rate.py`（手工跑）。 |
| **O4** | 200 文件并发 fan-out 对 50 RPM provider → 0 个 429 | Phase 6 | `test_parallel_runner_with_rate_budget_does_not_429` + 手工对真 anthropic 限速 50 RPM key 跑一次 200 文件。 |
| **O5** | 故意重合的 shard → 立刻 raise | Phase 3 | `test_disjoint_assert_raises_on_overlap`。 |
| **O6** | Web UI 行可展开编辑 action + steps；改后 executor 走 human 选择 action | Phase 7 | `test_human_edit_marks_entry` + `test_auto_merge_respects_human_edited_action` + 手工 web 冒烟。 |
| **O7** | fresh repo 跑 `merge` → `git branch` 看到 `merge/auto-*` | Phase 4 | `test_orchestrator_creates_branch_on_run_when_enabled` + 手工 fresh 目录冒烟。 |
| **O8** | 总体：1822 文件首次 run 在 budget 内完成；二次 run 5min（cache 命中）；无 429 / timeout；plan 可逐文件编辑；测试全绿 | Phase 7 完成后 | 全套 unit + integration + 手工 forgejo 端到端跑一次（真 API key）。 |

**验收基线（doc §8 末尾）**：
- 当前主分支：~$25/run / 跑死或 47%+ 文件未处理；
- U1+U2 完成（Phase 2 末）：$25 上限触发 budget cap，部分结果落盘；
- U3 完成（Phase 5 末）：二次 run < $1；
- 全部完成（Phase 7 末）：首 run < $20、二次 run < $1。

---

## 6. Commit 计划

### 6.1 Commit 类型映射

| Phase | 主类型 | 次类型 |
|---|---|---|
| Phase 0 | `chore` | `test` |
| Phase 1 | `perf` + `refactor` | `feat`（schema 新字段） |
| Phase 2 | `feat` | `perf`（autosubmit 行为） |
| Phase 3 | `feat` | — |
| Phase 4 | `chore` | `test` |
| Phase 5 | `feat` | `perf`（cache hit 节省成本） |
| Phase 6 | `feat` | `perf`（防 429） |
| Phase 7 | `feat` | — |

### 6.2 Commit 数量与边界

总 commit 数预估 **19 个**（Phase 0:1 / Phase 1:3 / Phase 2:3 / Phase 3:1 / Phase 4:2 / Phase 5:4 / Phase 6:2 / Phase 7:3）。v2 修订未改 commit 数，但 Phase 0 / Phase 2 / Phase 3 的单 commit 内容增厚（详见各 Phase 交付物）。

每 commit 必须：
- 包含 src 改动 + 对应测试（不允许"代码 commit + 测试 commit"分开）；
- 通过 `pytest tests/unit/ -q && mypy src && ruff check src/`（doc §9 commit 切分约定）；
- conventional commits 格式（CLAUDE.md `git-workflow.md` 锁定）。

### 6.3 Commit message 范本

```text
perf(conflict_analyst): 大文件走 chunked analysis + 确定性聚合

- 当 max(current, target) > chunk_size_chars * 2 时按 AST 切 chunks
- ParallelFileRunner 并发 LLM 调用
- Reducer 三层路径：hard cap → fast path（unanimous） → slow path（precedence + 0.8 惩罚）
- 新增 ConflictAnalysis.is_chunked / chunk_count 字段供下游可见
- 6 个新单测覆盖 fast / slow / hard cap / security / count tracking

Refs: doc/large-scale-file-processing-optimization.md §5.1
```

```text
feat(base_agent,orchestrator): budget cap → RunBudgetExceeded → AWAITING_HUMAN + partial report

- _call_llm_with_retry 前后双检查 cost_tracker.total_cost_usd vs max_cost_usd
- 超 limit raise RunBudgetExceeded
- orchestrator 新增 except RunBudgetExceeded 分支：写 budget_exceeded_report.md + transition AWAITING_HUMAN + checkpoint tag
- 80% warn 触发一次 activity event "budget_warning"
- 与既有 phase-边界 ceiling check 协同：status==AWAITING_HUMAN 后短路避免 double-transition

Refs: doc/large-scale-file-processing-optimization.md §5.2
```

---

## 附 A: 不在本计划范围（避免越界）

明确**不做**的事（gatekeeper-plan 不应要求加入）：
- 单一文件内的 AST 切分细节（doc §1 "不在范围"已声明）；
- Prompt 内容微调（doc §1）；
- 模型路由策略（doc §1）；
- Prompt caching 优化（任务背景"你不要做" — U8 等扩展）；
- 具体测试用例的输入 / 期望 / 断言（Verifier 的活）；
- 实际代码实现（Executor 的活）；
- doc §10 列出之外的额外验收门槛。
