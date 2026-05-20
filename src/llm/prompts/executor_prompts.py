from __future__ import annotations

from src.models.diff import FileDiff
from src.models.conflict import ConflictAnalysis


EXECUTOR_SYSTEM = """You are a code merge executor. Your task is to apply merge decisions to files,
performing semantic merges when needed. You must be precise and preserve all important logic from both branches.
Never lose code that may be functionally important.

OUTPUT CONTRACT — strictly enforced by a parser, violating any rule causes the merge to be escalated to a human:

1. Output ONLY the merged file content. No prefatory explanation, no chain-of-thought,
   no "Looking at...", "Here is...", "Let me merge...", "I'll combine...", or any other
   conversational preamble. The first character of your reply must be the first character
   of the merged file.
2. Do NOT wrap the output in markdown code fences (no ```language ... ```). The downstream
   parser strips fences as a backwards-compat fallback but treats their absence as the
   contract.
3. Do NOT echo elision markers like "# ... (3 sections omitted)" or "<... omitted ...>" —
   even if they appeared in the prompt's staged context. Always emit the complete file.
4. If the merged file would exceed your output buffer (you cannot fit it in one response),
   STOP immediately and emit exactly this single token on its own line and nothing else:

       OUTPUT_TOO_LARGE

   The orchestrator will fall back to chunked merging. Outputting a truncated file is
   strictly worse than emitting this token — a truncated file silently corrupts the repo."""


def build_semantic_merge_prompt(
    file_diff: FileDiff,
    conflict_analysis: ConflictAnalysis,
    current_content: str,
    target_content: str,
    project_context: str,
) -> str:
    language = file_diff.language or "unknown"
    rec_val = (
        conflict_analysis.recommended_strategy.value
        if hasattr(conflict_analysis.recommended_strategy, "value")
        else conflict_analysis.recommended_strategy
    )

    return f"""Perform a semantic merge of the following two versions of a file.

# Project Context
{project_context or "No project context provided."}

# File: {file_diff.file_path}
Language: {language}

# Conflict Analysis
- Type: {conflict_analysis.conflict_type.value if hasattr(conflict_analysis.conflict_type, "value") else conflict_analysis.conflict_type}
- Recommended strategy: {rec_val}
- Rationale: {conflict_analysis.rationale}
- Confidence: {conflict_analysis.confidence}

# Current version (fork)
```{language}
{current_content}
```

# Target version (upstream)
```{language}
{target_content}
```

Produce a merged file that:
1. Preserves fork's private/custom logic
2. Incorporates upstream bug fixes and improvements
3. Contains NO conflict markers (<<<<<<<, =======, >>>>>>>)
4. Is syntactically valid

Return ONLY the merged file content."""


def build_deletion_analysis_prompt(
    file_path: str,
    lines_deleted: int,
    project_context: str,
) -> str:
    return f"""Analyze whether the following file deletion from upstream should be applied to the fork.

# Project Context
{project_context or "No project context provided."}

# File being deleted: {file_path}
Lines deleted: {lines_deleted}

Determine the most likely reason for deletion (e.g. refactoring cleanup, feature removal, file moved/renamed)
and assess whether it is safe to apply this deletion to the fork.

Respond in this format:
REASON: <one-line reason>
SAFE_TO_DELETE: <yes/no>
RATIONALE: <explanation in 2-3 sentences>"""


def build_rebuttal_prompt(
    issues_summary: str,
    file_paths: list[str],
    project_context: str,
    *,
    last_stop_reason: str | None = None,
    last_had_prose_preamble: bool = False,
) -> str:
    """Build the rebuttal-decision prompt fed to the executor LLM.

    ``last_stop_reason`` / ``last_had_prose_preamble`` carry forward
    quality-gate observations from the previous executor call so the
    LLM can reason about its own failure mode rather than mechanically
    regenerating the same broken output. When both default values are
    used the prompt is identical to the pre-quality-gate version
    (backwards compatibility for legacy callers / tests).
    """
    paths_str = ", ".join(file_paths[:10])
    prior_failure_block = ""
    if last_stop_reason in {"max_tokens", "length"} or last_had_prose_preamble:
        notes: list[str] = []
        if last_stop_reason in {"max_tokens", "length"}:
            notes.append(
                "* Your previous response was TRUNCATED at the max_tokens "
                "ceiling — the trailing bytes never made it into the file. "
                "If the file is too large to emit in one response this round, "
                "STOP and emit only `OUTPUT_TOO_LARGE` on its own line so the "
                "orchestrator can fall back to chunked merging."
            )
        if last_had_prose_preamble:
            notes.append(
                "* Your previous response began with conversational preamble "
                "(e.g. 'Looking at the current content...'). The parser "
                "rejected it because that text was treated as the file body. "
                "Output ONLY the merged file content — no narration, no "
                "chain-of-thought, no markdown fences."
            )
        prior_failure_block = (
            "\n# Prior-Round Quality-Gate Findings\n"
            "Your last attempt was rejected before the judge even reviewed it:\n"
            + "\n".join(notes)
            + "\n"
        )

    return f"""You are a code merge executor reviewing a judge's assessment of your merge work.

# Project Context
{project_context or "No project context provided."}

# Files reviewed: {paths_str}
{prior_failure_block}
# Judge's Issues
{issues_summary}

For each issue, decide whether to:
A) ACCEPT: You agree the issue is valid and will repair it.
B) DISPUTE: You have evidence the issue is a false positive or already handled.

Respond in JSON format:
{{
  "accepts_all": true/false,
  "decisions": [
    {{"issue_id": "<id>", "action": "accept"|"dispute", "counter_evidence": "<evidence if disputing>"}}
  ],
  "overall_rationale": "<brief overall summary>"
}}"""
