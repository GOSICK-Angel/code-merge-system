import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch
from src.models.config import MergeConfig, AgentLLMConfig
from src.models.state import MergeState, SystemStatus
from src.models.diff import FileDiff, FileStatus, RiskLevel
from src.models.decision import MergeDecision
from src.core.read_only_state_view import ReadOnlyStateView


def _make_config() -> MergeConfig:
    return MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")


def _make_state() -> MergeState:
    return MergeState(config=_make_config())


def _make_llm_config(
    provider: str = "anthropic", key_env: str = "TEST_KEY"
) -> AgentLLMConfig:
    return AgentLLMConfig(
        provider=provider,
        model="test-model",
        api_key_env=key_env,
    )


def _make_file_diff(
    file_path: str = "src/main.py",
    risk_level: RiskLevel = RiskLevel.AUTO_SAFE,
) -> FileDiff:
    return FileDiff(
        file_path=file_path,
        file_status=FileStatus.MODIFIED,
        risk_level=risk_level,
        risk_score=0.2,
        lines_added=10,
        lines_deleted=5,
        lines_changed=10,
    )


def test_planner_judge_receives_readonly_view():
    state = _make_state()
    readonly = ReadOnlyStateView(state)
    assert isinstance(readonly, ReadOnlyStateView)
    _ = readonly.status
    _ = readonly.merge_plan


def test_planner_judge_cannot_write_state():
    state = _make_state()
    readonly = ReadOnlyStateView(state)
    with pytest.raises(PermissionError):
        readonly.merge_plan = None
    with pytest.raises(PermissionError):
        readonly.status = SystemStatus.COMPLETED


def test_judge_cannot_write_state():
    state = _make_state()
    readonly = ReadOnlyStateView(state)
    with pytest.raises(PermissionError):
        readonly.judge_verdict = None
    with pytest.raises(PermissionError):
        readonly.file_decision_records = {}


def test_executor_dispute_does_not_change_risk_level():
    with patch.dict("os.environ", {"TEST_KEY": "fake-key"}):
        from src.agents.executor_agent import ExecutorAgent

        agent = ExecutorAgent(_make_llm_config(provider="openai", key_env="TEST_KEY"))
        state = _make_state()

        fd = _make_file_diff("src/auth/login.py", RiskLevel.AUTO_SAFE)
        state.file_classifications["src/auth/login.py"] = RiskLevel.AUTO_SAFE

        original_risk = state.file_classifications["src/auth/login.py"]

        dispute = agent.raise_plan_dispute(
            fd,
            "This auth file should be HUMAN_REQUIRED",
            {"src/auth/login.py": RiskLevel.HUMAN_REQUIRED},
            "Security concern",
            state,
        )

        assert state.file_classifications["src/auth/login.py"] == original_risk, (
            "raise_plan_dispute must not modify the risk classification"
        )
        assert len(state.plan_disputes) == 1
        assert dispute.dispute_reason == "This auth file should be HUMAN_REQUIRED"


@pytest.mark.asyncio
async def test_executor_requires_snapshot_before_write():
    import tempfile
    from pathlib import Path
    from src.tools.patch_applier import apply_with_snapshot

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        test_file = tmp_path / "src" / "module.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        original = "original content\n"
        test_file.write_text(original, encoding="utf-8")

        git_tool = MagicMock()
        git_tool.repo_path = tmp_path
        state = _make_state()

        record = await apply_with_snapshot(
            "src/module.py", "new content\n", git_tool, state
        )
        assert record.original_snapshot == original


def test_human_interface_never_auto_decides():
    with patch.dict("os.environ", {"TEST_KEY": "fake-key"}):
        from src.agents.human_interface_agent import HumanInterfaceAgent

        agent = HumanInterfaceAgent(_make_llm_config(key_env="TEST_KEY"))
        from src.models.human import HumanDecisionRequest, DecisionOption

        req = HumanDecisionRequest(
            file_path="src/complex.py",
            priority=1,
            conflict_points=[],
            context_summary="Complex conflict",
            upstream_change_summary="Added feature",
            fork_change_summary="Added different feature",
            analyst_recommendation=MergeDecision.ESCALATE_HUMAN,
            analyst_confidence=0.3,
            analyst_rationale="Too complex",
            options=[
                DecisionOption(
                    option_key="A",
                    decision=MergeDecision.TAKE_CURRENT,
                    description="Keep fork",
                )
            ],
            created_at=datetime.now(),
        )

        assert req.human_decision is None, "New request must have no decision"
        assert not agent.validate_decision(req), (
            "Request without decision must be invalid"
        )


def test_validate_decision_rejects_escalate_human():
    with patch.dict("os.environ", {"TEST_KEY": "fake-key"}):
        from src.agents.human_interface_agent import HumanInterfaceAgent
        from src.models.human import HumanDecisionRequest

        agent = HumanInterfaceAgent(_make_llm_config(key_env="TEST_KEY"))

        req = HumanDecisionRequest(
            file_path="src/complex.py",
            priority=1,
            conflict_points=[],
            context_summary="Complex conflict",
            upstream_change_summary="",
            fork_change_summary="",
            analyst_recommendation=MergeDecision.ESCALATE_HUMAN,
            analyst_confidence=0.3,
            analyst_rationale="",
            options=[],
            created_at=datetime.now(),
            human_decision=MergeDecision.ESCALATE_HUMAN,
        )
        assert not agent.validate_decision(req), (
            "ESCALATE_HUMAN must not be accepted as a valid human decision"
        )


def test_plan_revision_stops_at_max_rounds():
    state = _make_state()
    max_rounds = state.config.max_plan_revision_rounds
    assert max_rounds == 2, "Default max_plan_revision_rounds should be 2"

    state.plan_revision_rounds = max_rounds
    assert state.plan_revision_rounds >= state.config.max_plan_revision_rounds, (
        "When rounds reach max, system should escalate to human"
    )
