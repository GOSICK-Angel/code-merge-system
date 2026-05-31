"""Deterministic verb-counts for a three-way diff (PR-C).

The conflict_analyst prompt has historically given the LLM raw +N/-M
line counts. The model occasionally interpreted a "+1/-1" hunk as
"added + removed" instead of "modified" — the failure mode observed
on the zod merge (versions.ts: upstream replaced the `patch` field
in place, but the rationale claimed "both sides added entries").

``compute_diff_facts`` returns semantic per-side counts derived from
``difflib.SequenceMatcher.get_opcodes()`` so the prompt can inject
ground-truth verbs ("added" / "removed" / "modified") and a post-LLM
checker can compare the rationale's wording to facts the LLM was
*shown*.

Counts are per opcode group, not per line — `replace` of N base
lines by M new lines counts as ONE modification, matching how a
reviewer reads "we changed this stanza".
"""

from __future__ import annotations

import difflib
from typing import Literal, TypedDict


class _SideCounts(TypedDict):
    added: int
    removed: int
    modified: int


class DiffFacts(TypedDict):
    fork_side: _SideCounts
    upstream_side: _SideCounts


_ZERO: _SideCounts = {"added": 0, "removed": 0, "modified": 0}


def _opcode_counts(left: str | None, right: str | None) -> _SideCounts:
    left_lines = (left or "").splitlines()
    right_lines = (right or "").splitlines()
    matcher = difflib.SequenceMatcher(a=left_lines, b=right_lines, autojunk=False)
    added = 0
    removed = 0
    modified = 0
    for tag, _i1, _i2, _j1, _j2 in matcher.get_opcodes():
        if tag == "insert":
            added += 1
        elif tag == "delete":
            removed += 1
        elif tag == "replace":
            modified += 1
    return {"added": added, "removed": removed, "modified": modified}


def compute_diff_facts(
    base: str | None,
    fork: str | None,
    upstream: str | None,
) -> DiffFacts:
    return {
        "fork_side": _opcode_counts(base, fork),
        "upstream_side": _opcode_counts(base, upstream),
    }


def format_diff_facts(facts: DiffFacts, side: Literal["fork", "upstream"]) -> str:
    """Render one side's counts as a short human-readable string."""
    s = facts["fork_side"] if side == "fork" else facts["upstream_side"]
    return (
        f"{s['added']} added group(s), {s['removed']} removed group(s), "
        f"{s['modified']} modified group(s)"
    )
