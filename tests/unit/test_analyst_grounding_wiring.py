"""PR-A Slice 3: ConflictAnalystAgent.analyze_file populates grounding_warnings.

The pure helper + new model field are wired together here. After the LLM
returns and parse_conflict_analysis builds a ConflictAnalysis, the agent
scans the rationale against the ORIGINAL (pre-staging) fork and upstream
content and writes any fabricated qualified references into
``analysis.grounding_warnings``. The fallback path (LLM exception) leaves
the empty default untouched.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from src.agents.conflict_analyst_agent import ConflictAnalystAgent
from src.models.config import AgentLLMConfig
from src.models.conflict import ConflictAnalysis, ConflictType
from src.models.decision import MergeDecision
from src.models.diff import FileDiff, FileStatus, RiskLevel


def _make_agent() -> ConflictAnalystAgent:
    cfg = AgentLLMConfig(
        provider="anthropic",
        model="test-model",
        api_key_env="ANTHROPIC_API_KEY",
        max_retries=1,
    )
    return ConflictAnalystAgent(cfg)


def _make_file_diff(path: str = "packages/zod/src/v4/classic/schemas.ts") -> FileDiff:
    return FileDiff(
        file_path=path,
        file_status=FileStatus.MODIFIED,
        risk_level=RiskLevel.AUTO_RISKY,
        risk_score=0.4,
        lines_added=10,
        lines_deleted=5,
        lines_changed=10,
    )


def _make_analysis_with_rationale(rationale: str) -> ConflictAnalysis:
    return ConflictAnalysis(
        file_path="packages/zod/src/v4/classic/schemas.ts",
        conflict_points=[],
        overall_confidence=0.82,
        recommended_strategy=MergeDecision.SEMANTIC_MERGE,
        conflict_type=ConflictType.REFACTOR_VS_FEATURE,
        rationale=rationale,
        confidence=0.82,
    )


_FORK = (
    "import * as iso from './iso';\n"
    "import * as core from './core';\n"
    "inst.datetime = (p) => inst.check(core._isoDateTime(p));\n"
    "inst.week = (p) => inst.check(iso.week(p));\n"
)
_UPSTREAM = (
    "import * as core from './core';\n"
    "inst.datetime = (p) => inst.check(core._isoDateTime(p));\n"
    "inst.duration = (p) => inst.check(core._isoDuration(p));\n"
)


class TestAnalystGroundingWiring:
    def setup_method(self) -> None:
        self.agent = _make_agent()

    def test_flags_fabricated_symbol_in_rationale(self) -> None:
        fd = _make_file_diff()
        analysis = _make_analysis_with_rationale(
            "Upstream refactored iso methods to use core._iso* directly. "
            "Merge needs fork's week() adapted (using core._isoWeek if available, "
            "or keeping iso.week)."
        )

        with (
            patch.object(
                self.agent,
                "_call_llm_with_retry",
                new=AsyncMock(return_value="{}"),
            ),
            patch(
                "src.agents.conflict_analyst_agent.parse_conflict_analysis",
                return_value=analysis,
            ),
        ):
            result = asyncio.get_event_loop().run_until_complete(
                self.agent.analyze_file(fd, None, _FORK, _UPSTREAM)
            )

        assert result.grounding_warnings == ["core._isoWeek"]

    def test_no_warning_when_rationale_is_grounded(self) -> None:
        fd = _make_file_diff()
        analysis = _make_analysis_with_rationale(
            "Both sides keep iso.week so fork's feature survives the refactor."
        )

        with (
            patch.object(
                self.agent,
                "_call_llm_with_retry",
                new=AsyncMock(return_value="{}"),
            ),
            patch(
                "src.agents.conflict_analyst_agent.parse_conflict_analysis",
                return_value=analysis,
            ),
        ):
            result = asyncio.get_event_loop().run_until_complete(
                self.agent.analyze_file(fd, None, _FORK, _UPSTREAM)
            )

        assert result.grounding_warnings == []

    def test_llm_failure_fallback_has_empty_warnings(self) -> None:
        fd = _make_file_diff()

        with patch.object(
            self.agent,
            "_call_llm_with_retry",
            new=AsyncMock(side_effect=RuntimeError("API error")),
        ):
            result = asyncio.get_event_loop().run_until_complete(
                self.agent.analyze_file(fd, None, _FORK, _UPSTREAM)
            )

        assert result.recommended_strategy == MergeDecision.ESCALATE_HUMAN
        assert result.grounding_warnings == []


class TestAnalystGroundingSeparatesRequiredApis:
    """PR-D-A.2: when the rationale uses the ``REQUIRES NEW API:``
    sentinel, the symbol must land in ``required_new_apis`` (info) and
    NOT in ``grounding_warnings`` (warn) — otherwise the reviewer sees
    the same fact twice with conflicting severity."""

    def setup_method(self) -> None:
        self.agent = _make_agent()

    def test_sentinel_symbol_lands_in_required_not_warnings(self) -> None:
        fd = _make_file_diff()
        analysis = _make_analysis_with_rationale(
            "Fork adds .week(). REQUIRES NEW API: core._isoWeek — would "
            "need to be added to core/api.ts. Alternative: keep iso.week."
        )

        with (
            patch.object(
                self.agent,
                "_call_llm_with_retry",
                new=AsyncMock(return_value="{}"),
            ),
            patch(
                "src.agents.conflict_analyst_agent.parse_conflict_analysis",
                return_value=analysis,
            ),
        ):
            result = asyncio.get_event_loop().run_until_complete(
                self.agent.analyze_file(fd, None, _FORK, _UPSTREAM)
            )

        assert result.required_new_apis == ["core._isoWeek"]
        assert result.grounding_warnings == []

    def test_fabricated_symbol_without_sentinel_still_warns(self) -> None:
        fd = _make_file_diff()
        analysis = _make_analysis_with_rationale(
            # No sentinel — this is genuine sneaky fabrication and must
            # still raise the warning channel.
            "Upstream refactored to core._iso*; fork's .week() should "
            "use core._isoWeek transparently."
        )

        with (
            patch.object(
                self.agent,
                "_call_llm_with_retry",
                new=AsyncMock(return_value="{}"),
            ),
            patch(
                "src.agents.conflict_analyst_agent.parse_conflict_analysis",
                return_value=analysis,
            ),
        ):
            result = asyncio.get_event_loop().run_until_complete(
                self.agent.analyze_file(fd, None, _FORK, _UPSTREAM)
            )

        assert result.required_new_apis == []
        assert result.grounding_warnings == ["core._isoWeek"]

    def test_mixed_sentinel_and_fabrication(self) -> None:
        fd = _make_file_diff()
        # One symbol declared via sentinel; another sneakily fabricated.
        # Each lands in its own bucket.
        analysis = _make_analysis_with_rationale(
            "REQUIRES NEW API: core._isoWeek — declared.\n"
            "Also we can probably use core._bogusOther directly."
        )

        with (
            patch.object(
                self.agent,
                "_call_llm_with_retry",
                new=AsyncMock(return_value="{}"),
            ),
            patch(
                "src.agents.conflict_analyst_agent.parse_conflict_analysis",
                return_value=analysis,
            ),
        ):
            result = asyncio.get_event_loop().run_until_complete(
                self.agent.analyze_file(fd, None, _FORK, _UPSTREAM)
            )

        assert result.required_new_apis == ["core._isoWeek"]
        assert result.grounding_warnings == ["core._bogusOther"]


class TestAnalystGroundingWiringCommitRound:
    """The production conflict_analysis phase calls ``analyze_commit_round``
    (a multi-file batched analysis), not ``analyze_file`` — so grounding
    must wire through both entry points or the warnings stay empty in real
    runs (observed on the zod run: rationale said "core._isoWeek" but
    grounding_warnings=[] because this path was uncovered)."""

    def setup_method(self) -> None:
        self.agent = _make_agent()

    def test_commit_round_flags_fabricated_symbol(self) -> None:
        # ``analyze_commit_round`` receives per-file three-way content as
        # ``file_three_way: dict[fp, (base, fork, upstream)]``.
        file_three_way = {
            "packages/zod/src/v4/classic/schemas.ts": (None, _FORK, _UPSTREAM),
        }
        languages = {"packages/zod/src/v4/classic/schemas.ts": "typescript"}
        fabricated = _make_analysis_with_rationale(
            "Upstream refactored iso methods to use core._iso* directly. "
            "Fork added .week(). Use core._isoWeek if available."
        )

        with (
            patch.object(
                self.agent,
                "_call_llm_with_retry",
                new=AsyncMock(return_value="{}"),
            ),
            patch(
                "src.agents.conflict_analyst_agent.parse_commit_round_analyses",
                return_value={
                    "packages/zod/src/v4/classic/schemas.ts": fabricated,
                },
            ),
        ):
            result = asyncio.get_event_loop().run_until_complete(
                self.agent.analyze_commit_round(
                    round_commits=[{"sha": "abc", "files": list(file_three_way)}],
                    file_three_way=file_three_way,
                    file_languages=languages,
                )
            )

        fp = "packages/zod/src/v4/classic/schemas.ts"
        assert result[fp].grounding_warnings == ["core._isoWeek"]
