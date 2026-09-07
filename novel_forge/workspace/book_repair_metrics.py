"""Shared aggregation helpers for whole-book repair payloads."""

from __future__ import annotations

import json
from typing import Any


def coerce_book_repair_chapter_number(detail: dict[str, Any]) -> int:
    """Return the chapter number carried by a repair detail, or 0 when invalid."""
    try:
        chapter_number = int(detail.get("chapter_number", 0) or 0)
    except (TypeError, ValueError):
        return 0
    return chapter_number if chapter_number > 0 else 0


def book_repair_detail_was_applied(detail: dict[str, Any]) -> bool:
    """Return whether a repair detail represents a completed text change."""
    status = str(detail.get("status", "") or "").strip().lower()
    return bool(
        detail.get("applied")
        or detail.get("continuity_applied")
        or detail.get("causal_applied")
        or status == "applied"
    )


def _repair_detail_status(detail: dict[str, Any]) -> str:
    if book_repair_detail_was_applied(detail):
        return "applied"
    status = str(detail.get("status", "") or "").strip().lower()
    return status or "unknown"


def _stable_detail_key(detail: dict[str, Any]) -> str:
    normalized = {
        key: value
        for key, value in detail.items()
        if key not in {"duration_ms", "elapsed_ms", "timestamp"}
    }
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, default=str)


def dedupe_book_repair_details(details: list[Any]) -> list[dict[str, Any]]:
    """Remove exact duplicate repair details while preserving first-seen order."""
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_detail in details:
        if not isinstance(raw_detail, dict):
            continue
        key = _stable_detail_key(raw_detail)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(raw_detail)
    return deduped


def summarize_book_repair_details(
    details: list[Any],
    *,
    targeted_chapters: int = 0,
) -> dict[str, Any]:
    """Return stable chapter-level counts for whole-book repair details.

    The repair runner can create more than one detail for the same chapter when
    continuation or retries are involved. User-facing counts should describe
    unique chapters, while write-counts remain available for diagnostics.
    """
    clean_details = [detail for detail in details if isinstance(detail, dict)]
    processed: set[int] = set()
    applied: set[int] = set()
    failed: set[int] = set()
    blocked: set[int] = set()
    no_change: set[int] = set()
    skipped: set[int] = set()
    manual: set[int] = set()
    applied_write_count = 0

    for detail in clean_details:
        chapter_number = coerce_book_repair_chapter_number(detail)
        if chapter_number <= 0:
            continue
        processed.add(chapter_number)
        status = _repair_detail_status(detail)
        if detail.get("needs_manual_review"):
            manual.add(chapter_number)
        if status == "applied":
            applied.add(chapter_number)
            applied_write_count += 1
        elif status == "failed":
            failed.add(chapter_number)
        elif status == "blocked":
            blocked.add(chapter_number)
        elif status == "no_change":
            no_change.add(chapter_number)
        elif status == "skipped":
            skipped.add(chapter_number)

    metrics: dict[str, Any] = {
        "targeted_chapters": int(targeted_chapters or 0),
        "processed_chapters": len(processed),
        "processed_unique_chapter_count": len(processed),
        "processed_chapter_numbers": sorted(processed),
        "applied_chapters": len(applied),
        "applied_unique_chapter_count": len(applied),
        "applied_write_count": applied_write_count,
        "applied_chapter_numbers": sorted(applied),
        "failed_chapters": len(failed),
        "failed_unique_chapter_count": len(failed),
        "failed_chapter_numbers": sorted(failed),
        "blocked_chapters": len(blocked),
        "blocked_unique_chapter_count": len(blocked),
        "blocked_chapter_numbers": sorted(blocked),
        "no_change_unique_chapter_count": len(no_change),
        "no_change_chapter_numbers": sorted(no_change),
        "skipped_unique_chapter_count": len(skipped),
        "skipped_chapter_numbers": sorted(skipped),
        "needs_manual_review": sorted(manual),
        "detail_count": len(clean_details),
    }
    return metrics
