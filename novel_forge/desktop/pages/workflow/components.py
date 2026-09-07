"""Composable widgets used by the desktop workflow page.

This module contains lightweight components that compose together to build
the workflow page UI. Form widgets have been extracted to workflow_forms.py
for better separation of concerns.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from novel_forge.desktop.constants import JOB_STATUS_TEXT
from novel_forge.desktop.jobs import DesktopJobRecord
from novel_forge.desktop.pages.workflow.chapter_launcher import (
    ChapterStudioLauncher,  # noqa: F401 (kept for import compatibility)
)
from novel_forge.desktop.pages.workflow.forms import LongInitForm
from novel_forge.desktop.pages.workflow.jobs import JobCard, ModeBar, SubModeBar
from novel_forge.desktop.pages.workflow.workers import OutlinePolishWorker
from novel_forge.desktop.progress import memory_stage_status_for_job
from novel_forge.desktop.thread_pools import desktop_thread_pools
from novel_forge.desktop.widgets import ActionButton, SectionHeading, Surface
from novel_forge.desktop.workers.lifecycle import safe_shutdown_page
from novel_forge.desktop.workspace import DesktopWorkspaceSnapshot

if TYPE_CHECKING:
    from novel_forge.core.schemas.outline import StoryOutline
    from novel_forge.pipeline.steps.polish_outline_step import PolishOutlineResult

__all__ = [
    "ChapterStudioLauncher",
    "CurrentPageStack",
    "JobCard",
    "LongPanel",
    "ModeBar",
    "OutlinePolishPanel",
    "SubModeBar",
    "_StudioEntryCard",
]


class CurrentPageStack(QStackedWidget):
    """Stack whose size hint follows the visible page instead of all pages.

    Qt's default QStackedWidget reports the largest child size hint. On the
    workflow page that leaves stale vertical space after long-form sections are
    collapsed, because the job feed is laid out after the stack.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.currentChanged.connect(lambda _index: self.updateGeometry())

    def sizeHint(self) -> QSize:
        current = self.currentWidget()
        if current is not None:
            return current.sizeHint()
        return super().sizeHint()

    def minimumSizeHint(self) -> QSize:
        current = self.currentWidget()
        if current is not None:
            return current.minimumSizeHint()
        return super().minimumSizeHint()


class _StudioEntryCard(Surface):
    """Minimal redirect card — replaces ChapterStudioLauncher inside LongPanel.

    No duplicate form fields: project/chapter selection lives entirely inside
    ChapterStudioPage.  This card only shows current status and a single
    navigation button.
    """

    open_studio_requested = Signal(str, int)
    context_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("panel", parent)
        self._snapshot: DesktopWorkspaceSnapshot | None = None
        self._latest_job: DesktopJobRecord | None = None
        self._current_project_id: str = ""
        self._current_chapter_number: int = 1

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(14)

        layout.addWidget(
            SectionHeading(
                "章节续写",
                "章节工作台已独立成页——项目选择、方案确认、正文推进与 AI 决策全部集中在那里。",
            )
        )

        # ── 状态信息卡 ─────────────────────────────────────────────
        status_card = Surface("inset")
        sc_layout = QVBoxLayout(status_card)
        sc_layout.setContentsMargins(16, 14, 16, 14)
        sc_layout.setSpacing(6)

        self._project_info = QLabel("尚未加载项目。")
        self._project_info.setObjectName("cardMeta")
        self._project_info.setWordWrap(True)
        sc_layout.addWidget(self._project_info)

        self._job_hint = QLabel("工作台流程：方案确认 → 写作 → 校验 → 归档")
        self._job_hint.setObjectName("cardHint")
        self._job_hint.setWordWrap(True)
        sc_layout.addWidget(self._job_hint)

        layout.addWidget(status_card)

        # ── 操作按钮 ─────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        launch_btn = ActionButton("前往章台 →")
        launch_btn.clicked.connect(self._emit_open_studio)
        btn_row.addWidget(launch_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    def bind_snapshot(self, snapshot: DesktopWorkspaceSnapshot) -> None:
        self._snapshot = snapshot
        long_projects = [p for p in snapshot.projects if p.mode == "long"]
        if long_projects:
            selected = next(
                (p for p in long_projects if p.project_id == self._current_project_id),
                long_projects[0],
            )
            next_ch = selected.next_chapter or (selected.completed_chapters + 1)
            self._current_project_id = selected.project_id
            self._current_chapter_number = max(1, int(next_ch or 1))
            self._project_info.setText(
                f"最近项目：{selected.project_id} · 已完成 {selected.completed_chapters} 章 · "
                f"建议续写第 {next_ch} 章"
            )
        else:
            self._current_project_id = ""
            self._current_chapter_number = 1
            self._project_info.setText("暂无长篇项目，可先使用「立项初始化」创建。")
        self.context_changed.emit()

    def focus_project(self, project_id: str, chapter_number: int | None = None) -> None:
        target_project_id = str(project_id or "").strip()
        selected = None
        if self._snapshot is not None:
            long_projects = [p for p in self._snapshot.projects if p.mode == "long"]
            selected = next((p for p in long_projects if p.project_id == target_project_id), None)
            if selected is None and not target_project_id and long_projects:
                selected = long_projects[0]
                target_project_id = selected.project_id
        if not target_project_id:
            self._current_project_id = ""
            self._current_chapter_number = 1
            self._project_info.setText("暂无长篇项目，可先使用「立项初始化」创建。")
            self.context_changed.emit()
            return

        fallback_chapter = 1
        if selected is not None:
            fallback_chapter = selected.next_chapter or (selected.completed_chapters + 1)
        target_chapter = max(1, int(chapter_number or fallback_chapter or 1))
        self._current_project_id = target_project_id
        self._current_chapter_number = target_chapter
        if selected is not None:
            self._project_info.setText(
                f"最近项目：{selected.project_id} · 已完成 {selected.completed_chapters} 章 · "
                f"建议续写第 {target_chapter} 章"
            )
        else:
            self._project_info.setText(f"当前项目：{target_project_id} · 准备第 {target_chapter} 章")
        self.context_changed.emit()

    def current_project_id(self) -> str:
        return self._current_project_id

    def current_chapter_number(self) -> int:
        return max(1, int(self._current_chapter_number or 1))

    def update_job(self, job: DesktopJobRecord | None) -> None:
        self._latest_job = job
        if job is None:
            self._job_hint.setText("工作台流程：方案确认 → 写作 → 校验 → 归档")
            return
        hint = f"最近任务：{job.label} · {JOB_STATUS_TEXT[job.status]}"
        memory_stages = memory_stage_status_for_job(job)
        if memory_stages:
            def _label(state: str, name: str) -> str:
                if state == "done":
                    return f"{name}完成"
                if state == "running":
                    return f"{name}中"
                if state == "failed":
                    return f"{name}失败"
                return f"{name}待命"

            hint += (
                " · 记忆："
                + " / ".join(
                    [
                        _label(memory_stages.get("indexing", "pending"), "索引"),
                        _label(memory_stages.get("summary", "pending"), "摘要"),
                        _label(memory_stages.get("motif", "pending"), "母题"),
                    ]
                )
            )
        self._job_hint.setText(hint)

    def _emit_open_studio(self) -> None:
        self.open_studio_requested.emit(
            self.current_project_id(),
            self.current_chapter_number(),
        )


class OutlinePolishPanel(QWidget):
    polish_suggestions_ready = Signal(list)
    polish_complete = Signal(object)
    error = Signal(str)

    FOCUS_FIELDS = ["goal", "beats_summary", "main_plot_points", "subplot_points", "involved_characters", "notes"]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._threadpool = desktop_thread_pools().aux_pool
        self._suggestion_checkboxes: list[QCheckBox] = []
        self._focus_checkboxes: dict[str, QCheckBox] = {}
        self._polish_worker: OutlinePolishWorker | None = None
        self._current_outline: StoryOutline | None = None
        self._snapshot: DesktopWorkspaceSnapshot | None = None
        self._last_result: PolishOutlineResult | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(14)

        layout.addWidget(
            SectionHeading(
                "大纲润色",
                "基于现有大纲，AI 分析并提供优化建议，选择性采纳后批量更新章节大纲。",
            )
        )

        direction_card = Surface("inset")
        dir_layout = QVBoxLayout(direction_card)
        dir_layout.setContentsMargins(16, 14, 16, 14)
        dir_layout.setSpacing(8)

        dir_label = QLabel("调整方向提示")
        dir_label.setObjectName("fieldLabel")
        dir_layout.addWidget(dir_label)

        self._direction_input = QTextEdit()
        self._direction_input.setPlaceholderText(
            "请输入调整方向（如：增强第3-5章的悬念层次）"
        )
        self._direction_input.setMaximumHeight(80)
        dir_layout.addWidget(self._direction_input)

        layout.addWidget(direction_card)

        range_card = Surface("inset")
        range_layout = QHBoxLayout(range_card)
        range_layout.setContentsMargins(16, 14, 16, 14)
        range_layout.setSpacing(12)

        range_label = QLabel("章节范围（可选）")
        range_label.setObjectName("fieldLabel")
        range_layout.addWidget(range_label)

        self._chapter_range = QLineEdit()
        self._chapter_range.setPlaceholderText("3-5 或 1,3,5")
        self._chapter_range.setMaximumWidth(120)
        range_layout.addWidget(self._chapter_range)

        range_layout.addStretch(1)
        layout.addWidget(range_card)

        focus_card = Surface("inset")
        focus_layout = QVBoxLayout(focus_card)
        focus_layout.setContentsMargins(16, 14, 16, 14)
        focus_layout.setSpacing(8)

        focus_label = QLabel("聚焦字段（可多选）")
        focus_label.setObjectName("fieldLabel")
        focus_layout.addWidget(focus_label)

        focus_row = QHBoxLayout()
        focus_row.setSpacing(10)
        for field in self.FOCUS_FIELDS:
            cb = QCheckBox(field)
            cb.setChecked(True)
            self._focus_checkboxes[field] = cb
            focus_row.addWidget(cb)
        focus_row.addStretch(1)
        focus_layout.addLayout(focus_row)
        layout.addWidget(focus_card)

        self._suggestions_group = QWidget()
        sugg_layout = QVBoxLayout(self._suggestions_group)
        sugg_layout.setContentsMargins(0, 0, 0, 0)
        sugg_layout.setSpacing(6)
        self._suggestions_group.setVisible(False)
        layout.addWidget(self._suggestions_group)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self._analyze_btn = ActionButton("✨ 分析建议", variant="primary")
        self._analyze_btn.clicked.connect(self._on_analyze)
        btn_row.addWidget(self._analyze_btn)

        self._polish_btn = ActionButton("🚀 执行润色", variant="secondary")
        self._polish_btn.clicked.connect(self._on_polish)
        self._polish_btn.setEnabled(False)
        btn_row.addWidget(self._polish_btn)

        self._save_btn = ActionButton("💾 确认保存", variant="secondary")
        self._save_btn.clicked.connect(self._on_save)
        self._save_btn.setEnabled(False)
        btn_row.addWidget(self._save_btn)

        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self._preview_card = Surface("inset")
        preview_layout = QVBoxLayout(self._preview_card)
        preview_layout.setContentsMargins(16, 14, 16, 14)
        preview_layout.setSpacing(8)

        preview_label = QLabel("变更预览")
        preview_label.setObjectName("fieldLabel")
        preview_layout.addWidget(preview_label)

        self._preview_text = QLabel("（等待执行润色）")
        self._preview_text.setObjectName("cardMeta")
        self._preview_text.setWordWrap(True)
        preview_layout.addWidget(self._preview_text)
        self._preview_card.setVisible(False)
        layout.addWidget(self._preview_card)

        layout.addStretch(1)

    def _get_selected_focus(self) -> list[str]:
        return [k for k, cb in self._focus_checkboxes.items() if cb.isChecked()]

    def _get_suggestions(self) -> list[str]:
        return [cb.text() for cb in self._suggestion_checkboxes if cb.isChecked()]

    def _parse_chapter_range(self) -> str | None:
        text = self._chapter_range.text().strip()
        if not text:
            return None
        try:
            normalized = text.replace("，", ",")
            for part in normalized.split(","):
                if "-" in part:
                    start, end = [int(p.strip()) for p in part.split("-", 1)]
                    if start > end:
                        return None
                else:
                    int(part.strip())
            return normalized
        except ValueError:
            return None

    def _clear_suggestions(self) -> None:
        for cb in self._suggestion_checkboxes:
            cb.deleteLater()
        self._suggestion_checkboxes.clear()
        layout = self._suggestions_group.layout()
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None and widget != self._suggestions_group:
                widget.deleteLater()

    def _show_suggestions(self, suggestions: list[str]) -> None:
        self._clear_suggestions()
        layout = self._suggestions_group.layout()
        if layout is None:
            return
        for sugg in suggestions:
            cb = QCheckBox(sugg)
            self._suggestion_checkboxes.append(cb)
            layout.addWidget(cb)
        self._suggestions_group.setVisible(True)
        self._polish_btn.setEnabled(True)

    def _on_analyze(self) -> None:
        snapshot = getattr(self, "_snapshot", None)
        if snapshot is None:
            self.error.emit("No project loaded")
            return

        outline = getattr(self, "_current_outline", None)
        if outline is None:
            self.error.emit("No outline available")
            return

        hint = self._direction_input.toPlainText().strip()
        focus = self._get_selected_focus()

        self._polish_worker = OutlinePolishWorker(
            story_outline=outline,
            user_hint=hint,
            selected_suggestions=[],
            focus_fields=focus,
            chapter_range=self._parse_chapter_range(),
            analysis_only=True,
        )
        self._polish_worker.signals.suggestions_ready.connect(self._show_suggestions)
        self._polish_worker.signals.polish_done.connect(self._on_polish_done)
        self._polish_worker.signals.error.connect(self._on_worker_error)
        self._threadpool.start(self._polish_worker)

    def _on_polish(self) -> None:
        snapshot = getattr(self, "_snapshot", None)
        if snapshot is None:
            self.error.emit("No project loaded")
            return

        outline = getattr(self, "_current_outline", None)
        if outline is None:
            self.error.emit("No outline available")
            return

        hint = self._direction_input.toPlainText().strip()
        suggestions = self._get_suggestions()
        focus = self._get_selected_focus()

        self._polish_worker = OutlinePolishWorker(
            story_outline=outline,
            user_hint=hint,
            selected_suggestions=suggestions,
            focus_fields=focus,
            chapter_range=self._parse_chapter_range(),
            analysis_only=False,
        )
        self._polish_worker.signals.polish_done.connect(self._on_polish_done)
        self._polish_worker.signals.error.connect(self._on_worker_error)
        self._threadpool.start(self._polish_worker)

    def _on_polish_done(self, result: PolishOutlineResult) -> None:
        self._last_result = result
        self._save_btn.setEnabled(bool(result.changed_chapters))
        self._preview_card.setVisible(True)

        changed = result.changed_chapters or []
        warnings = result.warnings or []
        if changed:
            preview_text = f"变更章节: {changed}"
        elif result.polish_suggestions:
            preview_text = "已生成润色建议，请选择后执行润色。"
        else:
            preview_text = "未检测到可应用变更。"
        if warnings:
            preview_text += f"\n警告: {warnings}"
        self._preview_text.setText(preview_text)
        self.polish_complete.emit(result)

    def _on_worker_error(self, err: str) -> None:
        self.error.emit(err)

    def _on_save(self) -> None:
        if hasattr(self, "_last_result"):
            self.polish_suggestions_ready.emit(self._get_suggestions())

    def bind_outline(self, outline: StoryOutline | None) -> None:
        self._current_outline = outline

    def bind_snapshot(self, snapshot: DesktopWorkspaceSnapshot) -> None:
        self._snapshot = snapshot

    def shutdown(self) -> None:
        worker = self._polish_worker
        if worker is None:
            return
        safe_shutdown_page(
            self,
            workers=(worker,),
            signals_to_disconnect=(
                (worker.signals.suggestions_ready, self._show_suggestions),
                (worker.signals.polish_done, self._on_polish_done),
                (worker.signals.error, self._on_worker_error),
            ),
        )
        self._polish_worker = None


class LongPanel(QWidget):
    """Panel for long-form project creation and chapter continuation."""

    init_long_requested = Signal(object)
    init_long_autorun_requested = Signal(object)  # request — create project then start chapter auto-run
    init_long_copilot_requested = Signal(object)
    cancel_init_requested = Signal(str)   # job_id
    chapter_studio_requested = Signal(str, int)
    open_project_requested = Signal(str)
    context_changed = Signal()
    workspace_refresh_requested = Signal()
    restart_task_flow_cleanup_requested = Signal(str)
    outline_polish_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self._sub_bar = SubModeBar()
        # Hide legacy one-option sub-mode toggle to avoid duplicate "长篇初始化" entry.
        self._sub_bar.setVisible(False)
        self._sub_bar.mode_changed.connect(self._on_sub_mode)
        layout.addWidget(self._sub_bar)

        self._stack = CurrentPageStack()
        self._stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._init_form = LongInitForm()
        self._chapter_entry = _StudioEntryCard()
        self._chapter_project_id: str = ""
        self._chapter_number: int = 1

        self._init_form.submitted.connect(self.init_long_requested)
        self._init_form.init_long_autorun_requested.connect(self.init_long_autorun_requested)
        self._init_form.init_long_copilot_requested.connect(self.init_long_copilot_requested)
        self._init_form.cancel_requested.connect(self.cancel_init_requested)
        self._init_form.workspace_refresh_requested.connect(self.workspace_refresh_requested)
        self._init_form.restart_task_flow_cleanup_requested.connect(
            self.restart_task_flow_cleanup_requested
        )
        self._chapter_entry.open_studio_requested.connect(self.chapter_studio_requested)
        self._sub_bar.mode_changed.connect(lambda _mode: self.context_changed.emit())
        self._chapter_entry.context_changed.connect(lambda: self.context_changed.emit())

        self._stack.addWidget(self._init_form)
        self._stack.addWidget(self._chapter_entry)
        self._outline_polish = OutlinePolishPanel()
        self._stack.addWidget(self._outline_polish)
        layout.addWidget(self._stack)

    def _on_sub_mode(self, mode: str) -> None:
        self._stack.setCurrentIndex(0 if mode == "init" else 1)

    def current_mode(self) -> str:
        return self._sub_bar.current_mode()

    def select_init(self) -> None:
        self._sub_bar.select("init")

    def select_chapter(self) -> None:
        self._sub_bar.select_chapter()

    def set_storage_root(self, root: Path) -> None:
        self._init_form.set_storage_root(root)

    def shutdown(self) -> None:
        toolbar = getattr(self._init_form, "_toolbar", None)
        if toolbar is not None:
            toolbar.shutdown()
        if hasattr(self, "_outline_polish") and self._outline_polish:
            self._outline_polish.shutdown()

    def bind_snapshot(self, snapshot: DesktopWorkspaceSnapshot) -> None:
        self._init_form.bind_snapshot(snapshot)
        self._chapter_entry.bind_snapshot(snapshot)
        self._outline_polish.bind_snapshot(snapshot)
        valid_long_projects = {p.project_id for p in snapshot.projects if p.mode == "long"}
        if self._chapter_project_id and self._chapter_project_id not in valid_long_projects:
            self._chapter_project_id = ""
            self._chapter_number = 1
        if not self._chapter_project_id:
            self._chapter_project_id = self._chapter_entry.current_project_id()
            self._chapter_number = self._chapter_entry.current_chapter_number()
        else:
            self._chapter_entry.focus_project(self._chapter_project_id, self._chapter_number)
        self.context_changed.emit()

    def focus_chapter(self, project_id: str, chapter_number: int | None = None) -> None:
        self._sub_bar.select_chapter()
        self._chapter_project_id = str(project_id or "").strip()
        self._chapter_number = max(1, int(chapter_number or self._chapter_number or 1))
        self._chapter_entry.focus_project(self._chapter_project_id, self._chapter_number)
        self.context_changed.emit()

    def focus_init_project(self, project_id: str) -> None:
        self._sub_bar.select("init")
        self._init_form.focus_project(project_id)
        self.context_changed.emit()

    def prefill_latest_project(self) -> None:
        self._sub_bar.select_chapter()
        self._chapter_entry.focus_project("", None)
        self._chapter_project_id = self._chapter_entry.current_project_id()
        self._chapter_number = self._chapter_entry.current_chapter_number()
        self.context_changed.emit()

    def export_ui_state(self) -> dict[str, object]:
        """Return the currently selected long-form entry context."""
        return {
            "mode": self.current_mode(),
            "init_project_id": self._init_form.project_id_text(),
            "chapter_project_id": self.chapter_form_project_id(),
            "chapter_number": self.chapter_form_chapter_number(),
        }

    def restore_ui_state(self, payload: object) -> None:
        """Restore a safe navigation target; form text itself comes from drafts."""
        if not isinstance(payload, dict):
            return
        mode = str(payload.get("mode") or "init")
        raw_project_id = (
            payload.get("chapter_project_id")
            if mode == "chapter"
            else payload.get("init_project_id")
        )
        project_id = str(raw_project_id or "").strip()
        try:
            chapter_number = max(1, int(payload.get("chapter_number") or 1))
        except (TypeError, ValueError):
            chapter_number = 1
        if mode == "chapter":
            self.focus_chapter(project_id, chapter_number)
            return
        self.select_init()
        if project_id:
            self.focus_init_project(project_id)

    def export_template(self) -> None:
        self._init_form.export_template()

    def chapter_form_project_id(self) -> str:
        return self._chapter_project_id or self._chapter_entry.current_project_id()

    def init_form_project_id(self) -> str:
        return self._init_form.project_id_text()

    def chapter_form_chapter_number(self) -> int:
        if self._chapter_project_id:
            return max(1, int(self._chapter_number or 1))
        return self._chapter_entry.current_chapter_number()

    def update_init_progress(self, job: DesktopJobRecord | None) -> None:
        self._init_form.update_progress(job)

    def update_chapter_progress(self, job: DesktopJobRecord | None) -> None:
        self._chapter_entry.update_job(job)

    def save_init_draft(self, path: Path) -> bool:
        """Delegate to LongInitForm.save_draft."""
        return self._init_form.save_draft(path)

    def restore_init_draft(self, path: Path) -> bool:
        """Delegate to LongInitForm.restore_draft (only if form is blank)."""
        # Don't overwrite if the user already has content in the form.
        if self._init_form._premise.toPlainText().strip():  # noqa: SLF001
            return False
        return self._init_form.restore_draft(path)
