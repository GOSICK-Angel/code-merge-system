from src.models.diff import FileDiff
from src.models.plan import MergePlan
from src.models.plan_judge import PlanIssue


PLANNER_SYSTEM = """You are a code merge planning expert. Your task is to analyze differences between two branches,
classify all changed files into different risk levels, and generate a phased merge plan.
Focus on: complete coverage of all files, reasonable risk estimation, identifying critical dependencies.
Output structured JSON. Risk levels: auto_safe, auto_risky, human_required, deleted_only, binary, excluded."""


def build_classification_prompt(file_diffs: list[FileDiff], project_context: str) -> str:
    file_list_lines: list[str] = []
    for fd in file_diffs:
        file_list_lines.append(
            f"- {fd.file_path} | status={fd.file_status.value} | "
            f"lines_added={fd.lines_added} | lines_deleted={fd.lines_deleted} | "
            f"conflicts={fd.conflict_count} | security_sensitive={fd.is_security_sensitive}"
        )

    file_list = "\n".join(file_list_lines)

    return f"""Analyze the following changed files and create a merge plan.

Project context:
{project_context or "No project context provided."}

Changed files ({len(file_diffs)} total):
{file_list}

Create a phased merge plan with the following structure:
1. Classify each file by risk level
2. Group files into batches by phase
3. Summarize risk distribution

Return JSON with this structure:
{{
  "phases": [
    {{
      "batch_id": "unique-id",
      "phase": "auto_merge",
      "file_paths": ["path/to/file.py"],
      "risk_level": "auto_safe",
      "can_parallelize": true
    }}
  ],
  "risk_summary": {{
    "total_files": {len(file_diffs)},
    "auto_safe_count": 0,
    "auto_risky_count": 0,
    "human_required_count": 0,
    "deleted_only_count": 0,
    "binary_count": 0,
    "excluded_count": 0,
    "estimated_auto_merge_rate": 0.0,
    "top_risk_files": []
  }},
  "project_context_summary": "Brief project summary",
  "special_instructions": []
}}"""


def build_context_summary_prompt(repo_structure: str) -> str:
    return f"""You are a senior code reviewer.

Below is a summary of key files in a software project and change statistics between two branches.
Based on this information, summarize in 300 words or less:
1. The main functionality and technology stack of the project
2. The main customization direction of the fork branch relative to upstream
3. Modules or technical points that need special attention during merging

---
Project file summary:
{repo_structure}

Please answer in English."""


def build_revision_prompt(original_plan: MergePlan, judge_issues: list[PlanIssue]) -> str:
    issues_text = "\n".join(
        f"- File: {issue.file_path}\n"
        f"  Current: {issue.current_classification.value}\n"
        f"  Suggested: {issue.suggested_classification.value}\n"
        f"  Reason: {issue.reason}\n"
        f"  Type: {issue.issue_type}"
        for issue in judge_issues
    )

    return f"""The plan reviewer has identified specific issues with the merge plan that need correction.

Original plan summary:
- Total files: {original_plan.risk_summary.total_files}
- Auto-safe: {original_plan.risk_summary.auto_safe_count}
- Auto-risky: {original_plan.risk_summary.auto_risky_count}
- Human required: {original_plan.risk_summary.human_required_count}

Issues found by reviewer:
{issues_text}

Please revise only the files listed above. Do not change other file classifications.
Return the complete revised plan in the same JSON format as the original plan."""
