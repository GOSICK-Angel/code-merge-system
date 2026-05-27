# Phase 0 实施报告 v2（修订）

## 修订基线

- 上一版：v1（commit `aa540d2`，gatekeeper-code review-v1.md）
- 反馈：P1-1（ruff E402 module-level import not at top of file，2 处） + P2-1（覆盖率容差说明，不阻塞）

## 修订动作

| 反馈项 | 严重度 | 处理 | 锚点 |
|---|---|---|---|
| P1-1 | P1（阻塞） | 把 `CONTRACTS_DIR` / `EXPECTED_CONTRACT_STEMS` 5 行常量定义整体下移到所有 `import` 之后 | `tests/unit/test_agent_contracts.py:18-37`（修订后） |
| P2-1 | P2（不阻塞） | 覆盖率 0.03pp 浮动属正常，本 v2 加注容差说明（test/FINAL.md §6.2 已定义 ±0.5pp 容差，本次 +0.03pp 远在容差内） | 本报告"测试结果"段 |

## 新增 commit

- `1a40958ea2a414d8c980e08c7d76990210931df7` — fix(test): 修复 ruff E402 — 常量定义移至 import 块之后
- 不 amend 已审 `aa540d2`（按 team-lead 协议"修订只能加 fixup commit 或后续 commit"）

## 修订文件清单

- `tests/unit/test_agent_contracts.py` (+2 -2) — 仅常量块位置调整，语义零变化

## 测试结果

- `pytest tests/unit/test_agent_contracts.py tests/unit/test_run_budget_exceeded_dataclass.py -q`：**42/42 通过**
- `pytest tests/unit/ -q`：**2318 passed, 1 skipped**
- `mypy src`：**Success: no issues found in 160 source files**
- `ruff check src/`：**All checks passed!**（0 error）
- `ruff check tests/unit/test_agent_contracts.py`：**回到基线 1 error**（pre-existing F401 `contract_path` 未使用，与本 Phase 无关；non-blocking per gatekeeper review）
- 覆盖率：**83.28%**（基线 83.25%；漂移 +0.03pp，远在 test/FINAL.md §6.2 门槛 2 定义的 ±0.5pp 容差内；门槛 1 ≥80% 达标）

## GO 条件二审核查表（对应 review-v1.md "二审之后核查表" 4 项）

1. `tests/unit/test_agent_contracts.py` 中 5 行常量定义位于第一段 `from src.... import ...` 之后：✅（现位于 `:18-37`，所有 `from src...` import 在 `:10-19`）
2. `ruff check tests/unit/test_agent_contracts.py`：✅ 回到基线 1 error（F401 contract_path 未使用，与本 Phase 无关）
3. `ruff check src/`：✅ 0 error 维持
4. `pytest tests/unit/test_agent_contracts.py tests/unit/test_run_budget_exceeded_dataclass.py -v`：✅ 42 passed

## 与 v1 报告的契约对齐与 GO 核查

未变化项不再重列；v1.md「契约对齐」「Test/FINAL.md U-P0.* 用例覆盖追踪」「计划细节自纠」「Phase 1 续接锚点」全部继承有效。

## Phase 1 续接锚点

无变化（同 v1.md 末段）。
