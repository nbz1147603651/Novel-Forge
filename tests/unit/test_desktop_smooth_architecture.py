"""Focused tests for desktop smoothness architecture contracts."""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import novel_forge.desktop.jobs.manager as jobs_manager_module  # noqa: E402
import novel_forge.desktop.pages.settings.components as settings_components  # noqa: E402
import novel_forge.desktop.window as window_module  # noqa: E402
import novel_forge.desktop.workers.pool_assign as pool_assign_module  # noqa: E402
from novel_forge.desktop.jobs import DesktopJobManager  # noqa: E402
from novel_forge.desktop.window import NovelForgeDesktopWindow  # noqa: E402
from novel_forge.desktop.workspace import (  # noqa: E402
    DesktopWorkspaceMetrics,
    DesktopWorkspaceSnapshot,
    ProviderStatus,
)
from novel_forge.gateway.profiles import ModelProfile  # noqa: E402
from novel_forge.workspace.projects import WorkspaceOverview  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


class _FakePool:
    def __init__(self) -> None:
        self.workers: list[object] = []
        self.clear_count = 0
        self.wait_calls: list[int] = []

    def start(self, worker: object) -> None:
        self.workers.append(worker)

    def clear(self) -> None:
        self.clear_count += 1

    def waitForDone(self, timeout_ms: int = 0) -> bool:
        self.wait_calls.append(timeout_ms)
        return True


class _CancelableWorker:
    def __init__(self) -> None:
        self.cancel_count = 0

    def request_cancel(self) -> None:
        self.cancel_count += 1


class _FakeWorkspaceService:
    def __init__(self, snapshot: DesktopWorkspaceSnapshot) -> None:
        self._snapshot = snapshot

    def build_snapshot(self) -> DesktopWorkspaceSnapshot:
        return self._snapshot


def _fake_pools() -> SimpleNamespace:
    pools = SimpleNamespace(
        job_pool=_FakePool(),
        ui_io_pool=_FakePool(),
        aux_pool=_FakePool(),
    )
    pools.by_name = lambda name: getattr(pools, f"{name}_pool")
    return pools


def _build_snapshot() -> DesktopWorkspaceSnapshot:
    storage_root = Path("/tmp/novel_forge_smooth_architecture")
    overview = WorkspaceOverview(
        storage_root=str(storage_root),
        total_projects=0,
        short_projects=0,
        long_projects=0,
        total_generated_chapters=0,
        providers=["mock"],
        default_provider="mock",
    )
    return DesktopWorkspaceSnapshot(
        storage_root=storage_root,
        default_provider="mock",
        overview=overview,
        metrics=DesktopWorkspaceMetrics(
            total_projects=0,
            total_chapters=0,
            total_words=0,
            configured_providers=1,
        ),
        providers=[
            ProviderStatus(
                provider_id="mock",
                label="Mock",
                ready=True,
                configured=True,
                is_default=True,
                detail="测试",
            )
        ],
        projects=[],
        featured_project=None,
        details={},
    )


def _make_window(
    monkeypatch: pytest.MonkeyPatch,
    pools: SimpleNamespace | None = None,
) -> NovelForgeDesktopWindow:
    active_pools = pools or _fake_pools()
    snapshot = _build_snapshot()
    service = _FakeWorkspaceService(snapshot)
    monkeypatch.setattr(window_module, "desktop_thread_pools", lambda: active_pools)
    monkeypatch.setattr(jobs_manager_module, "desktop_thread_pools", lambda: active_pools)
    monkeypatch.setattr(
        window_module.DesktopWorkspaceService,
        "from_settings",
        classmethod(lambda cls, mock=False: service),
    )
    monkeypatch.setattr(
        window_module.NovelForgeDesktopWindow,
        "_load_ui_session",
        lambda self: None,
    )
    # B2 async init: the fake thread pool does not execute workers, so force
    # synchronous RuntimeServices construction to preserve the pre-B2 contract
    # where _workspace is populated before _make_window returns.
    monkeypatch.setattr(
        window_module.NovelForgeDesktopWindow,
        "_sync_runtime_services_init",
        True,
    )
    window = NovelForgeDesktopWindow()
    window._refresh_timer.stop()
    return window


def _dispose_window(window: NovelForgeDesktopWindow, qapp: QApplication) -> None:
    window._pre_close_cleanup()
    window.hide()
    window.deleteLater()
    qapp.processEvents()


def _drain_events(qapp: QApplication, duration_ms: int = 30) -> None:
    deadline = time.perf_counter() + duration_ms / 1000
    while time.perf_counter() < deadline:
        qapp.processEvents()
        time.sleep(0.001)
    qapp.processEvents()


def test_window_boot_only_instantiates_dashboard(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    window = _make_window(monkeypatch)

    assert {page_id for page_id, _page in window._pages.loaded_items()} == {"dashboard"}

    _dispose_window(window, qapp)


def test_switch_page_lazy_creates_settings_once(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    window = _make_window(monkeypatch)

    window.switch_page("settings")
    assert window._pages.get("settings") is None
    _drain_events(qapp)
    first_settings = window._pages.get("settings")
    window.switch_page("settings")

    assert first_settings is not None
    assert window._pages.get("settings") is first_settings
    assert {page_id for page_id, _page in window._pages.loaded_items()} == {
        "dashboard",
        "settings",
    }
    assert "settings" in window._connected_page_signals

    _dispose_window(window, qapp)


def test_page_prewarm_instantiates_without_binding(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    window = _make_window(monkeypatch)
    bind_calls: list[str] = []
    monkeypatch.setattr(
        window,
        "_bind_workspace_for_page",
        lambda page_id, force=False, sections=None: bind_calls.append(str(page_id)),
    )

    window._prewarm_page("settings")

    assert window._pages.get("settings") is not None
    assert bind_calls == []

    _dispose_window(window, qapp)


def test_switch_page_debounces_session_save(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    window = _make_window(monkeypatch)
    writes: list[str] = []
    window._ui_session_save_timer.stop()
    window._ui_session_save_timer.timeout.disconnect()
    window._ui_session_save_timer.timeout.connect(lambda: writes.append(window._current_page_id()))
    window._ui_session_restored = True

    window.switch_page("settings")

    assert writes == []
    assert window._ui_session_save_timer.isActive()
    _drain_events(qapp, duration_ms=100)
    assert writes == []
    _drain_events(qapp, duration_ms=520)
    assert len(writes) == 1

    _dispose_window(window, qapp)


def test_settings_activate_runs_after_switch_delay(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    window = _make_window(monkeypatch)
    calls: list[str] = []

    window.switch_page("settings")

    assert calls == []
    _drain_events(qapp, duration_ms=40)
    settings_page = window._pages.get("settings")
    assert settings_page is not None
    monkeypatch.setattr(settings_page, "activate", lambda: calls.append("activate"))
    _drain_events(qapp, duration_ms=100)
    assert calls == []
    _drain_events(qapp, duration_ms=360)
    assert calls == ["activate"]

    _dispose_window(window, qapp)


def test_jobs_section_hint_only_schedules_job_binding(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    window = _make_window(monkeypatch)
    refresh_calls: list[bool] = []
    bind_calls: list[str] = []
    monkeypatch.setattr(window, "refresh_workspace", lambda force=True: refresh_calls.append(force))
    monkeypatch.setattr(window, "_schedule_bind_jobs", lambda: bind_calls.append("jobs"))

    window._schedule_workspace_refresh("jobs", project_id="demo", reason="unit")

    assert bind_calls == ["jobs"]
    assert refresh_calls == []

    _dispose_window(window, qapp)


def test_event_section_changed_schedules_refresh_without_stale_binding(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    window = _make_window(monkeypatch)
    refresh_calls: list[bool] = []
    bind_calls: list[str] = []
    monkeypatch.setattr(window, "refresh_workspace", lambda force=True: refresh_calls.append(force))
    monkeypatch.setattr(
        window,
        "_bind_workspace_for_page",
        lambda page_id, force=False, sections=None: bind_calls.append(str(page_id)),
    )

    window._schedule_workspace_refresh("chapters")

    assert bind_calls == []
    assert refresh_calls == []
    assert window._pending_refresh_section_hints == {"details"}
    qapp.processEvents()
    assert refresh_calls == [True]

    _dispose_window(window, qapp)


def test_section_hints_coalesce_into_one_scheduled_refresh(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    window = _make_window(monkeypatch)
    refresh_calls: list[bool] = []
    monkeypatch.setattr(window, "refresh_workspace", lambda force=True: refresh_calls.append(force))

    window._schedule_workspace_refresh("projects", project_id="demo", reason="unit")
    window._schedule_workspace_refresh("chapters", project_id="demo", reason="unit")

    assert refresh_calls == []
    assert window._pending_refresh_section_hints == {"projects", "details"}
    qapp.processEvents()
    assert refresh_calls == [True]

    _dispose_window(window, qapp)


def test_workspace_refresh_uses_ui_io_pool(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    pools = _fake_pools()
    window = _make_window(monkeypatch, pools)

    assert pools.ui_io_pool.workers
    assert window._active_workspace_refresh_worker is pools.ui_io_pool.workers[-1]

    _dispose_window(window, qapp)


def test_job_manager_uses_job_pool_and_cancel_all_keeps_ui_io_pool(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    pools = _fake_pools()
    monkeypatch.setattr(jobs_manager_module, "desktop_thread_pools", lambda: pools)
    manager = DesktopJobManager(load_persisted_history=False)
    worker = _CancelableWorker()
    manager._workers["job-1"] = worker  # type: ignore[assignment]

    manager.request_cancel_all()

    assert manager._thread_pool is pools.job_pool
    assert worker.cancel_count == 1
    assert pools.job_pool.clear_count == 1
    assert pools.ui_io_pool.clear_count == 0

    manager._workers.clear()
    manager.shutdown(wait_ms=0)


def test_job_manager_section_change_emits_qt_signal(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    pools = _fake_pools()
    monkeypatch.setattr(jobs_manager_module, "desktop_thread_pools", lambda: pools)
    manager = DesktopJobManager(load_persisted_history=False)
    emitted: list[tuple[str, str]] = []
    core_events: list[Any] = []
    manager.section_changed.connect(
        lambda project_id, section: emitted.append((project_id, section))
    )
    monkeypatch.setattr(manager, "_publish_event", lambda event: core_events.append(event))

    manager._publish_section_change("demo", "details")

    assert emitted == [("demo", "details")]
    assert core_events and core_events[0].data == {"section": "details"}

    manager.shutdown(wait_ms=0)


def test_connection_probe_uses_aux_thread_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    pools = _fake_pools()
    monkeypatch.setattr(pool_assign_module, "desktop_thread_pools", lambda: pools)
    worker = settings_components._ConnectionTestWorker(
        ModelProfile(
            profile_id="tongyi:qwen-max",
            display_name="Qwen-max",
            provider="tongyi",
            model_id="qwen-max",
            api_key="sk-test",
        )
    )

    worker.start()

    assert pools.aux_pool.workers == [worker]
    assert pools.job_pool.workers == []
    assert pools.ui_io_pool.workers == []
