"""Batch A / P0-1: the Executor — the agent that actually writes merged
code — must carry the same anti-fabrication grounding as ConflictAnalyst.

ConflictAnalyst (analyst_prompts) already refuses to invent symbols and is
handed an Imported Symbol Surface. The Executor previously had neither, so a
hallucinated symbol could land verbatim in the committed file (root cause of
the zod compilation break). These tests pin:

* EXECUTOR_SYSTEM states the no-fabrication discipline, phrased for an agent
  that emits raw file content (it cannot leave a ``REQUIRES NEW API`` note like
  the analyst does);
* ``build_semantic_merge_prompt`` injects the symbol surface when given one,
  and is byte-for-byte unchanged when not (backwards compatibility).
"""

from __future__ import annotations

from src.llm.prompts.executor_prompts import (
    EXECUTOR_SYSTEM,
    build_semantic_merge_prompt,
)
from src.models.conflict import ConflictAnalysis, ConflictType
from src.models.decision import MergeDecision
from src.models.diff import FileChangeCategory, FileDiff, FileStatus, RiskLevel


def _fd(path: str = "packages/zod/src/v4/classic/schemas.ts") -> FileDiff:
    return FileDiff(
        file_path=path,
        change_category=FileChangeCategory.C,
        file_status=FileStatus.MODIFIED,
        risk_level=RiskLevel.AUTO_RISKY,
        risk_score=0.4,
        language="typescript",
    )


def _ca() -> ConflictAnalysis:
    return ConflictAnalysis(
        file_path="packages/zod/src/v4/classic/schemas.ts",
        conflict_points=[],
        overall_confidence=0.8,
        conflict_type=ConflictType.CONCURRENT_MODIFICATION,
        recommended_strategy=MergeDecision.SEMANTIC_MERGE,
        rationale="combine fork iso helpers with upstream refactor",
    )


class TestExecutorSystemGrounding:
    def test_system_forbids_fabricating_symbols(self) -> None:
        lowered = EXECUTOR_SYSTEM.lower()
        # Discipline must be present in some recognisable form.
        assert "fabricat" in lowered or "invent" in lowered
        # The specific symmetric-name failure mode is the one that broke zod.
        assert "symbol" in lowered

    def test_system_does_not_tell_executor_to_emit_requires_new_api(self) -> None:
        # The analyst may output ``REQUIRES NEW API: <symbol>``; the executor
        # emits ONLY raw file content, so instructing it to write such a note
        # would corrupt the file. The grounding must be phrased for raw output.
        assert "REQUIRES NEW API" not in EXECUTOR_SYSTEM


class TestSemanticMergeSymbolSurface:
    def test_injects_surface_when_symbols_provided(self) -> None:
        symbols = {
            "../core/api.js": ["_isoDateTime", "_isoDate", "_isoTime"],
            "./iso.js": ["datetime", "date", "time"],
        }
        prompt = build_semantic_merge_prompt(
            _fd(), _ca(), "current", "target", "", imported_symbols=symbols
        )
        assert "Imported Symbol Surface" in prompt
        assert "../core/api.js" in prompt
        assert "_isoDateTime" in prompt
        assert "datetime" in prompt

    def test_module_with_no_exports_renders_explicit_note(self) -> None:
        prompt = build_semantic_merge_prompt(
            _fd(), _ca(), "current", "target", "", imported_symbols={"./empty.js": []}
        )
        assert "./empty.js" in prompt
        assert "no exports" in prompt.lower() or "(none)" in prompt.lower()

    def test_no_surface_when_omitted_or_empty_is_backwards_compatible(self) -> None:
        baseline = build_semantic_merge_prompt(_fd(), _ca(), "current", "target", "")
        empty = build_semantic_merge_prompt(
            _fd(), _ca(), "current", "target", "", imported_symbols={}
        )
        assert "Imported Symbol Surface" not in baseline
        # Empty dict must not change the prompt versus omitting the kwarg.
        assert empty == baseline
