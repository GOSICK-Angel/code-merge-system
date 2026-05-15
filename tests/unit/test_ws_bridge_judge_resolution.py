"""Phase 4 — ``submit_judge_resolution`` handler tests.

The L4 view sends one of three resolutions (``accept`` / ``abort`` /
``rerun``); the back-end must:
1. Write the value onto ``state.judge_resolution``
2. Set ``_plan_review_received`` so the web run loop can resume
3. Ignore invalid resolutions without corrupting state
"""

from __future__ import annotations

from typing import Any

import pytest

from src.models.config import MergeConfig
from src.models.state import MergeState, SystemStatus
from src.web.ws_bridge import MergeWSBridge


def _make_bridge() -> MergeWSBridge:
    cfg = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/x")
    state = MergeState(config=cfg, status=SystemStatus.AWAITING_HUMAN)
    return MergeWSBridge(state)


class _NoopWS:
    sent: list[str] = []

    async def send(self, data: str) -> None:  # pragma: no cover - unused
        self.sent.append(data)


class TestSubmitJudgeResolution:
    @pytest.mark.parametrize("resolution", ["accept", "abort", "rerun"])
    @pytest.mark.asyncio
    async def test_valid_resolution_persists_and_wakes_waiter(
        self, resolution: str
    ) -> None:
        bridge = _make_bridge()
        assert bridge._state.judge_resolution is None
        assert not bridge._plan_review_received.is_set()

        await bridge._handle_command(  # type: ignore[arg-type]
            _NoopWS(),
            {
                "type": "submit_judge_resolution",
                "payload": {"resolution": resolution},
            },
        )

        assert bridge._state.judge_resolution == resolution
        assert bridge._plan_review_received.is_set()

    @pytest.mark.asyncio
    async def test_invalid_resolution_is_ignored(self) -> None:
        bridge = _make_bridge()
        await bridge._handle_command(  # type: ignore[arg-type]
            _NoopWS(),
            {
                "type": "submit_judge_resolution",
                "payload": {"resolution": "not-a-real-resolution"},
            },
        )
        assert bridge._state.judge_resolution is None
        assert not bridge._plan_review_received.is_set()

    @pytest.mark.asyncio
    async def test_missing_payload_is_ignored(self) -> None:
        bridge = _make_bridge()
        await bridge._handle_command(  # type: ignore[arg-type]
            _NoopWS(),
            {"type": "submit_judge_resolution"},
        )
        assert bridge._state.judge_resolution is None
        assert not bridge._plan_review_received.is_set()


def _unused(_: Any) -> None:  # pragma: no cover
    return None
