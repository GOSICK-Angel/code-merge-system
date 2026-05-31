# gatekeeper-code 审查报告（Phase 1 v1）

> 审查者：gatekeeper-code（team eval-impl）
> 日期：2026-05-15
> 送审 commit：`1101b2aa8a46412a5dee8b99ce82c5b93aa2add8`
> 实施报告：`.multi-agent/eval-impl/code/phase-1/v1.md`
> 计划基准：`.multi-agent/eval-impl/plan/FINAL.md` §Phase 1
> 测试契约：`.multi-agent/eval-impl/test/FINAL.md` §2 Phase 1（T1-L1..L8）
> 锁清单基准：`.multi-agent/eval-impl/locks/approved-facts.md`（[bootstrap]/[plan]/[plan-amend]/[code-phase-0]/[test-amend]/[test]）

---

## 结论

**通过**

Phase 1 全部 GO 条件命中、test FINAL T1-L1..L8 契约 100% 对齐、范围严格、4 项细节自纠理由充分；锁清单零违反。pytest 74/74 PASSED（Phase 0 54 + Phase 1 20 新）/ cov 94.56% / mypy strict 0 err / ruff clean / fork-name-check exit 0。Sample reproducible（USTAR + mtime=1767225600 + uid/gid=0 + mode 0o644）已现场拆 tar 验证内容 + tarinfo header。

P0=0 / P1=0 / P2=2（其中 1 项触发 [feedback_dead_code_check] 锁清单告警，列入下 Phase 必清单 — 但不阻塞本 Phase）。

---

## 契约核查表

### plan FINAL §Phase 1 GO 条件

| 契约 | 状态 | 锚点 |
|---|---|---|
| `python scripts/eval/lock.py --verify` 退出 0；篡改后非 0 | PASS | `cmd_verify` (`lock.py:158-206`) + T1-L2/L4 用例（`test_lock.py:167-229`） |
| `python scripts/eval/lock.py --update` 写出三份 `*.lock.json` | PASS | `cmd_update` (`lock.py:138-146`) + T1-L1 用例（`test_lock.py:108-158`） |
| 单测覆盖 ≥ 80% | PASS | scripts/eval 总 94.56% / lock.py 93% |
| `--verify` 检测 yaml 不存在时 warning（不 fail，本地） | PASS | `_check_acceptance_sync:215-220` + T1-L5 (`test_lock.py:304-333`) |
| 至少 1 个 Tier-1 sample (`t1-0001`) + 1 个 Tier-3 sample (`t3-m3-0001`) 完整 5 件套 | PASS | `tests/eval/datasets/tier1/samples/t1-0001/{base.tar,upstream.patch,fork.patch,golden.tar,meta.yaml}` + `tier3/adversarial/t3-m3-0001/...` |

### test FINAL §2 Phase 1 用例对账

| 用例 | 实现位置 | 状态 |
|---|---|---|
| T1-L1（`--update` 写 3 lock.json + tier2 empty） | `TestUpdate` 3 用例 (`test_lock.py:107-158`) | PASS |
| T1-L2（verify happy path） | `TestVerify.test_verify_after_update_is_zero` (`:167-184`) | PASS |
| T1-L3（sha 稳定 + 多余文件不影响） | `TestShaStability` 2 用例 (`:237-270`) | PASS |
| T1-L4（篡改 → verify 非 0 + stderr 含 mismatch） | `TestVerify.test_verify_detects_tampered_sample` (`:186-213`) + `test_verify_detects_missing_manifest` (`:215-229`) | PASS |
| T1-L5（yaml 缺 → warning） | `TestAcceptanceSyncCheck.test_missing_yaml_is_warning_not_failure` (`:304-333`) | PASS |
| T1-L6（CI 模式 sha mismatch → fail） | `TestAcceptanceSyncCheck.test_ci_mode_sha_mismatch_fails` (`:335-367`) + `test_local_mode_sha_mismatch_only_warns` + `test_sha_match_is_silent` | PASS |
| T1-L7（`--update-acceptance-sync` 仅改 sha+ts，阈值未被改回） | `TestUpdateAcceptanceSync.test_only_updates_sha_and_timestamp` (`:439-458`) — line 458 直接断言 `threshold == 999.99` 未变 | PASS |
| T1-L8（`--update` ⇄ `--update-acceptance-sync` 互斥，stderr 含互斥提示） | `TestArgparseMutualExclusion` 3 用例 (`:482-501`) | PASS（断言 spelling 收敛为 `"not allowed with"`，自纠合理） |

8/8 用例契约对齐；额外补强 12 个测试增强覆盖（manifest sha 一致性 / canonical artifact whitelist / missing acceptance.md / no-mode argparse error / committed sample stable sha / real-repo update→verify 端到端）。

总用例数：20（Phase 0 自带 54 + Phase 1 新增 20 = 74）。

### Approved-facts 锁清单遵守

| 锁条目 | 验证 |
|---|---|
| [code-phase-0] `_schemas.{ManifestEntry,TierManifest}` 复用，禁止平行重定义 | `lock.py:33` 直接 import + `model_validate` / `model_dump(mode="json")`；无平行定义 ✓ |
| [code-phase-0] `_common.write_json` 默认 sort_keys=True 保 sha 稳定 | `cmd_update:144` 用 `write_json(...)`；T1-L3 双更新断言 sha 一致 ✓ |
| [code-phase-0] `conftest.py` autouse strip MERGE_DEV | `test_lock.py` 仅 `monkeypatch.setenv("CI","true")` 与 `delenv("CI",...)`；不重复 strip MERGE_DEV ✓ |
| [code-phase-0] `_fork_name_check.WHITELIST_RELATIVE_PARTS` 含 `tests/eval/datasets/` | sample 落 datasets/ 下；fork-name-check 跳过；又因 sample 内容本身全 generic（python greet/add 例子），即使白名单失效也安全 ✓ |
| [plan-amend] gate.kind soft 语义 | Phase 1 不实现 gate.py；本 Phase 仅校验 `synced_with_sha` 字段，不假设 thresholds 内部 schema ✓ |
| [plan] CI 模式 sha mismatch → fail；本地仅 warn | `_check_acceptance_sync:236` `return 1 if is_ci else 0`；T1-L5/L6 + sha-match 三向覆盖 ✓ |
| [plan] `merge_state.json` 不存在 / `MERGE_HOME` 不存在 / Memory=db 单文件 | grep 全模块无误用 ✓ |

---

## 测试结果

- **pytest**：`tests/eval/unit/` 74/74 PASSED（in 0.22s）
- **覆盖率**：`--cov=scripts/eval` 总 94.56%（≥ 80%）
  - `_schemas.py` 100%
  - `_common.py` 94%
  - `_fork_name_check.py` 90%
  - `lock.py` 93%（未覆盖：`_sample_sha256` 缺文件分支 line 88、死函数 `_file_sha256_or_empty` line 101、`cmd_verify` 中 sample 仅 lock 或仅 disk 两个分支 183-191、acceptance.md missing in `_check_acceptance_sync` line 222-225、`main` 终态 fall-through 328-329）
- **mypy**：`scripts tests/eval` strict, 13 files, 0 error
- **ruff check**：All checks passed
- **ruff format --check**：13 files already formatted
- **fork-name-check**：`python -m scripts.eval._fork_name_check scripts/eval tests/eval` exit 0
- **Sample reproducibility**：现场 `tarfile.open` 拆 4 个 tar，全部 mtime=1767225600 / uid=gid=0 / mode 0o644，内容 generic（greet helper / add helper 等）

---

## 已通过事实（详见 locks/approved-facts.md，本轮新增 6 条 [code-phase-1] 标签）

详见锁清单追加段落。

---

## P0 / P1 / P2 分级问题

### P0（必须修复才通过）
无。

### P1（强烈建议修复）
无。

### P2（可选优化，不阻塞）

**P2-1（必须在 Phase 2 之前清理，触发 [feedback_dead_code_check] 锁清单）**：`_file_sha256_or_empty` 死函数

- **现状**：`scripts/eval/lock.py:99-101` 定义但全代码库零 caller（grep 验证仅自身一行）。无单测引用、无对应 GO 条件、未在 v1.md "Phase 2 续接锚点" 中列出。
- **触发的锁清单条**：用户 memory `feedback_dead_code_check.md`（"评估功能已实现前先 grep 生产 callers"），历史多次"defined-but-never-wired"教训。
- **影响等级**：P2（不阻塞 Phase 1 — GO 条件 + 契约 + 测试 + 范围全绿；但若不清理会成为 Phase 2+ 的 cruft 信号）
- **要求**：Phase 2 实施时必须二选一 —
  - (A) `_ground_truth.py` / `prepare.py` 真实接入（如校验 expanded `working_tree/` 单文件 sha），同时补单测；或
  - (B) 在 Phase 2 提交里直接删除 `_file_sha256_or_empty` 函数定义。
- **锚点**：`scripts/eval/lock.py:99-101`

**P2-2**：`cmd_update_acceptance_sync` 直接 `acceptance_yaml.write_text` 非原子

- **现状**：`lock.py:255-258` 用 `write_text` 写 yaml，未走 `_common.atomic_write_text`，理论上多 reader 场景可能读到半文件。
- **影响**：实际场景 `--update-acceptance-sync` 是单人手动命令，并发风险≈0。Phase 0 已建立的 atomic IO 范式应延伸到所有 lock 写路径。
- **建议**：Phase 6（创建 `acceptance_thresholds.yaml`）或更早顺手改成 `atomic_write_text`。
- **锚点**：`scripts/eval/lock.py:255-258`

---

## 计划自纠评估

| 自纠项 | 评估 | 接受度 |
|---|---|---|
| `lock.py` 250→334 行 | 4 子命令 argparse boilerplate + 12 函数 + docstring 密度合理；功能闭合于 plan §Phase 1，未越权扩 Phase 6 schema；总文件 ≤ 800 行硬上限 | **接受** |
| argparse 替代 click | `_fork_name_check.py` 已用 argparse；click 不在项目依赖；test FINAL T1-L8 也以 "argparse 拒绝" 为锚点 | **接受** |
| sha 算法 `name + \0 + len(8B BE) + bytes` | 防边界混淆是合理工程实践；`T1-L3 + canonical_artifacts_only` 双覆盖 | **接受** |
| argparse 互斥 stderr "not allowed with" 而非 "mutually exclusive" | argparse stdlib 默认输出文案；功能等价（互斥 + 退出非 0） | **接受**，但建议在锁清单留痕（已加 [code-phase-1] 第 6 条） |

---

## 残留风险

| ID | 风险 | 影响 | 处置 |
|---|---|---|---|
| RR1 | `_file_sha256_or_empty` 死函数 | 长期 cruft 信号 | P2-1 列下 Phase 必清单；锁清单留痕 |
| RR2 | `cmd_update_acceptance_sync` 非原子写 | 并发 reader 半文件可能 | P2-2 顺手改即可 |
| RR3 | T1-L7 fixture 直接以 dict 写法构造 yaml（未走 test FINAL §17.X factory）| 测试可读性 | factory 文件暂未实施；Verifier v3 落地后可重构，本轮不阻塞 |
| RR4 | `t3-m3-0001` 的 `fork.patch` 0 字节 | 可能与未来 prepare.py 的 patch apply 流程产生 edge case | meta.yaml 已注明 "fork did not modify either file"；Phase 2 prepare 需断言空 patch 接受 |

---

## 副作用核查

`git diff --name-only HEAD~1 HEAD` 列出 12 个文件，全部位于：

- `scripts/eval/lock.py`（1 文件，新建）
- `tests/eval/unit/test_lock.py`（1 文件，新建）
- `tests/eval/datasets/tier1/samples/t1-0001/{base,upstream,fork,golden}.{tar,patch} + meta.yaml`（5 文件）
- `tests/eval/datasets/tier3/adversarial/t3-m3-0001/{base,upstream,fork,golden}.{tar,patch} + meta.yaml`（5 文件）

**未触碰**：`src/` / `doc/evaluation/` / `.multi-agent/` / `pyproject.toml` / `.github/workflows/` / Phase 0 已交付的 17 文件中的任何一个。
**未引入新运行时依赖**：`yaml` 已是项目依赖，`tarfile` / `hashlib` / `argparse` / `datetime` 均 stdlib。

合规。

---

## 二审及之后

本轮为首审，无上轮反馈核查项。

---

## 通过决定

✅ Phase 1 v1 通过审查。
- copy `v1.md` 到 `code/phase-1/FINAL.md`
- 追加 6 条新事实到锁清单（带 `[code-phase-1]` 标签，含死函数告警）
- 通知 executor + team-lead，Phase 2 必须先处置 P2-1（接入或删除 `_file_sha256_or_empty`）
