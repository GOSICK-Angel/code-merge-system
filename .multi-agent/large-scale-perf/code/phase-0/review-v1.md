# gatekeeper-code 审查报告（Phase 0 v1）

审查者：gatekeeper-code
审查时间：2026-05-18
被审 artifact：`.multi-agent/large-scale-perf/code/phase-0/v1.md`
被审 commit：`aa540d2877e9e8e8a6d7bfc04f140f5eeef9af77`
对照：`plan/FINAL.md` §2 Phase 0 + `test/FINAL.md` §2.1 + `locks/approved-facts.md` plan #7 / #8 / test #13-17 / facts.md A3+A5+G3+G4+I3+Q2

## 结论

**要求修改**（P1 ×1，P2 ×1；功能 / mypy / pytest / 覆盖率全部通过；唯一阻塞是新增 2 处 ruff E402 — 见下方修订指引）。

## 契约核查表

| Planner Phase 0 契约 | 实施位置 | 状态 | 锚点 |
|---|---|---|---|
| `RunBudgetExceeded(Exception)` 仅定义未接线，签名 `(spent: float, limit: float, phase: str)` | `src/models/state.py:38-52` | ✅ | grep 仓库 `raise RunBudgetExceeded` 仅命中测试 (`tests/unit/test_run_budget_exceeded_dataclass.py:41`)；prod 代码 0 处 raise — 未接线条件成立 |
| `AgentContract.version: int = Field(default=0, ge=0, description=...)` | `src/agents/contract.py:30-39` | ✅ | description 包含 plan 要求的"Default 0 allows future yaml omissions" 关键语义 |
| 7 contract yaml 顶层 `version: 1` 全 7 文件 | `src/agents/contracts/{7 个文件}.yaml:2` | ✅ | 实地核对 7 个；无遗漏，名称集合 = facts.md A3 锁定的 7 个 stem |
| `_schema.md` 新增 Versioning 段（含 bump 触发条件） | `src/agents/contracts/_schema.md:51-77` | ✅ | 含 3 条 bump 条件（prompt 内容 / aggregation 规则 / I/O schema）+ "不 bump" 例外清单，与 plan §3 Q2 一致 |
| 单测 `test_run_budget_exceeded_dataclass.py` 覆盖 U-P0.1 (a)(b)(c)(d) | `tests/unit/test_run_budget_exceeded_dataclass.py` 5 函数 | ✅ | 拆 5 个独立函数实现 U-P0.1 全部子断言，符合 scope.md §3.1 允许的"细节自纠：同语义更细粒度" |
| 单测 `test_agent_contracts.py` 扩展 U-P0.2~U-P0.7 | `tests/unit/test_agent_contracts.py:275-350` | ✅ | 6 个新用例全部映射 test/FINAL.md U-P0.2~U-P0.7；锚点 `Path(__file__).resolve().parents[2]` 符合 P1-3 修订要求 + `assert len(yaml_files) == 7` sanity gate 落地 |
| GO 条件 G0-1 ~ G0-4 | 见下"测试结果" | ⚠ G0-3 部分不达标 | 见 P1 |

## 测试结果

- `pytest tests/unit/test_agent_contracts.py tests/unit/test_run_budget_exceeded_dataclass.py -v`：**42/42 通过**（含 Phase 0 新增 11：U-P0.1 拆 5 + U-P0.2~U-P0.7 共 6）
- `pytest tests/unit/ -q`：**2318 passed, 1 skipped**（基线 2307，新增 11，0 regression）
- `mypy src`：**Success: no issues found in 160 source files**（0 error）
- `ruff check src/`：**All checks passed!**（0 error）
- `ruff check tests/unit/test_agent_contracts.py tests/unit/test_run_budget_exceeded_dataclass.py`：⚠ **2 新 E402 错误 + 1 预存 F401**（详见 P1）
- 覆盖率：本地复现 `pytest tests/unit/ --cov=src --cov-report=term` → **TOTAL 83.25%**（与 executor 报告 83.28% 有 ~0.03pp 微差，属正常波动；覆盖率门槛 80% 远超达标）
- 提交范围 `git diff 4826a6e..aa540d2 --stat`：**12 文件 +201 行**，全部在 Phase 0 plan §2 交付清单内；无范围外侧改

## 已通过事实（详见 `.multi-agent/large-scale-perf/locks/approved-facts.md`）

本轮**暂不**追加锁清单事实（待 v2 修订 P1 后通过审查再追加）。

跨阶段已存档事实仍生效：plan 阶段 #1-12 + test 阶段 #13-17 共 17 条不重列。

## P0 / P1 / P2 分级问题

### P0
无。功能 / 类型 / 测试 / 安全全部达标。

### P1（1 项 — 阻塞 GO）

**P1-1：tests/unit/test_agent_contracts.py 新增 2 处 ruff E402（module-level import not at top of file）**

- 锚点：`tests/unit/test_agent_contracts.py:40-41`
  ```python
  from src.core.read_only_state_view import ReadOnlyStateView
  from src.llm.prompts.gate_registry import get_gate, registered_gate_ids
  ```
- 根因：executor 把新增的 `CONTRACTS_DIR` 与 `EXPECTED_CONTRACT_STEMS` 常量插入到 `from src.agents.contract import (...)` 与 `from src.core.read_only_state_view import ...` 之间，导致后两条 import 不再"位于文件顶部"。
- 基线对照：commit 4826a6e 同文件 `ruff check` **只有 1 个预存 F401**（与本次改动无关）；本 commit 把错误总数从 1 增加到 3 — 净新增 2 处 E402。
- 为何 executor 漏报：报告里只跑了 `ruff check src/`（CLAUDE.md 默认 lint scope），未对改动到的测试文件执行 ruff；GO 条件 G0-3 字面写 `ruff check src/` 也确实是 src/ 范围。但本仓 CLAUDE.md「Code Quality Checklist」与 plan §11.1 都把"无新增 ruff 错误"作为隐含基线 — 改 tests/ 同样不得**净新增** lint 错误（regression test 范畴）。
- 修订指引（最小侵入）：把 `CONTRACTS_DIR` 与 `EXPECTED_CONTRACT_STEMS` 这两个常量**整体下移**到 `from src.llm.prompts.gate_registry import get_gate, registered_gate_ids` 之后（即把 30-39 行的 5 行常量定义挪到当前第 41 行之后）。语义零变化，新增 ruff 错误归 0。
- 验证命令：`ruff check tests/unit/test_agent_contracts.py` 应回到基线 1 error（预存 F401，与本 Phase 无关）。

### P2（1 项 — 不阻塞，建议但非强制）

**P2-1：v1.md baseline 段记录覆盖率 83.28%，本地复现为 83.25%**

- 差值 0.03pp，远在测试方案 §6.2 ±0.5pp 容差内，非 regression。
- 可能源于：(a) executor 用 `--cov-report=json` 与本地 `--cov-report=term` 的浮点取整差；(b) 测试 collection 时的随机化（pytest-asyncio 未固定 seed）。
- 建议 v2 在 baseline 段加注 "复测时可能 ±0.05pp 浮动"，避免后续 Phase 给出"覆盖率倒退"的伪报警。**不阻塞 Phase 0 GO**。

## 残留风险（如有放行）

P1 修订后建议放行的残留：
- 修订 v2 应仅触及 `tests/unit/test_agent_contracts.py` 第 30-41 行；其他文件零改动 — 避免引入新副作用。

## 二审之后核查表
- ☐ P1-1 已落地：`ruff check tests/unit/test_agent_contracts.py` 错误数回到基线 1（仅预存 F401）。
- ☐ pytest 仍 42/42 + 全套 2318 绿。
- ☐ mypy + `ruff check src/` 仍清零。
- ☐ 覆盖率 ≥ 83.20%（基线漂移容差内）。

修订 v2 收到后我重审上述 4 项即给 GO。
