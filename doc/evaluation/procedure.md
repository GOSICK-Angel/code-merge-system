# 评估流程与实施

> 本章给出"从准备到出报告"的端到端流程、命令、产物规范，保证任何工程师在 30 分钟内复现出与发布版本一致的评估结论。

---

## 1. 评估时机

| 触发条件 | 跑哪些 Tier | 谁来跑 |
|---|---|---|
| 每个 PR | Tier-1 抽样（10 文件，1–3 分钟）| CI |
| Nightly | Tier-1 全量 + Tier-3 全量 | CI |
| Release 候选 | Tier-1 + Tier-2 + Tier-3 全量 | Release Owner 手动 |
| Prompt / Agent 行为变更 | 同上 + 与基线版本 A/B | 改动者 |

只有 **Release 候选** 跑完三层并通过 acceptance.md 阈值，才能对外宣称"已通过评估"。

---

## 2. 命令清单

### 2.1 准备评估集

```bash
# 校验 lock 一致性
python scripts/eval/lock.py --verify

# 渲染 tier 输入到隔离工作目录
python scripts/eval/prepare.py --tier 1 --out /tmp/eval-tier1
```

### 2.2 运行系统

每个样本独立 run，产物写入 `<work>/runs/<sample_id>/`：

```bash
python scripts/eval/run.py \
    --tier 1 \
    --workdir /tmp/eval-tier1 \
    --concurrency 4 \
    --merge-bin "$(which merge)" \
    --merge-args "--no-web --ci"
```

`scripts/eval/run.py` 必须：

- 注入隔离的 `.merge/.env`（dummy 或 release 专用 key）；
- 禁止访问真实的 Memory / 历史 checkpoint；
- 写入 `run_meta.json`（耗时、cost、模型版本、git sha）。

### 2.3 计算 Ground Truth 差分

```bash
python scripts/eval/diff_against_golden.py \
    --runs /tmp/eval-tier1/runs \
    --datasets tests/eval/datasets/tier1 \
    --output /tmp/eval-tier1/diff.json
```

输出 `diff.json` 是后续所有指标的唯一数据源（避免在多个脚本里重复算同一件事）。

### 2.4 汇总指标

```bash
python scripts/eval/summarize.py \
    --diff /tmp/eval-tier1/diff.json \
    --runs /tmp/eval-tier1/runs \
    --output reports/eval_report_$(date +%Y%m%d_%H%M).md
```

### 2.5 校验 acceptance gate

```bash
python scripts/eval/gate.py \
    --report reports/eval_report_*.md \
    --acceptance doc/evaluation/acceptance.md \
    --baseline reports/eval_report_<prev_release>.md \
    --output reports/eval_acceptance_*.json
```

`gate.py` 退出码：

- `0` —— 全部 pass，可放行；
- `1` —— 至少一个 hard gate fail；
- `2` —— soft gate 退化但未到红线，需人工裁定。

---

## 3. 报告产物规范

### 3.1 `eval_report_<version>.md` 必含章节

```
1. 元信息（git sha / 模型组合 / 时间 / 评估集 lock）
2. 总体指标摘要（OA / WMR / MMR / WDR / SSER / 主观 trust 评分）
3. 分 tier 结果
4. 失败案例清单（每个样本 1 行：sample_id | label | strategy | rationale_excerpt）
5. 与上一基线对比表（每个指标 ±%）
6. 异常 / 已知问题 / 缓解措施
```

### 3.2 `eval_diff_<version>.json` schema（节选）

```jsonc
{
  "tier": 1,
  "samples": [
    {
      "sample_id": "t1-0001",
      "category": "C",
      "loss_class": "M3",
      "expected_human": false,
      "system_decision": {
        "strategy": "SEMANTIC_MERGE",
        "risk": "AUTO_RISKY",
        "human": false
      },
      "match": "MISMATCH",
      "label": "WRONG_MERGE",
      "missed_lines": 12,
      "extra_lines": 3
    }
  ]
}
```

### 3.3 `eval_acceptance_<version>.json` schema

```jsonc
{
  "version": "v0.7.1",
  "baseline": "v0.7.0",
  "gates": [
    { "id": "WMR", "value": 0.0, "threshold": 0.0, "pass": true, "kind": "hard" },
    { "id": "MMR", "value": 0.018, "threshold": 0.02, "pass": true, "kind": "hard" },
    { "id": "SSER", "value": 1.0, "threshold": 1.0, "pass": true, "kind": "hard" },
    { "id": "DET", "value": 0.91, "threshold": 0.90, "pass": true, "kind": "soft" }
  ],
  "verdict": "PASS"
}
```

---

## 4. 跨模型 / 多次 run（DET / CPC）

```bash
# DET：同配置跑 3 次
for i in 1 2 3; do
  python scripts/eval/run.py --tier 1 --workdir /tmp/eval-det/run-$i --seed $i
done
python scripts/eval/consistency.py --runs /tmp/eval-det --metric DET

# CPC：切换 reviewer/executor provider
python scripts/eval/run.py --tier 1 --workdir /tmp/eval-cpc/anthropic --override-providers anthropic
python scripts/eval/run.py --tier 1 --workdir /tmp/eval-cpc/openai    --override-providers openai
python scripts/eval/consistency.py --runs /tmp/eval-cpc --metric CPC
```

---

## 5. 人工评估补丁（Optional but Recommended）

部分 Tier-2 / Tier-3 样本中"哪个 strategy 更好"难以纯算法判定，建议 release 前邀请 1–2 名熟悉目标库的工程师做盲评：

- 评估者只看 system_decision + golden，不看哪个是哪个；
- 标注 `prefer ∈ {system, golden, tie}`；
- 汇总到 `human_pref_*.csv`，反映 Ground Truth 本身的争议带（如果 system 经常被偏好，说明 oracle 也不绝对，可作为 acceptance 上的容差来源）。

---

## 6. 常见陷阱

1. **泄漏**：CI 跑评估时不要把样本仓库的真实路径挂到 Memory；任何 `.merge/memory/` 必须显式 wipe。
2. **缓存命中混淆**：Anthropic 缓存命中会显著降低 token 费用，但也意味着结论可能依赖前一次 run；DET 跑前先 `--no-cache` 至少一次。
3. **温度漂移**：评估期间务必锁 temperature（默认 0），并在报告头部写明。
4. **打补丁后的 run 不算**：如果跑完发现 bug 改了源码再重跑，旧 run 必须丢弃，不能挑选有利的那次。
5. **Judge 自评 vs Ground Truth**：报告必须并列 JA 与 OA，不允许只展示 JA。
