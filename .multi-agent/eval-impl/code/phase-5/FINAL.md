# Phase 5 实施报告 v1

## commit
`bf4cfda38fc042261b5864920539ef830b6fa95a` — feat(eval): Phase 5 — summarize + _report_render (jinja2)

## 新增/修改文件清单

### scripts/eval/
- `scripts/eval/_report_render.py` — 新增 66 行（≤ 150 上限）
- `scripts/eval/_templates/eval_report.md.j2` — 新增 98 行（无上限；procedure.md §3.1 六章节）
- `scripts/eval/summarize.py` — 新增 408 行（plan ≤ 350，超 58 行；详见 §"计划细节自纠"）

### tests/eval/unit/
- `tests/eval/unit/test_report_render.py` — 新增 186 行（12 用例：T5-R1..R2 + concurrency banner + Recall expansion + baseline + known_issues）
- `tests/eval/unit/test_summarize.py` — 新增 444 行（17 用例：T5-S1..S5 + ci_summary 包装感知 + arg validation + 内部 helper）

合计 5 文件新增 / 1202 行新增 / 0 行删除。**未触碰 src/、doc/evaluation/、Phase 0/1/2/3/4 已交付的 31 文件。**

## 测试结果

```
pytest tests/eval/unit/ —— 185 passed in 1.10s（156 from Phase 0-4 + 29 new）
pytest --cov=scripts/eval --cov-fail-under=80 —— 94.27% (PASS)
  - scripts/eval/__init__.py             100%
  - scripts/eval/_ast_equiv.py            94%
  - scripts/eval/_common.py               94%
  - scripts/eval/_fork_name_check.py      90%
  - scripts/eval/_ground_truth.py         94%
  - scripts/eval/_report_render.py       100%  (新增)
  - scripts/eval/_schemas.py             100%
  - scripts/eval/diff_against_golden.py   96%
  - scripts/eval/lock.py                  94%
  - scripts/eval/prepare.py               91%
  - scripts/eval/run.py                   93%
  - scripts/eval/summarize.py             94%  (新增)
mypy scripts tests/eval —— Success: no issues found in 27 source files (strict)
ruff check scripts tests/eval —— All checks passed
ruff format --check scripts tests/eval —— 27 files already formatted
python -m scripts.eval._fork_name_check scripts/eval tests/eval —— exit 0
```

## 契约对齐

| 契约项 | 来源 | 实施位置 | 状态 |
|---|---|---|---|
| 输出 eval_report 含 procedure.md §3.1 全部六章节 | plan §Phase 5 GO §1 | jinja2 模板顶层 6 个 `## N.` 标题；T5-R1 守护 | OK |
| 至少含 OA / WMR / MMR / WDR / SSER / DCRR / RR / RCR / Recall_M1..M6 | plan §Phase 5 GO §2 + Verifier T5-S1 v2 | 模板硬 9 + 软 9 + Recall 6 全 anchor；TestEighteenMetricAnchors 18 项断言 | OK |
| concurrency > 1 自动头部标注 "wall_time/cost not authoritative" | plan §Phase 5 GO §3 + 决策 3 / P1-7 + Verifier T5-S2/S3 | `_build_context.not_authoritative` + 模板 `{% if not_authoritative %}` 块；TestConcurrencyBanner 双向覆盖 | OK |
| 失败案例清单按 sample_id 排序 | plan §Phase 5 GO §4 + Verifier T5-S5 | `_failure_rows` 用 `sorted(..., key=lambda s: s.sample_id)` ; TestFailureSortOrder 验证 a-/m-/z- 序 | OK |
| mypy strict / ruff / fork-check / cov ≥ 80% | plan §Phase 5 GO §5 + Phase 0-4 标准 | 全绿 | OK |
| StrictUndefined：模板变量缺失抛 jinja2.UndefinedError | Verifier T5-R2 | `_build_env` 用 `StrictUndefined`；TestStrictUndefined 2 用例（metrics/top-level）| OK |
| 缺失 run_meta.json 退出非 0 | Verifier T5-S4 | `_load_run_metas` raise FileNotFoundError → `cmd_summarize` 转 rc=2；TestMissingRunMeta 验证 | OK |
| 18 指标 hard 9 + soft 9 + Recall 6 anchor 全覆盖 | Verifier T5-S1 v2 强化 (P0-1 + P1-4) | 模板 anchor 列表完整；TestEighteenMetricAnchors 全部 18 + 6 Recall 断言 | OK |

### Carry-forward 处置

| 来源 | 处置 | 状态 |
|---|---|---|
| Phase 3 P2-2 ci_summary.json 包装层 (raw_value / invalid_json) | `_load_ci_summary` 识别 `{"invalid_json": True}` 与 `{"raw_value": ...}` 两种 envelope；`_detect_known_issues` 把识别结果写入 §6 异常段；TestCiSummaryWrappingAwareness 2 用例守护 | CLOSED |
| Phase 4 P2-1 `_diff_one_sample` 多文件聚合 | Phase 5 本期不需多文件聚合（DiffEntry 已是 per-sample 粒度，metrics 走样本级聚合）；Phase 6/7/8 真出现多文件需求时再 revisit | DEFERRED |
| T4-D10 SRSR | 模板/metrics 中 `SRSR` anchor 值固定为 `"N/A (follow-up)"`；待 plan v3 落地 `MergeState.snapshot_rollback_events` 字段后回填 | FOLLOW-UP |

### Approved-facts 锁清单遵守对照

| 锁条目 | 实施侧验证 |
|---|---|
| [code-phase-0] `_schemas` 模型 frozen / extra=forbid | 本期未新增 schema 模型；仅复用 DiffReport / DiffEntry / RunMeta / MatchStatus / MismatchLabel / SystemDecision |
| [code-phase-0] `_common.{atomic_write_text, read_json}` 复用 | `cmd_summarize` 调 `atomic_write_text(output, markdown)`；`_load_diff/_load_run_metas` 走 `read_json` |
| [code-phase-3] `_persist_ci_summary` 包装层 raw_value/invalid_json 必须感知 | `_load_ci_summary` 识别两种 envelope；`_detect_known_issues` 写入异常段；2 用例守护 |
| [code-phase-4] DiffReport 反序列化用 model_validate | `_load_diff` 用 `DiffReport.model_validate(read_json(diff_path))` 而非平行 dict 解析 |
| [code-phase-4] DiffReportMeta.semantic_engine narrowed | `_build_context` 直接读 `diff.meta.semantic_engine` 传到模板，类型已是 `Literal["tree-sitter","fallback-bytes"]` |
| [test] T5-S1 v2 强化 18 指标 anchor | 模板硬 9 + 软 9 + Recall 6 全 anchor；TestEighteenMetricAnchors 18 项断言 |
| [test] T5-S5 失败案例按 sample_id 排序 | `_failure_rows` 用 `sorted` + TestFailureSortOrder rindex 严格序断言 |
| [test] T5-R2 StrictUndefined | `_build_env` 用 `StrictUndefined`；TestStrictUndefined 2 用例 |
| [test] T4-D10 SRSR 可 follow-up | SRSR anchor 用占位值；不阻塞 |
| TR2 mypy strict jinja2 untyped | jinja2 import 在 pyproject 主依赖，无需 type: ignore（mypy 0 error 已验证）|

## 计划细节自纠

| 计划原文 | 实际 | 采用理由 | 锚点 |
|---|---|---|---|
| `scripts/eval/summarize.py` ≤ 350 行 | 实际 408 行 | 内含：(a) 18 指标 `_compute_metrics` 完整实现 + `_empty_metrics` 空集分支；(b) 5 个 input loader (`_load_diff` / `_load_run_metas` / `_load_ci_summary`)；(c) 3 个 reducer (`_percentile` / `_format_pct` / `_failure_rows`)；(d) `_detect_known_issues` (Phase 3 carry-forward)；(e) `_baseline_rows`；(f) `_build_context` (~50 行) 把所有半成品组装成模板 context；(g) `cmd_summarize` + argparse + main。功能完全符合 plan §Phase 5 + Verifier T5-S1 v2 + Phase 3 P2-2 carry-forward。如需进一步压缩，自然分拆点是把 `_compute_metrics` / `_empty_metrics` 拆到 `_metrics_compute.py`，但目前耦合度低，跨模块依赖会损害可读性 | `summarize.py:1-408` |
| Verifier T5-S1 v1 "9 指标" | Verifier v2 强化为"18 指标 + Recall_M1..M6 分子项独立 anchor" | v2 §A.1 增量 + [test] 锁清单要求 hard 9 + soft 9 (包含 SRSR / JA / CRA / OverEscalation / cost_p95 / wall_time_p95 / plan_revision_rounds_p95 等) + Recall_M1..M6；TestEighteenMetricAnchors 18 项断言守护 | `summarize._compute_metrics` + 模板 `## 2.` 节 |
| Verifier T5-S1 SRSR anchor | 实施值固定为 `"N/A (follow-up)"` | [test] TR7 锁清单允许：snapshot_rollback_events 字段待 plan v3 落地。当前 anchor 存在保证报告对齐，数值"诚实标 N/A"避免误算 | `summarize._compute_metrics:140` |
| Verifier T5-S5 "按字符串排序断言" | TestFailureSortOrder 用 `rindex` 取最后一次出现位置断言序 | 同一 sample_id 在模板的 `## 3. 分 tier 结果` 表（数据集顺序）+ `## 4. 失败案例` 表（sorted by id）出现两次；用 rindex 取后者准确捕捉 §4 排序契约 | `test_summarize.py::TestFailureSortOrder` |
| `--baseline` 行为 | 实施为"按 key 文本匹配 + 标记 present/absent"，不做数值减法 | 上一基线 report 是 markdown，没有结构化字段；plan §Phase 5 GO 仅要求"对比表"存在；数值 delta 需先标准化基线 schema（out of scope）。改进留给后续工作 | `summarize._baseline_rows` |
| metrics N/A 值的退化 | OA / RR / SSER 等在 1-sample 上必为 0/100% 退化值 | 已与 scope.md §6 / plan §6 一致：本期仅守护管线连通，数值层留 Tier-1 抽样矩阵补齐。SRSR/JA/DET/CPC/plan_revision_rounds_p95 5 项明确 N/A，不假装可信 | `_compute_metrics` + `_empty_metrics` |
| 模板 `_templates/eval_report.md.j2` 路径 | 与 `_report_render.py` 同目录的 `_templates/` | jinja2 `FileSystemLoader` 默认；与 `importlib.resources` 兼容；测试时无需 monkeypatch 路径 | `_report_render.TEMPLATES_DIR` |

## 范围合规

- 未修改 `src/` 任何代码
- 未修改 `doc/evaluation/{README,metrics,dataset,procedure,acceptance}.md`
- 未修改 `pyproject.toml` 任何字符
- 未修改 Phase 0/1/2/3/4 已交付的 31 文件（含 _schemas、_ast_equiv、diff_against_golden 全部不动）
- 未在代码出现 `cvte` / `dify` / `insforge`（fork-name-check exit 0）
- 未新增运行时依赖（`jinja2` / `yaml` / `json` / `statistics` / `datetime` 全部已在 pyproject 主依赖或 stdlib）
- 未 `git add -A`，所有 add 都是显式文件清单（5 文件）
- 未跳过 pre-commit hook
- 未超出 Phase 5 范围（`gate.py` / `consistency.py` / `e2e` 是 Phase 6+）

## Phase 6 续接锚点

Phase 6 (`gate.py` + `acceptance_thresholds.yaml`) 直接可用：

- `scripts.eval.summarize.cmd_summarize(...)` — Phase 8 e2e 可调用产生 eval_report.md
- 模板 `_templates/eval_report.md.j2` 的 18 指标 anchor 是 gate.py 读取 metric 值的契约（regex / parser 共用）
- `_compute_metrics` 输出 dict 含完整 18 keys（含 N/A 占位），gate.py 可直接复用此格式做 metric→threshold 比对
- `_schemas.{AcceptanceReport, AcceptanceThresholds, AcceptanceThresholdEntry, GateKind, GateOperator, GateVerdict, GateResult}` 已就绪 — gate.py 直接复用
- [plan-amend] 已锁定 soft gate `kind: absolute | relative` 与 `multiplier`，Phase 6 实施时按此扩展 `AcceptanceThresholdEntry`

**已知遗留 / 留给后续**：
- **T4-D10 / Verifier SRSR data flow** — anchor 用占位 `"N/A (follow-up)"`，待 plan v3 落地后由 Executor 在后续 commit 中回填真实计算
- **--baseline 数值 delta** — 当前仅文本 present/absent 标记；需基线 schema 标准化后做结构化 delta，留给后续工作
- **多文件 sample 聚合** — Phase 4 P2-1 carry-forward 仍 open；本期 metrics 聚合在样本级，未触发多文件需求；Phase 6/7/8 真出现时再 revisit
- **真实指标数值** — 1-sample Tier-1 上多项指标退化为 0/100%；数值层留 Tier-1 抽样矩阵补齐（scope §6）
