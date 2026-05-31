# gatekeeper-test 审查报告（test v3）

> 审查时间：2026-05-18
> 被审 artifact：`.multi-agent/large-scale-perf/test/v3.md`
> 审查范围：Phase 2 §2.3.5 新增 3 用例（U-P2.14~16）+ Phase 3 §2.4 全部 8 用例 + Phase 4 §2.5 全部 5 单元 + 1 E2E
> Phase 0/1/2 §2.3.1~§2.3.4（v2 保留部分）按 scope.md §6 视为 regression 守护，**不重复审**。

---

## 结论

**要求修改**（1 处 P1 必修文字错误 + 1 处 P1 数字一致性 + 2 处 P2 不阻塞）。整体结构合规、锁清单交叉映射完整、失败:正常达标、范围严格 = Phase 0/1/2/3/4 不越界，**新增 16 用例（3+8+5）的设计实质上可放行**，仅需对错别字 + GO 条件数字小幅订正后视作通过。

---

## 已通过事实（详见 locks/approved-facts.md，本轮拟新增 4 条）

> 28 条历史 lock 不重列。本轮拟追加 [test] 标签事实：

- **A.** `MergeState.thresholds: ThresholdConfig` 字段语义锁定为"运行态快照"——orchestrator init phase 复制独立实例，后续修改 `state.config.thresholds` 不影响 `state.thresholds`（U-P2.15 (c)(d) 守护，对应 scope.md §6 路径 A 第 2 步）。
- **B.** `assert_disjoint_file_shards(shards) -> None` helper 异常类型锁定为 `FileShardOverlap(ValueError)`（非 `SystemExit`/`AssertionError`/`RuntimeError`）；`str(exc)` 必须包含重叠 key 字面值（U-P3.2 守护，对应 plan §2 Phase 3 交付物第 1/2 测试）。
- **C.** lock #5 6 接入点的测试断言形态锁定为 `MagicMock(wraps=原)` + `call_count >= 1` + 不替换实现体（U-P3.3~U-P3.8 mock 边界，对应 v3 §5.4 "不 mock helper 实现体"）。
- **D.** `enable_working_branch` 三态测试入口锁定在 `tests/unit/test_working_branch.py`（U-P4.1 重命名+断言迁移；U-P4.2 维持；U-P4.3 新增 method），且 lock #3 line 72-83 锚点区间在 Phase 4 实施期间**不得被删除**（v3 §10.2 第 4 项纪律守护）。

> 残留 P2（不阻塞 GO）：见下"P2 分级"。

---

## P0 / P1 / P2 分级问题

### P0（阻塞 GO）

**无。**

新增 16 用例无任一违反 28 条历史 lock：

- lock #5 6 接入点 1:1 映射 U-P3.3~U-P3.8（v3 §2.4 矩阵每行点名锚点 file:line 与 lock 完全一致）✓
- lock #3 三态完整覆盖 U-P4.1/2/3，且 line 72-83 行号锚点保留 ✓
- lock #11 Q3 决策清单 3 行 → v3 §3.5 G4-1 + §2.5.1 三用例 ✓
- lock #14/15/16 reducer 行为未被新用例反向期望（Phase 3/4 新用例不触碰 reducer） ✓
- lock #17 anti-pattern #2 沿用现有 regression，未在 v3 重写（§3.3 G2-2 / §3.4 G3-2 显式挂现有 regression）✓
- lock #26 明确归档不修（§9 排除清单第 4 项）✓
- lock #27 路径 A 三步 1:1 映射 U-P2.14/15/16 ✓
- 范围严格 = Phase 0/1/2/3/4（§9 排除清单显式列 Phase 5/6/7） ✓

scope.md §6 用户答复"路径 A"实施细节 5 项与 v3 §2.3.5 三用例对齐：
- (1) MergeState 加 thresholds 字段 → U-P2.14 (d) `MergeState.model_fields` 含 `"thresholds"` ✓
- (2) orchestrator init phase 从 `state.config.thresholds` 复制 → U-P2.15 (a)(b)(c)(d) ✓
- (3) `analyze_file` 入参从 `view.thresholds` 取 → U-P2.16 (a)(b) ✓
- (4) 同步修复 HANDOFF §4.3 P2 "`analyze_file` 新参数 `run()` 未驱动" → U-P2.16 (a) 显式注释"不再走 Phase 1 残留的 mock + setattr 合成默认 0.85 路径" ✓
- (5) 1-2 个新单测验证字段存在 + 默认值 + restricted_view 可读 → U-P2.14 (a)(b)(c)(d) 一次性覆盖三个子断言 ✓

### P1（必改回审或建议改后视为通过）

**P1-1（必修，文字错误）** — v3 §10.2 第 3 项错别字。

- **位置**：`/Users/angel/AI/personal/code-merge-system/.multi-agent/large-scale-perf/test/v3.md:558` 第 3 项："`assert_disjoke_file_shards` call 发生在对应 file:line 附近"
- **应为**：`assert_disjoint_file_shards`
- **影响**：Executor 按字面执行 §10.2 实施纪律时会找不到 helper，导致 U-P3.3~U-P3.8 mock patch target 错误。U-P3.1/U-P3.2 矩阵中已用正确拼写（line 226/227），但纪律段引用错。
- **处置**：替换为正确拼写。

**P1-2（建议修，GO 条件数字与 plan 表述差异自洽，不阻塞但易引混淆）** — v3 §2.4 G3-1 与 plan §Phase 3 GO 数字差异。

- **位置**：v3 §2.4 行 215 G3-1 "8 个新单测全绿（plan §2 Phase 3 列出 6 + 实际 8 — 见下注）"；与 plan/FINAL.md:185 GO 条件 "4 个新单测全绿" 表面冲突。
- **背景**：plan §Phase 3 交付物列 6 测试（doc §5.5.2 4 + P0-2 新增 2），plan §Phase 3 GO 条件文本 line 185 仍写"4 个"（plan v2 残留文字偏差，未与交付物清单同步更新）。v3 扩到 8（6 接入点 + 2 helper 基础），plan 数字 4→8 的口径增长需 Verifier 显式注释。
- **现状**：v3 §2.4 开头 "**接入点数与单测数**" 段已注释说明（"plan §2 Phase 3 列出 6 + 实际 8"），未引入 plan 偏离（仍在 lock #5 6 接入点范围内，仅加 U-P3.1/U-P3.2 helper 兜底）。
- **处置**：v3 自洽可接受；建议在 §2.4 G3-1 行后追加一句注释："（plan §Phase 3 GO 写 4，系交付物清单 6 同源偏差；v3 在 6 基础上加 2 helper = 8，未越 plan 范围）"。Executor 读 plan 与 v3 时不困惑。**不强求改可放行**。

**P1-3（建议修，GO 条件数字与 plan 表述差异自洽，不阻塞）** — v3 §2.5 G4-1 与 plan §Phase 4 GO 数字差异。

- **位置**：v3 §2.5 行 253 G4-1 "3 个新单测全绿 + 2 个清单改动测试"；plan/FINAL.md:211 GO "3 个新单测全绿"。
- **背景**：plan §Phase 4 仅列名 3 plan 新增；lock #3 + scope.md §6 路径 A 的 backward compat 第 3 行（U-P4.3 显式 False）是 P1-1 修订增量，plan §3.3 Q3 决策清单第 3 行已显式列出，**未越 plan 范围**。
- **处置**：v3 §2.5 行 258 已自洽注释（"实际单测数 = 3 plan 列名 + 2 现有改动 = 5"），可放行。**不强求改**。

### P2（不阻塞，记入未来会话）

**P2-1** — U-P3.7 接入参数形态不确定性。v3 §6.4 已显式记入"风险点"且给出"细节自纠"上报路径；本会话不修。

**P2-2** — U-P4.5 wizard default 取值路径不确定。v3 §6.4 已显式记入；本会话不修。

**P2-3** — U-P4.4 mock fork repo 用 `git.Repo.create_head` 拦截 vs subprocess `git branch` 拦截选择留给 Executor 实施。v3 §5.2 / §5.4 / §10.2 已分别约束，不阻塞。

---

## v3 关键设计核查（regression net）

| 维度 | 期望 | 实际 v3 | 判定 |
|---|---|---|---|
| Phase 0/1/2 §2.3.1~§2.3.4 v2 用例编号 / 名称 / 锚点 | 完全不动 | §2.1.1 / §2.2.1 / §2.3.1~§2.3.4 全部简表呈现，编号 U-P0.1~U-P0.8 / U-P1.1~U-P1.12 / U-P2.1~U-P2.13 / U-W2.1 / E2E-P1.A/B / E2E-P2.A/B 与 v2 FINAL 一致 | ✓ |
| lock #5 6 接入点 1:1 用例 | 6 个 | U-P3.3 (executor:829) / U-P3.4 (planner:645) / U-P3.5 (judge:167) / U-P3.6 (judge:1473) / U-P3.7 (conflict_analyst chunked line 277-280) / U-P3.8 (conflict_analyst:81) | ✓ |
| lock #3 三态 | True / 显式 True / 显式 False | U-P4.1 / U-P4.2 / U-P4.3 | ✓ |
| lock #27 路径 A 3 步 | 字段 / orchestrator copy / run drive | U-P2.14 / U-P2.15 / U-P2.16 | ✓ |
| Phase 3 GO 必含 overlap 失败 | 是 | U-P3.2 + U-P3.8 (d) 双重 | ✓ |
| Phase 4 GO 必含 backward compat 显式 False | 是 | U-P4.3 + E2E-P4.A | ✓ |
| 范围不含 Phase 5/6/7 | 是 | §9 排除清单 5 项明确 | ✓ |
| 失败 : 正常比例 | ≥ 1:3 (~25%) | 14/55 = 25.5% | ✓ |
| 三要素（输入 / 期望 / 断言锚点）完整 | 是 | 矩阵每行 6 列完整 | ✓ |
| mock 边界明示（不 mock 字段本身 / 不 mock helper 实现体 / 不 mock restricted_view） | 是 | §5.4 v3 扩展 4 条边界约束 + 矩阵 mock 边界列逐用例显式 | ✓ |
| §8 锁清单交叉验证 | lock #1~#28 全覆盖 | §8 矩阵每行有对应用例编号 / 元事实 / 范围外标注 | ✓ |

---

## SendMessage 摘要（回 verifier）

```
v3 测试方案审查：要求修改（1 P1 必修文字错误 + 2 P1 自洽数字建议改）
P0 0 / P1 3（1 必修 + 2 建议）/ P2 3（不阻塞）

P1-1（必修）：§10.2 第 3 项错别字 `assert_disjoke_file_shards` → `assert_disjoint_file_shards`
P1-2（建议）：§2.4 G3-1 "8 单测" 与 plan §Phase 3 GO "4 单测" 差异，加一句注释即可
P1-3（建议）：§2.5 G4-1 "3+2" 与 plan §Phase 4 GO "3" 差异，§2.5 行 258 已自洽，可不改

修订后送审 v4。
review 全文：/Users/angel/AI/personal/code-merge-system/.multi-agent/large-scale-perf/test/v3-review.md
```
