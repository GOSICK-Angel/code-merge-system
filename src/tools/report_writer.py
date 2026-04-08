import json
from functools import partial
from pathlib import Path
from src.models.state import MergeState


_I18N: dict[str, dict[str, str]] = {
    "en": {
        "merge_report": "Merge Report",
        "status": "Status",
        "created": "Created",
        "updated": "Updated",
        "merge_plan": "Merge Plan",
        "upstream": "Upstream",
        "fork": "Fork",
        "merge_base": "Merge base",
        "risk_summary": "Risk Summary",
        "total_files": "Total files",
        "auto_safe": "Auto-safe",
        "auto_risky": "Auto-risky",
        "human_required": "Human required",
        "estimated_auto_merge_rate": "Estimated auto-merge rate",
        "file_decision_records": "File Decision Records",
        "col_file": "File",
        "col_decision": "Decision",
        "col_source": "Source",
        "col_confidence": "Confidence",
        "judge_verdict": "Judge Verdict",
        "result": "Result",
        "confidence": "Confidence",
        "summary": "Summary",
        "critical_issues": "Critical issues",
        "high_issues": "High issues",
        "errors": "Errors",
        "plan_review_report": "Plan Review Report",
        "final_plan_summary": "Final Plan Summary",
        "special_instructions": "Special Instructions",
        "phase_batches": "Phase Batches",
        "batch": "Batch",
        "files": "Files",
        "planner_judge_log": "Planner / Judge Interaction Log",
        "no_review_rounds": "No review rounds recorded.",
        "round": "Round",
        "verdict": "Verdict",
        "issues": "Issues",
        "timestamp": "Timestamp",
        "issue_details": "Issue Details",
        "planner_revision": "Planner Revision",
        "human_review": "Human Review",
        "awaiting_human": "Awaiting human review.",
        "decision": "Decision",
        "reviewer": "Reviewer",
        "notes": "Notes",
        "decided_at": "Decided at",
        "human_decision_required": "Human Decision Required",
        "files_require_review": "The following files require human review.",
        "context": "Context",
        "upstream_changes": "Upstream changes",
        "fork_changes": "Fork changes",
        "analyst_recommendation": "Analyst recommendation",
        "rationale": "Rationale",
        "options": "Options",
        "warning": "Warning",
        "priority": "priority",
        "living_plan": "Living Merge Plan",
        "execution_log": "Execution Log",
        "judge_review_log": "Judge Review Log",
        "gate_check_log": "Gate Check Log",
        "open_issues": "Open Issues",
        "phase": "Phase",
        "processed": "Processed",
        "skipped": "Skipped",
        "commit": "Commit",
        "veto": "VETO",
        "repair": "Repair",
        "gate_name": "Gate",
        "passed": "Passed",
        "failed_item": "Failed",
        "severity": "Severity",
        "assigned_to": "Assigned to",
        "resolved": "Resolved",
        "category_summary": "Category Summary",
        "layer_summary": "Layer Summary",
        "todo_merge_count": "TODO [merge] count",
    },
    "zh": {
        "merge_report": "合并报告",
        "status": "状态",
        "created": "创建时间",
        "updated": "更新时间",
        "merge_plan": "合并计划",
        "upstream": "上游分支",
        "fork": "下游分支",
        "merge_base": "合并基准",
        "risk_summary": "风险摘要",
        "total_files": "文件总数",
        "auto_safe": "自动安全",
        "auto_risky": "自动风险",
        "human_required": "需人工审核",
        "estimated_auto_merge_rate": "预计自动合并率",
        "file_decision_records": "文件决策记录",
        "col_file": "文件",
        "col_decision": "决策",
        "col_source": "来源",
        "col_confidence": "置信度",
        "judge_verdict": "审核裁决",
        "result": "结果",
        "confidence": "置信度",
        "summary": "摘要",
        "critical_issues": "严重问题",
        "high_issues": "高优问题",
        "errors": "错误",
        "plan_review_report": "计划审查报告",
        "final_plan_summary": "最终计划摘要",
        "special_instructions": "特殊说明",
        "phase_batches": "阶段批次",
        "batch": "批次",
        "files": "文件",
        "planner_judge_log": "规划器 / 审查器交互日志",
        "no_review_rounds": "暂无审查轮次记录。",
        "round": "轮次",
        "verdict": "裁决",
        "issues": "问题",
        "timestamp": "时间戳",
        "issue_details": "问题详情",
        "planner_revision": "规划器修订",
        "human_review": "人工审查",
        "awaiting_human": "等待人工审查。",
        "decision": "决策",
        "reviewer": "审查者",
        "notes": "备注",
        "decided_at": "决策时间",
        "human_decision_required": "需要人工决策",
        "files_require_review": "以下文件需要人工审查。",
        "context": "上下文",
        "upstream_changes": "上游变更",
        "fork_changes": "下游变更",
        "analyst_recommendation": "分析师建议",
        "rationale": "依据",
        "options": "选项",
        "warning": "警告",
        "priority": "优先级",
        "living_plan": "实时合并计划",
        "execution_log": "执行日志",
        "judge_review_log": "审查日志",
        "gate_check_log": "门禁检查日志",
        "open_issues": "待解决问题",
        "phase": "阶段",
        "processed": "已处理",
        "skipped": "已跳过",
        "commit": "提交",
        "veto": "否决",
        "repair": "修复",
        "gate_name": "门禁",
        "passed": "通过",
        "failed_item": "失败",
        "severity": "严重性",
        "assigned_to": "分配至",
        "resolved": "已解决",
        "category_summary": "分类摘要",
        "layer_summary": "层次摘要",
        "todo_merge_count": "TODO [merge] 计数",
    },
}


def _t(language: str, key: str) -> str:
    return _I18N.get(language, _I18N["en"]).get(key, _I18N["en"].get(key, key))


def write_markdown_report(state: MergeState, output_dir: str) -> Path:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    lang = state.config.output.language
    t = partial(_t, lang)

    report_path = output_path / f"merge_report_{state.run_id}.md"

    lines: list[str] = [
        f"# {t('merge_report')} — {state.run_id}",
        "",
        f"**{t('status')}**: {state.status.value if hasattr(state.status, 'value') else state.status}",
        f"**{t('created')}**: {state.created_at.isoformat()}",
        f"**{t('updated')}**: {state.updated_at.isoformat()}",
        "",
    ]

    if state.merge_plan:
        plan = state.merge_plan
        lines += [
            f"## {t('merge_plan')}",
            f"- {t('upstream')}: `{plan.upstream_ref}`",
            f"- {t('fork')}: `{plan.fork_ref}`",
            f"- {t('merge_base')}: `{plan.merge_base_commit}`",
            "",
            f"### {t('risk_summary')}",
            f"- {t('total_files')}: {plan.risk_summary.total_files}",
            f"- {t('auto_safe')}: {plan.risk_summary.auto_safe_count}",
            f"- {t('auto_risky')}: {plan.risk_summary.auto_risky_count}",
            f"- {t('human_required')}: {plan.risk_summary.human_required_count}",
            f"- {t('estimated_auto_merge_rate')}: {plan.risk_summary.estimated_auto_merge_rate:.1%}",
            "",
        ]

    if state.file_decision_records:
        lines += [f"## {t('file_decision_records')}", ""]
        lines += [
            f"| {t('col_file')} | {t('col_decision')} | {t('col_source')} | {t('col_confidence')} |",
            "|------|----------|--------|------------|",
        ]
        for fp, rec in state.file_decision_records.items():
            decision_val = (
                rec.decision.value if hasattr(rec.decision, "value") else rec.decision
            )
            source_val = (
                rec.decision_source.value
                if hasattr(rec.decision_source, "value")
                else rec.decision_source
            )
            conf = f"{rec.confidence:.2f}" if rec.confidence is not None else "N/A"
            lines.append(f"| `{fp}` | {decision_val} | {source_val} | {conf} |")
        lines.append("")

    if state.judge_verdict:
        verdict = state.judge_verdict
        verdict_val = (
            verdict.verdict.value
            if hasattr(verdict.verdict, "value")
            else verdict.verdict
        )
        lines += [
            f"## {t('judge_verdict')}",
            f"- **{t('result')}**: {verdict_val}",
            f"- **{t('confidence')}**: {verdict.overall_confidence:.2f}",
            f"- **{t('summary')}**: {verdict.summary}",
            f"- {t('critical_issues')}: {verdict.critical_issues_count}",
            f"- {t('high_issues')}: {verdict.high_issues_count}",
            "",
        ]

    if state.errors:
        lines += [f"## {t('errors')}", ""]
        for err in state.errors:
            lines.append(f"- `{err.get('phase', '?')}`: {err.get('message', '')}")
        lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def write_json_report(state: MergeState, output_dir: str) -> Path:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    report_path = output_path / f"merge_report_{state.run_id}.json"

    data = state.model_dump(mode="json")
    report_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return report_path


def write_human_decision_report(
    state: MergeState,
    output_dir: str,
) -> Path:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    lang = state.config.output.language
    t = partial(_t, lang)

    report_path = output_path / f"human_decisions_{state.run_id}.md"
    lines: list[str] = [
        f"# {t('human_decision_required')} — Run {state.run_id}",
        "",
        t("files_require_review"),
        "",
    ]

    for req_id, req in state.human_decision_requests.items():
        rec_val = (
            req.analyst_recommendation.value
            if hasattr(req.analyst_recommendation, "value")
            else req.analyst_recommendation
        )
        lines += [
            f"## {req.file_path} ({t('priority')}={req.priority})",
            "",
            f"**{t('context')}**: {req.context_summary}",
            "",
            f"**{t('upstream_changes')}**: {req.upstream_change_summary}",
            "",
            f"**{t('fork_changes')}**: {req.fork_change_summary}",
            "",
            f"**{t('analyst_recommendation')}**: {rec_val} ({t('confidence')}: {req.analyst_confidence:.2f})",
            "",
            f"**{t('rationale')}**: {req.analyst_rationale}",
            "",
            f"### {t('options')}",
        ]
        for opt in req.options:
            opt_dec = (
                opt.decision.value if hasattr(opt.decision, "value") else opt.decision
            )
            lines.append(f"- **{opt.option_key}** (`{opt_dec}`): {opt.description}")
            if opt.risk_warning:
                lines.append(f"  - {t('warning')}: {opt.risk_warning}")
        lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def write_plan_review_report(state: MergeState, output_dir: str) -> Path:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    lang = state.config.output.language
    t = partial(_t, lang)

    report_path = output_path / f"plan_review_{state.run_id}.md"

    lines: list[str] = [
        f"# {t('plan_review_report')} — {state.run_id}",
        "",
        f"**{t('created')}**: {state.created_at.isoformat()}",
        "",
    ]

    if state.merge_plan:
        plan = state.merge_plan
        lines += [
            f"## {t('final_plan_summary')}",
            f"- {t('upstream')}: `{plan.upstream_ref}`",
            f"- {t('fork')}: `{plan.fork_ref}`",
            f"- {t('merge_base')}: `{plan.merge_base_commit}`",
            f"- {t('total_files')}: {plan.risk_summary.total_files}",
            f"- {t('auto_safe')}: {plan.risk_summary.auto_safe_count}",
            f"- {t('auto_risky')}: {plan.risk_summary.auto_risky_count}",
            f"- {t('human_required')}: {plan.risk_summary.human_required_count}",
            f"- {t('estimated_auto_merge_rate')}: {plan.risk_summary.estimated_auto_merge_rate:.1%}",
            "",
        ]

        if plan.special_instructions:
            lines.append(f"### {t('special_instructions')}")
            for inst in plan.special_instructions:
                lines.append(f"- {inst}")
            lines.append("")

        lines.append(f"### {t('phase_batches')}")
        for batch in plan.phases:
            risk_val = (
                batch.risk_level.value
                if hasattr(batch.risk_level, "value")
                else batch.risk_level
            )
            lines += [
                f"#### {t('batch')} `{batch.batch_id}` — {risk_val}",
                f"- {t('files')} ({len(batch.file_paths)}):",
            ]
            for fp in batch.file_paths:
                lines.append(f"  - `{fp}`")
            lines.append("")

    lines += [
        f"## {t('planner_judge_log')}",
        "",
    ]

    if not state.plan_review_log:
        lines.append(f"_{t('no_review_rounds')}_")
        lines.append("")
    else:
        for rnd in state.plan_review_log:
            result_val = (
                rnd.verdict_result.value
                if hasattr(rnd.verdict_result, "value")
                else rnd.verdict_result
            )
            lines += [
                f"### {t('round')} {rnd.round_number}",
                f"- **{t('verdict')}**: {result_val}",
                f"- **{t('summary')}**: {rnd.verdict_summary}",
                f"- **{t('issues')}**: {rnd.issues_count}",
                f"- **{t('timestamp')}**: {rnd.timestamp.isoformat()}",
            ]
            if rnd.issues_detail:
                lines.append(f"- **{t('issue_details')}**:")
                for issue in rnd.issues_detail:
                    lines.append(
                        f"  - `{issue.get('file_path', '?')}`: "
                        f"{issue.get('reason', '')} "
                        f"({issue.get('current', '?')} → {issue.get('suggested', '?')})"
                    )
            if rnd.planner_revision_summary:
                lines.append(
                    f"- **{t('planner_revision')}**: {rnd.planner_revision_summary}"
                )
            lines.append("")

    lines += [
        f"## {t('human_review')}",
        "",
    ]

    if state.plan_human_review is None:
        lines.append(f"_{t('awaiting_human')}_")
        lines.append("")
    else:
        review = state.plan_human_review
        decision_val = (
            review.decision.value
            if hasattr(review.decision, "value")
            else review.decision
        )
        lines += [
            f"- **{t('decision')}**: {decision_val}",
            f"- **{t('reviewer')}**: {review.reviewer_name or 'N/A'}",
            f"- **{t('notes')}**: {review.reviewer_notes or 'N/A'}",
            f"- **{t('decided_at')}**: {review.decided_at.isoformat()}",
            "",
        ]

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def write_living_plan_report(state: MergeState, output_dir: str) -> Path:
    from src.models.plan import MergePlanLive

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    lang = state.config.output.language
    t = partial(_t, lang)

    report_path = output_path / f"living_plan_{state.run_id}.md"
    lines: list[str] = [
        f"# {t('living_plan')} — {state.run_id}",
        "",
        f"**{t('status')}**: {state.status.value if hasattr(state.status, 'value') else state.status}",
        f"**{t('created')}**: {state.created_at.isoformat()}",
        f"**{t('updated')}**: {state.updated_at.isoformat()}",
        "",
    ]

    if state.merge_plan:
        plan = state.merge_plan
        lines += [
            f"## {t('merge_plan')}",
            f"- {t('upstream')}: `{plan.upstream_ref}`",
            f"- {t('fork')}: `{plan.fork_ref}`",
            f"- {t('merge_base')}: `{plan.merge_base_commit}`",
            "",
            f"### {t('risk_summary')}",
            f"| {t('total_files')} | {t('auto_safe')} | {t('auto_risky')} | {t('human_required')} | {t('estimated_auto_merge_rate')} |",
            "|---|---|---|---|---|",
            (
                f"| {plan.risk_summary.total_files} "
                f"| {plan.risk_summary.auto_safe_count} "
                f"| {plan.risk_summary.auto_risky_count} "
                f"| {plan.risk_summary.human_required_count} "
                f"| {plan.risk_summary.estimated_auto_merge_rate:.1%} |"
            ),
            "",
        ]

        if plan.category_summary:
            cs = plan.category_summary
            lines += [
                f"### {t('category_summary')}",
                "| A | B | C | D-missing | D-extra | E |",
                "|---|---|---|---|---|---|",
                (
                    f"| {cs.a_unchanged} | {cs.b_upstream_only} | {cs.c_both_changed} "
                    f"| {cs.d_missing} | {cs.d_extra} | {cs.e_current_only} |"
                ),
                "",
            ]

        if plan.layers:
            lines += [f"### {t('layer_summary')}", ""]
            for layer in plan.layers:
                deps = ", ".join(str(d) for d in layer.depends_on) or "none"
                lines.append(
                    f"- **Layer {layer.layer_id}** ({layer.name}): "
                    f"depends_on=[{deps}], gates={len(layer.gate_commands)}"
                )
            lines.append("")

        lines.append(f"### {t('phase_batches')}")
        for batch in plan.phases:
            risk_val = (
                batch.risk_level.value
                if hasattr(batch.risk_level, "value")
                else batch.risk_level
            )
            cat_val = ""
            if batch.change_category:
                cat_val = (
                    f" [{batch.change_category.value}]"
                    if hasattr(batch.change_category, "value")
                    else f" [{batch.change_category}]"
                )
            layer_val = f" L{batch.layer_id}" if batch.layer_id is not None else ""
            lines += [
                f"#### {t('batch')} `{batch.batch_id[:8]}…`{layer_val}{cat_val} — {risk_val}",
                f"- {t('files')} ({len(batch.file_paths)}):",
            ]
            total_batch_files = len(batch.file_paths)
            for fp in batch.file_paths[:20]:
                lines.append(f"  - `{fp}`")
            if total_batch_files > 20:
                lines.append(
                    f"  - ... +{total_batch_files - 20} more "
                    f"({total_batch_files} total)"
                )
            lines.append("")

    live_plan: MergePlanLive | None = None
    if isinstance(state.merge_plan, MergePlanLive):
        live_plan = state.merge_plan

    if live_plan and live_plan.execution_records:
        lines += [f"## {t('execution_log')}", ""]
        lines += [
            f"| {t('phase')} | {t('timestamp')} | {t('processed')} | {t('skipped')} | {t('commit')} |",
            "|---|---|---|---|---|",
        ]
        for rec in live_plan.execution_records:
            completed = rec.completed_at.isoformat() if rec.completed_at else "running"
            lines.append(
                f"| {rec.phase_id} | {completed} "
                f"| {rec.files_processed} | {rec.files_skipped} "
                f"| `{rec.commit_hash or 'N/A'}` |"
            )
        lines.append("")

    if live_plan and live_plan.judge_records:
        lines += [f"## {t('judge_review_log')}", ""]
        lines += [
            f"| {t('phase')} | {t('round')} | {t('verdict')} | {t('issues')} | {t('veto')} | {t('repair')} |",
            "|---|---|---|---|---|---|",
        ]
        for jrec in live_plan.judge_records:
            lines.append(
                f"| {jrec.phase_id} | {jrec.round_number} "
                f"| {jrec.verdict} | {len(jrec.issues)} "
                f"| {'YES' if jrec.veto_triggered else 'no'} "
                f"| {len(jrec.repair_instructions)} |"
            )
        lines.append("")

    if live_plan and live_plan.gate_records:
        lines += [f"## {t('gate_check_log')}", ""]
        for grec in live_plan.gate_records:
            status = t("passed") if grec.all_passed else t("failed_item")
            lines.append(f"### {grec.phase_id} — {status}")
            if grec.gate_results:
                lines += [
                    f"| {t('gate_name')} | {t('passed')} | exit |",
                    "|---|---|---|",
                ]
                for gr in grec.gate_results:
                    lines.append(
                        f"| {gr.get('gate_name', '?')} "
                        f"| {'YES' if gr.get('passed') else 'NO'} "
                        f"| {gr.get('exit_code', '?')} |"
                    )
            lines.append("")

    if live_plan:
        lines.append(
            f"**{t('todo_merge_count')}**: "
            f"{live_plan.todo_merge_count} / {live_plan.todo_merge_limit}"
        )
        lines.append("")

    if live_plan and live_plan.open_issues:
        lines += [f"## {t('open_issues')}", ""]
        lines += [
            f"| # | {t('phase')} | {t('severity')} | Description "
            f"| {t('assigned_to')} | {t('resolved')} |",
            "|---|---|---|---|---|---|",
        ]
        for oi in live_plan.open_issues:
            lines.append(
                f"| {oi.issue_id[:8]} | {oi.phase_id} | {oi.severity} "
                f"| {oi.description} | {oi.assigned_to_phase or 'N/A'} "
                f"| {'YES' if oi.resolved else 'no'} |"
            )
        lines.append("")

    if state.errors:
        lines += [f"## {t('errors')}", ""]
        for err in state.errors:
            lines.append(f"- `{err.get('phase', '?')}`: {err.get('message', '')}")
        lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path
