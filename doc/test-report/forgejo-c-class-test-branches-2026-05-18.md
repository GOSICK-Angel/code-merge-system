# Code Merge System — forgejo C-class 冲突测试分支建立报告

**生成时间**: 2026-05-18
**目标仓库**: `/Users/angel/AI/merge-test/forgejo`
**目的**: 构造含共同祖先的测试分支对，覆盖 C-class 冲突 / HUMAN_REQUIRED / plan_revising / 完整 human_review 流程

---

## 1. 背景与问题

之前使用 `gitea/main` + `merge/test2` 两个无共同祖先的分支做测试，存在以下覆盖盲区：

| 问题 | 根因 |
|------|------|
| `plan` 不调用 LLM | `state.file_categories` 始终由 `initialize` 填充，走确定性 layered path；LLM 路径（`_classify_batch`）仅在 `file_categories` 为空时触发，无法到达 |
| `plan_revising` SKIPPED | 无 C-class 文件 → PlannerJudge 在 Round 0 直接 APPROVED → `plan_revision_rounds == 0` → status 置为 "skipped" |
| `human_review` 显示 PENDING | 无 `HUMAN_REQUIRED` 文件（无 `security_sensitive.patterns`，且无 C-class 冲突升级） → `pending_user_decisions` 为空 → `HumanReviewPhase` 未写入 `PhaseResult` → Dashboard 默认显示 "pending" |
| `MERGE_PLAN_*.md` 生成太晚 | `write_merge_plan_report()` 仅在 `HumanReviewPhase` Case 2 中调用，planning phase 结束后无报告可供 PlannerJudge 参考 |

**核心根因**：`gitea/main` 与 `forgejo` 无共同祖先，`get_merge_base()` fallback 至 upstream HEAD，所有文件均为 B/D-class，永远不会出现 C-class（双边修改）。

---

## 2. 操作记录

### 2.1 创建测试分支

```bash
cd /Users/angel/AI/merge-test/forgejo

# test/upstream：指向 forgejo tip（20 commits ahead of common base）
git checkout -b test/upstream forgejo
# → 停在 4ecb25a54（forgejo 最新 commit）

# test/fork：从 forgejo~20（共同祖先）出发，叠加 fork 改动
git checkout -b test/fork-1 160377405
```

共同祖先 commit：`160377405c53145e56dd0aab6ee05fce9764c184`（`forgejo~20`）

### 2.2 test/fork 改动内容

在共同祖先基础上修改三个文件，与 `test/upstream`（包含 forgejo 的 20 个 upstream commits）形成真实三路冲突：

#### `models/auth/auth_token.go`

| 改动类型 | 内容 |
|---------|------|
| 新增常量 | `const MaxActiveTokensPerUser = 10` |
| 新增 var 成员 | `LongTermAuthorizationGuest AuthorizationPurpose = "long_term_authorization_guest"` |
| struct 新增字段 | `CreatedUnix timeutil.TimeStamp` + `UserAgent string` |

**冲突点**：`test/upstream` 在同一 var 块新增了 `LongTermAuthorizationSSO`，在同一 struct 新增了 `LoginSourceID optional.Option[int64]` → C-class。

#### `models/user/user.go`

| 改动类型 | 内容 |
|---------|------|
| 新增帮助函数 | `func EmailAuthCodeExpiry() int64` |
| 修改 `GenerateEmailAuthorizationCode` | 使用 `EmailAuthCodeExpiry()` 计算过期时间 |

**冲突点**：`test/upstream` 对同一函数添加了 `optional.None[int64]()` 参数 → C-class。

#### `routers/web/auth/oauth.go`

| 改动类型 | 内容 |
|---------|------|
| 新增 session 审计 | `ctx.Session.Set("oauth_provider", provider)` 记录 OAuth 提供商 |

**冲突点**：`test/upstream` 在同一区域新增了 `promptParam` 和 `isOIDCSilentAuthFailure` 处理逻辑 → C-class。

### 2.3 Commits

```
test/fork:
  49e919c  chore(config): use test/upstream+test/fork, add auth security_sensitive patterns
  56516543 feat(fork): add auth token guest purpose, session audit trail, expiry helper
```

### 2.4 config.yaml 更新

文件：`/Users/angel/AI/merge-test/forgejo/.merge/config.yaml`

```yaml
upstream_ref: test/upstream   # 原: gitea/main
fork_ref: test/fork           # 原: merge/test2

security_sensitive:
  patterns:
    - "models/auth/**"
    - "routers/web/auth/**"
    - "services/auth/**"
```

### 2.5 planning.py 修改

文件：`src/core/phases/planning.py`

在 `PlanningPhase.after()` 末尾新增调用：

```python
write_merge_plan_report(state)
```

使 plan report 在 planning phase 结束后立即生成，而不是等到 `HumanReviewPhase`。

---

## 3. 预期测试结果

### 3.1 文件分类

| 文件 | 分类 | 原因 |
|------|------|------|
| `models/auth/auth_token.go` | **C-class** | fork 和 upstream 均修改，有共同祖先 |
| `models/user/user.go` | **C-class** | fork 和 upstream 均修改，有共同祖先 |
| `routers/web/auth/oauth.go` | **C-class** | fork 和 upstream 均修改，有共同祖先 |
| 其余 forgejo~20..forgejo 新增文件 | **B-class** | 仅 upstream 修改 |

### 3.2 风险等级

| 文件 | 预期风险 | 原因 |
|------|---------|------|
| `models/auth/auth_token.go` | `HUMAN_REQUIRED` | 命中 `security_sensitive.patterns: models/auth/**` |
| `routers/web/auth/oauth.go` | `HUMAN_REQUIRED` | 命中 `security_sensitive.patterns: routers/web/auth/**` |
| `models/user/user.go` | `HIGH` 或 `HUMAN_REQUIRED` | C-class + auth 相关路径 |

### 3.3 各 Phase 预期状态

| Phase | 预期状态 | 原因 |
|-------|---------|------|
| `initializing` | completed | 正常 |
| `analysis` (planning) | completed | 正常；`_build_layered_plan` 按 risk 分层 |
| `MERGE_PLAN_*.md` | **立即生成** | `PlanningPhase.after()` 调用 `write_merge_plan_report()` |
| `plan_revising` | **completed**（非 skipped） | C-class 文件存在 mis-classification 风险，PlannerJudge 有内容可审查；预计至少 1 轮 revision |
| `human_review` | **AWAITING_HUMAN** | `HUMAN_REQUIRED` 文件产生 `UserDecisionItem`，触发人工决策等待 |
| `auto_merging` | 仅在人工决策完成后继续 | — |

### 3.4 plan_revising 触发条件验证

PlannerJudge 审查点：
- `auth_token.go` 的结构体字段冲突（`CreatedUnix`/`UserAgent` vs `LoginSourceID`）是否正确标注为需人工介入
- `LongTermAuthorizationGuest` vs `LongTermAuthorizationSSO` 语义冲突是否被识别
- oauth.go session 审计逻辑与 upstream 新增流程的兼容性分析

### 3.5 human_review 决策项

预期 `pending_user_decisions` 包含至少 2 个 `UserDecisionItem`：
- `models/auth/auth_token.go` — struct 字段合并策略
- `routers/web/auth/oauth.go` — session 审计与新 OAuth 流程兼容性

---

## 4. 验证步骤

```bash
# 在 forgejo 仓库目录运行
cd /Users/angel/AI/merge-test/forgejo
merge

# 观察以下输出：
# 1. initialize phase: file_categories 中 C-class 文件数 ≥ 3
# 2. planning phase 结束后: .merge/plans/ 目录有 MERGE_PLAN_*.md 生成
# 3. plan_review phase: PlannerJudge round > 0（非短路 APPROVED）
# 4. plan_revising status = "completed"（非 "skipped"）
# 5. 系统进入 AWAITING_HUMAN，Web UI human_review = "awaiting"
# 6. 人工决策页显示 ≥2 个 HUMAN_REQUIRED 文件
```

---

## 5. 代码变更汇总

| 文件 | 变更 | 类型 |
|------|------|------|
| `src/core/phases/planning.py` | `after()` 末尾调用 `write_merge_plan_report(state)` | fix |
| `src/core/phases/planning.py` | 新增 `from src.tools.merge_plan_report import write_merge_plan_report` | fix |
| `/Users/angel/AI/merge-test/forgejo/.merge/config.yaml` | `upstream_ref/fork_ref` 指向新测试分支；新增 `security_sensitive.patterns` | test config |
| `/Users/angel/AI/merge-test/forgejo` `test/fork` branch | 三个 auth 文件的 fork 改动 + config 更新 | test fixture |
