"""WebSocket bridge between Orchestrator state and React Ink TUI clients."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime
from typing import Any

import websockets
from websockets.asyncio.server import Server, ServerConnection

from src.core.phases.base import ActivityEvent
from src.models.decision import MergeDecision
from src.models.plan_review import PlanHumanDecision, PlanHumanReview
from src.models.state import MergeState

logger = logging.getLogger(__name__)


class MergeWSBridge:
    """Bridges MergeState changes to WebSocket TUI clients."""

    DEBOUNCE_SECONDS = 0.3

    def __init__(self, state: MergeState) -> None:
        self._state = state
        self._clients: set[ServerConnection] = set()
        self._server: Server | None = None
        self._last_status: str = (
            state.status.value if hasattr(state.status, "value") else str(state.status)
        )
        self._debounce_handle: asyncio.TimerHandle | None = None
        self._pending_broadcast: bool = False
        self._last_snapshot_hash: str = ""
        self._client_connected: asyncio.Event = asyncio.Event()
        self._plan_review_received: asyncio.Event = asyncio.Event()
        self._human_decisions_received: asyncio.Event = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self, host: str = "localhost", port: int = 8765) -> None:
        self._loop = asyncio.get_running_loop()
        self._server = await websockets.serve(
            self._handler,
            host,
            port,
        )
        logger.info("WebSocket bridge listening on ws://%s:%d", host, port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def wait_for_client(self, timeout: float = 30.0) -> bool:
        """Block until at least one TUI client connects."""
        try:
            await asyncio.wait_for(self._client_connected.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def wait_for_plan_review(self) -> None:
        """Block until a plan review decision arrives from the TUI."""
        await self._plan_review_received.wait()
        self._plan_review_received.clear()

    async def wait_for_human_decisions(self) -> None:
        """Block until all pending conflict decisions are submitted from the TUI."""
        await self._human_decisions_received.wait()
        self._human_decisions_received.clear()

    async def _handler(self, ws: ServerConnection) -> None:
        self._clients.add(ws)
        self._client_connected.set()
        logger.info("TUI client connected (%d total)", len(self._clients))
        try:
            await self._send_snapshot(ws)
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                    await self._handle_command(ws, msg)
                except json.JSONDecodeError:
                    pass
        finally:
            self._clients.discard(ws)
            logger.info("TUI client disconnected (%d remaining)", len(self._clients))

    async def _send_snapshot(self, ws: ServerConnection) -> None:
        snapshot = self._serialize_state()
        await ws.send(
            json.dumps(
                {
                    "type": "state_snapshot",
                    "payload": snapshot,
                },
                default=str,
            )
        )

    def _serialize_state(self) -> dict[str, Any]:
        s = self._state
        return {
            "runId": s.run_id,
            "status": s.status.value if hasattr(s.status, "value") else str(s.status),
            "currentPhase": s.current_phase.value
            if hasattr(s.current_phase, "value")
            else str(s.current_phase),
            "phaseResults": {
                k: {
                    "phase": v.phase.value
                    if hasattr(v.phase, "value")
                    else str(v.phase),
                    "status": v.status,
                    "started_at": v.started_at.isoformat() if v.started_at else None,
                    "completed_at": v.completed_at.isoformat()
                    if v.completed_at
                    else None,
                    "error": v.error,
                }
                for k, v in s.phase_results.items()
            },
            "mergePlan": self._serialize_plan() if s.merge_plan else None,
            "fileClassifications": {
                k: (v.value if hasattr(v, "value") else str(v))
                for k, v in s.file_classifications.items()
            },
            "fileDiffs": self._serialize_file_diffs(),
            "fileDecisionRecords": {
                k: {
                    "file_path": v.file_path,
                    "decision": v.decision.value
                    if hasattr(v.decision, "value")
                    else str(v.decision),
                    "strategy_used": v.decision_source.value
                    if hasattr(v.decision_source, "value")
                    else str(v.decision_source),
                    "success": not v.is_rolled_back,
                    "error": v.rollback_reason,
                }
                for k, v in s.file_decision_records.items()
            },
            "humanDecisionRequests": {
                k: self._serialize_human_request(v)
                for k, v in s.human_decision_requests.items()
            },
            "humanDecisions": {
                k: (v.value if hasattr(v, "value") else str(v))
                for k, v in s.human_decisions.items()
            },
            "judgeVerdict": self._serialize_judge_verdict()
            if s.judge_verdict
            else None,
            "judgeRepairRounds": s.judge_repair_rounds,
            "planReviewLog": [
                self._serialize_review_round(r) for r in s.plan_review_log
            ],
            "reviewConclusion": self._serialize_review_conclusion(),
            "pendingUserDecisions": [
                {
                    "item_id": item.item_id,
                    "file_path": item.file_path,
                    "description": item.description,
                    "risk_context": item.risk_context,
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
                for item in s.pending_user_decisions
            ],
            "gateHistory": s.gate_history,
            "errors": s.errors,
            "messages": s.messages,
            "memory": {
                "phase_summaries": {
                    k: str(v.key_decisions) if hasattr(v, "key_decisions") else str(v)
                    for k, v in (
                        s.memory.phase_summaries.items() if s.memory else {}.items()
                    )
                },
                "entries": [
                    {"key": e.entry_id, "value": e.content, "phase": e.phase}
                    for e in (s.memory.entries if s.memory else [])
                ],
            },
            "createdAt": s.created_at.isoformat()
            if s.created_at
            else datetime.now().isoformat(),
        }

    def _serialize_file_diffs(self) -> list[dict[str, Any]]:
        diffs: list[Any] = self._state.file_diffs
        result: list[dict[str, Any]] = []
        for fd in diffs:
            result.append(
                {
                    "file_path": fd.file_path,
                    "risk_level": fd.risk_level.value
                    if hasattr(fd.risk_level, "value")
                    else str(fd.risk_level),
                    "risk_score": fd.risk_score,
                    "lines_added": fd.lines_added,
                    "lines_deleted": fd.lines_deleted,
                    "language": fd.language,
                    "is_security_sensitive": fd.is_security_sensitive,
                    "change_category": fd.change_category.value
                    if fd.change_category and hasattr(fd.change_category, "value")
                    else fd.change_category,
                    "raw_diff": fd.raw_diff[:5000]
                    if hasattr(fd, "raw_diff") and fd.raw_diff
                    else "",
                }
            )
        return result

    def _serialize_plan(self) -> dict[str, Any] | None:
        plan = self._state.merge_plan
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
                    "phase": b.phase.value
                    if hasattr(b.phase, "value")
                    else str(b.phase),
                    "file_paths": b.file_paths,
                    "risk_level": b.risk_level.value
                    if hasattr(b.risk_level, "value")
                    else str(b.risk_level),
                    "layer_id": b.layer_id,
                    "change_category": b.change_category.value
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
            "project_context_summary": plan.project_context_summary,
            "special_instructions": plan.special_instructions,
        }

    def _serialize_human_request(self, req: Any) -> dict[str, Any]:
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
                    "decision": o.decision.value
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

    def _serialize_judge_verdict(self) -> dict[str, Any] | None:
        v = self._state.judge_verdict
        if v is None:
            return None
        return {
            "verdict": v.verdict.value
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

    def _serialize_review_round(self, r: Any) -> dict[str, Any]:
        result: dict[str, Any] = {
            "round_number": r.round_number,
            "verdict_result": r.verdict_result.value
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
                    "action": pr.action.value
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
        return result

    def _serialize_review_conclusion(self) -> dict[str, Any] | None:
        rc = self._state.review_conclusion
        if rc is None:
            return None
        return {
            "reason": rc.reason.value
            if hasattr(rc.reason, "value")
            else str(rc.reason),
            "final_round": rc.final_round,
            "total_rounds": rc.total_rounds,
            "max_rounds": rc.max_rounds,
            "summary": rc.summary,
            "pending_decisions_count": rc.pending_decisions_count,
            "rejection_details": rc.rejection_details,
        }

    async def _handle_command(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        cmd_type = msg.get("type", "")
        payload = msg.get("payload", {})

        if cmd_type == "submit_decision":
            self._apply_decision(
                file_path=payload.get("filePath", ""),
                decision=payload.get("decision", ""),
            )
            await self.broadcast_state_patch()

        elif cmd_type == "submit_plan_review":
            self._apply_plan_review(payload)
            await self.broadcast_state_patch()

        elif cmd_type == "submit_user_plan_decisions":
            self._apply_user_plan_decisions(payload.get("items", []))
            await self.broadcast_state_patch()

        elif cmd_type == "pause":
            logger.info("Pause requested by TUI")

        elif cmd_type == "resume":
            logger.info("Resume requested by TUI")

    def _apply_decision(self, file_path: str, decision: str) -> None:
        req = self._state.human_decision_requests.get(file_path)
        if req is None:
            return
        try:
            merge_decision = MergeDecision(decision)
        except ValueError:
            return

        updated = req.model_copy(update={"human_decision": merge_decision})
        self._state.human_decision_requests[file_path] = updated
        self._state.human_decisions[file_path] = merge_decision
        logger.info("TUI decision: %s -> %s", file_path, decision)

        all_decided = all(
            r.human_decision is not None
            for r in self._state.human_decision_requests.values()
        )
        if all_decided:
            self._human_decisions_received.set()
            logger.info(
                "All human conflict decisions received — signalling orchestrator"
            )

    def _apply_plan_review(self, payload: Any) -> None:
        if isinstance(payload, str):
            decision_str = payload
            notes = None
        else:
            decision_str = payload.get("decision", "")
            notes = payload.get("notes")

        decision_map = {
            "approve": PlanHumanDecision.APPROVE,
            "reject": PlanHumanDecision.REJECT,
            "modify": PlanHumanDecision.MODIFY,
        }
        pd = decision_map.get(decision_str)
        if pd is None:
            return

        self._state.plan_human_review = PlanHumanReview(
            decision=pd,
            reviewer_name="tui_user",
            reviewer_notes=notes,
            item_decisions=list(self._state.pending_user_decisions),
            decided_at=datetime.now(),
        )
        self._plan_review_received.set()
        logger.info("TUI plan review decision: %s", decision_str)

    def _apply_user_plan_decisions(self, items: list[dict[str, Any]]) -> None:
        item_map = {item.item_id: item for item in self._state.pending_user_decisions}
        for item_data in items:
            item_id = item_data.get("item_id", "")
            if item_id not in item_map:
                continue
            existing = item_map[item_id]
            updated = existing.model_copy(
                update={
                    "user_choice": item_data.get("user_choice"),
                    "user_input": item_data.get("user_input"),
                }
            )
            idx = next(
                i
                for i, it in enumerate(self._state.pending_user_decisions)
                if it.item_id == item_id
            )
            self._state.pending_user_decisions[idx] = updated
        logger.info("TUI user plan decisions received: %d items", len(items))

    async def broadcast_state_patch(self) -> None:
        """Send full state to all connected clients, skipping if unchanged."""
        if not self._clients:
            return
        data = json.dumps(
            {
                "type": "state_snapshot",
                "payload": self._serialize_state(),
            },
            default=str,
        )
        data_hash = hashlib.md5(data.encode()).hexdigest()
        if data_hash == self._last_snapshot_hash:
            return
        self._last_snapshot_hash = data_hash
        await asyncio.gather(
            *(ws.send(data) for ws in self._clients),
            return_exceptions=True,
        )

    def notify_state_change(self, reason: str = "") -> None:
        """Called by the orchestrator observer hook (sync or thread context).

        Thread-safe: uses call_soon_threadsafe to schedule on the event loop.
        Debounces broadcasts within a 300ms window.
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            return

        loop.call_soon_threadsafe(self._schedule_debounced_broadcast)

    def _schedule_debounced_broadcast(self) -> None:
        """Schedule a debounced broadcast (must run on event loop thread)."""
        self._pending_broadcast = True

        if self._debounce_handle is not None:
            self._debounce_handle.cancel()

        loop = self._loop
        if loop is None:
            return

        self._debounce_handle = loop.call_later(
            self.DEBOUNCE_SECONDS,
            self._flush_broadcast,
        )

    def _flush_broadcast(self) -> None:
        """Fire the debounced broadcast."""
        if self._pending_broadcast and self._loop:
            self._pending_broadcast = False
            self._loop.create_task(self.broadcast_state_patch())

    def notify_agent_activity(self, event: ActivityEvent) -> None:
        """Push structured agent activity notification to TUI clients (thread-safe)."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        data = json.dumps(
            {
                "type": "agent_activity",
                "payload": {
                    "agent": event.agent,
                    "action": event.action,
                    "phase": event.phase,
                    "event_type": event.event_type,
                    "elapsed": event.elapsed,
                },
            }
        )
        loop.call_soon_threadsafe(loop.create_task, self._broadcast_raw(data))

    async def _broadcast_raw(self, data: str) -> None:
        await asyncio.gather(
            *(ws.send(data) for ws in self._clients),
            return_exceptions=True,
        )
