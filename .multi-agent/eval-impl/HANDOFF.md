# 会话交接文档（HANDOFF）

> **暂停时间**：2026-05-16
> **暂停原因**：会话 token 不足以完成 Phase 7-9
> **下次会话续接**：用户提"continue eval-impl" 或"续接评估方案落地"

---

## 1. 当前进度（Phase 0-6 全部 GO）

| Phase | 内容 | commit | 测试 / cov | gatekeeper-code 锁清单 |
|---|---|---|---|---|
| 0 | scripts/eval 骨架 + _common/_schemas/_fork_name_check + tests/eval 框架 | `5658e77` | 54/54, 95.58% | [code-phase-0] 5 条 |
| 1 | lock.py + tier1/tier3 reference sample | `1101b2a` | 74/74, 94.56% | [code-phase-1] 7 条 |
| 2 | prepare.py + _ground_truth.py | `8cb3f84` | 101/101, 94.12% | [code-phase-2] 6 条 |
| 3 | run.py（最大风险点，env/cwd/memory 三层隔离 + concurrency） | `c3e2c6c` | 121/121, 93.92% | [code-phase-3] 7 条 |
| 4 | _ast_equiv.py + diff_against_golden.py + prepare 重构 | `6d3871e` | 156/156, 94.16% | [code-phase-4] 7 条 |
| 5 | summarize.py + _report_render.py + jinja2 模板 | `bf4cfda` | 185/185, 94.27% | [code-phase-5] 6 条 |
| 6 | gate.py + acceptance_thresholds.yaml + plan-amend kind/multiplier | `000d646` | 216/216, 94.52% | [code-phase-6] 8 条 |

**HEAD = `000d646`（Phase 6）**。所有 commit 都在 `feat/web` 分支，未 push。

## 2. 待完成 Phase（按依赖顺序）

### Phase 7 — consistency.py（估时 0.5d）
- 详见 `.multi-agent/eval-impl/plan/FINAL.md` §Phase 7 (ll.278-288)
- 派任务消息已在前会话发出（Executor 未启动实施，无 WIP commit）
- 派任务摘要：
  - DET / CPC 计算（N runs 比对 strategy + target_risk_level）
  - 不触发 N 次跑（plan 决策 3：调用方 shell 循环）
  - 顺手修 Phase 6 P2-1..P2-4
  - 测试 T7-C1..C5
- 派任务全文见 `.multi-agent/eval-impl/HANDOFF_phase7_dispatch.md`

### Phase 8 — 端到端集成测试（估时 0.5d）
- plan §Phase 8 (ll.290-298)
- 用 fake `merge-bin` 跑通 procedure.md §2 全部 5 步：prepare → run → diff → summarize → gate
- 断言 `eval_acceptance_*.json.verdict == "PASS"`

### Phase 9 — CI 接入（估时 0.5d）
- plan §Phase 9 (ll.300-331)
- 在 `.github/workflows/ci.yml` 现有 `test` job 后追加 5 step（lint/format/mypy/pytest+cov/lock-verify/fork-name-check）
- 新增 manual-only `eval-tier1` job（workflow_dispatch + nightly placeholder）
- 不阻塞 PR

### 最终任务（user-facing）
- 写 `doc/evaluation/IMPLEMENTATION_REPORT.md` —— 落地交付完整报告（按 scope.md §5）

---

## 3. 续接流程（下次会话）

main agent 在新会话收到"continue eval-impl"后：

1. **验证状态**：
   ```bash
   git log --oneline -10        # 确认 HEAD = 000d646 + Phase 0-6 commits 在
   git status                   # 应该 clean (除 .multi-agent/ doc/evaluation/ untracked)
   ls .multi-agent/eval-impl/   # 应有 facts.md, plan/FINAL.md, test/FINAL.md, code/phase-{0..6}/FINAL.md, locks/, decisions/
   ```

2. **Read 关键文件重建上下文**：
   - `.multi-agent/eval-impl/HANDOFF.md`（本文件）
   - `.multi-agent/eval-impl/facts.md`
   - `.multi-agent/eval-impl/plan/FINAL.md`
   - `.multi-agent/eval-impl/test/FINAL.md`
   - `.multi-agent/eval-impl/decisions/scope.md`
   - `.multi-agent/eval-impl/locks/approved-facts.md`（含 [code-phase-0..6] 全部 + plan-amend + test-amend）
   - `.multi-agent/eval-impl/code/phase-6/FINAL.md`（最近一个 GO 报告）

3. **检查是否有 stash**：
   ```bash
   git stash list | grep "wip(eval)"
   ```
   若有，参考 stash 内容继续 Phase 7；否则从零按派单消息推进。

4. **TeamCreate eval-impl-2** 或 **复用同名 team**（如已 TeamDelete 则新建）：
   - 起 2 个 teammate：`executor` + `gatekeeper-code`（plan/test 阶段已闭环，不需重启）
   - Executor first action: Read locks/approved-facts.md 重建基线
   - gatekeeper-code first action: 同上 + Read code/phase-6/FINAL.md

5. **派 Phase 7 任务**（参考 `.multi-agent/eval-impl/HANDOFF_phase7_dispatch.md` 的派单全文）

6. **滚动推进 Phase 8 → Phase 9**

7. **完成后写 doc/evaluation/IMPLEMENTATION_REPORT.md** + shutdown 两个 teammate + TeamDelete

---

## 4. 已知 carry-forward（Phase 7 必须处理）

来自 `[code-phase-6]` 审查 P2-1..P2-4：

| 编号 | 内容 |
|---|---|
| P2-1 | `assert ... is not None` type narrow → 改成显式 `if/raise`（防 `python -O` 剥离） |
| P2-2 | gate 缺指标 → SKIP 路径补显式 test |
| P2-3 | summarize._compute_rr glob 多匹配 → 显式选最新或报错 |
| P2-4 | acceptance_thresholds.yaml SRSR hard gate 隐式 SKIP → 显式 marker |

来自 `[code-phase-4]` Tier-2 follow-up（Phase 9 之后才做，本期跳过）：
- WDR 完整 MISS_FORK 区分
- SRSR 真实生成路径（T4-D10）
- tree-sitter 真实 AST（[optional-dependencies.ast] 安装 + 行为切换）

---

## 5. 关键约束（绝对不要破坏）

- 不修改 `src/` 任何代码（违反即 NO-GO）
- 不修改 `doc/evaluation/{README,metrics,dataset,procedure,acceptance}.md`（仅追加 `IMPLEMENTATION_REPORT.md`）
- 不引入新运行时依赖（dev 已有 + tree-sitter optional 即可）
- 不在代码出现 cvte / dify / insforge（fixture 路径除外，且 generic）
- mypy strict / ruff / pytest --cov ≥ 80% 全绿
- 文件 ≤ 800 行 / 无中文注释 / pydantic v2 / 无 mutation
- TaskCreate 续接 task；Executor commit 标准 `feat(eval): Phase N — ...`

---

## 6. 团队成员状态（暂停时）

| 角色 | 状态 | 备注 |
|---|---|---|
| planner | terminated | plan 阶段完成（v2 通过） |
| gatekeeper-plan | terminated | 同上 |
| verifier | terminated | test 阶段完成（v2 通过） |
| gatekeeper-test | terminated | 同上 |
| executor | shutdown_request 已发，待 approve | 下次会话需重起 |
| gatekeeper-code | shutdown_request 已发，待 approve | 下次会话需重起 |

`~/.claude/teams/eval-impl/config.json` 与 `~/.claude/tasks/eval-impl/` 在两个 teammate shutdown 后会保留（除非显式 TeamDelete）。**建议本次会话不 TeamDelete**，保留 task list 给下次续接。

---

## 7. Token 预算建议（下次会话）

剩余 3 Phase + 最终报告，估计需要：
- Phase 7：~30k tokens（含 carry-forward 修复 + 测试 + commit + 审查）
- Phase 8：~25k tokens（e2e 集成测试，无新业务逻辑）
- Phase 9：~20k tokens（CI yaml + 测试，相对机械）
- IMPLEMENTATION_REPORT.md：~15k tokens（汇总报告）
- 调度 overhead（消息 + locks）：~30k tokens

**保守估计 120k+ tokens 完成剩余工作**。如果新会话也接近上限，建议优先级：Phase 7 → Phase 8 → IMPLEMENTATION_REPORT（部分）→ Phase 9（最次要，可手动后补）。
