"""Mock MergeWSBridge driver for Web UI development.

Boots a real ``MergeWSBridge`` (so the on-wire schema is identical to
production) bound to a fabricated ``MergeState``, then drip-feeds a few
agent activity frames and a state-change transition so the L1 Dashboard
has something interesting to render. Cancel command echoes follow the
production code path — no shims.

Run alongside ``cd web && npm run dev``; see ``web/dev/README.md``.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Make ``src.*`` importable regardless of CWD — the script can be launched
# from anywhere (``python web/dev/mock-bridge.py`` or from inside web/).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.core.phases.base import ActivityEvent  # noqa: E402
from src.models.config import MergeConfig  # noqa: E402
from src.models.plan import MergePhase  # noqa: E402
from src.models.state import MergeState, PhaseResult, SystemStatus  # noqa: E402
from src.web.ws_bridge import MergeWSBridge  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("mock-bridge")


def _make_state() -> MergeState:
    cfg = MergeConfig(upstream_ref="upstream/main", fork_ref="feat/web")
    state = MergeState(config=cfg)
    state.status = SystemStatus.PLANNING
    start = datetime.now() - timedelta(seconds=42)
    state.phase_results["analysis"] = PhaseResult(
        phase=MergePhase.ANALYSIS,
        status="completed",
        started_at=start,
        completed_at=start + timedelta(seconds=12),
    )
    state.phase_results["plan_review"] = PhaseResult(
        phase=MergePhase.PLAN_REVIEW,
        status="running",
        started_at=start + timedelta(seconds=12),
    )
    state.cost_summary = {
        "total_cost_usd": 0.4231,
        "total_tokens": 18_452,
        "by_agent": {
            "planner": {"cost_usd": 0.21, "tokens": 9_200},
            "conflict_analyst": {"cost_usd": 0.21, "tokens": 9_252},
        },
    }
    return state


async def _drip_activity(bridge: MergeWSBridge) -> None:
    """Stream a few agent activity events so the L1 stream is non-empty."""
    sample = [
        ("planner", "Drafting layered plan", "analysis", "start", None),
        ("planner", "Layer 0 routed: 12 files", "analysis", "progress", 1.2),
        ("planner", "Plan v1 emitted (3 layers)", "analysis", "complete", 5.4),
        ("planner_judge", "Reviewing plan v1", "plan_review", "start", None),
        (
            "planner_judge",
            "2 risk_score deltas accepted",
            "plan_review",
            "progress",
            2.1,
        ),
    ]
    for agent, action, phase, evt, elapsed in sample:
        bridge.notify_agent_activity(
            ActivityEvent(
                agent=agent,
                action=action,
                phase=phase,
                event_type=evt,
                elapsed=elapsed,
            )
        )
        await asyncio.sleep(1.5)


async def _eventually_park_at_human(bridge: MergeWSBridge, state: MergeState) -> None:
    """After the warm-up, flip to AWAITING_HUMAN so the Cancel button lights
    up. The real cancel handler runs from this point — exactly what the
    production code does."""
    await asyncio.sleep(10)
    state.status = SystemStatus.AWAITING_HUMAN
    bridge.notify_state_change("parked at AWAITING_HUMAN (mock)")
    logger.info("State -> AWAITING_HUMAN (Cancel button should be enabled)")


async def main() -> None:
    state = _make_state()
    bridge = MergeWSBridge(state)
    await bridge.start("localhost", 8765)
    logger.info("Mock bridge ready on ws://localhost:8765")
    logger.info("Open http://localhost:5173/?ws=8765 in a browser.")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    drip_task = asyncio.create_task(_drip_activity(bridge))
    park_task = asyncio.create_task(_eventually_park_at_human(bridge, state))

    try:
        await stop.wait()
    finally:
        drip_task.cancel()
        park_task.cancel()
        await bridge.stop()
        logger.info("Mock bridge stopped.")


if __name__ == "__main__":
    asyncio.run(main())
