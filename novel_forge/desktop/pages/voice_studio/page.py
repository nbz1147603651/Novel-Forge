"""Voice Studio page — TTS management UI.

Provides interface for:
- Voice team management (character-voice mapping)
- Dubbing script preview/edit
- Audio playback and export
- TTS settings
"""

from __future__ import annotations

import html as html_lib
import json
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabBar,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from novel_forge.common.constants import TaskType
from novel_forge.core.config import Settings, get_settings, get_writable_env_path, reset_settings
from novel_forge.core.schemas.bible import CharacterBible
from novel_forge.desktop.components.dubbing_player import DubbingPlayerWidget
from novel_forge.desktop.components.forms import add_setting_group_description
from novel_forge.desktop.components.page_visibility import PageVisibilityMixin
from novel_forge.desktop.components.scroll_fade import SCROLL_FADE_DISABLED_PROPERTY
from novel_forge.desktop.components.sizing import smart_dialog_size
from novel_forge.desktop.components.stream_behavior import StreamFollowController
from novel_forge.desktop.config_store import DesktopSettingsStore
from novel_forge.desktop.pages.document_renderer.incremental import update_browser_html
from novel_forge.desktop.pages.settings.components import _TaskRouteRow
from novel_forge.desktop.pages.voice_studio.audio_model_center import AudioModelCenterMixin
from novel_forge.desktop.pages.voice_studio.audio_platform_settings import (
    AudioPlatformSettingsMixin,
)
from novel_forge.desktop.pages.voice_studio.automation_mode import (
    AudioAutomationModeSelector,
)
from novel_forge.desktop.pages.voice_studio.dialogs import (
    RebuildConfirmDialog,
    ScriptSegmentEditorDialog,
)
from novel_forge.desktop.pages.voice_studio.helpers import (
    HTML_BODY_FONT_FAMILY as _HTML_BODY_FONT_FAMILY,
)
from novel_forge.desktop.pages.voice_studio.helpers import (
    LINUX_AUDITION_PLAYERS as _LINUX_AUDITION_PLAYERS,
)
from novel_forge.desktop.pages.voice_studio.helpers import (
    NARRATOR_ID as _NARRATOR_ID,
)
from novel_forge.desktop.pages.voice_studio.helpers import (
    ChapterScriptStream as _ChapterScriptStream,
)
from novel_forge.desktop.pages.voice_studio.helpers import (
    effective_script_hash as _effective_script_hash,
)
from novel_forge.desktop.pages.voice_studio.helpers import (
    emotion_display as _emotion_display,
)
from novel_forge.desktop.pages.voice_studio.helpers import (
    logger as _logger,
)
from novel_forge.desktop.pages.voice_studio.playback_sync_mixin import PlaybackSyncMixin
from novel_forge.desktop.pages.voice_studio.provider_ui import (
    all_provider_ui_specs,
    provider_models,
    provider_ui_spec,
)
from novel_forge.desktop.pages.voice_studio.provider_widgets import (
    HeaderStat as _HeaderStat,
)
from novel_forge.desktop.pages.voice_studio.provider_widgets import (
    InlineMetric as _InlineMetric,
)
from novel_forge.desktop.pages.voice_studio.provider_widgets import (
    ProviderDropdown as _ProviderDropdown,
)
from novel_forge.desktop.pages.voice_studio.script_render_mixin import ScriptRenderMixin
from novel_forge.desktop.pages.voice_studio.sound_design_dialog import SoundDesignEditorDialog
from novel_forge.desktop.pages.voice_studio.sound_library_panel import SoundLibraryPanel
from novel_forge.desktop.pages.voice_studio.sound_settings import SoundGenerationSettingsMixin
from novel_forge.desktop.pages.voice_studio.voice_preview_panel import VoicePreviewDialog
from novel_forge.desktop.pages.voice_studio.workers import (
    AcceptSegmentTakeWorker,
    ApproveVoiceWorker,
    AssembleChapterAudioWorker,
    AudioBenchmarkWorker,
    AudioModelCenterWorker,
    BuildNarratorProfileWorker,
    BuildVoiceTeamWorker,
    CloneVoiceWorker,
    ConfirmVoiceTeamWorker,
    DesignVoiceWorker,
    ExportAudiobookWorker,
    ExportAudioWorker,
    FullTTSPipelineWorker,
    GenerateScriptWorker,
    GenerateSoundPaletteWorker,
    ListVoicesWorker,
    PreviewNarratorVoiceWorker,
    PreviewVoiceWorker,
    StableAudioModelWorker,
    SynthesizeSegmentWorker,
    SynthesizeWorker,
)
from novel_forge.desktop.task_observation import TaskObservationStore
from novel_forge.desktop.theme import qcolor_hex, qcolor_rgba
from novel_forge.desktop.widgets import (
    ActionButton,
    Badge,
    CollapsibleSection,
    EmptyState,
    SectionHeading,
    SettingRow,
    Surface,
    ask_confirmation,
    make_combo_setting,
    make_line_setting,
    show_multiline_input_dialog,
    show_text_input_dialog,
    show_warning_message,
)
from novel_forge.gateway.embedding_config import is_embedding_model
from novel_forge.gateway.profiles import (
    get_profiles_path,
    load_or_import_profiles,
)
from novel_forge.persistence.filesystem import atomic_write_json
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.project_staleness import (
    invalidate_all_tts_audio_derivatives,
    invalidate_chapter_tts_artifacts,
)
from novel_forge.tts.gateway.factory import TTSAdapterRegistry, resolve_tts_model
from novel_forge.tts.pipeline.build_voice_team_step import (
    build_voice_design_prompt,
)
from novel_forge.tts.runtime.cleanup import (
    ALL_CATEGORIES as _TTS_CLEANUP_CATEGORIES,
)
from novel_forge.tts.runtime.cleanup import (
    CAT_ORPHAN_CANDIDATES as _TTS_CAT_ORPHAN_CANDIDATES,
)
from novel_forge.tts.runtime.cleanup import (
    CATEGORY_LABELS as _TTS_CLEANUP_LABELS,
)
from novel_forge.tts.runtime.cleanup import (
    execute_cleanup_tts_files,
    execute_reset_project_tts_artifacts,
    preview_cleanup_tts_files,
    preview_reset_project_tts_artifacts,
)
from novel_forge.tts.runtime.performance_policy import (
    build_voice_preview_samples,
    derive_voice_performance_profile,
    hydrate_voice_team_performance_profiles,
    resolve_character_performance,
    update_voice_performance_profile,
    voice_performance_profile,
    with_manual_performance_overrides,
)
from novel_forge.tts.schemas import (
    ChapterAudioResult,
    DubbingScript,
    DubbingSegment,
    EmotionTag,
    NarratorVoiceProfile,
    SegmentTakeVersion,
    SegmentType,
    TakeReviewStatus,
    TTSProvider,
    VoiceCastEntry,
    VoiceCloneStatus,
    VoicePerformanceOverrides,
    VoiceTeamContract,
)
from novel_forge.tts.script_integrity import (
    DubbingScriptFreshness,
    assess_dubbing_script_freshness,
    compute_dubbing_script_hash,
    requires_script_source_audit,
    unresolved_speaker_indices,
)
from novel_forge.tts.services.automation import AudioAutomationMode, resolve_audio_automation_mode
from novel_forge.tts.services.delivery import (
    TTSDeliveryNotReadyError,
    delivery_blocking_reasons,
    require_delivery_ready,
)
from novel_forge.tts.services.studio_service import VoiceStudioProjectService


class _HeightAwareStackedWidget(QStackedWidget):
    """QStackedWidget that propagates heightForWidth from the current page.

    The default QStackedWidget reports a static sizeHint, which clips
    wrapping flow layouts inside DubbingPlayerWidget when the window is
    narrow.  Propagating heightForWidth lets the transport dock grow
    vertically instead of overlapping adjacent panels.
    """

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        w = self.currentWidget()
        return w.hasHeightForWidth() if w is not None else False

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        w = self.currentWidget()
        return w.heightForWidth(width) if w is not None else super().heightForWidth(width)


class VoiceStudioPage(
    PageVisibilityMixin,
    AudioModelCenterMixin,
    AudioPlatformSettingsMixin,
    SoundGenerationSettingsMixin,
    ScriptRenderMixin,
    PlaybackSyncMixin,
    QWidget,
):
    """Main Voice Studio page with tabs for different functions."""

    # Signals
    project_changed = Signal(str)
    project_selector_changed = Signal(str)
    ui_state_changed = Signal()
    task_focus_decision_selected = Signal(str, str, str, str)
    task_focus_expand_requested = Signal()
    _StableAudioModelWorker = StableAudioModelWorker
    _AudioBenchmarkWorker = AudioBenchmarkWorker
    _AudioModelCenterWorker = AudioModelCenterWorker

    def __init__(
        self,
        *,
        settings: Settings,
        parent: QWidget | None = None,
        defer_tabs: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("voiceStudioPage")
        self._settings = settings
        self._defer_tabs = defer_tabs
        self._project_id = ""
        self._restored_project_id = ""
        self._restored_tab_index: int | None = None
        # The settings tab is built lazily, so retain its last scroll offset
        # until its scroll area exists and has a real viewport.
        self._restored_settings_scroll_value: int | None = None
        self._audio_automation_mode = resolve_audio_automation_mode(
            None,
            settings=settings,
        ).value
        self._audio_automation_modes: dict[str, str] = {}
        self._restored_audio_automation_modes: dict[str, str] = {}
        self._layout: ProjectLayout | None = None
        self._studio_service: VoiceStudioProjectService | None = None
        self._storage_root: Path | None = None
        self._voice_team: VoiceTeamContract | None = None
        self._narrator_profile: NarratorVoiceProfile | None = None
        self._bible_characters: list[dict[str, Any]] = []  # Cached characters from character_bible
        self._current_worker: Any = None
        # Native OS audio process for short voice auditions (afplay/aplay/etc).
        # Auditions are plain single clips, so a fire-and-forget native player
        # avoids both the page jump to the heavy DubbingPlayerWidget and Qt's
        # FFmpeg backend, which cannot probe MiniMax AIGC-watermarked MP3s.
        self._audition_proc: subprocess.Popen[bytes] | None = None
        # Voice-catalog loading is an auxiliary read.  It must never replace the
        # active mutation worker, otherwise auto-build completion and cancellation
        # lose the only handle to BuildVoiceTeamWorker.
        self._voice_catalog_worker: ListVoicesWorker | None = None
        self._sound_generation_widgets: dict[str, Any] = {}
        self._stable_audio_workers: list[StableAudioModelWorker] = []
        self._stable_audio_download_running = False
        self._audio_benchmark_workers: list[AudioBenchmarkWorker] = []
        self._audio_model_center_workers: list[AudioModelCenterWorker] = []
        self._project_selector: QComboBox | None = None
        self._top_bar_context: QWidget | None = None
        self._provider_dropdown: _ProviderDropdown | None = None
        self._system_voices: list[dict[str, Any]] = []  # cached voice catalog
        self._provider_capabilities: dict[str, Any] = {}
        self._voice_combo_loading = False  # guard against recursive signals
        self._performance_dirty_fields: set[str] = set()
        self._restore_auto_performance = False
        self._available_chapters: list[int] = []
        self._avatars_dirty = False
        self._deferred_tab_builders: dict[int, Any] = {}
        self._deferred_tab_labels: dict[int, str] = {}
        self._pending_deferred_tab_index: int | None = None
        self._deferred_tab_timer = QTimer(self)
        self._deferred_tab_timer.setSingleShot(True)
        self._deferred_tab_timer.timeout.connect(self._build_next_deferred_tab)
        self._project_load_timer = QTimer(self)
        self._project_load_timer.setSingleShot(True)
        self._project_load_timer.timeout.connect(self._load_voice_team)
        self._chapter_combo_load_timer = QTimer(self)
        self._chapter_combo_load_timer.setSingleShot(True)
        self._chapter_combo_load_timer.timeout.connect(self._populate_chapter_combo)
        self._shutdown_done = False

        # Initialize runtime state before optional tabs are built. Workspace
        # binding and worker callbacks can therefore arrive while only the
        # lightweight voice-team tab exists.
        self._current_script: DubbingScript | None = None
        self._playback_script: DubbingScript | None = None
        self._current_audio_result: ChapterAudioResult | None = None
        self._current_timeline: Any = None
        self._current_results: list[Any] = []
        self._active_chapter_number = 0
        self._loaded_chapter_number = 0
        # A result manifest embeds a script snapshot for playback. Keep its
        # availability distinct from the separately persisted source script:
        # the latter is the only valid input for a new synthesis run.
        self._source_script_available = False
        self._script_freshness: DubbingScriptFreshness | None = None
        self._tts_operation: tuple[str, int] | None = None
        self._segment_status: dict[int, str] = {}
        self._room_draft_segments: dict[int, DubbingSegment] = {}
        self._room_candidate_takes: dict[int, SegmentTakeVersion] = {}
        self._room_loaded_audio_path = ""
        self._room_chapter_audio_path = ""
        self._room_preview_active = False
        self._avatar_buttons: dict[str, QPushButton] = {}
        self._highlighted_character: str | None = None
        self._playback_segment_idx = -1
        self._playback_active = False
        self._follow_playback = True
        self._programmatic_scroll = False
        # Origin of the current chapter playback so the voice-room segment list
        # only mirrors playback that was launched from the room (double-click),
        # not arbitrary seeks in the audio tab. Cleared when playback stops.
        self._voice_room_playback = False
        self._room_chapter_play_requested = False
        # Segment index to resume from once a fast reassemble (triggered by an
        # accepted take) completes; see ``_on_segment_take_accepted`` /
        # ``_on_audio_completed``. ``None`` means "no pending resume".
        self._resume_after_reassemble_segment: int | None = None
        self._synth_anim_timer: QTimer | None = None
        self._script_gen_timer: QTimer | None = None
        # Stall detection: fires periodically during script generation to
        # detect when the LLM stream has gone silent for too long.
        self._stream_stall_timer = QTimer(self)
        self._stream_stall_timer.setInterval(10_000)  # check every 10s
        self._stream_stall_timer.timeout.connect(self._check_stream_stall)
        self._last_stream_activity_at: float = 0.0
        self._stream_stall_warned: bool = False
        self._batch_start_time: float = 0.0
        # Throttle for the streaming progress bar: llm_stream_delta events
        # arrive at ~50/s; repainting the QProgressBar on every one saturates
        # the Qt main thread.  Limit visual updates to once per 400 ms.
        self._stream_progress_last_update_at: float = 0.0
        self._STREAM_PROGRESS_MIN_INTERVAL_S: float = 0.40
        # Throttle timers for playback-driven UI updates.  setHtml() on a
        # QTextBrowser and Badge.setText() on every positionChanged tick
        # (~50-100 ms) starves the Qt event loop and causes audio stutter
        # on the macOS QMediaPlayer backend.
        self._word_hl_throttle = QTimer(self)
        self._word_hl_throttle.setInterval(150)
        self._word_hl_throttle.setSingleShot(True)
        self._word_hl_throttle.timeout.connect(self._flush_word_highlight)
        self._progress_throttle = QTimer(self)
        self._progress_throttle.setInterval(250)
        self._progress_throttle.setSingleShot(True)
        self._progress_throttle.timeout.connect(self._flush_progress_badge)
        self._pending_word_hl: tuple[int, int, int] | None = None  # (seg, start, end)
        self._pending_progress: tuple[float, int, int] | None = None  # (frac, pos, total)
        self._room_word_hl_throttle = QTimer(self)
        self._room_word_hl_throttle.setInterval(150)
        self._room_word_hl_throttle.setSingleShot(True)
        self._room_word_hl_throttle.timeout.connect(self._flush_room_word_highlight)
        self._pending_room_word_hl: tuple[int, int, int] | None = None
        # Register throttle timers with PageVisibilityMixin so they are stopped
        # when the page is hidden (single-shot coalescers make no sense for an
        # invisible page).
        self._register_throttle_timer(self._word_hl_throttle)
        self._register_throttle_timer(self._progress_throttle)
        self._register_throttle_timer(self._room_word_hl_throttle)
        # Deferred play flags: when the audio tab (index 3) hasn't been lazily
        # built yet, _audio_player is None.  These flags cause the play action
        # to fire automatically once the tab finishes building.
        self._pending_room_play_full: bool = False
        self._pending_room_play_segment: int | None = None
        self._synth_anim_phase = 0
        # Per-chapter streaming state: keyed by chapter_number. Only chapters
        # that are generating (or just finished generating) hold an entry.
        self._script_streams: dict[int, _ChapterScriptStream] = {}
        # The chapter currently being generated; at most one at a time (single
        # task model). None when no generation is running.
        self._generating_chapter: int | None = None
        self._script_stream_follow: StreamFollowController | None = None
        self._audio_player: DubbingPlayerWidget | None = None
        self._segment_player: DubbingPlayerWidget | None = None
        self._room_chapter_player: DubbingPlayerWidget | None = None
        self._room_player_stack: QStackedWidget | None = None
        self._task_observation_store: TaskObservationStore | None = None
        self._observed_voice_task_ids: dict[str, str] = {}

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Initialize UI components."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(3, 3, 3, 3)
        main_layout.setSpacing(2)

        # The window owns the visible page header.  Build one reusable context
        # widget here and let the window mount it into that header, avoiding a
        # second decorative title card inside the page.
        self._top_bar_context = self._create_top_bar_context()

        # 自定义 Tab 栏行（与 QTabWidget 自带的 tabBar 分离）
        self._custom_tab_bar = QTabBar()
        self._custom_tab_bar.setObjectName("voiceStudioTabsBar")
        self._custom_tab_bar.setExpanding(True)
        self._custom_tab_bar.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )

        tab_bar_row = QWidget()
        tab_bar_row.setObjectName("voiceStudioTabBarRow")
        tab_bar_layout = QHBoxLayout(tab_bar_row)
        tab_bar_layout.setContentsMargins(0, 0, 0, 0)
        tab_bar_layout.setSpacing(0)
        tab_bar_layout.addWidget(self._custom_tab_bar, 1)
        self._save_settings_btn = ActionButton("保存设置", variant="primary")
        self._save_settings_btn.setVisible(False)
        self._save_settings_btn.clicked.connect(self._on_save_settings)
        tab_bar_layout.addWidget(self._save_settings_btn)

        # QTabWidget 隐藏自带 tabBar，只保留内容区
        self._tabs = QTabWidget()
        self._tabs.setObjectName("voiceStudioTabs")
        self._tabs.tabBar().hide()  # 隐藏默认 tabBar

        # 同步自定义 tabBar 与 QTabWidget，并控制「保存设置」按钮可见性
        def _on_custom_tab_changed(index: int) -> None:
            self._tabs.setCurrentIndex(index)
            # "平台设置" 始终是最后一个 tab
            is_settings_tab = index == self._custom_tab_bar.count() - 1
            self._save_settings_btn.setVisible(is_settings_tab)
            self.ui_state_changed.emit()

        self._custom_tab_bar.currentChanged.connect(_on_custom_tab_changed)

        # 添加 Tab 页（QTabWidget 内部仍然管理页面切换）
        self._tabs.addTab(self._create_voice_team_tab(), "1 配音团队")
        self._custom_tab_bar.addTab("1 配音团队")
        deferred_tabs = (
            (1, "2 配音脚本", self._create_script_tab),
            (2, "3 配音室", self._create_voice_room_tab),
            (3, "4 后处理", self._create_audio_tab),
            (4, "平台设置", self._create_settings_tab),
        )
        if self._defer_tabs:
            for index, label, builder in deferred_tabs:
                self._tabs.addTab(self._create_tab_placeholder(label), label)
                self._custom_tab_bar.addTab(label)
                self._deferred_tab_builders[index] = builder
                self._deferred_tab_labels[index] = label
            self._tabs.currentChanged.connect(self._ensure_tab_built)
        else:
            self._tabs.addTab(self._create_script_tab(), "2 配音脚本")
            self._custom_tab_bar.addTab("2 配音脚本")
            self._tabs.addTab(self._create_voice_room_tab(), "3 配音室")
            self._custom_tab_bar.addTab("3 配音室")
            self._tabs.addTab(self._create_audio_tab(), "4 后处理")
            self._custom_tab_bar.addTab("4 后处理")
            self._tabs.addTab(self._create_settings_tab(), "平台设置")
            self._custom_tab_bar.addTab("平台设置")

        # 自定义 tabBar 行 + QTabWidget 内容区，包裹在统一边框容器中
        # 与卷帙页面的项目选择栏容器风格一致，统一视觉衔接
        tabs_frame = QFrame()
        tabs_frame.setObjectName("voiceStudioTabsFrame")
        tabs_frame_layout = QVBoxLayout(tabs_frame)
        tabs_frame_layout.setContentsMargins(0, 0, 0, 0)
        tabs_frame_layout.setSpacing(0)
        tabs_frame_layout.addWidget(tab_bar_row)
        tabs_frame_layout.addWidget(self._tabs, 1)

        main_layout.addWidget(tabs_frame, 1)

        # Bottom action bar
        action_bar = QWidget()
        action_bar.setObjectName("voiceStudioStatusBar")
        action_layout = QHBoxLayout(action_bar)
        action_layout.setContentsMargins(3, 1, 3, 0)

        self._status_badge = Badge("就绪", tone="default")
        action_layout.addWidget(self._status_badge)

        self._workflow_hint_label = QLabel("")
        self._workflow_hint_label.setObjectName("voiceWorkflowHint")
        action_layout.addWidget(self._workflow_hint_label)

        self._voice_team_progress = QProgressBar()
        self._voice_team_progress.setObjectName("voiceTeamBuildProgress")
        self._voice_team_progress.setMinimumWidth(220)
        self._voice_team_progress.setMaximumWidth(320)
        self._voice_team_progress.setTextVisible(True)
        self._voice_team_progress.setVisible(False)
        action_layout.addWidget(self._voice_team_progress)
        action_layout.addStretch()

        self._cancel_btn = ActionButton("取消", variant="quiet")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)
        action_layout.addWidget(self._cancel_btn)

        main_layout.addWidget(action_bar)

    def _create_top_bar_context(self) -> QWidget:
        context = QWidget(self)
        context.setObjectName("voiceStudioTopContext")
        context_layout = QHBoxLayout(context)
        context_layout.setContentsMargins(0, 0, 0, 0)
        context_layout.setSpacing(8)

        self._metric_total = _HeaderStat("角色", context)
        self._metric_ready = _HeaderStat("已配", context)
        self._metric_pending = _HeaderStat("待确认", context)
        self._metric_expired = _HeaderStat("过期", context)
        header_metrics = QWidget(context)
        header_metrics.setObjectName("voiceStudioHeaderMetrics")
        header_metrics_layout = QHBoxLayout(header_metrics)
        header_metrics_layout.setContentsMargins(0, 0, 0, 0)
        header_metrics_layout.setSpacing(6)
        for metric in (
            self._metric_total,
            self._metric_ready,
            self._metric_pending,
            self._metric_expired,
        ):
            metric.setMinimumSize(66, 48)
            metric.setMaximumSize(82, 56)
            header_metrics_layout.addWidget(metric)
        context_layout.addWidget(header_metrics)

        self._provider_dropdown = _ProviderDropdown(context)
        self._provider_dropdown.setObjectName("voiceProviderSwitch")
        self._provider_dropdown.setAccessibleName("切换 TTS 平台")
        for spec in all_provider_ui_specs():
            self._provider_dropdown.add_item(
                spec.provider.value,
                spec.label,
                ", ".join(spec.models[:2]) + ("…" if len(spec.models) > 2 else ""),
            )
        self._provider_dropdown.item_selected.connect(self._switch_provider)
        self._set_provider_switch_state(self._get_current_provider(), loading=False)
        context_layout.addWidget(self._provider_dropdown)

        self._project_selector = QComboBox(context)
        self._project_selector.setObjectName("topBarProjectSelector")
        self._project_selector.setMinimumWidth(160)
        self._project_selector.setAccessibleName("当前配音项目")
        self._project_selector.addItem("未选择项目")
        self._project_selector.currentIndexChanged.connect(self._on_project_selector_changed)
        context_layout.addWidget(self._project_selector)
        return context

    @staticmethod
    def _create_tab_placeholder(label: str) -> QWidget:
        """Create a compact placeholder for a tab that will build on demand."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.addStretch()
        hint = QLabel(f"正在准备{label}…")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setObjectName("voiceStudioDeferredHint")
        layout.addWidget(hint)
        layout.addStretch()
        return widget

    def _ensure_tab_built(self, index: int) -> None:
        if index not in self._deferred_tab_builders:
            return
        self._pending_deferred_tab_index = index
        # Show the lightweight tab placeholder for one frame before creating
        # multimedia players, editors, and settings controls on the GUI thread.
        if self.isVisible():
            self._deferred_tab_timer.start(16)

    def _build_next_deferred_tab(self) -> None:
        if getattr(self, "_shutdown_done", False) or not self._deferred_tab_builders:
            return
        if not self.isVisible():
            return
        index = self._pending_deferred_tab_index
        self._pending_deferred_tab_index = None
        if index is None:
            index = self._tabs.currentIndex()
        if index in self._deferred_tab_builders:
            self._build_deferred_tab(index)

    def showEvent(self, event: QShowEvent) -> None:
        """Build only the selected lazy tab after the page gets a paint turn."""
        super().showEvent(event)
        index = self._tabs.currentIndex()
        if index in self._deferred_tab_builders and not getattr(self, "_shutdown_done", False):
            self._pending_deferred_tab_index = index
            self._deferred_tab_timer.start(16)

    def hideEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        """Pause optional tab construction as soon as the page is hidden."""
        self._deferred_tab_timer.stop()
        super().hideEvent(event)

    def _build_deferred_tab(self, index: int) -> None:
        builder = self._deferred_tab_builders.pop(index, None)
        label = self._deferred_tab_labels.get(index, "")
        if builder is None:
            return

        old_widget = self._tabs.widget(index)
        current_widget = self._tabs.currentWidget()
        try:
            built_widget = builder()
        except Exception:
            self._deferred_tab_builders[index] = builder
            _logger.exception("Failed to build deferred voice studio tab: %s", label)
            return

        self._tabs.blockSignals(True)
        try:
            self._tabs.removeTab(index)
            self._tabs.insertTab(index, built_widget, label)
            if current_widget is old_widget:
                self._tabs.setCurrentIndex(index)
            elif current_widget is not None and self._tabs.indexOf(current_widget) >= 0:
                self._tabs.setCurrentWidget(current_widget)
        finally:
            self._tabs.blockSignals(False)
        if old_widget is not None:
            old_widget.deleteLater()

        if index in (1, 2, 3):
            self._populate_chapter_combo()
        if index == 1:
            active_stream = self._script_streams.get(self._active_chapter_number)
            if (
                active_stream is not None
                and self._generating_chapter == self._active_chapter_number
            ):
                if active_stream.text:
                    active_stream._force_render = True  # Deferred tab built — repaint.
                    self._render_script_stream()
                else:
                    self._render_script_gen_placeholder(self._active_chapter_number)
                progress_data = dict(active_stream.progress_data)
                progress_data.setdefault("chapter", self._active_chapter_number)
                self._update_script_generation_progress(
                    active_stream.progress_step,
                    progress_data,
                )
            elif self._current_script is not None:
                self._render_script_html()
            self._update_script_source_hint()
            if self._avatars_dirty:
                self._update_avatars()
        if index == 2:
            self._populate_voice_room_segments()
        if index == 3:
            self._render_mix_manifest()
        self._refresh_workflow_controls()

        # Flush any deferred play requests that were waiting for the audio tab.
        if self._pending_room_play_full:
            self._pending_room_play_full = False
            self._on_play_full_chapter()
        if self._pending_room_play_segment is not None:
            seg_idx = self._pending_room_play_segment
            self._pending_room_play_segment = None
            if self._audio_player is not None:
                # Ensure audio is loaded (same logic as _on_play_full_chapter).
                if not self._audio_player.has_timeline():
                    audio_path: Path | None = None
                    if (
                        self._current_audio_result
                        and self._current_audio_result.assembled_audio_path
                        and not self._current_audio_result.metadata.get("assembly_stale")
                    ):
                        p = Path(self._current_audio_result.assembled_audio_path)
                        if p.is_file():
                            audio_path = p
                    if audio_path is not None:
                        timeline = self._current_timeline
                        if timeline is None:
                            try:
                                from novel_forge.tts.pipeline.timeline_builder import (
                                    build_timeline,
                                )

                                timeline = build_timeline(
                                    self._current_audio_result.script,
                                    self._current_audio_result.segment_results,
                                )
                                self._current_timeline = timeline
                            except Exception:
                                timeline = None
                        self._audio_player.load_audio(audio_path, timeline=timeline)
                if self._audio_player.has_timeline():
                    self._voice_room_playback = True
                    self._room_chapter_play_requested = True
                    self._audio_player.seek_to_segment(seg_idx)
                    self._audio_player.play()

    def _create_voice_team_tab(self) -> QWidget:
        """Create voice team management tab."""
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(5)

        # Left: cast overview and cost-first assignment entry point.
        left_card = Surface("card")
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(7, 5, 7, 7)
        left_layout.setSpacing(4)

        left_layout.addWidget(SectionHeading("配音角色"))
        self._cast_strategy_hint = QLabel(
            "先复用音色库；没有可靠候选时再由 AI 设计。组建完成后自动准备每位角色的试听。"
        )
        self._cast_strategy_hint.setObjectName("voiceCastStrategyHint")
        self._cast_strategy_hint.setWordWrap(True)
        left_layout.addWidget(self._cast_strategy_hint)
        self._character_search = QLineEdit()
        self._character_search.setObjectName("voiceCastSearch")
        self._character_search.setPlaceholderText("搜索旁白、角色、定位或音色状态")
        self._character_search.setClearButtonEnabled(True)
        self._character_search.textChanged.connect(self._filter_character_list)
        left_layout.addWidget(self._character_search)
        self._character_list = QListWidget()
        self._character_list.setObjectName("voiceCastList")
        # The shared scroll-edge fade is useful for long document readers, but
        # on this compact, selectable list it makes the first rows look
        # translucent after a scroll.  Keep cast entries fully opaque so a
        # selected character never appears to disappear under an overlay.
        self._character_list.setProperty(SCROLL_FADE_DISABLED_PROPERTY, True)
        # Do not claim that the viewport paints every pixel itself.  Its
        # background comes from QSS; marking it as WA_OpaquePaintEvent makes Qt
        # skip clearing the backing store and can retain pixels from the page
        # shown previously (most visibly Settings content inside this list).
        cast_viewport = self._character_list.viewport()
        cast_viewport.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        cast_viewport.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._character_list.currentItemChanged.connect(self._on_character_selected)
        self._character_list.itemActivated.connect(self._on_character_activated)
        self._character_list.setAccessibleName("配音角色与试听状态")
        self._character_list.setToolTip("单击查看匹配依据；双击或按 Enter 直接播放/生成试听")
        left_layout.addWidget(self._character_list, 1)

        btn_layout = QHBoxLayout()
        self._build_team_btn = ActionButton("自动组建并准备试听", variant="primary")
        self._build_team_btn.setToolTip("按音色库 → AI 设计 → 系统回退的顺序分配，并缓存角色试听")
        self._build_team_btn.clicked.connect(self._on_build_voice_team)
        btn_layout.addWidget(self._build_team_btn)
        left_layout.addLayout(btn_layout)

        # Building a cast can involve several remote calls and a per-character
        # preview pass.  A transient status-bar message made a successful click
        # indistinguishable from a stalled or failed job, especially while the
        # author remained on this first tab.  Keep the job state next to its
        # trigger and retain the terminal outcome until the next build/project.
        self._voice_team_task_card = QFrame()
        self._voice_team_task_card.setObjectName("voiceTeamTaskCard")
        task_layout = QVBoxLayout(self._voice_team_task_card)
        task_layout.setContentsMargins(9, 7, 9, 8)
        task_layout.setSpacing(4)

        task_header = QHBoxLayout()
        task_header.setContentsMargins(0, 0, 0, 0)
        task_header.setSpacing(6)
        self._voice_team_task_state = Badge("待启动", tone="muted")
        self._voice_team_task_state.setObjectName("voiceTeamTaskState")
        task_header.addWidget(self._voice_team_task_state)
        self._voice_team_task_title = QLabel("配音团队构建")
        self._voice_team_task_title.setObjectName("voiceTeamTaskTitle")
        task_header.addWidget(self._voice_team_task_title, 1)
        task_layout.addLayout(task_header)

        self._voice_team_task_progress = QProgressBar()
        self._voice_team_task_progress.setObjectName("voiceTeamTaskProgress")
        self._voice_team_task_progress.setRange(0, 100)
        self._voice_team_task_progress.setValue(0)
        self._voice_team_task_progress.setTextVisible(True)
        task_layout.addWidget(self._voice_team_task_progress)

        self._voice_team_task_detail = QLabel(
            "提交后会显示当前阶段、角色处理数、试听准备情况与最终结果。"
        )
        self._voice_team_task_detail.setObjectName("voiceTeamTaskDetail")
        self._voice_team_task_detail.setWordWrap(True)
        task_layout.addWidget(self._voice_team_task_detail)
        self._voice_team_task_card.setVisible(False)
        left_layout.addWidget(self._voice_team_task_card)

        layout.addWidget(left_card, 2)

        # Right: Voice details (3/5 width)
        right_card = Surface("card")
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(7, 5, 7, 7)
        right_layout.setSpacing(5)

        self._capability_hint = QLabel("正在读取平台能力…")
        self._capability_hint.setObjectName("voiceCapabilityHint")
        self._capability_hint.setWordWrap(True)
        right_layout.addWidget(self._capability_hint)

        detail_heading = QWidget()
        detail_heading.setObjectName("voiceDetailHeadingRow")
        detail_heading_row = QHBoxLayout(detail_heading)
        detail_heading_row.setContentsMargins(0, 0, 0, 0)
        detail_heading_row.addWidget(SectionHeading("角色音色"))
        detail_heading_row.addStretch()
        self._match_badge = QLabel("匹配待评估")
        self._match_badge.setObjectName("voiceMatchBadge")
        self._match_badge.setProperty("tone", "muted")
        detail_heading_row.addWidget(self._match_badge)
        self._apply_offset_btn = ActionButton("应用参数", variant="quiet")
        self._apply_offset_btn.clicked.connect(self._on_apply_offsets)
        detail_heading_row.addWidget(self._apply_offset_btn)
        right_layout.addWidget(detail_heading)

        # Structured voice info - expandable, no hard height cap
        self._voice_info_text = QTextBrowser()
        self._voice_info_text.setObjectName("voiceInfoPanel")
        self._voice_info_text.setOpenExternalLinks(False)
        self._voice_info_text.setMinimumHeight(100)
        right_layout.addWidget(self._voice_info_text, 1)

        # Empty state (shown when no character selected / no data)
        self._voice_empty = EmptyState(
            "尚未选择角色",
            "请从左侧列表选择角色查看音色详情，或点击「构建配音团队」自动分配。",
        )
        self._voice_empty.setVisible(False)
        right_layout.addWidget(self._voice_empty)

        preview_strip = QFrame()
        preview_strip.setObjectName("voicePreviewStrip")
        preview_layout = QHBoxLayout(preview_strip)
        preview_layout.setContentsMargins(10, 8, 10, 8)
        preview_layout.setSpacing(8)
        preview_copy = QVBoxLayout()
        preview_copy.setSpacing(1)
        preview_title = QLabel("角色试听")
        preview_title.setObjectName("voicePreviewTitle")
        self._preview_status_label = QLabel("选择角色后查看试听状态")
        self._preview_status_label.setObjectName("voicePreviewStatus")
        self._preview_status_label.setWordWrap(True)
        preview_copy.addWidget(preview_title)
        preview_copy.addWidget(self._preview_status_label)
        preview_layout.addLayout(preview_copy, 1)
        self._preview_sample_combo = QComboBox()
        self._preview_sample_combo.setToolTip("分别试听常规、短句、情绪和紧急语境")
        self._preview_sample_combo.addItem("常规台词", "identity")
        self._preview_sample_combo.addItem("短句", "short")
        self._preview_sample_combo.addItem("情绪台词", "emotion")
        self._preview_sample_combo.addItem("紧急台词", "urgent")
        self._preview_sample_combo.currentIndexChanged.connect(
            lambda _index: self._refresh_selected_voice_action_state()
        )
        preview_layout.addWidget(self._preview_sample_combo)
        self._preview_btn = ActionButton("生成试听", variant="primary")
        self._preview_btn.setToolTip("按当前音色与速度、音高、音量参数合成试听")
        self._preview_btn.clicked.connect(self._on_preview_voice)
        preview_layout.addWidget(self._preview_btn)
        right_layout.addWidget(preview_strip)

        # Voice selection
        self._voice_select_row = SettingRow("选择音色", "为当前角色分配 TTS 系统音色")
        self._voice_combo = QComboBox()
        self._voice_combo.addItem("加载中...")
        self._voice_combo.currentIndexChanged.connect(self._on_voice_combo_changed)
        self._voice_select_row.set_input(self._voice_combo)
        right_layout.addWidget(self._voice_select_row)

        # Parameter adjustments use one row per dimension so labels and values
        # remain legible at narrower desktop widths.
        parameter_card = QFrame()
        parameter_card.setObjectName("voiceParameterCard")
        parameter_layout = QVBoxLayout(parameter_card)
        parameter_layout.setContentsMargins(10, 8, 10, 8)
        parameter_layout.setSpacing(6)
        parameter_header = QHBoxLayout()
        parameter_title = QLabel("表达微调")
        parameter_title.setObjectName("voiceParameterTitle")
        parameter_header.addWidget(parameter_title)
        parameter_header.addStretch()
        self._reset_offsets_btn = ActionButton("恢复自动", variant="quiet")
        self._reset_offsets_btn.clicked.connect(self._on_reset_offsets)
        parameter_header.addWidget(self._reset_offsets_btn)
        parameter_layout.addLayout(parameter_header)
        self._performance_policy_label = QLabel(
            "自动策略会保持角色声纹稳定；只有拖动过的字段会转为人工覆盖。"
        )
        self._performance_policy_label.setObjectName("voicePerformancePolicy")
        self._performance_policy_label.setWordWrap(True)
        parameter_layout.addWidget(self._performance_policy_label)

        speed_row = QHBoxLayout()
        speed_name = QLabel("语速")
        speed_name.setObjectName("voiceParameterName")
        speed_name.setFixedWidth(42)
        speed_row.addWidget(speed_name)
        self._speed_slider = QSlider(Qt.Orientation.Horizontal)
        self._speed_slider.setRange(-50, 50)
        self._speed_slider.setValue(0)
        self._speed_slider.setTickInterval(10)
        self._speed_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._speed_slider.setAccessibleName("角色语速倍率")
        self._speed_slider.setToolTip(
            "角色全局语速基线；短句会自动保持自然速度，场景节奏由配音脚本单独控制"
        )
        self._speed_label = QLabel(f"{self._settings.tts_default_speed:.2f}×")
        self._speed_slider.valueChanged.connect(self._on_speed_slider_changed)
        speed_row.addWidget(self._speed_slider, 1)
        speed_row.addWidget(self._speed_label)
        parameter_layout.addLayout(speed_row)

        pitch_row = QHBoxLayout()
        pitch_name = QLabel("音调")
        pitch_name.setObjectName("voiceParameterName")
        pitch_name.setFixedWidth(42)
        pitch_row.addWidget(pitch_name)
        self._pitch_slider = QSlider(Qt.Orientation.Horizontal)
        self._pitch_slider.setRange(-12, 12)
        self._pitch_slider.setValue(0)
        self._pitch_slider.setTickInterval(2)
        self._pitch_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._pitch_slider.setAccessibleName("角色音调半音偏移")
        self._pitch_slider.setToolTip("音调半音偏移；供应商不支持时由本地音频处理补齐")
        self._pitch_label = QLabel("+0 st")
        self._pitch_slider.valueChanged.connect(self._on_pitch_slider_changed)
        pitch_row.addWidget(self._pitch_slider, 1)
        pitch_row.addWidget(self._pitch_label)
        parameter_layout.addLayout(pitch_row)

        volume_row = QHBoxLayout()
        volume_name = QLabel("音量")
        volume_name.setObjectName("voiceParameterName")
        volume_name.setFixedWidth(42)
        volume_row.addWidget(volume_name)
        self._vol_slider = QSlider(Qt.Orientation.Horizontal)
        self._vol_slider.setRange(-50, 50)
        self._vol_slider.setValue(0)
        self._vol_slider.setTickInterval(10)
        self._vol_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._vol_slider.setAccessibleName("角色音量倍率")
        self._vol_slider.setToolTip("最终音量倍率；供应商不支持时由本地音频处理补齐")
        self._vol_label = QLabel("1.00×")
        self._vol_slider.valueChanged.connect(self._on_volume_slider_changed)
        volume_row.addWidget(self._vol_slider, 1)
        volume_row.addWidget(self._vol_label)
        parameter_layout.addLayout(volume_row)
        right_layout.addWidget(parameter_card)

        # Action buttons - grouped: voice source (clone/design) + audition
        action_btn_layout = QHBoxLayout()
        self._clone_voice_btn = ActionButton("上传克隆", variant="secondary")
        self._clone_voice_btn.setToolTip("上传参考音频；不支持本地上传的平台会要求供应商文件 ID")
        self._clone_voice_btn.clicked.connect(self._on_clone_voice)
        action_btn_layout.addWidget(self._clone_voice_btn)

        self._design_voice_btn = ActionButton("设计新音色", variant="secondary")
        self._design_voice_btn.setToolTip(
            "使用角色画像和编辑声纹生成可复用专属音色；部分平台会对试听文本计费"
        )
        self._design_voice_btn.clicked.connect(self._on_design_voice)
        action_btn_layout.addWidget(self._design_voice_btn)

        # Approve: flips a pending designed/cloned voice to approved so it can
        # enter formal synthesis. Only visible for pending entries.
        self._approve_voice_btn = ActionButton("确认音色", variant="primary")
        self._approve_voice_btn.setToolTip(
            "试听确认该音色符合角色（如性别）后，解除合成限制并正式启用"
        )
        self._approve_voice_btn.clicked.connect(self._on_approve_voice)
        action_btn_layout.addWidget(self._approve_voice_btn)

        # Confirm entire team: one-click gate that lets post-archive TTS skip
        # per-entry checks.  Visible when the team exists, confirmed=False, and
        # every entry is ready (otherwise the confirm call returns diagnoses).
        self._confirm_team_btn = ActionButton("确认整支团队", variant="outline")
        self._confirm_team_btn.setToolTip(
            "一键确认整支配音团队可用于自动配音，后续章节归档后将跳过逐角色检查"
        )
        # Hidden until a team is loaded; _on_voice_team_updated syncs visibility.
        self._confirm_team_btn.setVisible(False)
        self._confirm_team_btn.clicked.connect(self._on_confirm_team)
        action_btn_layout.addWidget(self._confirm_team_btn)

        # Multi-candidate A/B preview: compare up to 3 system-voice candidates
        # with the same sample text, then persist the author's selection into
        # the voice team (P1: 多候选音色 A/B 预览).
        self._voice_preview_ab_btn = ActionButton("音色 A/B 对比", variant="outline")
        self._voice_preview_ab_btn.setToolTip(
            "为选中角色挑选 3 个系统音色候选，用同一段文本批量合成后对比试听，确认后写回配音团队"
        )
        self._voice_preview_ab_btn.clicked.connect(self._open_voice_preview_dialog)
        action_btn_layout.addWidget(self._voice_preview_ab_btn)
        action_btn_layout.addStretch()
        right_layout.addLayout(action_btn_layout)

        layout.addWidget(right_card, 3)

        return widget

    def _create_script_tab(self) -> QWidget:
        """Create dubbing script tab with visual character labels and progress coloring."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        # Top bar: chapter selector + action buttons in one row
        top_bar = QHBoxLayout()
        top_bar.setSpacing(6)
        chapter_lbl = QLabel("章节:")
        chapter_lbl.setObjectName("voiceFieldLabel")
        top_bar.addWidget(chapter_lbl)
        self._chapter_combo = QComboBox()
        self._chapter_combo.setMinimumWidth(100)
        self._chapter_combo.currentIndexChanged.connect(self._on_script_chapter_changed)
        top_bar.addWidget(self._chapter_combo)

        self._generate_script_btn = ActionButton("生成脚本", variant="primary")
        self._generate_script_btn.clicked.connect(lambda _checked=False: self._on_generate_script())
        top_bar.addWidget(self._generate_script_btn)

        self._edit_script_btn = ActionButton("编辑", variant="secondary")
        self._edit_script_btn.setToolTip("逐段修改台词、情绪、语气与语速；保存后自动使旧音频待重建")
        # QPushButton.clicked carries a ``checked`` boolean.  Do not pass it
        # through as a segment index (``False`` is also the integer 0).
        self._edit_script_btn.clicked.connect(lambda _checked=False: self._on_edit_script())
        top_bar.addWidget(self._edit_script_btn)

        self._edit_sound_design_btn = ActionButton("声场设计", variant="secondary")
        self._edit_sound_design_btn.setToolTip(
            "编辑环境音、剧情音效和 BGM 的片段锚点、音量、淡入淡出与对白下压"
        )
        self._edit_sound_design_btn.clicked.connect(self._on_edit_sound_design)
        top_bar.addWidget(self._edit_sound_design_btn)

        self._synthesize_from_script_btn = ActionButton("合成", variant="secondary")
        self._synthesize_from_script_btn.clicked.connect(self._on_synthesize_from_script)
        top_bar.addWidget(self._synthesize_from_script_btn)
        top_bar.addStretch()

        self._clear_script_chapter_btn = ActionButton("清理", variant="quiet")
        self._clear_script_chapter_btn.setToolTip(
            "清理当前章节的源配音脚本；已合成的音频和字幕会保留"
        )
        self._clear_script_chapter_btn.clicked.connect(self._on_clear_script_chapter)
        top_bar.addWidget(self._clear_script_chapter_btn)
        layout.addLayout(top_bar)

        # The script file name is stable across generations, so freshness must
        # be expressed explicitly in the UI instead of asking authors to infer
        # it from timestamps.  Stale and legacy scripts stay inspectable but
        # cannot enter synthesis; replacement is atomic and clears derivatives
        # only after the new reviewed script has succeeded.
        self._script_freshness_bar = QFrame()
        self._script_freshness_bar.setObjectName("voiceScriptFreshnessBar")
        freshness_layout = QHBoxLayout(self._script_freshness_bar)
        freshness_layout.setContentsMargins(8, 5, 8, 5)
        freshness_layout.setSpacing(7)
        self._script_freshness_badge = Badge("版本待检查", tone="muted")
        freshness_layout.addWidget(self._script_freshness_badge)
        self._script_freshness_hint = QLabel("选择章节后核对配音脚本与定稿正文。")
        self._script_freshness_hint.setObjectName("voiceScriptFreshnessHint")
        self._script_freshness_hint.setWordWrap(True)
        freshness_layout.addWidget(self._script_freshness_hint, 1)
        self._replace_script_btn = ActionButton("用当前正文覆盖", variant="primary")
        self._replace_script_btn.clicked.connect(lambda _checked=False: self._on_generate_script())
        freshness_layout.addWidget(self._replace_script_btn)
        self._remove_stale_script_btn = ActionButton("清理旧版", variant="quiet")
        self._remove_stale_script_btn.clicked.connect(self._on_clear_script_chapter)
        freshness_layout.addWidget(self._remove_stale_script_btn)
        self._script_freshness_bar.setVisible(False)
        layout.addWidget(self._script_freshness_bar)

        # Keep character navigation and statistics as two logical rows.  The
        # previous one-row layout made the metrics steal horizontal space from
        # the chips.  Once enough characters existed, QScrollArea created an
        # h-scrollbar inside a fixed 32 px viewport and covered the chips.
        avatar_bar = Surface("card")
        avatar_bar.setProperty("compact", "true")
        avatar_bar.setObjectName("voiceScriptContextStrip")
        avatar_layout = QVBoxLayout(avatar_bar)
        avatar_layout.setContentsMargins(6, 4, 6, 4)
        avatar_layout.setSpacing(3)

        avatar_row = QWidget(avatar_bar)
        avatar_row.setObjectName("voiceScriptCharacterNavRow")
        avatar_layout_row = QHBoxLayout(avatar_row)
        avatar_layout_row.setContentsMargins(0, 0, 0, 0)
        avatar_layout_row.setSpacing(4)
        avatar_lbl = QLabel("角色:")
        avatar_lbl.setObjectName("voiceFieldLabel")
        avatar_layout_row.addWidget(avatar_lbl)
        self._avatar_scroll = QScrollArea()
        self._avatar_scroll.setWidgetResizable(True)
        self._avatar_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._avatar_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._avatar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # 36 px chips + their vertical margins + the native h-scrollbar.
        # Qt's offscreen/macOS styles consume about 18 px outside the viewport,
        # so 64 px preserves the full 46 px chip content height when it overflows.
        self._avatar_scroll.setFixedHeight(64)
        self._avatar_scroll.setObjectName("avatarScrollArea")
        self._avatar_container = QWidget()
        self._avatar_layout_inner = QHBoxLayout(self._avatar_container)
        self._avatar_layout_inner.setContentsMargins(4, 2, 4, 2)
        self._avatar_layout_inner.setSpacing(6)
        self._avatar_scroll.setWidget(self._avatar_container)
        avatar_layout_row.addWidget(self._avatar_scroll, 1)
        avatar_layout.addWidget(avatar_row)

        metrics_row = QWidget(avatar_bar)
        metrics_row.setObjectName("voiceScriptMetricsRow")
        metrics_layout = QHBoxLayout(metrics_row)
        metrics_layout.setContentsMargins(0, 0, 0, 0)
        metrics_layout.setSpacing(4)
        metrics_layout.addStretch()
        self._seg_metric_total = _InlineMetric("片段")
        self._seg_metric_narration = _InlineMetric("旁白")
        self._seg_metric_dialogue = _InlineMetric("对白")
        self._seg_metric_sfx = _InlineMetric("声场")
        for metric in (
            self._seg_metric_total,
            self._seg_metric_narration,
            self._seg_metric_dialogue,
            self._seg_metric_sfx,
        ):
            metrics_layout.addWidget(metric)
        avatar_layout.addWidget(metrics_row)
        layout.addWidget(avatar_bar)

        self._speaker_review_bar = QFrame()
        self._speaker_review_bar.setObjectName("voiceSpeakerReviewBar")
        review_layout = QHBoxLayout(self._speaker_review_bar)
        review_layout.setContentsMargins(8, 5, 8, 5)
        review_layout.setSpacing(7)
        self._speaker_review_badge = Badge("说话人复核", tone="muted")
        review_layout.addWidget(self._speaker_review_badge)
        self._speaker_review_hint = QLabel("生成脚本后，这里会显示需要人工确认的具体片段。")
        self._speaker_review_hint.setObjectName("voiceSpeakerReviewHint")
        self._speaker_review_hint.setWordWrap(True)
        review_layout.addWidget(self._speaker_review_hint, 1)
        self._speaker_review_btn = ActionButton("开始复核", variant="secondary")
        self._speaker_review_btn.clicked.connect(self._on_review_pending_speakers)
        review_layout.addWidget(self._speaker_review_btn)
        layout.addWidget(self._speaker_review_bar)

        # Stable, event-driven progress for autonomous script production.
        # This deliberately sits outside QTextBrowser: updating a progress
        # label/bar does not reset the script viewport or steal scroll focus.
        self._script_generation_bar = QFrame()
        self._script_generation_bar.setObjectName("voiceScriptGenerationBar")
        generation_layout = QHBoxLayout(self._script_generation_bar)
        generation_layout.setContentsMargins(8, 5, 8, 5)
        generation_layout.setSpacing(7)
        self._script_generation_badge = Badge("脚本导演 1/4", tone="warning")
        generation_layout.addWidget(self._script_generation_badge)
        self._script_generation_progress = QProgressBar()
        self._script_generation_progress.setObjectName("voiceScriptGenerationProgress")
        self._script_generation_progress.setRange(0, 100)
        self._script_generation_progress.setValue(0)
        self._script_generation_progress.setTextVisible(True)
        self._script_generation_progress.setFormat("正在分析正文与角色…")
        generation_layout.addWidget(self._script_generation_progress, 1)
        self._script_generation_bar.setVisible(False)
        layout.addWidget(self._script_generation_bar)

        # Script viewer - fills remaining space
        script_card = Surface("panel")
        script_layout = QVBoxLayout(script_card)
        script_layout.setContentsMargins(6, 4, 6, 6)
        script_layout.setSpacing(2)
        script_header = QHBoxLayout()
        script_header.setSpacing(6)
        script_hdr = QLabel("配音脚本")
        script_hdr.setObjectName("voiceSectionTitle")
        script_header.addWidget(script_hdr)
        script_header.addStretch()
        self._script_source_hint = QLabel()
        self._script_source_hint.setObjectName("voiceScriptSourceHint")
        self._script_source_hint.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        script_header.addWidget(self._script_source_hint)
        script_layout.addLayout(script_header)
        self._script_browser = QTextBrowser()
        self._script_browser.setReadOnly(True)
        self._script_browser.setOpenLinks(False)
        self._script_browser.setOpenExternalLinks(False)
        self._script_browser.anchorClicked.connect(self._on_script_anchor_clicked)
        self._script_browser.setObjectName("dubbingScriptBrowser")
        self._script_browser.verticalScrollBar().actionTriggered.connect(
            self._on_script_browser_manually_scrolled
        )
        self._script_stream_follow = StreamFollowController(self._script_browser)
        script_layout.addWidget(self._script_browser, 1)
        layout.addWidget(script_card, 1)

        # Animation timers
        self._synth_anim_timer = QTimer(self)
        self._synth_anim_timer.setInterval(600)
        self._synth_anim_timer.timeout.connect(self._tick_synth_animation)
        # Script progress is driven by real worker events.  There is no timer
        # that rewrites the browser while the model is thinking.
        self._script_gen_timer = None
        # Register periodic timers with PageVisibilityMixin so they are paused
        # when the page is hidden and resumed (if active) when it becomes
        # visible again. Without this, the 600ms/400ms spinners keep firing
        # _render_script_html on an invisible page, starving the Qt event loop.
        self._register_periodic_timer(self._synth_anim_timer)

        self._update_script_source_hint()
        self._refresh_script_freshness_ui()
        return widget

    def _create_voice_room_tab(self) -> QWidget:
        """Create the take-by-take audition and re-recording workspace."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(7)

        header = QWidget()
        header.setObjectName("voiceCompactToolbar")
        header_layout = QGridLayout(header)
        header_layout.setContentsMargins(10, 5, 8, 5)
        header_layout.setSpacing(8)
        title = QLabel("配音室")
        title.setObjectName("voiceWorkspaceTitle")
        header_layout.addWidget(title, 0, 0)
        subtitle = QLabel("选择片段试听或播放全章，右侧台词与字幕会实时跟随。")
        subtitle.setObjectName("panelDescription")
        subtitle.setWordWrap(True)
        header_layout.addWidget(subtitle, 0, 1, 1, 3)
        chapter_label = QLabel("章节:")
        chapter_label.setObjectName("voiceFieldLabel")
        header_layout.addWidget(chapter_label, 1, 0)
        self._room_chapter_combo = QComboBox()
        self._room_chapter_combo.setMinimumWidth(100)
        self._room_chapter_combo.currentIndexChanged.connect(self._on_room_chapter_changed)
        header_layout.addWidget(self._room_chapter_combo, 1, 1)
        self._room_play_full_btn = ActionButton("▶ 播放全章", variant="secondary")
        self._room_play_full_btn.setToolTip("从头播放本章已装配音频；播放后可在同一位置暂停或继续")
        self._room_play_full_btn.clicked.connect(self._on_play_full_chapter)
        header_layout.addWidget(self._room_play_full_btn, 1, 2)
        header_layout.setColumnStretch(3, 1)
        layout.addWidget(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("voiceStudioRoomSplitter")
        splitter.setChildrenCollapsible(False)

        take_list_panel = Surface("card")
        take_list_layout = QVBoxLayout(take_list_panel)
        take_list_layout.setContentsMargins(8, 8, 8, 8)
        take_list_layout.setSpacing(6)
        list_title = QLabel("本章分段")
        list_title.setObjectName("voiceSectionTitle")
        take_list_layout.addWidget(list_title)
        self._room_segment_list = QListWidget()
        self._room_segment_list.setObjectName("voiceRoomSegmentList")
        self._room_segment_list.setMinimumWidth(260)
        self._room_segment_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._room_segment_list.currentRowChanged.connect(self._on_voice_room_segment_changed)
        # Double-clicking a segment jumps the chapter player to that segment and
        # resumes playback, so the voice room can audition the full chapter in
        # order and iterate sentence-by-sentence (see ``_on_room_segment_double_clicked``).
        self._room_segment_list.itemDoubleClicked.connect(self._on_room_segment_double_clicked)
        take_list_layout.addWidget(self._room_segment_list, 1)
        splitter.addWidget(take_list_panel)

        editor_panel = Surface("card")
        editor_panel.setObjectName("voiceRoomEditorPanel")
        editor_panel.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        editor_layout = QVBoxLayout(editor_panel)
        editor_layout.setContentsMargins(12, 8, 12, 8)
        editor_layout.setSpacing(6)
        inspector_header = QWidget()
        inspector_header.setObjectName("voiceRoomInspectorHeader")
        inspector_header_layout = QHBoxLayout(inspector_header)
        inspector_header_layout.setContentsMargins(0, 0, 0, 0)
        inspector_header_layout.setSpacing(8)
        self._room_segment_heading = QLabel("选择一个片段")
        self._room_segment_heading.setObjectName("voiceWorkspaceTitle")
        inspector_header_layout.addWidget(self._room_segment_heading, 1)
        self._room_playback_state = QLabel("● 字幕待机")
        self._room_playback_state.setObjectName("voiceRoomPlaybackState")
        inspector_header_layout.addWidget(self._room_playback_state)
        editor_layout.addWidget(inspector_header)
        # Direction (synthesis parameters) and sound-context are reference
        # info for the performer.  Keeping them in a collapsible section that
        # defaults to collapsed hands their vertical space to the teleprompter
        # below — the primary reading surface — so the "正在演绎" cue and the
        # spoken line are never pushed out of view by the action bar and
        # transport dock.  The performer can expand the section at any time.
        self._room_reference_section = CollapsibleSection(
            "参考信息",
            expanded=False,
            nested=True,
            persist_key="voice_room_reference_section",
        )
        self._room_direction = QLabel("配音脚本会为每段保存情绪、语气、语速、音量和音高指令。")
        self._room_direction.setObjectName("voiceRoomDirection")
        self._room_direction.setWordWrap(True)
        self._room_reference_section.body_layout.addWidget(self._room_direction)
        self._room_sound_context = QLabel(
            "场景声音参考将在脚本生成后显示；这里只供演员把握空间与节奏，选择和混音请到后处理。"
        )
        self._room_sound_context.setObjectName("voiceRoomSoundContext")
        self._room_sound_context.setWordWrap(True)
        self._room_reference_section.body_layout.addWidget(self._room_sound_context)
        editor_layout.addWidget(self._room_reference_section)
        caption_stage = QFrame()
        caption_stage.setObjectName("voiceRoomCaptionStage")
        caption_layout = QVBoxLayout(caption_stage)
        caption_layout.setContentsMargins(10, 8, 10, 8)
        caption_layout.setSpacing(5)
        caption_meta = QHBoxLayout()
        subtitle_title = QLabel("提词器 · 前后文")
        subtitle_title.setObjectName("voiceRoomSubtitleTitle")
        caption_meta.addWidget(subtitle_title)
        self._room_caption_position = QLabel("字幕 -- / --")
        self._room_caption_position.setObjectName("voiceRoomCaptionPosition")
        caption_meta.addStretch()
        caption_meta.addWidget(self._room_caption_position)
        self._room_caption_speaker = Badge("待机", tone="muted")
        self._room_caption_speaker.setObjectName("voiceRoomCaptionSpeaker")
        caption_meta.addWidget(self._room_caption_speaker)
        caption_layout.addLayout(caption_meta)
        self._room_segment_text = QTextBrowser()
        self._room_segment_text.setObjectName("voiceRoomSegmentText")
        self._room_segment_text.setReadOnly(True)
        self._room_segment_text.setOpenExternalLinks(False)
        # This is the only vertically scrollable surface in the inspector.
        # Keeping the surrounding panel fixed to the splitter viewport avoids
        # the redundant macOS scrollbar gutter shown at the far right.
        # A modest minimum height lets the transport dock below retain its
        # full control surface when the window is not maximized.
        self._room_segment_text.setMinimumHeight(72)
        self._room_segment_text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._room_segment_text.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._room_segment_text.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        caption_layout.addWidget(self._room_segment_text, 1)
        editor_layout.addWidget(caption_stage, 1)

        action_bar = QWidget()
        action_bar.setObjectName("voiceRoomActionBar")
        actions = QGridLayout(action_bar)
        actions.setContentsMargins(8, 5, 8, 5)
        actions.setHorizontalSpacing(6)
        actions.setVerticalSpacing(4)
        self._room_edit_btn = ActionButton("编辑指导", variant="secondary")
        self._room_edit_btn.setToolTip("修改文本、情绪、语气、语速、音量或音高")
        self._room_edit_btn.clicked.connect(self._on_edit_voice_room_segment)
        actions.addWidget(self._room_edit_btn, 0, 0)
        self._room_generate_btn = ActionButton("生成试听", variant="primary")
        self._room_generate_btn.setToolTip("只调用当前段；不会重做本章其余片段")
        self._room_generate_btn.clicked.connect(self._on_synthesize_selected_segment)
        actions.addWidget(self._room_generate_btn, 0, 1)
        self._room_accept_btn = ActionButton("接受此版", variant="secondary")
        self._room_accept_btn.setToolTip("接受后才替换正式分段，并将全章标记为待重装配")
        self._room_accept_btn.clicked.connect(self._on_accept_segment_take)
        actions.addWidget(self._room_accept_btn, 0, 2)
        self._room_discard_btn = ActionButton("舍弃试听", variant="quiet")
        self._room_discard_btn.setToolTip("保留原正式版本；舍弃的文件可在后处理中统一清理")
        self._room_discard_btn.clicked.connect(self._on_discard_segment_take)
        actions.addWidget(self._room_discard_btn, 0, 3)
        self._room_status_badge = Badge("等待选择片段", tone="muted")
        actions.addWidget(
            self._room_status_badge,
            1,
            0,
            1,
            4,
            Qt.AlignmentFlag.AlignRight,
        )
        for column in range(4):
            actions.setColumnStretch(column, 1)
        editor_layout.addWidget(action_bar)

        transport_dock = QFrame()
        transport_dock.setObjectName("voiceRoomTransportDock")
        transport_layout = QVBoxLayout(transport_dock)
        transport_layout.setContentsMargins(8, 5, 8, 5)
        transport_layout.setSpacing(3)
        self._room_player_scope_label = QLabel("当前播放：片段")
        self._room_player_scope_label.setObjectName("voiceRoomPlayerScope")
        transport_layout.addWidget(self._room_player_scope_label)
        self._room_player_stack = _HeightAwareStackedWidget()
        self._room_player_stack.setObjectName("voiceRoomPlayerStack")

        self._segment_player = DubbingPlayerWidget(compact=True)
        self._segment_player.setObjectName("voiceRoomSegmentPlayer")
        self._segment_player.set_context_visible(False)
        self._segment_player.prev_segment_requested.connect(self._on_segment_player_prev)
        self._segment_player.next_segment_requested.connect(self._on_segment_player_next)
        self._segment_player.word_highlight.connect(self._on_room_word_highlight)
        self._segment_player.playback_state_changed.connect(
            self._on_segment_preview_playback_state_changed
        )
        self._segment_player.playback_finished.connect(self._on_segment_preview_finished)
        self._room_player_stack.addWidget(self._segment_player)

        # Full-chapter playback needs its own visible transport.  Previously the
        # room launched the hidden player in the post-processing tab while this
        # surface continued to show the selected clip's duration and progress,
        # producing contradictory states such as "全章已暂停" beside a 36s
        # segment timeline.  The stack keeps both playback scopes independent
        # and exposes the timeline that actually owns the current audio.
        self._room_chapter_player = DubbingPlayerWidget(compact=True)
        self._room_chapter_player.setObjectName("voiceRoomChapterPlayer")
        self._room_chapter_player.set_context_visible(False)
        self._room_chapter_player.segment_changed.connect(self._on_playback_segment_changed)
        self._room_chapter_player.character_changed.connect(self._on_playback_character_changed)
        self._room_chapter_player.word_highlight.connect(self._on_playback_word_highlight)
        self._room_chapter_player.progress_updated.connect(self._on_playback_progress)
        self._room_chapter_player.playback_state_changed.connect(
            self._on_chapter_playback_state_changed
        )
        self._room_chapter_player.playback_finished.connect(self._on_playback_finished)
        self._room_player_stack.addWidget(self._room_chapter_player)
        self._room_player_stack.setCurrentWidget(self._segment_player)
        transport_layout.addWidget(self._room_player_stack)
        editor_layout.addWidget(transport_dock)
        splitter.addWidget(editor_panel)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 5)
        splitter.setSizes([330, 790])
        layout.addWidget(splitter, 1)
        return widget

    def _create_audio_tab(self) -> QWidget:
        """Create the post-production workspace for complete chapter audio."""
        widget = QWidget()
        outer_layout = QVBoxLayout(widget)
        outer_layout.setContentsMargins(4, 4, 4, 4)
        outer_layout.setSpacing(4)

        self._post_workspace_tabs = QTabWidget()
        self._post_workspace_tabs.setObjectName("voicePostWorkspaceTabs")
        self._post_workspace_tabs.tabBar().setExpanding(False)

        master_widget = QWidget()
        layout = QVBoxLayout(master_widget)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(3)

        # One compact production toolbar: context on the left, authority in the
        # middle and the three workflow actions on the right.  Cleanup remains
        # in the overflow menu so this row stays usable in non-maximized windows.
        command_bar = QWidget(master_widget)
        command_bar.setObjectName("voicePostCommandBar")
        top_bar = QGridLayout(command_bar)
        top_bar.setContentsMargins(0, 0, 0, 0)
        top_bar.setHorizontalSpacing(5)
        chapter_lbl = QLabel("章节:")
        chapter_lbl.setObjectName("voiceFieldLabel")
        top_bar.addWidget(chapter_lbl, 0, 0)
        self._audio_chapter_combo = QComboBox()
        self._audio_chapter_combo.setMinimumWidth(100)
        self._audio_chapter_combo.currentIndexChanged.connect(self._on_audio_chapter_changed)
        top_bar.addWidget(self._audio_chapter_combo, 0, 1)

        self._audio_automation_selector = AudioAutomationModeSelector()
        self._audio_automation_selector.set_mode(self._audio_automation_mode)
        self._audio_automation_selector.mode_changed.connect(self._on_audio_automation_mode_changed)
        top_bar.addWidget(self._audio_automation_selector, 0, 2)

        self._synthesize_btn = ActionButton("合成", variant="primary")
        self._synthesize_btn.clicked.connect(self._on_synthesize)
        top_bar.addWidget(self._synthesize_btn, 0, 3)

        self._reassemble_btn = ActionButton("重装配", variant="secondary")
        self._reassemble_btn.setToolTip(
            "不再调用配音模型，直接用已批准的分段音频生成全章 MP3 与字幕"
        )
        self._reassemble_btn.clicked.connect(self._on_reassemble_chapter)
        top_bar.addWidget(self._reassemble_btn, 0, 4)

        self._full_pipeline_btn = ActionButton("全流程", variant="secondary")
        self._full_pipeline_btn.clicked.connect(self._on_full_pipeline)
        top_bar.addWidget(self._full_pipeline_btn, 0, 5)
        self._sync_audio_automation_mode_ui()

        export_menu = QMenu(self)
        export_menu.setObjectName("voiceStudioActionMenu")
        self._export_mp3_btn = QAction("导出 MP3", self)
        self._export_mp3_btn.triggered.connect(lambda: self._on_export_audio("mp3"))
        export_menu.addAction(self._export_mp3_btn)
        self._export_wav_btn = QAction("导出 WAV", self)
        self._export_wav_btn.triggered.connect(lambda: self._on_export_audio("wav"))
        export_menu.addAction(self._export_wav_btn)
        self._export_flac_btn = QAction("导出 FLAC", self)
        self._export_flac_btn.triggered.connect(lambda: self._on_export_audio("flac"))
        export_menu.addAction(self._export_flac_btn)
        self._export_lufs_btn = QAction("导出 MP3 (-14 LUFS)", self)
        self._export_lufs_btn.setToolTip("在已装配音频基础上重新标准化到 -14 LUFS")
        self._export_lufs_btn.triggered.connect(
            lambda: self._on_export_audio("mp3", target_lufs=-14.0)
        )
        export_menu.addAction(self._export_lufs_btn)
        export_menu.addSeparator()
        self._export_srt_btn = QAction("导出字幕", self)
        self._export_srt_btn.triggered.connect(self._on_export_srt)
        export_menu.addAction(self._export_srt_btn)
        export_menu.addSeparator()
        self._export_all_btn = QAction("全书打包导出 (MP3)", self)
        self._export_all_btn.triggered.connect(lambda: self._on_export_all())
        export_menu.addAction(self._export_all_btn)
        self._export_all_srt_btn = QAction("全书打包导出 (含字幕)", self)
        self._export_all_srt_btn.triggered.connect(lambda: self._on_export_all(include_srt=True))
        export_menu.addAction(self._export_all_srt_btn)
        export_menu.addSeparator()
        self._export_audiobook_btn = QAction("导出有声书包", self)
        self._export_audiobook_btn.setToolTip(
            "成品交付包：分章 MP3 + 目录/封面元数据 + 段级汇编报告（按章节顺序门控）"
        )
        self._export_audiobook_btn.triggered.connect(self._on_export_audiobook_package)
        export_menu.addAction(self._export_audiobook_btn)
        self._export_menu_btn = ActionButton("导出", variant="quiet")
        self._export_menu_btn.setMenu(export_menu)
        top_bar.addWidget(self._export_menu_btn, 0, 6)

        manage_menu = QMenu(self)
        manage_menu.setObjectName("voiceStudioActionMenu")
        self._cleanup_takes_btn = QAction("清理旧资产…", self)
        self._cleanup_takes_btn.setToolTip(
            "预览并勾选可清理的旧试听、检查点、孤儿音效与报告；正在使用的资产始终保留"
        )
        self._cleanup_takes_btn.triggered.connect(
            lambda _checked=False: self._on_cleanup_tts_files(
                default_categories={_TTS_CAT_ORPHAN_CANDIDATES}
            )
        )
        manage_menu.addAction(self._cleanup_takes_btn)
        manage_menu.addSeparator()
        self._clear_audio_chapter_btn = QAction("清理当前章节产物", self)
        self._clear_audio_chapter_btn.setToolTip(
            "清理当前章节全部可再生的 TTS 产物（脚本、音频、字幕与处理记录）"
        )
        self._clear_audio_chapter_btn.triggered.connect(self._on_clear_audio_chapter)
        manage_menu.addAction(self._clear_audio_chapter_btn)
        self._batch_clear_chapters_btn = QAction("批量清理章节", self)
        self._batch_clear_chapters_btn.setToolTip("选择多个章节批量清理 TTS 产物")
        self._batch_clear_chapters_btn.triggered.connect(self._on_batch_clear_chapters)
        manage_menu.addAction(self._batch_clear_chapters_btn)
        manage_menu.addSeparator()
        self._cleanup_tts_files_btn = QAction("清理过期 TTS 文件...", self)
        self._cleanup_tts_files_btn.setToolTip(
            "按类别清理 TTS 目录下的过期试听/候选/检查点/音效/报告等"
        )
        self._cleanup_tts_files_btn.triggered.connect(self._on_cleanup_tts_files)
        manage_menu.addAction(self._cleanup_tts_files_btn)
        manage_menu.addSeparator()
        self._reset_project_tts_btn = QAction("重新开始：清空可再生配音产物…", self)
        self._reset_project_tts_btn.setToolTip(
            "清空全部章节脚本、音频、字幕、试听、断点与混音报告；保留配音团队和声音资源库"
        )
        self._reset_project_tts_btn.triggered.connect(self._on_reset_project_tts_artifacts)
        manage_menu.addAction(self._reset_project_tts_btn)
        self._manage_menu_btn = ActionButton("更多", variant="quiet")
        self._manage_menu_btn.setMenu(manage_menu)
        top_bar.addWidget(self._manage_menu_btn, 0, 7)
        top_bar.setColumnStretch(2, 1)
        layout.addWidget(command_bar)

        status_bar = QGridLayout()
        status_bar.setHorizontalSpacing(4)
        status_bar.setVerticalSpacing(2)
        self._progress_badge = Badge("进度 0/0", tone="muted")
        status_bar.addWidget(self._progress_badge, 0, 0)
        self._automation_progress_badge = Badge("AI 伴随 · 待命", tone="muted")
        self._automation_progress_badge.setObjectName("audioAutomationProgressBadge")
        status_bar.addWidget(self._automation_progress_badge, 0, 1)
        self._sound_resolution_badge = Badge("场景声音: 等待脚本生成", tone="muted")
        self._sound_resolution_badge.setObjectName("soundResolutionBadge")
        status_bar.addWidget(self._sound_resolution_badge, 0, 2)
        self._inspect_mix_btn = ActionButton("查看混音清单", variant="quiet")
        self._inspect_mix_btn.clicked.connect(self._show_mix_manifest)
        status_bar.addWidget(self._inspect_mix_btn, 1, 0)
        self._open_sound_library_btn = ActionButton("补齐声音素材", variant="secondary")
        self._open_sound_library_btn.clicked.connect(self._show_sound_library)
        status_bar.addWidget(self._open_sound_library_btn, 1, 1)
        self._follow_playback_check = QCheckBox("跟随朗读")
        self._follow_playback_check.setChecked(True)
        self._follow_playback_check.setToolTip("手动滚动脚本后会暂停跟随；勾选可恢复自动定位。")
        self._follow_playback_check.toggled.connect(self._on_follow_playback_toggled)
        status_bar.addWidget(
            self._follow_playback_check,
            1,
            2,
            Qt.AlignmentFlag.AlignRight,
        )
        status_bar.setColumnStretch(2, 1)
        layout.addLayout(status_bar)

        # ── Resizable content: player (left) + script/subtitle (right) ──
        # A splitter keeps both columns usable on smaller windows and lets
        # readers prioritize either playback controls or synchronized text.
        content_splitter = QSplitter(Qt.Orientation.Horizontal)
        content_splitter.setObjectName("voiceStudioAudioSplitter")
        content_splitter.setChildrenCollapsible(False)

        # Left: DubbingPlayer + empty state
        left_panel = QWidget()
        left_panel.setObjectName("voiceStudioAudioPlayerPane")
        left_panel.setMinimumWidth(300)
        left_col = QVBoxLayout(left_panel)
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(4)
        self._audio_player = DubbingPlayerWidget(compact=True)
        self._audio_player.segment_changed.connect(self._on_playback_segment_changed)
        self._audio_player.character_changed.connect(self._on_playback_character_changed)
        self._audio_player.word_highlight.connect(self._on_playback_word_highlight)
        self._audio_player.progress_updated.connect(self._on_playback_progress)
        self._audio_player.playback_state_changed.connect(self._on_chapter_playback_state_changed)
        self._audio_player.playback_finished.connect(self._on_playback_finished)
        left_col.addWidget(self._audio_player, 1)

        self._audio_empty = QLabel("尚未合成音频 · 选择章节后点击“合成”或“全流程”")
        self._audio_empty.setObjectName("audioEmptyHint")
        self._audio_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._audio_empty.setVisible(True)
        left_col.addWidget(self._audio_empty)
        content_splitter.addWidget(left_panel)

        # Right column: tabbed script/subtitle + inline export
        right_panel = QWidget()
        right_panel.setObjectName("voiceStudioAudioTextPane")
        right_panel.setMinimumWidth(260)
        right_col = QVBoxLayout(right_panel)
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(4)

        # Tabbed browser: 实时脚本 / 字幕
        right_tabs = QTabWidget()
        right_tabs.setObjectName("audioRightTabs")
        right_tabs.tabBar().setExpanding(False)
        right_tabs.setAccessibleName("实时脚本与字幕")
        self._audio_right_tabs = right_tabs

        # Tab 1: Script sync browser
        script_sync_widget = QWidget()
        script_sync_layout = QVBoxLayout(script_sync_widget)
        script_sync_layout.setContentsMargins(4, 4, 4, 4)
        script_sync_layout.setSpacing(2)
        self._sync_script_browser = QTextBrowser()
        self._sync_script_browser.setReadOnly(True)
        self._sync_script_browser.setOpenExternalLinks(False)
        self._sync_script_browser.setObjectName("syncScriptBrowser")
        self._sync_script_browser.verticalScrollBar().actionTriggered.connect(
            self._on_script_browser_manually_scrolled
        )
        script_sync_layout.addWidget(self._sync_script_browser, 1)
        right_tabs.addTab(script_sync_widget, "实时脚本")

        # Tab 2: Subtitle display
        subtitle_widget = QWidget()
        subtitle_layout_v = QVBoxLayout(subtitle_widget)
        subtitle_layout_v.setContentsMargins(4, 4, 4, 4)
        subtitle_layout_v.setSpacing(2)
        self._subtitle_text = QTextEdit()
        self._subtitle_text.setReadOnly(True)
        subtitle_layout_v.addWidget(self._subtitle_text, 1)
        right_tabs.addTab(subtitle_widget, "字幕")

        mix_widget = QWidget()
        mix_layout = QVBoxLayout(mix_widget)
        mix_layout.setContentsMargins(4, 4, 4, 4)
        mix_layout.setSpacing(2)
        self._mix_manifest_browser = QTextBrowser()
        self._mix_manifest_browser.setReadOnly(True)
        self._mix_manifest_browser.setOpenExternalLinks(False)
        self._mix_manifest_browser.setObjectName("voiceMixManifestBrowser")
        self._mix_manifest_browser.setAccessibleName("场景声音与混音清单")
        mix_layout.addWidget(self._mix_manifest_browser, 1)
        right_tabs.addTab(mix_widget, "混音清单")

        right_col.addWidget(right_tabs, 1)

        content_splitter.addWidget(right_panel)
        content_splitter.setStretchFactor(0, 1)
        content_splitter.setStretchFactor(1, 1)
        content_splitter.setSizes([520, 520])
        layout.addWidget(content_splitter, 1)

        self._sound_library_panel = SoundLibraryPanel()
        self._sound_library_panel.generate_palette_requested.connect(
            self._on_generate_sound_palette
        )
        self._sound_library_panel.import_requested.connect(self._on_import_sound_assets)
        self._sound_library_panel.library_changed.connect(self._on_sound_library_changed)
        self._sound_library_panel.set_project_service(self._studio_service)
        self._post_workspace_tabs.addTab(master_widget, "成品装配")
        self._post_workspace_tabs.addTab(self._sound_library_panel, "声音资源库（项目 / 应用）")
        outer_layout.addWidget(self._post_workspace_tabs, 1)

        return widget

    def _show_mix_manifest(self) -> None:
        if hasattr(self, "_audio_right_tabs"):
            self._audio_right_tabs.setCurrentIndex(2)
        self._render_mix_manifest()

    def _show_sound_library(self) -> None:
        if hasattr(self, "_post_workspace_tabs"):
            self._post_workspace_tabs.setCurrentIndex(1)

    @staticmethod
    def _format_mix_time(value_ms: int) -> str:
        value_ms = max(0, int(value_ms or 0))
        minutes, remainder = divmod(value_ms, 60_000)
        seconds = remainder / 1000
        return f"{minutes}:{seconds:04.1f}"

    @staticmethod
    def _delivery_reason_label(reason: str) -> str:
        return {
            "incomplete_synthesis": "仍有语音片段未完成",
            "unresolved_sound_cues": "仍有场景声音未匹配素材",
            "assembly_stale": "脚本或分段已更新，成品待重装配",
            "quality_gate_failed": "音频质量检查未通过",
            "render_integrity_failed": "多轨渲染完整性未通过",
            "mix_render_integrity": "多轨渲染完整性未通过",
            "clipping": "检测到削波失真",
            "alignment_coverage": "语音与文本对齐覆盖不足",
            "text_error_rate": "语音文本误差率超标",
            "segment_quality": "仍有分段质量未达标",
            "master_measurement_unavailable": "无法取得母带响度测量",
            "master_loudness": "母带响度未达到目标",
            "master_true_peak": "母带真峰值超标",
            "cross_chapter_loudness": "与相邻章节响度差异过大",
            "speech_masking_risk": "背景轨可能遮蔽对白",
            "mix_transition_risk": "混音转场存在突变风险",
            "missing_assembled_audio": "未生成可交付的全章音频",
            "delivery_not_ready": "交付检查尚未完成",
        }.get(reason, reason.replace("_", " "))

    def _render_mix_manifest(self) -> None:
        """Expose authoring anchors, resolved timing, buses, and delivery evidence."""
        if not hasattr(self, "_mix_manifest_browser"):
            return
        script = self._current_script or self._playback_script
        if script is None:
            update_browser_html(
                self._mix_manifest_browser,
                f"<p style='color:{qcolor_hex('text.muted')}'>"
                "暂无混音清单。请先在“配音脚本”生成脚本，并通过“声场设计”添加或检查声音线索。"
                "</p>",
            )
            return

        result = self._current_audio_result
        use_render_evidence = bool(
            result
            and result.chapter_number == script.chapter_number
            and not result.metadata.get("assembly_stale")
        )
        resolution_items: dict[tuple[str, int], dict[str, Any]] = {}
        mix_events: dict[str, dict[str, Any]] = {}
        mix_plan: dict[str, Any] = {}
        if self._layout is not None and use_render_evidence:
            resolution_path = self._layout.tts_sound_resolution_path(script.chapter_number)
            mix_plan_path = self._layout.tts_mix_plan_path(script.chapter_number)
            try:
                payload = json.loads(resolution_path.read_text(encoding="utf-8"))
                for item in payload.get("resolutions", []):
                    if isinstance(item, dict):
                        key = (str(item.get("cue_kind") or ""), int(item.get("cue_index", 0)))
                        resolution_items[key] = item
            except (OSError, ValueError, TypeError):
                resolution_items = {}
            try:
                mix_plan = json.loads(mix_plan_path.read_text(encoding="utf-8"))
                for item in mix_plan.get("events", []):
                    if isinstance(item, dict):
                        mix_events[str(item.get("event_id") or "")] = item
            except (OSError, ValueError, TypeError):
                mix_plan = {}
                mix_events = {}

        parts = [
            f"<div style='font-family:{_HTML_BODY_FONT_FAMILY}; font-size:11pt; "
            "line-height:1.55; padding:5px;'>",
            f"<h3 style='margin:0 0 6px 0; color:{qcolor_hex('text.heading')}'>"
            f"第 {script.chapter_number} 章 · 混音清单</h3>",
        ]

        cue_count = len(script.soundscapes) + len(script.bgm_suggestions) + len(script.sfx_cues)
        if result is None:
            state_text = "待合成：片段锚点会在语音生成后换算成真实时间"
            state_color = qcolor_hex("status.warning")
        elif result.delivery_ready and not result.delivery_blocking_reasons:
            state_text = "可交付：人声、场景声音、多轨渲染和母带检查均已通过"
            state_color = qcolor_hex("status.success")
        else:
            reasons = result.delivery_blocking_reasons or delivery_blocking_reasons(result)
            state_text = "暂不可交付：" + "；".join(
                self._delivery_reason_label(reason) for reason in reasons
            )
            state_color = qcolor_hex("status.danger.deep")
        parts.append(
            f"<div style='background:{qcolor_rgba('status.info', 0.07)}; "
            f"border:1px solid {qcolor_rgba('status.info', 0.18)}; border-radius:7px; "
            f"padding:7px 9px; color:{state_color}'><b>{html_lib.escape(state_text)}</b><br>"
            f"<span style='color:{qcolor_hex('text.muted')}'>"
            f"人声 {len(script.segments)} 段 · 环境 {len(script.soundscapes)} 轨 · "
            f"BGM {len(script.bgm_suggestions)} 轨 · 音效 {len(script.sfx_cues)} 点</span></div>"
        )

        def resolution_html(kind: str, index: int) -> str:
            item = resolution_items.get((kind, index))
            if not use_render_evidence:
                return f"<span style='color:{qcolor_hex('status.warning')}'>待合成后匹配素材</span>"
            if not item or item.get("status") != "matched":
                reason = html_lib.escape(str((item or {}).get("reason") or "未找到已批准素材"))
                return f"<span style='color:{qcolor_hex('status.danger.deep')}'>未匹配 · {reason}</span>"
            asset = html_lib.escape(
                str(item.get("asset_id") or item.get("relative_path") or "已批准素材")
            )
            return f"<span style='color:{qcolor_hex('status.success')}'>已匹配 · {asset}</span>"

        def timing_html(event_id: str, fallback: str) -> str:
            event = mix_events.get(event_id)
            if not event:
                return html_lib.escape(fallback)
            start = self._format_mix_time(int(event.get("start_ms", 0) or 0))
            end = self._format_mix_time(int(event.get("end_ms", 0) or 0))
            return f"真实时间 {start}–{end}"

        if cue_count == 0:
            parts.append(
                f"<p style='color:{qcolor_hex('text.muted')}'>本章尚未设计场景声音。"
                "可回到“配音脚本 → 声场设计”新增环境音、BGM 或剧情音效。</p>"
            )
        else:
            parts.append(
                f"<h4 style='margin:12px 0 5px; color:{qcolor_hex('text.secondary')}'>声音轨与触发点</h4>"
            )
        for index, cue in enumerate(script.soundscapes):
            start = cue.start_segment_index + 1 if cue.start_segment_index is not None else 1
            end = (
                f"至第 {cue.end_segment_index + 1} 段"
                if cue.end_segment_index is not None
                else "至章节末"
            )
            parts.append(
                f"<div style='margin:5px 0; padding:6px 8px; border-left:3px solid "
                f"{qcolor_hex('status.info')}; background:{qcolor_rgba('status.info', 0.045)}'>"
                f"<b>[环境] {html_lib.escape(cue.name)}</b> · "
                f"{timing_html(f'soundscape:{index}', f'第 {start} 段{end}')}<br>"
                f"音量 {cue.volume:.0%} · 对白下压 {cue.ducking_db:g}dB · "
                f"淡入/淡出 {cue.fade_in_ms}/{cue.fade_out_ms}ms · "
                f"{resolution_html('soundscape', index)}</div>"
            )
        for index, cue in enumerate(script.bgm_suggestions):
            start = cue.start_segment_index + 1 if cue.start_segment_index is not None else 1
            end = (
                f"至第 {cue.end_segment_index + 1} 段"
                if cue.end_segment_index is not None
                else "至章节末"
            )
            name = cue.track_name or cue.mood or "背景音乐"
            parts.append(
                f"<div style='margin:5px 0; padding:6px 8px; border-left:3px solid "
                f"{qcolor_hex('motif.purple')}; background:{qcolor_rgba('motif.purple', 0.045)}'>"
                f"<b>[BGM] {html_lib.escape(name)}</b> · "
                f"{timing_html(f'bgm:{index}', f'第 {start} 段{end}')}<br>"
                f"音量 {cue.volume:.0%} · 对白下压 {cue.ducking_db:g}dB · "
                f"淡入/淡出 {cue.fade_in_ms}/{cue.fade_out_ms}ms · "
                f"{resolution_html('bgm', index)}</div>"
            )
        placement_labels = {
            "script_anchor": "按脚本锚点",
            "moved_to_dialogue_gap": "已移至最近对白空隙",
            "dialogue_overlap_unavoidable": "无法避开对白，请人工试听",
        }
        for index, cue in enumerate(script.sfx_cues):
            segment = cue.trigger_segment_index + 1 if cue.trigger_segment_index is not None else 1
            fallback = f"第 {segment} 段 {cue.offset_ms:+d}ms"
            event = mix_events.get(f"sfx:{index}") or {}
            placement = placement_labels.get(str(event.get("placement_reason") or ""), "")
            placement_suffix = f" · {placement}" if placement else ""
            parts.append(
                f"<div style='margin:5px 0; padding:6px 8px; border-left:3px solid "
                f"{qcolor_hex('accent.warm')}; background:{qcolor_rgba('accent.warm', 0.045)}'>"
                f"<b>[音效] {html_lib.escape(cue.effect_name)}</b> · "
                f"{timing_html(f'sfx:{index}', fallback)}{placement_suffix}<br>"
                f"音量 {cue.volume:.0%} · 时长 {cue.duration_ms or 1000}ms · "
                f"{resolution_html('sfx', index)}</div>"
            )

        if result is not None:
            quality = result.metadata.get("audio_quality")
            quality = quality if isinstance(quality, dict) else {}
            mix_summary = result.metadata.get("mix_plan")
            mix_summary = mix_summary if isinstance(mix_summary, dict) else {}
            integrated_lufs = quality.get("integrated_lufs")
            true_peak_db = quality.get("true_peak_db")
            lufs_text = (
                f"{float(integrated_lufs):.1f} LUFS" if integrated_lufs is not None else "待测量"
            )
            peak_text = f"{float(true_peak_db):.1f} dBTP" if true_peak_db is not None else "待测量"
            render_ok = bool(quality.get("render_integrity_passed", False))
            parts.append(
                f"<h4 style='margin:12px 0 5px; color:{qcolor_hex('text.secondary')}'>母带与交付证据</h4>"
                f"<div style='padding:7px 9px; border:1px solid {qcolor_rgba('border.default', 0.18)}; "
                f"border-radius:7px; color:{qcolor_hex('text.body')}'>"
                f"目标响度 {mix_plan.get('target_lufs', -16.0):g} LUFS · 实测 {lufs_text}<br>"
                f"真峰值目标 {mix_plan.get('true_peak_db', -1.5):g} dBTP · 实测 {peak_text}<br>"
                f"多轨渲染 {'通过' if render_ok else '待检查'} · "
                f"事件 {int(mix_summary.get('event_count', 0) or 0)} 项 · "
                f"渲染器 {html_lib.escape(str(mix_summary.get('renderer_plugin_id') or '未记录'))}</div>"
            )
        parts.append("</div>")
        update_browser_html(self._mix_manifest_browser, "".join(parts))

    def _create_settings_tab(self) -> QWidget:
        """Create a stable single-column TTS settings flow grouped by intent.

        Provider forms contain credentials and long labels, so they must not
        live in a horizontally draggable split pane.  The vertical hierarchy
        mirrors the Fire settings page: category heading, then its related
        collapsible section.
        """
        # Outer wrapper with an explicitly themed scroll surface.
        outer = QWidget()
        outer.setObjectName("voiceStudioSettingsTab")
        outer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setObjectName("voiceStudioSettingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        self._settings_scroll = scroll
        scroll.verticalScrollBar().valueChanged.connect(lambda _value: self.ui_state_changed.emit())
        # Lazy restoration can run before this offscreen tab receives a real
        # viewport.  Retry only when Qt later publishes a usable scroll range.
        scroll.verticalScrollBar().rangeChanged.connect(
            lambda _minimum, _maximum: self._apply_restored_settings_scroll()
        )
        outer_layout.addWidget(scroll)

        widget = QWidget()
        widget.setObjectName("voiceStudioSettingsContent")
        widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(10, 10, 10, 14)
        layout.setSpacing(10)
        scroll.setWidget(widget)
        self._apply_restored_settings_scroll()

        def add_category(title: str, subtitle: str) -> None:
            heading = SectionHeading(title, subtitle)
            heading.setObjectName("voiceSettingsCategory")
            layout.addWidget(heading)

        # ── Section 1: shared settings ─────────────────────────────
        basic_section = CollapsibleSection(
            "通用合成策略",
            expanded=False,
            persist_key="voice_studio/settings/general_synthesis",
        )
        basic_layout = basic_section.body_layout
        basic_layout.setSpacing(8)
        add_setting_group_description(
            basic_layout,
            "所有 TTS 平台共用的合成行为与默认叙述参数。平台专属模型、密钥和服务地址请在下一节设置。",
        )

        # Speed defaults
        speed_row, self._speed_combo = make_combo_setting(
            "默认语速",
            "未设置片段语速时使用的项目级倍率，试听与正式合成都生效",
            [f"{value / 10:.1f}" for value in range(5, 21)],
            current=f"{self._settings.tts_default_speed:.1f}",
        )
        basic_layout.addWidget(speed_row)

        # Auto trigger
        auto_trigger_row = SettingRow("自动触发", "章节完成后自动触发 TTS 合成")
        self._auto_trigger_check = QCheckBox()
        self._auto_trigger_check.setChecked(self._settings.tts_auto_trigger_after_chapter)
        auto_trigger_row.set_input(self._auto_trigger_check)
        basic_layout.addWidget(auto_trigger_row)

        # Narrator voice ID
        narrator_row, self._narrator_voice_input = make_line_setting(
            "旁白音色 ID",
            "旁白使用的默认音色 ID（留空则使用系统默认）",
            self._settings.tts_narrator_voice_id,
            placeholder="例如: male-qingnian-zhiye",
        )
        basic_layout.addWidget(narrator_row)

        # ── Section 2: platform-specific settings ──────────────────
        initial_provider = self._get_current_provider()
        api_section = CollapsibleSection(
            f"{provider_ui_spec(initial_provider).label} — 连接与模型",
            expanded=True,
            persist_key="voice_studio/settings/platform_connection",
        )
        self._platform_settings_section = api_section
        api_layout = api_section.body_layout
        api_layout.setSpacing(8)
        self._settings_context_hint = add_setting_group_description(
            api_layout,
            f"当前平台：{provider_ui_spec(self._get_current_provider()).label}。只显示该平台专属的模型、地址、凭据与推理模式。",
        )
        self._platform_capability_summary = QLabel()
        self._platform_capability_summary.setObjectName("voicePlatformCapabilitySummary")
        self._platform_capability_summary.setWordWrap(True)
        self._platform_capability_summary.setText(
            self._provider_capability_summary(self._get_current_provider())
        )
        api_layout.addWidget(self._platform_capability_summary)

        initial_models = provider_models(initial_provider, self._settings)
        initial_models = self._available_provider_models(initial_provider, initial_models)
        configured_model = self._configured_model_for_provider(initial_provider)
        selected_model = (
            configured_model
            if configured_model in initial_models
            else (initial_models[0] if initial_models else "")
        )
        model_row, self._model_combo = make_combo_setting(
            "当前平台模型",
            "切换顶部 TTS 平台后，该模型列表和保存目标会同步切换。",
            initial_models,
            current=selected_model,
        )
        self._model_combo.currentTextChanged.connect(self._on_provider_model_changed)
        self._on_provider_model_changed(selected_model)
        api_layout.addWidget(model_row)

        # MiniMax
        self._minimax_key_row, self._minimax_key_input = make_line_setting(
            "MiniMax API Key",
            "语音合成与克隆所需的服务密钥",
            self._settings.tts_minimax_api_key,
            placeholder="输入 MiniMax API Key",
            secret=True,
        )
        api_layout.addWidget(self._minimax_key_row)

        self._minimax_url_row, self._minimax_url_input = make_line_setting(
            "MiniMax API 地址",
            "默认官方全球端点；可按账号区域或低首包延迟需求改为官方兼容地址",
            self._settings.tts_minimax_base_url,
            placeholder="https://api.minimax.io/v1",
        )
        api_layout.addWidget(self._minimax_url_row)

        # MiniMax Group ID
        self._minimax_group_row, self._minimax_group_input = make_line_setting(
            "MiniMax Group ID",
            "仅供旧版账号兼容；当前 MiniMax TTS v2 与音色接口通常无需填写",
            self._settings.tts_minimax_group_id,
            placeholder="输入 MiniMax Group ID",
        )
        api_layout.addWidget(self._minimax_group_row)
        self._minimax_bitrate_row, self._minimax_bitrate_combo = make_combo_setting(
            "MiniMax MP3 比特率",
            "成品建议 128 kbps 或 256 kbps；仅 MP3 输出生效",
            ["32000", "64000", "128000", "256000"],
            current=str(self._settings.tts_minimax_bitrate),
            item_labels=["32 kbps", "64 kbps", "128 kbps（推荐）", "256 kbps"],
        )
        api_layout.addWidget(self._minimax_bitrate_row)
        self._minimax_channel_row, self._minimax_channel_combo = make_combo_setting(
            "MiniMax 声道",
            "人声素材推荐单声道；声场与立体声在后处理统一完成",
            ["1", "2"],
            current=str(self._settings.tts_minimax_channel),
            item_labels=["单声道（推荐）", "双声道"],
        )
        api_layout.addWidget(self._minimax_channel_row)
        self._minimax_language_row, self._minimax_language_combo = make_combo_setting(
            "MiniMax 语言增强",
            "auto 会自动识别；粤语或外语作品可显式指定",
            ["auto", "Chinese", "Chinese,Yue", "English"],
            current=self._settings.tts_minimax_language_boost,
            item_labels=["自动识别（推荐）", "普通话", "粤语", "英语"],
        )
        api_layout.addWidget(self._minimax_language_row)

        # DashScope
        self._dashscope_key_row, self._dashscope_key_input = make_line_setting(
            "DashScope API Key",
            "阿里云百炼 / 通义千问 TTS 服务密钥",
            self._settings.tts_dashscope_api_key,
            placeholder="输入阿里云百炼 API Key",
            secret=True,
        )
        api_layout.addWidget(self._dashscope_key_row)

        # DashScope model
        self._dashscope_model_row, self._dashscope_model_combo = make_combo_setting(
            "DashScope 模型",
            "阿里云百炼 TTS 模型版本",
            [
                "qwen-audio-3.0-tts-plus",
                "qwen-audio-3.0-tts-flash",
                "cosyvoice-v3.5-plus",
                "cosyvoice-v3.5-flash",
                "qwen3-tts-flash",
                "qwen3-tts-instruct-flash",
                "qwen3-tts-vc-2026-01-22",
                "qwen3-tts-vd-2026-01-26",
            ],
            current=self._settings.tts_dashscope_model,
        )
        api_layout.addWidget(self._dashscope_model_row)
        self._dashscope_preview_row, self._dashscope_preview_combo = make_combo_setting(
            "百炼人声试听模型",
            "留空时随正式模型；已绑定音色始终使用其所属模型",
            [
                "",
                "qwen-audio-3.0-tts-plus",
                "qwen-audio-3.0-tts-flash",
                "cosyvoice-v3.5-plus",
                "cosyvoice-v3.5-flash",
                "qwen3-tts-flash",
                "qwen3-tts-instruct-flash",
            ],
            current=self._settings.tts_dashscope_preview_model,
            item_labels=[
                "随正式模型（推荐）",
                "Qwen Audio 3.0 Plus",
                "Qwen Audio 3.0 Flash",
                "CosyVoice 3.5 Plus",
                "CosyVoice 3.5 Flash",
                "Qwen3 TTS Flash",
                "Qwen3 TTS Instruct Flash",
            ],
        )
        api_layout.addWidget(self._dashscope_preview_row)
        self._dashscope_clone_row, self._dashscope_clone_combo = make_combo_setting(
            "百炼声音复刻模型",
            "复刻音色与目标模型强绑定；Qwen3 VC 可直接上传本地参考音频",
            [
                "qwen-audio-3.0-tts-plus",
                "qwen-audio-3.0-tts-flash",
                "cosyvoice-v3.5-plus",
                "cosyvoice-v3.5-flash",
                "qwen3-tts-vc-2026-01-22",
            ],
            current=self._settings.tts_dashscope_voice_clone_model,
        )
        api_layout.addWidget(self._dashscope_clone_row)
        self._dashscope_design_row, self._dashscope_design_combo = make_combo_setting(
            "百炼声音设计模型",
            "CosyVoice 3.5 与 Qwen3 VD 均支持从文字描述创建并试听音色",
            [
                "cosyvoice-v3.5-plus",
                "cosyvoice-v3.5-flash",
                "qwen3-tts-vd-2026-01-26",
            ],
            current=self._settings.tts_dashscope_voice_design_model,
        )
        api_layout.addWidget(self._dashscope_design_row)
        self._dashscope_url_row, self._dashscope_url_input = make_line_setting(
            "百炼 API 地址",
            "生产环境优先使用与 API Key 同地域的 Workspace 专属 /api/v1 地址",
            self._settings.tts_dashscope_base_url,
            placeholder="https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1",
        )
        api_layout.addWidget(self._dashscope_url_row)
        self._dashscope_instruction_row, self._dashscope_instruction_combo = make_combo_setting(
            "Qwen3 指令优化",
            "成品建议开启；严格 A/B 可关闭以保持指令原样",
            ["true", "false"],
            current=str(self._settings.tts_dashscope_optimize_instructions).lower(),
        )
        api_layout.addWidget(self._dashscope_instruction_row)

        # Tencent
        self._tencent_id_row, self._tencent_id_input = make_line_setting(
            "腾讯云 SecretId",
            "腾讯云语音合成账号标识",
            self._settings.tts_tencent_secret_id,
            placeholder="输入腾讯云 SecretId",
            secret=True,
        )
        api_layout.addWidget(self._tencent_id_row)

        self._tencent_key_row, self._tencent_key_input = make_line_setting(
            "腾讯云 SecretKey",
            "腾讯云语音合成加密密钥",
            self._settings.tts_tencent_secret_key,
            placeholder="输入腾讯云 SecretKey",
            secret=True,
        )
        api_layout.addWidget(self._tencent_key_row)

        # Volcengine Ark Agent Plan / Doubao Seed TTS 2.0
        self._volcengine_key_row, self._volcengine_key_input = make_line_setting(
            "Agent Plan 专属 API Key",
            "与方舟 Agent Plan 文字模型共用，从个人版套餐控制台获取",
            self._settings.volcengine_ark_api_key,
            placeholder="输入方舟 Agent Plan 专属 API Key",
            secret=True,
        )
        api_layout.addWidget(self._volcengine_key_row)
        self._volcengine_url_row, self._volcengine_url_input = make_line_setting(
            "Agent Plan TTS 地址",
            "新版 Agent Plan HTTP Chunked 单向接口",
            self._settings.tts_volcengine_base_url,
            placeholder="https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional",
        )
        api_layout.addWidget(self._volcengine_url_row)

        # Xiaomi MiMo V2.5 TTS
        self._mimo_key_row, self._mimo_key_input = make_line_setting(
            "MiMo API Key",
            "MiMo 开放平台普通 API Key；不使用 Token Plan 专用地址",
            self._settings.tts_mimo_api_key,
            placeholder="输入 MiMo API Key",
            secret=True,
        )
        api_layout.addWidget(self._mimo_key_row)
        self._mimo_url_row, self._mimo_url_input = make_line_setting(
            "MiMo TTS 地址",
            "MiMo V2.5 TTS 的 OpenAI 兼容 API 地址",
            self._settings.tts_mimo_base_url,
            placeholder="https://api.xiaomimimo.com/v1",
        )
        api_layout.addWidget(self._mimo_url_row)

        # Local base URL
        self._local_url_row, self._local_url_input = make_line_setting(
            "本地服务地址",
            "本地 TTS 服务地址；基础兼容 /audio/speech，可选扩展 /capabilities 与 /voices",
            self._settings.tts_local_base_url,
            placeholder="http://localhost:8000/v1",
        )
        api_layout.addWidget(self._local_url_row)

        self._local_key_row, self._local_key_input = make_line_setting(
            "本地服务 API Key",
            "反向代理或局域网服务需要鉴权时填写，可留空",
            self._settings.tts_local_api_key,
            placeholder="可选",
            secret=True,
        )
        api_layout.addWidget(self._local_key_row)

        self._local_model_row, self._local_model_input = make_line_setting(
            "本地模型 ID",
            "支持服务端自定义；例如 Qwen3-TTS、CosyVoice、Fish Speech、GPT-SoVITS",
            self._settings.tts_local_model,
            placeholder="default",
        )
        api_layout.addWidget(self._local_model_row)

        self._qwen3_url_row, self._qwen3_url_input = make_line_setting(
            "Qwen3-TTS 服务地址",
            "独立 Qwen3-TTS sidecar 地址；正式合成使用 1.7B，旁白/角色试听自动使用 0.6B。",
            self._settings.tts_qwen3_base_url,
            placeholder="http://127.0.0.1:8011/v1",
        )
        api_layout.addWidget(self._qwen3_url_row)
        self._qwen3_key_row, self._qwen3_key_input = make_line_setting(
            "Qwen3-TTS Sidecar Token",
            "仅 sidecar 不在本机回环地址或已启用 Bearer Token 时填写。",
            self._settings.tts_qwen3_api_key,
            placeholder="可选",
            secret=True,
        )
        api_layout.addWidget(self._qwen3_key_row)
        self._qwen3_preview_row, self._qwen3_preview_combo = make_combo_setting(
            "快速试听模型",
            "角色/旁白点击试听专用；不会降低章节正式成品的人声规格。",
            ["Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"],
            current=self._settings.tts_qwen3_preview_model,
        )
        api_layout.addWidget(self._qwen3_preview_row)
        self._qwen3_design_row, self._qwen3_design_combo = make_combo_setting(
            "品牌音色设计模型",
            "设计后会由 sidecar 生成参考音频，并用 Base 固化为长期可复用角色音色。",
            ["Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"],
            current=self._settings.tts_qwen3_design_model,
        )
        api_layout.addWidget(self._qwen3_design_row)
        self._qwen3_clone_row, self._qwen3_clone_combo = make_combo_setting(
            "授权克隆模型",
            "仅在确认拥有说话人授权后使用；逐字转写可获得更稳定的 Base 克隆效果。",
            ["Qwen/Qwen3-TTS-12Hz-1.7B-Base"],
            current=self._settings.tts_qwen3_clone_model,
        )
        api_layout.addWidget(self._qwen3_clone_row)

        self._cosyvoice_url_row, self._cosyvoice_url_input = make_line_setting(
            "CosyVoice 服务地址",
            "官方 CosyVoice FastAPI 地址；使用 /inference_sft、zero_shot、cross_lingual、instruct2。",
            self._settings.tts_cosyvoice_base_url,
            placeholder="http://127.0.0.1:50000",
        )
        api_layout.addWidget(self._cosyvoice_url_row)
        self._cosyvoice_mode_row, self._cosyvoice_mode_combo = make_combo_setting(
            "CosyVoice 合成模式",
            "克隆音色默认使用 instruct2，将角色特质作为表达指令；zero_shot 需要正确的参考音频转写。",
            ["instruct2", "cross_lingual", "zero_shot", "sft"],
            current=self._settings.tts_cosyvoice_mode,
        )
        api_layout.addWidget(self._cosyvoice_mode_row)
        self._openvoice_checkpoint_row, self._openvoice_checkpoint_input = make_line_setting(
            "OpenVoice V2 权重目录",
            "包含 converter/ 与 base_speakers/ 的 checkpoints_v2 目录；首次试听会加载 OpenVoice 与 MeloTTS。",
            self._settings.tts_openvoice_checkpoint_dir,
            placeholder=self._settings.tts_openvoice_checkpoint_dir,
        )
        api_layout.addWidget(self._openvoice_checkpoint_row)
        self._openvoice_device_row, self._openvoice_device_combo = make_combo_setting(
            "OpenVoice 推理设备",
            "auto 自动选择 CUDA；无 GPU 时请选择 cpu。",
            ["auto", "cpu", "cuda"],
            current=self._settings.tts_openvoice_device,
        )
        api_layout.addWidget(self._openvoice_device_row)

        # Keep every settings group in one scrollable column.  This avoids
        # clipping when a provider form changes its visible fields.
        add_category(
            "本机模型中心",
            "下载、校验、删除所有项目共享的本地模型，并检查独立 sidecar 运行时。",
        )
        layout.addWidget(self._build_audio_model_center_section())

        add_category(
            "质量与路由",
            "以成片目标组织模型组合，并保留每个平台的原生字段和专属运行参数。",
        )
        layout.addWidget(self._build_audio_planning_section())

        add_category(
            "当前平台",
            "配置本次配音所用平台的模型、连接地址、凭据与推理选项。",
        )
        layout.addWidget(api_section)

        add_category(
            "项目配音策略",
            "配置所有 TTS 平台共用的叙述节奏与章节自动合成行为。",
        )
        layout.addWidget(basic_section)

        # ── Section 3: shared sound assets ─────────────────────────
        add_category(
            "声音设计",
            "管理环境声、短音效与背景音乐的生成模型、目录与复用策略。",
        )
        layout.addWidget(self._build_sound_generation_section())

        # ── Section 4: shared advanced parameters ──────────────────
        add_category(
            "输出与运行",
            "配置并发、重试、文件格式、采样率与克隆音色的生命周期。",
        )
        advanced_section = CollapsibleSection(
            "合成性能与输出",
            expanded=False,
            persist_key="voice_studio/settings/synthesis_output",
        )
        adv_layout = advanced_section.body_layout
        adv_layout.setSpacing(8)
        add_setting_group_description(adv_layout, "合成性能、输出格式与音色有效期等调试参数")

        pipeline_concurrency_row = SettingRow(
            "后台整章配音并发",
            "同时运行的整章配音流水线数。小说任务走独立通道；默认 1 让多章配音排队，避免内存峰值。",
        )
        self._background_pipeline_spin = QSpinBox()
        self._background_pipeline_spin.setRange(1, 3)
        self._background_pipeline_spin.setValue(self._settings.tts_background_pipeline_concurrency)
        pipeline_concurrency_row.set_input(self._background_pipeline_spin)
        adv_layout.addWidget(pipeline_concurrency_row)

        script_batch_row = SettingRow(
            "配音脚本分批并发",
            "长章脚本拆批后同时调用文本模型的上限；默认 2，单批失败会独立降级。",
        )
        self._script_batch_concurrency_spin = QSpinBox()
        self._script_batch_concurrency_spin.setRange(1, 4)
        self._script_batch_concurrency_spin.setValue(
            self._settings.tts_script_max_concurrent_batches
        )
        script_batch_row.set_input(self._script_batch_concurrency_spin)
        adv_layout.addWidget(script_batch_row)

        # Max concurrent synthesis
        concurrent_row = SettingRow(
            "最大并发合成数",
            "同时进行的 TTS 合成请求数（越大越快，但可能触发限流）",
        )
        self._concurrent_spin = QSpinBox()
        self._concurrent_spin.setRange(1, 16)
        self._concurrent_spin.setValue(self._settings.tts_max_concurrent_synthesis)
        concurrent_row.set_input(self._concurrent_spin)
        adv_layout.addWidget(concurrent_row)

        rate_row = SettingRow(
            "远程合成 RPM 上限",
            "每分钟最多向云端 TTS 发起的请求数（包含重试）；会自动均匀排队，避免触发服务商限流。",
        )
        self._rate_limit_spin = QSpinBox()
        self._rate_limit_spin.setRange(1, 600)
        self._rate_limit_spin.setValue(self._settings.tts_synthesis_requests_per_minute)
        rate_row.set_input(self._rate_limit_spin)
        adv_layout.addWidget(rate_row)

        cooldown_row = SettingRow(
            "限流冷却秒数",
            "服务商返回限流后，暂停整条远程合成队列的时间；通常保留 60 秒。",
        )
        self._rate_limit_cooldown_spin = QSpinBox()
        self._rate_limit_cooldown_spin.setRange(5, 600)
        self._rate_limit_cooldown_spin.setValue(self._settings.tts_synthesis_rate_limit_cooldown_s)
        cooldown_row.set_input(self._rate_limit_cooldown_spin)
        adv_layout.addWidget(cooldown_row)

        # Retry limit
        retry_row = SettingRow("单段重试次数", "单个片段合成失败时的最大重试次数")
        self._retry_spin = QSpinBox()
        self._retry_spin.setRange(0, 10)
        self._retry_spin.setValue(self._settings.tts_synthesis_retry_limit)
        retry_row.set_input(self._retry_spin)
        adv_layout.addWidget(retry_row)

        # Output format
        format_row, self._format_combo = make_combo_setting(
            "输出格式",
            "合成音频的输出文件格式",
            ["mp3", "wav", "flac", "pcm"],
            current=self._settings.tts_output_format,
        )
        adv_layout.addWidget(format_row)

        # Sample rate
        sample_row, self._sample_combo = make_combo_setting(
            "采样率",
            "合成音频的采样率（Hz）",
            ["16000", "22050", "24000", "32000", "44100", "48000"],
            current=str(self._settings.tts_sample_rate),
        )
        adv_layout.addWidget(sample_row)

        # Voice clone TTL
        ttl_row = SettingRow("克隆音色有效期", "克隆音色的有效天数，过期后需重新克隆")
        self._ttl_spin = QSpinBox()
        self._ttl_spin.setRange(1, 30)
        self._ttl_spin.setValue(self._settings.tts_voice_clone_ttl_days)
        ttl_row.set_input(self._ttl_spin)
        adv_layout.addWidget(ttl_row)

        # Parallel voice clone
        parallel_row = SettingRow("并行克隆", "Init 阶段是否并行执行多个角色的音色克隆")
        self._parallel_clone_check = QCheckBox()
        self._parallel_clone_check.setChecked(self._settings.tts_parallel_voice_clone)
        parallel_row.set_input(self._parallel_clone_check)
        adv_layout.addWidget(parallel_row)

        layout.addWidget(advanced_section)

        # ── Section 4: LLM routing for TTS analysis tasks ───────────
        add_category(
            "文本智能",
            "为配音脚本生成、说话人复核、角色音色复核与旁白画像选择文本模型；保存只影响后续任务。",
        )

        voice_review_section = CollapsibleSection(
            "角色音色 LLM 复核",
            expanded=False,
            persist_key="voice_studio/settings/voice_match_llm",
        )
        voice_review_layout = voice_review_section.body_layout
        voice_review_layout.setSpacing(8)
        add_setting_group_description(
            voice_review_layout,
            "只处理规则和向量匹配仍不够确定的角色。LLM 会在通过性别、年龄、"
            "供应商与有效期硬约束的候选中比较；高置信度时可自动改配，否则保留对比试听清单。默认关闭。",
        )
        voice_review_enabled_row = SettingRow(
            "启用低置信度复核",
            "组建团队后，批量复核低置信度的音色库或系统音色匹配。",
        )
        self._voice_llm_adjudication_check = QCheckBox()
        self._voice_llm_adjudication_check.setChecked(
            self._settings.tts_voice_llm_adjudication_enabled
        )
        voice_review_enabled_row.set_input(self._voice_llm_adjudication_check)
        voice_review_layout.addWidget(voice_review_enabled_row)

        voice_review_threshold_row = SettingRow(
            "触发阈值",
            "画像匹配分低于此值，或已有风险提示时才进入 LLM 复核。",
        )
        self._voice_llm_min_score_spin = QDoubleSpinBox()
        self._voice_llm_min_score_spin.setRange(0.0, 1.0)
        self._voice_llm_min_score_spin.setDecimals(2)
        self._voice_llm_min_score_spin.setSingleStep(0.05)
        self._voice_llm_min_score_spin.setValue(
            self._settings.tts_voice_llm_adjudication_min_match_score
        )
        voice_review_threshold_row.set_input(self._voice_llm_min_score_spin)
        voice_review_layout.addWidget(voice_review_threshold_row)

        voice_review_candidates_row = SettingRow(
            "单次最大复核角色数",
            "按最低匹配分优先，限制每次建组时的模型费用。",
        )
        self._voice_llm_max_candidates_spin = QSpinBox()
        self._voice_llm_max_candidates_spin.setRange(1, 12)
        self._voice_llm_max_candidates_spin.setValue(
            self._settings.tts_voice_llm_adjudication_max_candidates
        )
        voice_review_candidates_row.set_input(self._voice_llm_max_candidates_spin)
        voice_review_layout.addWidget(voice_review_candidates_row)

        voice_review_choices_row = SettingRow(
            "每个角色候选数",
            "从音色库和平台目录中选取通过硬约束的候选供 LLM 比较。",
        )
        self._voice_llm_choices_spin = QSpinBox()
        self._voice_llm_choices_spin.setRange(2, 8)
        self._voice_llm_choices_spin.setValue(
            self._settings.tts_voice_llm_adjudication_choices_per_character
        )
        voice_review_choices_row.set_input(self._voice_llm_choices_spin)
        voice_review_layout.addWidget(voice_review_choices_row)

        voice_review_auto_select_row = SettingRow(
            "自动改配置信度",
            "达到此阈值且候选未被其他角色占用时，才允许 LLM 自动替换音色。",
        )
        self._voice_llm_auto_select_spin = QDoubleSpinBox()
        self._voice_llm_auto_select_spin.setRange(0.5, 1.0)
        self._voice_llm_auto_select_spin.setDecimals(2)
        self._voice_llm_auto_select_spin.setSingleStep(0.05)
        self._voice_llm_auto_select_spin.setValue(
            self._settings.tts_voice_llm_adjudication_auto_select_score
        )
        voice_review_auto_select_row.set_input(self._voice_llm_auto_select_spin)
        voice_review_layout.addWidget(voice_review_auto_select_row)

        voice_review_tokens_row = SettingRow(
            "复核输出预算",
            "每批复核可使用的最大输出 token；建议保持简短，以说明和试听建议为主。",
        )
        self._voice_llm_max_tokens_spin = QSpinBox()
        self._voice_llm_max_tokens_spin.setRange(256, 4096)
        self._voice_llm_max_tokens_spin.setSingleStep(128)
        self._voice_llm_max_tokens_spin.setValue(
            self._settings.tts_voice_llm_adjudication_max_output_tokens
        )
        voice_review_tokens_row.set_input(self._voice_llm_max_tokens_spin)
        voice_review_layout.addWidget(voice_review_tokens_row)

        self._voice_llm_adjudication_controls = (
            self._voice_llm_min_score_spin,
            self._voice_llm_max_candidates_spin,
            self._voice_llm_choices_spin,
            self._voice_llm_auto_select_spin,
            self._voice_llm_max_tokens_spin,
        )
        self._voice_llm_adjudication_check.toggled.connect(
            self._sync_voice_llm_adjudication_controls
        )
        self._sync_voice_llm_adjudication_controls(self._voice_llm_adjudication_check.isChecked())
        layout.addWidget(voice_review_section)

        routing_section = CollapsibleSection(
            "TTS 文本模型路由",
            expanded=False,
            persist_key="voice_studio/settings/text_model_routes",
        )
        routing_layout = routing_section.body_layout
        routing_layout.setSpacing(8)
        add_setting_group_description(
            routing_layout,
            "脚本生成、受限说话人裁决、配音专业审校和旁白画像使用模型档案；"
            "保存后只影响新任务，不会中断正在运行的合成。",
        )
        script_review_enabled_row = SettingRow(
            "启用配音专业审校",
            "按可演性、说话人风险、情绪意图和 TTS 稳定性复核；小说去痕仅作为候选信号。",
        )
        self._script_llm_review_check = QCheckBox()
        self._script_llm_review_check.setChecked(self._settings.tts_script_llm_review_enabled)
        script_review_enabled_row.set_input(self._script_llm_review_check)
        routing_layout.addWidget(script_review_enabled_row)

        script_review_tokens_row = SettingRow(
            "专业审校输出预算",
            "整章仅返回问题与安全修复；章节较长或分段较多时可适当提高。",
        )
        self._script_llm_review_tokens_spin = QSpinBox()
        self._script_llm_review_tokens_spin.setRange(2048, 32768)
        self._script_llm_review_tokens_spin.setSingleStep(1024)
        self._script_llm_review_tokens_spin.setValue(
            self._settings.tts_script_llm_review_max_output_tokens
        )
        self._script_llm_review_tokens_spin.setEnabled(self._script_llm_review_check.isChecked())
        self._script_llm_review_check.toggled.connect(
            self._script_llm_review_tokens_spin.setEnabled
        )
        script_review_tokens_row.set_input(self._script_llm_review_tokens_spin)
        routing_layout.addWidget(script_review_tokens_row)

        self._tts_route_rows: dict[str, _TaskRouteRow] = {}
        route_config = load_or_import_profiles(self._settings)
        # Build profile choices: all non-embedding profiles, locked ones marked can_route=False
        route_profiles: list[tuple[str, str, bool]] = []
        for p in route_config.profiles:
            if is_embedding_model(p.provider, p.model_id):
                continue
            if not p.is_key_configured:
                route_profiles.append((p.profile_id, f"\U0001f512 {p.display_name}", False))
            else:
                route_profiles.append((p.profile_id, p.display_name, True))
        # Map each TTS routing task to its temperature setting attribute so the
        # route row exposes a temperature spin (mirrors the 火候 settings page).
        _TTS_ROUTE_TEMPERATURE_MAP: dict[str, str] = {
            TaskType.TTS_GENERATE_DUBBING_SCRIPT.value: "tts_script_generation_temperature",
            TaskType.TTS_ADJUDICATE_SCRIPT_SEGMENTS.value: "tts_review_adjudication_temperature",
            TaskType.TTS_REVIEW_DUBBING_SCRIPT.value: "tts_review_adjudication_temperature",
            TaskType.TTS_ADJUDICATE_VOICE_MATCH.value: "tts_review_adjudication_temperature",
            TaskType.TTS_BUILD_NARRATOR_PROFILE.value: "tts_narrator_profile_temperature",
            TaskType.TTS_SOUND_DESIGN.value: "tts_sound_design_temperature",
        }
        for task_type, label, hint in (
            (
                TaskType.TTS_GENERATE_DUBBING_SCRIPT,
                "配音脚本生成",
                "将章节正文转换为分段、角色、情绪和声音提示的结构化脚本。",
            ),
            (
                TaskType.TTS_ADJUDICATE_SCRIPT_SEGMENTS,
                "说话人证据复核",
                "只复核正文规则无法确定的对白归属，不改写台词。",
            ),
            (
                TaskType.TTS_REVIEW_DUBBING_SCRIPT,
                "配音脚本专业审校",
                "独立检查可演性、声音角色、情绪意图和合成稳定性；不复用小说审查结论。",
            ),
            (
                TaskType.TTS_ADJUDICATE_VOICE_MATCH,
                "角色音色复核",
                "在硬约束候选白名单内比较音色；高置信度时可改配，否则要求对比试听。",
            ),
            (
                TaskType.TTS_BUILD_NARRATOR_PROFILE,
                "旁白画像构建",
                "分析作品气质并生成旁白语速、叙述距离与情绪范围。",
            ),
            (
                TaskType.TTS_SOUND_DESIGN,
                "声音设计提取",
                "从定稿脚本中提取 SFX/BGM/环境声/转场设计，支持库优先复用。",
            ),
        ):
            task_key = task_type.value
            route = route_config.routes.get(task_key)
            fallback_routes = route_config.fallback_routes.get(task_key, [])
            temp_attr = _TTS_ROUTE_TEMPERATURE_MAP.get(task_key)
            temp_value = (
                float(getattr(self._settings, temp_attr)) if temp_attr else None
            )
            route_row = _TaskRouteRow(
                task_key,
                label,
                hint,
                route_profiles,
                route,
                fallback_routes,
                show_multi_turn=False,
                temperature=temp_value,
                profile_configs=list(route_config.profiles),
            )
            if temp_attr and route_row.temperature_spin is not None:
                route_row.temperature_spin.setProperty("setting_attr", temp_attr)
            self._tts_route_rows[task_key] = route_row
            routing_layout.addWidget(route_row)

        layout.addWidget(routing_section)

        layout.addStretch()

        # Initial visibility
        self._update_api_key_visibility()

        return outer

    # ─── Public API ────────────────────────────────────────────────────────

    def get_top_bar_widget(self) -> QWidget | None:
        """Return metrics, provider switch and project selector for the window header."""
        return self._top_bar_context

    def _current_audio_automation_mode(self) -> AudioAutomationMode:
        try:
            return AudioAutomationMode(self._audio_automation_mode)
        except ValueError:
            return AudioAutomationMode.ASSISTED

    def _on_audio_automation_mode_changed(self, mode: str) -> None:
        """Persist the current project's authority boundary and refresh actions."""
        try:
            resolved = AudioAutomationMode(mode)
        except ValueError:
            resolved = AudioAutomationMode.ASSISTED
        self._audio_automation_mode = resolved.value
        if self._project_id:
            self._audio_automation_modes[self._project_id] = resolved.value
        self._sync_audio_automation_mode_ui()
        self._refresh_workflow_controls()
        self.ui_state_changed.emit()

    def _sync_audio_automation_mode_ui(self) -> None:
        """Keep selector, primary action and explanation on one effective mode."""
        mode = self._current_audio_automation_mode()
        selector = getattr(self, "_audio_automation_selector", None)
        if selector is not None and selector.current_mode() != mode:
            selector.set_mode(mode)
        full_button = getattr(self, "_full_pipeline_btn", None)
        if full_button is None:
            return
        labels = {
            AudioAutomationMode.MANUAL: "按现状成片",
            AudioAutomationMode.ASSISTED: "AI 伴随推进",
            AudioAutomationMode.AUTONOMOUS: "AI 自主成片",
        }
        tooltips = {
            AudioAutomationMode.MANUAL: (
                "不自动创建配音团队、脚本或声音素材；仅使用已准备、已审核的内容。"
            ),
            AudioAutomationMode.ASSISTED: (
                "AI 补齐设计和声音候选，生成资产需试听批准后才进入正式混音。"
            ),
            AudioAutomationMode.AUTONOMOUS: (
                "AI 自动完成设计、生成、批准、混音与质检；交付门失败时会明确停止。"
            ),
        }
        full_button.setText(labels[mode])
        full_button.setToolTip(tooltips[mode])
        phase_badge = getattr(self, "_automation_progress_badge", None)
        if phase_badge is not None and getattr(self, "_tts_operation", None) is None:
            phase_badge.setText(
                {
                    AudioAutomationMode.MANUAL: "全人工 · 待命",
                    AudioAutomationMode.ASSISTED: "AI 伴随 · 待命",
                    AudioAutomationMode.AUTONOMOUS: "AI 自主 · 待命",
                }[mode]
            )
            phase_badge.set_tone("muted")

    def _update_audio_phase_progress(self, step: str, data: dict[str, Any]) -> None:
        """Show one concise production phase; technical detail stays in logs."""
        badge = getattr(self, "_automation_progress_badge", None)
        if badge is None:
            return
        mode_label = {
            AudioAutomationMode.MANUAL: "全人工",
            AudioAutomationMode.ASSISTED: "AI 伴随",
            AudioAutomationMode.AUTONOMOUS: "AI 自主",
        }[self._current_audio_automation_mode()]
        stage: tuple[int, str] | None = None
        if (
            step.startswith("tts_narrator")
            or step.startswith("tts_voice")
            or step.startswith("build_voice_team")
        ):
            stage = (1, "声音设计")
        elif (
            step.startswith("tts_script")
            or step.startswith("tts_speaker")
            or step.startswith("llm_stream")
        ):
            stage = (2, "脚本导演")
        elif step.startswith("tts_synthesis") or step.startswith("tts_alignment"):
            stage = (3, "人声与对齐")
        elif step.startswith("tts_sound"):
            stage = (4, "声场素材")
        elif step.startswith("tts_assembly"):
            stage = (5, "混音装配")
        elif step.startswith("tts_quality"):
            stage = (6, "交付质检")
        elif step == "tts_automation_mode":
            badge.setText(f"{mode_label} · 已锁定策略")
            badge.set_tone("warning")
            return
        if stage is None:
            return
        current, label = stage
        suffix = ""
        completed = data.get("completed", data.get("current"))
        total = data.get("total")
        if completed is not None and total is not None:
            suffix = f" · {completed}/{total}"
        badge.setText(f"{mode_label} · {current}/6 {label}{suffix}")
        phase_failed = step.endswith("failed") or (
            step == "tts_quality_complete" and data.get("passed") is False
        )
        badge.set_tone(
            "danger"
            if phase_failed
            else "success"
            if step.endswith(("complete", "completed", "done", "reused"))
            else "warning"
        )

    def _update_script_generation_progress(self, step: str, data: dict[str, Any]) -> None:
        """Render real script-production milestones without refreshing its browser."""
        chapter = int(data.get("chapter") or self._generating_chapter or 0)
        if chapter and chapter != self._active_chapter_number:
            return
        stream = self._script_streams.get(chapter) if chapter else None
        value: int | None = None
        phase = 1
        detail = "正在分析正文、出场角色与场景边界…"
        if step in {"tts_script_start", "tts_script_llm_call"}:
            value = 8 if step == "tts_script_start" else 12
        elif step == "tts_script_llm_batch_start":
            total = max(1, int(data.get("batch_count") or 1))
            if stream is not None:
                stream.batch_count = total
            completed = len(stream.completed_batches) if stream is not None else 0
            value = 12 + round(completed / total * 38)
            detail = f"分批改写配音脚本 · 已完成 {completed}/{total} 批"
        elif step == "tts_script_llm_batch_complete":
            total = max(1, int(data.get("batch_count") or 1))
            batch_index = int(data.get("batch_index") or 0)
            if stream is not None:
                stream.batch_count = total
                if batch_index:
                    stream.completed_batches.add(batch_index)
                completed = len(stream.completed_batches)
            else:
                completed = min(batch_index, total)
            value = 12 + round(completed / total * 38)
            detail = f"分批改写配音脚本 · 已完成 {completed}/{total} 批"
        elif step.startswith("tts_speaker") or step.startswith("tts_script_segment_adjudication"):
            phase = 2
            value = 58 if step.endswith("start") else 68
            candidates = int(data.get("candidate_count") or 0)
            detail = (
                f"正在用正文证据复核 {candidates} 个说话人歧义…"
                if candidates
                else "正在复核说话人与角色归属…"
            )
        elif step.startswith("tts_script_llm_review") or step == (
            "tts_script_professional_review_complete"
        ):
            phase = 3
            value = 78 if step.endswith("start") else 88
            detail = "正在审校情绪、语气、语速与事件音效…"
        elif step.startswith("tts_spoken_rewrite"):
            phase = 3
            if step == "tts_spoken_rewrite_batch_start":
                batch_idx = int(data.get("batch_index") or 1)
                batch_total = int(data.get("batch_count") or 1)
                value = 88 + round((batch_idx - 1) / max(1, batch_total) * 6)
                detail = f"正在口语改写 · 第 {batch_idx}/{batch_total} 批"
            elif step == "tts_spoken_rewrite_complete":
                rewritten = int(data.get("rewritten") or 0)
                total = int(data.get("total_candidates") or 0)
                value = 94
                detail = f"口语改写完成 · {rewritten}/{total} 段已改写"
            elif step == "tts_spoken_rewrite_batch_failed":
                value = 90
                detail = "口语改写部分批次失败，已安全回退"
            else:
                value = 89
                detail = "正在进行口语改写…"
        elif step in {"tts_script_done", "tts_script_persisted", "tts_script_reused"}:
            phase = 4
            value = 100 if step != "tts_script_done" else 96
            segment_count = int(data.get("segment_count") or 0)
            detail = f"配音脚本已就绪 · {segment_count} 段" if segment_count else "配音脚本已就绪"
        elif step == "llm_stream_start":
            value = 18
            detail = "正在接收真实脚本流；已完成段落会立即固定显示…"
        elif step == "llm_stream_end":
            total = max(1, stream.batch_count if stream is not None else 1)
            completed = len(stream.completed_batches) if stream is not None else 0
            value = 12 + round(completed / total * 38) + 2
            detail = "流式输出已接收，正在校验角色归属与文本保真…"
        if value is None:
            return
        if stream is not None:
            stream.progress_step = step
            merged = dict(stream.progress_data)
            merged.update(data)
            # tts_script_start 设置的全章 source_chars 不得被批次级
            # tts_script_llm_batch_start 的单批次 source_chars 覆盖，
            # 否则流式预览的字符膨胀检测会因分母缩小而误报。
            merged.setdefault("source_chars", 0)
            if "source_chars" in stream.progress_data and stream.progress_data["source_chars"]:
                merged["source_chars"] = stream.progress_data["source_chars"]
            stream.progress_data = merged
        bar = getattr(self, "_script_generation_bar", None)
        progress = getattr(self, "_script_generation_progress", None)
        badge = getattr(self, "_script_generation_badge", None)
        if bar is None or progress is None or badge is None:
            return
        badge.setText(f"脚本导演 {phase}/4")
        badge.set_tone("success" if value >= 100 else "warning")
        progress.setRange(0, 100)
        progress.setValue(value)
        progress.setFormat(detail)
        bar.setVisible(True)

    def _hide_script_generation_progress(self) -> None:
        bar = getattr(self, "_script_generation_bar", None)
        if bar is not None:
            bar.setVisible(False)

    def _update_stream_progress_from_delta(self, stream: Any, *, force: bool = False) -> None:
        """Update the progress bar during active LLM streaming.

        Shows real-time character reception count so the user sees activity
        during long batch LLM calls (30-120 s per batch) instead of a
        frozen "已完成 0/4 批" progress bar.

        Throttled: llm_stream_delta events arrive at ~50/s and each
        setValue()/setFormat() triggers a QProgressBar repaint.  Without
        throttling this saturates the Qt main thread and causes visible UI
        stutter.  Updates are limited to once per 400 ms unless *force* is
        set (batch complete / stream end).
        """
        import time as _time

        now = _time.monotonic()
        if not force and (now - self._stream_progress_last_update_at) < self._STREAM_PROGRESS_MIN_INTERVAL_S:
            return
        self._stream_progress_last_update_at = now

        bar = getattr(self, "_script_generation_bar", None)
        progress = getattr(self, "_script_generation_progress", None)
        badge = getattr(self, "_script_generation_badge", None)
        if bar is None or progress is None or badge is None:
            return
        if not stream.batch_count:
            return
        completed = len(stream.completed_batches)
        total = max(1, stream.batch_count)
        received_chars = len(stream.text)
        # Show character count and elapsed time as proof of activity.
        elapsed_s = int(now - self._batch_start_time) if self._batch_start_time else 0
        elapsed_label = f"{elapsed_s // 60}:{elapsed_s % 60:02d}" if elapsed_s >= 60 else f"{elapsed_s}s"
        detail = (
            f"分批改写配音脚本 · 已完成 {completed}/{total} 批"
            f" · 已接收 {received_chars:,} 字符 · {elapsed_label}"
        )
        # Compute a fine-grained progress within the current batch.
        # Base: completed batches contribute their full share.
        # Current batch: estimate progress from received chars vs source chars.
        source_chars = int(stream.progress_data.get("source_chars") or 0)
        batch_fraction = 0.0
        if source_chars > 0 and received_chars > 0:
            # Rough estimate: received chars / (source chars * expansion ratio).
            # Typical expansion is ~3-5x for structured JSON output.
            estimated_total = source_chars * 4
            batch_fraction = min(1.0, received_chars / max(1, estimated_total))
        value = 12 + round((completed + batch_fraction) / total * 38)
        value = min(value, 50)  # Cap at 50% until batch actually completes.
        progress.setRange(0, 100)
        progress.setValue(value)
        progress.setFormat(detail)
        bar.setVisible(True)

    def _check_stream_stall(self) -> None:
        """Periodic check: warn the user if the LLM stream appears stalled.

        Fires every 10s during active generation.  If no stream data has
        arrived for 90s, updates the status badge to inform the user that
        the connection may be interrupted and the system is waiting for the
        backend timeout to trigger recovery.
        """
        if self._generating_chapter is None:
            self._stream_stall_timer.stop()
            return
        import time as _time

        now = _time.monotonic()
        idle_s = now - self._last_stream_activity_at
        if idle_s >= 90.0 and not self._stream_stall_warned:
            self._stream_stall_warned = True
            self._status_badge.setText(
                f"流式连接疑似中断（已 {int(idle_s)}s 无数据），正在等待超时恢复…"
            )
            self._status_badge.set_tone("error")

    def _stop_stream_stall_timer(self) -> None:
        """Stop the stall detection timer (called on generation end)."""
        self._stream_stall_timer.stop()
        self._stream_stall_warned = False

    def _sync_voice_llm_adjudication_controls(self, enabled: bool) -> None:
        """Keep cost controls inactive until optional review is explicitly enabled."""
        for control in getattr(self, "_voice_llm_adjudication_controls", ()):
            control.setEnabled(enabled)

    def set_storage_root(self, storage_root: Path) -> None:
        """Set the storage root path for creating ProjectLayout."""
        self._storage_root = storage_root

    def export_ui_state(self) -> dict[str, Any]:
        """Return a restart-safe voice-studio location, never an active job."""
        tab_index = 0
        if hasattr(self, "_custom_tab_bar"):
            tab_index = max(0, self._custom_tab_bar.currentIndex())
        settings_scroll_value = 0
        settings_scroll = getattr(self, "_settings_scroll", None)
        if settings_scroll is not None:
            settings_scroll_value = max(0, settings_scroll.verticalScrollBar().value())
        return {
            "version": 3,
            "project_id": self._project_id,
            "tab_index": tab_index,
            "chapter_number": max(0, int(self._active_chapter_number or 0)),
            "settings_scroll_value": settings_scroll_value,
            "automation_modes": dict(self._audio_automation_modes),
        }

    def restore_ui_state(self, payload: object) -> None:
        """Stage a project and tab selection until the header selector is populated."""
        if not isinstance(payload, dict):
            return
        self._restored_project_id = str(payload.get("project_id") or "").strip()
        raw_modes = payload.get("automation_modes")
        if isinstance(raw_modes, dict):
            for project_id, raw_mode in raw_modes.items():
                project_key = str(project_id or "").strip()
                try:
                    mode = AudioAutomationMode(str(raw_mode)).value
                except ValueError:
                    continue
                if project_key:
                    self._restored_audio_automation_modes[project_key] = mode
            self._audio_automation_modes.update(self._restored_audio_automation_modes)
        try:
            self._restored_tab_index = max(0, int(payload.get("tab_index") or 0))
            self._active_chapter_number = max(0, int(payload.get("chapter_number") or 0))
            self._restored_settings_scroll_value = max(
                0,
                int(payload.get("settings_scroll_value") or 0),
            )
        except (TypeError, ValueError):
            self._restored_tab_index = 0
        self._apply_restored_ui_state()

    def _apply_restored_ui_state(self) -> None:
        """Apply a stored tab position once its lazily-built tab bar exists."""
        if self._restored_tab_index is None or not hasattr(self, "_custom_tab_bar"):
            return
        if self._custom_tab_bar.count() <= 0:
            return
        index = min(self._restored_tab_index, self._custom_tab_bar.count() - 1)
        self._custom_tab_bar.setCurrentIndex(index)
        self._restored_tab_index = None

    def _apply_restored_settings_scroll(self) -> None:
        """Restore the settings offset after the lazy tab receives its viewport."""
        value = self._restored_settings_scroll_value
        scroll = getattr(self, "_settings_scroll", None)
        if value is None or scroll is None:
            return

        def _restore() -> None:
            current_scroll = getattr(self, "_settings_scroll", None)
            if current_scroll is None:
                return
            bar = current_scroll.verticalScrollBar()
            if bar.maximum() <= 0:
                return
            bar.setValue(min(value, bar.maximum()))
            self._restored_settings_scroll_value = None

        QTimer.singleShot(0, _restore)

    def populate_project_selector(self, projects: list[tuple[str, str]]) -> None:
        """Populate the project selector with (project_id, display_label) pairs.

        Auto-loads the first project if no project is currently set.
        """
        selector = self._project_selector
        if selector is None:
            return
        selector.blockSignals(True)
        selector.clear()
        if not projects:
            selector.addItem("未选择项目")
        else:
            for project_id, label in projects:
                selector.addItem(label, project_id)
        preferred_project_id = self._restored_project_id or self._project_id
        if preferred_project_id:
            idx = selector.findData(preferred_project_id)
            if idx >= 0:
                selector.setCurrentIndex(idx)
        selector.blockSignals(False)

        # Restore the prior project when it still exists; only then fall back
        # to the first project.  Blocking selector signals above avoids a
        # navigation loop, so loading is deliberately explicit here.
        if preferred_project_id and self._storage_root and self._layout is None:
            idx = selector.findData(preferred_project_id)
            if idx >= 0:
                self._auto_load_project(preferred_project_id)
                self._restored_project_id = ""
                self._apply_restored_ui_state()
                return
        if not self._project_id and projects and self._storage_root:
            first_project_id, _ = projects[0]
            selector.blockSignals(True)
            selector.setCurrentIndex(0)  # First project is at index 0 after clear
            selector.blockSignals(False)
            self._auto_load_project(first_project_id)
        self._apply_restored_ui_state()

    def _on_project_selector_changed(self, index: int) -> None:
        """Handle project selector change."""
        selector = self._project_selector
        if selector is None:
            return
        project_id = selector.itemData(index)
        if project_id:
            self.project_selector_changed.emit(str(project_id))

    def set_project(self, project_id: str, layout: ProjectLayout) -> None:
        """Set the current project."""
        previous_project_id = self._project_id
        if self._project_id:
            self._audio_automation_modes[self._project_id] = self._audio_automation_mode
        self._project_id = project_id
        self._layout = layout
        if previous_project_id and previous_project_id != project_id:
            self._hide_voice_team_progress()
            self._hide_voice_team_task_feedback()
        self._audio_automation_mode = self._audio_automation_modes.get(
            project_id,
            resolve_audio_automation_mode(None, settings=self._settings).value,
        )
        self._audio_automation_modes[project_id] = self._audio_automation_mode
        self._sync_audio_automation_mode_ui()
        # A workspace refresh may rebind the same project after its artifacts
        # changed on disk.  Invalidate the chapter snapshot once here; staged
        # tab construction can then safely reuse the first refreshed snapshot.
        self._loaded_chapter_number = 0
        self._studio_service = VoiceStudioProjectService(layout, project_id=project_id)
        self._refresh_task_focus_scope()
        if hasattr(self, "_sound_generation_widgets"):
            self._refresh_stable_audio_runtime_summary()
        if hasattr(self, "_sound_library_panel"):
            self._sound_library_panel.set_project_service(self._studio_service)
        if self._project_selector is not None:
            idx = self._project_selector.findData(project_id)
            if idx >= 0:
                self._project_selector.blockSignals(True)
                self._project_selector.setCurrentIndex(idx)
                self._project_selector.blockSignals(False)
        # Owned timers are cancelled automatically if the page is destroyed;
        # static singleShot callbacks can otherwise touch deleted child widgets.
        self._project_load_timer.start(0)
        self._chapter_combo_load_timer.start(0)
        self.project_changed.emit(project_id)
        self.ui_state_changed.emit()

    def shutdown(self) -> None:
        """Clean up resources."""
        if self._shutdown_done:
            return
        self._shutdown_done = True
        self._deferred_tab_timer.stop()
        self._project_load_timer.stop()
        self._chapter_combo_load_timer.stop()
        self._word_hl_throttle.stop()
        self._progress_throttle.stop()
        if self._current_worker:
            self._current_worker.request_cancel()
        if self._voice_catalog_worker:
            self._voice_catalog_worker.request_cancel()
            self._voice_catalog_worker = None
        if self._synth_anim_timer is not None:
            self._stop_visibility_periodic_timer(self._synth_anim_timer)
        if self._script_gen_timer is not None:
            self._stop_visibility_periodic_timer(self._script_gen_timer)
        if self._segment_player is not None:
            self._segment_player.shutdown()
        if self._room_chapter_player is not None:
            self._room_chapter_player.shutdown()
        self._stop_audition()
        if self._audio_player is not None:
            self._audio_player.shutdown()
        if hasattr(self, "_sound_library_panel"):
            self._sound_library_panel.shutdown()
        from novel_forge.desktop.shutdown_utils import safe_disconnect

        for worker in self._stable_audio_workers:
            worker.request_cancel()
            safe_disconnect(worker.signals.listed)
            safe_disconnect(worker.signals.download_progress)
            safe_disconnect(worker.signals.download_finished)
            safe_disconnect(worker.signals.delete_finished)
            safe_disconnect(worker.signals.migration_finished)
        self._stable_audio_workers.clear()
        for benchmark_worker in self._audio_benchmark_workers:
            benchmark_worker.request_cancel()
            safe_disconnect(benchmark_worker.signals.audio_benchmark_completed)
            safe_disconnect(benchmark_worker.signals.worker_failed)
        self._audio_benchmark_workers.clear()
        for model_worker in self._audio_model_center_workers:
            model_worker.request_cancel()
            safe_disconnect(model_worker.signals.models_listed)
            safe_disconnect(model_worker.signals.runtimes_listed)
            safe_disconnect(model_worker.signals.repository_status)
            safe_disconnect(model_worker.signals.operation_progress)
            safe_disconnect(model_worker.signals.runtime_progress)
            safe_disconnect(model_worker.signals.operation_finished)
            safe_disconnect(model_worker.signals.worker_failed)
        self._audio_model_center_workers.clear()

    def bind_task_observation_store(self, store: TaskObservationStore) -> None:
        """Bind the window-owned task feed when the audio tab is available."""
        self._task_observation_store = store
        if self._current_worker is not None:
            self._observe_voice_worker(self._current_worker, label="声腔 · 处理中")
        if self._voice_catalog_worker is not None:
            self._observe_voice_worker(
                self._voice_catalog_worker,
                label="声腔 · 加载音色列表",
            )

    def _observe_voice_worker(
        self,
        worker: Any,
        *,
        label: str,
        chapter_number: int = 0,
    ) -> None:
        """Bridge Voice Studio workers into the window-level observation store."""

        store = self._task_observation_store
        worker_id = str(getattr(worker, "worker_id", "") or "").strip()
        if store is None or not worker_id or worker_id in self._observed_voice_task_ids:
            return
        observed_id = store.begin_external_task(
            worker_id,
            kind="voice_studio",
            label=label,
            project_id=self._project_id,
            current_step="voice_studio",
            chapter_number=chapter_number,
        )
        self._observed_voice_task_ids[worker_id] = observed_id
        signals = worker.signals
        if hasattr(signals, "step_progress"):
            signals.step_progress.connect(
                lambda step, payload, oid=observed_id: self._forward_voice_observation_step(
                    oid, step, payload
                )
            )
        signals.worker_cancelled.connect(
            lambda _wid, oid=observed_id, wid=worker_id: self._finish_voice_observation(
                oid, worker_id=wid, error="已取消"
            )
        )
        signals.worker_failed.connect(
            lambda _wid, payload, oid=observed_id, wid=worker_id: self._finish_voice_observation(
                oid,
                worker_id=wid,
                error=str(
                    (payload or {}).get("summary")
                    or (payload or {}).get("message")
                    or "声腔任务失败"
                ),
            )
        )
        signals.worker_finished.connect(
            lambda _wid, oid=observed_id, wid=worker_id: self._finish_voice_observation(
                oid, worker_id=wid
            )
        )

    def _forward_voice_observation_step(
        self,
        observed_id: str,
        step: str,
        payload: dict[str, Any],
    ) -> None:
        store = self._task_observation_store
        if store is not None:
            store.ingest_external_step(observed_id, step, payload)

    def _finish_voice_observation(
        self,
        observed_id: str,
        *,
        worker_id: str = "",
        error: str = "",
    ) -> None:
        store = self._task_observation_store
        if store is not None:
            store.complete_external_task(observed_id, error=error)
        if worker_id:
            self._observed_voice_task_ids.pop(worker_id, None)

    def bind_workspace_sections(self, snapshot: Any, sections: frozenset[str]) -> None:
        """Update project selector when workspace snapshot changes."""
        projects: list[tuple[str, str]] = []
        if hasattr(snapshot, "projects"):
            for item in snapshot.projects:
                projects.append((item.project_id, item.title or item.project_id))
        self.populate_project_selector(projects)

    def _refresh_task_focus_scope(self) -> None:
        """Compatibility no-op after removing the generic task-flow panel."""
        return

    # ─── Visual script rendering helpers ───────────────────────────────────
    # Script rendering, avatar buttons, speaker-review badge, browser HTML
    # replacement, and streaming preview are provided by ScriptRenderMixin.

    # ─── Private: Character list / voice team ──────────────────────────────

    def _auto_load_project(self, project_id: str) -> None:
        """Auto-load a project without triggering signals."""
        if not self._storage_root:
            return
        from novel_forge.persistence.models import ProjectLayout

        layout = ProjectLayout(self._storage_root / project_id)
        self.set_project(project_id, layout)

    def _load_voice_team(self) -> None:
        """Load voice team from disk; fall back to character bible preview."""
        if self._shutdown_done or not self._layout:
            return

        try:
            # Always cache bible characters for fallback display
            self._bible_characters = self._load_characters_from_bible()
            self._load_narrator_profile()

            voice_team_path = self._layout.tts_voice_team_path
            if voice_team_path.exists():
                data = json.loads(voice_team_path.read_text(encoding="utf-8"))
                loaded_team = VoiceTeamContract.model_validate(data)
                self._voice_team = hydrate_voice_team_performance_profiles(
                    loaded_team,
                    self._bible_characters,
                )
                if self._voice_team != loaded_team:
                    self._persist_voice_team()
                    invalidate_all_tts_audio_derivatives(self._layout)
                self._update_character_list()
                self._update_avatars()
            else:
                self._voice_team = None
                # Show characters from bible as "pending" so user knows what to build
                self._update_character_list_from_bible()

            # Show summary in voice info panel
            self._show_default_voice_info()
        except Exception as exc:
            # Log error and show user-friendly message
            import logging

            logging.getLogger("novel_forge.voice_studio").error(
                "Failed to load voice team: %s", exc, exc_info=True
            )
            if self._status_badge:
                self._status_badge.setText(f"加载失败: {str(exc)[:60]}")
                self._status_badge.set_tone("danger")

    def _load_narrator_profile(self) -> None:
        """Load the persistent LLM narrator design without requiring a cast entry."""
        self._narrator_profile = None
        if not self._layout or not self._layout.tts_narrator_profile_path.exists():
            return
        try:
            self._narrator_profile = NarratorVoiceProfile.model_validate(
                json.loads(self._layout.tts_narrator_profile_path.read_text(encoding="utf-8"))
            )
        except Exception as exc:
            _logger.warning("Failed to load narrator profile: %s", exc)

    @staticmethod
    def _voice_role_label(role: object) -> str:
        text = str(role or "").strip()
        return {
            "protagonist": "主角",
            "deuteragonist": "副主",
            "antagonist": "反派",
            "supporting": "配角",
            "minor": "龙套",
        }.get(text, text)

    @staticmethod
    def _voice_source_label(source: str) -> str:
        return {
            "designed": "AI设计",
            "cloned": "克隆",
            "system": "系统",
            "manual": "手动",
            "library": "音色库",
        }.get(source, source)

    def _cast_entry_row_text(
        self,
        entry: VoiceCastEntry,
        character: dict[str, Any],
    ) -> str:
        meta: list[str] = []
        role_label = self._voice_role_label(character.get("role"))
        if role_label:
            meta.append(role_label)
        source_label = self._voice_source_label(entry.voice_source)
        if source_label:
            meta.append(source_label)
        if entry.match_score is not None:
            meta.append(f"画像 {entry.match_score:.0%}")
        elif entry.voice_source == "manual":
            meta.append("待人工确认")
        adjudication_label = {
            "approved": "LLM 已确认",
            "recast": "LLM 已改配",
            "needs_audition": "待对比试听",
            "rejected": "候选已否决",
        }.get(entry.llm_adjudication_status, "")
        if adjudication_label:
            meta.append(adjudication_label)
        if any(
            vp.audio_path and Path(vp.audio_path).is_file()
            for vp in entry.preview_variants.values()
        ) or (entry.preview_audio_path and Path(entry.preview_audio_path).is_file()):
            meta.append("可试听")
        elif any(vp.error for vp in entry.preview_variants.values()) or entry.preview_error:
            meta.append("试听待重试")
        else:
            meta.append("试听待生成")
        if entry.is_expired:
            meta.insert(0, "已过期")
        elif entry.approval_status == "pending":
            meta.insert(0, "待确认音色")
        elif not entry.is_ready:
            meta.insert(0, "未就绪")
        return f"{entry.character_name}\n" + " · ".join(meta)

    def _append_narrator_list_item(self) -> None:
        """Add the work-level narrator as the first, selectable cast member."""
        profile = self._narrator_profile
        voice_id = str(
            (profile.voice_id if profile else "")
            or (self._voice_team.narrator_voice_id if self._voice_team else "")
        ).strip()
        if profile is None:
            detail = "待构建作品级音色"
        elif profile.is_expired:
            detail = "专属音色已过期 · 待重新构建"
        elif voice_id:
            source = {"designed": "专属设计", "manual": "手动指定", "system": "系统匹配"}.get(
                profile.voice_source,
                profile.voice_source,
            )
            detail = f"{profile.voice_type or '作品级旁白'} · {source}"
        else:
            detail = f"{profile.voice_type or '作品级旁白'} · 待分配音色"
        preview_state = (
            "可试听" if voice_id and not (profile and profile.is_expired) else "试听待生成"
        )
        item = QListWidgetItem(f"旁白\n{detail} · {preview_state}")
        item.setData(Qt.ItemDataRole.UserRole, _NARRATOR_ID)
        item.setData(
            Qt.ItemDataRole.UserRole + 1,
            "旁白 narrator llm 大纲 氛围 "
            f"{detail} {voice_id} {' '.join(profile.style_keywords) if profile else ''}".lower(),
        )
        if not voice_id or (profile and profile.is_expired):
            item.setForeground(Qt.GlobalColor.gray)
        self._character_list.addItem(item)

    def _update_character_list_from_bible(self) -> None:
        """Show characters from character_bible when no voice team exists yet."""
        self._character_list.clear()
        self._append_narrator_list_item()
        if not self._bible_characters:
            hint = QListWidgetItem("  (未找到 character_bible.json，请先完成长篇初始化)")
            hint.setFlags(hint.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self._character_list.addItem(hint)
            return

        for char in self._bible_characters:
            name = char.get("name", "未知角色")
            role = char.get("role", "")
            role_label = self._voice_role_label(role)
            meta = " · ".join(part for part in (role_label, "未分配音色") if part)
            item = QListWidgetItem(f"{name}\n{meta}")
            item.setData(Qt.ItemDataRole.UserRole, char.get("character_id", char.get("name", "")))
            item.setData(
                Qt.ItemDataRole.UserRole + 1,
                f"{name} {role_label} 未分配 系统音色".lower(),
            )
            item.setForeground(Qt.GlobalColor.gray)
            self._character_list.addItem(item)

        # Add a hint item
        hint = QListWidgetItem("  ↓ 点击「构建配音团队」为以上角色分配音色")
        hint.setFlags(hint.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        hint.setForeground(Qt.GlobalColor.gray)
        self._character_list.addItem(hint)
        self._refresh_voice_metrics()
        self._filter_character_list(self._character_search.text())

    def _update_character_list(self) -> None:
        """Update character list from voice team, supplementing with bible entries."""
        if not self._voice_team or not self._voice_team.entries:
            self._update_character_list_from_bible()
            return
        self._character_list.clear()
        self._append_narrator_list_item()

        if self._voice_team.entries:
            for entry in self._voice_team.entries:
                character = next(
                    (
                        value
                        for value in self._bible_characters
                        if value.get("character_id", value.get("name", "")) == entry.character_id
                    ),
                    {},
                )
                item = QListWidgetItem(self._cast_entry_row_text(entry, character))
                item.setData(Qt.ItemDataRole.UserRole, entry.character_id)
                upstream_traits = " ".join(
                    str(character.get(key) or "")
                    for key in ("role", "gender", "age", "personality", "voice")
                )
                item.setData(
                    Qt.ItemDataRole.UserRole + 1,
                    f"{entry.character_name} {entry.voice_source} {entry.clone_status.value} "
                    f"{entry.voice_id} {upstream_traits}".lower(),
                )
                item.setToolTip(
                    "\n".join(
                        [
                            *entry.match_reasons,
                            *(f"需确认：{warning}" for warning in entry.match_warnings),
                            "双击或按 Enter 播放试听",
                        ]
                    )
                )
                self._character_list.addItem(item)

            # Show bible characters not yet in voice team
            team_char_ids = {e.character_id for e in self._voice_team.entries}
            for char in self._bible_characters:
                cid = char.get("character_id", char.get("name", ""))
                if cid and cid not in team_char_ids:
                    name = char.get("name", "未知角色")
                    role_label = self._voice_role_label(char.get("role"))
                    meta = " · ".join(part for part in (role_label, "未分配音色") if part)
                    item = QListWidgetItem(f"{name}\n{meta}")
                    item.setData(Qt.ItemDataRole.UserRole, cid)
                    item.setData(
                        Qt.ItemDataRole.UserRole + 1,
                        f"{name} 未分配 系统音色".lower(),
                    )
                    item.setForeground(Qt.GlobalColor.gray)
                    self._character_list.addItem(item)
        self._refresh_voice_metrics()
        self._filter_character_list(self._character_search.text())

    def _update_rebuilt_character_rows(self, character_ids: list[str]) -> bool:
        """Update only rebuilt cast rows, preserving the list's UI state.

        A partial rebuild returns the complete ``VoiceTeamContract`` so that it
        can be persisted atomically.  Repainting that full contract made an
        update to one character look like the entire cast had been changed.
        """
        if not self._voice_team or not character_ids:
            return False

        entries_by_id = {entry.character_id: entry for entry in self._voice_team.entries}
        items_by_id = {
            str(item.data(Qt.ItemDataRole.UserRole) or ""): item
            for index in range(self._character_list.count())
            if (item := self._character_list.item(index)) is not None
        }
        selected_ids = {str(character_id) for character_id in character_ids}
        if not selected_ids.issubset(entries_by_id) or not selected_ids.issubset(items_by_id):
            return False

        for character_id in selected_ids:
            entry = entries_by_id[character_id]
            item = items_by_id[character_id]
            character = next(
                (
                    value
                    for value in self._bible_characters
                    if value.get("character_id", value.get("name", "")) == entry.character_id
                ),
                {},
            )
            item.setText(self._cast_entry_row_text(entry, character))
            upstream_traits = " ".join(
                str(character.get(key) or "")
                for key in ("role", "gender", "age", "personality", "voice")
            )
            item.setData(
                Qt.ItemDataRole.UserRole + 1,
                f"{entry.character_name} {entry.voice_source} {entry.clone_status.value} "
                f"{entry.voice_id} {upstream_traits}".lower(),
            )
            item.setData(Qt.ItemDataRole.ForegroundRole, None)
            item.setToolTip(
                "\n".join(
                    [
                        *entry.match_reasons,
                        *(f"需确认：{warning}" for warning in entry.match_warnings),
                        "双击或按 Enter 播放试听",
                    ]
                )
            )

        self._refresh_voice_metrics()
        self._filter_character_list(self._character_search.text())
        current = self._character_list.currentItem()
        if (
            current is not None
            and str(current.data(Qt.ItemDataRole.UserRole) or "") in selected_ids
        ):
            self._on_character_selected(current, current)
        return True

    def _filter_character_list(self, query: str) -> None:
        """Filter the cast list without rebuilding or losing selection state."""
        needle = query.strip().lower()
        for index in range(self._character_list.count()):
            item = self._character_list.item(index)
            searchable = str(item.data(Qt.ItemDataRole.UserRole + 1) or item.text()).lower()
            item.setHidden(bool(needle and needle not in searchable))

    def _refresh_voice_metrics(self) -> None:
        """Refresh the compact KPI cards mounted in the window header."""
        total = len(self._bible_characters) + (1 if self._layout else 0)
        ready = 0
        pending = 0
        expired = 0
        if self._voice_team:
            for e in self._voice_team.entries:
                if e.is_expired:
                    expired += 1
                elif e.is_ready:
                    ready += 1
                elif e.approval_status == "pending" and not e.is_expired:
                    # Voice designed/cloned but not yet audition-confirmed.
                    pending += 1
        narrator_voice_id = str(
            (self._narrator_profile.voice_id if self._narrator_profile else "")
            or (self._voice_team.narrator_voice_id if self._voice_team else "")
        ).strip()
        if self._narrator_profile and self._narrator_profile.is_expired:
            expired += 1
        elif narrator_voice_id:
            ready += 1
        self._metric_total.set_content("配音角色", str(total), "")
        self._metric_ready.set_content("已配", str(ready), "")
        self._metric_pending.set_content("待确认", str(pending), "")
        self._metric_expired.set_content("过期", str(expired), "")

    def _load_characters_from_bible(self) -> list[dict[str, Any]]:
        """Load character list from character_bible.json."""
        if not self._layout:
            return []
        chars_path = self._layout.characters_path
        if not chars_path.exists():
            return []
        try:
            data = json.loads(chars_path.read_text(encoding="utf-8"))
            bible = CharacterBible.model_validate(data)
            result: list[dict[str, Any]] = []
            for profile in bible.characters:
                name = str(profile.name or "").strip()
                if not name:
                    continue
                entry: dict[str, Any] = {"name": name}
                gender = str(getattr(profile, "gender", "") or "").strip()
                if gender:
                    entry["gender"] = gender
                age = str(getattr(profile, "age", "") or "").strip()
                if age:
                    entry["age"] = age
                cid = str(getattr(profile, "character_id", "") or "").strip()
                if cid:
                    entry["character_id"] = cid
                role = str(getattr(profile, "role", "") or "").strip()
                if role:
                    entry["role"] = role
                personality = str(getattr(profile, "personality", "") or "").strip()
                if personality:
                    entry["personality"] = personality
                voice = str(getattr(profile, "voice", "") or "").strip()
                if voice:
                    entry["voice"] = voice
                voice_hints = getattr(profile, "tts_voice_hints", None)
                if voice_hints:
                    entry["tts_voice_hints"] = voice_hints
                result.append(entry)
            return result
        except Exception:
            return []

    def _load_chapter_text(self, chapter_number: int) -> str:
        """Load finalized chapter text from chapters/ directory only."""
        if not self._layout:
            return ""
        chapter_path = self._layout.chapter_path(chapter_number)
        if chapter_path.exists():
            return chapter_path.read_text(encoding="utf-8")
        return ""

    def _chapter_tts_artifact_group_count(self, chapter_number: int) -> int:
        """Return the number of removable TTS artifact groups for one chapter.

        An audio directory is deliberately counted as one group: one click clears
        all of its segments, assembled audio and subtitle together.
        """
        if not self._layout or chapter_number < 1:
            return 0
        paths = (
            self._layout.tts_dubbing_script_path(chapter_number),
            self._layout.tts_audio_dir(chapter_number),
            self._layout.tts_take_dir(chapter_number),
            self._layout.tts_audio_result_path(chapter_number),
            self._layout.tts_sound_resolution_path(chapter_number),
            self._layout.tts_sound_generation_report_path(chapter_number),
            self._layout.reports_dir / f"chapter_{chapter_number:03d}_tts_metadata.json",
        )
        return sum(path.exists() for path in paths)

    def _chapters_with_tts_artifacts(self) -> list[int]:
        """Return available chapters that currently have removable TTS output."""
        return [
            chapter_number
            for chapter_number in self._available_chapters
            if self._chapter_tts_artifact_group_count(chapter_number)
        ]

    def _update_script_source_hint(self) -> None:
        """Explain whether the script view is backed by a usable source file."""
        hint = getattr(self, "_script_source_hint", None)
        if hint is None:
            return
        stream = self._script_streams.get(self._active_chapter_number)
        if stream is not None and self._generating_chapter == self._active_chapter_number:
            if stream.text:
                hint.setText(
                    f"第 {self._active_chapter_number} 章正在生成 · 已接收 {len(stream.text)} 字符"
                )
            else:
                hint.setText(f"第 {self._active_chapter_number} 章正在准备流式输出")
            hint.setProperty("state", "streaming")
        elif self._source_script_available:
            script = self._current_script
            freshness = self._assess_current_script_freshness()
            if freshness != DubbingScriptFreshness.CURRENT:
                labels = {
                    DubbingScriptFreshness.STALE: "旧版脚本 · 定稿正文已变化",
                    DubbingScriptFreshness.LEGACY: "旧版脚本 · 未记录正文版本",
                    DubbingScriptFreshness.SOURCE_MISSING: "脚本来源正文不可用",
                }
                hint.setText(labels.get(freshness, "脚本版本待核对"))
                hint.setProperty("state", "stale")
                hint.style().unpolish(hint)
                hint.style().polish(hint)
                self._refresh_script_freshness_ui()
                return
            sound_layers = 0
            directed = 0
            spoken = 0
            if script is not None:
                sound_layers = (
                    len(script.bgm_suggestions) + len(script.sfx_cues) + len(script.soundscapes)
                )
                for segment in script.segments:
                    if segment.segment_type not in {
                        SegmentType.NARRATION,
                        SegmentType.DIALOGUE,
                        SegmentType.INNER_THOUGHT,
                    }:
                        continue
                    spoken += 1
                    if (
                        segment.emotion != EmotionTag.NEUTRAL
                        or segment.tone_hint
                        or segment.speed_override is not None
                        or segment.paralinguistic_tags
                        or segment.stress_words
                    ):
                        directed += 1
            coverage = round(directed * 100 / spoken) if spoken else 0
            hint.setText(f"源脚本已就绪 · 导演标注 {coverage}% · 声场 {sound_layers} 项")
            hint.setProperty("state", "ready")
        elif self._current_audio_result is not None:
            hint.setText("源脚本已清理 · 仅保留已合成音频")
            hint.setProperty("state", "cleared")
        else:
            hint.setText("尚未生成脚本")
            hint.setProperty("state", "empty")
        hint.style().unpolish(hint)
        hint.style().polish(hint)
        self._refresh_script_freshness_ui()

    def _assess_current_script_freshness(self) -> DubbingScriptFreshness | None:
        """Compare the visible script with the authoritative finalized chapter."""

        script = self._current_script
        if script is None:
            self._script_freshness = None
            return None
        source_text = self._load_chapter_text(script.chapter_number)
        self._script_freshness = assess_dubbing_script_freshness(script, source_text)
        return self._script_freshness

    def _refresh_script_freshness_ui(self) -> None:
        """Project source identity into a visible, actionable version banner."""

        if not hasattr(self, "_script_freshness_bar"):
            return
        script = self._current_script
        freshness = self._assess_current_script_freshness()
        visible = bool(script is not None and self._source_script_available)
        self._script_freshness_bar.setVisible(visible)
        if not visible or script is None or freshness is None:
            return

        generated_at = ""
        try:
            generated_at = script.created_at.astimezone().strftime("%m-%d %H:%M")
        except (AttributeError, ValueError):
            pass
        identity = (
            f"正文 #{script.source_text_hash or '未记录'} · "
            f"脚本 #{script.script_hash or compute_dubbing_script_hash(script)}"
        )
        if generated_at:
            identity = f"生成于 {generated_at} · {identity}"

        if freshness == DubbingScriptFreshness.CURRENT:
            self._script_freshness_badge.setText("当前版本")
            self._script_freshness_badge.set_tone("success")
            self._script_freshness_hint.setText(f"与当前定稿正文一致 · {identity}")
            self._replace_script_btn.setText("重新生成")
            self._replace_script_btn.setVisible(False)
            self._remove_stale_script_btn.setVisible(False)
            self._script_freshness_bar.setProperty("state", "current")
        else:
            legacy = freshness == DubbingScriptFreshness.LEGACY
            source_missing = freshness == DubbingScriptFreshness.SOURCE_MISSING
            self._script_freshness_badge.setText(
                "来源未记录" if legacy else ("正文不可用" if source_missing else "旧版脚本")
            )
            self._script_freshness_badge.set_tone("warning" if legacy else "danger")
            if legacy:
                reason = "此脚本没有正文身份，不能证明属于当前定稿"
            elif source_missing:
                reason = "找不到当前章节定稿，已禁止试听生成、合成与导出"
            else:
                reason = "定稿正文已经更新，旧脚本已禁止试听生成、合成与导出"
            self._script_freshness_hint.setText(f"{reason} · {identity}")
            self._replace_script_btn.setText("用当前正文覆盖")
            self._replace_script_btn.setVisible(True)
            self._remove_stale_script_btn.setVisible(True)
            self._script_freshness_bar.setProperty("state", "stale")
        self._script_freshness_bar.style().unpolish(self._script_freshness_bar)
        self._script_freshness_bar.style().polish(self._script_freshness_bar)

    def _begin_tts_operation(self, kind: str, chapter_number: int) -> None:
        """Lock destructive actions while a chapter TTS workflow can still write files."""
        self._tts_operation = (kind, chapter_number)
        labels = {
            "segment": "声腔 · 分段重录",
            "segment_accept": "声腔 · 接受分段试听",
            "assemble": "声腔 · 装配章节音频",
            "script": "声腔 · 生成配音脚本",
            "synthesis": "声腔 · 合成章节音频",
            "sound_palette": "声腔 · 生成声音候选",
            "full_pipeline": "声腔 · 完整配音流程",
        }
        if self._current_worker is not None:
            self._observe_voice_worker(
                self._current_worker,
                label=labels.get(kind, "声腔 · 任务处理"),
                chapter_number=chapter_number,
            )
        self._refresh_workflow_controls()

    def _finish_tts_operation(self) -> None:
        """Release the artifact mutation lock after a terminal worker signal."""
        self._tts_operation = None
        if hasattr(self, "_sound_library_panel"):
            self._sound_library_panel.set_generation_running(False)

    def _clear_operation_is_running(self, action: str = "清理产物") -> bool:
        """Warn when a user action would race a live TTS worker."""
        if self._tts_operation is None:
            return False
        _kind, chapter_number = self._tts_operation
        show_warning_message(
            self,
            "TTS 任务仍在运行",
            f"第 {chapter_number} 章正在处理，暂时不能{action}。",
            informative_text="请等待任务完成，或取消任务并等待状态更新后再清理。",
        )
        return True

    def _populate_chapter_combo(self) -> None:
        """Populate chapter combo boxes from available chapters."""
        if self._shutdown_done or not self._layout:
            return
        chapters: list[int] = []
        chapters_dir = self._layout.chapters_dir
        if chapters_dir.exists():
            for f in sorted(chapters_dir.glob("chapter_*.md")):
                try:
                    num = int(f.stem.split("_")[1])
                    chapters.append(num)
                except (IndexError, ValueError):
                    pass
        chapters.sort()
        self._available_chapters = chapters
        combos = tuple(
            combo
            for combo in (
                getattr(self, "_chapter_combo", None),
                getattr(self, "_room_chapter_combo", None),
                getattr(self, "_audio_chapter_combo", None),
            )
            if combo is not None
        )
        preferred = self._active_chapter_number or next(
            (int(combo.currentData()) for combo in combos if combo.currentData() is not None),
            0,
        )
        for combo in combos:
            combo.blockSignals(True)
            try:
                combo.clear()
                if not chapters:
                    combo.addItem("无可用章节")
                else:
                    for num in chapters:
                        combo.addItem(f"第 {num} 章", num)
                    target = combo.findData(preferred)
                    combo.setCurrentIndex(target if target >= 0 else 0)
            finally:
                combo.blockSignals(False)
        selected = preferred if preferred in chapters else (chapters[0] if chapters else 0)
        if selected:
            # Deferred tab construction calls this method once per new tab.
            # Reuse the in-memory chapter snapshot instead of clearing and
            # re-probing the same segment/full-chapter media on every call.
            if selected != self._loaded_chapter_number:
                self._load_chapter_artifacts(selected)
        else:
            self._active_chapter_number = 0
            self._loaded_chapter_number = 0
            self._current_script = None
            self._playback_script = None
            self._current_audio_result = None
            self._source_script_available = False
            self._script_freshness = None
            self._update_script_source_hint()
            self._refresh_workflow_controls()

    @staticmethod
    def _select_chapter(combo: QComboBox | None, chapter_number: int) -> None:
        """Select a chapter without recursively firing the peer selector."""
        if combo is None:
            return
        target = combo.findData(chapter_number)
        if target < 0 or target == combo.currentIndex():
            return
        combo.blockSignals(True)
        try:
            combo.setCurrentIndex(target)
        finally:
            combo.blockSignals(False)

    def _on_script_chapter_changed(self, _index: int) -> None:
        if self._tts_operation is not None:
            show_warning_message(self, "操作进行中", "请等待当前合成完成或取消后再切换章节。")
            if self._active_chapter_number:
                self._select_chapter(self._chapter_combo, self._active_chapter_number)
            return
        chapter_number = self._chapter_combo.currentData()
        if chapter_number is None:
            return
        chapter_number = int(chapter_number)
        self._select_chapter(getattr(self, "_audio_chapter_combo", None), chapter_number)
        self._select_chapter(getattr(self, "_room_chapter_combo", None), chapter_number)
        self._load_chapter_artifacts(chapter_number)

    def _on_room_chapter_changed(self, _index: int) -> None:
        """Keep the script, voice-room, and post-production selections together."""
        if self._tts_operation is not None:
            show_warning_message(self, "操作进行中", "请等待当前合成完成或取消后再切换章节。")
            if self._active_chapter_number:
                self._select_chapter(self._room_chapter_combo, self._active_chapter_number)
            return
        chapter_number = self._room_chapter_combo.currentData()
        if chapter_number is None:
            return
        chapter_number = int(chapter_number)
        self._select_chapter(getattr(self, "_chapter_combo", None), chapter_number)
        self._select_chapter(getattr(self, "_audio_chapter_combo", None), chapter_number)
        self._load_chapter_artifacts(chapter_number)

    def _on_audio_chapter_changed(self, _index: int) -> None:
        if self._tts_operation is not None:
            show_warning_message(self, "操作进行中", "请等待当前合成完成或取消后再切换章节。")
            if self._active_chapter_number:
                self._select_chapter(self._audio_chapter_combo, self._active_chapter_number)
            return
        chapter_number = self._audio_chapter_combo.currentData()
        if chapter_number is None:
            return
        chapter_number = int(chapter_number)
        self._select_chapter(getattr(self, "_chapter_combo", None), chapter_number)
        self._select_chapter(getattr(self, "_room_chapter_combo", None), chapter_number)
        self._load_chapter_artifacts(chapter_number)

    def _load_chapter_artifacts(self, chapter_number: int) -> None:
        """Restore the exact script, synthesis results, timeline and subtitles for one chapter."""
        if not self._layout or chapter_number < 1:
            return
        self._active_chapter_number = chapter_number
        self._loaded_chapter_number = chapter_number
        self._refresh_task_focus_scope()
        self._current_script = None
        self._playback_script = None
        self._current_audio_result = None
        self._source_script_available = False
        self._script_freshness = None
        self._current_results = []
        self._current_timeline = None
        self._segment_status.clear()
        self._room_draft_segments.clear()
        self._room_candidate_takes.clear()
        self._room_loaded_audio_path = ""
        self._room_chapter_audio_path = ""
        self._voice_room_playback = False
        self._room_chapter_play_requested = False
        if self._segment_player is not None:
            self._segment_player.clear()
        if self._room_chapter_player is not None:
            self._room_chapter_player.clear()

        # If this chapter has an active generation stream, render its streaming
        # progress (recovering any mid-stream fragments already received) and
        # skip loading the stale/absent on-disk script. This is the key fix:
        # switching away from and back to a generating chapter restores its
        # own streaming view instead of showing "暂无脚本" or another chapter's
        # "正在分析脚本" animation.
        active_stream = self._script_streams.get(chapter_number)
        if active_stream is not None and self._generating_chapter == chapter_number:
            progress_step = (
                "tts_script_llm_batch_start" if active_stream.batch_count else "tts_script_start"
            )
            self._update_script_generation_progress(
                progress_step,
                {"chapter": chapter_number, "batch_count": active_stream.batch_count},
            )
            if active_stream.text:
                active_stream._force_render = True  # Chapter switched back — must repaint.
                self._render_script_stream()
            else:
                self._render_script_gen_placeholder(chapter_number)
            self._update_script_source_hint()
            self._render_sync_script_html()
            self._populate_voice_room_segments()
            self._render_mix_manifest()
            return

        script_path = self._layout.tts_dubbing_script_path(chapter_number)
        if script_path.exists():
            try:
                self._current_script = DubbingScript.model_validate_json(
                    script_path.read_text(encoding="utf-8")
                )
                self._source_script_available = True
                self._script_freshness = assess_dubbing_script_freshness(
                    self._current_script,
                    self._load_chapter_text(chapter_number),
                )
            except Exception:
                _logger.exception("Failed to load chapter %d dubbing script", chapter_number)

        result_path = self._layout.tts_audio_result_path(chapter_number)
        if result_path.exists():
            try:
                result = ChapterAudioResult.model_validate_json(
                    result_path.read_text(encoding="utf-8")
                )
                self._current_audio_result = result
                self._playback_script = result.script
                # The result manifest keeps a private script copy only so the
                # existing audio timeline remains playable.  Once the author
                # deletes the source script, do not surface that copy as an
                # unrestorable "snapshot" in Script or Voice Room.
                if not self._source_script_available:
                    self._current_script = None
                self._current_results = list(result.segment_results)
                self._segment_status = {
                    item.segment_index: item.status.value for item in result.segment_results
                }
                from novel_forge.tts.pipeline.timeline_builder import build_timeline

                self._current_timeline = build_timeline(result.script, result.segment_results)
            except Exception:
                _logger.exception("Failed to load chapter %d audio result", chapter_number)

        self._load_voice_room_take_state(chapter_number)

        self._render_script_html()
        self._update_script_source_hint()
        self._render_sync_script_html()
        if self._current_audio_result is not None:
            self._update_sound_resolution_badge(self._current_audio_result.metadata)
        elif hasattr(self, "_sound_resolution_badge"):
            cue_count = (
                len(self._current_script.soundscapes)
                + len(self._current_script.bgm_suggestions)
                + len(self._current_script.sfx_cues)
                if self._current_script is not None
                else 0
            )
            auto_design = bool(
                self._current_script
                and isinstance(self._current_script.metadata.get("soundscape_design"), dict)
                and self._current_script.metadata["soundscape_design"].get("mode")
                == "automatic_backfill"
            )
            self._sound_resolution_badge.setText(
                (
                    f"场景声音: {cue_count} 项待合成匹配（含自动声场）"
                    if auto_design
                    else f"场景声音: {cue_count} 项待合成匹配"
                )
                if cue_count
                else "场景声音: 本章未设计"
            )
            self._sound_resolution_badge.set_tone("warning" if cue_count else "muted")
        self._render_mix_manifest()
        self._populate_voice_room_segments()
        if hasattr(self, "_subtitle_text"):
            assembly_stale = bool(
                self._current_audio_result
                and self._current_audio_result.metadata.get("assembly_stale")
            )
            subtitle_path = (
                Path(self._current_audio_result.subtitle_path)
                if self._current_audio_result and self._current_audio_result.subtitle_path
                else self._layout.tts_subtitle_path(chapter_number)
            )
            self._subtitle_text.setPlainText(
                "已接受新分段；重新装配后生成与正式版一致的字幕。"
                if assembly_stale
                else (
                    subtitle_path.read_text(encoding="utf-8")
                    if subtitle_path.exists()
                    else "暂无字幕"
                )
            )
        if self._audio_player is not None:
            audio_path = (
                Path(self._current_audio_result.assembled_audio_path)
                if self._current_audio_result
                and self._current_audio_result.assembled_audio_path
                and not self._current_audio_result.metadata.get("assembly_stale")
                else None
            )
            if audio_path is not None and audio_path.is_file():
                self._audio_player.load_audio(audio_path, timeline=self._current_timeline)
                if hasattr(self, "_audio_empty"):
                    self._audio_empty.setVisible(False)
            else:
                self._audio_player.clear()
                if hasattr(self, "_audio_empty"):
                    self._audio_empty.setVisible(True)
        if self._room_chapter_player is not None:
            room_audio_path = (
                Path(self._current_audio_result.assembled_audio_path)
                if self._current_audio_result
                and self._current_audio_result.assembled_audio_path
                and not self._current_audio_result.metadata.get("assembly_stale")
                else None
            )
            if room_audio_path is not None and room_audio_path.is_file():
                self._room_chapter_player.load_audio(
                    room_audio_path,
                    timeline=self._current_timeline,
                )
                self._room_chapter_audio_path = str(room_audio_path)
            else:
                self._room_chapter_player.clear()
                self._room_chapter_audio_path = ""
        self._refresh_workflow_controls()

    def _load_voice_room_take_state(self, chapter_number: int) -> None:
        """Restore only drafts/candidates that match the current source script."""
        self._room_draft_segments.clear()
        self._room_candidate_takes.clear()
        if self._studio_service is None or self._current_script is None:
            return
        script_hash = _effective_script_hash(self._current_script)
        manifest = self._studio_service.load_take_manifest(chapter_number)
        if manifest.source_script_hash != script_hash:
            return
        self._room_draft_segments = {
            int(index): segment.model_copy(deep=True)
            for index, segment in manifest.drafts.items()
            if str(index).isdigit()
        }
        for take in reversed(manifest.takes):
            if take.status != TakeReviewStatus.CANDIDATE:
                continue
            if take.segment_index in self._room_candidate_takes:
                continue
            if take.source_script_hash != script_hash:
                continue
            if take.segment_result.audio_path and Path(take.segment_result.audio_path).is_file():
                self._room_candidate_takes[take.segment_index] = take

    def _populate_voice_room_segments(self) -> None:
        """Refresh the voice-room take list from the selected chapter script."""
        if not hasattr(self, "_room_segment_list"):
            return
        selected_index = self._selected_voice_room_segment_index()
        self._room_segment_list.blockSignals(True)
        try:
            self._room_segment_list.clear()
            if self._current_script is not None:
                for segment in self._current_script.segments:
                    status = self._segment_status.get(segment.segment_index, "pending")
                    has_candidate = segment.segment_index in self._room_candidate_takes
                    icon = (
                        "◉"
                        if has_candidate
                        else {
                            "completed": "✓",
                            "failed": "!",
                            "synthesizing": "…",
                            "skipped": "–",
                        }.get(status, "○")
                    )
                    speaker = (
                        "旁白"
                        if segment.segment_type == SegmentType.NARRATION
                        else segment.character_name or "角色"
                    )
                    preview = re.sub(r"\s+", " ", segment.text).strip()
                    if len(preview) > 20:
                        preview = f"{preview[:20]}…"
                    review_label = "  待审" if has_candidate else ""
                    item = QListWidgetItem(
                        f"{icon} {segment.segment_index + 1:02d}  {speaker}{review_label} · {preview}"
                    )
                    item.setData(Qt.ItemDataRole.UserRole, segment.segment_index)
                    item.setToolTip(segment.text)
                    self._room_segment_list.addItem(item)
            target_row = 0
            if selected_index is not None:
                for row in range(self._room_segment_list.count()):
                    item = self._room_segment_list.item(row)
                    if item.data(Qt.ItemDataRole.UserRole) == selected_index:
                        target_row = row
                        break
            if self._room_segment_list.count():
                self._room_segment_list.setCurrentRow(target_row)
        finally:
            self._room_segment_list.blockSignals(False)
        self._render_voice_room_segment()

    def _selected_voice_room_segment_index(self) -> int | None:
        if not hasattr(self, "_room_segment_list"):
            return None
        item = self._room_segment_list.currentItem()
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return int(value) if value is not None else None

    def _selected_voice_room_segment(self) -> DubbingSegment | None:
        segment_index = self._selected_voice_room_segment_index()
        if self._current_script is None or segment_index is None:
            return None
        draft = self._room_draft_segments.get(segment_index)
        if draft is not None:
            return draft
        return next(
            (item for item in self._current_script.segments if item.segment_index == segment_index),
            None,
        )

    def _on_segment_player_prev(self) -> None:
        """Navigate to the previous segment in the voice room list."""
        if not hasattr(self, "_room_segment_list"):
            return
        keep_playing = bool(self._segment_player and self._segment_player.is_playing())
        current = self._room_segment_list.currentRow()
        if current > 0:
            self._room_segment_list.setCurrentRow(current - 1)
            if keep_playing and self._segment_player is not None:
                self._segment_player.play()

    def _on_segment_player_next(self) -> None:
        """Navigate to the next segment in the voice room list."""
        if not hasattr(self, "_room_segment_list"):
            return
        keep_playing = bool(self._segment_player and self._segment_player.is_playing())
        current = self._room_segment_list.currentRow()
        if current < self._room_segment_list.count() - 1:
            self._room_segment_list.setCurrentRow(current + 1)
            if keep_playing and self._segment_player is not None:
                self._segment_player.play()

    def _on_voice_room_segment_changed(self, _row: int) -> None:
        chapter_is_playing = bool(
            self._voice_room_playback
            and self._room_chapter_player is not None
            and self._room_chapter_player.is_playing()
        )
        # Selecting a row while the master is running only changes the
        # inspector.  It must not replace the visible master timeline with a
        # clip timeline.  Once the master is paused, selecting a row returns to
        # isolated-segment audition mode.
        if self._voice_room_playback and not chapter_is_playing:
            self._voice_room_playback = False
            self._room_chapter_play_requested = False
        self._render_voice_room_segment(load_audio=not chapter_is_playing)

    def _set_room_player_scope(
        self,
        scope: Literal["segment", "chapter"],
        *,
        segment_index: int | None = None,
    ) -> None:
        """Expose the transport whose timeline matches the active audio scope."""
        if self._room_player_stack is None:
            return
        if scope == "chapter" and self._room_chapter_player is not None:
            self._room_player_stack.setCurrentWidget(self._room_chapter_player)
            chapter = self._active_chapter_number or 1
            suffix = (
                f" · 当前第 {segment_index + 1} 段"
                if segment_index is not None and segment_index >= 0
                else ""
            )
            self._room_player_scope_label.setText(f"当前播放：整章 · 第 {chapter} 章{suffix}")
            return
        if self._segment_player is not None:
            self._room_player_stack.setCurrentWidget(self._segment_player)
        selected = self._selected_voice_room_segment_index()
        suffix = f" · 第 {selected + 1} 段" if selected is not None else ""
        self._room_player_scope_label.setText(f"当前播放：片段{suffix}")

    def _prepare_room_chapter_player(self) -> bool:
        """Load the current assembled master into Voice Room's visible player."""
        player = self._room_chapter_player
        result = self._current_audio_result
        if player is None or result is None:
            return False
        raw_path = result.assembled_audio_path
        if not raw_path or result.metadata.get("assembly_stale"):
            return False
        audio_path = Path(raw_path)
        if not audio_path.is_file():
            return False
        timeline = self._current_timeline
        if timeline is None:
            try:
                from novel_forge.tts.pipeline.timeline_builder import build_timeline

                timeline = build_timeline(result.script, result.segment_results)
                self._current_timeline = timeline
            except Exception:
                _logger.exception(
                    "Failed to build chapter %d Voice Room timeline",
                    result.chapter_number,
                )
                return False
        path_key = str(audio_path)
        if path_key != self._room_chapter_audio_path or not player.has_timeline():
            player.load_audio(audio_path, timeline=timeline)
            self._room_chapter_audio_path = path_key
        return player.has_timeline()

    def _select_voice_room_playback_segment(self, segment_index: int) -> None:
        """Select and render the segment reached by full-chapter playback."""
        if not hasattr(self, "_room_segment_list"):
            return
        for row in range(self._room_segment_list.count()):
            item = self._room_segment_list.item(row)
            if item is None or item.data(Qt.ItemDataRole.UserRole) != segment_index:
                continue
            self._room_segment_list.blockSignals(True)
            try:
                self._room_segment_list.setCurrentRow(row)
                self._room_segment_list.scrollToItem(item)
            finally:
                self._room_segment_list.blockSignals(False)
            # Do not re-probe every isolated clip while the assembled master is
            # playing. Only the inspector content needs to follow here.
            self._render_voice_room_segment(load_audio=False)
            return

    def _on_room_segment_double_clicked(self, item: QListWidgetItem) -> None:
        """Jump the chapter player to the double-clicked segment and resume.

        Single-click still selects the segment for editing/audition-preview;
        double-click treats the room as a chapter playback surface, seeking the
        assembled master to that segment and continuing playback so the left
        list follows progress.  Requires the chapter audio to be loaded with a
        timeline (i.e. not ``assembly_stale``).
        """
        value = item.data(Qt.ItemDataRole.UserRole)
        if value is None:
            return
        segment_index = int(value)
        if not self._prepare_room_chapter_player() or self._room_chapter_player is None:
            self._room_status_badge.setText("整章音频未就绪，无法从该段跳转播放")
            self._room_status_badge.set_tone("warning")
            return
        if self._segment_player is not None and self._segment_player.is_playing():
            self._segment_player.pause()
        self._voice_room_playback = True
        self._room_chapter_play_requested = True
        self._set_room_player_scope("chapter", segment_index=segment_index)
        self._room_chapter_player.seek_to_segment(segment_index)
        self._room_chapter_player.play()
        self._set_room_full_playback_ui(True, segment_index=segment_index)
        self._room_status_badge.setText(f"从第 {segment_index + 1} 段开始播放整章 · 右侧字幕已跟随")
        self._room_status_badge.set_tone("success")

    def _on_play_full_chapter(self) -> None:
        """Play the assembled full-chapter audio from the beginning.

        Voice Room owns a dedicated visible chapter player so the displayed
        duration, seek range and transport boundaries always describe the
        assembled master rather than the last selected clip.
        """
        player = self._room_chapter_player
        if player is None:
            return
        # Once chapter playback has started, the same prominent control is the
        # pause/resume affordance. This keeps transport available in Voice Room
        # instead of forcing the user to switch to the post-processing tab.
        if self._voice_room_playback and (
            self._room_chapter_play_requested or self._playback_active or player.is_playing()
        ):
            self._set_room_player_scope("chapter", segment_index=self._playback_segment_idx)
            if self._room_chapter_play_requested or player.is_playing():
                self._room_chapter_play_requested = False
                player.pause()
                self._set_room_full_playback_ui(False, paused=True)
            else:
                self._room_chapter_play_requested = True
                player.play()
                self._set_room_full_playback_ui(True)
            return
        if not self._prepare_room_chapter_player():
            self._room_status_badge.setText("全章音频尚未装配，请先到“后处理”合成")
            self._room_status_badge.set_tone("warning")
            return
        if self._segment_player is not None and self._segment_player.is_playing():
            self._segment_player.pause()
        self._voice_room_playback = True
        self._room_chapter_play_requested = True
        self._room_preview_active = False
        self._set_room_player_scope("chapter", segment_index=0)
        player.seek_to_segment(0)
        player.play()
        self._set_room_full_playback_ui(True, segment_index=0)
        self._room_status_badge.setText("正在播放全章音频 · 右侧字幕已跟随")
        self._room_status_badge.set_tone("success")

    def _set_room_full_playback_ui(
        self,
        is_playing: bool,
        *,
        paused: bool = False,
        segment_index: int | None = None,
    ) -> None:
        """Mirror chapter transport state in the visible Voice Room controls."""
        if hasattr(self, "_room_play_full_btn"):
            self._room_play_full_btn.setText("⏸ 暂停全章" if is_playing else "▶ 继续全章")
        if not hasattr(self, "_room_playback_state"):
            return
        active_index = self._playback_segment_idx if segment_index is None else segment_index
        self._set_room_player_scope(
            "chapter",
            segment_index=active_index if active_index >= 0 else None,
        )
        if is_playing:
            suffix = f" · 第 {active_index + 1} 段" if active_index >= 0 else ""
            self._room_playback_state.setText(f"● 正在播放{suffix} · 字幕跟随")
        elif paused:
            self._room_playback_state.setText("● 已暂停 · 字幕停在当前位置")

    def _render_voice_room_segment(self, *, load_audio: bool = True) -> None:
        """Render the selected take and its effective direction for immediate audition."""
        if not hasattr(self, "_room_segment_heading"):
            return
        segment = self._selected_voice_room_segment()
        if segment is None:
            self._room_segment_heading.setText("选择一个片段")
            self._room_direction.setText("配音脚本会为每段保存情绪、语气、语速、音量和音高指令。")
            self._room_sound_context.setText(
                "场景声音参考将在脚本生成后显示；这里只供演员把握空间与节奏，选择和混音请到后处理。"
            )
            self._room_segment_text.clear()
            self._room_caption_position.setText("字幕 -- / --")
            self._room_caption_speaker.setText("待机")
            self._room_caption_speaker.set_tone("muted")
            self._room_playback_state.setText("● 字幕待机")
            self._room_edit_btn.setEnabled(False)
            self._room_generate_btn.setEnabled(False)
            self._room_accept_btn.setEnabled(False)
            self._room_discard_btn.setEnabled(False)
            if self._segment_player is not None:
                self._segment_player.clear()
            self._room_loaded_audio_path = ""
            return
        kind = {
            SegmentType.NARRATION: "旁白",
            SegmentType.DIALOGUE: "对白",
            SegmentType.INNER_THOUGHT: "内心独白",
            SegmentType.BGM: "背景音乐",
            SegmentType.SFX: "音效",
            SegmentType.SILENCE: "静音",
        }.get(segment.segment_type, "片段")
        speaker = segment.character_name or ("旁白" if kind == "旁白" else "未指定角色")
        heading_meta = speaker if speaker == kind else f"{speaker} · {kind}"
        self._room_segment_heading.setText(f"第 {segment.segment_index + 1} 段 · {heading_meta}")
        emotion, _ = _emotion_display(segment.emotion)
        tone = segment.tone_hint or "自然表达"
        speed, volume, pitch = self._voice_room_effective_parameter_labels(segment)
        self._room_direction.setText(
            f"实际合成：情绪「{emotion}」· 语气「{tone}」· 语速「{speed}」· "
            f"音量「{volume}」· 音高「{pitch}」。"
        )
        self._room_sound_context.setText(self._voice_room_sound_context(segment))
        self._set_voice_room_segment_text(segment)
        is_spoken = segment.segment_type in {
            SegmentType.NARRATION,
            SegmentType.DIALOGUE,
            SegmentType.INNER_THOUGHT,
        }
        script_is_current = (
            self._assess_current_script_freshness() == DubbingScriptFreshness.CURRENT
        )
        available = (
            self._source_script_available and script_is_current and self._tts_operation is None
        )
        speaker_review_pending = bool(
            self._current_script
            and segment.segment_index in unresolved_speaker_indices(self._current_script)
        )
        source_audit_pending = bool(
            self._current_script
            and requires_script_source_audit(
                self._current_script,
                self._load_chapter_text(self._current_script.chapter_number),
            )
        )
        self._room_edit_btn.setEnabled(available)
        self._room_generate_btn.setEnabled(
            available and is_spoken and not speaker_review_pending and not source_audit_pending
        )
        candidate = self._room_candidate_takes.get(segment.segment_index)
        self._room_accept_btn.setEnabled(available and candidate is not None)
        self._room_discard_btn.setEnabled(available and candidate is not None)
        self._room_generate_btn.setText("重新生成试听" if candidate else "生成试听")
        if not script_is_current:
            self._room_generate_btn.setToolTip(
                "此脚本不属于当前定稿正文；请回到配音脚本页用当前正文覆盖。"
            )
        elif source_audit_pending:
            self._room_generate_btn.setToolTip("这是旧版未审核脚本；请先回到配音脚本页重新生成。")
        elif speaker_review_pending:
            self._room_generate_btn.setToolTip(
                "该对白的说话人尚未通过正文审核；请回到配音脚本编辑器指定角色。"
            )
        elif not is_spoken:
            self._room_generate_btn.setToolTip("环境声、BGM 与静音段在后处理阶段管理。")

        audio_path = ""
        status_text = "本段尚无可试听音频"
        status_tone = "muted"
        formal_result = None
        if candidate is not None:
            audio_path = candidate.segment_result.audio_path
            status_text = "待审试听 · 未接受，不会参与装配"
            status_tone = "warning"
        else:
            formal_result = next(
                (
                    item
                    for item in self._current_results
                    if item.segment_index == segment.segment_index
                    and item.status.value == "completed"
                    and item.audio_path
                    and Path(item.audio_path).is_file()
                ),
                None,
            )
            if formal_result is not None:
                audio_path = formal_result.audio_path
                stale = bool(
                    self._current_audio_result
                    and self._current_audio_result.metadata.get("assembly_stale")
                )
                accepted_ids = (
                    self._current_audio_result.metadata.get("accepted_take_ids", {})
                    if self._current_audio_result
                    else {}
                )
                if stale and str(segment.segment_index) in accepted_ids:
                    status_text = "已接受正式版 · 待后处理重新装配"
                    status_tone = "success"
                else:
                    status_text = "已装配正式版 · 可直接试听"
                    status_tone = "success"
        preview_result = candidate.segment_result if candidate is not None else formal_result
        if self._segment_player is not None and load_audio:
            self._set_room_player_scope("segment")
            if audio_path and audio_path != self._room_loaded_audio_path:
                self._segment_player.load_audio(
                    audio_path,
                    timeline=self._build_voice_room_preview_timeline(segment, preview_result),
                )
                self._room_loaded_audio_path = audio_path
            elif not audio_path and self._room_loaded_audio_path:
                self._segment_player.clear()
                self._room_loaded_audio_path = ""
        self._room_status_badge.setText(status_text)
        self._room_status_badge.set_tone(status_tone)

    def _voice_room_effective_parameter_labels(
        self,
        segment: DubbingSegment,
    ) -> tuple[str, str, str]:
        """Describe the parameters that synthesis will actually receive."""
        base_speed = (
            segment.speed_override
            if segment.speed_override is not None
            else self._settings.tts_default_speed
        )
        volume = segment.vol_override if segment.vol_override is not None else 1.0
        segment_pitch = segment.pitch_override if segment.pitch_override is not None else 0
        entry = (
            self._voice_team.get_entry(segment.character_id)
            if self._voice_team is not None and segment.character_id
            else None
        )
        if entry is None:
            return f"{base_speed:.2f}×", f"{volume:.2f}×", f"{segment_pitch:+d} st"

        resolved = resolve_character_performance(
            entry,
            entry.provider,
            text=segment.text,
            default_speed=self._settings.tts_default_speed,
            speed_override=segment.speed_override,
            pitch_override=segment.pitch_override,
            vol_override=segment.vol_override,
            emotion=segment.emotion,
        )
        suffix = " · 短句稳定" if resolved.short_utterance_stabilized else ""
        return (
            f"{resolved.speed:.2f}×{suffix}",
            f"{resolved.volume:.2f}×",
            f"{resolved.pitch:+d} st{suffix}",
        )

    @staticmethod
    def _build_voice_room_preview_timeline(segment: DubbingSegment, result: Any) -> Any:
        """Build a zero-based timeline for one isolated take preview."""
        if result is None:
            return None
        from novel_forge.tts.pipeline.timeline_builder import PlaybackTimeline, TimelineEntry

        text = segment.synthesis_text
        duration_ms = int(getattr(result, "duration_ms", 0) or segment.duration_ms)
        if duration_ms <= 0:
            duration_ms = max(1_000, len(text) * 200)
        timeline = PlaybackTimeline(
            entries=[
                TimelineEntry(
                    segment_index=segment.segment_index,
                    start_ms=0,
                    end_ms=duration_ms,
                    segment_type=segment.segment_type,
                    character_id=segment.character_id,
                    character_name=segment.character_name,
                    text=text,
                    emotion=segment.emotion.value,
                    source_paragraph=segment.source_paragraph,
                )
            ],
            total_duration_ms=duration_ms,
        )
        timeline.build_index()
        return timeline

    def _set_voice_room_segment_text(
        self,
        segment: DubbingSegment,
        *,
        highlight: tuple[int, int] | None = None,
    ) -> None:
        """Render a teleprompter-style current cue with one quiet cue on either side."""
        spoken = segment.synthesis_text
        start, end = highlight or (0, 0)
        start = max(0, min(start, len(spoken)))
        end = max(start, min(end, len(spoken)))
        if highlight is None:
            spoken_html = html_lib.escape(spoken)
        else:
            # Karaoke focus mode: show a compact phrase around the current
            # character rather than painting a moving bar across the entire
            # paragraph. Adjacent context stays visible as quiet preview lines.
            clause_boundaries = [0]
            clause_boundaries.extend(
                match.end() for match in re.finditer(r"[。！？!?；;\n]+", spoken)
            )
            if clause_boundaries[-1] != len(spoken):
                clause_boundaries.append(len(spoken))
            clause_start = 0
            clause_end = len(spoken)
            for left, right in zip(clause_boundaries, clause_boundaries[1:], strict=False):
                if left <= start < right or (start == len(spoken) and right == len(spoken)):
                    clause_start, clause_end = left, right
                    break
            focus_start = max(clause_start, start - 14)
            focus_end = min(clause_end, max(end + 18, start + 1))
            focus_prefix = "…" if focus_start > clause_start else ""
            focus_suffix = "…" if focus_end < clause_end else ""
            previous_context = spoken[max(0, clause_start - 16) : clause_start].strip()
            next_context = spoken[clause_end : min(len(spoken), clause_end + 16)].strip()
            spoken_html = (
                (
                    f"<div style='font-size:10pt; color:{qcolor_hex('text.muted')}; "
                    f"text-align:center; margin-bottom:10px;'>"
                    f"{html_lib.escape(previous_context)}</div>"
                    if previous_context
                    else ""
                )
                + f"<div style='font-size:15pt; color:{qcolor_hex('text.primary')}; "
                f"font-weight:500; line-height:1.7; text-align:center; padding:10px 8px;'>"
                f"{focus_prefix}{html_lib.escape(spoken[focus_start:start])}"
                f"<span style='color:{qcolor_hex('accent.primary')}; font-weight:800;'>"
                f"{html_lib.escape(spoken[start:end])}</span>"
                f"{html_lib.escape(spoken[end:focus_end])}{focus_suffix}</div>"
                + (
                    f"<div style='font-size:10pt; color:{qcolor_hex('text.disabled')}; "
                    f"text-align:center; margin-top:10px;'>"
                    f"{html_lib.escape(next_context)}</div>"
                    if next_context
                    else ""
                )
            )
        context_segments: list[DubbingSegment] = []
        if self._current_script is not None:
            context_segments = [
                item
                for item in self._current_script.segments
                if item.segment_type
                in {SegmentType.NARRATION, SegmentType.DIALOGUE, SegmentType.INNER_THOUGHT}
            ]
        context_position = next(
            (
                index
                for index, item in enumerate(context_segments)
                if item.segment_index == segment.segment_index
            ),
            -1,
        )
        current_speaker = segment.character_name or (
            "旁白" if segment.segment_type == SegmentType.NARRATION else "待审说话人"
        )
        if context_position >= 0:
            self._room_caption_position.setText(
                f"字幕 {context_position + 1:02d} / {len(context_segments):02d}"
            )
        else:
            self._room_caption_position.setText(f"片段 {segment.segment_index + 1:02d}")
        self._room_caption_speaker.setText(current_speaker)
        self._room_caption_speaker.set_tone(
            "warning" if current_speaker == "待审说话人" else "info"
        )

        def context_line(item: DubbingSegment, *, before: bool) -> str:
            preview = " ".join(item.synthesis_text.split())
            if len(preview) > 64:
                preview = f"…{preview[-63:]}" if before else f"{preview[:63]}…"
            speaker = item.character_name or (
                "旁白" if item.segment_type == SegmentType.NARRATION else "待审说话人"
            )
            return (
                f"<div style='font-size:10.5pt; color:{qcolor_hex('text.muted')}; "
                "line-height:1.55; margin:4px 8px;'>"
                f"<span style='font-weight:600;'>{html_lib.escape(speaker)}</span>"
                f"<span style='color:{qcolor_hex('text.disabled')};'> · </span>"
                f"{html_lib.escape(preview)}</div>"
            )

        blocks: list[str] = []
        if context_position >= 0:
            for item in context_segments[max(0, context_position - 1) : context_position]:
                blocks.append(context_line(item, before=True))
        if segment.spoken_text.strip() and segment.spoken_text.strip() != segment.text.strip():
            blocks.append(
                f"<div style='font-size:10pt; color:{qcolor_hex('text.muted')}; "
                f"margin-bottom:8px;'>正文原文</div>"
                f"<div style='font-size:12pt; color:{qcolor_hex('text.secondary')}; "
                f"line-height:1.7; margin-bottom:14px;'>{html_lib.escape(segment.text)}</div>"
                f"<div style='font-size:10pt; color:{qcolor_hex('text.muted')}; "
                f"margin-bottom:8px;'>试听朗读</div>"
            )
        blocks.append(
            f"<div style='font-size:9.5pt; color:{qcolor_hex('accent.primary')}; "
            "font-weight:700; text-align:center; margin-top:7px;'>"
            f"正在演绎 · {html_lib.escape(current_speaker)}</div>"
        )
        if highlight is None:
            blocks.append(
                f"<div style='font-size:15pt; color:{qcolor_hex('text.primary')}; "
                "font-weight:550; line-height:1.8; text-align:center; "
                f"padding:10px 18px 14px 18px;'>{spoken_html}</div>"
            )
        else:
            blocks.append(spoken_html)
        if context_position >= 0:
            for item in context_segments[context_position + 1 : context_position + 2]:
                blocks.append(context_line(item, before=False))
        update_browser_html(
            self._room_segment_text,
            f"<div style='padding:5px 10px;'>{''.join(blocks)}</div>",
        )

    def _voice_room_sound_context(self, segment: DubbingSegment) -> str:
        """Return read-only sound-design context relevant to one actor take."""
        script = self._current_script
        if script is None:
            return "声音设计参考：本章尚无环境声、音乐或音效线索。"
        context = VoiceStudioProjectService.segment_sound_context(script, segment)
        parts: list[str] = []
        parts.extend(f"环境「{name}」" for name in context.ambience)
        parts.extend(f"音乐「{name}」" for name in context.music)
        for name, offset_ms in context.effects:
            offset = f"（台词后 {offset_ms}ms）" if offset_ms > 0 else ""
            parts.append(f"音效「{name}」{offset}")
        prefix = f"表演空间：{context.scene}。" if context.scene else ""
        if not parts:
            return f"{prefix}声音设计参考：本段不需要演员主动模拟音效；保持自然留白。"
        return (
            f"{prefix}声音设计参考：{' · '.join(parts)}。"
            "仅用于把握空间、停顿和注意力方向；资产选择、音量与淡入淡出由后处理完成。"
        )

    def _on_edit_voice_room_segment(self) -> None:
        segment = self._selected_voice_room_segment()
        if segment is None or self._current_script is None or self._studio_service is None:
            return
        working_segments = [
            self._room_draft_segments.get(item.segment_index, item).model_copy(deep=True)
            for item in self._current_script.segments
        ]
        working_script = self._current_script.model_copy(update={"segments": working_segments})
        dialog = ScriptSegmentEditorDialog(
            working_script,
            initial_segment_index=segment.segment_index,
            provider=self._get_current_provider(),
            performance_only=True,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.has_changes:
            return
        edited_script = dialog.edited_script()
        changed = 0
        for edited, previous in zip(edited_script.segments, working_segments, strict=False):
            if edited == previous:
                continue
            candidate = self._room_candidate_takes.pop(edited.segment_index, None)
            if candidate is not None:
                self._studio_service.reject_candidate_take(
                    self._active_chapter_number,
                    candidate.take_id,
                )
            self._room_draft_segments[edited.segment_index] = edited.model_copy(deep=True)
            self._studio_service.save_take_draft(
                self._active_chapter_number,
                script_hash=_effective_script_hash(self._current_script),
                segment=edited,
            )
            changed += 1
        if changed:
            self._room_status_badge.setText(f"已保存 {changed} 段待审指导，尚未覆盖正式版")
            self._room_status_badge.set_tone("warning")
            self._populate_voice_room_segments()
            self._refresh_workflow_controls()

    def _on_synthesize_selected_segment(self) -> None:
        """Generate only the selected spoken take and load it for immediate audition."""
        if not self._layout:
            return
        segment = self._selected_voice_room_segment()
        if segment is None:
            return
        worker = SynthesizeSegmentWorker(
            project_id=self._project_id,
            chapter_number=self._active_chapter_number,
            segment_index=segment.segment_index,
            settings=self._settings,
            layout=self._layout,
            provider=self._get_current_provider(),
            segment_override=segment.model_dump(mode="json"),
            mock=(self._get_current_provider() == "mock"),
        )
        worker.signals.segment_audio_completed.connect(self._on_segment_audio_completed)
        worker.signals.worker_failed.connect(self._on_worker_failed)
        self._current_worker = worker
        self._begin_tts_operation("segment", self._active_chapter_number)
        self._cancel_btn.setEnabled(True)
        self._room_status_badge.setText(f"正在生成第 {segment.segment_index + 1} 段")
        self._room_status_badge.set_tone("warning")
        self._status_badge.setText(f"正在生成第 {segment.segment_index + 1} 段试听…")
        self._status_badge.set_tone("warning")
        worker.submit()

    def _on_segment_audio_completed(self, data: dict[str, Any]) -> None:
        """Load an isolated candidate without changing formal audio state."""
        self._current_worker = None
        self._finish_tts_operation()
        result = data.get("segment_result") if isinstance(data, dict) else None
        if not isinstance(result, dict):
            self._on_worker_failed("", {"summary": "本段生成完成，但试听结果无法读取"})
            return
        segment_index = int(result.get("segment_index", -1))
        audio_path = str(result.get("audio_path") or "")
        if segment_index < 0 or not audio_path or not Path(audio_path).is_file():
            self._on_worker_failed("", {"summary": "本段生成完成，但找不到试听文件"})
            return
        if self._studio_service is not None:
            take = self._studio_service.latest_candidate_take(
                self._active_chapter_number,
                segment_index,
                script_hash=(
                    _effective_script_hash(self._current_script) if self._current_script else ""
                ),
            )
            if take is not None:
                self._room_candidate_takes[segment_index] = take
                self._room_draft_segments[segment_index] = take.segment.model_copy(deep=True)
        if self._segment_player is not None:
            self._segment_player.load_audio(audio_path)
        self._voice_room_playback = False
        self._room_chapter_play_requested = False
        self._set_room_player_scope("segment", segment_index=segment_index)
        self._room_loaded_audio_path = audio_path
        self._room_status_badge.setText(f"第 {segment_index + 1} 段待审试听已生成 · 未接受")
        self._room_status_badge.set_tone("warning")
        self._status_badge.setText(f"第 {segment_index + 1} 段试听已隔离保存；正式脚本与成品未变")
        self._status_badge.set_tone("success")
        self._render_script_html()
        self._render_sync_script_html()
        self._populate_voice_room_segments()
        self._refresh_workflow_controls()

    def _on_accept_segment_take(self) -> None:
        segment_index = self._selected_voice_room_segment_index()
        if segment_index is None or self._layout is None:
            return
        take = self._room_candidate_takes.get(segment_index)
        if take is None:
            return
        worker = AcceptSegmentTakeWorker(
            project_id=self._project_id,
            chapter_number=self._active_chapter_number,
            take_id=take.take_id,
            settings=self._settings,
            layout=self._layout,
        )
        worker.signals.segment_audio_completed.connect(self._on_segment_take_accepted)
        worker.signals.worker_failed.connect(self._on_worker_failed)
        self._current_worker = worker
        self._begin_tts_operation("segment_accept", self._active_chapter_number)
        self._cancel_btn.setEnabled(True)
        self._room_status_badge.setText("正在接受试听版本…")
        self._room_status_badge.set_tone("warning")
        worker.submit()

    def _on_segment_take_accepted(self, data: dict[str, Any]) -> None:
        """Accept a take, then fast-reassemble so playback can continue in context.

        The accepted segment's audio is already promoted into the formal result
        manifest, but the assembled master is marked ``assembly_stale`` until
        reassembly.  Rather than asking the user to click "重装配" and restart
        from segment 0, we kick off a fast reassemble (reusing the speech
        timeline) and remember the accepted segment so ``_on_audio_completed``
        can resume playback from there once the new master is ready.
        """
        segment_index = int((data.get("segment") or {}).get("segment_index", -1))
        chapter_number = int(data.get("chapter_number") or self._active_chapter_number)
        self._current_worker = None
        self._finish_tts_operation()
        self._load_chapter_artifacts(chapter_number)
        if segment_index >= 0:
            for row in range(self._room_segment_list.count()):
                if (
                    self._room_segment_list.item(row).data(Qt.ItemDataRole.UserRole)
                    == segment_index
                ):
                    self._room_segment_list.setCurrentRow(row)
                    break
        if not self._layout or not self._active_chapter_number:
            self._status_badge.setText(f"第 {segment_index + 1} 段已接受；请在后处理点击“重装配”")
            self._status_badge.set_tone("success")
            self._cancel_btn.setEnabled(False)
            return
        # Trigger a fast reassemble and remember where to resume playback.
        self._resume_after_reassemble_segment = segment_index if segment_index >= 0 else None
        worker = AssembleChapterAudioWorker(
            project_id=self._project_id,
            chapter_number=self._active_chapter_number,
            settings=self._settings,
            layout=self._layout,
            mock=(self._get_current_provider() == "mock"),
            fast=True,
        )
        worker.signals.audio_completed.connect(self._on_audio_completed)
        worker.signals.worker_failed.connect(self._on_worker_failed)
        self._current_worker = worker
        self._begin_tts_operation("assemble", self._active_chapter_number)
        self._cancel_btn.setEnabled(True)
        self._status_badge.setText("已接受新试听，正在快速重新装配以继续播放…")
        self._status_badge.set_tone("warning")

    def _on_discard_segment_take(self) -> None:
        segment_index = self._selected_voice_room_segment_index()
        if segment_index is None or self._studio_service is None:
            return
        take = self._room_candidate_takes.pop(segment_index, None)
        if take is None:
            return
        self._studio_service.reject_candidate_take(
            self._active_chapter_number,
            take.take_id,
        )
        self._room_loaded_audio_path = ""
        self._populate_voice_room_segments()
        self._room_status_badge.setText("已舍弃试听，正式版本未变")
        self._room_status_badge.set_tone("muted")
        self._refresh_workflow_controls()

    def _on_cleanup_redundant_takes(self) -> None:
        """Backward-compatible entry point for the former take-only cleanup."""
        self._on_cleanup_tts_files(default_categories={_TTS_CAT_ORPHAN_CANDIDATES})

    def _on_reassemble_chapter(self) -> None:
        """Build master audio/subtitles from the currently approved individual takes."""
        if not self._layout or not self._active_chapter_number:
            return
        worker = AssembleChapterAudioWorker(
            project_id=self._project_id,
            chapter_number=self._active_chapter_number,
            settings=self._settings,
            layout=self._layout,
            mock=(self._get_current_provider() == "mock"),
        )
        worker.signals.audio_completed.connect(self._on_audio_completed)
        worker.signals.worker_failed.connect(self._on_worker_failed)
        self._current_worker = worker
        self._begin_tts_operation("assemble", self._active_chapter_number)
        self._cancel_btn.setEnabled(True)
        self._status_badge.setText("正在使用已批准分段装配新成品…")
        self._status_badge.set_tone("warning")
        worker.submit()

    def _refresh_workflow_controls(self) -> None:
        """Gate actions by artifacts while keeping every step inspectable."""
        chapter_number = self._active_chapter_number
        chapter_ready = bool(chapter_number and self._load_chapter_text(chapter_number))
        voice_ready = bool(
            self._voice_team and (self._voice_team.entries or self._voice_team.narrator_voice_id)
        )
        script_ready = bool(self._source_script_available and chapter_number)
        script_freshness = self._assess_current_script_freshness()
        script_is_current = script_freshness == DubbingScriptFreshness.CURRENT
        script_editable = bool(
            self._current_script
            and self._current_script.chapter_number == chapter_number
            and script_is_current
        )
        unresolved_speakers = (
            unresolved_speaker_indices(self._current_script) if self._current_script else ()
        )
        script_source_audit_pending = bool(
            self._current_script
            and requires_script_source_audit(
                self._current_script,
                self._load_chapter_text(chapter_number),
            )
        )
        synthesis_ready = (
            script_ready
            and script_is_current
            and not unresolved_speakers
            and not script_source_audit_pending
        )
        current_script_hash = (
            self._current_script.script_hash or compute_dubbing_script_hash(self._current_script)
            if self._current_script is not None
            else ""
        )
        current_audio_script_hash = (
            str(
                self._current_audio_result.metadata.get("script_hash")
                or self._current_audio_result.script.script_hash
                or compute_dubbing_script_hash(self._current_audio_result.script)
            ).strip()
            if self._current_audio_result is not None
            else ""
        )
        audio_matches_current_script = bool(
            current_script_hash and current_audio_script_hash == current_script_hash
        )
        automation_mode = self._current_audio_automation_mode()
        tts_operation_running = self._tts_operation is not None
        audio_ready = bool(
            self._current_audio_result
            and self._current_audio_result.chapter_number == chapter_number
            and audio_matches_current_script
            and self._current_audio_result.delivery_ready
            and not delivery_blocking_reasons(self._current_audio_result)
            and self._current_audio_result.assembled_audio_path
            and Path(self._current_audio_result.assembled_audio_path).is_file()
            and not self._current_audio_result.metadata.get("assembly_stale")
        )
        subtitle_ready = bool(
            self._current_audio_result
            and audio_matches_current_script
            and self._current_audio_result.delivery_ready
            and not delivery_blocking_reasons(self._current_audio_result)
            and self._current_audio_result.subtitle_path
            and Path(self._current_audio_result.subtitle_path).is_file()
            and not self._current_audio_result.metadata.get("assembly_stale")
        )
        if hasattr(self, "_generate_script_btn"):
            self._generate_script_btn.setText(
                "覆盖旧版"
                if script_ready and not script_is_current
                else ("重新生成" if script_ready else "生成脚本")
            )
            self._generate_script_btn.setEnabled(
                chapter_ready and voice_ready and not tts_operation_running
            )
            self._generate_script_btn.setToolTip(
                "根据章节文本、角色档案和配音团队生成生产脚本"
                if voice_ready and not tts_operation_running
                else (
                    "请先完成第 1 步：构建配音团队" if not voice_ready else "当前 TTS 任务仍在运行"
                )
            )
            self._synthesize_from_script_btn.setEnabled(
                synthesis_ready and not tts_operation_running
            )
            self._synthesize_from_script_btn.setToolTip(
                "合成当前脚本"
                if synthesis_ready and not tts_operation_running
                else (
                    "此脚本不属于当前定稿正文；请用当前正文覆盖后再合成"
                    if script_ready and not script_is_current
                    else (
                        "这是旧版未审核脚本；请先重新生成，完成正文引号角色核对"
                        if script_source_audit_pending
                        else (
                            f"还有 {len(unresolved_speakers)} 段说话人待复核；请在脚本编辑器中指定"
                            if unresolved_speakers
                            else (
                                "请先生成或载入当前章节脚本"
                                if not script_ready
                                else "当前 TTS 任务仍在运行"
                            )
                        )
                    )
                )
            )
            if hasattr(self, "_clear_script_chapter_btn"):
                self._clear_script_chapter_btn.setEnabled(
                    script_ready and not tts_operation_running
                )
            if hasattr(self, "_replace_script_btn"):
                self._replace_script_btn.setEnabled(
                    chapter_ready and voice_ready and not tts_operation_running
                )
            if hasattr(self, "_remove_stale_script_btn"):
                self._remove_stale_script_btn.setEnabled(not tts_operation_running)
            if hasattr(self, "_edit_script_btn"):
                self._edit_script_btn.setEnabled(script_editable and not tts_operation_running)
                self._edit_script_btn.setToolTip(
                    "逐段修改台词、情绪、语气与语速；保存后自动使旧音频待重建"
                    if script_editable and not tts_operation_running
                    else (
                        "等待当前 TTS 任务完成后再编辑"
                        if tts_operation_running
                        else "请先生成或载入配音脚本"
                    )
                )
            if hasattr(self, "_edit_sound_design_btn"):
                self._edit_sound_design_btn.setEnabled(
                    script_editable and not tts_operation_running
                )
                self._edit_sound_design_btn.setToolTip(
                    "编辑环境音、音效、BGM 的片段锚点与混音参数"
                    if script_editable and not tts_operation_running
                    else (
                        "等待当前 TTS 任务完成后再编辑声场"
                        if tts_operation_running
                        else "请先生成或载入配音脚本"
                    )
                )
        if hasattr(self, "_synthesize_btn"):
            self._synthesize_btn.setEnabled(synthesis_ready and not tts_operation_running)
            self._synthesize_btn.setToolTip(
                "从已保存脚本合成音频与字幕"
                if synthesis_ready and not tts_operation_running
                else (
                    "此脚本不属于当前定稿正文；请先用当前正文覆盖"
                    if script_ready and not script_is_current
                    else (
                        "这是旧版未审核脚本；请先重新生成，完成正文引号角色核对"
                        if script_source_audit_pending
                        else (
                            f"还有 {len(unresolved_speakers)} 段说话人待复核；请先人工确认"
                            if unresolved_speakers
                            else (
                                "请先完成第 2 步：生成配音脚本"
                                if not script_ready
                                else "当前 TTS 任务仍在运行"
                            )
                        )
                    )
                )
            )
            full_pipeline_ready = chapter_ready and (
                automation_mode != AudioAutomationMode.MANUAL or (voice_ready and synthesis_ready)
            )
            self._full_pipeline_btn.setEnabled(full_pipeline_ready and not tts_operation_running)
            if automation_mode == AudioAutomationMode.MANUAL and not full_pipeline_ready:
                self._full_pipeline_btn.setToolTip(
                    "全人工模式不会自动创建前置产物；请先准备配音团队并审核当前章节脚本。"
                )
            else:
                self._sync_audio_automation_mode_ui()
            self._export_mp3_btn.setEnabled(audio_ready)
            self._export_wav_btn.setEnabled(audio_ready)
            self._export_flac_btn.setEnabled(audio_ready)
            self._export_lufs_btn.setEnabled(audio_ready)
            self._export_srt_btn.setEnabled(subtitle_ready)
            # "播放全章" requires assembled audio (may exist even before delivery-ready).
            has_assembled = bool(
                self._current_audio_result
                and audio_matches_current_script
                and self._current_audio_result.assembled_audio_path
                and Path(self._current_audio_result.assembled_audio_path).is_file()
                and not self._current_audio_result.metadata.get("assembly_stale")
            )
            if hasattr(self, "_room_play_full_btn"):
                self._room_play_full_btn.setEnabled(has_assembled)
            zip_ready = bool(
                self._layout and any(self._layout.tts_dir.glob("results/chapter_*_audio.json"))
            )
            self._export_all_btn.setEnabled(zip_ready)
            self._export_all_srt_btn.setEnabled(zip_ready)
            if hasattr(self, "_reassemble_btn"):
                has_segment_manifest = bool(
                    self._layout
                    and self._layout.tts_audio_result_path(chapter_number).is_file()
                    and audio_matches_current_script
                )
                has_pending_candidates = bool(self._room_candidate_takes)
                self._reassemble_btn.setEnabled(
                    synthesis_ready
                    and has_segment_manifest
                    and not has_pending_candidates
                    and not tts_operation_running
                )
                self._reassemble_btn.setToolTip(
                    "使用已批准的分段音频重新生成全章 MP3 与字幕"
                    if (
                        synthesis_ready
                        and has_segment_manifest
                        and not has_pending_candidates
                        and not tts_operation_running
                    )
                    else (
                        "存在未接受的试听；请先接受或舍弃，避免误以为已装配"
                        if has_pending_candidates
                        else (
                            "旧版脚本或待复核说话人不能参与重装配"
                            if script_ready and not synthesis_ready
                            else (
                                "已保存的分段音频属于另一版脚本，请重新合成"
                                if current_audio_script_hash and not audio_matches_current_script
                                else "请先生成至少一个分段音频，且等待当前任务结束"
                            )
                        )
                    )
                )
            if hasattr(self, "_cleanup_takes_btn"):
                # The chooser covers project-wide stale assets, not only the
                # active chapter's take manifest. Let it open even when no
                # candidate take exists so every cleanup category is visible.
                self._cleanup_takes_btn.setEnabled(
                    self._layout is not None and not tts_operation_running
                )
            has_any_artifact = bool(self._chapter_tts_artifact_group_count(chapter_number))
            if hasattr(self, "_clear_audio_chapter_btn"):
                self._clear_audio_chapter_btn.setEnabled(
                    has_any_artifact and not tts_operation_running
                )
            if hasattr(self, "_batch_clear_chapters_btn"):
                self._batch_clear_chapters_btn.setEnabled(
                    bool(self._chapters_with_tts_artifacts()) and not tts_operation_running
                )
            if hasattr(self, "_reset_project_tts_btn"):
                self._reset_project_tts_btn.setEnabled(
                    self._layout is not None and not tts_operation_running
                )
            if hasattr(self, "_inspect_mix_btn"):
                self._inspect_mix_btn.setEnabled(script_editable)
            if hasattr(self, "_open_sound_library_btn"):
                self._open_sound_library_btn.setEnabled(self._studio_service is not None)

        if hasattr(self, "_room_segment_list"):
            self._render_voice_room_segment()

        # ── Workflow hint: guide the user to the next logical step ──
        if tts_operation_running:
            self._workflow_hint_label.setText("")
        elif not voice_ready:
            self._workflow_hint_label.setText("下一步：在「配音团队」构建或确认角色音色")
        elif not script_ready:
            self._workflow_hint_label.setText("下一步：在「配音脚本」选择章节并生成脚本")
        elif not script_is_current:
            self._workflow_hint_label.setText("提示：脚本与正文不一致，建议重新生成")
        elif not self._current_audio_result:
            self._workflow_hint_label.setText(
                "下一步：在「后处理」执行合成，或在「配音室」逐段录制"
            )
        else:
            self._workflow_hint_label.setText("")

    def _get_current_provider(self) -> str:
        """Get current TTS provider from settings (not hardcoded mock)."""
        return self._settings.tts_default_provider

    def _set_provider_switch_state(self, provider: str, *, loading: bool) -> None:
        spec = provider_ui_spec(provider)
        suffix = " · 读取中" if loading else ""
        if self._provider_dropdown is not None:
            self._provider_dropdown.set_display_text(f"平台 · {spec.label}{suffix}")
            self._provider_dropdown.set_selected(provider)
            self._provider_dropdown.set_loading(loading)
            self._provider_dropdown.setToolTip(f"当前使用 {spec.label}；点击切换 TTS 平台")

    def _switch_provider(self, provider: str) -> None:
        """Switch the active provider immediately without rebuilding the page."""
        provider = provider.strip().lower()
        if provider == self._get_current_provider():
            return
        if self._current_worker is not None:
            self._status_badge.setText("当前任务运行中，请完成或取消后再切换平台")
            self._status_badge.set_tone("warning")
            return
        if self._voice_catalog_worker is not None:
            self._voice_catalog_worker.request_cancel()
            self._voice_catalog_worker = None

        self._settings = self._settings.model_copy(update={"tts_default_provider": provider})
        TTSAdapterRegistry.reset_instance()
        self._system_voices.clear()
        self._provider_capabilities.clear()
        if self._provider_dropdown is not None:
            self._provider_dropdown.set_loading(True)
        self._set_provider_switch_state(provider, loading=True)
        self._capability_hint.setText(f"正在读取 {provider_ui_spec(provider).label} 能力…")

        if hasattr(self, "_model_combo"):
            self._sync_provider_settings_ui(provider)

        char_id = self._get_selected_character_id()
        entry = self._voice_team.get_entry(char_id) if self._voice_team and char_id else None
        self._sync_voice_action_state(entry)
        self._status_badge.setText(
            f"已切换至 {provider_ui_spec(provider).label}；如需长期使用请在平台设置中保存"
        )
        self._status_badge.set_tone("default")
        self._load_system_voices()

    def _provider_feature(self, key: str) -> bool:
        """Return negotiated capability, with conservative built-in defaults."""
        if key in self._provider_capabilities:
            return bool(self._provider_capabilities[key])
        defaults: dict[str, dict[str, bool]] = {
            "minimax": {"voice_clone": True, "voice_design": True},
            "bailian": {"voice_clone": True, "voice_design": True},
            "dashscope": {"voice_clone": True, "voice_design": True},
            "mock": {"voice_clone": True, "voice_design": True},
            "local": {"voice_clone": False, "voice_design": False},
            "tencent": {"voice_clone": False, "voice_design": False},
            "cosyvoice": {"voice_clone": True, "voice_design": False},
            "openvoice": {"voice_clone": True, "voice_design": False},
        }
        return defaults.get(self._get_current_provider(), {}).get(key, False)

    def _sync_voice_action_state(self, entry: VoiceCastEntry | None) -> None:
        has_entry = entry is not None
        is_narrator = self._get_selected_character_id() == _NARRATOR_ID
        build_running = self._voice_team_build_running()
        clone_supported = self._provider_feature("voice_clone")
        design_supported = self._provider_feature("voice_design")
        self._clone_voice_btn.setVisible(clone_supported and not is_narrator)
        self._clone_voice_btn.setEnabled(
            has_entry and clone_supported and not is_narrator and not build_running
        )
        self._design_voice_btn.setText("重新构建旁白音色" if is_narrator else "AI 音色设计")
        self._design_voice_btn.setToolTip(
            "让 LLM 生成旁白画像，并据此构建可试听的作品级音色"
            if is_narrator
            else "使用角色画像和编辑声纹生成可复用专属音色；部分平台会对试听文本计费"
        )
        self._design_voice_btn.setVisible(is_narrator or design_supported)
        self._design_voice_btn.setEnabled(
            (is_narrator or (has_entry and design_supported)) and not build_running
        )
        # Approve button: only for non-narrator cast entries still pending audition.
        approve_visible = (
            has_entry
            and not is_narrator
            and entry is not None
            and entry.approval_status == "pending"
        )
        self._approve_voice_btn.setVisible(approve_visible)
        self._approve_voice_btn.setEnabled(approve_visible and not build_running)
        if is_narrator:
            preview_ready = bool(
                self._narrator_voice_id()
                and self._narrator_profile
                and not self._narrator_profile.is_expired
                and self._narrator_profile.provider.value == self._get_current_provider()
            )
        else:
            preview_ready = bool(
                entry
                and entry.voice_id
                and not entry.is_expired
                and entry.provider.value == self._get_current_provider()
            )
        self._preview_btn.setEnabled(preview_ready and not build_running)
        if is_narrator:
            self._match_badge.setText("作品级旁白")
            self._match_badge.setProperty("tone", "good" if preview_ready else "muted")
            self._preview_status_label.setText(
                "已分配旁白音色，可生成或播放试听" if preview_ready else "旁白音色尚未就绪"
            )
            self._preview_btn.setText("播放 / 生成试听")
        elif entry is not None:
            if entry.llm_adjudication_status == "recast":
                confidence = entry.llm_adjudication_confidence or 0.0
                self._match_badge.setText(f"LLM 改配 {confidence:.0%}")
                match_tone = "good"
            elif entry.llm_adjudication_status == "needs_audition":
                self._match_badge.setText("待对比试听")
                match_tone = "warning"
            elif entry.llm_adjudication_status == "rejected":
                self._match_badge.setText("候选已否决")
                match_tone = "warning"
            elif entry.match_score is None:
                self._match_badge.setText("人工确认")
                match_tone = "muted"
            else:
                self._match_badge.setText(f"画像匹配 {entry.match_score:.0%}")
                match_tone = "good" if entry.match_score >= 0.8 else "warning"
            self._match_badge.setProperty("tone", match_tone)
            variant_key = str(self._preview_sample_combo.currentData() or "identity")
            vp = entry.get_variant_preview(variant_key)
            if variant_key == "identity":
                # identity 变体只需有台词文本即可（台词可能来自角色画像，不必与样本完全一致）
                preview_exists = bool(vp.audio_path and vp.text and Path(vp.audio_path).is_file())
            else:
                preview_exists = bool(
                    vp.audio_path
                    and vp.text == self._selected_voice_preview_text(entry.character_id)
                    and Path(vp.audio_path).is_file()
                )
            if preview_exists:
                if entry.llm_adjudication_status == "needs_audition":
                    count = len(entry.audition_candidate_voice_ids)
                    self._preview_status_label.setText(
                        f"当前试听已准备 · LLM 建议对比 {count} 个候选"
                    )
                else:
                    self._preview_status_label.setText("试听已准备 · 双击左侧角色也可直接播放")
                self._preview_btn.setText("播放试听")
            elif vp.error:
                self._preview_status_label.setText("自动试听准备失败 · 可点击重试")
                self._preview_btn.setText("重试试听")
            else:
                self._preview_status_label.setText("尚未生成试听 · 将按当前参数缓存")
                self._preview_btn.setText("生成试听")
        else:
            self._match_badge.setText("匹配待评估")
            self._match_badge.setProperty("tone", "muted")
            self._preview_status_label.setText("选择已分配音色的角色后试听")
            self._preview_btn.setText("生成试听")
        self._match_badge.style().unpolish(self._match_badge)
        self._match_badge.style().polish(self._match_badge)
        parameters_enabled = (is_narrator or has_entry) and not build_running
        self._preview_sample_combo.setEnabled(has_entry and not build_running)
        self._voice_combo.setEnabled(not build_running)
        self._apply_offset_btn.setEnabled(parameters_enabled)
        self._reset_offsets_btn.setEnabled(parameters_enabled)
        for slider in (self._speed_slider, self._pitch_slider, self._vol_slider):
            slider.setEnabled(parameters_enabled)
        if build_running:
            self._preview_btn.setToolTip(
                "自动组建正在使用本地音频模型；完成或取消后可试听，已加载音频仍可播放"
            )
        elif (
            is_narrator
            and self._narrator_profile
            and (self._narrator_profile.provider.value != self._get_current_provider())
        ):
            self._preview_btn.setToolTip("旁白音色属于其他平台，请先选择当前平台的旁白音色")
        elif entry and entry.provider.value != self._get_current_provider():
            self._preview_btn.setToolTip("该音色属于其他平台，请先重新匹配、克隆或选择当前平台音色")
        else:
            self._preview_btn.setToolTip("按当前角色音色与速度、音高、音量参数合成试听")

    def _selected_voice_preview_text(self, character_id: str) -> str:
        character = self._selected_bible_character(character_id)
        if not character:
            entry = self._voice_team.get_entry(character_id) if self._voice_team else None
            character = {
                "character_id": character_id,
                "name": entry.character_name if entry else "这个角色",
            }
        samples = build_voice_preview_samples(character)
        variant = str(self._preview_sample_combo.currentData() or "identity")
        return samples.get(variant, samples["identity"])

    def _preview_matches_selected_variant(self, entry: VoiceCastEntry) -> bool:
        """Accept legacy identity auditions while keeping new context samples isolated."""

        variant = str(self._preview_sample_combo.currentData() or "identity")
        vp = entry.get_variant_preview(variant)
        if not vp.text:
            return False
        if variant == "identity":
            # identity 变体只需有台词文本即可
            return True
        return vp.text == self._selected_voice_preview_text(entry.character_id)

    def _voice_team_build_running(self) -> bool:
        """Return whether automatic cast construction owns the audio model slot."""
        return isinstance(self._current_worker, BuildVoiceTeamWorker)

    def _reject_during_voice_team_build(self, action: str) -> bool:
        """Prevent model work and voice mutations from racing automatic construction."""
        if not self._voice_team_build_running():
            return False
        self._status_badge.setText(f"自动组建正在使用本地音频模型，请完成或取消后再{action}")
        self._status_badge.set_tone("warning")
        return True

    def _refresh_selected_voice_action_state(self) -> None:
        """Recompute controls for the selected narrator or character."""
        char_id = self._get_selected_character_id()
        entry = self._voice_team.get_entry(char_id) if self._voice_team and char_id else None
        self._sync_voice_action_state(entry)

    def _on_provider_status(self, payload: dict[str, Any]) -> None:
        """Apply negotiated provider features to labels and action availability."""
        capabilities = payload.get("capabilities", {})
        provider = str(payload.get("provider") or self._get_current_provider())
        if provider != self._get_current_provider():
            return
        self._provider_capabilities = dict(capabilities) if isinstance(capabilities, dict) else {}
        self._set_provider_switch_state(provider, loading=False)
        labels = ["语音合成"]
        if self._provider_feature("system_voice_catalog"):
            labels.append("音色目录")
        if self._provider_feature("voice_design"):
            labels.append("特征设计")
        if self._provider_feature("voice_clone"):
            labels.append("参考音频克隆")
        raw_features = self._provider_capabilities.get("synthesis_features", [])
        feature_labels = {
            "emotion": "原生情绪",
            "paralinguistic": "停顿/气息",
            "pronunciation": "发音字典",
            "language_boost": "方言增强",
            "voice_effects": "声音效果器",
            "ssml": "SSML",
            "instruction_control": "指令式表演",
        }
        if isinstance(raw_features, (list, set, tuple)):
            labels.extend(
                feature_labels[value]
                for value in (str(item) for item in raw_features)
                if value in feature_labels
            )
        self._capability_hint.setText(
            f"{provider_ui_spec(provider).label} 可用能力 · " + " / ".join(labels)
        )
        self._voice_select_row.setVisible(self._provider_feature("system_voice_catalog"))
        if self._provider_feature("voice_clone"):
            if self._provider_feature("local_reference_audio"):
                self._clone_voice_btn.setText("上传参考音频")
            else:
                self._clone_voice_btn.setText("输入参考文件 ID")
        char_id = self._get_selected_character_id()
        entry = self._voice_team.get_entry(char_id) if self._voice_team and char_id else None
        self._sync_voice_action_state(entry)

    # ─── Private slots: Voice team ─────────────────────────────────────────

    def _on_character_selected(self, current: QListWidgetItem | None, previous: Any) -> None:
        """Update voice info panel when a character is selected."""
        if not current:
            self._show_default_voice_info()
            self._sync_voice_action_state(None)
            return
        char_id = current.data(Qt.ItemDataRole.UserRole)
        if not char_id:
            self._show_default_voice_info()
            self._sync_voice_action_state(None)
            return

        if char_id == _NARRATOR_ID:
            self._render_narrator_info()
            if self._narrator_profile is not None:
                self._sync_sliders_to_narrator(self._narrator_profile)
            else:
                self._reset_sliders()
            self._load_voice_combo_for_entry(None, voice_id=self._narrator_voice_id())
            self._sync_voice_action_state(None)
            return

        # Try voice team first
        if self._voice_team:
            entry = self._voice_team.get_entry(char_id)
            if entry:
                self._render_entry_info(entry)
                self._sync_sliders_to_entry(entry)
                self._load_voice_combo_for_entry(entry)
                self._sync_voice_action_state(entry)
                return

        # Fall back to bible character info
        for char in self._bible_characters:
            cid = char.get("character_id", char.get("name", ""))
            if cid == char_id:
                self._render_bible_char_info(char)
                self._reset_sliders()
                self._load_voice_combo_for_entry(None)
                self._sync_voice_action_state(None)
                return

        self._show_default_voice_info()
        self._sync_voice_action_state(None)

    def _on_character_activated(self, item: QListWidgetItem) -> None:
        """Make the discoverable list shortcut play the selected role audition."""
        if not item.data(Qt.ItemDataRole.UserRole):
            return
        if self._character_list.currentItem() is not item:
            self._character_list.setCurrentItem(item)
        self._on_preview_voice()

    def _narrator_voice_id(self) -> str:
        """Return the effective narrator voice from profile, team, or settings."""
        if self._narrator_profile and self._narrator_profile.is_expired:
            return self._settings.tts_narrator_voice_id.strip()
        return str(
            (self._narrator_profile.voice_id if self._narrator_profile else "")
            or (self._voice_team.narrator_voice_id if self._voice_team else "")
            or self._settings.tts_narrator_voice_id
        ).strip()

    def _render_narrator_info(self) -> None:
        """Render the narrator's persisted LLM design and voice assignment."""
        profile = self._narrator_profile
        if profile is None:
            html = (
                f"<div style='font-family:{_HTML_BODY_FONT_FAMILY}; font-size: 11pt; line-height: 1.65;'>"
                "<h2>旁白</h2><p><b>待构建作品级音色</b></p>"
                "<p>构建配音团队时，系统会先让 LLM 结合故事大纲、体裁、整体氛围、"
                "故事圣经与风格档案生成旁白画像，再用当前平台实际构建专属音色。"
                "也可点击“重新构建旁白音色”。</p></div>"
            )
        else:
            keywords = "、".join(profile.style_keywords) or "（未提供）"
            distance = {"close": "贴近角色", "medium": "适度距离", "distant": "全知视角"}.get(
                profile.narration_distance, profile.narration_distance
            )
            expression = {"wide": "宽广", "moderate": "适度", "restrained": "克制"}.get(
                profile.emotional_range, profile.emotional_range
            )
            source = {
                "designed": "作品特征设计",
                "manual": "人工指定",
                "system": "系统目录匹配",
            }.get(
                profile.voice_source,
                profile.voice_source,
            )
            status = "已过期，需要重新构建" if profile.is_expired else "已就绪"
            expires = (
                profile.expires_at.isoformat(timespec="minutes")
                if profile.expires_at
                else "长期有效"
            )
            html = (
                f"<div style='font-family:{_HTML_BODY_FONT_FAMILY}; font-size: 11pt; line-height: 1.65;'>"
                "<h2>旁白</h2><p><b>作品级旁白</b> · 画像来源：大纲、故事氛围、"
                "故事圣经与风格档案</p><table cellspacing='5'>"
                f"<tr><td><b>状态</b></td><td>{html_lib.escape(status)}</td></tr>"
                f"<tr><td><b>音色类型</b></td><td>{html_lib.escape(profile.voice_type or '待补全')}</td></tr>"
                f"<tr><td><b>音色 ID</b></td><td>{html_lib.escape(self._narrator_voice_id() or '(待选择)')}</td></tr>"
                f"<tr><td><b>平台</b></td><td>{html_lib.escape(profile.provider.value)}</td></tr>"
                f"<tr><td><b>音色来源</b></td><td>{html_lib.escape(source)}</td></tr>"
                f"<tr><td><b>有效期</b></td><td>{html_lib.escape(expires)}</td></tr>"
                f"<tr><td><b>基础语速</b></td><td>{profile.base_speed:.2f}（范围 "
                f"{profile.speed_range_low:.2f}–{profile.speed_range_high:.2f}）</td></tr>"
                f"<tr><td><b>情感表达</b></td><td>{html_lib.escape(expression)}</td></tr>"
                f"<tr><td><b>叙述距离</b></td><td>{html_lib.escape(distance)}</td></tr>"
                f"<tr><td><b>风格关键词</b></td><td>{html_lib.escape(keywords)}</td></tr>"
                "</table>"
                + (
                    f"<p><b>设计说明</b><br>{html_lib.escape(profile.notes)}</p>"
                    if profile.notes
                    else ""
                )
                + (
                    f"<p><b>试听文本</b><br>{html_lib.escape(profile.sample_narration_text)}</p>"
                    if profile.sample_narration_text
                    else ""
                )
                + (
                    f"<p><b>音色构建设计简报</b><br>{html_lib.escape(profile.voice_design_prompt)}</p>"
                    if profile.voice_design_prompt
                    else ""
                )
                + "</div>"
            )
        update_browser_html(self._voice_info_text, html)
        self._voice_empty.setVisible(False)
        self._voice_info_text.setVisible(True)

    def _render_entry_info(self, entry: VoiceCastEntry) -> None:
        """Render an auditable explanation of how the character voice was created."""
        status_label = {
            VoiceCloneStatus.READY: "已就绪",
            VoiceCloneStatus.PENDING: "待分配",
            VoiceCloneStatus.CLONING: "克隆中",
            VoiceCloneStatus.EXPIRED: "已过期",
            VoiceCloneStatus.FAILED: "失败",
        }.get(entry.clone_status, "未知")
        source_label = {
            "designed": "角色特征生成",
            "cloned": "参考音频克隆",
            "manual": "人工指定",
            "system": "系统目录匹配",
            "library": "全局音色库",
        }.get(entry.voice_source, entry.voice_source)
        character = next(
            (
                item
                for item in self._bible_characters
                if item.get("character_id", item.get("name", "")) == entry.character_id
            ),
            {},
        )
        gender_label = {
            "male": "男声",
            "female": "女声",
            "neutral": "中性",
        }.get(str(character.get("gender") or "").strip().lower(), character.get("gender", ""))
        traits = " · ".join(
            str(value or "").strip()
            for value in (
                self._voice_role_label(character.get("role")),
                gender_label,
                character.get("age"),
                character.get("personality"),
            )
            if str(value or "").strip()
        )
        design_brief = entry.voice_design_prompt or entry.clone_prompt
        expires = entry.expires_at.isoformat(timespec="minutes") if entry.expires_at else "长期有效"
        activation = (
            f"需在 {entry.activation_deadline.isoformat(timespec='minutes')} 前首次正式合成"
            if entry.activation_deadline
            else "已激活 / 无需激活"
        )
        match_label = (
            f"{entry.match_score:.0%}" if entry.match_score is not None else "人工分配 / 未评估"
        )
        quality_label = f"{entry.quality_score:.0%}" if entry.quality_score > 0 else "尚未测评"
        adjudication_label = {
            "not_reviewed": "未触发",
            "approved": "LLM 确认当前音色",
            "recast": "LLM 已在白名单内改配",
            "needs_audition": "需对比试听",
            "rejected": "LLM 否决全部候选",
        }.get(entry.llm_adjudication_status, entry.llm_adjudication_status)
        if entry.llm_adjudication_confidence is not None:
            adjudication_label += f" · {entry.llm_adjudication_confidence:.0%}"
        audition_ids = "、".join(entry.audition_candidate_voice_ids) or "无"
        catalog_status = "未同步"
        if self._voice_team:
            if self._voice_team.voice_catalog_sync_status == "live":
                synced_at = self._voice_team.voice_catalog_synced_at
                catalog_status = (
                    f"已与供应商同步 · {synced_at.strftime('%Y-%m-%d %H:%M')}"
                    if synced_at
                    else "已与供应商同步"
                )
            elif self._voice_team.voice_catalog_sync_status == "unavailable":
                catalog_status = "供应商目录暂时不可用，未利用缓存冒充最新数据"
        reasons_html = "".join(
            f"<li>{html_lib.escape(reason)}</li>" for reason in entry.match_reasons
        )
        warnings_html = "".join(
            f"<li>{html_lib.escape(warning)}</li>" for warning in entry.match_warnings
        )
        performance_profile = voice_performance_profile(entry)
        configured = performance_profile.configured_offsets
        manual_fields = performance_profile.manual_overrides.active_fields
        offset_source = (
            f"人工覆盖：{'、'.join(self._performance_field_labels(manual_fields))}"
            if manual_fields
            else "自动·声纹稳定"
        )
        conditional_directions = "；".join(
            f"{item.trigger} → {self._performance_direction_label(item.direction)}"
            for item in performance_profile.conditional_directions
        )
        conditional_html = (
            f"<tr><td><b>情境表演</b></td><td>{html_lib.escape(conditional_directions)}</td></tr>"
            if conditional_directions
            else ""
        )
        html = (
            f"<div style='font-family:{_HTML_BODY_FONT_FAMILY}; font-size: 11pt; line-height: 1.65;'>"
            f"<h2>{html_lib.escape(entry.character_name)}</h2>"
            f"<p><b>{html_lib.escape(status_label)}</b> · {html_lib.escape(source_label)}</p>"
            "<table cellspacing='5'>"
            f"<tr><td><b>音色 ID</b></td><td>{html_lib.escape(entry.voice_id or '(未设置)')}</td></tr>"
            f"<tr><td><b>平台</b></td><td>{html_lib.escape(entry.provider.value)}</td></tr>"
            f"<tr><td><b>角色依据</b></td><td>{html_lib.escape(traits or '等待上游角色画像')}</td></tr>"
            f"<tr><td><b>表达参数</b></td><td>{html_lib.escape(offset_source)} · "
            f"语速 {configured.speed_offset:+.2f} · 音调 {configured.pitch_offset:+d} · "
            f"音量 {configured.vol_offset:+.2f}</td></tr>"
            f"{conditional_html}"
            f"<tr><td><b>有效期</b></td><td>{html_lib.escape(expires)}</td></tr>"
            f"<tr><td><b>激活状态</b></td><td>{html_lib.escape(activation)}</td></tr>"
            f"<tr><td><b>画像匹配</b></td><td>{html_lib.escape(match_label)}</td></tr>"
            f"<tr><td><b>LLM 选角</b></td><td>{html_lib.escape(adjudication_label)}</td></tr>"
            f"<tr><td><b>对比试听候选</b></td><td>{html_lib.escape(audition_ids)}</td></tr>"
            f"<tr><td><b>合成质量</b></td><td>{html_lib.escape(quality_label)}</td></tr>"
            f"<tr><td><b>目录新鲜度</b></td><td>{html_lib.escape(catalog_status)}</td></tr>"
            f"</table>"
            + (f"<p><b>为什么匹配</b></p><ul>{reasons_html}</ul>" if reasons_html else "")
            + (f"<p><b>试听时重点确认</b></p><ul>{warnings_html}</ul>" if warnings_html else "")
            + (
                f"<p><b>试听台词</b><br>{html_lib.escape(entry.get_variant_preview('identity').text or entry.preview_text)}</p>"
                if (entry.get_variant_preview("identity").text or entry.preview_text)
                else ""
            )
            + (
                f"<p><b>音色设计简报</b><br>{html_lib.escape(design_brief)}</p>"
                if design_brief
                else ""
            )
            + "</div>"
        )
        update_browser_html(self._voice_info_text, html)
        self._voice_empty.setVisible(False)
        self._voice_info_text.setVisible(True)

    def _render_bible_char_info(self, char: dict[str, Any]) -> None:
        """Render info for a character not yet in the voice team."""
        role_labels = {
            "protagonist": "主角",
            "deuteragonist": "副主",
            "antagonist": "反派",
            "supporting": "配角",
            "minor": "龙套",
        }
        badges: list[str] = []
        role = char.get("role", "")
        if role:
            role_text = str(role)
            badges.append(f"<b>{html_lib.escape(role_labels.get(role_text, role_text))}</b>")
        gender = char.get("gender", "")
        if gender:
            badges.append(f"<b>{html_lib.escape(str(gender))}</b>")
        age = char.get("age", "")
        if age:
            badges.append(f"<b>{html_lib.escape(str(age))}</b>")
        badge_row = " ".join(badges)
        voice_desc = char.get("voice", "")
        voice_html = (
            f"<p><b>上游声纹描述</b><br>{html_lib.escape(str(voice_desc or '(无)'))}</p>"
            if voice_desc
            else ""
        )
        html = (
            f"<div style='font-family:{_HTML_BODY_FONT_FAMILY}; font-size: 11pt; line-height: 1.65;'>"
            f"<h2>{html_lib.escape(str(char.get('name', '未知')))}</h2>"
            f"<p>{badge_row}</p>"
            f"{voice_html}"
            f"<p>尚未分配音色。构建团队后将优先根据上述角色特质生成专属音色。</p>"
            f"</div>"
        )
        update_browser_html(self._voice_info_text, html)
        self._voice_empty.setVisible(False)
        self._voice_info_text.setVisible(True)

    def _sync_sliders_to_entry(self, entry: VoiceCastEntry) -> None:
        """Set controls from the canonical automatic/manual performance profile."""
        profile = voice_performance_profile(entry)
        configured = profile.configured_offsets
        self._speed_slider.blockSignals(True)
        self._pitch_slider.blockSignals(True)
        self._vol_slider.blockSignals(True)
        self._speed_slider.setValue(round(configured.speed_offset * 100))
        self._pitch_slider.setValue(configured.pitch_offset)
        self._vol_slider.setValue(round(configured.vol_offset * 100))
        self._speed_label.setText(
            f"{self._effective_character_speed(configured.speed_offset):.2f}×"
        )
        self._pitch_label.setText(f"{configured.pitch_offset:+d} st")
        self._vol_label.setText(f"{1.0 + configured.vol_offset:.2f}×")
        self._speed_slider.blockSignals(False)
        self._pitch_slider.blockSignals(False)
        self._vol_slider.blockSignals(False)
        self._performance_dirty_fields.clear()
        self._restore_auto_performance = False
        self._update_performance_policy_label(entry)
        self._apply_offset_btn.setText("应用参数")

    def _sync_sliders_to_narrator(self, profile: NarratorVoiceProfile) -> None:
        """Show the persisted narrator controls using the same units as character voices."""
        self._speed_slider.blockSignals(True)
        self._pitch_slider.blockSignals(True)
        self._vol_slider.blockSignals(True)
        self._speed_slider.setValue(round((profile.base_speed - 1.0) * 100))
        self._pitch_slider.setValue(profile.pitch_offset)
        self._vol_slider.setValue(round(profile.vol_offset * 100))
        self._speed_label.setText(f"{profile.base_speed:.2f}×")
        self._pitch_label.setText(f"{profile.pitch_offset:+d} st")
        self._vol_label.setText(f"{1.0 + profile.vol_offset:.2f}×")
        self._speed_slider.blockSignals(False)
        self._pitch_slider.blockSignals(False)
        self._vol_slider.blockSignals(False)
        self._performance_dirty_fields.clear()
        self._restore_auto_performance = False
        self._performance_policy_label.setText("旁白参数为作品级设置，不使用角色短句稳定策略。")
        self._apply_offset_btn.setText("应用参数")

    def _reset_sliders(self) -> None:
        """Reset sliders to zero."""
        self._speed_slider.blockSignals(True)
        self._pitch_slider.blockSignals(True)
        self._vol_slider.blockSignals(True)
        self._speed_slider.setValue(0)
        self._pitch_slider.setValue(0)
        self._vol_slider.setValue(0)
        self._speed_label.setText(f"{self._settings.tts_default_speed:.2f}×")
        self._pitch_label.setText("+0 st")
        self._vol_label.setText("1.00×")
        self._speed_slider.blockSignals(False)
        self._pitch_slider.blockSignals(False)
        self._vol_slider.blockSignals(False)
        self._performance_dirty_fields.clear()
        self._restore_auto_performance = False
        self._performance_policy_label.setText(
            "自动策略会保持角色声纹稳定；只有拖动过的字段会转为人工覆盖。"
        )
        self._apply_offset_btn.setText("应用参数")

    def _on_speed_slider_changed(self, value: int) -> None:
        effective = self._effective_character_speed(value / 100)
        self._speed_label.setText(f"{effective:.2f}×")
        self._performance_dirty_fields.add("speed")
        self._restore_auto_performance = False
        if effective < 0.9:
            self._set_pending_performance_policy(
                "低于 0.90× 容易拖腔，建议先用情境表演指令表达迟疑。"
            )
        elif effective > 1.15:
            self._set_pending_performance_policy("高于 1.15× 可能损伤吐字与声纹稳定。")
        else:
            self._set_pending_performance_policy()
        self._apply_offset_btn.setText("应用参数 *")

    def _effective_character_speed(self, offset: float) -> float:
        return max(0.5, min(2.0, self._settings.tts_default_speed + offset))

    def _on_pitch_slider_changed(self, value: int) -> None:
        self._pitch_label.setText(f"{value:+d} st")
        self._performance_dirty_fields.add("pitch")
        self._restore_auto_performance = False
        self._set_pending_performance_policy(
            "超过 ±3 半音可能改变角色声纹。" if abs(value) > 3 else ""
        )
        self._apply_offset_btn.setText("应用参数 *")

    def _on_volume_slider_changed(self, value: int) -> None:
        self._vol_label.setText(f"{1.0 + value / 100:.2f}×")
        self._performance_dirty_fields.add("volume")
        self._restore_auto_performance = False
        self._set_pending_performance_policy()
        self._apply_offset_btn.setText("应用参数 *")

    def _set_pending_performance_policy(self, warning: str = "") -> None:
        labels = "、".join(self._performance_field_labels(self._performance_dirty_fields))
        suffix = f" {warning}" if warning else ""
        scope = "旁白参数" if self._get_selected_character_id() == _NARRATOR_ID else "人工覆盖"
        self._performance_policy_label.setText(f"待应用：{scope} {labels}。{suffix}".strip())

    def _on_reset_offsets(self) -> None:
        """Clear character overrides and preview the current automatic baseline."""
        char_id = self._get_selected_character_id()
        if char_id == _NARRATOR_ID:
            self._speed_slider.setValue(0)
            self._pitch_slider.setValue(0)
            self._vol_slider.setValue(0)
            self._status_badge.setText("已恢复旁白项目默认参数；应用后生效")
            self._status_badge.set_tone("default")
            return
        entry = self._voice_team.get_entry(char_id) if self._voice_team and char_id else None
        if entry is None:
            return
        character = self._selected_bible_character(char_id)
        profile = (
            derive_voice_performance_profile(character)
            if character
            else voice_performance_profile(entry).model_copy(
                update={"manual_overrides": VoicePerformanceOverrides()}
            )
        )
        baseline = profile.automatic_baseline
        for slider in (self._speed_slider, self._pitch_slider, self._vol_slider):
            slider.blockSignals(True)
        self._speed_slider.setValue(round(baseline.speed_offset * 100))
        self._pitch_slider.setValue(baseline.pitch_offset)
        self._vol_slider.setValue(round(baseline.vol_offset * 100))
        for slider in (self._speed_slider, self._pitch_slider, self._vol_slider):
            slider.blockSignals(False)
        self._speed_label.setText(f"{self._effective_character_speed(baseline.speed_offset):.2f}×")
        self._pitch_label.setText(f"{baseline.pitch_offset:+d} st")
        self._vol_label.setText(f"{1.0 + baseline.vol_offset:.2f}×")
        self._performance_dirty_fields = {"speed", "pitch", "volume"}
        self._restore_auto_performance = True
        self._apply_offset_btn.setText("应用参数 *")
        self._performance_policy_label.setText("待应用：解除所有人工覆盖，恢复自动基线。")
        self._status_badge.setText("已恢复自动表达策略；应用后将重新生成试听")
        self._status_badge.set_tone("default")

    def _selected_bible_character(self, character_id: str) -> dict[str, Any]:
        return next(
            (
                dict(item)
                for item in self._bible_characters
                if str(item.get("character_id") or item.get("name") or "") == character_id
            ),
            {},
        )

    @staticmethod
    def _performance_field_labels(fields: set[str]) -> list[str]:
        labels = {"speed": "语速", "pitch": "音调", "volume": "音量"}
        return [labels[field] for field in ("speed", "pitch", "volume") if field in fields]

    @staticmethod
    def _performance_direction_label(direction: str) -> str:
        return {
            "slow_down": "放慢",
            "speed_up": "加快",
            "pause_more": "增加停顿",
            "lower_volume": "压低音量",
            "raise_volume": "提高音量",
        }.get(direction, direction)

    def _update_performance_policy_label(self, entry: VoiceCastEntry) -> None:
        profile = voice_performance_profile(entry)
        manual_fields = profile.manual_overrides.active_fields
        source = (
            f"人工覆盖：{'、'.join(self._performance_field_labels(manual_fields))}；其余字段自动"
            if manual_fields
            else "全部字段由自动基线管理"
        )
        context_count = len(profile.conditional_directions)
        context = f"已保留 {context_count} 条情境表演指令；" if context_count else ""
        self._performance_policy_label.setText(
            f"{source}；{context}短句自动保持自然语速与原始声纹。"
        )

    def _load_voice_combo_for_entry(
        self,
        entry: VoiceCastEntry | None,
        *,
        voice_id: str = "",
    ) -> None:
        """Populate the voice combo with system voices, pre-selecting the entry's voice.

        If the system voice catalog has not been loaded yet, trigger an async
        load and show a placeholder; the combo is repopulated in
        ``_on_voices_listed`` once loaded.
        """
        if not self._system_voices:
            self._voice_combo.blockSignals(True)
            self._voice_combo.clear()
            self._voice_combo.addItem("（加载中...）", "")
            self._voice_combo.blockSignals(False)
            # Trigger one-time async load (only if not already loading)
            if self._voice_catalog_worker is None:
                self._load_system_voices()
            return

        self._voice_combo.blockSignals(True)
        self._voice_combo.clear()
        self._voice_combo.addItem("- 不修改 -", "")
        current_id = voice_id or (entry.voice_id if entry else "")
        select_idx = 0
        for i, v in enumerate(self._system_voices):
            vid = v.get("voice_id", "")
            name = v.get("name", vid)
            tag_str = "/".join(v.get("tags", []))
            label = f"{name} ({vid})"
            if tag_str:
                label += f" [{tag_str}]"
            self._voice_combo.addItem(label, vid)
            if vid and vid == current_id:
                select_idx = i + 1
        self._voice_combo.setCurrentIndex(select_idx)
        self._voice_combo.blockSignals(False)

    def _show_default_voice_info(self) -> None:
        """Show default information when no character is selected."""
        if self._bible_characters:
            html = (
                f"<div style='font-family:{_HTML_BODY_FONT_FAMILY}; font-size: 11pt; line-height: 1.7; "
                "text-align: center; padding: 24px;'>"
                "<div style='font-size: 14pt; margin-bottom: 8px;'>选择一个角色</div>"
                f"<div style='color:{qcolor_hex('text.muted')};'>查看角色特质、音色来源与表达参数；"
                "尚未配置时可直接构建配音团队。</div></div>"
            )
            update_browser_html(self._voice_info_text, html)
        else:
            update_browser_html(
                self._voice_info_text,
                f"<div style='color:{qcolor_hex('text.muted')}; text-align:center; padding:30px; font-size:12pt;'>"
                "未找到角色数据<br><br>请确保项目已完成长篇初始化<br>"
                "character_bible.json 中包含角色信息</div>",
            )
        self._voice_info_text.setVisible(True)

    def _on_voice_combo_changed(self, index: int) -> None:
        """Assign a catalog voice to the selected character or narrator."""
        if self._voice_combo_loading:
            return
        if self._reject_during_voice_team_build("修改音色"):
            return
        if index <= 0:
            return  # placeholder or "- 不修改 -"
        voice_id = self._voice_combo.itemData(index)
        if not voice_id:
            return
        char_id = self._get_selected_character_id()
        if not char_id:
            return
        if char_id == _NARRATOR_ID:
            self._persist_narrator_voice_selection(str(voice_id))
            return
        if not self._voice_team:
            return
        entry = self._voice_team.get_entry(char_id)
        if not entry:
            self._status_badge.setText("该角色尚未加入配音团队，请先构建团队")
            self._status_badge.set_tone("warning")
            return
        # Update entry's voice_id via model_copy (Pydantic) and persist
        updated_entry = entry.model_copy(
            update={
                "voice_id": voice_id,
                "clone_status": VoiceCloneStatus.READY,
                "provider": TTSProvider(self._get_current_provider()),
                "voice_source": "manual",
                "match_score": None,
                "match_reasons": ["使用作者明确指定的系统音色"],
                "match_warnings": ["请通过试听人工确认角色特质与指定音色是否一致"],
                "llm_adjudication_status": "not_reviewed",
                "llm_adjudication_confidence": None,
                "llm_adjudication_reason": "",
                "audition_candidate_voice_ids": [],
                "preview_audio_path": "",
                "preview_text": "",
                "preview_error": "",
                "preview_variants": {},
            }
        )
        self._voice_team.entries = [
            updated_entry if e.character_id == char_id else e for e in self._voice_team.entries
        ]
        self._persist_voice_team()
        self._render_entry_info(updated_entry)
        self._update_rebuilt_character_rows([char_id])
        self._sync_voice_action_state(updated_entry)
        self._status_badge.setText(f"音色已更新: {voice_id}")
        self._status_badge.set_tone("success")

    def _persist_narrator_voice_selection(self, voice_id: str) -> None:
        """Persist a narrator catalog choice in both narrator and team contracts."""
        if not self._layout:
            return
        provider = TTSProvider(self._get_current_provider())
        profile = self._narrator_profile or NarratorVoiceProfile(provider=provider)
        self._narrator_profile = profile.model_copy(
            update={
                "voice_id": voice_id,
                "provider": provider,
                "voice_source": "manual",
                "expires_at": None,
            }
        )
        atomic_write_json(
            self._layout.tts_narrator_profile_path,
            self._narrator_profile.model_dump(mode="json"),
        )
        if self._voice_team is None:
            self._voice_team = VoiceTeamContract(
                narrator_voice_id=voice_id,
                narrator_provider=provider,
                default_provider=provider,
            )
        else:
            self._voice_team.narrator_voice_id = voice_id
            self._voice_team.narrator_provider = provider
        self._persist_voice_team()
        self._update_character_list()
        self._render_narrator_info()
        self._status_badge.setText(f"旁白音色已更新: {voice_id}")
        self._status_badge.set_tone("success")

    def _load_system_voices(self) -> None:
        """Async-load system voices from the current provider to populate the combo."""
        requested_provider = self._get_current_provider()
        if self._voice_catalog_worker is not None:
            self._voice_catalog_worker.request_cancel()
            self._voice_catalog_worker = None
        worker = ListVoicesWorker(
            settings=self._settings,
            provider=requested_provider,
            mock=(requested_provider == "mock"),
        )
        worker.signals.voices_listed.connect(
            lambda voices, provider=requested_provider, active_worker=worker: (
                self._on_voices_listed(
                    voices,
                    provider=provider,
                    worker=active_worker,
                )
            )
        )
        worker.signals.provider_status.connect(
            lambda payload, active_worker=worker: self._on_voice_catalog_provider_status(
                active_worker, payload
            )
        )
        worker.signals.worker_failed.connect(
            lambda worker_id, error, active_worker=worker: self._on_voice_catalog_failed(
                active_worker, worker_id, error
            )
        )
        worker.signals.worker_cancelled.connect(
            lambda _worker_id, active_worker=worker: self._finish_voice_catalog_worker(
                active_worker
            )
        )
        worker.signals.worker_finished.connect(
            lambda _worker_id, active_worker=worker: self._finish_voice_catalog_worker(
                active_worker
            )
        )
        self._voice_catalog_worker = worker
        if self._current_worker is None:
            self._status_badge.setText("正在加载音色列表...")
            self._status_badge.set_tone("warning")
        self._observe_voice_worker(worker, label="声腔 · 加载音色列表")
        worker.submit()

    def _on_voice_catalog_provider_status(
        self,
        worker: ListVoicesWorker,
        payload: dict[str, Any],
    ) -> None:
        """Ignore provider metadata from superseded catalog requests."""
        if getattr(self, "_shutdown_done", False) or worker is not self._voice_catalog_worker:
            return
        try:
            self._cancel_btn.isEnabled()
        except RuntimeError:
            return
        self._on_provider_status(payload)

    def _finish_voice_catalog_worker(self, worker: ListVoicesWorker) -> None:
        """Clear only the auxiliary catalog handle owned by ``worker``."""
        if worker is self._voice_catalog_worker:
            self._voice_catalog_worker = None

    def _on_voice_catalog_failed(
        self,
        worker: ListVoicesWorker,
        _worker_id: str,
        error: dict[str, Any],
    ) -> None:
        """Report catalog failures without terminating an active TTS operation."""
        if worker is not self._voice_catalog_worker:
            return
        self._voice_catalog_worker = None
        if getattr(self, "_shutdown_done", False) or self._current_worker is not None:
            return
        try:
            self._cancel_btn.isEnabled()
        except RuntimeError:
            return
        summary = str(error.get("summary") or error.get("message") or "获取音色列表失败")
        self._status_badge.setText(f"音色列表加载失败: {summary[:60]}")
        self._status_badge.set_tone("warning")

    def _on_voices_listed(
        self,
        voices: list[Any],
        *,
        provider: str = "",
        worker: ListVoicesWorker | None = None,
    ) -> None:
        """Handle loaded system voices - cache and repopulate combo."""
        if getattr(self, "_shutdown_done", False):
            return
        try:
            self._cancel_btn.isEnabled()
        except RuntimeError:
            # A page can be deleted while a provider worker finishes; Qt has
            # already disposed its child controls even if this Python wrapper
            # survives long enough to receive the queued signal.
            return
        if worker is not None and worker is not self._voice_catalog_worker:
            return
        if provider and provider != self._get_current_provider():
            return
        self._system_voices = list(voices)
        if worker is not None:
            self._finish_voice_catalog_worker(worker)
        if self._current_worker is None:
            self._cancel_btn.setEnabled(False)
            self._status_badge.setText(f"已加载 {len(voices)} 个音色")
            self._status_badge.set_tone("success")
        # Re-populate the current character or narrator selection.
        char_id = self._get_selected_character_id()
        if char_id == _NARRATOR_ID:
            self._load_voice_combo_for_entry(None, voice_id=self._narrator_voice_id())
        elif self._voice_team:
            entry = self._voice_team.get_entry(char_id) if char_id else None
            self._load_voice_combo_for_entry(entry)

    def _on_apply_offsets(self) -> None:
        """Apply current slider offsets to the selected character's entry and persist."""
        if self._reject_during_voice_team_build("应用参数"):
            return
        self._persist_current_offsets()

    def _persist_current_offsets(self, *, silent: bool = False) -> bool:
        """Persist visible controls so audition and final synthesis use the same values."""
        char_id = self._get_selected_character_id()
        if char_id == _NARRATOR_ID:
            if not self._layout:
                return False
            speed = 1.0 + self._speed_slider.value() / 100
            pitch = self._pitch_slider.value()
            vol = self._vol_slider.value() / 100
            provider = TTSProvider(self._get_current_provider())
            profile = self._narrator_profile or NarratorVoiceProfile(provider=provider)
            self._narrator_profile = profile.model_copy(
                update={
                    "base_speed": speed,
                    "pitch_offset": pitch,
                    "vol_offset": vol,
                }
            )
            atomic_write_json(
                self._layout.tts_narrator_profile_path,
                self._narrator_profile.model_dump(mode="json"),
            )
            self._render_narrator_info()
            self._apply_offset_btn.setText("应用参数")
            if not silent:
                self._status_badge.setText(
                    f"旁白参数已应用: 语速 {speed:.2f}× · 音调 {pitch:+d} st · "
                    f"音量 {1.0 + vol:.2f}×"
                )
                self._status_badge.set_tone("success")
            return True
        if not char_id or not self._voice_team:
            if not silent:
                self._status_badge.setText("请先选择一个已分配音色的角色")
                self._status_badge.set_tone("warning")
            return False
        entry = self._voice_team.get_entry(char_id)
        if not entry:
            if not silent:
                self._status_badge.setText("该角色尚未分配音色")
                self._status_badge.set_tone("warning")
            return False
        speed = self._speed_slider.value() / 100
        pitch = self._pitch_slider.value()
        vol = self._vol_slider.value() / 100
        previous_profile = voice_performance_profile(entry)
        if self._restore_auto_performance:
            character = self._selected_bible_character(char_id)
            character_profile = (
                derive_voice_performance_profile(character)
                if character
                else previous_profile.model_copy(
                    update={"manual_overrides": VoicePerformanceOverrides()}
                )
            )
            updated = update_voice_performance_profile(entry, character_profile)
        else:
            updated = with_manual_performance_overrides(
                entry,
                speed_offset=speed if "speed" in self._performance_dirty_fields else ...,
                pitch_offset=pitch if "pitch" in self._performance_dirty_fields else ...,
                vol_offset=vol if "volume" in self._performance_dirty_fields else ...,
            )
        parameters_changed = voice_performance_profile(updated) != previous_profile
        if parameters_changed:
            updated = updated.model_copy(
                update={
                    "preview_audio_path": "",
                    "preview_text": "",
                    "preview_error": "",
                    "preview_variants": {},
                }
            )
        self._voice_team.entries = [
            updated if e.character_id == char_id else e for e in self._voice_team.entries
        ]
        self._persist_voice_team()
        self._render_entry_info(updated)
        self._sync_voice_action_state(updated)
        self._update_performance_policy_label(updated)
        self._performance_dirty_fields.clear()
        self._restore_auto_performance = False
        self._apply_offset_btn.setText("应用参数")
        if not silent:
            applied_profile = voice_performance_profile(updated)
            manual_labels = self._performance_field_labels(
                applied_profile.manual_overrides.active_fields
            )
            source_label = f"人工覆盖 {'、'.join(manual_labels)}" if manual_labels else "自动策略"
            self._status_badge.setText(
                f"参数已应用（{source_label}）: "
                f"语速 {self._effective_character_speed(updated.speed_offset):.2f}× · "
                f"音调 {updated.pitch_offset:+d} st · "
                f"音量 {1.0 + updated.vol_offset:.2f}×"
            )
            self._status_badge.set_tone("success")
        return True

    def _persist_voice_team(self) -> None:
        """Persist current voice team atomically."""
        if not self._layout or not self._voice_team:
            return
        atomic_write_json(
            self._layout.tts_voice_team_path,
            self._voice_team.model_dump(mode="json"),
        )

    def _on_build_voice_team(self) -> None:
        """Handle build voice team button click."""
        if not self._layout:
            return

        characters = self._bible_characters or self._load_characters_from_bible()
        if not characters:
            self._status_badge.setText("未找到角色数据 (character_bible.json)")
            self._status_badge.set_tone("danger")
            return

        # If voice team already exists, show rebuild dialog
        rebuild_ids: list[str] | None = None
        if self._voice_team and self._voice_team.entries:
            dialog = RebuildConfirmDialog(
                characters=characters,
                voice_team_entries=self._voice_team.entries,
                parent=self,
            )
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            rebuild_ids = dialog.get_selected_ids()
            if not rebuild_ids:
                self._status_badge.setText("未选择任何角色")
                self._status_badge.set_tone("muted")
                return

        provider = self._get_current_provider()
        worker = BuildVoiceTeamWorker(
            project_id=self._project_id,
            characters=characters,
            settings=self._settings,
            layout=self._layout,
            provider=provider,
            mock=(provider == "mock"),
            rebuild_character_ids=rebuild_ids,
        )

        worker.signals.step_progress.connect(self._on_step_progress)
        worker.signals.voice_team_updated.connect(
            lambda data, active_worker=worker, selected_ids=rebuild_ids: (
                self._on_voice_team_updated(
                    data, worker=active_worker, rebuild_character_ids=selected_ids
                )
            )
        )
        worker.signals.narrator_profile_updated.connect(
            lambda data, active_worker=worker: self._on_narrator_profile_updated(
                data, worker=active_worker
            )
        )
        worker.signals.worker_started.connect(
            lambda _worker_id, active_worker=worker: self._on_voice_team_worker_started(
                active_worker
            )
        )
        worker.signals.worker_failed.connect(self._on_worker_failed)

        self._current_worker = worker
        self._cancel_btn.setEnabled(True)
        self._build_team_btn.setEnabled(False)
        self._refresh_selected_voice_action_state()
        self._show_voice_team_task_feedback(
            state="已提交 · 等待启动",
            tone="warning",
            title="配音团队构建任务已提交",
            detail="后台任务正在启动；将依次检查旁白、匹配角色音色并准备角色试听。",
            indeterminate=True,
        )
        self._status_badge.setText("配音团队任务已提交，正在进入后台队列…")
        self._status_badge.set_tone("warning")

        self._observe_voice_worker(worker, label="声腔 · 构建配音团队")
        worker.submit()

    def _on_voice_team_worker_started(self, worker: Any) -> None:
        """Turn a submitted voice-team task into a visible running task."""
        if worker is not self._current_worker:
            return
        self._show_voice_team_progress(
            completed=0,
            total=2,
            phase_index=1,
            phase_label="前置准备",
            detail="检查旁白与平台",
        )
        self._status_badge.setText("阶段 1/5 · 正在检查旁白音色与平台连接…")
        self._status_badge.set_tone("warning")

    def _on_build_narrator_profile(self) -> None:
        """Ask the LLM to refresh the narrator design from current work artifacts."""
        if self._reject_during_voice_team_build("重新构建旁白音色"):
            return
        if not self._layout:
            return
        provider = self._get_current_provider()
        worker = BuildNarratorProfileWorker(
            project_id=self._project_id,
            settings=self._settings,
            layout=self._layout,
            provider=provider,
            mock=(provider == "mock"),
        )
        worker.signals.step_progress.connect(self._on_step_progress)
        worker.signals.narrator_profile_updated.connect(self._on_narrator_profile_updated)
        worker.signals.worker_failed.connect(self._on_worker_failed)
        self._current_worker = worker
        self._cancel_btn.setEnabled(True)
        self._status_badge.setText("正在分析作品气质并构建旁白音色...")
        self._status_badge.set_tone("warning")
        self._observe_voice_worker(worker, label="声腔 · 构建旁白音色")
        worker.submit()

    def _on_clone_voice(self) -> None:
        """Open file dialog and clone voice from reference audio."""
        if self._reject_during_voice_team_build("克隆音色"):
            return
        if not self._layout:
            return

        char_id = self._get_selected_character_id()
        if not char_id:
            self._status_badge.setText("请先选择一个角色")
            self._status_badge.set_tone("danger")
            return
        if char_id == _NARRATOR_ID:
            self._status_badge.setText("旁白不可从参考音频克隆，请选择系统音色或重新生成设计")
            self._status_badge.set_tone("warning")
            return

        provider = self._get_current_provider()
        if not self._provider_feature("voice_clone"):
            self._status_badge.setText(f"{provider} 当前不支持参考音频克隆")
            self._status_badge.set_tone("warning")
            return
        if self._provider_feature("local_reference_audio") or provider in {"minimax", "mock"}:
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                "选择参考音频",
                "",
                "音频文件 (*.mp3 *.wav *.flac);;所有文件 (*)",
            )
        else:
            file_path, ok = show_text_input_dialog(
                self,
                "参考音频公网地址",
                "当前平台要求参考音频可由云端直接访问，请输入 HTTPS 音频 URL：",
                placeholder_text="https://example.com/authorized-voice.wav",
                confirm_text="继续克隆",
            )
            if not ok:
                return
        if not file_path:
            return

        authorized = ask_confirmation(
            self,
            "确认授权音色克隆",
            "请确认你已获得参考音频说话人的明确授权，可将其声音用于本作品配音。",
            informative_text="未经授权请勿继续；授权确认会随本次克隆请求提交。",
            confirm_text="我已获得授权",
        )
        if not authorized:
            return
        reference_transcript = ""
        if provider == TTSProvider.QWEN3.value:
            reference_transcript, accepted = show_text_input_dialog(
                self,
                "参考音频转写",
                "请输入参考音频的逐字转写（建议提供，可显著提升 Qwen3-TTS Base 克隆稳定性）：",
                placeholder_text="留空将仅使用声纹向量，质量可能下降",
                confirm_text="开始克隆",
                require_text=False,
            )
            if not accepted:
                return

        worker = CloneVoiceWorker(
            project_id=self._project_id,
            character_id=char_id,
            reference_audio_path=file_path,
            settings=self._settings,
            layout=self._layout,
            provider=provider,
            reference_transcript=reference_transcript,
            authorized=authorized,
            mock=(provider == "mock"),
        )
        worker.signals.step_progress.connect(self._on_step_progress)
        worker.signals.voice_team_updated.connect(self._on_voice_team_updated)
        worker.signals.worker_failed.connect(self._on_worker_failed)

        self._current_worker = worker
        self._cancel_btn.setEnabled(True)
        self._status_badge.setText("正在克隆音色...")
        self._status_badge.set_tone("warning")
        self._observe_voice_worker(worker, label="声腔 · 克隆角色音色")
        worker.submit()

    def _on_design_voice(self) -> None:
        """Review the upstream-derived brief before designing a new voice."""
        if self._reject_during_voice_team_build("设计音色"):
            return
        if not self._layout:
            return

        char_id = self._get_selected_character_id()
        if not char_id:
            self._status_badge.setText("请先选择一个角色")
            self._status_badge.set_tone("danger")
            return

        if char_id == _NARRATOR_ID:
            self._on_build_narrator_profile()
            return

        provider = self._get_current_provider()
        if not self._provider_feature("voice_design"):
            self._status_badge.setText(f"{provider} 当前不支持音色设计")
            self._status_badge.set_tone("warning")
            return
        entry = self._voice_team.get_entry(char_id) if self._voice_team else None
        character = next(
            (
                dict(item)
                for item in self._bible_characters
                if item.get("character_id", item.get("name", "")) == char_id
            ),
            {},
        )
        if entry and entry.clone_prompt:
            character.setdefault("voice_description", entry.clone_prompt)
        default_brief = (
            entry.voice_design_prompt if entry and entry.voice_design_prompt else ""
        ) or build_voice_design_prompt(character)
        character_name = (
            entry.character_name
            if entry
            else str(character.get("name") or character.get("character_name") or "未命名角色")
        )
        description, ok = show_multiline_input_dialog(
            self,
            "AI 音色设计",
            "简报已根据上游角色特质生成。可调整声线、节奏与表达要求，建议保留角色核心辨识度。",
            heading="确认角色音色简报",
            initial_text=default_brief,
            placeholder_text="描述角色的年龄感、音域、语速、情绪底色与表达习惯…",
            helper_text="提交后将生成候选音色并自动进入试听；Ctrl/⌘ + Enter 可快速提交。",
            context_text=(
                f"角色 · {character_name}    平台 · {provider_ui_spec(provider).label}    "
                "来源 · 上游角色档案"
            ),
            confirm_text="生成并试听",
            reset_text="恢复上游简报",
        )
        if not ok or not description.strip():
            return

        worker = DesignVoiceWorker(
            project_id=self._project_id,
            character_id=char_id,
            description=description,
            settings=self._settings,
            layout=self._layout,
            provider=provider,
            mock=(provider == "mock"),
        )
        worker.signals.step_progress.connect(self._on_step_progress)
        worker.signals.voice_team_updated.connect(self._on_voice_team_updated)
        worker.signals.worker_failed.connect(self._on_worker_failed)

        def on_design_preview(data: dict[str, Any]) -> None:
            audio_path = str(data.get("audio_path") or "")
            played = bool(audio_path) and self._play_audition(audio_path)
            if not played and audio_path:
                self._ensure_tab_built(3)
                if self._audio_player is not None:
                    self._audio_player.load_audio(audio_path)
                    self._audio_player.play()
            self._status_badge.setText("音色设计完成，正在播放试听")
            self._status_badge.set_tone("success")

        worker.signals.audio_completed.connect(on_design_preview)

        self._current_worker = worker
        self._cancel_btn.setEnabled(True)
        self._status_badge.setText("正在设计音色...")
        self._status_badge.set_tone("warning")
        self._observe_voice_worker(worker, label="声腔 · 设计角色音色")
        worker.submit()

    def _on_approve_voice(self) -> None:
        """Approve the pending voice for the selected character after audition."""
        if self._reject_during_voice_team_build("确认音色"):
            return
        if not self._layout:
            return
        char_id = self._get_selected_character_id()
        if char_id == _NARRATOR_ID or not char_id:
            return
        entry = self._voice_team.get_entry(char_id) if self._voice_team else None
        if entry is None or entry.approval_status != "pending":
            return
        worker = ApproveVoiceWorker(
            project_id=self._project_id,
            character_id=char_id,
            settings=self._settings,
            layout=self._layout,
        )
        worker.signals.step_progress.connect(self._on_step_progress)
        worker.signals.voice_team_updated.connect(self._on_voice_team_updated)
        worker.signals.worker_failed.connect(self._on_worker_failed)
        self._current_worker = worker
        self._cancel_btn.setEnabled(True)
        self._status_badge.setText(f"正在确认 {entry.character_name} 的音色...")
        self._status_badge.set_tone("warning")
        self._observe_voice_worker(worker, label="声腔 · 确认角色音色")
        worker.submit()

    def _on_confirm_team(self) -> None:
        """Confirm the entire voice team as auto-dubbing-ready with one click.

        Calls ``execute_confirm_voice_team`` which pre-checks every entry is
        ready + approved + unexpired + provider-consistent.  On success the
        returned ``VoiceTeamContract`` carries ``confirmed=True``, which lets
        subsequent post-archive TTS runs short-circuit the per-entry check.
        """
        if self._reject_during_voice_team_build("确认团队"):
            return
        if not self._layout:
            return
        worker = ConfirmVoiceTeamWorker(
            project_id=self._project_id,
            settings=self._settings,
            layout=self._layout,
        )
        worker.signals.step_progress.connect(self._on_step_progress)
        worker.signals.voice_team_updated.connect(self._on_voice_team_updated)
        worker.signals.worker_failed.connect(self._on_worker_failed)
        self._current_worker = worker
        self._cancel_btn.setEnabled(True)
        self._status_badge.setText("正在确认整支配音团队...")
        self._status_badge.set_tone("warning")
        self._observe_voice_worker(worker, label="声腔 · 确认整支团队")
        worker.submit()

    def _stop_audition(self) -> None:
        """Stop any in-flight native audition playback."""
        proc = self._audition_proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        self._audition_proc = None

    def _play_audition(self, audio_path: str) -> bool:
        """Play a short audition clip through the native OS audio player.

        Auditions are single voice clips that need no timeline/segment sync, so
        a fire-and-forget native player (afplay on macOS) is both simpler and
        avoids Qt's FFmpeg backend, which cannot probe MiniMax
        AIGC-watermarked MP3s and would report ``InvalidMedia``.  Returns
        ``True`` when a player was launched.
        """
        self._stop_audition()
        path = Path(audio_path)
        if not path.is_file():
            return False
        command: list[str] | None = None
        if sys.platform == "darwin":
            command = ["/usr/bin/afplay", str(path)]
        elif sys.platform.startswith("linux"):
            for player, args in _LINUX_AUDITION_PLAYERS:
                executable = shutil.which(player)
                if executable:
                    command = [executable, *args, str(path)]
                    break
        elif sys.platform == "win32":
            # Windows has no afplay; fall back to Qt's player via the caller.
            return False
        if command is None:
            return False
        try:
            self._audition_proc = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
            )
        except Exception:
            self._audition_proc = None
            return False
        return True

    def _on_preview_voice(self) -> None:
        """Preview/audition the currently selected voice."""
        if self._reject_during_voice_team_build("试听"):
            return
        if not self._layout:
            return

        char_id = self._get_selected_character_id()
        if not char_id:
            self._status_badge.setText("请先选择一个角色")
            self._status_badge.set_tone("danger")
            return

        if char_id == _NARRATOR_ID:
            if not self._persist_current_offsets(silent=True):
                return
            worker: PreviewVoiceWorker | PreviewNarratorVoiceWorker = PreviewNarratorVoiceWorker(
                project_id=self._project_id,
                settings=self._settings,
                layout=self._layout,
                provider=self._get_current_provider(),
                mock=(self._get_current_provider() == "mock"),
            )
        else:
            if not self._persist_current_offsets(silent=True):
                return
            entry = self._voice_team.get_entry(char_id) if self._voice_team else None
            sample_text = self._selected_voice_preview_text(char_id)
            variant_key = str(self._preview_sample_combo.currentData() or "identity")
            if entry:
                vp = entry.get_variant_preview(variant_key)
                # Always compare cached text against the current sample text.
                # The previous identity-variant shortcut ("is text non-empty?")
                # played stale audio when the sample text changed (e.g. after a
                # character rename or voice-sample update).
                text_matches = vp.text == sample_text
                if vp.audio_path and text_matches and Path(vp.audio_path).is_file():
                    played = self._play_audition(vp.audio_path)
                    if not played:
                        self._ensure_tab_built(3)
                        if self._audio_player is not None:
                            self._audio_player.load_audio(vp.audio_path)
                            self._audio_player.play()
                    self._status_badge.setText("正在播放已准备的角色试听")
                    self._status_badge.set_tone("success")
                    return
            worker = PreviewVoiceWorker(
                project_id=self._project_id,
                character_id=char_id,
                sample_text=sample_text,
                settings=self._settings,
                layout=self._layout,
                provider=self._get_current_provider(),
                mock=(self._get_current_provider() == "mock"),
            )
        worker.signals.step_progress.connect(self._on_step_progress)
        worker.signals.worker_failed.connect(self._on_worker_failed)

        # Connect to play the audition when the clip is ready
        # Capture current variant at call time so the closure writes to the correct slot
        _variant_key = str(self._preview_sample_combo.currentData() or "identity")

        def on_preview_done(data: dict[str, Any]) -> None:
            audio_path = data.get("audio_path", "")
            if char_id != _NARRATOR_ID and audio_path and self._voice_team:
                selected_entry = self._voice_team.get_entry(char_id)
                if selected_entry is not None:
                    updated_entry = selected_entry.with_variant_preview(
                        _variant_key,
                        audio_path=str(audio_path),
                        text=sample_text,
                        error="",
                    )
                    self._voice_team.entries = [
                        updated_entry if value.character_id == char_id else value
                        for value in self._voice_team.entries
                    ]
                    self._persist_voice_team()
                    self._update_rebuilt_character_rows([char_id])
            played = bool(audio_path) and self._play_audition(audio_path)
            if not played and audio_path:
                # Fall back to the in-app player (e.g. on Windows) when no
                # native CLI player is available.
                self._ensure_tab_built(3)
                if self._audio_player is not None:
                    self._audio_player.load_audio(audio_path)
                    self._audio_player.play()
            self._status_badge.setText(
                "已播放已保存的试听" if data.get("cached") else "试听完成并已保存"
            )
            self._status_badge.set_tone("success")
            self._cancel_btn.setEnabled(False)
            if self._current_worker is worker:
                self._current_worker = None
            self._refresh_selected_voice_action_state()

        worker.signals.audio_completed.connect(on_preview_done)

        self._current_worker = worker
        self._cancel_btn.setEnabled(True)
        wait_timeout_s = self._settings.tts_preview_lock_wait_timeout_s
        self._status_badge.setText(f"正在准备试听…自动配音占用时最多等待 {wait_timeout_s:g} 秒")
        self._status_badge.set_tone("warning")
        self._observe_voice_worker(worker, label="声腔 · 生成音色试听")
        worker.submit()

    # ─── Private slots: Script ─────────────────────────────────────────────

    def _on_script_anchor_clicked(self, url: Any) -> None:
        """Open the targeted segment editor when the inline edit link is clicked."""
        scheme = str(getattr(url, "scheme", lambda: "")())
        if scheme == "edit-sound-design":
            self._on_edit_sound_design()
            return
        if scheme != "edit-segment":
            return
        target = str(url.toString())
        segment_token = str(getattr(url, "path", lambda: "")()).strip("/")
        if not segment_token:
            segment_token = str(getattr(url, "host", lambda: "")()).strip()
        if not segment_token:
            # Backward compatibility with scripts already rendered using the
            # former ``edit-segment://<index>`` form.
            segment_token = target.removeprefix("edit-segment:").lstrip("/")
        legacy_ipv4_index = re.fullmatch(r"0\.0\.0\.(\d+)", segment_token)
        if legacy_ipv4_index:
            # Qt normalizes a numeric URI host (``//12``) to ``0.0.0.12``.
            # This was why the old per-row "编辑" links silently did nothing.
            segment_token = legacy_ipv4_index.group(1)
        try:
            segment_index = int(segment_token)
        except ValueError:
            _logger.warning("Ignored malformed script editor link: %s", target)
            return
        self._on_edit_script(segment_index)

    def _on_edit_script(self, segment_index: int | None = None) -> None:
        """Edit the current script in a staged dialog, one segment at a time."""
        if self._tts_operation is not None:
            self._clear_operation_is_running("编辑脚本")
            return
        if not self._current_script:
            self._status_badge.setText("请先生成或载入配音脚本")
            self._status_badge.set_tone("warning")
            return
        dialog = ScriptSegmentEditorDialog(
            self._current_script,
            initial_segment_index=segment_index,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if not dialog.has_changes:
            self._status_badge.setText("未检测到脚本修改")
            self._status_badge.set_tone("muted")
            return
        updated_script = dialog.edited_script()
        self._persist_edited_script(updated_script)

    def _on_edit_sound_design(self) -> None:
        """Edit segment-anchored ambience, BGM, and SFX authoring cues."""
        if self._tts_operation is not None:
            self._clear_operation_is_running("编辑声场")
            return
        if not self._current_script:
            self._status_badge.setText("请先生成或载入配音脚本")
            self._status_badge.set_tone("warning")
            return
        dialog = SoundDesignEditorDialog(self._current_script, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if not dialog.has_changes:
            self._status_badge.setText("未检测到声场设计修改")
            self._status_badge.set_tone("muted")
            return
        self._persist_edited_script(dialog.edited_script())

    def _persist_edited_script(self, script: DubbingScript) -> None:
        """Atomically save a user-edited script and retire stale audio derivatives."""
        if not self._layout:
            return
        if self._tts_operation is not None:
            self._clear_operation_is_running("保存脚本")
            return
        chapter_number = script.chapter_number
        if chapter_number != self._active_chapter_number:
            show_warning_message(
                self,
                "章节状态已变化",
                "当前选择的章节已经变化，请重新打开脚本编辑器。",
            )
            return

        has_audio_derivatives = any(
            (
                self._layout.tts_audio_dir(chapter_number).exists(),
                self._layout.tts_audio_result_path(chapter_number).exists(),
                self._layout.tts_subtitle_path(chapter_number).exists(),
            )
        )
        if has_audio_derivatives and not ask_confirmation(
            self,
            "保存脚本修改",
            f"保存第 {chapter_number} 章脚本，并将旧音频标记为待重建吗？",
            informative_text=(
                "脚本文本、情绪或语速发生变化后，旧音频和字幕不再可靠，将被清理以避免导出错误版本。"
            ),
            confirm_text="保存并重建",
            confirm_variant="danger",
        ):
            return

        try:
            script.script_hash = compute_dubbing_script_hash(script)
            atomic_write_json(
                self._layout.tts_dubbing_script_path(chapter_number),
                script.model_dump(mode="json"),
            )
            removed = invalidate_chapter_tts_artifacts(
                self._layout,
                chapter_number,
                include_metadata=False,
                include_script=False,
            )
        except OSError as exc:
            _logger.exception("Failed to save edited script for chapter %d", chapter_number)
            self._status_badge.setText(f"第 {chapter_number} 章脚本保存失败")
            self._status_badge.set_tone("danger")
            show_warning_message(
                self, "保存脚本失败", "无法保存脚本修改", informative_text=str(exc)
            )
            return

        self._current_script = script
        self._source_script_available = True
        self._current_audio_result = None
        self._current_results = []
        self._current_timeline = None
        self._segment_status.clear()
        self._room_draft_segments.clear()
        self._room_candidate_takes.clear()
        self._room_loaded_audio_path = ""
        self._playback_active = False
        self._playback_segment_idx = -1
        self._render_script_html()
        self._render_sync_script_html()
        self._render_mix_manifest()
        self._update_script_source_hint()
        if hasattr(self, "_subtitle_text"):
            self._subtitle_text.setPlainText("脚本已修改；重新合成后将生成新字幕。")
        if self._audio_player is not None:
            self._audio_player.clear()
        if hasattr(self, "_audio_empty"):
            self._audio_empty.setVisible(True)
        if hasattr(self, "_sound_resolution_badge"):
            self._sound_resolution_badge.setText("场景声音: 脚本已修改，待重新合成")
            self._sound_resolution_badge.set_tone("muted")
        self._status_badge.setText(
            f"第 {chapter_number} 章脚本已保存；{len(removed)} 组旧 TTS 产物待重建"
        )
        self._status_badge.set_tone("success")
        self._refresh_workflow_controls()

    def _on_generate_script(self) -> None:
        """Handle generate script button click."""
        if not self._layout:
            return

        chapter_num = self._chapter_combo.currentData()
        if chapter_num is None:
            return
        chapter_num = int(chapter_num)

        chapter_text = self._load_chapter_text(chapter_num)
        if not chapter_text:
            self._status_badge.setText(
                f"第 {chapter_num} 章尚未定稿，请先完成章节生成流程后再进行配音"
            )
            self._status_badge.set_tone("danger")
            return

        script_path = self._layout.tts_dubbing_script_path(chapter_num)
        if script_path.is_file():
            freshness = self._assess_current_script_freshness()
            is_stale = freshness != DubbingScriptFreshness.CURRENT
            derivatives = self._chapter_tts_artifact_group_count(chapter_num)
            if not ask_confirmation(
                self,
                "覆盖旧版配音脚本" if is_stale else "重新生成配音脚本",
                (
                    f"用第 {chapter_num} 章当前定稿正文覆盖旧版脚本吗？"
                    if is_stale
                    else f"重新生成第 {chapter_num} 章配音脚本吗？"
                ),
                informative_text=(
                    f"新脚本会先完成正文对齐、说话人裁决和专业审核；成功后才原子替换旧脚本，"
                    f"并清理 {derivatives} 组旧音频、字幕和混音派生产物。生成失败时旧版仍会保留。"
                ),
                confirm_text="用当前正文覆盖" if is_stale else "重新生成",
                confirm_variant="danger" if derivatives > 1 else "primary",
            ):
                return

        worker = GenerateScriptWorker(
            project_id=self._project_id,
            chapter_number=chapter_num,
            chapter_text=chapter_text,
            settings=self._settings,
            layout=self._layout,
        )

        worker_id = str(worker.worker_id)
        worker.signals.step_progress.connect(
            lambda step, data, worker_id=worker_id: self._on_step_progress(
                step,
                data,
                worker_id=worker_id,
            )
        )
        worker.signals.script_updated.connect(
            lambda data, worker_id=worker_id: self._on_script_updated(
                data,
                worker_id=worker_id,
            )
        )
        worker.signals.worker_failed.connect(self._on_worker_failed)

        self._current_worker = worker
        self._begin_tts_operation("script", chapter_num)
        self._cancel_btn.setEnabled(True)
        self._status_badge.setText("正在生成配音脚本...")
        self._status_badge.set_tone("warning")
        # Mark this chapter as the one being generated and reset its stream
        # so any leftover state from a previous run is discarded.
        self._generating_chapter = chapter_num
        self._script_streams[chapter_num] = _ChapterScriptStream()
        self._update_script_generation_progress("tts_script_start", {"chapter": chapter_num})
        # Start stall detection timer.
        import time as _time

        self._last_stream_activity_at = _time.monotonic()
        self._batch_start_time = _time.monotonic()
        self._stream_stall_warned = False
        self._stream_progress_last_update_at = 0.0  # First delta renders immediately.
        self._stream_stall_timer.start()
        if chapter_num == self._active_chapter_number:
            self._render_script_gen_placeholder(chapter_num)
            self._update_script_source_hint()
        if self._script_gen_timer is not None:
            self._start_visibility_periodic_timer(self._script_gen_timer)

        worker.submit()

    def _on_synthesize_from_script(self) -> None:
        """Handle synthesize button from script tab."""
        if not self._layout or not self._current_script or not self._source_script_available:
            self._status_badge.setText("请先生成或载入当前章节的源配音脚本")
            self._status_badge.set_tone("warning")
            return

        chapter_num = self._current_script.chapter_number
        provider = self._get_current_provider()
        worker = SynthesizeWorker(
            project_id=self._project_id,
            chapter_number=chapter_num,
            settings=self._settings,
            layout=self._layout,
            provider=provider,
            automation_mode=self._current_audio_automation_mode(),
            mock=(provider == "mock"),
        )

        worker.signals.step_progress.connect(self._on_step_progress)
        worker.signals.synthesis_progress.connect(self._on_synthesis_progress)
        worker.signals.segment_progress.connect(self._on_segment_progress)
        worker.signals.audio_completed.connect(self._on_audio_completed)
        worker.signals.worker_failed.connect(self._on_worker_failed)

        self._current_worker = worker
        self._begin_tts_operation("synthesis", chapter_num)
        self._cancel_btn.setEnabled(True)
        self._status_badge.setText("正在合成音频...")
        self._status_badge.set_tone("warning")

        worker.submit()

    # ─── Private slots: Audio ──────────────────────────────────────────────

    def _on_synthesize(self) -> None:
        """Handle synthesize button click."""
        if not self._layout:
            return

        chapter_num = self._audio_chapter_combo.currentData()
        if chapter_num is None:
            return
        chapter_num = int(chapter_num)
        if not self._source_script_available:
            self._status_badge.setText("请先生成或载入当前章节的源配音脚本")
            self._status_badge.set_tone("warning")
            return

        provider = self._get_current_provider()
        worker = SynthesizeWorker(
            project_id=self._project_id,
            chapter_number=chapter_num,
            settings=self._settings,
            layout=self._layout,
            provider=provider,
            automation_mode=self._current_audio_automation_mode(),
            mock=(provider == "mock"),
        )

        worker.signals.step_progress.connect(self._on_step_progress)
        worker.signals.synthesis_progress.connect(self._on_synthesis_progress)
        worker.signals.segment_progress.connect(self._on_segment_progress)
        worker.signals.audio_completed.connect(self._on_audio_completed)
        worker.signals.worker_failed.connect(self._on_worker_failed)

        self._current_worker = worker
        self._begin_tts_operation("synthesis", chapter_num)
        self._cancel_btn.setEnabled(True)
        self._status_badge.setText("正在合成音频...")
        self._status_badge.set_tone("warning")
        worker.submit()

    def _on_import_sound_assets(self, kind_override: str = "") -> None:
        """Collect import intent in Qt, then delegate mutation to the backend service."""
        if self._studio_service is None:
            return
        kind_data = kind_override or "soundscape"
        kind: Literal["soundscape", "bgm", "sfx"]
        if kind_data == "bgm":
            kind = "bgm"
        elif kind_data == "sfx":
            kind = "sfx"
        else:
            kind = "soundscape"
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "导入声音资产",
            "",
            "音频文件 (*.mp3 *.wav *.flac *.ogg *.m4a)",
        )
        if not paths:
            return
        tag_text, ok = show_text_input_dialog(
            self,
            "为声音资产添加标签",
            "用逗号分隔标签。它们用于将脚本中的环境声、BGM 或音效匹配到此资产：",
            placeholder_text="例如：夜雨, 城市, 悬疑",
            confirm_text="导入",
        )
        if not ok:
            return
        tags = [item.strip() for item in re.split(r"[,，]", tag_text) if item.strip()]
        result = self._studio_service.import_sound_assets(paths, kind=kind, tags=tags)
        if not result.count:
            self._status_badge.setText("未导入可用的音频文件")
            self._status_badge.set_tone("warning")
            return
        if hasattr(self, "_sound_resolution_badge"):
            self._sound_resolution_badge.setText(f"声音资产库: 已导入 {result.count} 项")
            self._sound_resolution_badge.set_tone("success")
        self._status_badge.setText(f"已导入 {result.count} 个声音资产；重新合成即可匹配")
        self._status_badge.set_tone("success")
        if hasattr(self, "_sound_library_panel"):
            self._sound_library_panel.refresh()

    def _on_generate_sound_palette(self) -> None:
        """Generate several work-level BGM/ambience candidates for audition."""
        if self._layout is None or self._current_worker is not None:
            return
        worker = GenerateSoundPaletteWorker(
            story_context=self._project_sound_context(),
            settings=self._settings,
            layout=self._layout,
        )
        worker.signals.step_progress.connect(self._on_step_progress)
        worker.signals.sound_library_updated.connect(self._on_sound_palette_updated)
        worker.signals.worker_failed.connect(self._on_worker_failed)
        self._current_worker = worker
        self._begin_tts_operation("sound_palette", 0)
        self._sound_library_panel.set_generation_running(True)
        self._cancel_btn.setEnabled(True)
        self._status_badge.setText("正在生成作品声音候选；完成后请逐项试听并批准…")
        self._status_badge.set_tone("warning")
        worker.submit()

    def _project_sound_context(self) -> dict[str, Any]:
        """Project stable story/style projection used for reusable sound generation."""
        return dict(self._studio_service.story_sound_context()) if self._studio_service else {}

    def _on_sound_palette_updated(self, data: dict[str, Any]) -> None:
        self._current_worker = None
        self._finish_tts_operation()
        self._cancel_btn.setEnabled(False)
        generated = int(data.get("generated", 0) or 0)
        pending = int(data.get("pending_review", 0) or 0)
        self._sound_library_panel.refresh()
        self._status_badge.setText(f"已生成 {generated} 项作品声音候选 · 待试听 {pending}")
        self._status_badge.set_tone("success" if generated else "warning")
        self._post_workspace_tabs.setCurrentIndex(1)

    def _on_sound_library_changed(self) -> None:
        """Refresh cue matching guidance after an approval or tag decision."""
        self._status_badge.setText("声音资源库已更新；重新装配可应用新的批准与标签")
        self._status_badge.set_tone("success")

    def _on_full_pipeline(self) -> None:
        """Handle full pipeline button click."""
        if not self._layout:
            return

        chapter_num = self._audio_chapter_combo.currentData()
        if chapter_num is None:
            return
        chapter_num = int(chapter_num)

        characters = self._load_characters_from_bible()
        chapter_text = self._load_chapter_text(chapter_num)
        if not chapter_text:
            self._status_badge.setText(
                f"第 {chapter_num} 章尚未定稿，请先完成章节生成流程后再进行配音"
            )
            self._status_badge.set_tone("danger")
            return

        provider = self._get_current_provider()
        worker = FullTTSPipelineWorker(
            project_id=self._project_id,
            chapter_number=chapter_num,
            chapter_text=chapter_text,
            characters=characters,
            settings=self._settings,
            layout=self._layout,
            provider=provider,
            automation_mode=self._current_audio_automation_mode(),
            mock=(provider == "mock"),
        )

        worker_id = str(worker.worker_id)
        worker.signals.step_progress.connect(
            lambda step, data, worker_id=worker_id: self._on_step_progress(
                step,
                data,
                worker_id=worker_id,
            )
        )
        worker.signals.script_updated.connect(
            lambda data, worker_id=worker_id: self._on_script_updated(
                data,
                worker_id=worker_id,
            )
        )
        worker.signals.segment_progress.connect(self._on_segment_progress)
        worker.signals.audio_completed.connect(self._on_audio_completed)
        worker.signals.worker_failed.connect(self._on_worker_failed)

        self._current_worker = worker
        self._begin_tts_operation("full_pipeline", chapter_num)
        self._cancel_btn.setEnabled(True)
        automation_mode = self._current_audio_automation_mode()
        status_text = {
            AudioAutomationMode.MANUAL: "正在按已审核内容合成与混音…",
            AudioAutomationMode.ASSISTED: "AI 正在伴随推进；声音候选将保留试听关口…",
            AudioAutomationMode.AUTONOMOUS: "AI 正在自主完成配音、声场、混音与质检…",
        }[automation_mode]
        self._status_badge.setText(status_text)
        self._status_badge.set_tone("warning")
        # The full pipeline also runs the script-generation stage, so mark the
        # chapter and reset its stream the same way as _on_generate_script.
        if automation_mode != AudioAutomationMode.MANUAL:
            self._generating_chapter = chapter_num
            self._script_streams[chapter_num] = _ChapterScriptStream()
            self._update_script_generation_progress("tts_script_start", {"chapter": chapter_num})
            if chapter_num == self._active_chapter_number:
                self._render_script_gen_placeholder(chapter_num)
                self._update_script_source_hint()
            if self._script_gen_timer is not None:
                self._start_visibility_periodic_timer(self._script_gen_timer)

        worker.submit()

    def _on_export_audio(self, format: str = "mp3", *, target_lufs: float | None = None) -> None:
        """Export current chapter audio in the requested format."""
        if not self._layout:
            return

        chapter_num = self._audio_chapter_combo.currentData()
        if chapter_num is None:
            return

        fmt_label = format.upper()
        ext = {"mp3": ".mp3", "wav": ".wav", "flac": ".flac"}.get(format, f".{format}")
        filter_map = {
            "mp3": "MP3 音频 (*.mp3)",
            "wav": "WAV 音频 (*.wav)",
            "flac": "FLAC 无损音频 (*.flac)",
        }
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            f"导出 {fmt_label}",
            f"chapter_{int(chapter_num):03d}{ext}",
            filter_map.get(format, f"{fmt_label} 音频 (*{ext})"),
        )
        if not file_path:
            return

        worker = ExportAudioWorker(
            project_id=self._project_id,
            chapter_number=int(chapter_num),
            export_format=format,
            output_path=file_path,
            settings=self._settings,
            layout=self._layout,
            target_lufs=target_lufs,
        )
        worker.signals.worker_failed.connect(self._on_worker_failed)

        def on_export_done(data: dict[str, Any]) -> None:
            path = data.get("export_path", "")
            self._status_badge.setText(f"已导出: {path}")
            self._status_badge.set_tone("success")

        worker.signals.audio_completed.connect(on_export_done)
        lufs_hint = f" ({target_lufs:+.0f} LUFS)" if target_lufs is not None else ""
        self._status_badge.setText(f"正在导出 {fmt_label}{lufs_hint}...")
        self._status_badge.set_tone("warning")
        self._observe_voice_worker(
            worker,
            label=f"声腔 · 导出章节音频 ({fmt_label})",
            chapter_number=int(chapter_num),
        )
        worker.submit()

    def _on_export_srt(self) -> None:
        """Export subtitle file."""
        if not self._layout or not self._current_script or not self._current_audio_result:
            self._status_badge.setText("无字幕可导出")
            self._status_badge.set_tone("danger")
            return
        try:
            require_delivery_ready(self._current_audio_result)
        except TTSDeliveryNotReadyError as exc:
            self._status_badge.setText("尚不可交付：" + "、".join(exc.reasons))
            self._status_badge.set_tone("danger")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出字幕", "subtitle.srt", "字幕文件 (*.srt)"
        )
        if not file_path:
            return

        # Generate SRT content
        srt_lines: list[str] = []
        for i, seg in enumerate(self._current_script.segments, 1):
            start = self._ms_to_srt_time(seg.start_ms)
            end = self._ms_to_srt_time(seg.end_ms)
            srt_lines.append(f"{i}")
            srt_lines.append(f"{start} --> {end}")
            srt_lines.append(seg.text)
            srt_lines.append("")

        Path(file_path).write_text("\n".join(srt_lines), encoding="utf-8")
        self._status_badge.setText(f"字幕已导出: {file_path}")
        self._status_badge.set_tone("success")

    def _on_export_all(self, *, include_srt: bool = False) -> None:
        """Export all chapter audio as ZIP."""
        if not self._layout:
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "全书打包导出", "tts_export.zip", "ZIP 文件 (*.zip)"
        )
        if not file_path:
            return

        worker = ExportAudioWorker(
            project_id=self._project_id,
            chapter_number=0,  # 0 = all chapters
            export_format="zip",
            output_path=file_path,
            settings=self._settings,
            layout=self._layout,
            include_srt=include_srt,
        )
        worker.signals.worker_failed.connect(self._on_worker_failed)

        def on_export_done(data: dict[str, Any]) -> None:
            path = data.get("export_path", file_path)
            srt_hint = " (含字幕)" if include_srt else ""
            self._status_badge.setText(f"全书已导出{srt_hint}: {path}")
            self._status_badge.set_tone("success")

        worker.signals.audio_completed.connect(on_export_done)
        srt_hint = " (含字幕)" if include_srt else ""
        self._status_badge.setText(f"正在打包导出{srt_hint}...")
        self._status_badge.set_tone("warning")
        self._observe_voice_worker(worker, label=f"声腔 · 打包全书音频{srt_hint}")
        worker.submit()

    def _on_export_audiobook_package(self) -> None:
        """Export the finished audiobook delivery package (sequential gate)."""
        if not self._layout:
            return

        worker = ExportAudiobookWorker(
            project_id=self._project_id,
            settings=self._settings,
            layout=self._layout,
        )
        worker.signals.worker_failed.connect(self._on_worker_failed)

        def on_export_done(data: dict[str, Any]) -> None:
            count = data.get("chapter_count", 0)
            path = data.get("export_path", "")
            self._status_badge.setText(f"有声书包已导出（{count} 章）: {path}")
            self._status_badge.set_tone("success")

        worker.signals.audio_completed.connect(on_export_done)
        self._status_badge.setText("正在导出有声书包...")
        self._status_badge.set_tone("warning")
        self._observe_voice_worker(worker, label="声腔 · 导出有声书包")
        worker.submit()

    # ─── Private slots: Chapter clearing ─────────────────────────────

    def _on_reset_project_tts_artifacts(self) -> None:
        """Reset every regenerable chapter output while retaining reusable assets."""

        if not self._layout:
            return
        if self._clear_operation_is_running("重置配音产物"):
            return
        preview = preview_reset_project_tts_artifacts(self._layout)
        if preview.removed_count == 0:
            self._status_badge.setText("当前项目没有可重置的配音产物")
            self._status_badge.set_tone("muted")
            return
        reclaimed_mib = preview.reclaimed_bytes / (1024 * 1024)
        if not ask_confirmation(
            self,
            "重新开始配音制作",
            f"清空当前项目全部 {preview.removed_count} 个可再生配音文件吗？",
            informative_text=(
                f"预计释放 {reclaimed_mib:.1f} MiB。将删除所有章节脚本、分段/整章音频、"
                "字幕、试听版本、断点、时间线、混音与质量报告。\n\n"
                "会保留：配音团队、旁白音色、平台路由、声音创意圣经、项目/应用声音资源库。"
            ),
            confirm_text="清空并重新开始",
            confirm_variant="danger",
        ):
            return
        report = execute_reset_project_tts_artifacts(self._layout)
        self._script_streams.clear()
        self._generating_chapter = None
        self._current_worker = None
        active_chapter = self._active_chapter_number
        if active_chapter > 0:
            self._load_chapter_artifacts(active_chapter)
        else:
            self._current_script = None
            self._current_audio_result = None
            self._source_script_available = False
            self._script_freshness = None
            self._update_script_source_hint()
            self._refresh_workflow_controls()
        reclaimed_mib = report.reclaimed_bytes / (1024 * 1024)
        self._status_badge.setText(
            f"已清空 {report.removed_count} 个可再生配音文件 · 释放 {reclaimed_mib:.1f} MiB"
        )
        self._status_badge.set_tone("success")

    def _on_clear_script_chapter(self) -> None:
        """Clear the dubbing script for the current chapter."""
        if not self._layout:
            return
        if self._clear_operation_is_running():
            return
        chapter_number = self._active_chapter_number
        if chapter_number < 1:
            show_warning_message(self, "清理脚本", "请先选择一个章节")
            return
        if not ask_confirmation(
            self,
            "清理配音脚本",
            f"确定要清理第 {chapter_number} 章的配音脚本吗？",
            informative_text=(
                "删除可重新生成的源脚本，并立即从脚本与配音室界面移除其内容。"
                "已合成的音频和字幕仍可播放，但需重新生成脚本后才能再次合成。"
            ),
            confirm_text="清理",
            confirm_variant="danger",
        ):
            return
        script_path = self._layout.tts_dubbing_script_path(chapter_number)
        try:
            if script_path.exists():
                script_path.unlink()
                self._status_badge.setText(f"已清理第 {chapter_number} 章源配音脚本")
                self._status_badge.set_tone("success")
            else:
                self._status_badge.setText(f"第 {chapter_number} 章没有可清理的源脚本")
                self._status_badge.set_tone("muted")
        except OSError as exc:
            _logger.exception("Failed to remove script: %s", script_path)
            self._status_badge.setText(f"清理第 {chapter_number} 章脚本失败")
            self._status_badge.set_tone("danger")
            show_warning_message(
                self, "清理脚本失败", "无法删除源配音脚本", informative_text=str(exc)
            )
            return
        self._load_chapter_artifacts(chapter_number)

    def _on_clear_audio_chapter(self) -> None:
        """Clear all TTS artifacts (script, audio, subtitle) for the current chapter."""
        if not self._layout:
            return
        if self._clear_operation_is_running():
            return
        chapter_number = self._active_chapter_number
        if chapter_number < 1:
            show_warning_message(self, "清理章节", "请先选择一个章节")
            return
        if not ask_confirmation(
            self,
            "清理章节 TTS 产物",
            f"确定要清理第 {chapter_number} 章的全部配音产物吗？",
            informative_text=(
                "包括源脚本、合成音频、字幕与本章处理记录。不会删除共享的声音资产库。"
                "清理后需要重新走 TTS 流程。"
            ),
            confirm_text="清理",
            confirm_variant="danger",
        ):
            return
        try:
            removed = invalidate_chapter_tts_artifacts(
                self._layout, chapter_number, include_script=True
            )
        except OSError as exc:
            _logger.exception("Failed to clear TTS artifacts for chapter %d", chapter_number)
            self._status_badge.setText(f"清理第 {chapter_number} 章失败")
            self._status_badge.set_tone("danger")
            show_warning_message(
                self, "清理章节失败", "部分 TTS 产物无法删除", informative_text=str(exc)
            )
            return
        count = len(removed)
        if count:
            self._status_badge.setText(f"已清理第 {chapter_number} 章 {count} 组 TTS 产物")
            self._status_badge.set_tone("success")
        else:
            self._status_badge.setText(f"第 {chapter_number} 章没有可清理的 TTS 产物")
            self._status_badge.set_tone("muted")
        _logger.info("Cleared %d TTS artifacts for chapter %d", count, chapter_number)
        self._load_chapter_artifacts(chapter_number)

    def _on_batch_clear_chapters(self) -> None:
        """Show dialog to select and clear multiple chapters' TTS artifacts."""
        if not self._layout:
            show_warning_message(self, "批量清理", "无可用章节")
            return
        layout = self._layout
        if self._clear_operation_is_running():
            return
        chapters_with_artifacts = self._chapters_with_tts_artifacts()
        if not chapters_with_artifacts:
            show_warning_message(self, "批量清理", "没有可清理的 TTS 产物")
            return

        # Build a multi-select dialog that exposes the amount of work before
        # the destructive confirmation, rather than treating every chapter as
        # if it has output to remove.
        dialog = QDialog(self)
        dialog.setWindowTitle("批量清理章节 TTS 产物")
        dialog.setObjectName("appDialog")
        dialog.resize(*smart_dialog_size(self, 520, 500, max_screen_fraction=0.60))
        d_layout = QVBoxLayout(dialog)
        d_layout.setSpacing(8)

        hint = QLabel("选择要清理的章节（可多选）。每项包含本章可再生的 TTS 产物：")
        hint.setWordWrap(True)
        d_layout.addWidget(hint)

        list_widget = QListWidget()
        list_widget.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        list_widget.setAccessibleName("选择需要清理的章节")
        for ch in chapters_with_artifacts:
            group_count = self._chapter_tts_artifact_group_count(ch)
            item = QListWidgetItem(f"第 {ch} 章  ·  {group_count} 组产物")
            item.setData(Qt.ItemDataRole.UserRole, ch)
            item.setToolTip("清理源脚本、音频、字幕与本章处理记录")
            list_widget.addItem(item)
        d_layout.addWidget(list_widget, 1)

        btn_row = QHBoxLayout()
        select_all_btn = ActionButton("全选", variant="secondary")
        select_all_btn.clicked.connect(list_widget.selectAll)
        btn_row.addWidget(select_all_btn)
        btn_row.addStretch()
        confirm_btn = ActionButton("清理选中", variant="danger")
        confirm_btn.setEnabled(False)
        cancel_btn = ActionButton("取消", variant="quiet")

        def _update_confirm_label() -> None:
            selected_count = len(list_widget.selectedItems())
            confirm_btn.setEnabled(bool(selected_count))
            confirm_btn.setText(f"清理选中 ({selected_count})" if selected_count else "清理选中")

        def _do_clear() -> None:
            selected = list_widget.selectedItems()
            if not selected:
                return
            chapters_to_clear = sorted(
                int(item.data(Qt.ItemDataRole.UserRole)) for item in selected
            )
            estimated_groups = sum(
                self._chapter_tts_artifact_group_count(chapter) for chapter in chapters_to_clear
            )
            chapter_summary = "、".join(f"第 {chapter} 章" for chapter in chapters_to_clear)
            if not ask_confirmation(
                dialog,
                "确认批量清理",
                f"确定要清理 {chapter_summary} 的 TTS 产物吗？",
                informative_text=(
                    f"共 {len(chapters_to_clear)} 章、约 {estimated_groups} 组可再生产物。"
                    "共享声音资产库不会受到影响。"
                ),
                confirm_text="确认清理",
                confirm_variant="danger",
            ):
                return
            total_removed = 0
            failures: list[int] = []
            for ch in chapters_to_clear:
                try:
                    removed = invalidate_chapter_tts_artifacts(layout, ch, include_script=True)
                except OSError:
                    _logger.exception("Failed to batch clear TTS artifacts for chapter %d", ch)
                    failures.append(ch)
                    continue
                total_removed += len(removed)
                _logger.info(
                    "Batch cleared %d TTS artifacts for chapter %d",
                    len(removed),
                    ch,
                )
            if failures:
                self._status_badge.setText(
                    f"已清理 {len(chapters_to_clear) - len(failures)} 章；"
                    f"第 {', '.join(map(str, failures))} 章清理失败"
                )
                self._status_badge.set_tone("warning")
            else:
                self._status_badge.setText(
                    f"已批量清理 {len(chapters_to_clear)} 章共 {total_removed} 组 TTS 产物"
                )
                self._status_badge.set_tone("success")
            dialog.accept()
            # Reload current chapter artifacts
            if self._active_chapter_number:
                self._load_chapter_artifacts(self._active_chapter_number)

        list_widget.itemSelectionChanged.connect(_update_confirm_label)
        confirm_btn.clicked.connect(_do_clear)
        cancel_btn.clicked.connect(dialog.reject)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(confirm_btn)
        d_layout.addLayout(btn_row)

        dialog.exec()

    # ─── Private slots: Settings ───────────────────────────────────────────

    def _sync_provider_settings_ui(self, provider: str) -> None:
        """Apply one provider spec to model choices and configuration visibility."""
        provider_label = provider_ui_spec(provider).label
        self._settings_context_hint.setText(f"当前配置：{provider_label}；平台请在页面顶栏切换")
        capability_summary = getattr(self, "_platform_capability_summary", None)
        if capability_summary is not None:
            capability_summary.setText(self._provider_capability_summary(provider))
        platform_section = getattr(self, "_platform_settings_section", None)
        if platform_section is not None:
            platform_section.set_title(f"{provider_label} — 连接与模型")
        models = provider_models(provider, self._settings)
        models = self._available_provider_models(provider, models)
        configured = self._configured_model_for_provider(provider)
        selected = configured if configured in models else (models[0] if models else "")
        signals_were_blocked = self._model_combo.blockSignals(True)
        try:
            self._model_combo.clear()
            self._model_combo.addItems(models)
            if selected:
                self._model_combo.setCurrentText(selected)
        finally:
            self._model_combo.blockSignals(signals_were_blocked)
        self._on_provider_model_changed(selected)
        self._sync_platform_model_control_state(selected)
        self._update_api_key_visibility(provider)
        self._refresh_audio_execution_plan()

    def _configured_model_for_provider(self, provider: str) -> str:
        """Return the source-of-truth model value used by the selected provider."""
        return resolve_tts_model(self._settings, provider)

    def _on_provider_model_changed(self, model: str) -> None:
        """Keep the active runtime model aligned with the visible provider selector."""
        model = model.strip()
        if not model:
            return
        provider = self._get_current_provider()
        setting_fields = {
            TTSProvider.MINIMAX.value: "tts_default_model",
            TTSProvider.BAILIAN.value: "tts_dashscope_model",
            TTSProvider.DASHSCOPE.value: "tts_dashscope_model",
            TTSProvider.TENCENT.value: "tts_tencent_voice_type",
            TTSProvider.VOLCENGINE_ARK.value: "tts_volcengine_model",
            TTSProvider.MIMO.value: "tts_mimo_model",
            TTSProvider.LOCAL.value: "tts_local_model",
            TTSProvider.QWEN3.value: "tts_qwen3_formal_model",
            TTSProvider.COSYVOICE.value: "tts_cosyvoice_model",
            TTSProvider.OPENVOICE.value: "tts_openvoice_model",
        }
        setting_field = setting_fields.get(provider)
        if setting_field is None or getattr(self._settings, setting_field) == model:
            return
        self._settings = self._settings.model_copy(update={setting_field: model})
        TTSAdapterRegistry.reset_instance()

    def _update_api_key_visibility(self, provider: str = "") -> None:
        """Show/hide API key rows based on selected provider."""
        provider = provider or self._get_current_provider()
        group = provider_ui_spec(provider).settings_group
        self._minimax_key_row.setVisible(group == "minimax")
        self._minimax_url_row.setVisible(group == "minimax")
        self._minimax_group_row.setVisible(group == "minimax")
        self._minimax_bitrate_row.setVisible(group == "minimax")
        self._minimax_channel_row.setVisible(group == "minimax")
        self._minimax_language_row.setVisible(group == "minimax")
        self._dashscope_key_row.setVisible(group == "dashscope")
        self._dashscope_model_row.setVisible(False)
        self._dashscope_preview_row.setVisible(group == "dashscope")
        self._dashscope_clone_row.setVisible(group == "dashscope")
        self._dashscope_design_row.setVisible(group == "dashscope")
        self._dashscope_url_row.setVisible(group == "dashscope")
        self._dashscope_instruction_row.setVisible(group == "dashscope")
        self._tencent_id_row.setVisible(group == "tencent")
        self._tencent_key_row.setVisible(group == "tencent")
        self._volcengine_key_row.setVisible(group == "volcengine_ark")
        self._volcengine_url_row.setVisible(group == "volcengine_ark")
        self._mimo_key_row.setVisible(group == "mimo")
        self._mimo_url_row.setVisible(group == "mimo")
        self._local_url_row.setVisible(group == "local")
        self._local_key_row.setVisible(group == "local")
        self._local_model_row.setVisible(False)
        self._qwen3_url_row.setVisible(group == "qwen3")
        self._qwen3_key_row.setVisible(group == "qwen3")
        self._qwen3_preview_row.setVisible(group == "qwen3")
        self._qwen3_design_row.setVisible(group == "qwen3")
        self._qwen3_clone_row.setVisible(group == "qwen3")
        self._cosyvoice_url_row.setVisible(group == "cosyvoice")
        self._cosyvoice_mode_row.setVisible(group == "cosyvoice")
        self._openvoice_checkpoint_row.setVisible(group == "openvoice")
        self._openvoice_device_row.setVisible(group == "openvoice")

    def _on_save_settings(self) -> None:
        """Save settings to .env file."""
        provider = self._get_current_provider()
        model = self._model_combo.currentText()
        auto_trigger = self._auto_trigger_check.isChecked()

        # Build env updates
        env_updates: dict[str, str] = {
            "NOVEL_FORGE_TTS_DEFAULT_PROVIDER": provider,
            "NOVEL_FORGE_TTS_DEFAULT_SPEED": self._speed_combo.currentText(),
            "NOVEL_FORGE_TTS_AUTO_TRIGGER_AFTER_CHAPTER": str(auto_trigger).lower(),
            # Auto-trigger is an actionable feature switch, not merely a
            # preference.  Without this, the visible checkbox could be on
            # while the default-disabled TTS module silently skipped every
            # finalized chapter.
            "NOVEL_FORGE_TTS_ENABLED": str(
                bool(getattr(self._settings, "tts_enabled", False)) or auto_trigger
            ).lower(),
        }

        # Narrator voice ID
        narrator_voice = self._narrator_voice_input.text().strip()
        env_updates["NOVEL_FORGE_TTS_NARRATOR_VOICE_ID"] = narrator_voice

        # Persist only the visible platform group, avoiding stale hidden fields.
        if provider == TTSProvider.MINIMAX.value:
            env_updates["NOVEL_FORGE_TTS_DEFAULT_MODEL"] = model
            minimax_key = self._minimax_key_input.text().strip()
            if minimax_key:
                env_updates["NOVEL_FORGE_TTS_MINIMAX_API_KEY"] = minimax_key
            env_updates["NOVEL_FORGE_TTS_MINIMAX_BASE_URL"] = (
                self._minimax_url_input.text().strip() or "https://api.minimax.io/v1"
            )
            minimax_group = self._minimax_group_input.text().strip()
            if minimax_group:
                env_updates["NOVEL_FORGE_TTS_MINIMAX_GROUP_ID"] = minimax_group
            env_updates["NOVEL_FORGE_TTS_MINIMAX_BITRATE"] = (
                self._minimax_bitrate_combo.currentData()
                or self._minimax_bitrate_combo.currentText()
            )
            env_updates["NOVEL_FORGE_TTS_MINIMAX_CHANNEL"] = (
                self._minimax_channel_combo.currentData()
                or self._minimax_channel_combo.currentText()
            )
            env_updates["NOVEL_FORGE_TTS_MINIMAX_LANGUAGE_BOOST"] = (
                self._minimax_language_combo.currentData()
                or self._minimax_language_combo.currentText()
            )
        elif provider in {TTSProvider.BAILIAN.value, TTSProvider.DASHSCOPE.value}:
            dashscope_key = self._dashscope_key_input.text().strip()
            if dashscope_key:
                env_updates["NOVEL_FORGE_TTS_DASHSCOPE_API_KEY"] = dashscope_key
            env_updates["NOVEL_FORGE_TTS_DASHSCOPE_MODEL"] = model
            preview_model = self._dashscope_preview_combo.currentData()
            env_updates["NOVEL_FORGE_TTS_DASHSCOPE_PREVIEW_MODEL"] = (
                str(preview_model)
                if preview_model is not None
                else self._dashscope_preview_combo.currentText()
            )
            env_updates["NOVEL_FORGE_TTS_DASHSCOPE_VOICE_CLONE_MODEL"] = (
                self._dashscope_clone_combo.currentData()
                or self._dashscope_clone_combo.currentText()
            )
            env_updates["NOVEL_FORGE_TTS_DASHSCOPE_VOICE_DESIGN_MODEL"] = (
                self._dashscope_design_combo.currentData()
                or self._dashscope_design_combo.currentText()
            )
            env_updates["NOVEL_FORGE_TTS_DASHSCOPE_BASE_URL"] = (
                self._dashscope_url_input.text().strip()
                or "https://dashscope.aliyuncs.com/api/v1"
            )
            env_updates["NOVEL_FORGE_TTS_DASHSCOPE_OPTIMIZE_INSTRUCTIONS"] = (
                self._dashscope_instruction_combo.currentData()
                or self._dashscope_instruction_combo.currentText()
            )
        elif provider == TTSProvider.TENCENT.value:
            env_updates["NOVEL_FORGE_TTS_TENCENT_VOICE_TYPE"] = model
            tencent_id = self._tencent_id_input.text().strip()
            tencent_key = self._tencent_key_input.text().strip()
            if tencent_id:
                env_updates["NOVEL_FORGE_TTS_TENCENT_SECRET_ID"] = tencent_id
            if tencent_key:
                env_updates["NOVEL_FORGE_TTS_TENCENT_SECRET_KEY"] = tencent_key
        elif provider == TTSProvider.VOLCENGINE_ARK.value:
            env_updates["NOVEL_FORGE_TTS_VOLCENGINE_MODEL"] = model
            env_updates["NOVEL_FORGE_TTS_VOLCENGINE_BASE_URL"] = (
                self._volcengine_url_input.text().strip()
                or "https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional"
            )
            volcengine_key = self._volcengine_key_input.text().strip()
            if volcengine_key:
                env_updates["NOVEL_FORGE_VOLCENGINE_ARK_API_KEY"] = volcengine_key
        elif provider == TTSProvider.MIMO.value:
            env_updates["NOVEL_FORGE_TTS_MIMO_MODEL"] = model
            env_updates["NOVEL_FORGE_TTS_MIMO_BASE_URL"] = (
                self._mimo_url_input.text().strip() or "https://api.xiaomimimo.com/v1"
            )
            mimo_key = self._mimo_key_input.text().strip()
            if mimo_key:
                env_updates["NOVEL_FORGE_TTS_MIMO_API_KEY"] = mimo_key
        elif provider == TTSProvider.LOCAL.value:
            env_updates["NOVEL_FORGE_TTS_LOCAL_BASE_URL"] = (
                self._local_url_input.text().strip() or "http://localhost:8000/v1"
            )
            env_updates["NOVEL_FORGE_TTS_LOCAL_API_KEY"] = self._local_key_input.text().strip()
            env_updates["NOVEL_FORGE_TTS_LOCAL_MODEL"] = model
        elif provider == TTSProvider.QWEN3.value:
            env_updates["NOVEL_FORGE_TTS_QWEN3_BASE_URL"] = (
                self._qwen3_url_input.text().strip() or "http://127.0.0.1:8011/v1"
            )
            env_updates["NOVEL_FORGE_TTS_QWEN3_API_KEY"] = self._qwen3_key_input.text().strip()
            env_updates["NOVEL_FORGE_TTS_QWEN3_FORMAL_MODEL"] = model
            env_updates["NOVEL_FORGE_TTS_QWEN3_PREVIEW_MODEL"] = (
                self._qwen3_preview_combo.currentText()
            )
            env_updates["NOVEL_FORGE_TTS_QWEN3_DESIGN_MODEL"] = (
                self._qwen3_design_combo.currentText()
            )
            env_updates["NOVEL_FORGE_TTS_QWEN3_CLONE_MODEL"] = self._qwen3_clone_combo.currentText()
        elif provider == TTSProvider.COSYVOICE.value:
            env_updates["NOVEL_FORGE_TTS_COSYVOICE_BASE_URL"] = (
                self._cosyvoice_url_input.text().strip() or "http://127.0.0.1:50000"
            )
            env_updates["NOVEL_FORGE_TTS_COSYVOICE_MODE"] = self._cosyvoice_mode_combo.currentText()
            env_updates["NOVEL_FORGE_TTS_COSYVOICE_MODEL"] = model
        elif provider == TTSProvider.OPENVOICE.value:
            env_updates["NOVEL_FORGE_TTS_OPENVOICE_CHECKPOINT_DIR"] = (
                self._openvoice_checkpoint_input.text().strip()
                or self._settings.tts_openvoice_checkpoint_dir
            )
            env_updates["NOVEL_FORGE_TTS_OPENVOICE_DEVICE"] = (
                self._openvoice_device_combo.currentText()
            )
            env_updates["NOVEL_FORGE_TTS_OPENVOICE_MODEL"] = model

        # Advanced parameters
        env_updates["NOVEL_FORGE_TTS_BACKGROUND_PIPELINE_CONCURRENCY"] = str(
            self._background_pipeline_spin.value()
        )
        env_updates["NOVEL_FORGE_TTS_SCRIPT_MAX_CONCURRENT_BATCHES"] = str(
            self._script_batch_concurrency_spin.value()
        )
        env_updates["NOVEL_FORGE_TTS_MAX_CONCURRENT_SYNTHESIS"] = str(self._concurrent_spin.value())
        env_updates["NOVEL_FORGE_TTS_SYNTHESIS_REQUESTS_PER_MINUTE"] = str(
            self._rate_limit_spin.value()
        )
        env_updates["NOVEL_FORGE_TTS_SYNTHESIS_RATE_LIMIT_COOLDOWN_S"] = str(
            self._rate_limit_cooldown_spin.value()
        )
        env_updates["NOVEL_FORGE_TTS_SYNTHESIS_RETRY_LIMIT"] = str(self._retry_spin.value())
        env_updates["NOVEL_FORGE_TTS_OUTPUT_FORMAT"] = self._format_combo.currentText()
        env_updates["NOVEL_FORGE_TTS_SAMPLE_RATE"] = self._sample_combo.currentText()
        env_updates["NOVEL_FORGE_TTS_VOICE_CLONE_TTL_DAYS"] = str(self._ttl_spin.value())
        env_updates["NOVEL_FORGE_TTS_PARALLEL_VOICE_CLONE"] = str(
            self._parallel_clone_check.isChecked()
        ).lower()
        env_updates["NOVEL_FORGE_TTS_VOICE_LLM_ADJUDICATION_ENABLED"] = str(
            self._voice_llm_adjudication_check.isChecked()
        ).lower()
        env_updates["NOVEL_FORGE_TTS_VOICE_LLM_ADJUDICATION_MIN_MATCH_SCORE"] = (
            f"{self._voice_llm_min_score_spin.value():.2f}"
        )
        env_updates["NOVEL_FORGE_TTS_VOICE_LLM_ADJUDICATION_MAX_CANDIDATES"] = str(
            self._voice_llm_max_candidates_spin.value()
        )
        env_updates["NOVEL_FORGE_TTS_VOICE_LLM_ADJUDICATION_CHOICES_PER_CHARACTER"] = str(
            self._voice_llm_choices_spin.value()
        )
        env_updates["NOVEL_FORGE_TTS_VOICE_LLM_ADJUDICATION_AUTO_SELECT_SCORE"] = (
            f"{self._voice_llm_auto_select_spin.value():.2f}"
        )
        env_updates["NOVEL_FORGE_TTS_VOICE_LLM_ADJUDICATION_MAX_OUTPUT_TOKENS"] = str(
            self._voice_llm_max_tokens_spin.value()
        )
        env_updates["NOVEL_FORGE_TTS_SCRIPT_LLM_REVIEW_ENABLED"] = str(
            self._script_llm_review_check.isChecked()
        ).lower()
        env_updates["NOVEL_FORGE_TTS_SCRIPT_LLM_REVIEW_MAX_OUTPUT_TOKENS"] = str(
            self._script_llm_review_tokens_spin.value()
        )
        # Persist TTS route-row temperature spins to .env
        _tts_temp_map = {
            "tts_generate_dubbing_script": "tts_script_generation_temperature",
            "tts_adjudicate_script_segments": "tts_review_adjudication_temperature",
            "tts_review_dubbing_script": "tts_review_adjudication_temperature",
            "tts_adjudicate_voice_match": "tts_review_adjudication_temperature",
            "tts_build_narrator_profile": "tts_narrator_profile_temperature",
            "tts_sound_design": "tts_sound_design_temperature",
        }
        for task_key, row in self._tts_route_rows.items():
            temp_attr = _tts_temp_map.get(task_key)
            if temp_attr and row.temperature_spin is not None:
                env_updates[f"NOVEL_FORGE_{temp_attr.upper()}"] = (
                    f"{row.temperature_spin.value():.2f}"
                )
        env_updates.update(self._sound_generation_env_updates())
        env_updates.update(self._audio_platform_env_updates())

        env_path = get_writable_env_path()
        DesktopSettingsStore.merge_env(env_path, env_updates)
        reset_settings()
        TTSAdapterRegistry.reset_instance()
        self._settings = get_settings()
        self._refresh_audio_execution_plan()
        try:
            self._save_tts_llm_routes()
        except Exception as exc:
            _logger.exception("Failed to save TTS LLM routes")
            self._status_badge.setText(f"TTS 平台设置已保存，但模型路由保存失败：{exc}")
            self._status_badge.set_tone("warning")
            return
        self._system_voices.clear()
        self._provider_capabilities.clear()
        self._set_provider_switch_state(provider, loading=True)
        self._load_system_voices()

        self._status_badge.setText(f"设置已原子保存到 {env_path.name}，新任务立即生效")
        self._status_badge.set_tone("success")

    def _save_tts_llm_routes(self) -> None:
        """Persist only Voice Studio's LLM route keys without touching others."""
        route_rows = getattr(self, "_tts_route_rows", {})
        if not route_rows:
            return

        config = load_or_import_profiles(self._settings)
        for task_key, row in route_rows.items():
            route = row.get_route()
            fallback_routes = row.get_fallback_routes()
            if route is not None:
                config.routes[task_key] = route
            else:
                config.routes.pop(task_key, None)
            if fallback_routes:
                config.fallback_routes[task_key] = fallback_routes[:3]
            else:
                config.fallback_routes.pop(task_key, None)

        config.save(get_profiles_path())

    # ─── Private slots: Cancel ─────────────────────────────────────────────

    def _on_cancel_clicked(self) -> None:
        """Handle cancel button click."""
        # Stop all animation timers so progress dots / synth spinners halt
        if self._script_gen_timer is not None:
            self._stop_visibility_periodic_timer(self._script_gen_timer)
        cancelled_chapter = self._generating_chapter
        if cancelled_chapter is not None:
            self._script_streams.pop(cancelled_chapter, None)
        self._generating_chapter = None
        self._hide_script_generation_progress()
        if self._synth_anim_timer is not None:
            self._stop_visibility_periodic_timer(self._synth_anim_timer)

        cancelled_voice_team_build = isinstance(self._current_worker, BuildVoiceTeamWorker)
        if self._current_worker:
            self._current_worker.request_cancel()
            # Detach worker so late-arriving signals are silently ignored
            self._current_worker = None

        # Release TTS operation lock so action buttons re-enable immediately
        self._finish_tts_operation()

        self._status_badge.setText("操作已取消")
        self._status_badge.set_tone("muted")
        if hasattr(self, "_automation_progress_badge"):
            self._automation_progress_badge.setText("已取消 · 进度已保留")
            self._automation_progress_badge.set_tone("muted")
        self._cancel_btn.setEnabled(False)
        self._build_team_btn.setEnabled(True)
        self._hide_voice_team_progress()
        if cancelled_voice_team_build:
            self._show_voice_team_task_feedback(
                state="已取消",
                tone="muted",
                title="配音团队构建已取消",
                detail="已保留已写入的角色音色与试听；可重新组建或继续编辑现有角色。",
                completed=0,
                total=1,
            )
        self._refresh_selected_voice_action_state()
        self._refresh_workflow_controls()
        if (
            cancelled_chapter is not None
            and cancelled_chapter == self._active_chapter_number
            and self._layout is not None
        ):
            self._load_chapter_artifacts(cancelled_chapter)

    # ─── Signal handlers ───────────────────────────────────────────────────

    def _on_step_progress(
        self,
        step: str,
        data: dict[str, Any],
        *,
        worker_id: str | None = None,
    ) -> None:
        """Handle step progress signal - update status badge and step indicator."""
        if worker_id is not None:
            current_worker_id = str(getattr(self._current_worker, "worker_id", "") or "")
            if not current_worker_id or worker_id != current_worker_id:
                return
        self._update_audio_phase_progress(step, data)
        self._update_script_generation_progress(step, data)
        task = str(data.get("task") or "").strip().lower()
        is_script_stream = task == TaskType.TTS_GENERATE_DUBBING_SCRIPT.value.lower()
        is_speaker_review_stream = task == TaskType.TTS_ADJUDICATE_SCRIPT_SEGMENTS.value.lower()
        if is_speaker_review_stream and step.startswith("llm_stream_"):
            self._status_badge.setText("正在用正文证据复核歧义对白的说话人…")
            self._status_badge.set_tone("warning")
            return
        # Drop a late-arriving llm_stream_start after the worker has already
        # terminated (success/fail/cancel). Terminal handlers pop the stream and
        # clear _generating_chapter; without this guard a tardy start could
        # resurrect a stale stream entry via _get_or_create_stream and write the
        # browser once. Other llm_stream_* events use .get() and return on None,
        # so they cannot resurrect a popped entry and need no guard.
        if step == "llm_stream_start" and is_script_stream and self._current_worker is None:
            return
        if step == "llm_stream_start" and is_script_stream:
            chapter = int(data.get("chapter") or self._generating_chapter or 0)
            if chapter:
                stream = self._get_or_create_stream(chapter)
                stream.stream_id = str(data.get("stream_id") or "")
                stream.text = ""
                stream.active = True
                # Reset incremental parse cache for the new stream.
                stream._cached_segments = []
                stream._cached_scan_pos = 0
                stream._cached_array_start = -1
                stream._cached_anomaly_report = None
                stream._cached_anomaly_seg_count = 0
                # Force first render so the empty-state placeholder appears.
                stream._force_render = True
                stream._last_rendered_segment_count = -1
            if chapter == self._active_chapter_number:
                if self._script_stream_follow is not None:
                    self._script_stream_follow.reset_to_latest()
                if self._script_gen_timer is not None:
                    self._stop_visibility_periodic_timer(self._script_gen_timer)
                self._render_script_stream()
            self._status_badge.setText(f"正在流式生成第 {chapter} 章配音脚本…")
            self._status_badge.set_tone("warning")
            return
        if step == "llm_stream_delta" and is_script_stream:
            chapter = int(data.get("chapter") or self._generating_chapter or 0)
            if not chapter:
                return
            stream = self._script_streams.get(chapter)
            if stream is None:
                return
            stream_id = str(data.get("stream_id") or "")
            if stream.stream_id and stream_id != stream.stream_id:
                return
            segments = data.get("segments")
            if isinstance(segments, list):
                for item in segments:
                    if isinstance(item, dict) and item.get("kind") == "content":
                        stream.text += str(item.get("text") or "")
            stream.active = True
            # Track activity for stall detection.
            import time as _time

            self._last_stream_activity_at = _time.monotonic()
            if self._stream_stall_warned:
                self._stream_stall_warned = False
                self._status_badge.setText(f"正在流式生成第 {chapter} 章配音脚本…")
                self._status_badge.set_tone("warning")
            if chapter == self._active_chapter_number:
                self._schedule_stream_render()
                # Update progress bar with streaming activity so the UI
                # does not appear frozen during long batch LLM calls.
                self._update_stream_progress_from_delta(stream)
            return
        if step == "llm_stream_end" and is_script_stream:
            chapter = int(data.get("chapter") or self._generating_chapter or 0)
            if chapter:
                stream = self._script_streams.get(chapter)
                if stream is not None:
                    final_text = str(data.get("text") or "")
                    if final_text:
                        stream.text = final_text
                    stream.active = True
                    # Force final render to show the complete stream output.
                    stream._force_render = True
            if chapter == self._active_chapter_number:
                self._render_script_stream()
                # Force a final progress-bar refresh so the last received char
                # count is shown (delta updates are throttled during streaming).
                _end_stream = self._script_streams.get(chapter)
                if _end_stream is not None:
                    self._update_stream_progress_from_delta(_end_stream, force=True)
            self._status_badge.setText(f"第 {chapter} 章流式输出完成，正在校验角色与导演指令…")
            self._status_badge.set_tone("warning")
            self._update_script_generation_progress(
                "llm_stream_end", {"chapter": chapter}
            )
            return
        if step == "llm_stream_restart" and is_script_stream:
            chapter = int(data.get("chapter") or self._generating_chapter or 0)
            if chapter:
                stream = self._script_streams.get(chapter)
                if stream is not None:
                    stream.stream_id = ""
                    stream.text = ""
                    stream.active = False
                    # Reset incremental parse cache on stream restart.
                    stream._cached_segments = []
                    stream._cached_scan_pos = 0
                    stream._cached_array_start = -1
                    stream._cached_anomaly_report = None
                    stream._cached_anomaly_seg_count = 0
                    stream._force_render = True
                    stream._last_rendered_segment_count = -1
            if chapter == self._active_chapter_number:
                if self._script_stream_follow is not None:
                    self._script_stream_follow.reset_to_latest()
            self._status_badge.setText("流式连接已重启，正在重新生成完整脚本…")
            self._status_badge.set_tone("warning")
            return
        if step == "llm_stream_error" and is_script_stream:
            chapter = int(data.get("chapter") or self._generating_chapter or 0)
            if chapter:
                stream = self._script_streams.get(chapter)
                if stream is not None:
                    stream.active = False
            self._status_badge.setText("流式输出中断，正在执行安全重试或规则回退…")
            self._status_badge.set_tone("warning")
            return
        # Automatic cast is a five-stage workflow.  Keep one monotonic overall
        # progress bar while still exposing the honest count within each stage.
        if step == "voice_team_workflow_start":
            total = int(data.get("total") or 0)
            provider = str(data.get("provider") or "当前平台")
            self._show_voice_team_progress(
                completed=0,
                total=2,
                phase_index=1,
                phase_label="前置准备",
                detail=f"{total} 个角色 · {provider}",
            )
            self._status_badge.setText(f"阶段 1/5 · 正在检查旁白音色与 {provider} 平台…")
            self._status_badge.set_tone("warning")
            return
        if step == "voice_team_narrator_check_start":
            self._show_voice_team_progress(
                completed=0,
                total=2,
                phase_index=1,
                phase_label="前置准备",
                detail="检查旁白音色",
            )
            self._status_badge.setText("阶段 1/5 · 正在校验旁白声音画像…")
            self._status_badge.set_tone("warning")
            return
        if step == "voice_team_narrator_ready":
            rebuilt = bool(data.get("rebuilt"))
            self._show_voice_team_progress(
                completed=1,
                total=2,
                phase_index=1,
                phase_label="前置准备",
                detail="旁白已就绪 · 检查平台",
            )
            action = "已重新生成" if rebuilt else "已复用"
            self._status_badge.setText(f"阶段 1/5 · 旁白音色{action}，正在检查平台运行环境…")
            self._status_badge.set_tone("warning")
            return
        if step == "voice_team_runtime_check_start":
            provider = str(data.get("provider") or "当前平台")
            self._show_voice_team_progress(
                completed=1,
                total=2,
                phase_index=1,
                phase_label="前置准备",
                detail=f"连接 {provider}",
            )
            self._status_badge.setText(f"阶段 1/5 · 正在连接 {provider} 并检查运行环境…")
            self._status_badge.set_tone("warning")
            return
        if step == "voice_team_runtime_check_done":
            self._show_voice_team_progress(
                completed=2,
                total=2,
                phase_index=1,
                phase_label="前置准备",
                detail="平台已就绪",
            )
            self._status_badge.setText("阶段 1/5 · 平台已就绪，即将同步音色目录…")
            self._status_badge.set_tone("warning")
            return
        if step in {"voice_team_cast_phase_start", "voice_catalog_sync_start"}:
            total = int(data.get("total") or 0)
            detail = f"同步平台音色 · {total} 个角色" if total else "同步平台音色"
            self._show_voice_team_progress(
                completed=0,
                total=2,
                phase_index=2,
                phase_label="目录与匹配",
                detail=detail,
            )
            self._status_badge.setText("阶段 2/5 · 正在获取平台音色目录…")
            self._status_badge.set_tone("warning")
            return
        if step == "voice_catalog_sync_done":
            voices = int(data.get("voices") or 0)
            available = bool(data.get("available"))
            self._show_voice_team_progress(
                completed=1,
                total=2,
                phase_index=2,
                phase_label="目录与匹配",
                detail=f"音色目录 {voices} 条",
            )
            if available:
                message = f"阶段 2/5 · 已载入 {voices} 个平台音色，正在计算角色匹配…"
            else:
                message = "阶段 2/5 · 音色目录暂不可用，正在用本地证据继续匹配…"
            self._status_badge.setText(message)
            self._status_badge.set_tone("warning")
            return
        if step == "voice_semantic_retrieval_start":
            characters = int(data.get("characters") or 0)
            total_characters = int(data.get("total_characters") or characters)
            voices = int(data.get("catalog_voices") or 0)
            character_detail = (
                f"仅需重新匹配 {characters}/{total_characters} 个角色"
                if characters < total_characters
                else f"{characters} 个角色"
            )
            self._show_voice_team_progress(
                completed=1,
                total=2,
                phase_index=2,
                phase_label="目录与匹配",
                detail=f"{character_detail} × {voices} 个音色",
            )
            self._status_badge.setText(f"阶段 2/5 · {character_detail}，正在计算音色匹配…")
            self._status_badge.set_tone("warning")
            return
        if step == "voice_semantic_retrieval_done":
            self._show_voice_team_progress(
                completed=2,
                total=2,
                phase_index=2,
                phase_label="目录与匹配",
                detail="匹配证据已就绪",
            )
            self._status_badge.setText("阶段 2/5 · 匹配证据已就绪，即将分配角色音色…")
            self._status_badge.set_tone("warning")
            return
        if step == "voice_assignment_batch_start":
            completed = int(data.get("completed") or 0)
            total = max(1, int(data.get("total") or 0))
            parallel = bool(data.get("parallel"))
            self._show_voice_team_progress(
                completed=completed,
                total=total,
                phase_index=3,
                phase_label="角色分配",
                detail=f"{completed}/{total}",
            )
            mode = "并行设计与匹配" if parallel else "逐个设计与匹配"
            self._status_badge.setText(f"阶段 3/5 · 正在{mode} {total} 个角色音色…")
            self._status_badge.set_tone("warning")
            return
        if step == "character_voice_assignment_started":
            completed = int(data.get("completed") or 0)
            total = max(1, int(data.get("total") or 0))
            name = str(data.get("character_name") or "当前角色")
            self._show_voice_team_progress(
                completed=completed,
                total=total,
                phase_index=3,
                phase_label="角色分配",
                detail=f"{completed}/{total} · {name}",
            )
            self._status_badge.setText(
                f"阶段 3/5 · 正在为 {name} 选音或设计音色 · {completed}/{total}"
            )
            self._status_badge.set_tone("warning")
            return
        if step == "minimax_voice_activation_batch_start":
            total = int(data.get("total") or 0)
            self._show_voice_team_progress(
                completed=0,
                total=max(1, total),
                phase_index=4,
                phase_label="音色激活",
                detail=f"待激活 {total} 个",
            )
            message = (
                f"阶段 4/5 · 正在激活 {total} 个 MiniMax 新音色…"
                if total
                else "阶段 4/5 · 没有需要激活的新音色，即将准备试听…"
            )
            self._status_badge.setText(message)
            self._status_badge.set_tone("warning")
            return
        if step in {"minimax_voice_activation_started", "minimax_voice_activation_progress"}:
            completed = int(data.get("completed") or 0)
            total = max(1, int(data.get("total") or 0))
            label = str(data.get("label") or "当前角色")
            self._show_voice_team_progress(
                completed=completed,
                total=total,
                phase_index=4,
                phase_label="音色激活",
                detail=f"{completed}/{total} · {label}",
            )
            verb = "已处理" if step.endswith("progress") else "正在激活"
            self._status_badge.setText(
                f"阶段 4/5 · {verb} {label} 的 MiniMax 音色 · {completed}/{total}"
            )
            self._status_badge.set_tone("warning")
            return
        if step == "minimax_voice_activation_batch_done":
            total = int(data.get("total") or 0)
            activated = int(data.get("activated") or 0)
            self._show_voice_team_progress(
                completed=max(1, total),
                total=max(1, total),
                phase_index=4,
                phase_label="音色激活",
                detail=f"已激活 {activated}/{total}",
            )
            self._status_badge.setText(
                f"阶段 4/5 · 音色激活处理完成 · {activated}/{total}，即将生成试听…"
            )
            self._status_badge.set_tone("warning")
            return
        if step == "voice_team_workflow_done":
            total = int(data.get("total") or 0)
            self._show_voice_team_progress(
                completed=1,
                total=1,
                phase_index=5,
                phase_label="试听准备",
                detail="全部完成",
            )
            self._status_badge.setText(f"阶段 5/5 · 配音团队与 {total} 个角色试听已就绪")
            self._status_badge.set_tone("success")
            return
        progress_labels = {
            "tts_automation_mode": "配音推进策略已锁定，正在检查前置产物…",
            "tts_manual_prerequisites_ready": "已确认配音团队与脚本，正在按现状成片…",
            "tts_narrator_start": "正在准备旁白声音画像…",
            "tts_narrator_llm_call": "正在生成旁白声音画像…",
            "tts_narrator_done": "旁白声音画像已就绪",
            "tts_narrator_rule_fallback": "旁白画像服务不可用，已使用规则兜底",
            "tts_voice_team_reused": "配音团队有效，已复用",
            "tts_voice_team_narrator_only": "未配置角色，已切换为旁白配音模式",
            "tts_script_start": "正在生成配音脚本…",
            "tts_script_llm_call": "正在分析台词与情绪…",
            "tts_script_parsed": "配音脚本已解析",
            "tts_script_rule_fallback": "脚本服务不可用，已使用规则分段",
            "tts_speaker_adjudication_start": "正在复核歧义对白的说话人…",
            "tts_speaker_adjudication_complete": "说话人证据复核完成",
            "tts_script_segment_adjudication_start": "正在裁决歧义片段的声音角色…",
            "tts_script_segment_adjudication_complete": "声音角色与说话人裁决完成",
            "tts_script_segment_adjudication_unavailable": "声音角色裁决暂不可用，已安全回退并标记复核",
            "tts_script_professional_review_complete": "专业配音脚本审校完成，已校正表演参数并补齐事件音效",
            "tts_script_llm_review_start": "正在进行独立的配音脚本专业审校…",
            "tts_script_llm_review_complete": "配音脚本专业审校完成，安全修复与人工复核项已记录",
            "tts_script_llm_review_unavailable": "专业审校暂不可用，已保留确定性审校结果",
            "tts_script_llm_review_skipped": "专业审校已关闭，继续使用确定性保真检查",
            "tts_spoken_rewrite_batch_start": "正在将文学文本改写为口语朗读版…",
            "tts_spoken_rewrite_complete": "口语改写完成，已写入配音脚本",
            "tts_spoken_rewrite_batch_failed": "口语改写部分失败，已安全回退为原文朗读",
            "tts_voice_llm_adjudication_start": "正在复核低置信度的角色音色…",
            "tts_voice_llm_adjudication_done": "角色音色复核完成，结果已写入匹配说明",
            "tts_voice_llm_adjudication_skipped": "未触发角色音色 LLM 复核，已保留规则与向量匹配结果",
            "tts_script_done": "配音脚本已就绪",
            "tts_script_reused": "配音脚本未变，已复用",
            "tts_synthesis_start": "正在合成缺失音频段…",
            "tts_synthesis_reused": "人声片段已复用，跳过重复合成…",
            "tts_synthesis_complete": "音频段合成完成，正在准备场景声音…",
            "tts_alignment_start": "正在核验台词并建立真实字词时间线…",
            "tts_alignment_complete": "真实人声时间线已就绪，正在解析声音锚点…",
            "tts_sound_generation_start": "正在解析并补充缺失的场景声音…",
            "tts_sound_generation_cue": "正在生成场景声音资产…",
            "tts_sound_generation_complete": "场景声音资产已就绪，正在装配…",
            "tts_assembly_start": "正在统一装配音频、转场与背景声…",
            "tts_quality_complete": "成片响度、峰值与对齐质量检查完成",
            "tts_assembly_complete": "音频装配完成",
            "tts_assembly_partial": "部分音频段失败，可直接续跑",
            "tts_assembly_failed": "音频装配失败；音频段进度已保留，可续跑",
        }
        if step in progress_labels:
            message = progress_labels[step]
            if step == "tts_sound_generation_start":
                missing = int(data.get("missing") or 0)
                message = f"正在补齐 {missing} 个缺失的 BGM / 环境声 / 音效候选…"
            elif step == "tts_script_professional_review_complete":
                removed = int(data.get("removed_numeric_overrides") or 0)
                recovered = int(data.get("recovered_event_sfx") or 0)
                message = f"专业脚本审校完成 · 稳定表演参数 {removed} 项 · 事件音效 {recovered} 处"
            elif step == "tts_voice_llm_adjudication_start":
                candidates = int(data.get("candidate_count") or 0)
                choices = int(data.get("voice_choice_count") or 0)
                message = (
                    f"阶段 3/5 · LLM 正在为 {candidates} 个角色比较 {choices} 个硬约束合格音色…"
                )
                self._show_voice_team_progress(
                    completed=1,
                    total=1,
                    phase_index=3,
                    phase_label="角色分配",
                    detail=f"{candidates} 个角色 · {choices} 个音色",
                )
            elif step == "tts_voice_llm_adjudication_done":
                candidates = int(data.get("candidate_count") or 0)
                decisions = int(data.get("decision_count") or 0)
                recast = int(data.get("recast_count") or 0)
                audition = int(data.get("audition_count") or 0)
                rejected = int(data.get("rejected_count") or 0)
                message = (
                    f"阶段 3/5 · LLM 选角完成 {decisions}/{candidates} · "
                    f"自动改配 {recast} · 待对比试听 {audition} · 全部否决 {rejected}"
                )
            elif step == "tts_sound_generation_cue":
                cue = str(data.get("cue") or "当前声音")
                current = int(data.get("current") or 0)
                total = int(data.get("total") or 0)
                count_suffix = f" · {current}/{total}" if current and total else ""
                message = f"正在生成：{cue}{count_suffix}"
            elif step == "tts_sound_generation_complete":
                message = (
                    f"声音处理完成 · 已生成 {int(data.get('generated') or 0)} · "
                    f"待试听 {int(data.get('pending_review') or 0)} · "
                    f"失败 {int(data.get('failed') or 0)}"
                )
            elif step == "tts_quality_complete":
                message = (
                    "成片质检通过"
                    if bool(data.get("passed"))
                    else "成片质检未通过，正在保留阻塞原因"
                )
            self._status_badge.setText(message)
            if step in {"tts_assembly_partial", "tts_assembly_failed"} or (
                step == "tts_quality_complete" and data.get("passed") is False
            ):
                tone = "danger"
            elif step.endswith(("start", "call", "cue")) or step in {
                "tts_automation_mode",
                "tts_alignment_complete",
                "tts_synthesis_complete",
                "tts_sound_generation_complete",
                "tts_assembly_start",
            }:
                tone = "warning"
            else:
                tone = "success"
            self._status_badge.set_tone(tone)
            return
        # Build-voice-team specific progress events
        if step == "build_voice_team_start":
            total = data.get("total", 0)
            self._show_voice_team_progress(
                completed=0,
                total=2,
                phase_index=2,
                phase_label="目录与匹配",
                detail=f"准备 {total} 个角色",
            )
            self._status_badge.setText(f"阶段 2/5 · 正在为 {total} 个角色准备匹配证据…")
            self._status_badge.set_tone("warning")
            return
        if step == "build_voice_team_progress":
            completed = int(data.get("completed") or 0)
            total = int(data.get("total") or 0)
            name = str(data.get("character_name") or "当前角色")
            status = str(data.get("status") or "assigned")
            self._show_voice_team_progress(
                completed=completed,
                total=max(1, total),
                phase_index=3,
                phase_label="角色分配",
                detail=f"{completed}/{total} · {name}",
            )
            if status == "reused":
                detail = f"已复用 {name} 的现有音色"
            elif status == "failed":
                detail = f"{name} 分配失败，正在继续处理"
            else:
                detail = f"已完成 {name} 的音色分配"
            self._status_badge.setText(f"阶段 3/5 · {detail} · {completed}/{total}")
            self._status_badge.set_tone("warning" if status != "failed" else "danger")
            return
        if step == "character_voice_assigned":
            name = data.get("character_name", "")
            source = data.get("voice_source", "")
            source_label = {
                "designed": "AI设计",
                "cloned": "克隆",
                "system": "系统匹配",
                "manual": "手动指定",
                "library": "音色库",
            }.get(source, source)
            self._status_badge.setText(f"已为 {name} 分配音色 ({source_label})")
            self._status_badge.set_tone("warning")
            return
        if step == "character_voice_recast":
            name = str(data.get("character_name") or "当前角色")
            confidence = float(data.get("confidence") or 0.0)
            self._status_badge.setText(f"LLM 已在硬约束候选内为 {name} 自动改配 · {confidence:.0%}")
            self._status_badge.set_tone("success")
            return
        if step == "build_voice_team_done":
            completed = int(data.get("completed") or data.get("entries") or 0)
            total = int(data.get("total") or completed)
            self._show_voice_team_progress(
                completed=max(1, completed),
                total=max(1, total),
                phase_index=3,
                phase_label="角色分配",
                detail=f"{completed}/{total} 已完成",
            )
            self._status_badge.setText(
                f"阶段 3/5 · 音色分配完成 · {completed}/{total}，正在保存团队…"
            )
            self._status_badge.set_tone("warning")
            return
        if step == "voice_preview_batch_start":
            total = int(data.get("total") or 0)
            self._show_voice_team_progress(
                completed=0,
                total=max(1, total),
                phase_index=5,
                phase_label="试听准备",
                detail=f"0/{total}",
            )
            self._status_badge.setText(f"阶段 5/5 · 正在准备 {total} 个角色试听…")
            self._status_badge.set_tone("warning")
            return
        if step == "voice_preview_started":
            completed = int(data.get("completed") or 0)
            total = int(data.get("total") or 0)
            name = str(data.get("character_name") or "当前角色")
            self._show_voice_team_progress(
                completed=completed,
                total=max(1, total),
                phase_index=5,
                phase_label="试听准备",
                detail=f"{completed}/{total} · {name}",
            )
            self._status_badge.setText(f"阶段 5/5 · 正在生成 {name} 的试听 · {completed}/{total}")
            self._status_badge.set_tone("warning")
            return
        if step in {"voice_preview_ready", "voice_preview_failed"}:
            completed = int(data.get("completed") or 0)
            total = int(data.get("total") or 0)
            name = str(data.get("character_name") or "当前角色")
            self._show_voice_team_progress(
                completed=completed,
                total=max(1, total),
                phase_index=5,
                phase_label="试听准备",
                detail=f"{completed}/{total} · {name}",
            )
            if step == "voice_preview_ready":
                self._status_badge.setText(f"阶段 5/5 · {name} 试听已准备 · {completed}/{total}")
                self._status_badge.set_tone("success")
            else:
                self._status_badge.setText(f"{name} 试听生成失败，音色分配仍已保留")
                self._status_badge.set_tone("warning")
            return
        if step == "voice_preview_batch_done":
            total = int(data.get("total") or 0)
            ready = int(data.get("ready") or 0)
            self._show_voice_team_progress(
                completed=max(1, total),
                total=max(1, total),
                phase_index=5,
                phase_label="试听准备",
                detail=f"{ready}/{total} 可播放",
            )
            self._status_badge.setText(f"阶段 5/5 · 角色试听准备完成 · {ready}/{total} 可直接播放")
            self._status_badge.set_tone("success" if ready == total else "warning")
            return
        # Voice library events
        if step == "voice_library_hit":
            name = data.get("character_name", "")
            self._status_badge.setText(f"从音色库复用 {name} 的音色")
            self._status_badge.set_tone("success")
            return
        if step == "voice_library_saved":
            name = data.get("character_name", "")
            self._status_badge.setText(f"已将 {name} 的音色存入全局音色库")
            self._status_badge.set_tone("success")
            return
        if step == "voice_design_fallback":
            name = data.get("character_name", "")
            self._status_badge.setText(f"{name} AI 设计失败，已用系统音色兜底")
            self._status_badge.set_tone("warning")
            return
        if step == "voice_design_unavailable":
            name = data.get("character_name", "")
            self._status_badge.setText(f"{name} 当前平台不支持 AI 设计，已用系统音色匹配")
            self._status_badge.set_tone("muted")
            return
        if step == "narrator_voice_ready":
            source = {"designed": "作品特征设计", "manual": "人工指定"}.get(
                str(data.get("source") or ""),
                "已分配",
            )
            self._status_badge.setText(f"旁白音色已就绪：{source}")
            self._status_badge.set_tone("success")
            return
        if step == "narrator_voice_fallback":
            self._status_badge.setText("旁白专属设计不可用，已匹配系统音色")
            self._status_badge.set_tone("warning")
            return

        self._status_badge.setText(f"步骤: {step}")
        self._status_badge.set_tone("warning")

    def _open_voice_preview_dialog(self) -> None:
        """Open the multi-candidate voice A/B preview dialog (P1)."""
        if self._tts_operation is not None:
            self._clear_operation_is_running("音色对比")
            return
        characters = list(self._bible_characters or [])
        if not characters and self._voice_team:
            characters = [
                {
                    "character_id": entry.character_id,
                    "name": entry.character_name,
                    "gender": entry.character_gender,
                }
                for entry in self._voice_team.entries
            ]
        if not characters:
            self._status_badge.setText("角色圣经中暂无角色，请先创建角色")
            self._status_badge.set_tone("warning")
            return
        dialog = VoicePreviewDialog(
            settings=self._settings,
            layout=self._layout,
            characters=characters,
            provider=self._get_current_provider(),
            parent=self,
        )
        dialog.voice_team_updated.connect(self._on_voice_preview_team_confirmed)
        dialog.worker_failed.connect(self._on_voice_preview_worker_failed)
        dialog.exec()

    def _on_voice_preview_team_confirmed(self, data: dict[str, Any]) -> None:
        """Refresh the studio after a candidate voice was persisted to the team."""
        try:
            self._voice_team = VoiceTeamContract.model_validate(data)
        except Exception as exc:
            self._status_badge.setText(f"配音团队解析失败: {exc}")
            self._status_badge.set_tone("danger")
            return
        self._update_character_list()
        self._update_avatars()
        self._status_badge.setText("音色已确认并写入配音团队，可继续试听或正式合成")
        self._status_badge.set_tone("success")
        self._refresh_selected_voice_action_state()

    def _on_voice_preview_worker_failed(self, payload: dict[str, Any]) -> None:
        """Surface candidate-generation/confirmation failures on the page badge."""
        message = str(
            payload.get("message")
            or payload.get("detail")
            or payload.get("summary")
            or "音色对比失败"
        )
        self._status_badge.setText(message)
        self._status_badge.set_tone("danger")

    def _on_voice_team_updated(
        self,
        data: dict[str, Any],
        *,
        worker: Any = None,
        rebuild_character_ids: list[str] | None = None,
    ) -> None:
        """Handle voice team updated signal."""
        # Guard against late signals arriving after user cancelled the operation
        if self._current_worker is None or (
            worker is not None and worker is not self._current_worker
        ):
            return
        completed_voice_team_build = isinstance(self._current_worker, BuildVoiceTeamWorker)
        self._voice_team = VoiceTeamContract.model_validate(data)
        self._current_worker = None
        if rebuild_character_ids is None:
            self._update_character_list()
            updated_count = len(self._voice_team.entries)
        else:
            if not self._update_rebuilt_character_rows(rebuild_character_ids):
                self._update_character_list()
            updated_count = len(rebuild_character_ids)
        self._update_avatars()
        preview_ready = sum(
            bool(
                any(
                    vp.audio_path and Path(vp.audio_path).is_file()
                    for vp in entry.preview_variants.values()
                )
                or (entry.preview_audio_path and Path(entry.preview_audio_path).is_file())
            )
            for entry in self._voice_team.entries
        )
        self._status_badge.setText(
            f"配音团队已更新 · {updated_count} 个角色 · {preview_ready} 个试听可播放"
        )
        self._status_badge.set_tone(
            "success" if preview_ready == len(self._voice_team.entries) else "warning"
        )
        # Update confirm-team button visibility: show only when team has
        # entries; enable only when confirmed=False and every voice is ready.
        team_has_entries = len(self._voice_team.entries) > 0
        if team_has_entries and self._voice_team.confirmed:
            self._confirm_team_btn.setText("已确认")
            self._confirm_team_btn.setVisible(True)
            self._confirm_team_btn.setEnabled(False)
        elif team_has_entries and not self._voice_team.confirmed:
            # execute_confirm_voice_team also hard-blocks on a missing narrator,
            # so keep the button disabled until the narrator exists too —
            # otherwise the user can click an action that is guaranteed to fail.
            narrator_ok = bool(self._voice_team.narrator_voice_id)
            all_ready = narrator_ok and all(
                e.is_ready and not e.is_expired and e.provider == self._voice_team.default_provider
                for e in self._voice_team.entries
            )
            self._confirm_team_btn.setText("确认整支团队")
            self._confirm_team_btn.setVisible(True)
            self._confirm_team_btn.setEnabled(all_ready)
        else:
            self._confirm_team_btn.setVisible(False)
        self._cancel_btn.setEnabled(False)
        self._build_team_btn.setEnabled(True)
        self._hide_voice_team_progress()
        if completed_voice_team_build:
            outcome_detail = (
                f"已更新 {updated_count} 个角色；{preview_ready} 个角色试听可直接播放。"
            )
            if preview_ready != len(self._voice_team.entries):
                outcome_detail += " 未就绪的试听可从角色详情单独重试，不影响已保存的音色分配。"
            self._show_voice_team_task_feedback(
                state="已完成"
                if preview_ready == len(self._voice_team.entries)
                else "已完成 · 部分试听待补齐",
                tone="success" if preview_ready == len(self._voice_team.entries) else "warning",
                title="配音团队已更新",
                detail=outcome_detail,
                completed=1,
                total=1,
            )
        self._refresh_selected_voice_action_state()
        self._refresh_workflow_controls()

    def _on_narrator_profile_updated(self, data: dict[str, Any], *, worker: Any = None) -> None:
        """Refresh the visible narrator row after its profile and voice are persisted."""
        if worker is not None and worker is not self._current_worker:
            return
        building_voice_team = isinstance(self._current_worker, BuildVoiceTeamWorker)
        self._narrator_profile = NarratorVoiceProfile.model_validate(data)
        if not building_voice_team:
            self._current_worker = None
        self._update_character_list()
        self._update_avatars()
        source = {"designed": "专属音色", "manual": "人工指定音色", "system": "系统匹配音色"}.get(
            self._narrator_profile.voice_source,
            "音色",
        )
        if self._narrator_profile.is_expired:
            self._status_badge.setText("旁白音色已过期，请重新构建")
            self._status_badge.set_tone("warning")
        elif self._narrator_profile.voice_id:
            self._status_badge.setText(f"旁白已构建：{source}，现在可试听")
            self._status_badge.set_tone("success")
        else:
            self._status_badge.setText("旁白画像已更新，但尚未获得可用音色")
            self._status_badge.set_tone("warning")
        if building_voice_team:
            self._status_badge.setText("旁白音色已就绪，正在继续组建角色音色...")
            self._status_badge.set_tone("warning")
            return
        self._cancel_btn.setEnabled(False)

    def _on_script_updated(
        self,
        data: dict[str, Any],
        *,
        worker_id: str | None = None,
    ) -> None:
        """Handle script updated signal."""
        # Guard against late signals arriving after user cancelled the operation
        if self._current_worker is None:
            return
        if worker_id is not None:
            current_worker_id = str(getattr(self._current_worker, "worker_id", "") or "")
            if not current_worker_id or worker_id != current_worker_id:
                return
        if self._script_gen_timer is not None:
            self._stop_visibility_periodic_timer(self._script_gen_timer)
        self._stop_stream_stall_timer()
        finished_chapter = self._generating_chapter
        if finished_chapter is not None:
            self._script_streams.pop(finished_chapter, None)
        self._generating_chapter = None
        self._hide_script_generation_progress()
        self._current_script = DubbingScript.model_validate(data)
        self._source_script_available = True
        self._active_chapter_number = self._current_script.chapter_number
        self._select_chapter(
            getattr(self, "_chapter_combo", None), self._current_script.chapter_number
        )
        self._select_chapter(
            getattr(self, "_audio_chapter_combo", None), self._current_script.chapter_number
        )
        self._select_chapter(
            getattr(self, "_room_chapter_combo", None), self._current_script.chapter_number
        )
        self._current_audio_result = None
        self._current_results = []
        self._current_timeline = None
        self._segment_status.clear()
        self._room_draft_segments.clear()
        self._room_candidate_takes.clear()
        self._room_loaded_audio_path = ""
        self._update_avatars()
        self._render_script_html()
        self._populate_voice_room_segments()
        self._render_mix_manifest()
        self._update_script_source_hint()
        # One-time tooltip explaining the streaming preview was validated.
        hint = getattr(self, "_script_source_hint", None)
        if hint is not None:
            hint.setToolTip(
                "流式预览已经后端保真校验，重复/异常内容已被自动处理"
            )
        if hasattr(self, "_sound_resolution_badge"):
            cue_count = (
                len(self._current_script.soundscapes)
                + len(self._current_script.bgm_suggestions)
                + len(self._current_script.sfx_cues)
            )
            auto_design = bool(
                isinstance(self._current_script.metadata.get("soundscape_design"), dict)
                and self._current_script.metadata["soundscape_design"].get("mode")
                == "automatic_backfill"
            )
            self._sound_resolution_badge.setText(
                (
                    f"场景声音: {cue_count} 项待合成匹配（含自动声场）"
                    if auto_design
                    else f"场景声音: {cue_count} 项待合成匹配"
                )
                if cue_count
                else "场景声音: 本章未设计"
            )
            self._sound_resolution_badge.set_tone("warning" if cue_count else "muted")
        segment_count = len(self._current_script.segments)
        unresolved_count = len(unresolved_speaker_indices(self._current_script))
        if unresolved_count:
            self._status_badge.setText(
                f"脚本已生成: {segment_count} 段 · {unresolved_count} 段说话人待复核"
            )
            self._status_badge.set_tone("warning")
        else:
            self._status_badge.setText(f"脚本已生成: {segment_count} 段")
            self._status_badge.set_tone("success")
        self._cancel_btn.setEnabled(False)
        if self._tts_operation is not None and self._tts_operation[0] == "script":
            self._current_worker = None
            self._finish_tts_operation()
        self._refresh_workflow_controls()
        # Auto-open speaker review dialog when unresolved speakers exist.
        if unresolved_count and self._current_script is not None:
            _first_unresolved = unresolved_speaker_indices(self._current_script)[0]
            QTimer.singleShot(400, lambda: self._on_edit_script(_first_unresolved))

    def _on_segment_progress(self, segment_idx: int, status_str: str) -> None:
        """Handle per-segment synthesis progress."""
        self._segment_status[segment_idx] = status_str
        if (
            status_str == "synthesizing"
            and self._synth_anim_timer is not None
            and not self._synth_anim_timer.isActive()
        ):
            self._start_visibility_periodic_timer(self._synth_anim_timer)
        if status_str != "synthesizing":
            any_synth = any(s == "synthesizing" for s in self._segment_status.values())
            if not any_synth and self._synth_anim_timer is not None:
                self._stop_visibility_periodic_timer(self._synth_anim_timer)
                self._render_script_html()
                return
        self._render_script_html()
        total = len(self._current_script.segments) if self._current_script else 0
        completed = sum(1 for s in self._segment_status.values() if s == "completed")
        if total > 0 and hasattr(self, "_progress_badge"):
            self._progress_badge.setText(f"进度: {completed}/{total} 段")
            self._progress_badge.set_tone("default")

    def _on_synthesis_progress(self, completed: int, total: int) -> None:
        """Handle synthesis progress signal."""
        if hasattr(self, "_progress_badge"):
            self._progress_badge.setText(f"进度: {completed}/{total} 段")
            self._progress_badge.set_tone("default")

    def _update_sound_resolution_badge(self, metadata: Any) -> None:
        """Show whether generated ambience/BGM/SFX cues found usable project assets."""
        sound_resolution = metadata.get("sound_resolution") if isinstance(metadata, dict) else None
        if not isinstance(sound_resolution, dict) or not hasattr(self, "_sound_resolution_badge"):
            return
        matched = int(sound_resolution.get("matched", 0) or 0)
        unresolved = int(sound_resolution.get("unresolved", 0) or 0)
        generation = metadata.get("sound_generation") if isinstance(metadata, dict) else None
        generated = int(generation.get("generated", 0) or 0) if isinstance(generation, dict) else 0
        cached = int(generation.get("cached", 0) or 0) if isinstance(generation, dict) else 0
        pending = (
            int(generation.get("pending_review", 0) or 0) if isinstance(generation, dict) else 0
        )
        failed = int(generation.get("failed", 0) or 0) if isinstance(generation, dict) else 0
        generation_parts: list[str] = []
        if generated:
            generation_parts.append(f"新生成 {generated}")
        if cached:
            generation_parts.append(f"复用 {cached}")
        if pending:
            generation_parts.append(f"待审核 {pending}")
        if failed:
            generation_parts.append(f"生成失败 {failed}")
        generation_suffix = f" · {' · '.join(generation_parts)}" if generation_parts else ""
        total = matched + unresolved
        if total == 0:
            self._sound_resolution_badge.setText(f"场景声音: 本章未使用{generation_suffix}")
            self._sound_resolution_badge.set_tone("muted")
        elif unresolved:
            self._sound_resolution_badge.setText(
                f"场景声音: 已匹配 {matched} · 待补充 {unresolved}{generation_suffix}"
            )
            self._sound_resolution_badge.set_tone("warning")
        else:
            self._sound_resolution_badge.setText(f"场景声音: {matched} 项已匹配{generation_suffix}")
            self._sound_resolution_badge.set_tone("warning" if pending or failed else "success")
        # Tooltip with specific failure details for actionable diagnosis.
        if failed and isinstance(generation, dict):
            failures = generation.get("failures") or []
            if failures:
                tooltip_lines = [
                    f"  {f.get('cue_label', '?')} ({f.get('kind', '?')}): "
                    f"{str(f.get('error', ''))[:80]}"
                    for f in failures[:4]
                ]
                self._sound_resolution_badge.setToolTip(
                    "声音生成失败明细:\n" + "\n".join(tooltip_lines)
                    + "\n\n可点击「补齐声音素材」导入替代资产，或从脚本中移除对应 cue"
                )
            else:
                self._sound_resolution_badge.setToolTip("")
        elif hasattr(self._sound_resolution_badge, "setToolTip"):
            self._sound_resolution_badge.setToolTip("")

    def _on_audio_completed(self, data: dict[str, Any]) -> None:
        """Handle audio completed signal — build timeline and load into player."""
        # Guard against late signals arriving after user cancelled the operation
        if self._current_worker is None:
            return
        if self._synth_anim_timer is not None:
            self._stop_visibility_periodic_timer(self._synth_anim_timer)
        self._current_worker = None
        self._finish_tts_operation()
        self._update_sound_resolution_badge(data.get("metadata"))
        try:
            result = ChapterAudioResult.model_validate(data)
        except Exception:
            _logger.exception("Invalid chapter audio result payload")
            self._status_badge.setText("音频已返回，但结果清单无法读取")
            self._status_badge.set_tone("danger")
            self._cancel_btn.setEnabled(False)
            return

        self._current_audio_result = result
        self._playback_script = result.script
        self._current_script = result.script
        self._source_script_available = bool(
            self._layout and self._layout.tts_dubbing_script_path(result.chapter_number).is_file()
        )
        self._current_results = list(result.segment_results)
        self._active_chapter_number = result.chapter_number
        self._select_chapter(getattr(self, "_chapter_combo", None), result.chapter_number)
        self._select_chapter(getattr(self, "_audio_chapter_combo", None), result.chapter_number)
        self._select_chapter(getattr(self, "_room_chapter_combo", None), result.chapter_number)
        self._segment_status = {
            item.segment_index: item.status.value for item in result.segment_results
        }
        duration_ms = result.total_duration_ms
        duration_str = f"{duration_ms // 60_000}:{duration_ms // 1000 % 60:02d}"
        if result.delivery_ready and not delivery_blocking_reasons(result):
            self._status_badge.setText(f"成片已就绪: {duration_str}")
            self._status_badge.set_tone("success")
        else:
            reasons = result.delivery_blocking_reasons or delivery_blocking_reasons(result)
            reason_text = "；".join(self._delivery_reason_label(reason) for reason in reasons)
            if "unresolved_sound_cues" in reasons:
                reason_text += "（点击「补齐声音素材」导入，或编辑脚本移除对应声音线索后重装配）"
            result_mode = str(result.metadata.get("automation_mode") or "")
            sound_generation = result.metadata.get("sound_generation")
            pending_review = (
                int(sound_generation.get("pending_review") or 0)
                if isinstance(sound_generation, dict)
                else 0
            )
            if result_mode == AudioAutomationMode.ASSISTED.value and pending_review:
                self._status_badge.setText(
                    f"AI 伴随已完成生成 · {pending_review} 项声音待试听批准；批准后点击“重装配”"
                )
            elif result_mode == AudioAutomationMode.AUTONOMOUS.value:
                self._status_badge.setText(
                    f"AI 自主推进已停止在交付门: {reason_text or '质检尚未通过'}"
                )
            else:
                self._status_badge.setText(
                    f"人声已合成，成片待完善: {reason_text or '交付检查尚未通过'}"
                )
            self._status_badge.set_tone("warning")
        phase_badge = getattr(self, "_automation_progress_badge", None)
        if phase_badge is not None:
            phase_badge.setText(
                "6/6 已通过交付门" if result.delivery_ready else "6/6 已完成 · 待处理"
            )
            phase_badge.set_tone("success" if result.delivery_ready else "warning")
        self._cancel_btn.setEnabled(False)
        self._render_script_html()
        self._update_script_source_hint()
        metadata = result.metadata
        self._update_sound_resolution_badge(metadata)
        self._render_mix_manifest()

        # Build timeline from script + synthesis results
        audio_path = result.assembled_audio_path
        timeline = None
        if audio_path:
            try:
                from novel_forge.tts.pipeline.timeline_builder import build_timeline

                timeline = build_timeline(result.script, result.segment_results)
                self._current_timeline = timeline
            except Exception:
                _logger.exception(
                    "Failed to build chapter %d playback timeline", result.chapter_number
                )

        # Auto-load audio into DubbingPlayer with timeline
        if audio_path and Path(audio_path).exists():
            self._ensure_tab_built(3)
            if self._audio_player is not None:
                self._audio_player.load_audio(audio_path, timeline=timeline)
            if self._room_chapter_player is not None:
                self._room_chapter_player.load_audio(audio_path, timeline=timeline)
                self._room_chapter_audio_path = str(Path(audio_path))
            if hasattr(self, "_audio_empty"):
                self._audio_empty.setVisible(False)
        else:
            self._render_sync_script_html()

        # Initialize sync script browser in audio tab
        self._render_sync_script_html()
        if hasattr(self, "_subtitle_text"):
            subtitle_path = Path(result.subtitle_path) if result.subtitle_path else None
            self._subtitle_text.setPlainText(
                subtitle_path.read_text(encoding="utf-8")
                if subtitle_path is not None and subtitle_path.exists()
                else "暂无字幕"
            )
        self._render_script_html()
        self._populate_voice_room_segments()
        self._render_mix_manifest()
        self._refresh_workflow_controls()
        self._maybe_resume_after_reassemble(timeline)

    def _maybe_resume_after_reassemble(self, timeline: Any) -> None:
        """Resume chapter playback from the segment accepted before a fast reassemble.

        ``_on_segment_take_accepted`` records the accepted segment index and
        kicks off a fast reassemble; once the new master + timeline are loaded
        here, seek to that segment and continue playback so the accept -> edit
        -> continue loop is seamless.  No-op when no resume is pending.
        """
        resume_idx = self._resume_after_reassemble_segment
        self._resume_after_reassemble_segment = None
        player = self._room_chapter_player or self._audio_player
        if resume_idx is None or player is None or timeline is None:
            return
        # Only resume if the rebuilt timeline still contains this segment.
        if not any(getattr(e, "segment_index", -1) == resume_idx for e in timeline.entries):
            return
        self._voice_room_playback = True
        self._room_chapter_play_requested = True
        self._set_room_player_scope("chapter", segment_index=resume_idx)
        player.seek_to_segment(resume_idx)
        player.play()
        if hasattr(self, "_room_status_badge"):
            self._room_status_badge.setText(f"已应用第 {resume_idx + 1} 段新试听 · 从该段继续播放")
            self._room_status_badge.set_tone("success")

    # ─── Playback sync signal handlers ─────────────────────────────────────
    # Playback segment/character/word highlight, progress badge, scroll sync,
    # and sync-script rendering are provided by PlaybackSyncMixin.

    def _on_worker_failed(self, worker_id: str, error: dict[str, Any]) -> None:
        """Handle worker failure."""
        # If the user already cancelled, the worker reference was cleared;
        # silently swallow the late-arriving failure to avoid a misleading dialog.
        if self._current_worker is None:
            return
        current_worker_id = str(getattr(self._current_worker, "worker_id", "") or "")
        if worker_id and current_worker_id and worker_id != current_worker_id:
            return
        failed_voice_team_build = isinstance(self._current_worker, BuildVoiceTeamWorker)
        if self._synth_anim_timer is not None:
            self._stop_visibility_periodic_timer(self._synth_anim_timer)
        if self._script_gen_timer is not None:
            self._stop_visibility_periodic_timer(self._script_gen_timer)
        self._stop_stream_stall_timer()
        failed_chapter = self._generating_chapter
        if failed_chapter is not None:
            self._script_streams.pop(failed_chapter, None)
        self._generating_chapter = None
        self._hide_script_generation_progress()
        self._current_worker = None
        self._finish_tts_operation()
        if (
            failed_chapter is not None
            and failed_chapter == self._active_chapter_number
            and self._layout is not None
        ):
            self._load_chapter_artifacts(failed_chapter)
        # Keep the concise outcome and the provider-level failure evidence
        # together.  Previously the dialog showed only “可续跑”, hiding the
        # actual failed segment and making a retry impossible to diagnose.
        summary = str(error.get("summary") or "操作未完成")
        detail = str(error.get("detail") or error.get("message") or "").strip()
        informative_text = detail if detail and detail != summary else ""
        failed_segments = error.get("failed_segments")
        if isinstance(failed_segments, list):
            for item in failed_segments:
                if isinstance(item, dict) and isinstance(item.get("segment_index"), int):
                    self._segment_status[item["segment_index"]] = "failed"
            self._populate_voice_room_segments()
            if hasattr(self, "_room_status_badge"):
                self._room_status_badge.setText(f"{len(failed_segments)} 段失败，可逐段重录")
                self._room_status_badge.set_tone("danger")
            # Auto-navigate to the voice room and highlight the first failed segment.
            first_failed_idx = next(
                (
                    int(item["segment_index"])
                    for item in failed_segments
                    if isinstance(item, dict) and isinstance(item.get("segment_index"), int)
                ),
                -1,
            )
            if first_failed_idx >= 0:
                self._tabs.setCurrentIndex(2)  # Voice Room tab
                if hasattr(self, "_room_segment_list"):
                    self._room_segment_list.setCurrentRow(first_failed_idx)
        self._status_badge.setText(f"失败: {summary[:80]}")
        self._status_badge.set_tone("danger")
        if hasattr(self, "_automation_progress_badge"):
            self._automation_progress_badge.setText("推进已停止 · 查看处理提示")
            self._automation_progress_badge.set_tone("danger")
        self._cancel_btn.setEnabled(False)
        self._build_team_btn.setEnabled(True)
        self._hide_voice_team_progress()
        if failed_voice_team_build:
            recovery_hint = "已保留先前可用的角色音色；修正后可重新组建或单独调整角色。"
            self._show_voice_team_task_feedback(
                state="构建失败",
                tone="danger",
                title="配音团队构建未完成",
                detail=(f"{summary}。{informative_text or recovery_hint}").strip(),
                completed=0,
                total=1,
            )
        self._refresh_selected_voice_action_state()
        self._refresh_workflow_controls()
        # A fast reassemble (triggered by accepting a take) failing must not
        # strand the chapter in the stale state: clear the pending resume and
        # point the user at the manual full reassemble button.
        if self._resume_after_reassemble_segment is not None:
            self._resume_after_reassemble_segment = None
            if hasattr(self, "_room_status_badge"):
                self._room_status_badge.setText("快速重装配失败；请在后处理点击“重装配”")
                self._room_status_badge.set_tone("danger")
        # ── Completeness gate failure: show actionable diagnostics ──────────
        error_code = str(error.get("error_code") or "")
        if error_code == "tts_script_incomplete":
            self._show_completeness_gate_failure_dialog(error)
            return
        if error_code == "tts_delivery_blocked":
            self._show_sound_delivery_blocked_dialog(error)
            return
        show_warning_message(
            self,
            "TTS 错误",
            summary,
            informative_text=informative_text,
        )

    # ─── Helpers ────────────────────────────────────────────────────────────

    def _show_completeness_gate_failure_dialog(self, error: dict[str, Any]) -> None:
        """Show actionable diagnostics when the completeness gate blocks script persistence."""
        report = error.get("completeness_report") or {}
        failures = report.get("failures") or []
        spoken_cov = report.get("spoken_text_coverage", 0.0)
        emotion_diff = report.get("emotion_differentiation", 0.0)
        voice_cov = report.get("voice_assignment_coverage", 0.0)
        draft_path = str(error.get("draft_script_path") or "")

        # Build diagnostic detail text.
        detail_lines = [
            f"• 口语改写覆盖率: {spoken_cov:.0%}",
            f"• 情绪分化度: {emotion_diff:.0%}",
            f"• 音色分配覆盖率: {voice_cov:.0%}",
        ]
        if failures:
            detail_lines.append("")
            detail_lines.append("失败原因:")
            for failure in failures[:4]:
                detail_lines.append(f"  - {failure}")

        msg = QMessageBox(self)
        msg.setObjectName("appDialog")
        msg.setWindowTitle("配音脚本完整度门禁未通过")
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setText(
            "配音脚本未通过完整度门禁，不能落盘为正式成品。\n"
            "已保存为草稿，您可以选择以下操作继续推进。"
        )
        msg.setInformativeText("\n".join(detail_lines))
        msg.setDetailedText(
            "源头诊断提示:\n"
            "- Phase 5 口语改写是否成功执行\n"
            "- Phase 4b LLM 审校是否修正了情绪\n"
            "- Phase 2 说话人裁决是否完成\n"
            "- 情绪修复是否已尝试"
        )

        # Action buttons.
        open_draft_btn = msg.addButton("打开草稿编辑器", QMessageBox.ButtonRole.ActionRole)
        regenerate_btn = msg.addButton("重新生成", QMessageBox.ButtonRole.ActionRole)
        msg.addButton("知道了", QMessageBox.ButtonRole.RejectRole)

        msg.exec()

        clicked = msg.clickedButton()
        if clicked == open_draft_btn and draft_path:
            self._open_gate_failed_draft(draft_path)
        elif clicked == regenerate_btn:
            self._on_generate_script()

    def _open_gate_failed_draft(self, draft_path: str) -> None:
        """Load a gate-failed draft script into the editor for manual review."""
        from pathlib import Path

        from novel_forge.tts.schemas import DubbingScript

        path = Path(draft_path)
        if not path.is_file():
            show_warning_message(self, "草稿不存在", "门禁失败的草稿文件已丢失，请重新生成脚本。")
            return
        try:
            import json

            data = json.loads(path.read_text(encoding="utf-8"))
            script = DubbingScript.model_validate(data)
        except Exception as exc:
            show_warning_message(
                self, "草稿读取失败", f"无法读取草稿文件: {exc}"
            )
            return
        # Load into the page as the current script (draft mode).
        self._current_script = script
        self._source_script_available = True
        self._active_chapter_number = script.chapter_number
        self._render_script_html()
        self._update_script_source_hint()
        self._status_badge.setText(
            f"已载入草稿: {len(script.segments)} 段 · 请手动修正后保存"
        )
        self._status_badge.set_tone("warning")
        # Auto-open the editor at the first problematic segment.
        from novel_forge.tts.script_integrity import unresolved_speaker_indices

        unresolved = unresolved_speaker_indices(script)
        target_idx = unresolved[0] if unresolved else None
        QTimer.singleShot(300, lambda: self._on_edit_script(target_idx))

    def _show_sound_delivery_blocked_dialog(self, error: dict[str, Any]) -> None:
        """Show actionable diagnostics when delivery is blocked by unresolved sound cues.

        Reuses the same QMessageBox three-button pattern as
        ``_show_completeness_gate_failure_dialog``.
        """
        blocking_reasons = error.get("blocking_reasons") or []
        reason_labels = [self._delivery_reason_label(r) for r in blocking_reasons]

        # Try to read persisted sound generation report for failure details.
        detail_lines: list[str] = []
        if self._layout is not None:
            chapter_number = int(error.get("chapter_number") or self._active_chapter_number or 1)
            gen_report_path = self._layout.tts_sound_generation_report_path(chapter_number)
            try:
                import json

                gen_data = json.loads(gen_report_path.read_text(encoding="utf-8"))
                for attempt in gen_data.get("attempts", []):
                    if isinstance(attempt, dict) and attempt.get("status") == "failed":
                        cue = attempt.get("request", {}).get("cue_label", "?")
                        kind = attempt.get("request", {}).get("kind", "?")
                        err_msg = str(attempt.get("error_message") or "")[:100]
                        detail_lines.append(f"  - {cue} ({kind}): {err_msg}")
            except (OSError, ValueError, TypeError):
                pass

        msg = QMessageBox(self)
        msg.setObjectName("appDialog")
        msg.setWindowTitle("声音素材未就绪")
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setText(
            "章节音频未通过交付门，场景声音素材尚未补齐。\n"
            "您可以导入声音资产或从脚本中移除对应声音线索。"
        )
        info_parts = [f"阻断原因: {'; '.join(reason_labels)}"]
        if detail_lines:
            info_parts.append("")
            info_parts.append("生成失败明细:")
            info_parts.extend(detail_lines[:6])
        msg.setInformativeText("\n".join(info_parts))

        # Action buttons — reuse existing entry points.
        open_library_btn = msg.addButton("补齐声音素材", QMessageBox.ButtonRole.ActionRole)
        open_script_btn = msg.addButton("打开脚本", QMessageBox.ButtonRole.ActionRole)
        msg.addButton("知道了", QMessageBox.ButtonRole.RejectRole)

        msg.exec()

        clicked = msg.clickedButton()
        if clicked == open_library_btn:
            self._show_sound_library()
        elif clicked == open_script_btn:
            self._tabs.setCurrentIndex(0)  # Script tab

    def _show_voice_team_progress(
        self,
        *,
        completed: int = 0,
        total: int = 0,
        indeterminate: bool = False,
        phase_index: int = 0,
        phase_total: int = 5,
        phase_label: str = "",
        detail: str = "",
    ) -> None:
        """Show honest automatic-cast progress in the status bar and task card."""
        task_title = (
            f"阶段 {phase_index}/{phase_total} · {phase_label}"
            if phase_index
            else "正在准备配音团队"
        )
        task_detail = detail or "后台任务正在处理，请勿重复提交。"
        if indeterminate or total <= 0:
            self._voice_team_progress.setRange(0, 0)
            label = task_title if phase_index else "正在准备…"
            self._voice_team_progress.setFormat(label)
            self._show_voice_team_task_feedback(
                state="进行中",
                tone="warning",
                title=task_title,
                detail=task_detail,
                indeterminate=True,
            )
        elif phase_index > 0:
            bounded_phase = max(1, min(phase_index, phase_total))
            bounded_completed = max(0, min(completed, total))
            phase_fraction = bounded_completed / total
            overall = round(((bounded_phase - 1) + phase_fraction) / phase_total * 100)
            self._voice_team_progress.setRange(0, 100)
            self._voice_team_progress.setValue(overall)
            suffix = detail or f"{bounded_completed}/{total}"
            self._voice_team_progress.setFormat(
                f"{bounded_phase}/{phase_total} {phase_label} · {suffix}"
            )
            self._show_voice_team_task_feedback(
                state=f"进行中 · 阶段 {bounded_phase}/{phase_total}",
                tone="warning",
                title=task_title,
                detail=task_detail,
                completed=overall,
                total=100,
            )
        else:
            bounded_completed = max(0, min(completed, total))
            self._voice_team_progress.setRange(0, total)
            self._voice_team_progress.setValue(bounded_completed)
            self._voice_team_progress.setFormat(f"{bounded_completed} / {total}")
            self._show_voice_team_task_feedback(
                state="进行中",
                tone="warning",
                title=task_title,
                detail=task_detail,
                completed=bounded_completed,
                total=total,
            )
        self._voice_team_progress.setVisible(True)

    def _hide_voice_team_progress(self) -> None:
        """Hide the compact status-bar progress without erasing task feedback."""
        self._voice_team_progress.setVisible(False)
        self._voice_team_progress.setRange(0, 1)
        self._voice_team_progress.setValue(0)
        self._voice_team_progress.setFormat("")

    def _show_voice_team_task_feedback(
        self,
        *,
        state: str,
        tone: str,
        title: str,
        detail: str,
        completed: int = 0,
        total: int = 0,
        indeterminate: bool = False,
    ) -> None:
        """Render persistent, actionable feedback for an automatic cast job."""
        self._voice_team_task_state.setText(state)
        self._voice_team_task_state.set_tone(tone)
        self._voice_team_task_title.setText(title)
        self._voice_team_task_detail.setText(detail)
        if indeterminate or total <= 0:
            self._voice_team_task_progress.setRange(0, 0)
            self._voice_team_task_progress.setFormat("正在启动…")
        else:
            bounded_total = max(1, total)
            bounded_completed = max(0, min(completed, bounded_total))
            self._voice_team_task_progress.setRange(0, bounded_total)
            self._voice_team_task_progress.setValue(bounded_completed)
            self._voice_team_task_progress.setFormat(
                "已完成"
                if bounded_completed == bounded_total
                else f"{bounded_completed}%"
                if bounded_total == 100
                else f"{bounded_completed}/{bounded_total}"
            )
        self._voice_team_task_card.setVisible(True)

    def _hide_voice_team_task_feedback(self) -> None:
        """Clear stale cast feedback when loading another project."""
        self._voice_team_task_card.setVisible(False)
        self._voice_team_task_state.setText("待启动")
        self._voice_team_task_state.set_tone("muted")
        self._voice_team_task_title.setText("配音团队构建")
        self._voice_team_task_detail.setText(
            "提交后会显示当前阶段、角色处理数、试听准备情况与最终结果。"
        )
        self._voice_team_task_progress.setRange(0, 100)
        self._voice_team_task_progress.setValue(0)
        self._voice_team_task_progress.setFormat("")

    def _get_selected_character_id(self) -> str:
        """Get the currently selected character ID from the list."""
        item = self._character_list.currentItem()
        if item:
            return str(item.data(Qt.ItemDataRole.UserRole))
        return ""

    @staticmethod
    def _ms_to_srt_time(ms: int) -> str:
        """Convert milliseconds to SRT time format HH:MM:SS,mmm."""
        hours = ms // 3600000
        ms %= 3600000
        minutes = ms // 60000
        ms %= 60000
        seconds = ms // 1000
        millis = ms % 1000
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"

    def _on_cleanup_tts_files(
        self,
        default_categories: set[str] | bool | None = None,
    ) -> None:
        """Show categorized-cleanup dialog and prune selected TTS artifacts."""
        if not self._layout:
            return
        if self._clear_operation_is_running():
            return
        defaults = default_categories if isinstance(default_categories, set) else set()
        dialog = _TTSCleanupDialog(
            self._layout,
            self,
            default_categories=defaults,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        categories = dialog.selected_categories()
        if not categories:
            self._status_badge.setText("未选择任何清理类别")
            self._status_badge.set_tone("warning")
            return
        # Skip the confirmation dialog the operator already reviewed per-category
        # descriptions in the chooser; downstream is reversible only with
        # partial TTS regeneration.
        try:
            report = execute_cleanup_tts_files(self._layout, categories=categories)
        except OSError as exc:
            logging.getLogger("novel_forge.voice_studio").exception("TTS cleanup failed")
            self._status_badge.setText("清理失败")
            self._status_badge.set_tone("danger")
            show_warning_message(
                self, "清理 TTS 文件失败", "部分文件无法删除", informative_text=str(exc)
            )
            return
        if report.is_empty:
            self._status_badge.setText("没有可清理的过期 TTS 文件")
            self._status_badge.set_tone("muted")
            return
        self._room_loaded_audio_path = ""
        if self._active_chapter_number:
            self._load_chapter_artifacts(self._active_chapter_number)
        if hasattr(self, "_sound_library_panel"):
            self._sound_library_panel.refresh()
        reclaim_mb = report.reclaimed_bytes / (1024 * 1024)
        self._status_badge.setText(
            f"已清理 {report.removed_count} 个 TTS 文件（释放 {reclaim_mb:.2f} MB）"
        )
        self._status_badge.set_tone("success")


class _TTSCleanupDialog(QDialog):
    """Modal checkbox dialog letting the user pick which TTS categories to clean."""

    def __init__(
        self,
        layout: ProjectLayout,
        parent: QWidget | None = None,
        *,
        default_categories: set[str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("选择要清理的旧资产")
        self.setObjectName("appDialog")
        self.resize(*smart_dialog_size(self, 560, 520, max_screen_fraction=0.65))
        self._layout = layout
        self._checkboxes: dict[str, QCheckBox] = {}
        self._preview = preview_cleanup_tts_files(layout)
        defaults = default_categories or set()

        outer = QVBoxLayout(self)
        outer.setSpacing(8)
        intro = QLabel(
            "仅清理已失去引用、可重建的资产。请勾选清理范围后再确认；"
            "已接受试听、正式分段、成品、声音团队和仍在引用的素材不会被删除。"
        )
        intro.setWordWrap(True)
        outer.addWidget(intro)

        for cat in _TTS_CLEANUP_CATEGORIES:
            label, desc = _TTS_CLEANUP_LABELS[cat]
            result = self._preview.categories.get(cat)
            count = (
                len(result.removed_files) + len(result.removed_dirs) if result is not None else 0
            )
            reclaim_mb = (result.reclaimed_bytes if result is not None else 0) / (1024 * 1024)
            box = QCheckBox(f"{label}  ·  {count} 项  ·  {reclaim_mb:.2f} MB")
            box.setToolTip(desc)
            box.setEnabled(count > 0)
            box.setChecked(count > 0 and cat in defaults)
            self._checkboxes[cat] = box
            row = QVBoxLayout()
            row.setSpacing(2)
            row.addWidget(box)
            hint = QLabel(desc)
            hint.setWordWrap(True)
            hint.setObjectName("voiceStudioCleanCategoryHint")
            hint.setContentsMargins(20, 0, 0, 0)
            row.addWidget(hint)
            outer.addLayout(row)

        self._selection_summary = QLabel()
        self._selection_summary.setObjectName("voiceStudioCleanPreview")
        outer.addWidget(self._selection_summary)

        outer.addStretch()
        btn_row = QHBoxLayout()
        select_all_btn = ActionButton("全选可清理项", variant="secondary")
        select_all_btn.clicked.connect(self._select_all_available)
        btn_row.addWidget(select_all_btn)
        cancel_btn = ActionButton("取消", variant="quiet")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        btn_row.addStretch()
        self._confirm_btn = ActionButton("清理已选资产", variant="danger")
        self._confirm_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._confirm_btn)
        outer.addLayout(btn_row)
        for box in self._checkboxes.values():
            box.toggled.connect(self._update_selection_summary)
        self._update_selection_summary()

    def selected_categories(self) -> set[str]:
        return {cat for cat, box in self._checkboxes.items() if box.isChecked()}

    def _select_all_available(self) -> None:
        for box in self._checkboxes.values():
            if box.isEnabled():
                box.setChecked(True)

    def _update_selection_summary(self) -> None:
        selected = self.selected_categories()
        count = 0
        reclaimed = 0
        for cat in selected:
            result = self._preview.categories.get(cat)
            if result is None:
                continue
            count += len(result.removed_files) + len(result.removed_dirs)
            reclaimed += result.reclaimed_bytes
        self._selection_summary.setText(
            f"已选 {len(selected)} 类 · {count} 项旧资产 · 预计释放 {reclaimed / (1024 * 1024):.2f} MB"
            if selected
            else "请至少勾选一类可清理资产"
        )
        self._confirm_btn.setEnabled(bool(selected))
        self._confirm_btn.setText(f"清理已选（{count} 项）" if selected else "清理已选资产")
