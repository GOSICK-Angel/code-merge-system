# LLM 层（`src/llm/`）

> **版本**：2026-04-17
> 封装 LLM 调用的全部横切关注点：客户端、路由、凭据池、上下文预算、压缩、分块、缓存、错误分类、重试。上层 Agent 不应直接接触 anthropic/openai SDK。

---

## 1. 模块分工

| 文件 | 行数 | 职责 |
|---|---|---|
| `client.py` | 261 | `LLMClient` 抽象 + `LLMClientFactory`；Anthropic/OpenAI 具体实现；`with_model()` 上下文管理器 |
| `prompt_caching.py` | 95 | Anthropic Prompt Caching（system_only / system_and_recent） |
| `credential_pool.py` | 130 | 一个 Agent 多 Key 轮转（限流后换 Key） |
| `model_router.py` | 107 | D1 Smart Routing：按任务复杂度动态降档到 `cheap_model` |
| `error_classifier.py` | 248 | 8 类 `ErrorCategory` + 每类的默认策略（重试 / 熔断 / rate-limit 等） |
| `retry_utils.py` | 51 | `jittered_backoff()` 指数退避 |
| `context.py` | 191 | `TokenBudget` + `ContextPriority` + `ContextAssembler`（按优先级截断） |
| `context_compressor.py` | 285 | 保头保尾 + 中段摘要式压缩 |
| `chunker.py` | 569 | 大文件 AST / 行级分块 |
| `relevance.py` | 191 | 对 chunks 打相关性分，挑 top-k |
| `prompt_builders.py` | 147 | 通用 prompt 组装辅助（把 sections 拼成 final system/user message） |
| `response_parser.py` | 315 | 把 LLM 文本解析为 Pydantic 模型（tool_use / JSON mode / 容错抽取） |
| `prompts/` | — | 各 Agent 的专属 Prompt 模板 |

---

## 2. `LLMClient` 抽象

```python
class LLMClient(ABC):
    model: str

    async def complete(messages, system=None, **kw) -> str
    async def complete_structured(messages, schema, system=None) -> BaseModel

    def update_api_key(new_key: str)         # 供凭据池轮转调用
    def with_model(model: str)               # 临时换模型的 context manager
```

两个具体实现：`AnthropicClient`、`OpenAIClient`。通过 `LLMClientFactory.create(AgentLLMConfig)` 按 `provider` 字段分发。

---

## 3. Prompt Caching

仅 Anthropic 支持。三档策略（`AgentLLMConfig.cache_strategy`）：

| 策略 | 行为 |
|---|---|
| `none` | 不加 cache marker |
| `system_only` | 仅 system prompt 标 `cache_control: ephemeral` |
| `system_and_recent` | system + 最近一条 user message（默认，最经济） |

`apply_cache_markers()` 在 `complete()` 调用前自动注入 marker。

---

## 4. 凭据池（`credential_pool.py`）

当 `AgentLLMConfig.api_key_env: list[str]` 时启用。场景：一个公司有多个 API Key 分摊配额。

```yaml
agents:
  planner:
    api_key_env: [ANTHROPIC_API_KEY_1, ANTHROPIC_API_KEY_2]
```

限流（`RATE_LIMIT` 分类错误）时自动 `client.update_api_key(pool.next())` 轮转。单 Key 也不会创建池，零开销。

---

## 5. D1 Smart Routing

`AgentLLMConfig.cheap_model` 非空时生效。`model_router.select_model()` 依据：

- 任务类型（来自调用方提示）
- 上下文 token 数
- 历史决策复杂度

输出：返回 `cheap_model`（走 `with_model()` 临时切换）或继续用默认 `model`。典型用法是把 HumanInterface Agent 的"规整文本"任务永远走 Haiku。

---

## 6. 错误分类（`ErrorCategory`）

八类：

| 分类 | 典型来源 | 策略 |
|---|---|---|
| `RATE_LIMIT` | 429 | 等 `retry-after`，最多 5 次；配合凭据池轮转 |
| `TRANSIENT` | 5xx / network | `jittered_backoff` 重试 |
| `AUTH_TRANSIENT` | 401 一次性 | 重试 1 次 |
| `AUTH_PERMANENT` | 401 持续 | 累计 3 次熔断 |
| `QUOTA` | 余额/配额耗尽 | 立刻熔断 |
| `BAD_INPUT` | 400 prompt 过大/参数错 | 不重试，提示上层改 prompt |
| `FORMAT` | 响应无法被 Pydantic 解析 | 累计 3 次熔断 |
| `UNKNOWN` | 兜底 | 重试 1 次 |

熔断类错误命中 `CIRCUIT_BREAKER_THRESHOLD=3` 即 raise `CircuitBreakerOpen`。

---

## 7. 上下文预算（`context.py`）

```python
class TokenBudget(BaseModel, frozen=True):
    model: str
    context_window: int           # get_context_window(model)
    reserved_for_output: int      # max_tokens
    used: int = 0

    available = window - reserved - used - 5% 安全余量
```

`MODEL_CONTEXT_WINDOWS` 表内置常见模型（Claude 系列 200K，GPT-4o 128K，o3/o4-mini 200K，gpt-4.1/gpt-5.4 1M）。未登记模型默认 128K。

### ContextAssembler

```python
class ContextSection:
    name, content, priority, min_tokens
    can_truncate: bool
    truncation_strategy: Literal["tail", "head", "middle"]

class ContextPriority(IntEnum):
    CRITICAL = 0     # 永远不截
    HIGH = 1
    MEDIUM = 2
    LOW = 3
    OPTIONAL = 4
```

超出预算时按优先级倒序截断；`can_truncate=False` 的 section 触发 `drop` 而非 `truncate`；最低保留 `min_tokens`。

---

## 8. 压缩（`context_compressor.py`）

策略："保头 + 保尾 + 中段 summary"。参数来自每 Agent 的 `CompressionConfig`：

```python
protect_head_tokens: 4000    # 保留开头 N tokens
protect_tail_tokens: 20000   # 保留末尾 N tokens
stale_output_threshold: 200  # 过期输出阈值
summary_budget_ratio: 0.05   # 中段摘要占预算比例
```

压缩只在 context 超过预算时触发；未超预算时原样透传。

---

## 9. 分块 + 相关性（`chunker.py` + `relevance.py`）

大文件（Plan/Judge 处理的 diff）先走 chunker：
- 按 AST 边界（函数/类）切块
- 无 AST 支持的语言退到"按空行 + 大小上限"
- 每个 chunk 过 `relevance.score(chunk, query)` 打分
- `top_k` chunks 进入最终 prompt

---

## 10. 响应解析（`response_parser.py`）

两种模式：
1. **Native structured output**：Anthropic tool_use / OpenAI json_mode
2. **Fallback**：从文本中抽 ` ```json ... ``` `，再 `pydantic.model_validate_json()`

解析失败抛 `ParseError`，归类为 `FORMAT` 错误 → 熔断计数。

---

## 11. `prompts/` 目录

每个 Agent 一个模块，导出若干函数返回字符串（system + user message）：

| 文件 | 服务 Agent |
|---|---|
| `planner_prompts.py` | Planner |
| `planner_judge_prompts.py` | PlannerJudge |
| `analyst_prompts.py` | ConflictAnalyst |
| `executor_prompts.py` | Executor |
| `judge_prompts.py` | Judge |
| `risk_scoring_prompts.py` | LLM 风险打分（`llm_risk_scoring.enabled=true` 时） |

Prompt 中使用 Jinja2 风格的占位符，由对应 Agent 的 `_build_prompt()` 方法填充。

---

## 12. 添加新 LLM Provider 的清单

1. 在 `client.py` 新增 `class FooClient(LLMClient)`，实现 `complete()` / `complete_structured()` / `update_api_key()`
2. 在 `LLMClientFactory.create()` 加入 provider 分支
3. 在 `AgentLLMConfig.provider: Literal[...]` 加入新字面量
4. 在 `error_classifier.py` 补全该 provider 的错误映射
5. 如果要支持 Prompt Caching，扩展 `prompt_caching.py`
6. 在 `context.py::MODEL_CONTEXT_WINDOWS` 登记该 provider 的模型
