# Phase 3 实施报告 v2（修订）

## 修订基线

- 上一版：v1（commit `8f81798`，gatekeeper-code `review-v1.md`）
- 反馈：P1-1（U-P3.5 / U-P3.6 / U-P3.8 module-level call helper，无法 regression-protect plan #5 第 1/4/5 接入点）+ P2-2 顺手建议（thresholds 对照断言）

## 修订动作

| 反馈项 | 严重度 | 处理 | 锚点 |
|---|---|---|---|
| P1-1 | P1（阻塞） | U-P3.5 / U-P3.6 / U-P3.8 改造为真实 agent fan-out 路径 — 真实 instantiate JudgeAgent / ConflictAnalystAgent + 完整 MergeState fixture，patch 下游 LLM AsyncMock，让 wrapped spy 捕获 production 函数体内 assert 调用 | `tests/unit/test_disjoint_assert.py:166-310`（v2 新写） |
| P2-2 | P2（建议） | 新增 `test_run_reads_state_thresholds_not_config_thresholds`，验证 state.thresholds 是源 of truth — 修改 state.config.thresholds 不影响 analyze_file 入参（值同源对照） | `tests/unit/test_state_thresholds.py:178-265` |

## Acceptance 验证（按 review-v1 修复方案 A）

按 review-v1.md "v2 acceptance" 要求：注释 src/ 中 3 处 assert 行任一处 → 对应测试必须失败。实测：

| 接入点 src 行 | 注释后测试结果 | 行为 |
|---|---|---|
| `src/agents/judge_agent.py` per-file fan-out (`assert_disjoint_file_shards([[fp] for fp in high_risk_records.keys()])`) | `TestJudgePerFileFanOut` **FAILED** (`assert spy.call_count >= 1 → 0`) | ✅ |
| `src/agents/judge_agent.py` chunk runner (`assert_disjoint_file_shards([[entry[0] for entry in chunk] for chunk in chunks])`) | `TestJudgeChunkRunnerFanOut` **FAILED** (`assert spy.call_count >= 1 → 0`) | ✅ |
| `src/agents/conflict_analyst_agent.py` multi-file fan-out (`assert_disjoint_file_shards([[fp] for fp in high_risk_files])`) | `TestConflictAnalystMultiFileFanOut::test_clean_keys_pass` + `test_duplicate_key_raises` **FAILED**（duplicate 子断言：`Failed: DID NOT RAISE FileShardOverlap`） | ✅ |

恢复后跑全 disjoint suite：**9/9 通过**。

## 新增 commit

- `5d659a2` — test(disjoint): U-P3.5/3.6/3.8 改造为真实 agent fan-out 路径 + P2-2 thresholds 对照断言
- 不 amend 已审 `8f81798`（按 team-lead 协议"修订只能加 fixup commit 或后续 commit"）

## 修订文件清单

- `tests/unit/test_disjoint_assert.py`：+155 -40 lines
  - 新增 `_build_judge_state` helper（U-P3.5/3.6 共用，类似 test_state_thresholds.py `_build_conflict_state` 模式）
  - `TestJudgePerFileFanOut.test_judge_per_file_fan_out_passes_disjoint_assert`：真实 `JudgeAgent.run(state)` 路径；patch `_run_deterministic_pipeline` + `review_file`；shards 形态断言 `[[fp]]`
  - `TestJudgeChunkRunnerFanOut.test_judge_chunk_runner_passes_disjoint_assert`：真实 `JudgeAgent.review_batch` 路径；10 risky files 触发 `_BATCH_SIZE=8` 切 2 chunks；patch `_review_files_batch_llm`；shards 形态校验
  - `_build_conflict_state` helper（U-P3.8 重写）
  - `TestConflictAnalystMultiFileFanOut.test_clean_keys_pass`：真实 `ConflictAnalystAgent.run(state)` 路径 + 完整 MergeState + 3 file MergePlan；patch `analyze_file` AsyncMock
  - `TestConflictAnalystMultiFileFanOut.test_duplicate_key_raises`：duplicate plan phase file_paths 触发 assert raise；额外断言 `analyze_mock.assert_not_called()` 证明 assert 在 fan-out LLM 调用之前 raise
- `tests/unit/test_state_thresholds.py`：+87 lines
  - 新增 `test_run_reads_state_thresholds_not_config_thresholds`：修改 `state.config.thresholds.chunked_aggregation_min_confidence = 0.5` 后，`analyze_file` 仍接收 `state.thresholds = 0.91` 的快照值，证明 view.thresholds 链路非 view.config.thresholds

## 测试结果

- `pytest tests/unit/test_disjoint_assert.py -q`：**9/9 通过**
- `pytest tests/unit/test_state_thresholds.py -q`：**5/5 通过**（v1 4 + 新增 P2-2 对照断言 1）
- `pytest tests/unit/ -q`：**2355 passed / 1 skipped**（v1 出口 2354 + 1 新增）
- `pytest tests/unit/ --cov=src --cov-report=term -q`：coverage **83.80%**（v1 出口 83.59%，+0.21pp；Phase 2 出口 83.60%，+0.20pp）
- `mypy src`：**Success: no issues found in 160 source files**
- `ruff check src/ tests/unit/test_disjoint_assert.py tests/unit/test_state_thresholds.py`：**All checks passed!**

## src/ 改动

**0 行**。本次修订仅改 tests/。Phase 3 v1 的 6 处 src 接入点（已通过审查）+ helper 实现 + Phase 0/1/2 已锁定路径全部未触动。

## P1-1 修复模式总结（对比 v1 → v2）

| 用例 | v1 形态（错） | v2 形态（对） |
|---|---|---|
| U-P3.5 (judge per-file) | `judge_agent.assert_disjoint_file_shards([[fp]])` 直接 module 调用 | `JudgeAgent().run(state)` 真实路径；fixture `_build_judge_state` 准备 3 risky files；patch `_run_deterministic_pipeline` + `review_file` AsyncMock |
| U-P3.6 (judge chunk runner) | `judge_agent.assert_disjoint_file_shards(...)` 直接 module 调用 | `JudgeAgent().review_batch(layer_id, file_paths, state)` 真实路径；10 risky files 触发 2 chunks；patch `_review_files_batch_llm` AsyncMock |
| U-P3.8 (conflict_analyst multi-file) | `conflict_analyst_agent.assert_disjoint_file_shards(...)` 直接 module 调用 + `pytest.raises` 包裹直接调用 | `ConflictAnalystAgent().run(state)` 真实路径；fixture `_build_conflict_state` 准备完整 MergePlan + FileDiffs；duplicate 子断言验证 `pytest.raises` + `analyze_mock.assert_not_called()` 确认 assert 在 LLM 调用之前 raise |

## GO 条件二审核查表

按 review-v1.md "v2 acceptance" 项：

1. ✅ 注释 `src/agents/judge_agent.py` per-file fan-out assert → `TestJudgePerFileFanOut` 失败
2. ✅ 注释 `src/agents/judge_agent.py` chunk runner assert → `TestJudgeChunkRunnerFanOut` 失败
3. ✅ 注释 `src/agents/conflict_analyst_agent.py` multi-file fan-out assert → `TestConflictAnalystMultiFileFanOut::*` 失败
4. ✅ U-P3.1~U-P3.4 / U-P3.7 维持不变（v1 已通过审查）
5. ✅ tests/unit/test_state_thresholds.py P2-2 对照断言新增（+1 测试）
6. ✅ `pytest tests/unit/` 2355 passed / coverage 83.80%
7. ✅ `mypy src` 0 error / `ruff check` 0 error
8. ✅ src/ 0 改动

## 与 v1 报告的契约对齐与 GO 核查

v1.md 的「契约对齐」「lock #5 / lock #31 落地核查」「Test/FINAL.md U-P3.* 覆盖追踪」全部保留有效；本 v2 仅修订 P1-1 + 加 P2-2，未触动 v1 的 src/ 接入点。

## P2 残留

- **P2-1（Phase 2 滚来）**：`src/agents/base_agent.py` 830 行越 800 软约束。建议 Phase 5/6 拆 sections 时一并处理。**本 v2 不动**。

## Phase 4 续接锚点

- Phase 3 v2 出口 HEAD `5d659a2`，feat/web 分支未推送。
- `FileShardOverlap` / `assert_disjoint_file_shards` 落 `src/core/parallel_file_runner.py`；6 接入点全部 regression-protected by 真实 fan-out 路径测试。
- Phase 4 范围：`MergeConfig.enable_working_branch` default False → True；wizard 默认勾选；3+2 测试改动（lock #3 line 72-83 锚点区间不得删除）；U-P4.1/2/3/4/5 守护。
