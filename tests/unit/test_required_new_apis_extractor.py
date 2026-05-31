"""PR-D-A.2 Slice 1: parse `REQUIRES NEW API: <symbol>` sentinels.

The analyst prompt forces the LLM to declare missing symbols via the
``REQUIRES NEW API: <symbol>`` sentinel (PR-D-A.1). This extractor pulls
those symbols out so they can be displayed as a distinct (informational,
not warning) UI section, separate from PR-A's grounding_warnings for
genuine fabrication.

We keep the regex strict on the sentinel marker (uppercase, exact
phrasing — that's what the prompt mandates) but tolerant on the symbol
shape (qualified refs like ``core._isoWeek``, plain identifiers).
"""

from __future__ import annotations

from src.tools.required_new_apis import extract_required_new_apis


class TestExtractRequiredNewApis:
    def test_single_sentinel_extracts_symbol(self) -> None:
        rationale = (
            "Fork adds .week(). REQUIRES NEW API: core._isoWeek — would "
            "need to exist in core for the fork's week feature to follow "
            "upstream's pattern."
        )
        assert extract_required_new_apis(rationale) == ["core._isoWeek"]

    def test_multiple_sentinels_all_collected(self) -> None:
        rationale = (
            "REQUIRES NEW API: core._isoWeek — for the week method.\n"
            "REQUIRES NEW API: core._isoQuarter — for the quarter method."
        )
        assert extract_required_new_apis(rationale) == [
            "core._isoWeek",
            "core._isoQuarter",
        ]

    def test_no_sentinel_returns_empty(self) -> None:
        rationale = "Keep iso.week — it already works on both sides."
        assert extract_required_new_apis(rationale) == []

    def test_empty_rationale_returns_empty(self) -> None:
        assert extract_required_new_apis("") == []

    def test_qualified_ref_with_dot(self) -> None:
        # Symbol shape must allow dotted member access (``core._isoWeek``)
        # since that is the exact form the prompt's example uses.
        rationale = "REQUIRES NEW API: schemas.$ZodISOWeek for the class."
        assert extract_required_new_apis(rationale) == ["schemas.$ZodISOWeek"]

    def test_backtick_wrapped_symbol(self) -> None:
        # Observed in zod E2E: the LLM tends to wrap the symbol in
        # backticks (markdown code style) — the extractor must look
        # through the wrapping rather than treating the backtick as
        # part of the name.
        rationale = (
            "REQUIRES NEW API: `core._isoWeek` — needed to follow upstream's pattern."
        )
        assert extract_required_new_apis(rationale) == ["core._isoWeek"]

    def test_duplicate_sentinels_deduped(self) -> None:
        rationale = (
            "REQUIRES NEW API: core._isoWeek — first mention.\n"
            "REQUIRES NEW API: core._isoWeek — restated later."
        )
        assert extract_required_new_apis(rationale) == ["core._isoWeek"]
