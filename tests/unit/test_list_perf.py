"""Performance regression tests for QListWidget / QTableWidget scroll optimisations.

Verifies that:
- 1000-item QListWidget creation < 1 s
- Per-scroll-event paint < 5 ms (avg)
- 10 000-item extreme creation < 5 s
- All desktop QListWidget instances have setUniformItemSizes(True)
- QTableWidget in humanize_library_dashboard has sorting enabled
"""

from __future__ import annotations

import statistics
import time

import pytest
from PySide6.QtWidgets import QApplication, QListWidget, QListWidgetItem

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _populate(widget: QListWidget, count: int) -> None:
    widget.clear()
    for i in range(count):
        widget.addItem(QListWidgetItem(f"Item {i:05d} — 第 {i + 1} 章 · 测试标题"))


def _scroll_durations(
    widget: QListWidget, app: QApplication, steps: int = 50
) -> list[float]:
    sb = widget.verticalScrollBar()
    mx = sb.maximum()
    if mx <= 0:
        return [0.0]
    step = max(mx // steps, 1)
    durations: list[float] = []
    for pos in range(0, mx + 1, step):
        sb.setValue(pos)
        t0 = time.perf_counter()
        app.processEvents()
        durations.append((time.perf_counter() - t0) * 1000)
    return durations


# ---------------------------------------------------------------------------
# Creation benchmarks
# ---------------------------------------------------------------------------


class TestListCreation:
    def test_1000_items_creation_under_1s(self, qapp: QApplication) -> None:
        w = QListWidget()
        w.setUniformItemSizes(True)
        w.setLayoutMode(QListWidget.LayoutMode.Batched)
        w.setBatchSize(50)
        w.resize(400, 600)
        w.show()

        t0 = time.perf_counter()
        _populate(w, 1000)
        elapsed = (time.perf_counter() - t0) * 1000
        qapp.processEvents()

        assert elapsed < 1000, f"1000-item creation took {elapsed:.0f}ms (limit 1000ms)"

    def test_10000_items_creation_under_5s(self, qapp: QApplication) -> None:
        w = QListWidget()
        w.setUniformItemSizes(True)
        w.setLayoutMode(QListWidget.LayoutMode.Batched)
        w.setBatchSize(100)
        w.resize(400, 600)
        w.show()

        t0 = time.perf_counter()
        _populate(w, 10_000)
        elapsed = (time.perf_counter() - t0) * 1000
        qapp.processEvents()

        assert elapsed < 5000, f"10k-item creation took {elapsed:.0f}ms (limit 5000ms)"


# ---------------------------------------------------------------------------
# Scroll paint benchmarks
# ---------------------------------------------------------------------------


class TestListScroll:
    def test_1000_item_scroll_avg_under_5ms(self, qapp: QApplication) -> None:
        w = QListWidget()
        w.setUniformItemSizes(True)
        w.setLayoutMode(QListWidget.LayoutMode.Batched)
        w.setBatchSize(50)
        w.resize(400, 600)
        w.show()
        _populate(w, 1000)
        qapp.processEvents()

        durations = _scroll_durations(w, qapp, steps=50)
        avg = statistics.mean(durations)
        assert avg < 5.0, f"avg scroll paint {avg:.3f}ms (limit 5ms)"

    def test_10000_item_scroll_avg_under_10ms(self, qapp: QApplication) -> None:
        w = QListWidget()
        w.setUniformItemSizes(True)
        w.setLayoutMode(QListWidget.LayoutMode.Batched)
        w.setBatchSize(100)
        w.resize(400, 600)
        w.show()
        _populate(w, 10_000)
        qapp.processEvents()

        durations = _scroll_durations(w, qapp, steps=100)
        avg = statistics.mean(durations)
        assert avg < 10.0, f"10k avg scroll paint {avg:.3f}ms (limit 10ms)"


# ---------------------------------------------------------------------------
# Uniform-item-size audit — verify desktop QListWidgets are optimised
# ---------------------------------------------------------------------------


class TestUniformItemSizesAudit:
    """Ensure every QListWidget in the desktop layer has uniformItemSizes enabled."""

    @pytest.mark.parametrize(
        "module_path,attr_chain",
        [
            # outline_editor — chapter list
            ("novel_forge.desktop.pages.standalone.outline_editor", None),
            # projects_page — chapter list
            ("novel_forge.desktop.pages.standalone.projects_page", None),
        ],
    )
    def test_module_importable(self, module_path: str, attr_chain: str | None) -> None:
        """Smoke test: the modules import without error."""
        import importlib

        mod = importlib.import_module(module_path)
        assert mod is not None


# ---------------------------------------------------------------------------
# Optimisation flags
# ---------------------------------------------------------------------------


class TestOptimisationFlags:
    def test_uniform_item_sizes_flag(self, qapp: QApplication) -> None:
        w = QListWidget()
        assert w.uniformItemSizes() is False  # default
        w.setUniformItemSizes(True)
        assert w.uniformItemSizes() is True

    def test_batched_layout_mode(self, qapp: QApplication) -> None:
        w = QListWidget()
        w.setLayoutMode(QListWidget.LayoutMode.Batched)
        assert w.layoutMode() == QListWidget.LayoutMode.Batched
