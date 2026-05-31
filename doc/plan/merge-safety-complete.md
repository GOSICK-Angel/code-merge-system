# 二次开发合并安全 — 完整落地方案

> 本文是 `upstream-sync-automation.md`、`pre-dev-assessment.md`、`coding-agent-integration.md`
> 三份设计文档的合并版，覆盖从"开发者开始写第一行代码"到"PR 合入主线"全程的合并安全机制。

---

## 1. 问题陈述

当前系统的介入点在 **fork 已有 diff 之后**，而影响合并难度最大的决策（哪些文件做 inline 改动、
是否 seam 化、是否有 flag-off 保护）发生在**设计阶段**。

三个合规漏洞：

| 漏洞 | 现状 | 解决方案 |
|---|---|---|
| 设计决策无记录 | 没有标准产物，merge 运行不知道"这段 inline 改动是有意为之的 seam" | `dev-profile.yaml` + `merge assess` |
| 上游接缝悄悄断裂 | upstream 改了依赖符号，系统无法主动检测，直到运行时崩溃 | `seam_anchors` + Judge 确定性检查 |
| 评估靠人自觉 | `merge assess <doc>` 很容易被跳过 | Skill（嵌入）+ Hook（提醒）+ CI（强制） |

---

## 2. 总体架构

```
┌─────────────────────────────────────────────────────────────────┐
│  设计阶段（代码之前）                                              │
│                                                                 │
│  /assess Skill ─── merge assess ──► dev-profile.yaml           │
│  (F.1)            (E.1–E.3)         (E.4)                      │
│     │                                    │                      │
│     └─ 无设计文档: 从 git state 推断       └─ git commit（版本化） │
└──────────────────────────────────┬──────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────┐
│  开发阶段                                                         │
│                                                                 │
│  PostToolUse Hook ─── 编辑高 churn 文件时静默提醒（不阻断）         │
│  (F.2)                                                          │
└──────────────────────────────────┬──────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────┐
│  合并阶段（merge / merge sync）                                    │
│                                                                 │
│  Initialize Phase                                               │
│    _load_dev_profile()    ─── seam_anchors 注入 config (E.5)    │
│    _check_seam_anchors()  ─── 验证接缝 + on_break=HR→提升 (A.3) │
│    _detect_platform_risk()─── 扫描上游迁移信号 (C，config 驱动) │
│                                                                 │
│  Planner                                                        │
│    sync_mode=upstream ─── P-SYNC-001 Gate：优先跟 upstream (B)  │
│                                                                 │
│  Judge（只读，返回 JudgeIssue）                                  │
│    _check_seam_anchors()  ─── on_break=veto → VETO issue (A.4)  │
└──────────────────────────────────┬──────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────┐
│  PR 阶段（强制门控）                                               │
│                                                                 │
│  CI merge-readiness 作业                                         │
│    merge assess --no-llm ─── 自动评估                            │
│    risk=critical + !acknowledged ─── 阻断 PR (F.3)             │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. 组件清单与依赖

| ID | 组件 | 新建文件 / 改动文件 | 估时 | 依赖 |
|---|---|---|---|---|
| **A.1** | SeamAnchor / SeamAnchorBreak 模型 | `config.py` / `state.py` | 0.5d | — |
| **E.4** | DevProfile / FileChurnEntry 等模型 | `design_review.py`（新） | 0.5d | A.1 |
| **E.2** | DesignFileExtractor + measure_churn | `design_file_extractor.py`（新） | 0.5d | — |
| **E.1** | `merge assess` CLI 命令 | `cli/main.py` + `cli/commands/assess.py`（新） | 0.5d | E.2、E.4 |
| **E.3** | DesignReviewAgent + Gate DR-001 | `design_reviewer.py`（新）+ 合约 yaml | 1.5d | E.4 |
| **A.3** | Initialize: `_check_seam_anchors()` + `_extract_symbols`（需新写符号/参数解析器，非复用 InterfaceChangeExtractor） | `initialize.py` | 1d | A.1 |
| **E.5** | Initialize: `_load_dev_profile()` | `initialize.py` | 0.5d | E.4、A.3 |
| **C** | Initialize: `_detect_platform_risk()` | `initialize.py` / `state.py` / report | 0.5d | — |
| **A.4** | Judge: `_check_seam_anchors()` | `judge_agent.py` / `judge.yaml` | 0.5d | A.1、A.3 |
| **B** | sync_mode + Gate P-SYNC-001 | `config.py` / `planner.yaml` / `gate_registry.py` | 1d | A.1 |
| **D** | `merge sync` CLI 命令 | `cli/main.py` | 0.5d | B |
| **F.1** | `/assess` Skill（Claude Code） | `.claude/skills/assess/SKILL.md`（新） | 0d | — |
| **F.2** | PostToolUse Hook | `.claude/hooks/seam_zone_reminder.py`（新） | 0.25d | — |
| **F.3** | CI merge-readiness 作业 | `.github/workflows/merge-readiness.yml`（新） | 0.25d | — |

**总估时**：~7.5 天（含单测；A.3 因需新写符号解析器较原估上调 0.5d）

---

## 4. 推荐 PR 拆分

```
PR-1  模型层（A.1 + E.4）          ← 零侵入，先合入
PR-2  工具层（E.2 + merge assess）   ← 依赖 PR-1，E.1 + E.3 可并行开发
PR-3  Initialize 集成（A.3 + E.5 + C）← 依赖 PR-1
PR-4  DesignReviewAgent（E.3）       ← 依赖 PR-1，工作量最大
PR-5  Judge 检查（A.4）              ← 依赖 PR-1 + PR-3
PR-6  sync_mode + merge sync（B + D）← 依赖 PR-1 + PR-3
PR-7  编码代理集成（F.1 + F.2 + F.3）← 纯配置文件，随时可合入
```

---

## 5. 详细设计

### 5.1 数据模型（PR-1）

#### `src/models/config.py` — 追加 SeamAnchor

```python
from typing import Literal
from pydantic import BaseModel

class SeamAnchor(BaseModel):
    file: str
    symbol: str
    required_params: list[str] = []
    on_break: Literal["veto", "warn", "human_required"] = "warn"

class MergeConfig(BaseModel):
    ...
    seam_anchors: list[SeamAnchor] = []
    sync_mode: Literal["feature", "upstream"] = "feature"
    # 平台风险信号 pattern 不得硬编码进 src/（Project Generality）；默认空，由 config 提供
    platform_risk_patterns: list["PlatformRiskPattern"] = []

class PlatformRiskPattern(BaseModel):
    pattern: str                                          # 正则
    severity: Literal["warning", "critical"] = "warning"
```

#### `src/models/config.py` — `AgentsLLMConfig` 追加 design_reviewer

`merge assess` 走独立 CLI 路径、不经 Orchestrator，`DesignReviewAgent` 仍需 `AgentLLMConfig`。
在 `AgentsLLMConfig` 增加一个条目（与 judge/planner 同风格），供 assess 命令取用：

```python
class AgentsLLMConfig(BaseModel):
    ...
    design_reviewer: AgentLLMConfig = Field(
        default_factory=lambda: AgentLLMConfig(
            provider="anthropic", model="claude-opus-4-6", api_key_env="ANTHROPIC_API_KEY"
        )
    )
```

#### `src/models/state.py` — 追加 SeamAnchorBreak / PlatformRiskSignal

```python
class SeamAnchorBreak(BaseModel):
    anchor: SeamAnchor
    break_kind: Literal["symbol_removed", "symbol_renamed", "param_removed", "file_removed"]
    detail: str

class PlatformRiskSignal(BaseModel):
    pattern: str
    matched_commits: list[str]
    severity: Literal["warning", "critical"]

class MergeState(BaseModel):
    ...
    seam_anchor_breaks: list[SeamAnchorBreak] = []
    platform_risk_signals: list[PlatformRiskSignal] = []
    dev_profile: "DevProfile | None" = None
```

#### `src/models/design_review.py`（新文件）

> **跨模块前向引用**：`DevProfile.seam_anchors` 引用 `config.py` 的 `SeamAnchor`，
> `state.py` 的 `MergeState.dev_profile` 反向引用本文件的 `DevProfile`。pydantic v2 需
> 显式 import + 在模块末尾 `DevProfile.model_rebuild()`（state.py 同理对 MergeState），
> 否则 `model_validate` 抛 unresolved-ref。

```python
from typing import Literal
from pydantic import BaseModel
from src.models.config import SeamAnchor  # 用于 DevProfile.seam_anchors 嵌套校验

class FileChurnEntry(BaseModel):
    path: str
    commit_count_90d: int
    churn_risk: Literal["low", "medium", "high", "critical"]
    is_new_file: bool
    is_inline_edit: bool

class DesignIssue(BaseModel):
    severity: Literal["info", "warning", "error"]
    file: str | None = None
    message: str
    suggestion: str

class SeamRecommendation(BaseModel):
    file: str
    symbol: str
    reason: str
    suggested_on_break: Literal["veto", "warn", "human_required"]
    required_params: list[str] = []

class DesignMergeAssessment(BaseModel):
    overall_risk: Literal["low", "medium", "high", "critical"]
    merge_friendly_score: float          # 0.0–1.0
    positive_patterns: list[str]
    design_issues: list[DesignIssue]
    seam_recommendations: list[SeamRecommendation]
    platform_risk_note: str | None = None
    summary: str

class HighRiskFileEntry(BaseModel):
    path: str
    churn_risk: Literal["low", "medium", "high", "critical"]
    commit_count_90d: int
    design_approach: str = ""

class DevProfile(BaseModel):
    version: int = 1
    assessed_at: str
    design_doc: str
    design_doc_sha256: str
    upstream_ref: str
    overall_risk: Literal["low", "medium", "high", "critical"]
    merge_friendly_score: float
    seam_anchors: list[SeamAnchor] = []
    high_risk_files: list[HighRiskFileEntry] = []
    positive_patterns: list[str] = []
    design_issues: list[DesignIssue] = []
    risk_acknowledged: bool = False      # CI gate：critical 风险时需人工置为 true

DevProfile.model_rebuild()
```

> **MergeConfig 不可变更**：运行期注入 seam_anchors 时禁止 `config.seam_anchors.append(...)`。
> 现有约定（`initialize.py:373`）是 `state.config = state.config.model_copy(update={...})`。
> 见 §5.5 `_load_dev_profile`。

---

### 5.2 工具层：DesignFileExtractor + Churn 分析（PR-2）

#### `src/tools/design_file_extractor.py`（新文件）

```python
import re
from pathlib import Path
from typing import Literal
from pydantic import BaseModel

class FileExtractResult(BaseModel):
    path: str
    source: Literal["code_block", "inline_code", "file_list"]
    is_new_file: bool
    is_inline_edit: bool

class DesignFileExtractor:
    _KNOWN_EXTS = {".ts", ".tsx", ".py", ".go", ".js", ".jsx", ".yaml", ".yml",
                   ".json", ".sql", ".md", ".toml", ".rs"}

    def extract(self, doc_content: str, repo_root: Path) -> list[FileExtractResult]:
        raw: list[tuple[str, str]] = []
        raw.extend(self._from_code_blocks(doc_content))
        raw.extend(self._from_inline_code(doc_content))
        raw.extend(self._from_file_list_section(doc_content))
        return self._deduplicate_and_classify(raw, repo_root)

    def _from_code_blocks(self, content: str) -> list[tuple[str, str]]:
        results = []
        for block in re.findall(r"```[^\n]*\n(.*?)```", content, re.DOTALL):
            for line in block.splitlines():
                line = line.strip().lstrip("+-").strip()
                if self._looks_like_path(line):
                    results.append((line, "code_block"))
        return results

    def _from_inline_code(self, content: str) -> list[tuple[str, str]]:
        results = []
        for match in re.findall(r"`([^`]+)`", content):
            if self._looks_like_path(match):
                results.append((match, "inline_code"))
        return results

    def _from_file_list_section(self, content: str) -> list[tuple[str, str]]:
        results = []
        in_file_section = False
        for line in content.splitlines():
            if re.search(r"##+\s*(文件清单|file list|changed files)", line, re.I):
                in_file_section = True
                continue
            if in_file_section and line.startswith("#"):
                in_file_section = False
            if in_file_section:
                path = line.strip().lstrip("+-•").strip().split()[0] if line.strip() else ""
                if self._looks_like_path(path):
                    results.append((path, "file_list"))
        return results

    def _looks_like_path(self, s: str) -> bool:
        return (
            "/" in s
            and any(s.endswith(ext) for ext in self._KNOWN_EXTS)
            and not s.startswith("http")
            and len(s) < 200
        )

    def _deduplicate_and_classify(
        self, raw: list[tuple[str, str]], repo_root: Path
    ) -> list[FileExtractResult]:
        seen: dict[str, FileExtractResult] = {}
        for path, source in raw:
            if path in seen:
                continue
            exists = (repo_root / path).exists()
            seen[path] = FileExtractResult(
                path=path,
                source=source,
                is_new_file=not exists,
                is_inline_edit=exists,
            )
        return list(seen.values())
```

#### Churn 分析（可放在同文件或 `src/tools/churn_analyzer.py`）

```python
from typing import Literal
from src.tools.git_tool import GitTool

CHURN_THRESHOLDS = {"critical": 20, "high": 10, "medium": 3}

def classify_churn(count: int) -> Literal["low", "medium", "high", "critical"]:
    if count >= CHURN_THRESHOLDS["critical"]: return "critical"
    if count >= CHURN_THRESHOLDS["high"]:     return "high"
    if count >= CHURN_THRESHOLDS["medium"]:   return "medium"
    return "low"

def measure_churn(git_tool: GitTool, file_path: str,
                  upstream_ref: str, days: int = 90) -> int:
    try:
        log = git_tool.repo.git.log(
            "--oneline", f"--since={days} days ago",
            upstream_ref, "--", file_path
        )
        return len([l for l in log.splitlines() if l.strip()])
    except Exception:
        return 0
```

---

### 5.3 `merge assess` CLI 命令（PR-2）

#### `src/cli/main.py` — 新增子命令

```python
@cli.command("assess")
@click.argument("design_doc", required=False, type=click.Path())
@click.option("--repo-path",      default=".", type=click.Path(exists=True))
@click.option("--upstream-ref",   default="upstream/main")
@click.option("--no-llm",         is_flag=True, default=False)
@click.option("--from-context",   is_flag=True, default=False,
              help="Read inferred context from stdin as JSON {changed_files, commit_messages}.")
@click.option("--output-format",  default="terminal",
              type=click.Choice(["terminal", "json"]))
@click.option("--config", "-c",   default=None, type=click.Path())
def assess_command(design_doc, repo_path, upstream_ref, no_llm, from_context, output_format, config):
    """Assess merge risk for a design or current branch state.

    Three call modes:
      merge assess docs/xxx.md      -- from design document (highest quality)
      merge assess                   -- auto-infer from git state (default)
      merge assess --from-context    -- read inferred context from stdin JSON
    """
    from src.cli.commands.assess import run_assess
    run_assess(design_doc, repo_path, upstream_ref, no_llm, from_context, output_format, config)
```

#### `src/cli/commands/assess.py`（新文件）— 核心流程

```python
import sys, json, hashlib, datetime
from pathlib import Path
import yaml
from src.tools.design_file_extractor import DesignFileExtractor
from src.tools.churn_analyzer import measure_churn, classify_churn
from src.models.design_review import (
    DevProfile, FileChurnEntry, HighRiskFileEntry, DesignMergeAssessment
)
from src.tools.git_tool import GitTool

def run_assess(design_doc, repo_path, upstream_ref, no_llm, from_context, output_format, config):
    repo = Path(repo_path).resolve()
    git_tool = GitTool(str(repo))

    if from_context:
        ctx = json.load(sys.stdin)
        changed_files = ctx.get("changed_files", [])
        doc_content = "\n".join(changed_files)
        doc_path_str = "<inferred from git state>"
        doc_sha256 = ""
    elif design_doc:
        doc_path = Path(design_doc)
        doc_content = doc_path.read_text(encoding="utf-8")
        doc_path_str = str(doc_path)
        doc_sha256 = hashlib.sha256(doc_content.encode()).hexdigest()[:16]
    else:
        doc_content = _infer_from_git_state(git_tool, upstream_ref)
        doc_path_str = "<inferred from git state>"
        doc_sha256 = ""

    extractor = DesignFileExtractor()
    extracted = extractor.extract(doc_content, repo)

    churn_entries: list[FileChurnEntry] = []
    for f in extracted:
        count = measure_churn(git_tool, f.path, upstream_ref)
        churn_entries.append(FileChurnEntry(
            path=f.path,
            commit_count_90d=count,
            churn_risk=classify_churn(count),
            is_new_file=f.is_new_file,
            is_inline_edit=f.is_inline_edit,
        ))

    if no_llm or not extracted:
        assessment = _build_heuristic_assessment(churn_entries)
    else:
        import asyncio
        from src.agents.design_reviewer import DesignReviewAgent
        merge_config = _load_merge_config(repo)          # 见下；缺失时回落到默认 MergeConfig
        agent = DesignReviewAgent(merge_config.agents.design_reviewer)
        assessment = asyncio.run(agent.run(             # run() 是 async（底层 _call_llm_with_retry 为协程）
            design_doc_content=doc_content,
            file_churn_matrix=churn_entries,
            upstream_ref=upstream_ref,
        ))

    profile = _build_profile(doc_path_str, doc_sha256, upstream_ref, assessment, churn_entries)
    _write_profile(repo, profile)

    if output_format == "json":
        print(json.dumps(profile.model_dump(), indent=2, default=str))
    else:
        _render_terminal(doc_path_str, assessment, churn_entries, profile)


def _infer_from_git_state(git_tool: GitTool, upstream_ref: str) -> str:
    try:
        diff_files = git_tool.repo.git.diff(
            "--name-only", f"{upstream_ref}..HEAD"
        )
        commits = git_tool.repo.git.log(
            "--oneline", f"{upstream_ref}..HEAD"
        )
        return f"Changed files:\n{diff_files}\n\nCommits:\n{commits}"
    except Exception:
        return ""


def _build_heuristic_assessment(entries: list[FileChurnEntry]) -> DesignMergeAssessment:
    from src.models.design_review import DesignMergeAssessment, SeamRecommendation
    risk_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for e in entries:
        risk_counts[e.churn_risk] += 1
    # 注意：CI 门控（F.3）按 overall_risk == "critical" 阻断 PR；heuristic 必须能产出
    # "critical"，否则 --no-llm 路径下门控永不触发（死代码）。
    if risk_counts["critical"] >= 2:
        overall = "critical"
    elif risk_counts["critical"] >= 1:
        overall = "high"
    elif risk_counts["high"] >= 2:
        overall = "medium"
    else:
        overall = "low"
    score = max(0.0, 1.0 - risk_counts["critical"] * 0.3 - risk_counts["high"] * 0.15)
    return DesignMergeAssessment(
        overall_risk=overall,
        merge_friendly_score=round(score, 2),
        positive_patterns=[],
        design_issues=[],
        seam_recommendations=[],
        summary="Heuristic-only assessment (--no-llm). Run without --no-llm for full analysis.",
    )


def _load_merge_config(repo: Path):
    """Load .merge/config.yaml for the design_reviewer AgentLLMConfig; fall back to defaults."""
    from src.models.config import MergeConfig
    cfg_path = repo / ".merge" / "config.yaml"
    if cfg_path.exists():
        return MergeConfig.model_validate(yaml.safe_load(cfg_path.read_text(encoding="utf-8")))
    return MergeConfig(repo_path=str(repo))


def _build_profile(doc_path, doc_sha256, upstream_ref, assessment, churn_entries) -> DevProfile:
    from src.models.config import SeamAnchor
    seam_anchors = [
        SeamAnchor(
            file=r.file,
            symbol=r.symbol,
            required_params=r.required_params,
            on_break=r.suggested_on_break,
        )
        for r in assessment.seam_recommendations
    ]
    high_risk = [
        HighRiskFileEntry(
            path=e.path,
            churn_risk=e.churn_risk,
            commit_count_90d=e.commit_count_90d,
        )
        for e in churn_entries if e.churn_risk in ("high", "critical")
    ]
    return DevProfile(
        assessed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        design_doc=doc_path,
        design_doc_sha256=doc_sha256,
        upstream_ref=upstream_ref,
        overall_risk=assessment.overall_risk,
        merge_friendly_score=assessment.merge_friendly_score,
        seam_anchors=seam_anchors,
        high_risk_files=high_risk,
        positive_patterns=assessment.positive_patterns,
        design_issues=assessment.design_issues,
    )


def _write_profile(repo: Path, profile: DevProfile) -> None:
    merge_dir = repo / ".merge"
    merge_dir.mkdir(exist_ok=True)
    out = merge_dir / "dev-profile.yaml"
    with out.open("w", encoding="utf-8") as f:
        yaml.dump(
            profile.model_dump(mode="json"),
            f, allow_unicode=True, sort_keys=False, default_flow_style=False,
        )
    print(f"Saved → {out}")


def _render_terminal(doc_path, assessment, churn_entries, profile) -> None:
    risk_icon = {"low": "✓", "medium": "⚠", "high": "🔴", "critical": "🚨"}
    icon = risk_icon.get(assessment.overall_risk, "?")
    print(f"\n╭─ Merge Readiness Assessment {'─'*44}╮")
    print(f"│  Design: {doc_path[:60]:<60}│")
    print(f"│  Risk:   {icon} {assessment.overall_risk.upper():<8} "
          f"Score: {assessment.merge_friendly_score:.2f} / 1.00{' ':>20}│")
    print(f"╰{'─'*72}╯\n")
    if assessment.positive_patterns:
        print("✓ Positive patterns")
        for p in assessment.positive_patterns:
            print(f"  • {p}")
    print("\n⚠ File risk matrix")
    for e in sorted(churn_entries, key=lambda x: x.commit_count_90d, reverse=True):
        tag = "新文件 ✓" if e.is_new_file else f"{e.commit_count_90d} commits/90d"
        print(f"  {e.churn_risk.upper():<9} {e.path:<60} {tag}")
    if assessment.design_issues:
        print("\n○ Design issues")
        for issue in assessment.design_issues:
            print(f"  [{issue.severity.upper()}] {issue.file or ''}: {issue.message}")
            print(f"       → {issue.suggestion}")
    if profile.seam_anchors:
        print("\n○ Seam anchor recommendations (saved to dev-profile.yaml)")
        for anchor in profile.seam_anchors:
            params = f"  params: {', '.join(anchor.required_params)}" if anchor.required_params else ""
            print(f"  • {anchor.symbol:<30} {anchor.file:<50} → {anchor.on_break}")
            if params:
                print(f"    {params}")
    print()
```

---

### 5.4 DesignReviewAgent（PR-4）

#### `src/agents/contracts/design_reviewer.yaml`（新文件）

```yaml
name: design_reviewer
version: 1
collaboration: review_only
requires_human_options: false

inputs:
  - design_doc_content
  - file_churn_matrix
  - existing_seam_anchors
  - upstream_ref

output_schema: DesignMergeAssessment

gates:
  - DR-001

forbidden:
  - rule: writes_state
  - rule: direct_llm_call
  - rule: fills_missing_fields_with_defaults
```

#### Gate DR-001 注册（`src/llm/prompts/gate_registry.py` 追加）

> **不存在 `GateConfig`**。注册接口是 `register_gate(gate_id, builder, description)`，
> 其中 `builder` 是返回字符串的可调用对象（system prompt 是它的返回值，不是配置字段）。
> 现有 system gate 都用「常量 builder」写法（见 `_judge_system_constant`）。

```python
DESIGN_REVIEW_SYSTEM = """You are a merge-safety reviewer. Analyse the design document and
file churn matrix, then produce a DesignMergeAssessment JSON object.

Evaluation dimensions:
1. INLINE EDIT RATIO: High-risk inline edits = scattered logic changes on high-churn
   upstream files (not additions). Merge-friendly = changes concentrated in ≤2 call
   sites (seam), with logic extracted to new files.
2. FLAG-OFF SAFETY: Does the design declare a feature flag? Is behavior identical to
   main when the flag is off? Are new fields/params safe-default?
3. MIGRATION ARTIFACT HANDLING: If schema migrations are involved (drizzle/alembic/
   flyway), does the design state "regenerate, never hand-merge"?
4. SEAM IDENTIFICATION: Extract upstream symbols (function names, param names) the fork
   will depend on. If they disappear or change signature, the fork's integration seam
   breaks. Output as seam_recommendations list — NOT free text.
5. UPSTREAM CONTRIBUTION POTENTIAL: Are there additive seam changes (opt-in params,
   extension points) that could be PRed upstream? If yes, add to positive_patterns.

Output ONLY valid JSON matching DesignMergeAssessment schema. No prose outside JSON."""


def _design_review_system_constant(*_args: Any, **_kwargs: Any) -> str:
    return DESIGN_REVIEW_SYSTEM


register_gate(
    "DR-001",
    _design_review_system_constant,
    "Design merge readiness review system prompt.",
)
```

#### `src/agents/design_reviewer.py`（新文件）— 骨架

> 关键修正：构造需 `AgentLLMConfig`；`run()` 必须 `async`；不存在 `_call_llm_with_retry(gate_id=, user_prompt=)`，
> 真实签名是 `(messages, system=None, schema=None, ...)`。传 `schema=DesignMergeAssessment` 时直接返回
> 解析好的模型实例，无需手写 `json.loads`（与 planner/judge 现有用法一致）。gate 文本经 `get_gate(...).render()` 取得。

```python
from src.agents.base_agent import BaseAgent
from src.models.config import AgentLLMConfig, SeamAnchor
from src.models.design_review import FileChurnEntry, DesignMergeAssessment
from src.llm.prompts.gate_registry import get_gate

class DesignReviewAgent(BaseAgent):
    contract_name = "design_reviewer"

    def __init__(self, llm_config: AgentLLMConfig):
        super().__init__(llm_config)

    async def run(
        self,
        design_doc_content: str,
        file_churn_matrix: list[FileChurnEntry],
        upstream_ref: str,
        existing_seam_anchors: list[SeamAnchor] | None = None,
    ) -> DesignMergeAssessment:
        matrix_text = "\n".join(
            f"  {e.churn_risk.upper():9} {e.path} "
            f"({e.commit_count_90d} commits/90d) "
            f"{'[NEW]' if e.is_new_file else '[EDIT]'}"
            for e in file_churn_matrix
        )
        prompt = (
            f"## Design Document\n\n{design_doc_content[:8000]}\n\n"
            f"## File Churn Matrix (upstream {upstream_ref}, last 90 days)\n\n"
            f"{matrix_text}\n\n"
            "Produce DesignMergeAssessment JSON."
        )
        result = await self._call_llm_with_retry(
            messages=[{"role": "user", "content": prompt}],
            system=get_gate("DR-001").render(),
            schema=DesignMergeAssessment,   # 返回已校验的模型；schema 校验失败由重试层抛 ModelOutputError
        )
        assert isinstance(result, DesignMergeAssessment)
        return result
```

---

### 5.5 Initialize Phase 集成（PR-3）

#### A.3 — `_check_seam_anchors()`（Initialize Phase，在 interface_change_extractor 之后）

> 关键修正：① initialize 访问 git 用 `ctx.git_tool`（非 `self.git_tool`）；② upstream ref 取
> `state.config.upstream_ref`；③ break 检测到后，`on_break=human_required` 的锚点**在本阶段**经
> `_record_profile_escalate_human`-同款机制提升为 `ESCALATE_HUMAN`（judge 只读、不能提升，见 §5.6）。

```python
def _check_seam_anchors(self, state: MergeState, ctx: PhaseContext) -> None:
    config = state.config
    if not config.seam_anchors:
        return
    upstream_ref = config.upstream_ref
    for anchor in config.seam_anchors:
        upstream_file = ctx.git_tool.get_file_content(upstream_ref, anchor.file)
        if upstream_file is None:
            self._record_seam_break(state, anchor, "file_removed",
                f"{anchor.file} not found in {upstream_ref}")
            continue
        symbols = self._extract_symbols(upstream_file, anchor.file)   # {symbol: [param, ...]}
        if anchor.symbol not in symbols:
            self._record_seam_break(state, anchor, "symbol_removed",
                f"'{anchor.symbol}' not found in {anchor.file}")
            continue
        sym_params = symbols[anchor.symbol]
        for param in anchor.required_params:
            if param not in sym_params:
                self._record_seam_break(state, anchor, "param_removed",
                    f"param '{param}' missing from '{anchor.symbol}' in {anchor.file}")

def _record_seam_break(
    self, state: MergeState, anchor: SeamAnchor, break_kind: str, detail: str
) -> None:
    state.seam_anchor_breaks.append(SeamAnchorBreak(
        anchor=anchor, break_kind=break_kind, detail=detail,
    ))
    # on_break=human_required: 在 initialize 即提升为 ESCALATE_HUMAN（judge 无写权）。
    # category 取该文件已计算的分类，回落到 C（both_changed，接缝断裂的典型情形）。
    if anchor.on_break == "human_required":
        category = state.file_categories.get(anchor.file, FileChangeCategory.C)
        self._record_profile_escalate_human(
            state, anchor.file, category=category,
            rationale=f"seam anchor broken: {detail}",
        )
```

> **`_extract_symbols` 需新写解析器，不能直接复用 `InterfaceChangeExtractor`**。后者
> `extract_from_paths()` 返回 `list[InterfaceChange]`（`file_path/symbol/change_kind/before/after`，
> 是「base↔upstream 的差异」），而这里需要的是「单份内容里某符号是否存在 + 其参数列表」
> （`{symbol: [params]}`）。可参考 `interface_change_extractor` 的私有方法 `_extract_methods` 的正则/
> AST 思路另写一个轻量提取器。**因此 A.3 工时应从 0.5d 上调至 ~1d。**

#### E.5 — `_load_dev_profile()`（Initialize Phase 末尾）

> 关键修正：① 不存在 `state.notifications` → 用 `ctx.notify(...)`；② 禁止 `config.seam_anchors.append(...)`
> 原地改 config → 用 `state.config.model_copy(update={...})` 重建（与 `initialize.py:373` 一致）。

```python
def _load_dev_profile(self, state: MergeState, ctx: PhaseContext) -> None:
    config = state.config
    profile_path = Path(config.repo_path) / ".merge" / "dev-profile.yaml"
    if not profile_path.exists():
        return
    import yaml, hashlib
    from src.models.design_review import DevProfile
    profile = DevProfile.model_validate(yaml.safe_load(profile_path.read_text()))

    design_doc_path = Path(config.repo_path) / profile.design_doc
    if design_doc_path.exists() and profile.design_doc_sha256:
        current_sha = hashlib.sha256(design_doc_path.read_bytes()).hexdigest()[:16]
        if current_sha != profile.design_doc_sha256:
            ctx.notify(
                "initialize",
                f"dev-profile.yaml 可能已过期：{profile.design_doc} "
                f"自上次 `merge assess` 后已变化，建议重新评估。",
            )

    existing_files = {a.file for a in config.seam_anchors}
    new_anchors = [a for a in profile.seam_anchors if a.file not in existing_files]
    if new_anchors:
        state.config = state.config.model_copy(
            update={"seam_anchors": list(config.seam_anchors) + new_anchors}
        )

    state.dev_profile = profile
```

#### C — `_detect_platform_risk_signals()`（Initialize Phase）

> **Project Generality（CLAUDE.md 硬规则）**：pattern list **不得硬编码进 `src/`**——原方案的
> `rewrite.*go` / `golang` 是从 dify「rewrite in go」校准出来的特定 fork 信号，属「calibration
> constants bleeding into production code」。改为读 `state.config.platform_risk_patterns`，源码默认空
> （零信号、零副作用），具体 pattern 由 `<repo>/.merge/config.yaml` 提供。

```python
import re
from src.models.state import PlatformRiskSignal

def _detect_platform_risk_signals(
    self, state: MergeState, ctx: PhaseContext, commit_window: int = 80,
) -> None:
    patterns = state.config.platform_risk_patterns
    if not patterns:
        return
    upstream_ref = state.config.upstream_ref
    try:
        log = ctx.git_tool.repo.git.log("--oneline", f"-{commit_window}", upstream_ref)
    except Exception:
        return
    lines = log.splitlines()
    for p in patterns:
        matched = [l for l in lines if re.search(p.pattern, l, re.I)]
        if matched:
            state.platform_risk_signals.append(PlatformRiskSignal(
                pattern=p.pattern,
                matched_commits=matched[:5],
                severity=p.severity,
            ))
```

**Report Phase 追加**：若 `state.platform_risk_signals` 非空，在报告首页插入警告框。

---

### 5.6 Judge 确定性检查（PR-5）

#### `src/agents/judge_agent.py` — 追加 `_check_seam_anchors()`

> 关键修正（违反 CLAUDE.md「Judge read-only」硬约束的部分）：
> - 不存在 `DeterministicIssue` → 用 `JudgeIssue`（字段 `file_path/issue_level/issue_type/description/
>   must_fix_before_merge/veto_condition`；VETO 通过 `must_fix_before_merge=True` + `veto_condition` 表达，
>   `issue_level=IssueSeverity.CRITICAL`）。
> - 不存在 `_elevate_to_human_required`，judge 也无 `self.state` 写权 → `on_break=human_required` 已在
>   **initialize**（§5.5 `_record_seam_break`）经 `ESCALATE_HUMAN` 提升，judge 不处理。
> - 读 `state.seam_anchor_breaks` 须**直接访问**（已加入 judge.yaml `inputs`），不要用
>   `getattr(state, ..., default)`——默认值会吞掉 `FieldNotInContract`，使检查静默失效（参见历史教训
>   `project_judge_dead_contract_checks`）。
> - 签名收 `ReadOnlyStateView`，与现有 `_check_sentinel_hits(self, state)` 一致。

```python
def _check_seam_anchors(self, state: ReadOnlyStateView) -> list[JudgeIssue]:
    """Emit VETO for every seam anchor whose break is configured on_break=veto."""
    issues: list[JudgeIssue] = []
    for brk in state.seam_anchor_breaks:
        if brk.anchor.on_break != "veto":
            continue   # human_required 已在 initialize 提升；warn 由 initialize 通知
        issues.append(JudgeIssue(
            file_path=brk.anchor.file,
            issue_level=IssueSeverity.CRITICAL,
            issue_type="seam_anchor_broken",
            description=(
                f"Seam anchor '{brk.anchor.symbol}' {brk.break_kind}: {brk.detail}"
            ),
            must_fix_before_merge=True,
            veto_condition=f"Seam anchor '{brk.anchor.symbol}' broken ({brk.break_kind})",
        ))
    return issues
```

在 `_run_deterministic_pipeline()` 末尾（与 `_check_sentinel_hits` 等并列）追加
`issues.extend(self._check_seam_anchors(state))`，并入既有确定性结果。

#### `src/agents/contracts/judge.yaml` inputs 追加

```yaml
inputs:
  ...
  - seam_anchor_breaks    # list[SeamAnchorBreak]
```

---

### 5.7 sync_mode + Planner 集成（PR-6）

#### Gate P-SYNC-001（`gate_registry.py` 追加）

> 同 DR-001：用 `register_gate(id, builder, description)`，无 `GateConfig`。

```python
SYNC_UPSTREAM_STRATEGY = """[SYNC MODE: upstream]
This run is an upstream synchronisation (not a feature merge).

For high-churn mainline files and seam-anchor files:
1. Prefer the upstream version as the base.
2. Retain fork changes only within ±N lines of the seam_anchor call sites declared in config.
3. Mark migration artefacts (drizzle/**, **/migrations/**) as SKIP; do not hand-merge.
4. If the seam boundary cannot be determined, escalate to HUMAN_REQUIRED rather than AUTO_SAFE.
"""


def _sync_upstream_strategy_constant(*_args: Any, **_kwargs: Any) -> str:
    return SYNC_UPSTREAM_STRATEGY


register_gate(
    "P-SYNC-001",
    _sync_upstream_strategy_constant,
    "Upstream sync strategy gate (active only when sync_mode=upstream).",
)
```

#### `src/agents/planner_agent.py` — 条件激活 P-SYNC-001

```python
if config.sync_mode == "upstream":
    active_gates.append("P-SYNC-001")
```

#### `src/agents/contracts/planner.yaml` inputs 追加

```yaml
inputs:
  ...
  - sync_mode    # Literal["feature","upstream"]
  - dev_profile  # DevProfile | None
```

#### `merge sync` 子命令（`src/cli/main.py` 追加）

```python
@cli.command("sync")
@click.option("--upstream-ref",   required=True)
@click.option("--target-ref",     default="origin/main")
@click.option("--feature-branch", default=None)
@click.option("--skip-patterns",  multiple=True)
def sync_cmd(upstream_ref, target_ref, feature_branch, skip_patterns):
    """Upstream-aware sync: seam-check → sync_mode=upstream merge → post-merge hints."""
    from src.cli.commands.sync import run_sync
    run_sync(upstream_ref, target_ref, feature_branch, list(skip_patterns))
```

内部流程：`merge validate` → 锚点预检（只读） → 以 `sync_mode=upstream` 调主合并流程 →
打印后处理提示（如"若有迁移文件，请重新运行 schema 生成命令"）。

---

### 5.8 编码代理集成（PR-7）

#### F.1 — `/assess` Skill（`.claude/skills/assess/SKILL.md`，新文件）

```markdown
---
name: assess
description: >
  Assess merge risk for the current feature branch before writing more code.
  Reads git state and conversation context — no design document required.
  Writes .merge/dev-profile.yaml for consumption by future merge runs.
---

## When to invoke
- At the START of planning a new feature on a fork of an upstream project
- After the first few commits on a new feature branch
- Whenever touching files that have high upstream churn

## Steps

1. Run `git diff origin/main..HEAD --name-only` and `git log --oneline origin/main..HEAD`
2. If a design document is referenced in conversation, read it; otherwise use git output as context
3. Run: `merge assess --upstream-ref upstream/main`
   (no-arg mode auto-infers from git state)
4. Show the risk matrix and seam recommendations inline
5. If critical-risk files found: warn explicitly before proceeding with implementation

## Output
- `.merge/dev-profile.yaml` written (or updated) in the repo
- Inline summary: file risk, seam anchors, merge_friendly_score
```

集成到 `feature-dev` Skill 的 Phase 0。**注意：`.claude/skills/feature-dev/SKILL.md` 当前不存在**
（现有 skills：verify / add-agent / explain-arch / run-integration / control-cli /
setup-conflict-test-branches）。因此本段是**新建该 skill 文件**，而非「追加段落」；若不打算新建，
可将 Phase 0 指引并入 `/assess` skill 自身的 "When to invoke"。

```markdown
## Phase 0 — Merge Risk Assessment (MANDATORY for fork projects)

1. Run `git remote -v` — if `upstream` remote exists, this is a fork project
2. If fork: run `/assess` now (no design doc needed, reads git state automatically)
3. If assessment shows HIGH or CRITICAL risk for any planned file:
   - Surface this to the user BEFORE proceeding to design
   - Ask: "This file has N upstream commits in 90 days. Consider extracting your
     changes to a new file + seam pattern to reduce merge risk. Proceed anyway?"
4. Continue to Phase 1 (Requirements) only after assessment is recorded
```

#### F.2 — PostToolUse Hook（`.claude/hooks/seam_zone_reminder.py`，新文件；目录 `.claude/hooks/` 需新建）

> **Project Generality**：原方案 fallback 分支硬编码 `["response-handler", "schema.ts",
> "guard-pipeline", "statistics.ts"]`——全是 dify 专属路径，随仓库分发即固化特定 fork 行为，禁止。
> 改为只依据 `.merge/dev-profile.yaml` 的 `high_risk_files`（已是 per-repo 数据）做匹配；无 profile
> 时不再凭硬编码 pattern 提醒，仅在文件确属高 churn 记录时提示。

```python
import json, sys, os
from pathlib import Path

hook_input = json.load(sys.stdin)
edited_file = hook_input.get("tool_input", {}).get("file_path", "")
session_assessed = os.getenv("MERGE_ASSESS_DONE_IN_SESSION") == "1"

if session_assessed:
    sys.exit(0)

profile_path = Path(".merge/dev-profile.yaml")
if profile_path.exists():
    import yaml
    profile = yaml.safe_load(profile_path.read_text())
    high_risk = [f["path"] for f in profile.get("high_risk_files", [])]
    if any(r in edited_file for r in high_risk):
        print(json.dumps({
            "type": "notification",
            "message": (
                f"⚠️  {edited_file} 是已记录的高 churn 文件。"
                "本 session 未重新评估——如设计有变化，请运行 /assess 更新。"
            ),
        }))
```

Hook 配置（**追加到 `.claude/settings.json` 现有 `PostToolUse` 数组**，勿替换整个 `hooks` 对象——
当前已有 ruff/mypy 的 PostToolUse hook）。给现有 `Write|Edit` 匹配项追加一个 hook，或新增一项：

```json
{
  "matcher": "Edit|Write|MultiEdit",
  "hooks": [{
    "type": "command",
    "command": "python3 .claude/hooks/seam_zone_reminder.py",
    "timeout": 5
  }]
}
```

#### F.3 — CI merge-readiness 作业（`.github/workflows/merge-readiness.yml`，新文件）

```yaml
name: Merge Readiness Check
on:
  pull_request:
    branches: [main, dev]
    types: [opened, synchronize]

jobs:
  assess:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }

      - name: Setup Python + merge tool
        run: pip install -e ".[dev]"

      - name: Fetch upstream
        run: |
          git remote add upstream ${{ vars.UPSTREAM_URL }} || true
          git fetch upstream main --depth=100

      - name: Run merge assess (auto-infer, no LLM)
        id: assess
        run: |
          merge assess --upstream-ref upstream/main --no-llm \
            --output-format json > /tmp/assessment.json 2>&1 || true
          echo "risk=$(python3 -c "
          import json; d=json.load(open('/tmp/assessment.json'))
          print(d.get('overall_risk','unknown'))")" >> "$GITHUB_OUTPUT"
          echo "score=$(python3 -c "
          import json; d=json.load(open('/tmp/assessment.json'))
          print(d.get('merge_friendly_score', 0))")" >> "$GITHUB_OUTPUT"

      - name: Post assessment comment
        uses: actions/github-script@v7
        env:
          RISK: ${{ steps.assess.outputs.risk }}
          SCORE: ${{ steps.assess.outputs.score }}
        with:
          script: |
            const risk  = process.env.RISK;
            const score = process.env.SCORE;
            const icon  = { low:'✅', medium:'⚠️', high:'🔴', critical:'🚨' }[risk] || '❓';
            await github.rest.issues.createComment({
              owner: context.repo.owner, repo: context.repo.repo,
              issue_number: context.issue.number,
              body: `## ${icon} Merge Readiness: ${risk.toUpperCase()} (${score}/1.0)\n\n` +
                    `Auto-assessed from branch changes against upstream.\n` +
                    `_Run \`/assess\` in Claude Code for full LLM analysis._`,
            });

      - name: Block critical risk without acknowledgement
        if: steps.assess.outputs.risk == 'critical'
        run: |
          ACKED=$(python3 -c "
          import yaml, pathlib
          p = pathlib.Path('.merge/dev-profile.yaml')
          if p.exists():
              d = yaml.safe_load(p.read_text())
              print('yes' if d.get('risk_acknowledged') else 'no')
          else:
              print('no')
          ")
          if [ "$ACKED" != "yes" ]; then
            echo "::error::Merge risk is CRITICAL."
            echo "::error::Add 'risk_acknowledged: true' to .merge/dev-profile.yaml"
            echo "::error::after reviewing the seam anchors."
            exit 1
          fi
```

---

## 6. `.merge/dev-profile.yaml` 完整 Schema 参考

```yaml
# Auto-generated by `merge assess`. Edit seam_anchors as needed.
# Re-run `merge assess` if the design changes significantly.

version: 1
assessed_at: "2026-05-25T21:00:00+08:00"
design_doc: docs/limit/group-rate-limit.md
design_doc_sha256: "a3f8c2d1..."
upstream_ref: upstream/main

overall_risk: medium               # low | medium | high | critical
merge_friendly_score: 0.72

# Seam anchors — auto-populated, manually curated; consumed by Initialize Phase
seam_anchors:
  - file: src/lib/rate-limit/lease.ts
    symbol: calculateLeaseSlice
    required_params: [minSliceUsd]
    on_break: veto
  - file: src/app/v1/_lib/proxy/guard-pipeline.ts
    symbol: registerExtensionStep
    on_break: veto
  - file: src/repository/statistics.ts
    symbol: sumUserCostInTimeRange
    required_params: [countedInGlobalOnly]
    on_break: warn

high_risk_files:
  - path: src/app/v1/_lib/proxy/response-handler.ts
    churn_risk: critical
    commit_count_90d: 47
    design_approach: "seam-extracted to backfill.ts"
  - path: src/drizzle/schema.ts
    churn_risk: high
    commit_count_90d: 23
    design_approach: "tail-append + regenerate"

positive_patterns:
  - "Flag-off 安全：关闭时行为与 main 一致"
  - "新文件占比高（~90%）"
  - "Seam 化：response-handler 仅 2 处调用点"

design_issues: []

# CI gate: set to true after reviewing seam anchors when overall_risk=critical
risk_acknowledged: false
```

---

## 7. 完整文件变更清单

### 新建文件

| 文件 | 所属 ID |
|---|---|
| `src/models/design_review.py` | E.4 |
| `src/tools/design_file_extractor.py` | E.2 |
| `src/tools/churn_analyzer.py` | E.2 |
| `src/cli/commands/assess.py` | E.1 |
| `src/cli/commands/sync.py` | D |
| `src/agents/design_reviewer.py` | E.3 |
| `src/agents/contracts/design_reviewer.yaml` | E.3 |
| `tests/unit/test_seam_anchors.py` | A |
| `tests/unit/test_design_file_extractor.py` | E.2 |
| `tests/unit/test_assess_command.py` | E.1 |
| `tests/unit/test_design_reviewer_agent.py` | E.3 |
| `.claude/skills/assess/SKILL.md` | F.1 |
| `.claude/skills/feature-dev/SKILL.md`（当前不存在；若采用 Phase 0 集成则新建，否则并入 assess skill） | F.1 |
| `.claude/hooks/seam_zone_reminder.py`（含新建 `.claude/hooks/` 目录） | F.2 |
| `.github/workflows/merge-readiness.yml` | F.3 |

### 修改文件

| 文件 | 变更内容 | 所属 ID |
|---|---|---|
| `src/models/config.py` | `+SeamAnchor`、`+PlatformRiskPattern`、`MergeConfig.seam_anchors`、`MergeConfig.sync_mode`、`MergeConfig.platform_risk_patterns`、`AgentsLLMConfig.design_reviewer` | A.1、B、C、E.1 |
| `src/models/state.py` | `+SeamAnchorBreak`、`+PlatformRiskSignal`、`MergeState` 新字段 | A.1、C |
| `src/core/phases/initialize.py` | `+_check_seam_anchors()`、`+_load_dev_profile()`、`+_detect_platform_risk_signals()` | A.3、E.5、C |
| `src/agents/judge_agent.py` | `+_check_seam_anchors()` in deterministic pipeline | A.4 |
| `src/agents/contracts/judge.yaml` | inputs 加 `seam_anchor_breaks` | A.4 |
| `src/agents/planner_agent.py` | 条件激活 P-SYNC-001 | B |
| `src/agents/contracts/planner.yaml` | inputs 加 `sync_mode`、`dev_profile` | B |
| `src/llm/prompts/gate_registry.py` | 注册 `DR-001`、`P-SYNC-001` | E.3、B |
| `src/cli/main.py` | `+assess` 命令、`+sync` 命令 | E.1、D |
| `src/core/phases/report_generation.py` | 首页平台风险警告框 | C |
| `.claude/settings.json` | **追加到现有 `PostToolUse` 数组**（勿替换；已有 ruff/mypy hook） | F.2 |

---

## 8. 测试要求

每个 PR 须覆盖以下用例：

| PR | 关键测试场景 |
|---|---|
| PR-1 | `SeamAnchor` / `DevProfile` Pydantic 校验；`seam_anchors: []` 默认空时零副作用 |
| PR-2 | `DesignFileExtractor`: code_block / inline_code / file_list 三路提取；`measure_churn` git mock |
| PR-2 | `assess_command` 无参数（git 推断）、`--no-llm`、`--from-context` 三种模式产出正确 profile |
| PR-3 | `_check_seam_anchors`: file_removed / symbol_removed / param_removed 三种断裂场景；`on_break=human_required` 时在 initialize 即写入 `ESCALATE_HUMAN`（不依赖 judge）；`_extract_symbols` 对各语言符号/参数提取 |
| PR-3 | `_load_dev_profile`: sha256 漂移告警；seam_anchors 去重合并；profile 缺失时无副作用 |
| PR-3 | `_detect_platform_risk_signals`: patterns 来自 `config.platform_risk_patterns`，空（默认）时零信号；非空时正则命中 / 无命中两种 case |
| PR-4 | `DesignReviewAgent` LLM 输出 non-JSON 时抛 `ModelOutputError`；正常路径 schema 校验 |
| PR-5 | Judge 确定性 pipeline：VETO 接缝断裂不被后续 LLM 覆盖；`on_break=warn` 不触发 VETO |
| PR-6 | `sync_mode=upstream` 时 P-SYNC-001 激活；`sync_mode=feature`（默认）时不激活 |
| PR-7 | hook 脚本：profile 存在 + 文件命中 → notification；session_assessed=1 → 静默 |
