"""Desktop workflow page composed from smaller workflow components."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QTimer, Signal
from PySide6.QtGui import QHideEvent, QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLineEdit,
    QSizePolicy,
    QSpinBox,
    QTextEdit,
    QWidget,
)

from novel_forge.desktop.components.task_focus import TaskFocusPanel
from novel_forge.desktop.jobs import WORKFLOW_JOB_KINDS, DesktopJobRecord, DesktopJobState
from novel_forge.desktop.pages.workflow.artifacts import StepArtifactDialog
from novel_forge.desktop.pages.workflow.components import (
    CurrentPageStack,
    JobCard,
    LongPanel,
    ModeBar,
)
from novel_forge.desktop.pages.workflow.forms import ShortForm
from novel_forge.desktop.pages.workflow.widgets import (
    ActiveProjectsPanel,
    HeroPanel,
    JobsPanel,
)
from novel_forge.desktop.task_observation import TaskFocusScope, TaskObservationStore
from novel_forge.desktop.ui_perf import ui_perf_span
from novel_forge.desktop.widgets import EmptyState, ScrollPage
from novel_forge.desktop.workspace import DesktopWorkspaceSnapshot
from novel_forge.workspace.projects import (
    is_generated_test_project_id,
    should_list_project_dir,
)

_log = logging.getLogger(__name__)

_AUTOSAVE_INTERVAL_MS = 30_000  # 30 s
_DRAFT_AUTOSAVE_DEBOUNCE_MS = 750
_DEFERRED_BUILD_INTERVAL_MS = 8


class WorkflowPage(ScrollPage):
    """Thin assembly page for short-form, long-form, and job feed workflows."""

    short_requested = Signal(object)
    init_long_requested = Signal(object)
    init_long_autorun_requested = Signal(
        object
    )  # request — create project then start chapter auto-run
    init_long_copilot_requested = Signal(object)
    cancel_init_requested = Signal(str)  # job_id
    chapter_studio_requested = Signal(str, int)
    open_root_requested = Signal()
    open_project_requested = Signal(str)
    view_project_requested = Signal(str)
    context_changed = Signal()
    workspace_refresh_requested = Signal()
    clear_task_flow_requested = Signal(object)  # job_ids
    task_flow_error_logs_resolved = Signal(object)  # {job_id: [entry_id, ...]}
    init_repair_retry_requested = Signal(str, str, bool)  # job_id, project_id, reset history
    restart_task_flow_cleanup_requested = Signal(str)  # project_id
    task_focus_decision_selected = Signal(str, str, str, str)
    task_focus_expand_requested = Signal()
    ui_state_changed = Signal()

    def __init__(self, *, defer_sections: bool = False) -> None:
        super().__init__()
        self._defer_sections = defer_sections
        self._ui_ready = False
        self._snapshot: DesktopWorkspaceSnapshot | None = None
        self._mock_enabled = False
        self._draft_dir: Path | None = None
        self._draft_restored = False
        self._height_sync_pending = False
        self._dynamic_height_watchers: list[QObject] = []
        self._task_observation_store: TaskObservationStore | None = None
        self._jobs_binding_signature: tuple[object, ...] | None = None
        self._pending_workspace_full_bind = False
        self._pending_workspace_sections: set[str] = set()
        self._pending_jobs: list[DesktopJobRecord] | None = None
        self._pending_focus_request: tuple[str, str, int | None] | None = None
        self._deferred_build_index = 0
        self._deferred_placeholder: QWidget | None = None
        self._deferred_build_timer = QTimer(self)
        self._deferred_build_timer.setSingleShot(True)
        self._deferred_build_timer.timeout.connect(self._run_deferred_build_step)
        self._mode_context_changed_slot = lambda _mode: self.context_changed.emit()
        self._long_context_changed_slot = lambda: self.context_changed.emit()
        self._draft_autosave_timer = QTimer(self)
        self._draft_autosave_timer.setSingleShot(True)
        self._draft_autosave_timer.setInterval(_DRAFT_AUTOSAVE_DEBOUNCE_MS)
        self._draft_autosave_timer.timeout.connect(self._save_debounced_draft)
        self._draft_autosave_wired = False
        if defer_sections:
            self._deferred_placeholder = EmptyState(
                "正在准备机杼",
                "正在铺开创作表单、项目入口与任务流…",
            )
            self.body_layout.addWidget(self._deferred_placeholder)
            self._deferred_build_timer.start(0)
        else:
            self._build_ui()
            self._ui_ready = True

        # Periodic auto-save timer (saves drafts silently every 30 s)
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(_AUTOSAVE_INTERVAL_MS)
        self._autosave_timer.timeout.connect(self._do_autosave)
        self._autosave_timer.start()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if (
            self._defer_sections
            and not self._ui_ready
            and not self._deferred_build_timer.isActive()
            and not getattr(self, "_shutdown_done", False)
        ):
            self._deferred_build_timer.start(0)

    def hideEvent(self, event: QHideEvent) -> None:
        """Pause staged widget construction while another page is visible."""
        host_window = self.window()
        if host_window is self or host_window.isVisible():
            self._deferred_build_timer.stop()
        super().hideEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_mode_stack"):
            self._request_dynamic_height_sync()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if getattr(self, "_shutdown_done", False):
            return super().eventFilter(watched, event)
        if not getattr(self, "_ui_ready", False):
            return super().eventFilter(watched, event)
        if event.type() in {
            QEvent.Type.LayoutRequest,
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.Hide,
        }:
            self._request_dynamic_height_sync()
        return super().eventFilter(watched, event)

    def shutdown(self) -> None:
        """Stop autosave timer and disconnect all signals on app exit."""
        if getattr(self, "_shutdown_done", False):
            return
        self._shutdown_done = True
        self._deferred_build_timer.stop()

        for widget in self._dynamic_height_watchers:
            try:
                widget.removeEventFilter(self)
            except (RuntimeError, TypeError):
                pass
        self._dynamic_height_watchers.clear()
        self._height_sync_pending = False

        self._autosave_timer.stop()
        self._draft_autosave_timer.stop()
        if not self._ui_ready:
            if hasattr(self, "_short_form"):
                self._short_form._toolbar.shutdown()
            if hasattr(self, "_long_panel"):
                self._long_panel.shutdown()
            if hasattr(self, "_task_focus_panel"):
                self._task_focus_panel.shutdown()
            return
        self._short_form._toolbar.shutdown()
        self._long_panel.shutdown()

        # Hero panel signals
        try:
            self._hero_panel.view_project_clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        try:
            self._hero_panel.goto_studio_clicked.disconnect()
        except (RuntimeError, TypeError):
            pass

        # Active projects panel signals
        try:
            self._active_projects_panel.project_resume_requested.disconnect()
        except (RuntimeError, TypeError):
            pass

        # Mode bar signals
        try:
            self._mode_bar.mode_changed.disconnect(self._on_mode_changed)
        except (RuntimeError, TypeError):
            pass
        try:
            self._mode_bar.mode_changed.disconnect(self._mode_context_changed_slot)
        except (RuntimeError, TypeError):
            pass
        try:
            self._long_panel.context_changed.disconnect(self.ui_state_changed)
        except (RuntimeError, TypeError):
            pass

        # Short form signals
        try:
            self._short_form.submitted.disconnect()
        except (RuntimeError, TypeError):
            pass

        # Long panel signals
        try:
            self._long_panel.init_long_requested.disconnect()
        except (RuntimeError, TypeError):
            pass
        try:
            self._long_panel.init_long_autorun_requested.disconnect()
        except (RuntimeError, TypeError):
            pass
        try:
            self._long_panel.init_long_copilot_requested.disconnect()
        except (RuntimeError, TypeError):
            pass
        try:
            self._long_panel.cancel_init_requested.disconnect()
        except (RuntimeError, TypeError):
            pass
        try:
            self._long_panel.chapter_studio_requested.disconnect()
        except (RuntimeError, TypeError):
            pass
        try:
            self._long_panel.open_project_requested.disconnect()
        except (RuntimeError, TypeError):
            pass
        try:
            self._long_panel.context_changed.disconnect(self._long_context_changed_slot)
        except (RuntimeError, TypeError):
            pass
        try:
            self._long_panel.workspace_refresh_requested.disconnect()
        except (RuntimeError, TypeError):
            pass
        try:
            self._long_panel.restart_task_flow_cleanup_requested.disconnect()
        except (RuntimeError, TypeError):
            pass

        # Jobs panel signals
        try:
            self._jobs_panel.clear_task_flow_requested.disconnect()
        except (RuntimeError, TypeError):
            pass
        try:
            self._jobs_panel.task_flow_error_logs_resolved.disconnect()
        except (RuntimeError, TypeError):
            pass
        try:
            self._jobs_panel.stop_job_requested.disconnect()
        except (RuntimeError, TypeError):
            pass
        try:
            self._jobs_panel.resume_job_requested.disconnect()
        except (RuntimeError, TypeError):
            pass
        try:
            self._jobs_panel.checkpoint_resume_requested.disconnect()
        except (RuntimeError, TypeError):
            pass
        try:
            self._jobs_panel.init_repair_retry_requested.disconnect()
        except (RuntimeError, TypeError):
            pass
        if hasattr(self, "_task_focus_panel"):
            self._task_focus_panel.shutdown()

    def _build_ui(self) -> None:
        for build_step in self._ui_build_steps():
            build_step()
        self._finish_ui_build()

    def _ui_build_steps(self) -> tuple[Callable[[], None], ...]:
        return (
            self._build_hero_section,
            self._build_active_projects_section,
            self._build_mode_bar_section,
            self._build_short_form_section,
            self._build_long_panel_section,
            self._build_mode_stack_section,
            self._build_jobs_section,
            self._build_task_focus_section,
        )

    def _run_deferred_build_step(self) -> None:
        if getattr(self, "_shutdown_done", False) or self._ui_ready:
            return
        parent = self.parentWidget()
        current_widget = getattr(parent, "currentWidget", None)
        is_current_stack_page = callable(current_widget) and current_widget() is self
        if not self.isVisible() and not is_current_stack_page:
            return
        build_steps = self._ui_build_steps()
        if self._deferred_build_index >= len(build_steps):
            self._finish_ui_build()
            return
        build_step = build_steps[self._deferred_build_index]
        with ui_perf_span(
            "workflow.deferred_section",
            section=build_step.__name__,
        ):
            build_step()
        self._deferred_build_index += 1
        self._deferred_build_timer.start(_DEFERRED_BUILD_INTERVAL_MS)

    def _build_hero_section(self) -> None:
        self._hero_panel = HeroPanel()
        self._hero_panel.view_project_clicked.connect(self._emit_view_current_project)
        self._hero_panel.goto_studio_clicked.connect(self._emit_goto_studio)
        self._add_staged_widget(self._hero_panel)
        self._hero_panel.fade_in()

    def _add_staged_widget(self, widget: QWidget) -> None:
        placeholder = self._deferred_placeholder
        if placeholder is None:
            self.body_layout.addWidget(widget)
            return
        placeholder_index = self.body_layout.indexOf(placeholder)
        self.body_layout.insertWidget(max(0, placeholder_index), widget)

    def _build_active_projects_section(self) -> None:
        self._active_projects_panel = ActiveProjectsPanel()
        self._active_projects_panel.project_resume_requested.connect(self._resume_project)
        self._add_staged_widget(self._active_projects_panel)

    def _build_mode_bar_section(self) -> None:
        self._mode_bar = ModeBar()
        self._mode_bar.mode_changed.connect(self._on_mode_changed)
        self._mode_bar.mode_changed.connect(self._mode_context_changed_slot)
        self._add_staged_widget(self._mode_bar)

    def _build_short_form_section(self) -> None:
        self._short_form = ShortForm()
        self._short_form.submitted.connect(self.short_requested)

    def _build_long_panel_section(self) -> None:
        self._long_panel = LongPanel()
        self._long_panel.init_long_requested.connect(self.init_long_requested)
        self._long_panel.init_long_autorun_requested.connect(self.init_long_autorun_requested)
        self._long_panel.init_long_copilot_requested.connect(self.init_long_copilot_requested)
        self._long_panel.cancel_init_requested.connect(self.cancel_init_requested)
        self._long_panel.chapter_studio_requested.connect(self.chapter_studio_requested)
        self._long_panel.open_project_requested.connect(self.open_project_requested)
        self._long_panel.context_changed.connect(self._long_context_changed_slot)
        self._long_panel.context_changed.connect(self.ui_state_changed)
        self._long_panel.workspace_refresh_requested.connect(self.workspace_refresh_requested)
        self._long_panel.restart_task_flow_cleanup_requested.connect(
            self.restart_task_flow_cleanup_requested
        )

    def _build_mode_stack_section(self) -> None:
        self._mode_stack = CurrentPageStack()
        self._mode_stack.setObjectName("workflowModeStack")
        self._mode_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._mode_stack.addWidget(self._short_form)
        self._mode_stack.addWidget(self._long_panel)
        self._add_staged_widget(self._mode_stack)

    def _build_jobs_section(self) -> None:
        self._jobs_panel = JobsPanel()
        self._jobs_panel.clear_task_flow_requested.connect(self.clear_task_flow_requested)
        self._jobs_panel.task_flow_error_logs_resolved.connect(self.task_flow_error_logs_resolved)
        self._jobs_panel.stop_job_requested.connect(self._on_stop_job)
        self._jobs_panel.resume_job_requested.connect(self._on_resume_job)
        self._jobs_panel.checkpoint_resume_requested.connect(self._on_checkpoint_resume)
        self._jobs_panel.init_repair_retry_requested.connect(self.init_repair_retry_requested)
        self._add_staged_widget(self._jobs_panel)

    def _build_task_focus_section(self) -> None:
        self._task_focus_panel = TaskFocusPanel(
            TaskFocusScope.WORKFLOW, title="机杼关注", compact=True
        )
        self._task_focus_panel.decision_selected.connect(self.task_focus_decision_selected)
        self._task_focus_panel.expand_requested.connect(self.task_focus_expand_requested)
        self._task_focus_panel.artifact_requested.connect(self._on_task_focus_artifact_requested)
        self._add_staged_widget(self._task_focus_panel)

    def _finish_ui_build(self) -> None:
        if self._ui_ready:
            return
        placeholder = self._deferred_placeholder
        if placeholder is not None:
            self.body_layout.removeWidget(placeholder)
            placeholder.deleteLater()
            self._deferred_placeholder = None
        self.body_layout.addStretch()
        self._install_dynamic_height_watchers()
        self._ui_ready = True
        self._on_mode_changed(self._mode_bar.current_mode())
        self._wire_debounced_draft_autosave()
        self._replay_pending_bindings()

    def _replay_pending_bindings(self) -> None:
        snapshot = self._snapshot
        pending_full_bind = self._pending_workspace_full_bind
        pending_sections = frozenset(self._pending_workspace_sections)
        pending_jobs = self._pending_jobs
        pending_focus = self._pending_focus_request
        self._pending_workspace_full_bind = False
        self._pending_workspace_sections.clear()
        self._pending_jobs = None
        self._pending_focus_request = None

        if snapshot is not None:
            if pending_full_bind:
                self.bind_workspace(snapshot)
            elif pending_sections:
                self.bind_workspace_sections(snapshot, pending_sections)
        if self._task_observation_store is not None:
            self.bind_task_observation_store(self._task_observation_store)
        if pending_jobs is not None:
            self.bind_jobs(pending_jobs)
        if pending_focus is not None:
            action, project_id, chapter_number = pending_focus
            if action == "project":
                self.focus_project(project_id, chapter_number)
            elif action == "short":
                self.focus_short_create()
            elif action == "long_init":
                self.focus_long_init()
            elif action == "long_init_project":
                self.focus_long_init_project(project_id)
            elif action == "long_chapter":
                self.focus_long_chapter()
        self.context_changed.emit()

    def _resume_project(self, project_id: str, mode: str, next_chapter: int) -> None:
        """Handle project resume request from active projects panel."""
        if not self._ui_ready:
            action = "long_init_project" if mode == "long" else "short"
            self._pending_focus_request = (action, project_id, next_chapter)
            self.open_project_requested.emit(project_id)
            return
        if mode == "long":
            self._mode_bar.select("long")
            self._long_panel.focus_init_project(project_id)
        else:
            self._mode_bar.select("short")
        self.open_project_requested.emit(project_id)

    def _on_checkpoint_resume(self, job_id: str, project_id: str, chapter_number: int) -> None:
        """Handle checkpoint resume request from a failed job card."""
        if project_id:
            self.chapter_studio_requested.emit(project_id, chapter_number)

    def _on_task_focus_artifact_requested(
        self,
        project_id: str,
        job_kind: str,
        step_key: str,
        step_label: str,
        chapter_number: int,
    ) -> None:
        project_dir = (
            self._snapshot.storage_root / project_id
            if self._snapshot is not None and project_id
            else None
        )
        nav_target = StepArtifactDialog.show_for_step(
            kind=job_kind,
            step_key=step_key,
            step_label=step_label or step_key,
            project_dir=project_dir,
            chapter_number=chapter_number,
            parent=self.window(),
        )
        if nav_target:
            win = self.window()
            if hasattr(win, "switch_page"):
                win.switch_page(nav_target)

    def _on_mode_changed(self, mode: str) -> None:
        if not self._ui_ready:
            return
        is_short = mode == "short"
        self._mode_stack.setCurrentWidget(self._short_form if is_short else self._long_panel)
        self._sync_task_flow_preferred_width()
        self._update_hero_buttons()
        self._sync_dynamic_height()
        self.ui_state_changed.emit()

    def export_ui_state(self) -> dict[str, object]:
        """Persist navigation choices; form values are stored in draft files."""
        if not self._ui_ready:
            return {"version": 1}
        return {
            "version": 1,
            "mode": self._mode_bar.current_mode(),
            "long": self._long_panel.export_ui_state(),
        }

    def restore_ui_state(self, payload: object) -> None:
        """Restore selected workflow panes without overwriting saved drafts."""
        if not isinstance(payload, dict):
            return
        if not self._ui_ready:
            return
        mode = str(payload.get("mode") or "short")
        self._mode_bar.select("long" if mode == "long" else "short")
        self._long_panel.restore_ui_state(payload.get("long"))

    def _sync_task_flow_preferred_width(self) -> None:
        current_form = self._mode_stack.currentWidget()
        if current_form is None:
            return
        layout = current_form.layout()
        if layout is not None:
            layout.activate()
        self._jobs_panel.set_preferred_width(current_form.sizeHint().width())

    def _install_dynamic_height_watchers(self) -> None:
        for widget in (
            self._mode_stack,
            self._short_form,
            self._long_panel,
            self._long_panel._stack,
            self._long_panel._init_form,
            self._long_panel._init_form._blueprint_preferences_section,
            self._jobs_panel,
            self._task_focus_panel,
        ):
            widget.installEventFilter(self)
            self._dynamic_height_watchers.append(widget)

    def _request_dynamic_height_sync(self) -> None:
        if self._height_sync_pending:
            return
        self._height_sync_pending = True
        QTimer.singleShot(0, self._run_dynamic_height_sync)

    def _run_dynamic_height_sync(self) -> None:
        self._height_sync_pending = False
        if getattr(self, "_shutdown_done", False):
            return
        try:
            self._sync_dynamic_height_now()
        except RuntimeError:
            return

    def _sync_dynamic_height(self) -> None:
        self._sync_dynamic_height_now()
        self._request_dynamic_height_sync()

    def _sync_dynamic_height_now(self) -> None:
        self._sync_stack_height_caps()
        self.sync_body_height()

    def _sync_stack_height_caps(self) -> None:
        widgets = (
            self._mode_stack.currentWidget(),
            self._long_panel._stack.currentWidget(),
            self._long_panel._stack,
            self._long_panel,
            self._mode_stack,
        )
        for widget in widgets:
            if widget is None:
                continue
            layout = widget.layout()
            if layout is not None:
                layout.activate()
            target_height = max(widget.minimumSizeHint().height(), widget.sizeHint().height())
            widget.setMaximumHeight(target_height)
            widget.updateGeometry()

    def _update_hero_buttons(self) -> None:
        if not self._ui_ready:
            return
        project_id = ""
        if self._mode_bar.current_mode() == "long":
            project_id = self._long_panel.chapter_form_project_id()
        has_project = bool(project_id)
        self._hero_panel.set_buttons_enabled(has_project)

    def bind_workspace(self, snapshot: DesktopWorkspaceSnapshot) -> None:
        self._snapshot = snapshot
        if not self._ui_ready:
            self._pending_workspace_full_bind = True
            self._pending_workspace_sections.update({"projects", "overview"})
            return
        self._short_form.set_storage_root(snapshot.storage_root)
        self._long_panel.set_storage_root(snapshot.storage_root)
        self._long_panel.bind_snapshot(snapshot)
        self._active_projects_panel.render_snapshot(snapshot)
        self._update_hero_buttons()
        self.context_changed.emit()
        self._sync_dynamic_height()

        # Set draft directory and restore drafts on first bind, or when a clean
        # form follows a storage-root change.
        new_draft_dir = snapshot.storage_root / ".presets" / ".draft"
        draft_dir_changed = self._draft_dir != new_draft_dir
        should_restore = not self._draft_restored or (
            draft_dir_changed and not self.has_unsaved_changes()
        )
        self._draft_dir = new_draft_dir
        if should_restore:
            self._restore_drafts()
            self._draft_restored = True

    def bind_workspace_sections(
        self, snapshot: DesktopWorkspaceSnapshot, sections: frozenset[str]
    ) -> None:
        """Incremental bind — only re-render widgets for changed *sections*.

        Section mapping (matches ``_PAGE_SECTION_MAP["workflow"]``):
        - ``"projects"``: active projects panel
        - ``"overview"``: hero panel, forms, long panel, hero buttons, draft restore
        """
        self._snapshot = snapshot
        if not getattr(self, "_ui_ready", True):
            self._pending_workspace_sections.update(sections)
            return

        if "projects" in sections:
            self._active_projects_panel.render_snapshot(snapshot)

        if "overview" in sections:
            self._short_form.set_storage_root(snapshot.storage_root)
            self._long_panel.set_storage_root(snapshot.storage_root)
            self._long_panel.bind_snapshot(snapshot)
            self._update_hero_buttons()
            self.context_changed.emit()
            self._sync_dynamic_height()

            # Set draft directory and restore drafts on first bind, or when a
            # clean form follows a storage-root change.
            new_draft_dir = snapshot.storage_root / ".presets" / ".draft"
            draft_dir_changed = self._draft_dir != new_draft_dir
            should_restore = not self._draft_restored or (
                draft_dir_changed and not self.has_unsaved_changes()
            )
            self._draft_dir = new_draft_dir
            if should_restore:
                self._restore_drafts()
                self._draft_restored = True

    def _draft_path(self, mode: str) -> Path | None:
        return (self._draft_dir / f"_autosave_{mode}.json") if self._draft_dir else None

    def _restore_drafts(self) -> None:
        short_path = self._draft_path("short")
        if short_path:
            self._short_form.restore_draft(short_path)
        long_path = self._draft_path("long")
        if long_path:
            # Only restore long draft if LongInitForm hasn't already loaded a project
            self._long_panel.restore_init_draft(long_path)

    def _do_autosave(self) -> None:
        """Silently save form drafts (called by timer)."""
        if not self.has_unsaved_changes():
            return
        short_path = self._draft_path("short")
        if short_path:
            self._short_form.save_draft(short_path)
        long_path = self._draft_path("long")
        if long_path:
            self._long_panel.save_init_draft(long_path)

    def _wire_debounced_draft_autosave(self) -> None:
        """Persist form edits shortly after typing instead of only every 30 seconds."""
        if self._draft_autosave_wired or not self._ui_ready:
            return
        self._draft_autosave_wired = True

        def _queue(*_args: object) -> None:
            if self._draft_dir is not None:
                self._draft_autosave_timer.start()

        for form in (self._short_form, self._long_panel._init_form):  # noqa: SLF001
            for widget in form.findChildren(QLineEdit):
                widget.textChanged.connect(_queue)
            for widget in form.findChildren(QTextEdit):
                widget.textChanged.connect(_queue)
            for widget in form.findChildren(QComboBox):
                widget.currentIndexChanged.connect(_queue)
            for widget in form.findChildren(QSpinBox):
                widget.valueChanged.connect(_queue)
            for widget in form.findChildren(QCheckBox):
                widget.toggled.connect(_queue)

    def _save_debounced_draft(self) -> None:
        """Flush the coalesced form edit, including an inactive form just edited."""
        if self._draft_dir is None:
            return
        self.save_pending_changes()

    def save_pending_changes(self) -> bool:
        """Persist drafts to disk. Called by window on "保存并退出". Returns True."""
        if not self._ui_ready:
            return False
        saved_any = False
        short_path = self._draft_path("short")
        if short_path:
            ok = self._short_form.save_draft(short_path)
            saved_any = saved_any or ok
        long_path = self._draft_path("long")
        if long_path:
            ok = self._long_panel.save_init_draft(long_path)
            saved_any = saved_any or ok
        return saved_any

    def supports_job_binding(self) -> bool:
        return True

    def is_ui_ready(self) -> bool:
        """Return whether all staged workflow sections are available."""
        return self._ui_ready

    def on_jobs_changed(self, jobs: list[DesktopJobRecord]) -> None:
        self.bind_jobs(jobs)

    def bind_task_observation_store(self, store: TaskObservationStore) -> None:
        self._task_observation_store = store
        if not self._ui_ready:
            return
        self._task_focus_panel.bind_store(store)
        self._task_focus_panel.set_scope(TaskFocusScope.WORKFLOW)

    def bind_jobs(self, jobs: list[DesktopJobRecord]) -> None:
        if not self._ui_ready:
            self._pending_jobs = list(jobs)
            return
        storage_root = self._snapshot.storage_root if self._snapshot else None
        visible_jobs = [job for job in jobs if self._is_visible_task_flow_job(job)]

        # The task observation store is updated by the window before this
        # method runs.  Skip the expensive form-height sync and progress
        # updates when the incoming job event cannot change visible workflow
        # content.  JobCard owns the same visual signature used by JobsPanel.
        binding_signature = (
            tuple(JobCard._signature_for(job) for job in visible_jobs),
            self._long_panel.init_form_project_id(),
            self._long_panel.chapter_form_project_id(),
        )
        if binding_signature == self._jobs_binding_signature:
            return
        self._jobs_binding_signature = binding_signature
        workflow_jobs = [job for job in visible_jobs if job.kind in WORKFLOW_JOB_KINDS]
        active_project_ids = {
            str(job.project_id or "").strip()
            for job in visible_jobs
            if job.status in {DesktopJobState.QUEUED, DesktopJobState.RUNNING}
        }
        active_project_ids.discard("")
        self._jobs_panel.render_jobs(workflow_jobs, storage_root, active_project_ids)

        # Update form progress
        short_job: DesktopJobRecord | None = None
        init_long_job: DesktopJobRecord | None = None
        chapter_job: DesktopJobRecord | None = None
        chapter_job_fallback: DesktopJobRecord | None = None
        init_target_project = self._long_panel.init_form_project_id()
        chapter_target_project = self._long_panel.chapter_form_project_id()
        for job in visible_jobs:
            if short_job is None and job.kind == "run_short":
                short_job = job
                continue
            if init_long_job is None and job.kind == "init_long":
                if init_target_project:
                    if job.project_id != init_target_project:
                        continue
                    if job.status == DesktopJobState.SUCCEEDED:
                        continue
                    init_long_job = job
                    continue
                if job.status in {DesktopJobState.QUEUED, DesktopJobState.RUNNING}:
                    init_long_job = job
                    continue
            if chapter_job is None and job.kind in {
                "prepare_chapter",
                "resolve_chapter_checkpoint",
                "resolve_chapter_checkpoint_finalize",
                "run_chapter",
            }:
                if chapter_target_project and job.project_id == chapter_target_project:
                    chapter_job = job
                elif chapter_job_fallback is None:
                    chapter_job_fallback = job
            if short_job and init_long_job and chapter_job:
                break
        self._short_form.update_progress(short_job)
        self._long_panel.update_init_progress(init_long_job)
        self._long_panel.update_chapter_progress(chapter_job or chapter_job_fallback)
        self._sync_dynamic_height()

    def _is_visible_task_flow_job(self, job: DesktopJobRecord) -> bool:
        project_id = str(job.project_id or "").strip()
        if not project_id:
            return True
        if is_generated_test_project_id(project_id) or project_id.casefold() == "dlq":
            return False

        if self._snapshot is None:
            return True

        visible_ids = {project.project_id for project in self._snapshot.projects}
        visible_ids.update(self._snapshot.details)
        if project_id in visible_ids:
            return True

        if job.status in {DesktopJobState.QUEUED, DesktopJobState.RUNNING}:
            return True

        project_dir = self._snapshot.storage_root / project_id
        if project_dir.exists():
            return should_list_project_dir(project_dir)
        return True

    def _on_stop_job(self, job_id: str) -> None:
        win = self.window()
        if hasattr(win, "_job_manager"):
            win._job_manager.cancel_job(job_id, reason="用户已取消")

    def _on_resume_job(self, job_id: str, project_id: str, chapter_number: int) -> None:
        if project_id:
            self.chapter_studio_requested.emit(project_id, chapter_number or 1)

    def set_mock_mode(self, enabled: bool) -> None:
        self._mock_enabled = enabled
        if self._snapshot is not None:
            self.bind_workspace(self._snapshot)

    def focus_project(self, project_id: str, chapter_number: int | None = None) -> None:
        if not self._ui_ready:
            self._pending_focus_request = ("project", project_id, chapter_number)
            return
        self._mode_bar.select("long")
        self._long_panel.focus_chapter(project_id, chapter_number)
        self._update_hero_buttons()

    def focus_short_create(self) -> None:
        if not self._ui_ready:
            self._pending_focus_request = ("short", "", None)
            return
        self._mode_bar.select("short")
        self._update_hero_buttons()

    def focus_long_init(self) -> None:
        if not self._ui_ready:
            self._pending_focus_request = ("long_init", "", None)
            return
        self._mode_bar.select("long")
        self._long_panel.select_init()
        self._update_hero_buttons()

    def focus_long_init_project(self, project_id: str) -> None:
        if not self._ui_ready:
            self._pending_focus_request = ("long_init_project", project_id, None)
            return
        self._mode_bar.select("long")
        self._long_panel.focus_init_project(project_id)
        self._update_hero_buttons()

    def focus_long_chapter(self) -> None:
        if not self._ui_ready:
            self._pending_focus_request = ("long_chapter", "", None)
            return
        self._mode_bar.select("long")
        self._long_panel.select_chapter()
        self._update_hero_buttons()

    def current_mode(self) -> str:
        if not self._ui_ready:
            return "short"
        return self._mode_bar.current_mode()

    def current_long_mode(self) -> str:
        if not self._ui_ready:
            return "init"
        return self._long_panel.current_mode()

    def export_active_template(self) -> None:
        if not self._ui_ready:
            return
        if self._mode_bar.current_mode() == "short":
            self._short_form.export_template()
            return
        self._long_panel.export_template()

    def prefill_latest_project(self) -> None:
        if not self._ui_ready:
            self._pending_focus_request = ("long_init", "", None)
            return
        self._mode_bar.select("long")
        self._long_panel.prefill_latest_project()
        self._update_hero_buttons()

    def current_project_id(self) -> str:
        if not self._ui_ready:
            return ""
        return self._long_panel.chapter_form_project_id()

    def current_chapter_number(self) -> int:
        if not self._ui_ready:
            return 1
        return self._long_panel.chapter_form_chapter_number()

    def _emit_open_current_project(self) -> None:
        if not self._ui_ready:
            return
        project_id = self._long_panel.chapter_form_project_id()
        if project_id:
            self.open_project_requested.emit(project_id)

    def _emit_view_current_project(self) -> None:
        if not self._ui_ready:
            return
        project_id = self._long_panel.chapter_form_project_id()
        if project_id:
            self.view_project_requested.emit(project_id)

    def _emit_goto_studio(self) -> None:
        if not self._ui_ready:
            return
        project_id = self._long_panel.chapter_form_project_id()
        chapter_num = self._long_panel.chapter_form_chapter_number()
        if project_id:
            self.chapter_studio_requested.emit(project_id, chapter_num or 1)

    def has_unsaved_changes(self) -> bool:
        # Only consider user-modified editable fields as unsaved changes.
        # This avoids false positives from prefilled/read-only values.
        if not self._ui_ready:
            return False
        target = self._short_form if self._mode_bar.current_mode() == "short" else self._long_panel
        for line in target.findChildren(QLineEdit):
            if line.isReadOnly():
                continue
            if line.isModified():
                return True
        for edit in target.findChildren(QTextEdit):
            if edit.isReadOnly():
                continue
            doc = edit.document()
            if doc is not None and doc.isModified():
                return True
        return False

    def unsaved_changes_description(self) -> str:
        return "- 机杼页有临时输入尚未保存（将自动存为草稿，下次启动自动恢复）"
