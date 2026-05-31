# Phase 9 v1 Review — gatekeeper-code

**审查时间**：2026-05-16
**被审件**：`.multi-agent/eval-impl/code/phase-9/v1.md` + commit `7687ba7`
**结论**：**GO**

---

## 1. 自动化 verify 套件

| 检查 | 命令 | 结果 |
|---|---|---|
| pytest (unit + integration + meta) | `pytest tests/eval/ -q --cov=scripts/eval --cov-fail-under=80` | **242/242 PASSED** |
| coverage | (同上) | **94.50%** (≥80%) |
| mypy strict | `mypy scripts/eval tests/eval` | Success: 33 source files, 0 error |
| ruff check | `ruff check scripts/eval tests/eval` | All checks passed |
| ruff format | `ruff format --check scripts/eval tests/eval` | 33 files already formatted |
| fork-name-check | `python -m scripts.eval._fork_name_check scripts/eval tests/eval` | exit 0 |
| lock --verify (local) | `python -m scripts.eval.lock --verify` | exit 0 |
| lock --verify (CI) | `CI=true python -m scripts.eval.lock --verify` | exit 0 |
| ci.yml yaml.safe_load | `python -c "yaml.safe_load(open('.github/workflows/ci.yml'))"` | jobs=['web-build', 'test', 'eval-tier1'] |

模块覆盖率与 Phase 8 一致（94-100%），无回归。

---

## 2. 架构合规

| 项 | 验证 | 状态 |
|---|---|---|
| 不动 `src/` | stat 仅 2 文件（ci.yml + test_ci_workflow_meta.py）| ✓ |
| 不动 `doc/evaluation/` / `pyproject.toml` / `scripts/eval/` 主源码 | stat 未列 | ✓ |
| 不动 `tests/eval/unit/` 其他既有用例 | stat 仅新增 test_ci_workflow_meta.py | ✓ |
| ci.yml 现有 `web-build` / `test` job 结构未破坏 | diff 仅追加 step + 新增独立 job | ✓ |
| 无新运行时依赖 | `yaml` / `subprocess` / `time` / `re` 全已存在或 stdlib | ✓ |
| 无 fork name | fork-name-check exit 0 | ✓ |
| 无中文注释 | docstring/comments 全英 | ✓ |

---

## 3. 强制契约对齐（team-lead 派单 + plan §Phase 9 GO §1-4）

| 强制契约 | 实施 | 状态 |
|---|---|---|
| **eval-tier1 job 必须 `continue-on-error: true` 或 `if` 排除 PR (T9-W7)** | **双保险**：`if: github.event_name != 'pull_request'` (ci.yml:104) + `continue-on-error: true` (ci.yml:105) | ✓ |
| 5 必备 step 显式新增（不假设 `pytest tests/` 自动覆盖） | ci.yml `Lint eval scripts (ruff)` / `Type check eval scripts (mypy)` / `Eval unit + e2e tests` / `Verify dataset locks` / `Fork name purity check` 全部存在 | ✓ |
| cov source 独立 `--cov=scripts/eval` 而非 `--cov=src` | `Eval unit + e2e tests` step run cmd 显式 `--cov=scripts/eval`；不含 `--cov=src`（T9-W2 守护）| ✓ |
| mypy `scripts/eval tests/eval` 与现有 `mypy src` 独立 step | 2 个独立 step 并存；T9-W3 显式守护 | ✓ |
| PR 时长 ≤ 30s | unit 套件实测 ~2.5s（T9-W6 自检阈值 25s 5s 余量）| ✓ |
| eval-tier1 可手动触发 | `workflow_dispatch` 在顶层 `on:`（GitHub Actions 不支持 per-job `on:`，正确语法）| ✓ |

---

## 4. T9-W1..W7 用例覆盖（test FINAL §10）

| 用例 ID | 测试位置 | 核心断言 | 状态 |
|---|---|---|---|
| T9-W1 | `TestRequiredEvalSteps::test_all_five_steps_present` | `missing == []` for 5 必备 step | ✓ |
| T9-W2 | `TestCovSourceIndependent` 2 用例 | `--cov=scripts/eval` ∈ eval step + `--cov=src` ∈ Unit tests step，互不渗透 | ✓ |
| T9-W3 | `TestMypyEvalStepIndependent::test_eval_mypy_step_does_not_share_with_src_mypy` | `mypy scripts/eval tests/eval` 在独立 step + `mypy src` step 不含 `scripts/eval` | ✓ |
| T9-W4 | `TestMissingStepDetected::test_removing_verify_dataset_locks_fails_required_check` | regex 删 step → missing 集合含此 step | ✓ |
| T9-W5 | `TestEvalTier1ManualTrigger` 2 用例 | `eval-tier1` job 存在 + workflow_dispatch/schedule/nightly 占位 OR | ✓ |
| T9-W6 | `TestUnitSuiteRuntime::test_unit_suite_under_threshold` | unit 套件 `elapsed <= 25.0` + `pytest.mark.skipif(os.getenv("CI"))` 防自递归 | ✓ |
| T9-W7 | `TestEvalTier1NonBlocking::test_one_of_three_non_blocking_conditions_holds` | 3 OR 条件（if PR 排除 / continue-on-error / on 不含 PR）之一成立 | ✓ |

---

## 5. 锁清单遵守对照（[code-phase-0..8]）

| 锁条目 | 影响位置 | 验证 |
|---|---|---|
| [code-phase-0] `_fork_name_check` 入口 `python -m scripts.eval._fork_name_check scripts/eval tests/eval` exit 0 | ci.yml `Fork name purity check` step | ✓ 完整命令对齐 |
| [code-phase-1] `lock.cmd_verify` CI 区分 = `os.environ.get("CI") == "true"` | ci.yml `Verify dataset locks` step 不显式设 CI；GitHub Actions runner 默认 `CI=true` | ✓ 自然走 CI 严格模式 |
| [code-phase-1] argparse 互斥 3 子命令上限 | 未触 lock.py | ✓ |
| [code-phase-3] eval_subprocess_env / fake_merge.sh | 未触；e2e step 通过 pytest 间接调用 | ✓ |
| [code-phase-6] yaml synced_with_sha | 未触；CI verify step 守护 sha 一致 | ✓ |
| [code-phase-7] consistency.py 独立 CLI | 未触；通过 e2e step 间接覆盖（test_e2e_tier1.TestE2eDetChain）| ✓ |
| [code-phase-8] e2e 3 用例契约 | 未触；CI `Eval unit + e2e tests` step 自动覆盖 `tests/eval/integration/` | ✓ |

---

## 6. 计划细节自纠核对

| Executor 自纠 | 评估 |
|---|---|
| ruff/mypy 路径 `scripts/eval` vs plan 原文 `scripts` | ✓ 接受：与 cov source 一致 + 避免未来兄弟目录被意外 lint |
| eval-tier1 顶层 `on:` 公用（GitHub Actions 不支持 per-job on）| ✓ 接受：语法正确性 trump 计划字面 |
| nightly schedule 注释占位 vs 真启用 | ✓ 接受：避免预算泄露 + T9-W5 OR 含 `nightly placeholder, not blocking` 注释分支 |
| T9-W4 regex 替代 yaml round-trip | ✓ 接受：最小破坏 + 显式 `assert "Verify dataset locks" not in mutated` 防 regex 失效 |
| T9-W6 用 `sys.executable` + `--ignore=test_ci_workflow_meta.py` 防自递归 | ✓ 接受：CI 中 skip 单/双层防御 |
| yaml `on` key 在 YAML 1.1 下解析为 `True` | ✓ `_workflow_on` helper 兼容 `True` 与 `"on"` 两种 key，且 ci.yml 中 `on:` 已确认走 `True` 分支 |

---

## 7. P0 / P1 / P2 问题

**P0**: 无
**P1**: 无
**P2**: 无（Phase 9 范围限于 CI yaml + meta tests，无引入新遗留）

---

## 8. Carry-forward 状态（与 Phase 8 对照，未恶化）

| 来源 | 状态 | 处置 |
|---|---|---|
| [code-phase-5] P2-4 git_sha vs model_matrix 多值策略 | DEFERRED | 需 Tier-1 真实评估实测时再决（eval-tier1 nightly 启用后） |
| [code-phase-4] P2-3 双字段名 e2e 真实验证 | TRACKED | 需真实 merge CLI 跑通后决定 |
| [code-phase-8] P2-1 `_extract_golden_tree` 风格优化 | TRACKED | test helper 风格 |
| SRSR / WDR / tree-sitter / 多文件 sample / `--baseline` | DEFERRED | Tier-2 后或 plan v3 后 |

**未引入新遗留** ✓

---

## 9. 最终基线快照（Phase 0-9 全部 GO 后）

```
pytest tests/eval/                              242/242 PASSED in 2.96s
  - tests/eval/unit/                            233 passed
  - tests/eval/integration/                       3 passed
  - tests/eval/unit/test_ci_workflow_meta.py      9 passed (Phase 9 meta)
coverage scripts/eval                           94.50% (每模块 ≥ 90%)
mypy scripts/eval tests/eval                    33 source files, 0 error
ruff check / ruff format --check                clean
fork-name-check                                 exit 0
lock --verify (local + CI=true)                 双模式 exit 0
```

---

## 10. 结论与下一步

**GO** — Phase 9 实施达成 plan §Phase 9 全部 GO 条件 + team-lead 派单强制要求（eval-tier1 双保险非阻塞）+ T9-W1..W7 全覆盖。无 P0 / P1 / P2 问题。**eval-impl Phase 0-9 全部落地**。

**后续动作**：
- 拷贝 `code/phase-9/v1.md` → `code/phase-9/FINAL.md`
- 追加 `locks/approved-facts.md` 新事实（带 `[code-phase-9]` 标签）
- SendMessage executor 通知 GO + 触发 IMPLEMENTATION_REPORT_PARTIAL.md（task #4，scope.md §5 大纲）

Phase 0-9 已闭环；剩 IMPLEMENTATION_REPORT_PARTIAL.md 由 task-list / team-lead 决定 owner（按当前 TaskList 该任务已 in_progress；team-lead 已认领，gatekeeper-code 不跨边界）。
