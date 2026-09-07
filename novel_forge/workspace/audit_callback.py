"""Audit callback helper for repair functions.

Centralizes the callback invocation + exception handling so individual
repair functions don't need to repeat the try/except boilerplate.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


def _emit_audit_update(
    callback: Callable[[str, int, dict[str, Any]], None] | None,
    project_id: str,
    chapter_num: int,
    result: dict[str, Any],
) -> None:
    """Invoke an audit update callback with exception handling.

    - If callback is None, this is a no-op (current behavior preserved).
    - If callback raises, log full traceback and swallow (don't crash repair).
    """
    if callback is None:
        return
    try:
        callback(project_id, chapter_num, result)
    except Exception:
        logger.exception(
            "audit callback failed: project=%s chapter=%s",
            project_id,
            chapter_num,
        )
