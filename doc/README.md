# 文档索引

> **最后更新**：2026-05-31
> 中文版文档。

---

## 新人从这里开始

- [**新人上手指南**](modules/onboarding.md) — 环境搭建、阅读顺序、常见改动起点

---

## 核心设计文档（根目录）

| 文档 | 说明 |
|---|---|
| [architecture.md](architecture.md) | 系统架构总览：分层、数据流、持久化、扩展点 |
| [flow.md](flow.md) | 状态机与 8 个 Phase 的执行流程 |

---

## 模块技术文档（`modules/`）

| 文档 | 说明 |
|---|---|
| [data-models.md](modules/data-models.md) | Pydantic 数据模型字段详解 |
| [agents.md](modules/agents.md) | 各 Agent 职责、模型选择、合作模式 |
| [core.md](modules/core.md) | Orchestrator / StateMachine / Checkpoint / Phases |
| [tools.md](modules/tools.md) | 扫描器 / 门禁 / Git 工具 |
| [llm.md](modules/llm.md) | LLM 路由、成本控制、熔断、压缩 |
| [memory.md](modules/memory.md) | 三层记忆系统、跨 run 持久化 |
| [cli.md](modules/cli.md) | CLI 命令、Web UI 与后端 WebSocket 通信 |
| [web-ui.md](modules/web-ui.md) | Web UI 组件设计与状态管理 |
| [web-ui-redesign-handoff.md](modules/web-ui-redesign-handoff.md) | Web UI 重设计交付说明 |
| [forks-profile.md](modules/forks-profile.md) | `merge forks-profile init/diff`；drift 检测；首次向导触发阈值 |
| [migration-aware-merge.md](modules/migration-aware-merge.md) | 迁移感知合并（bulk-copy 场景） |
| [risk-levels.md](modules/risk-levels.md) | `RiskLevel` 枚举定义与触发条件 |
| [onboarding.md](modules/onboarding.md) | 新人上手指南 |

---

## 计划与提案（`plan/`）

| 文档 | 说明 |
|---|---|
| [roadmap.md](plan/roadmap.md) | 产品路线图与里程碑 |
| [self-learning-system.md](plan/self-learning-system.md) | 自学习系统方案（深研究支撑，2026-05-30） |
| [per-hunk-resolution.md](plan/per-hunk-resolution.md) | 细粒度 hunk 级别冲突解决方案 |
| [merge-safety-complete.md](plan/merge-safety-complete.md) | 合并安全完整方案 |
| [dead-code-remediation-and-compression-plan.md](plan/dead-code-remediation-and-compression-plan.md) | 死代码清理与上下文压缩计划 |
| [large-scale-file-processing-optimization.md](plan/large-scale-file-processing-optimization.md) | 大规模文件处理优化 |
| [implementation-notes.md](plan/implementation-notes.md) | 实施过程笔记 |

---

## 合并质量审计（`review/`）

记录一次深度合并质量 + LLM 幻觉处理路径审计，以及后续 Wave 实施日志。

| 文档 | 说明 |
|---|---|
| [README.md](review/README.md) | 审计背景与文档索引 |
| [00-audit-findings.md](review/00-audit-findings.md) | 根因分析与确认缺陷 |
| [01-optimization-plan.md](review/01-optimization-plan.md) | 12 项优化计划 |
| [02-implementation-log.md](review/02-implementation-log.md) | Wave 1–3 实施日志 |
| [03-production-readiness.md](review/03-production-readiness.md) | Wave 3 后生产就绪度评估 |
| [04-production-hardening-plan.md](review/04-production-hardening-plan.md) | Wave 4 加固计划 |
| [05-wave4-implementation-log.md](review/05-wave4-implementation-log.md) | Wave 4 实施日志 |
| [06-production-readiness-post-wave4.md](review/06-production-readiness-post-wave4.md) | Wave 4 后生产就绪度 |
| [07-wave5-residual-closure-plan.md](review/07-wave5-residual-closure-plan.md) | Wave 5 残余问题关闭计划 |

---

## 评估体系（`evaluation/`）

| 文档 | 说明 |
|---|---|
| [README.md](evaluation/README.md) | 评估方案总览 |
| [metrics.md](evaluation/metrics.md) | 度量指标定义（含 P0 记忆有效性） |
| [acceptance.md](evaluation/acceptance.md) | 验收门槛 |
| [dataset.md](evaluation/dataset.md) | 数据集定义 |
| [procedure.md](evaluation/procedure.md) | 评估流程 |
| [EXECUTION_PLAN.md](evaluation/EXECUTION_PLAN.md) | 执行计划 |
| [IMPLEMENTATION_REPORT_PARTIAL.md](evaluation/IMPLEMENTATION_REPORT_PARTIAL.md) | 部分实施报告 |

---

## 测试报告（`test-report/`）

各版本与目标仓库的实测报告，按时间排列。

| 文档 | 说明 |
|---|---|
| [insforge-v2.1.0-merge-report-2026-05-06.md](test-report/insforge-v2.1.0-merge-report-2026-05-06.md) | InsForge v2.1.0 正式合并测试报告 |
| [dify-plugin-daemon-0.6.0-merge-validation.md](test-report/dify-plugin-daemon-0.6.0-merge-validation.md) | dify-plugin-daemon 0.6.0 合并验证 |
| [dify-plugins-upstream25-merge-test-2026-05-08.md](test-report/dify-plugins-upstream25-merge-test-2026-05-08.md) | dify-plugins upstream-25 合并测试 |
| [dify-plugins-upstream25-regression-2026-05-08.md](test-report/dify-plugins-upstream25-regression-2026-05-08.md) | dify-plugins upstream-25 回归分析 |
| [2026-05-01-dify-plugins-upstream10-validation.md](test-report/2026-05-01-dify-plugins-upstream10-validation.md) | dify-plugins upstream-10 验证 |
| [2026-05-10-planner-judge-optimizations-review.md](test-report/2026-05-10-planner-judge-optimizations-review.md) | Planner/Judge 优化 review |
| [forgejo-c-class-test-branches-2026-05-18.md](test-report/forgejo-c-class-test-branches-2026-05-18.md) | forgejo C-class 测试分支建立 |
| [forgejo-planner-judge-divergence-2026-05-18.md](test-report/forgejo-planner-judge-divergence-2026-05-18.md) | forgejo Planner/Judge 分歧分析 |
| [upstream-29-full-flow-analysis.md](test-report/upstream-29-full-flow-analysis.md) | upstream-29 全流程分析 |
| [upstream-36-commits-validation-report.md](test-report/upstream-36-commits-validation-report.md) | upstream-36 验证报告 |
| [upstream-50-commits-test-report.md](test-report/upstream-50-commits-test-report.md) | upstream-50 测试报告 |
| [merge-validation-report.md](test-report/merge-validation-report.md) | 通用合并验证报告 |
| [dify-plugin-daemon.md](test-report/dify-plugin-daemon.md) | dify-plugin-daemon 早期记录 |

---

## BUG 分析记录（`bugfix/`）

| 文档 | 说明 |
|---|---|
| [0527.md](bugfix/0527.md) | 2026-05-27 批次 BUG 分析与修复方案 |
| [0528-agent-prompt-engineering-review.md](bugfix/0528-agent-prompt-engineering-review.md) | 2026-05-28 Agent Prompt 工程化审查 |
| [0528-legacy-merge-base-attr.md](bugfix/0528-legacy-merge-base-attr.md) | 2026-05-28 遗留 merge_base 属性问题 |
| [0529-context-memory-opt-evaluation.md](bugfix/0529-context-memory-opt-evaluation.md) | 2026-05-29 上下文/记忆优化评估 |

---

## 参考开源项目分析（`references/`）

外部项目学习笔记，提炼可借鉴的模式，**不是系统设计**。

| 文档 | 项目 / 主题 | 借鉴点 |
|---|---|---|
| [graphify-analysis.md](references/graphify-analysis.md) | Graphify | 知识图谱压缩代码上下文 |
| [mempalace-analysis.md](references/mempalace-analysis.md) | MemPalace | 语义索引 + 分层记忆 |
| [hermes-inspired-improvements.md](references/hermes-inspired-improvements.md) | NousResearch/hermes-agent | Agent 架构与工具抽象 |
| [openai-agents-python-analysis.md](references/openai-agents-python-analysis.md) | openai-agents-python | 轻量 Agent 框架设计 |
| [claude-code-game-studios-analysis.md](references/claude-code-game-studios-analysis.md) | claude-code-game-studios | 多 Agent 游戏开发实证 |
| [opensource-comparison.md](references/opensource-comparison.md) | 15+ 合并相关开源项目 | 对照分析与能力矩阵 |
| [enhanced-context-memory-proposal.md](references/enhanced-context-memory-proposal.md) | 综合 MemPalace + Graphify | 增强方案蓝图 |
| [dependency-graph-optimization-plan.md](references/dependency-graph-optimization-plan.md) | 依赖图优化 | 基于 forgejo 实测的依赖图优化计划 |
| [multi-agent-optimization.md](references/multi-agent-optimization.md) | 合并实战经验 | 六大丢失模式 + P0/P1/P2 加固项 |

---

## 分享材料（`share/`）

| 文档 | 说明 |
|---|---|
| [agent-engineering-sharing.md](share/agent-engineering-sharing.md) | Agent 工程化经验分享 |
| [dependency-graph-deep-dive.html](share/dependency-graph-deep-dive.html) | 依赖图深度解析（HTML 演示） |

---

## 查找路径速查

```
想了解什么                                           → 打开哪份
─────────────────────────────────────────────────────────────
项目是做什么的、整体设计怎么分层                    → architecture.md
一次合并怎么从输入到产出，每个 Phase 干啥            → flow.md
Agent 各自用什么模型、职责边界                       → modules/agents.md
Checkpoint 怎么落盘、状态机怎么转移                  → modules/core.md
某个具体扫描器原理（shadow / scar / sentinel…）      → modules/tools.md
LLM 请求如何做成本/预算/熔断                         → modules/llm.md
记忆在 Agent 间是如何传递的                          → modules/memory.md
怎么用命令行、Web UI 怎么和后端通信                   → modules/cli.md
Pydantic 模型到底长什么样                            → modules/data-models.md
fork 被 bulk-copy 迁移过怎么处理                     → modules/migration-aware-merge.md
六大丢失模式 / 为什么要设计这么多扫描器              → references/multi-agent-optimization.md
想学开源项目怎么做类似问题                           → references/
合并质量审计历史、Wave 实施记录                      → review/
实测合并报告（dify/forgejo/insforge）                → test-report/
BUG 分析与修复记录                                   → bugfix/
评估指标、验收门槛、数据集定义                       → evaluation/
```
