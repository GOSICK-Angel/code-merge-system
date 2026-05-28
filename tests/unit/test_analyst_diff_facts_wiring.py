"""PR-C Slice 4: conflict_analyst threads diff_facts end-to-end.

Two pieces of wiring tested here:

1. ``_with_grounding_warnings(facts=...)`` — when diff_facts are
   supplied AND the rationale's side-attributed verbs disagree with
   the facts, the returned analysis must carry verb-mismatch warnings
   in ``grounding_warnings`` (merged with any existing fabrication
   warnings).

2. ``analyze_file`` builds and passes diff_facts to
   ``build_conflict_analysis_prompt`` — the prompt the LLM sees has
   the deterministic block.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.conflict_analyst_agent import (
    ConflictAnalystAgent,
    _with_grounding_warnings,
)
from src.models.config import AgentLLMConfig
from src.models.conflict import ConflictAnalysis, ConflictType
from src.models.decision import MergeDecision
from src.models.diff import FileDiff, FileStatus, RiskLevel


def _analysis(rationale: str) -> ConflictAnalysis:
    return ConflictAnalysis(
        file_path="versions.ts",
        conflict_points=[],
        overall_confidence=0.5,
        recommended_strategy=MergeDecision.SEMANTIC_MERGE,
        conflict_type=ConflictType.UNKNOWN,
        rationale=rationale,
        confidence=0.5,
    )


class TestWithGroundingWarningsMergesVerbMismatches:
    def test_no_warning_when_no_facts_passed(self) -> None:
        # Backward compat: callers that don't pass facts get the old behaviour.
        out = _with_grounding_warnings(
            _analysis("Both sides added entries."),
            fork_content="x",
            upstream_content="x",
            file_path="versions.ts",
        )
        assert out.grounding_warnings == []

    def test_verb_mismatch_becomes_grounding_warning(self) -> None:
        # versions.ts shape: rationale claims both added, facts say upstream
        # modified in place.
        facts = {
            "fork_side": {"added": 0, "removed": 0, "modified": 0},
            "upstream_side": {"added": 0, "removed": 0, "modified": 1},
        }
        out = _with_grounding_warnings(
            _analysis("Both sides added entries to the versions table."),
            fork_content="x",
            upstream_content="x",
            file_path="versions.ts",
            diff_facts=facts,  # type: ignore[arg-type]
        )
        joined = " ".join(out.grounding_warnings).lower()
        assert "added" in joined

    def test_verb_mismatch_merges_with_existing_fabrication(self) -> None:
        # Both a fabricated symbol AND a wrong verb — both must appear.
        facts = {
            "fork_side": {"added": 0, "removed": 0, "modified": 0},
            "upstream_side": {"added": 0, "removed": 0, "modified": 1},
        }
        out = _with_grounding_warnings(
            _analysis("use core._isoWeek if available. Both sides added entries."),
            fork_content="const x = core._isoDate;",
            upstream_content="const x = core._isoDate;",
            file_path="versions.ts",
            diff_facts=facts,  # type: ignore[arg-type]
        )
        joined = " ".join(out.grounding_warnings).lower()
        # fabrication warning channel still fires (core exists, _isoWeek does not)
        assert any("_isoweek" in w.lower() for w in out.grounding_warnings)
        # verb-mismatch warning appended
        assert "added" in joined


@pytest.mark.asyncio
async def test_analyze_file_passes_diff_facts_to_prompt_builder() -> None:
    with patch("src.llm.client.LLMClientFactory.create"):
        agent = ConflictAnalystAgent(AgentLLMConfig())

    fd = FileDiff(
        file_path="versions.ts",
        file_status=FileStatus.MODIFIED,
        hunks=[],
        lines_added=1,
        lines_deleted=1,
        risk_score=0.5,
        risk_level=RiskLevel.AUTO_RISKY,
    )

    captured: dict[str, object] = {}

    def fake_build(file_diff, base, current, target, context, **kwargs) -> str:
        captured["diff_facts"] = kwargs.get("diff_facts")
        return "PROMPT"

    fake_parse = MagicMock(
        return_value=MagicMock(file_path="versions.ts", rationale="r")
    )
    agent._call_llm_with_retry = AsyncMock(return_value="{}")  # type: ignore[method-assign]
    with (
        patch(
            "src.agents.conflict_analyst_agent.build_conflict_analysis_prompt",
            side_effect=fake_build,
        ),
        patch(
            "src.agents.conflict_analyst_agent.parse_conflict_analysis",
            fake_parse,
        ),
    ):
        await agent.analyze_file(
            fd,
            base_content='v = "1.0.0"\n',
            current_content='v = "1.0.0"\n',
            target_content='v = "1.0.1"\n',
            project_context="",
        )

    facts = captured["diff_facts"]
    assert facts is not None
    assert isinstance(facts, dict)
    # upstream_side modified=1 reflects the in-place version bump
    assert facts["upstream_side"]["modified"] >= 1  # type: ignore[index]
