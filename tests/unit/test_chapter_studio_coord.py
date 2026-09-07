"""Unit tests for ChapterStudioCoordMixin project switching feature."""

from __future__ import annotations

import os
from typing import Set
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QComboBox, QSpinBox, QTextEdit, QWidget

from novel_forge.desktop.jobs import DesktopJobRecord, DesktopJobState
from novel_forge.desktop.pages.chapter_studio.coord import ChapterStudioCoordMixin
from novel_forge.desktop.pages.chapter_studio.dialogs import ProjectSwitchConfirmDialog
from novel_forge.workspace.contracts import DecisionCheckpoint, DecisionOption


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


class TestGetProjectsWithActiveJobs:
    """Tests for the _get_projects_with_active_jobs static-like method."""

    def test_returns_running_jobs(self) -> None:
        mixin = _create_pure_coord_mixin()
        jobs = [
            _make_job("job-1", "project-a", DesktopJobState.RUNNING),
            _make_job("job-2", "project-b", DesktopJobState.SUCCEEDED),
        ]

        result = mixin._get_projects_with_active_jobs(jobs)

        assert result == {"project-a"}

    def test_returns_queued_jobs(self) -> None:
        mixin = _create_pure_coord_mixin()
        jobs = [
            _make_job("job-1", "project-a", DesktopJobState.QUEUED),
            _make_job("job-2", "project-b", DesktopJobState.SUCCEEDED),
        ]

        result = mixin._get_projects_with_active_jobs(jobs)

        assert result == {"project-a"}

    def test_returns_multiple_active_projects(self) -> None:
        mixin = _create_pure_coord_mixin()
        jobs = [
            _make_job("job-1", "project-a", DesktopJobState.RUNNING),
            _make_job("job-2", "project-b", DesktopJobState.QUEUED),
            _make_job("job-3", "project-c", DesktopJobState.RUNNING),
        ]

        result = mixin._get_projects_with_active_jobs(jobs)

        assert result == {"project-a", "project-b", "project-c"}

    def test_ignores_succeeded_jobs(self) -> None:
        mixin = _create_pure_coord_mixin()
        jobs = [
            _make_job("job-1", "project-a", DesktopJobState.SUCCEEDED),
            _make_job("job-2", "project-b", DesktopJobState.SUCCEEDED),
        ]

        result = mixin._get_projects_with_active_jobs(jobs)

        assert result == set()

    def test_ignores_failed_jobs(self) -> None:
        mixin = _create_pure_coord_mixin()
        jobs = [
            _make_job("job-1", "project-a", DesktopJobState.FAILED),
            _make_job("job-2", "project-b", DesktopJobState.FAILED),
        ]

        result = mixin._get_projects_with_active_jobs(jobs)

        assert result == set()

    def test_ignores_paused_jobs(self) -> None:
        mixin = _create_pure_coord_mixin()
        jobs = [
            _make_job("job-1", "project-a", DesktopJobState.PAUSED),
        ]

        result = mixin._get_projects_with_active_jobs(jobs)

        assert result == set()

    def test_empty_job_list(self) -> None:
        mixin = _create_pure_coord_mixin()

        result = mixin._get_projects_with_active_jobs([])

        assert result == set()

    def test_mixed_active_and_inactive(self) -> None:
        mixin = _create_pure_coord_mixin()
        jobs = [
            _make_job("job-1", "project-a", DesktopJobState.RUNNING),
            _make_job("job-2", "project-b", DesktopJobState.QUEUED),
            _make_job("job-3", "project-c", DesktopJobState.SUCCEEDED),
            _make_job("job-4", "project-d", DesktopJobState.FAILED),
            _make_job("job-5", "project-e", DesktopJobState.PAUSED),
        ]

        result = mixin._get_projects_with_active_jobs(jobs)

        assert result == {"project-a", "project-b"}


class TestProjectSelectionChangedDialog:
    """Tests for the confirmation dialog logic in project switching."""

    def test_no_previous_project_proceeds(self, qapp: QApplication) -> None:
        mixin = _create_coord_mixin(qapp)
        mixin._previous_project = None
        mixin._all_jobs = []
        mixin._context_request_timer = _MockTimer()

        with patch.object(ProjectSwitchConfirmDialog, "exec", return_value=True) as mock_exec:
            mixin._on_project_selection_changed("new-project")
            mock_exec.assert_not_called()

        assert mixin._previous_project == "new-project"
        assert mixin._context_request_timer.started
        assert mixin._context_request_timer._interval == 0

    def test_same_project_proceeds(self, qapp: QApplication) -> None:
        mixin = _create_coord_mixin(qapp)
        mixin._previous_project = "same-project"
        mixin._project_combo.setCurrentText("same-project")
        mixin._all_jobs = []
        mixin._context_request_timer = _MockTimer()

        with patch.object(ProjectSwitchConfirmDialog, "exec", return_value=True) as mock_exec:
            mixin._on_project_selection_changed("same-project")
            mock_exec.assert_not_called()

        assert mixin._context_request_timer.started

    def test_no_active_jobs_proceeds(self, qapp: QApplication) -> None:
        mixin = _create_coord_mixin(qapp)
        mixin._previous_project = "old-project"
        mixin._all_jobs = [
            _make_job("job-1", "old-project", DesktopJobState.SUCCEEDED),
            _make_job("job-2", "old-project", DesktopJobState.FAILED),
        ]
        mixin._context_request_timer = _MockTimer()

        with patch.object(ProjectSwitchConfirmDialog, "exec", return_value=True) as mock_exec:
            mixin._on_project_selection_changed("new-project")
            mock_exec.assert_not_called()

        assert mixin._context_request_timer.started

    def test_all_jobs_none_proceeds(self, qapp: QApplication) -> None:
        mixin = _create_coord_mixin(qapp)
        mixin._previous_project = "old-project"
        mixin._all_jobs = None
        mixin._context_request_timer = _MockTimer()

        with patch.object(ProjectSwitchConfirmDialog, "exec", return_value=True) as mock_exec:
            mixin._on_project_selection_changed("new-project")
            mock_exec.assert_not_called()

        assert mixin._context_request_timer.started

    def test_running_job_shows_dialog_and_proceeds_on_confirm(self, qapp: QApplication) -> None:
        mixin = _create_coord_mixin(qapp)
        mixin._previous_project = "old-project"
        mixin._all_jobs = [
            _make_job("job-1", "old-project", DesktopJobState.RUNNING, chapter_number=5),
        ]
        mixin._context_request_timer = _MockTimer()

        mock_dialog = MagicMock()
        mock_dialog.exec.return_value = True

        with patch.object(ProjectSwitchConfirmDialog, "__init__", lambda self, **kwargs: None):
            with patch.object(ProjectSwitchConfirmDialog, "exec", mock_dialog.exec):
                mixin._on_project_selection_changed("new-project")

        assert mock_dialog.exec.called
        assert mixin._context_request_timer.started

    def test_queued_job_shows_dialog_and_proceeds_on_confirm(self, qapp: QApplication) -> None:
        mixin = _create_coord_mixin(qapp)
        mixin._previous_project = "old-project"
        mixin._all_jobs = [
            _make_job("job-1", "old-project", DesktopJobState.QUEUED, chapter_number=3),
        ]
        mixin._context_request_timer = _MockTimer()

        mock_dialog = MagicMock()
        mock_dialog.exec.return_value = True

        with patch.object(ProjectSwitchConfirmDialog, "__init__", lambda self, **kwargs: None):
            with patch.object(ProjectSwitchConfirmDialog, "exec", mock_dialog.exec):
                mixin._on_project_selection_changed("new-project")

        assert mock_dialog.exec.called
        assert mixin._context_request_timer.started


class TestProjectSelectionStateRestoration:
    """Tests for state restoration when dialog is canceled."""

    def test_dialog_cancel_restores_previous_project(self, qapp: QApplication) -> None:
        mixin = _create_coord_mixin(qapp)
        mixin._previous_project = "old-project"
        mixin._project_combo.setCurrentText("old-project")
        mixin._all_jobs = [
            _make_job("job-1", "old-project", DesktopJobState.RUNNING, chapter_number=5),
        ]
        mixin._context_request_timer = _MockTimer()

        mock_dialog = MagicMock()
        mock_dialog.exec.return_value = False

        with patch.object(ProjectSwitchConfirmDialog, "__init__", lambda self, **kwargs: None):
            with patch.object(ProjectSwitchConfirmDialog, "exec", mock_dialog.exec):
                mixin._on_project_selection_changed("new-project")

        assert mixin._project_combo.currentText() == "old-project"
        assert mixin._previous_project == "old-project"
        assert not mixin._context_request_timer.started

    def test_dialog_cancel_uses_block_signals(self, qapp: QApplication) -> None:
        mixin = _create_coord_mixin(qapp)
        mixin._previous_project = "old-project"
        mixin._project_combo.setCurrentText("old-project")
        mixin._all_jobs = [
            _make_job("job-1", "old-project", DesktopJobState.RUNNING, chapter_number=5),
        ]
        mixin._context_request_timer = _MockTimer()

        mock_dialog = MagicMock()
        mock_dialog.exec.return_value = False

        with patch.object(ProjectSwitchConfirmDialog, "__init__", lambda self, **kwargs: None):
            with patch.object(ProjectSwitchConfirmDialog, "exec", mock_dialog.exec):
                mixin._on_project_selection_changed("new-project")

        assert mixin._project_combo.blockSignals_calls >= 2

    def test_dialog_confirm_proceeds(self, qapp: QApplication) -> None:
        mixin = _create_coord_mixin(qapp)
        mixin._previous_project = "old-project"
        mixin._all_jobs = [
            _make_job("job-1", "old-project", DesktopJobState.RUNNING, chapter_number=5),
        ]
        mixin._context_request_timer = _MockTimer()

        mock_dialog = MagicMock()
        mock_dialog.exec.return_value = True

        with patch.object(ProjectSwitchConfirmDialog, "__init__", lambda self, **kwargs: None):
            with patch.object(ProjectSwitchConfirmDialog, "exec", mock_dialog.exec):
                mixin._on_project_selection_changed("new-project")

        assert mixin._context_request_timer.started

    def test_no_signal_emit_on_cancel_restore(self, qapp: QApplication) -> None:
        mixin = _create_coord_mixin(qapp)
        mixin._previous_project = "old-project"
        mixin._project_combo.setCurrentText("old-project")
        mixin._all_jobs = [
            _make_job("job-1", "old-project", DesktopJobState.RUNNING, chapter_number=5),
        ]
        mixin._context_request_timer = _MockTimer()

        text_changed_signals: list[str] = []
        mixin._project_combo.currentTextChanged.connect(
            lambda t: text_changed_signals.append(t)
        )

        mock_dialog = MagicMock()
        mock_dialog.exec.return_value = False

        with patch.object(ProjectSwitchConfirmDialog, "__init__", lambda self, **kwargs: None):
            with patch.object(ProjectSwitchConfirmDialog, "exec", mock_dialog.exec):
                mixin._on_project_selection_changed("new-project")

        assert text_changed_signals == []

    def test_previous_project_tracks_correctly(self, qapp: QApplication) -> None:
        mixin = _create_coord_mixin(qapp)
        mixin._previous_project = None
        mixin._all_jobs = []
        mixin._context_request_timer = _MockTimer()

        mixin._on_project_selection_changed("project-a")
        assert mixin._previous_project == "project-a"

        mixin._on_project_selection_changed("project-b")
        assert mixin._previous_project == "project-b"


class TestCheckpointDialogMount:
    """Tests checkpoint dialog geometry is based on the full studio page."""

    def test_dialog_uses_page_parent_not_center_column(self, qapp: QApplication) -> None:
        page = QWidget()
        page.resize(1200, 800)
        page._center_wrapper = QWidget(page)  # type: ignore[attr-defined]
        page._center_wrapper.setGeometry(300, 100, 500, 600)  # type: ignore[attr-defined]
        page._notes = QTextEdit(page)  # type: ignore[attr-defined]
        page._checkpoint_dialog = None  # type: ignore[attr-defined]

        def _ignore_option(_option: DecisionOption, _notes: str) -> None:
            return None

        page._on_checkpoint_dialog_option_selected = _ignore_option  # type: ignore[attr-defined]
        page._on_checkpoint_dialog_dismissed = lambda: None  # type: ignore[attr-defined]
        page.show()
        checkpoint = DecisionCheckpoint(
            checkpoint_id="guard-1",
            checkpoint_type="guard_checkpoint",
            summary="守卫检查",
            prompt="请选择处理方式",
            options=[
                DecisionOption(
                    option_id="repair",
                    label="应用修复",
                    description="重新应用修复后再归档",
                    is_recommended=True,
                )
            ],
        )

        ChapterStudioCoordMixin._show_checkpoint_dialog(
            page,  # type: ignore[arg-type]
            checkpoint,
            "章节草稿已完成。",
        )

        dialog = page._checkpoint_dialog  # type: ignore[attr-defined]
        assert dialog is not None
        assert dialog.parent() is page
        assert dialog._free_floating is True  # type: ignore[attr-defined]
        outside_parent = QPoint(-200, -160)
        assert dialog._clamped_position(outside_parent) == outside_parent  # type: ignore[attr-defined]
        assert dialog.width() < page.width()
        assert dialog._panel is not None
        center_width = page._center_wrapper.width()  # type: ignore[attr-defined]
        assert dialog.width() > center_width
        assert dialog._panel.geometry() == dialog.rect()

        dialog.close()
        page.close()


class _MockTimer:
    def __init__(self) -> None:
        self.started = False
        self._interval = 0

    def start(self, interval: int = 0) -> None:
        self.started = True
        self._interval = interval

    def stop(self) -> None:
        self.started = False


class _MockComboBox(QComboBox):
    def __init__(self) -> None:
        super().__init__()
        self.blockSignals_calls = 0
        self._current_text = ""

    def blockSignals(self, block: bool) -> bool:
        self.blockSignals_calls += 1
        return super().blockSignals(block)

    def setCurrentText(self, text: str) -> None:
        self._current_text = text
        super().setCurrentText(text)

    def currentText(self) -> str:
        return self._current_text


class _PureTestCoord(ChapterStudioCoordMixin):
    chapter_context_requested = MagicMock()
    navigate_requested = MagicMock()
    open_project_requested = MagicMock()
    view_project_requested = MagicMock()
    cancel_job_requested = MagicMock()
    context_changed = MagicMock()

    def __init__(self) -> None:
        self._project_combo = MagicMock()
        self._chapter_spin = MagicMock()
        self._previous_project: str | None = None
        self._active_projects: Set[str] = set()
        self._all_jobs: list[DesktopJobRecord] | None = None
        self._studio = None
        self._state = MagicMock()
        self._workspace = None
        self._checkpoint_dialog = None
        self._action_presenter = MagicMock()
        self._context_request_timer = _MockTimer()
        # Auto-pilot attrs (needed by _on_project_selection_changed)
        self._auto_pilot = False
        self._auto_started = False

    def _stop_auto_pilot(self, cancel_jobs: bool = False) -> None:
        pass

    def window(self) -> None:
        return None


class _QtTestCoord(ChapterStudioCoordMixin):
    chapter_context_requested = MagicMock()
    navigate_requested = MagicMock()
    open_project_requested = MagicMock()
    view_project_requested = MagicMock()
    cancel_job_requested = MagicMock()
    context_changed = MagicMock()

    def __init__(self, app: QApplication) -> None:
        self._project_combo = _MockComboBox()
        self._chapter_spin = QSpinBox()
        self._previous_project: str | None = None
        self._active_projects: Set[str] = set()
        self._all_jobs: list[DesktopJobRecord] | None = None
        self._studio = None
        self._state = MagicMock()
        self._workspace = None
        self._checkpoint_dialog = None
        self._action_presenter = MagicMock()
        self._context_request_timer = _MockTimer()
        self._project_status_dot = MagicMock()
        # Auto-pilot attrs (needed by _on_project_selection_changed)
        self._auto_pilot = False
        self._auto_started = False
        # Rotation state (needed by _on_project_selection_changed for edge case 3)
        self._rotation_active_projects: list[str] = []
        self._rotation_index: int = 0
        self._rotation_paused: bool = False

    def _stop_auto_pilot(self, cancel_jobs: bool = False) -> None:
        pass

    def window(self) -> None:
        return None


def _create_pure_coord_mixin() -> ChapterStudioCoordMixin:
    return _PureTestCoord()


def _create_coord_mixin(app: QApplication) -> ChapterStudioCoordMixin:
    return _QtTestCoord(app)
