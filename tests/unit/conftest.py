"""Unit-test fixtures.

Ensures LLM env vars have dummy values so tests that instantiate agents (and
therefore construct an LLM client via `LLMClientFactory.create`) do not abort
when CI runs without real API keys. Unit tests never make real API calls —
the LLM client is mocked or replaced.
"""

from __future__ import annotations

import asyncio
import os

import pytest

for _key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
    if not os.environ.get(_key):
        os.environ[_key] = "test-key"


@pytest.fixture(autouse=True)
def _ensure_main_thread_event_loop():
    """Keep ``asyncio.get_event_loop()`` usable across sync tests.

    pytest-asyncio (auto mode) creates a fresh loop per async test and
    clears the policy's "current loop" slot on teardown. Sync tests that
    follow an async test and call the deprecated
    ``asyncio.get_event_loop()`` (e.g. ``test_agents_extended.py``,
    ~30 call sites) then crash with ``RuntimeError: There is no current
    event loop in thread 'MainThread'``. Re-priming the policy at the
    start of every test makes the legacy pattern work without rewriting
    each call site to ``asyncio.run`` / ``@pytest.mark.asyncio``.

    For async tests, pytest-asyncio's own ``event_loop`` fixture
    overrides the loop we set here, so this fixture is a no-op for them.
    """
    try:
        loop = asyncio.get_event_loop_policy().get_event_loop()
        if loop.is_closed():
            raise RuntimeError("loop closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    yield
