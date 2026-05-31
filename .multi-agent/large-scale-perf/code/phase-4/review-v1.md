# gatekeeper-code 审查报告（Phase 4 v1）

源：`code/phase-4/v1.md` / commits `d195642` + `23d159a`
HEAD = `23d159a`（feat/web 分支，未推送）；基线 = `5d659a2`（Phase 3 出口）

## 结论
**通过**（无 P0 / P1；P2 残留仅 Phase 2 滚来的 base_agent.py 830 行越软约束，本会话不动）

## 契约核查表

| Plan / Test FINAL 契约 | 状态 | 锚点 |
|---|---|---|
| `MergeConfig.enable_working_branch` default `False → True`（lock #3 第 1 行 / facts.md I4） | ✅ | `src/models/config.py:981-989` default=True；description 含 "U7: default flipped to True" |
| Setup wizard 默认勾选（yaml synth path） | ✅ | `src/cli/commands/setup.py:244` `enable_working_branch: True`（计划自纠：v0.PR-3 已移除 interactive wizard，等价为 yaml synth default） |
| Wizard description 含 "推荐：每 run 隔离写入 + fork_ref" 关键字 | ✅ | `src/cli/commands/setup.py:223-226` `ENABLE_WORKING_BRANCH_HINT` 常量 |
| **U-P4.1** `test_enable_working_branch_defaults_true`（重命名 + 断言迁移）| ✅ | `tests/unit/test_working_branch.py:72-78` |
| **U-P4.2** `test_enable_working_branch_can_be_set`（不动） | ✅ | `tests/unit/test_working_branch.py:81-88` |
| **U-P4.3** `test_enable_working_branch_can_be_disabled_with_explicit_false`（新增 backward compat） | ✅ | `tests/unit/test_working_branch.py:91-101` |
| **U-P4.4** `test_orchestrator_creates_branch_on_run_when_enabled`（无显式 enable 参数；走真实 Orchestrator.run）| ✅ | `tests/unit/test_worktree_defaults.py:63-102`；`mock_create.assert_called_once_with("merge/auto-{timestamp}", "main")` |
| **U-P4.5** wizard yaml default + description 一致性 | ✅ | `tests/unit/test_worktree_defaults.py:137-167`（2 sub-test）|
| plan §2 P4 列名 #1 `test_worktree_enabled_by_default_in_new_state` | ✅ | `tests/unit/test_worktree_defaults.py:54-60` |
| plan §2 P4 列名 #3 `test_existing_yaml_explicit_false_still_respected`（显式 False 不被 override；orchestrator 不调 create_working_branch） | ✅ | `tests/unit/test_worktree_defaults.py:105-134`；`mock_create.assert_not_called()` |
| **lock #3 锚点区间 line 72-83 不得删除**（lock #29 第 4 行守护） | ✅ | 旧 `_defaults_false` 重命名为 `_defaults_true`（重命名 ≠ 删除），新增 `_can_be_disabled` 落 :91-101；line 72-83 区间所有测试保留 |
| CLAUDE.md "Configuration" 段同步 U7 default=True 说明 | ✅ | `CLAUDE.md:147` 新增段：`enable_working_branch defaults to True (U7)...` |
| commit 边界（chore + test 两 commit，conventional commits 格式） | ✅ | `d195642 chore(config,setup): ...` + `23d159a test: ...` |

## 测试结果

- `pytest tests/unit/ --cov=src --cov-report=term -q`：**2361 passed / 1 skipped / coverage 83.80%**
  - Phase 3 出口基线：2355 / 83.80% → 净 +6 测试 / 0pp 漂移（容差内）
- `mypy src`：**Success: no issues found in 160 source files**
- `ruff check src/`：**All checks passed!**
- `ruff check tests/unit/test_working_branch.py`：1 pre-existing F401（`pytest` import 未用 — Phase 4 前即存在，不属本 Phase 引入；与 phase-0 #22 注的 `test_agent_contracts.py` 现有 F401 同性质）

## 二审独立 acceptance 验证

参考 Phase 3 v2 acceptance 范式 — gatekeeper-code 端独立跑反向回归 net：

```
Stage A: 还原 src/models/config.py default=True → False
  → tests/unit/test_working_branch.py::test_enable_working_branch_defaults_true: FAILED (assert False is True)
  → tests/unit/test_worktree_defaults.py::test_worktree_enabled_by_default_in_new_state: FAILED
  → tests/unit/test_worktree_defaults.py::test_orchestrator_creates_branch_on_run_when_enabled: FAILED
  → 3 个测试全部 FAILED ✅

Stage B: 还原 src/cli/commands/setup.py yaml 默认 True → False
  → tests/unit/test_worktree_defaults.py::TestSetupWizardDefault::test_default_config_enables_worktree: FAILED
  → 1 个测试 FAILED ✅

Restore → 16/16 pass（test_working_branch + test_worktree_defaults 子集）
```

config default 与 setup wizard default 都受规范 regression net 守护；任一回退都被 Phase 4 测试集捕获。

## 计划细节自纠核查（v1.md 自报 4 项）

| 自纠项 | 是否合理 | 锚点 |
|---|---|---|
| #1 setup.py 不是 interactive wizard 而是 yaml synth；"复选框" 等价为 dict default True；description 通过新增模块级常量 `ENABLE_WORKING_BRANCH_HINT` | ✅ 合理（lock #29 第 5 行预留 "U-P4.5 wizard default 取值路径由 Executor 实施时定位"）；测试断言 `"推荐" in HINT` + `"fork_ref" in HINT` 覆盖 doc §5.7.2 关键字守护 | `src/cli/commands/setup.py:223-226` |
| #2 U-P4.4 (b) 模板正则 `^merge/auto-[0-9a-f]+$` → `startswith("merge/auto-")` | ✅ 合理（避免 Phase 3 review-v1 P1-1 同型 spy-self 退化；create_working_branch 已有 5 个 GitTool 测试守护模板格式，U-P4.4 范围内只验证"orchestrator 触发了 create_working_branch + 入参正确"） | `tests/unit/test_worktree_defaults.py:99-102` mock_create.assert_called_once_with + startswith |
| #3 `tests/unit/test_tools_extended.py:641` 含 `mock_repo.active_branch.name`，但与 state.active_branch 无关（GitTool branch property 的 mock），列入"不动"清单 | ✅ 合理（grep 完整性补充，归类正确） | test/FINAL.md §2.6 grep 复核未覆盖该行，本 v1 显式补全后归类正确 |
| #4 项目根无 CHANGELOG，仅同步 CLAUDE.md；不主动创建 CHANGELOG.md 避免计划外文件 | ✅ 合理（plan §4 "向后兼容"段未强制 CHANGELOG；CLAUDE.md 同步满足"用户可见行为变化需文档化"） | `CLAUDE.md:147` 新增段 |

4 项自纠均不构成架构级偏离，均落入 scope.md §3.1 细节自纠允许范围。

## 副作用检查（git diff `5d659a2..23d159a`）

```
 CLAUDE.md                            |   2 +
 src/cli/commands/setup.py            |  11 ++-
 src/models/config.py                 |   7 +-
 tests/unit/test_working_branch.py    |  22 ++++-
 tests/unit/test_worktree_defaults.py | 167 +++++++++++++++++++++++++++++++++++
 5 files changed, 203 insertions(+), 6 deletions(-)
```

5 个文件全部命中 v1.md 清单 + plan §2 Phase 4 范围。**无 Phase 4 外文件改动**；未引入计划外依赖；未触动 Phase 0/1/2/3 锁定路径。

特别核查 grep 完整性（按 lock #29 / test FINAL §2.6）：

```
$ grep -rn enable_working_branch tests/
  → tests/unit/test_working_branch.py 4 处 + tests/unit/test_worktree_defaults.py 多处（本 v1 新增）
$ grep -rn active_branch tests/
  → tests/unit/test_working_branch.py 6 处 + tests/unit/test_tools_extended.py:641（GitTool mock，无关）+ tests/unit/test_worktree_defaults.py（本 v1 新增）
```

无 Phase 4 应处理但漏改的 hit；v1.md "现有测试梳理"表格 8 行（重命名 1 / 不动 6 / 新增 1）= 完整。

## P0 / P1 / P2 分级问题

无 P0 / P1。

### P2 残留

- **P2-1（Phase 2 滚来）**：`src/agents/base_agent.py` 830 行越 CLAUDE.md "<800" 软约束。本 Phase 不动；保留延后到 Phase 5/6 cache `_cached_call` 接入时一并抽 helper。v1.md "P2 残留"段已显式承认延后。同意。

## Step 3 / 4 — 代码质量 / 安全

- 命名：`ENABLE_WORKING_BRANCH_HINT` 全大写 + 类型注解 `str`，符合 module-level 常量惯例；`_default_config_data` inline 注释说明 *why*（U7 翻转 + 避免 fork_ref HEAD 污染），符合 CLAUDE.md "Comments only when intent is non-obvious"。
- 测试 `_make_repo(tmp_path)` helper 是真实 `git init` + `git commit --allow-empty`，不引入网络 / 大文件；`subprocess.run(... capture_output=True)` 抑制 stdout 噪声；按 plan §4 风险表 "未量化风险" 已 grep 完整复核测试影响面。
- mypy strict 通过；ruff 通过；测试新增 `test_worktree_defaults.py` 全部新代码无 F401 / E402。
- 安全：`_default_config_data` 已存在的 yaml synth 路径，本次仅改一个 bool 字段；不引入 user-controlled 字符串注入面；`ENABLE_WORKING_BRANCH_HINT` 是写死的 const string，无 template injection 风险。
- 注释：description（config.py）+ inline 注释（setup.py）+ docstring（测试文件）三处一致，全部说明 *why*，无简单 *what* 重述。

## 已通过事实（详见 locks/approved-facts.md；本轮新增 4 条，已存档 42 条不重列）

本轮新增（待 SendMessage 通过后追加）：

- **[code-phase-4 #43]** `MergeConfig.enable_working_branch` default 翻转 `False → True`（`src/models/config.py:981-989`），description 显式 "U7: default flipped to True so a half-finished run never pollutes fork_ref HEAD; set to False explicitly to restore the legacy in-place behavior"。Phase 3 出口前 default=False（plan #3 锁定）已成历史。
- **[code-phase-4 #44]** Setup wizard yaml synth 同步：`src/cli/commands/setup.py:244` `enable_working_branch: True`（dict default）+ `:223-226` 新增模块级常量 `ENABLE_WORKING_BRANCH_HINT: str = "推荐：每 run 隔离写入..."`（含 "fork_ref" 关键字守护）。**细节自纠**（lock #29 第 5 行预留）：setup.py 是 yaml synth 写出器（v0.PR-3 已移除 interactive wizard），"复选框"等价为 dict default；description 通过模块级常量暴露给测试断言 + 文档参考。
- **[code-phase-4 #45]** lock #3 锚点区间 `tests/unit/test_working_branch.py:72-83` Phase 4 守护方式：旧 `test_enable_working_branch_defaults_false` 重命名为 `_defaults_true` + 断言迁移 `is False → is True`（重命名 ≠ 删除，符合 lock #29 第 4 行"锚点区间不得被删除"约束）；新增 `test_enable_working_branch_can_be_disabled_with_explicit_false`（backward compat，line 91-101）。`_make_config(enable: bool = False)` helper（line 57-64）默认值未翻转，因为调用方都显式传 True / False 参数，与 schema default 解耦。
- **[code-phase-4 #46]** U-P4.4 / plan §2 P4 列名 #3 真实 Orchestrator 路径 regression net acceptance 锁定：注释 `src/models/config.py:982 default=True → False` → `test_enable_working_branch_defaults_true` + `test_worktree_enabled_by_default_in_new_state` + `test_orchestrator_creates_branch_on_run_when_enabled` 三测全部 FAILED；注释 `src/cli/commands/setup.py:244 yaml default True → False` → `test_default_config_enables_worktree` FAILED。gatekeeper-code 二审端独立 Stage A + Stage B 实测通过。后续 Phase 不得 regression：删除 default=True 而测试仍绿 = test 守护失效，立刻 NO-GO。

> 验证基线刷新（Phase 4 出口）：commit `23d159a` 后 `pytest tests/unit/` = **2361 passed, 1 skipped**（Phase 3 +6）；`mypy src` = 0 error；`ruff check src/` = 0 error；coverage TOTAL = **83.80%**（持平 Phase 3）。本会话累计 Phase 2+3+4 = 8 commit；基线 2330 → 2361 / cov 83.54% → 83.80%。

> 残留 P2（不阻塞 Phase 4 GO；Phase 5/6 处理）：(a) `src/agents/base_agent.py` 830 行越 CLAUDE.md "<800" 软约束（Phase 2 滚来）。

## 修订建议

无。可进入会话收尾。

## 二审及之后

本 Phase 第一次送审，无上轮反馈核查项。
