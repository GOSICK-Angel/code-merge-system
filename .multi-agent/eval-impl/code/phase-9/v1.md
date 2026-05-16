# Phase 9 实施报告 v1

## commit
`7687ba7` — feat(eval): Phase 9 — CI workflow + meta tests

## 新增/修改文件清单

### .github/workflows/
- `.github/workflows/ci.yml` — 修改 +57 行：
  - 顶层 `on:` 追加 `workflow_dispatch`（手动触发）+ schedule nightly placeholder 注释
  - `test` job 在 `Unit tests` 后追加 5 个 eval step：`Lint eval scripts (ruff)` / `Type check eval scripts (mypy)` / `Eval unit + e2e tests` / `Verify dataset locks` / `Fork name purity check`
  - 新增 `eval-tier1` job（manual-only，`if: github.event_name != 'pull_request'` + `continue-on-error: true` 双保险不阻塞 PR）

### tests/eval/
- `tests/eval/unit/test_ci_workflow_meta.py` — 新增 220 行（T9-W1..W7 9 用例 meta 测试）

合计 2 文件改动 / 277 行新增 / 0 行删除。**未触碰 `src/`、`doc/evaluation/`、`pyproject.toml`、`scripts/eval/*` 主源码、tests/eval/unit/ 其他文件。**

## 测试结果

```
pytest tests/eval/ —— 242 passed in 2.96s（Phase 0-8 233 + Phase 9 9 meta）
pytest --cov=scripts/eval —— 94.50% (PASS, 阈值 80%)
mypy scripts/eval tests/eval —— Success: no issues found in 33 source files (strict)
ruff check scripts/eval tests/eval —— All checks passed
ruff format --check scripts/eval tests/eval —— 33 files already formatted
python -m scripts.eval._fork_name_check scripts/eval tests/eval —— exit 0
python -m scripts.eval.lock --verify —— exit 0 (local mode)
CI=true python -m scripts.eval.lock --verify —— exit 0 (CI mode)
yaml.safe_load(.github/workflows/ci.yml) —— 解析成功（jobs: web-build / test / eval-tier1）
```

## 契约对齐

| 契约项 | 来源 | 实施位置 | 状态 |
|---|---|---|---|
| `test` job 后追加 5 eval steps | plan §Phase 9 §0 + GO §1 + dispatch §强制 | ci.yml `test` job 顺序：Unit tests → Lint eval → Mypy eval → Eval unit+e2e → Verify locks → Fork name check | OK |
| `--cov=scripts/eval`（独立 cov source） | plan §Phase 9 关键修正 + T9-W2 | `Eval unit + e2e tests` step 用 `pytest tests/eval/unit/ tests/eval/integration/ -v --cov=scripts/eval ...`；显式**不**含 `--cov=src` | OK |
| `mypy scripts/eval tests/eval` 与 `mypy src` 独立 step | plan §Phase 9 + T9-W3 | 2 个独立 step 并存（`Type check (mypy)` = `mypy src`；`Type check eval scripts (mypy)` = `mypy scripts/eval tests/eval`） | OK |
| `eval-tier1` manual-only job | plan §Phase 9 + T9-W5 | `jobs.eval-tier1` 存在；`workflow_dispatch` 在顶层 `on:`；nightly schedule 注释 placeholder | OK |
| 不阻塞 PR（3 OR 条件） | plan §Phase 9 GO §4 + facts.md §11 + T9-W7 | 同时满足 (a) `if: github.event_name != 'pull_request'` + (b) `continue-on-error: true`（双保险） | OK |
| 缺任一 step → meta 测试 fail | T9-W4 | `TestMissingStepDetected` 用 regex 移除 `Verify dataset locks` step + 断言 missing 集合含此 step | OK |
| PR 时长 ≤ 30s（unit 部分） | plan §Phase 9 GO §1 + T9-W6 | unit 套件实测 ~2.5s（远低于 25s 自检阈值，5s 头空间） | OK |
| mypy strict / ruff / cov ≥ 80% 全绿 | plan §Phase 9 + Phase 0-8 标准 | 242 passed / cov 94.50% / mypy 33 files 0 err / ruff clean / fork-check exit 0 | OK |

### Approved-facts 锁清单遵守对照

| 锁条目 | 实施侧验证 |
|---|---|
| [code-phase-0] `_fork_name_check` exit 0 | 新增 `Fork name purity check` step 显式 invoke `python -m scripts.eval._fork_name_check scripts/eval tests/eval` |
| [code-phase-1] `lock.cmd_verify` CI 区分 = `os.environ.get("CI") == "true"` | 新增 `Verify dataset locks` step 不传 `CI` env（GitHub Actions runner 默认设 `CI=true`，自动走 CI 严格模式） |
| [code-phase-1] `cmd_verify` CI 严格模式 = sha 不一致 → error | 本地双模式验证：`lock --verify` 本地 + `CI=true ... --verify` 两种 exit 0 |
| [code-phase-3] eval_subprocess_env 唯一 env 工厂 | 未触碰；e2e step 通过 pytest 间接调用 |
| [code-phase-3] fake_merge.sh 唯一 fake CLI | 未触碰 |
| [code-phase-6] yaml synced_with_sha 锚定 acceptance.md sha | 未触碰；CI verify step 会守护此 sha 一致性 |
| [code-phase-7] consistency.py 独立 CLI | 未触碰；e2e step 间接调用 |

## 计划细节自纠

| 计划原文 | 实际 | 采用理由 | 锚点 |
|---|---|---|---|
| plan §Phase 9 ruff 命令 = `ruff check scripts tests/eval` | 实际 = `ruff check scripts/eval tests/eval`（限定到 `scripts/eval/` 子目录） | scripts/ 根目录目前只含 `eval/`，但显式限定 `scripts/eval` 与 mypy 命令对齐 + 避免未来其他 `scripts/<sibling>/` 子目录被意外 lint | ci.yml `Lint eval scripts (ruff)` step |
| plan §Phase 9 mypy 命令 = `mypy scripts tests/eval` | 实际 = `mypy scripts/eval tests/eval`（限定到子目录） | 同上 + 与 cov source `scripts/eval` 一致；mypy strict 配置只对 `scripts/eval/` 的 frozen pydantic 模型生效 | ci.yml `Type check eval scripts (mypy)` step |
| `eval-tier1` 顶层 `on:` 含 `schedule` | 实际只含 `workflow_dispatch`，nightly schedule 注释保留 placeholder | 当前 fake CLI 不烧实际成本，但避免开启 nightly 触发预算 / 监控 / 通知；待 Tier-1 真实评估稳定后再 uncomment | ci.yml ll.5-11 |
| `eval-tier1` 独立 trigger schedule | 顶层 `on:` 公用（GitHub Actions 不支持 per-job `on:` block） | `on:` 是 workflow-level key，job-level `on:` 是无效语法；用 `if: github.event_name != 'pull_request'` + `continue-on-error: true` 组合达成 "manual-only + non-blocking" 语义 | ci.yml `eval-tier1` job |
| T9-W4 mock 删 step → assert fail | 实现：`re.sub` 字符串级移除 step + assert missing 集合含该 step | yaml 数据结构 round-trip 风险大；regex 移除是最小破坏方案 | `TestMissingStepDetected` |
| T9-W6 unit 时长 ≤ 25s + CI 跳过自递归 | 实现：`pytest.mark.skipif(os.getenv("CI"))` + 调用 `sys.executable` + `--ignore=test_ci_workflow_meta.py` 防自递归死锁 | 单测自身耗时计入；CI 跑就 skip 这一项；本地 ~2.5s 远低阈值 | `TestUnitSuiteRuntime` |
| `on` yaml key 解析 | `yaml.safe_load` 在 YAML 1.1 下把 `on` 当 boolean `True` key | 已知行为；`_workflow_on` helper 兼容 `True` 与 `"on"` 两种 key | `_workflow_on:51-60` |

## 范围合规

- 未修改 `src/` 任何代码
- 未修改 `doc/evaluation/{README,metrics,dataset,procedure,acceptance}.md`
- 未修改 `pyproject.toml`
- 未修改 `scripts/eval/` 任何主源码
- 未修改 tests/eval/unit/ 其他既有用例
- 未在代码出现 `cvte` / `dify` / `insforge`（fork-name-check exit 0）
- 未新增运行时依赖（`yaml` 已在 dev deps；`subprocess` / `time` / `re` 全 stdlib）
- 未 `git add -A`，所有 add 都是显式文件清单（2 文件）
- 未跳过 pre-commit hook
- 未超出 Phase 9 范围（IMPLEMENTATION_REPORT 是最后一步）

## 最终续接锚点

eval-impl Phase 0-9 全部落地。继续 IMPLEMENTATION_REPORT_PARTIAL.md（scope.md §5 大纲：现状 / 已落 deliverable / 测试基线 / 已知 follow-up / 暂未实施 trade-off）。

**最终测试基线**：
- pytest tests/eval/ —— 242 passed in 2.96s（unit 233 + integration 3 + meta 9）
- cov scripts/eval —— 94.50%（每模块 ≥ 90%）
- mypy strict —— 33 source files 0 error
- ruff check / format —— clean
- fork-name-check —— exit 0
- lock --verify 本地 + `CI=true` —— 双模式 exit 0

**遗留 follow-up（不阻塞 GO；详见 [code-phase-4..7] 锁清单 Carry-forward 段）**：
- SRSR 数据流（待 plan v3 `MergeState.snapshot_rollback_events`）
- MISS_FORK 完整区分（Tier-2 真实历史回放）
- WDR hard gate 重启用（Tier-2 后）
- tree-sitter 真实 AST 集成
- `--baseline` 数值 delta（需基线 schema 标准化）
- 多文件 sample 聚合（`_diff_one_sample` 当前仅取第一个 decision record）
- `_decision_to_system_decision` 双字段名兜底统一（待真实 merge CLI 跑通后决定）
- `_build_context` git_sha vs model_matrix 多值策略统一
