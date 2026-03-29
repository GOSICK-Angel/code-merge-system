import pytest
from src.models.config import MergeConfig, AgentLLMConfig
from src.models.decision import DecisionSource
from src.models.state import MergeState, SystemStatus
from src.models.plan import MergePhase
from src.models.plan_judge import PlanJudgeVerdict


def _make_config() -> MergeConfig:
    return MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")


def test_merge_config_no_timeout_field():
    config = _make_config()
    config_fields = set(MergeConfig.model_fields.keys())
    assert "human_decision_timeout_hours" not in config_fields, (
        "MergeConfig must not have human_decision_timeout_hours"
    )


def test_agent_llm_config_requires_env_var():
    agent_cfg = AgentLLMConfig(provider="anthropic", model="claude-opus-4-6", api_key_env="MY_KEY")
    assert agent_cfg.api_key_env == "MY_KEY"
    agent_cfg2 = AgentLLMConfig(provider="openai", model="gpt-4o", api_key_env="OPENAI_KEY")
    assert agent_cfg2.api_key_env == "OPENAI_KEY"
    assert agent_cfg2.api_key_env != ""


def test_decision_source_no_timeout():
    source_values = {s.value for s in DecisionSource}
    assert "timeout_default" not in source_values, (
        "DecisionSource must not contain TIMEOUT_DEFAULT"
    )
    assert DecisionSource.AUTO_PLANNER.value == "auto_planner"
    assert DecisionSource.AUTO_EXECUTOR.value == "auto_executor"
    assert DecisionSource.HUMAN.value == "human"
    assert DecisionSource.BATCH_HUMAN.value == "batch_human"


def test_merge_state_new_fields():
    config = _make_config()
    state = MergeState(config=config)
    assert hasattr(state, "plan_judge_verdict")
    assert state.plan_judge_verdict is None
    assert hasattr(state, "plan_disputes")
    assert isinstance(state.plan_disputes, list)
    assert state.plan_disputes == []
    assert hasattr(state, "plan_revision_rounds")
    assert state.plan_revision_rounds == 0


def test_merge_phase_includes_plan_review():
    phase_values = {p.value for p in MergePhase}
    assert "plan_review" in phase_values, "MergePhase must contain PLAN_REVIEW"
    assert "plan_revising" in phase_values, "MergePhase must contain PLAN_REVISING"


def test_system_status_new_states():
    status_values = {s.value for s in SystemStatus}
    assert "plan_reviewing" in status_values, "SystemStatus must contain PLAN_REVIEWING"
    assert "plan_revising" in status_values, "SystemStatus must contain PLAN_REVISING"
    assert "plan_dispute_pending" in status_values, "SystemStatus must contain PLAN_DISPUTE_PENDING"
