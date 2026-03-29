"""
Integration tests: human escalation paths.

Covers:
- ConflictAnalyst returns low confidence → AWAITING_HUMAN
- AWAITING_HUMAN state has populated human_decision_requests
- System never auto-fills skipped human decisions
"""
from unittest.mock import AsyncMock
import pytest

from src.core.orchestrator import Orchestrator
from src.models.state import MergeState, SystemStatus
from src.models.decision import MergeDecision

from tests.integration.conftest import (
    PLAN_ONE_AUTO_RISKY,
    PLANNER_JUDGE_APPROVED_1,
    CONFLICT_LOW_CONFIDENCE,
)


@pytest.mark.asyncio
async def test_low_confidence_conflict_transitions_to_awaiting_human(
    mocker, tmp_path, patch_llm_factory, make_config, fake_git_auto_risky
):
    mocker.patch("src.core.orchestrator.GitTool", return_value=fake_git_auto_risky)
    config = make_config()
    orchestrator = Orchestrator(config)

    orchestrator.planner._call_llm_with_retry = AsyncMock(
        side_effect=[PLAN_ONE_AUTO_RISKY]
    )
    orchestrator.planner_judge._call_llm_with_retry = AsyncMock(
        side_effect=[PLANNER_JUDGE_APPROVED_1]
    )
    orchestrator.conflict_analyst._call_llm_with_retry = AsyncMock(
        side_effect=[CONFLICT_LOW_CONFIDENCE]
    )

    state = MergeState(config=config)
    result = await orchestrator.run(state)

    assert result.status == SystemStatus.AWAITING_HUMAN


@pytest.mark.asyncio
async def test_low_confidence_conflict_populates_human_decision_requests(
    mocker, tmp_path, patch_llm_factory, make_config, fake_git_auto_risky
):
    mocker.patch("src.core.orchestrator.GitTool", return_value=fake_git_auto_risky)
    config = make_config()
    orchestrator = Orchestrator(config)

    orchestrator.planner._call_llm_with_retry = AsyncMock(
        side_effect=[PLAN_ONE_AUTO_RISKY]
    )
    orchestrator.planner_judge._call_llm_with_retry = AsyncMock(
        side_effect=[PLANNER_JUDGE_APPROVED_1]
    )
    orchestrator.conflict_analyst._call_llm_with_retry = AsyncMock(
        side_effect=[CONFLICT_LOW_CONFIDENCE]
    )

    state = MergeState(config=config)
    result = await orchestrator.run(state)

    assert "src/service.py" in result.human_decision_requests


@pytest.mark.asyncio
async def test_low_confidence_no_auto_decision_in_records(
    mocker, tmp_path, patch_llm_factory, make_config, fake_git_auto_risky
):
    """Files escalated to human must NOT appear with auto-resolved decisions."""
    mocker.patch("src.core.orchestrator.GitTool", return_value=fake_git_auto_risky)
    config = make_config()
    orchestrator = Orchestrator(config)

    orchestrator.planner._call_llm_with_retry = AsyncMock(
        side_effect=[PLAN_ONE_AUTO_RISKY]
    )
    orchestrator.planner_judge._call_llm_with_retry = AsyncMock(
        side_effect=[PLANNER_JUDGE_APPROVED_1]
    )
    orchestrator.conflict_analyst._call_llm_with_retry = AsyncMock(
        side_effect=[CONFLICT_LOW_CONFIDENCE]
    )

    state = MergeState(config=config)
    result = await orchestrator.run(state)

    record = result.file_decision_records.get("src/service.py")
    if record is not None:
        assert record.decision == MergeDecision.ESCALATE_HUMAN


@pytest.mark.asyncio
async def test_awaiting_human_state_has_no_errors(
    mocker, tmp_path, patch_llm_factory, make_config, fake_git_auto_risky
):
    mocker.patch("src.core.orchestrator.GitTool", return_value=fake_git_auto_risky)
    config = make_config()
    orchestrator = Orchestrator(config)

    orchestrator.planner._call_llm_with_retry = AsyncMock(
        side_effect=[PLAN_ONE_AUTO_RISKY]
    )
    orchestrator.planner_judge._call_llm_with_retry = AsyncMock(
        side_effect=[PLANNER_JUDGE_APPROVED_1]
    )
    orchestrator.conflict_analyst._call_llm_with_retry = AsyncMock(
        side_effect=[CONFLICT_LOW_CONFIDENCE]
    )

    state = MergeState(config=config)
    result = await orchestrator.run(state)

    assert result.errors == []


@pytest.mark.asyncio
async def test_awaiting_human_conflict_analysis_stored(
    mocker, tmp_path, patch_llm_factory, make_config, fake_git_auto_risky
):
    mocker.patch("src.core.orchestrator.GitTool", return_value=fake_git_auto_risky)
    config = make_config()
    orchestrator = Orchestrator(config)

    orchestrator.planner._call_llm_with_retry = AsyncMock(
        side_effect=[PLAN_ONE_AUTO_RISKY]
    )
    orchestrator.planner_judge._call_llm_with_retry = AsyncMock(
        side_effect=[PLANNER_JUDGE_APPROVED_1]
    )
    orchestrator.conflict_analyst._call_llm_with_retry = AsyncMock(
        side_effect=[CONFLICT_LOW_CONFIDENCE]
    )

    state = MergeState(config=config)
    result = await orchestrator.run(state)

    assert "src/service.py" in result.conflict_analyses
    analysis = result.conflict_analyses["src/service.py"]
    assert analysis.confidence == pytest.approx(0.3)
