# Gatekeeper-plan 审查报告（v2）

## 结论
**通过**。

v2 完整内化 v1-review.md 的 P0×7 / P1×7 / P2×5 共 19 项；新增锚点（`paths.py:39 MERGE_DEV`、`report_writer.py:538-542 write_json_report`、`paths.py:127 get_project_memory_db_path`、`main.py:159 repo_path="."`）全部经过 grep 与 Read 二次核实，与现仓状态一致；§8 修订摘要表与正文逐项对应。决策 9（不实现 `_git_oracle.py`）符合 memory `feedback_dead_code_check.md` 已沉淀的纪律。

仅遗留三条 P2 级提示（不阻塞通过，建议在实施 Phase 时顺手处理，不要求 v3）。

---

## 已通过事实（详见 `.multi-agent/eval-impl/locks/approved-facts.md`，本轮新增 12 条）

1. `merge_report_<run_id>.json` 与 `merge_report_<run_id>.md` 是真实产物名（`src/tools/report_writer.py:424,542`），**不存在 `merge_state.json`**
2. `plan_review_<run_id>.md` 真实产物名（`report_writer.py:601-608`）
3. `checkpoint.json` 文件名（`src/cli/paths.py:79`、`src/cli/commands/resume.py:105`）
4. `MERGE_DEV` 是 dev/prod 模式开关（`src/cli/paths.py:39 if os.environ.get("MERGE_DEV") == "1"`），**不存在** `MERGE_HOME` 这个 env var
5. Memory 是 SQLite 单文件 `<repo>/.merge/memory.db`（prod 模式，`paths.py:127-130 get_project_memory_db_path`），不是目录
6. `is_dev_mode(repo_path)` 在 `repo_path != pkg_root` 时返回 False，评估子进程 cwd 在隔离 tmp 目录 → 走 prod 模式产物路径
7. `repo_path="."` 在 `src/cli/main.py:159` 硬编码注入到 `detect_or_setup`；下游所有 paths 调用都通过 `state.config.repo_path` 流转（`src/tools/merge_plan_report.py:27`、`src/core/phases/{report_generation,plan_review,human_review}.py`）
8. `--ci` stdout 输出由 `src/tools/ci_reporter.py:8-60 build_ci_summary` + `format_ci_summary` 产生，**仅含 run-level 字段**（status / run_id / total_files / auto_merged / human_required / human_decided / failed_count / judge_verdict / errors[-5:]），**不含 per-file decisions**——per-file 真相必须读 `merge_report_<run_id>.json`
9. `.github/workflows/ci.yml` `test` job 显式跑 `pytest tests/unit/`、`ruff check src/`、`ruff format --check src/`、`mypy src`，**`tests/eval/` 与 `scripts/` 默认完全在 CI 之外**；Phase 9 必须显式新增 step 才能纳入
10. `tool.coverage.run.source = ["src"]` 是 pyproject 默认，但 `--cov=scripts/eval` flag 会 override，pytest-cov 标准行为
11. M1-M6 detector 文件全部存在（`src/tools/scar_list_builder.py`、`shadow_conflict_detector.py`、`interface_change_extractor.py`、`reverse_impact_scanner.py`、`three_way_diff.py`、`config_line_retention_checker.py`、`gate_runner.py`），与 `facts.md §6` 映射一致
12. `merge` CLI **无** `--no-cache` 标志（`grep --no-cache src/cli/` 无匹配）；procedure.md §6.2 提到的 `--no-cache` 是评估侧建议，**非现状**

---

## P0（必改）
无。

---

## P1（应改）
无。

---

## P2（建议，不阻塞，实施时顺手处理）

### [P2-v2-1] Memory 隔离断言可补 sidecar
- **锚点**：v2.md Phase 3 GO 第 3 条（决策 2 §3）
- **事实**：除 `memory.db`，`paths.py:138 get_project_hit_stats_path` 还在 `<repo>/.merge/memory_hit_stats.json` 写 sidecar。每 sample 跑前断言只检查 `memory.db` 不存在不会出错（隔离 cwd 本身就保证），但完整性建议同时断言 `memory_hit_stats.json` 不存在。
- **建议**：Phase 3 GO 第 3 条改为"`<cwd>/.merge/memory.db` 与 `<cwd>/.merge/memory_hit_stats.json` 均不存在"。

### [P2-v2-2] platformdirs 在 macOS 上的 HOME 隔离假设需在 Phase 0 测试中验证
- **锚点**：v2.md 决策 2 §1（HOME=<workdir>/home）
- **事实**：`paths.py:151-156 get_system_log_dir` 在 prod 模式走 `_pd.user_data_dir("code-merge-system")`，macOS 上 platformdirs 通常返回 `~/Library/Application Support/code-merge-system/logs`。`~` expand 依赖 `HOME` env，所以临时 HOME 应能隔离——但**这是假设**，需要 Phase 3 GO 加一条单测断言"在 fake `HOME=/tmp/xxx/home` 下 `_pd.user_data_dir(...)` 解析到 tmp 路径之内"。
- **建议**：Phase 3 `tests/eval/unit/test_run.py` 加一个 `test_log_dir_isolated_under_tmp_home` 用例，避免日志泄漏到宿主 `~/Library/.../logs/` 而无人发觉。

### [P2-v2-3] 项目级 ruff config 不存在
- **锚点**：`pyproject.toml` 无 `[tool.ruff]` section（grep 确认）
- **事实**：`ruff check scripts tests/eval` 与现 CI `ruff check src/` 都走 ruff default config（无项目自定义规则）。这意味着新代码不会触发 src/ 之外的额外规则——本身无 bug，但 v2 §5 全局验收依赖 ruff 时，行为与开发者本地 IDE 配置可能不一致。
- **建议**：Phase 0 commit 时如发现 ruff default 与 src/ 实际风格冲突，记录到 R5 或新增 R11；不要求 v3 调整。

---

## 二审及之后：上轮反馈落地核查（v1 → v2）

| 上轮反馈 | v2 落地情况 | 是否引入新 regression |
|---|---|---|
| **P0-1** 产物名 `merge_state.json` 不存在 | ✅ §0 fixtures 已改名 `merge_report_<run_id>.{json,md}` + `plan_review_<run_id>.md` + `checkpoint.json`；§5 验收表同步；Phase 3 GO 全列出 | 否 |
| **P0-2** `MERGE_HOME` 不存在 | ✅ 决策 2 重写：删 MERGE_HOME，强制 `env.pop("MERGE_DEV", None)`；Phase 3 GO 隔离三层明示；R10 单列高概率风险 | 否 |
| **P0-3** Memory 是 .db 文件 | ✅ 决策 2 §3 改 `<cwd>/.merge/memory.db`；删 `.claude/projects/` 误述；Phase 3 GO 第 3 条修正 | 否 |
| **P0-4** Phase 9 误读 CI | ✅ Phase 9 重写：5 个显式 step（lint/format/mypy/pytest+cov/lock-verify/fork-check），独立 `--cov=scripts/eval`；§9 GO 关键修正点段落明示"不能假设 pytest tests/ 会跑 tests/eval/" | 否 |
| **P0-5** 数据来源未指明 | ✅ §1 新增"Per-file decisions data flow"块；Phase 3 GO 加 `working_tree/` 拷贝；Phase 4 GO 明确 per-file 数据源 | 否 |
| **P0-6** 抽样矩阵冲突 | ✅ §6 加显式免责声明（含数字 6×3=18 格 = 90 样本起步）；`scripts/eval/__init__.py` docstring 入清单 | 否 |
| **P0-7** stdout JSON 不够 | ✅ 决策 1 / Phase 3 GO 明写 `ci_summary.json`（stdout 捕获）+ `merge_report_<run_id>.json`（per-file 真相）双写；§1 数据流图中 ci_summary 标 "sanity-check only" | 否 |
| **P1-1** `--no-cache` 不存在 | ✅ 决策 3 增段说明 cache 控制方案 + §6 范围外列入"现 CLI 无 --no-cache" | 否 |
| **P1-2** 依赖图缺边 | ✅ §1 已新增 Per-file 数据流块 | 否 |
| **P1-3** conftest 缺 MERGE_DEV 处理 | ✅ Phase 0 GO 加 fixture 断言；`_common.py.eval_subprocess_env()` 强制 pop；conftest 也用 `monkeypatch.delenv` | 否 |
| **P1-4** fork 名 grep 位置 | ✅ 新增 `_fork_name_check.py` 模块；Phase 0 GO + Phase 9 CI 双检查；§5 全局验收第 4 条列出 | 否 |
| **P1-5** AST 去注释风险 | ✅ 决策 4 收窄：fallback 仅 BOM/换行/行尾空白，**不**去注释；如未来需去注释必须用 tokenizer | 否 |
| **P1-6** 同步检查细节 | ✅ 决策 7 补 yaml schema（synced_with_sha / synced_at / hard_gates / soft_gates）+ 三种处置（warning / CI error / `--update-acceptance-sync` 子命令） | 否 |
| **P1-7** concurrency 与测时矛盾 | ✅ 决策 3 末尾补段；Phase 5 summarize 自动头部标注 | 否 |
| **P2-1** 估时偏紧 | ✅ Phase 3 1.5d→2d；§7 总估时改 8.5 d 区间口径（最坏 10 d） | 否 |
| **P2-2** _schemas 行数 | ✅ §0 上限 250→400 | 否 |
| **P2-3** e2e fake merge 契约 | ✅ Phase 8 交付物明写"copy fixtures + echo stdout" | 否 |
| **P2-4** _git_oracle 死代码 | ✅ §0 删除该模块；§1 依赖图同步删 `_git_oracle.py` 行；新增决策 9 解释；§6 范围外列入 | 否 |
| **P2-5** acceptance_thresholds 缺清单 | ✅ §0 入清单；标注 Phase 6 创建；决策 7 补完整 yaml schema | 否 |

**全部 19 项落地，无 regression**。
