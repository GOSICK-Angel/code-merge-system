# gatekeeper-code 审查报告（Phase 3 v1）

源：`code/phase-3/v1.md` / commit `8f81798`
HEAD = `8f81798`（feat/web 分支，未推送）；基线 HEAD = `1780dec`（Phase 2 出口）

## 结论
**要求修改**（1 项 P1：U-P3.5 / U-P3.6 / U-P3.8 实际不能 regression-protect 对应 3 个接入点；经验证可删除 src 中 assert 行测试仍全绿）

## 契约核查表

| Plan / Test FINAL 契约 | 状态 | 锚点 |
|---|---|---|
| `FileShardOverlap` 异常类继承 `ValueError`，**非** `SystemExit` / `AssertionError` / `RuntimeError`（lock #31） | ✅ | `src/core/parallel_file_runner.py:19-24` |
| `assert_disjoint_file_shards(shards: list[list[str]]) -> None` 纯函数 / 不副作用 / 返回 None | ✅ | `src/core/parallel_file_runner.py:27-41` |
| `str(FileShardOverlap)` 含重叠 file_path 字面值（U-P3.2 (b) 守护） | ✅ | `:41` `f"file shards overlap on: {duplicates}"`；`test_disjoint_assert_raises_on_overlap` 断言 `"b.py" in str(exc.value)` |
| 接入点 1：`conflict_analyst_agent.py` multi-file fan-out（plan #5 第 1 处） | ✅ | `src/agents/conflict_analyst_agent.py:107-109` runner.run_files 调用前 |
| 接入点 2：`conflict_analyst_agent.py` chunked path runner（plan #5 第 6 处） | ✅ | `src/agents/conflict_analyst_agent.py:291-296` runner.run_files 调用前；shard 形态 `"<file>#<idx>"` |
| 接入点 3：`executor_agent.py:829` rebuttal chunk runner | ✅ | `src/agents/executor_agent.py:832-837` runner.run_files 调用前 |
| 接入点 4：`planner_agent.py:645` `_classify_batch` sub-chunks | ✅ | `src/agents/planner_agent.py:648-653` runner.run_files 调用前 |
| 接入点 5：`judge_agent.py:167` per-file fan-out | ✅ | `src/agents/judge_agent.py:170-173` runner.run_files 调用前 |
| 接入点 6：`judge_agent.py:1473` chunk runner | ✅ | `src/agents/judge_agent.py:1480-1483` chunk_runner.run_files 调用前 |
| **mock 边界**：`MagicMock(wraps=原)`，绝不替换实现体（lock #31 / test FINAL §5.4） | ✅ | `tests/unit/test_disjoint_assert.py:77, 136, 172, 189, 231, 292, 304` 全部用 `MagicMock(wraps=...)` |
| **每个接入点测试必须独立验证 assert call 发生在对应 file:line 附近**（test FINAL §11 第 558 行） | ❌ **P1** | U-P3.3 (executor) / U-P3.4 (planner) / U-P3.7 (conflict_analyst chunked) — ✅ 驱动真实 agent path；U-P3.5 / U-P3.6 / U-P3.8 (a)(d) — ❌ 仅直接 `module.assert_disjoint_file_shards(...)`，未 instantiate Agent，未走 fan-out 函数体 |

## 测试结果

- `pytest tests/unit/ --cov=src --cov-report=term -q`：**2354 passed / 1 skipped / coverage 83.59%**
  - 基线 Phase 2 出口：2345 / 83.60% → 净 +9 测试 / -0.01pp（容差内）
- `mypy src`：**Success: no issues found in 160 source files**
- `ruff check src/ tests/unit/test_disjoint_assert.py`：**All checks passed!**

## P0 / P1 / P2 分级问题

### P1-1 — U-P3.5 / U-P3.6 / U-P3.8 (a)(d) 不能 regression-protect 对应接入点

**测试形态**：

```python
# 例 U-P3.5 (test_disjoint_assert.py:169-180)
spy = MagicMock(wraps=assert_disjoint_file_shards)
with patch.object(judge_agent, "assert_disjoint_file_shards", spy):
    judge_agent.assert_disjoint_file_shards([[fp] for fp in keys])  # 直接调用模块内 alias
assert spy.call_count == 1
```

这只验证 helper 自身可被 spy 调用 + 入参形态，**没有 instantiate `JudgeAgent`，没有进入 `judge_agent.py:_review_high_risk_files_async`** 函数体。U-P3.6 / U-P3.8 (a)(d) 同形态。

**经验证（empirical regression test）**：

1. 注释掉 `src/agents/judge_agent.py:173` 与 `:1483` 两处 `assert_disjoint_file_shards(...)` 调用 → 重新跑 `tests/unit/test_disjoint_assert.py::TestJudgePerFileFanOut tests/unit/test_disjoint_assert.py::TestJudgeChunkRunnerFanOut` → **2 passed**（应该 fail 但 pass）。
2. 同样注释 `src/agents/conflict_analyst_agent.py:109` → `tests/unit/test_disjoint_assert.py::TestConflictAnalystMultiFileFanOut` → **2 passed**（应该 fail 但 pass）。

= 3 个接入点（plan #5 第 1, 4, 5 接入点）实际**没有 regression 守护**。删除生产代码中的 assert 行不会触发任何测试失败，违反 test FINAL §11 (line 558) 锁定的"每个接入点测试必须独立验证 `assert_disjoint_file_shards` call 发生在对应 file:line 附近（非任意其他位置）"。

**对比 ✅ 用例**：U-P3.3 (executor `build_rebuttal`) / U-P3.4 (planner `_classify_batch`) / U-P3.7 (conflict_analyst `analyze_file` chunked) — 这 3 个测试**真实驱动 agent 方法**，spy 通过模块级 patch 捕获 wrapped helper 的调用；删除生产 assert 行**会**导致 spy.call_count==0 → 失败。

**v1.md "细节自纠"行 4 论据不成立**：

> "改为直接通过模块级 `judge_agent.assert_disjoint_file_shards([...])` 验证 patch + spy 形态（与接入点同一调用路径，但避免重建完整 JudgeAgent fixture）"

直接 module 级调用**不是**"与接入点同一调用路径"——接入点在 `_review_high_risk_files_async` / `_run_chunk_runner` / `run()` 函数体里，测试根本没进入这些函数。`MagicMock(wraps=原)` + `call_count` 在直接调用形态下退化为 "我刚刚调了 spy，所以 spy.call_count==1"，与生产路径完全脱钩。

**修复方案（任选其一，建议方案 A）**：

- **方案 A（最小侵入，推荐）**：参考 U-P3.3 / U-P3.4 / U-P3.7 的模式 — 真实 instantiate `JudgeAgent` / `ConflictAnalystAgent`，patch `_review_files_batch_llm` / `_call_llm_with_retry` 等下游 LLM 调用为 AsyncMock，触发真实 fan-out 路径，让 `MagicMock(wraps=assert_disjoint_file_shards)` spy 在模块级 patch 后捕获**真实的** agent 函数体内 assert 调用。即将 U-P3.5 / U-P3.6 / U-P3.8 改造成与 U-P3.3 / U-P3.4 / U-P3.7 一致的"真实 agent 路径"形态。
- **方案 B（轻量替代）**：保留现状直接调用 + 额外加 1 个 source-level assertion：用 `ast.parse(judge_agent_module_source)` 验证 `judge_agent.py:173` 与 `:1483` 行包含 `assert_disjoint_file_shards(` 字面值。可验证 line 锚点但不验证运行时调用。**该方案不能替代方案 A**（生产路径仍未测过），仅作辅助。

**规模评估**：3 个用例需重写；预计 +60~100 lines 测试代码。U-P3.5 (judge per-file) fixture 可参考 U-P3.4 (planner) 的 `_run_single_classify` AsyncMock 模式；U-P3.6 (judge chunk runner) 需触发 `_review_files_batch_llm` 切 chunks 路径（找出最低触发文件数）；U-P3.8 需走 `ConflictAnalystAgent.run(state)` 路径，参考 `test_state_thresholds.py:108-176`（同文件已有完整 MergeState fixture 模板）。

### P2 残留（不阻塞 GO；继承 Phase 2，未处理）

- **P2-1（Phase 2 滚来）**：`src/agents/base_agent.py` 830 行越 800 软约束。v1.md "P2 残留处理"段已显式延后到 Phase 5/6。同意。
- **P2-2（Phase 2 滚来）**：U-P2.16 (b) "id() 同源" 子断言。Phase 2 review 建议 Phase 3 v1 顺手补强，v1.md 显示**未处理**（task prompt 未要求）。本轮可接受延后，但既然要 v2 修 P1-1，建议 v2 顺手补 `state.config.thresholds.chunked_aggregation_min_confidence = 0.99` 修改后 `state.thresholds` 仍 == 0.91 的对照断言（+5 行）。

## 残留风险（如放行需关注，但本轮 NO-GO）

- 6 接入点已在 src/ 全部落地（生产行为正确），P1-1 是测试守护盲点而非生产 bug。
- planner_agent / judge_agent / executor_agent 文件大小已超基线（Phase 1 即超），本 Phase 仅 +8 ~ +14 行接入；Phase 5/6 拆 sections 时一并处理。

## 副作用检查（git diff `1780dec..8f81798`）

```
 src/agents/conflict_analyst_agent.py |  14 +-
 src/agents/executor_agent.py         |  11 +-
 src/agents/judge_agent.py            |  13 +-
 src/agents/planner_agent.py          |  11 +-
 src/core/parallel_file_runner.py     |  26 +++
 tests/unit/test_disjoint_assert.py   | 312 +++++++++++++++++++++++++++++++++++
 6 files changed, 383 insertions(+), 4 deletions(-)
```

6 个文件全部命中 v1.md 清单 + plan §2 Phase 3 范围。**无 Phase 3 外文件改动**；未引入计划外依赖；未触动 Phase 2 锁定的 base_agent budget cap / orchestrator try-except 分支 / state.thresholds 字段 / Phase 1 chunked reducer / Phase 0 RunBudgetExceeded dataclass / 7 contract yaml。

## Step 3 / 4 — 代码质量 / 安全

- **`FileShardOverlap` / `assert_disjoint_file_shards` 实现**：用 `Counter` 统计 + `sorted(duplicates)` 列表入 exc 字符串，确定性输出；纯函数，无副作用；mypy strict 通过。命名清晰。
- **6 处接入点位置**：全部紧贴对应 `runner.run_files` 调用之前，符合 plan §2 P3 "每个 fan-out 都过校验"语义。注释一律说明 *why*（防 file_diffs 重复 key / dict.keys() 名义 disjoint 仍 assert / chunking 重写防回归），符合 CLAUDE.md "Comments only when intent is non-obvious"。
- **`"<file>#<idx>"` chunked shard 形态**（接入点 2）：符合 lock #31 "U-P3.7 接入参数形态由 Executor 实施时决定"放权；保持 list[list[str]] 形态一致；既不会与真实 file_path 冲突（"#" 不是 Python 文件名合法字符 in 常规仓库），又保留 file 来源信息。
- **安全**：helper 不接 user-controlled 输入流；exc 字符串仅含 sorted duplicate file_path（来自 git diff / agent 内部计算路径），无 shell escape / template injection 风险；不引入新 TOCTOU / 资源泄漏表面。
- **mypy strict / ruff**：全部通过。

## 二审及之后

本 Phase 第一次送审，无上轮反馈核查项。

## 修订建议（v2 提交）

1. **必须**：修 P1-1。U-P3.5 / U-P3.6 / U-P3.8 改造为真实 agent 路径形态（推荐方案 A）。
   - 改造后 acceptance：注释掉 `src/agents/judge_agent.py:173 / :1483` 或 `conflict_analyst_agent.py:109` 任意一处 assert 行 → 对应测试必须失败（spy.call_count==0）。
2. **建议**：顺手补 P2-2 对照断言（+5 行 `tests/unit/test_state_thresholds.py`）。
3. **不要改动**：production src/ 中 6 个 接入点（已通过审查）、helper 实现、Phase 0/1/2 已锁定路径。
