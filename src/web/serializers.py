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
                "risk_level": _enum_value(fd.risk_level),
                "risk_score": fd.risk_score,
                "lines_added": fd.lines_added,
                "lines_deleted": fd.lines_deleted,
                "language": fd.language,
                "is_security_sensitive": fd.is_security_sensitive,
                "change_category": _enum_value(fd.change_category)
                if fd.change_category
                else None,
                "raw_diff": fd.raw_diff[:5000] if fd.raw_diff else "",
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
                "phase": _enum_value(b.phase),
                "file_paths": b.file_paths,
                "risk_level": _enum_value(b.risk_level),
                "layer_id": b.layer_id,
                "change_category": _enum_value(b.change_category)
                if b.change_category
                else None,
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
        "special_instructions": plan.special_instructions,
    }


def _severity_from_confidence(confidence: float) -> str:
    if confidence >= 0.7:
        return "high"
    if confidence >= 0.4:
        return "medium"
    return "low"


def _serialize_change_intent(intent: Any) -> dict[str, Any] | None:
    if intent is None:
        return None
    return {
        "description": getattr(intent, "description", ""),
        "intent_type": getattr(intent, "intent_type", ""),
        "confidence": getattr(intent, "confidence", 0.0),
    }


def serialize_conflict_point(cp: Any) -> dict[str, Any]:
    """Full ConflictPoint payload for the L3 diff marker overlay.

    Older snapshots (Phase 1 era) only contained ``description / severity /
    line_range``; the L3 view needs the upstream/fork intents and risk
    factors to drive the marker hover panel. New fields are added; legacy
    ones are preserved so any older client that still binds to them keeps
    working.
    """
    return {
        "conflict_id": getattr(cp, "conflict_id", None),
        "hunk_id": getattr(cp, "hunk_id", None),
        "conflict_type": _enum_value(cp.conflict_type),
        "description": f"{_enum_value(cp.conflict_type)}: {cp.rationale}",
        "severity": _severity_from_confidence(cp.confidence),
        "line_range": getattr(cp, "line_range", ""),
        "upstream_intent": _serialize_change_intent(
            getattr(cp, "upstream_intent", None)
        ),
        "fork_intent": _serialize_change_intent(getattr(cp, "fork_intent", None)),
        "can_coexist": getattr(cp, "can_coexist", None),
        "suggested_decision": _enum_value(getattr(cp, "suggested_decision", None)),
        "confidence": cp.confidence,
        "rationale": cp.rationale,
        "risk_factors": list(getattr(cp, "risk_factors", []) or []),
    }


def serialize_human_request(req: Any) -> dict[str, Any]:
    return {
        "request_id": getattr(req, "request_id", None),
        "file_path": req.file_path,
        "priority": req.priority,
        "conflict_points": [serialize_conflict_point(cp) for cp in req.conflict_points],
        "context_summary": req.context_summary,
        "upstream_change_summary": req.upstream_change_summary,
        "fork_change_summary": req.fork_change_summary,
        "analyst_recommendation": _enum_value(req.analyst_recommendation),
        "analyst_confidence": req.analyst_confidence,
        "analyst_rationale": req.analyst_rationale,
        "options": [
            {
                "option_key": o.option_key,
                "decision": _enum_value(o.decision),
                "description": o.description,
                "preview_content": getattr(o, "preview_content", None),
                "risk_warning": getattr(o, "risk_warning", None),
            }
            for o in req.options
        ],
        "human_decision": _enum_value(req.human_decision),
        "custom_content": getattr(req, "custom_content", None),
        "reviewer_notes": getattr(req, "reviewer_notes", None),
        "related_files": list(getattr(req, "related_files", []) or []),
    }


def serialize_judge_verdict(state: MergeState) -> dict[str, Any] | None:
    v = state.judge_verdict
    if v is None:
        return None
    return {
        "verdict": _enum_value(v.verdict),
        "summary": v.summary,
        "failed_files": list(getattr(v, "failed_files", []) or []),
        "passed_files": list(getattr(v, "passed_files", []) or []),
        "conditional_files": list(getattr(v, "conditional_files", []) or []),
        "reviewed_files_count": getattr(v, "reviewed_files_count", 0),
        "critical_issues_count": getattr(v, "critical_issues_count", 0),
        "high_issues_count": getattr(v, "high_issues_count", 0),
        "overall_confidence": getattr(v, "overall_confidence", 0.0),
        "blocking_issues": list(getattr(v, "blocking_issues", []) or []),
        "issues": [
            {
                "issue_id": getattr(i, "issue_id", None),
                "file_path": i.file_path,
                "issue_type": i.issue_type,
                # ``JudgeIssue.issue_level`` is the canonical severity field;
                # older snapshots may also carry ``severity`` as a string —
                # fall back so legacy clients keep rendering.
                "severity": _enum_value(getattr(i, "issue_level", None))
                or getattr(i, "severity", "unknown"),
                "description": getattr(i, "description", ""),
                "suggested_fix": getattr(i, "suggested_fix", None),
                "must_fix_before_merge": getattr(i, "must_fix_before_merge", False),
                "resolvability": _enum_value(getattr(i, "resolvability", None)),
                "affected_lines": list(getattr(i, "affected_lines", []) or []),
            }
            for i in v.issues
        ],
        "veto_triggered": v.veto_triggered,
        "veto_reason": v.veto_reason,
        "repair_instructions": [
            {
                "file_path": getattr(r, "file_path", ""),
                "instruction": r.instruction,
                "is_repairable": r.is_repairable,
                "severity": _enum_value(getattr(r, "severity", None)),
                "source_issue_id": getattr(r, "source_issue_id", None),
            }
            for r in v.repair_instructions
        ],
    }


def serialize_plan_human_review(state: MergeState) -> dict[str, Any] | None:
    """L2 view detects 'server already decided' via this payload (M13).

    ``state.plan_human_review`` is set by ``ws_bridge._apply_user_plan_decisions``
    (and the CLI / decisions-YAML loader). When non-None, the L2 view
    treats the plan as final and disables Approve/Reject/Modify to
    prevent a double-submit race during the snapshot-debounce window.
    """
    review = state.plan_human_review
    if review is None:
        return None
    return {
        "decision": _enum_value(review.decision),
        "reviewer_name": review.reviewer_name,
        "reviewer_notes": review.reviewer_notes,
        "decided_at": review.decided_at.isoformat() if review.decided_at else None,
        "item_decisions_count": len(review.item_decisions),
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


def _serialize_cost_summary(state: MergeState) -> dict[str, Any] | None:
    """Enrich ``state.cost_summary`` with the U2 budget knobs the dashboard
    renders as a progress bar (green / amber / red).

    Returns ``None`` when no LLM activity has been recorded yet, matching
    the pre-existing front-end expectation. When activity exists, the
    payload always includes ``limit_usd`` (None when disabled) and
    ``warn_pct``; the dashboard hides the bar when ``limit_usd is None``.
    """
    summary = state.cost_summary
    if summary is None:
        return None
    enriched: dict[str, Any] = dict(summary)
    enriched["limit_usd"] = state.config.max_cost_usd
    enriched["warn_pct"] = state.config.per_run_cost_warn_pct
    return enriched


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
        "judgeResolution": state.judge_resolution,
        "rerunRound": state.rerun_round,
        "maxRerunRounds": state.config.max_rerun_rounds,
        "planHumanReview": serialize_plan_human_review(state),
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
                        "kind": opt.kind,
                        "preview": opt.preview,
                    }
                    for opt in item.options
                ],
                "user_choice": item.user_choice,
                "user_input": item.user_input,
                "custom_instruction": item.custom_instruction,
                "manual_resolution": item.manual_resolution,
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
        "costSummary": _serialize_cost_summary(state),
        "phaseElapsed": _phase_elapsed(state),
        "decisionRecordCounts": _decision_record_counts(state),
    }
