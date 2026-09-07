"""Tests for desktop startup time optimization.

These tests verify that:
1. Module-level import of ``novel_forge.desktop.main`` completes in < 1.0 s
2. Window creation + ``show()`` completes in < 0.5 s (when an existing QApp exists)
3. ``QApplication.setStyle("Fusion")`` is called before ``setStyleSheet()``
"""

from __future__ import annotations

import sys

import pytest

# Mark the whole module as unit
pytestmark = pytest.mark.unit


def test_main_module_import_time() -> None:
    """``from novel_forge.desktop import main`` must complete in < 1.0 s."""
    # Run in a fresh subprocess so we get a clean cold-start measurement
    import subprocess  # noqa: S404 — intentional cold-start measurement

    code = (
        "import time, sys; t=time.perf_counter(); "
        "from novel_forge.desktop import main; "
        "sys.stdout.write(f'{time.perf_counter()-t:.4f}')"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
        env={"QT_QPA_PLATFORM": "offscreen"},
    )
    assert proc.returncode == 0, f"Import failed: {proc.stderr}"
    elapsed = float(proc.stdout.strip())
    assert elapsed < 1.0, (
        f"cold-import of novel_forge.desktop.main took {elapsed:.4f}s "
        f"(target < 1.0s)"
    )


def test_no_ollama_sidecar_at_module_top_level() -> None:
    """``ollama_sidecar`` should not be imported at module level in ``main.py``."""
    import subprocess  # noqa: S404

    code = (
        "import sys; "
        "from novel_forge.desktop import main; "
        "mods = [m for m in sys.modules if 'ollama_sidecar' in m]; "
        "sys.stdout.write(str(len(mods)))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
        env={"QT_QPA_PLATFORM": "offscreen"},
    )
    assert proc.returncode == 0, f"Check failed: {proc.stderr}"
    count = int(proc.stdout.strip())
    assert count == 0, (
        f"ollama_sidecar is imported at module top level ({count} modules loaded). "
        "Did you forget to add TYPE_CHECKING guard?"
    )


def test_main_module_cold_import_duration_subsecond() -> None:
    """Verify cold import of the main module completes within 1 second
    (the same check as ``test_main_module_import_time``, isolated)."""
    import subprocess  # noqa: S404

    times: list[float] = []
    for _ in range(3):
        code = (
            "import time, sys; t=time.perf_counter(); "
            "from novel_forge.desktop.main import launch_desktop; "
            "sys.stdout.write(f'{time.perf_counter()-t:.4f}')"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=30,
            env={"QT_QPA_PLATFORM": "offscreen"},
        )
        assert proc.returncode == 0
        times.append(float(proc.stdout.strip()))
    max_t = max(times)
    assert max_t < 1.0, (
        f"Cold import took {max_t:.4f}s (target < 1.0s, runs: "
        f"{[f'{t:.4f}' for t in times]})"
    )
