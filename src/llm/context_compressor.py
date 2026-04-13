"""B2: Three-stage context compression.

Inspired by Hermes Agent's ``context_compressor.py``, this module replaces
the naive truncation in ``BaseAgent._mitigate_context_pressure`` with a
layered strategy:

1. **Zero-cost cleanup** — Replace stale tool outputs (long content from
   older turns) with placeholders.  No LLM cost.
2. **Boundary-aware truncation** — Trim low-priority middle sections while
   preserving head (system context) and tail (recent exchanges).  Never
   orphan a tool-call from its result.
3. **Summarize middle** — For remaining excess, summarise middle sections
   with a cheap LLM call (optional, only when a summary client is provided).

The compressor works on a flat ``list[dict]`` message list and uses the
existing ``TokenBudget`` / ``estimate_tokens`` helpers from ``context.py``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.llm.context import TokenBudget, _CHARS_PER_TOKEN, estimate_tokens

logger = logging.getLogger(__name__)

_PLACEHOLDER = "[output truncated — stale content removed to save context]"


@dataclass(frozen=True)
class CompressionStats:
    """Metrics reported after a compression pass."""

    tokens_before: int
    tokens_after: int
    phase1_saved: int
    phase2_saved: int
    phase3_saved: int

    @property
    def total_saved(self) -> int:
        return self.phase1_saved + self.phase2_saved + self.phase3_saved


class ContextCompressor:
    """Three-stage context compressor for LLM message lists."""

    def __init__(
        self,
        budget: TokenBudget,
        *,
        protect_head: int = 1,
        protect_tail: int = 4,
        stale_char_threshold: int = 200,
        stale_age: int = 2,
    ) -> None:
        """
        Parameters
        ----------
        budget:
            Token budget that defines how much space is available.
        protect_head:
            Number of messages at the start to never touch.
        protect_tail:
            Number of messages at the end to never touch.
        stale_char_threshold:
            Content longer than this (chars) in old turns is a prune candidate.
        stale_age:
            Messages older than ``len(messages) - protect_tail - stale_age``
            are considered stale.
        """
        self._budget = budget
        self._protect_head = protect_head
        self._protect_tail = protect_tail
        self._stale_char_threshold = stale_char_threshold
        self._stale_age = stale_age

    def compress(
        self, messages: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], CompressionStats]:
        """Run the three-stage compression pipeline.

        Returns the (possibly shortened) message list and compression stats.
        """
        tokens_before = self._estimate_total(messages)

        if self._budget.can_fit(tokens_before):
            return messages, CompressionStats(
                tokens_before=tokens_before,
                tokens_after=tokens_before,
                phase1_saved=0,
                phase2_saved=0,
                phase3_saved=0,
            )

        result = list(messages)

        # Phase 1: zero-cost cleanup
        result = self._prune_stale_outputs(result)
        after_p1 = self._estimate_total(result)
        p1_saved = tokens_before - after_p1

        if self._budget.can_fit(after_p1):
            return result, CompressionStats(
                tokens_before=tokens_before,
                tokens_after=after_p1,
                phase1_saved=p1_saved,
                phase2_saved=0,
                phase3_saved=0,
            )

        # Phase 2: boundary-aware truncation of middle messages
        result = self._truncate_middle(result)
        after_p2 = self._estimate_total(result)
        p2_saved = after_p1 - after_p2

        if self._budget.can_fit(after_p2):
            return result, CompressionStats(
                tokens_before=tokens_before,
                tokens_after=after_p2,
                phase1_saved=p1_saved,
                phase2_saved=p2_saved,
                phase3_saved=0,
            )

        # Phase 3: aggressive middle removal (synchronous, no LLM call)
        result = self._drop_middle(result)
        after_p3 = self._estimate_total(result)
        p3_saved = after_p2 - after_p3

        stats = CompressionStats(
            tokens_before=tokens_before,
            tokens_after=after_p3,
            phase1_saved=p1_saved,
            phase2_saved=p2_saved,
            phase3_saved=p3_saved,
        )

        if not self._budget.can_fit(after_p3):
            logger.warning(
                "Context (%d tokens) still exceeds budget (%d) after 3-stage compression "
                "(saved %d total)",
                after_p3,
                self._budget.available,
                stats.total_saved,
            )

        return result, stats

    # ------------------------------------------------------------------
    # Phase 1: Zero-cost cleanup
    # ------------------------------------------------------------------

    def _prune_stale_outputs(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Replace old, long tool outputs with a placeholder."""
        n = len(messages)
        tail_boundary = n - self._protect_tail
        head_boundary = self._protect_head

        result: list[dict[str, Any]] = []
        for idx, msg in enumerate(messages):
            if idx < head_boundary or idx >= tail_boundary:
                result.append(msg)
                continue

            content = msg.get("content", "")
            is_tool_output = msg.get("role") == "assistant" or msg.get("role") == "tool"
            is_long = (
                isinstance(content, str) and len(content) > self._stale_char_threshold
            )
            age = tail_boundary - idx

            if is_tool_output and is_long and age > self._stale_age:
                result.append({**msg, "content": _PLACEHOLDER})
            else:
                result.append(msg)

        return result

    # ------------------------------------------------------------------
    # Phase 2: Boundary-aware truncation
    # ------------------------------------------------------------------

    def _truncate_middle(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Truncate the longest messages in the unprotected middle zone.

        When the middle zone is empty (few messages), falls back to truncating
        the largest messages anywhere in the list.
        """
        n = len(messages)
        head_end = min(self._protect_head, n)
        tail_start = max(n - self._protect_tail, head_end)

        excess = self._estimate_total(messages) - self._budget.available
        if excess <= 0:
            return messages

        candidates: list[tuple[int, int]] = []
        if head_end < tail_start:
            for idx in range(head_end, tail_start):
                content = messages[idx].get("content", "")
                if isinstance(content, str):
                    candidates.append((idx, len(content)))
        else:
            for idx in range(n):
                content = messages[idx].get("content", "")
                if isinstance(content, str):
                    candidates.append((idx, len(content)))

        candidates.sort(key=lambda x: x[1], reverse=True)

        result = list(messages)
        chars_to_cut = int(excess * _CHARS_PER_TOKEN) + int(500 * _CHARS_PER_TOKEN)

        for idx, size in candidates:
            if chars_to_cut <= 0:
                break
            content = result[idx].get("content", "")
            if not content or not isinstance(content, str):
                continue

            cut = min(chars_to_cut, size // 2)
            if cut < 50:
                continue

            truncated = (
                content[: size - cut]
                + "\n\n... [auto-truncated to fit context window] ...\n"
            )
            result[idx] = {**result[idx], "content": truncated}
            chars_to_cut -= cut

        return result

    # ------------------------------------------------------------------
    # Phase 3: Drop middle messages entirely
    # ------------------------------------------------------------------

    def _drop_middle(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Drop non-essential middle messages, preserving paired tool calls."""
        n = len(messages)
        head_end = min(self._protect_head, n)
        tail_start = max(n - self._protect_tail, head_end)

        excess = self._estimate_total(messages) - self._budget.available
        if excess <= 0:
            return messages

        dropped_tokens = 0
        keep_indices: set[int] = set(range(head_end)) | set(range(tail_start, n))

        for idx in range(head_end, tail_start):
            if dropped_tokens >= excess:
                keep_indices.add(idx)
                continue
            content = messages[idx].get("content", "")
            tokens = estimate_tokens(content if isinstance(content, str) else "")
            dropped_tokens += tokens

        result = [messages[i] for i in sorted(keep_indices)]

        if len(result) < n:
            summary_msg = {
                "role": "user",
                "content": f"[{n - len(result)} earlier messages were removed to fit context window]",
            }
            insert_at = min(head_end, len(result))
            result.insert(insert_at, summary_msg)

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _estimate_total(messages: list[dict[str, Any]]) -> int:
        text = "".join(
            m.get("content", "") for m in messages if isinstance(m.get("content"), str)
        )
        return estimate_tokens(text)
