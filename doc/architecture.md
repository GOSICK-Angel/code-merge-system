# 系统架构设计文档

> **版本**：2026-04-17
> **对应代码**：`main` 分支 commit `50834f7` 及之后
> **本文档定位**：CodeMergeSystem 的权威架构总览。单模块细节请查 `doc/modules/`。

---

## 目录

1. [项目定位与问题背景](#1-项目定位与问题背景)
2. [总体架构](#2-总体架构)
3. [核心设计原则](#3-核心设计原则)
4. [运行时视图：Phase 驱动循环](#4-运行时视图phase-驱动循环)
5. [分层与模块划分](#5-分层与模块划分)
6. [关键数据流](#6-关键数据流)
7. [状态与持久化](#7-状态与持久化)
8. [LLM 路由、凭据池与成本](#8-llm-路由凭据池与成本)
9. [工具层与加固管线](#9-工具层与加固管线)
10. [配置模型与 `.merge/` 目录](#10-配置模型与-merge-目录)
11. [CLI 与 TUI 前端](#11-cli-与-tui-前端)
12. [可观测性](#12-可观测性)
13. [扩展点](#13-扩展点)
14. [术语表](#14-术语表)

---

## 1. 项目定位与问题背景

CodeMergeSystem 是一个**通用**的多 Agent 合代码系统，目标是为任意"长期分叉 fork ↔ upstream"场景提供可复用的合并流水线：

- fork 基于旧版 upstream 做了大量私有改动；
- upstream 长期迭代后产生跨越若干大版本的变更；
- 直接 `git merge` 会出现成百上千的冲突，行级 diff 无法表达语义差异，也无法看出哪些 fork 独有功能已被悄悄覆盖。

系统通过 LLM + 确定性工具混合的流水线解决四个问题：

| 问题 | 本系统给出的答案 |
|---|---|
| 冲突数量大 | 按 ABCDE 五类文件分流，低风险自动合并、高风险送审 |
| 语义丢失 | 六类丢失模式 (M1–M6) 专项工具扫描 + LLM 语义合并 |
| 上下文不足 | 三层 Memory + Layered Memory Loader 注入每个 Agent |
| 不可逆 | 每次写入前快照，失败自动回滚；全阶段 Checkpoint |

---

## 2. 总体架构

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          CLI / TUI 前端（面向用户）                       │
│  merge <branch>  merge run/resume/report/validate/init/ui/tui            │
└──────────────────────────────────────────────────────────────────────────┘
                                    │
┌──────────────────────────────────────────────────────────────────────────┐
│                             Orchestrator                                  │
│         状态机驱动 Phase 循环 · 注入依赖 · 持久化 · Hook 事件             │
└──────────────────────────────────────────────────────────────────────────┘
           │                │                │                 │
           ▼                ▼                ▼                 ▼
     ┌──────────┐     ┌──────────┐     ┌──────────┐      ┌──────────┐
     │  Phases  │ ◀── │  Agents  │ ◀── │   LLM    │ ◀──  │  Memory  │
     │ 8 阶段   │     │ 7 类角色 │     │ 多提供商 │      │ 三层记忆 │
     └──────────┘     └──────────┘     └──────────┘      └──────────┘
           │                │                │
           ▼                ▼                ▼
     ┌──────────────────────────────────────────────┐
     │                   Tools                      │
     │  Git · Diff · 分类器 · Gate · 六大加固扫描器 │
     └──────────────────────────────────────────────┘
                            │
                            ▼
                      ┌──────────┐
                      │ File I/O │  带快照的原子写入、Checkpoint JSON
                      └──────────┘
```

---

## 3. 核心设计原则

这些原则是**加载到代码的不变量**，由 `CLAUDE.md` 与 mypy/unit test 共同守护：

| # | 原则 | 代码执行位 |
|---|------|-----------|
| **P1** | 不丢失（No-Loss） | `FileDecisionRecord.discarded_content` + `rationale` 必填 |
| **P2** | 语义优先 | ConflictAnalyst + ThreeWayDiff AST 抽取 |
| **P3** | 可解释 | 每个 MergeDecision 附 rationale，Plan Review 报告全量保留 |
| **P4** | 不确定即升级 | 置信度 < threshold → `ESCALATE_HUMAN` |
| **P5** | 审查隔离 | Judge / PlannerJudge 接收 `ReadOnlyStateView`；写操作仅 Executor |
| **P6** | 显式人工 | 人工决策无超时回退；`DecisionSource` 无 `TIMEOUT_DEFAULT` |
| **P7** | 快照先于写入 | `patch_applier.apply_with_snapshot()` 唯一写入通道 |
| **P8** | 零仓库知识 | `src/` 不出现项目专属字符串；规则走 YAML |

---

## 4. 运行时视图：Phase 驱动循环

Orchestrator 是纯 Phase 分派器（~400 LOC），不含业务逻辑。它按状态机 `status → Phase 类` 的映射循环调度：

```
INITIALIZED        → InitializePhase
PLANNING           → PlanningPhase
PLAN_REVIEWING     → PlanReviewPhase   （可回环 PLAN_REVISING）
AUTO_MERGING       → AutoMergePhase    （可进入 PLAN_DISPUTE_PENDING）
ANALYZING_CONFLICTS→ ConflictAnalysisPhase
AWAITING_HUMAN     → HumanReviewPhase  （人工决策后 resume）
JUDGE_REVIEWING    → JudgeReviewPhase  （可回环 AUTO_MERGING / ANALYZING_CONFLICTS）
GENERATING_REPORT  → ReportGenerationPhase → COMPLETED
```

完整状态转换表与每阶段前置/后置条件详见 [`flow.md`](flow.md)。

每个 Phase 实现 `Phase.execute(state, ctx) -> PhaseOutcome` 接口。Outcome 告诉 Orchestrator：

- 下一个 `target_status` 与 reason
- 是否需要 `checkpoint_tag`
- 是否需要触发 memory 汇总
- `extra.paused=True` 可让流程在此阶段挂起（等待人工）

---

## 5. 分层与模块划分

实际目录（以 `src/` 为根）：

```
src/
├── cli/              # Click CLI + commands/（run / resume / init / setup / tui）
├── core/             # Orchestrator · StateMachine · Checkpoint · MessageBus · Hooks
│   └── phases/       # 8 个 Phase 类 + PhaseContext · PhaseOutcome
├── agents/           # 7 类 Agent + BaseAgent + Registry
├── llm/              # 客户端 · 路由 · 凭据池 · 上下文 · 压缩 · 分块 · prompts/
├── memory/           # 三层记忆 · Store · Summarizer · LayeredLoader
├── models/           # 全部 Pydantic v2 数据模型
├── tools/            # Git · 分类 · 门禁 · 加固扫描 · 报告输出
│   └── baseline_parsers/  # 可插拔的多语言测试/Lint 输出解析器
├── integrations/     # 外部系统对接（GitHub）
└── web/              # Web UI + WebSocket Bridge（供 TUI 使用）

tui/                  # React Ink 终端 UI（Node.js）
tests/unit/           # 单元测试
tests/integration/    # 集成测试（需真实 API Key，CI 不跑）
config/               # 默认 YAML 模板
```

各模块详细文档位于 `doc/modules/` 下，建议按以下顺序阅读：

1. [`modules/data-models.md`](modules/data-models.md) — 数据模型是所有模块的契约
2. [`flow.md`](flow.md) — 状态机与 Phase 流程
3. [`modules/agents.md`](modules/agents.md) — 七类 Agent 的职责
4. [`modules/core.md`](modules/core.md) — Phase 调度与 Checkpoint
5. [`modules/tools.md`](modules/tools.md) — 确定性加固扫描器
6. [`modules/llm.md`](modules/llm.md) — LLM 路由与成本
7. [`modules/memory.md`](modules/memory.md) — 三层记忆系统
8. [`modules/cli.md`](modules/cli.md) — 一站式 CLI + TUI

---

## 6. 关键数据流

```
┌────────────┐  git diff       ┌────────────┐   FileDiff[]   ┌────────────┐
│  GitTool   │ ──────────────▶ │ DiffParser │ ─────────────▶ │ Classifier │
└────────────┘                 └────────────┘                └─────┬──────┘
                                                                   │ RiskLevel × 文件
                                                                   ▼
                                 ┌──────────────────────────────────────────┐
                                 │ PollutionAuditor · SyncPointDetector     │
                                 │ ShadowConflictDetector · ScarListBuilder │
                                 │ InterfaceChangeExtractor · Sentinel...   │
                                 └──────────────────┬───────────────────────┘
                                                    │ state 字段
                                                    ▼
                                              ┌──────────┐
                                              │ Planner  │ ──▶ MergePlan
                                              └─────┬────┘
                                                    │
                                           ┌────────▼────────┐
                                           │ PlannerJudge    │ ──▶ approve / revise
                                           └────────┬────────┘
                                                    │ 达成一致或 AWAITING_HUMAN
                                                    ▼
                         ┌──────────── Executor ──────────────┐
                         │ apply_with_snapshot → 写文件        │
                         │ 失败自动回滚；可 raise_plan_dispute │
                         └────────────┬────────────────────────┘
                                      │
                   ┌──────────────────▼──────────────────┐
                   │ ConflictAnalyst（仅高风险文件）      │
                   └──────────────────┬──────────────────┘
                                      │
                                      ▼
                                   Judge
                        ┌───── verdict ─────┐
                        │ approved → REPORT │
                        │ repair   → 回环     │
                        │ escalate → HUMAN   │
                        └───────────────────┘
```

---

## 7. 状态与持久化

### 7.1 `MergeState`

全局状态对象（`src/models/state.py`）。贯穿所有 Phase 和 Agent，包含：

- 原始输入：`config`、`upstream_ref/fork_ref`、`merge_base_commit`
- 分析产物：`file_diffs`、`file_classifications`、`file_categories`
- 六大扫描结果：`shadow_conflicts`、`interface_changes`、`reverse_impacts`、`scar_list`、`sentinel_hits`、`config_drifts`
- 迁移感知：`migration_info: SyncPointResult | None`（InitializePhase 迁移检测结果，含 effective merge-base、跳过 commit 数、sync_ratio 等）
- 迭代过程：`merge_plan`、`plan_review_log`、`file_decision_records`、`applied_patches`
- 仲裁：`judge_verdict`、`judge_verdicts_log`、`plan_disputes`
- 记忆：`memory: MergeMemory`
- 轨迹：`messages`、`errors`、`phase_results`

### 7.2 Checkpoint

- 单文件滚动写入 `<run_dir>/checkpoint.json`（`_atomic_write`: 先写 tmp 再 rename，POSIX 原子）
- 开启 `debug_checkpoints` 后，额外在 `checkpoints_debug/<tag>.json` 落盘每个 Phase 快照
- 注册 SIGINT/SIGTERM 处理器，中断时打 `interrupt` 标记
- Schema mismatch 时直接报错——永远不会静默恢复出半损坏状态

### 7.3 `.merge/` 生产目录

生产模式（pip 安装后在目标仓库运行）下，所有产物都写入 `<repo>/.merge/`：

```
.merge/
  config.yaml          # 首次运行由向导生成
  .env                 # API Keys，自动 gitignore
  .gitignore           # 自动生成
  plans/               # MERGE_PLAN_<id>.md 报告
  runs/<run_id>/
    checkpoint.json
    merge_report.md
    plan_review.md
    checkpoints_debug/   # 开启 debug 后才有
    logs/run_<id>.log
    logs/run_<id>.jsonl  # 开启 structured_logs 后才有
```

---

## 8. LLM 路由、凭据池与成本

| 组件 | 文件 | 要点 |
|---|---|---|
| 客户端工厂 | `src/llm/client.py` | 按 `AgentLLMConfig.provider` 实例化 anthropic/openai；支持 `update_api_key()` 热切换、`with_model()` 临时换模型 |
| 凭据池 | `src/llm/credential_pool.py` | 同一 Agent 可声明 `api_key_env: [KEY_A, KEY_B, ...]`，限流后轮转 |
| 模型路由 | `src/llm/model_router.py` | `cheap_model` 存在时，对"简单任务"降档（D1 smart routing） |
| 错误分类 | `src/llm/error_classifier.py` | 8 类 ErrorCategory → 不同重试/熔断策略 |
| 上下文预算 | `src/llm/context.py` | `TokenBudget` + 优先级分段组装 + 保底/截断 |
| 压缩 | `src/llm/context_compressor.py` | 保头保尾 + 中段摘要（每 Agent 可调） |
| 分块 | `src/llm/chunker.py` | 大文件 AST/行级分块，配合 `relevance.py` 评分 |
| Prompt Caching | `src/llm/prompt_caching.py` | Anthropic 专属，`cache_strategy` 三档可配 |
| 成本追踪 | `src/tools/cost_tracker.py` | 每次调用记 token/美金；run 结束汇总 |
| 熔断与重试 | `src/agents/base_agent.py` | 3 类错误累计 ≥ 3 触发熔断；rate-limit 最多等 5 轮 |

每个 Agent 的模型、provider、api_key_env 均在 `config.agents.<name>` 独立配置，详见 [`modules/llm.md`](modules/llm.md)。

---

## 9. 工具层与加固管线

`src/tools/` 是整个系统的确定性脊柱。LLM 负责"理解"，Tools 负责"证伪"。

按职责分为三组：

### 9.1 基础工具
- `git_tool.py`：GitPython 封装
- `diff_parser.py`：unified diff → `FileDiff[]`
- `file_classifier.py`：ABCDE 分类 + 风险打分
- `patch_applier.py`：快照+原子写入（P7）
- `commit_replayer.py`：**git 历史保留 — 第一阶段**。在 AutoMergePhase Executor 运行前，将所有文件均属 Category B / D_MISSING 的 upstream commits 通过 `git cherry-pick` 原样重放，保留原始作者、时间戳、commit message；cherry-picked 文件写入 `state.replayed_files`，Executor 遇到后自动跳过
- `git_committer.py`：**git 历史保留 — 第二阶段**。AutoMergePhase 全部层完成后，将 Executor 写入的文件（排除已 cherry-pick 的文件）`git add` 并 `git commit`，生成一条 `merge(auto_merge): resolve N files` 记录；受 `history.commit_after_phase` 控制
- `report_writer.py` / `merge_plan_report.py`：Markdown / JSON 报告
- `gate_runner.py` + `baseline_parsers/`：门禁命令执行与 baseline-diff

### 9.2 六大丢失模式扫描器（见 `multi-agent-optimization-from-merge-experience.md`）

| 模式 | 工具 |
|---|---|
| M1 定制被整文件覆盖 | `scar_list_builder.py`（P2-1 自学习） |
| M2 同名不同扩展的 shadow 冲突 | `shadow_conflict_detector.py` |
| M3 接口变更未同步调用方 | `interface_change_extractor.py` + `reverse_impact_scanner.py` |
| M4 顶层调用被替换 | `three_way_diff.py` |
| M5 配置行被覆盖 | `config_line_retention_checker.py`、`config_drift_detector.py` |
| M6 类型/API 契约回归 | `gate_runner.py` + `baseline_parsers/*_json.py` |

另外：
- `pollution_auditor.py`：历史合并污染再分类（**条件执行**：仅当 git log 在 fork 分支中检测到以往 upstream merge commit 时才进行分类修正；若无历史 merge 记录则直接跳过，不产生任何开销）
- `sync_point_detector.py`：**迁移感知同步点检测**（Migration-Aware Merge）。fork 曾通过 bulk copy 手工同步 upstream 代码时，git merge-base 会指向远古 commit，导致大量误分类。该工具在 InitializePhase 最前端运行，三阶段算法自动识别并覆写 effective merge-base：
  1. **文件级**：对比三处 blob hash，找出 upstream 修改而 fork 已同步的文件集合
  2. **Patch-ID 验证**：对 hash 不同但 patch-ID 相同的模糊文件升级为 synced（检测带微调的 copy）
  3. **Commit 边界**：oldest→newest 遍历 upstream commits（>50 条二分搜索），确定最后一个完全已同步的 commit
  - 结果存入 `state.migration_info`；支持 `merge_base_override` 手动覆盖；受 `config.migration` 控制
- `cross_layer_checker.py`：跨层键一致性断言
- `sentinel_scanner.py`：业务哨兵 regex 扫描（P2-2）
- `smoke_runner.py`：post-judge 冒烟测试（P1-3）

### 9.3 可观测性工具
- `cost_tracker.py`、`trace_logger.py`、`structured_logger.py`、`ci_reporter.py`

---

## 10. 配置模型与 `.merge/` 目录

配置的权威模型在 `src/models/config.py`：

```
MergeConfig
├── upstream_ref / fork_ref / working_branch / repo_path
├── project_context              # 注入到每个 Agent 的 system prompt
├── max_files_per_run
├── max_plan_revision_rounds     # Planner ↔ Judge 最多协商轮数（默认 5）
├── max_judge_repair_rounds      # Executor ↔ Judge 最多修复轮数
├── llm                          # 旧版全局默认，保留向后兼容
├── agents                       # 每个 Agent 独立 LLM 配置（权威）
│   ├── planner / planner_judge / conflict_analyst
│   └── executor / judge / human_interface
├── thresholds                   # auto_merge_confidence / human_escalation /
│                                #   risk_score_low / risk_score_high
├── file_classifier              # 排除/二进制/安全敏感规则
├── output                       # directory / debug_directory / formats /
│                                #   include_llm_traces / structured_logs / language
├── syntax_check / llm_risk_scoring / github
├── layer_config                 # 层依赖拓扑（DEFAULT_LAYERS 9 层）
├── customizations               # fork 定制项 + verification 规则
├── shadow_rules_extra           # P0-2 额外 shadow 规则
├── cross_layer_assertions       # P0-4 断言
├── gate                         # 门禁命令清单 + baseline_parser
├── reverse_impact               # P1-1 反向扫描范围
├── smoke_tests                  # P1-3 冒烟测试套件
├── sentinels_extra              # P2-2 业务哨兵
├── config_retention             # P2-3 配置行保留规则
├── scar_learning                # P2-1 scar 自学习
├── migration                    # 迁移感知合并（MigrationConfig）
│   ├── merge_base_override      #   手动指定 effective merge-base commit SHA
│   ├── auto_detect_sync_point   #   是否自动检测（默认 true）
│   ├── sync_detection_threshold #   触发检测的最小 sync_ratio（默认 0.3）
│   └── min_synced_files         #   最少已同步文件数（默认 5，防假阳性）
└── history                      # Commit 历史保留（HistoryPreservationConfig）
    ├── enabled                  #   总开关（默认 true）
    ├── cherry_pick_clean        #   对 replayable commits 执行 cherry-pick（默认 true）
    └── commit_after_phase       #   每 Phase 结束后 commit Executor 产出（默认 true）
```

生产模式下 `.merge/` 目录由 `src/cli/paths.py` + `src/cli/commands/setup.py` 管理，首次运行通过向导生成。API Key 解析顺序：**shell env → `.merge/.env` → `~/.config/code-merge-system/.env`**。

---

## 11. CLI 与 TUI 前端

### 11.1 CLI（`src/cli/main.py`）

一站式入口（主命令）：

```bash
merge <target-branch>              # 默认进入 TUI；首次运行自动触发 setup 向导
merge <target-branch> --no-tui     # 纯文本输出模式
merge <target-branch> --ci         # CI 模式：无交互，JSON 摘要到 stdout
merge <target-branch> --dry-run    # 仅分析，不写文件
merge <target-branch> -r           # 强制重新触发配置向导
```

辅助子命令：

| 子命令 | 用途 |
|--------|------|
| `merge resume --run-id <id>` | 从 checkpoint 恢复上次中断的 run |
| `merge validate --config <path>` | 校验配置文件及所有 API Key 环境变量 |
| `merge run --config <path>` | 以显式配置文件启动（高级 / CI 场景） |

### 11.2 TUI（`tui/` + `src/web/ws_bridge.py`）

- 前端：React Ink（Node.js），入口 `tui/src`
- 后端：`src/web/ws_bridge.py` 启动 WebSocket 服务（默认 `ws://localhost:8765`）
- 状态机 Observer 把 `state_transition / activity / phase_event` 推流到前端
- 人工决策：前端提交 → Bridge → HumanReviewPhase 消费

### 11.3 Web UI（`src/web/`）

`merge ui --run-id <id>` 启动一个独立 HTTP 服务，用于回看历史 run 的合并决策（app.py + server.py）。

---

## 12. 可观测性

### 12.1 用户可见（写入合并报告）

| 输出 | 位置 | 说明 |
|---|---|---|
| 成本汇总 | `merge_report.md` → "Run Insights" 区块 | LLM 调用次数、总费用、各 Agent 明细 |
| Context 利用率 | `merge_report.md` → "Run Insights" 区块 | 各 Agent 平均 / 峰值 token 占用率 |

### 12.2 开发者内部（写入日志文件）

| 输出 | 文件 | 用途 |
|---|---|---|
| 纯文本日志 | `.merge/runs/<id>/logs/run_<id>.log` | 调试、事后审阅 |
| 结构化日志 | `run_<id>.jsonl`（`output.structured_logs=true`） | 日志聚合、指标 |
| LLM trace | `llm_traces_<id>.jsonl`（`output.include_llm_traces=true`） | 回放完整 prompt/response |
| Hook 事件 | `phase:before` / `phase:after` / `merge:complete` | 外部系统挂接 |

---

## 13. 扩展点

1. **新增 Agent**：继承 `BaseAgent`，在类定义末尾 `AgentRegistry.register("my_agent", MyAgent)`，import 该模块后 Orchestrator 即可通过 `AgentRegistry.create_all()` 创建。
2. **新增 Phase**：继承 `Phase`，在 `src/core/phases/__init__.py` 注册，更新 `Orchestrator.PHASE_MAP` 和 `state_machine.VALID_TRANSITIONS`。
3. **新增 LLM provider**：在 `src/llm/client.py` 中扩展 `LLMClientFactory`；修改 `AgentLLMConfig.provider` 的 Literal。
4. **新增门禁 baseline parser**：在 `src/tools/baseline_parsers/` 新增模块，名称作为 `GateCommandConfig.baseline_parser` 的值即可。
5. **新增定制项验证类型**：扩展 `CustomizationVerification.type` Literal 与 `judge_agent._verify_*` 分支。
6. **新增 shadow 规则**：通过 `config.shadow_rules_extra` 注入；默认规则见 `DEFAULT_SHADOW_RULES`。

---

## 14. 术语表

| 术语 | 释义 |
|---|---|
| fork | 下游分叉分支，包含私有改动 |
| upstream | 上游主干，持续迭代 |
| merge-base | 两个 ref 的共同祖先 commit |
| ABCDE 分类 | 五种文件变更类型（A 未变 / B upstream 独有 / C 双改 / D 新增 / E fork 独有） |
| Phase | 一个编排阶段，对应状态机的一个状态 |
| Gate | 门禁命令（lint/test/typecheck），必须通过才能前进 |
| Scar | 历史上 restore/compat-fix/revert commit 命中的定制项 |
| Sentinel | 业务哨兵：必须在 fork 中存在的正则/标记 |
| Shadow | 同名不同扩展或 module/package 布局差异造成的隐式冲突 |
| VETO | Judge 确定性流水线否决（不可协商，必须修复） |
| Plan Dispute | Executor 执行时发现计划不合理，回退请求修订 |

---

## 相关文档

- 模块级细节：`doc/modules/*.md`
- 加固项设计：`doc/multi-agent-optimization-from-merge-experience.md`
- 迁移感知合并：`doc/migration-aware-merge.md`
- 参考开源项目分析：`doc/references/*.md`
