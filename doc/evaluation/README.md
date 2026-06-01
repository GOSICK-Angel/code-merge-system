# CodeMergeSystem 评估方案

> **版本**：2026-05-15
> **目标读者**：希望客观评估"是否可以信任本系统的合并产出"的使用方、SRE、Release Owner。
> **配套源码**：`src/agents/`、`src/core/`、`src/tools/`；评估脚本（待建）位于 `scripts/eval/`。

---

## 0. 一句话定位

本评估方案不是再让 LLM Judge 自评一遍 verdict，而是**用专家黄金合并（Human Golden Merge）作为 Ground Truth**，按统一的差分协议度量"系统合并产物 vs. 黄金合并"的偏差，以**漏合率 / 错合率 / 语义丢失率**等可复现的数字回答两个问题：

1. **能不能信？**——产物正确性是否达到放行阈值（acceptance gate）。
2. **该信多少？**——升级率、可解释性、成本稳定性等过程指标，是否说明系统对自身能力边界有自知之明。

未通过 acceptance gate 的版本，禁止对外宣称"可信"。

---

## 1. 信任的五个维度

| 维度 | 关注点 | 主要指标 | 详见 |
|---|---|---|---|
| **正确性（Correctness）** | 该合的合了没？合的对不对？ | 漏合率、错合率、误删率、冲突解决正确率 | [metrics.md §2](metrics.md#2-正确性指标correctness) |
| **安全性（Safety）** | 没有偷偷丢私有改动 / 安全敏感文件没误判 | 语义丢失率（M1-M6）、安全敏感文件人工率、快照可回滚率 | [metrics.md §3](metrics.md#3-安全性指标safety) |
| **过程可信（Process Trust）** | 不确定的事会不会上报，而不是硬猜 | 升级率、Plan Dispute 命中率、Judge 与 Ground Truth 一致率 | [metrics.md §4](metrics.md#4-过程可信指标process-trust) |
| **可解释性（Explainability）** | 每个决策都能复盘 | rationale 完整率、`discarded_content` 留存率、Trace 可回放率 | [metrics.md §5](metrics.md#5-可解释性指标explainability) |
| **运行稳健（Operational）** | 重复跑、不同模型跑结果稳定；成本可控 | 决策一致性、Token / 美元成本、人工耗时、端到端时延 | [metrics.md §6](metrics.md#6-运行稳健指标operational) |

只有正确性达标还不够——一个"全部直接 take_target、覆盖率 100% 但漏合 50%"的系统也能在某些指标上看起来不错。**必须五个维度同时通过 acceptance.md 阈值**才算放行。

---

## 2. 三层评估集（Dataset Tiers）

| 层 | 规模 | 来源 | 用途 | 详见 |
|---|---|---|---|---|
| **Tier-1 微基准（Micro Bench）** | 30–60 PR / 200–500 文件 | 手工标注的小 PR，覆盖 ABCDE × M1-M6 × 3 种 risk | CI 门禁、回归 | [dataset.md §2](dataset.md#2-tier-1-微基准) |
| **Tier-2 历史回放（Replay）** | 5–10 个真实长跨度合并 | 已经被人工合过的 upstream→fork 历史，git diff 即 oracle | 端到端验收 | [dataset.md §3](dataset.md#3-tier-2-历史回放) |
| **Tier-3 对抗集（Adversarial）** | 60–120 注入样本 | 在干净基线上人工注入 6 类语义丢失陷阱 | 安全性专项 | [dataset.md §4](dataset.md#4-tier-3-对抗集) |

Tier-1 跑得快、可天天跑；Tier-2 接近真实，但贵；Tier-3 用来回答"系统真的能识别 M1-M6 吗"。三层缺一不可。

---

## 3. 评估流程与产出

完整流程见 [procedure.md](procedure.md)，简版：

```
1. 准备评估集    → scripts/eval/prepare.py --tier {1|2|3}
2. 运行系统       → merge <branch> --no-web --ci      （JSON summary 落盘）
3. 计算 Ground   → scripts/eval/diff_against_golden.py
   Truth 差分
4. 汇总指标       → scripts/eval/summarize.py --output report.md
5. 校验 acceptance→ scripts/eval/gate.py --baseline <prev_version>
```

每个评估周期产出三份产物（任何一份缺失都不算完成）：

- `eval_report_<version>.md` —— 全量指标 + 失败案例列表
- `eval_diff_<version>.json` —— 与 Ground Truth 的逐文件差分，可回放
- `eval_acceptance_<version>.json` —— 各指标 vs. 阈值的 pass/fail 结果

---

## 4. 用户应该看什么才相信结果

按可信度从高到低：

1. **Acceptance Gate 全绿**（`eval_acceptance_*.json` 中所有项 `pass=true`）。
2. **Tier-2 历史回放**：在历史已知正确的合并上，本系统的产物与人工合并 diff 行数小于阈值（默认 ≤ 1% 文件数 / ≤ 0.5% 行数差异）。
3. **Tier-3 对抗集**：M1-M6 召回率 ≥ 95%，错合率 0%。
4. **过程证据**：每个决策有 rationale；`discarded_content` 留存；Trace 可逐步回放。
5. **跨模型 / 跨 run 一致性**：同一评估集跑 3 次、换两个模型组合，决策一致性 ≥ 90%。

**只看"verdict=pass"或"merge 成功率 99%"是不够的**——前者来自 Judge 自评，后者把"丢了一半东西的成功合并"也算成功。

---

## 5. 文档索引

| 文件 | 内容 |
|---|---|
| [metrics.md](metrics.md) | 所有指标的精确定义、计算公式、数据来源 |
| [dataset.md](dataset.md) | 三层评估集如何构造、如何维护、如何防止过拟合 |
| [procedure.md](procedure.md) | 端到端评估流程、复现命令、报告产物规范 |
| [acceptance.md](acceptance.md) | 每个指标的合格 / 不合格阈值与版本基线管理 |
| [golden.md](golden.md) | `merge optimize-prompts --golden` 的逐 gate 决策 oracle（LLM-判断密集 golden 集）如何从样本 `meta.yaml` 派生 |

---

## 6. 与现有文档的关系

- 本目录是"系统对外可信度"的正式评估规范；
- `doc/test-report/` 是历史 run 报告（描述某次具体合并发生了什么），不是评估结论；
- `doc/risk-levels.md` 描述系统**内部**的 risk 计算口径，本评估用其作为分层抽样维度，不替代 Ground Truth；
- 任何对评估口径的修改必须更新 acceptance.md 中的版本基线，避免"为了通过指标改阈值"。
