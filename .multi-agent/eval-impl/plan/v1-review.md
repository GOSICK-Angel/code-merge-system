# Gatekeeper-plan 审查报告（v1）

## 结论
**要求修改（中等）** — NO-GO。

总体评价：v1 整体方向正确（Phase 数量/估时合理、隔离方案/AST fallback/jinja2/sha256 等关键决策可接受、范围严守 facts.md §9）。但 **Phase 3「驱动 merge 子进程」的产物名与隔离机制和真实代码脱节**——若不修，Phase 3 GO 永远不可能达成；同时 **Phase 9 CI 接入**误读了现 CI 仅跑 `tests/unit/`，会导致 `tests/eval/unit/` 与 `mypy scripts` / `ruff check scripts` 全部漏跑。这两类 P0 必须改完才能进入实施。

---

## 已通过事实（详见 `.multi-agent/eval-impl/locks/approved-facts.md`，本轮新增 0 条）

P0 修复前不沉淀新事实。修订版本（v2）通过后再追加。

---

## P0（必改）

### [P0-1] Phase 3 产物名 `merge_state.json` 不存在
- **锚点**：`plan/v1.md:69`、`plan/v1.md:196`、`plan/v1.md:424`
- **事实**：`grep -rn "merge_state.json" src/` 无任何匹配。`src/tools/report_writer.py:424,542` 真实产物名为 `merge_report_<run_id>.md` 与 `merge_report_<run_id>.json`；checkpoint 文件名是 `checkpoint.json`（`src/cli/paths.py:79`、`src/cli/commands/resume.py:105-106`）。`MergeState` 的完整机器可读 dump 在 `merge_report_<run_id>.json`，**没有** `merge_state.json` 这个文件。
- **期望**：把所有 `merge_state.json` 改为 `merge_report_<run_id>.json`（机器可读）+ `merge_report_<run_id>.md`（人读）+ `plan_review_<run_id>.md` + `checkpoint.json`。Phase 3 §0 fixtures `tests/eval/fixtures/dummy_run/runs/t1-0001/` 内文件名同步修正。

### [P0-2] Phase 3 隔离机制依赖不存在的 `MERGE_HOME`
- **锚点**：`plan/v1.md:311`（决策 2 §1）
- **事实**：`grep -rn "MERGE_HOME" src/` 无任何匹配。`merge` CLI 的产物路径开关是 `MERGE_DEV=1`（`src/cli/paths.py:41`、`is_dev_mode()`），不是 `MERGE_HOME`。计划文字"如 `src/cli/main.py` 不识别此变量则 fallback"实际上 100% 走 fallback。
- **期望**：删除 `MERGE_HOME` 提法。明确写：
  - 子进程 env **必须**包含 `MERGE_DEV` **未设**（避免被父进程从 dev 仓库继承）；评估子进程必须显式 `env.pop("MERGE_DEV", None)`，否则会被 `is_dev_mode()` 误判走 `outputs/debug/checkpoints/checkpoint.json` + `MERGE_RECORD/` 路径，与 plan 的 `runs/<id>/.merge/...` 完全错位。
  - 隔离手段只剩两个：**临时 HOME** + **每 sample 独立 cwd**（在 cwd 下产物自然落到 `<cwd>/.merge/runs/<run_id>/`，是 prod 模式真实行为）。

### [P0-3] Memory 路径与对象错位
- **锚点**：`plan/v1.md:198`（Phase 3 GO 第三条）、`plan/v1.md:313`（决策 2 §3）
- **事实**：merge 系统的 memory 是 SQLite 单文件 `<repo>/.merge/memory.db`（prod，`src/cli/paths.py:128-130`）或 `<repo>/outputs/debug/memory.db`（dev），**不是目录** `.merge/memory/`。`<workdir>/home/.claude/projects/` 是 Claude Code（agent harness）自身的对话 memory，与待评估的 merge 系统无关。
- **期望**：
  - 改成检查/wipe `<cwd>/.merge/memory.db`（prod 模式实际位置）。
  - 删除"删除 `<workdir>/home/.claude/projects/`"——那是无关系统。
  - Phase 3 GO 条件第 3 条文字同步修正为"`<cwd>/.merge/memory.db` 在每 sample 跑前不存在或被 `unlink`"。

### [P0-4] Phase 9 误读 CI 现状导致 `tests/eval/unit/` 不会被 CI 跑
- **锚点**：`plan/v1.md:274-275`
- **事实**：`.github/workflows/ci.yml` 中 `test` job 显式跑 `pytest tests/unit/ -v --cov=src --cov-report=term-missing --cov-fail-under=80`，**不是** `pytest tests/`。`testpaths = ["tests"]` 只在不显式给路径时才生效；CI 给了 `tests/unit/`，因此 `tests/eval/unit/` 一行都不会跑。同样 `ruff check src/` / `ruff format --check src/` / `mypy src` 也只覆盖 `src/`，`scripts/eval/` 与 `tests/eval/` 完全在 CI 之外。
- **期望**：Phase 9 必须显式新增 CI step：
  - `pytest tests/eval/unit/ -v --cov=scripts --cov-fail-under=80`（独立 cov，不与 src 80% 门槛混用——`pyproject.toml [tool.coverage.run] source = ["src"]` 也需要在该 step 用 `--cov=scripts/eval` override）。
  - `ruff check scripts tests/eval` + `ruff format --check scripts tests/eval`。
  - `mypy scripts tests/eval`。
  - 否则 v1 §5"全局验收"列出的所有质量门槛在 CI 上根本不生效。

### [P0-5] `diff_against_golden.py` 数据来源未指明
- **锚点**：`plan/v1.md:131-132`（依赖关系表）、`plan/v1.md:208-216`
- **事实**：要计算 metrics.md §1.2 的 `match` 与 `MISS_UPSTREAM/MISS_FORK/WRONG_MERGE` label，必须能拿到 (a) 系统合并后的工作树（用于 `D_sys[f]`），(b) 系统的 `FileDecisionRecord` per-file（用于 H[f]/strategy/risk/discarded_content），(c) 黄金合并工作树（用于 `D_gold[f]`）。
  - (a) 来源：cwd 下 git working tree（merge 系统直接改文件）。plan 没说明在哪一步采样并保存。
  - (b) 来源：`merge_report_<run_id>.json`（含完整 `MergeState.file_decision_records`）；plan 误以为是 `merge_state.json`（见 P0-1）。
  - (c) 来源：`prepare.py` 已展开的 `golden_tree/`。
- **期望**：在 §1 模块依赖图新增一条：`diff_against_golden.py` 读 `<workdir>/runs/<id>/working_tree/`（`run.py` 跑完后必须把 cwd 工作树**拷贝/链接**到 `working_tree/` 子目录，而不是任由它停留在 cwd——否则下一 sample 跑会污染）+ `<workdir>/runs/<id>/merge_report_<run_id>.json` + `<datasets>/.../golden_tree/`。Phase 3 GO 条件相应增补"产 `working_tree/` 子目录拷贝"的断言。

### [P0-6] Tier-1 抽样矩阵 = 0 样本与 dataset.md 强约束冲突
- **锚点**：`plan/v1.md:165-166`（仅 1 个 Tier-1 sample）vs `doc/evaluation/dataset.md:27-35`（每格至少 5 样本，6×3=18 格 = 90 样本起步）
- **事实**：dataset.md §2.2 抽样矩阵明确"每格至少 5 个样本"是 Tier-1 的设计约束。facts.md §9 写"提供至少 1 个 Tier-1 sample…作为参考"是范围下限。这本身没冲突——但 v1 计划应在范围段（§6）**显式声明**："本次只交付 1 个 Tier-1 sample 作为骨架，dataset.md §2.2 的抽样矩阵 N≥5 由后续工作完成；这意味着本次落地后 Tier-1 即便跑通也**无法用于发布决策**"。
- **期望**：v1 §6"范围外"加一条该免责声明，并在 README/`scripts/eval/__init__.py` docstring 注明"骨架样本仅供 schema 验证"。否则将来误用 = 用 1 sample 算 SSER/MMR 出 0/100% 假结论。

### [P0-7] `--ci` 输出 JSON 没有逐文件 decisions，但 plan 把它当数据源
- **锚点**：`facts.md:46-47` 提示"如果 `--ci` 输出 JSON 到 stdout，优先用 stdout，path 仅作 fallback"；plan v1 完全没提 stdout 捕获
- **事实**：`src/tools/ci_reporter.py:8-60` 显示 `--ci` 输出仅含 `status / run_id / total_files / auto_merged / human_required / human_decided / failed_count / judge_verdict / errors[-5:]`。**没有 per-file 决策、没有 rationale、没有 discarded_content**。计算 RCR / DCRR / SSER / per-file label 必须读 `merge_report_<run_id>.json`，stdout JSON 只够算 ER/JA/run-level 指标。
- **期望**：在决策 1 或 Phase 3 GO 中明写："`run.py` 同时捕获 stdout JSON 写到 `runs/<id>/ci_summary.json`（用于 ER/JA 速算）+ 拷贝 `merge_report_<run_id>.json` 到 `runs/<id>/`（per-file 真相）。`diff_against_golden.py` per-file 指标只从 `merge_report_<run_id>.json` 取，stdout JSON 仅作 sanity-check"。

---

## P1（应改）

### [P1-1] `--no-cache` 在真实 CLI 不存在，决策 3 引用了不存在的开关
- **锚点**：`plan/v1.md` 决策 3（间接引 procedure.md §6.2）；`grep --no-cache src/cli/` 无匹配
- **建议**：在决策 3 加一条"现 CLI 无 `--no-cache` 标志，本次评估的 cache 控制只能依赖`不复用 workdir`（每次 run 用独立 `--workdir/run-N`）+ Anthropic prompt-caching 的 5min TTL；如需硬保证 N 次 run 互不影响，需后续给 `merge` CLI 加 `--no-cache` 选项（out-of-scope）"。否则 metrics DET 计算可能被 cache 命中污染而无人发现。

### [P1-2] §1 依赖图未画出"`merge_report_<run_id>.json` → diff_against_golden"边
- **锚点**：`plan/v1.md:108-114`
- **建议**：依赖图中 `runs/` 与 `diff.json` 之间补一条注释"per-file decisions: read from `merge_report_<run_id>.json` inside `runs/<id>/`"。配合 P0-5。

### [P1-3] Phase 0 的 `_common.py` 隔离 fixture 未明确写入 `MERGE_DEV` 处理
- **锚点**：`plan/v1.md:149`、`plan/v1.md:37-38`（conftest.py）
- **建议**：`conftest.py` fixture `eval_subprocess_env` 必须 `monkeypatch.delenv("MERGE_DEV", raising=False)`（否则在开发者本机有 `MERGE_DEV=1` 时单测产物路径会跑偏）。Phase 0 GO 条件加一条断言。

### [P1-4] Phase 9 中 fork 名 grep 检查放错位置
- **锚点**：`plan/v1.md:410`、`plan/v1.md:437`
- **事实**：`grep -E '(cvte|dify|insforge)'` 是 hard rule（CLAUDE.md "Project Generality"），但 §5 全局验收只写 grep `tests/eval/datasets/ tests/eval/manifests/`，没 grep `scripts/eval/`。计划 R8 风险表也只把它放在 Phase 9 CI。
- **建议**：把 grep 检查同时放进 Phase 0 的 lint 步骤（`scripts/eval/` + `tests/eval/`），由 `ruff check` 之外的额外脚本完成；CI 接入只是兜底。

### [P1-5] Phase 4 AST normalize "去注释行" 实现风险被低估
- **锚点**：`plan/v1.md:333`（决策 4 第 2 条）
- **事实**：按文件后缀分派 `# / // /* */ <!-- -->` 的"去注释"是简易实现，对字符串里包含 `//` 的代码（URL、正则）会误删。决策 4 自己定为 fallback 但没告知精度损失。
- **建议**：把 fallback 进一步收窄为"normalize: 去 BOM + `\r\n→\n` + trim 行尾空白；不做去注释"——否则 SEMANTIC 判定假阳/假阴都难定位。如果一定要去注释，必须用 tokenizer (`tokenize` 标准库 for Python)，不要用正则。

### [P1-6] 决策 7 acceptance.md 同步检查实现细节缺失
- **锚点**：`plan/v1.md:380-389`
- **建议**：`acceptance_thresholds.yaml` 顶部需要 `synced_with_sha:` 字段记录 `acceptance.md` 当前 sha256，`lock.py --verify` 调 `_assert_acceptance_sync()` 比对。这一节 plan 已写，但没说"yaml 字段名"和"哪类变更允许放过"（如纯 typo 修订也会触发？）。建议写明：sha 不一致时报 warning 并要求开发者跑 `lock.py --update-acceptance-sync`，CI 才升为 error。

### [P1-7] Phase 3 `--concurrency` 与决策 3"串行测时" 矛盾未澄清
- **锚点**：`plan/v1.md:199`、`plan/v1.md:325-326`
- **事实**：Phase 3 GO 要求 `--concurrency N` 并行跑通；决策 3 又强调"串行可保证 cost/wall_time 测量不受影响"。两者都成立但适用场景不同。
- **建议**：在决策 3 末尾补一句"`--concurrency` 用于 schema/正确性验证；任何用于发布决策的 cost/wall_time 数据必须 `--concurrency=1` 并写入 `run_meta.json.concurrency`，summarize 时若发现 N>1 必须在报告头部标注 'wall_time/cost not authoritative'"。

---

## P2（建议）

### [P2-1] 估时整体偏紧
- 8 工作日做 7 个 CLI + 4 个 helper 模块 + e2e + CI + 2 个真实 sample，平均每天 2 个 deliverable。Phase 3（最大风险）只给 1.5 d 偏乐观——R1 一旦命中（CLI 与隔离环境冲突）大概率延 1 天。建议把整体口径改为"8–10 工作日（最坏情况）"，避免后续 slip 引发"砍 Tier-3 仅留 1 个" R9 兜底。

### [P2-2] Phase 0 `_schemas.py` ≤ 250 行偏乐观
- DiffEntry / DiffReport / GateResult / RunMeta / ManifestEntry + 各自 enum + validators，按 pydantic v2 风格至少 300+ 行。建议放宽到 ≤ 400 行，或拆 `_schemas/diff.py` + `_schemas/run.py`。

### [P2-3] Phase 8 e2e 缺 fake `merge` 输出契约说明
- `tests/eval/fixtures/dummy_run/` 是 Phase 3 用的 mock，Phase 8 是否复用？建议明写"Phase 8 fake merge-bin 直接 copy `tests/eval/fixtures/dummy_run/runs/<id>/` 到目标位置 + echo `ci_summary.json` 到 stdout"。

### [P2-4] `_git_oracle.py` Tier-2 占位价值低
- 既然 §6 范围外明确 Tier-2 只 `.gitkeep`，那 `_git_oracle.py` 在本次落地中没有任何 caller，纯死代码。建议直接砍掉（移到下一阶段），符合 CLAUDE.md "feedback_dead_code_check"（已在 memory）。

### [P2-5] §1 依赖图与 §0 文件清单未列 `acceptance_thresholds.yaml`
- 决策 7 引入了这个新文件（`tests/eval/manifests/acceptance_thresholds.yaml`），但 §0 目录树没标。建议补上，且写明"由 Phase 6 创建并由 lock.py 校验同步"。

---

## 二审及之后：上轮反馈落地核查

首轮，无上轮反馈。
