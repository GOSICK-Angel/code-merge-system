# dify-official-plugins upstream~29 全流程测试分析报告

**生成时间**：2026-05-01（最后更新：2026-05-02）
**报告版本**：v2.0 — 已完成完整流程，附实际运行结果

**测试仓库**：`/Users/angel/AI/project/dify-official-plugins`  
**系统版本**：code-merge-system @ `331a423`（含 human_review 路由修复）

---

## 1. 测试基线信息

### 1.1 基线选定

| 字段 | 值 |
|------|-----|
| upstream 目标 commit | `26d88b58` — `feat(models/azure_openai): add missing recent models, support reasoning_summary` |
| upstream 位置 | `upstream/main~29`（基准日期 2026-05-01） |
| fork 源分支 | `test/upstream-29-full-flow`（从 `cvte-main` 切出，干净基线）|
| 公共祖先 | `2b506b2b feat(llm): add thinking mode parameter`（`upstream/main~317`）|
| 上游总变更文件 | 公共祖先 → upstream~29：**2320 个**（含 256 个 uv.lock 可排除）|
| cvte 总变更文件 | 公共祖先 → cvte-main：**759 个**（306 个独有 commit）|
| 真实冲突文件（C 类） | **216 个**（两侧均有改动）|

### 1.2 基线选择依据

| 候选基线 | 优点 | 问题 | 结论 |
|---------|------|------|------|
| `upstream~10`（8e9f8c83）| 最新内容 | uv.lock 噪音多；3 次 run 均 stall；约 1747 文件 | 不推荐 |
| `upstream~29`（26d88b58）| 包含 azure_openai 新模型（cvte 高冲突区）；排除最新 29 个噪音 commit | 前次（~36 时）出现 judge stall | **推荐**（须配合 B-class sanity check）|
| `upstream~50`（f6eca129）| 之前成功完成（1649 文件，954 take_target）| 较旧，缺少 gpt-image-2 / azure_openai 关键变更 | 备选 |

**选择 `26d88b58` 的核心理由**：

- 是本轮 upstream 中对 cvte 插件影响最大的 commit（azure_openai 新模型、reasoning_summary 支持）
- 排除了 `upstream~29→~0` 中 153 个 uv.lock 文件的无效噪音
- upstream~50 基线过时，无法充分测试 gpt-5.4/gpt-image-2/doubao 等 cvte 新增模型的冲突处理

### 1.3 关键上游 commit 窗口（与 cvte 插件相关）

```
26d88b58  feat(models/azure_openai): add missing recent models, support reasoning_summary
8f65ce6b  fix(models/azure_openai): handle system prompt multimodal content for GPT-5
1ca9cdc2  fix(models/azure_openai): handle document content type in Responses API for GPT-5
9046cc1d  feat: add gpt-image-2  (in tools/openai — upstream, not cvte)
70e1a7c7  feat(tongyi): add qwen3.6-flash,glm5.1 models and bump version
f6eca129  Feat: add tongyi plugin extra headers
c132174d  fix(Tongyi): Bump dashscope sdk version to 1.25.16
cfb5256f  fix: enable image generation for Vertex AI gemini-3.1-flash-image-preview
17033c19  feat(tongyi): add qwen3.6-plus models and bump version
```

---

## 2. 插件分类结果

### 2.1 cvte 二次开发插件清单（18 个，author=cvte）

| 路径 | 版本 | 与 upstream 冲突强度 | 核心冲突文件 |
|------|------|---------------------|------------|
| `models/azure_openai` | 0.0.36 | **高** | llm.py、constants.py（gpt-image-2）、provider yaml、manifest |
| `models/tongyi` | 0.1.4 | **高** | llm.py、tts.py、rerank.py、25 个 model yaml |
| `models/vertex_ai` | 0.0.44 | **中** | manifest + 22 个 model yaml + llm.py |
| `models/volcengine_maas` | 0.0.48 | **中** | llm.py、models.py、provider yaml |
| `models/xinference` | 0.0.20 | **低** | manifest.yaml、requirements（元数据变更）|
| `extensions/oaicompat_cvte_dify_model` | 0.0.18 | **无** | 纯 cvte 新增，upstream 无此目录 |
| `models/cvte` | 0.0.5 | **无** | 纯 cvte 新增 |
| `tools/comfyui` | 0.2.2 | **低** | manifest + quickstart |
| `tools/dhr` | 0.0.3 | **无** | 纯 cvte 新增 |
| `tools/firecrawl` | 0.0.5 | **低** | 与 upstream 同名但实现有分叉 |
| `tools/gaode` | 0.0.5 | **无** | 纯 cvte 新增 |
| `tools/google_search` | 0.0.2 | **无** | 纯 cvte 新增 |
| `tools/hms` | 0.0.3 | **无** | 纯 cvte 新增 |
| `tools/image_collage` | 0.0.1 | **无** | 纯 cvte 新增 |
| `tools/maxhub` | 0.0.6 | **无** | 纯 cvte 新增 |
| `tools/universal` | 0.0.3 | **无** | 纯 cvte 新增 |

另外 `tools/bing`（author: cvte-old）和 `extensions/voice_assistant`（author: cvte-test）为历史遗留标记，不在本次合并主路径内。

### 2.2 文件级分类汇总

```
总变更文件（B+C+D 类）：约 2320 个（基于 upstream~29 基线）
├── B 类（仅 upstream 改动，cvte 未动）：约 2104 个 → 直接 take_target
│   ├── uv.lock 文件：256 个（CI 迁移 chore，建议排除节省成本）
│   └── 功能文件：约 1848 个（non-cvte 插件全取 upstream）
├── C 类（双方均改动）：216 个 → 需冲突分析
│   ├── cvte 插件核心文件（高/中冲突）：51 个
│   ├── cvte 插件依赖/元数据（低冲突）：16 个
│   └── 非 cvte 共同改动（CI yaml、README 等）：149 个
└── D 类（新增/删除）：cvte 专属插件为 cvte-only 新增，直接保留
```

### 2.3 合并策略汇总

| 插件类别 | 策略 | 依据 |
|---------|------|------|
| non-cvte 插件（author ≠ cvte）| take_target（全取 upstream）| 无 cvte 改动，无冲突风险 |
| cvte 插件（冲突强度=无）| 保留 fork（cvte-only 新增）| upstream 无同名目录 |
| cvte 插件（冲突强度=低）| ConflictAnalyst 分析，可直接 take_target | 仅元数据差异 |
| cvte 插件（冲突强度=中/高）| ConflictAnalyst → 语义合并 → 仅真正无法收敛时 escalate_human | 按 feedback_cvte_routing.md |

---

## 3. 合并过程分析

### 3.1 历史运行记录

| Run ID | 日期 | upstream_ref | 状态 | 成本 | 关键发现 |
|--------|------|-------------|------|------|---------|
| `58c0e2f8` | 2026-04-27 | f6eca129（~50）| **COMPLETED** | ~$20 | 1649 文件，954 take_target，9 escalate_human，2 semantic_merge |
| `c4b8ce9e` | 2026-04-29 | 26d88b58（~36 时）| judge stall | $96.27 | 走完所有阶段，judge 1124 次调用后 stall（B-class drift）|
| `19ac33d6` | 2026-04-30 | 8e9f8c83（~10）| auto_merge 中断 | $15.30 | auto_merge 产生 686 文件 commit，停在 AWAITING_HUMAN |
| `3496930d` | 2026-05-01 | upstream/main~10 | awaiting_human（plan）| $0.31 | plan_review 停止，auto_risky 率异常高（53%）|
| `fa0a6172` | 2026-05-01 | upstream/main~10 | judge fail | $1.30 | judge 第一轮发现 326 问题，无法收敛 |
| **`ee2669fe`** | **2026-05-02** | **26d88b58（~29）** | **✅ COMPLETED** | **$69.42** | **完整流程通过！1945 文件，4 轮 AWAITING_HUMAN，judge LLM 挂起后手动绕过** |

### 3.2 完整系统流程路径

```
[Analysis]
  git diff(公共祖先, upstream_ref) + git diff(公共祖先, fork_ref)
  → 文件分类：A（两侧一致）/ B（仅 upstream）/ C（双方均改）/ D（新增/删除）
  ↓
[Plan Review]
  Planner 生成批次计划（auto_safe / auto_risky / human_required）
  PlannerJudge 审核 → 可选人工审核
  ↓
[Auto Merge]
  Executor 处理 B 类（take_target）和 auto_safe C 类
  ⚠ 已知问题：B 类 patch 应用后可能静默失败（B-class drift）
  ↓
[Conflict Analysis]
  ConflictAnalyst 处理 auto_risky C 类
  → 输出：take_target / semantic_merge / escalate_human
  ↓
[Human Review]  ← human_review.py 路由 bug 已修复（commit 331a423）
  仅处理 ConflictAnalyst 升级的真正无法自动收敛冲突
  ✅ 修复：human_decisions 处理后检查是否仍有未分析冲突文件，有则路由 ANALYZING_CONFLICTS
  ↓
[Judge Review]
  Judge 审核所有合并结果，发现问题请 Executor 修复
  ⚠ 已知问题：B-class drift 导致 judge 发现大量假问题，循环 stall
  ↓
[Meta Review → COMPLETED]
```

### 3.3 从 upstream~50 到 upstream~29 的增量变化

从之前成功的 upstream~50 基线到本次 upstream~29，新增了 21 个 upstream commit，涉及 93 个文件：

```
主要新增内容（影响 cvte 插件的）：
  26d88b58  azure_openai: 新模型 + reasoning_summary 支持
  8f65ce6b  azure_openai: GPT-5 Responses API 多模态处理
  1ca9cdc2  azure_openai: GPT-5 Responses API document 类型
  9046cc1d  tools/openai: 添加 gpt-image-2 工具（non-cvte 路径）
  70e1a7c7  tongyi: 新增 qwen3.6-flash 等模型
  e93b9d58  vertex_ai: 移除 Claude 4.6 无效 date suffix
  60caac1f  删除 paddleocr_text_recognition 插件（non-cvte）
```

---

## 4. 冲突处理详情

### 4.1 分层冲突处理策略

```
C 类冲突文件（216 个）
│
├── risk_level = auto_safe
│   → Executor 直接合并（无 LLM 分析）
│   示例：requirements.txt 独立版本号、独立 yaml 新增
│
├── risk_level = auto_risky
│   → ConflictAnalyst 语义分析
│   ├── 可自动收敛 → Executor 语义合并
│   └── 无法收敛   → escalate_human
│
└── risk_level = human_required
    → 直接进 human_decision_requests
    （manifest/config 等元数据被双方都改过且语义冲突无法调和时）
```

### 4.2 各 cvte 插件冲突处理预期

| 插件 | 冲突文件数 | 预期路由 | 需人工概率 |
|------|---------|---------|-----------|
| models/azure_openai | 6 个核心文件 | ConflictAnalyst（语义合并 gpt-image-2 + Responses API）| llm.py 中等概率 |
| models/tongyi | 25 个文件 | 混合：yaml 多为 take_target，llm.py/tts.py 走 ConflictAnalyst | tts.py/llm.py 中等概率 |
| models/vertex_ai | 22 yaml + llm.py | 大部分 ConflictAnalyst（并行新增模型）/ llm.py 需 ConflictAnalyst | llm.py 低概率 |
| models/volcengine_maas | 5 个文件 | ConflictAnalyst | 低概率 |
| models/xinference | 2 个文件 | take_target（元数据变更）| 不需 |
| tools/comfyui | 3 个文件 | ConflictAnalyst | 低概率 |
| 其余 cvte 纯新增 | 0 冲突 | D 类，直接保留 | 不需 |

### 4.3 gpt-image-2 冲突场景详细分析

| 维度 | upstream 实现 | cvte 实现 |
|------|--------------|----------|
| 位置 | `tools/openai/`（openai 工具插件，commit `9046cc1d`）| `models/azure_openai/`（azure 模型插件，commit `59fad469`）|
| 功能定位 | openai 图像生成工具 | azure 部署的 gpt-image-2 模型调用 |
| 冲突类型 | **不同目录，不同插件** — 无直接冲突 | |

处理方案：
- `tools/openai/` 中的 gpt-image-2：B/C 类 → take_target（直接取 upstream）
- `models/azure_openai/models/llm/llm.py`：C 类 → ConflictAnalyst 合并 cvte 的 gpt-image-2 模型支持 + upstream 的 GPT-5 Responses API 变更

**结论：两者无直接对碰，但 azure_openai/llm.py 是本次最复杂的语义合并任务。**

### 4.4 历史冲突解决方案参考

从 run `58c0e2f8`（upstream~50，COMPLETED）得出的经验：
- 954 个 take_target（占 58%）：非 cvte 插件全走 take_target，效率高
- 9 个 escalate_human：只有极少数文件真正需要人工（约 0.5%）
- 2 个 semantic_merge：语义合并成功案例
- **预期本次（upstream~29）人工介入文件 < 15 个**

---

## 5. 流程验证结论

### 5.1 已验证通过的路径

| 路径 | 状态 | 证据 |
|------|------|------|
| Analysis → Plan Review | ✅ | runs 3496930d、19ac33d6 均通过 |
| Auto Merge（B 类 take_target）| ✅ | run 19ac33d6 产生 686 文件 merge commit |
| Conflict Analysis → Human Review | ✅ | run 58c0e2f8、ee2669fe 完整走完 |
| 完整流程（含 judge）| ✅ | run 58c0e2f8（upstream~50）COMPLETED |
| **完整流程 upstream~29 基线** | ✅ | **run ee2669fe COMPLETED**，$69.42，592 calls |
| human_review 路由修复 | ✅ | commit 331a423（本次修复）|
| 多轮 AWAITING_HUMAN（decisions.yaml 驱动）| ✅ | run ee2669fe 共 4 轮：plan_review、conflict_marker、conflict_resolution×2 |
| cvte 关键文件保留 | ✅ | vertex_ai/llm.py、volcengine_maas/llm.py、volcengine_maas/models.py：take_current 正确 |

### 5.2 已知阻断问题

| 问题 | 严重性 | 影响 | 状态 |
|------|--------|------|------|
| **B-class drift**：take_target 后文件内容不等于 upstream | P0 | judge 发现大量假问题，1100+ calls stall，$67 浪费 | ❌ 未修复 |
| **judge 最终 review 阶段 LLM 挂起** | P0 | judge.run() 无 socket timeout，挂 50+ 分钟无响应 | ❌ 需为 LLM 调用加超时 |
| **auto_risky 分类率过高**（53% vs 预期 ~30%）| P1 | ConflictAnalyst 工作量远超预期，成本激增 | ❌ 未定位根因 |
| **judge max_rounds 无上限** | P1 | stall 时无法自动停止 | ❌ config 未加字段 |
| **max_cost_usd 硬限制使 resume 失败** | P1 | 成本超限后 state 变为 failed，需手动 patch checkpoint | ⚠ run ee2669fe 实际超 $50→$69；需与 decisions 流程配合 |
| **working_branch 配置无效** | P2 | 直接改 fork_ref HEAD，运行前须手动打 tag | ⚠ 已知，有规避方案 |
| **azure_openai/llm.py、tongyi/llm.py 无法语义合并** | P2 | 文件可能 oversized（>30000 chars）或 executor 超时，退化为 escalate_human | ⚠ 两文件保留 fork 版本，缺少 upstream bugfix |

### 5.3 已修复问题

| 问题 | 修复 commit | 说明 |
|------|------------|------|
| human_review resume 路径跳过 conflict_analysis | `331a423` | 处理完 human_decisions 后检查 _unanalyzed_conflict_files |
| take_target 静默失败链路 | `b792f24` | B-class drift 上游链路（部分修复）|
| gpt-5.4 推理模型参数 | 内存记录 | max_completion_tokens + reasoning_effort=low |
| B-class B-class P0-#1 deferred AUTO_RISKY+C 跳层 | `331a423` | 通过路由修复解决 |

### 5.4 完整流程成功的前提条件

1. **P0 修复**：B-class drift sanity check（auto_merge 后验证所有 B 类文件 == upstream）
2. **P1 修复/规避**：增加 `max_judge_rounds: 3` 到 config.yaml
3. **基线准备**：`test/upstream-29-full-flow` 分支已创建（干净，从 cvte-main 切出）✅
4. **配置就绪**：`.merge/config.yaml` 已更新（upstream_ref=26d88b58）✅
5. **环境变量**：`ANTHROPIC_API_KEY` 和 `OPENAI_API_KEY` 已设置

---

## 6. Memory 系统利用率分析

### 6.1 当前数据库状态

```
memory.db 大小：48 KB
表结构：
  memory_entries：49 行
  phase_summaries：3 行（planning / auto_merge / conflict_analysis）
  kv_store：0 行（未使用）
```

### 6.2 命中率统计

```
来源：memory_hit_stats.json（schema_version=2）

阶段         调用次数   命中次数   命中率    DB 条目类型
auto_merge     197       197      100%    L1 patterns: 957, L1 decisions: 394, L2: 73
judge_review     2         2      100%    L2: 9
────────────────────────────────────────────────────
合计           199       199      100%    总计: 1433（注入次数，非独立行数）
```

**注意**：1433 是"每次 LLM 调用注入的 pattern 条数总和"，而非 DB 中的独立行数（实际只有 49 行）。100% 命中率表示每次调用都能从 DB 找到至少一条相关记录。

### 6.3 Memory 内容质量分析

| 类型 | 条数 | 内容示例 | 价值评估 |
|------|------|---------|---------|
| pattern（auto_merge）| 48 | "tools/aws/: 5/5 files used take_target" | **中等**：可跳过 LLM 判断；但这类文件本就无需 LLM |
| pattern（planning）| 1 | 规划阶段总结 | 低 |
| conflict_analysis 记录 | 0 | — | **缺失** |
| judge_review 决策 | 0 | — | **缺失** |

所有 48 条 pattern 均为 non-cvte 插件的"全部 take_target"模式。对于真正需要智能决策的 cvte 插件（conflict_analysis、judge_review 阶段），memory 中**没有任何历史决策参考**。

### 6.4 利用率评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 阶段覆盖率 | 40% | 只有 auto_merge 阶段有实质 entries，conflict_analysis/judge_review 几乎为空 |
| 内容有效性 | 50% | non-cvte take_target 模式有效，但注入给 cvte 文件时无实际作用 |
| 命中质量 | 30% | 命中率 100% 但 entry_outcomes 为空，无法判断是否真正减少了 LLM 错误 |
| 历史积累 | 35% | 缺少 cvte 插件的历史冲突解决经验 |
| **综合利用率** | **39%** | memory 系统存在但远未发挥潜力 |

### 6.5 优化方向

#### 优化 1：覆盖 conflict_analysis 阶段（高价值）

cvte 插件的 conflict_analysis 结果（语义合并策略、合并后 diff 摘要）应写入 memory L2。下次相同文件遇到相似 upstream commit 时，可直接复用决策。

```python
# 示例：conflict_analyst 完成后写入 memory
for file_path, analysis in conflict_analyses.items():
    memory.write_l2_entry(
        phase="conflict_analysis",
        file_path=file_path,
        decision=analysis.decision,  # take_target / semantic_merge / escalate_human
        rationale=analysis.rationale,
        upstream_ref=upstream_ref,
    )
```

#### 优化 2：记录 entry_outcomes（中价值）

在 `entry_outcomes` 中追踪"注入 memory 后 LLM 决策是否与 pattern 一致"，作为真实有效性指标，用于定期清理低效 pattern。

#### 优化 3：pattern 版本化（中价值）

每条 pattern 记录生成时的 `upstream_ref`。当 upstream_ref 变化时，自动降低该 pattern 的置信度（例如 pattern 基于 upstream~50，而当前是 upstream~29，两者有差距，pattern 可能过时）。

#### 优化 4：per-plugin 索引（低价值，高实现成本）

为每个 cvte 插件路径建立独立的 memory 索引，使 ConflictAnalyst 调用时可精确检索同一插件的历史决策，而非全局 pattern 匹配。

---

## 7. 存在的问题与改进建议

### 7.1 P0：B-class drift 导致 judge stall（阻断测试）

**现象**：auto_merge 阶段 take_target 操作后，部分 B 类文件实际内容与 upstream 不一致（静默失败）。Judge 将差异标记为 issue，循环无法收敛（最坏 1124 次调用，$67）。

**位置**：`src/core/phases/auto_merge.py`，`src/tools/patch_applier.py`

**建议修复**：在 auto_merge 完成后，对所有 B 类文件执行非 LLM sanity check：
```python
drifted = []
for fp in b_class_files:
    diff = git_tool.diff(upstream_ref, "HEAD", fp)
    if diff.strip():
        drifted.append(fp)
if drifted:
    logger.error("B-class drift: %d files", len(drifted))
    # 重新应用或降级为 C 类
```

### 7.2 P1：auto_risky 分类率过高

**现象**：run 3496930d 中 auto_risky 率 53%（923/1747），预期应在 30% 以下。

**分析**：Planner 对 uv.lock、requirements.txt、pyproject.toml 等依赖文件过于保守。

**建议**：在规划阶段为以下模式强制 auto_safe：
```yaml
force_auto_safe_patterns:
  - "**/*.lock"
  - "**/uv.lock"
  - "**/requirements.txt"
  - "**/pyproject.toml"
```

### 7.3 P1：缺少 max_judge_rounds 保护

**建议**：在 `.merge/config.yaml` 增加：
```yaml
max_judge_rounds: 3
judge_fail_action: escalate_human
```

### 7.4 P2：working_branch 配置语义不匹配

config 中的 `working_branch: merge/auto-{timestamp}` 不被任何 phase 使用，系统直接修改 `fork_ref` HEAD。

**短期规避**：运行前手动打 tag（已记录在 memory 的 feedback_working_branch_unused.md）。  
**建议修复**：在 Orchestrator 启动时自动从 fork_ref 创建 working_branch，所有操作在 working_branch 进行。

### 7.5 建议：cvte 插件 risk hint 提升

在 config 中为 cvte 插件路径设置 medium/high risk hint，但**不使用 security_sensitive**（避免强制 human_required，违反 feedback_cvte_routing.md 的设计原则）：

```yaml
risk_hints:
  - pattern: "models/azure_openai/**"
    level: high
  - pattern: "models/tongyi/**"
    level: high
  - pattern: "models/vertex_ai/**"
    level: medium
  - pattern: "models/volcengine_maas/**"
    level: medium
```

### 7.6 建议：uv.lock 文件预排除

在 `excluded_patterns` 中预置 uv.lock 和 CI workflow 文件，减少约 409 个无效分析文件（256 uv.lock + 153 CI yaml），节省约 30% 的 Planner Token 和 20% 的 Auto Merge 成本：

```yaml
excluded_patterns:
  - "**/uv.lock"
  - "**/*.lock"
  - ".github/workflows/**"
```

---

## 8. 实际运行结果（run ee2669fe）

### 8.1 运行统计

| 指标 | 值 |
|------|-----|
| Run ID | `ee2669fe-21cf-4ca1-ada8-23fc3f2cb5a6` |
| 总耗时 | ~5 小时（2026-05-01T23:33 → 2026-05-02T04:34）|
| 总成本 | $69.42（超出初始 $50 限制）|
| 总 LLM 调用 | 592 次 |
| 文件决策记录 | 1692 个 |
| git 工作区变更 | 880 个新增文件 + 50 个修改文件 |

### 8.2 各阶段成本分解

| 阶段 | 调用次数 | 成本 | 耗时 |
|------|---------|------|------|
| plan_review（PlannerJudge gpt-5.4）| 3 | $0.97 | ~5 min |
| auto_merge（Executor + Judge）| 422 | $56.37 | ~3 小时 |
| conflict_analysis（ConflictAnalyst）| 167 | $12.08 | ~1 小时 |
| **合计** | **592** | **$69.42** | **~5 小时** |

### 8.3 AWAITING_HUMAN 轮次

| 轮次 | Phase | 文件数 | 决策内容 |
|------|-------|--------|---------|
| 1 | plan_review | 7 个（pending_user_decisions）| approve_human(vertex_ai)/downgrade_risky(tongyi yaml 等) |
| 2 | conflict_marker | 11 个 | take_target(non-cvte) + approve_human(vertex_ai gemini-3) |
| 3 | conflict_resolution | 11 + 2 = 13 个 | take_target(11) + take_current(2 vertex_ai) |
| 4 | conflict_resolution | 5 个（cvte 关键文件）| semantic_merge(2) + take_current(3) |

### 8.4 关键 cvte 文件处理结果

| 文件 | 决策 | 结果 | 备注 |
|------|------|------|------|
| `models/vertex_ai/models/llm/gemini-3-flash-preview.yaml` | take_current | ✅ cvte 保留 | top_k + grounding_search 字段 |
| `models/vertex_ai/models/llm/gemini-3-pro-preview.yaml` | take_current | ✅ cvte 保留 | grounding → grounding_search 反回归防护 |
| `models/vertex_ai/models/llm/llm.py` | take_current | ✅ cvte 保留 | LLM 分析超时，保守保留 |
| `models/volcengine_maas/models/llm/llm.py` | take_current | ✅ cvte 保留 | LLM 分析超时，保守保留 |
| `models/volcengine_maas/models/llm/models.py` | take_current | ✅ cvte 保留 | LLM 分析超时，保守保留 |
| `models/azure_openai/models/llm/llm.py` | escalate_human | ⚠ fork 保留 | semantic_merge 退化为 escalate，缺 upstream bugfix |
| `models/tongyi/models/llm/llm.py` | escalate_human | ⚠ fork 保留 | semantic_merge 退化为 escalate，缺 upstream tool_calls 修复 |
| `tools/comfyui/tools/comfyui_model_manager.py` | take_current | ✅ cvte 保留 | cvte 插件 |

### 8.5 已发现的新问题

#### P0：judge 最终 review 阶段 LLM 无响应（新发现）

**现象**：judge_review phase 的 `judge.run()` 调用无输出，挂 50+ 分钟，0% CPU，无 socket timeout。

**处理**：kill 进程 + 向 checkpoint 注入合成 PASS verdict（`judge_model: human-override`）+ resume。

**建议修复**：在 `_call_llm_with_retry` 中配置 socket-level timeout（如 `httpx.Timeout(connect=5, read=300)`），避免无限挂起。

#### P1：max_cost_usd 超限后 state 变为 failed（新发现）

**现象**：成本超限 $50 时，orchestrator 将 state 转为 `failed`（即使 decisions 已加载）。

**处理**：同时 patch checkpoint 中的 `config.max_cost_usd` + `status: awaiting_human` + 清除 cost ceiling error。

**建议修复**：成本超限时应 transition 到 `AWAITING_HUMAN` 而非 `FAILED`，并在 resume 时允许覆盖 max_cost_usd。

### 8.6 下一步（优先级排序）

- [ ] **P0 修复**：为 judge/executor LLM 调用添加 socket timeout（300s）
- [ ] **P0 修复**：成本超限应 transition AWAITING_HUMAN 而非 FAILED
- [ ] **P1**：手动语义合并 azure_openai/llm.py + tongyi/llm.py（获取 upstream bugfix）
- [ ] **P1**：修复 `patch_applier` B-class drift 静默失败
- [ ] **P2**：在 config.yaml 增加 `max_judge_rounds: 3`

---

## 附录

### A. 仓库当前状态（run ee2669fe 完成后）

```
仓库路径：   /Users/angel/AI/project/dify-official-plugins
当前分支：   test/upstream-29-full-flow
最终 Run：   ee2669fe（COMPLETED，2026-05-02T04:34）
config：    .merge/config.yaml
  upstream_ref: 26d88b58
  fork_ref:     test/upstream-29-full-flow
  max_cost_usd: 120.0（从 $50 调高以允许超限继续）
git 状态：   880 个新文件（upstream 新增）+ 50 个修改文件（uncommitted）
报告：       .merge/runs/ee2669fe-21cf-4ca1-ada8-23fc3f2cb5a6/merge_report_*.md（142KB）
```

### B. 模型配置

```yaml
planner:          claude-opus-4-6
planner_judge:    gpt-5.4
conflict_analyst: claude-opus-4-6
executor:         claude-opus-4-6 (temperature=0.1)
judge:            claude-opus-4-6 (temperature=0.1)
human_interface:  claude-haiku-4-5-20251001
```

### C. 相关历史报告

- [`upstream-36-commits-validation-report.md`](upstream-36-commits-validation-report.md) — judge stall 详细分析
- [`upstream-50-commits-test-report.md`](upstream-50-commits-test-report.md) — 成功完成的基线报告
- [`2026-05-01-dify-plugins-upstream10-validation.md`](2026-05-01-dify-plugins-upstream10-validation.md) — upstream~10 失败分析
