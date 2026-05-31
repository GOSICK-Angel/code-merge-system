"""Legacy `_merge_base` residual regression (doc/bugfix/0528).

`_build_merge_plan` gated `merge_base_commit` behind
``hasattr(state, "_merge_base")``, a private attribute production never
sets. Every built plan therefore carried ``merge_base_commit=""`` — most
visibly on the legacy non-layered ``revise_plan`` path. This test pins the
fix: the plan must echo ``state.merge_base_commit``.
"""

from __future__ import annotations

from unittest.mock import patch

from src.agents.planner_agent import PlannerAgent
from src.models.config import AgentLLMConfig, MergeConfig
from src.models.state import MergeState


def _planner() -> PlannerAgent:
    with patch("src.llm.client.LLMClientFactory.create"):
        return PlannerAgent(AgentLLMConfig())


def test_build_merge_plan_carries_merge_base_commit() -> None:
    config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
    state = MergeState(config=config)
    state.merge_base_commit = "abc123"

    plan = _planner()._build_merge_plan({}, state, [])

    assert plan.merge_base_commit == "abc123"
