# Facts Anchors — large-scale-perf

> main agent 已核实的事实锚点。基于 `doc/large-scale-file-processing-optimization.md` 与代码现状交叉验证。
> Planner / Verifier / Executor / 三 Gatekeeper 应基于此做计划与实施，不要重新调研改写已锁的事实。
> 仓库 HEAD：`4826a6e` (feat/web)；日期：2026-05-18。

---

## A. 项目结构与约束（CLAUDE.md 已涵盖，此处补关键路径）

- A1. 源码根 `src/`，分层：`models/` → `tools/` → `llm/` → `agents/` → `core/` → `cli/` → `web/`。文件 <800 行，mypy strict，async/await 全程。
- A2. 单元测试 `tests/unit/`、集成 `tests/integration/`（不在 CI；需真实 API key）。覆盖率门槛 80%（`pyproject.toml --cov-fail-under=80`）。
- A3. 合约 yaml 全列表（仅这 7 个）：`src/agents/contracts/{conflict_analyst,executor,human_interface,judge,memory_extractor,planner,planner_judge}.yaml`。新增 `version: int` 字段时必须全部 7 个文件一起改。
- A4. 项目通用性约束（CLAUDE.md「Project Generality」）：`src/` 不得引入 target-repo 名 / 路径 / 域名硬编码；所有可变行为放 `.merge/config.yaml`。本优化方案所有阈值都必须支持 yaml 覆盖。
- A5. Agent 合约 anti-pattern（`tests/unit/test_agent_contracts.py` 强制）：reviewer agent 禁止写 state；禁止绕过 `BaseAgent._call_llm_with_retry`；缺字段不许默认值填充；prompt 必须走 `get_gate("<ID>")`；state 字段访问受 `restricted_view` 限制。

## B. 已实施的前置修复（L0 层，本方案的基础）

- B1. `844defc perf(executor): 切分 build_rebuttal LLM 调用避免单调用超时` — 已 push。
- B2. `4826a6e perf(planner): 切分 _classify_batch LLM 调用避免 125KB prompt` — 已 push。
- B3. 当前分支 `feat/web`，未推送的本地工作仅 `562daa9 chore(web,setup)` 等历史 commit。新 commit 都基于 `4826a6e`。

## C. ConflictAnalyst 现状（U1 改造对象）

- C1. `src/agents/conflict_analyst_agent.py:106-201` `analyze_file(...)` 主路径。`memory_store` 为 None 时 `builder` 不创建，**整个 staged_content 截断逻辑被跳过**（lines 117-172 嵌在 `if self._memory_store:` 内）。这是 U1.A 要修的耦合 bug。
- C2. `src/agents/conflict_analyst_agent.py:174-187` 调用 `build_conflict_analysis_prompt` + `_call_llm_with_retry`；失败 fallback 在 188-199（ESCALATE_HUMAN）。
- C3. `src/models/conflict.py:40-51` `ConflictAnalysis` 当前字段（无 `is_chunked` / `chunk_count`，U1 需新增）：`analysis_id, file_path, conflict_points, overall_confidence, recommended_strategy, conflict_type, can_coexist, is_security_sensitive, rationale, confidence, analysis_notes`。
- C4. `src/agents/contracts/conflict_analyst.yaml` 当前 `inputs`：`_merge_base, config, conflict_analyses, file_diffs, forks_profile, merge_plan, status`；`output_schema: ConflictAnalysis`；`gates: CA-SYSTEM, CA-THREE-WAY, CA-COMMIT-ROUND`。U1 增 `thresholds` 入参（若需读 chunked threshold）。

## D. Executor 现状（U1 复用 split_by_semantic_boundary，U5 复用 chunk fan-out 模式）

- D1. `src/agents/executor_agent.py:805-877` `build_rebuttal`：当 issues 超 `_REBUTTAL_CHUNK_SIZE` 切 chunks → `ParallelFileRunner.from_api_key_env_list(...)` 并发 → `_merge` 聚合。Chunk fan-out 模板 reference。
- D2. `src/agents/executor_agent.py:482-491` `split_by_semantic_boundary(...)` 复用入口（在 `execute_semantic_merge`）。U1 切 chunks 复用同一 helper。
- D3. `src/agents/executor_agent.py:879-916` `_run_rebuttal_chunk`：单 chunk LLM 调用样板，含 try/except + accept-all fallback。U1 `_run_conflict_chunk` 可类比。
- D4. `src/llm/prompt_builders.py:97` `build_staged_content(...)` 是 diff-aware 截断的真正实现；U1.A 解耦后需独立调用。

## E. Planner 现状（U1 chunked agg 设计参考；U6 per-file plan 扩展点）

- E1. `src/agents/planner_agent.py:588-666` `_classify_batch`：超 `_CLASSIFY_FILE_CHUNK_SIZE` 切 sub-chunks → ParallelFileRunner 并发 → `_merge_batch_plans` 聚合。已 P1 fast-path 模式（B2 commit）。
- E2. `src/agents/planner_agent.py:645-648` 调 `ParallelFileRunner.from_api_key_env_list(..., override=None)`——不依赖 `parallel_file_concurrency`。U4 RPM-aware 需保持向后兼容。
- E3. `src/models/plan.py:215-227` `MergePlan` 当前字段：`plan_id, created_at, upstream_ref, fork_ref, merge_base_commit, phases, risk_summary, category_summary, layers, project_context_summary, special_instructions, version`。**无 per_file_entries**（U6 新增）。
- E4. `src/models/plan.py:266-280` `MergePlanLive` 继承 MergePlan 加 execution/judge/gate/open_issues + pollution_summary 等。U6 编辑标记落 MergePlanLive 还是 MergePlan 待 Planner 决定。

## F. ParallelFileRunner 现状（U4/U5 改造对象）

- F1. `src/core/parallel_file_runner.py:1-66` 全文 66 行。`__init__(concurrency)` + `from_api_key_env_list` + `run_files(keys, handler)`。**无 RPM/TPM 感知**（U4 引入），**无 disjoint 校验**（U5 引入）。
- F2. `run_files` 用 `asyncio.Semaphore(concurrency)` + `asyncio.gather(_bounded)`；异常被 `_bounded` 捕获返回 `{key: exc}`，不取消兄弟任务。
- F3. `_bounded` 是改造 U4 的最小侵入点：拿 semaphore 后调 `await rate_budget.acquire(estimated_tokens)`。

## G. BaseAgent / Cost Tracker 现状（U2 budget cap 实施点）

- G1. `src/agents/base_agent.py:130` `class BaseAgent(ABC)`；`:172` `restricted_view(state)` 限制字段访问；`:426` `_call_llm_with_retry` 是**所有** LLM 调用的统一入口（合约 anti-pattern 强制）。U2 必须在此函数体内插入 budget 检查。
- G2. `src/agents/base_agent.py:434-463` 已有 circuit breaker + fallback provider 模式可参考。U2 `RunBudgetExceeded` 走类似 raise 路径。
- G3. `src/tools/cost_tracker.py:138` `total_cost_usd` property；`:192` summary 字典含 `total_cost_usd`。U2 budget cap 查这个属性。
- G4. `src/models/state.py:244-251` `cost_summary: dict | None`——checkpoint 前快照，**不是实时 source of truth**。BaseAgent 应查 `self._cost_tracker.total_cost_usd`，不查 `state.cost_summary`。
- G5. `src/core/orchestrator.py:262-280` **已有** `max_cost_usd` 字段 + ceiling 检查！当前实现方式：每轮 phase 开始前查 `prior + tracker.total >= ceiling` → transition AWAITING_HUMAN。U2 的"per-call budget check"是补充，不是替换——必须协同设计避免重复 transition。

## H. Orchestrator / 状态机现状（U2 + U7 实施点）

- H1. `src/core/orchestrator.py:240-247` worktree 启动逻辑已落地：`if self.config.enable_working_branch and state.active_branch is None: ... create_working_branch(...)`。U7 只改 default 即可生效（CLAUDE.md「Working branch」已实装）。
- H2. `src/core/orchestrator.py:346` `except Exception as e` 是当前唯一兜底。U2 在此之上新增 `except RunBudgetExceeded` 分支：写 partial report + transition AWAITING_HUMAN + checkpoint tag `budget_exceeded`。
- H3. `src/core/phases/` 目录下每个 phase 单文件；U6 编辑过的 per-file entry 在 `auto_merge.py` 短路使用。
- H4. 状态机终止状态见 `_TERMINAL`（orchestrator.py 顶部）。AWAITING_HUMAN 是合法终止之一。

## I. 配置 schema 现状（U1/U2/U3/U4/U7 改 config.py）

- I1. `src/models/config.py:931-936` `chunk_size_chars: int = 20000`（ge=5000）。已存在；U1 直接复用作为单文件切分阈值上限（doc §5.1.1 `chunk_size_chars * 2 = 40KB`）。
- I2. `src/models/config.py:943-948` `parallel_file_concurrency: int | None = None`。U4 RPM-aware 时此字段降级为 hard cap（rate_limits 不为 None 时 min(override, rate_budget) 取小）。
- I3. `src/models/config.py:949-954` `max_cost_usd: float | None = None`。U2 改默认值或新增 `per_run_cost_limit_usd`——doc 提议后者新名，但与 G5 现有字段重复。**待 Planner 决策**：是改名 / 共用 / 新增并 deprecate。
- I4. `src/models/config.py:956-963` `enable_working_branch: bool = False`。U7 改默认 True；CLAUDE.md 与 Setup wizard 同步。
- I5. `src/models/config.py:151-152` `risk_score_low/high` 已存在。U1 新增 `chunked_aggregation_min_confidence` 应放 `ThresholdConfig`（doc §5.1.2）。

## J. Web UI 现状（U2 budget bar / U6 per-file plan UI）

- J1. `src/web/` 文件：`app.py, serializers.py, static_server.py, ws_bridge.py`。U2 改 `serializers.py` 暴露 `limit_usd / warn_pct`；U6 改 `ws_bridge.py` 新增 `update_per_file_entry` 消息。
- J2. `web/src/views/` 文件：`RunDashboard.tsx, PlanReview.tsx, ConflictResolution.tsx, JudgeVerdict.tsx, Report.tsx, Setup.tsx`（含对应 test.tsx）。U2 改 RunDashboard，U6 改 PlanReview。

## K. 已知技术限制（不要试图绕过）

- K1. Anthropic transport 长请求 ~272 秒 timeout（doc §1.1 实测）；任何单次 LLM call 必须留出余量。
- K2. mgrep 工具有月度 100 次配额；探索时优先 grep / Read（mgrep 已耗尽，调研全程改用 grep）。
- K3. 现有 2307 测试全套不允许 regression；新 unit + integration 都要并存。
- K4. `pyproject.toml` cov-fail-under=80；新增模块覆盖率不能拉低总体。
- K5. asyncio_mode=auto；测试函数无需显式 `@pytest.mark.asyncio`。
- K6. `patch_llm_factory` 是 unit test 的 LLM mock 工具（CLAUDE.md「Testing Notes」）。

## L. 行业对照画像（doc §2）— 锁定不再重论证

- L1. SWE-agent: `per_instance_cost_limit=$3.00`；超 budget 触 `CostLimitExceededError` + autosubmit。U2 对标实现。
- L2. Cursor: Merkle tree 跨用户 chunk hash 重叠 92%。U3 cache 对标。
- L3. Continue: SQLite `tag_catalog` (path, branch, artifact_id) → (mtime, hash) 复合主键缓存。U3 schema 参考。
- L4. OpenHands: per-agent git worktree + `MAX_ITERATIONS≈100` + `LLM_NUM_RETRIES≈8` + 硬成本 cutoff。U7 默认开启对标。
- L5. LangGraph: `max_concurrency` 必须绑 provider RPM/TPM。U4 设计直接对应。
- L6. Claude Code 子 agent: 文件 disjoint contract 在 fan-out 前显式校验。U5 对标。
- L7. Copilot Workspace: per-file action-typed plan 是一等公民；人工可改。U6 对标。

## M. 7 个优化单元依赖图（doc §4.1，锁定）

```
U1 conflict_analyst chunked ──┐
                              ├──► U6 per-file editable plan v2
U7 worktree isolation ────────┘            ▲
                                           │
U2 per-run budget + autosubmit ──┐         │
                                 ├──► U3 cross-run cache
U4 RPM-aware concurrency ────────┤         │
                                 │         │
U5 file-disjointness contract ───┘         │
                                           │
                              完整生产化形态
```

- M1. U2 / U4 / U5 互相独立可任意顺序，但 U2 优先（用户痛点最显性）。
- M2. U3 依赖 U2 的 budget tracker 语义。
- M3. U6 依赖 U1。
- M4. U7 完全独立。
- M5. doc §9 推荐顺序：Day1 U1 → Day2 U2 → Day3 U5+U7（小且独立）→ Day4-5 U3 → Day6 U4+U6（并行）。

## N. 工时与体量预估（doc §4.2，锁定）

| Unit | 改动文件数 | LOC（净增） | 新测试数 | 估时 |
|---|---|---|---|---|
| U1 conflict_analyst chunked | 4 | ~250 | 6-8 | 1 天 |
| U2 budget + autosubmit | 6 | ~200 | 5-7 | 1 天 |
| U3 cross-run cache | 5 | ~300 | 6-8 | 1.5 天 |
| U4 RPM-aware concurrency | 3 | ~150 | 3-5 | 0.5 天 |
| U5 disjointness contract | 2 | ~80 | 3-4 | 0.5 天 |
| U6 per-file editable plan v2 | 5 | ~250 | 4-6 | 1 天 |
| U7 worktree defaults | 3 | ~50 | 2-3 | 0.5 天 |
| **合计** | 28 | ~1280 | 29-41 | **6 天** |

## O. 验收门槛（doc §10，锁定）

- O1. U1: 任何文件 >40KB 走 chunked；fast-path 命中率 ≥ 60%；hard cap 触发率 < 5%。
- O2. U2: 故意 over-budget 触发 → AWAITING_HUMAN + 报告文件存在。
- O3. U3: 二次 run cache 命中率 ≥ 90%（classifier + conflict_analyst）。
- O4. U4: 200 文件并发 fan-out 对 50 RPM provider → 0 个 429。
- O5. U5: 故意重合的 shard → 立刻 raise。
- O6. U6: Web UI 行可展开编辑 action + steps；改后 executor 走 human 选择 action。
- O7. U7: fresh repo 跑 `merge` → `git branch` 看到 `merge/auto-*`。
- O8. 总体：forgejo 1822-file repo 首次 run 在 budget 内完成；二次 run 5 分钟内（cache 命中）；全程无 429 / 无 transport timeout；plan review 可逐文件编辑；所有测试绿。

## P. 重要术语对齐（doc §附录 C，锁定）

- run = 一次 merge 命令执行
- chunk = LLM 调用粒度的输入切片
- shard = 并发 fan-out 时的工作单元（多个 chunks 组成一 shard）
- budget = 一个 run 累计的 LLM 美元成本
- fast path = 聚合规则允许跳过额外 LLM 调用时的短路路径
- disjoint = 多个 shard 的文件集两两无交集
- autosubmit = 触发 budget / 异常时强制落盘当前进度并转人工

## Q. 已存在但有待澄清的点（待 Planner 决策）

- Q1. `max_cost_usd`（已存在，I3 + G5） vs doc §5.2.1 `per_run_cost_limit_usd`（新提议）。重复字段——Planner 应选共用 / 改名 / 新增 deprecate。
- Q2. doc §5.3 提到 `contract_version`，本仓 7 个合约 yaml 当前 **无** `version:` 字段（A3）。U3 需新增字段定义且每个 yaml 都加。
- Q3. doc §5.7 提到「现有 working_branch 测试可能依赖 default=False」——U7 实施时必须 grep 现有测试，受影响者要么显式设 False 要么改断言。
- Q4. doc §5.6 PerFilePlanEntry 落 `MergePlan` 还是 `MergePlanLive`（E3/E4）——影响序列化路径与 Web UI 拉取入口，Planner 需明确。
