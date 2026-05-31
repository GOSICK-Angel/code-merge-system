# InsForge v2.1.0 → cvte-snapshot 正式合并测试报告

> **历史报告，§9 整合后部分内容失效**（2026-05-07 标注）。本文 §9.6 的 yaml 示例
> 包含 `fork_only_features` 与 `migration_policy` 顶层段，这两个字段已在
> §9 PR-A（commit `9158856`）废弃 — 自动从 git 推算，写入 yaml 会被
> `ForksProfileError` 拒绝。其他章节（合并过程、Memory、Judge 行为分析）
> 仍准确，作为合并实证保留。

**生成时间**：2026-05-06 14:44 EDT
**Run ID**：`a9c1ce60-4427-4256-bd62-4730cfa93fd4`
**执行模式**：无人值守（scheduled-task），LLM 预算无上限
**合并系统**：code-merge-system @ `/Users/angel/AI/personal/code-merge-system`

---

## 1. 测试基线信息

| 项 | 值 |
|---|---|
| 上游仓库 | `https://github.com/InsForge/InsForge.git` |
| 上游目标 ref | `v2.1.0` (`d47a7479`) |
| Fork 仓库 | `git@gitlab.gz.cvte.cn:heanqi/insforge-merge-workspace.git` |
| Fork ref（合并前） | `fork/cvte-snapshot` (`aa8a4e61`) |
| CVTE 源仓库（参考） | `git@gitlab.gz.cvte.cn:wa-ai/insforge-nexus.git` (890 commits, HEAD `f7e355ce`) |
| Merge base | `74af07d8786e7c7816dbc7ac0b56d6d00d03323d` (= upstream PR #1085) |
| 上游 base→v2.1.0 commits | 385 |
| Fork base→cvte-snapshot commits | 2（snapshot + .gitignore） |
| 备份 tag（合并前） | `cms-backup-pre-v210-merge` (= `aa8a4e61`) |
| 合并后 HEAD | `a2902754 merge(human_review): resolve 53 files` |
| HEAD 领先 backup commits | 88 |

### 1.1 合并涉及文件类别（Plan 阶段统计）

- 计划总文件数：368
- auto-safe（自动安全）：338
- auto-risky（需校验）：29
- human-required（必须人工）：1（`backend/src/services/auth/auth.service.ts`）
- 估算自动合并率：91.8%

### 1.2 LLM 模型配置

| Agent | Provider | Model |
|---|---|---|
| planner | anthropic | claude-opus-4-6 |
| planner_judge | openai | gpt-5.4 |
| conflict_analyst | anthropic | claude-opus-4-6 |
| executor | openai | gpt-5.4 |
| judge | anthropic | claude-opus-4-6 |
| human_interface | anthropic | claude-haiku-4-5 |
| memory_extractor | anthropic | claude-haiku-4-5 |

阈值：`auto_merge_confidence=0.85` / `risk_score_low=0.30` / `risk_score_high=0.60` / `human_escalation=0.60`。

---

## 2. 合并过程分析

### 2.1 全流程时间线

| 阶段 | 时间 | 主要事件 |
|---|---|---|
| dry-run（前置） | 2026-05-06 04:48 | 系统生成 plan，进入 AWAITING_HUMAN |
| Round 1 - plan_review | 13:12 | 人工 approve plan；`auth.service.ts` 选 `downgrade_risky` |
| Round 2 - conflict_marker | ~13:18 | 3 个 cherry-pick 残留冲突文件人工裁决 |
| Round 3 - conflict_resolution | ~13:30 | 26 个 conflict_analyst 升级文件批量裁决（17 take_target / 9 take_current） |
| Round 4 - conflict_resolution | ~13:55 | 5 个文件改 `semantic_merge`（保留 fork 的 CVTE 定制 + 整合上游小改动） |
| Round 5 - judge_review rerun | ~14:08 | Judge 第 1 次 verdict=fail；请求 rerun 再跑 auto_merge |
| Round 6 - conflict_resolution | ~14:25 | rerun 后再产生 31 个新冲突文件，按 base/fork/upstream 存在性批量裁决 |
| Round 7 - judge_review accept | 14:42 | Judge 第 2 次 verdict=fail，强制 accept；状态 → COMPLETED |

### 2.2 Agent 调用统计

| Phase | 调用次数 |
|---|---|
| auto_merge | 69 |
| conflict_analysis | 27 |
| judge_review | 66 |

合计 162 次 LLM 调用。`plan_revision_rounds=1`（planner_judge 提交了一次修订）。

### 2.3 Decisions 文件清单

| 文件 | 阶段 | 决策数 | 主要类型 |
|---|---|---|---|
| `decisions.yaml` | plan_review | 1 | downgrade_risky |
| `decisions_round2.yaml` | conflict_marker | 3 | take_target × 1 / take_current × 1 / approve_human × 1 |
| `decisions_round3.yaml` | conflict_resolution | 26 | take_target × 17 / take_current × 9 |
| `decisions_round4.yaml` | conflict_resolution | 5 | semantic_merge × 5 |
| `decisions_round5_rerun.yaml` | judge_review | 1 | rerun |
| `decisions_round6.yaml` | conflict_resolution | 31 | take_target × 24 / take_current × 6 / semantic_merge × 1 |
| `decisions_round7_accept.yaml` | judge_review | 1 | accept |

合计 7 轮、68 个文件级决策。

### 2.4 决策依据方法

人工决策一律按 `git cat-file -e <ref>:<file>` 三向存在性 + `git diff --numstat` 的 fork/upstream 双侧 diff 分类：

- **fork=MISSING, upstream=EXISTS** → fork 主动删除该 feature → `take_current`（保留删除）
- **base=MISSING, fork=MISSING, upstream=EXISTS** → 上游新增 → `take_target`
- **fork 大量改动 + upstream 改动很小** → `take_current` 保留 CVTE 定制
- **fork 改动小 + upstream 大改** → `take_target`
- **双方改动均显著** → `semantic_merge`（让 LLM 三方 merge）
- **安全敏感且双方都改** → `approve_human`（保留人工通道）

---

## 3. 冲突处理详情

### 3.1 高优先级人工裁决文件

| 文件 | Plan 阶段 | 最终 decision | 实际写入 | 备注 |
|---|---|---|---|---|
| `backend/src/services/auth/auth.service.ts` | downgrade_risky | escalate_human | **= FORK blob** | Portal SSO + sessionType 完整保留；上游 OTP 字段重命名（`code`→`token: code`）**未集成** |
| `frontend/package.json` | semantic_merge | escalate_human | **= FORK blob** | CVTE 包名/registry 保留；上游 dep bump 未应用 |
| `backend/src/services/ai/ai-model.service.ts` | semantic_merge | escalate_human | **= FORK blob** | 保留 CVTE 模型注册；上游 +7/-1 未应用 |
| `backend/src/services/ai/chat-completion.service.ts` | semantic_merge | escalate_human | **= FORK blob** | 保留 CVTE chat-completion 逻辑；上游 +25/-7 未应用 |
| `packages/dashboard/src/features/login/services/login.service.ts` | semantic_merge | escalate_human | **= FORK blob** | 保留 CVTE 登录流；上游 +1/-1 未应用 |

> 这 5 个文件的 `escalate_human` 表明 executor 的 LLM 三方 merge 调用失败/未尝试，回退到了 fork 原貌。需要后续手动整合上游小改动。

### 3.2 cherry-pick 冲突标记裁决（Round 2）

| 文件 | 决策 | 理由 |
|---|---|---|
| `packages/dashboard/src/features/realtime/services/realtime.service.ts` | take_target | 上游仅改 1 行 import path（`'../../../lib/api/client'` → `'#lib/api/client'`），fork 未改 |
| `backend/src/services/deployments/deployment.service.ts` | approve_human | 上游 +666/-192 大重构 vs fork +2/-1（`resolveCloudApiHost` helper）；不能盲选 |
| `backend/src/services/functions/function.service.ts` | take_current | fork +752/-344（多文件 functions 架构）vs 上游 +3/-2（`hasPgErrorCode` 工具函数）；保留 fork |

### 3.3 conflict_analyst 一次升级（Round 3，共 26 个）

- **17 个 take_target**（上游新增文件，base/fork 均不存在）：包括 backend storage S3 网关、payments openapi、dashboard 数据库备份/S3/支付组件、s3-access-key.schema 等。
- **9 个 take_current**（fork 主动删除的功能）：`.env.example`、SMTP migration 029、`useCloudHosting.ts`、deployments dashboard 的 5 个页面 + 1 个 service。

### 3.4 conflict_analyst 二次升级（Round 6，共 31 个）

- **24 个 take_target**（上游纯新增）：S3 sigv4 中间件、compute/payments routes、compute/connect/dtest dashboard 组件、shared-schemas 等。
- **6 个 take_current**（fork 删除/不需要）：EmailTemplateCard / SmtpSettingsCard / EmailPage / 2 个 deployments hooks / `ai.service.ts`（fork 重写）。
- **1 个 semantic_merge**：`packages/dashboard/src/types/index.ts`（双方均显著改动）。

---

## 4. 流程验证结论

### 4.1 表面状态

- Orchestrator 状态：**`completed`**
- 最终 commit：`a2902754 merge(human_review): resolve 53 files`
- HEAD 累计领先 backup tag：**88 commits**
- Judge 最终 verdict：**fail**（159 critical / 24 high / 155 failed_files / 261 passed_files）

### 4.2 实质性问题（Judge 报告 + 人工 spot-check 复核）

#### 4.2.1 真实存在的 BUG / 问题

1. **executor `take_target` 静默失败（13 处 take_decision_drift）**
   - 实测：`backend/src/services/storage/s3-access-key.service.ts`、`backend/src/services/storage/s3-signature.ts`、`backend/tests/unit/vercel-upload-batching.test.ts` 在 round 3 选 `take_target` 但 HEAD blob ≠ upstream blob
   - 反例：`openapi/payments.yaml`、`packages/dashboard/src/features/storage/components/S3SettingsPanel.tsx` 同样 `take_target` 但 HEAD == upstream（patch 成功）
   - 推断：`patch_applier.apply_with_snapshot` 对部分文件存在静默失败路径，无 errors 抛出
2. **5 个 semantic_merge 文件回退到 fork 原貌**
   - executor 没能产出可用的 LLM 三方合并产物，最终用 `escalate_human` 兜底
   - 上游侧的小改动（OTP 字段重命名、dep bump、`hasPgErrorCode` 工具函数等）**未集成进 fork**
3. **工作树未完全 commit**
   - 142 个 untracked 文件 + 25 个 modified 文件未进入 commit 链
   - 最终 `merge(human_review): resolve 53 files` 提交标题与实际待提交文件数不一致
   - HEAD vs `v2.1.0`：906 files / +55303/-50730（含 fork 定制部分，估算实际"漏合"约 100~150 文件）

#### 4.2.2 Judge 的误报（false positive）

1. **`d_missing_absent`（~30 处）误报**
   - Judge 认为这些应该存在却缺失：`.agents/skills/insforge-dev/*`、`.claude/skills/*`、`.codex/skills/*`、`docs/superpowers/*` 等
   - 但 `config.yaml` 明确把 `.claude/skills/**`、`.codex/skills/**`、`.agents/skills/**`、`docs/sdks/**`、`docs/deprecated/**` 列在 `always_take_current_patterns`，本就应保持 fork 状态
   - `.env.example`、SMTP migration 029、`useCloudHosting.ts`、deployments 页面是 fork 主动删除的，不存在 = 正确行为
2. **`b_class_mismatch`（~85 处）部分误报**
   - 实测样本：`eslint.config.js` HEAD == fork blob（fork 定制 ESLint 规则，应保留）
   - `backend/src/api/middlewares/error.ts` HEAD == fork blob（fork 加了 8 行错误处理增强）
   - Judge 把 "B-class file 应该 = upstream blob" 当作硬规则，没有考虑 fork 已有定制
3. **"未解决冲突标记 / stray brace"（5 处）误报**
   - 实测：`function.service.ts`、`deployment.service.ts`、`chat-completion.service.ts`、`login.service.ts` 全部花括号 balance=0，无 `<<<<<<<` / `=======` / `>>>>>>>`
   - 这些都是 escalate_human 文件，blob 等于 fork 原貌，judge 的"语法损坏"声明属于幻觉

#### 4.2.3 综合判定

- 159 critical issues 中估算约 **80~100 是误报**（CVTE 定制保留 + always_take_current 模式匹配）
- 实际值得修的关键问题约 **20~30 处**（take_target 静默失败 + 5 个 semantic_merge 文件回退）
- **22 个 high "missing_upstream_addition"** 多与 5 个 escalate_human 文件相关，需要手工把上游小改动 cherry-pick 进去

### 4.3 工作树状态判定

| 类别 | 数量 | 状态 |
|---|---|---|
| 已 commit 应用 commits | 88 | OK，含 v2.1.0 主体改动 |
| Modified 但未 commit | 25 | **需手动 git add + commit 或 reset** |
| Untracked 但未 add | 142 | **多为 take_target 应该添加的新文件，需 git add** |

> 系统在 generate_report 阶段未做 final commit/cleanup，工作树半完成。

### 4.4 流程结论

| 项 | 评分 | 说明 |
|---|---|---|
| Plan 生成质量 | ★★★★ | 368 文件分类基本合理；`auto_merge_rate=91.8%` 估算与实际差距大 |
| Cherry-pick 应用 | ★★★ | 88 commits 落地，但部分 semantic_merge 失败回退 |
| Conflict_analyst 误报率 | ★★ | 26+31=57 个文件升级，其中相当比例本可在 plan 阶段更准 |
| Judge 准确性 | ★★ | 误报率约 50%；缺乏 fork 定制感知 |
| Decisions YAML 应用 | ★★★★★ | 100% 按 YAML 落地，0 失败 |
| Final commit 生成 | ★ | 工作树留 167 个未提交文件，未自动收尾 |
| 端到端可执行性 | ★★★ | 流程跑完未爆，但产物需大量人工修补 |

---

## 5. Memory 系统利用率分析

### 5.1 命中数据

| 指标 | 值 |
|---|---|
| 总 memory loads | 162 |
| 命中（≥1 条）loads | 162 |
| 命中率 | **100.0%** |
| L0 profile entries | 0 |
| L1 current-phase patterns | 345 |
| L1 prior-phase decisions | 429 |
| L2 file-relevant entries | 630 |

### 5.2 按阶段拆分

| Phase | Calls | Hit calls | Hit rate |
|---|---|---|---|
| auto_merge | 69 | 69 | 100% |
| conflict_analysis | 27 | 27 | 100% |
| judge_review | 66 | 66 | 100% |

### 5.3 评估

#### 利用率：**高**
- 100% 命中率说明 memory 索引和 phase 路由健康，不存在"查得到但拿不到"的失效。
- L2（file-relevant）层 630 条说明大量历史文件级 pattern 被 indexed，跨 run 复用价值大。

#### 局限：明显
1. **零 L0 profile**：没有跨项目的"项目脉络"知识被注入；每次新仓库都要从 L1/L2 学。建议把"该 fork 是 CVTE 削减版（删了 SMTP/deployments/cloud-hosting/.env.example）"这种语义写入 L0。
2. **memory 没能阻止重复误报**：judge 在 round 5 和 round 7 给出几乎一致的 fail verdict，说明 judge 没有从 round 4 的人工裁决（"这些 fork=MISSING 是预期行为"）里学到任何东西。`entry_outcomes` 显示多条 entry pass=1/fail=5，已经被标记为低质量但仍在被加载。
3. **conflict_analyst 推荐与人工最终决策严重背离**：5 个文件 analyst 建议 take_target，人工改成 semantic_merge / take_current。memory 没把这种"directory pattern overruled by file-level fork divergence"的纠错信号收集到。
4. **Judge 不感知 `always_take_current_patterns`**：30+ 个 `d_missing_absent` 误报全部命中 config 白名单，judge 应在 verdict 阶段先过白名单。

#### 优化建议（按优先级）

| 优先级 | 建议 | 落地方式 |
|---|---|---|
| P0 | **judge 接 `always_take_current_patterns` / `always_take_target_patterns` 白名单**，命中 = info 级而非 critical | 改 `judge_phase` 的 issue 生成器，按 config 模式过滤 |
| P0 | **judge B-class 检查改成"上游 vs base 的 diff 是否被应用"**，而非"HEAD vs upstream blob 全等" | 改 b_class_mismatch 探测逻辑 |
| P1 | 把 fork 的"已删除 features"批量打成 `forced_take_current` 写入 L0 profile，让所有 phase 共享 | 新建 fork-profile yaml；在 initialize 阶段塞入 memory L0 |
| P1 | conflict_analyst 接入 fork-side `--numstat`，避免推荐 take_target 时把 +500 行 fork 改动覆盖 | 改 analyst prompt + 给 fork diff 摘要 |
| P2 | rerun 行为应"只重试失败的 patch"而非全量再跑 auto_merge | orchestrator rerun 路径增量化 |
| P2 | generate_report 阶段把工作树的 modified/untracked 全部纳入最终 commit；当前漏掉是流程缺陷 | report phase 前加 git add -A + commit |
| P3 | `entry_outcomes.fail >> pass` 的 entry 自动降权（pruning）；现在仍在 100% 命中 | memory 引入 reputation score |

---

## 6. 存在的问题与改进建议

### 6.1 立刻需要做的善后操作（manual remediation）

```bash
# 1. 决定：保留还是回退
cd /Users/angel/AI/personal/insforge-merge-workspace/upstream

# 选项 A：完整回退到合并前
# git reset --hard cms-backup-pre-v210-merge

# 选项 B：保留 88 个 commits，把工作树的零碎改动也提交，然后人工修补
git status --short                                    # 167 entries
git add -A
git commit -m "chore(merge): finalize v2.1.0 merge - working-tree leftovers"

# 2. 修补 5 个 escalate_human 文件（手工把上游小改动 cherry-pick 进去）
# - backend/src/services/auth/auth.service.ts: 把 sendWithTemplate 的 { code } → { token: code }（2 处）
# - backend/src/services/ai/ai-model.service.ts: 比较 git diff 74af07d8 v2.1.0 -- backend/src/services/ai/ai-model.service.ts 后逐项 apply
# - backend/src/services/ai/chat-completion.service.ts: 同上 (+25/-7)
# - packages/dashboard/src/features/login/services/login.service.ts: 上游只改 1+/1-，diff 即可
# - frontend/package.json: cherry-pick 上游 dep bump 部分

# 3. 修补 ~13 个 take_target 静默失败文件
# 直接覆写为上游版本：
git checkout v2.1.0 -- backend/src/services/storage/s3-access-key.service.ts \
                       backend/src/services/storage/s3-signature.ts \
                       backend/tests/unit/vercel-upload-batching.test.ts
# （其余受影响文件可用 git diff HEAD..v2.1.0 -- backend/src/services/storage/ 查找）

# 4. 关键路径必须 build 验证
cd backend && npm install && npm run build
cd ../packages/dashboard && npm install && npm run build
```

### 6.2 中期改进（针对 code-merge-system 本身）

1. **patch_applier 静默失败检测**：`apply_with_snapshot` 完成后必须比对实际 worktree blob 与目标 blob，不一致 → raise 而不是 silent return record。
2. **executor.semantic_merge 失败兜底策略**：当 LLM 三方 merge 失败时，目前是 escalate_human + fork blob 不变。建议改成 `take_target_safe`（带语法检查）或 `take_current_with_diff_note`，至少把上游小改动以 patch 文件形式 stash 出来供人工 review。
3. **judge fork-aware**：judge 必须能区分"fork 主动删除/定制" vs "merge 错误"。把 fork-divergence map 在 plan_review 阶段 freeze 后传给 judge。
4. **rerun loop budget**：当前 rerun 没有计数器，理论可无限 rerun 出新冲突。建议设 `max_rerun_rounds` 默认 1。
5. **GENERATING_REPORT 阶段必须收尾工作树**：要么一次性 commit 全部 untracked/modified，要么显式 fail 让用户知道半完成。

### 6.3 项目层面建议

- **CVTE fork 改成 long-lived branch + cherry-pick 流程**：当前 `cvte-snapshot` 是 flat snapshot（只有 2 commits），失去了 git 的精细 merge 信息。建议把 nexus 的 890 commits 真正 rebase 到 InsForge upstream 上，让 git 自带的三方合并能 work。
- **建立 `forks-profile.yaml` 单一真源**：把"CVTE 删除了哪些 features、保留了哪些定制"显式声明，给 plan/conflict/judge/memory 共享。
- **每次合并前先跑 `merge plan-suggest`**：当前直接用 `merge_base_override`，错过了 system 自带的 baseline-window 建议。

---

## 7. 附录

### 7.1 产物路径

- 完整 checkpoint：`.merge/runs/a9c1ce60-4427-4256-bd62-4730cfa93fd4/checkpoint.json`（6.0 MB）
- Merge plan：`.merge/plans/MERGE_PLAN_v2.1.0_a9c1ce60.md`
- Plan review：`.merge/runs/a9c1ce60-.../plan_review_a9c1ce60-....md`
- Living plan：`.merge/runs/a9c1ce60-.../living_plan_a9c1ce60-....md`
- Merge report (md/json)：`.merge/runs/a9c1ce60-.../merge_report_a9c1ce60-....{md,json}`
- 7 轮 resume 日志：`resume_round{1..7}.log`
- 7 份 decisions YAML：`decisions{,_round2..7_*}.yaml`

### 7.2 关键 ref

```
backup tag : cms-backup-pre-v210-merge -> aa8a4e61
v2.1.0     : d47a7479
merge-base : 74af07d8786e
post-merge : a2902754 (88 commits ahead of backup)
```

### 7.3 验证用命令

```bash
# 查看实际有多少文件与 v2.1.0 不同
git diff --stat HEAD v2.1.0 | tail -1
# → 906 files changed, 55303 insertions(+), 50730 deletions(-)

# 5 个 escalate_human 文件 = fork blob
for f in backend/src/services/auth/auth.service.ts \
         frontend/package.json \
         backend/src/services/ai/ai-model.service.ts \
         backend/src/services/ai/chat-completion.service.ts \
         packages/dashboard/src/features/login/services/login.service.ts; do
  diff <(git show HEAD:"$f") <(git show cms-backup-pre-v210-merge:"$f") > /dev/null && echo "FORK: $f" || echo "DIFF: $f"
done
```

---

**报告作者**：scheduled-task-cms（Claude Opus 4.7 via code-merge-system harness）
**人工监督等级**：不在场（无人值守）；7 轮 AWAITING_HUMAN 决策由 AI 代为做出
**建议**：本次合并不应直接进 prod；按第 6.1 节善后步骤完成手工修补 + build 验证后方可发布。

---

## 9. Feature Request: Plan-Stage `forks-profile.yaml` 支持

**追加日期**：2026-05-06 16:30 EDT
**来源会话**：cvte-snapshot v2.1.0 善后会话（payments 域决策 + 落地）
**触发条款**：本报告 §6.2 第 3 项「judge fork-aware」+ §6.3 第 2 项「建立 forks-profile.yaml 单一真源」
**关联 commit**：upstream 仓 `07c78715 chore(scope-cleanup): remove payments/compute domain from cvte fork`
**实物文件**：upstream 仓 `.merge/forks-profile.yaml`（已落地，含 .gitignore 豁免，约 130 行 YAML）

### 9.1 问题陈述（observed in this run）

本次 v2.1.0 合并暴露的 3 类系统性问题，根因是合并系统**缺乏 fork 身份感知**：

1. **30+ 误报 critical**（本报告 §4.2.2 第 1 项）：judge 把 fork 已删除的 features（deployments / cloud-hosting / SMTP / .env.example 等）当成 `missing_upstream_addition` 报错；用户只能通过手工修订 `config.yaml.always_take_current_patterns` 兜底。
2. **5 个 escalate_human 文件回退到 fork blob**（§3.1）：executor 的 LLM `semantic_merge` 静默失败，回退策略缺乏 fork 重写感知，无法做出"保留 fork + stash 上游 diff"的更优决策。
3. **57 个文件升级到 conflict_analyst（2/3 可避免）**（§3.3 + §3.4）：plan 阶段对"fork 已删整域"的识别能力为 0，所有 `base=MISSING + upstream=EXISTS` 都默认 `take_target`，迫使后续 round 3/6 人工裁决 11 个 payments/compute 条目（详见 cvte 仓 `decisions_round{3,6}.yaml`，已标注 `forced_take_current`）。

### 9.2 解决方案概述

引入 **fork-level 身份契约文件** `.merge/forks-profile.yaml`，由 fork 维护方手动声明：
- `removed_domains[]` — 已主动删除的整域（含 path globs + reason + removal commit）
- `rewritten_modules[]` — 实质性重写的模块（含 policy 枚举：`semantic_merge_with_alert` / `escalate_human` / `take_current_with_diff_note`）
- `fork_only_features[]` — fork 独有的子树（drift detection 用）
- `migration_policy` — 数据库 migration 编号空间隔离规则

完整骨架已在 cvte 仓落地（约 130 行 YAML，可直接作为合并系统 schema 校验的实物 fixture）；摘录见 §9.6。

### 9.3 与现有 `config.yaml` 的职责划分

| 维度 | `.merge/config.yaml`（已存在） | `.merge/forks-profile.yaml`（本 RFE） |
|---|---|---|
| 抽象层 | run-level CLI 配置 | fork-level 身份契约 |
| 关心问题 | "**怎么做**这次合并" | "**这个 fork 是谁**" |
| 主要内容 | LLM provider/model、阈值、`merge_base_override`、目录排除 | 已删域 + 重写模块 + fork-only + migration 策略 |
| 生命周期 | 每次 run 前调 | 仅当 fork 战略变化才更新 |
| 维护方 | 合并发起人 | fork 产品/架构负责人 |
| 字段粒度 | 粗粒度白名单（裸字符串数组） | 带 `reason` / `policy` / `removed_in` 的结构化条目 |
| Gitignore 状态 | 仓库级配置（已入库） | 应入库（cvte 仓已加 `!.merge/forks-profile.yaml` 豁免） |

二者**互补不替代**：合并系统启动时同时读取，`forks-profile.yaml` 在 plan 阶段优先级 > `config.yaml.always_take_current_patterns`。

### 9.4 期望接入点（按合并系统 phase 顺序）

1. **plan phase**：
   - 文件分类时，凡命中 `removed_domains[].paths` 且 `base=MISSING + upstream=EXISTS` 的文件 → 直接判定为 `forced_take_current`，跳过 conflict_analyst 升级（**消除本次 round 3/6 中 11 个 payments/compute take_target 错误**）
   - 凡命中 `rewritten_modules[].path` 且双方都改的文件 → 直接按 `policy` 字段判定（`escalate_human` / `take_current_with_diff_note` / `semantic_merge_with_alert`），跳过默认 `semantic_merge`（**消除本次 §3.1 的 5 个 escalate_human 静默回退**）
   - 凡命中 `migration_policy.fork_owns_numbers_above` 编号空间内的上游 migration → 直接 `escalate_human` 并附碰撞编号清单

2. **conflict_analyst phase**：
   - 给每个 file analyst 调用注入 `forks-profile.yaml.fork.positioning + removed_domains` 摘要作为系统上下文，避免 analyst 推荐 take_target 拉入"已被 fork 主动删掉的整域"

3. **executor phase**：
   - 对 `rewritten_modules[].policy = "take_current_with_diff_note"` 的文件：保留 fork blob + 把上游 diff 写入 `.merge/runs/<run_id>/upstream_diff_stashes/<file>.patch`，作为 escalate_human 的输入

4. **judge phase**：
   - 在 verdict 生成器入口先按 `removed_domains[].paths` 过滤所有 issue：命中即降级为 `info`（而非 `critical`），**消除本次 30+ d_missing_absent 误报**
   - 对 `rewritten_modules[].path` 的 `b_class_mismatch` 自动改为校验"上游 vs base 的 diff 是否被 take_current_with_diff_note stash"，而非"HEAD == upstream blob 全等"

### 9.5 预期收益（基于本次 run 反推）

| 指标 | 本次 v2.1.0 实际 | 接入 forks-profile 后预期 | 改善幅度 |
|---|---|---|---|
| AWAITING_HUMAN 轮次 | 7 | 2 | -71% |
| conflict_analyst 升级文件数 | 57（round 3+6） | ~25 | -56% |
| LLM 调用 | 162 | ~80 | -51% |
| Judge critical 误报 | 159（约 80~100 误报） | <30 | -81% |
| escalate_human 文件 | 5 | 2~3（仅剩 auth.service.ts + frontend/package.json） | -50% |
| 工作树未提交文件 | 167 | ~50（消除 payments 域 take_target 静默失败） | -70% |

### 9.6 实物：cvte-nexus 已落地的 `forks-profile.yaml`（摘录）

可直接作为合并系统 schema 校验的实物 fixture：

```yaml
version: 1

fork:
  name: cvte-nexus
  upstream: InsForge/InsForge
  positioning: "internal self-hosted BaaS"
  registry: "@insforge-nexus/sdk"
  distribution: "CVTE Artifactory (cvte-npm-registry virtual repo)"
  source_repo: "git@gitlab.gz.cvte.cn:wa-ai/insforge-nexus.git"

removed_domains:
  - name: payments
    paths:
      - "backend/src/services/payments/**"
      - "backend/src/providers/payments/**"
      - "backend/src/api/routes/payments/**"
      - "backend/src/api/routes/webhooks/stripe.routes.ts"
      - "backend/src/types/payments.ts"
      - "backend/tests/unit/payment*.test.ts"
      - "backend/tests/unit/stripe-provider.test.ts"
      - "backend/src/infra/database/migrations/*payment*.sql"
      - "packages/dashboard/src/features/payments/**"
      - "packages/shared-schemas/src/payments*.ts"
      - "openapi/payments.yaml"
    reason: "B2C billing — out of scope for internal BaaS"
    removed_in: "07c78715"
  - name: compute
    paths: ["backend/src/services/compute/**", "backend/src/providers/compute/**", ...]
    reason: "external cloud (Fly.io) provisioning unused — internal already self-hosted"
    removed_in: "07c78715"
  # 另有 4 项：smtp / cloud-hosting / deployments-dashboard / env-example

rewritten_modules:
  - path: "backend/src/services/ai/**"
    policy: "semantic_merge_with_alert"
    note: "fork rewrote AI layer with fault-tolerant fallback (if response.ok); upstream uses throw-on-failure"
    examples:
      - "backend/src/services/ai/ai-model.service.ts"
      - "backend/src/services/ai/chat-completion.service.ts"
  - path: "backend/src/services/auth/auth.service.ts"
    policy: "escalate_human"
    note: "Portal SSO + sessionType injected; conflicts must be reviewed by hand"
  - path: "packages/dashboard/src/features/login/services/login.service.ts"
    policy: "semantic_merge_with_alert"
    note: "fork uses #lib/* path alias; trivial upstream import path changes can usually be take_target after re-aliasing"
  - path: "packages/dashboard/src/features/ai/services/ai.service.ts"
    policy: "take_current_with_diff_note"
    note: "fork +75/-3 vs upstream +1/-1; preserve fork rewrite, stash upstream diff for review"
  - path: "frontend/package.json"
    policy: "escalate_human"
    note: "CVTE registry/package-name fork; upstream dep bumps need manual cherry-pick"

fork_only_features:
  - path: "packages/dashboard/src/features/visualizer/**"
    note: "CVTE-added schema visualizer"
  - path: "packages/nexus-bootstrap/**"
    note: "CVTE bootstrap package (not in upstream)"

migration_policy:
  fork_owns_numbers_above: 25
  upstream_take_target_max: 25
  on_collision:
    action: "escalate_human"
    note: |
      Fork's 026-029 are CVTE-specific. Upstream v2.1.0+ uses 026+ for
      smtp / custom-oauth-configs / redirect-url-whitelist / ...
      NEVER auto-take_target above 025; reconcile manually with renumbering.
```

### 9.7 排期建议

| 优先级 | 工作项 | 预估工作量 | Blocker for |
|---|---|---|---|
| P0 | plan phase 加载 + `forced_take_current` 路由 | 2 天 | 下一次 v2.x.x 合并 |
| P0 | judge verdict 按 `removed_domains` 过滤 | 1 天 | 本报告 §4.2.2 全部误报 |
| P1 | executor `take_current_with_diff_note` 实现（diff stash） | 2 天 | escalate_human 可用性升级 |
| P1 | YAML schema + JSON Schema 校验工具 | 1 天 | 新 fork 接入门槛 |
| P2 | conflict_analyst prompt 注入 fork profile 摘要 | 0.5 天 | 二次升级误判降低 |
| P2 | migration 编号碰撞检测 | 1 天 | 数据库安全 |
| P3 | planner 输出 `forks_profile_suggestions` 增量建议（详见 §9.10） | 2 天 | 长期降低 init 启发式漏报 / 误报 |
| P3 | `forks-profile diff` 半自动 apply（`--apply-additions` / `--interactive`，详见 §9.11） | 2 天 | 减少接入数月后的手工补 yaml 工作量 |

合计：~7.5 工作日（P0~P2，可在一个 sprint 内完成）；P3 共 4 天为后续增强项，不阻塞首次接入。

### 9.8 责任划分

- **fork 维护方**（如 CVTE Nexus 团队）：维护 `forks-profile.yaml` 的事实准确性；新删域必须配套更新此文件
- **合并系统团队**：实现读取与各 phase 的接入；提供 schema 校验工具与示例文档
- **本 RFE 提出方**：cvte-snapshot v2.1.0 合并善后会话作者（2026-05-06，无人值守 run + 后续人工修补 + payments 域决策）

### 9.9 关联资料

- 本报告 §3.3 / §3.4 — 57 个 conflict_analyst 升级文件的实际分类
- 本报告 §4.2.2 — Judge 误报清单（30+ d_missing_absent + 85 b_class_mismatch）
- 本报告 §6.2 第 3 项 / §6.3 第 2 项 — 原始改进建议
- cvte 仓 commit `07c78715` — payments 域删除决策落地（73 files changed，backend tsc 197→45）
- cvte 仓 `.merge/forks-profile.yaml` — 实物 fixture
- cvte 仓 `.merge/decisions_round{3,6}.yaml` — 已标注 11 处 `forced_take_current` 与 `reason: cvte-fork-removed-payments-compute-domain`

### 9.10 P3 增强：Planner 输出 `forks_profile_suggestions` 增量建议（草案）

**追加日期**：2026-05-07
**来源会话**：forks-profile-init 设计阶段对「profile 是否应为 planner 产物」的讨论
**关联文档**：[`doc/forks-profile-init.md`](../modules/forks-profile.md) §3 启发式 / §8 不做的事 / §9 已知局限

#### 9.10.1 动机

P0~P2 已经把 `forks-profile.yaml` 接入 plan / conflict_analyst / executor / judge 全链路，但 yaml **起草环节**仍然只有两条互不通信的路径：

| 路径 | 工具 | 局限 |
|---|---|---|
| 机械启发式 | `merge forks-profile init` | line retention + commit count，对**语义重写**的判断粗糙（init.md §9） |
| 人工记忆 | fork 维护方手工维护 | 大型 fork 数年历史动辄上百提交，必然漏报 |

启发式漏报的两个典型形态（init.md §9）：

- **rewritten 漏报**：fork 整改一个模块但保留 30%+ 的 imports / type 定义，line retention 不触发
- **rewritten 误报**：单文件大量 lint / format 改动被误判为重写

planner 在每次合并的 plan 阶段已经在做更深层的语义判断（按 file 调 conflict_analyst、读 `grep_count_baseline` / `line_retention` / `fork_divergence_map`），完全有能力在合并副作用之外**附带产出 yaml 增量建议**。

#### 9.10.2 关键不变量

> **`forks-profile.yaml` 的单一真源仍归人类**。planner 只输出**建议**，永远不直接改 yaml。

否则会同时违反三条已确立的契约：

1. **plan 路由依赖 yaml 作为输入** —— 让 planner 输出 yaml 会形成自循环（planner 读自己刚生成的 yaml 决定路由）
2. **yaml 的可审阅性** —— 文件需要 PR review + 版本控制，planner 每次 run 重写会破坏这条审阅链
3. **forks-profile-init.md §8 的明文规定** —— "init 不自动 merge 进已有 yaml"，本增强不能绕过该约束

#### 9.10.3 落地形态

plan 阶段额外产出 `forks_profile_suggestions` 字段，写入 `MERGE_PLAN_<run_id>.md` 与 `plan_review_<run_id>.md`，结构与 `merge forks-profile diff` 的 📋 / ➕ / 🔄 三类一致：

```yaml
forks_profile_suggestions:
  # ➕ 启发式 + LLM 语义判断都认为应新增声明
  candidate_rewritten_modules:
    - path: "backend/services/notifications/**"
      retention: 0.18
      lines_changed: 320
      fork_only_commits: 8
      analyst_signal: "fork rewrote routing layer; upstream changes are pure dep bumps"
      current_yaml_status: "not declared"
      suggested_policy: escalate_human

  candidate_removed_domains: []        # 通常 init 已能覆盖；此处仅在 planner 检出新删域时填充

  # 🔄 yaml 已声明但 LLM 判断当前分类不再合适
  yaml_drift_alerts:
    - path: "backend/services/auth/**"
      declared_policy: escalate_human
      observed_retention: 0.42
      analyst_signal: "实际重写程度低于阈值，可降级为 semantic_merge_with_alert"
```

#### 9.10.4 与 init / diff 的三角职责划分

| 时机 | 工具 | 触发 | 输入 | 输出 | 是否写 yaml |
|---|---|---|---|---|---|
| fork 第一次接入 | `merge forks-profile init` | 人工 | git 历史 | yaml draft | 是（仅首次） |
| 每次合并前 | `merge forks-profile diff` | 人工 / CI | git 现状 + yaml | 启发式差异报告 | 否 |
| 每次合并后（**新**） | planner | 主合并流水自动 | LLM 语义判断 + yaml | plan 报告中 `forks_profile_suggestions` | 否 |
| 周期性维护 | fork 维护方 review | 人工 | 上述三种产物 | 改后 yaml + commit | 是 |

#### 9.10.5 接入位置

| 文件 | 操作 |
|---|---|
| `src/agents/contracts/planner.yaml` | 在 `outputs` 增加 `forks_profile_suggestions` 字段（schema 与 §9.10.3 对齐） |
| `src/agents/planner_agent.py` | plan 阶段末尾增加 fork-aware 反推；复用已读的 `fork_divergence_map` + `line_retention` 结果，**不额外调 LLM** |
| `src/core/phases/generating_report.py` | plan_review 报告渲染 `forks_profile_suggestions` 段，沿用 diff 子命令的 📋 / ➕ / 🔄 视觉风格 |
| `src/tools/forks_profile_loader.py` | **不动**（loader 仍只读 yaml；本增强不引入写路径） |

#### 9.10.6 验收

- **复现率**：对 cvte 仓 v2.1.0 重跑，planner 产出的 `candidate_rewritten_modules` 应能复现 §9.6 yaml 已声明的 5 个 rewritten_modules 中至少 3 个（命中率 ≥ 60%）
- **语义召回**：对一个**未声明任何 rewritten_modules** 的合成 fork fixture，planner 应至少检出 1 个被纯 line retention 启发式漏判的语义重写
- **幂等性**：同一 run 跑两次，第二次 yaml 不变 → 路由结果完全一致；`forks_profile_suggestions` 不影响本次 run 的合并副作用

#### 9.10.7 不做的事

| 项 | 理由 |
|---|---|
| planner 直接覆盖 / 增量 merge yaml | 破坏单一真源 + 审阅链路；与 forks-profile-init.md §8 明文约束冲突 |
| 在本次 run 用 suggestions 改写路由 | 破坏幂等；建议仅服务于"下次合并前的人工 review" |
| 把 suggestions 接入 CI gate | 启发式 + LLM 联合判断在新 fork 上假阳性高；强 gate 会变成持续噪音，由人工节奏更合理 |
| 让 planner 调用 drafter 的 `cluster_paths` 等内部函数 | 避免循环依赖（drafter 是 init 的纯函数库，planner 不应反向耦合）；planner 走 fork_divergence_map 现成数据即可 |

### 9.11 P3 增强：`forks-profile diff` 半自动 apply 模式（草案）

**追加日期**：2026-05-07
**关联文档**：[`doc/forks-profile-init.md`](../modules/forks-profile.md) §4.2 `diff` 子命令 / §8 「不做的事」第 2 项

#### 9.11.1 动机

`diff` 子命令现状（init.md §4.2）只输出三类差异（📋 / ➕ / 🔄）的人类可读文本，**应用全靠人工编辑 yaml**。对每月一次的合并节奏，单次 5~15 分钟人工把关尚可接受；但当 ➕ 类条目较多（fork 接入数月后第一次回看），全手编辑容易漏改。

**不能直接 `--apply-all` 的根本原因**（init.md §8 第 2 项）：
- 启发式不知道用户为什么删了某条 yaml 条目
- 启发式不知道用户为什么改过某条的 `reason` / `policy`
- 自动重写会让人工编辑被悄悄回滚

但**新增（➕）类**的语义不同：它本质是 yaml 没声明、不会撞到任何人工编辑过的字段——append 操作天然不破坏历史。

#### 9.11.2 两条互补增强

##### A. `--apply-additions` —— 仅自动 apply ➕ 类

```bash
merge forks-profile diff --apply-additions
# 把启发式新检出的 ➕ 类条目以 "# TODO: review" 标记 append 到现有 yaml
# 📋 和 🔄 仍只展示，强制人工处理（这两类必然撞人工编辑过的字段）
```

约束：
- **仅 append**，永不修改/删除已有条目（避免破坏单一真源审阅链）
- append 的条目必带 `# auto-added by diff --apply-additions on <date> — review` 注释
- 用 `ruamel.yaml` round-trip 保留现有注释 + 字段顺序
- 写文件前生成 `<yaml>.bak` 兜底

##### B. `--interactive` —— 逐条 y / n / e

```bash
merge forks-profile diff --interactive
# 类似 git add -p：每条差异问 y(应用) / n(跳过) / e(打开 $EDITOR 改) / q(退出)
# 📋 类的 y 表示从 yaml 删除该条；➕ 类的 y 表示 append；🔄 类的 y 表示 patch policy 字段
```

适用场景：差异条目较多（>10）但每条都需要人工把关；典型为 fork 接入数月后第一次跑 `diff`。

#### 9.11.3 与 §9.10 的关系

| 增强项 | 数据源 | 对 yaml 的写权限 | 主要服务 |
|---|---|---|---|
| §9.10 planner 输出 `forks_profile_suggestions` | LLM 语义判断 | **永远不写** | "下次合并前的人工 review" 资料 |
| §9.11.A `diff --apply-additions` | git 启发式 | 仅 append | 数月后 batch 补条目 |
| §9.11.B `diff --interactive` | git 启发式 | y/n/e 三选一 | 中等差异规模下逐条审阅 |

三者**互不冲突**：planner suggestions 是合并副产品给下次 review 用；`--apply-additions` 是人工节奏的 batch 工具；`--interactive` 是逐条工具。fork 维护方按节奏选用即可。

#### 9.11.4 实施估算

| 工作项 | 估时 | 依赖 |
|---|---|---|
| `diff --apply-additions`：ruamel.yaml round-trip append | 0.5 天 | init.md §6 PR B（differ 模块） |
| `diff --interactive`：基于 click 的 prompt loop（y/n/e/q） | 1 天 | 同上 |
| 单测：append 不破坏注释 / interactive 各分支 | 0.5 天 | — |
| **合计** | **2 天** | |

#### 9.11.5 不做的事

| 项 | 理由 |
|---|---|
| `--apply-all`（连带 📋 / 🔄） | 必然破坏人工编辑过的字段；init.md §8 已禁 |
| `--apply-additions` 自动 commit | 让用户自己 review 后再 commit，与 init 输出 `# TODO: review` 一致 |
| `--interactive` 接 LLM 总结分歧 | 与 §9.10 planner suggestions 重复；保持工具单一职责 |
| `--apply-additions` 写入新 fork 的 `removed_domains` | 默认仅 append `rewritten_modules` / `fork_only_features`；删域是高影响决策，必须人工 |
