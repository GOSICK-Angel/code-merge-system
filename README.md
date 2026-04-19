# CodeMergeSystem

一个面向"长期分叉 fork ↔ upstream"场景的多 Agent 代码合并系统。通过 LLM 做语义理解、通过确定性工具做**可证伪**的加固扫描，把原本需要人工逐文件处理的大规模合并变成一条 **可审计、可暂停、可恢复** 的流水线。

> 中文文档为权威版本。英文文档将在后续补充。

---

## 这是为了解决什么问题

在长期维护的软件项目中，下游团队常常基于某个历史版本做了大量私有改动，同时 upstream 持续迭代新功能、重构接口、升级依赖。分叉时间一长，直接 `git merge` 会出现：

- 数百到数千个文件级冲突，人工无法逐一处理；
- 行级 diff 无法表达语义，LLM/人都容易判错；
- fork 独有的定制（API、路由、哨兵、CI job）被整文件覆盖而不被察觉；
- 合并错一处可能导致运行时漏洞或功能失踪，且难以回滚。

CodeMergeSystem 用 **七个专门化 Agent + 五十余个确定性工具 + 三层记忆 + 完整 Checkpoint** 提供一条通用合并流水线。

## 核心能力

- **六大丢失模式识别**：shadow 冲突 / 接口反向影响 / 顶层调用丢失 / 配置行保留 / Scar 自学习 / 业务哨兵扫描
- **Planner ↔ Judge 协商**：审查 Agent 与 Executor 使用不同 LLM 提供商，避免共谋偏差
- **写入即快照**：任何文件写入前自动保存原内容，失败即回滚
- **全阶段 Checkpoint**：任意时刻 SIGINT 可安全中断，`merge resume` 从上次停下处继续
- **门禁 baseline-diff**：只看"新引入的失败"，而非简单 exit 0，避免合入隐性 regression
- **显式人工决策**：决策无默认回退，避免"超时即接受"的隐患
- **多语言 AST 分块**：Python/TS/JS/Go/Rust/Java/C 均走 tree-sitter

## 环境要求

- Python 3.11+
- `ANTHROPIC_API_KEY`（用于 Planner / ConflictAnalyst / Judge / HumanInterface）
- `OPENAI_API_KEY`（用于 PlannerJudge / Executor）
- Node.js（仅在使用 TUI 时需要）

## 快速开始

```bash
# 1. 安装
git clone <repo-url> && cd CodeMergeSystem
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. 配置 API Key
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...

# 3. 在目标仓库内一键合并（首次运行自动进入配置向导）
cd /path/to/your-fork-repo
merge upstream/main
```

首次运行会自动生成 `<repo>/.merge/config.yaml` 与 `.env`（后者自动写入 `.gitignore`）。

## 常用命令

```bash
merge <target-branch>              # 一站式入口（默认进入 TUI）
merge <target-branch> --no-tui     # 纯文本输出
merge <target-branch> --ci         # CI 模式：无交互，JSON 摘要到 stdout
merge <target-branch> --dry-run    # 只分析不写文件
merge <target-branch> -r           # 强制重新配置

merge run --config <path>          # 显式指定配置文件
merge resume --run-id <id>         # 从 checkpoint 恢复
merge resume --run-id <id> --decisions decisions.yaml   # 带人工决策续跑
merge report --run-id <id>         # 仅重新生成报告
merge validate --config <path>     # 校验配置 + 所有 api_key_env
merge ui --run-id <id>             # 启动 Web UI 回看历史 run
```

## 架构一览

```
CLI / TUI / Web UI
       │
  Orchestrator ── 状态机驱动 8 个 Phase
       │
  ┌────┴─────┐
  │          │
Agents     Tools            Memory
(7 角色)  (50+ 工具 +         (L0/L1/L2
         baseline parsers)   三层记忆)
  │
LLM 层（anthropic / openai，凭据池、智能路由、压缩）
```

| Agent | 角色 | 默认模型 |
|-------|------|----------|
| Planner | 生成合并计划 | Claude Opus |
| PlannerJudge | 审查计划 | GPT-4o |
| ConflictAnalyst | 高风险冲突语义分析 | Claude Sonnet |
| Executor | **唯一写权限**，应用合并 | GPT-4o |
| Judge | 审查合并结果 + 确定性复检 | Claude Opus |
| HumanInterface | 决策模板生成 | Claude Haiku |
| SmokeTest | 合并后冒烟测试 | — |

每个 Agent 的模型、API Key、降档策略均可在 `config.yaml` 中独立配置。

## `.merge/` 生产目录布局

pip 安装后在目标仓库运行时，所有产物写入 `<repo>/.merge/`：

```
.merge/
  config.yaml        # 首次运行自动生成
  .env               # API Keys，自动 gitignore
  .gitignore         # 自动生成
  plans/             # MERGE_PLAN_<id>.md 报告
  runs/<run_id>/
    checkpoint.json
    merge_report.md
    plan_review.md
    logs/run_<id>.log
```

API Key 解析顺序：**Shell env → `.merge/.env` → `~/.config/code-merge-system/.env`**

## 文档

完整中文文档索引见 [`doc/README.md`](doc/README.md)。关键入口：

- [**新人上手指南**](doc/modules/onboarding.md) — 第一次接触本项目必读
- [系统架构](doc/architecture.md) — 分层 / 数据流 / 持久化 / 扩展点
- [执行流程与状态机](doc/flow.md) — 13 个状态、8 个 Phase
- [六大丢失模式 + P0/P1/P2 加固项](doc/multi-agent-optimization-from-merge-experience.md)
- [迁移感知合并](doc/migration-aware-merge.md) — bulk-copy 场景
- [风险等级](doc/risk-levels.md)

模块技术文档（`doc/modules/`）：

| 模块 | 文档 |
|---|---|
| 数据模型（Pydantic v2） | [data-models.md](doc/modules/data-models.md) |
| Agents | [agents.md](doc/modules/agents.md) |
| Core（Orchestrator / Phases / Checkpoint） | [core.md](doc/modules/core.md) |
| Tools（扫描器 / 门禁 / Git） | [tools.md](doc/modules/tools.md) |
| LLM 层（路由 / 压缩 / 凭据池） | [llm.md](doc/modules/llm.md) |
| 记忆系统（L0/L1/L2） | [memory.md](doc/modules/memory.md) |
| CLI / TUI / Web UI | [cli.md](doc/modules/cli.md) |

## 参考开源项目

本项目在设计过程中参考了多个开源实现，相关分析文档位于 [`doc/references/`](doc/references/)：

| 项目 | 类型 | 借鉴点 |
|---|---|---|
| [Weave](https://github.com/ataraxy-labs/weave) | 语义合并引擎 | tree-sitter entity-level merge；函数/类粒度三方合并 |
| [merge-engine](https://docs.rs/merge-engine/) | Rust 合并库 | 4 层合并策略（Pattern DSL → CST → VSA → Genetic） |
| [Mergiraf](https://mergiraf.org/) | AST 结构化合并 | AST 级语法感知合并 |
| [git-machete](https://github.com/VirtusLab/git-machete) | 分支工作流 | Fork-point 推断 + `--override-to` 手动校正 |
| [mergefix](https://pypi.org/project/mergefix/) | AI 冲突修复 | LLM 后处理冲突标记 |
| [reconcile-ai](https://github.com/kailashchanel/reconcile-ai) | 批量冲突修复 | 批量提示节省成本 |
| [clash](https://github.com/clash-sh/clash) | 并行 Agent | Worktree 级冲突检测 |
| [NousResearch/hermes-agent](https://github.com/nousresearch/hermes-agent) | Agent 架构 | 工具抽象与 Agent 协作模式 |
| Graphify | 代码知识图谱 | 用图谱压缩代码上下文 |
| MemPalace | 记忆系统 | 语义索引 + 分层记忆 |

详细对照见 [`doc/references/opensource-comparison.md`](doc/references/opensource-comparison.md) 与各 `*-analysis.md`。

## 开发

```bash
pytest tests/unit/ -q              # 单元测试（不打 LLM API）
pytest tests/integration/ -v       # 集成测试（打真 API，本地跑，不进 CI）
mypy src                           # 类型检查（strict 模式）
ruff check src/                    # Lint
ruff format src/                   # 格式化

# TUI 前端
cd tui && npm run start            # 启动
cd tui && npm run build            # tsc --noEmit 类型检查
```

关键约束（PR review 会检查）：

- 不要给 `DecisionSource` 加 `TIMEOUT_DEFAULT`
- Judge / PlannerJudge 只接收 `ReadOnlyStateView`
- Executor 写文件必须走 `apply_with_snapshot()`
- `plan_revision_rounds >= max` 时转 `AWAITING_HUMAN`，不是 `FAILED`
- HumanInterface 不填默认值

## 许可证

TBD
