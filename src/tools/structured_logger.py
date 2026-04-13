"""Structured JSON log formatter (C4).

Produces one JSON object per log line, making logs machine-parseable
for external observability tools (ELK, Loki, CloudWatch, etc.).

Usage::

    handler = logging.FileHandler("run.jsonl")
    handler.setFormatter(StructuredFormatter())
    logging.getLogger().addHandler(handler)
"""

from __future__ import annotations

import json
import logging
from typing import Any


class StructuredFormatter(logging.Formatter):
    """Emit each log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        log_data: dict[str, Any] = {
            "ts": record.created,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1] is not None:
            log_data["exception"] = self.formatException(record.exc_info)

        extra: dict[str, Any] = getattr(record, "extra", {})
        if extra:
            log_data.update(extra)

        return json.dumps(log_data, ensure_ascii=False, default=str)


def create_structured_handler(
    path: str,
    level: int = logging.DEBUG,
) -> logging.FileHandler:
    """Create a file handler with the structured JSON formatter."""
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(StructuredFormatter())
    handler.setLevel(level)
    return handler
