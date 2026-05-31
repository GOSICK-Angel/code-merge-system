# Planner & PlannerJudge 优化分析报告

**日期**: 2026-05-19  
**测试仓库**: `/Users/angel/AI/merge-test/forgejo`  
**测试分支**: `test/upstream` → `test/fork`（共同祖先 `160377405c53`）  
**参考**: `doc/test-report/forgejo-c-class-test-branches-2026-05-18.md`  
**产物**: `.merge/plans/MERGE_PLAN_test_upstream_4d609544.md`

---

## 背景

使用含共同祖先的 C-class 冲突测试分支（3 个 auth 相关文件双边修改），验证 Planner 和 PlannerJudge 在以下场景的行为：

- C-class 文件是否正确触发 `HUMAN_REQUIRED`
- PlannerJudge 是否进行至少 1 轮审查（`plan_revising` 状态）
- `security_sensitive.patterns` 配置是否生效

实际结果与预期存在显著偏差，暴露出 4 个核心问题和 3 个体验改进点。

---

## 核心问题

### P0 — `is_security_sensitive` 未正确传递，HUMAN_REQUIRED = 0

**现象**

MERGE_PLAN 汇总：

| Metric | Value |
|--------|-------|
| Human required | **0** |
| Auto-risky | 8 |

高风险文件表中，Security 列对所有 auth 文件为空：

```
| models/auth/auth_token.go   | auto_risky | auto_risky | 0.52 |   | both_changed |
| routers/web/auth/oauth.go   | auto_risky | auto_risky | 0.53 |   | both_changed |
```

config.yaml 已配置：

```yaml
security_sensitive:
  patterns:
    - "models/auth/**"
    - "routers/web/auth/**"
    - "services/auth/**"
```

**根因链**

```
config.yaml（顶层 security_sensitive）
    ↓ _hoist_top_level_security_sensitive（model_validator mode="before"）
    ↓ fc.setdefault("security_sensitive", sec)
    ↓ state.config.file_classifier.security_sensitive.patterns
    ↓ initialize.py:477 → is_security_sensitive(file_path, config.file_classifier)
    ↓ 返回 False（模式未命中或未加载）
    ↓ fd.is_security_sensitive = False
    ↓ compute_risk_score: security_score=0.0，security floor（max(score,0.8)）不触发
    ↓ risk_score=0.52（低于 high 阈值 0.6）→ risk_level=AUTO_RISKY
    ↓ _split_by_risk_level 将文件放入 risky 桶（非 human 桶）
    ↓ HUMAN_REQUIRED = 0
```

实际 `risk_score=0.52`（期望 ≥0.8），证明 security floor 完全未触发。

**影响**

- `pending_user_decisions` 为空 → 系统不进入 `AWAITING_HUMAN`
- PlannerJudge 因 `is_security_sensitive=False` + `conflict_count=0` → 跳过所有 auth 文件
- `_No review rounds recorded.`

**排查建议**

在 `initialize.py` plan 完成后增加诊断日志：

```python
self.logger.info(
    "Security patterns loaded: %s",
    state.config.file_classifier.security_sensitive.patterns,
)
sensitive_count = sum(1 for fd in file_diffs if fd.is_security_sensitive)
self.logger.info("Files with is_security_sensitive=True: %d", sensitive_count)
```

重点检查 `_hoist_top_level_security_sensitive` 在 Pydantic v2 `mode="before"` 下，`data.pop("security_sensitive")` 是否在所有解析路径（包括 `model_validate()`）中均能正确执行。

---

### P1 — Planner 分类提示词中 `human_required` 规则死锁

**现象**

`planner_prompts.py` 的 `build_classification_prompt` 存在三条相互矛盾的规则：

| 位置 | 规则 |
|------|------|
| `human_required` 第 3 条（L88–94） | "Path obviously implements auth/OAuth — **even when security_sensitive=false**, prefer human_required" |
| HARD RULE（L103） | "**NEVER use human_required** for conflicts=0 AND security_sensitive=false unless both_changed with large upstream delta" |
| `PLANNER_EVALUATION_SYSTEM`（L198–201） | "conflict_count=0 AND security_sensitive=false, **path-name alone is NOT a valid reason** to upgrade — REJECT suggestions" |

**实际效果**：Judge 基于第 3 条提建议 → Planner 依据 EVALUATION_SYSTEM 直接拒绝 → 协商循环僵死。这是一个 **规则死锁**：规则允许 Judge 提，但同时保证 Planner 永远拒。

注意：这个矛盾在 layered 路径下实际不触发（layered planner 不调用 LLM 分类），但会影响 legacy 路径和 plan revising 阶段的正确性。

**修复方向**

统一规则：删除 `human_required` 的第 3 条路径依赖 bullet，将路径检测逻辑完全委托给 `is_security_sensitive` 标志。即：
- LLM 分类只看 `conflict_count` 和 `is_security_sensitive`，不做路径启发
- 路径启发由 `SecuritySensitiveConfig.risk_hint_patterns`（弱信号）和 `patterns`（强信号）统一处理

---

### P1 — PlannerJudge 对 C-class + auth 路径无专项规则

**现象**

PlannerJudge 的 5 条审查触发规则（`build_plan_review_prompt:504–510`）：

```
1. is_security_sensitive=true 但分类低于 auto_risky → flag
2. conflict_count > 0 但未分 human_required → flag
3. 路径明显安全关键 且 有 [SEC] 或 conflicts>0 → flag
4. 危险批次顺序依赖 → flag
5. conflict_count=0 且 is_security_sensitive=false → 不 flag
```

对于本次测试的 3 个 C-class auth 文件，均满足第 5 条（`conflict_count=0` + `is_security_sensitive=False`），PlannerJudge 正确地遵守规则跳过了它们。但 `both_changed` 本身代表双边修改、语义冲突风险高，目前没有任何规则捕获 **"C-class 文件在批次中聚合但缺乏足够风险标注"** 的场景。

**修复方向**

在 `build_plan_review_prompt` 的 Review Tasks 中增加第 6 条：

```
6. C-class (both_changed) files classified auto_risky — verify the classification is
   intentional, not a fallback from a missing security_sensitive match. If the file
   is in an auth/crypto/permission directory, flag for confirmation.
```

同时在 `_build_file_manifest()` 中对 `change_category=both_changed` 的文件增加 `C` flag，使 Judge 能直接在 manifest 中识别它们。

---

### P2 — Report 中 Project Context 嵌入完整 README

**现象**

MERGE_PLAN 第 7–50 行：

```markdown
**Project Context**: <div align="center">
    <img src="./assets/logo.svg" alt="" width="192" align="center" />
    <h1 align="center">Welcome to Forgejo</h1>
    ...（300+ 行 README）
```

**根因**

`merge_plan_report.py:_header()` 的 fallback 逻辑：

```python
user_ctx = (state.user_project_context or "").strip()
if not user_ctx:
    user_ctx = state.config.project_context.strip()  # 可能是完整 README
```

当 `user_project_context` 为空、`project_context` 为 README 全文时，整个 README 被嵌入报告。

**修复方向**

```python
# 最多截取前 500 字符，避免 README 全文嵌入
MAX_CTX_CHARS = 500
if user_ctx and len(user_ctx) > MAX_CTX_CHARS:
    user_ctx = user_ctx[:MAX_CTX_CHARS].rstrip() + "…"
```

或者在 initialize 阶段生成 `user_project_context` 时，主动截断为不超过 500 字符的摘要。

---

## 体验改进点

### P3 — Precheck 问题不写入报告

`precheck_plan_integrity()` 捕获的 MISMATCH / NOT-BATCHED 问题在 plan review 阶段消费后被丢弃，不出现在 MERGE_PLAN 报告中。审查者无法知道是否有文件因分类器与批次不一致而被自动纠正。

**建议**：在 `_planner_judge_log()` 前增加 `_precheck_issues_section()`，列出 precheck 检测到的问题（即使已被自动处理）。

---

### P3 — 安全模式命中计数缺失

报告汇总表只有 `Human required: N`。当 `N=0` 时，用户无法区分"真的没有安全文件"和"模式没生效"。

**建议**：在汇总表增加一行：

```python
sensitive_count = sum(1 for fd in state.file_diffs if fd.is_security_sensitive)
f"| Security sensitive | {sensitive_count} |"
```

这样 `Human required: 0` + `Security sensitive: 0`（而 auth 文件显然存在）就能立即提示配置问题。

---

### P3 — C-class 批次在报告中缺乏突出标注

当前 C-class 文件与 B-class 文件共享相同的批次展示格式：

```
### Batch `46c74afb` — auto_risky [both_changed]
Layer: 2 | 3 files
```

`[both_changed]` 标签存在但不突出，与普通 `auto_risky [upstream_only]` 批次在视觉上没有区别。

**建议**：在 `_batch_plan()` 中对 `change_category=FileChangeCategory.C` 的批次增加醒目标注，如在批次标题前加 `⚠️ THREE-WAY CONFLICT`。

---

## 问题优先级汇总

| # | 类别 | 描述 | 优先级 |
|---|------|------|--------|
| 1 | Bug | `security_sensitive` 模式匹配未命中，`is_security_sensitive` 全为 False | **P0** |
| 2 | Prompt | Planner human_required 规则与 Evaluation System 死锁矛盾 | **P1** |
| 3 | Logic | PlannerJudge 对 C-class + auth 路径无专项审查规则 | **P1** |
| 4 | Report | Project Context 嵌入完整 README，报告不可读 | **P2** |
| 5 | Report | Precheck 问题不写入报告 | P3 |
| 6 | Report | 安全模式命中计数缺失，无法判断配置是否生效 | P3 |
| 7 | Report | C-class 批次缺少突出标注 | P3 |

**建议优先级**：先诊断并修复 P0（安全模式传递）→ 再统一 P1 的 Prompt 规则 → 最后修复 P2/P3 报告问题。P0 修复后 HUMAN_REQUIRED 应该能正确触发，届时重跑测试验证其他问题是否被连带修复。
