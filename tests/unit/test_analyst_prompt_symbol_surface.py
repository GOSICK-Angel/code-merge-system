"""PR-D-B.3: ``build_conflict_analysis_prompt`` injects the ``Imported
Symbol Surface`` block when callers pass ``imported_symbols``.

Combined with PR-D-A's GROUNDING RULES, this is the second half of the
root-cause fix: rather than relying on the LLM to pattern-complete a
symmetric name, hand it the actual list of symbols each imported
module exposes. The LLM should then either pick from the list, or
flag the gap via ``REQUIRES NEW API`` — fabrication has no foothold.

When the caller passes nothing (or an empty dict), behaviour is
unchanged so existing callers don't break.
"""

from __future__ import annotations

from src.llm.prompts.analyst_prompts import build_conflict_analysis_prompt
from src.models.diff import FileDiff, FileStatus, RiskLevel


def _fd(path: str = "packages/zod/src/v4/classic/schemas.ts") -> FileDiff:
    return FileDiff(
        file_path=path,
        file_status=FileStatus.MODIFIED,
        risk_level=RiskLevel.AUTO_RISKY,
        risk_score=0.4,
        lines_added=2,
        lines_deleted=0,
        lines_changed=2,
    )


class TestImportedSymbolSurfaceInPrompt:
    def test_injects_block_when_symbols_provided(self) -> None:
        symbols = {
            "../core/api.js": [
                "_isoDateTime",
                "_isoDate",
                "_isoTime",
                "_isoDuration",
            ],
            "./iso.js": ["datetime", "date", "time", "duration"],
        }
        prompt = build_conflict_analysis_prompt(
            _fd(), None, "current", "target", "", imported_symbols=symbols
        )
        assert "Imported Symbol Surface" in prompt
        # Each module path must appear so the LLM can ground qualified
        # refs back to the source.
        assert "../core/api.js" in prompt
        assert "./iso.js" in prompt
        # Concrete symbol names so the LLM can verify before referencing.
        assert "_isoDateTime" in prompt
        assert "datetime" in prompt

    def test_block_lists_symbols_per_module(self) -> None:
        # Symbols belonging to a given import path must appear together
        # (so the LLM can resolve ``core._isoX`` → which path provides X)
        # rather than scattered with no module attribution.
        symbols = {"../core/api.js": ["_isoDateTime"]}
        prompt = build_conflict_analysis_prompt(
            _fd(), None, "current", "target", "", imported_symbols=symbols
        )
        # Find the module line; the symbol should appear within ~200
        # chars of it (allows for bullet/dash formatting).
        idx = prompt.index("../core/api.js")
        nearby = prompt[idx : idx + 200]
        assert "_isoDateTime" in nearby

    def test_no_block_when_symbols_empty_or_omitted(self) -> None:
        # Omitting the kwarg keeps existing callers unaffected; passing
        # an empty dict also yields no block (cleaner than an empty
        # header).
        prompt_omitted = build_conflict_analysis_prompt(
            _fd(), None, "current", "target", ""
        )
        prompt_empty = build_conflict_analysis_prompt(
            _fd(), None, "current", "target", "", imported_symbols={}
        )
        assert "Imported Symbol Surface" not in prompt_omitted
        assert "Imported Symbol Surface" not in prompt_empty

    def test_module_resolved_but_no_exports_renders_explicit_note(self) -> None:
        # If a module resolved but exposed nothing (e.g. re-export only,
        # or our regex missed the syntax), the LLM should still see that
        # the module was checked — otherwise it may assume the gap
        # means "the symbol probably exists, I just don't see it".
        symbols = {"./empty.js": []}
        prompt = build_conflict_analysis_prompt(
            _fd(), None, "current", "target", "", imported_symbols=symbols
        )
        assert "./empty.js" in prompt
        # An explicit marker so the LLM does not silently assume.
        assert "no exports" in prompt.lower() or "(none)" in prompt.lower()
