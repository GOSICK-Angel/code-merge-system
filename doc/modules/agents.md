# Agents（`src/agents/`）

> **版本**：2026-04-17
> 共 7 个 Agent + 1 基类 + 1 Registry。Orchestrator 只依赖 Registry，Agent 之间不直接互调。

---

## 1. 总览

| Agent | 文件 | 主要 LLM | 角色 | 写权限 |
|---|---|---|---|---|
| Planner | `planner_agent.py` | Claude Opus | 生成 MergePlan | ❌ |
| PlannerJudge | `planner_judge_agent.py` | GPT-4o | 审查 MergePlan | ❌（只读） |
| ConflictAnalyst | `conflict_analyst_agent.py` | Claude Sonnet | 高风险冲突语义分析 | ❌ |
| Executor | `executor_agent.py` | GPT-4o | 应用合并 | ✅ 唯一写权限 |
| Judge | `judge_agent.py` | Claude Opus | 审查合并结果 | ❌（只读） |
| HumanInterface | `human_interface_agent.py` | Claude Haiku | 人工决策模板/汇总 | ❌ |
| SmokeTest | `smoke_test_agent.py` | — | 执行冒烟测试（P1-3） | ❌ |

**Reviewer-Executor Provider 隔离**：审查类 Agent（Judge、PlannerJudge）故意用与 Executor 不同的 LLM 提供商，防止共谋偏差。

---

## 2. `BaseAgent`（`base_agent.py`, 461 LOC）

所有 Agent 继承该基类。它把所有通用能力收敛到一起：

### 2.1 核心能力
- **LLM 客户端构建**：从 `AgentLLMConfig` 构造，按 `api_key_env` 初始化凭据池
- **重试与错误分类**：接入 `error_classifier`，八类错误各走不同策略
- **熔断器**：`CIRCUIT_BREAKER_THRESHOLD=3`，对 `AUTH_PERMANENT` / `FORMAT` 分类累计 ≥ 3 即熔断
- **Rate-limit 等待**：最多 `MAX_RATE_LIMIT_WAITS=5` 轮退避
- **Token 预算**：通过 `TokenBudget` + `get_context_window(model)` 预算上下文
- **上下文压缩**：`ContextCompressor`（保头保尾 + 中段摘要）
- **智能路由**：`model_router.select_model()`，配合 `cheap_model` 降档
- **记忆注入**：`set_memory_store()` 把 `MemoryStore` 注入，`LayeredMemoryLoader` 组装 L0/L1/L2
- **成本追踪**：`set_cost_tracker()`；每次 LLM 调用自动记账
- **Trace 日志**：`set_trace_logger()`；prompt/response 可回放

### 2.2 抽象接口
```python
class BaseAgent(ABC):
    @abstractmethod
    async def run(self, state: MergeState, **kwargs) -> SomeOutput:
        ...
```
具体返回类型由子类决定（如 Planner → `MergePlan`，Judge → `JudgeVerdict`）。

### 2.3 错误类型
- `CircuitBreakerOpen` — 熔断打开
- `AgentError(classification)` — 非可重试错误，附带分类信息
- `AgentExhaustedError(last_classification)` — 重试耗尽

---

## 3. `AgentRegistry`（`registry.py`）

集中式工厂：解耦 Orchestrator 与具体 Agent 类。

```python
# Agent 文件末尾自注册：
AgentRegistry.register(
    "planner",
    PlannerAgent,
    extra_kwargs=["git_tool"]   # 声明需要注入的额外参数
)

# Orchestrator 这样创建全部 Agent：
agents = AgentRegistry.create_all(config, git_tool=self.git_tool)
```

`register()` 接收：`name`（与 `AgentsLLMConfig` 字段名一致）、`factory`（通常是类本身）、`extra_kwargs`（声明除 `AgentLLMConfig` 外还需要什么共享依赖）。

测试时可用 `AgentRegistry.clear()` 重置，便于挂 stub。

---

## 4. 七个具体 Agent

### 4.1 `PlannerAgent`（`planner_agent.py`, 1003 LOC，最复杂）

输入：整个 `MergeState`（file_diffs、file_classifications、六大扫描结果等）
输出：`MergePlan`

关键逻辑：
- **两阶段 Prompting**：先生成整体策略，再按层生成 PhaseFileBatch
- **层依赖拓扑排序**：消费 `DEFAULT_LAYERS` 或用户覆盖的 `layer_config.custom_layers`
- **HUMAN_REQUIRED 强制**：`security_sensitive.patterns` 命中的文件一律 HUMAN_REQUIRED
- **修订模式**：`revision_request` 参数携带 PlannerJudge 的 issues，驱动定向修订
- **Plan Dispute 响应**：处理 Executor 发来的 `PlanDisputeRequest`，调整 Plan

### 4.2 `PlannerJudgeAgent`（`planner_judge_agent.py`, 98 LOC）

- 接收 `ReadOnlyStateView`（由 `src/core/read_only_state_view.py` 提供）
- 输出 `PlanJudgeVerdict`（APPROVED / REVISE / REJECT + issues 列表）
- 只读；决不修改 state

### 4.3 `ConflictAnalystAgent`（`conflict_analyst_agent.py`, 219 LOC）

- 针对单个高风险文件做 three-way-diff 语义诊断
- 输出 `ConflictAnalysis`：诊断结论、建议 MergeDecision、置信度、rationale
- 置信度 < `human_escalation` → 自动生成 `HumanDecisionRequest`

### 4.4 `ExecutorAgent`（`executor_agent.py`, 534 LOC）— **唯一写权限**

- 遍历 `MergePlan.phases` 逐文件处理
- 简单决策（TAKE_TARGET / TAKE_CURRENT）直接走 `apply_with_snapshot()`
- SEMANTIC_MERGE 调 LLM 合成新内容后再写
- **Plan Dispute**：发现依赖/层级/规则矛盾时，raise `PlanDisputeRequest` 而非硬冲过去
- 失败自动回滚 + `is_rolled_back=True`

### 4.5 `JudgeAgent`（`judge_agent.py`, 889 LOC）

**最复杂的审查 Agent**，两段式：

1. **确定性流水线（不可被 LLM 覆盖）**：
   - `verify_customizations(CustomizationEntry)` — grep/line_retention/file_exists 等
   - Gate baseline-diff — 比较当前与 baseline 的 `failed_ids` 差集
   - Shadow 复检 — 确认 shadow_conflicts 已被解决
   - Sentinel 复扫 — 业务哨兵是否仍在
   - Config retention — 配置行保留率
   - 跨层断言 — `CrossLayerAssertion` 一致性
   - 任一项 VETO → `verdict=NEEDS_REPAIR`（不与 LLM 商量）

2. **LLM 审查**：
   - 针对未 VETO 的文件做语义检查
   - 生成 `JudgeVerdict`：APPROVED / NEEDS_REPAIR / ESCALATE

- 只读：接收 `ReadOnlyStateView`

### 4.6 `HumanInterfaceAgent`（`human_interface_agent.py`, 402 LOC）

- 生成决策模板（YAML + Markdown），最小化人工决策成本
- 解析 decisions.yaml 回填到 `state.human_decisions`
- 永远不填默认值（原则 P6）

### 4.7 `SmokeTestAgent`（`smoke_test_agent.py`, 70 LOC）

- 薄包装：把 `SmokeTestConfig.suites` 交给 `smoke_runner` 跑
- 产出 `SmokeTestReport` 挂到 state
- Judge Review Phase 在 LLM 审查通过后才调用

---

## 5. 典型交互时序

```
Planner
   │ generate plan
   ▼
PlannerJudge ──(REVISE × N 轮)──┐
   │                              │
   │ APPROVED                     │
   ▼                              │
Executor ──(Plan Dispute)──────► Planner（回到上面）
   │
   │ 对 HUMAN_REQUIRED 文件
   ▼
ConflictAnalyst ──(低置信度)──► HumanInterface
   │ 高置信度
   ▼
Executor（继续）
   │
   ▼
Judge ──(VETO / NEEDS_REPAIR)──► Executor
   │ APPROVED
   ▼
SmokeTest ──(失败)──► AWAITING_HUMAN
   │ 通过
   ▼
Report Generation
```

---

## 6. 开发新 Agent 的清单

1. 在 `src/agents/your_agent.py` 继承 `BaseAgent`
2. 定义 `async def run(...)`，返回 Pydantic 模型
3. 如需 `git_tool` 等额外依赖，在类末尾：
   ```python
   AgentRegistry.register("your_agent", YourAgent, extra_kwargs=["git_tool"])
   ```
4. 在 `src/core/orchestrator.py` 顶部 `import src.agents.your_agent  # noqa: F401`
5. 在 `AgentsLLMConfig` 中为它加默认 `AgentLLMConfig`
6. 决定 Agent 接入哪个 Phase（改 `src/core/phases/*.py`）
7. 在 `config/default.yaml` 中提供示例配置
8. 写单元测试：`tests/unit/test_your_agent.py`，用 `patch_llm_factory` mock 掉真实 LLM
