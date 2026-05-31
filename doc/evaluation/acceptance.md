# Acceptance Gate（验收基线）

> 本章给出"通过 / 不通过"的明确数字。每个指标分 **hard**（必须达标，否则禁止对外宣称"已通过评估"）与 **soft**（不达标需人工裁定，记录在 release notes）。
> 阈值随版本演进会收紧，每次修改阈值必须 PR 评审 + 同步更新本文件历史区。

---

## 1. Hard Gates（一票否决）

| 指标 | 阈值 | 数据源 | 备注 |
|---|---|---|---|
| `WMR` 错合率 | **= 0%** | Tier-1 + Tier-2 + Tier-3 | 任何未升级的文件错合即不通过 |
| `SSER` 安全敏感升级率 | **= 100%** | Tier-1（注入安全敏感样本）| 命中 `security_sensitive.patterns` 的文件必须人工 |
| `DCRR` 私有内容留存率 | **= 100%** | Tier-1 take_target 样本 | P1 No-Loss 不可降级 |
| `SRSR` 快照回滚成功率 | **= 100%** | 注入写入失败用例 | P7 快照先于写入 |
| `MMR` 漏合率（行级）| **≤ 2%** Tier-1 / **≤ 5%** Tier-2 | 行级口径 | Tier-2 阈值更宽（真实 PR 复杂度高） |
| `WDR` 误删率 | **≤ 1%** | Tier-2 + Tier-3 | 私有改动 / fork-only 文件 |
| `Recall_Mi` 任一类语义丢失召回 | **≥ 95%** | Tier-3 | 6 类各自分别校验 |
| `RR` 报告完整率 | **= 100%** | 全量 | plan / merge_report / plan_review 三份齐全 |
| `RCR` Rationale 完整率 | **= 100%** | 全量 | rationale ≥ 30 字符 |
| `DUP` 重复顶层符号数 | **= 0** | 全量（merged 产物，确定性）| metrics §8.1；接缝去重 + judge veto 双保险 |
| `AERR` 加性导出保留率 | **= 100%** | 全量（base→fork 新增导出）| metrics §8.2；fork 新增导出不得丢失 |
| `HMR` 幻觉跨模块引用数 | **= 0** | 全量（确定性）| metrics §8.3；命中须 H=true 而非静默提交 |
| `DESC` 未决升级丢弃数 | **= 0** | 全量（确定性）| metrics §8.4；绕过闸口的 escalate 不得静默丢 |

---

## 2. Soft Gates（达不到不一票否决，但需说明）

| 指标 | 阈值 | 数据源 | 处理 |
|---|---|---|---|
| `OA` 总正确率 | **≥ 92%** Tier-1 / **≥ 85%** Tier-2 | 全量 | 退化 ≥ 3% 触发回归排查 |
| `CRA` 冲突解决正确率 | **≥ 88%** | Tier-1 AUTO_RISKY 子集 | |
| `Over-escalation Rate` | **≤ 15%** | Tier-1 | 过高说明系统过度保守，影响用户体验 |
| `JA` Judge 一致率 | **≥ 90%** | 全量 | 与 Ground Truth 比；仅作 Judge 校准用 |
| `DET` 决策一致性 | **≥ 90%** | 同配置 3 次 run | |
| `CPC` 跨模型一致性 | **≥ 85%** | 切换 reviewer/executor provider | |
| `cost_usd_per_run` P95 | **≤ 上一基线 × 1.15** | 全量 | 防止成本悄悄回退 |
| `wall_time_seconds` P95 | **≤ 上一基线 × 1.20** | 全量 | |
| `P95 plan_revision_rounds` | **≤ `max_plan_revision_rounds - 1`** | 全量 | 见 metrics.md §4.4 |
| `BCP` 编译门禁通过率 | **= 100%** | 配置了 build_check 的 run | metrics §8.5；未探测到工具链的目标不计入分母 |

---

## 3. 自学习反馈环激活门（Phase 1 前置）

> 这组门**不**判定一次合并 run 的好坏，而是决定自学习方案
> （`doc/plan/self-learning-system.md`）的反馈环——OPP-5 写回（P1-B）、持久化
> suppress（P1-A）——能否从 opt-in 翻为**默认开启**。设计原则 P2「先度量再激活」：
> 任一反馈环默认开启前，必须先用 `merge eval-memory` 在固定数据集上跑出消融基线
> 证明净收益为正。指标定义见 metrics.md §9。

| 门 | 阈值 | 数据源 | 作用 |
|---|---|---|---|
| `MDL` 记忆决策增益 | **> 0** | `merge eval-memory`（on/off 消融）| 任一反馈环默认开启的**硬前置**；≤ 0 则保持 opt-in |
| `HIR` 有害影响率 | **不高于同数据集 off 基线** | `memory_effectiveness.json` | 上升即说明记忆在污染决策，禁止默认开启 |
| `CRI` 影响后正确率 | **≥ off 基线 overall_correct_rate** | `memory_effectiveness.json` | 被记忆改变的决策不得比无记忆更差 |
| `MCPD` 单决策记忆成本 | **≤ off 基线 × 1.15** | `CostTracker` | 防止记忆注入让 prompt 成本悄悄回退 |

**判定流程**：
1. 同数据集跑 `memory=on`（默认）与 `memory=off`（config `memory.inject_enabled: false`）两 run；
2. `merge eval-memory --on <on_run> --off <off_run>` 产出 `MemoryAblationComparison`；
3. `MDL > 0` 且 `HIR` 不升 → 允许把对应反馈环 default 翻为 `True`，并在本文件 §5 历史区记录基线数；
4. 任一门未过 → 反馈环维持 opt-in，记录原因。

> 这是"默认开启"的闸口，不是合并质量的一票否决；故归为独立章节，与 §1/§2 的合并
> 质量门互不替代。

---

## 4. 报告必备元数据

`eval_acceptance_<version>.json` 必须含：

```jsonc
{
  "version": "<git sha or release tag>",
  "baseline": "<previous release>",
  "evaluated_at": "<ISO timestamp>",
  "datasets": {
    "tier1_lock_sha": "...",
    "tier2_lock_sha": "...",
    "tier3_lock_sha": "..."
  },
  "model_matrix": {
    "planner": "anthropic/claude-opus-4-7",
    "planner_judge": "anthropic/claude-opus-4-7",
    "executor": "openai/gpt-5.4",
    "judge": "anthropic/claude-opus-4-7"
  },
  "hard_gates": [...],
  "soft_gates": [...],
  "verdict": "PASS | FAIL | NEEDS_REVIEW"
}
```

---

## 5. 版本基线历史

| 版本 | 评估时间 | 数据集 lock | 主要结果 | 备注 |
|---|---|---|---|---|
| v0.0.0-template | 2026-05-15 | _未生成_ | _待首次评估_ | 本文件首次落盘 |

每次发布更新该表，至少记录 `OA / MMR / WMR / cost_p95 / wall_time_p95`。

### 5.1 记忆有效性基线（§3 激活门）

> 由 `merge eval-memory` 对同数据集 memory=on/off 两 run 产出（metrics.md §9）。

| 评估时间 | 数据集 | on/off run_id | `MDL` | `HIR`(on) | 激活判定 |
|---|---|---|---|---|---|
| 2026-05-31 | forgejo `test/fork` ← `origin/forgejo`（124 文件，judge 复审 16）| `a0563230` / `81ce3475` | **0.0000** | 0.20 | **不默认开启**（MDL 未 > 0）|

口径与 caveat（务必随基线一并阅读，避免误用）：
- 模型 `deepseek-v4-pro`（temperature：executor/judge=0.1，余默认）；两臂同一 `ablation_decisions.yaml`（plan_review 15×`take_target` + judge_review `accept`），唯一变量为 `memory.inject_enabled`。
- off 臂 `memory_influenced_decisions=0`，证实 `inject_enabled=false` 完全抑制注入（开关有效）。on 臂注入影响 15/16 判决，但 `overall_correct_rate` 与 off 持平（均 81.25%，13/16 pass）→ 本 run 记忆对总体正确率净中性。
- 单 run、单数据集、`judge_verdict=fail`/`partial_failure`（确定性 reverse_impact veto），样本量小（judged=16）；**不足以作收紧/默认开启依据**，仅为首组可搬动基线。需多 run / 多数据集复算（procedure 待补）方能据 §3 翻默认开启。

---

## 6. 阈值修改流程

修改任何阈值必须：

1. 在 PR 中说明 **触发原因**（数据集变更 / 系统能力提升 / 系统能力退化）；
2. 提供当前主分支在新阈值下的实际数据；
3. 至少一名 release owner 评审；
4. 不允许"为了通过本次评估临时放宽阈值"——这种修改必须先合入主分支并跑出基线，再用于评估。

---

## 7. 用户对外承诺模板

通过 acceptance gate 后，可向用户输出如下承诺（示例）：

> CodeMergeSystem v0.7.1 在 Tier-1（48 PR / 312 文件）+ Tier-2（5 个真实长跨度合并）+ Tier-3（72 注入样本）评估集上：
> - 错合率 0%、安全敏感文件 100% 人工、私有内容留存 100%；
> - 漏合率 1.8%（Tier-1 行级），均落在系统主动升级到人工的范围内；
> - M1-M6 语义丢失召回 96.4%；
> - 同配置 3 次 run 决策一致性 91.7%；
> - 与上一版本相比成本 +6%、时延 +8%，均在阈值内。
>
> 评估集 lock SHA、详细报告、与人工黄金合并的逐文件差分均已开放：
> `doc/evaluation/` + `reports/eval_report_v0.7.1.md`。

未通过 acceptance gate 的版本不得使用上述模板。
