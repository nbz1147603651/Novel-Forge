"""Settings page — model-centric configuration with connection status."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, TypeAlias

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStackedLayout,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from novel_forge.core.config import get_settings
from novel_forge.core.parsing.temperature_jitter import is_temperature_jitter_protected
from novel_forge.core.task_catalog import (
    LONG_TEMPERATURE_TASKS,
    MULTI_TURN_TASK_KEYS,
    ROUTING_GROUPS,
    SHORT_TEMPERATURE_TASKS,
    RoutingGroup,
    RoutingSubgroup,
    RoutingTask,
)
from novel_forge.desktop.components.sizing import smart_dialog_size
from novel_forge.desktop.components.skeleton import LoadingState
from novel_forge.desktop.pages.settings.components import (
    _ConnectionTestWorker,
    _ElidedDescriptionLabel,
    _GroupBulkRow,
    _ModelDialog,
    _ModelManageCard,
    _ModelStatusCard,
    _OllamaEngineViewWorker,
    _ResearchConnectionTestWorker,
    _RuntimeCard,
    _TaskRouteRow,
)
from novel_forge.desktop.pages.settings.model_mgmt import ModelManagementMixin
from novel_forge.desktop.pages.settings.ollama import OllamaManagementMixin
from novel_forge.desktop.pages.settings.save import (
    _build_signature,
    _merge_materialized_signature,
    collect_param_env_pairs,
    save_settings,
)
from novel_forge.desktop.pages.settings_page_parameters import (
    _build_debug_section,
    _build_desktop_notification_params,
    _build_long_context_params,
    _build_long_creation_params,
    _build_long_initialization_params,
    _build_memory_params,
    _build_ollama_params,
    _build_quality_audit_params,
    _build_reading_power_params,
    _build_research_params,
    _build_short_params,
    _build_storage_params,
    _build_temperature_jitter_params,
    _build_theme_params,
    _build_tts_params,
    research_preset_payload,
)
from novel_forge.desktop.pages.standalone.humanize_library_dashboard import (
    HumanizeLibraryDashboardPage,
    _HumanizeLibraryCard,
)
from novel_forge.desktop.task_flow_errors import (
    clear_task_flow_error_archive,
    task_flow_error_archive_summary,
)
from novel_forge.desktop.theme.runtime import apply_desktop_theme, root_for_widget
from novel_forge.desktop.tokens.typography import apply_desktop_typography
from novel_forge.desktop.ui_density import configure_tab_widget_density
from novel_forge.desktop.ui_perf import ui_perf_span
from novel_forge.desktop.widgets import (
    ActionButton,
    CollapsibleSection,
    ScrollPage,
    SectionHeading,
    ask_confirmation,
    show_info_message,
    show_warning_message,
)
from novel_forge.desktop.workspace import DesktopWorkspaceSnapshot
from novel_forge.gateway.profiles import ModelProfile, TaskRouteEntry, load_or_import_profiles

_GroupBulkRowType: TypeAlias = _GroupBulkRow
_TaskRouteRowType: TypeAlias = _TaskRouteRow


def _collect_param_env_pairs(page: Any) -> dict[str, str]:
    """Backward-compatible wrapper for test monkeypatching."""
    return collect_param_env_pairs(page)


def _task_route_entry_from_mapping(raw: object) -> TaskRouteEntry | None:
    if not isinstance(raw, dict):
        return None
    profile_id = str(raw.get("profile_id", "") or "").strip()
    if not profile_id:
        return None
    return TaskRouteEntry(
        profile_id=profile_id,
        thinking=bool(raw.get("thinking", False)),
        thinking_mode=str(raw.get("thinking_mode", "") or ""),
        multi_turn=bool(raw.get("multi_turn", False)),
    )


def _bulk_route_entries_from_config(
    raw: object,
) -> tuple[TaskRouteEntry | None, list[TaskRouteEntry]]:
    if not isinstance(raw, dict):
        return None, []
    route = _task_route_entry_from_mapping(raw)
    fallbacks: list[TaskRouteEntry] = []
    for entry_raw in raw.get("fallback_routes", []):
        entry = _task_route_entry_from_mapping(entry_raw)
        if entry is not None:
            fallbacks.append(entry)
    return route, fallbacks


class SettingsPage(ModelManagementMixin, OllamaManagementMixin, ScrollPage):
    """Model-centric settings page with connection status and task routing."""

    open_root_requested = Signal()
    settings_saved = Signal()
    mock_mode_toggled = Signal(bool)
    ui_state_changed = Signal()

    _ConnectionTestWorker = _ConnectionTestWorker
    _OllamaEngineViewWorker = _OllamaEngineViewWorker
    _ResearchConnectionTestWorker = _ResearchConnectionTestWorker
    _ModelDialog = _ModelDialog
    _ModelManageCard = _ModelManageCard
    _ModelStatusCard = _ModelStatusCard
    _RuntimeCard = _RuntimeCard
    _TaskRouteRow = _TaskRouteRow
    _GroupBulkRow = _GroupBulkRow

    _ROUTE_SUBSECTIONS_BY_GROUP: dict[str, dict[str, tuple[str, str]]] = {
        group.name: {
            subgroup.start_key: (subgroup.title, subgroup.description)
            for subgroup in group.subgroups
        }
        for group in ROUTING_GROUPS
        if group.subgroups
    }
    _ROUTE_SUBGROUP_KEYS: frozenset[str] = frozenset(
        f"{group_name} / {title}"
        for group_name, sections in _ROUTE_SUBSECTIONS_BY_GROUP.items()
        for title, _desc in sections.values()
    )
    _HIDDEN_ROUTE_KEYS: frozenset[str] = frozenset()
    _VALID_ROUTE_KEYS: frozenset[str] = (
        frozenset(task.key for group in ROUTING_GROUPS for task in group.tasks) | _HIDDEN_ROUTE_KEYS
    )
    _VALID_ROUTE_GROUPS: frozenset[str] = (
        frozenset(group.name for group in ROUTING_GROUPS) | _ROUTE_SUBGROUP_KEYS
    )
    _SHORT_TEMPERATURE_TASKS = {task.key: task for task in SHORT_TEMPERATURE_TASKS}
    _LONG_TEMPERATURE_TASKS = {task.key: task for task in LONG_TEMPERATURE_TASKS}
    _ROUTE_TEMPERATURE_TASKS = {**_SHORT_TEMPERATURE_TASKS, **_LONG_TEMPERATURE_TASKS}
    # Keep automatic probes conservative: several profiles often share one provider/API key,
    # and parallel health checks can trigger transient provider-side rate limits.
    _MAX_STATUS_TEST_WORKERS = 1
    _MANUAL_STATUS_TEST_WORKERS = 2
    _AUTO_STATUS_TEST_DELAY_MS = 3000
    _POST_STATUS_GRID_TEST_DELAY_MS = 500
    # 0 means every configured profile.  The worker queue remains bounded by
    # _MAX_STATUS_TEST_WORKERS, so this completes one safe batch rather than
    # leaving lower-priority cards permanently in a manual-only state.
    _AUTO_STATUS_TEST_MAX_PROFILES = 0

    _DEFERRED_SECTION_BUILD_INTERVAL_MS = 16
    _SAVE_PREPARE_INTERVAL_MS = 12
    _THEME_PREVIEW_DELAY_MS = 80
    _STATUS_GRID_BUILD_ACTIVATE_DELAY_MS = 50  # Reduced from 100ms for faster initial display
    _STATUS_GRID_BUILD_INTERVAL_MS = 8  # Reduced from 32ms for smoother incremental builds
    _STATUS_GRID_BUILD_BATCH_SIZE = 4  # Increased from 2 for faster batch processing

    def __init__(self, eager_build: bool = True) -> None:
        super().__init__()
        # Settings is a dense form surface; keep the generic ScrollPage top
        # fade off so form controls and deferred sections never sit under an overlay.
        self.set_fade_enabled(False)
        self._eager_build = eager_build
        self._snapshot: DesktopWorkspaceSnapshot | None = None
        self._mock_enabled = False
        self._settings = get_settings()
        self._config = load_or_import_profiles(self._settings)
        self._sanitize_loaded_routing_config()
        from novel_forge.desktop.config_store import DesktopSettingsStore

        self._store = DesktopSettingsStore()
        self._test_workers: list[_ConnectionTestWorker] = []
        self._ollama_workers: list[_OllamaEngineViewWorker] = []
        self._research_test_workers: list[_ResearchConnectionTestWorker] = []
        self._status_cards: dict[str, _ModelStatusCard] = {}
        self._detected_capabilities: dict[str, tuple[bool, bool]] = {}
        self._status_test_queue: list[tuple[int, ModelProfile, bool, bool]] = []
        self._status_tests_active = 0
        self._status_test_generation = 0
        self._status_test_next_token = 0
        self._status_test_running: dict[int, tuple[str, int | None, bool, float]] = {}
        self._status_test_ignored: set[int] = set()
        self._status_detection_busy = False
        self._route_combos_refresh_pending = False
        self._status_tests_started = False
        self._status_tests_pending = False
        self._status_auto_activation_active = False
        self._status_grid_build_profiles: list[ModelProfile] = []
        self._status_grid_build_cursor = 0
        # When eager_build=False, grid is deferred so mark as incomplete until built
        self._status_grid_build_complete = eager_build
        self._model_status_grid: QGridLayout | None = None  # built by _build_model_status_area()
        self._model_status_grid_host: QWidget | None = None
        self._model_status_stack: QStackedLayout | None = None
        self._model_status_loading: LoadingState | None = None
        self._status_grid_has_pending_tests = False
        self._status_grid_auto_profile_ids: set[str] | None = None
        self._ollama_refresh_started = False
        self._error_archive_summary_label: QLabel | None = None
        self._last_saved_signature = ""
        self._last_saved_signature_pending = not eager_build
        self._signature_materialization_depth = 0
        self._signature_before_materialization = ""
        self._deferred_sections: list[CollapsibleSection] = []
        self._deferred_build_cursor = 0
        self._deferred_build_complete = eager_build
        self._pending_session_tab_states: dict[str, str] = {}
        self._pending_session_scroll_value: int | None = None
        self._session_tab_tracking_ids: set[int] = set()
        self._hero_and_grid_built = eager_build
        self._runtime_cards: dict[str, Any] = {}  # populated by _build_hero()
        self._save_btn: ActionButton | None = None
        self._save_prepare_timer: QTimer | None = None
        self._theme_preview_timer: QTimer | None = None
        self._pending_save_sections: list[CollapsibleSection] = []
        self._save_in_progress = False
        self._pending_theme_preview_id: str | None = None
        self._lazy_route_groups: dict[
            int, tuple[QTabWidget, RoutingGroup, list[tuple[str, str, bool]]]
        ] = {}
        self._lazy_route_subgroups: dict[
            tuple[int, int],
            tuple[QTabWidget, RoutingGroup, RoutingSubgroup, list[tuple[str, str, bool]]],
        ] = {}
        # 关键定时器 — 仅保留页面切换必需的 deferred_build_timer；
        # 其余 timer 延迟到 activate / 首次使用时再创建（见 _ensure_*_timer）。
        self._deferred_build_timer = QTimer(self)
        self._deferred_build_timer.setSingleShot(True)
        self._deferred_build_timer.setInterval(self._DEFERRED_SECTION_BUILD_INTERVAL_MS)
        self._deferred_build_timer.timeout.connect(self._build_next_deferred_section)
        # 延迟创建的 timer 占位（None = 尚未创建）
        self._refresh_combos_timer: QTimer | None = None
        self._status_test_watchdog_timer: QTimer | None = None
        self._status_test_start_timer: QTimer | None = None
        self._status_grid_build_timer: QTimer | None = None
        self._param_widgets: dict[str, Any] = {
            "_short_temp_spins": {},
            "_long_temp_spins": {},
        }
        self._route_rows: dict[str, _TaskRouteRowType] = {}
        self._group_bulk_rows: dict[str, _GroupBulkRowType] = {}
        self._bulk_route_task_keys: dict[str, list[str]] = {}
        with ui_perf_span("settings_page_build", eager_build=eager_build):
            self._build_ui()
        self._wire_session_tab_tracking()
        # Lazy pages start with a partial UI-backed signature. Each deferred
        # materialization extends this baseline without treating newly-created
        # controls as user edits.
        self._last_saved_signature = _build_signature(self)

        # 关键优化：在页面构建完成后，立即开始后台预加载模型连接状态
        # 这样当用户首次切换到火候页面时，缓存已经准备好，无需等待
        self._prewarm_timer = QTimer(self)
        self._prewarm_timer.setSingleShot(True)
        self._prewarm_timer.timeout.connect(self.prewarm_model_cache)
        self._prewarm_timer.start(2000)

    def _ensure_refresh_combos_timer(self) -> QTimer:
        """Lazily create the route-combo refresh timer."""
        if self._refresh_combos_timer is None:
            self._refresh_combos_timer = QTimer(self)
            self._refresh_combos_timer.setSingleShot(True)
            self._refresh_combos_timer.setInterval(150)
            self._refresh_combos_timer.timeout.connect(self._refresh_route_combos)
        return self._refresh_combos_timer

    def _ensure_status_test_watchdog_timer(self) -> QTimer:
        if self._status_test_watchdog_timer is None:
            self._status_test_watchdog_timer = QTimer(self)
            self._status_test_watchdog_timer.setInterval(1000)
            self._status_test_watchdog_timer.timeout.connect(self._on_status_test_watchdog)
        return self._status_test_watchdog_timer

    def _ensure_status_test_start_timer(self) -> QTimer:
        if self._status_test_start_timer is None:
            self._status_test_start_timer = QTimer(self)
            self._status_test_start_timer.setSingleShot(True)
            self._status_test_start_timer.setInterval(self._AUTO_STATUS_TEST_DELAY_MS)
            self._status_test_start_timer.timeout.connect(self._start_pending_status_tests)
        return self._status_test_start_timer

    def _ensure_status_grid_build_timer(self) -> QTimer:
        if self._status_grid_build_timer is None:
            self._status_grid_build_timer = QTimer(self)
            self._status_grid_build_timer.setSingleShot(True)
            self._status_grid_build_timer.setInterval(self._STATUS_GRID_BUILD_INTERVAL_MS)
            self._status_grid_build_timer.timeout.connect(self._build_next_status_card_batch)
        return self._status_grid_build_timer

    def _ensure_save_prepare_timer(self) -> QTimer:
        if self._save_prepare_timer is None:
            self._save_prepare_timer = QTimer(self)
            self._save_prepare_timer.setSingleShot(True)
            self._save_prepare_timer.setInterval(self._SAVE_PREPARE_INTERVAL_MS)
            self._save_prepare_timer.timeout.connect(self._prepare_next_save_section)
        return self._save_prepare_timer

    def _ensure_theme_preview_timer(self) -> QTimer:
        if self._theme_preview_timer is None:
            self._theme_preview_timer = QTimer(self)
            self._theme_preview_timer.setSingleShot(True)
            self._theme_preview_timer.setInterval(self._THEME_PREVIEW_DELAY_MS)
            self._theme_preview_timer.timeout.connect(self._apply_pending_desktop_theme)
        return self._theme_preview_timer

    def _build_ui(self) -> None:
        content_layout = self.body_layout
        if self._eager_build:
            content_layout.insertWidget(0, self._build_hero())
            self._build_model_status_area(content_layout)
            self._build_settings_sections(content_layout, deferred=False)
        else:
            # 冷启动：hero 面板和模型状态网格均延迟构建，仅放一个轻量占位
            content_layout.insertWidget(0, self._build_hero_placeholder())
            self._build_settings_sections(content_layout, deferred=True)
        content_layout.addStretch()

    def _build_hero_placeholder(self) -> QLabel:
        """Create a lightweight placeholder shown while the real hero panel is deferred."""
        placeholder = QLabel("加载中…")
        placeholder.setObjectName("emptyMessage")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hero_placeholder = placeholder
        return placeholder

    def _build_model_status_area(self, content_layout: QVBoxLayout) -> None:
        """Build the model-status heading + grid (shared by eager & deferred paths)."""
        model_heading = SectionHeading(
            "模型连接状态",
            "自动检测模型接口连通性。卡片左上绿灯 = 正常（含延迟），红灯 = 异常；底部能力红点 = 不支持。",
        )
        content_layout.addWidget(model_heading)
        # Keep the result grid mounted behind a single reusable loading state.
        # Connection workers update their cards off the visible path, then the
        # completed batch is revealed at once instead of reflowing one card per
        # probe result.
        status_host = QWidget()
        status_host.setObjectName("modelStatusContentHost")
        status_host.setMinimumHeight(96)
        self._model_status_stack = QStackedLayout(status_host)
        self._model_status_stack.setContentsMargins(0, 0, 0, 0)
        self._model_status_loading = LoadingState(
            "正在检测模型连接…",
            "将在全部检测完成后显示结果",
            card_count=0,
            parent=status_host,
        )
        # The loading page is an overlay state, not a source of geometry for
        # the result grid. Ignoring its generic 112 px size hint prevents one
        # model card from stretching vertically, while status_host keeps a
        # stable compact footprint before the real cards are mounted.
        self._model_status_loading.setMinimumHeight(0)
        self._model_status_loading.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Ignored,
        )
        self._model_status_grid_host = QWidget(status_host)
        self._model_status_grid = QGridLayout(self._model_status_grid_host)
        self._model_status_grid.setHorizontalSpacing(14)
        self._model_status_grid.setVerticalSpacing(14)
        self._model_status_stack.addWidget(self._model_status_loading)
        self._model_status_stack.addWidget(self._model_status_grid_host)
        self._model_status_stack.setCurrentWidget(self._model_status_loading)
        content_layout.addWidget(status_host)
        self._refresh_model_status_grid(deferred=not self._eager_build)

    def _ensure_hero_and_grid_built(self) -> None:
        """Insert the real hero panel and model-status grid on first deferred build."""
        if self._hero_and_grid_built:
            return
        self._hero_and_grid_built = True
        # Remove the lightweight placeholder
        placeholder = getattr(self, "_hero_placeholder", None)
        if placeholder is not None:
            placeholder.setParent(None)
            placeholder.deleteLater()
            self._hero_placeholder = None  # type: ignore[assignment]
        # Insert hero at the top of body_layout (before deferred sections)
        hero = self._build_hero()
        self.body_layout.insertWidget(0, hero)
        # Insert model-status area right after hero (index 1)
        grid_container = QWidget()
        grid_layout = QVBoxLayout(grid_container)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.setSpacing(0)
        self._build_model_status_area(grid_layout)
        self.body_layout.insertWidget(1, grid_container)

    def _build_settings_sections(self, content_layout: QVBoxLayout, *, deferred: bool) -> None:
        s = self._settings

        self._add_section(
            content_layout,
            title="模型管理 — 添加、编辑和删除模型",
            expanded=True,
            persist_key="settings/models_expanded",
            deferred=deferred,
            builder=self._build_model_management_body,
            save_fields_required=False,
        )

        self._add_section(
            content_layout,
            title="流程路由 — 为每个步骤选择模型与能力",
            expanded=True,
            persist_key="settings/routing_expanded",
            deferred=deferred,
            builder=self._build_routing_body,
            save_fields_required=False,
        )

        content_layout.addWidget(SectionHeading("界面外观", "控制桌面端主题、侧栏品牌与全局配色。"))
        self._add_built_section(
            content_layout,
            deferred,
            "界面主题 — 配色与风格",
            lambda: _build_theme_params(s),
        )

        content_layout.addWidget(
            SectionHeading("创作参数", "控制短篇、长篇初始化、章节写作、上下文投喂与状态归档。")
        )

        self._add_built_section(
            content_layout,
            deferred,
            "创作火候 — 浮动与适用范围",
            lambda: _build_temperature_jitter_params(s),
        )
        self._add_built_section(
            content_layout, deferred, "短篇生成参数", lambda: _build_short_params(s)
        )
        self._add_built_section(
            content_layout,
            deferred,
            "长篇生成 — 初始化与蓝图",
            lambda: _build_long_initialization_params(s),
        )
        self._add_built_section(
            content_layout,
            deferred,
            "长篇生成 — 章节写作与节拍",
            lambda: _build_long_creation_params(s, self._routing_profile_choices()),
        )
        self._add_built_section(
            content_layout,
            deferred,
            "长篇状态 — 上下文、投喂与归档",
            lambda: _build_long_context_params(s),
        )

        content_layout.addWidget(
            SectionHeading(
                "质量与记忆",
                "控制章节审计、归档门控、自动修复、追读力与记忆增强。",
            )
        )

        def _quality_builder() -> tuple[CollapsibleSection, dict[str, Any]]:
            sec, ws = _build_quality_audit_params(s)
            self._connect_element_progress_hint_widgets(ws)
            return sec, ws

        self._add_built_section(content_layout, deferred, "质量、门控与自动修复", _quality_builder)
        self._add_built_section(
            content_layout,
            deferred,
            "追读力 — 评估修复与下章提示",
            lambda: _build_reading_power_params(s),
        )
        self._add_built_section(
            content_layout,
            deferred,
            "记忆模块 — 语义检索与质量增强",
            lambda: _build_memory_params(s, self._embedding_profile_choices()),
        )

        content_layout.addWidget(
            SectionHeading(
                "配音",
                "控制 TTS 平台路由、推进模式、并发速率、音色管理与预算。",
            )
        )

        self._add_built_section(
            content_layout,
            deferred,
            "配音 — TTS 路由与参数",
            lambda: _build_tts_params(s),
        )

        content_layout.addWidget(
            SectionHeading(
                "运行环境",
                "管理桌面提醒、本地模型、日志、格式修复、存储与调试开关。",
            )
        )

        self._add_built_section(
            content_layout,
            deferred,
            "桌面提醒 — 任务提示音",
            lambda: _build_desktop_notification_params(s),
        )
        self._add_built_section(
            content_layout,
            deferred,
            "资料检索 — 路由与检索参数",
            lambda: _build_research_params(s),
        )

        def _ollama_builder() -> tuple[CollapsibleSection, dict[str, Any]]:
            sec, ws = _build_ollama_params(s, self._build_ollama_model_panel)
            self._param_widgets.update(ws)
            self._init_ollama_runtime_panel()
            if self._status_tests_started and not self._ollama_refresh_started:
                self._ollama_refresh_started = True
                QTimer.singleShot(0, self._refresh_ollama_models)
            return sec, {}

        self._add_built_section(
            content_layout, deferred, "Ollama 本地模型 — 免费、隐私保护", _ollama_builder
        )
        self._add_built_section(
            content_layout,
            deferred,
            "存储、日志与格式修复",
            lambda: _build_storage_params(s, self._routing_profile_choices()),
        )
        self._add_widget_builder(
            content_layout,
            deferred,
            title="拟人化库 — 仪表板与维护",
            builder=self._build_humanize_library_section,
        )
        self._add_widget_builder(
            content_layout,
            deferred,
            title="任务错误档案 — 本地保留与清空",
            builder=self._build_error_archive_section,
        )
        self._add_built_section(
            content_layout,
            deferred,
            "调试",
            lambda: _build_debug_section(self._mock_enabled, self.mock_mode_toggled),
        )
        if not deferred:
            self._refresh_element_progress_hint()

    def _add_section(
        self,
        content_layout: QVBoxLayout,
        *,
        title: str,
        expanded: bool,
        persist_key: str | None,
        deferred: bool,
        builder: Callable[[QVBoxLayout], None],
        save_fields_required: bool = True,
    ) -> CollapsibleSection:
        if not deferred:
            section = CollapsibleSection(title, expanded=expanded, persist_key=persist_key)
            builder(section.body_layout)
        else:

            def _build_lazy_body(target_layout: QVBoxLayout) -> None:
                self._materialize_without_dirtying(lambda: builder(target_layout))

            section = CollapsibleSection(
                title,
                expanded=expanded,
                persist_key=persist_key,
                lazy_body_builder=_build_lazy_body,
                defer_initial_body_build=True,
            )
            self._deferred_sections.append(section)
        section.setProperty("saveFieldsRequired", save_fields_required)
        content_layout.addWidget(section)
        return section

    def _add_built_section(
        self,
        content_layout: QVBoxLayout,
        deferred: bool,
        title: str,
        builder: Callable[[], tuple[CollapsibleSection, dict[str, Any]]],
    ) -> None:
        if not deferred:
            section, widgets = builder()
            self._param_widgets.update(widgets)
            self._connect_theme_preview_widgets(widgets)
            self._connect_research_widgets(widgets)
            content_layout.addWidget(section)
            return

        def _build_body(target_layout: QVBoxLayout) -> None:
            section, widgets = builder()
            self._param_widgets.update(widgets)
            self._connect_theme_preview_widgets(widgets)
            self._connect_research_widgets(widgets)
            self._move_body_items(section.body_layout, target_layout)
            section.deleteLater()

        self._add_section(
            content_layout,
            title=title,
            expanded=False,
            persist_key=None,
            deferred=True,
            builder=_build_body,
            save_fields_required=True,
        )

    def _connect_theme_preview_widgets(self, widgets: dict[str, Any]) -> None:
        combo = widgets.get("_desktop_theme")
        if combo is not None:
            try:
                if not combo.property("themePreviewConnected"):
                    combo.currentIndexChanged.connect(self._preview_desktop_theme)
                    combo.setProperty("themePreviewConnected", True)
            except AttributeError:
                pass

        for key in (
            "_desktop_ui_font_family",
            "_desktop_reading_font_family",
            "_desktop_font_scale",
        ):
            typography_combo = widgets.get(key)
            if typography_combo is None:
                continue
            try:
                if typography_combo.property("typographyPreviewConnected"):
                    continue
                typography_combo.currentIndexChanged.connect(self._preview_desktop_typography)
                typography_combo.setProperty("typographyPreviewConnected", True)
            except AttributeError:
                continue

    def _connect_research_widgets(self, widgets: dict[str, Any]) -> None:
        apply_btn = widgets.get("_research_apply_preset_btn")
        test_btn = widgets.get("_research_test_btn")
        if apply_btn is not None and not bool(apply_btn.property("researchConnected")):
            apply_btn.clicked.connect(self._apply_research_preset)
            apply_btn.setProperty("researchConnected", True)
        if test_btn is not None and not bool(test_btn.property("researchConnected")):
            test_btn.clicked.connect(self._test_research_connection)
            test_btn.setProperty("researchConnected", True)

    def _apply_research_preset(self) -> None:
        preset_id = self._combo_value("_research_preset_combo")
        payload = research_preset_payload(preset_id)
        status = self._param_widgets.get("_research_test_status")
        if not payload:
            if status is not None:
                status.setText("测试状态：请先选择一个预设。")
            return

        self._set_combo_data("_research_default_provider", payload.get("provider", "auto"))
        self._set_line_text("_research_http_endpoint", payload.get("http_endpoint", ""))
        self._set_line_text("_research_mcp_command", payload.get("mcp_command", ""))
        self._set_line_text("_research_mcp_args", payload.get("mcp_args", ""))
        self._set_line_text("_research_mcp_args_json", payload.get("mcp_args_json", ""))
        self._set_line_text("_research_mcp_env", payload.get("mcp_env", ""))
        self._set_line_text("_research_mcp_env_json", payload.get("mcp_env_json", ""))
        self._set_line_text("_research_mcp_api_key_env", payload.get("api_key_env", ""))
        self._set_line_text(
            "_research_mcp_protocol_version",
            payload.get("mcp_protocol_version", "2024-11-05"),
        )
        self._set_combo_data(
            "_research_mcp_stdio_framing",
            payload.get("mcp_stdio_framing", "newline"),
        )
        self._set_line_text("_research_mcp_tool_name", payload.get("mcp_tool_name", ""))
        self._set_line_text(
            "_research_mcp_query_argument",
            payload.get("mcp_query_argument", "query"),
        )
        self._set_line_text(
            "_research_mcp_tool_arguments_json",
            payload.get("mcp_tool_arguments_json", ""),
        )
        if payload.get("provider") == "mcp_search":
            mcp_section = self._param_widgets.get("_research_mcp_section")
            with contextlib.suppress(AttributeError):
                mcp_section.set_expanded(True)

        if status is not None:
            api_key_env = payload.get("api_key_env", "").strip()
            if payload.get("provider") == "mcp_search" and api_key_env:
                status.setText(
                    f"测试状态：已应用预设（MCP）；请在上方 API Key 填写密钥"
                    f"（注入为 {api_key_env}），再测试联网。"
                )
            else:
                status.setText("测试状态：已应用预设，尚未保存。可先点击“测试联网”。")

    def _test_research_connection(self) -> None:
        self._prune_research_test_workers()
        test_btn = self._param_widgets.get("_research_test_btn")
        status = self._param_widgets.get("_research_test_status")
        if test_btn is not None:
            test_btn.setEnabled(False)
        if status is not None:
            status.setText("测试状态：正在测试当前联网配置...")

        worker = self._ResearchConnectionTestWorker(
            self._current_research_settings_values(),
            self._combo_value("_research_default_provider") or "auto",
        )

        def _on_finished(success: bool, detail: str, worker_ref: Any = worker) -> None:
            if status is not None:
                prefix = "测试状态：连通" if success else "测试状态：失败"
                status.setText(f"{prefix} - {detail}")
            if test_btn is not None:
                test_btn.setEnabled(True)
            with contextlib.suppress(ValueError):
                self._research_test_workers.remove(worker_ref)

        worker.signals.finished.connect(_on_finished)
        self._research_test_workers.append(worker)
        worker.start()

    def _current_research_settings_values(self) -> dict[str, object]:
        return {
            "research_default_provider": self._combo_value("_research_default_provider") or "auto",
            "research_http_endpoint": self._line_value("_research_http_endpoint"),
            "research_api_key": self._line_value("_research_api_key"),
            "research_timeout_s": self._float_value("_research_timeout_s", 10.0),
            "research_results_per_query": self._spin_value("_research_results_per_query", 5),
            "research_max_results": self._spin_value("_research_max_results", 5),
            "research_retry_attempts": self._spin_value("_research_retry_attempts", 1),
            "research_include_domains": self._line_value("_research_include_domains"),
            "research_exclude_domains": self._line_value("_research_exclude_domains"),
            "research_locale": self._line_value("_research_locale"),
            "research_search_depth": self._combo_value("_research_search_depth") or "basic",
            "research_mcp_command": self._line_value("_research_mcp_command"),
            "research_mcp_args": self._line_value("_research_mcp_args"),
            "research_mcp_args_json": self._line_value("_research_mcp_args_json"),
            "research_mcp_env": self._line_value("_research_mcp_env"),
            "research_mcp_env_json": self._line_value("_research_mcp_env_json"),
            "research_mcp_api_key_env": self._line_value("_research_mcp_api_key_env"),
            "research_mcp_protocol_version": self._line_value(
                "_research_mcp_protocol_version",
                "2024-11-05",
            ),
            "research_mcp_stdio_framing": self._combo_value("_research_mcp_stdio_framing")
            or "newline",
            "research_mcp_inherit_environment": self._bool_value(
                "_research_mcp_inherit_environment"
            ),
            "research_mcp_tool_name": self._line_value("_research_mcp_tool_name"),
            "research_mcp_query_argument": self._line_value(
                "_research_mcp_query_argument",
                "query",
            ),
            "research_mcp_tool_arguments_json": self._line_value(
                "_research_mcp_tool_arguments_json"
            ),
        }

    def _prune_research_test_workers(self) -> None:
        self._research_test_workers = [
            worker for worker in self._research_test_workers if worker.isRunning()
        ]

    def _set_combo_data(self, widget_key: str, value: str) -> None:
        widget = self._param_widgets.get(widget_key)
        if widget is None:
            return
        try:
            idx = widget.findData(value)
            if idx < 0:
                idx = widget.findText(value)
            if idx >= 0:
                widget.setCurrentIndex(idx)
        except AttributeError:
            return

    def _set_line_text(self, widget_key: str, value: str) -> None:
        widget = self._param_widgets.get(widget_key)
        if widget is None:
            return
        try:
            widget.setText(str(value or ""))
        except AttributeError:
            return

    def _combo_value(self, widget_key: str) -> str:
        widget = self._param_widgets.get(widget_key)
        if widget is None:
            return ""
        try:
            data = widget.currentData()
            return str(data if data is not None else widget.currentText())
        except AttributeError:
            return ""

    def _line_value(self, widget_key: str, default: str = "") -> str:
        widget = self._param_widgets.get(widget_key)
        if widget is None:
            return default
        try:
            return str(widget.text())
        except AttributeError:
            return default

    def _spin_value(self, widget_key: str, default: int) -> int:
        widget = self._param_widgets.get(widget_key)
        if widget is None:
            return default
        try:
            return int(widget.value())
        except AttributeError:
            return default

    def _float_value(self, widget_key: str, default: float) -> float:
        widget = self._param_widgets.get(widget_key)
        if widget is None:
            return default
        try:
            return float(widget.value())
        except AttributeError:
            return default

    def _bool_value(self, widget_key: str) -> bool:
        widget = self._param_widgets.get(widget_key)
        if widget is None:
            return False
        try:
            return bool(widget.isChecked())
        except AttributeError:
            return False

    def _preview_desktop_theme(self, *_args: object) -> None:
        combo = self._param_widgets.get("_desktop_theme")
        if combo is None:
            return
        try:
            theme_id = combo.currentData()
            if theme_id is None:
                theme_id = combo.currentText()
        except AttributeError:
            return
        self._pending_theme_preview_id = str(theme_id)
        self._ensure_theme_preview_timer().start(self._THEME_PREVIEW_DELAY_MS)

    def _apply_pending_desktop_theme(self) -> None:
        theme_id = self._pending_theme_preview_id
        self._pending_theme_preview_id = None
        if not theme_id or getattr(self, "_shutdown_done", False):
            return
        with ui_perf_span("settings_theme_preview", theme_id=theme_id):
            apply_desktop_theme(theme_id, root=root_for_widget(self))

    def _preview_desktop_typography(self, *_args: object) -> None:
        """Preview every typography preference from one shared token entry point."""
        app = QApplication.instance()
        if not isinstance(app, QApplication) or getattr(self, "_shutdown_done", False):
            return
        theme_id = self._combo_value("_desktop_theme")
        root = root_for_widget(self)
        # Restore the selected theme's original QSS before changing the reading
        # family.  This makes the preview reversible without a restart.
        apply_desktop_theme(theme_id, app=app, root=root, force=True)
        apply_desktop_typography(
            app,
            ui_profile=self._combo_value("_desktop_ui_font_family") or "source_sans",
            reading_profile=(
                self._combo_value("_desktop_reading_font_family") or "source_serif"
            ),
            scale=self._combo_value("_desktop_font_scale") or "1.0",
        )

    def _add_widget_builder(
        self,
        content_layout: QVBoxLayout,
        deferred: bool,
        *,
        title: str,
        builder: Callable[[], QWidget],
    ) -> None:
        if not deferred:
            content_layout.addWidget(builder())
            return

        def _build_body(target_layout: QVBoxLayout) -> None:
            widget = builder()
            if isinstance(widget, CollapsibleSection):
                self._move_body_items(widget.body_layout, target_layout)
                widget.deleteLater()
            else:
                target_layout.addWidget(widget)

        self._add_section(
            content_layout,
            title=title,
            expanded=False,
            persist_key=None,
            deferred=True,
            builder=_build_body,
            save_fields_required=False,
        )

    def _move_body_items(self, source_layout: QVBoxLayout, target_layout: QVBoxLayout) -> None:
        while source_layout.count():
            item = source_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                target_layout.addWidget(widget)
                continue
            child_layout = item.layout()
            if child_layout is not None:
                target_layout.addLayout(child_layout)
                continue
            spacer = item.spacerItem()
            if spacer is not None:
                target_layout.addSpacerItem(spacer)

    def _build_model_management_body(self, body_layout: QVBoxLayout) -> None:
        model_desc = _ElidedDescriptionLabel(
            "在此添加你需要使用的 AI 模型。每个模型需要指定供应商、模型 ID 和 API Key。\n"
            "添加完成后，可在下方「流程路由」中为各个创作步骤选择对应的模型。"
        )
        model_desc.setObjectName("panelDescription")
        body_layout.addWidget(model_desc)

        default_row = QHBoxLayout()
        default_row.setSpacing(10)
        default_lbl = QLabel("默认模型")
        default_lbl.setObjectName("settingLabel")
        default_lbl.setFixedWidth(80)
        default_row.addWidget(default_lbl)
        self._default_model_combo = QComboBox()
        self._default_model_combo.setMinimumWidth(240)
        self._default_model_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self._default_model_combo.currentIndexChanged.connect(self._on_default_model_changed)
        default_row.addWidget(self._default_model_combo, 1)
        default_hint = QLabel("未分配路由的步骤均使用此模型")
        default_hint.setObjectName("settingHint")
        default_row.addWidget(default_hint)
        body_layout.addLayout(default_row)

        self._models_list_layout = QVBoxLayout()
        self._models_list_layout.setSpacing(10)
        body_layout.addLayout(self._models_list_layout)

        add_btn_row = QHBoxLayout()
        add_btn = ActionButton("+ 添加模型")
        add_btn.clicked.connect(self._add_model)
        add_btn_row.addWidget(add_btn)
        add_btn_row.addStretch()
        body_layout.addLayout(add_btn_row)
        self._refresh_models_list()

    def _build_routing_body(self, body_layout: QVBoxLayout) -> None:
        routing_desc = _ElidedDescriptionLabel(
            "先按主流程或子流程批量指定模型，再按单个步骤覆盖；思考/多轮只在支持的模型上生效。"
            "未指定时使用默认模型，灰锁模型需补 API Key 后才能调用。"
        )
        routing_desc.setObjectName("panelDescription")
        body_layout.addWidget(routing_desc)

        profiles = self._routing_profile_choices()
        route_tabs = QTabWidget()
        route_tabs.setObjectName("routingGroupTabs")
        route_tabs.setDocumentMode(True)
        configure_tab_widget_density(route_tabs, "roomy", scroll_buttons=True)
        if self._eager_build:
            for group in ROUTING_GROUPS:
                page = self._build_route_group_page(group, profiles)
                tab_index = route_tabs.addTab(page, group.name)
                route_tabs.setTabToolTip(tab_index, group.description)
        else:
            for group in ROUTING_GROUPS:
                page = QWidget()
                page.setObjectName("routingGroupPagePlaceholder")
                tab_index = route_tabs.addTab(page, group.name)
                route_tabs.setTabToolTip(tab_index, group.description)
                self._lazy_route_groups[tab_index] = (route_tabs, group, profiles)
            route_tabs.currentChanged.connect(
                lambda index, tabs=route_tabs: self._ensure_route_group_tab(tabs, index)
            )
            self._ensure_route_group_tab(route_tabs, route_tabs.currentIndex())
        body_layout.addWidget(route_tabs)

    def _connect_element_progress_hint_widgets(self, widgets: dict[str, Any]) -> None:
        for key in (
            "_element_progress_arbiter_enabled",
            "_element_progress_arbiter_max_items",
            "_element_progress_gray_low",
            "_element_progress_gray_high",
            "_element_progress_arbiter_max_tokens",
            "_element_progress_arbiter_temp",
        ):
            cw = widgets.get(key)
            if cw is not None:
                try:
                    cw.currentTextChanged.connect(lambda _v: self._refresh_element_progress_hint())
                except AttributeError:
                    try:
                        cw.valueChanged.connect(lambda _v: self._refresh_element_progress_hint())
                    except AttributeError:
                        pass

    def schedule_deferred_build(self) -> None:
        """Start building deferred settings sections after the current UI turn."""
        if (
            self._deferred_build_complete
            or self._deferred_build_timer.isActive()
            or self._status_detection_busy
            or not self.isVisible()
        ):
            return
        self._deferred_build_timer.start(0)

    def ensure_deferred_built(self) -> None:
        self.ensure_all_deferred_sections_built()

    def prewarm_visuals(self) -> None:
        """Synchronously build cold-path visuals that are cheap to pre-render."""
        self.ensure_status_grid_built()
        self.ensurePolished()
        layout = self.layout()
        if layout is not None:
            layout.activate()

    def is_ui_ready(self) -> bool:
        """Return whether the first visible settings surface has finished staging.

        The normal settings route deliberately paints a cold placeholder before
        it builds the hero and connection-card grid. Production activation
        remains asynchronous; this predicate is only an observation point for
        callers that need to distinguish that transient frame from the usable
        page (for example the deterministic UI-parity capture fixture).
        """

        return self._hero_and_grid_built and self._status_grid_build_complete

    def ensure_all_deferred_sections_built(self) -> None:
        """Synchronously build every lazy section and route tab."""
        if self._deferred_build_timer.isActive():
            self._deferred_build_timer.stop()

        def _build_all() -> None:
            with ui_perf_span("settings_deferred_build_all"):
                self._ensure_hero_and_grid_built()
                for section in self._deferred_sections:
                    section.ensure_body_built()
                self._ensure_all_route_tabs_built()
                self._refresh_element_progress_hint()
                self._deferred_build_complete = True
                self._wire_session_tab_tracking()
                self._apply_pending_ui_state()

        self._materialize_without_dirtying(_build_all)

    def ensure_save_fields_built(self) -> None:
        """Build fields needed for persistence without expanding hidden route tabs.

        Unmaterialized task rows are already represented by ``self._config``.
        Building every route group and subgroup before each save creates nearly
        a hundred hidden rows and stalls the UI, while adding no new values to
        persist. Parameter sections still need to be materialized because their
        widgets are the editable source used by ``collect_param_env_pairs``.
        """
        if self._deferred_build_timer.isActive():
            self._deferred_build_timer.stop()

        def _build_save_fields() -> None:
            with ui_perf_span("settings_save_fields_build"):
                for section in self._deferred_sections:
                    if bool(section.property("saveFieldsRequired")):
                        section.ensure_body_built()
                self._refresh_element_progress_hint()
                self._wire_session_tab_tracking()
                self._apply_pending_ui_state()

        self._materialize_without_dirtying(_build_save_fields)

    def _build_next_deferred_section(self) -> None:
        if self._deferred_build_complete:
            return
        if self._status_detection_busy or not self.isVisible():
            return
        # Build hero + model-status grid before the first deferred section
        self._ensure_hero_and_grid_built()
        while self._deferred_build_cursor < len(self._deferred_sections):
            section = self._deferred_sections[self._deferred_build_cursor]
            self._deferred_build_cursor += 1
            # Collapsed sections already know how to construct themselves on
            # first expansion.  Building every collapsed body in the idle
            # loop created thousands of hidden controls that still took part
            # in future style/layout passes whenever the page was shown.
            if section.body_built or not section.is_expanded:
                continue
            with ui_perf_span(
                "settings_deferred_section", title=getattr(section, "_title_text", "")
            ):
                section.ensure_body_built()
            self._deferred_build_timer.start(self._DEFERRED_SECTION_BUILD_INTERVAL_MS)
            return
        # Route group and subgroup tabs have their own first-activation lazy
        # builders.  Keep those placeholders intact instead of materialising
        # the complete routing matrix in the background.
        self._refresh_element_progress_hint()
        self._deferred_build_complete = True
        self._wire_session_tab_tracking()
        self._apply_pending_ui_state()

    def _materialize_without_dirtying(self, builder: Callable[[], Any]) -> Any:
        """Run a synchronous lazy build and merge only its signature delta."""
        is_outermost = self._signature_materialization_depth == 0
        if is_outermost:
            self._signature_before_materialization = _build_signature(self)
        self._signature_materialization_depth += 1
        succeeded = False
        try:
            result = builder()
            succeeded = True
            return result
        finally:
            self._signature_materialization_depth -= 1
            if is_outermost:
                before = self._signature_before_materialization
                self._signature_before_materialization = ""
                if succeeded:
                    after = _build_signature(self)
                    self._last_saved_signature = _merge_materialized_signature(
                        self._last_saved_signature,
                        before,
                        after,
                    )
                    self._last_saved_signature_pending = False

    def _ensure_route_group_tab(self, tabs: QTabWidget, index: int) -> None:
        entry = self._lazy_route_groups.get(index)
        if entry is None:
            return
        entry_tabs, group, profiles = entry
        if entry_tabs is not tabs:
            return
        self._lazy_route_groups.pop(index, None)

        def _build_group() -> None:
            page = self._build_route_group_page(group, profiles, lazy_subgroups=True)
            placeholder = tabs.widget(index)
            signals_were_blocked = tabs.blockSignals(True)
            try:
                tabs.removeTab(index)
                tabs.insertTab(index, page, group.name)
                tabs.setTabToolTip(index, group.description)
                tabs.setCurrentIndex(index)
            finally:
                tabs.blockSignals(signals_were_blocked)
            if placeholder is not None:
                placeholder.deleteLater()

        self._materialize_without_dirtying(_build_group)

    def _ensure_route_subgroup_tab(self, tabs: QTabWidget, index: int) -> None:
        entry = self._lazy_route_subgroups.get((id(tabs), index))
        if entry is None:
            return
        entry_tabs, group, subgroup, profiles = entry
        if entry_tabs is not tabs:
            return
        self._lazy_route_subgroups.pop((id(tabs), index), None)

        def _build_subgroup() -> None:
            page = self._build_route_subgroup_page(group, subgroup, profiles)
            placeholder = tabs.widget(index)
            signals_were_blocked = tabs.blockSignals(True)
            try:
                tabs.removeTab(index)
                tabs.insertTab(index, page, subgroup.title)
                tabs.setTabToolTip(index, subgroup.description)
                tabs.setCurrentIndex(index)
            finally:
                tabs.blockSignals(signals_were_blocked)
            if placeholder is not None:
                placeholder.deleteLater()

        self._materialize_without_dirtying(_build_subgroup)

    def _ensure_all_route_tabs_built(self) -> None:
        while self._lazy_route_groups:
            for index, (tabs, _group, _profiles) in sorted(self._lazy_route_groups.items()):
                self._ensure_route_group_tab(tabs, index)
                break
        while self._lazy_route_subgroups:
            for (tabs_id, index), (tabs, _group, _subgroup, _profiles) in sorted(
                self._lazy_route_subgroups.items()
            ):
                if tabs_id == id(tabs):
                    self._ensure_route_subgroup_tab(tabs, index)
                else:
                    self._lazy_route_subgroups.pop((tabs_id, index), None)
                break

    def _temperature_value_for_task(self, task_key: str) -> float | None:
        temp_task = self._ROUTE_TEMPERATURE_TASKS.get(task_key)
        if temp_task is None:
            return None
        return float(getattr(self._settings, temp_task.setting_attr))

    def _register_temperature_spin(self, task_key: str, spin: Any | None) -> None:
        if spin is None:
            return
        temp_task = self._ROUTE_TEMPERATURE_TASKS.get(task_key)
        if temp_task is not None:
            spin.setProperty("setting_attr", temp_task.setting_attr)
            if is_temperature_jitter_protected(temp_task.task_type):
                spin.setToolTip("固定温度；此步骤不使用创意火候浮动")
            else:
                spin.setToolTip("基础火候；适用范围包含此步骤时会围绕此值随机")
        if task_key in self._LONG_TEMPERATURE_TASKS:
            self._param_widgets["_long_temp_spins"][task_key] = spin
        elif task_key in self._SHORT_TEMPERATURE_TASKS:
            self._param_widgets["_short_temp_spins"][task_key] = spin

    def _build_route_group_page(
        self,
        group: RoutingGroup,
        profiles: list[tuple[str, str, bool]],
        *,
        lazy_subgroups: bool = False,
    ) -> QWidget:
        page = QWidget()
        page.setObjectName("routingGroupPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 12, 8, 10)
        layout.setSpacing(10)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(6)
        icon_label = QLabel(group.icon)
        icon_label.setObjectName("routingGroupIcon")
        header_row.addWidget(icon_label)
        group_label = QLabel(group.name)
        group_label.setObjectName("routingGroupTitle")
        header_row.addWidget(group_label)
        header_row.addStretch()
        layout.addLayout(header_row)

        desc_label = _ElidedDescriptionLabel(group.description)
        desc_label.setObjectName("routingGroupDesc")
        layout.addWidget(desc_label)

        group_task_keys = [t.key for t in group.tasks]
        bulk_row = self._make_group_bulk_row(
            group.name,
            group_task_keys,
            profiles,
            scope="group",
        )
        layout.addWidget(bulk_row)

        if group.subgroups:
            subgroup_tabs = QTabWidget()
            subgroup_tabs.setObjectName("routingSubgroupTabs")
            subgroup_tabs.setDocumentMode(True)
            configure_tab_widget_density(subgroup_tabs, "content", scroll_buttons=True)
            for subgroup in group.subgroups:
                if lazy_subgroups:
                    subsection_page = QWidget()
                    subsection_page.setObjectName("routingSubgroupPagePlaceholder")
                else:
                    subsection_page = self._build_route_subgroup_page(
                        group,
                        subgroup,
                        profiles,
                    )
                tab_index = subgroup_tabs.addTab(subsection_page, subgroup.title)
                subgroup_tabs.setTabToolTip(tab_index, subgroup.description)
                if lazy_subgroups:
                    self._lazy_route_subgroups[(id(subgroup_tabs), tab_index)] = (
                        subgroup_tabs,
                        group,
                        subgroup,
                        profiles,
                    )
            if lazy_subgroups:
                subgroup_tabs.currentChanged.connect(
                    lambda index, tabs=subgroup_tabs: self._ensure_route_subgroup_tab(
                        tabs,
                        index,
                    )
                )
                self._ensure_route_subgroup_tab(subgroup_tabs, subgroup_tabs.currentIndex())
            layout.addWidget(subgroup_tabs)
        else:
            for task in group.tasks:
                layout.addWidget(self._make_task_route_row(task, profiles))

        layout.addStretch()
        return page

    def _build_route_subgroup_page(
        self,
        group: RoutingGroup,
        subgroup: RoutingSubgroup,
        profiles: list[tuple[str, str, bool]],
    ) -> QWidget:
        page = QWidget()
        page.setObjectName("routingSubgroupPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 12, 10, 12)
        layout.setSpacing(8)

        desc_label = _ElidedDescriptionLabel(subgroup.description)
        desc_label.setObjectName("routingSubgroupDesc")
        layout.addWidget(desc_label)

        subgroup_key = self._route_subsection_bulk_key(group.name, subgroup.title)
        subgroup_task_keys = self._route_subsection_task_keys(
            group.tasks,
            group.name,
            subgroup.start_key,
        )
        bulk_row = self._make_group_bulk_row(
            subgroup_key,
            subgroup_task_keys,
            profiles,
            scope="subgroup",
        )
        layout.addWidget(bulk_row)

        tasks_by_key = {task.key: task for task in group.tasks}
        for task_key in subgroup_task_keys:
            task = tasks_by_key.get(task_key)
            if task is not None:
                layout.addWidget(self._make_task_route_row(task, profiles))

        layout.addStretch()
        return page

    def _make_group_bulk_row(
        self,
        group_key: str,
        task_keys: list[str],
        profiles: list[tuple[str, str, bool]],
        *,
        scope: Literal["group", "subgroup"],
    ) -> _GroupBulkRowType:
        show_multi_turn = any(tk in MULTI_TURN_TASK_KEYS for tk in task_keys)
        route, fallbacks = _bulk_route_entries_from_config(
            self._config.group_bulk_routes.get(group_key)
        )
        bulk_row = self._GroupBulkRow(
            group_key=group_key,
            profiles=profiles,
            route=route,
            fallback_routes=fallbacks,
            scope=scope,
            show_multi_turn=show_multi_turn,
            profile_configs=list(self._config.profiles),
            detected_capabilities=self._detected_capabilities,
        )
        self._bulk_route_task_keys[group_key] = task_keys
        self._group_bulk_rows[group_key] = bulk_row
        bulk_row.applied.connect(lambda gk=group_key: self._on_group_bulk_apply(gk))
        return bulk_row

    def _make_task_route_row(
        self,
        task: RoutingTask,
        profiles: list[tuple[str, str, bool]],
    ) -> _TaskRouteRowType:
        task_key = task.key
        route = self._config.routes.get(task_key)
        fallback_routes = self._config.fallback_routes.get(task_key, [])
        row = self._TaskRouteRow(
            task_key,
            task.label,
            task.hint,
            profiles,
            route,
            fallback_routes,
            show_multi_turn=(task_key in MULTI_TURN_TASK_KEYS),
            temperature=self._temperature_value_for_task(task_key),
            profile_configs=list(self._config.profiles),
            detected_capabilities=self._detected_capabilities,
        )
        self._route_rows[task_key] = row
        self._register_temperature_spin(task_key, row.temperature_spin)
        return row

    def _route_subsection_bulk_key(self, group_name: str, title: str) -> str:
        return f"{group_name} / {title}"

    def _route_subsection_task_keys(
        self,
        tasks: tuple[RoutingTask, ...],
        group_name: str,
        start_key: str,
    ) -> list[str]:
        start_keys = set(self._ROUTE_SUBSECTIONS_BY_GROUP.get(group_name, {}))
        keys = [task.key for task in tasks]
        try:
            start_index = keys.index(start_key)
        except ValueError:
            return []
        end_index = len(keys)
        for idx in range(start_index + 1, len(keys)):
            if keys[idx] in start_keys:
                end_index = idx
                break
        return keys[start_index:end_index]

    def _build_hero(self) -> QWidget:
        from novel_forge.desktop.widgets import Surface

        hero = Surface("hero")
        self._hero_panel = hero
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(26, 24, 26, 24)
        hero_layout.setSpacing(24)

        hero_text = QVBoxLayout()
        hero_text.setSpacing(10)

        eyebrow = QLabel("火候与设置")
        eyebrow.setObjectName("eyebrowLabel")
        hero_text.addWidget(eyebrow)

        self._hero_title = QLabel("炼鼎通路，调鹽火候，令山河流载不穷。")
        self._hero_title.setObjectName("heroTitle")
        self._hero_title.setWordWrap(True)
        hero_text.addWidget(self._hero_title)

        self._hero_body = QLabel(
            "模型档案与任务路由落入 model_profiles.json，运行参数写回 .env。\n"
            "新提交的任务读取最新存档；正在运行的任务不会热切换配置。"
        )
        self._hero_body.setObjectName("heroBody")
        self._hero_body.setWordWrap(True)
        hero_text.addWidget(self._hero_body)

        actions = QHBoxLayout()
        actions.setSpacing(12)
        from novel_forge.desktop.widgets import ActionButton

        self._save_btn = ActionButton("保存设置", variant="primary")
        self._save_btn.clicked.connect(self.save_with_feedback)
        actions.addWidget(self._save_btn)
        test_all_btn = ActionButton("检测全部通路", variant="secondary")
        test_all_btn.clicked.connect(self._test_all_connections)
        actions.addWidget(test_all_btn)
        actions.addStretch()
        hero_text.addLayout(actions)
        hero_text.addStretch()
        hero_layout.addLayout(hero_text, 1)

        runtime_panel = QWidget()
        runtime_panel.setMinimumWidth(420)
        runtime_panel.setMaximumWidth(560)
        runtime_layout_outer = QVBoxLayout(runtime_panel)
        runtime_layout_outer.setContentsMargins(0, 0, 0, 0)
        runtime_layout_outer.setSpacing(8)

        runtime_title = QLabel("运行总览")
        runtime_title.setObjectName("cardTitle")
        runtime_title.setProperty("compact", True)
        runtime_layout_outer.addWidget(runtime_title)

        runtime_hint = QLabel("当前工作区环境、已载入 Provider 与模型连接状态。")
        runtime_hint.setObjectName("cardHint")
        runtime_hint.setProperty("compact", True)
        runtime_hint.setWordWrap(True)
        runtime_layout_outer.addWidget(runtime_hint)

        cards = QGridLayout()
        cards.setHorizontalSpacing(10)
        cards.setVerticalSpacing(10)
        self._runtime_cards = {
            "storage": self._RuntimeCard("工作区路径", compact=True),
            "provider": self._RuntimeCard("默认模型", compact=True),
            "loaded": self._RuntimeCard("已载入 Provider", compact=True),
            "mode": self._RuntimeCard("运行模式", compact=True),
        }
        for idx, key in enumerate(("storage", "provider", "loaded", "mode")):
            self._runtime_cards[key].setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Preferred,
            )
            cards.addWidget(self._runtime_cards[key], idx // 2, idx % 2)
        runtime_layout_outer.addLayout(cards)
        hero_layout.addWidget(runtime_panel, 2)

        return hero

    def _storage_root_path(self) -> Path:
        return Path(getattr(self._settings, "storage_root", "data")).expanduser()

    def _build_humanize_library_section(self) -> QWidget:
        """A compact launcher card for the humanize library dashboard.

        The actual dashboard lives in ``HumanizeLibraryDashboardPage`` and is
        opened in a child ``QDialog`` window so it can be closed without
        losing the fire page's scroll position.  This card mirrors the same
        stats as the dashboard footer (total / enabled / user / imported /
        stale vectors / last update) and surfaces the library health via a
        colored badge.
        """
        sec = CollapsibleSection(
            "拟人化库 — 仪表板与维护",
            expanded=False,
            persist_key="settings/humanize_library_expanded",
        )
        self._humanize_library_card = _HumanizeLibraryCard()
        self._humanize_library_card.setObjectName("humanizeLibraryCard")
        self._humanize_library_card.open_dashboard_requested.connect(
            self._open_humanize_library_dashboard
        )
        sec.body_layout.addWidget(self._humanize_library_card)
        self._humanize_library_card.refresh()
        return sec

    def _open_humanize_library_dashboard(self) -> None:
        """Open the humanize library dashboard in a child window.

        We keep the dashboard out of the fire page's body so the scroll
        position, routing table, and storage settings remain in view while
        the user is editing entries.
        """
        if not hasattr(self, "_humanize_library_card"):
            self.ensure_all_deferred_sections_built()
        card = getattr(self, "_humanize_library_card", None)

        from PySide6.QtWidgets import QDialog, QVBoxLayout

        dialog = QDialog(self)
        dialog.setObjectName("humanizeLibraryDashboardDialog")
        dialog.setWindowTitle("拟人化库 — 仪表板")
        dialog.resize(*smart_dialog_size(dialog, 1100, 720))
        dialog.setModal(False)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        dashboard = HumanizeLibraryDashboardPage(dialog)
        # Keep the fire page card in sync with whatever the user does inside
        # the dashboard; refresh on every stats emit.
        if card is not None:
            dashboard.stats_refreshed.connect(card.update_from_stats_dict)
        layout.addWidget(dashboard)
        # Refresh the card on close in case the user mutates the library
        # without ever emitting a stats signal (e.g. before the first event).
        if card is not None:
            dialog.finished.connect(lambda _result: card.refresh())
        dialog.show()

    def open_humanize_library_dialog(self) -> None:
        """Public launcher for the fire page's local dashboard entry."""
        self._open_humanize_library_dashboard()

    def _build_error_archive_section(self) -> QWidget:
        sec = CollapsibleSection(
            "任务错误档案 — 本地保留与清空",
            expanded=False,
            persist_key="settings/error_archive_expanded",
        )

        self._error_archive_summary_label = QLabel()
        self._error_archive_summary_label.setObjectName("panelDescription")
        self._error_archive_summary_label.setWordWrap(True)
        sec.body_layout.addWidget(self._error_archive_summary_label)

        row = QHBoxLayout()
        row.setSpacing(10)
        refresh_btn = ActionButton("刷新统计", variant="secondary")
        refresh_btn.clicked.connect(self._refresh_error_archive_summary)
        row.addWidget(refresh_btn)

        clear_btn = ActionButton("清空错误档案", variant="danger")
        clear_btn.clicked.connect(self._clear_task_flow_error_archive)
        row.addWidget(clear_btn)
        row.addStretch()
        sec.body_layout.addLayout(row)

        self._refresh_error_archive_summary()
        return sec

    def _refresh_error_archive_summary(self) -> None:
        if self._error_archive_summary_label is None:
            return
        summary = task_flow_error_archive_summary(self._storage_root_path())
        count = int(summary.get("entry_count") or 0)
        project_count = int(summary.get("project_count") or 0)
        latest = str(summary.get("latest_time") or "").strip()
        if count <= 0:
            text = "暂无本地任务错误档案。"
        else:
            latest_text = f"最近：{latest}" if latest else "最近：未记录"
            text = f"已保留 {count} 条错误档案，涉及 {project_count} 个项目。{latest_text}。"
        self._error_archive_summary_label.setText(text)

    def _clear_task_flow_error_archive(self) -> None:
        summary = task_flow_error_archive_summary(self._storage_root_path())
        count = int(summary.get("entry_count") or 0)
        if count <= 0:
            show_info_message(self, "无需清空", "当前没有本地任务错误档案。")
            self._refresh_error_archive_summary()
            return
        if not ask_confirmation(
            self,
            "确认清空错误档案",
            f"将清空本地保留的 {count} 条任务错误档案；任务流 UI 历史不受影响。",
            confirm_text="清空错误档案",
            confirm_variant="danger",
        ):
            return
        removed = clear_task_flow_error_archive(self._storage_root_path())
        if removed <= 0:
            show_warning_message(self, "清空失败", "未能删除本地错误档案文件。")
        else:
            show_info_message(self, "已清空", f"已删除 {removed} 个错误档案文件。")
        self._refresh_error_archive_summary()

    def _refresh_element_progress_hint(self) -> None:
        w = self._param_widgets
        enabled = w.get("_element_progress_arbiter_enabled")
        enabled_text = enabled.currentText() if enabled else "false"
        max_items_w = w.get("_element_progress_arbiter_max_items")
        max_items = int(max_items_w.value()) if max_items_w else 0
        low_w = w.get("_element_progress_gray_low")
        low = float(low_w.value()) if low_w else 0.0
        high_w = w.get("_element_progress_gray_high")
        high = float(high_w.value()) if high_w else 0.0
        max_tokens_w = w.get("_element_progress_arbiter_max_tokens")
        max_tokens = int(max_tokens_w.value()) if max_tokens_w else 0
        temp_w = w.get("_element_progress_arbiter_temp")
        temperature = float(temp_w.value()) if temp_w else 0.0

        parts: list[str] = []
        if enabled_text != "true":
            parts.append("当前已关闭灰区仲裁：仅规则判定，额外 LLM 调用为 0。")
        elif max_items <= 0:
            parts.append("当前虽已开启，但\u201c每章仲裁上限=0\u201d，等同禁用。")
        else:
            parts.append(f"当前预计每章最多仲裁 {max_items} 个要素（仅灰区触发）。")

        if low > high:
            parts.append(
                f"灰区设置为 {low:.2f}~{high:.2f}（下界高于上界）；"
                f"运行时会自动互换为 {high:.2f}~{low:.2f}。"
            )
        elif abs(low - high) < 1e-9:
            parts.append(f"灰区设置为单点 {low:.2f}：仅该分数触发仲裁。")
        else:
            parts.append(f"当前灰区范围：{low:.2f}~{high:.2f}。")

        parts.append(f"单次输出上限 {max_tokens} token，温度 {temperature:.2f}。")
        hint = w.get("_element_progress_hint_label")
        if hint:
            hint.setText(" ".join(parts))

    def reload_config(self) -> None:
        self.ensure_all_deferred_sections_built()
        self._config = load_or_import_profiles(self._settings)
        self._sanitize_loaded_routing_config()
        self._ConnectionTestWorker.clear_all_cached_results()
        self._refresh_models_list()
        self._refresh_model_status_grid()
        self._refresh_route_combos()
        self._refresh_ollama_models()
        self._last_saved_signature = _build_signature(self)

    def _sanitize_loaded_routing_config(self) -> None:
        for key in list(self._config.routes):
            if key not in self._VALID_ROUTE_KEYS:
                del self._config.routes[key]
        for key in list(self._config.fallback_routes):
            if key not in self._VALID_ROUTE_KEYS:
                del self._config.fallback_routes[key]
        for key in list(self._config.group_bulk_routes):
            if key not in self._VALID_ROUTE_GROUPS:
                del self._config.group_bulk_routes[key]

    def bind_workspace(self, snapshot: DesktopWorkspaceSnapshot) -> None:
        self._snapshot = snapshot
        if not self._runtime_cards:
            return  # hero not yet built (deferred)
        self._runtime_cards["storage"].bind(
            str(snapshot.storage_root),
            f"当前检测到 {snapshot.metrics.total_projects} 个项目",
        )
        default_profile = self._config.get_profile(self._config.default_profile_id)
        default_label = (
            default_profile.display_name if default_profile else snapshot.default_provider
        )
        self._runtime_cards["provider"].bind(
            default_label,
            f"共 {len(self._config.profiles)} 个已配置模型 · 可在模型管理中修改",
        )
        self._runtime_cards["loaded"].bind(
            str(snapshot.metrics.configured_providers),
            "、".join(snapshot.overview.providers)
            if snapshot.overview.providers
            else "当前仅 Mock",
        )
        self._runtime_cards["mode"].bind(
            "Mock 模式" if self._mock_enabled else "真实模型模式",
            "可在下方「调试」分区开关。",
        )

    def bind_workspace_sections(
        self, snapshot: DesktopWorkspaceSnapshot, sections: frozenset[str]
    ) -> None:
        """Incremental bind — only update cards for changed *sections*.

        Section mapping (matches ``_PAGE_SECTION_MAP["settings"]``):

        - ``"providers"``: provider card + loaded card
        - ``"overview"``: storage card + mode card
        """
        self._snapshot = snapshot
        if not self._runtime_cards:
            return  # hero not yet built (deferred)

        if "providers" in sections:
            default_profile = self._config.get_profile(self._config.default_profile_id)
            default_label = (
                default_profile.display_name if default_profile else snapshot.default_provider
            )
            self._runtime_cards["provider"].bind(
                default_label,
                f"共 {len(self._config.profiles)} 个已配置模型 · 可在模型管理中修改",
            )
            self._runtime_cards["loaded"].bind(
                str(snapshot.metrics.configured_providers),
                "、".join(snapshot.overview.providers)
                if snapshot.overview.providers
                else "当前仅 Mock",
            )

        if "overview" in sections:
            self._runtime_cards["storage"].bind(
                str(snapshot.storage_root),
                f"当前检测到 {snapshot.metrics.total_projects} 个项目",
            )
            self._runtime_cards["mode"].bind(
                "Mock 模式" if self._mock_enabled else "真实模型模式",
                "可在下方「调试」分区开关。",
            )

    def set_mock_mode(self, enabled: bool) -> None:
        self._mock_enabled = enabled
        cb = self._param_widgets.get("_mock_cb")
        if cb:
            cb.blockSignals(True)
            cb.setChecked(enabled)
            cb.blockSignals(False)
        if self._snapshot is not None:
            self.bind_workspace(self._snapshot)

    def export_ui_state(self) -> dict[str, Any]:
        """Persist scroll and nested route-tab choices; section expansion uses QSettings."""
        tab_states: dict[str, str] = {}
        for index, tabs in enumerate(self.findChildren(QTabWidget)):
            if tabs.currentIndex() < 0:
                continue
            key = tabs.objectName() or f"tab_widget_{index}"
            tab_states[f"{key}:{index}"] = tabs.tabText(tabs.currentIndex())
        return {
            "version": 1,
            "scroll_value": self.verticalScrollBar().value(),
            "tab_states": tab_states,
        }

    def restore_ui_state(self, payload: object) -> None:
        """Restore scroll position after deferred sections have had a chance to lay out."""
        if not isinstance(payload, dict):
            return
        try:
            self._pending_session_scroll_value = max(0, int(payload.get("scroll_value") or 0))
        except (TypeError, ValueError):
            return
        raw_tab_states = payload.get("tab_states")
        self._pending_session_tab_states = (
            {str(key): str(value) for key, value in raw_tab_states.items()}
            if isinstance(raw_tab_states, dict)
            else {}
        )

        QTimer.singleShot(0, self._apply_pending_ui_state)

    def _apply_pending_ui_state(self) -> None:
        """Apply what is available now, retaining choices until lazy UI is complete."""
        tab_states = self._pending_session_tab_states
        if tab_states:
            for index, tabs in enumerate(self.findChildren(QTabWidget)):
                key = tabs.objectName() or f"tab_widget_{index}"
                selected_label = tab_states.get(f"{key}:{index}")
                if not selected_label:
                    continue
                for tab_index in range(tabs.count()):
                    if tabs.tabText(tab_index) == selected_label:
                        tabs.setCurrentIndex(tab_index)
                        break
        if self._pending_session_scroll_value is not None:
            self.verticalScrollBar().setValue(self._pending_session_scroll_value)
        if self._deferred_build_complete:
            self._pending_session_tab_states.clear()
            self._pending_session_scroll_value = None

    def _wire_session_tab_tracking(self) -> None:
        """Schedule a shell-session update when a nested settings tab changes."""
        for tabs in self.findChildren(QTabWidget):
            identity = id(tabs)
            if identity in self._session_tab_tracking_ids:
                continue
            self._session_tab_tracking_ids.add(identity)
            tabs.currentChanged.connect(lambda _idx: self.ui_state_changed.emit())

    def has_unsaved_changes(self) -> bool:
        if self._last_saved_signature_pending and not any(
            section.body_built for section in self._deferred_sections
        ):
            return False
        self.ensure_save_fields_built()
        return _build_signature(self) != self._last_saved_signature

    def unsaved_changes_description(self) -> str:
        return "- 火候页有未保存设置"

    def save_pending_changes(self) -> bool:
        self._cancel_save_preparation()
        self.ensure_save_fields_built()
        return save_settings(self, silent=True)

    def save_with_feedback(self) -> bool:
        if self._save_in_progress:
            return False
        self._pending_save_sections = [
            section
            for section in self._deferred_sections
            if bool(section.property("saveFieldsRequired")) and not section.body_built
        ]
        if not self._pending_save_sections:
            return self._commit_save_with_feedback()
        if self._deferred_build_timer.isActive():
            self._deferred_build_timer.stop()
        self._save_in_progress = True
        self._set_save_button_busy(True, "正在准备…")
        self._ensure_save_prepare_timer().start(0)
        return True

    def _prepare_next_save_section(self) -> None:
        if getattr(self, "_shutdown_done", False):
            self._cancel_save_preparation()
            return
        if self._pending_save_sections:
            section = self._pending_save_sections.pop(0)
            with ui_perf_span(
                "settings_save_prepare_section",
                title=getattr(section, "_title_text", ""),
            ):
                self._materialize_without_dirtying(section.ensure_body_built)
            self._ensure_save_prepare_timer().start(self._SAVE_PREPARE_INTERVAL_MS)
            return
        self._commit_save_with_feedback()

    def _commit_save_with_feedback(self) -> bool:
        self._save_in_progress = True
        self._set_save_button_busy(True, "正在保存…")
        try:
            self.ensure_save_fields_built()
            with ui_perf_span("settings_save_commit"):
                return save_settings(self, silent=False)
        finally:
            self._pending_save_sections.clear()
            self._save_in_progress = False
            self._set_save_button_busy(False, "保存设置")

    def _cancel_save_preparation(self) -> None:
        timer = self._save_prepare_timer
        if timer is not None:
            timer.stop()
        self._pending_save_sections.clear()
        self._save_in_progress = False
        self._set_save_button_busy(False, "保存设置")

    def _set_save_button_busy(self, busy: bool, text: str) -> None:
        button = self._save_btn
        if button is None:
            return
        button.setEnabled(not busy)
        button.setText(text)

    def _collect_param_env_pairs(self) -> dict[str, str]:
        self.ensure_save_fields_built()
        return collect_param_env_pairs(self)

    def hideEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        self._deferred_build_timer.stop()
        self._cancel_pending_auto_status_tests()
        super().hideEvent(event)

    def shutdown(self) -> None:
        if getattr(self, "_shutdown_done", False):
            return
        self._shutdown_done = True
        from novel_forge.desktop.shutdown_utils import safe_disconnect, safe_stop_timer

        self._status_tests_pending = False
        self._status_test_generation += 1
        self._status_test_queue = []
        self._status_tests_active = 0
        self._ignore_active_status_test_tokens()
        self._status_test_running = {}
        self._status_detection_busy = False
        self._route_combos_refresh_pending = False
        self._status_auto_activation_active = False
        safe_stop_timer(getattr(self, "_prewarm_timer", None))
        safe_stop_timer(self._status_test_start_timer)
        safe_stop_timer(self._status_test_watchdog_timer)
        safe_stop_timer(self._refresh_combos_timer)
        safe_stop_timer(self._status_grid_build_timer)
        safe_stop_timer(self._deferred_build_timer)
        safe_stop_timer(self._save_prepare_timer)
        safe_stop_timer(self._theme_preview_timer)
        self._pending_save_sections.clear()
        self._pending_theme_preview_id = None
        self._save_in_progress = False
        self._status_grid_build_profiles.clear()
        self._status_grid_build_cursor = 0
        self._status_grid_build_complete = True
        self._status_grid_has_pending_tests = False
        self._status_grid_auto_profile_ids = None
        self._deferred_sections.clear()
        self._deferred_build_cursor = 0
        self._deferred_build_complete = True
        self._lazy_route_groups.clear()
        self._lazy_route_subgroups.clear()
        for worker in list(getattr(self, "_test_workers", [])):
            worker.request_cancel()
            safe_disconnect(worker.signals.probe_started)
            safe_disconnect(worker.signals.finished)
        self._test_workers.clear()
        for worker in list(getattr(self, "_ollama_workers", [])):
            worker.request_cancel()
            signal = getattr(worker.signals, "loaded", None)
            if signal is not None:
                safe_disconnect(signal)
        self._ollama_workers.clear()
        for worker in list(getattr(self, "_research_test_workers", [])):
            worker.request_cancel()
            safe_disconnect(worker.signals.finished)
        self._research_test_workers.clear()
