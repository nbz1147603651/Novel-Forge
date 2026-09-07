#!/usr/bin/env python3
"""Benchmark: Surface shadow offscreen buffer paint performance.

Creates 100 Surface instances (mixed tones: hero/elevated/inset/panel/card/flat/rail),
simulates 5 seconds of scrolling, and reports avg/p99 paint time per surface.

Target: scroll paint time < 2ms per surface.
"""

from __future__ import annotations

import os
import statistics
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QPaintEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea, QVBoxLayout, QWidget  # noqa: E402

from novel_forge.desktop.components.primitives import Surface  # noqa: E402

TONES = ["hero", "elevated", "inset", "panel", "card", "flat", "rail"]
NUM_SURFACES = 100
SCROLL_FRAMES = 300  # ~5 seconds at 60fps
SURFACE_WIDTH = 400
SURFACE_HEIGHT = 80


def main() -> None:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setSpacing(8)

    surfaces: list[Surface] = []
    for i in range(NUM_SURFACES):
        tone = TONES[i % len(TONES)]
        s = Surface(tone=tone)
        s.setFixedWidth(SURFACE_WIDTH)
        s.setFixedHeight(SURFACE_HEIGHT)
        layout.addWidget(s)
        surfaces.append(s)

    scroll = QScrollArea()
    scroll.setWidget(container)
    scroll.setWidgetResizable(True)
    scroll.resize(500, 600)
    scroll.show()
    app.processEvents()

    paint_durations: list[float] = []

    for surface in surfaces:
        original_paint = surface.paintEvent
        rect = surface.rect()

        def make_tracking_paint(orig, r):
            def tracking_paint(ev: QPaintEvent) -> None:
                start = time.perf_counter()
                orig(ev)
                elapsed_ms = (time.perf_counter() - start) * 1000
                paint_durations.append(elapsed_ms)
            return tracking_paint

        surface.paintEvent = make_tracking_paint(original_paint, rect)  # type: ignore[assignment]

    for frame in range(SCROLL_FRAMES):
        y = int((frame / SCROLL_FRAMES) * container.sizeHint().height())
        scroll.verticalScrollBar().setValue(y)
        app.processEvents()

    total_ms = sum(paint_durations)
    avg_ms = statistics.mean(paint_durations) if paint_durations else 0
    p50_ms = statistics.median(paint_durations) if paint_durations else 0
    sorted_durations = sorted(paint_durations)
    p99_idx = min(int(len(sorted_durations) * 0.99), len(sorted_durations) - 1)
    p99_ms = sorted_durations[p99_idx] if sorted_durations else 0
    max_ms = max(paint_durations) if paint_durations else 0

    shadow_tones_count = sum(
        1 for s in surfaces if s.graphicsEffect() is not None
    )

    print("=" * 60)
    print("Surface Shadow Offscreen Buffer Benchmark")
    print("=" * 60)
    print(f"Surfaces:           {NUM_SURFACES}")
    print(f"Shadow surfaces:    {shadow_tones_count} (hero/elevated/inset)")
    print(f"Non-shadow surfaces:{NUM_SURFACES - shadow_tones_count} (panel/card/flat/rail)")
    print(f"Scroll frames:      {SCROLL_FRAMES}")
    print(f"Total paint calls:  {len(paint_durations)}")
    print()
    print(f"Total paint time:   {total_ms:.2f} ms")
    print(f"Average paint time: {avg_ms:.4f} ms")
    print(f"P50 paint time:     {p50_ms:.4f} ms")
    print(f"P99 paint time:     {p99_ms:.4f} ms")
    print(f"Max paint time:     {max_ms:.4f} ms")
    print()

    target_per_surface = 2.0
    status_avg = "PASS" if avg_ms < target_per_surface else "FAIL"
    status_p99 = "PASS" if p99_ms < target_per_surface * 2 else "FAIL"
    print(f"Avg < {target_per_surface}ms/surface:  {status_avg} ({avg_ms:.4f} ms)")
    print(f"P99 < {target_per_surface * 2}ms:       {status_p99} ({p99_ms:.4f} ms)")
    print("=" * 60)

    pixmap = surfaces[0].grab()
    pixmap.save("/tmp/surface_benchmark.png")
    print("Visual snapshot saved: /tmp/surface_benchmark.png")


if __name__ == "__main__":
    main()
