# 自学习系统方案：执行结果接地的闭环自我改进（无权重微调）

> **定位**：为 CodeMergeSystem（基于商用 API LLM 的多 agent 代码合并系统）设计一套**不改模型权重、不引入 embedding、可量化验证**的持续自我改进系统。
>
> **方法**：先做全网调研（5 路并行检索 → 24 源抓取 → 119 条断言 → 25 条对抗式核验，0 条被推翻），再对照本仓库现有"记忆/反思"地基做缺口分析，最后给出分阶段、可单独评审的落地方案。
>
> **一句话结论**：主流自学习 agent 的共识是「**非参数化、可审计的记忆 + 自然语言反思 + 可复用经验库 + 提示/策略自动进化**」，全部在商用 API 上验证有效——这**正面印证了本项目刻意不微调、用 sqlite 记忆的设计**。本项目已有相当完整的记忆地基，真正的缺口不在"加更多记忆"，而在**把"本次 run 的可验证结果"接回去改进下次 run，并对此做度量**。

最后更新：2026-05-30

---

## 0. 阅读路径

| 想看什么 | 跳到 |
|---|---|
| 调研结论（5 范式 + 利弊 + 失败模式） | §1 |
| 本项目现有"自学习"地基 + 缺口矩阵 | §2 |
| 设计原则（哪些能做 / 哪些被否） | §3 |
| 分阶段落地方案（含文件级接入点） | §4 |
| 量化评估与验收门 | §5 |
| 风险、失败模式防护、明确非目标 | §6 |
| 引用清单 | §7 |

---

## 1. 调研结论：主流 agent 自学习系统的五大范式

> 每条断言均经 3 票对抗式核验；标注 `confidence` 与来源。完整引用见 §7。

### 1.1 总判断（直接印证本项目的设计选择）

- **非参数化、可审计的记忆是持续改进的首选机制，优于权重微调**（high）。参数化记忆"每条事实都要微调、难审计、难删除（machine unlearning 仍不成熟）、且基本只能离线"，并伴随灾难性遗忘；对 agent 交互逐步反向传播被描述为"unaffordable"。来源：arXiv 2603.07670 §4.6、ACM TOIS 10.1145/3748302 §5.2.3、arXiv 2502.14802。
  → **本项目"不微调、用可读 sqlite 记忆"的路线被一手综述正面背书，不是成本妥协。**
- **不微调 ≠ 能力打折，甚至能反超权重 RL**（high）。GEPA（纯提示进化）在 6 个任务上平均超过 GRPO（权重更新 RL）6%、最高 20%，且 rollout 少最多 35×（arXiv 2507.19457）；Memento（无梯度、case-based 记忆）在 GAIA 验证集 87.88% Pass@3、OOD 任务 +4.7~9.6%（arXiv 2508.16153）。
  → 无微调路径在工程上可竞争；本项目的方向有上限保障。

### 1.2 五类范式 × 实现原理 / 利弊 / 失败模式

| # | 范式 | 实现原理 | 利 | 弊 / 失败模式 | 对本项目的可迁移点 |
|---|---|---|---|---|---|
| 1 | **经验记忆与跨任务复用** | 综述把记忆形式化为 **W/P/R 三操作**：Writing（把原始观测投影成精炼内容）、Management/Consolidation（摘要、合并相似、遗忘不重要）、Reading（按相似度检索）。自我进化来自 experience accumulation / environment exploration / knowledge abstraction。来源：ACM TOIS 10.1145/3748302 | 跨 run 复用过去失败；可审计、可删除 | **检索污染**（逐条原样写入降低检索精度）、**摘要漂移**、**无界增长导致错误传播** | 本项目 `memory_extractor`=W/P、`layered_loader`=R 已精确对应该分类 |
| 2 | **反思 / 自我批判循环** | Reflexion：失败后写一段自然语言"复盘"，下一回合 prepend 进 prompt——"不靠更新权重，靠语言反馈强化"（arXiv 2303.11366）。反思**信息量决定效果**：高信息（完整解法/修复步骤）>> 低信息（retry/关键词），GPT-4 baseline 0.79→0.93（solution）vs 0.83（retry）（arXiv 2405.06682） | 零权重更新即可显著提升 | **反思可信度陷阱**：MCQA 上 p<0.001 的提升是 **oracle 反馈上界**（被直接告知正确答案）；盲反思被高估 | **执行接地的反馈（compile/test 错误）比自由反思可信得多**；记忆条目要带"具体怎么修的"而非空泛标记 |
| 3 | **技能 / 课程式增长** | Voyager 仅用 GPT-4 黑盒查询（零微调）维护一个**不断增长、可组合、可复用的可执行技能库**，由"环境反馈 + 执行错误 + 自我验证"驱动迭代（arXiv 2305.16291） | 能力可累积、缓解灾难性遗忘 | 领域是 Minecraft；可迁移的承重点是"**反思必须接地在具体执行信号**"而非自由文本 | 本项目 executor 修复链 + judge 复审产生确定性"成功/失败"信号，可沉淀为可复用"修复配方" |
| 4 | **提示 / 策略自动优化** | GEPA：采样执行轨迹→自然语言诊断→提出并测试提示更新→在自身 Pareto 前沿合并经验（2507.19457）。MIPROv2（DSPy）：bootstrap few-shot + 提议指令 + 贝叶斯优化联合选优（dspy.ai）。PromptBreeder：遗传算法演化提示**且演化"变异提示"本身**，PaLM 2-L 上超 OPRO/APE（2309.16797） | 可超权重 RL、样本效率高 | **需要带标注的验证集 + metric**；PromptBreeder ~$60/~1万次调用/run，成本高 | 本项目有稳定 gate 提示 ID（`gate_registry` P-*/J-*…）+ `doc/evaluation/`，是离线提示优化的理想底座，但受"标注集+成本"前置约束 |
| 5 | **结果反馈在线学习 vs 微调取舍** | 综述把记忆进化分三层 **Storage（忠实记录轨迹）→ Reflection（主动 critic 去噪/纠偏）→ Experience（跨轨迹抽象成可复用规则/策略/技能）**（arXiv 2605.06716）；Memento 证明无梯度 case 记忆可匹敌梯度基线（2508.16153） | 在线、可审计、低成本 | 微调离线、贵、灾难性遗忘 | 本项目 raw run log → `memory_extractor` 反思去噪 → 跨 run 抽象的合并决策规则，恰是这三层 |

### 1.3 三大生产失败模式（本项目必须工程防护）

| 失败模式 | 机理 | 量化证据 | 来源 |
|---|---|---|---|
| **F1 摘要漂移（summarization drift）** | 每次压缩静默丢弃低频细节，多轮后只剩"消毒过的泛化历史" | 仍约 1/5 事实被扭曲；多 session 保留率低至 ~37% | 2603.07670 §4.1、2601.04463 |
| **F2 检索污染 + 无界增长** | 逐条原样写入引入噪声降低检索精度；不受限扩张让错误传播污染学习 | **选择性 add+delete 比裸增长平均 +10% 绝对分**；"全存"可能**比没有记忆还差** | 2603.07670 §7.1、2505.16067 |
| **F3 陈旧知识静默失效** | 过时知识失败时无明显信号，且在语义检索里**仍打高相关分**——相似度无法识别时间失效 | 需显式 temporal-decay/recency/supersession 策略 | 202601.0618、2501.13956 |

> **关键洞察（决定本方案边界）**：F3 在论文里是针对 embedding 检索说的；**纯词法检索同样缺时间感知**。但本项目已用 `upstream_ref 0.5× 衰减` 覆盖了"换上游基线后旧知识失效"这一最主要场景（见 §2、§3）。因此 F3 在本项目是**已部分缓解**的，不需要再加被否决的裸 recency 打分。

### 1.4 调研给出的开放问题（直接转化为本方案的工作项）

1. **代码合并这个具体领域，无微调自学习的量化收益是多少？** 所有引用基准都是 QA/数学/游戏/通用 agent，没有一个测"跨 run 经验复用对冲突解决/合并决策质量"的提升 → **必须先自建度量（§5）**。
2. **纯词法、无 embedding 的存储，加 recency/supersession 最省成本的形态是什么、不加到底损失多少检索精度？** → 本项目已用 upstream_ref 衰减回答了主场景；其余作为非目标（§6）。
3. **离线 GEPA/MIPROv2 跑一遍 gate 提示是否值得、最小标注验证集要多大？** → 列为 opt-in 后期项（§4 Phase 4）。
4. **写记忆时，执行接地反馈（compile/test/CI，可信）与自由 LLM 反思（oracle 膨胀、不可信）该如何加权，避免把噪声/幻觉教训跨 run 强化？** → 本方案核心设计原则（§3 P1）。

---

## 2. 本项目现状：已有的"自学习"地基 + 缺口矩阵

### 2.1 已有地基（经代码核实，避免重复造轮子）

**记忆子系统 `src/memory/`（W/P/R 三操作已齐全）**
- `models.py`：`MemoryEntry`（类型 PATTERN/DECISION/RELATIONSHIP/PHASE_SUMMARY/CODEBASE_INSIGHT；信心级 EXTRACTED/INFERRED/HEURISTIC）。
- `store.py` / `sqlite_store.py`：不可变存储 + WAL sqlite 持久化；`MAX_ENTRIES=500`、`CONSOLIDATION_THRESHOLD=300`、`content_hash` 唯一索引去重；`get_relevant_context()` **纯词法**打分 = `0.5 × score_path_overlap()`（exact/前缀/Jaccard×0.85）`+ 0.5 × confidence`（加权和，非相乘，见 `store.py:136`）；`upstream_ref` 标签不匹配则该条 confidence ×0.5（=F3 的主场景缓解）；另有 `remove_superseded(phase)` 做**run 内** phase 顺序覆盖。
- `layered_loader.py`：三层加载 L0（codebase profile）/L1（phase 模式与决策）/L2（文件相关条目），动态 L2 上限（条目越多注入越少），`relevance_filter_threshold=100` 后启用相关性过滤。
- `summarizer.py`：每 phase 后自动产 `PhaseSummary` + 条目；`_is_epistemically_empty()` 正则**防记忆投毒**（剔除模型"放弃/无 diff"类空话）——这就是 §1.2 范式 2 说的"反思去噪"的雏形。
- `hit_tracker.py`：记录 L0/L1/L2 每 phase 实际注入 → 反向记录每个 `entry_id` 被注入到哪些 file → 后续 pass/fail；`outcome_scores()` 产出 per-entry 有效性 =(pass−fail)/(pass+fail)；`harmful_entry_ids(≤−0.5, min_obs=2)` 识别有害条目，**且已被 `layered_loader._build_l2():117-127` 在注入时过滤跳过（O-M6，已接线）**——即"软删"的**临时形态已存在**，但不持久化为 entry 字段、依赖 tracker 累计观测存活、且只作用于读取时。
- `bootstrap.py`：冷启动从 `<repo>/CLAUDE.md` 抽 section 作 CODEBASE_INSIGHT 种子。

**已有的"反思/自我改进"循环（执行接地，质量很高）**
- PlannerJudge 修订循环：`max_plan_revision_rounds=5`。
- Judge dispute-repair：`max_dispute_rounds=2`、`max_batch_repair_rounds=1`。
- Executor 修复链：dedup（顶层重复符号）/ `_foreign_chars` / `hallucinated_symbol_guard` / grounding（base/current/target 三路 diff）——**全部确定性、可验证**。

**已有的反馈信号源（=自学习的"地面真值"，已就绪）**
- `ci_reporter`：status（success/needs_human/failed/**partial_failure**）、by_category 矩阵、judge_verdict。
- `compile_gate`：`has_compile_gate(config)`；E2E 实证可抓"括号平衡但类型错"的合并（见 `doc/review/03-production-readiness`）。
- `CostTracker`：per-call token/成本。
- `state.errors` / `state.judge_verdict.issues` / `state.file_decision_records`：结构化错误与 per-file 决策来源。

**已有的一个反馈环（但休眠）**
- **OPP-5 `outcome_confidence_writeback`**（`config.memory.outcome_confidence_writeback`，**默认 False**）：judge pass/fail → 经 `hit_tracker.outcome_scores()` 微调存量条目 confidence；`min_observations=3`；豁免 `decision_source∈{HUMAN,BATCH_HUMAN}` 与 bootstrap 条目。**只调存量、不增不删。**

### 2.2 缺口矩阵：研究范式/失败模式 × 本项目现状

| 研究项 | 本项目已覆盖 | 真实缺口（本方案补齐） | 优先级 |
|---|---|---|---|
| 范式1 记忆 W/P/R | ✅ 完整 | — | — |
| 范式2 反思信息量（高信息 >> 低信息） | 🟡 有 `_is_epistemically_empty` 去噪 | 未强制条目"带具体修复内容"；可能写出低信息条目 | P2 |
| 范式3 技能/经验库 | 🟡 `ScarListBuilder`（**git 提交挖掘** restore/revert/compat） | 缺**运行期 verified-repair 配方库**：executor 修复成功（judge PASS）的"错误签名→修复算子"未沉淀复用 | **P1** |
| 范式4 提示自动优化 | 🟡 稳定 gate ID + `doc/evaluation/` | 无离线 GEPA/MIPROv2 通道（成本/标注前置） | P3（opt-in 后期） |
| 范式5 Storage→Reflection→Experience | ✅ Storage、🟡 Reflection | **Experience 抽象层薄**（同范式3 缺口） | **P1** |
| **F2 选择性 add+DELETE（+10%）** | ✅ 选择性 add；✅ `harmful_entry_ids` 已被 `layered_loader` 注入时过滤（O-M6，软删临时形态） | 软删**不持久化/不可审计/依赖 tracker 存活**，且只作用于读取时（写入/consolidation 期不作用、跨 run 易丢失） | P1（巩固为持久可审计） |
| 执行接地反馈环（OPP-5） | 🟡 已实现但**默认关、仅调 confidence** | 反馈环休眠；未与 compile/CI 信号融合；无安全护栏与度量支撑其开启 | **P1** |
| F1 摘要漂移 | 🟡 consolidation 有目录桶（OPP-8）防有损合并 | 关键不变量未"锚定"防被反复再摘要稀释 | P2 |
| **度量"记忆是否真的有用"** | ❌ hit_tracker 有原始数据，**无消融/AB 评估** | **无法证明"学到了"**——开放问题1 | **P0（前置一切）** |

**结论**：本项目不缺"记忆"，甚至已有软删（O-M6）与休眠的反馈环（OPP-5）。真正缺的是 **(P0) 度量 + (P1) 把可验证结果稳定接回去（把现有临时软删巩固为持久可审计 + 激活并加固反馈环 + verified-repair 经验库）**。P2/P3 是质量与上界增益。

---

## 3. 设计原则（每条建议都必须满足）

| # | 原则 | 落地约束 | 研究依据 / 本项目依据 |
|---|---|---|---|
| **P1** | **执行接地优先于 LLM 自报** | 任何写回/经验沉淀的"成功/失败"信号，只取自确定性来源（judge verdict、compile_gate、ci_reporter、deterministic_issues），**绝不取 LLM 自报 provenance** | 范式2 oracle 陷阱；round2 已否"LLM reflection 层（自报不可信）" |
| **P2** | **先度量再激活** | 任何反馈环/写回/delete 在默认开启前，必须先有 §5 消融基线证明净收益为正 | 开放问题1；Anthropic evals 指南 |
| **P3** | **选择性 add + 选择性 delete** | 写入保持选择性（现状）；**新增**对有害条目的"suppress/quarantine"（软删、保留可审计），不裸删 | F2（+10%）；保持记忆可审计 |
| **P4** | **无默认零行为反模式** | 不新增"默认 no-op 的打分维度"。新机制要么默认有可验证行为、要么是 opt-in 工具 | round2 否决"recency+importance（默认零行为）" |
| **P5** | **不引入 embedding、保持纯词法可复现** | 检索维持 `score_path_overlap × confidence`；新增信号走标签/结构化字段，不引向量 | round2 刻意拒绝 embedding 的 lightweight/可复现 ethos |
| **P6** | **目标仓库无关 + 既有架构约束** | `src/` 零仓库知识；reviewer agent（judge/planner_judge/human_interface）只读、不写 state；LLM 调用走 `BaseAgent._call_llm_with_retry`；提示走 `get_gate("<ID>")`；文件 <800 行；mypy strict；单测 ≥80% | CLAUDE.md / 契约反模式 |

**明确不做（§6 详述）**：裸 recency+importance 打分、通用跨 run 失效（upstream_ref 衰减已覆盖）、反向 impact 半径、通用 LLM reflection 层、embedding 检索、任何权重微调。

---

## 4. 分阶段落地方案

> 每阶段：目标 → 接入点（文件/类/gate/契约）→ 防护 → 验收。接入点位置为**方向性参考**，落地前按 [[feedback_dead_code_check]] 先 `grep` 生产 caller 复核。

### Phase 0 —— 记忆有效性度量底座（前置一切，必须先做）

**目标**：把"记忆/反馈是否真的让合并决策更好"变成一个能搬动的数字。没有它，后续任何反馈环都不能安全默认开启（原则 P2）。

**做什么**
1. **消融评估器**：在固定合并数据集上对同一组 run 跑两遍——`memory=on` vs `memory=off`（已有 `config.memory` 开关足以驱动），对比决策质量。
2. **决策质量指标**（复用 `doc/evaluation/metrics.md` 口径，新增）：
   - `memory_influenced_decisions`：注入记忆**改变了**的决策数（hit_tracker 已有注入↔file 映射）。
   - 其中 `correct_after_influence`：被改变且最终 judge PASS / compile 通过的比例。
   - `harmful_influence_rate`：被改变且导致 fail 的比例（= F2 的直接度量）。
   - `per_entry_effectiveness` Top/Bottom 榜（`hit_tracker.outcome_scores()` 已有，做成报告项）。
3. **离线回放器**：从既有 `runs/<id>/checkpoint.json` + memory.db 复算，无需重跑真实 LLM（降成本）。

**接入点**
- 新增 `src/tools/memory_eval.py`（纯函数，读 hit_tracker + state + ci_reporter，产 `MemoryEffectivenessReport`）。
- `hit_tracker.py:summary()` 扩展：输出 per-entry effectiveness 排名（数据已具备）。
- `doc/evaluation/metrics.md` / `acceptance.md` 增"记忆有效性"小节，定义验收阈值。
- report_writer 末尾渲染 "Top-5 helpful / harmful entries this run"。

**防护**：纯读、不改决策路径；reviewer 只读约束不触碰。
**验收**：在 forgejo Tier-1 样本（[[reference_forgejo_eval]]）上产出 `memory=on/off` 决策质量对比表；得到本项目第一组"记忆收益基线"。
**工作量**：~2–3 天。**这是整套方案的地基，优先级最高。**

---

### Phase 1 —— 闭合执行接地反馈环（最高 ROI）

> **落地状态（2026-05-31，feat/web）**：A/B/C 全部实装（`b83d142`/`6b4f905`/`6bc77c3`）。
> A、B 的反馈环按 P2「先度量再激活」默认 **opt-in（False）**——`memory.persist_suppress`、
> `memory.writeback_signal_sources` 默认 `["judge"]`（=旧行为），需 `merge eval-memory`
> 多 run 基线证明净收益为正（§3 激活门：`MDL>0` 且 `memory_harmed=0`）方可翻默认。
> C 为纯加性、执行接地，默认 **True**（`memory.repair_recipe_enabled`）。
> **B 偏差**：CI/partial_failure 信号有意延后——它在 `report_generation` 产出，晚于
> judge_review 记忆钩子；完整融合需把写回迁到 report 阶段（未做）。故 B 现仅
> `judge + compile` 两源。

> 对应研究最强三条证据：选择性 add+**delete** +10%（F2）、执行接地 >> 自反思（范式2）、Experience 抽象（范式5）。拆三个可独立评审的子项。

#### P1-A 把临时软删（O-M6）巩固为持久、可审计的 suppress（原则 P3）

**现状与问题**：`layered_loader._build_l2()` 已调用 `harmful_entry_ids()` 在**注入时过滤**有害条目（O-M6，已接线）——选择性 delete 的核心机制其实已存在。但它是**临时、读取期、不持久化**的：判定每次重算、完全依赖 `hit_tracker` 累计观测存活；一旦 tracker sidecar 跨 run 丢失或观测不足，被过滤的有害条目又会"复活"注入。它也**只作用于读取**，对写入/consolidation 无约束（F2 的无界增长侧未覆盖）。

**改造（巩固，而非从零造）**
- `MemoryEntry` 增字段 `suppressed: bool = False` + `suppressed_reason: str | None`（**软删，保留可审计**，不物理删除——契合"可审计"卖点；把临时过滤升级为持久状态）。
- `MemoryStore/SQLiteMemoryStore` 增 `suppress_entry(entry_id, reason)`（不可变返回新对象 / sqlite UPDATE 标志位）。
- `layered_loader._build_l2()` 的过滤改为"`suppressed=True` **或** 命中当前 `harmful_entry_ids()`"——持久判定 + 实时判定并存。
- `orchestrator` 在 run 末尾（judge 终判后）把稳定有害（跨 run effectiveness≤阈值）的条目固化为 `suppress_entry(...)`，使判定不再依赖 tracker 存活。
- consolidation 期跳过 `suppressed` 条目（堵住 F2 写入/增长侧）。
- 触发**默认 opt-in**，因 Phase 0 已能度量净收益，可在基线为正后转默认开启。

**防护**：只固化满足 `min_observations` 且 effectiveness≤阈值的条目；豁免 HUMAN/bootstrap（同 OPP-5 现有豁免）；软删可经 CLI 复活。
**验收**：Phase 0 harness 显示 `harmful_influence_rate` 在"tracker 重置"场景下仍不回升（=证明持久化的增量价值），且总决策质量不降。

#### P1-B 激活并加固 OPP-5 写回，融合 compile/CI 信号（原则 P1）

**问题**：唯一的反馈环 `outcome_confidence_writeback` 默认关、只用 judge pass/fail、且只调 confidence。

**改造**
- 写回信号从"judge pass/fail"扩展为**确定性信号融合**：`judge_verdict` + `compile_gate` 结果 + `ci_reporter.status`（partial_failure 计入 fail 侧）。来源全确定性，**不引 LLM 自报**。
- 双向：helpful 条目 +Δ、harmful 条目 −Δ 并在跌破阈值时移交 P1-A 的 suppress（而非无限降权）。
- 默认值：在 Phase 0 基线为正后，从 `False` 翻为 `True`（原则 P2 把关）。

**接入点**：`orchestrator._apply_outcome_confidence_writeback()`（现有）扩展输入信号；`config.memory` 增 `writeback_signal_sources: list[Literal["judge","compile","ci"]]`。
**防护**：reviewer 只读；写回仍由 Orchestrator 持久化；保留 `min_observations`、人工/bootstrap 豁免。
**验收**：Phase 0 harness 显示 `per_entry_effectiveness` 分布右移、`correct_after_influence` 上升。

#### P1-C verified-repair 经验库（范式3/5 的 Experience 层）

**问题**：executor 修复链每次"用 dedup 解决了 RedeclarationError 并最终 judge PASS"这类**确定性成功事件**未被沉淀，下次同类错误重新试错。

**改造**
- 新 `MemoryEntryType.REPAIR_RECIPE`：键 = `error_signature`（结构化：error_class + 触发的修复算子 + 文件层），值 = **高信息**修复描述（原则：带"具体怎么修"，对应范式2 信息量证据）。
- 写入条件（**纯执行接地**）：仅当某修复算子运行后 `judge 终判 PASS`（或 compile 由 fail→pass）才写；LLM 不参与"是否成功"的判定。
- 读取：executor 打开文件命中相同 `error_signature` 时，把历史 recipe 注入 prompt（"历史上此类错误用 X 修复，成功率 Y%"）。
- 与既有 `ScarListBuilder`（git 提交挖掘）**互补**：Scar=历史人工 restore 的"坑"，REPAIR_RECIPE=本系统运行期验证过的"解法"。

**接入点**：`summarizer.py` 增 `summarize_judge_repair_rounds()`；executor 检索 recipe 经 `get_memory_context` 现有通道注入；error_signature 抽取复用 executor 已有的 deterministic 检测器输出。
**防护**：与 §1.2 被否的"LLM reflection 层"区别——provenance 是**确定性 judge/compile 结果**，非 LLM 自报（原则 P1）。
**验收**：Phase 0 harness 显示重复错误类的修复轮数（`judge_repair_rounds`）下降。

---

### Phase 2 —— 记忆质量加固（中等 ROI，便宜）

> **落地状态（2026-05-31，feat/web）**：A/B 全部实装（`4525008`/`2af4890`）。
> A（`content_quality.is_actionable_content`/`enforce_actionable`）保守降级而非删，
> 默认随入库即生效；B（`MemoryEntry.pinned`）锚定 REPAIR_RECIPE + 人工决策条目，
> consolidation 对其 passthrough。**B 偏差**：security-sensitive 锚定延后——
> summarizer 无 config 的 `security_sensitive.patterns`；`pinned` 字段已就位，
> 需后续在有 config 的入库点补标。

**P2-A 高信息条目强制**（范式2，GPT-4 0.79→0.93 的直接杠杆）
- 扩展 `_is_epistemically_empty` 的对偶：`_has_actionable_content()`——DECISION/REPAIR_RECIPE 类条目若缺"具体动作/修复"则降级或拒写。
- 接入 `summarizer.py` 各 `summarize_*` 与 `memory_extractor` 出口。

**P2-B 关键不变量锚定，防摘要漂移**（F1）
- consolidation 时给"人工决策 / REPAIR_RECIPE / security-sensitive"打 `pinned=True`，`_consolidate_entries` 跳过对 pinned 条目的再摘要。
- 接入 `store.py:_consolidate_entries`（OPP-8 已加目录桶，这里加 pinned 豁免）。

**验收**：F1 防护——多轮 consolidation 后关键条目内容零损失（单测断言）。

---

### Phase 3 —— 离线提示/策略自动优化（opt-in，后期，成本透明）

> **落地状态（2026-05-31，feat/web）**：确定性可测核心已实装（`f540613`）——
> `src/tools/prompt_optimizer.py` + `merge optimize-prompts` CLI。生成具名候选变体
> （GEPA 确定性子集=反思指令注入）、按 golden 决策准确率排名、产**人工评审报告**，
> **永不自动写回 gate_registry**。**有意外移的部分**：① 昂贵的 LLM rollout 抽象为注入的
> `rollouts` 映射（操作者自担成本产出），harness 保持纯离线可单测；② 仅支持
> no-arg/`*-SYSTEM` gate（参数化 gate 无静态基线文本）；③ LLM-反思式变体生成（GEPA
> 完整形态）留待后续，当前为确定性指令注入。这是 opt-in 子命令、默认不跑，符合
> 「上界增益、不应早于 0–1」定位。

**目标**：用 Phase 0 的评估器当 metric，离线对 gate 提示（`gate_registry` P-*/J-*/CA-*…）做 GEPA/MIPROv2 式进化。

**强约束（来自调研成本警示）**
- **前置**：必须先有带标注的合并决策验证集（最小集：每语言若干 C-class + HUMAN_REQUIRED golden，参考 [[reference_forgejo_eval]] 的 `eval/golden-forgejo-auth`）。
- **成本透明**：PromptBreeder ~$60/~1万次 LLM 调用/run；GEPA/MIPROv2 需验证集+metric。**默认不跑**，作为 `merge optimize-prompts`（离线、opt-in）子命令。
- 产物：优化后的提示候选写回 `gate_registry`，**人工评审后**才生效（不自动改生产提示）。

**接入点**：新 `src/tools/prompt_optimizer.py`（离线）+ CLI 子命令；metric=Phase 0 `MemoryEffectivenessReport` / 决策准确率。
**验收**：在 golden 集上优化后提示的决策准确率 ≥ 现状，且成本/收益记录在案。
**判断**：这是"锦上添花、上界增益"，**不应早于 Phase 0–1**。

---

### 落地路线图与依赖

```
Phase 0 (度量底座, 2-3d) ──┬──> Phase 1-A (selective delete)
   [一切前置]             ├──> Phase 1-B (激活+加固 OPP-5 反馈环)
                          └──> Phase 1-C (verified-repair 经验库)
                                   │
Phase 2 (质量加固, 便宜) <─────────┘   [可与 P1 并行]
                                   │
Phase 3 (离线提示优化, opt-in) <───┘   [需 golden 验证集 + 成本预算]
```

| Phase | ROI | 工作量 | 风险 | 默认状态 |
|---|---|---|---|---|
| 0 度量底座 | 极高（解锁其余全部） | 2–3d | 低（纯读） | 直接产报告 |
| 1-A 软删巩固 | 中（O-M6 已覆盖读取期核心；本项补持久化/审计/写入侧） | 1–2d | 低（软删可逆） | opt-in→基线正后默认开 |
| 1-B 反馈环加固 | 高 | 2–3d | 中（改 confidence 路径） | 基线正后默认开 |
| 1-C 修复经验库 | 中高 | 4–6d | 中（新 entry type + 抽取） | opt-in |
| 2 质量加固 | 中（便宜） | 2–3d | 低 | 默认开 |
| 3 离线提示优化 | 上界增益 | 持续 | 高（成本/标注） | opt-in 子命令 |

---

## 5. 量化评估与验收门（回答开放问题1）

**核心指标**（写入 `doc/evaluation/metrics.md`，复用既有口径）

| 指标 | 定义 | 数据源 | 期望方向 |
|---|---|---|---|
| `memory_decision_lift` | (memory=on 决策正确率) − (off) | Phase 0 消融器 | > 0 才算"学到了" |
| `harmful_influence_rate` | 注入记忆导致 fail 的决策占比 | hit_tracker + judge | ↓（P1-A 目标） |
| `correct_after_influence` | 记忆改变的决策中最终 PASS 占比 | hit_tracker + ci_reporter | ↑（P1-B 目标） |
| `repeat_error_repair_rounds` | 同 error_signature 的平均修复轮数（需 P1-C 新增 `summarize_judge_repair_rounds` 按签名聚合，`judge_repair_rounds` 仅为整数计数器） | judge_repair_rounds | ↓（P1-C 目标） |
| `memory_drift_loss` | consolidation 前后 pinned 条目内容差异 | store 单测 | =0（P2-B 目标） |
| `cost_per_decision` | 单决策 token 成本 | CostTracker | 不显著上升 |

**验收流程**
1. 每个 Phase 落地前后，在 forgejo Tier-1 golden（[[reference_forgejo_eval]]）跑 Phase 0 消融器，出对比表。
2. **门槛**：任一反馈环（P1-A/B）默认开启的硬条件 = `memory_decision_lift > 0` 且 `harmful_influence_rate` 不升（原则 P2）。
3. 集成测试（`tests/integration/`，真实 key、不进 CI）复用既有 harness 加"记忆消融"用例。
4. 单测 ≥80% 覆盖每个新 tool。

---

## 6. 风险、失败模式防护与明确非目标

### 6.1 失败模式防护映射

| 研究失败模式 | 本方案防护 |
|---|---|
| F1 摘要漂移 | P2-B pinned 锚定 + OPP-8 目录桶；关键不变量不再被反复摘要 |
| F2 检索污染/无界增长 | 选择性 add（现状）+ O-M6 读取期过滤（现状）+ **P1-A 持久化 suppress（补写入/consolidation 侧）** + MAX_ENTRIES/consolidation（现状）|
| F3 陈旧知识 | upstream_ref 0.5× 衰减（现状，覆盖换基线主场景）；不加裸 recency（见 §6.2）|
| 反思不可信（oracle 陷阱）| 原则 P1：所有写回/经验沉淀只取确定性信号，LLM 不判"成功与否" |
| 反馈噪声跨 run 强化 | min_observations 门槛 + Phase 0 度量把关 + 人工/bootstrap 豁免 |
| 成本失控 | Phase 3 默认不跑、成本透明；离线回放器降评估成本 |

### 6.2 明确非目标（曾被否决或与 ethos 冲突，**不在本方案内**）

- **裸 recency+importance 打分**：round2 已否（默认零行为=未接线反模式，违原则 P4）。F3 的主场景已由 upstream_ref 衰减覆盖。
- **通用跨 run 失效机制**：upstream_ref 0.5× 衰减已覆盖，避免冗余。
- **反向 impact 半径**：与 both_changed 分类 + reverse_impact_scanner 冗余，已否。
- **通用 LLM reflection 层**：round2 已否（默认关 + 自报 provenance 不可信）。本方案的 P1-C 是**执行接地**的经验沉淀，与之本质不同（provenance=确定性 judge/compile）。
- **embedding / 向量检索**：刻意拒绝（lightweight/可复现 ethos，原则 P5）。
- **任何权重微调 / RLHF / SFT**：调研结论支持无微调路线；不做。

### 6.3 实施纪律

- 遵循 [[feedback_dead_code_check]]：每个接入点落地前 `grep -rn` 生产 caller，确认 wiring，不靠"单测绿"判定已接入。
- 遵循 reviewer 只读契约：judge/planner_judge/human_interface 不得新增 `state.<field> =` 写入；所有持久化经 Orchestrator。
- 每个新 entry type / gate 入 `gate_registry` 与对应 contract，过 `tests/unit/test_agent_contracts.py`。
- 小步可评审 PR：Phase 0 单 PR；Phase 1 拆 A/B/C 三 PR；Phase 2 拆 A/B；Phase 3 独立 opt-in PR。

---

## 7. 引用清单（经对抗式核验）

> 调研：5 路并行检索 → 24 源 → 119 断言 → 25 条 3 票核验，**0 条被推翻**；2 条带 2-1 分歧已在文中标注 caveat。

**记忆与综述（范式1、5；失败模式 F1–F3）**
- ACM TOIS 综述 `10.1145/3748302`（arXiv 2404.13501）— 记忆 W/P/R 形式化、自我进化、微调灾难性遗忘/离线局限
- arXiv `2603.07670` — 非参数化可审计记忆为首选（§4.6）；摘要漂移（§4.1）；检索污染（§7.1）
- arXiv `2502.14802`（From RAG to Memory）— 非参数记忆的在线改进
- arXiv `2605.06716` — Storage→Reflection→Experience 三层
- arXiv `2505.16067` — 选择性 add+delete 比裸增长 +10%；全存可能比无记忆更差
- arXiv `2601.04463`（ProMem）— 多 session 保留率/锚定摘要
- preprints `202601.0618` + arXiv `2501.13956`（Zep）— 陈旧知识静默失效、需 temporal-decay/supersession

**反思（范式2）**
- arXiv `2303.11366`（Reflexion, NeurIPS 2023）— 语言反馈强化、episodic buffer
- arXiv `2405.06682`（Renze & Guven）— 反思信息量决定效果，GPT-4 0.79→0.93；含 oracle 上界 caveat

**技能/课程（范式3）**
- arXiv `2305.16291`（Voyager, NeurIPS 2023）— 黑盒查询零微调、可组合技能库、执行接地迭代

**提示/策略优化（范式4）**
- arXiv `2507.19457`（GEPA, ICLR 2026 Oral）— 提示进化超 GRPO 6–20%、rollout 少 35×；含"GRPO≠RLHF"caveat
- DSPy MIPROv2 `https://dspy.ai/api/optimizers/MIPROv2/` + `github.com/stanfordnlp/dspy` — 联合指令+few-shot 贝叶斯优化
- arXiv `2309.16797`（PromptBreeder, DeepMind, NeurIPS 2024）— 自指进化；~$60/1万调用成本 caveat

**无微调 vs 微调取舍（范式5）**
- arXiv `2508.16153`（Memento/AgentFly）— 无梯度 case 记忆，GAIA 87.88% Pass@3、OOD +4.7~9.6%；含 backbone 差异 caveat

**评估方法论（§5）**
- Anthropic `https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents` — agent 评估实践

---

## 附录 A：与既有文档/记忆的关系

- `doc/multi-agent-optimization-from-merge-experience.md`：6 大丢失模式 + ScarListBuilder（git 提交挖掘）。**本方案 P1-C 的运行期 verified-repair 经验库与之互补**（历史坑 vs 运行期解法）。
- `doc/plan/roadmap.md`：本方案不与 P1 per-hunk / seam-anchor 等冲突，是正交的"自学习"维度。
- 记忆 [[project_memory_context_opt_round2]]：OPP-1/4/5/6/8/10 已落地；本方案在 OPP-5 之上做"激活+加固+选择性 delete+度量"，**不重做 OPP-1~10**。
- 记忆 [[feedback_dead_code_check]]：所有接入点落地前先 grep 生产 caller。
- 记忆 [[feedback_judge_readonly_violation]]：reviewer 只读约束贯穿全方案。
