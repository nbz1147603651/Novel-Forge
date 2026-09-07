"""Shared form widgets and choice helpers for workflow pages."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from novel_forge.prompts.packs import prompt_language_choices

_GENRE_CHOICES = [
    ("literary", "文学"),
    ("fantasy", "奇幻"),
    ("scifi", "科幻"),
    ("mystery", "悬疑"),
    ("romance", "言情"),
    ("thriller", "惊悚"),
    ("horror", "恐怖"),
    ("historical", "历史"),
    ("other", "其他"),
]

_TONE_CHOICES = [
    ("neutral", "中性"),
    ("warm", "温暖"),
    ("gentle", "温柔"),
    ("dark", "阴郁"),
    ("suspenseful", "悬疑"),
    ("humorous", "幽默"),
    ("solemn", "庄重"),
    ("lyrical", "抒情"),
]

try:
    _LANG_CHOICES = prompt_language_choices(include_draft=False)
except Exception:
    _LANG_CHOICES = [("zh", "中文 (zh)")]
_LANG_CHOICE_VALUES = {value for value, _label in _LANG_CHOICES}
if "zh" not in _LANG_CHOICE_VALUES:
    _LANG_CHOICES.insert(0, ("zh", "中文 (zh)"))
if "en" not in _LANG_CHOICE_VALUES:
    _LANG_CHOICES.append(("en", "English (en)"))

_PARAM_FIELD_WIDTH = 200
_PARAM_FIELD_HEIGHT = 32


def _genre_combo(default: str = "literary") -> QComboBox:
    combo = QComboBox()
    for value, label in _GENRE_CHOICES:
        combo.addItem(label, value)
    idx = next((i for i, (value, _) in enumerate(_GENRE_CHOICES) if value == default), 0)
    combo.setCurrentIndex(idx)
    return combo


def _genre_edit(default: str = "") -> QLineEdit:
    """Free-text genre input — supports mixed genres like 「言情悬疑」."""
    edit = QLineEdit()
    edit.setObjectName("workflowParamInput")
    edit.setPlaceholderText("例：言情、悬疑言情、古代宫斗言情（支持混合题材）")
    edit.setFixedSize(_PARAM_FIELD_WIDTH, _PARAM_FIELD_HEIGHT)
    edit.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    if default:
        edit.setText(default)
    return edit


def _set_combo(combo: QComboBox, value: str) -> None:
    """Set combo by data value, falling back to text-contains matching."""
    idx = combo.findData(value)
    if idx >= 0:
        combo.setCurrentIndex(idx)
        return
    idx = combo.findText(value, Qt.MatchFlag.MatchContains)
    if idx >= 0:
        combo.setCurrentIndex(idx)


def _tone_combo(default: str = "neutral") -> QComboBox:
    combo = QComboBox()
    for value, label in _TONE_CHOICES:
        combo.addItem(label, value)
    idx = next((i for i, (value, _) in enumerate(_TONE_CHOICES) if value == default), 0)
    combo.setCurrentIndex(idx)
    return combo


def _lang_combo(default: str = "zh") -> QComboBox:
    combo = QComboBox()
    for value, label in _LANG_CHOICES:
        combo.addItem(label, value)
    idx = next((i for i, (value, _) in enumerate(_LANG_CHOICES) if value == default), 0)
    combo.setCurrentIndex(idx)
    return combo


def _hint_edit(placeholder: str, min_height: int = 60, max_height: int = 96) -> QTextEdit:
    widget = QTextEdit()
    widget.setPlaceholderText(placeholder)
    widget.setMinimumHeight(min_height)
    widget.setMaximumHeight(max_height)
    return widget


def _param_spin(min_val: int, max_val: int, value: int, suffix: str = "") -> QSpinBox:
    spin = QSpinBox()
    spin.setObjectName("workflowParamInput")
    spin.setRange(min_val, max_val)
    spin.setValue(value)
    spin.setFixedSize(_PARAM_FIELD_WIDTH, _PARAM_FIELD_HEIGHT)
    spin.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    if suffix:
        spin.setSuffix(suffix)
    return spin


def _param_block(label_text: str, widget: QWidget) -> QVBoxLayout:
    block = QVBoxLayout()
    block.setContentsMargins(0, 0, 0, 0)
    block.setSpacing(4)
    label = QLabel(label_text)
    label.setObjectName("settingLabel")
    label.setFixedWidth(_PARAM_FIELD_WIDTH)
    block.addWidget(label)
    if not widget.objectName():
        widget.setObjectName("workflowParamInput")
    widget.setFixedSize(_PARAM_FIELD_WIDTH, _PARAM_FIELD_HEIGHT)
    widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    block.addWidget(widget)
    return block


def _field_hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("formFieldHint")
    label.setWordWrap(True)
    return label


def _field_label(text: str, *, required: bool = False) -> QLabel:
    label = QLabel(f"{text}  *" if required else text)
    label.setObjectName("fieldRequiredLabel" if required else "settingLabel")
    return label
