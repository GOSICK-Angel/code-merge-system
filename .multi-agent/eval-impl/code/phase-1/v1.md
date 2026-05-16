# Phase 1 实施报告 v1

## commit
`1101b2aa8a46412a5dee8b99ce82c5b93aa2add8` — feat(eval): Phase 1 — lock.py + tier1/tier3 reference samples

## 新增/修改文件清单

### scripts/eval/
- `scripts/eval/lock.py` — 334 行（**超 plan ≤ 250 上限，详见 §"计划细节自纠"**）

### tests/eval/
- `tests/eval/unit/test_lock.py` — 547 行（20 用例：T1-L1..L8 + 必要补强）

### tests/eval/datasets/tier1/samples/t1-0001/
- `base.tar` 10240B — 含 `hello.py`：原始 `def greet(name)`
- `upstream.patch` 226B — 改 `def greet(name, loud=False)` + 实现
- `fork.patch` 213B — 仅在 `greet` 上方加注释
- `golden.tar` 10240B — 合并版（保留 fork 注释 + 应用 upstream 签名）
- `meta.yaml` 247B — `tier=1, category=C, expected_human=false`

### tests/eval/datasets/tier3/adversarial/t3-m3-0001/
- `base.tar` 10240B — 含 `lib.py: def add(a,b)` + `caller.py: print(add(1,2))`
- `upstream.patch` 160B — 改 `def add(a,b,c=0)`
- `fork.patch` 0B — 空（fork 未动）
- `golden.tar` 10240B — 接受 upstream，caller 自然兼容
- `meta.yaml` 486B — `tier=3, loss_class=M3, category=C`

合计 12 个新文件 / 924 行新增 / 0 行删除。**未触碰 src/、doc/evaluation/、.multi-agent/、Phase 0 已交付的任何文件。**

## 测试结果

```
pytest tests/eval/unit/ —— 74 passed (54 from Phase 0 + 20 new)
pytest --cov=scripts/eval —— 94.57% (>= 80%)
  - scripts/eval/__init__.py             100%
  - scripts/eval/_common.py               94%
  - scripts/eval/_fork_name_check.py      90%
  - scripts/eval/_schemas.py             100%
  - scripts/eval/lock.py                  93%   (新增)
mypy scripts tests/eval —— Success: no issues found in 13 source files (strict)
ruff check scripts tests/eval —— All checks passed
ruff format --check scripts tests/eval —— 13 files already formatted
python -m scripts.eval._fork_name_check scripts/eval tests/eval —— exit 0
```

## 契约对齐

| 契约项 | 来源 | 实施位置 | 状态 |
|---|---|---|---|
| `lock.py --verify` 退出 0 / 篡改后非 0 | plan §Phase 1 GO §1 | `cmd_verify` + T1-L2/L4 | ✅ |
| `lock.py --update` 写出 3 份 `tier{1,2,3}.lock.json` | plan §Phase 1 GO §2 | `cmd_update` + T1-L1 | ✅ |
| 单测覆盖 ≥ 80% | plan §Phase 1 GO §3 | lock.py 93% / 整体 94.57% | ✅ |
| `--verify` 检测 yaml 不存在时 warning（不 fail，本地）| plan §Phase 1 GO §3 + 决策 7 | `_check_acceptance_sync` + T1-L5 | ✅ |
| `--update-acceptance-sync` 仅更新 sha + 时间戳 | Verifier T1-L7 + plan 决策 7 | `cmd_update_acceptance_sync` + TestUpdateAcceptanceSync | ✅ |
| `--update` 与 `--update-acceptance-sync` 互斥 | Verifier T1-L8 | argparse mutually-exclusive group + TestArgparseMutualExclusion | ✅ |
| sha 算法集中在 lock.py 单一函数 | Verifier TR5 | `_sample_sha256` + `_sha256_file`，全模块仅这两处调用 hashlib | ✅ |
| Tier-1 `t1-0001` + Tier-3 `t3-m3-0001` reference samples | plan §Phase 1 交付物 | datasets/{tier1,tier3} 全 5 件套 | ✅ |
| sample 内容 generic（无 cvte/dify/insforge）| 派单 §纪律 + facts.md §9 | 全 generic Python；fork-name-check exit 0 | ✅ |
| tar 用 Python tarfile + reproducible | 派单 §纪律 + Verifier T1-L3 | `tarfile.USTAR_FORMAT` + 固定 mtime 1767225600 | ✅ |

### Approved-facts 锁清单遵守对照
| 锁条目 | 实施侧验证 |
|---|---|
| [code-phase-0] `_schemas.ManifestEntry`/`TierManifest` frozen 复用 | `lock.py` 直接 `model_validate` / `model_dump(mode="json")`，无平行重定义 ✅ |
| [code-phase-0] `_common.write_json` 默认 sort_keys=True 保证 sha 稳定 | `cmd_update` 用 `write_json(..., manifest.model_dump(mode="json"))` ✅；T1-L3 验证两次 update 内的 `content_sha256` 完全一致 |
| [code-phase-0] `conftest.py` autouse 已 strip MERGE_DEV | `test_lock.py` 不重复 monkeypatch.delenv("MERGE_DEV") ✅ |
| [code-phase-0] `_fork_name_check.WHITELIST_RELATIVE_PARTS` 含 `tests/eval/datasets/` | sample 文件落 datasets/ 下，fork-name-check 跳过；通用 sample 内容也无 forbidden token ✅ |
| [plan-amend] gate.kind soft 语义 | Phase 1 不实现 gate.py；`AcceptanceThresholds` schema 由 Phase 6 决定如何扩展 kind 字段，本 Phase 不动 ✅ |
| [plan] CI 模式 sha mismatch → fail；本地仅 warn | `_check_acceptance_sync` 以 `os.environ.get("CI")=="true"` 区分；T1-L5/L6/T-sha-match 三向覆盖 ✅ |

## 计划细节自纠

| 计划原文 | 实际 | 采用理由 | 锚点 |
|---|---|---|---|
| `scripts/eval/lock.py` ≤ 250 行 | 实际 334 行 | 单文件 12 函数（4 hash 工具 + 2 manifest builder + 3 cmd_* + 1 stderr helper + 1 argparse builder + 1 main）+ 4 子命令的 argparse boilerplate（每个 `--datasets/--manifests/--acceptance/--acceptance-thresholds` help 文本占 4-6 行）+ 12 块 docstring + 46 空行。功能完全符合计划 §Phase 1 与 Verifier T1-L1..L8 用例要求；行数预估偏紧。可考虑下个 Phase 拆出 `_acceptance_sync.py`，但目前耦合度低（仅在 verify / update-acceptance-sync 两处调用），拆分会引入 cross-module dependency 反而损害可读性。 | `lock.py:1-334` |
| 派单消息说 "Read scripts/eval/_fork_name_check.py 学习 click CLI 写法" | 实际用 `argparse` 而非 `click` | `_fork_name_check.py` 自身用的就是 argparse（不是 click）；Verifier T1-L8 也以 "argparse 拒绝 / mutually exclusive" 为断言锚点；用 argparse 无新依赖且与已有风格一致 | `lock.py:275-322` |
| 计划只提"按 sample 算 sha256" | 实际 `_sample_sha256` 用 `name + \0 + len(8B big-endian) + bytes` 拼接所有 5 个 artifact 后 sha | 防止"长度延伸"与"边界混淆"攻击：纯 concat 时 `(a="ab",b="c")` 与 `(a="a",b="bc")` sha 相同。此细节也让 `T1-L3 sha256 算法稳定` + `T-canonical-only` 测试可校验 | `lock.py:80-102` |
| Verifier T1-L8 期望 stderr 含 "mutually exclusive" | argparse 默认输出 "not allowed with argument" | 这是 stdlib argparse 行为，无法改写 spelling 而保留功能；测试中已收敛断言为 `"not allowed with"` 子串 + 退出码 ≠ 0，与 Verifier 意图一致（退出非 0 + 提示互斥） | `test_lock.py:518-526` |

## 范围合规

- ✅ 未修改 `src/` 任何代码
- ✅ 未修改 `doc/evaluation/{README,metrics,dataset,procedure,acceptance}.md`
- ✅ 未修改 Phase 0 已交付的任何文件（`_common.py` / `_schemas.py` / `_fork_name_check.py` / `conftest.py` / Phase 0 的 3 个 test 文件）
- ✅ 未在代码出现 `cvte` / `dify` / `insforge`（`_fork_name_check scripts/eval tests/eval` exit 0 已验证；sample 内容 100% generic）
- ✅ 未新增运行时依赖（`yaml` 已是 Phase 0 项目依赖；`tarfile` / `hashlib` / `argparse` / `datetime` 均 stdlib）
- ✅ 未 `git add -A`，所有 add 都是显式文件清单（12 文件）
- ✅ 未跳过 pre-commit hook
- ✅ 未超出 Phase 1 范围（`prepare.py` / `run.py` / `_ground_truth.py` 等是 Phase 2+）

## Phase 2 续接锚点

Phase 2 (`prepare.py` + `_ground_truth.py`) 直接可用：

- `scripts.eval.lock._sample_sha256(sample_dir)` — 校验 sample 在 prepare 展开前与 lock 一致
- `scripts.eval.lock.ARTIFACT_FILES` 元组 = `("base.tar","fork.patch","golden.tar","meta.yaml","upstream.patch")` — 单一来源；prepare 解 tar 时按此列表枚举
- `scripts.eval.lock.TIER_LAYOUT[tier]` — 按 tier 解析 sample container 路径
- `scripts.eval._common.{read_json,write_json,atomic_write_text,resolve_workdir}` — workdir 与 manifest IO
- `scripts.eval._schemas.{ManifestEntry,TierManifest}` — manifest 反序列化
- `tests/eval/datasets/tier1/samples/t1-0001/` 与 `tier3/adversarial/t3-m3-0001/` — Phase 2 可直接复用作 fixture，golden.tar 解出后是 ground truth 比对基线

**Phase 2 测试线索**：Verifier v2 §3 / §4 的 `T2-G1..G3 + T2-P1..P6` 矩阵；当前 sample 的 base/fork/upstream/golden 文件结构已 reproducible，可用于损坏-patch、缺失-golden、lock-不一致三类异常的真实数据驱动。

**已知遗留 / 留给 Phase 6**：
- `tests/eval/manifests/acceptance_thresholds.yaml` 本 Phase **未**创建（plan §Phase 6 才创建）；Phase 1 `--verify` 在缺失时仅 warning，符合 GO 条件
- Phase 6 创建 yaml 后，必须按 [plan-amend] kind=absolute|relative discriminator 设计；本 Phase `_check_acceptance_sync` 仅校验 `synced_with_sha` 字段，对 thresholds 内部 schema 不做假设
