# Approved Facts Lock — large-scale-perf

> 各阶段 Gatekeeper 通过审查时**追加**已通过事实到此文件（不覆盖）。
> 每条带阶段标签：`[plan]` / `[test]` / `[code-phase-N]`。
> 后续 Gatekeeper 启动时 first action 是 Read 该文件重建跨阶段事实基线。
> 被审者**禁止改动**此文件中任何事实——改动等同 regression。
> 如修订必须触碰锁清单事实，必须先 SendMessage 给对应 Gatekeeper 申请解锁。

---

## 由 gatekeeper-plan 追加（Planner v2 通过审查，2026-05-18）

1. **[plan]** `src/models/conflict.py:40-51` ConflictAnalysis 当前 11 字段，无 `is_chunked` / `chunk_count`（Phase 1 新增不冲突）。
2. **[plan]** `src/models/config.py:949-954` `max_cost_usd` 当前 `default=None`，type `float | None`，`gt=0`。Phase 2 改 default→5.0 须随同修改 `tests/unit/test_telemetry_snapshot.py:125` 断言 + 方法名。
3. **[plan]** `src/models/config.py:956-963` `enable_working_branch` 当前 `default=False`。Phase 4 改 default→True 须随同修改 `tests/unit/test_working_branch.py:72-83` 2 处显式断言。
4. **[plan]** `src/core/orchestrator.py:262-280` ceiling check 现行实装：`spent = prior + tracker.total_cost_usd`；`spent >= ceiling` → transition AWAITING_HUMAN + checkpoint tag `"cost_ceiling_halt"`。Phase 2 RunBudgetExceeded 路径须与此协同避免 double-transition（`test_budget_double_transition_idempotent` 验证）。
5. **[plan]** `src/core/parallel_file_runner.py` 当前 65 行；`ParallelFileRunner.from_api_key_env_list` 仓库内 5 个调用点：`conflict_analyst_agent.py:81 / executor_agent.py:829 / planner_agent.py:645 / judge_agent.py:167 / judge_agent.py:1473`。Phase 3 必须全部接入 disjoint assert + Phase 1 新增 chunked 路径 = 共 6 个具名接入点。
6. **[plan]** `split_by_semantic_boundary` 真实定义位置 `src/tools/chunk_processor.py:50`。Phase 1 conflict_analyst 必须直接 `from src.tools.chunk_processor import split_by_semantic_boundary`，禁止反向依赖 agents/ 层 executor。
7. **[plan]** `src/agents/contract.py:19 AgentContract(BaseModel)` 真实存在，当前无 `version` 字段。Phase 0 新增 `version: int = Field(default=0, ge=0)`；7 yaml 全显式 `version: 1`；缺省 0 仅作 future yaml 漏写兼容兜底。
8. **[plan]** `src/agents/base_agent.py:147 / :235` `_current_phase: str` 已存在。Phase 2 `RunBudgetExceeded(phase=current_phase)` 签名兼容。
9. **[plan]** `src/agents/conflict_analyst_agent.py:106-201` analyze_file；`builder is not None` gate 在 line 146-172（与 facts.md C1 一致）。Phase 1 U1.A 解耦把 staged_content 构造移出此 gate。
10. **[plan]** `src/agents/executor_agent.py:392-427` 与 conflict_analyst 同形态 `if builder is not None:` gate；executor_agent.py 当前 1026 行（已超 <800 软约束）；config.py 当前 971 行（接近软约束）。Phase 5 末 `config.py` > 1100 行触发拆 `config_sections/`；Phase 1 末 `executor_agent.py` > 1100 行触发拆 `conflict_aggregation.py`（v2 §4 风险表已纳入）。
11. **[plan]** Q1-Q4 决策已锁定（v2 §3）：Q1 保留 `max_cost_usd` default→5.0 + 新增 `per_run_cost_warn_pct=0.8`，不引入 `per_run_cost_limit_usd` 新字段；Q2 Phase 0 集中加 `version: 1`；Q3 Phase 4 处理 worktree 默认测试；Q4 `PerFilePlanEntry` 落 `MergePlan` 基类。
12. **[plan]** 8 Phase 顺序 + 估时（合计 9.5 天）+ 19 commit 总数已锁定。修订仅允许在 P0/P1 反馈下进行；Verifier / Executor 不得擅改。

---

## 由 gatekeeper-test 追加（测试方案 v2 通过审查，2026-05-18）

源：`.multi-agent/large-scale-perf/test/FINAL.md` v2。覆盖 Phase 0+1+2。

13. **[test]** chunked reducer hard cap 锚点：`_aggregate_chunked_analyses` hard cap 触发条件 `if len(chunks) > 8 or total_content_bytes > 10 * 1024 * 1024`（doc §5.1.1 line 167，**reducer 内部源码常量，非 config 字段**）；hard cap 触发返回 `MergeDecision.ESCALATE_HUMAN` + `confidence=0.3` + rationale 含 `"too large for safe chunked analysis"` 子串（doc line 169-171 硬编码）。U-P1.4 守护。
14. **[test]** chunked reducer slow path precedence + penalty：`_strategy_precedence` 顺序锁 `ESCALATE > SEMANTIC > TAKE_*`（doc §5.1.1 line 197 注释）；slow path `confidence = min_conf * 0.8`（doc line 199 硬编码 PENALTY_FACTOR=0.8）；slow path rationale 含 `"disagreement"` 子串（doc line 202）。U-P1.3 守护。
15. **[test]** chunked reducer fast path 触发条件：`unanimous strategies AND min(confidence) >= ThresholdConfig.chunked_aggregation_min_confidence (default 0.85) AND not any(is_security_sensitive)`（doc §5.1.1 第 2 段）；任一 chunk `is_security_sensitive=True` 强制走 slow path（doc fast path 条件 `and not any(...)`）。U-P1.2 + U-P1.5 守护。
16. **[test]** U-P1.9 spec-by-test：chunked path 单 chunk LLM 失败时聚合 strategy = `MergeDecision.ESCALATE_HUMAN`（**doc/plan 未显式规定，由测试方案锁定为最保守安全默认**）。若 Executor 实施期发现 doc/plan 有更合理失败容错路径，走 scope.md §3.1 "细节自纠" 上报，Verifier 修订测试期望。
17. **[test]** 测试方案不写新的 BaseAgent 唯一入口 anti-pattern 测试：anti-pattern #2（"BaseAgent 子类未绕过 `_call_llm_with_retry`"）由现有 `tests/unit/test_agent_contracts.py` regression 守护；本测试方案不重复写。Phase 2 GO 条件 G2-2 直接挂现有 regression。

> 残留 P2（不阻塞 GO）：(a) v2 用例汇总数字 33+1+4=38，但 §0/§1/§4/§12 多处仍写 34/39，Executor 实施前可统一修；(b) U-P0.5 / U-P0.6 可参数化合并；(c) U-P1.9 spec-by-test 已纳入 #16。

---

## 由 gatekeeper-code 追加（Phase 0 v2 通过审查，2026-05-18）

源：`.multi-agent/large-scale-perf/code/phase-0/FINAL.md` v2 / commits `aa540d2` + `1a40958`。

18. **[code-phase-0]** `RunBudgetExceeded(Exception)` 落 `src/models/state.py:38-52`；签名 `__init__(self, spent: float, limit: float, phase: str)`；`str(exc)` 模板 `f"Run budget exceeded in phase {phase!r}: spent={spent} limit={limit}"`。**仅定义未接线** — 仓库 `grep "raise RunBudgetExceeded"` 仅命中测试 `tests/unit/test_run_budget_exceeded_dataclass.py:41`，prod 代码 0 处 raise。Phase 2 必须 `from src.models.state import RunBudgetExceeded` 后由 `base_agent._call_llm_with_retry` raise。
19. **[code-phase-0]** `AgentContract.version: int = Field(default=0, ge=0, description=...)` 落 `src/agents/contract.py:30-39`；description 显式写明 "Default 0 allows future yaml omissions to load without crash; the 7 shipped yaml all declare version=1, so default is never consumed in practice"。`AgentContract` 字段顺序：`name → version → inputs → output_schema → gates → forbidden → collaboration → requires_human_options`（model_dump 输出含 `"version"` 键）。
20. **[code-phase-0]** 7 个 shipped contract yaml 全部声明 `version: 1`（int），均位于文件第 2 行 `name:` 之下：`src/agents/contracts/{conflict_analyst,executor,human_interface,judge,memory_extractor,planner,planner_judge}.yaml:2`。无遗漏。Phase 5 cache key 消费 `contract.version` 时这 7 个值即权威来源。
21. **[code-phase-0]** `src/agents/contracts/_schema.md` Versioning 段位于 `:51-77`；bump 触发条件硬编码 3 条（prompt 内容变 / aggregation reducer 改阈值或惩罚 / inputs+output_schema+gates 变化）；"不 bump" 例外 3 条（纯 refactor / docstring / 加测试）。U3 cache 引入时按此规则 bump。
22. **[code-phase-0]** Phase 0 unit 测试入口：`tests/unit/test_run_budget_exceeded_dataclass.py`（5 函数覆盖 U-P0.1 (a)(b)(c)(d)）+ `tests/unit/test_agent_contracts.py:275-350` 6 函数（U-P0.2~U-P0.7）。常量 `CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "src/agents/contracts"` + `EXPECTED_CONTRACT_STEMS` 7 stem 集合 落 `tests/unit/test_agent_contracts.py:30-39`（位置在所有 import 之后，符合 ruff E402）。

> 验证基线锁定：commit `1a40958` 后 `pytest tests/unit/` = **2318 passed, 1 skipped**；`mypy src` = 0 error；`ruff check src/` = 0 error；`ruff check tests/unit/test_agent_contracts.py` = 1 error (pre-existing F401，不属本 Phase 引入)；coverage TOTAL = **83.25%**。后续 Phase 不得 regression 此基线。

---

## 由 gatekeeper-code 追加（Phase 1 v1 通过审查，2026-05-18）

源：`.multi-agent/large-scale-perf/code/phase-1/FINAL.md` v1 / commits `fca228b` + `3d5deee` + `32483b1`。

23. **[code-phase-1]** ConflictAnalyst U1.A 解耦落地 `src/agents/conflict_analyst_agent.py:121-227`：`builder = AgentPromptBuilder(...)` 在 line 134 无条件创建；`build_staged_content` 三次调用在 line 188-207 无条件运行；`if self._memory_store is not None:` (line 138) 仅 gate memory 文本注入。Executor 同形态解耦落 `src/agents/executor_agent.py:392-427`（commit fca228b）。
24. **[code-phase-1]** `_chunked_analyze_file` 落 `src/agents/conflict_analyst_agent.py:237-323`；触发条件 `max(len(current_content or ""), len(target_content or "")) > chunk_size * 2`（默认 chunk_size=20000，即 40KB 阈值）落 line 165-167；split 复用 `from src.tools.chunk_processor import split_by_semantic_boundary` (top-level import line 20)；并发 fan-out 走 `ParallelFileRunner.from_api_key_env_list(self.llm_config.api_key_env_list)` (line 277-280) — 这是 plan #5 第 6 个具名 disjoint 接入点，Phase 3 必须接入。
25. **[code-phase-1]** `_aggregate_chunked_analyses` 落 `src/agents/conflict_analyst_agent.py:433-512`，**模块级私有纯函数**（非 ConflictAnalystAgent 类方法）；module-level constants `PENALTY_FACTOR=0.8 / HARD_CAP_CHUNKS=8 / HARD_CAP_BYTES=10*1024*1024 / HARD_CAP_CONFIDENCE=0.3 / _STRATEGY_PRECEDENCE=(ESCALATE, SEMANTIC, TAKE_TARGET, TAKE_CURRENT)` 落 line 25-35。三层 hard cap / fast / slow 行为锁定 — U-P1.2 ~ U-P1.5 regression net 守护，后续 Phase 改动 reducer 时必须保证这 4 用例仍绿。
26. **[code-phase-1]** **P2-1 已知缺陷（不阻塞 Phase 1）**：`_aggregate_chunked_analyses:450` `total_bytes = sum(len(c.rationale or "") for c in chunk_analyses)` 实际累加 rationale 字节而非 doc §5.1.1 伪码所指 chunk 源内容字节。第一支路 `chunk_count > 8` 先触发（U-P1.4 验证），所以 locked 测试不暴露；但 `total_bytes > 10MiB` 这条 OR 分支变成几乎永假死分支。**修复时机**：Phase 3 disjoint 接入或 U3 cache 接入时一并改正，需把原 content 字节往下传 reducer 或新增 `_chunk_source_size` 字段。
27. **[code-phase-1]** **P2-2 未接线 (`thresholds` 入参)**：`conflict_analyst.yaml:11` `inputs` 含 `thresholds`，但 `MergeState` 无 `thresholds` 字段（实际位于 `state.config.thresholds` / `src/models/config.py:840 MergeConfig.thresholds`）；U-P1.11 测试用 mock `_State + setattr` 合成，未验证真实 MergeState。Phase 1 走 `analyze_file` 可选参数（默认 20000 / 0.85）规避运行时崩，但 Phase 5 cache key 接入或 Phase 2 真消费 `view.thresholds` 时会 AttributeError — Verifier Phase 2 计划必须选定 `view.config.thresholds.chunked_aggregation_min_confidence` 路径或 promote `thresholds` 至 MergeState 顶层。
28. **[code-phase-1]** Phase 1 unit 测试入口：`tests/unit/test_conflict_analyst_chunked.py`（12 函数覆盖 U-P1.1 ~ U-P1.12 全部）；`tests/unit/test_conflict_analyst_round.py` (Phase 1 前已存在 5 函数) regression 全绿，确保 commit_round 路径不受 U1.A 解耦影响。文件大小：`conflict_analyst_agent.py` 314→519（<800 ✅）/ `executor_agent.py` 1026→1026 无变化（已超基线，Phase 5 末 >1100 trigger 拆 conflict_aggregation.py） / `config.py` 971→983（<1100 ✅）。

> 验证基线刷新（Phase 1 出口）：commit `32483b1` 后 `pytest tests/unit/` = **2330 passed, 1 skipped**（基线 +12）；`mypy src` = 0 error；`ruff check src/` = 0 error；`ruff check tests/unit/test_conflict_analyst_chunked.py` = 0 error；coverage TOTAL = **83.54%**（+0.29pp）。后续 Phase 不得 regression 此基线。

---

## 由 gatekeeper-test 追加（测试方案 v3 → FINAL，2026-05-18）

源：`.multi-agent/large-scale-perf/test/FINAL.md` v3。新增覆盖 Phase 2 lock #27 路径 A + Phase 3 + Phase 4（合计 16 用例：3 + 8 + 5；外加 1 E2E-P4.A）。Phase 0/1/2 §2.3.1~§2.3.4 v2 用例编号 / 名称 / 锚点完全保留。

29. **[test]** `MergeState.thresholds: ThresholdConfig` 字段语义锁定为"运行态快照" —— orchestrator init phase 从 `state.config.thresholds` 复制独立实例（非引用），后续修改 `state.config.thresholds` 不影响 `state.thresholds`。U-P2.14 (a)(b)(c)(d) + U-P2.15 (a)(b)(c)(d) 守护；对应 scope.md §6 路径 A 第 1-2 步。Executor 实施时若选 `MergeStateLive` 或更细粒度子类落点，按 scope.md §6 路径 A 第 1 步 "如已存在 ThresholdConfig 默认实例化路径则复用" 处理，Verifier 修订时锁定具体路径。
30. **[test]** `ConflictAnalystAgent.run()` 必须把 `view.thresholds` 真实驱动到 `analyze_file(thresholds=...)`；**不再走 Phase 1 残留的 mock + setattr 合成默认 0.85 路径**。U-P2.16 (a)(b)(c) 守护；对应 scope.md §6 路径 A 第 3-4 步 + HANDOFF §4.3 未编号 P2 修复。U-P2.16 (b) 子断言：mock 截获的 `ThresholdConfig` 实例 `id()` 与 `state.thresholds` 相同（链路 view.thresholds → analyze_file 入参），**非** `view.config.thresholds`。
31. **[test]** `assert_disjoint_file_shards(shards: list[list[str]]) -> None` helper 落 `src/core/parallel_file_runner.py`；异常类型锁定为 `FileShardOverlap(ValueError)`（**非** `SystemExit` / `AssertionError` / `RuntimeError`）；`str(exc)` 必须包含重叠 file_path 字面值（U-P3.2 (a)(b)(c)(d) 守护）。lock #5 列出的 6 处具名接入点必须各自 1 个单测（U-P3.3~U-P3.8 一一对应），mock 边界统一为 `MagicMock(wraps=原)` + `call_count >= 1` + **绝不替换实现体**（v3 §5.4 第 3 条 mock 边界约束）。U-P3.7 接入参数形态（chunk_id vs file_path）由 Executor 实施时决定，本测试方案只锁"helper 被调 + 不 raise"；走 scope.md §3.1 细节自纠路径。
32. **[test]** `enable_working_branch` default `False → True` 三态测试入口锁定在 `tests/unit/test_working_branch.py`：U-P4.1 重命名 line 72-75 `test_enable_working_branch_defaults_false` → `_defaults_true` + 断言 `is False → is True`；U-P4.2 line 77-83 维持不动；U-P4.3 新增 method `test_enable_working_branch_can_be_disabled_with_explicit_false`（backward compat）。lock #3 line 72-83 锚点区间在 Phase 4 实施期间**不得被删除**（v3 §10.2 第 4 项实施纪律守护）。U-P4.4 orchestrator init phase 接入点 + U-P4.5 wizard default 取值路径由 Executor 实施时定位，本测试方案锁"行为可观察 + helper 被调"；走 scope.md §3.1 细节自纠路径（v3 §6.4 已显式记入风险）。

> 验证基线锁定：v3 = 55 测试项（47 单元 + 1 Web + 7 手工 E2E）；新增 16 用例（3 Phase 2 路径 A + 8 Phase 3 + 5 Phase 4）+ 1 E2E-P4.A；失败 : 正常 = 14/55 = 25.5% ≥ 1:3 达标。Phase 0/1/2 v2 用例编号 / 名称 / 锚点 100% regression 守护。残留 P2（不阻塞 GO）：U-P3.7 接入参数形态不确定 / U-P4.5 wizard default 路径不确定 / U-P4.4 mock fork repo 方式选择 — 三项均由 scope.md §3.1 细节自纠 + Verifier 修订路径兜底，Executor 实施期按需上报。

---

## 由 gatekeeper-code 追加（Phase 2 v1 通过审查，2026-05-18）

源：`.multi-agent/large-scale-perf/code/phase-2/FINAL.md` v1 / commits `8eb0a26` + `c1de270` + `506c44b` + `1780dec`。

33. **[code-phase-2]** RunBudgetExceeded 真实接线：`base_agent._check_budget()` 在 `src/agents/base_agent.py:493`（pre）+ `:665`（post）双调；签名沿用 phase-0 #18 锁定的 `(spent, limit, phase)`，`spent >= limit`（含 ==，U-P2.9 守护）触发 raise；`limit_usd is None` 或 `_cost_tracker is None` 时安全 noop（U-P2.4 守护）。`_call_llm_with_retry` 仍是唯一 LLM 入口（anti-pattern #2 由 `test_agent_contracts.py` 37 用例 regression 守护，本次全绿）。
34. **[code-phase-2]** Budget warning 一次性 emit：`_budget_warning_emitted: bool` 状态字段在 `BaseAgent.__init__:153` 初始化；`set_budget()` 调用重置；首次 `spent >= limit*warn_pct` 时 emit `ActivityEvent(event_type="progress", action="budget_warning", phase=current_phase, extra={"pct": ratio})` 一次后落 True；后续 check 不重复 emit（U-P2.3 守护）。**细节自纠**：plan §2 P2 原文 "调 ctx.emit"，BaseAgent 无 ctx，改为新加 `set_activity_callback` setter + `_on_activity(ActivityEvent(...))` 调用，由 Orchestrator `_inject_cost_tracker:529-533` 注入，语义等价。
35. **[code-phase-2]** Orchestrator 双 transition 互锁两层：(a) 主循环顶 `if state.status == SystemStatus.AWAITING_HUMAN: self._finalize_log(state, run_start); return state`（`src/core/orchestrator.py:267-269`），保证 BaseAgent raise + transition 完成后下一轮直接退出；(b) `except RunBudgetExceeded` 内 transition 包 `try/except ValueError`（`:371-374`），覆盖 G5 ceiling 已先 transition 的并发场景。`test_budget_double_transition_end_to_end_scenario` + `test_g5_ceiling_check_skips_when_status_awaiting_human` 共同守护。
36. **[code-phase-2]** Partial report 路径：`<repo>/.merge/runs/<run_id>/budget_exceeded_report.md`，由 `_write_budget_exceeded_report`（`src/core/orchestrator.py:535-560`）写出；Markdown 含 `run_id / phase / spent / limit` 四字段 + 人类可读 prompt；write 失败仅 `logger.debug(..., exc_info=True)`，AWAITING_HUMAN transition + checkpoint tag `"budget_exceeded"` 必走（plan §2 P2 第 3-4 项要求）。
37. **[code-phase-2]** `MergeConfig.max_cost_usd` default 锁定 `5.0`（`gt=0`，`src/models/config.py:961-971`）；新增 `per_run_cost_warn_pct: float = 0.8`（`ge=0.0, le=1.0`，`:972-980`）。`max_cost_usd=None` 仍合法且禁用 cap（U-P2.4 + U-P2.8 共同守护）。serializer cost_summary 输出 enriched 字段 `limit_usd` + `warn_pct`（`src/web/serializers.py:_serialize_cost_summary:372-388`）；web `BudgetBar` 三态渲染绿/橙/红 + `limit===null` 隐藏（`web/src/views/RunDashboard.tsx:316-356`，U-W2.1 4 props 子断言守护）。
38. **[code-phase-2]** lock #27 路径 A 完整落地（HANDOFF §4.3 phase-1 未编号 P2 修复）：(1) `MergeState.thresholds: ThresholdConfig = Field(default_factory=ThresholdConfig, ...)` 字段（`src/models/state.py:86-94`）；(2) `InitializePhase._run_sync` 顶部 `state.thresholds = state.config.thresholds.model_copy()`（`src/core/phases/initialize.py:293-297`）— **值快照非引用**，U-P2.15 (c)(d) 守护；(3) `ConflictAnalystAgent.run()` 读 `view.thresholds.chunked_aggregation_min_confidence` + `view.config.chunk_size_chars`，显式驱动 `analyze_file(chunk_size_chars=..., min_chunked_confidence=...)`（`src/agents/conflict_analyst_agent.py:72-101`）。**细节自纠**：`analyze_file` 签名保持 Phase 1 锁定的 `(chunk_size_chars: int | None, min_chunked_confidence: float | None)`（不重构为传 `ThresholdConfig` 整对象）；U-P2.16 (b) "id() 同源" 子断言因此弱化为"值同源"，由 Phase 3 v1 顺手补强（review-v1.md P2-2 记录）。

> 验证基线刷新（Phase 2 出口）：commit `1780dec` 后 `pytest tests/unit/` = **2345 passed, 1 skipped**（Phase 1 +15）；`mypy src` = 0 error；`ruff check src/` = 0 error；coverage TOTAL = **83.60%**（+0.06pp）；web `vitest run` = **83/83 passed / 9 test files**。后续 Phase 不得 regression 此基线。

> 残留 P2（不阻塞 Phase 2 GO；Phase 3/5 处理）：(a) `src/agents/base_agent.py` 830 行越过 CLAUDE.md "<800" 软约束，建议 Phase 5 cache `_cached_call` 接入时一并抽 helper；(b) U-P2.16 (b) "id() 同源" 子断言弱化，建议 Phase 3 v1 顺手扩 `state.config.thresholds` 与 `state.thresholds` 值不同的对照断言。

---

## 由 gatekeeper-code 追加（Phase 3 v2 通过审查，2026-05-18）

源：`.multi-agent/large-scale-perf/code/phase-3/FINAL.md` v2 / commits `8f81798`（v1 src + 测试基础） + `5d659a2`（v2 test 改造，0 src 改动）。

v1 → v2 修订路径：review-v1 标 P1-1（U-P3.5/3.6/3.8 module-level call helper，3 接入点无 regression 守护）；v2 改造为方案 A：真实 instantiate Agent + 完整 MergeState fixture + patch 下游 LLM AsyncMock；gatekeeper-code 二审 acceptance 通过（Stage 1 + Stage 2 实测注释 src 中 3 处 assert 行 → 对应测试全部 FAILED）。

39. **[code-phase-3]** `FileShardOverlap(ValueError)` + `assert_disjoint_file_shards(shards: list[list[str]]) -> None` 落 `src/core/parallel_file_runner.py:19-41`；纯函数（不副作用排序），用 `Counter` 统计 + `sorted(duplicates)` 入 exc 字符串；`str(exc)` 模板 `f"file shards overlap on: {duplicates}"`。`issubclass(FileShardOverlap, ValueError) is True / SystemExit is False`（U-P3.2 守护）；helper 接 `[]` / 单元素 shard 均合法 noop。
40. **[code-phase-3]** lock #5 列出的 6 处具名接入点全部接入 + 真实 agent path regression-protected（位置紧贴对应 `runner.run_files` 调用之前）：
    1. `src/agents/conflict_analyst_agent.py:107-109` multi-file fan-out — `assert_disjoint_file_shards([[fp] for fp in high_risk_files])`（U-P3.8 守护，真实 `run()` 路径）
    2. `src/agents/conflict_analyst_agent.py:291-296` chunked path runner — shard 形态 `"<file>#<idx>"`（U-P3.7 守护，真实 `analyze_file` chunked 路径；lock #31 接入参数形态自纠落地）
    3. `src/agents/executor_agent.py:832-837` rebuttal chunk runner — `[[issue.file_path for issue in chunk] for chunk in chunks]`（U-P3.3 守护，真实 `build_rebuttal` 路径，触发阈值 `_REBUTTAL_CHUNK_SIZE=25`）
    4. `src/agents/planner_agent.py:648-653` `_classify_batch` sub-chunks — `[[fd.file_path for fd in chunk] for chunk in chunks]`（U-P3.4 守护，真实 `_classify_batch` 路径，触发阈值 `_CLASSIFY_FILE_CHUNK_SIZE=100`）
    5. `src/agents/judge_agent.py:170-173` per-file fan-out — `[[fp] for fp in high_risk_records.keys()]`（U-P3.5 守护，真实 `run()` 路径，O-J1/O-J3 short-circuit 必须被 fixture 关闭）
    6. `src/agents/judge_agent.py:1480-1483` chunk runner — `[[entry[0] for entry in chunk] for chunk in chunks]`（U-P3.6 守护，真实 `review_batch` 路径，触发阈值 `_BATCH_SIZE=8`）
41. **[code-phase-3]** U-P3.3 ~ U-P3.8 真实 agent path regression net acceptance 锁定：注释 `src/` 中 6 接入点任意 1 处 assert 行 → 对应 1 个 test 必须 FAILED（要么 `spy.call_count == 0`，要么 `DID NOT RAISE FileShardOverlap`）。gatekeeper-code 二审端独立 Stage 1 + Stage 2 实测通过。后续 Phase 不得 regression：**删除 assert 行而测试仍绿 = test 守护失效，立刻 NO-GO**。mock 边界统一 `MagicMock(wraps=assert_disjoint_file_shards)` + `call_count >= 1`，绝不替换实现体（lock #31 第 3 句 + test FINAL §5.4 第 3 条约束守护）。
42. **[code-phase-3]**（**Phase 2 P2-2 闭合**）`view.thresholds` 路径与 `view.config.thresholds` 路径非同源 — `test_run_reads_state_thresholds_not_config_thresholds`（`tests/unit/test_state_thresholds.py:178-263`）：构造 `state.thresholds.chunked_aggregation_min_confidence == 0.91` + `state.config.thresholds.chunked_aggregation_min_confidence == 0.5`（model_copy update 互不影响），`ConflictAnalystAgent.run()` 后 `analyze_file` 捕获 0.91 → 锁定 view.thresholds 是源 of truth，非 view.config.thresholds。Phase 2 review-v1 P2-2 残留闭合。

> 验证基线刷新（Phase 3 出口）：commit `5d659a2` 后 `pytest tests/unit/` = **2355 passed, 1 skipped**（Phase 2 +10）；`mypy src` = 0 error；`ruff check src/` = 0 error；coverage TOTAL = **83.80%**（+0.20pp）。后续 Phase 不得 regression 此基线。

> 残留 P2（不阻塞 Phase 3 GO；Phase 5/6 处理）：(a) `src/agents/base_agent.py` 830 行越 CLAUDE.md "<800" 软约束（Phase 2 滚来），建议 Phase 5 cache `_cached_call` 接入时一并抽 helper。

---

## 由 gatekeeper-code 追加（Phase 4 v1 通过审查，2026-05-18）

源：`.multi-agent/large-scale-perf/code/phase-4/FINAL.md` v1 / commits `d195642` + `23d159a`。

43. **[code-phase-4]** `MergeConfig.enable_working_branch` default 翻转 `False → True`（`src/models/config.py:981-989`），description 显式 "U7: default flipped to True so a half-finished run never pollutes fork_ref HEAD; set to False explicitly to restore the legacy in-place behavior"。Phase 3 出口前 default=False（plan #3 锁定）已成历史，CLAUDE.md `:147` 新增段落同步用户可见行为变化说明。
44. **[code-phase-4]** Setup wizard yaml synth 同步：`src/cli/commands/setup.py:244` `enable_working_branch: True`（dict default）+ `:223-226` 新增模块级常量 `ENABLE_WORKING_BRANCH_HINT: str = "推荐：每 run 隔离写入，避免 fork_ref 被半完成状态污染 (worktree isolation; matches MergeConfig.enable_working_branch default)"`。**细节自纠**（lock #29 第 5 行预留）：setup.py 是 yaml synth 写出器（v0.PR-3 已移除 interactive wizard），"复选框"等价为 dict default；description 通过模块级常量暴露给测试断言 + 文档参考。
45. **[code-phase-4]** lock #3 锚点区间 `tests/unit/test_working_branch.py:72-83` Phase 4 守护方式：旧 `test_enable_working_branch_defaults_false` 重命名为 `_defaults_true`（`:72-78`）+ 断言迁移 `is False → is True`（**重命名 ≠ 删除，符合 lock #29 第 4 行"锚点区间不得被删除"约束**）；`test_enable_working_branch_can_be_set`（`:81-88`）维持不变；新增 `test_enable_working_branch_can_be_disabled_with_explicit_false`（`:91-101`，backward compat）。`_make_config(enable: bool = False)` helper（`:57-64`）默认值未翻转，因为所有调用方显式传 True / False 参数，与 schema default 解耦。
46. **[code-phase-4]** U-P4.1 / U-P4.4 / U-P4.5 / plan §2 P4 列名 #1 真实 acceptance 锁定（gatekeeper-code 二审端独立 Stage A + Stage B 实测通过）：(a) 注释 `src/models/config.py:982 default=True → False` → `test_enable_working_branch_defaults_true` + `test_worktree_enabled_by_default_in_new_state` + `test_orchestrator_creates_branch_on_run_when_enabled` 三测全部 FAILED；(b) 注释 `src/cli/commands/setup.py:244 yaml default True → False` → `test_default_config_enables_worktree` FAILED。U-P4.4 mock 边界用 `patch.object(orch.git_tool, "create_working_branch")` + `mock_create.assert_called_once_with("merge/auto-{timestamp}", "main")`（细节自纠：放弃 spy-self 模板正则，改 startswith 入参形态校验，模板格式由 GitTool 单测 `test_working_branch.py:105-127` 5 个测试守护）。后续 Phase 不得 regression：删除 default=True 而测试仍绿 = test 守护失效，立刻 NO-GO。

> 验证基线刷新（Phase 4 出口）：commit `23d159a` 后 `pytest tests/unit/` = **2361 passed, 1 skipped**（Phase 3 +6）；`mypy src` = 0 error；`ruff check src/` = 0 error；coverage TOTAL = **83.80%**（持平 Phase 3）。本会话累计 Phase 2+3+4 = 8 commit；基线 2330 → 2361 / cov 83.54% → 83.80%。

> 残留 P2（不阻塞 Phase 4 GO；Phase 5/6 处理）：(a) `src/agents/base_agent.py` 830 行越 CLAUDE.md "<800" 软约束（Phase 2 滚来）。本会话 scope 内 Phase 2+3+4 已全部 GO；Phase 5/6/7 下次会话继续。

