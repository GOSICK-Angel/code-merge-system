# 文档索引

> **最后更新**：2026-04-17
> 中文版文档。英文版后续补充。

---

## 新人从这里开始

- [**新人上手指南**](modules/onboarding.md) — 环境、阅读顺序、常见改动起点

## 核心设计文档

| 文档 | 说明 |
|---|---|
| [architecture.md](architecture.md) | 系统架构总览：分层、数据流、持久化、扩展点 |
| [flow.md](flow.md) | 状态机与 8 个 Phase 的执行流程 |
| [risk-levels.md](risk-levels.md) | `RiskLevel` 枚举定义与触发条件 |
| [migration-aware-merge.md](migration-aware-merge.md) | 迁移感知合并（bulk-copy 场景） |
| [multi-agent-optimization-from-merge-experience.md](multi-agent-optimization-from-merge-experience.md) | 六大丢失模式 + P0/P1/P2 加固项（最新） |

## 模块技术文档（`modules/`）

| 模块 | 文档 |
|---|---|
| 数据模型 | [data-models.md](modules/data-models.md) |
| Agents | [agents.md](modules/agents.md) |
| Core（Orchestrator / StateMachine / Checkpoint / Phases） | [core.md](modules/core.md) |
| Tools（扫描器 / 门禁 / Git） | [tools.md](modules/tools.md) |
| LLM 层 | [llm.md](modules/llm.md) |
| 记忆系统 | [memory.md](modules/memory.md) |
| CLI / TUI / Web UI | [cli.md](modules/cli.md) |
| 新人指南 | [onboarding.md](modules/onboarding.md) |

## 参考开源项目分析（`references/`）

这些文档**不是系统设计**，而是对外部项目的学习笔记，用于提炼可借鉴的模式。

| 文件 | 项目 | 借鉴点 |
|---|---|---|
| [graphify-analysis.md](references/graphify-analysis.md) | Graphify | 知识图谱压缩代码上下文 |
| [mempalace-analysis.md](references/mempalace-analysis.md) | MemPalace | 语义索引 + 分层记忆 |
| [hermes-inspired-improvements.md](references/hermes-inspired-improvements.md) | NousResearch/hermes-agent | Agent 架构与工具抽象 |
| [opensource-comparison.md](references/opensource-comparison.md) | 15+ 合并相关开源项目 | 对照分析与能力矩阵 |
| [enhanced-context-memory-proposal.md](references/enhanced-context-memory-proposal.md) | 综合 MemPalace + Graphify | 基于上述项目的增强方案蓝图 |

## 查找路径速查

```
想了解什么                                           → 打开哪份
─────────────────────────────────────────────────────────────
项目是做什么的、整体设计怎么分层                    → architecture.md
一次合并怎么从输入到产出，每个 Phase 干啥            → flow.md
Agent 各自用什么模型、职责边界                       → modules/agents.md
Checkpoint 怎么落盘、状态机怎么转移                  → modules/core.md
某个具体扫描器原理（shadow / scar / sentinel…）      → modules/tools.md
                                                     + multi-agent-optimization-from-merge-experience.md
LLM 请求如何做成本/预算/熔断                         → modules/llm.md
记忆在 Agent 间是如何传递的                          → modules/memory.md
怎么用命令行、TUI 怎么和后端通信                      → modules/cli.md
Pydantic 模型到底长什么样                            → modules/data-models.md
fork 被 bulk-copy 迁移过怎么处理                     → migration-aware-merge.md
为什么要设计这么多扫描器                             → multi-agent-optimization-from-merge-experience.md
想学开源项目怎么做类似问题                           → references/
```
