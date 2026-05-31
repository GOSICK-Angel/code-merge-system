# 文件依赖图全局优化方案

> 综合 [Graphify](./graphify-analysis.md) 与 [Understand-Anything](https://github.com/Lum1104/Understand-Anything) 两个开源项目，
> 把当前半死的依赖图机制升级为**全局共享、多 agent 消费**的一等资产。
>
> **状态（2026-05-26 更新）**：**Phase A 已落地**（提交 `58600ac`，2026-05-25）——多语言提取 + initialize 构建 +
> planner topo/fanout + judge EXTRACTED 漏改 + config 开关，§0 反死代码 DoD 四条均满足。**Phase A+（计划外）**：
> tree-sitter 实装后 edge `target_symbol` 已填充,新增「依赖图符号 → staging relevance」消费方(提交 `d1aa956`)。
> **Phase B 已落地**（2026-05-26）——conflict_analyst blast-radius/God Node + executor dependents_of + planner_judge
> topo 违规 precheck（单测 +16）。**Phase C 已落地**（2026-05-26）——import 别名/monorepo 解析（opt-in）+ stdlib
> label-propagation 社区检测（opt-in，未引 Leiden 重依赖）+ God Node planner 风险 + memory_extractor hub/surprising
> 沉淀 + human_interface blast-radius 决策卡（单测 +19，2724 passed）。**全部三阶段完成**。进度详见 §7 标记与
> `doc/execute/implementation-notes.md` §7/§8。**下一步：实测验证**——§10 列预计收益（消费方 × 指标），
> §11 给 A/B 消融验证方案（`dependency_graph.enabled` 开关消融、对照 Ground Truth 差分）。原始日期：2026-05-25。

---

## 0. 为什么写这份文档：上一轮提案为何变成死代码

`graphify-analysis.md` §11 提出了三个启发（文件依赖图、冲突波及范围、置信度分级），
落地为两段代码：

- `src/models/dependency.py` —— `DependencyEdge` / `FileDependencyGraph`，含
  `dependents_of` / `dependencies_of` / `topological_order` / `impact_radius`，
  以及 `ConfidenceLabel`（EXTRACTED / INFERRED / AMBIGUOUS）。**API 完整。**
- `src/tools/dependency_extractor.py` —— 两阶段 import 解析（module index + `ast.parse`）。

但它**死了**，原因有三，本方案必须逐条规避：

1. **只建模型不接线**：`state.dependency_graph` 是 `default_factory` 空图，
   生产代码里**没有任何地方调用 `DependencyExtractor` 去填充它**（grep 0 命中）。
2. **没进任何 agent 契约**：`planner.yaml` / 其它契约的 `inputs` 都没有
   `dependency_graph`。即使填充了，agent 用 `self.restricted_view(state)` 读它也会
   抛 `FieldNotInContract`（与 `project_judge_dead_contract_checks` 同一个坑）。
3. **能力太弱、无人愿用**：`dependency_extractor` 只支持 Python（`ast.parse`，仅 `.py`），
   而本系统要 target-repo 无关、多语言。一个只覆盖 Python 的提取器对 Go/TS fork 毫无价值。

> **反死代码铁律（本方案每个组件的 Definition of Done）**：
> 任何依赖图组件交付时必须同时满足 ——
> (a) 在某个 phase 中被显式构建/填充；
> (b) 至少一个 agent 契约 `inputs` 声明了它；
> (c) 该 agent 的 gate prompt 或确定性逻辑真正读取它；
> (d) 有单测断言"图非空时行为改变"。
> 缺任何一条 = 又一段死代码，不予合入。

---

## 1. 现状盘点（2026-05-25 grep 核实）

| 部件 | 位置 | 状态 |
|---|---|---|
| `FileDependencyGraph`（topo / impact_radius / dependents_of） | `src/models/dependency.py` | 存在，**frozen 不可变**，原语齐全 |
| `DependencyExtractor`（两阶段 import） | `src/tools/dependency_extractor.py` | **死代码**：Python-only + 生产零调用 |
| `state.dependency_graph` | `src/models/state.py:244` | 永远是空默认值 |
| `ReverseImpactScanner` | `src/tools/reverse_impact_scanner.py` | **活的**：`initialize.py:609` 构建 → `state.reverse_impacts` → judge 消费 |
| tree-sitter 运行时 | `src/llm/chunker.py:77+` | 已 vendored：py/js/ts/tsx/go/rust/java/c 文法已加载 |
| planner 耦合信号 | `planner_agent.py:1344 _compute_fanout_map` | 仅"同模块兄弟数-1"**路径代理**，非真实边 |

**结论**：原语（模型）和底座（tree-sitter）都在，缺的是**多语言提取 + 构建接线 + 契约消费**。
`reverse_impacts` 是现成的"建一次多处消费"范例，但它是 regex 文本 grep（自承"不解析 import/namespace"），
且只喂 judge。依赖图是它的**精确化 + 全局化**版本。

---

## 2. 两个项目的取舍

| 维度 | Graphify | Understand-Anything | 本方案采纳 |
|---|---|---|---|
| 提取引擎 | tree-sitter，16 语言，策略模式按扩展名路由 | tree-sitter + 非代码 parser，importMap 预解析 | ✅ tree-sitter 多语言；复用 chunker.py 已装文法 |
| import 解析 | 两阶段（全局实体映射 → 类级边） | tsconfig 别名 / go.mod / monorepo workspace / nearest-config | ✅ 两阶段 + 别名/monorepo 解析（按需，见 §6） |
| 置信度 | EXTRACTED / INFERRED / AMBIGUOUS + 权重 | （隐式，edge.weight 0-1） | ✅ 复用已有 `ConfidenceLabel`，用于风险单调性门控 |
| 波及分析 | BFS impact_radius | understand-diff：1-hop 边 = affected components；风险=f(complexity, 跨层边数, blast radius) | ✅ `impact_radius` 已实现；采纳风险公式形态 |
| 模块边界 | Leiden 社区检测（拓扑驱动，可解释） | 架构分层（LLM 标 layer） | ⚠️ 可选增强（§6.4），替代路径 `infer_modules` |
| 坏味道 | God Node + Surprising Connection | — | ✅ God Node→风险提升；surprising→冲突标记 |
| 增量 | SHA256 内容哈希缓存 + post-commit 钩子 | fingerprint 增量 | ⚠️ 合并是一次性，靠 checkpoint 持久化即可（§6.5） |
| 成本 | 核心管道零 LLM | tree-sitter 确定 + LLM 补语义 | ✅ 确定性边零 LLM；AMBIGUOUS 边可选 LLM 补判 |
| 查询接口 | MCP Server 7 工具 | dashboard 可视化 | ❌ 本系统不需要外部查询面，图是内部 state |

**一句话**：拿 Graphify 的**确定性提取管线 + 置信度 + 社区/God Node 分析**，
拿 UA 的**多语言 import 解析 + blast-radius 风险公式**，丢掉两者的可视化/查询外壳——
本系统只需要一个 state 内的图供 agent 内部消费。

---

## 3. 核心理念：建一次，全局消费

依赖图不是 planner 的私有工具，而是一个**跨 phase 共享的只读资产**，
与 `reverse_impacts` 走完全相同的生命周期：

```
INITIALIZING
  └─ _build_dependency_graph()  ← 紧挨 _run_reverse_impact (initialize.py:609)
       └─ state.dependency_graph = FileDependencyGraph(edges=..., file_count=...)
            │  （frozen，随 checkpoint 持久化，resume 自动恢复）
            ▼
   ┌────────────────────────── 只读消费者 ──────────────────────────┐
PLANNING        planner        topo 排序 batch + impact_radius → risk
PLAN_REVIEWING  planner_judge  校验合并顺序不违反 topo
ANALYZING_…     conflict_analyst  冲突 hunk 的 blast radius → 谨慎度
AUTO_MERGING    executor       删除/改签名前查 dependents
JUDGE_REVIEWING judge          实边 impact_radius 增强 reverse_impacts 检查
GENERATING_…    （报告）        附依赖摘要 / God Node 列表
```

构建点唯一、消费者多个、图不可变——天然契合现有 `ReadOnlyStateView` + 契约 inputs 模型。

---

## 4. 全局 agent 评估（本方案核心）

按 **价值 × 接入成本** 排优先级。每个条目标注消费方式与所需契约改动。

| Agent | 怎么用依赖图 | 价值 | 接入难度 | 优先级 |
|---|---|---|---|---|
| **planner** | (1) `topological_order` 决定 batch 顺序（基类先于子类，graphify §11.1 的原始诉求）；(2) `len(impact_radius(f))` 作为 `compute_complexity` 的 `fanout` 维度，替代模块大小代理 | 高 | 低：`compute_complexity` 已有 fanout 维度；只需 planner.yaml 加 input + 改 `_compute_fanout_map` | **P0** |
| **judge** | 接口变更后用 `impact_radius` 找**真实未更新的 dependents**，精化现有 `_check_reverse_impacts`（judge_agent.py:821，目前是文本 grep）。EXTRACTED 边的漏改 = 硬 issue | 高 | 低：judge 已消费 `reverse_impacts` 且**已在 contract**；加 `dependency_graph` input 即可 | **P0** |
| **conflict_analyst** | 冲突 hunk 所在文件的 `impact_radius` 命中 God Node / 大爆炸半径 → 在 ConflictAnalysis 里提升谨慎度、要求更保守的解法 | 中高 | 中：加 input + CA gate prompt 注入 blast-radius 摘要 | P1 |
| **executor** | E-DELETION：删符号/文件前查 `dependents_of`，fork-only 仍依赖则不安全（比 sentinel 文本更精确）；E-SEMANTIC-MERGE：改签名前查下游 `uses_type`/`calls` | 中 | 中：加 input + 与 sentinel_hits 协同，gate prompt 注入 | P1 |
| **planner_judge** | 校验 plan 的 batch 顺序未违反 topo（子类先于基类应判 issue） | 中 | 低：加 input + PJ-PLAN-REVIEW 增一条确定性检查 | P2 |
| **memory_extractor** | God Node / surprising connection 沉淀为持久 memory（"此 hub 文件历史多次冲突，谨慎"） | 低中（投机） | 中 | P3 |
| **human_interface** | 决策卡展示"此文件有 N 个 dependents / 爆炸半径 = M"，辅助人工判断 | 低（纯展示） | 低 | P3 |

**全局洞察**：P0 两个消费者（planner 排序+风险、judge 漏改检查）覆盖了"合并前定序"和"合并后验证"
两端，且**接入成本最低**（judge 已有消费模式、planner 已有 fanout 维度）。先做 P0 即可让图从死变活、产生可测量收益，再渐进扩展 P1/P2。

---

## 5. 风险单调性：置信度门控（守卫原则）

依赖图只能**抬高**风险/谨慎度，**绝不**因"图里没找到边"而把风险拉低到规则地板线以下。
与 `语义合并保真守卫` 和 `cvte routing`（risk hint 提升而非强制）一致。

| 置信度 | 来源 | 可触发的行为 |
|---|---|---|
| `EXTRACTED` | AST 确定：`imports` / `inherits` / 注解 `uses_type` | 硬抬风险、强制 topo 顺序、judge 漏改硬 issue |
| `INFERRED` | 调用图推断 `calls` | 软提示：risk hint +δ，进 prompt 供参考，不阻断 |
| `AMBIGUOUS` | 动态调用 / 反射 / getattr | 仅记录，最多进 human_interface 展示，不影响自动决策 |

实现要点：风险叠加用 `max(rule_score, graph_hint)` 或加性 clamp，**不可用乘法把 0 边乘没**。

---

## 6. 关键设计决策

### 6.1 子图作用域：不建全仓图
合并是一次性、聚焦改动文件的操作。构建范围 = **改动文件 ∪ 其 N-hop 邻居**，
而非整库解析。改动文件来自 diff；反向边（谁 import 了改动文件）的扫描作用域
**复用 `ReverseImpactScanner._resolve_scope`**（fork-only + customization + extra_globs），
避免重复造扫描逻辑、也避免全仓 AST 的分钟级耗时（graphify 缺点之一）。

### 6.2 多语言提取：复用已装的 tree-sitter
`dependency_extractor.py` 从 Python-only 升级为 tree-sitter 驱动，文法已在
`chunker.py:150+` 注册（py/js/ts/tsx/go/rust/java/c）。按 graphify 的策略模式
按扩展名路由 per-language 提取函数；缺绑定时**优雅降级返回空**（不崩溃）。

### 6.3 import → 路径解析：分层启用
- 基础：相对 import + 两阶段实体映射（graphify 思路，已有雏形）。
- 增强（按需、可配置）：tsconfig `paths` 别名、go.mod module、monorepo workspace
  的 nearest-config 解析（UA `extract-import-map.mjs` 蓝本）。
  **默认关闭**，仅当目标 repo 命中对应生态再启用——保持 target-repo 无关。

### 6.4 社区检测 / God Node：可选增强
Leiden 社区可替代 planner 当前的路径式 `infer_modules`（更贴合真实调用边界）。
God Node（高 degree 节点）→ 命中的改动文件风险提升。
**列为 P2 可选**，因引入 `graspologic`/`networkx` 依赖，需权衡 target-repo 无关下的体积成本。

### 6.5 持久化：靠 checkpoint，不引缓存层
图是 frozen 模型、随 `state` 进单一 rolling checkpoint，resume 自动恢复。
合并一次性，不需要 graphify 的 SHA256 跨运行缓存 + post-commit 钩子——那是为长期重复分析设计的。

---

## 7. 分阶段实施

> 每阶段结尾必须通过 §0 的反死代码 DoD 四条。

### Phase A — 复活 + 接通最小闭环（P0，最高杠杆）✅ 已完成（提交 `58600ac`，2026-05-25）
1. ✅ tree-sitter 化提取，子图作用域（§6.1）。落地于 `src/tools/dep_extractors/`
   （`python_extractor`=stdlib ast、`treesitter_extractor`=js/ts/tsx/go，缺绑定优雅降级）。
2. ✅ `initialize.py`：`_build_dependency_graph()`（定义 :1317，调用 :615 紧挨 `_run_reverse_impact`），
   受 config 开关控制（`DependencyGraphConfig`，config.py:683）。
3. ✅ `planner.yaml` 加 `dependency_graph`；`_compute_fanout_map`（planner_agent.py:1384）改用
   `len(impact_radius(f))` 归一化喂 `compute_complexity` fanout。
4. ✅ planner batch 排序接 `topological_order`（planner_agent.py:233）。
5. ✅ `judge.yaml` 加 `dependency_graph`；`_check_dependency_graph_impacts`（judge_agent.py:876）
   用 EXTRACTED 边报漏改硬 issue。
6. ✅ 单测：`test_dependency_graph_phase` / `test_dependency_graph_consumers` / `test_dep_extractors`。

### Phase A+ — 计划外新增：依赖图符号 → staging 相关性 ✅（提交 `d1aa956`，2026-05-26）
> 原 §4 表格未列此消费方。tree-sitter 实装后，edge 的 `target_symbol` 被真正填充
> （Python `ImportFrom` 按 alias 多重边、JS/TS named import 标识符），首次让依赖图的**符号粒度**可用，
> 由此接通 LLM 上下文压缩（staging）的 relevance 评分——把审大文件时「展开真正相关代码块」落到依赖信号上。
- ✅ 提取器填 `target_symbol`；`FileDependencyGraph.referenced_symbols(fp)` 汇总入边符号。
- ✅ judge / executor / conflict_analyst 的 `build_staged_content` 用 `referenced_symbols` 喂
  `relevance._reference_score`：文件中被其他文件 import 的公共符号，即使不在 diff 内也保 SIGNATURE+，不被压缩丢弃。
- 关联：同轮还接通了 relevance 的 `security` / `conflict` 维度（详见 `doc/execute/implementation-notes.md` §6）。

### Phase B — 冲突与执行消费（P1）✅ 已完成（2026-05-26）
7. ✅ conflict_analyst：blast-radius / God Node 经 `enriched_context` 注入 CA-THREE-WAY（`DependencyImpactHint` +
   `FileDependencyGraph.impact_hint`；config `god_node_min_dependents` 默认 8）。**未改 analyst_prompts 签名**——
   走 enriched_context 一处注入覆盖 chunked/非 chunked 两路。
8. ✅ executor：`build_semantic_merge_prompt` / `build_deletion_analysis_prompt` 加「Downstream Dependents」段
   （`dependents_of` 驱动）；`execute_semantic_merge` 传 dependents+referenced_symbols 保接口；`analyze_deletion`
   删前查 dependents、与 `sentinel_hits` 协同写入 `UserDecisionItem.risk_context`。
9. ✅ planner_judge：`planner_judge.yaml` +`dependency_graph`（唯一需改 contract，因走 restricted_view）；
   新增确定性 `precheck_batch_topological_order`（仅 EXTRACTED 边、扁平执行序倒置、`issue_type=batch_ordering`、
   `suggested==current` 不触发重分类、封顶 25）；`run`/`PlanReviewPhase`/dispute 三处 `review_plan` 均透传 graph。
> 注：conflict_analyst / executor 经 phase 参数 / 原始 state 访问图（未经 `restricted_view`），**无需扩二者 contract**；
> 仅 planner_judge 因 restricted_view 须声明。详见 `doc/execute/implementation-notes.md` §7。
> 已知近似/局限：blast-radius 为文件级（非 hunk 级）；God Node 阈值未标定；topo 封顶 25 为噪音上限。

### Phase C — 增强（P2/P3）✅ 已完成（2026-05-26）
10. ✅ import 别名/monorepo 解析（§6.3）：新 `alias_resolver`（tsconfig paths/baseUrl + go.mod module +
    package.json workspace 名），`dependency_graph.resolve_aliases` 默认关、可配；treesitter resolver 接 alias_map。
    ✅ 社区检测（§6.4）：**未引 Leiden/graspologic（用户确认避免重依赖）**，改用纯 stdlib label-propagation
    `infer_communities`，经 `module_config.mode: "graph"` opt-in（默认 "auto" 不变），图空降级路径式。
    ✅ God Node 风险（§6.4）：`planner._apply_god_node_risk` 在风险评分后抬 God Node 改动文件的 risk_score
    （`god_node_risk_bump` 默认 0.15，§5 只抬不降）并重分类。
11. ✅ memory_extractor：确定性 `_graph_insights` 沉淀 God Node（CODEBASE_INSIGHT）+ 跨目录耦合（RELATIONSHIP，
    surprising-connection 代理），`confidence_level=EXTRACTED`，与 LLM 洞察共享 per-phase 预算；契约加
    `dependency_graph`+`file_categories`。
    ✅ human_interface：`HumanDecisionRequest` 加 `dependents_count/blast_radius/is_god_node`，在决策卡构建期
    （conflict_analysis / auto_merge）预填并把 blast-radius 摘要写进 `context_summary`。
> 依赖取舍：社区检测用 stdlib label-propagation 而非 Leiden（零新依赖、target-repo 无关）。
> 别名解析 + 社区检测均 **opt-in 默认关/auto**，图空/未开 → 行为逐字节不变。详见 `implementation-notes.md` §8。

---

## 8. 风险 / 约束 / 非目标

**约束**
- target-repo 无关：语言驱动的提取天然通用；别名/monorepo 解析必须可配置、默认中性。
- mypy strict、async、不可变：`FileDependencyGraph` 已 frozen，新提取器返回新对象。
- 文件 ≤800 行：多语言提取按 per-language 模块拆分（`src/tools/dep_extractors/`）。
- 契约：每个消费 agent 必须在 yaml `inputs` 显式声明，否则 `FieldNotInContract`。

**风险**
- 动态语言（反射/字符串调用）边不准 → 一律 `AMBIGUOUS`，不进自动决策（§5）。
- 大型 fork 子图仍可能偏大 → `max_depth` + 作用域上限 + degree 截断（借 graphify token 预算思路）。
- 误报漏改 issue 拖慢 judge 收敛 → 仅 EXTRACTED 边升硬 issue，INFERRED 进 prompt 参考。

**非目标**
- 不做 dashboard / MCP 查询接口（图是内部 state，无外部消费方）。
- 不做跨运行 SHA256 缓存（一次性合并，checkpoint 足够）。
- 不替换 `reverse_impacts`——二者共存：图是精确层，文本 grep 是召回兜底。

---

## 9. 测试策略

- 单元：per-language 提取器对 fixture 的 edges/confidence 断言；`topological_order` 在
  base→subclass 场景的定序；`impact_radius` 的 max_depth 边界。
- 契约：`tests/unit/test_agent_contracts.py` 验证新 input 已声明、gate 已注册。
- 反死代码回归：**断言图非空 ⇒ 至少一个 agent 的输出/排序/issue 集合发生变化**
  （直接守护 §0 铁律，防止再次退化为死代码）。
- 集成（本地真实 forgejo，见 `feedback_verify_real_forgejo`）：在含跨文件签名分裂的
  C-class 样本上验证 topo 定序与 judge 漏改检测的端到端效果。

---

## 10. 预计带来的收益（按消费方 × 指标）

> 收益方向一律用 `doc/evaluation/metrics.md` 既有指标口径表述。依赖图遵守 §5 风险单调性——
> 只能**抬高**正确性/谨慎度，理论上不引入新错合（`WMR=0` 是硬约束，不可被任何消费方破坏）。
> 下表是**假设（hypothesis）**，方向与机制明确，幅度待 §11 实测证实/证伪。

| 消费方 | 阶段 | 机制 | 主要受益指标（方向） | 需监控的代价 |
|---|---|---|---|---|
| planner topo 排序 | A4 | 基类先于子类的 batch 序 → 多文件语义合并中间态不破 | `CRA`↑、多文件子集 `OA`↑；并可能减少 topo 争议 → `plan_revision_rounds`↓ | — |
| planner fanout 维度 | A3 | 真实 `impact_radius` 扇出喂 `compute_complexity` → 风险标定更准 | Under-escalation↓ | — |
| planner God Node 抬分 | C1 | hub 文件 `+god_node_risk_bump` → 合理升级 | Under-escalation↓、该子集 `WMR`↓ | Over-escalation（守 ≤15%） |
| judge 漏改硬 issue | A5 | EXTRACTED 边查接口变更后**真实未更新的 dependents** | **`MMR`↓（核心杠杆）**、`Recall_Mi`↑（接口/签名类）、`JA`↑ | judge/修订轮数 → cost、wall_time |
| conflict_analyst blast-radius | B7 | 高爆炸半径/God Node 冲突 → 更保守解法 | `CRA`↑、AUTO_RISKY 子集 `WMR`↓ | — |
| executor 删除守卫 | B8 | 删符号前查 `dependents_of`，fork-only 仍依赖则不删 | **`WDR`↓** | — |
| executor 签名感知 | B8 | 改签名前查下游 caller → 保接口 | `CRA`↑、`MMR`↓ | — |
| planner_judge topo precheck | B9 | batch 逆序判 `batch_ordering` issue → 修订收敛到合法序 | Plan Dispute Precision↑ | `plan_revision_rounds` P95（topo 噪音） |
| 符号 → staging relevance | A+ | 被外部 import 的公共符号即使不在 diff 也保留进 staged content → reviewer 上下文更全 | 减少截断致误判（间接 `OA`/`CRA`↑） | — |
| memory_extractor hub/surprising | C4 | 跨运行沉淀 God Node/跨目录耦合 | 投机，单次 eval 不可测；可能跳过部分 LLM insight（cost 微降） | — |
| human_interface 决策卡 | C5 | 展示 dependents 数/爆炸半径 | `human_minutes_per_run`↓、人工决策质量↑（主观） | — |

**成本与稳健侧净效应（需实测）**：

- 边提取零 LLM（§2），构建本身近乎**成本中性**；
- judge 漏改硬 issue + planner_judge topo issue 可能**增加修订/复检轮** → `cost_usd_per_run` / `wall_time` 上行，
  硬约束 `cost_p95 ≤ baseline×1.15`、`wall_time_p95 ≤ ×1.20`（acceptance.md §2）；
- C4 graph insights 走确定性，可**跳过**部分 LLM insight 调用 → 成本微降；
- 图构建确定性（temperature 无关）→ `DET` 应持平或上行。

---

## 11. 验证方式：实际代码合并（A/B 消融）

核心方法 = **同评估集、单一开关消融**。所有消费方由总闸 `dependency_graph.enabled` + 三个 opt-in 子开关
（`module_config.mode: graph`、`dependency_graph.resolve_aliases`、`dependency_graph.god_node_*`）控制，且实现保证
**「图空/未开 → 行为逐字节不变」**（见 §7.3 / §8.5 安全降级、各 DoD (d)）。这使开关成为**干净的消融杠杆**：
关 vs 开跑同一数据集，指标差值即依赖图净贡献。

### 11.1 实验矩阵

| Arm | 配置 | 用途 |
|---|---|---|
| **Control** | `dependency_graph.enabled: false` | 基线（= 当前主分支行为） |
| **Treatment-core** | `enabled: true`，子开关默认（`mode: auto`、`resolve_aliases: false`） | A / B / 部分 C 的净收益 |
| **Treatment-full** | `+ module_config.mode: graph` `+ resolve_aliases: true`（仅生态命中的 repo） | C2/C3 增量收益 |

**逐消费方归因（可选）**：在 Treatment 基础上单独回退某子开关定位贡献者——
`god_node_risk_bump: 0`（关 C1）、`mode: auto`（关 C2）、`resolve_aliases: false`（关 C3），对比指标位移。

### 11.2 数据集与样本

- **真实 forgejo**（`feedback_verify_real_forgejo` / `reference_forgejo_eval`）：fork=`test/fork`、upstream=`origin/forgejo`、
  base=`160377405c`；3 个 C-class + Tier-1 样本 `t1-0031..0033`（golden 在分支 `eval/golden-forgejo-auth`）。
- **C-class 跨文件签名分裂样本**（§9 集成项）：依赖图最对口的场景——上游改基类/接口签名、下游 fork 文件未跟随，
  验证 judge 漏改硬 issue + executor 下游感知 + planner topo 定序的端到端效果。
- 如有 dify fork 长跨度样本（`project_dify_plugins`）：补 Tier-2 真实复杂度口径。
- 运行前先加载密钥：`merge validate` 不自动读 `.merge/.env`（`feedback_validate_env`），需先
  `set -a && source <repo>/.merge/.env && set +a`。

### 11.3 度量与采集

按 `metrics.md` 口径对 Ground Truth 差分，逐指标记录 Control vs Treatment：

- **聚焦指标**（依赖图直接作用）：`MMR`（行级）、`WDR`、`CRA`、`Recall_Mi`（接口/签名类）、`JA`、Under/Over-escalation。
- **守门指标**（不可回退）：`WMR=0`、`SSER=100%`、`DCRR=100%`、`SRSR=100%`；
  `cost_p95 ≤ baseline×1.15`、`wall_time_p95 ≤ ×1.20`、`plan_revision_rounds P95 ≤ max-1`、`DET ≥ 90%`。

**消费方触发计数**（确认「图非空真的改变了输出」= DoD (d) 的**集成级**证据，而非仅单测）——
从 `checkpoint.json` / 三份报告中抽取：

- judge：dependency-graph 漏改 issue 条数；
- planner：因 God Node 重分类的文件数、batch 序与 Control 的差异数；
- planner_judge：`issue_type=batch_ordering` 条数；
- conflict_analyst：注入 blast-radius 块的文件数；
- executor：因 dependents 改变删除/合并决策的文件数；
- human_interface：决策卡 blast-radius 摘要出现数。

任一消费方在全集上触发计数为 0 → 该消费方在本数据集「形同死代码」，需换样本或核查接线（呼应 `feedback_dead_code_check`）。

### 11.4 判定标准

- **通过（净收益成立）**：聚焦指标中至少 `MMR` / `CRA` / `WDR` 之一有可测改善，其余聚焦指标无显著回退，
  且所有守门指标维持硬阈值。
- **可接受代价**：`cost` / `wall_time` / `plan_revision_rounds` 上行但在阈值内。
- **失败/回退信号**：守门指标破线（尤其 `WMR>0`、`cost` 超 1.15×、topo 顶格致 `AWAITING_HUMAN` 激增）→
  先调子开关（提高 `god_node_min_dependents` / 降 `god_node_risk_bump` / `mode: auto` / 降 `_MAX_TOPO_ISSUES`），
  仍不行则 `enabled: false` 整体回退（§7.3 逃生口）。

### 11.5 执行顺序（建议）

1. **冒烟**：forgejo 上各跑一次 Control / Treatment-core，diff 三份报告，确认每个消费方 ≥1 次触发（§11.3 计数）——先证「活」。
2. **小集 A/B**：3 个 C-class + `t1-0031..0033` 各跑 N=3（`DET` 口径），算聚焦指标位移。
3. **归因**（若收益显著）：按 §11.1 单开关回退定位主要贡献者。
4. **记录**：结果落 `eval_acceptance_<sha>.json`（acceptance.md §3 schema），基线历史表追加一行；
   用本轮真实数据回填 §7.3/§8.5 标注「未标定」的阈值（`god_node_min_dependents=8` / `god_node_risk_bump=0.15` /
   `_MAX_TOPO_ISSUES=25`）的标定建议。

### 11.6 实测冒烟结果（2026-05-26，forgejo）

> §11.5 step 1 的首次实测。目标仓库 forgejo（Go+JS），`test/fork` 合 `origin/forgejo`，base `160377405c`，
> 124 文件 / 32 commits。为干净隔离图效应，本轮用 `llm_assist.mode: off` + 全 agent `temperature: 0`
> 把 planning 变为**纯确定性**（risk_score 不再经 LLM 重打分）；模型 mimo-v2.5-pro。

**前提（决定性）——非 Python 仓库必须装 `[ast]` extra**：首次跑时 `tree_sitter*` 未安装，依赖图在 Go 仓库
**全程为空**（`treesitter_extractor` 优雅降级返回 0 边），所有消费方空转。`pip install -e ".[ast]"` 后图才非空。
→ **运维结论**：对 Go/TS/JS fork，依赖图开关之外还须确保 `[ast]` 已装，否则 feature 静默失效（应在 `merge validate`
或首跑 wizard 增加一条「图已开但 tree-sitter 缺失」告警——见下「附带发现」）。

**确定性 A/B（Control `enabled:false` vs Treatment `enabled:true`，均 llm_assist off + temp 0）：**

| 维度 | Control（图关） | Treatment（图开） | 归因 |
|---|---|---|---|
| 依赖图边 | 0 | **53**（file_count 161，含反向邻居）；2 God Node：`services/context/context_cookie.go`=42 deps、`web_src/js/.../common-global.js`=10 | 图构建（Go+JS 提取均生效） |
| 风险分布 | 109 auto_safe / 15 auto_risky | **109 / 15（完全相同）** | 确定性下图**不重分类任何文件** |
| `context_cookie.go` risk | 0.14 | **0.29（精确 +0.15）** | **C1 God Node 抬分**（未跨 0.3 档→level 不变，§5 单调只抬不跨） |
| planner_judge | **approved，0 issue** | **revision_needed，6 个 `batch_ordering`** | **B9 topo precheck**（确定性、无 LLM） |
| 计划落点 | 自动放行（→AUTO_MERGING） | **STALLED → AWAITING_HUMAN** | topo 逼出人工检查点 |

**消费方触发证据（全部在真实合并上观测到）：**

| 消费方 | 触发 | 证据 |
|---|---|---|
| 图构建 | ✅ | 0→53 边、2 God Node、Go+JS 提取 |
| planner C1 God Node | ✅ 确定 | `context_cookie.go` +0.15 |
| planner_judge B9 topo | ✅ 确定 | 6 `batch_ordering`；Control 0 |
| conflict_analyst B7 | ✅ | 3 个真实冲突文件（`user.go`/`auth_token.go`/`oauth.go`）注入 impact_hint（blast=0，因非 hub，God Node 本次未冲突） |
| executor B8 | ✅ | auto_merge 2 轮 dispute 修复轮 |
| human_interface C5 | ✅ | 决策卡 `dependents_count/blast_radius/is_god_node` 字段已填（叶子文件值 0，字段在线） |
| judge A5（`_check_dependency_graph_impacts`→`dependency_missed_update`） | ⚠️ 接线运行、**本数据集 0 命中** | judge verdict=FAIL，3 个漏改由**既有文本 grep**（`reverse_impact_unhandled`：`GenerateAuthToken`/`Callout`/`Verify` 签名变更）抓到，**非图**——印证 §8「图=精确层、grep=召回兜底」，本次 grep 兜底生效 |

**确定性（DET）**：Treatment plan 阶段 N=3 逐字节一致（53 边 / 109-15 / cookie 0.29 / 6 topo）——图构建、God Node 抬分、
topo precheck 均非 LLM，确定性由构造保证。

**关键澄清（A3 fanout 之前的「降级」是噪音不是图）**：temp=0.2 + llm_assist auto 时曾见 17 文件 risk_score 变动、
含 1 个 human 文件降级跌破 0.6——经本轮确定性复跑证实，那是 **fanout 改变 LLM tier 路由后 rescore 的噪音**，
**非图的确定性效果**（llm_assist off 时 fanout 对 risk_score 零影响、风险分布两臂相同）。即 A3 仅在 llm_assist 开时
经 LLM 间接生效，本身不构成 §5 单调性违反。

**附带发现（非依赖图问题，但本轮暴露）：**
1. **CLI 驱动器盲区**：`--auto-decisions` / `resume` 的 `detect_current_phase` 仅在有 per-file pending 项时识别
   `PLAN_REVIEW`；而依赖图 topo issue 造成的「**0 pending 的非收敛 STALLED 计划**」识别不出 → 自动驱动器无法推过
   （本轮靠 `resume` 的 fallback 注入 `plan_approval: approve` 才推进）。建议给 `detect_current_phase` 补一条
   「STALLED 计划且 `plan_human_review is None`」→ `PLAN_REVIEW` 分支。
2. **B9 topo issue 非 actionable**：planner 对 6 条 topo 建议**逐条拒绝**（「只引文件路径、无具体 diff 证据，且
   conflict_count=0/非安全敏感」）→ 计划必 STALLED → 人工。即 topo 信号目前只能**逼出人工签字**、无法被 planner
   消化为自动重排序——价值有限，符合 §7.3「逃生口」但偏保守。
3. **端点抖动**：mimo-v2.5-pro 响应在 1.5s ↔ 143s ↔ 完全挂起间剧烈波动，致整管线（judge 批量评审）一度挂死。
   **确定性 plan 阶段（仅 ~3 调用）是当前最可靠的图效应度量口径**；整管线 A/B 受端点稳定性与本数据集 0 文本冲突双重限制。

**成本**：plan 阶段 ~$0.005–0.014/run（mimo，可忽略；与记忆中 Claude 单 run $96 不同量级）。

**结论**：依赖图在真实 forgejo 合并上**确定性地改变了系统行为**——最显著者为 planner_judge 把一个本会**自动放行**的
计划判成 **revision/STALLED 并逼出人工检查点**（B9），以及 God Node 风险确定性抬升（C1）。conflict_analyst/executor/
human_interface 三消费方均触发但因本数据集（God Node 未冲突、叶子文件、0 文本冲突）效果值偏小；judge 图漏改检查（A5）
接线正常但本数据集无触发条件，漏改由文本 grep 兜底捕获。**下一步**应换一个「上游改 hub 文件签名 + 下游 fork 未跟随」
的 C-class 样本，才能看到 B7 blast-radius 非平凡值与 A5 `dependency_missed_update` 的真实命中。

### 11.7 受控 C-class 复验：hub 签名变更 + 下游未跟随（B7/A5 真实命中）

> 承 §11.6 的「下一步」。forgejo 数据集恰好 God Node 干净合并、0 文本冲突，使 B7/A5 取不到非平凡值。这里用一个
> **最小受控 Python 仓库**（`/Users/angel/AI/merge-test/hub-cclass`）精确构造目标拓扑，隔离验证这两个消费方。
> Python 走 stdlib-ast 提取（不依赖 tree-sitter，确定性最强）；llm_assist off + temp 0。

**样本拓扑**：`auth/token.py` 定义 hub 函数 `verify_token`，被 `handlers/h1.py`..`h10.py` 各 import+调用（10 入边
→ God Node，阈值 8）。`test/upstream` 把签名改为 `verify_token(token, audience)`（**method_signature 接口变更**）；
`test/fork` 仅改 hub docstring（→ 与上游 **C-class** 冲突）并给每个 handler 加无关注释（→ E-class fork-only 修改），
**handlers 的调用点不跟随新签名**（仍单参）。

**关键 scope 发现**：handlers 是 **E 类（`current_only_change`，fork-only 修改）**，而 `_build_dependency_graph` 的
actionable 集只含 `{B, C, D_MISSING}` → handlers **默认不入图 scope**，首跑 `edges=0`、A5 无从触发。需用
`dependency_graph.extra_scan_globs: ["handlers/*.py"]` 把它们拉进 scope，才建出 10 条边。
→ **潜在局限**：一个被改动 hub 的纯 fork-modified（E 类）/未改动（A 类）下游，默认不在子图作用域内，A5 会漏检；
真实仓库若依赖此类下游覆盖，需配 `extra_scan_globs` 或扩 actionable 集。已记入「附带发现」。

**B7（conflict_analyst blast-radius）真实命中**：hub 选 `llm_auto_merge` 路由进 conflict_analyst，phase 在
`analyze_file(..., impact_hint=state.dependency_graph.impact_hint("auth/token.py"))` 传入 hint
（`direct_dependents=10, impact_radius=10, is_god_node=True`），`_format_blast_radius_block` 注入 enriched_context：

```
## Dependency Impact
- Direct dependents (files importing this one): 10
- Transitive impact radius (files affected by a break): 10
- GOD NODE: this file is a dependency hub. A regression here ripples widely — strongly prefer
  preserving its public interface; ... lean toward semantic_merge or escalate_human over a blind side-pick.
```

conflict_analyst 据此 **`recommended_strategy=semantic_merge`**（rationale 点明签名变更）——blast-radius 提示**实际改变了
解法选择**（非盲目 take_target）。✅ B7 非平凡命中。

**A5（judge `dependency_missed_update`）真实命中**：实跑驱动到 `JUDGE_REVIEWING` 时 run.log 记录
`Executor accepts all issues; repairing 10 items (round 1/2)`；用真实生产代码
`JudgeAgent._check_dependency_graph_impacts(state)` 在该 run 状态上确定性复算，得 **10 个 `dependency_missed_update`**
（h1–h10 各一，`HIGH` / `must_fix_before_merge=True`），描述：

> `'handlers/h1.py' imports 'auth/token.py' (EXTRACTED dependency) and still references 'verify_token',
> whose signature changed upstream ('token' -> 'token, audience'). The dependent was not taken from
> upstream, so it may not be updated for the new signature.`

此处 `reverse_impacts={}`（文本 grep 未覆盖 E 类 handlers），所以这 10 个命中是 **EXTRACTED 依赖图独有的召回**——
正是 §11.6 中 forgejo 缺失的「图相对 grep 的边际价值」。✅ A5 非平凡命中。

**小结**：在对口拓扑（改动 hub + 在 scope 内的过时下游）上，B7 给出 God Node 谨慎并改变解法、A5 精确召回 10 个
未更新下游——二者均按设计产出非平凡硬信号。两个数据集合起来印证 §8 分工：**图=精确层**（hub 集中、下游在 scope 时精准命中），
**文本 grep=召回兜底**（下游分散/超出子图时由 reverse_impacts 抓）。

### 11.8 本轮代码改动（持久化 tree-sitter 守卫）

§11.6 暴露的「`[ast]` 缺失致图静默空图」不能只靠环境装包兜底。已加持久代码守卫（防再次静默失效）：

- `src/tools/dep_extractors/treesitter_extractor.py`：新增 `missing_grammar_languages(languages)`（复用 `_GRAMMAR_MODULE`，
  排除 stdlib-ast 的 `python`）。
- `src/cli/main.py`：`validate_command` 新增 `validate_config_warnings()`——图开且文法缺失时打印**非致命黄色告警** +
  `pip install ".[ast]"` 提示（图关则静默）。
- `src/core/phases/initialize.py` `_build_dependency_graph`：同条件 `logger.warning` + `ctx.notify`，真实 run 也暴露降级。
- 测试：`tests/unit/test_dep_extractors.py::TestMissingGrammarLanguages`（4 例）；`mypy src` 干净、`ruff` 通过、
  `test_cli.py` 22 例无回归。
