"""PR-A Slice 1: pure-helper grounding scan for analyst rationale text.

The conflict_analyst's free-text ``rationale`` field occasionally invents a
``base.member`` reference (the zod run produced "use core._isoWeek if
available" with no such symbol in either fork or upstream). This helper
scans the rationale for fabricated qualified references so the UI can warn
the reviewer before they act on the recommendation.

Behaviour mirrors the existing ``find_invented_member_accesses`` (a real
``base`` is required so brand-new imports and English noise stay quiet),
but the input is rationale prose, not merged code.
"""

from __future__ import annotations

from src.tools.hallucinated_symbol_guard import scan_rationale_for_hallucinations


class TestScanRationaleForHallucinations:
    def test_flags_fabricated_member_on_real_base(self) -> None:
        fork = (
            "import * as core from './core';\n"
            "inst.datetime = (p) => inst.check(core._isoDateTime(p));\n"
        )
        upstream = (
            "import * as core from './core';\n"
            "inst.datetime = (p) => inst.check(core._isoDateTime(p));\n"
            "inst.duration = (p) => inst.check(core._isoDuration(p));\n"
        )
        rationale = (
            "Upstream refactored iso methods to use core._iso* directly. "
            "Merge needs upstream's refactor plus fork's week() adapted "
            "(using core._isoWeek if available, or keeping iso.week)."
        )
        assert scan_rationale_for_hallucinations(
            rationale, [fork, upstream], "packages/zod/src/v4/classic/schemas.ts"
        ) == ["core._isoWeek"]

    def test_recombined_reference_not_flagged(self) -> None:
        fork = "inst.week = (params) => inst.check(iso.week(params));\n"
        upstream = "import * as iso from './iso';\n"
        # ``iso.week`` exists verbatim in fork — faithful recombination.
        rationale = "The fork's iso.week call still works after the refactor."
        assert (
            scan_rationale_for_hallucinations(rationale, [fork, upstream], "schemas.ts")
            == []
        )

    def test_brand_new_base_not_flagged(self) -> None:
        # ``lodash`` appears in no source — the rationale proposes a new
        # dependency, not a fabricated member on an existing one.
        fork = "const a = 1;\n"
        upstream = "const b = 2;\n"
        rationale = "Consider importing lodash.merge to deep-merge the configs."
        assert (
            scan_rationale_for_hallucinations(rationale, [fork, upstream], "x.ts") == []
        )

    def test_empty_rationale_returns_empty(self) -> None:
        assert scan_rationale_for_hallucinations("", ["core.foo\n"], "x.ts") == []

    def test_non_code_file_path_skipped(self) -> None:
        # File extension gate: rationale for a JSON/YAML/MD file does not
        # use ``.`` as member access, so we do not scan it (avoids
        # flagging prose like ``version.major``).
        rationale = "core._isoWeek is missing"
        assert (
            scan_rationale_for_hallucinations(rationale, ["core.foo\n"], "x.json") == []
        )
