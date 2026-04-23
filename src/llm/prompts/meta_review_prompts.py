from __future__ import annotations

import json
from typing import Any

META_PLAN_SYSTEM = """\
You are a senior merge strategy advisor reviewing a failed plan negotiation.
The Planner and PlannerJudge have been unable to converge, or the Executor has
raised repeated plan disputes. Your job is to identify the root cause of the
impasse and suggest a high-level strategic direction.

Return ONLY a valid JSON object — no markdown, no prose:
{
  "assessment": "<one sentence ≤ 200 chars: root cause>",
  "recommendation": "<one sentence ≤ 200 chars: strategic action to try>"
}\
"""

META_JUDGE_SYSTEM = """\
You are a senior code quality advisor reviewing a failed merge quality cycle.
The Judge and Executor have been unable to reach consensus after multiple
repair rounds. Your job is to identify what is fundamentally blocking a
PASS verdict and suggest a strategic direction.

Return ONLY a valid JSON object — no markdown, no prose:
{
  "assessment": "<one sentence ≤ 200 chars: root cause>",
  "recommendation": "<one sentence ≤ 200 chars: strategic action to try>"
}\
"""


def build_meta_plan_review_prompt(
    plan_review_log: list[dict[str, Any]],
    plan_disputes: list[dict[str, Any]],
    total_rounds: int,
) -> str:
    sections = [
        f"## Failed Plan Negotiation — {total_rounds} round(s)",
        f"## Disputes raised during execution: {len(plan_disputes)}",
        "",
    ]
    if plan_disputes:
        sections.append("### Plan disputes")
        sections.append(json.dumps(plan_disputes[-5:], ensure_ascii=False, indent=2))
        sections.append("")
    if plan_review_log:
        sections.append("### Revision history (last 3 rounds)")
        sections.append(json.dumps(plan_review_log[-3:], ensure_ascii=False, indent=2))
        sections.append("")
    sections.append(
        "Provide a JSON object with 'assessment' and 'recommendation' keys only."
    )
    return "\n".join(sections)


def build_meta_judge_review_prompt(
    judge_verdicts_log: list[dict[str, Any]],
    judge_repair_rounds: int,
) -> str:
    sections = [
        f"## Failed Judge Review — {judge_repair_rounds + 1} round(s)",
        "",
    ]
    if judge_verdicts_log:
        verdict_summary = [
            {
                "round": v.get("round"),
                "verdict": v.get("verdict"),
                "issues": v.get("issues_count"),
            }
            for v in judge_verdicts_log
        ]
        sections.append("### Verdict history")
        sections.append(json.dumps(verdict_summary, ensure_ascii=False, indent=2))
        sections.append("")
        last = judge_verdicts_log[-1]
        if last.get("issues_count"):
            sections.append(
                f"Final round had {last['issues_count']} unresolved issues."
            )
            sections.append("")
    sections.append(
        "Provide a JSON object with 'assessment' and 'recommendation' keys only."
    )
    return "\n".join(sections)
