"""Tests for whole-book repair metric aggregation."""

from __future__ import annotations

from novel_forge.workspace.book_repair_metrics import (
    dedupe_book_repair_details,
    summarize_book_repair_details,
)


def test_book_repair_metrics_count_unique_chapters_and_write_records() -> None:
    details = [
        {"chapter_number": 5, "status": "applied", "applied": True},
        {"chapter_number": 5, "status": "applied", "continuity_applied": True},
        {"chapter_number": 6, "status": "no_change"},
        {"chapter_number": 6, "status": "no_change"},
        {"chapter_number": 7, "status": "blocked", "needs_manual_review": True},
        {"chapter_number": 8, "status": "skipped"},
    ]

    metrics = summarize_book_repair_details(details, targeted_chapters=4)

    assert metrics["targeted_chapters"] == 4
    assert metrics["processed_chapters"] == 4
    assert metrics["applied_chapters"] == 1
    assert metrics["applied_write_count"] == 2
    assert metrics["no_change_unique_chapter_count"] == 1
    assert metrics["blocked_chapters"] == 1
    assert metrics["skipped_unique_chapter_count"] == 1
    assert metrics["applied_chapter_numbers"] == [5]
    assert metrics["needs_manual_review"] == [7]


def test_book_repair_detail_dedupe_preserves_distinct_statuses() -> None:
    details = [
        {"chapter_number": 5, "status": "applied", "applied": True},
        {"chapter_number": 5, "status": "applied", "applied": True},
        {"chapter_number": 5, "status": "no_change"},
    ]

    deduped = dedupe_book_repair_details(details)

    assert deduped == [
        {"chapter_number": 5, "status": "applied", "applied": True},
        {"chapter_number": 5, "status": "no_change"},
    ]
