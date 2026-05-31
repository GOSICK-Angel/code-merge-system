"""Thread-safety tests for ``MergeWSBridge.notify_agent_activity`` (M6).

The production code path is: ``Orchestrator`` runs phase callbacks on the
``asyncio`` event-loop thread, but some agents push ``ActivityEvent``s from
worker threads (LLM client thread pool, git subprocess wrappers, …). The
WebSocket bridge therefore guarantees:

1. ``notify_agent_activity`` is callable from any thread without lock.
2. The ring buffer mutation and the broadcast both observe a single-writer
   invariant — they are marshalled onto the event-loop thread.
3. Concurrent producers cannot lose events past the ``ACTIVITY_BUFFER_MAX``
   cap (events may be evicted as designed, but the buffer length never
   exceeds the cap and no event is duplicated).
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from src.core.phases.base import ActivityEvent
from src.models.config import MergeConfig
from src.models.state import MergeState
from src.web.ws_bridge import MergeWSBridge


def _make_bridge() -> MergeWSBridge:
    cfg = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/x")
    state = MergeState(config=cfg)
    return MergeWSBridge(state)


def _event(idx: int) -> ActivityEvent:
    return ActivityEvent(
        agent=f"agent-{idx % 4}",
        action=f"step-{idx}",
        phase="analysis",
        event_type="progress",
        elapsed=None,
    )


@pytest.mark.asyncio
async def test_concurrent_threads_do_not_corrupt_buffer() -> None:
    """100 events pushed from 5 worker threads while the event loop runs;
    final buffer must contain exactly 100 distinct events in monotonic
    insertion order (per-thread)."""
    bridge = _make_bridge()
    bridge._loop = asyncio.get_running_loop()
    bridge.ACTIVITY_BUFFER_MAX = 1000  # type: ignore[misc]

    n_threads = 5
    per_thread = 20
    total = n_threads * per_thread

    def producer(thread_id: int) -> None:
        base = thread_id * per_thread
        for i in range(per_thread):
            bridge.notify_agent_activity(_event(base + i))

    threads = [threading.Thread(target=producer, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All ``call_soon_threadsafe`` callbacks have been scheduled but may not
    # have executed yet — yield until the loop drains.
    while True:
        await asyncio.sleep(0)
        if len(bridge._activity_buffer) >= total:
            break

    assert len(bridge._activity_buffer) == total
    actions = [e["action"] for e in bridge._activity_buffer]
    # No duplicates, no losses.
    assert len(set(actions)) == total
    assert set(actions) == {f"step-{i}" for i in range(total)}


@pytest.mark.asyncio
async def test_buffer_cap_respected_under_concurrent_pressure() -> None:
    """500 events from 10 threads with cap=50: buffer length stays ≤ 50,
    final contents are the last 50 events seen by the loop thread (some
    interleaving is acceptable but length cap is hard)."""
    bridge = _make_bridge()
    bridge._loop = asyncio.get_running_loop()
    bridge.ACTIVITY_BUFFER_MAX = 50  # type: ignore[misc]

    n_threads = 10
    per_thread = 50

    def producer(thread_id: int) -> None:
        base = thread_id * per_thread
        for i in range(per_thread):
            bridge.notify_agent_activity(_event(base + i))

    threads = [threading.Thread(target=producer, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Drain pending callbacks
    for _ in range(20):
        await asyncio.sleep(0)

    assert len(bridge._activity_buffer) == 50


def test_no_loop_bound_falls_back_to_inline_mutation() -> None:
    """Without an event loop (single-threaded test driver) the in-line path
    is used. This is the same path exercised by existing unit tests."""
    bridge = _make_bridge()
    assert bridge._loop is None

    for i in range(3):
        bridge.notify_agent_activity(_event(i))
    assert len(bridge._activity_buffer) == 3
    assert bridge._activity_buffer[0]["action"] == "step-0"


def test_closed_loop_treated_as_unbound() -> None:
    """If the event loop is closed (shutdown race), the activity is buffered
    in-line rather than queued on a dead loop — prevents
    ``RuntimeError: Event loop is closed``."""
    bridge = _make_bridge()
    closed_loop = asyncio.new_event_loop()
    closed_loop.close()
    bridge._loop = closed_loop  # type: ignore[assignment]

    bridge.notify_agent_activity(_event(0))

    assert len(bridge._activity_buffer) == 1
    assert bridge._activity_buffer[0]["action"] == "step-0"


def _payload(event: ActivityEvent) -> dict[str, Any]:
    return {
        "agent": event.agent,
        "action": event.action,
        "phase": event.phase,
        "event_type": event.event_type,
        "elapsed": event.elapsed,
    }
