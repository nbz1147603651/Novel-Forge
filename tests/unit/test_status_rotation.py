"""E2E tests for the status rotation feature in ChapterStudioCoordMixin."""

from __future__ import annotations

import os
from typing import Set
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QMessageBox,
    QSpinBox,
    QWidget,
)

from novel_forge.desktop.components.primitives import Badge
from novel_forge.desktop.jobs import DesktopJobRecord, DesktopJobState
from novel_forge.desktop.pages.chapter_studio.coord import ChapterStudioCoordMixin
from novel_forge.desktop.pages.chapter_studio.dialogs import ProjectSwitchConfirmDialog


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def _make_job(
    job_id: str,
    project_id: str,
    status: DesktopJobState,
    chapter_number: int = 1,
    kind: str = "run_chapter",
) -> DesktopJobRecord:
    return DesktopJobRecord(
        job_id=job_id,
        kind=kind,
        label=f"任务 {job_id}",
        project_id=project_id,
        status=status,
        result={"chapter_number": chapter_number},
    )


class _MockTimer:
    """Mock QTimer that tracks start/stop calls."""

    def __init__(self) -> None:
        self._active = False
        self._interval = 5000
        self.start_calls: list[int] = []
        self.stop_calls: int = 0

    def isActive(self) -> bool:
        return self._active

    def start(self, interval: int | None = None) -> None:
        self._active = True
        if interval is not None:
            self._interval = interval
        self.start_calls.append(interval if interval is not None else self._interval)

    def stop(self) -> None:
        self._active = False
        self.stop_calls += 1

    def setInterval(self, interval: int) -> None:
        self._interval = interval

    def connect_timeout(self, callback) -> None:
        pass


class _MockActionPanel(QWidget):
    """Mock action panel for event filter testing."""

    def __init__(self) -> None:
        super().__init__()
        self._event_filter = None

    def installEventFilter(self, obj: QObject) -> None:
        self._event_filter = obj

    def removeEventFilter(self, obj: QObject) -> None:
        self._event_filter = None


class _MockEvent:
    """Mock QEvent for hover testing."""

    def __init__(self, event_type: QEvent.Type) -> None:
        self._type = event_type

    def type(self) -> QEvent.Type:
        return self._type


class _RotationTestCoord(ChapterStudioCoordMixin, QWidget):
    """Test coordination class with real QTimer for status rotation tests."""

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self.chapter_context_requested = MagicMock()
        self.navigate_requested = MagicMock()
        self.open_project_requested = MagicMock()
        self.view_project_requested = MagicMock()
        self.cancel_job_requested = MagicMock()
        self.context_changed = MagicMock()

        self._project_combo = QComboBox()
        self._project_combo.setEditable(True)
        self._project_combo.addItem("project-a")
        self._project_combo.addItem("project-b")
        self._project_combo.addItem("project-c")
        self._project_combo.setCurrentText("project-a")

        self._chapter_spin = QSpinBox()
        self._chapter_spin.setRange(1, 10000)
        self._chapter_spin.setValue(1)

        self._previous_project: str | None = "project-a"
        self._active_projects: Set[str] = set()
        self._all_jobs: list[DesktopJobRecord] | None = None

        self._studio = None
        self._state = MagicMock()
        self._workspace = None

        self._context_request_timer = _MockTimer()

        self._auto_pilot = False
        self._auto_started = False

        self._rotation_index: int = 0
        self._rotation_paused: bool = False
        self._rotation_active_projects: list[str] = []
        self._status_rotation_timer = QTimer()
        self._status_rotation_timer.setInterval(5000)
        self._status_rotation_timer.timeout.connect(self._on_status_rotation_tick)

        self._action_panel = _MockActionPanel()
        self._action_panel.installEventFilter(self)

        self._project_status_dot = MagicMock()

        self._action_title = MagicMock()
        self._action_badge = Badge("待命", tone="default")
        self._action_summary = MagicMock()

    def _stop_auto_pilot(self, cancel_jobs: bool = False) -> None:
        pass

    def window(self) -> None:
        return None

    def current_project_id(self) -> str:
        return self._project_combo.currentText().strip()

    def current_chapter_number(self) -> int:
        return self._chapter_spin.value()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        # Override to handle mock objects in tests without calling
        # QObject.eventFilter() which rejects non-QObject/QEvent types.
        if hasattr(self, "_action_panel") and obj is self._action_panel:
            if event.type() == QEvent.Type.Enter:
                self._rotation_paused = True
            elif event.type() == QEvent.Type.Leave:
                self._rotation_paused = False
        return False


class TestStatusRotationTimerStart:
    """Scenario 1: Timer starts with 2+ active projects."""

    def test_timer_starts_with_two_active_projects(self, qapp: QApplication) -> None:
        mixin = _RotationTestCoord(qapp)
        mixin._all_jobs = [
            _make_job("job-1", "project-a", DesktopJobState.RUNNING),
            _make_job("job-2", "project-b", DesktopJobState.RUNNING),
        ]

        mixin._start_status_rotation()

        assert mixin._status_rotation_timer.isActive() is True
        assert len(mixin._rotation_active_projects) == 2
        assert "project-a" in mixin._rotation_active_projects
        assert "project-b" in mixin._rotation_active_projects

    def test_timer_starts_with_three_active_projects(self, qapp: QApplication) -> None:
        mixin = _RotationTestCoord(qapp)
        mixin._all_jobs = [
            _make_job("job-1", "project-a", DesktopJobState.RUNNING),
            _make_job("job-2", "project-b", DesktopJobState.QUEUED),
            _make_job("job-3", "project-c", DesktopJobState.RUNNING),
        ]

        mixin._start_status_rotation()

        assert mixin._status_rotation_timer.isActive() is True
        assert len(mixin._rotation_active_projects) == 3


class TestStatusRotationTimerStop:
    """Scenario 2: Timer stops with 1 active project."""

    def test_timer_stops_with_one_active_project(self, qapp: QApplication) -> None:
        mixin = _RotationTestCoord(qapp)
        mixin._all_jobs = [
            _make_job("job-1", "project-a", DesktopJobState.RUNNING),
            _make_job("job-2", "project-b", DesktopJobState.SUCCEEDED),
        ]

        mixin._start_status_rotation()

        assert mixin._status_rotation_timer.isActive() is False
        assert len(mixin._rotation_active_projects) == 1

    def test_timer_stops_with_no_active_projects(self, qapp: QApplication) -> None:
        mixin = _RotationTestCoord(qapp)
        mixin._all_jobs = [
            _make_job("job-1", "project-a", DesktopJobState.SUCCEEDED),
            _make_job("job-2", "project-b", DesktopJobState.FAILED),
        ]

        mixin._start_status_rotation()

        assert mixin._status_rotation_timer.isActive() is False
        assert len(mixin._rotation_active_projects) == 0

    def test_timer_stops_when_all_jobs_none(self, qapp: QApplication) -> None:
        mixin = _RotationTestCoord(qapp)
        mixin._all_jobs = None

        mixin._start_status_rotation()

        assert mixin._status_rotation_timer.isActive() is False
        assert mixin._rotation_active_projects == []


class TestStatusRotationTick:
    """Scenario 3: Timer tick advances rotation index."""

    def test_tick_advances_index(self, qapp: QApplication) -> None:
        mixin = _RotationTestCoord(qapp)
        mixin._all_jobs = [
            _make_job("job-1", "project-a", DesktopJobState.RUNNING),
            _make_job("job-2", "project-b", DesktopJobState.RUNNING),
        ]
        mixin._start_status_rotation()
        assert mixin._rotation_active_projects == ["project-a", "project-b"]

        mixin._rotation_index = 0
        mixin._on_status_rotation_tick()

        assert mixin._rotation_index == 1

    def test_tick_wraps_around(self, qapp: QApplication) -> None:
        mixin = _RotationTestCoord(qapp)
        mixin._all_jobs = [
            _make_job("job-1", "project-a", DesktopJobState.RUNNING),
            _make_job("job-2", "project-b", DesktopJobState.RUNNING),
        ]
        mixin._start_status_rotation()

        mixin._rotation_index = 1
        mixin._on_status_rotation_tick()

        assert mixin._rotation_index == 0

    def test_tick_does_nothing_when_paused(self, qapp: QApplication) -> None:
        mixin = _RotationTestCoord(qapp)
        mixin._all_jobs = [
            _make_job("job-1", "project-a", DesktopJobState.RUNNING),
            _make_job("job-2", "project-b", DesktopJobState.RUNNING),
        ]
        mixin._start_status_rotation()
        mixin._rotation_index = 0
        mixin._rotation_paused = True

        mixin._on_status_rotation_tick()

        assert mixin._rotation_index == 0


class TestHoverPauseRotation:
    """Scenario 4: Hover pauses rotation."""

    def test_enter_event_pauses_rotation(self, qapp: QApplication) -> None:
        mixin = _RotationTestCoord(qapp)
        mixin._rotation_paused = False

        enter_event = _MockEvent(QEvent.Type.Enter)
        mixin.eventFilter(mixin._action_panel, enter_event)

        assert mixin._rotation_paused is True

    def test_leave_event_resumes_rotation(self, qapp: QApplication) -> None:
        mixin = _RotationTestCoord(qapp)
        mixin._rotation_paused = True

        leave_event = _MockEvent(QEvent.Type.Leave)
        mixin.eventFilter(mixin._action_panel, leave_event)

        assert mixin._rotation_paused is False

    def test_hover_sequence(self, qapp: QApplication) -> None:
        mixin = _RotationTestCoord(qapp)
        mixin._rotation_paused = False

        enter_event = _MockEvent(QEvent.Type.Enter)
        mixin.eventFilter(mixin._action_panel, enter_event)
        assert mixin._rotation_paused is True

        leave_event = _MockEvent(QEvent.Type.Leave)
        mixin.eventFilter(mixin._action_panel, leave_event)
        assert mixin._rotation_paused is False

        enter_event2 = _MockEvent(QEvent.Type.Enter)
        mixin.eventFilter(mixin._action_panel, enter_event2)
        assert mixin._rotation_paused is True


class TestProjectRemovedEdgeCase:
    """Scenario 5: Project removed edge case."""

    def test_index_reset_when_project_removed(self, qapp: QApplication) -> None:
        mixin = _RotationTestCoord(qapp)
        mixin._all_jobs = [
            _make_job("job-1", "project-a", DesktopJobState.RUNNING),
            _make_job("job-2", "project-b", DesktopJobState.RUNNING),
        ]
        mixin._start_status_rotation()
        assert mixin._rotation_active_projects == ["project-a", "project-b"]

        mixin._rotation_index = 1

        mixin._all_jobs = [
            _make_job("job-1", "project-a", DesktopJobState.RUNNING),
            _make_job("job-2", "project-b", DesktopJobState.SUCCEEDED),
        ]

        mixin._on_status_rotation_tick()

        assert mixin._rotation_active_projects == ["project-a"]
        assert mixin._rotation_index == 0

    def test_timer_stops_when_only_one_project_remains(self, qapp: QApplication) -> None:
        mixin = _RotationTestCoord(qapp)
        mixin._all_jobs = [
            _make_job("job-1", "project-a", DesktopJobState.RUNNING),
            _make_job("job-2", "project-b", DesktopJobState.RUNNING),
        ]
        mixin._start_status_rotation()
        assert mixin._status_rotation_timer.isActive() is True

        mixin._all_jobs = [
            _make_job("job-1", "project-a", DesktopJobState.RUNNING),
            _make_job("job-2", "project-b", DesktopJobState.SUCCEEDED),
        ]
        mixin._rotation_index = 1

        mixin._on_status_rotation_tick()

        assert mixin._rotation_active_projects == ["project-a"]
        assert mixin._rotation_index == 0


class TestAllJobsCompleteEdgeCase:
    """Scenario 6: All jobs complete edge case."""

    def test_bind_jobs_empty_stops_timer(self, qapp: QApplication) -> None:
        mixin = _RotationTestCoord(qapp)
        mixin._all_jobs = [
            _make_job("job-1", "project-a", DesktopJobState.RUNNING),
            _make_job("job-2", "project-b", DesktopJobState.RUNNING),
        ]
        mixin._start_status_rotation()
        assert mixin._status_rotation_timer.isActive() is True

        mixin._jobs_fingerprint = ("old",)
        mixin._build_jobs_fingerprint = MagicMock(return_value=())
        mixin._select_latest_memory_event = MagicMock(return_value=None)
        mixin._load_memory_status_from_disk = MagicMock(return_value=None)
        mixin._handle_memory_invalidated = MagicMock()
        mixin._handle_memory_updated = MagicMock()
        mixin._last_applied_memory_event_key = {}
        mixin._jobs = []
        mixin._book_level_jobs = []
        mixin._rail = MagicMock()
        mixin._render_action_panel = MagicMock()
        mixin._render_jobs_panel = MagicMock()
        mixin._update_repair_btn_state = MagicMock()
        mixin._update_reevaluate_btn_state = MagicMock()
        mixin._update_reextract_btn_state = MagicMock()
        mixin._update_motif_repair_btn_state = MagicMock()
        mixin._update_book_level_btn_state = MagicMock()
        mixin._try_auto_action = MagicMock()
        mixin._state.scheduled_retry_at = 0

        mixin.bind_jobs([])

        assert mixin._status_rotation_timer.isActive() is False


class TestRapidProjectSwitch:
    """Scenario 7: Rapid project switch."""

    def test_project_switch_resets_rotation_index(self, qapp: QApplication) -> None:
        mixin = _RotationTestCoord(qapp)
        mixin._all_jobs = [
            _make_job("job-1", "project-a", DesktopJobState.RUNNING),
            _make_job("job-2", "project-b", DesktopJobState.RUNNING),
        ]
        mixin._start_status_rotation()
        mixin._rotation_index = 1

        # Switch to a different project to trigger the reset path
        mixin._previous_project = "project-b"
        with patch.object(ProjectSwitchConfirmDialog, "exec", return_value=QMessageBox.StandardButton.Yes):
            mixin._on_project_selection_changed("project-a")

        assert mixin._rotation_index == 0
        assert mixin._rotation_paused is False

    def test_timer_still_running_after_switch_with_2_active(self, qapp: QApplication) -> None:
        mixin = _RotationTestCoord(qapp)
        mixin._all_jobs = [
            _make_job("job-1", "project-a", DesktopJobState.RUNNING),
            _make_job("job-2", "project-b", DesktopJobState.RUNNING),
        ]
        mixin._start_status_rotation()
        assert mixin._status_rotation_timer.isActive() is True

        with patch.object(ProjectSwitchConfirmDialog, "exec", return_value=QMessageBox.StandardButton.Yes):
            mixin._on_project_selection_changed("project-a")

        assert mixin._status_rotation_timer.isActive() is True


class TestShutdownStopsTimer:
    """Scenario 8: shutdown stops timer."""

    def test_shutdown_stops_status_rotation_timer(self, qapp: QApplication) -> None:
        mixin = _RotationTestCoord(qapp)
        mixin._status_rotation_timer.start()
        assert mixin._status_rotation_timer.isActive() is True

        if hasattr(mixin, "_status_rotation_timer"):
            mixin._status_rotation_timer.stop()

        assert mixin._status_rotation_timer.isActive() is False

    def test_shutdown_stops_timer_from_page(self, qapp: QApplication) -> None:
        from novel_forge.desktop.pages.chapter_studio.page import ChapterStudioPage

        page = ChapterStudioPage()

        page._status_rotation_timer.start()
        assert page._status_rotation_timer.isActive() is True

        page.shutdown()

        assert page._status_rotation_timer.isActive() is False
