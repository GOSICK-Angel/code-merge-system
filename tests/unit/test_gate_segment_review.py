"""Batch A / P0-2: the segment plan-review prompt is the one actually used in
production (planner_judge_agent._review_segment), yet only the whole-plan
variant was registered as a gate (PJ-PLAN-REVIEW). That let the live path
bypass the gate registry the contract architecture relies on. Pin that the
segment builder is now reachable via a stable gate ID and declared by the
planner_judge contract.
"""

from __future__ import annotations

from src.agents.contract import load_contract
from src.llm.prompts.gate_registry import get_gate
from src.llm.prompts.planner_judge_prompts import build_segment_plan_review_prompt


def test_segment_gate_registered_and_points_at_builder() -> None:
    gate = get_gate("PJ-PLAN-REVIEW-SEGMENT")
    assert gate.builder is build_segment_plan_review_prompt
    assert gate.description


def test_planner_judge_contract_declares_segment_gate() -> None:
    contract = load_contract("planner_judge")
    assert "PJ-PLAN-REVIEW-SEGMENT" in contract.gates
