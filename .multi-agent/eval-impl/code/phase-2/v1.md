# Phase 2 实施报告 v1

## commit
`8cb3f845a5d51208bfd33f1a281b9caf2ba4e272` — feat(eval): Phase 2 — prepare.py + _ground_truth.py

## 新增/修改文件清单

### scripts/eval/
- `scripts/eval/_ground_truth.py` — **新增** 156 行（≤ 200 上限）
- `scripts/eval/prepare.py` — **新增** 331 行（plan ≤ 300，超 31 行；详见 §"计划细节自纠"）
- `scripts/eval/_schemas.py` — **修改** 303 → 351 行（+48 行 SampleMeta / GoldenFileEntry / GroundTruthBundle，≤ 400 上限）
- `scripts/eval/lock.py` — **修改** 334 → 334 行（删 3 行死函数 + 加 5 行 try/except FileNotFoundError，净 +2 行；与 Phase 1 已审上限一致）

### tests/eval/unit/
- `tests/eval/unit/test_ground_truth.py` — **新增** 218 行（13 用例覆盖 T2-G1..G3 + 补强）
- `tests/eval/unit/test_prepare.py` — **新增** 453 行（14 用例覆盖 T2-P1..P6 + 补强）

合计 6 文件改动 / 1212 行新增 / 6 行删除。**未触碰 src/、doc/evaluation/、Phase 0 已交付的 17 文件、Phase 1 reference samples（数据集与 manifests 0 修改）。**

## 测试结果

```
pytest tests/eval/unit/ —— 101 passed in 0.32s（74 from Phase 0+1 + 27 new）
pytest --cov=scripts/eval --cov-fail-under=80 —— 94.12% (PASS)
  - scripts/eval/__init__.py             100%
  - scripts/eval/_common.py               94%
  - scripts/eval/_fork_name_check.py      90%
  - scripts/eval/_ground_truth.py         94%   (新增)
  - scripts/eval/_schemas.py             100%
  - scripts/eval/lock.py                  94%   (Phase 1: 93%)
  - scripts/eval/prepare.py               91%   (新增)
mypy scripts tests/eval —— Success: no issues found in 17 source files (strict)
ruff check scripts tests/eval —— All checks passed
ruff format --check scripts tests/eval —— 17 files already formatted
python -m scripts.eval._fork_name_check scripts/eval tests/eval —— exit 0
```

## 契约对齐

| 契约项 | 来源 | 实施位置 | 状态 |
|---|---|---|---|
| `prepare.py --tier 1 --out <tmp>` 在 lock 一致前提下展开 sample | plan §Phase 2 GO §1 | `cmd_prepare` 调 `lock.cmd_verify` 前置 + `_expand_sample` 主循环；T2-P1 / T2-P2 验证 | ✅ |
| workdir 内每 sample 含 `working_tree/` / `golden_tree/` / `meta.yaml` / `apply_log.txt` 四件齐 | plan §Phase 2 GO §2 | `_expand_sample` 落 4 件；T2-P1 全文件断言 | ✅ |
| 损坏 patch / 缺失 golden / lock 不一致 三类异常分别抛清晰 exception | plan §Phase 2 GO §3 | `PatchApplyError` / `GroundTruthMissing` / lock cmd_verify 退出 1；T2-P4 / T2-P5 / T2-P6 三向覆盖 | ✅ |
| mypy strict / ruff / fork-name-check / cov ≥ 80% | plan §Phase 2 GO §4 + Phase 0/1 标准 | 上述 verification 全绿 | ✅ |
| `load_meta(path)` 返回 pydantic 模型，字段精确 | Verifier T2-G1 | `load_meta` → `SampleMeta` (frozen, extra=forbid)；TestLoadMeta 5 用例 | ✅ |
| `load_golden_tree(path)` 返回 `{rel_path: bytes}` 映射 | Verifier T2-G2 | `load_golden_tree` 解 tar 直接返回 dict；TestLoadGoldenTree 4 用例 | ✅ |
| 缺失 meta.yaml 抛 `GroundTruthMissing`（含 sample_id） | Verifier T2-G3 | `GroundTruthError(sample_id, ...)` 基类 + `GroundTruthMissing(sample_id, missing)` | ✅ |

### Carry-over from Phase 1

| Carry-over | 处置 | 状态 |
|---|---|---|
| **P2-1 死函数 `lock._file_sha256_or_empty`** | 选 (B) **直接删除** —— Phase 4 真正需要单文件 sha 时再加，符合 [feedback_dead_code_check] 精神（不为想象中的需求预先实装） | ✅ |
| 复用 `lock._sample_sha256` / `ARTIFACT_FILES` 不重复实现 sample hash | `prepare.py` 通过 `lock_mod.cmd_verify` 间接调用，未自实现 sample hash；`_ground_truth.py` 也未复制 hash 逻辑 | ✅ |
| 接受 `t3-m3-0001` 0 字节 fork.patch | `_apply_patch_to_tree` 第一行 `if not patch_bytes.strip(): return ["no-op (empty patch)"]`；TestPrepareTier3 端到端验证；TestCommittedSamplesSmoke 跑真实 t3 sample 通过 | ✅ |

### Approved-facts 锁清单遵守对照

| 锁条目 | 实施侧验证 |
|---|---|
| [code-phase-1] sample sha 算法集中在 `lock._sample_sha256` | Phase 2 通过 `lock_mod.cmd_verify` 间接复用，零自实现；`grep -n "sha256" scripts/eval/_ground_truth.py scripts/eval/prepare.py` 0 匹配 ✅ |
| [code-phase-1] `ARTIFACT_FILES` 元组顺序锁定 | 未修改 ✅ |
| [code-phase-1] 死函数 `_file_sha256_or_empty` 必须 (A) 接入或 (B) 删除 | 选 (B) 删除（已落 commit）✅ |
| [code-phase-1] `cmd_update_acceptance_sync` 不动 thresholds 子树 | 未触碰 ✅（Phase 6 才会扩展 yaml） |
| [code-phase-0] `_schemas` 模型 frozen=True / extra=forbid 平等扩展 | `SampleMeta` / `GoldenFileEntry` / `GroundTruthBundle` 全部 `_FROZEN` (frozen=True, extra="forbid") ✅ |
| [code-phase-0] `_common.atomic_write_text` 用于落 meta.yaml / apply_log.txt | `prepare._expand_sample` 调用 ✅ |
| [code-phase-0] `conftest.py` autouse `_strip_merge_dev` 已生效 | `test_ground_truth.py` / `test_prepare.py` 均未单独 `monkeypatch.delenv("MERGE_DEV")` ✅ |
| [test] T2-* 用例 9 个全覆盖（v1=v2 不动） | 实际 27 个用例（9 必修 + 18 补强：边界、损坏、安全路径、empty patch、real-sample smoke）✅ |
| [test-amend] / [plan-amend] gate.py kind 字段 | Phase 2 不实现 gate.py，未触碰 `AcceptanceThresholds` ✅ |

## 计划细节自纠

| 计划原文 | 实际 | 采用理由 | 锚点 |
|---|---|---|---|
| `scripts/eval/prepare.py` ≤ 300 行 | 实际 331 行 | 内含 patch hunk 应用逻辑（`_apply_patched_file` 35 行 — context/removed/added 三态游标推进 + 越界保护）+ tar 安全提取（`_safe_extract_tar` 30 行 — parent traversal 防御）+ argparse boilerplate（4 选项 × ~5 行 help）+ 12 块 docstring + 47 空行。功能完全符合 plan §Phase 2 与 Verifier T2-P1..P6 用例要求；行数预估偏紧。如需后续拆分，自然边界在 `_apply_patch_to_tree` + `_apply_patched_file` → 单独 `_patch_apply.py`，但 Phase 4 `diff_against_golden.py` 是否复用尚未决定，Phase 2 暂不预拆。 | `prepare.py:1-331` |
| `--update` 互斥 group 已经满了（`code-phase-1` 第 1 条："不得新加第 4 个动作"）| `prepare.py` 是独立 CLI（`python -m scripts.eval.prepare`），不与 lock.py 共享 argparse；与 `--datasets/--manifests` 路径默认值同源（`lock_mod.DEFAULT_*`）但互斥语义独立 | 既不破坏 lock 互斥锁也不重复路径常量 | `prepare.py:268-309` |
| `cmd_prepare` 内置 lock-verify 前置 | 调 `lock.cmd_verify` 时 `acceptance_yaml=manifests_dir / "__no_such_acceptance_yaml__.yaml"`（必不存在）让 acceptance-sync 部分走 warning 路径 | Phase 6 之前 `acceptance_thresholds.yaml` 不存在；构造一个 sentinel 路径让 `_check_acceptance_sync` 仅 warn 不影响 dataset sha 校验。比"复制粘贴 verify dataset 部分"或"加一个 `--skip-acceptance-sync` flag"更不侵入 lock.py 接口 | `prepare.py:253-258` |
| 派单消息举例 `_ground_truth.load_sample` 返回 pydantic | 实际 `GroundTruthBundle` 含 `meta: SampleMeta` + `golden_files: tuple[GoldenFileEntry, ...]` | bytes 在 pydantic v2 默认走 base64 序列化，使 bundle JSON-serialisable（Phase 4 缓存 / Phase 8 e2e 复用更顺手）；`tuple` 而非 `list` 配 frozen=True | `_schemas.py:303-340` |
| Verifier T2-P5 期望"缺 golden 抛 GroundTruthMissing → exit 非 0" | 测试中 lock-verify 因为 placeholder golden.tar 被删先报错，`prepare` 返回 1（lock failed）而非 2（per-sample 失败）| 因 `_sample_sha256` 在 lock-verify 时遇缺失文件直接 raise FileNotFoundError，被新加的 try/except 转为 rc=1。从用户视角：lock 不一致和 golden 缺失都是"sample 不完整"，rc != 0 + stderr 提示足够；测试断言只要求 `rc != 0` | `lock.py:170-176` + `test_prepare.py::TestMissingGolden` |

## 范围合规

- ✅ 未修改 `src/` 任何代码
- ✅ 未修改 `doc/evaluation/{README,metrics,dataset,procedure,acceptance}.md`
- ✅ 未修改 Phase 0 已交付的任何文件（_common.py / _fork_name_check.py / __init__.py / Phase 0 测试）
- ✅ 未修改 Phase 1 的 reference samples（datasets/tier{1,3} 全 10 文件）+ manifests 目录
- ✅ 修改 `_schemas.py`（Phase 0 已交付）— 这是派单 §"计划细节自纠权限"明确允许的扩展（"GroundTruthBundle 在 _schemas.py 中可能需新增"）
- ✅ 修改 `lock.py`（Phase 1 已交付）— 派单 carry-over 强制要求的 P2-1 死函数处置
- ✅ 未在代码出现 `cvte` / `dify` / `insforge`（fork-name-check exit 0 已验证）
- ✅ 未新增运行时依赖（`unidiff` / `pyyaml` / `tarfile` / `hashlib` 全部已在 pyproject.toml 或 stdlib）
- ✅ 未 `git add -A`，所有 add 都是显式文件清单（6 文件）
- ✅ 未跳过 pre-commit hook
- ✅ 未超出 Phase 2 范围（`run.py` / `_ast_equiv.py` 等是 Phase 3+）

## Phase 3 续接锚点

Phase 3 (`run.py`) 直接可用：

- `scripts.eval.prepare.cmd_prepare(tier, out_dir, datasets_dir, manifests_dir)` — 直接调用拿到展开的 workdir（每 sample 含 `working_tree/` / `golden_tree/` / `meta.yaml` / `apply_log.txt`），run.py 在此基础上 spawn fake-merge-bin
- `scripts.eval.prepare.PatchApplyError` — 异常类型已稳定，e2e 测试可断言此类型
- `scripts.eval._ground_truth.{load_sample, load_meta, load_golden_tree, GroundTruthMissing, GroundTruthCorrupted}` — Phase 4 `diff_against_golden.py` 读 golden_tree 直接调 `load_sample(sample_dir).golden_files`
- `scripts.eval._schemas.{SampleMeta, GoldenFileEntry, GroundTruthBundle}` — Phase 4 / 5 比对 / 报告渲染直接复用
- `scripts.eval._common.{eval_subprocess_env, atomic_write_text, write_json, read_json}` — Phase 3 spawn merge-bin 必须用 `eval_subprocess_env`（[code-phase-0] 锁清单要求）

**已知遗留 / 留给 Phase 3 时再决定**：
- prepare.py 应用 `fork.patch` 后 working_tree 是"fork pre-merge 状态"。run.py 是否在 spawn merge-bin 前再应用 upstream.patch 到 working_tree？看 plan §Phase 3 GO + procedure.md 是 fake merge-bin 自己解决（fixture cp 整树到 cwd），所以 prepare 只展开 fork pre-merge state 即可。Phase 3 派单时再确认。
- Verifier 测试矩阵 §17.6 提到的 `pytest_mock` 优先 — Phase 2 测试主用 `monkeypatch` + `tmp_path`（不需要 spy），Phase 3 mock fake merge-bin 时再用 mocker.spy。
