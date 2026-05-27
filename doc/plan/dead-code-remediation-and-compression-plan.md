# 死代码治理 + Prompt 压缩 修复方案

> 下一阶段执行。基于 2026-05-25 全量死代码盘点（[../references](../references)）+ prompt 截断路径核查 + TUI 迁移残留核查。
> 关联：[死代码盘点 memory]、[dependency-graph-optimization-plan.md](../references/dependency-graph-optimization-plan.md)。
> 原则：被取代/弃用的直接删；半成品架构优化按开源参考文档判断保留价值；契约漂移修复；迁移术语债按重命名/文档处理（非删除）。

---

## Part 1 — Prompt 直接截断 → 压缩（用户要求 #1）

现状：仍有**两层**对 prompt 做 `content[:max_chars]` 式硬截断（盲切尾部，可能截断 JSON/代码中段）。

### 1.1 prompt 段级（`prompt_builders.py`）
主路径 `build_staged_content` 已是基于相关性的分级渲染（AST chunk → FULL/SIGNATURE/DROP），是真正的压缩。但保留了 **3 处 `content[:max_chars]` 硬截断兜底**：

| 行 | 触发场景 | 问题 |
|---|---|---|
| :115 | 小文件快路径 | 文件虽小仍可能超 budget 被盲切 |
| :132 | AST chunk 为空（不支持的语言/非代码）| 配置/文档文件尾部被静默丢弃 |
| :173 | 相关性把所有 chunk 都 DROP（`used_tokens==0`）| 正是注释自述导致 Judge "false truncation" bug 的场景，回退又重新引入半截内容 |

**改造**：把这 3 处 `content[:max_chars]` 换成**边界感知截断**——不在 token/语法中段切断。复用已死的 `ContextAssembler._truncate_text`（context.py，含 head/middle/tail 策略）作为该原语**抢救保留**（其余 ContextAssembler 删除，见 Part 3）。对非代码文件优先按行/段边界裁剪并加 `[... 中段省略 ...]` 标记；对 `used_tokens==0` 场景退回 SIGNATURE 级渲染而非裸切。

### 1.2 对话级（`context_compressor.py`）
实际实现三阶段：① `_prune_stale_outputs` 剪枝陈旧输出（零成本）② `_truncate_middle` 边界感知截断中段 ③ `_drop_middle`（context_compressor.py:128 明确标注 "aggressive middle removal (synchronous, **no LLM call**)"，直接丢弃中段消息）。
**问题**：模块顶部 docstring 与 `base_agent.py:494` docstring 都宣称第 ③ 阶段是 "Summarize middle（需 summary client）"，但**该能力从未实现**——`ContextCompressor.__init__`(context_compressor.py:50) 根本没有 summary client 参数，第 ③ 阶段写死为同步丢弃。docstring 是 aspirational 的虚标。

**改造**：
- 新增一个**可选的语义总结阶段**（注入廉价 summary client，Haiku 档），在 `_drop_middle` 之前尝试总结中段而非直接丢弃；通过 `AgentLLMConfig.compression` 增配开关，默认开启、可关、缺 client 时回退到现有 drop 行为。
- **同步修正 docstring**：要么实现总结、要么把顶部/`base_agent` docstring 改为如实描述（第 ③ 阶段=丢弃），消除虚标。

> 收益：上下文压力下不再盲丢中段历史 / 盲切文件尾部，保留语义。源自 [hermes-inspired-improvements.md](../references/hermes-inspired-improvements.md) §三阶段压缩——hermes 设计含 summarize 阶段，本仓库**只落地了前两阶段 + drop，summarize 从未建**。

---

## Part 2 — 修复 judge 质量门静默失效（Tier 1，是 bug）✅ 已完成（f9b60ae）

> 状态（2026-05-27 核查）：契约字段 `sentinel_hits`/`shadow_conflicts` 已在 `judge.yaml` inputs（第 17-18 行）；回归测试 `test_p2_hardening.py::TestJudgeAgentP2Checks::test_dead_checks_revived_under_judge_contract` 走真实 `restricted_view` 契约路径断言两类 issue 触发。本节为历史记录。


`_check_sentinel_hits`(judge_agent.py:897) 与 shadow_conflicts 检查(judge_agent.py:714) 读 `getattr(state, x, 默认)`，但 `sentinel_hits`/`shadow_conflicts` **不在 `judge.yaml` inputs**。`FieldNotInContract` 继承 `AttributeError`(contract.py:90) → `restricted_view`(judge_agent.py:60) 下 getattr 吞异常返回空默认 → 检查永不触发。数据确被填充（executor_agent.py:124 / planner_agent.py:190）。

**改造**：`src/agents/contracts/judge.yaml` 的 `inputs` 增加 `sentinel_hits`、`shadow_conflicts` 两行（同此前修 `interface_changes`/`reverse_impacts` 的做法）。
**回归**：单测断言两字段非空时 judge 产出对应 issue（守护，防再次静默失效）。纯收益、最高优先级。

---

## Part 3 — 直接删除（被取代 / 弃用 / 无依据，已确认无异议）

| 单元 | 成因 | 备注 |
|---|---|---|
| `src/integrations/` 整包（github_client.py + github_formatter.py）+ `config.github` | 一次性 spike（2026-03-29 单提交）；GitHub PR-review 设想的人机通道被后续 Web UI（经 TUI 过渡）取代 | 删整包 + 清 config 字段 |
| `ContextAssembler` 类 + `_SAFETY_MARGIN`（context.py）| 被 context_compressor/prompt_builders 取代 | **保留 `_truncate_text`**（Part 1.1 复用）；保留 TokenBudget/estimate_tokens/get_context_window/ContextSection（仍在用）|
| `planner_agent._classify_file`(1626) | 被批量分类 `_classify_batch` + `_enhance_risk_scores` 取代 | 私有方法，无歧义死代码 |
| `PhaseRunner.run_batched`（含仅被它调用的 `run_parallel`/`run_sequential`）| 活的并行走 `ParallelFileRunner`（executor_agent.py:944 / conflict_analyst_agent.py:112，= game-studios O-C 落地版）| ⚠️ `PhaseRunner` 类**被实例化并贯穿传入每个 phase**（orchestrator.py:169 → `ctx.phase_runner`，base.py:65），但 phase 从不调用其任何 run_* 方法——属"接线但零流量"（同 MessageBus，见 4.2）。删 run_* 方法前确认 `ctx.phase_runner` 整个字段是否可一并移除 |
| `MemoryStore.consolidate()` / `SQLiteMemoryStore.consolidate()`（store.py:203 / sqlite_store.py:368）| 冗余公开包装：真正的 300+ 去重已由 `_consolidate_entries` 在 load/save 时**自动执行**（store.py:32、sqlite_store.py:157），公开 `consolidate()` 无人调用 | 删公开方法；保留 `_consolidate_entries`（活）|
| `planner_judge_prompts.filter_obviously_safe_files` | 为"future split-send pass"预留、无落地、无文档支撑 | 兄弟 `is_segment_obviously_safe` 仍在用，仅删逐文件变体 |
| `state_machine.get_valid_transitions` / `remove_observer` | 框架完备性方法，无调用、无文档 | |
| `cli/commands/setup.migrate_merge_record` | 一次性迁移工具，从未接线（根目录 `MERGE_RECORD/` 至今残留=从未执行）| 删函数；遗留目录手动处理 |

---

## Part 4 — 成因三半成品：保留并进一步优化（依据开源参考文档）

核查 `doc/references/` 后确认：成因三确为**参考开源项目做架构优化的半成品**。部分提案已完成（`guardrails.py` 已接 planning.py；`Coordinator` O-D 已接 orchestrator/auto_merge），以下是**值得保留并完成**的：

### 4.1 🔧 完成 LLM 生命周期钩子（HookManager）— 高价值，有完整设计
- **出处**：[openai-agents-python-analysis.md](../references/openai-agents-python-analysis.md) §2.1（+ hermes §Hook 系统）。
- **现状**：`hooks.py` 的 `HookManager` + `HOOK_LLM_START/HOOK_LLM_END` 常量已建，但 `base_agent` 未 emit、`orchestrator` 无 `_inject_hooks`——半成品。
- **完成动作**（文档已给出 ~40 行方案）：`base_agent._call_llm_with_retry` 成功/失败分支 emit `agent:llm_start/end`；`orchestrator` 仿 `_inject_memory` 增 `_inject_hooks`。
- **收益**：监控/成本统计/Web UI 进度订阅 LLM 事件**无需侵入 agent 代码**。

### 4.2 ⚖️ 统一 MessageBus 与 Hook（去重）
- **出处**：[claude-code-game-studios-analysis.md](../references/claude-code-game-studios-analysis.md) §7 误以为 MessageBus 是工作中的 agent 消息底座；hermes §184 指出其 `try/except: pass` 静默吞错。
- **现状**：`MessageBus` 被实例化并贯穿传入每个 phase(base.py:63)，但 `.publish/.subscribe` **零调用**——线性流水线用 `MergeState` 做共享底座，不需要 pub/sub 消息。
- **动作**：**二选一**——(a) 若做 4.1 的事件化，把 MessageBus 折叠进 HookManager（一套事件机制，去重）；(b) 否则删除 MessageBus 及其 PhaseContext 字段。倾向 (a)：事件订阅与生命周期钩子是同一需求。

### 4.3 🌱 内存按需加载 + 效果反馈（query_by_path / query_by_tags / query_by_type / entry_outcome）
- **出处**：[enhanced-context-memory-proposal.md](../references/enhanced-context-memory-proposal.md)（:352 `query_by_path` 按需加载）+ [mempalace-analysis.md](../references/mempalace-analysis.md)。
- **现状**：`query_by_path/by_tags/by_type` + `entry_outcome` 已建未接；实际检索走更简单的 `get_memory_context`。
- **动作**：**对照 enhanced-context-memory 提案决定**——`query_by_path`（按路径按需加载）是提案核心检索路径，若推进该提案则保留并接通；`entry_outcome`（命中效果反馈闭环）随提案的 memory 有效性度量一并评估。**这一组的去留绑定 enhanced-context-memory 提案是否立项**，不在本轮单独删。
- 注：提案 :565 描述的 300+ 去重（`_consolidate_entries`）**已在生产自动执行**，不在此列；其冗余公开包装 `consolidate()` 已归入 Part 3 删除。

---

## Part 5 — TUI 术语债清理（迁移残留，非死代码）

迁移到 Web UI 后已**无终端 TUI**：`src` 无 Ink 导入/终端渲染，`web/` 是纯 React 18 + Vite，`web/src` 零 Ink 引用。残留的是迁移术语债——**代码是活的，只是名字/文档没改**，按重命名/文档处理，不是删除：

| 类型 | 位置 | 处理 |
|---|---|---|
| 命名残留 | `src/web/ws_bridge.py`（10 处 "TUI" 字样：docstring/日志，如 "WebSocket TUI clients"、"TUI client connected"、"from the TUI"）| WebSocket 客户端实为**浏览器**，重命名为 "Web/WebSocket client"。纯 docstring/日志改名，零功能风险。`serializers.py` 已清零 |
| 废弃 flag | `src/cli/main.py:224` `--tui`（已 deprecated，别名 `--web`，`web = web or tui`，带迁移警告）| 若 Web 迁移已稳定无人再用，连同 deprecation 分支删除；否则保留兼容期 |
| 注释/文档残留 | `plan_review.py`/`human_review.py`/`auto_merge.py` 注释里 "CLI/TUI"、"--no-tui"；`doc/architecture.md`、`doc/web-ui.md`、explain-arch skill（"Ink TUI ws-client.ts" 已不存在）等多份文档 | 批量 TUI→Web 文档更新，可单独一轮，不紧急 |

> 注意：`ws_bridge.py` 是**活的 Web 桥**（cli/commands/web.py:36 在用），归"重命名/文档"而非"删除"。

---

## 执行顺序与优先级

| 阶段 | 内容 | 风险 | 优先级 |
|---|---|---|---|
| 1 | Part 2 judge 契约修复（2 行 + 回归测试）| 极低，纯收益 | **P0** ✅ 完成（f9b60ae）|
| 2 | Part 3 删除（被取代/弃用项，含抢救 `_truncate_text`）| 低（已确认无引用）| **P0** |
| 3 | Part 1 截断→压缩（1.1 边界感知兜底 + 1.2 注入 summary client）| 中（影响所有 LLM 调用上下文）| **P1** |
| 4 | Part 4.1 完成 HookManager LLM 钩子 + 4.2 与 MessageBus 去重 | 中 | **P1** |
| 5 | Part 5 TUI 术语债：ws_bridge 重命名（低风险）+ `--tui` flag 去留 + 文档批量更新 | 低（重命名/文档）| **P2** |
| 6 | Part 4.3 内存 API：等 enhanced-context-memory 提案立项再定 | — | **P2/待定** |

## 风险与约束
- Part 1 压缩改动影响所有 agent 的上下文装配——需在真实 forgejo 仓库验证 Judge 不再误判 truncation（见 `feedback_verify_real_forgejo`），不能只靠单测。
- Part 3 删除前对每个符号重新 `grep` 确认仍无生产引用（盘点是 2026-05-25 时点快照）。
- summary client 注入遵守"按复杂度分档"——用 Haiku 档，不得用主 agent 的贵模型做压缩总结。
- 删除遵守"不留向后兼容垫片"：直接删，不保留 re-export / `# removed` 注释。
- 所有契约字段改动后跑 `tests/unit/test_agent_contracts.py`。
