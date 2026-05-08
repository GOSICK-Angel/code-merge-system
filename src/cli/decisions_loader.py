"""Read a decisions YAML and apply one round to MergeState.

The same logic was previously inlined in ``cli/commands/resume.py``;
extracted so that the CI ``--auto-decisions`` driver can apply one round
per AWAITING_HUMAN cycle without forking another shell.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from src.models.decisions_bundle import (
    DecisionPhase,
    DecisionRound,
    DecisionsBundle,
    parse_bundle,
)
from src.models.plan_review import PlanHumanDecision, PlanHumanReview
from src.models.state import MergeState

logger = logging.getLogger(__name__)


def load_bundle(yaml_path: str | Path) -> DecisionsBundle:
    raw: Any = yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8"))
    return parse_bundle(raw or {})


def apply_round(state: MergeState, rnd: DecisionRound) -> dict[str, int]:
    """Apply a single round's decisions to ``state``.

    Returns a stats dict ``{"item_choices": n, "conflict_decisions": m, ...}``
    so callers can log progress. Validation errors raise ``ValueError``.
    """
    stats: dict[str, int] = {
        "item_choices": 0,
        "conflict_decisions": 0,
        "group_decisions": 0,
        "plan_approval_set": 0,
        "judge_resolution_set": 0,
    }

    if rnd.item_decisions:
        stats["item_choices"] = _apply_item_choices(state, rnd)

    if rnd.plan_approval and state.plan_human_review is None:
        try:
            pd = PlanHumanDecision(str(rnd.plan_approval).lower())
        except ValueError as exc:
            raise ValueError(
                f"Invalid plan_approval: {rnd.plan_approval!r} "
                "(expected approve|reject|modify)"
            ) from exc
        state.plan_human_review = PlanHumanReview(
            decision=pd,
            reviewer_name=rnd.reviewer or "cli",
            reviewer_notes=rnd.notes,
            item_decisions=list(state.pending_user_decisions),
        )
        stats["plan_approval_set"] = 1
    elif stats["item_choices"] and state.plan_human_review is not None:
        # Keep plan_human_review.item_decisions snapshot in sync with the
        # updated pending_user_decisions so downstream consumers see the
        # latest user_choice values.
        state.plan_human_review = state.plan_human_review.model_copy(
            update={"item_decisions": list(state.pending_user_decisions)}
        )

    if rnd.judge_resolution is not None:
        val = str(rnd.judge_resolution).lower().strip()
        if val not in {"accept", "abort", "rerun"}:
            raise ValueError(
                f"Invalid judge_resolution: {rnd.judge_resolution!r} "
                "(expected accept|abort|rerun)"
            )
        state.judge_resolution = val  # type: ignore[assignment]
        stats["judge_resolution_set"] = 1

    if rnd.decisions or rnd.group_decisions:
        applied = _apply_conflict_decisions(state, rnd)
        stats["conflict_decisions"] = applied["files"]
        stats["group_decisions"] = applied["groups"]

    return stats


def _apply_item_choices(state: MergeState, rnd: DecisionRound) -> int:
    by_path = {it.file_path: it for it in rnd.item_decisions}
    applied = 0
    for idx, item in enumerate(state.pending_user_decisions):
        payload = by_path.get(item.file_path)
        if payload is None or payload.user_choice is None:
            continue
        if item.user_choice is not None:
            continue  # never overwrite an already-decided item
        valid_keys = {o.key for o in item.options}
        if payload.user_choice not in valid_keys:
            raise ValueError(
                f"Invalid user_choice {payload.user_choice!r} for "
                f"{item.file_path} (valid: {sorted(valid_keys)})"
            )
        state.pending_user_decisions[idx] = item.model_copy(
            update={
                "user_choice": payload.user_choice,
                "user_input": payload.notes,
            }
        )
        applied += 1
    return applied


def _apply_conflict_decisions(state: MergeState, rnd: DecisionRound) -> dict[str, int]:
    """Mirror what ``HumanInterfaceAgent.collect_decisions_file`` does, but
    drive it from a parsed ``DecisionRound`` (in-memory) rather than re-reading
    the yaml from disk."""
    from src.agents.human_interface_agent import HumanInterfaceAgent
    from src.models.decision import MergeDecision

    pending = [
        req
        for req in state.human_decision_requests.values()
        if req.human_decision is None
    ]
    if not pending:
        return {"files": 0, "groups": 0}

    decisions_map = {d.file_path: d for d in rnd.decisions}
    files_applied = 0
    for req in list(pending):
        d = decisions_map.get(req.file_path)
        if d is None:
            continue
        try:
            decision = MergeDecision(d.decision)
        except ValueError:
            logger.warning(
                "Invalid decision %r for %s — skipping", d.decision, req.file_path
            )
            continue
        if decision == MergeDecision.MANUAL_PATCH and not d.custom_content:
            logger.warning(
                "MANUAL_PATCH for %s has no custom_content — skipping",
                req.file_path,
            )
            continue
        updated = req.model_copy(
            update={
                "human_decision": decision,
                "custom_content": d.custom_content,
                "reviewer_name": d.reviewer_name,
                "reviewer_notes": d.reviewer_notes,
                "decided_at": datetime.now(),
            }
        )
        state.human_decision_requests[req.file_path] = updated
        state.human_decisions[req.file_path] = decision
        files_applied += 1

    groups_applied = 0
    if rnd.group_decisions:
        # Group decisions reuse HumanInterfaceAgent's logic (which needs
        # ``ConflictGroup``s). Round-trip through a temp yaml so we don't
        # duplicate that fan-out code here.
        hi = HumanInterfaceAgent(state.config.agents.human_interface)
        synthetic_yaml = {
            "decisions": [d.model_dump() for d in rnd.decisions],
            "group_decisions": [g.model_dump() for g in rnd.group_decisions],
        }
        tmp = Path(state.config.repo_path) / ".merge" / "_auto_decisions_round.tmp.yaml"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(yaml.safe_dump(synthetic_yaml), encoding="utf-8")
        try:
            updated_reqs = asyncio.run(
                hi.collect_decisions_file(
                    str(tmp), pending, getattr(state, "conflict_groups", None)
                )
            )
            for ur in updated_reqs:
                if (
                    ur.human_decision is not None
                    and ur.file_path not in state.human_decisions
                ):
                    state.human_decision_requests[ur.file_path] = ur
                    state.human_decisions[ur.file_path] = ur.human_decision
                    if ur.is_batch_decision:
                        groups_applied += 1
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass

    return {"files": files_applied, "groups": groups_applied}


def detect_current_phase(state: MergeState) -> DecisionPhase | None:
    """Infer which DecisionPhase a paused ``state`` is waiting on.

    The orchestrator does not record an explicit "awaiting_human stage" tag,
    so we infer from observable state. Order of checks matters: we pick the
    *most-specific* stage that has unresolved items.
    """
    from src.models.state import SystemStatus

    if state.status != SystemStatus.AWAITING_HUMAN:
        return None

    has_plan_pending = any(
        item.user_choice is None for item in state.pending_user_decisions
    )
    if has_plan_pending and state.plan_human_review is None:
        return DecisionPhase.PLAN_REVIEW

    if has_plan_pending and state.plan_human_review is not None:
        return DecisionPhase.CONFLICT_MARKER

    if any(
        req.human_decision is None for req in state.human_decision_requests.values()
    ):
        return DecisionPhase.CONFLICT_RESOLUTION

    if (
        getattr(state, "judge_verdict", None) is not None
        and getattr(state, "judge_resolution", None) is None
    ):
        return DecisionPhase.JUDGE_REVIEW

    return None
