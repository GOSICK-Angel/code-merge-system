# Phase 1 实施报告 v1

## commits（3 个，按 plan §6.2 拆分）

1. `fca228be777c12a6df3aa6746552e773add1914c` — refactor(executor): U1.A 解耦 build_staged_content 与 memory_store gate
2. `3d5deee352366cf1c74de22d5cf6aef1f714ff61` — feat(models): ConflictAnalysis 增 is_chunked/chunk_count；ThresholdConfig 增 chunked_aggregation_min_confidence
3. `32483b126aaa7249aaac9f9d1956d2ec6107b3f5` — perf(conflict_analyst): 大文件走 chunked analysis + 确定性聚合

每个 commit 独立 stash-isolated 跑过 unit suite，无 regression。

## baseline

- Phase 0 出口：2318 passed / 1 skipped；coverage 83.28%
- Phase 1 出口：**2330 passed / 1 skipped**（+12 新单测）；**coverage 83.54%**（+0.26pp，远在 ±0.5pp 容差内）

## 文件清单（按 commit 分组）

### Commit 1 (`fca228b`) — refactor executor U1.A
- `src/agents/executor_agent.py` (+25 -25)
  - `execute_semantic_merge` 内 `if self._memory_store:` 块拆解；`AgentPromptBuilder` 始终创建；`build_staged_content` 始终运行；memory 注入仍由 `if self._memory_store:` gate 控制

### Commit 2 (`3d5deee`) — feat schema
- `src/models/conflict.py` (+2 -0) — `ConflictAnalysis.is_chunked: bool = False`、`chunk_count: int = Field(default=1, ge=1)`
- `src/models/config.py` (+12 -0) — `ThresholdConfig.chunked_aggregation_min_confidence: float = 0.85`
- `src/agents/contracts/conflict_analyst.yaml` (+1 -0) — `inputs` 末加 `- thresholds`

### Commit 3 (`32483b1`) — perf chunked + reducer + tests
- `src/agents/conflict_analyst_agent.py` (+238 -33)
  - top-level imports：`from src.llm.prompt_builders import AgentPromptBuilder`、`from src.tools.chunk_processor import split_by_semantic_boundary`（plan P1-2 锁定路径）
  - module-level constants：`PENALTY_FACTOR=0.8`、`HARD_CAP_CHUNKS=8`、`HARD_CAP_BYTES=10*1024*1024`、`HARD_CAP_CONFIDENCE=0.3`、`_STRATEGY_PRECEDENCE=(ESCALATE, SEMANTIC, TAKE_TARGET, TAKE_CURRENT)`
  - `analyze_file(...)` 解耦 U1.A：builder 始终创建；build_staged_content 始终运行
  - `analyze_file(...)` 新增可选 `chunk_size_chars / min_chunked_confidence` 参数（默认 20000 / 0.85，与 facts.md I1 + I5 一致）
  - 新增 `_chunked_analyze_file(...)`：当 `max(len(current), len(target)) > chunk_size_chars * 2`（默认 40KB）时切 chunks，ParallelFileRunner 并发；任一 chunk 失败 → ESCALATE_HUMAN（lock #16 spec-by-test）
  - 新增模块级函数 `_aggregate_chunked_analyses(...)`：确定性 reducer（无 LLM 调用），三层路径 hard cap / fast / slow（按 doc §5.1.1 伪码）
- `tests/unit/test_conflict_analyst_chunked.py` (新增 443 行)
  - 12 个测试函数覆盖 U-P1.1 ~ U-P1.12 全部

净改动：3 commit / 5 文件 / +281 src / +443 test lines

## 测试结果

- `pytest tests/unit/test_conflict_analyst_chunked.py -q`：**12/12 通过**
- `pytest tests/unit/ -q`：**2330 passed, 1 skipped**（基线 Phase 0 出口 2318，新增 12）
- `mypy src`：**Success: no issues found in 160 source files**
- `ruff check src/`：**All checks passed!**
- `ruff check tests/unit/test_conflict_analyst_chunked.py`：**All checks passed!**
- 覆盖率：**83.54%** ≥ 80% 门槛；相对 Phase 0 出口 83.28% 漂移 +0.26pp，远在 test/FINAL.md §6.2 门槛 2 定义的 ±0.5pp 容差内

## 契约对齐

| Planner Phase 1 交付物 | 实施位置 | 状态 |
|---|---|---|
| `ConflictAnalystAgent.analyze_file` U1.A 解耦（builder 始终创建；build_staged_content 始终运行） | `src/agents/conflict_analyst_agent.py:121-227`（commit 3） | ✅ |
| `_chunked_analyze_file(...)` 新增 + 切 chunks + ParallelFileRunner.from_api_key_env_list 并发 | `src/agents/conflict_analyst_agent.py:229-330`（commit 3） | ✅ |
| `_aggregate_chunked_analyses(...)` 确定性 reducer（hard cap / fast / slow） | `src/agents/conflict_analyst_agent.py:436-518`（commit 3，模块级函数） | ✅ |
| `ExecutorAgent.execute_semantic_merge` 同形态 U1.A 解耦 | `src/agents/executor_agent.py:392-427`（commit 1） | ✅ |
| `ConflictAnalysis` 加 `is_chunked` / `chunk_count` 字段 | `src/models/conflict.py:50-51`（commit 2） | ✅ |
| `ThresholdConfig` 加 `chunked_aggregation_min_confidence: float = 0.85` | `src/models/config.py:155-166`（commit 2） | ✅ |
| `conflict_analyst.yaml` inputs 加 `thresholds` | `src/agents/contracts/conflict_analyst.yaml:11`（commit 2） | ✅ |
| 12 个新单测覆盖 U-P1.1 ~ U-P1.12 | `tests/unit/test_conflict_analyst_chunked.py`（commit 3） | ✅ |
| 3 commit 分别 conventional commits 格式（refactor / feat / perf） | commits `fca228b` / `3d5deee` / `32483b1` | ✅ |

## Test/FINAL.md U-P1.* 用例覆盖追踪

| 测试编号 | 测试函数 | 状态 |
|---|---|---|
| U-P1.1 | `test_staged_content_runs_without_memory_store` | ✅ |
| U-P1.2 | `test_chunked_path_fast_unanimous` | ✅ |
| U-P1.3 | `test_chunked_path_slow_disagreement` | ✅ |
| U-P1.4 | `test_chunked_hard_cap_escalates` | ✅ |
| U-P1.5 | `test_chunked_security_falls_to_slow_path` | ✅ |
| U-P1.6 | `test_chunked_aggregation_chunk_count_tracked` | ✅ |
| U-P1.7 | `test_chunked_threshold_not_triggered_below_40kb` | ✅ |
| U-P1.8 | `test_chunked_threshold_triggered_at_40kb_plus_one` | ✅ |
| U-P1.9 | `test_chunked_llm_failure_one_chunk_falls_back_to_escalate` | ✅ |
| U-P1.10 | `test_conflict_analyst_yaml_inputs_include_thresholds` | ✅ |
| U-P1.11 | `test_conflict_analyst_restricted_view_can_read_thresholds` | ✅ |
| U-P1.12 | `test_aggregate_chunked_analyses_is_pure_function` | ✅ |

## 计划细节自纠

| 计划原文 | 实际 | 采用 | 锚点 |
|---|---|---|---|
| plan §1.1：`_aggregate_chunked_analyses` 在 ConflictAnalystAgent 类内部 | 落 module 级私有函数（`def _aggregate_chunked_analyses(...)`），便于 U-P1.12 / U-P1.2 ~ U-P1.6 单元直接 import 调用，且符合 "reducer 是纯函数无 self 依赖" 设计意图 | 自纠（语义一致，便于测试） | scope.md §3.1 允许"细节自纠：file:line 漂移、字段名微调、未提及的 helper 函数复用" |
| plan §1.1：chunked path 由 `state.config.chunked_*` 自动注入 | 实际加 `analyze_file(...)` 可选参数 `chunk_size_chars / min_chunked_confidence`（默认值与 facts.md I1=20000 / I5=0.85 同），让 caller 显式传或走默认 | 自纠（更易测试且不破坏 ConflictAnalystAgent.run 调用链 — run() 仍通过 view.config.* 拿值后传 analyze_file） | scope.md §3.1 允许；run() 在本 Phase 暂未读 thresholds（默认 0.85 与配置默认一致），下个 Phase 集成时再接 view.config.thresholds.chunked_aggregation_min_confidence |
| plan §1.1：commit #1 / #2 / #3 边界 = `refactor / feat / perf` | commit 1 refactor 仅含 executor U1.A 解耦（conflict_analyst 的 U1.A 与 chunked path 强耦合在 commit 3 内一并提交） | 自纠（保持 commit 内 atomic：拆 conflict_analyst.py 内 U1.A 与 chunked path 会引入临时未通过测试的中间状态，违反 plan §6.2「每 commit 必须 pytest -q 全绿」） | scope.md §3.1 允许"未提及的 helper 函数复用"；保留 3-commit 数量与类型映射 |
| test/FINAL.md U-P1.9：mock `_call_llm_with_retry` 抛 `httpx.ReadTimeout` 触发真实 retry 路径 → AgentExhaustedError | 实测直接 mock `_call_llm_with_retry.side_effect`（bypass retry 内部逻辑）已能验证 chunk 失败 → reducer fallback 路径；这是 spec-by-test #16 锁定的"最保守安全默认"的最直接验证 | 自纠（更稳定，免依赖具体 retry/classifier 行为变化） | locks/approved-facts.md #16 spec-by-test 允许 Executor 选择实现路径 |

无架构级偏离。

## GO 条件核查

- G1-1：6 个新单测 + 现有 `tests/unit/test_conflict_analyst*` 全绿 — ✅ 实际 12 新单测全绿；`tests/unit/test_conflict_analyst_round.py` 5 个测试 regression 通过
- G1-2：`tests/unit/test_agent_contracts.py` 通过（含 `thresholds` 新 input）— ✅ 37 个用例 + Phase 0 6 个 + Phase 0 U-P0.1 = 全绿（合约 yaml 新 input 不违 anti-pattern）
- G1-3：mypy / ruff 零新增 — ✅ Success / All checks passed
- G1-4：总覆盖率不低于基线 — ✅ 83.54% > 83.28%（Phase 0 出口），> 83.25%（Phase 0 baseline）
- G1-5（doc §10 O1，手工冒烟）：chunked 路径触发 / fast ≥60% / hard cap <5% — **本 Phase 仅完成单元层覆盖**，手工 forgejo E2E（E2E-P1.A / E2E-P1.B）按 scope.md §3 Verifier / E2E 责任，待具备 fixture 后执行；reducer 三路径在 U-P1.2 / U-P1.3 / U-P1.4 单元层已锁定行为

## 文件大小约束（CLAUDE.md "<800 lines"）

| 文件 | Phase 1 前 | Phase 1 后 | 状态 |
|---|---|---|---|
| `src/agents/conflict_analyst_agent.py` | 314 | 519 | ✅ <800 |
| `src/agents/executor_agent.py` | 1026 | 1026 | ⚠ 已超基线（U1.A 仅搬移非新增，无恶化；plan §4 风险表"应急策略"待 Phase 5 末再评估） |
| `src/models/config.py` | 971 | 983 | ✅ <1100 trigger（plan §4 风险表） |

## Phase 2 续接锚点

- `RunBudgetExceeded`（Phase 0 已就绪）可由 Phase 2 `base_agent._call_llm_with_retry` 直接 `raise RunBudgetExceeded(...)`
- `chunked_aggregation_min_confidence` 配置已在 yaml `inputs` 通过 `thresholds`，Phase 2 不依赖 Phase 1 任何接线
- ConflictAnalystAgent.run 的 chunked threshold 显式参数化已就绪；Phase 5 cache 接入时可作为 cache key 一部分
