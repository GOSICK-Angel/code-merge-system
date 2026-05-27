# gatekeeper-code 审查报告（Phase 3 v2）

源：`code/phase-3/v2.md` / commit `5d659a2`（fixup on `8f81798`）
HEAD = `5d659a2`（feat/web 分支，未推送）；基线 = `1780dec`（Phase 2 出口）

## 结论
**通过**（P1-1 修复，acceptance 经独立实测验证；P2-2 顺手补全；P2-1 延后至 Phase 5 继续延后）

## 上轮反馈落地核查

| review-v1 反馈项 | 严重度 | v2 落地 | gatekeeper 二审实测 |
|---|---|---|---|
| **P1-1**：U-P3.5 / U-P3.6 / U-P3.8 (a)(d) module-level call helper，无法 regression-protect plan #5 第 1/4/5 接入点 | P1（阻塞） | 改造方案 A：真实 instantiate Agent + 完整 MergeState fixture + patch 下游 LLM AsyncMock；`_build_judge_state` (line 166-213) + `_build_conflict_state` (line 389-453) 两个 helper；4 个用例分别走 `JudgeAgent.run` / `JudgeAgent.review_batch` / `ConflictAnalystAgent.run` 真实路径 | ✅ 实测：注释 `judge_agent.py:173 + :1483` → `TestJudgePerFileFanOut + TestJudgeChunkRunnerFanOut` 双双 `FAILED (spy.call_count == 0)`；注释 `conflict_analyst_agent.py:109` → `TestConflictAnalystMultiFileFanOut::test_clean_keys_pass` FAILED (spy.call_count 0) + `test_duplicate_key_raises` FAILED (`Failed: DID NOT RAISE FileShardOverlap`)。3 个接入点全部 regression-protected。 |
| **P2-2 顺手建议**：thresholds 对照断言 | P2（不阻塞） | `tests/unit/test_state_thresholds.py:178-263` 新增 `test_run_reads_state_thresholds_not_config_thresholds`：`state.thresholds=0.91` + `state.config.thresholds=0.5`（model_copy update 互不影响）→ `analyze_file` 收到 0.91，证明 `view.thresholds` 路径非 `view.config.thresholds` | ✅ Phase 2 残留 P2-2 弥补，正面证伪两路径同源 |
| **P2-1**：base_agent.py 830 行越 800 软约束 | P2（不阻塞，建议 Phase 5/6） | 保留延后 | ✅ 一致 |

## 测试结果

- `pytest tests/unit/ --cov=src --cov-report=term -q`：**2355 passed / 1 skipped / coverage 83.80%**
  - v1 出口：2354 / 83.59% → 净 +1 测试 / +0.21pp
  - Phase 2 出口基线：2345 / 83.60% → 净 +10 测试 / +0.20pp
- `mypy src`：**Success: no issues found in 160 source files**
- `ruff check src/ tests/unit/test_disjoint_assert.py tests/unit/test_state_thresholds.py`：**All checks passed!**

## 二审独立 acceptance 验证

按 review-v1 "v2 acceptance" 要求 — gatekeeper 端独立跑回归 net：

```
Stage 1: pass # 替换 judge_agent.py:173 + :1483 两处 assert
  → pytest TestJudgePerFileFanOut: AssertionError: assert 0 >= 1
  → pytest TestJudgeChunkRunnerFanOut: AssertionError: assert 0 >= 1
  → 双双 FAILED ✅

Stage 2: pass # 替换 conflict_analyst_agent.py:109 assert
  → pytest TestConflictAnalystMultiFileFanOut::test_clean_keys_pass: spy.call_count == 0 FAILED
  → pytest TestConflictAnalystMultiFileFanOut::test_duplicate_key_raises: DID NOT RAISE FAILED
  → 双双 FAILED ✅

Restore → 2355 passed / 1 skipped
```

3 处 src/ 接入点全部 regression-protected by 真实 agent path 测试。test FINAL §11 line 558 "每个接入点测试必须独立验证 assert call 发生在对应 file:line 附近"约束**完全满足**。

## 副作用检查（git diff `8f81798..5d659a2`）

```
 tests/unit/test_disjoint_assert.py  | 284 +++++++++++++++++++++++++++++++-----
 tests/unit/test_state_thresholds.py |  86 +++++++++++
 2 files changed, 330 insertions(+), 40 deletions(-)
```

**0 src/ 改动**；v1 锁定的 6 处接入点 / helper 实现 / Phase 0-2 锁定路径全部未触动。

## 已通过事实（详见 locks/approved-facts.md；本轮新增 3 条 + 1 条 Phase 2 残留闭合，已存档 38 条不重列）

本轮新增（待 SendMessage 通过后追加）：
- **[code-phase-3 #39]** `FileShardOverlap(ValueError)` + `assert_disjoint_file_shards(shards: list[list[str]]) -> None` 落 `src/core/parallel_file_runner.py:19-41`；纯函数（不副作用排序），用 `Counter` 统计 + `sorted(duplicates)` 入 exc 字符串；`str(exc)` 模板 `f"file shards overlap on: {duplicates}"`。`issubclass(FileShardOverlap, ValueError) is True / SystemExit is False`（U-P3.2 守护）。
- **[code-phase-3 #40]** lock #5 列出的 6 处具名接入点全部接入 + 真实 agent path regression-protected：
  1. `src/agents/conflict_analyst_agent.py:109` multi-file fan-out — `assert_disjoint_file_shards([[fp] for fp in high_risk_files])`（U-P3.8 守护，真实 `run()` 路径）
  2. `src/agents/conflict_analyst_agent.py:294-296` chunked path runner — shard 形态 `"<file>#<idx>"`（U-P3.7 守护，真实 `analyze_file` chunked 路径；lock #31 接入参数形态自纠落地）
  3. `src/agents/executor_agent.py:835-837` rebuttal chunk runner — `[[issue.file_path for issue in chunk] for chunk in chunks]`（U-P3.3 守护，真实 `build_rebuttal` 路径）
  4. `src/agents/planner_agent.py:651-653` `_classify_batch` sub-chunks — `[[fd.file_path for fd in chunk] for chunk in chunks]`（U-P3.4 守护，真实 `_classify_batch` 路径，触发阈值 `_CLASSIFY_FILE_CHUNK_SIZE=100`）
  5. `src/agents/judge_agent.py:173` per-file fan-out — `[[fp] for fp in high_risk_records.keys()]`（U-P3.5 守护，真实 `run()` 路径）
  6. `src/agents/judge_agent.py:1483` chunk runner — `[[entry[0] for entry in chunk] for chunk in chunks]`（U-P3.6 守护，真实 `review_batch` 路径，触发阈值 `_BATCH_SIZE=8`）
- **[code-phase-3 #41]** U-P3.3 ~ U-P3.8 真实 agent path regression net acceptance 锁定：注释 `src/` 中任意 1 处 6 接入点 assert 行 → 对应 1 个 test 必须 FAILED（要么 `spy.call_count == 0`，要么 `DID NOT RAISE FileShardOverlap`）。gatekeeper-code 二审端独立实测 Stage 1 + Stage 2 通过。后续 Phase 不得 regression：删除 assert 行而测试仍绿 = test 守护失效，立刻 NO-GO。
- **[code-phase-3 #42]**（**Phase 2 P2-2 闭合**）`view.thresholds` 路径与 `view.config.thresholds` 路径非同源 — `test_run_reads_state_thresholds_not_config_thresholds`（`tests/unit/test_state_thresholds.py:178-263`）通过 `state.config.thresholds.chunked_aggregation_min_confidence = 0.5` 修改后 `state.thresholds = 0.91` 的对照断言锁定 `ConflictAnalystAgent.run()` 读 state 快照而非 config 现态。Phase 2 review-v1 P2-2 残留闭合。

> 验证基线刷新（Phase 3 出口）：commit `5d659a2` 后 `pytest tests/unit/` = **2355 passed, 1 skipped**（Phase 2 +10）；`mypy src` = 0 error；`ruff check src/` = 0 error；coverage TOTAL = **83.80%**（+0.20pp）。后续 Phase 不得 regression 此基线。

> 残留 P2（不阻塞 Phase 3 GO；Phase 5/6 处理）：(a) `src/agents/base_agent.py` 830 行越 CLAUDE.md "<800" 软约束（Phase 2 滚来），建议 Phase 5 cache `_cached_call` 接入时一并抽 helper。

## P0 / P1 / P2 分级问题（本轮）

无 P0 / P1。

### P2 残留（继承）

- **P2-1**（Phase 2 滚来）：`src/agents/base_agent.py` 830 行越 800 软约束。Phase 5/6 处理。

## Step 3 / 4 — 代码质量 / 安全（增量）

- 两个新 helper `_build_judge_state` / `_build_conflict_state` 命名 + 结构与 `test_state_thresholds.py` 既有 fixture 模式一致；不引入新依赖。
- `_build_judge_state` 显式 `judge_skip_high_confidence=False / judge_skip_take_decisions=False` 覆盖 O-J1/O-J3 short-circuit，确保 fan-out 分支必走 — 是必要的精确控制，符合 plan §2 P3 "fan-out 接入点必走"。
- `TestJudgeChunkRunnerFanOut` for-else 模式（找首个匹配 shape 的 spy call）容错合理（review_batch 有多个 assert 调用点），逻辑明确。
- `test_duplicate_key_raises` 额外加 `analyze_mock.assert_not_called()` 证明 assert 在 LLM 调用之前 raise — 验证 shard 检查在 fan-out 前置位置，是隐含的"early-fail"契约守护。
- 安全：测试新增 fixture 不引入 user-controlled 流；MergeConfig 用 tmp_path + git.Repo.init 走标准路径；无新 TOCTOU / 资源泄漏表面。

## 修订建议（无）

可进入 Phase 4。
