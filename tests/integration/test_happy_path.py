"""
Integration test: happy path.

Two AUTO_SAFE files flow through the complete pipeline:
INITIALIZED → PLANNING → PLAN_REVIEWING → AUTO_MERGING → JUDGE_REVIEWING
→ GENERATING_REPORT → COMPLETED
"""

from unittest.mock import AsyncMock
import pytest

from src.core.orchestrator import Orchestrator
from src.models.state import MergeState, SystemStatus

from tests.integration.conftest import (
    PLAN_ALL_AUTO_SAFE,
    PLANNER_JUDGE_APPROVED_2,
    JUDGE_VERDICT_PASS,
)


@pytest.mark.asyncio
async def test_all_auto_safe_reaches_completed(
    mocker, tmp_path, patch_llm_factory, make_config, fake_git_auto_safe
):
    mocker.patch("src.core.orchestrator.GitTool", return_value=fake_git_auto_safe)
    config = make_config()
    orchestrator = Orchestrator(config)

    orchestrator.planner._call_llm_with_retry = AsyncMock(
        side_effect=[PLAN_ALL_AUTO_SAFE]
    )
    orchestrator.planner_judge._call_llm_with_retry = AsyncMock(
        side_effect=[PLANNER_JUDGE_APPROVED_2]
    )
    orchestrator.judge._call_llm_with_retry = AsyncMock(
        side_effect=[JUDGE_VERDICT_PASS]
    )

    state = MergeState(config=config)
    result = await orchestrator.run(state)

    assert result.status == SystemStatus.COMPLETED


@pytest.mark.asyncio
async def test_all_auto_safe_plan_judge_called_once(
    mocker, tmp_path, patch_llm_factory, make_config, fake_git_auto_safe
):
    mocker.patch("src.core.orchestrator.GitTool", return_value=fake_git_auto_safe)
    config = make_config()
    orchestrator = Orchestrator(config)

    planner_mock = AsyncMock(side_effect=[PLAN_ALL_AUTO_SAFE])
    judge_mock = AsyncMock(side_effect=[PLANNER_JUDGE_APPROVED_2])
    verdict_mock = AsyncMock(side_effect=[JUDGE_VERDICT_PASS])

    orchestrator.planner._call_llm_with_retry = planner_mock
    orchestrator.planner_judge._call_llm_with_retry = judge_mock
    orchestrator.judge._call_llm_with_retry = verdict_mock

    state = MergeState(config=config)
    await orchestrator.run(state)

    planner_mock.assert_called_once()
    judge_mock.assert_called_once()


@pytest.mark.asyncio
async def test_all_auto_safe_executor_records_both_files(
    mocker, tmp_path, patch_llm_factory, make_config, fake_git_auto_safe
):
    mocker.patch("src.core.orchestrator.GitTool", return_value=fake_git_auto_safe)
    config = make_config()
    orchestrator = Orchestrator(config)

    orchestrator.planner._call_llm_with_retry = AsyncMock(
        side_effect=[PLAN_ALL_AUTO_SAFE]
    )
    orchestrator.planner_judge._call_llm_with_retry = AsyncMock(
        side_effect=[PLANNER_JUDGE_APPROVED_2]
    )
    orchestrator.judge._call_llm_with_retry = AsyncMock(
        side_effect=[JUDGE_VERDICT_PASS]
    )

    state = MergeState(config=config)
    result = await orchestrator.run(state)

    assert "src/utils.py" in result.file_decision_records
    assert "src/helpers.py" in result.file_decision_records


@pytest.mark.asyncio
async def test_all_auto_safe_no_human_decision_requests(
    mocker, tmp_path, patch_llm_factory, make_config, fake_git_auto_safe
):
    mocker.patch("src.core.orchestrator.GitTool", return_value=fake_git_auto_safe)
    config = make_config()
    orchestrator = Orchestrator(config)

    orchestrator.planner._call_llm_with_retry = AsyncMock(
        side_effect=[PLAN_ALL_AUTO_SAFE]
    )
    orchestrator.planner_judge._call_llm_with_retry = AsyncMock(
        side_effect=[PLANNER_JUDGE_APPROVED_2]
    )
    orchestrator.judge._call_llm_with_retry = AsyncMock(
        side_effect=[JUDGE_VERDICT_PASS]
    )

    state = MergeState(config=config)
    result = await orchestrator.run(state)

    assert result.human_decision_requests == {}


@pytest.mark.asyncio
async def test_all_auto_safe_files_written_to_disk(
    mocker, tmp_path, patch_llm_factory, make_config, fake_git_auto_safe
):
    mocker.patch("src.core.orchestrator.GitTool", return_value=fake_git_auto_safe)
    config = make_config()
    orchestrator = Orchestrator(config)

    orchestrator.planner._call_llm_with_retry = AsyncMock(
        side_effect=[PLAN_ALL_AUTO_SAFE]
    )
    orchestrator.planner_judge._call_llm_with_retry = AsyncMock(
        side_effect=[PLANNER_JUDGE_APPROVED_2]
    )
    orchestrator.judge._call_llm_with_retry = AsyncMock(
        side_effect=[JUDGE_VERDICT_PASS]
    )

    state = MergeState(config=config)
    await orchestrator.run(state)

    assert (tmp_path / "src" / "utils.py").exists()
    assert (tmp_path / "src" / "helpers.py").exists()
