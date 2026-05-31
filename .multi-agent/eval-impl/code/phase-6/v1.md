# Phase 6 实施报告 v1

## commit
`000d64649d5e0c1eba0b1d6f3c5f0fb45d45ecdf` — feat(eval): Phase 6 — gate.py + acceptance_thresholds.yaml (kind absolute/relative)

## 新增/修改文件清单

### scripts/eval/
- `scripts/eval/gate.py` — 新增 313 行（≤ 350 上限）
- `scripts/eval/_schemas.py` — 修改（AcceptanceThresholdEntry 扩 kind+multiplier+model_validator；GateKind HARD/SOFT → ABSOLUTE/RELATIVE；GateResult 扩 5 字段 + passed 可空）
- `scripts/eval/lock.py` — 修改（cmd_verify acceptance_yaml: Path | None；Phase 2 carry-forward P2-3 CLOSED）
- `scripts/eval/prepare.py` — 修改（cmd_prepare 移除 sentinel 路径，传 None）
- `scripts/eval/summarize.py` — 修改（_compute_sser + _compute_rr 真公式；Phase 5 carry-forward P2-1/P2-2 CLOSED）

### tests/eval/
- `tests/eval/manifests/acceptance_thresholds.yaml` — 新增 128 行（hard 13 + soft 9；WDR 注释为后续）
- `tests/eval/manifests/tier{1,2,3}.lock.json` — 新增（lock --update 首次落盘，CI 可开箱跑 --verify）
- `tests/eval/unit/test_gate.py` — 新增 557 行（18 用例：T6-G1..G11 全覆盖 + committed-yaml smoke + 内部 helper）
- `tests/eval/unit/test_schemas.py` — 修改（+7 用例：T0-S4 / S4b / S4c / 3 个 kind 组合边界）
- `tests/eval/unit/test_summarize.py` — 修改（+6 用例：SSER 3 路径 + RR 3 路径守护新公式）

合计 12 文件改动 / 1326 行新增 / 35 行删除。**未触碰 src/、doc/evaluation/、pyproject.toml、Phase 0/1/3/4 已交付的 35 文件中的非允许部分。**

## 测试结果

```
pytest tests/eval/unit/ —— 216 passed in 0.77s（185 from Phase 0-5 + 31 new）
pytest --cov=scripts/eval --cov-fail-under=80 —— 94.53% (PASS)
  - scripts/eval/__init__.py             100%
  - scripts/eval/_ast_equiv.py            94%
  - scripts/eval/_common.py               94%
  - scripts/eval/_fork_name_check.py      90%
  - scripts/eval/_ground_truth.py         94%
  - scripts/eval/_report_render.py       100%
  - scripts/eval/_schemas.py             100%
  - scripts/eval/diff_against_golden.py   96%
  - scripts/eval/gate.py                  95%  (新增)
  - scripts/eval/lock.py                  94%
  - scripts/eval/prepare.py               91%
  - scripts/eval/run.py                   93%
  - scripts/eval/summarize.py             95%
mypy scripts tests/eval —— Success: no issues found in 29 source files (strict)
ruff check scripts tests/eval —— All checks passed
ruff format --check scripts tests/eval —— 29 files already formatted
python -m scripts.eval._fork_name_check scripts/eval tests/eval —— exit 0
python -m scripts.eval.lock --verify —— exit 0 (local mode)
CI=true python -m scripts.eval.lock --verify —— exit 0 (CI mode)
```

## 契约对齐

| 契约项 | 来源 | 实施位置 | 状态 |
|---|---|---|---|
| 输出 eval_acceptance_*.json 符合 procedure.md §3.3 | plan §Phase 6 GO §1 | `cmd_gate` 写 `AcceptanceReport.model_dump(by_alias=True)` | OK |
| 退出码 0=全 pass / 1=hard fail / 2=仅 soft fail | plan §Phase 6 GO §2 + [test-amend] T6-G11 | `_derive_verdict` hard 优先；T6-G1/G5/G6/G11 四向覆盖 | OK |
| lock --verify 同步检查 sha 不一致 → warning（local）/ error（CI）| plan §Phase 6 GO §3 + [code-phase-1] | acceptance_thresholds.yaml synced_with_sha 锚定真实 sha；lock --verify exit 0 双模式验证 | OK |
| 单测覆盖 hard fail / soft 退化 / 全绿 / baseline 缺失 SKIP / hard+soft 并存 | plan §Phase 6 GO §4 + [test-amend] | T6-G1 (PASS) / G5 (hard) / G6 (absolute soft) / G8 (SKIP) / G9 (relative fail) / G10 (relative pass) / G11 (hard+soft) 全覆盖 | OK |
| mypy strict / ruff / fork-check / cov ≥ 80% | plan §Phase 6 GO §5 + Phase 0-5 标准 | 全绿 | OK |
| soft gate kind ∈ {absolute, relative}；relative 必填 multiplier；absolute 必填 threshold | [plan-amend] / [test-amend] T0-S4 / S4b / S4c | `AcceptanceThresholdEntry` model_validator 双向；7 schema 测试守护 | OK |
| gates[].kind 字段语义 = absolute / relative | [test-amend] T6-G6/G8 | GateKind 重定义为 ABSOLUTE/RELATIVE；TestKindField 验证 | OK |
| gates[id=...].pass=null + skipped_reason="no baseline" + computed_threshold + baseline_value | [test-amend] T6-G8/G9 | GateResult 扩展 5 字段；T6-G8/G9 验证 | OK |
| 18 指标 anchor 与 yaml id 同源 | [code-phase-5] | yaml hard 13 (含 SRSR + 6 Recall + 不含 WDR) + soft 9 (含 cost/wall_time/plan_revision relative)；TestCommittedYaml 守护 | OK |
| acceptance_thresholds_sha 透传到 acceptance.json.meta | Verifier T6-G4 | `cmd_gate` 写 `datasets={"acceptance_thresholds_sha": ...}`；TestSyncedShaPassThrough 验证 | OK |
| missing yaml → exit 1 + 清晰报错 | Verifier T6-G7 | `load_thresholds` raise FileNotFoundError；TestMissingYaml 验证 | OK |
| CRA 替代 OA 验证 schema-driven 多指标路径 | Verifier T6-G2 (P1-4 修订) | TestSchemaDriven 用 CRA tighten 触发 soft fail | OK |

### Carry-forward 闭环

| 来源 | 处置 | 状态 |
|---|---|---|
| [code-phase-2] P2-3 sentinel acceptance_yaml | `lock.cmd_verify(acceptance_yaml: Path | None = None)`；prepare 传 None；现有 test_lock / test_prepare 35 用例零回归 | CLOSED |
| [code-phase-5] P2-1 SSER 语义偏差（实际是"占比"而非"escalation"） | 按 metrics.md §3.2 重写：`_compute_sser` = (sensitive 且 human 的样本) / (sensitive 样本)；vacuous PASS 当分母 = 0；3 用例守护 | CLOSED |
| [code-phase-5] P2-2 RR 硬编码 1.0 | 按 metrics.md §5.3 重写：`_compute_rr` 检查 runs/<id>/ 内三件产物（merge_report_*.json + .md + plan_review_*.md）；3 用例守护 | CLOSED |
| [code-phase-4] WDR/MISS_FORK 简化 | yaml 故意不放 WDR hard gate，加注释说明 Tier-2 follow-up；TestCommittedYaml 显式断言 `"WDR" not in ids_hard` | TRACKED |
| [code-phase-5] P2-4 git_sha vs model_matrix 多值策略 | 本期未顺手修（不在 carry-forward 强制要求）；Phase 7/8 顺手 | DEFERRED |
| T4-D10 SRSR | summarize.py 仍占位 "N/A (follow-up)"；待 plan v3 落地 MergeState.snapshot_rollback_events | DEFERRED |

### Approved-facts 锁清单遵守对照

| 锁条目 | 实施侧验证 |
|---|---|
| [code-phase-0] `_schemas` 模型 frozen / extra=forbid | 新增 `AcceptanceThresholdEntry.kind/multiplier` 全保持 `_FROZEN`；GateResult 仍 frozen |
| [code-phase-1] `cmd_update_acceptance_sync` 不触碰 thresholds 子树 | 未修改 cmd_update_acceptance_sync；Phase 6 yaml 通过手动落盘 + 跑一次 `--update-acceptance-sync` 同步 sha |
| [code-phase-1] argparse 互斥 3 子命令（不得加第 4 个）| 未触碰 lock argparse 互斥 group |
| [code-phase-2] `_apply_patch_to_tree` 已 immutable | 未触碰 Phase 4 的重构成果 |
| [code-phase-3] eval_subprocess_env 是 Phase 3 唯一 env 工厂 | 本期不涉及 subprocess |
| [code-phase-4] diff_against_golden 不解 tar | 未触碰 |
| [code-phase-5] _persist_ci_summary 包装层 Phase 5 已识别 | 未触碰 |
| [plan-amend] yaml soft_gates kind 字段 | AcceptanceThresholdEntry 实现 |
| [test-amend] T6-G6..G11 用例语义 | T6-G6/G8/G9/G10/G11 全用例 1:1 落地，断言字段精确 |

## 计划细节自纠

| 计划原文 | 实际 | 采用理由 | 锚点 |
|---|---|---|---|
| `GateKind` enum 含 HARD/SOFT | 重定义为 ABSOLUTE/RELATIVE | [test-amend] T6-G6/G8 显式期望 `gates[id=...].kind == "absolute"|"relative"`。Hard/soft 分组已由 `AcceptanceReport.hard_gates` vs `soft_gates` tuple 表达，独立 `GateKind` 字段重复且冲突 Verifier 期望 | `_schemas.py:131-141` |
| `GateResult` 必填 value/threshold/operator/passed | 全改为可空 | T6-G8 SKIP 路径 `pass=null + value` 存在但 `threshold/operator` 取决于 kind；relative gate 没有 threshold 概念。可空才能 schema-验证 SKIP 行 | `_schemas.py:156-185` |
| `**{"pass": ...}` 解包 | 加 `# type: ignore[arg-type]` 而非改 `passed=...` kw | pydantic v2 mypy plugin 看到 alias 字段时同时拒绝两边（解包推断不到 alias / kw 名 mismatch）。维持 alias 解包 + type ignore 是最不破坏 alias 测试的方案 | `gate.py:131,174` + 3 处测试 |
| acceptance_thresholds.yaml 完整 18 指标 hard+soft | 实际 hard 13 (WMR/SSER/DCRR/SRSR/MMR/Recall_M1..M6/RR/RCR) + soft 9 = 22 项；WDR 故意缺 | [code-phase-4] MISS_FORK/UPSTREAM 简化 → WDR 永远 0；按 carry-forward 派单要求注释为后续；TestCommittedYaml 显式守护 `"WDR" not in ids_hard` | acceptance_thresholds.yaml + test_gate.py::TestCommittedYaml |
| RR 公式 = 三件产物存在 / 总 run | 实施 `_compute_rr(runs_dir, sample_ids)` glob `merge_report_*.{json,md}` + `plan_review_*.md` + 文件 size > 0 | 与 metrics.md §5.3 一致；plan_review_*.md 命名遵循 [plan] 真实产物名锁清单；runs_dir=None 时退化为 1.0（schema-only 路径，summarize 测试不依赖真实 disk）| `summarize.py:104-130` |
| SSER 公式 = security_sensitive 命中且 human=True / security_sensitive 命中 | 实施 `_compute_sser(samples)`；分母 0 退化为 1.0 | 与 metrics.md §3.2 一致；vacuous PASS 当无 security_sensitive 样本（避免"0/0" undefined）| `summarize.py:79-92` |
| 多 sample 数据集 `_diff_one_sample` 多文件 carry-forward (Phase 4 P2-1) | 本期 deferred | 当前 Tier-1 单样本不触发多文件聚合需求；Phase 7/8 e2e 时再 revisit；当前 single-sample fixture 与产线一致 | n/a |
| `_build_context` git_sha vs model_matrix 多值策略 carry-forward (Phase 5 P2-4) | 本期 deferred | 派单"顺手修"非强制；本期已聚焦 5 项强制 carry-forward，避免范围蔓延 | n/a |

## 范围合规

- 未修改 `src/` 任何代码
- 未修改 `doc/evaluation/{README,metrics,dataset,procedure,acceptance}.md`
- 未修改 `pyproject.toml` 任何字符
- 未修改 Phase 0/1/3/4 已交付的核心文件（仅按 carry-forward 派单允许修改 _schemas / lock / prepare / summarize）
- 未在代码出现 `cvte` / `dify` / `insforge`（fork-name-check exit 0）
- 未新增运行时依赖（`yaml` 已在 pyproject 主依赖；其他全 stdlib）
- 未 `git add -A`，所有 add 都是显式文件清单（12 文件）
- 未跳过 pre-commit hook
- 未超出 Phase 6 范围（`consistency.py` / `e2e` 是 Phase 7+）

## Phase 7 续接锚点

Phase 7 (`consistency.py`) 直接可用：

- `scripts.eval.gate.cmd_gate(...)` — Phase 8 e2e 可调用产生 acceptance.json
- `scripts.eval.gate.parse_metric_table(markdown)` — 通用 metric 表解析；Phase 7 若需读 report 复用此函数
- `scripts.eval._schemas.{AcceptanceReport, GateResult, GateKind, GateVerdict}` — Phase 7 可输出类似 schema 的一致性报告
- `tests/eval/manifests/acceptance_thresholds.yaml` — 已落 commit；Phase 8 e2e 整链可读
- `_compute_rr` / `_compute_sser` 公式 helper — Phase 7/8 复用

**已知遗留 / 留给后续**：
- **WDR hard gate** — yaml 故意缺；Tier-2 真正区分 MISS_FORK 后重新启用
- **SRSR 数据流** — summarize anchor 仍占位；plan v3 落地 snapshot_rollback_events 后回填
- **多文件 sample 聚合** — Phase 4 P2-1 carry-forward 仍 open
- **git_sha vs model_matrix 策略** — Phase 5 P2-4 carry-forward 仍 open
- **`_persist_ci_summary` raw_value/invalid_json envelope** — Phase 5 已识别，Phase 6 无新影响
