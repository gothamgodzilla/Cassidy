"""Structured JSON logging.

Every vote attempt emits exactly one ``mix_vote`` record carrying ``user_id``,
``mix_id`` and ``vote_timestamp`` so that votes can be reconciled against the
database from logs alone. Secrets are never passed to the logger.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

LOGGER_NAME = "mangasm.voting"

# Attributes present on every LogRecord; anything else was supplied via `extra`
# and is promoted into the JSON payload.
_RESERVED = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()
    | {"asctime", "message", "taskName"}
)


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging(level: str = "INFO") -> logging.Logger:
    """Attach a single JSON handler to the service logger and return it."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level.upper())
    logger.propagate = False
    if not any(isinstance(h.formatter, JsonFormatter) for h in logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)
