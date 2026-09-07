"""Benchmark QTextBrowser rendering at different HTML sizes.

Used to decide whether chunked rendering is worth activating.
Currently the largest chapter is ~28 KB and setHtml is fast enough,
but future 1MB+ chapters may want chunked rendering.

Usage:
    python scripts/benchmark_chapter_render.py
"""

from __future__ import annotations

import statistics
import sys
import time

from PySide6.QtWidgets import QApplication, QTextBrowser

SIZES_BYTES = (28_000, 100_000, 500_000, 1_000_000)
TRIALS = 3


def make_html(size_bytes: int) -> str:
    """Synthesize a paragraph-heavy HTML of roughly size_bytes."""
    para = (
        "<p>This is a paragraph with several sentences of test prose that "
        "exercise word wrap and layout. It contains punctuation, numbers like "
        "12345, and unicode characters like 中文 and emoji 🎉 to mimic "
        "realistic content.</p>\n"
    )
    out = []
    total = 0
    while total < size_bytes:
        out.append(para)
        total += len(para)
    return "".join(out)[:size_bytes]


def measure_sethtml(browser, html, trials=TRIALS):
    times = []
    for _ in range(trials):
        t0 = time.perf_counter()
        browser.setHtml(html)
        times.append(time.perf_counter() - t0)
    return statistics.median(times)


def main():
    _app = QApplication.instance() or QApplication(sys.argv)

    header = f"{'size_KB':>10}  {'med_ms':>10}  {'trials':>10}"
    print(header)
    print("-" * len(header))
    for size in SIZES_BYTES:
        html = make_html(size)
        browser = QTextBrowser()
        med = measure_sethtml(browser, html)
        print(f"{size/1024:>10.1f}  {med*1000:>10.1f}  {TRIALS:>10}")

    print()
    print("Decision rule:")
    print("  - If med_ms > 200ms at 1MB+, consider activating chunked rendering")
    print("    via desktop/components/chunked_html_setter.py (Phase M2.4 deliverable).")
    print("  - Until then, keep current setHtml path; chunks add complexity")
    print("    without benefit at current sizes.")


if __name__ == "__main__":
    main()
