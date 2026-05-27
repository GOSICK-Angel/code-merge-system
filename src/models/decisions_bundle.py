"""Unified decisions schema (V2) for non-interactive runs.

Pre-existing decision YAML files come in two shapes:

- **Plan-stage** (parsed in ``cli/commands/resume.py``)::

    plan_approval: approve
    item_decisions:
      - file_path: a/b.py
        user_choice: downgrade_risky
    judge_resolution: accept    # optional

- **Conflict-stage** (parsed by ``HumanInterfaceAgent.collect_decisions_file``)::

    decisions:
      - file_path: c.py
        decision: take_target

Both shapes describe ONE round of human review. A typical end-to-end run
produces 3+ AWAITING_HUMAN cycles, forcing the operator to write three
separate yaml files and call ``merge resume --decisions ...`` between
each. The V2 schema bundles them into one document::

    version: 2
    rounds:
      - phase: plan_review
        plan_approval: approve
        item_decisions:
          - file_path: a/b.py
            user_choice: downgrade_risky
      - phase: conflict_marker
        item_decisions:
          - file_path: c.py
            user_choice: take_target
      - phase: conflict_resolution
        decisions:
          - file_path: d.py
            decision: take_target
      - phase: judge_review
        judge_resolution: accept

A V2 bundle can be passed end-to-end via ``merge --auto-decisions <yaml>``
in CI mode; the orchestrator pops one round per AWAITING_HUMAN cycle.

Backwards compatibility: ``parse_bundle`` accepts V1 YAML (no ``version``
field) and wraps it in a single-round V2 bundle whose phase is inferred
from the keys present.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class DecisionPhase(str, Enum):
    """Which AWAITING_HUMAN cycle this round addresses."""

    PLAN_REVIEW = "plan_review"
    CONFLICT_MARKER = "conflict_marker"  # post auto_merge, R2 in the report
    CONFLICT_RESOLUTION = "conflict_resolution"  # post conflict_analysis, R3
    JUDGE_REVIEW = "judge_review"


class ItemChoice(BaseModel):
    """Plan-stage / conflict-marker decision for ONE file.

    ``user_choice`` is a string-keyed value from the agent's ``options``
    (e.g. ``approve_human``, ``downgrade_risky``, ``take_target``).
    """

    file_path: str
    user_choice: str | None = None
    notes: str | None = None


class ConflictDecision(BaseModel):
    """conflict_resolution stage decision for ONE file.

    ``decision`` MUST be a ``MergeDecision`` enum value (e.g. ``take_target``,
    ``take_current``, ``semantic_merge``, ``manual_patch``).
    """

    file_path: str
    decision: str
    custom_content: str | None = None
    reviewer_name: str | None = None
    reviewer_notes: str | None = None


class GroupConflictDecision(BaseModel):
    conflict_type: str
    decision: str
    reviewer_notes: str | None = None


class DecisionRound(BaseModel):
    """One AWAITING_HUMAN cycle's worth of decisions."""

    phase: DecisionPhase
    plan_approval: str | None = None  # approve | reject | modify
    reviewer: str | None = None
    notes: str | None = None
    item_decisions: list[ItemChoice] = Field(default_factory=list)
    decisions: list[ConflictDecision] = Field(default_factory=list)
    group_decisions: list[GroupConflictDecision] = Field(default_factory=list)
    judge_resolution: str | None = None  # accept | abort | rerun


class DecisionsBundle(BaseModel):
    version: int = 2
    rounds: list[DecisionRound] = Field(default_factory=list)

    def take_round(self, phase: DecisionPhase) -> DecisionRound | None:
        """Return the first matching round and consume it (mutates list)."""
        for i, rnd in enumerate(self.rounds):
            if rnd.phase == phase:
                return self.rounds.pop(i)
        return None

    def peek_round(self, phase: DecisionPhase) -> DecisionRound | None:
        for rnd in self.rounds:
            if rnd.phase == phase:
                return rnd
        return None


def parse_bundle(raw: dict[str, Any]) -> DecisionsBundle:
    """Parse YAML dict into a DecisionsBundle, accepting V1 or V2 shape.

    V2 detection keys on the presence of a ``rounds`` list, NOT on
    ``version: 2``. Requiring the version sentinel made a ``rounds:``
    document that merely omitted it collapse silently into a single empty
    ``plan_review`` round — dropping every decision with no error. The
    ``version`` field is now an optional annotation; ``rounds`` alone
    selects the V2 path.

    V1 documents (no ``rounds`` list) are wrapped into a single-round
    bundle whose phase is inferred from which top-level keys are present.
    """
    if not isinstance(raw, dict):
        raise ValueError("decisions YAML root must be a mapping")
    if "rounds" in raw:
        if not isinstance(raw["rounds"], list):
            raise ValueError("decisions YAML 'rounds' must be a list")
        return DecisionsBundle.model_validate({"version": 2, **raw})
    return DecisionsBundle(rounds=[_v1_to_round(raw)])


def _v1_to_round(raw: dict[str, Any]) -> DecisionRound:
    has_plan_keys = (
        "plan_approval" in raw or "item_decisions" in raw or "judge_resolution" in raw
    )
    has_conflict_keys = "decisions" in raw or "group_decisions" in raw

    if has_plan_keys and not has_conflict_keys:
        if raw.get("judge_resolution") and not raw.get("plan_approval"):
            phase = DecisionPhase.JUDGE_REVIEW
        else:
            phase = DecisionPhase.PLAN_REVIEW
    elif has_conflict_keys and not has_plan_keys:
        phase = DecisionPhase.CONFLICT_RESOLUTION
    elif has_conflict_keys and has_plan_keys:
        phase = DecisionPhase.CONFLICT_RESOLUTION
    else:
        phase = DecisionPhase.PLAN_REVIEW

    return DecisionRound(
        phase=phase,
        plan_approval=raw.get("plan_approval"),
        reviewer=raw.get("reviewer"),
        notes=raw.get("notes"),
        item_decisions=[
            ItemChoice.model_validate(it)
            for it in (raw.get("item_decisions") or [])
            if isinstance(it, dict) and it.get("file_path")
        ],
        decisions=[
            ConflictDecision.model_validate(it)
            for it in (raw.get("decisions") or [])
            if isinstance(it, dict) and it.get("file_path")
        ],
        group_decisions=[
            GroupConflictDecision.model_validate(it)
            for it in (raw.get("group_decisions") or [])
            if isinstance(it, dict) and it.get("conflict_type")
        ],
        judge_resolution=raw.get("judge_resolution"),
    )
