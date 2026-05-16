# Phase 8 实施报告 v1

## commit
`2854a82` — feat(eval): Phase 8 — e2e integration test (Tier-1)

## 新增/修改文件清单

### tests/eval/
- `tests/eval/integration/__init__.py` — 新增（package marker）
- `tests/eval/integration/test_e2e_tier1.py` — 新增 240 行（T8-E1 / T8-E2 / T8-E3 三用例 + 共享 helper）

### tests/eval/fixtures/
- `tests/eval/fixtures/fake_merge_bin/fake_merge.sh` — 修改 14 行（新增 `FAKE_MERGED_TREE_DIR` env，可选 overlay 一个目录到 cwd 模拟合并后 working tree；不影响 Phase 3 现有 22 用例）

合计 3 文件改动 / 254 行新增 / 0 行删除。**未触碰 `src/`、`doc/evaluation/`、`pyproject.toml`、`scripts/eval/*` 主源码、`.github/workflows/*`。**

## 测试结果

```
pytest tests/eval/ —— 233 passed in 1.92s（Phase 0-7 230 + Phase 8 3）
pytest --cov=scripts/eval —— 94.50% (PASS, 阈值 80%)
  - scripts/eval/__init__.py             100%
  - scripts/eval/_ast_equiv.py            94%
  - scripts/eval/_common.py               94%
  - scripts/eval/_fork_name_check.py      90%
  - scripts/eval/_ground_truth.py         94%
  - scripts/eval/_report_render.py       100%
  - scripts/eval/_schemas.py             100%
  - scripts/eval/consistency.py           96%
  - scripts/eval/diff_against_golden.py   96%
  - scripts/eval/gate.py                  95%
  - scripts/eval/lock.py                  94%
  - scripts/eval/prepare.py               91%
  - scripts/eval/run.py                   93%
  - scripts/eval/summarize.py             94%
mypy scripts/eval tests/eval —— Success: no issues found in 32 source files (strict)
ruff check scripts/eval tests/eval —— All checks passed
ruff format --check scripts/eval tests/eval —— 32 files already formatted
python -m scripts.eval._fork_name_check scripts/eval tests/eval —— exit 0
```

## 契约对齐

| 契约项 | 来源 | 实施位置 | 状态 |
|---|---|---|---|
| 单测试函数串起 prepare → run → diff → summarize → gate 全链 | plan §Phase 8 GO + dispatch §GO §1 | `TestE2eFullChain::test_chain_lands_pass_verdict` 显式调 `run.main → diff.main → summarize.main → gate.main` | OK |
| 断言 `eval_acceptance_*.json.verdict == "PASS"` | dispatch §GO §2（强制） | `TestE2eFullChain` 末尾 `assert gate_payload["verdict"] == "PASS"` | OK |
| T8-E3 DET 链：3 runs × consistency.py → DET=1.0 | dispatch §GO §3（强制）+ test FINAL §9 T8-E3 | `TestE2eDetChain::test_three_runs_consistency_det_equals_one` 3 次循环跑 run → consistency；断言 `value == 1.0` + `inconsistent == []` | OK |
| 复用 [code-phase-3] fake_merge.sh + [code-phase-7] consistency.py CLI | dispatch §强制 | `FAKE_MERGE_BIN = .../fake_merge_bin/fake_merge.sh`；`consistency_mod.main([...])` 直调 | OK |
| fixture 走 [test] §17.3-prime 字面量 FIXTURE 文件名约定（禁止 glob fallback） | dispatch §强制 | 未引入新 fixture 文件名规则；沿用 [code-phase-3] FIXTURE 字面量；fake_merge.sh 仍 `cp ${FIXTURE_BASE}/merge_report_FIXTURE.json` | OK |
| 链中任一步失败 → e2e 显式 fail（不掩盖） | test FINAL §9 T8-E2 + v1.md ll.580-583 | `TestE2eFailurePropagation::test_run_failure_short_circuits_chain` 用 `FAKE_EXIT_CODE=7` 强制失败；run.py rc=1 + diff rc=2 + run_meta.status="failed" + 无 merge_report_*.json 全断言 | OK |
| mypy strict / ruff / cov ≥ 80% 全绿 | dispatch §GO §4 | 233/233 PASS, cov 94.50%, mypy 32 files 0 err, ruff clean, fork-check exit 0 | OK |

### Approved-facts 锁清单遵守对照

| 锁条目 | 实施侧验证 |
|---|---|
| [code-phase-0] `_schemas` 模型 frozen / extra=forbid | 未引入新 schema |
| [code-phase-0] `eval_subprocess_env` 是唯一 env 工厂 | 通过 `run.main` 间接调用；FAKE_* env 由 monkeypatch.setenv 注入，经 `eval_subprocess_env(use_real_keys=False)` 透传（仅剥 MERGE_DEV + 注入 dummy LLM key） |
| [code-phase-1] argparse 互斥 3 子命令 | 未触碰 lock.py |
| [code-phase-1] sample sha256 算法 | 未触碰 |
| [code-phase-2] `_apply_patch_to_tree` immutable | 未触碰 |
| [code-phase-3] fake_merge.sh 是唯一 fake CLI 实现 + FIXTURE 字面量命名 | 在原脚本上**追加** `FAKE_MERGED_TREE_DIR` env（非破坏：未设置时行为完全等价于原版；既有 22 unit tests 全数通过） |
| [code-phase-3] `_locate_merge_run_dir` 单子目录假设 | fake_merge.sh 仍只产 1 个 run_id 目录 |
| [code-phase-4] `_locate_merge_report` lex-last 策略 | diff.main 内部使用；e2e 仅依赖 rc + 输出 |
| [code-phase-4] diff_against_golden 不解 tar | e2e helper `_extract_golden_tree` 解 tar 仅用于 test fixture 准备（不在 scripts/eval/ 主源码） |
| [code-phase-5] `_persist_ci_summary` 包装层 | 未触碰；e2e 不直接读 ci_summary.json |
| [code-phase-5] 18 指标 anchor | 未触碰 |
| [code-phase-6] gate.py exit code 三态 | e2e 期望 rc=0 + verdict=PASS（命中 yaml 全 PASS 路径） |
| [code-phase-6] yaml WDR 缺失 / SRSR 隐式 SKIP | e2e gate 走 PASS 路径需要 yaml 不含 WDR + SRSR auto-SKIP；已对齐 |
| [code-phase-7] consistency.py CLI flags | T8-E3 直接调用 `consistency_mod.main([..., "--metric", "DET", "--output", ...])` |
| [code-phase-7] `_decision_tuple` 双字段名 fallback | e2e fixture 使用 `decision` + 无 `target_risk_level` → fallback 到 "UNKNOWN"；不影响 DET=1.0 链路（3 runs 都返回 ("semantic_merge", "UNKNOWN")，一致） |
| [code-phase-7] `_validate_sample_alignment` 不静默截共集 | e2e 3 runs 都跑 t1-0001，sample 集合完全对齐 |

## 计划细节自纠

| 计划原文 | 实际 | 采用理由 | 锚点 |
|---|---|---|---|
| Phase 8 e2e "5 步" prepare → run → diff → summarize → gate | 实际 4 步：run.main 已内部调用 prepare.cmd_prepare（[code-phase-3] `cmd_run:263-271` 显式调），所以 e2e 只需 run / diff / summarize / gate 四个独立调用 | 避免重复跑 prepare；与 plan §Phase 3 已锁定的 "run.py 内部驱动 prepare" 一致 | `tests/eval/integration/test_e2e_tier1.py:118-156` |
| dispatch "fake merge-bin 用 Phase 3 已有的 fake fixture 风格" | 在 fake_merge.sh 上**追加** `FAKE_MERGED_TREE_DIR` env（非破坏） | 用 1-sample dummy fixture 的 working tree 与 golden tree 不一致（fixture 只产 merge_report 不改 cwd 文件），所以默认 WMR/DCRR 都失败、verdict=HARD_FAIL。需要 fake CLI 在 cwd 写出"合并后"的 working tree 才能让 diff classify 为 EXACT/SEMANTIC + label=None → gate verdict=PASS。最小破坏方案 = 加可选 env 跳过既有 22 用例（未设置时行为不变） | `fake_merge.sh:11-13,42-48` + `test_e2e_tier1.py:42-56` |
| T8-E1 步骤含 `lock.py --update` + `lock.py --verify` | 本期不在 e2e 串 lock --update / --verify | tier1.lock.json 已在 Phase 6 commit 到 repo（不需 e2e 重生成）；--verify 由 Phase 9 CI step 独立守护，e2e 关注的是产物链路连通性 | n/a |
| dispatch "T8-E2 与 v1 一致"（test FINAL §9 line 580-583 略） | 实现：FAKE_EXIT_CODE=7 → run rc=1 + 无 merge_report → diff rc=2 | v1 原文 "刻意把 prepare.py 给损坏 patch" 在统一 e2e harness 下不可行（run 内置 prepare）；改用 FAKE_EXIT_CODE 切到同一失败传播路径（"上游失败 → 下游显式 fail / 不掩盖"）；语义等价 | `test_e2e_tier1.py:192-220` |

## 范围合规

- 未修改 `src/` 任何代码
- 未修改 `doc/evaluation/{README,metrics,dataset,procedure,acceptance}.md`
- 未修改 `pyproject.toml` 任何字符
- 未修改 `scripts/eval/` 任何主源码（consistency/gate/summarize/run/prepare/diff/lock 全部 untouched）
- 未修改 `.github/workflows/*`（Phase 9 才动）
- 未在代码出现 `cvte` / `dify` / `insforge`（fork-name-check exit 0）
- 未新增运行时依赖（全 stdlib：tarfile / os / json / pathlib）
- 未 `git add -A`，所有 add 都是显式文件清单（3 文件）
- 未跳过 pre-commit hook
- 未超出 Phase 8 范围（CI yaml 是 Phase 9）

## Phase 9 续接锚点

Phase 9 (`.github/workflows/ci.yml` + meta tests) 直接可用：

- `tests/eval/integration/test_e2e_tier1.py` 已落地；Phase 9 CI 的 `pytest tests/eval/unit/ tests/eval/integration/` step 会自动跑此 e2e
- `fake_merge.sh` 已加 `FAKE_MERGED_TREE_DIR` env；Phase 9 manual `eval-tier1` job 可复用同样模式跑真实 sample
- 现有 230 unit + 3 integration 跑总耗时 ~2s，远低于 plan §Phase 9 GO §1 "PR 时长 ≤ 30s" 上限

**已知遗留（与 Phase 6-7 一致，未在 Phase 8 引入新遗留）**：
- [code-phase-5] P2-4 git_sha vs model_matrix 多值策略 — 1-sample fixture 不触发
- [code-phase-4] P2-3 双字段名兜底 — e2e fixture 用 `decision` (JSON-mode 名)，与 [code-phase-7] `_decision_tuple` fallback 兼容；真实 merge CLI 跑通后再决是否统一字段名
- SRSR 数据流（待 plan v3）/ WDR hard gate（Tier-2 后）/ tree-sitter 真实 AST / `--baseline` 数值 delta
