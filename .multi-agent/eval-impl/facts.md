# Facts — 评估方案落地（eval-impl）

> 由 main agent 在调研阶段核实，所有 teammate 都基于这些事实工作，**不要自行重新调研改写**。
> 本任务目标：把 `doc/evaluation/{README,metrics,dataset,procedure,acceptance}.md` 描述的评估体系落地为可运行的脚本与目录骨架。

---

## 1. 评估方案文档（不可改）

落地必须**严格对齐** `doc/evaluation/` 已有 5 份文档。任何脚本能力或目录结构与文档不一致时，应优先改脚本而不是改文档。文档锁版本：2026-05-15。

- `doc/evaluation/README.md` — 五维度信任框架、三层评估集、Acceptance Gate
- `doc/evaluation/metrics.md` — 指标公式与数据来源（§1.2 EXACT/SEMANTIC/MISMATCH 判定、§2-§6 各类指标）
- `doc/evaluation/dataset.md` — Tier-1/2/3 目录结构与 lock 机制
- `doc/evaluation/procedure.md` — 命令清单（`scripts/eval/{lock,prepare,run,diff_against_golden,summarize,gate,consistency}.py`）+ 报告 schema
- `doc/evaluation/acceptance.md` — hard/soft gate 阈值表

---

## 2. 仓库当前状态（基线）

| 事实 | 锚点 |
|---|---|
| 仓库根 | `/Users/angel/AI/personal/code-merge-system/` |
| `scripts/` 目录**不存在**——本次需新建 | `ls /repo/` 确认 |
| `tests/` 仅有 `unit/`、`integration/`、`fixtures/`——本次需新建 `tests/eval/` | `tests/__init__.py`、`tests/integration/`、`tests/fixtures/` |
| Python 包入口 | `pyproject.toml:26` `merge = "src.cli.main:cli"` |
| 依赖：`pydantic>=2.5 / gitpython / unidiff / click / pyyaml / jinja2 / python-dotenv / tenacity` | `pyproject.toml:9-23` |
| dev 依赖：`pytest / pytest-asyncio (asyncio_mode=auto) / pytest-mock / pytest-cov / mypy / ruff` | `pyproject.toml:39-46` + `[tool.pytest.ini_options]` |
| Python 版本：3.11+ | `pyproject.toml:8` |
| mypy strict | `pyproject.toml:80-83` |
| 覆盖率门槛 80% | `pyproject.toml:57` + CI `--cov-fail-under=80` |

---

## 3. CLI 与产物路径

| 事实 | 锚点 |
|---|---|
| `merge` 顶层 group | `src/cli/main.py:71 def cli()` |
| `merge merge` 子命令 + 选项 `--ci` / `--no-web` / `--no-tui`（已 deprecated）/ `--dry-run` | `src/cli/main.py:75-148` |
| `merge resume` | `src/cli/main.py:198` |
| `merge validate` | `src/cli/main.py:404` |
| 默认产物路径文档 | `CLAUDE.md` 中"`.merge/` Directory" 节，实测以 `outputs/debug/checkpoints/checkpoint.json` 为准（见 memory `reference_merge_artifacts.md`）|
| Plan 报告路径（产物）| `MERGE_RECORD/MERGE_PLAN_<run_id>.md` 或 `.merge/plans/`（取决于 mode）|

**关键点**：评估脚本驱动 `merge ... --no-web --ci` 后必须能从产物路径读到 `MergeState`、`MergePlan`、`JudgeVerdict`、`merge_report.md`。如果 `--ci` 输出 JSON 到 stdout，优先用 stdout，path 仅作 fallback。

---

## 4. 数据模型（Ground Truth diff 计算依赖）

| 模型 | 锚点 | 关键字段 |
|---|---|---|
| `RiskLevel` enum | `src/models/diff.py:36` | `AUTO_SAFE / AUTO_RISKY / HUMAN_REQUIRED / DELETED_ONLY / BINARY / EXCLUDED` |
| `FileDiff` | `src/models/diff.py:64` | category / risk_level 等 |
| `FileDecisionRecord` | `src/models/decision.py:24` | `rationale: str`（必填）、`discarded_content: str \| None`（DCRR 指标依赖此字段） |
| `DecisionSource` enum（无 TIMEOUT_DEFAULT）| `src/models/decision.py:17` | 评估必须验证此约束 |
| `MergeState` | `src/models/state.py:62` | 含 plan / decisions / messages |
| `MergePlan` | `src/models/plan.py:215` + `MergePlanLive:266` | |
| `JudgeVerdict` | `src/models/judge.py:117` | `verdict: VerdictType`、`overall_confidence: float [0,1]` |

**JA（Judge Agreement）指标**计算：从 `JudgeVerdict.verdict` 与 Ground Truth match 状态比对。

---

## 5. 工具层（指标实现依赖）

| 工具 | 锚点 | 用途 |
|---|---|---|
| `compute_risk_score` | `src/tools/file_classifier.py:119` | 评估抽样按风险维度分层依据 |
| `is_security_sensitive` | `src/tools/file_classifier.py:267` | SSER 指标实现：判定哪些样本是 security-sensitive |
| `classify_file` | `src/tools/file_classifier.py:277` | 评估前置：把样本分入 ABCDE × risk |
| `apply_with_snapshot` | `src/tools/patch_applier.py:16` | SRSR 指标的回滚路径——评估脚本通过 mock 此函数测试回滚 |
| `apply_bytes_with_snapshot` | `src/tools/patch_applier.py:151` | 同上，二进制变体 |
| `CostTracker` | `src/tools/cost_tracker.py:87` | 提供 `total_cost_usd / summary()`，用于 `cost_usd_per_run` 指标 |
| `TraceLogger` | `src/tools/trace_logger.py:29` | TRR 指标的 trace 回放路径 |

---

## 6. 六类丢失模式 M1-M6（Tier-3 对抗集映射）

权威来源：`doc/architecture.md:293-298`

| 类别 | 对应 detector |
|---|---|
| M1 定制被整文件覆盖 | `src/tools/scar_list_builder.py`（P2-1 自学习） |
| M2 同名不同扩展的 shadow 冲突 | `src/tools/shadow_conflict_detector.py` |
| M3 接口变更未同步调用方 | `src/tools/interface_change_extractor.py` + `reverse_impact_scanner.py` |
| M4 顶层调用被替换 | `src/tools/three_way_diff.py` |
| M5 配置行被覆盖 | `src/tools/config_line_retention_checker.py`、`config_drift_detector.py` |
| M6 类型/API 契约回归 | `src/tools/gate_runner.py` + `src/tools/baseline_parsers/*_json.py` |

注：`doc/evaluation/dataset.md §4.2` 给出的注入手段是评估**注入策略**描述（如"删调用、重命名 module"），与上面 detector 名称是双向映射；Tier-3 注入样本必须能对应一个 detector。

---

## 7. 配置与样例

| 事实 | 锚点 |
|---|---|
| 默认 config | `config/default.yaml`（`upstream_ref` / `fork_ref` / `agents.{planner,planner_judge,...}` block） |
| dify-plugins 真实 config | `config/dify-plugins.yaml`（含 `enable_working_branch: true`、真实 `repo_path`）—— Tier-2 历史回放可参考此结构 |
| 历史 run 报告 | `doc/test-report/`（如 `upstream-50-commits-test-report.md`）—— Tier-2 历史回放可挑这些 run 作为 oracle 候选 |

---

## 8. CI（落地后需修改）

`.github/workflows/ci.yml` 现有两个 job：

- `web-build`（Node 20 + npm test）
- `test`（py 3.11/3.12 矩阵 + ruff + mypy + pytest --cov-fail-under=80）

**本次落地需追加 evaluation 相关 job**（详细方式由 Planner 决定）：
- 单元测试覆盖 `scripts/eval/` 模块
- Tier-1 抽样跑（短 / 廉价 / dummy data）
- 对 `tests/eval/manifests/*.lock.json` 做 verify

---

## 9. 范围与边界

**必须做（in-scope）**：
- 新建 `scripts/eval/` 目录，提供 `lock.py / prepare.py / run.py / diff_against_golden.py / summarize.py / gate.py / consistency.py` 七个脚本（每个文件 ≤ 800 行）
- 新建 `tests/eval/datasets/{tier1,tier2,tier3}/` 与 `tests/eval/manifests/` 目录骨架
- 提供至少 1 个 Tier-1 sample、1 个 Tier-3 sample 作为参考（文档与脚本最小可跑）
- 单元测试覆盖率 ≥ 80%（每个新模块）
- mypy strict 通过
- 报告产物 schema 与 `doc/evaluation/procedure.md §3` 完全对齐

**可选 / 后续（out-of-scope，本次不做）**：
- Tier-2 真实历史合并构造（需要真实 fork repo，跨日工程）
- Tier-3 6 类共 60+ 注入样本完整集（仅做 1-2 个示范）
- 实际 release-grade evaluation 跑（需真实 API key 与时间）
- CI 把 evaluation 接入 PR 阻塞（先以 nightly / manual 触发为目标）

**禁止（hard no）**：
- 修改 `src/` 下任何生产代码（评估代码与生产代码隔离）
- 修改 `doc/evaluation/` 文档（除非发现指标公式有 bug，需向 main agent 报告并经 AskUser 确认）
- 引入新的运行时依赖（评估脚本只用 dev 已有依赖：`click / pydantic / pyyaml / gitpython / unidiff / pytest`）
- 在评估脚本里写入真实 API key、真实 fork 路径、组织内部域名（违反 CLAUDE.md "Project Generality"）
- 任何代码 / 评估样本里出现 `cvte` / `dify` / `insforge` 等具体 fork 名（fixture 路径除外，且必须 generic）

---

## 10. 工程惯例（必须遵守）

- 所有新 Python 文件：`from __future__ import annotations`、type hints 完整、无 mutation
- 无中文注释（`~/.claude/CLAUDE.md`）
- 文件 ≤ 800 行（CLAUDE.md "Code Style"）
- pydantic v2 语法（`model_dump()` / `Field(default_factory=...)` / `@field_validator`）
- async 工具应保持 async，sync 工具保持 sync——不要混用
- 错误处理：抛具体异常类型，不要 `except Exception: pass`
- 不引入 console.log 等价物（`print` 仅 CLI 入口允许，库代码用 `logging`）

---

## 11. 已知 / 待裁决

- **CI 接入策略**：Tier-1 应在 PR 跑还是 nightly 跑？取决于成本——本任务先实现"可手动触发 + 接 nightly placeholder"，PR 阻塞留给后续。
- **scripts 包是否进 wheel**：`pyproject.toml [tool.hatch.build.targets.wheel] packages = ["src"]`，scripts 不进 wheel；`tests/eval/` 也不进 wheel。这是预期行为。
- **Ground Truth 来源**：Tier-1 黄金合并由人工预先生成（脚本只读 `golden.tar` / `golden_diff.patch`），不在脚本范围内自动生成。

---

## 12. 锁清单初始化

`locks/approved-facts.md` 由各 Gatekeeper 在通过审查时**追加**写入。本任务开始时为空文件（仅含 header）。
