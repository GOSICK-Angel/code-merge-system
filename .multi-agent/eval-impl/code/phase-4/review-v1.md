# gatekeeper-code 审查报告（Phase 4 v1）

> 审查者：gatekeeper-code（team eval-impl）
> 日期：2026-05-15
> 送审 commit：`6d3871e3c93226b9380ed07c088ffa6bee92f6db`
> 实施报告：`.multi-agent/eval-impl/code/phase-4/v1.md`
> 计划基准：`.multi-agent/eval-impl/plan/FINAL.md` §Phase 4
> 测试契约：`.multi-agent/eval-impl/test/FINAL.md` §5 Phase 4（T4-A1..A7 + T4-D1..D9，T4-D10 [test] TR7 follow-up）
> 锁清单基准：`.multi-agent/eval-impl/locks/approved-facts.md`

---

## 结论

**通过**

Phase 4 plan GO 5/5 全绿；test FINAL T4-A1..A7 + T4-D1..D9 共 16 项契约 100% 对齐；T4-D10 SRSR 按 [test] TR7 follow-up 不实现（锁清单明确允许）。Phase 2 P2-1 carry-forward 闭环（`_apply_patch_to_tree` 重构为 `tuple[dict, list]` + immutability test 永久守护）。范围严格、7 项细节自纠理由充分；pytest 156/156 / cov 94.16% / mypy strict 23 files 0 err / ruff clean / fork-name-check exit 0。

P0=0 / P1=0 / P2=3（均不阻塞，carry-forward Phase 5）。

---

## 契约核查表

### plan FINAL §Phase 4 GO 条件

| 契约 | 状态 | 锚点 |
|---|---|---|
| `diff_against_golden.py --runs ... --datasets ... --output` 输出符合 procedure.md §3.2 + 决策 1 扩展 | PASS | DiffReport schema + 3 扩展字段（rationale_length / discarded_content_present / is_security_sensitive）+ T4-D5 |
| per-file 数据来源严格从 `merge_report_<run_id>.json`（[plan] 锁定） | PASS | `_load_decision_records:152-158` 唯一源；T4-D6 decoy `ci_summary.json` 守护契约 |
| tree-sitter import 失败时降级 + `meta.semantic_engine = "fallback-bytes"` | PASS | `_has_tree_sitter` + `_summarise_engine` narrowed to Literal["tree-sitter","fallback-bytes"]；T4-A1/D9 |
| 4 类 label 各 1 用例 | PASS | T4-D1 (MISS_UPSTREAM) / T4-D2 (架构限制走 MISS_UPSTREAM) / T4-D3 (WRONG_MERGE) / T4-D4 (EXTRA_NOISE) + `_escalate_label` 优先级聚合 |
| mypy strict / ruff / fork-check / cov ≥ 80% | PASS | 23 files 0 err / clean / exit 0 / 94.16% |

### test FINAL §5 Phase 4 用例对账

| 用例 | 实现位置 | 状态 |
|---|---|---|
| T4-A1（BOM + CRLF normalise） | `TestFallbackBytes` 2 用例 (`test_ast_equiv.py:22-37`) | PASS |
| T4-A2（fallback **不**去注释） | `TestFallbackNoCommentStripping` 2 用例 (`:45-62`) | PASS |
| T4-A3（JSON / YAML canonical） | `TestCanonicalSerialisers` 4 用例 (`:70-96`) — 含 invalid JSON 强制走 fallback 自纠 | PASS |
| T4-A4（binary 严格字节） | `TestBinarySuffix` 2 用例 (`:104-113`) | PASS |
| T4-A5（tree-sitter engine name） | `TestTreeSitterEngine` 2 用例 (`:121-146`) | PASS |
| T4-A6（未知后缀抛 `UnsupportedFileType`） | `TestUnsupportedSuffix` (`:155-158`) | PASS |
| T4-A7（单边文件不存在抛 `FileNotFoundError`） | `TestFileIOWrapper` 2 用例 (`:167-180`) | PASS |
| T4-D1（MISS_UPSTREAM） | `TestMissUpstream` (`test_diff_against_golden.py:117-147`) | PASS |
| T4-D2（MISS_FORK → MISS_UPSTREAM，架构限制） | `TestMissFork` (`:155-188`) — 测试注释已声明限制 | PASS（自纠合理） |
| T4-D3（WRONG_MERGE） | `TestWrongMerge` (`:196-223`) | PASS |
| T4-D4（EXTRA_NOISE） | `TestExtraNoise` (`:231-260`) | PASS |
| T4-D5（扩展字段：rationale_length / discarded_content_present / is_security_sensitive） | `TestExtensionFields` (`:268-304`) | PASS |
| T4-D6（per-file 真相只读 merge_report，ci_summary 是 decoy） | `TestPerFileSourceContract` (`:312-349`) | PASS |
| T4-D7（缺 merge_report → rc=2） | `TestMissingMergeReport` (`:357-382`) | PASS |
| T4-D8（缺 working_tree → rc=2） | `TestMissingWorkingTree` (`:390-417`) | PASS |
| T4-D9（fallback 模式诚实标注 `meta.semantic_engine`） | `TestSemanticEngineHonesty` (`:425-451`) | PASS |
| T4-D10（SRSR 数据流） | **未实现，[test] TR7 follow-up，本期允许** | DEFERRED |

16/17 用例契约对齐（T4-D10 锁清单明确允许 plan v3 follow-up）；额外 12 个补强（`_walk_tree` posix path / `_locate_merge_report` 多文件 lex-last / `_summarise_engine` 3 路径 / `_escalate_label` 优先级 / arg validation 2 用例 + `_apply_patch_to_tree` immutability 守护）。

总用例数：35（Phase 0 54 + Phase 1 20 + Phase 2 27 + Phase 3 20 + Phase 4 35 = 156）。

### Carry-forward / Carry-over 处置

| 来源 | 处置 | 状态 |
|---|---|---|
| [code-phase-2] P2-1 `_apply_patch_to_tree` mutate 入参 | 重构为 `tuple[dict, list]` return；`_expand_sample` 同步更新；`test_apply_patch_to_tree_does_not_mutate_input` 永久守护 | **CLOSED** |
| [code-phase-3] P2-1 失败 sample 不拷 partial artifacts | diff_against_golden 对失败 sample 走 `RunArtifactMissing` → rc=2 隔离 | OK by design |
| [code-phase-3] P2-2 ci_summary.json 包装层 | Phase 4 本期不读 ci_summary 用于决策（[plan] 锁定 per-file 真相只从 merge_report） | DEFERRED Phase 5 |
| [code-phase-3] P2-3 3 处 type:ignore[arg-type] | 本期未触；Phase 4 新增 1 处 `# type: ignore[import-not-found]`（tree-sitter optional dep 合理） | DEFERRED Phase 5/6 |
| [code-phase-2] P2-3 sentinel acceptance_yaml 路径 | Phase 4 未涉及 yaml | DEFERRED Phase 6 |

### Approved-facts 锁清单遵守

| 锁条目 | 验证 |
|---|---|
| [code-phase-0] `_schemas` 模型 frozen / extra=forbid | 本期未新增 schema 模型，复用 DiffReport / DiffEntry / SystemDecision / DiffReportMeta / MatchStatus / MismatchLabel ✓ |
| [code-phase-0] `_common.write_json` atomic | `cmd_diff:326` write_json + `model_dump(mode="json")` ✓ |
| [code-phase-0] `eval_subprocess_env` 唯一 env 工厂 | Phase 4 不 spawn subprocess ✓ |
| [code-phase-1] `ARTIFACT_FILES` / `_sample_sha256` 未修改 | 未触 ✓ |
| [code-phase-2] 必须复用 `_ground_truth.load_golden_tree`，禁止自解 tar | `_diff_one_sample:186` 调 load_golden_tree；`grep "tarfile" scripts/eval/diff_against_golden.py` = **0 命中** ✓ |
| [code-phase-2] `_apply_patch_to_tree` mutation 警告 | 已 CLOSED（重构 + 守护测试）✓ |
| [code-phase-3] `_locate_merge_run_dir` 单子目录假设 | diff_against_golden 用独立 `_locate_merge_report`（glob `merge_report_*.json` 多匹配取 lex-last 保确定性）—— 与 run.py 单 run 单目录输出兼容 ✓ |
| [test] per-file 真相从 `merge_report_<run_id>.json` 读 | T4-D6 显式守护（ci_summary 写 strategy=TAKE_CURRENT 作为 decoy，merge_report 写 strategy=TAKE_TARGET → diff 取 TAKE_TARGET）✓ |
| [test] semantic_engine 字段 `Literal["tree-sitter","fallback-bytes"]` | `_summarise_engine` 返回类型 narrowed；T4-D9 守护 fallback 模式诚实标注 ✓ |
| [test] T4-D10 SRSR follow-up（plan v3 决策前不阻塞 Phase 6/8） | 本期不实现，锁清单已记录；不引入 SRSR 字段到 DiffEntry 避免破坏 Phase 0-3 22 用例 ✓ |
| [plan] CI stdout 仅 run-level / per-file 必须读 merge_report | 严格遵守 ✓ |
| [plan] 决策 4 P1-5 fallback 仅 BOM + CRLF + 行尾空白，不去注释 | `_normalise_bytes:110-117` 实现 + T4-A2 守护 ✓ |

---

## 测试结果

- **pytest**：`tests/eval/unit/` 156/156 PASSED（in 0.85s）
- **覆盖率**：`--cov=scripts/eval` 总 94.16%（≥ 80%）
  - `_schemas.py` 100%
  - `_common.py` 94% / `_fork_name_check.py` 90% / `_ground_truth.py` 94% / `lock.py` 94% / `prepare.py` 91% / `run.py` 93%
  - `_ast_equiv.py` 94%（未覆盖：tree-sitter ImportError 105、JSON/YAML 解码 fallback 异常路径 165-166、179）
  - `diff_against_golden.py` 96%（未覆盖：phantom None/None 兜底分支 108、UnsupportedFileType 兜底 116-118、records 非 dict 兜底 157、`cmd_diff` datasets 缺失 295-296）
- **mypy**：`scripts tests/eval` strict, 23 files, 0 error
- **ruff check**：All checks passed
- **ruff format --check**：23 files already formatted
- **fork-name-check**：`python -m scripts.eval._fork_name_check scripts/eval tests/eval` exit 0
- **范围验证**：`git diff HEAD~1 HEAD --stat -- src/ doc/evaluation/ pyproject.toml .multi-agent/ datasets/ manifests/ fixtures/` 输出空 = 0 修改

---

## P0 / P1 / P2 分级问题

### P0（必须修复才通过）
无。

### P1（强烈建议修复）
无。

### P2（可选优化，不阻塞）

1. **`_diff_one_sample:212-215` 多文件 sample 仅取第一个 decision record 作 SystemDecision**
   - **现状**：`primary_record = next(iter(decisions.values()), {})` — comment 已声明 "one-sample / one-file fixtures, 真实多文件留 Phase 5"。
   - **影响**：当前 fixtures 都单文件，OK；Tier-3 / Phase 5 多文件场景下 SystemDecision 将无法代表整个 sample。
   - **建议**：Phase 5 summarize 若需多 strategy/risk 聚合，回头改 `_diff_one_sample` 输出 `tuple[DiffEntry, list[SystemDecision]]` 或在 DiffEntry 增加 `per_file_decisions: dict[str, SystemDecision]` 字段。
   - **锚点**：`scripts/eval/diff_against_golden.py:212-215`

2. **`diff_against_golden.py:368-369` `_ = BINARY_SUFFIXES` sentinel 抑制 unused-import**
   - **现状**：BINARY_SUFFIXES 在 diff_against_golden.py 内未直接被引用，但通过 `from scripts.eval._ast_equiv import (...)` 列出在 import 块；仅在测试中被引用。
   - **建议**：直接从 import 列表移除 BINARY_SUFFIXES；测试 import 自己的本地 alias 即可。或者保留并加 docstring 说明"作为公共 API re-export"。
   - **锚点**：`scripts/eval/diff_against_golden.py:42-47,368-369`

3. **`_decision_to_system_decision:161-166` 接受 `decision` / `strategy` 双字段名兼容**
   - **现状**：`record.get("decision") or record.get("strategy")` 兜底；同样 `target_risk_level` 与 `risk` 兜底；`decision_source == "human" or "batch_human"` 硬编码字符串。
   - **影响**：若真实 `MergeState.file_decision_records` 字段是 `decision`（fixture 用此），fallback 永不触发；fallback 路径未被测试。
   - **建议**：Phase 5 / Phase 8 e2e 跑真实 merge → diff 时确认 `decision` 字段名为标准；移除 strategy/risk 兜底或加锁清单记录"接受双名"。
   - **锚点**：`scripts/eval/diff_against_golden.py:161-166`

---

## 残留风险（含 carry-forward）

| ID | 风险 | 影响 | 处置 |
|---|---|---|---|
| RR1 | T4-D10 SRSR 未实现 | 待 plan v3 决策 MergeState.snapshot_rollback_events 字段 | [test] TR7 锁定为 follow-up；不阻塞 Phase 5/6/8 |
| RR2 | MISS_FORK 与 MISS_UPSTREAM 完整区分 | 当前简化为"缺失即 MISS_UPSTREAM" | 留给 Tier-2 历史回放工作（需引入 fork.patch + base tree 三方 oracle） |
| RR3 | [code-phase-3] P2-2 ci_summary 包装层 | Phase 5 必须感知 raw_value / invalid_json | carry-forward Phase 5 |
| RR4 | [code-phase-2] P2-3 sentinel acceptance_yaml | Phase 6 yaml 创建后行为偏离 | carry-forward Phase 6 |
| RR5 | tree-sitter 真实 AST 集成 | 当前 shim 仅保留 engine name | 后续工作，本期 plan 决策 4 已声明 |
| RR6 | `_decision_to_system_decision` 双字段兜底未测试 | Phase 5 e2e 跑真实 merge 时若字段名不一致会沉默漂移 | P2-3 列下 Phase 视情况验证 |

---

## 计划自纠评估

| 自纠项 | 评估 | 接受度 |
|---|---|---|
| `_ast_equiv.py` 200→209 行 | 5 suffix 表 + 5 engine Literal + 3 helpers + docstring 嵌入决策 4；远 ≤ 800 硬上限 | **接受** |
| T4-D2 MISS_FORK 走 MISS_UPSTREAM 分类 | 架构限制（仅 D_sys vs D_gold 两侧无法区分 upstream-only vs fork-only），测试已注释；完整区分留 Tier-2 工作 | **接受** |
| `_summarise_engine` narrowed Literal["tree-sitter","fallback-bytes"] | DiffReportMeta.semantic_engine schema 强制；mypy strict 要求精确 | **接受** |
| `_has_tree_sitter` `# type: ignore[import-not-found]` | tree-sitter 是 optional [ast] extras；CI 默认不装 | **接受** |
| ci_summary.json 包装层不用 | [plan] 锁定 per-file 真相只在 merge_report；T4-D6 守护 | **接受** |
| T4-A5 mock tree-sitter=True 字节相等短路 exact-bytes | 字节相等不需 AST，是更精确的答案；补 CRLF 用例覆盖 tree-sitter engine name | **接受** |
| T4-A3 invalid JSON → fallback：改 CRLF 差异强制走 fallback | 字节相等 EXACT 短路无法触发 JSON parse 失败 | **接受** |

---

## 副作用核查

`git diff --name-only HEAD~1 HEAD` 列出 6 个文件：

- `scripts/eval/_ast_equiv.py`（新建）
- `scripts/eval/diff_against_golden.py`（新建）
- `scripts/eval/prepare.py`（修改 净 +1 行，Phase 2 carry-forward 强制要求）
- `tests/eval/unit/test_ast_equiv.py`（新建）
- `tests/eval/unit/test_diff_against_golden.py`（新建）
- `tests/eval/unit/test_prepare.py`（修改 净 +8 行，加 immutability 测试 + 4 处调用更新）

**未触碰**：`src/` / `doc/evaluation/` / `pyproject.toml` / `.multi-agent/` / `.github/workflows/` / `tests/eval/datasets/` / `tests/eval/manifests/` / `tests/eval/fixtures/` / Phase 0/1/3 已交付的 25 文件中的任何一个。
**未引入新运行时依赖**：`yaml` / `json` / `tarfile` / `pathlib` 均 stdlib 或 pre-existing。

合规。

---

## 二审及之后

本轮为首审，无上轮反馈核查项。

---

## 通过决定

✅ Phase 4 v1 通过审查。
- copy `v1.md` 到 `code/phase-4/FINAL.md`
- 追加 7 条新事实到锁清单（带 `[code-phase-4]` 标签，含 Phase 2 P2-1 carry-forward CLOSED / SemanticEngine 5-元 union 与 meta narrowed 2-元 / `_normalise_bytes` 算法签名 / `_locate_merge_report` lex-last 选择 / per-file 真相契约守护 / MISS_FORK 简化策略 / 测试基线 156 用例）+ Carry-forward 待办段更新（Phase 5 必须感知 ci_summary 包装层）
- 通知 executor + team-lead，可继续 Phase 5（summarize.py + _report_render.py）
