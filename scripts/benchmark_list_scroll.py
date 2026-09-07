#!/usr/bin/env python3
"""Benchmark: QListWidget scroll performance with uniformItemSizes and batched layout.

Creates a 1000-item QListWidget and simulates scrolling, reporting avg/p99 paint time
per scroll event.  Also benchmarks creation time and a 10k-item extreme case.
"""

from __future__ import annotations

import os
import statistics
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QListWidget,
    QListWidgetItem,
    QScrollBar,
)


def _populate_list(widget: QListWidget, count: int) -> None:
    """Fill *widget* with *count* uniform text items."""
    widget.clear()
    for i in range(count):
        item = QListWidgetItem(f"Item {i:05d} — 第 {i + 1} 章 · 测试章节标题")
        widget.addItem(item)


def _measure_scroll_paints(
    widget: QListWidget, app: QApplication, steps: int = 50
) -> list[float]:
    """Scroll through the list and measure per-step paint time in ms."""
    scrollbar: QScrollBar = widget.verticalScrollBar()
    max_val = scrollbar.maximum()
    if max_val <= 0:
        return [0.0]

    durations: list[float] = []
    step_size = max(max_val // steps, 1)

    for pos in range(0, max_val + 1, step_size):
        scrollbar.setValue(pos)
        start = time.perf_counter()
        app.processEvents()
        elapsed_ms = (time.perf_counter() - start) * 1000
        durations.append(elapsed_ms)

    return durations


def _report(label: str, durations: list[float], threshold_ms: float) -> bool:
    """Print statistics and return True if avg < threshold."""
    if not durations:
        print(f"  {label}: no data")
        return True
    avg = statistics.mean(durations)
    p50 = statistics.median(durations)
    sorted_d = sorted(durations)
    p99_idx = min(int(len(sorted_d) * 0.99), len(sorted_d) - 1)
    p99 = sorted_d[p99_idx]
    mx = max(durations)
    status = "PASS" if avg < threshold_ms else "FAIL"
    print(f"  {label}:")
    print(f"    avg={avg:.3f}ms  p50={p50:.3f}ms  p99={p99:.3f}ms  max={mx:.3f}ms  [{status}]")
    return avg < threshold_ms


def main() -> None:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    all_pass = True
    print("=" * 64)
    print("QListWidget Scroll Performance Benchmark")
    print("=" * 64)

    # --- 1. Creation time (1000 items) ---
    print("\n[1] Creation time — 1000 items")
    w1 = QListWidget()
    w1.setUniformItemSizes(True)
    w1.setLayoutMode(QListWidget.LayoutMode.Batched)
    w1.setBatchSize(50)
    w1.resize(400, 600)
    w1.show()

    start = time.perf_counter()
    _populate_list(w1, 1000)
    creation_ms = (time.perf_counter() - start) * 1000
    status = "PASS" if creation_ms < 1000 else "FAIL"
    print(f"  Creation: {creation_ms:.1f}ms  [{status}]")
    all_pass = all_pass and creation_ms < 1000
    app.processEvents()

    # --- 2. Scroll paint time (1000 items) ---
    print("\n[2] Scroll paint time — 1000 items")
    durations_1k = _measure_scroll_paints(w1, app, steps=50)
    all_pass = all_pass and _report("1000-item scroll", durations_1k, threshold_ms=5.0)

    # --- 3. Extreme case — 10k items creation ---
    print("\n[3] Creation time — 10 000 items (extreme)")
    w2 = QListWidget()
    w2.setUniformItemSizes(True)
    w2.setLayoutMode(QListWidget.LayoutMode.Batched)
    w2.setBatchSize(100)
    w2.resize(400, 600)
    w2.show()

    start = time.perf_counter()
    _populate_list(w2, 10_000)
    creation_10k_ms = (time.perf_counter() - start) * 1000
    status_10k = "PASS" if creation_10k_ms < 5000 else "FAIL"
    print(f"  Creation: {creation_10k_ms:.1f}ms  [{status_10k}]")
    all_pass = all_pass and creation_10k_ms < 5000
    app.processEvents()

    # --- 4. Scroll paint time (10k items) ---
    print("\n[4] Scroll paint time — 10 000 items (extreme)")
    durations_10k = _measure_scroll_paints(w2, app, steps=100)
    all_pass = all_pass and _report("10k-item scroll", durations_10k, threshold_ms=10.0)

    # --- 5. Without optimization (baseline comparison) ---
    print("\n[5] Baseline — 1000 items WITHOUT uniformItemSizes")
    w3 = QListWidget()
    w3.resize(400, 600)
    w3.show()

    start = time.perf_counter()
    _populate_list(w3, 1000)
    baseline_creation_ms = (time.perf_counter() - start) * 1000
    app.processEvents()
    durations_baseline = _measure_scroll_paints(w3, app, steps=50)
    print(f"  baseline creation: {baseline_creation_ms:.1f} ms")
    _report("1000-item baseline (no opt)", durations_baseline, threshold_ms=999)

    # --- Summary ---
    print()
    print("=" * 64)
    if all_pass:
        print("OVERALL: PASS")
    else:
        print("OVERALL: FAIL")
    print("=" * 64)


if __name__ == "__main__":
    main()
