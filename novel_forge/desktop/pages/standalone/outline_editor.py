from __future__ import annotations

import difflib
import hashlib
import html
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from novel_forge.desktop.components import (
    ActionButton,
    Badge,
    MessageBoxAction,
    show_message_box,
)
from novel_forge.desktop.pages._page_utils import safe_disconnect
from novel_forge.desktop.pages.document_renderer.incremental import IncrementalDocumentRenderer
from novel_forge.desktop.pages.workflow.workers import (
    OutlinePolishWorker,
    SemanticResolveWorker,
)
from novel_forge.desktop.theme import qcolor_hex, qcolor_rgba
from novel_forge.desktop.thread_pools import desktop_thread_pools
from novel_forge.persistence.filesystem import FileSystemStorage, atomic_write_text
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.project_staleness import (
    RevisionScope,
    UpstreamArtifactKind,
    record_upstream_artifact_revision,
)


def _inline_diff_html(old: str, new: str) -> str:
    sm = difflib.SequenceMatcher(None, str(old), str(new))
    _del_bg = f"background:{qcolor_rgba('status.danger', 0.10)};"
    _ins_bg = f"background:{qcolor_rgba('status.success', 0.12)};"
    _ins_color = f"color:{qcolor_hex('status.success.deep')};"
    parts: list[str] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            parts.append(html.escape(str(old)[i1:i2], quote=True))
        elif tag == "delete":
            parts.append(
                f'<span style="{_del_bg}'
                'text-decoration:line-through;">'
                f"{html.escape(str(old)[i1:i2], quote=True)}</span>"
            )
        elif tag == "insert":
            parts.append(
                f'<span style="{_ins_bg}'
                f'{_ins_color}font-weight:500;">'
                f"{html.escape(str(new)[j1:j2], quote=True)}</span>"
            )
        elif tag == "replace":
            parts.append(
                f'<span style="{_del_bg}'
                'text-decoration:line-through;">'
                f"{html.escape(str(old)[i1:i2], quote=True)}</span>"
            )
            parts.append(
                f'<span style="{_ins_bg}'
                f'{_ins_color}font-weight:500;">'
                f"{html.escape(str(new)[j1:j2], quote=True)}</span>"
            )
    return "".join(parts)


def _extract_field_tag(text: str) -> str:
    if "goal" in text.lower():
        return "目标"
    if "beats" in text.lower():
        return "节奏"
    if "main_plot" in text.lower():
        return "主线"
    if "subplot" in text.lower():
        return "支线"
    if "hook" in text.lower():
        return "钩子"
    if "expected" in text.lower():
        return "预期"
    if "notes" in text.lower():
        return "备注"
    return "建议"


def _format_diff_value(value: object) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")  # type: ignore[call-arg]
    if isinstance(value, list):
        return "；".join(_format_diff_value(item) for item in value if str(item).strip())
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value or "")


def _field_label(field: str) -> str:
    return {
        "title": "章节标题",
        "goal": "章节目标",
        "beats_summary": "节奏梗概",
        "main_plot_points": "主线推进",
        "subplot_points": "支线推进",
        "subplot_focus": "支线焦点",
        "element_focus": "要素焦点",
        "pov_character": "视角人物",
        "setting": "场景地点",
        "expected_hook": "章尾钩子",
        "expected_payoffs": "预期兑现",
        "involved_characters": "出场人物",
        "notes": "章节备注",
    }.get(field, field)


_DEFAULT_POLISH_FIELDS = ("goal", "beats_summary", "main_plot_points")
_TITLE_ONLY_FIELDS = ("title",)
_DIFF_PREVIEW_FIELDS = (
    "title",
    "goal",
    "beats_summary",
    "main_plot_points",
    "subplot_points",
    "subplot_focus",
    "element_focus",
    "pov_character",
    "setting",
    "expected_hook",
    "expected_payoffs",
    "involved_characters",
    "notes",
)
_FOCUS_FIELD_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("title", ("标题", "题名", "章名", "命名", "小标题", "次级标题")),
    ("expected_hook", ("钩子", "章尾", "悬念", "追读", "爽点")),
    ("expected_payoffs", ("兑现", "回收", "伏笔", "铺垫")),
    ("subplot_points", ("支线", "副线", "感情线", "事业线")),
    ("subplot_focus", ("支线焦点", "副线焦点")),
    ("beats_summary", ("节奏", "节拍", "起承转合", "转折")),
    ("main_plot_points", ("主线", "主情节", "主剧情", "剧情线")),
    ("goal", ("目标", "概要", "梗概", "作用")),
    ("pov_character", ("视角", "pov", "叙述者")),
    ("setting", ("场景", "地点", "舞台", "空间")),
    ("involved_characters", ("人物", "角色", "出场")),
    ("element_focus", ("要素", "题材元素", "看点")),
    ("notes", ("备注", "约束", "说明")),
)


def _polish_focus_fields_for_text(
    hint: str,
    selected_suggestions: list[str] | None = None,
) -> list[str]:
    """Infer which ChapterOutline fields should be editable for this polish pass."""
    text = " ".join([hint, *(selected_suggestions or [])]).lower()
    matched: list[str] = []
    for field, keywords in _FOCUS_FIELD_KEYWORDS:
        if any(keyword.lower() in text for keyword in keywords):
            matched.append(field)

    if not matched:
        return list(_DEFAULT_POLISH_FIELDS)
    if matched == list(_TITLE_ONLY_FIELDS):
        return list(_TITLE_ONLY_FIELDS)

    fields: list[str] = []
    for field in (*_DEFAULT_POLISH_FIELDS, *matched):
        if field not in fields:
            fields.append(field)
    return fields


class SuggestionCard(QFrame):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = text
        self.setObjectName("suggestionCard")
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(8)

        self._checkbox = QCheckBox()
        self._checkbox.setChecked(True)
        self._checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        top.addWidget(self._checkbox, 0)

        tag = _extract_field_tag(self._text)
        tag_lbl = QLabel(tag)
        tag_lbl.setObjectName("suggestionTag")
        tag_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tag_lbl.setFixedHeight(20)
        top.addWidget(tag_lbl, 0)

        top.addStretch()
        layout.addLayout(top)

        lbl = QLabel(self._text)
        lbl.setObjectName("suggestionBody")
        lbl.setWordWrap(True)
        font = lbl.font()
        font.setPointSize(9)
        lbl.setFont(font)
        layout.addWidget(lbl)

        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        if self._checkbox.geometry().contains(pos):
            QFrame.mousePressEvent(self, event)
            return
        self._checkbox.toggle()

    def is_checked(self) -> bool:
        return self._checkbox.isChecked()

    def text(self) -> str:
        return self._text


class _OutlineChapterList(QListWidget):
    toggle_requested = Signal(QListWidgetItem)

    _TEXT_TOGGLE_X_OFFSET = 28

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        item = self.itemAt(
            event.position().toPoint() if hasattr(event, "position") else event.pos()
        )
        super().mouseReleaseEvent(event)
        if item is None or event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        item_rect = self.visualItemRect(item)
        if pos.x() >= item_rect.left() + self._TEXT_TOGGLE_X_OFFSET:
            self.toggle_requested.emit(item)


class ChapterOverviewCard(QFrame):
    def __init__(self, chapter: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._chapter = chapter
        self.setObjectName("chapterOverviewCard")
        self._build_ui()

    def _build_ui(self) -> None:
        num = self._chapter.get("chapter_number", 0)
        title = self._chapter.get("title", "未命名")
        goal = str(self._chapter.get("goal", "") or "")
        goal_short = goal[:48] + "…" if len(goal) > 48 else goal

        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 6, 9, 6)
        layout.setSpacing(2)

        top = QHBoxLayout()
        top.setSpacing(6)
        num_lbl = QLabel(f"第 {num} 章")
        num_lbl.setObjectName("overviewNum")
        top.addWidget(num_lbl)
        title_lbl = QLabel(title)
        title_lbl.setObjectName("overviewTitle")
        top.addWidget(title_lbl)
        top.addStretch()
        layout.addLayout(top)

        if goal_short:
            goal_lbl = QLabel(goal_short)
            goal_lbl.setObjectName("overviewGoal")
            goal_lbl.setWordWrap(True)
            layout.addWidget(goal_lbl)

        self.setFixedSize(170, 72)


class _ExtendOutlineDialog(QDialog):
    def __init__(
        self, *, project_id: str, current_total: int, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._project_id = project_id
        self._current_total = max(1, int(current_total or 1))
        self.setWindowTitle("延长全书")
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        title = QLabel(f"当前总章数：{self._current_total}")
        title.setObjectName("extendDialogTitle")
        layout.addWidget(title)

        self._additional_radio = QRadioButton("追加 N 章")
        self._target_radio = QRadioButton("指定总章数")
        self._additional_radio.setChecked(True)

        self._additional_spin = QSpinBox()
        self._additional_spin.setRange(1, max(1, 10000 - self._current_total))
        self._additional_spin.setValue(5)
        self._additional_spin.setFixedWidth(92)

        self._target_spin = QSpinBox()
        self._target_spin.setRange(self._current_total + 1, 10000)
        self._target_spin.setValue(min(10000, self._current_total + 5))
        self._target_spin.setFixedWidth(92)

        add_row = QHBoxLayout()
        add_row.addWidget(self._additional_radio)
        add_row.addWidget(self._additional_spin)
        add_row.addStretch()
        layout.addLayout(add_row)

        target_row = QHBoxLayout()
        target_row.addWidget(self._target_radio)
        target_row.addWidget(self._target_spin)
        target_row.addStretch()
        layout.addLayout(target_row)

        self._decommission_checkbox = QCheckBox("旧末章改为过渡章")
        self._decommission_checkbox.setChecked(True)
        layout.addWidget(self._decommission_checkbox)

        self._summary = QLabel("")
        self._summary.setWordWrap(True)
        self._summary.setObjectName("extendDialogSummary")
        layout.addWidget(self._summary)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._additional_radio.toggled.connect(self._refresh_summary)
        self._target_radio.toggled.connect(self._refresh_summary)
        self._additional_spin.valueChanged.connect(self._refresh_summary)
        self._target_spin.valueChanged.connect(self._refresh_summary)
        self._decommission_checkbox.toggled.connect(self._refresh_summary)
        self._refresh_summary()

    def _target_total(self) -> int:
        if self._target_radio.isChecked():
            return int(self._target_spin.value())
        return self._current_total + int(self._additional_spin.value())

    def _refresh_summary(self) -> None:
        target = self._target_total()
        self._additional_spin.setEnabled(self._additional_radio.isChecked())
        self._target_spin.setEnabled(self._target_radio.isChecked())
        first_new = self._current_total + 1
        old_final = self._current_total
        new_range = f"{first_new}-{target}" if first_new < target else str(target)
        transition = "会" if self._decommission_checkbox.isChecked() else "不会"
        self._summary.setText(
            f"目标总章数：{target}\n"
            f"新增章节：第 {new_range} 章\n"
            f"旧末章：第 {old_final} 章{transition}标记为过渡章\n"
            "提交后会生成新增章节大纲并同步章节契约，不修改既有正文。"
        )

    def to_request(self) -> Any:
        from novel_forge.workspace.contracts import ExtendOutlineRequest

        if self._target_radio.isChecked():
            return ExtendOutlineRequest(
                project_id=self._project_id,
                target_total=self._target_total(),
                decommission_old_ending=self._decommission_checkbox.isChecked(),
                sync_contracts=True,
            )
        return ExtendOutlineRequest(
            project_id=self._project_id,
            additional_chapters=int(self._additional_spin.value()),
            decommission_old_ending=self._decommission_checkbox.isChecked(),
            sync_contracts=True,
        )


class InteractiveOutlineWidget(QWidget):
    # Emitted when the user clicks the "同步契约" button in the polish toolbar.
    # Carries a fully-formed ``SyncChapterContractsRequest`` plus the cached
    # outline fingerprint (so the runner can resume from the last sync point).
    sync_chapter_contracts_requested = Signal(object)
    extend_outline_requested = Signal(object)

    def __init__(
        self,
        outline_data: dict[str, Any],
        outline_path: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._outline_data = outline_data
        self._saved_outline_data = deepcopy(outline_data)
        self._outline_path = outline_path
        raw_chapters = outline_data.get("chapters", [])
        self._chapters: list[dict[str, Any]] = (
            [dict(chapter) for chapter in raw_chapters if isinstance(chapter, dict)]
            if isinstance(raw_chapters, list)
            else []
        )
        self._session_path = outline_path.parent / "reports" / "polish_session.json"
        self._mode = "read"
        self._polish_suggestions: list[str] = []
        self._original_chapters: dict[int, dict[str, Any]] = {}
        self._all_chapter_items: list[tuple[int, str, dict[str, Any]]] = []
        self._pending_outline_data: dict[str, Any] | None = None
        self._pending_polish_result: object | None = None
        self._polish_in_progress = False
        self._semantic_resolving: bool = False
        self._loading_session: bool = False
        self._build_ui()
        self._load_session()
        self._render_read_view()
        self._set_mode(self._mode)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._toolbar = QWidget()
        toolbar = self._toolbar
        toolbar.setObjectName("outlineToolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(10, 5, 10, 4)
        toolbar_layout.setSpacing(6)
        toolbar.setFixedHeight(40)

        self._read_btn = ActionButton("阅读", variant="secondary")
        self._read_btn.setToolTip("阅读模式")
        self._read_btn.setCheckable(True)
        self._read_btn.setChecked(True)
        self._read_btn.clicked.connect(lambda: self._set_mode("read"))

        self._polish_btn = ActionButton("润色", variant="secondary")
        self._polish_btn.setToolTip("润色模式")
        self._polish_btn.setCheckable(True)
        self._polish_btn.clicked.connect(lambda: self._set_mode("polish"))

        toolbar_layout.addWidget(self._read_btn)
        toolbar_layout.addWidget(self._polish_btn)
        toolbar_layout.addSpacing(8)

        self._extend_btn = ActionButton("延长全书", variant="quiet")
        self._extend_btn.setToolTip("在当前大纲末尾追加章节并同步章节契约")
        self._extend_btn.clicked.connect(self._on_extend_outline_clicked)
        toolbar_layout.addWidget(self._extend_btn)

        self._polish_controls = QFrame()
        self._polish_controls.setObjectName("polishCommandPanel")
        pc_layout = QVBoxLayout(self._polish_controls)
        pc_layout.setContentsMargins(10, 8, 10, 8)
        pc_layout.setSpacing(7)

        self._hint_input = QLineEdit()
        self._hint_input.setObjectName("outlineHintInput")
        self._hint_input.setPlaceholderText("输入润色方向，如：重命名第12、21、46章，强化章尾钩子")
        self._hint_input.setFixedHeight(24)
        self._hint_input.textChanged.connect(self._on_hint_changed)

        self._analyze_btn = ActionButton("分析建议", variant="primary")
        self._analyze_btn.clicked.connect(self._on_analyze)

        self._exec_btn = ActionButton("执行润色", variant="primary")
        self._exec_btn.setEnabled(False)
        self._exec_btn.clicked.connect(self._on_execute)

        self._save_btn = ActionButton("应用并保存", variant="quiet")
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._on_save)

        self._discard_btn = ActionButton("放弃修改", variant="quiet")
        self._discard_btn.setEnabled(False)
        self._discard_btn.clicked.connect(self._discard_outline_changes)

        self._history_btn = ActionButton("历史", variant="quiet")
        self._history_btn.clicked.connect(self._on_show_history)

        self._sync_btn = ActionButton("同步契约", variant="quiet")
        self._sync_btn.setToolTip("根据当前 outline 局部刷新章节契约(不修改 prose)")
        self._sync_btn.setEnabled(False)
        self._sync_btn.clicked.connect(self._on_sync_contracts_clicked)

        compact_button_style = f"""
            QPushButton#actionButton {{
                font-size: 12px;
                padding: 2px 9px;
                border-radius: 7px;
                font-weight: 700;
            }}
            QPushButton#actionButton[variant="primary"] {{
                background: {qcolor_rgba('accent.primary', 0.10)};
                border: 1px solid {qcolor_rgba('accent.primary', 0.46)};
                color: {qcolor_hex('accent.deep')};
            }}
            QPushButton#actionButton[variant="primary"]:hover {{
                background: {qcolor_rgba('accent.primary', 0.16)};
                border-color: {qcolor_rgba('accent.primary', 0.62)};
                color: {qcolor_hex('accent.primary.pressed')};
            }}
            QPushButton#actionButton[variant="primary"]:pressed {{
                background: {qcolor_rgba('accent.primary', 0.22)};
                color: {qcolor_hex('accent.primary.pressed')};
            }}
            QPushButton#actionButton[variant="secondary"] {{
                background: {qcolor_rgba('bg.surface', 0.92)};
                border: 1px solid {qcolor_rgba('text.body', 0.20)};
                color: {qcolor_hex('text.body.warm')};
            }}
            QPushButton#actionButton[variant="secondary"]:checked {{
                background: {qcolor_rgba('accent.primary', 0.12)};
                border-color: {qcolor_rgba('accent.primary', 0.38)};
                color: {qcolor_hex('accent.deep')};
            }}
            QPushButton#actionButton[variant="quiet"] {{
                background: {qcolor_hex('bg.surface')};
                border: 1px solid {qcolor_rgba('text.body', 0.18)};
                color: {qcolor_hex('text.quiet')};
            }}
            QPushButton#actionButton:disabled {{
                background: {qcolor_rgba('bg.panel', 0.68)};
                border: 1px solid {qcolor_rgba('border.default', 0.18)};
                color: {qcolor_hex('text.disabled')};
            }}
        """
        mode_tab_style = (
            compact_button_style
            + f"""
            QPushButton#actionButton[outlineModeTab="true"] {{
                background: {qcolor_rgba('bg.outline.tab', 0.46)};
                border: 1px solid {qcolor_rgba('border.default', 0.12)};
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                border-bottom-left-radius: 0px;
                border-bottom-right-radius: 0px;
                color: {qcolor_hex('text.tab.alt')};
                min-width: 58px;
                padding: 3px 9px;
                text-align: center;
            }}
            QPushButton#actionButton[outlineModeTab="true"]:checked {{
                background: {qcolor_rgba('bg.input.soft', 0.95)};
                border-color: {qcolor_rgba('border.default', 0.18)};
                color: {qcolor_hex('text.heading')};
            }}
            QPushButton#actionButton[outlineModeTab="true"]:hover:!checked {{
                background: {qcolor_rgba('bg.hover.accent', 0.94)};
                color: {qcolor_hex('text.tab.hover')};
            }}
        """
        )
        for button in (self._read_btn, self._polish_btn):
            button.setProperty("outlineModeTab", "true")
        for button in (
            self._read_btn,
            self._polish_btn,
            self._analyze_btn,
            self._exec_btn,
            self._save_btn,
            self._discard_btn,
            self._history_btn,
            self._sync_btn,
            self._extend_btn,
        ):
            button.setFixedHeight(26)
            button.setStyleSheet(compact_button_style)
        for button in (self._read_btn, self._polish_btn):
            button.setFixedWidth(66)
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            button.setStyleSheet(mode_tab_style)
            toolbar_layout.setAlignment(button, Qt.AlignmentFlag.AlignBottom)

        self._selection_count_label = QLabel("")
        self._selection_count_label.setMinimumWidth(180)
        self._selection_count_label.setStyleSheet(
            f"QLabel {{ font-size: 12px; color: {qcolor_hex('text.chapter.rail')}; padding: 0 4px; font-weight: 600; }}"
        )

        scope_label = QLabel("润色范围")
        scope_label.setStyleSheet(
            f"QLabel {{ font-size: 12px; color: {qcolor_hex('text.secondary')}; font-weight: 700; }}"
        )
        scope_row = QHBoxLayout()
        scope_row.setContentsMargins(0, 0, 0, 0)
        scope_row.setSpacing(8)
        scope_row.addWidget(scope_label)
        scope_row.addWidget(self._selection_count_label, 1)
        scope_row.addStretch()
        scope_row.addWidget(self._save_btn)
        scope_row.addWidget(self._discard_btn)
        scope_row.addWidget(self._history_btn)
        scope_row.addWidget(self._sync_btn)
        pc_layout.addLayout(scope_row)

        direction_label = QLabel("润色方向")
        direction_label.setStyleSheet(
            f"QLabel {{ font-size: 12px; color: {qcolor_hex('text.secondary')}; font-weight: 700; }}"
        )
        direction_row = QHBoxLayout()
        direction_row.setContentsMargins(0, 0, 0, 0)
        direction_row.setSpacing(8)
        direction_row.addWidget(direction_label)
        direction_row.addWidget(self._hint_input, 1)
        direction_row.addWidget(self._analyze_btn)
        direction_row.addWidget(self._exec_btn)
        pc_layout.addLayout(direction_row)

        toolbar_layout.addStretch(1)

        self._status_label = QLabel("")
        self._status_label.setMinimumWidth(112)
        self._status_label.setStyleSheet(
            f"QLabel {{ font-size: 12px; color: {qcolor_hex('text.secondary')}; padding-left: 2px; }}"
        )
        toolbar_layout.addWidget(self._status_label)

        layout.addWidget(toolbar)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {qcolor_hex('separator')};")
        layout.addWidget(sep)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)

        self._left_panel = QWidget()
        self._left_panel.setFixedWidth(240)
        left_layout = QVBoxLayout(self._left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        self._chapter_list = _OutlineChapterList()
        self._chapter_list.setObjectName("outlineChapterList")
        self._chapter_list.setUniformItemSizes(True)
        self._chapter_list.setLayoutMode(QListView.LayoutMode.Batched)
        self._chapter_list.setBatchSize(50)
        self._chapter_list.setSpacing(1)
        self._chapter_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._chapter_list.setStyleSheet(
            "QListWidget#outlineChapterList {"
            " border: none; background: transparent; font-size: 13px;"
            "}"
            "QListWidget#outlineChapterList::item {"
            " padding: 2px 6px; border-radius: 5px;"
            "}"
            "QListWidget#outlineChapterList::item:selected {"
            f" background: {qcolor_rgba('accent.primary', 0.08)};"
            "}"
            "QListWidget#outlineChapterList::item:hover {"
            f" background: {qcolor_rgba('accent.primary', 0.04)};"
            "}"
            "QListWidget#outlineChapterList::indicator {"
            " width: 14px; height: 14px; margin-left: 2px; margin-right: 8px;"
            f" border: 1px solid {qcolor_rgba('border.default', 0.42)}; border-radius: 3px;"
            f" background: {qcolor_hex('bg.surface')};"
            "}"
            "QListWidget#outlineChapterList::indicator:hover {"
            f" border-color: {qcolor_rgba('accent.primary', 0.70)}; background: {qcolor_hex('bg.hover.accent')};"
            "}"
            "QListWidget#outlineChapterList::indicator:checked {"
            f" image: none; background: {qcolor_hex('accent.primary')}; border-color: {qcolor_hex('accent.primary')};"
            "}"
            "QListWidget#outlineChapterList::indicator:checked:hover {"
            f" background: {qcolor_hex('accent.primary.pressed')}; border-color: {qcolor_hex('accent.primary.pressed')};"
            "}"
        )
        self._chapter_list.itemChanged.connect(self._on_chapter_item_changed)
        self._chapter_list.currentItemChanged.connect(self._on_current_chapter_changed)
        self._chapter_list.toggle_requested.connect(self._toggle_chapter_item_check_state)
        self._store_all_chapters()
        self._populate_chapter_list()
        left_layout.addWidget(self._chapter_list, 1)

        self._splitter.addWidget(self._left_panel)

        self._right_panel = QWidget()
        right_layout = QVBoxLayout(self._right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        self._read_browser = QTextBrowser()
        self._read_browser_renderer = IncrementalDocumentRenderer(self._read_browser)
        right_layout.addWidget(self._read_browser, 1)

        self._polish_panel = QWidget()
        pp_layout = QVBoxLayout(self._polish_panel)
        pp_layout.setContentsMargins(8, 10, 8, 8)
        pp_layout.setSpacing(8)

        self._polish_workspace_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._polish_workspace_splitter.setChildrenCollapsible(False)
        self._polish_workspace_splitter.setHandleWidth(8)
        self._polish_workspace_splitter.setStyleSheet(
            f"QSplitter::handle {{ background: {qcolor_rgba('border.default', 0.10)};"
            " border-radius: 4px; margin: 4px 0; }"
        )

        self._polish_work_panel = QWidget()
        work_layout = QVBoxLayout(self._polish_work_panel)
        work_layout.setContentsMargins(0, 0, 0, 0)
        work_layout.setSpacing(8)

        work_layout.addWidget(self._polish_controls)

        self._step_flow = QWidget()
        self._step_flow.setFixedHeight(26)
        self._step_flow.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self._step_flow.setStyleSheet(
            f"QWidget {{ background: {qcolor_rgba('bg.panel', 0.34)}; border-radius: 8px; }}"
        )
        sf_layout = QHBoxLayout(self._step_flow)
        sf_layout.setContentsMargins(8, 2, 8, 2)
        sf_layout.setSpacing(0)

        self._step_labels: list[tuple[QLabel, QLabel]] = []
        steps = [("1", "选择章节"), ("2", "分析建议"), ("3", "执行润色")]
        for i, (num, label) in enumerate(steps):
            step_widget = QWidget()
            sw_layout = QHBoxLayout(step_widget)
            sw_layout.setContentsMargins(0, 0, 0, 0)
            sw_layout.setSpacing(6)

            num_lbl = QLabel(num)
            num_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            num_lbl.setFixedSize(18, 18)
            num_lbl.setStyleSheet(
                f"QLabel {{ background: {qcolor_rgba('accent.primary', 0.15)}; color: {qcolor_hex('accent.primary')};"
                " border-radius: 9px; font-size: 10px; font-weight: 700; }"
            )
            sw_layout.addWidget(num_lbl)

            text_lbl = QLabel(label)
            text_lbl.setStyleSheet(
                f"QLabel {{ font-size: 11px; color: {qcolor_hex('text.muted')}; font-weight: 500; }}"
            )
            sw_layout.addWidget(text_lbl)

            sf_layout.addWidget(step_widget)
            self._step_labels.append((num_lbl, text_lbl))

            if i < len(steps) - 1:
                arrow = QLabel("→")
                arrow.setStyleSheet(
                    f"QLabel {{ color: {qcolor_hex('separator')}; font-size: 11px; }}"
                )
                arrow.setContentsMargins(8, 0, 8, 0)
                sf_layout.addWidget(arrow)

        sf_layout.addStretch()
        work_layout.addWidget(self._step_flow)
        self._step_flow.setVisible(False)

        self._overview_scroll = QScrollArea()
        self._overview_scroll.setWidgetResizable(True)
        self._overview_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._overview_scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
        )
        self._overview_container = QWidget()
        self._overview_layout = QHBoxLayout(self._overview_container)
        self._overview_layout.setContentsMargins(0, 0, 0, 0)
        self._overview_layout.setSpacing(8)
        self._overview_layout.addStretch()
        self._overview_scroll.setWidget(self._overview_container)
        self._overview_scroll.setFixedHeight(78)
        work_layout.addWidget(self._overview_scroll)
        self._overview_scroll.setVisible(False)

        self._suggestions_area = QWidget()
        sa_layout = QVBoxLayout(self._suggestions_area)
        sa_layout.setContentsMargins(0, 0, 0, 0)
        sa_layout.setSpacing(6)

        self._suggestions_header = QWidget()
        sh_layout = QHBoxLayout(self._suggestions_header)
        sh_layout.setContentsMargins(0, 0, 0, 0)
        sh_layout.setSpacing(8)
        self._suggestions_title = QLabel("AI 建议")
        self._suggestions_title.setStyleSheet(
            f"QLabel {{ font-size: 13px; font-weight: 700; color: {qcolor_hex('text.primary')}; }}"
        )
        sh_layout.addWidget(self._suggestions_title)
        self._suggestions_count = Badge("0", tone="info")
        sh_layout.addWidget(self._suggestions_count)
        sh_layout.addStretch()
        self._select_all_btn = ActionButton("全选", variant="quiet")
        self._select_all_btn.setFixedHeight(26)
        self._select_all_btn.setStyleSheet(compact_button_style)
        self._select_all_btn.clicked.connect(self._select_all_suggestions)
        sh_layout.addWidget(self._select_all_btn)
        self._deselect_all_btn = ActionButton("全不选", variant="quiet")
        self._deselect_all_btn.setFixedHeight(26)
        self._deselect_all_btn.setStyleSheet(compact_button_style)
        self._deselect_all_btn.clicked.connect(self._deselect_all_suggestions)
        sh_layout.addWidget(self._deselect_all_btn)
        sa_layout.addWidget(self._suggestions_header)

        self._suggestions_scroll = QScrollArea()
        self._suggestions_scroll.setWidgetResizable(True)
        self._suggestions_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._suggestions_scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
        )
        self._suggestions_list = QWidget()
        self._suggestions_list_layout = QVBoxLayout(self._suggestions_list)
        self._suggestions_list_layout.setContentsMargins(0, 0, 0, 0)
        self._suggestions_list_layout.setSpacing(5)
        self._suggestions_list_layout.addStretch()
        self._suggestions_scroll.setWidget(self._suggestions_list)
        sa_layout.addWidget(self._suggestions_scroll, 1)
        work_layout.addWidget(self._suggestions_area, 1)
        self._suggestions_area.setVisible(False)

        self._polish_bottom_spacer = QWidget()
        self._polish_bottom_spacer.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        work_layout.addWidget(self._polish_bottom_spacer, 1)

        self._preview_panel = QFrame()
        self._preview_panel.setObjectName("polishPreviewPanel")
        self._preview_panel.setStyleSheet(
            f"QFrame#polishPreviewPanel {{ background: {qcolor_rgba('bg.surface', 0.72)};"
            f" border: 1px solid {qcolor_rgba('border.default', 0.16)}; border-radius: 10px; }}"
        )
        preview_layout = QVBoxLayout(self._preview_panel)
        preview_layout.setContentsMargins(10, 8, 10, 10)
        preview_layout.setSpacing(7)

        preview_header = QWidget()
        ph_layout = QHBoxLayout(preview_header)
        ph_layout.setContentsMargins(0, 0, 0, 0)
        ph_layout.setSpacing(8)
        preview_title = QLabel("对比预览")
        preview_title.setStyleSheet(
            f"QLabel {{ font-size: 13px; font-weight: 700; color: {qcolor_hex('text.primary')}; }}"
        )
        ph_layout.addWidget(preview_title)
        self._preview_state_label = QLabel("待执行")
        self._preview_state_label.setStyleSheet(
            f"QLabel {{ font-size: 11px; color: {qcolor_hex('text.muted')}; }}"
        )
        ph_layout.addWidget(self._preview_state_label)
        ph_layout.addStretch()
        preview_layout.addWidget(preview_header)

        self._diff_preview = QTextBrowser()
        self._diff_preview.setStyleSheet(
            f"QTextBrowser {{ background: {qcolor_hex('bg.blueprint.row')}; border: 1px solid {qcolor_hex('separator')}; "
            f"border-radius: 8px; padding: 8px; font-size: 12px; color: {qcolor_hex('text.heading.deep')}; }}"
        )
        self._diff_preview_renderer = IncrementalDocumentRenderer(self._diff_preview)
        preview_layout.addWidget(self._diff_preview, 1)
        self._set_preview_placeholder()

        self._polish_workspace_splitter.addWidget(self._polish_work_panel)
        self._polish_workspace_splitter.addWidget(self._preview_panel)
        self._polish_workspace_splitter.setStretchFactor(0, 1)
        self._polish_workspace_splitter.setStretchFactor(1, 1)
        self._polish_workspace_splitter.setSizes([420, 660])
        pp_layout.addWidget(self._polish_workspace_splitter, 1)

        right_layout.addWidget(self._polish_panel, 1)
        self._polish_panel.setVisible(False)

        self._splitter.addWidget(self._right_panel)
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setSizes([240, 720])

        layout.addWidget(self._splitter, 1)
        if self._chapter_list.count() > 0:
            self._chapter_list.setCurrentRow(0)

    def _populate_chapter_list(self) -> None:
        self._chapter_list.clear()
        for ch in self._chapters:
            num = ch.get("chapter_number", 0)
            title = ch.get("title", "未命名")
            item = QListWidgetItem(f"  第 {num} 章 · {title}")
            item.setSizeHint(QSize(0, 26))
            item.setData(Qt.ItemDataRole.UserRole, ch)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self._chapter_list.addItem(item)

    def _toggle_chapter_item_check_state(self, item: QListWidgetItem) -> None:
        item.setCheckState(
            Qt.CheckState.Unchecked
            if item.checkState() == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )

    def _update_step_flow(self, active_step: int) -> None:
        for i, (num_lbl, text_lbl) in enumerate(self._step_labels):
            if i < active_step:
                num_lbl.setText("✓")
                num_lbl.setStyleSheet(
                    f"QLabel {{ background: {qcolor_rgba('status.success', 0.2)}; color: {qcolor_hex('status.success.deep')};"
                    " border-radius: 9px; font-size: 10px; font-weight: 700; }"
                )
                text_lbl.setStyleSheet(
                    f"QLabel {{ font-size: 11px; color: {qcolor_hex('status.success.deep')}; font-weight: 600; }}"
                )
            elif i == active_step:
                num_lbl.setText(str(i + 1))
                num_lbl.setStyleSheet(
                    f"QLabel {{ background: {qcolor_hex('accent.primary')}; color: {qcolor_hex('white')};"
                    " border-radius: 9px; font-size: 10px; font-weight: 700; }"
                )
                text_lbl.setStyleSheet(
                    f"QLabel {{ font-size: 11px; color: {qcolor_hex('accent.primary')}; font-weight: 700; }}"
                )
            else:
                num_lbl.setText(str(i + 1))
                num_lbl.setStyleSheet(
                    f"QLabel {{ background: {qcolor_rgba('accent.primary', 0.15)}; color: {qcolor_hex('accent.primary')};"
                    " border-radius: 9px; font-size: 10px; font-weight: 700; }"
                )
                text_lbl.setStyleSheet(
                    f"QLabel {{ font-size: 11px; color: {qcolor_hex('text.muted')}; font-weight: 500; }}"
                )

    def _update_button_states(self) -> None:
        checked = self._get_checked_chapter_numbers()

        if not checked:
            self._analyze_btn.setEnabled(False)
            self._analyze_btn.setToolTip("请先在阅读页勾选要润色的章节")
            self._exec_btn.setEnabled(False)
            self._exec_btn.setToolTip("请先在阅读页勾选要润色的章节")
        else:
            self._analyze_btn.setEnabled(True)
            self._analyze_btn.setToolTip("")
            self._exec_btn.setEnabled(True)
            if self._polish_suggestions:
                self._exec_btn.setToolTip("按当前勾选的建议执行；未勾选时将按润色方向直接执行")
            else:
                self._exec_btn.setToolTip("直接按润色方向执行；也可先分析建议")

    def _store_all_chapters(self) -> None:
        self._all_chapter_items = []
        for ch in self._chapters:
            num = ch.get("chapter_number", 0)
            title = ch.get("title", "未命名")
            self._all_chapter_items.append((num, title, ch))

    def _get_checked_chapter_numbers(self) -> list[int]:
        selected: list[int] = []
        for i in range(self._chapter_list.count()):
            item = self._chapter_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                ch = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(ch, dict):
                    selected.append(ch.get("chapter_number", 0))
        return selected

    def _all_chapter_numbers(self) -> list[int]:
        numbers: list[int] = []
        for ch in self._chapters:
            try:
                num = int(ch.get("chapter_number", 0) or 0)
            except (TypeError, ValueError):
                continue
            if num > 0:
                numbers.append(num)
        return numbers

    def _effective_chapter_numbers(self) -> list[int]:
        return self._get_checked_chapter_numbers()

    def _render_read_view(self) -> None:
        from novel_forge.desktop.pages.document_renderer_story_artifacts import (
            render_outline,
        )

        browser = render_outline(self._outline_data)
        self._read_browser_renderer.update_content(browser.toHtml())
        self._scroll_read_view_to_current_chapter()

    def _on_current_chapter_changed(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        if self._mode != "read" or current is None:
            return
        self._scroll_read_view_to_current_chapter()

    def _scroll_read_view_to_current_chapter(self) -> None:
        if not hasattr(self, "_read_browser"):
            return
        item = self._chapter_list.currentItem()
        if item is None:
            return
        chapter = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(chapter, dict):
            return
        num = chapter.get("chapter_number")
        if num is None:
            return
        self._read_browser.scrollToAnchor(f"chapter-{num}")

    def _session_data(self) -> dict[str, Any]:
        return {
            "checked_chapters": self._get_checked_chapter_numbers(),
            "hint_text": self._hint_input.text(),
            "mode": self._mode,
        }

    def _save_session(self) -> None:
        if self._loading_session:
            return
        try:
            self._session_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(
                self._session_path,
                json.dumps(self._session_data(), ensure_ascii=False, indent=2),
            )
        except Exception:
            pass

    def _load_session(self) -> None:
        self._loading_session = True
        try:
            if not self._session_path.exists():
                return
            raw = self._session_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            checked = data.get("checked_chapters", [])
            if isinstance(checked, list):
                for i in range(self._chapter_list.count()):
                    item = self._chapter_list.item(i)
                    ch = item.data(Qt.ItemDataRole.UserRole)
                    if isinstance(ch, dict) and ch.get("chapter_number", 0) in checked:
                        item.setCheckState(Qt.CheckState.Checked)
            hint = data.get("hint_text", "")
            if isinstance(hint, str) and hint:
                self._hint_input.setText(hint)
            mode = data.get("mode", "")
            if mode in {"read", "polish"}:
                self._mode = mode
        except Exception:
            pass
        finally:
            self._loading_session = False

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        is_polish = mode == "polish"
        self._read_btn.setChecked(not is_polish)
        self._polish_btn.setChecked(is_polish)
        self._polish_panel.setVisible(is_polish)
        self._read_browser.setVisible(not is_polish)

        if is_polish:
            self._left_panel.setVisible(False)
            self._splitter.setSizes([0, 960])
            self._build_overview_cards()
            self._overview_scroll.setVisible(True)
            self._step_flow.setVisible(True)
            self._polish_bottom_spacer.setVisible(not self._suggestions_area.isVisible())
            self._update_step_flow(0)
            self._update_selection_count()
            self._on_hint_changed(self._hint_input.text())
            self._update_button_states()
        else:
            self._left_panel.setVisible(True)
            self._splitter.setSizes([240, 720])
            self._overview_scroll.setVisible(False)
            self._step_flow.setVisible(False)
            if self._pending_outline_data is None:
                self._polish_suggestions = []
                self._suggestions_area.setVisible(False)
                self._set_preview_placeholder()
                self._polish_bottom_spacer.setVisible(False)
            self._status_label.setText("")
            self._selection_count_label.setText("")
            self._exec_btn.setEnabled(False)
            self._analyze_btn.setEnabled(True)
            self._analyze_btn.setText("分析建议")

        if is_polish and self._pending_polish_result is not None:
            self._show_diff_preview(self._pending_polish_result)
            self._set_pending_result_controls(True)
            self._update_step_flow(3)
        self._save_session()

    def _build_overview_cards(self) -> None:
        while self._overview_layout.count():
            item = self._overview_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        checked = self._effective_chapter_numbers()

        if not checked:
            hint = QLabel("未选章节。请回到阅读页勾选需要润色的章节。")
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hint.setStyleSheet(
                f"QLabel {{ color: {qcolor_hex('text.muted')}; font-size: 12px; padding: 12px 8px;"
                f" background: {qcolor_rgba('bg.surface', 0.72)};"
                f" border: 1px dashed {qcolor_rgba('border.default', 0.20)};"
                " border-radius: 8px; }"
            )
            self._overview_layout.addWidget(hint, 1)
            self._overview_layout.addStretch()
            return

        for ch in self._chapters:
            num = ch.get("chapter_number", 0)
            if num in checked:
                card = ChapterOverviewCard(ch)
                self._overview_layout.addWidget(card)

        self._overview_layout.addStretch()

    def _update_selection_count(self) -> None:
        checked = self._get_checked_chapter_numbers()
        total = len(self._chapters)
        if checked:
            self._selection_count_label.setText(
                f"已选 {len(checked)}/{total} 章 · 第 {self._format_selected_chapters(checked)} 章"
            )
        else:
            self._selection_count_label.setText(f"未选章节 / 共 {total} 章")
        self._update_button_states()
        if self._mode == "polish" and checked:
            self._update_step_flow(1)
        elif self._mode == "polish":
            self._update_step_flow(0)

    def _on_chapter_item_changed(self) -> None:
        self._save_session()
        if self._mode != "polish":
            return
        self._build_overview_cards()
        self._update_selection_count()

    def _on_hint_changed(self, text: str) -> None:
        if self._mode != "polish":
            return
        self._status_label.setText("")
        self._update_button_states()
        self._save_session()

    def _parse_chapter_hint(self, hint: str) -> list[int] | None:
        if not hint:
            return None
        total = len(self._chapters)
        chapters: set[int] = set()

        range_patterns = [
            r"第\s*(\d+)\s*[\-至到~]\s*(\d+)\s*[章节]",
            r"(\d+)\s*[\-至到~]\s*(\d+)\s*[章节]",
        ]
        for pat in range_patterns:
            for m in re.finditer(pat, hint):
                start, end = int(m.group(1)), int(m.group(2))
                if start <= end and start >= 1 and end <= total:
                    chapters.update(range(start, end + 1))

        list_patterns = [
            r"第\s*([\d\s,，、]+)\s*[章节]",
            r"([\d\s,，、]+)\s*[章节]",
        ]
        for pat in list_patterns:
            list_match = re.search(pat, hint)
            if list_match:
                nums = re.split(r"[,，、\s]+", list_match.group(1).strip())
                for n in nums:
                    if n.isdigit():
                        num = int(n)
                        if 1 <= num <= total:
                            chapters.add(num)

        single_match = re.search(r"第?\s*(\d+)\s*[章节]", hint)
        if single_match:
            num = int(single_match.group(1))
            if 1 <= num <= total:
                chapters.add(num)

        return sorted(chapters) if chapters else None

    def _format_range(self, numbers: list[int]) -> str:
        if not numbers:
            return ""
        if len(numbers) == 1:
            return str(numbers[0])
        return f"{numbers[0]}-{numbers[-1]}"

    def _format_selected_chapters(self, numbers: list[int], *, max_segments: int = 6) -> str:
        ordered = sorted({int(num) for num in numbers if int(num) > 0})
        if not ordered:
            return ""
        segments: list[str] = []
        start = prev = ordered[0]
        for num in ordered[1:]:
            if num == prev + 1:
                prev = num
                continue
            segments.append(str(start) if start == prev else f"{start}-{prev}")
            start = prev = num
        segments.append(str(start) if start == prev else f"{start}-{prev}")
        if len(segments) > max_segments:
            visible = "、".join(segments[:max_segments])
            return f"{visible} 等"
        return "、".join(segments)

    def _hint_needs_semantic_scope(self, hint: str) -> bool:
        if not hint:
            return False
        scope_terms = (
            "前半",
            "后半",
            "前几",
            "后几",
            "中段",
            "中间",
            "开头",
            "开篇",
            "结尾",
            "收束",
            "末尾",
            "第",
        )
        return any(term in hint for term in scope_terms) or any(ch.isdigit() for ch in hint)

    def _apply_chapter_selection(self, numbers: list[int]) -> None:
        wanted = set(numbers)
        for i in range(self._chapter_list.count()):
            item = self._chapter_list.item(i)
            ch = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(ch, dict):
                num = ch.get("chapter_number", 0)
                item.setCheckState(
                    Qt.CheckState.Checked if num in wanted else Qt.CheckState.Unchecked
                )
        self._build_overview_cards()
        self._update_selection_count()

    def _start_semantic_resolve(self, hint: str, pending_action: str) -> None:
        if self._semantic_resolving:
            return
        self._semantic_resolving = True
        self._status_label.setText("正在理解指令中的章节范围…")
        self._pending_action = pending_action
        worker = SemanticResolveWorker(hint, self._chapters)
        worker.signals.resolved.connect(self._on_semantic_resolved)
        worker.signals.error.connect(self._on_semantic_error)
        desktop_thread_pools().aux_pool.start(worker)

    def _on_semantic_resolved(self, chapters: list[int], reason: str) -> None:
        self._semantic_resolving = False
        if chapters:
            valid = [n for n in chapters if 1 <= n <= len(self._chapters)]
            if valid:
                self._apply_chapter_selection(valid)
                self._status_label.setText(
                    f"已智能定位到第 {self._format_range(valid)} 章（{reason}）"
                )
                if getattr(self, "_pending_action", None) == "analyze":
                    self._on_analyze()
                elif getattr(self, "_pending_action", None) == "execute":
                    self._on_execute()
                return
        self._status_label.setText(
            "未能从指令中识别章节范围，请直接勾选或使用明确范围（如第10-15章）"
        )

    def _on_semantic_error(self, error_text: str) -> None:
        self._semantic_resolving = False
        self._status_label.setText(f"语义解析失败: {error_text}")

    def _on_analyze(self) -> None:
        if self._polish_in_progress:
            self._status_label.setText("当前大纲润色仍在执行，请稍候")
            return
        if self._pending_outline_data is not None:
            self._status_label.setText("请先应用或放弃当前润色结果")
            return
        hint = self._hint_input.text().strip()
        explicit_checked = self._get_checked_chapter_numbers()
        checked = explicit_checked

        if not checked:
            self._status_label.setText("请先在阅读页勾选要调整的章节")
            return

        self._status_label.setText("AI 正在分析建议…")
        self._analyze_btn.setEnabled(False)
        self._analyze_btn.setText("分析中…")
        self._exec_btn.setEnabled(False)
        self._exec_btn.setToolTip("正在分析建议，请稍候")
        self._update_step_flow(1)

        from novel_forge.core.schemas.outline import StoryOutline

        outline = StoryOutline.model_validate(self._outline_data)
        self._original_chapters = {ch.get("chapter_number", 0): ch for ch in self._chapters}

        range_str = None
        if not explicit_checked:
            range_str = None
        elif len(checked) == 1:
            range_str = str(checked[0])
        elif len(checked) > 1:
            range_str = ",".join(str(n) for n in sorted(checked))
        focus_fields = _polish_focus_fields_for_text(hint)

        worker = OutlinePolishWorker(
            story_outline=outline,
            user_hint=hint,
            selected_suggestions=[],
            focus_fields=focus_fields,
            chapter_range=range_str,
            analysis_only=True,
            project_id=self._outline_path.parent.name,
        )
        worker.signals.suggestions_ready.connect(self._on_suggestions_ready)
        worker.signals.polish_done.connect(self._on_analyze_done)
        worker.signals.error.connect(self._on_polish_error)
        desktop_thread_pools().aux_pool.start(worker)

    def _on_suggestions_ready(self, suggestions: list[str]) -> None:
        self._polish_suggestions = suggestions
        self._show_suggestions(suggestions)
        self._suggestions_count.setText(str(len(suggestions)))
        self._status_label.setText(f"AI 提供了 {len(suggestions)} 条建议")
        self._analyze_btn.setEnabled(True)
        self._analyze_btn.setText("分析建议")
        self._update_step_flow(2)
        self._update_button_states()

    def _show_suggestions(self, suggestions: list[str]) -> None:
        while self._suggestions_list_layout.count():
            item = self._suggestions_list_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for sugg in suggestions:
            card = SuggestionCard(sugg)
            self._suggestions_list_layout.addWidget(card)

        self._suggestions_list_layout.addStretch()
        self._suggestions_area.setVisible(True)
        self._polish_bottom_spacer.setVisible(False)

    def _select_all_suggestions(self) -> None:
        for i in range(self._suggestions_list_layout.count()):
            item = self._suggestions_list_layout.itemAt(i)
            if item is None:
                continue
            widget = item.widget()
            if isinstance(widget, SuggestionCard):
                widget._checkbox.setChecked(True)

    def _deselect_all_suggestions(self) -> None:
        for i in range(self._suggestions_list_layout.count()):
            item = self._suggestions_list_layout.itemAt(i)
            if item is None:
                continue
            widget = item.widget()
            if isinstance(widget, SuggestionCard):
                widget._checkbox.setChecked(False)

    def _get_checked_suggestions(self) -> list[str]:
        checked: list[str] = []
        for i in range(self._suggestions_list_layout.count()):
            item = self._suggestions_list_layout.itemAt(i)
            if item is None:
                continue
            widget = item.widget()
            if isinstance(widget, SuggestionCard) and widget.is_checked():
                checked.append(widget.text())
        return checked

    def _on_analyze_done(self, result: object) -> None:
        self._analyze_btn.setEnabled(True)
        self._analyze_btn.setText("分析建议")
        if not self._polish_suggestions:
            warnings = getattr(result, "warnings", []) or []
            if warnings:
                self._status_label.setText(str(warnings[0]))
            else:
                self._status_label.setText("暂无建议，可补充方向后重试，或直接执行润色")
            self._update_step_flow(1)
            self._update_button_states()

    def _on_execute(self) -> None:
        if self._polish_in_progress:
            self._status_label.setText("当前大纲润色仍在执行，请稍候")
            return
        if self._pending_outline_data is not None:
            self._status_label.setText("请先应用或放弃当前润色结果")
            return
        hint = self._hint_input.text().strip()
        explicit_checked = self._get_checked_chapter_numbers()
        checked = explicit_checked

        if not checked:
            self._status_label.setText("请先在阅读页勾选要润色的章节")
            return

        checked_suggestions = self._get_checked_suggestions()

        if checked_suggestions:
            self._status_label.setText("AI 正在按已选建议执行润色…")
        else:
            self._status_label.setText("AI 正在按润色方向直接执行…")
        self._preview_state_label.setText("润色中")
        self._exec_btn.setEnabled(False)
        self._exec_btn.setText("润色中…")
        self._save_btn.setEnabled(False)
        self._discard_btn.setEnabled(False)
        self._polish_in_progress = True
        self._update_step_flow(2)

        from novel_forge.core.schemas.outline import StoryOutline

        outline = StoryOutline.model_validate(self._outline_data)
        self._original_chapters = {ch.get("chapter_number", 0): ch for ch in self._chapters}

        range_str = None
        if not explicit_checked:
            range_str = None
        elif len(checked) == 1:
            range_str = str(checked[0])
        elif len(checked) > 1:
            range_str = ",".join(str(n) for n in sorted(checked))
        focus_fields = _polish_focus_fields_for_text(hint, checked_suggestions)

        worker = OutlinePolishWorker(
            story_outline=outline,
            user_hint=hint,
            selected_suggestions=checked_suggestions,
            focus_fields=focus_fields,
            chapter_range=range_str,
            analysis_only=False,
            project_id=self._outline_path.parent.name,
        )
        worker.signals.polish_done.connect(self._on_polish_done)
        worker.signals.error.connect(self._on_polish_error)
        desktop_thread_pools().aux_pool.start(worker)

    def _on_polish_done(self, result: object) -> None:
        self._polish_in_progress = False
        self._analyze_btn.setEnabled(True)
        self._analyze_btn.setText("分析建议")
        self._exec_btn.setEnabled(True)
        self._exec_btn.setText("执行润色")
        self._set_pending_result_controls(False)

        result_attr = getattr(result, "adjusted_outline", None)
        changed = getattr(result, "changed_chapters", []) or []

        if result_attr is not None and changed:
            self._pending_outline_data = result_attr.model_dump(mode="json")
            self._pending_polish_result = result
            if self._mode != "polish":
                self._set_mode("polish")
            self._show_diff_preview(result)
            self._set_pending_result_controls(True)
            self._status_label.setText(
                f"润色完成，涉及 {len(changed)} 章。请查看对比后应用或放弃。"
            )
            self._update_step_flow(3)
        else:
            self._pending_outline_data = None
            self._pending_polish_result = None
            self._set_pending_result_controls(False)
            self._status_label.setText("润色完成，无变更")
            self._set_preview_placeholder("本次润色没有产生字段变更")
            self._update_step_flow(2)

    def _set_pending_result_controls(self, enabled: bool) -> None:
        self._save_btn.setEnabled(enabled)
        self._discard_btn.setEnabled(enabled)

    def _set_preview_placeholder(self, message: str = "执行润色后显示对比") -> None:
        self._preview_state_label.setText("待执行")
        self._diff_preview_renderer.update_content(
            f"<div style='font-family:system-ui,-apple-system,sans-serif;"
            f" color:{qcolor_hex('text.muted')}; padding:18px; line-height:1.7;'>"
            f"<div style='font-size:13px; font-weight:700; color:{qcolor_hex('text.quiet')};'>{message}</div>"
            "<div style='margin-top:8px;'>"
            "仅处理阅读页勾选的章节；若已分析并勾选建议，则会按勾选建议优先执行。"
            "</div></div>"
        )

    def _show_diff_preview(self, result: object) -> None:
        old_map = self._original_chapters
        new_chapters = getattr(result, "adjusted_outline", None)
        if new_chapters is None:
            return
        changed = getattr(result, "changed_chapters", []) or []

        parts: list[str] = []
        for num in changed:
            old_ch = old_map.get(num, {})
            new_ch = None
            for ch in new_chapters.chapters or []:
                if getattr(ch, "chapter_number", None) == num:
                    new_ch = ch
                    break
            if new_ch is None:
                continue

            title = html.escape(str(getattr(new_ch, "title", "") or "未命名"), quote=True)
            parts.append(f"<section class='chapter'><h3>第 {num} 章 · {title}</h3>")
            for field in _DIFF_PREVIEW_FIELDS:
                old_val = _format_diff_value(old_ch.get(field, ""))
                new_val = _format_diff_value(getattr(new_ch, field, "") or "")
                if old_val != new_val:
                    parts.append(
                        "<div class='field'>"
                        f"<div class='field-title'>{_field_label(field)}</div>"
                        f"<div class='diff-text'>{_inline_diff_html(old_val, new_val)}</div>"
                        "</div>"
                    )
            parts.append("</section>")

        if parts:
            html_content = (
                "<style>"
                "body { margin:0; font-family:system-ui,-apple-system,sans-serif;"
                f" color:{qcolor_hex('text.heading.deep')}; background:{qcolor_hex('bg.blueprint.row')}; }}"
                f".legend {{ color:{qcolor_hex('text.muted')}; font-size:11px; margin:0 0 8px; }}"
                ".chapter { margin:0 0 12px; padding:10px 11px;"
                f" background:{qcolor_hex('bg.surface')}; border:1px solid {qcolor_rgba('border.default', 0.16)};"
                " border-radius:8px; }"
                f"h3 {{ margin:0 0 8px; font-size:13px; color:{qcolor_hex('text.primary')}; }}"
                ".field { margin-top:8px; padding-top:8px;"
                f" border-top:1px solid {qcolor_rgba('border.default', 0.12)}; }}"
                ".field-title { margin-bottom:4px; font-size:11px;"
                f" color:{qcolor_hex('text.muted')}; font-weight:700; }}"
                ".diff-text { font-size:12px; line-height:1.72; white-space:normal; }"
                "</style>"
                "<div class='legend'>绿色为新增或调整，红色删除线为移除。</div>"
                f"{''.join(parts)}"
            )
            self._diff_preview_renderer.update_content(html_content)
            self._preview_state_label.setText(f"涉及 {len(changed)} 章")

    def _on_polish_error(self, error_text: str) -> None:
        self._polish_in_progress = False
        self._status_label.setText(f"错误: {error_text}")
        self._preview_state_label.setText("执行失败")
        self._analyze_btn.setEnabled(True)
        self._analyze_btn.setText("分析建议")
        self._exec_btn.setEnabled(True)
        self._exec_btn.setText("执行润色")

    def has_unsaved_changes(self) -> bool:
        return (
            self._polish_in_progress
            or self._pending_outline_data is not None
            or self._outline_data != self._saved_outline_data
        )

    def shutdown(self) -> None:
        """Stop timers, disconnect signals, and tear down sub-widgets (I-5).

        Idempotent: safe to call multiple times.  Thread-pool draining is
        handled globally by ``shutdown_desktop_thread_pools()`` in
        ``_pre_close_cleanup()``.
        """
        if getattr(self, "_shutdown_done", False):
            return
        self._shutdown_done = True

        # Disconnect chapter-list signals
        chapter_list = getattr(self, "_chapter_list", None)
        if chapter_list is not None:
            safe_disconnect(chapter_list.itemChanged, self._on_chapter_item_changed)
            safe_disconnect(chapter_list.currentItemChanged, self._on_current_chapter_changed)
            safe_disconnect(chapter_list.toggle_requested, self._toggle_chapter_item_check_state)

        # Disconnect hint input
        hint_input = getattr(self, "_hint_input", None)
        if hint_input is not None:
            safe_disconnect(hint_input.textChanged, self._on_hint_changed)

        # Disconnect action buttons
        for attr in (
            "_read_btn",
            "_polish_btn",
            "_extend_btn",
            "_analyze_btn",
            "_exec_btn",
            "_save_btn",
            "_discard_btn",
            "_history_btn",
            "_sync_btn",
            "_select_all_btn",
            "_deselect_all_btn",
        ):
            btn = getattr(self, attr, None)
            if btn is not None:
                safe_disconnect(btn.clicked)

    def save_pending_changes(self) -> bool:
        if self._polish_in_progress:
            return False
        if not self.has_unsaved_changes():
            return True
        return self._save_outline()

    def unsaved_changes_description(self) -> str:
        return "- 卷帙页（全书大纲润色）有未保存修改"

    def confirm_close(self) -> bool:
        if self._polish_in_progress:
            show_message_box(
                self.window(),
                "大纲润色仍在执行",
                "请等待当前润色任务完成后再切换或关闭页面。",
                actions=(
                    MessageBoxAction(
                        "ok",
                        "知道了",
                        QMessageBox.ButtonRole.AcceptRole,
                        "primary",
                        True,
                    ),
                ),
            )
            return False
        if not self.has_unsaved_changes():
            return True
        choice = show_message_box(
            self.window(),
            "大纲润色尚未应用",
            "当前润色结果还只是对比预览，尚未写入 outline.json。",
            informative_text="可以应用并保存，也可以放弃本次润色结果。",
            actions=(
                MessageBoxAction(
                    "save",
                    "应用并保存",
                    QMessageBox.ButtonRole.AcceptRole,
                    "primary",
                    True,
                ),
                MessageBoxAction(
                    "discard",
                    "放弃修改",
                    QMessageBox.ButtonRole.DestructiveRole,
                    "danger",
                ),
                MessageBoxAction(
                    "cancel",
                    "继续查看",
                    QMessageBox.ButtonRole.RejectRole,
                    "secondary",
                ),
            ),
            escape_key="cancel",
        )
        if choice == "save":
            return self.save_pending_changes()
        if choice == "discard":
            self._discard_outline_changes()
            return True
        return False

    def _on_save(self) -> None:
        had_pending = self._pending_outline_data is not None
        if not self._save_outline():
            return
        if not had_pending:
            return
        self._preview_state_label.setText("已应用")
        self._status_label.setText("已应用并保存；可点击同步契约，或稍后手动同步")
        self._sync_btn.setToolTip("建议在进入章台生成前同步契约；点击后会先显示影响范围预览")
        if self._mode == "polish":
            self._update_step_flow(3)
        self._on_sync_contracts_clicked()

    def _save_outline(self) -> bool:
        try:
            from novel_forge.persistence.authoring_store import AuthoringStore

            authority = AuthoringStore(self._outline_path.parent)
            if authority.policy() is not None:
                self._status_label.setText(
                    "已启用作者授权：请通过版本化规划提案批准发布；本窗口候选保留，未写正式大纲。"
                )
                return False
            current_mode = self._mode
            previous_hash = (
                hashlib.sha256(self._outline_path.read_bytes()).hexdigest()
                if self._outline_path.exists()
                else ""
            )
            if self._pending_outline_data is not None:
                self._outline_data = deepcopy(self._pending_outline_data)
                raw_chapters = self._outline_data.get("chapters", [])
                self._chapters = (
                    [dict(chapter) for chapter in raw_chapters if isinstance(chapter, dict)]
                    if isinstance(raw_chapters, list)
                    else []
                )
            atomic_write_text(
                self._outline_path,
                json.dumps(self._outline_data, ensure_ascii=False, indent=2),
            )
            try:
                project_dir = self._outline_path.parent
                record_upstream_artifact_revision(
                    FileSystemStorage(project_dir.parent),
                    ProjectLayout(project_dir),
                    artifact_kind=UpstreamArtifactKind.OUTLINE,
                    previous_hash=previous_hash,
                    scope=RevisionScope.FORWARD_ONLY,
                    reason="desktop_outline_editor_save",
                )
            except Exception:
                pass
            self._saved_outline_data = deepcopy(self._outline_data)
            self._pending_outline_data = None
            self._pending_polish_result = None
            self._render_read_view()
            self._status_label.setText("已应用并保存")
            self._set_pending_result_controls(False)
            if current_mode == "polish":
                self._set_mode("polish")
            return True
        except OSError as exc:
            self._status_label.setText(f"保存失败: {exc}")
            return False

    def _discard_outline_changes(self) -> None:
        self._pending_outline_data = None
        self._pending_polish_result = None
        self._outline_data = deepcopy(self._saved_outline_data)
        raw_chapters = self._outline_data.get("chapters", [])
        self._chapters = (
            [dict(chapter) for chapter in raw_chapters if isinstance(chapter, dict)]
            if isinstance(raw_chapters, list)
            else []
        )
        self._render_read_view()
        self._set_preview_placeholder("未保存的润色已放弃")
        self._status_label.setText("未保存的润色已放弃")
        self._set_pending_result_controls(False)

    def _on_show_history(self) -> None:
        if self._polish_in_progress:
            self._status_label.setText("当前大纲润色仍在执行，请稍候")
            return
        if self._pending_outline_data is not None:
            self._status_label.setText("请先应用或放弃当前润色结果，再查看历史")
            return
        try:
            from novel_forge.persistence.polish_history import PolishHistoryRecorder

            recorder = PolishHistoryRecorder(self._outline_path.parent)
            history = recorder.load_outline_history()
            if not history:
                self._status_label.setText("暂无润色历史记录")
                return
            parts: list[str] = []
            for i, entry in enumerate(history[-5:], 1):
                ts = (
                    entry.timestamp[:19].replace("T", " ")
                    if len(entry.timestamp) > 19
                    else entry.timestamp
                )
                ch_range = entry.chapter_range or "全部"
                hint = entry.user_hint.strip()
                hint_preview = hint[:40] + ("..." if len(hint) > 40 else "") if hint else "未填写"
                if entry.changed_keys:
                    change_text = "第 " + ", ".join(entry.changed_keys) + " 章"
                elif entry.result_type == "analyze":
                    change_text = "仅分析建议，不改大纲"
                else:
                    change_text = "无字段变更"
                parts.append(
                    f"<b>{i}. {ts}</b> | 范围：第 {ch_range} 章 | "
                    f"类型：{'分析' if entry.result_type == 'analyze' else '执行'}<br/>"
                    f"方向：{html.escape(hint_preview, quote=True)}<br/>"
                    f"结果：{html.escape(change_text, quote=True)}<br/>"
                )
            html_content = (
                f"<div style='font-family:system-ui,sans-serif;font-size:12px;"
                f"color:{qcolor_hex('text.primary')};padding:8px;'>"
                f"<h4 style='margin:0 0 8px;'>最近润色历史（共 {len(history)} 条）</h4>"
                f"{'<hr/>'.join(parts)}</div>"
            )
            self._diff_preview_renderer.update_content(html_content)
            self._preview_state_label.setText("历史记录")
        except Exception as exc:
            self._status_label.setText(f"加载历史失败: {exc}")

    def _on_sync_contracts_clicked(self) -> None:
        """Open the sync-contracts confirmation flow.

        Loads the latest outline + chapter contracts, asks the presenter
        to build a preview and confirmation dialog, and emits
        ``sync_chapter_contracts_requested`` if the user confirms.
        """
        try:
            if self._polish_in_progress:
                self._status_label.setText("当前大纲润色仍在执行，暂不能同步契约")
                return
            if self._pending_outline_data is not None:
                self._status_label.setText("请先应用或放弃当前润色结果，再同步契约")
                return
            from novel_forge.desktop.pages.standalone.outline_sync_presenter import (
                ChapterContractSyncPresenter,
            )
            from novel_forge.workspace.contracts import SyncChapterContractsRequest

            project_id = self._outline_path.parent.name
            if self.has_unsaved_changes():
                choice = show_message_box(
                    self,
                    title="保存大纲后同步",
                    text="当前章节大纲有未保存修改。",
                    informative_text="同步契约会读取 outline.json。请先保存本次大纲修改后再同步。",
                    icon=QMessageBox.Icon.Question,
                    actions=(
                        MessageBoxAction(
                            "save",
                            "保存并同步",
                            QMessageBox.ButtonRole.AcceptRole,
                            "primary",
                            True,
                        ),
                        MessageBoxAction(
                            "cancel",
                            "取消",
                            QMessageBox.ButtonRole.RejectRole,
                            "secondary",
                        ),
                    ),
                    escape_key="cancel",
                )
                if choice != "save":
                    self._status_label.setText("已取消同步契约")
                    return
                if not self._save_outline():
                    return

            storage = _get_storage_or_none(self._outline_path.parent)
            if storage is None:
                self._status_label.setText("存储后端不可用,无法同步契约")
                return
            presenter = ChapterContractSyncPresenter(
                parent=self,
                storage=storage,
                layout_root=self._outline_path.parent,
                project_id=project_id,
                explicit_affected_numbers=self._get_checked_chapter_numbers(),
            )
            request: SyncChapterContractsRequest | None = presenter.run()
            if request is None:
                self._status_label.setText("已取消同步契约")
                return
            self._status_label.setText(
                f"已提交后台同步 · 焦点章节 {len(request.affected_chapter_numbers) or '自动检测'}"
            )
            self._sync_btn.setText("同步中...")
            self._sync_btn.setEnabled(False)
            self._sync_btn.setToolTip("同步契约任务正在后台执行，完成前请暂缓进入章台生成")
            self._show_sync_status(
                title="同步契约已提交",
                state="同步中",
                message="后台正在刷新章节契约。完成前，同项目章台生成会被写任务冲突保护挡住。",
                result={
                    "affected": list(request.affected_chapter_numbers),
                    "cascade": [],
                    "focus": list(request.affected_chapter_numbers),
                    "stale_marked": 0,
                },
                tone="pending",
            )
            self.sync_chapter_contracts_requested.emit(request)
        except Exception as exc:  # noqa: BLE001
            self._status_label.setText(f"同步契约触发失败: {exc}")
            show_message_box(
                self,
                title="同步契约触发失败",
                text=str(exc),
                actions=(
                    MessageBoxAction(
                        "ok",
                        "知道了",
                        QMessageBox.ButtonRole.AcceptRole,
                        "primary",
                        True,
                    ),
                ),
            )

    def _on_extend_outline_clicked(self) -> None:
        """Open the extend-outline dialog and emit a workspace request."""
        try:
            if self._polish_in_progress:
                self._status_label.setText("当前大纲润色仍在执行，暂不能延长全书")
                return
            if self._pending_outline_data is not None:
                self._status_label.setText("请先应用或放弃当前润色结果，再延长全书")
                return
            if self.has_unsaved_changes():
                choice = show_message_box(
                    self,
                    title="保存大纲后延长",
                    text="当前章节大纲有未保存修改。",
                    informative_text="延长全书会读取 outline.json。请先保存本次大纲修改后再继续。",
                    icon=QMessageBox.Icon.Question,
                    actions=(
                        MessageBoxAction(
                            "save",
                            "保存并继续",
                            QMessageBox.ButtonRole.AcceptRole,
                            "primary",
                            True,
                        ),
                        MessageBoxAction(
                            "cancel",
                            "取消",
                            QMessageBox.ButtonRole.RejectRole,
                            "secondary",
                        ),
                    ),
                    escape_key="cancel",
                )
                if choice != "save":
                    self._status_label.setText("已取消延长全书")
                    return
                if not self._save_outline():
                    return

            current_total = int(
                self._outline_data.get("total_chapters") or len(self._chapters) or 0
            )
            if current_total >= 10000:
                show_message_box(
                    self,
                    title="无法继续延长",
                    text="当前大纲已达到章节数上限 10000。",
                    actions=(
                        MessageBoxAction(
                            "ok",
                            "知道了",
                            QMessageBox.ButtonRole.AcceptRole,
                            "primary",
                            True,
                        ),
                    ),
                )
                return
            dialog = _ExtendOutlineDialog(
                project_id=self._outline_path.parent.name,
                current_total=current_total,
                parent=self,
            )
            if dialog.exec() != QDialog.DialogCode.Accepted:
                self._status_label.setText("已取消延长全书")
                return
            request = dialog.to_request()
            self._status_label.setText("已提交后台延长全书任务")
            self._extend_btn.setEnabled(False)
            self._extend_btn.setText("延长中...")
            self.extend_outline_requested.emit(request)
        except Exception as exc:  # noqa: BLE001
            self._status_label.setText(f"延长全书触发失败: {exc}")
            show_message_box(
                self,
                title="延长全书触发失败",
                text=str(exc),
                actions=(
                    MessageBoxAction(
                        "ok",
                        "知道了",
                        QMessageBox.ButtonRole.AcceptRole,
                        "primary",
                        True,
                    ),
                ),
            )

    def _set_sync_btn_enabled(self, enabled: bool) -> None:
        """Enable/disable the sync button. Public API for the parent page."""
        self._sync_btn.setEnabled(enabled)
        if enabled:
            self._sync_btn.setText("同步契约")
        elif self._sync_btn.text() == "同步契约":
            self._sync_btn.setText("同步中...")
        if enabled:
            self._sync_btn.setToolTip("根据当前 outline 局部刷新章节契约(不修改 prose)")

    def _on_extend_job_finished(
        self,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        payload = result or {}
        self._extend_btn.setText("延长全书")
        self._extend_btn.setEnabled(True)

        result_status = str(payload.get("status") or status or "").lower()
        if status == "succeeded" and result_status in {"completed", "outline_extended"}:
            target = int(payload.get("target_total") or 0)
            added = int(payload.get("added_chapters") or 0)
            self._status_label.setText(f"延长全书完成 · 目标 {target} 章 · 新增 {added} 章")
            return
        if status == "succeeded" and result_status == "partial":
            target = int(payload.get("target_total") or 0)
            self._status_label.setText(f"大纲已延长至 {target} 章 · 契约同步需重试")
            show_message_box(
                self,
                title="延长全书部分完成",
                text="outline.json 已延长，但章节契约同步未完成。",
                informative_text="请稍后点击“同步契约”重试。",
                icon=QMessageBox.Icon.Warning,
                actions=(
                    MessageBoxAction(
                        "ok",
                        "知道了",
                        QMessageBox.ButtonRole.AcceptRole,
                        "primary",
                        True,
                    ),
                ),
            )
            return
        self._status_label.setText(f"延长全书失败: {error or result_status or '未知错误'}")

    def _on_sync_job_finished(
        self,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        """Render final sync status from the desktop job manager."""
        payload = result or {}
        self._sync_btn.setText("同步契约")
        self._sync_btn.setEnabled(True)
        self._sync_btn.setToolTip("根据当前 outline 局部刷新章节契约(不修改 prose)")

        result_status = str(payload.get("status") or status or "").lower()
        if status == "succeeded" and result_status == "completed":
            refreshed = int(payload.get("refreshed") or 0)
            stale = int(payload.get("stale_marked") or 0)
            self._status_label.setText(
                f"同步契约完成 · 刷新 {refreshed} 章 · 软过期 {stale} 个产物"
            )
            self._show_sync_status(
                title="同步契约成功",
                state="同步成功",
                message="章节契约已按当前大纲局部刷新。正文、canon、memory、narrative_state 未被修改。",
                result=payload,
                tone="success",
            )
            return

        if status == "succeeded" and result_status == "noop":
            self._status_label.setText("同步契约完成 · 没有检测到需要刷新的章节")
            self._show_sync_status(
                title="无需同步",
                state="无变更",
                message="当前大纲与章节契约缓存一致，没有需要刷新的章节。",
                result=payload,
                tone="neutral",
            )
            return

        if status == "succeeded" and result_status == "requires_manual_scope":
            reason = str(payload.get("manual_scope_reason") or "检测到结构性变化")
            self._status_label.setText("同步契约需要手动确认范围")
            self._show_sync_status(
                title="需要手动选择同步范围",
                state="待确认",
                message=f"{reason}。请在阅读页勾选要同步的章节后再次点击同步契约。",
                result=payload,
                tone="warning",
            )
            return

        message = error or str(payload.get("error") or "后台任务未成功完成")
        self._status_label.setText(f"同步契约失败: {message}")
        self._show_sync_status(
            title="同步契约失败",
            state="失败",
            message=message,
            result=payload,
            tone="error",
        )

    def _show_sync_status(
        self,
        *,
        title: str,
        state: str,
        message: str,
        result: dict[str, Any],
        tone: str,
    ) -> None:
        color = {
            "success": qcolor_hex("status.success.deep"),
            "warning": qcolor_hex("status.warning.alt"),
            "error": qcolor_hex("status.danger.alt"),
            "pending": qcolor_hex("accent.badge"),
            "neutral": qcolor_hex("text.chapter.rail"),
        }.get(tone, qcolor_hex("text.chapter.rail"))
        bg = {
            "success": qcolor_rgba("status.success", 0.10),
            "warning": qcolor_rgba("status.warning", 0.12),
            "error": qcolor_rgba("status.danger", 0.10),
            "pending": qcolor_rgba("accent.primary", 0.08),
            "neutral": qcolor_rgba("border.default", 0.08),
        }.get(tone, qcolor_rgba("border.default", 0.08))
        affected = self._format_sync_numbers(result.get("affected"))
        cascade = self._format_sync_numbers(result.get("cascade"))
        focus = self._format_sync_numbers(result.get("focus"))
        refreshed = html.escape(str(result.get("refreshed", 0)), quote=True)
        stale = html.escape(str(result.get("stale_marked", 0)), quote=True)
        milestones = html.escape(str(result.get("milestones_rebuilt", 0)), quote=True)
        duration = result.get("duration_s")
        duration_text = f"{duration}s" if duration is not None else "进行中"
        self._preview_state_label.setText(state)
        self._diff_preview_renderer.update_content(
            f"<div style='font-family:system-ui,-apple-system,sans-serif;"
            f" color:{qcolor_hex('text.heading.deep')}; padding:18px; line-height:1.72;'>"
            f"<div style='display:inline-block; padding:4px 8px; border-radius:7px;"
            f" background:{bg}; color:{color}; font-size:12px; font-weight:700;'>"
            f"{html.escape(title, quote=True)}</div>"
            f"<div style='margin-top:12px; font-size:13px; font-weight:700;'>"
            f"{html.escape(message, quote=True)}</div>"
            f"<div style='margin-top:12px; font-size:12px; color:{qcolor_hex('text.body.alt')};'>"
            f"变更章节：{affected}<br/>"
            f"直接下游：{cascade}<br/>"
            f"实际刷新：{focus}<br/>"
            f"刷新契约：{refreshed} 章 · 软过期产物：{stale} 个 · "
            f"里程碑索引：{milestones}<br/>"
            f"耗时：{html.escape(duration_text, quote=True)}"
            "</div>"
            f"<div style='margin-top:12px; font-size:12px; color:{qcolor_hex('text.muted')};'>"
            "同步契约不会修改正文、canon、memory 或 narrative_state。"
            "</div></div>"
        )

    def _format_sync_numbers(self, raw: Any) -> str:
        numbers: list[int] = []
        if isinstance(raw, (list, tuple, set)):
            for item in raw:
                try:
                    num = int(item)
                except (TypeError, ValueError):
                    continue
                if num > 0:
                    numbers.append(num)
        if not numbers:
            return "无"
        return f"第 {html.escape(self._format_selected_chapters(numbers), quote=True)} 章"


def _get_storage_or_none(project_dir: Path) -> Any | None:
    """Best-effort filesystem storage for the project directory."""
    try:
        from novel_forge.persistence.filesystem import FileSystemStorage

        project_dir = Path(project_dir)
        if not project_dir.exists():
            return None
        return FileSystemStorage(project_dir.parent)
    except Exception:  # noqa: BLE001
        return None
