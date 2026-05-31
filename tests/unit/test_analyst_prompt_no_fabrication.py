"""PR-D-A.1: ANALYST_SYSTEM forbids fabricated symbols + mandates a
"REQUIRES NEW API" sentinel phrase.

LLM rationale routinely invents qualified references that exist on
neither side (the zod run produced "use core._isoWeek if available" —
no such symbol in upstream core/api.ts). PR-A's grounding scan catches
these post-hoc; this prompt rule attacks the source by telling the
model to write an explicit "REQUIRES NEW API: <name>" sentinel when
the recommended merge would need a brand-new symbol, instead of the
weasel "if available / could use" phrasing.

We assert phrase markers (case-insensitive) so prompt rewordings
survive without test churn.
"""

from __future__ import annotations

import re

from src.llm.prompts.analyst_prompts import ANALYST_SYSTEM


class TestAnalystSystemNoFabrication:
    def test_grounding_rule_anchors_symbols_to_fork_or_upstream(self) -> None:
        # The rule must constrain every mentioned symbol to either appear
        # verbatim in fork/upstream OR be flagged via REQUIRES NEW API.
        # Match the positive constraint shape rather than a particular
        # negative wording so prompt rewrites don't churn the test.
        text = ANALYST_SYSTEM.lower()
        assert "fork or upstream" in text or "fork and upstream" in text, ANALYST_SYSTEM
        assert "verbatim" in text or "appears" in text, ANALYST_SYSTEM
        # No fabrication safety valve — the rule must say "no third option"
        # or otherwise close the loophole between (a) cite verbatim and
        # (b) write REQUIRES NEW API.
        assert (
            "no third option" in text or "exactly one" in text or "must hold" in text
        ), ANALYST_SYSTEM

    def test_mandates_requires_new_api_sentinel(self) -> None:
        # When the merge truly needs a new symbol, force the structured
        # sentinel so downstream tooling (grounding scan / UI) can pick
        # it up unambiguously — "if available" is unparseable noise.
        assert "REQUIRES NEW API" in ANALYST_SYSTEM, ANALYST_SYSTEM

    def test_explicitly_bans_hedge_phrases(self) -> None:
        # Catch the failure-mode phrasing directly. The rule should
        # name the weasel words it replaces so the LLM understands what
        # NOT to do.
        text = ANALYST_SYSTEM.lower()
        assert "if available" in text or "if exists" in text, ANALYST_SYSTEM

    def test_bans_the_observed_variant_if_it_exists(self) -> None:
        # First real run after D-A.1 still produced "core._isoWeek if it
        # exists" — the model treated "if exists" as not matching its
        # literal "if exists" ban. The rule must close this variant.
        assert "if it exists" in ANALYST_SYSTEM.lower(), ANALYST_SYSTEM

    def test_includes_concrete_wrong_right_example(self) -> None:
        # Positive instruction by example beats negative blocklists.
        # The prompt must show both a WRONG and a RIGHT rationale snippet
        # so the LLM can pattern-match the structured sentinel.
        text = ANALYST_SYSTEM.lower()
        assert "wrong" in text and "right" in text, ANALYST_SYSTEM
        # The right-side example must demonstrate the REQUIRES NEW API
        # sentinel actually being used in context.
        assert "requires new api" in text, ANALYST_SYSTEM
