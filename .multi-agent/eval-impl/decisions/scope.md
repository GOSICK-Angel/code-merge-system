# Scope Decision — eval-impl

> **决策模式**：无人值守，由 main agent (team-lead) 自主拍板
> **决策时间**：2026-05-15
> **决策依据**：plan/FINAL.md v2（gatekeeper-plan 已通过，10 Phase × 8.5 工作日）

---

## 1. 决策结论

**本次会话推进范围：全部 Phase 0-9**，目标是把 `doc/evaluation/` 评估方案完整落地为可运行代码 + CI 接入。

不分批不缩减——理由见 §2 ROI 分析。

---

## 2. ROI 取舍分析

候选方案：

| 方案 | 范围 | 优点 | 缺点 |
|---|---|---|---|
| **A. 全量 Phase 0-9** | 全部 10 Phase | 完整闭环：脚本 + 数据集 + e2e + CI；用户可立即跑通评估 | 估时 8.5d，单会话可能超 token；无 stopping point |
| B. 核心三件套（Phase 0/1/4） | 骨架 + lock + diff | 最小可用——能算 OA/MMR/WMR | 没 summarize / gate，无法出 acceptance verdict；用户无法直接判断"是否可信" |
| C. MVP（Phase 0-6） | 砍 7/8/9 | 能产 eval_report + acceptance.json；DET/CPC 缺失 | 无 e2e 自动验证整链；无 CI step |

**选 A** 的理由：
- 无人值守模式下用户不会中途介入，单会话推到尽头是最优路径
- 每个 Phase 强制 commit + artifact，token 耗尽时已 committed 部分不丢，下次会话靠 git log + `.multi-agent/` 续接
- Phase 0-2 + Phase 4-6 是评估器内核，跑通后即便 Phase 3 (run.py) 复杂度高失败，已交付的 lock/diff/summarize/gate 仍可独立用于离线评估
- 缺少 Phase 9 (CI) 评估永远手动跑，是落地"半成品"的最大瓶颈

---

## 3. Phase 推进顺序与并行机会

| 顺序 | Phase | 估时 | 依赖 | 可并行？ |
|---|---|---|---|---|
| 1 | **Phase 0** 骨架 + 数据模型 | 1.0d | 无 | ✅ **与 Verifier 测试方案并行** |
| 2 | Phase 1 lock.py | 0.5d | Phase 0 | |
| 3 | Phase 2 prepare.py + _ground_truth.py | 0.5d | Phase 1 | |
| 4 | Phase 3 run.py（最大风险）| 2.0d | Phase 2 | |
| 5 | Phase 4 _ast_equiv + diff_against_golden | 1.5d | Phase 3 | |
| 6 | Phase 5 summarize + _report_render | 1.0d | Phase 4 | |
| 7 | Phase 6 gate + acceptance_thresholds | 0.5d | Phase 5 | |
| 8 | Phase 7 consistency | 0.5d | Phase 5 | 与 Phase 6 可并行（依赖相同），但单 Executor 无法真并行——仍串行 |
| 9 | Phase 8 e2e | 0.5d | Phase 1-7 | |
| 10 | Phase 9 CI | 0.5d | Phase 8 | |

**关键决策**：
- **Phase 0 与 Verifier 测试设计并行**（SKILL 法则 5）—— 同条消息派两个任务
- 后续 Phase 严格串行（单 Executor 实例）
- gatekeeper-code 跨 Phase 复用，每 Phase 通过后 idle 等下个

---

## 4. 会话续接机制（token 预算保险）

按 SKILL 法则 4，每 Phase 完成必 commit + artifact。如果会话在 Phase N 中段耗尽：

1. Executor 已完成的 Phase 0..N-1 都已 commit；用户 git log 可见
2. `.multi-agent/eval-impl/code/phase-{0..N-1}/FINAL.md` 含每 Phase 的 commit hash + 决策上下文
3. 下次会话用户提"续接 eval-impl"，main agent 从 git log + .multi-agent/ 重建 + 派 Phase N

不依赖会话内部状态，依赖磁盘。

---

## 5. 最终报告

按用户要求，所有 Phase 完成后，main agent 输出**最终实施报告**到 `doc/evaluation/IMPLEMENTATION_REPORT.md`，含：

- 落地交付清单（按 Phase 分组）
- 每个 Phase 的 commit hash + 测试结果
- 已实现的 acceptance gate 验收能力 vs. acceptance.md 的对照
- 已知 limitation 与后续工程建议
- 用户复现命令

如会话耗尽未完成全部 Phase，最终报告改名 `IMPLEMENTATION_REPORT_PARTIAL.md` 并明确标注完成度。

---

## 6. 范围外（保留 facts.md §9 边界）

**不做**：
- Tier-2 真实历史合并构造（仅占位）
- Tier-3 6 类全集（仅 M3 一个示范）
- 实际 release-grade evaluation 跑（无真实 API key）
- CI PR 阻塞（仅 nightly placeholder）
- 修改 `src/` 任何代码
- 修改 `doc/evaluation/{README,metrics,dataset,procedure,acceptance}.md`（仅新建 IMPLEMENTATION_REPORT.md）
- 引入新运行时依赖
