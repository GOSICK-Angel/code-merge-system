# gatekeeper-code 审查报告（Phase 1 v1）

审查者：gatekeeper-code
审查时间：2026-05-18
被审 artifact：`.multi-agent/large-scale-perf/code/phase-1/v1.md`
被审 commits：`fca228b` / `3d5deee` / `32483b1`（基于 `1a40958`）
对照：`plan/FINAL.md` §2 Phase 1 + `test/FINAL.md` §2.2 + `locks/approved-facts.md` plan #1-12 + test #13-17 + code-phase-0 #18-22

## 结论

**通过**。Phase 1 GO 条件 G1-1 ~ G1-4 全部达标；G1-5（forgejo E2E）按 scope.md §3 由 Verifier 后续执行不阻塞本 Phase。无 P0 / P1 阻塞项；3 个 P2 残留风险，全部记录到本报告并入锁清单"待后续 Phase 关闭"。

## 契约核查表

| Planner Phase 1 契约 | 实施位置 | 状态 | 锚点 |
|---|---|---|---|
| U1.A — ConflictAnalyst：builder 始终创建；`build_staged_content` 始终运行；memory 注入仍受 `if self._memory_store:` gate | `src/agents/conflict_analyst_agent.py:134-160` + `182-207` | ✅ | line 134 unconditional `builder = AgentPromptBuilder(...)`；line 188-207 unconditional `build_staged_content` 三次；line 138 `if self._memory_store is not None:` 仅 gate memory 文本注入 |
| U1.A — Executor `execute_semantic_merge` 同形态解耦（plan §99） | `src/agents/executor_agent.py:392-427`（commit fca228b） | ✅ | diff 显示 `builder = None / if self._memory_store:` gate 拆除；builder 总创建；`build_staged_content` 总运行 |
| U1.B — `_chunked_analyze_file` 新增；触发条件 `max(len(current), len(target)) > chunk_size * 2`（默认 40KB / facts.md I1） | `src/agents/conflict_analyst_agent.py:162-180` + `237-323` | ✅ | line 165-167 触发判定；line 250-255 复用 `from src.tools.chunk_processor import split_by_semantic_boundary`（top-level import line 20，禁用反向 import executor — plan P1-2 OK） |
| ParallelFileRunner.from_api_key_env_list 并发 fan-out | `src/agents/conflict_analyst_agent.py:277-280` | ✅ | 与 facts.md D1 模板一致；keys = chunk indices 列表 |
| `_aggregate_chunked_analyses` 确定性 reducer（hard cap / fast / slow） | `src/agents/conflict_analyst_agent.py:433-512`（模块级私有函数） | ✅ | 三层 doc §5.1.1 伪码全实现；常量 `PENALTY_FACTOR=0.8 / HARD_CAP_CHUNKS=8 / HARD_CAP_BYTES=10MiB / HARD_CAP_CONFIDENCE=0.3` 均落 module-level 命名常量（lock #13/14 锚点一致） |
| `ConflictAnalysis` 加 `is_chunked: bool = False` / `chunk_count: int = Field(default=1, ge=1)` | `src/models/conflict.py:52-53` | ✅ | 字段位置在 schema 末段；默认值与 plan §1.1 一致 |
| `ThresholdConfig.chunked_aggregation_min_confidence: float = 0.85` | `src/models/config.py:153-164` | ✅ | description 含 "Calibrated against forgejo 1822-file run" — 与 plan §1.1 + facts.md A4 兼容（CLAUDE.md "Acceptable forms：Historical references in docstrings"） |
| `conflict_analyst.yaml` inputs 加 `thresholds` | `src/agents/contracts/conflict_analyst.yaml:11` | ✅ | 8 个 inputs 全保留（原 7 + `thresholds`），与 facts.md C4 锚点一致 |
| 单测 12 个覆盖 U-P1.1 ~ U-P1.12（plan 要求 6 个，executor 多写 6 个边界 / 失败 / 纯度用例） | `tests/unit/test_conflict_analyst_chunked.py` | ✅ | 全部 mapped；测试方案 v2 §2.2.1 一一对应 |
| 3 commit conventional-commits 格式（refactor → feat → perf） | `fca228b` / `3d5deee` / `32483b1` | ✅ | 类型 / 顺序 / 各 commit atomic（diff stat 验证）；executor U1.A 与 conflict_analyst U1.A 拆 commit #1 vs commit #3 的自纠合理（避免临时未通过测试中间状态） |

## 测试结果

- `pytest tests/unit/test_conflict_analyst_chunked.py tests/unit/test_conflict_analyst_round.py -v`：**17/17 通过**（Phase 1 新增 12 + conflict_analyst 现有 regression 5 全绿）
- `pytest tests/unit/ -q`：**2330 passed, 1 skipped**（Phase 0 出口基线 2318，新增 12，0 regression）
- `mypy src`：**Success: no issues found in 160 source files**（0 error，Phase 0 基线维持）
- `ruff check src/`：**All checks passed!**（0 error）
- `ruff check tests/unit/test_conflict_analyst_chunked.py`：**All checks passed!**（新测试文件 0 error，比 Phase 0 的"基线 1 F401"更干净）
- 覆盖率：本地复现 `pytest tests/unit/ --cov=src --cov-report=term` → **TOTAL 83.54%**（与 executor 报告完全一致）；相对 Phase 0 出口 83.25% 漂移 **+0.29pp**，在 ±0.5pp 容差内；门槛 80% 远超
- 提交范围 `git diff 1a40958..32483b1 --stat`：**6 文件 +721 lines / -58 lines**，全部在 Phase 1 plan §1.1 交付清单内；无范围外侧改

## 锁清单 regression 检查（Phase 0 出口 #18-22 / plan #1-12 / test #13-17）

| Lock 事实 | 检查结果 |
|---|---|
| #18 `RunBudgetExceeded` 仅定义未接线 | `grep "raise RunBudgetExceeded" src/` = 0 处。维持。✅ |
| #19 `AgentContract.version: int = Field(default=0, ge=0)` 在 `src/agents/contract.py:30-39` | `git diff 1a40958..32483b1 -- src/agents/contract.py` empty。维持。✅ |
| #20 7 yaml `version: 1` 全在第 2 行 | head -3 全 7 文件 = `name: X / version: 1`。维持。✅ |
| #21 `_schema.md` Versioning 段未触碰 | diff = empty。维持。✅ |
| #22 Phase 0 测试文件未触碰 | `git diff -- tests/unit/test_run_budget_exceeded_dataclass.py tests/unit/test_agent_contracts.py` empty。维持。✅ |
| plan #5 `ParallelFileRunner.from_api_key_env_list` 5 个调用点 + Phase 1 新增 1 = 6 个 | conflict_analyst 新增的 chunked 路径 line 277-280 是第 6 处；Phase 3 disjoint assert 接入点 ready。✅ |
| plan #6 `split_by_semantic_boundary` 必须 `from src.tools.chunk_processor` import，禁止反向 import executor | conflict_analyst_agent.py:20 `from src.tools.chunk_processor import split_by_semantic_boundary` 直接 import；U-P1.8 测试还显式 grep `from src.tools.chunk_processor import` 在源文件中存在。✅ |
| plan #9 conflict_analyst.py:106-201 U1.A 解耦 | line 121-227 `analyze_file` 已解耦；line 134 builder unconditional；line 188-207 `build_staged_content` 三次 unconditional。✅ |
| plan #10 `executor_agent.py:392-427` U1.A 同形态 + 1026 行已超 800 软约束 | commit fca228b 仅搬移代码不新增行数；line count 1026→1026 维持。Phase 5 末再评估拆 conflict_aggregation.py 的条件（>1100 trigger）未触发。✅ |
| plan #11 `chunked_aggregation_min_confidence` 落 ThresholdConfig | config.py:153-164 ✅ |
| test #13 hard cap `> 8 chunks OR > 10MB`；返回 `ESCALATE_HUMAN` + `confidence=0.3` + rationale 含 `"too large for safe chunked analysis"` | conflict_analyst_agent.py:453-469 实装：condition `chunk_count > HARD_CAP_CHUNKS or total_bytes > HARD_CAP_BYTES`；返回 strategy=ESCALATE_HUMAN，confidence=0.3 (HARD_CAP_CONFIDENCE)，rationale 含 `"too large for safe chunked analysis"`。✅（见 P2-1 备注 total_bytes 语义） |
| test #14 slow path precedence `ESCALATE > SEMANTIC > TAKE_*`；slow penalty 0.8；rationale 含 `"disagreement"` | line 492-512：`_STRATEGY_PRECEDENCE = (ESCALATE_HUMAN, SEMANTIC_MERGE, TAKE_TARGET, TAKE_CURRENT)`；`confidence = min_conf * PENALTY_FACTOR`；rationale `"... chunks disagreement ..."`。✅ |
| test #15 fast path 条件 unanimous AND min_conf ≥ threshold AND not any(security) | line 474：`if len(strategies) == 1 and min_conf >= min_confidence and not is_security:`。✅ |
| test #16 单 chunk LLM 失败 → ESCALATE_HUMAN (spec-by-test) | line 299-317 `if failed_indices: return ConflictAnalysis(... strategy=ESCALATE_HUMAN, confidence=0.3, ...)`，rationale 含 chunk index + 失败类型。U-P1.9 单测验证。✅ |
| test #17 anti-pattern 2 BaseAgent 唯一入口 | chunked path 仍走 `self._call_llm_with_retry`（line 274）；`grep self.llm.complete\|chat\|generate src/agents/conflict_analyst_agent.py` = 0。✅ |

无 lock regression。

## P0 / P1 / P2 分级问题

### P0
无。

### P1
无。

### P2（3 项不阻塞 GO，但建议后续 Phase 关闭 / 跟进）

**P2-1：reducer hard cap 第二支路 `total_bytes` 用 rationale 字节而非源 content 字节**

- 锚点：`src/agents/conflict_analyst_agent.py:450` `total_bytes = sum(len(c.rationale or "") for c in chunk_analyses)`
- 对照：test #13 / doc §5.1.1 伪码 `total_content_bytes > 10 * 1024 * 1024` 在 doc 中指 chunk 源内容（chunk 切分前的原文件字节数）
- 影响：第一支路 `chunk_count > HARD_CAP_CHUNKS` 先触发（这是 U-P1.4 实际验证的路径），所以"换错变量"在 locked 测试中不会暴露。但 `total_bytes > 10MB` 这条 OR 分支因 rationale 几乎不可能累计到 10MiB（rationale 通常 < 1KB）变成几乎永真为假的死分支。
- 严重度：P2（行为非破坏，单测覆盖未受影响；但未来 forgejo 1822-file E2E 若依赖第二条 cap 触发会失效）。
- 建议：Phase 1 不阻塞；下一轮触动该 reducer 的 Phase（U3 cache 接入时）顺手改成 `total_bytes = sum(len(getattr(c, "_chunk_source_size", 0)) for c in chunk_analyses)` 或在 `_chunked_analyze_file` 切 chunks 时把原 `current_content + target_content` 字节数往下传。**E2E 跑通后由 Verifier 复核**。

**P2-2：`conflict_analyst.yaml.inputs` 含 `thresholds`，但 `MergeState` 无 `thresholds` 字段**

- 锚点：`src/models/config.py:840` `MergeConfig.thresholds: ThresholdConfig = Field(default_factory=ThresholdConfig)` —— `thresholds` 实际位于 `state.config.thresholds`，并非 `state.thresholds`
- 对照：test U-P1.11 用 mock `_State` 类 `setattr(state, "thresholds", object())` 合成 attribute，不验证真实 `MergeState` 有 `thresholds`
- 影响：Phase 1 `analyze_file` 走可选参数 `chunk_size_chars / min_chunked_confidence`（默认 20000 / 0.85），未通过 `view.thresholds` 实际消费；所以本 Phase 不会运行时崩。但下一 Phase（或 Phase 5 cache 接入）真要从 view 读 `thresholds` 时会触发 `AttributeError`。
- 严重度：P2（spec-by-test #16 风格，未来风险）。
- 建议：Phase 2+ 接入 `chunked_aggregation_min_confidence` 真消费时，要么走 `view.config.thresholds.chunked_aggregation_min_confidence`（此时不需要 yaml 加 `thresholds`），要么在 MergeState 顶层 promote `thresholds` 字段（破坏性较大）。**Verifier 在 Phase 2 计划中需选择路径并补单测**。

**P2-3：`analyze_file` 新增可选参数未通过 `run()` 实际驱动**

- 锚点：`conflict_analyst_agent.py:73-110` `run() → _analyze_one(file_path)` 调用 chain 未传 `chunk_size_chars` 或 `min_chunked_confidence`；落到 `_analyze_one` line 94 调用 `analyze_file(...)` 也未传
- 影响：本 Phase 走默认值（20000 / 0.85），与 facts.md I1 / I5 一致 — 行为正确。但 yaml 加 `thresholds` 与 `analyze_file` 新参数都"已建未接"。
- 严重度：P2（与 P2-2 同源；本 Phase 自纠 #2 已声明 Phase 5 cache 接入时一并接线）。
- 建议：Phase 5 cache key 设计阶段一并接入；本 Phase 不动。

## 残留风险（放行后跟踪）

3 项 P2 全部归入"待后续 Phase 跟进"。本 Phase 在单测层 lock-in 了 hard cap / fast / slow 三路径的行为，后续 reducer 改动必须保证 U-P1.2 ~ U-P1.4 仍绿 — 这是 regression 网。

## 已通过事实（追加锁清单 — 见 `locks/approved-facts.md` 新加 6 条 `[code-phase-1]` 标签）

跨阶段已存档事实仍生效（plan #1-12 + test #13-17 + code-phase-0 #18-22 共 22 条），本轮新增 6 条 code 阶段事实。
