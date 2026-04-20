# openai-agents-python 参考分析

**来源**：https://github.com/openai/openai-agents-python  
**分析日期**：2026-04-19  
**目的**：识别对 CodeMergeSystem 有借鉴价值的设计模式

---

## 1. 总体对比定位

| 维度 | openai-agents-python | CodeMergeSystem |
|------|---------------------|-----------------|
| 执行模型 | 网状对话（agent 动态选择下一个 agent） | 线性流水线（状态机驱动 Phase） |
| agent 间协作 | Handoff（LLM 决策移交） | Orchestrator 显式调度 |
| 上下文传递 | `RunContextWrapper[TContext]` 可变对象 | `MergeState`（状态）+ `PhaseContext`（DI） |
| 可恢复性 | 内存 `RunState` | 磁盘 `Checkpoint`（更完善） |
| 并行原语 | `asyncio.gather`（任务级） | `PhaseRunner(semaphore)`（批量文件级，更完善） |
| 流式输出 | token 级 + run item 级 | WebSocket activity 推送 |

两者定位不同：openai-agents-python 是通用对话式 agent 框架，CodeMergeSystem 是领域专用流水线。Handoff、Token Streaming、Tool 注册等核心特性**不适合直接移植**。

---

## 2. 有价值的借鉴点

### 2.1 LLM 调用钩子（高优先级）

**openai-agents-python 的设计**：`RunHooksBase` / `AgentHooksBase` 提供 `on_llm_start` / `on_llm_end` 回调，携带完整的 input items 和 `ModelResponse`，使外部消费者（监控、TUI）无需侵入 agent 代码即可订阅 LLM 事件。

**CodeMergeSystem 现状**：`TraceLogger` 是被动写入（JSONL 平面日志），`HookManager` 仅 emit `phase:before/after` 和 `merge:complete`，没有 agent 级或 LLM 级的钩子事件。

**建议实现**：

在 `HookManager` 新增两个标准事件，在 `BaseAgent._call_llm_with_retry` 的成功/失败分支 emit：

```python
# src/core/hooks.py — 新增事件常量
HOOK_LLM_START = "agent:llm_start"
# kwargs: agent, model, provider, prompt_chars, estimated_tokens, phase
HOOK_LLM_END = "agent:llm_end"
# kwargs: agent, model, provider, elapsed, success, response_chars, attempt

# src/agents/base_agent.py — BaseAgent 新增字段
self._hooks: HookManager | None = None

def set_hooks(self, hooks: HookManager) -> None:
    self._hooks = hooks

# 在 _call_llm_with_retry 成功分支 return 前：
if self._hooks:
    await self._hooks.emit(
        "agent:llm_end",
        agent=self.agent_type.value,
        model=routed_model,
        elapsed=elapsed,
        success=True,
        response_chars=resp_len,
    )

# src/core/orchestrator.py — _inject_memory 仿照模式，新增 _inject_hooks
def _inject_hooks(self) -> None:
    for agent in self._all_agents:
        agent.set_hooks(self._hooks)
```

**涉及文件**：`src/agents/base_agent.py`、`src/core/hooks.py`、`src/core/orchestrator.py`

---

### 2.2 Output Guardrail 层（高优先级）

**openai-agents-python 的设计**：每个 agent 有 `input_guardrails` 和 `output_guardrails` 列表，guardrail 是结构化校验对象，并行执行，触发时抛 `OutputGuardrailTripwireTriggered` 异常，在 LLM 输出**进入 session state 之前**拦截。

**CodeMergeSystem 现状**：没有等价机制。`judge_agent.py` 是事后审查（phase 级），`smoke_test_agent.py` 是测试（run 级），`HumanInterface` 的 `HUMAN_REQUIRED` 路径仅针对配置标记的高风险文件。LLM 输出的结构校验隐藏在各 agent 的 `except Exception` 分支里，不统一。

**已知风险场景**（guardrail 可拦截）：
- `PlannerAgent` 返回空 `phases` 列表或所有文件均为 `HUMAN_REQUIRED` 的异常计划
- `ExecutorAgent` 生成的 patch 覆盖了未在计划中的文件
- `JudgeAgent` 返回 confidence 异常低但未标记 DISPUTED 的结果

**建议接口**：

```python
# 新文件: src/agents/guardrails.py
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Generic, Literal, TypeVar
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

@dataclass(frozen=True)
class GuardrailResult:
    passed: bool
    triggered: bool = False
    reason: str = ""
    severity: Literal["warn", "block"] = "warn"

class OutputGuardrail(ABC, Generic[T]):
    name: str

    @abstractmethod
    async def run(self, output: T, state: MergeState) -> GuardrailResult: ...

# 内置 guardrail 示例
class EmptyPlanGuardrail(OutputGuardrail[MergePlan]):
    name = "empty_plan"

    async def run(self, plan: MergePlan, state: MergeState) -> GuardrailResult:
        if not plan.phases:
            return GuardrailResult(False, triggered=True, reason="Plan has no phases", severity="block")
        return GuardrailResult(True)
```

调用位置：在 `BaseAgent._call_llm_with_retry` 返回结构化输出（`schema` 路径）后，或在对应 Phase 的 `after()` hook 里检查。

**涉及文件**：新建 `src/agents/guardrails.py`，`src/core/phases/planning.py`、`src/core/phases/auto_merge.py`

---

### 2.3 Phase Transition Span（中优先级）

**openai-agents-python 的设计**：`HandoffSpanData` 记录 `from_agent → to_agent`，`AgentSpanData` 记录 `name`、`handoffs`、`tools`，每个 span 有 `parent_id` 形成树状层级，可接入 OpenTelemetry exporter。

**CodeMergeSystem 现状**：`TraceLogger` 记录 LLM 调用级别的平面日志，`Orchestrator` 的 `phase:after` hook 已有 `elapsed`，但状态转换（`from_status → to_status`）没有专门的追踪记录。

**最小化改进**（不引入完整 span 层级）：在 `TraceLogger` 新增 `record_phase_transition`：

```python
# src/tools/trace_logger.py
def record_phase_transition(
    self,
    run_id: str,
    from_status: str,
    to_status: str,
    triggered_by: str,   # agent 名称
    elapsed: float,
    reason: str = "",
) -> None:
    self._write({
        "type": "phase_transition",
        "run_id": run_id,
        "from": from_status,
        "to": to_status,
        "agent": triggered_by,
        "elapsed": elapsed,
        "reason": reason,
        "ts": time.time(),
    })
```

在 `Orchestrator.run()` 的 `phase:after` emit 处同步调用，无需修改 PhaseContext 接口。

**涉及文件**：`src/tools/trace_logger.py`、`src/core/orchestrator.py`

---

### 2.4 ActivityEvent 结构化（中优先级）

**openai-agents-python 的设计**：`RunItemStreamEvent` 是 typed dataclass，`event_type` 字段是 discriminated union，TUI/客户端可以基于类型做分发，不需要解析字符串。

**CodeMergeSystem 现状**：`on_activity(agent: str, action: str)` 只传两个字符串，TUI 消费方无法区分"开始"和"完成"，无法推算进度百分比，无法展示耗时。

**建议改进**：

```python
# src/core/phases/base.py — 替换 OnActivityCallback
from dataclasses import dataclass, field
from typing import Literal

@dataclass(frozen=True)
class ActivityEvent:
    agent: str
    action: str
    phase: str
    event_type: Literal["start", "progress", "complete", "error"]
    elapsed: float | None = None
    extra: dict = field(default_factory=dict)

OnActivityCallback = Callable[[ActivityEvent], None]
```

`_emit` 方法构造 `ActivityEvent` 后传出，WebSocket server 直接序列化为 JSON 推送。向后兼容：保留 `(agent, action)` 字符串接口为 deprecated，内部转为 `ActivityEvent`。

**涉及文件**：`src/core/phases/base.py`、`src/core/orchestrator.py`、`src/web/server.py`

---

### 2.5 ModelOutputError 异常（低优先级）

**openai-agents-python 的设计**：`ModelBehaviorError` 专门表示 LLM 返回非预期格式的情况，与网络错误（`TransportError`）和限流（`RateLimitError`）区分，携带 `raw_response` 和 `expected_schema`。

**CodeMergeSystem 现状**：JSON 解析失败归入 `FORMAT` 错误类别（`classify_error`），与结构化输出校验失败混在一起，重试逻辑不精确。

**建议**：

```python
# src/agents/base_agent.py 或 src/llm/error_classifier.py
class ModelOutputError(AgentError):
    """LLM 返回了合法 JSON 但不符合预期 schema。"""
    def __init__(self, raw: str, schema_name: str, detail: str) -> None:
        super().__init__(f"Model output doesn't match {schema_name}: {detail}", ...)
        self.raw = raw
        self.schema_name = schema_name
```

在 `complete_structured` 的 ValidationError catch 处抛出，而非直接 raise。使 `_call_llm_with_retry` 能区分"重试可能有效"（网络抖动）和"重试无意义"（模型持续返回错误格式）。

**涉及文件**：`src/llm/client.py`、`src/llm/error_classifier.py`

---

## 3. 不建议采纳的部分

| 特性 | 原因 |
|------|------|
| **Handoff pattern** | CodeMergeSystem 是状态机驱动的线性流水线，Handoff 是对话式 agent 网络的概念，强行引入会破坏 Phase 隔离边界 |
| **Token-level streaming** | 所有 LLM 调用都以结构化输出（Pydantic model）返回，流式解析 JSON 复杂度远超收益 |
| **Tool 注册机制** | 现有"工具"是 Python 函数直接调用（GitTool、PatchApplier），不是 LLM tool call，无注册必要 |
| **完整 span 层级** | PhaseRunner + TraceLogger 的平面日志已满足调试需求，parent_id 树状 span 对当前规模 over-engineering |
| **AgentHooksBase per-agent** | Orchestrator 已通过 setter DI 管理 agent 生命周期，per-agent hook 类增加继承层次但收益不明显 |

---

## 4. 优先级总结

| # | 功能 | 优先级 | 文件 | 预计改动量 |
|---|------|--------|------|-----------|
| 1 | LLM 调用钩子 `agent:llm_start/end` | **高** | `base_agent.py`, `hooks.py`, `orchestrator.py` | ~40 行 |
| 2 | Output Guardrail 层 | **高** | 新建 `agents/guardrails.py`, `phases/planning.py` | ~80 行 |
| 3 | Phase transition span | **中** | `trace_logger.py`, `orchestrator.py` | ~25 行 |
| 4 | ActivityEvent 结构化 | **中** | `phases/base.py`, `orchestrator.py`, `web/server.py` | ~30 行 |
| 5 | ModelOutputError 异常 | **低** | `llm/client.py`, `llm/error_classifier.py` | ~20 行 |

---

## 5. 相关文档

- [opensource-comparison.md](opensource-comparison.md) — git merge 工具横向对比
- [hermes-inspired-improvements.md](hermes-inspired-improvements.md) — Hermes 架构启发
- `src/agents/base_agent.py` — 当前 BaseAgent 实现（circuit breaker、retry budget、context compression）
- `src/core/hooks.py` — 当前 HookManager 实现
