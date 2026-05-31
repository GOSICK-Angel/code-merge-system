# Multi-Agent 通用合并优化建议：长期分叉合并的 6 大丢失模式与 10 条加固项

> **定位**：CodeMergeSystem 是一个**通用**的多 Agent 合代码系统，目标是为任意"长期分叉 fork ↔ upstream"场景提供可复用的合并流水线。
>
> **本文目的**：从多轮真实合并实践中归纳出 **6 类语言/仓库无关的丢失模式**，并给出 **10 条对 Planner / PlannerJudge / ConflictAnalyst / Executor / Judge / HumanInterface 六个 Agent 的通用加固建议**。
>
> **与项目实践关系**：文中 §10 附录以 Dify 项目的两次合并（upstream 1.13.0、1.13.3）作为 **案例实证**，用于展示每条建议对应的真实丢失案例。正文中的所有组件、门禁、配置均**不绑定任何具体仓库**；仓库专属信息一律放在附录与 YAML 配置中。
>
> **现状阅读范围**（证明建议有现状依托、非空谈）：
> - `src/tools/file_classifier.py`（ABCDE 分类）
> - `src/tools/pollution_auditor.py`（历史合并污染再分类）
> - `src/tools/sync_point_detector.py`（bulk-copy 迁移检测）
> - `src/tools/three_way_diff.py`（B/D-missing/C 的 VETO 与 TODO 检测）
> - `src/agents/judge_agent.py`（确定性流水线 + LLM 审查 + `verify_customizations`）
> - `src/models/config.py`（`CustomizationEntry`、`GateConfig`、`MigrationConfig`）
> - `pyproject.toml`（已声明 `tree-sitter` + python/js/ts/go/rust/java/c 共 7 种语言 parser —— 系统本就是多语言定位）
> - `doc/optimization-plan.md`（既有 P0–P3 规划）

---

## 1. 长期分叉合并的 6 大通用丢失模式

> 命名以"现象"为锚，适用于任意语言/框架。附录 §10 给出各模式的 Dify 实证。

| ID | 模式 | 典型表现 | 语言无关本质 |
|----|------|----------|--------------|
| **M1** | Fork 独有功能被 upstream 整文件覆盖 | 合并后运行时发现某个仅 fork 存在的配置/节点/常量消失 | 按文件粒度整体采纳 upstream，未扫描 fork 在该文件中的增量 |
| **M2** | 同名不同扩展名/包布局的 shadow 冲突 | upstream 新增 `foo.ts` 与 fork 已有 `foo.tsx` 并存；或 upstream 新增 `m/__init__.py` 与 fork 已有 `m.py` 并存。构建通过、运行时解析错文件 | 模块解析优先级差异，二进制 diff 查不出来 |
| **M3** | 接口签名/基类/枚举被 upstream 变更，fork 调用方未同步 | 构造函数新增必选参数 / 抽象方法改签名 / 枚举值重命名 → fork-only 文件运行时 TypeError | 缺"反向影响扫描"：只分析 upstream 自己调用方，不扫 fork-only 调用方 |
| **M4** | 顶层执行语句（注册/装饰器/模块初始化）整块被替换 | `register_route(...)` / `register_blueprint(...)` / `container.register(...)` 等顶层调用批量丢失 → 404/未注册 | 符号抽取只覆盖"定义"（`def/class`），不覆盖"调用表达式"和"顶层副作用" |
| **M5** | 配置/基础设施文件的 fork 行被 upstream 版本覆盖 | CI workflow、`.env.example`、Docker/K8s、构建脚本中 fork 专属 job/env var/参数被整段替换 | 文本合并不知道哪些行是"fork 必须保留"的 |
| **M6** | 类型/API 契约回归 | 类型标注、public API、导出列表被 upstream 重构 → 下游调用方编译报错或运行时契约不符 | Gate 只看 `exit 0` 不看 "本次新引入的失败项" |

**四个根因**：
- **根因 A（反例缺失）**：注册表只验"存在"，不验"数量下降/行保留率"。→ M1 / M4
- **根因 B（符号抽取窄）**：`_extract_symbols` 仅识别定义块，遗漏调用/装饰器/顶层副作用。→ M4
- **根因 C（无路径冲突检测）**：ABCDE 按路径哈希比对，对同名不同扩展/模块布局冲突视而不见。→ M2
- **根因 D（单向影响分析）**：只从 upstream 变更往外看，不反向扫 fork-only 的依赖面。→ M3 / M6

---

## 2. 现有能力 × 6 大模式 Gap 矩阵

| 模式 | 现有已覆盖 | 缺口（本文建议） |
|------|-----------|------------------|
| M1 | `CustomizationEntry` + Judge `verify_customizations`（grep / file_exists / function_exists） | ➜ **grep_count_baseline**、**line_retention**（P0-1）+ **ScarListBuilder** 自学习（P2-1） |
| M2 | 无 | ➜ 新增 **ShadowConflictDetector**（P0-2） |
| M3 | `security_sensitive` 模式 + Judge LLM 审查 | ➜ 新增 **InterfaceChangeExtractor + ReverseImpactScanner**（P1-1） |
| M4 | `three_way_diff.extract_upstream_additions` 基于 `def/class/function` | ➜ **TopLevelInvocationExtractor**（AST 级）（P0-3） |
| M5 | 无 | ➜ 新增 **ConfigLineRetentionChecker**（P2-3） |
| M6 | `syntax_checker` 仅语法 | ➜ **Gate baseline-diff**（按 failed_ids 集合增量判）+ 多语言 parser 插件（P1-2） |

横向补齐：**CrossLayerIntegrityChecker**（P0-4）、**SmokeTestAgent**（P1-3）、**SentinelScanner**（P2-2）。

---

## 3. 通用化设计原则（所有建议都必须满足）

| # | 原则 | 落地约束 |
|---|------|----------|
| 1 | **代码层零仓库知识** | `src/` 下不得出现任何项目专属词（用户项目名、域名、私有标识、业务术语） |
| 2 | **规则配置化** | 所有模式、哨兵、断言、门禁命令通过 YAML 注入；内置 `DEFAULT_*` 只包含跨项目通用项 |
| 3 | **多语言走 AST 而非正则** | 凡涉及符号/签名/调用抽取，优先用 tree-sitter（`pyproject.toml` 已有 py/js/ts/go/rust/java/c 依赖）；正则只作 fallback |
| 4 | **Baseline Parser 可插拔** | Gate 的 parser 列表通过 `entry_points` 注册，允许新项目为 `cargo test --format json` / `go test -json` / `surefire-reports` 等新增 parser，不改系统源码 |
| 5 | **案例证据与系统规范分离** | 任意项目的 restore 证据、`.merge/config.yaml` 样例，只能出现在 `doc/` 附录或 `examples/` 子目录，不得进入 `src/` 的默认值 |

以下第 4 节的 10 条建议，**每条都与原则 1–5 对齐**。

---

## 4. 十条通用加固建议（按优先级）

### P0-1. CustomizationRegistry 语义升级：从"存在性"到"保留率"

**问题**：`_verify_grep` 仅判 `total_matches == 0` → violation。但命中数从 N 降到 1（定制被大量删除）不会触发。

**改造**：
- `src/models/config.py` → `CustomizationVerification` 新增字段：
  ```python
  class CustomizationVerification(BaseModel):
      type: Literal[
          "grep", "grep_count_min", "grep_count_baseline",
          "file_exists", "function_exists",
          "line_retention"
      ] = "grep"
      pattern: str = ""
      files: list[str] = []
      min_count: int | None = None
      baseline_ref: str | None = None       # None 则自动取 merge_base
      retention_ratio: float | None = None  # e.g. 0.9 表示至少保留 90% 行
  ```
- `judge_agent._run_deterministic_pipeline` 新增 VETO：
  `customization_grep_below_baseline` / `customization_line_retention_below_ratio`

**通用性**：验证规则语言/框架无关；示例模式（如 Flask 的 `api.add_resource`）仅在用户项目 YAML 里写，不进默认值。

---

### P0-2. 新增 `ShadowConflictDetector`

**问题**：`foo.ts` + `foo.tsx`、`m.py` + `m/__init__.py`、`a.yaml` + `a.yml` 并存时解析优先级冲突。

**改造**：新增 `src/tools/shadow_conflict_detector.py`
```python
DEFAULT_SHADOW_RULES: list[ShadowRule] = [
    # Web / Node
    ShadowRule(exts_a={".ts"}, exts_b={".tsx"}),
    ShadowRule(exts_a={".js"}, exts_b={".jsx", ".mjs", ".cjs"}),
    # Python
    ShadowRule(module_vs_package=True),             # m.py vs m/__init__.py
    # JVM
    ShadowRule(exts_a={".java"}, exts_b={".kt"}),   # 同包同类名
    # Native
    ShadowRule(exts_a={".h"}, exts_b={".hpp"}),
    # Data
    ShadowRule(exts_a={".yaml"}, exts_b={".yml"}),
    ShadowRule(exts_a={".json"}, exts_b={".json5"}),
]
```
- 用户可通过 `config.shadow_rules_extra` 追加（如 `.toml` ↔ `.ini`）
- `MergeState` 新增 `shadow_conflicts: list[ShadowConflict]`
- Planner 遇到 shadow 自动升级为 `HUMAN_REQUIRED`，HumanInterface 提供三选一（重命名 / 合并 / 显式 re-export）

**触发**：Phase 1 分类后立即执行。

---

### P0-3. `TopLevelInvocationExtractor`（AST 级顶层副作用抽取）

**问题**：现 `_extract_symbols` 仅识别定义，`register_route(x)` / `@app.route(...)` / `init_app(app)` 这类顶层副作用在 diff 中消失也抓不到。

**改造**：
- **优先实现**：用 tree-sitter 取得 AST 后遍历：
  - Python：模块级 `call` 节点、`decorator` 节点
  - JavaScript/TypeScript：Program 级 `call_expression`、`decorator`
  - Java：类级 annotation、`static` 初始化块
  - Go：`init()` / 包级 `var x = Register(...)`
  - Rust：`#[...]` 属性、build.rs 顶层
- **Fallback**：当 tree-sitter parser 缺失时退化为正则：
  ```python
  DEFAULT_FALLBACK_PATTERNS = [
      r"^\s*\w+(?:\.\w+)*\s*\([^)]*\)\s*;?\s*$",   # 顶层调用
      r"^\s*@\w+(?:\.\w+)*\s*\(",                   # 装饰器/annotation
  ]
  ```
- 新增 `JudgeIssue.issue_type = "top_level_invocation_lost"`，VETO

**通用性**：默认无任何具体框架模式；用户通过 `config.customizations[*].verification.type = grep_count_baseline` 指定关心的调用表达式。

---

### P0-4. `CrossLayerIntegrityChecker`

**问题**：一个功能横跨"枚举 → 注册表 → Schema → 控制器 → 前端类型 → 前端注册 → 页面 → 前端服务"多层，每层 diff 都合理但少一层就整体不可用。

**改造**：纯配置驱动
```yaml
cross_layer_assertions:
  - name: "Enum → ComponentMap"
    keys_from: "<source_file>::<regex capturing keys>"
    keys_in:
      - "<registry_file_1>"
      - "<registry_file_2>"
    allow_missing: ["KeyA", "KeyB"]
```
- 每个 assertion 的 keys_from 支持 `file::regex` 或 `file::ast_query`
- PlannerJudge 审计时把同一个 assertion 涉及的所有文件强制打包到同一个 Phase
- Judge 在 `_run_deterministic_pipeline` 里检测未豁免键缺失 → VETO

**通用性**：无任何硬编码模式。常见用例目录（枚举→注册表 / Schema→序列化 / Route→Controller / API→前端调用 / Model→Migration）作文档示例，不进代码默认值。

---

### P1-1. `InterfaceChangeExtractor` + 反向影响扫描

**问题**：upstream 改动了 `__init__` 签名/基类/抽象方法/枚举值/store shape，fork-only 调用方未同步。

**改造**：
- `src/tools/interface_change_extractor.py`，基于 tree-sitter AST diff：
  ```python
  class InterfaceChange(BaseModel):
      file_path: str
      symbol: str
      change_kind: Literal[
          "constructor_signature", "method_signature",
          "base_class", "enum_value", "export_removed",
          "module_path_moved", "type_narrowed"
      ]
      before: str
      after: str
  ```
- `ReverseImpactScanner`：对每个 `InterfaceChange.symbol`，在 **D-extra ∪ CustomizationEntry.files** 范围内 grep → `ForkImpactedFile`
- **新增 Phase 0.5 ReverseImpact**：在 Planner 前运行，产物作为 Planner prior
- ConflictAnalyst prompt 注入 "upstream changed `X` from `A` to `B`, verify whether fork call site at `Y` still valid"

**通用性**：AST 语言无关；只对"fork 独有目录"做反向 grep，不预设任何具体目录名。

---

### P1-2. Gate 系统深度化：baseline-diff + 多语言 parser 插件

**问题**：`pass_criteria: not_worse_than_baseline` 只比数字，不比 failed_ids 集合。新引入但总数不增的失败项被放行。

**改造**：
- `GateBaseline.baseline_value` 结构化为 JSON：
  ```json
  {"passed": 0, "failed": 0, "failed_ids": ["<test_id_1>", "<test_id_2>"]}
  ```
- `src/tools/gate_runner.py` 新增 `BaselineParser` 注册表（entry_points），内置 8 种 parser：
  | Parser | 覆盖 |
  |--------|------|
  | `pytest_summary` | Python pytest `--tb=no` 文本 |
  | `mypy_json` / `basedpyright_json` | Python 类型检查 |
  | `ruff_json` / `eslint_json` | Python / JS lint |
  | `tsc_errors` | TypeScript |
  | `go_test_json` | Go `go test -json` |
  | `cargo_test_json` | Rust `cargo test --format json` |
  | `junit_xml` | Java/Kotlin Surefire / JUnit 通用 |
- 判决规则：`failed_ids(HEAD) \ failed_ids(baseline) ≠ ∅` → gate FAIL；即使总数不增，只要引入新失败项也 FAIL

**通用性**：parser 走 entry_points，新语言/测试框架零源码改动可接入。

---

### P1-3. 新增 `SmokeTestAgent`（轻量 Executor 变体）

**问题**：单元测试过 ≠ 功能跑通。合并后常在用户关键路径上发现回归。

**改造**：
- `src/agents/smoke_test_agent.py`：
  - 输入：`SmokeTestSuite` YAML（shell / http / playwright 三类 kind）
  - 输出：`SmokeTestReport`，每条独立判 PASS/FAIL，失败附 stderr 片段
  - 写权限：仅 `runs/<id>/smoke/`
- 新 Phase 5.5，夹在 Judge PASS 与 Report 之间：
  ```
  Phase 5 Judge PASS → Phase 5.5 SmokeTest → Phase 6 Report
                            ↓ fail
                       回到 Phase 3 定点修复
  ```
- 配置 schema（通用示意）：
  ```yaml
  smoke_tests:
    suites:
      - name: <name>
        kind: shell | http | playwright
        cases:
          - id: <case_id>
            cmd: "<arbitrary shell>"     # kind=shell
            url: "<http(s) URL>"         # kind=http
            method: GET | POST | ...
            expect_status: 200
            tag: "<playwright tag>"      # kind=playwright
  ```

**通用性**：无任何具体 curl/URL/测试名；全部由用户项目 YAML 定义。

---

### P2-1. `ScarListBuilder` —— 从历史 `restore`/`revert`/`compat-fix` 提交自学习

**问题**：每次合并踩到的新坑不自动沉淀，下次仍可能重现。

**改造**：
- `src/tools/scar_list_builder.py`：
  ```python
  class Scar(BaseModel):
      commit_sha: str
      commit_subject: str
      files: list[str]
      pattern_kind: Literal["restore", "fix_compat", "revert"]

  class ScarListBuilder:
      def build(self, repo_path: Path, since: str = "1 year ago",
                grep_patterns: list[str] | None = None) -> list[Scar]: ...
      def materialize_as_customizations(
          self, scars: list[Scar], existing: list[CustomizationEntry]
      ) -> list[CustomizationEntry]: ...
  ```
- `grep_patterns` 默认值 = `["restore", "fix.*compat", "revert"]`（语义无关）；用户可扩展（例如团队内部用 "hotfix/haq" 前缀）
- `CustomizationEntry` 新增：`source: Literal["manual", "scar_learned"]`、`confidence: float`
- Judge PASS 后回写：本轮产生的 restore 提交 → 自动追加 `scar_learned` 条目

**通用性**：不假设任何提交 message 风格；grep patterns 用户可覆盖。

---

### P2-2. Executor `PlanDispute` 触发器扩展：哨兵命中必须质疑

**问题**：Executor 打开 `AUTO_SAFE` 文件时如果里面已有 fork 定制标注，LLM 可能仍按 `AUTO_SAFE` 处理。

**改造**：
- `src/tools/sentinel_scanner.py`：
  ```python
  # 默认只保留跨项目通用的"标注类哨兵"
  DEFAULT_SENTINELS = [
      r"#\s*Current branch enhancement",
      r"#\s*TODO\s*\[merge\]",
      r"#\s*Merged from upstream",
      r"<<<<<<<", r"=======", r">>>>>>>",   # 未解决冲突
      r"@fork-only", r"@do-not-remove",     # 跨语言常见的标注
  ]
  ```
- **关键**：`DEFAULT_SENTINELS` 不含任何项目专属词；用户项目专属哨兵（业务术语、身份系统名、组件名等）通过 `config.sentinels_extra` 注入
- Executor 打开 `AUTO_SAFE` 文件时先 scan；命中任一 → 自动发起 `PlanDisputeRequest(suggested_reclassification=AUTO_RISKY 或 HUMAN_REQUIRED)`
- 降级不可 bypass：只有 Planner 修订后的 `PlannerJudgeVerdict(APPROVED)` 才能恢复

**通用性**：代码零仓库知识；全部业务语义由 YAML 注入。

---

### P2-3. `ConfigLineRetentionChecker` —— CI / env / docker 行级保留

**问题**：`.github/workflows/*.yml` 中 fork 专属 job、`docker/.env.example` 中 fork 新增 env var、`Dockerfile` 中 fork 专属 build step 被 upstream 整段覆盖。

**改造**：
- `src/tools/config_line_retention_checker.py`：
  ```python
  class ConfigRetentionRule(BaseModel):
      file_glob: str
      required_lines: list[str]    # 正则列表
      min_line_count: int = 1
  ```
- 配置示例：
  ```yaml
  config_retention:
    rules:
      - file_glob: ".github/workflows/*.yml"
        required_lines:
          - '^\s*<fork-specific-job-name>:'
      - file_glob: "docker/.env.example"
        required_lines:
          - '^<FORK_ENV_VAR>='
      - file_glob: "Dockerfile"
        required_lines:
          - 'RUN <fork-specific-step>'
  ```
- 挂到 Phase 4.5（基础设施 gate）后，violation 直接阻断进入后续 Phase

**通用性**：规则完全由用户配置；无任何默认模式。

---

## 5. Agent 级职责增量

| Agent | 新增职责 | 调用的新工具 |
|-------|----------|--------------|
| **Planner** | ① 加载 ScarList 作为 prior；② 基于 ShadowConflict 强制提升风险；③ 基于 ReverseImpact 提升风险 | `ScarListBuilder`、`ShadowConflictDetector`、`ReverseImpactScanner` |
| **PlannerJudge** | ① 审计跨层 assertion 是否分散在多个 Phase；② 对 Shadow/哨兵命中但未 HUMAN_REQUIRED 的文件出 REVISION_NEEDED | 读 `state.shadow_conflicts`、`state.sentinel_hits` |
| **ConflictAnalyst** | Prompt 注入 `InterfaceChange` 上下文（被引用符号的 before/after 签名） | `InterfaceChangeExtractor` |
| **Executor** | 打开 AUTO_SAFE 文件时先跑 `SentinelScanner`；命中即 PlanDispute | `SentinelScanner` |
| **Judge** | 确定性流水线扩展：顶层执行块保留、grep_count_baseline、line_retention、CrossLayer assertion | `three_way_diff` AST 扩展、`CrossLayerChecker`、`ConfigLineRetentionChecker` |
| **HumanInterface** | 新交互模板：Shadow 冲突解析（重命名/re-export/合并）、InterfaceChange 审阅 | — |
| **新：SmokeTestAgent** | Judge PASS 后运行冒烟 | `GateRunner` 扩展版 |

---

## 6. 模型/配置扩展速查

```python
# src/models/diff.py
class FileChangeCategory(str, Enum):
    A = "unchanged"
    B = "upstream_only"
    C = "both_changed"
    D_MISSING = "d_missing"
    D_EXTRA   = "d_extra"
    E = "fork_only"
    S = "shadow_conflict"        # 新增

# src/models/state.py（增量字段）
class MergeState(BaseModel):
    ...
    shadow_conflicts: list[ShadowConflict] = []
    interface_changes: list[InterfaceChange] = []
    reverse_impacts: dict[str, list[str]] = {}   # symbol -> fork-only files
    sentinel_hits: dict[str, list[SentinelHit]] = {}
    scar_list: list[Scar] = []
    smoke_test_report: SmokeTestReport | None = None

# src/models/judge.py（扩展 issue_type 枚举）
ISSUE_TYPES_NEW = {
    "top_level_invocation_lost",
    "customization_grep_below_baseline",
    "customization_line_retention_below_ratio",
    "shadow_conflict_unresolved",
    "cross_layer_assertion_missing",
    "reverse_impact_unhandled",
    "config_retention_violation",
    "smoke_test_failed",
}
```

---

## 7. Phase 流水调整（通用流程图）

```
(已有) Phase -1: PollutionAudit + SyncPoint
(新增) Phase  0:   ScarListBuilder
(新增) Phase  0.3: ShadowConflictDetector
(新增) Phase  0.5: InterfaceChange + ReverseImpact
(已有) Phase  1:   Planner                  [加载上述先验]
(已有) Phase  1.5: PlannerJudge
(扩展) Phase  2:   AutoMerge (Executor + SentinelScanner)
(已有) Phase  3:   ConflictAnalysis + Merge
(已有) Phase  4:   HumanInterface
(新增) Phase  4.5: ConfigLineRetentionChecker
(扩展) Phase  5:   Judge (扩展 VETO 规则)
(新增) Phase  5.5: SmokeTestAgent
(已有) Phase  6:   Report
```

---

## 8. 落地路线图

### Sprint 1（P0，2 周）

- [ ] `three_way_diff` 扩展 `extract_top_level_invocations`（tree-sitter AST）+ Judge `top_level_invocation_lost` VETO
- [ ] `CustomizationVerification` 扩展 `grep_count_baseline` / `line_retention`
- [ ] `ShadowConflictDetector` + Planner 集成
- [ ] `CrossLayerChecker` + YAML schema

### Sprint 2（P1，3–4 周）

- [ ] `InterfaceChangeExtractor`（基于 tree-sitter）+ `ReverseImpactScanner` + Phase 0.5
- [ ] `GateRunner` baseline-diff 模式；内置 8 种 parser 注册 + entry_points 扩展点
- [ ] `SmokeTestAgent` + Phase 5.5

### Sprint 3（P2，持续）

- [ ] `ScarListBuilder` + Judge PASS 后自动回写注册表
- [ ] `SentinelScanner` + Executor 强制 PlanDispute
- [ ] `ConfigLineRetentionChecker` + Phase 4.5

### 质量门

- [ ] 对每个新 tool 补 `tests/unit/` 覆盖 ≥ 80%
- [ ] 在 `tests/integration/` 用**多项目**真实 restore commits 构造回归用例集（至少覆盖 Python/TypeScript/Go 三种语言），确保每个历史 bug 都能被对应的新门拦住

---

## 9. 与 `doc/optimization-plan.md` 的关系

`optimization-plan.md` 已规划 ABCDE 分类（P0）、分层合并（P0）、定制注册表雏形（P1）、Judge 修复循环（P1）、Gate 骨架（P1）、污染审计（P3）。本文是在其之上的**Sprint 级加固补丁**：

| 本文建议 | 对应 optimization-plan 条目 | 增量 |
|----------|-------------------------------|------|
| P0-1 grep_count_baseline / line_retention | §4 Customization Protection Registry | 从"存在/不存在"升级为"数量/比例不劣化" |
| P0-2 ShadowConflictDetector | 未覆盖 | 全新 |
| P0-3 TopLevelInvocationExtractor | §8 Three-Way Diff in Judge | 从符号定义扩展到顶层副作用 + AST 化 |
| P0-4 CrossLayerChecker | 未覆盖 | 全新 |
| P1-1 InterfaceChangeExtractor | 未覆盖 | 全新 |
| P1-2 Gate baseline-diff + 多 parser | §6 Gate System | 从 exit_zero 升级为 no-new-regression + 多语言插件 |
| P1-3 SmokeTestAgent | 未覆盖 | 全新 |
| P2-1 ScarListBuilder | §10 Pollution Audit | 污染审计看"前合过"，Scar 看"被人工 restore 过"——互补 |
| P2-2 SentinelScanner | 未覆盖 | 全新 |
| P2-3 ConfigLineRetentionChecker | 未覆盖 | 全新 |

---

## 10. 附录 A：案例实证 — Dify 1.13.x 两轮合并

> 以下内容用 Dify 项目在 `integration/merge-1.13.3` 分支两轮合并中产生的 **20+ 个 `fix(...): restore ...` 类提交** 作为第 1 节 6 大模式的实证。仅作证据参考，不构成系统默认行为。

### A.1 证据清单（`git log --grep="restore\|revert.*compat\|fix.*compat"`）

| 模式 | 典型提交 | 具体丢失内容 |
|------|----------|--------------|
| M1 | `fix(api): restore sys.skill_id system variable for chatflow skill tags` | fork 新增的系统变量整类丢失 |
| M1 | `fix: restore SandboxExpiredRecordsCleanConfig, remove debug logs` | fork 新增配置类丢失 |
| M1 | `fix(chat): restore user memory feature broken by context.ts/context.tsx shadowing` | fork 独有功能被 shadow |
| M2 | 同上 `context.ts` / `context.tsx` 案例 | `.ts` vs `.tsx` 双存导致导入解析错误 |
| M3 | memory 记录：upstream 1.13.0 在 `login_required` 引入 `check_csrf_token(request, current_user.id)`，fork 的 Authorization header 认证模式不被识别 | 签名变更 + 反向影响未扫描 |
| M4 | fork 分支 `controllers/console/workspace/workspace.py` 合并后 `api.add_resource(...)` 所有路由注册丢失；现象 `OPTIONS 200 + GET 404` 死循环 | 顶层调用表达式未被保留 |
| M5 | `fix(ci): restore i18n bridge workflow generation` / `fix(docker): restore S3_ADDRESS_STYLE env examples` | CI/env 级行被整段替换 |
| M6 | `fix(api): restore typedict py311 compatibility` / `fix(api): restore workflow node compatibility` / `fix(api): restore advanced chat refresh_model contract` | 类型/契约回归 |

### A.2 Dify 项目下次合并（例 upstream 1.14.0）的 `.merge/config.yaml` 参考

> 此配置**仅供 Dify 使用**，不属于系统默认值。

```yaml
upstream_ref: upstream/1.14.0
fork_ref: integration/merge-1.14.0
repo_path: /Users/angel/Desktop/WA_AI/project/dify
project_context: |
  Dify CVTE fork. See md/merge-doc/UPSTREAM_MERGE_SPECIFICATION.md and
  .claude/rules/dify-cvte-merge-compat.md for mandatory customizations.

migration:
  auto_detect_sync_point: true

# 本项目专属的哨兵词（系统 DEFAULT_SENTINELS 不含这些）
sentinels_extra:
  - '\bCVTE\b'
  - 'portal_sso'
  - 'keycloak'
  - 'skill_id'
  - 'GeventWebSocketWorker'
  - 'PendingCounter'

customizations:
  - name: "Authorization header CSRF bypass"
    files: ["api/libs/token.py"]
    verification:
      - type: grep
        pattern: '_try_extract_from_header'
      - type: grep_count_min
        pattern: 'Authorization'
        min_count: 3

  - name: "Workspace routes (top-level api.add_resource)"
    files: ["api/controllers/console/workspace/workspace.py"]
    verification:
      - type: grep_count_baseline
        pattern: '^\s*api\.add_resource\('

  - name: "GeventWebSocketWorker"
    files: ["docker/entrypoint.sh", "api/app.py"]
    verification:
      - type: grep
        pattern: 'GeventWebSocketWorker'

  - name: "ext_socketio + ext_prometheus"
    files: ["api/app_factory.py"]
    verification:
      - type: grep
        pattern: 'init_app\s*\(\s*app\s*,\s*socketio'
      - type: grep
        pattern: 'init_app\s*\(\s*app\s*,\s*prometheus'

  - name: "TTS race fix"
    files: ["api/core/base/tts/app_generator_tts_publisher.py"]
    verification:
      - type: function_exists
        pattern: 'wait_until_finished'
      - type: grep
        pattern: 'PendingCounter'

  - name: "sys.skill_id variable"
    files: ["api/core/workflow/**/*.py"]
    verification:
      - type: grep
        pattern: 'skill_id'

cross_layer_assertions:
  - name: "BlockEnum → NodeComponentMap"
    keys_from: "web/app/components/workflow/types.ts::^\\s+(\\w+)\\s*=\\s*'"
    keys_in:
      - "web/app/components/workflow/nodes/components.ts"
      - "web/app/components/workflow/constants.ts"
    allow_missing: ["IterationStart", "LoopStart", "LoopEnd"]

config_retention:
  rules:
    - file_glob: ".github/workflows/translate-i18n-*.yml"
      required_lines:
        - 'dispatch_i18n_bridge'
    - file_glob: "docker/.env.example"
      required_lines:
        - '^S3_ADDRESS_STYLE='

gate:
  commands:
    - name: api-ruff
      command: "cd api && uv run ruff check ."
      pass_criteria: exit_zero
    - name: api-pytest
      command: "cd api && uv run pytest tests/unit_tests/ --tb=no -q"
      pass_criteria: not_worse_than_baseline
      baseline_parser: pytest_summary
    - name: web-type-check
      command: "cd web && pnpm type-check"
      pass_criteria: no_new_errors
      baseline_parser: tsc_errors

smoke_tests:
  suites:
    - name: backend-regression
      kind: shell
      cases:
        - id: S2-workspace
          cmd: "cd api && uv run pytest tests/cvte_regression/test_workspace_routes.py"
        - id: S6-confidence
          cmd: "cd api && uv run pytest tests/cvte_regression/test_confidence_level_node.py"
        - id: S7-celery-queues
          cmd: "cd api && uv run pytest tests/cvte_regression/test_celery_worker_queues.py"
    - name: frontend-e2e
      kind: playwright
      tag: "@merge-smoke"

scar_learning:
  enabled: true
  since: "1 year ago"
  auto_append_to_customizations: true
```

### A.3 其他项目可参考的 YAML 骨架

对于**其他任意 fork ↔ upstream 项目**，只需复制 §A.2 的骨架，把：
- `sentinels_extra` → 替换为本项目业务/身份相关的标识词
- `customizations` → 替换为本项目 fork-only 功能对应的 grep 模式
- `cross_layer_assertions` → 按本项目注册表结构填写
- `config_retention.rules` → 按本项目 CI/env/docker 定制填写
- `gate.commands` → 按本项目构建工具链填写（Go 项目用 `go_test_json`，Rust 项目用 `cargo_test_json`，Java 项目用 `junit_xml`）
- `smoke_tests.suites` → 按本项目关键用户路径填写

系统代码层 `src/` 对以上所有字段 **零感知**。

---

**一句话总结（给 Orchestrator）**

> 合并事故 99% 来自"看起来没变的东西被悄悄吞了"。
> 本轮 6 个新 VETO（top_level_invocation、grep_count_baseline、line_retention、shadow_conflict、cross_layer、reverse_impact）+ 1 个 SmokeTest Agent + 1 个 Scar 自学习流水线，把这个盲区从 Judge 阶段向前移到 Planner 之前，且全程与具体仓库解耦。
