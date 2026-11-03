"""Structured logging configuration.

All logs are JSON (in deployed environments) produced by `structlog`. Request-scoped fields
(`request_id`, `tenant_id`, and later `enrollment_id` / `ai_run_id`) are bound through
`structlog.contextvars` so every log line carries them without passing loggers around.

Two processors protect privacy: `redact_sensitive_fields` drops values of keys that may hold
secrets or email bodies, and `mask_email_addresses` masks any email address that appears in a
logged string value.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

REDACTED = "[REDACTED]"

# Keys whose values are never logged (case-insensitive). Suffix matching catches names such as
# `access_token` or `reply_body` while leaving counters like `tokens_in` alone.
_SENSITIVE_KEYS = frozenset(
    {"password", "secret", "token", "authorization", "api_key", "cookie", "body", "signature"}
)
_SENSITIVE_KEY_SUFFIXES = ("_password", "_secret", "_token", "_api_key", "_body", "_signature")

_EMAIL_PATTERN = re.compile(
    r"(?P<first>[A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@(?P<domain>[A-Za-z0-9.-]+\.[A-Za-z]{2,})"
)


def is_sensitive_log_key(key: str) -> bool:
    """Return whether values logged under `key` must be redacted.

    Args:
        key: A structured log field name.

    Returns:
        True when the field may carry a secret or an email body.
    """
    lowered = key.lower()
    return lowered in _SENSITIVE_KEYS or lowered.endswith(_SENSITIVE_KEY_SUFFIXES)


def mask_email_address(text: str) -> str:
    """Mask every email address in `text`, keeping the first character and the domain.

    Args:
        text: Arbitrary text.

    Returns:
        The text with an address such as `maya@forwarder.example` rewritten to
        `m***@forwarder.example`.
    """
    return _EMAIL_PATTERN.sub(lambda match: f"{match['first']}***@{match['domain']}", text)


def redact_sensitive_fields(
    _logger: Any, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Structlog processor replacing values of sensitive keys with a placeholder.

    Args:
        _logger: Unused; required by the processor signature.
        _method_name: Unused; required by the processor signature.
        event_dict: The event being logged.

    Returns:
        The event with sensitive values redacted.
    """
    for key in list(event_dict):
        if is_sensitive_log_key(key):
            event_dict[key] = REDACTED
    return event_dict


def mask_email_addresses(
    _logger: Any, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Structlog processor masking email addresses in string values.

    Args:
        _logger: Unused; required by the processor signature.
        _method_name: Unused; required by the processor signature.
        event_dict: The event being logged.

    Returns:
        The event with email addresses masked.
    """
    for key, value in event_dict.items():
        if isinstance(value, str):
            event_dict[key] = mask_email_address(value)
    return event_dict


def configure_logging(*, level: str = "INFO", json_output: bool = True) -> None:
    """Configure structlog and route standard-library logging through it.

    Safe to call more than once (tests and the app factory both call it).

    Args:
        level: Minimum log level name, for example `"INFO"`.
        json_output: Emit JSON when true, human-friendly console output otherwise.
    """
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_sensitive_fields,
        mask_email_addresses,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer: Any = (
        structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # Uvicorn's access log duplicates our request log and would print raw query strings.
    logging.getLogger("uvicorn.access").disabled = True
