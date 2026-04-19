# Tools（`src/tools/`）

> **版本**：2026-04-17
> Tools 是系统的**确定性脊柱**。LLM 负责"理解"，Tools 负责"证伪"。任何由 LLM 产出的判断，最终必须过得了 Tools 的复核。

---

## 1. 模块一览

按职责分为四组。行数仅作规模参考。

### 1.1 基础工具（Git / Diff / 写入 / 报告）

| 文件 | 行数 | 作用 |
|---|---|---|
| `git_tool.py` | 265 | GitPython 封装；`get_file_diff`, `show(ref:path)`, `merge_base`, `apply_patch` 等 |
| `diff_parser.py` | 202 | unified diff 文本 → `FileDiff[]`（含 hunks、冲突标记） |
| `file_classifier.py` | 246 | ABCDE 分类 + 风险打分（size/conflict_density/change_ratio/file_type/security） |
| `patch_applier.py` | 84 | **原则 P7 的唯一写入通道**：`apply_with_snapshot()` |
| `report_writer.py` | 748 | Markdown + JSON 报告输出（含 Plan Review、Judge、Smoke 等所有章节） |
| `merge_plan_report.py` | 380 | MERGE_PLAN_*.md 报告生成 |
| `decision_template.py` | 73 | 人工决策 YAML 模板 |
| `ci_reporter.py` | 64 | CI 模式 JSON 摘要 |

### 1.2 门禁与测试（Gate / Smoke）

| 文件 | 行数 | 作用 |
|---|---|---|
| `gate_runner.py` | 295 | 执行 `GateCommandConfig` 命令，捕获 stdout/stderr/exit_code，支持 baseline 对比 |
| `syntax_checker.py` | 98 | python/json/yaml 快速语法检查 |
| `smoke_runner.py` | 179 | 执行 `SmokeTestSuite`（shell/http/playwright 三类） |
| `conflict_grouper.py` | 65 | 把同类冲突合并呈现，减轻人工决策负担 |

#### `baseline_parsers/`（可插拔）

| 文件 | 针对的工具 |
|---|---|
| `pytest_summary.py` | `pytest -q` 尾部摘要 |
| `mypy_json.py` | `mypy --json-report` |
| `ruff_json.py` | `ruff check --output-format=json` |
| `eslint_json.py` | `eslint -f json` |
| `tsc_errors.py` | `tsc --noEmit` |
| `go_test_json.py` | `go test -json` |
| `cargo_test_json.py` | `cargo test --message-format=json` |
| `junit_xml.py` | 任何 JUnit XML |
| `basedpyright_json.py` | `basedpyright --outputjson` |

所有 parser 暴露统一的 `parse(stdout, stderr) -> {"passed": int, "failed": int, "failed_ids": list[str]}`。`GateCommandConfig.baseline_parser` 填文件名（不含 `.py`）即可接入。

### 1.3 六大丢失模式扫描器（详见 `multi-agent-optimization-from-merge-experience.md`）

| 模式 | 文件 | 行数 | 作用 |
|---|---|---|---|
| M1 | `scar_list_builder.py` | 212 | P2-1：从历史 restore/compat-fix/revert commit **自学习** scar 列表 |
| M2 | `shadow_conflict_detector.py` | 189 | P0-2：同名不同扩展 / module-vs-package 隐式冲突 |
| M3 | `interface_change_extractor.py` | 211 | P1-1：upstream 接口/签名/枚举变更抽取 |
| M3 | `reverse_impact_scanner.py` | 87 | P1-1：把 M3 抽到的符号反查 fork-only 文件的引用面 |
| M4 | `three_way_diff.py` | 193 | 三方 diff + 顶层调用/装饰器抽取 |
| M5 | `config_line_retention_checker.py` | 99 | P2-3：CI/env/docker 配置文件的必需行保留率 |
| M5 | `config_drift_detector.py` | 156 | 配置漂移跨文件扫描 |
| M6 | `baseline_parsers/` | — | 门禁 baseline-diff（不看 exit 0，只看新引入的 failed_ids） |

### 1.4 结构与完整性

| 文件 | 行数 | 作用 |
|---|---|---|
| `pollution_auditor.py` | 202 | Phase 0：识别历史合并中被"污染"（合进 upstream 但行为已变）的文件 |
| `sync_point_detector.py` | 282 | bulk-copy 迁移检测：找已被迁移的 upstream commit，避免重复 |
| `cross_layer_checker.py` | 107 | P0-4：跨层键一致性断言（`CrossLayerAssertion`） |
| `sentinel_scanner.py` | 103 | P2-2：业务哨兵正则扫描（`DEFAULT_SENTINELS` + `sentinels_extra`） |
| `dependency_extractor.py` | 225 | 提取文件级依赖图（import/include 关系） |
| `commit_replayer.py` | 97 | 把 upstream commit 回放到 fork 上 |
| `git_committer.py` | 55 | Phase 结束时按 `HistoryPreservationConfig` 做 commit |
| `rule_resolver.py` | 245 | 合并策略规则解析：把 Plan 的指令翻译成具体 MergeDecision |

### 1.5 可观测性与后端抽象

| 文件 | 行数 | 作用 |
|---|---|---|
| `cost_tracker.py` | 204 | 每次 LLM 调用的 token/美金统计，run 结束输出 summary |
| `trace_logger.py` | 118 | prompt/response 完整记录，`get_utilization_summary()` 返回 Context 利用率 |
| `structured_logger.py` | 48 | JSONL 结构化日志 handler，`output.structured_logs=true` 时启用 |
| `backend.py` | 211 | 工具后端抽象层（为未来接入不同 git 实现 / 远程执行预留） |

---

## 2. `apply_with_snapshot`：唯一写入通道

```python
async def apply_with_snapshot(
    file_path, new_content, git_tool, state,
    phase="auto_merge", agent="executor",
    decision=SEMANTIC_MERGE, rationale="", confidence=None,
) -> FileDecisionRecord:
    # 1. 读原内容作 original snapshot
    # 2. 写新内容到磁盘
    # 3. 成功 → 返回 FileDecisionRecord，附 original_snapshot + merged_content_preview
    # 4. 失败 → 回写 original → 返回 is_rolled_back=True 的 escalate 记录
```

**任何不走这个函数的写入都是 bug。**  Executor 是唯一合法的调用方（但 Executor 内部某些 SEMANTIC_MERGE 路径会传入 LLM 生成的完整内容）。

---

## 3. GateRunner 与 baseline-diff

### 3.1 运行流程
1. 按 `GateCommandConfig` 执行 `command`（在 `working_dir` 里，有 `timeout_seconds` 限制）
2. 捕获 stdout/stderr/exit_code
3. 按 `pass_criteria` 判断：
   - `exit_zero` — 必须 exit 0
   - `not_worse_than_baseline` — failed_ids 集合不扩大
   - `no_new_regression` — failed_ids 无新增项
4. 如果 `baseline_parser` 非空，调对应 parser 产生 `structured_baseline`

### 3.2 baseline-diff 例子

```
baseline passed=120, failed=3, failed_ids=[test_a, test_b, test_c]
current  passed=118, failed=5, failed_ids=[test_a, test_b, test_d, test_e]
new failures = [test_d, test_e]   ← VETO
fixed       = [test_c]            ← 加分
```

这解决了"门禁只看 exit 0 导致 M6 契约回归漏报"的问题。

---

## 4. 扫描器执行时机

| Phase | 执行的扫描器 |
|---|---|
| Phase 0 Initialize | PollutionAuditor, SyncPointDetector, ShadowConflictDetector, InterfaceChangeExtractor+ReverseImpactScanner, ScarListBuilder, SentinelScanner, ConfigLineRetentionChecker, ConfigDriftDetector |
| Phase 3 Auto Merge 末尾 | GateRunner（本层的 gate_commands） |
| Phase 6 Judge Review（确定性流水线） | 上述全部复检 + baseline-diff + SmokeRunner |

---

## 5. 新增扫描器的清单

1. 在 `src/tools/your_scanner.py` 定义纯函数或无状态类
2. 输入 `MergeState` + `MergeConfig`，输出可 JSON 序列化的 Pydantic 模型
3. 在 `MergeState` 新增对应字段（别忘了 `_rebuild_state_model`）
4. 在 `src/core/phases/initialize.py` 按配置开关调用
5. 在 `src/agents/judge_agent.py` 确定性流水线中加复检分支
6. 单元测试：最好用 `tmp_path` 搭小仓库跑端到端
