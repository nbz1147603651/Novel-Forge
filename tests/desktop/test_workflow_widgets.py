from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from novel_forge.desktop.jobs import DesktopJobEvent, DesktopJobRecord, DesktopJobState
from novel_forge.desktop.pages.workflow.artifacts import _artifact_candidates
from novel_forge.desktop.pages.workflow.page import WorkflowPage
from novel_forge.desktop.pages.workflow.widgets import ActiveProjectsPanel
from novel_forge.desktop.task_observation import TaskObservationStore
from novel_forge.desktop.widgets import ActionButton
from novel_forge.desktop.workspace import (
    DesktopProjectItem,
    DesktopWorkspaceMetrics,
    DesktopWorkspaceSnapshot,
    ProviderStatus,
)
from novel_forge.workspace.projects import WorkspaceOverview


def test_tts_task_nodes_expose_script_and_delivery_artifacts(tmp_path: Path) -> None:
    script = tmp_path / "tts/scripts/chapter_003_script.json"
    audio = tmp_path / "tts/audio/chapter_003/chapter_full.mp3"
    auto_run = tmp_path / "tts/results/chapter_003_auto_run.json"
    for path in (script, audio, auto_run):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio" if path.suffix == ".mp3" else b"{}")

    script_artifacts = _artifact_candidates(
        "tts_post_archive",
        "tts_script",
        tmp_path,
        chapter_number=3,
    )
    delivery_artifacts = _artifact_candidates(
        "tts_post_archive",
        "tts_delivery",
        tmp_path,
        chapter_number=3,
    )

    assert ("配音脚本", script) in script_artifacts
    assert ("自动配音运行记录", auto_run) in delivery_artifacts
    assert ("章节成品音频", audio) in delivery_artifacts


def _build_snapshot(projects: list[DesktopProjectItem]) -> DesktopWorkspaceSnapshot:
    storage_root = Path("/tmp/novel_forge_workflow_widgets")
    return DesktopWorkspaceSnapshot(
        storage_root=storage_root,
        default_provider="mock",
        overview=WorkspaceOverview(
            storage_root=str(storage_root),
            total_projects=len(projects),
            short_projects=sum(1 for item in projects if item.mode == "short"),
            long_projects=sum(1 for item in projects if item.mode == "long"),
            total_generated_chapters=sum(item.completed_chapters for item in projects),
            providers=["mock"],
            default_provider="mock",
        ),
        metrics=DesktopWorkspaceMetrics(
            total_projects=len(projects),
            total_chapters=sum(item.completed_chapters for item in projects),
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
                detail="当前已载入",
            )
        ],
        projects=projects,
        featured_project=projects[0] if projects else None,
        details={},
    )


def test_active_projects_panel_hides_archived_projects_and_uses_lifecycle_labels(
    qtbot,
    desktop_app,
) -> None:
    from novel_forge.desktop.theme import get_stylesheet

    desktop_app.setStyleSheet(get_stylesheet())
    panel = ActiveProjectsPanel()
    qtbot.addWidget(panel)

    snapshot = _build_snapshot(
        [
            DesktopProjectItem(
                project_id="archived_demo",
                title="归档项目",
                mode="long",
                mode_label="长篇",
                status="archived",
                status_label="已归档",
                progress_label="已归档",
                progress_percent=100,
                last_updated_label="2026-05-02 10:00",
                headline="归档后的项目",
                next_action="查看归档项目与历史产物",
                genre="mystery",
                tone="cold",
                completed_chapters=10,
                total_chapters=10,
                next_chapter=None,
                has_outline=True,
                has_canon=True,
                project_state="archived",
                project_state_label="已归档",
            ),
            DesktopProjectItem(
                project_id="paused_demo",
                title="暂停项目",
                mode="long",
                mode_label="长篇",
                status="paused",
                status_label="已暂停",
                progress_label="大纲与 Canon 已就绪，等待进入章台继续推进第 2 章",
                progress_percent=20,
                last_updated_label="2026-05-02 10:00",
                headline="等待人工决策",
                next_action="恢复并继续第 2 章",
                genre="mystery",
                tone="cold",
                completed_chapters=1,
                total_chapters=10,
                next_chapter=2,
                has_outline=True,
                has_canon=True,
                project_state="paused",
                project_state_label="已暂停",
                allowed_operations=("archive", "resume"),
            ),
            DesktopProjectItem(
                project_id="failed_demo",
                title="失败项目",
                mode="long",
                mode_label="长篇",
                status="planning",
                status_label="立项失败",
                progress_label="初始化失败，待重试",
                progress_percent=12,
                last_updated_label="2026-05-02 10:00",
                headline="初始化失败",
                next_action="检查初始化状态并重试立项",
                genre="fantasy",
                tone="epic",
                completed_chapters=0,
                total_chapters=12,
                next_chapter=1,
                has_outline=False,
                has_canon=False,
                project_state="init_failed",
                project_state_label="立项失败",
                allowed_operations=("archive", "retry"),
            ),
        ]
    )

    panel.render(snapshot)
    panel.resize(540, panel.sizeHint().height())
    panel.show()
    desktop_app.processEvents()

    buttons = panel.findChildren(ActionButton)
    labels = [button.text() for button in buttons]

    assert labels == ["恢复 →", "重试立项 →"]
    for button in buttons:
        assert button.property("compact") is True
        assert button.width() >= button.sizeHint().width()
        assert button.height() >= button.sizeHint().height()


def test_workflow_task_flow_filters_generated_test_project_jobs(
    qtbot,
    desktop_app,
) -> None:
    page = WorkflowPage()
    qtbot.addWidget(page)

    snapshot = _build_snapshot(
        [
            DesktopProjectItem(
                project_id="reader_project",
                title="读者项目",
                mode="long",
                mode_label="长篇",
                status="writing",
                status_label="连载中",
                progress_label="1/12",
                progress_percent=8,
                last_updated_label="2026-05-31 12:00",
                headline="真实创作项目",
                next_action="继续推进第 2 章",
                genre="fiction",
                tone="neutral",
                completed_chapters=1,
                total_chapters=12,
                next_chapter=2,
                has_outline=True,
                has_canon=True,
            ),
        ]
    )
    page.bind_workspace(snapshot)

    visible_job = DesktopJobRecord(
        job_id="job-visible",
        kind="init_long",
        label="长篇立项 · reader_project",
        project_id="reader_project",
        status=DesktopJobState.SUCCEEDED,
    )
    hidden_job = DesktopJobRecord(
        job_id="job-hidden",
        kind="init_long",
        label="长篇立项 · test-project",
        project_id="test-project",
        status=DesktopJobState.SUCCEEDED,
    )
    project_a_job = DesktopJobRecord(
        job_id="job-project-a",
        kind="init_long",
        label="长篇立项 · project_a",
        project_id="project_a",
        status=DesktopJobState.FAILED,
    )

    try:
        page.bind_jobs([hidden_job, visible_job, project_a_job])

        assert [job.job_id for job in page._jobs_panel._latest_render_jobs] == ["job-visible"]
    finally:
        page.shutdown()


def test_workflow_hides_compact_focus_until_stream_or_decision_is_available(
    qtbot,
    desktop_app,
) -> None:
    page = WorkflowPage()
    qtbot.addWidget(page)
    page.bind_workspace(
        _build_snapshot(
            [
                DesktopProjectItem(
                    project_id="reader_project",
                    title="读者项目",
                    mode="long",
                    mode_label="长篇",
                    status="writing",
                    status_label="连载中",
                    progress_label="1/12",
                    progress_percent=8,
                    last_updated_label="2026-05-31 12:00",
                    headline="真实创作项目",
                    next_action="继续推进第 2 章",
                    genre="fiction",
                    tone="neutral",
                    completed_chapters=1,
                    total_chapters=12,
                    next_chapter=2,
                    has_outline=True,
                    has_canon=True,
                ),
            ]
        )
    )
    focused = DesktopJobRecord(
        job_id="job-focused",
        kind="init_long",
        label="长篇立项 · reader_project",
        project_id="reader_project",
        status=DesktopJobState.RUNNING,
        current_step="init_story_bible",
    )
    historical = DesktopJobRecord(
        job_id="job-history",
        kind="init_long",
        label="长篇立项 · reader_project",
        project_id="reader_project",
        status=DesktopJobState.SUCCEEDED,
    )
    store = TaskObservationStore()

    try:
        store.ingest_jobs([focused, historical])
        page.bind_task_observation_store(store)
        page.bind_jobs([focused, historical])

        assert page._task_focus_panel.current_state is not None
        assert page._task_focus_panel.current_state.job_id == "job-focused"
        assert page._task_focus_panel.isHidden() is True
        assert [job.job_id for job in page._jobs_panel._latest_render_jobs] == [
            "job-focused",
            "job-history",
        ]

        focused.events = [
            DesktopJobEvent(
                at="2026-05-31T12:01:00+00:00",
                step="llm_stream_start",
                payload={"stream_id": "draft-1", "task": "DRAFT_CHAPTER"},
            ),
            DesktopJobEvent(
                at="2026-05-31T12:01:01+00:00",
                step="llm_stream_delta",
                payload={"stream_id": "draft-1", "delta": "可见的流式正文"},
            ),
        ]
        store.ingest_jobs([focused, historical])
        page.bind_jobs([focused, historical])

        assert page._task_focus_panel.isHidden() is False
        assert page._task_focus_panel._title_label.isHidden() is True
        assert page._task_focus_panel._meta_host.isHidden() is True
        assert page._task_focus_panel._phase_progress.isHidden() is True
    finally:
        page.shutdown()
