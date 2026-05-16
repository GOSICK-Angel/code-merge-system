# 评估方案落地实施计划 v2

> **作者**：planner（team eval-impl）
> **日期**：2026-05-15
> **修订基于**：`.multi-agent/eval-impl/plan/v1-review.md`（gatekeeper-plan，P0×7 / P1×7 / P2×5）
> **目标**：把 `doc/evaluation/{README,metrics,dataset,procedure,acceptance}.md` 描述的评估体系，落地为可运行的 `scripts/eval/` 与 `tests/eval/` 目录骨架，不动 `src/`，不引入新运行时依赖。
> **范围锚点**：`facts.md §9`（必须做 / 可选 / 禁止）。
> **文档锁**：`doc/evaluation/` @ 2026-05-15。

---

## 0. 目录结构图

```
scripts/
  __init__.py                        # 空 marker
  eval/
    __init__.py                      # 暴露版本常量；docstring 注明"骨架样本仅供 schema 验证"
    _common.py                       # 共享：路径解析、隔离工作区、json IO（≤ 250 行）
    _schemas.py                      # pydantic v2 模型：DiffEntry / DiffReport / GateResult / RunMeta（≤ 400 行）
    _ast_equiv.py                    # SEMANTIC 判定：tree-sitter 可选 + normalize fallback（≤ 200 行）
    _ground_truth.py                 # 加载 golden_tree / meta.yaml 的统一接口（≤ 200 行）
    _report_render.py                # jinja2 模板渲染 eval_report_*.md（≤ 150 行）
    _templates/
      eval_report.md.j2              # procedure.md §3.1 六章节模板
    _fork_name_check.py              # 复用脚本：grep -E '(cvte|dify|insforge)'，Phase 0/9 共用（≤ 80 行）
    lock.py                          # CLI: --verify / --update / --update-acceptance-sync（≤ 250 行）
    prepare.py                       # CLI: --tier {1|2|3} --out <dir>（≤ 300 行）
    run.py                           # CLI: --tier --workdir --concurrency --merge-bin --merge-args --seed（≤ 400 行）
    diff_against_golden.py           # CLI: --runs --datasets --output diff.json（≤ 400 行）
    summarize.py                     # CLI: --diff --runs --output report.md（≤ 350 行）
    gate.py                          # CLI: --report --acceptance --baseline --output（≤ 350 行）
    consistency.py                   # CLI: --runs --metric {DET|CPC}（≤ 250 行）

tests/
  eval/
    __init__.py
    conftest.py                      # 隔离 fixture：tmp HOME / monkeypatch.delenv("MERGE_DEV") / dummy env（≤ 150 行）
    datasets/
      tier1/
        samples/
          t1-0001/
            base.tar
            upstream.patch
            fork.patch
            golden.tar
            meta.yaml
      tier2/
        replays/
          .gitkeep                   # 空骨架，Tier-2 留 out-of-scope
      tier3/
        adversarial/
          t3-m3-0001/
            base.tar
            upstream.patch
            fork.patch
            golden.tar
            meta.yaml                # loss_class: M3
    manifests/
      tier1.lock.json                # 由 lock.py 生成
      tier2.lock.json                # 仅含 header，samples 为空
      tier3.lock.json
      acceptance_thresholds.yaml     # 决策 7：机器可读阈值表 + synced_with_sha 字段（Phase 6 创建，lock.py 校验）
    fixtures/
      dummy_run/                     # Phase 3 / Phase 8 共用 mock：模拟 merge --ci 输出的最小 run 目录
        runs/
          t1-0001/
            run_meta.json
            merge_report_<run_id>.json   # 机器可读 MergeState dump（report_writer.py:542）
            merge_report_<run_id>.md     # 人读报告（report_writer.py:424）
            plan_review_<run_id>.md
            checkpoint.json              # paths.py:79
            ci_summary.json              # 子进程 stdout 捕获
            working_tree/                # run.py 跑完后从 cwd 拷贝过来的合并产物
    unit/
      test_common.py
      test_schemas.py
      test_ast_equiv.py
      test_ground_truth.py
      test_lock.py
      test_prepare.py
      test_run.py
      test_diff_against_golden.py
      test_summarize.py
      test_gate.py
      test_consistency.py
      test_report_render.py
      test_fork_name_check.py
    integration/
      test_e2e_tier1.py              # 跑通 procedure.md §2 完整命令链（dummy data）
```

**v1→v2 变更**：
- 删除 `_git_oracle.py`（P2-4：本次 Tier-2 仅占位，无 caller，删死代码）
- 新增 `_fork_name_check.py`（P1-4：fork 名 grep 提前到 Phase 0，CI 兜底）
- 新增 `acceptance_thresholds.yaml`（P2-5：决策 7 文件入清单）
- `_schemas.py` 上限 250→400（P2-2）
- `_common.py` 上限 200→250（容纳 MERGE_DEV 处理）
- fixtures 文件名修正为真实产物名（P0-1）
- `dummy_run/runs/<id>/` 增加 `working_tree/` 与 `ci_summary.json`（P0-5 / P0-7）

---

## 1. 模块依赖图

```
                     ┌─ _schemas.py ◄────────────────────────────────┐
                     │     (DiffEntry, DiffReport, GateResult,       │
                     │      RunMeta, ManifestEntry,                  │
                     │      AcceptanceThresholds)                    │
                     │                                               │
   _common.py ◄──────┼─ _ast_equiv.py    _ground_truth.py            │
   (paths, isolate,  │   (EXACT/SEMANTIC) (load golden_tree)         │
    json IO,         │                                               │
    MERGE_DEV pop)   │                                               │
        ▲            │                                               │
        │            ├───────┬───────┬──────────┬──────────┬─────────┤
        │            │       │       │          │          │         │
     lock.py     prepare.py run.py diff_against summarize  gate.py consistency.py
        │            │       │      _golden.py    │           │        │
        │            │       │          │         │           │        │
        ▼            ▼       ▼          ▼         ▼           ▼        ▼
   manifests     workdir   runs/      diff.json  report.md  acceptance.json
                 (tar      (per
                 expand)   sample
                           merge run
                           +working
                           _tree/
                           +ci_summary
                           .json)

Per-file decisions data flow (P0-5 / P0-7 / P1-2):
  run.py  →  runs/<id>/
              ├── ci_summary.json                  ← stdout JSON capture (run-level only)
              ├── merge_report_<run_id>.json       ← per-file MergeState dump (P0-1, single source of truth for per-file metrics)
              ├── merge_report_<run_id>.md         ← human-readable
              ├── plan_review_<run_id>.md          ← required for RR metric
              ├── checkpoint.json                  ← paths.py:79
              ├── run_meta.json                    ← cost/wall_time/seed/concurrency/git_sha
              └── working_tree/                    ← copy of cwd post-merge (D_sys[f])

  diff_against_golden.py reads:
    - runs/<id>/working_tree/                      → D_sys[f]
    - runs/<id>/merge_report_<run_id>.json         → H[f] / strategy / risk / rationale / discarded_content
    - datasets/.../<id>/golden_tree/               → D_gold[f] (from prepare.py expanded)
    - runs/<id>/ci_summary.json                    → sanity-check only (cross-verify run-level numbers)

External (read-only) imports from src/:
  - src.models.diff.RiskLevel                      (facts.md §4)
  - src.models.decision.DecisionSource             (facts.md §4)
  - src.models.decision.FileDecisionRecord         (facts.md §4)
  - src.models.judge.JudgeVerdict, VerdictType     (facts.md §4)
  - src.models.state.MergeState                    (facts.md §4)
  - src.models.plan.MergePlan                      (facts.md §4)
  - src.tools.file_classifier.is_security_sensitive (facts.md §5)
  - src.tools.file_classifier.classify_file         (facts.md §5)
  - src.tools.cost_tracker.CostTracker              (facts.md §5)
```

---

## 2. Phase 拆分

### Phase 0 — 目录骨架与数据模型

- **估时**：1.0 工作日
- **输入依赖**：无
- **交付物**：
  - `scripts/__init__.py`、`scripts/eval/__init__.py`（含"骨架样本仅供 schema 验证"docstring，P0-6）
  - `scripts/eval/_common.py`（≤ 250 行，含 `eval_subprocess_env()` 工厂函数，强制 `env.pop("MERGE_DEV", None)`，P0-2 / P1-3）
  - `scripts/eval/_schemas.py`（≤ 400 行）
  - `scripts/eval/_fork_name_check.py`（≤ 80 行，P1-4）
  - `tests/eval/__init__.py`、`tests/eval/conftest.py`（≤ 150 行，含 `eval_subprocess_env` fixture，`monkeypatch.delenv("MERGE_DEV", raising=False)`）
  - 目录骨架：`tests/eval/datasets/{tier1,tier2,tier3}/`（含 `.gitkeep`）、`tests/eval/manifests/`
  - `tests/eval/unit/test_common.py`、`test_schemas.py`、`test_fork_name_check.py`
- **GO 条件**：
  - `pytest tests/eval/unit/test_common.py tests/eval/unit/test_schemas.py tests/eval/unit/test_fork_name_check.py` 通过
  - `mypy scripts tests/eval` 通过
  - `ruff check scripts tests/eval` + `ruff format --check scripts tests/eval` 通过
  - `python -m scripts.eval._fork_name_check scripts/eval tests/eval` 退出 0（无 fork 名命中）
  - `eval_subprocess_env()` 单测断言：返回 env dict 不含 `MERGE_DEV` 且即便 `os.environ["MERGE_DEV"]="1"` 也被剔除（P1-3）

### Phase 1 — `lock.py`

- **估时**：0.5 工作日
- **输入依赖**：Phase 0
- **交付物**：
  - `scripts/eval/lock.py`（≤ 250 行，含 `--verify` / `--update` / `--update-acceptance-sync`）
  - `tests/eval/unit/test_lock.py`
  - 至少 1 个 Tier-1 sample（`t1-0001`）：`base.tar / upstream.patch / fork.patch / golden.tar / meta.yaml`，最小可复现（如 base 含 `hello.py`，upstream 改函数签名，fork 加注释；golden 是合并版）
  - 1 个 Tier-3 sample（`t3-m3-0001`，loss_class M3）
- **GO 条件**：
  - `python scripts/eval/lock.py --verify` 退出 0；篡改 sample 后非 0
  - `python scripts/eval/lock.py --update` 写出三份 `*.lock.json`
  - 单测覆盖 ≥ 80%，含 `lock.py --verify` 检测 `acceptance_thresholds.yaml` 不存在时 warning（不 fail）

### Phase 2 — `prepare.py` + `_ground_truth.py`

- **估时**：0.5 工作日
- **输入依赖**：Phase 1
- **交付物**：
  - `scripts/eval/_ground_truth.py`（≤ 200 行）
  - `scripts/eval/prepare.py`（≤ 300 行）
  - `tests/eval/unit/test_prepare.py`、`test_ground_truth.py`
- **GO 条件**：
  - `python scripts/eval/prepare.py --tier 1 --out /tmp/eval-tier1` 在 lock 一致前提下展开 sample
  - workdir 内每 sample 含 `working_tree/`、`golden_tree/`、`meta.yaml`、`apply_log.txt`
  - 损坏 patch / 缺失 golden / lock 不一致三类异常分别抛清晰 exception
  - mypy strict 通过

### Phase 3 — `run.py`（最大风险点，已按 P0 重写）

- **估时**：2.0 工作日（P2-1：v1 1.5 d 偏紧，给 +0.5 d 缓冲）
- **输入依赖**：Phase 2
- **交付物**：
  - `scripts/eval/run.py`（≤ 400 行）
  - `tests/eval/unit/test_run.py`（用 fake `merge-bin` 验证 CLI 拼参 + 产物拷贝 + env 隔离）
  - `tests/eval/fixtures/dummy_run/runs/t1-0001/`（mock 产物，文件名按 P0-1 修正）
- **GO 条件**（P0-1 / P0-2 / P0-3 / P0-5 / P0-7 全部内化）：
  - 通过 `--merge-bin` 注入 fake `merge`，能在 workdir 跑出每 sample 以下产物：
    - `runs/<id>/run_meta.json` —— 含 `wall_time_seconds / cost_usd / model_matrix / git_sha / seed / concurrency / cache_disabled`
    - `runs/<id>/ci_summary.json` —— 子进程 stdout JSON 捕获（run-level 字段，P0-7）
    - `runs/<id>/merge_report_<run_id>.json` —— per-file 真相（P0-1，从 prepare 后的 cwd `<sample_workdir>/.merge/runs/<run_id>/` 拷贝）
    - `runs/<id>/merge_report_<run_id>.md`、`plan_review_<run_id>.md`、`checkpoint.json` —— 同样从 cwd 拷贝
    - `runs/<id>/working_tree/` —— **每 sample 跑完必须把 cwd 工作树拷贝到此**（P0-5），避免下一 sample 跑污染
  - 隔离三层：
    - 子进程 env 强制 `env.pop("MERGE_DEV", None)`（P0-2，避免被 dev 仓库继承）
    - `HOME=<workdir>/home`（临时目录）
    - 每 sample 独立 `cwd=<workdir>/runs/<id>/_cwd`（产物自然落到 `<cwd>/.merge/runs/<run_id>/`）
  - Memory 隔离断言：每 sample 跑前 `<cwd>/.merge/memory.db` 不存在（P0-3 修正：是 SQLite 单文件，不是目录）
  - `--concurrency N` 用 `asyncio.Semaphore` 串行/并行均跑通；同时 `run_meta.json` 必须记录 `concurrency` 字段，`summarize.py` 在 N>1 时自动在 report 头部标注 "wall_time/cost not authoritative"（P1-7）
  - mypy strict 通过

### Phase 4 — `_ast_equiv.py` + `diff_against_golden.py`

- **估时**：1.5 工作日
- **输入依赖**：Phase 3
- **交付物**：
  - `scripts/eval/_ast_equiv.py`（≤ 200 行）
  - `scripts/eval/diff_against_golden.py`（≤ 400 行）
  - `tests/eval/unit/test_ast_equiv.py`、`test_diff_against_golden.py`
- **GO 条件**：
  - `python scripts/eval/diff_against_golden.py --runs ... --datasets ... --output diff.json` 输出严格符合 procedure.md §3.2 + 扩展字段（决策 1）
  - per-file 数据来源严格遵循 P0-5 / P0-7：从 `merge_report_<run_id>.json` 读 decisions，从 `working_tree/` 读 D_sys，从 `golden_tree/` 读 D_gold；`ci_summary.json` 仅作 sanity-check
  - tree-sitter import 失败时降级到字节相等 + normalize（仅去 BOM + `\r\n→\n` + trim 行尾空白，**不去注释**，P1-5），`diff.json.meta.semantic_engine = "fallback-bytes"`
  - 测试覆盖：MISS_UPSTREAM / MISS_FORK / WRONG_MERGE / EXTRA_NOISE 各 1 用例

### Phase 5 — `summarize.py` + `_report_render.py`

- **估时**：1.0 工作日
- **输入依赖**：Phase 4
- **交付物**：
  - `scripts/eval/_report_render.py`（≤ 150 行）+ `_templates/eval_report.md.j2`
  - `scripts/eval/summarize.py`（≤ 350 行）
  - 单测对应文件
- **GO 条件**：
  - 输入固定 `diff.json` + `runs/*/run_meta.json`，输出 `eval_report_*.md` 含 procedure.md §3.1 全部六章节
  - 至少含指标：OA / WMR / MMR / WDR / SSER / DCRR / RR / RCR / Recall_M1..M6
  - 当 `run_meta.json.concurrency > 1` 时，报告头部自动标注 "wall_time/cost not authoritative"（P1-7）
  - 失败案例清单按 sample_id 排序

### Phase 6 — `gate.py` + `acceptance_thresholds.yaml`

- **估时**：0.5 工作日
- **输入依赖**：Phase 5
- **交付物**：
  - `scripts/eval/gate.py`（≤ 350 行）
  - `tests/eval/manifests/acceptance_thresholds.yaml`（首次落盘，含 `synced_with_sha` 字段=acceptance.md 当前 sha256）
  - `tests/eval/unit/test_gate.py`
- **GO 条件**：
  - 输入 `eval_report_*.md` + `acceptance_thresholds.yaml`，输出 `eval_acceptance_*.json` 严格符合 procedure.md §3.3
  - 退出码：0=全 pass / 1=任一 hard fail / 2=仅 soft 退化
  - `lock.py --verify` 同步检查（决策 7 / P1-6）：
    - sha 不一致 → warning（包括 typo 修订），提示跑 `lock.py --update-acceptance-sync`
    - CI 中 sha 不一致 → error（通过 env 区分；本地开发不阻塞）
  - 单测覆盖 hard 失败 / soft 退化 / 全绿三种路径

### Phase 7 — `consistency.py`

- **估时**：0.5 工作日
- **输入依赖**：Phase 5
- **交付物**：
  - `scripts/eval/consistency.py`（≤ 250 行）
  - `tests/eval/unit/test_consistency.py`
- **GO 条件**：
  - 输入多个 `runs/`，输出 DET / CPC 数值 + 不一致样本清单
  - 比对维度：`MergeState.file_decision_records[f]` 的 `(strategy, target_risk_level)`
  - mypy strict、覆盖 ≥ 80%

### Phase 8 — 端到端集成测试

- **估时**：0.5 工作日
- **输入依赖**：Phase 1-7
- **交付物**：
  - `tests/eval/integration/test_e2e_tier1.py`：用 fake `merge-bin` 跑通 procedure.md §2 全部 5 步
  - fake `merge-bin` 实现（P2-3）：shell 脚本，把 `tests/eval/fixtures/dummy_run/runs/<id>/` 内容 copy 到 `<cwd>/.merge/runs/<run_id>/`，并 echo `ci_summary.json` 到 stdout
- **GO 条件**：
  - 单测试函数串起 `prepare → run → diff → summarize → gate`，断言 `eval_acceptance_*.json.verdict == "PASS"`

### Phase 9 — CI 接入（P0-4 重写）

- **估时**：0.5 工作日
- **输入依赖**：Phase 8
- **交付物**：
  - `.github/workflows/ci.yml` 在现有 `test` job 后追加显式 step：
    ```yaml
    - name: Lint eval scripts (ruff)
      run: |
        ruff check scripts tests/eval
        ruff format --check scripts tests/eval
    - name: Type check eval scripts (mypy)
      run: mypy scripts tests/eval
    - name: Eval unit + e2e tests
      run: |
        pytest tests/eval/unit/ tests/eval/integration/ -v \
          --cov=scripts/eval --cov-report=term-missing --cov-fail-under=80
    - name: Verify dataset locks
      run: python scripts/eval/lock.py --verify
    - name: Fork name purity check
      run: python -m scripts.eval._fork_name_check scripts/eval tests/eval
    ```
  - 新增 manual-only `eval-tier1` job（`workflow_dispatch` 触发 + `schedule: cron: '0 18 * * *'` placeholder，注释标 "nightly placeholder, not blocking"）跑 Phase 8 e2e + 后续真实 sample 评估
- **GO 条件**：
  - PR 上现有 `test` job 时长不增加 > 30s（unit 测试很快）
  - `tests/eval/unit/` 全部跑过且覆盖 ≥ 80%（独立 cov source = `scripts/eval`，与 `src` 80% 解耦）
  - `eval-tier1` job 可手动触发并跑完
  - 不对 PR 阻塞（facts.md §11）
  - 关键修正点（P0-4）：
    - **必须**新增上面 5 个 step；不能假设 `pytest tests/` 会跑 `tests/eval/`（CI 显式给路径 `tests/unit/`，不会自动包含）
    - cov source 独立：用 `--cov=scripts/eval` 而非 `--cov=src`，避免 `pyproject.toml [tool.coverage.run] source = ["src"]` 默认值污染
    - `mypy scripts tests/eval` 与现有 `mypy src` 并列，独立 step

---

## 3. 关键技术决策（v2 修订版）

### 决策 1：`diff.json` schema 设计

不变。schema 字段含 `rationale_length / discarded_content_present / is_security_sensitive` 三处扩展（用于 RCR / DCRR / SSER）。`DiffReport.meta.semantic_engine ∈ {"tree-sitter","fallback-bytes"}`。

per-file 数据来源严格遵循 P0-5 / P0-7：从 `merge_report_<run_id>.json` 读，**不**从 stdout `ci_summary.json` 读（后者只够 ER/JA 速算）。

### 决策 2：评估运行的隔离（P0-2 / P0-3 重写）

`run.py` 子进程的隔离机制：

1. **环境变量**：
   - 强制 `env.pop("MERGE_DEV", None)`（避免被开发者本机 `MERGE_DEV=1` 继承导致产物路径错位到 `outputs/debug/`）
   - `HOME=<workdir>/home`（临时目录，初始化为空，避免泄漏 `~/.config/code-merge-system/.env` 等真实凭据）
   - 默认注入 `ANTHROPIC_API_KEY=DUMMY-EVAL-KEY` / `OPENAI_API_KEY=DUMMY-EVAL-KEY`，仅当 `--use-real-keys` 时透传宿主 env
2. **cwd 隔离**：每 sample 子进程 `cwd=<workdir>/runs/<id>/_cwd`，子进程产物自然落到 `<cwd>/.merge/runs/<run_id>/`，符合 prod 模式真实行为（`src/cli/paths.py:39`）
3. **Memory wipe**：每 sample 跑前断言 `<cwd>/.merge/memory.db` 不存在；跑后不需 wipe（cwd 整体丢弃即可）
4. **产物拷贝**（P0-5）：跑完后 `run.py` 把 `<cwd>/` 的 working tree、`<cwd>/.merge/runs/<run_id>/` 内的 reports、stdout 捕获，分别拷贝到 `<workdir>/runs/<id>/working_tree/`、`<workdir>/runs/<id>/`、`<workdir>/runs/<id>/ci_summary.json`

**v2 删除**：v1 决策 2 中 `MERGE_HOME` 的提法、`<workdir>/home/.claude/projects/` 删除步骤（与 merge 系统无关）。

### 决策 3：DET / CPC 的 N 次跑：串行（P1-1 / P1-7 补充）

`consistency.py` 不触发 N 次跑，只读已有的多个 `runs/`。N 次跑由调用方 shell 循环（procedure.md §4 已示例）或 `run.py --seed N --workdir/run-N` 串行触发。

**Cache 控制**：现 `merge` CLI **无** `--no-cache` 标志（实测 `grep --no-cache src/cli/` 无匹配）。本次评估的 cache 控制只能依赖：

- 每次 run 用独立 `--workdir/run-N`（不复用 cwd）
- Anthropic prompt-caching 5min TTL —— 间隔 ≥ 6min 触发新 N 次跑可降低 cache 命中
- 如需硬保证 N 次互不影响 → 后续给 `merge` CLI 加 `--no-cache` 选项（out-of-scope）

**Concurrency 与测时矛盾**（P1-7）：`run.py --concurrency` 用于 schema/正确性验证；任何用于发布决策的 cost/wall_time 数据必须 `--concurrency=1` 并写入 `run_meta.json.concurrency=1`，`summarize.py` 在 N>1 时自动在 report 头部标注 "wall_time/cost not authoritative"。

### 决策 4：AST 等价比较（P1-5 收窄）

1. 优先 `import tree_sitter`（仅 optional `[ast]` extras 已装时）→ 解析后对比"去 comment / whitespace 节点"的 canonical 形式
2. 失败降级 → 字节 normalize：仅去 BOM + `\r\n→\n` + trim 行尾空白，**不**做正则去注释
3. `.json / .yaml / .toml` 走 normalize+sort（pydantic / json / yaml 标准库读取后对比）
4. 二进制文件走严格字节相等

`DiffReport.meta.semantic_engine` 标注实际引擎，避免 fallback 误算成"AST 等价"。

**v2 删除**：v1 中"按文件后缀分派 `# // /* */ <!-- -->` 去注释"——P1-5 指出对 URL/正则字符串内的 `//` 会误删，风险 > 收益。如未来一定要去注释，必须用 tokenizer（`tokenize` 标准库 for Python），不要用正则。

### 决策 5：`lock.py` 用 sha256

不变。`lock.json` schema 同 v1。

### 决策 6：报告模板用 jinja2

不变。

### 决策 7：`acceptance.md` 阈值机器可读化（P1-6 补充）

`tests/eval/manifests/acceptance_thresholds.yaml` schema：

```yaml
synced_with_sha: <acceptance.md sha256>
synced_at: <ISO timestamp>
hard_gates:
  - id: WMR
    threshold: 0.0
    operator: "=="
    source: "Tier-1 + Tier-2 + Tier-3"
  # ...
soft_gates:
  - id: OA
    threshold: 0.92
    operator: ">="
    source: "Tier-1"
  # ...
```

`lock.py --verify` 调 `_assert_acceptance_sync()`：

- sha 不一致 → 输出 warning（包括纯 typo 修订），提示跑 `lock.py --update-acceptance-sync`
- 在 CI 中 sha 不一致 → error（通过 env `CI=true` 判定；本地开发不阻塞）
- `--update-acceptance-sync` 仅更新 yaml 中的 `synced_with_sha` 与 `synced_at`，**不**改 thresholds 字段（避免脚本误改阈值）

### 决策 8：不引入新运行时依赖

不变。

### 决策 9（新增，P2-4）：本次不实现 `_git_oracle.py`

理由：facts.md §9 明确 Tier-2 历史回放是 out-of-scope，`_git_oracle.py` 在本次落地无 caller；按 memory `feedback_dead_code_check.md`，避免 defined-but-never-wired。Tier-2 实现纳入后续工作时再引入此模块。

---

## 4. 风险与对策（v2 修订）

| ID | 风险 | 概率 | 影响 | 对策 |
|---|---|---|---|---|
| R1 | `merge` CLI 在隔离环境下行为偏差 | 低 | 已大幅降低 | 决策 2 三层隔离已对齐真实代码（`paths.py:39 / :128-130`），P0 修复后剩余风险仅在边角 |
| R2 | tree-sitter 在 CI 编译困难 | 高 | 拖慢 Phase 4 | 决策 4 已定 fallback；CI 默认走 fallback，不安装 `[ast]` |
| R3 | Tier-1 真实 sample 构造需要人工内容 | 中 | Phase 1 阻塞 | 用最小可复现 sample（hello.py 类）；只为骨架可跑 |
| R4 | `acceptance.md` 与 yaml 阈值不同步 | 中 | 维护成本 | 决策 7 同步检查 + warning/error 分级（P1-6） |
| R5 | mypy strict 对 jinja2 / tar 覆盖不足 | 低 | Phase 0 / 5 | `cast` + 必要时模块级 `# type: ignore[import-untyped]` 仅限 `_report_render.py` |
| R6 | 评估脚本意外读到真实 `~/.merge/memory.db` | 低 | 决策 2 隔离已防 | 每 sample 独立 cwd + tmp HOME |
| R7 | `MergeState` schema 未来变化 | 中 | 后续维护 | `diff_against_golden.py` 用 `MergeState.model_validate_json` 直接走 pydantic |
| R8 | 评估 sample 误植 fork 名 | 中 | 违反 facts.md §9 | `_fork_name_check.py` 在 Phase 0 + Phase 9 CI 双检查（P1-4） |
| R9 | Phase 估时偏紧整体 Slip | 中 | 8→9-10 d | 总估时改为 8–10 d 区间（P2-1）；Slip 时优先砍 Tier-3 仅留 1 个 |
| R10 | `MERGE_DEV` 被开发者本机继承 | 高（如不防）| 产物路径完全错位 | 决策 2 / Phase 0 GO 强制 `env.pop("MERGE_DEV")` + fixture 单测断言（P1-3） |

---

## 5. 验收标准（与 procedure.md §2 命令清单逐项对应）

| 命令 | 验收点 | Phase |
|---|---|---|
| `python scripts/eval/lock.py --verify` | 退出 0；篡改后非 0；acceptance.md sha 不同步给 warning | Phase 1 / 6 |
| `python scripts/eval/lock.py --update` | 写出 3 份 `*.lock.json` | Phase 1 |
| `python scripts/eval/lock.py --update-acceptance-sync` | 仅更新 `acceptance_thresholds.yaml.synced_with_sha` | Phase 6 |
| `python scripts/eval/prepare.py --tier 1 --out /tmp/eval-tier1` | 每 sample 含 `working_tree/` + `golden_tree/` + `meta.yaml` | Phase 2 |
| `python scripts/eval/run.py --tier 1 --workdir ... --concurrency 4 --merge-bin <fake> --merge-args "--no-web --ci"` | 每 sample 产 `runs/<id>/{run_meta.json, ci_summary.json, merge_report_<run_id>.json, merge_report_<run_id>.md, plan_review_<run_id>.md, checkpoint.json, working_tree/}` | Phase 3 |
| `python scripts/eval/diff_against_golden.py --runs ... --datasets ... --output /tmp/.../diff.json` | `diff.json` 符合 procedure.md §3.2 + 扩展字段；per-file 来自 `merge_report_<run_id>.json` | Phase 4 |
| `python scripts/eval/summarize.py --diff ... --runs ... --output reports/eval_report_*.md` | 含全部六章节 + 至少 9 个指标值；concurrency>1 时头部标注 not authoritative | Phase 5 |
| `python scripts/eval/gate.py --report ... --acceptance ... --baseline ... --output reports/eval_acceptance_*.json` | json 含 `gates/verdict`，退出码三态；阈值读自 `acceptance_thresholds.yaml` | Phase 6 |
| `python scripts/eval/consistency.py --runs /tmp/eval-det --metric DET` | 输出 DET 数值 + 不一致样本表 | Phase 7 |
| `python scripts/eval/consistency.py --runs /tmp/eval-cpc --metric CPC` | 输出 CPC 数值 + 不一致样本表 | Phase 7 |

**全局验收**（在 Phase 9 CI 中实际生效）：

- `pytest tests/eval/unit/ tests/eval/integration/ --cov=scripts/eval --cov-fail-under=80` 通过（独立 cov，非 `--cov=src`）
- `mypy scripts tests/eval` 通过
- `ruff check scripts tests/eval` + `ruff format --check scripts tests/eval` 通过
- `python -m scripts.eval._fork_name_check scripts/eval tests/eval` 退出 0（覆盖 `scripts/eval/` + `tests/eval/`，P1-4）
- `python scripts/eval/lock.py --verify` 退出 0

---

## 6. 范围外（明确不做，与 facts.md §9 一致）

- Tier-2 真实历史回放仅 `.gitkeep` 占位，不构造实际 replay；本次**不**实现 `_git_oracle.py`（P2-4）
- Tier-3 仅 1 个 M3 示范样本，其余 5 类后续工作
- 不在 PR 上阻塞 evaluation；CI 仅纳入 unit + e2e + lint + lock-verify + fork-name-check
- 不修改 `src/` / `doc/evaluation/` / `pyproject.toml` 任何字符
- 不真实调用 LLM API；`run.py` 通过 `--merge-bin` 注入 fake 子进程
- **Tier-1 抽样矩阵免责声明（P0-6）**：本次只交付 1 个 Tier-1 sample 作为骨架。`doc/evaluation/dataset.md §2.2` 抽样矩阵要求"每格至少 5 个样本"（6×3=18 格 = 90 样本起步），由后续工作补齐；这意味着本次落地后 Tier-1 即便跑通也**无法用于发布决策**——所有 SSER/MMR/OA 等指标在 1 sample 上是 0/100% 的退化值，只能用于验证 schema/管线连通。`scripts/eval/__init__.py` docstring 与 `tests/eval/datasets/tier1/samples/README.md`（如需补）必须明确这一点
- 现 CLI 无 `--no-cache` 标志（P1-1），cache 硬隔离纳入后续工作

---

## 7. 总估时

| Phase | 估时 |
|---|---|
| 0 | 1.0 d |
| 1 | 0.5 d |
| 2 | 0.5 d |
| 3 | 2.0 d（v1 1.5d → v2 +0.5d，P2-1） |
| 4 | 1.5 d |
| 5 | 1.0 d |
| 6 | 0.5 d |
| 7 | 0.5 d |
| 8 | 0.5 d |
| 9 | 0.5 d |
| **合计** | **8.5 工作日**（最坏情况下可至 10 d，区间口径，P2-1） |

每 Phase 一次 commit + 一次本地 `pytest + mypy + ruff` 验证，可独立合入。

---

## 8. v1 → v2 修订摘要（按 review item 编号）

| Item | v2 处置 |
|---|---|
| P0-1 产物名修正 | §0 fixtures 文件名改为 `merge_report_<run_id>.{json,md}` + `plan_review_<run_id>.md` + `checkpoint.json`；§5 命令验收同步；Phase 3 GO 全量列出 |
| P0-2 `MERGE_HOME` 不存在 | 决策 2 重写：删 `MERGE_HOME`，强制 `env.pop("MERGE_DEV", None)`；Phase 0 / Phase 3 GO 同步 |
| P0-3 Memory 是 `.db` 文件 | 决策 2 §3 改为 `<cwd>/.merge/memory.db`；删 `.claude/projects/` 误述；Phase 3 GO 第 3 条修正 |
| P0-4 CI 漏跑 | Phase 9 重写：显式 5 个 step（lint / format / mypy / pytest / lock-verify / fork-check），独立 `--cov=scripts/eval` |
| P0-5 数据来源未指明 | §1 依赖图新增"Per-file decisions data flow"块；Phase 3 GO 加 `working_tree/` 拷贝；Phase 4 GO 明确 per-file 数据源 |
| P0-6 抽样矩阵冲突 | §6 加显式免责声明；`scripts/eval/__init__.py` docstring |
| P0-7 stdout JSON 不够用 | 决策 1 / Phase 3 GO 明写 `ci_summary.json`（stdout 捕获）+ `merge_report_<run_id>.json`（per-file 真相）双写 |
| P1-1 `--no-cache` 不存在 | 决策 3 增段说明 cache 控制方案 + 后续 work |
| P1-2 依赖图缺边 | §1 已新增 "Per-file decisions data flow" |
| P1-3 conftest 缺 MERGE_DEV 处理 | Phase 0 GO 加 fixture 断言；`_common.py.eval_subprocess_env()` 强制 pop |
| P1-4 fork 名 grep 位置 | 新增 `_fork_name_check.py`；Phase 0 GO + Phase 9 CI 双检查 |
| P1-5 AST 去注释风险 | 决策 4 收窄：fallback 不去注释，仅 BOM/换行/行尾空白 |
| P1-6 同步检查细节 | 决策 7 补 yaml schema + 三种处置（warning / CI error / `--update-acceptance-sync` 子命令） |
| P1-7 concurrency 与测时矛盾 | 决策 3 末尾补段；Phase 5 summarize 自动头部标注 |
| P2-1 估时偏紧 | Phase 3 1.5d→2d；总估时改为 8.5 d 区间口径（最坏 10 d） |
| P2-2 _schemas 行数 | §0 上限 250→400 |
| P2-3 e2e fake merge 契约 | Phase 8 交付物明写"copy fixtures + echo stdout" |
| P2-4 _git_oracle 死代码 | 删除该模块；新增决策 9 |
| P2-5 acceptance_thresholds 缺清单 | §0 加入清单；标注 Phase 6 创建 |

---

## 9. 修订记录（plan-amend）

### 9.1 [2026-05-15] gate.py soft gate 语义扩展（team-lead 决策方案 C）

**触发**：Verifier v1 → gatekeeper-test 审查 P0-4 决策歧义请示。
**决策权威**：team-lead 在无人值守模式下基于 `doc/evaluation/acceptance.md §2`（已含 absolute + relative 两类 soft gate 的事实）作出。
**完整记录**：`.multi-agent/eval-impl/locks/approved-facts.md` § `[plan-amend] gate.py soft gate 语义扩展`。

**对本计划 §决策 7 / §Phase 6 的修订**：

`acceptance_thresholds.yaml.soft_gates[]` schema 扩展：

```yaml
soft_gates:
  - id: <metric_id>
    kind: absolute | relative      # 必填
    threshold: <float>             # kind=absolute 时必填
    multiplier: <float>            # kind=relative 时必填（如 1.15 / 1.20）
```

`gate.py` 行为：
- `--baseline` **可选**
- 缺 baseline 时 `kind: relative` 全部 SKIP（写 `pass: null, skipped_reason: "no baseline"`），不影响 verdict
- exit 1：任一 hard fail（最高优先级）
- exit 2：任一 absolute soft < threshold OR 任一 relative soft 有 baseline 且 > baseline × multiplier

**对 Phase 6 实施的影响**：`_schemas.py` 中 `SoftGate` 模型按 `kind` 加 discriminator；`gate.py` 实现按上述路由。
