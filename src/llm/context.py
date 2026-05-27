from __future__ import annotations

from enum import IntEnum
from typing import Literal

from pydantic import BaseModel

MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-opus-4-6": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
    "claude-3-5-sonnet-20241022": 200_000,
    "claude-3-5-haiku-20241022": 200_000,
    "gpt-5.4": 1_000_000,
    "gpt-4.1": 1_000_000,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-3.5-turbo": 16_385,
    "o3": 200_000,
    "o4-mini": 200_000,
}

_DEFAULT_CONTEXT_WINDOW = 128_000
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
