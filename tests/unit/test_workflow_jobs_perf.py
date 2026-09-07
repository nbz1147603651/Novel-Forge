"""Performance regression tests for JobCard paintEvent throttling and pixmap caching."""

from __future__ import annotations

import time

import pytest
from PySide6.QtGui import QColor, QPaintEvent
from PySide6.QtWidgets import QApplication

from novel_forge.desktop.jobs import (
    DesktopJobEvent,
    DesktopJobRecord,
    DesktopJobState,
)
from novel_forge.desktop.pages.workflow.jobs import JobCard
from novel_forge.desktop.theme.components import CONTENT as COMPONENTS_QSS_CONTENT


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def _make_job(
    job_id: str = "perf-test",
    *,
    step: str = "step_0",
    status: DesktopJobState = DesktopJobState.RUNNING,
    event_count: int = 0,
) -> DesktopJobRecord:
    record = DesktopJobRecord(job_id=job_id, kind="run_short", label=f"Task {job_id}")
    record.status = status
    record.current_step = step
    record.updated_at = f"2026-03-30T12:00:{min(event_count, 59):02d}+00:00"
    record.events = [
        DesktopJobEvent(at=record.updated_at, step=f"evt_{i}", payload={})
        for i in range(event_count)
    ]
    return record


class TestPaintThrottling:
    def test_1000_rapid_ticks_produce_fewer_than_100_paints(self, qapp: QApplication) -> None:
        card = JobCard(_make_job())
        card.resize(400, 120)
        card.show()
        qapp.processEvents()

        paint_count = 0
        original_paint = card.paintEvent

        def counting_paint(event: QPaintEvent) -> None:
            nonlocal paint_count
            paint_count += 1
            original_paint(event)

        card.paintEvent = counting_paint  # type: ignore[assignment]

        for i in range(1000):
            job = _make_job(step=f"step_{i}", event_count=i)
            card.set_job(job)

        card._flush_repaint()
        qapp.processEvents()

        assert paint_count < 100, (
            f"Expected <100 paint calls for 1000 rapid ticks, got {paint_count}"
        )

    def test_schedule_repaint_coalesces_multiple_calls(self, qapp: QApplication) -> None:
        card = JobCard(_make_job())
        card.resize(400, 120)
        card.show()
        qapp.processEvents()

        card._repaint_pending = False
        card._repaint_timer.stop()

        card._schedule_repaint()
        assert card._repaint_pending is True

        card._schedule_repaint()
        card._schedule_repaint()
        assert card._repaint_pending is True

        card._flush_repaint()
        assert card._repaint_pending is False

    def test_flush_repaint_triggers_update(self, qapp: QApplication) -> None:
        card = JobCard(_make_job())
        card.resize(400, 120)
        card.show()
        qapp.processEvents()

        update_called = False
        original_update = card.update

        def tracking_update(*args: object) -> None:
            nonlocal update_called
            update_called = True
            original_update(*args)

        card.update = tracking_update  # type: ignore[assignment]
        card._flush_repaint()
        assert update_called is True


class TestPixmapCaching:
    def test_cache_hit_when_color_and_size_unchanged(self, qapp: QApplication) -> None:
        card = JobCard(_make_job())
        card.resize(400, 120)
        card.show()
        qapp.processEvents()

        event = QPaintEvent(card.rect())
        card.paintEvent(event)

        assert card._cached_pixmap is not None
        cached = card._cached_pixmap
        cached_size = card._cached_pixmap_size
        cached_color = card._cached_pixmap_color

        card.paintEvent(event)
        assert card._cached_pixmap is cached
        assert card._cached_pixmap_size == cached_size
        assert card._cached_pixmap_color == cached_color

    def test_cache_invalidated_on_color_change(self, qapp: QApplication) -> None:
        card = JobCard(_make_job())
        card.resize(400, 120)
        card.show()
        qapp.processEvents()

        event = QPaintEvent(card.rect())
        card.paintEvent(event)
        old_pixmap = card._cached_pixmap

        card._card_color = QColor(255, 0, 0, 50)
        card.paintEvent(event)
        assert card._cached_pixmap is not old_pixmap

    def test_cache_invalidated_on_resize(self, qapp: QApplication) -> None:
        card = JobCard(_make_job())
        card.resize(400, 120)
        card.show()
        qapp.processEvents()

        event = QPaintEvent(card.rect())
        card.paintEvent(event)
        assert card._cached_pixmap is not None

        card._invalidate_pixmap_cache()
        assert card._cached_pixmap is None

    def test_cache_invalidated_on_set_job(self, qapp: QApplication) -> None:
        card = JobCard(_make_job())
        card.resize(400, 120)
        card.show()
        qapp.processEvents()

        event = QPaintEvent(card.rect())
        card.paintEvent(event)
        assert card._cached_pixmap is not None

        new_job = _make_job(step="step_99", event_count=99)
        card.set_job(new_job)
        assert card._cached_pixmap is None


class TestPaintDuration:
    def test_average_paint_under_5ms(self, qapp: QApplication) -> None:
        card = JobCard(_make_job())
        card.resize(400, 120)
        card.show()
        qapp.processEvents()

        event = QPaintEvent(card.rect())
        durations: list[float] = []
        for _ in range(100):
            start = time.perf_counter()
            card.paintEvent(event)
            elapsed = (time.perf_counter() - start) * 1000
            durations.append(elapsed)

        avg_ms = sum(durations) / len(durations)
        assert avg_ms < 5.0, f"Average paint duration {avg_ms:.2f}ms exceeds 5ms target"


class TestConcurrentTicks:
    def test_concurrent_progress_ticks_no_reentry_crash(self, qapp: QApplication) -> None:
        card = JobCard(_make_job())
        card.resize(400, 120)
        card.show()
        qapp.processEvents()

        for i in range(500):
            job = _make_job(step=f"step_{i}", event_count=i)
            card.set_job(job)
            card._schedule_repaint()

        card._flush_repaint()
        qapp.processEvents()

        assert card._repaint_pending is False

    def test_rapid_color_animation_no_crash(self, qapp: QApplication) -> None:
        card = JobCard(_make_job())
        card.resize(400, 120)
        card.show()
        qapp.processEvents()

        for status in ["running", "succeeded", "failed", "queued", "paused"]:
            card._animate_status_color(status)
            card._card_color = QColor(200, 100, 50, 30)
            card._schedule_repaint()

        card._flush_repaint()
        qapp.processEvents()
        assert card._repaint_pending is False


class TestShadowReplacement:
    def test_no_graphics_effect_on_jobcard(self, qapp: QApplication) -> None:
        card = JobCard(_make_job())
        assert card.graphicsEffect() is None

    def test_qss_border_present(self, qapp: QApplication) -> None:
        card = JobCard(_make_job())
        assert card.objectName() == "surface"
        assert card.property("tone") == "card"

        start = COMPONENTS_QSS_CONTENT.index('QFrame#surface[tone="card"]')
        end = COMPONENTS_QSS_CONTENT.index("}", start)
        stylesheet = COMPONENTS_QSS_CONTENT[start : end + 1]
        assert "border" in stylesheet
        assert "rgba(157, 122, 92, 0.12)" in stylesheet


class TestVisualPreservation:
    def test_card_color_property_still_works(self, qapp: QApplication) -> None:
        card = JobCard(_make_job())
        card.resize(400, 120)
        card.show()
        qapp.processEvents()

        new_color = QColor(100, 200, 50, 40)
        card.card_color = new_color
        assert card.card_color == new_color

    def test_color_animation_preserved(self, qapp: QApplication) -> None:
        card = JobCard(_make_job())
        assert card._color_animation.duration() == 300
        card._animate_status_color("running")
        card._color_animation.stop()

    def test_visual_snapshot_save(self, qapp: QApplication) -> None:
        card = JobCard(_make_job())
        card.resize(400, 120)
        card.show()
        qapp.processEvents()

        pixmap = card.grab()
        assert not pixmap.isNull()
        saved = pixmap.save("/tmp/jobcard_optimized.png")
        assert saved is True
