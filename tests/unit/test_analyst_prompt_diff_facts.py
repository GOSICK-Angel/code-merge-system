"""PR-C Slice 2: analyst prompts inject deterministic diff facts.

Both prompt builders accept a ``diff_facts`` kwarg and embed a
"# Deterministic Diff Facts" block listing how many add/remove/modify
groups each side actually has, plus a directive to match those verbs
in the rationale (no "added" if facts say modified=N, etc.).

Passing None preserves the pre-PR-C behaviour (no block) so the
existing test corpus stays green until callers are wired (Slice 4).
"""

from __future__ import annotations

from src.llm.prompts.analyst_prompts import (
    build_commit_round_prompt,
    build_conflict_analysis_prompt,
)
from src.models.diff import FileDiff, FileStatus, RiskLevel
from src.tools.diff_facts import DiffFacts


def _diff() -> FileDiff:
    return FileDiff(
        file_path="src/x.ts",
        file_status=FileStatus.MODIFIED,
        hunks=[],
        lines_added=1,
        lines_deleted=1,
        risk_score=0.5,
        risk_level=RiskLevel.AUTO_RISKY,
    )


def _facts() -> DiffFacts:
    # upstream modified in place; fork untouched — the versions.ts shape.
    return {
        "fork_side": {"added": 0, "removed": 0, "modified": 0},
        "upstream_side": {"added": 0, "removed": 0, "modified": 1},
    }


class TestPerFilePromptInjectsFacts:
    def test_block_appears_when_facts_passed(self) -> None:
        prompt = build_conflict_analysis_prompt(
            _diff(), "b", "c", "t", "", diff_facts=_facts()
        )
        assert "Deterministic Diff Facts" in prompt

    def test_block_includes_per_side_counts(self) -> None:
        prompt = build_conflict_analysis_prompt(
            _diff(), "b", "c", "t", "", diff_facts=_facts()
        )
        assert "modified" in prompt.lower()
        # The fork-side line should mention zeros, upstream-side should
        # mention the 1 modified group — that's the ground truth.
        assert "0 added" in prompt or "0 modified" in prompt
        assert "1 modified" in prompt

    def test_block_has_verb_directive(self) -> None:
        prompt = build_conflict_analysis_prompt(
            _diff(), "b", "c", "t", "", diff_facts=_facts()
        )
        lowered = prompt.lower()
        # Must instruct the model to USE these verbs.
        assert "modified" in lowered
        assert "must" in lowered or "match" in lowered

    def test_no_facts_keeps_old_behaviour(self) -> None:
        prompt_with = build_conflict_analysis_prompt(
            _diff(), "b", "c", "t", "", diff_facts=_facts()
        )
        prompt_without = build_conflict_analysis_prompt(
            _diff(), "b", "c", "t", "", diff_facts=None
        )
        assert "Deterministic Diff Facts" in prompt_with
        assert "Deterministic Diff Facts" not in prompt_without


class TestCommitRoundPromptInjectsFacts:
    def test_block_appears_per_file(self) -> None:
        prompt = build_commit_round_prompt(
            round_commits=[{"sha": "abc12345", "message": "x", "files": ["src/x.ts"]}],
            file_three_way={"src/x.ts": ("b", "c", "t")},
            file_languages={"src/x.ts": "typescript"},
            project_context="",
            diff_facts_by_file={"src/x.ts": _facts()},
        )
        assert "Deterministic Diff Facts" in prompt
        assert "1 modified" in prompt

    def test_no_facts_keeps_old_behaviour(self) -> None:
        prompt = build_commit_round_prompt(
            round_commits=[{"sha": "abc12345", "message": "x", "files": ["src/x.ts"]}],
            file_three_way={"src/x.ts": ("b", "c", "t")},
            file_languages={"src/x.ts": "typescript"},
            project_context="",
            diff_facts_by_file=None,
        )
        assert "Deterministic Diff Facts" not in prompt
