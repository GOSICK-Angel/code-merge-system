# Agent Prompt 工程化审查与优化方案

**日期** 2026-05-28
**范围** 全部 7 个 LLM agent 的 prompt（`src/llm/prompts/*.py`）
**依据** Anthropic 官方《Prompting best practices》(platform.claude.com/docs，覆盖 Opus 4.8/4.7/4.6、Sonnet 4.6、Haiku 4.5)
**结论** 官方文档对本系统有显著参考价值。本系统的 prompt 在「角色设定 / 显式规则 / grounding」上做得好，但在 **XML 结构化、长上下文排序、few-shot 示例、Structured Outputs、Opus 4.8 字面化倾向** 这几条公认高收益技巧上系统性缺位。

---

## 一、官方最佳实践 × 本系统适配性总览

| 最佳实践 | 适用性 | 本系统现状 | 收益 |
|---|---|---|---|
| 角色设定（system role） | ✅ | 7/7 都有 | 已达标 |
| 清晰直接 + 顺序步骤 | ✅ | planner/judge 规则编号清晰 | 基本达标 |
| 解释 WHY（加动机） | ✅ | analyst grounding 做得好，余者弱 | 中 |
| **XML 标签结构化** | ✅✅ | **0/7 使用，全用 markdown `#` + 内插** | **高** |
| **长上下文排序（长内容置顶、query 置底）** | ✅✅ | analyst/judge/executor 把文件内容放中间 | **高（官方称 ≤30%）** |
| **few-shot 示例（3–5，`<example>` 包裹）** | ✅✅ | 仅 analyst 有 1 对正/反例，余 0 | **高** |
| **Structured Outputs / tool schema 出 JSON** | ✅✅ | 全靠 prompt 内插 JSON + best-effort parser | **高（可靠性）** |
| 引用式 grounding（先引证再判断） | ✅ | judge(evidence_excerpt)/analyst 已做 | 已对齐，可推广 |
| **Opus 4.8 字面化（不外推、显式声明范围）** | ✅✅ | revision prompt 依赖「对相似文件套用同样模式」 | **高（潜在 bug）** |
| 正向表述优于负向 | ✅ | executor/analyst 大量 `NEVER/Do NOT` | 中 |
| 代码审查 recall（find 与 filter 分离） | ✅ | judge/planner_judge 带「别过度上报」语气 | 中（需权衡） |
| 迁移离开 prefill | ✅ | 已核实未用 prefill | 已达标 |
| effort / adaptive thinking 调参 | ⚠️ 配置层 | Anthropic 系 agent 无 effort/thinking 配置 | 中 |
| 防幻觉（investigate before answering） | ✅✅ | analyst 强；**executor 写代码却无 grounding** | **高** |
| 前端美学 / computer use / vision / 子代理编排 / 多上下文窗口状态管理 | ❌ N/A | 后端单次调用流水线，不涉及 | — |

---

## 二、问题清单（按优先级）

> 说明：P0/P1 中部分条目（Executor grounding、segment gate、规则去重）来自 2026-05-28 先期 prompt 审查；本节将其与官方文档导出的技巧问题合并统一编号。

### 🔴 P0-1 Executor 语义合并 prompt 缺防幻觉护栏

- **位置** `executor_prompts.py:60 build_semantic_merge_prompt`（调用点 `executor_agent.py:460`）
- **官方依据**「Minimizing hallucinations in agentic coding」+「investigate_before_answering」
- **现状** ConflictAnalyst（`analyst_prompts.py:193-230`）有完整 GROUNDING RULES + `REQUIRES NEW API` + Imported Symbol Surface + Diff Facts；但**真正写出最终合并代码**的 Executor 一项都没有。已 grep 确认 `imported_symbols` 根本未传入 executor。
- **影响** 与 zod 编译失败根因同源——分析层不造假，执行层照样凭空造符号写进文件。
- **修法** 把 symbol surface + 反 hedge 禁令（`if available/should exist`→`REQUIRES NEW API`）下沉到 semantic merge prompt，并将 `imported_symbols` 透传进 executor。

### 🔴 P0-2 segment 审查 prompt 绕过 gate 注册（契约缺口）

- **位置** 生产实际走 `build_segment_plan_review_prompt`（`planner_judge_agent.py:539`），但它**无 gate ID**；只有 `build_plan_review_prompt` 注册成 `PJ-PLAN-REVIEW`。
- **影响** 违反「agents 必须按 gate ID 引用 prompt」架构约束；契约测试覆盖不到实际跑的路径。
- **修法** 补注册 `PJ-PLAN-REVIEW-SEGMENT`，并在 planner_judge 契约 yaml 声明。

### 🔴 P0-3 全部 7 个 prompt 未做 XML 标签结构化

- **官方依据**「Structure prompts with XML tags」「Long context prompting → 用 `<document>` 包裹」
- **现状** 全部用 markdown `# 标题` + f-string 内插文件内容。文件内容里若含 `#`、```` ``` ````、`<<<<<<<` 等，会与 prompt 自身 markdown 边界混淆，增加误解析/注入风险。
- **影响** 三方内容、diff、manifest 与「指令/JSON schema」边界不清晰，Opus 4.8 解析不如 XML 稳。
- **修法** 变量内容统一用描述性标签包裹：`<base_content>` / `<fork_content>` / `<upstream_content>` / `<file_manifest>` / `<judge_issues>` 等；指令置于 `<instructions>`，输出 schema 置于 `<output_format>`。优先改 analyst/judge/executor 这类含大段代码内容的。

### 🔴 P0-4 长上下文排序违反「长内容置顶、query 置底」

- **官方依据**「Put longform data at the top … Queries at the end can improve response quality by up to 30%」
- **现状**
  - `analyst_prompts.py:382 build_conflict_analysis_prompt`：任务说明在顶，三方代码内容夹在中间，JSON schema 在底。
  - `judge_prompts.py:99 build_file_review_prompt`：merged content 在中部，review tasks 在底。
  - executor 同类结构。
- **修法** 把大段三方/merged 内容上移到 prompt 顶部（紧随 system 之后），把「分析任务 + 输出 schema」固定在末尾。这是低风险纯排序调整，收益官方实测显著。

### 🟠 P1-1 缺 few-shot 示例

- **官方依据**「Use examples effectively（3–5 个，`<example>` 包裹）是最可靠的输出对齐手段」
- **现状** 仅 analyst 有 1 对 WRONG/RIGHT rationale 例。planner 分类、judge 评审、conflict 分析的**输出 JSON 全无示例**。
- **修法** 给 planner 分类、analyst、judge 各加 1–3 个 `<example>` 的「输入片段→期望 JSON」对，覆盖典型 + 边界（如 C-class、B-class 偏置、安全文件）。

### 🟠 P1-2 两个审查 prompt 近 50 行规则逐字重复

- **位置** `build_plan_review_prompt`（600-648）与 `build_segment_plan_review_prompt`（701-752）的 Review Tasks 规则 1-7、JSON schema、中文提示完全重复。
- **影响** 改 rule 6 极易只改一处 → 漂移。
- **修法** 抽 `_REVIEW_TASKS_BLOCK` / `_RETURN_SCHEMA_BLOCK` 常量共用。

### 🟠 P1-3 auth 关键词列表副本（⚠️ 前提已修正）

- **原始判断（部分错误）** 以为 `_SAFELIST_RISK_KEYWORDS`（13 项）与两处 rule-6 内联（各 14 项）是同一概念的三副本应合一。
- **核实后** 这是**两个不同机制**：① `_SAFELIST_RISK_KEYWORDS`（含 `verify`）驱动**确定性**「显然安全」短路排除；② rule-6 内联（含 `session`/`user`，无 `verify`）是**面向 LLM** 的 C-class 复核提示。关键词本就该不同，强行合一会双向改变行为。
- **正确修法（已落地 B1）** 只合并那两份**完全相同**的 rule-6 内联 → 抽 `_REVIEW_TASKS_RULES` 常量单一来源；`_SAFELIST_RISK_KEYWORDS` 保持独立并加注释说明差异。

### 🟠 P1-4 Opus 4.8 字面化：revision prompt 依赖外推

- **官方依据**「More literal instruction following … does not silently generalize an instruction from one item to another」
- **位置** `planner_prompts.py:155-158`：`Apply the same reclassification pattern to similar files.`
- **影响** Opus 4.8 倾向只处理被显式列出的 issue，不外推到「相似文件」，截断 50 条后剩余同类文件可能不被重分类。
- **修法** 要么不截断（按文件分批送全量），要么显式列出需套用的文件路径，避免依赖模型外推。

### 🟡 P2-1 Structured Outputs 未启用

- **官方依据**「Migrating away from prefill → 用 Structured Outputs 约束 schema；newer models 可靠匹配复杂 schema」
- **现状** 全部 JSON agent 靠 prompt 内插 JSON + 末尾「Respond with ONLY JSON」+ best-effort parser（`parse_decision_proposals` 等）。仅 OpenAI 侧用了 `response_format=json_object`（`client.py:428`），未用带 schema 的 Structured Outputs。
- **修法** 评估对 planner/planner_judge/analyst/judge/memory/risk 启用 Structured Outputs（或 tool schema），可移除大量「only JSON / no markdown」样板与脆弱解析。属架构级改造，单列评估。

### 🟡 P2-2 JSON-only 强制措辞不统一

| agent | 强制力度 |
|---|---|
| planner_judge | 强（`parsed by json.loads()`） |
| planner eval | 中（`ONLY the JSON object`） |
| **analyst / judge** | **弱（仅 `Return JSON:`）** |

- **修法** 在未迁 Structured Outputs 前，统一为强措辞；analyst/judge 补「无前言、首字符即 JSON」。

### 🟡 P2-3 代码审查 recall：find 与 filter 未分离（⚠️ 前提依赖模型，见 §五）

- **官方依据**「Code review harnesses：Opus 4.8 会更忠实地执行『be conservative / don't nitpick』→ 测得 recall 下降」
- **现状**
  - PlannerJudge system（`planner_judge_prompts.py:323-343`）：`NOT to find as many issues as possible`、`stay silent`、`flagging >20% 就反思`。
  - Judge 审查合并结果，是真正需要高 recall 找缺陷的环节。
- **⚠️ 重要前提更正** 该 recall 下降是 **Opus 4.8 特有**行为。但默认配置下 **PlannerJudge 跑 gpt-5.4（OpenAI，非 Claude）**、**Judge 跑 opus-4-6（非 4.8）**——即官方那条 rationale 当前**前提并不成立**，属前瞻性建议。
- **权衡说明** PlannerJudge 的保守是**刻意校准**（抑制 B-class drift 误报，见 `project_judge_stall_root_cause`），不可盲目调高；对已易过度上报的模型/reasoning 模型套用「全报、下游过滤」会放大误报。建议：仅当 Judge 被配置为 Opus 4.8 时，才按官方「find 阶段只管覆盖、confidence/severity 交下游过滤」改造（Judge 已有 `must_fix_before_merge`/`resolvability`/`confidence` 字段，天然适配）；且须 eval 验证 recall/误报。**不可 blanket 套用。**

### 🟡 P2-4 语言支持只覆盖一半 agent

- **现状** planner / planner_judge 支持 zh 输出；**analyst / judge / executor 永远英文**。
- **影响** 中文 run 下给人看的 Judge issue description / suggested_fix、analyst rationale 仍是英文，UX 割裂。
- **修法** 给 analyst/judge 的面向人类字段加 `lang` 注入（与 planner 同模式）。

### 🟢 P3-1 正向表述优于负向

- **官方依据**「Tell Claude what to do instead of what not to do；正例比负例有效」
- **现状** executor OUTPUT CONTRACT、analyst「Do NOT write boilerplate」大量负向。
- **说明** executor 的负向是 parser 强约束，建议**保留负向 + 补一个正向「合格输出」示例**，而非删负向。

### 🟢 P3-2 Anthropic 系 agent 未配 effort / adaptive thinking

- **官方依据**「coding/agentic 用 xhigh，多步推理至少 high；Opus 4.8 thinking 默认关闭」
- **现状** 仅 OpenAI 模型（planner_judge/executor）配了 `reasoning_effort=medium`；Anthropic 系（planner/conflict_analyst/judge/...）无 effort/thinking 配置。
- **修法** 配置层（非 prompt）：评估给 conflict_analyst / executor / judge 配 `effort=high` + `thinking: adaptive`，配大 `max_tokens`。

### 🟢 P3-3 其余小项

- Judge 内容截断 5000 / batch 2000 字符（`judge_prompts.py:9,191`）：大文件尾部缺陷看不到，与 evidence_excerpt 要求冲突——大文件审查盲区。
- risk_scoring system 过薄（`risk_scoring_prompts.py:4`）：未锚定 0.3/0.6 阈值语义。
- planner 阈值 200/50 硬编码在 prompt 文本，未从 config 取，可能与 `MergeConfig` 漂移。

---

## 三、落地建议（分批，避免大改）

**批次 A（正确性，先做）✅ 已落地（2026-05-28，feat/web，未提交）**
1. ✅ P0-1 Executor grounding 下沉 + 透传 imported_symbols
   - `EXECUTOR_SYSTEM` 增反造符号 grounding（适配「只输出代码、不写 REQUIRES NEW API」），覆盖主/chunk 全部 4 个 LLM 调用点（共用 system）
   - `build_semantic_merge_prompt` 加 `imported_symbols` 参数 + executor 版 Symbol Surface 块（None/空 时零行为变化）
   - `executor_agent.py:460` 安全 harvest（`_safe_harvest_symbols`，复用 `harvest_imports_for_file`，git_tool/None 守卫）+ 透传
   - 单测 `test_executor_prompt_grounding.py`（5 例）
2. ✅ P0-2 注册 `PJ-PLAN-REVIEW-SEGMENT` + 契约声明
   - `gate_registry.py` 注册；`planner_judge.yaml` gates 增列
   - 单测 `test_gate_segment_review.py`（2 例）+ 既有契约测试自动覆盖
   - 验证：2965 单测全绿 / mypy strict 干净 / ruff 干净

**批次 B（结构化重构）**
- ✅ **B1 已落地（2026-05-28，feat/web，未提交）** — P1-2 + 修正后的 P1-3 去重：
  - 抽 `_REVIEW_TASKS_RULES` / `_MISMATCH_NOTE` / `_ZH_LANG_NOTE` 常量 + `_return_schema_block()` helper，两个 review prompt 单一来源
  - `_SAFELIST_RISK_KEYWORDS` 保留独立 + 注释（不同机制，见 P1-3 修正）
  - **黄金快照证明逐字节不变**（en/zh × rr 0/2 × 两函数 共 8 组）；新增 `test_plan_review_shared_blocks.py`（3 例）；2968 单测全绿 / mypy / ruff 干净
- ✅ **B2 已落地（2026-05-29，feat/web，未提交时已 eval）** — P0-3 XML + P0-4 长上下文排序，**仅 Claude 系**：
  - `build_conflict_analysis_prompt`（analyst）：三方内容上移到 `<three_way_content>` 顶部、`<instructions>`/`<output_format>` 置底，全程逐字保留 grounding/schema/semantic_compatibility/symbol surface
  - `build_file_review_prompt`（judge）：`<merged_content>` + Fork Original 上移、schema 置底
  - executor/planner_judge（OpenAI）按 §五 未碰；grounding 块（facts/surface/native）沿用 `#` 头（helper 被 round prompt 共用，不动）
  - **eval（§五 护栏#3）**：因 Anthropic 端点 503，改用 `OPENAI_BASE_URL`+`OPENAI_MODELS`（mimo-v2.5-pro）在真实 zod `iso.ts` 上 A/B：baseline 虚构 `core._isoWeek`（grounding_warnings 命中），NEW 两次有效分析**零真实虚构**且正确用 `required_new_apis` 声明缺口 → **未退化（实为改善）**。注：mimo≠生产 claude，XML 系 Claude 亲和，样本小
  - 2968 单测全绿 / mypy strict / ruff 干净

**批次 C（输出质量）**
- ✅ **C-A类 已落地（2026-05-29，feat/web，未提交）** — 三项 universal-safe（无需 eval）：
  - **P1-4** `planner_prompts.py` revision 截断提示去掉「Apply the same reclassification pattern to similar files」外推依赖 → 显式「只重分类列出的文件，其余截断条目分批处理」
  - **P2-2** analyst（conflict/commit_round）+ judge（file_review/verdict/batch/re_evaluate）的弱 `Return JSON:` 统一为强措辞常量 `_JSON_ONLY_INSTRUCTION`（json.loads 可解析、首字符 `{`、无 markdown/前言）；decision_proposal 本就有闭合强句，保留
  - **P2-4** `build_conflict_analysis_prompt` / `build_file_review_prompt` 加 `lang` 参数 + `_ZH_LANG_NOTE`（zh 时注入 rationale/intent description、issue description/suggested_fix/overall_assessment 用中文）；agent 侧从 `config.output.language` 透传（analyst 两调用点经 `analyze_file`/`_chunked_analyze_file`，judge 经 `review_file`）。**英文 run 逐字节不变**（`default == en` 断言）
  - 新增 `test_prompt_batch_c.py`（7 例）；2987 单测全绿 / mypy strict / ruff 干净；`test_state_thresholds.py` 两处 fake 签名补 `lang`
  - §五分级：三项均 **A类**（措辞/语言注入/去外推），文档 §五 明列为 universal-safe 无需跨模型 eval
- ⏳ **P1-1 few-shot 示例（B类，待 eval）** — 仅 planner 分类 / analyst / judge（Claude 系），executor/planner_judge 保持 zero-shot；须在配置模型上 eval，下一轮处理

**批次 D（架构级，单独评估）**
10. P2-1 Structured Outputs 迁移
11. P2-3 Judge find/filter 分离（需 eval 验证 recall）
- ✅ **12. P3-2 Anthropic extended-thinking 旋钮 已落地（2026-05-29，feat/web，未提交）** — 纯配置层，opt-in，**默认全部关闭**（零行为变更，无需 eval）：
  - `AgentLLMConfig.thinking_budget_tokens: int | None`（默认 None）+ model_validator（budget ≥1024 且 < max_tokens）
  - `AnthropicClient` 接 `thinking_budget_tokens`:set 时 `messages.create` 注入 `thinking={"type":"enabled","budget_tokens":N}` 并**强制 temperature=1.0**（Anthropic 约束）;None 时请求逐字段不变（`temperature` 不动、无 `thinking` 键）
  - `LLMClientFactory` anthropic 分支透传 `config.thinking_budget_tokens`
  - **OpenAI 侧已无缺口**:`reasoning_effort` 本就是自由 `str` 字段（可配 `high`/`xhigh`），executor/planner_judge 默认 medium 不动（改默认=成本/行为变更，须 eval）
  - 新增 `test_anthropic_thinking.py`（8 例:校验/请求形状/工厂透传/默认全关）;2995 单测 / mypy strict / ruff 干净
  - `_extract_anthropic_text` 早已跳过 thinking block，输出抽取无需改

> 每批次后跑 `pytest tests/unit/`（契约测试 + 80% 覆盖）+ 在 forgejo / zod 上做一次真实 run 对照 rationale 质量不退化（见 `feedback_verify_real_forgejo` / `feedback_verify_real_run`）。Structured Outputs 与阈值调整须先核对 `doc/evaluation/`。

## 四、验收

- [x] 批次 A：executor 单测覆盖 grounding 引导；`grep "PJ-PLAN-REVIEW-SEGMENT" src/llm/prompts/gate_registry.py` 命中；契约测试绿（2026-05-28）
- [x] 批次 A 接线 live 验证（2026-05-28，真实 zod 仓库，零 API 成本）：跑 executor 同一对生产函数 `_safe_harvest_symbols`+`build_semantic_merge_prompt` 喂真实 git 三方内容；`classic/iso.ts`→`./schemas.js`(257 exports)、`classic/schemas.ts`→含 `./iso.js`(15 exports，正是当年断编译区域) 均渲染入 prompt，grounding 就位。forgejo(Go) 无法验（harvester 仅 TS/JS）
- [ ] 批次 A 全链路（可选，需 API $）：真实 LLM `merge --ci` 跑一遍看 executor 产物/rationale 不退化（注：zod 产物有已知无关编译缺陷，merge 质量信号偏噪，见 `project_zod_eval_and_batch1_fixes`）
- [x] 批次 B1（2026-05-28）：review prompt 去重，黄金快照逐字节不变（8 组）+ `test_plan_review_shared_blocks.py` 绿；2968 单测 / mypy / ruff 干净
- [x] 批次 B2（2026-05-29）：analyst/judge XML+排序重构；单测/mypy/ruff 全绿；zod iso.ts A/B eval（mimo）证 grounding 未退化（baseline 虚构 core._isoWeek，NEW 零真实虚构）
- [x] 批次 C-A类（2026-05-29）：P1-4 去外推 + P2-2 强 JSON 措辞 + P2-4 zh 语言注入；`test_prompt_batch_c.py`（7 例）绿；英文 run 逐字节不变；2987 单测 / mypy / ruff 干净
- [ ] 批次 C-B类（P1-1 few-shot）：需配置模型 eval（Claude 系 only）
- [x] 批次 D·P3-2（2026-05-29）：Anthropic extended-thinking 可配置旋钮（默认全关，opt-in）+ 完整 client 接线；`test_anthropic_thinking.py`（8 例）绿；2995 单测 / mypy / ruff 干净；OpenAI effort 本就可配，不动默认
- [ ] follow-up：harvester barrel/re-export（`export * from` / `export {x} from`）抓不到 → 0 exports 误导 grounding（analyst 侧同存，非批次 A 引入）
- [ ] 批次 B：规则/关键词单一来源（`grep` 副本数=1）；prompt snapshot 测（若有）更新
- [ ] 全程 mypy / ruff / pytest 全绿
- [ ] forgejo + zod 各一次 `merge --ci` 对照，rationale/分类质量不退化

## 五、模型适用性分级（⚠️ 必读：prompt 跨模型共享）

本系统 per-agent 可配不同 provider/model，prompt 为**共享文本**（除 `lang` 外无 per-model 分支）。默认配置：

| Agent | Provider | 默认 model | 备注 |
|---|---|---|---|
| planner | anthropic | claude-opus-4-6 | Claude 系 |
| **planner_judge** | **openai** | **gpt-5.4**（reasoning, effort=medium） | **非 Claude** |
| conflict_analyst | anthropic | claude-opus-4-6 | Claude 系 |
| **executor** | **openai** | **gpt-5.4**（reasoning, temp=0.1） | **非 Claude** |
| judge | anthropic | claude-opus-4-6 | Claude 系 |
| human_interface | anthropic | claude-haiku-4-5 | Claude 系 |
| memory_extractor | anthropic | claude-haiku-4-5 | Claude 系 |

**注意：默认无任何 Opus 4.8。** 官方「Prompting Opus 4.8」专章（response length / effort 严格遵守 / 极致字面化）当前属前瞻；4.x 家族通用特性（轻度字面化、XML 亲和、adaptive thinking@4.6+）适用于 Claude 系 agent。

### 分级原则

**A 类·通用安全（任何 provider/版本都可放心写进共享 prompt）**
P0-1 executor grounding、P0-2 segment gate、P0-4 长上下文排序、P1-2/P1-3 去重、P1-4 显式范围、P2-2 JSON-only 措辞、P2-4 语言注入。

**B 类·provider 特化（Claude 系受益，OpenAI 系慎用/反伤）**
- **P0-3 XML 标签**：Claude 训练有 XML 亲和；gpt-5.4（executor/planner_judge）更吃 markdown/JSON。→ 仅对 planner/conflict_analyst/judge/human_interface/memory_extractor 做；executor/planner_judge **不** XML 化。
- **P1-1 few-shot**：Claude 受益；gpt-5.4 是 reasoning 模型，OpenAI 建议 reasoning 模型少用/不用 few-shot。→ executor/planner_judge **保持 zero-shot**。

**C 类·版本特化（4.8 特有，当前默认不命中，且 blanket 套用有害）**
- **P2-3 find/filter recall**：仅当 Judge 被显式配成 Opus 4.8 才考虑，且须 eval；对 reasoning/易误报模型有害。
- **P3-2 effort/adaptive thinking**：Anthropic 4.6+ 与 OpenAI `reasoning_effort` API 不同，纯配置层按模型分别处理。✅ 已落地为 opt-in 旋钮（`thinking_budget_tokens`，默认全关）;OpenAI `reasoning_effort` 本就可配。默认值不变，故无需 eval;若团队 opt-in 开 thinking 或调 effort=high/xhigh，须自行 eval 成本/质量。

### 实施护栏

1. 批次 B 改动**只动 Claude 系 agent 的 prompt**，executor/planner_judge 的 prompt 不加 XML/few-shot。
2. 若未来要对单一模型深度调优，应引入 **per-model prompt 分支**（类似现有 `lang` 注入）或走 config，**不要**把模型特化文本固化进共享 prompt。
3. 任何 B/C 类改动，在**实际配置的模型**上跑 eval（forgejo/zod）对照，禁止「假设 4.8」直接上线。

## 六、上下文链接

- 先期 prompt 审查对话：2026-05-28
- 官方文档：platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices
- 关联记忆：`project_zod_eval_and_batch1_fixes`（grounding/编译失败）、`project_judge_stall_root_cause`（PlannerJudge 保守校准缘由）、`feedback_verify_real_forgejo`
- 相关源码：`src/llm/prompts/`（7 文件）、`src/llm/prompts/gate_registry.py`、`src/agents/{executor,planner_judge}_agent.py`、`src/models/config.py`（effort 配置位）
