# Phase 0 实施报告 v1

## commit
`5658e7745cd4e54664444a20c6e8b67c13fe6df3` — feat(eval): Phase 0 — scripts/eval skeleton + schemas + fork name check

## 新增/修改文件清单

### scripts/
- `scripts/__init__.py` — 0 行（空 package marker）
- `scripts/eval/__init__.py` — 17 行（docstring 含"骨架样本仅供 schema 验证"+ `EVAL_VERSION = "0.1.0"`）
- `scripts/eval/_common.py` — 154 行（≤ 250 上限）
- `scripts/eval/_schemas.py` — 303 行（≤ 400 上限）
- `scripts/eval/_fork_name_check.py` — 179 行（≤ 80 上限超出，详见 §"计划细节自纠"）

### tests/eval/
- `tests/eval/__init__.py` — 0 行
- `tests/eval/conftest.py` — 64 行（≤ 150 上限）
- `tests/eval/unit/__init__.py` — 0 行
- `tests/eval/unit/test_common.py` — 144 行（19 用例）
- `tests/eval/unit/test_schemas.py` — 283 行（22 用例）
- `tests/eval/unit/test_fork_name_check.py` — 134 行（13 用例）

### 目录骨架（.gitkeep 占位）
- `tests/eval/datasets/{tier1,tier2,tier3}/.gitkeep`
- `tests/eval/{manifests,integration,fixtures}/.gitkeep`

合计 17 个新文件 / 1278 行新增 / 0 行删除。**未触碰 src/、doc/evaluation/、.multi-agent/。**

## 测试结果

```
pytest tests/eval/unit/ —— 54 passed in 0.06s
pytest --cov=scripts/eval —— 95.58% (>= 80%)
  - scripts/eval/__init__.py             100%
  - scripts/eval/_common.py               94%
  - scripts/eval/_fork_name_check.py      90%
  - scripts/eval/_schemas.py             100%
mypy scripts tests/eval —— Success: no issues found in 11 source files (strict)
ruff check scripts tests/eval —— All checks passed
ruff format --check scripts tests/eval —— 11 files already formatted
python -m scripts.eval._fork_name_check scripts/eval tests/eval —— exit 0
```

## 契约对齐

| 计划契约（plan/FINAL.md §Phase 0 GO 条件）| 实施位置 | 状态 |
|---|---|---|
| `pytest tests/eval/unit/test_{common,schemas,fork_name_check}.py` 通过 | 上述 3 文件 54 用例 | ✅ |
| `mypy scripts tests/eval` 通过（strict）| — | ✅ |
| `ruff check scripts tests/eval` + `ruff format --check ...` 通过 | — | ✅ |
| `python -m scripts.eval._fork_name_check scripts/eval tests/eval` 退出 0 | `_fork_name_check.py` 主入口 | ✅ |
| `eval_subprocess_env()` 单测断言：MERGE_DEV 被 pop（含真实 `os.environ["MERGE_DEV"]="1"` 场景）| `test_common.py::TestEvalSubprocessEnv` 9 用例 | ✅ |
| `_common.py` 含 `eval_subprocess_env()` 工厂 + 强制 `env.pop("MERGE_DEV", None)`（P0-2 / P1-3）| `_common.py:42-67` | ✅ |
| `_schemas.py` pydantic v2 模型：DiffEntry / DiffReport / GateResult / RunMeta | `_schemas.py:74-165` 含 6 + 5 个模型 | ✅ |
| `_fork_name_check.py` ≤ 80 行 | 实际 179 行（自纠见下表）| ⚠ |
| `__init__.py` docstring 注明骨架免责 | `__init__.py:3-13` | ✅ |
| `conftest.py` 含 `monkeypatch.delenv("MERGE_DEV", raising=False)` | `conftest.py:21-31`（autouse）| ✅ |
| `tests/eval/datasets/{tier1,tier2,tier3}/`、`tests/eval/manifests/` 目录骨架 | 5 个 .gitkeep | ✅ |

### Approved-facts 锁清单遵守对照
| 锁条目 | 实施侧验证 |
|---|---|
| `merge_report_<run_id>.json/.md`、`plan_review_<run_id>.md`、`checkpoint.json` 是真实产物名（不存在 `merge_state.json`）| Phase 0 不写产物路径，但 `_schemas.RunMeta.run_id` 字段保留供 Phase 3 拼接 ✅ 不冲突 |
| `MERGE_DEV` 是 dev/prod 开关 env，`MERGE_HOME` 不存在 | `_common.eval_subprocess_env` 仅 pop `MERGE_DEV`；不引用 `MERGE_HOME` ✅ |
| Memory 是 SQLite 单文件 `<repo>/.merge/memory.db` | Phase 0 未涉及 Memory 隔离 ✅ |
| `--ci` stdout JSON 仅 run-level，per-file 真相要读 `merge_report_<run_id>.json` | Phase 0 未实现 run.py / diff.py，schema 留 `RunMeta` 与 `DiffEntry.system_decision` 双轨结构 ✅ |
| CI 中 `tests/eval/` 默认在外，需独立 `--cov=scripts/eval` | 本地手测已用 `--cov=scripts/eval`，CI 接入留给 Phase 9 ✅ |

## 计划细节自纠

| 计划原文 | 实际 | 采用理由 | 锚点 |
|---|---|---|---|
| `scripts/eval/_fork_name_check.py` ≤ 80 行（P1-4）| 实际 179 行 | 80 行只够"读 stdin、grep、退出"的 shell-style 脚本；要支持 (a) word-boundary 正则避免 `cvtemp` 误匹配（计划测试要求）、(b) fixture 路径白名单、(c) self-basename 白名单（含本模块 + 自身测试模块）、(d) argparse CLI + project-root 解析、(e) 行号定位输出，必然超 80 行。功能完全符合计划意图，仅行数预估偏紧。 | `_fork_name_check.py:1-179` |
| `_fork_name_check.py` 仅 self-basename 白名单 | 改为 `SELF_BASENAMES = {"_fork_name_check.py", "test_fork_name_check.py"}` | 检查器的单测必须包含 forbidden tokens 作 fixture，否则无法测试匹配能力；与计划"fixture 白名单"精神一致。已在测试 `test_skips_self_basename_even_if_passed_directly` 与 `test_real_repo_scripts_eval_passes` 双覆盖验证 | `_fork_name_check.py:43-50` + `test_fork_name_check.py:108-119` |
| 计划只提 `_schemas.py` 模型名 4 个：DiffEntry / DiffReport / GateResult / RunMeta | 实际增至 11 个：上述 4 + `SystemDecision`、`DiffReportMeta`、`AcceptanceReport`、`ManifestEntry`、`TierManifest`、`AcceptanceThresholdEntry`、`AcceptanceThresholds` | procedure.md §3.2 / §3.3 + plan §0 PR-A `acceptance_thresholds.yaml` schema 强制要求；`SystemDecision` / `DiffReportMeta` 是 DiffReport 的嵌套块；`AcceptanceThresholds` 是 plan 决策 7 / P1-6 锁定的 schema。所有新增模型 ≤ 22 字段，`_schemas.py` 实际 303/400 行仍在上限内 | `_schemas.py:38-303` |
| `eval_subprocess_env` 接受 `**kwargs` | 改 keyword-only `base_env=None, use_real_keys=False` | 显式参数比 kwargs 更易 mypy strict 过 + 调用点更清晰 | `_common.py:42-67` |

## 范围合规

- ✅ 未修改 `src/` 任何代码
- ✅ 未修改 `doc/evaluation/{README,metrics,dataset,procedure,acceptance}.md`
- ✅ 未在代码中出现 `cvte` / `dify` / `insforge`（除 `_fork_name_check.py` 自身的 token 声明 + 同名测试 fixture，二者均在白名单内；`_fork_name_check scripts/eval tests/eval` exit 0 已验证）
- ✅ 未新增运行时依赖（仅用 stdlib + 已有 pydantic v2 + pytest）
- ✅ 未 `git add -A`，所有 add 都是显式文件清单
- ✅ 未跳过 pre-commit hook
- ✅ 未超出 Phase 0 范围（lock.py / prepare.py / 等是 Phase 1+）

## Phase 1 续接锚点

Phase 1 (`lock.py`) 直接可用：

- `scripts.eval._common.read_json / write_json` — 写 `tier{N}.lock.json` 的原子 IO
- `scripts.eval._schemas.TierManifest / ManifestEntry` — lock.json 的 pydantic 反序列化
- `scripts.eval._schemas.AcceptanceThresholds` — `lock.py --update-acceptance-sync` 子命令读写目标
- `scripts.eval._fork_name_check.scan_paths` — Phase 1 / 9 lock 流程可直接调用
- `tests/eval/conftest.py::tmp_workdir` fixture — 给 lock 测试提供隔离 workdir

CI 接入待 Phase 9，目前 `tests/eval/` 不在 `pytest tests/unit/` 默认路径下，PR 不会触发回归。
