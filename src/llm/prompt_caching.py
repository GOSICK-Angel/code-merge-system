"""B1: Prompt caching via Anthropic cache_control.

Implements the System_and_N strategy inspired by Hermes Agent:
- System prompt is marked ``ephemeral`` so Anthropic caches it across calls.
- The N most recent non-system messages are also marked ``ephemeral``
  to benefit from cache hits during multi-turn interactions (e.g.
  PlannerJudge revision loops, Judge repair loops).

OpenAI does not expose an equivalent mechanism, so markers are only
applied to Anthropic-bound payloads.
"""

from __future__ import annotations

import copy
import logging
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class CacheStrategy(str, Enum):
    """Prompt caching strategy for Anthropic API calls."""

    NONE = "none"
    SYSTEM_ONLY = "system_only"
    SYSTEM_AND_RECENT = "system_and_recent"


def apply_cache_markers(
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
    strategy: CacheStrategy = CacheStrategy.SYSTEM_AND_RECENT,
    recent_count: int = 3,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | str | None]:
    """Return (messages, system) with ``cache_control`` markers applied.

    The Anthropic API accepts ``cache_control`` on individual content blocks
    inside both the ``system`` parameter (when passed as a list of blocks)
    and ``messages[*].content`` (when content is a list of blocks).

    Parameters
    ----------
    messages:
        The conversation messages (user/assistant turns).
    system:
        The system prompt string (may be ``None``).
    strategy:
        Which caching strategy to apply.
    recent_count:
        How many recent user/assistant turns to mark (only for
        ``SYSTEM_AND_RECENT``).

    Returns
    -------
    tuple
        ``(cached_messages, cached_system)`` ready for ``messages.create``.
    """
    if strategy == CacheStrategy.NONE:
        return messages, system

    cached_system: list[dict[str, Any]] | str | None = system
    if system:
        cached_system = [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    if strategy == CacheStrategy.SYSTEM_ONLY:
        return messages, cached_system

    cached_messages = copy.deepcopy(messages)
    to_mark = cached_messages[-recent_count:] if recent_count > 0 else []
    for msg in to_mark:
        content = msg.get("content")
        if isinstance(content, str):
            msg["content"] = [
                {
                    "type": "text",
                    "text": content,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        elif isinstance(content, list):
            if content:
                last_block = content[-1]
                if isinstance(last_block, dict):
                    last_block["cache_control"] = {"type": "ephemeral"}

    return cached_messages, cached_system
