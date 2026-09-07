"""Optional lightweight timing helpers for desktop UI hot paths."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager

_logger = logging.getLogger(__name__)


def ui_perf_enabled() -> bool:
    value = os.getenv("NOVEL_FORGE_DESKTOP_UI_PERF_LOG", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


@contextmanager
def ui_perf_span(label: str, **fields: object) -> Iterator[None]:
    if not ui_perf_enabled():
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        suffix = " ".join(f"{key}={value}" for key, value in fields.items())
        if suffix:
            _logger.info("ui_perf %s %.1fms %s", label, elapsed_ms, suffix)
        else:
            _logger.info("ui_perf %s %.1fms", label, elapsed_ms)
