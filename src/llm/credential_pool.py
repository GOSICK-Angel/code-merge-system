"""Credential pool with cooldown-based rotation (C2).

Supports multiple API keys per provider.  When a key hits a rate limit
or transient auth failure, it enters a cooldown period and the pool
transparently rotates to the next available key.

Thread-safe: all state mutations are protected by a lock.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

DEFAULT_COOLDOWN_SECONDS = 3600


class AllCredentialsCoolingDown(RuntimeError):
    """Raised when no credential is available (all in cooldown)."""


@dataclass
class Credential:
    """A single API key with cooldown tracking."""

    key: str
    source: str = "env"
    cooldown_until: datetime | None = None

    @property
    def is_available(self) -> bool:
        if self.cooldown_until is None:
            return True
        return datetime.now(timezone.utc) >= self.cooldown_until

    @property
    def remaining_cooldown_seconds(self) -> float:
        if self.cooldown_until is None:
            return 0.0
        remaining = (self.cooldown_until - datetime.now(timezone.utc)).total_seconds()
        return max(0.0, remaining)


class CredentialPool:
    """Manages a pool of API credentials with automatic rotation.

    Usage::

        pool = CredentialPool.from_env_vars(["ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY_2"])
        cred = pool.get_active()
        # ... use cred.key ...
        # On rate limit:
        pool.cooldown(cred, seconds=3600)
        cred = pool.get_active()  # returns next available key
    """

    def __init__(self, credentials: list[Credential]) -> None:
        self._pool: list[Credential] = list(credentials)
        self._lock = threading.Lock()
        self._current_index: int = 0

    @classmethod
    def from_env_vars(cls, env_var_names: list[str]) -> CredentialPool:
        """Build a pool from environment variable names.

        Missing or empty env vars are silently skipped.
        """
        credentials: list[Credential] = []
        for name in env_var_names:
            value = os.environ.get(name, "").strip()
            if value:
                credentials.append(Credential(key=value, source=f"env:{name}"))
        return cls(credentials)

    @property
    def size(self) -> int:
        return len(self._pool)

    @property
    def available_count(self) -> int:
        with self._lock:
            return sum(1 for c in self._pool if c.is_available)

    def get_active(self) -> Credential:
        """Return the next available credential.

        Raises :class:`AllCredentialsCoolingDown` if none are usable.
        """
        with self._lock:
            if not self._pool:
                raise AllCredentialsCoolingDown("Credential pool is empty")

            for _ in range(len(self._pool)):
                cred = self._pool[self._current_index]
                self._current_index = (self._current_index + 1) % len(self._pool)
                if cred.is_available:
                    return cred

            soonest = min(c.remaining_cooldown_seconds for c in self._pool)
            raise AllCredentialsCoolingDown(
                f"All {len(self._pool)} credentials in cooldown "
                f"(soonest available in {soonest:.0f}s)"
            )

    def cooldown(
        self,
        cred: Credential,
        seconds: int = DEFAULT_COOLDOWN_SECONDS,
    ) -> None:
        """Put *cred* into cooldown for *seconds*."""
        with self._lock:
            cred.cooldown_until = datetime.now(timezone.utc) + timedelta(
                seconds=seconds
            )
            logger.info(
                "Credential %s in cooldown for %ds (until %s)",
                cred.source,
                seconds,
                cred.cooldown_until.isoformat(),
            )

    def reset(self, cred: Credential) -> None:
        """Remove cooldown from *cred*."""
        with self._lock:
            cred.cooldown_until = None
