"""
Integration tests: semantic merge flow.

An AUTO_RISKY file with high-confidence conflict analysis goes through:
PLAN_REVIEWING → AUTO_MERGING → ANALYZING_CONFLICTS → (semantic merge) →
JUDGE_REVIEWING → GENERATING_REPORT → COMPLETED
"""

from unittest.mock import AsyncMock
import pytest

from src.core.orchestrator import Orchestrator
from src.models.state import MergeState, SystemStatus
from src.models.decision import MergeDecision

from tests.integration.conftest import (
    PLAN_ONE_AUTO_RISKY,
    PLANNER_JUDGE_APPROVED_1,
    CONFLICT_HIGH_CONFIDENCE,
    SEMANTIC_MERGE_CONTENT,
    JUDGE_VERDICT_PASS,
)


@pytest.mark.asyncio
async def test_high_confidence_conflict_reaches_completed(
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
        side_effect=[CONFLICT_HIGH_CONFIDENCE]
    )
    orchestrator.executor._call_llm_with_retry = AsyncMock(
        side_effect=[SEMANTIC_MERGE_CONTENT]
    )
    orchestrator.judge._call_llm_with_retry = AsyncMock(
        side_effect=[JUDGE_VERDICT_PASS]
    )

    state = MergeState(config=config)
    result = await orchestrator.run(state)

    assert result.status == SystemStatus.COMPLETED


@pytest.mark.asyncio
async def test_semantic_merge_decision_recorded(
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
        side_effect=[CONFLICT_HIGH_CONFIDENCE]
    )
    orchestrator.executor._call_llm_with_retry = AsyncMock(
        side_effect=[SEMANTIC_MERGE_CONTENT]
    )
    orchestrator.judge._call_llm_with_retry = AsyncMock(
        side_effect=[JUDGE_VERDICT_PASS]
    )

    state = MergeState(config=config)
    result = await orchestrator.run(state)

    assert "src/service.py" in result.file_decision_records
    record = result.file_decision_records["src/service.py"]
    assert record.decision == MergeDecision.SEMANTIC_MERGE


@pytest.mark.asyncio
async def test_semantic_merge_content_written_to_disk(
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
        side_effect=[CONFLICT_HIGH_CONFIDENCE]
    )
    orchestrator.executor._call_llm_with_retry = AsyncMock(
        side_effect=[SEMANTIC_MERGE_CONTENT]
    )
    orchestrator.judge._call_llm_with_retry = AsyncMock(
        side_effect=[JUDGE_VERDICT_PASS]
    )

    state = MergeState(config=config)
    await orchestrator.run(state)

    merged_file = tmp_path / "src" / "service.py"
    assert merged_file.exists()
    assert "merged_service" in merged_file.read_text()


@pytest.mark.asyncio
async def test_semantic_merge_judge_verdict_is_pass(
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
        side_effect=[CONFLICT_HIGH_CONFIDENCE]
    )
    orchestrator.executor._call_llm_with_retry = AsyncMock(
        side_effect=[SEMANTIC_MERGE_CONTENT]
    )
    orchestrator.judge._call_llm_with_retry = AsyncMock(
        side_effect=[JUDGE_VERDICT_PASS]
    )

    state = MergeState(config=config)
    result = await orchestrator.run(state)

    from src.models.judge import VerdictType

    assert result.judge_verdict is not None
    assert result.judge_verdict.verdict == VerdictType.PASS


@pytest.mark.asyncio
async def test_semantic_merge_no_human_escalation(
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
        side_effect=[CONFLICT_HIGH_CONFIDENCE]
    )
    orchestrator.executor._call_llm_with_retry = AsyncMock(
        side_effect=[SEMANTIC_MERGE_CONTENT]
    )
    orchestrator.judge._call_llm_with_retry = AsyncMock(
        side_effect=[JUDGE_VERDICT_PASS]
    )

    state = MergeState(config=config)
    result = await orchestrator.run(state)

    assert result.human_decision_requests == {}


@pytest.mark.asyncio
async def test_semantic_merge_conflict_analysis_stored(
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
        side_effect=[CONFLICT_HIGH_CONFIDENCE]
    )
    orchestrator.executor._call_llm_with_retry = AsyncMock(
        side_effect=[SEMANTIC_MERGE_CONTENT]
    )
    orchestrator.judge._call_llm_with_retry = AsyncMock(
        side_effect=[JUDGE_VERDICT_PASS]
    )

    state = MergeState(config=config)
    result = await orchestrator.run(state)

    assert "src/service.py" in result.conflict_analyses
    analysis = result.conflict_analyses["src/service.py"]
    assert analysis.confidence == pytest.approx(0.92)
