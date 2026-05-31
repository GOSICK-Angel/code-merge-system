# Test Plan v1 — Gatekeeper Review

> gatekeeper-test 审，针对 `.multi-agent/large-scale-perf/test/v1.md`。
> 上游契约：`plan/FINAL.md` v2 + `decisions/scope.md`(Phase 0+1+2) + `locks/approved-facts.md` 12 条 [plan] 事实 + `facts.md` A-Q。
> 审查日期：2026-05-18。**结论：NO-GO（要求修改）**。

---

## 0. 已通过事实（基线）

本次为首审，`locks/approved-facts.md` 中 [plan] 标签事实 12 条：

- #1 ConflictAnalysis 11 字段清单 + Phase 1 新增 `is_chunked`/`chunk_count`
- #2 `max_cost_usd` default `None → 5.0`；`test_telemetry_snapshot.py:125` 须改
- #3 `enable_working_branch` Phase 4 范畴（**本会话排除**）
- #4 orchestrator G5 ceiling 现行实装 + double-transition 协同
- #5 ParallelFileRunner 6 接入点（**Phase 3 范畴，本会话排除**）
- #6 `split_by_semantic_boundary` 真定义 `src/tools/chunk_processor.py:50`
- #7 `AgentContract.version` 字段 + 7 yaml 全显式 1 + 默认 0 兼容
- #8 `RunBudgetExceeded(phase=current_phase)` 签名 + `_current_phase: str` 存在（`base_agent.py:147`）
- #9 conflict_analyst U1.A 解耦点（`conflict_analyst_agent.py:106-201`）
- #10 executor 同形态 U1.A + 文件大小约束
- #11 Q1-Q4 决策已锁定
- #12 8 Phase 顺序 + 19 commit 已锁定

代码现状已交叉核验：
- `web/src/views/RunDashboard.test.tsx` **确实不存在**（v1 §2.1.1/§7.3 描述准确）
- `tests/unit/test_telemetry_snapshot.py:125` 实测 = `def test_max_cost_usd_field_defaults_none`，与 v1 一致
- `MergeDecision` enum 定义在 `src/models/decision.py:8`（详见 P0-1）
- 5 处 `ParallelFileRunner.from_api_key_env_list` 调用点全部存在
- `base_agent.py` 异常类层级：`CircuitBreakerOpen` / `AgentError` / `AgentExhaustedError`，**无 `LLMRetriableError`**（详见 P0-2）

---

## 1. P0（必改，阻塞 GO）

### P0-1：U-P1.2 / U-P1.3 / U-P1.4 / U-P1.5 使用了根本不存在的 `recommended_strategy` 枚举值

**事实**：`src/models/conflict.py:42` `ConflictAnalysis.recommended_strategy: MergeDecision`。
`MergeDecision` 定义在 `src/models/decision.py:8-14`，**合法成员只有 6 个**：
- `TAKE_CURRENT = "take_current"`
- `TAKE_TARGET = "take_target"`
- `SEMANTIC_MERGE = "semantic_merge"`
- `MANUAL_PATCH = "manual_patch"`
- `ESCALATE_HUMAN = "escalate_human"`
- `SKIP = "skip"`

**问题**：v1 §2.2.1 多处出现字面量 `recommended_strategy="auto_merge"`（U-P1.2、U-P1.5）和 `"human_review"`（U-P1.3、U-P1.4）。这两个字符串不在 enum 里，`ConflictAnalysis(recommended_strategy="auto_merge", ...)` 在测试 setUp 阶段就会抛 `pydantic.ValidationError`，**测试无法运行**。

**修改要求**：
- U-P1.2 fast path："unanimous + min_conf ≥ threshold + 无 security" 的语义对应"全部 chunk 一致为某 auto 决策（如 `SEMANTIC_MERGE`）" → 用 `MergeDecision.SEMANTIC_MERGE`（fast path 的可自动合并值）；期望聚合返回 `MergeDecision.SEMANTIC_MERGE`。
- U-P1.3 slow path（disagreement）："precedence 取严格者" → 输入混合 `[SEMANTIC_MERGE, ESCALATE_HUMAN, SEMANTIC_MERGE]`，期望返回 `MergeDecision.ESCALATE_HUMAN`（precedence 严格端）。
- U-P1.4 hard cap：期望返回 `MergeDecision.ESCALATE_HUMAN`（唯一表达"转人工"的合法 enum 值），rationale 含 `"hard cap"` 子串。**无需 SendMessage 给 gatekeeper-plan 申请解锁**——锁清单 #1 仅锁 11 字段清单，未锁聚合返回值；本项是修类型错误，不动语义。
- U-P1.5 security fallback：fast path 输入用 `SEMANTIC_MERGE` 全 unanimous，但某 chunk `is_security_sensitive=True`；期望返回 slow path 决策（如降级为 `ESCALATE_HUMAN`）。
- §8 "违反锁清单的风险点"那段（关于 `"escalate"` vs `"human_review"`）整段删除——前提是错的。

**锚点**：`src/models/decision.py:8-14`，`src/models/conflict.py:42`。

---

### P0-2：U-P1.9 引用不存在的异常类 `LLMRetriableError`

**事实**：`src/agents/base_agent.py` 异常层级（实测 grep）：
- line 79：`class CircuitBreakerOpen(RuntimeError)`
- line 83：`class AgentError(RuntimeError)`
- line 91：`class AgentExhaustedError(RuntimeError)`
- 错误分类用 `src/llm/error_classifier.py` 的 `ClassifiedError` + `ErrorCategory`（StrEnum），**无 `LLMRetriableError`**。

**问题**：U-P1.9 写"mock LLM 抛 `LLMRetriableError`（或 transport timeout）"——类名虚构，违反 CLAUDE.md「不要发明 API」。Executor 看到这条会卡壳。

**修改要求**：
- 用真实异常：mock provider 抛 `httpx.ReadTimeout` / `httpx.TimeoutException`（transport 层），让 `error_classifier.classify_error` 归类为 `ErrorCategory.TIMEOUT` 触发 `_call_llm_with_retry` 内的 retry → 最终 `AgentExhaustedError`；
- 或更简单：在 `_run_conflict_chunk` 单 chunk handler 内 mock 直接 raise `AgentError(msg, classification)`，验证 reducer 跳过失败 chunk + 落 rationale。
- 期望字段调整：rationale 含失败 chunk 索引；strategy = `MergeDecision.ESCALATE_HUMAN`；不抛异常到 caller。

**锚点**：`src/agents/base_agent.py:79-95`，`src/llm/error_classifier.py:16,29`。

---

### P0-3：U-P1.4 mock 输入 "hard cap = 10" 缺锚点

**事实**：plan §1.1 / doc §5.1.1 伪码均未明文规定 hard cap 数值或配置字段名。`MergeConfig` / `ThresholdConfig` 现状（`src/models/config.py:931-963` + `:151-152`）也无 `hard_cap_chunks` 之类字段。

**问题**：U-P1.4 输入"mock chunk 列表长度 = 配置 hard cap + 1（如 11，假设 hard cap = 10）"——既没列出 config 字段名也没说运行时来源。Executor 实施时会两难：自己拍脑袋一个常量？还是新增 config 字段？后者属于"架构级偏离"（scope.md §3.1）要 SendMessage 给 team-lead。

**修改要求**：
- 要求 v2 显式声明 hard cap 的来源之一：(a) 复用已有 `ThresholdConfig` 字段、(b) Phase 1 plan 交付物里**已存在**的某常量、(c) 新增 `ThresholdConfig.chunked_hard_cap_chunks: int = 10`（若 (c) 走，需 SendMessage gatekeeper-plan 申请扩 plan §1.1 ThresholdConfig 字段集，因 plan 当前 Phase 1 只列了 `chunked_aggregation_min_confidence` 单字段）。
- 推荐 (a) / (b)：先 grep doc §5.1.1 全段 + plan §2 Phase 1 交付物，若文档真无来源则锁 (c) 并申请解锁；否则用文档既有字段。
- 修订后 U-P1.4 输入段落必须含字段名 + 默认值的 anchor。

---

## 2. P1（必改，阻塞 GO）

### P1-1：U-P2.11 与现有 `test_agent_contracts.py` 重复，且 mock 边界手段不合规

**事实**：facts.md A5 anti-pattern #2 已由 `tests/unit/test_agent_contracts.py` **强制**（plan 反复引用）。该文件已 grep `self.llm.complete|chat|generate(` 全 `src/agents/`。

**问题**：
- U-P2.11 "test_baseagent_call_llm_with_retry_remains_sole_llm_entry" 本质上重复 `test_agent_contracts.py` 既有断言（v1 自己在 "断言锚点" 列承认"现有 `test_agent_contracts.py` 已强制；本测试只验现状不破"）——**冗余用例**。
- 测试体内调 `git grep` shell 子进程也违反 v1 §10.1 "测试间无共享可变状态" 精神（fs/外部依赖）；CI 容器没 git 时直接 fail。

**修改要求**：删除 U-P2.11，把"BaseAgent 唯一入口"GO 条件 G2-2 直接挂到现有 `test_agent_contracts.py` regression（§3.3 表内 G2-2 行已可这样写）。

---

### P1-2：U-P0.2 期望"禁止继承 BaseException / SystemExit" 写成断言，但无对应被测代码路径

**问题**：U-P0.2 期望列写"**禁止**继承 `BaseException` / `SystemExit`"，但 Phase 0 plan 交付物已锁定 `class RunBudgetExceeded(Exception)`（plan §2 Phase 0 第 1 项 + locks #8）。也就是说测试断言变成"验证一个已被 plan 强制的事实"——属于结构性断言而非行为断言，等价于"读源码"测试，价值低。

**修改要求**：
- 简化为：`assert issubclass(RunBudgetExceeded, Exception)` + 一条 `assert not issubclass(RunBudgetExceeded, SystemExit)` 即可；
- 或合并进 U-P0.1（去掉 U-P0.2 单立），把这两条 sub-assert 放 U-P0.1 末尾。

---

### P1-3：U-P0.6 / U-P0.7 mock 边界与 fs 真读混用未隔离

**事实**：`src/agents/contracts/*.yaml` 是项目源文件；测试 `glob` + 真读会被 Phase 0 一起 commit 的 yaml 改动反复影响（CI 跑时 yaml 已是改后状态）。

**问题**：v1 §5.3 写"yaml load 测试直接 fs read 真实文件，不 mock `yaml.safe_load`"——OK，但 U-P0.6 / U-P0.7 用例描述只说"`for yaml in glob("src/agents/contracts/*.yaml")`"，没明确 path resolve 策略。若 pytest cwd 漂移会 glob 到 0 文件——测试反而 silent pass（`for` 体未执行）。

**修改要求**：U-P0.6 / U-P0.7 输入段加锚点：
- 路径用 `pathlib.Path(__file__).resolve().parent.parent.parent / "src/agents/contracts/*.yaml"`（或 `importlib.resources`）；
- 用 `assert len(yaml_files) == 7` 作为 sanity gate 放断言列首位（防 silent pass）。

---

### P1-4：U-P2.3 "warning emits 一次" 无窗口/状态字段锚点

**问题**：U-P2.3 期望"首次跨越 warn_pct 时触发一次 + 之后不重复触发"。plan §2 Phase 2 base_agent 第 3 项写"首次跨越 `warn_pct` 时调 `ctx.emit(...)`"，未指定**用什么状态字段记录"已触发过"**。Executor 实施时有多种选择（agent 内 bool / cost_tracker 旁路 / ctx 内幂等）。Verifier 没指 mock 边界 → 测试断言"调用次数==1"会受实现选择影响。

**修改要求**：U-P2.3 mock 边界列加一句"若 plan 实现选择把状态放 BaseAgent 实例属性，则同一 agent 实例连续多次调；若放 cost_tracker，则不同 agent 共享同一 tracker"——给 Executor 留实现自由度的同时锁观察点（`ctx.emit` 调用次数）。

---

### P1-5：覆盖率维持论证（§6.2）数学不严

**问题**：§6.2 末尾"新增 ~250 LOC × 平均 90% 覆盖 ≈ +225 covered LOC ... 总均必上不下"——这是把"新增模块自身覆盖率"和"项目总覆盖率"混淆。事实上若现有覆盖率正好 80%，新增 89.6% 的 240 LOC 只会把总均**微微上拉**；若现有 84%，新增 89.6% 仍会拉总均 → 但反向同理：若现有 90%，新增 89.6% 会**下拉**。论证少了"以当前基线 X% 为前提"这一假设。

**修改要求**：§6.2 末尾论证改写为：
- "当前基线覆盖率：Phase 0 commit #1 前 Executor 必须先跑 `pytest tests/unit/ --cov=src --cov-report=term` 记入 `code/phase-0/v1.md` baseline 段；后续每 Phase 用同一命令对比基线 ≥ baseline。"
- 删除 "总均必上不下" 断言式说法；
- 改为门槛验证："cov-fail-under=80 不破 + per-Phase 不低于上一 Phase 基线"。

---

## 3. P2（建议改，不阻塞）

### P2-1：用例汇总数字不一致

§0 "总用例数 单元 32+集成 0+Web 1+手工 E2E 2" / §1 "单元 32" / §11 汇总 "9+12+14 = 35 单元 + 1 Web + 4 E2E + 1 grep static" — **三处不一致**。建议统一以 §11 汇总为准，把 §0、§1 改写为 "单元 35 / 含 P0-2 必删 1 条 U-P2.11 后 34 / Web 1 / 手工 E2E 4 / 静态守护 1"。

### P2-2：U-P1.3 期望 confidence 计算 `min(...) * 0.8 == 0.704` 写死数值

`0.88 * 0.8 = 0.704` 是对的，但写死数值的断言对未来 Plan 修订很脆（plan §3 后续若调"0.8 惩罚系数"成 0.85 会全部失败）。建议改 `pytest.approx(min(confs) * PENALTY)`，其中 `PENALTY` 从 `ThresholdConfig` 或测试常量读。

### P2-3：U-P2.6 与 U-P2.14 重叠

两个测试都验"G5 短路当 status==AWAITING_HUMAN"。U-P2.14 是单元粒度直接验 G5 函数，U-P2.6 是端到端验"BaseAgent raise → transition → G5 不重触发"。可保留，但建议 U-P2.6 重命名为 `test_budget_double_transition_end_to_end_scenario`、U-P2.14 命名为 `test_g5_ceiling_check_skips_when_status_awaiting_human` 区分意图，避免审 code 时被当冗余删错。

### P2-4：手工 E2E 步骤过粗，缺验收复现脚本

E2E-P1.A / E2E-P2.A 等只写"选 forgejo 子集 / 跑 merge --ci"，无具体命令行 / 期望输出 grep 锚点。Phase 1/2 末 GO 条件验收时，team-lead 或 Executor 需要可复现脚本。建议每个 E2E 用例补一段 shell 草稿（command + expected stdout grep）。

### P2-5：§9 "不在本测试方案范围" 列入"集成测试 fixture 选型"——但本会话压根没集成测试

可删，避免混淆 reviewer。

---

## 4. 范围合规性核查

scope.md §1 锁 Phase 0+1+2；v1：
- §2.1 / §2.2 / §2.3 严格三 Phase，无 Phase 3-7 用例渗入 ✅
- §8 锁清单表对 #3 / #5 / Q3 / Q4 标"本会话不测" / "范围外" ✅
- §9 排除清单完整 ✅

**范围合规通过**，无超范围 P0。

---

## 5. Regression 检测

首审无需对照过往 [test] 锁清单（尚无）。

对 [plan] 锁清单，本方案：
- 锁 #1 ConflictAnalysis 字段——U-P1.6 守护 ✅
- 锁 #2 max_cost_usd default=5.0——U-P2.7 守护 ✅
- 锁 #4 G5 + double-transition——U-P2.6 + U-P2.14 守护 ✅
- 锁 #6 split_by_semantic_boundary 来源——U-P1.8 守护 ✅
- 锁 #7 contract version——U-P0.3 ~ U-P0.8 守护 ✅
- 锁 #8 RunBudgetExceeded 签名 + `_current_phase`——U-P0.1 + U-P2.13 守护 ✅
- 锁 #9 U1.A 解耦——U-P1.1 守护 ✅
- 锁 #11 Q1 决策（5.0 + 双路径 + None 兼容）——U-P2.7 / U-P2.8 / U-P2.6 / U-P2.14 守护 ✅

**未违反任何 plan 阶段 [plan] 锁定事实**（但 §8 "可能违反 #1" 那段误判，详见 P0-1）。

---

## 6. 修订总览

| 级别 | 数量 | 概要 |
|---|---|---|
| P0 | 3 | enum 值虚构 / 异常类虚构 / hard cap 锚点缺 |
| P1 | 5 | 冗余 U-P2.11 / U-P0.2 结构断言低价值 / yaml glob 路径锚点 / warn-emit 状态字段锚点 / 覆盖率数学不严 |
| P2 | 5 | 数字不一致 / 写死数值 / 用例重叠命名 / E2E 复现脚本 / 排除清单冗余 |

**结论：NO-GO**。请 Verifier 出 v2 修订，重点处理 3 项 P0（直接阻断 Executor 实施可行性）与 5 项 P1。P2 视情况采纳。

修订时**无需向 gatekeeper-plan 申请解锁**——所有 P0/P1 都在测试方案表达层，未触动 plan 锁清单事实。
