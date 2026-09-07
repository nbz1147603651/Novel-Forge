"""Page coordination and lifecycle for ChapterStudioPage."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Set

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from novel_forge.core.config import get_settings, reset_settings
from novel_forge.desktop.components.skeleton import create_skeleton_card_with_lines
from novel_forge.desktop.components.task_focus import TaskFocusPanel
from novel_forge.desktop.constants import JOBS_PANEL_RENDER_COALESCE_MS
from novel_forge.desktop.jobs import DesktopJobRecord, DesktopJobState
from novel_forge.desktop.pages.standalone.memory_panel import UnifiedMemoryPanelPresenter
from novel_forge.desktop.pages.workflow.artifacts import StepArtifactDialog
from novel_forge.desktop.progress import display_step_name_for_job
from novel_forge.desktop.task_observation import TaskFocusScope, TaskObservationStore
from novel_forge.desktop.ui_density import configure_tab_widget_density
from novel_forge.desktop.widgets import (
    ActionButton,
    Badge,
    CheckpointDialog,
    CollapsibleSection,
    SectionHeading,
    Surface,
    build_checkpoint_summary_text,
)
from novel_forge.desktop.workspace import DesktopWorkspaceSnapshot
from novel_forge.workspace.contracts import (
    ChapterWorkspaceSnapshot,
    DecisionCheckpoint,
    DecisionOption,
)

from .action_panel import ChapterStudioActionPanelPresenter
from .artifacts import ChapterStudioArtifactPresenter
from .contract import ChapterStudioMixinBase
from .dialogs import ProjectSwitchConfirmDialog
from .widgets import (
    ChapterRailPanel,
    CompassPanel,
    ModeSelector,
    WritingModeSelector,
)

_CHAPTER_RAIL_MIN_WIDTH = 220
_CHAPTER_RAIL_MAX_WIDTH = 320
_MEMORY_PANEL_MIN_WIDTH = 340
_MEMORY_PANEL_MAX_WIDTH = 430
_TASK_FLOW_MAX_HEIGHT = 560
_TASK_FLOW_MIN_HEIGHT = 320
_WORKBENCH_ARTIFACT_TABS_MIN_HEIGHT = _TASK_FLOW_MIN_HEIGHT + 48
_WORKBENCH_ARTIFACT_PANEL_MIN_HEIGHT = _TASK_FLOW_MIN_HEIGHT + 112


class ChapterStudioCoordMixin(ChapterStudioMixinBase):
    """Mixin providing UI construction, lifecycle, and signal coordination."""

    def _coord_current_mode(self) -> str:
        """Return the current mode with a safe default for lightweight mixin tests."""
        try:
            mode = self._mode
        except AttributeError:
            mode = None
        return str(mode or getattr(self, "MODE_MANUAL", "manual"))

    def _coord_auto_started(self) -> bool:
        """Return whether auto execution is active without requiring page state proxies."""
        try:
            return bool(self._auto_started)
        except AttributeError:
            return False

    def _coord_book_auto_running(self) -> bool:
        """True when the host is running whole-book auto mode."""
        return (
            self._coord_current_mode() == str(getattr(self, "MODE_BOOK_AUTO", "book_auto"))
            and self._coord_auto_started()
        )

    def _build_ui(self) -> None:
        self._lock_horizontal_page_motion()
        if self._defer_sections:
            # Insert a lightweight placeholder so the page paints immediately.
            from PySide6.QtWidgets import QLabel

            placeholder = QLabel("正在铺开章节工作台…")
            placeholder.setObjectName("chapterStudioDeferredHint")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet("color: rgba(255,255,255,0.5); font-size: 14px; padding: 40px;")
            self._deferred_placeholder = placeholder
            self.body_layout.addWidget(placeholder)
            self._deferred_build_timer.start(0)
        else:
            for build_step in self._ui_build_steps():
                self.body_layout.addWidget(build_step())
            self.body_layout.addStretch()

    def _ui_build_steps(self) -> tuple[Callable[[], QWidget], ...]:
        """Return the ordered build steps (shared by eager + deferred paths).

        Each step is a zero-arg callable that **returns** a QWidget to be
        inserted into ``body_layout``.
        """
        return (
            self._build_selector,
            self._build_compose_surface,
            self._build_jobs_section,
            self._build_artifacts_section,
        )

    def _lock_horizontal_page_motion(self) -> None:
        """Keep the chapter studio fixed horizontally even with trackpad gestures."""
        hbar = self.horizontalScrollBar()
        hbar.setEnabled(False)
        hbar.setValue(0)
        hbar.valueChanged.connect(lambda value: hbar.setValue(0) if value else None)

    def _build_selector(self) -> QWidget:
        panel = Surface("hero")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(22, 18, 22, 14)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(12)
        title_block = QVBoxLayout()
        title_block.setSpacing(2)
        title = QLabel("章节工作台")
        title.setObjectName("cardTitle")
        title_block.addWidget(title)
        subtitle = QLabel("选择项目，在左侧章节轨道中切换章节，工作台自动带出上下文与方案。")
        subtitle.setObjectName("cardMeta")
        subtitle.setWordWrap(False)
        title_block.addWidget(subtitle)
        header.addLayout(title_block, 1)

        self._view_btn = ActionButton("阅卷", variant="secondary")
        self._view_btn.clicked.connect(self._emit_view_project)
        header.addWidget(self._view_btn)
        self._goto_workflow_btn = ActionButton("回机杼 →", variant="secondary")
        self._goto_workflow_btn.clicked.connect(lambda: self.navigate_requested.emit("workflow"))
        header.addWidget(self._goto_workflow_btn)
        layout.addLayout(header)

        row = QHBoxLayout()
        row.setSpacing(10)

        self._project_combo = QComboBox()
        self._project_combo.setEditable(True)
        self._project_combo.setMinimumWidth(260)
        self._project_combo.currentTextChanged.connect(self._on_project_selection_changed)
        self._previous_project: str | None = None
        row.addWidget(self._labeled_field("长篇项目", self._project_combo), 3)

        self._project_status_dot = QLabel()
        self._project_status_dot.setObjectName("statusDotGreen")
        self._project_status_dot.setVisible(False)
        self._project_status_dot.setFixedSize(10, 10)
        row.addWidget(self._project_status_dot)

        # 后台连跑提示标签：显示当前在后台执行 auto/book_auto 的其它项目，
        # 帮助用户在多项目场景下随时知道哪些项目仍在推进。
        self._background_autorun_label = QLabel("")
        self._background_autorun_label.setObjectName("cardMeta")
        self._background_autorun_label.setVisible(False)
        row.addWidget(self._background_autorun_label, 1)

        # Status rotation state (for multi-project parallel execution display)
        self._rotation_index: int = 0
        self._rotation_paused: bool = False
        self._rotation_active_projects: list[str] = []
        self._status_rotation_timer = QTimer(self)
        self._status_rotation_timer.setInterval(8000)
        self._status_rotation_timer.timeout.connect(self._on_status_rotation_tick)

        self._chapter_spin = QSpinBox()
        self._chapter_spin.setRange(1, 10000)
        self._chapter_spin.setValue(1)
        self._chapter_spin.setVisible(False)
        self._chapter_spin.valueChanged.connect(self._on_user_chapter_changed)

        self._writing_mode_selector = WritingModeSelector()
        self._writing_mode_selector.mode_changed.connect(self._on_writing_mode_changed)
        row.addWidget(self._writing_mode_selector, 2)

        self._mode_selector = ModeSelector()
        self._mode_selector.mode_changed.connect(self._on_mode_changed)
        row.addWidget(self._mode_selector, 3)

        # 跟随连跑 toggle
        follow_wrapper = QWidget()
        follow_layout = QHBoxLayout(follow_wrapper)
        follow_layout.setContentsMargins(0, 0, 0, 0)
        follow_layout.setSpacing(6)
        follow_label = QLabel("跟随连跑")
        follow_label.setObjectName("cardMeta")
        self._follow_autorun_cb = QCheckBox()
        self._follow_autorun_cb.setToolTip(
            "开启后，章节连跑完成当前章后自动跳转下一章；关闭则停在当前章。"
        )
        self._follow_autorun_cb.setChecked(False)
        self._follow_autorun_cb.stateChanged.connect(self._on_follow_autorun_changed)
        follow_layout.addWidget(follow_label)
        follow_layout.addWidget(self._follow_autorun_cb)
        row.addWidget(follow_wrapper)

        layout.addLayout(row)

        info_bar = QHBoxLayout()
        info_bar.setSpacing(10)

        self._book_synopsis = QLabel("")
        self._book_synopsis.setObjectName("cardHint")
        self._book_synopsis.setWordWrap(False)
        self._book_synopsis.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        info_bar.addWidget(self._book_synopsis, 1)

        self._consistency_tool_btn = ActionButton("📋 全书审计", variant="secondary")
        self._consistency_tool_btn.setToolTip(
            "检查全书一致性：命名、时间线、世界观、角色状态等，可选择审计范围"
        )
        self._consistency_tool_btn.clicked.connect(self._submit_book_consistency)
        self._export_tool_btn = ActionButton("📤 导出", variant="secondary")
        self._export_tool_btn.setToolTip(
            "导出为 Markdown / 纯文本 / EPUB，可选择章节范围和输出路径"
        )
        self._export_tool_btn.clicked.connect(self._submit_export)
        self._diff_tool_btn = ActionButton("📊 版本对比", variant="secondary")
        self._diff_tool_btn.setToolTip("对比当前章节的不同草稿版本，行级高亮显示修改差异")
        self._diff_tool_btn.clicked.connect(self._show_version_diff)
        self._clean_stale_btn = ActionButton("🗑 清理章节", variant="secondary")
        self._clean_stale_btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._clean_stale_btn.setToolTip(
            "删除当前或失效章节的已生成文件（正文、草稿、报告等），从所选章节重新开始。\n"
            "Canon 水位将回滚至清理起始章的前一章，后续章节文件一并清除。\n"
            "遇到上下文污染或需要强制重写时可手动触发。"
        )
        self._clean_stale_btn.clicked.connect(self._on_clean_stale_chapters)
        self._clean_stale_btn.setVisible(False)
        info_bar.addStretch(1)
        info_bar.addWidget(self._clean_stale_btn)
        info_bar.addWidget(self._consistency_tool_btn)
        info_bar.addWidget(self._export_tool_btn)
        info_bar.addWidget(self._diff_tool_btn)

        layout.addLayout(info_bar)

        return panel

    def _build_compose_surface(self) -> QWidget:
        shell = QWidget()
        layout = QHBoxLayout(shell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        self._rail = ChapterRailPanel()
        self._rail.chapter_selected.connect(lambda ch: self._chapter_spin.setValue(ch))
        self._rail.setMinimumWidth(_CHAPTER_RAIL_MIN_WIDTH)
        self._rail.setMaximumWidth(_CHAPTER_RAIL_MAX_WIDTH)
        self._rail.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        layout.addWidget(self._rail, 4)

        center = self._build_center()
        center.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._center_wrapper = center  # Save reference for CheckpointDialog parent
        layout.addWidget(center, 9)

        self._memory_panel = self._build_memory_panel()
        self._memory_panel.setMinimumWidth(_MEMORY_PANEL_MIN_WIDTH)
        self._memory_panel.setMaximumWidth(_MEMORY_PANEL_MAX_WIDTH)
        self._memory_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        layout.addWidget(self._memory_panel, 4)

        return shell

    def _build_center(self) -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        self._context_skeleton = create_skeleton_card_with_lines(line_count=5)
        self._context_skeleton.start_pulse()
        self._context_skeleton.setVisible(True)

        self._action_panel = Surface("panel")
        action_layout = QVBoxLayout(self._action_panel)
        action_layout.setContentsMargins(22, 16, 22, 16)
        action_layout.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        self._action_title = QLabel("准备章节方案")
        self._action_title.setObjectName("cardTitle")
        title_row.addWidget(self._action_title, 1)
        self._action_badge = Badge("待命", tone="default")
        title_row.addWidget(self._action_badge)
        action_layout.addLayout(title_row)

        self._job_hint = QLabel("章节工作台当前无运行中任务。")
        self._job_hint.setObjectName("cardHint")
        self._job_hint.setWordWrap(True)
        action_layout.addWidget(self._job_hint)

        self._action_summary = QLabel("工作台载入后，这里会显示当前章节的方案摘要或待决策内容。")
        self._action_summary.setObjectName("cardBody")
        self._action_summary.setWordWrap(True)
        action_layout.addWidget(self._action_summary)

        self._ai_suggestion_frame = Surface("inset")
        _sug_row = QHBoxLayout(self._ai_suggestion_frame)
        _sug_row.setContentsMargins(12, 8, 12, 8)
        _sug_row.setSpacing(8)
        _sug_icon = QLabel("💡")
        _sug_icon.setObjectName("cardMeta")
        _sug_row.addWidget(_sug_icon)
        self._ai_suggestion_label = QLabel("")
        self._ai_suggestion_label.setObjectName("cardHint")
        self._ai_suggestion_label.setWordWrap(True)
        _sug_row.addWidget(self._ai_suggestion_label, 1)
        self._ai_suggestion_frame.setVisible(False)
        action_layout.addWidget(self._ai_suggestion_frame)

        self._notes_section = CollapsibleSection("编写备注与补充约束", expanded=False)
        self._notes_section._toggle.clicked.connect(self._on_notes_toggled)
        self._notes = QTextEdit()
        self._notes.setPlaceholderText("在这里写本章补充约束、方案备注，或对 AI 选项的人工说明。")
        self._notes.setMinimumHeight(80)
        self._notes.setMaximumHeight(120)
        self._notes_section.body_layout.addWidget(self._notes)
        action_layout.addWidget(self._notes_section)

        self._rewrite_strategy_row = QWidget()
        rewrite_strategy_layout = QHBoxLayout(self._rewrite_strategy_row)
        rewrite_strategy_layout.setContentsMargins(0, 0, 0, 0)
        rewrite_strategy_layout.setSpacing(8)
        rewrite_strategy_label = QLabel("重写模式")
        rewrite_strategy_label.setObjectName("cardMeta")
        rewrite_strategy_layout.addWidget(rewrite_strategy_label)
        self._rewrite_strategy_combo = QComboBox()
        self._rewrite_strategy_combo.addItem("自动", "auto")
        self._rewrite_strategy_combo.addItem("兼容后文", "compatible")
        self._rewrite_strategy_combo.addItem("普通重写", "sequential")
        self._rewrite_strategy_combo.addItem("重构后续", "reconstruct")
        self._rewrite_strategy_combo.addItem("外科修补", "surgical")
        self._rewrite_strategy_combo.setToolTip(
            "仅在重写早期章节且后面已有正文时使用。\n"
            "自动：有后文则生成作者层兼容约束；无后文则普通重写。\n"
            "兼容后文：照顾已写后文，但不把未来事实当成本章因果。\n"
            "重构后续：不照顾旧后文，允许后续章节失效。\n"
            "外科修补：尽量保留原章结构，只围绕问题做保守重写。"
        )
        self._rewrite_strategy_combo.setMinimumWidth(150)
        rewrite_strategy_layout.addWidget(self._rewrite_strategy_combo)
        rewrite_strategy_hint = QLabel("后文只作为作者层约束")
        rewrite_strategy_hint.setObjectName("cardHint")
        rewrite_strategy_layout.addWidget(rewrite_strategy_hint, 1)
        self._rewrite_strategy_row.setVisible(False)
        action_layout.addWidget(self._rewrite_strategy_row)

        self._action_buttons = QVBoxLayout()
        self._action_buttons.setSpacing(6)
        action_layout.addLayout(self._action_buttons)
        self._action_presenter = ChapterStudioActionPanelPresenter(
            action_title=self._action_title,
            action_summary=self._action_summary,
            action_badge=self._action_badge,
            job_hint=self._job_hint,
            ai_suggestion_frame=self._ai_suggestion_frame,
            ai_suggestion_label=self._ai_suggestion_label,
            notes_section=self._notes_section,
            notes=self._notes,
            rewrite_strategy_row=self._rewrite_strategy_row,
            action_buttons=self._action_buttons,
            on_submit_prepare=self._submit_prepare,
            on_cancel_running_job=self._cancel_running_job,
            on_stop_auto_pilot=self._stop_auto_pilot,
            on_start_auto_pilot=self._start_auto_pilot,
            on_switch_to_suggest=lambda: self._set_mode(self.MODE_SUGGEST),
            on_handle_checkpoint_option=self._handle_checkpoint_option,
            on_resume_from_progress=self._on_resume_from_progress,
            on_resume_auto_pilot=self._resume_auto_pilot,
            on_submit_regen_with_continuity_check=self._submit_regen_with_continuity_check,
            on_submit_polish_with_continuity_check=self._submit_polish_with_continuity_check,
            on_go_to_next_chapter=lambda: (
                self._chapter_spin.setValue(self._studio.chapter_number + 1)
                if self._studio
                else None
            ),
            on_submit_book_consistency=self._submit_book_consistency,
            on_submit_export=self._submit_export,
            on_schedule_retry=self._schedule_retry_after,
            on_cancel_retry=self._cancel_scheduled_retry,
            on_show_checkpoint_dialog=self._show_checkpoint_dialog,
            is_checkpoint_dialog_dismissed=self._is_checkpoint_dialog_dismissed,
        )
        layout.addWidget(self._action_panel)

        self._compass = CompassPanel()
        layout.addWidget(self._compass, 1)

        layout.addWidget(self._context_skeleton)

        self._action_panel.setVisible(False)
        self._compass.setVisible(False)

        # Install event filter on _action_panel for hover pause
        self._action_panel.installEventFilter(self)

        # Checkpoint dialog state
        self._checkpoint_dialog: CheckpointDialog | None = None

        return wrapper

    def _build_artifacts_section(self) -> QWidget:
        artifacts = Surface("panel")
        artifacts.setMinimumHeight(_WORKBENCH_ARTIFACT_PANEL_MIN_HEIGHT)
        artifacts.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        artifact_layout = QVBoxLayout(artifacts)
        artifact_layout.setContentsMargins(28, 22, 28, 22)
        artifact_layout.setSpacing(10)
        artifact_layout.addWidget(
            SectionHeading(
                "工作台产物",
                "正文、章节计划、创作报告与 Canon 相关文件都可以在这里直接查看。",
            )
        )
        self._artifact_tabs = QTabWidget()
        self._artifact_tabs.setObjectName("chapterStudioArtifactTabs")
        self._artifact_tabs.setDocumentMode(True)
        self._artifact_tabs.setMinimumHeight(_WORKBENCH_ARTIFACT_TABS_MIN_HEIGHT)
        self._artifact_tabs.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        configure_tab_widget_density(self._artifact_tabs, "content")
        self._artifact_presenter = ChapterStudioArtifactPresenter(self._artifact_tabs)
        artifact_layout.addWidget(self._artifact_tabs, 1)
        return artifacts

    def _build_memory_panel(self) -> QWidget:
        from novel_forge.desktop.components.memory_components import MemoryBadge, UnifiedMemoryPanel
        from novel_forge.desktop.state.store import get_ui_store

        panel = Surface("panel")
        panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        title_bar = QWidget()
        tb_layout = QHBoxLayout(title_bar)
        tb_layout.setContentsMargins(20, 14, 20, 12)
        tb_layout.setSpacing(10)
        heading = QLabel("记忆上下文")
        heading.setObjectName("cardTitle")
        tb_layout.addWidget(heading)

        self._memory_badge = MemoryBadge("记忆", memory_status="inactive")
        tb_layout.addWidget(self._memory_badge)

        tb_layout.addStretch()

        outer.addWidget(title_bar)

        sep_head = QFrame()
        sep_head.setObjectName("railSep")
        sep_head.setFixedHeight(1)
        outer.addWidget(sep_head)

        self._memory_panel_widget = UnifiedMemoryPanel()
        outer.addWidget(self._memory_panel_widget, 1)

        self._memory_presenter = UnifiedMemoryPanelPresenter(
            unified_panel=self._memory_panel_widget,
            memory_badge=self._memory_badge,
            store=get_ui_store(),
        )
        self._memory_panel_widget.set_motif_lookback_chapters(
            self._memory_presenter.motif_related_lookback_chapters()
        )

        self._memory_panel_widget.get_repair_button().clicked.connect(self._submit_repair_issues)
        self._memory_panel_widget.get_reevaluate_button().clicked.connect(
            self._submit_reevaluate_chapter
        )
        self._memory_panel_widget.chapter_warnings_clear_requested.connect(
            self._on_clear_chapter_warnings
        )
        self._memory_panel_widget.get_relations_action_button().clicked.connect(
            self._goto_global_relationships
        )
        self._memory_panel_widget.get_relations_extract_button().clicked.connect(
            self._submit_reextract_relationships
        )
        self._memory_panel_widget.get_motif_repair_button().clicked.connect(
            self._submit_repair_motif_history
        )
        self._memory_panel_widget.get_motif_lookback_spinbox().valueChanged.connect(
            self._on_motif_lookback_changed
        )
        self._sync_motif_lookback_from_settings()

        return panel

    def _hsep(self) -> QWidget:
        sep = QFrame()
        sep.setObjectName("railSep")
        sep.setFixedHeight(1)
        return sep

    def _build_jobs_section(self) -> QWidget:
        panel = Surface("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(28, 22, 28, 22)
        layout.setSpacing(14)
        heading_row = QHBoxLayout()
        heading_row.setSpacing(10)
        heading_row.addWidget(
            SectionHeading(
                "任务流",
                "后台任务实时进度、执行步骤与失败原因。",
            ),
            1,
        )
        self._error_logs_btn = ActionButton("错误日志 0", variant="secondary")
        self._error_logs_btn.setProperty("errorState", "clean")
        self._error_logs_btn.setToolTip("查看任务流中的格式错误、重试次数与失败摘要。")
        self._error_logs_btn.clicked.connect(self._on_view_error_logs)
        heading_row.addWidget(self._error_logs_btn, 0, Qt.AlignmentFlag.AlignTop)
        self._clear_task_flow_btn = ActionButton(" 清理", variant="secondary")
        self._clear_task_flow_btn.setToolTip(
            "清空当前章节任务流里的历史步骤（已完成/失败/待决策）。\n运行中任务不会被中断。"
        )
        self._clear_task_flow_btn.clicked.connect(self._on_clear_task_flow)
        self._clear_task_flow_btn.setEnabled(False)
        heading_row.addWidget(self._clear_task_flow_btn, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(heading_row)
        self._jobs_scroll = QScrollArea()
        self._jobs_scroll.setWidgetResizable(True)
        self._jobs_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._jobs_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._jobs_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._jobs_scroll.setMinimumHeight(_TASK_FLOW_MIN_HEIGHT)
        self._jobs_scroll.setMaximumHeight(_TASK_FLOW_MAX_HEIGHT)
        self._jobs_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._jobs_scroll.setObjectName("chapterTaskFlowScroll")

        jobs_container = QWidget()
        jobs_container.setObjectName("chapterTaskFlowContent")
        self._jobs_layout = QVBoxLayout(jobs_container)
        self._jobs_layout.setSpacing(12)
        self._jobs_layout.setContentsMargins(12, 12, 12, 12)
        self._jobs_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._jobs_scroll.setWidget(jobs_container)
        layout.addWidget(self._jobs_scroll)
        self._task_focus_panel = TaskFocusPanel(
            TaskFocusScope.CHAPTER, title="章台关注", compact=True
        )
        self._task_focus_panel.decision_selected.connect(self.task_focus_decision_selected)
        self._task_focus_panel.expand_requested.connect(self.task_focus_expand_requested)
        self._task_focus_panel.artifact_requested.connect(self._on_task_focus_artifact_requested)
        layout.addWidget(self._task_focus_panel)
        return panel

    def _on_task_focus_artifact_requested(
        self,
        project_id: str,
        job_kind: str,
        step_key: str,
        step_label: str,
        chapter_number: int,
    ) -> None:
        project_id = str(project_id or self.current_project_id()).strip()
        workspace = getattr(self, "_workspace", None)
        project_dir = (
            workspace.storage_root / project_id if workspace is not None and project_id else None
        )
        nav_target = StepArtifactDialog.show_for_step(
            kind=job_kind,
            step_key=step_key,
            step_label=step_label or step_key,
            project_dir=project_dir,
            chapter_number=chapter_number or self.current_chapter_number(),
            parent=self.window(),
        )
        if nav_target:
            win = self.window()
            if hasattr(win, "switch_page"):
                win.switch_page(nav_target)

    def _labeled_field(self, label: str, widget: QWidget) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        if label:
            title = QLabel(label)
            title.setObjectName("cardMeta")
            layout.addWidget(title)
        layout.addWidget(widget)
        return container

    def bind_workspace(self, snapshot: DesktopWorkspaceSnapshot) -> None:
        if not self.is_ui_ready():
            self._pending_workspace_snapshot = snapshot
            return
        self._workspace = snapshot
        if not hasattr(self, "_active_projects"):
            self._active_projects: Set[str] = set()
        self._update_active_projects()
        current = self._project_combo.currentText().strip()
        long_projects = [item for item in snapshot.projects if item.mode == "long"]
        long_project_ids = {item.project_id for item in long_projects}
        selected_project = current if current in long_project_ids else ""
        selection_changed = bool(current and current != selected_project)
        self._project_combo.blockSignals(True)
        self._project_combo.clear()
        for project in long_projects:
            label = project.project_id
            self._project_combo.addItem(label)
        if selected_project:
            self._project_combo.setCurrentText(selected_project)
        elif long_projects:
            first = long_projects[0]
            self._project_combo.setCurrentText(first.project_id)
            self._chapter_spin.blockSignals(True)
            self._chapter_spin.setValue(first.next_chapter or 1)
            self._chapter_spin.blockSignals(False)
            selected_project = first.project_id
            selection_changed = bool(current and current != selected_project)
        else:
            self._project_combo.setEditText("")
            self._chapter_spin.blockSignals(True)
            self._chapter_spin.setValue(1)
            self._chapter_spin.blockSignals(False)
            selection_changed = bool(current)
        self._project_combo.blockSignals(False)
        self._previous_project = selected_project or None
        self._activate_project_context(selected_project)
        self._update_status_dot()

        cleared_stale_context = False
        if selection_changed:
            self.bind_studio(None)
            cleared_stale_context = True

        if selected_project:
            self._request_context()
        elif not cleared_stale_context:
            self.bind_studio(None)

    def bind_workspace_sections(
        self, snapshot: DesktopWorkspaceSnapshot, sections: frozenset[str]
    ) -> None:
        """Incremental workspace binding - only refresh changed *sections*.

        ``"projects"`` -> update the project combo box (list of long projects).
        ``"details"``  -> refresh context for the currently selected project.

        Called by the window dispatcher when only a subset of the workspace
        snapshot has changed.  Falls back to full ``bind_workspace`` when
        both sections are present.
        """
        if not self.is_ui_ready():
            self._pending_workspace_sections = (snapshot, sections)
            return
        if not sections:
            return

        self._workspace = snapshot

        if "projects" in sections:
            self._refresh_project_combo(snapshot)

        if "details" in sections:
            self._refresh_selected_project_context()

    def _refresh_project_combo(self, snapshot: DesktopWorkspaceSnapshot) -> None:
        if not hasattr(self, "_active_projects"):
            self._active_projects = set()
        self._update_active_projects()

        current = self._project_combo.currentText().strip()
        long_projects = [item for item in snapshot.projects if item.mode == "long"]
        long_project_ids = {item.project_id for item in long_projects}
        selected_project = current if current in long_project_ids else ""

        self._project_combo.blockSignals(True)
        self._project_combo.clear()
        for project in long_projects:
            self._project_combo.addItem(project.project_id)
        if selected_project:
            self._project_combo.setCurrentText(selected_project)
        elif long_projects:
            first = long_projects[0]
            self._project_combo.setCurrentText(first.project_id)
            self._chapter_spin.blockSignals(True)
            self._chapter_spin.setValue(first.next_chapter or 1)
            self._chapter_spin.blockSignals(False)
        else:
            self._project_combo.setEditText("")
            self._chapter_spin.blockSignals(True)
            self._chapter_spin.setValue(1)
            self._chapter_spin.blockSignals(False)
        self._project_combo.blockSignals(False)
        self._previous_project = selected_project or None
        self._update_status_dot()

    def _refresh_selected_project_context(self) -> None:
        selected = self._project_combo.currentText().strip()
        if not selected:
            return
        self._activate_project_context(selected)
        self._request_context()

    def bind_studio(self, snapshot: ChapterWorkspaceSnapshot | None) -> None:
        if not self.is_ui_ready():
            self._pending_studio_binding = (snapshot,)
            return
        current_project = self.current_project_id()
        current_chapter = self.current_chapter_number()
        if snapshot is not None and (
            snapshot.project_id != current_project or snapshot.chapter_number != current_chapter
        ):
            return
        if snapshot is None:
            self._activate_project_context(current_project)

        # A fresh snapshot means a previous auto-refresh triggered by a stale
        # checkpoint error has landed.  Clear the in-flight guard so future
        # stale errors can trigger another auto-refresh.
        self._auto_refresh_in_flight = False

        previous_key = (
            (self._studio.project_id, self._studio.chapter_number)
            if self._studio is not None
            else ("", 0)
        )
        previous_checkpoint_id = (
            self._studio.pending_checkpoint.checkpoint_id
            if self._studio is not None and self._studio.pending_checkpoint is not None
            else ""
        )
        self._studio = snapshot
        self._auto_pilot_pending = False
        if snapshot is not None and snapshot.pending_checkpoint is None:
            self._last_submitted_checkpoint_id = None
        self._restore_notes_input()

        if snapshot is not None:
            next_key = (snapshot.project_id, snapshot.chapter_number)
            if next_key != previous_key:
                self._state.reset_chapter_context()
                self._dismiss_checkpoint_dialog()
                self._action_presenter.clear_dismissed_checkpoints()
            self._activate_project_context(snapshot.project_id)

        # ── CheckpointDialog lifecycle sync ──
        pending_checkpoint = snapshot.pending_checkpoint if snapshot is not None else None
        if pending_checkpoint is None:
            self._dismiss_checkpoint_dialog()
            self._action_presenter.clear_dismissed_checkpoints()
        elif previous_checkpoint_id and previous_checkpoint_id != pending_checkpoint.checkpoint_id:
            # Checkpoint changed (e.g. replan). Allow the new checkpoint to pop again.
            self._action_presenter.clear_dismissed_checkpoints()
            if self._checkpoint_dialog is not None:
                new_summary = build_checkpoint_summary_text(
                    pending_checkpoint,
                    studio_warnings=self._studio_warnings(),
                )
                self._checkpoint_dialog.update_checkpoint(pending_checkpoint, new_summary)

        has_data = snapshot is not None
        if not hasattr(self, "_context_skeleton"):
            self.context_changed.emit()
            return
        self._context_skeleton.setVisible(not has_data)
        if not has_data:
            self._context_skeleton.start_pulse()
            self._jobs_loading = False
            self._jobs = []
            self._book_level_jobs = []
            self._clear_chapter_job_cards()
            self._render_jobs_panel([], loading=False)
            # 显式清理其他面板，避免在切换项目时仍显示旧项目残留信息：
            # 章节列表、罗盘、检视面板、记忆面板、关系卡、连贯/因果清单。
            self._render_rail()
            self._render_compass()
            self._render_inspector()
            self._render_memory_panel()
            self._render_relationship_card()
            self._render_continuity_checklist()
            self._render_causal_checklist()
        else:
            self._context_skeleton.stop_pulse()
        self._action_panel.setVisible(has_data)
        self._compass.setVisible(has_data)

        if has_data:
            self._jobs_loading = True
            self._clear_chapter_job_cards()
            # 清空 self._jobs 避免旧项目/旧章节的 job cards 在 _clear_chapter_job_cards
            # 之后被 _render_jobs_panel 重新插入布局（视觉闪现旧数据）。
            # _force_refresh_jobs 会从 _all_jobs 重新过滤并渲染正确数据。
            self._jobs = []
            self._render_jobs_panel([], loading=True)
            QTimer.singleShot(0, self._force_refresh_jobs)

        # bind_jobs() may consume user_chapter_nav before the async chapter
        # snapshot returns.  Keep the pending marker in the render gate so a
        # manual rail click during auto-run still refreshes the full page.
        user_context_navigation = bool(
            self._state.user_chapter_nav or getattr(self, "_user_nav_ctx_pending", False)
        )
        if self._auto_pilot and self._auto_started and not user_context_navigation:
            # 切回到一个仍处于活跃连跑的项目时，确保 page 级 watchdog 定时器重新启动
            # （当 UI 在其他项目期间，watchdog 会因 _auto_started=False 自停）。
            if not self._auto_watchdog.isActive():
                self._auto_last_progress_at = time.monotonic()
                self._auto_watchdog.start()
            self._render_rail()
            self._render_action_panel()
            self._render_continuity_checklist()
            self._render_causal_checklist()
        else:
            self._render_rail()
            self._render_compass()
            self._render_action_panel()
            self._render_artifacts()
            self._render_inspector()
            self._render_memory_panel()
            self._render_relationship_card()
            self._render_continuity_checklist()
            self._render_causal_checklist()

        self._clean_stale_btn.setVisible(has_data)
        self.context_changed.emit()
        # Skip auto-pilot evaluation while jobs are being reloaded.
        # _force_refresh_jobs → bind_jobs will call _try_auto_action()
        # once the job list is populated.  Calling it here with an empty
        # _jobs list makes current_job=None, bypassing the re-submission
        # guard in decide_autopilot_action and causing a tight loop.
        if not self._jobs_loading:
            self._try_auto_action()

    def _get_projects_with_active_jobs(self, jobs: list[DesktopJobRecord]) -> Set[str]:
        return {
            job.project_id
            for job in jobs
            if job.status in {DesktopJobState.RUNNING, DesktopJobState.QUEUED}
        }

    def _update_active_projects(self) -> None:
        if hasattr(self, "_all_jobs") and self._all_jobs is not None:
            self._active_projects = self._get_projects_with_active_jobs(self._all_jobs)

    def _update_status_dot(self) -> None:
        current_project = self.current_project_id()
        if current_project and current_project in getattr(self, "_active_projects", set()):
            self._project_status_dot.setVisible(True)
        else:
            self._project_status_dot.setVisible(False)
        self._update_background_autorun_label(current_project)

    def _update_background_autorun_label(self, current_project: str) -> None:
        """Show 「后台连跑：A、B」 when other projects are still auto-running."""
        label = getattr(self, "_background_autorun_label", None)
        if label is None:
            return
        state = getattr(self, "_state", None)
        if state is None or not hasattr(state, "active_auto_project_ids"):
            label.setVisible(False)
            return
        active = [pid for pid in state.active_auto_project_ids() if pid and pid != current_project]
        if not active:
            label.setText("")
            label.setVisible(False)
            return
        preview = "、".join(active[:3])
        suffix = f"…等 {len(active)} 个" if len(active) > 3 else ""
        label.setText(f"🔄 后台连跑：{preview}{suffix}")
        label.setToolTip("、".join(active))
        label.setVisible(True)

    def _activate_project_context(self, project_id: str) -> None:
        """Make page-level state follow the visible project immediately."""
        normalized = project_id.strip()
        state = getattr(self, "_state", None)
        previous = getattr(state, "current_project_id", "") if state is not None else ""
        if state is not None:
            state.current_project_id = normalized
        if previous != normalized:
            self._jobs_fingerprint = ()
            self._jobs = []
            self._book_level_jobs = []
        self._sync_project_controls_from_state()

    def _sync_project_controls_from_state(self) -> None:
        writing_mode_selector = getattr(self, "_writing_mode_selector", None)
        state = getattr(self, "_state", None)
        if writing_mode_selector is not None and state is not None:
            writing_mode_selector.blockSignals(True)
            writing_mode_selector.set_mode(state.writing_mode)
            writing_mode_selector.blockSignals(False)

        mode_selector = getattr(self, "_mode_selector", None)
        mode_index = getattr(
            self,
            "_MODE_INDEX",
            {"manual": 0, "suggest": 1, "auto": 2, "book_auto": 3},
        )
        if mode_selector is not None:
            mode_selector.blockSignals(True)
            mode_selector.set_mode(
                mode_index.get(getattr(self, "_mode", "manual"), 0),
                animate=False,
            )
            mode_selector.blockSignals(False)

        follow_cb = getattr(self, "_follow_autorun_cb", None)
        if follow_cb is not None and state is not None:
            follow_cb.blockSignals(True)
            follow_cb.setChecked(bool(state.follow_autorun))
            follow_cb.blockSignals(False)

    def _commit_project_selection(self, new_project: str, *, clear_studio: bool) -> None:
        self._previous_project = new_project
        self._activate_project_context(new_project)
        if clear_studio:
            self.bind_studio(None)
        if (
            hasattr(self, "_all_jobs")
            and self._all_jobs is not None
            and hasattr(self, "_build_jobs_fingerprint")
        ):
            self._jobs_fingerprint = ()
            self.bind_jobs(self._all_jobs)
        # Project switches should feel immediate.  Other chapter-context refresh
        # paths use the timer's default debounce, but a confirmed combo-box
        # selection already represents a stable target, so queue the request for
        # the next UI tick instead of waiting the full debounce interval.
        self._context_request_timer.stop()
        self._context_request_timer.start(0)
        self._update_status_dot()

    def _start_status_rotation(self) -> None:
        if not hasattr(self, "_all_jobs") or self._all_jobs is None:
            self._rotation_active_projects = []
            self._status_rotation_timer.stop()
            return

        active_set = self._get_projects_with_active_jobs(self._all_jobs)
        self._rotation_active_projects = sorted(active_set)

        if len(self._rotation_active_projects) <= 1:
            self._status_rotation_timer.stop()
            return

        self._rotation_index = 0
        if not self._status_rotation_timer.isActive():
            self._status_rotation_timer.start()

    def _render_action_panel_for_project(self, project_id: str) -> None:
        """Render the action panel for a specific project's job status."""
        from .jobs import _CHAPTER_JOB_KINDS, _job_chapter_number

        project_jobs = (
            [
                job
                for job in self._all_jobs
                if job.project_id == project_id and job.kind in _CHAPTER_JOB_KINDS
            ]
            if self._all_jobs
            else []
        )

        priority = {
            DesktopJobState.RUNNING: 0,
            DesktopJobState.QUEUED: 1,
            DesktopJobState.PAUSED: 2,
            DesktopJobState.FAILED: 3,
            DesktopJobState.SUCCEEDED: 4,
        }
        project_jobs.sort(key=lambda j: priority.get(j.status, 5))

        latest_job = project_jobs[0] if project_jobs else None

        if latest_job:
            chapter_num = _job_chapter_number(latest_job) or 1

            auto_active = getattr(self, "_auto_started", False)
            auto_label = "自动" if auto_active else ""
            title_text = f"{project_id} · 正在执行..."
            self._action_title.setText(title_text)

            badge_text = {
                DesktopJobState.QUEUED: f"{auto_label}中" if auto_active else "排队中",
                DesktopJobState.RUNNING: f"{auto_label}中" if auto_active else "执行中",
                DesktopJobState.PAUSED: "自动中" if auto_active else "等待决策",
                DesktopJobState.SUCCEEDED: "已完成",
                DesktopJobState.FAILED: "失败",
            }.get(latest_job.status, "待命")
            self._action_badge.setText(badge_text)
            self._action_badge.set_tone(
                "warning"
                if latest_job.status in (DesktopJobState.RUNNING, DesktopJobState.QUEUED)
                else "success"
                if latest_job.status == DesktopJobState.SUCCEEDED
                else "danger"
                if latest_job.status == DesktopJobState.FAILED
                else "default"
            )

            step_label = (
                display_step_name_for_job(latest_job) if latest_job.current_step else "等待中"
            )
            self._action_summary.setText(f"第 {chapter_num} 章 · {step_label}")
        else:
            active_set = (
                self._get_projects_with_active_jobs(self._all_jobs) if self._all_jobs else set()
            )
            if active_set:
                running_count = len(active_set)
                self._action_title.setText(f"{running_count} 个项目正在运行")
                self._action_badge.setText("并行中")
                self._action_badge.set_tone("warning")
                project_names = "、".join(sorted(active_set))
                self._action_summary.setText(f"当前项目无任务，以下项目正在运行：{project_names}")
            else:
                self._action_title.setText("无运行中任务")
                self._action_badge.setText("待命")
                self._action_badge.set_tone("default")
                self._action_summary.setText("")

    def _stop_status_rotation(self) -> None:
        self._status_rotation_timer.stop()
        self._rotation_index = 0
        self._rotation_paused = False

    def _on_status_rotation_tick(self) -> None:
        if self._rotation_paused:
            return
        if not self._rotation_active_projects:
            self._stop_status_rotation()
            return

        # Rebuild active projects list to handle removed projects
        active_set = (
            self._get_projects_with_active_jobs(self._all_jobs) if self._all_jobs else set()
        )
        self._rotation_active_projects = sorted(active_set)

        # If current index is now out of bounds, reset
        if self._rotation_index >= len(self._rotation_active_projects):
            self._rotation_index = 0

        # If no active projects left, stop rotation
        if not self._rotation_active_projects:
            self._stop_status_rotation()
            return

        # Advance to next project
        self._rotation_index = (self._rotation_index + 1) % len(self._rotation_active_projects)
        next_project = self._rotation_active_projects[self._rotation_index]
        self._render_action_panel_for_project(next_project)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if hasattr(self, "_action_panel") and obj is self._action_panel:
            if event.type() == QEvent.Type.Enter:
                self._rotation_paused = True
            elif event.type() == QEvent.Type.Leave:
                self._rotation_paused = False
        return super().eventFilter(obj, event)

    def _force_refresh_jobs(self) -> None:
        """Re-render jobs panel after bind_studio completes, preventing skeleton hang."""
        if hasattr(self, "_all_jobs") and self._all_jobs is not None:
            from .jobs import _CHAPTER_JOB_KINDS, _job_chapter_number

            project_id = self.current_project_id()
            chapter_number = self.current_chapter_number()
            self._jobs = [
                job
                for job in self._all_jobs
                if job.kind in _CHAPTER_JOB_KINDS
                and job.project_id == project_id
                and (_job_chapter_number(job) or chapter_number) == chapter_number
            ]
            if self._coord_book_auto_running():
                known_ids = {job.job_id for job in self._jobs}
                self._jobs = [
                    *[
                        job
                        for job in self._active_project_jobs(self._all_jobs)
                        if job.job_id not in known_ids
                    ],
                    *self._jobs,
                ]
            # _all_jobs 已可用时可安全清除 loading 标记，无需等待下次 bind_jobs 触发。
            # 这防止 _render_jobs_panel 因 loading=True 显示不必要的骨架屏。
            self._jobs_loading = False
        self._render_jobs_panel(self._jobs if self._jobs else [], loading=self._jobs_loading)

    def supports_job_binding(self) -> bool:
        """Return True — this page participates in the generic job-binding protocol."""
        return True

    def on_jobs_changed(self, jobs: list[DesktopJobRecord]) -> None:
        """Generic protocol entry point - delegates to existing bind_jobs."""
        if not self.is_ui_ready():
            self._pending_jobs = list(jobs)
            return
        self.bind_jobs(jobs)

    def bind_task_observation_store(self, store: TaskObservationStore) -> None:
        if not hasattr(self, "_task_focus_panel"):
            self._task_observation_store = store
            return
        self._task_observation_store = store
        self._task_focus_panel.bind_store(store)
        self._task_focus_panel.set_scope(
            TaskFocusScope.CHAPTER,
            project_id=self.current_project_id(),
            chapter_number=self.current_chapter_number(),
        )

    def _schedule_jobs_panel_render(self, jobs: list[DesktopJobRecord], *, loading: bool) -> None:
        """节流的 jobs panel 渲染 — 仅渲染，不影响 bind_jobs 其他副作用。

        第一次调用时 lazy 创建 16 ms ``QTimer``，避免修改 ``ChapterStudioPage.__init__``。
        """
        if not hasattr(self, "_jobs_panel_render_pending"):
            self._jobs_panel_render_pending = False
            self._jobs_panel_render_jobs: list[DesktopJobRecord] = []
            self._jobs_panel_render_loading = False
            self._jobs_panel_render_timer = QTimer(self)
            self._jobs_panel_render_timer.setSingleShot(True)
            self._jobs_panel_render_timer.setInterval(JOBS_PANEL_RENDER_COALESCE_MS)
            self._jobs_panel_render_timer.timeout.connect(self._flush_jobs_panel_render)
        self._jobs_panel_render_jobs = list(jobs)
        self._jobs_panel_render_loading = loading
        if self._jobs_panel_render_pending:
            return
        self._jobs_panel_render_pending = True
        host_window = self.window()
        if host_window is not self and host_window.isVisible() and not self.isVisible():
            # State binding still runs while hidden, but rebuilding dozens of
            # job-card widgets can wait until the user returns to this page.
            self._jobs_panel_render_timer.stop()
            return
        incoming_shape = tuple((job.job_id, job.status) for job in jobs)
        rendered_shape = tuple(
            (job.job_id, job.status) for job in getattr(self, "_latest_render_jobs", [])
        )
        if not self._chapter_job_cards or incoming_shape != rendered_shape:
            # Structural changes (first bind, task added/removed, terminal
            # transition) are user-visible state, not repaint bursts. Render
            # them in the same turn; only event-only updates are coalesced.
            self._flush_jobs_panel_render()
            return
        self._jobs_panel_render_timer.start()

    def _flush_jobs_panel_render(self) -> None:
        host_window = self.window()
        if host_window is not self and host_window.isVisible() and not self.isVisible():
            self._jobs_panel_render_pending = True
            return
        self._jobs_panel_render_pending = False
        self._render_jobs_panel(
            self._jobs_panel_render_jobs,
            loading=self._jobs_panel_render_loading,
        )

    def bind_jobs(self, jobs: list[DesktopJobRecord]) -> None:
        from .jobs import (
            _BOOK_LEVEL_JOB_KINDS,
            _CHAPTER_JOB_KINDS,
            _job_chapter_number,
        )

        project_id = self.current_project_id()
        chapter_number = self.current_chapter_number()
        if hasattr(self, "_task_focus_panel"):
            self._task_focus_panel.set_scope(
                TaskFocusScope.CHAPTER,
                project_id=project_id,
                chapter_number=chapter_number,
            )
        fingerprint = self._build_jobs_fingerprint(jobs, project_id, chapter_number)
        if fingerprint == self._jobs_fingerprint:
            return
        self._jobs_fingerprint = fingerprint

        self._all_jobs = jobs
        self._update_active_projects()
        self._update_status_dot()
        self._start_status_rotation()

        # If rotation stopped, show selected project's status instead
        if not self._status_rotation_timer.isActive() and self._rotation_active_projects:
            current = self.current_project_id()
            if current in self._rotation_active_projects:
                self._render_action_panel_for_project(current)

        latest_memory_event = self._select_latest_memory_event(
            jobs,
            project_id=project_id,
            fallback_chapter=chapter_number,
        )
        if latest_memory_event is not None:
            event_type, event_at, payload = latest_memory_event
            current_key = (event_type, event_at)
            applied_key = self._last_applied_memory_event_key.get(project_id)

            if applied_key is None:
                from novel_forge.desktop.state.store import get_ui_store

                store_status = get_ui_store().memory_status(project_id)
                if not store_status:
                    disk_status = self._load_memory_status_from_disk(project_id)
                    if disk_status:
                        self._last_applied_memory_event_key[project_id] = current_key
                        applied_key = current_key

            if applied_key != current_key:
                # P0-5 (round 2): ``tasks_failed`` must NOT be routed to
                # ``_handle_memory_updated`` — that method only knows
                # about the happy-path status fields and would silently
                # drop ``tasks_failed``. The dedicated
                # ``_handle_memory_tasks_failed`` keeps the failure
                # signal visible in the UI store.
                if event_type == "invalidated":
                    self._handle_memory_invalidated(project_id, payload)
                elif event_type == "tasks_failed":
                    self._handle_memory_tasks_failed(project_id, payload)
                else:
                    self._handle_memory_updated(project_id, payload)
                self._last_applied_memory_event_key[project_id] = current_key

        self._jobs_loading = False
        self._jobs = [
            job
            for job in jobs
            if job.kind in _CHAPTER_JOB_KINDS
            and job.project_id == project_id
            and (_job_chapter_number(job) or chapter_number) == chapter_number
        ]
        if self._coord_book_auto_running():
            known_ids = {job.job_id for job in self._jobs}
            self._jobs = [
                *[job for job in self._active_project_jobs(jobs) if job.job_id not in known_ids],
                *self._jobs,
            ]
        self._book_level_jobs = [
            job
            for job in jobs
            if job.kind in _BOOK_LEVEL_JOB_KINDS and job.project_id == project_id
        ]
        if self._studio is not None and self._studio.pending_checkpoint is None:
            paused_jobs_sorted = sorted(
                [(i, j) for i, j in enumerate(self._jobs) if j.status == DesktopJobState.PAUSED],
                key=lambda t: t[1].updated_at or "",
                reverse=True,
            )
            first_paused_idx = paused_jobs_sorted[0][0] if paused_jobs_sorted else None
            self._jobs = [
                job
                for i, job in enumerate(self._jobs)
                if job.status != DesktopJobState.PAUSED or i == first_paused_idx
            ]
        self._rail.bind_jobs(jobs, project_id=project_id)
        self._maybe_auto_refresh_on_stale_error(self._jobs)
        self._render_action_panel()
        self._schedule_jobs_panel_render(jobs, loading=self._jobs_loading)
        active = any(
            j.status in {DesktopJobState.RUNNING, DesktopJobState.QUEUED} for j in self._jobs
        )
        if active and self._state.scheduled_retry_at > 0:
            self._cancel_scheduled_retry()
        self._update_repair_btn_state()
        self._update_reevaluate_btn_state()
        self._update_reextract_btn_state()
        self._update_motif_repair_btn_state()
        self._update_book_level_btn_state()
        if active and self._auto_pilot and self._auto_started:
            self._auto_last_progress_at = time.monotonic()
        self._try_auto_action()

    def _on_clear_chapter_warnings(self) -> None:
        if self._studio is None or self._memory_presenter is None:
            return

        warning_items = self._raw_chapter_warnings()
        if not warning_items:
            return

        key = (self._studio.project_id, self._studio.chapter_number)
        self._state.dismissed_warning_fingerprints[key] = self._warning_fingerprint(warning_items)
        self._memory_presenter.update_chapter_warnings([])
        self._render_inspector()
        self._render_action_panel()

    def _sync_motif_lookback_from_settings(self) -> None:
        if self._memory_presenter is None or not hasattr(self, "_memory_panel_widget"):
            return
        try:
            raw_lookback = getattr(get_settings(), "memory_motif_related_lookback_chapters", None)
            lookback = max(
                0,
                int(2 if raw_lookback is None else raw_lookback),
            )
        except Exception:
            lookback = 2
        self._memory_presenter.set_motif_related_lookback_chapters(lookback)
        self._memory_panel_widget.set_motif_lookback_chapters(lookback)

    def _on_motif_lookback_changed(self, value: int) -> None:
        lookback = max(0, int(value))
        if self._memory_presenter is not None:
            self._memory_presenter.set_motif_related_lookback_chapters(lookback)

        env_key = "NOVEL_FORGE_MEMORY_MOTIF_RELATED_LOOKBACK_CHAPTERS"
        if os.environ.get(env_key) != str(lookback):
            os.environ[env_key] = str(lookback)
            reset_settings()

    def focus_project(self, project_id: str, chapter_number: int | None = None) -> None:
        # NOTE: project_id must be plain text (no emoji suffix) to match setCurrentText() exact match requirement.
        # The combo box items are populated with plain project_id in bind_workspace().
        project_value = project_id.strip()
        if not self.is_ui_ready():
            self._pending_focus_request = (project_value, chapter_number)
            return
        self._project_combo.setCurrentText(project_value)
        if self.current_project_id() != project_value:
            return
        if chapter_number is not None and chapter_number > 0:
            self._chapter_spin.blockSignals(True)
            self._chapter_spin.setValue(chapter_number)
            self._chapter_spin.blockSignals(False)
        self._activate_project_context(project_value)
        if (
            hasattr(self, "_all_jobs")
            and self._all_jobs is not None
            and hasattr(self, "_build_jobs_fingerprint")
        ):
            self._jobs_fingerprint = ()
            self.bind_jobs(self._all_jobs)
        self._request_context()

    def forget_project(self, project_id: str) -> None:
        project_id = project_id.strip()
        if not project_id:
            return
        studio_project_id = self._studio.project_id if self._studio is not None else ""
        if self.current_project_id() != project_id and studio_project_id != project_id:
            return

        if hasattr(self, "_all_jobs") and self._all_jobs is not None:
            for job in self._all_jobs:
                if job.project_id == project_id and job.status in {
                    DesktopJobState.RUNNING,
                    DesktopJobState.QUEUED,
                }:
                    self.cancel_job_requested.emit(job.job_id, "项目已删除")

        self._context_request_timer.stop()
        self._state.reset_auto_pilot()
        self._state.jobs_fingerprint = ()
        self._jobs = []
        self._book_level_jobs = []

        self._project_combo.blockSignals(True)
        if self.current_project_id() == project_id:
            self._project_combo.setCurrentText("")
            self._project_combo.setEditText("")
        self._chapter_spin.blockSignals(True)
        self._chapter_spin.setValue(1)
        self._chapter_spin.blockSignals(False)
        self._project_combo.blockSignals(False)

        self.bind_studio(None)
        self._render_jobs_panel([], loading=False)

    def current_project_id(self) -> str:
        if not self.is_ui_ready():
            pending = self._pending_focus_request
            return pending[0] if pending is not None else ""
        return self._project_combo.currentText().strip()

    def current_chapter_number(self) -> int:
        if not self.is_ui_ready():
            pending = self._pending_focus_request
            if pending is not None and pending[1] is not None:
                return max(1, pending[1])
            return 1
        return self._chapter_spin.value()

    def primary_action_label(self) -> str:
        if self._studio is None:
            return "继续当前节点 →"
        if self._studio.pending_checkpoint is not None:
            return "继续当前节点 →"
        if self._is_current_chapter_done():
            if self._studio.chapter_number < self._studio.total_chapters:
                return f"前往第 {self._studio.chapter_number + 1} 章 →"
            return "本书已完成"
        return "继续当前节点 →"

    def trigger_primary_action(self) -> None:
        if self._studio and self._studio.pending_checkpoint:
            recommended = next(
                (
                    option
                    for option in self._studio.pending_checkpoint.options
                    if option.is_recommended
                ),
                None,
            )
            if recommended is not None:
                self._handle_checkpoint_option(recommended)
                return
        if self._studio and self._is_current_chapter_done():
            if self._studio.chapter_number < self._studio.total_chapters:
                self._chapter_spin.setValue(self._studio.chapter_number + 1)
            return
        self._submit_prepare()

    def open_current_project(self) -> None:
        self._emit_open_project()

    def has_unsaved_changes(self) -> bool:
        if self._notes.isReadOnly():
            return False
        doc = self._notes.document()
        if doc is None:
            return False
        return bool(doc.isModified() and self._notes.toPlainText().strip())

    def unsaved_changes_description(self) -> str:
        return "- 章台页有临时备注尚未提交（当前版本不支持自动保存）"

    def _mark_notes_committed(self, *, submitted_with_notes: bool = False) -> None:
        doc = self._notes.document()
        if doc is not None:
            doc.setModified(False)
        if submitted_with_notes:
            self._notes_submitted = True
            self._notes.setReadOnly(True)
            self._notes.setPlaceholderText("约束已提交，等待新方案生成…")
            self._notes.clear()

    def _restore_notes_input(self) -> None:
        if not getattr(self, "_notes_submitted", False):
            return
        self._notes_submitted = False
        notes = getattr(self, "_notes", None)
        if notes is None:
            return
        notes.setReadOnly(False)
        notes.setPlaceholderText("在这里写本章补充约束、方案备注，或对 AI 选项的人工说明。")

    def _on_project_selection_changed(self, new_project: str) -> None:
        """Handle project combo selection change with active job confirmation."""
        from .jobs import _job_chapter_number

        new_project = new_project.strip()
        previous_project = self._previous_project

        # Same project or first selection - confirm immediately
        if not previous_project or previous_project == new_project:
            clear_studio = self._studio is not None and self._studio.project_id != new_project
            self._commit_project_selection(new_project, clear_studio=clear_studio)
            return

        # Check if previous project has active jobs
        if not hasattr(self, "_all_jobs") or self._all_jobs is None:
            self._commit_project_selection(new_project, clear_studio=True)
            return

        active_projects = self._get_projects_with_active_jobs(self._all_jobs)
        if previous_project not in active_projects:
            self._commit_project_selection(new_project, clear_studio=True)
            return

        # Find job status for the dialog
        active_jobs = [
            job
            for job in self._all_jobs
            if job.project_id == previous_project
            and job.status in {DesktopJobState.RUNNING, DesktopJobState.QUEUED}
        ]
        if not active_jobs:
            self._commit_project_selection(new_project, clear_studio=True)
            return

        if self._coord_book_auto_running():
            # Multi-project parallel: A 在 BOOK_AUTO 后台连跑由 window 级驱动
            # （window._drive_active_autoruns）继续推进，UI 视图静默切到 B。
            # 这里不动 _auto_pilot_per_project[A]，保留 A 的 auto_started=True。
            window = self.window()
            if hasattr(window, "show_priority_status"):
                window.show_priority_status(
                    f"「{previous_project}」继续后台连跑，UI 已切换到「{new_project}」",
                    4_000,
                    1,  # _STATUS_INFO
                )
            self._commit_project_selection(new_project, clear_studio=True)
            return

        # Get chapter number from job
        job = active_jobs[0]
        chapter_number = _job_chapter_number(job) or 1
        if job.status == DesktopJobState.QUEUED:
            job_status = "排队中"
        else:
            job_status = f"正在生成第 {chapter_number} 章"

        # Show confirmation dialog
        dialog = ProjectSwitchConfirmDialog(
            project_name=previous_project,
            chapter_number=chapter_number,
            job_status=job_status,
            parent=self,
        )
        if not dialog.exec():
            # User canceled - restore previous selection
            self._project_combo.blockSignals(True)
            self._project_combo.setCurrentText(previous_project)
            self._project_combo.blockSignals(False)
            self._previous_project = previous_project
            return

        # User confirmed - proceed with switch
        # 多项目并行：A 的连跑（auto/book_auto）由 window 级驱动在后台继续推进，
        # 不再调用 _stop_auto_pilot。这与对话框提示「切换后任务将继续在后台运行」
        # 保持一致。状态仍保存在 _state._auto_pilot_per_project[A] 中。
        if self._auto_pilot and self._auto_started:
            window = self.window()
            if hasattr(window, "show_priority_status"):
                window.show_priority_status(
                    f"「{previous_project}」继续后台连跑，UI 已切换到「{new_project}」",
                    5_000,
                    1,  # _STATUS_INFO
                )
        self._commit_project_selection(new_project, clear_studio=True)

        # Reset rotation state on manual project switch
        self._rotation_index = 0
        self._rotation_paused = False

        # Restart rotation if there are now 2+ active projects
        if len(self._rotation_active_projects) > 1:
            if not self._status_rotation_timer.isActive():
                self._status_rotation_timer.start()

    def _on_user_chapter_changed(self, _value: int) -> None:
        if self._auto_pilot and self._auto_started:
            self._state.user_chapter_nav = True
        self._request_context()
        # 章节切换时立即用已有的 jobs 数据重新过滤并渲染任务流面板，
        # 避免等待下一次 bind_jobs 调用导致任务流显示为空。
        if hasattr(self, "_all_jobs") and self._all_jobs is not None:
            self.bind_jobs(self._all_jobs)

    def _on_follow_autorun_changed(self, state: int) -> None:
        self._state.follow_autorun = bool(state)
        self._notify_ui_state_changed()

    def _on_writing_mode_changed(self, mode: str) -> None:
        self._state.writing_mode = mode
        self._notify_ui_state_changed()

    def _notify_ui_state_changed(self) -> None:
        """Ask the window to debounce-save this page's selection state."""
        signal = getattr(self, "ui_state_changed", None)
        emit = getattr(signal, "emit", None)
        if callable(emit):
            emit()

    def _on_notes_toggled(self) -> None:
        """Persist the notes section expanded state per chapter."""
        if self._studio is None:
            return
        key = (self._studio.project_id, self._studio.chapter_number)
        self._notes_expanded_by_chapter[key] = self._notes_section._toggle.isChecked()

    def _maybe_auto_refresh_on_stale_error(self, jobs: list[DesktopJobRecord]) -> None:
        """收到 chapter_session_stale 错误时，自动重拉章节快照刷新 checkpoint 选项。

        后台 asyncio 任务可能推进了 checkpoint（如 plan->guard），导致用户在前端
        持有的旧 checkpoint_id 上提交决策时失败。识别到结构化 error_code 后触发一次
        章节上下文刷新，让用户看到最新的 checkpoint 选项，而非弹"请刷新后重试"。
        用 ``_auto_refresh_in_flight`` 防重入，``bind_studio`` 回来时清除。
        失败 job 会留在历史记录里，因此按 ``job_id`` 去重（
        ``_auto_refresh_consumed_job_ids``），避免同一条历史失败在每次
        jobs 指纹变化时反复触发刷新。
        """
        if self._auto_refresh_in_flight:
            return
        for job in jobs:
            if job.status != DesktopJobState.FAILED:
                continue
            if job.kind not in {
                "resolve_chapter_checkpoint",
                "resolve_chapter_checkpoint_finalize",
            }:
                continue
            if job.job_id in self._auto_refresh_consumed_job_ids:
                continue
            summary = job.error_summary if isinstance(job.error_summary, dict) else {}
            if str(summary.get("error_code") or "").strip() != "chapter_session_stale":
                continue
            self._auto_refresh_in_flight = True
            consumed = self._auto_refresh_consumed_job_ids
            consumed.add(job.job_id)
            # Keep the dedupe set bounded; only recent failures matter.
            if len(consumed) > 32:
                for stale_id in list(consumed)[: len(consumed) - 32]:
                    consumed.discard(stale_id)
            QTimer.singleShot(0, self._request_context)
            return

    def _request_context(self) -> None:
        project_id = self.current_project_id()
        if not project_id:
            return
        self._sync_motif_lookback_from_settings()
        if self._workspace is not None:
            matched = next(
                (
                    project
                    for project in self._workspace.projects
                    if project.project_id == project_id and project.mode == "long"
                ),
                None,
            )
            if matched is None:
                return
        if project_id:
            self.chapter_context_requested.emit(project_id, self.current_chapter_number())

    def _goto_global_relationships(self) -> None:
        project_id = self.current_project_id()
        if project_id:
            self.navigate_requested.emit(f"projects:relationships:{project_id}")
        else:
            self.navigate_requested.emit("projects:relationships")

    def _emit_open_project(self) -> None:
        project_id = self.current_project_id()
        if project_id:
            self.open_project_requested.emit(project_id)

    def _emit_view_project(self) -> None:
        project_id = self.current_project_id()
        if project_id:
            self.view_project_requested.emit(project_id)

    def _cancel_running_job(self) -> None:
        for job in [*self._jobs, *self._book_level_jobs]:
            if job.status in {DesktopJobState.RUNNING, DesktopJobState.QUEUED}:
                self.cancel_job_requested.emit(job.job_id, "用户已取消")

    # ── Checkpoint dialog methods ──────────────────────────────────────────

    def _show_checkpoint_dialog(self, checkpoint: DecisionCheckpoint, summary_text: str) -> None:
        """Create/update CheckpointDialog, covering the chapter workspace."""
        if self._checkpoint_dialog is not None:
            if self._checkpoint_dialog.is_for_checkpoint(checkpoint):
                if self._checkpoint_dialog.summary_text != summary_text:
                    self._checkpoint_dialog.update_checkpoint(checkpoint, summary_text)
                # Task 2.1: skip raise/activate when already visible to prevent UI flicker
                if self._checkpoint_dialog.isVisible():
                    return
                self._checkpoint_dialog.show()
                self._checkpoint_dialog.raise_()
                self._checkpoint_dialog.activateWindow()
                return  # Same checkpoint, don't recreate
            self._dismiss_checkpoint_dialog()  # Different checkpoint, silent replace
        dialog = CheckpointDialog(
            parent=self,
            checkpoint=checkpoint,
            summary_text=summary_text,
            initial_notes=self._notes.toPlainText(),
            free_floating=True,
        )
        dialog.option_selected.connect(self._on_checkpoint_dialog_option_selected)
        dialog.dismissed.connect(self._on_checkpoint_dialog_dismissed)
        self._checkpoint_dialog = dialog
        dialog.show_animated()
        # Task 2.3: pause rotation timer while dialog is open to avoid unnecessary re-renders
        if hasattr(self, "_status_rotation_timer"):
            self._status_rotation_timer.stop()

    def _on_checkpoint_dialog_option_selected(self, option: DecisionOption, notes: str) -> None:
        """Dialog option clicked → sync notes to _notes → use existing submit path."""
        self._notes.setPlainText(notes)  # Dialog notes are the submitted notes source.
        self._dismiss_checkpoint_dialog()
        self._handle_checkpoint_option(option)  # Reuse existing submit logic

    def _on_checkpoint_dialog_dismissed(self) -> None:
        """User manually closed dialog → record dismissed → fall back to inline buttons."""
        if self._checkpoint_dialog is not None:
            cp_id = self._checkpoint_dialog.checkpoint_id
            self._action_presenter.mark_checkpoint_dismissed(cp_id)
            self._remember_checkpoint_dialog_dismissed(cp_id)
            self._checkpoint_dialog = None
        # Task 2.2: defer render to next event loop tick to avoid synchronous re-render loop
        QTimer.singleShot(0, self._render_action_panel)
        # Task 2.3: resume rotation timer after dialog is dismissed
        if (
            hasattr(self, "_status_rotation_timer")
            and len(getattr(self, "_rotation_active_projects", [])) > 1
        ):
            self._status_rotation_timer.start()

    def _checkpoint_dialog_is_for(self, checkpoint: DecisionCheckpoint) -> bool:
        """Helper to check if dialog is for a given checkpoint."""
        return self._checkpoint_dialog is not None and self._checkpoint_dialog.is_for_checkpoint(
            checkpoint
        )

    def _studio_warnings(self) -> list[str]:
        """Extract warnings from current snapshot for summary_text construction."""
        if self._studio is None:
            return []
        return [str(w).strip() for w in (self._studio.warnings or []) if str(w).strip()]

    def _dismiss_checkpoint_dialog(self) -> None:
        """Close the active checkpoint dialog without triggering fallback render."""
        if self._checkpoint_dialog is None:
            return
        self._checkpoint_dialog.dismiss_animated(emit_dismissed=False)
        self._checkpoint_dialog = None
        # Task 2.3: resume rotation timer after programmatic dismiss
        if (
            hasattr(self, "_status_rotation_timer")
            and len(getattr(self, "_rotation_active_projects", [])) > 1
        ):
            self._status_rotation_timer.start()

    def _checkpoint_dialog_store_key(self, checkpoint_id: str) -> str:
        """Return a stable UI-store key for a dismissed checkpoint dialog."""
        project_id = self.current_project_id()
        chapter_number = self.current_chapter_number()
        return f"chapter_studio.dismissed_checkpoint_dialog.{project_id}.{chapter_number}.{checkpoint_id}"

    def _remember_checkpoint_dialog_dismissed(self, checkpoint_id: str) -> None:
        """Remember a manual dialog close for the current desktop session."""
        store = getattr(self, "_store", None)
        if store is None:
            return
        try:
            store.set(self._checkpoint_dialog_store_key(checkpoint_id), True)
        except Exception:
            return

    def _is_checkpoint_dialog_dismissed(self, checkpoint: DecisionCheckpoint) -> bool:
        """Return whether this checkpoint dialog was already dismissed in this session."""
        if self._checkpoint_dialog_is_for(checkpoint):
            return False
        store = getattr(self, "_store", None)
        if store is None:
            return False
        try:
            return bool(
                store.get(self._checkpoint_dialog_store_key(checkpoint.checkpoint_id), False)
            )
        except Exception:
            return False
