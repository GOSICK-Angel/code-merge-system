# 指标体系

> 所有指标都按"统一差分协议"对照 **Ground Truth**（人工黄金合并 / 历史已合 commit / 对抗注入答案）计算，避免使用 Judge 自评作为终端正确性来源。
> 计量单位：除特别说明，分母为"评估集中实际进入合并流程的文件数（不含 EXCLUDED）"。

---

## 1. 通用约定

### 1.1 单位与符号

| 符号 | 含义 |
|---|---|
| `F_total` | 评估集中进入合并流程的文件总数 |
| `F_eval` | 进入差分评估的文件数（剔除 EXCLUDED / BINARY 不可比文件） |
| `D_sys[f]` | 系统对文件 `f` 的最终内容（merged tree 中的文件） |
| `D_gold[f]` | Ground Truth 对文件 `f` 的最终内容（人工黄金合并） |
| `Decision[f]` | 系统对文件 `f` 的 `MergeDecision`（含 strategy、rationale） |
| `H[f]` | 文件是否需要人工决策（系统是否升级到 `AWAITING_HUMAN`） |

### 1.2 文件级"对不对"的判定

对每个 `f ∈ F_eval`，计算 `match(f) ∈ {EXACT, SEMANTIC, MISMATCH}`：

- **EXACT**：`D_sys[f] == D_gold[f]`（去除空白后字节相等）。
- **SEMANTIC**：AST 结构等价（仅注释 / 空白差异），用 `tree-sitter` 解析；非代码文件退化为 normalize（去 BOM、统一行尾）后字节相等。
- **MISMATCH**：以上两者都不成立。

`MISMATCH` 文件再细分：

| 细分标签 | 触发条件 |
|---|---|
| `MISS_UPSTREAM` | `D_gold` 含上游变更，`D_sys` 缺失（漏合） |
| `MISS_FORK` | `D_gold` 保留 fork 私有改动，`D_sys` 丢失（误删 / 覆盖） |
| `WRONG_MERGE` | 双方都改动，系统给出第三种错误结果 |
| `EXTRA_NOISE` | `D_sys` 引入 `D_gold` 不存在的内容 |

---

## 2. 正确性指标（Correctness）

### 2.1 漏合率（Miss-Merge Rate, MMR）

> "应该合过来的上游变更，没合过来"。

```
MMR = | { f ∈ F_eval : MISMATCH(f) ∧ label = MISS_UPSTREAM } | / F_eval
```

补充行级版本（更敏感）：

```
MMR_lines = sum(missed_lines_in_f) / sum(upstream_changed_lines_in_f)
```

### 2.2 错合率（Wrong-Merge Rate, WMR）

> "合了，但合错了"——系统把 `H[f] = false`（即未升级）的文件合成了与 Ground Truth 不一致且不是简单漏合的内容。

```
WMR = | { f ∈ F_eval : H[f]=false ∧ label ∈ {WRONG_MERGE, EXTRA_NOISE} } | / | { f : H[f]=false } |
```

WMR 是最关键的安全指标——**人工要决策的文件不算系统错合**（系统已主动放权），但**未升级的文件错了，全部计入**。Acceptance gate 要求 WMR = 0%。

### 2.3 误删率（Wrong-Deletion Rate, WDR）

> 针对 fork 私有改动 / fork-only 文件被错误丢弃。

```
WDR = | { f : f ∈ FORK_ONLY ∪ FORK_MODIFIED ∧ MISMATCH(f) ∧ label = MISS_FORK } | / | FORK_ONLY ∪ FORK_MODIFIED |
```

`FORK_ONLY` 与 `FORK_MODIFIED` 来自 `ForkDivergence` 计算结果（见 `src/tools/fork_divergence.py`）。

### 2.4 冲突解决正确率（Conflict Resolution Accuracy, CRA）

> 仅针对 risk 为 `AUTO_RISKY` 且经过 ConflictAnalyst + Executor 的文件。

```
CRA = | { f ∈ AUTO_RISKY_PROCESSED : match(f) ∈ {EXACT, SEMANTIC} } | / | AUTO_RISKY_PROCESSED |
```

### 2.5 总正确率（Overall Accuracy, OA）

```
OA = | { f ∈ F_eval : match(f) ∈ {EXACT, SEMANTIC} } | / F_eval
```

---

## 3. 安全性指标（Safety）

### 3.1 语义丢失召回率（Semantic-Loss Recall, M1-M6）

针对 Tier-3 对抗集，每个样本预先标注属于 M1-M6 中的哪一类（参见 `doc/architecture.md` 六类丢失模式）。系统是否在 Plan / ConflictAnalysis / Judge 阶段任意一处把该文件标为需要人工或给出正确语义合并：

```
Recall_Mi = | { 注入样本 : 系统正确识别为 Mi 或在该文件触发 H=true } | / | 注入样本 of Mi |
Recall_overall = sum(Recall_Mi) / 6
```

### 3.2 安全敏感文件人工率（Security-Sensitive Escalation Rate, SSER）

> `security_sensitive.patterns` 命中的文件**必须 100% 进入人工**。

```
SSER = | { f ∈ SECURITY_SENSITIVE_HITS : H[f]=true } | / | SECURITY_SENSITIVE_HITS |
```

Acceptance: SSER = 100%。

### 3.3 快照可回滚率（Snapshot Rollback Success Rate, SRSR）

针对人工注入的"写入失败"用例（mock `patch_applier` 抛错），验证：

```
SRSR = | 触发回滚的用例中，工作树恢复到写入前快照 SHA 的次数 | / | 触发回滚用例总数 |
```

Acceptance: SRSR = 100%。

### 3.4 私有改动留存率（Discarded Content Retention Rate, DCRR）

> Plan Review 报告 / `FileDecisionRecord.discarded_content` 中是否留存了被丢弃的私有内容（即使决策是 take_target）。

```
DCRR = | { f : 决策为 take_target 且 fork 侧有私有改动 ∧ discarded_content 非空 } | / | 同前条件分母 |
```

Acceptance: DCRR = 100%（P1 不丢失原则）。

---

## 4. 过程可信指标（Process Trust）

### 4.1 升级率（Escalation Rate, ER）

```
ER = | { f : H[f]=true } | / F_eval
```

ER 本身没有"越低越好"的方向——**关键看升级是否合理**。引入两个配套指标：

- **Over-escalation Rate**：`H[f]=true` 但 Ground Truth 显示是 trivial take_target → 越低越好。
- **Under-escalation Rate**：`H[f]=false` 且 `match(f) = MISMATCH` → 越低越好（与 WMR 重合一部分）。

### 4.2 Judge 一致率（Judge Agreement, JA）

> Judge verdict 是否与 Ground Truth 一致。这是检验"Judge 能不能信"的指标，本身不能替代正确性。

```
JA = | { f : Judge verdict ∈ {pass, escalate} 与 Ground Truth match 状态一致 } | / F_eval
```

低 JA + 高 OA → Judge 偏严，可放宽；
高 JA + 低 OA → Judge 与系统共谋（reviewer-executor 偏差），需切换不同 provider 复测（见架构 §3.P5）。

### 4.3 Plan Dispute 命中率（Plan Dispute Precision / Recall）

```
Precision = | { 触发 dispute 且事后修订更接近 Ground Truth } | / | dispute 总数 |
Recall    = | { 触发 dispute 且事后修订更接近 Ground Truth } | / | 应当 dispute 的样本数 |
```

应当 dispute = Ground Truth 显示原 plan 在该文件给出的策略错误。

### 4.4 PlannerJudge 收敛轮数

```
P50 / P95 of plan_revision_rounds across all runs
```

Acceptance: P95 ≤ `max_plan_revision_rounds - 1`，即至少有一轮余量。超阈值需复盘 prompt。

---

## 5. 可解释性指标（Explainability）

### 5.1 Rationale 完整率

```
RCR = | { f : decision.rationale 非空且 ≥ 30 字符 } | / F_total
```

Acceptance: RCR = 100%。

### 5.2 Trace 可回放率

> 抽样 10% 决策，使用 `trace_logger` 重放 prompt → 能否得到一致的 response 哈希。

```
TRR = 重放一致 / 抽样总数
```

Acceptance: TRR ≥ 95%（允许 5% 模型 nondeterminism；非零温度时报告中需注明）。

### 5.3 报告完整率

`MERGE_PLAN_<run_id>.md` / `merge_report.md` / `plan_review.md` 三份产物全部存在且非空：

```
RR = 三份齐全的 run 数 / 总 run 数
```

Acceptance: RR = 100%。

---

## 6. 运行稳健指标（Operational）

### 6.1 决策一致性（Determinism, DET）

> 同一评估集 / 配置跑 N 次（默认 N=3），文件级最终决策是否相同。

```
DET = | { f : N 次跑都得到相同的 (strategy, target_risk_level) } | / F_eval
```

Acceptance: DET ≥ 90%（在 temperature=0 / cache 命中下应更高）。

### 6.2 跨模型一致性（Cross-Provider Consistency, CPC）

> 切换 Reviewer/Executor 模型组合（如 Anthropic↔OpenAI 调换），再跑一次：

```
CPC = | { f : 两种模型组合得到等价决策 } | / F_eval
```

Acceptance: CPC ≥ 85%。低于阈值意味着结论强依赖具体模型，不能宣称"系统稳健"。

### 6.3 端到端成本与时延

| 指标 | 单位 | 统计 |
|---|---|---|
| `cost_usd_per_run` | USD | P50 / P95 |
| `tokens_in / tokens_out` | tokens | P50 / P95 |
| `wall_time_seconds` | 秒 | P50 / P95 |
| `human_minutes_per_run` | 分钟 | P50 / P95（人工决策实际耗时） |

来源：`CostTracker` + Web UI 计时器 + `checkpoint.json` 时间戳。Acceptance 用同评估集上"上一基线版本 ±15%"作为门槛，避免回退。

### 6.4 失败模式分布

按 `error_classifier` 八类错误统计本次评估中触发次数与平均处理耗时；用于判断稳定性回退。

---

## 7. 指标之间的取舍关系（必须同时考量）

| 现象 | 含义 | 应做的事 |
|---|---|---|
| OA 高 + ER 极低 | 系统几乎不升级，但凑巧对了 | 检查 Tier-3 上 WMR 是否仍为 0；不为 0 即说明运气好 |
| OA 高 + ER 极高 | 把所有事都丢给人 | 用 Over-escalation Rate 验证；过高需收紧规则 |
| WMR=0 + MMR 高 | 系统宁可不合也不错合 | 看 MMR 是否落在可接受人工接管范围 |
| JA 高 + OA 低 | Judge 与 Executor 共谋 | 强制切换 reviewer-executor provider 复测 |
| DET 低 | 结论靠运气 | 复查温度参数 / 模型路由 / cache 设置 |

任何单一指标都可能被 game。Acceptance 用复合阈值（见 acceptance.md）。

---

## 8. 确定性产物校验门槛（LLM-free，0527 批次落地）

这一组指标全部由确定性静态检查产出，不依赖 LLM、不依赖 Ground Truth，可在每次
run 末尾直接计算。它们对应 0527 修复批次，目的是"产物可证伪即不放行"——把曾经
"无法编译却 COMPLETED"的失败模式转成显式信号。

> 信号通路：report 阶段的 finding 写入 `state.errors` → `ci_reporter.build_ci_summary`
> 把 COMPLETED 降为 `partial_failure`（退出码 `EXIT_PARTIAL_FAILURE=30`）；judge 阶段
> 的确定性 veto 直接把 verdict 拉到 FAIL（`parse_judge_verdict` 由 issue 计数决定，
> 忽略 LLM）。

### 8.1 重复顶层符号数（Duplicate Top-level Symbols, DUP）

```
DUP = Σ_f  | 文件 f 中声明 >1 次的顶层 value 符号 |
```

数据源：`duplicate_symbol_check.find_duplicate_symbols` 跑遍 merged 产物。三道防线
共同保证 DUP=0：executor `remove_duplicate_top_level_symbols` 接缝去重（方案3.1）、
judge 确定性 veto（方案5）、report 校验聚合（方案2）。Acceptance: **DUP = 0**。

### 8.2 加性导出保留率（Additive Export Retention, AERR）

```
AERR = | { fork 新增的顶层导出符号 s : s 仍存在于 merged 产物 } | / | fork 新增导出符号总数 |
```

数据源：`feature_preservation.added_exported_symbols` / `missing_symbols`（base→fork
diff 得到 fork 新增导出，断言其在 merged 中存活）。覆盖"regexes.ts 丢 cidrv6Mapped 仍
PASS"的假 PASS。Acceptance: **AERR = 100%**。

### 8.3 幻觉跨模块引用数（Hallucinated Member Accesses, HMR）

```
HMR = Σ_f  | merged 中 base.member 引用：两源都无该引用且 base. 在某源出现 |
```

数据源：`hallucinated_symbol_guard.find_invented_member_accesses`（方案3.2）。命中即
executor 升级人工。覆盖"捏造 core._isoWeek"。Acceptance: **HMR = 0**（命中必须以 H=true
体现，而非静默提交）。

### 8.4 未决升级零丢弃（Dropped Escalations, DESC）

```
DESC = | { f : 决策仍 ESCALATE_HUMAN 且 source≠HUMAN 且 f 从未进人工闸口 } |
```

数据源：report 阶段 `_assert_no_dropped_escalations`（方案6 part2）。覆盖"内部
escalate(0.0) 文件绕过闸口静默丢失"。Acceptance: **DESC = 0**（用户在闸口主动跳过的
不计入）。

### 8.5 编译门禁通过率（Build-Check Pass Rate, BCP）

```
BCP = | 配置了 build_check 且退出码 0 的 run | / | 配置了 build_check 的 run |
```

数据源：judge 阶段 `_run_build_check`（command 由 setup 自动探测填充，方案1）。非零退出
把 Judge PASS 降级 FAIL+veto。Acceptance（Soft）: **BCP = 100%**（仅统计已配置 command 的
run；未探测到工具链的目标不计入分母）。
