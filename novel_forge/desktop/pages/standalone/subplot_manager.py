"""Subplot management panel for the narrative blueprint editor.

Provides CRUD operations for subplot plans, including:
- Subplot list with expand/collapse
- Add/edit/delete subplot dialogs
- Arc-to-subplot conversion for event-driven character arcs
- Data binding to narrative_blueprint.json
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics, QResizeEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from novel_forge.core.constants import TaskType
from novel_forge.core.parsing.parse_utils import safe_parse_json
from novel_forge.desktop.components.dialogs import (
    MessageBoxAction,
    _style_message_box_button,
    ask_confirmation,
    show_critical_message,
    show_message_box,
    show_warning_message,
)
from novel_forge.desktop.theme import qcolor_hex
from novel_forge.desktop.thread_pools import desktop_thread_pools
from novel_forge.desktop.workers import BaseJobWorker, BaseJobWorkerSignals
from novel_forge.gateway.router import ModelRouter
from novel_forge.obs.logger import get_logger
from novel_forge.persistence.filesystem import FileSystemStorage, atomic_write_json
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.project_staleness import (
    RevisionScope,
    UpstreamArtifactKind,
    record_upstream_artifact_revision,
)
from novel_forge.pipeline.token_budget import calculate_route_aware_max_tokens
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.workspace.runtime import create_runtime_services

_log = get_logger("desktop.subplot_polish")

SUBPLOT_COLORS = [
    qcolor_hex("chart.1"),
    qcolor_hex("chart.2"),
    qcolor_hex("chart.3"),
    qcolor_hex("chart.4"),
    qcolor_hex("chart.5"),
    qcolor_hex("chart.6"),
    qcolor_hex("chart.7"),
    qcolor_hex("chart.8"),
]

LINK_TYPE_LABELS: dict[str, str] = {
    "trigger_start": "触发启动",
    "trigger_turn": "触发转折",
    "constrain": "约束走向",
    "enable": "提供条件",
    "conflict": "制造冲突",
    "feed_main": "反哺主线",
    "reveal_key": "揭露关键",
    "create_tension": "制造张力",
    "theme_echo": "呼应主题",
}

PRIORITY_LABELS: dict[str, str] = {
    "primary": "准主线",
    "normal": "常规",
    "background": "背景",
}

RESOLUTION_TYPE_LABELS: dict[str, str] = {
    "resolve": "问题解决",
    "reveal": "悬念揭示",
    "ascend": "价值升华",
    "merge": "并入主线",
}


def _is_event_driven_arc(arc: dict[str, Any], total_chapters: int = 0) -> bool:
    """Detect if a character arc is event-driven (suitable for subplot conversion).

    Heuristics:
    1. involved_chapters span many chapters (> 20% of total, or > 10 chapters)
    2. Milestone descriptions contain event-oriented language (specific actions,
       locations, conflicts) rather than internal psychological changes
    """
    milestones = arc.get("milestones", [])
    if not milestones:
        return False

    chapter_ranges = []
    for ms in milestones:
        if isinstance(ms, dict):
            start = ms.get("chapter_start", 0)
            end = ms.get("chapter_end", 0)
            if start > 0 and end > 0:
                chapter_ranges.append((start, end))

    if not chapter_ranges:
        return False

    min_ch = min(r[0] for r in chapter_ranges)
    max_ch = max(r[1] for r in chapter_ranges)
    span = max_ch - min_ch + 1

    span_threshold = max(10, int(total_chapters * 0.2)) if total_chapters > 0 else 10
    if span < span_threshold:
        return False

    event_keywords = [
        "发现",
        "揭露",
        "战斗",
        "对抗",
        "阴谋",
        "计划",
        "行动",
        "事件",
        "冲突",
        "危机",
        "背叛",
        "联盟",
        "争夺",
        "逃亡",
        "调查",
        "追踪",
        "伏击",
        "突袭",
        "谈判",
        "交易",
        "密谋",
        "discover",
        "battle",
        "fight",
        "conspiracy",
        "plot",
        "action",
        "conflict",
        "crisis",
        "betrayal",
        "alliance",
        "investigation",
    ]
    internal_keywords = [
        "内心",
        "成长",
        "转变",
        "觉悟",
        "领悟",
        "释怀",
        "放下",
        "挣扎",
        "迷茫",
        "坚定",
        "信念",
        "情感",
        "心理",
        "认知",
        "inner",
        "growth",
        "realize",
        "accept",
        "understand",
        "feel",
        "emotion",
        "psychological",
        "belief",
        "change",
        "mature",
    ]

    event_score = 0
    internal_score = 0
    for ms in milestones:
        desc = str(ms.get("description", "")).lower()
        for kw in event_keywords:
            if kw.lower() in desc:
                event_score += 1
        for kw in internal_keywords:
            if kw.lower() in desc:
                internal_score += 1

    return event_score >= internal_score or internal_score <= 1


class SubplotItemWidget(QWidget):
    """Collapsible subplot list item with header and detail panel."""

    edit_requested = Signal(dict)
    delete_requested = Signal(str)

    def __init__(
        self,
        subplot: dict[str, Any],
        color: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._subplot = subplot
        self._color = color
        self._expanded = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._header = self._build_header()
        layout.addWidget(self._header)

        self._detail = self._build_detail()
        self._detail.setVisible(False)
        layout.addWidget(self._detail)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setObjectName("subplotSep")
        sep.setFixedHeight(1)
        layout.addWidget(sep)

    class _ElidedLabel(QLabel):
        """Single-line label that elides overflowing text."""

        def __init__(self, text: str, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self._full_text = text
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            self.setMinimumWidth(120)
            self.setToolTip(text)
            self._apply_elide_text()

        def setText(self, text: str) -> None:
            self._full_text = text
            self.setToolTip(text)
            self._apply_elide_text()

        def resizeEvent(self, event: QResizeEvent) -> None:
            self._apply_elide_text()
            super().resizeEvent(event)

        def _apply_elide_text(self) -> None:
            width = self.contentsRect().width()
            if width <= 0:
                QLabel.setText(self, self._full_text)
                return
            metrics = QFontMetrics(self.font())
            elided = metrics.elidedText(self._full_text, Qt.TextElideMode.ElideRight, width)
            QLabel.setText(self, elided)

    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setMinimumHeight(44)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)

        self._toggle_btn = QPushButton("▶")
        self._toggle_btn.setFixedSize(20, 20)
        self._toggle_btn.setObjectName("subplotToggleBtn")
        self._toggle_btn.clicked.connect(self._toggle_expand)
        layout.addWidget(self._toggle_btn)

        indicator = QFrame()
        indicator.setFixedSize(4, 20)
        indicator.setObjectName("subplotToggleIndicator")
        indicator.setProperty("color", self._color)
        layout.addWidget(indicator)

        name = str(self._subplot.get("name", "未命名支线") or "未命名支线")
        name_label = self._ElidedLabel(name)
        name_label.setObjectName("subplotName")
        layout.addWidget(name_label, 1)

        priority = self._subplot.get("priority", "normal")
        priority_label = PRIORITY_LABELS.get(priority, priority)
        badge = QLabel(priority_label)
        badge.setObjectName("subplotPriorityBadge")
        badge.setProperty("priority", priority)
        badge.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout.addWidget(badge)

        chapters = self._subplot.get("involved_chapters", [])
        if chapters:
            ch_range = f"Ch.{min(chapters)}-{max(chapters)}"
            ch_label = QLabel(ch_range)
            ch_label.setObjectName("subplotChapters")
            ch_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            layout.addWidget(ch_label)

        layout.addStretch(1)

        edit_btn = QPushButton("编辑")
        edit_btn.setObjectName("actionButton")
        edit_btn.setProperty("variant", "secondary")
        edit_btn.setProperty("compact", True)
        edit_btn.setFixedWidth(48)
        edit_btn.clicked.connect(lambda: self.edit_requested.emit(self._subplot))
        layout.addWidget(edit_btn)

        delete_btn = QPushButton("删除")
        delete_btn.setObjectName("actionButton")
        delete_btn.setProperty("variant", "danger")
        delete_btn.setProperty("compact", True)
        delete_btn.setFixedWidth(48)
        delete_btn.clicked.connect(
            lambda: self.delete_requested.emit(self._subplot.get("name", ""))
        )
        layout.addWidget(delete_btn)

        return header

    def _build_detail(self) -> QWidget:
        detail = QWidget()
        layout = QVBoxLayout(detail)
        layout.setContentsMargins(40, 8, 16, 12)
        layout.setSpacing(8)

        desc = self._subplot.get("description", "")
        if desc:
            desc_label = QLabel(desc)
            desc_label.setWordWrap(True)
            desc_label.setObjectName("subplotDesc")
            layout.addWidget(desc_label)

        events = self._subplot.get("chapter_events", [])
        if events:
            events_label = QLabel(f"节点事件 ({len(events)} 个):")
            events_label.setObjectName("subplotEvents")
            layout.addWidget(events_label)

            for evt in events[:5]:
                ch = evt.get("chapter_number", "?")
                evt_text = evt.get("event", "")
                if evt_text:
                    evt_label = QLabel(f"  Ch.{ch}: {evt_text}")
                    evt_label.setWordWrap(True)
                    evt_label.setObjectName("subplotEventItem")
                    layout.addWidget(evt_label)

            if len(events) > 5:
                more_label = QLabel(f"  ... 及其他 {len(events) - 5} 个节点")
                more_label.setObjectName("subplotMore")
                layout.addWidget(more_label)

        weave_links = self._subplot.get("weave_links", [])
        if weave_links:
            weave_label = QLabel(f"交织关系 ({len(weave_links)} 个):")
            weave_label.setObjectName("memoryMeta")
            layout.addWidget(weave_label)

            for lk in weave_links[:3]:
                link_type = lk.get("link_type", "")
                link_label = LINK_TYPE_LABELS.get(link_type, link_type)
                target = lk.get("target_subplot", "")
                desc_text = lk.get("description", "")
                lk_text = f"  {link_label} → {target}"
                if desc_text:
                    lk_text += f": {desc_text[:60]}..."
                lk_label = QLabel(lk_text)
                lk_label.setWordWrap(True)
                lk_label.setObjectName("subplotLinkItem")
                layout.addWidget(lk_label)

        res_ch = self._subplot.get("resolution_chapter", 0)
        res_target = self._subplot.get("resolution_target", "")
        res_type = self._subplot.get("resolution_type", "")
        if res_ch > 0 or res_target or res_type:
            res_parts = []
            if res_ch > 0:
                res_parts.append(f"收束章节: Ch.{res_ch}")
            if res_type:
                res_parts.append(f"收束类型: {RESOLUTION_TYPE_LABELS.get(res_type, res_type)}")
            if res_target:
                res_parts.append(f"收束目标: {res_target}")
            res_label = QLabel(" | ".join(res_parts))
            res_label.setObjectName("subplotRes")
            layout.addWidget(res_label)

        return detail

    def _toggle_expand(self) -> None:
        self._expanded = not self._expanded
        self._detail.setVisible(self._expanded)
        self._toggle_btn.setText("▼" if self._expanded else "▶")


class SubplotDialog(QDialog):
    """Dialog for adding or editing a subplot."""

    def __init__(
        self,
        parent: QWidget | None = None,
        subplot: dict[str, Any] | None = None,
        total_chapters: int = 0,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("appDialog")
        self._subplot = subplot or {}
        self._total_chapters = total_chapters
        self._result: dict[str, Any] | None = None

        self.setWindowTitle("编辑支线" if subplot else "添加支线")
        self.setMinimumWidth(500)
        self.setMinimumHeight(400)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(8)

        self._name_edit = QLineEdit(self._subplot.get("name", ""))
        self._name_edit.setPlaceholderText("支线名称")
        form.addRow("名称:", self._name_edit)

        self._desc_edit = QTextEdit()
        self._desc_edit.setPlainText(self._subplot.get("description", ""))
        self._desc_edit.setMaximumHeight(80)
        self._desc_edit.setPlaceholderText("支线描述...")
        form.addRow("描述:", self._desc_edit)

        self._priority_combo = QComboBox()
        for key, label in PRIORITY_LABELS.items():
            self._priority_combo.addItem(label, key)
        current_priority = self._subplot.get("priority", "normal")
        idx = self._priority_combo.findData(current_priority)
        if idx >= 0:
            self._priority_combo.setCurrentIndex(idx)
        form.addRow("优先级:", self._priority_combo)

        ch_layout = QHBoxLayout()
        self._ch_start = QSpinBox()
        self._ch_start.setRange(1, max(999, total_chapters))
        self._ch_start.setValue(1)
        self._ch_end = QSpinBox()
        self._ch_end.setRange(1, max(999, total_chapters))
        self._ch_end.setValue(max(1, total_chapters))
        ch_layout.addWidget(QLabel("Ch."))
        ch_layout.addWidget(self._ch_start)
        ch_layout.addWidget(QLabel("—"))
        ch_layout.addWidget(self._ch_end)
        ch_layout.addStretch(1)

        # Pre-fill from existing subplot
        involved = self._subplot.get("involved_chapters", [])
        if involved:
            self._ch_start.setValue(min(involved))
            self._ch_end.setValue(max(involved))
        form.addRow("章节范围:", ch_layout)

        self._res_ch = QSpinBox()
        self._res_ch.setRange(0, max(999, total_chapters))
        self._res_ch.setValue(self._subplot.get("resolution_chapter", 0))
        self._res_ch.setSpecialValueText("未规划")
        form.addRow("收束章节:", self._res_ch)

        self._res_type_combo = QComboBox()
        self._res_type_combo.addItem("未指定", "")
        for key, label in RESOLUTION_TYPE_LABELS.items():
            self._res_type_combo.addItem(label, key)
        current_res_type = self._subplot.get("resolution_type", "")
        idx = self._res_type_combo.findData(current_res_type)
        if idx >= 0:
            self._res_type_combo.setCurrentIndex(idx)
        form.addRow("收束类型:", self._res_type_combo)

        self._res_target_edit = QLineEdit(self._subplot.get("resolution_target", ""))
        self._res_target_edit.setPlaceholderText("如: main_turning_point:3")
        form.addRow("收束目标:", self._res_target_edit)

        layout.addLayout(form)
        layout.addStretch(1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if ok_btn is not None:
            ok_btn.setText("保存")
            _style_message_box_button(ok_btn, variant="primary")
        if cancel_btn is not None:
            cancel_btn.setText("取消")
            _style_message_box_button(cancel_btn, variant="secondary")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        name = self._name_edit.text().strip()
        if not name:
            show_warning_message(self, "验证错误", "支线名称不能为空")
            return

        ch_start = self._ch_start.value()
        ch_end = self._ch_end.value()
        if ch_start > ch_end:
            show_warning_message(self, "验证错误", "起始章节不能大于结束章节")
            return

        involved_chapters = list(range(ch_start, ch_end + 1))

        self._result = {
            "name": name,
            "description": self._desc_edit.toPlainText().strip(),
            "priority": self._priority_combo.currentData(),
            "involved_chapters": involved_chapters,
            "resolution_chapter": self._res_ch.value(),
            "resolution_type": self._res_type_combo.currentData(),
            "resolution_target": self._res_target_edit.text().strip(),
            "chapter_events": self._subplot.get("chapter_events", []),
            "weave_links": self._subplot.get("weave_links", []),
        }
        self.accept()

    def get_result(self) -> dict[str, Any] | None:
        return self._result


class ArcConversionDialog(QDialog):
    """Dialog showing event-driven arcs that can be converted to subplots."""

    def __init__(
        self,
        arcs: list[dict[str, Any]],
        existing_subplot_names: set[str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("appDialog")
        self._arcs = arcs
        self._existing_names = existing_subplot_names
        self._selected_arcs: list[dict[str, Any]] = []

        self.setWindowTitle("角色弧光转支线")
        self.setMinimumWidth(550)
        self.setMinimumHeight(400)

        layout = QVBoxLayout(self)

        info = QLabel(
            "以下角色弧光检测到事件驱动特征，可转换为独立支线。\n"
            "选择要转换的弧光，系统将自动生成支线框架。"
        )
        info.setWordWrap(True)
        info.setObjectName("subplotInfo")
        layout.addWidget(info)

        self._arc_list = QListWidget()
        self._arc_list.setUniformItemSizes(True)
        self._arc_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)

        for arc in arcs:
            character = arc.get("character", "未知角色")
            arc_summary = arc.get("arc_summary", "")
            milestones = arc.get("milestones", [])
            ch_ranges = []
            for ms in milestones:
                if isinstance(ms, dict):
                    s = ms.get("chapter_start", 0)
                    e = ms.get("chapter_end", 0)
                    if s > 0 and e > 0:
                        ch_ranges.append((s, e))
            ch_span = ""
            if ch_ranges:
                min_ch = min(r[0] for r in ch_ranges)
                max_ch = max(r[1] for r in ch_ranges)
                ch_span = f" (Ch.{min_ch}-{max_ch})"

            item_text = f"{character}{ch_span}"
            item = QListWidgetItem(item_text)
            item.setToolTip(arc_summary)
            self._arc_list.addItem(item)

        layout.addWidget(self._arc_list)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if ok_btn is not None:
            ok_btn.setText("转换选中")
            _style_message_box_button(ok_btn, variant="primary")
        if cancel_btn is not None:
            cancel_btn.setText("取消")
            _style_message_box_button(cancel_btn, variant="secondary")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        selected_rows = self._arc_list.selectionModel().selectedRows()
        self._selected_arcs = [self._arcs[row.row()] for row in selected_rows]
        self.accept()

    def get_selected_arcs(self) -> list[dict[str, Any]]:
        return self._selected_arcs


def _arc_to_subplot(arc: dict[str, Any]) -> dict[str, Any]:
    """Convert a character arc to a subplot plan."""
    character = arc.get("character", "")
    if not character:
        character = "未知角色"
    milestones = arc.get("milestones", [])

    ch_ranges = []
    for ms in milestones:
        if isinstance(ms, dict):
            s = ms.get("chapter_start", 0)
            e = ms.get("chapter_end", 0)
            if s > 0 and e > 0:
                ch_ranges.append((s, e))

    involved_chapters = []
    if ch_ranges:
        min_ch = min(r[0] for r in ch_ranges)
        max_ch = max(r[1] for r in ch_ranges)
        involved_chapters = list(range(min_ch, max_ch + 1))

    chapter_events = []
    for ms in milestones:
        if isinstance(ms, dict):
            ch_start = ms.get("chapter_start", 0)
            if ch_start > 0:
                chapter_events.append(
                    {
                        "chapter_number": ch_start,
                        "event": ms.get("description", ""),
                        "weave_notes": "",
                        "depends_on": [],
                    }
                )

    return {
        "name": f"{character}线",
        "description": arc.get("arc_summary", ""),
        "involved_chapters": involved_chapters,
        "chapter_events": chapter_events,
        "weave_links": [],
        "priority": "normal",
        "resolution_chapter": 0,
        "resolution_target": "",
        "resolution_type": "",
    }


class SubplotManagerPanel(QWidget):
    """Panel for managing subplot plans in the narrative blueprint.

    Signals:
        data_changed: Emitted when subplot data is modified.
    """

    data_changed = Signal()

    def __init__(
        self,
        project_path: Path | str | None = None,
        *,
        auto_load: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._project_path = Path(project_path) if project_path else None
        self._blueprint: dict[str, Any] = {}
        self._total_chapters = 0
        self._auto_load = auto_load

        self._setup_ui()
        if self._project_path and self._auto_load:
            self.load_data()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        toolbar = self._build_toolbar()
        layout.addWidget(toolbar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._list_container = QWidget()
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(4)
        self._list_layout.addStretch(1)

        scroll.setWidget(self._list_container)
        layout.addWidget(scroll, 1)

        self._arc_section = self._build_arc_section()
        self._arc_section.setVisible(False)
        layout.addWidget(self._arc_section)

        self._empty_label = QLabel("暂无支线计划。点击「添加支线」创建第一条支线。")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setObjectName("subplotEmpty")
        self._empty_label.setVisible(False)
        layout.addWidget(self._empty_label)

    def _build_toolbar(self) -> QWidget:
        toolbar = QWidget()
        layout = QHBoxLayout(toolbar)
        layout.setContentsMargins(0, 0, 0, 8)
        layout.setSpacing(8)

        layout.addStretch(1)

        self._polish_btn = QPushButton("AI 润色")
        self._polish_btn.setObjectName("actionButton")
        self._polish_btn.setProperty("variant", "secondary")
        self._polish_btn.setProperty("compact", True)
        self._polish_btn.clicked.connect(self._open_polish_dialog)
        layout.addWidget(self._polish_btn)

        self._arc_convert_btn = QPushButton("弧光转支线")
        self._arc_convert_btn.setObjectName("actionButton")
        self._arc_convert_btn.setProperty("variant", "secondary")
        self._arc_convert_btn.setProperty("compact", True)
        self._arc_convert_btn.setVisible(False)
        self._arc_convert_btn.clicked.connect(self._convert_arcs)
        layout.addWidget(self._arc_convert_btn)

        add_btn = QPushButton("+ 添加支线")
        add_btn.setObjectName("actionButton")
        add_btn.setProperty("variant", "primary")
        add_btn.setProperty("compact", True)
        add_btn.clicked.connect(self._add_subplot)
        layout.addWidget(add_btn)

        refresh_btn = QPushButton("刷新")
        refresh_btn.setObjectName("actionButton")
        refresh_btn.setProperty("variant", "secondary")
        refresh_btn.setProperty("compact", True)
        refresh_btn.clicked.connect(self.refresh)
        layout.addWidget(refresh_btn)

        return toolbar

    def _build_arc_section(self) -> QWidget:
        section = QGroupBox("可转换的角色弧光")
        layout = QVBoxLayout(section)
        layout.setSpacing(4)

        self._arc_list_label = QLabel("")
        self._arc_list_label.setWordWrap(True)
        self._arc_list_label.setObjectName("subplotArcList")
        layout.addWidget(self._arc_list_label)

        return section

    def load_data(self) -> None:
        """Load subplot data from narrative_blueprint.json."""
        if not self._project_path:
            return

        blueprint_path = self._project_path / "plans" / "narrative_blueprint.json"
        if not blueprint_path.exists():
            self._blueprint = {}
            self._total_chapters = 0
            self._render_list()
            return

        try:
            with open(blueprint_path, "r", encoding="utf-8") as f:
                self._blueprint = json.load(f)
        except (json.JSONDecodeError, OSError):
            self._blueprint = {}

        self._total_chapters = self._estimate_total_chapters()
        self._render_list()
        self._check_arc_conversion()

    def save_data(self) -> None:
        """Save subplot data back to narrative_blueprint.json using atomic write."""
        if not self._project_path or not self._blueprint:
            return

        blueprint_path = self._project_path / "plans" / "narrative_blueprint.json"
        if not blueprint_path.exists():
            return

        try:
            previous_hash = hashlib.sha256(blueprint_path.read_bytes()).hexdigest()
            with open(blueprint_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError):
            existing = {}
            previous_hash = ""

        existing["subplot_plan"] = self._blueprint.get("subplot_plan", [])

        try:
            atomic_write_json(blueprint_path, existing)
            record_upstream_artifact_revision(
                FileSystemStorage(self._project_path.parent),
                ProjectLayout(self._project_path),
                artifact_kind=UpstreamArtifactKind.NARRATIVE_BLUEPRINT,
                previous_hash=previous_hash,
                scope=RevisionScope.WHOLE_BOOK,
                reason="desktop_subplot_manager_save",
            )
        except OSError as e:
            show_critical_message(self, "保存失败", f"无法保存蓝图文件:\n{e}")
        except Exception as e:  # noqa: BLE001 - keep subplot save successful
            _log.warning("subplot_revision_record_failed | error=%s", e)

    def refresh(self) -> None:
        """Reload data from disk and re-render."""
        self.load_data()

    def set_project_path(self, path: Path | str) -> None:
        """Set the project path and reload data."""
        self._project_path = Path(path)
        self.load_data()

    def shutdown(self) -> None:
        """Tear down sub-widgets on app exit.

        Thread-pool draining is handled globally by
        ``shutdown_desktop_thread_pools()`` in ``_pre_close_cleanup()``.
        """

    def set_blueprint_data(self, data: dict[str, Any]) -> None:
        """Set blueprint data directly (for integration with other components)."""
        self._blueprint = data
        self._total_chapters = self._estimate_total_chapters()
        self._render_list()
        self._check_arc_conversion()

    def get_blueprint_data(self) -> dict[str, Any]:
        """Return current blueprint data."""
        return self._blueprint

    def get_subplots(self) -> list[dict[str, Any]]:
        """Return current subplot list."""
        subplots = self._blueprint.get("subplot_plan", [])
        if not isinstance(subplots, list):
            self._blueprint["subplot_plan"] = []
            return []
        return subplots

    def _estimate_total_chapters(self) -> int:
        chapters = self._blueprint.get("chapters", [])
        if chapters:
            return len(chapters)

        phases = self._blueprint.get("narrative_phases", [])
        max_ch = 0
        for ph in phases:
            if isinstance(ph, dict):
                end = ph.get("chapter_end", 0) or ph.get("end_chapter", 0)
                if end > max_ch:
                    max_ch = end
        if max_ch > 0:
            return max_ch

        subplots = self._blueprint.get("subplot_plan", [])
        for sp in subplots:
            if isinstance(sp, dict):
                chs = sp.get("involved_chapters", [])
                if chs:
                    max_ch = max(max_ch, max(chs))
        if max_ch > 0:
            return max_ch

        return 0

    def _render_list(self) -> None:
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                # Detach immediately to avoid transient overlap of stale widgets
                # when a second render happens in the same event-loop turn.
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

        subplots = self._blueprint.get("subplot_plan", [])
        if not subplots:
            self._empty_label.setVisible(True)
            return

        self._empty_label.setVisible(False)

        for idx, sp in enumerate(subplots):
            if not isinstance(sp, dict):
                continue
            color = SUBPLOT_COLORS[idx % len(SUBPLOT_COLORS)]
            widget = SubplotItemWidget(sp, color)
            widget.edit_requested.connect(self._edit_subplot)
            widget.delete_requested.connect(self._delete_subplot)
            self._list_layout.insertWidget(self._list_layout.count() - 1, widget)

    def _check_arc_conversion(self) -> None:
        arcs = self._blueprint.get("character_arcs", [])
        if not arcs:
            self._arc_convert_btn.setVisible(False)
            self._arc_section.setVisible(False)
            return

        existing_names = {
            sp.get("name", "")
            for sp in self._blueprint.get("subplot_plan", [])
            if isinstance(sp, dict)
        }

        event_driven = [
            arc
            for arc in arcs
            if isinstance(arc, dict) and _is_event_driven_arc(arc, self._total_chapters)
        ]

        convertible = [
            arc for arc in event_driven if f"{arc.get('character', '')}线" not in existing_names
        ]

        if convertible:
            self._arc_convert_btn.setVisible(True)
            self._arc_convert_btn.setText(f"弧光转支线 ({len(convertible)})")
            names = ", ".join(a.get("character", "") for a in convertible)
            self._arc_list_label.setText(f"检测到 {len(convertible)} 个事件驱动型弧光: {names}")
        else:
            self._arc_convert_btn.setVisible(False)
            self._arc_section.setVisible(False)

    def _add_subplot(self) -> None:
        dialog = SubplotDialog(self, total_chapters=self._total_chapters)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            result = dialog.get_result()
            if result:
                subplots = self._blueprint.setdefault("subplot_plan", [])
                subplots.append(result)
                self._render_list()
                self.save_data()
                self.data_changed.emit()

    def _edit_subplot(self, subplot: dict[str, Any]) -> None:
        dialog = SubplotDialog(self, subplot=subplot, total_chapters=self._total_chapters)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            result = dialog.get_result()
            if result:
                old_name = subplot.get("name", "")
                new_name = result.get("name", "")
                if old_name != new_name or result != subplot:
                    confirm = ask_confirmation(
                        self,
                        "确认保存修改",
                        f"确定要保存支线「{old_name}」的修改吗？",
                        confirm_text="保存",
                        confirm_variant="primary",
                    )
                    if not confirm:
                        return

                subplots = self._blueprint.get("subplot_plan", [])
                for i, sp in enumerate(subplots):
                    if sp.get("name") == old_name:
                        subplots[i] = result
                        break
                self._render_list()
                self.save_data()
                self.data_changed.emit()

    def _delete_subplot(self, name: str) -> None:
        if not name:
            return

        result = show_message_box(
            self,
            "确认删除",
            f"确定要删除支线「{name}」吗？",
            informative_text="此操作不可撤销。支线的所有节点事件和交织关系将被移除。",
            icon=QMessageBox.Icon.Warning,
            actions=(
                MessageBoxAction(
                    "confirm", "删除", QMessageBox.ButtonRole.AcceptRole, "danger", True
                ),
                MessageBoxAction("cancel", "取消", QMessageBox.ButtonRole.RejectRole, "secondary"),
            ),
        )

        if result == "confirm":
            subplots = self._blueprint.get("subplot_plan", [])
            self._blueprint["subplot_plan"] = [
                sp for sp in subplots if isinstance(sp, dict) and sp.get("name") != name
            ]
            self._render_list()
            self.save_data()
            self.data_changed.emit()

    def _open_polish_dialog(self) -> None:
        subplots = self._blueprint.get("subplot_plan", [])
        if not subplots:
            show_message_box(
                self,
                "提示",
                "暂无支线可润色。请先添加支线。",
                icon=QMessageBox.Icon.Information,
            )
            return

        dialog = SubplotPolishDialog(subplots, self._blueprint, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            result = dialog.get_result()
            if result:
                self._apply_polish_result(result)

    def _apply_polish_result(self, polished: list[dict[str, Any]]) -> None:
        """Apply polished subplot data back to the blueprint."""
        subplots = self._blueprint.get("subplot_plan", [])
        polished_by_name = {sp.get("name", ""): sp for sp in polished}

        updated_count = 0
        for i, sp in enumerate(subplots):
            name = sp.get("name", "")
            if name in polished_by_name:
                subplots[i] = polished_by_name[name]
                updated_count += 1

        self._render_list()
        self.save_data()
        self.data_changed.emit()

        show_message_box(
            self,
            "润色完成",
            f"已成功更新 {updated_count} 条支线。",
            icon=QMessageBox.Icon.Information,
        )

    def _convert_arcs(self) -> None:
        arcs = self._blueprint.get("character_arcs", [])
        existing_names = {
            sp.get("name", "")
            for sp in self._blueprint.get("subplot_plan", [])
            if isinstance(sp, dict)
        }

        event_driven = [
            arc
            for arc in arcs
            if isinstance(arc, dict) and _is_event_driven_arc(arc, self._total_chapters)
        ]
        convertible = [
            arc for arc in event_driven if f"{arc.get('character', '')}线" not in existing_names
        ]

        if not convertible:
            show_message_box(
                self,
                "无可转换弧光",
                "当前没有可转换为支线的事件驱动型角色弧光。",
                icon=QMessageBox.Icon.Information,
            )
            return

        dialog = ArcConversionDialog(convertible, existing_names, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            selected = dialog.get_selected_arcs()
            if selected:
                confirm = ask_confirmation(
                    self,
                    "确认转换",
                    f"确定要将 {len(selected)} 个角色弧光转换为支线吗？",
                    informative_text="转换后弧光数据将保留，同时生成对应的支线计划。",
                    confirm_text="转换",
                    confirm_variant="primary",
                )
                if not confirm:
                    return

                subplots = self._blueprint.setdefault("subplot_plan", [])
                for arc in selected:
                    new_subplot = _arc_to_subplot(arc)
                    existing = {sp.get("name", "") for sp in subplots}
                    if new_subplot["name"] in existing:
                        new_subplot["name"] = f"{new_subplot['name']}_2"
                    subplots.append(new_subplot)

                self._render_list()
                self.save_data()
                self.data_changed.emit()
                self._check_arc_conversion()

                converted_names = ", ".join(a.get("character", "") for a in selected)
                show_message_box(
                    self,
                    "转换完成",
                    f"已成功转换 {len(selected)} 个角色弧光为支线:\n{converted_names}",
                    icon=QMessageBox.Icon.Information,
                )


# ═══════════════════════════════════════════════════════════════════
# AI Subplot Polish — background worker + multi-select dialog
# ═══════════════════════════════════════════════════════════════════


class _PolishWorkerSignals(BaseJobWorkerSignals):
    started = Signal()
    finished = Signal(list)  # list of polished subplot dicts
    failed = Signal(str)  # error message


class _SubplotPolishWorker(BaseJobWorker):
    """Background worker that calls the LLM to polish subplots."""

    pool = "aux"

    def __init__(
        self,
        subplots: list[dict[str, Any]],
        blueprint: dict[str, Any],
        all_subplot_names: list[str],
    ) -> None:
        super().__init__()
        self._subplots = subplots
        self._blueprint = blueprint
        self._all_subplot_names = all_subplot_names
        self.signals = _PolishWorkerSignals()

    async def _run_async(self) -> None:
        self.signals.started.emit()
        try:
            result = await self._call_llm()
            self.signals.finished.emit(result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log.error("Subplot polish failed: %s", exc)
            self.signals.failed.emit(str(exc))

    async def _call_llm(self) -> list[dict[str, Any]]:
        runtime = create_runtime_services()
        try:
            router: ModelRouter = runtime.router
            builder: PromptBuilder = runtime.builder

            selected_names = {sp.get("name", "") for sp in self._subplots}
            context = {
                "subplots": self._subplots,
                "blueprint": self._blueprint,
                "all_subplots": self._blueprint.get("subplot_plan", []),
                "selected_names": list(selected_names),
            }

            request = builder.build(
                TaskType.POLISH_SUBPLOT,
                context,
                max_tokens=calculate_route_aware_max_tokens(
                    router,
                    TaskType.POLISH_SUBPLOT,
                    max(3600, len(self._subplots) * 700),
                    prompt_overhead=3000,
                    min_tokens=4096,
                ),
                temperature=0.7,
            )

            response = await router.route(request)
            raw = safe_parse_json(response.content)

            if isinstance(raw, list):
                return [item for item in raw if isinstance(item, dict)]
            if isinstance(raw, dict):
                subplots = raw.get("subplots")
                if isinstance(subplots, list):
                    return [item for item in subplots if isinstance(item, dict)]
                return [raw]
            raise ValueError(f"LLM 返回的不是 JSON 数组或对象: {type(raw).__name__}")
        finally:
            await runtime.shutdown()


class SubplotPolishDialog(QDialog):
    """Dialog for selecting subplots to polish and displaying progress/results."""

    def __init__(
        self,
        subplots: list[dict[str, Any]],
        blueprint: dict[str, Any],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("appDialog")
        self.setWindowTitle("AI 润色支线")
        self.setMinimumWidth(480)
        self.setMinimumHeight(400)

        self._subplots = subplots
        self._blueprint = blueprint
        self._result: list[dict[str, Any]] | None = None
        self._worker: _SubplotPolishWorker | None = None

        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Selection area
        select_label = QLabel("选择要润色的支线（可多选）：")
        select_label.setObjectName("subplotSelectLabel")
        layout.addWidget(select_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMaximumHeight(200)

        self._check_container = QWidget()
        self._check_layout = QVBoxLayout(self._check_container)
        self._check_layout.setContentsMargins(8, 4, 8, 4)
        self._check_layout.setSpacing(4)

        self._checks: list[tuple[QCheckBox, dict[str, Any]]] = []
        for sp in self._subplots:
            cb = QCheckBox(sp.get("name", "未命名支线"))
            cb.setChecked(True)
            cb.setObjectName("subplotCheck")
            self._check_layout.addWidget(cb)
            self._checks.append((cb, sp))

        self._check_layout.addStretch()
        scroll.setWidget(self._check_container)
        layout.addWidget(scroll)

        # Select all / none
        select_row = QHBoxLayout()
        select_row.setSpacing(8)
        select_all_btn = QPushButton("全选")
        select_all_btn.setObjectName("actionButton")
        select_all_btn.setProperty("variant", "secondary")
        select_all_btn.setProperty("compact", True)
        select_all_btn.clicked.connect(self._select_all)
        select_row.addWidget(select_all_btn)

        select_none_btn = QPushButton("全不选")
        select_none_btn.setObjectName("actionButton")
        select_none_btn.setProperty("variant", "secondary")
        select_none_btn.setProperty("compact", True)
        select_none_btn.clicked.connect(self._select_none)
        select_row.addWidget(select_none_btn)
        select_row.addStretch()
        layout.addLayout(select_row)

        # Progress area (hidden initially)
        self._progress_area = QWidget()
        progress_layout = QVBoxLayout(self._progress_area)
        progress_layout.setContentsMargins(0, 0, 0, 0)

        self._progress_label = QLabel("正在调用 AI 润色支线...")
        self._progress_label.setObjectName("subplotProgress")
        progress_layout.addWidget(self._progress_label)

        layout.addWidget(self._progress_area)
        self._progress_area.setVisible(False)

        # Result area (hidden initially)
        self._result_area = QWidget()
        result_layout = QVBoxLayout(self._result_area)
        result_layout.setContentsMargins(0, 0, 0, 0)

        self._result_label = QLabel("")
        self._result_label.setWordWrap(True)
        self._result_label.setObjectName("subplotResult")
        result_layout.addWidget(self._result_label)

        layout.addWidget(self._result_area)
        self._result_area.setVisible(False)

        # Buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok_btn = button_box.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_btn.setText("开始润色")
        _style_message_box_button(self._ok_btn, variant="primary")
        cancel_btn = button_box.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn is not None:
            cancel_btn.setText("取消")
            _style_message_box_button(cancel_btn, variant="secondary")
        self._ok_btn.clicked.connect(self._start_polish)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _select_all(self) -> None:
        for cb, _ in self._checks:
            cb.setChecked(True)

    def _select_none(self) -> None:
        for cb, _ in self._checks:
            cb.setChecked(False)

    def _get_selected(self) -> list[dict[str, Any]]:
        return [sp for cb, sp in self._checks if cb.isChecked()]

    def _start_polish(self) -> None:
        selected = self._get_selected()
        if not selected:
            show_message_box(
                self,
                "提示",
                "请至少选择一条支线进行润色。",
                icon=QMessageBox.Icon.Warning,
            )
            return

        # Disable UI
        self._ok_btn.setEnabled(False)
        self._ok_btn.setText("润色中...")
        for cb, _ in self._checks:
            cb.setEnabled(False)
        self._progress_area.setVisible(True)
        self._result_area.setVisible(False)

        # Start worker
        all_names = [sp.get("name", "") for sp in self._subplots]
        self._worker = _SubplotPolishWorker(selected, self._blueprint, all_names)
        self._worker.signals.started.connect(self._on_started)
        self._worker.signals.finished.connect(self._on_finished)
        self._worker.signals.failed.connect(self._on_failed)

        desktop_thread_pools().aux_pool.start(self._worker)

    def _on_started(self) -> None:
        self._progress_label.setText("正在调用 AI 润色支线，请稍候...")

    def _on_finished(self, result: list[dict[str, Any]]) -> None:
        self._result = result
        self._progress_area.setVisible(False)
        self._result_area.setVisible(True)

        count = len(result)
        names = ", ".join(sp.get("name", "") for sp in result)
        self._result_label.setText(f"✅ 润色完成！共 {count} 条支线：{names}")

        self._ok_btn.setEnabled(True)
        self._ok_btn.setText("应用结果")
        self._ok_btn.clicked.disconnect()
        self._ok_btn.clicked.connect(self.accept)

        for cb, _ in self._checks:
            cb.setEnabled(True)

    def _on_failed(self, error: str) -> None:
        self._progress_area.setVisible(False)
        show_message_box(
            self,
            "润色失败",
            f"AI 润色过程中发生错误：\n{error}",
            icon=QMessageBox.Icon.Critical,
        )
        self._ok_btn.setEnabled(True)
        self._ok_btn.setText("开始润色")
        for cb, _ in self._checks:
            cb.setEnabled(True)

    def get_result(self) -> list[dict[str, Any]] | None:
        return self._result
