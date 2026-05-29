# Legacy `state._merge_base` 残留清理

**发现于** 2026-05-28 zod E2E（追查 conflict_analyst "No actual diff content available" 根因时连带挖出）
**当前状态** 1/4 已修（feat/web `50b6471`），其余 3 处待本 PR 处理

## 背景

`MergeState` 现代 schema 的合并基线字段是 `merge_base_commit: str`（`src/models/state.py:130`）。
代码中残留对**旧时代私有属性 `state._merge_base`** 的依赖：
- pydantic v2 允许任意属性赋值但不变成 model 字段
- 生产代码从不设 `state._merge_base`
- 但 5 处源码仍用 `hasattr(state, "_merge_base")` 门控逻辑
- → 这些分支生产里**永远 False**，对应路径死亡 / 静默退化

zod 案例：`conflict_analysis.py:797` 让 per-file 路径每次都拿到 `base/current/target=None`，
LLM 看到 "Not available" 直接放弃分析，产 "No actual diff content available" 类污染 rationale。

## 残留点清单

| # | 文件:行 | 类型 | 影响 | 修法 |
|---|---|---|---|---|
| 1 | `src/core/phases/conflict_analysis.py:797` | 真 bug | per-file 路径取不到三方内容 | ✅ 已修 `50b6471`，改用 `state.merge_base_commit` |
| 2 | **`src/agents/planner_agent.py:1080-1081`** | **真 bug，未修** | `_build_merge_plan` 总返回 `merge_base_commit=""`；触发条件：legacy 非 layered 路径 + `revise_plan`（plan 修订时） | 改用 `state.merge_base_commit` |
| 3 | `src/agents/planner_agent.py:325-326` | 死分支 | LHS `state.merge_base_commit` 已正常，hasattr fallback 永远 False | 删 dead branch |
| 4 | `src/agents/conflict_analyst_agent.py:125-128` | 死方法 | 整个 `ConflictAnalystAgent.run()` 生产从未被调用（phase 直接调 `analyze_file` / `analyze_commit_round`）；method body 含同款 hasattr 误用 | 删整个 `run()` 方法 + 相关 import |
| 5 | `tests/unit/test_agents_extended.py:208` | 在测死代码 | `state._merge_base = "abc123"` 设私有属性，让 hasattr 返回 True，测的就是 #4 死方法 | 若 #4 删则同步删测；否则改用 `state.merge_base_commit` |

## 修复路线（两层 commit）

### Commit 1 — 功能修复

**目标**：`planner_agent._build_merge_plan` (line 1079-1081) 输出非空 `merge_base_commit`

**TDD 切片**：
- RED：新测 `tests/unit/test_planner_revise_plan_merge_base.py`，构造 `state.merge_base_commit="abc123"` + 触发 `revise_plan` 路径，断言返回 plan 的 `merge_base_commit == "abc123"`
- GREEN：把 line 1080-1081 替为 `merge_base = state.merge_base_commit`

**Risk**：低（只改一处常量赋值；功能补救而非行为改变）

**回归覆盖**：跑 `pytest tests/unit/test_planner*.py` 全绿 + 在 zod 上重跑 merge 确认 MergePlan.merge_base_commit 非空（zod 已经走 layered 路径，所以 zod 不能直接验证 _build_merge_plan；可考虑 legacy mode 跑一个测试 target）

### Commit 2 — 死代码清理

**目标**：清掉所有 hasattr-on-_merge_base 调用 + 对应过时测试

具体动作：
1. 删 `src/agents/conflict_analyst_agent.py:125-128` 整段，或更激进地删整个 `async def run` 方法（line 87-160ish）。先用 `grep "conflict_analyst.run\|conflict_analyst_agent.run\|.agents\[\"conflict_analyst\"\].run"` 确认生产无调用
2. 删 `src/agents/planner_agent.py:325-326` 两行
3. 同步删 `tests/unit/test_agents_extended.py:206-...` 整个 `test_run_uses_git_tool_for_three_way_diff` 测试（如已删 `run()`）

**Risk**：中（删除代码，必须先 grep 确认无生产引用）

**验证**：
- mypy + ruff
- 跑 `pytest tests/unit/` 全套，确认无失败（除非被删的那一个 stale 测）
- 跑 zod E2E 一次 sanity（不应有任何行为变化）

## 验收

- [ ] `grep -rn "state\._merge_base\|hasattr(state, \"_merge_base\")\|hasattr(view, \"_merge_base\")" src/` 返回空
- [ ] mypy / ruff / pytest 全绿
- [ ] zod merge --ci 与本次基线对照 rationale 质量不退化

## 上下文链接

- 触发分析 run：zod `5182ef61-06bd-4997-8ee3-965045976384`（已删 .merge/runs/* 后丢失，但 checkpoint 记录见对话 2026-05-28）
- 关联 commit：`50b6471 fix: prompt 注入真实 native 3-way outcome + 修复 per-file 路径取不到内容`
- 关联 grep：`grep -rn 'hasattr(state\|state\._[a-z]\|getattr(state,\s*"_' src/`
