"""Per-file fallback path must fetch three-way content from git.

Regression test: the phase used ``hasattr(state, "_merge_base")`` to
gate the git_tool call, but ``MergeState`` exposes the merge base as
``merge_base_commit``. ``hasattr`` returned False on every modern
pydantic state, so the per-file path called ``analyze_file`` with
``base_content=current_content=target_content=None``. The LLM then
correctly reported "Not available" for all three sides and produced
abstract rationales.

This test pins the fix: when the phase is configured with a real
merge_base_commit and a git_tool that returns content, those values
must reach ``analyze_file``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.phases.conflict_analysis import ConflictAnalysisPhase
from src.models.config import MergeConfig
from src.models.conflict import ConflictAnalysis, ConflictType
from src.models.decision import MergeDecision
from src.models.diff import FileDiff, FileStatus, RiskLevel
from src.models.state import MergeState


@pytest.mark.asyncio
async def test_per_file_path_fetches_three_way_from_git_tool() -> None:
    fp = "versions.ts"
    state = MergeState(
        config=MergeConfig(
            upstream_ref="test/upstream",
            fork_ref="test/fork",
        )
    )
    state.merge_base_commit = "deadbeef"
    state.file_diffs = [
        FileDiff(
            file_path=fp,
            file_status=FileStatus.MODIFIED,
            hunks=[],
            lines_added=1,
            lines_deleted=1,
            risk_score=0.5,
            risk_level=RiskLevel.AUTO_RISKY,
        )
    ]
    state.pending_conflict_files = [fp]

    git_tool = MagicMock()
    git_tool.get_three_way_diff = MagicMock(
        return_value=("base body\n", "fork body\n", "upstream body\n")
    )

    analyst = MagicMock()
    analyst.git_tool = git_tool
    analyst.consecutive_failures = 0
    captured: dict[str, object] = {}

    async def fake_analyze_file(*args, **kwargs):
        captured["base_content"] = kwargs.get("base_content")
        captured["current_content"] = kwargs.get("current_content")
        captured["target_content"] = kwargs.get("target_content")
        return ConflictAnalysis(
            file_path=fp,
            conflict_points=[],
            overall_confidence=0.5,
            recommended_strategy=MergeDecision.TAKE_TARGET,
            conflict_type=ConflictType.CONCURRENT_MODIFICATION,
            rationale="ok",
            confidence=0.5,
        )

    analyst.analyze_file = AsyncMock(side_effect=fake_analyze_file)
    analyst.analyze_commit_round = AsyncMock(return_value={})

    executor = MagicMock()
    executor.execute = AsyncMock()
    executor.execute_auto_merge = AsyncMock(return_value=MagicMock())
    executor.execute_semantic_merge = AsyncMock(return_value=MagicMock())

    ctx = MagicMock()
    ctx.agents = {"conflict_analyst": analyst, "executor": executor}
    ctx.git_tool = git_tool
    ctx.notify = MagicMock()

    phase = ConflictAnalysisPhase()
    await phase.execute(state, ctx)

    assert captured.get("base_content") == "base body\n", (
        f"expected base content from git_tool, got {captured.get('base_content')!r}"
    )
    assert captured.get("current_content") == "fork body\n"
    assert captured.get("target_content") == "upstream body\n"
    git_tool.get_three_way_diff.assert_called_with(
        "deadbeef", "test/fork", "test/upstream", fp
    )
