from src.models.diff import FileDiff
from src.models.plan import MergePlan
from src.models.plan_judge import PlanIssue


PLANNER_SYSTEM_TEMPLATE = """You are a code merge planning expert. Your task is to analyze differences between two branches,
classify all changed files into different risk levels, and generate a phased merge plan.
Focus on: complete coverage of all files, reasonable risk estimation, identifying critical dependencies.
Output structured JSON. Risk levels: auto_safe, auto_risky, human_required, deleted_only, binary, excluded.
{lang_instruction}"""


def get_planner_system(language: str = "en") -> str:
    if language == "en":
        return PLANNER_SYSTEM_TEMPLATE.format(lang_instruction="")
    return PLANNER_SYSTEM_TEMPLATE.format(
        lang_instruction=f"\nIMPORTANT: All text fields (project_context_summary, special_instructions, summaries, reasons) MUST be written in {language}. JSON keys remain in English."
    )


PLANNER_SYSTEM = get_planner_system("en")


def build_classification_prompt(
    file_diffs: list[FileDiff],
    project_context: str,
    batch_index: int = 0,
    total_batches: int = 1,
    rename_pairs: list[tuple[str, str]] | None = None,
) -> str:
    file_list_lines: list[str] = []
    for fd in file_diffs:
        cat = fd.change_category.value if fd.change_category is not None else "unknown"
        file_list_lines.append(
            f"- {fd.file_path} | status={fd.file_status.value} | "
            f"category={cat} | "
            f"fork_lines_added={fd.lines_added} | fork_lines_deleted={fd.lines_deleted} | "
            f"upstream_lines_added={fd.upstream_lines_added} | "
            f"upstream_lines_deleted={fd.upstream_lines_deleted} | "
            f"conflicts={fd.conflict_count} | security_sensitive={fd.is_security_sensitive}"
        )

    file_list = "\n".join(file_list_lines)

    batch_hint = ""
    if total_batches > 1:
        batch_hint = f"\nNote: This is batch {batch_index + 1} of {total_batches}. Classify only the files listed below.\n"

    rename_section = ""
    if rename_pairs:
        rename_lines = "\n".join(f"  {old} → {new}" for old, new in rename_pairs)
        rename_section = (
            f"\n## Detected File Renames\n"
            f"The following paths are the same file moved/renamed (treat them as related):\n"
            f"{rename_lines}\n"
            f"When both old and new paths appear in the file list, classify them together "
            f"and note the rename in special_instructions.\n"
        )

    return f"""Analyze the following changed files and create a merge plan.

Project context:
{project_context or "No project context provided."}
{batch_hint}
Changed files ({len(file_diffs)} total):
{file_list}
{rename_section}
## Classification Rules (apply strictly in order)

**auto_safe** — DEFAULT for most files. Use when ALL of:
  - conflicts = 0
  - security_sensitive = false
  - fork_lines_added + fork_lines_deleted < 200
  - upstream_lines_added + upstream_lines_deleted < 200
  - category != both_changed (C-class needs at minimum auto_risky)
  - Routine changes: deps, config, docs, tests, minor refactors

**auto_risky** — Use when ANY of:
  - Large fork diffs (fork_lines_added + fork_lines_deleted >= 200) touching shared interfaces
  - Large upstream diffs (upstream_lines_added + upstream_lines_deleted >= 200) — even if fork delta is small, a big upstream refactor risks silently dropping fork edits
  - category = both_changed (both sides modified the file — must go through ConflictAnalyst)
  - Cross-cutting changes that affect many callers
  - Database schema or migration files (even without conflicts)

**human_required** — Use ONLY when at least ONE of:
  - conflicts > 0  (actual merge conflict markers present)
  - security_sensitive = true  (auth, crypto, secrets, permissions)
  - Core business logic with both sides making semantic changes

**deleted_only** — file_status is deleted, no conflicts
**binary** — binary files (images, compiled artifacts)
**excluded** — generated files, lock files, .gitignore patterns

⚠️  HARD RULE: If category=both_changed, you MUST NOT classify auto_safe. Choose at least auto_risky so ConflictAnalyst can inspect the file.
⚠️  HARD RULE: If upstream_lines_added + upstream_lines_deleted >= 200 AND category=both_changed, choose human_required (large upstream refactor over fork edits is high-risk).
⚠️  BIAS TOWARD AUTO_SAFE for category=upstream_only files (B-class). When in doubt between auto_safe and auto_risky for B-class, choose auto_safe.
⚠️  NEVER use human_required for a file with conflicts=0 and security_sensitive=false unless category=both_changed with large upstream delta.

Create a phased merge plan with the following structure:
1. Classify each file by risk level using the rules above
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


MAX_REVISION_ISSUES = 50


def build_revision_prompt(
    original_plan: MergePlan, judge_issues: list[PlanIssue]
) -> str:
    capped_issues = judge_issues[:MAX_REVISION_ISSUES]
    issues_text = "\n".join(
        f"- File: {issue.file_path}\n"
        f"  Current: {issue.current_classification.value}\n"
        f"  Suggested: {issue.suggested_classification.value}\n"
        f"  Reason: {issue.reason}\n"
        f"  Type: {issue.issue_type}"
        for issue in capped_issues
    )
    if len(judge_issues) > MAX_REVISION_ISSUES:
        issues_text += (
            f"\n\n(Showing {MAX_REVISION_ISSUES} of {len(judge_issues)} issues. "
            f"Apply the same reclassification pattern to similar files.)"
        )

    phases_text = "\n".join(
        f"- Batch {b.batch_id}: phase={b.phase.value}, "
        f"risk_level={b.risk_level.value}, "
        f"file_count={len(b.file_paths)}"
        for b in original_plan.phases
    )

    return f"""The plan reviewer has identified specific issues with the merge plan that need correction.

Original plan summary:
- Total files: {original_plan.risk_summary.total_files}
- Auto-safe: {original_plan.risk_summary.auto_safe_count}
- Auto-risky: {original_plan.risk_summary.auto_risky_count}
- Human required: {original_plan.risk_summary.human_required_count}

Current phases:
{phases_text}

Issues found by reviewer:
{issues_text}

Instructions:
1. For each issue, move the file from its current batch to a new or existing batch matching the suggested classification.
2. Do NOT change classifications of files not listed in the issues.
3. Recalculate risk_summary counts after reclassification.
4. Return the complete revised plan in the same JSON format as the original plan."""


PLANNER_EVALUATION_SYSTEM = """You are a code merge planning expert evaluating reviewer feedback on your merge plan.
For each issue raised by the reviewer, you must independently assess whether it is valid based on
the file's actual characteristics (diff size, conflict count, security sensitivity, language).
You are allowed to REJECT suggestions you disagree with — provide a clear technical reason.
Respond with ONLY a JSON object. No markdown, no extra text."""


def build_evaluation_prompt(
    plan: MergePlan,
    judge_issues: list[PlanIssue],
    lang: str = "en",
) -> str:
    capped = judge_issues[:MAX_REVISION_ISSUES]

    issues_text = "\n".join(
        f"- issue_id: {issue.issue_id}\n"
        f"  file_path: {issue.file_path}\n"
        f"  current_classification: {issue.current_classification.value}\n"
        f"  suggested_classification: {issue.suggested_classification.value}\n"
        f"  reason: {issue.reason}\n"
        f"  issue_type: {issue.issue_type}"
        for issue in capped
    )

    phases_text = "\n".join(
        f"- Batch {b.batch_id}: phase={b.phase.value}, "
        f"risk_level={b.risk_level.value}, "
        f"files={len(b.file_paths)}"
        for b in plan.phases
    )

    lang_note = ""
    if lang == "zh":
        lang_note = '\n\nIMPORTANT: All "reason" and "counter_proposal" fields MUST be written in Chinese.'

    return f"""The plan reviewer has raised the following issues about your merge plan.
Evaluate each issue independently. You may accept, reject, or request discussion.

## Your Current Plan
- Total files: {plan.risk_summary.total_files}
- Auto-safe: {plan.risk_summary.auto_safe_count}
- Auto-risky: {plan.risk_summary.auto_risky_count}
- Human required: {plan.risk_summary.human_required_count}

Phases:
{phases_text}

## Reviewer Issues
{issues_text}

## Instructions
For EACH issue, decide:
- "accept": You agree the file should be reclassified as suggested. State why you agree.
- "reject": You disagree and want to keep the current classification. Give a clear technical reason.
- "discuss": You partially agree or need clarification. Propose an alternative.

Return JSON:
{{
  "responses": [
    {{
      "issue_id": "<from above>",
      "file_path": "<file_path>",
      "action": "accept" | "reject" | "discuss",
      "reason": "Your technical reasoning",
      "counter_proposal": "Only for discuss — your alternative suggestion (null otherwise)"
    }}
  ]
}}{lang_note}

Respond with ONLY the JSON object."""
