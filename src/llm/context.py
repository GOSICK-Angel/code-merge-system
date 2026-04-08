from __future__ import annotations

import logging
from enum import IntEnum
from typing import Literal

from pydantic import BaseModel

logger = logging.getLogger(__name__)

MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-opus-4-6": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
    "claude-3-5-sonnet-20241022": 200_000,
    "claude-3-5-haiku-20241022": 200_000,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-3.5-turbo": 16_385,
}

_DEFAULT_CONTEXT_WINDOW = 8_000
_CHARS_PER_TOKEN = 3.5
_SAFETY_MARGIN = 0.05


def estimate_tokens(text: str) -> int:
    return int(len(text) / _CHARS_PER_TOKEN)


def get_context_window(model: str) -> int:
    if model in MODEL_CONTEXT_WINDOWS:
        return MODEL_CONTEXT_WINDOWS[model]
    for prefix, window in MODEL_CONTEXT_WINDOWS.items():
        if model.startswith(prefix):
            return window
    return _DEFAULT_CONTEXT_WINDOW


class TokenBudget(BaseModel, frozen=True):
    model: str
    context_window: int
    reserved_for_output: int
    used: int = 0

    @property
    def available(self) -> int:
        margin = int(self.context_window * _SAFETY_MARGIN)
        return max(
            0,
            self.context_window - self.reserved_for_output - self.used - margin,
        )

    def consume(self, tokens: int) -> TokenBudget:
        return self.model_copy(update={"used": self.used + tokens})

    def can_fit(self, tokens: int) -> bool:
        return tokens <= self.available


class ContextPriority(IntEnum):
    CRITICAL = 0
    HIGH = 1
    MEDIUM = 2
    LOW = 3
    OPTIONAL = 4


class ContextSection(BaseModel):
    name: str
    content: str
    priority: ContextPriority
    min_tokens: int = 0
    can_truncate: bool = True
    truncation_strategy: Literal["tail", "head", "middle"] = "tail"


def _truncate_text(text: str, max_chars: int, strategy: str) -> str:
    if len(text) <= max_chars:
        return text
    marker = "\n\n... [truncated] ...\n\n"
    marker_len = len(marker)
    available = max_chars - marker_len
    if available <= 0:
        return text[:max_chars]

    if strategy == "tail":
        return text[:available] + marker
    if strategy == "head":
        return marker + text[-available:]
    half = available // 2
    return text[:half] + marker + text[-half:]


class ContextAssembler:
    def __init__(self, budget: TokenBudget) -> None:
        self._budget = budget
        self._sections: list[ContextSection] = []

    def add_section(self, section: ContextSection) -> None:
        self._sections.append(section)

    def build(self) -> tuple[str, TokenBudget]:
        sorted_sections = sorted(self._sections, key=lambda s: s.priority)

        section_tokens: list[tuple[ContextSection, int]] = []
        for section in sorted_sections:
            tokens = estimate_tokens(section.content)
            section_tokens.append((section, tokens))

        total_tokens = sum(t for _, t in section_tokens)
        budget = self._budget

        if total_tokens <= budget.available:
            joined = [s.content for s, _ in section_tokens]
            return "\n\n".join(joined), budget.consume(total_tokens)

        excess = total_tokens - budget.available

        for priority in reversed(list(ContextPriority)):
            if excess <= 0:
                break
            if priority == ContextPriority.CRITICAL:
                continue

            for i, (section, tokens) in enumerate(section_tokens):
                if excess <= 0:
                    break
                if section.priority != priority:
                    continue

                if not section.can_truncate:
                    section_tokens[i] = (section, 0)
                    excess -= tokens
                    continue

                if section.min_tokens > 0 and tokens > section.min_tokens:
                    target_chars = int(section.min_tokens * _CHARS_PER_TOKEN)
                    truncated_content = _truncate_text(
                        section.content, target_chars, section.truncation_strategy
                    )
                    new_section = section.model_copy(
                        update={"content": truncated_content}
                    )
                    new_tokens = estimate_tokens(truncated_content)
                    section_tokens[i] = (new_section, new_tokens)
                    excess -= tokens - new_tokens
                elif section.min_tokens == 0 and not section.can_truncate:
                    section_tokens[i] = (section, 0)
                    excess -= tokens
                else:
                    target_tokens = max(0, tokens - excess)
                    if target_tokens < section.min_tokens:
                        section_tokens[i] = (section, 0)
                        excess -= tokens
                    else:
                        target_chars = int(target_tokens * _CHARS_PER_TOKEN)
                        truncated_content = _truncate_text(
                            section.content,
                            target_chars,
                            section.truncation_strategy,
                        )
                        new_section = section.model_copy(
                            update={"content": truncated_content}
                        )
                        new_tokens = estimate_tokens(truncated_content)
                        section_tokens[i] = (new_section, new_tokens)
                        excess -= tokens - new_tokens

        parts: list[str] = []
        final_tokens = 0
        for section, tokens in section_tokens:
            if tokens == 0 and section.priority != ContextPriority.CRITICAL:
                continue
            parts.append(section.content)
            final_tokens += tokens

        if final_tokens > budget.available:
            logger.warning(
                "Context (%d tokens) still exceeds budget (%d) after truncation",
                final_tokens,
                budget.available,
            )

        return "\n\n".join(parts), budget.consume(final_tokens)
