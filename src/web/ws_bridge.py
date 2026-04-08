"""WebSocket bridge between Orchestrator state and React Ink TUI clients."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

import websockets
from websockets.asyncio.server import Server, ServerConnection

from src.models.state import MergeState
from src.models.decision import MergeDecision
from src.models.plan_review import PlanHumanReview, PlanHumanDecision

logger = logging.getLogger(__name__)


class MergeWSBridge:
    """Bridges MergeState changes to WebSocket TUI clients."""

    def __init__(self, state: MergeState) -> None:
        self._state = state
        self._clients: set[ServerConnection] = set()
        self._server: Server | None = None
        self._last_status: str = (
            state.status.value if hasattr(state.status, "value") else str(state.status)
        )

    async def start(self, host: str = "localhost", port: int = 8765) -> None:
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

    async def _handler(self, ws: ServerConnection) -> None:
        self._clients.add(ws)
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
                {
                    "round_number": r.round_number,
                    "verdict_result": r.verdict_result.value
                    if hasattr(r.verdict_result, "value")
                    else str(r.verdict_result),
                    "verdict_summary": r.verdict_summary,
                    "issues_count": r.issues_count,
                }
                for r in s.plan_review_log
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
        diffs: list[Any] = getattr(self._state, "_file_diffs", None) or []
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
                    "description": cp.description,
                    "severity": cp.severity,
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
            self._apply_plan_review(payload.get("decision", ""))
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

    def _apply_plan_review(self, decision: str) -> None:
        decision_map = {
            "approve": PlanHumanDecision.APPROVE,
            "reject": PlanHumanDecision.REJECT,
            "modify": PlanHumanDecision.MODIFY,
        }
        pd = decision_map.get(decision)
        if pd is None:
            return

        self._state.plan_human_review = PlanHumanReview(
            decision=pd,
            reviewer_name="tui_user",
            decided_at=datetime.now(),
        )
        logger.info("TUI plan review decision: %s", decision)

    async def broadcast_state_patch(self) -> None:
        """Send full state to all connected clients."""
        if not self._clients:
            return
        data = json.dumps(
            {
                "type": "state_snapshot",
                "payload": self._serialize_state(),
            },
            default=str,
        )
        await asyncio.gather(
            *(ws.send(data) for ws in self._clients),
            return_exceptions=True,
        )

    def notify_state_change(self, reason: str = "") -> None:
        """Called by the orchestrator observer hook (sync context).

        Schedules an async broadcast on the running event loop.
        """
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.broadcast_state_patch())
        except RuntimeError:
            pass

    def notify_agent_activity(self, agent: str, action: str) -> None:
        """Push agent activity notification to TUI clients."""
        try:
            loop = asyncio.get_running_loop()
            data = json.dumps(
                {
                    "type": "agent_activity",
                    "payload": {"agent": agent, "action": action},
                }
            )
            loop.create_task(self._broadcast_raw(data))
        except RuntimeError:
            pass

    async def _broadcast_raw(self, data: str) -> None:
        await asyncio.gather(
            *(ws.send(data) for ws in self._clients),
            return_exceptions=True,
        )
