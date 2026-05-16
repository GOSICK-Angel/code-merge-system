# Phase 8 v1 Review — gatekeeper-code

**审查时间**：2026-05-16
**被审件**：`.multi-agent/eval-impl/code/phase-8/v1.md` + commit `2854a82`
**结论**：**GO**

---

## 1. 自动化 verify 套件

| 检查 | 命令 | 结果 |
|---|---|---|
| pytest (unit + integration) | `pytest tests/eval/ -q --cov=scripts/eval --cov-fail-under=80` | **233/233 PASSED in 1.92s** |
| coverage | (同上) | **94.50%** (≥80%) |
| mypy strict | `mypy scripts/eval tests/eval` | Success: 32 source files, 0 error |
| ruff check | `ruff check scripts/eval tests/eval` | All checks passed |
| ruff format | `ruff format --check scripts/eval tests/eval` | 32 files already formatted |
| fork-name-check | `python -m scripts.eval._fork_name_check scripts/eval tests/eval` | exit 0 |
| lock --verify (local) | `python -m scripts.eval.lock --verify` | exit 0 |
| lock --verify (CI) | `CI=true python -m scripts.eval.lock --verify` | exit 0 |

模块覆盖率与 Phase 7 一致（94-100%），无回归。

---

## 2. 架构合规

| 项 | 验证 | 状态 |
|---|---|---|
| 不动 `src/` | stat 仅 3 文件，全在 `tests/eval/` 下 | ✓ |
| 不动 `doc/evaluation/` / `pyproject.toml` / `.github/workflows/` | stat 未列 | ✓ |
| **不动 `scripts/eval/` 主源码** | stat 未列任何 `scripts/eval/*.py`（Phase 8 仅写测试 + 改一个 fixture shell）| ✓ |
| 无新运行时依赖 | 全 stdlib：tarfile / os / json / pathlib + pytest（已是 dev dep） | ✓ |
| 无 fork name | fork-name-check exit 0 | ✓ |
| 无中文注释 | docstring / comments 全英 | ✓ |
| pydantic v2 | 未引入新 schema | ✓ |
| 无 mutation | helper 用入参 monkeypatch + Path 操作，无外部状态 mutation | ✓ |

---

## 3. 强制契约对齐（team-lead 派单 GO §1-3）

| GO 条件 | 实施位置 | 状态 |
|---|---|---|
| §1 单测试函数串起 prepare → run → diff → summarize → gate | `TestE2eFullChain::test_chain_lands_pass_verdict` 显式调 `run.main → diff.main → summarize.main → gate.main`（run.main 内部已驱动 prepare，[code-phase-3] 锁定） | ✓ |
| **§2 断言 `eval_acceptance_*.json.verdict == "PASS"`** | `test_e2e_tier1.py:208 assert gate_payload["verdict"] == "PASS"` | ✓ |
| **§3 T8-E3 DET 链：3 runs × consistency.py → DET=1.0** | `TestE2eDetChain::test_three_runs_consistency_det_equals_one` `assert payload["value"] == 1.0` + `n_runs == 3` + `inconsistent == []` | ✓ |
| §4 mypy strict / ruff / cov ≥ 80% | 全绿 | ✓ |

---

## 4. T8-E1..E3 用例覆盖（test FINAL §9）

| 用例 ID | 语义 | 测试位置 | 核心断言 | 状态 |
|---|---|---|---|---|
| T8-E1 | 整链 PASS | `TestE2eFullChain` | rc_run==0 → rc_diff==0 → rc_sum==0 → rc_gate==0 + verdict=="PASS" | ✓ |
| T8-E2 | 中断传播（上游 fail → 下游显式 fail / 不掩盖） | `TestE2eFailurePropagation::test_run_failure_short_circuits_chain` | rc_run==1 + `meta["status"]=="failed"` + 无 `merge_report_*.json` + rc_diff==2 | ✓ |
| T8-E3 | DET 完整链 (3 runs × consistency) | `TestE2eDetChain` | 3 runs 全成功 + DET value==1.0 + n_runs==3 + inconsistent==[] | ✓ |

**T8-E2 实施细节**：自纠改用 `FAKE_EXIT_CODE=7` 触发上游失败（替代原文"刻意损坏 patch"），语义等价（上游 fail → 下游显式 fail）。断言更完整（4 个角度：rc=1 / status=failed / 无 merge_report / diff rc=2），强化失败传播契约。**接受**。

---

## 5. fake_merge.sh 修改非破坏性验证

| 关键点 | 验证 |
|---|---|
| 仅追加 `FAKE_MERGED_TREE_DIR` 可选 env（行 11-13 docstring + 行 42-48 实现）| ✓ |
| 未触 FIXTURE 字面量 `cp + rename` 路径（[code-phase-3] §17.3-prime 锁定）| ✓ |
| 未设置该 env 时与原版完全等价 | ✓ 既有 22 unit 用例（含 T3-R8 memory leak + T3-R1 产物齐 + T3-R4 concurrency）全数通过 |
| 实现方式：`cp -R "${FAKE_MERGED_TREE_DIR}/." "$(pwd)/"`（trailing `/.` 防嵌套子目录） | ✓ POSIX 标准，注释明确 |

**判定**：非破坏性扩展，符合 [code-phase-3] FIXTURE 字面量命名锁清单。

---

## 6. Carry-forward 状态（与 Phase 7 对照，未恶化）

| 来源 | 状态 | 处置 |
|---|---|---|
| [code-phase-5] P2-4 git_sha vs model_matrix 多值策略 | DEFERRED | 1-sample fixture 不触发多机数据；Phase 9 nightly job 实测时再决 |
| [code-phase-4] P2-3 `_decision_to_system_decision` 双字段名 e2e 验证 | TRACKED | e2e fixture 用 JSON-mode `decision` 名走 `_decision_tuple` 主分支；fallback 分支由 `TestDualFieldNameFallback` unit 测覆盖；真实 merge CLI 跑通后再决是否统一字段名 |
| SRSR 数据流 (T4-D10) | DEFERRED | 待 plan v3 + yaml `[FOLLOW-UP — auto-SKIP]` marker 守护 |
| 多文件 sample 聚合 ([code-phase-4] P2-1) | DEFERRED | 1-sample fixture 验证 single-sample；Tier-2 抽样矩阵时再处理 |
| WDR hard gate ([code-phase-4]) | TRACKED | yaml 不含 WDR，Tier-2 后启用 |

**未引入新遗留** ✓

---

## 7. 锁清单遵守对照（[code-phase-0..7]）

| 锁条目 | 影响位置 | 验证 |
|---|---|---|
| [code-phase-0] schema frozen / extra=forbid | 未引入新 schema | ✓ |
| [code-phase-0] eval_subprocess_env 唯一 env 工厂 | run.main 内部调用；FAKE_* env 经 monkeypatch.setenv 注入并由 eval_subprocess_env 透传（仅剥 MERGE_DEV + 注入 dummy LLM key） | ✓ |
| [code-phase-1] argparse 互斥 3 子命令上限 | 未触 lock.py | ✓ |
| [code-phase-1] sample sha256 算法 | 未触 | ✓ |
| [code-phase-2] `_apply_patch_to_tree` immutable | 未触 prepare.py | ✓ |
| [code-phase-3] fake_merge.sh 唯一 fake CLI + §17.3-prime FIXTURE 字面量 | 追加 `FAKE_MERGED_TREE_DIR` env 非破坏；FIXTURE cp+rename 路径未动 | ✓ |
| [code-phase-3] `_locate_merge_run_dir` 单子目录假设 | fake_merge.sh 仍只产 1 个 run_id 目录 | ✓ |
| [code-phase-3] run.main 内部驱动 prepare | e2e 4 步而非 5 步与此锁一致 | ✓ |
| [code-phase-4] `_locate_merge_report` lex-last | diff.main 内部使用 | ✓ |
| [code-phase-4] `_ground_truth.load_golden_tree` 唯一入口（针对 scripts/eval/ 主源码） | e2e helper `_extract_golden_tree` 不在主源码范围；为测试 fixture 准备目的，使用 stdlib tarfile + member.isfile() 防护 | ✓（针对主源码契约无冲突；P2 风格改进见 §8） |
| [code-phase-5] `_persist_ci_summary` envelope | 未触 | ✓ |
| [code-phase-5] 18 指标 anchor | 未触 | ✓ |
| [code-phase-6] GateKind / yaml WDR 缺失 / SRSR `[FOLLOW-UP — auto-SKIP]` | e2e 走 PASS 路径与 yaml 当前形态对齐 | ✓ |
| [code-phase-7] consistency.py CLI flags `--runs <r1>+ --metric --output` | T8-E3 直调 `consistency_mod.main([...])`，flags 完全一致 | ✓ |
| [code-phase-7] `_decision_tuple` 双字段名 fallback | e2e fixture 用 `decision` 走主分支；不影响 DET=1.0（3 runs 都返回 `("semantic_merge", "UNKNOWN")` 一致） | ✓ |
| [code-phase-7] `_validate_sample_alignment` 不静默截共集 | e2e 3 runs 都跑 t1-0001，sample 集合完全对齐 | ✓ |

---

## 8. P0 / P1 / P2 问题

**P0**: 无
**P1**: 无
**P2**: 仅 1 个新风格优化（不阻塞，不要求 Phase 9 处理）

| 编号 | 内容 | 文件:行 | 处置建议 |
|---|---|---|---|
| P2-1 (新, 风格) | `_extract_golden_tree` 在 test helper 中直接用 stdlib `tarfile.open` 解 tar，建议改调 `_ground_truth.load_golden_tree(sample_dir)` 复用主源码 path-traversal-safe + content base64 序列化路径 | `test_e2e_tier1.py:42-56` | 自纠表已 self-disclosed 知情决策；属于 test fixture 准备代码（不在 scripts/eval/ 主源码契约范围），不要求 Phase 9 修；后续若 e2e helper 抽包可顺手 |

---

## 9. 计划细节自纠核对

| Executor 自纠 | 评估 |
|---|---|
| e2e 4 步而非 5 步（run.main 内部驱动 prepare）| ✓ 与 [code-phase-3] cmd_run 内部 prepare 调用一致 |
| `FAKE_MERGED_TREE_DIR` env 非破坏扩展 | ✓ 22 unit 用例零回归；FIXTURE 字面量未动 |
| T8-E1 跳过 `lock --update`/`--verify`（已 commit 到 repo + Phase 9 CI 独立守护） | ✓ 合理：lock --verify 由 Phase 9 CI step 显式覆盖 |
| T8-E2 用 FAKE_EXIT_CODE 替代损坏 patch | ✓ 语义等价（上游 fail → 下游显式 fail）+ 4-断言更具体 |

---

## 10. 结论与下一步

**GO** — Phase 8 实施达成 plan §Phase 8 全部 GO 条件 + team-lead 派单 3 个强制要求（verdict=="PASS" / DET=1.0 链路 / 不动 src+主源码）+ T8-E1..E3 全覆盖。无 P0 / P1 / 阻塞性 P2。fake_merge.sh 非破坏扩展通过既有 22 unit 用例零回归验证。

**后续动作**：
- 拷贝 `code/phase-8/v1.md` → `code/phase-8/FINAL.md`
- 追加 `locks/approved-facts.md` 新事实（带 `[code-phase-8]` 标签）
- SendMessage executor 通知 GO + 触发 Phase 9（CI workflow + meta tests）

**Token 提示**：剩 Phase 9 + REPORT。已通知 executor，若 token 紧张可优先 IMPLEMENTATION_REPORT_PARTIAL.md，Phase 9 CI yaml 是 nice-to-have（与派单要求一致）。
