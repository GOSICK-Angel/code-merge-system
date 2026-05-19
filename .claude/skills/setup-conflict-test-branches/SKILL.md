---
name: setup-conflict-test-branches
description: 为 Code Merge System 建立冲突测试分支。用于对任意目标仓库建立含共同祖先的 test/upstream + test/fork 分支对，覆盖 C-class 冲突和 HUMAN_REQUIRED 路径。当用户需要对新仓库建立测试基线、或提到"建测试分支"/"冲突测试"/"C-class 覆盖"时立即使用此 skill。
---

# 建立冲突测试分支 (Code Merge System)

Code Merge System 的 C-class 文件分类、plan_revising、AWAITING_HUMAN 等核心路径**只有在 fork 与 upstream 有共同祖先时才能被触发**。没有共同祖先，`get_merge_base()` 会 fallback 到 upstream HEAD，所有文件归 B/D-class，C-class 路径永远不可达。

---

## Step 1 — 选定共同祖先

```bash
cd <target-repo>
git log --oneline -30 <main-branch>
```

选一个 commit 作为 `BASE_COMMIT`：

- 距 HEAD **15–30 个 commit**（太近则 upstream 改动太少；太远则 B-class 文件泛滥）
- 该 commit 之后 upstream 有改动的文件，应与你计划在 fork 中修改的文件有重叠

---

## Step 2 — 创建 test/upstream

```bash
git checkout -b test/upstream <main-branch>
```

`test/upstream` 指向当前 main HEAD，扮演开源 upstream 最新版本。

---

## Step 3 — 创建 test/fork 并制造 C-class 冲突

```bash
git checkout -b test/fork $BASE_COMMIT
```

### 3.1 找出 C-class 候选文件

```bash
# upstream 在 BASE_COMMIT 之后改动了哪些文件
git diff --name-only $BASE_COMMIT test/upstream | head -30
```

从中挑 **3–5 个文件**：优先选认证、权限、核心数据结构路径（这类文件后续更容易命中 `security_sensitive.patterns`）。

### 3.2 在 test/fork 上改动这些文件

改动必须与 upstream 的改动**位置相邻或重叠**，否则不会产生三路冲突：

| 冲突模式 | fork 做什么 | upstream 做了什么 |
|---------|-----------|----------------|
| 常量/枚举扩展 | 在同一 `var` 块新增常量 | 同块也新增了其他常量 |
| struct 字段 | 在 struct 尾部新增字段 | 同 struct 也新增了字段 |
| 函数重构 | 提取 helper，修改调用方 | 同函数增加了新参数 |
| 逻辑注入 | 在某个区域插入新逻辑行 | 同区域也插入了其他逻辑 |

```bash
git add <修改的文件>
git commit -m "feat(fork): <描述 fork 引入的业务改动>"
```

---

## Step 4 — 配置 .merge/config.yaml

```yaml
upstream_ref: test/upstream
fork_ref: test/fork

security_sensitive:
  patterns:
    - "<认证相关路径>/**"     # 覆盖 ≥1 个 Step 3 修改的 C-class 文件
    - "<权限相关路径>/**"
```

`security_sensitive.patterns` 至少命中 1–2 个 C-class 文件，才能触发 HUMAN_REQUIRED 和 AWAITING_HUMAN 流程。

---

## Step 5 — 验证分支结构

```bash
# 1. 确认共同祖先存在
git merge-base test/upstream test/fork
# → 应输出 BASE_COMMIT hash（非 upstream HEAD）

# 2. 确认 C-class 交集文件数量
comm -12 \
  <(git diff --name-only $BASE_COMMIT test/fork | sort) \
  <(git diff --name-only $BASE_COMMIT test/upstream | sort)
# → 应有 ≥2 个文件；若为空则回 Step 3 重新选文件
```

交集文件数量 < 2 时，系统不会进入 C-class 路径，重新选更靠近 upstream 改动位置的文件重做 Step 3。

---

## Step 6 — 运行合并

```bash
cd <target-repo>
merge
```

分支结构正确时，系统会依次经过：

```
PLANNING → PLAN_REVIEWING → AUTO_MERGING → ANALYZING_CONFLICTS
→ AWAITING_HUMAN → JUDGE_REVIEWING → GENERATING_REPORT
```

human_review 阶段需要在 Web UI 填写决策后继续。

---

## Step 7 — 生成校验文档

合并完成（或在任意 phase 暂停）后，用 skill 内置脚本生成结构化校验报告：

```bash
python <skill_dir>/scripts/generate_verification_doc.py <target-repo> \
  --output merge_verification_<run_id>.md
```

`<skill_dir>` 为本 skill 目录（`.claude/skills/setup-conflict-test-branches/`）。  
`--run-id <id>` 可指定特定 run，省略则自动取最新一次。

### 校验文档涵盖内容

| 章节 | 内容 |
|------|------|
| **基本信息** | run_id、status、分支引用、merge_base、耗时、费用摘要 |
| **分支差异** | C-class / B-class 文件列表（由 git diff 实时计算） |
| **Phase 1 Initialize** | 文件分类汇总（C/B/D-class 计数）、C-class 文件风险评分 |
| **Phase 2 Planning** | MergePlan 风险分布、HUMAN_REQUIRED 文件列表、merge phase 批次 |
| **Phase 3 Plan Review** | PlannerJudge 裁定、修订轮次、每轮 issues |
| **Phase 4 Auto Merge** | 每个文件的决策（SEMANTIC_MERGE/TAKE_TARGET/…）、来源、回滚情况、plan_disputes |
| **Phase 5 Conflict Analysis** | 每个文件的置信度、推荐策略、冲突类型、安全敏感标记 |
| **Phase 6 Human Review** | 决策项数、已填决策内容 |
| **Phase 7 Judge Review** | JudgeVerdict（verdict/置信度/VETO）、未通过文件、Issues、SmokeTest 结果 |
| **Phase 8 Report Generation** | merge_report.md 路径及预览 |
| **最终合并结果** | 文件处置汇总表 + 前 6 个文件的 `git diff`（fork base → 合并后） |

### 脚本依赖

- Python 3.11+（标准库，无需安装额外包）
- `git` 命令（在 `<target-repo>` 内执行）
- 至少存在一个 `checkpoint.json`（dev: `outputs/debug/checkpoints/`；production: `.merge/runs/<run_id>/`）
