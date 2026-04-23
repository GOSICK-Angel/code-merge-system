"""Unit-test fixtures.

Ensures LLM env vars have dummy values so tests that instantiate agents (and
therefore construct an LLM client via `LLMClientFactory.create`) do not abort
when CI runs without real API keys. Unit tests never make real API calls —
the LLM client is mocked or replaced.
"""

from __future__ import annotations

import os

for _key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
    if not os.environ.get(_key):
        os.environ[_key] = "test-key"
