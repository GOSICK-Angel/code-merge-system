# 新人上手指南

> **目标读者**：第一次接触 CodeMergeSystem 的工程师。
> **完成本指南后**：你能看懂架构、跑通本地测试、找到合适的切入点。

---

## 1. 花 10 分钟了解这是做什么的

读 [`../architecture.md`](../architecture.md) §1 + §2。一句话概括：

> 把"fork 长期偏离 upstream 导致的 git merge 爆炸"变成一条**可审计、可暂停、可恢复、带加固扫描**的流水线。

---

## 2. 本地搭环境

```bash
git clone <repo> && cd CodeMergeSystem
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

### 配置 API Keys

系统支持三种方式，**优先级从低到高**：

| 方式 | 文件位置 | 说明 |
|------|---------|------|
| 全局 `.env` | `~/.config/code-merge-system/.env` | 所有项目共享，一次配好 |
| 项目 `.env` | `<target-repo>/.merge/.env` | 由 `merge <branch>` 首次运行时向导写入 |
| Shell 环境变量 | — | `export` 只对当前终端会话有效，临时使用 |

> **推荐做法**：写入全局 `.env`，无需每次 export。

支持的变量：

```dotenv
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_BASE_URL=          # 可选，代理或自托管端点
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=             # 可选，代理或自托管端点
GITHUB_TOKEN=                # 可选，GitHub 操作
```

配置完成后验证：

```bash
merge validate                  # 检查 env vars + config 是否齐全
merge --help                    # CLI 能出 usage 即 OK
pytest tests/unit/ -q           # 应全绿
mypy src                        # 应全绿（strict mode）
```

（如果不需要跑 LLM，可以只做 `pytest tests/unit/` + `mypy src`，那些不打 LLM API。）

---

## 3. 按这个顺序读代码

顺序很重要——先看"契约"再看"实现"：

1. **`src/models/state.py::SystemStatus`** — 13 个状态；把 `VALID_TRANSITIONS` 粘到纸上画一下
2. **`src/models/state.py::MergeState`** — 所有字段，不必背，知道有哪几组就行
3. **`src/models/config.py::MergeConfig`** — 所有可配置点
4. **`src/core/state_machine.py`** — 80 行，唯一真理
5. **`src/core/phases/base.py`** — `Phase / PhaseContext / PhaseOutcome`
6. **`src/core/orchestrator.py`** — 主循环 `Orchestrator.run()`，约 90 行，非常清楚
7. **`src/core/phases/initialize.py`** — 最简单的 Phase，看它怎么用 ctx
8. **`src/agents/base_agent.py`** — 前 100 行已经能理解重试/熔断/记忆注入
9. **`src/tools/patch_applier.py`** — 85 行，理解"快照+原子写入+自动回滚"
10. **`src/core/phases/plan_review.py`** — 最复杂的 Phase（Planner↔PlannerJudge 循环）

读完这 10 个文件就算入门了。

---

## 4. 对应文档路标

| 你想了解 | 文档 |
|---|---|
| 总体架构、术语、分层 | [`../architecture.md`](../architecture.md) |
| 状态机与每个 Phase 做什么 | [`../flow.md`](../flow.md) |
| 数据模型字段 | [`data-models.md`](data-models.md) |
| 各 Agent 职责 | [`agents.md`](agents.md) |
| 确定性工具（Gate/扫描器/Git） | [`tools.md`](tools.md) |
| LLM 路由/压缩/缓存 | [`llm.md`](llm.md) |
| 三层记忆系统 | [`memory.md`](memory.md) |
| CLI + TUI + Web UI | [`cli.md`](cli.md) |
| 六大丢失模式 + P0/P1/P2 加固项 | [`../multi-agent-optimization-from-merge-experience.md`](../multi-agent-optimization-from-merge-experience.md) |
| 迁移感知合并 | [`../migration-aware-merge.md`](../migration-aware-merge.md) |
| 风险等级枚举 | [`../risk-levels.md`](../risk-levels.md) |
| 参考开源项目分析 | [`../references/`](../references/) |

---

## 5. 跑一次完整合并看效果

用项目自带的 fixture 仓库（如果存在）或随便一个 fork + upstream 演示：

```bash
mkdir /tmp/demo && cd /tmp/demo
git init
echo "hello" > a.py && git add a.py && git commit -m init
git checkout -b upstream && echo "hello world" > a.py && git commit -am up
git checkout -b fork main && echo "hello fork" > a.py && git commit -am fk

# 在 demo 根目录
cd /path/to/CodeMergeSystem
merge upstream --no-tui --dry-run
```

看 `outputs/debug/` 或 `.merge/runs/<id>/` 目录下的 checkpoint 与 plan 报告。

---

## 6. 常见"我想改..."的起点

| 想改 | 从哪里入手 |
|---|---|
| 新增一个 Agent | `agents.md` §6 |
| 新增一个 Phase | `core.md` §10 |
| 新增一个加固扫描器 | `tools.md` §5 |
| 新增 baseline parser（比如新语言的 test runner） | 加文件到 `src/tools/baseline_parsers/`，文件名作为 `GateCommandConfig.baseline_parser` |
| 接入新 LLM Provider | `llm.md` §12 |
| 改 Prompt | `src/llm/prompts/` 下对应 Agent 的文件 |
| 改默认层级拓扑 | `src/models/plan.py::DEFAULT_LAYERS` |
| 改默认配置 | `src/models/config.py` 的 `Field(default=...)` |
| 加新 Hook 事件 | `src/core/orchestrator.py` 找 `hooks.emit(...)` 位置 |

---

## 7. 关键设计约束（违反会被 CR 打回）

完整见 `CLAUDE.md` 的 "Architecture Constraints" 一节：

1. 不要给 `DecisionSource` 加 `TIMEOUT_DEFAULT`
2. Reviewer Agent（Judge/PlannerJudge）只收 `ReadOnlyStateView`
3. Executor 写文件必须走 `apply_with_snapshot()`
4. Plan dispute 不修改 `risk_level`
5. HumanInterface 不填默认值
6. `plan_revision_rounds >= max` 转 `AWAITING_HUMAN`，不是 `FAILED`
7. PlannerJudge 通过后：有 HUMAN_REQUIRED 文件 → AWAITING_HUMAN，否则直接 AUTO_MERGING

---

## 8. 测试风格

- `tests/unit/` — 不打 LLM API；用 `patch_llm_factory` fixture mock
- `tests/integration/` — 打真 API；本地手跑；**不在 CI**
- `asyncio_mode = "auto"` 已全局开启；不用加 `@pytest.mark.asyncio`
- `mypy src` strict 模式必须通过
- 覆盖率目标 80%

---

## 9. 提 PR 前自检

```bash
pytest tests/unit/ -q
mypy src
ruff check src/
ruff format src/ --check
```

---

## 10. 遇到问题找谁

- 架构类问题 → 先翻 `CLAUDE.md` 和 `../architecture.md`
- 设计演进 / 加固项背景 → `../multi-agent-optimization-from-merge-experience.md`
- 参考外部项目的思路 → `../references/` 三篇分析文档
