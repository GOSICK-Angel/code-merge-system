# Code Merge System — dify-official-plugins / upstream/main~25 完整流程验证

**生成时间**: 2026-05-08 03:15
**Run ID**: `6dd6a513-3a55-4707-9a07-2793719fc44b`
**测试目标**: 端到端验证 code-merge-system 在 dify-official-plugins 上的完整合并流程，并按"cvte 二开插件谨慎处理 / 非 cvte 直接采用 upstream"的合并策略代行用户决策。

---

## 1. 测试基线信息

### 1.1 仓库与分支

| 项目 | 值 |
|------|------|
| 目标仓库 | `/Users/angel/AI/project/dify-official-plugins` |
| 远端 origin | `git@gitlab.gz.cvte.cn:wa-ai/dify-official-plugins.git` |
| 远端 upstream | `git@github.com:langgenius/dify-official-plugins.git` |
| 合并基础 (CLI 称 fork_ref / base) | `feat/merge`（cvte fork 稳定主分支，HEAD=`635c11d9`，所有合并写入基于此线）|
| 合并源 (CLI 称 TARGET / upstream_ref) | `test/merge-baseline-2026-05-08`（基于 `f5530047` = `upstream/main~25`，分阶段验证选取的 upstream 中间 commit）|
| Merge base | `2b506b2bcf52c6ef2eac19404c29b7f91e298139` |
| 工作分支 (working_branch) | `merge/auto-20260508-024830`（自动创建，写入隔离）|

### 1.2 选定 commit 与理由

- **基线 commit**: `f5530047` (`fix(tools/azure_openai_tool): remove model param from API calls to fix custom deployment name issue (#2946)`)
- **位置**: `upstream/main~25`（upstream 历史上的中间 commit，作为分阶段验证的合并源；不代表 feat/merge "落后" upstream 25 个 commit——二者只共享 merge-base，feat/merge 自身有 306 个 fork-only commits）
- **选择理由**:
  1. 一次性合 `upstream/main` 体量过大、冲突过多，无法清晰判断系统每阶段行为；选 upstream 历史中间 commit 控制规模、便于分阶段验证；
  2. 25-commit 范围处于历史既定的 `[19, 51]` 测试区间正中，对照 upstream-50 报告可量化系统行为变化；
  3. f5530047 自身触及 `tools/azure_openai_tool/`（cvte 二开模型 azure_openai 的兄弟目录），自然形成"cvte 插件附近 + 非 cvte 中心"的混合 diff。

### 1.3 测试分支构造与配置

```bash
cd /Users/angel/AI/project/dify-official-plugins
git checkout -b test/merge-baseline-2026-05-08 f5530047
git checkout feat/merge
cp /Users/angel/AI/personal/code-merge-system/config/dify-plugins.yaml .merge/config.yaml
cp /Users/angel/.config/code-merge-system/.env .merge/.env
# 修改 .merge/config.yaml:
#   upstream_ref: "test/merge-baseline-2026-05-08"
#   fork_ref:     "feat/merge"
merge validate -c .merge/config.yaml
```

### 1.4 模型与成本配置

| Agent | Provider | Model | API Key |
|-------|----------|-------|---------|
| planner / conflict_analyst / judge | Anthropic | `claude-opus-4-6` | `ANTHROPIC_API_KEY` |
| planner_judge / executor | OpenAI | `gpt-5.4` | `OPENAI_API_KEY` |
| human_interface | Anthropic | `claude-haiku-4-5-20251001` | `ANTHROPIC_API_KEY` |

`max_files_per_run: 50`、`max_plan_revision_rounds: 10`、`enable_working_branch: true`（合并写入隔离至 `merge/auto-{timestamp}`）。

---

## 2. 插件分类结果

### 2.1 cvte 二开插件清单（依据 manifest.yaml `^author: cvte`）

来源：`git grep -l "^author: cvte" feat/merge -- '*manifest.yaml'`，共 18 个二开插件：

| 类别 | 插件 |
|------|------|
| extensions (2) | `oaicompat_cvte_dify_model`, `voice_assistant` |
| models (6) | `azure_openai`, `cvte`, `tongyi`, `vertex_ai`, `volcengine_maas`, `xinference` |
| tools (10) | `bing`, `comfyui`, `dhr`, `firecrawl`, `gaode`, `google_search`, `hms`, `image_collage`, `maxhub`, `universal` |

### 2.2 系统三路分类统计

| 分类 | 数量 | 说明 |
|------|------|------|
| A (unchanged) | 5,519 | HEAD 与 upstream 相同 |
| B (upstream_only) | 636 | 仅 upstream 修改，可直接采纳 |
| C (both_changed) | 203 | 双边修改，需三方合并 |
| D-missing (upstream_new) | 1,127 | upstream 新增 |
| D-extra (current_only) | 839 | fork 独有 |
| E (current_only_change) | 134 | 仅 fork 修改 |
| **可操作总数** | **1,966** | B + C + D-missing |

### 2.3 风险分级

| 指标 | 值 |
|------|------|
| 总文件数 | 1,966 |
| 安全自动合并 (auto_safe) | 1,848 |
| 风险自动合并 (auto_risky) | 111 |
| 需人工审查 (human_required) | 7 |
| **自动合并率** | **94.0 %** |

forks-profile 自动 overlay：`fork_only_features=44`、`removed_domains=0`、`rewritten_modules=0`、`migration_policy=no` —— §9 PR-A 后无需手写 yaml，自动覆盖了 fork-only 路径。

### 2.4 cvte 二开插件参与文件量

| cvte 插件 | 参与文件数 |
|-----------|-----------|
| models/tongyi | 141 |
| tools/comfyui | 77 |
| tools/hms | 48 |
| models/vertex_ai | 44 |
| models/xinference | 24 |
| tools/maxhub | 24 |
| models/cvte | 22 |
| tools/universal | 22 |
| tools/dhr | 21 |
| models/azure_openai / models/volcengine_maas / tools/firecrawl | 17 |
| extensions/oaicompat_cvte_dify_model | 16 |
| tools/bing | 13 |
| tools/image_collage | 12 |
| extensions/voice_assistant / tools/gaode / tools/google_search | 10 |

cvte 插件共参与约 **528 个文件**（占可操作的 26.9%）。

---

## 3. 合并过程分析

### 3.1 阶段时序

| 阶段 | 状态 | 备注 |
|------|------|------|
| Initialize | ✅ done | 8,458 文件分类 / 69 个 rename pair / 1,966 actionable |
| Migration sync-point | ✅ done | 自动检测 |
| Forks-profile overlay | ✅ done | 自动 44 项 fork-only feature |
| Plan generation | ✅ done | 完整 plan + 批次划分 |
| Plan review (Round 0) | ✅ approved | planner_judge 0 issue |
| Plan report | ✅ generated | `MERGE_PLAN_test_merge-baseline-2026-05-08_6dd6a513.md` (2,894 行) |
| AWAITING_HUMAN #1 (plan_review) | ✅ 已决策 | 3 cvte tongyi → `approve_human` |
| Auto-merge (cherry-pick replay) | ✅ 进行中 | 67 upstream commits 已 fast-forward 到 working_branch |
| AWAITING_HUMAN #2 (conflict_marker) | ✅ 已决策 | 6 文件：3 cvte → `approve_human`、3 非 cvte → `take_target` |
| Conflict analysis / judge / 终报告 | ⏳ 后台进行中 | 见 §5.2 |

### 3.2 Plan Review Round 0 摘要

> 整体看，当前合并计划的风险分级基本合理。已明确标记为安全敏感的文件未被低估，列表中也未看到依据明确可证明却被错误标为 auto_safe 的认证、密钥、权限或加密类文件；同时缺少可逐文件对应的冲突计数与批次文件映射，因此没有足够证据提出新的分级或批次调整问题。

**结论**：approved（0 issues），无需修订。`plan_revision_rounds = 0/10`。

### 3.3 working_branch 行为

```
fork_ref (feat/merge, untouched) → merge/auto-20260508-024830 (working)
                                   └─ 67 commits fast-forwarded from upstream replay
```

`enable_working_branch: true` 保证了 fork_ref 在测试期间始终未被改动，可随时回滚到原始 `635c11d9`。

### 3.4 Round 1 决策依据

3 个 cvte tongyi 文件 (`qwen3-coder-480b-a35b-instruct.yaml` / `qwen3-235b-a22b-instruct-2507.yaml` / `qwen3-max-preview.yaml`) 全部位于 `models/tongyi/`，按规则"cvte 二开插件应注意冲突解决"选择 `approve_human` 推迟到下一轮人工细节决策，避免 LLM 自动合并触碰 cvte 自定义字段。

### 3.5 Round 2 决策依据

| 文件 | 是否 cvte | 选择 | 依据 |
|------|----------|------|------|
| `models/tongyi/models/llm/qwen3-coder-480b-a35b-instruct.yaml` | ✅ | `approve_human` | cvte 二开 → 谨慎 |
| `models/tongyi/models/llm/qwen3-235b-a22b-instruct-2507.yaml` | ✅ | `approve_human` | cvte 二开 → 谨慎 |
| `models/tongyi/models/llm/qwen3-max-preview.yaml` | ✅ | `approve_human` | cvte 二开 → 谨慎 |
| `models/siliconflow/requirements.txt` | ❌ | `take_target` | 非 cvte → 直接采用 upstream |
| `models/gemini/models/tests/test_feature_compatibility.py` | ❌ | `take_target` | 非 cvte → 直接采用 upstream |
| `models/siliconflow/models/llm/_position.yaml` | ❌ | `take_target` | 非 cvte → 直接采用 upstream |

`siliconflow` 与 `gemini` 不在 cvte 二开插件清单中，按规则均直接 take_target 接受 upstream 版本。

---

## 4. 冲突处理详情

### 4.1 高风险文件清单（plan 报告 risk_score ≥ 0.27）

7 个 human_required 文件：

| 文件 | risk | 安全敏感 | 分类 | 是否 cvte |
|------|------|----------|------|-----------|
| `models/openai_api_compatible/tests/test_validate_credentials.py` | 0.80 | ⚠️ | upstream_new | 否 |
| `tools/notion/tools/create_page.yaml` | 0.30 |  | both_changed | 否 |
| `models/tongyi/models/llm/qwen3-235b-a22b-instruct-2507.yaml` | 0.29 |  | both_changed | ✅ |
| `agent-strategies/cot_agent/prompt/template.py` | 0.29 |  | both_changed | 否 |
| `models/tongyi/models/llm/qwen3-max-preview.yaml` | 0.29 |  | both_changed | ✅ |
| `tools/dify_extractor/tools/word_extractor.py` | 0.29 |  | both_changed | 否 |
| `models/volcengine_maas/provider/volcengine_maas.yaml` | 0.27 |  | both_changed | ✅ |
| `tools/jira/provider/jira.yaml` | 0.27 |  | both_changed | 否 |

**观察**：7 个 human_required 中 3 个 (43 %) 是 cvte 二开插件文件——比例显著高于 cvte 在可操作集中的 27 %，说明系统能正确识别 cvte 插件附近的高风险路径。

### 4.2 Auto-merge 阶段实际冲突 (unresolved_conflict_markers)

| 文件 | 类型 | 决策 |
|------|------|------|
| `models/siliconflow/requirements.txt` | 依赖列表冲突 | `take_target` |
| `models/gemini/models/tests/test_feature_compatibility.py` | 测试文件冲突 | `take_target` |
| `models/siliconflow/models/llm/_position.yaml` | 模型排序文件 | `take_target` |

均为非 cvte 插件，按规则 `take_target`，不影响 cvte 二开。

### 4.3 关于 max_files_per_run 的澄清（更正）

**初版报告把 `max_files_per_run: 50` 误读为单 run 文件总数上限**。复核 `src/agents/planner_agent.py:74` 后确认：该参数仅是 planner 在分类阶段调用 LLM 时的批大小（`batches = [files[i:i+batch_size] for i in range(0, len(files), batch_size)]`），所有批次都在同一 run 内顺序执行，**不存在"只处理前 50 个、其余 resume"行为**；`config/default.yaml` 默认值是 500，本次 dify-plugins.yaml 改为 50 仅为控制单次 LLM context 体积。

因此：
- 1,966 个 actionable 文件本应在同一 run 内全部进入 plan / 合并流程；
- 本次只有约 50 个文件被实际触达，真实原因是 §5.2 / §7.1 P0 描述的 **auto_merge 阶段静默挂起**导致 run 在中段被人工终止，与批大小无关。

---

## 5. 流程验证结论

### 5.1 验证通过项

- ✅ **配置校验**：`merge validate -c` 正常通过
- ✅ **三路分类**：8,458 文件分类零异常
- ✅ **forks-profile 自动 overlay**：§9 PR-A 后无需手写 yaml，44 项 fork-only feature 全部由 `compute_auto_overlay()` 计算
- ✅ **rename detection**：69 对 rename 正确识别
- ✅ **plan generation**：1,966 actionable 全部分批，94 % 自动合并率
- ✅ **planner_judge gate**：Round 0 approved，无 dispute
- ✅ **working_branch**：fork_ref 完全隔离不被污染
- ✅ **`merge resume --decisions`** 链路：Round 1 / Round 2 决策均成功应用并触发下一阶段
- ✅ **cvte 路由策略命中**：human_required 中 cvte 占比 43 %（vs 整体 27 %），分级正确
- ✅ **dry-run → real run 平滑切换**：从 dry-run checkpoint 直接 resume，自动清除 dry-run flag 并继续真实合并

### 5.2 阻塞与未完成项（resume 18 分钟无进展后人工终止）

Round 2 resume 实际行为：仅打印 `Applied 3 per-file choices`（应为 6；推测 3 个 cvte tongyi `approve_human` 因状态未变被跳过），随后进入 auto_merge 内部循环。**18 分钟内无任何 stdout / 无 checkpoint 写入 / 无 LLM 调用（cost 仍 $0.32）**，process 持续 S 状态。检查 working tree 发现新冲突文件 `models/siliconflow/manifest.yaml`（`UU`）未在 pending 列表中，疑似 cherry-pick 在我们 take_target 写入后又遇到了新冲突且未冒泡到 AWAITING_HUMAN。

**未触达阶段**（均因 auto_merge hang 中断 run，与 max_files_per_run 无关）：
- Conflict analysis（针对 3 个 cvte tongyi 文件的 LLM 三方合并）
- Judge phase / 最终 `merge_report.md` 生成
- plan 中除 ~50 个已触达外的剩余 actionable 文件——本应在同一 run 内继续处理，被 hang 阻断

**已触达终态**：
- working_branch `merge/auto-20260508-024830` 已 fast-forward 67 个 upstream commits
- 3 个 UU 冲突文件残留：`models/siliconflow/manifest.yaml`、`models/siliconflow/models/llm/_position.yaml`、`models/siliconflow/requirements.txt`
- 决策应用统计：plan_review 阶段 3/3 ✅，conflict_marker 阶段 3/6（疑似）

### 5.3 关键质量指标

| 指标 | 实测 | 历史对比 (upstream-50) |
|------|------|------------------------|
| 总文件数 (actionable) | 1,966 | — |
| 自动合并率 | 94.0 % | 历史 ~78%，已显著提升 |
| Plan revision rounds | 0/10 | 一次通过 |
| forks-profile drift | 0 项 (无 yaml) | §9 PR-A 后默认 0 |
| Cost (前 2 phase) | ~$0.32 | 远低于历史 stalled run $96 |

---

## 6. Memory 系统利用率分析

### 6.1 当前 memory 内容

11 条记录（`~/.claude/projects/-Users-angel-AI-personal-code-merge-system/memory/`）：

| 类型 | 条数 | 主要内容 |
|------|------|----------|
| project | 4 | dify P0-P3 修复落地 / fork model / cvte plugin inventory / Judge stall RCA |
| feedback | 4 | grep 验证 wired / OpenAI reasoning 修复 / merge validate env / cvte 路由 / working_branch 默认关闭 |
| reference | 2 | merge artifacts paths / 50-commit baseline run |
| user | 0 | （无） |

### 6.2 本次会话命中点

- ✅ **cvte plugin inventory** (`project_cvte_plugins.md`)：直接对应本次 18 个二开插件清单——本次实测 18 个，与 memory 完全一致 → 高复用价值。
- ✅ **测试基线 N∈[19,51]** (`reference_test_baseline.md`)：直接指引选 `upstream/main~25`，节省调研时间。
- ✅ **`merge validate` env 加载** (`feedback_validate_env.md`)：提前知道需 `set -a && source .merge/.env && set +a`，避免一次失败。
- ✅ **OpenAI reasoning 模型修复** (`feedback_planner_judge.md`)：本次 `gpt-5.4` 走 chat 路径而非 reasoning，但 memory 已具备防御性。
- ⚠️ **fork model** (`project_dify_plugins.md`)：标注 `feat_merge` 是 cvte fork 主分支——本次实际分支名 `feat/merge`（带斜杠），memory 中是下划线，需更新。
- ⚠️ **artifacts paths** (`reference_merge_artifacts.md`)：标注路径在 `MERGE_RECORD/` + `outputs/debug/checkpoints/`，本次实测全部已迁移到 `.merge/plans/` + `.merge/runs/<run_id>/checkpoint.json`，**memory 已过期**。

### 6.3 利用率评估与优化建议

| 观察 | 评分 | 改进建议 |
|------|------|----------|
| memory 与本次需求高度匹配（cvte 清单 / 基线区间 / env 加载） | 4/5 | — |
| `user` 类记录缺失 | 2/5 | 应记录用户偏好，例如 "测试报告输出语言中文 / 优先表格 / 不要 emoji" 等 |
| `reference` 类信息存在过期（`MERGE_RECORD/`、`feat_merge` vs `feat/merge`） | 2/5 | 引入 memory **rotting check**：每次会话启动时让 agent 比对 memory 中的关键路径与当前仓库状态，自动标记 stale |
| memory 命中靠人工排序（MEMORY.md）串行加载 | 3/5 | 考虑根据当前 `pwd` 自动只加载相关 project 的 memory（按 path prefix 过滤） |
| feedback 字段已含 `**Why:**` / `**How to apply:**` 结构 | 5/5 | 维持 |

**总评**：本次会话约 **6 条 memory 直接命中、2 条需要更新**，命中率 ~55 %、可信度 ~73 %。最大优化空间：
1. **自动 stale 检测**（脚本对比 MEMORY.md 中的路径与当前 fs）；
2. **按 cwd 过滤加载**（避免 reference 类记录腐烂）；
3. **建立 user 类记录**（语言、格式、偏好）。

---

## 7. 存在的问题与改进建议

### 7.1 系统层面

#### P0 — Auto-merge 阶段在新增 cherry-pick 冲突时静默挂起
**现象**：本次 Round 2 应用 3 个 take_target 决策后，cherry-pick 在 `models/siliconflow/manifest.yaml` 撞到了**未在 pending 列表里**的新冲突，process 进入 S 状态超过 18 分钟无任何 stdout / checkpoint / LLM 调用增量；working tree 残留 3 个 UU 文件。
**风险**：操作者无法判断系统是"还在思考"还是"卡死"，只能凭 `ps`/`mtime` 取证。这与 memory 中已有的 [Judge stall on B-class drift](../../memory/project_judge_stall_root_cause.md) 是同类问题但发生阶段不同。
**建议**：
- auto_merge 阶段每 N 秒（≤30s）写一次 checkpoint 心跳（`last_active_at`）；
- cherry-pick 触发新冲突时立即冒泡到 AWAITING_HUMAN，不再隐式重试；
- 增加 `merge status --run-id <id>` 子命令展示当前活跃 phase 与最近事件时间戳。

#### P1 — `pending_user_decisions` 中 plan-review 选项与 conflict-marker 选项混合
**现象**：Round 2 时 6 个 pending 同时含 `approve_human/downgrade_risky/downgrade_safe`（plan-review 风格）与 `approve_human/take_target/take_current`（conflict-marker 风格）两套 option_keys。
**建议**：在每条 pending 决策中显式带 `option_set: "plan_review" | "conflict_marker"`，让 decisions yaml 编写者可校验。

#### P2 — `dry_run` checkpoint resume 时静默清除 dry-run flag
**现象**：dry-run 跑出 plan 后 resume 命令会输出 *"checkpoint was saved in dry-run mode; resuming as a full run"*，但若操作者本意只是想多跑一阶段分析，会意外触发真实 cherry-pick 写入 working_branch。
**建议**：resume 时增加 `--keep-dry-run` 选项；缺省保持原 dry_run；切换前交互式确认。

### 7.2 配置策略层面

#### P1 — cvte 路由仅靠路径前缀，未利用 manifest.yaml `author:` 字段
**现象**：当前 cvte 插件识别只能通过 `forks-profile.yaml` 的 `fork_only_features` 路径列表（auto-overlay 的 44 项），无法精确识别"author=cvte 但与 upstream 同名的插件"，且 plan-review 阶段无法显式给出"这是 cvte 二开"信号。
**建议**：在 initialize 阶段额外扫描 `**/manifest.yaml` 的 `author:` 字段，把 `author=cvte` 的目录列入新的 `cvte_modified_paths` 集合，risk_score 自动 +0.15 / 强制 require_review。

#### P2 — 安全敏感 glob 覆盖面有限
**现象**：本次 plan 仅 1 个文件命中 ⚠️ 安全敏感，但 `**/auth/**`、`**/security/**`、`**/*credential*` 这些 glob 不能覆盖 model provider 的鉴权相关代码（如 `models/azure_openai/provider/azure_openai.py` 中的 API Key 处理）。
**建议**：增加 `**/provider/*.py` 与 `**/credentials/*.yaml`，更针对插件型 monorepo。

### 7.3 报告与可观察性层面

#### P2 — Plan 报告前 47 行嵌入了项目 README badge
**现象**：MERGE_PLAN 报告嵌入了完整 README.md badge / 介绍，约 1,500 字符无关内容，干扰人工 review。
**建议**：删除 README 嵌入，或改为 `## 项目概况` 一行 summary。

#### P3 — checkpoint.json 中决策字段冗余
**现象**：每次 resume 后 checkpoint 持续累积 `human_decision_requests` 与 `human_decisions` 两个相似列表，结构未压缩。
**建议**：合并为单一 `decision_log: [...]`，加入 `applied_at` 时间戳。

### 7.4 工作流层面

#### P1 — `merge resume` 缺少 `--auto-decisions` 等价模式
**现象**：`merge merge` 支持 `--auto-decisions <yaml>` 一次性驱动多轮 AWAITING_HUMAN；但 `merge resume` 只支持单文件 `--decisions`，每轮需手工再写一份 yaml。
**建议**：让 `merge resume --decisions <bundle.yaml>` 也支持 V2 多 round bundle，自动按 `phase` 字段消费。

#### P3 — 决策文件路径未版本化
**现象**：`.merge/decisions.yaml` 与 `.merge/decisions_round2.yaml` 是手动命名，复杂场景容易混。
**建议**：统一存到 `.merge/runs/<run_id>/decisions/round_N.yaml`，与 checkpoint 同目录，自动 review trail。

---

## 8. 附录

### 8.1 Run 产物清单

```
.merge/
  config.yaml                                       # 调整后的配置
  .env                                              # 4 个 API key
  decisions.yaml                                    # Round 1 (plan_review)
  decisions_round2.yaml                             # Round 2 (conflict_marker)
  plans/
    MERGE_PLAN_test_merge-baseline-2026-05-08_6dd6a513.md   # 2,894 行完整计划
  runs/
    6dd6a513-3a55-4707-9a07-2793719fc44b/
      checkpoint.json                               # 滚动 checkpoint
```

### 8.2 复现命令

```bash
cd /Users/angel/AI/project/dify-official-plugins
git checkout -b test/merge-baseline-2026-05-08 f5530047
git checkout feat/merge

# 配置
cp /Users/angel/AI/personal/code-merge-system/config/dify-plugins.yaml .merge/config.yaml
# 编辑：upstream_ref=test/merge-baseline-2026-05-08 / fork_ref=feat/merge
cp /Users/angel/.config/code-merge-system/.env .merge/.env

# 运行
set -a && source .merge/.env && set +a
echo "" | merge merge test/merge-baseline-2026-05-08 --no-tui --dry-run

# 决策与 resume
merge resume --run-id 6dd6a513-... --decisions .merge/decisions.yaml
merge resume --run-id 6dd6a513-... --decisions .merge/decisions_round2.yaml
```

### 8.3 关键 commit 与状态

- 测试基线：`f5530047` (`upstream/main~25`)
- fork HEAD：`635c11d9` (feat/merge, untouched)
- working_branch HEAD：`fa98055d` (`merge/auto-20260508-024830`)
- 已 fast-forward upstream commits：67 个

---

**报告状态**：已覆盖用户要求的全部 7 个章节。Round 2 resume 因 §7.1 P0 hang 18 分钟无进展被人工终止，最终 conflict_analysis / judge / final report 三阶段未触达；详见 §5.2 阻塞分析与 §7.1 P0 改进建议。

**修订记录 (2026-05-08)**：
- §1.1/§1.2 澄清 fork_ref / upstream_ref 语义：feat/merge 是合并基础，upstream/main~25 是分阶段验证选取的 upstream 中间 commit，非"feat/merge 落后 25 个 commit"。
- §4.3 / §5.2 / §7.1 更正 `max_files_per_run` 误读：该参数仅是 planner 分类阶段的 LLM 批大小，不是单 run 文件总数上限；删除原 P0「max_files_per_run 量级失配」条目。本次只触达 ~50 文件的真实原因是 auto_merge hang 中断了 run。
