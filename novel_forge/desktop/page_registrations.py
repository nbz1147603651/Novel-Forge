"""Register all built-in desktop pages with the PageRegistry.

This module is imported by ``window.py`` to ensure all pages are registered
before the window builds its UI.  Adding a new page requires:
1. Create the page class
2. Add a ``@page_registry.register(...)`` call below
3. No changes to ``window.py`` needed.

M4 subdir-layout refactor: imports target the per-group subpackages
(``pages.chapter_studio``, ``pages.workflow``, …) instead of the flat
``pages.chapter_studio_page`` etc. that pre-existed M4.
"""

from __future__ import annotations

from typing import Any

from novel_forge.desktop.pages.chapter_studio.page import ChapterStudioPage
from novel_forge.desktop.pages.settings.page import SettingsPage
from novel_forge.desktop.pages.standalone.dashboard_page import DashboardPage
from novel_forge.desktop.pages.standalone.projects_page import ProjectsPage
from novel_forge.desktop.pages.workflow.page import WorkflowPage
from novel_forge.desktop.registry import page_registry
from novel_forge.desktop.strings import UIStrings

# ── Signal connectors ──────────────────────────────────────────────────
# Each function connects a page's signals to window slots.  Called exactly
# once per page via ``connect_page_signals`` (guarded by
# ``_connected_page_signals`` set on the window).


def _connect_dashboard(dashboard: Any, owner: Any) -> None:
    dashboard.navigate_requested.connect(owner.switch_page)
    dashboard.compose_requested.connect(owner._focus_workflow)
    dashboard.view_project_requested.connect(owner._view_project_in_reader)
    dashboard.open_project_requested.connect(owner._open_project_folder)
    dashboard.delete_requested.connect(owner._handle_delete_project)
    dashboard.rebuild_memory_vectors_requested.connect(owner._submit_rebuild_memory_vectors)
    dashboard.context_changed.connect(lambda: owner._refresh_top_actions_for("dashboard"))
    dashboard.context_changed.connect(owner._schedule_ui_session_save)
    dashboard.clear_task_flow_requested.connect(owner._clear_workflow_task_flow)
    dashboard.task_focus_decision_selected.connect(owner._provide_task_decision)
    dashboard.task_focus_expand_requested.connect(
        lambda: owner._open_task_focus_dialog(focus_stream_detail=True)
    )
    if hasattr(dashboard, "open_blueprint_requested"):
        dashboard.open_blueprint_requested.connect(owner._on_open_blueprint)
    if hasattr(dashboard, "open_graph_requested"):
        dashboard.open_graph_requested.connect(owner._on_open_graph)
    if hasattr(dashboard, "open_profile_requested"):
        dashboard.open_profile_requested.connect(owner._on_open_profile)
    if hasattr(dashboard, "open_book_consistency_requested"):
        dashboard.open_book_consistency_requested.connect(owner._on_open_book_consistency)
    # Wire action builder so _page_actions_for can delegate to the registry.
    dashboard._action_builder = lambda: _dashboard_actions(dashboard, owner)


def _dashboard_actions(dashboard: Any, owner: Any) -> list[tuple[str, Any]]:
    selected = dashboard.selected_project()
    if selected is not None:
        if selected.mode == "long" and selected.init_resume_available:
            return [
                (
                    UIStrings.ACTION_CONTINUE_INIT,
                    lambda: owner._focus_long_init_project(selected.project_id),
                ),
                (
                    UIStrings.ACTION_READ_PROJECT,
                    lambda: owner._view_project_in_reader(selected.project_id),
                ),
            ]
        if selected.mode == "long":
            return [
                (
                    UIStrings.ACTION_CONTINUE_VOLUME,
                    lambda: owner._focus_chapter_studio(
                        selected.project_id, selected.next_chapter or 1
                    ),
                ),
                (
                    UIStrings.ACTION_READ_PROJECT,
                    lambda: owner._view_project_in_reader(selected.project_id),
                ),
            ]
        return [
            (
                UIStrings.ACTION_READ_PROJECT_ARROW,
                lambda: owner._view_project_in_reader(selected.project_id),
            ),
            (UIStrings.ACTION_GO_WORKFLOW, lambda: owner.switch_page("workflow")),
        ]
    return [
        (UIStrings.ACTION_START_WRITING, lambda: owner.switch_page("workflow")),
        (UIStrings.ACTION_GO_SETTINGS, lambda: owner.switch_page("settings")),
    ]


def _connect_projects(projects: Any, owner: Any) -> None:
    service = getattr(getattr(owner, "_job_manager", None), "app_jobs", None)
    if service is not None and hasattr(projects, "bind_authoring_service"):
        projects.bind_authoring_service(service)
    projects.compose_requested.connect(owner._focus_workflow)
    projects.navigate_requested.connect(owner.switch_page)
    projects.context_changed.connect(lambda: owner._refresh_top_actions_for("projects"))
    projects.context_changed.connect(owner._schedule_ui_session_save)
    projects.ui_state_changed.connect(owner._schedule_ui_session_save)
    projects.workspace_refresh_requested.connect(
        lambda: owner._schedule_workspace_refresh("projects", reason="projects_page")
    )
    projects._action_builder = lambda: _projects_actions(projects, owner)


def _projects_actions(projects: Any, owner: Any) -> list[tuple[str, Any]]:
    pid = projects.current_project_id()
    if pid and owner._snapshot and pid in owner._snapshot.details:
        detail = owner._snapshot.details[pid]
        if detail.mode == "long" and detail.init_resume_available:
            return [
                (
                    UIStrings.ACTION_CONTINUE_INIT,
                    lambda: owner._focus_long_init_project(pid),
                ),
                (UIStrings.ACTION_BACK_DASHBOARD, lambda: owner.switch_page("dashboard")),
            ]
        if detail.mode == "long":
            return [
                (
                    UIStrings.ACTION_CONTINUE_VOLUME,
                    lambda: owner._focus_chapter_studio(pid, detail.completed_chapters + 1),
                ),
                (UIStrings.ACTION_BACK_DASHBOARD, lambda: owner.switch_page("dashboard")),
            ]
        return [
            (UIStrings.ACTION_BACK_DASHBOARD, lambda: owner.switch_page("dashboard")),
            (UIStrings.ACTION_GO_WORKFLOW, lambda: owner.switch_page("workflow")),
        ]
    return [
        (UIStrings.ACTION_BACK_DASHBOARD, lambda: owner.switch_page("dashboard")),
        (UIStrings.ACTION_GO_WORKFLOW, lambda: owner.switch_page("workflow")),
    ]


def _connect_workflow(workflow: Any, owner: Any) -> None:
    workflow.short_requested.connect(owner._dispatch_job)
    workflow.init_long_requested.connect(owner._dispatch_job)
    workflow.init_long_autorun_requested.connect(owner._dispatch_init_long_autorun)
    workflow.init_long_copilot_requested.connect(owner._dispatch_job)
    workflow.cancel_init_requested.connect(owner._cancel_init_job)
    workflow.chapter_studio_requested.connect(owner._focus_chapter_studio)
    workflow.open_root_requested.connect(owner._open_storage_root)
    workflow.open_project_requested.connect(owner._open_project_folder)
    workflow.view_project_requested.connect(owner._view_project_in_reader)
    workflow.context_changed.connect(lambda: owner._refresh_top_actions_for("workflow"))
    workflow.ui_state_changed.connect(owner._schedule_ui_session_save)
    workflow.workspace_refresh_requested.connect(
        lambda: owner._schedule_workspace_refresh("projects", reason="workflow_page")
    )
    workflow.clear_task_flow_requested.connect(owner._clear_workflow_task_flow)
    workflow.task_flow_error_logs_resolved.connect(owner._mark_task_flow_error_logs_resolved)
    workflow.init_repair_retry_requested.connect(owner._retry_init_repair)
    workflow.task_focus_decision_selected.connect(owner._provide_task_decision)
    workflow.task_focus_expand_requested.connect(
        lambda: owner._open_task_focus_dialog(focus_stream_detail=True)
    )
    workflow.restart_task_flow_cleanup_requested.connect(owner._clear_restarted_init_task_flow)
    workflow._action_builder = lambda: _workflow_actions(workflow, owner)


def _workflow_actions(workflow: Any, owner: Any) -> list[tuple[str, Any]]:
    if hasattr(workflow, "is_ui_ready") and not workflow.is_ui_ready():
        return []
    if workflow.current_mode() == "short":
        return [
            (UIStrings.ACTION_EXPORT_SHORT_TEMPLATE, workflow.export_active_template),
            (UIStrings.ACTION_SWITCH_TO_LONG, workflow.focus_long_init),
        ]
    if workflow.current_long_mode() == "init":
        return [
            (UIStrings.ACTION_EXPORT_LONG_TEMPLATE, workflow.export_active_template),
            (
                UIStrings.ACTION_GO_CHAPTER_STUDIO,
                lambda: owner.switch_page("chapter_studio"),
            ),
        ]
    return [
        (
            UIStrings.ACTION_GO_CHAPTER_STUDIO_PLAIN,
            lambda: owner.switch_page("chapter_studio"),
        ),
        (UIStrings.ACTION_EXPORT_LONG_TEMPLATE, workflow.export_active_template),
    ]


def _connect_settings(settings: Any, owner: Any) -> None:
    settings.mock_mode_toggled.connect(owner._handle_mock_toggled)
    settings.open_root_requested.connect(owner._open_storage_root)
    settings.settings_saved.connect(owner._on_settings_saved)
    settings.ui_state_changed.connect(owner._schedule_ui_session_save)
    settings._action_builder = lambda: [
        ("保存设置", settings.save_with_feedback),
        ("导入配置文件", owner._import_profiles_config),
        ("导出配置文件", owner._export_profiles_config),
    ]


def _connect_chapter_studio(chapter_studio: Any, owner: Any) -> None:
    manager = getattr(owner, "_job_manager", None)
    if hasattr(chapter_studio, "set_engine_owned_autorun") and hasattr(
        manager, "start_chapter_autorun"
    ):
        chapter_studio.set_engine_owned_autorun(True)
    chapter_studio.prepare_requested.connect(owner._dispatch_job)
    chapter_studio.resolve_requested.connect(owner._dispatch_job)
    chapter_studio.chapter_context_requested.connect(owner._bind_chapter_studio_context)
    chapter_studio.open_project_requested.connect(owner._open_project_folder)
    chapter_studio.view_project_requested.connect(owner._view_project_in_reader)
    chapter_studio.navigate_requested.connect(owner.switch_page)
    chapter_studio.context_changed.connect(lambda: owner._refresh_top_actions_for("chapter_studio"))
    chapter_studio.ui_state_changed.connect(owner._schedule_ui_session_save)
    chapter_studio.auto_advance_requested.connect(owner._auto_advance_chapter)
    chapter_studio.auto_pilot_started.connect(owner._on_project_autorun_started)
    chapter_studio.auto_pilot_stopped.connect(owner._on_project_autorun_stopped)
    chapter_studio.cancel_job_requested.connect(owner._on_stop_auto_pilot)
    chapter_studio.repair_continuity_requested.connect(owner._dispatch_job)
    chapter_studio.repair_causal_requested.connect(owner._dispatch_job)
    chapter_studio.repair_issues_requested.connect(owner._dispatch_job)
    chapter_studio.reevaluate_requested.connect(owner._dispatch_job)
    chapter_studio.polish_requested.connect(owner._dispatch_job)
    chapter_studio.book_consistency_requested.connect(owner._dispatch_job)
    chapter_studio.export_requested.connect(owner._dispatch_job)
    chapter_studio.reextract_relationships_requested.connect(owner._dispatch_job)
    chapter_studio.repair_motif_history_requested.connect(owner._dispatch_job)
    chapter_studio.clear_task_flow_requested.connect(owner._clear_chapter_task_flow)
    chapter_studio.clean_chapters_flow_requested.connect(owner._clear_chapter_range_task_flow)
    chapter_studio.task_focus_decision_selected.connect(owner._provide_task_decision)
    chapter_studio.task_focus_expand_requested.connect(
        lambda: owner._open_task_focus_dialog(focus_stream_detail=True)
    )
    chapter_studio.workspace_refresh_requested.connect(
        lambda: owner._schedule_workspace_refresh("chapters", reason="chapter_studio")
    )
    chapter_studio._action_builder = lambda: _chapter_studio_actions(chapter_studio, owner)


def _chapter_studio_actions(studio: Any, owner: Any) -> list[tuple[str, Any]]:
    if hasattr(studio, "is_ui_ready") and not studio.is_ui_ready():
        return []
    primary_label = UIStrings.ACTION_CONTINUE_CURRENT_NODE
    label_getter = getattr(studio, "primary_action_label", None)
    if callable(label_getter):
        primary_label = str(label_getter() or primary_label)
    return [
        (primary_label, studio.trigger_primary_action),
        (
            UIStrings.ACTION_OPEN_FOLDER,
            lambda: owner._open_project_folder(studio.current_project_id()),
        ),
    ]


# ── Built-in page registrations ────────────────────────────────────────

page_registry.register(
    "dashboard",
    DashboardPage,
    title="先定手头所重",
    subtitle="诸卷总领、任务行止与通路火候，皆陈于案头。",
    eyebrow="案头",
    label="案头",
    sections=frozenset({"projects", "metrics", "overview", "featured_project"}),
    signal_connector=_connect_dashboard,
)

page_registry.register(
    "projects",
    ProjectsPage,
    title="卷帙总览",
    subtitle="设定、契约、章节与报告分层归档，查阅时少绕路。",
    eyebrow="卷帙",
    label="卷帙",
    cold_instant=True,
    sections=frozenset({"projects", "details"}),
    signal_connector=_connect_projects,
)

page_registry.register(
    "workflow",
    lambda: WorkflowPage(defer_sections=True),
    title="把任务调度清楚",
    subtitle="在同一页完成短篇快启、长篇立项与章节续写。",
    eyebrow="机杼",
    label="机杼",
    cold_instant=True,
    sections=frozenset({"projects", "overview"}),
    signal_connector=_connect_workflow,
)

page_registry.register(
    "settings",
    lambda: SettingsPage(eager_build=False),
    title="读懂当前运行环境",
    subtitle="确认工作区、默认通路、Provider 和模式是否都在正确位置。",
    eyebrow="火候",
    label="火候",
    transition_profile="instant",
    cold_instant=True,
    sections=frozenset({"providers", "overview"}),
    signal_connector=_connect_settings,
)

page_registry.register(
    "chapter_studio",
    lambda: ChapterStudioPage(defer_sections=True),
    title="把章节工作放到一张台面上",
    subtitle="上一章结果、本章目标、下一章预埋点与 AI 决策，俱在章台同看。",
    eyebrow="章台",
    label="章台",
    cold_instant=True,
    sections=frozenset({"projects", "details"}),
    signal_connector=_connect_chapter_studio,
)


def _connect_voice_studio(voice_studio: Any, owner: Any) -> None:
    voice_studio.project_changed.connect(owner._open_project_folder)
    voice_studio.project_selector_changed.connect(owner._on_voice_studio_project_selected)
    voice_studio.ui_state_changed.connect(owner._schedule_ui_session_save)
    voice_studio.task_focus_decision_selected.connect(owner._provide_task_decision)
    voice_studio.task_focus_expand_requested.connect(
        lambda: owner._open_task_focus_dialog(focus_stream_detail=True)
    )


def _create_voice_studio() -> Any:
    from novel_forge.core.config import get_settings
    from novel_forge.desktop.pages.voice_studio.page import VoiceStudioPage

    # Reuse the process-wide settings snapshot. Constructing a fresh
    # Settings model here re-parses the full environment on the GUI thread
    # during the first navigation to 声腔.
    return VoiceStudioPage(settings=get_settings())


page_registry.register(
    "voice_studio",
    _create_voice_studio,
    title="AI 配音工作室",
    subtitle="配音团队管理、脚本预览、语音合成与音频导出。",
    eyebrow="声腔",
    label="声腔",
    cold_instant=True,
    sections=frozenset({"projects"}),
    signal_connector=_connect_voice_studio,
)
