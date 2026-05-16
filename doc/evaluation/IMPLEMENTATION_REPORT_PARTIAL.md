# 评估方案落地实施报告

> **状态**：**Phase 0-9 全部完成**（保留 PARTIAL 文件名以满足 scope.md §5 约定；
>   实质上是完整报告，无遗漏 Phase）
> **会话日期**：2026-05-15 起 → 2026-05-16 完成
> **分支**：`feat/web`（未 push）

---

## 1. 一句话结论

`doc/evaluation/` 评估方案已在 `scripts/eval/` + `tests/eval/` 完整落地。从
dataset lock → 子进程隔离 → 产物比对 → 指标聚合 → acceptance gate →
DET/CPC 一致性 → e2e 串链 → CI 集成全链路 **242 个测试 100% 通过**
（cov 94.50%，mypy strict 33 文件零错），且**不阻塞现有 PR**（仅追加
CI step + 一个 manual-only job）。

---

## 2. 落地交付（按 Phase 分组）

### 2.1 git commit 链

| Phase | commit | 内容 |
|---|---|---|
| 0 | `5658e77` | scripts/eval 骨架 + 11 个 pydantic v2 模型 + `_fork_name_check` |
| 1 | `1101b2a` | `lock.py`（manifest sha256 三子命令）+ tier1/tier3 reference samples |
| 2 | `8cb3f84` | `prepare.py`（展开 sample 到 workdir）+ `_ground_truth.py` |
| 3 | `c3e2c6c` | `run.py`（驱动 merge 子进程：env / cwd / memory 三层隔离 + concurrency） |
| 4 | `6d3871e` | `_ast_equiv.py`（tree-sitter optional + bytes fallback）+ `diff_against_golden.py` |
| 5 | `bf4cfda` | `summarize.py` + `_report_render.py` + jinja2 模板（procedure.md §3.1 六章节） |
| 6 | `000d646` | `gate.py` + `acceptance_thresholds.yaml`（plan-amend kind/multiplier 双语义） |
| 7 | `35cbf69` | `consistency.py`（DET/CPC）+ Phase 6 P2-1..P2-4 carry-forward CLOSED |
| 8 | `2854a82` | `tests/eval/integration/test_e2e_tier1.py`（T8-E1/E2/E3 串链） |
| 9 | `7687ba7` | `.github/workflows/ci.yml` 追加 5 eval steps + manual `eval-tier1` job + 9 个 meta 测试 |

### 2.2 文件清单

```
scripts/eval/                  # 14 文件，~1400 stmts，cov 94.50%
├── __init__.py
├── _common.py                 # eval_subprocess_env (MERGE_DEV pop), atomic IO
├── _schemas.py                # pydantic v2: 14 frozen 模型
├── _fork_name_check.py        # 拒绝 cvte/dify/insforge 进入 scripts/tests
├── _ast_equiv.py              # EXACT/SEMANTIC/MISMATCH 判定（tree-sitter optional）
├── _ground_truth.py           # load_sample / load_golden_tree
├── _report_render.py          # jinja2 渲染入口（StrictUndefined）
├── _templates/
│   └── eval_report.md.j2      # procedure.md §3.1 六章节模板
├── lock.py                    # CLI: --verify / --update / --update-acceptance-sync
├── prepare.py                 # CLI: --tier --out
├── run.py                     # CLI: --tier --workdir --concurrency --merge-bin --merge-args --seed --use-real-keys
├── diff_against_golden.py     # CLI: --runs --datasets --output diff.json --tier
├── summarize.py               # CLI: --diff --runs --output report.md --baseline
├── gate.py                    # CLI: --report --acceptance --baseline --output --version
└── consistency.py             # CLI: --runs <r1> <r2> ... --metric {DET,CPC} --output

tests/eval/
├── __init__.py
├── conftest.py                # autouse: strip MERGE_DEV; tmp_workdir; eval_subprocess_env_factory
├── datasets/
│   ├── tier1/samples/t1-0001/         # 完整 5 文件 sample
│   ├── tier2/                         # 占位（Tier-2 follow-up）
│   └── tier3/adversarial/t3-m3-0001/  # M3 类（接口变更未同步调用方）
├── manifests/
│   ├── tier{1,2,3}.lock.json
│   └── acceptance_thresholds.yaml     # hard 13 + soft 9（含 3 relative gates）
├── fixtures/
│   ├── dummy_run/runs/t1-0001/        # FIXTURE-命名 merge 产物
│   └── fake_merge_bin/fake_merge.sh   # FAKE_FIXTURE_DIR/FAKE_SAMPLE_ID/FAKE_MERGED_TREE_DIR/...
├── unit/                              # 233 用例，14 测试模块
│   ├── test_common.py / test_schemas.py / test_fork_name_check.py
│   ├── test_lock.py / test_prepare.py / test_ground_truth.py
│   ├── test_run.py / test_ast_equiv.py
│   ├── test_diff_against_golden.py / test_summarize.py / test_report_render.py
│   ├── test_gate.py / test_consistency.py
│   └── test_ci_workflow_meta.py
└── integration/                       # 3 用例，e2e 串链
    ├── __init__.py
    └── test_e2e_tier1.py
```

### 2.3 acceptance gate 能力 vs. `acceptance.md` 对照

| Hard Gate | 计算实现 | yaml 阈值 | 状态 |
|---|---|---|---|
| WMR | summarize._compute_metrics + diff label WRONG_MERGE | 0 | PASS-able |
| SSER | summarize._compute_sser（真实 metrics.md §3.2 公式） | 1.0 | PASS-able |
| DCRR | summarize._compute_metrics（discarded_content_present 检查） | 1.0 | PASS-able |
| MMR | summarize._compute_metrics + diff label MISS_UPSTREAM | ≤ 0.02 | PASS-able |
| Recall_M1..M6 | summarize._compute_metrics + diff loss_class | ≥ 0.95 each | PASS-able (Tier-3 数据齐全后) |
| RR | summarize._compute_rr（runs/<id> 三件产物 size>0） | 1.0 | PASS-able |
| RCR | rationale_length ≥ 30 | 1.0 | PASS-able |
| **WDR** | 简化为 0（MISS_FORK 未独立区分） | yaml 故意缺；TestCommittedYaml 守护 | DEFERRED → Tier-2 |
| **SRSR** | summarize 输出 `"N/A (follow-up)"` | hard gate 隐式 SKIP（[FOLLOW-UP — auto-SKIP] marker） | DEFERRED → plan v3 |

| Soft Gate | kind | yaml | 状态 |
|---|---|---|---|
| OA / CRA / OverEscalationRate / JA / DET / CPC | absolute | 6 项 | PASS-able |
| cost_usd_per_run_p95 | relative (multiplier 1.15) | 1 项 | 缺 baseline 时 SKIP |
| wall_time_seconds_p95 | relative (multiplier 1.20) | 1 项 | 缺 baseline 时 SKIP |
| plan_revision_rounds_p95 | relative (multiplier 1.0) | 1 项 | 缺 baseline 时 SKIP |

退出码三态（[plan-amend] team-lead 方案 C）：
- `0` = 全 hard + 全 soft 通过
- `2` = soft 退化（absolute < threshold 或 relative > baseline × multiplier）
- `1` = hard fail（严格优先 soft fail，T6-G11 守护）

### 2.4 测试基线

```
pytest tests/eval/ —— 242 passed in 2.96s
  Phase 0 (54) + Phase 1 (20) + Phase 2 (27) + Phase 3 (20) + Phase 4 (35)
  + Phase 5 (29) + Phase 6 (31) + Phase 6 P2-2 补 (2) + Phase 7 (12)
  + Phase 8 (3) + Phase 9 (9)
pytest --cov=scripts/eval --cov-fail-under=80 —— 94.50% PASS
  每模块 ≥ 90%：consistency 96% / diff 96% / gate 95% / summarize 94%
  / lock 94% / run 93% / prepare 91% / 余模块 ≥ 94%
mypy scripts/eval tests/eval —— 33 source files 0 error (strict)
ruff check / format —— clean
python -m scripts.eval._fork_name_check scripts/eval tests/eval —— exit 0
python -m scripts.eval.lock --verify (local + CI=true) —— exit 0 双模式
```

---

## 3. 用户复现命令（生产模式 / 真实 sample）

> 假设：repo 已 `pip install -e ".[dev]"`；评估 sample 已置于
> `<repo>/.merge/eval_datasets/tier1/samples/<id>/`（或 tests/eval 自带 fixture）。

```bash
# 0. 校验 dataset / acceptance yaml sha 同步（CI 严格模式同时跑）
python -m scripts.eval.lock --verify

# 1. 展开 sample 到 workdir（含 working_tree/ + golden_tree/）
python -m scripts.eval.prepare --tier 1 --out /tmp/eval-1

# 2. 跑 merge 子进程（env / cwd / memory 三层隔离 + concurrency=1）
python -m scripts.eval.run \
    --tier 1 --workdir /tmp/eval-1 --concurrency 1 \
    --merge-bin /usr/local/bin/merge --merge-args "--no-web --ci" \
    --seed 1 --use-real-keys

# 3. 比对 working_tree vs golden_tree，产出 per-sample diff
python -m scripts.eval.diff_against_golden \
    --runs /tmp/eval-1/runs \
    --datasets tests/eval/datasets/tier1/samples \
    --output /tmp/eval-1/diff.json --tier 1

# 4. 聚合 diff + run_meta 出 markdown 报告
python -m scripts.eval.summarize \
    --diff /tmp/eval-1/diff.json \
    --runs /tmp/eval-1/runs \
    --output /tmp/eval-1/eval_report_v1.md

# 5. 应用 acceptance threshold，产出 eval_acceptance.json + verdict
python -m scripts.eval.gate \
    --report /tmp/eval-1/eval_report_v1.md \
    --acceptance tests/eval/manifests/acceptance_thresholds.yaml \
    --output /tmp/eval-1/eval_acceptance_v1.json
echo "exit code = $?"   # 0=PASS / 1=hard fail / 2=soft fail

# 6. （可选）DET / CPC：跑 N 次后比对
for i in 1 2 3; do
  python -m scripts.eval.run \
      --tier 1 --workdir /tmp/eval-1/run-$i --seed $i \
      --merge-bin /usr/local/bin/merge --merge-args "--no-web --ci"
done
python -m scripts.eval.consistency \
    --runs /tmp/eval-1/run-1/runs /tmp/eval-1/run-2/runs /tmp/eval-1/run-3/runs \
    --metric DET --output /tmp/eval-1/consistency.json
```

---

## 4. 已知 limitation 与后续工程建议

### 4.1 留作 plan v3 follow-up

| 项 | 当前状态 | 阻塞 | 建议 |
|---|---|---|---|
| **SRSR 数据流** | summarize 输出 `"N/A (follow-up)"`；gate yaml 隐式 SKIP | `MergeState.snapshot_rollback_events` 字段未在 src/models/ 落地 | plan v3 引入字段后扩 `DiffEntry.snapshot_rollback_attempted/_succeeded` + summarize 真实公式 + 移除 yaml `[FOLLOW-UP — auto-SKIP]` marker |
| **MISS_FORK 完整区分** | 简化为 MISS_UPSTREAM | 需 fork.patch + base tree 三方 oracle | Tier-2 真实历史回放工作；现 yaml 故意缺 WDR hard gate |
| **WDR hard gate 重启用** | yaml 注释为后续 | 依赖 MISS_FORK 区分 | Tier-2 后启用 |
| **tree-sitter 真实 AST** | optional dep；当前走 fallback-bytes | tree-sitter 子语言 grammar 安装与维护成本 | 后续把 `pyproject.toml [optional-dependencies.ast]` 落地 + `_ast_equiv` engine 切换 + T4 系列补 tree-sitter 路径用例 |
| **`--baseline` 数值 delta** | summarize / gate 已支持读 baseline_metrics；只比较绝对值 | 基线 schema 未标准化 | 引入 `BaselineReport` schema + summarize §5 表新增 delta 列 |
| **多文件 sample 聚合** | `_diff_one_sample` 仅取第一个 decision record | Tier-1 1-sample fixture 不触发 | Tier-1 抽样矩阵补齐后 refactor 为 list-aggregation |
| **`_decision_to_system_decision` 双字段名兜底** | `decision ↔ strategy`、`target_risk_level ↔ risk` | 真实 `MergeState` dump 字段名未观测 | 接上真实 merge CLI 一次跑通后决定是否删 fallback |
| **`_build_context` git_sha vs model_matrix 多值策略** | 不一致（一个 mixed，一个取第一个） | 1-sample fixture 不触发 | 多 run 数据出现时统一为 "取第一个" |
| **`eval-tier1` nightly cron** | yaml 注释 placeholder | 真实 evaluation 预算 / 通知未敲定 | 稳定 Tier-1 评估后 uncomment `schedule.cron: "0 18 * * *"` |

### 4.2 范围外（scope.md §6 锁定，不在本次 deliverable）

- Tier-2 真实历史合并构造（仅占位 dir）
- Tier-3 6 类全集（仅 M3 一个示范）
- 实际 release-grade evaluation 跑（fake CLI；无真实 API key 路径未端到端验证）
- CI PR 阻塞（按 facts.md §11 故意 non-blocking）

---

## 5. 关键架构决策（落地依据）

- **不动 `src/`**：评估器是 `merge` CLI 的外部观察者；任何对 src 的改动会污染目标系统的合并能力，违反"通用 code-merge-system"原则（CLAUDE.md §Project Generality）。
- **不动 `doc/evaluation/{README,metrics,dataset,procedure,acceptance}.md`**：方案文档已锁版本；如有差异以本报告 §4 follow-up 描述。
- **lock 文件落 commit**：`tier{1,2,3}.lock.json` + `acceptance_thresholds.yaml` 锚定 git；CI 开箱跑 `lock --verify` 即可校验完整性，无需第一步先 `--update`。
- **fake merge CLI**：唯一实现 `tests/eval/fixtures/fake_merge_bin/fake_merge.sh`；Phase 8 在原脚本上**追加**`FAKE_MERGED_TREE_DIR` env（非破坏）让 e2e 跑通 `verdict=PASS`。
- **三层隔离**：[code-phase-3] `eval_subprocess_env` 是唯一 env 工厂；强制 `env.pop("MERGE_DEV")` + `HOME=<workdir>/home` + cwd 独立 + memory.db spawn-前断言 + concurrency=1 是发布数据的硬约束。
- **gate kind 双语义**：[plan-amend] / [test-amend] 方案 C；soft gate 区分 `absolute` / `relative`，baseline 可选；relative 缺 baseline 全部 SKIP（不影响 verdict）。
- **hard fail 优先 soft fail**：T6-G11 守护；任何 hard fail → exit 1 即使 soft 也 fail。

---

## 6. 锁清单引用

详见 `.multi-agent/eval-impl/locks/approved-facts.md`，含：

- `[plan]` 8 条（产物名 / 模式开关 / memory 形态 / `--ci` 输出边界 / CI 现状 / 不存在的 flag / M1-M6 detector 映射）
- `[plan-amend]` 7 条（gate kind/multiplier 双语义）
- `[code-phase-0..7]` 各 5-9 条（每个 Phase 通过审查后固化的实施事实）
- `[test]` / `[test-amend]` 用例总数 / Phase 6 用例语义 / fake_merge.sh 命名约定 / 金字塔分类

后续接手任何 eval-impl 后续工作（plan v3 / Tier-2 / tree-sitter）的工程师，**第一动作**是 Read 这份锁清单重建跨阶段事实基线。
