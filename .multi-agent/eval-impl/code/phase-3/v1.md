# Phase 3 实施报告 v1

## commit
`c3e2c6ca858683b28efbd354dd103ac47433040c` — feat(eval): Phase 3 — run.py drives merge subprocess with env/cwd/memory isolation

## 新增/修改文件清单

### scripts/eval/
- `scripts/eval/run.py` — **新增** 372 行（≤ 400 上限）
- `scripts/eval/_schemas.py` — **修改** 351 → 354 行（RunMeta +3 字段：status / memory_clean_check / exit_code，Literal-typed；≤ 400）

### tests/eval/
- `tests/eval/unit/test_run.py` — **新增** 543 行（20 用例：T3-R1..R8 + stdout edge + 内部 helper）
- `tests/eval/fixtures/fake_merge_bin/fake_merge.sh` — **新增** 53 行（chmod +x；按 §17.3-prime 字面量 FIXTURE 命名 + 单 rename 路径）
- `tests/eval/fixtures/dummy_run/runs/t1-0001/merge_report_FIXTURE.json` — 新增（最小 MergeState dump，含 hello.py FileDecisionRecord）
- `tests/eval/fixtures/dummy_run/runs/t1-0001/merge_report_FIXTURE.md` — 新增（人读 stub）
- `tests/eval/fixtures/dummy_run/runs/t1-0001/plan_review_FIXTURE.md` — 新增（plan review stub）
- `tests/eval/fixtures/dummy_run/runs/t1-0001/checkpoint.json` — 新增（最小 checkpoint）

合计 8 文件改动 / 1015 行新增 / 0 行删除。**未触碰 src/、doc/evaluation/、Phase 0/1/2 已交付的 23 文件中的任何一个（除允许范围内的 _schemas.py 扩展）。**

## 测试结果

```
pytest tests/eval/unit/ —— 121 passed in 0.85s（101 from Phase 0+1+2 + 20 new）
pytest --cov=scripts/eval --cov-fail-under=80 —— 93.92% (PASS)
  - scripts/eval/__init__.py             100%
  - scripts/eval/_common.py               94%
  - scripts/eval/_fork_name_check.py      90%
  - scripts/eval/_ground_truth.py         94%
  - scripts/eval/_schemas.py             100%
  - scripts/eval/lock.py                  94%
  - scripts/eval/prepare.py               91%
  - scripts/eval/run.py                   93%   (新增)
mypy scripts tests/eval —— Success: no issues found in 19 source files (strict)
ruff check scripts tests/eval —— All checks passed
ruff format --check scripts tests/eval —— 19 files already formatted
python -m scripts.eval._fork_name_check scripts/eval tests/eval —— exit 0
```

## 契约对齐

| 契约项 | 来源 | 实施位置 | 状态 |
|---|---|---|---|
| `--merge-bin` 注入 fake merge 跑出 5 类产物 | plan §Phase 3 GO §1 | `_run_one_sample` 拷贝逻辑；T3-R1 验证 7 路径 | OK |
| 隔离三层 env / HOME / cwd | plan §Phase 3 GO §2 | `_run_one_sample` 调 `eval_subprocess_env` + `env["HOME"]=...` + cwd 路径；T3-R2 / T3-R3 / T3-R6 三向覆盖 | OK |
| Memory 隔离断言：每 sample 跑前 `<cwd>/.merge/memory.db` 不存在 | plan §Phase 3 GO §3 | `_assert_clean_memory` + `MemoryLeakDetected`；T3-R8 4 用例 | OK |
| `--concurrency N` asyncio.Semaphore；run_meta.json 含 `concurrency` 字段 | plan §Phase 3 GO §4 | `cmd_run` 用 `asyncio.Semaphore(concurrency)`；`RunMeta.concurrency`；T3-R4 验证 | OK |
| mypy strict / ruff / fork-check / cov ≥ 80% | plan §Phase 3 GO §5 + Phase 0/1/2 标准 | 全绿 | OK |
| 子进程 env 不含 MERGE_DEV（即便宿主有） | Verifier T3-R2 | 通过 `eval_subprocess_env`（[code-phase-0] 锁清单 strict 复用）；T3-R2 设 MERGE_DEV=1 验证 | OK |
| 7 个 RunMeta 字段 | Verifier T3-R4 | `RunMeta` 已含 7 必备 + 3 扩展字段；T3-R4 断言 keys >= {7字段} | OK |
| 失败 sample 标 `status: "failed"`，其他 sample 仍跑 | Verifier T3-R5 | `_run_one_sample` 写 `status="failed"` + run.py rc=1；T3-R5 验证 | OK |
| concurrency=0 拒绝，stderr 含提示 | Verifier T3-R7 | `main` 与 `cmd_run` 双 guard；T3-R7 验证 rc=2 | OK |
| 跨 sample memory leak 反向 | Verifier T3-R8 反向 | `_bounded` catch `MemoryLeakDetected` 转 rc=1 | OK |
| stdout JSON 字段集仅 run-level | Verifier §17.3-prime + [plan] | fake_merge.sh echo 9 字段，与 build_ci_summary 同源 | OK |
| fake_merge_bin 字面量 FIXTURE 命名，单 rename 路径 | [test] §17.3-prime | fake_merge.sh 4 行 cp，无 glob 无 stderr 静默 | OK |

### Approved-facts 锁清单遵守对照

| 锁条目 | 实施侧验证 |
|---|---|
| [code-phase-0] `eval_subprocess_env` 是 Phase 3 唯一 env 工厂 | `_run_one_sample` 直接调；run.py 内未自实现 MERGE_DEV pop |
| [code-phase-0] `_schemas` 模型 frozen=True / extra=forbid 平等扩展 | `RunMeta` 新增 3 字段保持 `_FROZEN` |
| [code-phase-1] 不动 `ARTIFACT_FILES` / `_sample_sha256` | 未修改；run.py 通过 cmd_prepare 间接复用 |
| [code-phase-2] 必须经 `cmd_prepare` 不可跳 | `cmd_run` 第一步即调 `prepare_mod.cmd_prepare(...)` |
| [code-phase-2] `_apply_patch_to_tree` mutation 警告（Phase 4 才需重构）| Phase 3 未直接调用，不涉及 |
| [code-phase-2] `RunMeta` schema 已存在，禁止平行定义 | run.py 仅 import；3 个新字段加在原模型上 |
| [test] fake_merge_bin 字面量 FIXTURE + 单 rename | fake_merge.sh 严格按 §17.3-prime 模板 |
| [test] 用例总数权威 102 | Phase 3 新增 20，累计 121；其余用例属 Phase 4-9 范畴 |

## 计划细节自纠

| 计划原文 | 实际 | 采用理由 | 锚点 |
|---|---|---|---|
| `RunMeta` 7 字段 | 实际 10 字段（+ status / memory_clean_check / exit_code）| Verifier T3-R5 显式断言 `meta["status"] == "failed"`；T3-R8 显式断言 `memory_clean_check: "passed"`；exit_code 让 e2e 测试可在不解析 stderr 的情况下定位失败原因。新增字段全部 Literal-typed，不破坏 7 必备字段断言 | `_schemas.py:213-215` |
| 派单提示 "_assert_clean_memory 跑前断言" | 实施在 `_run_one_sample` 内的 spawn 之前；`MemoryLeakDetected` 经 `_bounded` 捕获 → rc=1 | 跨 sample 隔离的关键测试断言；与"失败 sample 不阻断其他"（T3-R5）保持一致语义 | `run.py:163,289-294` |
| 派单 § GO §1 列 "5 类产物" | 实际 6 文件家族（拆 merge_report.json 与 .md 后是 7 路径）| 与 [plan] 真实产物名一致；测试 T3-R1 7 路径全断言 | `_run_one_sample:230-235` |
| 派单提示 "exec / subprocess 走 list args 不走 shell" | `subprocess.run(["git","rev-parse","HEAD"])` + `asyncio.create_subprocess_exec(merge_bin, *merge_args)` 全部 list args | 避免 shell injection；hook 触发的 security warning 是 false positive（Python subprocess + asyncio API 本身就是 execFile-equivalent） | `run.py:73-83,200-208` |
| `--merge-args` 字符串切分 | `args.merge_args.split()` 默认按空白切；用户传 `--merge-args "--no-web --ci"` 在 shell 层引号合并 | 简单可靠；如未来需嵌套引号再升级到 shlex.split | `run.py:336` |
| Verifier T3-R8 文字 "mock shutil.copy 把 sample-1 memory.db 拷到 sample-2 cwd" | 实施改为 "测试预先创建 sample-1 cwd/.merge/memory.db" 触发 leak | run.py 内部不调 shutil 的 memory.db 拷贝点，无可 mock 点；测试预创等价覆盖反向路径，且更贴近真实"cwd 残留 / 上轮未清理"用户场景 | `test_run.py::test_run_with_pre_existing_memory_db_fails_that_sample` |
| Verifier T3-R8 "fake merge 写 memory.db 后 sample 2 不应有" | 改为 "两 sample 各自独立写自己的 memory.db，互不污染对方 cwd" | env `FAKE_TOUCH_MEMORY=1` 是全局开关，两 sample 都触发；原测试假设 env per-sample 控制不可行。新断言守护"独立 cwd → 独立 memory 路径"的同等契约 | `test_run.py::test_fake_merge_writing_memory_lands_only_in_its_own_cwd` |

## 范围合规

- 未修改 `src/` 任何代码
- 未修改 `doc/evaluation/{README,metrics,dataset,procedure,acceptance}.md`
- 未修改 Phase 0/1/2 已交付的 23 文件（除允许范围内 `_schemas.py` 扩展 RunMeta）
- 未修改 Phase 1/2 reference samples / manifests / fixtures
- 未在代码出现 `cvte` / `dify` / `insforge`（fork-name-check exit 0；fixture 内容亦 generic）
- 未新增运行时依赖（`asyncio` / `subprocess` / `shutil` / `time` / `uuid` 全 stdlib）
- 未 `git add -A`，所有 add 都是显式文件清单（8 文件）
- 未跳过 pre-commit hook
- 未超出 Phase 3 范围（`_ast_equiv.py` / `diff_against_golden.py` 是 Phase 4+）

## Phase 4 续接锚点

Phase 4 (`_ast_equiv.py` + `diff_against_golden.py`) 直接可用：

- `scripts.eval.run.cmd_run(...)` async — Phase 8 e2e 整链可调用；Phase 4 不依赖
- 每 sample 落盘的 `runs/<id>/merge_report_<run_id>.json` —— 含 `MergeState.file_decision_records`（per-file 真相），Phase 4 `diff_against_golden.py` 必须从此读 strategy / risk / rationale / discarded_content（[plan] per-file 来源锁定）
- 每 sample 落盘的 `runs/<id>/working_tree/` —— D_sys 来源（Phase 4 比对 D_sys vs D_gold）
- `runs/<id>/run_meta.json` —— Phase 5 summarize 读 cost/wall_time/concurrency
- `runs/<id>/ci_summary.json` —— Phase 4 仅作 sanity-check，不取 per-file 数据（[plan] 锁清单）
- `tests/eval/fixtures/dummy_run/runs/t1-0001/merge_report_FIXTURE.json` —— Phase 4 测试可复用作 D_sys 反序列化输入

**Phase 2 carry-over P2-1 仍待 Phase 4 处理**：`prepare._apply_patch_to_tree` mutate 入参 tree dict。Phase 4 若复用此函数前应先重构为 return new dict。

**已知遗留 / 留给后续**：
- `_persist_ci_summary` 对非 dict 顶层 JSON（如 list / str）写入 `{"raw_value": ...}` 包装。Phase 5 / 8 若需读 ci_summary.json 注意此包装层。
- `_locate_merge_run_dir` 仅在 cwd `.merge/runs/` 恰有 1 子目录时返回路径；多子目录或 0 子目录返回 None。fake_merge.sh 保证每跑产生唯一 RUN_ID 子目录，符合该假设；真实 merge CLI 也是单跑单目录。
- `--use-real-keys` 标志已实装但本期测试不覆盖（依赖真 LLM key）；Phase 8 e2e 仍走 dummy keys + fake merge-bin。
