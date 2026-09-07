"""Memory reindex and invalidation orchestration helpers."""

from __future__ import annotations

from typing import Any, Callable


async def rebuild_vector_collection(
    *,
    episodic_memory: Any,
    save_to_disk: Callable[[], bool],
    get_status_summary: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Rebuild an episodic vector collection and persist the refreshed state."""
    if episodic_memory is None:
        raise RuntimeError("Episodic memory is not enabled for this project")
    rebuild = getattr(episodic_memory, "rebuild_vector_collection", None)
    if not callable(rebuild):
        raise RuntimeError("Episodic memory backend does not support vector rebuild")

    raw_result = await rebuild()
    result = dict(raw_result) if isinstance(raw_result, dict) else {"result": raw_result}
    result["saved"] = save_to_disk()
    result["status"] = get_status_summary()
    return result


def invalidate_chapter_memory(
    *,
    from_chapter: int,
    episodic_memory: Any,
    motif_tracker: Any,
    summary_cache: dict[int, Any],
    chapter_content_hash: dict[int, str],
    motif_cache: dict[int, Any],
    expression_memory: Any,
    get_last_indexed_chapter: Callable[[], int],
    set_last_indexed_chapter: Callable[[int], None],
    logger: Any,
) -> dict[str, Any]:
    """Invalidate memory data for all chapters from ``from_chapter`` onward."""
    result: dict[str, Any] = {
        "from_chapter": from_chapter,
        "episodic_removed": 0,
        "motifs_removed": 0,
        "summaries_cleared": 0,
        "motif_cache_cleared": 0,
        "expression_removed": 0,
    }

    if episodic_memory:
        try:
            deleted = episodic_memory.delete_chapters_from(from_chapter)
            result["episodic_removed"] = len(deleted)
            logger.info(
                "Invalidated episodic memory from chapter %d | removed=%d",
                from_chapter,
                len(deleted),
            )
        except Exception as exc:
            logger.error(
                "Failed to invalidate episodic memory from chapter %d: %s",
                from_chapter,
                exc,
                exc_info=True,
            )

    if motif_tracker:
        try:
            removed = motif_tracker.delete_chapters_from(from_chapter)
            result["motifs_removed"] = removed
            logger.info(
                "Invalidated motif data from chapter %d | removed=%d",
                from_chapter,
                removed,
            )
        except Exception as exc:
            logger.error(
                "Failed to invalidate motif data from chapter %d: %s",
                from_chapter,
                exc,
                exc_info=True,
            )

    summary_keys_to_clear = [ch for ch in summary_cache if ch >= from_chapter]
    for chapter in summary_keys_to_clear:
        del summary_cache[chapter]
    result["summaries_cleared"] = len(summary_keys_to_clear)

    hash_keys_to_clear = [ch for ch in chapter_content_hash if ch >= from_chapter]
    for chapter in hash_keys_to_clear:
        del chapter_content_hash[chapter]

    motif_cache_keys_to_clear = [ch for ch in motif_cache if ch >= from_chapter]
    for chapter in motif_cache_keys_to_clear:
        del motif_cache[chapter]
    result["motif_cache_cleared"] = len(motif_cache_keys_to_clear)

    last_indexed_chapter = get_last_indexed_chapter()
    if expression_memory:
        removed = 0
        for chapter in range(from_chapter, max(last_indexed_chapter, from_chapter - 1) + 1):
            try:
                removed += int(expression_memory.delete_chapter(chapter) or 0)
            except Exception as exc:
                logger.debug(
                    "Failed to invalidate expression memory | chapter=%d | error=%s",
                    chapter,
                    exc,
                )
        result["expression_removed"] = removed
        try:
            expression_memory.save()
        except Exception:
            pass

    if last_indexed_chapter >= from_chapter:
        new_last_indexed = max(0, from_chapter - 1)
        set_last_indexed_chapter(new_last_indexed)
        logger.info(
            "Updated last_indexed_chapter to %d after invalidation",
            new_last_indexed,
        )

    logger.info(
        "Memory invalidation complete | from_chapter=%d | result=%s",
        from_chapter,
        result,
    )
    return result


__all__ = (
    "invalidate_chapter_memory",
    "rebuild_vector_collection",
)
