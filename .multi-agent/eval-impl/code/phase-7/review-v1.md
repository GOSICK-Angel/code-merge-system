# Phase 7 v1 Review — gatekeeper-code

**审查时间**：2026-05-16
**被审件**：`.multi-agent/eval-impl/code/phase-7/v1.md` + commit `35cbf69`
**结论**：**GO**

---

## 1. 自动化 verify 套件

| 检查 | 命令 | 结果 |
|---|---|---|
| pytest | `pytest tests/eval/unit/ -q --cov=scripts/eval --cov-fail-under=80` | 230/230 PASSED in 1.67s |
| coverage | (合并入上) | 94.50% (≥80%) |
| mypy strict | `mypy scripts/eval tests/eval` | Success: 30 source files, 0 error |
| ruff check | `ruff check scripts/eval tests/eval` | All checks passed |
| ruff format | `ruff format --check scripts/eval tests/eval` | 30 files already formatted |
| fork-name-check | `python -m scripts.eval._fork_name_check scripts/eval tests/eval` | exit 0 |
| lock --verify (local) | `python -m scripts.eval.lock --verify` | exit 0 |
| lock --verify (CI) | `CI=true python -m scripts.eval.lock --verify` | exit 0 |

每模块覆盖率：

```
scripts/eval/__init__.py                100%
scripts/eval/_ast_equiv.py               94%
scripts/eval/_common.py                  94%
scripts/eval/_fork_name_check.py         90%
scripts/eval/_ground_truth.py            94%
scripts/eval/_report_render.py          100%
scripts/eval/_schemas.py                100%
scripts/eval/consistency.py              96%   (新增)
scripts/eval/diff_against_golden.py      96%
scripts/eval/gate.py                     95%
scripts/eval/lock.py                     94%
scripts/eval/prepare.py                  91%
scripts/eval/run.py                      93%
scripts/eval/summarize.py                94%
```

---

## 2. 架构合规

| 项 | 验证 | 状态 |
|---|---|---|
| 不动 `src/` | `git show 35cbf69 --stat` 仅 6 文件，全在 scripts/eval + tests/eval | ✓ |
| 不动 `doc/evaluation/{README,metrics,dataset,procedure,acceptance}.md` | stat 未列 | ✓ |
| 不动 `pyproject.toml` | stat 未列 | ✓ |
| 不动 `.github/workflows/*`（Phase 9 才动） | stat 未列 | ✓ |
| 无新运行时依赖 | consistency.py 仅 `argparse / json / sys / collections / pathlib / typing` + `_common.write_json` | ✓ |
| 无 fork name (cvte/dify/insforge) in src | fork-name-check exit 0 | ✓ |
| 文件 ≤ 800 行 | consistency.py 245 行（≤ 250 上限）| ✓ |
| 无中文注释 | grep 全英 docstring + comments | ✓ |
| pydantic v2 | 复用 [code-phase-0] 现有 schema，未引入新模型 | ✓ |
| 无 mutation | `_compute_metric` 内部 `defaultdict` 是局部状态；不 mutate 入参 | ✓ |

---

## 3. 契约合规（与 plan §Phase 7 GO + locks 对照）

| 契约 | 来源 | 实施 | 状态 |
|---|---|---|---|
| 输入多个 runs/，输出 DET/CPC 数值 + 不一致样本清单 | plan §Phase 7 GO §1 | `cmd_consistency` 写 JSON `{metric, value, n_runs, total_files, inconsistent[], run_dirs[]}` | ✓ |
| 比对维度 `MergeState.file_decision_records[f].(strategy, target_risk_level)` | plan §Phase 7 GO §2 + 决策 3 | `_decision_tuple` 双字段名兜底（`decision\|strategy` × `target_risk_level\|risk`）| ✓ |
| 独立 CLI（不挤 lock 互斥 group） | dispatch §强制 + [code-phase-1] | `scripts.eval.consistency.main` 独立 argparse | ✓ |
| 不触发 N 次跑 | plan 决策 3 + dispatch §强制 | 仅读已落盘文件，无 subprocess | ✓ |
| 复用 [code-phase-4] `_locate_merge_report` lex-last | dispatch §强制 | `consistency._locate_merge_report` 1:1 复刻 `sorted(matches)[-1]` | ✓ |
| mypy strict / ruff / fork-check / cov ≥ 80% | plan §Phase 7 GO §3 | 全绿 | ✓ |

---

## 4. T7-C1..C5 用例覆盖（test FINAL §8 + v1.md ll.535-558）

| 用例 ID | v1.md 语义 | 测试位置 | 断言 | 状态 |
|---|---|---|---|---|
| T7-C1 | DET 全一致 → 1.0 | `TestDetAllAgree::test_det_returns_one_when_all_runs_agree` | value==1.0, inconsistent==[] | ✓ |
| T7-C2 | DET 部分不一致 → <1.0 + 列样本 | `TestDetPartialDisagree::test_det_lists_inconsistent_sample` | value≈0.5, inconsistent[0].sample_id=="t1-0001", len(decisions)==3 | ✓ |
| T7-C3 | CPC 切 provider 同管道 | `TestCpcSamePipeline` 2 用例 (agree + disagree) | metric=="CPC", agree=1.0, disagree=0.0 | ✓ |
| T7-C4 | runs < 2 → exit 1 + stderr 含 "requires" | `TestTooFewRuns::test_single_run_returns_one` | rc==1, "requires" in stderr, "DET" in stderr, out 不存在 | ✓ |
| T7-C5 | sample_id 不一致 → exit 1 + 差集 | `TestSampleSetMismatch::test_disjoint_samples_returns_one` | rc==1, "t1-0002" in stderr, "missing-somewhere" in stderr | ✓ |

**额外加强（高于 GO 要求）**：

- `TestRunDirValidation` — run dir 不存在 / sample 缺 merge_report → rc=1 + stderr
- `TestDualFieldNameFallback` — JSON-mode names vs MergeState alias names 跨 run 一致
- `TestEmptySampleSet` — `total_files=0 → 1.0` vacuous PASS（与 `_compute_rr` `runs_dir=None → 1.0` 退化策略对齐）
- `TestAbsentFileSentinel` — 文件在一个 run 出现另一个缺失 → ABSENT 标 disagreement（防分母膨胀）
- `TestLocateMergeReportPicksLast` — 多 `merge_report_*.json` 文件取 lex-last（[code-phase-4] 锁守护）

---

## 5. Carry-forward 闭环验证

| 编号 | 内容 | 实施位置 | 验证 | 状态 |
|---|---|---|---|---|
| **P2-1** | gate.py `assert ... is not None` → 显式 `if/raise` 防 `-O` 剥离 | `gate.py:124-131,162-166` | ✓ 两处都改成 `if entry.threshold is None: raise ValueError(...)` / `if entry.multiplier is None: raise ValueError(...)`，注释明确说明"survives ``python -O``" | **CLOSED ✓** |
| **P2-2** | 缺指标 SKIP 路径补显式 test | `test_gate.py:559-610` 新增 `TestSkipPaths` 两用例 | ✓ absolute (WMR 缺) + relative (cost_p95 缺) 双向覆盖；断言 `pass is None` + `"not numeric" in skipped_reason` | **CLOSED ✓** |
| **P2-3** | `_compute_rr` glob 多匹配策略 | `summarize.py:134-160` | ✓ 拆 `_has_nonempty_match` helper + docstring 显式说明"任一 non-empty 计数"策略 + `directory.is_dir()` 防御 + 与 [code-phase-3] 单子目录假设兼容性注释 | **CLOSED ✓** |
| **P2-4** | yaml SRSR hard gate 隐式 SKIP → 显式 marker | `acceptance_thresholds.yaml:40` SRSR.source | ✓ 前置 `[FOLLOW-UP — auto-SKIP]` marker + 完整解释 "gate.py currently emits pass=null+skipped_reason because summarize.py SRSR anchor is the placeholder 'N/A (follow-up)'" + "Verdict does NOT depend on SRSR until plan v3" | **CLOSED ✓** |

**4/4 Phase 6 carry-forward 全部闭环**。

---

## 6. 锁清单遵守对照（[code-phase-0..6]）

| 锁条目 | 影响位置 | 验证 |
|---|---|---|
| [code-phase-0] schema frozen / extra=forbid | 未引入新 schema | ✓ 未触碰 |
| [code-phase-1] argparse 互斥 3 子命令上限 | consistency.py 独立 CLI | ✓ 未触 lock.py |
| [code-phase-2] `_apply_patch_to_tree` immutable | 未触 prepare.py | ✓ |
| [code-phase-3] `eval_subprocess_env` 唯一 env 工厂 | 本期不涉及 subprocess | ✓ |
| [code-phase-3] `_locate_merge_run_dir` 单子目录假设 | `_compute_rr` 多匹配策略与本锁兼容（单子目录场景仍单匹配，不破坏既有契约）| ✓ |
| [code-phase-4] `diff_against_golden` 不解 tar | 未触 | ✓ |
| [code-phase-4] `_locate_merge_report` lex-last | consistency.py 1:1 复刻同策略 | ✓ |
| [code-phase-4] 双字段名 fallback (`decision\|strategy`) | consistency `_decision_tuple` 主动遵循 | ✓ |
| [code-phase-5] `_persist_ci_summary` envelope | 未触 | ✓ |
| [code-phase-6] GateKind = ABSOLUTE/RELATIVE | 未触 | ✓ |
| [code-phase-6] yaml soft_gates kind/multiplier | 未触 thresholds 子树（仅改 SRSR source 字符串）| ✓ |
| [plan-amend] hard 优先于 soft | gate.py P2-1 加固未破坏 `_derive_verdict` 优先级 | ✓ |
| [test-amend] T6-G6..G11 语义 | gate.py P2-1 仅在不可达分支加 raise，行为不变 | ✓ |

---

## 7. 计划细节自纠核对

| Executor 自纠 | 评估 |
|---|---|
| consistency.py 实际 245 行（≤ 250） | 接受（留余量） |
| DET/CPC 共用管道，仅 metric 字段区分 | 接受（与 metrics.md §6.1/§6.2 一致：算法对称，区分意图）|
| 缺失文件 ABSENT sentinel | 接受（防分母膨胀，显式 disagreement）|
| 输出裸 dict 而非 frozen schema | 接受（Phase 7 范围未要求；预留 Phase 8 e2e 扩展空间）|
| `total_files=0 → value=1.0` | 接受（与 `_compute_rr` `runs_dir=None → 1.0` 一致）|

---

## 8. 遗留 / 后续

未在 Phase 7 引入新遗留。原有 carry-forward 状态：

| 来源 | 状态 | 处置 |
|---|---|---|
| [code-phase-5] P2-4 git_sha vs model_matrix 多值策略 | DEFERRED | Phase 8 e2e 真实多机数据时再决，不阻 Phase 8 |
| [code-phase-4] P2-3 `_decision_to_system_decision` 双字段名 e2e 验证 | TRACKED | Phase 8 e2e 真实 merge 跑触发；consistency.py 已主动遵循同样 fallback 模式 |
| SRSR 数据流 (T4-D10) | DEFERRED | 待 plan v3 落地 `MergeState.snapshot_rollback_events` |
| 多文件 sample 聚合 ([code-phase-4] P2-1) | DEFERRED | 待 Tier-2 抽样矩阵 |
| WDR hard gate ([code-phase-4]) | TRACKED | Tier-2 启用 MISS_FORK 完整区分后 |

---

## 9. 结论与下一步

**GO** — Phase 7 实施达成 plan §Phase 7 全部 GO 条件 + 4/4 Phase 6 carry-forward 闭环 + T7-C1..C5 全覆盖 + 5 额外加强用例。无 P0 / P1 / P2 新增问题。

**后续动作**：
- 拷贝 `code/phase-7/v1.md` → `code/phase-7/FINAL.md`
- 追加 `locks/approved-facts.md` 新事实（带 `[code-phase-7]` 标签 + 4 个 carry-forward CLOSED 标记）
- SendMessage executor 通知 GO + 触发 Phase 8（e2e integration test）
