"""Correlation context and safe formatters for application logs.

The application serves CLI, API, and desktop workflows concurrently. Plain
``logging`` records do not carry enough information to tell which run created
them, so this module keeps that identity in :mod:`contextvars`. Context vars
also flow into ``asyncio`` tasks, without leaking between concurrent runs.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from copy import copy
from datetime import datetime, timezone
from typing import Any

LOG_CONTEXT_FIELDS: tuple[str, ...] = (
    "run_id",
    "project_id",
    "command",
    "request_id",
    "chapter",
    "step",
    "task",
    "call_id",
)

_log_context: ContextVar[Mapping[str, Any] | None] = ContextVar(
    "novel_forge_log_context",
    default=None,
)

_SECRET_KEY_RE = re.compile(
    r"(?:api[_-]?key|authorization|credential|password|secret|token)",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"(?i)(bearer\s+)[^\s,;]+")
_ASSIGNMENT_SECRET_RE = re.compile(
    r"(?i)((?:api[_-]?key|authorization|credential|password|secret|token)\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_MAX_LOG_VALUE_CHARS = 4_000


def current_log_context() -> dict[str, Any]:
    """Return a copy of the correlation fields active in this task/thread."""

    return dict(_log_context.get() or {})


@contextmanager
def bind_log_context(**values: Any) -> Iterator[None]:
    """Temporarily merge correlation fields into the current execution context.

    ``None`` values are ignored so callers can bind optional context without
    erasing a more specific parent value.
    """

    merged = current_log_context()
    merged.update({key: value for key, value in values.items() if value is not None})
    token = _log_context.set(merged)
    try:
        yield
    finally:
        _log_context.reset(token)


def redact_log_value(value: Any) -> Any:
    """Bound and redact values before they are persisted to a diagnostic log."""

    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        shortened = value
        if len(shortened) > _MAX_LOG_VALUE_CHARS:
            shortened = shortened[: _MAX_LOG_VALUE_CHARS - 1] + "…"
        shortened = _BEARER_RE.sub(r"\1[REDACTED]", shortened)
        return _ASSIGNMENT_SECRET_RE.sub(r"\1[REDACTED]", shortened)
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]" if _SECRET_KEY_RE.search(str(key)) else redact_log_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [redact_log_value(item) for item in list(value)[:40]]
    return redact_log_value(str(value))


class ContextEnrichmentFilter(logging.Filter):
    """Attach stable correlation fields to every record that reaches a handler."""

    def filter(self, record: logging.LogRecord) -> bool:
        context = current_log_context()
        for field in LOG_CONTEXT_FIELDS:
            value = context.get(field)
            if value is not None:
                # The active context is authoritative: a lower layer must not
                # accidentally attach its record to a different concurrent run.
                setattr(record, field, value)
            elif not hasattr(record, field):
                setattr(record, field, "")
        return True


class RunContextFilter(ContextEnrichmentFilter):
    """Keep a run-scoped handler from accepting records from another run."""

    def __init__(self, run_id: str) -> None:
        super().__init__()
        self._run_id = run_id

    def filter(self, record: logging.LogRecord) -> bool:
        super().filter(record)
        return getattr(record, "run_id", "") == self._run_id


class ContextTextFormatter(logging.Formatter):
    """Human-readable formatter with compact run correlation and redaction."""

    def format(self, record: logging.LogRecord) -> str:
        # A record is offered to each handler in turn. Format a copy so a
        # redacted run log never mutates the message seen by other handlers.
        safe_record = copy(record)
        try:
            safe_record.msg = redact_log_value(record.getMessage())
            safe_record.args = ()
        except Exception:  # pragma: no cover - defensive logging fallback
            safe_record.msg = redact_log_value(str(record.msg))
            safe_record.args = ()
        if record.exc_info:
            safe_record.exc_text = redact_log_value(self.formatException(record.exc_info))
        elif getattr(record, "exc_text", None):
            safe_record.exc_text = redact_log_value(record.exc_text)
        if record.stack_info:
            safe_record.stack_info = redact_log_value(record.stack_info)

        rendered = super().format(safe_record)
        fields = [
            f"{name}={getattr(safe_record, name)}"
            for name in ("run_id", "project_id", "chapter", "step", "task", "call_id")
            if getattr(safe_record, name, "") not in (None, "")
        ]
        if not fields:
            return rendered
        return f"{rendered} | {' '.join(fields)}"


class JsonLogFormatter(logging.Formatter):
    """One JSON object per application log record for machine-assisted debugging."""

    def format(self, record: logging.LogRecord) -> str:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - logging itself tolerates malformed args
            message = str(record.msg)

        payload: dict[str, Any] = {
            "schema_version": 1,
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_log_value(message),
            "process": record.process,
            "thread": record.threadName,
        }
        for field in LOG_CONTEXT_FIELDS:
            value = getattr(record, field, None)
            if value not in (None, ""):
                payload[field] = redact_log_value(value)
        if record.exc_info:
            payload["exception"] = redact_log_value(self.formatException(record.exc_info))
        if record.stack_info:
            payload["stack"] = redact_log_value(self.formatStack(record.stack_info))
        return json.dumps(payload, ensure_ascii=False, default=str)
