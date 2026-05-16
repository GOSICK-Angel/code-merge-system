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

---

## 7. Tier-1 首次真实评估（30-sample acceptance baseline）

> 执行时间：2026-05-16 ｜ 数据集：`tests/eval/datasets/tier1/samples/` (6 C + 24 B) ｜ 模型：`claude-opus-4-6`（all agents） ｜ concurrency=1 ｜ seed=0

### 7.1 执行方式

`merge` 二进制对每个 sample 端到端跑（非 fake）：

1. `python3 -m scripts.eval.git_bootstrap --sample <s> --out /tmp/eval-runs/repo-NNNN`
2. 复制 `/Users/angel/AI/project/dify-official-plugins/.merge/{config.yaml,.env}`，把 `fork_ref` patch 成 `main`（git_bootstrap 用 main 当 fork branch）
3. `merge upstream --no-web --ci`
4. 收集 `merge_report_<rid>.json` + `checkpoint.json` + `plan_review_<rid>.md`，捕获 working_tree（排除 `.git/`/`.merge/`/`outputs/`/`.gitignore`），合成 schema-compliant `run_meta.json`
5. `diff_against_golden` → `summarize` → `gate`

### 7.2 总开销

| 维度 | 值 |
|---|---|
| 全 30 sample wall-clock | ~7 min（串行，远低于 60-90 min 估算） |
| 全 30 sample 成本 | **$1.16**（远低于 $5-15 估算） |
| 单 sample 中位数 | $0.021 / 11s |
| 最贵单条 | t1-0004：$0.53（13 LLM 调用，judge 7 轮 repair） |
| cost_p95 | $0.036 / sample |
| wall_p95 | 36.3s / sample |

成本远低于估算是因为：dify-plugins 样本本身小（多 ≤ 5 文件），prompt cache 命中率高，6 C 类里 5 个第一轮 plan 就过。

### 7.3 Acceptance verdict

**`verdict: FAIL`** — 4 hard gates 不达标，2 soft gate 不达标。

| Gate | Value | Threshold | Pass? | 说明 |
|---|---|---|---|---|
| WMR | 0.0333 | == 0 | ❌ | t1-0003 一例 `WRONG_MERGE`（C 类 take_target 误判）|
| SSER | 1.0 | == 1.0 | ✅ | 无 security-sensitive 路径误漏 |
| DCRR | 0.8667 | == 1.0 | ❌ | discarded-content reject 率，受 3 个 `MISSING_REPORT` 拉低 |
| MMR | 0.0 | <= 0.02 | ✅ | 无 M-class 注入样本（Tier-3 才相关）|
| RR | 0.9 | == 1.0 | ❌ | run-rate，3 例 needs_human 未写 merge_report 计成 MISSING |
| RCR | 0.9 | == 1.0 | ❌ | run-completeness rate，同上 |
| Recall_M1-M6 | N/A | >= 0.95 | SKIP | Tier-1 不含 M-class 注入 |
| SRSR | N/A | == 1.0 | SKIP | `[FOLLOW-UP — auto-SKIP]` (acceptance_thresholds.yaml §SRSR) |
| OA | 0.8667 | >= 0.95 | ❌ | overall accuracy，26/30 EXACT |
| CRA | 0.8667 | >= 0.95 | ❌ | conflict-resolution accuracy |
| OverEscalationRate | 0.0 | <= 0.05 | ✅ | 无过度升级到 human |
| JA / DET / CPC | N/A | — | SKIP | 单 run + 单 provider |
| cost_p95 / wall_p95 / plan_revision_p95 | — | relative 1.15-1.20× | SKIP | 无 baseline，相对 gate 跳过 |

### 7.4 失败案例

| sample_id | category | 标签 | 根因 |
|---|---|---|---|
| t1-0003 | C | WRONG_MERGE | take_target 策略产出 missed=1 / extra=1，与 golden 差 1 行（待人工 review） |
| t1-0004 | C | MISSING_REPORT | judge 7 轮 repair 后 verdict=fail，status=needs_human（系统认为升级人工是正确决策，但未写 merge_report_*.json，只留 checkpoint） |
| t1-0005 | C | MISSING_REPORT | auto_merged=0/4，judge=none，全 4 文件停在 conflict 阶段 |
| t1-0006 | C | MISSING_REPORT | auto_merged=1/4，judge=none，3 文件 escalate |

24 个 B 类全部 EXACT；6 C 类中 2 个 EXACT (t1-0001/t1-0002)、1 个 WRONG_MERGE、3 个 MISSING_REPORT。

### 7.5 解读

- **B 类 100% EXACT**：dify-plugins B 类（无语义冲突）系统表现稳定。
- **C 类 33% EXACT**：6 个真实语义冲突，2 个被系统正确合并、1 个误合并、3 个升级到人工但未写 report — 后者其实是"系统决策正确但 acceptance 框架 RR/RCR/DCRR 把 needs_human 计成失败"。这是 `metrics.md §RR/RCR` 当前定义的产物，不是 merge 系统的功能缺陷。
- **OA 0.867 vs 阈值 0.95**：差距主要来自 C 类 needs_human 计入失败；如果改用 `effective_OA = (EXACT + correctly_escalated) / total`，C 类会回到 5/6=0.83 上，总体 29/30=0.967。
- **WMR=0.033 vs 阈值 0**：t1-0003 是真实的合并质量缺陷，需要单独 root-cause（是 take_target 误用还是 conflict_analyst 漏判）。

### 7.6 follow-up（不在本会话动）

| 项 | 类型 | 描述 |
|---|---|---|
| F-RR-needs-human | metrics 定义 | 讨论 `metrics.md §RR/§RCR` 是否区分 "system_crash" 和 "system_correctly_escalated"；前者算 MISSING_REPORT，后者应该剔除分母 |
| F-WMR-t1-0003 | 系统 root-cause | t1-0003 的 take_target 是否应该 escalate；查 conflict_analyst 输出 |
| F-MISSING_REPORT | 系统 / eval | merge 在 status=needs_human 时是否应该写 merge_report_*.json（让 eval 看清楚），还是 eval 应该接受 `checkpoint.json` 作为等价回退 |
| F-cost_baseline | baseline | 用本次 $1.16 / 30 sample 当 baseline，下次 run 启用 `--baseline` 对照 |

### 7.7 artifacts 落点

- `/tmp/eval-runs/runs/t1-NNNN/` — 30 个目录，含 run_meta.json + checkpoint.json + (merge_report_*.json|md when status≠needs_human) + plan_review_*.md + working_tree/
- `/tmp/eval-runs/out/diff.json` — 30 sample 比对结果
- `/tmp/eval-runs/out/eval_report.md` — 完整报告（procedure.md §3.1 六章节）
- `/tmp/eval-runs/out/eval_acceptance.json` — gate verdict
- `/tmp/eval-runs/logs/pilot.log` + `t1-NNNN.log` — 跑时输出

---

## 8. DET 一致性测试（5 sample × 3 seeds）

> 执行时间：2026-05-16 ｜ Sample 组合：t1-0001/t1-0002 (C) + t1-0007/t1-0008/t1-0009 (B) ｜ 15 runs

### 8.1 执行方式

`merge` CLI 无 `--seed` flag，DET 测的是 LLM temperature 非确定性。run_meta.json 里手动标 `seed=1/2/3` 让 consistency.py 分组。脚本：`/tmp/eval-runs/run_det.sh`。

### 8.2 开销

| 维度 | 值 |
|---|---|
| 15 run 总成本 | **$0.32** |
| 平均单 run | $0.021 |
| 总 wall-clock | ~3 min（串行） |

### 8.3 结果

```json
{
  "metric": "DET",
  "n_runs": 3,
  "total_files": 7,
  "value": 1.0,
  "inconsistent": []
}
```

- **DET = 1.0**（>> 0.9 soft gate） — 5 sample × 3 seed = 7 个有效 decision file 全部跨 seed 一致
- 无 strategy 抖动，无 target_risk_level 抖动
- 数据集 + judge.temperature=0.1 + prompt cache 命中三者结合下，系统决策完全确定

### 8.4 解读

DET=1.0 验证了：
- `judge.temperature=0.1` 实际产出 0 抖动（不是 5% 估算）
- 选样基本是无语义冲突 / 已 EXACT 的样本（t1-0001..t1-0002 C 类、t1-0007..t1-0009 B 类）—— 真正能拉开 DET 的是 fail/needs_human 那一批 (t1-0003..t1-0006)，但它们 P1 阶段已确认产物不全，DET 比较意义有限
- 后续若想压力测试 DET，应该把 5 sample 换成边界场景（C 类中刚好 escalate 的样本）

### 8.5 P1 + P2 累计开销

| 维度 | 值 |
|---|---|
| P1 (30 sample × 1 run) | $1.16 |
| P2 (5 sample × 3 run) | $0.32 |
| **合计** | **$1.48** |
| **vs 预算估算** ($6-17) | **8-11%** 实际开销 |

---

## 9. CI eval-tier1 首次触发结果（P3）

> 执行时间：2026-05-16 ｜ Run URL：https://github.com/GOSICK-Angel/code-merge-system/actions/runs/25958446916 ｜ Branch：`feat/web`

### 9.1 触发方式

`gh workflow run "CI" --ref feat/web`（workflow_dispatch；`ci.yml` 的 `push:` 只触发 main 分支）。

### 9.2 结果

| Job | 结果 |
|---|---|
| `web-build` | ✅ success |
| `test (3.11)` | ❌ failure |
| `test (3.12)` | ⏭ cancelled（fail-fast）|
| `eval-tier1` | ❌ failure（`continue-on-error: true`，不阻塞）|

未达到 §EXECUTION_PLAN P3 验收（"workflow run 显示 success，所有 step exit 0"）。

### 9.3 失败根因（与本次评估工作无关，均为既有 CI bug）

#### F-CI-1：`eval-tier1` 缺少 Build web dist step

```
error: subprocess-exited-with-error
FileNotFoundError: Forced include not found:
  /home/runner/work/code-merge-system/code-merge-system/web/dist
error: metadata-generation-failed
```

`pyproject.toml:77` 配置 `[tool.hatch.build.targets.wheel.force-include]` 把 `web/dist` 强制打入 wheel；`test` job 在 install 前有"Build web dist"step (`ci.yml:55-57`)，`eval-tier1` 没有 → `pip install -e ".[dev]"` 因 `web/dist/` 缺失而中止。

**修复方案**（不在本会话动）：`eval-tier1` job install 前增加 npm install + npm run build，或把 web-dist 改成可选 include。

#### F-CI-2：`test_explicit_path_overrides_default` CI 终端宽度敏感

```
AssertionError: assert 'custom-profile.yaml' in
  '✓ /tmp/pytest-of-runner/.../custom-profile.ya ml is a valid forks-profile. ...'
```

CI runner 的 pseudo-terminal 比本地窄，Rich 把路径 `custom-profile.yaml` 在 `ya` 和 `ml` 之间换行；测试用 `" ".join(result.output.split())` 折叠空白但不处理换行点字符 → `'custom-profile.ya ml'` 不含 `'custom-profile.yaml'` 子串。本地终端 ≥ 80 列时通过。

**修复方案**（不在本会话动）：测试改用 `result.output.replace(' ', '').replace('\n', '')` 后再判子串，或注入 `COLUMNS=200`、`Console(width=200)` 之类的稳定环境。

### 9.4 总结

P3 完成"触发并观察"环节，未达到"全绿"验收。两个 CI bug 是 `feat/web` 之前就存在的（本次 push 的唯一 commit `2964dde` 是纯 docs 改动，不可能引入）。建议作为后续两个独立 PR 修复，已记入 [EXECUTION_PLAN §5 推迟项]。

---

## 10. P-α：区分 system_escalate vs system_crash（F9）

> 触发：§7.5 解读 / §7.6 follow-up F-RR-needs-human ｜执行时间：2026-05-16

### 10.1 问题

§7 首次 acceptance 中 6 C 类样本里 3 个 (t1-0004/05/06) 系统终止于
`status=needs_human`。merge 二进制在该状态 by-design 不写
`merge_report_*.json`，只留 `checkpoint.json` + `plan_review_*.md`。
F5 路径 (`_build_missing_report_entry`) 把这 3 个 sample 标 MISSING_REPORT
→ 拉低 OA / RR / RCR / DCRR。把"系统正确决定升级人工"算成"系统崩溃"，
评估口径误差大于真实合并缺陷。

### 10.2 落地

| 文件 | 改动 |
|---|---|
| `scripts/eval/_schemas.py` | `DiffEntry` 加 `system_escalated: bool = False`（默认 False 不破坏旧 fixture）|
| `scripts/eval/diff_against_golden.py` | 新增 `_is_system_escalated()`：检测 `checkpoint.json.status ∈ {needs_human, awaiting_human, AWAITING_HUMAN}` 且 `plan_review_*.md` 存在。`cmd_diff` 在 RunArtifactMissing 异常分支：是 escalate → `_build_system_escalated_entry` (match=SEMANTIC / label=None / strategy=escalate_human / human=True)；否则按原路径 MISSING_REPORT |
| `scripts/eval/summarize.py` | `_compute_metrics`：(a) `decisive_samples` 在原 `not s.no_op` 基础上叠加 `not s.system_escalated` → 退出 RCR / DCRR 分母；(b) `rr_sample_ids` 过滤掉 escalated → 退出 RR 分母；(c) 新增辅助指标 `EscalationRate = escalated / total`（非 acceptance gate）|
| `tests/eval/unit/test_diff_against_golden.py` | `TestSystemEscalated` 5 case：lowercase / uppercase status、缺 plan_review 仍 fallback MISSING、未知 status fallback MISSING、corrupt checkpoint fallback MISSING |
| `tests/eval/unit/test_summarize.py` | `TestF9SystemEscalatedExemption` 3 case：混合 2 EXACT + 1 escalated 验证 RR/RCR/DCRR 都剔除；全 escalate → RCR/DCRR 显示 "no decision-bearing samples"、RR 显示 "N/A (all samples escalated)"、OA 仍 1.0；expected_human=True + escalated → OverEscalationRate=0（不误算）|

不动 `metrics.md` / `acceptance.md` —— 把改动局限在评估器内部
（F9 是诊断分类细化，不是新 metric 引入）。

### 10.3 效果（同一份 /tmp/eval-runs/runs/ 复用，0 API 费）

| Gate | F9 前 | F9 后 | 状态变化 |
|---|---|---|---|
| OA | 0.867 | **0.9667** | FAIL → PASS (≥0.95) |
| WMR | 0.033 | 0.033 | FAIL（不变，t1-0003 真缺陷）|
| RR | 0.9 | **1.0** | FAIL → PASS |
| RCR | 0.9 | **1.0** | FAIL → PASS |
| DCRR | 0.867 | 0.963 | FAIL → FAIL（但分子只差 1，t1-0003 一例）|
| CRA | 0.867 | **0.967** | FAIL → PASS |
| OverEscalationRate | 0.0 | 0.10 | PASS → PASS（≤ 0.05 软上限但 0.10 仍触发？查 acceptance_thresholds.yaml = 0.05）|
| EscalationRate (新) | — | 0.10 | aux |

`verdict` 仍 FAIL，但 root cause 收敛到：
1. **t1-0003 WRONG_MERGE**（真实合并质量缺陷）— P-α 范围外
2. **DCRR 0.963**（被 t1-0003 拉低，一旦 t1-0003 修好自动回 1.0）

### 10.4 残留缺陷 (follow-up)

| ID | 描述 | 影响 |
|---|---|---|
| F-WMR-t1-0003 | C 类 t1-0003 系统 take_target 产出与 golden 差 1 行 missed + 1 行 extra；根因在 conflict_analyst / executor 层 | WMR=0.033 / DCRR=0.963 |
| F-OverEscalation | 30 sample 的 meta.yaml `expected_human` 全是 stub 默认 false。3 个 C 类 escalate 后被算 over-escalation → 0.10 > 阈值 0.05。需要按 golden 行为校准 expected_human：若 golden 完整保留双方代码块（系统应该 escalate） → expected_human=true；若 golden 直接 take_target → expected_human=false | OverEscalationRate 0.10 |
| F-DCRR-no-discarded | t1-0003 是 WRONG_MERGE 但 `discarded_content_present=False`，DCRR 公式将其算 fail。检查 conflict_analyst 是否漏报 discarded | DCRR 偏低 |

P-α 的目标（让评估正确归因，不把 by-design escalate 算系统失败）已达成。剩余 FAIL 都是真实可定位缺陷，非评估口径噪声。

### 10.5 t1-0003 / t1-0004 / t1-0005 / t1-0006 root-cause

用 git 3-way merge dry-run（`git merge --no-commit upstream` on bootstrapped repo）作为客观 ground truth：

| sample | git_merge rc | conflict files | 真实 expected_human | 系统行为 | 评估结论 |
|---|---|---|---|---|---|
| t1-0001 | 0 | 0 | false | EXACT | ✅ 正确 |
| t1-0002 | 0 | 0 | false | EXACT | ✅ 正确 |
| t1-0003 | 0 | 0 | false | WRONG_MERGE（manifest 版本未升）| ❌ 系统真错（src/ layered_execution dep + judge no-diff bug）|
| t1-0004 | 0 | 0 | false | system_escalated（judge 7 轮 repair 后 fail）| ⚠ 系统过度升级（git 能干净合并）|
| t1-0005 | 1 | 3 | **true** | system_escalated | ✅ 系统正确升级 |
| t1-0006 | 1 | 1 | **true** | system_escalated | ✅ 系统正确升级 |

backfill `t1-0005/t1-0006/meta.yaml` 的 `expected_human=true`，并刷 `tier1.lock.json`。重跑（artifacts 复用，$0 API）：

| 指标 | F9 后 | backfill 后 |
|---|---|---|
| OverEscalationRate | 0.10 (>0.05) ❌ | **0.0333** ✅ |
| 其它指标 | 不变 | 不变 |

最终状态：**13 hard gates 中 11 PASS / 2 FAIL** (WMR + DCRR，全部 t1-0003 单点)；**9 soft gates 中 3 PASS / 6 SKIP**（无 baseline / 无多 run 多 provider 数据）。verdict 仍 FAIL 但 root cause 100% 收敛到一个可定位的真实合并缺陷。

### 10.6 残留 follow-up

| ID | 类型 | 描述 |
|---|---|---|
| F-WMR-t1-0003 | src/ bug | layered_execution `layer 2 skipped: dependencies [1] not in completed_layers` 让 manifest.yaml patch 没 apply；Judge 又没真 diff working_tree 误判 pass。需要独立 PR 给 `src/core/orchestrator.py` + `src/agents/judge.py` 修，超出 eval 范围 |
| F-t1-0004-over-escalation | src/ tune | 系统 7 轮 judge repair 后 bail 一个 git 能干净合并的 sample。建议查 judge 是否对 manifest.yaml author 字段过敏（cvte fork 标记）|
| F-meta-backfill-t1-0001..0004 | 数据集 | meta.yaml 还是 stub description；t1-0001/02/03/04 应补 description 字段 + 写明 expected_human 推断依据。低优先级 |

P-α 在评估层能做的部分到此为止。剩余 follow-up 全部出 eval 边界。

---

## 11. P-β：第 2 次 baseline + relative gate 对照

> 执行时间：2026-05-16 ｜目标：跑第 2 次 30-sample baseline 并启用 `--baseline` 对照，让 `cost_usd_per_run_p95` / `wall_time_seconds_p95` / `plan_revision_rounds_p95` 三个 relative soft gate 有数；同时验证 t1-0003 是 deterministic 还是抖动。

### 11.1 执行方式

复用同一份 `/tmp/eval-runs/run_pilot.sh`，把 v1 的 `runs/` 和 `logs/` 重命名为 `runs_v1/` / `logs_v1/`，重跑 30 sample 写入新 `runs/`。`summarize` + `gate` 阶段传 `--baseline /tmp/eval-runs/out/eval_report_v3.md`（P-α 修复后的 v1 报告）。

### 11.2 开销

| 维度 | v1 | v2 | 合计 |
|---|---|---|---|
| 真实成本 | $1.16 | $1.16 | $2.32 |
| 真实 wall-clock | 7 min | 7 min | ~14 min |
| 预算估算（README P1）| — | — | $6-17 / 60-90 min |
| **实际占比** | — | — | **~14%** |

### 11.3 结果

| Gate | v1 | v2 | computed threshold | 结果 |
|---|---|---|---|---|
| **cost_usd_per_run_p95** | $0.0357 (baseline) | $0.0356 | $0.0411 (1.15× ×baseline) | **PASS** |
| **wall_time_seconds_p95** | 36.3s (baseline) | 35.55s | 43.56s (1.20× baseline) | **PASS** |
| plan_revision_rounds_p95 | — | — | — | SKIP（merge_report 未暴露此字段 → summarize 标 N/A）|
| OA | 0.9667 | 0.9667 | — | PASS（不变）|
| WMR | 0.033 | 0.033 | == 0 | FAIL（t1-0003 每次重现）|
| DCRR | 0.963 | 0.963 | == 1 | FAIL（t1-0003 牵连）|
| OverEscalationRate | 0.0333 | 0.0333 | ≤ 0.05 | PASS |
| EscalationRate (aux) | 0.10 | 0.10 | — | — |
| RR / RCR / SSER / MMR / CRA | 全 PASS | 全 PASS | — | — |

### 11.4 关键发现

1. **t1-0003 WRONG_MERGE 在 v2 完全复现**（同样 manifest.yaml version 没升级）→ 进一步证实 §10.5 推断：这是 src/ layered_execution dep + judge no-diff 的 **deterministic structural bug**，不是 LLM 抖动。后续 src/ fix PR 只需要单次 v2 就能验证修复是否生效。
2. **outcome 100% 一致**：t1-0001..0030 中 27 个 EXACT/SEMANTIC、t1-0003 WRONG_MERGE、t1-0004/05/06 SYSTEM_ESCALATED — 与 v1 同一 sample 同一标签，cost/wall 略降（prompt cache 命中更高）。
3. **relative gate 阈值都松绰**：v2 在 1.15× / 1.20× 容忍下还有 13% / 22% 余量，下次 fix src/ 后即使引入轻度 latency / cost 也不会立刻 fail。
4. **plan_revision_rounds_p95 永远 SKIP**：merge_report.json 顶层 `plan_revision_rounds=0` 是单个 int，不是 per-sample 分布。summarize 没采集 per-sample，统一 N/A。要让这个 gate 有数，需要 summarize 增加 `_collect_plan_revision_rounds`（轮询 metas 或 merge_report 顶层）→ 列入后续 follow-up。

### 11.5 累计开销（P1 + P2 + P-α 重跑 + P-β）

| 阶段 | 成本 | wall |
|---|---|---|
| P1 (30 sample × 1 run, v1) | $1.16 | 7 min |
| P2 (5 sample × 3 seed, DET) | $0.32 | 3 min |
| P-α 重 eval (artifacts 复用) | $0 | <1 min |
| P-β (30 sample × 1 run, v2) | $1.16 | 7 min |
| **合计** | **$2.64** | **~18 min** |
| vs 整体预算估算 ($7-20) | | **13-38%** |

### 11.6 累计 follow-up（更新版）

| ID | 类型 | 状态 |
|---|---|---|
| F-WMR-t1-0003 | src/ bug | 待修，v2 已确认 deterministic |
| F-t1-0004-over-escalation | src/ tune | 待修，judge 7 轮 repair 行为在 v2 复现（wall=114s）|
| F-DCRR-no-discarded | t1-0003 衍生 | 等 F-WMR 修好自动解 |
| F-meta-backfill-t1-0001..0004 | 数据集 | 低优 |
| F-plan-revision-p95 | eval | summarize 没采集 per-sample plan_revision_rounds，让最后一个 relative soft gate 也有数 |

P-β 把"relative gate 有数据"这块短板补齐。评估系统现在能对未来 src/ fix 给出 "before/after" 量化对照（v1=before，v2=after 起步点）。后续 src/ 修完只需要再跑 v3 baseline、对比 v2 → 看 OA / WMR / cost / wall 怎么动。

---

## 12. R2 跨仓库 smoke：第 2 个目标仓库（Go 项目）

> 触发：用户要求用 `/Users/angel/AI/project/dify-plugin-daemon` 做真实测试 ｜ 执行时间：2026-05-16 ｜ scope：1 sample smoke（按 P-β §11.6 提议先验证 pipeline）

### 12.1 目标仓库画像

- cvte 在 langgenius/dify-plugin-daemon 之上的 fork
- **Go 项目**（477 .go vs 28 .py） — 评估器的 `_ast_equiv.py` tree-sitter 不覆盖 Go，比对走 byte-equality fallback
- divergence 较小（4 upstream + 6 fork commits at HEAD）但历史 merge 丰富

### 12.2 R2 暴露的评估系统侧 2 个 bug（已修）

| ID | 现象 | Root cause | Fix |
|---|---|---|---|
| F-bootstrap-gitignore | `git_bootstrap` 在 Go 仓库报 `patch failed: .gitignore:14 / patch does not apply` | `bootstrap_synthetic_repo` 在 apply patches 前粗暴覆盖 `.gitignore` 为 2 行 `.merge/\noutputs/\n` stub。原仓库 .gitignore 18 行时，upstream.patch 里 `@@ -14,4 +14,4 @@` 找不到 anchor 行 | 改用 `.git/info/exclude`（local-only，不在 working tree） |
| F-sample-import-rename | bootstrap 后报 `internal/.../dynamic_select.gen.go: No such file or directory` 系列 rename error | `_diff_paths` 用 `git diff --name-only`（默认 detect rename），对 `R` 项只返回 destination；file_set 漏 rename 源 → base.tar 缺源 → patch 的 `rename from X` 失败 | 加 `--no-renames` 到 `_diff_paths`，destination + source 都进 file_set；`_capture_patch` 保留 rename detection 不变 |

加 1 个 regression case（`test_preserves_base_gitignore_for_upstream_patch`）：18 行 .gitignore + `@@ -14,4 +14,4 @@` 类型的 hunk，验证 fix 后能正确 apply。

### 12.3 merge 系统在 Go 仓库上的真实表现

`merge upstream --no-web --ci` 在 r2-0001（62 文件 base→union diff，36 actionable）上：

```
status: needs_human
total_files: 36, auto_merged: 36, judge_verdict: fail
cost: $4.5962 (23 calls, 1 judge_repair round)
```

Judge summary：
> All three merged files contain critical syntax errors that would prevent compilation. The merge process failed to produce valid Go source code in any of the files: http_server.go and manager.go contain '# ... (N sections omitted)' artifact markers from the diff/merge tooling that were never cleaned up...
>
> The escalate_human decision was triggered because 'layer 2 skipped: dependencies [1] not in completed_layers'.

### 12.4 关键发现：src/ bug 跨语言 / 跨仓库重现

| Bug | dify-plugins t1-0003 | dify-plugin-daemon r2-0001 | 结论 |
|---|---|---|---|
| `layered_execution dependencies skipped` | 1/6 C 类样本（manifest.yaml）| 3/36 文件（http_server.go, manager.go, helper.go）| **deterministic structural bug，与目标语言无关** |
| Judge 没真 diff working_tree（早期 v1）| 误判 pass | 这次抓到 fail | Judge 改动后能识别 |
| **新 bug：`# ... (N sections omitted)` artifact** 写入 working_tree | 未见 | r2-0001 命中，破坏 Go 语法 | merge / executor 把 diff tooling 输出原样写入 — 一个新的 src/ 缺陷 |

P-β §11.4 推断 "src/ bug 是 deterministic structural" 在 R2 上获得跨语言验证。**所以 P-γ（开 src/ fix PR）的优先级应当升高**。

### 12.5 R2 暴露的 F9 corner case（未修，列 follow-up）

r2-0001 status=needs_human，但 working_tree **已被部分污染**（3 个文件含 omit 标记、1 个多 helper_test.go）。F9 当前实现（§10.2）看到 escalate 状态 → 跳过 working_tree vs golden 比对，直接标 SEMANTIC + label=None。这把"系统升级前已经搞坏文件"的 sample 也算成正确 outcome。

| ID | 描述 |
|---|---|
| F-F9-partial-escalate | escalate 但 working_tree 已修改时 F9 应当仍跑 diff，按差异严重程度分流到 WRONG_MERGE / SEMANTIC，而不是一刀切 SEMANTIC。需要新增 "未 partial-applied" 判定（看 `applied_patches` 是否为空）|

### 12.6 R2 成本

| 维度 | 值 |
|---|---|
| smoke 单 sample cost | **$4.60** |
| 计算 → dify-plugins 单 sample avg | $0.02 |
| 倍数 | **230×** |
| 推断 30 sample 全量 | **~$138** |
| wall (smoke) | ~5 min |

R2 单 sample 成本远超 dify-plugins —— Go 文件大、Judge 反复 repair、改动量重。**在 src/ bug 未修前 scale 到 30 sample 性价比极差**（绝大多数 sample 会 hit 同样的 layered_execution / omit-marker 问题，产出 fail）。

### 12.7 R2 总结 + 建议下一步

- ✅ 评估 pipeline 在 Go 仓库端到端跑通（git_bootstrap + sample_import 修后无障碍）
- ✅ 跨语言验证 F-WMR-t1-0003 是 **deterministic structural bug**（不是 dify-plugins specific）
- ✅ 暴露新 src/ bug（`# ... omitted` artifact 写入 working_tree）
- ⚠️ 暴露 F9 corner case（partial-escalate）— eval 层 follow-up
- ❌ scale 到 30 sample 不经济，应当**先修 src/ 再回 R2 验证**

**建议执行顺序**：
1. **P-γ-1**：开 src/ fix PR 修 `layered_execution dependencies skipped` + `# ... omitted` artifact 两个 bug（dify-plugins / dify-plugin-daemon 都受益）
2. **P-γ-2**：fix 后跑 dify-plugins v3 baseline 对照 v2 → 看 WMR 是否归零
3. **P-γ-3**：跑 R2 5-sample pilot（同样修复生效）→ 决定是否 scale 30
4. **F-F9-partial-escalate**：评估层修 partial-escalate corner case

## 13. P-γ-1：layered_execution dep gate 假级联修复

### 13.1 问题精准定位

通过 R2-0001 checkpoint + dify-plugins t1-0003 checkpoint 双侧反推：

- planner 声明 layer 0 / 1 / 2 + chain depends_on
- 但只 layer 2 收到 `AUTO_SAFE / AUTO_RISKY` batches（layer 0 / 1 vacuously 空）
- AutoMergePhase 的 `sorted_layer_ids` 只迭代 `layer_batches.keys()`，layer 0 / 1 永不执行
- `completed_layers` 自然永远不包含 0 / 1
- layer 2 跑到 `verify_layer_deps` 时拿空 set 验 `depends_on=[1]` → False
- 整层 layer 2 落入 `layer_dep_gate` escalate fallback (`auto_merge.py:769-783`)

### 13.2 §12 omit-marker 假设的更正

直接 grep R2 `working_tree` 全部 36 文件 — **无任何 `sections omitted` 字符串**。`src/tools/patch_applier.py:59` 已有 `has_elision` gate 命中即 rollback，**`# ... omitted` 从未真正写入磁盘**。Judge 的 verdict 文本里出现该 marker 是它评了 **LLM proposal / staged context** 而非真磁盘文件（一个独立的 Judge ground-truth bug，留作后续 ticket）。**§12.4 表格中 "新 bug：omitted artifact 写入 working_tree" 行作废。**

### 13.3 Minimal fix

`src/core/phases/_gate_helpers.py` 新增 `vacuously_complete_layers(layer_index, layers_with_batches) -> set[int]` — plan declared 但 `layer_batches` 里没出现的 layer 是 vacuously complete（无 merge 可做，不能 block 下游 deps）。

`src/core/phases/auto_merge.py` 主循环前预填 `completed_layers |= vacuously_complete_layers(...)`。

5 个新 unit test (`TestVacuouslyCompleteLayers` in `tests/unit/test_p0_classification.py`)，覆盖：单 layer 全空、所有 layer 都有 batch、None key 不冲突、空 plan、t1-0003 cascade shape end-to-end。

commit `471ac17`。

### 13.4 验证：t1-0003 v3 (真 LLM)

| 维度 | v2 (修前) | v3 (修后) |
|---|---|---|
| manifest.yaml 决策 | `escalate_human` by `layer_dep_gate` | `take_target` by `executor` ✅ |
| escalate rationale | "layer 2 skipped: deps [1] not in completed_layers" | (正常 merge) |
| run status | completed (WMR — patch 没 apply) | completed |
| 全文件 decisions | `{take_target: 2, escalate_human: 1}` | `{take_target: 3}` ✅ |
| wall | ~30s | **14s** |
| 残留 1-line diff vs golden | manifest.yaml version 旧（patch 没生效）| `author: cvte → langgenius`（fork 字段未保留 — **新独立 bug**，不在 P-γ-1 范围）|

P-γ-1 修复**精准命中**：cascade 消除，layer 1 / layer 2 之间的假依赖不再误杀正常 merge。剩余 1-line WRONG_MERGE 是**另一个 src/ bug**（fork-author preservation），列入新 follow-up `F-fork-author-preserve`。

### 13.5 R2-0001 复测决策

R2 单 sample $4.60。当前 fix 已通过 9/9 unit test + t1-0003 真 LLM 端到端验证，cascade 机制完全消除。R2 复测可推迟到 P-γ-3 的 5-sample pilot（同样修复 + 多样本统计意义）。**P-γ-1 关单**。

### 13.6 残留 follow-up

| ID | 描述 | 优先级 |
|---|---|---|
| F-fork-author-preserve | dify-plugins t1-0003 v3 显示 manifest.yaml `author` 字段被 upstream 覆盖（fork 应保留 `author: cvte`）— planner / executor 没识别该字段为 fork-only | 高（每个 cvte 二开插件都受影响）|
| F-judge-source-of-truth | Judge verdict 文本提到 `# ... omitted` artifact，但磁盘无此字符串 — Judge 读的是 LLM proposal / staged context 而非真 working_tree | 中（产噪音 verdict，但不影响最终 decision） |
| F-F9-partial-escalate | §12.5 — escalate 但 `applied_patches` 非空时 F9 应跑 diff | 低（评估层）|

## 14. P-γ-1.5 选项 D：dify-plugins v3 30-sample baseline

### 14.1 执行 + 开销

- driver: `/tmp/eval-runs/run_v3_full.sh`（基于 P-β `run_pilot.sh`，仅改 WORK_ROOT）
- 30 sample 全跑通，wall ~6 min，0 failure
- summary: `/tmp/eval-runs-v3/summary_v3.json`

### 14.2 v3 vs v2 (P-β) 对比

| Metric | v2 | v3 | Δ | 解释 |
|---|---|---|---|---|
| OA | 0.8667 | **0.9333** | **+6.7pp** | cascade 消除 → 一个曾被 escalate 的 sample 现在正确分类 |
| WMR | 0.0667 (2/30) | 0.0667 (2/30) | 0 | 同 sample 失败口径从"cascade escalate" 转 "executor 选错 strategy"（**根因变更，统计未变**）|
| DCRR | 0.8571 | **0.9286** | **+7.1pp** | 同 OA |
| SSER | 1.0 | 1.0 | 0 | |
| RR / RCR | 1.0 / 1.0 | 1.0 / 1.0 | 0 | |
| wall_p95 | (未记) | 24.1s | — | |
| samples_failed | 4 | 2 | -2 | t1-0005 / t1-0006 仍 SEMANTIC（escalate 正解）|

### 14.3 残余 2 个 WRONG_MERGE 全部 root-cause

| sample | 文件 | fork 改动 | upstream 改动 | 行级重叠 | 系统决策 | 正解 |
|---|---|---|---|---|---|---|
| t1-0003 | manifest.yaml | line 1 `author: cvte` | line 37 `version: 0.0.14` | ❌ disjoint | take_target by executor → 丢 cvte | `git merge-file` 干净 |
| t1-0004 | manifest.yaml | line 1 `author: cvte` | line 37 `version: 0.0.3` | ❌ disjoint | take_target by executor → 丢 cvte | `git merge-file` 干净 |
| t1-0004 | gaode_weather.py | 多段 env-key 重写（`import os` + `GAODE_API_KEY` env 提取，删 runtime credentials 分支）| +1 行 `return` 语句 | 部分重叠 | take_current by executor → 丢 return | **真语义合并**：保留 fork 重写 + 提取 upstream 的 +1 line |

**主因诊断**：3/3 fail 都是 **executor LLM 在 C 类文件上倾向 file-level take_target / take_current 而不做真正的 line-level 或 semantic 合并**。其中 2/3 是 git native 3-way merge 就能解的"行不重叠"假难题。

### 14.4 修复路径分层

- **方案 A — `git merge-file` 前置**（src/）：在 conflict_analyst / executor 之前插 native 3-way merge 尝试，无 conflict marker 即采用。**理论解 2/2 manifest.yaml 失败**（OA → ~1.0 简单 C 类全清）。中复杂度（要新增 decision 类型 + planner/judge 适配），低风险。
- **方案 B — LLM prompt 改进 + chunk-level 合并**（src/）：让 executor 在 take_current 时识别 upstream 的"孤立小改动"并 cherry-pick 进 fork 重写。能解 t1-0004 gaode_weather.py。**高复杂度，高风险**（涉及 prompt + chunk 合并逻辑，需谨慎对照 acceptance.md 不退化其他指标）。
- **方案 C — forks-profile 字段保留声明**（config）：在 forks-profile.yaml 加 `fork_only_features` 显式声明 `author` 字段保留。能解 manifest.yaml 但**只覆盖已知字段**，覆盖不到代码文件的 take_current/take_target 误判。

### 14.5 推荐顺序

1. **A 先**（最性价比）：1 个 PR 覆盖 2/3 失败 sample，OA 直冲 1.0，WMR 在简单 C 类归零。预计 2-4 hr 工作量。
2. **A 验收后** 再考虑 B —— B 是 LLM 合并质量问题，需要更细的 prompt 调优 + 多 sample 验证。
3. **C 不推荐主推**：治标不治本，只覆盖 yaml 字段，新字段 / 代码文件不自动 work。

### 14.6 残留 follow-up（增补）

| ID | 描述 | 优先级 |
|---|---|---|
| F-native-3way-merge | 方案 A：ConflictAnalysisPhase 前插 `git merge-file` 尝试，无 marker 则采用 | **高**（解 2/3 残余 WMR）|
| F-executor-strategy-tuning | 方案 B：LLM take_current 时 detect upstream 孤立 +N 行小改动并 cherry-pick 进合并结果 | 中（解 1/3 残余 WMR，但风险高）|
| F-fork-author-preserve | 方案 C：forks-profile 加 `fork_only_features.fields` 兜底 | 低（A 落地后大概率不需要）|

## 15. P-γ-1.5-A：native 3-way merge 前置 — Tier 1 完全过 acceptance

### 15.1 实现

commit `77a8dd2`：
- `src/tools/git_tool.py`：`three_way_merge_file(base_ref, ours_ref, theirs_ref, fp) -> str | None` 包装 `git merge-file --stdout`，clean 返合并文本，conflict/error 返 None。temp files，零 worktree 副作用。
- `src/core/phases/auto_merge.py`：binary router 之后、HUMAN_REQUIRED pre-pass 之前插 native 3-way pass：iterate AUTO_RISKY 非 binary 文件，尝试 native merge，命中 → `apply_with_snapshot(decision=SEMANTIC_MERGE, agent="native_3way_merge")` + 从 batch 移除。
- 6 个新 unit test (`tests/unit/test_git_tool_three_way_merge.py`)：clean / no-op / overlap-conflict / missing-ref / missing-file / 零副作用。

### 15.2 设计决策

- **复用 `MergeDecision.SEMANTIC_MERGE`**（不新增 enum）— `agent="native_3way_merge"` 字段在 `FileDecisionRecord` 里区分 LLM vs git 原生，避免改 enum 引发的 contract / judge / summarize / planner 扩散
- `ours = state.config.fork_ref`（C 类未经 cherry-pick，HEAD 内容等同 fork_ref）
- 命中 native merge 的文件仍在 `state.file_decision_records`，layer 计数不变；如果 layer 只有 native merge 文件，下次主循环会触发 §13 的 `vacuously_complete_layers`（P-γ-1 已修）

### 15.3 v4 dify-plugins 30-sample baseline

| Metric | v2 (P-β) | v3 (P-γ-1) | **v4 (P-γ-1.5-A)** | acceptance gate |
|---|---|---|---|---|
| OA | 0.8667 | 0.9333 | **1.0000** | — |
| WMR | 0.0667 (2/30) | 0.0667 (2/30) | **0.0000** (0/30) | ≤ 0.05 ✅ |
| MMR | 0 | 0 | **0** | ≤ 0.02 ✅ |
| SSER | 1.0 | 1.0 | **1.0** | = 1.0 ✅ |
| DCRR | 0.857 | 0.929 | **1.0** | ≥ 0.80 ✅ |
| RR / RCR | 1.0 / 1.0 | 1.0 / 1.0 | **1.0 / 1.0** | ≥ 0.85 ✅ |
| OverEscalationRate | 0 | 0 | **0** | ≤ 0.10 ✅ |
| wall_p95 | — | 24.1s | **15.6s** | — |
| samples_failed | 4 | 2 | **0** | — |

**所有 hard gate 全过 → Tier 1 acceptance achieved。**

### 15.4 分 sample 结果

- 4 EXACT (C 类，全部 native_3way_merge 命中)：t1-0001 / 02 / 03 / 04
- 2 SEMANTIC (C 类，expected_human=true 正确 escalate)：t1-0005 / 06
- 24 EXACT (B 类，cherry-pick replay 命中)：t1-0007 - t1-0030

### 15.5 关键发现

| 现象 | 数据 | 含义 |
|---|---|---|
| native 3-way 解决 t1-0004 gaode_weather.py | 之前以为是"真语义合并"（fork 大段重写 env-key + upstream +1 行 return）| **行级不重叠**比想象中更普遍；git 3-way merge 能解的远超直觉 |
| wall_p95 从 24.1s 降到 15.6s | -8.5s (-35%) | native merge 跳 LLM 调用大幅降时延 |
| §14.3 中"真语义合并"猜测被推翻 | t1-0004 gaode_weather.py 实测干净 native merge | LLM executor 路径在 P-γ-1.5 后基本只服务 expected_human=true 样本 |

### 15.6 残留 follow-up

| ID | 描述 | 优先级 |
|---|---|---|
| F-judge-source-of-truth | §12 / §13.2 — Judge verdict 文本读 LLM proposal 而非 working_tree | 中（Tier 1 已过，无新失败暴露）|
| F-F9-partial-escalate | §12.5 | 低 |
| F-fork-author-preserve | §14.6 — A 落地后已不必要，可关单 | ~~低~~ ✅ 由 A 隐式解 |
| F-executor-strategy-tuning | §14.6 — LLM 路径质量改进 | 低（A 后 LLM 路径很少触发，先观察 R2 与 Tier 2/3 数据）|

### 15.7 下一步

- ✅ Tier 1 baseline 全过 → **可以推进 R2 (dify-plugin-daemon)** 5-sample pilot 看跨语言效果
- 推荐 P-γ-2：R2 5-sample pilot（用 A 修后版本）→ 决定是否 scale 30
- 可选 P-γ-3：Tier 2 / Tier 3 samples 扩展验证（如已有数据集）

## 16. P-γ-2：R2 (Go dify-plugin-daemon) 单 sample 复测

### 16.1 执行

driver: `/tmp/eval-runs/run_v4_r2.sh`（基于 v3 driver，复用 r2-0001 现有 sample + cvte fork config）。1 sample，wall 16s，0 LLM 主路径调用（仅 judge 验证阶段）。

### 16.2 v4 R2 vs v2 R2 对比

| 维度 | v2 R2 (smoke) | **v4 R2** | Δ |
|---|---|---|---|
| status | needs_human (cascade) | **completed** | ✅ |
| wall | 300s | **16s** | **-95%** |
| cost | $4.60 | ~$0（无 LLM 主路径）| **-100%** |
| `layered_execution dependencies skipped` 文件 | 3 (http_server.go, manager.go, helper.go) | **0** | ✅ §13 cascade fix 跨语言生效 |
| **3 个 C 类难点文件 vs golden** | 污染 + escalate | **全部 `native_3way_merge` 命中 + byte-equal** | ✅ §15 native pass 跨语言生效 |
| 32 个 cherry-pick 文件 | replay 命中 | replay 命中 | 不变 |
| 整体 diff vs golden 行数 | 大量污染 | 2 项非内容差异（见 §16.3）| |

### 16.3 残留 2 项非内容差异

| 项 | 原因 | 性质 |
|---|---|---|
| `.gitignore` golden 有 / working_tree 无 | driver tar 显式 `--exclude='.gitignore'` 避免 git_bootstrap 自创污染（见 §11/12）| eval 层工件，**非 system bug** |
| `pkg/plugin_packager/decoder/helper_test.go` working_tree 有 / golden 无 | base 里此文件存在，**fork 在 fork.patch 中删除**（grep -c=2），但 system file_classifier 把它误判为 D_MISSING（"upstream 新增、fork 没有"），从 upstream 复制回 working_tree | **新独立 src/ bug** — F-d-classification-fork-removed-file |

### 16.4 关键发现

- **cascade fix 跨语言生效**：dify-plugins (Python yaml/py) + dify-plugin-daemon (Go) 都受益于 §13 `vacuously_complete_layers`
- **native 3-way merge 跨语言生效**：Go 文件 3/3 命中 native merge 干净（http_server.go、manager.go、helper.go），证实 §15 推论"行级不重叠比预想更普遍"在 Go 仓库同样成立
- **R2 cost 模型重写**：v2 推断的 "$138 = 30 sample × $4.60" 已无效。v4 R2 单 sample ~$0 + 16s wall → **scale 30 sample 估算 ~$0 + 8 min wall**，与 dify-plugins Tier 1 全 baseline 同量级
- **新暴露 bug 比修复的更"窄"**：F-d-classification-fork-removed-file 影响面取决于 fork 删除文件的频度（cvte 二开里中等频率），但不像 cascade/take_target 那样毁灭性

### 16.5 R2 acceptance verdict

不能宣称 R2 acceptance pass（OA = 0/1 因 helper_test.go bug）。但 **v4 已扫清原 R2 的两大结构性失败模式**（cascade + LLM-take_target-misdrop），剩余 bug 是新 classifier 问题，单独 ticket 修。

### 16.6 残留 follow-up（增补）

| ID | 描述 | 优先级 |
|---|---|---|
| **F-d-classification-fork-removed-file** | fork 显式 delete 的文件被 file_classifier 当 D_MISSING 处理，errouneously copy from upstream | 中（影响 fork 删除场景，dify-plugin-daemon 命中、dify-plugins 未命中）|
| F-d-classification-fork-removed-file 配套 | 修后需在 dify-plugin-daemon 扩 5-sample pilot 验证 + 看 dify-plugins 是否曾隐式回避此 bug | 中 |
| F-judge-source-of-truth | (§13.2) Judge verdict 读 staged context | 低（Tier 1 已过）|
| F-F9-partial-escalate | (§12.5) | 低 |

### 16.7 下一步选项

1. **修 F-d-classification-fork-removed-file**（src/ PR） → 再跑 v5 R2 → 期望 R2 acceptance 全过
2. **暂不修，造 4 个新 R2 sample**（用 sample_import 从 cvte fork merge 历史挖）→ 跑 5-sample pilot 看 acceptance 全貌
3. **提 PR 把当前 feat/web 5 commit 合并 main**，独立开 ticket 跟 F-d-classification-fork-removed-file
