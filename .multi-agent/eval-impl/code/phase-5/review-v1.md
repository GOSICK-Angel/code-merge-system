# gatekeeper-code 审查报告（Phase 5 v1）

> 审查者：gatekeeper-code（team eval-impl）
> 日期：2026-05-15
> 送审 commit：`bf4cfda38fc042261b5864920539ef830b6fa95a`
> 实施报告：`.multi-agent/eval-impl/code/phase-5/v1.md`
> 计划基准：`.multi-agent/eval-impl/plan/FINAL.md` §Phase 5
> 测试契约：`.multi-agent/eval-impl/test/FINAL.md` §6 Phase 5（T5-R1..R2 + T5-S1..S5）
> 锁清单基准：`.multi-agent/eval-impl/locks/approved-facts.md`

---

## 结论

**通过**

Phase 5 plan GO 5/5 全绿；test FINAL T5-R1..R2 + T5-S1..S5 全 7 项契约 100% 对齐（T5-S1 v2 强化 18 指标 anchor 全覆盖）；**Phase 3 P2-2 ci_summary 包装层 carry-forward 正式 CLOSED**（识别 invalid_json + raw_value 两种 envelope，写入 §6 异常段，2 用例守护）；范围严格、7 项细节自纠理由充分；pytest 185/185 / cov 94.27% / mypy strict 27 files 0 err / ruff clean / fork-name-check exit 0。

P0=0 / P1=0 / P2=4（均不阻塞，carry-forward Phase 6）。

---

## 契约核查表

### plan FINAL §Phase 5 GO 条件

| 契约 | 状态 | 锚点 |
|---|---|---|
| 输出 markdown 含 procedure.md §3.1 全部六章节 | PASS | 模板 `## 1.` ~ `## 6.` 标题 (`eval_report.md.j2:12-90`) + T5-R1 双用例 |
| 至少含 OA/WMR/MMR/WDR/SSER/DCRR/RR/RCR/Recall_M1..M6 | PASS | 模板 hard 9 + soft 9 + Recall 6 全 anchor；TestEighteenMetricAnchors 18 项断言 |
| concurrency > 1 自动头部标注 "wall_time/cost not authoritative"（决策 3 / P1-7） | PASS | `_build_context.not_authoritative` + 模板 `{% if not_authoritative %}` (`:4-10`) + T5-S2/S3 双向 |
| 失败案例清单按 sample_id 排序 | PASS | `_failure_rows:222-233` sorted + T5-S5 rindex 严格序 |
| mypy strict / ruff / fork-check / cov ≥ 80% | PASS | 27 files 0 err / clean / exit 0 / 94.27% |

### test FINAL §6 Phase 5 用例对账

| 用例 | 实现位置 | 状态 |
|---|---|---|
| T5-R1（六章节 anchor + 元信息 keys） | `TestSixSections` 2 用例 (`test_report_render.py:78-88`) | PASS |
| T5-R2（StrictUndefined：metrics + top-level 缺失） | `TestStrictUndefined` 2 用例 (`:96-107`) | PASS |
| T5-S1 v2 强化（18 指标 anchor 全覆盖 + Recall_M1..M6 独立） | `TestEighteenMetricAnchors` (`test_summarize.py:103-142`) | PASS |
| T5-S2（serial 模式无 banner） | `TestConcurrencyBanner.test_serial_run_omits_banner` | PASS |
| T5-S3（parallel 模式有 banner + max=N） | `TestConcurrencyBanner.test_parallel_run_inserts_banner` | PASS |
| T5-S4（缺 run_meta.json → rc=2） | `TestMissingRunMeta` | PASS |
| T5-S5（失败按 sample_id 排序） | `TestFailureSortOrder` rindex 严格序断言 | PASS |

7/7 用例契约对齐；额外 18 个补强（concurrency banner template 直测 / Recall expansion / baseline 占位 + 渲染 / known_issues 列表 / ci_summary wrapping 2 路径 / arg validation 2 路径 / `_compute_metrics` 空集 / `_failure_rows` sort / `_build_context` `<mixed>` git_sha / `_percentile` 边界 / `_load_ci_summary` 边界 / 模块常量）。

总用例数：29（Phase 0 54 + Phase 1 20 + Phase 2 27 + Phase 3 20 + Phase 4 35 + Phase 5 29 = 185）。

### Carry-forward 处置

| 来源 | 处置 | 状态 |
|---|---|---|
| [code-phase-3] P2-2 ci_summary.json 包装层 (raw_value / invalid_json) | `_load_ci_summary:81-90` 识别两种 envelope + `_detect_known_issues:245-257` 写入 §6 + 2 用例守护 | **CLOSED** |
| [code-phase-4] P2-1 `_diff_one_sample` 多文件聚合 | Phase 5 metrics 在样本级聚合，未触发多文件需求；Phase 6/7/8 真出现时再 revisit | DEFERRED |
| [code-phase-4] P2-3 `_decision_to_system_decision` 双字段名兜底 | Phase 5 不读 strategy/risk（只取 sample.system_decision.strategy 字符串展示）；未触发 | DEFERRED Phase 8 e2e |
| T4-D10 SRSR | metrics anchor 值固定 `"N/A (follow-up)"`；待 plan v3 落地 | FOLLOW-UP |
| [code-phase-2] P2-3 sentinel acceptance_yaml | Phase 5 未涉及 yaml | DEFERRED Phase 6 |

### Approved-facts 锁清单遵守

| 锁条目 | 验证 |
|---|---|
| [code-phase-0] `_schemas` 模型 frozen / extra=forbid | 本期未新增 schema 模型；复用 DiffReport / DiffEntry / RunMeta / MatchStatus / MismatchLabel / SystemDecision ✓ |
| [code-phase-0] `_common.{atomic_write_text, read_json}` 复用 | `cmd_summarize:369` atomic_write_text；`_load_diff/_load_run_metas` 走 read_json ✓ |
| [code-phase-0] `eval_subprocess_env` 唯一 env 工厂 | Phase 5 不 spawn subprocess ✓ |
| [code-phase-2] `_apply_patch_to_tree` 重构后 pure | Phase 5 未触 ✓ |
| [code-phase-2] `_ground_truth.load_golden_tree` 唯一 tar 入口 | Phase 5 不解 tar ✓ |
| [code-phase-3] `_persist_ci_summary` 包装层必须感知 | `_load_ci_summary` 识别两种 envelope + `_detect_known_issues` 写入异常段 ✓ |
| [code-phase-4] DiffReport 反序列化用 model_validate | `_load_diff:62-63` ✓ |
| [code-phase-4] DiffReportMeta.semantic_engine narrowed Literal["tree-sitter","fallback-bytes"] | `_build_context:327` 直接读 `diff.meta.semantic_engine` 传到模板 ✓ |
| [test] T5-S1 v2 强化 18 指标 anchor | 模板硬 9 + 软 9 + Recall 6 全 anchor；TestEighteenMetricAnchors 18 项断言 ✓ |
| [test] T5-S5 失败排序 | `_failure_rows:224` sorted + rindex 严格序 ✓ |
| [test] T5-R2 StrictUndefined | `_build_env:32-37` 用 StrictUndefined；2 用例 ✓ |
| [test] T4-D10 SRSR 可 follow-up | SRSR anchor 用占位值 "N/A (follow-up)"；不阻塞 ✓ |
| [plan] 决策 3 / P1-7 concurrency 测时矛盾 | `_build_context.not_authoritative` + 模板 banner + T5-S2/S3 ✓ |

---

## 测试结果

- **pytest**：`tests/eval/unit/` 185/185 PASSED（in 1.21s）
- **覆盖率**：`--cov=scripts/eval` 总 94.27%（≥ 80%）
  - `_report_render.py` 100%（15/15 stmts）
  - `_schemas.py` 100%
  - `summarize.py` 94%（未覆盖：runs_dir 不存在分支 70、ci_summary read_json 异常 88-89、_percentile 边界 111、_baseline_rows OSError 181-187、_baseline_rows key absent 267-268）
  - 其他模块覆盖率维持 Phase 4 基线
- **mypy**：`scripts tests/eval` strict, 27 files, 0 error
- **ruff check**：All checks passed
- **ruff format --check**：27 files already formatted
- **fork-name-check**：`python -m scripts.eval._fork_name_check scripts/eval tests/eval` exit 0
- **范围验证**：`git diff HEAD~1 HEAD --stat -- src/ doc/evaluation/ pyproject.toml .multi-agent/ datasets/ manifests/ fixtures/ <所有 Phase 0-4 现有源文件>` 输出空 = 0 修改

---

## P0 / P1 / P2 分级问题

### P0（必须修复才通过）
无。

### P1（强烈建议修复）
无。

### P2（可选优化，不阻塞）

1. **`_compute_metrics` 中 `SSER` 语义疑问**
   - **现状**：`summarize.py:150-152` `"SSER": _format_pct((len(security_sensitive) / total) if security_sensitive else 1.0)`。
   - **预期**：acceptance.md §1 SSER (Security-Sensitive Escalation Rate) 应是"敏感文件被升级到 human 的比例"；当前实施是"security-sensitive 样本占比 if any else 1.0"，**语义错位**。
   - **影响**：当前 1-sample fixtures 上无影响（scope.md §6 数值层退化已声明）；Phase 6 / Tier-1 抽样矩阵补齐后会偏离 acceptance.md 定义，gate 决策可能错。
   - **建议**：Phase 6 实施 gate.py 时回头对照 acceptance.md §1 SSER 公式重写：`SSER = count(security_sensitive AND escalated_to_human) / count(security_sensitive)`。
   - **锚点**：`scripts/eval/summarize.py:150-152`

2. **`_compute_metrics` 中 `RR` 硬编码 1.0**
   - **现状**：`summarize.py:155` `"RR": _format_pct(1.0)` + comment "Phase 3 GO already proved 3-artifact landing"。
   - **预期**：RR (Rationale Retention Rate) 应该按样本统计 `plan_review_<run_id>.md` 是否存在 / rationale 是否含完整 trace；硬编码 1.0 等同 "永远 pass"。
   - **影响**：Phase 6 gate 若 RR 是 hard gate，永远绿；真实 RR 退化不被发现。
   - **建议**：Phase 6 实施 gate.py 时按 acceptance.md §1 RR 公式真实计算（plan_review.md 存在性 + rationale 完整度）。
   - **锚点**：`scripts/eval/summarize.py:155`

3. **`_compute_metrics` 中 WDR 永远 0**
   - **现状**：`summarize.py:131,149` WDR = miss_fork / total；但 [code-phase-4] `_classify_pair` 已把 MISS_FORK 简化为 MISS_UPSTREAM，所以 miss_fork 在当前架构永远 0。
   - **影响**：报告里 WDR 永远 "0.0000"，不反映真实"系统过度回滚 fork 改动"现象。
   - **建议**：与 P2-3 [code-phase-4] 锁定的"完整 MISS_FORK 区分留 Tier-2"一致；当前可接受，但建议在模板注释 "WDR 当前架构下永远 0，待 Tier-2 引入 fork.patch + base oracle"。
   - **锚点**：`scripts/eval/summarize.py:131,149`

4. **`_build_context` git_sha 与 model_matrix 不一致聚合策略**
   - **现状**：`summarize.py:312-313` git_sha 多值取 `<mixed>`，`model_matrix` 多值取第一个 non-empty。
   - **影响**：metadata 一致性弱，Phase 5 报告可能误导用户"以为所有 sample 共享 model_matrix"。
   - **建议**：Phase 6/8 统一策略 — 多个 metas 时都用第一个或都用 `<mixed>`/`{}` 标记。
   - **锚点**：`scripts/eval/summarize.py:306-313`

---

## 残留风险（含 carry-forward）

| ID | 风险 | 影响 | 处置 |
|---|---|---|---|
| RR1 | T4-D10 SRSR anchor 占位 | 待 plan v3 落地 MergeState.snapshot_rollback_events | [test] TR7 follow-up，Phase 5/6/8 不阻塞 |
| RR2 | `--baseline` 仅文本 present/absent 标记 | 报告 §5 对比表无数值 delta | 需基线 schema 标准化（out of scope） |
| RR3 | metrics 语义偏差（SSER/RR/WDR） | Phase 6 gate 决策可能偏离 acceptance.md | P2-1/P2-2/P2-3 列入 Phase 6 必查清单 |
| RR4 | 1-sample 数值退化 | 多项指标 0/100% | scope §6 已声明；Tier-1 抽样矩阵补齐工作 |
| RR5 | [code-phase-2] P2-3 sentinel acceptance_yaml | Phase 6 yaml 创建后行为偏离 | carry-forward Phase 6 |
| RR6 | [code-phase-4] P2-1 多文件聚合 | Phase 6/7/8 真出现时再 revisit | DEFERRED |

---

## 计划自纠评估

| 自纠项 | 评估 | 接受度 |
|---|---|---|
| summarize.py 350→408 行 | 18 指标 + 5 loader + 6 reducer + ci_summary 感知；远 ≤ 800 硬上限 | **接受** |
| 9→18 指标 anchor | Verifier v2 / [test] 锁清单强化要求 | **接受** |
| SRSR anchor 占位 "N/A (follow-up)" | [test] TR7 follow-up 明确允许 | **接受** |
| T5-S5 用 rindex 取最后位置 | §3 数据集顺序 + §4 sorted 双出现，rindex 准确捕捉 §4 排序 | **接受** |
| `--baseline` 文本 present/absent | 上一基线无结构化 schema；数值 delta 留后续 | **接受**（P2 已 carry-forward） |
| 1-sample metrics 退化 | scope §6 / plan §6 已声明 | **接受** |
| 模板 `_templates/` 同目录 | jinja2 FileSystemLoader 默认 | **接受** |

---

## 副作用核查

`git diff --name-only HEAD~1 HEAD` 列出 5 个新文件：

- `scripts/eval/_report_render.py`（新建）
- `scripts/eval/_templates/eval_report.md.j2`（新建）
- `scripts/eval/summarize.py`（新建）
- `tests/eval/unit/test_report_render.py`（新建）
- `tests/eval/unit/test_summarize.py`（新建）

**未触碰**：`src/` / `doc/evaluation/` / `pyproject.toml` / `.multi-agent/` / `.github/workflows/` / `tests/eval/datasets/` / `tests/eval/manifests/` / `tests/eval/fixtures/` / Phase 0/1/2/3/4 已交付的 31 文件中的任何一个（_schemas / _ast_equiv / diff_against_golden / lock / prepare / run / _ground_truth / _common / _fork_name_check / __init__ + 全部测试与 fixture）。
**未引入新运行时依赖**：`jinja2` 已是 pyproject 主依赖（pre-existing）；`statistics` / `datetime` / `json` / `argparse` / `sys` 均 stdlib。

合规。

---

## 二审及之后

本轮为首审，无上轮反馈核查项。

---

## 通过决定

✅ Phase 5 v1 通过审查。
- copy `v1.md` 到 `code/phase-5/FINAL.md`
- 追加 6 条新事实到锁清单（带 `[code-phase-5]` 标签，含 Phase 3 P2-2 carry-forward CLOSED / `_report_render.render_report` API 与 StrictUndefined 契约 / 18 指标 anchor 模板锁定 / `_compute_metrics` 语义偏差 P2 carry-forward Phase 6 / `_failure_rows` 排序契约 / 测试基线 185 用例）+ Carry-forward 待办段更新（Phase 6 必须重写 SSER/RR/WDR 真实公式）
- 通知 executor + team-lead，可继续 Phase 6（gate.py + acceptance_thresholds.yaml）
