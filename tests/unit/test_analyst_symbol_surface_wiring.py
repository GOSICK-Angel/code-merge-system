"""PR-D-B.5/6: ConflictAnalystAgent runs the import-symbol harvester
and threads the result into prompt builders.

Both production entry points must be covered — analyze_file (single-shot
+ chunked) and analyze_commit_round (the actual prod path per
[[feedback_verify_real_run]]). Failure modes (no git_tool, no fork_ref,
harvest exception) must degrade silently to "no surface block" so
analysis still completes.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.conflict_analyst_agent import ConflictAnalystAgent
from src.models.config import AgentLLMConfig
from src.models.conflict import ConflictAnalysis, ConflictType
from src.models.decision import MergeDecision
from src.models.diff import FileDiff, FileStatus, RiskLevel


_FORK_WITH_IMPORT = (
    'import * as core from "../core/api.js";\ninst.datetime = core._isoDateTime(...);\n'
)
_UPSTREAM = _FORK_WITH_IMPORT


def _make_agent(git_tool: object | None = None) -> ConflictAnalystAgent:
    cfg = AgentLLMConfig(
        provider="anthropic",
        model="test-model",
        api_key_env="ANTHROPIC_API_KEY",
        max_retries=1,
    )
    return ConflictAnalystAgent(cfg, git_tool=git_tool)


def _fd() -> FileDiff:
    return FileDiff(
        file_path="packages/zod/src/v4/classic/schemas.ts",
        file_status=FileStatus.MODIFIED,
        risk_level=RiskLevel.AUTO_RISKY,
        risk_score=0.4,
        lines_added=2,
        lines_deleted=0,
        lines_changed=2,
    )


def _stub_analysis() -> ConflictAnalysis:
    return ConflictAnalysis(
        file_path="packages/zod/src/v4/classic/schemas.ts",
        conflict_points=[],
        overall_confidence=0.7,
        recommended_strategy=MergeDecision.SEMANTIC_MERGE,
        conflict_type=ConflictType.REFACTOR_VS_FEATURE,
        rationale="r",
        confidence=0.7,
    )


class TestAnalyzeFileInjectsSymbolSurface:
    def test_passes_imported_symbols_to_prompt_builder(self) -> None:
        git = MagicMock()
        # Resolved module exposes 4 _iso* helpers — analyst should see
        # the full list and NOT pattern-complete _isoWeek.
        git.get_file_content.return_value = (
            "export function _isoDateTime() {}\n"
            "export function _isoDate() {}\n"
            "export function _isoTime() {}\n"
            "export function _isoDuration() {}\n"
        )
        agent = _make_agent(git_tool=git)
        captured: dict[str, object] = {}

        def fake_build_prompt(*args: object, **kwargs: object) -> str:
            captured["imported_symbols"] = kwargs.get("imported_symbols")
            return "prompt"

        with (
            patch.object(
                agent, "_call_llm_with_retry", new=AsyncMock(return_value="{}")
            ),
            patch(
                "src.agents.conflict_analyst_agent.build_conflict_analysis_prompt",
                side_effect=fake_build_prompt,
            ),
            patch(
                "src.agents.conflict_analyst_agent.parse_conflict_analysis",
                return_value=_stub_analysis(),
            ),
        ):
            asyncio.get_event_loop().run_until_complete(
                agent.analyze_file(
                    _fd(),
                    None,
                    _FORK_WITH_IMPORT,
                    _UPSTREAM,
                    fork_ref="test/fork",
                )
            )

        symbols = captured.get("imported_symbols")
        assert symbols and "../core/api.js" in symbols
        assert "_isoDateTime" in symbols["../core/api.js"]
        assert "_isoWeek" not in symbols["../core/api.js"]

    def test_no_git_tool_degrades_silently(self) -> None:
        # No git_tool → harvest impossible → empty dict → no block.
        # Analysis still completes successfully.
        agent = _make_agent(git_tool=None)
        captured: dict[str, object] = {}

        def fake_build_prompt(*args: object, **kwargs: object) -> str:
            captured["imported_symbols"] = kwargs.get("imported_symbols")
            return "prompt"

        with (
            patch.object(
                agent, "_call_llm_with_retry", new=AsyncMock(return_value="{}")
            ),
            patch(
                "src.agents.conflict_analyst_agent.build_conflict_analysis_prompt",
                side_effect=fake_build_prompt,
            ),
            patch(
                "src.agents.conflict_analyst_agent.parse_conflict_analysis",
                return_value=_stub_analysis(),
            ),
        ):
            asyncio.get_event_loop().run_until_complete(
                agent.analyze_file(
                    _fd(),
                    None,
                    _FORK_WITH_IMPORT,
                    _UPSTREAM,
                    fork_ref="test/fork",
                )
            )

        # Empty dict means harvester ran but found nothing; either way
        # the block won't be rendered and analysis proceeds.
        assert not captured.get("imported_symbols")


class TestAnalyzeCommitRoundInjectsSymbolSurface:
    def test_passes_per_file_symbols_to_prompt_builder(self) -> None:
        git = MagicMock()
        git.get_file_content.return_value = (
            "export function _isoDateTime() {}\nexport function _isoDate() {}\n"
        )
        agent = _make_agent(git_tool=git)
        file_three_way = {
            "packages/zod/src/v4/classic/schemas.ts": (
                None,
                _FORK_WITH_IMPORT,
                _UPSTREAM,
            ),
        }
        captured: dict[str, object] = {}

        def fake_build_round(*args: object, **kwargs: object) -> str:
            captured["imported_symbols_by_file"] = kwargs.get(
                "imported_symbols_by_file"
            )
            return "prompt"

        stub = _stub_analysis()
        with (
            patch.object(
                agent, "_call_llm_with_retry", new=AsyncMock(return_value="{}")
            ),
            patch(
                "src.agents.conflict_analyst_agent.build_commit_round_prompt",
                side_effect=fake_build_round,
            ),
            patch(
                "src.agents.conflict_analyst_agent.parse_commit_round_analyses",
                return_value={"packages/zod/src/v4/classic/schemas.ts": stub},
            ),
        ):
            asyncio.get_event_loop().run_until_complete(
                agent.analyze_commit_round(
                    round_commits=[{"sha": "abc", "files": list(file_three_way)}],
                    file_three_way=file_three_way,
                    file_languages={
                        "packages/zod/src/v4/classic/schemas.ts": "typescript"
                    },
                    fork_ref="test/fork",
                )
            )

        by_file = captured.get("imported_symbols_by_file") or {}
        fp = "packages/zod/src/v4/classic/schemas.ts"
        assert fp in by_file
        assert "../core/api.js" in by_file[fp]
        assert "_isoDateTime" in by_file[fp]["../core/api.js"]
