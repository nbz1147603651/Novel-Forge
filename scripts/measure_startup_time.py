#!/usr/bin/env python3
"""Measure desktop startup time (import + window.show).

Usage:
    QT_QPA_PLATFORM=offscreen python scripts/measure_startup_time.py

Output:
    - Per-run import time
    - Per-run window.show() time (approximate)
    - Min / median / max over 3 runs
"""

from __future__ import annotations

import subprocess
import sys
import time

SCRIPT_IMPORT = r"""
import time
t = time.perf_counter()
from novel_forge.desktop.main import launch_desktop
t1 = time.perf_counter()
print(f'IMPORT:{t1 - t:.4f}')
"""

SCRIPT_FULL = r"""
import sys
import time

from PySide6.QtWidgets import QApplication

from novel_forge.core.config import get_settings
from novel_forge.desktop.ollama_sidecar import OllamaSidecarService
from novel_forge.desktop.window import NovelForgeDesktopWindow

app = QApplication.instance() or QApplication(sys.argv)

t = time.perf_counter()
sidecar = OllamaSidecarService.from_settings(get_settings())
result = sidecar.ensure_available()
window = NovelForgeDesktopWindow(ollama_sidecar=sidecar, ollama_sidecar_result=result)
window.show()
elapsed = time.perf_counter() - t
print(f'WINDOW_SHOW:{elapsed:.4f}')
window.close()
"""


def _run_python(code: str, label: str) -> list[float]:
    times: list[float] = []
    for i in range(3):
        t0 = time.perf_counter()
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
            env={"QT_QPA_PLATFORM": "offscreen"},
        )
        wall = time.perf_counter() - t0
        out = proc.stdout.strip()
        val = None
        for line in out.splitlines():
            if ":" in line:
                try:
                    val = float(line.split(":", 1)[1])
                except ValueError:
                    pass
        if val is not None:
            times.append(val)
        print(f"  Run {i+1}: {val or 0:.4f}s (wall: {wall:.2f}s)")
    return times


def _stats(times: list[float]) -> tuple[float, float, float]:
    sorted_t = sorted(times)
    return sorted_t[0], sorted_t[len(sorted_t) // 2], sorted_t[-1]


def main() -> None:
    print("=" * 50)
    print("Startup Time Measurement")
    print("=" * 50)

    print("\n[1/2] Module import time (novel_forge.desktop.main):")
    import_times = _run_python(SCRIPT_IMPORT, "import")
    if import_times:
        lo, med, hi = _stats(import_times)
        print(f"  >> Import: min={lo:.4f}s  median={med:.4f}s  max={hi:.4f}s")

    print("\n[2/2] Window create + show() time:")
    win_times = _run_python(SCRIPT_FULL, "window")
    if win_times:
        lo, med, hi = _stats(win_times)
        print(f"  >> Window: min={lo:.4f}s  median={med:.4f}s  max={hi:.4f}s")

    print("\n" + "=" * 50)
    all_ok = True
    if import_times and max(import_times) >= 1.0:
        print("⚠️  WARNING: Import time >= 1.0s (target: < 1.0s)")
        all_ok = False
    if win_times and max(win_times) >= 0.5:
        print("⚠️  WARNING: Window.show() time >= 0.5s (target: < 0.5s)")
        all_ok = False
    if all_ok:
        print("✅ All startup time targets met.")
    print("=" * 50)


if __name__ == "__main__":
    main()
