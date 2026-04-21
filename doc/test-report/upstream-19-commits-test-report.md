# CodeMergeSystem 集成测试报告

**测试日期**: 2026-04-21  
**Run ID**: `dcdf1729-75f9-4fe4-9aa6-84f79349e9f5`  
**合并目标**: `dify-official-plugins` — `test/upstream-19-commits` → `feat_merge`  
**测试人**: Angel (同时作为用户进行人工决策)

---

## 执行摘要

| 项目 | 值 |
|------|-----|
| 状态 | ✅ 全流程跑通（COMPLETED） |
| 上游提交数 | 19 commits |
| 计划文件总数 | 32 |
| 自动合并文件 | 49 (take_target) + 13 (human take_target/take_current) |
| 语义合并文件 | 16 (semantic_merge) |
| 升级人工文件 | 14 (escalate_human — 超大文件/SEMANTIC_MERGE不支持) |
| Judge 最终裁定 | FAIL (75 issues: 52 critical, 3 high, 4 medium, 1 low, 15 info) |
| 人工干预次数 | 2次（Plan 审批 + 13个文件决策） |
| 运行总时长 | ~3.5 小时（包含多次 debug/重试） |

> **说明**: Judge FAIL 的主要原因为 48 个 D-missing 文件未被 auto_merge 处理（layer 依赖阻塞），以及 escalate_human 文件（超大 cvte 文件）无法自动合并。这是已知系统局限，不影响流程验证。

---

## Phase 断言记录

### Phase 1: ANALYSIS（初始化）

**状态**: ✅ COMPLETED  
**时长**: ~0.2s  
**关键输出**:
- 分类文件: 7456
- 可操作文件: 124
- 上游提交: 19 commits (其中 6 commit 无法 cherry-pick，fallback to apply)

**断言**:
- [x] 成功识别 `feat_merge` 为 fork，`test/upstream-19-commits` 为上游
- [x] 正确计算 merge base commit
- [x] 所有 19 upstream commits 被枚举
- [x] 6 个无法 cherry-pick 的 commit 被标记为 fallback

---

### Phase 2: PLAN_REVIEW（Planner + PlannerJudge 协商）

**状态**: ✅ COMPLETED（stalled at round 1，人工审批通过）  
**时长**: 05:19:43 → 05:21:07（约 84s）

**协商轮次**:

| Round | PlannerJudge 裁定 | Planner 响应 | 说明 |
|-------|-------------------|-------------|------|
| 0 | revision_needed（1 issue） | 1 accept | auth.py 从 auto_safe 升级为 auto_risky ✅ 正确 |
| 1 | revision_needed（3 issues） | 3 reject | 幻觉：引用不在计划中的 3 个文件 ✅ Planner 正确拒绝 |

**最终计划**:
- Total: 32 files / Auto-safe: 8 / Auto-risky: 24 / Human-required: 0
- Auto-merge rate: 25.0%

**人工审批决策**（用户身份）:
- 决定: **APPROVE**
- 原因: Round 0 的升级正确；Round 1 的问题是 PlannerJudge 幻觉，Planner 已正确拒绝，计划分布合理

**断言**:
- [x] PlannerJudge 正确识别安全敏感文件 `tools/jira/tools/auth.py` 被低估
- [x] Planner 正确接受安全升级建议
- [x] Planner 正确拒绝 PlannerJudge 幻觉（引用不存在的文件路径）
- [x] 计划中 cvte 插件文件正确列入 auto_risky 批次
- [x] 人工审批可通过 `--decisions` 文件提供

---

### Phase 3: AUTO_MERGE（自动执行）

**状态**: ✅ COMPLETED  
**时长**: 06:44:29 → 06:44:30（约 1s，因文件已有决策，大多跳过）

**执行结果**:
- take_target: 17 files（requirements.txt、小 Python/YAML 文件）
- escalate_human: 23 files（SEMANTIC_MERGE 策略不支持，超大 cvte 文件无法修复）
- semantic_merge: 6 files（manifest 文件版本协商）

**Batch Judge sub-review**:
- 首次运行: no consensus（PlannerJudge validation 错误，行号字段接受了字符串）
- **已修复**: `JudgeIssue.affected_lines` 添加 `_LineList` BeforeValidator，静默丢弃非整数行号

**断言**:
- [x] auto_safe 批次无需 judge 审查，直接执行
- [x] auto_risky 批次触发 batch judge sub-review
- [x] escalate_human 决策被正确记录（不阻塞其他文件）
- [x] 已有决策的文件不被重复处理（`_execute_batch` 跳过逻辑）

---

### Phase 3b: CONFLICT_ANALYSIS（冲突分析）

**状态**: ✅ COMPLETED  
**时长**: 06:44:30 → 06:48:26（约 4 分钟）

**分析结果**:
- conflict_analyses: 482 files
- 需要人工决策: 13 files

**13 个人工决策文件**（用户身份）:

| 文件 | 判断依据 | 决定 |
|------|----------|------|
| `models/azure_openai/provider/azure_openai.yaml` | cvte 插件，但 upstream 新增 Entra ID auth 有价值 | take_target |
| `models/tongyi/manifest.yaml` | cvte 插件，upstream 要改 author 回 langgenius | take_current（保留 cvte） |
| `agent-strategies/cot_agent/strategies/ReAct.py` | langgenius 插件 | take_target |
| `agent-strategies/cot_agent/strategies/function_calling.py` | langgenius 插件 | take_target |
| `models/bedrock/manifest.yaml` | langgenius，版本升级 0.0.54→0.0.58 | take_target |
| `models/bedrock/utils/inference_profile.py` | langgenius | take_target |
| `models/ollama/manifest.yaml` | langgenius | take_target |
| `tools/jira/provider/jira.yaml` | langgenius | take_target |
| `tools/paddleocr/manifest.yaml` | langgenius，版本升级 0.1.3→0.1.4 | take_target |
| `tools/paddleocr/tools/document_parsing.py` | langgenius | take_target |
| `tools/paddleocr/tools/document_parsing_vl.py` | langgenius | take_target |
| `tools/paddleocr/tools/text_recognition.py` | langgenius | take_target |
| `tools/paddleocr/tools/utils.py` | langgenius | take_target |

**断言**:
- [x] conflict_analysis 正确识别需要语义合并的文件
- [x] cvte 插件与 langgenius 插件的决策路径被正确区分
- [x] `--decisions` 文件格式（decisions: list）被正确解析
- [x] 13 个文件决策全部被系统加载（"Loaded 13 decisions"）

---

### Phase 4: JUDGE_REVIEW（Judge 最终审查）

**状态**: ✅ COMPLETED（3次 resume，最终接受裁定）  
**时长**: ~26 分钟（Judge 逐文件 LLM 审查）

**Judge 裁定摘要**:

| 轮次 | 时间 | 裁定 | Issues |
|------|------|------|--------|
| Round 0（第1次 resume）| 07:01 | FAIL | 85 issues |
| Round 0（第2次 resume）| 07:53 | FAIL | 74 issues |
| Round 0（第3次 resume）| 08:36 | FAIL | 75 issues |

**最终 Judge Verdict**:
```
verdict: FAIL
passed_files: 35
failed_files: 52
critical: 52 | high: 3 | medium: 4 | low: 1 | info: 15
veto_triggered: false
```

**问题分类**:
- **D-missing 文件未处理**: 44 issues（占 52 critical 中的绝大多数）
  - 模块: `triggers/twilio_trigger/`, `tools/comfyui/tools/json/`, `tools/jira/utils/`
  - 原因: layer 依赖未满足导致 auto_merge 跳过
- **B-class 文件与 upstream 不一致**: 5 issues
  - `tools/aihubmix_image/`, `tools/email/`
- **escalate_human 文件质量问题**: 8 issues
  - `azure_openai/models/constants.py`: 截断字符串、垃圾文本（超大 cvte 文件）
  - `azure_openai/models/llm/llm.py`: gpt-5 支持不完整
  - `cot_agent/strategies/ReAct.py`: 空类 stub

**人工裁定接受决策**（用户身份）:
- 决定: **ACCEPT** judge_verdict
- 原因: 已知局限，不影响流程验证；D-missing 问题属系统设计边界，escalate_human 文件需人工手动合并

**断言**:
- [x] Judge 成功运行 LLM 逐文件审查（71 files × ~8-15s/file）
- [x] Judge 正确识别 D-missing 文件为 critical issue
- [x] Judge 正确识别 B-class 文件差异
- [x] judge_resolution: accept 通过 checkpoint 修改被正确处理
- [x] 流程从 judge_review → generating_report → completed

---

### Phase 5: GENERATING_REPORT

**状态**: ✅ COMPLETED  
**时长**: 08:37:32（约 0.01s）

**输出文件**:
- `outputs/merge_report_dcdf1729.md` — 详细合并报告
- `outputs/merge_report_dcdf1729.json` — JSON 格式报告
- `outputs/plan_review_dcdf1729.md` — Plan 审查报告
- `MERGE_RECORD/MERGE_PLAN_test_upstream-19-commits_dcdf1729.md` — 合并计划

---

## Bug 修复记录（本次测试发现并修复）

| # | Bug | 修复位置 | 描述 |
|---|-----|----------|------|
| 1 | `OPENAI_BASE_URL` 配置错误 | `.env` | URL 末尾含 `/v1/chat/completions`，client.py 追加 `/v1` 导致 404 |
| 2 | `MemoryExtractorAgent.can_handle` 未实现 | `src/agents/memory_extractor_agent.py` | 添加 `return False` |
| 3 | `JudgeIssue.affected_lines` Pydantic 验证 | `src/models/judge.py` | LLM 输出 `"last visible line"` 等字符串导致解析失败，改用 `_LineList` BeforeValidator |
| 4 | Auto_merge 无限循环 | `src/core/phases/auto_merge.py` | Batch judge sub-review 失败后缺少 `paused=True`，导致 human_review → AUTO_MERGING 死循环 |
| 5 | 已决策文件被重复处理 | `src/core/phases/auto_merge.py` | `_execute_batch` 未跳过已有 file_decision_records 的文件 |
| 6 | Judge repair 无限超时 | `src/core/phases/judge_review.py` | 最后一轮仍调用 `executor.build_rebuttal()`，大文件超时 182s × 3次，添加 `if round_num >= max_rounds - 1: continue` |

---

## 优化建议清单

### P0（阻断性）

1. **D-missing 文件 layer 依赖阻塞**  
   48 个 D-missing 文件被跳过是本次 Judge FAIL 的主因。auto_merge 的 `verify_layer_deps()` 阻止了这些文件的处理，但缺少降级策略（直接 copy from upstream）。应为 D-missing 文件添加 fallback：即使 layer 依赖未满足，也通过 `_copy_from_upstream()` 处理。

2. **SEMANTIC_MERGE 策略在 Executor 中未实现**  
   14 个文件因 `Unsupported auto-merge strategy: MergeDecision.SEMANTIC_MERGE` 被 escalate_human。需要在 Executor 的 `execute_auto_merge()` 中实现 semantic merge 逻辑，或在 conflict_analysis 阶段将超大文件直接路由为 human_required。

### P1（影响自动化率）

3. **超大文件（>30K chars）无法 LLM 处理**  
   `azure_openai/models/constants.py`（139K chars）、`llm.py`（69K chars）超出 LLM 修复上限（30K），被反复 escalate。建议：
   - 为超大 cvte 文件生成结构化 diff patch，分段提交
   - 或在初始化时将 `>30K` 文件自动分类为 `human_required`，避免无效的 LLM 尝试

4. **Judge repair 超时严重（~9 min/大文件）**  
   182s 请求超时 × 3次重试 = 每个大文件耗时 9 分钟。建议降低 `request_timeout_seconds` 到 60s，减少单次等待时间。或在 `executor.repair()` 中预先检查文件大小，跳过无法处理的大文件。

5. **PlannerJudge 幻觉（引用计划外文件）**  
   Round 1 PlannerJudge 输出了 3 个不在计划中的文件路径。Planner 虽然正确拒绝，但浪费了一轮协商和额外 LLM 调用。建议 PlannerJudge 的输出模式明确要求文件路径必须来自输入的 plan batches。

### P2（体验改进）

6. **Checkpoint 未在 awaiting_human+judge_verdict 时保存**  
   多次 resume 后 checkpoint 仍显示 `judge_reviewing` 而非 `awaiting_human`，需手动修改 checkpoint。建议在 judge_review phase 完成（PASS 或 FAIL 裁定后）添加一次强制 checkpoint save。

7. **resume 不支持从 `judge_reviewing` 直接注入 `judge_resolution`**  
   目前 resume.py 仅在 `state.status == AWAITING_HUMAN` 时处理 `judge_resolution`。应扩展以支持从 `judge_reviewing` 状态注入，避免需要手动修改 checkpoint。

8. **B-class 文件判断过于严格**  
   `tools/email/` 文件被判定为 "B-class differs from upstream" 但实际上是有意的 CVTE 定制。Judge 的 B-class 检测应参考 `manifest.yaml` 中的 `author` 字段，对 `author: cvte` 的插件给予豁免。

9. **YAML 决策文件中含冒号的注释字段报错**  
   `reviewer_notes: Non-cvte plugin (author: langgenius)` 中的冒号导致 YAML 解析失败。建议 collect_decisions_file 使用更宽松的解析，或在文档中提示注释字段需加引号。

---

## 运行时间线

| 时间 | 事件 |
|------|------|
| 05:14 | 发现 Bug#1（OPENAI_BASE_URL 404），修复并重启 |
| 05:19 | Run dcdf1729 启动 |
| 05:21 | Plan review 完成（2轮 negotiation），进入 AWAITING_HUMAN |
| 05:22 | 人工审批计划（APPROVE） |
| 05:33 | Resume 启动，进入 AUTO_MERGING |
| 05:55 | Bug#4（无限循环）发现，修复 paused=True |
| 06:06 | Bug#5（重复处理）修复，跳过已有决策文件 |
| 06:33 | Auto_merge 完成（696.5s），进入 conflict_analysis |
| 06:48 | Conflict analysis 完成，13 文件待人工决策 |
| 06:51 | 13 个文件决策提供，Executor 开始处理 |
| 07:01 | 第一次 Judge run FAIL（85 issues） |
| 07:19 | Bug#3（JudgeIssue 行号 Pydantic 错误）修复 |
| 07:38 | Bug#6（judge repair 超时）修复，max_dispute_rounds=1 |
| 08:19 | Judge phase 完成（1579.7s），verdict: FAIL |
| 08:37 | Checkpoint 手动更新为 awaiting_human，接受 judge 裁定 |
| 08:37 | 报告生成，状态: COMPLETED |

---

## 参考文件

- Plan Review: `outputs/plan_review_dcdf1729-75f9-4fe4-9aa6-84f79349e9f5.md`
- Merge Report: `outputs/merge_report_dcdf1729-75f9-4fe4-9aa6-84f79349e9f5.md`
- Debug Log: `outputs/debug/run_dcdf1729-75f9-4fe4-9aa6-84f79349e9f5.log`
- LLM Traces: `outputs/debug/llm_traces_dcdf1729-75f9-4fe4-9aa6-84f79349e9f5.jsonl`
- Checkpoint: `outputs/debug/checkpoints/checkpoint.json`
