"""Mixin module: core methods for ``NovelForgeDesktopWindow``.

Auto-extracted in the M3.1 giant-file-split refactor. Methods live here so the
main :file:`window/__init__.py` facade can compose them via mixin inheritance.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable
from typing import Any, cast

from PySide6.QtCore import (
    QEvent,
    QFileSystemWatcher,
    QMutex,
    QPropertyAnimation,
    QSize,
    Qt,
    QTimer,
)
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

import novel_forge.desktop.page_registrations as _page_registrations  # noqa: F401
from novel_forge.desktop.components.task_focus import (
    FloatingStreamWindow,
    FloatingTaskCompanion,
    TaskFocusDialog,
)
from novel_forge.desktop.components.toast import ToastManager
from novel_forge.desktop.constants import (
    COMPACT_HEIGHT_THRESHOLD,
    COMPACT_WIDTH_THRESHOLD,
    DENSITY_RESIZE_DEBOUNCE_MS,
    FS_WATCHER_DEBOUNCE_MS,
    JOB_BIND_DEBOUNCE_MS,
    WINDOW_DEFAULT_MIN_HEIGHT,
    WINDOW_DEFAULT_MIN_WIDTH,
    WINDOW_DEFAULT_START_HEIGHT,
    WINDOW_DEFAULT_START_WIDTH,
    WINDOW_SCREEN_HEIGHT_RATIO,
    WINDOW_SCREEN_WIDTH_RATIO,
    WORKSPACE_REFRESH_INTERVAL_MS,
)
from novel_forge.desktop.jobs import (
    DesktopJobManager,
    DesktopJobRecord,
)
from novel_forge.desktop.notification_sounds import DesktopNotificationSoundPlayer
from novel_forge.desktop.registry import page_registry
from novel_forge.desktop.sleep_wake_monitor import SleepWakeMonitor
from novel_forge.desktop.state.store import WindowState, get_ui_store
from novel_forge.desktop.strings import UIStrings
from novel_forge.desktop.task_observation import TaskObservationStore
from novel_forge.desktop.workspace import DesktopWorkspaceService, DesktopWorkspaceSnapshot
from novel_forge.workspace.contracts import (
    BookConsistencyRequest,
    ExportBookRequest,
    ExtendOutlineRequest,
    InitLongRequest,
    PolishChapterRequest,
    PrepareChapterRequest,
    RebuildMemoryVectorsRequest,
    ReevaluateChapterRequest,
    ReextractRelationshipsRequest,
    RepairCausalRequest,
    RepairContinuityRequest,
    RepairIssuesRequest,
    RepairMotifHistoryRequest,
    ResolveChapterCheckpointRequest,
    RunChapterRequest,
    RunShortRequest,
    SyncChapterContractsRequest,
)

_logger = logging.getLogger(__name__)

_WINDOW_DEFAULT_MIN_WIDTH = WINDOW_DEFAULT_MIN_WIDTH
_WINDOW_DEFAULT_MIN_HEIGHT = WINDOW_DEFAULT_MIN_HEIGHT
_WINDOW_DEFAULT_START_WIDTH = WINDOW_DEFAULT_START_WIDTH
_WINDOW_DEFAULT_START_HEIGHT = WINDOW_DEFAULT_START_HEIGHT
_WINDOW_SCREEN_WIDTH_RATIO = WINDOW_SCREEN_WIDTH_RATIO
_WINDOW_SCREEN_HEIGHT_RATIO = WINDOW_SCREEN_HEIGHT_RATIO
_COMPACT_WIDTH_THRESHOLD = COMPACT_WIDTH_THRESHOLD
_COMPACT_HEIGHT_THRESHOLD = COMPACT_HEIGHT_THRESHOLD


from novel_forge.desktop.window._runnables import (  # noqa: E402
    _ChapterContextRefreshRunnable,
    _RuntimeServicesInitRunnable,
    _WorkspaceRefreshRunnable,
)
from novel_forge.desktop.window._widgets import (  # noqa: E402
    CachedGradientWidget,
    PageMeta,
)


def _desktop_thread_pools() -> Any:
    """Lazy accessor for the desktop thread pools (avoids a circular import).

    Matches the pattern in ``workspace_refresh.py`` -- ``desktop_thread_pools``
    is re-exported from ``novel_forge.desktop.window`` (this package), so
    importing it eagerly here would create a cycle.
    """
    import novel_forge.desktop.window as window_facade

    return window_facade.desktop_thread_pools()


class CoreMixin:
    """Mixin that contributes the **core** method group."""

    # Class-level constants from the original window.py class body.
    _STATUS_INFO: int = 0
    _STATUS_SUCCESS: int = 1
    _STATUS_WARNING: int = 2
    _STATUS_ERROR: int = 3
    _FORCE_REFRESH_MAX_RETRIES: int = 3
    _DEFERRED_PAGE_ACTIVATE_MS: int = 420
    # Only prewarm the lightweight settings shell.  Workflow and chapter
    # studio are intentionally kept cold: constructing them during the first
    # idle window steals the event loop for hundreds of ms and feels like a
    # delayed freeze immediately after launch.  They remain cold-instant so a
    # user click can paint the placeholder before their real page is built.
    _PAGE_PRELOAD_MODULES: tuple[str, ...] = ("novel_forge.desktop.pages.voice_studio.page",)
    # When True, RuntimeServices construction runs synchronously inside
    # ``_start_runtime_services_init`` instead of on the ui_io pool. Production
    # leaves this False (async) so the window appears immediately; tests that
    # monkeypatch ``from_settings`` to a fake and run against a non-executing
    # fake thread pool set this True to preserve the pre-B2 sync contract.
    _sync_runtime_services_init: bool = False
    _PREWARM_INITIAL_DELAY_MS: int = 250
    _PREWARM_STEP_DELAY_MS: int = 350
    _PAGE_PRELOAD_INITIAL_DELAY_MS: int = 80
    _PAGE_PRELOAD_STEP_DELAY_MS: int = 120
    _ACTIVE_INDICATOR_DURATION_MS: int = 200
    _ACTIVE_INDICATOR_INSET: int = 8  # distance from rail's left edge
    _FS_WATCHED_FILE_NAMES: tuple[str, ...] = (
        "spec.json",
        "story_bible.json",
        "character_bible.json",
        "outline.json",
        "states/outline_session.json",
        "blueprint.json",
    )
    _FS_WATCHED_KERNEL_FILE: str = "story_kernel/canon_current.json"

    # ── Declarative page metadata (derived from registry at import time) ──
    # These class attributes are computed from PageDescriptor.extra so new
    # pages can self-declare cold-instant, prewarm, and section bindings via
    # register(..., cold_instant=True, prewarm=True, sections=frozenset(...))
    # without editing shell constants.  page_registrations is imported above
    # so the registry is populated before these are evaluated.

    @staticmethod
    def _derive_prewarm_page_ids() -> tuple[str, ...]:
        return tuple(
            pid
            for pid in page_registry.list_pages()
            if page_registry.metadata(pid) and page_registry.metadata(pid).extra.get("prewarm")
        )

    @staticmethod
    def _derive_cold_instant_page_ids() -> frozenset[str]:
        return frozenset(
            pid
            for pid in page_registry.list_pages()
            if page_registry.metadata(pid) and page_registry.metadata(pid).extra.get("cold_instant")
        )

    @staticmethod
    def _derive_page_section_map() -> dict[str, frozenset[str]]:
        result: dict[str, frozenset[str]] = {}
        for pid in page_registry.list_pages():
            meta = page_registry.metadata(pid)
            if meta is None:
                continue
            sections = meta.extra.get("sections")
            if sections:
                result[pid] = frozenset(sections)
        return result

    # Evaluate the derived values as class attributes so they behave as
    # plain data (dict/frozenset/tuple) accessible on both class and instance.
    _PREWARM_PAGE_IDS: tuple[str, ...] = _derive_prewarm_page_ids.__func__()
    _COLD_INSTANT_PAGE_IDS: frozenset[str] = _derive_cold_instant_page_ids.__func__()
    _PAGE_SECTION_MAP: dict[str, frozenset[str]] = _derive_page_section_map.__func__()

    _JOB_DISPATCH: list[tuple[type, str, str]] = [
        (RunShortRequest, "submit_short", UIStrings.JOB_SUBMIT_SHORT),
        (InitLongRequest, "submit_init_long", UIStrings.JOB_SUBMIT_INIT_LONG),
        (RunChapterRequest, "submit_run_chapter", UIStrings.JOB_SUBMIT_RUN_CHAPTER),
        (PrepareChapterRequest, "submit_prepare_chapter", UIStrings.JOB_SUBMIT_PREPARE_CHAPTER),
        (
            ResolveChapterCheckpointRequest,
            "submit_resolve_chapter_checkpoint",
            UIStrings.JOB_SUBMIT_RESOLVE_CHECKPOINT,
        ),
        (
            RepairContinuityRequest,
            "submit_repair_continuity",
            UIStrings.JOB_SUBMIT_REPAIR_CONTINUITY,
        ),
        (RepairCausalRequest, "submit_repair_causal", UIStrings.JOB_SUBMIT_REPAIR_CAUSAL),
        (RepairIssuesRequest, "submit_repair_issues", UIStrings.JOB_SUBMIT_REPAIR_ISSUES),
        (ReevaluateChapterRequest, "submit_reevaluate_chapter", UIStrings.JOB_SUBMIT_REEVALUATE),
        (PolishChapterRequest, "submit_polish_chapter", UIStrings.JOB_SUBMIT_POLISH),
        (BookConsistencyRequest, "submit_book_consistency", UIStrings.JOB_SUBMIT_BOOK_CONSISTENCY),
        (ExportBookRequest, "submit_export_book", UIStrings.JOB_SUBMIT_EXPORT),
        (
            ReextractRelationshipsRequest,
            "submit_reextract_relationships",
            UIStrings.JOB_SUBMIT_REEXTRACT_RELATIONSHIPS,
        ),
        (
            RepairMotifHistoryRequest,
            "submit_repair_motif_history",
            UIStrings.JOB_SUBMIT_REPAIR_MOTIF_HISTORY,
        ),
        (
            RebuildMemoryVectorsRequest,
            "submit_rebuild_memory_vectors",
            UIStrings.JOB_SUBMIT_REBUILD_MEMORY_VECTORS,
        ),
        (
            SyncChapterContractsRequest,
            "submit_sync_chapter_contracts",
            UIStrings.JOB_SUBMIT_SYNC_CONTRACTS,
        ),
        (
            ExtendOutlineRequest,
            "submit_extend_outline",
            UIStrings.JOB_SUBMIT_EXTEND_OUTLINE,
        ),
    ]

    def show_priority_status(self, msg: str, timeout_ms: int = 3000, priority: int = 0) -> None:
        now = time.monotonic()
        if now > getattr(self, "_status_priority_expiry", 0.0):
            self._status_priority = -1
        if priority < getattr(self, "_status_priority", -1):
            return
        self._status_priority = priority
        self._status_priority_expiry = now + (timeout_ms / 1000)
        self.statusBar().showMessage(msg, timeout_ms)

    _DEFERRED_TIMERS_MAX: int = 200

    def _safe_deferred(self, delay_ms: int, callback: Callable[[], None]) -> None:
        """Schedule *callback* after *delay_ms*, guarding against post-close execution.

        If the window is already closing (``_is_closing`` is True), the call
        is silently dropped.  The callback is wrapped so that it also checks
        the flag at fire time, preventing use-after-free crashes when a
        timer fires after the window has been destroyed.

        Timers are tracked in ``_deferred_timers`` so they can be stopped
        during ``_pre_close_cleanup``.  A cap prevents unbounded accumulation
        during rapid refresh cycles.
        """
        if getattr(self, "_is_closing", False):
            return

        deferred_set: set[QTimer] = getattr(self, "_deferred_timers", set())
        if len(deferred_set) >= self._DEFERRED_TIMERS_MAX:
            # Cap reached — silently drop to prevent unbounded timer growth.
            return

        timer = QTimer(self)
        timer.setSingleShot(True)
        deferred_set.add(timer)

        def _guarded() -> None:
            # Remove from tracking set regardless of outcome.
            deferred_set.discard(timer)
            if getattr(self, "_is_closing", False):
                return
            try:
                callback()
            except RuntimeError:
                # C++ object already deleted — safe to ignore.
                pass

        timer.timeout.connect(_guarded)
        timer.start(delay_ms)

    @property
    def PAGE_META(self) -> dict[str, PageMeta]:
        """Return page metadata from the registry (backward-compat shim)."""
        result: dict[str, PageMeta] = {}
        for page_id in page_registry.list_pages():
            meta = page_registry.metadata(page_id)
            if meta is not None:
                result[page_id] = PageMeta(
                    label=meta.label,
                    eyebrow=meta.eyebrow,
                    title=meta.title,
                    subtitle=meta.subtitle,
                )
        return result

    def __init__(self) -> None:
        super().__init__()
        self._mock_enabled = False
        # RuntimeServices construction (DesktopWorkspaceService.from_settings)
        # builds the full model router (12 adapters), parses model_profiles.json,
        # and constructs the PromptBuilder/PromptRegistry -- ~430ms of work that
        # does not need to block the GUI thread. We kick it off on the ui_io
        # pool and keep the skeleton overlay visible until it lands.
        # ``_workspace`` stays None until ``_on_runtime_services_ready`` runs on
        # the GUI thread; all callers must tolerate the None state (see the
        # guards in refresh_workspace / autorun / shutdown / navigation_handlers).
        self._workspace: DesktopWorkspaceService | None = None
        self._workspace_init_worker: _RuntimeServicesInitRunnable | None = None
        self._workspace_needs_reload = False
        self._snapshot: DesktopWorkspaceSnapshot | None = None
        self._workspace_revision: int = 0
        self._refresh_in_progress: bool = False
        self._last_refresh_time: float = 0.0
        self._refresh_count: int = 0
        self._last_snapshot_hash: str | None = None
        self._last_snapshot_payload: dict[str, Any] | None = None
        self._section_hash_cache: dict[str, str] = {}
        self._last_payload_values: dict[str, Any] = {}
        self._last_payload_refs: dict[str, Any] = {}
        self._last_detail_ids: dict[str, int] = {}
        self._refresh_force_current: bool = False
        self._refresh_force_pending: bool = False
        self._force_refresh_retries: int = 0
        self._last_refresh_complete_time: float = 0.0
        self._perf_probe_times: deque[float] = deque(maxlen=200)
        self._perf_probe_max_samples: int = 200  # kept for backward-compat; deque enforces maxlen
        self._deferred_timers: set[QTimer] = set()
        self._active_workspace_refresh_worker: _WorkspaceRefreshRunnable | None = None
        self._active_context_refresh_workers: set[_ChapterContextRefreshRunnable] = set()
        self._page_workspace_revision: dict[str, int] = {}
        # Compatibility set used by older tests/callers; the authoritative
        # state is _pending_rebind_sections so hidden pages can replay only the
        # sections that actually changed when they become visible.
        self._pending_rebind_pages: set[str] = set()
        self._pending_rebind_sections: dict[str, set[str]] = {}
        self._latest_jobs: list[DesktopJobRecord] = []
        self._chapter_studio_project_id: str = ""
        self._chapter_studio_chapter_numbers: dict[str, int] = {}  # per-project chapter tracking
        self._autorun_chapter_numbers: dict[str, int] = {}
        self._autorun_writing_modes_by_project: dict[str, str] = {}
        self._chapter_context_refresh_key: tuple[int, str, int] | None = None
        self._autorun_last_submitted_checkpoint: dict[tuple[str, int], str] = {}
        self._autorun_prepared_chapters: set[tuple[str, int]] = set()
        self._autorun_refresh_counts: dict[str, int] = {}
        self._autorun_cooldown_until: dict[str, float] = {}
        # (project_id, chapter) -> (checkpoint_id, 已提交次数)，用于归档重试上限与退避
        self._autorun_resolve_attempts: dict[tuple[str, int], tuple[str, int]] = {}
        self._autorun_driving_projects: set[str] = set()
        self._is_closing: bool = False
        self._was_minimized: bool = False
        self._autorun_ticker: QTimer | None = None
        self._sleep_wake_monitor = SleepWakeMonitor(parent=self)
        self._sleep_wake_monitor.system_woke.connect(self._on_system_woke)
        self._wake_recovery_timer = QTimer(self)
        self._wake_recovery_timer.setSingleShot(True)
        self._wake_recovery_timer.setInterval(5_000)
        self._wake_recovery_timer.timeout.connect(self._complete_wake_recovery)
        self._pending_autorun_job_ids: set[str] = (
            set()
        )  # init_long job_ids that should trigger book_auto
        self._job_manager = DesktopJobManager()
        self._application_active = True
        self._application_state_connection = None
        app = QApplication.instance()
        if app is not None:
            self._application_state_connection = app.applicationStateChanged.connect(
                self._on_application_state_changed
            )
        self._task_observation_store = TaskObservationStore(self)
        self._notification_sounds = DesktopNotificationSoundPlayer(self)
        self._init_ui_components()
        # Install shared scroll fades only after the complete window subtree is
        # constructed.  Doing this from a global QApplication Polish filter
        # re-enters partially-built PySide scroll areas and can segfault.
        from novel_forge.desktop.components.scroll_fade import refresh_scroll_edge_fades

        refresh_scroll_edge_fades(cast(QWidget, self))

    def _get_chapter_number(self, project_id: str) -> int:
        """Get the tracked chapter number for a project, defaulting to 1."""
        if not project_id:
            return 1
        return self._chapter_studio_chapter_numbers.get(project_id, 1)

    def _set_chapter_number(self, project_id: str, chapter_number: int) -> None:
        """Set the tracked chapter number for a project."""
        if project_id:
            self._chapter_studio_chapter_numbers[project_id] = max(1, chapter_number)

    def _default_autorun_chapter_number(self, project_id: str) -> int:
        """Return the best initial backend auto-run target for a project."""
        if (
            project_id
            and self._snapshot is not None
            and (detail := self._snapshot.details.get(project_id)) is not None
            and detail.mode == "long"
        ):
            next_chapter = getattr(detail, "next_chapter", None)
            if next_chapter is None:
                latest = int(getattr(detail, "latest_chapter", 0) or 0)
                completed = int(getattr(detail, "completed_chapters", 0) or 0)
                next_chapter = (latest or completed) + 1
            return max(1, int(next_chapter or 1))
        return self._get_chapter_number(project_id)

    def _get_autorun_chapter_number(self, project_id: str) -> int:
        """Get the backend auto-run target, isolated from the visible rail selection."""
        if not project_id:
            return 1
        if project_id in self._autorun_chapter_numbers:
            return max(1, int(self._autorun_chapter_numbers.get(project_id) or 1))
        return self._default_autorun_chapter_number(project_id)

    def _set_autorun_chapter_number(self, project_id: str, chapter_number: int) -> None:
        """Set the backend auto-run target without changing the visible chapter."""
        if project_id:
            self._autorun_chapter_numbers[project_id] = max(1, chapter_number)

    def _sync_chapter_studio_page_chapter(self, project_id: str, chapter_number: int) -> None:
        """Keep the visible chapter-studio page aligned with the window target.

        Project selection is driven by the chapter-studio combo box, while the
        canonical chapter target lives in this window so each project can keep
        its own last-viewed chapter.  When those differ, the async context
        snapshot can be valid but still rejected by bind_studio() as stale.
        """
        studio_page = getattr(self, "_pages", {}).get("chapter_studio")
        if studio_page is None or not hasattr(studio_page, "current_project_id"):
            return
        if studio_page.current_project_id() != project_id:
            return
        if not hasattr(studio_page, "current_chapter_number"):
            return
        target_chapter = max(1, chapter_number)
        if studio_page.current_chapter_number() == target_chapter:
            return
        chapter_spin = getattr(studio_page, "_chapter_spin", None)
        if chapter_spin is None:
            return
        chapter_spin.blockSignals(True)
        try:
            chapter_spin.setValue(target_chapter)
        finally:
            chapter_spin.blockSignals(False)

    def _init_ui_components(self) -> None:
        self._job_lock = QMutex()
        self._page_animation: QPropertyAnimation | None = None
        self._page_animation_widget: QWidget | None = None
        self._page_transition_animations: list[QPropertyAnimation] = []
        self._page_transition_widgets: list[QWidget] = []
        self._previous_widget: QWidget | None = None
        self._last_step_text = ""
        self._last_cost_text = ""
        self._cost_fade_anim: QPropertyAnimation | None = None
        self._step_fade_out_anim: QPropertyAnimation | None = None
        self._step_fade_in_anim: QPropertyAnimation | None = None
        self._step_fade_generation = 0
        self._skeleton_overlay: QFrame | None = None
        self._skeleton_detail_label: QLabel | None = None
        self._skeleton_loading_label: QLabel | None = None
        self._skeleton_loading_timer: QTimer | None = None
        self._skeleton_fade_animation: QPropertyAnimation | None = None
        self._skeleton_loading_tick: int = 0
        self._top_button_handlers: dict[QPushButton, Callable[[], None]] = {}
        self._layout_density: str = ""
        self._workspace_root: QWidget | None = None
        self._side_rail: QFrame | None = None
        self._side_rail_layout: QVBoxLayout | None = None
        self._side_rail_collapsed: bool = False
        self._side_rail_width_anim: Any | None = None
        self._side_rail_toggle_btn: Any | None = None
        self._side_rail_collapsible_widgets: list[QWidget] = []
        self._content_layout: QVBoxLayout | None = None
        self._top_bar: QFrame | None = None
        self._top_layout: QHBoxLayout | None = None
        self._ui_session_restored: bool = False
        # Page state is restored lazily because most desktop pages are also
        # created lazily.  Each page may opt in with restore_ui_state().
        self._pending_page_ui_states: dict[str, dict[str, Any]] = {}
        self._restored_page_ui_state_ids: set[str] = set()
        self._last_changed_sections: set[str] = set()
        self._page_placeholders: dict[str, QWidget] = {}
        self._connected_page_signals: set[str] = set()
        self._page_signal_connections_ready: bool = False
        self._global_signals_connected: bool = False
        self._force_instant_page_transition_once: bool = False
        self._page_prewarm_queue: list[str] = []
        self._page_prewarm_done: bool = False
        self._page_preload_queue: list[str] = list(self._PAGE_PRELOAD_MODULES)
        self._page_preload_done: bool = False
        self._pending_refresh_section_hints: set[str] = set()
        self._refresh_section_hints_current: set[str] = set()
        self._floating_task_companion: FloatingTaskCompanion | None = None
        self._task_companion_position_ratio: tuple[float, float] | None = None
        self._task_focus_dialog: TaskFocusDialog | None = None
        self._floating_stream_window: FloatingStreamWindow | None = None
        self._active_page_id: str = "dashboard"
        self._previous_page_id: str | None = None
        self._last_switch_page_id: str | None = None
        self._page_activate_generation: int = 0
        self._last_page_switch_at: float = 0.0
        self._rapid_page_switch: bool = False

        # Per-window observable state
        self._window_state = WindowState(parent=self)
        get_ui_store().attach_window_state(self._window_state)

        self._build_window()
        self._build_ui()
        self._register_shortcuts()
        self._build_task_companion()
        self._apply_window_density(force=True)
        ToastManager.instance().set_parent(self)
        self._page_workspace_revision = {page_id: -1 for page_id in page_registry.list_pages()}
        self._jobs_bind_timer = QTimer(self)
        self._jobs_bind_timer.setSingleShot(True)
        self._jobs_bind_timer.setInterval(JOB_BIND_DEBOUNCE_MS)
        self._jobs_bind_timer.timeout.connect(self._bind_jobs)
        self._workspace_refresh_schedule_timer = QTimer(self)
        self._workspace_refresh_schedule_timer.setSingleShot(True)
        self._workspace_refresh_schedule_timer.setInterval(0)
        self._workspace_refresh_schedule_timer.timeout.connect(
            lambda: self.refresh_workspace(force=True)
        )
        self._ui_session_save_timer = QTimer(self)
        self._ui_session_save_timer.setSingleShot(True)
        self._ui_session_save_timer.setInterval(500)
        self._ui_session_save_timer.timeout.connect(self._save_ui_session)
        self._page_prewarm_timer = QTimer(self)
        self._page_prewarm_timer.setSingleShot(True)
        self._page_prewarm_timer.timeout.connect(self._prewarm_next_page)
        self._page_preload_timer = QTimer(self)
        self._page_preload_timer.setSingleShot(True)
        self._page_preload_timer.timeout.connect(self._preload_next_page_module)
        self._density_resize_timer = QTimer(self)
        self._density_resize_timer.setSingleShot(True)
        self._density_resize_timer.setInterval(DENSITY_RESIZE_DEBOUNCE_MS)
        self._density_resize_timer.timeout.connect(self._apply_window_density)
        self._fs_watcher: QFileSystemWatcher | None = None
        self._connect_signals()
        self._show_skeleton_overlay()
        # Build RuntimeServices off the GUI thread. The skeleton overlay stays
        # visible until ``_on_runtime_services_ready`` lands on the GUI thread,
        # at which point we kick off the first workspace refresh, the Ollama
        # status announcement, and the autorun ticker. These three were
        # previously called synchronously from __init__ and would touch
        # ``self._workspace`` before it was populated.
        self._start_runtime_services_init()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(lambda: self.refresh_workspace(force=False))
        self._refresh_timer.start(WORKSPACE_REFRESH_INTERVAL_MS)

        # Event-driven refresh via filesystem watcher (timer is fallback)
        self._fs_watcher = QFileSystemWatcher(self)
        self._fs_watcher_debounce_timer = QTimer(self)
        self._fs_watcher_debounce_timer.setSingleShot(True)
        self._fs_watcher_debounce_timer.setInterval(FS_WATCHER_DEBOUNCE_MS)
        self._fs_watcher_debounce_timer.timeout.connect(lambda: self.refresh_workspace(force=True))
        self._fs_watcher.fileChanged.connect(
            lambda _path="": self._fs_watcher_debounce_timer.start()
        )
        self._fs_watcher.directoryChanged.connect(
            lambda _path="": self._fs_watcher_debounce_timer.start()
        )

    def _start_runtime_services_init(self) -> None:
        """Kick off RuntimeServices construction on the ui_io thread pool.

        The skeleton overlay remains visible until ``_on_runtime_services_ready``
        runs on the GUI thread. If the worker fails (e.g. corrupt config), we
        still need to surface a usable window, so the failure path logs and
        leaves ``self._workspace`` as None -- ``refresh_workspace`` and the
        startup-dependent handlers short-circuit on that.

        Tests that monkeypatch ``DesktopWorkspaceService.from_settings`` to a
        fake and run against a non-executing fake thread pool set
        ``_sync_runtime_services_init = True`` on the window subclass (or via
        monkeypatch) so the ready callback fires synchronously, preserving the
        pre-B2 construction contract.
        """
        if self._sync_runtime_services_init:
            from novel_forge.desktop.workspace import DesktopWorkspaceService

            workspace = DesktopWorkspaceService.from_settings(mock=self._mock_enabled)
            self._on_runtime_services_ready(workspace)
            return
        worker = _RuntimeServicesInitRunnable(mock=self._mock_enabled)
        self._workspace_init_worker = worker
        worker.signals.services_ready.connect(self._on_runtime_services_ready)
        worker.signals.failed.connect(self._on_runtime_services_init_failed)
        _desktop_thread_pools().ui_io_pool.start(worker)

    def _on_runtime_services_ready(self, workspace: Any) -> None:
        """GUI-thread callback when RuntimeServices finishes building."""
        self._workspace = workspace
        self._workspace_init_worker = None
        # Now that the workspace is populated, run the startup sequence that
        # was previously inline in __init__.
        self.refresh_workspace()
        self._start_autorun_ticker()
        self._sleep_wake_monitor.start()

    def _on_runtime_services_init_failed(self, message: str) -> None:
        """GUI-thread callback when RuntimeServices construction raises."""
        self._workspace_init_worker = None
        self._hide_skeleton_overlay()
        _logger.error("RuntimeServices 初始化失败: %s", message)
        self.show_priority_status(
            f"运行时服务初始化失败：{message}", 8000, self._STATUS_ERROR
        )

    def _start_autorun_ticker(self) -> None:
        # Periodic ticker to drive background auto-run regardless of job events.
        # Without this, an auto-pilot project waiting for a stale snapshot to
        # catch up may stall indefinitely (multi-project chapter 连跑 was
        # observed to interrupt and require manual intervention because the
        # next decision cycle was only re-triggered by job-change events that
        # never came).
        if self._autorun_ticker is not None:
            if not self._autorun_ticker.isActive():
                self._autorun_ticker.start()
            return
        self._autorun_ticker = QTimer(self)
        self._autorun_ticker.setInterval(6_000)
        self._autorun_ticker.timeout.connect(self._drive_active_autoruns)
        self._autorun_ticker.start()

    # ------------------------------------------------------------------
    # Sleep/Wake recovery
    # ------------------------------------------------------------------

    def _on_system_woke(self, sleep_duration: float) -> None:
        """Handle system resume from sleep.

        Strategy:
        1. Suppress QFileSystemWatcher events for 5 s (macOS delivers a burst
           of stale notifications after wake: Spotlight reindex, iCloud sync).
        2. Pause the autorun ticker briefly to avoid submitting jobs against
           stale workspace state.
        3. Reset debounce timestamps so the next periodic refresh starts clean.
        4. Schedule a single deferred workspace refresh after the suppression
           window expires, giving the filesystem time to stabilise.
        """
        _logger.info(
            "System woke from sleep (%.1f s) — starting graceful recovery", sleep_duration
        )

        # 1. Block FS watcher signals to absorb the post-wake event storm.
        if self._fs_watcher is not None:
            self._fs_watcher.blockSignals(True)

        # 2. Pause autorun ticker during recovery.
        if self._autorun_ticker is not None and self._autorun_ticker.isActive():
            self._autorun_ticker.stop()

        # Pause refresh sources that may all become due on the first event-loop
        # turn after wake. Their pending state is preserved and folded into the
        # single forced refresh at the end of recovery.
        self._refresh_timer.stop()
        self._fs_watcher_debounce_timer.stop()
        self._workspace_refresh_schedule_timer.stop()

        # 3. Reset refresh debounce so the recovery refresh is not skipped.
        # Preserve the in-flight guard: a refresh worker may have been running
        # when the machine slept, and clearing the flag would allow a second
        # worker to race it and apply an older snapshot last.
        self._last_refresh_time = 0.0
        self._last_refresh_complete_time = 0.0
        self._refresh_force_pending = True

        # 3b. Flush stale events accumulated during sleep.
        self._job_manager.flush_stale_events_after_wake()

        # 4. Staggered recovery sequence. A reusable single-shot timer coalesces
        # duplicate wake notifications instead of scheduling repeated refreshes.
        # +5 s: unblock FS watcher, restart timers, do one refresh.
        self._wake_recovery_timer.start()

    def _on_application_state_changed(self, state: Qt.ApplicationState) -> None:
        """Throttle presentation-only work while macOS keeps NIMO in background."""
        active = state == Qt.ApplicationState.ApplicationActive
        if active == self._application_active or self._is_closing:
            return
        self._application_active = active
        self._job_manager.set_ui_active(active)

        if not active:
            self._refresh_force_pending = True
            self._refresh_timer.stop()
            self._fs_watcher_debounce_timer.stop()
            self._workspace_refresh_schedule_timer.stop()
            return

        # Sleep/wake recovery owns its own staggered restart sequence.
        if self._wake_recovery_timer.isActive():
            return
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()
        self._safe_deferred(150, lambda: self.refresh_workspace(force=True))

    def _complete_wake_recovery(self) -> None:
        """Finalise wake recovery: re-enable watchers and refresh state."""
        if self._is_closing:
            return
        _logger.info("Wake recovery complete — resuming normal operation")

        # Re-enable FS watcher.
        if self._fs_watcher is not None:
            self._fs_watcher.blockSignals(False)

        # Restart autorun ticker.
        if self._autorun_ticker is not None and not self._autorun_ticker.isActive():
            self._autorun_ticker.start()

        # Reset the periodic refresh deadline so it cannot immediately collide
        # with the forced recovery refresh below.
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

        # Perform a clean workspace refresh to pick up any changes made
        # during sleep (e.g. by other processes, Time Machine, etc.).
        self.refresh_workspace(force=True)

    def _build_window(self) -> None:
        self.setWindowTitle(UIStrings.APP_TITLE)
        available = self._available_screen_size()
        min_width = min(
            _WINDOW_DEFAULT_MIN_WIDTH,
            max(760, int(available.width() * _WINDOW_SCREEN_WIDTH_RATIO)),
        )
        min_height = min(
            _WINDOW_DEFAULT_MIN_HEIGHT,
            max(620, int(available.height() * _WINDOW_SCREEN_HEIGHT_RATIO)),
        )
        start_width = min(
            _WINDOW_DEFAULT_START_WIDTH,
            max(min_width, int(available.width() * _WINDOW_SCREEN_WIDTH_RATIO)),
        )
        start_height = min(
            _WINDOW_DEFAULT_START_HEIGHT,
            max(min_height, int(available.height() * _WINDOW_SCREEN_HEIGHT_RATIO)),
        )
        self.setMinimumSize(min_width, min_height)
        self.resize(start_width, start_height)

    def _available_screen_size(self) -> QSize:
        screen = QApplication.primaryScreen()
        if screen is None:
            return self.size()
        return screen.availableGeometry().size()

    def _build_ui(self) -> None:
        from novel_forge.desktop.theme.palettes import (
            DEFAULT_DESKTOP_THEME_ID,
            desktop_theme_token_overrides,
        )
        from novel_forge.desktop.tokens.colors import COLORS

        # Resolve gradient colors from current theme tokens
        theme_id = DEFAULT_DESKTOP_THEME_ID
        try:
            from novel_forge.core.config import get_settings

            theme_id = getattr(get_settings(), "desktop_theme", theme_id) or theme_id
        except Exception:
            pass
        overrides = desktop_theme_token_overrides(theme_id)

        def _tok(name: str) -> str:
            return overrides.get(name, COLORS[name][0])

        root = CachedGradientWidget(
            stops=[
                (0.0, _tok("bg.workspace")),
                (0.5, _tok("bg.workspace.mid")),
                (1.0, _tok("bg.workspace.end")),
            ],
            angle=135.0,
        )
        root.setObjectName("workspaceRoot")
        self._workspace_root = root
        self.setCentralWidget(root)

        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        shell.addWidget(self._build_side_rail())
        shell.addWidget(self._build_content(), 1)

        self.setStatusBar(QStatusBar())
        # Permanent widgets (never overwritten by transient showMessage()).
        self._status_step_label = QLabel("")
        self._status_step_label.setObjectName("statusStepLabel")
        self._status_cost_label = QLabel("")
        self._status_cost_label.setObjectName("statusCostLabel")
        self.statusBar().addPermanentWidget(self._status_step_label)
        self.statusBar().addPermanentWidget(self._status_cost_label)

    def _register_shortcuts(self) -> None:
        """Register global keyboard shortcuts for navigation."""
        from novel_forge.desktop.shortcuts import register_global_shortcuts

        self._global_shortcuts = register_global_shortcuts(self)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._density_resize_timer.start()
        overlay = self._skeleton_overlay
        if overlay is not None:
            overlay.setGeometry(self.rect())
        self._sync_active_indicator_geometry()
        self._position_task_companion()

    def changeEvent(self, event: QEvent) -> None:
        """Pause refresh I/O while minimized and recover once on restore."""
        super().changeEvent(event)
        if self._is_closing or event.type() != QEvent.Type.WindowStateChange:
            return

        refresh_timer = getattr(self, "_refresh_timer", None)
        if self.isMinimized():
            self._was_minimized = True
            # Files can change while the window is hidden. Remember one forced
            # refresh instead of letting periodic and watcher refreshes compete.
            self._refresh_force_pending = True
            if refresh_timer is not None:
                refresh_timer.stop()
            return

        if not self._was_minimized:
            return
        self._was_minimized = False

        # A wake recovery already owns timer restart and the forced refresh.
        wake_timer = getattr(self, "_wake_recovery_timer", None)
        if wake_timer is not None and wake_timer.isActive():
            return
        if refresh_timer is not None and not refresh_timer.isActive():
            refresh_timer.start()
        self._safe_deferred(100, lambda: self.refresh_workspace(force=True))

    def _apply_window_density(self, *, force: bool = False) -> None:
        density = (
            "compact"
            if self.width() <= _COMPACT_WIDTH_THRESHOLD
            or self.height() <= _COMPACT_HEIGHT_THRESHOLD
            else "regular"
        )
        if not force and density == self._layout_density:
            return
        self._layout_density = density
        compact = density == "compact"

        if self._side_rail is not None:
            self._sync_side_rail_collapsed(animate=False)
        if self._content_layout is not None:
            margin = 14 if compact else 18
            self._content_layout.setContentsMargins(margin, margin, margin, margin)
            self._content_layout.setSpacing(10 if compact else 12)
        if self._top_bar is not None:
            self._top_bar.setMinimumHeight(84 if compact else 96)
        if self._top_layout is not None:
            self._top_layout.setContentsMargins(
                18 if compact else 22,
                10 if compact else 14,
                18 if compact else 22,
                10 if compact else 14,
            )
            self._top_layout.setSpacing(8 if compact else 10)

        root = self._workspace_root
        if root is not None:
            root.setProperty("density", density)
            self._refresh_widget_style(root)
        self._sync_active_indicator_geometry()
        self._position_task_companion()

    @staticmethod
    def _refresh_widget_style(widget: QWidget) -> None:
        """Refresh QSS styles for a widget and its descendants.

        Optimized version that:
        - Uses iterative DFS traversal instead of ``findChildren()`` to avoid
          building a full widget list (reduces memory pressure on complex pages)
        - Skips widgets whose density property already matches the target
        - Only calls unpolish/polish on widgets that actually changed
        """
        density = widget.property("density")
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)

        # Iterative DFS to avoid building a full list via findChildren()
        stack: list[QWidget] = []
        try:
            children = widget.children()
        except RuntimeError:
            return
        for child in children:
            if isinstance(child, QWidget):
                stack.append(child)

        while stack:
            child = stack.pop()
            try:
                # Only update if density actually differs (avoids redundant polish)
                if child.property("density") != density:
                    child.setProperty("density", density)
                    child_style = child.style()
                    child_style.unpolish(child)
                    child_style.polish(child)
                # Always recurse into children — a matching parent does not
                # guarantee that all descendants already have the correct
                # density (e.g. widgets created between density changes).
                grandchildren = child.children()
                for grandchild in grandchildren:
                    if isinstance(grandchild, QWidget):
                        stack.append(grandchild)
            except RuntimeError:
                continue

    def _apply_workspace_refresh(
        self,
        snapshot: DesktopWorkspaceSnapshot,
        service: DesktopWorkspaceService,
        payload: dict[str, Any],
        section_hashes: dict[str, str],
        changed_sections: set[str],
        snapshot_hash: str,
    ) -> None:
        """Called on main thread when background refresh + hash computation finished."""
        self._active_workspace_refresh_worker = None
        if not self._window_callbacks_allowed():
            self._refresh_in_progress = False
            return
        t0 = time.monotonic()
        self._refresh_in_progress = False
        self._last_refresh_complete_time = time.monotonic()
        force_bind = self._refresh_force_current
        self._refresh_force_current = False
        section_hints = set(self._refresh_section_hints_current)
        self._refresh_section_hints_current.clear()
        self._workspace = service
        self._workspace_revision += 1

        first_snapshot = self._last_snapshot_payload is None
        self._section_hash_cache = section_hashes
        effective_changed_sections = set(changed_sections)
        effective_changed_sections.update(section_hints)
        content_changed = (
            force_bind
            or bool(section_hints)
            or self._last_snapshot_hash is None
            or snapshot_hash != self._last_snapshot_hash
        )
        self._last_snapshot_hash = snapshot_hash
        self._snapshot = snapshot
        self._last_snapshot_payload = payload
        self._update_fs_watcher_paths(snapshot)
        self._last_changed_sections = effective_changed_sections if content_changed else set()

        if first_snapshot:
            self._hide_skeleton_overlay()

        if not content_changed:
            self._force_refresh_retries += 1
            if (
                self._refresh_force_pending
                and self._force_refresh_retries < self._FORCE_REFRESH_MAX_RETRIES
            ):
                self._safe_deferred(0, lambda: self.refresh_workspace(force=True))
            else:
                if self._force_refresh_retries >= self._FORCE_REFRESH_MAX_RETRIES:
                    _logger.warning(
                        "Force-refresh retry limit reached (%d); stopping retry loop",
                        self._FORCE_REFRESH_MAX_RETRIES,
                    )
                self._refresh_force_pending = False
                self._force_refresh_retries = 0
            return

        self._force_refresh_retries = 0

        # When force_bind=True but no data actually changed, skip the expensive
        # page-binding and UI-update cycle. Clear the pending flag to break the
        # feedback loop — scheduling another force refresh here would re-enter
        # the same no-change path.
        if not effective_changed_sections and not first_snapshot:
            self._refresh_force_pending = False
            return

        # Disconnect signals from old context refresh workers before clearing
        # the set, so late completions in the thread pool cannot invoke
        # lambdas that reference this window.
        for _old_ctx_worker in self._active_context_refresh_workers:
            try:
                _old_ctx_worker.signals.finished.disconnect()
            except (RuntimeError, TypeError):
                pass
            try:
                _old_ctx_worker.signals.failed.disconnect()
            except (RuntimeError, TypeError):
                pass
        self._active_context_refresh_workers.clear()
        self._chapter_context_refresh_key = None
        self._sync_chapter_studio_target_with_snapshot(snapshot)

        current_page_id = self._current_page_id()
        pages_to_bind = self._pages_needing_bind(
            effective_changed_sections,
            current_page_id,
            initial=first_snapshot,
        )

        # Dirty-flag pattern (Wave 2 / Task 8): only bind the visible page
        # immediately; defer hidden pages to _pending_rebind_pages for lazy
        # binding on switch_page.  Exception: chapter_studio is bound immediately
        # when auto-pilot is running (it drives automated job dispatch).
        auto_pilot_active = bool(self._autorun_driving_projects)

        if first_snapshot:
            for page_id in pages_to_bind:
                self._bind_workspace_for_page(page_id, force=True)
            self._pending_rebind_pages.clear()
            self._pending_rebind_sections.clear()
        else:
            for page_id in pages_to_bind:
                if page_id == current_page_id:
                    self._bind_workspace_for_page(page_id, force=True)
                    self._clear_page_pending_sections(page_id)
                elif (
                    page_id == "chapter_studio"
                    and auto_pilot_active
                    and self._changed_sections_overlap(
                        effective_changed_sections,
                        self._PAGE_SECTION_MAP.get("chapter_studio", frozenset()),
                    )
                ):
                    self._bind_workspace_for_page(page_id, force=True)
                    self._clear_page_pending_sections(page_id)
                else:
                    self._mark_page_sections_pending(page_id, effective_changed_sections)
        self._refresh_chapter_studio_context()
        workflow_page = self._pages.get("workflow")
        if workflow_page is not None:
            workflow_page.set_mock_mode(self._mock_enabled)
        settings_page = self._pages.get("settings")
        if settings_page is not None:
            settings_page.set_mock_mode(self._mock_enabled)
        self._bind_jobs()

        words = snapshot.metrics.total_words
        projects = snapshot.metrics.total_projects
        loaded_providers = snapshot.overview.providers

        self._rail_summary.setText(UIStrings.RAIL_FOOTER_ACTIVE.format(projects=projects))

        info = ""
        if words > 0:
            info += UIStrings.RAIL_FOOTER_WORDS.format(words=words)
        else:
            info += UIStrings.RAIL_FOOTER_NO_WORDS
        if loaded_providers:
            info += UIStrings.RAIL_FOOTER_PROVIDERS.format(
                providers="、".join(loaded_providers[:4])
            )
        else:
            info += UIStrings.RAIL_FOOTER_MOCK
        info += UIStrings.RAIL_FOOTER_CLOSING

        self._rail_path.setText(info)

        self._apply_page_meta(self._current_page_id())

        if not self._ui_session_restored:
            self._ui_session_restored = True
            self._load_ui_session()
            self._schedule_idle_page_prewarm()

        elapsed_ms = (time.monotonic() - t0) * 1000
        if __debug__:
            self._perf_probe_times.append(elapsed_ms)
        if elapsed_ms > 200:
            _logger.warning(
                "Workspace refresh took %.0fms (sections changed: %s, pages bound: %s)",
                elapsed_ms,
                effective_changed_sections or "none",
                pages_to_bind,
            )

        if self._refresh_force_pending:
            self._safe_deferred(0, lambda: self.refresh_workspace(force=True))
