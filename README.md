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

## 前置准备

| 项 | 说明 |
|---|---|
| Python 3.11+ | mypy strict / Pydantic v2 / async 全程 |
| `ANTHROPIC_API_KEY` | Planner / ConflictAnalyst / Judge / HumanInterface 用 |
| `OPENAI_API_KEY` | PlannerJudge / Executor 用（双 provider 是为了避免共谋偏差） |
| `GITHUB_TOKEN`（可选） | 仅在 `merge ui` 浏览 PR 评论或将合并结果推 PR 时需要 |
| Node.js（可选） | 仅当用 TUI（默认开启，`--no-tui` 关闭）时需要 |

**目标仓库需满足**：

- 是个 git 仓库，且当前 HEAD 是你的 fork 主分支
- 工作树干净（`git status` 无未提交更改）—— 系统会写文件，脏树会被拒
- upstream 那一端可访问：要么是本地分支（如 `upstream/main`、`origin/upstream-main`），要么 `git fetch <remote>` 已拉到本地

如果你 fork 还没接 upstream 远端：

```bash
cd /path/to/your-fork-repo
git remote add upstream https://github.com/<owner>/<repo>.git
git fetch upstream
```

## 安装

```bash
git clone <repo-url> && cd CodeMergeSystem
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...
```

## 首次合并：完整流程

下面是一次真实合并里你**会依次看到的内容 + 每步要做的判断**。第一次跑建议先来一遍 `--dry-run` 摸清规模再决定真合。

### 1. 启动 + 首次配置向导

```bash
cd /path/to/your-fork-repo
merge upstream/main --dry-run
```

首次运行进入交互向导，依次问你：

- **项目背景描述**（一句话即可，会喂给 Planner 帮它理解上下文）
- **API Key 确认**（已 export 的会显示掩码，回车跳过表示沿用）
- **阈值**（默认 `auto_merge=0.85 / risk_low=0.30 / risk_high=0.60`，新手直接默认）

之后系统在 `<repo>/.merge/` 写入 `config.yaml` + `.env`（后者自动加进 `.gitignore`），下次运行不再问。

> **如果你的 fork 删过整片功能域**（例如砍掉了 payments 子树）：当系统检测到 ≥30 个被 fork 删除的文件时，向导会**主动提示生成 `forks-profile.yaml` 草稿**并打开 `$EDITOR` 让你审阅。低于阈值则完全静默 —— `fork_only_features` 与 `migration_policy` 已在每次 run 时自动从 git 推算，无需手工维护。

### 2. dry-run 跑出合并计划

向导通过后系统进入 TUI（`--no-tui` 切纯文本）。你会看到 8 个 phase 依次推进：

```
INITIALIZE  → 三方分类、风险打分、forks-profile 路由
PLANNING    → Planner 出合并计划
PLAN_REVIEW → PlannerJudge 审查；最多 2 轮修订
AWAITING_HUMAN → 你审阅计划报告
...（dry-run 在此停止）
```

dry-run 结束后**重点看这两个文件**：

```
.merge/plans/MERGE_PLAN_<upstream>_<run_id>.md
.merge/runs/<run_id>/plan_review.md
```

报告会告诉你：

- 触及多少文件、按 ABCDE 五类分布
- auto_merge / conflict_analysis / human_required 的占比
- forks-profile drift 附录（如果 yaml 老化）
- Planner-Judge 审查记录

### 3. 决定继续真合并还是先调整

如果计划合理：

```bash
merge upstream/main          # 不带 --dry-run，正式跑
```

系统会从 `INITIALIZE` 开始重新走一遍直到 `AUTO_MERGING` / `CONFLICT_ANALYSIS`，写入文件、做快照、跑门禁。

> **任意时刻 Ctrl+C 都安全** —— 已经写盘的 checkpoint 让你下次用 `merge resume --run-id <id>` 续跑。

### 4. 处理人工决策（AWAITING_HUMAN）

当系统遇到 risk_score 高于 `human_escalation` 的文件、或 Judge 判定不通过时，会暂停在 `AWAITING_HUMAN`，并在 `.merge/runs/<run_id>/` 下生成一个待填的 `decisions.yaml` 模板：

```yaml
# decisions.yaml — 系统生成模板，你填决定
- file_path: "backend/services/auth/auth.service.ts"
  decision: take_current        # 可选：take_target / take_current / semantic_merge / escalate_human
  rationale: "fork 用 SSO，必须保留"
```

填完续跑：

```bash
merge resume --run-id <id> --decisions .merge/runs/<id>/decisions.yaml
```

### 5. 最终产出

合并跑完后看：

| 路径 | 说明 |
|---|---|
| `.merge/runs/<run_id>/merge_report.md` | 最终合并报告（变更摘要、Judge verdict、未解决项） |
| `.merge/runs/<run_id>/checkpoint.json` | 完整状态，可继续 resume |
| `.merge/runs/<run_id>/logs/run_<id>.log` | 全量执行日志 |
| 工作树本身 | 合并产物已落到当前分支；`git status` 看具体改了什么；自己决定是否 `git commit` |

> **系统不自动 commit / push** —— 写到工作树就停手，让你 review 完再提交。

## 常用命令

按使用场景分组：

```bash
# === 首次接入 / 日常合并 ===
merge <target-branch>                         # 一站式（默认 TUI）
merge <target-branch> --dry-run               # 只跑到 plan，不动文件
merge <target-branch> --no-tui                # 纯文本输出
merge <target-branch> -r                      # 强制重新跑配置向导

# === 续跑 / 决策 ===
merge resume --run-id <id>                    # 从 checkpoint 续跑
merge resume --run-id <id> --decisions decisions.yaml   # 带人工决策续跑
merge report --run-id <id>                    # 仅重新生成报告（不改状态）

# === 查看 / 校验 ===
merge ui --run-id <id>                        # Web UI 回看历史 run
merge validate --config <path>                # 校验 config.yaml + 所有 api_key_env

# === forks-profile（仅在做 fork 整域裁剪时用）===
merge forks-profile init -o .merge/forks-profile.yaml   # 起草草稿
merge forks-profile diff                                # 检查 yaml 是否过时
merge forks-profile validate                            # 校验 yaml 语法

# === CI ===
merge <target-branch> --ci                    # 无交互，JSON 摘要到 stdout
```

## 卡住了？

| 现象 | 排查 |
|---|---|
| 向导报 "API Key not set" | 检查 `merge validate --config .merge/config.yaml`；shell env > `.merge/.env` > `~/.config/code-merge-system/.env` |
| 启动报 "working tree dirty" | `git status` 看到未提交改动；`git stash` 或 `git commit` 后再跑 |
| 启动报 "upstream ref not found" | 没 `git fetch upstream`，或者 `target-branch` 拼错（要写 `upstream/main` 不是 `main`） |
| dry-run 卡在 PLAN_REVIEW 多轮 | Planner 与 PlannerJudge 在博弈；正常 1-2 轮，`max_plan_revision_rounds=2` 后会转 `AWAITING_HUMAN`，去看 `plan_review.md` |
| 跑了一半中断 | 重新跑 `merge resume --run-id <id>`（`run_id` 在 `.merge/runs/` 下能看到） |
| 想丢弃这次 run 重来 | `rm -rf .merge/runs/<id>/`，再 `merge <target-branch>` |

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
