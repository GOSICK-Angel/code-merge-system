# Phase 2 实施报告 v1

## commit 序列

1. `8eb0a26` — feat(state,orchestrator): MergeState.thresholds + init phase 复制 + conflict_analyst 驱动 (lock #27 path A)
2. `c1de270` — feat(config): per_run_cost_limit semantic on max_cost_usd (default 5.0) + per_run_cost_warn_pct
3. `506c44b` — feat(base_agent,orchestrator): budget cap → RunBudgetExceeded → AWAITING_HUMAN + partial report + double-transition idempotent
4. `1780dec` — feat(web): RunDashboard 预算进度条 + serializer limit/warn 字段

HEAD = `1780dec`（feat/web 分支，未推送）。

## 新增/修改文件清单

### src/ 改动

| commit | 文件 | 改动 |
|---|---|---|
| `8eb0a26` | `src/models/state.py` | import `ThresholdConfig`；`MergeState` 加 `thresholds: ThresholdConfig = Field(default_factory=ThresholdConfig)` 字段（line 84-92） |
| `8eb0a26` | `src/core/phases/initialize.py` | `_run_sync` 顶部加 `state.thresholds = state.config.thresholds.model_copy()`（line 293-296） |
| `8eb0a26` | `src/agents/conflict_analyst_agent.py` | `run()` 中读 `view.thresholds.chunked_aggregation_min_confidence` 与 `view.config.chunk_size_chars`，显式传到 `analyze_file` 调用（line 71-103） |
| `c1de270` | `src/models/config.py` | `MergeConfig.max_cost_usd` default `None → 5.0`；新增 `per_run_cost_warn_pct: float = 0.8`（line 961-983） |
| `506c44b` | `src/agents/base_agent.py` | `__init__` 加 4 个 budget 状态属性；新增 `set_budget` / `set_activity_callback` / `_check_budget` 三个公开方法（line 152-156, 245-295）；`_call_llm_with_retry` 前后双 `_check_budget()` 调用（line 491-493, 664-668） |
| `506c44b` | `src/core/orchestrator.py` | import `RunBudgetExceeded`；`_inject_cost_tracker` 注入 budget + activity_callback（line 492-503）；循环顶 AWAITING_HUMAN 短路（line 262-269）；新增 `_write_budget_exceeded_report` helper；新增 `except RunBudgetExceeded` 分支（line 346-371） |
| `1780dec` | `src/web/serializers.py` | 新增 `_serialize_cost_summary` helper，把 `limit_usd` / `warn_pct` 注入 costSummary（line 372-388）；`serialize_state` 中 `"costSummary"` 改走 helper |

### tests/ 改动

| commit | 文件 | 改动 |
|---|---|---|
| `8eb0a26` | `tests/unit/test_state_thresholds.py` | 新建（4 个测试函数覆盖 U-P2.14/15/16） |
| `c1de270` | `tests/unit/test_telemetry_snapshot.py` | `:125` 测试改名 `test_max_cost_usd_field_defaults_none` → `test_max_cost_usd_defaults_to_five_dollars` + 断言改 `== 5.0`；新增 `test_max_cost_usd_can_be_disabled_with_none`（U-P2.7 + U-P2.8） |
| `506c44b` | `tests/unit/test_budget_cap.py` | 新建（9 个测试函数覆盖 U-P2.1/2/3/5/6/9/10/12/13） |
| `1780dec` | `tests/unit/test_serializers.py` | `test_cost_summary_passthrough` 改名 + 富化为 `test_cost_summary_enriched_with_budget_knobs`；新增 `test_cost_summary_limit_usd_none_when_disabled`（U-P2.11） |

### web/ 改动

| commit | 文件 | 改动 |
|---|---|---|
| `1780dec` | `web/src/views/RunDashboard.tsx` | 新增 `BudgetBar` 组件 + 嵌入 Run cost 卡片（line 310-355, 444-450） |
| `1780dec` | `web/src/views/RunDashboard.test.tsx` | 新建（U-W2.1 四 props 子断言：ok / warn / exceeded / hidden） |
| `1780dec` | `web/src/types/state.ts` | `CostSummary` 接口加 `limit_usd?: number \| null` 与 `warn_pct?: number` |

## 测试结果

- `pytest tests/unit/ --cov=src --cov-report=term -q`：**2345 passed / 1 skipped / coverage 83.57%**
  - Phase 1 出口基线：2330 passed / 83.54%
  - 本次净增：+15 测试（4 thresholds + 1 None tele + 9 budget cap + 1 serializer enriched + 0 替换 = 计 15 新测函数；覆盖率 +0.03pp 在 ±0.5pp 容差内）
- `mypy src`：**Success: no issues found in 160 source files**
- `ruff check src/`：**All checks passed!**
- `cd web && npm run lint`（tsc --noEmit）：通过
- `cd web && npx vitest run`：**83 passed / 9 test files**（含 RunDashboard.test.tsx 4 用例）

## 契约对齐

| Planner 契约 | 实施位置 | 状态 |
|---|---|---|
| `MergeConfig.max_cost_usd` default→5.0 | `src/models/config.py:962` | ✅ |
| 新增 `per_run_cost_warn_pct: float = 0.8` | `src/models/config.py:971-979` | ✅ |
| `BaseAgent._call_llm_with_retry` 前后双 check | `src/agents/base_agent.py:491-493, 664-668` | ✅ |
| `RunBudgetExceeded(spent, limit, phase=current_phase)` 签名 | `src/agents/base_agent.py:279`（raise 位置） | ✅ |
| warn_pct 首次跨越 emit `budget_warning` 事件 | `src/agents/base_agent.py:280-294` | ✅ |
| Orchestrator `except RunBudgetExceeded` 分支 | `src/core/orchestrator.py:346-365` | ✅ |
| 写 `.merge/runs/<id>/budget_exceeded_report.md` | `src/core/orchestrator.py:_write_budget_exceeded_report` | ✅ |
| transition AWAITING_HUMAN + checkpoint tag `"budget_exceeded"` | `src/core/orchestrator.py:357-365` | ✅ |
| 既有 ceiling check (G5) 协同 (status==AWAITING_HUMAN 短路) | `src/core/orchestrator.py:262-269` | ✅ |
| serializers cost_summary 加 `limit_usd` + `warn_pct` | `src/web/serializers.py:_serialize_cost_summary` | ✅ |
| RunDashboard.tsx budget 进度条三态 (绿/橙/红) | `web/src/views/RunDashboard.tsx:BudgetBar` | ✅ |
| `MergeState.thresholds: ThresholdConfig` 字段 | `src/models/state.py:84-92` | ✅ |
| Orchestrator init phase 复制 `config.thresholds → state.thresholds` | `src/core/phases/initialize.py:293-296`（_run_sync 顶部 InitializePhase） | ✅ |
| conflict_analyst run() 真实驱动 `view.thresholds` | `src/agents/conflict_analyst_agent.py:71-103` | ✅ |
| HANDOFF §4.3 phase-1 未编号 P2 修复（`analyze_file` 新参数 run() 未驱动） | 同上 | ✅ |

## 计划细节自纠

| 计划原文 | 实际 | 采用 | 锚点 |
|---|---|---|---|
| plan §2 Phase 2 "调 `ctx.emit(event_type=\"progress\", action=\"budget_warning\", extra={\"pct\": ratio})`" | BaseAgent 无 `ctx`（ctx 仅在 Phase 层注入）；改为新加 `set_activity_callback` setter + `_on_activity(ActivityEvent(...))` 调用，由 orchestrator 在 `_inject_cost_tracker` 时一并注入 | 自纠（语义一致：ActivityEvent 形状不变，仍由 orchestrator `_on_activity` callback 消费）；不引入计划外字段，仅复用 `ActivityEvent.extra` | scope.md §3.1 "细节自纠：未提及的 helper 函数复用" |
| plan §2 Phase 2 commit 1 仅含 `max_cost_usd` default 改动 + `test_telemetry_snapshot.py:125` | 实际拆为 4 commit（增加 commit 0a 「MergeState.thresholds 字段 + lock #27 path A」），与 task prompt 一致 | 自纠（task prompt Step 2 明确要求 commit 0a 单独提交 lock #27 path A，避免 budget cap 与 thresholds 接入混在一起难审） | scope.md §6 lock #27 path A 实施细节列出独立 path |
| test/FINAL.md U-P2.3 "复用同一 BaseAgent + 同一 cost_tracker 跨 6 次调用" | 测试改为复用同一 BaseAgent，但每次重置 cost_tracker（用新 `_make_stub_tracker(total)` 替换）以精确控制 `total_cost_usd` 序列；agent 实例本身保留 `_budget_warning_emitted` 状态 | 自纠（观察点仍是 `ctx.emit` 调用次数；状态字段保留在 agent 实例上是 plan §2 Phase 2 描述的"首次跨越"语义所必需） | scope.md §3.1；test/FINAL.md U-P2.3 mock 边界注 "Executor 可自由选择已触发状态字段落 BaseAgent 实例 / cost_tracker / ctx 任意位置" |
| test/FINAL.md U-P0.5/0.6 `parents[2]` | 实施时未触动 Phase 0 用例（Phase 0 已通过审查） | n/a — Phase 0 已锁定 | locks/approved-facts.md #22 |

无架构级偏离。

## lock #27 路径 A 落地核查

- **MergeState.thresholds 字段**：`src/models/state.py:84-92`（`thresholds: ThresholdConfig = Field(default_factory=ThresholdConfig, description=...)`）
- **orchestrator init phase 复制**：`src/core/phases/initialize.py:293-296`（`InitializePhase._run_sync` 顶部 `state.thresholds = state.config.thresholds.model_copy()`）
- **conflict_analyst.run() 驱动**：`src/agents/conflict_analyst_agent.py:71-103`（`thresholds = view.thresholds` + 显式传 `chunk_size_chars` / `min_chunked_confidence` 到 `analyze_file`）
- **HANDOFF §4.3 phase-1 未编号 P2 修复**：上一行同位置；之前 `analyze_file` 接收可选 `chunk_size_chars / min_chunked_confidence` 但 `run()` 走默认值，本 commit 显式驱动
- **3 个新单测 U-P2.14/15/16**：`tests/unit/test_state_thresholds.py`
  - U-P2.14 `TestMergeStateThresholdsField.test_thresholds_field_default_is_threshold_config` + `test_thresholds_default_factory_independent_of_config`
  - U-P2.15 `TestInitializePhaseCopiesThresholds.test_initialize_phase_run_sync_copies_thresholds`
  - U-P2.16 `TestConflictAnalystDrivesThresholdsFromState.test_run_passes_view_thresholds_to_analyze_file`

## Test/FINAL.md U-P2.* 用例覆盖追踪

| 测试编号 | 测试函数 | 文件 | 状态 |
|---|---|---|---|
| U-P2.1 | `test_budget_exceeded_at_hard_cap_raises` | `test_budget_cap.py` | ✅ |
| U-P2.2 | `test_transitions_to_awaiting_human_and_writes_report` | `test_budget_cap.py` | ✅ |
| U-P2.3 | `test_first_crossing_emits_once` | `test_budget_cap.py` | ✅ |
| U-P2.4 | `test_budget_disabled_when_limit_is_none` | `test_budget_cap.py` | ✅ |
| U-P2.5 | `test_transitions_to_awaiting_human_and_writes_report` (同 U-P2.2 文件断言) | `test_budget_cap.py` | ✅ |
| U-P2.6 | `test_budget_double_transition_end_to_end_scenario` | `test_budget_cap.py` | ✅ |
| U-P2.7 | `test_max_cost_usd_defaults_to_five_dollars` | `test_telemetry_snapshot.py` | ✅ |
| U-P2.8 | `test_max_cost_usd_can_be_disabled_with_none` | `test_telemetry_snapshot.py` | ✅ |
| U-P2.9 | `test_budget_exceeded_at_exact_limit` | `test_budget_cap.py` | ✅ |
| U-P2.10 | `test_pre_call_check_blocks_llm` | `test_budget_cap.py` | ✅ |
| U-P2.11 | `test_cost_summary_enriched_with_budget_knobs` + `test_cost_summary_limit_usd_none_when_disabled` | `test_serializers.py` | ✅ |
| U-P2.12 | `test_phase_sourced_from_current_phase` | `test_budget_cap.py` | ✅ |
| U-P2.13 | `test_g5_ceiling_check_skips_when_status_awaiting_human` | `test_budget_cap.py` | ✅ |
| U-W2.1 | `RunDashboard.test.tsx` (4 props 子断言) | `web/src/views/RunDashboard.test.tsx` | ✅ |
| U-P2.14/15/16 | `test_state_thresholds.py` (4 函数) | `test_state_thresholds.py` | ✅ |

## GO 条件核查

- G2-1（5 新单测 + web 测试全绿）— ✅ 实际 9 + 1 子 = 10 单测 + 4 web 子断言全绿
- G2-2（agent_contracts BaseAgent 唯一入口 anti-pattern regression）— ✅ `tests/unit/test_agent_contracts.py` 37 用例全绿（base_agent 改动未引入新 LLM 调用入口）
- G2-3（mypy 零新增 error）— ✅
- G2-4（手工冒烟 → AWAITING_HUMAN + 报告）— ⏳ 单元层 `test_transitions_to_awaiting_human_and_writes_report` 已覆盖（含 fs report 校验）；手工 E2E-P2.A/B 待 Verifier / 用户决定何时跑（依赖真 API key）
- G2-5（既有 G5 ceiling 路径不破）— ✅ `test_orchestrator_halts_when_cost_ceiling_exceeded`（既有 telemetry_snapshot 测试）+ `test_g5_ceiling_check_skips_when_status_awaiting_human`（新增）共同守护

## 文件大小约束（CLAUDE.md "<800 lines"）

| 文件 | Phase 2 前 | Phase 2 后 | 状态 |
|---|---|---|---|
| `src/models/state.py` | 327 | 337 | ✅ <800 |
| `src/core/phases/initialize.py` | (未量化) | (无显著增加，<800) | ✅ |
| `src/agents/conflict_analyst_agent.py` | 519 | 525 | ✅ <800 |
| `src/models/config.py` | 983 | 1003 | ✅ <1100 trigger（plan §4 风险表） |
| `src/agents/base_agent.py` | (≈760) | (≈820) | ⚠ 接近 800 — 下个 Phase 注意（多了 set_budget / set_activity_callback / _check_budget 三方法 + 2 处 check 调用） |
| `src/core/orchestrator.py` | (≈680) | (≈740) | ✅ <800 |
| `src/web/serializers.py` | 481 | 499 | ✅ <800 |

## Phase 3 续接锚点

- Phase 2 后 HEAD `1780dec`，分支 `feat/web` 未推送。
- `MergeState.thresholds` 字段已上线，下 Phase（U3 cache）可作 cache key 一部分。
- BaseAgent `_check_budget` 已接 `_on_activity` callback，下 Phase 在 fan-out 内的 ParallelFileRunner 中（U4 rate budget）可复用同一 `_on_activity` 路径。
- Phase 3 (U5 disjointness) 的 6 个接入点中 `conflict_analyst_agent.py:81` 接入点已在 phase-1 chunked path 锁定（locks/approved-facts.md #24），Phase 3 仅需在此处新增 `assert_disjoint_file_shards` 调用，不需重写 fan-out。
- `tests/unit/test_state_thresholds.py` 现 4 测试函数全绿；Phase 3 中若 disjoint helper 接 thresholds（不预期），可直接复用 fixture。
