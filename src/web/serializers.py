"""Pure serializers for ``MergeState`` → JSON payloads consumed by the Web UI.

Extracted from ``ws_bridge.py`` (plan v1.1 §P2-1) so the WebSocket transport
layer stays thin and the state-snapshot shape can be unit-tested without
spinning up a websocket server.

All public functions return plain ``dict`` / ``list`` structures that are
``json.dumps`` ready. Field naming follows the front-end schema
(``runId`` / ``currentPhase`` / ``mergePlan`` …) and must remain
backward-compatible with the snapshot consumed by ``web/src/types/state.ts``.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from src.models.state import MergeState

_PROJECT_SUMMARY_MAX_LINES = 4
_PROJECT_SUMMARY_MAX_CHARS = 600
_INSTRUCTION_MAX_LINES = 6
_INSTRUCTION_MAX_CHARS = 800


def _enum_value(v: Any) -> Any:
    return v.value if hasattr(v, "value") else v


def truncate_project_summary(raw: str) -> str:
    if not raw:
        return raw
    lines = raw.splitlines()
    clipped_lines = lines[:_PROJECT_SUMMARY_MAX_LINES]
    joined = "\n".join(clipped_lines)
    if len(joined) > _PROJECT_SUMMARY_MAX_CHARS:
        joined = joined[:_PROJECT_SUMMARY_MAX_CHARS].rstrip() + "…"
    elif len(lines) > _PROJECT_SUMMARY_MAX_LINES:
        joined += "\n…"
    return joined


def truncate_instructions(items: list[str]) -> list[str]:
    """Defensive per-item cap; user-authored guidance is preserved in full
    when within bounds. Items that exceed the cap are tail-marked so the
    reviewer is alerted to consult the plan report for the full text."""
    out: list[str] = []
    for instr in items:
        lines = instr.splitlines()
        clipped = lines[:_INSTRUCTION_MAX_LINES]
        joined = "\n".join(clipped)
        overflow = False
        if len(joined) > _INSTRUCTION_MAX_CHARS:
            joined = joined[:_INSTRUCTION_MAX_CHARS].rstrip()
            overflow = True
        elif len(lines) > _INSTRUCTION_MAX_LINES:
            overflow = True
        if overflow:
            joined += "\n… (truncated — see plan report)"
        out.append(joined)
    return out


def serialize_file_diffs(state: MergeState) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for fd in state.file_diffs:
        result.append(
            {
                "file_path": fd.file_path,
                "risk_level": _enum_value(fd.risk_level)
                if hasattr(fd.risk_level, "value")
                else str(fd.risk_level),
                "risk_score": fd.risk_score,
                "lines_added": fd.lines_added,
                "lines_deleted": fd.lines_deleted,
                "language": fd.language,
                "is_security_sensitive": fd.is_security_sensitive,
                "change_category": _enum_value(fd.change_category)
                if fd.change_category and hasattr(fd.change_category, "value")
                else fd.change_category,
                "raw_diff": fd.raw_diff[:5000]
                if hasattr(fd, "raw_diff") and fd.raw_diff
                else "",
            }
        )
    return result


def serialize_plan(state: MergeState) -> dict[str, Any] | None:
    plan = state.merge_plan
    if plan is None:
        return None
    return {
        "plan_id": plan.plan_id,
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
        "upstream_ref": plan.upstream_ref,
        "fork_ref": plan.fork_ref,
        "merge_base_commit": plan.merge_base_commit,
        "phases": [
            {
                "batch_id": b.batch_id,
                "phase": _enum_value(b.phase)
                if hasattr(b.phase, "value")
                else str(b.phase),
                "file_paths": b.file_paths,
                "risk_level": _enum_value(b.risk_level)
                if hasattr(b.risk_level, "value")
                else str(b.risk_level),
                "layer_id": b.layer_id,
                "change_category": _enum_value(b.change_category)
                if b.change_category and hasattr(b.change_category, "value")
                else b.change_category,
            }
            for b in plan.phases
        ],
        "risk_summary": plan.risk_summary.model_dump(mode="json"),
        "category_summary": plan.category_summary.model_dump(mode="json")
        if plan.category_summary
        else None,
        "layers": [
            {
                "layer_id": ly.layer_id,
                "name": ly.name,
                "description": ly.description,
                "depends_on": ly.depends_on,
            }
            for ly in plan.layers
        ],
        "project_context_summary": truncate_project_summary(
            plan.project_context_summary
        ),
        "special_instructions": truncate_instructions(plan.special_instructions),
    }


def serialize_human_request(req: Any) -> dict[str, Any]:
    rec_val = req.analyst_recommendation
    if hasattr(rec_val, "value"):
        rec_val = rec_val.value

    return {
        "file_path": req.file_path,
        "priority": req.priority,
        "conflict_points": [
            {
                "description": f"{cp.conflict_type.value}: {cp.rationale}",
                "severity": (
                    "high"
                    if cp.confidence >= 0.7
                    else "medium"
                    if cp.confidence >= 0.4
                    else "low"
                ),
                "line_range": getattr(cp, "line_range", ""),
            }
            for cp in req.conflict_points
        ],
        "context_summary": req.context_summary,
        "upstream_change_summary": req.upstream_change_summary,
        "fork_change_summary": req.fork_change_summary,
        "analyst_recommendation": rec_val,
        "analyst_confidence": req.analyst_confidence,
        "analyst_rationale": req.analyst_rationale,
        "options": [
            {
                "option_key": o.option_key,
                "decision": _enum_value(o.decision)
                if hasattr(o.decision, "value")
                else str(o.decision),
                "description": o.description,
                "risk_warning": getattr(o, "risk_warning", None),
            }
            for o in req.options
        ],
        "human_decision": (
            req.human_decision.value
            if req.human_decision and hasattr(req.human_decision, "value")
            else req.human_decision
        ),
    }


def serialize_judge_verdict(state: MergeState) -> dict[str, Any] | None:
    v = state.judge_verdict
    if v is None:
        return None
    return {
        "verdict": _enum_value(v.verdict)
        if hasattr(v.verdict, "value")
        else str(v.verdict),
        "summary": v.summary,
        "issues": [
            {
                "file_path": i.file_path,
                "issue_type": i.issue_type,
                "severity": getattr(i, "severity", "unknown"),
                "description": getattr(i, "description", ""),
            }
            for i in v.issues
        ],
        "veto_triggered": v.veto_triggered,
        "veto_reason": v.veto_reason,
        "repair_instructions": [
            {"instruction": r.instruction, "is_repairable": r.is_repairable}
            for r in v.repair_instructions
        ],
    }


def serialize_review_round(r: Any) -> dict[str, Any]:
    return {
        "round_number": r.round_number,
        "verdict_result": _enum_value(r.verdict_result)
        if hasattr(r.verdict_result, "value")
        else str(r.verdict_result),
        "verdict_summary": r.verdict_summary,
        "issues_count": r.issues_count,
        "issues_detail": r.issues_detail,
        "planner_revision_summary": r.planner_revision_summary,
        "planner_responses": [
            {
                "issue_id": pr.issue_id,
                "file_path": pr.file_path,
                "action": _enum_value(pr.action)
                if hasattr(pr.action, "value")
                else str(pr.action),
                "reason": pr.reason,
                "counter_proposal": pr.counter_proposal,
            }
            for pr in (r.planner_responses or [])
        ],
        "plan_diff": [
            {
                "file_path": d.file_path,
                "old_risk": d.old_risk,
                "new_risk": d.new_risk,
            }
            for d in (r.plan_diff or [])
        ],
        "negotiation_messages": [
            {
                "sender": m.sender,
                "round_number": m.round_number,
                "content": m.content,
                "timestamp": m.timestamp.isoformat()
                if hasattr(m.timestamp, "isoformat")
                else str(m.timestamp),
            }
            for m in (r.negotiation_messages or [])
        ],
        "timestamp": r.timestamp.isoformat()
        if hasattr(r.timestamp, "isoformat")
        else str(r.timestamp),
    }


def serialize_review_conclusion(state: MergeState) -> dict[str, Any] | None:
    rc = state.review_conclusion
    if rc is None:
        return None
    return {
        "reason": _enum_value(rc.reason)
        if hasattr(rc.reason, "value")
        else str(rc.reason),
        "final_round": rc.final_round,
        "total_rounds": rc.total_rounds,
        "max_rounds": rc.max_rounds,
        "summary": rc.summary,
        "pending_decisions_count": rc.pending_decisions_count,
        "rejection_details": rc.rejection_details,
    }


def read_memory_snapshot(state: MergeState) -> dict[str, Any]:
    if not state.memory_db_path:
        return {"phase_summaries": {}, "entries": []}
    from pathlib import Path

    from src.memory.sqlite_store import SQLiteMemoryStore

    db = Path(state.memory_db_path)
    if not db.exists():
        return {"phase_summaries": {}, "entries": []}
    store = SQLiteMemoryStore.open(db)
    mem = store.to_memory()
    return {
        "phase_summaries": {
            k: str(v.key_decisions) if hasattr(v, "key_decisions") else str(v)
            for k, v in mem.phase_summaries.items()
        },
        "entries": [
            {"key": e.entry_id, "value": e.content, "phase": e.phase}
            for e in mem.entries
        ],
    }


def _phase_elapsed(state: MergeState) -> dict[str, float | None]:
    """For each completed phase compute wall-clock seconds; running phase
    returns None so the front-end can decide whether to render a live timer."""
    out: dict[str, float | None] = {}
    for k, v in state.phase_results.items():
        if v.started_at and v.completed_at:
            out[k] = (v.completed_at - v.started_at).total_seconds()
        else:
            out[k] = None
    return out


def _decision_record_counts(state: MergeState) -> dict[str, int]:
    """Aggregate ``file_decision_records`` by ``decision_source.value``.

    Useful for the L1 Dashboard's "decisions by source" chip (auto-merge vs
    plan-routed vs human-required vs human-confirmed)."""
    counter: Counter[str] = Counter()
    for rec in state.file_decision_records.values():
        src = rec.decision_source
        counter[_enum_value(src) if hasattr(src, "value") else str(src)] += 1
    return dict(counter)


def serialize_state(state: MergeState) -> dict[str, Any]:
    """Full ``MergeState`` → JSON snapshot.

    Layout mirrors the v1 ws_bridge implementation exactly so existing
    front-end consumers keep working; new fields (``costSummary``,
    ``phaseElapsed``, ``decisionRecordCounts``) are additive and may be
    absent on older snapshots — front-end code must treat them as
    ``Optional``.
    """
    return {
        "runId": state.run_id,
        "status": _enum_value(state.status)
        if hasattr(state.status, "value")
        else str(state.status),
        "currentPhase": _enum_value(state.current_phase)
        if hasattr(state.current_phase, "value")
        else str(state.current_phase),
        "phaseResults": {
            k: {
                "phase": _enum_value(v.phase)
                if hasattr(v.phase, "value")
                else str(v.phase),
                "status": v.status,
                "started_at": v.started_at.isoformat() if v.started_at else None,
                "completed_at": v.completed_at.isoformat() if v.completed_at else None,
                "error": v.error,
            }
            for k, v in state.phase_results.items()
        },
        "mergePlan": serialize_plan(state) if state.merge_plan else None,
        "fileClassifications": {
            k: (_enum_value(v) if hasattr(v, "value") else str(v))
            for k, v in state.file_classifications.items()
        },
        "fileDiffs": serialize_file_diffs(state),
        "fileDecisionRecords": {
            k: {
                "file_path": v.file_path,
                "decision": _enum_value(v.decision)
                if hasattr(v.decision, "value")
                else str(v.decision),
                "strategy_used": _enum_value(v.decision_source)
                if hasattr(v.decision_source, "value")
                else str(v.decision_source),
                "success": not v.is_rolled_back,
                "error": v.rollback_reason,
            }
            for k, v in state.file_decision_records.items()
        },
        "humanDecisionRequests": {
            k: serialize_human_request(v)
            for k, v in state.human_decision_requests.items()
        },
        "humanDecisions": {
            k: (_enum_value(v) if hasattr(v, "value") else str(v))
            for k, v in state.human_decisions.items()
        },
        "judgeVerdict": serialize_judge_verdict(state),
        "judgeRepairRounds": state.judge_repair_rounds,
        "planReviewLog": [serialize_review_round(r) for r in state.plan_review_log],
        "reviewConclusion": serialize_review_conclusion(state),
        "pendingUserDecisions": [
            {
                "item_id": item.item_id,
                "file_path": item.file_path,
                "description": item.description,
                "risk_context": item.risk_context,
                "conflict_preview": item.conflict_preview,
                "current_classification": item.current_classification,
                "options": [
                    {
                        "key": opt.key,
                        "label": opt.label,
                        "description": opt.description,
                    }
                    for opt in item.options
                ],
                "user_choice": item.user_choice,
                "user_input": item.user_input,
            }
            for item in state.pending_user_decisions
        ],
        "gateHistory": state.gate_history,
        "errors": state.errors,
        "messages": state.messages,
        "memory": read_memory_snapshot(state),
        "createdAt": state.created_at.isoformat()
        if state.created_at
        else datetime.now().isoformat(),
        "costSummary": state.cost_summary,
        "phaseElapsed": _phase_elapsed(state),
        "decisionRecordCounts": _decision_record_counts(state),
    }
