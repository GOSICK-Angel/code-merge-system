# CodeMergeSystem 集成测试报告 — upstream-50-v2 验证重跑

**测试日期**：2026-04-23
**Run ID**：`6171dd37-9b02-4f86-9a08-b8bb64ee94c8`
**合并目标**：`dify-official-plugins` 的 `test/upstream-50-commits-v2` → `feat_merge`
**基线**：`feat_merge` @ `d73426c5`（已 reset）
**上游 HEAD**：`19d4300e`
**测试人**：Angel（同时担任人工决策者）
**模型**：与 v2 首轮一致（Anthropic claude-opus-4-6 / haiku-4-5；OpenAI gpt-5.4）
**目的**：验证基于首轮 [upstream-50-commits-v2](upstream-50-commits-v2-test-report.md) 暴露的 P0（O-M1/O-M2/O-L3）+ P1（O-B3）修复后，端到端链路是否畅通。

---

## 执行摘要

| 指标 | 值 |
|------|-----|
| **O-L4（无 ping-pong）** | ✅ 验证（单测 + E2E 59.1s 干净退出）|
| **O-M1（UU 文件拦截）** | ✅ 验证（2 文件成功转 ESCALATE_HUMAN）|
| **O-L3（dispute exhaust 不再 bounce）** | ✅ 验证（exhausted=['None']，注册 421 HDRs）|
| **O-B4（二进制字节路径）** | ⚠️ 单测 4 passed，E2E 未触发（原因见下）|
| **O-B3（9 binary 路由）** | ⚠️ E2E 未重新验证（见 O-B4）|
| **最终状态** | ❌ **FAILED**（非本轮修复范围，新暴露 2 个语义 gap）|
| **触发原因** | `git.exc.UnmergedEntriesError` on `models/anthropic/manifest.yaml` |
| AUTO_MERGE 实际耗时（第 1 次） | **59.1s**（对比 51-commits 的 14826s，消除死循环）|
| 总轮次 | 3 次 resume（初次 plan approve + decisions；2nd 触发 O-L4 fix；3rd 批量 take_target FAILED）|

---

## 本轮修复代码变更

### P0 — [9c0f454] AUTO_MERGE 死循环（O-M1/O-M2/O-L3）
- [conflict_markers.py](../../src/tools/conflict_markers.py) — 新 `has_conflict_markers`/`file_has_conflict_markers` 工具
- [auto_merge.py](../../src/core/phases/auto_merge.py) — O-M1 pre-scan 拦截 UU；O-L3 dispute exhaust 注册 HumanDecisionRequest
- [patch_applier.py](../../src/tools/patch_applier.py) — 写入前复检 conflict markers
- [judge_agent.py](../../src/agents/judge_agent.py)、[human_review.py](../../src/core/phases/human_review.py) — O-L3 exhausted → JUDGE_REVIEWING
- 单测：[test_p0_fixes.py](../../tests/unit/test_p0_fixes.py) 17 passed

### P1 — [513e352] 二进制资源白名单（O-B3）
- [binary_assets.py](../../src/tools/binary_assets.py) — 扩展名白名单 + `is_binary_asset`
- [auto_merge.py](../../src/core/phases/auto_merge.py) — O-B3 分类路由（C → escalate；B/D → TAKE_TARGET via `_copy_from_upstream`）
- 单测：[test_p1_fixes.py](../../tests/unit/test_p1_fixes.py) 7 passed

### 本轮新增 P0 — O-L4（resume item_decisions 注入解耦）
- [resume.py](../../src/cli/commands/resume.py) — item_decisions 注入脱离 `plan_human_review is None` 守护；已决条目不覆盖；plan 已存在时同步刷新 `item_decisions` 快照
- [human_review.py](../../src/core/phases/human_review.py) — Case 2 APPROVE 分支新增 undecided 兜底（O-L4 guard），避免 20s ping-pong
- 单测 2 passed：`test_resume_item_decisions_injected_after_plan_approved`、`test_human_review_stays_awaiting_when_items_undecided_after_approval`

### 本轮新增 P1 — O-B4（二进制字节路径）
- [git_tool.py](../../src/tools/git_tool.py) — 新 `get_file_bytes(ref, file_path) -> bytes | None`（`stdout_as_string=False`）
- [patch_applier.py](../../src/tools/patch_applier.py) — 新 `apply_bytes_with_snapshot`，`original_snapshot` 以 base64 存储（保持 pydantic 模型不变）
- [auto_merge.py](../../src/core/phases/auto_merge.py) — O-B3 `binary_take_target` 循环改走字节路径，绕过 executor text 写入
- 单测 4 passed：`test_get_file_bytes_*` / `test_apply_bytes_with_snapshot_*`

---

## 端到端时间线（3 次 resume）

| # | 时间 | 命令 | 结果 | 耗时 |
|---|---|---|---|---|
| 1 | 10:14 → 10:32 | `merge <branch>` → plan human review → plan_approval=approve + 3 tongyi downgrade_safe decisions | AUTO_MERGE 进入 → **AUTO_MERGE ↔ HumanReview 20s ping-pong** | ~18 min（被人工 kill）|
| 2 | 21:59 → 22:00 | `resume --run-id ...`（O-L4 fix 后首次，不带 decisions） | ✅ 干净退出到 AWAITING_HUMAN | **59.1s** |
| 3 | 22:16 → 22:16 | `resume --decisions batch.yaml`（421 take_target）| ❌ FAILED: `UnmergedEntriesError` | 10.2s |

---

## Phase 断言

### Phase AUTO_MERGE（第 1 次进入，Run #1）
- **O-M1 生效**：`WARNING O-M1: 2 file(s) contain unresolved conflict markers — escalating to human review: models/anthropic/pyproject.toml, models/gemini/models/tests/test_feature_compatibility.py`
- **O-B3 路由**：`INFO O-B3: routing 9 binary asset(s) to TAKE_TARGET and 0 to human escalation`
- **O-B3 旧路径失败（本轮修复的对象）**：
  - `vanna_configure.png`：`'utf-8' codec can't decode byte 0x89 in position 0`（`_copy_from_upstream` → `get_file_content` 返回 str 就失败）
  - `icon_s_en.png / img.png(x2) / workflow-*(x2) / actions.png / apikey.gif / tavilytool.gif`：`'utf-8' codec can't encode character '\udcXX'`（`get_file_content` 勉强返回 surrogate str，但 `apply_with_snapshot.write_text(..., encoding='utf-8')` 崩）

### Phase AUTO_MERGE（Run #2，O-L4 fix 后）
- **Replay**：`0 cherry-picked (0 partial), 29 failed` — 全部 fallback apply（fork/upstream 历史分歧过大）
- **Applied user downgrades**: 3 files（tongyi yaml）
- **Judge LLM calls**：3 轮 gpt-5.4（每轮 ~16k input tokens）
- **dispute**：`Layer None batch judge sub-review: no consensus after 2 dispute rounds`
- **O-L3 生效**：`auto_merge_dispute_exhausted_layers=['None']` 同时注册 **421 HumanDecisionRequests**
- **退出**：59.1s 内干净转到 AWAITING_HUMAN，**零 ping-pong**（log 中 `"plan approved"` 出现 0 次）

### Phase HUMAN_REVIEW（Run #3，带 421 decisions）
- **Loaded 421 decisions from decisions-batch.yaml**
- **Case 1 pending check**：0 pending（decisions 全部 load 进 `human_decision`）
- **执行循环**：`Executed 0 human decisions — proceeding to judge review`
  - **语义 gap**：human_review.py Case 1 的 executor 执行循环要求 `req.file_path NOT in file_decision_records`。421 文件在 Run #2 AUTO_MERGE 主循环时已全部 write 过 FileDecisionRecord，所以 take_target decisions **全部被跳过**。
- **Commit 尝试**：`commit_phase_changes` → `git.index.fun.write_tree_from_cache` → **UnmergedEntriesError on `models/anthropic/manifest.yaml`**

---

## 新暴露的 bug（供下一轮修复）

### O-M2（P0，新）— Commit phase 对 index 未解决 entry 无防护
**现象**：cherry-pick fallback 留下 2 条 unmerged entries（`manifest.yaml`、`pyproject.toml`），HumanReview 走到 `commit_phase_changes` 时 `write_tree_from_cache` 直接抛 `UnmergedEntriesError`，状态机无兜底、直接 FAILED。

**建议修法**：
1. `git_committer.commit_phase_changes` 前调用 `git diff --name-only --diff-filter=U` 检查 unmerged entries。
2. 若存在：对每个 unmerged file 查 `file_decision_records[fp].decision`，按决策执行 `git add` / `git rm --cached` / 调用 `apply_with_snapshot` 重写；都不匹配则转 AWAITING_HUMAN 而非 FAILED。
3. 单测：seed repo with `git update-index --cacheinfo 100644 $SHA 1 path` 模拟 stage-1 entry，断言 commit phase 能清理或 escalate。

### O-L5（P0，新）— UserDecisionItem 的 take_target/take_current 从未被"执行"
**现象**：O-M1 conflict_markers_* 和 O-B3 binary_asset_* 的 UserDecisionItem 选项含 `take_target`/`take_current`，但 auto_merge.py 的 `user_choice_by_path` 只对 HUMAN_REQUIRED 批次的 downgrade 做映射。用户选了 `take_target` 后：`pending_user_decisions` 更新了，`file_decision_records` 里却还是 ESCALATE_HUMAN（O-M1 预设的）—— 文件本体从未被 upstream 覆盖。

**建议修法**：
1. auto_merge.py pre-pass 全部 decided 后，增加一步 "执行 UserDecisionItem 选择"：对 `current_classification == HUMAN_REQUIRED` 且 `user_choice in {take_target, take_current}` 的项，调用 `apply_bytes_with_snapshot` / `apply_with_snapshot` 并覆写 `file_decision_records[fp]`。
2. 新增测试：构造 conflict_markers_ item with `user_choice=take_target` → 断言 file_decision_records 变为 TAKE_TARGET 且工作树内容 = upstream。

### O-B4-e2e-gap（P1，新）— AUTO_MERGE 已完成时 O-B4 不会重跑
**现象**：本轮清理了 9 个失败 binary 的 file_decision_records 后 resume，系统直接从 AWAITING_HUMAN 走 Case 1，AUTO_MERGE phase 被跳过（因状态早已是 awaiting_human），O-B4 的 bytes 路径完全没被触发。

**建议修法**（择一）：
1. **A（最小）**：HumanReview Case 1 执行循环后，扫描 `merge_plan.phases` 中 `is_binary_asset(fp)` 的文件，若 `file_path not in file_decision_records` 则直接走 O-B4 bytes 路径补写。
2. **B（对称）**：resume 前提供 `--rerun-auto-merge` 开关，状态 reset 到 AUTO_MERGING 重入。

---

## Cost / Memory / 上下文

| 项 | 值 |
|---|---|
| Run #2 LLM calls | 5 (judge 3 / executor 2) |
| Run #2 tokens | input 16860 / output 747 / cache read 0 / cache write 0 |
| Run #2 cost | **$0.191** |
| Run #2 avg latency | 6.57s |
| Run #3 | 0 LLM calls（纯执行路径）|
| Memory 更新 | 31 entries total, 28 new, 2 superseded removed |
| Context peak utilization | 0.47%（judge）— 远未到 80% warning 阈值 |

---

## 优化建议（按优先级）

1. **P0 — O-M2 + O-L5**：两者互相依赖，建议一并修。没有它们，O-M1/O-B3 的"拦截后提供选项"机制对 E2E 是失效的。
2. **P1 — O-B4-e2e-gap**：修完 O-M2 + O-L5 后顺带处理（HumanReview 循环里补写二进制即可复用 O-B4 路径）。
3. **P2 — 验证重跑**：在同一 test 场景上跑完整一轮，确认：
   - (a) 9 个 PNG 以 take_target 正确写入工作树（字节比对 SHA256）
   - (b) 2 个 O-M1 UU 文件按 user_choice=take_target 执行
   - (c) 421 HDR 正确完成 commit 并进入 JUDGE_REVIEWING
4. **P2 — HumanDecisionRequest 生成数量**：O-L3 dispute exhaust 一次性注册 421 HDR 过于粗粒度；建议只对实际有 issue 的文件（batch_verdict.issues 中涉及的 file_path）注册。

---

## 附件与复现路径

- 诊断 log：`/tmp/merge-upstream-50-v2-rerun2.log`（Run #2）、`/tmp/merge-upstream-50-v2-rerun3.log`（Run #3）
- Debug log：`outputs/debug/run_6171dd37-9b02-4f86-9a08-b8bb64ee94c8.log`
- Checkpoint 备份（O-B4 清理前）：`outputs/debug/checkpoints/checkpoint.json.bak-before-ob4-rerun`
- 批量 decisions yaml：`/tmp/merge-upstream-50-v2-rerun-decisions-batch.yaml`（421 take_target 条目）
- 单测覆盖：`tests/unit/test_p0_fixes.py`（20 passed）+ `tests/unit/test_p1_fixes.py`（11 passed）

## 下一轮 run 的交接点

目标 repo 当前 git 索引存在 2 个 unmerged entries（`models/anthropic/manifest.yaml`、`models/anthropic/pyproject.toml`）。在修 O-M2/O-L5 之前，需要手工 `git read-tree -m HEAD` 或 `git reset --hard d73426c5` 重置 repo，再启动新 run，不要基于当前 checkpoint 继续。
