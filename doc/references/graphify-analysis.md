# Graphify 技术解析

> 面向初学者的技术分析，帮助理解 Graphify 如何用知识图谱压缩代码上下文。

## 目录

1. [Graphify 是什么](#1-graphify-是什么)
2. [它解决了什么问题](#2-它解决了什么问题)
3. [处理管道：从代码到图谱](#3-处理管道从代码到图谱)
4. [AST 提取：让机器理解代码结构](#4-ast-提取让机器理解代码结构)
5. [图构建：节点、边和置信度](#5-图构建节点边和置信度)
6. [社区检测：自动发现模块边界](#6-社区检测自动发现模块边界)
7. [智能分析：God Node 与意外连接](#7-智能分析god-node-与意外连接)
8. [MCP 查询接口](#8-mcp-查询接口)
9. [缓存与增量处理](#9-缓存与增量处理)
10. [优缺点分析](#10-优缺点分析)
11. [对我们项目的启发](#11-对我们项目的启发)

---

## 1. Graphify 是什么

Graphify 是一个**代码库知识图谱生成工具**。它把源代码解析成一张"关系网"——每个类、函数、模块是节点，它们之间的调用、继承、导入关系是边。AI 助手不需要读原始代码，只需查询这张图就能理解代码库的结构。

一句话总结：**不让 AI 读代码，让 AI 查图谱。**

```
传统方式: AI 读取 500 个源文件 → 消耗 200K+ tokens
Graphify: AI 查询知识图谱    → 消耗 ~3K tokens (71.5x 压缩)
```

---

## 2. 它解决了什么问题

AI 编程助手的核心瓶颈是**上下文窗口有限**。一个中等规模的项目可能有几万行代码，全部塞进上下文不现实。

| 场景 | 传统方式 | Graphify 方式 |
|------|---------|-------------|
| "这个类继承了谁？" | 读取整个文件找 `extends` | 查图谱的 `inherits` 边 |
| "哪些文件依赖 utils.py？" | 全局搜索 `import utils` | 查图谱的 `uses` 入边 |
| "修改 A 会影响哪些模块？" | 人工追踪调用链 | 图谱 BFS 遍历 |
| "代码库的核心模块是哪些？" | 通读所有文件 | 查 God Nodes（连接最多的节点） |

---

## 3. 处理管道：从代码到图谱

Graphify 的处理流程是一条**线性管道**，每个阶段独立、可测试：

```
detect() → extract() → build_graph() → cluster() → analyze() → report() → export()
  │            │            │              │            │           │          │
  │            │            │              │            │           │          │
找到所有    AST 解析     合并为        社区检测     识别关键    生成报告   导出为
源文件     提取结构     NetworkX 图   (Leiden)     节点/连接              多种格式
```

### 每个阶段做什么

| 阶段 | 输入 | 输出 | 是否需要 LLM |
|------|------|------|-------------|
| `detect()` | 项目目录 | 文件路径列表 | 否 |
| `extract()` | 源文件 | `{nodes, edges}` 字典 | 否（AST 解析） |
| `build_graph()` | 所有提取结果 | NetworkX 图对象 | 否 |
| `cluster()` | 图对象 | 带社区标签的图 | 否 |
| `analyze()` | 带社区的图 | God Nodes、意外连接 | 否 |
| `report()` | 分析结果 | GRAPH_REPORT.md | 否 |
| `export()` | 图对象 | HTML/JSON/SVG/... | 否 |

注意：**核心管道完全不需要 LLM 调用**。只有多模态内容（图片、文档）的语义提取才用 Claude 子代理。这意味着基本使用是零成本的。

---

## 4. AST 提取：让机器理解代码结构

AST（Abstract Syntax Tree，抽象语法树）是编译器用来理解代码结构的内部表示。Graphify 用 tree-sitter 库把代码解析成语法树，然后从中提取有意义的结构。

### 一个例子

```python
# 源代码
class UserService:
    def __init__(self, db: Database):
        self.db = db

    def get_user(self, user_id: str) -> User:
        return self.db.query(User, user_id)
```

经过 AST 提取后变成：

```
节点:
  - UserService (type: class)
  - UserService.__init__ (type: method)
  - UserService.get_user (type: method)

边:
  - UserService --contains--> __init__
  - UserService --contains--> get_user
  - UserService --uses--> Database  (来自类型注解)
  - UserService --uses--> User      (来自返回类型)
```

### 支持的语言

Graphify 支持 **16 种编程语言**的 AST 解析：

```
Python, JavaScript, TypeScript, TSX, Go, Rust, Java,
C, C++, C#, Ruby, PHP, Swift, Kotlin, Scala, Lua
```

每种语言有专用的提取函数（策略模式），通过文件扩展名自动路由：

```python
LANGUAGE_MAP = {
    ".py": extract_python,
    ".js": extract_javascript,
    ".ts": extract_typescript,
    # ...
}
```

### 优雅降级

如果某种语言的 tree-sitter 绑定未安装，不会崩溃，而是返回空结果：

```python
try:
    parser = get_parser(language)
    tree = parser.parse(source_bytes)
    return extract_from_tree(tree)
except ImportError:
    return {"nodes": [], "edges": []}  # 跳过而不是报错
```

---

## 5. 图构建：节点、边和置信度

### 节点模型

每个节点代表代码中的一个"实体"：

```python
node = {
    "id": "src/services/user_service.py::UserService",  # 全局唯一
    "label": "UserService",                               # 显示名称
    "type": "class",                                      # class/function/method/import
    "file": "src/services/user_service.py",               # 来源文件
    "line": 5,                                             # 所在行号
}
```

### 边模型与置信度分级

这是 Graphify 最值得学习的设计之一——每条边都有**置信度标签**：

```python
edge = {
    "source": "UserService",
    "target": "Database",
    "relation": "uses",
    "confidence": "EXTRACTED",  # 置信度等级
}
```

| 置信度 | 含义 | 举例 | 权重 |
|--------|------|------|------|
| `EXTRACTED` | 从代码中确定性提取 | `class A(B)` → A inherits B | 1.0 |
| `INFERRED` | 合理推断但有不确定性 | 调用图推断的 `calls` 关系 | 0.8 |
| `AMBIGUOUS` | 不确定，需人工审核 | 动态调用、反射 | 0.5 |

### 为什么需要置信度？

考虑这段 Python 代码：

```python
# 确定性: EXTRACTED
class PaymentService(BaseService):  # 明确的继承关系
    pass

# 推断性: INFERRED
def process():
    service = get_service("payment")  # 字符串查找，推断关联
    service.charge()                   # 推断 process() calls PaymentService

# 模糊性: AMBIGUOUS
handler = getattr(module, handler_name)  # 运行时才知道调的是谁
handler()
```

没有置信度标签，AI 会把所有关系同等对待。有了标签，AI 知道哪些关系是铁板钉钉的，哪些需要更多验证。

---

## 6. 社区检测：自动发现模块边界

社区检测回答一个问题：**哪些代码"应该在一起"？**

### Leiden 算法

Graphify 使用 Leiden 算法（graspologic 库），基于**图的拓扑结构**——连接紧密的节点被归为一个社区：

```
社区 0: [UserService, UserRepository, UserModel]     ← 用户模块
社区 1: [PaymentService, StripeClient, Invoice]       ← 支付模块
社区 2: [AuthMiddleware, TokenManager, SessionStore]   ← 认证模块
```

### 关键设计细节

**大社区自动拆分**：如果某个社区超过总节点的 25%，递归拆分：

```
社区 0 (300 个节点, 占比 40%) → 太大！
  ├── 社区 0a (120 个节点)
  └── 社区 0b (180 个节点) → 仍然太大
        ├── 社区 0b-1 (90 个节点)
        └── 社区 0b-2 (90 个节点)
```

**内聚力评分**：衡量社区内部连接的紧密程度：

```
cohesion_score = 实际边数 / 最大可能边数

社区 [A, B, C]:
  最大可能边数 = 3 (A-B, A-C, B-C)
  实际边数 = 2 (A-B, B-C)
  cohesion_score = 2/3 = 0.67
```

### 为什么不用 embedding？

| 方案 | 优点 | 缺点 |
|------|------|------|
| Embedding 聚类 | 语义相似性好 | 需要向量数据库，不可解释 |
| Leiden 图聚类 | 基于真实调用关系，可解释，确定性 | 只看结构不看语义 |

Graphify 选择 Leiden 是因为：代码的模块边界由**调用关系**决定，不是由"名字长得像"决定。

---

## 7. 智能分析：God Node 与意外连接

### God Node（上帝节点）

连接数异常多的节点，通常是系统的核心或潜在的坏味道：

```
God Nodes:
  1. DatabaseManager (degree: 45) ← 几乎所有模块都依赖它
  2. ConfigLoader (degree: 32)    ← 全局配置
  3. Logger (degree: 28)          ← 日志工具
```

**智能过滤**：Graphify 过滤掉"机械性"的 God Node（如文件级的 `__init__.py` 汇总节点），只保留有业务意义的。

### Surprising Connections（意外连接）

跨越正常模块边界的连接，可能暗示设计问题：

```
意外连接:
  PaymentService → UserPreferences (跨社区, 跨文件类型)
  惊喜评分: 0.85

为什么"意外"？
  - 跨社区: 支付模块直接访问用户偏好
  - 跨文件类型: Service → Model 的直接依赖（应该通过 Repository）
```

**复合惊喜评分**由多个因素加权：
- 跨文件？跨类型？跨社区？跨仓库？
- 满足越多条件，惊喜分越高

---

## 8. MCP 查询接口

Graphify 通过 MCP Server 暴露 7 个查询工具：

| 工具 | 作用 | 示例查询 |
|------|------|---------|
| `query_graph` | 关键词搜索节点 | "找所有和 payment 相关的类" |
| `get_node` | 获取单个节点详情 | "UserService 的详细信息" |
| `get_neighbors` | 获取邻居节点 | "谁调用了 UserService？" |
| `get_community` | 获取整个社区 | "用户模块包含哪些组件？" |
| `god_nodes` | 列出核心节点 | "系统的关键依赖是什么？" |
| `graph_stats` | 图的统计信息 | "多少节点、边、社区？" |
| `shortest_path` | 最短路径 | "Logger 和 PaymentService 之间的调用链？" |

### Token 预算控制

查询结果会尊重 token 预算，通过字符截断控制输出长度：

```python
def format_result(nodes, max_chars=4000):
    result = ""
    for node in nodes:
        line = f"- {node['label']} ({node['type']}) in {node['file']}\n"
        if len(result) + len(line) > max_chars:
            result += f"... and {remaining} more nodes\n"
            break
        result += line
    return result
```

---

## 9. 缓存与增量处理

### SHA256 内容哈希缓存

处理过的文件用内容哈希作为缓存键：

```
文件: src/user.py
内容哈希: sha256("class User:...") = "a3f8c2..."
缓存位置: graphify-out/cache/a3f8c2.json

下次运行时:
  1. 计算文件哈希
  2. 缓存命中 → 直接用缓存结果
  3. 缓存未命中 → 重新 AST 解析
```

### Git 自动化

Graphify 可以安装 `post-commit` 钩子，每次提交后自动增量重建图谱：

```bash
# .git/hooks/post-commit
# graphify-hook
graphify build --incremental
```

只重新处理被修改的文件（通过 `git diff` 识别），不需要 LLM 调用。

---

## 10. 优缺点分析

### 优点

| 优点 | 说明 |
|------|------|
| 71.5x token 压缩 | 用图谱替代原始代码，极大节省上下文 |
| 确定性提取 | AST 解析不依赖 LLM，结果稳定可复现 |
| 置信度分级 | AI 知道哪些关系是确定的、哪些是推断的 |
| 增量处理 | SHA256 缓存 + Git 钩子，只处理变更 |
| 可解释性 | 图结构比 embedding 更透明 |
| 16 种语言 | 覆盖主流编程语言 |

### 缺点

| 缺点 | 说明 |
|------|------|
| 丢失代码细节 | 图谱只有结构信息，没有具体实现 |
| 动态语言限制 | 反射、动态调用无法准确提取 |
| 初始构建耗时 | 大型项目首次解析可能需要几分钟 |
| 不理解业务逻辑 | 知道 A 调用 B，但不知道为什么 |

---

## 11. 对我们项目的启发

### 11.1 文件依赖图

当前 CodeMergeSystem 的最大短板：**按文件独立分类（ABCDE），不知道文件间的依赖关系**。

```
当前问题:
  fileA.py (C-class, both-changed) → 先合并
  fileB.py (C-class, both-changed) → 后合并
  但实际上 fileA 继承了 fileB 的基类，应该 fileB 先合并！

引入依赖图后:
  graph: fileB --inherited_by--> fileA
  合并顺序: fileB → fileA（依赖树底部优先）
```

### 11.2 冲突波及范围分析

Conflict Analyst 目前只分析单个文件的冲突。有了图谱，可以回答：

```
"fileA 有冲突" → 查图谱 → fileB, fileC 都依赖 fileA
→ 需要检查 fileB, fileC 的合并结果是否受影响
```

### 11.3 置信度语义化

当前 `MemoryEntry.confidence` 是一个裸浮点数（0.0-1.0），没有语义。可以借鉴 Graphify 的分级：

```python
class ConfidenceLevel(str, Enum):
    EXTRACTED = "extracted"    # 从代码/diff 确定性提取
    INFERRED = "inferred"     # 基于 pattern 推断
    HEURISTIC = "heuristic"   # 启发式估计

# Agent 可以据此调整决策权重
if memory.confidence_level == ConfidenceLevel.EXTRACTED:
    trust_fully()
elif memory.confidence_level == ConfidenceLevel.HEURISTIC:
    verify_before_using()
```

### 11.4 跨文件 Import 解析

Graphify 的两阶段 import 解析思路可以直接复用：

```
阶段 1: 扫描所有文件，建立全局实体映射
  { "user_service.py": {"UserService": node_1, "get_user": node_2} }

阶段 2: 解析 import 语句，建立类级依赖
  "from user_service import UserService"
  → payment.py --uses--> UserService (不只是 payment.py --imports--> user_service.py)
```

这让依赖关系从"文件级"升级为"类/函数级"，合并分析更精准。
