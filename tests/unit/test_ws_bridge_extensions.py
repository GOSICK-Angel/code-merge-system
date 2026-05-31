"""Tests for Phase 1 ``MergeWSBridge`` extensions:
- ``cancel_run`` accepted at AWAITING_HUMAN gate (sets event + wakes
  plan/human waiters); rejected with ``cancel_error`` elsewhere
- ``agent_activity`` ring buffer cap + ``agent_activity_replay`` frame on
  handshake
- ``is_cancelled()`` reflects the underlying ``_cancel_event``
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from src.core.phases.base import ActivityEvent
from src.models.config import MergeConfig
from src.models.state import MergeState, SystemStatus
from src.web.ws_bridge import MergeWSBridge


@dataclass
class _StubWS:
    """Captures outbound frames; mimics ``ServerConnection.send``."""

    sent: list[str] = field(default_factory=list)

    async def send(self, data: str) -> None:
        self.sent.append(data)


def _make_bridge(status: SystemStatus = SystemStatus.INITIALIZED) -> MergeWSBridge:
    cfg = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/x")
    state = MergeState(config=cfg, status=status)
    return MergeWSBridge(state)


class TestCancelRun:
    @pytest.mark.asyncio
    async def test_rejected_outside_human_gate(self) -> None:
        bridge = _make_bridge(SystemStatus.ANALYZING_CONFLICTS)
        ws = _StubWS()

        await bridge._handle_command(ws, {"type": "cancel_run", "payload": {}})  # type: ignore[arg-type]

        assert bridge.is_cancelled() is False
        assert len(ws.sent) == 1
        frame = json.loads(ws.sent[0])
        assert frame["type"] == "cancel_error"
        assert frame["payload"]["reason"] == "not_in_human_gate"
        assert frame["payload"]["current_status"] == "analyzing_conflicts"

    @pytest.mark.asyncio
    async def test_accepted_at_human_gate_sets_event_and_wakes_waiters(self) -> None:
        bridge = _make_bridge(SystemStatus.AWAITING_HUMAN)
        ws = _StubWS()

        plan_wait = asyncio.create_task(bridge.wait_for_plan_review())
        human_wait = asyncio.create_task(bridge.wait_for_human_decisions())
        # Yield once so the wait tasks register on the events.
        await asyncio.sleep(0)

        await bridge._handle_command(ws, {"type": "cancel_run", "payload": {}})  # type: ignore[arg-type]

        assert bridge.is_cancelled() is True
        # Both waiters must complete promptly (event was set by cancel handler).
        await asyncio.wait_for(plan_wait, timeout=0.2)
        await asyncio.wait_for(human_wait, timeout=0.2)
        # No cancel_error frame in the accept path.
        assert ws.sent == []

    @pytest.mark.asyncio
    async def test_is_cancelled_default_false(self) -> None:
        bridge = _make_bridge()
        assert bridge.is_cancelled() is False


class TestAgentActivityReplay:
    def _make_event(self, agent: str, action: str) -> ActivityEvent:
        return ActivityEvent(
            agent=agent,
            action=action,
            phase="analysis",
            event_type="progress",
            elapsed=None,
        )

    def test_buffer_appends_events(self) -> None:
        bridge = _make_bridge()
        # No event loop bound; notify_agent_activity still buffers locally
        # and only skips the broadcast leg — this is exactly the path used
        # by tests that don't spin up websockets.
        bridge.notify_agent_activity(self._make_event("planner", "start"))
        bridge.notify_agent_activity(self._make_event("planner", "complete"))
        assert len(bridge._activity_buffer) == 2
        assert bridge._activity_buffer[0]["agent"] == "planner"
        assert bridge._activity_buffer[0]["action"] == "start"

    def test_buffer_caps_at_max(self) -> None:
        bridge = _make_bridge()
        bridge.ACTIVITY_BUFFER_MAX = 5  # type: ignore[misc]
        for i in range(10):
            bridge.notify_agent_activity(self._make_event("planner", f"step-{i}"))
        assert len(bridge._activity_buffer) == 5
        # Oldest entries dropped — only step-5..step-9 survive.
        actions = [e["action"] for e in bridge._activity_buffer]
        assert actions == [f"step-{i}" for i in range(5, 10)]

    @pytest.mark.asyncio
    async def test_send_snapshot_replays_buffered_activity(self) -> None:
        bridge = _make_bridge()
        bridge.notify_agent_activity(self._make_event("planner", "start"))
        bridge.notify_agent_activity(self._make_event("executor", "complete"))

        ws = _StubWS()
        await bridge._send_snapshot(ws)  # type: ignore[arg-type]

        # Two frames: state_snapshot first, then agent_activity_replay.
        assert len(ws.sent) == 2
        snapshot_frame = json.loads(ws.sent[0])
        replay_frame = json.loads(ws.sent[1])
        assert snapshot_frame["type"] == "state_snapshot"
        assert replay_frame["type"] == "agent_activity_replay"
        assert len(replay_frame["payload"]["events"]) == 2
        assert replay_frame["payload"]["events"][0]["agent"] == "planner"

    @pytest.mark.asyncio
    async def test_send_snapshot_skips_replay_when_buffer_empty(self) -> None:
        bridge = _make_bridge()
        ws = _StubWS()
        await bridge._send_snapshot(ws)  # type: ignore[arg-type]

        assert len(ws.sent) == 1
        assert json.loads(ws.sent[0])["type"] == "state_snapshot"


class TestActivityBufferIsolation:
    """The buffer is per-bridge and survives the broadcast leg being a no-op
    — important because tests don't run inside an event-loop-bound bridge."""

    def test_buffer_independent_per_instance(self) -> None:
        b1 = _make_bridge()
        b2 = _make_bridge()
        b1.notify_agent_activity(
            ActivityEvent(
                agent="a", action="x", phase="p", event_type="start", elapsed=None
            )
        )
        assert len(b1._activity_buffer) == 1
        assert len(b2._activity_buffer) == 0


def _payload(frame: str) -> dict[str, Any]:
    return json.loads(frame)["payload"]  # type: ignore[no-any-return]
