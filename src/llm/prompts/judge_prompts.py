from src.models.diff import FileDiff
from src.models.decision import FileDecisionRecord


JUDGE_SYSTEM = """You are an independent reviewer of code merge results. Your task is to verify whether
the merge result preserves all private logic of the fork branch and correctly introduces all changes from the upstream branch.
You do not know the Executor's decision process; you only look at the final merge result and the original diff,
and independently assess quality. Be thorough and critical."""


def build_file_review_prompt(
    file_path: str,
    merged_content: str,
    decision_record: FileDecisionRecord,
    original_diff: FileDiff,
    project_context: str = "",
) -> str:
    language = original_diff.language or "unknown"
    decision_val = decision_record.decision.value if hasattr(decision_record.decision, "value") else decision_record.decision
    source_val = decision_record.decision_source.value if hasattr(decision_record.decision_source, "value") else decision_record.decision_source

    return f"""Review the following merged file for correctness and completeness.

# Project Context
{project_context or "No project context provided."}

# File: {file_path}
Language: {language}

# Merge Decision Applied
- Decision: {decision_val}
- Source: {source_val}
- Rationale: {decision_record.rationale}

# Original Diff Statistics
- Lines added: {original_diff.lines_added}
- Lines deleted: {original_diff.lines_deleted}
- Conflicts: {original_diff.conflict_count}
- Security sensitive: {original_diff.is_security_sensitive}

# Merged Content
```{language}
{merged_content[:5000]}{"..." if len(merged_content) > 5000 else ""}
```

# Review Tasks
1. Check for remaining conflict markers (<<<<<<, =======, >>>>>>>)
2. Check if fork's private logic is preserved
3. Check if upstream's key features are incorporated
4. Check for any obvious errors or missing logic

Return JSON:
{{
  "issues": [
    {{
      "file_path": "{file_path}",
      "issue_level": "critical | high | medium | low | info",
      "issue_type": "missing_logic | wrong_merge | unresolved_conflict | syntax_error | other",
      "description": "Specific issue description",
      "affected_lines": [],
      "suggested_fix": "How to fix this issue",
      "must_fix_before_merge": true
    }}
  ],
  "overall_assessment": "Brief overall quality assessment",
  "confidence": 0.8
}}"""


def build_verdict_prompt(
    reviewed_files: list[str],
    all_issues_summary: str,
    critical_count: int,
    high_count: int,
) -> str:
    if critical_count > 0 or high_count > 0:
        verdict_hint = "fail"
    elif all_issues_summary.strip():
        verdict_hint = "conditional"
    else:
        verdict_hint = "pass"

    return f"""Based on the review of {len(reviewed_files)} files, provide a final verdict.

Issues summary:
{all_issues_summary or "No issues found."}

Critical issues: {critical_count}
High issues: {high_count}

Provide a final verdict:
- pass: No critical or high issues
- conditional: Has medium/low issues that should be addressed
- fail: Has critical or high issues that must be fixed

Return JSON:
{{
  "verdict": "{verdict_hint}",
  "summary": "Overall merge quality assessment",
  "blocking_issues": ["issue_id_1", "issue_id_2"]
}}"""
