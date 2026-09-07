"""Type-only host contract shared by ChapterStudioPage mixins.

The chapter studio is intentionally split into narrow mixins.  At runtime those
mixins stay plain Python classes; during type checking they inherit this contract
so cross-mixin calls and page-owned Qt attributes are visible to mypy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import (
        QCheckBox,
        QComboBox,
        QLabel,
        QScrollArea,
        QSpinBox,
        QTabWidget,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )

    from novel_forge.desktop.jobs import DesktopJobRecord
    from novel_forge.desktop.widgets import (
        ActionButton,
        Badge,
        CheckpointDialog,
        CollapsibleSection,
        ScrollPage,
    )
    from novel_forge.desktop.workspace import DesktopWorkspaceSnapshot
    from novel_forge.workspace.contracts import ChapterWorkspaceSnapshot, DecisionOption
    from novel_forge.workspace.runtime import RuntimeServices

    from .action_panel import ChapterStudioActionPanelPresenter
    from .artifacts import ChapterStudioArtifactPresenter
    from .autorun import AutoPilotContext
    from .state import ChapterStudioState
    from .widgets import (
        ChapterRailPanel,
        CompassPanel,
        ModeSelector,
        WritingModeSelector,
    )

    class ChapterStudioMixinBase(ScrollPage):
        """Static contract for attributes supplied by ``ChapterStudioPage``."""

        MODE_MANUAL: str
        MODE_SUGGEST: str
        MODE_AUTO: str
        MODE_BOOK_AUTO: str
        AUTO_PILOT_TIMEOUT_SECS: int
        MAX_AUTO_REFRESH_ATTEMPTS: int
        _WATCHDOG_INTERVAL_MS: int
        _MODE_INDEX: dict[str, int]
        _CONTINUITY_REFRESH_KINDS: frozenset[str]
        _CAUSAL_REFRESH_KINDS: frozenset[str]

        prepare_requested: Any
        resolve_requested: Any
        repair_continuity_requested: Any
        repair_causal_requested: Any
        repair_issues_requested: Any
        reevaluate_requested: Any
        polish_requested: Any
        book_consistency_requested: Any
        export_requested: Any
        reextract_relationships_requested: Any
        repair_motif_history_requested: Any
        clear_task_flow_requested: Any
        clean_chapters_flow_requested: Any
        chapter_context_requested: Any
        open_project_requested: Any
        view_project_requested: Any
        navigate_requested: Any
        context_changed: Any
        auto_advance_requested: Any
        cancel_job_requested: Any
        workspace_refresh_requested: Any
        auto_pilot_started: Any
        auto_pilot_stopped: Any
        ui_state_changed: Any

        body_layout: QVBoxLayout
        _state: ChapterStudioState
        _store: Any | None
        _studio: ChapterWorkspaceSnapshot | None
        _workspace: DesktopWorkspaceSnapshot | None
        _runtime: RuntimeServices | None
        _memory_presenter: Any | None
        _pending_focus_request: tuple[str, int | None] | None
        _pending_studio_binding: tuple[ChapterWorkspaceSnapshot | None] | None

        _mode: str
        _auto_pilot_pending: bool
        _auto_started: bool
        _stopped_from_auto: bool
        _stopped_mode: str
        _auto_gen: int
        _auto_chapter_prepared: bool
        _auto_last_progress_at: float
        _auto_refresh_count: int
        _auto_repair_pending: bool
        _auto_repair_attempts: dict[tuple[str, int], int]
        _book_auto_skip_done: bool
        _last_submitted_checkpoint_id: str | None
        _auto_refresh_in_flight: bool
        _auto_refresh_consumed_job_ids: set[str]
        _notes_submitted: bool
        _notes_expanded_by_chapter: dict[tuple[str, int], bool]
        _jobs_fingerprint: tuple[Any, ...]
        _last_applied_memory_event_key: dict[str, tuple[str, str]]

        _jobs: list[DesktopJobRecord]
        _book_level_jobs: list[DesktopJobRecord]
        _all_jobs: list[DesktopJobRecord]
        _task_flow_clearable_job_ids: list[str]
        _chapter_job_cards: dict[str, Any]
        _jobs_empty_state: QWidget | None
        _jobs_skeleton_widget: QWidget | None
        _jobs_loading: bool
        _jobs_skeleton_timeout_active: bool
        _history_separator_widget: QWidget | None
        _upstream_ready_cache: dict[tuple[str, int], tuple[bool, float]]
        _user_nav_ctx_pending: bool

        _project_combo: QComboBox
        _chapter_spin: QSpinBox
        _writing_mode_selector: WritingModeSelector
        _mode_selector: ModeSelector
        _follow_autorun_cb: QCheckBox
        _project_status_dot: QLabel
        _background_autorun_label: QLabel
        _view_btn: ActionButton
        _goto_workflow_btn: ActionButton
        _book_synopsis: QLabel
        _consistency_tool_btn: ActionButton
        _export_tool_btn: ActionButton
        _diff_tool_btn: ActionButton
        _clean_stale_btn: ActionButton
        _rail: ChapterRailPanel
        _compass: CompassPanel
        _memory_panel: QWidget
        _memory_panel_widget: Any
        _memory_badge: Any
        _context_skeleton: Any
        _action_panel: QWidget
        _action_title: QLabel
        _action_badge: Badge
        _job_hint: QLabel
        _action_summary: QLabel
        _ai_suggestion_frame: QWidget
        _ai_suggestion_label: QLabel
        _notes_section: CollapsibleSection
        _notes: QTextEdit
        _rewrite_strategy_row: QWidget
        _rewrite_strategy_combo: QComboBox
        _action_buttons: QVBoxLayout
        _action_presenter: ChapterStudioActionPanelPresenter
        _checkpoint_dialog: CheckpointDialog | None
        _center_wrapper: QWidget
        _artifact_tabs: QTabWidget
        _artifact_presenter: ChapterStudioArtifactPresenter
        _clear_task_flow_btn: ActionButton
        _jobs_scroll: QScrollArea
        _jobs_layout: QVBoxLayout

        _context_request_timer: QTimer
        _auto_watchdog: QTimer
        _retry_countdown_timer: QTimer
        _deferred_build_timer: QTimer
        _defer_sections: bool
        _ui_ready: bool
        _deferred_build_index: int
        _deferred_placeholder: QWidget | None
        _pending_workspace_snapshot: DesktopWorkspaceSnapshot | None
        _pending_workspace_sections: tuple[DesktopWorkspaceSnapshot, frozenset[str]] | None
        _pending_jobs: list[DesktopJobRecord] | None
        _status_rotation_timer: QTimer
        _rotation_index: int
        _rotation_paused: bool
        _rotation_active_projects: list[str]
        _active_projects: set[str]
        _previous_project: str | None

        def current_project_id(self) -> str: ...
        def current_chapter_number(self) -> int: ...
        def current_writing_mode(self) -> str: ...
        def is_ui_ready(self) -> bool: ...
        def bind_jobs(self, jobs: list[DesktopJobRecord]) -> None: ...
        def bind_studio(self, snapshot: ChapterWorkspaceSnapshot | None) -> None: ...
        def _build_autopilot_context(self) -> AutoPilotContext: ...
        @property
        def _auto_pilot(self) -> bool: ...
        def _latest_relevant_job(self) -> DesktopJobRecord | None: ...
        def _latest_active_project_job(self) -> DesktopJobRecord | None: ...
        def _active_project_jobs(
            self,
            jobs: list[DesktopJobRecord] | None = None,
        ) -> list[DesktopJobRecord]: ...
        def _is_current_chapter_done(self) -> bool: ...
        def _current_chapter_stale_data(self) -> tuple[bool, int]: ...
        def _is_upstream_chapter_ready(self) -> bool: ...
        def _feasibility_check_word_count(self) -> bool: ...
        def _auto_mode_label(self) -> str: ...
        def _set_mode(self, mode: str) -> None: ...
        def _start_auto_pilot(self) -> None: ...
        def _stop_auto_pilot(
            self,
            reason: str = "用户已取消",
            cancel_jobs: bool = True,
        ) -> None: ...
        def _resume_auto_pilot(self) -> None: ...
        def _try_auto_action(self) -> None: ...
        def _schedule_retry_after(self, delay_secs: int) -> None: ...
        def _cancel_scheduled_retry(self) -> None: ...
        def _handle_checkpoint_option(self, option: DecisionOption) -> None: ...
        def _on_resume_from_progress(self, checkpoint: Any) -> None: ...
        def _submit_prepare(
            self,
            *,
            force: bool = False,
            extra_continuity_notes: str = "",
        ) -> None: ...
        def _submit_repair_issues(self) -> None: ...
        def _submit_reevaluate_chapter(self) -> None: ...
        def _submit_reextract_relationships(self) -> None: ...
        def _submit_repair_motif_history(self) -> None: ...
        def _submit_book_consistency(self) -> None: ...
        def _submit_export(self) -> None: ...
        def _submit_regen_with_continuity_check(self) -> None: ...
        def _submit_polish_with_continuity_check(self) -> None: ...
        def _render_rail(self) -> None: ...
        def _render_compass(self) -> None: ...
        def _render_action_panel(self) -> None: ...
        def _render_action_panel_for_project(self, project_id: str) -> None: ...
        def _render_artifacts(self) -> None: ...
        def _render_inspector(self) -> None: ...
        def _render_memory_panel(self) -> None: ...
        def _render_relationship_card(self) -> None: ...
        def _render_continuity_checklist(self) -> None: ...
        def _render_causal_checklist(self) -> None: ...
        def _render_jobs_panel(
            self,
            all_jobs: list[DesktopJobRecord],
            *,
            loading: bool = False,
        ) -> None: ...
        def _clear_chapter_job_cards(self) -> None: ...
        @staticmethod
        def _build_jobs_fingerprint(
            jobs: list[DesktopJobRecord],
            project_id: str,
            chapter_number: int,
        ) -> tuple[object, ...]: ...
        @staticmethod
        def _select_latest_memory_event(
            jobs: list[DesktopJobRecord],
            *,
            project_id: str,
            fallback_chapter: int,
        ) -> tuple[str, str, dict[str, Any]] | None: ...
        def _load_memory_status_from_disk(self, project_id: str) -> dict[str, Any] | None: ...
        def _handle_memory_invalidated(
            self,
            project_id: str,
            payload: dict[str, Any],
        ) -> None: ...
        def _handle_memory_updated(
            self,
            project_id: str,
            payload: dict[str, Any],
        ) -> None: ...
        def _update_repair_btn_state(self) -> None: ...
        def _update_reevaluate_btn_state(self) -> None: ...
        def _update_reextract_btn_state(self) -> None: ...
        def _update_motif_repair_btn_state(self) -> None: ...
        def _update_book_level_btn_state(self) -> None: ...
        def _update_clear_task_flow_btn_state(self) -> None: ...
        def _cancel_running_job(self) -> None: ...
        def _request_context(self) -> None: ...
        def _mark_notes_committed(self, *, submitted_with_notes: bool = False) -> None: ...
        def _restore_notes_input(self) -> None: ...
        def _queue_auto_submit_repair(self) -> None: ...
        def _project_layout(self) -> Any | None: ...
        def _invalidate_memory_context(self, project_id: str, from_chapter: int) -> None: ...
        def _patch_memory_disk_file(self, project_id: str, from_chapter: int) -> None: ...
        def _patch_outline_episodic_file(self, project_id: str, from_chapter: int) -> None: ...
        def _raw_chapter_warnings(self) -> list[str]: ...
        def _warning_fingerprint(self, items: list[str]) -> str: ...
        def _delete_completed_chapter_files(self) -> None: ...
        def _on_clean_stale_chapters(self) -> None: ...
        def _show_version_diff(self) -> None: ...
        def _goto_global_relationships(self) -> None: ...
        def _emit_open_project(self) -> None: ...
        def _emit_view_project(self) -> None: ...
        def _on_mode_changed(self, button_id: int, checked: bool) -> None: ...
        def _on_user_chapter_changed(self, value: int) -> None: ...
        def _on_project_selection_changed(self, new_project: str) -> None: ...
        def _on_clear_task_flow(self) -> None: ...
        def _on_clear_chapter_warnings(self) -> None: ...
        def _on_follow_autorun_changed(self, state: int) -> None: ...
        def _on_writing_mode_changed(self, mode: str) -> None: ...
        def _notify_ui_state_changed(self) -> None: ...
        def _on_motif_lookback_changed(self, value: int) -> None: ...
        def _sync_motif_lookback_from_settings(self) -> None: ...
        def _sync_project_controls_from_state(self) -> None: ...
        def _activate_project_context(self, project_id: str) -> None: ...
        def _commit_project_selection(self, new_project: str, *, clear_studio: bool) -> None: ...
        def _update_active_projects(self) -> None: ...
        def _update_status_dot(self) -> None: ...
        def _update_background_autorun_label(self, current_project: str) -> None: ...
        def _get_projects_with_active_jobs(self, jobs: list[DesktopJobRecord]) -> set[str]: ...
        def _start_status_rotation(self) -> None: ...
        def _stop_status_rotation(self) -> None: ...
        def _on_status_rotation_tick(self) -> None: ...
        def _force_refresh_jobs(self) -> None: ...
        def _labeled_field(self, label: str, widget: QWidget) -> QWidget: ...
        def _build_selector(self) -> QWidget: ...
        def _build_compose_surface(self) -> QWidget: ...
        def _build_center(self) -> QWidget: ...
        def _build_memory_panel(self) -> QWidget: ...
        def _build_jobs_section(self) -> QWidget: ...
        def _build_artifacts_section(self) -> QWidget: ...
        def _coord_book_auto_running(self) -> bool: ...

else:

    class ChapterStudioMixinBase:
        """Runtime marker base; no attributes to keep Qt lookup untouched."""

        def is_ui_ready(self) -> bool:
            """Return readiness for lightweight mixin hosts.

            ``ChapterStudioPage`` provides the real deferred-build implementation.
            Test and integration hosts that compose only the mixins have no deferred
            UI, so they are ready by definition.
            """

            return bool(getattr(self, "_ui_ready", True))


__all__ = ["ChapterStudioMixinBase"]
