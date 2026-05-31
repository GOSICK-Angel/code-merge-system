from __future__ import annotations

from collections.abc import Sequence

from src.models.diff import FileDiff
from src.models.conflict import ConflictAnalysis


def _format_dependents_block(
    dependents: Sequence[str],
    referenced_symbols: frozenset[str] = frozenset(),
    *,
    max_listed: int = 8,
) -> str:
    """Phase B step 8: render a downstream-dependents warning, or ``""`` when
    nothing depends on the file. Built from the dependency graph's EXTRACTED
    in-edges so the executor preserves a file's public interface before
    deleting or rewriting it."""
    if not dependents:
        return ""
    listed = list(dependents)[:max_listed]
    more = len(dependents) - len(listed)
    files_line = ", ".join(listed) + (f" (+{more} more)" if more > 0 else "")
    block = [
        "# Downstream Dependents",
        f"{len(dependents)} file(s) import this one: {files_line}.",
        "Preserve its public interface — do NOT remove or rename exported "
        "symbols these dependents rely on.",
    ]
    if referenced_symbols:
        syms = ", ".join(sorted(referenced_symbols)[:max_listed])
        block.append(f"Symbols other files import from here: {syms}.")
    return "\n".join(block)


EXECUTOR_SYSTEM = """You are a code merge executor. Your task is to apply merge decisions to files,
performing semantic merges when needed. You must be precise and preserve all important logic from both branches.
Never lose code that may be functionally important.

GROUNDING — do not fabricate symbols:
Only call functions, methods, fields, or constants that already appear in the
fork content, the upstream content, or the "Imported Symbol Surface" block when
one is provided. Do NOT invent module exports, and do NOT infer a symmetric name
you have not actually seen (e.g. assuming `core._isoWeek` exists just because
`core._isoDate` does — that exact guess broke a real merge). If combining both
sides would require a symbol that exists on neither, prefer the side that already
compiles over calling a non-existent API. You emit raw file content only, so you
cannot leave a "to be added later" note — a reference to a symbol that does not
exist will break the build.

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


def _format_symbol_surface_block(
    imported_symbols: dict[str, list[str]] | None,
) -> str:
    """Render the symbols each namespace import exposes so the executor can
    ground qualified references instead of inventing them. Empty / None input
    renders the empty string so existing callers see no behaviour change.

    Phrased for an agent that emits raw file content — unlike the analyst's
    surface, it must NOT mention ``REQUIRES NEW API`` (the executor cannot
    leave annotations in the output)."""
    if not imported_symbols:
        return ""
    lines = [
        "# Imported Symbol Surface",
        (
            "Symbols each imported module actually exposes (read at fork_ref). "
            "A name not in this list does not exist on that module — do not "
            "reference it. Prefer combining symbols already present on either "
            "side over introducing new ones."
        ),
    ]
    for path, names in imported_symbols.items():
        if names:
            lines.append(f"- `{path}` exports: {', '.join(names)}")
        else:
            lines.append(f"- `{path}` exports: (no exports detected)")
    return "\n".join(lines) + "\n"


def build_semantic_merge_prompt(
    file_diff: FileDiff,
    conflict_analysis: ConflictAnalysis,
    current_content: str,
    target_content: str,
    project_context: str,
    dependents: Sequence[str] = (),
    referenced_symbols: frozenset[str] = frozenset(),
    imported_symbols: dict[str, list[str]] | None = None,
) -> str:
    language = file_diff.language or "unknown"
    rec_val = (
        conflict_analysis.recommended_strategy.value
        if hasattr(conflict_analysis.recommended_strategy, "value")
        else conflict_analysis.recommended_strategy
    )

    dependents_block = _format_dependents_block(dependents, referenced_symbols)
    dependents_section = f"\n{dependents_block}\n" if dependents_block else ""

    surface_block = _format_symbol_surface_block(imported_symbols)
    surface_section = f"\n{surface_block}" if surface_block else ""

    return f"""Perform a semantic merge of the following two versions of a file.

# Project Context
{project_context or "No project context provided."}

# File: {file_diff.file_path}
Language: {language}
{dependents_section}{surface_section}
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
    dependents: Sequence[str] = (),
) -> str:
    dependents_block = _format_dependents_block(dependents)
    dependents_section = f"\n{dependents_block}\n" if dependents_block else ""

    return f"""Analyze whether the following file deletion from upstream should be applied to the fork.

# Project Context
{project_context or "No project context provided."}

# File being deleted: {file_path}
Lines deleted: {lines_deleted}
{dependents_section}
Determine the most likely reason for deletion (e.g. refactoring cleanup, feature removal, file moved/renamed)
and assess whether it is safe to apply this deletion to the fork. If files above still depend on it,
deletion is risky — prefer keeping the file unless the dependents are also being removed.

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
