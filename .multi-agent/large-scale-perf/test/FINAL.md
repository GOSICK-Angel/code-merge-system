# Test Plan v3 — large-scale-perf

> Verifier 基于 v2（已通过 FINAL）叠加 Phase 3 + Phase 4 用例，并在 Phase 2 内补 lock #27 路径 A 的 3 个用例。
> 范围严格扩展到 Phase 2 + 3 + 4（scope.md §6 锁定）。Phase 0/1 视为已完成基线（regression 守护，**用例完全保留不动**）。
> 所有新增用例编号严格延续：U-P2.14 / U-P2.15 / U-P2.16（Phase 2 补强）/ U-P3.1~U-P3.8（Phase 3）/ U-P4.1~U-P4.5（Phase 4）/ E2E-P4.A。

---

## 0. 版本与修订说明

| 项 | v2 | v3 |
|---|---|---|
| Plan 版本 | v2（FINAL.md / sha 4826a6e+） | v2（同上，未改 plan 锁清单） |
| Test Plan 版本 | v2 | v3 |
| Scope | Phase 0 + Phase 1 + Phase 2 | Phase 0 + Phase 1 + Phase 2 + **Phase 3** + **Phase 4** |
| 覆盖 doc §10 | O1 / O2 | O1 / O2 / **O5**（Phase 3 末）/ **O7**（Phase 4 末） |
| Phase 0 单元 | 8 | 8（不变） |
| Phase 1 单元 | 12 | 12（不变） |
| Phase 2 单元 | 13 | **16**（+3：lock #27 路径 A） |
| Phase 2 Web | 1 | 1（不变） |
| Phase 3 单元（新增） | — | **8** |
| Phase 4 单元（新增） | — | **5** |
| Phase 1 手工 E2E | 2 | 2（不变） |
| Phase 2 手工 E2E | 2 | 2（不变） |
| Phase 4 手工 E2E（新增） | — | **1** |
| **总用例数** | 38 | **55**（47 单元 + 1 Web + 7 手工 E2E） |

**v3 关键修订摘要**：

- **A**：Phase 2 §2.3.5 新增 lock #27 路径 A 3 用例（U-P2.14 / U-P2.15 / U-P2.16），守护 `MergeState.thresholds` 字段 + orchestrator init phase 复制 + `conflict_analyst.run()` 真正驱动 `view.thresholds`（同时解决 HANDOFF §4.3 未编号 P2）。
- **B**：Phase 3 §2.4 新增 disjoint contract 8 用例（U-P3.1 / U-P3.2 基础 assert 行为；U-P3.3 ~ U-P3.8 覆盖 6 处具名接入点 — lock #5）。
- **C**：Phase 4 §2.5 新增 worktree default 5 单元 + 1 手工 E2E。其中 U-P4.1 / U-P4.2 / U-P4.3 直接对应 lock #3 列出的 `test_working_branch.py:72-83` 改动清单。
- **D**：Phase 0 / Phase 1 / Phase 2 §2.3.1 ~ §2.3.4 原 v2 内容**完整保留不动**（regression 守护）。
- **E**：§7 / §8 / §11 / §12 整体扩表纳入新增用例；§9 不在范围明确排除 Phase 5/6/7。

---

## 1. 测试金字塔总览

| 层级 | 工具 | 数量 | 比例 | 运行频率 |
|---|---|---|---|---|
| 单元测试（pytest） | `pytest` + `patch_llm_factory` | 47 | ~85% | 每次 push / 每 commit |
| 集成测试（pytest） | `pytest tests/integration/` | 0 | 0% | — |
| Web 单元（vitest） | `vitest` + RTL | 1（`RunDashboard.test.tsx`） | ~2% | 每次 push |
| 手工 E2E | `merge` CLI + mock cost tracker / fresh dir | 7 | ~13% | Phase 1/2/4 末 |

**集成测试为 0** 的说明同 v2（Phase 0-4 验收皆可单元 + 手工冒烟覆盖；集成属 doc §10 O3/O4/O8 → Phase 5/6/7 范畴，本会话排除）。

---

## 2. 按 Phase 拆分用例

### 2.1 Phase 0 — 基础设施

**覆盖 GO 条件**：
- G0-1：`pytest tests/unit/test_agent_contracts.py tests/unit/test_run_budget_exceeded_dataclass.py -v` 全绿
- G0-2：`mypy src` 无新增 error
- G0-3：`ruff check src/` 无新增 warning
- G0-4：未引入任何运行时行为变化（异常未 raise；version 字段未消费）

> 本 Phase 用例 v3 完全继承 v2，不重复展开矩阵；详见 `test/FINAL.md` v2 §2.1。

#### 2.1.1 单元测试矩阵（v2 原文保留，简表）

| 编号 | 名称 | 锚点 |
|---|---|---|
| **U-P0.1** | `RunBudgetExceeded.__init__` + 异常层级（4 sub-assert） | [plan #8] / `src/models/state.py` |
| **U-P0.2** | `AgentContract.version` 默认值（兼容性） | [plan #7] / `src/agents/contract.py:19` |
| **U-P0.3** | `AgentContract.version` 显式 `0` 合法 | 同上 |
| **U-P0.4** | `AgentContract.version` 负数被拒 | 同上 `ge=0` 约束 |
| **U-P0.5** | 7 contract yaml 实地 `version: 1` + sanity gate | facts.md A3 + P1-3 修订 |
| **U-P0.6** | yaml 加载到 `AgentContract` 后 `.version == 1` | [plan #7] |
| **U-P0.7** | 缺 `version` 字段的 mock yaml 仍可加载（兼容） | P1-3 修订 |
| **U-P0.8** | 现有 `test_agent_contracts.py` 全部 anti-pattern 用例不破 | facts.md A5 regression net |

**用例数**：8

#### 2.1.2 集成测试 / 手工 E2E

无。

#### 2.1.3 边界与失败场景小结

- 兼容性三态：缺字段（U-P0.7） / 显式 0（U-P0.3） / 负数（U-P0.4）
- yaml 实地双锁：U-P0.5（fs read） + U-P0.6（pydantic 反序列化）
- 异常继承防御：U-P0.1 (c) (d)

---

### 2.2 Phase 1 — U1 conflict_analyst chunked analysis

**覆盖 GO 条件**：
- G1-1：6 个新单测 + 现有 `tests/unit/test_conflict_analyst*` 全绿
- G1-2：`tests/unit/test_agent_contracts.py` 通过（含 `thresholds` 新 input）
- G1-3：`mypy src` / `ruff check src/` 零新增
- G1-4：总覆盖率不低于基线
- G1-5（doc §10 O1）：chunked path 触发 / fast ≥60% / hard cap <5%（手工冒烟）

> 本 Phase 用例 v3 完全继承 v2，**不改任何用例编号 / 输入 / 期望 / 锚点**；详见 `test/FINAL.md` v2 §2.2。

#### 2.2.1 单元测试矩阵（简表）

| 编号 | 名称 | 锚点 |
|---|---|---|
| **U-P1.1** | `test_staged_content_runs_without_memory_store`（U1.A 解耦） | [plan #9] / [code-phase-1 #23] |
| **U-P1.2** | `test_chunked_path_fast_unanimous` | doc §5.1.1 fast path / [code-phase-1 #25] |
| **U-P1.3** | `test_chunked_path_slow_disagreement` | doc §5.1.1 slow path + precedence |
| **U-P1.4** | `test_chunked_hard_cap_escalates`（9 chunks → ESCALATE_HUMAN） | doc §5.1.1 hard cap 伪码 + [test #13] |
| **U-P1.5** | `test_chunked_security_falls_to_slow_path` | doc §5.1.1 fast path 条件 + [test #15] |
| **U-P1.6** | `test_chunked_aggregation_chunk_count_tracked` | [plan #1] 新字段 |
| **U-P1.7** | `test_chunked_threshold_not_triggered_below_40kb`（39999） | facts.md I1 / [code-phase-1 #24] |
| **U-P1.8** | `test_chunked_threshold_triggered_at_40kb_plus_one` + import 来源锁 | [plan #6] / [code-phase-1 #24] |
| **U-P1.9** | `test_chunked_llm_failure_one_chunk_falls_back_to_escalate` | [test #16] spec-by-test |
| **U-P1.10** | `test_conflict_analyst_yaml_inputs_include_thresholds` | facts.md C4 |
| **U-P1.11** | `test_conflict_analyst_restricted_view_can_read_thresholds` | facts.md A5 anti-pattern #5 |
| **U-P1.12** | `test_aggregate_chunked_analyses_is_pure_function` | doc §5.1.1 确定性 reducer |

**用例数**：12

#### 2.2.2 手工 E2E（O1 验收）

| 编号 | 命令草稿 | 期望输出锚点 |
|---|---|---|
| **E2E-P1.A** | `cd <forgejo-fixture> && merge --ci --dry-run 2>&1 \| tee phase1-e2e.log` | `grep -c 'is_chunked=True' phase1-e2e.log` ≥1；`grep 'chunk_count=' phase1-e2e.log` 至少一行 |
| **E2E-P1.B** | 解析 `<repo>/.merge/plans/MERGE_PLAN_<run_id>.md` + `merge_report.md` | fast path（rationale 含 `unanimous`）占 ≥60%；hard cap（含 `too large for safe chunked analysis`）占 <5% |

#### 2.2.3 边界与失败场景小结（v2 原文保留）

- 阈值边界：39999（U-P1.7） / 40001（U-P1.8）双侧
- 单 chunk LLM 失败（U-P1.9）
- 三路径：fast / slow / hard cap / security 强制 slow
- memory_store=None 路径修复（U-P1.1）
- 反向 import 防御（U-P1.8）
- reducer 纯度（U-P1.12）

---

### 2.3 Phase 2 — U2 per-run budget + autosubmit

**覆盖 GO 条件**：
- G2-1：5 个新单测 + RunDashboard web 测试全绿
- G2-2：现有 `tests/unit/test_agent_contracts.py`（anti-pattern #2）regression
- G2-3：`mypy src` 零新增 error
- G2-4：手工冒烟构造 mock cost tracker → AWAITING_HUMAN + 报告
- G2-5：现有 `max_cost_usd` ceiling 路径（facts.md G5）测试仍通过
- **G2-6（v3 新增 / lock #27 路径 A）**：`MergeState.thresholds` 字段存在 + orchestrator init phase 从 `config.thresholds` 复制 + `conflict_analyst.run()` 真正驱动 `view.thresholds` 到 `analyze_file`

> 本 Phase v3 = v2 §2.3 完整保留 + 末尾追加 §2.3.5（lock #27 路径 A 3 用例）。U-P2.1 ~ U-P2.13 / U-W2.1 / E2E-P2.A/B 全部**不动**。

#### 2.3.1 单元测试矩阵（v2 原文保留，简表）

| 编号 | 名称 | 锚点 |
|---|---|---|
| **U-P2.1** | `test_budget_exceeded_at_hard_cap_raises` | facts.md G3 / [plan #4] / [plan #8] |
| **U-P2.2** | `test_budget_exceeded_transitions_to_awaiting_human` | facts.md H2 / `orchestrator.py:346` |
| **U-P2.3** | `test_budget_warning_emits_event_at_80pct`（首次跨越 + 不重复） | plan §2 Phase 2 base_agent 第 3 项 |
| **U-P2.4** | `test_budget_disabled_when_limit_is_none` | plan §3.1 Q1 |
| **U-P2.5** | `test_budget_exceeded_writes_partial_report` | doc §5.2.2 |
| **U-P2.6** | `test_budget_double_transition_end_to_end_scenario` | plan §4 风险表 row 5 + Q1 |
| **U-P2.7** | `test_max_cost_usd_defaults_to_five_dollars` | [plan #2] / P0-1 修订 |
| **U-P2.8** | `test_max_cost_usd_can_be_disabled_with_none` | P0-1 修订 |
| **U-P2.9** | `test_budget_exceeded_at_exact_limit`（==） | facts.md G5 `>=` 语义 |
| **U-P2.10** | `test_budget_exceeded_pre_and_post_call_check` | plan §2 Phase 2 base_agent 第 2 项 |
| **U-P2.11** | `test_run_dashboard_serializer_exposes_limit_and_warn` | facts.md J1 |
| **U-P2.12** | `test_run_budget_exceeded_phase_source_is_current_phase` | [plan #8] |
| **U-P2.13** | `test_g5_ceiling_check_skips_when_status_awaiting_human` | plan §3.1 Q1 |

#### 2.3.2 Web 单元测试矩阵（vitest，v2 原文保留）

| 编号 | 名称 | 锚点 |
|---|---|---|
| **U-W2.1** | `RunDashboard.test.tsx` budget 进度条三态 + None 隐藏 | plan §2 Phase 2 web / facts.md J2 |

#### 2.3.3 手工 E2E（O2 验收，v2 原文保留）

| 编号 | 命令草稿 | 期望输出锚点 |
|---|---|---|
| **E2E-P2.A** | `/tmp/budget-smoke` 写 `max_cost_usd: 0.01` 跑 `merge --ci` | `grep RunBudgetExceeded` ≥1；`grep AWAITING_HUMAN` ≥1；退出码 ≠ 0 且非 FAILED |
| **E2E-P2.B** | `ls /tmp/budget-smoke/.merge/runs/*/budget_exceeded_report.md` | 文件存在；`spent`/`limit`/`phase` 三 token 全在文件内容中 |

#### 2.3.4 边界与失败场景小结（v2 原文保留）

- budget 6 点：None / 0.01 / limit-ε 不触发 / limit 整临界 / limit+ε / 远超 99 倍
- 双 transition 幂等：U-P2.6（端到端） + U-P2.13（单元）
- pre/post check（U-P2.10）
- web 三态 + None hidden（U-W2.1 四 props）

#### 2.3.5 lock #27 路径 A 补强（v3 新增）

**背景**：scope.md §6 用户答复选定路径 A —— Phase 2 commit 内同步在 `MergeState` 加 `thresholds: ThresholdConfig` 字段；orchestrator init phase 从 `state.config.thresholds` 复制；`conflict_analyst.run()` 把 `view.thresholds` 真正驱动到 `analyze_file`（同时解决 HANDOFF §4.3 未编号 P2 "`analyze_file` 新参数 `run()` 未驱动"）。lock #27 [code-phase-1 #27] 锁定此风险，本会话必修。

| 编号 | 被测对象 | 输入 | 期望 | 断言锚点 | mock 边界 |
|---|---|---|---|---|---|
| **U-P2.14** `test_merge_state_has_thresholds_field` | `MergeState.thresholds` 字段定义 + 默认值 + restricted_view 可读 | (1) `MergeState(...)` 默认实例化不传 `thresholds`；(2) `state.thresholds` 取值；(3) 任意 reader agent（如 `ConflictAnalystAgent`）`self.restricted_view(state).thresholds` 取值 | (a) `state.thresholds` 是 `ThresholdConfig` 实例（不为 None）；(b) `state.thresholds.chunked_aggregation_min_confidence == 0.85`（与 `config.thresholds` 同默认）；(c) restricted_view 取 `thresholds` 不 raise `FieldNotInContract`；(d) `MergeState.model_fields` 含 `"thresholds"` key | scope.md §6 路径 A 第 1 步；plan §1.1 Phase 1 thresholds 入参；facts.md A5 anti-pattern #5；[code-phase-1 #27] | 无（纯 pydantic 默认值 + 字段存在性验证）；conflict_analyst contract yaml 必须已含 `thresholds` 入参（U-P1.10 守护） |
| **U-P2.15** `test_orchestrator_init_phase_copies_thresholds_from_config` | orchestrator init phase 复制语义 | mock `MergeConfig(thresholds=ThresholdConfig(chunked_aggregation_min_confidence=0.91, risk_score_low=0.42))`；进入 orchestrator init phase | (a) init phase 完成后 `state.thresholds.chunked_aggregation_min_confidence == 0.91`；(b) `state.thresholds.risk_score_low == 0.42`；(c) `state.thresholds is not state.config.thresholds`（**不直接引用 config，保持运行态快照语义**）；(d) 后续修改 `state.config.thresholds.chunked_aggregation_min_confidence = 0.99` 不影响 `state.thresholds`（**深 copy 或重新构造**，验证快照独立性） | scope.md §6 路径 A 第 2 步"orchestrator init phase 中从 `state.config.thresholds` 复制"；运行态快照语义 | mock orchestrator 仅跑到 init phase 完成；不真跑后续 phase |
| **U-P2.16** `test_conflict_analyst_run_drives_thresholds_param` | conflict_analyst.run() 真正驱动 thresholds | (1) 构造 `MergeState` 含 `thresholds=ThresholdConfig(chunked_aggregation_min_confidence=0.72)`；(2) 触发 `ConflictAnalystAgent.run(state)`；(3) mock `analyze_file` 截获实际入参 | (a) `analyze_file` 被调用的所有次中，`thresholds.chunked_aggregation_min_confidence == 0.72`（**不再走 Phase 1 残留的 mock + setattr 合成默认 0.85 路径**）；(b) 调用入参 `thresholds` 来源 = `view.thresholds`，**非** `view.config.thresholds`（断言：mock 的 ThresholdConfig 实例 `id()` 与 `state.thresholds` 相同）；(c) U-P1.11 仍绿（restricted_view 读 thresholds 不破） | scope.md §6 路径 A 第 3-4 步；HANDOFF §4.3 未编号 P2 修复；[code-phase-1 #27] | patch `ConflictAnalystAgent.analyze_file`（`MagicMock(wraps=原)` + 截获 kwargs `thresholds`）；patch_llm_factory；mock MergeState 内置 ThresholdConfig；**绝不** mock `restricted_view`（验证真实链路） |

**用例数（Phase 2 补强）**：3

#### 2.3.6 Phase 2 用例汇总（v3）

| 来源 | 编号区间 | 数量 |
|---|---|---|
| Phase 2 单元（v2 保留） | U-P2.1 ~ U-P2.13 | 13 |
| Phase 2 单元（lock #27 路径 A 新增） | U-P2.14 ~ U-P2.16 | 3 |
| Phase 2 Web | U-W2.1 | 1 |
| Phase 2 手工 E2E | E2E-P2.A / E2E-P2.B | 2 |
| **Phase 2 小计** | | **19** |

---

### 2.4 Phase 3 — U5 disjointness contract（**v3 新增**）

**覆盖 GO 条件**：
- G3-1：8 个新单测全绿（plan §2 Phase 3 GO 条件原文 "4 个新单测"；plan §2 Phase 3 交付物列名 6 条；本测试方案在 lock #5 6 接入点 1:1 守护基础上加 2 helper 基础行为单测，实际 8 个 — **未越 lock #5 6 接入点范围**，仅补 helper 自身正/反路径；见下注）
- G3-2：现有 `tests/unit/test_executor*` / `tests/unit/test_planner*` / `tests/unit/test_judge*` / `tests/unit/test_conflict_analyst*` 不破
- G3-3：`mypy src` / `ruff check src/` 零新增
- G3-4（doc §10 O5）：故意重合的 shard → 立刻 raise `FileShardOverlap`（U-P3.2 守护）

> **接入点数与单测数**：plan §1.1 lock #5 锁定 6 处具名接入点（`conflict_analyst.py:81` / `executor.py:829` / `planner.py:645` / `judge.py:167` / `judge.py:1473` / Phase 1 新增 `conflict_analyst._chunked_analyze_file` line 277-280，详见 [code-phase-1 #24]）。本测试方案为每处具名接入点设 1 个单测（6 个），加 2 个 helper 基础行为单测（U-P3.1 / U-P3.2），共 **8 个单元用例**。Plan §2 Phase 3 估时 "0.5 天 / 1 commit"，单测数小幅扩展不增加 commit 边界（与 lock #5 一致）。

#### 2.4.1 单元测试矩阵

| 编号 | 被测对象 | 输入 | 期望 | 断言锚点 | mock 边界 |
|---|---|---|---|---|---|
| **U-P3.1** `test_disjoint_assert_passes_for_clean_shards` | `assert_disjoint_file_shards(shards)` helper（doc §5.5.1） | `shards = [["a.py", "b.py"], ["c.py"], ["d.py", "e.py"]]`（5 文件 0 重合） | (a) 不 raise；(b) 返回 None（doc §5.5.1 伪码 `-> None`）；(c) shards 顺序保持（**不副作用排序**） | plan §1.1 / plan §2 Phase 3 第 1 用例；doc §5.5.1 伪码 | 无（纯函数） |
| **U-P3.2** `test_disjoint_assert_raises_on_overlap` | `assert_disjoint_file_shards` 异常路径 | `shards = [["a.py", "b.py"], ["b.py", "c.py"]]`（`b.py` 重复） | (a) raise `FileShardOverlap` 异常；(b) `str(exc)` 含 `"b.py"`（重叠 key 列表）；(c) `issubclass(FileShardOverlap, ValueError) is True`（合理父类继承）；(d) `issubclass(FileShardOverlap, SystemExit) is False` | plan §1.1 / plan §2 Phase 3 第 2 用例；plan §2 Phase 3 "及自定义异常 `FileShardOverlap`" | 无（纯函数 + 异常类） |
| **U-P3.3** `test_executor_chunks_pass_disjoint_assert` | `executor_agent.py:829` `_chunk_issues_by_file` 后接入点 | mock executor `_chunk_issues_by_file` 输出 3 个 issue chunks（同文件 issues 已 group → 文件集天然 disjoint） | (a) `assert_disjoint_file_shards` 被调用 ≥1 次（验证已接入）；(b) 入参为 list-of-list-of-str（file_paths）；(c) 不 raise（chunks 实际 disjoint） | [plan #5] 第 2 接入点 `executor_agent.py:829`；plan §2 Phase 3 用例 3 | patch `src.core.parallel_file_runner.assert_disjoint_file_shards`（`MagicMock(wraps=原)`）观察 call_count；patch_llm_factory；其他路径走 fake |
| **U-P3.4** `test_planner_sub_chunks_pass_disjoint_assert` | `planner_agent.py:645` `_classify_batch` 切 sub-chunks 后 | mock planner `_classify_batch` 触发 sub-chunks 切分（如 ≥10 文件触发切分）；构造 disjoint sub-chunks | (a) `assert_disjoint_file_shards` 被调用 ≥1 次；(b) 入参对应 sub-chunks 的 file_path 集合；(c) 不 raise | [plan #5] 第 3 接入点 `planner_agent.py:645`；facts.md E1 | patch `assert_disjoint_file_shards`；patch_llm_factory；mock `_classify_batch` 输入文件列表 |
| **U-P3.5** `test_judge_per_file_fan_out_passes_disjoint_assert` | `judge_agent.py:167` high-risk per-file fan-out | mock judge 进入 per-file fan-out 分支；high-risk 文件 dict 含 3 个 file_path → 3 个 shards | (a) `assert_disjoint_file_shards` 被调用 ≥1 次；(b) 入参 = `[[fp] for fp in dict.keys()]` 形态；(c) dict.keys() 名义 disjoint → 不 raise | [plan #5] 第 4 接入点 `judge_agent.py:167` + plan §2 Phase 3 "理由：入参 dict.keys() 名义 disjoint，仍需 assert 防上游传入重复 keys" | patch `assert_disjoint_file_shards`；patch_llm_factory；mock judge state |
| **U-P3.6** `test_judge_chunk_runner_passes_disjoint_assert` | `judge_agent.py:1473` judge chunk runner | mock judge chunk runner 切分 ≥2 chunks；每 chunk 文件集 disjoint | (a) `assert_disjoint_file_shards` 被调用 ≥1 次；(b) 入参对应 chunks 的 file 集合；(c) 不 raise | [plan #5] 第 5 接入点 `judge_agent.py:1473` + plan §2 Phase 3 "chunk 拆分逻辑变更时 assert 是回归网" | patch `assert_disjoint_file_shards`；patch_llm_factory |
| **U-P3.7** `test_conflict_analyst_chunked_path_passes_disjoint_assert` | Phase 1 新增 `_chunked_analyze_file` line 277-280 接入点 | 触发 chunked path（file_diff >40KB）；split 出 3 chunks | (a) `assert_disjoint_file_shards` 被调用 ≥1 次（在 `ParallelFileRunner.from_api_key_env_list` 之前或之中）；(b) 同文件内多 chunk 的 file 集合天然 disjoint（每 chunk 对应同一文件但不同段落 — 实际入参可能为 `[[chunk_id]]` 或 `[[file_path]]` 视实施而定，本断言仅检 helper 被调）；(c) 不 raise | [plan #5] 第 6 接入点；[code-phase-1 #24] 真实 line 277-280；plan §2 Phase 3 "Phase 1 新增第 6 处" | patch `assert_disjoint_file_shards`；patch_llm_factory；构造大文件 ≥40001 字节触发 chunked |
| **U-P3.8** `test_conflict_analyst_multi_file_fan_out_passes_disjoint_assert` | `conflict_analyst_agent.py:81` multi-file fan-out 接入点 | mock conflict_analyst `run()` 触发 multi-file fan-out（≥2 文件 file_diffs）；每文件独立 shard | (a) `assert_disjoint_file_shards` 被调用 ≥1 次；(b) 入参 = `[[fp] for fp in file_keys]` 或等价形态；(c) file_keys 无重复 → 不 raise；(d) 故意构造重复 file_key（如 `file_diffs` 含 `"a.py"` 两次）→ 同样调用 helper 并 raise `FileShardOverlap`（**失败场景子断言**） | [plan #5] 第 1 接入点 `conflict_analyst_agent.py:81` + plan §2 Phase 3 "理由：防 file_diffs 重复 key 漏检" | patch `assert_disjoint_file_shards`；patch_llm_factory；分两个 sub-test：正常 & 故意重复 |

**用例数**：8（plan §2 Phase 3 列名 6 + 2 helper 基础 = 实际 8；与 lock #5 6 接入点对齐 + U-P3.1/3.2 兜底 helper 行为）

#### 2.4.2 集成测试 / 手工 E2E

无（plan §2 Phase 3 GO 条件全部单测可覆盖；doc §10 O5 由 U-P3.2 守护，无需手工冒烟）。

#### 2.4.3 边界与失败场景小结

- 正常 disjoint（U-P3.1 / U-P3.3 / U-P3.4 / U-P3.5 / U-P3.6 / U-P3.7 / U-P3.8 正常路径）
- 故意 overlap raise（U-P3.2 + U-P3.8 失败子断言）
- 接入点覆盖：6/6（lock #5 全量）
- 异常类继承防御（U-P3.2 (c) (d) sub-assert）

---

### 2.5 Phase 4 — U7 worktree defaults（**v3 新增**）

**覆盖 GO 条件**：
- G4-1：3 个新单测全绿（plan §2 Phase 4 列名）+ 2 个清单改动测试（lock #3）
- G4-2：全套 unit + integration 全绿（facts.md K3 严禁 regression）
- G4-3：`mypy src` / `ruff check src/` 零新增
- G4-4（doc §10 O7）：fresh 目录 `merge` → `git -C <fork> branch` 看到 `merge/auto-*`

> **plan §2 Phase 4 列名 3 用例** = `test_worktree_enabled_by_default_in_new_state` / `test_orchestrator_creates_branch_on_run_when_enabled` / `test_existing_yaml_explicit_false_still_respected`。lock #3 额外锁定 `test_working_branch.py:72-83` 现有 2 处显式断言改动。**实际单测数 = 3 plan 列名 + 2 现有改动 = 5**。U-P4.1 / U-P4.2 / U-P4.3 直接覆盖 lock #3 清单 + plan §3.3 Q3 决策清单的 3 行；U-P4.4 / U-P4.5 覆盖 plan §2 Phase 4 新增 wizard + orchestrator branch creation。

#### 2.5.1 单元测试矩阵

| 编号 | 被测对象 | 输入 | 期望 | 断言锚点 | mock 边界 |
|---|---|---|---|---|---|
| **U-P4.1** `test_enable_working_branch_defaults_true` | `MergeConfig.enable_working_branch` 新 default（lock #3 第 1 行清单） | `MergeConfig(...)` 不传 `enable_working_branch` | (a) `config.enable_working_branch is True`；(b) 文件名锚点验证：测试方法名是 `test_enable_working_branch_defaults_true`（而非旧 `_defaults_false`）；(c) 与 `tests/unit/test_working_branch.py:72-75` 原测试对齐迁移 | [plan #3] lock #3；plan §3.3 Q3 决策清单第 1 行；facts.md I4；plan §2 Phase 4 P1-1 修订 | 无（纯字段默认值）；**改动现有测试**：`test_working_branch.py:72-75` 旧 `test_enable_working_branch_defaults_false` 重命名 + 断言 `is False → is True` |
| **U-P4.2** `test_enable_working_branch_can_be_set` | 显式传 `True` 仍生效（兼容路径，lock #3 第 2 行清单） | `MergeConfig(enable_working_branch=True, ...)` | (a) `config.enable_working_branch is True`；(b) 测试已是新 default 同值，**无需改动测试体**（仅可能改 docstring） | [plan #3] lock #3 第 2 行；plan §3.3 Q3 决策清单第 2 行 | 无；**现有测试不动**：`test_working_branch.py:77-83` 维持 |
| **U-P4.3** `test_enable_working_branch_can_be_disabled_with_explicit_false` | 显式传 `False` 不被新 default override（新增 backward compat） | `MergeConfig(enable_working_branch=False, ...)` | (a) `config.enable_working_branch is False`；(b) 字段类型仍为 `bool`（无 `ValidationError`）；(c) orchestrator init phase 中若读取到此值，不进入 worktree 分支（**仅断言字段读取语义，不真跑 orchestrator**） | plan §3.3 Q3 决策清单第 3 行（**新增**）；plan §2 Phase 4 P1-1 修订；为防止 plan §3.3 Q3 "测试本意验证关闭分支的旁路 → 显式 `MergeConfig(enable_working_branch=False)`" 路径被静默砍掉 | 无（纯字段值断言） |
| **U-P4.4** `test_orchestrator_creates_branch_on_run_when_enabled` | orchestrator init phase 接入点（plan §2 Phase 4 列名第 2 用例） | (1) `MergeConfig(enable_working_branch=True)`（即新 default）；(2) mock fork repo + run 启动；(3) 截获 `git branch` 调用 | (a) orchestrator init phase 完成后，调用过 `git -C <fork> branch <new_branch_name>` 或等价 GitPython API；(b) 新分支名匹配正则 `^merge/auto-[0-9a-f]+$` 或 `^merge/auto-<run_id>$`（视 plan §2 Phase 4 wizard description "推荐：每 run 隔离写入"语义）；(c) `state.config.fork_ref` **未被改写到主分支**（保持半完成隔离） | plan §2 Phase 4 GO 条件 "新单测 3 个" 第 2 个；facts.md H1 `orchestrator.py:240-247`（plan §1.1 "**仅 default 生效，0 代码改动**"——但 default 翻转后 init phase 走入此路径） | mock `git.Repo.create_head` 或等价；不真改 fs；tmp_path 提供 fork 路径 |
| **U-P4.5** `test_setup_wizard_defaults_worktree_checkbox_on` | Setup wizard 默认勾选（plan §2 Phase 4 cli/ 改动） | (1) 加载 `src/cli/commands/setup.py` wizard 默认表；(2) 取 `worktree` / `enable_working_branch` 字段 default | (a) wizard 复选框 default 为 True（**勾选**）；(b) description 含 "推荐" 或 "isolat" 关键字（"推荐：每 run 隔离写入，避免 fork_ref 被半完成状态污染"，doc §5.7.2）；(c) wizard 字段名 = `enable_working_branch`（与 config key 一致，**不重命名**） | plan §2 Phase 4 cli/ 改动；doc §5.7.2；plan §3.3 Q3 决策 | 无 LLM；直接 import wizard 模块取 default 表；不跑 interactive |

**用例数**：5

#### 2.5.2 手工 E2E（O7 验收）

| 编号 | 命令草稿 | 期望输出锚点 |
|---|---|---|
| **E2E-P4.A** | (1) `mkdir /tmp/worktree-smoke && cd /tmp/worktree-smoke` (2) `git init && git commit --allow-empty -m init`（fresh 目录 + git 仓库）(3) 设 `ANTHROPIC_API_KEY` + `OPENAI_API_KEY` (4) `merge --ci 2>&1 \| tee worktree-smoke.log`（不写 `.merge/config.yaml` enable_working_branch，验证 default=True 生效）(5) `git -C <fork_path> branch` 列出分支 | (a) `git branch` 输出含 `merge/auto-*` 形式分支（至少 1 行匹配 `^[\* ]*merge/auto-[0-9a-f]+`）；(b) fork 主分支 HEAD 未被 merge 半完成状态污染（即 `git log <fork-main-branch> -1` 输出不含本次 run 的提交）；(c) doc §10 O7 验收通过 |

#### 2.5.3 边界与失败场景小结

- default 翻转三态：默认 True（U-P4.1） / 显式 True（U-P4.2） / 显式 False（U-P4.3）
- orchestrator 接入点（U-P4.4）
- wizard 默认勾选（U-P4.5）
- 手工 E2E fresh 目录（E2E-P4.A）

---

### 2.6 现有测试改动清单（Phase 4 部分新增 v3）

| 文件 | 改动 | 类型 | 来源 |
|---|---|---|---|
| `tests/unit/test_working_branch.py:72-75` | 测试名 `test_enable_working_branch_defaults_false` → `test_enable_working_branch_defaults_true`；断言 `is False` → `is True`（实现：U-P4.1） | **改动**（重命名 + 断言迁移） | [plan #3] lock #3；plan §3.3 Q3 决策清单第 1 行；P1-1 修订 |
| `tests/unit/test_working_branch.py:77-83` `test_enable_working_branch_can_be_set` | **不改**（断言已是 True，与新 default 兼容；实现：U-P4.2） | 维持 | plan §3.3 Q3 决策清单第 2 行 |
| `tests/unit/test_working_branch.py` 新增 method | `test_enable_working_branch_can_be_disabled_with_explicit_false`（实现：U-P4.3） | **新增** | plan §3.3 Q3 决策清单第 3 行 + P1-1 修订 |
| **grep 全测试树复核**（Executor Phase 4 commit #1 前跑） | `grep -rn enable_working_branch tests/` + `grep -rn active_branch tests/`；现存 hit 全在 `tests/unit/test_working_branch.py`；新增 hit 必列入清单 | grep 复核 | plan §3.3 Q3 "未量化风险" |

> 该清单与 `.multi-agent/large-scale-perf/decisions/u7-affected-tests.md`（Executor 实施时维护）协同；Phase 4 启动时 gatekeeper-test 必须核对清单完整性。

---

## 3. GO 条件 ↔ 测试映射

### 3.1 Phase 0（v2 原文保留）

| GO 条件 | 覆盖测试编号 |
|---|---|
| G0-1（合约 + 异常类单测全绿） | U-P0.1 ~ U-P0.8 |
| G0-2（mypy 无新增） | 离线命令；Executor 责任 |
| G0-3（ruff 无新增） | 离线命令；Executor 责任 |
| G0-4（无运行时行为变化） | U-P0.1（异常仅定义） + U-P0.6（version=1 加载但不消费） |

### 3.2 Phase 1（v2 原文保留）

| GO 条件 | 覆盖测试编号 |
|---|---|
| G1-1（6 新单测 + 现有 conflict_analyst* 不破） | U-P1.1 ~ U-P1.6 + 现有 `test_conflict_analyst_round.py` regression |
| G1-2（agent_contracts 通过） | U-P1.10 + U-P1.11 |
| G1-3（mypy / ruff） | 离线命令 |
| G1-4（总覆盖率 ≥ 基线） | §6 + U-P1.12 |
| G1-5 / O1 | E2E-P1.A / B + U-P1.2 ~ U-P1.5 + U-P1.7 / U-P1.8 |

### 3.3 Phase 2（v2 + v3 lock #27 路径 A 扩展）

| GO 条件 | 覆盖测试编号 |
|---|---|
| G2-1（5 新单测 + web 测试） | U-P2.1 ~ U-P2.5 + U-W2.1；补强 U-P2.6 ~ U-P2.13 |
| G2-2（agent_contracts: BaseAgent 唯一入口） | **现有** `test_agent_contracts.py` regression（P1-1 修订） |
| G2-3（mypy 零新增） | 离线 `mypy src` |
| G2-4（手工冒烟） | E2E-P2.A + E2E-P2.B |
| G2-5（现有 G5 ceiling 不破） | U-P2.13 + 现有 `test_orchestrator_halts_when_cost_ceiling_exceeded` + U-P2.8 |
| O2（doc §10） | U-P2.2 + U-P2.5 + E2E-P2.A/B |
| **G2-6（v3 新增 / lock #27 路径 A）** | **U-P2.14 + U-P2.15 + U-P2.16** |

### 3.4 Phase 3（v3 新增）

| GO 条件 | 覆盖测试编号 |
|---|---|
| G3-1（8 个新单测全绿） | U-P3.1 ~ U-P3.8 |
| G3-2（现有 executor* / planner* / judge* / conflict_analyst* 不破） | regression net（unit 全跑） |
| G3-3（mypy / ruff 零新增） | 离线命令 |
| G3-4 / O5（故意 overlap → raise） | U-P3.2 + U-P3.8（失败子断言） |

### 3.5 Phase 4（v3 新增）

| GO 条件 | 覆盖测试编号 |
|---|---|
| G4-1（3 plan 列名 + 2 lock #3 现有改动） | U-P4.1 ~ U-P4.5 |
| G4-2（全套 unit + integration 不破） | regression net（unit 全跑 + 现有 `test_working_branch.py` 改动） |
| G4-3（mypy / ruff 零新增） | 离线命令 |
| G4-4 / O7（fresh dir → `merge/auto-*`） | E2E-P4.A + U-P4.4 |

---

## 4. 边界与失败场景清单（横切）

| 类别 | 覆盖测试 |
|---|---|
| 空输入 | U-P0.7（缺 version 字段） |
| None 输入 | U-P2.4 / U-P2.8（max_cost_usd=None） / U-W2.1 (d)（limit_usd=null） |
| 超长输入 | U-P1.7 / U-P1.8（chunked 阈值 40KB±1） / U-P1.4（9 chunks 触发 hard cap） |
| LLM 调用失败 / timeout | U-P1.9（`httpx.ReadTimeout` 走真实 retry 路径） |
| budget 6 点 | U-P2.4（None） / E2E-P2.A（0.01） / U-P2.3 第 1-3 次（limit-ε） / U-P2.9（limit 整） / U-P2.1 + U-P2.10（limit+ε） / U-P2.4（99 倍） |
| 双 transition 幂等 | U-P2.6 + U-P2.13 |
| 7 yaml 缺 version 字段（兼容） | U-P0.7 |
| AgentContract.version 默认 0 反序列化 | U-P0.2 + U-P0.3 + U-P0.7 |
| 反向 import 防御 | U-P1.8（split_by_semantic_boundary 来源锁 chunk_processor） |
| anti-pattern 守护 | **现有** `test_agent_contracts.py` regression |
| 序列化往返 | U-P1.6（is_chunked / chunk_count 双向） |
| **运行态快照独立性（v3 新增）** | U-P2.15 (c)(d)（`state.thresholds is not state.config.thresholds` + 互不影响） |
| **thresholds 真实驱动（v3 新增 / HANDOFF §4.3）** | U-P2.16 (a)(b)（`view.thresholds` 入参 `analyze_file` 而非 mock 合成） |
| **shard 故意 overlap（v3 新增）** | U-P3.2 + U-P3.8 (d) |
| **shard 名义 disjoint 但接入回归网（v3 新增）** | U-P3.3 / U-P3.5 / U-P3.6（dict.keys() / group-by-file 名义安全仍 assert） |
| **default 翻转 backward compat（v3 新增）** | U-P4.3（显式 False 不被新 default override）+ E2E-P4.A（fresh 目录默认 True） |
| **wizard default 一致性（v3 新增）** | U-P4.5（wizard checkbox default ↔ config field default 一致） |

**正常 vs 失败比例**：失败场景 14 条 / 总用例 55 = 25.5%。**满足"失败:正常 ≥ 1:3"**（要求 ~25% 以上，v3 仍达标，与 v2 的 28% 相当）。

---

## 5. Mock / Fixture 设计

### 5.1 v2 已有 fixture（保留不动）

| Fixture 名 | 用途 | 实现 |
|---|---|---|
| `large_diff_40kb_minus_one` | U-P1.7（39999） | `FileDiff(current="a"*39999, target="b"*39999)` |
| `large_diff_40kb_plus_one` | U-P1.8（40001） | 同上 +1 |
| `mock_chunk_analyses_unanimous` | U-P1.2 | 3 个 unanimous `ConflictAnalysis` |
| `mock_chunk_analyses_disagreement` | U-P1.3 | 3 个 mixed strategies |
| `mock_chunk_analyses_security` | U-P1.5 | 4 个 + 1 security |
| `mock_chunk_analyses_hard_cap` | U-P1.4 | 9 个任意合法 analysis |
| `mock_cost_tracker_sequence` | U-P2.1/3/4/9/10 | `MagicMock(spec=CostTracker)` + `PropertyMock(side_effect=[...])` |
| `tmp_run_dir` | U-P2.5 / E2E-P2.B | `tmp_path / ".merge" / "runs" / "<run_id>"` |
| `mock_dashboard_props` (web) | U-W2.1 a/b/c/d | TS 内联 4 props |

### 5.2 v3 新增 fixture

| Fixture 名 | 用途 | 实现 | 落地位置 |
|---|---|---|---|
| `mock_threshold_config_custom` | U-P2.14 / U-P2.15 / U-P2.16 | `ThresholdConfig(chunked_aggregation_min_confidence=0.72, risk_score_low=0.42, ...)`；显式注入 conftest factory 函数 | `tests/unit/conftest.py` |
| `mock_state_with_thresholds` | U-P2.14 / U-P2.16 | `MergeState(..., thresholds=mock_threshold_config_custom)` | `tests/unit/conftest.py` |
| `mock_shards_disjoint` | U-P3.1 / U-P3.3 ~ U-P3.8 正常路径 | `[["a.py", "b.py"], ["c.py"], ["d.py", "e.py"]]` | `tests/unit/conftest.py` |
| `mock_shards_overlap` | U-P3.2 / U-P3.8 (d) 失败子断言 | `[["a.py", "b.py"], ["b.py", "c.py"]]`（`b.py` 重复） | `tests/unit/conftest.py` |
| `mock_fork_repo_fresh` | U-P4.4 / E2E-P4.A | `tmp_path` 内 `git init && git commit --allow-empty`；返回 fork repo path | `tests/unit/conftest.py` |
| `wizard_defaults_table` | U-P4.5 | import `src.cli.commands.setup` 取 default 表；不跑 interactive | inline in test |

### 5.3 复用现有 conftest（v2 保留）

| 既有项 | 用途 |
|---|---|
| `patch_llm_factory` | U-P1.* / U-P2.* / U-P3.* / U-P4.4 全部需 LLM mock 用例 |
| `_make_config(tmp_path)` | U-P2.7 / U-P2.8 / U-P4.1 ~ U-P4.3 |

### 5.4 Mock 边界约束（v3 扩展）

v2 约束**全部保留**，v3 追加：

- **绝不 mock** `state.thresholds` 字段本身（U-P2.14/15/16 必须验证真实 pydantic 字段定义）
- **绝不 mock** `ConflictAnalystAgent.restricted_view`（U-P2.16 验证真实链路 view.thresholds → analyze_file）
- **绝不 mock** `assert_disjoint_file_shards` 的实现体（U-P3.3 ~ U-P3.8 用 `MagicMock(wraps=原)` 只观察 call_count，不替换逻辑）
- **绝不 mock** `MergeConfig.enable_working_branch` 字段（U-P4.1 ~ U-P4.3 必须验证真实 pydantic 字段 default）
- **U-P4.4 不真改 fs**：用 `mock` git 调用拦截（`git.Repo.create_head` 或等价），避免 unit test 写 fork repo

---

## 6. 覆盖率维持策略

### 6.1 目标（v2 保留）

CLAUDE.md / facts.md K4：总覆盖率 ≥80%（`pyproject.toml --cov-fail-under=80`）；新增模块自身覆盖率不能拉低基线。

### 6.2 基线对比方法（v2 P1-5 修订保留）

- **基线测定**：Phase 0 已锁定 commit `1a40958` 后 coverage TOTAL = **83.25%**；Phase 1 已锁定 commit `32483b1` 后 coverage TOTAL = **83.54%**（+0.29pp，[code-phase-1 #28]）。Phase 2 起点基线 = 83.54%。
- **每 Phase 验证**：每个 Phase 末 commit 前用 `pytest tests/unit/ --cov=src --cov-report=term --cov-report=json:.coverage-phase-N.json -q` 产出新 coverage.json，对比：
  - 门槛 1：`total_pct >= 80.0`（pyproject.toml 强制）
  - 门槛 2：`new_total_pct >= baseline_total_pct - 0.5`（容差 0.5pp）

### 6.3 per-Phase 局部覆盖率（v3 扩展）

- Phase 2（v2 保留）：base_agent budget 分支目标 ≥90%；orchestrator except 分支目标 100%。
- **Phase 2 lock #27 路径 A（v3 新增）**：`MergeState.thresholds` 字段定义 + orchestrator init copy 分支 + conflict_analyst view drive 路径，目标 ≥85%（U-P2.14 / U-P2.15 / U-P2.16 三用例可覆盖）。
- **Phase 3（v3 新增）**：`assert_disjoint_file_shards` + `FileShardOverlap` 异常类目标 100%（U-P3.1 + U-P3.2 全 path）；6 个接入点 invocation 行覆盖目标 100%（U-P3.3 ~ U-P3.8 各 1 行 helper call）。
- **Phase 4（v3 新增）**：`enable_working_branch` 字段 default 行为 100%（U-P4.1 ~ U-P4.3 三态）；orchestrator init phase worktree 分支 ≥90%（U-P4.4）；wizard default 表 ≥85%（U-P4.5）。

### 6.4 风险点（v3 扩展）

- v2 保留：`_aggregate_chunked_analyses` corner（"所有 chunk strategy=ESCALATE"）；Web `requestAnimationFrame` mock。
- **v3 新增风险**：
  - U-P3.7 接入参数形态（chunk_id vs file_path）取决于 Executor 实施选择。本测试方案锁断言 = helper 被调 + 不 raise，不锁 shards 内具体值。若 Executor 走"chunks 集合视为 single shard"路径（即只 1 个 shard，disjoint 平凡成立）则 U-P3.7 退化为存在性测试；走 scope.md §3.1 "细节自纠"上报，Verifier 修订时补 `test_chunked_path_single_shard_is_degenerate_pass`。
  - U-P4.4 init phase 接入点（`orchestrator.py:240-247`）实际代码已在 [code-phase-0 #21] 之前接入但未生效（plan §1.1 "**仅 default 生效，0 代码改动**"）。Executor 实施时若发现 default 翻转后 init phase 必须有额外 wiring，走"细节自纠"上报；若 wiring 已自动生效（仅 default 翻转）则 U-P4.4 直接验证既有行为。
  - U-P4.5 wizard default 取值路径（dict / Pydantic field / setup module 函数）取决于 `src/cli/commands/setup.py` 内部结构。本测试方案锁断言 = wizard 模块导入后某属性/函数返回 default=True；若 Executor 实施时改为字典 default 外置，走"细节自纠"上报，Verifier 修订时锁定具体路径。

---

## 7. 现有测试改动清单

### 7.1 Phase 0（v2 保留）

| 文件 | 改动 | 类型 |
|---|---|---|
| `tests/unit/test_agent_contracts.py` | 扩展断言：U-P0.4 / U-P0.5 / U-P0.6 / U-P0.7 | **扩展**（不删原有） |

### 7.2 Phase 1（v2 保留）

| 文件 | 改动 | 类型 |
|---|---|---|
| `tests/unit/test_conflict_analyst_round.py` 等 `test_conflict_analyst*` | **不改**——facts.md K3 严禁 regression | regression 守护 |

### 7.3 Phase 2（v2 保留 + v3 路径 A 扩展）

| 文件 | 改动 | 来源 |
|---|---|---|
| `tests/unit/test_telemetry_snapshot.py:125` | 测试名 → `test_max_cost_usd_defaults_to_five_dollars`；断言 → `== 5.0`（实现 U-P2.7） | [plan #2] / plan §3.1 Q1 P0-1 修订 |
| `tests/unit/test_telemetry_snapshot.py` 新增 method | `test_max_cost_usd_can_be_disabled_with_none`（实现 U-P2.8） | plan §3.1 Q1 P0-1 修订 |
| `tests/unit/test_telemetry_snapshot.py:140` `test_orchestrator_halts_when_cost_ceiling_exceeded` | **不改**（显式赋值 `max_cost_usd=1.0` 不依赖 default） | G2-5 regression |
| **grep 全测试树复核** | `grep -rn "max_cost_usd" tests/` 当前 4 hit 在 `test_telemetry_snapshot.py`；新增 hit 必列入清单 | plan §3.1 Q1 风险与对策 |
| **新建** `web/src/views/RunDashboard.test.tsx` | 新增（plan §2 Phase 2 GO 条件 G2-1） | facts.md J2 |
| **新建 / 扩展（v3 新增）** `tests/unit/test_thresholds_view.py` 或扩展 `tests/unit/test_merge_state.py` | 实现 U-P2.14 / U-P2.15 / U-P2.16；锁定 lock #27 路径 A | scope.md §6 路径 A + [code-phase-1 #27] |

### 7.4 Phase 4（v3 新增）

| 文件 | 改动 | 来源 |
|---|---|---|
| `tests/unit/test_working_branch.py:72-75` | 测试名 `test_enable_working_branch_defaults_false` → `_defaults_true`；断言 `is False` → `is True`（实现 U-P4.1） | [plan #3] lock #3 + plan §3.3 Q3 决策清单第 1 行 + P1-1 修订 |
| `tests/unit/test_working_branch.py:77-83` `test_enable_working_branch_can_be_set` | **不改**（实现 U-P4.2） | plan §3.3 Q3 决策清单第 2 行 |
| `tests/unit/test_working_branch.py` 新增 method | `test_enable_working_branch_can_be_disabled_with_explicit_false`（实现 U-P4.3） | plan §3.3 Q3 决策清单第 3 行 |
| **新建** `tests/unit/test_orchestrator_creates_branch_on_run.py` 或扩展现有 `test_orchestrator*.py` | 实现 U-P4.4 | plan §2 Phase 4 GO 条件 |
| **新建 / 扩展** `tests/unit/test_setup_wizard.py` | 实现 U-P4.5 | plan §2 Phase 4 cli/ 改动 |
| **grep 全测试树复核**（Executor Phase 4 commit #1 前跑） | `grep -rn enable_working_branch tests/ && grep -rn active_branch tests/`；新增 hit 列入 `decisions/u7-affected-tests.md` | plan §3.3 Q3 + P1-1 修订 |

---

## 8. 测试方案与锁清单 [plan] / [test] / [code-phase-N] 事实交叉验证

| Lock # | 锁定事实 | 本方案对应测试 |
|---|---|---|
| #1 | ConflictAnalysis 11 字段 + Phase 1 加 is_chunked/chunk_count | U-P1.6 |
| #2 | max_cost_usd default→5.0；`test_telemetry_snapshot.py:125` 须改 | U-P2.7 + U-P2.8 |
| **#3** | enable_working_branch default→True；`test_working_branch.py:72-83` 须改 | **U-P4.1 + U-P4.2 + U-P4.3**（v3 新增） |
| #4 | orchestrator G5 ceiling 现行实装 + double-transition | U-P2.6 + U-P2.13 |
| **#5** | ParallelFileRunner 6 具名接入点（lock 列名）+ Phase 1 新增第 6 处 | **U-P3.3 ~ U-P3.8**（v3 新增，6 用例一一对应 6 接入点）+ U-P3.1 / U-P3.2 helper 基础 |
| #6 | split_by_semantic_boundary 真定义 `src/tools/chunk_processor.py:50` | U-P1.8（import 来源锁） |
| #7 | AgentContract.version 字段定义 + 7 yaml 全显式 1 + 默认 0 兼容 | U-P0.2 ~ U-P0.7 |
| #8 | RunBudgetExceeded(phase=current_phase) 签名 + `_current_phase: str` | U-P0.1 + U-P2.12 |
| #9 | conflict_analyst U1.A 解耦点 | U-P1.1 |
| #10 | executor 同形态 U1.A + 文件大小约束 | 现有 `test_executor*` regression 守护 |
| **#11** | Q1-Q4 决策已锁定 | Q1 → U-P2.6/7/8/13；Q2 → U-P0.5/6；**Q3 → U-P4.1/2/3（v3 新增）**；Q4 → 范围外（Phase 7） |
| #12 | 8 Phase 顺序 + 19 commit 已锁定 | 元事实，无需测试 |
| #13 | chunked reducer hard cap 锚点（>8 chunks / 10MB / ESCALATE_HUMAN / 0.3 / "too large for safe chunked analysis"） | U-P1.4 |
| #14 | chunked reducer slow path precedence + penalty（ESCALATE>SEMANTIC>TAKE_\* / 0.8 / "disagreement"） | U-P1.3 |
| #15 | chunked reducer fast path 条件（unanimous + min(conf)≥0.85 + not security） | U-P1.2 + U-P1.5 |
| #16 | U-P1.9 spec-by-test：LLM 失败聚合 = ESCALATE_HUMAN | U-P1.9 |
| #17 | anti-pattern #2 不重复写，由现有 `test_agent_contracts.py` regression 守护 | G2-2 行 / G3-2 行（regression net） |
| #18 | `RunBudgetExceeded` 落 `src/models/state.py:38-52`；仅定义未接线 | U-P0.1（v2 保留）+ U-P2.1 / U-P2.12（Phase 2 真接线后） |
| #19 | `AgentContract.version` 落 `src/agents/contract.py:30-39` 字段顺序 + description | U-P0.2 ~ U-P0.4 + U-P0.6 |
| #20 | 7 yaml `version: 1` 均落 line 2 | U-P0.5 + U-P0.6 |
| #21 | `_schema.md` Versioning 段 `:51-77` | 元事实（验证由 Phase 5 cache key bump 触发） |
| #22 | Phase 0 unit 测试入口 `tests/unit/test_run_budget_exceeded_dataclass.py` + `test_agent_contracts.py:275-350` | 已固化（实施细节） |
| #23 | ConflictAnalyst U1.A 解耦真实落地 `conflict_analyst_agent.py:121-227` + executor:392-427 | U-P1.1 |
| #24 | `_chunked_analyze_file` 落 `:237-323`；`split_by_semantic_boundary` top-level import line 20；Phase 1 新增 chunked 路径 disjoint 接入点 line 277-280（第 6 接入点） | U-P1.8 + **U-P3.7（v3 新增）** |
| #25 | `_aggregate_chunked_analyses` 模块级私有纯函数 + 4 个 module-level constants（PENALTY_FACTOR / HARD_CAP_CHUNKS / HARD_CAP_BYTES / HARD_CAP_CONFIDENCE / _STRATEGY_PRECEDENCE） | U-P1.2 ~ U-P1.5 + U-P1.12 |
| **#26** | reducer hard cap `total_bytes` 语义偏差（P2 不阻塞 Phase 1） | **本会话不修**（scope.md §6 末段 "归 Phase 5 cache 接入时处理"）；v3 不为此设计测试 |
| **#27** | `thresholds` 入参未真接线（HANDOFF §4.3 P2 未编号）；本会话路径 A 必修 | **U-P2.14 + U-P2.15 + U-P2.16**（v3 新增） |
| #28 | Phase 1 测试入口 `test_conflict_analyst_chunked.py` 12 函数；coverage 基线 83.54% | U-P1.1 ~ U-P1.12（已实装）+ §6.2 基线 |

**未违反任何 plan / test / code 阶段 lock 事实**。v3 新增 9 用例（3 Phase 2 + 8 Phase 3 + 5 Phase 4 + 1 E2E）全部直接绑定到 lock #3 / #5 / #11 / #27 或 plan §2 Phase 3/4 列名要求。

---

## 9. 不在本测试方案范围（明确排除）

按 scope.md §6 / 任务 Prompt"你不要做"：

- Phase 5 / Phase 6 / Phase 7 任何测试用例
- doc §10 O3 / O4 / O6 / O8 验收
- forgejo 1822-file 真集成跑（doc §10 O8 → Phase 7 末）
- lock #26（reducer hard cap `total_bytes` 语义偏差）的测试 — 归 Phase 5 cache 接入时处理（scope.md §6 末段）
- 任何 Phase 5+ 涉及的合约 yaml `version` bump 行为测试
- 测试代码实现（Executor 的活）
- 对 plan §2 Phase 3 / Phase 4 锁定的接入点 / 字段 / 默认值的设计性质疑（gatekeeper-plan 已通过审查锁定）

---

## 10. 实施纪律

### 10.1 Executor 实施时（v2 保留）

- 每用例必须带断言锚点（[plan #N] / [test #N] / [code-phase-N #N] 或 facts.md 段落 / doc §x 引用）
- 用例命名与 §2 矩阵严格一致（含下划线 / 大小写）
- 新增 fixture 落 `tests/unit/conftest.py`，不污染单测 module 级 namespace
- 不在测试中触发真 LLM 调用（所有 LLM 路径走 `patch_llm_factory`）
- 测试间无共享可变状态（每用例 `tmp_path` 隔离；U-P2.3 同测试内复用 BaseAgent 实例是必要约束不算违反）

### 10.2 v3 实施补充

- **U-P2.14 / U-P2.15**：实施时若 `state.thresholds` 字段落点选 `MergeStateLive` 或更细粒度子类，走 scope.md §6 路径 A 第 1 步 "如已存在 ThresholdConfig 默认实例化路径则复用" 处理，Verifier 修订时锁定具体路径。
- **U-P2.16**：实施时若 Phase 1 残留的 `analyze_file` 可选默认值（20000 / 0.85）需要保留作为 fallback（防 view.thresholds 不存在），则 U-P2.16 (a) 改为 "**真实 view.thresholds 存在时**驱动；不存在时走 fallback" 双断言。
- **U-P3.3 ~ U-P3.8**：每个接入点测试必须独立验证 `assert_disjoint_file_shards` call 发生在对应 file:line 附近（非任意其他位置）。建议 Executor 用 `MagicMock(wraps=原)` + `call_args_list` 检查至少一个调用对应当前接入点的 shards 形态。
- **U-P4.1 / U-P4.2 / U-P4.3**：必须保留 lock #3 列出的现有 `test_working_branch.py:72-83` 行号锚点（即使重命名 + 加 method，对应行的测试主体仍可识别）。Executor 不得删除该文件的 line 72-83 区间。
- **E2E-P4.A**：手工冒烟时若 fresh 目录因没有 fork 远程仓库无法创建有意义的 fork_ref，允许走 plan §2 Phase 4 "fresh 目录" 简化形态（即 fork=origin 本身的 single-repo 路径）；E2E 主断言仍锁 `git branch` 输出 `merge/auto-*`。

### 10.3 gatekeeper-test 审查焦点（v3 扩展）

- "输入 + 期望 + 断言锚点" 三要素完整性（缺一即 P1）
- 所有锁清单 [plan] / [test] / [code-phase-N] 事实被引用（§8 表）
- 失败 : 正常 ≥ 1:3（§4 末统计 25.5%）
- 不测实现细节（如 `state.count == 5` 禁止；本方案全部用可观察行为或 reducer 返回值）
- 范围严格 = Phase 0/1/2/3/4（§9 排除清单）
- **lock #5 6 接入点一一对应**：U-P3.3 ~ U-P3.8 6 用例必须各自对应一个 plan #5 lock 行号，不得偏移
- **lock #3 三态完整**：U-P4.1（默认 True） / U-P4.2（显式 True） / U-P4.3（显式 False）三态必须全部存在
- **lock #27 路径 A 三步**：U-P2.14（字段定义） / U-P2.15（orchestrator copy） / U-P2.16（run drive）三步缺一不可

### 10.4 修订流程（v2 保留）

- P0/P1：必改回审
- P2：列入未来会话遗留，本方案不动

---

## 11. 用例汇总（**v3 唯一权威表**）

| 来源 | 编号区间 | 数量 |
|---|---|---|
| Phase 0 单元 | U-P0.1 ~ U-P0.8 | 8 |
| Phase 1 单元 | U-P1.1 ~ U-P1.12 | 12 |
| Phase 2 单元（v2 保留） | U-P2.1 ~ U-P2.13 | 13 |
| **Phase 2 单元（lock #27 路径 A 新增）** | **U-P2.14 ~ U-P2.16** | **3** |
| Phase 2 Web | U-W2.1（含 4 props 子断言） | 1 |
| Phase 1 手工 E2E | E2E-P1.A / E2E-P1.B | 2 |
| Phase 2 手工 E2E | E2E-P2.A / E2E-P2.B | 2 |
| **Phase 3 单元（v3 新增）** | **U-P3.1 ~ U-P3.8** | **8** |
| **Phase 4 单元（v3 新增）** | **U-P4.1 ~ U-P4.5** | **5** |
| **Phase 4 手工 E2E（v3 新增）** | **E2E-P4.A** | **1** |
| **合计** | | **55 测试项**（47 单元 + 1 Web + 7 手工 E2E） |

总 commit 数 ≈ 7（plan §6.2 + scope.md §6：Phase 2 3 commit + Phase 3 1 commit + Phase 4 2 commit + lock #27 路径 A 同步入 Phase 2 commit 1 不增计）；每 commit 必须挂对应测试通过证据。

---

## 12. v2 → v3 变更对照表

| 变更项 | 处理位置 | 处理方式 |
|---|---|---|
| **A**：scope.md §6 路径 A 落地（lock #27 + HANDOFF §4.3 P2） | §2.3.5 + §3.3 G2-6 + §4 横切 + §5.2 fixture + §6.3 + §7.3 + §8 lock #27 + §10.2 + §11 | 新增 U-P2.14 / U-P2.15 / U-P2.16 3 用例；新增 fixture `mock_threshold_config_custom` + `mock_state_with_thresholds`；§8 锁清单加 lock #27 行 |
| **B**：Phase 3 U5 disjoint contract 用例扩展 | §2.4（整节新增） + §3.4 + §4 横切 + §5.2 fixture + §6.3 + §8 lock #5 + §11 | 新增 U-P3.1 ~ U-P3.8 8 用例；新增 fixture `mock_shards_disjoint` + `mock_shards_overlap`；§8 lock #5 行扩展对应 6 接入点 |
| **C**：Phase 4 U7 worktree default 用例扩展 | §2.5（整节新增） + §2.6 现有改动清单 + §3.5 + §4 横切 + §5.2 fixture + §6.3 + §7.4 + §8 lock #3 + §11 | 新增 U-P4.1 ~ U-P4.5 5 单元 + E2E-P4.A；新增 fixture `mock_fork_repo_fresh` + `wizard_defaults_table`；§7.4 新增 Phase 4 改动表；§8 lock #3 行扩展 |
| **D**：Phase 0 / Phase 1 / Phase 2 §2.3.1 ~ §2.3.4 不动 | §2.1 / §2.2 / §2.3.1 ~ §2.3.4 | regression 守护，简表呈现并指向 v2 FINAL 原文 |
| **E**：§9 不在范围明确排除 Phase 5/6/7 / lock #26 | §9 | 显式声明 5 项排除（含 lock #26 归 Phase 5） |
| **F**：§11 总用例从 38 → 55 | §0 + §1 + §11 | 唯一权威表统一更新；47 单元 + 1 Web + 7 手工 E2E |
| **G**：失败 : 正常 比例从 28% → 25.5% | §4 末统计 | 仍达 ≥1:3 要求；分母从 39 升至 55，分子从 11 升至 14 |
| **H**：§10 实施纪律 v3 扩展 | §10.2 + §10.3 | 加 U-P2.14/15/16 / U-P3.* / U-P4.* / E2E-P4.A 实施补充；gatekeeper-test 审查焦点加 lock #3/#5/#27 三步检查 |
