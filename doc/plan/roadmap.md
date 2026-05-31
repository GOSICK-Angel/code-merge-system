# Code Merge System — Roadmap

本文档跟踪 Planner / PlannerJudge / HUMAN_REQUIRED UX 改造序列里 **已落地** 的工作，以及 **下一步** 的待办项。每一项都标注了优先级、依赖、预估工作量、风险面，以便单独拆分 PR 评审。

最后更新：2026-05-19

---

## 已落地（Round 0 → Round 3）

| 轮次 | 主题 | 关键改动 | 已交付 |
|---|---|---|---|
| R0 (P0) | 报告 / 风险评分 / Manifest 修复 | directory matrix bug；plan report 二次覆盖；C-class `risk_score` floor；PlannerJudge manifest 加 fork/upstream/regions 信号 | ✓ |
| R1 (P1) | Rule 6 词典 / safelist 短路 / 协商软化 | 词典扩展、segment safelist round_log、`filter_obviously_safe_files`、批次按目录二次分组、Planner REJECT 软化 | ✓ |
| R2 (UI 视觉) | hunk 渲染 + 触发原因分类 | `conflict_preview` 渲染、`_classify_human_required_reason` 区分 policy/conflict/large_diff | ✓ |
| R2 (选项扩展) | `union_additions` / `llm_with_instruction` | DecisionOption schema 扩展（kind+preview）；UserDecisionItem 加 `custom_instruction`；Executor L5 dispatch；UI kind-aware textarea | ✓ |
| R3 (人审三件套) | `manual_paste` / `skip` / `analyst_proposed` | UserDecisionItem 加 `manual_resolution`；ConflictAnalyst.propose_decision_options（opt-in 配置 `analyst_decision_options_enabled`）；UI paste/skip 模式 | ✓ |
| 顺手修 | `**/foo` glob 漏 root；DEFAULT_LAYERS 补齐 lockfile | `_matches_layer` 加 `pattern.startswith("**/")` 回退；补 `**/go.sum` / `**/package-lock.json` / `**/pnpm-lock.yaml` | ✓ |

---

## 下一步（暂缓项 → 待排期）

### P1 — `per_hunk` 决策选项 *(Round 3 deferred)*

> 详细设计见 **[per-hunk-resolution.md](./per-hunk-resolution.md)**

**摘要**：让审查者对 HUMAN_REQUIRED 文件按 hunk（甚至按 conflict region）逐块选择 fork / upstream / base / 自定义内容，组装出最终文件并写盘。当前 4 类选项粒度都是 *整文件*，对"前一半跟 fork、后一半跟 upstream"这种诉求无解。

| 项 | 评估 |
|---|---|
| 优先级 | P1（高价值，但 UI 投入大） |
| 估时 | 后端 ~1.5 天 + 前端 ~3 天 + 测试 1 天 |
| 依赖 | 无（Round 3 的 schema 已足以承载） |
| 风险面 | 中：新 UI 视图 + 跨 fork/upstream/base 的 hunk 对齐算法；写盘必须用 `apply_with_snapshot` 走 quality gate |
| 单 PR | ✗ 建议拆三段：① 后端 hunk API + 装配算法 ② UI hunk picker 视图 ③ L5 dispatch 接管 |

---

### P2 — Layer 拓扑细化

**现状**：`DEFAULT_LAYERS` 只有 3 层（infra → deps → everything_else），L2 是 `**` catch-all，吞下所有源码。配合 #3 目录二次分组，L2 已经按 top-level dir 切成多个 sub-batch，但 **层间依赖** 没体现 —— 比如 forgejo 这种 monorepo 其实应该是 `models → services → routers → templates/web_src → tests`。

| 项 | 评估 |
|---|---|
| 优先级 | P2（增益边际，需要数据支撑） |
| 估时 | 1.5 天（含 forgejo / dify-plugins 两个 target 的 layer pattern 集合调优） |
| 依赖 | 推荐先有 1-2 个跑通的真实 auto_merge run（forgejo 当前停在 AWAITING_HUMAN），观察 batch 执行顺序的实际痛点 |
| 风险面 | 高：改 `DEFAULT_LAYERS` 影响所有 target。新增的 layer pattern 需要逐 target 验证不漏文件 / 不打乱依赖。`tests/unit/test_planner_layer_matching.py` 已有 root deps 回归用例可复用。 |
| 单 PR | 建议加 `coordinator.layer_profile: Literal["minimal","full","monorepo"]` 让用户显式选，默认仍是 `minimal`（当前 3 层），新 profile opt-in 评估。 |

**建议拆分**：
1. **观察阶段**：先跑 1-2 个真实 auto_merge run 到完成，记录 batch 失败/回滚是否真和层序相关
2. **数据驱动**：在 forgejo / dify-plugins 上跑统计，看哪类源码层失败常见
3. **方案**：要么 `monorepo` profile 拆出 `data → service → http → ui → tests`，要么允许 yaml 自定义 layers（已有 `LayerConfig.custom_layers` 字段，只需文档化）

---

### P2 — Project Context → LLM 摘要

**现状**：`merge_plan_report.md` 头部的 "Project Context" 直接塞 README raw 截断，包含 forgejo logo 的 HTML、Esperanto 字源说明等噪声。PlannerJudge / ConflictAnalyst 看到的也是这堆噪声。

| 项 | 评估 |
|---|---|
| 优先级 | P2（影响所有 prompt 质量，但成本/收益要先 quantify） |
| 估时 | 1 天 |
| 依赖 | 无 |
| 风险面 | 中：每个 run 增加 1 次 LLM 调用；需要 opt-in 开关（`initialize.project_context_summarize: bool = False`） |
| 单 PR | 单独 PR；落地一个新 agent gate `INIT-PROJECT-SUMMARY` 在 initialize phase 末尾跑 |

**设计草图**：
- Initialize phase 抓到 README/CLAUDE.md 后，如果 `project_context_summarize_enabled = True` 且原文 > 800 字，走一次轻量 LLM 调用（Haiku 即可）压成 ≤ 200 字结构化摘要：
  - 项目类型（CLI / web framework / SDK / data pipeline / ...）
  - 主要语言 + 框架
  - 关键领域词汇（forgejo: "git hosting, OAuth, federation, ActivityPub"）
- 摘要存入 `state.user_project_context`，下游 prompt 全部受益
- 失败回退到 raw README 截断（与现状一致）

---

## 跟踪中的小型改进 ideas（不是优先级排序，按发现顺序）

这些是实施 Round 2/3 时浮出来的小问题，足以单独 PR：

1. **forgejo 测试基线扩展** — 当前测试分支只覆盖 3 个 C-class auth 文件。建议加 2-3 类典型场景：
   - 双侧 add-only 不同方法到同 class（验证 `union_additions` 路径）
   - 双侧改同一函数签名（验证 `analyst_proposed` 是否能提出有意义的方案）
   - 一侧重命名、一侧改内容（验证 rename 跟踪）
   - 文档：`doc/test-report/forgejo-c-class-test-branches-2026-05-18.md` 已有 baseline

2. **`c_class_risk_floor` 数据调优** — 当前默认 0.40 是直觉值，建议跑 5-10 个不同 fork / merge_base 距离的 run，统计 C-class 文件真实 risk_score 分布、决定是否调低到 0.35 或加分段（小 fork-delta + 小 upstream-delta 不 floor）。

3. **`analyst_proposed` 选项的 evaluation suite** — Round 3 落地了 opt-in 调用，但缺乏对"analyst 给出的方案有多大比例被人采纳"的度量。建议加：
   - `state.pending_user_decisions` 里记录 `option_kind_selected`
   - report 末尾汇总 "X% of HR files: reviewer picked analyst proposal vs base ladder"
   - 跑几个真实 run 后再决定是否把 `analyst_decision_options_enabled` 默认翻到 True

4. **conflict_preview 完整 diff** — 当前 50 行截断在大文件上可能不够。两个方向：
   - UI 端"展开全文"按钮，按需 fetch 完整 raw_diff
   - 智能裁剪：仅截留含 hunk 的窗口 ± 上下文 5 行

5. **`analyst::<key>` 选项的稳定性** — 当前 LLM 每次生成的 key 不稳定（一次 `merge-additions`，下次 `keep-both`）。如果用户分多次 review 同一文件（resume），选项面板会跳。建议把 `analyst_proposed` 选项的 raw text 存入 `state.pending_user_decisions[].options`（已经存了）+ resume 时优先复用 cached 而不是重新调用 analyst。

---

### P1 — 二开前置评估：`merge assess` + DesignReviewAgent

> 详细设计见 **[pre-dev-assessment.md](./pre-dev-assessment.md)**

**摘要**：系统当前只在 fork 有实际 diff 后才介入。本项将介入点提前到**设计阶段**——开发者
提交代码之前，用 `merge assess <design-doc>` 分析设计文档的合并友好性。系统提取设计中
涉及的文件、量化每个文件的 upstream churn 风险、由 DesignReviewAgent 评估 seam 化程度
和 flag-off 安全性，生成 `.merge/dev-profile.yaml`（含自动推导的 seam_anchors）。
后续 merge / merge sync 运行自动消费该产物，实现设计期信息到合并期的闭环传递。

| 项 | 评估 |
|---|---|
| 优先级 | P1（工作流入口，其余优化项的上游） |
| 估时 | 3.5 天（E.1–E.5，可分 PR） |
| 依赖 | E.4（dev-profile 模型）为 A 项 seam_anchors 的前置 |
| 风险面 | 低：dev-profile 缺失时系统降级为当前行为，完全可选 |
| 单 PR | 拆 3 个 PR：① E.2+E.4 工具层 ② E.1+E.3 CLI+Agent ③ E.5 Phase 集成 |

---

### P1 — Seam Anchor 声明 + Judge 确定性检查

> 详细设计见 **[upstream-sync-automation.md § A](./upstream-sync-automation.md)**

**摘要**：在 `.merge/config.yaml` 新增 `seam_anchors` 字段，让用户声明 fork 对 upstream 的
集成接缝（关键符号 + 必要参数名）。Initialize Phase 在上游 HEAD 中验证这些符号是否存在及签名是否完整，
结果写入 `state.seam_anchor_breaks`；Judge 的确定性 pipeline 新增 `_check_seam_anchors()`，
按 `on_break` 配置（`veto` / `warn` / `human_required`）路由处理。

| 项 | 评估 |
|---|---|
| 优先级 | P1 |
| 估时 | 1.5 天（含单测） |
| 依赖 | 无 |
| 风险面 | 低：`seam_anchors: []` 默认空，现有运行零影响；符号提取复用 interface_change_extractor |
| 单 PR | ✓ 可单独合入 |

---

### P2 — 平台迁移风险信号（R0 等效）

> 详细设计见 **[upstream-sync-automation.md § C](./upstream-sync-automation.md)**

**摘要**：Initialize Phase 扫描上游最近 N 个 commit message，匹配预配置的风险模式（Go 重写、
API 废弃、breaking change 等），将匹配结果写入 `state.platform_risk_signals`，在 plan report
首页渲染警告框。不阻断合并，仅作人工提示。

| 项 | 评估 |
|---|---|
| 优先级 | P2 |
| 估时 | 0.5 天 |
| 依赖 | 无 |
| 风险面 | 极低：只读 git log，不影响任何决策路由 |
| 单 PR | ✓ 可单独合入 |

---

### P2 — `sync_mode` + `merge sync` 子命令

> 详细设计见 **[upstream-sync-automation.md § B & D](./upstream-sync-automation.md)**

**摘要**：`MergeConfig` 新增 `sync_mode: Literal["feature","upstream"]`，`upstream` 模式激活
Planner 专用 Gate（`P-SYNC-001`），指导 Planner 对高 churn 主线文件优先跟 upstream、仅在 seam
调用点保留 fork 改动。`merge sync` 子命令封装完整上游同步工作流（锚点预检 → sync_mode 合并 → 后处理提示）。

| 项 | 评估 |
|---|---|
| 优先级 | P2 |
| 估时 | 1.5 天合并（B 1 天 + D 0.5 天） |
| 依赖 | B+D 可先落，A 项的 seam_anchors 字段完善后 Planner 提示更精确 |
| 风险面 | 低：`sync_mode` 默认 `"feature"`，CLI 封装不改核心逻辑 |
| 单 PR | B+D 合并一个 PR |

---

## 文档检索索引

| 文档 | 内容 |
|---|---|
| [per-hunk-resolution.md](./per-hunk-resolution.md) | per_hunk 决策选项的完整设计（schema / UI / dispatch / 测试 / 风险） |
| [merge-safety-complete.md](./merge-safety-complete.md) | merge assess / DesignReviewAgent / Seam Anchor / sync_mode / CI Gate / Skill+Hook 完整落地方案（A–F 项） |
| `doc/architecture.md` | 系统总体结构 |
| `doc/risk-levels.md` | risk_score → risk_level 阈值与决策路由 |
| `doc/evaluation/` | 决策准确率 / 性能基线 / 数据集定义 |
| `doc/test-report/` | 历次 forgejo / dify-plugins 测试记录 |
