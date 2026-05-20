"""Provider connectivity probes for the Setup wizard.

Issues a minimal real round-trip against a provider endpoint with the
supplied credentials + base URL so the Web UI can tell the user whether
each configured model is actually reachable before a run starts. Reuses
``LLMClientFactory`` (via its override hooks) so the probe exercises the
exact construction path a run will use — including ``/v1`` normalisation
and reasoning-model handling — without mutating ``os.environ``.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from src.llm.client import (
    LLMClientFactory,
    _is_openai_reasoning_model,
    _OPENAI_REASONING_MIN_MAX_TOKENS,
)
from src.llm.error_classifier import classify_error
from src.models.config import AgentLLMConfig

# A connectivity probe should fail fast — a healthy endpoint answers a
# one-token prompt well within this window, and the user is waiting on
# the result interactively.
_PROBE_TIMEOUT_SECONDS = 30
_PROBE_PROMPT = "ping"


@dataclass(frozen=True)
class ModelProbeResult:
    model: str
    ok: bool
    latency_ms: int | None
    detail: str


async def probe_model(
    provider: str,
    model: str,
    api_key: str,
    base_url: str | None,
    *,
    timeout: int = _PROBE_TIMEOUT_SECONDS,
) -> ModelProbeResult:
    """Run a single minimal completion against ``model`` and report the result.

    Never raises — any failure is caught, classified, and returned as a
    ``ModelProbeResult`` with ``ok=False`` so callers can probe a whole
    list concurrently without one bad model aborting the batch.
    """
    # Reasoning models share max_tokens between hidden reasoning and visible
    # output; sizing the probe to the reasoning floor avoids both an empty
    # response and the factory's auto-bump warning.
    max_tokens = (
        _OPENAI_REASONING_MIN_MAX_TOKENS if _is_openai_reasoning_model(model) else 512
    )
    config = AgentLLMConfig(
        provider=provider,  # type: ignore[arg-type]
        model=model,
        max_tokens=max_tokens,
        request_timeout_seconds=timeout,
        cache_strategy="none",
    )
    t0 = time.monotonic()
    try:
        client = LLMClientFactory.create(
            config, api_key_override=api_key, base_url_override=base_url
        )
        text = await client.complete([{"role": "user", "content": _PROBE_PROMPT}])
        latency_ms = int((time.monotonic() - t0) * 1000)
        if not text.strip():
            return ModelProbeResult(model, False, latency_ms, "empty response")
        return ModelProbeResult(model, True, latency_ms, "ok")
    except Exception as e:
        classified = classify_error(e, provider)
        return ModelProbeResult(
            model, False, None, f"{classified.category}: {classified.message[:200]}"
        )


async def probe_provider(
    provider: str,
    models: list[str],
    api_key: str,
    base_url: str | None,
    *,
    timeout: int = _PROBE_TIMEOUT_SECONDS,
) -> list[ModelProbeResult]:
    """Probe every model in ``models`` concurrently."""
    if not models:
        return []
    return list(
        await asyncio.gather(
            *(
                probe_model(provider, m, api_key, base_url, timeout=timeout)
                for m in models
            )
        )
    )
