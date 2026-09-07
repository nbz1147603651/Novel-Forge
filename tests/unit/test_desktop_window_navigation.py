from __future__ import annotations

from types import SimpleNamespace

from novel_forge.desktop import window_jobs, window_navigation
from novel_forge.desktop.jobs import DesktopJobRecord, DesktopJobState
from novel_forge.desktop.window import _navigation as _navigation_impl


class _FakeRegistry:
    def __init__(self, page: object | None = None) -> None:
        self.page = page
        self.connected: list[tuple[str, object]] = []

    def has(self, page_id: str) -> bool:
        return page_id == "workflow" and self.page is not None

    def get(self, page_id: str) -> object | None:
        assert page_id == "workflow"
        return self.page

    def connect_signals(self, page_id: str, owner: object) -> None:
        self.connected.append((page_id, owner))


class _FakeStack:
    def __init__(self) -> None:
        self.widgets: list[object] = []
        self.current: object | None = None

    def indexOf(self, widget: object) -> int:
        try:
            return self.widgets.index(widget)
        except ValueError:
            return -1

    def addWidget(self, widget: object) -> None:
        self.widgets.append(widget)

    def currentWidget(self) -> object | None:
        return self.current

    def setCurrentWidget(self, widget: object) -> None:
        self.current = widget


class _FakePage:
    def __init__(self) -> None:
        self.bound_store: object | None = None
        self.mock_mode: bool | None = None

    def bind_task_observation_store(self, store: object) -> None:
        self.bound_store = store

    def set_mock_mode(self, enabled: bool) -> None:
        self.mock_mode = enabled


def test_ensure_page_creates_binds_and_preserves_mock_mode(monkeypatch) -> None:
    page = _FakePage()
    registry = _FakeRegistry(page)
    monkeypatch.setattr(_navigation_impl, "page_registry", registry)
    bind_workspace_calls: list[tuple[str, bool]] = []
    bind_jobs_calls: list[tuple[str, list[str]]] = []
    store = object()
    owner = SimpleNamespace(
        _pages={},
        _page_placeholders={},
        _stack=_FakeStack(),
        _page_workspace_revision={},
        _page_signal_connections_ready=False,
        _snapshot=object(),
        _latest_jobs=["job"],
        _task_observation_store=store,
        _mock_enabled=True,
        _bind_workspace_for_page=lambda page_id, *, force: bind_workspace_calls.append(
            (page_id, force)
        ),
        _bind_jobs_for_page=lambda page_id, jobs: bind_jobs_calls.append((page_id, jobs)),
    )

    created = window_navigation.ensure_page(owner, "workflow")

    assert created is page
    assert owner._pages["workflow"] is page
    assert owner._stack.widgets == [page]
    assert owner._page_workspace_revision["workflow"] == -1
    assert bind_workspace_calls == [("workflow", False)]
    assert bind_jobs_calls == [("workflow", ["job"])]
    assert page.bound_store is store
    assert page.mock_mode is True


class _FakeButton:
    def __init__(self) -> None:
        self.checked: list[bool] = []
        self.active: list[bool] = []

    def setChecked(self, value: bool) -> None:
        self.checked.append(value)

    def set_active(self, value: bool) -> None:
        self.active.append(value)


def test_switch_page_updates_navigation_state_and_preserves_deep_link() -> None:
    old_page = object()
    new_page = object()
    stack = _FakeStack()
    stack.current = old_page
    post_switch: list[tuple[str, int, str | None]] = []
    applied_meta: list[str] = []
    animated_indicator: list[str] = []
    owner = SimpleNamespace(
        _pages={"dashboard": old_page, "projects": new_page},
        _active_page_id="dashboard",
        _last_switch_page_id="dashboard",
        _nav_buttons={"dashboard": _FakeButton(), "projects": _FakeButton()},
        _stack=stack,
        _COLD_INSTANT_PAGE_IDS=frozenset(),
        _page_placeholders={},
        _page_activate_generation=4,
        _ui_session_restored=False,
        _PREWARM_STEP_DELAY_MS=350,
        _schedule_idle_page_prewarm=lambda: None,
        _ensure_page=lambda page_id, *, bind_workspace, bind_jobs: new_page,
        _apply_page_meta=lambda page_id: applied_meta.append(page_id),
        _animate_top_bar_title=lambda: None,
        _animate_current_page=lambda: None,
        _animate_active_indicator=lambda page_id: animated_indicator.append(page_id),
        _enter_mac_fullscreen_safe_mode=lambda: False,
        _exit_mac_fullscreen_safe_mode=lambda page_id, generation: None,
        _schedule_post_switch_work=lambda page_id, generation, focus_tab: post_switch.append(
            (page_id, generation, focus_tab)
        ),
        _schedule_ui_session_save=lambda: None,
        _schedule_deferred_page_creation=lambda page_id, generation, focus_tab: None,
    )

    window_navigation.switch_page(owner, "projects:relationships:demo")

    assert owner._previous_widget is old_page
    assert owner._previous_page_id == "dashboard"
    assert owner._active_page_id == "projects"
    assert owner._last_switch_page_id == "projects"
    assert owner._page_activate_generation == 5
    assert stack.current is new_page
    assert applied_meta == ["projects"]
    assert animated_indicator == ["projects"]
    assert post_switch == [("projects", 5, "relationships:demo")]
    assert owner._nav_buttons["dashboard"].active == [False]
    assert owner._nav_buttons["projects"].active == [True]


class _FakeSignal:
    def __init__(self) -> None:
        self.connections: list[object] = []

    def connect(self, callback: object) -> None:
        self.connections.append(callback)


def _dashboard_page() -> SimpleNamespace:
    return SimpleNamespace(
        navigate_requested=_FakeSignal(),
        compose_requested=_FakeSignal(),
        view_project_requested=_FakeSignal(),
        open_project_requested=_FakeSignal(),
        delete_requested=_FakeSignal(),
        rebuild_memory_vectors_requested=_FakeSignal(),
        context_changed=_FakeSignal(),
        clear_task_flow_requested=_FakeSignal(),
        task_focus_decision_selected=_FakeSignal(),
        task_focus_expand_requested=_FakeSignal(),
    )


def test_connect_page_signals_is_idempotent_for_known_pages() -> None:
    page = _dashboard_page()
    owner = SimpleNamespace(
        _connected_page_signals=set(),
        switch_page=object(),
        _focus_workflow=object(),
        _view_project_in_reader=object(),
        _open_project_folder=object(),
        _handle_delete_project=object(),
        _submit_rebuild_memory_vectors=object(),
        _refresh_top_actions_for=lambda page_id: None,
        _schedule_ui_session_save=object(),
        _clear_workflow_task_flow=object(),
        _provide_task_decision=object(),
        _open_task_focus_dialog=lambda *, focus_stream_detail: None,
    )

    window_navigation.connect_page_signals(owner, "dashboard", page)
    window_navigation.connect_page_signals(owner, "dashboard", page)

    assert owner._connected_page_signals == {"dashboard"}
    assert page.navigate_requested.connections == [owner.switch_page]
    assert page.compose_requested.connections == [owner._focus_workflow]
    assert len(page.context_changed.connections) == 2


def test_window_job_helpers_parse_and_select_chapter_jobs() -> None:
    running = DesktopJobRecord(
        job_id="run",
        kind="run_chapter",
        label="章节续写 · demo / 第 4 章",
        project_id="demo",
        status=DesktopJobState.RUNNING,
    )
    queued = DesktopJobRecord(
        job_id="queue",
        kind="prepare_chapter",
        label="章节方案 · demo / 第 5 章",
        project_id="demo",
        status=DesktopJobState.QUEUED,
        result={"chapter_number": "5"},
    )
    other = DesktopJobRecord(
        job_id="other",
        kind="export_book",
        label="导出书稿 · demo",
        project_id="demo",
        status=DesktopJobState.RUNNING,
    )

    assert window_jobs.job_chapter_number(running) == 4
    assert window_jobs.job_chapter_number(queued) == 5
    assert (
        window_jobs.active_write_job_for_project(
            jobs=[other, running, queued],
            project_id="demo",
        )
        is running
    )
    assert (
        window_jobs.latest_chapter_job(
            jobs=[other, running, queued],
            project_id="demo",
            chapter_number=5,
        )
        is queued
    )


def test_window_jobs_bind_jobs_updates_active_and_background_chapter_studio() -> None:
    job = DesktopJobRecord(
        job_id="run",
        kind="run_chapter",
        label="章节续写 · demo / 第 3 章",
        project_id="demo",
        status=DesktopJobState.RUNNING,
    )
    active_page_jobs: list[list[DesktopJobRecord]] = []
    studio_jobs: list[list[DesktopJobRecord]] = []
    ingested: list[list[DesktopJobRecord]] = []
    autorun_calls: list[bool] = []
    status_calls: list[list[DesktopJobRecord]] = []
    owner = SimpleNamespace(
        _latest_jobs=[],
        _pages={
            "dashboard": object(),
            "chapter_studio": SimpleNamespace(
                needs_job_binding=lambda: True,
                bind_jobs=lambda jobs: studio_jobs.append(jobs),
            ),
        },
        _job_manager=SimpleNamespace(jobs=lambda: [job]),
        _task_observation_store=SimpleNamespace(
            ingest_jobs=lambda jobs: ingested.append(jobs)
        ),
        _window_callbacks_allowed=lambda: True,
        _current_page_id=lambda: "dashboard",
        _bind_jobs_for_page=lambda page_id, jobs: active_page_jobs.append(jobs),
        _drive_active_autoruns=lambda: autorun_calls.append(True),
        _update_status_bar_labels=lambda jobs: status_calls.append(jobs),
    )

    window_jobs.bind_jobs(owner)

    assert owner._latest_jobs == [job]
    assert ingested == [[job]]
    assert active_page_jobs == [[job]]
    assert studio_jobs == [[job]]
    assert autorun_calls == [True]
    assert status_calls == [[job]]


def test_window_jobs_decision_submission_updates_observation_store() -> None:
    decisions: list[tuple[str, str, str]] = []
    messages: list[tuple[str, int, object]] = []
    owner = SimpleNamespace(
        _job_manager=SimpleNamespace(
            provide_decision=lambda job_id, decision_id, choice, *, custom_text="": True
        ),
        _task_observation_store=SimpleNamespace(
            mark_decision_submitted=lambda job_id, decision_id, choice: decisions.append(
                (job_id, decision_id, choice)
            )
        ),
        _STATUS_SUCCESS=object(),
        _STATUS_WARNING=object(),
        show_priority_status=lambda msg, ms, priority: messages.append((msg, ms, priority)),
    )

    window_jobs.provide_task_decision(owner, "job", "decision", "accept")

    assert decisions == [("job", "decision", "accept")]
    assert messages[0][0] == "确认已提交，任务继续执行"
