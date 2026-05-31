# gatekeeper-plan 审查报告（v1）

## 结论

**要求修改（中等）** — P0 2 项、P1 3 项、P2 3 项。整体框架与 facts.md / doc §1-§10 对齐良好，4 个决策点（Q1-Q4）方向均合理；问题集中在 "已知现存测试 / 已知现存调用点" 未在交付物中具名列入，存在 regression 与 O5 覆盖不全的风险。修订聚焦下列 P0/P1 即可通过。

## 已通过事实（本轮新增 0 条；通过时再追加）

（首次审查；本轮新增 0 条。通过审查时由 gatekeeper-plan 将下列已核事实追加到 `locks/approved-facts.md`。）

候选追加事实（待通过时落盘，预计 8 条）：
- `src/models/conflict.py` 当前 ConflictAnalysis 字段 11 项，无 is_chunked / chunk_count（与 facts.md C3 一致，本轮再次确认）
- `src/models/config.py:949-954` max_cost_usd 当前 default=None（与 facts.md I3/G5 一致）
- `src/models/config.py:956-963` enable_working_branch 当前 default=False（与 facts.md I4 一致）
- `src/core/orchestrator.py:262-280` ceiling check 已实装，`AWAITING_HUMAN + "cost_ceiling_halt" checkpoint`（与 facts.md G5 一致）
- `src/core/parallel_file_runner.py` 65 行；`from_api_key_env_list` 在仓库内有 5 个调用点：conflict_analyst.py:81 / executor.py:829 / planner.py:645 / judge.py:167 / judge.py:1473（**facts.md F1 + 新增确认**）
- `split_by_semantic_boundary` 真实定义在 `src/tools/chunk_processor.py`；executor_agent.py:488/491 是**调用点**而非定义点（facts.md D2 措辞需结合此理解）
- `_call_llm_with_retry` 当前不查任何 cost 字段；G4 锁定 base_agent 必须查 `self._cost_tracker.total_cost_usd` 而非 `state.cost_summary`（**plan §3.1 已正确遵守 G4，背离 doc §5.2.2 文本**）
- `src/agents/base_agent.py:147/235` `_current_phase` 是 `str` 类型（与 plan `RunBudgetExceeded(phase=current_phase: str)` 签名兼容）

## P0（必改）

### [P0-1] 现存测试 regression 漏列：`test_max_cost_usd_field_defaults_none`

- **锚点**：`tests/unit/test_telemetry_snapshot.py:123-127`
  ```python
  def test_max_cost_usd_field_defaults_none(self, tmp_path):
      config = ...
      assert config.max_cost_usd is None
  ```
- **问题**：plan §3.1 Q1 决策把 `max_cost_usd` default `None → 5.0`，**会直接 break 上述测试**。Plan §2 Phase 2 交付物列出 5 个新测试 + 3 个 commit，但未提及修改此现存测试；只在风险表内含糊写"Phase 2 开工前 grep `max_cost_usd` 现有测试"。
- **期望**：
  1. Phase 2 交付物中新增第 4 个 commit 项（或合并到 commit #1 `feat(config)`）：明确写"`tests/unit/test_telemetry_snapshot.py:125` 断言改为 `config.max_cost_usd == 5.0`，配套新增 `test_max_cost_usd_can_be_disabled_with_none` 验证 None 仍合法"。
  2. 在 §3.1 决策的"风险与对策"段把这条测试**具名**列入受影响清单（与 Q3 P0-2 同等级处理）。
- **依据**：facts.md K3"现有 2307 测试全套不允许 regression"；CLAUDE.md「coding-style.md / Code Quality Checklist」无 regression。

### [P0-2] U5 disjointness contract 接入点遗漏 2 处现存 `ParallelFileRunner` 调用

- **锚点**：plan §2 Phase 3 "调用点 4 处" vs 仓库实际 `ParallelFileRunner.from_api_key_env_list` 5 个调用点：
  - `src/agents/conflict_analyst_agent.py:81`（**Phase 1 引入之前就存在**，与 plan 描述的 "Phase 1 新增 chunked 路径切 chunks 后" 是不同调用点）
  - `src/agents/executor_agent.py:829` ✓ plan 已覆盖
  - `src/agents/planner_agent.py:645` ✓ plan 已覆盖
  - `src/agents/judge_agent.py:167` — **plan 漏**（high-risk per-file fan-out）
  - `src/agents/judge_agent.py:1473` — **plan 漏**（judge chunk runner）
- **问题**：plan 把 conflict_analyst 仅算作 "Phase 1 引入"，未识别 conflict_analyst.py:81 已存在的 multi-file fan-out；同时 judge_agent 2 处 fan-out 完全未提。validation 验收 O5 "任何重合的 shard → 立刻 raise" 是合约语义（不只是 happy path），漏一个 fan-out 点 = 合约不闭环。
- **期望**：Phase 3 交付物中调用点列表更新为 6 个具名点（5 现存 + 1 Phase 1 新增），或对每个未接入的点显式给出"判定无需校验"的理由（例如 "judge:167 入参是 dict.keys() 天然 disjoint，本合约不接入"——但 L6"显式校验"的目标恰恰要求每个 fan-out 都过校验，倾向于全部接入）。
- **依据**：facts.md L6 "Claude Code 子 agent: 文件 disjoint contract 在 fan-out 前**显式校验**"；plan §1.2 自陈 "U5 给后续 fan-out 保险"。

## P1（应改）

### [P1-1] Phase 4 测试影响清单可在送审时就提供，不必拖到 Phase 4 开工

- **锚点**：`tests/unit/test_working_branch.py:72-83`（`test_enable_working_branch_defaults_false` / `test_enable_working_branch_can_be_set` 显式断言 default=False）；plan §3.3 Q3 决策"Phase 4 第一步执行 grep"。
- **问题**：facts.md Q3 已锁定问题；现在 grep 立刻可得结果（至少 `test_working_branch.py:72-83` 2 处显式断言），plan 完全可以在 v1 就列出影响清单 + 处理建议，让 gatekeeper-plan / verifier 提前评估"测试改动是否合理"，而非延迟到 Phase 4 才暴露。Phase 4 commit message 范本写"适配 worktree 默认开启的 5 处现有测试"——5 是猜测数字。
- **期望**：plan §3.3 增补一段已知影响测试预览（来自现成 grep）；至少 `test_enable_working_branch_defaults_false` 必须显式列入，处理动作 = "断言改 True + 新增 `test_enable_working_branch_can_be_disabled_with_explicit_false`"。Phase 4 commit message 范本里的"5 处"改为"已知 ≥2 处，开工 grep 复核"。

### [P1-2] `split_by_semantic_boundary` 引用位置不准确

- **锚点**：plan §1.1 Phase 1 第二条 "复用 `executor_agent.split_by_semantic_boundary`"；实际定义在 `src/tools/chunk_processor.py`（被 executor_agent.py:479-483 `from src.tools.chunk_processor import ...` 引入）。
- **问题**：facts.md D2 "executor_agent.py:482-491 split_by_semantic_boundary(...) 复用入口"措辞可解释为"调用位置"，plan 把它误读为函数定义在 executor_agent。U1 实现时应直接 `from src.tools.chunk_processor import split_by_semantic_boundary`，避免循环 import 风险（conflict_analyst 反过来 import executor_agent 会造成 agents/ 层内部耦合）。
- **期望**：plan §1.1 Phase 1 第二条改为 "复用 `src/tools/chunk_processor.split_by_semantic_boundary`"；plan §2 Phase 1 同步修订。

### [P1-3] Phase 0 7 yaml 加 version 字段未指明加载器兼容点

- **锚点**：plan §3.2 Q2 决策 "加载器（contract 解析代码）兼容旧 yaml 无 version 字段（缺省视作 0，cache 会 miss 一次以拉新）"；当前仓库的 contract 解析代码位置未在 plan 中给出 file:line。
- **问题**：plan §2 Phase 0 交付物清单只列了 7 个 yaml + 2 个新测试，没有列出"加载器兼容旧 yaml 的代码改动点"。如果加载器不改而 yaml schema 强制要求 version，新加载 + 老 yaml 会爆 ValidationError；要么 7 yaml 全部加 version（plan 已做）+ 加载器仍保留 `version: int = 0` 默认（plan 需明确这一点）。
- **期望**：plan §2 Phase 0 交付物加一条"contract 加载器（grep `_load_contract` / `parse_contract` 定位 file:line）：声明 `version: int = 0` 默认；7 yaml 全显式 version=1 后默认值实际不被消费，仅为防 future yaml 漏写时不崩"。Phase 0 commit 范围相应包含这处加载器改动。

## P2（建议）

### [P2-1] `PerFilePlanEntry` 引入 `datetime` 字段未提示 import 路径

- 锚点：plan §2 Phase 7 第 1 项；`src/models/plan.py` 当前未 import `datetime.datetime`。
- 建议：Phase 7 第 1 项备注 "需新增 `from datetime import datetime` import"，避免 verifier / executor 漏掉。

### [P2-2] `executor_agent.py` 与 `config.py` 已接近或超过文件大小约束

- 锚点：当前 `executor_agent.py` 1026 行（已 > CLAUDE.md "<800" 软约束）；`config.py` 971 行（接近）。
- 建议：plan §4 风险表新增一行 "Phase 1 / Phase 2 / Phase 5 / Phase 6 都将向 executor_agent 或 config.py 追加；若 Phase 5 完成时 config.py 超过 1100 行，将 CacheConfig / RateLimitConfig / ThresholdConfig 拆 `src/models/config_sections/` 子模块"。U1.A 解耦如果在 executor 内只是搬动而非新增，影响可控；但 U5 新 helper 是否 inline 到 executor 也建议提前决策。

### [P2-3] Phase 5 集成测试 fixture 来源未指定

- 锚点：plan §2 Phase 5 GO 条件 "集成测试新增 1 个 fixture repo 二次 run 验证 cache 命中率 ≥ 90%"；facts.md 未给出 fixture repo 路径。
- 建议：plan §2 Phase 5 备注 "fixture 选择策略：复用 `tests/integration/` 现有 fixture，若不存在则 doc §8 提到的 forgejo 子集（约 500 文件）作为新 fixture——决策推迟到 Verifier 设计测试方案时"。

## Phase 拆分合理性核查

| 维度 | 评价 |
|---|---|
| 独立可验收 | ✓ 每 Phase 都有独立 GO 条件 + 独立 commit + 独立 test |
| 估时合理性 | ✓ 合计 9.5 天 vs doc 6 天 = 上调 58%；每 Phase 上调都给具体理由（U1.A 解耦 / web 跨端 / sqlite 调试 / Q3 grep 影响 / 跨端调试） |
| 依赖顺序 | ✓ 符合 facts.md M5 + doc §9；Phase 0 基础设施前置合理（避免 7 yaml 在 Phase 5 才动） |
| 锁清单 regression | N/A 首次审查 |
| commit 数 19 个 | ✓ 平均每 Phase 2-3 commit，conventional commits 格式（facts.md A1+CLAUDE.md git-workflow） |
| 不变量保护 | ✓ 自陈遵守 anti-pattern A5；plan §3.1 主动背离 doc 文本以遵守 facts.md G4 是加分项 |

## 二审及之后：上轮反馈落地核查

| 上轮反馈 | 落地情况 | 是否引入新 regression |
|---|---|---|
| （首次审查，无上轮） | N/A | N/A |
