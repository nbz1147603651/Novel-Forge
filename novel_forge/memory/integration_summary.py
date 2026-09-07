"""Summary generation helper for MemoryContext.

Extracted from ``integration.py`` to reduce its size. Handles async chapter
summary generation under the MemoryContext lock.

All functions take ``ctx`` (the MemoryContext instance) as first argument.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from novel_forge.obs.logger import get_logger

if TYPE_CHECKING:
    from novel_forge.memory.integration import MemoryContext

_log = get_logger("memory.summary_gen")

# Maximum number of chapter summaries retained in memory. Older summaries are
# evicted (they remain on disk in project_memory.json and can be reloaded by
# the summary service). This prevents unbounded memory growth on very long
# novels (200+ chapters).
_SUMMARY_CACHE_MAX_ENTRIES = 80


def _prune_summary_cache(ctx: "MemoryContext") -> int:
    """Evict oldest chapter summaries when cache exceeds the retention limit.

    Returns the number of entries evicted. Only evicts entries whose chapter
    number is below the current high-water mark minus the retention window,
    ensuring the most recent chapters always remain cached.
    """
    cache = ctx._summary_cache
    if len(cache) <= _SUMMARY_CACHE_MAX_ENTRIES:
        return 0

    # Determine the retention window: keep the most recent N chapters.
    max_chapter = max(cache.keys()) if cache else 0
    retention_floor = max_chapter - _SUMMARY_CACHE_MAX_ENTRIES

    evicted = 0
    chapters_to_remove = sorted(
        ch for ch in cache if ch <= retention_floor
    )
    # Only evict down to the limit
    excess = len(cache) - _SUMMARY_CACHE_MAX_ENTRIES
    for ch in chapters_to_remove[:excess]:
        del cache[ch]
        ctx._chapter_content_hash.pop(ch, None)
        evicted += 1

    if evicted:
        ctx._summary_stats["evicted"] = ctx._summary_stats.get("evicted", 0) + evicted
        _log.info(
            "summary_cache_pruned | evicted=%d | remaining=%d | max_chapter=%d",
            evicted,
            len(cache),
            max_chapter,
        )
    return evicted


async def generate_summary_async(
    ctx: "MemoryContext",
    *,
    chapter: int,
    text: str,
    report: str,
    source_hash: str,
) -> None:
    """Generate summary asynchronously under the MemoryContext lock."""
    if ctx._summary_service is None:
        return

    async with ctx._ensure_lock():
        existing_raw = ctx._summary_cache.get(chapter)
        existing_entry = (
            existing_raw
            if isinstance(existing_raw, dict)
            else ctx._normalize_summary_cache_entry(existing_raw)
        )
        if (
            existing_raw is not None
            and existing_entry is not None
            and not isinstance(existing_raw, dict)
        ):
            ctx._summary_cache[chapter] = existing_entry
            ctx._summary_stats["legacy_migrated"] = (
                ctx._summary_stats.get("legacy_migrated", 0) + 1
            )
        if (
            existing_entry
            and str(existing_entry.get("source_hash", "") or "") == source_hash
            and str(existing_entry.get("text", "") or "").strip()
        ):
            ctx._summary_stats["hash_skips"] = ctx._summary_stats.get("hash_skips", 0) + 1
            ctx._chapter_content_hash[chapter] = source_hash
            return

        try:
            _log.debug("Generating summary for chapter %d", chapter)
            summary = await ctx._summary_service.generate_chapter_summary(
                chapter_number=chapter,
                chapter_text=text,
                creative_report=None,
            )

            quality_score = summary.metadata.get("quality_score", 1.0)
            quality_warnings = summary.metadata.get("quality_warnings", [])
            if quality_warnings:
                _log.warning(
                    "Summary quality warnings for chapter %d: score=%.2f warnings=%s",
                    chapter,
                    quality_score,
                    quality_warnings,
                )

            previous_version = (
                int(existing_entry.get("version", 0) or 0) if existing_entry else 0
            )
            ctx._summary_cache[chapter] = {
                "text": summary.text,
                "source_hash": source_hash,
                "version": max(1, previous_version + 1),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "quality_score": quality_score,
            }
            ctx._chapter_content_hash[chapter] = source_hash
            ctx._summary_stats["generated"] = ctx._summary_stats.get("generated", 0) + 1
            if existing_entry is not None:
                ctx._summary_stats["regenerated"] = (
                    ctx._summary_stats.get("regenerated", 0) + 1
                )

            # Prune old summaries to prevent unbounded memory growth.
            _prune_summary_cache(ctx)

            _log.info(
                "Summary generated for chapter %d | length=%d", chapter, len(summary.text)
            )

            ctx._emit_progress(
                "summary_generated",
                {
                    "chapter": chapter,
                    "length": len(summary.text),
                    "version": ctx._summary_cache[chapter]["version"],
                    "success": True,
                },
            )
        except Exception as exc:
            _log.error(
                "Failed to generate summary for chapter %d: %s", chapter, exc, exc_info=True
            )
            ctx._emit_progress(
                "summary_generated",
                {
                    "chapter": chapter,
                    "length": 0,
                    "success": False,
                    "error": str(exc),
                },
            )
