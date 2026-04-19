# 数据模型（`src/models/`）

> **版本**：2026-04-17
> 所有模型均为 Pydantic v2。本文档按文件组织，只列出新人最常接触的字段。完整字段请查源码。

---

## 1. `config.py` — 配置模型

### `MergeConfig`（顶层）

必填：
- `upstream_ref: str` — 上游分支 ref（例：`upstream/main`）
- `fork_ref: str` — 下游分支 ref（例：`feature/my-fork`）

常调字段：
- `working_branch`（默认 `merge/auto-{timestamp}`）
- `repo_path`（默认 `.`）
- `project_context` — 注入到 Agent system prompt
- `max_files_per_run`（默认 500）
- `max_plan_revision_rounds`（默认 5，Planner↔Judge 协商上限）
- `max_judge_repair_rounds`（默认 3，Executor↔Judge 修复上限）

嵌套子模型（共 20+）：
- `agents: AgentsLLMConfig` — **权威**的每 Agent LLM 配置（见下）
- `llm: LLMConfig` — 旧版全局默认，仅向后兼容
- `thresholds: ThresholdConfig`
- `file_classifier: FileClassifierConfig`
- `output: OutputConfig`
- `gate: GateConfig`、`smoke_tests: SmokeTestConfig`
- `customizations: list[CustomizationEntry]`
- `shadow_rules_extra`、`cross_layer_assertions`、`reverse_impact`
- `sentinels_extra`、`config_retention`、`scar_learning`
- `migration: MigrationConfig`、`history: HistoryPreservationConfig`
- `github: GitHubConfig`、`layer_config: MergeLayerConfig`
- `syntax_check: SyntaxCheckConfig`、`llm_risk_scoring: LLMRiskScoringConfig`

### `AgentLLMConfig`（每个 Agent 独立一份）

```python
provider: Literal["anthropic", "openai"]
model: str
temperature: float
max_tokens: int
max_retries: int
api_key_env: str | list[str]       # 支持凭据池
api_base_url_env: str
cache_strategy: Literal["none", "system_only", "system_and_recent"]
cheap_model: str | None            # D1 smart routing
compression: CompressionConfig     # 每 Agent 可调的压缩参数
```

### `AgentsLLMConfig` 默认值

| Agent | provider | model | env |
|---|---|---|---|
| planner | anthropic | claude-opus-4-6 | ANTHROPIC_API_KEY |
| planner_judge | openai | gpt-4o | OPENAI_API_KEY |
| conflict_analyst | anthropic | claude-sonnet-4-6 | ANTHROPIC_API_KEY |
| executor | openai | gpt-4o | OPENAI_API_KEY |
| judge | anthropic | claude-opus-4-6 | ANTHROPIC_API_KEY |
| human_interface | anthropic | claude-haiku-4-5-20251001 | ANTHROPIC_API_KEY |

### `ThresholdConfig`
```python
auto_merge_confidence: 0.85   # ≥ 此值 → 自动合并
human_escalation: 0.60        # < 此值 → 升级人工
risk_score_low: 0.30          # < 此值 → AUTO_SAFE
risk_score_high: 0.60         # > 此值 → HUMAN_REQUIRED
```

### `CustomizationEntry` + `CustomizationVerification`

用于声明 fork 独有功能以及自动验证规则。`verification.type` 七选一：
`grep`, `grep_count_min`, `grep_count_baseline`, `file_exists`, `function_exists`, `line_retention`

`source="scar_learned"` 表示由 `ScarListBuilder` 自动从 git 历史学习得来（P2-1）。

### `GateCommandConfig`
```python
name, command, working_dir, timeout_seconds
pass_criteria: "exit_zero" | "not_worse_than_baseline" | "no_new_regression"
baseline_parser: ""   # 或 pytest_summary / mypy_json / ruff_json / eslint_json /
                     #     tsc_errors / go_test_json / cargo_test_json / junit_xml
```

---

## 2. `state.py` — 全局状态

### `SystemStatus`（13 个值）
见 `doc/flow.md` §1.1。

### `MergeState`（贯穿全流程的唯一状态对象）

分组字段（节选）：

**输入与元数据**
```python
run_id: str                         # UUID
config: MergeConfig
status: SystemStatus
current_phase: MergePhase
phase_results: dict[str, PhaseResult]
merge_base_commit: str
```

**分析产物**
```python
file_diffs: list[FileDiff]
file_classifications: dict[str, RiskLevel]
file_categories: dict[str, FileChangeCategory]
upstream_commits / replayable_commits / non_replayable_commits
```

**六大加固扫描**
```python
pollution_audit: PollutionAuditReport | None
migration_info: SyncPointResult | None
config_drifts: ConfigDriftReport | None
shadow_conflicts: list[ShadowConflict]      # P0-2
interface_changes: list[InterfaceChange]    # P1-1
reverse_impacts: dict[str, list[str]]        # P1-1 symbol → fork 文件
scar_list: list[Scar]                        # P2-1
sentinel_hits: dict[str, list[SentinelHit]]  # P2-2
```

**计划与审查**
```python
merge_plan: MergePlan | None
plan_revision_rounds: int
plan_judge_verdict: PlanJudgeVerdict | None
plan_review_log: list[PlanReviewRound]
plan_human_review: PlanHumanReview | None
review_conclusion: ReviewConclusion | None
pending_user_decisions: list[UserDecisionItem]
plan_disputes: list[PlanDisputeRequest]
```

**执行**
```python
file_decision_records: dict[str, FileDecisionRecord]
applied_patches: list[str]
conflict_analyses: dict[str, ConflictAnalysis]
```

**人工与仲裁**
```python
human_decision_requests: dict[str, HumanDecisionRequest]
human_decisions: dict[str, MergeDecision]
judge_verdict: JudgeVerdict | None
judge_repair_rounds: int
judge_verdicts_log: list[dict]
smoke_test_report: SmokeTestReport | None
consecutive_smoke_failures: int
```

**门禁**
```python
gate_baselines: dict[str, str]   # gate_name → stdout_tail
gate_history: list[dict]
consecutive_gate_failures: int
```

**记忆与依赖图**
```python
memory: MergeMemory
dependency_graph: FileDependencyGraph
```

**轨迹**
```python
errors: list[dict]
messages: list[dict]    # 含 state_transition 事件
created_at / updated_at
checkpoint_path: str | None
```

> **重要约束**：`MergeState` 序列化必须能 round-trip 过 `model_validate(model_dump(mode="json"))`——这是 Checkpoint 恢复的前提。新增字段时务必保证 JSON 可序列化。

---

## 3. `plan.py` — 合并计划

### `MergePhase` 枚举
`ANALYSIS` / `PLAN_REVIEW` / `PLAN_REVISING` / `AUTO_MERGE` / `CONFLICT_ANALYSIS` / `HUMAN_REVIEW` / `JUDGE_REVIEW` / `REPORT`

### `MergePlan`
```python
plan_id: UUID
created_at, upstream_ref, fork_ref, merge_base_commit
phases: list[PhaseFileBatch]       # 按执行次序排列
risk_summary: RiskSummary
category_summary: CategorySummary | None
layers: list[MergeLayer]           # 默认取 DEFAULT_LAYERS 9 层
project_context_summary: str
special_instructions: list[str]
version: "2.0"
```

### `MergePlanLive`（执行中扩展）
继承 MergePlan，额外记录：
```python
execution_records, judge_records, gate_records, open_issues
todo_merge_count / todo_merge_limit
config_drifts, pollution_summary
```

### `DEFAULT_LAYERS`（默认 9 层拓扑）

| # | 层名 | 典型路径 |
|---|------|---------|
| 0 | infrastructure | `docker/`, `.github/`, Makefile |
| 1 | dependencies | `pyproject.toml`, `package.json`, lock 文件 |
| 2 | types_configs | `types/`, `configs/`, `.d.ts` |
| 3 | models_extensions | `models/`, `migrations/` |
| 4 | core_engine | `core/` |
| 5 | services_controllers | `services/`, `tasks/`, `controllers/` |
| 6 | frontend | `web/`, `app/`, `components/` |
| 7 | i18n | `i18n/`, `locales/` |
| 8 | tests | `tests/`, `*.test.*`, `*.spec.*` |
| 9 | sdk_plugins | `sdks/`, `plugins/` |

Planner 生成的 Phase 会自动做拓扑排序（`topological_sort_layers`），环检测失败 raise `LayerCycleError`。

---

## 4. `diff.py` — 文件差异

```python
class FileChangeCategory(Enum):
    A = "unchanged"
    B = "upstream_only"
    C = "both_changed"
    D_MISSING = "upstream_new"     # upstream 有，fork 无
    D_EXTRA = "current_only"        # fork 有，upstream 无
    E = "current_only_change"       # fork 独有的改动

class RiskLevel(Enum):
    AUTO_SAFE | AUTO_RISKY | HUMAN_REQUIRED | DELETED_ONLY | BINARY | EXCLUDED

class FileDiff(BaseModel):
    file_path, file_status, risk_level, risk_score: float
    risk_factors: list[str]
    lines_added / lines_deleted / lines_changed / conflict_count
    hunks: list[DiffHunk]
    change_category: FileChangeCategory | None
    is_security_sensitive: bool
    language: str | None
    raw_diff: str | None
```

`DiffHunk` 保留行号、内容及冲突标记行。

---

## 5. `decision.py` — 合并决策

```python
class MergeDecision(Enum):
    TAKE_CURRENT | TAKE_TARGET | SEMANTIC_MERGE |
    MANUAL_PATCH | ESCALATE_HUMAN | SKIP

class DecisionSource(Enum):
    AUTO_PLANNER | AUTO_EXECUTOR | HUMAN | BATCH_HUMAN
    # 注意：故意没有 TIMEOUT_DEFAULT（原则 P6）

class FileDecisionRecord(BaseModel):
    record_id, file_path, file_status, decision, decision_source
    confidence: float | None
    rationale: str                     # 必填：原则 P3
    applied_patch / original_snapshot / merged_content_preview
    discarded_content / discard_reason  # 原则 P1：不丢失
    conflict_points_resolved: list[str]
    human_notes
    phase / agent / timestamp
    is_rolled_back: bool
    rollback_reason: str | None
```

每次写入都会生成一条 `FileDecisionRecord`，失败自动填 `is_rolled_back=True` 并记录回滚原因。

---

## 6. `judge.py`、`plan_judge.py`、`plan_review.py`

- `JudgeVerdict` — Judge 审查结论（APPROVED / NEEDS_REPAIR / ESCALATE）
- `PlanJudgeVerdict` — PlannerJudge 审查结论（APPROVED / REVISE / REJECT）
- `PlanReviewRound` — 单轮 Planner↔PlannerJudge 交互完整记录
- `PlanHumanReview` — 人工对最终计划的评论
- `UserDecisionItem` — 待用户决策的单条目
- `ReviewConclusion` — `CONVERGED / MAX_ROUNDS / STALLED / LLM_FAILURE`

---

## 7. `conflict.py`、`dispute.py`、`human.py`、`dependency.py`

- `ConflictAnalysis` — ConflictAnalyst 输出，包含语义诊断与建议策略
- `PlanDisputeRequest` — Executor 发起的计划质疑
- `HumanDecisionRequest` — 呈现给人工的决策项
- `FileDependencyGraph` — 从 import/include 关系构建的文件依赖图

---

## 8. `smoke.py`、`message.py`

- `SmokeTestReport` — 冒烟测试结果（P1-3）
- `AgentMessage` / `AgentType` — Agent 间通信的轻量消息协议（当前主要用于日志）

---

## 9. `memory/models.py`

不在 `models/` 目录下但同为数据模型：

```python
class MemoryEntryType(Enum):
    PATTERN | DECISION | RELATIONSHIP | PHASE_SUMMARY | CODEBASE_INSIGHT

class MemoryEntry(BaseModel, frozen=True):
    entry_id, entry_type, phase, content
    file_paths, tags
    confidence: float (0..1)
    confidence_level: Literal["extracted", "inferred", "heuristic"]
    content_hash: str   # 自动填，用于去重
    created_at

class PhaseSummary(BaseModel, frozen=True):
    phase, files_processed, key_decisions, patterns_discovered
    error_summary, statistics

class MergeMemory(BaseModel):
    entries: list[MemoryEntry]
    phase_summaries: dict[str, PhaseSummary]
    codebase_profile: dict[str, str]
```

详见 [`memory.md`](memory.md)。
