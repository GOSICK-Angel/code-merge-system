"""Prompts must replace the misleading ``Conflict count: 0`` line with
a fact-grounded ``Native 3-way merge: <outcome>`` line.

Background: ``FileDiff.conflict_count`` is computed against the original
refs (clean branches → always 0) and the LLM read it as "no conflict",
abandoning specific analysis. We instead inject the result of an actual
``git merge-file`` so the model sees ground truth.
"""

from __future__ import annotations

import pytest

from src.llm.prompts.analyst_prompts import (
    build_commit_round_prompt,
    build_conflict_analysis_prompt,
)
from src.models.diff import FileDiff, FileStatus, RiskLevel


def _diff() -> FileDiff:
    return FileDiff(
        file_path="src/x.ts",
        file_status=FileStatus.MODIFIED,
        hunks=[],
        lines_added=2,
        lines_deleted=1,
        risk_score=0.5,
        risk_level=RiskLevel.AUTO_RISKY,
    )


class TestPerFilePromptOutcomeBlock:
    @pytest.mark.parametrize("outcome", ["conflict", "clean", "missing"])
    def test_outcome_line_appears(self, outcome: str) -> None:
        prompt = build_conflict_analysis_prompt(
            _diff(), "b", "c", "t", "", native_3way_outcome=outcome
        )
        assert "Native 3-way merge" in prompt
        assert outcome.upper() in prompt or outcome in prompt

    def test_conflict_outcome_explains_why_llm_called(self) -> None:
        prompt = build_conflict_analysis_prompt(
            _diff(), "b", "c", "t", "", native_3way_outcome="conflict"
        )
        lowered = prompt.lower()
        # The LLM needs to know that "no conflict markers in refs" is
        # expected and not evidence the file is conflict-free.
        assert "markers" in lowered or "would produce" in lowered

    def test_misleading_conflict_count_line_removed_or_rephrased(self) -> None:
        # The old line "Conflict count: 0" must not appear standalone — it
        # gave the LLM a fake "no conflict" signal.
        prompt = build_conflict_analysis_prompt(
            _diff(), "b", "c", "t", "", native_3way_outcome="conflict"
        )
        # Either the field is gone or contextualized (mentions "pre-existing"
        # or "in refs"). Bare "Conflict count: 0" with no qualifier is a fail.
        bad = "Conflict count: 0\n"
        if bad in prompt:
            # Allow only if it's clearly contextualized on the same line.
            line = next(
                (l for l in prompt.splitlines() if l.startswith("Conflict count:")),
                "",
            )
            assert "pre-existing" in line.lower() or "ref" in line.lower(), (
                f"bare misleading line still present: {line!r}"
            )

    def test_no_kwarg_keeps_old_behaviour(self) -> None:
        prompt_default = build_conflict_analysis_prompt(_diff(), "b", "c", "t", "")
        assert "Native 3-way merge" not in prompt_default


class TestCommitRoundPromptOutcomeBlock:
    def test_per_file_outcome_in_prompt(self) -> None:
        prompt = build_commit_round_prompt(
            round_commits=[{"sha": "abc12345", "message": "x", "files": ["src/x.ts"]}],
            file_three_way={"src/x.ts": ("b", "c", "t")},
            file_languages={"src/x.ts": "typescript"},
            project_context="",
            native_3way_outcome_by_file={"src/x.ts": "conflict"},
        )
        assert "Native 3-way merge" in prompt
        assert "CONFLICT" in prompt or "conflict" in prompt

    def test_no_kwarg_keeps_old_behaviour(self) -> None:
        prompt = build_commit_round_prompt(
            round_commits=[{"sha": "abc12345", "message": "x", "files": ["src/x.ts"]}],
            file_three_way={"src/x.ts": ("b", "c", "t")},
            file_languages={"src/x.ts": "typescript"},
            project_context="",
        )
        assert "Native 3-way merge" not in prompt
