from src.models.plan import MergePlan
from src.models.diff import FileDiff
from src.models.plan_judge import PlanIssue


PLANNER_JUDGE_SYSTEM = """You are an independent reviewer of code merge plans. Your task is to find
risks that may be underestimated in the plan, incorrect file classifications, missing security-sensitive files,
and batch granularity issues.
You do not know the Planner's reasoning process; you only see the final plan and the raw diff, and draw independent conclusions.
When you find issues, you must point out specific file paths and specific reasons. Vague descriptions are not allowed.
Be critical and thorough."""


def build_plan_review_prompt(plan: MergePlan, file_diffs: list[FileDiff]) -> str:
    phases_summary = "\n".join(
        f"  Phase {batch.phase.value}: {len(batch.file_paths)} files ({batch.risk_level.value})"
        for batch in plan.phases
    )

    diff_summary_lines = [
        f"- {fd.file_path}: {fd.file_status.value}, "
        f"lines_added={fd.lines_added}, lines_deleted={fd.lines_deleted}, "
        f"conflicts={fd.conflict_count}, security={fd.is_security_sensitive}"
        for fd in file_diffs
    ]
    diff_summary = "\n".join(diff_summary_lines)

    return f"""Review the following merge plan for quality and correctness.

## Merge Plan Summary
- Upstream: {plan.upstream_ref}
- Fork: {plan.fork_ref}
- Total files: {plan.risk_summary.total_files}
- Auto-safe: {plan.risk_summary.auto_safe_count}
- Auto-risky: {plan.risk_summary.auto_risky_count}
- Human required: {plan.risk_summary.human_required_count}

## Phase Breakdown
{phases_summary}

## All File Diffs
{diff_summary}

## Your Review Tasks
1. Check if any security-sensitive files are incorrectly classified as auto_safe
2. Check if high-conflict files are correctly classified
3. Check if any deleted files should require human review
4. Check if batch granularity is appropriate

Return JSON with:
{{
  "result": "approved" | "revision_needed" | "critical_replan",
  "issues": [
    {{
      "file_path": "path/to/file",
      "current_classification": "auto_safe",
      "suggested_classification": "human_required",
      "reason": "Specific reason why classification is wrong",
      "issue_type": "risk_underestimated | wrong_batch | missing_dependency | security_missed"
    }}
  ],
  "approved_files_count": 0,
  "flagged_files_count": 0,
  "summary": "Overall assessment"
}}"""


def build_issue_report_prompt(issues: list[PlanIssue]) -> str:
    issues_text = "\n".join(
        f"- {issue.file_path}: {issue.reason} (type={issue.issue_type})"
        for issue in issues
    )
    return f"""The following plan issues have been identified:

{issues_text}

Provide a concise summary of the impact of these issues on the merge process."""
