#!/usr/bin/env python3
"""Benchmark: JobCard paintEvent performance with throttling and pixmap caching.

Measures 50 cards × 1000 progress ticks, reporting total/avg/p99 paint time.
"""

from __future__ import annotations

import os
import statistics
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from novel_forge.desktop.pages.workflow_jobs import JobCard  # noqa: E402
from PySide6.QtGui import QPaintEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from novel_forge.desktop.jobs import (  # noqa: E402
    DesktopJobEvent,
    DesktopJobRecord,
    DesktopJobState,
)


def _make_job(job_id: str, step: int) -> DesktopJobRecord:
    record = DesktopJobRecord(job_id=job_id, kind="run_short", label=f"Task {job_id}")
    record.status = DesktopJobState.RUNNING
    record.current_step = f"step_{step}"
    record.updated_at = f"2026-03-30T12:00:{min(step, 59):02d}+00:00"
    record.events = [
        DesktopJobEvent(at=record.updated_at, step=f"evt_{i}", payload={})
        for i in range(step)
    ]
    return record


def main() -> None:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    num_cards = 50
    num_ticks = 1000

    cards: list[JobCard] = []
    for i in range(num_cards):
        card = JobCard(_make_job(f"job-{i:03d}", 0))
        card.resize(400, 120)
        card.show()
        cards.append(card)
    app.processEvents()

    paint_durations: list[float] = []
    total_paint_calls = 0

    for card in cards:
        card_paint_count = 0
        original_paint = card.paintEvent
        event = QPaintEvent(card.rect())

        def make_counting_paint(orig, evt):
            def counting_paint(ev: QPaintEvent) -> None:
                nonlocal card_paint_count
                card_paint_count += 1
                start = time.perf_counter()
                orig(ev)
                elapsed_ms = (time.perf_counter() - start) * 1000
                paint_durations.append(elapsed_ms)
            return counting_paint

        card.paintEvent = make_counting_paint(original_paint, event)  # type: ignore[assignment]

        for _step in range(num_ticks):
            card._card_color = card._card_color
            card._schedule_repaint()

        card._flush_repaint()
        app.processEvents()
        total_paint_calls += card_paint_count

    total_ms = sum(paint_durations)
    avg_ms = statistics.mean(paint_durations) if paint_durations else 0
    p50_ms = statistics.median(paint_durations) if paint_durations else 0
    p99_idx = int(len(paint_durations) * 0.99)
    sorted_durations = sorted(paint_durations)
    p99_ms = sorted_durations[p99_idx] if p99_idx < len(sorted_durations) else 0
    max_ms = max(paint_durations) if paint_durations else 0

    print("=" * 60)
    print("JobCard paintEvent Benchmark Results")
    print("=" * 60)
    print(f"Cards:              {num_cards}")
    print(f"Ticks per card:     {num_ticks}")
    print(f"Total ticks:        {num_cards * num_ticks}")
    print(f"Total paint calls:  {total_paint_calls}")
    print(f"Paint reduction:    {100 * (1 - total_paint_calls / (num_cards * num_ticks)):.1f}%")
    print()
    print(f"Total paint time:   {total_ms:.2f} ms")
    print(f"Average paint time: {avg_ms:.4f} ms")
    print(f"P50 paint time:     {p50_ms:.4f} ms")
    print(f"P99 paint time:     {p99_ms:.4f} ms")
    print(f"Max paint time:     {max_ms:.4f} ms")
    print()

    target_avg = 5.0
    target_paint_count = num_cards * 100
    status_avg = "PASS" if avg_ms < target_avg else "FAIL"
    status_count = "PASS" if total_paint_calls < target_paint_count else "FAIL"
    print(f"Avg < {target_avg}ms:           {status_avg} ({avg_ms:.4f} ms)")
    print(f"Paints < {target_paint_count}:   {status_count} ({total_paint_calls})")
    print("=" * 60)

    pixmap = cards[0].grab()
    pixmap.save("/tmp/jobcard_benchmark.png")
    print("Visual snapshot saved: /tmp/jobcard_benchmark.png")


if __name__ == "__main__":
    main()
