from __future__ import annotations

import difflib
from typing import Any

from src.models.diff import FileDiff
from src.tools.diff_facts import DiffFacts

_ROUND_PER_VERSION_CHARS = 1000
# Per-side diff budget for commit-round prompts. Two sides (fork, upstream)
# keep total per-file content near _FILE_TOKEN_ESTIMATE (1000 tokens ≈ 4000
# chars) so the round token estimate in conflict_analysis.py stays valid and
# a full 60-file round cannot blow the context window.
_ROUND_DIFF_MAX_CHARS_PER_SIDE = 2000


def _fmt_version(content: str | None, language: str) -> str:
    if not content:
        return "*(not available)*"
    trimmed = content[:_ROUND_PER_VERSION_CHARS]
    if len(content) > _ROUND_PER_VERSION_CHARS:
        trimmed += "\n... [truncated]"
    return f"```{language}\n{trimmed}\n```"


def _unified_diff_section(
    from_text: str | None,
    to_text: str | None,
    from_label: str,
    to_label: str,
    max_chars: int,
) -> str | None:
    """Render a char-bounded unified diff of ``from_text`` → ``to_text``.

    Returns ``None`` when the two sides are identical (no diff to show), so
    callers can emit an explicit "no changes" note. Truncation happens on
    line boundaries with a trailing marker counting omitted diff lines.
    """
    from_lines = (from_text or "").splitlines(keepends=True)
    to_lines = (to_text or "").splitlines(keepends=True)
    diff_lines = list(
        difflib.unified_diff(
            from_lines, to_lines, fromfile=from_label, tofile=to_label, n=3
        )
    )
    if not diff_lines:
        return None

    kept: list[str] = []
    used = 0
    omitted = 0
    for idx, line in enumerate(diff_lines):
        if kept and used + len(line) > max_chars:
            omitted = len(diff_lines) - idx
            break
        kept.append(line)
        used += len(line)
    body = "".join(kept)
    if omitted:
        body += f"... (+{omitted} more diff lines)\n"
    return body


def build_commit_round_prompt(
    round_commits: list[dict[str, Any]],
    file_three_way: dict[str, tuple[str | None, str | None, str | None]],
    file_languages: dict[str, str],
    project_context: str = "",
    imported_symbols_by_file: dict[str, dict[str, list[str]]] | None = None,
    diff_facts_by_file: dict[str, DiffFacts] | None = None,
) -> str:
    commit_summary = "\n".join(
        f"  - {c['sha'][:8]}: {c.get('message', '')}  ({len(c.get('files', []))} files)"
        for c in round_commits
    )

    file_sections: list[str] = []
    for fp, (base_c, current_c, target_c) in file_three_way.items():
        lang = file_languages.get(fp, "")
        fork_diff = _unified_diff_section(
            base_c,
            current_c,
            f"base:{fp}",
            f"fork:{fp}",
            _ROUND_DIFF_MAX_CHARS_PER_SIDE,
        )
        upstream_diff = _unified_diff_section(
            base_c,
            target_c,
            f"base:{fp}",
            f"upstream:{fp}",
            _ROUND_DIFF_MAX_CHARS_PER_SIDE,
        )
        fork_block = (
            f"```diff\n{fork_diff}```"
            if fork_diff
            else "*(fork made no changes vs merge-base)*"
        )
        upstream_block = (
            f"```diff\n{upstream_diff}```"
            if upstream_diff
            else "*(upstream made no changes vs merge-base)*"
        )
        surface = (
            (imported_symbols_by_file or {}).get(fp)
            if imported_symbols_by_file
            else None
        )
        surface_section = ""
        if surface:
            surface_lines = ["### Imported Symbol Surface"]
            for path, names in surface.items():
                if names:
                    surface_lines.append(f"- `{path}` exports: {', '.join(names)}")
                else:
                    surface_lines.append(f"- `{path}` exports: (no exports detected)")
            surface_section = "\n".join(surface_lines) + "\n"
        facts_section = ""
        per_file_facts = (
            (diff_facts_by_file or {}).get(fp) if diff_facts_by_file else None
        )
        if per_file_facts:
            facts_section = _format_diff_facts_block(per_file_facts).replace(
                "# Deterministic Diff Facts", "### Deterministic Diff Facts"
            )
        file_sections.append(
            f"## {fp}  (language: {lang})\n"
            f"{surface_section}"
            f"{facts_section}"
            f"### Fork changes (merge-base → fork)\n{fork_block}\n"
            f"### Upstream changes (merge-base → upstream)\n{upstream_block}"
        )

    return (
        f"Analyze the following {len(file_three_way)} files from "
        f"{len(round_commits)} upstream commits being merged into a fork.\n\n"
        f"# Project Context\n{project_context or 'No project context provided.'}\n\n"
        f"# Commits in this round\n{commit_summary}\n\n"
        f"# File Changes (three-way diffs against the merge-base)\n"
        f"Each file shows what the fork changed and what upstream changed, "
        f"relative to their common ancestor. Reason about whether the two "
        f"sets of changes touch the same regions and can coexist.\n\n"
        + "\n\n".join(file_sections)
        + """

For every file above provide a conflict analysis. Rationale and intent
descriptions must be SPECIFIC — name the affected functions / fields /
regions. Do NOT write boilerplate like "comparable small changes",
"both sides made similar edits", "minor refactor". The
`semantic_compatibility` field is REQUIRED and must be one of:
  - "compatible"   — both edits address related concerns and CAN be
                     combined (typical companion to semantic_merge)
  - "incompatible" — the two edits contradict each other on the same
                     contract / invariant; merge needs a human decision
                     (forces escalate_human downstream)
  - "orthogonal"   — the edits do not interact; either take_* is safe

Return JSON:
{
  "files": [
    {
      "file_path": "<exact path>",
      "conflict_type": "concurrent_modification | logic_contradiction | semantic_equivalent | dependency_update | interface_change | deletion_vs_modification | refactor_vs_feature | configuration | unknown",
      "recommended_strategy": "take_target | take_current | semantic_merge | escalate_human",
      "confidence": 0.85,
      "can_coexist": true,
      "semantic_compatibility": "compatible | incompatible | orthogonal",
      "is_security_sensitive": false,
      "rationale": "concise explanation",
      "upstream_intent": {"description": "...", "intent_type": "bugfix | refactor | feature | upgrade | config", "confidence": 0.9},
      "fork_intent": {"description": "...", "intent_type": "bugfix | refactor | feature | upgrade | config", "confidence": 0.8}
    }
  ]
}"""
    )


ANALYST_SYSTEM = """You are a professional code merge expert specializing in semantic analysis of Git conflicts.
Your task is to deeply analyze each conflict point, understand the intent of both sides,
and provide merge recommendations with confidence scores.
Always provide specific, actionable recommendations based on code semantics, not just syntax.

GROUNDING RULES — non-negotiable:

For EVERY function / method / class / constant name you mention in the
rationale, exactly ONE of the following must hold:

  (a) The exact name appears verbatim somewhere in the fork or upstream
      content shown above (you can quote the line).

  (b) You write on its own line: `REQUIRES NEW API: <symbol>` followed by
      a one-line justification.

There is no third option. In particular, ANY conditional phrasing that
hedges a symbol's existence is fabrication and is forbidden — including
"if available", "if exists", "if it exists", "if X exists", "could use",
"you can use", "should exist", "presumably", "likely has", or any
paraphrase of these. Replace every such hedge with REQUIRES NEW API.

Pattern-completing a symmetric name (inferring `core._isoWeek` from seeing
`core._isoDate` + a fork-side `iso.week`) is the most common failure mode
and is explicitly fabrication — the symbol may simply not exist on either
side.

Example of WRONG rationale (observed on the zod merge, broke compilation):
  "Use core._isoWeek if it exists, or keep iso.week."

Example of RIGHT rationale for the same situation:
  "Keep `iso.week` (present in fork). For symmetry with upstream's
   `core._isoDate / _isoTime / _isoDuration` refactor, one option is
   REQUIRES NEW API: core._isoWeek — would need to be added to
   core/api.ts. Preferred path: keep `iso.week` since it already works."

Prefer recommendations that combine symbols already present on either
side over recommendations that need new API surface."""


def _format_diff_facts_block(
    diff_facts: "DiffFacts | None",
) -> str:
    """Render PR-C ground-truth verb counts the LLM must match.

    Empty input returns an empty string so legacy callers see no
    behaviour change. The block is intentionally short — the model has
    plenty of other context, this is a 3-line truth check.
    """
    if not diff_facts:
        return ""
    f = diff_facts["fork_side"]
    u = diff_facts["upstream_side"]
    return (
        "# Deterministic Diff Facts\n"
        "Counts derived from a difflib opcode pass on the actual three-way "
        "content. Use these exact verbs in your rationale — do not say "
        '"added" when the operation is a modify-in-place, and do not say '
        '"both added" when one side modified.\n'
        f"- FORK side (base→fork): {f['added']} added group(s), "
        f"{f['removed']} removed group(s), {f['modified']} modified group(s)\n"
        f"- UPSTREAM side (base→upstream): {u['added']} added group(s), "
        f"{u['removed']} removed group(s), {u['modified']} modified group(s)\n"
    )


def _format_imported_symbol_surface(
    imported_symbols: dict[str, list[str]] | None,
) -> str:
    """Render the analyst's view of what each namespace import exposes.

    PR-D-B: pairs with the GROUNDING RULES (PR-D-A) — the rules tell
    the LLM not to fabricate, this block tells it what it may use.
    Empty input renders the empty string so existing callers see no
    behaviour change.
    """
    if not imported_symbols:
        return ""
    lines = [
        "# Imported Symbol Surface",
        (
            "These are the symbols each imported module actually exposes "
            "(read at the same ref as the diff above). A name not in this "
            "list does not exist on that module — do not reference it; if "
            "you genuinely need it, use REQUIRES NEW API."
        ),
    ]
    for path, names in imported_symbols.items():
        if names:
            lines.append(f"- `{path}` exports: {', '.join(names)}")
        else:
            lines.append(f"- `{path}` exports: (no exports detected)")
    return "\n".join(lines) + "\n"


def build_conflict_analysis_prompt(
    file_diff: FileDiff,
    base_content: str | None,
    current_content: str | None,
    target_content: str | None,
    project_context: str,
    imported_symbols: dict[str, list[str]] | None = None,
    diff_facts: "DiffFacts | None" = None,
) -> str:
    language = file_diff.language or "unknown"
    base_section = (
        f"```{language}\n{base_content}\n```" if base_content else "Not available"
    )
    current_section = (
        f"```{language}\n{current_content}\n```" if current_content else "Not available"
    )
    target_section = (
        f"```{language}\n{target_content}\n```" if target_content else "Not available"
    )

    fork_added = file_diff.lines_added
    fork_deleted = file_diff.lines_deleted
    upstream_added = file_diff.upstream_lines_added
    upstream_deleted = file_diff.upstream_lines_deleted
    fork_total = fork_added + fork_deleted
    upstream_total = upstream_added + upstream_deleted

    if upstream_total == 0 and fork_total == 0:
        size_signal = "Both sides made no line changes (suspect rename / mode-only)."
    elif upstream_total == 0:
        size_signal = (
            "FORK changed lines only — upstream did not modify this file. "
            "Strongly prefer take_current."
        )
    elif fork_total == 0:
        size_signal = (
            "UPSTREAM changed lines only — fork did not modify this file. "
            "Strongly prefer take_target."
        )
    else:
        ratio = fork_total / upstream_total
        if ratio >= 5.0:
            size_signal = (
                f"FORK changes ({fork_total} lines) dominate upstream "
                f"({upstream_total} lines) by {ratio:.1f}x. Prefer take_current "
                f"or semantic_merge — take_target would discard substantial "
                f"fork customization."
            )
        elif ratio <= 0.2:
            size_signal = (
                f"UPSTREAM changes ({upstream_total} lines) dominate fork "
                f"({fork_total} lines). take_target is usually safe, but "
                f"verify that fork's small change isn't load-bearing."
            )
        else:
            size_signal = (
                f"Both sides made comparable changes "
                f"(fork={fork_total} vs upstream={upstream_total} lines). "
                f"semantic_merge is the default; only choose take_target / "
                f"take_current if one side's change clearly subsumes the other."
            )

    surface_block = _format_imported_symbol_surface(imported_symbols)
    facts_block = _format_diff_facts_block(diff_facts)

    return f"""Analyze this Git merge conflict and provide a structured analysis.

# Project Context
{project_context or "No project context provided."}

# File Information
Path: {file_diff.file_path}
Language: {language}
Fork-side change (base→fork): +{fork_added} / -{fork_deleted}
Upstream-side change (base→upstream): +{upstream_added} / -{upstream_deleted}
Conflict count: {file_diff.conflict_count}

# Change-volume signal
{size_signal}

{facts_block}{surface_block}# Three-way Diff

## Common ancestor version (merge-base)
{base_section}

## Current version (fork's modifications)
{current_section}

## Target version (upstream's modifications)
{target_section}

# Analysis Task
Analyze this conflict and output:
1. conflict_type: one of concurrent_modification, logic_contradiction, semantic_equivalent,
   dependency_update, interface_change, deletion_vs_modification, refactor_vs_feature, configuration, unknown
2. upstream_intent: upstream modification intent (type, description, confidence)
3. fork_intent: fork modification intent (type, description, confidence)
4. can_coexist: whether both modifications can coexist
5. recommended_strategy: take_current, take_target, semantic_merge, escalate_human
6. confidence: overall confidence (0.0 to 1.0)
7. rationale: reasoning explanation
8. semantic_compatibility: required three-state field describing how the
   two sides interact:
   - "compatible"   — both edits address related concerns and CAN be
                      combined (typical companion to semantic_merge)
   - "incompatible" — the two edits contradict each other on the same
                      contract / invariant; the merge needs a human
                      decision (forces escalate_human downstream)
   - "orthogonal"   — the edits do not interact (different fields,
                      different code paths); either take_* is safe

Rationale and intent descriptions must be SPECIFIC about the actual code
changes — name the affected functions / fields / regions. Do NOT write
boilerplate like "comparable small changes", "both sides made similar
edits", "minor refactor". If the change really is trivial, say WHAT it
is (e.g. "fork renamed `parseDate` to `parseISODate`; upstream added a
`strict` parameter to the same function").

Return JSON:
{{
  "conflict_type": "concurrent_modification",
  "upstream_intent": {{
    "description": "What upstream changed and why",
    "intent_type": "bugfix | refactor | feature | upgrade | config",
    "confidence": 0.8
  }},
  "fork_intent": {{
    "description": "What fork changed and why",
    "intent_type": "bugfix | refactor | feature | upgrade | config",
    "confidence": 0.8
  }},
  "can_coexist": true,
  "semantic_compatibility": "compatible | incompatible | orthogonal",
  "recommended_strategy": "semantic_merge",
  "confidence": 0.75,
  "rationale": "Detailed explanation of the analysis and recommendation",
  "is_security_sensitive": false
}}"""


def build_decision_proposal_prompt(
    file_path: str,
    base_content: str | None,
    fork_content: str | None,
    upstream_content: str | None,
    language: str = "",
    project_context: str = "",
    max_options: int = 3,
) -> str:
    """Build a prompt that asks the analyst to propose 1–``max_options``
    file-specific decision options for a HUMAN_REQUIRED file.

    Each option must be concrete and actionable — not "review carefully"
    — and the analyst should ground each proposal in the actual fork /
    upstream / base content.  Returned JSON is consumed by
    ``parse_decision_proposals`` below.
    """
    context_section = (
        f"\n## Project Context\n{project_context}\n" if project_context else ""
    )

    return f"""You are proposing concrete merge-resolution options for a file that
landed in HUMAN_REQUIRED. The reviewer will see these proposals as
clickable buttons; each option must be actionable, file-specific, and
grounded in the actual three-way content below — not a generic
"review carefully" advisory.
{context_section}
## File
`{file_path}` (language={language or "unknown"})

## Base (common ancestor)
{_fmt_version(base_content, language)}

## Fork side (HEAD)
{_fmt_version(fork_content, language)}

## Upstream side (target)
{_fmt_version(upstream_content, language)}

## Your Task
Propose 1 to {max_options} concrete merge strategies for this file. Each
strategy should describe a SPECIFIC way to combine fork and upstream
changes — naming the actual fields, functions, or regions involved.

Avoid these generic non-actions:
- "review and merge manually"
- "take both sides"  (be specific about WHICH parts of each side)
- "ask the team"

Return JSON:
{{
  "proposals": [
    {{
      "key": "short-kebab-id",
      "label": "Short button label (≤60 chars)",
      "description": "1–2 sentences explaining WHAT this option does for this file",
      "preview": "Optional short snippet showing the expected merged region (may be empty)"
    }}
  ]
}}

Respond with ONLY the JSON object. No markdown, no extra prose."""


def parse_decision_proposals(raw: str) -> list[dict[str, str]]:
    """Best-effort parser for ``build_decision_proposal_prompt`` output.

    Returns a list of ``{key, label, description, preview}`` dicts. Any
    parse failure yields an empty list — the caller treats that as
    "analyst could not propose anything" and falls back to the base
    decision ladder. Never raises.
    """
    import json
    import re

    text = (raw or "").strip()
    # Strip code fences if the model added them despite the instruction.
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)

    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return []
    raw_props = obj.get("proposals") if isinstance(obj, dict) else None
    if not isinstance(raw_props, list):
        return []

    out: list[dict[str, str]] = []
    for p in raw_props:
        if not isinstance(p, dict):
            continue
        key = str(p.get("key", "")).strip()
        label = str(p.get("label", "")).strip()
        description = str(p.get("description", "")).strip()
        preview = p.get("preview")
        preview_str = (
            str(preview).strip() if isinstance(preview, str) and preview.strip() else ""
        )
        if not key or not label:
            continue
        out.append(
            {
                "key": key,
                "label": label[:80],
                "description": description,
                "preview": preview_str,
            }
        )
    return out
