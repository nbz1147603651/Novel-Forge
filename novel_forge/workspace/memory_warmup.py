"""Best-effort memory warmup helpers for workspace entrypoints."""

from __future__ import annotations

import inspect
from typing import Any

from novel_forge.obs.logger import get_logger

_log = get_logger("workspace.memory_warmup")
_WARM_START_MARKER = "_init_artifacts_warm_start_attempted"


async def warm_start_first_chapter_memory(
    memory_ctx: Any | None,
    *,
    project_id: str,
    chapter_number: int,
) -> dict[str, Any]:
    """Prime chapter-1 memory from init artifacts when the context supports it.

    The warm start is deliberately limited to chapter 1 because init outline
    entries are planning seeds, not already-happened story facts.
    """
    if memory_ctx is None or int(chapter_number) != 1:
        return {"skipped": True, "reason": "not_first_chapter"}

    if getattr(memory_ctx, _WARM_START_MARKER, False):
        return {"skipped": True, "reason": "already_attempted"}

    warm_start = getattr(memory_ctx, "warm_start_from_init_artifacts", None)
    if not callable(warm_start):
        return {"skipped": True, "reason": "unsupported"}

    try:
        result = warm_start()
        if inspect.isawaitable(result):
            result = await result
        stats = result if isinstance(result, dict) else {"result": result}
        setattr(memory_ctx, _WARM_START_MARKER, True)
        if stats.get("outline_episodic_loaded") or stats.get("motif_warmup"):
            _log.info(
                "first_chapter_memory_warm_started | project=%s | stats=%s",
                project_id,
                stats,
            )
        return stats
    except Exception as exc:
        _log.warning(
            "first_chapter_memory_warm_start_failed | project=%s | error=%s",
            project_id,
            exc,
        )
        return {"skipped": True, "reason": "error", "error": str(exc)}
