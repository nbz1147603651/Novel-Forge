"""Dedicated chapter-studio page for long-form chapter work.

Thin facade — all logic delegated to coordinators and mixins.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QWidget

from novel_forge.desktop.jobs import DesktopJobRecord
from novel_forge.desktop.shutdown_utils import safe_disconnect
from novel_forge.desktop.widgets import ScrollPage
from novel_forge.desktop.workspace import DesktopWorkspaceSnapshot
from novel_forge.workspace.contracts import ChapterWorkspaceSnapshot
from novel_forge.workspace.runtime import RuntimeServices

from .actions import ChapterStudioActionMixin
from .auto import ChapterStudioAutoMixin
from .coord import (
    ChapterStudioCoordMixin,
)
from .coordinator import (
    StudioCoordinator,
    StudioRenderer,
    StudioStateManager,
)
from .data import (
    ChapterStudioDataMixin,
    _filter_episodic_index_payload,
    _filter_outline_payload,
)
from .jobs import ChapterStudioJobsMixin
from .renderers import ChapterStudioRenderMixin
from .state import AutoPilotProjectState, ChapterStudioState

__all__ = [
    "ChapterStudioPage",
    "_filter_episodic_index_payload",
    "_filter_outline_payload",
]


class ChapterStudioPage(
    ChapterStudioCoordMixin,
    ChapterStudioJobsMixin,
    ChapterStudioDataMixin,
    ChapterStudioAutoMixin,
    ChapterStudioRenderMixin,
    ChapterStudioActionMixin,
    ScrollPage,
):
    """Long-form chapter workspace with checkpoints and artifact inspection."""

    MODE_MANUAL = "manual"
    MODE_SUGGEST = "suggest"
    MODE_AUTO = "auto"
    MODE_BOOK_AUTO = "book_auto"

    _MODE_INDEX: dict[str, int] = {
        MODE_MANUAL: 0,
        MODE_SUGGEST: 1,
        MODE_AUTO: 2,
        MODE_BOOK_AUTO: 3,
    }

    AUTO_PILOT_TIMEOUT_SECS: int = 900
    _WATCHDOG_INTERVAL_MS: int = 30_000
    MAX_AUTO_REFRESH_ATTEMPTS: int = 15

    prepare_requested = Signal(object)
    resolve_requested = Signal(object)
    repair_continuity_requested = Signal(object)
    repair_causal_requested = Signal(object)
    repair_issues_requested = Signal(object)
    reevaluate_requested = Signal(object)
    polish_requested = Signal(object)
    book_consistency_requested = Signal(object)
    export_requested = Signal(object)
    reextract_relationships_requested = Signal(object)
    repair_motif_history_requested = Signal(object)
    clear_task_flow_requested = Signal(str, object)
    clean_chapters_flow_requested = Signal(str, int)
    chapter_context_requested = Signal(str, int)
    open_project_requested = Signal(str)
    view_project_requested = Signal(str)
    navigate_requested = Signal(str)
    context_changed = Signal()
    auto_advance_requested = Signal(str, int)
    cancel_job_requested = Signal(str, str)
    workspace_refresh_requested = Signal()
    auto_pilot_started = Signal(str)
    auto_pilot_stopped = Signal(str)
    task_focus_decision_selected = Signal(str, str, str, str)
    task_focus_expand_requested = Signal()
    # A page-level persistence boundary.  The window listens to this instead
    # of coupling page controls directly to its session implementation.
    ui_state_changed = Signal()

    # ── State property proxies ─────────────────────────────────────

    @property
    def _mode(self) -> str:
        return self._state.mode

    @_mode.setter
    def _mode(self, value: str) -> None:
        self._state.mode = value

    @property
    def _auto_pilot_pending(self) -> bool:
        return self._state.auto_pilot_pending

    @_auto_pilot_pending.setter
    def _auto_pilot_pending(self, value: bool) -> None:
        self._state.auto_pilot_pending = value

    @property
    def _auto_started(self) -> bool:
        return self._state.auto_started

    @_auto_started.setter
    def _auto_started(self, value: bool) -> None:
        self._state.auto_started = value

    @property
    def _stopped_from_auto(self) -> bool:
        return self._state.stopped_from_auto

    @_stopped_from_auto.setter
    def _stopped_from_auto(self, value: bool) -> None:
        self._state.stopped_from_auto = value

    @property
    def _stopped_mode(self) -> str:
        return self._state.stopped_mode

    @_stopped_mode.setter
    def _stopped_mode(self, value: str) -> None:
        self._state.stopped_mode = value

    @property
    def _auto_gen(self) -> int:
        return self._state.auto_gen

    @_auto_gen.setter
    def _auto_gen(self, value: int) -> None:
        self._state.auto_gen = value

    @property
    def _notes_expanded_by_chapter(self) -> dict[tuple[str, int], bool]:
        return self._state.notes_expanded_by_chapter

    @_notes_expanded_by_chapter.setter
    def _notes_expanded_by_chapter(self, value: dict[tuple[str, int], bool]) -> None:
        self._state.notes_expanded_by_chapter = value

    @property
    def _jobs(self) -> list[DesktopJobRecord]:
        return self._state.jobs

    @_jobs.setter
    def _jobs(self, value: list[DesktopJobRecord]) -> None:
        self._state.jobs = value

    @property
    def _book_auto_skip_done(self) -> bool:
        return self._state.book_auto_skip_done

    @_book_auto_skip_done.setter
    def _book_auto_skip_done(self, value: bool) -> None:
        self._state.book_auto_skip_done = value

    @property
    def _auto_repair_pending(self) -> bool:
        return self._state.auto_repair_pending

    @_auto_repair_pending.setter
    def _auto_repair_pending(self, value: bool) -> None:
        self._state.auto_repair_pending = value

    @property
    def _auto_repair_attempts(self) -> dict[tuple[str, int], int]:
        return self._state.auto_repair_attempts

    @_auto_repair_attempts.setter
    def _auto_repair_attempts(self, value: dict[tuple[str, int], int]) -> None:
        self._state.auto_repair_attempts = value

    @property
    def _last_submitted_checkpoint_id(self) -> str | None:
        return self._state.last_submitted_checkpoint_id

    @_last_submitted_checkpoint_id.setter
    def _last_submitted_checkpoint_id(self, value: str | None) -> None:
        self._state.last_submitted_checkpoint_id = value

    @property
    def _auto_refresh_in_flight(self) -> bool:
        return self._state.auto_refresh_in_flight

    @_auto_refresh_in_flight.setter
    def _auto_refresh_in_flight(self, value: bool) -> None:
        self._state.auto_refresh_in_flight = bool(value)

    @property
    def _auto_refresh_consumed_job_ids(self) -> set[str]:
        return self._state.auto_refresh_consumed_job_ids

    @_auto_refresh_consumed_job_ids.setter
    def _auto_refresh_consumed_job_ids(self, value: set[str]) -> None:
        self._state.auto_refresh_consumed_job_ids = set(value)

    @property
    def _notes_submitted(self) -> bool:
        return self._state.notes_submitted

    @_notes_submitted.setter
    def _notes_submitted(self, value: bool) -> None:
        self._state.notes_submitted = value

    @property
    def _auto_chapter_prepared(self) -> bool:
        return self._state.auto_chapter_prepared

    @_auto_chapter_prepared.setter
    def _auto_chapter_prepared(self, value: bool) -> None:
        self._state.auto_chapter_prepared = value

    @property
    def _auto_last_progress_at(self) -> float:
        return self._state.auto_last_progress_at

    @_auto_last_progress_at.setter
    def _auto_last_progress_at(self, value: float) -> None:
        self._state.auto_last_progress_at = value

    @property
    def _auto_refresh_count(self) -> int:
        return self._state.auto_refresh_count

    @_auto_refresh_count.setter
    def _auto_refresh_count(self, value: int) -> None:
        self._state.auto_refresh_count = value

    @property
    def _jobs_fingerprint(self) -> tuple[object, ...]:
        return self._state.jobs_fingerprint

    @_jobs_fingerprint.setter
    def _jobs_fingerprint(self, value: tuple[object, ...]) -> None:
        self._state.jobs_fingerprint = value

    @property
    def _last_applied_memory_event_key(self) -> dict[str, tuple[str, str]]:
        return self._state.last_applied_memory_event_key

    @_last_applied_memory_event_key.setter
    def _last_applied_memory_event_key(self, value: dict[str, tuple[str, str]]) -> None:
        self._state.last_applied_memory_event_key = value

    def __init__(self, *, defer_sections: bool = False) -> None:
        super().__init__()
        # Runtime attributes - initialized to None so tests can set them
        self._store: Any | None = None
        try:
            from novel_forge.desktop.state.store import get_ui_store

            self._store = get_ui_store()
        except Exception:
            pass
        self._studio: ChapterWorkspaceSnapshot | None = None
        self._workspace: DesktopWorkspaceSnapshot | None = None
        self._runtime: RuntimeServices | None = None
        self._memory_presenter: Any | None = None

        # Deferred-build state (mirrors WorkflowPage's pattern).
        self._defer_sections = defer_sections
        self._ui_ready = not defer_sections
        self._deferred_build_index = 0
        self._deferred_placeholder: QWidget | None = None
        self._deferred_build_timer: QTimer = QTimer(self)
        self._deferred_build_timer.setSingleShot(True)
        self._deferred_build_timer.timeout.connect(self._run_deferred_build_step)
        # Pending bindings buffered while deferred build is in progress.
        self._pending_workspace_snapshot: DesktopWorkspaceSnapshot | None = None
        self._pending_workspace_sections: tuple[DesktopWorkspaceSnapshot, frozenset[str]] | None = None
        self._pending_jobs: list[DesktopJobRecord] | None = None
        self._pending_focus_request: tuple[str, int | None] | None = None
        # A one-item tuple distinguishes "no pending bind" from a pending
        # bind_studio(None), where None intentionally clears stale context.
        self._pending_studio_binding: tuple[ChapterWorkspaceSnapshot | None] | None = None

        # State
        self._state = ChapterStudioState()
        self._state.state_changed.connect(self._on_state_changed)
        # Production windows set this when their job manager exposes the
        # Engine-owned durable autorun state machine.  The legacy page driver
        # remains available only for isolated compatibility tests.
        self._engine_owned_autorun = False

        # Coordinator objects
        self._state_manager = StudioStateManager(self)
        self._coordinator = StudioCoordinator(self)
        self._renderer = StudioRenderer(self)

        # UI-related attributes
        self._task_flow_clearable_job_ids: list[str] = []
        self._chapter_job_cards: dict[str, Any] = {}
        self._jobs_empty_state: QWidget | None = None
        self._jobs_skeleton_widget: QWidget | None = None
        self._jobs_loading: bool = False
        self._book_level_jobs: list[DesktopJobRecord] = []
        self._history_separator_widget: QWidget | None = None
        self._task_flow_error_entries: list[dict[str, str]] = []
        self._resolved_task_flow_error_ids: set[str] = set()
        self._latest_render_jobs: list[DesktopJobRecord] = []
        self._task_observation_store: Any | None = None

        self._build_ui()

        self._context_request_timer: QTimer = QTimer(self)
        self._context_request_timer.setSingleShot(True)
        self._context_request_timer.setInterval(
            __import__(
                "novel_forge.desktop.constants", fromlist=["CONTEXT_REQUEST_DEBOUNCE_MS"]
            ).CONTEXT_REQUEST_DEBOUNCE_MS
        )
        self._context_request_timer.timeout.connect(self._request_context)

        self._auto_watchdog: QTimer = QTimer(self)
        self._auto_watchdog.setInterval(self._WATCHDOG_INTERVAL_MS)
        self._auto_watchdog.timeout.connect(self._check_auto_pilot_timeout)

        self._retry_countdown_timer: QTimer = QTimer(self)
        self._retry_countdown_timer.setInterval(1000)
        self._retry_countdown_timer.timeout.connect(self._on_retry_countdown_tick)

    # ── Explicit composition properties (replaces __getattr__ delegation) ──

    @property
    def state_manager(self) -> StudioStateManager:
        """Return the state manager coordinator."""
        return self._state_manager

    @property
    def coordinator(self) -> StudioCoordinator:
        """Return the studio coordinator."""
        return self._coordinator

    @property
    def renderer(self) -> StudioRenderer:
        """Return the studio renderer."""
        return self._renderer

    # ── Forwarded properties from coordinators (for external access) ──

    @property
    def store(self) -> Any:
        """Forward to state_manager.store (page._store)."""
        return self._state_manager.store

    @property
    def studio(self) -> Any:
        """Forward to state_manager.studio (page._studio)."""
        return self._state_manager.studio

    @property
    def workspace(self) -> Any:
        """Forward to state_manager.workspace (page._workspace)."""
        return self._state_manager.workspace

    @property
    def runtime(self) -> Any:
        """Forward to state_manager.runtime (page._runtime)."""
        return self._state_manager.runtime

    @property
    def memory_presenter(self) -> Any:
        """Forward to state_manager.memory_presenter (page._memory_presenter)."""
        return self._state_manager.memory_presenter

    def __dir__(self) -> list[str]:
        """Extend dir() to include forwarded coordinator attributes for IDE autocomplete."""
        base = super().__dir__()
        return sorted(
            set(base)
            | {
                "state_manager",
                "coordinator",
                "renderer",
                "store",
                "studio",
                "workspace",
                "runtime",
                "memory_presenter",
            }
        )

    def _on_state_changed(self, field_name: str, value: object) -> None:
        if getattr(self, "_shutdown_done", False) or not self.is_ui_ready():
            return
        self._coordinator.on_state_changed(field_name, value)

    # ── Public API for window.py (encapsulates private state) ──────────

    def needs_job_binding(self) -> bool:
        """Return True if auto-pilot is active and needs job binding even when page is hidden."""
        return bool(self._state.active_auto_project_ids())

    def active_autorun_project_ids(self) -> list[str]:
        """Return projects whose chapter auto-run should keep moving in the background."""
        return self._state.active_auto_project_ids()

    def autorun_state_for_project(self, project_id: str) -> AutoPilotProjectState:
        """Return the per-project auto-run state used by the window-level driver."""
        return self._state.auto_pilot_state_for(project_id)

    def set_autorun_project_started(self, project_id: str, started: bool) -> None:
        """Set auto-run active state for one project without changing the visible project."""
        self._state.set_auto_started_for_project(project_id, started)
        if not self._state.active_auto_project_ids():
            self._auto_watchdog.stop()
        if project_id == self.current_project_id():
            self._render_action_panel()

    def start_auto_pilot(self) -> None:
        """Public wrapper for _start_auto_pilot."""
        self._start_auto_pilot()

    def set_engine_owned_autorun(self, enabled: bool) -> None:
        """Switch the page to observer/control mode for chapter autorun."""

        self._engine_owned_autorun = bool(enabled)

    def stop_auto_pilot(self, reason: str = "用户已取消", *, cancel_jobs: bool = False) -> None:
        """Public wrapper to stop auto-pilot and reset state."""
        self._stop_auto_pilot(reason=reason, cancel_jobs=cancel_jobs)

    def set_mode(self, mode: str) -> None:
        """Public wrapper for _set_mode."""
        self._set_mode(mode)

    def should_follow_autorun(self) -> bool:
        """Public wrapper for _should_follow_autorun."""
        return self._should_follow_autorun()

    def should_follow_autorun_for_project(self, project_id: str) -> bool:
        """Return whether UI should follow auto-run jumps for a specific project."""
        return self._state.auto_pilot_state_for(project_id).follow_autorun

    def current_writing_mode(self) -> str:
        """Return the currently selected chapter writing mode."""
        selector = getattr(self, "_writing_mode_selector", None)
        current_mode = getattr(selector, "current_mode", None)
        if callable(current_mode):
            return "scene_level" if current_mode() == "scene_level" else "whole_chapter"
        return "whole_chapter"

    def export_ui_state(self) -> dict[str, Any]:
        """Serialize restart-safe selector choices for the desktop session."""
        return {
            "version": 1,
            "project_preferences": self._state.export_project_preferences(),
        }

    def restore_ui_state(self, payload: object) -> None:
        """Restore selector choices without restoring an in-flight auto-run."""
        if not isinstance(payload, dict):
            return
        self._state.restore_project_preferences(payload.get("project_preferences"))
        self._sync_project_controls_from_state()

    @property
    def stopped_from_auto(self) -> bool:
        """Public property for _stopped_from_auto state."""
        return self._stopped_from_auto

    def get_actions(self) -> list[tuple[str, Callable[[], None]]]:
        """Return action descriptors for top-bar buttons."""
        return [
            (self.primary_action_label(), self.trigger_primary_action),
            ("打开文件夹", lambda: self.open_project_requested.emit(self.current_project_id())),
        ]

    def clear_memory_status(self, project_id: str) -> None:
        """Clear memory status for a deleted project."""
        if self._store is not None:
            self._store.clear_memory_status(project_id)

    # ── Shutdown ───────────────────────────────────────────────────────

    def shutdown(self) -> None:
        if getattr(self, "_shutdown_done", False):
            return
        self._shutdown_done = True

        self._deferred_build_timer.stop()
        self._context_request_timer.stop()
        self._auto_watchdog.stop()
        self._retry_countdown_timer.stop()
        if hasattr(self, "_status_rotation_timer"):
            self._status_rotation_timer.stop()
        # When deferred build hasn't completed, UI widgets may not exist yet.
        if self.is_ui_ready():
            safe_disconnect(self._project_combo.currentTextChanged)
            safe_disconnect(self._chapter_spin.valueChanged, self._on_user_chapter_changed)
            safe_disconnect(self._writing_mode_selector.mode_changed, self._on_writing_mode_changed)
            safe_disconnect(self._mode_selector.mode_changed, self._on_mode_changed)
            safe_disconnect(self._rail.chapter_selected)
        if hasattr(self, "_task_focus_panel"):
            self._task_focus_panel.shutdown()
        if hasattr(self, "_checkpoint_dialog"):
            self._dismiss_checkpoint_dialog()
        # State resets below emit synchronously.  Disconnect before mutating so
        # closing the window cannot rebuild widgets during teardown.
        safe_disconnect(self._state.state_changed, self._on_state_changed)
        self._state.scheduled_retry_at = 0.0
        self._state.reset_auto_pilot()
        self._state.notes_expanded_by_chapter.clear()
        self._state_manager.detach()
        self._coordinator.detach()
        self._renderer.detach()

    # ── Deferred build (mirrors WorkflowPage pattern) ──────────────────

    def is_ui_ready(self) -> bool:
        """Return whether all staged chapter-studio sections are available."""
        return self._ui_ready

    def ensure_all_deferred_sections_built(self) -> None:
        """Synchronously build all deferred sections (used by tests / direct access)."""
        if self._ui_ready:
            return
        self._deferred_build_timer.stop()
        build_steps = self._ui_build_steps()
        for step in build_steps[self._deferred_build_index :]:
            widget = step()
            placeholder = self._deferred_placeholder
            if placeholder is not None:
                index = self.body_layout.indexOf(placeholder)
                self.body_layout.insertWidget(max(0, index), widget)
            else:
                self.body_layout.addWidget(widget)
        self._deferred_build_index = len(build_steps)
        self._finish_deferred_build()

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        if (
            self._defer_sections
            and not self._ui_ready
            and not self._deferred_build_timer.isActive()
            and not getattr(self, "_shutdown_done", False)
        ):
            self._deferred_build_timer.start(0)
        if (
            self._ui_ready
            and getattr(self, "_jobs_panel_render_pending", False)
            and hasattr(self, "_chapter_job_cards")
        ):
            QTimer.singleShot(0, self._flush_jobs_panel_render)

    def hideEvent(self, event: Any) -> None:
        """Suspend paint-only construction and renders while the page is hidden."""
        host_window = self.window()
        if host_window is self or host_window.isVisible():
            self._deferred_build_timer.stop()
            if hasattr(self, "_jobs_panel_render_timer"):
                self._jobs_panel_render_timer.stop()
        super().hideEvent(event)

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
            self._finish_deferred_build()
            return
        build_step = build_steps[self._deferred_build_index]
        widget = build_step()
        # Insert before the placeholder so sections appear in order.
        placeholder = self._deferred_placeholder
        if placeholder is not None:
            index = self.body_layout.indexOf(placeholder)
            self.body_layout.insertWidget(max(0, index), widget)
        else:
            self.body_layout.addWidget(widget)
        self._deferred_build_index += 1
        self._deferred_build_timer.start(16)

    def _finish_deferred_build(self) -> None:
        placeholder = self._deferred_placeholder
        if placeholder is not None:
            self.body_layout.removeWidget(placeholder)
            placeholder.deleteLater()
            self._deferred_placeholder = None
        self.body_layout.addStretch()
        self._ui_ready = True
        self._replay_pending_bindings()
        # Action builders intentionally stay disabled while widgets are
        # incomplete; refresh the shared top bar at the readiness boundary.
        self.context_changed.emit()

    def _replay_pending_bindings(self) -> None:
        """Drain buffered bindings and deep links after deferred build completes."""
        if self._pending_workspace_snapshot is not None:
            snapshot = self._pending_workspace_snapshot
            self._pending_workspace_snapshot = None
            self.bind_workspace(snapshot)
        if self._pending_workspace_sections is not None:
            snapshot, sections = self._pending_workspace_sections
            self._pending_workspace_sections = None
            self.bind_workspace_sections(snapshot, sections)
        if self._pending_focus_request is not None:
            project_id, chapter_number = self._pending_focus_request
            self._pending_focus_request = None
            self.focus_project(project_id, chapter_number)
        if self._pending_studio_binding is not None:
            (studio_snapshot,) = self._pending_studio_binding
            self._pending_studio_binding = None
            self.bind_studio(studio_snapshot)
        if self._pending_jobs is not None:
            jobs = self._pending_jobs
            self._pending_jobs = None
            self.on_jobs_changed(jobs)
