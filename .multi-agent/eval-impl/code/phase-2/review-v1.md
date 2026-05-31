# gatekeeper-code 审查报告（Phase 2 v1）

> 审查者：gatekeeper-code（team eval-impl）
> 日期：2026-05-15
> 送审 commit：`8cb3f845a5d51208bfd33f1a281b9caf2ba4e272`
> 实施报告：`.multi-agent/eval-impl/code/phase-2/v1.md`
> 计划基准：`.multi-agent/eval-impl/plan/FINAL.md` §Phase 2
> 测试契约：`.multi-agent/eval-impl/test/FINAL.md` §3 Phase 2（T2-G1..G3 + T2-P1..P6）
> 锁清单基准：`.multi-agent/eval-impl/locks/approved-facts.md`

---

## 结论

**通过**

Phase 2 全部 GO 条件命中、test FINAL T2-* 9 项契约 100% 对齐、Phase 1 carry-over P2-1（死函数 `_file_sha256_or_empty`）已选 (B) 删除并补 try/except 保护、范围严格、5 项细节自纠理由充分。pytest 101/101 PASSED（Phase 0+1 74 + Phase 2 27）/ cov 94.12%（每模块 ≥ 90%）/ mypy strict 17 files 0 err / ruff clean / fork-name-check exit 0。

P0=0 / P1=0 / P2=4（均不阻塞，留 Phase 3+ 顺手优化）。

---

## 契约核查表

### plan FINAL §Phase 2 GO 条件

| 契约 | 状态 | 锚点 |
|---|---|---|
| `prepare.py --tier 1 --out <tmp>` 在 lock 一致前提下展开 sample | PASS | `cmd_prepare:259-265` lock-verify 前置 + T2-P1 `TestPrepareTier1` |
| 每 sample 含 working_tree/golden_tree/meta.yaml/apply_log.txt 4 件 | PASS | `_expand_sample:197-225` + `test_expands_sample_with_four_artifacts` |
| 损坏 patch / 缺失 golden / lock 不一致 三类异常分别抛清晰 exception | PASS | `PatchApplyError` (`prepare.py:43`) / `GroundTruthMissing` (`_ground_truth.py:40`) / lock-verify 退出 1；T2-P4/P5/P6 三向覆盖 |
| mypy strict / ruff / fork-name-check / cov ≥ 80% | PASS | 17 files 0 err / clean / exit 0 / 94.12% |

### test FINAL §3 Phase 2 用例对账

| 用例 | 实现位置 | 状态 |
|---|---|---|
| T2-G1（load_meta：valid + missing + malformed + non-mapping + schema-violation） | `TestLoadMeta` 5 用例 (`test_ground_truth.py:78-127`) | PASS |
| T2-G2（load_golden_tree：valid + missing + corrupted-tar + unsafe-path） | `TestLoadGoldenTree` 4 用例 (`:134-173`) | PASS |
| T2-G3（load_sample 组合：bundle + 缺 meta 传播） | `TestLoadSample` 2 用例 (`:181-198`) | PASS |
| T2-P1（happy: 4 件齐 + apply_log 含 patch step） | `TestPrepareTier1` 2 用例 (`test_prepare.py:131-175`) | PASS |
| T2-P2（lock 一致 → exit 0） | `TestLockGate` (`:183-201`) | PASS |
| T2-P3（tier3 0 字节 fork.patch + apply_log "no-op (empty patch)"） | `TestPrepareTier3` (`:209-232`) | PASS |
| T2-P4（损坏 fork.patch → exit 2 + stderr 含 sample_id + patch_name） | `TestCorruptedPatch` (`:240-268`) | PASS |
| T2-P5（缺 golden → exit 非 0） | `TestMissingGolden` (`:276-306`)；自纠：lock-verify 先 fire 返回 1，rc != 0 与 Verifier 断言一致 | PASS |
| T2-P6（lock 不一致 → exit 1 + stderr 含 "lock verify failed"） | `TestLockMismatch` (`:314-342`) | PASS |

9/9 用例契约对齐；额外 18 个补强（empty-tier / unsafe-path-in-base-tar / corrupted-tar / empty-patch-helper / unified-diff helper / garbage-patch / committed-samples real-tier1 round-trip 等）。

### Phase 1 Carry-over 处置

| Carry-over | 选项 | 验证 |
|---|---|---|
| **P2-1**：`lock.py:99-101 _file_sha256_or_empty` 死函数 | (B) 直接删除 | `git diff HEAD~1 HEAD -- scripts/eval/lock.py` 确认 -3 行 + 空行清理；同步给 `cmd_verify` 加 try/except FileNotFoundError → rc=1（保护 sample 损坏后 verify 失败的边界）✓ |
| 复用 `lock._sample_sha256` / `ARTIFACT_FILES` 不重复实现 sample hash | 间接复用 | `prepare.cmd_prepare` 调 `lock.cmd_verify` 前置；`grep -n "sha256" scripts/eval/_ground_truth.py scripts/eval/prepare.py` 0 匹配 ✓ |
| 接受 `t3-m3-0001` 0 字节 fork.patch | 短路 | `_apply_patch_to_tree:113` `if not patch_bytes.strip(): return [...no-op...]`；T2-P3 + `TestCommittedSamplesSmoke.test_real_tier1_round_trip` 端到端验证 ✓ |

### Approved-facts 锁清单遵守

| 锁条目 | 验证 |
|---|---|
| [code-phase-0] `_schemas` 模型 frozen=True / extra="forbid" 平等扩展 | `SampleMeta` / `GoldenFileEntry` / `GroundTruthBundle` 全部 `_FROZEN` ✓ |
| [code-phase-0] `_common.atomic_write_text` 保 atomic IO | `_expand_sample:223-225` 用 atomic_write_text 写 meta.yaml + apply_log.txt ✓ |
| [code-phase-0] `conftest.py` autouse strip MERGE_DEV | 新测试无重复 monkeypatch.delenv ✓ |
| [code-phase-0] eval_subprocess_env 是 spawn merge 的唯一 env 工厂 | Phase 2 不 spawn merge；Phase 3 必须用 ✓ |
| [code-phase-1] sample sha 算法集中在 lock._sample_sha256 | 通过 `lock.cmd_verify` 间接复用，零自实现 ✓ |
| [code-phase-1] ARTIFACT_FILES 顺序锁定 | 未修改 ✓ |
| [code-phase-1] 4 子命令互斥 group 不得加第 4 动作 | prepare 是独立 CLI（`prepare.py:290-317`），不挤压 lock 互斥 ✓ |
| [code-phase-1] 死函数告警 P2-1 | 选 (B) 删除（已落 commit）✓ |
| [code-phase-1] cmd_update_acceptance_sync 不动 thresholds 子树 | 未触碰（Phase 2 不写 acceptance yaml）✓ |
| [code-phase-1] reference samples USTAR + mtime=1767225600 | 测试 helper `_make_tar` 沿用（`test_ground_truth.py:25-34`、`test_prepare.py:20-29`）✓ |
| [test-amend] / [plan-amend] gate.kind soft 语义 | Phase 2 不实现 gate.py；未触碰 AcceptanceThresholds ✓ |

---

## 测试结果

- **pytest**：`tests/eval/unit/` 101/101 PASSED（in 0.30s）
- **覆盖率**：`--cov=scripts/eval` 总 94.12%（≥ 80%）
  - `_schemas.py` 100%（+ 3 模型仍 100%）
  - `_common.py` 94%
  - `_fork_name_check.py` 90%
  - `_ground_truth.py` 94%（未覆盖：read_text OSError 异常分支 60-61、tar 非 file member 跳过 120、129）
  - `lock.py` 94%（升 1pp，未覆盖：cmd_verify sample-only-in-lock 与 sample-only-on-disk 分支 183-191、acceptance.md missing 222-225、main fall-through 328-329）
  - `prepare.py` 91%（未覆盖：少数异常分支 + `_apply_patched_file` 越界保护 161/170-177/180）
- **mypy**：`scripts tests/eval` strict, 17 files, 0 error
- **ruff check**：All checks passed
- **ruff format --check**：17 files already formatted
- **fork-name-check**：`python -m scripts.eval._fork_name_check scripts/eval tests/eval` exit 0
- **范围验证**：`git diff HEAD~1 HEAD --stat -- src/ doc/evaluation/ pyproject.toml .multi-agent/ tests/eval/datasets/ tests/eval/manifests/` 输出空 = 0 修改

---

## P0 / P1 / P2 分级问题

### P0（必须修复才通过）
无。

### P1（强烈建议修复）
无。

### P2（可选优化，不阻塞）

1. **`_apply_patch_to_tree` 直接 mutate caller 传入的 `tree` dict**
   - **现状**：`prepare.py:99-135` 函数 docstring 已声明 "in-memory ``{path: bytes}``" 是输入兼输出，调用点 `_expand_sample:208,213-215` 也只在本地 base_tree 上跑，功能正确。
   - **风险**：与项目 CLAUDE.md / coding-style.md "Immutable patterns — return new objects, never mutate in place" 轻微冲突；Phase 4 `diff_against_golden.py` 若复用 `_apply_patch_to_tree` 接收外部传入 tree 时容易踩坑。
   - **建议**：Phase 4 复用前改为 `return new_tree` 并在 `_expand_sample` 替换为 `base_tree = _apply_patch_to_tree(...)`。
   - **锚点**：`scripts/eval/prepare.py:99-135`

2. **`_serialise_meta_yaml` 函数内 `import yaml as _yaml`**
   - **现状**：`prepare.py:237-243` 函数体内 import yaml；模块顶部未 import yaml（与 `_ground_truth.py` 不同）。
   - **建议**：升至模块顶部 `import yaml`；阅读流畅性更好，Phase 4+ 若有人复制粘贴时不易遗漏依赖声明。
   - **锚点**：`scripts/eval/prepare.py:237-243`

3. **`cmd_prepare` sentinel acceptance_yaml 路径 hack**
   - **现状**：`prepare.py:263` 传入 `manifests_dir / "__no_such_acceptance_yaml__.yaml"` 让 lock.cmd_verify 走 "yaml 不存在仅 warn" 路径，避免 Phase 6 之前 `_check_acceptance_sync` 干扰。
   - **风险**：耦合知识 — 如果 Phase 6 后 yaml 必存且 sentinel 路径意外存在（用户拷贝？），行为会偏离预期。
   - **建议**：Phase 6 创建 yaml 时顺手把 `lock.cmd_verify` 签名改为 `acceptance_yaml: Path | None = None`，prepare 传 `None` 显式跳过 sync 检查。
   - **锚点**：`scripts/eval/prepare.py:259-265` + `lock.py:158-206`

4. **`_apply_patch_to_tree` 不检查 `_apply_patched_file` 输出 byte 长度合理性**
   - **现状**：patch 应用后 only validates context/removed not past EOF；如果整个文件被 `---/+++` 替换但未给 hunk header，输出可能为空字符串且不报错。
   - **风险**：低（PatchSet 解析本身会拒绝大多数畸形输入；测试 `test_apply_patch_garbage_raises` 已覆盖纯 garbage）。
   - **建议**：Phase 4 比对 fork-applied tree 与 golden 时若发现"empty result"再回头加防御。
   - **锚点**：`scripts/eval/prepare.py:146-189`

---

## 残留风险

| ID | 风险 | 影响 | 处置 |
|---|---|---|---|
| RR1 | sentinel acceptance_yaml 路径在 Phase 6 后可能与真实路径冲突 | 极低 | P2-3 列下 Phase 重构 |
| RR2 | `_apply_patch_to_tree` mutation 模式 | Phase 4 复用风险 | P2-1 列下 Phase 重构 |
| RR3 | unidiff 0.7+ 的 `target_file` 在某些 git-format diff 上可能含 `b/` 前缀 | 已处理（`_patched_file_target_path:138-143`），但仅测试了 `b/` 与 `a/`；其他可能（如 `<path>` 无前缀）依赖 unidiff 行为稳定 | 可接受；committed sample smoke test 已端到端验证 |
| RR4 | `_safe_extract_tar` 写盘 + 返回 dict 双轨 | `_apply_patch_to_tree` 在 dict 上跑，修改 tree 后 `_write_tree_overlay` 二次写盘 — 同一文件写两次 | 功能正确（第二次覆盖第一次），但 IO 开销加倍；样本小不痛，Phase 3+ 大文件可能慢 |

---

## 计划自纠评估

| 自纠项 | 评估 | 接受度 |
|---|---|---|
| `prepare.py` 300→331 行 | patch 应用 + 安全提取 + 4 选项 argparse 必需，文件 ≤ 800 硬上限 | **接受** |
| 独立 argparse 不挤压 lock 互斥 group | 完美遵守 [code-phase-1] 锁清单 | **接受** |
| sentinel acceptance_yaml 路径 | 避免改 lock.py 公共接口的合理 trade-off；P2-3 标注下 Phase 重构 | **接受** |
| `GroundTruthBundle` 含 `tuple[GoldenFileEntry, ...]` + bytes content | Phase 4 缓存友好；frozen=True 不允许 list；bytes 在 pydantic v2 默认 base64 序列化 | **接受** |
| T2-P5 lock-fail 先于 golden-missing → rc=1 而非 rc=2 | 用户视角"sample 不完整"，rc != 0 + stderr 提示足够；Verifier 断言 `rc != 0`（不是精确 rc=2）兼容 | **接受** |

---

## 副作用核查

`git diff --name-only HEAD~1 HEAD` 列出 6 个文件：

- `scripts/eval/_ground_truth.py`（新建）
- `scripts/eval/prepare.py`（新建）
- `scripts/eval/_schemas.py`（修改 +48，派单允许扩展）
- `scripts/eval/lock.py`（修改 净 +2，Phase 1 carry-over P2-1 强制要求）
- `tests/eval/unit/test_ground_truth.py`（新建）
- `tests/eval/unit/test_prepare.py`（新建）

**未触碰**：`src/` / `doc/evaluation/` / `pyproject.toml` / `.multi-agent/` / `.github/workflows/` / `tests/eval/datasets/` / `tests/eval/manifests/` / Phase 0 已交付的 17 文件中除 `_schemas.py` 外的任何一个 / Phase 1 reference samples 全 10 文件 + manifest dir。
**未引入新运行时依赖**：`unidiff>=0.7` + `pyyaml>=6.0` 已在 pyproject.toml `[project] dependencies` 内（pre-existing）。

合规。

---

## 二审及之后

本轮为首审，无上轮反馈核查项。

---

## 通过决定

✅ Phase 2 v1 通过审查。
- copy `v1.md` 到 `code/phase-2/FINAL.md`
- 追加 5 条新事实到锁清单（带 `[code-phase-2]` 标签，含 prepare 独立 CLI 锁定 / GroundTruthBundle 字段契约 / sentinel yaml 路径 hack 待重构 / lock cmd_verify 加 FileNotFoundError 保护 / Phase 1 carry-over 闭环）
- Phase 1 carry-over [code-phase-1] 第 7 条（死函数告警）正式 close — 选 (B) 已落 commit
- 通知 executor + team-lead，Phase 3 实施时请关注 P2-1（mutation 模式重构）+ P2-3（sentinel 路径优化）
