"""Workflow form components — split from workflow_components.py.

This module contains the form widgets for short and long project creation,
extracted from the monolithic workflow_components.py file.
"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from PySide6.QtCore import QSignalBlocker, QSize, Qt, Signal
from PySide6.QtGui import QResizeEvent, QWheelEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from novel_forge.core.config import get_settings
from novel_forge.core.domain.story_defaults import DEFAULT_TONE
from novel_forge.desktop.components.dialogs import ask_confirmation_with_checkbox
from novel_forge.desktop.components.sizing import smart_dialog_size
from novel_forge.desktop.jobs import DesktopJobRecord, DesktopJobState
from novel_forge.desktop.pages.workflow.artifacts import (
    StepArtifactDialog,
    _compute_progress,
    _parallel_pairs_for_kind,
    _resolve_step_key,
    _steps_for_kind,
)
from novel_forge.desktop.pages.workflow.form_utils import (
    _field_hint,
    _field_label,
    _genre_edit,
    _hint_edit,
    _lang_combo,
    _param_block,
    _param_spin,
    _set_combo,
    _tone_combo,
)
from novel_forge.desktop.pages.workflow.presets import PresetToolbar
from novel_forge.desktop.progress import display_step_name_for_job
from novel_forge.desktop.widgets import (
    ActionButton,
    CollapsibleSection,
    SectionHeading,
    StepIndicatorRow,
    Surface,
    show_warning_message,
)
from novel_forge.desktop.workflow_requests import build_init_long_request, build_short_request
from novel_forge.desktop.workspace import DesktopWorkspaceSnapshot
from novel_forge.persistence.filesystem import atomic_write_text
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.project_staleness import (
    finalized_chapter_numbers,
)
from novel_forge.pipeline.long.services.init.init_cache import reset_project_for_reinit
from novel_forge.pipeline.steps.blueprint_element_select_step import (
    get_blueprint_element_cards,
    get_blueprint_genre_presets,
    get_related_extension_ids_for_genre,
    recommend_preset_for_genre,
)

_log = logging.getLogger(__name__)

__all__ = ["ShortForm", "LongInitForm"]


_FieldKind = Literal["text", "line", "combo", "title_language"]
_FieldSource = QTextEdit | QLineEdit | QComboBox | tuple[QLineEdit, QComboBox]


def _default_research_enabled_from_settings() -> bool:
    try:
        return bool(get_settings().research_enabled)
    except Exception:  # noqa: BLE001 - settings load must not break form construction
        return False


@dataclass(frozen=True)
class _EditableFieldSpec:
    key: str
    label: str
    kind: _FieldKind
    source: _FieldSource
    placeholder: str = ""
    required: bool = False


def _normalize_summary(text: str) -> str:
    return " ".join(str(text or "").split())


def _compact_summary(text: str, fallback: str, *, limit: int = 92) -> str:
    summary = _normalize_summary(text)
    if not summary:
        return fallback
    if len(summary) <= limit:
        return summary
    return summary[: max(1, limit - 3)].rstrip() + "..."


def _combo_current_label(combo: QComboBox) -> str:
    text = combo.currentText().strip()
    return text or str(combo.currentData() or "").strip()


def _field_summary_text(spec: _EditableFieldSpec) -> str:
    source = spec.source
    if spec.kind == "text" and isinstance(source, QTextEdit):
        return source.toPlainText()
    if spec.kind == "line" and isinstance(source, QLineEdit):
        return source.text()
    if spec.kind == "combo" and isinstance(source, QComboBox):
        return _combo_current_label(source)
    if spec.kind == "title_language" and isinstance(source, tuple):
        title_edit, lang_combo = source
        title = title_edit.text().strip()
        language = _combo_current_label(lang_combo)
        title_text = title or "作品名留空，由 AI 生成"
        return f"{title_text} · {language}"
    return ""


def _field_status_text(spec: _EditableFieldSpec) -> str:
    source = spec.source
    if spec.kind == "title_language" and isinstance(source, tuple):
        title_edit, lang_combo = source
        title = title_edit.text().strip()
        title_state = f"{len(title)} 字" if title else "作品名待生成"
        return f"{title_state} · {_combo_current_label(lang_combo)}"
    text = _normalize_summary(_field_summary_text(spec))
    if text:
        return f"{len(text)} 字"
    return "必填 · 待填写" if spec.required else "可选 · 未填写"


def _copy_combo_items(source: QComboBox, target: QComboBox) -> None:
    for index in range(source.count()):
        target.addItem(source.itemText(index), source.itemData(index))
    target.setCurrentIndex(max(0, source.currentIndex()))


def _connect_source_changed(source: _FieldSource, callback: Callable[[], None]) -> None:
    def _refresh(*_args: Any) -> None:
        callback()

    if isinstance(source, QTextEdit):
        source.textChanged.connect(_refresh)
        return
    if isinstance(source, QLineEdit):
        source.textChanged.connect(_refresh)
        return
    if isinstance(source, QComboBox):
        source.currentIndexChanged.connect(_refresh)
        return
    for child in source:
        _connect_source_changed(child, callback)


def _hide_input(parent: QWidget, widget: QWidget) -> QWidget:
    widget.setParent(parent)
    widget.hide()
    return widget


class _WorkflowFieldCard(QPushButton):
    """Compact field preview button that opens the full-size editor dialog."""

    def __init__(
        self,
        spec: _EditableFieldSpec,
        *,
        open_requested: Callable[[str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._spec = spec
        self._open_requested = open_requested
        self.setObjectName("workflowFieldCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(92)
        self.setMinimumWidth(210)
        self.setAccessibleName(spec.label)
        self.clicked.connect(lambda _checked=False: self._open_requested(self._spec.key))
        self.refresh()

    def refresh(self) -> None:
        marker = " *" if self._spec.required else ""
        status = _field_status_text(self._spec)
        summary = _compact_summary(_field_summary_text(self._spec), self._spec.placeholder)
        self.setText(f"{self._spec.label}{marker}\n{status}\n{summary}")
        self.setToolTip(_field_summary_text(self._spec) or self._spec.placeholder)


class _WorkflowFieldCardSection(QWidget):
    """Two-column field preview section used by short and long workflow forms."""

    def __init__(
        self,
        title: str,
        subtitle: str,
        fields: list[_EditableFieldSpec],
        *,
        open_requested: Callable[[str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.fields = fields
        self.cards: dict[str, _WorkflowFieldCard] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        title_label = QLabel(title)
        title_label.setObjectName("workflowFieldSectionTitle")
        layout.addWidget(title_label)

        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setObjectName("workflowFieldSectionSubtitle")
            subtitle_label.setWordWrap(True)
            layout.addWidget(subtitle_label)

        self._grid = QGridLayout()
        self._grid.setHorizontalSpacing(10)
        self._grid.setVerticalSpacing(10)
        layout.addLayout(self._grid)

        for index, spec in enumerate(fields):
            card = _WorkflowFieldCard(spec, open_requested=open_requested)
            self.cards[spec.key] = card
            self._grid.addWidget(card, index // 2, index % 2)
            _connect_source_changed(spec.source, self.refresh)

    def refresh(self) -> None:
        for card in self.cards.values():
            card.refresh()


class _FixedSizeStack(QStackedWidget):
    """QStackedWidget that reports a stable sizeHint (max across all pages).

    The default QStackedWidget.sizeHint() returns the hint of the *current*
    page only, which causes the parent dialog to resize when switching tabs.
    This subclass returns the maximum sizeHint across all child widgets so
    the dialog stays at a constant size regardless of the active page.
    """

    def sizeHint(self) -> QSize:  # noqa: N802
        max_w = 0
        max_h = 0
        for i in range(self.count()):
            w = self.widget(i)
            if w is not None:
                hint = w.sizeHint()
                max_w = max(max_w, hint.width())
                max_h = max(max_h, hint.height())
        return QSize(max_w, max_h)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()


class _WorkflowFieldEditDialog(QDialog):
    """Large modal editor for workflow field cards."""

    def __init__(
        self,
        fields: list[_EditableFieldSpec],
        *,
        selected_key: str,
        title: str,
        read_only: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._fields = fields
        self._read_only = read_only
        self._editors: dict[str, QWidget | tuple[QLineEdit, QComboBox]] = {}
        self._nav_buttons: dict[str, QPushButton] = {}
        self._applied = False

        self.setObjectName("appDialog")
        self.setWindowTitle(title)
        self.setMinimumSize(860, 560)
        self.resize(*smart_dialog_size(self, 1040, 680))
        self.setModal(True)
        self.setSizeGripEnabled(True)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)

        heading = QLabel(title)
        heading.setObjectName("dialogText")
        heading.setWordWrap(True)
        root.addWidget(heading)

        hint = QLabel(
            "任务运行中仅可查看。"
            if read_only
            else "在这里集中查看和编辑长字段，应用后同步回主表单。"
        )
        hint.setObjectName("dialogInformative")
        hint.setWordWrap(True)
        root.addWidget(hint)

        body = QHBoxLayout()
        body.setSpacing(14)

        nav_col = QVBoxLayout()
        nav_col.setSpacing(8)
        for index, spec in enumerate(fields):
            button = QPushButton(f"{spec.label}{' *' if spec.required else ''}")
            button.setObjectName("fieldDialogNavButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, idx=index: self._select_index(idx))
            self._nav_buttons[spec.key] = button
            nav_col.addWidget(button)
        nav_col.addStretch()
        body.addLayout(nav_col, 0)

        self._stack = _FixedSizeStack()
        self._stack.setObjectName("fieldDialogStack")
        for spec in fields:
            self._stack.addWidget(self._build_editor_page(spec))
        body.addWidget(self._stack, 1)
        root.addLayout(body, 1)

        button_row = QHBoxLayout()
        button_row.addStretch()
        if not read_only:
            cancel_btn = QPushButton("取消")
            cancel_btn.setObjectName("actionButton")
            cancel_btn.setProperty("variant", "secondary")
            cancel_btn.setProperty("compact", True)
            cancel_btn.clicked.connect(self.reject)
            button_row.addWidget(cancel_btn)
        apply_btn = QPushButton("关闭" if read_only else "应用")
        apply_btn.setObjectName("actionButton")
        apply_btn.setProperty("variant", "primary")
        apply_btn.setProperty("compact", True)
        apply_btn.clicked.connect(self.accept if read_only else self._apply_changes)
        button_row.addWidget(apply_btn)
        root.addLayout(button_row)

        selected_index = next(
            (index for index, spec in enumerate(fields) if spec.key == selected_key),
            0,
        )
        self._select_index(selected_index)

    @property
    def applied(self) -> bool:
        return self._applied

    def _build_editor_page(self, spec: _EditableFieldSpec) -> QWidget:
        page = QWidget()
        page.setMinimumHeight(380)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        label = QLabel(f"{spec.label}{' *' if spec.required else ''}")
        label.setObjectName("settingLabel")
        layout.addWidget(label)

        if spec.placeholder:
            placeholder = QLabel(spec.placeholder)
            placeholder.setObjectName("formFieldHint")
            placeholder.setWordWrap(True)
            layout.addWidget(placeholder)

        if spec.kind == "text" and isinstance(spec.source, QTextEdit):
            editor = QTextEdit()
            editor.setObjectName("workflowFieldDialogText")
            editor.setPlaceholderText(spec.placeholder)
            editor.setPlainText(spec.source.toPlainText())
            editor.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
            editor.setReadOnly(self._read_only)
            editor.setMinimumHeight(360)
            layout.addWidget(editor, 1)
            self._editors[spec.key] = editor
        elif spec.kind == "line" and isinstance(spec.source, QLineEdit):
            editor = QLineEdit()
            editor.setObjectName("dialogInput")
            editor.setPlaceholderText(spec.placeholder)
            editor.setText(spec.source.text())
            editor.setReadOnly(self._read_only)
            layout.addWidget(editor)
            layout.addStretch()
            self._editors[spec.key] = editor
        elif spec.kind == "combo" and isinstance(spec.source, QComboBox):
            editor = QComboBox()
            editor.setObjectName("workflowParamInput")
            _copy_combo_items(spec.source, editor)
            editor.setEnabled(not self._read_only)
            layout.addWidget(editor)
            layout.addStretch()
            self._editors[spec.key] = editor
        elif spec.kind == "title_language" and isinstance(spec.source, tuple):
            title_edit, lang_combo = spec.source
            title_input = QLineEdit()
            title_input.setObjectName("dialogInput")
            title_input.setPlaceholderText("作品暂定名，留空由 AI 生成")
            title_input.setText(title_edit.text())
            title_input.setReadOnly(self._read_only)
            layout.addWidget(QLabel("作品名"))
            layout.addWidget(title_input)

            language_input = QComboBox()
            language_input.setObjectName("workflowParamInput")
            _copy_combo_items(lang_combo, language_input)
            language_input.setEnabled(not self._read_only)
            layout.addWidget(QLabel("语言"))
            layout.addWidget(language_input)
            layout.addStretch()
            self._editors[spec.key] = (title_input, language_input)
        else:
            empty = QLabel("该字段暂不可编辑。")
            empty.setObjectName("formFieldHint")
            layout.addWidget(empty)
            layout.addStretch()

        return page

    def _select_index(self, index: int) -> None:
        if index < 0 or index >= len(self._fields):
            return
        self._stack.setCurrentIndex(index)
        active_key = self._fields[index].key
        for key, button in self._nav_buttons.items():
            button.setChecked(key == active_key)

    def _apply_changes(self) -> None:
        if self._read_only:
            self.accept()
            return
        for spec in self._fields:
            editor = self._editors.get(spec.key)
            source = spec.source
            if (
                spec.kind == "text"
                and isinstance(editor, QTextEdit)
                and isinstance(source, QTextEdit)
            ):
                source.setPlainText(editor.toPlainText())
            elif (
                spec.kind == "line"
                and isinstance(editor, QLineEdit)
                and isinstance(source, QLineEdit)
            ):
                source.setText(editor.text())
            elif (
                spec.kind == "combo"
                and isinstance(editor, QComboBox)
                and isinstance(source, QComboBox)
            ):
                source.setCurrentIndex(max(0, editor.currentIndex()))
            elif (
                spec.kind == "title_language"
                and isinstance(editor, tuple)
                and isinstance(source, tuple)
            ):
                title_input, language_input = editor
                title_edit, lang_combo = source
                title_edit.setText(title_input.text())
                _set_combo(lang_combo, str(language_input.currentData() or ""))
        self._applied = True
        self.accept()


class _NoWheelSlider(QSlider):
    """Slider that ignores wheel to prevent accidental value changes while scrolling."""

    def wheelEvent(self, event: QWheelEvent) -> None:
        event.ignore()


class BlueprintElementPreferencePanel(Surface):
    """Manual controls for blueprint element selection (preset + toggle + lock + weight)."""

    def __init__(
        self,
        mode: str,
        parent: QWidget | None = None,
        *,
        show_title: bool = True,
    ) -> None:
        super().__init__("inset", parent)
        self._mode = mode
        self._top_controls_layout_mode: str | None = None
        self._density_profile: str | None = None
        self._row_min_width: int | None = None
        self._slider_min_width: int | None = None
        self._scroll_content: QWidget | None = None
        self._genre_text = ""
        self._suggested_preset_id = ""
        self._extension_cards = get_blueprint_element_cards(tier="extension")
        self._preset_payloads = get_blueprint_genre_presets()
        self._preset_map = {
            str(item.get("preset_id", "")).strip(): item
            for item in self._preset_payloads
            if str(item.get("preset_id", "")).strip()
        }
        self._preset_applied = False
        self._rows: dict[str, dict[str, Any]] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        if show_title:
            title_label = _field_label("叙事要素偏好（手动勾选 / 锁定 / 权重）")
            title_label.setObjectName("settingGroupTitle")
            layout.addWidget(title_label)
        layout.addWidget(
            _field_hint(
                "勾选=建议保留；锁定=覆盖自动选择；权重越高越优先。"
                f"当前模式扩展项上限：{8 if mode == 'long' else 6}。"
            )
        )

        self._top_grid = QGridLayout()
        self._top_grid.setHorizontalSpacing(10)
        self._top_grid.setVerticalSpacing(8)
        self._preset_combo = QComboBox()
        self._preset_combo.setMinimumWidth(170)
        self._preset_combo.setMaximumWidth(300)
        self._preset_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._preset_combo.setToolTip("选择后需点击“套用题材预置”才会生效。")
        self._preset_combo.addItem("不使用预置", "")
        for preset in self._preset_payloads:
            preset_id = str(preset.get("preset_id", "")).strip()
            if not preset_id:
                continue
            label = str(preset.get("label", preset_id))
            self._preset_combo.addItem(label, preset_id)
        self._top_grid.addWidget(self._preset_combo, 0, 0)

        self._apply_btn = QPushButton("套用题材预置")
        self._apply_btn.setObjectName("subModeBtn")
        self._apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_btn.setFixedHeight(30)
        self._apply_btn.setMinimumWidth(116)
        self._apply_btn.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._apply_btn.clicked.connect(self._apply_selected_preset)

        self._apply_suggested_btn = QPushButton("应用建议预置")
        self._apply_suggested_btn.setObjectName("subModeBtn")
        self._apply_suggested_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_suggested_btn.setFixedHeight(30)
        self._apply_suggested_btn.setMinimumWidth(116)
        self._apply_suggested_btn.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        self._apply_suggested_btn.setEnabled(False)
        self._apply_suggested_btn.clicked.connect(self._apply_suggested_preset)

        self._reset_btn = QPushButton("重置偏好")
        self._reset_btn.setObjectName("subModeBtn")
        self._reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reset_btn.setFixedHeight(30)
        self._reset_btn.setMinimumWidth(96)
        self._reset_btn.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._reset_btn.clicked.connect(self._reset_rows)
        layout.addLayout(self._top_grid)
        self._update_top_controls_layout(force=True)

        self._suggestion_label = QLabel("当前题材建议预置：未匹配")
        self._suggestion_label.setObjectName("formFieldHint")
        self._suggestion_label.setWordWrap(True)
        layout.addWidget(self._suggestion_label)

        option_row = QHBoxLayout()
        option_row.setSpacing(14)

        self._related_only = QCheckBox("只看当前题材相关要素")
        self._related_only.setObjectName("blueprintPanelToggle")
        self._related_only.setToolTip("按当前题材（和建议预置）过滤扩展要素列表。")
        self._related_only.toggled.connect(self._refresh_row_visibility)
        option_row.addWidget(self._related_only, 0, Qt.AlignmentFlag.AlignLeft)
        option_row.addStretch()
        layout.addLayout(option_row)

        manual_row = QHBoxLayout()
        manual_row.setSpacing(14)
        self._manual_override = QCheckBox("手动覆盖自动选择结果")
        self._manual_override.setObjectName("blueprintPanelToggle")
        self._manual_override.setToolTip("开启后，仅保留你勾选（或锁定保留）的扩展要素。")
        manual_row.addWidget(self._manual_override, 0, Qt.AlignmentFlag.AlignLeft)
        manual_row.addStretch()
        layout.addLayout(manual_row)

        scroll = QScrollArea()
        scroll.setObjectName("blueprintElementScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumHeight(280)
        scroll_content = QWidget()
        self._scroll_content = scroll_content
        scroll_content.setUpdatesEnabled(False)
        rows_layout = QVBoxLayout(scroll_content)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(6)

        for card in self._extension_cards:
            row_widget = QWidget()
            row_widget.setObjectName("blueprintElementRow")
            row_layout = QVBoxLayout(row_widget)
            row_layout.setContentsMargins(12, 10, 12, 10)
            row_layout.setSpacing(5)

            line_row = QHBoxLayout()
            line_row.setSpacing(8)

            name_label = QLabel(card.name)
            name_label.setObjectName("blueprintElementName")
            name_label.setWordWrap(True)
            name_label.setMinimumWidth(0)
            name_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            name_label.setToolTip(
                f"{card.description}\n\n价值：{card.rationale}\n推荐题材："
                f"{'、'.join(card.recommended_genres) if card.recommended_genres else '通用'}"
            )
            line_row.addWidget(name_label, 2)

            category_label = QLabel(card.category)
            category_label.setObjectName("blueprintCategoryBadge")
            category_label.setToolTip("题材标签")
            line_row.addWidget(category_label, 0, Qt.AlignmentFlag.AlignVCenter)

            enabled = QCheckBox("启用")
            enabled.setObjectName("blueprintRowToggle")
            enabled.setToolTip("建议保留该要素（与“锁定”同时勾选可强制保留）。")
            line_row.addWidget(enabled, 0, Qt.AlignmentFlag.AlignVCenter)
            locked = QCheckBox("锁定")
            locked.setObjectName("blueprintRowToggle")
            locked.setToolTip("锁定后覆盖自动选择：勾选启用=强制保留，不勾选启用=强制排除。")
            line_row.addWidget(locked, 0, Qt.AlignmentFlag.AlignVCenter)

            weight_meta = QLabel("权重")
            weight_meta.setObjectName("blueprintWeightMeta")
            weight_meta.setFixedWidth(28)
            line_row.addWidget(weight_meta, 0, Qt.AlignmentFlag.AlignVCenter)

            weight = _NoWheelSlider(Qt.Orientation.Horizontal)
            weight.setObjectName("blueprintWeightSlider")
            weight.setRange(0, 100)
            weight.setValue(50)
            weight.setMinimumWidth(120)
            line_row.addWidget(weight, 3)

            weight_label = QLabel("50")
            weight_label.setObjectName("blueprintWeightValue")
            weight_label.setFixedWidth(28)
            weight_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            line_row.addWidget(weight_label, 0, Qt.AlignmentFlag.AlignVCenter)

            def _sync_weight(value: int, target: QLabel = weight_label) -> None:
                target.setText(str(value))

            weight.valueChanged.connect(_sync_weight)
            row_layout.addLayout(line_row)

            rows_layout.addWidget(row_widget)

            self._rows[card.element_id] = {
                "card": card,
                "widget": row_widget,
                "enabled": enabled,
                "locked": locked,
                "weight": weight,
                "weight_label": weight_label,
                "line_layout": line_row,
                "row_layout": row_layout,
            }

        rows_layout.addStretch()
        scroll_content.setUpdatesEnabled(True)
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)
        self.setObjectName("blueprintPreferencePanel")
        self._update_row_width_constraints(force=True)
        self._update_density_profile(force=True)

    def _set_row_state(
        self,
        element_id: str,
        *,
        enabled: bool | None = None,
        locked: bool | None = None,
        weight: float | None = None,
        refresh_visibility: bool = True,
    ) -> None:
        row = self._rows.get(element_id)
        if row is None:
            return
        if enabled is not None:
            with QSignalBlocker(row["enabled"]):
                row["enabled"].setChecked(bool(enabled))
        if locked is not None:
            with QSignalBlocker(row["locked"]):
                row["locked"].setChecked(bool(locked))
        if weight is not None:
            with QSignalBlocker(row["weight"]):
                row["weight"].setValue(max(0, min(100, int(round(weight)))))
            cast_label = row.get("weight_label")
            if isinstance(cast_label, QLabel):
                cast_label.setText(str(row["weight"].value()))
        if refresh_visibility and self._related_only.isChecked():
            self._refresh_row_visibility()

    def _reset_rows(self) -> None:
        self.setUpdatesEnabled(False)
        try:
            self._preset_combo.setCurrentIndex(0)
            self._manual_override.setChecked(False)
            self._related_only.setChecked(False)
            self._preset_applied = False
            for element_id in self._rows:
                self._set_row_state(
                    element_id,
                    enabled=False,
                    locked=False,
                    weight=50.0,
                    refresh_visibility=False,
                )
        finally:
            self.setUpdatesEnabled(True)
        self._refresh_row_visibility()

    def _apply_selected_preset(self) -> None:
        preset_id = str(self._preset_combo.currentData() or "").strip()
        self.apply_preset(preset_id)

    def _apply_suggested_preset(self) -> None:
        if not self._suggested_preset_id:
            return
        idx = self._preset_combo.findData(self._suggested_preset_id)
        if idx >= 0:
            self._preset_combo.setCurrentIndex(idx)
        self.apply_preset(self._suggested_preset_id)

    def apply_preset(self, preset_id: str) -> None:
        self.setUpdatesEnabled(False)
        try:
            for element_id in self._rows:
                self._set_row_state(
                    element_id,
                    enabled=False,
                    locked=False,
                    weight=50.0,
                    refresh_visibility=False,
                )

            self._preset_applied = bool(preset_id)
            if not preset_id:
                return
            preset = self._preset_map.get(preset_id)
            if preset is None:
                self._preset_applied = False
                return

            for element_id, weight in dict(preset.get("default_weights") or {}).items():
                self._set_row_state(element_id, weight=float(weight), refresh_visibility=False)
            for element_id in list(preset.get("default_enabled") or []):
                self._set_row_state(element_id, enabled=True, refresh_visibility=False)
        finally:
            self.setUpdatesEnabled(True)
        self._refresh_row_visibility()

    def set_genre_context(self, genre_text: str) -> None:
        self._genre_text = " ".join(str(genre_text or "").split()).strip()
        previous_suggested = self._suggested_preset_id
        suggested = recommend_preset_for_genre(self._genre_text)
        self._suggested_preset_id = suggested
        if suggested:
            preset = self._preset_map.get(suggested, {})
            label = str(preset.get("label", suggested))
            desc = str(preset.get("description", "")).strip()
            self._suggestion_label.setText(
                f"当前题材建议预置：{label}（仅建议，不自动套用）" + (f" · {desc}" if desc else "")
            )
            self._apply_suggested_btn.setEnabled(True)
            if not self._preset_applied:
                current_preset = str(self._preset_combo.currentData() or "").strip()
                if not current_preset or current_preset == previous_suggested:
                    idx = self._preset_combo.findData(suggested)
                    if idx >= 0:
                        self._preset_combo.setCurrentIndex(idx)
        else:
            self._suggestion_label.setText("当前题材暂未匹配预置，可手动选择。")
            self._apply_suggested_btn.setEnabled(False)
            if not self._preset_applied:
                current_preset = str(self._preset_combo.currentData() or "").strip()
                if current_preset and current_preset == previous_suggested:
                    self._preset_combo.setCurrentIndex(0)
        self._refresh_row_visibility()

    def _refresh_row_visibility(self) -> None:
        if not self._related_only.isChecked():
            for row in self._rows.values():
                row_widget = row.get("widget")
                if isinstance(row_widget, QWidget):
                    if not row_widget.isVisible():
                        row_widget.setVisible(True)
            return

        current_preset = (
            str(self._preset_combo.currentData() or "").strip()
            if self._preset_applied
            else self._suggested_preset_id
        )
        related_ids = set(
            get_related_extension_ids_for_genre(
                self._genre_text,
                preset_id=current_preset,
            )
        )
        if not related_ids:
            for row in self._rows.values():
                row_widget = row.get("widget")
                if isinstance(row_widget, QWidget):
                    if not row_widget.isVisible():
                        row_widget.setVisible(True)
            return

        for element_id, row in self._rows.items():
            enabled_checked = bool(row["enabled"].isChecked())
            locked_checked = bool(row["locked"].isChecked())
            should_show = element_id in related_ids or enabled_checked or locked_checked
            row_widget = row.get("widget")
            if isinstance(row_widget, QWidget):
                if row_widget.isVisible() != should_show:
                    row_widget.setVisible(should_show)

    def collect_preferences(self) -> dict[str, Any]:
        preset_id = (
            str(self._preset_combo.currentData() or "").strip() if self._preset_applied else ""
        )
        items: list[dict[str, Any]] = []
        for element_id, row in self._rows.items():
            enabled_checked = bool(row["enabled"].isChecked())
            locked_checked = bool(row["locked"].isChecked())
            weight_value = float(row["weight"].value())

            enabled_value: bool | None
            if enabled_checked:
                enabled_value = True
            elif locked_checked:
                enabled_value = False
            else:
                enabled_value = None

            if enabled_value is True or locked_checked or abs(weight_value - 50.0) >= 0.1:
                items.append(
                    {
                        "element_id": element_id,
                        "enabled": enabled_value,
                        "locked": locked_checked,
                        "weight": weight_value,
                    }
                )

        return {
            "preset_id": preset_id,
            "manual_override": bool(self._manual_override.isChecked()),
            "items": items,
        }

    def fill_preferences(self, payload: dict[str, Any] | None) -> None:
        self._reset_rows()
        if not isinstance(payload, dict):
            return

        self.setUpdatesEnabled(False)
        try:
            preset_id = str(payload.get("preset_id", "") or "").strip()
            if preset_id:
                combo_index = self._preset_combo.findData(preset_id)
                if combo_index >= 0:
                    self._preset_combo.setCurrentIndex(combo_index)
                self.apply_preset(preset_id)

            self._manual_override.setChecked(bool(payload.get("manual_override", False)))
            for raw in payload.get("items", []) if isinstance(payload.get("items"), list) else []:
                if not isinstance(raw, dict):
                    continue
                element_id = str(raw.get("element_id", "")).strip()
                if element_id not in self._rows:
                    continue
                enabled_raw = raw.get("enabled")
                enabled = True if enabled_raw is True else False if enabled_raw is False else None
                locked = bool(raw.get("locked", False))
                weight = raw.get("weight", 50.0)
                self._set_row_state(
                    element_id,
                    enabled=enabled,
                    locked=locked,
                    weight=float(weight),
                    refresh_visibility=False,
                )
        finally:
            self.setUpdatesEnabled(True)
        self._refresh_row_visibility()

    def _update_top_controls_layout(self, *, force: bool = False) -> None:
        width = max(0, self.width())
        if width < 860:
            mode = "stack"
        elif width < 1120:
            mode = "wrap"
        else:
            mode = "inline"
        if not force and self._top_controls_layout_mode == mode:
            return
        self._top_controls_layout_mode = mode
        while self._top_grid.count():
            self._top_grid.takeAt(0)
        for idx in range(4):
            self._top_grid.setColumnStretch(idx, 0)

        if mode == "stack":
            self._preset_combo.setMinimumWidth(0)
            self._top_grid.setColumnStretch(0, 1)
            self._top_grid.addWidget(self._preset_combo, 0, 0)
            self._top_grid.addWidget(self._apply_btn, 1, 0)
            self._top_grid.addWidget(self._apply_suggested_btn, 2, 0)
            self._top_grid.addWidget(self._reset_btn, 3, 0)
            return

        self._preset_combo.setMinimumWidth(170)
        if mode == "wrap":
            self._top_grid.setColumnStretch(0, 1)
            self._top_grid.setColumnStretch(1, 1)
            self._top_grid.addWidget(self._preset_combo, 0, 0, 1, 2)
            self._top_grid.addWidget(self._apply_btn, 1, 0)
            self._top_grid.addWidget(self._apply_suggested_btn, 1, 1)
            self._top_grid.addWidget(self._reset_btn, 2, 0, 1, 2)
            return

        self._top_grid.setColumnStretch(0, 1)
        self._top_grid.addWidget(self._preset_combo, 0, 0)
        self._top_grid.addWidget(self._apply_btn, 0, 1)
        self._top_grid.addWidget(self._apply_suggested_btn, 0, 2)
        self._top_grid.addWidget(self._reset_btn, 0, 3)

    def _update_row_width_constraints(self, *, force: bool = False) -> None:
        width = max(0, self.width())
        row_min_width = 0
        slider_min_width = min(170, max(96, int(max(width, 520) * 0.16)))

        if (
            not force
            and self._row_min_width == row_min_width
            and self._slider_min_width == slider_min_width
        ):
            return

        self._row_min_width = row_min_width
        self._slider_min_width = slider_min_width
        if self._scroll_content is None:
            return
        self._scroll_content.setMinimumWidth(row_min_width)
        for row in self._rows.values():
            row["widget"].setMinimumWidth(row_min_width)
            row["weight"].setMinimumWidth(slider_min_width)

    def _update_density_profile(self, *, force: bool = False) -> None:
        width = max(0, self.width())
        if width < 860:
            density = "compact"
        elif width > 1380:
            density = "roomy"
        else:
            density = "regular"
        if not force and self._density_profile == density:
            return
        self._density_profile = density
        self.setProperty("densityProfile", density)
        self._related_only.setProperty("densityProfile", density)
        self._manual_override.setProperty("densityProfile", density)

        if density == "compact":
            row_margins = (10, 8, 10, 8)
            row_spacing = 4
            line_spacing = 7
        elif density == "roomy":
            row_margins = (14, 11, 14, 11)
            row_spacing = 6
            line_spacing = 10
        else:
            row_margins = (12, 10, 12, 10)
            row_spacing = 5
            line_spacing = 8

        for row in self._rows.values():
            row_widget = row.get("widget")
            if isinstance(row_widget, QWidget):
                row_widget.setProperty("densityProfile", density)
            row_layout = row.get("row_layout")
            if isinstance(row_layout, QVBoxLayout):
                row_layout.setContentsMargins(*row_margins)
                row_layout.setSpacing(row_spacing)
            line_layout = row.get("line_layout")
            if isinstance(line_layout, QHBoxLayout):
                line_layout.setSpacing(line_spacing)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._update_top_controls_layout()
        self._update_row_width_constraints()
        self._update_density_profile()


class _DeferredBlueprintPreferences:
    """Keep blueprint preference state without constructing its large widget tree.

    The preference editor contains hundreds of controls.  Most users never open
    it during a normal launch, so forms retain a lightweight payload until the
    containing collapsible section is expanded for the first time.
    """

    _EMPTY_PAYLOAD: dict[str, Any] = {
        "preset_id": "",
        "manual_override": False,
        "items": [],
    }

    def __init__(self, mode: str) -> None:
        self._mode = mode
        self._panel: BlueprintElementPreferencePanel | None = None
        self._payload = deepcopy(self._EMPTY_PAYLOAD)
        self._genre_text = ""
        self._enabled = True

    @property
    def is_built(self) -> bool:
        return self._panel is not None

    def build_into(self, layout: QVBoxLayout) -> None:
        if self._panel is not None:
            return
        panel = BlueprintElementPreferencePanel(mode=self._mode, show_title=False)
        panel.fill_preferences(deepcopy(self._payload))
        panel.set_genre_context(self._genre_text)
        panel.setEnabled(self._enabled)
        layout.addWidget(panel)
        self._panel = panel

    def collect_preferences(self) -> dict[str, Any]:
        if self._panel is not None:
            self._payload = self._panel.collect_preferences()
        return deepcopy(self._payload)

    def fill_preferences(self, payload: dict[str, Any] | None) -> None:
        self._payload = (
            deepcopy(payload) if isinstance(payload, dict) else deepcopy(self._EMPTY_PAYLOAD)
        )
        if self._panel is not None:
            self._panel.fill_preferences(deepcopy(self._payload))

    def set_genre_context(self, genre_text: str) -> None:
        self._genre_text = str(genre_text or "")
        if self._panel is not None:
            self._panel.set_genre_context(self._genre_text)

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802 - mirrors QWidget API
        self._enabled = bool(enabled)
        if self._panel is not None:
            self._panel.setEnabled(self._enabled)


# ──────────────────────────────────────────────────────────────────────────────
# Short Form
# ──────────────────────────────────────────────────────────────────────────────


class ShortForm(Surface):
    """Form for creating short stories."""

    submitted = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("panel", parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        # This panel hosts several large collapsible sections. Keeping it on the
        # regular paint path avoids off-screen shadow compositing trails on macOS.
        self.setGraphicsEffect(None)  # type: ignore[arg-type]  # PySide accepts clearing with None.
        self._storage_root: Path | None = None
        self._current_job: DesktopJobRecord | None = None
        self._field_inputs_enabled = True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        layout.addWidget(
            SectionHeading(
                "短篇创作",
                "填写基础参数后一键发起 — 规格确认 → 节拍生成 → 初稿 → 编辑×N → 质量评估。",
            )
        )

        # ── Preset toolbar ────────────────────────────────────────
        self._toolbar = PresetToolbar("short", payload_provider=self._collect)
        self._toolbar.fill_requested.connect(self._fill)
        layout.addWidget(self._toolbar)
        layout.addWidget(
            _field_hint(
                "可通过工具栏载入预设、导入 JSON、导出模板/当前配置，或让 AI 随机生成/润色创作要素。"
            )
        )

        self._theme = _hint_edit(
            "例：一个人在关键选择中发现旧秩序的裂缝，并为此付出代价……",
            min_height=88,
            max_height=120,
        )
        _hide_input(self, self._theme)

        self._characters_hint = _hint_edit(
            "例：主角A（外在目标明确、内在有缺口）；角色B（与主角目标相冲突）",
            min_height=64,
            max_height=88,
        )
        _hide_input(self, self._characters_hint)

        self._world_hint = _hint_edit(
            "例：当代城市、架空王国、近未来空间站或任意自定义舞台",
            min_height=64,
            max_height=88,
        )
        _hide_input(self, self._world_hint)

        self._conflict_hint = _hint_edit(
            "例：外部阻力、内在困境与关系代价同时推进",
            min_height=64,
            max_height=88,
        )
        _hide_input(self, self._conflict_hint)

        self._core_fields = [
            _EditableFieldSpec(
                "theme",
                "故事主题",
                "text",
                self._theme,
                "主题是创作核心，描述越具体，生成质量越高。",
                required=True,
            ),
            _EditableFieldSpec(
                "characters_hint",
                "人物提示",
                "text",
                self._characters_hint,
                "例：主角A（外在目标明确、内在有缺口）；角色B（与主角目标相冲突）",
            ),
            _EditableFieldSpec(
                "world_hint",
                "世界观 / 场景提示",
                "text",
                self._world_hint,
                "例：当代城市、架空王国、近未来空间站或任意自定义舞台",
            ),
            _EditableFieldSpec(
                "conflict_hint",
                "核心冲突提示",
                "text",
                self._conflict_hint,
                "例：外部阻力、内在困境与关系代价同时推进",
            ),
        ]
        self._core_field_section = _WorkflowFieldCardSection(
            "核心故事",
            "点击字段按钮可在大窗口中查看和编辑；主页面只保留摘要，方便扫配置。",
            self._core_fields,
            open_requested=self._open_core_field_dialog,
        )
        layout.addWidget(self._core_field_section)

        params_row = QHBoxLayout()
        params_row.setSpacing(14)
        self._genre = _genre_edit()
        params_row.addLayout(_param_block("题材", self._genre))
        self._tone = _tone_combo(DEFAULT_TONE)
        params_row.addLayout(_param_block("基调", self._tone))
        self._length = _param_spin(500, 50000, 3200, " 字")
        params_row.addLayout(_param_block("目标字数", self._length))
        params_row.addStretch()
        layout.addLayout(params_row)

        params_row2 = QHBoxLayout()
        params_row2.setSpacing(14)
        self._rounds = _param_spin(0, 10, 2)
        params_row2.addLayout(_param_block("最多修订轮次", self._rounds))
        segment_trigger_default = get_settings().short_segment_trigger_words
        self._segment_trigger = _param_spin(1500, 50000, segment_trigger_default, " 字")
        params_row2.addLayout(_param_block("开始分段字数", self._segment_trigger))
        self._writing_mode = QComboBox()
        self._writing_mode.addItem("自动", "auto")
        self._writing_mode.addItem("整篇写作", "whole_chapter")
        self._writing_mode.addItem("分段写作", "scene_level")
        params_row2.addLayout(_param_block("写作模式", self._writing_mode))
        params_row2.addStretch()
        layout.addLayout(params_row2)
        layout.addWidget(
            _field_hint("自适应模式按问题决定 0–2 轮修订；该值是上限。自动模式会按目标字数启用多段起稿。")
        )

        self._short_research_enabled = QCheckBox("本次启用章节资料检索")
        self._short_research_provider = QComboBox()
        for value, label in (
            ("auto", "自动"),
            ("tavily", "Tavily"),
            ("brave", "Brave"),
            ("searxng", "SearXNG"),
            ("http_json", "HTTP JSON"),
            ("bailian_web_search", "百炼 Web Search"),
            ("mcp_search", "MCP Search"),
        ):
            self._short_research_provider.addItem(label, value)
        self._short_research_provider.setMinimumWidth(168)
        self._short_research_query_hint = _hint_edit(
            "可选：只填写本篇必须核实的事实方向；空白时不会为事实考据强行联网。",
            min_height=52,
            max_height=76,
        )
        short_research_body = QWidget()
        short_research_layout = QVBoxLayout(short_research_body)
        short_research_layout.setContentsMargins(0, 0, 0, 0)
        short_research_layout.setSpacing(8)
        short_research_top = QHBoxLayout()
        short_research_top.addWidget(self._short_research_enabled)
        short_research_top.addStretch()
        short_research_top.addLayout(_param_block("本次检索后端", self._short_research_provider))
        short_research_layout.addLayout(short_research_top)
        short_research_layout.addWidget(self._short_research_query_hint)
        short_research_layout.addWidget(
            _field_hint(
                "整次短篇最多构造一个证据包；事实与抽象灵感会分权投影，"
                "外部资料不得改写人物、POV、结局或架空规则。"
            )
        )
        self._short_research_section = CollapsibleSection("联网资料与低频灵感", expanded=False)
        self._short_research_section.body_layout.addWidget(short_research_body)
        layout.addWidget(self._short_research_section)

        self._title = QLineEdit()
        self._title.setPlaceholderText("作品暂定名，留空由 AI 生成")
        _hide_input(self, self._title)
        self._language = _lang_combo()
        _hide_input(self, self._language)

        self._pov_hint = _hint_edit("例：第三人称双主角限知；非对话正文禁用我/我们")
        _hide_input(self, self._pov_hint)

        self._opening_style = _hint_edit("开篇方式，例：以训练事故现场切入")
        _hide_input(self, self._opening_style)
        self._ending_style = _hint_edit("结尾方式，例：甜向余韵收束")
        _hide_input(self, self._ending_style)

        self._extra = _hint_edit("例：减少解释性旁白，强化动作和感官细节")
        _hide_input(self, self._extra)

        self._project_id = QLineEdit()
        self._project_id.setPlaceholderText("留空自动生成（用作保存目录名）")
        _hide_input(self, self._project_id)

        self._advanced_fields = [
            _EditableFieldSpec(
                "title_language",
                "作品名 / 语言",
                "title_language",
                (self._title, self._language),
                "作品名可留空交给 AI；语言决定生成文本的主要输出语种。",
            ),
            _EditableFieldSpec(
                "pov_hint",
                "叙事视角",
                "text",
                self._pov_hint,
                "例：第三人称双主角限知；非对话正文禁用我/我们",
            ),
            _EditableFieldSpec(
                "opening_style",
                "开篇方式",
                "text",
                self._opening_style,
                "开篇方式，例：以训练事故现场切入",
            ),
            _EditableFieldSpec(
                "ending_style",
                "结尾方式",
                "text",
                self._ending_style,
                "结尾方式，例：甜向余韵收束",
            ),
            _EditableFieldSpec(
                "extra_instructions",
                "额外创作指令",
                "text",
                self._extra,
                "例：减少解释性旁白，强化动作和感官细节",
            ),
            _EditableFieldSpec(
                "project_id",
                "项目 ID",
                "line",
                self._project_id,
                "留空自动生成（用作保存目录名）",
            ),
        ]
        self._advanced_field_section = _WorkflowFieldCardSection(
            "高级设定",
            "视角、开篇、结尾和项目标识集中在这里；不需要时保持空白即可。",
            self._advanced_fields,
            open_requested=self._open_advanced_field_dialog,
        )
        layout.addWidget(self._advanced_field_section)

        self._blueprint_preferences = _DeferredBlueprintPreferences(mode="short")
        self._blueprint_preferences_section = CollapsibleSection(
            "叙事要素偏好（手动勾选 / 锁定 / 权重）",
            expanded=False,
            lazy_body_builder=self._blueprint_preferences.build_into,
        )
        layout.addWidget(self._blueprint_preferences_section)
        self._genre.textChanged.connect(self._on_genre_changed)
        self._on_genre_changed(self._genre.text())
        self._refresh_field_cards()

        button_row = QHBoxLayout()
        save_preset_btn = ActionButton("存为预设", variant="secondary")
        save_preset_btn.clicked.connect(self._save_preset)
        button_row.addWidget(save_preset_btn)
        self._clear_btn = ActionButton("清空表单", variant="secondary")
        self._clear_btn.clicked.connect(self.reset_form)
        button_row.addWidget(self._clear_btn)
        launch_button = ActionButton("发起短篇创作 →")
        launch_button.clicked.connect(self._submit)
        button_row.addWidget(launch_button)
        button_row.addStretch()
        layout.addLayout(button_row)

        self._preview = StepIndicatorRow(_steps_for_kind("run_short"))
        self._preview.reset()
        self._preview.step_clicked.connect(self._on_step_clicked)

    def _make_field_editor_dialog(
        self,
        fields: list[_EditableFieldSpec],
        selected_key: str,
        title: str,
    ) -> _WorkflowFieldEditDialog:
        return _WorkflowFieldEditDialog(
            fields,
            selected_key=selected_key,
            title=title,
            read_only=not self._field_inputs_enabled,
            parent=self.window(),
        )

    def _open_core_field_dialog(self, selected_key: str) -> None:
        dialog = self._make_field_editor_dialog(self._core_fields, selected_key, "核心故事")
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.applied:
            self._refresh_field_cards()

    def _open_advanced_field_dialog(self, selected_key: str) -> None:
        dialog = self._make_field_editor_dialog(self._advanced_fields, selected_key, "高级设定")
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.applied:
            self._refresh_field_cards()

    def _refresh_field_cards(self) -> None:
        self._core_field_section.refresh()
        self._advanced_field_section.refresh()

    def set_storage_root(self, root: Path) -> None:
        self._storage_root = root

    def reset_form(self) -> None:
        """Clear all user-entered text and reset parameters to their defaults."""
        self._theme.setPlainText("")
        self._characters_hint.setPlainText("")
        self._world_hint.setPlainText("")
        self._conflict_hint.setPlainText("")
        self._pov_hint.setPlainText("")
        self._opening_style.setPlainText("")
        self._ending_style.setPlainText("")
        self._extra.setPlainText("")
        self._title.setText("")
        self._project_id.setText("")
        self._genre.setText("")
        _set_combo(self._tone, DEFAULT_TONE)
        self._length.setValue(3200)
        self._rounds.setValue(2)
        self._segment_trigger.setValue(get_settings().short_segment_trigger_words)
        _set_combo(self._writing_mode, "auto")
        self._short_research_enabled.setChecked(False)
        _set_combo(self._short_research_provider, "auto")
        self._short_research_query_hint.setPlainText("")
        self._blueprint_preferences.fill_preferences(None)
        self._toolbar.unbind_preset()
        self._refresh_field_cards()
        # Clear modified flags and delete any saved draft
        for te in self.findChildren(QTextEdit):
            doc = te.document()
            if doc is not None:
                doc.setModified(False)
        for le in self.findChildren(QLineEdit):
            le.setModified(False)
        if self._storage_root is not None:
            draft = self._storage_root / ".presets" / ".draft" / "_autosave_short.json"
            try:
                draft.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass

    def save_draft(self, path: Path) -> bool:
        """Save current form state to *path* as JSON. Returns True on success."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = self._collect()
            atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))
            return True
        except Exception as exc:  # noqa: BLE001
            _log.warning("短篇草稿保存失败: %s", exc)
            return False

    def restore_draft(self, path: Path) -> bool:
        """Load form state from *path*. Returns True if data was restored."""
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not data.get("theme"):
                return False
            self._toolbar.unbind_preset()
            self._fill(data)
            # Reset modified flags so a fresh restore doesn't immediately
            # trigger the "unsaved changes" warning.
            for te in self.findChildren(QTextEdit):
                doc = te.document()
                if doc is not None:
                    doc.setModified(False)
            for le in self.findChildren(QLineEdit):
                le.setModified(False)
            return True
        except Exception as exc:  # noqa: BLE001
            _log.warning("短篇草稿恢复失败: %s", exc)
            return False

    def _on_step_clicked(self, index: int) -> None:
        job = self._current_job
        project_dir = None
        if job and job.project_id and self._storage_root:
            project_dir = self._storage_root / job.project_id
        nav_target = StepArtifactDialog.show_for_step(
            kind="run_short",
            step_key=self._preview.step_key(index),
            step_label=self._preview.step_label(index),
            project_dir=project_dir,
            parent=self.window(),
        )
        if nav_target:
            win = self.window()
            if hasattr(win, "switch_page"):
                win.switch_page(nav_target)

    def _collect(self) -> dict[str, Any]:
        """Collect all form fields into a dict (for preset save / export)."""
        return {
            "theme": self._theme.toPlainText(),
            "genre": self._genre.text().strip(),
            "tone": self._tone.currentData() or DEFAULT_TONE,
            "length_target": self._length.value(),
            "max_edit_rounds": self._rounds.value(),
            "segment_trigger_words": self._segment_trigger.value(),
            "writing_mode": self._writing_mode.currentData() or "auto",
            "title": self._title.text(),
            "language": self._language.currentData() or "zh",
            "characters_hint": self._characters_hint.toPlainText(),
            "world_hint": self._world_hint.toPlainText(),
            "conflict_hint": self._conflict_hint.toPlainText(),
            "pov_hint": self._pov_hint.toPlainText(),
            "opening_style": self._opening_style.toPlainText(),
            "ending_style": self._ending_style.toPlainText(),
            "extra_instructions": self._extra.toPlainText(),
            "project_id": self._project_id.text(),
            "blueprint_element_preferences": self._blueprint_preferences.collect_preferences(),
            "research_enabled": self._short_research_enabled.isChecked(),
            "research_provider": self._short_research_provider.currentData() or "auto",
            "research_query_hint": self._short_research_query_hint.toPlainText(),
        }

    def _fill(self, data: dict[str, Any]) -> None:
        """Fill form fields from a data dict (for preset load / import / AI gen)."""
        if "theme" in data:
            self._theme.setPlainText(str(data["theme"]))
        if "genre" in data:
            self._genre.setText(str(data["genre"]))
        if "tone" in data:
            _set_combo(self._tone, str(data["tone"]))
        if "length_target" in data:
            self._length.setValue(int(data["length_target"]))
        if "max_edit_rounds" in data:
            self._rounds.setValue(int(data["max_edit_rounds"]))
        if "segment_trigger_words" in data:
            self._segment_trigger.setValue(int(data["segment_trigger_words"]))
        if "writing_mode" in data:
            _set_combo(self._writing_mode, str(data["writing_mode"]))
        if "title" in data:
            self._title.setText(str(data["title"]))
        if "language" in data:
            _set_combo(self._language, str(data["language"]))
        if "characters_hint" in data:
            self._characters_hint.setPlainText(str(data["characters_hint"]))
        if "world_hint" in data:
            self._world_hint.setPlainText(str(data["world_hint"]))
        if "conflict_hint" in data:
            self._conflict_hint.setPlainText(str(data["conflict_hint"]))
        if "pov_hint" in data:
            self._pov_hint.setPlainText(str(data["pov_hint"]))
        if "opening_style" in data:
            self._opening_style.setPlainText(str(data["opening_style"]))
        if "ending_style" in data:
            self._ending_style.setPlainText(str(data["ending_style"]))
        if "extra_instructions" in data:
            self._extra.setPlainText(str(data["extra_instructions"]))
        if "project_id" in data:
            self._project_id.setText(str(data["project_id"]))
        if "blueprint_element_preferences" in data:
            self._blueprint_preferences.fill_preferences(data.get("blueprint_element_preferences"))
        if "research_enabled" in data:
            self._short_research_enabled.setChecked(bool(data["research_enabled"]))
        if "research_provider" in data:
            _set_combo(self._short_research_provider, str(data["research_provider"]))
        if "research_query_hint" in data:
            self._short_research_query_hint.setPlainText(str(data["research_query_hint"]))
        self._on_genre_changed(self._genre.text())
        self._refresh_field_cards()

    def _save_preset(self) -> None:
        self._toolbar.do_save(self._collect())

    def _on_genre_changed(self, genre_text: str) -> None:
        self._blueprint_preferences.set_genre_context(genre_text)

    def export_template(self) -> None:
        self._toolbar.export_template()

    def _set_inputs_enabled(self, enabled: bool) -> None:
        """Enable or disable all input fields during job execution."""
        self._field_inputs_enabled = enabled
        for widget in (
            self._theme,
            self._characters_hint,
            self._world_hint,
            self._conflict_hint,
            self._genre,
            self._tone,
            self._length,
            self._rounds,
            self._segment_trigger,
            self._writing_mode,
            self._title,
            self._language,
            self._pov_hint,
            self._opening_style,
            self._ending_style,
            self._extra,
            self._project_id,
            self._toolbar,
            self._blueprint_preferences,
            self._short_research_enabled,
            self._short_research_provider,
            self._short_research_query_hint,
            self._clear_btn,
        ):
            widget.setEnabled(enabled)

    def update_progress(self, job: DesktopJobRecord | None) -> None:
        self._current_job = job
        self._preview.reset()
        if job is None:
            self._set_inputs_enabled(True)
            return
        for event in job.events:
            self._preview.update_step(_resolve_step_key(job.kind, event.step))
        is_active = job.status in {DesktopJobState.QUEUED, DesktopJobState.RUNNING}
        self._set_inputs_enabled(not is_active)
        if job.status == DesktopJobState.SUCCEEDED:
            self._preview.mark_done()
            return
        if job.status == DesktopJobState.FAILED:
            self._preview.mark_failed(_resolve_step_key(job.kind, job.current_step or ""))
            return

    def _submit(self) -> None:
        try:
            request = build_short_request(
                project_id=self._project_id.text(),
                theme=self._theme.toPlainText(),
                genre=self._genre.text().strip(),
                tone=self._tone.currentData() or DEFAULT_TONE,
                length_target=self._length.value(),
                max_edit_rounds=self._rounds.value(),
                segment_trigger_words=self._segment_trigger.value(),
                writing_mode=self._writing_mode.currentData() or "auto",
                title=self._title.text(),
                language=self._language.currentData() or "zh",
                characters_hint=self._characters_hint.toPlainText(),
                world_hint=self._world_hint.toPlainText(),
                conflict_hint=self._conflict_hint.toPlainText(),
                pov_hint=self._pov_hint.toPlainText(),
                opening_style=self._opening_style.toPlainText(),
                ending_style=self._ending_style.toPlainText(),
                extra_instructions=self._extra.toPlainText(),
                blueprint_element_preferences=self._blueprint_preferences.collect_preferences(),
                research_enabled=self._short_research_enabled.isChecked(),
                research_provider=self._short_research_provider.currentData() or "auto",
                research_query_hint=self._short_research_query_hint.toPlainText(),
            )
        except Exception as exc:
            show_warning_message(self.window(), "输入有误", str(exc))
            return
        self.submitted.emit(request)


# ──────────────────────────────────────────────────────────────────────────────
# Long Init Form
# ──────────────────────────────────────────────────────────────────────────────


class LongInitForm(Surface):
    """Form for initializing long-form projects."""

    submitted = Signal(object)
    cancel_requested = Signal(str)  # job_id — user pressed stop
    workspace_refresh_requested = Signal()
    restart_task_flow_cleanup_requested = Signal(str)  # project_id
    init_long_autorun_requested = Signal(
        object
    )  # request — create project then start chapter auto-run
    init_long_copilot_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("panel", parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        # Avoid shadow off-screen rendering for this heavy, frequently-resized form.
        self.setGraphicsEffect(None)  # type: ignore[arg-type]  # PySide accepts clearing with None.
        self._storage_root: Path | None = None
        self._snapshot: DesktopWorkspaceSnapshot | None = None
        self._current_job: DesktopJobRecord | None = None
        self._field_inputs_enabled = True
        self._research_enabled_user_overridden = False
        self._research_enabled_default_snapshot = _default_research_enabled_from_settings()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        layout.addWidget(
            SectionHeading(
                "立项初始化",
                "根据前提自动生成世界观、角色设定与分章大纲，之后切换到「章节续写」逐章推进。",
            )
        )

        # Resume panel
        self._resume_panel = Surface("inset")
        resume_layout = QVBoxLayout(self._resume_panel)
        resume_layout.setContentsMargins(16, 14, 16, 14)
        resume_layout.setSpacing(8)
        self._resume_title = QLabel("")
        self._resume_title.setObjectName("cardMeta")
        self._resume_title.setWordWrap(True)
        resume_layout.addWidget(self._resume_title)

        resume_bar_row = QHBoxLayout()
        resume_bar_row.setSpacing(8)
        self._resume_bar = QProgressBar()
        self._resume_bar.setObjectName("jobProgress")
        self._resume_bar.setRange(0, 100)
        self._resume_bar.setTextVisible(False)
        self._resume_bar.setFixedHeight(7)
        resume_bar_row.addWidget(self._resume_bar, 1)

        self._resume_percent = QLabel("")
        self._resume_percent.setObjectName("jobProgressPct")
        self._resume_percent.setFixedWidth(38)
        self._resume_percent.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        resume_bar_row.addWidget(self._resume_percent)
        resume_layout.addLayout(resume_bar_row)

        self._resume_hint = QLabel("")
        self._resume_hint.setObjectName("fieldHint")
        self._resume_hint.setWordWrap(True)
        resume_layout.addWidget(self._resume_hint)
        self._resume_panel.hide()
        layout.addWidget(self._resume_panel)

        # ── Preset toolbar ────────────────────────────────────────
        self._toolbar = PresetToolbar("long", payload_provider=self._collect)
        self._toolbar.fill_requested.connect(self._fill)
        layout.addWidget(self._toolbar)
        layout.addWidget(
            _field_hint(
                "可通过工具栏载入预设、导入 JSON、导出模板/当前配置，或让 AI 随机生成/润色创作要素。"
            )
        )

        self._premise = _hint_edit(
            "例：一个人接到无法拒绝的委托，逐步发现世界运行规则并不如表面所见……",
            min_height=100,
            max_height=140,
        )
        _hide_input(self, self._premise)

        self._characters_hint = _hint_edit(
            "例：主角A（核心欲望与弱点）；盟友B（资源与代价）；对手C（价值观冲突）",
            min_height=72,
            max_height=100,
        )
        _hide_input(self, self._characters_hint)

        self._world_hint = _hint_edit(
            "例：近未来东亚超级都市，底层运行着持续删改个体痛苦与群体记忆的系统",
            min_height=72,
            max_height=100,
        )
        _hide_input(self, self._world_hint)

        self._conflict_hint = _hint_edit(
            "例：外部：触碰城市记忆修正系统的边界；内在：越靠近彼此，记忆越容易被系统抹去",
            min_height=72,
            max_height=100,
        )
        _hide_input(self, self._conflict_hint)

        self._core_fields = [
            _EditableFieldSpec(
                "premise",
                "故事前提",
                "text",
                self._premise,
                "前提是世界观与大纲的基础，建议详细描述核心设定。",
                required=True,
            ),
            _EditableFieldSpec(
                "characters_hint",
                "主角群提示",
                "text",
                self._characters_hint,
                "例：主角A（核心欲望与弱点）；盟友B（资源与代价）；对手C（价值观冲突）",
            ),
            _EditableFieldSpec(
                "world_hint",
                "世界观 / 时代背景",
                "text",
                self._world_hint,
                "例：近未来东亚超级都市，底层运行着持续删改个体痛苦与群体记忆的系统",
            ),
            _EditableFieldSpec(
                "conflict_hint",
                "主冲突提示",
                "text",
                self._conflict_hint,
                "例：外部危机、内在困境与关系代价同时推进",
            ),
        ]
        self._core_field_section = _WorkflowFieldCardSection(
            "核心梗概",
            "点击字段按钮可在大窗口中查看和编辑；主页面只保留摘要，方便扫配置。",
            self._core_fields,
            open_requested=self._open_core_field_dialog,
        )
        layout.addWidget(self._core_field_section)

        # 6 字段排成 2×3：每行 3 个，减少纵向占位便于一眼扫完。
        params_row1 = QHBoxLayout()
        params_row1.setSpacing(14)
        self._genre = _genre_edit()
        params_row1.addLayout(_param_block("题材", self._genre))
        self._tone = _tone_combo(DEFAULT_TONE)
        params_row1.addLayout(_param_block("基调", self._tone))
        self._total_chapters = _param_spin(1, 1000, 24, " 章")
        params_row1.addLayout(_param_block("总章节数", self._total_chapters))
        params_row1.addStretch()
        layout.addLayout(params_row1)

        params_row2 = QHBoxLayout()
        params_row2.setSpacing(14)
        self._words_per_chapter = _param_spin(500, 20000, 4500, " 字")
        params_row2.addLayout(_param_block("每章字数", self._words_per_chapter))
        self._volume_mode = QComboBox()
        self._volume_mode.addItems(["auto", "on", "off"])
        params_row2.addLayout(_param_block("分卷模式", self._volume_mode))
        self._chapters_per_volume = _param_spin(0, 500, 0, " 章/卷")
        self._chapters_per_volume.setSpecialValueText("自动")
        params_row2.addLayout(_param_block("每卷章节数", self._chapters_per_volume))
        params_row2.addStretch()
        layout.addLayout(params_row2)

        params_row3 = QHBoxLayout()
        params_row3.setSpacing(14)
        self._creative_exploration = QComboBox()
        self._creative_exploration.addItem("自适应 2+1", "adaptive")
        self._creative_exploration.addItem("单路径（兼容）", "single")
        params_row3.addLayout(_param_block("创意探索", self._creative_exploration))
        self._planning_commitment = QComboBox()
        self._planning_commitment.addItem("全书规划（默认）", "full")
        self._planning_commitment.addItem("渐进规划（先规划前 10 章）", "progressive")
        self._planning_commitment.setToolTip(
            "全书规划按总章数分批生成全部大纲与契约；渐进规划先生成前 10 章，"
            "其中前 5 章细化，后续随写作推进。全书规划的初始化耗时和模型用量较多。"
        )
        params_row3.addLayout(_param_block("规划承诺", self._planning_commitment))
        params_row3.addStretch()
        layout.addLayout(params_row3)
        # 章台默认轮次已删除：WAVE 阶段在长篇里固定为单次连贯起稿，不再给用户配置。
        # 该字段如需传入请求，将以 0 兜底。
        self._volume_hint = _field_hint("")
        layout.addWidget(self._volume_hint)
        self._volume_mode.currentTextChanged.connect(self._sync_volume_mode_controls)
        self._sync_volume_mode_controls(self._volume_mode.currentText())

        self._research_enabled = QCheckBox("本次启用联网资料检索")
        self._set_research_enabled_checked(self._research_enabled_default_snapshot)
        self._research_enabled.stateChanged.connect(self._on_research_enabled_changed)
        self._research_provider = QComboBox()
        for value, label in (
            ("auto", "自动"),
            ("tavily", "Tavily"),
            ("brave", "Brave"),
            ("searxng", "SearXNG"),
            ("http_json", "HTTP JSON"),
            ("bailian_web_search", "百炼 Web Search"),
            ("mcp_search", "MCP Search"),
        ):
            self._research_provider.addItem(label, value)
        self._research_provider.setMinimumWidth(168)
        _set_combo(self._research_provider, "auto")
        self._research_query_hint = _hint_edit(
            "可选：补充希望检索的方向，例如「唐代市舶司制度」「近未来脑机接口伦理争议」",
            min_height=56,
            max_height=80,
        )
        research_body = QWidget()
        research_layout = QVBoxLayout(research_body)
        research_layout.setContentsMargins(0, 0, 0, 0)
        research_layout.setSpacing(8)
        research_top = QHBoxLayout()
        research_top.setSpacing(14)
        research_top.addWidget(self._research_enabled)
        research_top.addStretch()
        research_top.addLayout(_param_block("本次检索后端", self._research_provider))
        research_layout.addLayout(research_top)
        research_layout.addWidget(self._research_query_hint)
        research_layout.addWidget(
            _field_hint(
                "默认开关与后端连接参数在「火候 > 资料检索」中管理；"
                "这里的联网开关、本次后端和检索方向会随本地预设一并保存。"
            )
        )
        self._research_section = CollapsibleSection("联网资料检索", expanded=False)
        self._research_section.body_layout.addWidget(research_body)
        layout.addWidget(self._research_section)

        self._title = QLineEdit()
        self._title.setPlaceholderText("作品暂定名，留空由 AI 生成")
        _hide_input(self, self._title)
        self._language = _lang_combo()
        _hide_input(self, self._language)

        self._pov_hint = _hint_edit("例：第三人称多视角，女主主视角为主，少量穿插男主")
        _hide_input(self, self._pov_hint)

        self._opening_style = _hint_edit("开篇方式，例：开篇即高概念与高情绪")
        _hide_input(self, self._opening_style)
        self._ending_style = _hint_edit("结尾方向，例：HE 余韵型收束")
        _hide_input(self, self._ending_style)

        self._extra = _hint_edit("例：女频优势优先，关系拉扯与情绪流动贯穿始终")
        _hide_input(self, self._extra)

        self._project_id = QLineEdit()
        self._project_id.setPlaceholderText("留空自动生成（用作保存目录名）")
        self._project_id.textChanged.connect(lambda _text: self._refresh_resume_feedback())
        _hide_input(self, self._project_id)

        self._advanced_fields = [
            _EditableFieldSpec(
                "title_language",
                "作品名 / 语言",
                "title_language",
                (self._title, self._language),
                "作品名可留空交给 AI；语言决定生成文本的主要输出语种。",
            ),
            _EditableFieldSpec(
                "pov_hint",
                "叙事视角",
                "text",
                self._pov_hint,
                "例：第三人称多视角，女主主视角为主，少量穿插男主",
            ),
            _EditableFieldSpec(
                "opening_style",
                "开篇方式",
                "text",
                self._opening_style,
                "开篇方式，例：开篇即高概念与高情绪",
            ),
            _EditableFieldSpec(
                "ending_style",
                "结尾方式",
                "text",
                self._ending_style,
                "结尾方向，例：HE 余韵型收束",
            ),
            _EditableFieldSpec(
                "extra_instructions",
                "额外创作指令",
                "text",
                self._extra,
                "例：女频优势优先，关系拉扯与情绪流动贯穿始终",
            ),
            _EditableFieldSpec(
                "project_id",
                "项目 ID",
                "line",
                self._project_id,
                "留空自动生成（用作保存目录名）",
            ),
        ]
        self._advanced_field_section = _WorkflowFieldCardSection(
            "高级设定",
            "视角、开篇、结尾和项目标识集中在这里；不需要时保持空白即可。",
            self._advanced_fields,
            open_requested=self._open_advanced_field_dialog,
        )
        layout.addWidget(self._advanced_field_section)

        self._blueprint_preferences = _DeferredBlueprintPreferences(mode="long")
        self._blueprint_preferences_section = CollapsibleSection(
            "叙事要素偏好（手动勾选 / 锁定 / 权重）",
            expanded=False,
            lazy_body_builder=self._blueprint_preferences.build_into,
        )
        layout.addWidget(self._blueprint_preferences_section)
        self._genre.textChanged.connect(self._on_genre_changed)
        self._on_genre_changed(self._genre.text())
        self._refresh_field_cards()

        button_row = QHBoxLayout()
        save_preset_btn = ActionButton("存为预设", variant="secondary")
        save_preset_btn.clicked.connect(self._save_preset)
        button_row.addWidget(save_preset_btn)
        self._clear_btn = ActionButton("清空表单", variant="secondary")
        self._clear_btn.clicked.connect(self.reset_form)
        button_row.addWidget(self._clear_btn)
        self._submit_button = ActionButton("创建长篇项目 →")
        self._submit_button.clicked.connect(self._submit)
        button_row.addWidget(self._submit_button)
        self._submit_copilot_button = ActionButton("AI 伴随立项 →", variant="secondary")
        self._submit_copilot_button.clicked.connect(self._submit_copilot)
        button_row.addWidget(self._submit_copilot_button)
        self._submit_autorun_button = ActionButton("创建并连跑 →", variant="primary")
        self._submit_autorun_button.clicked.connect(self._submit_autorun)
        button_row.addWidget(self._submit_autorun_button)
        self._restart_btn = ActionButton("重新立项", variant="secondary")
        self._restart_btn.clicked.connect(self._on_force_restart_init)
        self._restart_btn.setVisible(False)
        button_row.addWidget(self._restart_btn)
        self._stop_button = ActionButton("终止立项", variant="secondary")
        self._stop_button.clicked.connect(self._cancel_init)
        self._stop_button.setVisible(False)
        button_row.addWidget(self._stop_button)
        button_row.addStretch()
        layout.addLayout(button_row)

        self._preview = StepIndicatorRow(
            _steps_for_kind("init_long"), parallel_pairs=_parallel_pairs_for_kind("init_long")
        )
        self._preview.reset()
        self._preview.step_clicked.connect(self._on_step_clicked)

    def _make_field_editor_dialog(
        self,
        fields: list[_EditableFieldSpec],
        selected_key: str,
        title: str,
    ) -> _WorkflowFieldEditDialog:
        return _WorkflowFieldEditDialog(
            fields,
            selected_key=selected_key,
            title=title,
            read_only=not self._field_inputs_enabled,
            parent=self.window(),
        )

    def _open_core_field_dialog(self, selected_key: str) -> None:
        dialog = self._make_field_editor_dialog(self._core_fields, selected_key, "核心梗概")
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.applied:
            self._refresh_field_cards()

    def _open_advanced_field_dialog(self, selected_key: str) -> None:
        dialog = self._make_field_editor_dialog(self._advanced_fields, selected_key, "高级设定")
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.applied:
            self._refresh_field_cards()

    def _refresh_field_cards(self) -> None:
        self._core_field_section.refresh()
        self._advanced_field_section.refresh()

    def _set_research_enabled_checked(self, enabled: bool) -> None:
        self._research_enabled.blockSignals(True)
        self._research_enabled.setChecked(bool(enabled))
        self._research_enabled.blockSignals(False)

    def _sync_volume_mode_controls(self, mode: str) -> None:
        """Keep the dependent volume-size field semantically unambiguous.

        ``chapters_per_volume`` is a user preference only for forced volume
        planning (``on``).  In automatic and single-volume modes, retaining a
        previous numeric value makes the saved request misleading even though
        the pipeline ignores it.  Normalize it at the UI boundary so restored
        drafts, presets, and new requests all show the same intent.
        """
        normalized_mode = mode if mode in {"auto", "on", "off"} else "auto"
        is_manual_volume_size = normalized_mode == "on"
        with QSignalBlocker(self._chapters_per_volume):
            if not is_manual_volume_size:
                self._chapters_per_volume.setValue(0)
            self._chapters_per_volume.setSpecialValueText(
                "自动" if normalized_mode != "off" else "不适用"
            )
        self._chapters_per_volume.setEnabled(is_manual_volume_size)

        if normalized_mode == "on":
            hint = (
                "on=强制分卷；可指定每卷章节数，设为「自动」时使用火候页的默认值。"
            )
        elif normalized_mode == "off":
            hint = "off=强制不分卷；每卷章节数不适用。"
        else:
            hint = (
                "auto=系统根据总章节数和预计总字数决定是否分卷；每卷章节数由系统自动决定。"
            )
        self._volume_hint.setText(
            f"{hint} 每章字数建议 3000-6000；初始化会按当前模型能力自动选择大纲与契约批次。"
        )

    def _on_research_enabled_changed(self, _state: int) -> None:
        self._research_enabled_user_overridden = True

    def refresh_settings_defaults(self) -> None:
        """Refresh settings-backed defaults without overwriting explicit form choices."""
        default_enabled = _default_research_enabled_from_settings()
        self._research_enabled_default_snapshot = default_enabled
        if not self._research_enabled_user_overridden:
            self._set_research_enabled_checked(default_enabled)

    def set_storage_root(self, root: Path) -> None:
        self._storage_root = root

    def reset_form(self) -> None:
        """Clear all user-entered text and reset parameters to their defaults."""
        self._premise.setPlainText("")
        self._characters_hint.setPlainText("")
        self._world_hint.setPlainText("")
        self._conflict_hint.setPlainText("")
        self._pov_hint.setPlainText("")
        self._opening_style.setPlainText("")
        self._ending_style.setPlainText("")
        self._extra.setPlainText("")
        self._title.setText("")
        self._project_id.setText("")
        self._genre.setText("")
        _set_combo(self._tone, DEFAULT_TONE)
        self._total_chapters.setValue(24)
        self._words_per_chapter.setValue(4500)
        _set_combo(self._volume_mode, "auto")
        self._chapters_per_volume.setValue(0)
        _set_combo(self._creative_exploration, "adaptive")
        _set_combo(self._planning_commitment, "full")
        self._sync_volume_mode_controls(self._volume_mode.currentText())
        self._research_enabled_user_overridden = False
        self.refresh_settings_defaults()
        _set_combo(self._research_provider, "auto")
        self._research_query_hint.setPlainText("")
        self._blueprint_preferences.fill_preferences(None)
        self._toolbar.unbind_preset()
        self._refresh_field_cards()
        # Clear modified flags and delete any saved draft
        for te in self.findChildren(QTextEdit):
            doc = te.document()
            if doc is not None:
                doc.setModified(False)
        for le in self.findChildren(QLineEdit):
            le.setModified(False)
        if self._storage_root is not None:
            draft = self._storage_root / ".presets" / ".draft" / "_autosave_long.json"
            try:
                draft.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass

    def save_draft(self, path: Path) -> bool:
        """Save current form state to *path* as JSON. Returns True on success."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = self._collect()
            atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))
            return True
        except Exception as exc:  # noqa: BLE001
            _log.warning("长篇草稿保存失败: %s", exc)
            return False

    def restore_draft(self, path: Path) -> bool:
        """Load form state from *path*. Returns True if data was restored."""
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not data.get("premise"):
                return False
            self._toolbar.unbind_preset()
            self._fill(data)
            # Reset modified flags so a fresh restore doesn't immediately
            # trigger the "unsaved changes" warning.
            for te in self.findChildren(QTextEdit):
                doc = te.document()
                if doc is not None:
                    doc.setModified(False)
            for le in self.findChildren(QLineEdit):
                le.setModified(False)
            return True
        except Exception as exc:  # noqa: BLE001
            _log.warning("长篇草稿恢复失败: %s", exc)
            return False

    def bind_snapshot(self, snapshot: DesktopWorkspaceSnapshot) -> None:
        self._snapshot = snapshot
        self.refresh_settings_defaults()
        if not self._project_id.text().strip() and not self._premise.toPlainText().strip():
            resumable = self._pick_resume_detail()
            if resumable is not None:
                self._fill_from_project_detail(resumable)
        self._refresh_resume_feedback()

    def focus_project(self, project_id: str) -> None:
        project_value = project_id.strip()
        if not project_value:
            return
        detail = self._project_detail(project_value)
        if detail is not None:
            self._fill_from_project_detail(detail)
        else:
            self._project_id.setText(project_value)
        self._refresh_resume_feedback()

    def submit_button_text(self) -> str:
        return self._submit_button.text()

    def project_id_text(self) -> str:
        return self._project_id.text().strip()

    def resume_hint_text(self) -> str:
        return self._resume_hint.text()

    def resume_percent_text(self) -> str:
        return self._resume_percent.text()

    def _project_detail(self, project_id: str) -> Any | None:
        if self._snapshot is None:
            return None
        return self._snapshot.details.get(project_id)

    def _pick_resume_detail(self) -> Any | None:
        if self._snapshot is None:
            return None
        for project in self._snapshot.projects:
            detail = self._snapshot.details.get(project.project_id)
            if detail is None or detail.mode != "long" or not detail.init_resume_available:
                continue
            return detail
        return None

    def _active_resume_detail(self) -> Any | None:
        project_value = self._project_id.text().strip()
        if project_value:
            detail = self._project_detail(project_value)
            if detail is not None and detail.init_resume_available:
                return detail
            return None
        if self._premise.toPlainText().strip():
            return None
        return self._pick_resume_detail()

    def _fill_from_project_detail(self, detail: Any) -> None:
        self._project_id.setText(detail.project_id)
        if not self._premise.toPlainText().strip():
            self._premise.setPlainText(detail.premise or "")
        self._genre.setText(detail.genre or "")
        _set_combo(self._tone, detail.tone or DEFAULT_TONE)
        if detail.total_chapters:
            self._total_chapters.setValue(int(detail.total_chapters))
        if detail.words_per_chapter:
            self._words_per_chapter.setValue(int(detail.words_per_chapter))
        if detail.volume_mode:
            _set_combo(self._volume_mode, detail.volume_mode)
        chapters_per_volume = int(getattr(detail, "chapters_per_volume", 0) or 0)
        self._chapters_per_volume.setValue(max(0, chapters_per_volume))
        self._sync_volume_mode_controls(self._volume_mode.currentText())
        if not self._title.text().strip():
            self._title.setText(detail.title or "")
        if detail.language:
            _set_combo(self._language, detail.language)
        if not self._characters_hint.toPlainText().strip():
            self._characters_hint.setPlainText(detail.characters_hint or "")
        if not self._world_hint.toPlainText().strip():
            self._world_hint.setPlainText(detail.world_hint or "")
        if not self._conflict_hint.toPlainText().strip():
            self._conflict_hint.setPlainText(detail.conflict_hint or "")
        if not self._pov_hint.toPlainText().strip():
            self._pov_hint.setPlainText(detail.pov_hint or "")
        if not self._opening_style.toPlainText().strip():
            self._opening_style.setPlainText(detail.opening_style or "")
        if not self._ending_style.toPlainText().strip():
            self._ending_style.setPlainText(detail.ending_style or "")
        if not self._extra.toPlainText().strip():
            self._extra.setPlainText(detail.extra_instructions or "")
        self._on_genre_changed(self._genre.text())
        self._refresh_field_cards()

    def _show_resume_panel(self, *, title: str, percent: int, hint: str) -> None:
        self._resume_title.setText(title)
        self._resume_bar.setValue(percent)
        self._resume_percent.setText(f"{percent}%")
        self._resume_hint.setText(hint)
        self._resume_panel.show()

    def _hide_resume_panel(self) -> None:
        self._resume_title.clear()
        self._resume_bar.setValue(0)
        self._resume_percent.clear()
        self._resume_hint.clear()
        self._resume_panel.hide()

    def _refresh_resume_feedback(self) -> None:
        if self._current_job is not None:
            return
        detail = self._active_resume_detail()
        self._preview.reset()
        if detail is None:
            self._hide_resume_panel()
            self._submit_button.setEnabled(True)
            self._submit_button.setText("创建长篇项目 →")
            self._submit_autorun_button.setEnabled(True)
            self._submit_autorun_button.setText("创建并连跑 →")
            self._restart_btn.setVisible(False)
            return
        if detail.init_resume_step:
            self._preview.update_step(detail.init_resume_step)
        hint_parts = [detail.init_resume_progress_label or "上次立项已停在当前步骤。"]
        if detail.init_resume_step == "plan_outline" and detail.init_resume_next_chapter:
            hint_parts.append(f"下次将从第 {detail.init_resume_next_chapter} 章继续。")
        elif detail.init_resume_step_label:
            hint_parts.append(f"下一步将继续{detail.init_resume_step_label}。")
        hint_parts.append("继续会沿用上次立项参数；若要应用当前表单改动，请先使用「重新立项」。")
        self._show_resume_panel(
            title=f"检测到可继续项目：{detail.title or detail.project_id}",
            percent=detail.init_resume_progress_percent,
            hint=" ".join(hint_parts),
        )
        self._submit_button.setEnabled(True)
        self._submit_button.setText("继续立项 →")
        self._submit_autorun_button.setEnabled(True)
        self._submit_autorun_button.setText("继续并连跑 →")
        self._restart_btn.setVisible(True)

    def _on_force_restart_init(self) -> None:
        """清除所有立项阶段产物，从头重新立项。"""
        detail = self._active_resume_detail()
        project_id = ""
        if detail is not None:
            project_id = str(detail.project_id or "").strip()
        if not project_id:
            # Workspace snapshots can lag behind the job manager, especially
            # when a project failed before its first long-form artifact was
            # written.  The terminal init job still carries the effective
            # project id and is the safest source for this destructive action.
            job = self._current_job
            if (
                job is not None
                and job.kind == "init_long"
                and job.status in {DesktopJobState.FAILED, DesktopJobState.PAUSED}
            ):
                project_id = str(job.project_id or "").strip()
        if not project_id:
            project_id = self._project_id.text().strip()
        if not project_id:
            show_warning_message(
                self.window(),
                "无法重新立项",
                "当前没有找到可重新立项的项目，请先重新加载项目后再试。",
            )
            return
        if self._storage_root is None:
            show_warning_message(
                self.window(),
                "无法重新立项",
                "存储目录尚未就绪，请稍后刷新工作区再试。",
            )
            return
        project_dir = self._storage_root / project_id
        layout = ProjectLayout(project_dir)
        project_title = str(getattr(detail, "title", "") or project_id)

        existing_chapters = finalized_chapter_numbers(layout)
        has_chapters = len(existing_chapters) > 0
        chapter_info = (
            f"（共 {len(existing_chapters)} 章：第 {', '.join(str(n) for n in existing_chapters[:5])}{'...' if len(existing_chapters) > 5 else ''}）"
            if has_chapters
            else ""
        )

        result = ask_confirmation_with_checkbox(
            self,
            title="重新立项确认",
            text=f"将清除项目「{project_title}」的所有立项数据：\n"
            f"故事规格、世界观、角色设定、叙事要素选择、叙事蓝图、大纲、Canon 状态，\n"
            f"然后从头重新生成。",
            checkbox_label="同时删除已生成的章节正文（推荐）" if has_chapters else "（无章节文件）",
            informative_text=(
                f"项目已生成 {len(existing_chapters)} 章正文 {chapter_info}。\n"
                f"保留章节将导致新旧世界观冲突，建议一并删除。"
                if has_chapters
                else ""
            ),
            confirm_text="重新立项",
            cancel_text="取消",
            confirm_variant="primary",
            checkbox_default=has_chapters,
        )

        if not result.confirmed:
            return

        self.restart_task_flow_cleanup_requested.emit(project_id)
        try:
            reset_project_for_reinit(
                layout,
                preserve_chapters=has_chapters and not result.checkbox_checked,
                preserve_logs=False,
            )
        except Exception as exc:  # noqa: BLE001 - surface destructive-action failures
            _log.exception("重新立项清理失败 | project_id=%s", project_id)
            show_warning_message(
                self.window(),
                "重新立项失败",
                f"无法清理项目「{project_id}」的旧数据：{exc}",
            )
            return

        # The job manager removes the old task asynchronously.  Clear the
        # form-side reference immediately so the button and progress panel
        # respond in the same click instead of waiting for the next refresh.
        self._current_job = None
        self.workspace_refresh_requested.emit()
        self._refresh_resume_feedback()

    def _on_step_clicked(self, index: int) -> None:
        job = self._current_job
        project_dir = None
        if job and job.project_id and self._storage_root:
            project_dir = self._storage_root / job.project_id
        nav_target = StepArtifactDialog.show_for_step(
            kind="init_long",
            step_key=self._preview.step_key(index),
            step_label=self._preview.step_label(index),
            project_dir=project_dir,
            parent=self.window(),
        )
        if nav_target:
            win = self.window()
            if hasattr(win, "switch_page"):
                win.switch_page(nav_target)

    def _collect(self) -> dict[str, Any]:
        """Collect all form fields into a dict."""
        volume_mode = self._volume_mode.currentText()
        chapters_per_volume = (
            self._chapters_per_volume.value() if volume_mode == "on" else 0
        )
        return {
            "premise": self._premise.toPlainText(),
            "genre": self._genre.text().strip(),
            "tone": self._tone.currentData() or DEFAULT_TONE,
            "total_chapters": self._total_chapters.value(),
            "words_per_chapter": self._words_per_chapter.value(),
            "volume_mode": volume_mode,
            "chapters_per_volume": chapters_per_volume,
            "title": self._title.text(),
            "language": self._language.currentData() or "zh",
            "characters_hint": self._characters_hint.toPlainText(),
            "world_hint": self._world_hint.toPlainText(),
            "conflict_hint": self._conflict_hint.toPlainText(),
            "pov_hint": self._pov_hint.toPlainText(),
            "opening_style": self._opening_style.toPlainText(),
            "ending_style": self._ending_style.toPlainText(),
            "extra_instructions": self._extra.toPlainText(),
            "project_id": self._project_id.text(),
            "research_enabled": self._research_enabled.isChecked(),
            "research_provider": self._research_provider.currentData() or "auto",
            "research_query_hint": self._research_query_hint.toPlainText(),
            "creative_exploration": self._creative_exploration.currentData() or "adaptive",
            "planning_commitment": self._planning_commitment.currentData() or "full",
            "blueprint_element_preferences": self._blueprint_preferences.collect_preferences(),
        }

    def _resume_project_id_for_stored_request(self) -> str:
        detail = self._active_resume_detail()
        if detail is not None:
            return str(detail.project_id or "").strip()
        if (
            self._current_job is not None
            and self._current_job.kind == "init_long"
            and self._current_job.status in {DesktopJobState.FAILED, DesktopJobState.PAUSED}
        ):
            return str(self._current_job.project_id or "").strip()
        return ""

    def _stored_resume_request_kwargs(self) -> dict[str, Any] | None:
        """Return original init request fields when continuing a failed init."""
        project_id = self._resume_project_id_for_stored_request()
        if not project_id or self._storage_root is None:
            return None
        meta_path = ProjectLayout(self._storage_root / project_id).init_request_meta_path
        if not meta_path.exists():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            _log.warning("init resume metadata read failed: %s", exc)
            return None
        request_payload = meta.get("request") if isinstance(meta, dict) else None
        if not isinstance(request_payload, dict):
            return None
        init_input = request_payload.get("init_input")
        generation_options = request_payload.get("generation_options")
        if not isinstance(init_input, dict) or not isinstance(generation_options, dict):
            return None
        premise = str(init_input.get("premise") or "").strip()
        if not premise:
            return None

        def _int_option(key: str, fallback: int) -> int:
            try:
                return int(generation_options.get(key, fallback))
            except (TypeError, ValueError):
                return fallback

        preferences = generation_options.get("blueprint_element_preferences")
        if not isinstance(preferences, dict):
            preferences = {}
        # 长篇 WAVE 单次连贯起稿，不再支持用户配置编辑轮次；
        # 历史 init meta 里残留的 chapter_defaults.max_edit_rounds 也会被忽略。
        return {
            "project_id": project_id,
            "premise": premise,
            "genre": str(init_input.get("genre") or self._genre.text().strip()),
            "tone": str(init_input.get("tone") or self._tone.currentData() or DEFAULT_TONE),
            "total_chapters": _int_option("total_chapters", self._total_chapters.value()),
            "words_per_chapter": _int_option(
                "words_per_chapter",
                self._words_per_chapter.value(),
            ),
            "volume_mode": str(
                generation_options.get("volume_mode_setting")
                or self._volume_mode.currentText()
                or "auto"
            ),
            "chapters_per_volume": _int_option(
                "chapters_per_volume_setting",
                self._chapters_per_volume.value(),
            ),
            "title": str(init_input.get("title") or ""),
            "language": str(init_input.get("language") or "zh"),
            "characters_hint": str(init_input.get("characters_hint") or ""),
            "world_hint": str(init_input.get("world_hint") or ""),
            "conflict_hint": str(init_input.get("conflict_hint") or ""),
            "pov_hint": str(init_input.get("pov_hint") or ""),
            "opening_style": str(init_input.get("opening_style") or ""),
            "ending_style": str(init_input.get("ending_style") or ""),
            "extra_instructions": str(init_input.get("extra_instructions") or ""),
            "research_enabled": bool(
                generation_options.get(
                    "research_enabled",
                    _default_research_enabled_from_settings(),
                )
            ),
            "research_provider": str(generation_options.get("research_provider") or "auto"),
            "research_query_hint": str(generation_options.get("research_query_hint") or ""),
            "creative_exploration": str(
                generation_options.get("creative_exploration") or "adaptive"
            ),
            "planning_commitment": str(
                generation_options.get("planning_commitment") or "progressive"
            ),
            "blueprint_element_preferences": preferences,
        }

    def _current_form_request_kwargs(self) -> dict[str, Any]:
        self.refresh_settings_defaults()
        volume_mode = self._volume_mode.currentText()
        chapters_per_volume = (
            self._chapters_per_volume.value() if volume_mode == "on" else 0
        )
        return {
            "project_id": self._project_id.text(),
            "premise": self._premise.toPlainText(),
            "genre": self._genre.text().strip(),
            "tone": self._tone.currentData() or DEFAULT_TONE,
            "total_chapters": self._total_chapters.value(),
            "words_per_chapter": self._words_per_chapter.value(),
            "volume_mode": volume_mode,
            "chapters_per_volume": chapters_per_volume,
            "title": self._title.text(),
            "language": self._language.currentData() or "zh",
            "characters_hint": self._characters_hint.toPlainText(),
            "world_hint": self._world_hint.toPlainText(),
            "conflict_hint": self._conflict_hint.toPlainText(),
            "pov_hint": self._pov_hint.toPlainText(),
            "opening_style": self._opening_style.toPlainText(),
            "ending_style": self._ending_style.toPlainText(),
            "extra_instructions": self._extra.toPlainText(),
            "research_enabled": self._research_enabled.isChecked(),
            "research_provider": str(self._research_provider.currentData() or "auto"),
            "research_query_hint": self._research_query_hint.toPlainText(),
            "creative_exploration": self._creative_exploration.currentData() or "adaptive",
            "planning_commitment": self._planning_commitment.currentData() or "full",
            "blueprint_element_preferences": self._blueprint_preferences.collect_preferences(),
        }

    def _build_submit_request(self) -> Any:
        kwargs = self._stored_resume_request_kwargs() or self._current_form_request_kwargs()
        return build_init_long_request(**kwargs)

    def _fill(self, data: dict[str, Any]) -> None:
        """Fill form fields from a data dict."""
        if "premise" in data:
            self._premise.setPlainText(str(data["premise"]))
        if "genre" in data:
            self._genre.setText(str(data["genre"]))
        if "tone" in data:
            _set_combo(self._tone, str(data["tone"]))
        if "total_chapters" in data:
            self._total_chapters.setValue(int(data["total_chapters"]))
        if "words_per_chapter" in data:
            self._words_per_chapter.setValue(int(data["words_per_chapter"]))
        if "volume_mode" in data:
            _set_combo(self._volume_mode, str(data["volume_mode"]))
        if "chapters_per_volume" in data:
            self._chapters_per_volume.setValue(int(data["chapters_per_volume"]))
        if "creative_exploration" in data:
            _set_combo(self._creative_exploration, str(data["creative_exploration"]))
        if "planning_commitment" in data:
            _set_combo(self._planning_commitment, str(data["planning_commitment"]))
        if "title" in data:
            self._title.setText(str(data["title"]))
        if "language" in data:
            _set_combo(self._language, str(data["language"]))
        if "characters_hint" in data:
            self._characters_hint.setPlainText(str(data["characters_hint"]))
        if "world_hint" in data:
            self._world_hint.setPlainText(str(data["world_hint"]))
        if "conflict_hint" in data:
            self._conflict_hint.setPlainText(str(data["conflict_hint"]))
        if "pov_hint" in data:
            self._pov_hint.setPlainText(str(data["pov_hint"]))
        if "opening_style" in data:
            self._opening_style.setPlainText(str(data["opening_style"]))
        if "ending_style" in data:
            self._ending_style.setPlainText(str(data["ending_style"]))
        if "extra_instructions" in data:
            self._extra.setPlainText(str(data["extra_instructions"]))
        if "project_id" in data:
            self._project_id.setText(str(data["project_id"]))
        if "research_enabled" in data:
            self._set_research_enabled_checked(bool(data["research_enabled"]))
            self._research_enabled_user_overridden = True
        if "research_provider" in data:
            _set_combo(self._research_provider, str(data["research_provider"]))
        if "research_query_hint" in data:
            self._research_query_hint.setPlainText(str(data["research_query_hint"]))
        if "blueprint_element_preferences" in data:
            self._blueprint_preferences.fill_preferences(data.get("blueprint_element_preferences"))
        self._sync_volume_mode_controls(self._volume_mode.currentText())
        self._on_genre_changed(self._genre.text())
        self._refresh_field_cards()

    def _save_preset(self) -> None:
        self._toolbar.do_save(self._collect())

    def _on_genre_changed(self, genre_text: str) -> None:
        self._blueprint_preferences.set_genre_context(genre_text)

    def export_template(self) -> None:
        self._toolbar.export_template()

    def _set_inputs_enabled(self, enabled: bool) -> None:
        """Enable or disable all input fields to prevent edits during active init job."""
        self._field_inputs_enabled = enabled
        for widget in (
            self._premise,
            self._characters_hint,
            self._world_hint,
            self._conflict_hint,
            self._genre,
            self._tone,
            self._total_chapters,
            self._words_per_chapter,
            self._volume_mode,
            self._chapters_per_volume,
            self._creative_exploration,
            self._planning_commitment,
            self._title,
            self._language,
            self._pov_hint,
            self._opening_style,
            self._ending_style,
            self._extra,
            self._project_id,
            self._research_enabled,
            self._research_provider,
            self._research_query_hint,
            self._research_section,
            self._toolbar,
            self._blueprint_preferences,
            self._clear_btn,
            self._submit_copilot_button,
            self._submit_autorun_button,
        ):
            widget.setEnabled(enabled)

    def update_progress(self, job: DesktopJobRecord | None) -> None:
        self._current_job = job
        self._preview.reset()
        if job is None:
            self._set_inputs_enabled(True)
            self._stop_button.setVisible(False)
            self._refresh_resume_feedback()
            return
        for event in job.events:
            self._preview.update_step(_resolve_step_key(job.kind, event.step))
        is_active = job.status in {DesktopJobState.QUEUED, DesktopJobState.RUNNING}
        self._set_inputs_enabled(not is_active)
        if job.status == DesktopJobState.SUCCEEDED:
            self._preview.mark_done()
            self._hide_resume_panel()
            self._stop_button.setVisible(False)
            self._submit_button.setEnabled(True)
            self._submit_button.setText("创建长篇项目 →")
            self._submit_copilot_button.setEnabled(True)
            self._submit_copilot_button.setText("AI 伴随立项 →")
            self._submit_autorun_button.setEnabled(True)
            self._submit_autorun_button.setText("创建并连跑 →")
            self._restart_btn.setVisible(False)
            return
        if job.status == DesktopJobState.FAILED:
            failed_hint = (
                "已取消。可重新提交从上次停靠点继续。"
                if job.current_step == "cancelled"
                else f"停在{display_step_name_for_job(job)}，修正后可直接继续立项。"
            )
            self._preview.mark_failed(_resolve_step_key(job.kind, job.current_step or ""))
            self._show_resume_panel(
                title=f"上次立项中断：{job.label}",
                percent=_compute_progress(job),
                hint=failed_hint,
            )
            self._stop_button.setVisible(False)
            self._submit_button.setEnabled(True)
            self._submit_button.setText("继续立项 →")
            self._submit_copilot_button.setEnabled(True)
            self._submit_copilot_button.setText("伴随继续 →")
            self._submit_autorun_button.setEnabled(True)
            self._submit_autorun_button.setText("继续并连跑 →")
            self._restart_btn.setVisible(True)
            return
        self._show_resume_panel(
            title=f"当前任务进度：{job.label}",
            percent=_compute_progress(job),
            hint=f"正在执行 {display_step_name_for_job(job)}。",
        )
        if is_active:
            self._submit_button.setEnabled(False)
            self._submit_copilot_button.setEnabled(False)
            self._submit_autorun_button.setEnabled(False)
            self._submit_button.setText("立项进行中…")
            self._submit_copilot_button.setText("伴随立项中…")
            self._stop_button.setVisible(True)
            self._restart_btn.setVisible(False)
            return
        self._stop_button.setVisible(False)
        self._submit_button.setEnabled(True)
        self._submit_copilot_button.setEnabled(True)
        self._submit_autorun_button.setEnabled(True)
        self._submit_button.setText("继续立项 →")
        self._submit_copilot_button.setText("伴随继续 →")
        self._submit_autorun_button.setText("继续并连跑 →")
        self._restart_btn.setVisible(True)

    def _cancel_init(self) -> None:
        """User pressed the stop button during active init job."""
        if self._current_job:
            self.cancel_requested.emit(self._current_job.job_id)

    def _submit(self) -> None:
        try:
            request = self._build_submit_request()
        except Exception as exc:
            show_warning_message(self.window(), "输入有误", str(exc))
            return
        self.submitted.emit(request)

    def _submit_autorun(self) -> None:
        """Submit init_long request and signal that chapter auto-run should follow."""
        try:
            request = self._build_submit_request()
        except Exception as exc:
            show_warning_message(self.window(), "输入有误", str(exc))
            return
        self.init_long_autorun_requested.emit(request)

    def _submit_copilot(self) -> None:
        try:
            request = self._build_submit_request().model_copy(
                update={
                    "copilot_gates": (
                        "concept",
                        "characters",
                        "blueprint",
                        "outline",
                        "contracts",
                    )
                }
            )
        except Exception as exc:
            show_warning_message(self.window(), "输入有误", str(exc))
            return
        self.init_long_copilot_requested.emit(request)
