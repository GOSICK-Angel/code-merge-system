# 多 Agent 实战分享：从 CodeMergeSystem 看 Agent 系统的工程化

> **受众**：已写过一两个 agent 原型，正在把它推向"可以跑在生产上的真事儿"的开发者。
> **前置知识**：熟悉 LLM 基础 API、async、状态机、Pydantic。不需要了解本项目业务。
> **一句话定位**：本文不是"又一个 LangGraph vs LangChain"讨论，而是一份**真实产品**里 agent 工程化的决策集锦——哪些抽象必须建、哪些不必建、哪些开源项目值得抄、哪些坑只有在真流量下才会炸。

---

## 目录

1. [为什么是多 agent：从单 agent 的天花板说起](#1-为什么是多-agent)
2. [系统地图：6 个 Agent × 8 个 Phase × 3 层记忆](#2-系统地图)
3. [六个值得抄的工程抽象](#3-六个值得抄的工程抽象)
   - 3.1 Agent Contract：岗位说明书作为代码
   - 3.2 ReadOnlyStateView：审查/执行的写权限分离
   - 3.3 Snapshot-before-write + Checkpoint：对不可逆操作的防御
   - 3.4 Coordinator + Meta-Review：多轮死循环的解套机制
   - 3.5 三层记忆 + MemoryExtractor：上下文成本/质量的 tradeoff
   - 3.6 Prompt Gate Registry：Prompt 作为可审计资产
4. [LLM 层的工程化：不是包一层 client 就完事](#4-llm-层的工程化)
5. [Tools 脊柱：LLM 负责"理解"，Tools 负责"证伪"](#5-tools-脊柱)
6. [生产上真正踩过的坑（本月修复录）](#6-生产上真正踩过的坑)
7. [开源对标地图：他们解决了哪一块](#7-开源对标地图)
8. [给 Agent 开发者的决策 checklist](#8-给-agent-开发者的决策-checklist)

---

## 1. 为什么是多 Agent

CodeMergeSystem 的目标是把「长期分叉的 fork 如何安全合并 upstream」自动化：fork 做了大量私有改动、upstream 跨版本迭代、直接 `git merge` 会产出几百上千冲突、还会静默吞掉 fork 独有的功能。

第一版是一个**单 agent + system prompt 500 行 + 一堆 if-else**，跑一轮就暴露了三个死穴：

1. **审查偏差**。生成 patch 的同一个模型再来审查自己的 patch，会系统性放过自己的错误——这是跟人 code review 同样的"作者盲区"。
2. **Prompt 膨胀**。同一个 agent 既要读诊断数据又要产决策又要写报告，上下文轻松 50K+，每一轮成本不可接受。
3. **无法回滚**。LLM 输出不靠谱，但写文件是不可逆操作；单 agent 架构里"写"和"审"混在一个 run 里，出错了只能整段重跑。

多 agent 不是"把一个 prompt 拆成多个 prompt"，它真正解决的是：

| 单 agent 痛点 | 多 agent 的工程回答 |
|---|---|
| 审查偏差 | **Reviewer-Executor Provider 隔离**：Judge 用 Claude，Executor 用 GPT-4o，故意不同家模型 |
| Prompt 膨胀 | 每个 agent 只吃**自己 contract 声明的字段**，其余一律拒读 |
| 不可回滚 | 审查 agent 拿 `ReadOnlyStateView`，**写权限集中在唯一 Executor 上**，且 Executor 写入前必须快照 |
| 能力不均衡 | 每个 agent 用独立的 `AgentLLMConfig`（provider / model / key env / cheap_model），按任务成本独立调参 |

这份表里每一行在第 3 章都会展开成一个可抄的工程模式。

---

## 2. 系统地图

### 2.1 架构分层（10 秒版）

```
CLI / Web UI
    ↓
Orchestrator（~400 LOC 纯分派器） + Coordinator（异常回环路由）
    ↓
Phases（8 类）  ←  Agents（6 主 + 2 辅）  ←  LLM Layer  ←  Memory（3 层 SQLite）
    ↓                ↓                        ↓
                  Tools（Git / Diff / Gate / 六大加固扫描）
                  ↓
                  原子写入 + Checkpoint
```

权威文档：[`doc/architecture.md`](../architecture.md)、[`doc/modules/core.md`](../modules/core.md)、[`doc/modules/agents.md`](../modules/agents.md)。

### 2.2 Agent 花名册

| Agent | 角色 | 写权限 | 典型用量 |
|---|---|---|---|
| **Planner** | 把 diff + 六大扫描结果转成 `MergePlan`（按层批次） | ❌ | Opus，1~N 轮修订 |
| **PlannerJudge** | 审 MergePlan，允许回环 REVISE | ❌ 只读 | GPT-4o |
| **ConflictAnalyst** | 高风险文件 3-way diff 语义诊断 | ❌ | Sonnet |
| **Executor** | **唯一**写文件的 agent；失败自动回滚 | ✅ | GPT-4o |
| **Judge** | 确定性流水线（VETO 不可协商）+ LLM 审查 | ❌ 只读 | Opus |
| **HumanInterface** | 生成 YAML+Markdown 决策模板；永不填默认值 | ❌ | Haiku |
| **SmokeTest** | Judge 通过后跑冒烟（shell / http / playwright） | ❌ | 无 LLM |
| **MemoryExtractor** | 在高信息量事件上追加一次 LLM 提炼，补确定性规则盲区 | ❌ | Haiku |

注意这里的**不对称**：写的只有一个、审的两个、诊断的两个、人机协作一个。不是"每个子任务一个 agent"，而是按**权限边界 + 认知偏差隔离**切分的。

### 2.3 状态机驱动的 Phase 循环

```
INITIALIZED → PLANNING → PLAN_REVIEWING ⇄ PLAN_REVISING
           → AUTO_MERGING ⇄ PLAN_DISPUTE_PENDING
           → ANALYZING_CONFLICTS → AWAITING_HUMAN
           → JUDGE_REVIEWING ⇄ AUTO_MERGING / ANALYZING_CONFLICTS
           → GENERATING_REPORT → COMPLETED
```

有几个设计决定值得单独点出来：

- **Orchestrator 不含业务**。它就是一张 `status → Phase 类` 的字典（~400 LOC），从 [`src/core/orchestrator.py`](../../src/core/orchestrator.py) 可以直接读出来。业务全在 `Phase.execute()` 里；这让新增阶段只需 3 步：新 Phase 类、扩 `VALID_TRANSITIONS`、登记 `PHASE_MAP`。
- **回环状态不建独立 Phase**。`PLAN_REVISING` / `PLAN_DISPUTE_PENDING` 都是父 Phase 内部的中间状态，不进 `PHASE_MAP`——这避免了状态机图论上爆炸。
- **PhaseOutcome 是不可变值对象**。每个 Phase 只决定「下一个 `target_status` + `checkpoint_tag` + `should_update_memory` + `paused?`」，所有副作用由 Orchestrator 统一施加。这对测试 phase 是**巨大**的解放——你给它一个 `MergeState` + mock 的 `PhaseContext`，拿回 outcome 直接断言，不用起半个系统。

对 agent 开发者的直接启发：

> **把"调度"和"执行"分开**。很多框架（比如早期 AutoGen）把两件事混在同一层，导致想测单个 agent 时必须起一个 runner。用"纯分派器 + 值对象 outcome"的模式，你单测一个 phase 只需要构造 state 和 context。

---

## 3. 六个值得抄的工程抽象

### 3.1 Agent Contract：岗位说明书作为代码

**问题**：LLM agent 最容易悄悄跑偏的两件事是 (a) 读了本不该读的 state 字段（耦合爆炸），(b) 偷偷改了 state（审查 agent 变成了隐性执行）。写在 docstring 里没用——下次改 prompt 的人根本不会看。

**解法**：把 agent 的**输入白名单、输出 schema、允许调用的 prompt、绝对禁止的行为**声明成 YAML，再加两层守护。

```yaml
# src/agents/contracts/judge.yaml
name: judge
inputs:                        # 只能读这些 MergeState 字段
  - config
  - file_decision_records
  - judge_verdicts_log
  - shadow_conflicts
  - interface_changes
  ...
output_schema: JudgeVerdict
gates:                         # 只能调这些 prompt gate
  - J-SYSTEM
  - J-FILE-REVIEW
  - META-JUDGE-REVIEW
forbidden:
  - writes_state               # 绝对不能 state.x = y
  - direct_llm_call            # 绝对不能绕过 _call_llm_with_retry
collaboration: review_only     # review_only / compute / propose_then_confirm
```

两层守护：

1. **运行时**：`BaseAgent.restricted_view(state)` 返回一个 `ReadOnlyStateView`，访问不在 `inputs` 里的属性直接抛 `FieldNotInContract`。你想 `view.some_private_field` 都取不到。
2. **静态**：[`tests/unit/test_agent_contracts.py`](../../tests/unit/test_agent_contracts.py) 用 AST 扫每个 agent 文件，出现 `state.<field> = ...` 就挂 CI，出现 `self.llm.complete(` 也挂 CI。

**为什么值得抄**：

- **Contract 是一份单点事实**。review 时看 diff 之前先看 contract 有没有变——如果 contract 没变只是改实现，审查焦点立刻收窄到"新代码是不是越权了"。
- **对 LLM 输出的安全边界**。即使 LLM 被 prompt injection 骗了让它写 state，`ReadOnlyStateView` 在 Python 层就抛了，不可能绕过。
- **"强制最小权限"不是空话**。同一个 BaseAgent 基类，给 Executor 传 `state`，给 Judge 传 `restricted_view(state)`，**能写的 agent 和能读什么 state 的 agent 物理隔离**。

**抄作业代价**：一个 YAML 读取器（<100 LOC）+ 一个 `ReadOnlyStateView` 代理（<50 LOC）+ 一个 AST 扫描测试（~150 LOC）。见 [`src/core/read_only_state_view.py`](../../src/core/read_only_state_view.py)、[`src/agents/contracts/_schema.md`](../../src/agents/contracts/_schema.md)。

---

### 3.2 ReadOnlyStateView：审查/执行的写权限分离

接上一节，这里单独强调**为什么写权限集中**。

合并系统里"写"一次 = 一次 `os.write` = 一次不可逆副作用。常见框架的做法是"每个 agent 都能调 `write_file` 工具，由 orchestrator 约束顺序"。这在原型阶段没问题，但出 bug 时你**无法追责**——是 Planner 的 plan 错了还是 Executor 写错了还是 Judge 放过了？

本项目的解法是把"写"从 agent 权限模型里整个抽走：

```python
# 唯一写入通道（src/tools/patch_applier.py）
def apply_with_snapshot(path: Path, new_content: str) -> PatchApplyResult:
    snapshot = path.read_text() if path.exists() else None
    try:
        atomic_write(path, new_content)
        return PatchApplyResult(ok=True, snapshot=snapshot)
    except Exception as e:
        if snapshot is not None:
            atomic_write(path, snapshot)      # 立即回滚
        raise
```

- **只有 ExecutorAgent 持有对这个函数的调用权**——其他 agent 的 contract 里不允许声明。
- Judge 提出修复建议后，必须走"回到 JUDGE_REVIEWING → target_status=AUTO_MERGING"的状态机回环，让 Executor 再去写。Judge 自己不能写。

这个模式对 agent 开发者的启发是：

> **不要给所有 agent 都挂一个 `file_write_tool`**。在你的系统里找出"不可逆操作的最小集合"，让它们只有一个调用入口，其他 agent 只能通过**状态机转移**间接驱动这个入口。事故归因时，你永远只需要看 Executor 的日志。

---

### 3.3 Snapshot-before-write + Checkpoint：不可逆操作的防御

Agent 系统最恐怖的 bug 不是 crash，是**错了但没 crash 还跑完了**。本项目在两个粒度做防御：

**文件粒度**：`apply_with_snapshot()` 如上。关键是**先算 snapshot 再写**，异常时立刻 rollback；write 前后都记 `is_rolled_back` 布尔进 state，后续 Judge 能看到"这个文件写失败过"。

**Run 粒度**：Checkpoint。

| 不变量 | 如何保证 |
|---|---|
| 原子写入 | 先写 `checkpoint.json.tmp` 再 rename（POSIX 原子） |
| 单文件滚动 | 生产模式永远只留 `checkpoint.json`；`debug_checkpoints=true` 才额外落 tagged 快照 |
| Schema 不匹配即失败 | `MergeState.model_validate()` 失败直接抛 RuntimeError，**拒绝**静默恢复半损坏 state |
| 中断保护 | 注册 SIGINT/SIGTERM，打 `interrupt` 标签后 SystemExit(0) |

**一个反直觉的决策**：schema 不匹配时**不尝试部分恢复**。很多系统会"尽力加载已知字段"，但 agent 系统里半损坏 state 会导致 LLM 得到半真半假的事实，做出比"从头重跑"更坏的决策。在本系统里宁愿让用户 resume 失败、重新扫一次 diff，也不让 LLM 吃到陈腐 state。

对 agent 开发者：

> **"尽力恢复"在传统 CRUD 系统里是美德，在 LLM 系统里是 bug**。LLM 会把半损坏的 state 当成完整事实展开推理。要么完整恢复，要么明确失败，**没有中间态**。

相关代码：[`src/core/checkpoint.py`](../../src/core/checkpoint.py)、[`src/tools/patch_applier.py`](../../src/tools/patch_applier.py)。

---

### 3.4 Coordinator + Meta-Review：多轮死循环的解套机制

多 agent 系统最容易发生的生产事故是**死循环**：Planner 出计划→PlannerJudge REVISE→Planner 改→PlannerJudge 再 REVISE→无限往复，烧光预算。

幼稚解法：加一个 `max_rounds=5` 硬上限。但到第 5 轮强制前进会把半烂的计划喂给 Executor，事故更大。

本项目的解法：**引入 Coordinator，在死循环快发生时让参与方"换个大脑"回看问题**。

```
轮次 1、2：Planner 和 PlannerJudge 正常你来我往
轮次 3（达到 dispute_meta_review_threshold）：
    Coordinator 检测到同一类 issue 反复出现
    → 调 Planner.meta_review()，换一套 META-PLAN-* prompt
    → 输出 "assessment + strategic recommendation"
    → 写入 state.coordinator_directives（不直接改 plan）
轮次 4：Planner 带着 directive 重新出计划
轮次 5（max_plan_revision_rounds=5）：
    仍未收敛 → 转 AWAITING_HUMAN，同时把 coordinator_directives
    作为上下文展示给人工审阅者
```

几个要点对 agent 开发者有用：

1. **Meta-review 不是"再 review 一次"**，用的 prompt 完全不同。常规 review 问"这个计划对不对"，meta-review 问"为什么我们卡在这里"。这在 agent 开发里叫"**把视角拉高一层**"。
2. **Coordinator 自己不持有 LLM**。它是纯 Python 逻辑（路由规则、批次大小计算），[`src/core/coordinator.py`](../../src/core/coordinator.py) ~200 LOC。决定"要不要 meta-review"的是规则，真正做 meta-review 的是原 agent 换个 prompt。
3. **人工是最终出口**。死循环的最终解不是"更聪明的 LLM"，而是"把问题结构化地交给人"。HumanInterface 的 contract 里明确禁止填默认值——人没明确决策的条目永远停在 `ESCALATE_HUMAN`。
4. **threshold 是配置项**。`config.coordinator.judge_meta_review_threshold`、`dispute_meta_review_threshold` 默认 2，用户可调。这让"多努力几轮"和"早早交人"成为项目级决策而非代码写死。

**抄作业模板**：

```python
# 伪代码：任何多 agent review 回环都可以套这个模式
async def review_loop(max_rounds, meta_threshold):
    for round_i in range(max_rounds):
        verdict = await reviewer.review(subject)
        if verdict.approved:
            return subject
        if round_i + 1 == meta_threshold:
            # 换个视角
            directive = await subject_author.meta_review(state)
            state.coordinator_directives.append(directive)
        subject = await subject_author.revise(subject, verdict.issues)
    # 到这还没收敛 → 交人
    return await escalate_to_human(state, reason=MAX_ROUNDS)
```

---

### 3.5 三层记忆 + MemoryExtractor：上下文成本/质量的 tradeoff

记忆系统是最容易过度工程化的部分。本项目的设计原则是 **"先把规则榨干再上 LLM"**。

**三层注入结构**（`LayeredMemoryLoader`）：

```
L0  Project Profile   # 全局：主语言/框架/团队规范
L1  Phase Essentials  # 当前 Phase patterns + 上一 Phase key decisions
L2  File-Relevant     # 按文件路径 + confidence 加权 top-8 MemoryEntry
```

**L2 的打分函数**是一句话：`score = path_score * 0.5 + confidence * 0.5`。简单，但 `confidence_level` 有层级（`extracted` / `inferred` / `heuristic`），**低置信度的历史猜测不会占高置信度证据的位置**——这在 token 有限时比复杂 embedding 检索更可控。

**去重靠 content_hash**：`sha256(entry_type:phase:content)[:16]` 作为主键，重复语义条目只存一次。这让"Judge 每轮都记录同一个问题"不会线性涨记忆库。

**超限靠合并**：`MAX_ENTRIES=500`，到 `CONSOLIDATION_THRESHOLD=300` 先按 `(phase, entry_type, primary_tag)` 分组合并组内 ≥3 条，合并后置信度 +0.05。这模仿了人脑"多次观察的结论更可信"的直觉。

**MemoryExtractorAgent**（有意思的权衡在这里）：

早期版本只用确定性规则从 state 里提取 `MemoryEntry`（"auto_merge phase 有 12 个 C 类文件"），这类提取**便宜但只能说 What，不能说 Why**。

生产上发现真正有价值的记忆是"Judge 第 3 轮因为同一个 shadow_conflict 还在 REPAIR，说明这类文件的批次粒度需要缩小"——这种因果洞察用规则写不出来，只能 LLM 提。

但不能每个 Phase 都调 LLM 提——贵。于是：

```python
# Orchestrator._update_memory()（简化）
def _should_llm_extract(phase, state) -> bool:
    if not config.memory.llm_extraction:      # 全局开关，默认关闭
        return False
    if state.errors:                           # 任何 phase 都提
        return True
    if phase == "planning" and state.plan_disputes:
        return True
    if phase == "judge_review" and state.judge_repair_rounds >= 2:
        return True
    return False
```

- 只在"发生过异常信号"的时候调 LLM
- 默认用 Haiku（便宜 10 倍）
- `max_insights_per_phase=5` 硬上限
- 输出仍是标准 `MemoryEntry`，走 content_hash 去重

对 agent 开发者：

> **不要给所有事件都配 LLM**。先用规则提取结构化信号，只在"规则认为值得提"的地方追加一次 LLM。把 LLM 放在规则的 supplement 位置，而不是 driver 位置，成本会降一个数量级，质量反而更稳。

这个思路直接受 [MemPalace](../references/mempalace-analysis.md) 的四层记忆栈和 [Graphify](../references/graphify-analysis.md) 的"先图谱后 LLM"启发，具体细节看 [`doc/modules/memory.md`](../modules/memory.md)。

---

### 3.6 Prompt Gate Registry：Prompt 作为可审计资产

很多项目的 prompt 是 **literal string 散落在 agent 代码里**，谁改的、什么时候改的、谁在用、要不要热补丁全是谜。一到生产就只能靠 git blame。

本项目强制走**注册表模式**：

```python
# src/llm/prompts/gate_registry.py
register_gate(
    id="J-FILE-REVIEW",
    builder=build_judge_file_review_prompt,
    description="Judge 审查单个文件合并结果的 prompt",
)

# Judge agent 里
prompt = get_gate("J-FILE-REVIEW").render(file_decision, context)
```

每个 agent 的 contract 里声明它**允许调的 gate ID 白名单**，`test_agent_contracts.py` 校验 agent 代码里所有 `get_gate("...")` 调用都在白名单里。

好处很具体：

1. **改 prompt 必须走 registry**。禁止在 agent 里 literal。CI 扫描不到 import prompt builder 的直接调用。
2. **版本化与 A/B**。注册时可以带 variant，`cache_strategy` 也在 gate 层面配置。
3. **审计面清晰**。想知道"Judge 现在用哪些 prompt"：看 `judge.yaml` 的 gates 列表 + registry 里这些 ID 的 builder，两份文件搞定。
4. **Trace 可回放**。`TraceLogger` 记录每次 LLM 调用的 gate ID + rendered prompt + response，JSONL 一行一条，事故复盘不用猜。

前缀分派约定：`P-*` Planner、`PJ-*` PlannerJudge、`CA-*` ConflictAnalyst、`E-*` Executor、`J-*` Judge、`M-*` MemoryExtractor、`META-*` meta-review。看到前缀就知道哪个 agent 调的。

---

## 4. LLM 层的工程化

"Agent 不就是 LLM + 循环吗"——这句话成立的前提是你接受 5% 的成功率。剩下 95% 来自下面这层工程。

### 4.1 错误分类器：不是所有 500 都该重试

[`src/llm/error_classifier.py`](../../src/llm/error_classifier.py) 把 provider 返回的错误分成 **8 类 `ErrorCategory`**（摘要）：

| 类别 | 策略 |
|---|---|
| `RATE_LIMIT` | 退避 + 最多等 `MAX_RATE_LIMIT_WAITS=5` 轮 |
| `AUTH_TRANSIENT` | 立刻重试，可能是瞬时 token 过期 |
| `AUTH_PERMANENT` | **累计 ≥ 3 触发熔断**，整个 agent 停工 |
| `FORMAT` | LLM 返回结构错误，重试同时在 prompt 里追加 error trace |
| `CONTEXT_LENGTH` | 触发上下文压缩再重试 |
| `SERVER_ERROR` | 指数退避 |
| `USER_ABORT` | 不重试，SystemExit |
| `UNKNOWN` | 保守退避 + 到 3 次就上报 |

关键工程决定：

- **熔断器不是整个系统级，是 per-agent**。一个 Planner 熔断不会让 Judge 跟着挂。
- **`FORMAT` 类错误的 prompt 回灌**。LLM 返回 JSON 解析失败时，下一轮 prompt 会带上"你上一次返回了 X，解析失败因为 Y"。这个小技巧让 GPT-4o 的 JSON 成功率从 ~92% 飙到 ~99%。
- **拒绝 infinite retry**。任何一类错误都有明确 cap，到 cap 就 raise `AgentExhaustedError(last_classification)`，由 Orchestrator 决定转 FAILED 还是 AWAITING_HUMAN。

### 4.2 凭据池 + Provider 隔离

每个 agent 的 `AgentLLMConfig.api_key_env` 可以**声明为列表**：

```yaml
agents:
  planner:
    provider: anthropic
    model: claude-opus-4-7
    api_key_env: [ANTHROPIC_API_KEY, ANTHROPIC_API_KEY_BACKUP]
  judge:
    provider: anthropic                      # 同家但不同 key 池
    model: claude-opus-4-7
    api_key_env: [ANTHROPIC_API_KEY_REVIEW]
  executor:
    provider: openai                         # 换家，反共谋
    model: gpt-4o
    api_key_env: [OPENAI_API_KEY]
```

`CredentialPool` 在 rate-limit 时轮转到下一把 key，到尾端回到队首并等待。这避免了"一个用户多 agent 并发把单 key 打到 429"的 DoS 现象。

**刻意的 Provider 隔离**：Executor 用 OpenAI、Judge 用 Anthropic。不是因为 Anthropic 更会审查，而是**避免同家模型共谋偏差**。这个决策在 `doc/architecture.md` 明确写进 P5 原则。

### 4.3 上下文预算 + 压缩 + Prompt Caching

三件套：

- **TokenBudget**：按优先级分段组装。段 1（system）必须保底；段 2（contract inputs）按 priority 截断；段 3（memory L0/L1/L2）最先被挤出去。
- **ContextCompressor**：保头保尾 + 中段摘要。每个 agent 可调压缩比例。中段摘要本身是便宜模型（Haiku）一次调用。
- **Prompt Caching**：Anthropic 专属。`cache_strategy` 三档：off / system_only / system_and_history。system_and_history 在多轮 review 场景可省 ~75% 输入 token（对齐 [hermes-inspired-improvements](../references/hermes-inspired-improvements.md) §5）。

### 4.4 Smart Model Routing（不要把所有任务都丢给 Opus）

`model_router.select_model(task_kind, estimated_complexity)`：每个 agent 可以声明一个 `cheap_model`，对"简单任务"（如 `confidence_level=extracted` 的单文件决策）自动降档。实测 Planner 降档 ~30% 任务，成本降 ~20%，质量无明显下降。

对 agent 开发者：

> 不要追求"一个大模型统治所有任务"。每个 agent 配 primary + cheap 两档，让简单的归简单、复杂的归复杂。这是 agent 工程 ROI 最高的改动之一。

---

## 5. Tools 脊柱

这是本项目**最违反"LLM-first"直觉**但收益最大的设计：

> LLM 只负责"理解语义"，**Tools 负责"证伪"**。所有 LLM 的输出都要过一遍确定性工具检查，工具否决 LLM 不可商量。

对应到 Judge 的代码是两段式：

```python
async def review(self, state):
    # 第一段：确定性流水线，任一 VETO 直接 NEEDS_REPAIR，不问 LLM
    for veto_check in [
        self.verify_customizations,     # grep_count_baseline / line_retention
        self.gate_baseline_diff,        # failed_ids 差集
        self.shadow_recheck,            # shadow_conflicts 已解决？
        self.sentinel_rescan,           # 业务哨兵仍在？
        self.config_retention,          # 配置行保留率
        self.cross_layer_assertion,     # 多层键一致性
    ]:
        issues = await veto_check(state)
        if issues:
            return JudgeVerdict(verdict=NEEDS_REPAIR, issues=issues)

    # 第二段：LLM 审查（仅对未 VETO 的文件做语义检查）
    return await self._llm_review(state)
```

六大加固扫描器（详见 [`doc/multi-agent-optimization-from-merge-experience.md`](../multi-agent-optimization-from-merge-experience.md)）对应 fork 合并的 6 种典型丢失模式：

| 模式 | 工具 | 对应的 agent 盲区 |
|---|---|---|
| M1 定制被整文件覆盖 | `scar_list_builder` | LLM 不知道历史上这里被 restore 过 |
| M2 同名不同扩展 shadow 冲突 | `shadow_conflict_detector` | `a.ts` + `a.tsx` 并存 LLM 认不出 |
| M3 接口变更未同步调用方 | `interface_change_extractor` + `reverse_impact_scanner` | LLM 只看 diff 不会反向 grep |
| M4 顶层调用被替换 | `three_way_diff` AST 级 | LLM 看不出 `register_route(...)` 整批丢失 |
| M5 配置行被覆盖 | `config_line_retention_checker` | YAML/Dockerfile 的"必须保留行"LLM 不知道 |
| M6 类型/API 契约回归 | `gate_runner` + baseline parsers | LLM 不会主动运行 `tsc` |

对 agent 开发者的启发：

> **找出你领域里的"工具能检测、LLM 会漏"的模式清单**。每一条都写成确定性 checker，作为 VETO。LLM 的输出必须通过所有 checker 才能落地。这比"再加一个审查 LLM"稳定 10 倍、便宜 100 倍。

另一个小设计：`gate_runner` 的 **baseline parser 用 entry_points 可插拔**（[`src/tools/baseline_parsers/`](../../src/tools/baseline_parsers/)）。Python 项目用 `pytest_summary` + `mypy_json`，Go 项目加 `go_test_json`，Rust 项目加 `cargo_test_json`，都不用动系统源码。这是经典的"**代码零业务知识**"原则落地。

---

## 6. 生产上真正踩过的坑

这一节是"过去几个月真实修的 bug"，对刚上生产的 agent 系统尤其值得预警。

### 6.1 ThinkingBlock 解析崩溃

**症状**：启用 Anthropic extended thinking 后，偶发性 `AttributeError` 在 Judge 第 3 轮修复后炸。

**根因**：Anthropic 返回的 content blocks 里 `ThinkingBlock` 没有 `.text` 属性，但代码假设每个 block 都是 `TextBlock`。

**修复**：[2026-04-17 commit `762ed40`](../../src/llm/client.py) 改为按 block type 分派：

```python
for block in response.content:
    if block.type == "text":
        buf.append(block.text)
    elif block.type == "thinking":
        continue   # 不要拼进最终输出
    else:
        logger.warning("unknown block type: %s", block.type)
```

**对 agent 开发者的教训**：provider 的 content 返回结构**不是稳定的扁平字符串**。对新特性（thinking、citations、tool_use、server_side_tool）要显式处理 block type，不要 `"".join(b.text for b in content)`。

### 6.2 UTF-8 代理对让 JSON 解析挂

**症状**：`json.JSONDecodeError: unpaired surrogate`，LLM 返回的某些 emoji / 东亚字符触发。

**根因**：OpenAI tool-use 路径的 streamed token 偶尔会在 surrogate pair 中间切开，拼起来后是非法 UTF-8。

**修复**：在 `_call_llm_with_retry` 出口统一 `content.encode("utf-16", "surrogatepass").decode("utf-16")` 往返清洗。同 commit 762ed40。

**教训**：任何"LLM 输出 → JSON 解析"的路径都要有编码兜底。流式 API 尤其。

### 6.3 Judge 死循环（max_rounds 的边界条件）

**症状**：某次合并 Judge 进入 NEEDS_REPAIR → AUTO_MERGING → JUDGE_REVIEWING 循环 8 轮，烧光了 $12 才被日志报警注意到。

**根因**：`max_judge_repair_rounds` 判断用了 `>`，应该 `>=`。第 5 轮判 `5 > 5 == False` 没拦住，跑到第 6 轮。

**修复**：同 762ed40，统一用 `>=`，并加单测覆盖边界。

**教训**：**配了上限的地方全部加 unit test 覆盖边界**。LLM 系统跑多一轮的成本远高于传统系统，off-by-one 会直接变钱。

### 6.4 Cherry-pick 策略阶梯

**症状**：早期 `commit_replayer` 对所有 "全 Category B 或 D_MISSING 文件的 upstream commit" 无差别 cherry-pick，碰到 commit 里夹杂一个 binary 文件就整段失败，退化成硬合。

**修复**：P1 批次 3（commit `31b8c0e`）改为**策略阶梯**：
1. 尝试 `cherry-pick -n`（no-commit），失败退到第 2 档
2. 仅对 text-only 文件 cherry-pick，剩余让 Executor 处理
3. 全 fallback：不 cherry-pick，整个 commit 走 Executor

**教训**：agent 系统里的"快速通道"要准备好**降级路径**。任何"我要 bypass LLM 省钱"的优化，都要能优雅回落到 LLM 路径，不然 edge case 会打脸。

### 6.5 Provider 兜底

**症状**：OpenAI 单 key 限流打穿后，Executor 整个 phase 失败，用户得手动 resume。

**修复**：`AgentLLMConfig.api_key_env` 改为 list 支持凭据池（§4.2），同时给每个 agent 可选的 `fallback_provider` —— 主 provider 全部 key 限流后切到备用家。Executor 默认配了 `fallback_provider: anthropic` + `fallback_model: claude-sonnet-4-6`。

**教训**：**一个 provider = 一个单点故障**。多 agent 系统在生产上要至少两家 provider 可用。这个改动同时让 P5 原则（Reviewer-Executor Provider 隔离）成为"默认正确"而不是"用户要记得配"。

---

## 7. 开源对标地图

放在这里不是为了"向你推销又一个框架"，而是给你找参考实现时少走弯路。每个项目的详细分析在 [`doc/references/`](../references/) 下。

| 维度 | 可参考项目 | 能抄走什么 | 对应本项目哪一块 |
|---|---|---|---|
| **多 agent 编排范式** | [openai-agents-python](../references/openai-agents-python-analysis.md) | Handoff、on_llm_start/end hooks、Output Guardrail | §3.1 Contract 可以扩展成 output guardrail |
| **Orchestrator 分阶 + 钩子系统** | [Hermes Agent](../references/hermes-inspired-improvements.md) | 生命周期钩子、Prompt caching `system_and_3`、上下文三阶段压缩、凭据池轮转 | §4 整章都有它的影子 |
| **长期记忆设计** | [MemPalace](../references/mempalace-analysis.md) | 四层记忆栈（Palace/Wing/Room/Drawer）、时间线关系图 | §3.5 的三层加载 |
| **代码库压缩成图** | [Graphify](../references/graphify-analysis.md) | AST + 社区检测 + God Node 识别 | 规划中的可选 L0 增强 |
| **语义级 3-way merge** | [Weave](https://github.com/ataraxy-labs/weave) / [merge-engine](https://docs.rs/merge-engine/) / Mergiraf | tree-sitter 实体级合并；4 层 Pattern DSL → CST → Version Space → Genetic 兜底 | 未来 Executor 的 rule-first pre-resolution |
| **Fork 同步点检测** | [git-machete](https://github.com/VirtusLab/git-machete) / `git cherry` / `git log --cherry-pick` | Patch-ID 验证、fork-point override | `sync_point_detector.py` 的直接来源 |
| **Post-conflict LLM 修复** | [mergefix](https://pypi.org/project/mergefix/) / [reconcile-ai](https://github.com/kailashchanel/reconcile-ai) | 批量冲突解析 | 定位不同，但可启发 Executor 批次粒度 |
| **并行 agent 隔离** | [clash](https://github.com/clash-sh/clash) | Worktree 冲突检测 | 未来并发 run 隔离 |

**一个反向的观察**：我们调研的 15+ 个开源项目里，没有任何一个同时具备 "多 agent + 确定性 VETO + 迁移感知 merge-base + 可插拔 baseline parser"。这是领域专用系统相比通用框架的天然优势——**你的业务约束会迫使你做出通用框架不会做的设计**。如果你的 agent 系统还完全对齐某个通用框架，大概率是还没走到生产。

---

## 8. 给 Agent 开发者的决策 Checklist

按"从易到难 + 从早到晚"排。能在原型阶段就加的就不要等生产：

### 原型阶段（第 1 周就该有）

- [ ] 每个 agent 一份 **contract YAML**，最小版本只写 `inputs` 白名单也值。配套一个 `ReadOnlyStateView` 代理。
- [ ] LLM 调用**统一走 `_call_llm_with_retry`**。原型阶段重试策略可以粗糙，但入口必须唯一。
- [ ] Prompt 走 **registry**，不允许 literal string 出现在 agent 代码里。前缀分派（`A-*`、`B-*`）约定早定。
- [ ] State 用 **Pydantic v2**，所有写操作返回新对象。禁 in-place mutation，从第一天就禁。

### 接近 MVP（上第一个 beta 用户前）

- [ ] **错误分类器** + per-agent 熔断器。至少区分 `RATE_LIMIT / AUTH / FORMAT / OTHER`。
- [ ] **Checkpoint**（单文件滚动 + 原子写入 + SIGINT hook）。就算你现在不支持 resume 也要写 checkpoint，事故复盘要用。
- [ ] **唯一写入通道** + **snapshot-before-write**。列出你系统里"不可逆副作用"的清单，给每一项单独过一次 review。
- [ ] **Trace logger**（JSONL，每条 LLM call 一行，含 gate ID + rendered prompt + response）。生产事故第一现场。

### 上生产前（最后一根弦）

- [ ] **Reviewer-Executor Provider 隔离**。至少一个审查 agent 用和 Executor 不同家的模型。
- [ ] **Review 回环的 max_rounds + meta-review threshold**。两个都配，并加边界单测（§6.3 那种）。
- [ ] **凭据池 + fallback provider**。单 key 单 provider 在任何量级都是定时炸弹。
- [ ] **Cost tracker**。每个 agent 的 token/$ 分开记，Run 结束汇总进报告。
- [ ] **至少一个确定性 VETO 通道**。找出 LLM 在你领域会系统性漏掉的 3~5 种模式，每个写 checker。

### 生产后的迭代

- [ ] Prompt caching（Anthropic `system_and_history`）：几乎无脑 ROI。
- [ ] Smart model routing（primary + cheap_model）：简单任务降档，≥ 20% 成本省。
- [ ] MemoryExtractor 式的"条件 LLM 提炼"：先用规则，只在规则捞不到的高信息事件上上 LLM。
- [ ] Baseline parser 插件化：如果你有多语言/多生态要支持，早做 entry_points。

---

## 附：项目内参考

| 主题 | 文档 |
|---|---|
| 系统架构总览 | [`doc/architecture.md`](../architecture.md) |
| 状态机与 Phase | [`doc/flow.md`](../flow.md) |
| Agents 详解 | [`doc/modules/agents.md`](../modules/agents.md) |
| Core 调度 | [`doc/modules/core.md`](../modules/core.md) |
| LLM 层 | [`doc/modules/llm.md`](../modules/llm.md) |
| 记忆系统 | [`doc/modules/memory.md`](../modules/memory.md) |
| Tools 工具层 | [`doc/modules/tools.md`](../modules/tools.md) |
| 6 大丢失模式 + 10 条加固 | [`doc/multi-agent-optimization-from-merge-experience.md`](../multi-agent-optimization-from-merge-experience.md) |
| 迁移感知 merge-base | [`doc/migration-aware-merge.md`](../migration-aware-merge.md) |
| 开源对标（汇总） | [`doc/references/opensource-comparison.md`](../references/opensource-comparison.md) |

---

**写在最后**

做 agent 最容易犯的错是把它当成"把 LLM 接到 for 循环里"。真实生产里 90% 的工程量花在**约束 LLM 不该做什么**，而不是让它做更多——contract、权限隔离、确定性 VETO、熔断、snapshot、checkpoint、meta-review、人工出口。这 90% 的工作通用框架不会替你做，因为它们不了解你的领域约束。

本项目不是一个"可以直接用的 agent 框架"，它是一份**在真实问题上一条条踩出来的工程清单**。希望这份分享能帮你少踩几条。
