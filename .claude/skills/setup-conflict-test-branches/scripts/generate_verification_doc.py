#!/usr/bin/env python3
"""
Generate merge outcome verification document from a Code Merge System run.

Usage:
  python generate_verification_doc.py <target_repo> [--run-id <id>] [--output <path>]

  <target_repo>  Path to the target repository (default: current directory)
  --run-id       Specific run ID; if omitted, uses the most recent run
  --output       Output path for the markdown document (default: stdout)
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Locate checkpoint
# ---------------------------------------------------------------------------


def find_checkpoint(target_repo: Path, run_id: str | None) -> tuple[Path, dict]:
    candidates = []

    runs_dir = target_repo / ".merge" / "runs"
    if runs_dir.exists():
        for d in runs_dir.iterdir():
            if d.is_dir():
                cp = d / "checkpoint.json"
                if cp.exists():
                    candidates.append(cp)

    dev_cp = target_repo / "outputs" / "debug" / "checkpoints" / "checkpoint.json"
    if dev_cp.exists():
        candidates.append(dev_cp)

    if not candidates:
        sys.exit("No checkpoint.json found. Run 'merge' first.")

    if run_id:
        matches = [c for c in candidates if run_id in str(c)]
        if not matches:
            sys.exit(f"No checkpoint found for run_id={run_id}")
        cp_path = matches[0]
    else:
        cp_path = max(candidates, key=lambda p: p.stat().st_mtime)

    state = json.loads(cp_path.read_text())
    return cp_path, state


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)
    return result.stdout.strip()


def file_diff(path: str, base: str, head: str, cwd: Path) -> str:
    diff = git(["diff", "--unified=3", f"{base}:{path}", f"{head}:{path}"], cwd=cwd)
    if not diff:
        return "_（无差异或文件不存在于某一侧）_"
    lines = diff.split("\n")
    if len(lines) > 60:
        lines = lines[:60] + [f"... (省略 {len(lines) - 60} 行)"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def phase_status(state: dict, phase_key: str) -> str:
    results = state.get("phase_results") or {}
    r = results.get(phase_key)
    if not r:
        return "— (未记录)"
    status = r.get("status", "?")
    err = r.get("error")
    return f"`{status}`" + (f"  ⚠ {err}" if err else "")


def risk_badge(level: str) -> str:
    return {
        "auto_safe": "🟢 auto_safe",
        "auto_risky": "🟡 auto_risky",
        "human_required": "🔒 human_required",
        "deleted_only": "⬜ deleted_only",
        "binary": "⬜ binary",
    }.get(level, level)


def fmt_ts(ts: str | None) -> str:
    if not ts:
        return "—"
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except Exception:
        return ts


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def section_basic(state: dict, cp_path: Path) -> list[str]:
    cfg = state.get("config") or {}
    lines = [
        "## 基本信息",
        "",
        f"| 项目 | 值 |",
        f"|------|---|",
        f"| Run ID | `{state.get('run_id', '?')}` |",
        f"| 最终状态 | `{state.get('status', '?')}` |",
        f"| upstream_ref | `{cfg.get('upstream_ref', '?')}` |",
        f"| fork_ref | `{cfg.get('fork_ref', '?')}` |",
        f"| merge_base | `{state.get('merge_base_commit', '?')[:12]}` |",
        f"| 创建时间 | {fmt_ts(state.get('created_at'))} |",
        f"| 更新时间 | {fmt_ts(state.get('updated_at'))} |",
        f"| checkpoint | `{cp_path}` |",
        "",
    ]
    cost = state.get("cost_summary")
    if cost:
        lines += [
            "**费用摘要**",
            "",
            f"```json\n{json.dumps(cost, indent=2, ensure_ascii=False)}\n```",
            "",
        ]
    return lines


def section_branch_diff(state: dict, repo: Path) -> list[str]:
    base = state.get("merge_base_commit", "")
    cfg = state.get("config") or {}
    upstream = cfg.get("upstream_ref", "test/upstream")
    fork = cfg.get("fork_ref", "test/fork")

    lines = [
        "## 分支差异（制造的冲突）",
        "",
        f"- 共同祖先：`{base[:12] if base else '?'}`",
        f"- upstream：`{upstream}`",
        f"- fork：`{fork}`",
        "",
    ]

    if base:
        fork_files = git(["diff", "--name-only", base, fork], repo).splitlines()
        upstream_files = git(["diff", "--name-only", base, upstream], repo).splitlines()
        c_class = sorted(set(fork_files) & set(upstream_files))
        b_class = sorted(set(upstream_files) - set(fork_files))

        lines += [
            f"**C-class 文件（双边修改，共 {len(c_class)} 个）**",
            "",
        ]
        for f in c_class:
            lines.append(f"- `{f}`")
        lines += [
            "",
            f"**B-class 文件（仅 upstream 修改，共 {len(b_class)} 个）**（前 10 个）",
            "",
        ]
        for f in b_class[:10]:
            lines.append(f"- `{f}`")
        if len(b_class) > 10:
            lines.append(f"- _...共 {len(b_class)} 个，省略其余_")
        lines.append("")

    return lines


def section_initialize(state: dict) -> list[str]:
    cats = state.get("file_categories") or {}
    risks = state.get("file_classifications") or {}

    by_cat: dict[str, list[str]] = {}
    for f, c in cats.items():
        by_cat.setdefault(c, []).append(f)

    lines = [
        "## Phase 1 — Initialize",
        "",
        f"状态：{phase_status(state, 'initialize')}",
        "",
        "**文件分类汇总**",
        "",
        f"| 分类 | 数量 |",
        f"|------|------|",
    ]
    for cat, files in sorted(by_cat.items()):
        lines.append(f"| {cat} | {len(files)} |")

    c_files = by_cat.get("both_changed", [])
    if c_files:
        lines += ["", "**C-class 文件风险评分**", ""]
        lines.append("| 文件 | 风险 |")
        lines.append("|------|------|")
        for f in sorted(c_files):
            r = risks.get(f, "—")
            lines.append(f"| `{f}` | {risk_badge(r)} |")
    lines.append("")
    return lines


def section_planning(state: dict) -> list[str]:
    plan = state.get("merge_plan")
    lines = [
        "## Phase 2 — Planning (PlannerAgent)",
        "",
        f"状态：{phase_status(state, 'planning')}",
        "",
    ]
    if not plan:
        lines += ["_无 merge_plan（planning 未完成）_", ""]
        return lines

    risk_sum = plan.get("risk_summary") or {}
    lines += [
        "**风险分布**",
        "",
        f"| 等级 | 数量 |",
        f"|------|------|",
        f"| HUMAN_REQUIRED | {risk_sum.get('human_required_count', 0)} |",
        f"| HIGH | {risk_sum.get('high_count', 0)} |",
        f"| MEDIUM | {risk_sum.get('medium_count', 0)} |",
        f"| LOW | {risk_sum.get('low_count', 0)} |",
        "",
        f"**plan_id**: `{plan.get('plan_id', '?')}`  "
        f"**版本**: `{plan.get('version', '?')}`",
        "",
    ]

    phases = plan.get("phases") or []
    if phases:
        lines += ["**Merge Phases（文件批次）**", ""]
        for ph in phases:
            layer = ph.get("layer_name") or ph.get("layer", "?")
            files = ph.get("files") or []
            lines.append(f"- **{layer}**：{len(files)} 个文件")
        lines.append("")

    hr_files = [
        f
        for f in (state.get("file_classifications") or {})
        if (state.get("file_classifications") or {}).get(f) == "human_required"
    ]
    if hr_files:
        lines += ["**HUMAN_REQUIRED 文件**", ""]
        for f in sorted(hr_files):
            lines.append(f"- `{f}`")
        lines.append("")

    return lines


def section_plan_review(state: dict) -> list[str]:
    verdict = state.get("plan_judge_verdict")
    log = state.get("plan_review_log") or []
    rounds = state.get("plan_revision_rounds", 0)

    lines = [
        "## Phase 3 — Plan Review (PlannerJudgeAgent)",
        "",
        f"状态：{phase_status(state, 'plan_review')}",
        "",
        f"**修订轮次**：{rounds}",
        "",
    ]

    if verdict:
        result = verdict.get("result", "?")
        lines += [
            f"**最终裁定**：`{result}`  **模型**：`{verdict.get('judge_model', '?')}`",
            "",
            f"> {verdict.get('summary', '')}",
            "",
        ]
        issues = verdict.get("issues") or []
        if issues:
            lines += ["**Issues**", ""]
            for issue in issues:
                sev = issue.get("severity", "?")
                desc = issue.get("description", "")
                lines.append(f"- [{sev}] {desc}")
            lines.append("")

    for i, rnd in enumerate(log):
        v = rnd.get("verdict") or {}
        lines.append(
            f"**Round {i}** — `{v.get('result', '?')}` — {fmt_ts(v.get('timestamp'))}"
        )

    lines.append("")
    return lines


def section_auto_merge(state: dict) -> list[str]:
    records = state.get("file_decision_records") or {}
    disputes = state.get("plan_disputes") or []

    lines = [
        "## Phase 4 — Auto Merge (ExecutorAgent)",
        "",
        f"状态：{phase_status(state, 'auto_merge')}",
        "",
        f"**已处理文件**：{len(records)}  **plan_disputes**：{len(disputes)}",
        "",
    ]

    if records:
        lines += [
            "| 文件 | 决策 | 来源 | 回滚? |",
            "|------|------|------|-------|",
        ]
        for path, rec in sorted(records.items()):
            decision = rec.get("decision", "?")
            source = rec.get("decision_source", "?")
            rolled = "✅" if rec.get("is_rolled_back") else "—"
            lines.append(f"| `{path}` | {decision} | {source} | {rolled} |")
        lines.append("")

    if disputes:
        lines += ["**Plan Disputes**", ""]
        for d in disputes:
            lines.append(f"- `{d.get('file_path', '?')}` — {d.get('reason', '')}")
        lines.append("")

    return lines


def section_conflict_analysis(state: dict) -> list[str]:
    analyses = state.get("conflict_analyses") or {}
    requests = state.get("human_decision_requests") or {}

    lines = [
        "## Phase 5 — Conflict Analysis (ConflictAnalystAgent)",
        "",
        f"状态：{phase_status(state, 'conflict_analysis')}",
        "",
        f"**分析文件数**：{len(analyses)}  **HumanDecisionRequests**：{len(requests)}",
        "",
    ]

    if analyses:
        lines += [
            "| 文件 | 置信度 | 推荐策略 | 冲突类型 | 安全敏感 |",
            "|------|--------|---------|---------|---------|",
        ]
        for path, a in sorted(analyses.items()):
            conf = f"{a.get('overall_confidence', a.get('confidence', 0)):.2f}"
            strategy = a.get("recommended_strategy", "?")
            ctype = a.get("conflict_type", "?")
            sec = "🔒" if a.get("is_security_sensitive") else "—"
            lines.append(f"| `{path}` | {conf} | {strategy} | {ctype} | {sec} |")
        lines.append("")

    return lines


def section_human_review(state: dict) -> list[str]:
    decisions = state.get("human_decisions") or {}
    pending = state.get("pending_user_decisions") or []

    lines = [
        "## Phase 6 — Human Review (HumanInterfaceAgent)",
        "",
        f"状态：{phase_status(state, 'human_review')}",
        "",
        f"**待决策项**：{len(pending)}  **已填决策**：{len(decisions)}",
        "",
    ]

    if decisions:
        lines += ["| 文件 | 决策 |", "|------|------|"]
        for path, dec in sorted(decisions.items()):
            lines.append(f"| `{path}` | `{dec}` |")
        lines.append("")

    return lines


def section_judge_review(state: dict) -> list[str]:
    verdict = state.get("judge_verdict")
    smoke = state.get("smoke_test_report")
    repair_rounds = state.get("judge_repair_rounds", 0)

    lines = [
        "## Phase 7 — Judge Review (JudgeAgent + SmokeTestAgent)",
        "",
        f"状态：{phase_status(state, 'judge_review')}",
        "",
        f"**修复轮次**：{repair_rounds}",
        "",
    ]

    if verdict:
        v = verdict.get("verdict", "?")
        conf = verdict.get("overall_confidence", 0)
        veto = "✅ 触发" if verdict.get("veto_triggered") else "—"
        lines += [
            f"**JudgeVerdict**：`{v}`  置信度：`{conf:.2f}`  VETO：{veto}",
            "",
            f"> {verdict.get('summary', '')}",
            "",
        ]
        failed = verdict.get("failed_files") or []
        if failed:
            lines += ["**未通过文件**", ""]
            for f in failed:
                lines.append(f"- `{f}`")
            lines.append("")

        issues = verdict.get("issues") or []
        if issues:
            lines += ["**Issues**", ""]
            for iss in issues[:10]:
                sev = iss.get("severity", "?")
                desc = iss.get("description", "")
                lines.append(f"- [{sev}] {desc}")
            if len(issues) > 10:
                lines.append(f"- _...共 {len(issues)} 条，省略其余_")
            lines.append("")

    if smoke:
        status = smoke.get("status", "?")
        lines += [f"**SmokeTest**：`{status}`", ""]
        suites = smoke.get("suite_results") or []
        for s in suites:
            name = s.get("name", "?")
            sr = s.get("status", "?")
            lines.append(f"- {name}：`{sr}`")
        lines.append("")
    else:
        lines += ["**SmokeTest**：未运行（未配置 suites 或 Judge 未 APPROVED）", ""]

    return lines


def section_report_generation(state: dict, repo: Path) -> list[str]:
    lines = [
        "## Phase 8 — Report Generation",
        "",
        f"状态：{phase_status(state, 'report_generation')}",
        "",
    ]
    run_id = state.get("run_id", "")
    report_path = repo / ".merge" / "runs" / run_id / "merge_report.md"
    if report_path.exists():
        lines += [f"报告路径：`{report_path}`", ""]
        content = report_path.read_text()
        preview = content[:800]
        if len(content) > 800:
            preview += f"\n\n_...（共 {len(content)} 字符，省略其余）_"
        lines += [f"```\n{preview}\n```", ""]
    else:
        lines += ["_merge_report.md 未找到（run 可能未完成）_", ""]

    return lines


def section_final_results(state: dict, repo: Path) -> list[str]:
    records = state.get("file_decision_records") or {}
    cfg = state.get("config") or {}
    base = state.get("merge_base_commit", "")
    fork = cfg.get("fork_ref", "test/fork")

    lines = [
        "## 最终合并结果",
        "",
        "### 文件处置汇总",
        "",
        "| 文件 | 决策 | 来源 | 置信度 |",
        "|------|------|------|--------|",
    ]

    for path, rec in sorted(records.items()):
        decision = rec.get("decision", "?")
        source = rec.get("decision_source", "?")
        conf = rec.get("confidence")
        conf_str = f"{conf:.2f}" if conf is not None else "—"
        lines.append(f"| `{path}` | {decision} | {source} | {conf_str} |")

    lines += ["", "### 代码变更（fork base → 合并结果）", ""]

    if not base:
        lines += ["_无 merge_base_commit，跳过 diff_", ""]
        return lines

    active_branch = state.get("active_branch", "")
    head_ref = active_branch or fork

    c_files = [
        path
        for path, rec in records.items()
        if rec.get("decision") not in ("TAKE_TARGET", None)
    ]

    if not c_files:
        c_files = list(records.keys())[:5]

    for path in sorted(c_files)[:6]:
        lines += [
            f"<details>",
            f"<summary><code>{path}</code></summary>",
            "",
            "```diff",
            file_diff(path, f"{base}", head_ref, repo),
            "```",
            "",
            "</details>",
            "",
        ]

    if len(records) > 6:
        lines += [f"_（共 {len(records)} 个文件，只展示前 6 个）_", ""]

    return lines


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "target_repo", nargs="?", default=".", help="Target repository path"
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    repo = Path(args.target_repo).resolve()
    cp_path, state = find_checkpoint(repo, args.run_id)

    run_id = state.get("run_id", "unknown")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    doc_lines = [
        f"# 合并校验报告 — {run_id}",
        "",
        f"> 生成时间：{now}  |  目标仓库：`{repo}`",
        "",
        "---",
        "",
    ]

    doc_lines += section_basic(state, cp_path)
    doc_lines += ["---", ""]
    doc_lines += section_branch_diff(state, repo)
    doc_lines += ["---", ""]
    doc_lines += section_initialize(state)
    doc_lines += ["---", ""]
    doc_lines += section_planning(state)
    doc_lines += ["---", ""]
    doc_lines += section_plan_review(state)
    doc_lines += ["---", ""]
    doc_lines += section_auto_merge(state)
    doc_lines += ["---", ""]
    doc_lines += section_conflict_analysis(state)
    doc_lines += ["---", ""]
    doc_lines += section_human_review(state)
    doc_lines += ["---", ""]
    doc_lines += section_judge_review(state)
    doc_lines += ["---", ""]
    doc_lines += section_report_generation(state, repo)
    doc_lines += ["---", ""]
    doc_lines += section_final_results(state, repo)

    doc = "\n".join(doc_lines)

    if args.output:
        Path(args.output).write_text(doc)
        print(f"已写入：{args.output}", file=sys.stderr)
    else:
        print(doc)


if __name__ == "__main__":
    main()
