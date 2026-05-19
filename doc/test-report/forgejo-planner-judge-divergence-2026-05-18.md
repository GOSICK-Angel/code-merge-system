# Planner / PlannerJudge 无法收敛分析报告

**Run ID**: `8e8feced-a9d3-405d-898b-74542add365a`
**生成时间**: 2026-05-18
**结论**: `review_conclusion.reason = max_rounds`，3 轮均为 `revision_needed`，未能 APPROVED

---

## 1. 运行摘要

| 轮次 | 裁定 | 新 issue 数 | Planner 接受 | Planner 拒绝 |
|------|------|------------|------------|------------|
| Round 0 | revision_needed | 5 | 5 | 0 |
| Round 1 | revision_needed | 3 | 3 | 0 |
| Round 2 | revision_needed | 1 | — (max_rounds hit) | — |

**模式**：每轮 PlannerJudge 发现新的「auth 路径文件」并要求从 `auto_safe` 升级为 `auto_risky`；Planner 接受全部建议；下一轮 PlannerJudge 又找到新的 auth 路径文件。循环在 `max_plan_revision_rounds=2` 耗尽后终止。

### 被逐轮标记的文件

| 轮次 | 文件 | 标记理由（摘要）|
|------|------|----------------|
| R0 | `routers/web/auth/auth.go` | "auth 路径，安全敏感" |
| R0 | `routers/web/auth/password.go` | "密码认证，安全关键" |
| R0 | `services/auth/source.go` | "auth 服务层，安全敏感" |
| R0 | `services/auth/source/oauth2/source_callout.go` | "OAuth2，token 处理" |
| R0 | `services/context/context_cookie.go` | "cookie/session，auth 状态" |
| R1 | `cmd/admin_user_generate_authorized_integration.go` | "admin 凭据生成命令" |
| R1 | `models/auth/authorized_integration.go` | "models/auth 下的持久化" |
| R1 | `services/authz/authorized_integration.go` | "authz 服务逻辑" |
| R2 | `templates/user/settings/keys_ssh.tmpl` | "SSH key 管理模板" |

**全部 9 个文件的共同特征**：`conflict_count = 0`，`is_security_sensitive = false`（未命中 config 的 `security_sensitive.patterns`），均为 B-class（upstream-only 变更）。

---

## 2. 根本原因分析

### 2.1 PlannerJudge prompt 自相矛盾

**System prompt（"When NOT to raise"）**：
```
File has `conflict_count = 0` AND `is_security_sensitive = false`
→ its `auto_safe` classification is almost certainly correct; do NOT suggest upgrading it
```

**Review task #3**：
```
3. Files that are obviously security-critical by name/path
   but classified `auto_safe` → flag
```

这两条规则直接冲突。`auth.go` 的 `conflict_count = 0` + `is_security_sensitive = false` 本应触发"不标记"规则，但 task #3 要求仅凭路径名就标记。**模型优先遵从了 task #3**（列表中更显著的结构化指令），忽略了系统 prompt 中的"When NOT to raise"约束。

### 2.2 Manifest 不显示 `conflict_count=0`

`_build_file_manifest()` 的标记逻辑：
- `is_security_sensitive = true` → 显示 `[SEC]`
- `conflict_count > 0` → 显示 `[conflicts=N]`
- 无冲突、无 SEC → **不显示任何标记**

模型看到的是：
```
routers/web/auth/auth.go: auto_safe
```

没有任何数据可以反驳"auth 路径 = 安全风险"的直觉。如果显示 `[conflicts=0]`，模型就有明确的事实依据去匹配"When NOT to raise"规则。

### 2.3 Planner 评估 prompt 缺少 `conflict_count` 和 `is_security_sensitive`

`build_evaluation_prompt()` 传给 Planner 的信息仅包含：
- `current_classification: auto_safe`
- `suggested_classification: auto_risky`
- `reason: "auth 路径，安全敏感"`

Planner **看不到** `conflict_count = 0`，无法以此为据拒绝建议。它只能依赖 judge 的理由文本，而"auth 路径安全敏感"听起来合理，因此每次都接受。

### 2.4 问题的本质

这是一个 **"B-class 文件被错误升级"** 的问题。B-class 文件（只有 upstream 修改，fork 未触碰）的正确合并策略是 `take_target`（直接取 upstream 版本），无论其路径名如何敏感。分类为 `auto_safe` 完全正确。PlannerJudge 的路径名启发式规则不应覆盖 `conflict_count=0` 这个决定性事实。

---

## 3. 建议修复

### Fix 1：收紧 task #3（主修复）

**当前**（`build_segment_plan_review_prompt` / `build_plan_review_prompt` 中）：
```
3. Files that are obviously security-critical by name/path
   but classified `auto_safe` → flag
```

**修改为**：
```
3. Files that are obviously security-critical by name/path,
   classified `auto_safe`, AND have at least one of:
   [SEC] flag (is_security_sensitive=true) OR conflicts>0 flag in the manifest → flag.
   Path name alone is NOT sufficient when conflict_count=0 and is_security_sensitive=false.
```

这消除了与"When NOT to raise"的矛盾，使两条规则一致。

### Fix 2：在 Manifest 中显示 `conflicts=0`（辅助修复）

修改 `_build_file_manifest()` 的标记逻辑：

```python
# 当前：只在 conflict_count > 0 时添加 flag
if fd.conflict_count > 0:
    flags.append(f"conflicts={fd.conflict_count}")

# 修改为：始终显示冲突数
flags.append(f"conflicts={fd.conflict_count}")
```

或者更简洁：仅对 auth/security 路径显示 `conflicts=0`。最简单的改法是始终显示：

```python
flags.append(f"conflicts={fd.conflict_count}")
```

这样模型看到 `routers/web/auth/auth.go: auto_safe [conflicts=0]`，"When NOT to raise"规则有了明确的数据锚点。

### Fix 3：在 Planner 评估 prompt 中传入文件元数据（深层修复）

修改 `build_evaluation_prompt()` 的 issue 渲染，加入文件的关键元数据：

```python
issues_text = "\n".join(
    f"- issue_id: {issue.issue_id}\n"
    f"  file_path: {issue.file_path}\n"
    f"  conflict_count: {file_meta.get(issue.file_path, {}).get('conflicts', 0)}\n"
    f"  is_security_sensitive: {file_meta.get(issue.file_path, {}).get('sec', False)}\n"
    f"  current_classification: {_render_curr(issue)}\n"
    f"  suggested_classification: {issue.suggested_classification.value}\n"
    f"  reason: {issue.reason}"
    for issue in capped
)
```

加上指导语：
```
If conflict_count=0 AND is_security_sensitive=false AND the file is B-class
(upstream-only change), the auto_safe classification is correct — REJECT
path-name-only escalation requests.
```

---

## 4. 优先级与影响

| 修复 | 工作量 | 预期效果 |
|------|--------|---------|
| Fix 1：收紧 task #3 | 小（改 prompt 字符串） | 直接阻止 R0-R2 的所有误标 |
| Fix 2：Manifest 显示 conflicts=0 | 小（改一行逻辑） | 给模型提供反驳证据，防止类似场景 |
| Fix 3：Planner eval 传入元数据 | 中（需改函数签名） | 使 Planner 能拒绝无依据的升级请求 |

**建议**：Fix 1 + Fix 2 组合实施，Fix 3 作为后续改进。Fix 1 解决语义矛盾，Fix 2 提供数据支撑，两者结合后 Round 0 预计可直接 APPROVED（84 个文件中三个 C-class auth 文件无 `conflict_count > 0`，应标记为有冲突的文件才触发升级）。

---

## 5. 补充：C-class 文件未被识别的问题

本次测试的另一个发现：三个手工构造的 C-class 文件（`models/auth/auth_token.go`、`models/user/user.go`、`routers/web/auth/oauth.go`）被分配到 `auto_risky` batch，但其 `conflict_count` 显示为 0。

这说明三路冲突检测（`get_conflict_count()`）可能没有正确识别这些文件的冲突。需要单独确认 `initialize` phase 的 `conflict_count` 计算逻辑是否对 `test/fork` + `test/upstream` 分支对正确工作。
