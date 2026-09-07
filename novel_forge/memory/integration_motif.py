"""Motif extraction and scheduling helpers for MemoryContext.

Extracted from ``integration.py`` to reduce its size. Handles async motif
extraction and background task scheduling.

All functions take ``ctx`` (the MemoryContext instance) as first argument.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from novel_forge.obs.logger import get_logger

if TYPE_CHECKING:
    from novel_forge.memory.integration import MemoryContext

_log = get_logger("memory.motif_extract")


async def extract_motifs_async(
    ctx: "MemoryContext",
    chapter_number: int,
    text: str,
    chapter_outline: dict[str, Any] | None = None,
) -> None:
    """Extract motifs asynchronously and store results in cache.

    The entire extraction + cache write runs under the MemoryContext lock
    to prevent concurrent state modifications from other async tasks
    (e.g. summary generation, repair operations) on the same context.
    """
    if ctx._motif_tracker is None:
        return

    try:
        async with ctx._ensure_lock():
            motifs = await ctx._motif_tracker.extract_from_chapter(
                chapter_number=chapter_number,
                chapter_text=text,
                chapter_outline=chapter_outline,
            )
            if motifs:
                ctx._motif_cache[chapter_number] = motifs
                _log.info(
                    "motif_extraction_cached | chapter=%d | count=%d",
                    chapter_number,
                    len(motifs),
                )
            else:
                _log.warning(
                    "motif_extraction_empty | chapter=%d | not_caching_to_allow_retry",
                    chapter_number,
                )
            # Style-rule tracking is deterministic and chapter-local; register
            # the chapter's fired technique rules alongside motif extraction so
            # cross-chapter template detection has current data.
            if ctx._style_rule_tracker is not None:
                fired = ctx._style_rule_tracker.register_chapter(chapter_number, text)
                if fired:
                    _log.debug(
                        "style_rule_registered | chapter=%d | fired=%d",
                        chapter_number,
                        len(fired),
                    )

        _log.debug("Extracted %d motifs for chapter %d", len(motifs), chapter_number)
        ctx._emit_progress(
            "motifs_completed",
            {
                "chapter": chapter_number,
                "count": len(motifs),
                "success": bool(motifs),
            },
        )
        if motifs:
            ctx.save_to_disk()
    except Exception as exc:
        _log.error(
            "Async motif extraction failed for chapter %d: %s",
            chapter_number,
            exc,
            exc_info=True,
        )
        ctx._emit_progress(
            "motifs_completed",
            {
                "chapter": chapter_number,
                "count": 0,
                "success": False,
                "error": str(exc),
            },
        )


def schedule_motif_extraction(
    ctx: "MemoryContext",
    chapter_number: int,
    text: str,
    chapter_outline: dict[str, Any] | None = None,
) -> bool:
    """Schedule motif extraction as a background task.

    Returns True if the task was successfully scheduled.
    """
    scheduled = ctx._schedule_background_task(
        ctx._extract_motifs_async(
            chapter_number=chapter_number,
            text=text,
            chapter_outline=chapter_outline,
        ),
        tag="motif_extract",
    )
    if scheduled:
        _log.debug("Scheduled async motif extraction for chapter %d", chapter_number)
        ctx._emit_progress(
            "motifs_extracted",
            {
                "chapter": chapter_number,
                "count": 0,
                "success": True,
            },
        )
    else:
        _log.error("Failed to schedule motif extraction for chapter %d", chapter_number)
        ctx._emit_progress(
            "motifs_extracted",
            {
                "chapter": chapter_number,
                "count": 0,
                "success": False,
                "error": "failed_to_schedule",
            },
        )
    return scheduled
