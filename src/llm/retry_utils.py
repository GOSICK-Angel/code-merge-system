"""Decorrelated jittered backoff for LLM retries.

Uses a monotonic counter XOR'd with nanosecond time to seed each
call's RNG, preventing the "thundering herd" effect when multiple
agents retry concurrently.  Inspired by Hermes Agent.
"""

from __future__ import annotations

import random
import threading
import time

_counter: int = 0
_lock = threading.Lock()


def jittered_backoff(
    attempt: int,
    base: float = 1.0,
    max_delay: float = 60.0,
) -> float:
    """Compute a jittered exponential backoff delay.

    The jitter is decorrelated across concurrent callers by using a
    per-call seed derived from a monotonic counter and wall-clock
    nanoseconds — no two concurrent calls share the same RNG state.

    Parameters
    ----------
    attempt:
        Zero-based retry attempt index.
    base:
        Base delay in seconds (applied before exponential growth).
    max_delay:
        Upper bound on the total delay (before jitter addition).

    Returns
    -------
    float
        Delay in seconds, always >= 0.
    """
    global _counter
    with _lock:
        _counter += 1
        tick = _counter
    seed = (time.time_ns() ^ (tick * 0x9E3779B9)) & 0xFFFFFFFF
    rng = random.Random(seed)
    delay: float = min(base * (2**attempt), max_delay)
    jitter: float = float(rng.uniform(0, delay * 0.5))
    return float(delay + jitter)
