from __future__ import annotations

import json
from typing import Any

MEMORY_EXTRACTOR_SYSTEM = """\
You are a memory extraction specialist for a code merge system.
Your sole job is to read runtime events (errors, plan disputes, judge repair rounds)
and produce a compact list of causal insights that will help future merge phases
avoid the same problems.

Rules:
- Return ONLY a valid JSON array — no markdown, no prose.
- Each element must have: entry_type, content, confidence, tags, file_paths.
- entry_type: one of "decision", "pattern", "codebase_insight".
- content: one sentence ≤ 120 characters explaining WHY something happened or WHAT to watch for.
- confidence: float [0.0, 1.0] reflecting how certain you are.
- tags: list of short snake_case labels (e.g. ["judge_failure", "plan_dispute"]).
- file_paths: list of file paths involved; empty list if global.
- Do NOT repeat insights already summarised in the existing_entries context.
- Return an empty array [] if there is nothing new to add.\
"""


def build_extraction_prompt(
    phase: str,
    events: dict[str, Any],
    max_insights: int,
) -> str:
    errors = events.get("errors", [])
    disputes = events.get("plan_disputes", [])
    verdicts_log = events.get("judge_verdicts_log", [])
    repair_rounds = events.get("judge_repair_rounds", 0)

    sections: list[str] = [
        f"## Phase: {phase}",
        f"## Max insights to return: {max_insights}",
        "",
    ]

    if errors:
        sections.append("## Errors")
        sections.append(json.dumps(errors[-10:], ensure_ascii=False, indent=2))
        sections.append("")

    if disputes:
        sections.append("## Plan disputes")
        sections.append(json.dumps(disputes, ensure_ascii=False, indent=2))
        sections.append("")

    if verdicts_log or repair_rounds:
        sections.append("## Judge repair context")
        sections.append(f"repair_rounds: {repair_rounds}")
        if verdicts_log:
            sections.append(json.dumps(verdicts_log[-5:], ensure_ascii=False, indent=2))
        sections.append("")

    sections.append(
        "Return a JSON array of insight objects (entry_type, content, confidence, tags, file_paths). "
        f"Limit to {max_insights} entries."
    )
    return "\n".join(sections)
