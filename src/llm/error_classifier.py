"""Error classification for LLM API calls.

Classifies exceptions from Anthropic and OpenAI SDKs into semantic
categories, each with a recovery strategy (retry, compress, rotate,
fallback, or abort).  Inspired by Hermes Agent's error_classifier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ErrorCategory(StrEnum):
    AUTH_TRANSIENT = "auth_transient"
    AUTH_PERMANENT = "auth_permanent"
    RATE_LIMIT = "rate_limit"
    OVERLOAD = "overload"
    CONTEXT_OVERFLOW = "context_overflow"
    TRANSPORT = "transport"
    FORMAT = "format"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ClassifiedError:
    category: ErrorCategory
    retryable: bool
    should_compress: bool
    should_rotate: bool
    should_fallback: bool
    cooldown_seconds: float
    message: str

    @property
    def is_fatal(self) -> bool:
        return not self.retryable


_CONTEXT_OVERFLOW_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"maximum context length", re.IGNORECASE),
    re.compile(r"context.window", re.IGNORECASE),
    re.compile(r"token.limit", re.IGNORECASE),
    re.compile(r"too.many.tokens", re.IGNORECASE),
    re.compile(r"prompt is too long", re.IGNORECASE),
    re.compile(r"input.*too.*large", re.IGNORECASE),
    re.compile(r"max_tokens.*exceeded", re.IGNORECASE),
)

_OVERLOAD_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"overloaded", re.IGNORECASE),
    re.compile(r"capacity", re.IGNORECASE),
    re.compile(r"temporarily unavailable", re.IGNORECASE),
    re.compile(r"server.*busy", re.IGNORECASE),
)


def _get_status_code(error: Exception) -> int | None:
    """Extract HTTP status code from SDK exceptions."""
    code = getattr(error, "status_code", None)
    if isinstance(code, int):
        return code
    response = getattr(error, "response", None)
    if response is not None:
        code = getattr(response, "status_code", None)
        if isinstance(code, int):
            return code
    return None


def _get_error_message(error: Exception) -> str:
    body: Any = getattr(error, "body", None)
    if isinstance(body, dict):
        msg = body.get("message") or body.get("error", {}).get("message", "")
        if msg:
            return str(msg)
    return str(error)


def _matches_any(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(p.search(text) for p in patterns)


def _is_connection_error(error: Exception) -> bool:
    """Check whether the error is a transport/connection-level failure."""
    cls_name = type(error).__name__.lower()
    if "connection" in cls_name or "timeout" in cls_name:
        return True
    for base in type(error).__mro__:
        name = base.__name__.lower()
        if "connectionerror" in name or "timeouterror" in name:
            return True
    return isinstance(error, (ConnectionError, TimeoutError, OSError))


def classify_error(error: Exception, provider: str = "") -> ClassifiedError:
    """Classify an LLM API error into a semantic category with recovery hints."""
    status = _get_status_code(error)
    msg = _get_error_message(error)

    if _is_connection_error(error):
        return ClassifiedError(
            category=ErrorCategory.TRANSPORT,
            retryable=True,
            should_compress=False,
            should_rotate=False,
            should_fallback=False,
            cooldown_seconds=0,
            message=f"Transport error: {msg}",
        )

    if status == 401:
        return ClassifiedError(
            category=ErrorCategory.AUTH_PERMANENT,
            retryable=False,
            should_compress=False,
            should_rotate=True,
            should_fallback=True,
            cooldown_seconds=0,
            message=f"Authentication failed ({provider}): {msg}",
        )

    if status == 403:
        is_transient = "temporarily" in msg.lower() or "quota" in msg.lower()
        if is_transient:
            return ClassifiedError(
                category=ErrorCategory.AUTH_TRANSIENT,
                retryable=True,
                should_compress=False,
                should_rotate=True,
                should_fallback=False,
                cooldown_seconds=30,
                message=f"Transient auth issue ({provider}): {msg}",
            )
        return ClassifiedError(
            category=ErrorCategory.AUTH_PERMANENT,
            retryable=False,
            should_compress=False,
            should_rotate=True,
            should_fallback=True,
            cooldown_seconds=0,
            message=f"Permission denied ({provider}): {msg}",
        )

    if status == 429:
        retry_after = _extract_retry_after(error)
        return ClassifiedError(
            category=ErrorCategory.RATE_LIMIT,
            retryable=True,
            should_compress=False,
            should_rotate=False,
            should_fallback=False,
            cooldown_seconds=retry_after,
            message=f"Rate limited ({provider}): {msg}",
        )

    if status == 400 and _matches_any(msg, _CONTEXT_OVERFLOW_PATTERNS):
        return ClassifiedError(
            category=ErrorCategory.CONTEXT_OVERFLOW,
            retryable=True,
            should_compress=True,
            should_rotate=False,
            should_fallback=False,
            cooldown_seconds=0,
            message=f"Context overflow ({provider}): {msg}",
        )

    if status == 400:
        return ClassifiedError(
            category=ErrorCategory.FORMAT,
            retryable=False,
            should_compress=False,
            should_rotate=False,
            should_fallback=False,
            cooldown_seconds=0,
            message=f"Bad request ({provider}): {msg}",
        )

    if status is not None and status >= 500:
        if _matches_any(msg, _OVERLOAD_PATTERNS):
            return ClassifiedError(
                category=ErrorCategory.OVERLOAD,
                retryable=True,
                should_compress=False,
                should_rotate=False,
                should_fallback=False,
                cooldown_seconds=5,
                message=f"Server overloaded ({provider}): {msg}",
            )
        return ClassifiedError(
            category=ErrorCategory.OVERLOAD,
            retryable=True,
            should_compress=False,
            should_rotate=False,
            should_fallback=False,
            cooldown_seconds=2,
            message=f"Server error {status} ({provider}): {msg}",
        )

    if _matches_any(msg, _CONTEXT_OVERFLOW_PATTERNS):
        return ClassifiedError(
            category=ErrorCategory.CONTEXT_OVERFLOW,
            retryable=True,
            should_compress=True,
            should_rotate=False,
            should_fallback=False,
            cooldown_seconds=0,
            message=f"Context overflow ({provider}): {msg}",
        )

    from src.llm.client import ParseError

    if isinstance(error, ParseError):
        return ClassifiedError(
            category=ErrorCategory.FORMAT,
            retryable=True,
            should_compress=False,
            should_rotate=False,
            should_fallback=False,
            cooldown_seconds=0,
            message=f"Parse/format error: {msg}",
        )

    return ClassifiedError(
        category=ErrorCategory.UNKNOWN,
        retryable=True,
        should_compress=False,
        should_rotate=False,
        should_fallback=False,
        cooldown_seconds=1,
        message=f"Unknown error ({type(error).__name__}): {msg}",
    )


def _extract_retry_after(error: Exception) -> float:
    """Try to extract Retry-After from SDK exception headers."""
    response = getattr(error, "response", None)
    if response is not None:
        headers = getattr(response, "headers", {})
        val = headers.get("retry-after") or headers.get("Retry-After")
        if val is not None:
            try:
                return max(1.0, float(val))
            except (ValueError, TypeError):
                pass
    return 30.0
