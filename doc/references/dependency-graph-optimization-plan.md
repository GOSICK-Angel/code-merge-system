# 文件依赖图全局优化方案

> 综合 [Graphify](./graphify-analysis.md) 与 [Understand-Anything](https://github.com/Lum1104/Understand-Anything) 两个开源项目，
> 把当前半死的依赖图机制升级为**全局共享、多 agent 消费**的一等资产。
>
> **状态（2026-05-26 更新）**：**Phase A 已落地**（提交 `58600ac`，2026-05-25）——多语言提取 + initialize 构建 +
> planner topo/fanout + judge EXTRACTED 漏改 + config 开关，§0 反死代码 DoD 四条均满足。**Phase A+（计划外）**：
> tree-sitter 实装后 edge `target_symbol` 已填充,新增「依赖图符号 → staging relevance」消费方(提交 `d1aa956`)。
> **Phase B / C 待做**。进度详见 §7 标记与 `doc/execute/implementation-notes.md`。原始日期：2026-05-25。

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

### Phase B — 冲突与执行消费（P1）⬜ 待做
7. `conflict_analyst.yaml` + CA gate：注入 hunk blast-radius / God Node 命中。
8. `executor.yaml` + E-DELETION/E-SEMANTIC-MERGE：删除/改签名前查 `dependents_of`，与 sentinel 协同。
9. `planner_judge.yaml` + PJ-PLAN-REVIEW：topo 顺序违规检查。
> 注：conflict_analyst / executor 本轮已因 Phase A+ 的 staging 而读取 `state.dependency_graph`（走原始 state /
> phase 传参，未经 `restricted_view`，故无需扩二者 contract）；但 §4 表设想的 **blast-radius / dependents_of**
> 消费（step 7/8）本身仍未实现。

### Phase C — 增强（P2/P3，可选）⬜ 待做
10. import 别名/monorepo 解析（§6.3）；Leiden 社区 + God Node（§6.4）。
11. memory_extractor 沉淀 hub/surprising；human_interface 决策卡展示依赖摘要。

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
```
