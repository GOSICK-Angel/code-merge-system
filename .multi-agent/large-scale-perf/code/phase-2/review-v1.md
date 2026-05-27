# gatekeeper-code 审查报告（Phase 2 v1）

源：`code/phase-2/v1.md` / commits `8eb0a26` → `c1de270` → `506c44b` → `1780dec`
HEAD = `1780dec`（feat/web 分支，未推送）；基线 HEAD = `32483b1`（Phase 1 出口）

## 结论
**通过**（无 P0/P1；2 项 P2 残留留待后续 Phase 处理）

## 契约核查表

| Planner / Test FINAL 契约 | 状态 | 锚点 |
|---|---|---|
| plan §2 P2 commit 1：`max_cost_usd` default → 5.0 + 新增 `per_run_cost_warn_pct=0.8` | ✅ | `src/models/config.py:961-980` |
| plan §2 P2：`tests/unit/test_telemetry_snapshot.py:125` 测试改名 + 断言 == 5.0 + 新增 None 兼容用例 | ✅ | `tests/unit/test_telemetry_snapshot.py:125-142` |
| plan §2 P2 base_agent 第 1 项：调 LLM 前查 `cost_tracker.total_cost_usd >= max_cost_usd` → raise `RunBudgetExceeded(spent, limit, phase=current_phase)` | ✅ | `src/agents/base_agent.py:263-279, :493` |
| plan §2 P2 base_agent 第 2 项：调用后再查一次（post-call gate） | ✅ | `src/agents/base_agent.py:661-665` |
| plan §2 P2 base_agent 第 3 项：首次跨越 warn_pct emit `ActivityEvent(action="budget_warning", event_type="progress", extra={"pct": ratio})` | ✅ | `src/agents/base_agent.py:280-295` |
| plan §2 P2 base_agent 第 4 项：保持 retry / circuit-breaker / `_call_llm_with_retry` 唯一入口 | ✅ | 新逻辑包裹最外层（line 491-493）；anti-pattern #2 由 `test_agent_contracts.py` regression 守护，本次 `test_agent_contracts.py` 37 用例全绿 |
| plan §2 P2 orchestrator 第 1 项：`except RunBudgetExceeded` 分支在 `except Exception` 之上 | ✅ | `src/core/orchestrator.py:355-374` |
| plan §2 P2 orchestrator 第 2 项：写 `.merge/runs/<id>/budget_exceeded_report.md` | ✅ | `src/core/orchestrator.py:_write_budget_exceeded_report:535-560` |
| plan §2 P2 orchestrator 第 3 项：transition AWAITING_HUMAN | ✅ | `src/core/orchestrator.py:365-370`（含 ValueError 兜底） |
| plan §2 P2 orchestrator 第 4 项：checkpoint tag `"budget_exceeded"` | ✅ | `src/core/orchestrator.py:375` |
| plan §4 风险表 row 5 "双 transition 互锁"：BaseAgent raise → orchestrator transition → ceiling check 短路 | ✅ | `src/core/orchestrator.py:262-269` AWAITING_HUMAN 短路；`except ValueError` 兜底 |
| plan §2 P2 serializers：cost_summary 输出加 `limit_usd` + `warn_pct` | ✅ | `src/web/serializers.py:372-388, :495` |
| plan §2 P2 web：RunDashboard budget 进度条三态（绿/橙/红） | ✅ | `web/src/views/RunDashboard.tsx:316-356, :502-506` |
| **lock #27 路径 A 第 1 步**：`MergeState.thresholds: ThresholdConfig` 字段（运行态快照） | ✅ | `src/models/state.py:86-94` |
| **lock #27 路径 A 第 2 步**：InitializePhase `_run_sync` 顶部 `state.thresholds = state.config.thresholds.model_copy()`（值快照，非引用） | ✅ | `src/core/phases/initialize.py:293-297` |
| **lock #27 路径 A 第 3-4 步**：ConflictAnalyst.run() 真实驱动 `view.thresholds`（**HANDOFF §4.3 phase-1 未编号 P2 修复**） | ✅ | `src/agents/conflict_analyst_agent.py:72-77, :100-101` |
| Test FINAL §2.3.5 U-P2.14/15/16 三用例 | ✅ | `tests/unit/test_state_thresholds.py:50-177` |

## 测试结果

- `pytest tests/unit/ --cov=src --cov-report=term -q`：**2345 passed / 1 skipped / coverage 83.60%**
  - 基线 Phase 1 出口：2330 / 83.54% → 净 +15 测试 / 覆盖率 +0.06pp（在 ±0.5pp 容差内）
  - v1 自报 83.57%，实测 83.60%（浮点差异不重要）
- `mypy src`：**Success: no issues found in 160 source files**
- `ruff check src/`：**All checks passed!**
- `cd web && npx vitest run`：**83 passed / 9 test files**（含 RunDashboard.test.tsx 4 用例）

## 已通过事实（详见 locks/approved-facts.md；本轮新增 6 条，已存档 28 条不重列）

本轮新增（待 SendMessage 通过后追加）：
- **[code-phase-2 #29]** RunBudgetExceeded 真实接线：`base_agent._check_budget()` 在 `_call_llm_with_retry:493` (pre) + `:665` (post) 双调；签名沿用 phase-0 锁定的 `(spent, limit, phase)`。`spent >= limit`（含 ==，U-P2.9 守护）即 raise。
- **[code-phase-2 #30]** Budget warning 一次性：`_budget_warning_emitted: bool` 状态字段在 `BaseAgent.__init__:153` 初始化；`set_budget()` 重置；首次 `spent >= limit*warn_pct` 时 emit `ActivityEvent(event_type="progress", action="budget_warning", extra={"pct": ratio})` 一次后落 True。
- **[code-phase-2 #31]** Orchestrator 双 transition 互锁：循环顶 `if state.status == SystemStatus.AWAITING_HUMAN: return state`（`orchestrator.py:267-269`） + `except RunBudgetExceeded` 内 `try/except ValueError` 兜底（`:371-374`）。`test_budget_double_transition_end_to_end_scenario` + `test_g5_ceiling_check_skips_when_status_awaiting_human` 共同守护。
- **[code-phase-2 #32]** Partial report 路径：`<repo>/.merge/runs/<run_id>/budget_exceeded_report.md`，由 `_write_budget_exceeded_report` 写出；write 失败仅 logger.debug，AWAITING_HUMAN transition 必走。Markdown 含 `run_id / phase / spent / limit` 四字段 + 人类可读 prompt。
- **[code-phase-2 #33]** `MergeConfig.max_cost_usd` default 锁定 5.0（`gt=0`），`per_run_cost_warn_pct` default 0.8（`ge=0.0, le=1.0`）。`max_cost_usd=None` 仍合法且禁用 cap（U-P2.4 + U-P2.8 共同守护）。
- **[code-phase-2 #34]** lock #27 路径 A 落地：`MergeState.thresholds: ThresholdConfig`（`src/models/state.py:86-94`）；`InitializePhase._run_sync` 顶部 `state.thresholds = state.config.thresholds.model_copy()`（`src/core/phases/initialize.py:293-297`）；ConflictAnalyst.run() 读 `view.thresholds.chunked_aggregation_min_confidence` + `view.config.chunk_size_chars`，显式驱动 `analyze_file` 入参（`src/agents/conflict_analyst_agent.py:72-101`）。HANDOFF §4.3 phase-1 未编号 P2 修复完成。

> 验证基线刷新（Phase 2 出口）：commit `1780dec` 后 `pytest tests/unit/` = **2345 passed, 1 skipped**（基线 +15）；`mypy src` = 0 error；`ruff check src/` = 0 error；coverage TOTAL = **83.60%**（+0.06pp）；web `vitest run` = **83/83 passed**。后续 Phase 不得 regression 此基线。

## P0 / P1 / P2 分级问题

无 P0 / P1。

### P2-1（不阻塞 GO；Phase 3 / 4 注意）— `base_agent.py` 文件大小 830 行

- CLAUDE.md soft 约束 "<800 lines"；Phase 1 出口约 766 行，Phase 2 加入 `set_budget` / `set_activity_callback` / `_check_budget` 三方法 + 2 处 pre/post check 调用 + 4 个 budget 状态属性 + import `RunBudgetExceeded`，扩到 830 行（v1 自报 "≈820"，实测 830）。
- v1.md "文件大小约束" 表 P2 已自标 "⚠ 接近 800 — 下个 Phase 注意"，但实际已小幅越线。
- **建议处理时机**：Phase 5 cache 接入时（plan §2 P5 要新增 `_cached_call` wrapper）一并把 budget gate + cache wrapper 抽到 `src/agents/base_agent_helpers.py`（或独立 mixin）；Phase 3 / 4 不强制处理（避免 lock #5 6 处接入点变成边修边重构）。
- 不视作 regression 阻断（参考 `executor_agent.py:1026` 在 Phase 1 通过审查时同样已超阈）。

### P2-2（不阻塞 GO；后续完善测试）— U-P2.16 (b) 子断言形态弱化

- Test FINAL §2.3.5 U-P2.16 (b) 原文要求 "mock 截获的 `ThresholdConfig` 实例 `id()` 与 `state.thresholds` 相同（链路 view.thresholds → analyze_file 入参），**非** `view.config.thresholds`"。
- 实施选择是**传递 float 字段**（`chunked_aggregation_min_confidence` + `chunk_size_chars`）而非整 `ThresholdConfig` 对象到 `analyze_file`（保留 Phase 1 锁定的 `analyze_file(chunk_size_chars: int | None, min_chunked_confidence: float | None)` 签名）。这是合理的细节自纠（v1.md "计划细节自纠"未列此项，但 phase-1 #27 锁定的 `analyze_file` 签名不变是事实）。
- 后果：`test_state_thresholds.py:175-176` 只断言 `min_chunked_confidence == 0.91`，未能区分该值是从 `view.thresholds` 还是 `view.config.thresholds` 取得（两者值相同）。
- **建议处理时机**：Phase 3 v1（U5 disjoint 接入 conflict_analyst:104 接入点）顺手扩 U-P2.16 (b)：在 `_make_config` 之外再造一个 `state.config.thresholds.chunked_aggregation_min_confidence = 0.99`，确认 `analyze_file` 仍收到 `0.91`（state 快照值），即可证伪两者同源。Phase 2 不强制修。

## 残留风险（放行说明）

1. **Phase 1 P2-1（reducer total_bytes 语义偏差）**：`_aggregate_chunked_analyses:450` 仍按 rationale 字节累加而非源 chunk 字节。lock #26 归 Phase 5 处理，本会话不审。
2. **base_agent.py 830 行**：见 P2-1，建议 Phase 5 拆分。
3. **U-P2.16 (b)**：见 P2-2，建议 Phase 3 v1 扩。

## 副作用检查（git diff `32483b1..1780dec`）

```
 src/agents/base_agent.py              |  66 ++++++++
 src/agents/conflict_analyst_agent.py  |   8 +
 src/core/orchestrator.py              |  68 ++++++++-
 src/core/phases/initialize.py         |   5 +
 src/models/config.py                  |  21 ++-
 src/models/state.py                   |  12 +-
 src/web/serializers.py                |  20 ++-
 tests/unit/test_budget_cap.py         | 275 ++++++++++++++++++++++++++++++++++
 tests/unit/test_serializers.py        |  28 +++-
 tests/unit/test_state_thresholds.py   | 176 ++++++++++++++++++++++
 tests/unit/test_telemetry_snapshot.py |  17 ++-
 web/src/types/state.ts                |   4 +
 web/src/views/RunDashboard.test.tsx   | 133 ++++++++++++++++
 web/src/views/RunDashboard.tsx        |  57 +++++++
```

14 个文件全部命中 v1.md 清单 + plan §2 Phase 2 范围。**无 Phase 2 外文件改动**；未引入计划外依赖；未触动 `executor_agent.py` / Phase 1 锁定的 reducer / Phase 0 锁定的 `RunBudgetExceeded` dataclass / 7 contract yaml。

## Step 3 / 4 — 代码质量 / 安全

- 命名：`_check_budget` / `_budget_warning_emitted` / `_on_activity` 与 BaseAgent 现有 `_cost_tracker` / `_current_phase` 风格一致；`_serialize_cost_summary` / `_write_budget_exceeded_report` 与同文件 helper 风格一致。
- 错误处理：`_write_budget_exceeded_report` 内 `except Exception: logger.debug(..., exc_info=True)` 是合理的"best-effort"（plan §2 P2 显式要求 transition 必走）；`state_machine.transition` 走 `except ValueError` 兜底覆盖 G5 ceiling 已先 transition 的并发场景。
- 资源释放：`_check_budget` 不持有锁 / 文件句柄 / 网络；retry 循环内的 `model_override.__exit__` 流程不变。
- 注释：3 处新增 docstring + 行内注释（base_agent / orchestrator / serializer）均说明 *why*（U2 语义 + 与 G5 协同）而非重述 *what*；符合 CLAUDE.md "Comments only when intent is non-obvious"。
- mypy strict：`Any | None` 用于 `_on_activity`（避免循环 import `ActivityEvent`）— 与 BaseAgent 既有 `_contract: Any | None` / `_memory_config: object | None` 风格一致，不引入新 anti-pattern。
- 安全：`_write_budget_exceeded_report` 路径走 `get_run_dir(self.config.repo_path, state.run_id)`，未拼接 user-controlled 字符串；spent / limit 仅做 `f"{x:.4f}"` 格式化（无 shell escape 风险）；run_id 来自 uuid4 / state.run_id（既有路径，不引入新 TOCTOU 表面）。
- TOCTOU：post-call check 在 `_consecutive_failures = 0` 与 `_trace_logger.record` 之后、`return` 之前；race 窗口最小化，符合 plan §2 "调用后再查一次（防 race）"。

## 二审及之后

本 Phase 第一次送审，无上轮反馈核查项。
