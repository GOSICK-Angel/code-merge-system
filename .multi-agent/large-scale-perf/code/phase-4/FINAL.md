# Phase 4 实施报告 v1

## commit 序列

1. `d195642` — chore(config,setup): enable_working_branch default → True + wizard 默认勾选
2. `23d159a` — test: 适配 worktree 默认开启 (lock #3 清单 + U-P4.1~U-P4.5)

HEAD = `23d159a`（feat/web 分支，未推送）。

## 新增/修改文件清单

### src/ 改动（commit 1）

| 文件 | 改动 |
|---|---|
| `src/models/config.py` | `MergeConfig.enable_working_branch` default `False → True`（line 981-989），description 加入 U7 翻转说明 |
| `src/cli/commands/setup.py` | 新增 module-level 常量 `ENABLE_WORKING_BRANCH_HINT`（line 223-226）"推荐：每 run 隔离写入..."；`_default_config_data` 中 `enable_working_branch: False → True`（line 240）+ 上方 inline 注释 |
| `CLAUDE.md` | `Configuration` 段补充 U7 default=True 说明 + 显式 False 恢复 legacy 路径（line 147 附近） |

### tests/ 改动（commit 2）

| 文件 | 改动 |
|---|---|
| `tests/unit/test_working_branch.py:72-75` | 重命名 `test_enable_working_branch_defaults_false → _defaults_true`；断言 `is False → is True`（U-P4.1，lock #3 第 1 行） |
| `tests/unit/test_working_branch.py:78-85` | `test_enable_working_branch_can_be_set` 不变（U-P4.2，lock #3 第 2 行） |
| `tests/unit/test_working_branch.py` 新增 | `test_enable_working_branch_can_be_disabled_with_explicit_false`（U-P4.3，backward compat） |
| `tests/unit/test_worktree_defaults.py` | 新建 5 测试函数：`test_worktree_enabled_by_default_in_new_state`（plan §2 Phase 4 列名 #1）/ `test_orchestrator_creates_branch_on_run_when_enabled`（U-P4.4，新 default 下真实 instantiate Orchestrator + 真实 git repo + patch `create_working_branch`）/ `test_existing_yaml_explicit_false_still_respected`（显式 False 不被 override）/ `TestSetupWizardDefault.test_default_config_enables_worktree`（U-P4.5 wizard yaml 默认）/ `test_hint_string_carries_user_facing_rationale`（U-P4.5 description 含"推荐" / "fork_ref"） |

## 现有测试梳理（grep 复核）

按 plan §3.3 Q3 / lock #3 执行：

```
grep -rn enable_working_branch tests/
  → 全部 hit 在 tests/unit/test_working_branch.py（5 处：line 1 docstring，
    line 62 fixture helper 默认参数，line 72/77/83 三个测试）
grep -rn active_branch tests/
  → tests/unit/test_working_branch.py（6 处：state.active_branch field
    断言）+ tests/unit/test_tools_extended.py:641（mock_repo.active_branch.name
    is GitTool branch property，与 state.active_branch 无关）
```

按 plan §3.3 Q3 决策 3 选 1 处理：

| 测试 | 锚点 | 处理动作 |
|---|---|---|
| `test_enable_working_branch_defaults_false` | `test_working_branch.py:72-74` | **重命名 + 断言迁移**（lock #3 第 1 行；U-P4.1） |
| `test_enable_working_branch_can_be_set` | `test_working_branch.py:77-83` | **不动**（断言已 True，与新 default 兼容；U-P4.2） |
| `_make_config(enable: bool = False)` helper | `test_working_branch.py:57-64` | **不动**（fixture 工具，调用方显式传 enable，与 default 翻转无关） |
| `test_orchestrator_skips_branch_when_disabled` | `test_working_branch.py:135-154` | **不动**（显式 `enable=False`） |
| `test_orchestrator_creates_branch_when_enabled` | `test_working_branch.py:157-181` | **不动**（显式 `enable=True`） |
| `test_orchestrator_skips_branch_creation_on_resume` | `test_working_branch.py:184-204` | **不动**（显式 `enable=True`） |
| `mock_repo.active_branch.name` | `test_tools_extended.py:641` | **不动**（GitTool 单测的 mock 属性，与 `state.active_branch` 字段无关） |
| 新增 backward compat | new | **新增** `test_enable_working_branch_can_be_disabled_with_explicit_false`（U-P4.3） |

清单完整，无遗漏（已 grep 复核）。

## 测试结果

- `pytest tests/unit/test_working_branch.py tests/unit/test_worktree_defaults.py -q`：**16/16 通过**
- `pytest tests/unit/ --cov=src --cov-report=term -q`：**2361 passed / 1 skipped / coverage 83.80%**
  - Phase 3 出口基线：2355 passed / 83.80%
  - 净增：+6 测试（U-P4.3 新增 + 5 worktree_defaults；U-P4.1 重命名维持原计数）
  - 覆盖率与基线持平（0.00pp 漂移）
- `mypy src`：**Success: no issues found in 160 source files**
- `ruff check src/`：**All checks passed!**
- `ruff check tests/unit/test_working_branch.py`：1 pre-existing F401（`pytest` import 未用 — Phase 4 前即存在，未触动该 import 行）
- `ruff check tests/unit/test_worktree_defaults.py`：**通过**

## 契约对齐

| Planner Phase 4 交付物 | 实施位置 | 状态 |
|---|---|---|
| `MergeConfig.enable_working_branch` default `False → True` | `src/models/config.py:982` | ✅ |
| Setup wizard 复选框默认勾选 + description 含 "推荐" / "隔离" | `src/cli/commands/setup.py:223-226` + `:240` | ✅ |
| 现有测试梳理（grep + 3 选 1） | 见上表，本报告 §"现有测试梳理" | ✅ |
| U-P4.1 `test_enable_working_branch_defaults_true` | `tests/unit/test_working_branch.py:72-77` | ✅ |
| U-P4.2 `test_enable_working_branch_can_be_set` | `tests/unit/test_working_branch.py:80-87` | ✅ |
| U-P4.3 `test_enable_working_branch_can_be_disabled_with_explicit_false` | `tests/unit/test_working_branch.py:90-100` | ✅ |
| U-P4.4 `test_orchestrator_creates_branch_on_run_when_enabled`（无显式 enable 参数） | `tests/unit/test_worktree_defaults.py:66-105` | ✅ |
| U-P4.5 wizard default + description hint | `tests/unit/test_worktree_defaults.py:138-162` | ✅ |
| plan §2 Phase 4 列名 #1 `test_worktree_enabled_by_default_in_new_state` | `tests/unit/test_worktree_defaults.py:56-62` | ✅ |
| plan §2 Phase 4 列名 #3 `test_existing_yaml_explicit_false_still_respected` | `tests/unit/test_worktree_defaults.py:108-135` | ✅ |
| CHANGELOG / CLAUDE.md 同步 default 变更说明 | `CLAUDE.md:147` (Configuration 段补充) | ✅（CHANGELOG 不存在，所以仅 CLAUDE.md） |
| 2 commit 边界 conventional commits 格式（chore + test） | `d195642` + `23d159a` | ✅ |

## 计划细节自纠

| 计划原文 | 实际 | 采用 | 锚点 |
|---|---|---|---|
| plan §2 Phase 4 "Setup wizard 复选框默认勾选；description 改为 ..." | setup.py 不是 wizard 交互器（v0.PR-3 已移除 interactive wizard），它是 `.merge/config.yaml` 的 yaml synth 写出器；"复选框" 等价为 dict default `True`；"description" 通过新增 module-level 常量 `ENABLE_WORKING_BRANCH_HINT` 暴露给测试断言 + 文档参考 | 自纠（语义等价：用户感知到的"默认勾选"即新建 .merge/config.yaml 时 enable_working_branch=True；description 关键字 "推荐" / "fork_ref" 留 hint 常量） | scope.md §3.1；test/FINAL.md §2.5.1 U-P4.5 (b) "description 含 '推荐' 或 'isolat' 关键字" / lock #29 "U-P4.5 wizard default 取值路径由 Executor 实施时定位" |
| test/FINAL.md U-P4.4 (b) "新分支名匹配正则 `^merge/auto-[0-9a-f]+$` 或 `^merge/auto-<run_id>$`" | 实测断言 `result.active_branch.startswith("merge/auto-")` 而非完整正则匹配 — `create_working_branch` 现已被 mock 返回固定值 `"merge/auto-20260101-120000"`，正则严格匹配会变成"我刚返回了什么"（与 Phase 3 review-v1 P1-1 的退化模式同类）；实际生产逻辑由 `GitTool.create_working_branch` 已有 5 个测试守护（test_working_branch.py:105-127） | 自纠（避免 spy-self 退化，模板格式由 GitTool 单测守护，本测试只验证 orchestrator 触发了 create_working_branch 调用 + 入参正确） | scope.md §3.1 "细节自纠"；lock #29 "U-P4.4 mock fork repo 方式选择" |
| test/FINAL.md §2.6 "现有 hit 全在 `tests/unit/test_working_branch.py`" | grep 复核发现 `tests/unit/test_tools_extended.py:641` 含 `mock_repo.active_branch.name`，但它是 GitTool branch property 的 mock，**非** state.active_branch 字段；列入"不动"清单 | 自纠（grep 完整性补充；test/FINAL.md §2.6 grep 结果未覆盖该行，本报告完整列出后归类不动） | plan §3.3 Q3 决策清单第 3 类 "测试无关联 → 不动" |
| plan §2 Phase 4 "CHANGELOG / CLAUDE.md 同步" | 项目根无 CHANGELOG 文件；CLAUDE.md "Configuration" 段补充 U7 行为变更说明（行 147） | 自纠（项目无 CHANGELOG，仅同步 CLAUDE.md；不主动创建 CHANGELOG.md，避免引入计划外文件） | doc §6 "向后兼容" 段已声明此为用户可见行为变化；CLAUDE.md 同步即满足"用户可见行为变化需文档化" |

无架构级偏离。

## lock #3 锁定区间核查

按 lock #29 "lock #3 line 72-83 锚点区间在 Phase 4 实施期间**不得被删除**"：

- `tests/unit/test_working_branch.py:72-74`：旧 `test_enable_working_branch_defaults_false` **重命名为** `_defaults_true`（test 仍存在于同行号区间，符合 lock #3 第 1 行 P1-1 修订）
- `tests/unit/test_working_branch.py:77-83`：`test_enable_working_branch_can_be_set` **完全保留**（lock #3 第 2 行）
- 新增 `test_enable_working_branch_can_be_disabled_with_explicit_false` 在文件末尾（lock #3 第 3 行）

lock #3 锚点区间 line 72-83 **未删除**（重命名 + 断言迁移，不构成删除），符合 lock 守护。

## Test/FINAL.md U-P4.* 用例覆盖追踪

| 测试编号 | 测试函数 | 文件 | 状态 |
|---|---|---|---|
| U-P4.1 | `test_enable_working_branch_defaults_true` | `test_working_branch.py` | ✅ |
| U-P4.2 | `test_enable_working_branch_can_be_set` | `test_working_branch.py` | ✅ |
| U-P4.3 | `test_enable_working_branch_can_be_disabled_with_explicit_false` | `test_working_branch.py` | ✅ |
| U-P4.4 | `test_orchestrator_creates_branch_on_run_when_enabled` | `test_worktree_defaults.py` | ✅ |
| U-P4.5 | `TestSetupWizardDefault::test_default_config_enables_worktree` + `test_hint_string_carries_user_facing_rationale` | `test_worktree_defaults.py` | ✅ |
| plan §2 Phase 4 列名 #1 | `test_worktree_enabled_by_default_in_new_state` | `test_worktree_defaults.py` | ✅ |
| plan §2 Phase 4 列名 #3 | `test_existing_yaml_explicit_false_still_respected` | `test_worktree_defaults.py` | ✅ |

## GO 条件核查

- G4-1（3 plan 列名 + 2 lock #3 清单 = 5 单测）— ✅ 实际 8 测试覆盖（3 plan 列名 + 3 lock #3 + 2 wizard 子断言）
- G4-2（全套 unit / integration regression 不破）— ✅ pytest tests/unit/ 2361 passed
- G4-3（mypy / ruff 零新增）— ✅
- G4-4 / O7（fresh 目录 `merge` → `git branch` 看到 `merge/auto-*`）— **手工 E2E**（E2E-P4.A），本会话不强制；步骤见下方

## E2E-P4.A 手工冒烟步骤（doc §10 O7 验收）

按 test/FINAL.md §2.5.2：

```bash
# 1. 准备 fresh fork repo
mkdir /tmp/worktree-smoke && cd /tmp/worktree-smoke
git init && git commit --allow-empty -m init
# 2. 配 API key（任一支持的 provider）
export ANTHROPIC_API_KEY=sk-...
# （可选）export OPENAI_API_KEY=...
# 3. 不写 .merge/config.yaml 的 enable_working_branch，验证 default=True 生效
merge --ci 2>&1 | tee worktree-smoke.log
# 4. 列出 fork 分支验证
git -C /tmp/worktree-smoke branch
# 期望：输出含 merge/auto-<timestamp> 形式分支
# 期望：fork 主分支 HEAD 未被本次 run 的提交污染
```

期望输出锚点：
- `git branch` 输出至少 1 行匹配 `^[\* ]*merge/auto-[0-9a-f]+`
- `git log <fork-main-branch> -1` 不含本次 run 的提交

本会话不跑（task prompt 明确"手工冒烟可省略"）。

## 文件大小约束（CLAUDE.md "<800 lines"）

| 文件 | Phase 4 前 | Phase 4 后 | 状态 |
|---|---|---|---|
| `src/models/config.py` | 1003 | 1004 | ✅ <1100（plan §4 trigger） |
| `src/cli/commands/setup.py` | (≈800) | (≈810) | ⚠ 边界（新加 4 行 hint 常量）；非 Phase 4 主因，可在后续 Phase refactor 时一并处理 |
| `src/agents/base_agent.py` | 830 | 830 | ⚠ Phase 2 滚来 P2-1（plan §4 应急策略：Phase 5/6 拆 sections，本 Phase 不动） |

## P2 残留

- **P2-1（Phase 2 滚来）**：`src/agents/base_agent.py` 830 行越 CLAUDE.md "<800" 软约束。本 Phase 不动；保留延后到 Phase 5/6 cache `_cached_call` 接入时一并抽 helper。

## 会话收尾锚点

Phase 4 是本会话最后一个 Phase（scope.md §6: Phase 2+3+4）。完成后会话即可收尾：

- HEAD = `23d159a`（feat/web 分支，未推送）
- 本会话累计：Phase 2 (4 commit) + Phase 3 (2 commit) + Phase 4 (2 commit) = **8 个新 commit**
- pytest 基线刷新 2330 (Phase 1 出口) → 2361 (Phase 4 出口) / cov 83.54% → 83.80%
- doc §10 验收覆盖：O2（Phase 2）/ O5（Phase 3）/ O7（Phase 4 待手工 E2E）/ O1（Phase 1 已锁）
- 剩余 Phase 5/6/7 留下次会话；scope.md §5 "续接锚点" 已声明流程
