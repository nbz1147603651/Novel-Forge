"""Structured logger for Novel Forge."""

from __future__ import annotations

import logging
import sys
from typing import Any

from novel_forge.obs.context import ContextEnrichmentFilter, ContextTextFormatter


def get_logger(name: str, level: str = "INFO") -> logging.Logger:
    """Create a structured logger with consistent formatting.

    The logger is named ``novel_forge.<name>`` and propagates to the root
    ``novel_forge`` logger, which allows ``ProjectRunLogger`` to attach a
    run-scoped FileHandler and capture WARNING+ messages to ``python.log``.
    """
    logger_name = name if name == "novel_forge" or name.startswith("novel_forge.") else f"novel_forge.{name}"
    logger = logging.getLogger(logger_name)
    if not any(getattr(handler, "_novel_forge_console", False) for handler in logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        formatter = ContextTextFormatter(
            fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        handler.addFilter(ContextEnrichmentFilter())
        handler._novel_forge_console = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    # Ensure propagation is on so the run-scoped FileHandler on the root
    # novel_forge logger can capture this logger's records.
    logger.propagate = True
    return logger


def log_step(
    logger: logging.Logger,
    step_name: str,
    *,
    status: str = "start",
    **extra: Any,
) -> None:
    """Emit a structured step log entry."""
    parts = [f"step={step_name}", f"status={status}"]
    for k, v in extra.items():
        parts.append(f"{k}={v}")
    logger.info(" | ".join(parts))
