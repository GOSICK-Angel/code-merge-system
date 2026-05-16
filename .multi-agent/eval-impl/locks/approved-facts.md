# Approved Facts — eval-impl

> 跨阶段累积"已通过事实锁清单"。三个 Gatekeeper 在通过审查时**追加**新事实到本文件。
> 后续 Gatekeeper 启动时 first action 是 Read 本文件以重建跨阶段事实基线。
> 本文件仅追加，不覆盖；任何被审者不得修改本文件。

---

## [bootstrap] 初始事实

由 main agent 在 `facts.md` 中已确认，列入基线（不重复全文，详见 facts.md）：

- 评估方案文档锁版本：`doc/evaluation/{README,metrics,dataset,procedure,acceptance}.md` @ 2026-05-15
- 落地范围：仅新建 `scripts/eval/` + `tests/eval/`，不动 `src/`
- 工程纪律：mypy strict / pytest --cov-fail-under=80 / 文件 ≤ 800 行 / 无中文注释 / pydantic v2

---

## [plan] v2 通过审查新增事实（2026-05-15，gatekeeper-plan）

来源：`.multi-agent/eval-impl/plan/v2-review.md` Step 1 事实核查。后续 teammate 不得改写以下结论；如发现冲突，先 SendMessage 给 main agent 报锁清单 regression。

### 真实产物名（per-run artifact）

- [plan] `merge_report_<run_id>.json` 与 `merge_report_<run_id>.md` 是 `merge` CLI 真实产物名（`src/tools/report_writer.py:424,542`）。**`merge_state.json` 不存在**，任何 plan/test/code 引用此名为错。
- [plan] `plan_review_<run_id>.md` 是 plan review 真实产物名（`src/tools/report_writer.py:601-608`）。
- [plan] `checkpoint.json` 是 checkpoint 文件名（`src/cli/paths.py:79`、`src/cli/commands/resume.py:105`）；非 `merge_state.json`。

### 模式开关与隔离机制

- [plan] dev/prod 模式开关 env 是 `MERGE_DEV=1`（`src/cli/paths.py:39`）。**`MERGE_HOME` 不存在**，任何代码引用为错。
- [plan] `is_dev_mode(repo_path)` 在 `repo_path != pkg_root`（`Path(__file__).resolve().parents[2]`）时返回 False；评估子进程 cwd 在隔离 tmp 目录，自动走 prod 模式产物路径 `<cwd>/.merge/runs/<run_id>/`。
- [plan] `repo_path="."` 在 `src/cli/main.py:159` 硬编码注入到 `detect_or_setup`；下游所有 `paths.py` 调用通过 `state.config.repo_path` 流转。

### Memory 形态

- [plan] Memory 是 SQLite 单文件 `<repo>/.merge/memory.db`（prod，`paths.py:127-130 get_project_memory_db_path`），**不是目录** `.merge/memory/`。
- [plan] sidecar `memory_hit_stats.json` 同样写在 `<repo>/.merge/`（`paths.py:138 get_project_hit_stats_path`）。
- [plan] `<workdir>/home/.claude/projects/` 是 Claude Code 自身的 memory 路径，与 merge 系统**无关**，不要在评估隔离中删除该路径。

### `--ci` 输出能力边界

- [plan] `merge --ci` stdout JSON 由 `src/tools/ci_reporter.py:8-60 build_ci_summary` + `format_ci_summary` 产生，**仅含 run-level 字段**（status / run_id / total_files / auto_merged / human_required / human_decided / failed_count / judge_verdict / errors[-5:]）；**不含 per-file decisions / rationale / discarded_content**。
- [plan] per-file 真相必须读 `merge_report_<run_id>.json`（含完整 `MergeState.file_decision_records`）；stdout JSON 仅可作 sanity-check。

### CI 现状

- [plan] `.github/workflows/ci.yml` `test` job 显式跑 `pytest tests/unit/`、`ruff check src/`、`ruff format --check src/`、`mypy src`；**`tests/eval/` 与 `scripts/` 默认完全在 CI 之外**。任何 evaluation 落地必须显式新增 CI step，不能假设 `pytest tests/` 自动覆盖。
- [plan] `tool.coverage.run.source = ["src"]` 是 pyproject 默认；新增 `--cov=scripts/eval` flag 会 override（pytest-cov 标准行为）。

### 现 CLI 不存在的 flag

- [plan] `merge` CLI **无** `--no-cache` 标志（`grep --no-cache src/cli/` 无匹配）；procedure.md §6.2 引用的 `--no-cache` 是评估侧建议，非现状。本次落地的 cache 隔离只能依赖独立 workdir + Anthropic 5min TTL；硬隔离 out-of-scope。

### M1-M6 detector 映射

- [plan] M1-M6 六类丢失 detector 文件全部存在（`src/tools/scar_list_builder.py` / `shadow_conflict_detector.py` / `interface_change_extractor.py` / `reverse_impact_scanner.py` / `three_way_diff.py` / `config_line_retention_checker.py` / `gate_runner.py`），与 `facts.md §6` 映射一致。

---

## [plan-amend] gate.py soft gate 语义扩展（2026-05-15，team-lead 决策）

来源：Verifier P0-4 决策歧义请示。team-lead 决策方案 C：soft gate 区分 `kind: absolute | relative`，baseline 可选。

- [plan-amend] `acceptance_thresholds.yaml.soft_gates[].kind` ∈ {`absolute`, `relative`}（必填）
- [plan-amend] `kind: absolute` 项含 `threshold` 字段；判定 `value < threshold` → 不达
- [plan-amend] `kind: relative` 项含 `multiplier` 字段（如 1.15 / 1.20）；判定 `value > baseline_value × multiplier` → 不达
- [plan-amend] `gate.py --baseline` **可选**；缺 baseline 时 `kind: relative` gate 全部 SKIP（`eval_acceptance_*.json` 写 `pass: null, skipped_reason: "no baseline"`），不影响 verdict
- [plan-amend] hard gate 永远绝对，无 baseline 概念
- [plan-amend] exit 2 触发条件：(任一 absolute soft gate < threshold) OR (任一 relative soft gate 有 baseline 且 > baseline × multiplier)
- [plan-amend] exit 1 仍只对应 hard gate fail，优先级最高
- [plan-amend] cost_usd_per_run_p95 (multiplier 1.15) + wall_time_seconds_p95 (multiplier 1.20) 是首批 relative soft gates，对齐 acceptance.md §2

**对 Executor 的影响**（Phase 6 实施时）：`_schemas.py` 中 `SoftGate` 模型按上述加 `kind` discriminator；`gate.py` 实现按上述路由。**对 Verifier 的影响**（v2 修订）：T6 系列补 "缺 baseline + relative gate skip" + "baseline 存在 + cost 超阈" 两条用例。

---

## [code-phase-0] Phase 0 v1 通过审查新增事实（2026-05-15，gatekeeper-code）

来源：`.multi-agent/eval-impl/code/phase-0/review-v1.md` Step 1-5 核查 + commit `5658e7745cd4e54664444a20c6e8b67c13fe6df3`。后续 teammate 不得改写以下结论。

- [code-phase-0] `scripts/eval/_common.py:40-70 eval_subprocess_env(*, base_env=None, use_real_keys=False) -> dict[str, str]` 是 Phase 3 启动 `merge` 子进程的唯一 env 工厂；强制 `env.pop("MERGE_DEV", None)` + 默认注入 `DUMMY_LLM_KEY="DUMMY-EVAL-KEY"` 到 `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`；签名 keyword-only（不是原计划 `**kwargs`）。Phase 3 `run.py` 必须调用此函数，**不得**自己重新拼 env。
- [code-phase-0] `scripts/eval/_schemas.py` 已落地 11 个 frozen pydantic v2 模型（`extra="forbid"`）：`SystemDecision` / `DiffEntry` / `DiffReportMeta` / `DiffReport` / `GateKind` / `GateOperator` / `GateVerdict` / `GateResult` / `AcceptanceReport` / `RunMeta` / `ManifestEntry` / `TierManifest` / `AcceptanceThresholdEntry` / `AcceptanceThresholds`。后续 Phase **直接复用**，禁止平行重定义；新增字段须遵守 `frozen=True, extra="forbid"`。
- [code-phase-0] `scripts/eval/_fork_name_check.py:35` 词边界正则 `r'\b(cvte|dify|insforge)\b'`（IGNORECASE）；`SELF_BASENAMES = {"_fork_name_check.py", "test_fork_name_check.py"}`（白名单基于 basename，不是绝对路径）；`WHITELIST_RELATIVE_PARTS` 仅含 `tests/eval/datasets/` 与 `tests/eval/fixtures/`（**不**包含 `tests/eval/unit/`，单测中除自身外仍受查）。Phase 9 CI 直接 `python -m scripts.eval._fork_name_check scripts/eval tests/eval`。
- [code-phase-0] `scripts/eval/_common.py:103-121 write_json` 默认 `sort_keys=True, indent=2`，atomic via `tempfile.mkstemp + os.replace`；Phase 1 `lock.py` 写 `tier{N}.lock.json` 必须复用此函数以保证 sha 稳定。读侧用 `read_json`（不静默 JSONDecodeError）。
- [code-phase-0] `tests/eval/conftest.py:21-31` autouse fixture `_strip_merge_dev` 已对所有 `tests/eval/**` 测试生效；后续 Phase 单测**无需**再单独 `monkeypatch.delenv("MERGE_DEV")`。Phase 0 测试集 `pytest tests/eval/unit/` 基线 = 54 用例 / 95.58% cov / mypy strict 0 error / ruff clean。

## [test-amend] v2 强制通过 + 文档整合差异权威裁决（2026-05-15，team-lead 卡点裁决）

**触发**：gatekeeper-test 3 轮 NO-GO 告警（P0-1 用例数 §A 92 / §0 97 / §A.1 100 三处打架；P0-2 §A.1 表宣告 T6-G9/G10/G11 但 §7.1 正文未落）。Verifier 实质修复（SRSR / memory.db / RR / P1/P2）已全部到位，仅文档整合卡住。

**裁决**：v2 强制通过，**v2.md copy 到 test/FINAL.md**。后续 Phase 实施按下列权威：

- [test-amend] 用例总数权威 = **100**（§A.1 增量表口径）；§A=92 / §0=97 系文档整合残留，**忽略**
- [test-amend] Phase 6 gate.py 测试用例权威 = §A.1 增量表 + 方案 C plan-amend；§7.1 正文 T6-G6/G8 旧措辞作废
- [test-amend] T6-G6 = absolute soft 不达 threshold → exit 2
- [test-amend] T6-G8 = 缺 baseline + relative → SKIP（pass=null + skipped_reason）
- [test-amend] T6-G9 = baseline 存在 + cost_p95 超 baseline×1.15 → exit 2
- [test-amend] T6-G10 = baseline 存在 + cost_p95 在阈值内 → exit 0
- [test-amend] T6-G11 = hard fail + soft fail 并存 → exit 1（hard 优先）
- [test-amend] T0-S4 = yaml schema 含 `kind` 字段必填；T0-S4b/S4c = 非法 kind / relative 缺 multiplier 校验
- [test-amend] T4-D10 SRSR 字段在本期用 fixture 注入，待 plan v3 决策真实生成路径（follow-up）

**对 Phase 6 / Phase 0 后续 Executor 的影响**：实施时以 test/FINAL.md (=v2.md) 为底，但用例数 / Phase 6 用例语义按上述 [test-amend] 条目为准。gatekeeper-code 审 Phase 6 时对照 §A.1 + 上述 amend，不要按 §7.1 旧措辞挑刺。

## [test] v2 二审通过新增事实（2026-05-15，gatekeeper-test）

来源：`.multi-agent/eval-impl/test/v2-review.md`（v2 二审）+ team-lead `[test-amend]` 卡点裁决回放确认。Verifier 在 team-lead 裁决后实质把 §A.1 v2.1 增量完整落到 v2.md 正文（T0-S4/S4b/S4c + T6-G6/G8/G9/G10/G11 + §16.2 cost_p95 升级 + §17.3-prime fake_merge_bin 收敛 + §0a Meta 独立分类）。本审在 amend 之上做最终核对并固化以下事实。

- [test] 用例总数权威更新：**102**（v2.md §0 / §0a / §15 三处一致）。`[test-amend]` 写的"100"是 verifier 当时承诺数；落地 v2.md 实际 +5 v2.1 用例后是 102。Executor / gatekeeper-code 后续以 102 为基线对账，不再以 100 或 §A 表 line 18 旧值"92"为准。
- [test] Phase 6 gate.py 用例已在 v2.md §7.1 line 369-423 正文完整落地，含：
  - T6-G6（line 369）absolute soft 不达 threshold → exit 2
  - T6-G8（line 380）缺 baseline + relative → SKIP（pass=null + skipped_reason="no baseline" + meta.baseline_used=null）
  - T6-G9（line 392）baseline + cost 超 1.15× → exit 2 + computed_threshold/baseline_value 字段透传
  - T6-G10（line 407）baseline + cost 在 1.15× 内 → exit 0
  - T6-G11（line 416）hard fail + soft fail → exit 1（hard 优先于 soft 的 2）
  Executor Phase 6 实现时按本段行号定位用例正文，**不**再按 §A.1 摘要。
- [test] Phase 0 schema 已落地（gatekeeper-code [code-phase-0] 段已固化）；本审对照确认 v2.md T0-S4 全 18 项 id（hard 9 + soft 9）+ kind 字段约束与已实现的 `_schemas.AcceptanceThresholds` 兼容；Executor Phase 6 加 `kind/multiplier` 字段时直接扩展该模型，不平行重定义。
- [test] fake_merge_bin 文件名约定权威 = §17.3-prime（v2.md line 651-668）：fixture 落盘命名 **固定字面量** `merge_report_FIXTURE.json` / `merge_report_FIXTURE.md` / `plan_review_FIXTURE.md`；shell 脚本 cp + rename 到 `merge_report_${RUN_ID}.json` 等运行时名；**禁止** glob fallback。Phase 3 / Phase 8 fixture 与 Executor 必须按此约定。
- [test] 金字塔分类权威 = §0a 修订版：Unit / Integration / **Meta（独立第三档，不计入推荐 60/25/15）**；T9-W1..W7 全部归 Meta（yml 静态校验本质 unit-like，但跨"代码 ↔ 工程基础设施"边界单列）。后续 review 不再争论"meta 是 e2e 还是 unit"。
- [test] SRSR 数据流是 follow-up（TR7）：T4-D10 fixture 假设 `MergeState.snapshot_rollback_events: list[SnapshotRollbackEvent]` 字段在 plan v3 落地；fixture 已写入（`tests/eval/fixtures/dummy_run/runs/t1-rollback-0001/merge_report_<run_id>.json`，v2.md §17.7）；如 plan team 拒绝该字段，fixture 改为 `MergeState.errors[]` 字符串模式 `'rollback:{file}:{ok|fail}'`（兼容现 schema）。本期 Executor Phase 4 实现 `diff_against_golden.py` 时**优先**走 `snapshot_rollback_events` 字段；plan v3 决策前不阻塞 Phase 6/8。
- [test] 残留文档 stale 不阻断 Executor：v2.md §A line 18 仍写"= 92"、§A.1 line 47 仍写"97 → 100"、§0a line 91-93 残留旧"Unit 89.7% / Meta 归 e2e"措辞。这些是历史变更摘要段的整合残留，正文 §0 / §0a 表 / §7.1 用例正文 / §15 全部正确（102）。Executor 按正文实施，忽略 §A 历史段冲突。

---

## [code-phase-1] Phase 1 v1 通过审查新增事实（2026-05-15，gatekeeper-code）

来源：`.multi-agent/eval-impl/code/phase-1/review-v1.md` Step 1-5 核查 + commit `1101b2aa8a46412a5dee8b99ce82c5b93aa2add8`。后续 teammate 不得改写以下结论。

- [code-phase-1] `scripts/eval/lock.py` 落地 4 子命令（互斥 group, required=True）：`--update` / `--verify` / `--update-acceptance-sync`；4 共享 path 选项 `--datasets` / `--manifests` / `--acceptance` / `--acceptance-thresholds`，默认值锚定 `Path(__file__).resolve().parents[2]` = repo root。Phase 2-9 任何脚本扩展互斥语义时**不得**新加第 4 个动作（避免破坏 T1-L8）；要新增子命令请改用 sub-parsers。
- [code-phase-1] sample sha256 算法 = `sha256( for name in ARTIFACT_FILES: name.encode("utf-8") + b"\0" + len(data).to_bytes(8,"big") + data )`，`ARTIFACT_FILES = ("base.tar","fork.patch","golden.tar","meta.yaml","upstream.patch")` 字典序固定。Phase 2 `prepare.py` / `_ground_truth.py` 校验 sample 完整性必须复用 `lock._sample_sha256(sample_dir)`，**不得**自己实现 hash。修改 `ARTIFACT_FILES` 元组或顺序 = 全量 lock.json 失效。
- [code-phase-1] reference samples 已落 commit：`tests/eval/datasets/tier1/samples/t1-0001/`（greet helper：upstream 加 loud kwarg / fork 加 comment / golden 综合）+ `tests/eval/datasets/tier3/adversarial/t3-m3-0001/`（lib add(a,b) → add(a,b,c=0) / caller 不动 / loss_class M3）。tar 全部 USTAR_FORMAT + mtime=1767225600 + uid=gid=0 + mode=0o644，sha 跨主机稳定。Phase 2+ 直接复用作 fixture；t3 `fork.patch` 是 0 字节文件（合法："fork did not modify either file"），prepare 必须接受空 patch。
- [code-phase-1] `cmd_verify` CI 区分 = `os.environ.get("CI") == "true"` 严格匹配（GitHub Actions 默认值）；`_check_acceptance_sync` 在 `acceptance_thresholds.yaml` 不存在时返回 0 + 仅 warning（Phase 6 才创建该 yaml）。Phase 6 创建 yaml 后**不需要**改 `_check_acceptance_sync` 行为，只需保证 yaml schema 含 `synced_with_sha` 字段即可被读到。
- [code-phase-1] `cmd_update_acceptance_sync` 仅修改 yaml 顶层 `synced_with_sha` + `synced_at` 两字段，**不**触碰 `hard_gates` / `soft_gates` 子树（T1-L7 显式守护：sentinel `threshold=999.99` 跑完命令后未变）。任何后续工具改 yaml 必须保持此契约；plan §决策 7 / [plan-amend] 已锁定该护栏。
- [code-phase-1] argparse 互斥提示文案 = stdlib 默认 `"argument X: not allowed with argument Y"`（不是 "mutually exclusive"）。test FINAL T1-L8 在 v3 修订时若想严格断言应改成 `"not allowed with"`，否则维持现状（已收敛 v1 实施 + 锁清单留痕）。
- [code-phase-1] **死函数告警** `lock.py:99-101 _file_sha256_or_empty` 全代码库零 caller / 零测试。Phase 2 实施时必须二选一：(A) 真实接入 `_ground_truth.py` 或 `prepare.py` 的 expanded `working_tree/` 单文件 sha 校验并补单测；或 (B) 直接删除该函数定义。命中 [feedback_dead_code_check] 锁清单。Phase 1 GO 通过但 Phase 2 审查时本条作为 carry-over P1 校验项。

---

## [code-phase-2] Phase 2 v1 通过审查新增事实（2026-05-15，gatekeeper-code）

来源：`.multi-agent/eval-impl/code/phase-2/review-v1.md` Step 1-5 核查 + commit `8cb3f845a5d51208bfd33f1a281b9caf2ba4e272`。后续 teammate 不得改写以下结论。

- [code-phase-2] Phase 1 carry-over P2-1（[code-phase-1] 第 7 条）正式 close — 选 (B) 删除 `_file_sha256_or_empty`；同步给 `lock.cmd_verify` 加 try/except `FileNotFoundError` → rc=1（保护 sample 损坏后 verify 失败的边界）。Phase 4 若真要单文件 sha 比对，请新建函数并立即接入 caller + 补单测，不要复活已删的旧函数名。
- [code-phase-2] `scripts/eval/_ground_truth.py` 落地 4 公开 API：`load_meta(sample_dir) -> SampleMeta` / `load_golden_tree(sample_dir) -> dict[str, bytes]` / `load_sample(sample_dir) -> GroundTruthBundle` / 异常基类 `GroundTruthError(sample_id, message)` + `GroundTruthMissing(sample_id, missing)` + `GroundTruthCorrupted`。Phase 4 `diff_against_golden.py` 必须复用此模块读 golden tree，**不得**自己解 tar。tar 提取强制拒绝绝对路径与 `..` 父目录穿越（`_materialise_tar_members:122-126`）。
- [code-phase-2] `scripts/eval/_schemas.py` 新增 3 frozen 模型：`SampleMeta`（sample_id/tier/category/loss_class/expected_human/description；tier 范围 ge=1 le=3；loss_class 仅 Tier-3 必填，Tier-1/2 None）+ `GoldenFileEntry`（relative_path + content: bytes，pydantic v2 默认 base64 序列化）+ `GroundTruthBundle`（meta + tuple[GoldenFileEntry,...]，frozen 用 tuple 而非 list）。Phase 4/5 直接复用，禁止平行重定义；`__all__` 已扩展 3 项。
- [code-phase-2] `scripts/eval/prepare.py` 是**独立 CLI**（`python -m scripts.eval.prepare`），不挤压 lock.py 4 子命令互斥 group（[code-phase-1] 第 1 条锁定）；4 选项 `--tier {1|2|3}`（required choices）/ `--out`（required）/ `--datasets` / `--manifests`，后两默认值锚定 `lock_mod.DEFAULT_*` 同源。每 sample 落 4 件：`working_tree/`（base.tar 解 + fork.patch 应用）+ `golden_tree/`（golden.tar 解）+ `meta.yaml`（atomic_write_text 拷贝）+ `apply_log.txt`（patch 应用 trace）。退出码三态：0=成功 / 1=lock fail（lock-verify 前置） / 2=per-sample expansion fail。
- [code-phase-2] `_apply_patch_to_tree(sample_id, patch_name, patch_bytes, tree)` 契约：第一行 `if not patch_bytes.strip(): return [...no-op...]` 短路接受 0 字节 patch（如 `t3-m3-0001/fork.patch`）；非空但 `len(PatchSet) == 0` 视作 "malformed silent zero-hunk input" 显式抛 `PatchApplyError`；**当前实现 mutate 入参 `tree` dict**（docstring 已声明），Phase 4 复用前应先重构为 return new dict 以符合 immutable 范式。
- [code-phase-2] `cmd_prepare` 用 sentinel 路径 `manifests_dir / "__no_such_acceptance_yaml__.yaml"` 让 `lock.cmd_verify._check_acceptance_sync` 走 "yaml 不存在仅 warn" 分支，回避 Phase 6 之前 yaml 缺失干扰 dataset sha 校验。Phase 6 创建真实 yaml 时建议把 `lock.cmd_verify(acceptance_yaml: Path | None = None)` 签名扩展为可选并改 prepare 传 None 显式跳过；本期保持 sentinel 路径不变。Phase 4+ 不得依赖 sentinel 字面量。
- [code-phase-2] Phase 2 测试基线：`pytest tests/eval/unit/` 101 用例（Phase 0 54 + Phase 1 20 + Phase 2 27）；cov 94.12%（每模块 ≥ 90%；prepare.py 91% / _ground_truth.py 94% / lock.py 升至 94%）；mypy strict 17 files 0 err；ruff clean；fork-name-check exit 0。Phase 3 `run.py` 必须在 `cmd_prepare` 之上 spawn fake merge-bin（用 [code-phase-0] eval_subprocess_env 工厂），不能跳过 prepare 直接消费 raw datasets。

---

## [code-phase-3] Phase 3 v1 通过审查新增事实（2026-05-15，gatekeeper-code）

来源：`.multi-agent/eval-impl/code/phase-3/review-v1.md` Step 1-5 核查 + commit `c3e2c6ca858683b28efbd354dd103ac47433040c`。后续 teammate 不得改写以下结论。

- [code-phase-3] `scripts/eval/run.py` 三层隔离契约：(1) env 经 `eval_subprocess_env(use_real_keys=...)` + 函数级 mutation `env["HOME"]=str(home_dir)` + `env.setdefault("FAKE_SEED", str(seed))`；(2) per-sample HOME=`<workdir>/home`（共享，仅作 placeholder 隔离 `~/.config` 真实凭据）；(3) cwd=`<workdir>/runs/<sample_id>/_cwd`，绝对独立。Phase 4+ 任何 spawn merge subprocess 的代码必须复用此三层契约，**不得**绕过 eval_subprocess_env 自拼 env。
- [code-phase-3] `MEMORY_DB_RELATIVE = Path(".merge/memory.db")`（[plan] memory 是 SQLite 单文件锁定）；`_assert_clean_memory(cwd, sample_id)` 在 spawn 之前调用，存在则抛 `MemoryLeakDetected(sample_id, path)`；`cmd_run._bounded` catch 后转 sample-level rc=99 + run-level rc=1。Phase 4-9 任何后续工具检测 memory 路径必须沿用此常量与异常类型，不平行重定义。
- [code-phase-3] `RunMeta` schema 已扩展为 10 字段（原 7 必备 + status: Literal["success","failed"] + memory_clean_check: Literal["passed","skipped"] + exit_code: int），全部带 default 不破坏 7 必备字段 issubset 断言（T3-R4 守护）。Phase 5 summarize.py 读 RunMeta 时**优先**用 `status` 区分成功/失败，`exit_code` 用于诊断，`memory_clean_check` 当前固定 "passed"（spawn 前已 assert）。
- [code-phase-3] `tests/eval/fixtures/fake_merge_bin/fake_merge.sh` 是 Phase 3+ fake merge CLI 唯一实现，[test] §17.3-prime 字面量 FIXTURE 命名锁定：fixture 落盘 `merge_report_FIXTURE.{json,md}` + `plan_review_FIXTURE.md` + `checkpoint.json`，shell 单 `cp` + 单 `rename` 到运行时 `merge_report_${RUN_ID}.{json,md}` 等。env 控制开关：`FAKE_FIXTURE_DIR`（必填）/ `FAKE_SAMPLE_ID`（必填）/ `FAKE_DUMP_ENV`（写 _env.json + _cwd.txt）/ `FAKE_EXIT_CODE`（spawn 失败模拟）/ `FAKE_BAD_JSON`（stdout 非 JSON）/ `FAKE_TOUCH_MEMORY`（写 .merge/memory.db）/ `FAKE_NO_OUTPUT`（空 stdout）。Phase 8 e2e 必须复用此脚本，**不得**复制粘贴另写一份。
- [code-phase-3] `_persist_ci_summary(stdout_text, dest)` 包装契约：dict 顶层 → 原样写；非 dict 顶层（list/str/int 等） → `{"raw_value": ...}`；非 JSON → `{"invalid_json": True, "raw_stdout": <text>}`。Phase 5 summarize.py 与 Phase 4 sanity-check 读 `runs/<id>/ci_summary.json` 必须感知此包装层；遇 `raw_value` / `invalid_json` 应跳过 sanity check 而非崩溃。
- [code-phase-3] `_locate_merge_run_dir(cwd_merge)` 仅当 `<cwd>/.merge/runs/` 恰有 1 子目录返回；0 / N (N>1) 返回 None。fake_merge.sh 与真实 merge CLI 单 run 单目录都符合此假设；Phase 4+ 若 merge CLI 未来支持单 cwd 多 run，需修改本函数 + 同步更新 [code-phase-3] 第 6 条。
- [code-phase-3] Phase 3 测试基线：`pytest tests/eval/unit/` 121 用例（Phase 0 54 + Phase 1 20 + Phase 2 27 + Phase 3 20）；cov 93.92%（每模块 ≥ 90%；run.py 93%）；mypy strict 19 files 0 err；ruff clean；fork-name-check exit 0。

**Carry-forward 待办（不阻塞 Phase 3 GO）**：
- Phase 4：[code-phase-2] P2-1 仍 open — `prepare._apply_patch_to_tree` mutate 入参 dict；Phase 4 若复用前必须先重构为 return new dict（contract 已锁定）。
- Phase 5：本审 P2-2 — `_persist_ci_summary` 包装层；summarize.py 需感知 raw_value / invalid_json 容错。
- Phase 6：[code-phase-2] P2-3 仍 open — sentinel acceptance_yaml 路径；建议 cmd_verify 改 `acceptance_yaml: Path | None = None`，prepare/run 显式传 None。

---

## [code-phase-4] Phase 4 v1 通过审查新增事实（2026-05-15，gatekeeper-code）

来源：`.multi-agent/eval-impl/code/phase-4/review-v1.md` Step 1-5 核查 + commit `6d3871e3c93226b9380ed07c088ffa6bee92f6db`。后续 teammate 不得改写以下结论。

- [code-phase-4] **Phase 2 carry-forward P2-1 正式 CLOSED**：`prepare._apply_patch_to_tree(sample_id, patch_name, patch_bytes, tree) -> tuple[dict[str, bytes], list[str]]` 重构为 pure function；入参 `tree` dict 通过 `dict(tree)` shallow copy 后操作，绝不 mutate；`_expand_sample:215-217` 同步更新为 `forked_tree, log_lines = _apply_patch_to_tree(...)` + `_write_tree_overlay(working_tree, forked_tree)`；`test_apply_patch_to_tree_does_not_mutate_input` 永久守护契约。Phase 5+ 任何后续调用必须遵守 tuple return；不得回到原 mutation 风格。
- [code-phase-4] `scripts/eval/_ast_equiv.py` 公共 API：`is_equivalent(a: bytes, b: bytes, *, suffix: str) -> tuple[bool, SemanticEngine]` 是无 IO 纯函数 + `is_equivalent_files(a_path, b_path)` 是 IO 包装（raise FileNotFoundError）+ `UnsupportedFileType(suffix)` 异常 + 4 个 frozenset 后缀表（CODE/JSON/YAML/TEXT/BINARY）。`SemanticEngine = Literal["exact-bytes","fallback-bytes","tree-sitter","json-canonical","yaml-canonical"]` 5-元 union；分派优先级：byte-equal 短路 exact-bytes → BINARY 严格字节 → JSON canonical → YAML canonical → CODE 走 tree-sitter（若 `_has_tree_sitter()`）或 fallback → TEXT 走 fallback → 未知后缀抛 `UnsupportedFileType`。`_normalise_bytes` 严格只去：BOM (`\xef\xbb\xbf`) + CRLF→LF + 行尾 `rstrip(b" \t")`，**不**去注释（plan P1-5 守护）。
- [code-phase-4] `DiffReportMeta.semantic_engine` 字段值 narrowed 为 `Literal["tree-sitter","fallback-bytes"]` 2-元（schema 强制）；`diff_against_golden._summarise_engine(engines)` 聚合：空列表 → "fallback-bytes"，全 "tree-sitter" → "tree-sitter"，含任意 fallback / json-canonical / yaml-canonical / exact-bytes → "fallback-bytes"（即"任一非 AST 路径就降级标"，防止 fallback 模式冒充 AST 等价）。Phase 5 summarize 渲染时按此 2-元类型展示。
- [code-phase-4] `scripts/eval/diff_against_golden.py` per-file 真相来源契约：唯一从 `_load_decision_records(run_dir)` = `_locate_merge_report(run_dir)` (glob `merge_report_*.json`，多匹配取 lex-last 保确定性) 读 `payload["file_decision_records"]`。**禁止**从 `ci_summary.json` 取 per-file 数据（[plan] 锁定 + T4-D6 decoy 守护）。Phase 5 / Phase 8 若需 per-file 数据必须沿用此路径，不得另开后门。
- [code-phase-4] **`_ground_truth.load_golden_tree` 是 Phase 4+ 读 golden tree 的唯一入口**：`scripts/eval/diff_against_golden.py` 内 `grep "tarfile"` = **0 命中**，完美遵守 [code-phase-2] 锁清单。Phase 5/6/8 任何代码读 golden 必须复用该函数（含 tar 安全防御 _materialise_tar_members）；禁止平行解 tar 实现。
- [code-phase-4] **MISS_FORK 当前简化为 MISS_UPSTREAM**（架构限制）：在仅 D_sys vs D_gold 两侧字节比对的简单架构下，"gold 有 sys 无" 路径统一归 MISS_UPSTREAM。完整 MISS_FORK 区分需引入 fork.patch + base tree 三方 oracle（Tier-2 历史回放工作）。`_escalate_label` 优先级：WRONG_MERGE(4) > MISS_UPSTREAM(3) > MISS_FORK(2) > EXTRA_NOISE(1)；当前 MISS_FORK 优先级保留但实际无 caller，Tier-2 时启用。
- [code-phase-4] **T4-D10 SRSR 未实现**（[test] TR7 follow-up 明确允许）：本期 `DiffEntry` 未加 `snapshot_rollback_attempted/_succeeded` 字段；`DiffReportMeta` 未加 `srsr_total_attempted/_succeeded` 汇总字段。待 plan v3 决策 `MergeState.snapshot_rollback_events` 字段后由 Executor 在后续 commit 中扩展 schema + 实现 + 补 T4-D10 用例。Phase 5/6/8 实施时**不阻塞**，summarize/gate/e2e 跑通现有 16 项契约即可。
- [code-phase-4] Phase 4 测试基线：`pytest tests/eval/unit/` 156 用例（Phase 0 54 + Phase 1 20 + Phase 2 27 + Phase 3 20 + Phase 4 35）；cov 94.16%（每模块 ≥ 90%；diff_against_golden 96% / _ast_equiv 94%）；mypy strict 23 files 0 err；ruff clean；fork-name-check exit 0。

**Carry-forward 待办（不阻塞 Phase 4 GO）**：
- Phase 5：[code-phase-3] P2-2 仍 open — `_persist_ci_summary` 包装层 (`{"raw_value": ...}` / `{"invalid_json": True}`)；summarize.py 需感知 raw_value / invalid_json 容错。
- Phase 5：本审 P2-1 — `_diff_one_sample` 多文件 sample 仅取第一个 decision record；若 summarize 需多 strategy/risk 聚合需重构。
- Phase 5/Phase 8：本审 P2-3 — `_decision_to_system_decision` 双字段名兜底（`decision` ↔ `strategy`、`target_risk_level` ↔ `risk`）；e2e 跑真实 merge 时验证字段名锁定。
- Phase 6：[code-phase-2] P2-3 仍 open — sentinel acceptance_yaml 路径；建议 cmd_verify 改 `acceptance_yaml: Path | None = None`。
- 后续工作（非本批次）：tree-sitter 真实 AST 集成；MISS_FORK 完整区分（Tier-2 工作）；T4-D10 SRSR 字段（待 plan v3）。

---

## [code-phase-5] Phase 5 v1 通过审查新增事实（2026-05-15，gatekeeper-code）

来源：`.multi-agent/eval-impl/code/phase-5/review-v1.md` Step 1-5 核查 + commit `bf4cfda38fc042261b5864920539ef830b6fa95a`。后续 teammate 不得改写以下结论。

- [code-phase-5] **Phase 3 carry-forward P2-2 正式 CLOSED**：`summarize._load_ci_summary(runs_dir, sample_id)` 识别 [code-phase-3] `_persist_ci_summary` 两种 envelope：`{"invalid_json": True}` 与 `{"raw_value": ...}`；`_detect_known_issues:245-257` 把识别结果写入 eval_report §6 异常段；`TestCiSummaryWrappingAwareness` 2 用例（invalid_json / raw_value）守护契约。Phase 6/8 读 ci_summary 时**优先**通过 `_load_ci_summary` 而非裸 read_json。
- [code-phase-5] `scripts/eval/_report_render.py` 公共 API：`render_report(context: dict, *, templates_dir: Path | None = None) -> str` + 常量 `TEMPLATES_DIR = Path(__file__).resolve().parent / "_templates"` + `TEMPLATE_NAME = "eval_report.md.j2"`。`_build_env` 强制 `StrictUndefined` + `autoescape=False`（markdown 输出，非 HTML）+ `keep_trailing_newline=True`。模板缺 context key 抛 `jinja2.UndefinedError`（T5-R2 守护）。Phase 6 gate.py 若复用模板渲染（如 acceptance summary）必须沿用 StrictUndefined 契约，不得允许默认值兜底。
- [code-phase-5] `scripts/eval/_templates/eval_report.md.j2` procedure.md §3.1 六章节锁定：`## 1. 元信息` / `## 2. 总体指标摘要` / `## 3. 分 tier 结果` / `## 4. 失败案例清单` / `## 5. 与上一基线对比表` / `## 6. 异常 / 已知问题 / 缓解措施`。**18 指标 anchor 锁定**：hard 9（OA/WMR/MMR/WDR/SSER/DCRR/SRSR/RR/RCR）+ Recall_M1..M6 + soft 9（OA/CRA/OverEscalationRate/JA/DET/CPC/cost_usd_per_run_p95/wall_time_seconds_p95/plan_revision_rounds_p95）。Phase 6 gate.py 读 metrics 必须按此 18 个 anchor 名匹配（与 acceptance_thresholds.yaml 同源）。模板顶部 `{% if not_authoritative %}` banner 块在 concurrency > 1 时插入"wall_time/cost not authoritative"（决策 3 / P1-7）。
- [code-phase-5] `_compute_metrics(samples, metas) -> dict[str, Any]` 输出字段集锁定 19 个 keys：18 个指标 anchor + `Recall` 子 dict 含 6 个 M1..M6 子项 + 2 个内部辅助 `_total_missed_lines` / `_total_extra_lines`。空 samples 走 `_empty_metrics()` 返回全 "N/A" 占位（确保模板 anchor 永不缺失）。Phase 6 gate.py 复用此 dict 做 metric→threshold 比对时按 keys 直接索引。
- [code-phase-5] **指标语义偏差 carry-forward 到 Phase 6**（本审 P2-1/P2-2/P2-3）：
  - **SSER** 当前实施为"security-sensitive 样本占比 if any else 1.0"（`summarize.py:150-152`），偏离 acceptance.md §1 "敏感文件被升级到 human 的比例" 定义；Phase 6 gate.py 必须按真实公式重写。
  - **RR** 当前硬编码 `1.0`（`summarize.py:155`），不按 plan_review_<run_id>.md 存在性 / rationale 完整度真实计算；Phase 6 gate.py 必须按 acceptance.md §1 真实统计。
  - **WDR** 当前依赖 miss_fork count，但 [code-phase-4] `_classify_pair` 已把 MISS_FORK 简化为 MISS_UPSTREAM，故 WDR 永远 0；与 [code-phase-4] "Tier-2 启用" 一致，但 Phase 6 gate threshold 不应对 WDR 设 hard 阈值（永远绿绕过）。
  当前 1-sample fixtures 上不影响 Phase 5 GO，但 Phase 6 / Tier-1 抽样矩阵补齐后必须修正。
- [code-phase-5] `_failure_rows(samples)` 契约：`sorted(failures, key=lambda s: s.sample_id)`；输出 dict 含 `sample_id` / `label` / `strategy` / `rationale_excerpt`（80 char 截断）。`_excerpt` 拼接 `rationale_len=N` + 可选 `; discarded_content=present` + 可选 `; security_sensitive=True`。模板 §4 直接 iter 此 list 保证排序。
- [code-phase-5] Phase 5 测试基线：`pytest tests/eval/unit/` 185 用例（Phase 0 54 + Phase 1 20 + Phase 2 27 + Phase 3 20 + Phase 4 35 + Phase 5 29）；cov 94.27%（每模块 ≥ 90%；_report_render 100% / summarize 94%）；mypy strict 27 files 0 err；ruff clean；fork-name-check exit 0。

**Carry-forward 待办（不阻塞 Phase 5 GO）**：
- Phase 6：本审 P2-1/P2-2/P2-3 — `_compute_metrics` 中 SSER / RR / WDR 语义偏差必须按 acceptance.md §1 真实公式重写；Phase 6 gate.py 不应对 WDR 设 hard 阈值（永远 0 绕过）。
- Phase 6：[code-phase-2] P2-3 仍 open — sentinel acceptance_yaml 路径；建议 cmd_verify 改 `acceptance_yaml: Path | None = None`，prepare/run 显式传 None。
- Phase 6：本审 P2-4 — `_build_context` git_sha 多值取 `<mixed>` vs model_matrix 取第一个 non-empty，策略不一致；统一为 "都取第一个" 或 "都标 mixed"。
- Phase 8：[code-phase-4] P2-3 仍 open — `_decision_to_system_decision` 双字段名兜底（e2e 跑真实 merge 时验证字段名锁定）。
- 后续工作（非本批次）：`--baseline` 数值 delta（需基线 schema 标准化）；多文件 sample 聚合（[code-phase-4] P2-1）；T4-D10 SRSR 字段（待 plan v3）；MISS_FORK 完整区分（Tier-2）；tree-sitter 真实 AST 集成。

---

## [code-phase-6] Phase 6 v1 通过审查新增事实（2026-05-15，gatekeeper-code）

来源：`.multi-agent/eval-impl/code/phase-6/review-v1.md` Step 1-5 核查 + commit `000d64649d5e0c1eba0b1d6f3c5f0fb45d45ecdf`。后续 teammate 不得改写以下结论。

- [code-phase-6] **三项 carry-forward 正式 CLOSED**：
  - [code-phase-2] P2-3 sentinel acceptance_yaml → `lock.cmd_verify(acceptance_yaml: Path | None = None)` 显式可选；`prepare.cmd_prepare` 传 `None`；35 lock+prepare 测试零回归。Phase 7+ 任何 caller 复用 lock.cmd_verify **不得**再传 sentinel 路径，显式 `None` 跳过 sync check。
  - [code-phase-5] P2-1 SSER → `summarize._compute_sser(samples)` 按 metrics.md §3.2 真实实现：`escalated_to_human / sensitive_total`；分母 0 → 1.0 (vacuous PASS, 与 acceptance.md §1 "0 violation = pass" 读法一致)；3 用例守护（none / escalated / not-escalated）。Phase 6 yaml SSER hard threshold=1.0 == 1.0 配套生效。
  - [code-phase-5] P2-2 RR → `summarize._compute_rr(runs_dir, sample_ids)` 按 metrics.md §5.3 真实实现：检查 `runs/<id>/` 下 `merge_report_*.json` + `merge_report_*.md` + `plan_review_*.md` 三件产物且 size > 0；`runs_dir=None` 退化 1.0；3 用例守护。
- [code-phase-6] `scripts/eval/gate.py` 公共 API：`cmd_gate(*, report_path, acceptance_yaml, baseline_path, output, version) -> int` + `parse_metric_table(markdown) -> dict[str, float | str]`（regex `^\|\s*([A-Za-z_][A-Za-z0-9_]*)\s*\|\s*([^|]+?)\s*\|`，numeric coerce float / 非 numeric 保 str）+ `load_thresholds(yaml_path) -> AcceptanceThresholds` + `_derive_verdict(hard, soft) -> (verdict, exit_code)`（**hard fail 严格优先 soft fail**：T6-G11 守护）。`_operator_passes` 支持 ==/>=/<=/</>，未知 operator raise ValueError。
- [code-phase-6] **GateKind 重定义** = `Literal["absolute", "relative"]`（不再是 HARD/SOFT，[test-amend] T6-G6/G8 期望）。hard / soft 分组由 `AcceptanceReport.hard_gates: tuple[GateResult,...]` vs `soft_gates: tuple[GateResult,...]` 表达。Phase 7+ 任何工具引用 GateKind 必须用新值；`GateKind.HARD` / `GateKind.SOFT` 已不存在。
- [code-phase-6] **GateResult 扩展 8 字段全部可空**（除 id/kind 必填）：`value: float | None` / `threshold: float | None` / `operator: GateOperator | None` / `passed: bool | None`(alias="pass") / `multiplier: float | None` / `baseline_value: float | None` / `computed_threshold: float | None` / `skipped_reason: str | None`。**SKIP 行约定**：`passed=None + skipped_reason="<short tag>"`；relative gate 跑 baseline 时填 `value + multiplier + baseline_value + computed_threshold + passed`；absolute gate 填 `value + threshold + operator + passed`。`{"pass":...}` 解包 + `# type: ignore[arg-type]` 仍是 [code-phase-0] T0-S4 锁定的契约（pydantic v2 mypy plugin 限制）。
- [code-phase-6] **AcceptanceThresholdEntry 扩 kind + multiplier**（[plan-amend] / [test-amend] 双向校验）：`kind: Literal["absolute","relative"] = "absolute"` + `threshold: float | None = None` + `multiplier: float | None = None` + `operator: GateOperator | None = None`。`@model_validator(mode="after") _validate_kind_consistency`：`kind=absolute` ↔ `threshold` 必填 + `multiplier` 必空；`kind=relative` ↔ `multiplier` 必填 + `threshold` 必空。T0-S4/S4b/S4c + 4 补强用例守护。Phase 7+ 扩展新 kind（如 "trend"）必须同步 model_validator 与 GateKind enum。
- [code-phase-6] `tests/eval/manifests/acceptance_thresholds.yaml` 首次落 commit：**hard 13** = WMR / SSER / DCRR / SRSR / MMR / Recall_M1..M6 / RR / RCR；**soft 9** = OA / CRA / OverEscalationRate / JA / DET / CPC + 3 relative (cost_usd_per_run_p95 multiplier 1.15 / wall_time_seconds_p95 multiplier 1.20 / plan_revision_rounds_p95 multiplier 1.0)。**WDR 故意缺**（[code-phase-4] MISS_FORK 简化的逻辑后果，注释充分；`TestCommittedYaml` 显式守护 `"WDR" not in ids_hard`）。`synced_with_sha` 锚定 acceptance.md 真实 sha (`6355be87...`)；lock --verify 本地 + CI 双模式 exit 0。**SRSR hard gate 隐式 SKIP**（summarize 当前输出 "N/A (follow-up)"）— 待 plan v3 后启用。
- [code-phase-6] `tests/eval/manifests/tier{1,2,3}.lock.json` 首次落 commit（`lock --update` 跑出 + commit），让 CI 开箱跑 `lock --verify` 即可校验 sample 完整性；tier2 samples 为空（占位）；tier1 含 t1-0001、tier3 含 t3-m3-0001 真实 sha。Phase 7+ 任何修改 reference sample 必须重跑 `lock --update` 同步。
- [code-phase-6] Phase 6 测试基线：`pytest tests/eval/unit/` 216 用例（Phase 0-5 185 + Phase 6 31）；cov 94.52%（每模块 ≥ 90%；gate.py 95% / _schemas 100% / summarize 95%）；mypy strict 29 files 0 err；ruff clean；fork-name-check exit 0；`python -m scripts.eval.lock --verify` 本地 + `CI=true` 双模式 exit 0。

**Carry-forward 待办（不阻塞 Phase 6 GO）**：
- Phase 7：本审 P2-1 — `gate.py` 用 `assert ... is not None` 做 type narrowing；建议改 `if ... is None: raise RuntimeError("BUG")` 避免 `-O` 模式被剥离。
- Phase 7：本审 P2-2 — 缺指标 → SKIP 路径（`gate.py:113-122,146-153`）未在 test 显式覆盖；建议补 1 用例守护 `skipped_reason="metric ... not numeric in report"`。
- Phase 7：本审 P2-3 — `_compute_rr glob("merge_report_*.json")` 多匹配策略与 [code-phase-3] `_locate_merge_run_dir` "单子目录假设" 不一致；多 run 场景出现时统一。
- Phase 7：本审 P2-4 — yaml SRSR hard gate 隐式 SKIP 用户可能误读；建议加 yaml 注释或 stderr warning。
- Phase 7/8：[code-phase-5] P2-4 仍 open — `_build_context` git_sha vs model_matrix 策略不一致。
- Phase 8：[code-phase-4] P2-3 仍 open — `_decision_to_system_decision` 双字段名兜底（e2e 真实 merge 验证字段名）。
- 后续工作（非本批次）：`--baseline` 数值 delta；多文件 sample 聚合；T4-D10 SRSR 字段（待 plan v3）；MISS_FORK 完整区分（Tier-2）；WDR hard gate 重新启用（Tier-2 后）；tree-sitter 真实 AST 集成。

---

## [code-phase-7] Phase 7 v1 通过审查新增事实（2026-05-16，gatekeeper-code）

来源：`.multi-agent/eval-impl/code/phase-7/review-v1.md` Step 1-8 核查 + commit `35cbf6918ea716388e44ae43eec7aaa3c88dbc35`。后续 teammate 不得改写以下结论。

- [code-phase-7] **四项 Phase 6 carry-forward 全部 CLOSED**：
  - [code-phase-6] P2-1 → `gate.py:124-131,162-166` 两处 `assert ... is not None` 改为显式 `if ... is None: raise ValueError(...)`；注释明文 "survives ``python -O`` (which strips asserts)"。Phase 8+ 任何后续维护 gate 都不得回到 `assert` 风格——`-O` 模式 invariant 必须靠 raise 守护。
  - [code-phase-6] P2-2 → `tests/eval/unit/test_gate.py:559-610 TestSkipPaths` 两用例：`test_absolute_gate_absent_metric_skipped`（WMR 缺失 → `pass is None` + `"not numeric" in skipped_reason`）+ `test_relative_gate_absent_metric_skipped`（cost_usd_per_run_p95 缺 + baseline 仍传 → `pass is None`）。Phase 8 e2e 不需要再覆盖该路径。
  - [code-phase-6] P2-3 → `summarize._compute_rr` 抽 `_has_nonempty_match(directory, pattern) -> bool` helper（包 `directory.is_dir()` 防御 + `any(p.stat().st_size > 0 for p in glob)`）；docstring 显式注明"任一 non-empty 计数"策略，与 [code-phase-3] `_locate_merge_run_dir` 单子目录假设兼容（单子目录场景退化为单匹配，多 re-run 场景任一通过即可）。
  - [code-phase-6] P2-4 → `tests/eval/manifests/acceptance_thresholds.yaml:40` SRSR.source 前置 `[FOLLOW-UP — auto-SKIP]` marker，并扩展为完整解释 "gate.py currently emits pass=null+skipped_reason because summarize.py SRSR anchor is the placeholder 'N/A (follow-up)' (test FINAL TR7). Verdict does NOT depend on SRSR until plan v3 lands MergeState.snapshot_rollback_events."。Phase 8/9 不得移除该 marker，直到 SRSR 真实数据流落地。
- [code-phase-7] `scripts/eval/consistency.py` 公共 API（245 行 ≤ 250 上限）：`main(argv: list[str] | None = None) -> int`（CLI 入口）+ `cmd_consistency(*, run_dirs: list[Path], metric: Metric, output: Path) -> int`（程序化入口）+ `Metric = Literal["DET", "CPC"]`。CLI flags：`--runs <dir>+`（nargs="+", required）/ `--metric {DET,CPC}`（required choices）/ `--output <path>`（required）。**不挤 [code-phase-1] lock.py 的 3 子命令互斥 group**——完全独立 argparse，无 sub-parser。退出码：0=成功；1=runs<2 / run dir 不存在 / sample 集合不一致 / 缺 merge_report。Phase 8 e2e 直接 `python -m scripts.eval.consistency --runs <r1> <r2> ... --metric DET --output <out>` 调用。
- [code-phase-7] `_decision_tuple(record) -> (decision, risk)` **双字段名 fallback 锁定**：`str(record.get("decision") or record.get("strategy") or "UNKNOWN")` + `str(record.get("target_risk_level") or record.get("risk") or "UNKNOWN")`。与 `diff_against_golden._decision_to_system_decision` 对齐，与 [code-phase-4] P2-3 carry-forward 同源。**Phase 8 e2e 真实 merge 跑通后**：若发现真实 `MergeState` dump 用唯一字段名（无两侧 alias），可统一为单字段读取并删 fallback；目前 fixture / 真实双轨并存阶段必须保持双字段名兼容。
- [code-phase-7] `_locate_merge_report(sample_dir) -> Path` 复用 [code-phase-4] lex-last 策略：`sorted(sample_dir.glob("merge_report_*.json"))[-1]`；无匹配抛 `FileNotFoundError(f"sample {sample_dir.name}: no merge_report_<run_id>.json")`。Phase 8+ 任何读 per-sample merge_report 的代码必须复用此函数或 1:1 同策略，**不得**任意选第一个 / 第二个；确定性来自 lex-last。
- [code-phase-7] `_compute_metric` 输出契约：`(value: float, total_files: int, inconsistent: list[dict])`。`inconsistent[i]` 结构 = `{"sample_id": str, "file_path": str, "decisions": [{"decision": str, "risk": str}, ...]}`，decisions 列表长度 == n_runs（包括 ABSENT sentinel）。**ABSENT sentinel 设计锁定**：当文件在某 run 缺失时 `rec_map.get(file_path, ("ABSENT", "ABSENT"))` 显式标 disagreement——防止分母膨胀也防止静默通过。Phase 8 测 DET=1.0 链路时不能依赖"缺文件自动跳过"路径。
- [code-phase-7] `_validate_sample_alignment(run_dirs) -> set[str]`：用 `set.intersection(*per_run)` 求交集，`set.union(*per_run) - 交集` 为差集；差集非空抛 `ValueError("consistency: runs disagree on sample set; missing-somewhere = ...")`。**不静默截取共有 sample 子集**——这是 plan §Phase 7 "拒绝一致性比对" 契约的实施。Phase 8 e2e 必须保证所有 run dirs 在 sample 集合上完全对齐。
- [code-phase-7] `cmd_consistency` 输出 JSON 顶层 keys = `{"metric", "value", "n_runs", "total_files", "inconsistent", "run_dirs"}` 6 个；落盘走 [code-phase-0] `write_json`（atomic + sort_keys=True + indent=2）。Phase 8 e2e 断言 `eval_acceptance_*.json.verdict == "PASS"` + DET=1.0 链路时，读 `consistency.json["value"] == 1.0` 判定（不读 `metric` 字段以外的命名约定）。
- [code-phase-7] Phase 7 测试基线：`pytest tests/eval/unit/` **230 用例**（Phase 0-6 216 + Phase 6 P2-2 补 2 + Phase 7 新 12）；cov **94.50%**（consistency.py 96% / gate.py 95% / summarize.py 94%）；mypy strict 30 source files 0 err；ruff clean；fork-name-check exit 0；`lock --verify` 本地 + `CI=true` 双模式 exit 0。
- [code-phase-7] consistency.py 不引入新 schema（裸 dict + `write_json`）；不引入新依赖（全 stdlib + `scripts.eval._common.write_json`）；不触 `src/` / `doc/evaluation/` / `pyproject.toml` / `.github/workflows/*`；不出现 cvte/dify/insforge fork name。

**Carry-forward 待办（不阻塞 Phase 7 GO；与 Phase 6 一致，未新增）**：
- Phase 8：[code-phase-5] P2-4 仍 open — `_build_context` git_sha vs model_matrix 多值策略不一致；e2e 真实多机数据时再决。
- Phase 8：[code-phase-4] P2-3 仍 open — `_decision_to_system_decision` 双字段名 e2e 验证；consistency `_decision_tuple` 已主动遵循同模式，Phase 8 真实 merge 跑通后视真实字段决定是否统一。
- 后续工作（非本批次）：T4-D10 SRSR 字段（待 plan v3）；MISS_FORK 完整区分（Tier-2）；WDR hard gate 重启用（Tier-2 后）；多文件 sample 聚合；tree-sitter 真实 AST 集成；`--baseline` 数值 delta。

---

## [code-phase-8] Phase 8 v1 通过审查新增事实（2026-05-16，gatekeeper-code）

来源：`.multi-agent/eval-impl/code/phase-8/review-v1.md` Step 1-9 核查 + commit `2854a82aff30bc75608d86e12af30b1e5145bae2`。后续 teammate 不得改写以下结论。

- [code-phase-8] `tests/eval/integration/test_e2e_tier1.py` 落地 3 用例锁定 e2e 契约：
  - `TestE2eFullChain::test_chain_lands_pass_verdict` 串 run → diff → summarize → gate 4 步（**run.main 内部已驱动 prepare**，与 [code-phase-3] 锁定一致；不需要在 e2e 层再调 prepare.main），末尾 `assert gate_payload["verdict"] == "PASS"`。Phase 9 CI 跑 `pytest tests/eval/integration/` 自动覆盖。
  - `TestE2eFailurePropagation::test_run_failure_short_circuits_chain` 用 `FAKE_EXIT_CODE=7` 触发上游失败，断言 4 角度：`rc_run == 1` / `run_meta["status"] == "failed"` / 无 `merge_report_*.json` / `rc_diff == 2`。**失败传播契约锁定**：下游禁止静默掩盖上游 fail；任何后续修改 run/diff 错误码语义须同步本测试。
  - `TestE2eDetChain::test_three_runs_consistency_det_equals_one` 3 runs × consistency CLI → `value == 1.0` + `n_runs == 3` + `inconsistent == []`，绑定 T8-E3 DET 链路。
- [code-phase-8] `tests/eval/fixtures/fake_merge_bin/fake_merge.sh` 新增 `FAKE_MERGED_TREE_DIR` env（**非破坏**：未设置时与原版完全等价；既有 22 unit 用例零回归）。语义 = `cp -R "${FAKE_MERGED_TREE_DIR}/." "$(pwd)/"`（trailing `/.` 防嵌套子目录），用于 e2e 把 golden tree 叠回 cwd 以使 diff classify 为 EXACT/SEMANTIC → gate verdict=PASS。**FIXTURE 字面量 `cp + rename` 路径未触**（[code-phase-3] §17.3-prime 锁定保留）。Phase 9+ 若再扩 fake_merge.sh 行为，必须遵循"可选 env + 未设置时行为等价"原则。
- [code-phase-8] e2e harness 辅助函数集（`_run_merge_pipeline` / `_run_diff` / `_run_summarize` / `_run_gate`）通过 monkeypatch.setenv 注入 `FAKE_FIXTURE_DIR` / `FAKE_SAMPLE_ID` / `FAKE_MERGED_TREE_DIR` / `FAKE_EXIT_CODE`；env 由 [code-phase-0] `eval_subprocess_env(use_real_keys=False)` 透传（仅剥 MERGE_DEV + 注入 dummy LLM key）。Phase 9 CI 不需要任何额外 env 配置，本地 + CI 两端跑 e2e 行为等价。
- [code-phase-8] e2e 4 步而非 5 步**正式锁定**：`prepare.cmd_prepare` 由 `run.cmd_run` 内部调用（[code-phase-3] 锁定），故 e2e 层调用序列 = `run.main → diff.main → summarize.main → gate.main`，**禁止**在 e2e 层重复跑 prepare.main（会重复展开 sample 浪费 IO 且与既定 phase-3 契约冲突）。
- [code-phase-8] e2e gate 走 PASS 路径与现 yaml 形态对齐锁定：yaml 不含 WDR hard gate（[code-phase-4] MISS_FORK 简化）+ SRSR auto-SKIP（[code-phase-6] P2-4 marker）+ SSER vacuous PASS（1-sample fixture 无 security_sensitive）+ RR=1.0（fixture 三件产物齐全）。**Tier-2 抽样矩阵 / WDR 重启用后，e2e PASS 期望可能失效**——届时 T8-E1 期望需要随 yaml 同步修订。
- [code-phase-8] `_extract_golden_tree` test helper 直接用 stdlib `tarfile.open` 解 tar（**P2-1 风格问题**）：理论上应调 `_ground_truth.load_golden_tree(sample_dir)` 复用主源码 path-traversal-safe + base64 序列化路径；本期 self-disclosed 接受（test fixture 准备代码不在 scripts/eval/ 主源码契约范围）。**后续若 e2e helper 抽包/重构可顺手优化**，不阻塞 Phase 9。
- [code-phase-8] Phase 8 测试基线：`pytest tests/eval/` **233 用例**（Phase 0-7 230 unit + Phase 8 3 integration）；cov **94.50%**（scripts/eval 模块全 ≥ 90%）；mypy strict 32 source files 0 err；ruff clean；fork-name-check exit 0；lock --verify 本地 + CI 双模式 exit 0；总耗时 ~2s（远低于 plan Phase 9 GO §1 的 30s 上限）。
- [code-phase-8] Phase 8 范围合规：未触 `src/` / `doc/evaluation/` / `pyproject.toml` / `scripts/eval/` 主源码（任何 .py 文件）/ `.github/workflows/*`；3 文件改动全在 `tests/eval/`；未新增运行时依赖（全 stdlib）；未出现 cvte/dify/insforge fork name。

**Carry-forward 待办（不阻塞 Phase 8 GO；与 Phase 7 一致，未新增）**：
- Phase 9 / 后续：[code-phase-5] P2-4 git_sha vs model_matrix 多值策略 — 1-sample fixture 不触发；Phase 9 nightly job 实测时再决。
- Phase 9 / 后续：[code-phase-4] P2-3 双字段名 e2e 真实验证 — e2e fixture 用 JSON-mode `decision`，未真实验证 `strategy/risk` alias；unit `TestDualFieldNameFallback` 已覆盖；待真实 merge CLI 跑通后决定是否统一字段名。
- 后续工作（非本批次）：T4-D10 SRSR 字段（plan v3）/ MISS_FORK 完整区分（Tier-2）/ WDR hard gate 重启用（Tier-2 后）/ 多文件 sample 聚合 / tree-sitter 真实 AST / `--baseline` 数值 delta / `_extract_golden_tree` 改调 `_ground_truth.load_golden_tree`（test helper 风格优化）。

---

## [code-phase-9] Phase 9 v1 通过审查新增事实（2026-05-16，gatekeeper-code）

来源：`.multi-agent/eval-impl/code/phase-9/review-v1.md` Step 1-9 核查 + commit `7687ba738a1f7ba0adba150ef80cbd7182ad00b8`。**eval-impl Phase 0-9 闭环。** 后续 teammate 不得改写以下结论。

- [code-phase-9] `.github/workflows/ci.yml` 修改非破坏：仅在 `test` job 末尾追加 5 个 eval step + 在 workflow 末尾新增 `eval-tier1` job + 在顶层 `on:` 追加 `workflow_dispatch` + nightly schedule 注释 placeholder。**现有 `web-build` / `test` job 的 step 顺序、cov source `--cov=src`、`mypy src` 命令完全保留**。Phase 10+ 任何修改 ci.yml 必须沿用"追加而非修改既有 step / job"原则。
- [code-phase-9] **eval-tier1 job 不阻塞 PR 双保险锁定**：`if: github.event_name != 'pull_request'` (ci.yml:104) + `continue-on-error: true` (ci.yml:105)。**后续维护者绝不可同时移除这两道防御**——任一保留即满足 T9-W7 OR 条件，但移除两者会触发 PR 阻塞 regression。`TestEvalTier1NonBlocking` 用 3-OR 断言永久守护。
- [code-phase-9] **CI 上 5 eval step 名称契约锁定**（`REQUIRED_EVAL_STEP_NAMES` tuple）：`"Lint eval scripts (ruff)"` / `"Type check eval scripts (mypy)"` / `"Eval unit + e2e tests"` / `"Verify dataset locks"` / `"Fork name purity check"`。后续重命名任一 step 名 → `TestRequiredEvalSteps` 立即 fail；故重命名时必须同步更新 `REQUIRED_EVAL_STEP_NAMES`（`test_ci_workflow_meta.py:70-76`）。
- [code-phase-9] **CI 上 cov source 严格隔离锁定**：`Unit tests` step → `--cov=src --cov-fail-under=80`（守护 `tool.coverage.run.source = ["src"]` 默认）；`Eval unit + e2e tests` step → `--cov=scripts/eval --cov-fail-under=80`（独立 source）。两 step 必须互不渗透（T9-W2 双向断言）。后续如需 evaluate 子目录加 cov，必须**新增独立 step**，不得合并 cov source。
- [code-phase-9] **mypy 双 step 锁定**：`Type check (mypy)` = `mypy src`（既有，未触）；`Type check eval scripts (mypy)` = `mypy scripts/eval tests/eval`（新增）。**禁止合并为单 step `mypy src scripts/eval tests/eval`**（T9-W3 守护：src step 必须不含 scripts/eval）。原因：mypy strict 配置在 pyproject 中可能仅对部分子目录生效，合并会导致 strict 模式静默失效。
- [code-phase-9] `_workflow_on(data)` helper 锁定 yaml `on` key 解析双兼容：`data.get(True, data.get("on"))`。**原因**：`yaml.safe_load` 在 YAML 1.1 下把字面量 key `on` 当 boolean `True`（已实测 ci.yml 走 `True` 分支）；新式 parser 可能保持 `"on"` 字符串。后续切换 yaml lib（如 ruamel.yaml）或 yaml 1.2 时本 helper 仍能工作。
- [code-phase-9] **eval-tier1 job per-job `on:` 不可行**：GitHub Actions `on:` 是 workflow-level key（不是 job-level）。**故 manual-only 语义只能靠 `if` + `continue-on-error` 组合**，不能写 `jobs.eval-tier1.on:`。任何后续重构若发现"per-job on"建议——拒绝，改用 if-condition + workflow_dispatch 顶层触发。
- [code-phase-9] **nightly schedule placeholder 而非启用**：ci.yml ll.7-11 保留 `# schedule: - cron: "0 18 * * *"` 注释。原因：当前 fake CLI 不烧实际成本，但 uncomment 后会触发真实 nightly runs（预算 / 监控 / 通知开销）；待 Tier-1 真实评估稳定后由人类显式 uncomment。T9-W5 OR 条件已显式接受 `"nightly placeholder, not blocking"` 注释分支作为有效 trigger 证据。
- [code-phase-9] **T9-W6 unit suite 时长回归测试自防御**：`pytest.mark.skipif(os.getenv("CI") is not None)` 防 CI 自递归（CI 跑 pytest 触发 pytest 套件循环）；本地用 `sys.executable -m pytest ... --ignore=tests/eval/unit/test_ci_workflow_meta.py` 双层防御。本地 ~2.5s 远低 25s 自检阈值（30s plan budget 留 5s headroom）。
- [code-phase-9] Phase 9 测试基线：`pytest tests/eval/` **242 用例**（Phase 0-7 230 unit + Phase 8 3 integration + Phase 9 9 meta）；cov **94.50%**（每模块 ≥ 90%）；mypy strict 33 source files 0 err；ruff clean；fork-name-check exit 0；lock --verify 本地 + CI 双模式 exit 0；yaml.safe_load ci.yml jobs=['web-build', 'test', 'eval-tier1']。
- [code-phase-9] Phase 9 范围合规：未触 `src/` / `doc/evaluation/` / `pyproject.toml` / `scripts/eval/` 主源码 / tests/eval/unit/ 其他既有用例；2 文件改动全是新增 / 追加；未新增运行时依赖（pyyaml 已是 dev dep）；未出现 cvte/dify/insforge fork name。

**Carry-forward 待办（不阻塞 Phase 9 GO；与 Phase 7-8 一致，未新增）**：
- Tier-1 真实评估实测时：[code-phase-5] P2-4 git_sha vs model_matrix 多值策略 — `eval-tier1` nightly 启用后再决。
- 真实 merge CLI 跑通后：[code-phase-4] P2-3 双字段名统一 — 决定是否删 fallback。
- 后续工作（非本批次）：T4-D10 SRSR 字段（plan v3）/ MISS_FORK 完整区分（Tier-2）/ WDR hard gate 重启用（Tier-2 后）/ 多文件 sample 聚合 / tree-sitter 真实 AST / `--baseline` 数值 delta / `_extract_golden_tree` 改调 `_ground_truth.load_golden_tree`（test helper 风格优化）/ nightly cron uncomment（待预算批准）。

---

## 闭环总结（Phase 0-9 GO 完成，2026-05-16）

| Phase | commit | 测试基线 | gatekeeper-code 锁清单 |
|---|---|---|---|
| 0 | `5658e77` | 54 用例 / 95.58% cov | [code-phase-0] 5 条 |
| 1 | `1101b2a` | 74 / 94.56% | [code-phase-1] 7 条 |
| 2 | `8cb3f84` | 101 / 94.12% | [code-phase-2] 6 条 |
| 3 | `c3e2c6c` | 121 / 93.92% | [code-phase-3] 7 条 |
| 4 | `6d3871e` | 156 / 94.16% | [code-phase-4] 7 条 |
| 5 | `bf4cfda` | 185 / 94.27% | [code-phase-5] 6 条 |
| 6 | `000d646` | 216 / 94.52% | [code-phase-6] 8 条 |
| 7 | `35cbf69` | 230 / 94.50% | [code-phase-7] 9 条 + Phase 6 P2-1..P2-4 全 CLOSED |
| 8 | `2854a82` | 233 / 94.50% | [code-phase-8] 8 条 |
| 9 | `7687ba7` | **242 / 94.50%** | [code-phase-9] 11 条 |

**eval-impl Phase 0-9 全部 GO 闭环**。后续：team-lead 写 IMPLEMENTATION_REPORT_PARTIAL.md（已认领 task #4）。
