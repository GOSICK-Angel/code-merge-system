# 依赖图全局优化 — 实施笔记 (Phase A)

> 对应方案：`doc/references/dependency-graph-optimization-plan.md`
> 范围：**仅 Phase A (P0)** —— 复活提取器 + 构建接线 + planner/judge 消费。B/C 阶段留作后续。
> 维护者请把这里当成「方案没写、但实现必须决定」的真相记录。开始日期：2026-05-25。

---

## 0. 范围与基本前提（实现前核实）

- **死代码确认**：`grep` 证实 `DependencyExtractor` / `state.dependency_graph` 生产侧零调用，
  仅模型定义 + 4 个单测（`test_dependency*.py`）。与方案 §0 描述一致。
- **tree-sitter 当前环境未安装**：它是 `pyproject.toml` 的 `[ast]` **可选** extra，
  `pip list` 无任何 `tree-sitter*`。方案 §1/§6.2 称「文法已 vendored」——
  **在当前 dev 环境不成立**。这是本实现最重要的偏差，决定了下面一系列设计。
- 用户确认的两个范围决策（2026-05-25）：
  1. 本轮**只做 Phase A**。
  2. Python 提取走 **stdlib `ast`**（始终可用、零额外依赖）；js/ts/tsx/go 走 tree-sitter，
     缺绑定时优雅降级返回空边。

---

## 1. 关键决策（方案未明确，由实现决定）

### D1. Python 用 stdlib `ast`，非 Python 用 tree-sitter
方案 §6.2 说「`dependency_extractor.py` 从 Python-only 升级为 tree-sitter 驱动」。
但 tree-sitter 在当前环境不可用，若 Python 也强依赖 tree-sitter，则**连 Python 都提取不到边**，
Phase A 在未装 `[ast]` 的环境里收益为零（又是死代码）。
- **决定**：保留并复用现有 stdlib `ast` 实现作为 Python 提取器（始终可用）；
  tree-sitter 仅用于其它语言，且不可用时降级返回 `[]`（不崩溃）。
- **后果**：未装 `[ast]` 的环境里，纯 JS/TS/Go 仓库的依赖图为空 → planner/judge 行为不变（安全降级）；
  Python 仓库始终有图。这与方案「risk 单调、绝不因图空降低风险」的守卫一致。

### D2. 保留 `dependency_extractor.py` 公共 API（不破坏现有测试）
现有 4 个测试通过 `DependencyExtractor.extract_from_sources(files)` +
`build_dependency_summary` / `build_impact_summary` 断言行为。
- **决定**：`dependency_extractor.py` 降级为**编排层**——构建共享 module_index、按扩展名分派到
  `src/tools/dep_extractors/` 下的 per-language 提取器、合并边。公共方法签名与语义保持不变。
- per-language 拆分满足方案 §8「文件 ≤800 行、按 per-language 模块拆分 `src/tools/dep_extractors/`」。

### D3. `DependencyGraphConfig.enabled` 默认值
方案 §7.2 要求「受新 config 开关 `dependency_graph.enabled` 控制」，但没给默认值。
其兄弟 `ReverseImpactConfig.enabled` 默认 `True`。
- **决定**：默认 `enabled=True`，与 reverse_impact 对齐，保证特性「活着」（反死代码 DoD 要求被构建）。
- **风险**：默认开启意味着 judge 可能因图新增 must_fix issue，改变既有合并结论。
  为压制误报：judge 侧只对 **EXTRACTED 边** + **符号仍被文本引用** 升 issue（见 D6），
  且未装 tree-sitter 的非 Python 仓库图为空 → 无行为变化。若线上发现误报过多，
  可在 `.merge/config.yaml` 设 `dependency_graph.enabled: false` 快速回退。

### D4. 构建作用域 & 文件内容来源
方案 §6.1：scope = 改动文件 ∪ N-hop 邻居，复用 `ReverseImpactScanner._resolve_scope`。
- **决定（Phase A 简化）**：
  - scope = 改动文件（B/C/D_MISSING 类）∪ reverse-impact scope（fork-only + customization +
    `dependency_graph.extra_scan_globs`），并按 `max_files` 截断（默认 800）。
  - 文件内容**从工作树读取**（`repo_path / fp`，与 `ReverseImpactScanner` 一致），
    UTF-8 解码失败/不存在则跳过。不为 D_MISSING（fork 中不存在的 upstream 新文件）单独取 upstream 内容——
    Phase A 不追求这部分边。
  - module_index 由 scope 文件集构建；只保留 target 落在 scope 内的边（子图，非全仓图）。
- **后果**：图是「改动相关子图」，足够支撑 planner 的批内定序与 judge 的 dependents 检查；
  不覆盖 scope 外的 importer。真正的 N-hop 反向扩展（扫描全仓找谁 import 改动文件）留作 Phase B/C。

### D5. planner 消费方式 —— topo 做「次级排序键」而非覆盖
现有 `_split_by_risk_level` 已按 `(risk_score 升序, path)` 排序，这是 P1-6 既定语义
（安全文件先合，回滚代价有界，有明确 rationale）。直接用 topo 覆盖会**回归 P1-6**。
- **决定**：把 topo 排名作为**次级键**插在 `risk_score` 之后、`path` 之前：
  `key = (risk_score, topo_rank.get(fp, 0), path)`。
  - 图空时 `topo_rank` 为空 → 退化为 `(risk_score, path)`，与现状逐字节一致。
  - 图非空时，**同 risk_score** 的文件按依赖拓扑排序（被依赖者/基类先），可观测、确定性。
- fanout：`_compute_fanout_map` 图非空时改用 `len(impact_radius(f))` 归一化
  （saturation 复用 `_FANOUT_SATURATION=10`），喂 `compute_complexity` 的 fanout 维度；
  图空时回退原「同模块兄弟数」代理。**只抬不降**：取两者 max，避免图把 fanout 拉低（§5 单调性）。

### D6. judge 消费方式 —— EXTRACTED 边 + 文本二次确认
方案 §4/§5：用 `impact_radius` 找真实未更新 dependents；EXTRACTED 边漏改 = 硬 issue。
- **决定**：新增 `_check_dependency_graph_impacts`：
  - 仅看 `interface_changes` 中**签名类**变更（`method_signature`/`constructor_signature`，
    `before != after`，有 symbol）——与 `_check_cross_decision_signature_split` 同源。
  - 对变更符号所在文件 F，取 **confidence==EXTRACTED** 的入边（`target_file==F` 的 dependents D）。
  - 二次确认：D 的工作树内容**仍用 word-boundary 正则命中该 symbol**（避免文件级粗粒度边误报），
    且 D 不是 F 本身。
  - 命中则升 `IssueSeverity.HIGH` + `must_fix_before_merge=True`，issue_type=`dependency_missed_update`。
- **为何 HIGH 而非 CRITICAL/VETO**：reverse_impact（文本召回兜底）已是 CRITICAL+veto；
  本检查是「精确层」补充，定 HIGH+must_fix 既构成真实门、又比全局 veto 略保守，控制 FP 影响。
  方案说「硬 issue」，HIGH+must_fix 满足「必须修才能合」的硬性。
- INFERRED/AMBIGUOUS 边**不**升 issue（§5：INFERRED 仅进 prompt 参考，本轮 Phase A 不做 prompt 注入）。

### D7. 契约 inputs
- `judge` 在 `run()` 用 `restricted_view`，**必须**把 `dependency_graph` 加入 `judge.yaml` inputs，
  否则 `getattr(view, "dependency_graph", default)` 会静默吞掉 `FieldNotInContract`
  （正是 memory `project_judge_dead_contract_checks.md` 记录的坑）——检查变死代码。
- `planner` 读**原始 `MergeState`**（非 restricted_view），加 input 是方案要求 + 约定，
  不加不会触发 FieldNotInContract，但仍按方案加上以保持契约自描述。

---

## 2. 反死代码 DoD 对照（方案 §0 铁律）

| 条件 | 落点 |
|---|---|
| (a) 某 phase 显式构建/填充 | `initialize._build_dependency_graph()` 填 `state.dependency_graph` |
| (b) ≥1 agent 契约 inputs 声明 | `planner.yaml` + `judge.yaml` 加 `dependency_graph` |
| (c) gate/确定性逻辑真正读取 | planner `_compute_fanout_map`/排序；judge `_check_dependency_graph_impacts` |
| (d) 单测断言「图非空 ⇒ 行为改变」 | planner 排序/fanout 变化测试；judge 漏改 issue 测试；反死代码回归测试 |

---

## 3. 临时假设 / 已知局限（维护须知）

- **未做（明确推迟到 B/C）**：conflict_analyst / executor / planner_judge / memory_extractor /
  human_interface 的消费；import 别名 / monorepo / tsconfig paths 解析；Leiden 社区 / God Node；
  跨运行缓存（持久化靠 checkpoint，方案 §6.5）。
- tree-sitter 提取器的 import→路径解析较朴素（相对 import + 模块名匹配），别名/裸模块可能漏边或判 INFERRED。
- 图随 `state` 进 checkpoint（`FileDependencyGraph` 是 frozen pydantic 模型），resume 自动恢复——无需额外缓存层。
- 性能：scope 受 `max_files`（默认 800）截断；Python `ast.parse` 对大文件 O(n)，
  大型 fork 子图仍可能偏大，必要时调小 `max_files` 或关 `enabled`。

---

## 4. 变更文件清单（随实现更新）

> 注：实施期间用户把笔记目录从 `doc/execution` 重命名为 `doc/execute`，内容不变。

- [x] `src/tools/dep_extractors/__init__.py`（新建：分派 `language_for` + 扩展名表）
- [x] `src/tools/dep_extractors/python_extractor.py`（新建：stdlib ast，迁移自旧文件）
- [x] `src/tools/dep_extractors/treesitter_extractor.py`（新建：js/ts/tsx/go，复用 chunker `_get_parser`，降级）
- [x] `src/tools/dependency_extractor.py`（改造为编排层，保 `extract_from_sources`/`build_*_summary` API）
- [x] `src/models/config.py`（+DependencyGraphConfig，挂 MergeConfig.dependency_graph）
- [x] `src/core/phases/initialize.py`（+_build_dependency_graph，紧邻 reverse_impact）
- [x] `src/agents/planner_agent.py`（fanout from impact_radius + topo 次级键）
- [x] `src/agents/judge_agent.py`（+_check_dependency_graph_impacts）
- [x] `src/agents/contracts/planner.yaml`、`judge.yaml`（+dependency_graph input）
- [x] `tests/unit/test_dep_extractors.py`（多语言路由 + tree-sitter 降级 + 含 skipif 的 TS 用例）
- [x] `tests/unit/test_dependency_graph_phase.py`（构建填充 / 空 scope noop / disabled / max_files 截断）
- [x] `tests/unit/test_dependency_graph_consumers.py`（planner topo+fanout、judge 漏改、反死代码回归、契约声明）

---

## 5. 实施过程记录（逐步追加）

- 2026-05-25：核实现状、确认范围（仅 A）、确认 Python 走 ast；建笔记。
- 2026-05-25：完成代码侧 5 项。补充实现细节（与方案的细微出入）：
  - **judge 漏改判定的「未更新」语义**：无法精确知道 dependent 是否已适配新签名，
    采用保守信号 = `decision != TAKE_TARGET`（未整体取 upstream）+ 文本仍命中符号。
    `decision == TAKE_TARGET` 视为已带上游新用法 → 不报；无 decision 记录的 dependent 仍报（更保守）。
  - **去重**：judge 跳过已被 `reverse_impacts[symbol]` 文本召回的 dependent，避免与 `_check_reverse_impacts` 重复 issue。
  - **fanout 组合**：图 fanout 与模块 fanout 取 `max`（§5 单调，图只抬不降）；两者都不可用时返回 None（保旧「丢弃 fanout 维度」）。
  - **topo 仅作 tiebreak**：`_split_by_risk_level` 排序键 `(risk_score, path)` → `(risk_score, topo_rank, path)`，
    图空时 topo_rank 全 0 → 与旧行为逐字节一致（167 个 planner 测试已验证未回归）。
  - judge `_build_issues` 在 `_check_cross_decision_signature_split` 之后插入新检查。
- 2026-05-25：Phase A 完成（tree-sitter 尚未安装时）。
- 2026-05-25：**应用户要求安装 tree-sitter（`pip install -e ".[ast]"`，装入 .venv）**，多语言路径真正激活。
  随之发现并处理两件事：
  - **D8. 不复用 chunker 的 `_get_parser`，提取器自带 parser 加载器**：
    已安装的 `tree_sitter_typescript` 0.23.2 暴露的是 `language_typescript()` / `language_tsx()`，
    **没有顶层 `language` 属性**。chunker 的 `_get_parser` 写死 `lang_mod.language`，对 TS **本就是坏的**
    （TS 的 AST 分块一直静默降级——这是先于本次的 latent bug，未在本轮修复，超出 Phase A 范围）。
    为此 `treesitter_extractor._load_parser` 改为**自带加载器**（`_grammar_language` 按语言选 `language_typescript`/`language_tsx`/`language`），
    与 chunker 解耦、隔离正确。js / go / python 三个 grammar 用统一 `language()` 正常。
    （**后续已修 chunker 本身**，见下方 2026-05-25 chunker 修复条目；提取器仍保留自带加载器，不依赖 chunker。）
  - **chunker.py 顶部 tree-sitter 守卫改写**：安装后 mypy strict 报 `import tree_sitter` 的 `type: ignore` unused +
    `tree_sitter=None` 赋值类型冲突（装 vs 不装环境表现不同）。改为
    `importlib.util.find_spec` + `importlib.import_module` 且 `tree_sitter: Any`，
    环境无关、stub 无关，不再触发 mypy/E402。行为等价（chunker 66 测试守护）。
- 2026-05-25：**应用户要求顺手修复 chunker 的 TS parser 加载本身**（不止依赖图提取器）：
  - `chunker._get_parser` 原写死 `lang_mod.language` 再 `lang_fn("typescript")`，对新版 `tree_sitter_typescript`
    （只有 `language_typescript()`/`language_tsx()`）抛 AttributeError → TS/TSX 的 AST 分块一直静默退化为
    indent fallback。新增本地 `_resolve_grammar(lang_mod, language)`：优先无参 `language_typescript`/`language_tsx`，
    回退旧版带参 `language(name)`，其余语言 `language()`。9 种语言里 8 种加载 OK（cpp NONE 因 `[ast]` extra
    未列 `tree-sitter-cpp`，预存、与本次无关）。
  - 与依赖图提取器**刻意不共享 helper**：chunker 覆盖语言更多（rust/java/c…），且不想新增 chunker→dep_extractors 的耦合方向；
    两处各保留约 6 行小逻辑。
  - **新增回归测试** `test_chunker.py::test_ast_chunk_typescript_loads_parser` / `test_ast_chunk_tsx_loads_parser`
    （`@needs_tree_sitter` 守卫），断言 TS/TSX 真正产出 AST chunk（function+import），防止再次静默退化。
- 2026-05-25：最终验证（tree-sitter 已装）：
  - `pytest tests/unit/`：**2652 passed, 0 skipped**（含 TS/TSX 提取与 chunker 加载回归；新增 js/go/tsx 用例）。
  - `mypy src`：165 文件 0 错误（strict）。`ruff check src/`：通过。
  - **维护须知**：
    - tree-sitter 现已装入 `.venv`。`pyproject.toml` 里仍是 `[ast]` 可选 extra；新环境/CI 若未装 `[ast]`，
      非 Python 仓库依赖图为空 → planner/judge 无行为变化（安全降级，已被 graceful-degradation 测试覆盖）。
    - `_build_dependency_graph` 复用 `ReverseImpactScanner._resolve_scope`（私有方法）——签名变动需同步。
    - judge 新 issue 默认开启（`dependency_graph.enabled=True`）。误报可在 `.merge/config.yaml` 设
      `dependency_graph: {enabled: false}` 关闭，不影响其它逻辑。
    - 笔记目录被用户改名为 `doc/execute`（原 `doc/execution`）。

---

## 6. 相关修复：LLM 层上下文压缩（分段渲染）—— 与依赖图无直接关系，但共享 chunker

> 复活 chunker（TS 加载）后，顺带审查了"审大文件时只展开相关块、其余降级/丢弃"的 staging 基础设施
> （`build_staged_content` → `RelevanceScorer.score_and_assign` → `render_file_staged`），发现并修了两处硬伤。

- **背景结论**：该基础设施**不是死代码**，已接入 judge / conflict_analyst / executor 三个审文件 agent。
  但有两处缺陷让"只展开相关块"在实践中失效：

- **修复①：渲染级别字典曾用 `chunk.name` 做键 → 同名 chunk 碰撞塌缩**
  （`relevance.score_and_assign` / `_demote_to_fit`、`chunker.render_file_staged`、
  `prompt_builders.build_staged_content` 全部受影响）。同名来源：`@overload`/条件重定义的同名 def、
  空名或同首行的 STATEMENT 块。后果：相关块被同名无关块覆盖而丢弃，或无关块被连带展开 FULL。
  - **改法**：新增 `chunker.chunk_key(chunk) = (start_line, end_line)`（chunk 在文件内行段互斥，唯一；
    `byte_range` 因 indent-fallback 路径默认 (0,0) 不可靠故不用），三处统一改用此键。
  - 顺带：`score_and_assign` 的 name 子串 boost 加了空名防护（`chunk.name and ...`），避免空名 `""` 永远命中。
  - 回归测试：`test_relevance.py::test_same_named_chunks_do_not_collide`（3 chunk⇒3 键，相关块 FULL/无关块不被误丢）。

- **修复②：judge 的 staging 曾锁在 `if self._memory_store:` 内**（`judge_agent.review_file`）。
  conflict_analyst/executor 早有 U1.A 解绑，judge 漏了。memory 关时 judge 把大 merged 文件**盲目头截断**
  （`_truncate_content` 默认上限）而非分段——正是 forgejo "tokens=309/98789 后谎报 truncated" 场景。
  - **改法**：把 `AgentPromptBuilder` 构建 + 预算计算 + `build_staged_content` 提到 `if self._memory_store` 之外，
    仅 memory-text 注入仍受 gate。memory 关时现在走 relevance staging（保留相关块）而非头截断。
  - 回归测试：`test_judge_staging_decoupled.py::test_judge_stages_content_without_memory_store`。

- **修复③：conflict_analyst 大文件分块分析做相关性预过滤**（`_chunked_analyze_file`）。
  原先对 >40KB 文件按语义边界切块、**每块各发一次 LLM 调用、全部分析**，不管该块有无改动——
  100KB 文件切 10 块、只有 1 块有冲突也发 10 次调用。
  - 调研结论先行：executor 的整文件分块合并是**设计使然不能改**（合并产物必须完整、不能丢块）；
    judge 的"大文件超窗"已被 ② 修掉（一律走 staging + `_demote_to_fit` 压进预算）。所以 ③ 真正可改的只有 conflict_analyst。
  - **判据（最安全）**：成对块 `cur == tgt`（字节相同）⇒ 无分歧⇒不可能有冲突⇒跳过其 LLM 调用。
    仅当"存在改动对**且**非全部改动"时才裁剪；全相同 / 全改动两种 degenerate 情形保留所有对，
    保证聚合永不空、行为与改前逐字节一致。裁剪集是旧 fan-out 的**严格子集**⇒只省成本、绝不改 verdict。
  - **不依赖 diff 行号**（绕开问题④），也不动 executor。
  - 失败分母 / `chunk_count` 改用"已分析块数"。
  - 回归测试：`test_conflict_analyst_chunked.py::test_chunked_skips_unchanged_pairs`（改 1 块⇒调用数 < 总块数）、
    `::test_chunked_all_changed_analyzes_every_pair`（全改⇒不跳，无回归）。

- **修复④：judge 的 relevance 锚点改用 merged 坐标系的 diff 行段**（`judge_agent`）。
  原 `_extract_diff_ranges` 取 `hunk.start_line_current`（fork 侧、合并前行号），但 staging 是对 **merged** 内容分块——
  合并插/删行后行号漂移，相关性打分会锚错块（相关块被判无关而降级/丢弃）。
  - **改法**：新增 `_merged_content_diff_ranges(before, after)`：对 `decision_record.original_snapshot`（合并前快照，
    patch_applier 已填）与 `merged_content` 做 `difflib.SequenceMatcher` 行级 diff，输出 **after(merged) 坐标**的改动行段。
    `review_file` 优先用它；`original_snapshot` 缺失 / 无变化 / 文件过大（>6000 行 guard）时回退旧的 hunk 行段。
  - 验证示例:before 第 2 行变更、merged 顶部插 3 行 ⇒ 正确报 `(5,5)`（merged 坐标），而非旧的 `(2,2)`。
  - 回归测试：`test_judge_diff_ranges.py`（merged 坐标、None/identical/大文件 guard 回退、review_file 确实传 snapshot 行段）。
  - **范围**：judge 走 merged 坐标(本条);conflict_analyst/executor 的 `target_content` target 侧错配在下一条修复。

- **修复④-target：conflict_analyst/executor 给 `target_content` staging 改用 target 侧行段**
  （`conflict_analyst_agent` / `executor_agent`）。
  原两个 agent 的本地 `_extract_diff_ranges` 只取 `hunk.start_line_current/end_line_current`，但 `target_content` 是
  upstream 版本——同一改动在 fork 与 upstream 行号不同，用 current 侧行段会锚错 upstream 的相关块。
  - **改法**：两个本地 `_extract_diff_ranges` 加 `side: Literal["current","target"]="current"` 参数；`"target"` 取
    `hunk.start_line_target/end_line_target`，hunk 缺失时的回退分支(`lines_added+lines_deleted+100`)两侧通用、不变。
    staging `target_content` 时传 target ranges；`current_content` 仍 current。
  - **base 侧不修**(`DiffHunk` 无 base 行号,要对齐需额外 diff base↔current;base 的 staging 预算最小 `//4`、影响最低)，
    `base_content` 继续用 current ranges——记为已知限制。
  - executor 的 target 修复属"改 LLM 看到的合并视图"=行为变化面,保真守卫(`_foreign_chars` 拿**全量源**比对)继续兜底。
  - 回归测试：`test_diff_range_target_side.py`（两 agent 的 side 提取 + analyze_file/execute_semantic_merge 确实把
    target ranges 传给 `target_content` staging、current 传给 `current_content`、base 传 current）。

- **修复⑥：relevance name-boost 改整标识符匹配,消除短名子串误命中**（`relevance.score_and_assign`）。
  原 `chunk.name in full_contents`(裸子串)会让短名(`id`/`x`)命中无关词内部(如 `width` 里的 `id`)→误升级无关块、白烧 token。
  - **改法**：新增 `_identifier_tokens()` 用 `re.findall(r"[A-Za-z_][A-Za-z0-9_]*", ...)` 把 FULL 块内容预切成标识符
    token 集合,判断改为整词 `chunk.name in full_names`。O(总长) 预处理 + O(1)/块,比逐块正则更快。不加长度阈值(会漏合法短名引用)。
  - 回归测试：`test_relevance.py::test_name_boost_requires_whole_identifier`（`width` 中的 `id` 不 boost、整词 `get_user` 仍 boost）
    + `test_identifier_tokens_whole_word_only`。

- **未修（已知、较轻，留作后续）**：
  - 问题④ **base 侧**：`base_content` staging 仍用 current 侧行段(见上,won't-fix,`DiffHunk` 无 base 坐标)。
  - budget 用 `//4` 而非 `/3.5`（轻微欠预算）；staging 无 AST/无锚点时兜底仍是头截断（评估后认为收益小未做 diff-anchored 窗口）。
  - executor 整文件分块合并按设计保留（不能丢块）。
- 验证：`pytest tests/unit/` **2667 passed**；`mypy src` 0 错；`ruff check src/` 通过。
