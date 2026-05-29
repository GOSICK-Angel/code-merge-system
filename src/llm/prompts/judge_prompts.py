from typing import Any, TYPE_CHECKING

from src.models.diff import FileDiff
from src.models.decision import FileDecisionRecord

if TYPE_CHECKING:
    from src.models.judge import JudgeCheckStrategy

_DEFAULT_MAX_CONTENT_CHARS = 5000

# P2-2: single strong JSON-only instruction shared by every judge prompt.
# Weak "Return JSON:" wording let some models emit a markdown preamble; this
# matches the planner_judge contract (parseable by json.loads, first char `{`).
_JSON_ONLY_INSTRUCTION = (
    "Respond with ONLY a single JSON object matching the schema — it will be "
    "parsed directly by json.loads(). The first character of your reply must "
    "be `{`. No markdown fences, no preamble, no trailing prose."
)

# P2-4: human-facing fields (issue description, suggested_fix, overall
# assessment) follow the run's output language. Injected only when lang ==
# "zh"; English runs are unchanged.
_ZH_LANG_NOTE = (
    "\n\n语言要求：每个 issue 的 description、suggested_fix 以及 "
    "overall_assessment 字段必须使用中文撰写（文件路径、函数名、枚举值等技术"
    "标识保留原文）。"
)


def _truncate_content(content: str, max_chars: int | None) -> str:
    limit = max_chars if max_chars is not None else _DEFAULT_MAX_CONTENT_CHARS
    if len(content) <= limit:
        return content
    return content[:limit] + "\n... [truncated]"


def _memory_section(memory_context: str) -> str:
    if not memory_context:
        return ""
    return f"\n{memory_context}\n\n"


def _fork_section(
    fork_content: str | None,
    merged_content: str,
    language: str,
    max_content_chars: int | None,
) -> str:
    if fork_content is None or fork_content == merged_content:
        return ""
    return (
        f"\n# Fork Original (pre-merge content of this file on fork_ref)\n"
        f"Use this to distinguish intentional fork customisations (e.g. \n"
        f"fork-only fields, renamed identifiers) from real merge defects.\n"
        f"```{language}\n"
        f"{_truncate_content(fork_content, max_content_chars)}\n"
        f"```\n"
    )


_UPSTREAM_MATCH_TASKS = """\
1. Check for remaining conflict markers (<<<<<<, =======, >>>>>>>)
2. Check that the merged result matches the upstream version — no upstream features missing
3. Check for any obvious errors or missing logic introduced during merge"""

_CUSTOMIZATION_PRESERVED_TASKS = """\
1. Check for remaining conflict markers (<<<<<<, =======, >>>>>>>)
2. Check that fork-specific customisations (added logic, extended interfaces, \
local business rules) are fully preserved and not overwritten
3. Check that upstream additions/changes are correctly integrated alongside the \
fork customisations — neither side silently dropped
4. Check for any obvious errors or inconsistencies caused by the merge"""


def _review_tasks_section(check_strategy: "JudgeCheckStrategy | None") -> str:
    if check_strategy is not None:
        from src.models.judge import JudgeCheckStrategy as _S

        if check_strategy == _S.CUSTOMIZATION_PRESERVED:
            return _CUSTOMIZATION_PRESERVED_TASKS
    return _UPSTREAM_MATCH_TASKS


JUDGE_SYSTEM = """You are an independent reviewer of code merge results. Your task is to verify whether
the merge result preserves all private logic of the fork branch and correctly introduces all changes from the upstream branch.
You do not know the Executor's decision process; you only look at the final merge result and the original diff,
and independently assess quality. Be thorough and critical."""


# P1-1: two worked examples anchor the verdict on its two failure modes — a
# clean merge (empty issues array, not an invented nitpick) and a real defect
# carrying the grounding the rule above demands (evidence_excerpt quoting a
# verbatim merged line). Claude-only — executor / planner_judge stay zero-shot
# per the §五 B-class guardrail.
_REVIEW_EXAMPLES = """<examples>
<example>
The merged file integrates upstream's new `parseConfig` call while keeping the
fork's `cacheTtl` field intact. No conflict markers, nothing dropped.
{
  "issues": [],
  "overall_assessment": "Clean merge: upstream's parseConfig integration is present and the fork's cacheTtl customisation is preserved. No conflict markers, no missing logic.",
  "confidence": 0.9
}
</example>

<example>
The merged file still contains an unresolved conflict marker and dropped the
fork's `retryCount` guard.
{
  "issues": [
    {
      "file_path": "src/client/http.py",
      "issue_level": "critical",
      "issue_type": "unresolved_conflict",
      "description": "Leftover conflict marker in the merged output — the merge was not completed.",
      "affected_lines": [42],
      "evidence_excerpt": "<<<<<<< HEAD",
      "suggested_fix": "Resolve the conflict region and remove the <<<<<<< / ======= / >>>>>>> markers.",
      "must_fix_before_merge": true,
      "resolvability": "fixable"
    },
    {
      "file_path": "src/client/http.py",
      "issue_level": "high",
      "issue_type": "missing_logic",
      "description": "The fork's retryCount guard before send() was dropped during the merge.",
      "affected_lines": [],
      "evidence_excerpt": "    def send(self, req):",
      "suggested_fix": "Re-introduce the `if self.retryCount > 0` guard ahead of the send() call.",
      "must_fix_before_merge": true,
      "resolvability": "fixable"
    }
  ],
  "overall_assessment": "Merge failed: an unresolved conflict marker remains and a fork-side retry guard was lost.",
  "confidence": 0.85
}
</example>
</examples>

"""


def build_file_review_prompt(
    file_path: str,
    merged_content: str,
    decision_record: FileDecisionRecord,
    original_diff: FileDiff,
    project_context: str = "",
    max_content_chars: int | None = None,
    memory_context: str = "",
    check_strategy: "JudgeCheckStrategy | None" = None,
    fork_content: str | None = None,
    lang: str = "en",
) -> str:
    language = original_diff.language or "unknown"
    lang_note = _ZH_LANG_NOTE if lang == "zh" else ""
    decision_val = (
        decision_record.decision.value
        if hasattr(decision_record.decision, "value")
        else decision_record.decision
    )
    source_val = (
        decision_record.decision_source.value
        if hasattr(decision_record.decision_source, "value")
        else decision_record.decision_source
    )

    fork_section = _fork_section(
        fork_content, merged_content, language, max_content_chars
    )

    return f"""<task>
Review the merged file below for correctness and completeness. The merged
content (and the fork's pre-merge original, when shown) come first; the merge
decision, review tasks, grounding rule and required JSON output follow.
</task>

<merged_content language="{language}">
```{language}
{_truncate_content(merged_content, max_content_chars)}
```
</merged_content>
{fork_section}{_memory_section(memory_context)}<file_info>
File: {file_path}
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
</file_info>

<project_context>
{project_context or "No project context provided."}
</project_context>

<instructions>
# Review Tasks
{_review_tasks_section(check_strategy)}

For each issue also set "resolvability":
- "fixable": can be resolved by re-running or applying a targeted fix (e.g. wrong merge decision, B-class file differs from upstream)
- "system_limitation": a known system boundary that cannot be auto-fixed (e.g. D-missing file skipped due to unsatisfied layer deps, unsupported merge strategy)
- "human_required": needs manual human intervention (e.g. escalate_human file with complex conflicts, ambiguous business logic)

GROUNDING RULE (P1-3): every CRITICAL or HIGH issue MUST include either a
non-empty "affected_lines" array OR a non-empty "evidence_excerpt" string
quoting a verbatim line from the merged content. Ungrounded CRITICAL/HIGH
issues will be auto-downgraded to MEDIUM by the parser, so failing to cite
evidence weakens your verdict.{lang_note}
</instructions>

{_REVIEW_EXAMPLES}<output_format>
{_JSON_ONLY_INSTRUCTION}
{{
  "issues": [
    {{
      "file_path": "{file_path}",
      "issue_level": "critical | high | medium | low | info",
      "issue_type": "missing_logic | wrong_merge | unresolved_conflict | syntax_error | other",
      "description": "Specific issue description",
      "affected_lines": [],
      "evidence_excerpt": "verbatim line from merged content backing the claim",
      "suggested_fix": "How to fix this issue",
      "must_fix_before_merge": true,
      "resolvability": "fixable | system_limitation | human_required"
    }}
  ],
  "overall_assessment": "Brief overall quality assessment",
  "confidence": 0.8
}}
</output_format>"""


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

{_JSON_ONLY_INSTRUCTION}
{{
  "verdict": "{verdict_hint}",
  "summary": "Overall merge quality assessment",
  "blocking_issues": ["issue_id_1", "issue_id_2"]
}}"""


_BATCH_PER_FILE_CONTENT_CHARS = 2000


def build_batch_file_review_prompt(
    file_reviews: list[dict[str, Any]],
    project_context: str = "",
) -> str:
    sections: list[str] = []
    for i, fr in enumerate(file_reviews, 1):
        fp: str = fr["file_path"]
        fd = fr["original_diff"]
        record = fr["decision_record"]
        content: str = fr["merged_content"]
        language: str = fd.language or "unknown"
        decision_val = (
            record.decision.value
            if hasattr(record.decision, "value")
            else record.decision
        )
        sections.append(
            f"## File {i}: {fp}\n"
            f"Language: {language} | Decision: {decision_val}\n"
            f"Lines added: {fd.lines_added}, deleted: {fd.lines_deleted}, "
            f"security: {fd.is_security_sensitive}\n"
            f"```{language}\n"
            f"{_truncate_content(content, _BATCH_PER_FILE_CONTENT_CHARS)}\n"
            f"```"
        )

    return (
        f"Review the following {len(file_reviews)} merged files.\n\n"
        f"# Project Context\n"
        f"{project_context or 'No project context provided.'}\n\n"
        + "\n\n".join(sections)
        + """

For each file check: conflict markers, fork logic preserved, upstream changes present.

For each issue set "resolvability":
- "fixable": can be resolved by re-running or applying a targeted fix
- "system_limitation": known system boundary (D-missing skipped, unsupported strategy)
- "human_required": needs manual human intervention

GROUNDING RULE (P1-3): every CRITICAL or HIGH issue MUST include either a
non-empty "affected_lines" array OR a non-empty "evidence_excerpt" string
quoting a verbatim line from the merged content. Ungrounded CRITICAL/HIGH
issues are auto-downgraded to MEDIUM by the parser.

"""
        + _JSON_ONLY_INSTRUCTION
        + """
{
  "files": [
    {
      "file_path": "<exact file path>",
      "issues": [
        {
          "issue_level": "critical | high | medium | low | info",
          "issue_type": "missing_logic | wrong_merge | unresolved_conflict | syntax_error | other",
          "description": "Specific issue description",
          "affected_lines": [],
          "evidence_excerpt": "verbatim line from merged content backing the claim",
          "suggested_fix": "How to fix",
          "must_fix_before_merge": true,
          "resolvability": "fixable | system_limitation | human_required"
        }
      ]
    }
  ]
}"""
    )


def build_re_evaluate_prompt(
    rebuttal_summary: str,
    original_issues_summary: str,
) -> str:
    return f"""You are an independent code merge reviewer. The executor has responded to your prior assessment.

# Your Original Issues
{original_issues_summary or "No issues were raised."}

# Executor's Rebuttal
{rebuttal_summary}

Re-evaluate each disputed issue. For each issue:
- If the executor's counter-evidence is convincing, WITHDRAW the issue.
- If the issue stands despite the rebuttal, MAINTAIN it.

{_JSON_ONLY_INSTRUCTION}
{{
  "remaining_issues": [
    {{
      "issue_id": "<original issue_id>",
      "status": "maintained" | "withdrawn",
      "reasoning": "<why maintained or withdrawn>"
    }}
  ],
  "overall_approved": true | false
}}"""
