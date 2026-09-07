"""Motif history repair and re-extraction orchestration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class MotifRepairContext:
    """Live dependencies required by motif repair helpers."""

    settings: Any
    storage: Any
    project_id: str
    motif_tracker: Any
    motif_cache: dict[int, list[Any]]
    ensure_lock: Callable[[], Any]
    rebuild_motif_stats_from_cache: Callable[[], dict[str, Any]]
    save_to_disk: Callable[[], bool]
    logger: Any


def _missing_reextract_result(reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "reason": reason,
        "chapters_processed": 0,
        "chapters_skipped": 0,
        "motifs_extracted": 0,
        "errors": [],
    }


async def re_extract_motifs_from_archived_chapters(
    ctx: MotifRepairContext,
    *,
    start_chapter: int = 1,
    end_chapter: int | None = None,
    on_progress: Callable[[int, int, str, int | None, str], None] | None = None,
) -> dict[str, Any]:
    """Re-extract motifs from archived chapter markdown files when cache is empty."""
    if ctx.motif_tracker is None or not ctx.storage or not ctx.project_id:
        return _missing_reextract_result("prerequisites_missing")

    project_dir = ctx.storage.project_path(ctx.project_id)
    chapters_dir = project_dir / "chapters"
    if not chapters_dir.is_dir():
        return _missing_reextract_result("chapters_dir_not_found")

    if end_chapter is None:
        max_ch = 0
        for path in chapters_dir.glob("chapter_*.md"):
            try:
                num = int(path.stem.split("_")[1])
                max_ch = max(max_ch, num)
            except (ValueError, IndexError):
                continue
        end_chapter = max_ch if max_ch > 0 else start_chapter

    stats: dict[str, Any] = {
        "ok": True,
        "chapters_processed": 0,
        "chapters_skipped": 0,
        "chapters_missing": 0,
        "chapters_empty": 0,
        "chapters_failed": 0,
        "chapters_attempted": 0,
        "motifs_extracted": 0,
        "errors": [],
    }
    total = end_chapter - start_chapter + 1
    resolved = 0

    chapters_to_extract: list[int] = []
    for chapter in range(start_chapter, end_chapter + 1):
        if ctx.motif_cache.get(chapter):
            stats["chapters_skipped"] += 1
            resolved += 1
            if on_progress:
                on_progress(resolved, total, "skipped", chapter, "scanning")
            continue
        chapter_file = chapters_dir / f"chapter_{chapter:03d}.md"
        if not chapter_file.exists():
            stats["chapters_missing"] += 1
            stats["errors"].append(f"chapter_{chapter:03d}.md not found")
            resolved += 1
            if on_progress:
                on_progress(resolved, total, "missing", chapter, "scanning")
            continue
        chapters_to_extract.append(chapter)
    stats["chapters_attempted"] = len(chapters_to_extract)

    if not chapters_to_extract:
        ctx.logger.info("layer2_no_chapters_to_extract | all cached or missing")
        return stats

    max_concurrency = int(getattr(ctx.settings, "memory_motif_re_extract_concurrency", 3) or 3)
    concurrency = min(max_concurrency, len(chapters_to_extract))
    semaphore = asyncio.Semaphore(concurrency)
    ctx.logger.info(
        "layer2_concurrent_extract | chapters=%d | concurrency=%d",
        len(chapters_to_extract),
        concurrency,
    )

    async def _extract_one(chapter: int) -> tuple[int, list[Any] | None, str | None]:
        async with semaphore:
            chapter_file = chapters_dir / f"chapter_{chapter:03d}.md"
            try:
                text = chapter_file.read_text(encoding="utf-8")
                if not text.strip():
                    return chapter, None, "empty"
                occurrences = await ctx.motif_tracker.extract_from_chapter(
                    chapter_number=chapter,
                    chapter_text=text,
                )
                return chapter, occurrences if occurrences else None, None
            except Exception as exc:
                return chapter, None, str(exc)

    tasks = [asyncio.create_task(_extract_one(chapter)) for chapter in chapters_to_extract]

    for task in asyncio.as_completed(tasks):
        try:
            chapter, occurrences, error = await task
        except Exception as exc:  # noqa: BLE001
            stats["chapters_failed"] += 1
            stats["errors"].append(f"unknown chapter: {exc}")
            resolved += 1
            if on_progress:
                on_progress(resolved, total, "error", None, "extracting")
            continue

        status = "done"
        if error:
            if error == "empty":
                stats["chapters_empty"] += 1
                status = "empty"
            else:
                stats["chapters_failed"] += 1
                stats["errors"].append(f"chapter_{chapter:03d}: {error}")
                status = "error"
        elif occurrences:
            async with ctx.ensure_lock():
                ctx.motif_cache[chapter] = occurrences
            stats["motifs_extracted"] += len(occurrences)
            stats["chapters_processed"] += 1
            ctx.logger.info(
                "layer2_motif_extracted | chapter=%d | count=%d",
                chapter,
                len(occurrences),
            )
        else:
            stats["chapters_empty"] += 1
            status = "empty"
            ctx.logger.warning("layer2_motif_empty | chapter=%d", chapter)

        resolved += 1
        if on_progress:
            on_progress(resolved, total, status, chapter, "extracting")

    if stats["chapters_processed"] > 0:
        ctx.save_to_disk()

    ctx.logger.info(
        "layer2_re_extract_complete | processed=%d | skipped=%d | extracted=%d | errors=%d",
        stats["chapters_processed"],
        stats["chapters_skipped"],
        stats["motifs_extracted"],
        len(stats["errors"]),
    )
    return stats


async def repair_motif_history_from_cache(
    ctx: MotifRepairContext,
    *,
    force_re_extract: bool = False,
    start_chapter: int = 1,
    end_chapter: int | None = None,
    on_progress: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run two-layer motif history repair from cache and optional archive re-extraction."""
    tracker = ctx.motif_tracker
    if tracker is None:
        return {
            "ok": False,
            "reason": "motif_tracker_unavailable",
            "saved": False,
            "cache_chapters": len(ctx.motif_cache),
        }

    result: dict[str, Any] = {
        "ok": True,
        "layer1": {},
        "layer2": {},
    }

    motifs_before = len(getattr(tracker, "_motifs", {}) or {})
    occurrences_before = sum(
        int(getattr(motif, "occurrence_count", 0) or 0)
        for motif in (getattr(tracker, "_motifs", {}) or {}).values()
    )

    if on_progress:
        on_progress("layer1_start", {"motifs_before": motifs_before})

    extraction_cache = getattr(tracker, "_extraction_cache", {})
    extraction_merged = 0
    if isinstance(extraction_cache, dict):
        for raw_chapter, entries in extraction_cache.items():
            try:
                chapter_num = int(raw_chapter)
            except (TypeError, ValueError):
                continue
            if chapter_num <= 0 or not isinstance(entries, list) or not entries:
                continue
            if chapter_num not in ctx.motif_cache or not ctx.motif_cache.get(chapter_num):
                ctx.motif_cache[chapter_num] = list(entries)
                extraction_merged += 1

    async with ctx.ensure_lock():
        rebuild_stats = ctx.rebuild_motif_stats_from_cache()

    saved = bool(rebuild_stats.get("saved", False))
    if not saved and extraction_merged > 0:
        saved = bool(ctx.save_to_disk())

    motifs_after = len(getattr(tracker, "_motifs", {}) or {})
    occurrences_after = sum(
        int(getattr(motif, "occurrence_count", 0) or 0)
        for motif in (getattr(tracker, "_motifs", {}) or {}).values()
    )

    result["layer1"] = {
        "saved": saved,
        "cache_chapters": len(ctx.motif_cache),
        "motifs_before": motifs_before,
        "motifs_after": motifs_after,
        "occurrences_before": occurrences_before,
        "occurrences_after": occurrences_after,
        "motifs_touched": int(rebuild_stats.get("motifs_touched", 0) or 0),
        "occurrences_seen": int(rebuild_stats.get("occurrences_seen", 0) or 0),
        "created_motifs": int(rebuild_stats.get("created_motifs", 0) or 0),
        "extraction_cache_merged": extraction_merged,
        "updated": bool(rebuild_stats.get("updated", False)),
    }

    if on_progress:
        on_progress("layer1_done", result["layer1"])

    if force_re_extract:
        max_concurrency = int(getattr(ctx.settings, "memory_motif_re_extract_concurrency", 3) or 3)
        if on_progress:
            on_progress(
                "layer2_start",
                {
                    "start_chapter": start_chapter,
                    "end_chapter": end_chapter,
                    "concurrency": max_concurrency,
                },
            )

        def _relay_layer2_progress(
            processed: int,
            total: int,
            status: str,
            chapter_number: int | None = None,
            phase: str = "extracting",
        ) -> None:
            if on_progress is None:
                return
            on_progress(
                "layer2_scanning" if phase == "scanning" else "layer2_progress",
                {
                    "processed": processed,
                    "total": total,
                    "status": status,
                    "chapter_number": chapter_number,
                },
            )

        layer2_stats = await re_extract_motifs_from_archived_chapters(
            ctx,
            start_chapter=start_chapter,
            end_chapter=end_chapter,
            on_progress=_relay_layer2_progress,
        )
        result["layer2"] = layer2_stats

        async with ctx.ensure_lock():
            post_rebuild = ctx.rebuild_motif_stats_from_cache()
        if post_rebuild.get("updated"):
            ctx.save_to_disk()

        motifs_final = len(getattr(tracker, "_motifs", {}) or {})
        occurrences_final = sum(
            int(getattr(motif, "occurrence_count", 0) or 0)
            for motif in (getattr(tracker, "_motifs", {}) or {}).values()
        )
        result["motifs_before"] = motifs_before
        result["motifs_after"] = motifs_final
        result["occurrences_before"] = occurrences_before
        result["occurrences_after"] = occurrences_final

        if on_progress:
            on_progress("layer2_done", result["layer2"])
    else:
        result["layer2"] = {"skipped": True}
        result["motifs_before"] = motifs_before
        result["motifs_after"] = motifs_after
        result["occurrences_before"] = occurrences_before
        result["occurrences_after"] = occurrences_after

    return result


__all__ = (
    "MotifRepairContext",
    "re_extract_motifs_from_archived_chapters",
    "repair_motif_history_from_cache",
)
