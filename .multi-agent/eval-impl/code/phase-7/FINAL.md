# Phase 7 实施报告 v1

## commit
`35cbf69` — feat(eval): Phase 7 — consistency.py (DET/CPC)

## 新增/修改文件清单

### scripts/eval/
- `scripts/eval/consistency.py` — 新增 246 行（≤ 250 上限）
- `scripts/eval/gate.py` — 修改 14 行（Phase 6 P2-1：`assert ... is not None` → 显式 `if ... is None: raise ValueError(...)`，防 `python -O` 剥离）
- `scripts/eval/summarize.py` — 修改 17 行（Phase 6 P2-3：`_compute_rr` 多匹配 glob 显式拆分为 `_has_nonempty_match` helper + 多匹配策略注释）

### tests/eval/
- `tests/eval/manifests/acceptance_thresholds.yaml` — 修改 1 行（Phase 6 P2-4：SRSR `source` 显式加 `[FOLLOW-UP — auto-SKIP]` marker）
- `tests/eval/unit/test_gate.py` — 新增 53 行（Phase 6 P2-2：新增 `TestSkipPaths` 两用例覆盖缺指标 → SKIP）
- `tests/eval/unit/test_consistency.py` — 新增 426 行（T7-C1..C5 全覆盖 + 4 边界用例 + 1 internal helper）

合计 6 文件改动 / 757 行新增 / 16 行删除。**未触碰 src/、doc/evaluation/、pyproject.toml。**

## 测试结果

```
pytest tests/eval/unit/ —— 230 passed in 1.69s（216 from Phase 0-6 + 2 P2-2 skip + 12 consistency）
pytest --cov=scripts/eval --cov-fail-under=80 —— 94.50% (PASS)
  - scripts/eval/__init__.py             100%
  - scripts/eval/_ast_equiv.py            94%
  - scripts/eval/_common.py               94%
  - scripts/eval/_fork_name_check.py      90%
  - scripts/eval/_ground_truth.py         94%
  - scripts/eval/_report_render.py       100%
  - scripts/eval/_schemas.py             100%
  - scripts/eval/consistency.py           96%  (新增)
  - scripts/eval/diff_against_golden.py   96%
  - scripts/eval/gate.py                  95%
  - scripts/eval/lock.py                  94%
  - scripts/eval/prepare.py               91%
  - scripts/eval/run.py                   93%
  - scripts/eval/summarize.py             94%
mypy scripts/eval tests/eval —— Success: no issues found in 30 source files (strict)
ruff check scripts/eval tests/eval —— All checks passed
ruff format --check scripts/eval tests/eval —— 30 files already formatted
python -m scripts.eval._fork_name_check scripts/eval tests/eval —— exit 0
```

## 契约对齐

| 契约项 | 来源 | 实施位置 | 状态 |
|---|---|---|---|
| 输入多个 runs/，输出 DET / CPC 数值 + 不一致样本清单 | plan §Phase 7 GO §1 | `cmd_consistency` 写 JSON：`{metric, value, n_runs, total_files, inconsistent[], run_dirs[]}` | OK |
| 比对维度 `MergeState.file_decision_records[f].(strategy, target_risk_level)` | plan §Phase 7 GO §2 + plan 决策 3 | `_decision_tuple` 读 `decision` ↔ `strategy` / `target_risk_level` ↔ `risk` 双字段名兜底 | OK |
| 独立 CLI（不挤压 lock argparse 互斥 [code-phase-1]） | dispatch §强制 | `scripts.eval.consistency.main` 独立 argparse，无 sub-parser | OK |
| 不触发 N 次跑（plan 决策 3：调用方 shell 循环） | dispatch §强制 | 仅读已落盘 `runs/<sample_id>/merge_report_*.json`，无 subprocess | OK |
| 复用 [code-phase-4] `_locate_merge_report` 多匹配选 lex-last 策略 | dispatch §强制 | `_locate_merge_report` 1:1 复刻 sorted matches[-1] 行为 | OK |
| mypy strict / ruff / fork-name-check / cov ≥ 80% | plan §Phase 7 GO §3 + Phase 0-6 标准 | 全绿 | OK |
| T7-C1 DET 全一致 → 1.0 | test FINAL §8 + v1.md ll.535-538 | `TestDetAllAgree::test_det_returns_one_when_all_runs_agree` | OK |
| T7-C2 DET 部分不一致 → <1.0 + 列样本 | test FINAL §8 + v1.md ll.540-543 | `TestDetPartialDisagree::test_det_lists_inconsistent_sample` | OK |
| T7-C3 CPC 切 provider 走相同管道 | test FINAL §8 + v1.md ll.545-548 | `TestCpcSamePipeline::test_cpc_uses_same_engine_with_two_runs` + disagree 变体 | OK |
| T7-C4 runs < 2 → exit 1 + stderr 含 "requires" | test FINAL §8 + v1.md ll.550-553 | `TestTooFewRuns::test_single_run_returns_one` | OK |
| T7-C5 runs 间 sample_id 集合不一致 → exit 1 + 差集 | test FINAL §8 + v1.md ll.555-558 | `TestSampleSetMismatch::test_disjoint_samples_returns_one` | OK |

### Carry-forward 闭环

| 来源 | 处置 | 状态 |
|---|---|---|
| [code-phase-6] P2-1 gate.py `assert ... is not None` | 改为显式 `if ... is None: raise ValueError(...)`（防 `python -O` 剥离） | CLOSED |
| [code-phase-6] P2-2 缺指标 → SKIP 路径未在 test 显式覆盖 | 新增 `TestSkipPaths` 两用例：absolute gate 缺指标 + relative gate 缺指标 → `pass=null + skipped_reason="metric ... not numeric in report"` | CLOSED |
| [code-phase-6] P2-3 `_compute_rr` glob 多匹配策略 | 抽取 `_has_nonempty_match` helper + 注释多匹配策略（任一 non-empty 计数；兼容单子目录与重跑两种场景） | CLOSED |
| [code-phase-6] P2-4 yaml SRSR hard gate 隐式 SKIP | 在 `source` 前置 `[FOLLOW-UP — auto-SKIP]` marker 显式标注 follow-up 性质 | CLOSED |
| [code-phase-5] P2-4 git_sha vs model_matrix 多值策略 | 本期未顺手修（不在 carry-forward 强制要求；待 Phase 8 e2e 真实多机数据时再决） | DEFERRED |
| [code-phase-4] P2-3 `_decision_to_system_decision` 双字段名兜底 | Phase 8 e2e 真实 merge 跑时验证；本期 `consistency._decision_tuple` 已主动遵循同样双字段名 fallback 模式 | TRACKED |

### Approved-facts 锁清单遵守对照

| 锁条目 | 实施侧验证 |
|---|---|
| [code-phase-0] `_schemas` 模型 frozen / extra=forbid | 未引入新 schema；consistency.py 输出裸 dict，由 `write_json` 落盘 |
| [code-phase-1] argparse 互斥 3 子命令（不得加第 4 个）| consistency.py 完全独立 CLI，未触碰 lock.py |
| [code-phase-2] `_apply_patch_to_tree` 已 immutable | 未触碰 |
| [code-phase-3] eval_subprocess_env 是唯一 env 工厂 | 本期不涉及 subprocess |
| [code-phase-3] `_locate_merge_run_dir` 单子目录假设 | `_compute_rr` 多匹配策略改进与本锁兼容（单子目录场景仍单匹配） |
| [code-phase-4] diff_against_golden 不解 tar | 未触碰 |
| [code-phase-4] `_locate_merge_report` 多匹配 → lex-last | `consistency._locate_merge_report` 1:1 复用同策略 |
| [code-phase-5] `_persist_ci_summary` 包装层 | 未触碰 |
| [code-phase-6] GateKind = ABSOLUTE/RELATIVE | 未触碰 |

## 计划细节自纠

| 计划原文 | 实际 | 采用理由 | 锚点 |
|---|---|---|---|
| `consistency.py ≤ 250 行` | 实际 246 行（含 docstring + 注释） | 留够边界余量；CLI driver / aggregation / IO 三段拆分清晰 | `scripts/eval/consistency.py` |
| `--metric {DET, CPC}` 共用管道 | 同管道，仅在输出 JSON 中标识 metric 名 | metrics.md §6.1 / §6.2 定义只在意图区分（重复 vs 切 provider），算法对称 | `consistency._compute_metric` |
| 缺失文件按 ABSENT sentinel | 实现：`rec_map.get(file_path, ("ABSENT", "ABSENT"))` | 避免静默膨胀一致率分母；显式标 disagreement | `consistency.py:142-144` |
| `consistency.py` 输出 schema 留作 follow-up | 输出裸 dict + `write_json`（atomic + sorted keys） | Phase 7 范围内未要求 frozen schema；e2e 测试只需校验顶层 keys；保留扩展空间 | `cmd_consistency:198-206` |
| `total_files = 0 → value = ?` | 实施退化为 `1.0`（vacuous PASS） | 与 `_compute_rr` `runs_dir=None → 1.0` 一致；避免 ZeroDivisionError | `_compute_metric:160` |

## 范围合规

- 未修改 `src/` 任何代码
- 未修改 `doc/evaluation/{README,metrics,dataset,procedure,acceptance}.md`
- 未修改 `pyproject.toml`
- 未修改 `.github/workflows/*`（Phase 9 才动）
- 未在代码出现 `cvte` / `dify` / `insforge`（fork-name-check exit 0）
- 未新增运行时依赖（全 stdlib + pydantic v2 已有）
- 未 `git add -A`，所有 add 都是显式文件清单（6 文件）
- 未跳过 pre-commit hook
- 未超出 Phase 7 范围（e2e / CI 是 Phase 8+）

## Phase 8 续接锚点

Phase 8 (`test_e2e_tier1.py`) 直接可用：

- `scripts.eval.consistency.main` — CLI 入口可直接调用
- `tests/eval/fixtures/fake_merge_bin/fake_merge.sh` — Phase 3 fake CLI（FAKE_FIXTURE_DIR / FAKE_SAMPLE_ID env 控制）
- `scripts.eval.{lock,prepare,run,diff_against_golden,summarize,gate}.main` — 5 步链全部就位
- `tests/eval/manifests/acceptance_thresholds.yaml` + tier1.lock.json — 已落 commit，e2e 可直接 verify

**已知遗留 / 留给后续**（与 Phase 6 v1 报告一致，未在 Phase 7 引入新遗留）：
- WDR hard gate / SRSR 数据流 / 多文件 sample 聚合 / git_sha multi-value 策略 / `_persist_ci_summary` envelope — 不在 Phase 7 范围
