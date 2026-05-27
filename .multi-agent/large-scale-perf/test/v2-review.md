# Test Plan v2 — Gatekeeper Review

> gatekeeper-test 复审，针对 `.multi-agent/large-scale-perf/test/v2.md`。
> 上游契约同 v1-review。审查日期：2026-05-18。**结论：GO**。

---

## 0. 已通过事实（基线）

[plan] 锁清单 12 条全部未被违反；v1 提的 3 P0 / 5 P1 / 5 P2 全部处理到位（详见 §1）。

代码现状再次核验：
- doc §5.1.1 line 167 实测 `if len(chunks) > 8 or total_content_bytes > 10 * 1024 * 1024` —— P0-3 锚点真实存在。
- doc §5.1.1 line 169-173 hard cap output：`MergeDecision.ESCALATE_HUMAN` / `confidence=0.3` / rationale `"file too large for safe chunked analysis"` —— U-P1.4 期望与 doc 伪码完全对齐。
- doc §5.1.1 line 197-199 slow path：`_strategy_precedence(chunks)  # ESCALATE > SEMANTIC > TAKE_*` + `confidence=min_conf * 0.8` —— U-P1.3 输入 `[TAKE_TARGET, SEMANTIC_MERGE, TAKE_CURRENT]` 期望 `SEMANTIC_MERGE`（无 ESCALATE 时取 SEMANTIC）+ `pytest.approx(min(confs) * 0.8)` 完全对齐。
- doc §5.1.1 line 189 fast path rationale `"chunked analysis: {n} chunks unanimous on {strategy}"` —— U-P1.2 期望 rationale 含 `"unanimous"` 子串对齐。
- doc §5.1.1 line 202 slow path rationale `"[chunked {n} parts, disagreement] ..."` —— U-P1.3 期望含 `"disagreement"` 对齐。
- doc §5.1.3 测试表用 `take_target` / `semantic_merge` 字面量与 `MergeDecision` enum 值一致。
- `src/agents/base_agent.py:79-95` 异常类层级（`CircuitBreakerOpen` / `AgentError` / `AgentExhaustedError`） + `src/llm/error_classifier.py:16,29` `ErrorCategory` —— U-P1.9 用 `httpx.ReadTimeout` 走真实分类路径，正确。

---

## 1. v1 反馈处理验收

| 反馈 | v2 处理位置 | 验收结论 |
|---|---|---|
| **P0-1** enum 值虚构 | §2.2.1 U-P1.2/1.3/1.4/1.5 + §8 末段删 | ✅ 全改 `MergeDecision.{TAKE_TARGET,SEMANTIC_MERGE,TAKE_CURRENT,ESCALATE_HUMAN}`；§8 误判段删 |
| **P0-2** 异常类虚构 | §2.2.1 U-P1.9 | ✅ 改 `httpx.ReadTimeout` 走 error_classifier → AgentExhaustedError |
| **P0-3** hard cap 锚点缺 | §2.2.1 U-P1.4 | ✅ 锚点锁 doc §5.1.1 line 167（已核实存在）；输入 9 chunk；期望 confidence=0.3、rationale 含 "too large for safe chunked analysis"；明确常量在源码 reducer 内非 config 字段 |
| **P1-1** U-P2.11 冗余 + shell 不合规 | §2.3.1 / §3.3 G2-2 | ✅ 删除；G2-2 改挂现有 test_agent_contracts.py regression |
| **P1-2** U-P0.2 结构断言低价值 | §2.1.1 U-P0.1 | ✅ 合并并加 (c)(d) sub-assert（issubclass Exception/not SystemExit） |
| **P1-3** yaml glob 路径锚点 | §2.1.1 U-P0.5/0.6 | ✅ 加 `pathlib.Path(__file__).resolve().parents[2]` + `assert len == 7` sanity gate |
| **P1-4** warn-emit 状态字段 | §2.3.1 U-P2.3 mock 边界列 | ✅ 明确观察点 = `ctx.emit` 调用次数；显式声明"留 Executor 实现自由度" |
| **P1-5** 覆盖率数学不严 | §6.2 | ✅ 基线对比 + 0.5pct 漂移容差 + per-Phase 不卡死；删除纯数学推断 |
| **P2-1** 数字不一致 | §0 / §1 / §11 | ⚠️ **未改干净**（详见 P2-A 残留） |
| **P2-2** 写死数值 | §2.2.1 U-P1.3 | ✅ 改 `pytest.approx(min(confs) * PENALTY_FACTOR)` |
| **P2-3** U-P2.6 / U-P2.13 命名 | §2.3.1 | ✅ 命名按"端到端 vs 单元"区分 |
| **P2-4** E2E 复现脚本 | §2.2.3 / §2.3.3 | ✅ 每 E2E 加 shell 草稿 + grep 锚点 |
| **P2-5** §9 误列项 | §9 | ✅ 删除 "集成测试 fixture 选型" 一行 |

**13/13 全部完成**（P2-1 数字残留留 §2 P2-A 提示，不阻塞）。

---

## 2. 残留 P2（建议改，不阻塞 GO）

### P2-A：用例汇总数字仍不一致（P2-1 未改干净）

实算：Phase 0 单元 8 + Phase 1 单元 12 + Phase 2 单元 13 = **33** 单元；加 1 Web + 4 手工 E2E = **38**。

v2 实际写法：
- §0 line 31："单元 34 + Web 1 + 手工 E2E 4 = 39"（**双错**：34 应 33；39 应 38）
- §1 line 39："34"（应 33）+ line 42 比例 "~87%" 也跟着错
- §4 line 240："失败 11 / 总 39 = 28%"（39 应 38；分子 11 实际 10——LLM 失败、None 输入、超长、双 transition、缺 version、反向 import、序列化 + budget 6 点中失败侧 = 实算需重梳）
- §11 line 399："38 测试项（34 单元 + 1 Web + 4 E2E）"（38 对，但 34 应 33）
- §12 摘要 line 15："39 测试项"（应 38）

**建议**：Verifier 在下次会话开始时（或 Executor 实施前）顺手统一为 33+1+4=38，所有出现"34/39"处一并修正。不阻塞本会话 GO（属元数据笔误，不影响 Executor 实施可行性）。

### P2-B：U-P0.5 与 U-P0.6 可参数化合并

两条用例都验"7 yaml + version=1"，差异仅在视角（raw dict vs pydantic 反序列化）。可参数化为 `@pytest.mark.parametrize("loader", ["raw_yaml", "agent_contract"])` 单条用例。

**建议**：保留独立两条也可（视角不同 + 失败定位更精准），Executor 实施时若觉重复可自行参数化合并，不强求。

### P2-C：U-P1.9 是 spec-by-test，明确声明

U-P1.9 期望"失败 chunk → strategy=ESCALATE_HUMAN"，v2 自注 "doc §5.1.1 伪码未显式，本测试锁 ESCALATE 是最保守安全默认"。这相当于测试方案为 plan 未规定的行为补一条 spec。

**建议**：在 v2 §10.1 加一句"U-P1.9 是 spec-by-test，若 Executor 实施期发现 doc/plan 有更合理失败容错路径，走 scope.md §3.1 '细节自纠'上报，Verifier 修订测试期望"。本会话不强制。

---

## 3. 范围合规

v2 §2.1/§2.2/§2.3 严格 Phase 0+1+2，无 Phase 3-7 用例渗入；§8 锁清单表对 #3/#5 标"本会话不测/范围外"；§9 排除清单完整。

**范围合规 ✅**。

---

## 4. Regression 检测

首审通过的 [plan] 锁清单事实 12 条全部守护（§8 表交叉验证）。v2 §8 末段误判（关于 "escalate" 解锁）已整段删除，无残留违反。

**无 plan 阶段 regression ✅**。

---

## 5. 结论

**GO**。3 项 P2 残留为元数据笔误 / 优化建议，不阻塞 Executor 实施。

执行动作：
1. copy `test/v2.md` → `test/FINAL.md`
2. 追加 5 条 [test] 标签新事实到 `locks/approved-facts.md`
3. SendMessage verifier + team-lead
