# Core（`src/core/`）

> **版本**：2026-04-17
> Core 只管 **调度 + 状态 + 持久化**，不含合并业务逻辑。

---

## 1. 模块组成

```
src/core/
├── orchestrator.py         # ~400 LOC · Phase 分派器 + 依赖注入
├── state_machine.py        # ~115 LOC · 状态转换表 + Observer
├── phase_runner.py         # ~60 LOC  · 并发批量执行器（asyncio.gather）
├── checkpoint.py           # ~80 LOC  · 单滚动 JSON + atomic write + signal hook
├── message_bus.py          # ~90 LOC  · Agent 间消息（当前主要用于事件日志）
├── hooks.py                # ~100 LOC · 事件钩子（phase:before/after / merge:complete）
├── read_only_state_view.py # ~30 LOC  · 给 Reviewer 的只读状态视图
└── phases/
    ├── base.py             # Phase / PhaseContext / PhaseOutcome
    ├── initialize.py
    ├── planning.py
    ├── plan_review.py      # 618 LOC（Planner↔PlannerJudge 协商循环）
    ├── auto_merge.py
    ├── conflict_analysis.py
    ├── human_review.py
    ├── judge_review.py
    ├── report_generation.py
    └── _gate_helpers.py    # 门禁命令辅助
```

---

## 2. Orchestrator

### 2.1 职责边界
1. 初始化 `GitTool / GateRunner / StateMachine / MessageBus / Checkpoint / PhaseRunner / MemoryStore / PhaseSummarizer / HookManager / CostTracker / TraceLogger`
2. 通过 `AgentRegistry.create_all(config, git_tool=...)` 创建全部 Agent
3. 按 `status → Phase 类` 的 `PHASE_MAP` 循环调度
4. 处理 `PhaseOutcome`（checkpoint、memory、暂停）
5. 注册信号处理器，维护 per-run 日志目录
6. 全局异常兜底：任何未捕获异常都落到 `state.errors` 并置 FAILED

### 2.2 `PHASE_MAP`（源码）
```python
PHASE_MAP = {
    SystemStatus.INITIALIZED:        InitializePhase,
    SystemStatus.PLANNING:           PlanningPhase,
    SystemStatus.PLAN_REVIEWING:     PlanReviewPhase,
    SystemStatus.AUTO_MERGING:       AutoMergePhase,
    SystemStatus.ANALYZING_CONFLICTS:ConflictAnalysisPhase,
    SystemStatus.AWAITING_HUMAN:     HumanReviewPhase,
    SystemStatus.JUDGE_REVIEWING:    JudgeReviewPhase,
    SystemStatus.GENERATING_REPORT:  ReportGenerationPhase,
}
```

> 注意：PLAN_REVISING / PLAN_DISPUTE_PENDING / PAUSED 不直接映射 Phase —— 它们是 `PlanReviewPhase` / `AutoMergePhase` 内部回环使用的中间状态。

### 2.3 `_PHASE_ACTIVITY`
每个状态对应一个 `(agent, before_msg, after_msg)` 三元组，驱动 TUI 实时显示当前活动。

---

## 3. StateMachine

- `VALID_TRANSITIONS: dict[SystemStatus, list[SystemStatus]]` 是唯一权威
- `transition(state, target, reason)` 三件事：校验合法性、更新 `state.status` + `state.updated_at`、追加 `messages` 条目、广播观察者
- Observer 抛出的异常被吞掉（防止观察者搞崩主流程）

---

## 4. Checkpoint

关键不变量：

| 不变量 | 实现 |
|---|---|
| 原子写入 | 先写 `checkpoint.json.tmp`，rename 覆盖 |
| 单文件滚动 | 生产模式只留 `checkpoint.json`；`debug_checkpoints=true` 才额外落 tagged 快照 |
| Schema 不匹配即报错 | `MergeState.model_validate()` 失败 raise `RuntimeError`，不静默恢复半损坏 |
| 中断保护 | SIGINT/SIGTERM 打 `interrupt` 标签后 SystemExit(0) |

路径由 `src/cli/paths.py::get_run_dir()` 决定：
- 开发模式（源码仓库内）：`outputs/debug/checkpoints/`
- 生产模式（pip 安装后）：`<target_repo>/.merge/runs/<run_id>/`

---

## 5. PhaseRunner

简单包装 `asyncio.gather`，但带两个旋钮：
- `batch_size`（默认 10）— 每次并发提交的任务数
- `max_concurrency`（默认 5）— 同一时刻最大并发

用于 Auto Merge Phase 并发处理同批文件。不做错误聚合，由上层 Phase 决定跨文件的失败策略。

---

## 6. HookManager

```python
class HookManager:
    def on(event: str, callback)
    async def emit(event: str, **kwargs)
```

内置三个事件：`phase:before` / `phase:after` / `merge:complete`。
外部可挂接做 Slack 提醒、Grafana metrics、Telegram 推送。

---

## 7. MessageBus

当前实现是内存队列，主要用于：
- Agent 输出日志汇总
- 人工决策从 TUI 推到 HumanReviewPhase（通过 `ws_bridge`）

未来若引入跨进程 Agent，可以替换为 Redis/NATS 等，API 不变。

---

## 8. ReadOnlyStateView（P5 审查隔离）

```python
class ReadOnlyStateView:
    def __init__(self, state: MergeState): ...
    # 只暴露 getter，没有 setter
```

Judge 和 PlannerJudge 的 `run()` 接收的是 `ReadOnlyStateView`，尝试修改 state 会引发 AttributeError。这是编译期而非运行期约束，保证审查 Agent 不会"偷偷改 state"。

---

## 9. Phases 子模块

### 9.1 `base.py`
- `Phase` — 抽象基类，定义 `before / execute / after / run` 生命周期
- `PhaseContext` — `@dataclass(frozen=True)`，装所有共享依赖
- `PhaseOutcome` — `@dataclass(frozen=True)`，告知 Orchestrator 下一步

### 9.2 Phase 类职责速查

| Phase | 关键输入 | 关键输出 |
|---|---|---|
| InitializePhase | config | file_diffs, file_classifications, 六大扫描结果 |
| PlanningPhase | file_diffs + scanner 结果 | merge_plan |
| PlanReviewPhase | merge_plan | plan_review_log, review_conclusion, pending_user_decisions |
| AutoMergePhase | merge_plan | file_decision_records, applied_patches, gate_history |
| ConflictAnalysisPhase | 高风险 file_diffs | conflict_analyses, human_decision_requests |
| HumanReviewPhase | human_decision_requests | human_decisions |
| JudgeReviewPhase | 所有执行结果 | judge_verdict, smoke_test_report |
| ReportGenerationPhase | 一切 | merge_report.md/.json |

### 9.3 `_gate_helpers.py`
把 `GateRunner` + `baseline_parsers` 的调用封成若干辅助函数（跑门禁、解析 baseline、diff 新旧 failed_ids），给 AutoMergePhase 和 JudgeReviewPhase 复用。

---

## 10. 新增 Phase 的步骤

1. 在 `src/core/phases/` 新增 `your_phase.py`，继承 `Phase`，实现 `execute()`
2. 在 `src/core/phases/__init__.py` 导出
3. 如果需要新 `SystemStatus`：
   - 加到 `src/models/state.py::SystemStatus`
   - 加到 `src/core/state_machine.py::VALID_TRANSITIONS`
4. 在 `Orchestrator.PHASE_MAP` 和 `_PHASE_ACTIVITY` 中登记
5. 单元测试：构造最小 `PhaseContext` + `MergeState`，断言 `PhaseOutcome`
