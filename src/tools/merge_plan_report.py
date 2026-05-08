"""Generate a comprehensive merge plan report for human review."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from src.cli.paths import get_plans_dir
from src.models.diff import FileDiff, RiskLevel
from src.models.state import MergeState

logger = logging.getLogger(__name__)


def write_merge_plan_report(state: MergeState) -> Path:
    """Write a detailed merge plan Markdown report.

    Path is resolved by ``get_plans_dir`` — ``.merge/plans/`` in production
    mode (running against any external repo) or ``MERGE_RECORD/`` in dev
    mode (running against the CodeMergeSystem source tree itself).

    Returns the path of the generated file.
    """
    record_dir = get_plans_dir(state.config.repo_path)
    record_dir.mkdir(parents=True, exist_ok=True)

    upstream = state.config.upstream_ref.replace("/", "_")
    filename = f"MERGE_PLAN_{upstream}_{state.run_id[:8]}.md"
    report_path = record_dir / filename

    if report_path.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"MERGE_PLAN_{upstream}_{state.run_id[:8]}_{ts}.md"
        report_path = record_dir / filename

    lang = state.config.output.language
    lines = _build_report(state, lang)
    report_path.write_text("\n".join(lines), encoding="utf-8")

    logger.info("Merge plan report written to %s", report_path)
    return report_path


def _build_report(state: MergeState, lang: str) -> list[str]:
    zh = lang == "zh"
    plan = state.merge_plan
    file_diffs = state.file_diffs

    lines: list[str] = []

    _header(lines, state, zh)
    _migration_section(lines, state, zh)
    _classification_summary(lines, state, zh)
    _directory_matrix(lines, state, zh)
    _risk_files(lines, file_diffs, zh)
    _batch_plan(lines, plan, zh)
    _layer_dependencies(lines, plan, zh)
    _planner_judge_log(lines, state, zh)
    _forks_profile_drift_section(lines, state, zh)

    return lines


def _header(lines: list[str], state: MergeState, zh: bool) -> None:
    plan = state.merge_plan
    title = "合并计划" if zh else "Merge Plan"
    lines += [
        f"# {title}: {state.config.upstream_ref} → {state.config.fork_ref}",
        "",
        f"{'生成时间' if zh else 'Generated'}: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Merge base: `{state.merge_base_commit[:12]}`",
        f"Run ID: `{state.run_id}`",
    ]

    if state.config.project_context:
        ctx_label = "项目背景" if zh else "Project Context"
        lines += [
            "",
            f"**{ctx_label}**: {state.config.project_context.strip()}",
        ]

    if plan:
        rs = plan.risk_summary
        total = rs.total_files
        rate_label = "自动合并率" if zh else "Auto-merge rate"
        lines += [
            "",
            f"| {'指标' if zh else 'Metric'} | {'值' if zh else 'Value'} |",
            "|------|------|",
            f"| {'总文件数' if zh else 'Total files'} | {total} |",
            f"| {'安全自动合并' if zh else 'Auto-safe'} | {rs.auto_safe_count} |",
            f"| {'风险自动合并' if zh else 'Auto-risky'} | {rs.auto_risky_count} |",
            f"| {'需人工审查' if zh else 'Human required'} | {rs.human_required_count} |",
            f"| {rate_label} | {rs.estimated_auto_merge_rate:.1%} |",
        ]

    lines += ["", "---", ""]


def _migration_section(lines: list[str], state: MergeState, zh: bool) -> None:
    info = state.migration_info
    if info is None or not info.detected:
        return

    title = "迁移检测" if zh else "Migration Detection"
    lines += [
        f"## {title}",
        "",
    ]

    if zh:
        lines += [
            f"- **检测结果**: 检测到代码迁移 (置信度 {info.confidence:.0%})",
            f"- **同步文件数**: {info.synced_file_count} / {info.upstream_changed_file_count}"
            f" ({info.sync_ratio:.0%})",
            f"- **有效合并基准**: `{info.effective_merge_base[:12]}`",
            f"- **Git 合并基准**: `{info.git_merge_base[:12]}`",
            f"- **跳过的提交数**: {info.skipped_commit_count}",
        ]
    else:
        lines += [
            f"- **Detection**: Migration detected (confidence {info.confidence:.0%})",
            f"- **Synced files**: {info.synced_file_count} / {info.upstream_changed_file_count}"
            f" ({info.sync_ratio:.0%})",
            f"- **Effective merge-base**: `{info.effective_merge_base[:12]}`",
            f"- **Git merge-base**: `{info.git_merge_base[:12]}`",
            f"- **Skipped commits**: {info.skipped_commit_count}",
        ]

    if info.last_synced_commit:
        label = "最后同步提交" if zh else "Last synced commit"
        lines.append(f"- **{label}**: `{info.last_synced_commit[:12]}`")

    if info.first_unsynced_commit:
        label = "首个未同步提交" if zh else "First unsynced commit"
        lines.append(f"- **{label}**: `{info.first_unsynced_commit[:12]}`")

    override_label = "手动覆盖" if zh else "Override"
    override_hint = (
        "如果检测不准确，可在配置中设置"
        if zh
        else "If detection is inaccurate, set in config"
    )
    lines += [
        "",
        f"> **{override_label}**: {override_hint}:",
        "> ```yaml",
        "> migration:",
        f'>   merge_base_override: "{info.effective_merge_base}"',
        "> ```",
        "",
        "---",
        "",
    ]


def _classification_summary(lines: list[str], state: MergeState, zh: bool) -> None:
    cats = state.file_categories
    if not cats:
        return

    counts: dict[str, int] = defaultdict(int)
    for cat in cats.values():
        counts[cat.value] += 1

    title = "文件三路分类统计" if zh else "Three-way Classification Summary"
    lines += [
        f"## {title}",
        "",
        f"| {'分类' if zh else 'Category'} | {'数量' if zh else 'Count'} | {'说明' if zh else 'Description'} |",
        "|------|------|------|",
    ]

    desc_map_zh = {
        "unchanged": "HEAD 与 upstream 相同，无需处理",
        "upstream_only": "仅 upstream 修改，可直接采纳",
        "both_changed": "两边都改了，需三方合并",
        "upstream_new": "upstream 新增文件",
        "current_only": "current 独有文件，保留",
        "current_only_change": "仅 current 修改，保留",
    }
    desc_map_en = {
        "unchanged": "Same in HEAD and upstream, skip",
        "upstream_only": "Only upstream changed, take upstream",
        "both_changed": "Both changed, three-way merge needed",
        "upstream_new": "New file from upstream",
        "current_only": "Current-only file, keep",
        "current_only_change": "Only current changed, keep",
    }
    label_map = {
        "unchanged": "A",
        "upstream_only": "B",
        "both_changed": "C",
        "upstream_new": "D-missing",
        "current_only": "D-extra",
        "current_only_change": "E",
    }
    desc_map = desc_map_zh if zh else desc_map_en

    for key in [
        "unchanged",
        "upstream_only",
        "both_changed",
        "upstream_new",
        "current_only",
        "current_only_change",
    ]:
        label = label_map.get(key, key)
        desc = desc_map.get(key, "")
        lines.append(f"| {label} ({key}) | {counts.get(key, 0)} | {desc} |")

    lines += ["", "---", ""]


def _directory_matrix(lines: list[str], state: MergeState, zh: bool) -> None:
    cats = state.file_categories
    if not cats:
        return

    dir_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for fp, cat in cats.items():
        parts = fp.split("/")
        dir_key = "/".join(parts[:2]) if len(parts) > 1 else parts[0]
        dir_counts[dir_key][cat.value] += 1

    actionable_dirs = {
        d: c
        for d, c in dir_counts.items()
        if c.get("upstream_only", 0)
        + c.get("both_changed", 0)
        + c.get("upstream_new", 0)
        > 0
    }

    if not actionable_dirs:
        return

    title = "按目录分类矩阵" if zh else "Directory Classification Matrix"
    lines += [
        f"## {title}",
        "",
        f"| {'目录' if zh else 'Directory'} | B | C | D-missing | {'合计' if zh else 'Total'} |",
        "|------|---|---|-----------|-------|",
    ]

    for d in sorted(actionable_dirs.keys()):
        c = actionable_dirs[d]
        b = c.get("upstream_only", 0)
        cc = c.get("both_changed", 0)
        dm = c.get("upstream_new", 0)
        total = b + cc + dm
        lines.append(f"| {d} | {b} | {cc} | {dm} | {total} |")

    lines += ["", "---", ""]


def _risk_files(lines: list[str], file_diffs: list[FileDiff], zh: bool) -> None:
    risky = [
        fd
        for fd in file_diffs
        if fd.risk_level in (RiskLevel.AUTO_RISKY, RiskLevel.HUMAN_REQUIRED)
    ]

    if not risky:
        return

    title = "高风险文件清单" if zh else "High-risk Files"
    lines += [
        f"## {title}",
        "",
        f"| {'文件' if zh else 'File'} | {'风险等级' if zh else 'Risk'} "
        f"| {'风险分' if zh else 'Score'} | {'安全敏感' if zh else 'Security'} "
        f"| {'分类' if zh else 'Category'} |",
        "|------|------|-------|------|------|",
    ]

    risky.sort(key=lambda fd: fd.risk_score, reverse=True)
    for fd in risky:
        risk = fd.risk_level.value if hasattr(fd.risk_level, "value") else fd.risk_level
        cat = (
            fd.change_category.value
            if fd.change_category and hasattr(fd.change_category, "value")
            else str(fd.change_category or "")
        )
        sec = "⚠️" if fd.is_security_sensitive else ""
        lines.append(
            f"| `{fd.file_path}` | {risk} | {fd.risk_score:.2f} | {sec} | {cat} |"
        )

    lines += ["", "---", ""]


def _batch_plan(lines: list[str], plan: Any, zh: bool) -> None:
    if not plan:
        return

    title = "合并批次计划" if zh else "Merge Batch Plan"
    lines += [f"## {title}", ""]

    for batch in plan.phases:
        risk = (
            batch.risk_level.value
            if hasattr(batch.risk_level, "value")
            else batch.risk_level
        )
        cat = ""
        if batch.change_category:
            cat_val = (
                batch.change_category.value
                if hasattr(batch.change_category, "value")
                else str(batch.change_category)
            )
            cat = f" [{cat_val}]"

        batch_title = "批次" if zh else "Batch"
        files_label = "文件" if zh else "files"
        lines += [
            f"### {batch_title} `{batch.batch_id}` — {risk}{cat}",
            f"Layer: {batch.layer_id} | {len(batch.file_paths)} {files_label}",
            "",
        ]

        for fp in batch.file_paths:
            lines.append(f"- `{fp}`")
        lines.append("")

    lines += ["---", ""]


def _layer_dependencies(lines: list[str], plan: Any, zh: bool) -> None:
    if not plan or not plan.layers:
        return

    title = "层级依赖关系" if zh else "Layer Dependencies"
    lines += [f"## {title}", ""]

    for layer in plan.layers:
        deps = (
            ", ".join(str(d) for d in layer.depends_on)
            if layer.depends_on
            else ("无" if zh else "none")
        )
        lines.append(
            f"- **[{layer.layer_id}] {layer.name}**: {layer.description} "
            f"({'依赖' if zh else 'depends on'}: {deps})"
        )

    lines += ["", "---", ""]


def _planner_judge_log(lines: list[str], state: MergeState, zh: bool) -> None:
    title = "Planner-Judge 审查记录" if zh else "Planner-Judge Review Log"
    lines += [f"## {title}", ""]

    if not state.plan_review_log:
        no_record = "暂无审查记录。" if zh else "No review rounds recorded."
        lines += [f"_{no_record}_", ""]
        return

    for rnd in state.plan_review_log:
        result = (
            rnd.verdict_result.value
            if hasattr(rnd.verdict_result, "value")
            else rnd.verdict_result
        )
        round_label = "轮次" if zh else "Round"
        lines += [
            f"### {round_label} {rnd.round_number}",
            f"- **{'结论' if zh else 'Verdict'}**: {result}",
            f"- **{'摘要' if zh else 'Summary'}**: {rnd.verdict_summary}",
            f"- **{'问题数' if zh else 'Issues'}**: {rnd.issues_count}",
        ]
        if rnd.issues_detail:
            detail_label = "问题详情" if zh else "Issue Details"
            lines.append(f"- **{detail_label}**:")
            for issue in rnd.issues_detail:
                lines.append(
                    f"  - `{issue.get('file_path', '?')}`: "
                    f"{issue.get('reason', '')} "
                    f"({issue.get('current', '?')} → {issue.get('suggested', '?')})"
                )
        lines.append("")


def _forks_profile_drift_section(lines: list[str], state: MergeState, zh: bool) -> None:
    """Append a forks-profile drift appendix when initialize phase populated it.

    No-op when ``state.forks_profile_drift is None`` (typical: yaml absent
    or drift below the notify threshold). Reviewers see this section
    alongside the plan they're approving so stale yaml entries surface
    at the same moment they have context to act on them.
    """
    drift = state.forks_profile_drift
    if not drift:
        return

    title = "Forks-profile 漂移" if zh else "Forks-profile drift"
    intro = (
        "yaml 与启发式重新检测的结果不一致；用 `merge forks-profile diff` "
        "复现并按需手动修补。"
        if zh
        else "The checked-in yaml diverges from a fresh heuristic draft. "
        "Run `merge forks-profile diff` to reproduce and patch by hand."
    )
    lines += [
        f"## {title}",
        "",
        f"_{intro}_",
        "",
        "```",
        drift.rstrip(),
        "```",
        "",
    ]
