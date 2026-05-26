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

- **审计发现（chunker 链路排查 2026-05-25）：relevance 5 个评分维度中 3 个在生产恒为 0（死维度）。**
  `build_staged_content` 的全部 6 个生产调用点(executor×2 / conflict_analyst×3 / judge×1)只传前 4 个位置参数,
  `ScoringContext` 从不收到 `conflict_ranges` / `security_patterns` / `referenced_names` → `_conflict_score` /
  `_security_score` / `_reference_score` 恒 0。压缩只靠 `_diff_overlap_score` + base + entry_point + name-boost 单一信号。
  后果:安全敏感块/冲突区/被引用依赖若不落在 diff 行段内,会被降级甚至 DROP——与"别丢相关代码"目标冲突。

- **修复A：security 信号接通 staging（文件级布尔）**（`relevance` / `prompt_builders` + 三个 agent）。
  - **为何不喂 `security_sensitive.patterns`**：该 config 是**文件路径 glob**(`**/.env`、`**/*.pem`、`**/credentials.py`)，
    而 `_security_score` 做的是 chunk **内容**子串匹配——直接喂 glob 几乎不命中=形式接通、实际无效(假修复)。config 无内容关键词列表。
  - **改法**：`ScoringContext` 删死字段 `security_patterns`、加 `is_security_sensitive: bool`;`_security_score` 改为
    `0.3 if ctx.is_security_sensitive else 0.0`(文件级)。`build_staged_content` 删 `security_patterns` 参数、加
    `is_security_sensitive: bool=False`。三个 agent 关键字传:conflict_analyst/executor 用 `file_diff.is_security_sensitive`、
    judge 用 `original_diff.is_security_sensitive`。
  - **效果**：安全敏感文件每个 chunk +0.3 → 至少 SIGNATURE(不被 DROP),预算压缩时也比普通块更晚被 demote → 整文件结构(签名级)保留。
  - 回归测试：`test_relevance.py::TestSecuritySensitiveBoost`(布尔抬 0.3) + `test_context.py::
    test_build_staged_content_security_sensitive_preserves_whole_file`(安全敏感大文件保住尾部 `func_99`,非敏感同预算被头截断丢尾)。

- **修复B：`chunk_processor.align_chunks` 块数不等时改「覆盖式反向分组」,杜绝 upstream 静默丢/重**（`chunk_processor`）。
  - **旧 bug**：不等分支是「每个 current(a) 块找最近 upstream(b) 块」的多对一映射 → 未被任何 a 选中的 b 块从不进 pair
    (upstream 改动静默丢失)、被多个 a 选中的 b 块重复合并。executor 分块合并外层只遍历 pairs(每个 a 一项),保真守卫
    `_foreign_chars` 只查无中生有字符、不查内容缺失 → 丢失无人拦。
  - **改法**：反转为「每个 b 块按中点比例分配给最近的 a 块」。每个 b 恰好消费一次(不丢不重);中点单调 ⇒ 每个 a 拿到**连续**
    b 片段,`"".join` 还原真实 upstream slice;未分到 b 的 a 得空 tgt(fork-only 段,LLM 保留 fork)。返回结构不变
    (`list[(a, joined_b)]`,长度 = len(a)),executor 无需改。保留 empty 短路 + `len==len` zip 快路。
  - 回归测试：`test_chunk_processor_merge.py`(b 全覆盖恰好一次、a 多于 b 时多余 a 空 tgt、b 顺序完整 `reassembled==full`、zip/empty 快路)。

- **修复C：`chunker` 签名提取改括号深度感知,消除参数类型注解 `:` 截断**（`chunker`）。
  - **旧 bug**：`_extract_signature` / `_extract_indent_signature` 用 `text.find(":")` 取第一个冒号 → 命中**参数**类型注解
    (`def f(a: int):` → `def f(a:`、TS `function f(x: number): T {` → `function f(x:`),SIGNATURE 级渲染给 LLM 残缺签名。
  - **改法**：新增 `_signature_cutoff(text)`,跟踪 `() [] {}` 深度,只接受**深度 0** 的 `:`(Python)或 body `{`(C-like)为终止符,
    跳过参数列表/默认值/类型注解/dict 字面量内部的冒号与花括号;无终止符回退首行。两个签名函数共用。
  - **效果**：`def f(a: int, b: str) -> None:` 取完整签名;Go `func F(a int) error {` 取到 body `{`;TS 返回类型场景至少保住完整参数
    (取到返回类型前的深度 0 冒号)。仅影响 SIGNATURE 渲染,FULL 不受影响。
  - 回归测试：`test_chunker.py::TestSignatureCutoff`(Python 注解/TS 返回类型/Go body/dict 默认值/无终止符) +
    `TestSignatureExtractionEndToEnd`(IndentChunker + tree-sitter 端到端保完整注解签名)。

- **修复(reference)：接通 `_reference_score`——依赖图符号 → staging（C 档:三 agent + 多语言）**
  （`dependency` / `python_extractor` / `treesitter_extractor` / `prompt_builders` + 三个 agent + conflict_analysis phase）。
  - **两个前置障碍**:(1) 提取器从不填 `target_symbol`(边全 file→file,symbol 恒空)→ 直接喂 = 空集假接通;
    (2) graph 未透传到 staging(`build_staged_content` 无参数、`analyze_file` 连 state 都没有)。
  - **改法**:① 提取器填符号——Python `ImportFrom` 按 alias 建多重边填 `target_symbol`(`import X`/`*` 留空);
    tree-sitter 提取 named import/export 标识符(`import {foo,bar}`)填 symbol,default/namespace/Go 整包不填(本地别名≠目标符号)。
    ② `FileDependencyGraph.referenced_symbols(fp)` = `{e.target_symbol for e in edges if e.target_file==fp and e.target_symbol}`。
    ③ `build_staged_content` 加 `referenced_names: frozenset[str]` → `ScoringContext.referenced_names`(字段早已存在、`_reference_score`
    早已读它,只是从无填充)。④ judge.review_file / executor.execute_semantic_merge / conflict_analyst.analyze_file 三处用
    `state.dependency_graph.referenced_symbols(fp)` 填(conflict_analyst 经 phase 传参)。
  - **边模型决策**:复用现有 `target_symbol` 单值 + 多重边(零模型改动)。多重边使 `topological_order` 的 in_degree 虚高,
    但 Kahn 算法 adj/in_degree 对称抵消、拓扑结果不变(已分析);`dependents_of`/`impact_radius` 用 set 去重不受影响。
  - **无需扩 executor/conflict_analyst contract**:二者经原始 state(`execute_semantic_merge`)/新参数(`analyze_file` 由 phase 传)
    访问 graph,不经 `restricted_view` → 不触发 `FieldNotInContract`(contract test 37 passed 确认)。judge.yaml 早已声明。
  - **效果**:审/合并文件 F 时,F 中被其他文件 import 的公共符号(即使不在 diff 内)+0.3 → 至少 SIGNATURE,不被 DROP。
  - 回归测试:`test_dependency.py::TestReferencedSymbols`、`test_dep_extractors.py::TestImportSymbols`(Python named/module/star +
    tree-sitter named)、`test_context.py::test_build_staged_content_referenced_symbol_survives`(被引用尾部符号存活、无信号时被头截断丢)。

- **修复(conflict)：接通 `_conflict_score`——从 staging 内容自身扫冲突标记**（`conflict_markers` / `prompt_builders`）。
  - **坐标系陷阱**:`DiffHunk.conflict_marker_lines` 有两种来源——`parse_unified_diff` 给的是 **diff-hunk 内相对行号**(且
    unified diff 一般无标记),`parse_conflict_markers` 给的是**带标记内容的绝对行号**。而 staging 的 current/target 是**干净单侧
    版本**(无标记)。把 DiffHunk 的行号喂给 `conflict_ranges` = 坐标错配假接通。
  - **改法**:不依赖 DiffHunk。`build_staged_content` 对**即将 stage 的 content 自身**扫冲突标记行(新增
    `conflict_markers.conflict_marker_line_numbers`,复用已校准的整行 7 字符正则,避免 `>>>>>>>>>STOP` 这类 8+字符误报),
    转成单行 `conflict_ranges` → `ScoringContext`。坐标天然对齐 chunk 行号、对 6 调用点统一生效、无需改调用点。同时移除
    `build_staged_content` 的死参数 `conflict_ranges`(从无调用方传)。
  - **效果**:当 staging 内容含未解决冲突标记(合并失败残留 / 冲突工作树文件进入 LLM 分析/审查)时,标记所在 chunk +0.5 →
    FULL/SIGNATURE,冲突区完整展开;干净内容扫不到标记 → 空 `conflict_ranges`、无副作用。
  - 回归测试:`test_p0_fixes.py`(行号 1-based、8+字符不误报) + `test_context.py::
    test_build_staged_content_conflict_region_survives`(带标记大文件尾部冲突区存活、无锚点头部被压缩)。

- **未修（已知、较轻，留作后续）**：
  - 问题④ **base 侧**：`base_content` staging 仍用 current 侧行段(见上,won't-fix,`DiffHunk` 无 base 坐标)。
  - budget 用 `//4` 而非 `/3.5`（轻微欠预算）；staging 无 AST/无锚点时兜底仍是头截断（评估后认为收益小未做 diff-anchored 窗口）。
  - executor 整文件分块合并按设计保留（不能丢块）。
- 注:relevance 5 个评分维度(diff_overlap / conflict / security / reference / entry_point + base)现已**全部在生产中生效**——
  审计初期发现的 conflict/security/reference 三个死维度均已接通。
- 验证：`pytest tests/unit/` **2689 passed**；`mypy src` 0 错；`ruff check src/` 通过；`test_agent_contracts.py` 37 passed。

---

## 7. Phase B — 冲突与执行消费（P1）实施笔记

> 对应方案 §7 step 7/8/9。开始日期：2026-05-26。范围：conflict_analyst（blast-radius/God Node）、
> executor（dependents_of 删除/改签名）、planner_judge（topo 顺序违规）。维护者把这里当成「方案没写、
> 但实现必须决定」的真相记录。

### 7.0 共享前提（实现前 grep 核实）

- **三个 agent 访问 graph 的路径**：
  - `conflict_analyst.analyze_file` —— 不经 `restricted_view`，graph 派生数据由 conflict_analysis phase 以参数传入。
  - `executor.execute_semantic_merge` / `analyze_deletion` —— 收**原始 `MergeState`**（`executor_agent.py:371/882`），
    直接 `state.dependency_graph`（Phase A+ 已在 :434 这么做）。
  - `planner_judge.run` —— 走 `restricted_view`（`planner_judge_agent.py:232`）。
- **由此推出 contract 改动面**：**只有 `planner_judge.yaml` 需要加 `dependency_graph`**。
  conflict_analyst / executor 不经 restricted_view，加 input 反而是噪音（与 Phase A+ 笔记 §6 reference 修复结论一致）。
- **DoD (b)「≥1 agent 契约 inputs 声明」**：Phase A 已由 `judge.yaml` 满足；Phase B 的 blast-radius / dependents
  消费组件依附于「图已是 judge 契约输入 + 其余消费方走原始 state」这一既定模式。

### 7.1 关键决策（方案未明确，由实现决定）

#### DB1. blast-radius / God Node 注入「走 enriched_context」而非改 CA gate 签名
方案 §7 step7 说「CA gate：注入 hunk blast-radius / God Node 命中」。`build_conflict_analysis_prompt`
（CA-THREE-WAY）的 `project_context` 形参实际接收 `analyze_file` 拼好的 `enriched_context`
（memory + forks_profile 块已是这么注入的）。
- **决定**：把 blast-radius 谨慎块拼进 `enriched_context`，**不动 analyst_prompts.py 的签名**。
  好处：chunked 与非 chunked 两条路径都用同一个 `enriched_context`，一处注入两路覆盖；零 prompt-builder 改动面。
- **粒度**：方案写「hunk blast-radius」，但 edge 是**文件级**（提取器产文件→文件边）。实现用**文件级** impact_radius
  近似 hunk 级——记为已知近似（hunk 级需把 edge 细化到符号行段，超 Phase B 范围）。

#### DB2. `DependencyImpactHint` 模型 + `FileDependencyGraph.impact_hint()`
为让「direct_dependents / impact_radius / is_god_node」可测、可复用，在 `models/dependency.py` 新增 frozen
`DependencyImpactHint` 与 `impact_hint(fp, *, max_depth, god_node_min_dependents)`。God Node 判定下沉到模型，
agent/phase 只调一次。`has_signal` 属性用于「无信号则不注入块」（图空安全降级）。

#### DB3. God Node 阈值 = config `god_node_min_dependents`，默认 8
方案 §6.4 称 God Node = 高 degree 节点，但**无标定值**。
- **决定**：`DependencyGraphConfig.god_node_min_dependents: int = 8`（保守、可配，target-repo 无关）。
  子图作用域上限 800 文件下，8 个直接 dependents 已属枢纽。**未经真实仓库标定**——线上偏多/偏少可在
  `.merge/config.yaml` 调。仅用于「抬谨慎度」，绝不降风险（§5）。

#### DB4. executor 注入「下游 dependents」——保接口 / 删前查
- **E-SEMANTIC-MERGE**：`build_semantic_merge_prompt` 加 `dependents: Sequence[str]` + `referenced_symbols:
  frozenset[str]`（均带默认空值，不破坏既有调用/测试）。非空时 prompt 增「Downstream Dependents」段，
  要求**保留公共接口、勿删/改导出符号**。`execute_semantic_merge` 用 `state.dependency_graph.dependents_of(fp)`
  + `referenced_symbols(fp)`（后者 Phase A+ 已取，复用 `referenced` 变量）。
- **E-DELETION**：`build_deletion_analysis_prompt` 加 `dependents`。`analyze_deletion` 删前查 `dependents_of(fp)`，
  非空 → prompt 提示「N 个文件仍 import 本文件，删除有风险」，并把计数写进 `UserDecisionItem.risk_context`
  （人工决策卡可见）。
- **与 sentinel 协同**：`analyze_deletion` 同时读 `state.sentinel_hits.get(fp, [])`，把 sentinel 命中数并入
  risk_context 文案（dependents 是 AST 精确层、sentinel 是文本召回层，二者并列展示，不互相覆盖）。
- **保真守卫不变**：`_foreign_chars` 仍拿全量源比对（dependents 只改 prompt 文案 = LLM 视图，行为变化面已被守卫兜底）。

#### DB5. planner_judge topo 违规——`precheck_batch_topological_order`，镜像 `precheck_plan_integrity`
- **违规判据**：扁平执行序 `order = [fp for batch in plan.phases for fp in batch.file_paths]`；位置 `pos[fp]`。
  对每条 **EXTRACTED** 边 `source→target`（source 依赖 target），若 `pos[source] < pos[target]`（依赖者先于被依赖者合并）
  ⇒ 违规，flag **source**。仅 EXTRACTED（§5 单调；INFERRED/AMBIGUOUS 不升 issue）。
- **`issue_type="batch_ordering"`**：刻意选此名——`plan_review.py:61 SHORTCIRCUIT_SAFE_ISSUE_TYPES`
  注释已把 `batch_ordering` 列为「非重分类可解决、需 LLM 复检」。故 topo issue 会**禁用短路**、强制 LLM 复检，
  修不动则耗尽 `max_plan_revision_rounds` → AWAITING_HUMAN（CLAUDE.md 既定逃生口）。
- **`suggested_classification == current_classification`（= source 当前 batch risk_level）**：topo 是**顺序**问题不是
  风险重分类。planner 的 `_apply_judge_issues_to_plan` 按 `suggested_classification` 重分类——令 suggested==current
  ⇒ 即使被 accept+apply 也**不改风险**（no-op 重分类），靠 `issue_type` 表达结构问题。
  `_detect_cross_source_conflicts` 看方向差，current==suggested ⇒ dir=0 ⇒ 不产生假冲突。
- **封顶 25 条**：大型 fork 跨模块边多，无界 flag 会拖慢收敛（§8 风险）。每个 source 一条 issue（提及至多 3 个它抢先的依赖），
  总数封顶 25。**这是为压制噪音的人为上限**，可后续按真实仓库调。
- **`_merge_with_precheck` 不改**：其 `" | precheck added "` marker 被 `plan_review.py` 解析器 +
  `test_planner_judge_optimizations.py` 硬依赖；trailing 文案 "(MISMATCH/NOT-BATCHED, deterministic)" 被测试硬断言。
  topo issue 并入同一 `precheck_issues` 列表透传即可（计数自然累加）。**已知瑕疵**：summary 括注在含 topo issue 时
  文案略不精确（仍写 MISMATCH/NOT-BATCHED），但 issue 本身 `issue_type=batch_ordering` + reason 携带真相，审计无损。

### 7.2 反死代码 DoD 对照（方案 §0）

| 条件 | conflict_analyst | executor | planner_judge |
|---|---|---|---|
| (a) phase 构建/填充图 | initialize（Phase A 已建） | 同 | 同 |
| (b) ≥1 契约 inputs 声明 | judge.yaml 已声明（共享） | 同 | **planner_judge.yaml 新增** |
| (c) gate/逻辑真正读取 | enriched_context 注入 blast-radius | 两个 prompt 注入 dependents | precheck_batch_topological_order |
| (d) 单测「图非空⇒行为改变」 | 注入块出现/缺席 | dependents 段出现/缺席 | 违规序产 issue、合规序不产 |

### 7.3 临时假设 / 已知局限

- blast-radius 文件级近似 hunk 级（DB1）；God Node 阈值未标定（DB3）；topo issue 封顶 25 为人为噪音上限（DB5）。
- topo 违规不可自动修时会耗几个修订轮再 AWAITING_HUMAN（设计逃生口，非 bug）。
- 跨模块 EXTRACTED 边在大 fork 上可能多 → topo issue 偏多；线上偏噪可调 `dependency_graph.enabled: false` 整体回退。

### 7.4 变更文件清单

- [x] `src/models/dependency.py`（+DependencyImpactHint +impact_hint）
- [x] `src/models/config.py`（+god_node_min_dependents）
- [x] `src/agents/conflict_analyst_agent.py`（analyze_file +impact_hint，注入 enriched_context；+`_format_blast_radius_block`）
- [x] `src/core/phases/conflict_analysis.py`（传 impact_hint）
- [x] `src/llm/prompts/executor_prompts.py`（两个 builder +dependents/referenced_symbols；+`_format_dependents_block`）
- [x] `src/agents/executor_agent.py`（execute_semantic_merge 传 dependents/referenced；analyze_deletion 查 dependents + sentinel 协同写 risk_context）
- [x] `src/agents/contracts/planner_judge.yaml`（+dependency_graph）
- [x] `src/llm/prompts/planner_judge_prompts.py`（+precheck_batch_topological_order +`_MAX_TOPO_ISSUES`）
- [x] `src/agents/planner_judge_agent.py`（run→review_plan 透传 dependency_graph + 并入 topo precheck）
- [x] `src/core/phases/plan_review.py`（多轮修订路径 review_plan 传 dependency_graph——**主编排路径，不传则 topo 检查只在 agent.run 生效、修订轮失效**）
- [x] `src/core/phases/auto_merge.py`（dispute 修订后 review_plan 传 dependency_graph）
- [x] `tests/unit/test_dependency_graph_phase_b.py`（新建：模型/conflict_analyst/executor/planner_judge topo 16 用例）
- [x] `tests/unit/test_dependency_graph_consumers.py`（contract 声明断言 +planner_judge）

> **实现中追加的接线点（方案未列）**：`plan_review.py` 与 `auto_merge.py` 两处 `review_plan` 直呼。
> planner_judge 的 topo 检查若只在 `run()` 入口透传 graph，多轮修订（PlanReviewPhase）和 dispute 修订
> （auto_merge）会绕过 `run()` 直呼 `review_plan`、拿不到 graph → topo 检查在真实编排里失效。两处补传
> `dependency_graph=state.dependency_graph` 才真正接通（DoD (c)）。

### 7.5 实施过程记录（逐步追加）

- 2026-05-26：完成调研、确认范围与上列决策、建 Phase B 笔记章节。
- 2026-05-26：完成 step 7/8/9 代码 + 两处 review_plan 直呼补传 graph。新增/改测试：
  `test_dependency_graph_phase_b.py`（16 passed）、`test_dependency_graph_consumers.py` contract 断言扩 planner_judge。
- 2026-05-26：最终验证：`pytest tests/unit/` **2705 passed**（+16，旧 2689 无回归）；`mypy src` 165 文件 0 错（strict）；
  `ruff check src/ tests/` 通过；`test_agent_contracts.py` 37 passed（planner_judge 新 input 已声明）。
  - **维护须知**：
    - God Node 阈值 `dependency_graph.god_node_min_dependents`（默认 8）**未经真实仓库标定**，仅抬 conflict_analyst
      谨慎度；偏噪可调高，整体回退 `dependency_graph.enabled: false`。
    - topo `batch_ordering` issue 走 precheck → 强制 LLM 复检（`SHORTCIRCUIT_SAFE_ISSUE_TYPES` 不含它）；
      跨风险层/跨模块的结构性违规 planner 可能修不动 → 耗 `max_plan_revision_rounds` 轮后 AWAITING_HUMAN（设计逃生口）。
      封顶 `_MAX_TOPO_ISSUES=25` 压噪，可按真实仓库调。
    - `_merge_with_precheck` 未改：summary 括注在含 topo issue 时仍写 "(MISMATCH/NOT-BATCHED, deterministic)"
      （已知瑕疵，issue 本身 issue_type/reason 携带真相，审计无损；改它会破 `test_planner_judge_optimizations.py`
      硬断言与 `plan_review.py` 的 `" | precheck added "` marker 解析）。
    - conflict_analyst / executor **未扩 contract**：二者经 phase 参数 / 原始 state 访问 graph，不经 restricted_view；
      仅 planner_judge 因走 restricted_view 必须在 yaml 声明 `dependency_graph`。
    - blast-radius 为**文件级**近似 hunk 级（edge 是文件→文件）；hunk 级需符号行段细化，超 Phase B 范围。

---

## 8. Phase C — 增强（P2/P3）实施笔记

> 对应方案 §7 step 10/11（§6.3 import 别名/monorepo 解析、§6.4 社区检测 + God Node、§4 P3 memory/human_interface）。
> 开始日期：2026-05-26。**用户确认范围（2026-05-26）**：5 个子项全做；社区检测用**轻量 stdlib label-propagation**
> （零新依赖，图空降级路径式），**不引入 graspologic/networkx**（与 CLAUDE.md target-repo 无关 + 体积约束一致）。

### 8.0 子项清单与接入路径（grep 核实）

| 子项 | 接入点 | 访问 graph 方式 | contract 改动 |
|---|---|---|---|
| C1 God Node → planner 风险 | `planner._generate_plan` 新增 `_apply_god_node_risk` | 原始 state | 无（planner run 只做 contract 副作用断言） |
| C2 图驱动社区检测 | `module_inference.infer_communities` + `planner._assign_modules` | 原始 state | 无 |
| C3 import 别名/monorepo 解析 | `dep_extractors/alias_resolver.py` + `dependency_extractor` + `initialize` | n/a（提取期） | 无 |
| C4 memory 沉淀 hub/surprising | `memory_extractor.extract` 加确定性 `_graph_insights` | restricted_view | **memory_extractor.yaml +dependency_graph +file_categories** |
| C5 human_interface 展示 blast-radius | `HumanDecisionRequest` +字段；构建处填充 | 构建期原始 state | 无（请求预填，human_interface 只读已填请求） |

### 8.1 关键决策

#### DC1. God Node 风险提升落在 planner 的确定性后处理（C1）
`compute_risk_score`（file_classifier）不接收 graph 且须保持 target-repo 无关 + 纯规则。**决定**：新增
`planner._apply_god_node_risk(file_diffs, state)`，在 `_enhance_risk_scores` 之后、建计划之前跑：对**改动文件**取
`impact_hint`，God Node（direct_dependents ≥ 阈值）则 `risk_score = min(1.0, max(old, old + god_node_risk_bump))`
（§5 单调，只抬不降），再 `classify_file` 重导 risk_level。图空 / 非 God Node → 原样返回（安全降级）。
- 配置：`DependencyGraphConfig.god_node_risk_bump: float = 0.15`（默认开启才算真消费者/DoD；仅 God Node 触发、罕见，
  不影响图空的既有测试）。**未标定**，偏噪可调 0 关闭。
- 纯函数：返回新 list（不可变），state 写仍在 `run()`。

#### DC2. 社区检测 = stdlib label-propagation，opt-in 经 `ModuleConfig.mode == "graph"`（C2）
`infer_modules` 用 container_dirs 路径拓扑。**决定**：`module_inference.infer_communities(graph, file_paths,
fallback_modules)`：无向化 EXTRACTED+INFERRED 边 → 异步 label-propagation（确定性：初始 label=自身、每轮取邻居
众数 label 平局取**最小 label**、max_iters 上限、节点按序遍历）→ 每个社区命名为该社区内**路径拓扑模块众数**
（平局取最小），使名字仍人类可读且与 `module_depends_on` 排序兼容。无边节点回退其 `infer_modules` 模块。
- 接入：`ModuleConfig.mode` 增 `"graph"`；`planner._assign_modules` 在 `mode=="graph"` 且 `graph.edges` 时调
  `infer_communities`，否则原样 `infer_modules`。**默认 mode 仍 "auto"**（零行为变化、无回归）。
- 为何不用 Leiden/graspologic：用户确认避免重依赖；label-propagation 纯 stdlib、O(边·迭代)、对"模块边界"这种
  粗聚类足够（不追求模块度最优）。

#### DC3. import 别名/monorepo 解析：新 `alias_resolver`，默认关闭（C3）
**决定**：新增 `src/tools/dep_extractors/alias_resolver.py`，从 scope 内的配置文件解析三种生态：
- **tsconfig.json / jsconfig.json**：`compilerOptions.baseUrl` + `paths`（`@app/*` → `src/app/*`）。容错解析
  （allow `//` 注释 / 尾逗号？仅做最小：标准 JSON，失败则跳过该文件）。
- **go.mod**：`module <prefix>` → 裸 import 以 `<prefix>/` 开头者去前缀映射到仓库相对路径。
- **package.json**：`workspaces` globs（仅记录 workspace 根，用于裸 import 落到 workspace 包）。
- 接入：`DependencyExtractor.extract_from_sources(sources, languages, alias_map=None)`；treesitter resolver 先试
  alias_map 再试相对/裸。`initialize._build_dependency_graph` 在 `cfg.resolve_aliases` 时额外读仓库根的配置文件
  （不计入 max_files scope）构建 alias_map。
- 配置：`DependencyGraphConfig.resolve_aliases: bool = False`（§6.3 要求默认关、可配；保持 target-repo 无关）。
- 默认关 → 既有行为逐字节不变。

#### DC4. memory 的 graph 洞察走**确定性**而非 LLM（C4）
God Node / surprising-connection 由图确定性可得，不必烧 LLM。**决定**：`memory_extractor.extract` 末尾追加
`_graph_insights(view)`（确定性，`confidence_level=EXTRACTED`）：
- **God Node**：改动文件中 direct_dependents ≥ 阈值者 → `CODEBASE_INSIGHT`（"hub 文件，N dependents，谨慎"）。
- **surprising connection**：改动文件间**跨顶层目录**的 EXTRACTED 边 → `RELATIONSHIP`（"X 依赖 Y，跨目录耦合"）。
  （轻量代理；不依赖 C2 社区，避免耦合 mode 开关。）
- 与 LLM entries 合并、按 `content_hash` 去重、整体仍受 `max_insights` 上限。
- contract：`memory_extractor.yaml` +`dependency_graph` +`file_categories`（extract 用 restricted_view，必须声明，
  否则 `FieldNotInContract` 静默吞 → 死代码，正是 `project_judge_dead_contract_checks` 的坑）。

#### DC5. human_interface 决策卡：请求预填字段，agent 不改 contract（C5）
**决定**：`HumanDecisionRequest` 加 `dependents_count:int=0` / `blast_radius:int=0` / `is_god_node:bool=False`
（默认 0/false → 向后兼容）。在**构建决策请求**处（`conflict_analysis._build_human_decision_request` 加可选
`impact_hint` 参；`auto_merge` 直构造处）用 `state.dependency_graph.impact_hint` 填充，并把一行 blast-radius
摘要追加进 `context_summary`（确保被渲染 = DoD c）。human_interface agent 只读已填请求 → **不需改其 contract**
（数据在构建期已 baked in，与 analyst_recommendation 等字段同模式）。

### 8.2 配置新增汇总
- `DependencyGraphConfig.god_node_risk_bump: float = 0.15`（C1）
- `DependencyGraphConfig.resolve_aliases: bool = False`（C3）
- `ModuleConfig.mode` 增 `"graph"` 取值（C2，默认仍 "auto"）

### 8.3 反死代码 DoD 对照
| 子项 | (a) 构建 | (b) 契约声明 | (c) 真正读取 | (d) 图非空⇒行为变 |
|---|---|---|---|---|
| C1 | initialize | planner 原始 state（无需声明） | `_apply_god_node_risk` 抬分 | God Node 文件 risk 上调/重分类 |
| C2 | initialize | planner 原始 state | `infer_communities` | mode=graph 下分组按真实边变化 |
| C3 | 提取期 | n/a | resolver 用 alias_map | 别名 import 解析出边（开关开时） |
| C4 | initialize | **memory_extractor.yaml 新增** | `_graph_insights` | 图非空产 hub/surprising memory |
| C5 | initialize | 构建期原始 state（无需声明） | 请求字段 + context_summary | dependents/blast 字段非零、摘要出现 |

### 8.4 变更文件清单
- [x] `src/models/config.py`（+god_node_risk_bump +resolve_aliases；ModuleConfig.mode +"graph"）
- [x] `src/agents/planner_agent.py`（+_apply_god_node_risk 接入 _generate_plan；_assign_modules graph 模式）
- [x] `src/tools/module_inference.py`（+infer_communities label-propagation +_modal_module）
- [x] `src/tools/dep_extractors/alias_resolver.py`（新建：AliasMap + build_alias_map，tsconfig/go.mod/package.json，JSONC 容错）
- [x] `src/tools/dependency_extractor.py`（extract_from_sources +alias_map）
- [x] `src/tools/dep_extractors/treesitter_extractor.py`（_resolve/extract_imports +alias_map，relative/go 失败再走 alias）
- [x] `src/core/phases/initialize.py`（resolve_aliases 时 _collect_alias_configs 建 alias_map；os.walk 剪枝 +cap 400）
- [x] `src/agents/memory_extractor_agent.py`（+_graph_insights 确定性洞察 + budget 共享）
- [x] `src/agents/contracts/memory_extractor.yaml`（+dependency_graph +file_categories）
- [x] `src/models/human.py`（HumanDecisionRequest +dependents_count/blast_radius/is_god_node）
- [x] `src/core/phases/conflict_analysis.py`（_build_human_decision_request +impact_hint 填充 + 调用处传 hint）
- [x] `src/core/phases/auto_merge.py`（O-L3 人工请求构造处填 blast-radius + context_summary 摘要）
- [x] `tests/unit/test_dependency_graph_phase_c.py`（新建：5 子项 19 用例 + 安全降级）
- [x] `tests/unit/test_dependency_graph_consumers.py`（contract 断言 +memory_extractor dependency_graph/file_categories）

### 8.5 实施过程记录
- 2026-05-26：确认范围（5 子项全做 + stdlib 社区检测）、建 Phase C 笔记章节。
- 2026-05-26：实现 C1–C5 + 测试。实现中细节补充：
  - **C2 fallback 命名**：`_assign_modules` 的 "graph" 分支用 `cfg.model_copy(update={"mode":"auto"})` 算 fallback
    模块名——因 `infer_modules` 的 topology 分支仅 `mode in ("auto","graph")` 触发（已同步放开 "graph"），
    但社区命名要的是 explicit>rewritten>topology 全链，用 auto 副本最稳。
  - **C3 alias 仅作用于非 Python**：treesitter 提取器消费 alias_map；Python `ast` 提取器走自身 module_index 未接 alias
    （Python import 别名罕见、且 `_resolve_module` 前缀搜索已较强）。记为范围限定。
  - **C3 go 解析**：`AliasMap.resolve_go` 要求 `pkg_dir == rest`（精确目录等值），比 treesitter 内置 `_resolve_go`
    的 `endswith` 更严，避免跨包误匹配；内置 endswith 作为无 alias 时的兜底保留。
  - **C4 budget 共享**：`extract` 先取确定性 graph insights（封顶 max_insights），LLM 只填 `remaining`。
    图空 ⇒ graph=[] ⇒ remaining=max_insights ⇒ 与旧行为逐字节一致；图满 ⇒ 可能跳过 LLM 调用（省成本，可接受）。
  - **C5 不改 human_interface contract**：决策卡字段在**构建期**（conflict_analysis/auto_merge，原始 state）预填，
    human_interface agent 只读已填请求 → 无需扩其 contract（与 analyst_recommendation 等字段同模式）。
- 2026-05-26：最终验证：`pytest tests/unit/` **2724 passed**（+19 Phase C，旧 2705 无回归）；`mypy src` 166 文件 0 错；
  `ruff check src/ tests/` 通过；`test_agent_contracts.py` 37 passed（memory_extractor 新 input 已声明）。
  - **维护须知**：
    - C1 `god_node_risk_bump`(默认 0.15)、`god_node_min_dependents`(8) 均**未标定**；偏噪调 0 / 调高阈值。仅抬不降(§5)。
    - C2 社区检测 **opt-in**：`module_config.mode: graph`（默认 "auto" 行为不变）。label-propagation 确定性
      （sorted 遍历、平局取 min label、max_iters=20）；不追模块度最优，只求粗聚类。AMBIGUOUS 边被 planner 滤除。
    - C3 **opt-in**：`dependency_graph.resolve_aliases: true`。开启才 os.walk 收集配置(cap 400，剪 node_modules 等)；
      默认关 → 零额外 IO、行为不变。仅 JS/TS/Go 受益（Python 未接 alias）。
    - C4 graph insights `confidence_level=EXTRACTED`(AST 确定)，与 LLM inferred 区分；surprising = 跨顶层目录 EXTRACTED 边
      （轻量代理，不依赖 C2 社区，避免耦合 mode 开关）。
    - C5 字段默认 0/false → 向后兼容；图空时决策卡无 "Dependency impact" 行。
