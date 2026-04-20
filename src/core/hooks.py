"""Lifecycle hook system (C1).

Provides a publish/subscribe mechanism for cross-cutting concerns
(logging, checkpoints, cost tracking, notifications) without coupling
them to the phase execution pipeline.

Error isolation: a failing hook never blocks the pipeline.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HookEvent:
    """Payload delivered to every handler registered for a given event name."""

    name: str
    data: dict[str, Any] = field(default_factory=dict)


HOOK_LLM_START = "agent:llm_start"
HOOK_LLM_END = "agent:llm_end"

# Handler signature: sync or async callable accepting **kwargs.
HookHandler = Callable[..., Any]


class HookManager:
    """Registry + dispatcher for lifecycle hooks.

    Usage::

        hooks = HookManager()
        hooks.on("phase:before", my_handler)
        await hooks.emit("phase:before", phase="planning", state=state)

    Wildcard matching: a handler registered for ``"phase:*"`` receives
    every event whose name starts with ``"phase:"``.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[HookHandler]] = defaultdict(list)

    def on(self, event: str, handler: HookHandler) -> None:
        """Register *handler* for *event*.

        *event* may end with ``"*"`` for wildcard matching.
        """
        self._handlers[event].append(handler)

    def off(self, event: str, handler: HookHandler) -> None:
        """Remove a previously registered handler."""
        handlers = self._handlers.get(event)
        if handlers and handler in handlers:
            handlers.remove(handler)

    async def emit(self, event: str, **kwargs: Any) -> list[Any]:
        """Fire *event*, passing *kwargs* to every matching handler.

        Returns a list of handler results (``None`` for failed handlers).
        Errors are logged but never propagated — the pipeline continues.
        """
        results: list[Any] = []
        for handler in self._matching_handlers(event):
            try:
                result = handler(**kwargs)
                if asyncio.iscoroutine(result):
                    result = await result
                results.append(result)
            except Exception as exc:
                logger.warning(
                    "Hook handler failed for event %r: %s",
                    event,
                    exc,
                )
                results.append(None)
        return results

    def clear(self) -> None:
        """Remove all handlers."""
        self._handlers.clear()

    @property
    def handler_count(self) -> int:
        return sum(len(hs) for hs in self._handlers.values())

    def _matching_handlers(self, event: str) -> list[HookHandler]:
        """Return handlers for exact match + wildcard patterns."""
        matched: list[HookHandler] = []
        for pattern, handlers in self._handlers.items():
            if pattern == event:
                matched.extend(handlers)
            elif pattern.endswith("*") and event.startswith(pattern[:-1]):
                matched.extend(handlers)
        return matched
