# 评估系统执行计划（下阶段）

> **状态（2026-05-16 更新）**：原 P1/P2/P3 全部落地 + 5 个 src/ fix branch（P-α / P-γ-1 / P-γ-1.5-A / P-γ-1.5-B）+ Tier 1 dify-plugins acceptance **全 hard gate 过**（OA 1.0 / WMR 0 / DCRR 1.0）+ R2 r2-0001 与 golden **byte-equal**。PR #1 已 open，56 commits ahead of main，等 CI 合并。
> **本文用途**：登记下一阶段任务（P-γ-3 / Tier 2/3 / 低优 follow-up），并评估"用 agent team 实施"的可行性。
> **不修改**：`metrics.md / dataset.md / procedure.md / acceptance.md`（仅追加本文 + IMPLEMENTATION_REPORT_PARTIAL.md）。

---

## 0. 当前 baseline（2026-05-16）

| 项 | 状态 |
|---|---|
| 评估框架 `scripts/eval/` | Phase 0-9 + F5/F6/F7/F8/F9 + F-OverEscalation + F-CI-1/2 全闭环 |
| 评估单测 `tests/eval/` | 全 pass；新增覆盖率维持 |
| 项目单测 `tests/unit/` | 2241 pass（2 偶发 isolation flaky，独立跑全过）|
| Tier 1 数据集 | 30 dify-plugins 真样本（6 C + 24 B），meta backfilled（5 / 6 expected_human=true）|
| Tier 1 v4 acceptance | **OA 1.0 / WMR 0 / DCRR 1.0 / SSER 1.0 / RR 1.0 / RCR 1.0 / OverEscalation 0**（全 hard gate 过 — IMPLEMENTATION_REPORT §15）|
| R2 跨仓库 sample | r2-0001 (Go dify-plugin-daemon) v5 = **byte-equal golden**（§17）|
| DET 测试 | 1.0（5×3 seed，IMPLEMENTATION_REPORT §8）|
| CI `eval-tier1` | 全绿，多次手动触发通过 |
| PR #1 | open，feat/web → main，等 CI |
| 关键 src/ 修复 | `vacuously_complete_layers` (§13) + `three_way_merge_file` (§15) + `fork_delete_preserver` (§17) |

---

## 1. 已完成任务（历史快照）

| 原任务 | 状态 | 文档 |
|---|---|---|
| **P1** 30-sample 全量真跑 | ✅ 完成（v1 → v2 → v3 → v4 共 4 轮 baseline）| §7 / §11 / §14 / §15 |
| **P2** DET 一致性 | ✅ 完成（5×3 seed → DET 1.0）| §8 |
| **P3** CI eval-tier1 首次触发 | ✅ 完成（5 次 push 全绿）| §9 |
| **P-α** F9 区分 escalate vs crash | ✅ 完成 | §10 |
| **P-β** 第 2 次 baseline + relative gate | ✅ 完成 | §11 |
| **R2 smoke** 跨仓库 Go 项目 | ✅ 完成（暴露 cascade + classifier bug）| §12 |
| **P-γ-1** layered_execution 假级联 src/ fix | ✅ 完成 | §13 / commit `471ac17` |
| **P-γ-1.5-D** v3 baseline 数据分层 | ✅ 完成 | §14 |
| **P-γ-1.5-A** native 3-way merge 前置 src/ fix | ✅ 完成 | §15 / commit `77a8dd2` |
| **P-γ-2** R2 v4 单 sample 复测 | ✅ 完成（cost -100%, wall -95%）| §16 |
| **P-γ-1.5-B** fork-delete 保留 src/ fix | ✅ 完成 | §17 / commit `9b229af` |

---

## 2. 下阶段待办（按优先级）

### 2.1 P-γ-3：R2 多 sample 扩展 + dify-plugins v5 regression check

**目标**：把 R2 (Go) 从 1 sample 扩到 5 sample，跑 acceptance；同时跑 dify-plugins v5 30-sample 验证 P-γ-1.5-B 无 regression（按 §17.4 推断 dify-plugins 0 FORK_DELETED entries 应仍 OA=1.0）。

**前置**：PR #1 合并（或并行独立分支）。

**实施**：
1. **造 4 个新 R2 sample**：用 `sample_import --from-merge SHA` 从 cvte fork merge 历史挖 4 个有代表性的 commit（候选 SHA 见 dify-plugin-daemon `git log --merges`）。每个 sample 需手工 backfill `meta.yaml` 的 `category` 与 `expected_human` 字段。
2. **跑 5 R2 sample baseline v5**：用 `/tmp/eval-runs/run_v4_r2.sh r2-0001 r2-0002 ... r2-0005`
3. **跑 dify-plugins v5 30-sample**：用 `/tmp/eval-runs/run_v4_full.sh` 全 30 重跑，对照 v4 数据
4. **summarize + 写 §18**

**估算**：
| 维度 | 数值 |
|---|---|
| R2 5 sample wall | ~80s (5×16s)，若有 sample 触发 LLM 主路径则按需上调 |
| R2 cost | < $5（v5 后大部分走 native + cherry-pick）|
| dify v5 wall | ~6 min |
| dify v5 cost | ~$0.6 |
| 总开销 | < $6 / ~10 min |

**风险**：
- 新 R2 sample 可能暴露未知 src bug（如 §17.4 所言，cvte 二开形态多样）→ 触发新 fix branch
- `sample_import` 对 cvte fork 的 merge SHA 处理需要 `--from-merge` 选 PR-style merge commit
- 4 个新 sample 的 expected_human 标注需要人工判断（与 golden 比对）

**验收**：
- R2 5 sample summary 全 EXACT 或 expected SEMANTIC
- dify-plugins v5 acceptance 与 v4 全等（无 regression）
- 新增 §18 / §19 IMPLEMENTATION_REPORT

---

### 2.2 P-γ-4：F-judge-source-of-truth 低优修复

**目标**：修 §13.2 中 Judge verdict 文本引用 LLM proposal 而非 working_tree 的 ground-truth gap。当前未导致错误决策，但 verdict 文本产噪音（如曾说"file contains '# ... omitted' artifact"实际磁盘无此字符串）。

**前置**：无。可与 P-γ-3 并行。

**实施**：
1. 找 Judge 读 working_tree 的位点（应在 `src/agents/judge_agent.py` 或 `src/tools/three_way_diff.py`）
2. 加测试：构造 working_tree 与 LLM proposal 不一致的场景，断言 verdict 文本仅引用 working_tree
3. 修 Judge prompt builder（如适用），让 Judge 看到的"current state"就是磁盘读取

**估算**：1-2 hr 工作量，0 LLM cost（pure src/ + unit test）。

**验收**：unit test 覆盖；可选 R2 / Tier 1 重跑确认 verdict 文本干净。

---

### 2.3 P-γ-5：Tier 2 历史回放数据集

**目标**：按 `dataset.md §3` 设计，构造 ≥10 个跨 25 commits 的真实合并样本（dify-plugins 或 dify-plugin-daemon 任选）。

**前置**：P-γ-3 完成（Tier 1 + R2 全过后再扩展）。

**实施**：
1. 在 fork 仓库找 ≥10 个 large merge SHA（涉及 25+ commits）
2. 每个用 `sample_import --from-merge` 生成 tier 2 dataset
3. 跑 baseline
4. 评估 acceptance gate 是否需要调整阈值

**估算**：中等工作量；造数据 1-2 hr / sample；baseline cost 与 R2 类比 < $50 全集。

**验收**：tier 2 acceptance 数据入 lock；新增 §IMPLEMENTATION_REPORT 段。

---

### 2.4 P-γ-6：Tier 3 M-注入对抗集

**目标**：按 `dataset.md §4` 构造 M1-M6 每类 ≥ 5 个样本（30 sample）验证 system 对 misleading 输入的鲁棒性。

**前置**：P-γ-5 完成。

**实施**：需要专门的注入工具（dataset.md §4 列出 6 类 mutation）。当前框架未提供注入器 → 需新建 `scripts/eval/m_inject.py`。

**估算**：高工作量；造数据 + baseline 共 1-2 周。

---

## 3. 推荐执行顺序

```
P-γ-3 (5-R2 + v5 regression — < $6, < 10 min)
  ↓
P-γ-4 (F-judge-source-of-truth — pure src, 1-2 hr)
  ↓
P-γ-5 (Tier 2 数据集 — 中工作量)
  ↓
P-γ-6 (Tier 3 注入 — 高工作量)
```

理由：
- P-γ-3 最便宜，能立刻把 R2 acceptance 从单 sample 扩到统计意义
- P-γ-4 与 P-γ-3 可并行，无 LLM 开销
- P-γ-5/6 是数据集扩展，依赖框架稳态（P-γ-3 后再开）

---

## 4. 推迟项 / 已知 limitation（更新版）

| 项 | 状态 | 备注 |
|---|---|---|
| ~~F-CI-1: web/dist build~~ | ✅ 已修（§9）| |
| ~~F-CI-2: 终端宽度敏感~~ | ✅ 已修（§9）| |
| ~~F-WMR-t1-0003: cascade~~ | ✅ §13 修 | |
| ~~F-WMR-t1-0003-2: take_target~~ | ✅ §15 修（native merge）| |
| ~~F-d-classification-fork-removed-file~~ | ✅ §17 修 | |
| F-judge-source-of-truth | open（中）| Judge verdict 文本 ground-truth gap — P-γ-4 |
| F-F9-partial-escalate | open（低）| v5 后 R2 不再 escalate，此 corner case 极少触发 |
| F-executor-strategy-tuning | open（低）| native merge 接管 C 类，LLM 路径很少触发 |
| Tier-2 历史回放 | P-γ-5 | |
| Tier-3 M-注入对抗集 | P-γ-6 | |
| SRSR 真实路径 | plan v3 | 需要 `MergeState.snapshot_rollback_events` 字段 |
| WDR hard gate 重启用 | Tier-2 后 | |
| tree-sitter 真实 AST | optional dependency | 当前 fallback-bytes |
| `--baseline` 数值 delta | ✅ 已用 4 次 baseline 跑出来 | |
| 多文件 sample 聚合 | Phase 5 优化 | 当前 `_diff_one_sample` 只取首个 decision record |
| nightly cron | 数据集 ≥ 50 + 预算确认 | 当前 `workflow_dispatch` only |
| forks-profile 完整 authoring | open | cvte fork `forks-profile.yaml` `fork: name` 仍空，未影响 acceptance |

---

## 5. 文档维护规则

- 完成 P-γ-3 / 4 / 5 / 6 任一后：追加 IMPLEMENTATION_REPORT §18 / 19 / 20 / 21（保持序号连续）
- 发现新 src/ bug：本文 §2 新增任务条目，commit message 引用 `repro:` 真实证据
- src/ fix 必须配 unit test + e2e 验证（baseline 或 R2 单 sample），不能仅靠 unit
- PR 合并 main 后：本文 §0 baseline 表更新 commit ref

---

## 6. 已通过 acceptance 阈值清单（v4 实测）

来自 `tests/eval/manifests/acceptance_thresholds.yaml` + IMPLEMENTATION_REPORT §15：

**Hard gates (13)**：
- WMR ≤ 0.05 → **0.0** ✅
- MMR ≤ 0.02 → **0.0** ✅
- SSER == 1.0 → **1.0** ✅
- DCRR == 1.0 → **1.0** ✅
- RR == 1.0 → **1.0** ✅
- RCR == 1.0 → **1.0** ✅
- Recall_M1-M6 ≥ 0.95 → N/A（Tier 3 未跑）
- SRSR — auto-SKIP

**Soft gates (9)**：
- OA ≥ 0.95 → **1.0** ✅
- CRA ≥ 0.95 → **1.0** ✅
- OverEscalationRate ≤ 0.05 → **0.0** ✅
- JA ≥ 0.9 → N/A (follow-up)
- DET ≥ 0.9 → **1.0** ✅
- CPC ≥ 0.85 → N/A (multi-provider)
- cost_p95 relative 1.15× → N/A baseline 不变
- wall_p95 relative 1.20× → 实测大幅低于（v3 24.1s → v4 15.6s）
- plan_revision_rounds_p95 1.0× → N/A

→ Tier 1 dify-plugins acceptance **全过**。R2 r2-0001 单 sample 全过。

---

## 7. Agent team 实施可行性评估（不在本会话执行）

针对 §2 的 4 项待办，按"是否适合 agent team 并行实施"分级：

### 7.1 高适配（**推荐**用 agent team）

| 任务 | 理由 | 推荐 agent 分工 |
|---|---|---|
| **P-γ-5 Tier 2 数据集构造** | 每个 sample 独立、流程标准化（pick SHA → sample_import → backfill meta → lock）。10 sample 并行可降 wall 80% | • `general-purpose` × N：每个负责 1-2 sample 端到端造数据<br>• `code-reviewer`：抽样审 meta.yaml 标注是否合理 |
| **P-γ-6 Tier 3 M-注入工具 + 数据集** | 6 类 mutation 独立、注入工具 + 数据可并行 | • `feature-dev:code-architect`：先设计注入器接口<br>• `general-purpose` × 6：每类 mutation 一个 agent 实现 + 造 5 sample<br>• `code-reviewer`：注入语义正确性审查 |
| **P-γ-4 F-judge-source-of-truth** | 单文件改动 + 配单测；可让 `debugger` 探 + `feature-dev:code-reviewer` 验 | • `debugger`：探 Judge ground-truth gap 真实位点<br>• `feature-dev:code-architect`：设计 fix<br>• `code-reviewer`：审 PR |

### 7.2 中适配（部分 agent，关键人工决策）

| 任务 | 理由 | 限制 |
|---|---|---|
| **P-γ-3 R2 多 sample 扩展** | 造 4 sample 可并行（agent 友好），但跑 baseline 是顺序的（API 限流 + sample 隔离），agent 加值有限 | sample expected_human 标注需人工判断，不能 full-auto |
| **forks-profile 完整 authoring** | 需要 cvte 二开领域知识（用户自己最熟），agent 能起草但定稿需人审 | 决策依赖业务上下文 |

### 7.3 低适配（**不建议**用 agent team）

| 任务 | 理由 |
|---|---|
| **跑 e2e baseline / 收 acceptance 数据** | API 调用串行执行，agent 添加 orchestration 开销而无并行收益。直接 `bash` 跑即可 |
| **PR 合并决策 / 阈值调整** | 需要全局视角 + 用户判断，agent 工作流冗余 |
| **src/ 紧耦合 refactor** | 如 `MergeState` schema 演进、Judge 与 ConflictAnalyst 协议变更 — agent 间需频繁同步设计，merge conflict 概率高 |

### 7.4 总体可行性结论

| 维度 | 评估 |
|---|---|
| **整体可行性** | ✅ 中高 — §2 大部分任务能 agent team 并行 |
| **建议 team 结构** | 1 leader (`claude` 协调) + 3-6 worker (`general-purpose` 造数据 / `feature-dev:*` 设计 / `code-reviewer` 审 / `debugger` 探 bug) |
| **不建议 full-auto 的环节** | sample expected_human 标注 / forks-profile 业务字段 / PR 合并决策 / 阈值调整 |
| **关键工作流约束** | (a) 每个 agent worktree 隔离避免 merge conflict；(b) 数据集类任务必须 leader 在 agent 提交前校验 `lock --verify`；(c) src fix 类任务必须 leader 强制 e2e baseline 验证（不仅 unit） |
| **预期收益** | wall-clock 缩 60-80%（数据集任务）；总 LLM cost 略增 10-30%（agent 协调）；人工 review 量约持平 |
| **潜在风险** | (a) agent 跨任务 commit message 风格不统一；(b) 数据集 sample_id 撞号需 leader 预分配；(c) `IMPLEMENTATION_REPORT` 多 agent 同时追加章节会冲突 — 建议每 agent 写独立子文档，leader 最后合并 |

**推荐启动方式**：
1. **先 P-γ-3 单 agent 验证**（agent team 基础流程演练）
2. **再 P-γ-5 Tier 2 数据集** 全力开 agent team（最大收益）
3. **P-γ-6 Tier 3 注入** 是 agent team 的"招牌项目" — 6 类并行最适合
4. **P-γ-4 与上述任一并行** 用 1 agent 单独承包
