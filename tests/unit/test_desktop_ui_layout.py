"""UI regression tests for desktop dashboard/projects layout roles."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QWidget,
)

import novel_forge.desktop.window as window_module  # noqa: E402
from novel_forge.desktop.jobs import DesktopJobRecord, DesktopJobState  # noqa: E402
from novel_forge.desktop.pages import chapter_studio_actions as chapter_actions_module  # noqa: E402
from novel_forge.desktop.pages import dashboard_page as dashboard_module  # noqa: E402
from novel_forge.desktop.pages.chapter_studio.coord import (  # noqa: E402
    _TASK_FLOW_MIN_HEIGHT,
    _WORKBENCH_ARTIFACT_PANEL_MIN_HEIGHT,
    _WORKBENCH_ARTIFACT_TABS_MIN_HEIGHT,
)
from novel_forge.desktop.pages.chapter_studio.page import ChapterStudioPage  # noqa: E402
from novel_forge.desktop.pages.settings.page import SettingsPage  # noqa: E402
from novel_forge.desktop.pages.standalone.dashboard_page import DashboardPage  # noqa: E402
from novel_forge.desktop.pages.standalone.outline_editor import (
    InteractiveOutlineWidget,  # noqa: E402
)
from novel_forge.desktop.pages.standalone.projects_page import ProjectsPage  # noqa: E402
from novel_forge.desktop.pages.workflow.page import WorkflowPage  # noqa: E402
from novel_forge.desktop.window import NovelForgeDesktopWindow  # noqa: E402
from novel_forge.desktop.workspace import (  # noqa: E402
    DesktopProjectItem,
    DesktopWorkspaceMetrics,
    DesktopWorkspaceSnapshot,
    ProviderStatus,
)
from novel_forge.workspace.contracts import (  # noqa: E402
    ChapterWorkspaceChapter,
    ChapterWorkspaceSnapshot,
    InitLongRequest,
    RebuildMemoryVectorsRequest,
)
from novel_forge.workspace.projects import (  # noqa: E402
    ChapterSummary,
    ProjectDetail,
    WorkspaceOverview,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def _build_snapshot(
    *, with_project: bool, resumable_init: bool = False
) -> DesktopWorkspaceSnapshot:
    storage_root = Path("/tmp/novel_forge_ui")
    providers = [
        ProviderStatus(
            provider_id="mock",
            label="Mock",
            ready=True,
            configured=True,
            is_default=True,
            detail="当前已载入",
        ),
        ProviderStatus(
            provider_id="openai",
            label="OpenAI",
            ready=False,
            configured=False,
            is_default=False,
            detail="尚未配置或不可用",
        ),
    ]
    if not with_project:
        overview = WorkspaceOverview(
            storage_root=str(storage_root),
            total_projects=0,
            short_projects=0,
            long_projects=0,
            total_generated_chapters=0,
            providers=["mock"],
            default_provider="mock",
        )
        metrics = DesktopWorkspaceMetrics(
            total_projects=0,
            total_chapters=0,
            total_words=0,
            configured_providers=1,
        )
        return DesktopWorkspaceSnapshot(
            storage_root=storage_root,
            default_provider="mock",
            overview=overview,
            metrics=metrics,
            providers=providers,
            projects=[],
            featured_project=None,
            details={},
        )

    project = DesktopProjectItem(
        project_id="long_demo",
        title="遗物人生",
        mode="long",
        mode_label="长篇",
        status="planning" if resumable_init else "writing",
        status_label="待续立项" if resumable_init else "连载中",
        progress_label="大纲已安全保存到第30章（30/36章）" if resumable_init else "1/12",
        progress_percent=88 if resumable_init else 15,
        last_updated_label="2026-03-13 09:30",
        headline="一枚旧怀表牵出一段被尘封的人生暗线。",
        next_action="继续立项，从第 31 章大纲接着写" if resumable_init else "续写第 2 章",
        genre="literary",
        tone="warm",
        completed_chapters=0 if resumable_init else 1,
        total_chapters=36 if resumable_init else 12,
        next_chapter=1 if resumable_init else 2,
        has_outline=True,
        has_canon=False if resumable_init else True,
        init_resume_available=resumable_init,
        init_resume_step_label="章节大纲" if resumable_init else "",
    )
    detail = ProjectDetail(
        project_id="long_demo",
        mode="long",
        title="遗物人生",
        genre="literary",
        tone="warm",
        preview="怀表盖启处，一声轻叹将旧事牵开。",
        total_chapters=36 if resumable_init else 12,
        completed_chapters=0 if resumable_init else 1,
        latest_chapter=None if resumable_init else 1,
        completion_ratio=0.0 if resumable_init else 0.083,
        has_outline=True,
        has_canon=False if resumable_init else True,
        updated_at="2026-03-13T09:30:00+00:00",
        language="zh",
        words_per_chapter=4500,
        volume_mode="off",
        premise="一枚旧怀表牵出一段被尘封的人生暗线。",
        characters_hint="遗物整理师、被覆盖身份的高层",
        world_hint="近未来都市",
        conflict_hint="越靠近真相，人生越会被重写",
        pov_hint="女主主视角",
        opening_style="高概念开场",
        ending_style="余韵式 HE",
        extra_instructions="强化情绪拉扯",
        init_resume_available=resumable_init,
        init_resume_step="plan_outline" if resumable_init else "",
        init_resume_step_label="章节大纲" if resumable_init else "",
        init_resume_progress_label="大纲已安全保存到第30章（30/36章）" if resumable_init else "",
        init_resume_progress_percent=88 if resumable_init else 0,
        init_resume_next_chapter=31 if resumable_init else None,
        chapters=[]
        if resumable_init
        else [
            ChapterSummary(
                chapter_number=1,
                title="旧怀表",
                word_count=3210,
                updated_at="2026-03-13T09:30:00+00:00",
                preview="怀表盖弹开时，屋里响起极轻的一声叹息。",
            )
        ],
        recent_files=[
            "chapters/chapter_001.md",
            "reports/chapter_001_eval.json",
            "plans/narrative_blueprint.json",
        ],
        artifact_counts={
            "chapters": 0 if resumable_init else 1,
            "drafts": 1,
            "reports": 1,
            "plans": 1,
            "states": 1,
        },
    )
    overview = WorkspaceOverview(
        storage_root=str(storage_root),
        total_projects=1,
        short_projects=0,
        long_projects=1,
        total_generated_chapters=0 if resumable_init else 1,
        providers=["mock"],
        default_provider="mock",
    )
    metrics = DesktopWorkspaceMetrics(
        total_projects=1,
        total_chapters=0 if resumable_init else 1,
        total_words=0 if resumable_init else 3210,
        configured_providers=1,
    )
    return DesktopWorkspaceSnapshot(
        storage_root=storage_root,
        default_provider="mock",
        overview=overview,
        metrics=metrics,
        providers=providers,
        projects=[project],
        featured_project=project,
        details={"long_demo": detail},
    )


def _build_chapter_studio_snapshot(
    *,
    project_id: str = "long_demo",
    chapter_number: int = 2,
) -> ChapterWorkspaceSnapshot:
    return ChapterWorkspaceSnapshot(
        project_id=project_id,
        project_title="遗物人生",
        chapter_number=chapter_number,
        total_chapters=12,
        chapters=[
            ChapterWorkspaceChapter(chapter_number=1, title="旧怀表", status="done"),
            ChapterWorkspaceChapter(
                chapter_number=chapter_number,
                title=f"第 {chapter_number} 章",
                status="current",
            ),
        ],
        current_title=f"第 {chapter_number} 章",
    )


def test_chapter_studio_primary_action_on_done_chapter_goes_next_not_prepare(
    qapp: QApplication,
) -> None:
    page = ChapterStudioPage()
    try:
        snapshot = ChapterWorkspaceSnapshot(
            project_id="long_demo",
            project_title="遗物人生",
            chapter_number=2,
            total_chapters=3,
            chapters=[
                ChapterWorkspaceChapter(chapter_number=1, title="旧怀表", status="done"),
                ChapterWorkspaceChapter(chapter_number=2, title="回声", status="done"),
                ChapterWorkspaceChapter(chapter_number=3, title="新章", status="pending"),
            ],
            current_title="回声",
        )
        submitted: list[object] = []
        page.prepare_requested.connect(lambda request: submitted.append(request))

        page.bind_workspace(_build_snapshot(with_project=True))
        page.focus_project("long_demo", 2)
        page.bind_studio(snapshot)
        qapp.processEvents()

        assert page.primary_action_label() == "前往第 3 章 →"

        page.trigger_primary_action()
        qapp.processEvents()

        assert submitted == []
        assert page.current_chapter_number() == 3
    finally:
        page.shutdown()
        page.hide()
        page.deleteLater()
        qapp.processEvents()


def test_chapter_studio_submit_prepare_blocks_archived_chapter_on_disk(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = ChapterStudioPage()
    try:
        project_dir = tmp_path / "long_demo"
        (project_dir / "chapters").mkdir(parents=True)
        (project_dir / "canon").mkdir(parents=True)
        (project_dir / "chapters" / "chapter_001.md").write_text("正文", encoding="utf-8")
        (project_dir / "canon" / "canon_current.json").write_text(
            json.dumps({"project_id": "long_demo", "current_chapter": 1}, ensure_ascii=False),
            encoding="utf-8",
        )

        workspace = replace(_build_snapshot(with_project=True), storage_root=tmp_path)
        stale_snapshot = ChapterWorkspaceSnapshot(
            project_id="long_demo",
            project_title="遗物人生",
            chapter_number=1,
            total_chapters=3,
            chapters=[
                ChapterWorkspaceChapter(chapter_number=1, title="旧怀表", status="pending"),
                ChapterWorkspaceChapter(chapter_number=2, title="回声", status="pending"),
            ],
            current_title="旧怀表",
        )
        submitted: list[object] = []
        warnings: list[tuple[str, str]] = []
        monkeypatch.setattr(
            chapter_actions_module,
            "show_warning_message",
            lambda _parent, title, message: warnings.append((title, message)),
        )
        page.prepare_requested.connect(lambda request: submitted.append(request))

        page.bind_workspace(workspace)
        page.focus_project("long_demo", 1)
        page.bind_studio(stale_snapshot)
        qapp.processEvents()

        page._submit_prepare()

        assert submitted == []
        assert warnings
        assert warnings[0][0] == "章节已归档"
        assert "第 1 章已经归档" in warnings[0][1]
    finally:
        page.shutdown()
        page.hide()
        page.deleteLater()
        qapp.processEvents()


class _FakeWorkspaceService:
    def __init__(self, snapshot: DesktopWorkspaceSnapshot) -> None:
        self._snapshot = snapshot

    def build_snapshot(self) -> DesktopWorkspaceSnapshot:
        return self._snapshot

    def get_chapter_workspace_snapshot(
        self,
        project_id: str,
        chapter_number: int,
        *,
        project_detail: object | None = None,
    ) -> ChapterWorkspaceSnapshot:
        return _build_chapter_studio_snapshot(
            project_id=project_id,
            chapter_number=chapter_number,
        )

    def delete_project(self, project_id: str) -> bool:
        return True


class _CapturingThreadPool:
    def __init__(self) -> None:
        self.workers: list[object] = []

    def start(self, worker: object) -> None:
        self.workers.append(worker)

    def waitForDone(self, timeout_ms: int = 0) -> bool:
        return True


def _fake_thread_pools(ui_io_pool: _CapturingThreadPool) -> SimpleNamespace:
    return SimpleNamespace(
        job_pool=_CapturingThreadPool(),
        ui_io_pool=ui_io_pool,
        aux_pool=_CapturingThreadPool(),
    )


def _emit_workspace_refreshed(
    window: NovelForgeDesktopWindow,
    snapshot: DesktopWorkspaceSnapshot,
    service: _FakeWorkspaceService,
    *,
    section_hints: set[str] | None = None,
) -> None:
    payload = window._build_snapshot_payload(snapshot)
    section_hashes, changed_sections, snapshot_hash = window._compute_section_hashes(
        snapshot,
        payload,
        window._last_snapshot_payload,
        window._section_hash_cache,
    )
    if section_hints:
        window._refresh_section_hints_current = set(section_hints)
    window._on_workspace_refreshed(
        snapshot,
        service,
        payload,
        section_hashes,
        changed_sections,
        snapshot_hash,
    )


def _make_window(
    monkeypatch: pytest.MonkeyPatch,
    snapshot: DesktopWorkspaceSnapshot,
) -> NovelForgeDesktopWindow:
    service = _FakeWorkspaceService(snapshot)
    monkeypatch.setattr(
        window_module.DesktopWorkspaceService,
        "from_settings",
        classmethod(lambda cls, mock=False: service),
    )
    # Prevent real UI session file from interfering with test expectations
    monkeypatch.setattr(
        window_module.NovelForgeDesktopWindow,
        "_load_ui_session",
        lambda self: None,
    )
    # B2 async init: force synchronous RuntimeServices construction so the
    # fake workspace lands before _make_window returns.
    monkeypatch.setattr(
        window_module.NovelForgeDesktopWindow,
        "_sync_runtime_services_init",
        True,
    )
    window = NovelForgeDesktopWindow()
    window._refresh_timer.stop()
    # Wait for the initial async workspace-refresh QRunnable to complete, then
    # deliver its queued signal so _on_workspace_refreshed runs synchronously
    # before any test assertion runs.
    _drain_qt_workers(QApplication.instance())
    return window


def _dispose_window(window: NovelForgeDesktopWindow, qapp: QApplication) -> None:
    window._pre_close_cleanup()
    window.hide()
    window.deleteLater()
    qapp.processEvents()


def _drain_qt_workers(qapp: QApplication, *, timeout_ms: int = 2000) -> None:
    # A workspace refresh can enqueue a follow-up chapter-context refresh from
    # its finished signal. Drain twice so tests observe the complete chain.
    pools = window_module.desktop_thread_pools()
    for _ in range(2):
        pools.ui_io_pool.waitForDone(timeout_ms)
        pools.aux_pool.waitForDone(timeout_ms)
        qapp.processEvents()


def _wait_for_qt_condition(
    qapp: QApplication,
    predicate: Callable[[], bool],
    *,
    timeout_ms: int = 1200,
) -> bool:
    deadline = time.perf_counter() + timeout_ms / 1000
    while time.perf_counter() < deadline:
        _drain_qt_workers(qapp, timeout_ms=200)
        if predicate():
            return True
        time.sleep(0.01)
        qapp.processEvents()
    return bool(predicate())


def _drain_deferred_switch(qapp: QApplication, *, duration_ms: int = 320) -> None:
    deadline = time.perf_counter() + duration_ms / 1000
    while time.perf_counter() < deadline:
        qapp.processEvents()
        time.sleep(0.001)
    qapp.processEvents()


def _is_descendant(widget: QWidget, ancestor: QWidget) -> bool:
    current: QWidget | None = widget
    while current is not None:
        if current is ancestor:
            return True
        current = current.parentWidget()
    return False


def test_dashboard_top_bar_continues_featured_project(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    window = _make_window(monkeypatch, _build_snapshot(with_project=True))

    assert window._primary_button.text() == "续此卷 →"
    assert window._secondary_button.text() == "阅卷"

    window._primary_button.click()
    # This window fixture remains hidden, so staged page timers intentionally
    # pause.  Explicit access is the supported synchronous path for tests.
    studio = window._pages["chapter_studio"]
    _drain_deferred_switch(qapp)

    assert window._stack.currentWidget() is studio
    assert studio.current_project_id() == "long_demo"
    _dispose_window(window, qapp)


def test_focus_chapter_studio_clamps_out_of_range_chapter(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    window = _make_window(monkeypatch, _build_snapshot(with_project=True))

    window._focus_chapter_studio("long_demo", 999)
    studio = window._pages["chapter_studio"]
    _drain_deferred_switch(qapp)

    assert studio.current_project_id() == "long_demo"
    assert studio.current_chapter_number() == 12
    _dispose_window(window, qapp)


def test_chapter_studio_clears_deleted_current_project(qapp: QApplication) -> None:
    page = ChapterStudioPage()
    page.bind_workspace(_build_snapshot(with_project=True))
    page.bind_studio(_build_chapter_studio_snapshot())
    qapp.processEvents()

    assert page.current_project_id() == "long_demo"
    assert page._studio is not None

    page.bind_workspace(_build_snapshot(with_project=False))
    qapp.processEvents()

    assert page.current_project_id() == ""
    assert page._studio is None
    assert page._rail._meta_label.text() == "尚未载入项目"
    assert not page._clean_stale_btn.isVisible()


def test_chapter_studio_artifacts_start_larger_than_task_flow(qapp: QApplication) -> None:
    page = ChapterStudioPage()
    try:
        assert page._artifact_tabs.minimumHeight() == _WORKBENCH_ARTIFACT_TABS_MIN_HEIGHT
        assert page._artifact_tabs.minimumHeight() > _TASK_FLOW_MIN_HEIGHT
        assert page._artifact_tabs.parentWidget() is not None
        assert (
            page._artifact_tabs.parentWidget().minimumHeight()
            == _WORKBENCH_ARTIFACT_PANEL_MIN_HEIGHT
        )
    finally:
        page.shutdown()
        page.hide()
        page.deleteLater()
        qapp.processEvents()


def test_window_delete_project_clears_chapter_studio_selection(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    class _DeletingWorkspaceService(_FakeWorkspaceService):
        def __init__(self) -> None:
            super().__init__(_build_snapshot(with_project=True))
            self.deleted = False

        def build_snapshot(self) -> DesktopWorkspaceSnapshot:
            return _build_snapshot(with_project=False) if self.deleted else self._snapshot

        def delete_project(self, project_id: str) -> bool:
            self.deleted = project_id == "long_demo"
            return self.deleted

    service = _DeletingWorkspaceService()
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
    monkeypatch.setattr(
        window_module.NovelForgeDesktopWindow,
        "_sync_runtime_services_init",
        True,
    )
    window = NovelForgeDesktopWindow()
    window._refresh_timer.stop()
    _drain_qt_workers(qapp)

    window._focus_chapter_studio("long_demo", 2)
    qapp.processEvents()
    assert window._pages["chapter_studio"].current_project_id() == "long_demo"

    window._handle_delete_project("long_demo")
    _drain_qt_workers(qapp)

    studio = window._pages["chapter_studio"]
    assert window._chapter_studio_project_id == ""
    assert studio.current_project_id() == ""
    assert studio._studio is None
    assert _wait_for_qt_condition(qapp, lambda: "项目 0" in window._top_meta.text())
    assert "项目 0" in window._top_meta.text()

    _dispose_window(window, qapp)


def test_dashboard_and_projects_top_bar_drop_open_workspace(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    window = _make_window(monkeypatch, _build_snapshot(with_project=False))

    assert window._primary_button.text() == "起笔 →"
    assert window._secondary_button.text() == "去火候"
    assert "打开工作区" not in {window._primary_button.text(), window._secondary_button.text()}

    window._primary_button.click()
    _drain_deferred_switch(qapp)
    assert window._stack.currentWidget() is window._pages["workflow"]
    assert window._pages["workflow"]._long_panel.chapter_form_project_id() == ""

    window.switch_page("projects")
    _drain_deferred_switch(qapp)
    assert window._primary_button.text() == "返回案头"
    assert window._secondary_button.text() == "去机杼"
    assert "打开工作区" not in {window._primary_button.text(), window._secondary_button.text()}
    _dispose_window(window, qapp)


def test_workflow_chapter_entry_preserves_focused_project(qapp: QApplication) -> None:
    base = _build_snapshot(with_project=True)
    first_project = base.projects[0]
    second_project = replace(
        first_project,
        project_id="long_second",
        title="第二部长篇",
        completed_chapters=4,
        next_chapter=5,
        progress_label="4/12",
    )
    first_detail = base.details["long_demo"]
    second_detail = first_detail.model_copy(
        update={
            "project_id": "long_second",
            "title": "第二部长篇",
            "completed_chapters": 4,
            "latest_chapter": 4,
        }
    )
    snapshot = replace(
        base,
        projects=[first_project, second_project],
        details={"long_demo": first_detail, "long_second": second_detail},
    )
    page = WorkflowPage()
    try:
        page.bind_workspace(snapshot)

        page.focus_project("long_second", 5)
        qapp.processEvents()

        assert page.current_project_id() == "long_second"
        assert page.current_chapter_number() == 5

        page.bind_workspace(snapshot)
        qapp.processEvents()

        assert page.current_project_id() == "long_second"
        assert page.current_chapter_number() == 5
    finally:
        page.shutdown()
        page.hide()
        page.deleteLater()


def test_projects_top_bar_uses_selected_project_actions(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    window = _make_window(monkeypatch, _build_snapshot(with_project=True))

    # Navigate to projects (viewer) via the dashboard’s view_project signal
    window._view_project_in_reader("long_demo")
    _drain_deferred_switch(qapp)

    assert window._primary_button.text() == "续此卷 →"
    assert window._secondary_button.text() == "返回案头"
    _dispose_window(window, qapp)


def test_workflow_top_bar_tracks_mode_specific_actions(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    window = _make_window(monkeypatch, _build_snapshot(with_project=True))

    window.switch_page("workflow")
    _drain_deferred_switch(qapp)
    assert window._primary_button.text() == "导出短篇模板"
    assert window._secondary_button.text() == "切到长篇"

    window._secondary_button.click()
    _drain_deferred_switch(qapp)
    assert window._primary_button.text() == "导出长篇模板"
    assert window._secondary_button.text() == "去章台 →"

    window._secondary_button.click()
    _drain_deferred_switch(qapp)
    # clicking "去章台 →" navigates to chapter_studio page
    assert window._primary_button.text() == "继续当前节点 →"
    assert window._secondary_button.text() == "打开文件夹"
    _dispose_window(window, qapp)


def test_workflow_long_init_panel_shows_outline_batching_guidance(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    window = _make_window(monkeypatch, _build_snapshot(with_project=True))

    window.switch_page("workflow")
    _drain_deferred_switch(qapp)
    window._secondary_button.click()
    _drain_deferred_switch(qapp)

    labels = {
        label.text() for label in window._pages["workflow"].findChildren(QLabel) if label.text()
    }
    assert any("自动选择大纲与契约批次" in text for text in labels)

    _dispose_window(window, qapp)


def test_workflow_long_init_panel_switches_to_resume_state_for_resumable_project(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    window = _make_window(monkeypatch, _build_snapshot(with_project=True, resumable_init=True))

    window.switch_page("workflow")
    _drain_deferred_switch(qapp)
    window._secondary_button.click()
    _drain_deferred_switch(qapp)

    init_form = window._pages["workflow"]._long_panel._init_form
    assert init_form.submit_button_text() == "继续立项 →"
    assert "31 章继续" in init_form.resume_hint_text()
    assert init_form.resume_percent_text() == "88%"

    _dispose_window(window, qapp)


def test_dashboard_resume_project_routes_back_to_long_init(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    window = _make_window(monkeypatch, _build_snapshot(with_project=True, resumable_init=True))

    assert window._primary_button.text() == "继续立项 →"
    window._primary_button.click()
    _drain_deferred_switch(qapp)

    assert window._stack.currentWidget() is window._pages["workflow"]
    assert window._pages["workflow"].current_long_mode() == "init"
    assert window._pages["workflow"]._long_panel._init_form.submit_button_text() == "继续立项 →"

    _dispose_window(window, qapp)


def test_projects_page_uses_library_controls_and_recent_files(qapp: QApplication) -> None:
    page = DashboardPage()
    page.bind_workspace(_build_snapshot(with_project=True))
    qapp.processEvents()

    button_texts = {button.text() for button in page.findChildren(QPushButton)}
    assert "去机杼创建" not in button_texts
    assert "续此卷" in button_texts
    assert "重建向量" in button_texts
    assert page._library_summary.text() == "在库 1 卷 \u00b7 长篇 1 \u00b7 短篇 0"
    assert page._compose_button.isEnabled()
    assert page._open_button.isEnabled()
    assert page._rebuild_vectors_button.isEnabled()
    assert page._delete_button.isEnabled()
    assert "近时产物" in page._detail_recent_files.text()
    # 路径应翻译为人类可读描述，不再直接显示原始文件路径
    assert "reports/chapter_001_eval.json" not in page._detail_recent_files.text()
    assert "质检报告" in page._detail_recent_files.text()


def test_dashboard_rebuild_vectors_uses_selected_project(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    page = DashboardPage()
    page.bind_workspace(_build_snapshot(with_project=True))
    qapp.processEvents()

    emitted: list[str] = []
    page.rebuild_memory_vectors_requested.connect(emitted.append)
    monkeypatch.setattr(dashboard_module, "ask_confirmation", lambda *args, **kwargs: True)

    page._rebuild_vectors_button.click()
    qapp.processEvents()

    assert emitted == ["long_demo"]


def test_projects_page_defers_heavy_auxiliary_tabs(qapp: QApplication) -> None:
    page = ProjectsPage()
    page.bind_workspace(_build_snapshot(with_project=True))
    page.load_project("long_demo")
    qapp.processEvents()

    tabs = page._outer_tabs
    assert tabs is not None
    tracking_index = next(index for index in range(tabs.count()) if tabs.tabText(index) == "追踪")
    tracking_tabs = tabs.widget(tracking_index)
    assert isinstance(tracking_tabs, QTabWidget)
    token_index = next(
        index
        for index in range(tracking_tabs.count())
        if tracking_tabs.tabText(index) == "Token 追踪"
    )
    relationship_index = next(
        index
        for index in range(tracking_tabs.count())
        if tracking_tabs.tabText(index) == "关系追踪"
    )

    assert page._token_tab is None
    assert tracking_tabs.widget(token_index).property("_lazy_project_tab")
    assert tracking_tabs.widget(relationship_index).property("_lazy_project_tab")

    tabs.setCurrentIndex(tracking_index)
    tracking_tabs.setCurrentIndex(token_index)
    qapp.processEvents()

    assert page._token_tab is tracking_tabs.widget(token_index)
    assert not bool(tracking_tabs.widget(token_index).property("_lazy_project_tab"))

    page.shutdown()
    page.deleteLater()
    qapp.processEvents()


def test_projects_page_fingerprint_tracks_existing_chapter_updates() -> None:
    snapshot = _build_snapshot(with_project=True)
    detail = snapshot.details["long_demo"]
    changed_chapter = detail.chapters[0].model_copy(update={"word_count": 9876})
    changed_detail = detail.model_copy(update={"chapters": [changed_chapter]})

    assert ProjectsPage._project_detail_fingerprint(
        detail
    ) != ProjectsPage._project_detail_fingerprint(changed_detail)


def test_dashboard_metrics_are_embedded_in_hero(qapp: QApplication) -> None:
    page = DashboardPage()

    assert len(page._metric_cards) == 4
    assert page._hero_panel.minimumHeight() >= 280
    assert page._hero_panel.maximumHeight() >= 340
    for card in page._metric_cards:
        assert card.property("compact") is True
        assert card.minimumHeight() >= 88
        assert _is_descendant(card, page._hero_panel)

    page.deleteLater()
    qapp.processEvents()


def test_settings_runtime_summary_is_embedded_in_hero(qapp: QApplication) -> None:
    page = SettingsPage()

    assert set(page._runtime_cards) == {"storage", "provider", "loaded", "mode"}
    for card in page._runtime_cards.values():
        assert card.property("compact") is True
        assert _is_descendant(card, page._hero_panel)

    page.deleteLater()
    qapp.processEvents()


def test_window_density_compacts_shell_chrome(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    window = _make_window(monkeypatch, _build_snapshot(with_project=True))

    window.resize(1300, 800)
    window._apply_window_density(force=True)
    qapp.processEvents()

    assert window._layout_density == "compact"
    assert window._side_rail is not None
    assert window._side_rail.width() == 264
    assert window._top_bar is not None
    assert window._top_bar.minimumHeight() == 84

    window.resize(1500, 900)
    window._apply_window_density(force=True)
    qapp.processEvents()

    assert window._layout_density == "regular"
    assert window._side_rail.width() == 300
    assert window._top_bar.minimumHeight() == 96
    _dispose_window(window, qapp)


def test_projects_page_reader_tabs_use_classic_eliding(qapp: QApplication) -> None:
    page = ProjectsPage()
    header = page.findChild(QWidget, "projectReaderHeader")
    tabs = ProjectsPage._configure_reader_tabs(QTabWidget(), "projectDocumentTabs")

    assert header is not None
    assert header.minimumHeight() == 42
    assert header.maximumHeight() == 42
    assert tabs.elideMode() == Qt.TextElideMode.ElideRight
    assert tabs.tabBar().elideMode() == Qt.TextElideMode.ElideRight
    assert tabs.tabBar().minimumHeight() == 0

    tabs.deleteLater()
    page.deleteLater()
    qapp.processEvents()


def test_dashboard_skips_noop_workspace_rebuilds(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    page = DashboardPage()
    snapshot = _build_snapshot(with_project=True)
    page.bind_workspace(snapshot)
    qapp.processEvents()

    calls: list[str] = []
    monkeypatch.setattr(page, "_render_hero", lambda project: calls.append("hero"))
    monkeypatch.setattr(page, "_render_status", lambda bound_snapshot: calls.append("status"))
    monkeypatch.setattr(page, "_render_cards", lambda: calls.append("cards"))
    monkeypatch.setattr(page, "_render_detail", lambda: calls.append("detail"))

    page.bind_workspace(snapshot)

    assert calls == []


def test_dashboard_defers_background_workspace_render_until_visible(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    stack = QStackedWidget()
    placeholder = QWidget()
    page = DashboardPage()
    stack.addWidget(placeholder)
    stack.addWidget(page)
    stack.setCurrentWidget(placeholder)
    stack.show()
    qapp.processEvents()

    calls: list[str] = []
    monkeypatch.setattr(page, "_render_hero", lambda project: calls.append("hero"))
    monkeypatch.setattr(page, "_render_status", lambda snapshot: calls.append("status"))
    monkeypatch.setattr(page, "_render_cards", lambda: calls.append("cards"))
    monkeypatch.setattr(page, "_render_detail", lambda: calls.append("detail"))

    snapshot = _build_snapshot(with_project=True)
    page.bind_workspace(snapshot)
    assert calls == []
    assert page._snapshot is snapshot

    stack.setCurrentWidget(page)
    qapp.processEvents()
    assert calls == ["hero", "status", "cards", "detail"]

    page.shutdown()
    stack.hide()
    stack.deleteLater()
    qapp.processEvents()


def test_dashboard_skips_noop_job_rebuilds(qapp: QApplication) -> None:
    page = DashboardPage()
    job = DesktopJobRecord(
        job_id="job-1",
        kind="run_chapter",
        label="生成第 1 章",
        project_id="long_demo",
        status=DesktopJobState.RUNNING,
        current_step="draft",
    )

    page.bind_jobs([job])
    qapp.processEvents()
    first_widget = page._jobs_layout.itemAt(0).widget()

    page.bind_jobs([job])
    qapp.processEvents()
    second_widget = page._jobs_layout.itemAt(0).widget()

    assert second_widget is first_widget


def test_window_refresh_workspace_loads_chapter_context_once_per_changed_refresh(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    class _CountingWorkspaceService(_FakeWorkspaceService):
        def __init__(self, snapshot: DesktopWorkspaceSnapshot) -> None:
            super().__init__(snapshot)
            self.chapter_requests: list[tuple[str, int]] = []

        def get_chapter_workspace_snapshot(
            self,
            project_id: str,
            chapter_number: int,
            *,
            project_detail: object | None = None,
        ) -> ChapterWorkspaceSnapshot:
            self.chapter_requests.append((project_id, chapter_number))
            return ChapterWorkspaceSnapshot(
                project_id=project_id,
                project_title="遗物人生",
                chapter_number=chapter_number,
                total_chapters=12,
                chapters=[
                    ChapterWorkspaceChapter(chapter_number=1, status="done"),
                    ChapterWorkspaceChapter(chapter_number=chapter_number, status="current"),
                ],
                current_title=f"第 {chapter_number} 章",
            )

    service = _CountingWorkspaceService(_build_snapshot(with_project=True))
    monkeypatch.setattr(
        window_module.DesktopWorkspaceService,
        "from_settings",
        classmethod(lambda cls, mock=False: service),
    )

    window = NovelForgeDesktopWindow()
    window._refresh_timer.stop()
    _drain_qt_workers(qapp)
    window._ensure_page("chapter_studio", bind_workspace=False, bind_jobs=False)
    window._chapter_studio_project_id = "long_demo"
    window._set_chapter_number("long_demo", 2)
    window._refresh_chapter_studio_context()
    _drain_qt_workers(qapp)

    assert service.chapter_requests == [("long_demo", 2)]

    _emit_workspace_refreshed(
        window,
        service._snapshot,
        service,
        section_hints={"details/long_demo"},
    )
    _drain_qt_workers(qapp)

    assert service.chapter_requests == [("long_demo", 2), ("long_demo", 2)]
    _dispose_window(window, qapp)


def test_window_retains_workspace_refresh_worker_until_callback(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    snapshot = _build_snapshot(with_project=False)
    service = _FakeWorkspaceService(snapshot)
    fake_pool = _CapturingThreadPool()

    monkeypatch.setattr(
        window_module.DesktopWorkspaceService,
        "from_settings",
        classmethod(lambda cls, mock=False: service),
    )
    monkeypatch.setattr(
        window_module,
        "desktop_thread_pools",
        lambda: _fake_thread_pools(fake_pool),
    )
    monkeypatch.setattr(
        window_module.NovelForgeDesktopWindow,
        "_load_ui_session",
        lambda self: None,
    )
    # B2 async init: force synchronous RuntimeServices construction so the
    # refresh worker is queued before the assertions run.
    monkeypatch.setattr(
        window_module.NovelForgeDesktopWindow,
        "_sync_runtime_services_init",
        True,
    )

    window = NovelForgeDesktopWindow()
    window._refresh_timer.stop()

    assert fake_pool.workers
    assert window._active_workspace_refresh_worker in fake_pool.workers

    window._on_workspace_refresh_failed("boom")

    assert window._active_workspace_refresh_worker is None
    _dispose_window(window, qapp)


def test_window_starts_autorun_ticker_without_sidecar_result(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    snapshot = _build_snapshot(with_project=False)
    service = _FakeWorkspaceService(snapshot)

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
    # B2 async init: force synchronous RuntimeServices construction so the
    # autorun ticker is started by _on_runtime_services_ready.
    monkeypatch.setattr(
        window_module.NovelForgeDesktopWindow,
        "_sync_runtime_services_init",
        True,
    )

    window = NovelForgeDesktopWindow()
    window._refresh_timer.stop()

    assert window._autorun_ticker is not None
    assert window._autorun_ticker.isActive()
    _dispose_window(window, qapp)


def test_window_retains_context_refresh_worker_until_callback(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    snapshot = _build_snapshot(with_project=True)
    service = _FakeWorkspaceService(snapshot)
    fake_pool = _CapturingThreadPool()

    monkeypatch.setattr(
        window_module.DesktopWorkspaceService,
        "from_settings",
        classmethod(lambda cls, mock=False: service),
    )
    monkeypatch.setattr(
        window_module,
        "desktop_thread_pools",
        lambda: _fake_thread_pools(fake_pool),
    )
    monkeypatch.setattr(
        window_module.NovelForgeDesktopWindow,
        "_load_ui_session",
        lambda self: None,
    )

    window = NovelForgeDesktopWindow()
    window._refresh_timer.stop()
    window._chapter_studio_project_id = "long_demo"
    window._set_chapter_number("long_demo", 2)
    window._ensure_page("chapter_studio", bind_workspace=False, bind_jobs=False)

    fake_pool.workers.clear()
    _emit_workspace_refreshed(window, snapshot, service)

    assert fake_pool.workers
    assert window._active_context_refresh_workers == {fake_pool.workers[0]}

    window._on_chapter_context_refreshed(fake_pool.workers[0], _build_chapter_studio_snapshot())

    assert not window._active_context_refresh_workers
    _dispose_window(window, qapp)


def test_window_context_refresh_callback_keeps_other_active_workers(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    snapshot = _build_snapshot(with_project=True)
    service = _FakeWorkspaceService(snapshot)
    fake_pool = _CapturingThreadPool()

    monkeypatch.setattr(
        window_module.DesktopWorkspaceService,
        "from_settings",
        classmethod(lambda cls, mock=False: service),
    )
    monkeypatch.setattr(
        window_module,
        "desktop_thread_pools",
        lambda: _fake_thread_pools(fake_pool),
    )
    monkeypatch.setattr(
        window_module.NovelForgeDesktopWindow,
        "_load_ui_session",
        lambda self: None,
    )

    window = NovelForgeDesktopWindow()
    window._refresh_timer.stop()
    window._chapter_studio_project_id = "long_demo"
    window._set_chapter_number("long_demo", 2)
    window._ensure_page("chapter_studio", bind_workspace=False, bind_jobs=False)

    fake_pool.workers.clear()
    _emit_workspace_refreshed(window, snapshot, service)
    window._chapter_context_refresh_key = None
    window._set_chapter_number("long_demo", 3)
    window._refresh_chapter_studio_context()

    assert len(fake_pool.workers) == 2
    first_worker, second_worker = fake_pool.workers
    assert window._active_context_refresh_workers == {first_worker, second_worker}

    window._on_chapter_context_refreshed(first_worker, _build_chapter_studio_snapshot())

    assert window._active_context_refresh_workers == {second_worker}
    _dispose_window(window, qapp)


def test_window_initial_workspace_refresh_binds_only_visible_and_background_pages(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    snapshot = _build_snapshot(with_project=True)
    service = _FakeWorkspaceService(snapshot)
    fake_pool = _CapturingThreadPool()

    monkeypatch.setattr(
        window_module.DesktopWorkspaceService,
        "from_settings",
        classmethod(lambda cls, mock=False: service),
    )
    monkeypatch.setattr(
        window_module,
        "desktop_thread_pools",
        lambda: _fake_thread_pools(fake_pool),
    )
    monkeypatch.setattr(
        window_module.NovelForgeDesktopWindow,
        "_load_ui_session",
        lambda self: None,
    )

    window = NovelForgeDesktopWindow()
    window._refresh_timer.stop()

    assert window._pages_needing_bind(
        {
            "storage_root",
            "default_provider",
            "overview",
            "metrics",
            "providers",
            "projects",
            "featured_project",
            "details",
        },
        "dashboard",
        initial=True,
    ) == ["dashboard"]
    _dispose_window(window, qapp)


def test_window_page_animation_skips_effectful_page_subtrees(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    window = _make_window(monkeypatch, _build_snapshot(with_project=True))
    monkeypatch.setattr(window_module, "animations_supported", lambda: True)

    workflow_page = window._pages["workflow"]
    projects_page = window._pages["projects"]
    assert window._widget_tree_has_graphics_effect(workflow_page)

    window.switch_page("workflow")
    assert workflow_page.graphicsEffect() is None
    assert window._page_animation is None

    window.switch_page("projects")
    assert workflow_page.graphicsEffect() is None
    assert projects_page.graphicsEffect() is None
    assert window._page_animation is None

    window._clear_page_animation()
    assert projects_page.graphicsEffect() is None
    _dispose_window(window, qapp)


def test_window_project_combo_switch_syncs_hidden_chapter_before_context_bind(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    base = _build_snapshot(with_project=True)
    first_project = base.projects[0]
    second_project = replace(
        first_project,
        project_id="long_second",
        title="第二部长篇",
        completed_chapters=4,
        next_chapter=5,
        progress_label="4/12",
    )
    first_detail = base.details["long_demo"]
    second_detail = first_detail.model_copy(
        update={
            "project_id": "long_second",
            "title": "第二部长篇",
            "completed_chapters": 4,
            "latest_chapter": 4,
        }
    )
    snapshot = replace(
        base,
        projects=[first_project, second_project],
        details={"long_demo": first_detail, "long_second": second_detail},
    )

    class _MultiProjectWorkspaceService(_FakeWorkspaceService):
        def __init__(self, snapshot: DesktopWorkspaceSnapshot) -> None:
            super().__init__(snapshot)
            self.chapter_requests: list[tuple[str, int]] = []

        def get_chapter_workspace_snapshot(
            self,
            project_id: str,
            chapter_number: int,
            *,
            project_detail: ProjectDetail | None = None,
        ) -> ChapterWorkspaceSnapshot:
            self.chapter_requests.append((project_id, chapter_number))
            title = "第二部长篇" if project_id == "long_second" else "遗物人生"
            return ChapterWorkspaceSnapshot(
                project_id=project_id,
                project_title=title,
                chapter_number=chapter_number,
                total_chapters=12,
                chapters=[
                    ChapterWorkspaceChapter(chapter_number=1, status="done"),
                    ChapterWorkspaceChapter(chapter_number=chapter_number, status="current"),
                ],
                current_title=f"第 {chapter_number} 章",
            )

    service = _MultiProjectWorkspaceService(snapshot)
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

    window = NovelForgeDesktopWindow()
    window._refresh_timer.stop()
    _drain_qt_workers(qapp)

    studio = window._pages["chapter_studio"]
    window.switch_page("chapter_studio")
    qapp.processEvents()

    studio._project_combo.setCurrentText("long_second")
    qapp.processEvents()
    _drain_qt_workers(qapp)

    assert service.chapter_requests[-1] == ("long_second", 5)
    assert studio.current_chapter_number() == 5
    assert studio._studio is not None
    assert studio._studio.project_id == "long_second"
    assert studio._studio.chapter_number == 5
    _dispose_window(window, qapp)


def test_window_collects_unsaved_changes_from_all_pages(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    window = _make_window(monkeypatch, _build_snapshot(with_project=True))

    window._pages["workflow"].has_unsaved_changes = lambda: True  # type: ignore[method-assign]
    window._pages["workflow"].unsaved_changes_description = lambda: (
        "- 机杼页有未提交输入（关闭后将丢失）"
    )  # type: ignore[method-assign]
    window._pages["projects"].has_unsaved_changes = lambda: True  # type: ignore[method-assign]
    window._pages["projects"].unsaved_changes_description = lambda: (
        "- 卷帙页（Token 追踪）有未保存设置"
    )  # type: ignore[method-assign]
    window._pages["projects"].save_pending_changes = lambda: True  # type: ignore[method-assign]

    entries = window._collect_unsaved_page_entries()
    details = {str(item["detail"]) for item in entries}

    assert "- 机杼页有未提交输入（关闭后将丢失）" in details
    assert "- 卷帙页（Token 追踪）有未保存设置" in details
    assert any(item["page_id"] == "projects" and callable(item["save_fn"]) for item in entries)
    _dispose_window(window, qapp)


def test_window_pre_close_cleanup_stops_settings_and_jobs(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    window = _make_window(monkeypatch, _build_snapshot(with_project=True))
    calls: list[str] = []

    monkeypatch.setattr(window._pages["settings"], "shutdown", lambda: calls.append("settings"))
    monkeypatch.setattr(
        window._job_manager,
        "shutdown",
        lambda wait_ms=200: calls.append(f"jobs:{wait_ms}"),
    )

    window._pre_close_cleanup()

    assert calls == ["settings", "jobs:100"]
    _dispose_window(window, qapp)


def test_window_close_unsavable_only_uses_unsavable_dialog_branch(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    window = _make_window(monkeypatch, _build_snapshot(with_project=True))
    calls: list[str] = []

    monkeypatch.setattr(
        window,
        "_collect_unsaved_page_entries",
        lambda: [{"page_id": "workflow", "detail": "- 机杼页有临时输入", "save_fn": None}],
    )
    monkeypatch.setattr(
        window, "_confirm_exit_for_unsavable", lambda details: calls.append("confirm") or True
    )
    monkeypatch.setattr(window, "_pre_close_cleanup", lambda: calls.append("cleanup"))

    class _FakeEvent:
        accepted = False
        ignored = False

        def accept(self) -> None:
            self.accepted = True

        def ignore(self) -> None:
            self.ignored = True

    event = _FakeEvent()
    window.closeEvent(event)

    assert calls == ["confirm", "cleanup"]
    assert event.accepted is True
    assert event.ignored is False
    _dispose_window(window, qapp)


def test_workflow_unsaved_changes_requires_modified_flag(qapp: QApplication) -> None:
    page = WorkflowPage()
    page._mode_bar.select("short")
    lines = page._short_form.findChildren(QLineEdit)
    assert lines

    line = lines[0]
    line.setText("测试输入")
    line.setModified(False)
    assert page.has_unsaved_changes() is False

    line.setModified(True)
    assert page.has_unsaved_changes() is True


def test_workflow_unsaved_changes_detects_cleared_modified_field(
    qapp: QApplication,
) -> None:
    page = WorkflowPage()
    page._mode_bar.select("short")
    lines = page._short_form.findChildren(QLineEdit)
    assert lines

    line = lines[0]
    line.setText("")
    line.setModified(True)
    assert page.has_unsaved_changes() is True


def test_workflow_reloads_draft_after_storage_root_change(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    page = WorkflowPage()
    try:
        first_root = tmp_path / "first"
        second_root = tmp_path / "second"
        first_draft = first_root / ".presets" / ".draft" / "_autosave_short.json"
        second_draft = second_root / ".presets" / ".draft" / "_autosave_short.json"
        first_draft.parent.mkdir(parents=True)
        second_draft.parent.mkdir(parents=True)
        first_draft.write_text(
            json.dumps({"theme": "第一份草稿", "genre": "悬疑"}, ensure_ascii=False),
            encoding="utf-8",
        )
        second_draft.write_text(
            json.dumps({"theme": "第二份草稿", "genre": "科幻"}, ensure_ascii=False),
            encoding="utf-8",
        )

        base_snapshot = _build_snapshot(with_project=False)
        page.bind_workspace(replace(base_snapshot, storage_root=first_root))
        assert page._short_form._theme.toPlainText() == "第一份草稿"

        page.bind_workspace(replace(base_snapshot, storage_root=second_root))
        assert page._short_form._theme.toPlainText() == "第二份草稿"
    finally:
        page.shutdown()
        page.deleteLater()
        qapp.processEvents()


def test_outline_widget_save_pending_tracks_unsaved_polish(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    outline_path = tmp_path / "outline.json"
    original = {"chapters": [{"chapter_number": 1, "title": "旧章"}]}
    outline_path.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
    widget = InteractiveOutlineWidget(dict(original), outline_path)
    try:
        widget._outline_data = {"chapters": [{"chapter_number": 1, "title": "新章"}]}
        assert widget.has_unsaved_changes() is True
        assert widget.save_pending_changes() is True
        saved = json.loads(outline_path.read_text(encoding="utf-8"))
        assert saved["chapters"][0]["title"] == "新章"
        assert widget.has_unsaved_changes() is False
    finally:
        widget.deleteLater()
        qapp.processEvents()


def test_projects_page_load_project_respects_unsaved_embedded_editor(
    qapp: QApplication,
) -> None:
    page = ProjectsPage()
    page._current_project_id = "old_project"
    page._outline_widgets = [
        SimpleNamespace(
            has_unsaved_changes=lambda: True,
            confirm_close=lambda: False,
        )
    ]

    page.load_project("new_project")

    assert page._current_project_id == "old_project"
    page.deleteLater()
    qapp.processEvents()


def test_chapter_studio_unsaved_notes_require_modified_and_can_commit(qapp: QApplication) -> None:
    page = ChapterStudioPage()
    page._notes.setPlainText("临时备注")
    page._notes.document().setModified(False)
    assert page.has_unsaved_changes() is False

    page._notes.document().setModified(True)
    assert page.has_unsaved_changes() is True

    page._mark_notes_committed()
    assert page.has_unsaved_changes() is False


def test_dispatch_init_long_autorun_tracks_job_id_with_empty_project_id(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    window = _make_window(monkeypatch, _build_snapshot(with_project=True))

    called: dict[str, bool] = {"dispatch_job": False}

    def _unexpected_dispatch(_request: object) -> None:
        called["dispatch_job"] = True

    def _fake_submit(request: InitLongRequest, *, mock: bool = False) -> DesktopJobRecord:
        assert request.project_id == ""
        assert request.premise == "测试前提"
        return DesktopJobRecord(
            job_id="init-autorun-1",
            kind="init_long",
            label="长篇立项 · 自动项目",
        )

    monkeypatch.setattr(window, "_dispatch_job", _unexpected_dispatch)
    monkeypatch.setattr(window._job_manager, "submit_init_long", _fake_submit)

    window._dispatch_init_long_autorun(InitLongRequest(project_id="", premise="测试前提"))

    assert called["dispatch_job"] is False
    assert "init-autorun-1" in window._pending_autorun_job_ids
    _dispose_window(window, qapp)


def test_dashboard_rebuild_vectors_dispatch_builds_request(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    window = _make_window(monkeypatch, _build_snapshot(with_project=True))
    captured: list[object] = []
    monkeypatch.setattr(window, "_dispatch_job", captured.append)

    window._submit_rebuild_memory_vectors("long_demo")

    assert len(captured) == 1
    request = captured[0]
    assert isinstance(request, RebuildMemoryVectorsRequest)
    assert request.project_id == "long_demo"
    assert request.include_expression is True
    _dispose_window(window, qapp)


def test_handle_job_completed_autorun_uses_job_id_tracking(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    window = _make_window(monkeypatch, _build_snapshot(with_project=True))
    window._pending_autorun_job_ids.add("init-autorun-2")

    completed_job = DesktopJobRecord(
        job_id="init-autorun-2",
        kind="init_long",
        label="长篇立项 · 自动项目",
        project_id="long_demo",
        status=DesktopJobState.SUCCEEDED,
        result={"chapter_number": 1},
    )
    monkeypatch.setattr(window._job_manager, "jobs", lambda: [completed_job])
    monkeypatch.setattr(window, "refresh_workspace", lambda force=True: None)
    monkeypatch.setattr(
        window._pages["workflow"], "focus_project", lambda project_id, chapter: None
    )

    autorun_calls: list[tuple[str, int]] = []
    monkeypatch.setattr(
        window,
        "_focus_chapter_studio_with_autorun",
        lambda project_id, chapter_number: autorun_calls.append((project_id, chapter_number)),
    )

    window._handle_job_completed("init-autorun-2")

    assert autorun_calls == [("long_demo", 1)]
    assert "init-autorun-2" not in window._pending_autorun_job_ids
    _dispose_window(window, qapp)


def test_handle_job_completed_archive_uses_job_result_when_snapshot_lags(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    window = _make_window(monkeypatch, _build_snapshot(with_project=True))
    completed_job = DesktopJobRecord(
        job_id="archive-ch2",
        kind="resolve_chapter_checkpoint_finalize",
        label="归档执行 · long_demo / 第 2 章",
        project_id="long_demo",
        status=DesktopJobState.SUCCEEDED,
        result={"chapter_number": 2, "status": "completed"},
    )
    monkeypatch.setattr(window._job_manager, "jobs", lambda: [completed_job])
    monkeypatch.setattr(window, "refresh_workspace", lambda force=True: None)

    workflow_focus: list[tuple[str, int]] = []
    studio_focus: list[tuple[str, int]] = []
    monkeypatch.setattr(
        window._pages["workflow"],
        "focus_project",
        lambda project_id, chapter: workflow_focus.append((project_id, chapter)),
    )
    monkeypatch.setattr(
        window,
        "_focus_chapter_studio",
        lambda project_id, chapter_number: studio_focus.append((project_id, chapter_number)),
    )

    window._handle_job_completed("archive-ch2")

    assert workflow_focus == [("long_demo", 3)]
    assert studio_focus == [("long_demo", 3)]
    _dispose_window(window, qapp)


def test_handle_job_completed_clears_failed_autorun_marker(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    window = _make_window(monkeypatch, _build_snapshot(with_project=True))
    window._pending_autorun_job_ids.add("init-autorun-3")

    failed_job = DesktopJobRecord(
        job_id="init-autorun-3",
        kind="init_long",
        label="长篇立项 · 自动项目",
        project_id="long_demo",
        status=DesktopJobState.FAILED,
        error="模型调用失败",
    )
    monkeypatch.setattr(window._job_manager, "jobs", lambda: [failed_job])
    monkeypatch.setattr(window, "refresh_workspace", lambda force=True: None)

    autorun_calls: list[tuple[str, int]] = []
    monkeypatch.setattr(
        window,
        "_focus_chapter_studio_with_autorun",
        lambda project_id, chapter_number: autorun_calls.append((project_id, chapter_number)),
    )
    monkeypatch.setattr(
        window._pages["workflow"], "focus_project", lambda project_id, chapter: None
    )

    window._handle_job_completed("init-autorun-3")

    assert autorun_calls == []
    assert "init-autorun-3" not in window._pending_autorun_job_ids
    _dispose_window(window, qapp)


def test_focus_chapter_studio_with_autorun_activates_book_auto(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    window = _make_window(monkeypatch, _build_snapshot(with_project=True))
    studio = window._pages["chapter_studio"]
    monkeypatch.setattr(studio, "_try_auto_action", lambda: None)
    # The page is now a controller for the Engine-owned state machine. Keep
    # this UI test isolated from any real persisted task history and assert
    # the command boundary explicitly.
    window._job_manager._storage_root = window._snapshot.storage_root
    window._job_manager._jobs.clear()
    studio.bind_jobs([])
    autorun_starts: list[dict[str, object]] = []

    def start_autorun(**kwargs: object) -> object:
        autorun_starts.append(kwargs)
        # A non-None record is the DesktopJobManager success contract.  None
        # means that Engine did not create or expose an active autorun session.
        return object()

    monkeypatch.setattr(
        window._job_manager,
        "start_chapter_autorun",
        start_autorun,
    )

    window._focus_chapter_studio_with_autorun("long_demo", 99)

    state = studio.autorun_state_for_project("long_demo")
    assert window._get_chapter_number("long_demo") == 12
    assert state.mode == studio.MODE_BOOK_AUTO
    assert state.auto_started is True
    assert studio.active_autorun_project_ids() == ["long_demo"]
    assert autorun_starts == [
        {
            "project_id": "long_demo",
            "chapter_number": 12,
            "mode": "book",
            "writing_mode": "whole_chapter",
            "force": False,
            "skip_done": True,
            "mock": window._mock_enabled,
        }
    ]
    _dispose_window(window, qapp)


def test_chapter_context_navigation_does_not_move_backend_autorun_target(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    window = _make_window(monkeypatch, _build_snapshot(with_project=True))
    window._chapter_studio_project_id = "long_demo"
    window._set_chapter_number("long_demo", 5)
    window._set_autorun_chapter_number("long_demo", 10)

    window._bind_chapter_studio_context("long_demo", 3)

    assert window._get_chapter_number("long_demo") == 3
    assert window._get_autorun_chapter_number("long_demo") == 10
    _dispose_window(window, qapp)


def test_background_autorun_advance_does_not_move_visible_chapter_when_unfollowed(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    window = _make_window(monkeypatch, _build_snapshot(with_project=True))
    studio = window._pages["chapter_studio"]
    studio._state.current_project_id = "long_demo"
    studio._mode = studio.MODE_BOOK_AUTO
    studio._auto_started = True
    studio._state.follow_autorun = False
    window._chapter_studio_project_id = "long_demo"
    window._set_chapter_number("long_demo", 5)
    window._set_autorun_chapter_number("long_demo", 10)
    studio._chapter_spin.setValue(5)
    focus_calls: list[tuple[str, int | None]] = []
    monkeypatch.setattr(
        studio,
        "focus_project",
        lambda project_id, chapter=None: focus_calls.append((project_id, chapter)),
    )

    window._apply_project_autorun_decision(
        "long_demo",
        _build_chapter_studio_snapshot(chapter_number=10),
        SimpleNamespace(action="advance_chapter", next_chapter=11, delay_ms=0),
        studio.autorun_state_for_project("long_demo"),
    )

    assert window._get_autorun_chapter_number("long_demo") == 11
    assert window._get_chapter_number("long_demo") == 11
    assert studio.current_chapter_number() == 5
    assert focus_calls == []
    _dispose_window(window, qapp)


def test_background_autorun_advance_updates_visible_chapter_when_followed(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    window = _make_window(monkeypatch, _build_snapshot(with_project=True))
    studio = window._pages["chapter_studio"]
    studio._state.current_project_id = "long_demo"
    studio._mode = studio.MODE_BOOK_AUTO
    studio._auto_started = True
    studio._state.follow_autorun = True
    window._chapter_studio_project_id = "long_demo"
    window._set_chapter_number("long_demo", 5)
    window._set_autorun_chapter_number("long_demo", 10)

    window._apply_project_autorun_decision(
        "long_demo",
        _build_chapter_studio_snapshot(chapter_number=10),
        SimpleNamespace(action="advance_chapter", next_chapter=11, delay_ms=0),
        studio.autorun_state_for_project("long_demo"),
    )

    assert window._get_autorun_chapter_number("long_demo") == 11
    assert window._get_chapter_number("long_demo") == 11
    _dispose_window(window, qapp)
