"""
Integration tests: PlannerJudge revision loop.

Covers two sub-scenarios:
1. One revision round: REVISION_NEEDED on round 0 → APPROVED on round 1 → COMPLETED
2. Max revision rounds exhausted (2 rounds) → AWAITING_HUMAN
"""

from unittest.mock import AsyncMock
import pytest

from src.core.orchestrator import Orchestrator
from src.models.state import MergeState, SystemStatus

from tests.integration.conftest import (
    PLAN_ALL_AUTO_SAFE,
    PLANNER_JUDGE_APPROVED_2,
    PLANNER_JUDGE_REVISION_NEEDED,
    JUDGE_VERDICT_PASS,
)


@pytest.mark.asyncio
async def test_one_revision_round_reaches_completed(
    mocker, tmp_path, patch_llm_factory, make_config, fake_git_auto_safe
):
    """Round 0: REVISION_NEEDED → Planner revises → Round 1: APPROVED → COMPLETED."""
    mocker.patch("src.core.orchestrator.GitTool", return_value=fake_git_auto_safe)
    config = make_config(max_plan_revision_rounds=2)
    orchestrator = Orchestrator(config)

    orchestrator.planner._call_llm_with_retry = AsyncMock(
        side_effect=[PLAN_ALL_AUTO_SAFE, PLAN_ALL_AUTO_SAFE]
    )
    orchestrator.planner_judge._call_llm_with_retry = AsyncMock(
        side_effect=[PLANNER_JUDGE_REVISION_NEEDED, PLANNER_JUDGE_APPROVED_2]
    )
    orchestrator.judge._call_llm_with_retry = AsyncMock(
        side_effect=[JUDGE_VERDICT_PASS]
    )

    state = MergeState(config=config)
    result = await orchestrator.run(state)

    assert result.status == SystemStatus.COMPLETED


@pytest.mark.asyncio
async def test_one_revision_round_increments_counter(
    mocker, tmp_path, patch_llm_factory, make_config, fake_git_auto_safe
):
    mocker.patch("src.core.orchestrator.GitTool", return_value=fake_git_auto_safe)
    config = make_config(max_plan_revision_rounds=2)
    orchestrator = Orchestrator(config)

    orchestrator.planner._call_llm_with_retry = AsyncMock(
        side_effect=[PLAN_ALL_AUTO_SAFE, PLAN_ALL_AUTO_SAFE]
    )
    orchestrator.planner_judge._call_llm_with_retry = AsyncMock(
        side_effect=[PLANNER_JUDGE_REVISION_NEEDED, PLANNER_JUDGE_APPROVED_2]
    )
    orchestrator.judge._call_llm_with_retry = AsyncMock(
        side_effect=[JUDGE_VERDICT_PASS]
    )

    state = MergeState(config=config)
    result = await orchestrator.run(state)

    assert result.plan_revision_rounds == 1


@pytest.mark.asyncio
async def test_one_revision_round_planner_called_twice(
    mocker, tmp_path, patch_llm_factory, make_config, fake_git_auto_safe
):
    """Planner is called once for initial plan and once for the revision."""
    mocker.patch("src.core.orchestrator.GitTool", return_value=fake_git_auto_safe)
    config = make_config(max_plan_revision_rounds=2)
    orchestrator = Orchestrator(config)

    planner_mock = AsyncMock(side_effect=[PLAN_ALL_AUTO_SAFE, PLAN_ALL_AUTO_SAFE])
    orchestrator.planner._call_llm_with_retry = planner_mock
    orchestrator.planner_judge._call_llm_with_retry = AsyncMock(
        side_effect=[PLANNER_JUDGE_REVISION_NEEDED, PLANNER_JUDGE_APPROVED_2]
    )
    orchestrator.judge._call_llm_with_retry = AsyncMock(
        side_effect=[JUDGE_VERDICT_PASS]
    )

    state = MergeState(config=config)
    await orchestrator.run(state)

    assert planner_mock.call_count == 2


@pytest.mark.asyncio
async def test_max_revisions_exceeded_transitions_to_awaiting_human(
    mocker, tmp_path, patch_llm_factory, make_config, fake_git_auto_safe
):
    """PlannerJudge persistently returns REVISION_NEEDED → AWAITING_HUMAN after 3 rounds."""
    mocker.patch("src.core.orchestrator.GitTool", return_value=fake_git_auto_safe)
    config = make_config(max_plan_revision_rounds=2)
    orchestrator = Orchestrator(config)

    orchestrator.planner._call_llm_with_retry = AsyncMock(
        side_effect=[PLAN_ALL_AUTO_SAFE] * 3
    )
    orchestrator.planner_judge._call_llm_with_retry = AsyncMock(
        side_effect=[PLANNER_JUDGE_REVISION_NEEDED] * 3
    )

    state = MergeState(config=config)
    result = await orchestrator.run(state)

    assert result.status == SystemStatus.AWAITING_HUMAN


@pytest.mark.asyncio
async def test_max_revisions_exceeded_does_not_set_failed(
    mocker, tmp_path, patch_llm_factory, make_config, fake_git_auto_safe
):
    """Exceeding revision limit must NOT set FAILED — only AWAITING_HUMAN."""
    mocker.patch("src.core.orchestrator.GitTool", return_value=fake_git_auto_safe)
    config = make_config(max_plan_revision_rounds=2)
    orchestrator = Orchestrator(config)

    orchestrator.planner._call_llm_with_retry = AsyncMock(
        side_effect=[PLAN_ALL_AUTO_SAFE] * 3
    )
    orchestrator.planner_judge._call_llm_with_retry = AsyncMock(
        side_effect=[PLANNER_JUDGE_REVISION_NEEDED] * 3
    )

    state = MergeState(config=config)
    result = await orchestrator.run(state)

    assert result.status != SystemStatus.FAILED


@pytest.mark.asyncio
async def test_max_revisions_exceeded_planner_judge_called_three_times(
    mocker, tmp_path, patch_llm_factory, make_config, fake_git_auto_safe
):
    """With max=2, the judge reviews 3 times (rounds 0, 1, 2) before giving up."""
    mocker.patch("src.core.orchestrator.GitTool", return_value=fake_git_auto_safe)
    config = make_config(max_plan_revision_rounds=2)
    orchestrator = Orchestrator(config)

    orchestrator.planner._call_llm_with_retry = AsyncMock(
        side_effect=[PLAN_ALL_AUTO_SAFE] * 3
    )
    pj_mock = AsyncMock(side_effect=[PLANNER_JUDGE_REVISION_NEEDED] * 3)
    orchestrator.planner_judge._call_llm_with_retry = pj_mock

    state = MergeState(config=config)
    await orchestrator.run(state)

    assert pj_mock.call_count == 3
