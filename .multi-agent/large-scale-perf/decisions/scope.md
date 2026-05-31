# Scope Decision — large-scale-perf

> main agent 根据 plan/FINAL.md + AskUserQuestion 用户答复落定，时间：2026-05-18。
> 此文件锁定本会话推进范围。Verifier / Executor 严格遵守；超范围工作记入 P2 留后续会话。

---

## 1. 本会话推进范围

**选定 Phase**：**Phase 0 + Phase 1 + Phase 2**（共 3 个 Phase）

- Phase 0 — 基础设施（RunBudgetExceeded 异常 + AgentContract.version + 7 yaml version=1） — 0.5 天 / 1 commit
- Phase 1 — U1 conflict_analyst chunked analysis — 1.5 天 / 3 commit
- Phase 2 — U2 per-run budget + autosubmit — 1.5 天 / 3 commit

**合计**：~3.5 天 / 7 commit

**覆盖的 doc §10 验收**：
- **O1**（chunked path + fast-path ≥60% + hard cap <5%）— Phase 1 末
- **O2**（故意超 budget → AWAITING_HUMAN + 报告）— Phase 2 末

## 2. 本会话**不做**的 Phase（明确跳过，下次会话续接）

- Phase 3 — U5 disjoint contract
- Phase 4 — U7 worktree default
- Phase 5 — U3 cross-run cache
- Phase 6 — U4 RPM-aware concurrency
- Phase 7 — U6 per-file editable plan v2

**理由**：单会话 token 预算优先保证 forgejo 卡死场景（doc §1）有可发布修复（U1+U2）。其余 Phase 价值高但非阻塞，下次会话以同 team 名续接（参见 SKILL.md「会话续接」节）。

## 3. 实施纪律调整（用户选择）

### 3.1 Plan 细节自纠权限

**允许 Executor 直接采用实际值不回审**——前提：

- 仅限"细节自纠"：file:line 漂移、字段名微调、import 路径修正、未提及的 helper 函数复用
- **架构级偏离必须停下来 SendMessage 给 team-lead 报告**，例如：
  - 改 Phase 边界
  - 改契约（input/output schema）语义
  - 新增计划外字段
  - 引入计划外依赖（新 import / pyproject 改动）
- 每个 Phase 实施报告 `code/phase-N/v{R}.md` 必须含「计划细节自纠」专列表（格式见 templates/executor.md Step 5）：
  ```
  | 计划原文 | 实际 | 采用 | 锚点 |
  |---|---|---|---|
  ```
- gatekeeper-code 审查时一并核查自纠合理性

## 4. Verifier / Executor 严守事项

- **Verifier**：测试矩阵只覆盖 Phase 0/1/2，**绝不**为 Phase 3-7 设计测试（即使方便也跳过）。
- **Executor**：本会话只跑 Phase 0 → Phase 1 → Phase 2 顺序，**绝不**主动跑 Phase 3+。
- **gatekeeper-test / gatekeeper-code**：审查时仅核查 Phase 0/1/2 范围；超范围内容判 P0（"超范围"）。
- 锁清单（`locks/approved-facts.md`）中已通过的 plan 阶段事实 #11/#12 仍然有效（doc 全计划锁定 8 Phase 顺序），但本会话执行只触发前 3 个。

## 5. 续接锚点（下次会话用）

下次会话用户主动提"续接 large-scale-perf team"时，main agent 续接流程：

1. Read `facts.md` / `decisions/scope.md` / `locks/approved-facts.md` / `plan/FINAL.md` / `test/FINAL.md`（如已存在）
2. 用 `git log --grep="Phase"` 确定已完成的 Phase 0/1/2
3. AskUserQuestion 询问续接范围：
   - "续 Phase 3-7 全部？"
   - "续 Phase 3+4+5 三个？"
   - "仅续 Phase 5 cache？"
4. 续 team 时**重用同 team_name** `large-scale-perf`，Gatekeeper 起步 Read `locks/approved-facts.md` 重建基线

---

## 6. 会话 2 续接决策（2026-05-18 后续）

**用户答复**：

| 问题 | 选项 |
|---|---|
| 续接范围 | **Phase 2 + 3 + 4** |
| lock #27 路径 | **路径 A** — Phase 2 commit 1 同步在 `MergeState` 加 `thresholds: ThresholdConfig` 字段；从 `config.thresholds` 在 orchestrator init phase 复制 |

**本次推进的 Phase**：

- Phase 2 — U2 per-run budget + autosubmit（1.5 天 / 3 commit + 0.5 day MergeState.thresholds 字段；约 4 commit）
- Phase 3 — U5 disjointness contract（0.5 天 / 1 commit）
- Phase 4 — U7 worktree defaults（1 天 / 2 commit）

合计 ~3.0 天 / 7 commit。

**Verifier 二次扩展任务**：

`test/FINAL.md` 当前只覆盖 Phase 0/1/2（38 用例）。Phase 3/4 的用例尚未设计。Verifier 必须基于已通过 `test/FINAL.md` 产出 **test/v3.md**（新一轮迭代，叠加 Phase 3-4 用例，原 Phase 0/1/2 内容保持不变），经 gatekeeper-test 审查后更新 `test/FINAL.md`。

**lock #27 路径 A 实施细节**（Phase 2 Executor 必须按此推进）：

1. `src/models/state.py` `MergeState` 加 `thresholds: ThresholdConfig = Field(default_factory=ThresholdConfig)`（如已存在 ThresholdConfig 默认实例化路径则复用）
2. orchestrator init phase 中从 `state.config.thresholds` 复制到 `state.thresholds`（不直接引用 config，保持 view 的"运行态快照"语义）
3. 锁定 lock #27 P2-2：phase-1 中 `analyze_file` `thresholds` 参数从 `view.thresholds` 取，不再走 mock + setattr 合成路径
4. 同步修复 phase-1 P2 「`analyze_file` 新参数 `run()` 未驱动」未编号项（HANDOFF §4.3）
5. 1-2 个新单测验证字段存在 + 默认值 + restricted_view 可读

**Phase 1 残留风险归档情况**：

- lock #26（reducer hard cap total_bytes 语义偏差）：本会话**不修**，归 Phase 5 cache 接入时处理。
- lock #27（thresholds 接入）：本会话 **必修**，已选路径 A。
- 未编号 P2（`analyze_file` 新参数 `run()` 未驱动）：本会话 **必修**，与 lock #27 路径 A 一并解决。

**实施纪律延续**：3.1 节"Plan 细节自纠权限"仍然适用；架构级偏离仍需 SendMessage 报告 main agent。

**Phase 3/4 测试方案设计纪律延续**：4 节 Verifier/Executor 严守事项中"Phase 3+ 测试不设计"的约束在本会话**解除**——Verifier 可且仅可为 Phase 3-4 设计用例，**不得**涉及 Phase 5-7。

