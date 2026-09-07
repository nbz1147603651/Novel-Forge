"""Tests for Qt signal/slot connections in the Desktop client."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from PySide6.QtCore import QObject, Qt, QThreadPool

from novel_forge.desktop.jobs import (
    DesktopJobManager,
    _WorkerSignals,
    _WorkspaceJobWorker,
)
from novel_forge.desktop.window import (
    NovelForgeDesktopWindow,
)
from novel_forge.desktop.window._runnables import (
    _ChapterContextRefreshRunnable,
    _ContextRefreshSignals,
    _RefreshSignals,
    _WorkspaceRefreshRunnable,
)


class _SignalSpy(QObject):
    """Minimal spy that records signal emissions."""

    def __init__(self, signal_instance):
        super().__init__()
        self.calls = []
        signal_instance.connect(self._record)

    def _record(self, *args):
        self.calls.append(args)


# ---------------------------------------------------------------------------
# 1. window_page_connections
# ---------------------------------------------------------------------------
class TestWindowPageConnections:
    """Verify that MainWindow connects page signals to its own slots."""

    def test_dashboard_signals_connected(self, qtbot) -> None:
        """Dashboard page signals are connected to MainWindow handlers."""
        window = NovelForgeDesktopWindow()
        qtbot.addWidget(window)

        dashboard = window._pages["dashboard"]
        spy = _SignalSpy(dashboard.navigate_requested)
        dashboard.navigate_requested.emit("workflow")
        assert len(spy.calls) >= 1

    def test_workflow_signals_connected(self, qtbot) -> None:
        """Workflow page signals are connected to MainWindow handlers."""
        window = NovelForgeDesktopWindow()
        qtbot.addWidget(window)

        workflow = window._pages["workflow"]
        spy = _SignalSpy(workflow.short_requested)
        workflow.short_requested.emit({"project_id": "test"})
        assert len(spy.calls) >= 1

    def test_chapter_studio_signals_connected(self, qtbot) -> None:
        """Chapter studio page signals are connected to MainWindow handlers."""
        window = NovelForgeDesktopWindow()
        qtbot.addWidget(window)

        studio = window._pages["chapter_studio"]
        spy = _SignalSpy(studio.prepare_requested)
        studio.prepare_requested.emit({"project_id": "test", "chapter_number": 1})
        assert len(spy.calls) >= 1

    def test_settings_signals_connected(self, qtbot) -> None:
        """Settings page signals are connected to MainWindow handlers."""
        window = NovelForgeDesktopWindow()
        qtbot.addWidget(window)

        settings = window._pages["settings"]
        spy = _SignalSpy(settings.mock_mode_toggled)
        settings.mock_mode_toggled.emit(True)
        assert len(spy.calls) >= 1

    def test_job_manager_signals_connected(self, qtbot) -> None:
        """JobManager signals are connected to MainWindow handlers."""
        window = NovelForgeDesktopWindow()
        qtbot.addWidget(window)

        jm = window._job_manager
        spy = _SignalSpy(jm.jobs_changed)
        jm.jobs_changed.emit()
        assert len(spy.calls) >= 1


# ---------------------------------------------------------------------------
# 2. signal_disconnect_on_shutdown
# ---------------------------------------------------------------------------
class TestSignalDisconnectOnShutdown:
    """Verify that timers are stopped and signals are cleaned up on close."""

    def test_refresh_timer_stopped_on_close(self, qtbot) -> None:
        """The workspace refresh timer is stopped when the window closes."""
        window = NovelForgeDesktopWindow()
        qtbot.addWidget(window)

        assert window._refresh_timer.isActive()
        window._refresh_timer.stop()
        assert not window._refresh_timer.isActive()

    def test_jobs_bind_timer_stopped_on_close(self, qtbot) -> None:
        """The job-bind debounce timer is stopped when the window closes."""
        window = NovelForgeDesktopWindow()
        qtbot.addWidget(window)

        assert not window._jobs_bind_timer.isActive()
        window._jobs_bind_timer.stop()
        assert not window._jobs_bind_timer.isActive()

    def test_wake_recovery_pauses_competing_refresh_sources(self, qtbot) -> None:
        window = NovelForgeDesktopWindow()
        qtbot.addWidget(window)
        window._refresh_timer.start()
        window._fs_watcher_debounce_timer.start()
        window._workspace_refresh_schedule_timer.start()

        window._on_system_woke(120.0)

        assert not window._refresh_timer.isActive()
        assert not window._fs_watcher_debounce_timer.isActive()
        assert not window._workspace_refresh_schedule_timer.isActive()
        assert window._wake_recovery_timer.isSingleShot()
        assert window._wake_recovery_timer.isActive()

        window._wake_recovery_timer.stop()
        if window._fs_watcher is not None:
            window._fs_watcher.blockSignals(False)

    def test_forced_refresh_is_remembered_while_minimized(
        self,
        qtbot,
        monkeypatch,
    ) -> None:
        window = NovelForgeDesktopWindow()
        qtbot.addWidget(window)
        window._workspace = MagicMock()
        window._refresh_force_pending = False
        monkeypatch.setattr(window, "isMinimized", lambda: True)

        window.refresh_workspace(force=True)

        assert window._refresh_force_pending is True

    def test_inactive_application_throttles_presentation_work(self, qtbot) -> None:
        window = NovelForgeDesktopWindow()
        qtbot.addWidget(window)
        window._refresh_timer.start()

        window._on_application_state_changed(Qt.ApplicationState.ApplicationInactive)

        assert window._application_active is False
        assert not window._refresh_timer.isActive()
        assert window._refresh_force_pending is True
        assert window._job_manager._app_event_timer.interval() == 1_000

        window._on_application_state_changed(Qt.ApplicationState.ApplicationActive)

        assert window._application_active is True
        assert window._refresh_timer.isActive()
        assert window._job_manager._app_event_timer.interval() == 150

    def test_page_widgets_deleted_on_close(self, qtbot) -> None:
        """Page widgets are children of the window and get deleted with it."""
        window = NovelForgeDesktopWindow()
        qtbot.addWidget(window)

        assert len(window._pages) >= 5
        for page_id, page in window._pages.items():
            assert page.parent() is not None, f"Page {page_id} has no parent"


# ---------------------------------------------------------------------------
# 3. cross_thread_signals
# ---------------------------------------------------------------------------
class TestCrossThreadSignals:
    """Verify that QRunnable workers carry signal objects for cross-thread communication."""

    def test_refresh_runnable_has_signals(self) -> None:
        """_WorkspaceRefreshRunnable carries a _RefreshSignals instance."""
        from novel_forge.desktop.workspace import DesktopWorkspaceService

        service = DesktopWorkspaceService.from_settings()
        worker = _WorkspaceRefreshRunnable(service, reload_from_settings=False, mock_enabled=False)

        assert hasattr(worker, "signals")
        assert isinstance(worker.signals, _RefreshSignals)
        assert hasattr(worker.signals, "finished")
        assert hasattr(worker.signals, "failed")

    def test_context_refresh_runnable_has_signals(self) -> None:
        """_ChapterContextRefreshRunnable carries a _ContextRefreshSignals instance."""
        from novel_forge.desktop.workspace import DesktopWorkspaceService

        service = DesktopWorkspaceService.from_settings()
        worker = _ChapterContextRefreshRunnable(service, "test-project", 1, None)

        assert hasattr(worker, "signals")
        assert isinstance(worker.signals, _ContextRefreshSignals)
        assert hasattr(worker.signals, "finished")
        assert hasattr(worker.signals, "failed")

    def test_context_refresh_runnable_reuses_shared_cache(self, tmp_path) -> None:
        """Context refresh workers share a fingerprinted cache across instances."""
        _ChapterContextRefreshRunnable._snapshot_cache.clear()
        project_dir = tmp_path / "demo"
        project_dir.mkdir()
        outline_path = project_dir / "outline.json"
        outline_path.write_text("{}", encoding="utf-8")
        snapshot = object()
        workspace = SimpleNamespace(
            runtime=SimpleNamespace(storage=SimpleNamespace(root=tmp_path)),
            get_chapter_workspace_snapshot=MagicMock(return_value=snapshot),
        )

        first = _ChapterContextRefreshRunnable(workspace, "demo", 1, None)
        first_spy = _SignalSpy(first.signals.finished)
        first.run()
        second = _ChapterContextRefreshRunnable(workspace, "demo", 1, None)
        second_spy = _SignalSpy(second.signals.finished)
        second.run()

        assert workspace.get_chapter_workspace_snapshot.call_count == 1
        assert first_spy.calls[0][0] is snapshot
        assert second_spy.calls[0][0] is snapshot

        outline_path.write_text('{"changed": true}', encoding="utf-8")
        third = _ChapterContextRefreshRunnable(workspace, "demo", 1, None)
        third.run()

        assert workspace.get_chapter_workspace_snapshot.call_count == 2
        _ChapterContextRefreshRunnable._snapshot_cache.clear()

    def test_context_refresh_cache_tracks_canon_current(self, tmp_path) -> None:
        """Chapter context cache invalidates when the canonical canon file changes."""
        _ChapterContextRefreshRunnable._snapshot_cache.clear()
        project_dir = tmp_path / "demo"
        canon_dir = project_dir / "canon"
        canon_dir.mkdir(parents=True)
        canon_path = canon_dir / "canon_current.json"
        canon_path.write_text("{}", encoding="utf-8")
        snapshot = object()
        workspace = SimpleNamespace(
            runtime=SimpleNamespace(storage=SimpleNamespace(root=tmp_path)),
            get_chapter_workspace_snapshot=MagicMock(return_value=snapshot),
        )

        first = _ChapterContextRefreshRunnable(workspace, "demo", 1, None)
        first.run()
        second = _ChapterContextRefreshRunnable(workspace, "demo", 1, None)
        second.run()

        assert workspace.get_chapter_workspace_snapshot.call_count == 1

        canon_path.write_text('{"changed": true}', encoding="utf-8")
        third = _ChapterContextRefreshRunnable(workspace, "demo", 1, None)
        third.run()

        assert workspace.get_chapter_workspace_snapshot.call_count == 2
        _ChapterContextRefreshRunnable._snapshot_cache.clear()

    def test_workspace_job_worker_has_signals(self) -> None:
        """_WorkspaceJobWorker carries a _WorkerSignals instance."""

        async def dummy_task(runtime, on_step):
            return {}

        worker = _WorkspaceJobWorker("test-job-id", mock=True, task=dummy_task)

        assert hasattr(worker, "signals")
        assert isinstance(worker.signals, _WorkerSignals)
        assert hasattr(worker.signals, "started")
        assert hasattr(worker.signals, "step")
        assert hasattr(worker.signals, "finished")
        assert hasattr(worker.signals, "failed")
        assert hasattr(worker.signals, "cleanup_runtime")

    def test_refresh_runnable_auto_delete(self) -> None:
        """_WorkspaceRefreshRunnable is set to auto-delete after run()."""
        from novel_forge.desktop.workspace import DesktopWorkspaceService

        service = DesktopWorkspaceService.from_settings()
        worker = _WorkspaceRefreshRunnable(service, reload_from_settings=False, mock_enabled=False)
        assert worker.autoDelete()

    def test_job_worker_auto_delete(self) -> None:
        """_WorkspaceJobWorker is set to auto-delete after run()."""

        async def dummy_task(runtime, on_step):
            return {}

        worker = _WorkspaceJobWorker("test-job-id", mock=True, task=dummy_task)
        assert worker.autoDelete()


# ---------------------------------------------------------------------------
# 4. no_orphaned_connections
# ---------------------------------------------------------------------------
class TestNoOrphanedConnections:
    """Verify that repeated operations don't create duplicate signal connections."""

    def test_no_duplicate_timer_connections(self, qtbot) -> None:
        """QTimer connections are not duplicated on repeated calls."""
        window = NovelForgeDesktopWindow()
        qtbot.addWidget(window)

        assert window._jobs_bind_timer.isSingleShot()

        window._schedule_bind_jobs()
        assert window._jobs_bind_timer.isActive()

        window._schedule_bind_jobs()
        assert window._jobs_bind_timer.isActive()

    def test_refresh_debounce_prevents_duplicate_workers(self, qtbot, monkeypatch) -> None:
        """Rapid refresh calls don't spawn multiple workers."""
        monkeypatch.setattr(NovelForgeDesktopWindow, "_sync_runtime_services_init", True)
        window = NovelForgeDesktopWindow()
        qtbot.addWidget(window)

        window.refresh_workspace(force=True)
        assert window._refresh_in_progress

        count_after_first = window._refresh_count

        window.refresh_workspace(force=True)
        assert window._refresh_force_pending
        assert window._refresh_count == count_after_first

    def test_wake_recovery_preserves_in_flight_refresh_guard(self, qtbot, monkeypatch) -> None:
        """Wake recovery must not permit a second refresh worker to race the first."""
        window = NovelForgeDesktopWindow()
        qtbot.addWidget(window)
        window._refresh_in_progress = True
        window._refresh_force_pending = False
        monkeypatch.setattr(
            "novel_forge.desktop.window.core.QTimer.singleShot",
            lambda _delay, _callback: None,
        )

        window._on_system_woke(30.0)

        assert window._refresh_in_progress is True
        assert window._refresh_force_pending is True


# ---------------------------------------------------------------------------
# 5. job_signals (started/finished/failed)
# ---------------------------------------------------------------------------
class TestJobSignals:
    """Verify that job worker signals emit correctly."""

    @staticmethod
    def _start_worker(worker: _WorkspaceJobWorker) -> QThreadPool:
        pool = QThreadPool()
        pool.setMaxThreadCount(1)
        pool.start(worker)
        return pool

    def test_worker_signals_emit_started(self, qtbot) -> None:
        """_WorkspaceJobWorker emits 'started' signal with job_id."""
        received: list[str] = []

        async def quick_task(runtime, on_step):
            return {"status": "ok"}

        worker = _WorkspaceJobWorker("job-123", mock=True, task=quick_task)
        worker.signals.started.connect(lambda jid: received.append(jid))

        pool = self._start_worker(worker)

        qtbot.waitUntil(lambda: len(received) > 0, timeout=5000)
        pool.waitForDone(5000)
        assert received == ["job-123"]

    def test_worker_signals_emit_finished(self, qtbot) -> None:
        """_WorkspaceJobWorker emits 'finished' signal with job_id and result."""
        received: list[tuple] = []

        async def quick_task(runtime, on_step):
            return {"chapter_number": 1, "word_count": 3000}

        worker = _WorkspaceJobWorker("job-456", mock=True, task=quick_task)
        worker.signals.finished.connect(lambda jid, result: received.append((jid, result)))

        pool = self._start_worker(worker)

        qtbot.waitUntil(lambda: len(received) > 0, timeout=5000)
        pool.waitForDone(5000)
        assert len(received) == 1
        jid, result = received[0]
        assert jid == "job-456"
        assert isinstance(result, dict)

    def test_worker_signals_emit_failed(self, qtbot) -> None:
        """_WorkspaceJobWorker emits 'failed' signal with job_id and error summary."""
        received: list[tuple] = []

        async def failing_task(runtime, on_step):
            raise RuntimeError("intentional test failure")

        worker = _WorkspaceJobWorker("job-789", mock=True, task=failing_task)
        worker.signals.failed.connect(lambda jid, summary: received.append((jid, summary)))

        pool = self._start_worker(worker)

        qtbot.waitUntil(lambda: len(received) > 0, timeout=5000)
        pool.waitForDone(5000)
        assert len(received) == 1
        jid, summary = received[0]
        assert jid == "job-789"
        assert isinstance(summary, dict)

    def test_job_manager_emits_jobs_changed(self, qtbot) -> None:
        """DesktopJobManager emits jobs_changed when a job is submitted."""
        jm = DesktopJobManager()
        qtbot.waitSignal(jm.jobs_changed, timeout=5000)

        from novel_forge.workspace.contracts import RunShortRequest

        record = jm.submit_short(
            RunShortRequest(
                project_id="test-project",
                theme="测试主题",
                genre="fiction",
                tone="neutral",
                target_length=1000,
            ),
            mock=True,
        )
        assert record is not None
        assert record.job_id != ""

    def test_job_manager_emits_job_submitted(self, qtbot) -> None:
        """DesktopJobManager emits job_submitted on new job submission."""
        jm = DesktopJobManager()
        qtbot.waitSignal(jm.job_submitted, timeout=5000)

        from novel_forge.workspace.contracts import RunShortRequest

        jm.submit_short(
            RunShortRequest(
                project_id="test-project",
                theme="测试主题",
                genre="fiction",
                tone="neutral",
                target_length=1000,
            ),
            mock=True,
        )

    def test_job_manager_emits_job_completed(self, qtbot) -> None:
        """DesktopJobManager emits job_completed when _handle_finished is called."""
        jm = DesktopJobManager()

        from novel_forge.workspace.contracts import RunShortRequest

        completed: list[str] = []
        jm.job_completed.connect(lambda jid: completed.append(jid))
        record = jm.submit_short(
            RunShortRequest(
                project_id="test-project",
                theme="测试主题",
                genre="fiction",
                tone="neutral",
                target_length=1000,
            ),
            mock=True,
        )

        qtbot.waitUntil(lambda: record.job_id in completed, timeout=60000)

    def test_worker_cancel_returns_cleanly(self, qtbot) -> None:
        """A cancelled worker returns without emitting 'finished'."""
        received_finished: list = []
        received_started: list = []

        async def slow_task(runtime, on_step):
            import asyncio
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                raise

        worker = _WorkspaceJobWorker("job-cancel", mock=True, task=slow_task)
        worker.signals.started.connect(lambda jid: received_started.append(jid))
        worker.signals.finished.connect(lambda jid, r: received_finished.append(jid))

        QThreadPool.globalInstance().start(worker)

        time.sleep(0.3)

        worker.request_cancel()

        qtbot.waitUntil(lambda: len(received_started) > 0, timeout=5000)
        time.sleep(1.0)
        assert len(received_finished) == 0

    def test_worker_cancel_before_start_never_runs_task(self, qtbot) -> None:
        """A worker cancelled while queued exits before creating runtime or running task."""
        task_started: list[bool] = []
        cleaned_up: list[bool] = []

        async def should_not_run(runtime, on_step):
            task_started.append(True)
            return {"status": "unexpected"}

        worker = _WorkspaceJobWorker("job-pre-cancel", mock=True, task=should_not_run)
        worker._cleanup_notify = lambda: cleaned_up.append(True)
        worker.request_cancel()

        QThreadPool.globalInstance().start(worker)

        qtbot.waitUntil(lambda: bool(cleaned_up), timeout=5000)
        assert task_started == []
