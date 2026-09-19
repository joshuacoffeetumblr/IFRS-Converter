"""Structured logging (spec §29) with financial-data redaction (spec §32).

Uploaded statements are sensitive corporate information. Amounts and raw account
labels must not leak into application logs; the audit tables are the one
intentional, access-controlled place those values live (ERD `audit_logs`).
"""

from __future__ import annotations

import logging
from collections.abc import MutableMapping
from typing import Any

import structlog

#: Keys whose values are replaced before a log event is emitted.
REDACTED_KEYS = frozenset(
    {
        "amount",
        "amounts",
        "raw_value",
        "raw_label",
        "original_account",
        "excerpt",
        "ai_reasoning",
        "ai_raw_response",
        "before",
        "after",
    }
)

REDACTED = "[REDACTED]"


def redact_financial_data(
    _logger: Any,
    _method: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    for key in list(event_dict):
        if key in REDACTED_KEYS:
            event_dict[key] = REDACTED
    return event_dict


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    logging.basicConfig(format="%(message)s", level=getattr(logging, level.upper(), logging.INFO))

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_financial_data,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
