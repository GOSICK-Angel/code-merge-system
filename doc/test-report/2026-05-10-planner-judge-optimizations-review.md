# Planner / PlannerJudge 优化（1-8 项）审查与实施计划

**日期**：2026-05-10
**审查范围**：本地未提交改动（8 文件，+547/-30 行）
**目标**：消化代码审查发现的问题，定义可执行的修复计划，确保通用合并 agent 不耦合具体测试仓库（dify-plugins）。

---

## 1. 改动概览

围绕 PlannerJudge 调优的 8 项优化：

| # | 优化 | 触及文件 |
|---|---|---|
| 1 | `risk_hint_patterns` 扩展（auth/oauth/otp/verify/2fa/permission/api_key 等）+ bump 0.15→0.25 | `src/models/config.py`, `src/tools/file_classifier.py` |
| 2 | Segment 跨轮签名缓存 | `src/llm/prompts/planner_judge_prompts.py`, `src/agents/planner_judge_agent.py` |
| 3 | 确定性 precheck（MISMATCH / NOT-BATCHED 不再走 LLM） | `src/llm/prompts/planner_judge_prompts.py`, `src/agents/planner_judge_agent.py` |
| 4 | `.env.example/.sample/.template` 降级到弱信号 | `src/models/config.py`, `src/tools/file_classifier.py` |
| 5 | Plan-review 整轮短路 APPROVE（前轮全部 ACCEPT 且应用） | `src/core/phases/plan_review.py` |
| 6 | Segment safelist 短路 LLM | `src/llm/prompts/planner_judge_prompts.py`, `src/agents/planner_judge_agent.py` |
| 7 | Planner LLM rescore 写回 `state.file_diffs` | `src/agents/planner_agent.py` |
| 8 | Planner 提示中加入鉴权 / 模板路径分类指引 | `src/llm/prompts/planner_prompts.py` |

新增测试：`tests/unit/test_planner_judge_optimizations.py`（14 case）。

---

## 2. 问题清单（按严重度）

### P0 — 必须修复（架构 / 安全）

#### P0-1 PlannerAgent 直接写 `state.file_diffs`
- **位置**：`src/agents/planner_agent.py:79-81`
- **问题**：`state.file_diffs = all_file_diffs` 违反项目"不变性 + agent 不写 state"约束（CLAUDE.md / `tests/unit/test_agent_contracts.py`）。Orchestrator 才是 state 唯一 writer。
- **影响**：架构腐蚀，未来 PlannerJudge / Executor 也可能仿效。
- **修复**：让 `PlannerAgent.run()` 输出携带 `rescored_file_diffs` 字段；orchestrator 在 phase 收尾持久化。同步更新 `src/agents/contracts/planner.yaml` 的 outputs 声明。
- **验证**：`tests/unit/test_agent_contracts.py` 应能在 contract 收紧后捕获越权写入。

#### P0-2 SAFELIST_PATTERNS 含 dify-plugins 私有模式
- **位置**：`src/llm/prompts/planner_judge_prompts.py:18-39`
- **问题**：通用合并 agent 中硬编码三个仓库特定模式：
  ```python
  "**/_position.yaml",     # dify 插件排序元数据
  "**/.difyignore",        # dify 工具链私有约定
  "**/_assets/**",         # 注释明说"shipped alongside plugins"
  ```
- **影响**：违反"代码与具体测试仓解耦"原则。
- **修复**：
  1. Python 内 `SAFELIST_PATTERNS` 仅保留生态通用项（lockfile / LICENSE / .gitignore / .gitattributes / .gitkeep）。
  2. 新增 `MergeConfig.plan_review.segment_safelist_patterns: list[str] = []`，从 `.merge/config.yaml` 读取。
  3. 实际 `is_segment_obviously_safe` 用 `default_safelist + config.segment_safelist_patterns`。
  4. 在 dify-plugins 项目的 `.merge/config.yaml` 追加三条覆盖。
- **验证**：新增测试 — 默认 safelist 不包含 `_position.yaml`；通过配置注入后才生效。

#### P0-3 Lockfile safelist 不限大小
- **位置**：`src/llm/prompts/planner_judge_prompts.py` `is_segment_obviously_safe`
- **问题**：`**/uv.lock` / `**/package-lock.json` 命中即跳 LLM，5000 行 lockfile 重生（典型 dep bump 或供应链注入）会被静默放过；下游也不会走 ConflictAnalyst（AUTO_SAFE）。
- **影响**：供应链投毒攻击场景下零 LLM 审视。
- **修复**：在 `is_segment_obviously_safe` 内对 lockfile 类（`safe_exts` 中含 `.lock` 的文件 / matches lockfile 子集）加 `lines_added + lines_deleted < 1000` 上限，超过即回退到 LLM。阈值可配置：`MergeConfig.plan_review.safelist_lockfile_max_lines: int = 1000`。
- **验证**：单测 — 5000 行 uv.lock 不被 safelist 接受。

### P1 — 应修复（误报 / 语义安全）

#### P1-1 `risk_hint_patterns` 中 `**/*token*` 误报面过大
- **位置**：`src/models/config.py:240-245`
- **问题**：与 bump=0.25 联动后，`tokenizer.py` / `tokens.json` / `bpe_tokens.json` 等 NLP/ML 高频文件批量从 AUTO_SAFE → AUTO_RISKY。
- **影响**：纯 ML 仓库 ConflictAnalyst LLM 调用量暴涨。
- **修复**：把 `**/*token*` 收窄为：
  ```python
  "**/*auth_token*",
  "**/*access_token*",
  "**/*api_token*",
  "**/*refresh_token*",
  "**/*bearer_token*",
  "**/*token_auth*",
  ```
  保留 `**/*credential*` / `**/*password*` / `**/*secret*` 不变。
- **验证**：单测 — `tokenizer.py` 不命中；`auth_token.py` 命中。

#### P1-2 短路 APPROVE 缺 ordering 类 issue 防护
- **位置**：`src/core/phases/plan_review.py:131-150`
- **问题**：前轮 issue 全部 ACCEPT+应用 + precheck 干净 ⇒ R{n+1} 直接 APPROVE。precheck 仅查 MISMATCH/NOT-BATCHED，不查批次顺序、跨文件依赖。
- **影响**：未来 issue_type 扩展到 `batch_ordering` 时，短路会放过 ordering 问题。
- **修复**：短路条件追加 — `last_verdict_issues` 中所有 issue 的 `issue_type ∈ {risk_underestimated, wrong_batch}`；出现其它类型时退回 LLM 复检。
- **验证**：单测 — 含 `batch_ordering` 类型 issue 时不短路。

#### P1-3 注释暴露 dify-plugins 校准来源
- **位置**：
  - `src/models/config.py:188` "blocks 8+ files in a typical dify-plugin merge"
  - `src/tools/file_classifier.py:174` "dify-plugin merges block on 8+ harmless template files"
  - `src/llm/prompts/planner_judge_prompts.py:14` "Calibrated to ... dify-plugins / typical fork merges"
- **问题**：通用 agent 注释引用具体仓库名，让维护者误以为这就是设计目标。
- **修复**：统一改为"插件类 / fork merge 场景"或"带 placeholder 的 env 模板批量合并"等通用表述。
- **验证**：`grep -r "dify" src/` 返回空（除非确属 dify 工具链相关）。

### P2 — 建议修复（代码质量）

#### P2-1 `_MISMATCH_TRACKED_LEVELS` 前向引用
- **位置**：`src/llm/prompts/planner_judge_prompts.py:94, 146`（使用），`:225`（定义）
- **问题**：源码组织违反"定义先于使用"。运行时无错（Python 函数体延迟绑定），但可读性差。
- **修复**：上提到 `SAFELIST_PATTERNS` 之前。

#### P2-2 `precheck_plan_integrity` 中 NOT-BATCHED 占位字段
- **位置**：`src/llm/prompts/planner_judge_prompts.py` `precheck_plan_integrity`
- **问题**：NOT-BATCHED 时 `current_classification=RiskLevel.AUTO_SAFE` 是占位，与"文件根本不在任何 batch"事实不符。
- **修复**：`PlanIssue.current_classification` 改为 `Optional[RiskLevel]`；NOT-BATCHED 传 `None`。下游展示层显式区分两种情况。

#### P2-3 单段路径不消费 `prior_segment_results`
- **位置**：`src/agents/planner_judge_agent.py:226-240`
- **问题**：单段只写 cache 不读 cache，与多段路径不一致。当部分 issue 被拒时 R{n+1} 仍全量重跑。
- **修复**：在单段路径内对照 cache，签名一致且前轮 APPROVED 则跳过；否则 LLM。
- **优先级低**：phase 层短路已覆盖大部分场景。

#### P2-4 `risk_hint_patterns` 大小写敏感
- **位置**：`src/models/config.py:215-249`
- **问题**：fnmatch 默认大小写敏感。`Login.py` / `OAuth.ts` 不命中 `*login*` / `*oauth*`。
- **修复**：在 `matches_any_pattern` 内统一 lower-case 双方再比对（注意 case-sensitive 文件系统的潜在副作用 — 实际仅影响匹配，不影响落盘）。
- **优先级低**：dify-plugins 多 lowercase；普适改进建议。

### P3 — 测试补强

#### P3-1 缺 `_merge_with_precheck` 独立单测
- **覆盖项**：去重（同 file_path 不重复追加）、APPROVED → REVISION_NEEDED 升级、`approved_files_count` 重新计算。
- **位置**：`tests/unit/test_planner_judge_optimizations.py` 增 2-3 case。

#### P3-2 缺 segment cache 命中跳过 LLM 的集成测试
- **覆盖项**：mock `_review_single`，验证多段第二次调用 `review_plan` 时被跳过的 segment 不触发 LLM。
- **位置**：同上文件，新增 1 case。

#### P3-3 缺 PlanReviewPhase 短路 APPROVE 的端到端测试
- **覆盖项**：mock `planner_judge.review_plan`，构造前轮全 ACCEPT 场景，验证 R1 未调用 LLM。
- **位置**：`tests/unit/test_plan_review_phase.py`（如不存在则新建）。

#### P3-4 缺 PlannerAgent state 写回回归测试
- **覆盖项**：rescored 后下游 agent（mock）看到 rescored `risk_level`。需配合 P0-1 修复后改为"通过 orchestrator 持久化"路径验证。

---

## 3. 实施顺序

按"先解耦、再修语义、再补测试"顺序，单 PR 不超过 ~300 行：

### Phase A — 解耦通用 agent（P0-2 + P1-3）
- [ ] 配置增加 `MergeConfig.plan_review.segment_safelist_patterns`（默认空）
- [ ] `SAFELIST_PATTERNS` 剥离 dify 三条
- [ ] 在 `.merge/config.yaml`（target dify-plugins 仓）追加三条
- [ ] 注释中所有 "dify-plugin" 字样替换为通用表述
- [ ] 测试：默认 safelist 不含被剥离模式；配置注入后生效
- **预计**：80 行 code + 30 行 test

### Phase B — 架构修正（P0-1）
- [ ] `PlannerAgent.run()` 输出 schema 增加 `rescored_file_diffs`
- [ ] 更新 `src/agents/contracts/planner.yaml` outputs 声明
- [ ] Orchestrator 在 plan phase 收尾时持久化到 `state.file_diffs`
- [ ] 移除 `state.file_diffs = all_file_diffs` 直接赋值
- [ ] 测试：contract test 收紧后捕获直接写 state 的回归
- **预计**：50 行 code + 40 行 test

### Phase C — 安全 / 语义边界（P0-3 + P1-1 + P1-2）
- [ ] Lockfile safelist 加 `safelist_lockfile_max_lines` 上限
- [ ] `risk_hint_patterns` 收窄 `*token*` 系列
- [ ] 短路 APPROVE 加 issue_type 白名单防护
- [ ] 测试：5000 行 lockfile 不命中 safelist；`tokenizer.py` 不命中 risk_hint；含 ordering issue 时不短路
- **预计**：40 行 code + 60 行 test

### Phase D — 代码质量（P2-1 至 P2-4）
- [ ] `_MISMATCH_TRACKED_LEVELS` 上提
- [ ] `PlanIssue.current_classification` 改 Optional
- [ ] 单段路径消费 cache
- [ ] `matches_any_pattern` lower-case 化（评估副作用后再决定）
- **预计**：60 行 code + 30 行 test

### Phase E — 测试补强（P3-1 至 P3-4）
- [ ] 4 类补测全部上齐
- [ ] coverage ≥ 80% 于 PlannerJudgeAgent / PlanReviewPhase 模块
- **预计**：100 行 test

---

## 4. 验收标准

提交所有 phase 后，必须满足：

- [ ] `mypy src` 通过
- [ ] `ruff check src/ tests/` 通过
- [ ] `pytest tests/unit/ --cov=src --cov-fail-under=80` 通过
- [ ] `tests/unit/test_agent_contracts.py` 全绿（特别是 anti-pattern 检测）
- [ ] `grep -r "dify" src/` 仅在显式 dify 适配层（如有）出现
- [ ] dify-plugins 测试基线（`upstream25` baseline）回归运行后：
  - 自动合并率不低于当前
  - PlannerJudge 总 LLM 调用次数不高于当前
  - 无新的 `AWAITING_HUMAN` 升级
- [ ] 在一个非 dify 仓（建议 `insforge-v2.1.0` baseline）跑一次合并，验证安全分类无明显回归

---

## 5. 风险与回滚策略

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| Phase A 配置剥离后 dify-plugins 实测 LLM 调用回升 | 中 | 中 | 在 dify 仓 `.merge/config.yaml` 同步追加 safelist；上线前 dry-run 比对 |
| Phase B contract 收紧捕获到现有其他 agent 越权写 state | 低 | 高 | 提前 `grep "state\." src/agents/*.py` 全量扫描，并行修复 |
| Phase C `*token*` 收窄后漏过真鉴权文件 | 低 | 中 | 与 ConflictAnalyst LLM 双层兜底；保留 `*api_key*` / `*credential*` 强匹配 |
| Phase C 短路条件收紧后回归测试触发 LLM 调用增加 | 低 | 低 | 仅当 issue_type 扩展时才生效；当前唯二类型不受影响 |

回滚单位 = 一个 Phase。任何 Phase 失败时只回滚该 Phase；后续依赖关系：B 依赖 A 的 contract 框架；C/D/E 互相独立。

---

## 6. 文件清单（落地后预期）

### 修改
- `src/agents/planner_agent.py`
- `src/agents/planner_judge_agent.py`
- `src/agents/contracts/planner.yaml`
- `src/core/phases/plan_review.py`
- `src/core/orchestrator.py`（Phase B）
- `src/llm/prompts/planner_judge_prompts.py`
- `src/llm/prompts/planner_prompts.py`
- `src/models/config.py`
- `src/models/plan_judge.py`（P2-2 Optional 化）
- `src/tools/file_classifier.py`

### 新增 / 增强
- `tests/unit/test_planner_judge_optimizations.py`（增量补 ~10 case）
- `tests/unit/test_plan_review_phase.py`（如需新建）

### 配置
- `<dify-plugins>/.merge/config.yaml`（追加 `segment_safelist_patterns`）

---

## 7. 参考

- 审查会话：本 session（2026-05-10）
- 上游优化提交：`18fd7cc feat: OpenAI Responses API + 项目 .env 覆盖 + 分段防御`
- 相关 baseline：`doc/test-report/dify-plugins-upstream25-merge-test-2026-05-08.md`
- 架构约束源：`CLAUDE.md` § "Architecture Constraints" / "Anti-Patterns"
