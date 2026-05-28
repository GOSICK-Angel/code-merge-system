"""PR-B Slice 2: analyst prompt declares semantic_compatibility field.

Both prompt builders (per-file and commit-round batched) must:
  1. document the three-state semantic_compatibility field
  2. enumerate all three states by name
  3. forbid vague rationale boilerplate (the "comparable changes"
     anti-pattern observed in the zod E2E)
"""

from __future__ import annotations

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


def _per_file_prompt() -> str:
    return build_conflict_analysis_prompt(
        _diff(),
        base_content="b",
        current_content="c",
        target_content="t",
        project_context="",
    )


def _round_prompt() -> str:
    return build_commit_round_prompt(
        round_commits=[{"sha": "abc12345", "message": "x", "files": ["src/x.ts"]}],
        file_three_way={"src/x.ts": ("b", "c", "t")},
        file_languages={"src/x.ts": "typescript"},
        project_context="",
    )


class TestPerFilePromptDeclaresSemanticCompatibility:
    def test_field_name_appears(self) -> None:
        assert "semantic_compatibility" in _per_file_prompt()

    def test_all_three_states_enumerated(self) -> None:
        prompt = _per_file_prompt()
        assert "compatible" in prompt
        assert "incompatible" in prompt
        assert "orthogonal" in prompt

    def test_forbids_vague_rationale_boilerplate(self) -> None:
        prompt = _per_file_prompt()
        lowered = prompt.lower()
        assert "comparable" in lowered
        assert "specific" in lowered


class TestCommitRoundPromptDeclaresSemanticCompatibility:
    def test_field_name_appears(self) -> None:
        assert "semantic_compatibility" in _round_prompt()

    def test_all_three_states_enumerated(self) -> None:
        prompt = _round_prompt()
        assert "compatible" in prompt
        assert "incompatible" in prompt
        assert "orthogonal" in prompt
