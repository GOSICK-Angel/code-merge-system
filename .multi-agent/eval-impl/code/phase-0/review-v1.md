# gatekeeper-code 审查报告（Phase 0 v1）

> 审查者：gatekeeper-code（team eval-impl）
> 日期：2026-05-15
> 送审 commit：`5658e7745cd4e54664444a20c6e8b67c13fe6df3`
> 实施报告：`.multi-agent/eval-impl/code/phase-0/v1.md`
> 计划基准：`.multi-agent/eval-impl/plan/FINAL.md` §Phase 0
> 锁清单基准：`.multi-agent/eval-impl/locks/approved-facts.md`（[bootstrap] + [plan]）

---

## 结论

**通过**

Phase 0 全部 GO 条件命中、范围严格、锁清单零违反、计划 2 项细节自纠（行数、模型数）理由充分且不动契约。test FINAL 尚未完成（gatekeeper-test 仍在审 v2），但 Phase 0 单测全部内化在本提交内、自洽 self-contained，不阻塞通过。

---

## 契约核查表

| 契约（plan FINAL §Phase 0 GO 条件） | 状态 | 锚点 |
|---|---|---|
| `pytest tests/eval/unit/test_{common,schemas,fork_name_check}.py` 通过 | PASS | 复跑 54/54 PASSED |
| `mypy scripts tests/eval`（strict）通过 | PASS | `Success: no issues found in 11 source files` |
| `ruff check scripts tests/eval` 通过 | PASS | `All checks passed!` |
| `ruff format --check scripts tests/eval` 通过 | PASS | `11 files already formatted` |
| `python -m scripts.eval._fork_name_check scripts/eval tests/eval` exit 0 | PASS | exit=0 |
| `eval_subprocess_env()` 在真实 `os.environ["MERGE_DEV"]="1"` 时仍 pop（P1-3） | PASS | `tests/eval/unit/test_common.py:29-34` + 实现 `_common.py:65-66` |
| `_common.py` 含 `eval_subprocess_env()` 工厂 + 强制 `env.pop("MERGE_DEV", None)`（P0-2） | PASS | `scripts/eval/_common.py:40-70` |
| `_schemas.py` pydantic v2 模型：DiffEntry / DiffReport / GateResult / RunMeta | PASS | `_schemas.py:72,110,150,194` |
| `__init__.py` docstring 注明骨架免责（P0-6） | PASS | `scripts/eval/__init__.py:6-10` |
| `conftest.py` autouse `monkeypatch.delenv("MERGE_DEV", raising=False)` | PASS | `tests/eval/conftest.py:21-31` |
| `tests/eval/datasets/{tier1,tier2,tier3}/`、`manifests/` 骨架 | PASS | `git diff --name-only HEAD~1 HEAD` 列出 5 个 .gitkeep |

11/11 全绿。

---

## 测试结果

- **pytest**：`tests/eval/unit/` 54/54 PASSED（in 0.14s）
- **覆盖率**：`--cov=scripts.eval` 总 95.58%（≥ 80%）
  - `_schemas.py` 100%
  - `_common.py` 94%（未覆盖：`_atomic_write_text` 异常清理 tmp 路径，140-142）
  - `_fork_name_check.py` 90%（未覆盖：whitelist 异常分支 + 不可读文件分支 + `__main__`）
  - `__init__.py` 100%
- **mypy**：`scripts tests/eval` strict, 11 files, 0 error
- **ruff check**：All checks passed
- **ruff format --check**：11 files already formatted
- **fork-name-check**：`python -m scripts.eval._fork_name_check scripts/eval tests/eval` exit 0

---

## 已通过事实（详见 locks/approved-facts.md，本轮新增 5 条带 [code-phase-0] 标签）

详见锁清单追加段落。

---

## P0 / P1 / P2 分级问题

### P0（必须修复才通过）
无。

### P1（强烈建议修复）
无。

### P2（可选优化，不阻塞）

1. **`tests/eval/conftest.py:46-64` `eval_subprocess_env_factory` fixture 未被任何测试调用**
   - 现状：fixture 已定义，参数 `monkeypatch` 仅靠 `del monkeypatch` 规避 unused-arg lint，未来调用者可直接 import `eval_subprocess_env`，fixture 价值有限。
   - 建议：Phase 1+ 若仍无 caller，可删；目前作为 plan §"Phase 0 GO 条件"附属物保留无害。
   - 锚点：`tests/eval/conftest.py:46-64`

2. **`tests/eval/conftest.py:34-43` `tmp_workdir` 与内置 `tmp_path` 等价（仅 `mkdir` 一个子目录）**
   - 现状：当前 Phase 0 单测无人使用；plan §"Phase 1 续接锚点"承诺 lock.py 测试会用。
   - 建议：保留，Phase 1 实施时确认实际调用即可；若 Phase 1 也未用则按 dead-code 清理。
   - 锚点：`tests/eval/conftest.py:34-43`

3. **`scripts/eval/_fork_name_check.py:43-51` SELF_BASENAMES 含 `test_fork_name_check.py`**
   - 现状：必要——单测 fixture 必须含 forbidden token；通过 basename 白名单（不是绝对路径）支持 tmp 拷贝场景，设计正确。
   - 风险：未来若有人在 `scripts/eval/` 下新建文件名同样叫 `_fork_name_check.py` 的辅助模块（极低概率）会被误跳过。
   - 处置：可接受；若 Phase 9 CI 接入后想更严，可改成 "basename + 父目录路径" 联合判定。
   - 锚点：`scripts/eval/_fork_name_check.py:43-51` + `tests/eval/unit/test_fork_name_check.py:98-109`

---

## 残留风险

| ID | 风险 | 影响 | 处置 |
|---|---|---|---|
| RR1 | test FINAL 尚未通过（gatekeeper-test 在审 v2） | 后续 Phase 单测设计若与本 Phase 自带单测风格冲突，可能要回头调整 | Phase 0 自带 54 用例自洽，不依赖外部 test FINAL；Phase 1+ 单测设计参照 test FINAL 通过版即可，本 Phase 无 regression 风险 |
| RR2 | `_common.atomic_write_text` 异常清理路径未覆盖（140-142） | 异常分支真实场景极少（同盘 tmp + os.replace 几乎不会失败）；总覆盖 95.58% 已远超 80% 阈值 | 接受；Phase 1 lock.py 写入大量文件时若需可补 |
| RR3 | Phase 0 未做端到端 subprocess 烟测（与 plan 一致——Phase 3 才有 fake `merge-bin`） | 隔离机制在真实 subprocess 下的行为待 Phase 3 验证 | 计划本身是这样设计的；`eval_subprocess_env` 单元测已断言 `os.environ["MERGE_DEV"]="1"` 场景下被 pop（test_common.py:29-34）足以证明工厂函数行为正确 |

---

## 副作用核查

`git diff --name-only HEAD~1 HEAD` 列出 17 个文件，全部位于：

- `scripts/__init__.py` + `scripts/eval/*.py`（4 文件）
- `tests/eval/__init__.py` + `tests/eval/conftest.py` + `tests/eval/unit/*.py`（5 文件）
- `tests/eval/{datasets/{tier1,tier2,tier3},manifests,fixtures,integration}/.gitkeep`（7 文件）

**未触碰**：`src/` / `doc/evaluation/` / `.multi-agent/` / `pyproject.toml` / `.github/workflows/`。
**未引入新运行时依赖**：仅用 stdlib + 已有 pydantic v2 + pytest（pyproject 未变）。

合规。

---

## 计划自纠评估

| 自纠项 | 评估 | 接受度 |
|---|---|---|
| `_fork_name_check.py` 80→179 行 | word-boundary 正则 + 路径白名单 + self-basename 白名单 + argparse + 行号定位均为 plan P1-4 隐含必需，80 行只够 grep-style；功能未越权 | **接受** |
| `_schemas.py` 4→11 模型 | 新增 7 个模型全部源自 plan §0（`AcceptanceThresholds` 来自决策 7）+ procedure.md §3.2/§3.3（嵌套 SystemDecision/DiffReportMeta）+ Phase 1 lock.py 必需（TierManifest/ManifestEntry）；303 行仍在 ≤ 400 上限内；无越权 | **接受** |

---

## 二审及之后

本轮为首审，无上轮反馈核查项。

---

## 通过决定

✅ Phase 0 v1 通过审查。
- copy `v1.md` 到 `code/phase-0/FINAL.md`
- 追加 5 条新事实到锁清单（带 `[code-phase-0]` 标签）
- 通知 executor + team-lead
