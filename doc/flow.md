# 执行流程与状态机

> **对应代码**：`src/core/state_machine.py`、`src/core/orchestrator.py`、`src/core/phases/*.py`
> **版本**：2026-04-17

本文档描述系统运行时的状态机、Phase 之间的调度关系，以及人工决策与恢复的交互时序。

---

## 1. 状态机

### 1.1 `SystemStatus` 枚举

定义于 `src/models/state.py`：

| 状态 | 含义 | 对应 Phase |
|---|---|---|
| `INITIALIZED` | 已加载配置，尚未开始分析 | `InitializePhase` |
| `PLANNING` | Planner 正在生成合并计划 | `PlanningPhase` |
| `PLAN_REVIEWING` | PlannerJudge 正在审查计划 | `PlanReviewPhase` |
| `PLAN_REVISING` | Planner 正在修订计划 | `PlanReviewPhase`（内循环） |
| `AUTO_MERGING` | Executor 正在应用合并 | `AutoMergePhase` |
| `PLAN_DISPUTE_PENDING` | Executor 发起 Plan Dispute | `AutoMergePhase`（回到 Planner） |
| `ANALYZING_CONFLICTS` | ConflictAnalyst 正在分析 | `ConflictAnalysisPhase` |
| `AWAITING_HUMAN` | 等待人工决策（挂起） | `HumanReviewPhase` |
| `JUDGE_REVIEWING` | Judge 正在审查合并结果 | `JudgeReviewPhase` |
| `GENERATING_REPORT` | 输出最终报告 | `ReportGenerationPhase` |
| `COMPLETED` | 终态：成功 | — |
| `FAILED` | 终态：失败 | — |
| `PAUSED` | 通用暂停态（resume 后恢复原状态） | — |

### 1.2 状态转换表

权威定义在 `state_machine.VALID_TRANSITIONS`：

| From | 可达 To |
|---|---|
| INITIALIZED | PLANNING, FAILED |
| PLANNING | PLAN_REVIEWING, FAILED |
| PLAN_REVIEWING | AUTO_MERGING, PLAN_REVISING, AWAITING_HUMAN, PLANNING, FAILED |
| PLAN_REVISING | PLAN_REVIEWING, FAILED |
| AUTO_MERGING | ANALYZING_CONFLICTS, JUDGE_REVIEWING, PLAN_DISPUTE_PENDING, FAILED, PAUSED |
| PLAN_DISPUTE_PENDING | PLAN_REVISING, AWAITING_HUMAN |
| ANALYZING_CONFLICTS | AWAITING_HUMAN, JUDGE_REVIEWING, PLAN_DISPUTE_PENDING, FAILED |
| AWAITING_HUMAN | AUTO_MERGING, ANALYZING_CONFLICTS, JUDGE_REVIEWING, FAILED |
| JUDGE_REVIEWING | GENERATING_REPORT, AUTO_MERGING, AWAITING_HUMAN, ANALYZING_CONFLICTS, FAILED |
| GENERATING_REPORT | COMPLETED, FAILED |
| PAUSED | * 除 COMPLETED 外的任何非终态 |

**非法转换直接 raise `ValueError`**——代码不会静默吞掉。

### 1.3 状态观察者

`StateMachine.add_observer(cb)` 允许挂接回调，TUI 通过这个机制实时推送状态变更到前端（见 `src/web/ws_bridge.py`）。

---

## 2. Phase 生命周期

所有 Phase 继承自 `src/core/phases/base.py::Phase`：

```python
class Phase(ABC):
    async def before(state, ctx)    # 可选：前置检查
    async def execute(state, ctx)   # 必须：返回 PhaseOutcome
    async def after(state, outcome, ctx)  # 可选：清理
    async def run(state, ctx)       # 串联上述三步，Orchestrator 调用它
```

`PhaseContext` 是只读的依赖容器：`config / git_tool / gate_runner / state_machine / message_bus / checkpoint / phase_runner / memory_store / summarizer / trace_logger / emit / hooks / cost_tracker / agents`。

`PhaseOutcome` 告诉 Orchestrator 下一步：

```python
@dataclass(frozen=True)
class PhaseOutcome:
    target_status: SystemStatus  # 状态机转到哪
    reason: str                  # 记入 messages 的原因
    checkpoint_tag: str = ""     # 非空即落盘
    memory_phase: str = ""       # 非空即触发 memory 汇总
    extra: dict = {}             # extra["paused"]=True 让流程挂起
```

---

## 3. 主循环

`Orchestrator.run()` 的核心循环（简化）：

```python
while state.status in PHASE_MAP and state.status not in _TERMINAL:
    phase = PHASE_MAP[state.status]()
    ctx = self._build_context()
    await hooks.emit("phase:before", ...)
    outcome = await phase.run(state, ctx)
    await hooks.emit("phase:after", ...)
    if outcome.should_update_memory:
        self._update_memory(outcome.memory_phase, state)
    if outcome.should_checkpoint:
        self.checkpoint.save(state, outcome.checkpoint_tag)
    if outcome.extra.get("paused"):
        return state   # 交还控制权，等 resume
```

每个 Phase 自行完成 `state_machine.transition(state, target, reason)`；Outcome 中的 `target_status` 主要是语义记录。

---

## 4. 八阶段流程

### 4.0 文件风险分级（RiskLevel）

`FileClassifier` 在 Phase 0 为每个文件打上风险等级，后续所有 Phase 的路由均基于此：

| RiskLevel | 触发条件 | AutoMerge 行为 | 后续流向 |
|---|---|---|---|
| `AUTO_SAFE` | risk_score < 0.3 | Executor 自动合并 | → 批次 Judge 子审查 |
| `AUTO_RISKY` | 0.3 ≤ score < 0.6 | Executor 尝试合并 | → ConflictAnalysis 复审 |
| `HUMAN_REQUIRED` | score ≥ 0.6 或安全敏感 | 不进 Executor，直接生成 `HumanDecisionRequest` | → AWAITING_HUMAN |
| `DELETED_ONLY` | 纯删除操作（upstream 删除） | Executor 分析删除原因，生成带 rationale 的删除建议 | → AWAITING_HUMAN（人工确认后执行或保留） |
| `BINARY` | 二进制文件 | 无法文本合并，跳过执行 | → AWAITING_HUMAN |
| `EXCLUDED` | 匹配用户预设排除模式（锁文件、node_modules 等） | 完全跳过，不进入任何 Phase | — |

---

### Phase 0 — Initialize (`phases/initialize.py`)

1. 解析 upstream/fork ref，拿 git merge-base
2. **迁移感知检测**（`SyncPointDetector`，`config.migration` 控制）：
   - 若 `merge_base_override` 已设置 → 直接用指定 commit 作为 effective merge-base
   - 否则运行三阶段算法：
     1. **文件级检测**：对比 merge-base / fork / upstream 三处 blob hash，凡 upstream 修改而 fork 已同步（hash 相同）的文件标记为 synced
     2. **Patch-ID 验证**（可选）：对 hash 不同但语义相同的模糊文件，比对 patch-ID 升级为 synced
     3. **Commit 边界搜索**：按 oldest→newest 遍历 upstream commits（>50 条时二分），找出最后一个"全部文件已同步"的 commit 作为 effective merge-base
   - 检测结果（`SyncPointResult`）写入 `state.migration_info`；若检测到迁移则覆写 `state.merge_base_commit`
   - 报告中输出 migration section，含跳过 commit 数和 override 建议
3. `git diff` + `DiffParser` → `file_diffs`（使用 effective merge-base）
4. `FileClassifier` → `file_classifications` + `file_categories`
5. **加固扫描（按配置开关）**：
   - `PollutionAuditor` → 历史污染分类（迁移检测后运行，捕获遗漏边缘情况）
   - `ShadowConflictDetector` → M2
   - `InterfaceChangeExtractor` + `ReverseImpactScanner` → M3
   - `ScarListBuilder` → M1（自学习 customizations）
   - `SentinelScanner` → M5/M6
   - `ConfigLineRetentionChecker` → M5
   - `ConfigDriftDetector` → 配置漂移
6. 建立 `dependency_graph`（用于 Phase 排序）
7. → PLANNING

### Phase 1 — Planning (`phases/planning.py`)

薄层包装：调用 `planner_agent.run()` 生成 `MergePlan`，挂到 `state.merge_plan` → PLAN_REVIEWING。

### Phase 2 — Plan Review (`phases/plan_review.py`)

内部是 Planner ↔ PlannerJudge 的协商循环：

```
for round in 0..max_plan_revision_rounds:
    verdict = planner_judge.run(plan)
    record PlanReviewRound
    if verdict.approved:   → 下一步
    elif verdict.needs_revision:
        plan = planner.run(state, revision_request=verdict.issues)
        state.plan_revision_rounds += 1
    else: break

# 检查 pending_user_decisions
if conclusion == CONVERGED and no HUMAN_REQUIRED: → AUTO_MERGING
elif conclusion == CONVERGED and has HUMAN_REQUIRED: → AWAITING_HUMAN
else (MAX_ROUNDS / STALLED / LLM_FAILURE): → AWAITING_HUMAN  # 总是需要人工
```

无论是否收敛，都生成 `plan_review_<run_id>.md` 报告。

### Phase 3 — Auto Merge (`phases/auto_merge.py`)

执行模型：**层间串行、层内并行**。

#### 3.1 入口前置分流（不进 Executor）

在进入执行循环之前，对全部批次扫描一遍：

- `HUMAN_REQUIRED` 批次 → 直接生成 `HumanDecisionRequest`，写入 `state.pending_user_decisions`
- `DELETED_ONLY` 批次 → Executor 分析删除原因（重构遗留？误删？配置清理？），生成带 rationale 的删除建议，同样写入 `state.pending_user_decisions`
- `BINARY` / `EXCLUDED` 批次 → 跳过
- **若此时已有 `pending_user_decisions`，立即转 AWAITING_HUMAN**，在冲突合并发生之前先取得人工决策

#### 3.2 Cherry-pick Replay（Executor 运行之前）

受 `history.enabled` + `history.cherry_pick_clean` 双开关控制，**早于主循环**执行：

```
replayable = state.replayable_commits   # Phase 0 已分类：所有文件均属 Cat B / D_MISSING
CommitReplayer.replay_clean_commits(git_tool, replayable):
    for commit in replayable:
        ok = git cherry-pick <sha>
        if ok:
            replayed_files.extend(commit["files"])   # 记录，Executor 后续跳过
        else:
            git cherry-pick --abort
            → 降级：由 Executor 文件级合并（不保留原 commit）
state.replayed_files = replayed_files
```

**效果**：replayable commits 保留原始作者、时间戳、commit message 和 SHA 祖先链；fork 未修改过的 upstream 新增/修改文件无需 LLM 介入。

#### 3.3 层内并行执行（`AUTO_SAFE` / `AUTO_RISKY`）

```
for layer in sorted(layers):
    batches = AUTO_SAFE + AUTO_RISKY batches in this layer
    results = await asyncio.gather(*[executor.run(batch) for batch in batches])
    # 每个文件：若在 replayed_files 中则跳过；否则 apply_with_snapshot()
    # 失败自动回滚，产出 FileDecisionRecord
```

同一层内批次无跨文件依赖，全部并行执行；不同层之间严格串行（遵守 `dependency_graph`）。

#### 3.4 层完成后批次 Judge 子审查

每层并行结束后，触发轻量 Judge 子审查：

```
batch_verdict = await judge.review_batch(layer_results, state)
if batch_verdict.approved:
    completed_layers.add(layer_id)
    continue  # 下一层
elif batch_verdict.needs_repair and repair_rounds < max_batch_repair_rounds:
    await executor.repair(batch_verdict.repair_instructions, state)
    # 修复后重审，仍不通过则进入协商
elif executor 有异议:
    # 进入 Executor ↔ Judge 协商（见 3.5）
else:
    → AWAITING_HUMAN
```

#### 3.5 Executor ↔ Judge 协商

Judge 裁决后，Executor 可接受并修复，也可提出反驳（附合并证据与理由）：

```
for dispute_round in 0..max_dispute_rounds:
    rebuttal = executor.build_rebuttal(batch_verdict.issues, merge_evidence)
    batch_verdict = await judge.re_evaluate(rebuttal)
    if batch_verdict.approved:
        break  # 达成一致
# 超出 max_dispute_rounds 仍无共识 → AWAITING_HUMAN
```

#### 3.6 层级 gate_commands 与 Phase Commit

- 每层通过子审查后，运行该层 `gate_commands`；连续失败 ≥ `max_consecutive_failures` → AWAITING_HUMAN
- Executor 发现计划不合理 → `raise_plan_dispute()` → PLAN_DISPUTE_PENDING
- 全部层完成且无 dispute，运行 `GitCommitter.commit_phase_changes()`（受 `history.commit_after_phase` 控制）：

```
committable = phase_changed_files - replayed_files   # 排除已 cherry-pick 的文件
git add <committable>
git commit -m "merge(auto_merge): resolve N files\n\nUpstream commits:\n..."
→ SHA 写入 state.merge_commit_log
```

- → ANALYZING_CONFLICTS（处理 AUTO_RISKY）或 JUDGE_REVIEWING（仅有 AUTO_SAFE）

### Phase 4 — Conflict Analysis (`phases/conflict_analysis.py`)

仅处理 `RiskLevel.AUTO_RISKY` 的文件（`HUMAN_REQUIRED` 已在 Phase 3 入口前置处理）：

- ConflictAnalyst 读 three-way-diff，产出 `ConflictAnalysis`
- 低置信度 或 `LOGIC_CONTRADICTION` → 生成 `HumanDecisionRequest` → AWAITING_HUMAN
- 高置信度 → 回到 Executor 自动合并（如果 Plan 允许）

### Phase 5 — Human Review (`phases/human_review.py`)

- 入口：AWAITING_HUMAN
- `HumanInterface.run()` 渲染决策模板（Markdown + YAML）
- 从 CLI/TUI/文件加载 `human_decisions`
- **永远不填默认值**：未决 item 保持 `ESCALATE_HUMAN`，等下次人工
- 所有 item 决完 → 转到下一阶段（由 Plan/Analysis 上下文决定）

### Phase 6 — Judge Review (`phases/judge_review.py`)

#### 6.1 确定性流水线（优先于 LLM）

- customization 验证 / gate baseline-diff / shadow 复检 / sentinel 复扫
- 此阶段发现的问题记入 `judge_verdict.issues`，作为协商起点

#### 6.2 Executor ↔ Judge 协商循环

不再有"VETO 硬终止"。Judge 的裁决是协商的起点，而非终点：

```
for round in 0..max_dispute_rounds:
    readonly = ReadOnlyStateView(state)
    verdict = await judge.run(readonly)          # Judge 输出裁决 + 问题列表

    if verdict.approved:
        break  # 达成一致

    rebuttal = await executor.build_rebuttal(    # Executor 接受或反驳
        verdict.issues, merge_evidence=state
    )
    if rebuttal.accepts_all:
        await executor.repair(rebuttal.repair_instructions, state)
        continue  # 修复后重审

    verdict = await judge.re_evaluate(rebuttal)  # Judge 重新评估反驳
    if verdict.approved:
        break  # Judge 接受反驳，达成一致

# 超出 max_dispute_rounds → AWAITING_HUMAN
```

- 双方在每轮交换的不是"命令"，而是"证据 + 理由"
- Judge 可维持原裁决，也可在充分理由下修改
- 无需区分 VETO / CONDITIONAL / FAIL：所有分歧要么协商解决，要么上升人工

#### 6.3 最终路由

- `verdict.approved` → 跑 smoke tests（如启用）→ GENERATING_REPORT
- 超出协商轮数仍无共识 → AWAITING_HUMAN（人工最终裁决）

### Phase 7 — Report Generation (`phases/report_generation.py`)

- 生成 `merge_report.md` + `merge_report.json`
- 生成 `plan_review_<run_id>.md`（若未生成）
- 汇总 cost、context 利用率、LLM trace 摘要
- → COMPLETED

---

## 5. 人工决策与 resume

```
[Phase 暂停]  AWAITING_HUMAN + checkpoint 落盘
    │
    │ 用户操作（三选一）：
    │   ① TUI 中点击决策按钮
    │   ② merge run --export-decisions decisions.yaml  → 编辑 → 下一次自带文件
    │   ③ merge resume --run-id <id> --decisions decisions.yaml
    ▼
[Checkpoint 加载] Orchestrator.run(state)
    │
    │ HumanReviewPhase 消费 decisions → 写回 state.human_decisions
    │
    ▼
[根据上下文转移到下一状态]
    - 来自 conflict_analysis → AUTO_MERGING 或 JUDGE_REVIEWING
    - 来自 plan_review      → AUTO_MERGING
    - 来自 judge_review     → AUTO_MERGING 或 COMPLETED（人工接受现状）
```

没有超时回退：`DecisionSource` 枚举故意不含 `TIMEOUT_DEFAULT`，未决 item 的 `decision` 保持 `ESCALATE_HUMAN`。

---

## 6. 异常与容错

- `Orchestrator.run()` 外层捕获任意异常 → 写 `state.errors` → 强制 `state.status = FAILED` → Checkpoint `failed` → 返回
- `apply_with_snapshot()` 失败自动回滚本文件，记录 `is_rolled_back=True`
- LLM 错误分类见 `llm/error_classifier.py`；`base_agent.py` 管重试与熔断
- SIGINT/SIGTERM：`Checkpoint.register_signal_handler()` 接管，打 `interrupt` 标记后退出

---

## 7. Hook 事件

Orchestrator 通过 `HookManager.emit()` 在关键时刻广播：

| 事件 | 传入 kwargs |
|---|---|
| `phase:before` | `phase`, `status`, `state` |
| `phase:after` | `phase`, `status`, `outcome`, `elapsed`, `state` |
| `merge:complete` | `state`, `elapsed` |

外部系统可挂接这些事件做 Slack 通知、Grafana 推送等（`src/core/hooks.py`）。
