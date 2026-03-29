import json
from pathlib import Path
from src.models.state import MergeState


def write_markdown_report(state: MergeState, output_dir: str) -> Path:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    report_path = output_path / f"merge_report_{state.run_id}.md"

    lines: list[str] = [
        f"# Merge Report — {state.run_id}",
        "",
        f"**Status**: {state.status.value if hasattr(state.status, 'value') else state.status}",
        f"**Created**: {state.created_at.isoformat()}",
        f"**Updated**: {state.updated_at.isoformat()}",
        "",
    ]

    if state.merge_plan:
        plan = state.merge_plan
        lines += [
            "## Merge Plan",
            f"- Upstream: `{plan.upstream_ref}`",
            f"- Fork: `{plan.fork_ref}`",
            f"- Merge base: `{plan.merge_base_commit}`",
            "",
            "### Risk Summary",
            f"- Total files: {plan.risk_summary.total_files}",
            f"- Auto-safe: {plan.risk_summary.auto_safe_count}",
            f"- Auto-risky: {plan.risk_summary.auto_risky_count}",
            f"- Human required: {plan.risk_summary.human_required_count}",
            f"- Estimated auto-merge rate: {plan.risk_summary.estimated_auto_merge_rate:.1%}",
            "",
        ]

    if state.file_decision_records:
        lines += ["## File Decision Records", ""]
        lines += [
            "| File | Decision | Source | Confidence |",
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
            "## Judge Verdict",
            f"- **Result**: {verdict_val}",
            f"- **Confidence**: {verdict.overall_confidence:.2f}",
            f"- **Summary**: {verdict.summary}",
            f"- Critical issues: {verdict.critical_issues_count}",
            f"- High issues: {verdict.high_issues_count}",
            "",
        ]

    if state.errors:
        lines += ["## Errors", ""]
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

    report_path = output_path / f"human_decisions_{state.run_id}.md"
    lines: list[str] = [
        f"# Human Decision Required — Run {state.run_id}",
        "",
        "The following files require human review.",
        "",
    ]

    for req_id, req in state.human_decision_requests.items():
        rec_val = (
            req.analyst_recommendation.value
            if hasattr(req.analyst_recommendation, "value")
            else req.analyst_recommendation
        )
        lines += [
            f"## {req.file_path} (priority={req.priority})",
            "",
            f"**Context**: {req.context_summary}",
            "",
            f"**Upstream changes**: {req.upstream_change_summary}",
            "",
            f"**Fork changes**: {req.fork_change_summary}",
            "",
            f"**Analyst recommendation**: {rec_val} (confidence: {req.analyst_confidence:.2f})",
            "",
            f"**Rationale**: {req.analyst_rationale}",
            "",
            "### Options",
        ]
        for opt in req.options:
            opt_dec = (
                opt.decision.value if hasattr(opt.decision, "value") else opt.decision
            )
            lines.append(f"- **{opt.option_key}** (`{opt_dec}`): {opt.description}")
            if opt.risk_warning:
                lines.append(f"  - Warning: {opt.risk_warning}")
        lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path
