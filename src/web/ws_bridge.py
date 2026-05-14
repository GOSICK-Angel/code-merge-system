"""WebSocket bridge between Orchestrator state and Web UI clients."""

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
from src.web.serializers import serialize_state

logger = logging.getLogger(__name__)


class MergeWSBridge:
    """Bridges MergeState changes to WebSocket TUI clients."""

    DEBOUNCE_SECONDS = 0.3
    ACTIVITY_BUFFER_MAX = 200

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
        self._cancel_event: asyncio.Event = asyncio.Event()
        self._activity_buffer: list[dict[str, Any]] = []
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

    def is_cancelled(self) -> bool:
        """Return True if a ``cancel_run`` command was accepted at an
        ``AWAITING_HUMAN`` gate. The caller (``_run_web`` loop) reads this
        between phase runs to decide whether to stop the orchestrator."""
        return self._cancel_event.is_set()

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
        if self._activity_buffer:
            await ws.send(
                json.dumps(
                    {
                        "type": "agent_activity_replay",
                        "payload": {"events": list(self._activity_buffer)},
                    }
                )
            )

    def _serialize_state(self) -> dict[str, Any]:
        return serialize_state(self._state)

    async def _handle_command(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        cmd_type = msg.get("type", "")
        payload = msg.get("payload", {})

        if cmd_type == "submit_decision":
            self._apply_decision(
                file_path=payload.get("filePath", ""),
                decision=payload.get("decision", ""),
            )
            await self.broadcast_state_patch()

        elif cmd_type == "submit_conflict_decisions_batch":
            self._apply_conflict_decisions_batch(payload.get("items", []))
            await self.broadcast_state_patch()

        elif cmd_type == "submit_plan_review":
            self._apply_plan_review(payload)
            await self.broadcast_state_patch()

        elif cmd_type == "submit_user_plan_decisions":
            self._apply_user_plan_decisions(payload.get("items", []))
            await self.broadcast_state_patch()

        elif cmd_type == "cancel_run":
            await self._handle_cancel_run(ws)

        elif cmd_type == "pause":
            logger.info("Pause requested by client")

        elif cmd_type == "resume":
            logger.info("Resume requested by client")

    async def _handle_cancel_run(self, ws: ServerConnection) -> None:
        """Cancel only takes effect when the run is parked at
        ``AWAITING_HUMAN`` — that's the only point where the orchestrator
        loop yields control back to ``_run_web``. Outside that gate we
        reply with a ``cancel_error`` frame so the UI can surface a
        tooltip / disabled-button state."""
        status = self._state.status
        status_val = status.value if hasattr(status, "value") else str(status)
        if status_val != "awaiting_human":
            await ws.send(
                json.dumps(
                    {
                        "type": "cancel_error",
                        "payload": {
                            "reason": "not_in_human_gate",
                            "current_status": status_val,
                        },
                    }
                )
            )
            logger.info(
                "cancel_run rejected: status=%s (not in human gate)", status_val
            )
            return

        self._cancel_event.set()
        # Wake up any waiter parked on plan/human events so the run loop
        # can re-check ``is_cancelled()`` and break out cleanly.
        self._plan_review_received.set()
        self._human_decisions_received.set()
        logger.info("cancel_run accepted at AWAITING_HUMAN gate")

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

    def _apply_conflict_decisions_batch(self, items: list[dict[str, Any]]) -> None:
        applied = 0
        for entry in items:
            file_path = entry.get("file_path", "")
            decision = entry.get("decision", "")
            if not file_path or not decision:
                continue
            req = self._state.human_decision_requests.get(file_path)
            if req is None:
                continue
            try:
                merge_decision = MergeDecision(decision)
            except ValueError:
                logger.warning(
                    "Skipping invalid decision %r for %s", decision, file_path
                )
                continue
            updated = req.model_copy(update={"human_decision": merge_decision})
            self._state.human_decision_requests[file_path] = updated
            self._state.human_decisions[file_path] = merge_decision
            applied += 1

        logger.info("TUI batch conflict decisions: %d/%d applied", applied, len(items))

        all_decided = bool(self._state.human_decision_requests) and all(
            r.human_decision is not None
            for r in self._state.human_decision_requests.values()
        )
        if all_decided:
            self._human_decisions_received.set()
            logger.info(
                "All human conflict decisions received (batch) — signalling orchestrator"
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

        self._state.plan_human_review = PlanHumanReview(
            decision=PlanHumanDecision.APPROVE,
            reviewer_name="tui_user",
            reviewer_notes=None,
            item_decisions=list(self._state.pending_user_decisions),
            decided_at=datetime.now(),
        )
        self._plan_review_received.set()
        logger.info("User plan decisions applied — signalling orchestrator")

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
        """Push structured agent activity to clients (thread-safe).

        Also stored in a ring buffer (max ``ACTIVITY_BUFFER_MAX``) so a
        client that connects mid-run receives the recent history via
        ``agent_activity_replay`` on handshake — without this, refreshing
        the browser would wipe the rolling stream.

        Thread-safety: when an event loop is bound, ``_activity_buffer``
        mutation **and** the broadcast are both marshalled to the loop
        thread via ``call_soon_threadsafe`` so they observe the same
        single-writer invariant as ``_send_snapshot`` (which reads the
        buffer on the loop thread). When no loop is bound (unit tests
        / single-threaded driver) we apply the buffer mutation in-line
        because there is no other reader/writer to race with.
        """
        payload: dict[str, Any] = {
            "agent": event.agent,
            "action": event.action,
            "phase": event.phase,
            "event_type": event.event_type,
            "elapsed": event.elapsed,
        }
        loop = self._loop
        if loop is None or loop.is_closed():
            self._append_to_activity_buffer(payload)
            return
        loop.call_soon_threadsafe(self._on_activity_event, payload)

    def _on_activity_event(self, payload: dict[str, Any]) -> None:
        """Runs on the event-loop thread — exclusive access to the
        activity buffer + client set."""
        self._append_to_activity_buffer(payload)
        data = json.dumps({"type": "agent_activity", "payload": payload})
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.create_task(self._broadcast_raw(data))

    def _append_to_activity_buffer(self, payload: dict[str, Any]) -> None:
        """Bounded ring buffer append. Single-writer guarantee from the
        caller — see ``notify_agent_activity`` for the threading model."""
        self._activity_buffer.append(payload)
        if len(self._activity_buffer) > self.ACTIVITY_BUFFER_MAX:
            self._activity_buffer = self._activity_buffer[-self.ACTIVITY_BUFFER_MAX :]

    async def _broadcast_raw(self, data: str) -> None:
        await asyncio.gather(
            *(ws.send(data) for ws in self._clients),
            return_exceptions=True,
        )
