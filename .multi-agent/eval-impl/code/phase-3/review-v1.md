# gatekeeper-code 审查报告（Phase 3 v1）

> 审查者：gatekeeper-code（team eval-impl）
> 日期：2026-05-15
> 送审 commit：`c3e2c6ca858683b28efbd354dd103ac47433040c`
> 实施报告：`.multi-agent/eval-impl/code/phase-3/v1.md`
> 计划基准：`.multi-agent/eval-impl/plan/FINAL.md` §Phase 3
> 测试契约：`.multi-agent/eval-impl/test/FINAL.md` §4 Phase 3（T3-R1..R8）
> 锁清单基准：`.multi-agent/eval-impl/locks/approved-facts.md`

---

## 结论

**通过**

Phase 3 plan GO 5/5 + test FINAL T3-R1..R8 全 8 项契约 100% 对齐；三层隔离（env/HOME/cwd）+ memory leak 守护 + fake_merge_bin 字面量 FIXTURE 模板 + RunMeta schema 扩展全部按 [code-phase-0]/[code-phase-2]/[test] 锁清单执行；范围严格、7 项细节自纠理由充分；pytest 121/121 / cov 93.92% / mypy strict 19 files 0 err / ruff clean / fork-name-check exit 0。

P0=0 / P1=0 / P2=4（均不阻塞，留 Phase 4/5 顺手优化）。

---

## 契约核查表

### plan FINAL §Phase 3 GO 条件

| 契约 | 状态 | 锚点 |
|---|---|---|
| `--merge-bin` 注入 fake 跑出 5 类产物（实际 7 路径，自纠合理） | PASS | `_run_one_sample:211-216` 拷贝 + T3-R1 验证 |
| 三层隔离：env (eval_subprocess_env) + HOME=<workdir>/home + cwd=<workdir>/runs/<id>/_cwd | PASS | `run.py:186-188` + T3-R2/R3/R6 |
| 每 sample 跑前 `<cwd>/.merge/memory.db` 不存在；存在则触发 MemoryLeakDetected | PASS | `_assert_clean_memory:56-59` + T3-R8 4 用例 |
| `--concurrency N` 用 asyncio.Semaphore + run_meta.json 含 concurrency 字段 | PASS | `cmd_run:279` + T3-R4 |
| mypy strict / ruff / fork-check / cov ≥ 80% | PASS | 19 files 0 err / clean / exit 0 / 93.92% |

### test FINAL §4 Phase 3 用例对账

| 用例 | 实现位置 | 状态 |
|---|---|---|
| T3-R1（happy + 7 产物齐） | `TestArtifacts.test_run_produces_seven_artifact_families` (`test_run.py:149-171`) | PASS |
| T3-R2（env 不含 MERGE_DEV，宿主 `MERGE_DEV=1` 设置后子进程仍剥离） | `TestEnvIsolation.test_subprocess_env_does_not_contain_merge_dev` (`:179-203`) | PASS |
| T3-R3 v2 强化（独立 cwd + memory.db 真实路径不存在） | `TestCwdIsolation.test_each_sample_gets_unique_cwd_and_no_memory_db` (`:211-238`) | PASS |
| T3-R4（RunMeta 7 必备字段 + concurrency=2） | `TestRunMeta.test_run_meta_contains_required_fields` (`:246-277`) | PASS |
| T3-R5（fake exit 1 → status="failed"，rc=1，其他 sample 仍跑） | `TestFailedSampleIsolation.test_failed_sample_marked_failed_and_other_continues` (`:285-310`) | PASS |
| T3-R6（HOME 只读 → 异常） | `TestWorkdirReadOnly.test_unwritable_workdir_raises` (`:318-338`) | PASS |
| T3-R7（concurrency=0 → rc=2 + stderr 提示） | `TestConcurrencyValidation.test_zero_concurrency_returns_two` (`:346-360`) | PASS |
| T3-R8 v2 新增（memory leak 检测：正向 helper + 反向 helper + 整链反向 + 跨 sample 隔离） | `TestMemoryLeakGuard` 4 用例 (`:368-436`) | PASS（自纠：反向用例改用"预创 memory.db"等价覆盖，更直接） |

8/8 用例契约对齐；额外 12 个补强（stdout JSON 边界 / `_persist_ci_summary` dict + non-dict 包装 / `_locate_merge_run_dir` 0/1/N 子目录三态 / `_copy_working_tree` 跳 .merge / `_git_sha` 容错）。

总用例数：20（Phase 0 54 + Phase 1 20 + Phase 2 27 + Phase 3 20 = 121）。

### Approved-facts 锁清单遵守

| 锁条目 | 验证 |
|---|---|
| [code-phase-0] `eval_subprocess_env` 是 spawn merge 的唯一 env 工厂 | `_run_one_sample:186` 直接调；run.py 内 `grep MERGE_DEV` 仅 docstring 1 处，无自实现 ✓ |
| [code-phase-0] `_schemas` 模型 frozen=True / extra="forbid" 平等扩展 | `RunMeta` 新 3 字段（Literal 受限，default 值兼容）保持 `_FROZEN` ✓ |
| [code-phase-0] `_common.write_json` atomic 保 sha 稳定 | `_run_one_sample:239` `write_json(...run_meta.json...)` + `_persist_ci_summary:126` ✓ |
| [code-phase-0] `conftest.py` autouse strip MERGE_DEV | T3-R2 显式 `monkeypatch.setenv("MERGE_DEV", "1")` 测试**子进程**剥离；不与 autouse 冲突（autouse 给测试本体，子进程 env 由 eval_subprocess_env 处理）✓ |
| [code-phase-1] sample sha 算法 / ARTIFACT_FILES 顺序锁定 | run.py 不直接哈希 sample；通过 cmd_prepare → cmd_verify 间接复用 ✓ |
| [code-phase-1] 4 子命令互斥 group 不得加第 4 动作 | run.py 是独立 CLI，不挤压 lock argparse ✓ |
| [code-phase-1] `cmd_update_acceptance_sync` 不动 thresholds 子树 | run.py 不接触 acceptance yaml ✓ |
| [code-phase-2] 必须经 cmd_prepare，不可跳 | `cmd_run:263-271` 第一步即调；prepare 失败直接 return prepare_rc ✓ |
| [code-phase-2] `_apply_patch_to_tree` mutation 警告（Phase 4 才需重构） | Phase 3 未直接调 _apply_patch_to_tree；prepare 已封装 ✓ |
| [code-phase-2] sentinel acceptance_yaml 路径 hack（Phase 6 重构） | run.py 通过 cmd_prepare 传播；不引新问题 ✓ |
| [code-phase-2] `RunMeta` schema 已存在，禁止平行定义 | run.py 仅 import；3 字段加在原模型上 ✓ |
| [test] fake_merge_bin 字面量 FIXTURE + 单 rename 路径 (§17.3-prime) | fake_merge.sh 4 行 cp 单 rename，无 glob，无 stderr 静默 ✓ |
| [test] 用例总数权威 102 | Phase 3 新增 20，累计 121 unit；其余用例属 Phase 4-9 范畴 ✓ |
| [plan] memory.db 是 SQLite 单文件 / merge_state.json 不存在 / MERGE_HOME 不存在 | `MEMORY_DB_RELATIVE = Path(".merge/memory.db")` 单文件路径 ✓；fixture 文件名用 `merge_report_FIXTURE.{json,md}` + `plan_review_FIXTURE.md` + `checkpoint.json` 全部正确 ✓ |
| [plan] CI stdout 仅 run-level 字段 | fake_merge.sh echo 9 字段（status/run_id/total_files/auto_merged/human_required/human_decided/failed_count/judge_verdict/errors）与 ci_reporter.build_ci_summary 同源 ✓ |

---

## 测试结果

- **pytest**：`tests/eval/unit/` 121/121 PASSED（in 0.84s）
- **覆盖率**：`--cov=scripts/eval` 总 93.92%（≥ 80%）
  - `_schemas.py` 100%（+ 3 新字段全覆盖）
  - `_common.py` 94% / `_fork_name_check.py` 90% / `_ground_truth.py` 94% / `lock.py` 94% / `prepare.py` 91%
  - `run.py` 93%（未覆盖：`_git_sha` git 缺失分支 76-78、`_copy_working_tree` 单文件分支 94、`_run_one_sample` 已拷贝 prepared_tree 失败回退 180、`cmd_run` 双 guard 256-257、空 prepared sample 270-271/275-276）
- **mypy**：`scripts tests/eval` strict, 19 files, 0 error
- **ruff check**：All checks passed
- **ruff format --check**：19 files already formatted
- **fork-name-check**：`python -m scripts.eval._fork_name_check scripts/eval tests/eval` exit 0
- **范围验证**：`git diff HEAD~1 HEAD --stat -- src/ doc/evaluation/ pyproject.toml .multi-agent/ datasets/ manifests/` 输出空 = 0 修改

---

## P0 / P1 / P2 分级问题

### P0（必须修复才通过）
无。

### P1（强烈建议修复）
无。

### P2（可选优化，不阻塞）

1. **`_run_one_sample` 失败 sample 不拷贝 merge artifacts**
   - **现状**：`run.py:211-216` `if exit_code == 0:` 才拷贝 working_tree + merge artifacts；失败时只落 `ci_summary.json` + `stderr.log` + `run_meta.json`。
   - **影响**：Phase 4/5 调试失败 sample 时数据不全（无 partial merge_report）。
   - **建议**：Phase 4/5 视实际需求决定是否扩展为"无论 exit_code 都尝试拷贝可读取的产物"；当前 status=failed + stderr.log 已够基础诊断。
   - **锚点**：`scripts/eval/run.py:211-216`

2. **`_persist_ci_summary` 对非 dict 顶层 JSON 包装 `{"raw_value": ...}`**
   - **现状**：`run.py:118-127` 当 stdout 是 list/str/number 等非 dict 时，包装成 `{"raw_value": ...}`；invalid JSON 包装成 `{"invalid_json": True, "raw_stdout": ...}`。
   - **影响**：Phase 5 summarize.py / Phase 4 sanity-check 读 ci_summary.json 必须感知此包装层。
   - **建议**：Phase 5 加文档说明；或在 ci_summary.json 顶层加 `"_eval_wrapper_version": "1"` 标识。
   - **锚点**：`scripts/eval/run.py:118-127` + 已在 [code-phase-3] 新事实记录 carry-forward

3. **`_build_run_meta` 用 3 处 `# type: ignore[arg-type]`**
   - **现状**：`run.py:153-155` `status` / `memory_clean_check` 是 Literal 字段，但 caller 传字符串表达式 `"success" if exit_code == 0 else "failed"`，mypy strict 推断为 `str` 而非 Literal，需 `# type: ignore[arg-type]`。
   - **建议**：可改为 `cast(Literal["success","failed"], ...)` 或显式定义 helper 返回 Literal；当前 `# type: ignore` 也能过 strict，但显式 cast 更精确。
   - **锚点**：`scripts/eval/run.py:139-156`

4. **`_run_one_sample` 复制 prepared_tree 到 cwd 用 `shutil.copytree(dirs_exist_ok=True)` 不做路径安全校验**
   - **现状**：`run.py:175-182` 信任 `prepare.py` 输出（已经 `_safe_extract_tar` 校验过），不再二次校验。
   - **影响**：信任边界明确；prepare.py 已建立的安全契约本就不应在 run.py 层重复。
   - **建议**：可接受；如未来 `--workdir` 直接接受外部 prepared dir（绕过 cmd_prepare）则需补防御。
   - **锚点**：`scripts/eval/run.py:175-182`

---

## 残留风险（含 carry-forward）

| ID | 风险 | 影响 | 处置 |
|---|---|---|---|
| RR1 | [code-phase-2] P2-1 `_apply_patch_to_tree` mutation | Phase 4 复用风险 | **carry-forward Phase 4**（必须先重构为 return new dict） |
| RR2 | [code-phase-2] P2-3 sentinel acceptance_yaml 路径 hack | Phase 6 yaml 创建后行为偏离 | **carry-forward Phase 6**（建议 cmd_verify 加 acceptance_yaml: Path \| None） |
| RR3 | `_locate_merge_run_dir` 单子目录假设 | 真实 merge CLI 单 run 单目录 + fake 同样保证；未来若 merge CLI 多 run 复用 cwd 会回 None | 当前可接受；Phase 8 e2e 一次跑一 run 不会破 |
| RR4 | failed sample 缺 partial artifacts | 调试不便 | P2-1 列下 Phase 视情况扩 |
| RR5 | ci_summary.json 包装层 | Phase 5 必须感知 | P2-2 列下 Phase 处理 |
| RR6 | `--use-real-keys` 标志已实装但未测试 | 真 LLM key 路径未覆盖 | Phase 8 e2e 仍走 dummy；真 keys 走 procedure.md 手动流程 |

---

## 计划自纠评估

| 自纠项 | 评估 | 接受度 |
|---|---|---|
| RunMeta 7→10 字段（status/memory_clean_check/exit_code） | T3-R5/R8 显式断言要求；Literal-typed default 不破坏 7 必备字段 issubset 断言 | **接受** |
| `_assert_clean_memory` + `MemoryLeakDetected` 经 `_bounded` 转 rc=1 | 与"失败 sample 不阻断其他"（T3-R5）保持一致语义 | **接受** |
| 5 类→7 路径 | 与 [plan] 真实产物名一致 | **接受** |
| subprocess list args（`asyncio.create_subprocess_exec` + `subprocess.run([...])`） | 避免 shell injection | **接受** |
| `--merge-args` 用 split() 而非 shlex.split() | 简单可靠；嵌套引号场景再升级；文档化 trade-off | **接受** |
| T3-R8 反向用例改"测试预创 memory.db" | 等价覆盖；run.py 无 `shutil.copy` memory.db 的 mock 点；测试更直接 | **接受** |
| T3-R8 `FAKE_TOUCH_MEMORY` 全局开关下两 sample 各自独立写 | 守护"独立 cwd → 独立 memory 路径"等同契约；env per-sample 不可行 | **接受** |

---

## 副作用核查

`git diff --name-only HEAD~1 HEAD` 列出 8 个文件：

- `scripts/eval/run.py`（新建）
- `scripts/eval/_schemas.py`（修改 +3 字段，派单允许扩展）
- `tests/eval/unit/test_run.py`（新建）
- `tests/eval/fixtures/fake_merge_bin/fake_merge.sh`（新建，可执行 -rwxr-xr-x）
- `tests/eval/fixtures/dummy_run/runs/t1-0001/{merge_report_FIXTURE.json, merge_report_FIXTURE.md, plan_review_FIXTURE.md, checkpoint.json}`（4 文件，按 [test] §17.3-prime 字面量 FIXTURE 命名）

**未触碰**：`src/` / `doc/evaluation/` / `pyproject.toml` / `.multi-agent/` / `.github/workflows/` / `tests/eval/datasets/` / `tests/eval/manifests/` / Phase 0/1/2 已交付的 23 文件中除 `_schemas.py` 外的任何一个。
**未引入新运行时依赖**：`asyncio` / `subprocess` / `shutil` / `time` / `uuid` / `json` 全 stdlib。

合规。

---

## 二审及之后

本轮为首审，无上轮反馈核查项。

---

## 通过决定

✅ Phase 3 v1 通过审查。
- copy `v1.md` 到 `code/phase-3/FINAL.md`
- 追加 7 条新事实到锁清单（带 `[code-phase-3]` 标签，含三层隔离契约 / fake_merge_bin env API / RunMeta 字段扩展 / _persist_ci_summary 包装契约 / Phase 4 carry-forward 待办 / 测试基线 121 用例）
- 通知 executor + team-lead，Phase 4 实施时请关注 [code-phase-2] P2-1 carry-forward（`_apply_patch_to_tree` 必须先重构）+ Phase 5 注意 ci_summary.json 包装层
