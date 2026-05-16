# gatekeeper-code 审查报告（Phase 6 v1）

> 审查者：gatekeeper-code（team eval-impl）
> 日期：2026-05-15
> 送审 commit：`000d64649d5e0c1eba0b1d6f3c5f0fb45d45ecdf`
> 实施报告：`.multi-agent/eval-impl/code/phase-6/v1.md`
> 计划基准：`.multi-agent/eval-impl/plan/FINAL.md` §Phase 6 + `[plan-amend]`
> 测试契约：`.multi-agent/eval-impl/test/FINAL.md` §7 + `[test-amend]` T6-G6..G11
> 锁清单基准：`.multi-agent/eval-impl/locks/approved-facts.md`

---

## 结论

**通过**

Phase 6 plan GO 5/5 全绿；test FINAL T6-G1..G11 全 11 项契约 100% 对齐（含 [test-amend] 方案 C 完整落地）；T0-S4/S4b/S4c + 4 个补强 schema 用例全 7 项通过；**三项 carry-forward 全部 CLOSED**（[code-phase-2] P2-3 sentinel yaml / [code-phase-5] P2-1 SSER 真公式 / [code-phase-5] P2-2 RR 真公式）；范围严格、7 项细节自纠理由充分；pytest 216/216 / cov 94.52% / mypy strict 29 files 0 err / ruff clean / fork-name-check exit 0 / lock --verify exit 0（local + CI 双模式）。

P0=0 / P1=0 / P2=4（均不阻塞）。

---

## 契约核查表

### plan FINAL §Phase 6 GO 条件

| 契约 | 状态 | 锚点 |
|---|---|---|
| 输出 eval_acceptance_*.json 符合 procedure.md §3.3 | PASS | `cmd_gate:268` write_json + `AcceptanceReport.model_dump(by_alias=True)` |
| 退出码 0=全 pass / 1=任一 hard fail / 2=仅 soft fail（hard 优先） | PASS | `_derive_verdict:200-215` + T6-G1/G5/G6/G11 |
| lock --verify 同步检查 sha 不一致 → warning (local) / error (CI) | PASS | yaml `synced_with_sha:6355be87...` 锚定真实 acceptance.md sha；lock --verify exit 0 双模式现场验证 |
| 单测覆盖 hard fail / soft 退化 / 全绿 / SKIP / hard+soft 并存 | PASS | T6-G1 (PASS) / G5 (hard) / G6 (absolute soft) / G8 (SKIP) / G9 (relative fail) / G10 (relative pass) / G11 (hard+soft) 全覆盖 |
| mypy strict / ruff / fork-check / cov ≥ 80% | PASS | 29 files 0 err / clean / exit 0 / 94.52% |

### test FINAL §7 + [test-amend] 用例对账

| 用例 | 实现位置 | 状态 |
|---|---|---|
| T6-G1（全 pass + PASS + exit 0） | `TestFullPass` (`test_gate.py:172-191`) | PASS |
| T6-G2 P1-4（CRA tighten → soft fail，多 soft 指标路径） | `TestSchemaDriven` (`:199-219`) | PASS |
| T6-G3（gates 含 kind 字段） | `TestKindField` (`:227-240`) | PASS |
| T6-G4（synced_with_sha 透传 acceptance.json.meta） | `TestSyncedShaPassThrough` (`:248-261`) | PASS |
| T6-G5（hard fail → exit 1 + FAIL） | `TestHardFail` (`:269-283`) | PASS |
| T6-G6 v2.1（absolute soft 不达 → exit 2 + NEEDS_REVIEW） | `TestAbsoluteSoftFail` (`:291-308`) | PASS |
| T6-G7（缺 yaml → exit 1） | `TestMissingYaml` (`:316-329`) | PASS |
| T6-G8 v2.1（无 baseline + relative → SKIP + pass=null + skipped_reason） | `TestRelativeSkip` (`:337-363`) — stderr 含 "skipped 1 relative gate(s)" | PASS |
| T6-G9 v2.1（baseline + cost > 1.15× → exit 2 + baseline_value/computed_threshold/value 字段） | `TestRelativeBreach` (`:371-403`) | PASS |
| T6-G10 v2.1（baseline + cost 在 1.15× 内 → exit 0） | `TestRelativePass` (`:411-441`) | PASS |
| T6-G11 v2.1（hard fail + soft fail → exit 1 hard 优先） | `TestHardOverridesSoft` (`:449-468`) | PASS |
| T0-S4 absolute/relative 双向接受 + kind discriminator | `test_t0_s4_relative_soft_gate_accepts_multiplier` + `test_t0_s4_absolute_soft_gate_accepts_threshold` | PASS |
| T0-S4b（非法 kind 拒绝） | `test_t0_s4b_kind_must_be_absolute_or_relative` | PASS |
| T0-S4c（relative 缺 multiplier） | `test_t0_s4c_relative_kind_requires_multiplier` + 4 个边界（absolute 缺 threshold / absolute 不允许 multiplier / relative 不允许 threshold） | PASS |

11/11 T6 + 7 T0-S4 全部对账绿；额外 12 个补强（committed yaml smoke / `_operator_passes` 5 路径 / `_derive_verdict` 优先级 3 路径 / `parse_metric_table` round-trip / SSER 3 路径 / RR 3 路径 / `cmd_gate` missing report 异常）。

总用例数：31（Phase 0-5 185 + Phase 6 31 = 216）。

### Carry-forward 闭环验证

| 来源 | 处置 | 状态 |
|---|---|---|
| [code-phase-2] P2-3 sentinel acceptance_yaml | `lock.cmd_verify(acceptance_yaml: Path | None = None)` 显式可选；`prepare.cmd_prepare` 传 `None`；35 lock+prepare 测试零回归 | **CLOSED** |
| [code-phase-5] P2-1 SSER 语义偏差（"占比"而非"escalation"） | 按 metrics.md §3.2 重写 `_compute_sser` = `escalated_to_human / sensitive_total`；vacuous PASS 当分母 0；3 用例守护（none / escalated / not-escalated） | **CLOSED** |
| [code-phase-5] P2-2 RR 硬编码 1.0 | 按 metrics.md §5.3 重写 `_compute_rr`：检查 runs/<id>/ 三件产物（merge_report_*.json + .md + plan_review_*.md）非空；3 用例守护 | **CLOSED** |
| [code-phase-4] MISS_FORK→MISS_UPSTREAM 简化 / WDR 永远 0 | yaml 故意缺 WDR + 注释充分；`TestCommittedYaml` 显式断言 `"WDR" not in ids_hard` 守护"未来不会无意中加回去" | TRACKED |
| [code-phase-5] P2-4 git_sha vs model_matrix 策略 | 本期 deferred（非强制 carry-forward） | DEFERRED |
| T4-D10 SRSR | summarize 占位 "N/A (follow-up)" 维持；yaml SRSR hard threshold=1.0 == 1.0 但 summarize 输出 non-numeric → gate 走 SKIP（建议 yaml 给 SRSR 加 follow-up 注释，见 P2-4） | DEFERRED |

### Approved-facts 锁清单遵守

| 锁条目 | 验证 |
|---|---|
| [code-phase-0] `_schemas` frozen / extra=forbid | `AcceptanceThresholdEntry` 扩 kind/multiplier 保持 `_FROZEN`；GateResult 5 个新字段全 frozen ✓ |
| [code-phase-1] `cmd_update_acceptance_sync` 不动 thresholds 子树 | 未修改 cmd_update_acceptance_sync；yaml 通过手动 + 一次 `--update-acceptance-sync` 同步 sha；T1-L7 仍守护 ✓ |
| [code-phase-1] argparse 4 子命令互斥不得加第 4 个 | 未触碰 lock argparse 互斥 group ✓ |
| [code-phase-1] sample sha 算法 / ARTIFACT_FILES | 未修改；tier{1,2,3}.lock.json 用 cmd_update 跑出，与算法一致 ✓ |
| [code-phase-2] `_apply_patch_to_tree` immutable | 未触碰 ✓ |
| [code-phase-3] eval_subprocess_env 唯一 env 工厂 | 本期不涉及 subprocess ✓ |
| [code-phase-3] _persist_ci_summary 包装层 | 未触碰；Phase 5 已闭环 ✓ |
| [code-phase-4] diff_against_golden 不解 tar | 未触碰 ✓ |
| [code-phase-4] DiffReport / DiffEntry / DiffReportMeta 复用 | 未触碰 ✓ |
| [code-phase-5] _report_render render_report API + StrictUndefined | 未触碰；Phase 5 已锁定 ✓ |
| [code-phase-5] 18 指标 anchor 与 yaml id 同源 | yaml hard 13 + soft 9 = 22 anchor，与模板 anchor 1:1 匹配（WDR 故意缺，已加注释；SRSR 含 follow-up source 描述）✓ |
| [plan-amend] kind: absolute/relative + multiplier | AcceptanceThresholdEntry 实现 + model_validator 双向校验 ✓ |
| [test-amend] T6-G6..G11 用例语义 | 5 个用例 1:1 落地，断言字段精确（pass / kind / baseline_value / computed_threshold / value） ✓ |

---

## 测试结果

- **pytest**：`tests/eval/unit/` 216/216 PASSED（in 1.12s）
- **覆盖率**：`--cov=scripts/eval` 总 94.52%（≥ 80%）
  - `_schemas.py` 100%（155 stmts，新增 kind/multiplier 字段 + model_validator 全覆盖）
  - `_report_render.py` 100%
  - `gate.py` 95%（未覆盖：未知 operator raise 104/106、metric 非 numeric SKIP 116、relative metric 非 numeric SKIP 148、relative baseline non-numeric 166）
  - `summarize.py` 95%（_compute_rr 文件读异常 225-231）
  - 其他模块覆盖率维持 Phase 5 基线
- **mypy**：`scripts tests/eval` strict, 29 files, 0 error
- **ruff check**：All checks passed
- **ruff format --check**：29 files already formatted
- **fork-name-check**：`python -m scripts.eval._fork_name_check scripts/eval tests/eval` exit 0
- **lock --verify**：本地 exit 0；`CI=true` exit 0（双模式同步 OK，acceptance.md sha 与 yaml synced_with_sha 一致）
- **范围验证**：`git diff HEAD~1 HEAD --stat -- src/ doc/evaluation/ pyproject.toml .multi-agent/ datasets/ fixtures/ <Phase 0/3/4/5 不允许触碰的所有文件>` 输出空 = 0 修改

---

## P0 / P1 / P2 分级问题

### P0（必须修复才通过）
无。

### P1（强烈建议修复）
无。

### P2（可选优化，不阻塞）

1. **`gate.py:124,155` 用 `assert ... is not None` 做 type narrowing**
   - **现状**：`assert entry.threshold is not None # invariant from model_validator` 与 `assert entry.multiplier is not None`。
   - **风险**：python `-O` 模式下 assert 被剥离；虽 model_validator 已守护 runtime 不会真触发，但 robustness 角度仍是脆弱依赖。
   - **建议**：改为 `if entry.threshold is None: raise RuntimeError("BUG: ...")` 或 `cast(float, entry.threshold)`；保留语义同时无视 -O。
   - **锚点**：`scripts/eval/gate.py:124,155`

2. **缺指标 → SKIP 路径未在 test 中显式覆盖**
   - **现状**：`gate.py:116` (absolute) 与 `:148` (relative) 当 `metric.get(entry.id)` 非 numeric（缺失 / "N/A"）时走 SKIP 路径 + `skipped_reason="metric ... not numeric in report"`。covage 报告显示这两行未覆盖。
   - **影响**：生产场景常见（如 SRSR 当前是 "N/A"，DET/CPC 在单 run 也 N/A）；行为合理但无显式 test 守护。
   - **建议**：Phase 7/8 补 1 个用例（构造 report 缺 OA → 验证 gate 输出 `skipped_reason="metric 'OA' not numeric in report"`）。
   - **锚点**：`scripts/eval/gate.py:113-122,146-153`

3. **`summarize.py:147 _compute_rr glob("merge_report_*.json")` 多匹配处理**
   - **现状**：`any(...)` 接受任一 non-empty；与 [code-phase-3] `_locate_merge_run_dir` "单子目录假设"不一致策略。
   - **影响**：当前 fake_merge.sh 单 run 单文件 OK；未来若一 cwd 多 run 会产生奇怪结果。
   - **建议**：Phase 7/8 视实际多 run 场景出现时统一策略（取 lex-last 或 raise）。
   - **锚点**：`scripts/eval/summarize.py:144-151`

4. **acceptance_thresholds.yaml SRSR hard gate 隐式 SKIP**
   - **现状**：yaml SRSR threshold=1.0 == 1.0；summarize 当前输出 SRSR=`"N/A (follow-up)"` (非 numeric) → gate.py `_evaluate_absolute_gate` 走 SKIP + `skipped_reason="metric 'SRSR' not numeric in report"`，**hard gate SKIP 不影响 verdict**（_derive_verdict 只检查 `passed is False`，None 不算 fail）。
   - **风险**：用户可能误读"SRSR 已 pass"，实际是被 SKIP。yaml source 字段虽含 "(follow-up: requires plan v3 snapshot_rollback_events)"，但未在 acceptance.json verdict 体现。
   - **建议**：Phase 7/8 在 yaml SRSR 条目顶部加 yaml 注释 `# WARNING: SKIPped until plan v3 lands snapshot_rollback_events`；或在 `_derive_verdict` 加 "hard gate skipped" warning 到 stderr。
   - **锚点**：`tests/eval/manifests/acceptance_thresholds.yaml:36-40` + `scripts/eval/gate.py:_derive_verdict`

---

## 残留风险（含 carry-forward）

| ID | 风险 | 影响 | 处置 |
|---|---|---|---|
| RR1 | WDR hard gate 故意缺 | Phase 6 yaml 无 WDR；待 Tier-2 真正区分 MISS_FORK 后启用 | TRACKED + `TestCommittedYaml` 显式守护 |
| RR2 | SRSR hard gate 隐式 SKIP | gate.py 不把 hard SKIP 算 FAIL；用户可能误读 PASS | P2-4 列下 Phase 视情况显式化 |
| RR3 | 缺指标 → SKIP 路径无显式 test | 生产场景常见但行为未守护 | P2-2 列下 Phase 补 1 用例 |
| RR4 | `_compute_rr` glob 多匹配策略不一致 | 单 run 场景 OK | P2-3 列下 Phase 统一策略 |
| RR5 | [code-phase-5] P2-4 git_sha vs model_matrix | DEFERRED | 非强制 carry-forward |
| RR6 | T4-D10 SRSR data flow | 待 plan v3 | DEFERRED |
| RR7 | [code-phase-4] P2-1 多文件 sample 聚合 | 当前 single-file fixtures 不触发 | DEFERRED |

---

## 计划自纠评估

| 自纠项 | 评估 | 接受度 |
|---|---|---|
| GateKind HARD/SOFT → ABSOLUTE/RELATIVE | [test-amend] T6-G6/G8 明确期望；hard/soft 分组由 AcceptanceReport.hard_gates/soft_gates tuple 表达更结构化 | **接受** |
| GateResult 5 字段可空（value/threshold/operator/passed/multiplier/baseline_value/computed_threshold/skipped_reason） | SKIP 行必须 pass=null + value 可空 | **接受** |
| `**{"pass":...}` + `# type: ignore[arg-type]` | pydantic v2 mypy plugin alias 字段限制；维持 alias 解包是 [code-phase-0] T0-S4 已锁定的契约 | **接受** |
| yaml hard 13（WDR 故意缺） | [code-phase-4] MISS_FORK 简化的逻辑后果；注释充分 + TestCommittedYaml 守护 | **接受** |
| RR 按 metrics.md §5.3 + plan_review_*.md 命名 | 遵循 [plan] 真实产物名锁清单 | **接受** |
| SSER vacuous PASS（分母 0 → 1.0） | 避免 0/0 undefined；与 acceptance.md §1 "0 violation = pass" 读法一致 | **接受** |
| 多文件聚合 / git_sha vs model_matrix deferred | 本期已聚焦 5 项强制 carry-forward；范围控制 | **接受** |

---

## 副作用核查

`git diff --name-only HEAD~1 HEAD` 列出 12 个文件：

- `scripts/eval/gate.py`（新建）
- `scripts/eval/_schemas.py`（修改 +94 行：AcceptanceThresholdEntry 扩 kind/multiplier + model_validator；GateKind 重定义；GateResult 5 字段可空）
- `scripts/eval/lock.py`（修改 净 +23 行：cmd_verify acceptance_yaml: Path | None，carry-forward CLOSED）
- `scripts/eval/prepare.py`（修改 净 +7 行：cmd_prepare 移除 sentinel，carry-forward CLOSED）
- `scripts/eval/summarize.py`（修改 +58 行：_compute_sser + _compute_rr 真公式，carry-forward CLOSED）
- `tests/eval/manifests/acceptance_thresholds.yaml`（新建 128 行）
- `tests/eval/manifests/tier{1,2,3}.lock.json`（新建 3 个 lock manifest）
- `tests/eval/unit/test_gate.py`（新建 557 行 / 18 用例）
- `tests/eval/unit/test_schemas.py`（修改 +78 行 / +7 用例）
- `tests/eval/unit/test_summarize.py`（修改 +71 行 / +6 用例）

**未触碰**：`src/` / `doc/evaluation/` / `pyproject.toml` / `.multi-agent/` / `.github/workflows/` / `tests/eval/datasets/` / `tests/eval/fixtures/` / Phase 0/3/4 已交付的核心源文件（_ground_truth / _ast_equiv / diff_against_golden / run / _common / _fork_name_check / __init__ / _report_render）+ 全部 Phase 0/2/3/4/5 测试与 fixture。
**未引入新运行时依赖**：`yaml` 已 pre-existing；`re` / `datetime` / `argparse` / `sys` 均 stdlib。

合规。

---

## 二审及之后

本轮为首审，无上轮反馈核查项。

---

## 通过决定

✅ Phase 6 v1 通过审查。
- copy `v1.md` 到 `code/phase-6/FINAL.md`
- 追加 8 条新事实到锁清单（带 `[code-phase-6]` 标签，含 3 项 carry-forward CLOSED / GateKind 重定义 / AcceptanceThresholdEntry kind+multiplier 双向校验 / GateResult 8 字段契约 / acceptance_thresholds.yaml hard 13+soft 9 锁定 / tier{N}.lock.json commit 入仓 / lock --verify CI 双模式 / 测试基线 216 用例）+ Carry-forward 待办段更新（Phase 7/8）
- 通知 executor + team-lead，可继续 Phase 7（consistency.py）
