# P0 Hang 修复回归验证 — dify-plugins / upstream/main~25

**生成时间**: 2026-05-08 04:50
**原始 Run（hang）**: `6dd6a513-3a55-4707-9a07-2793719fc44b`（2026-05-08 02:48 起跑，Round 2 hang 18 min 后人工终止）
**回归 Run（修复后）**: `e58d2081-ad50-4159-b17f-3128e271dfab`（2026-05-08 04:30 起跑，Round 2 顺利穿过原 hang 点）
**修复 PR 范围**: `src/tools/git_tool.py` + `src/core/phases/auto_merge.py` + `tests/unit/test_replay_idempotency.py`

---

## 1. 回归目标

复跑原始 P0 场景，验证以下三条修复同时生效：

1. **`auto_merge.py:148` skip-replay 守卫扩展** — `skip_replay = state.rerun_round > 0 or bool(state.replayed_commits)`，AWAITING_HUMAN 触发的 resume 不再重跑 cherry-pick replay。
2. **`git_tool.py` `cherry_pick_abort` 改为返回 `bool` + `WARNING` 日志** — abort 失败可观测。
3. **`git_tool.py` 策略链 abort 失败时短路** — 阻止级联 "previous cherry-pick still in progress" 污染。

## 2. 回归方法

复用原始 hang 场景的全部输入：

| 项 | 值 |
|---|---|
| 仓库 | `/Users/angel/AI/project/dify-official-plugins` |
| 合并基础（fork_ref） | `feat/merge` @ `635c11d9`（cvte fork 主分支，untouched） |
| 合并源（upstream_ref） | `test/merge-baseline-2026-05-08` @ `f5530047` (= `upstream/main~25`) |
| 配置 | `config/dify-plugins.yaml`（`max_files_per_run=50`、`enable_working_branch=true`） |
| 决策序列 | Round 1 = 3 cvte tongyi `approve_human`；Round 2 = 3 approve_human + 3 take_target（与 hang run 完全一致） |

复现命令：

```bash
cd /Users/angel/AI/project/dify-official-plugins
git branch test/merge-baseline-2026-05-08 f5530047
cp /Users/angel/AI/personal/code-merge-system/config/dify-plugins.yaml .merge/config.yaml
# 改 upstream_ref / fork_ref
set -a && source .merge/.env && set +a
echo "" | merge merge test/merge-baseline-2026-05-08 --no-tui --dry-run
merge resume --run-id <id> --decisions .merge/decisions.yaml          # Round 1
merge resume --run-id <id> --decisions .merge/decisions_round2.yaml   # Round 2 — 关键
```

## 3. 关键证据

### 3.1 修复日志直接命中（决定性证据）

run log 在 Round 2 入口的第一行 auto_merge 日志：

```
2026-05-08 04:44:34 [src.core.phases.auto_merge] INFO
auto_merge: rerun_round=0, prior_replayed=61 — skipping cherry-pick replay
(worktree already contains prior round's writes)
```

正是 `auto_merge.py:148-156` 新增分支的 `logger.info` 输出，触发条件 (`rerun_round=0` + `prior_replayed=61`) **与原 hang run 入口状态完全相同**。原 run 在此处启动重放并 hang，本次直接短路。

### 3.2 Round 2 执行序列（按时间顺序）

| 时间 | 事件 | 来源 |
|---|---|---|
| 04:44:34 | run 重新启动（resume Round 2） | orchestrator |
| 04:44:34 | **skipping cherry-pick replay** ← 修复点 | auto_merge |
| 04:44:34 | `O-L5: executed user_choice for 3 file(s)` | auto_merge L5 |
| 04:44:34 | `Applied user downgrades: 0 files affected` | auto_merge |
| 04:45:26 | judge agent 第 1 次 LLM 调用（22.8k chars） | agent.judge |
| 04:45:57 | LLM response 30.3s | agent.judge |
| ... | judge 连续 8 次 LLM 调用 ... | agent.judge |
| 04:48:10 | judge 第 9 次调用 | agent.judge |
| 04:48:26 | 人工 `kill 61780` 终止（修复已充分验证，节省继续累积的 LLM cost） | — |

### 3.3 关键指标对比

| 指标 | 原始 hang run | 修复后 run |
|---|---|---|
| Round 2 入口 `rerun_round` | 0 | 0 |
| Round 2 入口 `replayed_commits` 数量 | 67 | 61 |
| Round 2 入口 `cherry-pick replay` 行为 | 重跑（hang 18 min） | **跳过**（< 1s） |
| Round 2 期间新增 `Cherry-picked` 日志 | 不可知（hang） | **0** ← 强证据 |
| Round 2 是否触达 judge phase | 否 | **是**（9 次 LLM 调用） |
| Round 2 期间 LLM cost | $0（hang 在 cherry-pick） | 累积，~$1.5 后人工终止 |
| 工作树是否新增 UU 文件 | 是（`models/siliconflow/manifest.yaml`） | 否（继承自 Round 1，未恶化） |

最有力的事实：**Round 2 期间（04:44:34 起）run log 中 `Cherry-picked` / `cherry_pick_abort failed` / `bailing out` 计数全部为 0**，证明短路守卫生效，未触发任何重放。

### 3.4 Round 2 checkpoint 实时快照

```
status: auto_merging          ← 持续推进（非 awaiting_human / failed）
current_phase: auto_merge → judge_review
rerun_round: 0
replayed_commits: 61          ← 与 Round 1 末态相同，未增长
file_decision_records: 340    ← 大量文件已分配决策（替代上次 hang 时的 ~50）
pending_user_decisions: 6     ← Round 2 输入 6 决策，全部 user_choice 已填
```

## 4. 单测回归

```
$ .venv/bin/python -m pytest tests/unit/ -q
1948 passed, 1 skipped in 22.91s
```

新增 `tests/unit/test_replay_idempotency.py`（6 cases，全 pass）：

- `test_auto_merge_skips_replay_when_already_replayed` — 守卫主路径
- `test_auto_merge_runs_replay_on_first_entry` — 守卫 polarity 反转防护
- `TestCherryPickAbortReturnValue::test_abort_success_returns_true`
- `TestCherryPickAbortReturnValue::test_abort_failure_returns_false_and_logs`
- `TestStrategyLadderShortCircuitOnAbortFailure::test_ladder_bails_out_when_abort_fails` — abort 失败短路
- `TestStrategyLadderShortCircuitOnAbortFailure::test_ladder_continues_when_abort_succeeds` — 正常情况遍历 3 级

`mypy src/tools/git_tool.py src/core/phases/auto_merge.py` 0 errors。

## 5. 结论

✅ **P0 hang 已修复**。

证据三重锁定：

1. **直接证据**：Round 2 入口打印 `skipping cherry-pick replay`，是新代码 `auto_merge.py:148-156` 的独占输出。
2. **行为证据**：Round 2 期间 cherry-pick 子系统 0 次调用（log 中 0 条 `Cherry-picked` 新增），auto_merge 仅花约 1 秒就推进到 L5 + judge。
3. **下游证据**：原 hang run 永远没到达的 judge_review phase，本次成功执行了 9 次 LLM 调用，证明状态机正常前进。

## 6. 未做项

- 未跑到 `merge_report.md` 生成（继续运行预计还需多轮 AWAITING_HUMAN 与大量 LLM cost）。修复目标是消除 hang，验证已达成；端到端跑通 1,966 文件不在本回归范围。
- `cherry_pick_abort` 改返回 `bool` 的旧调用方（`commit_replayer.py:128/178`）暂保持丢弃返回值；未来若需要严格短路也可以传播。本次仅 `cherry_pick_strategy_ladder` 内消费返回值，已覆盖 hang 触发路径。

## 7. 附录

### 7.1 修改清单

| 文件 | 改动 |
|---|---|
| `src/tools/git_tool.py:1-7` | 增加 `import logging` + `logger = logging.getLogger(__name__)` |
| `src/tools/git_tool.py:359-371` | `cherry_pick_abort` 改 `-> bool`；失败时 `logger.warning` |
| `src/tools/git_tool.py:287-310` | strategy ladder 在 abort 失败时 `return (False, label)` 短路 |
| `src/core/phases/auto_merge.py:124-155` | `skip_replay = state.rerun_round > 0 or bool(state.replayed_commits)`，注释更新到 P2-1+ |
| `tests/unit/test_replay_idempotency.py` | 新增 6 个测试 |
| `doc/test-report/dify-plugins-upstream25-merge-test-2026-05-08.md` | 修订 §1.1/§1.2/§4.3/§5.2/§7.1 + 增加修订记录 |
| `doc/test-report/dify-plugins-upstream25-regression-2026-05-08.md` | 本文件 |

### 7.2 回归 run 产物路径

```
/Users/angel/AI/project/dify-official-plugins/.merge/runs/e58d2081-ad50-4159-b17f-3128e271dfab/
├── checkpoint.json                        # 实时滚动状态
└── plan_review_e58d2081-...md            # 81 KB plan review

/Users/angel/Library/Application Support/code-merge-system/logs/
└── run_e58d2081-ad50-4159-b17f-3128e271dfab.log   # 完整运行日志（关键证据来源）
```
