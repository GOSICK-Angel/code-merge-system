"""PR-C Slice 3: post-LLM verb-vs-facts grounding check.

When the analyst's rationale claims "both sides added" but the
deterministic diff facts say one side actually *modified* in place,
that mismatch is fabrication of the same shape as the qualified-ref
fabrication PR-A catches. We surface it via the same channel
(``grounding_warnings``) so the reviewer sees both fabrication
categories side-by-side in the UI.

Scope of the check is conservative:
  - Only the three core verbs: added, removed, modified
  - Only triggered when the rationale unambiguously attributes the
    verb to a side ("upstream added", "fork removed", "both sides
    added") — to avoid flagging valid phrases like "the existing
    schema is added to the registry at startup"
  - Zero false positives when the rationale is empty / generic
"""

from __future__ import annotations

from src.tools.diff_facts import DiffFacts
from src.tools.diff_facts_grounding import check_rationale_against_facts


def _facts(
    fork: tuple[int, int, int] = (0, 0, 0),
    upstream: tuple[int, int, int] = (0, 0, 0),
) -> DiffFacts:
    return {
        "fork_side": {
            "added": fork[0],
            "removed": fork[1],
            "modified": fork[2],
        },
        "upstream_side": {
            "added": upstream[0],
            "removed": upstream[1],
            "modified": upstream[2],
        },
    }


class TestNoWarningOnConsistentRationale:
    def test_silent_when_rationale_matches_facts(self) -> None:
        facts = _facts(fork=(1, 0, 0), upstream=(0, 0, 1))
        rationale = (
            "Fork added a new helper. Upstream modified the existing "
            "regex in place. Both edits touch different regions."
        )
        assert check_rationale_against_facts(rationale, facts) == []

    def test_silent_on_empty_rationale(self) -> None:
        assert check_rationale_against_facts("", _facts()) == []

    def test_silent_when_no_side_attributed_verbs(self) -> None:
        # "the schema is added to the registry" — no fork/upstream attr
        rationale = "The schema is added to the registry at startup."
        assert check_rationale_against_facts(rationale, _facts()) == []


class TestWarningOnFabricatedVerbs:
    def test_flags_upstream_added_when_facts_say_modified(self) -> None:
        # versions.ts shape: upstream did a modify-in-place but rationale
        # claims it "added entries". This is the bug PR-C exists to catch.
        facts = _facts(upstream=(0, 0, 1))
        rationale = "Both sides added entries to the versions table."
        warnings = check_rationale_against_facts(rationale, facts)
        assert warnings, "expected at least one verb-mismatch warning"
        joined = " ".join(warnings).lower()
        assert "added" in joined
        assert "modified" in joined or "no added" in joined

    def test_flags_fork_removed_when_facts_say_no_remove(self) -> None:
        facts = _facts(fork=(0, 0, 1))
        rationale = "Fork removed the legacy parser entirely."
        warnings = check_rationale_against_facts(rationale, facts)
        assert warnings
        joined = " ".join(warnings).lower()
        assert "removed" in joined

    def test_does_not_flag_when_one_side_correct_other_silent(self) -> None:
        # Rationale only talks about the side that's correct.
        facts = _facts(fork=(1, 0, 0), upstream=(0, 0, 1))
        rationale = "Fork added a new helper at the bottom."
        warnings = check_rationale_against_facts(rationale, facts)
        assert warnings == []
