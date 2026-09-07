"""Chapter Studio launcher widget for the workflow page."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from novel_forge.desktop.constants import JOB_STATUS_TEXT
from novel_forge.desktop.jobs import DesktopJobRecord
from novel_forge.desktop.pages.workflow.form_utils import _param_block, _param_spin
from novel_forge.desktop.progress import display_step_name_for_job
from novel_forge.desktop.widgets import ActionButton, SectionHeading, Surface
from novel_forge.desktop.workspace import DesktopWorkspaceSnapshot


class ChapterStudioLauncher(Surface):
    open_studio_requested = Signal(str, int)
    open_project_requested = Signal(str)
    context_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("panel", parent)
        self._snapshot: DesktopWorkspaceSnapshot | None = None
        self._latest_job: DesktopJobRecord | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(14)

        layout.addWidget(
            SectionHeading(
                "章节工作台入口",
                "这里不再直接跑完整续写表单，而是先定位项目与章节，再进入专属工作台处理方案、写作与决策。",
            )
        )

        info = Surface("inset")
        info_layout = QVBoxLayout(info)
        info_layout.setContentsMargins(16, 14, 16, 14)
        info_layout.setSpacing(6)
        self._project_info_label = QLabel("请先择定项目，再进入章节工作台。")
        self._project_info_label.setObjectName("cardMeta")
        self._project_info_label.setWordWrap(True)
        info_layout.addWidget(self._project_info_label)
        self._job_info_label = QLabel("工作台流程：方案确认 → 写作 → 校验 → 归档")
        self._job_info_label.setObjectName("cardHint")
        self._job_info_label.setWordWrap(True)
        info_layout.addWidget(self._job_info_label)
        layout.addWidget(info)

        params_row = QHBoxLayout()
        params_row.setSpacing(14)
        self._project_id = QComboBox()
        self._project_id.setEditable(True)
        self._project_id.setMinimumWidth(200)
        self._project_id.currentTextChanged.connect(self._on_project_changed)
        params_row.addLayout(_param_block("长篇项目 *", self._project_id))

        self._chapter_number = _param_spin(1, 10000, 1)
        self._chapter_number.valueChanged.connect(lambda _value: self.context_changed.emit())
        params_row.addLayout(_param_block("章节号 *", self._chapter_number))
        params_row.addStretch()
        layout.addLayout(params_row)

        button_row = QHBoxLayout()
        button_row.setSpacing(10)
        prefill_btn = ActionButton("带入最近项目", variant="secondary")
        prefill_btn.clicked.connect(self.prefill_latest_project)
        button_row.addWidget(prefill_btn)

        self._open_btn = ActionButton("打开项目文件夹", variant="secondary")
        self._open_btn.clicked.connect(self._emit_open)
        self._open_btn.setEnabled(False)
        button_row.addWidget(self._open_btn)

        launch_button = ActionButton("进入章节工作台 →")
        launch_button.clicked.connect(self._emit_open_studio)
        button_row.addWidget(launch_button)
        button_row.addStretch()
        layout.addLayout(button_row)

        route = QLabel(
            "工作台路线：上一章结果 / 本章目标 / 下一章预埋点，会与章节计划、创作报告、AI 选项决策放在同一页。"
        )
        route.setObjectName("formFieldHint")
        route.setWordWrap(True)
        layout.addWidget(route)

    def bind_snapshot(self, snapshot: DesktopWorkspaceSnapshot) -> None:
        self._snapshot = snapshot
        current = self._project_id.currentText().strip()
        long_projects = [project for project in snapshot.projects if project.mode == "long"]
        self._project_id.blockSignals(True)
        self._project_id.clear()
        for project in long_projects:
            self._project_id.addItem(project.project_id)
        if current:
            self._project_id.setEditText(current)
        elif long_projects:
            first = long_projects[0]
            self._project_id.setCurrentText(first.project_id)
            self._chapter_number.setValue(first.next_chapter or 1)
        self._project_id.blockSignals(False)
        self._on_project_changed(self._project_id.currentText())

    def focus_project(self, project_id: str, chapter_number: int | None = None) -> None:
        self._project_id.setCurrentText(project_id)
        if chapter_number and chapter_number > 0:
            self._chapter_number.setValue(chapter_number)
        self._open_btn.setEnabled(bool(project_id.strip()))
        self.context_changed.emit()

    def prefill_latest_project(self) -> None:
        if self._snapshot is None:
            self.context_changed.emit()
            return
        project = next((item for item in self._snapshot.projects if item.mode == "long"), None)
        if project is None:
            self.context_changed.emit()
            return
        self.focus_project(project.project_id, project.next_chapter or 1)

    def current_project_id(self) -> str:
        return self._project_id.currentText().strip()

    def current_chapter_number(self) -> int:
        return self._chapter_number.value()

    def update_job(self, job: DesktopJobRecord | None) -> None:
        self._latest_job = job
        if job is None:
            self._job_info_label.setText("工作台流程：方案确认 → 写作 → 校验 → 归档")
            return
        self._job_info_label.setText(
            f"最近任务：{job.label} · {JOB_STATUS_TEXT[job.status]} · {display_step_name_for_job(job)}"
        )

    def _on_project_changed(self, project_id: str) -> None:
        self._open_btn.setEnabled(bool(project_id.strip()))
        if self._snapshot is None:
            self.context_changed.emit()
            return
        project = next(
            (
                item
                for item in self._snapshot.projects
                if item.project_id == project_id and item.mode == "long"
            ),
            None,
        )
        if project is None:
            self._project_info_label.setText(f"卷帙「{project_id}」尚未建档，请核对项目 ID。")
            self.context_changed.emit()
            return
        completed = project.completed_chapters
        total = getattr(project, "total_chapters", "?")
        next_chapter = project.next_chapter or (completed + 1)
        self._project_info_label.setText(
            f"项目：{project_id} · 已完成 {completed} 章 / 共 {total} 章 · 建议进入第 {next_chapter} 章工作台"
        )
        self._chapter_number.setValue(next_chapter)
        self.context_changed.emit()

    def _emit_open(self) -> None:
        project_id = self.current_project_id()
        if project_id:
            self.open_project_requested.emit(project_id)

    def _emit_open_studio(self) -> None:
        project_id = self.current_project_id()
        if project_id:
            self.open_studio_requested.emit(project_id, self.current_chapter_number())
