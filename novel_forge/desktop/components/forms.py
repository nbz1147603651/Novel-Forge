"""Form widgets: setting rows, form fields, and factory helpers."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


def _compact_setting_hint(text: str, *, limit: int = 48) -> str:
    """Keep visible setting hints short; full text remains available as tooltip."""

    hint = " ".join(str(text or "").split())
    if len(hint) <= limit:
        return hint
    return hint[: max(1, limit - 3)].rstrip() + "..."


def _compact_label_text(text: str, *, limit: int) -> tuple[str, str]:
    """Return compact visible text and the full normalized text."""

    full_text = " ".join(str(text or "").split())
    if len(full_text) <= limit:
        return full_text, full_text
    return full_text[: max(1, limit - 3)].rstrip() + "...", full_text


def add_setting_group_header(
    layout: QVBoxLayout,
    title: str,
    hint: str = "",
) -> QWidget:
    """Add a consistent third-level settings group header to *layout*."""

    header = QWidget()
    header.setObjectName("settingSubgroupHeader")
    header_layout = QVBoxLayout(header)
    header_layout.setContentsMargins(0, 8, 0, 2)
    header_layout.setSpacing(2)

    title_label = QLabel(title)
    title_label.setObjectName("settingSubgroupTitle")
    header_layout.addWidget(title_label)

    if hint:
        visible_hint, full_hint = _compact_label_text(hint, limit=78)
        hint_label = QLabel(visible_hint)
        hint_label.setObjectName("settingSubgroupHint")
        hint_label.setWordWrap(True)
        if visible_hint != full_hint:
            hint_label.setToolTip(full_hint)
            header.setToolTip(full_hint)
        header_layout.addWidget(hint_label)

    layout.addWidget(header)
    return header


def add_setting_group_description(layout: QVBoxLayout, text: str) -> QLabel:
    """Add a compact panel description label to *layout*."""

    visible_text, full_text = _compact_label_text(text, limit=96)
    label = QLabel(visible_text)
    label.setObjectName("panelDescription")
    label.setWordWrap(True)
    if visible_text != full_text:
        label.setToolTip(full_text)
    layout.addWidget(label)
    return label


def make_nested_setting_section(
    title: str,
    hint: str = "",
    *,
    expanded: bool = False,
) -> QWidget:
    """Create a compact fourth-level collapsible section for advanced groups."""

    from novel_forge.desktop.components.containers import CollapsibleSection

    section = CollapsibleSection(title, expanded=expanded, nested=True)
    if hint:
        add_setting_group_description(section.body_layout, hint)
    return section


class SettingRow(QWidget):
    """A single setting row: label + hint + input widget."""

    def __init__(
        self,
        label: str,
        hint: str = "",
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        text_block = QVBoxLayout()
        text_block.setSpacing(2)
        self.label_widget = QLabel(label)
        self.label_widget.setObjectName("settingLabel")
        text_block.addWidget(self.label_widget)

        compact_hint = _compact_setting_hint(hint)
        self.hint_widget = QLabel(compact_hint)
        self.hint_widget.setObjectName("settingHint")
        self.hint_widget.setWordWrap(True)
        self.hint_widget.setVisible(bool(hint))
        if compact_hint != str(hint or ""):
            self.hint_widget.setToolTip(str(hint or ""))
            self.setToolTip(str(hint or ""))
        text_block.addWidget(self.hint_widget)

        self._text_container = QWidget()
        self._text_container.setLayout(text_block)
        self._text_container.setMinimumWidth(260)
        self._text_container.setMaximumWidth(420)
        layout.addWidget(self._text_container)

        self._input_slot = QHBoxLayout()
        self._input_slot.setContentsMargins(0, 0, 0, 0)
        self._input_slot.setSpacing(0)
        layout.addLayout(self._input_slot)
        layout.addStretch(1)

    def set_input(self, widget: QWidget) -> None:
        """Place the input widget on the right side."""
        while self._input_slot.count():
            item = self._input_slot.takeAt(0)
            if item is None:
                continue
            w = item.widget()
            if w:
                w.deleteLater()
        wrapper = QFrame()
        wrapper.setObjectName("settingInputWrap")
        inner = QHBoxLayout(wrapper)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(0)
        widget.setMinimumWidth(180)
        widget.setMaximumWidth(220)
        inner.addWidget(widget)
        self._input_slot.addWidget(wrapper)


class FormFieldWithHint(QWidget):
    """Vertical label+hint+input for workflow forms."""

    def __init__(
        self,
        label: str,
        hint: str = "",
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.label_widget = QLabel(label)
        self.label_widget.setObjectName("settingLabel")
        layout.addWidget(self.label_widget)

        if hint:
            self.hint_label = QLabel(hint)
            self.hint_label.setObjectName("formFieldHint")
            self.hint_label.setWordWrap(True)
            layout.addWidget(self.hint_label)

        self._slot = QVBoxLayout()
        self._slot.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self._slot)

    def set_input(self, widget: QWidget) -> None:
        while self._slot.count():
            item = self._slot.takeAt(0)
            if item is None:
                continue
            w = item.widget()
            if w:
                w.deleteLater()
        self._slot.addWidget(widget)


def make_spin_setting(
    label: str,
    hint: str,
    value: int,
    min_val: int,
    max_val: int,
    step: int = 1,
) -> tuple[SettingRow, QSpinBox]:
    """Create a SettingRow with an integer spin box."""
    row = SettingRow(label, hint)
    spin = QSpinBox()
    spin.setAccessibleName(label)
    spin.setRange(min_val, max_val)
    spin.setSingleStep(step)
    spin.setValue(value)
    row.set_input(spin)
    return row, spin


def make_float_setting(
    label: str,
    hint: str,
    value: float,
    min_val: float,
    max_val: float,
    decimals: int = 2,
    step: float = 0.1,
) -> tuple[SettingRow, QDoubleSpinBox]:
    """Create a SettingRow with a float spin box."""
    row = SettingRow(label, hint)
    spin = QDoubleSpinBox()
    spin.setAccessibleName(label)
    spin.setRange(min_val, max_val)
    spin.setDecimals(decimals)
    spin.setSingleStep(step)
    spin.setValue(value)
    row.set_input(spin)
    return row, spin


def make_combo_setting(
    label: str,
    hint: str,
    items: list[str],
    current: str = "",
    item_labels: list[str] | None = None,
) -> tuple[SettingRow, QComboBox]:
    """Create a SettingRow with a combo box.

    Args:
        label: Setting label
        hint: Help text
        items: Internal values for each option
        current: Currently selected value
        item_labels: Optional display labels for each item.
                    If None, uses items as labels.
    """
    row = SettingRow(label, hint)
    combo = QComboBox()
    combo.setAccessibleName(label)
    if item_labels is None:
        combo.addItems(items)
    else:
        for i, item in enumerate(items):
            combo.addItem(item_labels[i] if i < len(item_labels) else item, item)
    if current:
        if item_labels is None:
            if current in items:
                combo.setCurrentText(current)
        else:
            idx = combo.findData(current)
            if idx >= 0:
                combo.setCurrentIndex(idx)
    row.set_input(combo)
    return row, combo


def make_line_setting(
    label: str,
    hint: str,
    value: str = "",
    placeholder: str = "",
    *,
    secret: bool = False,
) -> tuple[SettingRow, QLineEdit]:
    """Create a SettingRow with a line edit."""
    row = SettingRow(label, hint)
    line = QLineEdit(value if isinstance(value, str) else "")
    line.setAccessibleName(label)
    if placeholder:
        line.setPlaceholderText(placeholder)
    if secret:
        line.setEchoMode(QLineEdit.EchoMode.Password)
        line.setProperty("secretInput", True)

        container = QWidget()
        container.setObjectName("secretLineInput")
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        line.setMinimumWidth(120)
        layout.addWidget(line, 1)

        toggle = QToolButton()
        toggle.setObjectName("secretToggleButton")
        toggle.setCheckable(True)
        toggle.setText("显示")
        toggle.setAccessibleName(f"{label}显示切换")
        toggle.setToolTip("显示密钥")
        toggle.setMinimumHeight(32)
        toggle.setFixedWidth(54)

        def _sync_secret_visibility(visible: bool) -> None:
            line.setEchoMode(
                QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
            )
            toggle.setText("隐藏" if visible else "显示")
            toggle.setToolTip("隐藏密钥" if visible else "显示密钥")

        toggle.toggled.connect(_sync_secret_visibility)
        layout.addWidget(toggle)
        row.set_input(container)
        return row, line
    row.set_input(line)
    return row, line
