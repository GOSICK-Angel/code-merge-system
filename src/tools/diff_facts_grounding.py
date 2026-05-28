"""Detect rationale verbs that contradict the deterministic diff facts.

PR-C Slice 3 — companion to ``src.tools.diff_facts``. The prompt
(Slice 2) injects ground-truth verb counts and tells the LLM to match
them; this checker is the safety net for when the LLM doesn't. A
mismatch becomes a ``grounding_warnings`` entry so the reviewer sees
"the analyst said 'added' but the diff is a modify-in-place" with the
same surface treatment PR-A gives to fabricated symbols.

The check is intentionally narrow:
  - Only catches *side-attributed* claims ("upstream added",
    "fork removed", "both sides added", "both added")
  - Only when the corresponding count is genuinely zero
  - Empty / generic rationale → no warnings
"""

from __future__ import annotations

import re

from src.tools.diff_facts import DiffFacts

# `<side> <verb>` and variants. Capturing groups: 1=side phrase, 2=verb stem.
# Side phrases: "fork", "upstream", "both sides", "both" (the latter implies
# both sides simultaneously).
_VERB_CLAIM = re.compile(
    r"\b(both\s+sides?|both|fork|upstream)\s+(added|removed|modified|changed)\b",
    re.IGNORECASE,
)

_VERB_TO_KEY: dict[str, str] = {
    "added": "added",
    "removed": "removed",
    "modified": "modified",
    # "changed" is ambiguous — count it as modified (the most common reading)
    "changed": "modified",
}


def _side_has_verb(facts: DiffFacts, side: str, verb_key: str) -> bool:
    counts = facts[side]  # type: ignore[literal-required]
    return int(counts[verb_key]) > 0


def check_rationale_against_facts(
    rationale: str,
    facts: DiffFacts,
) -> list[str]:
    if not rationale or not rationale.strip():
        return []
    warnings: list[str] = []
    seen: set[tuple[str, str]] = set()

    for match in _VERB_CLAIM.finditer(rationale):
        side_phrase = match.group(1).lower().strip()
        verb = match.group(2).lower()
        verb_key = _VERB_TO_KEY[verb]

        sides: tuple[str, ...]
        if side_phrase.startswith("both"):
            sides = ("fork_side", "upstream_side")
        elif side_phrase == "fork":
            sides = ("fork_side",)
        else:
            sides = ("upstream_side",)

        for side in sides:
            if (side, verb_key) in seen:
                continue
            if _side_has_verb(facts, side, verb_key):
                continue
            seen.add((side, verb_key))
            side_label = "fork" if side == "fork_side" else "upstream"
            counts = facts[side]  # type: ignore[literal-required]
            warnings.append(
                f"Rationale claims {side_label} {verb}, but diff facts show "
                f"no {verb_key} groups on {side_label} side "
                f"(added={counts['added']}, removed={counts['removed']}, "
                f"modified={counts['modified']})."
            )
    return warnings
