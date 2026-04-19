# MemPalace 技术解析

> 面向初学者的技术分析，帮助理解 MemPalace 的核心设计思想。

## 目录

1. [MemPalace 是什么](#1-mempalace-是什么)
2. [它解决了什么问题](#2-它解决了什么问题)
3. [核心概念：记忆宫殿隐喻](#3-核心概念记忆宫殿隐喻)
4. [四层记忆栈](#4-四层记忆栈)
5. [知识图谱：带时间线的关系网](#5-知识图谱带时间线的关系网)
6. [技术栈与存储机制](#6-技术栈与存储机制)
7. [三大存储的协同关系](#7-三大存储的协同关系)
8. [MCP 协议集成](#8-mcp-协议集成)
9. [安全与可靠性设计](#9-安全与可靠性设计)
10. [MemPalace 与 GraphRAG 的区别](#10-mempalace-与-graphrag-的区别)
11. [优缺点分析](#11-优缺点分析)
12. [对我们项目的启发](#12-对我们项目的启发)

---

## 1. MemPalace 是什么

MemPalace 是一个 **AI 长期记忆系统**。它让 AI 助手（如 Claude、ChatGPT、Cursor 等）能够**跨会话记住信息**——用户的偏好、项目细节、人际关系等。

一个简单的类比：

```
没有 MemPalace 的 AI：每次对话都像失忆症患者，从零开始
有了 MemPalace 的 AI：像一个有笔记本的助手，能翻阅之前的记录
```

**项目信息**：Python 3.9+，MIT 许可，本地运行无需云服务。

---

## 2. 它解决了什么问题

LLM（大语言模型）有一个根本限制：**无状态**。每次对话结束，所有上下文都丢失。

| 场景 | 没有记忆 | 有 MemPalace |
|------|---------|-------------|
| 用户说"我喜欢简洁的代码风格" | 下次又得重新说 | 自动记住并应用 |
| 讨论过的架构决策 | 完全遗忘 | 可以回顾和引用 |
| 同事 Max 的项目进度 | 每次从头了解 | 按时间线追踪 |

市面上有 Zep 等付费云端记忆服务（$25+/月），MemPalace 用本地存储实现了同等效果，在标准基准测试 LongMemEval 中达到 **96.6% 的检索准确率**。

---

## 3. 核心概念：记忆宫殿隐喻

MemPalace 借用了一个古老的记忆术——**记忆宫殿法**（Method of Loci）。想象一栋大楼，信息被放在不同的房间里：

```
Palace（宫殿）= 整个记忆库
  │
  ├── Wing（侧翼）= 一个人或一个项目
  │     │
  │     ├── Room（房间）= 某个主题（如"工作"、"爱好"）
  │     │     │
  │     │     ├── Drawer（抽屉）= 一段具体的记忆内容
  │     │     └── Drawer
  │     │
  │     └── Room
  │
  ├── Wing
  │
  └── Tunnel（隧道）= 跨 Room 的交叉引用
```

**为什么用这个隐喻？** 因为它直觉地表达了信息的层级关系。当 AI 需要回忆"Max 的工作项目"，它知道去 `Max(Wing) → Work(Room)` 里找。

### Hall（走廊）：记忆分类

每个 Room 里的 Drawer 按类型归入不同的 Hall：

| Hall 名称 | 存放内容 | 举例 |
|-----------|---------|------|
| `facts` | 确定性事实 | "Max 是后端工程师" |
| `events` | 发生过的事件 | "3 月 5 日发布了 v2.0" |
| `discoveries` | 新发现的信息 | "发现缓存命中率只有 30%" |
| `preferences` | 偏好设定 | "喜欢用 Vim 而不是 VS Code" |
| `advice` | 建议和经验 | "部署前一定要跑 E2E 测试" |

---

## 4. 四层记忆栈

这是 MemPalace 最精巧的设计——**不是一次加载所有记忆，而是分层按需加载**。

```
┌──────────────────────────────────┐
│  L0: Identity（身份层）           │  ~50-100 tokens
│  始终加载，定义 AI 是谁            │  从 identity.txt 读取
├──────────────────────────────────┤
│  L1: Essential Story（核心故事）   │  ~500-800 tokens
│  始终加载，最重要的近期记忆         │  自动生成，上限 15 条
├──────────────────────────────────┤
│  L2: On-Demand Recall（按需回忆） │  ~200-500 tokens/次
│  对话中提到某主题时才加载           │  按 Wing/Room 过滤
├──────────────────────────────────┤
│  L3: Deep Search（深度搜索）      │  无上限
│  全量语义搜索，很少使用            │  搜索整个 Palace
└──────────────────────────────────┘
```

### 为什么要分层？

假设 AI 的上下文窗口是 200K tokens。如果一次加载所有记忆，可能用掉 50K+，留给实际对话的空间就不够了。分层设计让 AI 启动时只用 ~900 tokens（L0+L1），把 **95%+ 的窗口留给当前对话**。

```python
# wake_up() 方法：会话初始化时调用
def wake_up():
    identity = load_identity_file()      # L0: ~80 tokens
    essentials = get_top_memories(15)     # L1: ~600 tokens
    return f"{identity}\n{essentials}"   # 总共 ~680 tokens
```

### L1 的自动生成逻辑

L1 不是手动维护的，而是从整个 Palace 中**自动挑选权重最高的近期记忆**：

1. 遍历所有 Drawer（记忆条目）
2. 按 `权重 × 时效性` 排序
3. 按 Room 分组（避免某个主题占满名额）
4. 取前 15 条，拼接为摘要文本

---

## 5. 知识图谱：带时间线的关系网

除了"抽屉里的文本"，MemPalace 还维护了一个**时序知识图谱**——记录实体之间的关系，并且每个关系都有时间窗口。

### 基本结构

```
实体 (Entity)
  - name: "Max"
  - entity_type: "person"

三元组 (Triple)
  - subject: "Max"
  - predicate: "works_on"
  - object: "AuthService"
  - valid_from: "2026-01-15"
  - valid_to: null  (仍然有效)
```

### 时间切片查询

这是知识图谱最强大的功能——可以回答"某个时间点的状态"：

```
Q: "2026 年 2 月时，Max 在做什么项目？"

查询: SELECT * FROM triples
       WHERE subject = 'Max'
       AND valid_from <= '2026-02-01'
       AND (valid_to IS NULL OR valid_to >= '2026-02-01')

A: Max works_on AuthService (2026-01-15 ~ 至今)
   Max works_on CacheLayer  (2025-11-01 ~ 2026-01-30) ← 已失效
```

### 关系失效（Invalidation）

当某个关系不再成立时，不是删除它，而是设置 `valid_to`：

```python
# Max 不再负责 CacheLayer
invalidate(subject="Max", predicate="works_on", object="CacheLayer")
# 效果: valid_to = "2026-01-30"
# 历史记录保留，可追溯
```

---

## 6. 技术栈与存储机制

| 组件 | 技术 | 作用 |
|------|------|------|
| 向量存储 | ChromaDB | 语义搜索（"找类似的记忆"） |
| 关系存储 | SQLite (WAL 模式) | 知识图谱的三元组存储 |
| 通信协议 | MCP (JSON-RPC 2.0) | 与 AI 助手集成 |
| 数据目录 | `~/.mempalace/` | 所有数据本地存储 |

### Drawer 的存储方式

原始文本被分块后存入 ChromaDB：

```
原始文本 (2000 字符)
    ↓ 分块 (800字符/块, 100字符重叠)
    ↓
┌──────────────────┐
│ Chunk 1 (0-800)  │ → ChromaDB Document #1
│ Chunk 2 (700-1500)│ → ChromaDB Document #2  (重叠保留上下文)
│ Chunk 3 (1400-2000)│ → ChromaDB Document #3
└──────────────────┘
```

**确定性 ID**：每个 Chunk 的 ID 由 SHA256 哈希生成，基于内容本身。这意味着：
- 相同内容永远产生相同 ID（可去重）
- 内容修改后 ID 改变（可检测变更）

### 逐字存储原则

MemPalace 坚持**存原文，不做摘要**：

```
❌ 摘要存储: "Max 讨论了几个技术方案"  → 信息丢失
✅ 逐字存储: "Max 建议用 Redis 替代内存缓存，
              因为单机内存不够扩展到 10 个节点"  → 信息完整
```

代价是存储空间更大，但检索准确率从 84.2%（摘要）提升到 96.6%（原文）。

---

## 7. 三大存储的协同关系

MemPalace 内部有三大存储组件，它们各有分工、协同配合。用一个图书馆的类比来理解：

```
ChromaDB（向量存储）= 图书馆的"搜索引擎"
  → 你说"找关于支付系统的内容"，它按语义相似度返回结果

SQLite（关系存储）= 图书馆的"人物关系墙"
  → 你问"Max 2 月在做什么项目？"，它按实体+时间查表

~/.mempalace/（数据目录）= 图书馆的"大楼本身"
  → 所有东西都放在这栋楼里，包括搜索引擎和关系墙
```

### 7.1 物理结构

```
~/.mempalace/                          ← 数据目录（大楼）
  ├── chroma_db/                       ← ChromaDB 持久化目录
  │     └── mempalace_drawers/         ←   Collection: 所有记忆块
  │           ├── document_1.bin       ←     向量 + 原文
  │           ├── document_2.bin
  │           └── ...
  │
  ├── knowledge_graph.db               ← SQLite 数据库
  │     ├── entities 表                ←   实体 (Max, AuthService, ...)
  │     └── triples 表                 ←   关系 (Max works_on AuthService)
  │
  ├── identity.txt                     ← L0 身份定义
  ├── config.yaml                      ← 配置文件
  └── wal/
        └── write_log.jsonl            ← 写前日志（审计用）
```

### 7.2 职责分工

| 维度 | ChromaDB（向量存储） | SQLite（关系存储） |
|------|---------------------|-------------------|
| **存什么** | 原始文本块（800 字符/块） | 实体-关系-实体三元组 |
| **怎么查** | 语义搜索（"类似的内容"） | 精确查询（"谁做了什么"） |
| **索引方式** | embedding 向量 + 余弦相似度 | B-Tree 索引 + SQL WHERE |
| **时间维度** | 无（只有创建时间戳） | 有（valid_from / valid_to） |
| **数据粒度** | 自然语言段落 | 结构化三元组 |

### 7.3 完整协同流程

用一个完整场景说明三者如何配合：

```
用户说: "Max 上周从 AuthService 转去做 PaymentSystem 了"

┌─────────────────────────────────────────────────────────┐
│ Step 1: 写入 ChromaDB                                   │
│                                                         │
│   mempalace_add_drawer(                                 │
│     content = "Max 于 2026-04-04 从 AuthService          │
│               转到 PaymentSystem",                      │
│     wing = "Max",                                       │
│     room = "work",                                      │
│     hall = "events"                                     │
│   )                                                     │
│                                                         │
│   → 文本被分块，生成 embedding，存入 ChromaDB             │
│   → 下次搜索"Max 的工作变动"能语义匹配到                   │
│                                                         │
├─────────────────────────────────────────────────────────┤
│ Step 2: 更新 SQLite 知识图谱                              │
│                                                         │
│   mempalace_kg_invalidate(                              │
│     subject = "Max",                                    │
│     predicate = "works_on",                             │
│     object = "AuthService"                              │
│   )                                                     │
│   → 旧关系: valid_to = "2026-04-04"                     │
│                                                         │
│   mempalace_kg_add(                                     │
│     subject = "Max",                                    │
│     predicate = "works_on",                             │
│     object = "PaymentSystem"                            │
│   )                                                     │
│   → 新关系: valid_from = "2026-04-04", valid_to = NULL  │
│                                                         │
├─────────────────────────────────────────────────────────┤
│ Step 3: WAL 日志                                        │
│                                                         │
│   两条写操作都记录到 wal/write_log.jsonl                  │
│   → 可审计、可回溯                                       │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### 7.4 不同问题走不同通道

之后的查询场景展示了两个存储如何互补：

```
┌─────────────────────────────────────────────────────────┐
│ 场景 A: "Max 现在在做什么？"（精确问题）                   │
│                                                         │
│   → 走 SQLite 精确查询:                                  │
│     SELECT object FROM triples                          │
│     WHERE subject='Max' AND predicate='works_on'        │
│       AND valid_to IS NULL                              │
│   → 结果: "PaymentSystem"                               │
│                                                         │
├─────────────────────────────────────────────────────────┤
│ 场景 B: "Max 之前做过什么？"（时间线问题）                 │
│                                                         │
│   → 走 SQLite 时间线查询:                                │
│     SELECT * FROM triples                               │
│     WHERE subject='Max' AND predicate='works_on'        │
│     ORDER BY valid_from                                 │
│   → 结果: AuthService(2026-01 ~ 2026-04),              │
│           PaymentSystem(2026-04 ~ 至今)                  │
│                                                         │
├─────────────────────────────────────────────────────────┤
│ 场景 C: "关于支付系统迁移有什么背景信息？"（模糊问题）      │
│                                                         │
│   → 走 ChromaDB 语义搜索:                                │
│     search("支付系统迁移", wing="Max", room="work")       │
│   → 返回原文: "Max 于 2026-04-04 从 AuthService           │
│               转到 PaymentSystem"                       │
│   → 可能还匹配到其他相关段落                              │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

**核心协同逻辑**：

```
精确问题（谁/什么/何时）→ SQLite 知识图谱
模糊问题（相关/类似/背景）→ ChromaDB 语义搜索
两者互补，不互相替代
```

### 7.5 四层记忆栈中的存储分工

```
L0 (Identity)    → 读 identity.txt 文件（数据目录）
L1 (Essentials)  → 读 ChromaDB 高权重记忆（向量存储）
L2 (On-Demand)   → ChromaDB 按 wing/room 过滤搜索（向量存储）
L3 (Deep Search) → ChromaDB 全量语义搜索（向量存储）
                   + SQLite 实体关系补充（关系存储）
```

注意：**SQLite 知识图谱主要在 L3 和特定工具调用中使用**，不参与常规的分层加载。这是因为知识图谱回答的是"关系型问题"，而分层加载主要解决的是"哪些记忆最相关"。

---

## 8. MCP 协议集成

MCP（Model Context Protocol）是让外部工具与 AI 助手通信的标准协议。MemPalace 通过 MCP 暴露 19 个工具：

### 核心读写工具

```
读取类:
  mempalace_search(query, wing, room)  → 语义搜索
  mempalace_kg_query(entity)           → 查知识图谱
  mempalace_status()                   → 查看 Palace 状态

写入类:
  mempalace_add_drawer(content, wing, room, hall)  → 存记忆
  mempalace_kg_add(subject, predicate, object)     → 加关系
  mempalace_kg_invalidate(subject, predicate)      → 标记关系失效
```

### 工作流示例

```
用户: "Max 上周转去做支付系统了"

AI 内部操作:
1. mempalace_kg_invalidate("Max", "works_on", "AuthService")
2. mempalace_kg_add("Max", "works_on", "PaymentSystem")
3. mempalace_add_drawer(
     content="Max 于 2026-04-04 从 AuthService 转到 PaymentSystem",
     wing="Max", room="work", hall="events"
   )
```

---

## 9. 安全与可靠性设计

| 机制 | 说明 |
|------|------|
| 写前日志 (WAL) | 所有写操作先记录到 `wal/write_log.jsonl`，可审计 |
| 路径遍历防护 | Wing/Room 名称过滤特殊字符，防止 `../../etc/passwd` |
| 文件权限管控 | 配置目录 0o700，配置文件 0o600 |
| 长度限制 | 单条 Drawer 内容有最大字符数限制 |
| 三层配置优先级 | 环境变量 > 配置文件 > 硬编码默认值 |

---

## 10. MemPalace 与 GraphRAG 的区别

GraphRAG（微软 2024 年提出）也用到了知识图谱，但和 MemPalace 解决的是**完全不同层面的问题**。

### 10.1 什么是 GraphRAG

GraphRAG 是一种**增强检索生成**的方法：先用 LLM 从文档中提取实体和关系构建知识图谱，再用图谱的社区结构辅助检索和回答。

```
GraphRAG 流程:
  文档 → LLM 提取实体/关系 → 知识图谱 → 社区检测
                                          ↓
  用户提问 → 检索相关社区 → 社区摘要 + 原文 → LLM 回答
```

### 10.2 核心差异对比

| 维度 | MemPalace | GraphRAG |
|------|-----------|----------|
| **定位** | AI 助手的个人长期记忆 | 文档集合的检索增强 |
| **数据来源** | 对话历史、用户信息、项目笔记 | 大规模文档语料库 |
| **图谱构建方式** | 手动/半自动写入三元组 | LLM 自动从文本提取实体和关系 |
| **图谱用途** | 回答"谁/什么/何时"的精确问题 | 生成社区摘要，辅助全局性问题 |
| **检索方式** | 向量搜索 + 精确图查询（双通道） | 社区摘要 + 向量搜索（层级化） |
| **LLM 依赖** | 读写时**不需要** LLM | 图谱构建**必须** LLM |
| **成本** | 零 API 调用（本地运行） | 构建阶段大量 LLM 调用（昂贵） |
| **更新频率** | 实时（每次对话随写随存） | 批量（需要重新构建索引） |
| **时间维度** | 有（valid_from/valid_to） | 无（静态快照） |

### 10.3 架构差异图解

```
MemPalace:
  ┌───────────────┐     ┌──────────────┐
  │  ChromaDB     │     │  SQLite KG   │
  │  (语义搜索)   │     │  (精确查询)   │
  │               │     │              │
  │  原文分块     │     │  手动三元组   │
  │  embedding    │     │  时序关系     │
  └───────┬───────┘     └──────┬───────┘
          │                    │
          └────────┬───────────┘
                   │
            ┌──────┴──────┐
            │  MCP Server  │ ← AI 助手通过工具调用
            │  (19 个工具)  │
            └─────────────┘

GraphRAG:
  ┌────────────────────────────────────────┐
  │             LLM 提取层                  │
  │  文档 → 实体识别 → 关系提取 → 三元组    │ ← 构建阶段（昂贵）
  └──────────────────┬─────────────────────┘
                     │
  ┌──────────────────┴─────────────────────┐
  │           知识图谱 + 社区检测            │
  │  Leiden 算法 → 社区分组 → 社区摘要       │ ← LLM 生成摘要
  └──────────────────┬─────────────────────┘
                     │
  ┌──────────────────┴─────────────────────┐
  │           检索层                        │
  │  全局搜索: 社区摘要 → 排序 → 合并        │
  │  局部搜索: 实体 → 邻居 → 相关文本        │
  └────────────────────────────────────────┘
```

### 10.4 图谱构建方式的根本差异

这是最核心的区别：

```
GraphRAG 的图谱（全自动 + 昂贵）:
  输入: "张三在腾讯工作，负责微信支付项目。"
  LLM 自动提取:
    实体: [张三(person), 腾讯(org), 微信支付(project)]
    关系: [张三 --works_at--> 腾讯]
          [张三 --manages--> 微信支付]

  优点: 全自动，可以处理海量文档
  缺点: LLM 可能提取错误；每次构建都要花钱

MemPalace 的图谱（半手动 + 免费）:
  AI 助手在对话中识别到信息，显式调用工具:
    mempalace_kg_add("张三", "works_at", "腾讯")
    mempalace_kg_add("张三", "manages", "微信支付")

  优点: 精确可控，零成本，有时间维度
  缺点: 依赖 AI 助手主动调用，可能遗漏信息
```

### 10.5 各自擅长的问题类型

```
MemPalace 擅长:
  ✅ "Max 目前负责什么项目？"          → SQLite 精确查
  ✅ "上次讨论 Redis 缓存时说了什么？"  → ChromaDB 语义搜
  ✅ "Max 3 月份的工作变动有哪些？"     → SQLite 时间线查
  ❌ "所有项目的技术栈有什么共同趋势？"  → 需要全局推理，不擅长

GraphRAG 擅长:
  ✅ "这 500 篇论文的主要研究方向是什么？" → 社区摘要聚合
  ✅ "气候变化研究中有哪些跨学科联系？"   → 社区间连接分析
  ❌ "论文 A 的作者去年做了什么？"        → 没有时序，不擅长
  ❌ "用户偏好用 Vim 还是 VS Code？"     → 不是为个人记忆设计的
```

### 10.6 一句话总结

```
MemPalace = 个人助理的笔记本（精确、实时、廉价、个人化）
GraphRAG  = 图书馆的智能索引（全局、批量、昂贵、文档化）
```

两者解决的是**不同层面**的问题：MemPalace 解决"AI 记不住用户"，GraphRAG 解决"AI 理解不了大规模文档集"。它们可以共存——用 MemPalace 记住用户偏好和项目上下文，用 GraphRAG 理解项目的文档库。

---

## 11. 优缺点分析

### 优点

| 优点 | 说明 |
|------|------|
| 本地优先 | 零网络调用，数据不离开用户机器 |
| 分层加载 | 启动仅 ~900 tokens，极低的上下文开销 |
| 时序图谱 | 能回答"某个时间点的状态"，不只是"现在是什么" |
| 协议标准 | MCP 兼容主流 AI 工具（Claude、GPT、Cursor 等） |
| 检索准确 | LongMemEval 96.6% R@5 |

### 缺点

| 缺点 | 说明 |
|------|------|
| 逐字存储空间大 | 大量对话历史会积累大量数据 |
| 手动管理 Wing/Room | 需要用户或 AI 主动分类记忆 |
| 图谱无自动推理 | 只存储显式关系，不能推断隐含关系 |
| AAAK 压缩有损 | 实验性压缩格式准确率下降到 84.2% |

---

## 12. 对我们项目的启发

CodeMergeSystem 可以从 MemPalace 借鉴以下设计：

### 12.1 分层记忆加载

当前系统在每个阶段给 agent 注入**全量** memory。可以学习 L0-L3 分层：

```
当前方式:  agent.set_memory_store(全部 500 条记忆)
改进方式:
  L0: 项目 profile（语言、框架、文件数）       → 始终加载
  L1: 当前阶段相关的 top-10 pattern           → 始终加载
  L2: 当前处理文件路径匹配的记忆               → 按需加载
  L3: 全量搜索（仅在分析复杂冲突时使用）        → 极少使用
```

### 12.2 时序维度

给 `MemoryEntry` 增加时间有效性，追踪记忆的"新鲜度"：

```python
# 当前: confidence 是静态的
confidence: float = 0.8

# 改进: 加入时序信息
valid_from: str = "planning"     # 哪个阶段创建的
superseded_by: str | None = None # 被哪条更新的记忆替代
```

### 12.3 内容哈希去重

当前 `add_entry()` 没有去重检查。MemPalace 的 SHA256 确定性 ID 思路值得借鉴：

```python
# 基于内容生成确定性 ID，自动跳过重复记忆
entry_id = hashlib.sha256(content.encode()).hexdigest()[:16]
```
