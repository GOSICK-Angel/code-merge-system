# Hermes Agent 启发的系统改善方案

> 基于 [NousResearch/hermes-agent](https://github.com/nousresearch/hermes-agent) 架构深度分析，交叉对比 CodeMergeSystem 现有架构，提取可落地的改善方向。
>
> 日期：2026-04-13

---

## 目录

1. [分析方法论](#1-分析方法论)
2. [差距总览](#2-差距总览)
3. [P0: Orchestrator 分阶策略化重构](#3-p0-orchestrator-分阶策略化重构)
4. [P0: 错误分类与智能恢复](#4-p0-错误分类与智能恢复)
5. [P1: Prompt 缓存与 Token 成本优化](#5-p1-prompt-缓存与-token-成本优化)
6. [P1: 上下文压缩分层策略](#6-p1-上下文压缩分层策略)
7. [P1: Agent 依赖注入与工厂模式](#7-p1-agent-依赖注入与工厂模式)
8. [P2: 凭证池与 Provider 容错轮转](#8-p2-凭证池与-provider-容错轮转)
9. [P2: 生命周期钩子系统](#9-p2-生命周期钩子系统)
10. [P2: 可观测性与洞察引擎](#10-p2-可观测性与洞察引擎)
11. [P3: Smart Model Routing](#11-p3-smart-model-routing)
12. [P3: 工具后端抽象层](#12-p3-工具后端抽象层)
13. [实施路线图](#13-实施路线图)
14. [附录：Hermes Agent 架构要点](#附录hermes-agent-架构要点)

---

## 1. 分析方法论

### 1.1 分析对象

| 维度 | Hermes Agent | CodeMergeSystem |
|------|-------------|-----------------|
| 定位 | 通用多平台 Agent 框架 | 专用代码合并编排系统 |
| 规模 | ~50+ 模块，57+ 工具 | ~72 文件，16 工具，~13.5K LOC |
| Agent 模型 | 单 Agent + 子 Agent 分叉 | 6 专用 Agent 流水线 |
| LLM 集成 | 多 Provider 统一抽象 | 双 Provider (Anthropic + OpenAI) |
| 状态管理 | Session + SQLite | MergeState + Checkpoint JSON |

### 1.2 分析原则

- **只借鉴适用于「专用编排系统」的模式**，不盲目引入通用框架的复杂性
- **优先解决已知痛点**：Orchestrator 臃肿、错误处理粗糙、Token 浪费
- **保持架构一致性**：所有改动必须兼容现有分层（models → tools → llm → agents → core → cli）

---

## 2. 差距总览

| 维度 | Hermes 做法 | 当前系统现状 | 差距等级 | 改善价值 |
|------|-----------|------------|---------|---------|
| **Phase 解耦** | 生命周期钩子 + 策略模式 | Orchestrator 单类 1,244 LOC 包含全部阶段 | **Critical** | 可测试性、可维护性 |
| **错误分类** | ErrorClassifier 按类型分策略恢复 | 宽泛 `try/except Exception` + 静默吞错 | **Critical** | 鲁棒性、可调试性 |
| **Prompt 缓存** | System_and_3 策略，~75% Token 节省 | 无缓存，每次调用全量发送 | **High** | 成本降低 50-75% |
| **上下文压缩** | 三阶段分层压缩（清理→边界→摘要） | 单一截断策略，可能丢失关键上下文 | **High** | 上下文利用率 |
| **Agent 实例化** | 懒加载 + Provider 抽象 | Orchestrator.__init__ 硬编码 6 个 Agent | **High** | 解耦、可测试 |
| **凭证管理** | 多源池化 + 冷却轮转 | 单 key 直连，无容错 | **Medium** | 可用性 |
| **生命周期钩子** | 事件驱动、错误隔离、支持通配符 | 无钩子系统 | **Medium** | 可扩展性 |
| **可观测性** | 洞察引擎 + 成本追踪 + 速率监控 | TraceLogger 基础记录 | **Medium** | 运营洞察 |
| **智能路由** | 短消息自动用廉价模型 | 固定模型分配 | **Low** | 成本微调 |
| **工具后端** | 同一工具可换底层实现 | 工具直连单一实现 | **Low** | 灵活性 |

---

## 3. P0: Orchestrator 分阶策略化重构

### 3.1 问题分析

当前 `src/core/orchestrator.py`（1,244 LOC）是整个系统的最大技术债：

- 6 个 Phase 的执行逻辑全部内联在 `_run_phase1` ~ `_run_phase6` 方法中
- 每个 Phase 方法 100-400 LOC，职责混杂（Agent 调用、状态修改、内存注入、Gate 执行、检查点保存）
- 无法单独测试某个 Phase 的逻辑
- 新增或修改 Phase 必须修改 Orchestrator 类

### 3.2 Hermes 启发

Hermes 使用 **生命周期钩子 + 策略模式** 将 Agent 执行循环分解为独立阶段：
- 每个阶段有明确的 `before/execute/after` 生命周期
- 阶段间通过事件通知而非直接调用解耦
- 失败不阻塞管线（错误隔离）

### 3.3 改造方案

#### 3.3.1 引入 Phase 基类

```python
# src/core/phases/base.py
from abc import ABC, abstractmethod

class Phase(ABC):
    """All phases implement this contract."""

    @abstractmethod
    async def execute(self, state: MergeState, ctx: PhaseContext) -> PhaseResult:
        ...

    async def before(self, state: MergeState, ctx: PhaseContext) -> None:
        """Hook: runs before execute. Override for pre-checks."""

    async def after(self, state: MergeState, result: PhaseResult, ctx: PhaseContext) -> None:
        """Hook: runs after execute. Override for cleanup / checkpoint."""
```

#### 3.3.2 PhaseContext 封装依赖

```python
# src/core/phases/context.py
@dataclass(frozen=True)
class PhaseContext:
    config: MergeConfig
    git: GitTool
    memory: MemoryStore
    checkpoint: CheckpointManager
    message_bus: MessageBus
    logger: logging.Logger
```

#### 3.3.3 各 Phase 独立文件

```
src/core/phases/
├── __init__.py
├── base.py                    # Phase ABC + PhaseContext + PhaseResult
├── initialize.py              # Phase 0: diff 获取、文件分类、配置漂移检测
├── planning.py                # Phase 1: Planner Agent 生成合并计划
├── plan_review.py             # Phase 1.5: PlannerJudge 审查 + 修订循环
├── auto_merge.py              # Phase 2: Executor 处理安全文件
├── conflict_analysis.py       # Phase 3: ConflictAnalyst 处理高风险文件
├── human_review.py            # Phase 4: 人工决策收集
├── judge_review.py            # Phase 5: Judge 审查合并结果
└── report_generation.py       # Phase 6: 报告生成
```

#### 3.3.4 Orchestrator 瘦身

重构后的 Orchestrator 只负责：
1. 按状态机顺序调度 Phase
2. Phase 间状态传递
3. 全局异常兜底

```python
# src/core/orchestrator.py (refactored, ~200 LOC)
class Orchestrator:
    def __init__(self, config: MergeConfig, phase_registry: PhaseRegistry):
        self._phases = phase_registry
        self._state_machine = StateMachine()

    async def run(self, state: MergeState) -> MergeState:
        ctx = self._build_context()
        for phase_key in self._state_machine.plan():
            phase = self._phases.get(phase_key)
            await phase.before(state, ctx)
            result = await phase.execute(state, ctx)
            state = self._apply_result(state, result)
            await phase.after(state, result, ctx)
            self._checkpoint(state)
        return state
```

### 3.4 预期收益

| 指标 | 改造前 | 改造后 |
|------|-------|-------|
| Orchestrator LOC | 1,244 | ~200 |
| 单 Phase 可测试性 | 不可能 | 完全可测试 |
| 新增 Phase 工作量 | 修改 Orchestrator | 新增一个文件 |
| Phase 间耦合度 | 高（共享 self） | 低（PhaseContext 注入） |

### 3.5 关键约束

- `MergeState` 保持不可变模式：Phase.execute 返回 `PhaseResult`，由 Orchestrator 生成新 state
- 状态机转换规则不变：仍由 `StateMachine` 守护合法转换
- 向后兼容：checkpoint 格式不变，已有 checkpoint 可被新 Orchestrator 恢复

---

## 4. P0: 错误分类与智能恢复

### 4.1 问题分析

当前系统的错误处理存在严重不足：

- `MessageBus` 中 `try/except Exception: pass` 静默吞错
- BaseAgent 的 circuit breaker 只计数，不区分错误类型
- LLM 调用失败一律重试，不区分可恢复（429 限流）和不可恢复（403 鉴权失败）
- 无 LLM 返回内容校验失败的分级恢复

### 4.2 Hermes 启发

Hermes 的 `error_classifier.py` 将错误分为 7 大类，每类带有明确的恢复策略：

| 错误类型 | 恢复动作 |
|---------|---------|
| Auth (transient) | 轮转凭证，重试 |
| Auth (permanent) | 跳过 Provider，降级 |
| Quota/Rate Limit | 冷却 1 小时 |
| Server Overload | 指数退避重试 |
| Context Overflow | 压缩上下文后重试 |
| Transport | 退避重试 |
| Format | 重新格式化或中止 |

### 4.3 改造方案

#### 4.3.1 错误分类器

```python
# src/llm/error_classifier.py
@dataclass(frozen=True)
class ClassifiedError:
    category: ErrorCategory       # AUTH_TRANSIENT | AUTH_PERMANENT | RATE_LIMIT | OVERLOAD | CONTEXT_OVERFLOW | TRANSPORT | FORMAT | UNKNOWN
    retryable: bool
    should_compress: bool         # context overflow → compress and retry
    should_rotate: bool           # auth failure → try next key
    should_fallback: bool         # permanent failure → try fallback provider
    cooldown_seconds: int         # 0 = immediate retry, >0 = wait
    message: str

def classify_error(error: Exception, provider: str) -> ClassifiedError:
    """Classify LLM errors by HTTP status, message patterns, and provider."""
```

#### 4.3.2 改造 BaseAgent 重试逻辑

```python
# src/agents/base_agent.py — _call_llm_with_retry 改造
async def _call_llm_with_retry(self, messages, ...):
    for attempt in range(max_retries):
        try:
            return await self._client.chat(messages, ...)
        except Exception as e:
            classified = classify_error(e, self._provider)
            if not classified.retryable:
                raise AgentError(classified.message, classified) from e
            if classified.should_compress:
                messages = self._compress_context(messages)
            delay = self._jittered_backoff(attempt, classified.cooldown_seconds)
            await asyncio.sleep(delay)
    raise AgentExhaustedError(f"{self.name}: {max_retries} retries exhausted")
```

#### 4.3.3 Jitter 退避（借鉴 Hermes 去关联种子）

```python
# src/llm/retry_utils.py
import threading, time

_counter = 0
_lock = threading.Lock()

def jittered_backoff(attempt: int, base: float = 1.0, max_delay: float = 60.0) -> float:
    global _counter
    with _lock:
        _counter += 1
        tick = _counter
    seed = (time.time_ns() ^ (tick * 0x9E3779B9)) & 0xFFFFFFFF
    rng = random.Random(seed)
    delay = min(base * (2 ** attempt), max_delay)
    jitter = rng.uniform(0, delay * 0.5)
    return delay + jitter
```

### 4.4 预期收益

- Rate limit (429) 不再浪费重试次数，直接进入冷却
- Context overflow 自动压缩后重试，而非直接失败
- Auth 失败快速定位，不再混淆为"网络问题"
- 去关联 jitter 避免多 Agent 并发时的"惊群效应"

---

## 5. P1: Prompt 缓存与 Token 成本优化

### 5.1 问题分析

当前系统每次 LLM 调用均发送完整 prompt，无缓存机制。对于 6 Agent 流水线、可能多轮修订的场景，Token 消耗大量浪费在重复发送 system prompt 和不变的上下文上。

### 5.2 Hermes 启发

Hermes 实现了 **System_and_3 缓存策略**：
- System prompt 标记为可缓存（跨轮次不变）
- 最近 3 条非 system 消息标记为可缓存（滚动窗口）
- 节省约 75% 输入 Token 成本

### 5.3 改造方案

#### 5.3.1 Anthropic 原生缓存支持

Anthropic API 原生支持 `cache_control` 参数，当前系统未使用。

```python
# src/llm/prompt_caching.py
from enum import Enum

class CacheStrategy(str, Enum):
    NONE = "none"
    SYSTEM_ONLY = "system_only"
    SYSTEM_AND_RECENT = "system_and_recent"  # Hermes System_and_3

def apply_cache_markers(
    messages: list[dict],
    strategy: CacheStrategy = CacheStrategy.SYSTEM_AND_RECENT,
    recent_count: int = 3,
) -> list[dict]:
    """Mark messages with cache_control for Anthropic prompt caching."""
    if strategy == CacheStrategy.NONE:
        return messages

    result = []
    for msg in messages:
        new_msg = {**msg}
        if msg["role"] == "system":
            new_msg["cache_control"] = {"type": "ephemeral"}
        result.append(new_msg)

    if strategy == CacheStrategy.SYSTEM_AND_RECENT:
        non_system = [m for m in result if m["role"] != "system"]
        for m in non_system[-recent_count:]:
            m["cache_control"] = {"type": "ephemeral"}

    return result
```

#### 5.3.2 集成到 AnthropicClient

```python
# src/llm/client.py — AnthropicClient.chat() 修改点
async def chat(self, messages, ..., cache_strategy=CacheStrategy.SYSTEM_AND_RECENT):
    cached_messages = apply_cache_markers(messages, cache_strategy)
    response = await self._client.messages.create(
        model=self._model,
        messages=cached_messages,
        ...
    )
```

#### 5.3.3 在 AgentLLMConfig 中增加配置

```yaml
agents:
  planner:
    provider: anthropic
    model: claude-opus-4-6
    cache_strategy: system_and_recent   # none | system_only | system_and_recent
```

### 5.4 预期收益

| Agent | 平均调用次数 | 估算 Token 节省 |
|-------|------------|---------------|
| Planner | 1-3 | 60-70%（system prompt ~4K tokens） |
| PlannerJudge (OpenAI) | 2-4 | 不适用（OpenAI 无此机制） |
| ConflictAnalyst | N（按文件数） | 50-60%（system prompt 复用） |
| Executor | N | 40-50% |
| Judge | 1-4 | 70-75%（多轮修订场景） |
| HumanInterface | 1 | 30% |

**整体估算：Anthropic 侧 Token 成本降低 50-60%。**

---

## 6. P1: 上下文压缩分层策略

### 6.1 问题分析

当前 `ContextAssembler`（`src/llm/context.py`）在 Token 超限时采用 **单一截断策略**：按优先级截断最长的 section。这可能导致：

- 关键 diff 内容被截断
- 工具调用结果与调用请求脱节
- 无法区分"可安全丢弃"和"必须保留"的内容

### 6.2 Hermes 启发

Hermes 的 `context_compressor.py` 采用 **三阶段分层压缩**：

1. **阶段 1（零成本清理）**：清理旧工具输出（>200 字符替换为占位符），不消耗 LLM Token
2. **阶段 2（边界保护）**：识别工具调用/结果对，确保不产生孤立的请求或响应
3. **阶段 3（智能摘要）**：对中间轮次进行 LLM 摘要，保护头部（system prompt）和尾部（最近上下文）

### 6.3 改造方案

#### 6.3.1 三阶段压缩器

```python
# src/llm/context_compressor.py
class ContextCompressor:
    def __init__(self, token_budget: TokenBudget):
        self._budget = token_budget

    async def compress(self, sections: list[ContextSection]) -> list[ContextSection]:
        # Phase 1: Zero-cost cleanup
        sections = self._prune_stale_outputs(sections)
        if self._budget.fits(sections):
            return sections

        # Phase 2: Boundary-aware truncation
        sections = self._truncate_with_boundaries(sections)
        if self._budget.fits(sections):
            return sections

        # Phase 3: Summarize low-priority middle sections
        sections = await self._summarize_middle(sections)
        return sections

    def _prune_stale_outputs(self, sections: list[ContextSection]) -> list[ContextSection]:
        """Replace old tool outputs >200 chars with placeholder."""
        return [
            s.with_content("[output truncated]") if s.is_tool_output and len(s.content) > 200 and s.age > 2
            else s
            for s in sections
        ]

    def _truncate_with_boundaries(self, sections: list[ContextSection]) -> list[ContextSection]:
        """Truncate but never orphan a tool call from its result."""
        ...

    async def _summarize_middle(self, sections: list[ContextSection]) -> list[ContextSection]:
        """Use cheap LLM to summarize middle sections, preserving head and tail."""
        ...
```

#### 6.3.2 保护区配置

```python
@dataclass(frozen=True)
class CompressionConfig:
    protect_head_tokens: int = 4000    # system prompt + initial context
    protect_tail_tokens: int = 20000   # recent exchanges
    stale_output_threshold: int = 200  # chars before pruning
    summary_budget_ratio: float = 0.05 # summary uses 5% of total budget
```

### 6.4 预期收益

- Agent 多轮交互（如 PlannerJudge 修订循环、Judge 修复循环）不再因上下文溢出而截断关键信息
- 零成本清理阶段可立即回收 30-50% 空间（旧 diff 输出通常很长）
- 保护区机制确保 system prompt 和最近决策始终可见

---

## 7. P1: Agent 依赖注入与工厂模式

### 7.1 问题分析

当前 Orchestrator 在 `__init__` 中硬编码实例化 6 个 Agent：

```python
# 当前代码（概念性）
class Orchestrator:
    def __init__(self, config):
        self.planner = PlannerAgent(config.agents.planner)
        self.planner_judge = PlannerJudgeAgent(config.agents.planner_judge)
        self.executor = ExecutorAgent(config.agents.executor)
        ...
```

问题：
- 无法 mock 单个 Agent 进行测试
- 新增 Agent 必须修改 Orchestrator
- Agent 与 Orchestrator 紧耦合

### 7.2 Hermes 启发

Hermes 使用 **Registry + 懒加载** 模式：
- 工具通过 `registry.register()` 自注册
- 执行时按需加载，避免初始化未使用的子系统
- `check_fn` 前置检查确保依赖就绪

### 7.3 改造方案

#### 7.3.1 Agent 注册表

```python
# src/agents/registry.py
class AgentRegistry:
    _factories: dict[str, Callable[..., BaseAgent]] = {}

    @classmethod
    def register(cls, name: str, factory: Callable[..., BaseAgent]) -> None:
        cls._factories[name] = factory

    @classmethod
    def create(cls, name: str, config: AgentLLMConfig, **kwargs) -> BaseAgent:
        if name not in cls._factories:
            raise ValueError(f"Unknown agent: {name}")
        return cls._factories[name](config, **kwargs)

    @classmethod
    def create_all(cls, config: MergeConfig) -> dict[str, BaseAgent]:
        return {
            name: cls.create(name, getattr(config.agents, name))
            for name in cls._factories
        }
```

#### 7.3.2 Agent 自注册

```python
# src/agents/planner_agent.py
from src.agents.registry import AgentRegistry

class PlannerAgent(BaseAgent):
    ...

AgentRegistry.register("planner", PlannerAgent)
```

#### 7.3.3 Orchestrator 使用注册表

```python
# src/core/orchestrator.py
class Orchestrator:
    def __init__(self, config: MergeConfig, agents: dict[str, BaseAgent] | None = None):
        self._agents = agents or AgentRegistry.create_all(config)
```

### 7.4 预期收益

- 测试时可注入 mock Agent：`Orchestrator(config, agents={"planner": MockPlanner()})`
- 新增 Agent 只需新文件 + `AgentRegistry.register()`
- 为未来按需加载（懒加载）铺路

---

## 8. P2: 凭证池与 Provider 容错轮转

### 8.1 问题分析

当前系统每个 Agent 绑定单一 API key（通过 `api_key_env` 配置）。一旦该 key 触发限流或过期，整个 Phase 失败。

### 8.2 Hermes 启发

Hermes 实现了 **多源凭证池**：
- 从环境变量、OAuth、手动配置等多源解析凭证
- 凭证触发限流后进入 1 小时冷却期
- 自动轮转到下一可用凭证
- 跨进程同步防止竞争

### 8.3 改造方案

```python
# src/llm/credential_pool.py
@dataclass
class Credential:
    key: str
    source: str                          # env | config | oauth
    cooldown_until: datetime | None = None

class CredentialPool:
    def __init__(self, keys: list[Credential]):
        self._pool = keys

    def get_active(self) -> Credential:
        now = datetime.now(timezone.utc)
        for cred in self._pool:
            if cred.cooldown_until is None or cred.cooldown_until < now:
                return cred
        raise AllCredentialsCoolingDown(
            f"All {len(self._pool)} credentials in cooldown"
        )

    def cooldown(self, cred: Credential, seconds: int = 3600) -> None:
        cred.cooldown_until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
```

配置扩展：

```yaml
agents:
  planner:
    provider: anthropic
    model: claude-opus-4-6
    api_key_env:
      - ANTHROPIC_API_KEY        # primary
      - ANTHROPIC_API_KEY_2      # fallback
```

### 8.4 适用场景

- 生产环境多 key 轮转
- 团队共享 key 时避免互相限流
- 长时间运行的大型合并任务（10K+ 文件）

---

## 9. P2: 生命周期钩子系统

### 9.1 问题分析

当前系统的 Phase 执行中穿插了大量横切关注点（内存写入、检查点保存、日志记录、Gate 执行），全部硬编码在 Orchestrator 中。

### 9.2 Hermes 启发

Hermes 的 Hook 系统：
- 在关键生命周期点（`session:start`、`agent:step`、`command:*`）触发事件
- 错误隔离：单个 Hook 失败不阻塞管线
- 支持同步/异步处理器
- 通配符匹配

### 9.3 改造方案

```python
# src/core/hooks.py
class HookManager:
    _handlers: dict[str, list[Callable]] = defaultdict(list)

    def on(self, event: str, handler: Callable) -> None:
        self._handlers[event].append(handler)

    async def emit(self, event: str, **kwargs) -> None:
        for handler in self._handlers.get(event, []):
            try:
                result = handler(**kwargs)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.warning(f"Hook {event} failed: {e}")
                # Error isolated — pipeline continues
```

可注册的事件：

| 事件 | 触发时机 | 典型用途 |
|------|---------|---------|
| `phase:before` | Phase 开始前 | 日志、前置检查 |
| `phase:after` | Phase 完成后 | 检查点保存、内存写入 |
| `agent:call` | LLM 调用前 | Token 预算检查 |
| `agent:response` | LLM 返回后 | 追踪记录、成本计算 |
| `gate:run` | Gate 执行 | CI 通知 |
| `merge:complete` | 全流程结束 | 报告生成、通知 |

---

## 10. P2: 可观测性与洞察引擎

### 10.1 问题分析

当前 `TraceLogger` 只记录 LLM 调用的基础指标（模型、Token、延迟），缺乏：
- 成本计算
- 跨 Phase 的聚合分析
- 趋势识别
- 结构化日志

### 10.2 Hermes 启发

Hermes 的 `insights.py` 提供：
- Token 消耗模式分析
- 模型/平台分布统计
- 成本归因到 session 级别
- 工具采用率排名

### 10.3 改造方案

#### 10.3.1 成本追踪

```python
# src/tools/cost_tracker.py
@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

@dataclass(frozen=True)
class CostEntry:
    agent: str
    phase: str
    model: str
    usage: TokenUsage
    cost_usd: float
    timestamp: datetime

class CostTracker:
    def __init__(self, pricing: dict[str, PricingEntry]):
        self._pricing = pricing
        self._entries: list[CostEntry] = []

    def record(self, agent: str, phase: str, model: str, usage: TokenUsage) -> CostEntry:
        price = self._pricing.get(model)
        cost = self._calculate(usage, price)
        entry = CostEntry(agent=agent, phase=phase, model=model, usage=usage, cost_usd=cost, timestamp=datetime.now(timezone.utc))
        self._entries = [*self._entries, entry]
        return entry

    def summary(self) -> dict:
        return {
            "total_cost_usd": sum(e.cost_usd for e in self._entries),
            "by_agent": self._group_by("agent"),
            "by_phase": self._group_by("phase"),
            "total_tokens": {
                "input": sum(e.usage.input_tokens for e in self._entries),
                "output": sum(e.usage.output_tokens for e in self._entries),
            },
        }
```

#### 10.3.2 结构化日志

```python
# src/tools/structured_logger.py
import json, logging

class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "ts": record.created,
            "level": record.levelname,
            "module": record.module,
            "message": record.getMessage(),
            **(record.__dict__.get("extra", {})),
        }
        return json.dumps(log_data, ensure_ascii=False)
```

#### 10.3.3 运行报告增强

在最终报告中增加运行洞察 section：

```markdown
## Run Insights

| Metric | Value |
|--------|-------|
| Total LLM calls | 47 |
| Total cost | $2.34 |
| Most expensive agent | Planner ($0.89) |
| Cache hit rate | 62% |
| Average latency | 3.2s |
| Context utilization | 78% |
| Retry count | 3 (all rate limit) |
```

---

## 11. P3: Smart Model Routing

### 11.1 Hermes 启发

Hermes 的 `smart_model_routing.py` 自动将简单查询路由到廉价模型：
- 消息 <160 字符、<28 词、无代码 → 廉价模型
- 其他 → 主力模型

### 11.2 CodeMergeSystem 适用场景

在我们的场景中，可考虑：
- **Judge 的初步检查**（语法是否正确、TODO 是否存在）→ 用 Haiku 而非 Opus
- **报告生成**中的格式化模板填充 → 用 Haiku
- **ConflictAnalyst** 对 Category A/B（单侧变更）的简单确认 → 用 Haiku

### 11.3 改造方案

```python
# src/llm/model_router.py
def select_model(task_complexity: TaskComplexity, config: AgentLLMConfig) -> str:
    if task_complexity == TaskComplexity.TRIVIAL and config.cheap_model:
        return config.cheap_model
    return config.model
```

配置扩展：

```yaml
agents:
  judge:
    model: claude-opus-4-6
    cheap_model: claude-haiku-4-5    # for trivial checks
```

**优先级低**：需要先完成 P0/P1 的基础改造，且需要验证廉价模型在合并场景中的可靠性。

---

## 12. P3: 工具后端抽象层

### 12.1 Hermes 启发

Hermes 的 Web 工具可在 Exa、Firecrawl、Tavily 等后端间切换，不改代码。

### 12.2 CodeMergeSystem 适用场景

当前 16 个工具大多是直接实现，但有几个可以受益于后端抽象：

- `syntax_checker.py`：可以在内置检查、ruff、mypy、tree-sitter 间切换
- `gate_runner.py`：可以在本地执行、Docker 容器、CI 远程执行间切换
- `diff_parser.py`：可以在 Python difflib、git diff --stat、外部工具间切换

**优先级低**：当前单一实现工作良好，仅在有实际切换需求时再引入。

---

## 13. 实施路线图

### Phase A（1-2 周）— 基础架构

| 序号 | 任务 | 优先级 | 依赖 | 风险 |
|------|------|-------|------|------|
| A1 | Phase 基类 + PhaseContext 定义 | P0 | 无 | 低 |
| A2 | 将 6 个 Phase 从 Orchestrator 提取为独立类 | P0 | A1 | 中（大规模重构） |
| A3 | Orchestrator 瘦身为 Phase 调度器 | P0 | A2 | 中 |
| A4 | 错误分类器 + jittered backoff | P0 | 无 | 低 |
| A5 | BaseAgent 重试逻辑改造 | P0 | A4 | 低 |

### Phase B（1-2 周）— 成本与上下文优化

| 序号 | 任务 | 优先级 | 依赖 | 风险 |
|------|------|-------|------|------|
| B1 | Prompt 缓存（Anthropic cache_control） | P1 | 无 | 低 |
| B2 | 三阶段上下文压缩器 | P1 | 无 | 中（需验证摘要质量） |
| B3 | Agent 注册表 + 工厂模式 | P1 | A2 | 低 |
| B4 | AgentLLMConfig 扩展（cache_strategy 等） | P1 | B1 | 低 |

### Phase C（1-2 周）— 可扩展性与可观测性

| 序号 | 任务 | 优先级 | 依赖 | 风险 |
|------|------|-------|------|------|
| C1 | 生命周期钩子系统 | P2 | A2 | 低 |
| C2 | 凭证池 + 轮转 | P2 | A4 | 低 |
| C3 | 成本追踪器 | P2 | 无 | 低 |
| C4 | 结构化日志 | P2 | 无 | 低 |
| C5 | 运行报告增强 | P2 | C3 | 低 |

### Phase D（需要时）— 高级优化

| 序号 | 任务 | 优先级 | 依赖 | 风险 |
|------|------|-------|------|------|
| D1 | Smart Model Routing | P3 | B1 | 中（需验证准确性） |
| D2 | 工具后端抽象 | P3 | 无 | 低 |

### 实施依赖图

```
A1 ──► A2 ──► A3
              │
A4 ──► A5    ├──► B3
              │
B1 ──► B4   C1
B2
C2 (A4)
C3 ──► C5
C4
D1 (B1)
D2
```

---

## 附录：Hermes Agent 架构要点

### A. 核心架构特征

| 特征 | 实现方式 |
|------|---------|
| Agent 循环 | 轮次制对话循环：上下文准备 → prompt 构建 → LLM 调用 → 工具执行 → 内存同步 → 响应交付 |
| 工具系统 | 57+ 工具，Registry 自注册，async-first，后处理管线（标准化 → 安全检查 → LLM 摘要 → 裁剪） |
| 内存管理 | Provider 委托模式（1 内置 + 最多 1 插件），prefetch → sync 双向同步，错误隔离 |
| 上下文压缩 | 三阶段：零成本清理 → 边界保护 → 智能摘要，保护区机制（头 + 尾） |
| 错误恢复 | 7 类错误分类，去关联 jitter 退避，凭证轮转 + 冷却，上下文溢出自动压缩 |
| Prompt 缓存 | System_and_3 策略，~75% 输入 Token 节省 |
| 配置 | YAML + 环境变量展开，深度合并，版本迁移，托管模式检测 |
| 可观测性 | 洞察引擎（Token 模式、成本归因、工具采用率），分层限流追踪，调试会话 JSON |

### B. 值得关注但不适用于 CodeMergeSystem 的模式

| 模式 | 原因 |
|------|------|
| 多平台 Gateway | CodeMergeSystem 是 CLI 工具，不需要 Telegram/Discord/Slack 集成 |
| Skills 系统（26 域） | CodeMergeSystem 的 Agent 职责固定，不需要动态能力注入 |
| Session 管理 + 自动重置 | CodeMergeSystem 是单次运行流水线，不需要长会话管理 |
| 子目录上下文发现 | CodeMergeSystem 通过配置文件显式指定上下文 |
| PII 脱敏 | CodeMergeSystem 处理的是代码，不涉及用户个人信息 |

---

> **文档维护说明**：本方案应随实施进度更新，已完成项标记为 ~~删除线~~，新发现的改善点追加到对应优先级 section。
