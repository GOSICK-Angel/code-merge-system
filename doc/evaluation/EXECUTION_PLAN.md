# 评估系统执行计划（下阶段）

> **状态**：评估框架 Phase 0-9 + F5/F6/F7/F8 修复 + 工具链（sample_import / git_bootstrap）+ 30 真样本数据集 全部已落地（feat/web 分支 19 commits ahead of origin）。
> **本文用途**：登记接下来 3 项可执行任务，包含目标 / 预算 / 前置 / 风险 / 验收，避免会话续接时重新讨论。
> **不修改**：`README.md / metrics.md / dataset.md / procedure.md / acceptance.md`（仅追加本文 + 视进度更新 IMPLEMENTATION_REPORT_PARTIAL.md）。

---

## 0. 当前 baseline

| 项 | 状态 |
|---|---|
| `scripts/eval/` 框架 | Phase 0-9 全闭环 + F5/F6/F7/F8 修复 |
| `tests/eval/` | 272/272 PASSED，cov 94% |
| 真实数据集（Tier-1）| 30 个 dify-plugins 干净样本（6 C + 24 B）|
| Synthetic fixture | `tests/eval/fixtures/reference_samples/`（hello.py 合成）|
| 单次 1-sample 真跑（t1-0001 旧的）| `merge` 二进制端到端通过（match=EXACT, verdict=PASS）|
| CI `eval-tier1` job | 已添加 (`workflow_dispatch` + `continue-on-error`)，未首次手动触发 |
| DET / CPC 一致性 | 未跑（需 ≥ 2 runs） |

---

## 1. 任务 P1：30 sample 全量真跑（Tier-1 acceptance baseline）

**目标**：用真 `merge` 二进制跑完 30 sample，产出第一份完整的 `eval_report.md` + `eval_acceptance.json`，把 acceptance gates 由"理论"变成"有数"。

**前置**：
- `.merge/.env` 含有效 `ANTHROPIC_API_KEY` + `OPENAI_API_KEY`（已确认 `/Users/angel/AI/project/dify-official-plugins/.merge/.env` 可用）
- 本地 `git_bootstrap` + `sample_import` 工具齐（已落地）
- 30 个 sample 入仓 `tests/eval/datasets/tier1/samples/`（已落地）

**步骤**（每个 sample 独立可重试）：

1. `python -m scripts.eval.git_bootstrap --sample tests/eval/datasets/tier1/samples/t1-NNNN --out /tmp/eval-runs/repo-NNNN`
2. `cp /Users/angel/AI/project/dify-official-plugins/.merge/{.env,config.yaml} /tmp/eval-runs/repo-NNNN/.merge/`
3. `cd /tmp/eval-runs/repo-NNNN && set -a && source .merge/.env && set +a && python -m src.cli.main merge upstream --no-web --ci`
4. 把 `merge_report_<rid>.json / .md / plan_review_<rid>.md` + working_tree 汇总到 `/tmp/eval-runs/runs/t1-NNNN/`，并合成最小 `run_meta.json`
5. 跑 `diff_against_golden / summarize / gate` 三件套

**估算**：
| 维度 | 数值 |
|---|---|
| 单 sample wall-clock | 1-3 min（取决于 actionable 文件数 + 模型反应）|
| 单 sample tokens | 5-30k（C 类型偏多）|
| 单 sample 成本（claude-opus-4-6）| $0.10-0.50 |
| 全 30 sample 总成本 | **$5-15**（取决于 cache 命中率 + C 占比）|
| 全 30 sample wall-clock | ~60-90 分钟（串行） |

**风险**：
- C 类型的 6 个 sample 可能触发多轮 plan revision（成本 / 时长上不封顶）
- 部分 sample 可能击中 max_files_per_run = 50 上限（当前 config，若需放宽 → 改 config）
- API 限流：claude opus 上调 RPM 限制可能在中段触发 → 失败重试机制由 `BaseAgent._call_llm_with_retry` 兜底
- t1-0001..t1-0006 (C 类) 命中真实语义冲突 → 系统可能产 ESCALATE_HUMAN，导致 verdict 非 PASS（这本身是评估目标）

**验收**：
- 全 30 sample 产 `merge_report_<rid>.json`（即使 verdict ≠ PASS 也算成功跑完）
- `acceptance.json` 写出，含 OA / WMR / MMR / DCRR / RR / RCR / CRA / OverEscalationRate 全部有数
- 单条 `tests/eval/datasets/tier1/results/<run_date>/` 持久化（建议新加 `.gitignore` 排除）
- 产出 `doc/evaluation/IMPLEMENTATION_REPORT_PARTIAL.md` 后续追加 §"Tier-1 首份 acceptance" 段（含真实数值）

**Stretch goal**：
- 若 verdict=FAIL，定位 fail gate 并产分类失败清单（system 真错 vs sample 数据问题 vs threshold 设定问题）
- 6 个 C 类 sample 的 system_decision 单独分析（系统对真实语义冲突的 take_target / fork_keep / escalate 比例）

---

## 2. 任务 P2：DET 一致性测试

**目标**：用同一 sample 多 seed 跑 3 遍，验证 `consistency.py --metric DET` ≥ 0.9 acceptance gate（metrics.md §6.1）。

**前置**：P1 完成（DET 需要先确认 1-run 正常，且需要多 sample 才能拉开有意义数值）。

**实施**：
- 挑 5 个 sample（推荐：1 C × 3 difficulty + 2 B）→ 控制成本 $1-2
- 每 sample 3 次 run，`--seed 1/2/3`
- 比对 strategy / target_risk_level 跨 run 一致性
- `python -m scripts.eval.consistency --runs runs/run-1 runs/run-2 runs/run-3 --metric DET --output det.json`

**估算**：
| 维度 | 数值 |
|---|---|
| Sample × runs | 5 × 3 = 15 runs |
| 总成本 | $1-2 |
| wall-clock | 30-45 min（串行） |

**风险**：
- claude opus 默认 temperature 不为零（config.yaml `judge.temperature=0.1`），可能造成 5% 抖动 → DET 真实 ≥ 0.9 是可达的 stretch
- 多次跑同 sample 会击中 prompt cache → 实际成本估算偏低

**验收**：
- DET ≥ 0.9（acceptance.md §2 soft gate）
- 不一致样本清单可列出（plan 决策 3：DET 输出含 `inconsistent` 数组）
- 如果 DET < 0.9，分析是 strategy 抖动还是 target_risk_level 抖动；后者更严重

---

## 3. 任务 P3：CI eval-tier1 首次触发

**目标**：在 GitHub Actions 上手动触发 `eval-tier1` workflow，验证 CI 环境能跑通 lock --verify / fork-name-check / pytest tests/eval/ → 兼容 30-sample 数据集体量。

**前置**：P1 不一定完成（CI 现阶段不跑真 merge，只跑 framework 测试 + lock-verify）；30 sample 入仓即可触发。

**实施**：
- push 当前 feat/web 分支到 origin（19 commits）
- GitHub Actions → "eval-tier1" workflow → "Run workflow" → branch=feat/web
- 看 5 个 step 全过：`ruff` / `ruff format` / `mypy scripts/eval` / `pytest tests/eval/unit --cov-fail-under=80` / `python -m scripts.eval.lock --verify` / `python -m scripts.eval._fork_name_check scripts/eval tests/eval`

**估算**：
| 维度 | 数值 |
|---|---|
| 成本 | 仅 GitHub Actions 时间（≈ 5 min） |
| wall-clock | 单次 5-10 min |

**风险**：
- CI 环境差异：mypy / ruff 版本可能与本地不一致 → fail 后查 `actions/setup-python` 版本
- pytest 时长：30 sample lock 读取多了 → `TestUnitSuiteRuntime` 25s 上限可能临界（本地实测 ~14s，CI 环境通常 + 30-50%）
- 真实 merge 不在该 job（避免 secrets 入 CI）→ 仅 framework / schema sanity

**验收**：
- workflow run 显示 success，所有 step exit 0
- 现有 PR (`test` job) 不受影响（eval-tier1 是独立 workflow_dispatch，不阻塞）

---

## 4. 推荐执行顺序

```
P3 (CI 触发 - 几乎零成本) → P1 (30 sample 真跑 - 主要里程碑) → P2 (DET - 收尾)
```

理由：
- P3 不依赖任何外部资源，可以马上做，验证 CI 路径
- P1 是评估系统的"真实测试"，结果驱动后续所有决策（threshold 微调 / 数据集增补方向 / 框架 bug 二轮）
- P2 是 acceptance.md soft gate 兜底，等 P1 数据稳了再做更省钱

---

## 5. 推迟项 / 已知 limitation

| 项 | 推迟到 | 原因 |
|---|---|---|
| Tier-2 历史回放 | Tier-1 稳定后 | dataset.md §3 设计，需要 ≥10 个跨 25 commits 的真实合并 |
| Tier-3 M-注入对抗集补全 | Tier-1 稳定后 | M1-M6 每类 ≥ 5 个 → 30 sample，需要专门注入工具 |
| SRSR 真实路径 | plan v3 | 需要 `MergeState.snapshot_rollback_events` 字段（acceptance_thresholds.yaml 当前 `[FOLLOW-UP — auto-SKIP]`）|
| WDR hard gate 重启用 | Tier-2 后 | 当前 yaml 故意不含（[code-phase-4] MISS_FORK 简化）|
| tree-sitter 真实 AST | optional dependency | `_ast_equiv.py` 当前 fallback-bytes，启用需 `pip install tree-sitter*` |
| `--baseline` 数值 delta | 累积 ≥ 2 次完整 run | 需要 baseline schema 规范化 + 跨 run diff 工具 |
| 多文件 sample 聚合 | Phase 5 优化 | 当前 `_diff_one_sample` 只取首个 decision record |
| `_decision_to_system_decision` 字段统一 | E2E 真跑校准 | 当前双字段名兜底（`decision`/`strategy`、`target_risk_level`/`risk`）|
| `eval-tier1` nightly cron | 数据集 ≥ 50 + 预算确认 | 当前 `workflow_dispatch` only |

---

## 6. 文档维护

- 完成 P1 后：把 acceptance 数值追加进 `IMPLEMENTATION_REPORT_PARTIAL.md` §"首次真实评估"
- 完成 P2 后：DET 数值同上
- 完成 P3 后：CI workflow URL 留存到 README
- 如果发现新 F-class 框架缺陷：本文 §5 新增条目，并新建 fix branch

---

## 7. 已通过 acceptance 阈值清单（供参考）

来自 `tests/eval/manifests/acceptance_thresholds.yaml`：

**Hard gates (13)**：WMR ≤ 0 / SSER == 1.0 / DCRR == 1.0 / SRSR auto-SKIP / MMR ≤ 0.02 / Recall_M1-M6 ≥ 0.95 / RR == 1.0 / RCR == 1.0

**Soft gates (9)**：OA ≥ 0.95 / CRA ≥ 0.95 / OverEscalationRate ≤ 0.05 / JA ≥ 0.9 / DET ≥ 0.9 / CPC ≥ 0.85 / cost_p95 relative 1.15× / wall_time_p95 1.20× / plan_revision_rounds_p95 1.0×

P1 跑完后会得到上述 gates 在 30-sample 真实环境的初始数值。
