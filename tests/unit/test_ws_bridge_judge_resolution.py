"""Phase 4/5 — ``submit_judge_resolution`` handler tests.

The L4 view sends one of three resolutions (``accept`` / ``abort`` /
``rerun``); the back-end must:
1. Write the value onto ``state.judge_resolution``
2. Set the **dedicated** ``_judge_resolution_received`` event so the
   web run loop wakes only on judge-gate input (the Phase 5d hotfix
   stopped reusing ``_plan_review_received``)
3. Ignore invalid resolutions without corrupting state
4. ``cancel_run`` at the AWAITING_HUMAN gate must wake the judge waiter
   too so the run loop can exit cleanly regardless of which gate parked
   the state
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.models.config import MergeConfig
from src.models.state import MergeState, SystemStatus
from src.web.ws_bridge import MergeWSBridge


def _make_bridge() -> MergeWSBridge:
    cfg = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/x")
    state = MergeState(config=cfg, status=SystemStatus.AWAITING_HUMAN)
    return MergeWSBridge(state)


class _CapturingWS:
    """In-memory replacement for ``ServerConnection`` so we can inspect
    outbound cancel_error frames without standing up a real server."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, data: str) -> None:
        self.sent.append(data)


class TestSubmitJudgeResolution:
    @pytest.mark.parametrize("resolution", ["accept", "abort", "rerun"])
    @pytest.mark.asyncio
    async def test_valid_resolution_persists_and_wakes_dedicated_waiter(
        self, resolution: str
    ) -> None:
        bridge = _make_bridge()
        assert bridge._state.judge_resolution is None
        assert not bridge._judge_resolution_received.is_set()
        assert not bridge._plan_review_received.is_set()

        await bridge._handle_command(  # type: ignore[arg-type]
            _CapturingWS(),
            {
                "type": "submit_judge_resolution",
                "payload": {"resolution": resolution},
            },
        )

        assert bridge._state.judge_resolution == resolution
        assert bridge._judge_resolution_received.is_set()
        # Phase 5d invariant: judge resolution must NOT wake the
        # plan-review waiter — those gates are decoupled now.
        assert not bridge._plan_review_received.is_set()

    @pytest.mark.asyncio
    async def test_invalid_resolution_is_ignored(self) -> None:
        bridge = _make_bridge()
        await bridge._handle_command(  # type: ignore[arg-type]
            _CapturingWS(),
            {
                "type": "submit_judge_resolution",
                "payload": {"resolution": "not-a-real-resolution"},
            },
        )
        assert bridge._state.judge_resolution is None
        assert not bridge._judge_resolution_received.is_set()
        assert not bridge._plan_review_received.is_set()

    @pytest.mark.asyncio
    async def test_missing_payload_is_ignored(self) -> None:
        bridge = _make_bridge()
        await bridge._handle_command(  # type: ignore[arg-type]
            _CapturingWS(),
            {"type": "submit_judge_resolution"},
        )
        assert bridge._state.judge_resolution is None
        assert not bridge._judge_resolution_received.is_set()

    @pytest.mark.asyncio
    async def test_cancel_run_wakes_judge_waiter(self) -> None:
        """``cancel_run`` at AWAITING_HUMAN must set all three gate
        events so whichever waiter is parked picks up cancellation."""
        bridge = _make_bridge()
        assert not bridge._judge_resolution_received.is_set()

        await bridge._handle_command(  # type: ignore[arg-type]
            _CapturingWS(),
            {"type": "cancel_run", "payload": {}},
        )

        assert bridge.is_cancelled()
        assert bridge._plan_review_received.is_set()
        assert bridge._human_decisions_received.is_set()
        assert bridge._judge_resolution_received.is_set()

    @pytest.mark.asyncio
    async def test_wait_for_judge_resolution_clears_event(self) -> None:
        """``wait_for_judge_resolution()`` must auto-clear the event on
        return so a subsequent gate cycle blocks again."""
        bridge = _make_bridge()
        bridge._judge_resolution_received.set()
        await bridge.wait_for_judge_resolution()
        assert not bridge._judge_resolution_received.is_set()


def _unused(_: Any) -> None:  # pragma: no cover
    json.loads("{}")
