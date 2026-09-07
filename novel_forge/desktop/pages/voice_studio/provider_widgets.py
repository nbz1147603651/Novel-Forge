"""Self-contained provider-switch and header widgets for Voice Studio.

Extracted from ``page.py`` to reduce the single-file size.  All classes
here are structurally decoupled from ``VoiceStudioPage`` - they reference
only each other and shared Qt/desktop widgets.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QEnterEvent,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPalette,
    QPen,
)
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from novel_forge.desktop.widgets import Badge

__all__ = (
    "ProviderDropdown",
    "ProviderPopup",
    "ProviderRow",
    "RadioIndicator",
    "HeaderStat",
    "InlineMetric",
)


class ProviderDropdown(QWidget):
    """Themed platform selector with radio-style popup.

    Replaces the plain QToolButton + QMenu with a custom widget that
    matches the app's visual design: rounded corners, hover effects,
    and a clear radio-style selection indicator.
    """

    item_selected = Signal(str)  # emits provider value

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("voiceProviderSwitch")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedHeight(36)

        self._items: list[tuple[str, str, str]] = []  # (value, label, models)
        self._selected_value: str = ""
        self._loading: bool = False
        self._popup: ProviderPopup | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 10, 0)
        layout.setSpacing(4)

        self._label = QLabel("平台")
        self._label.setObjectName("voiceProviderLabel")
        self._label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(self._label, 1)

        self._arrow = QLabel("▾")
        self._arrow.setObjectName("voiceProviderArrow")
        self._arrow.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._arrow.setFixedWidth(14)
        self._arrow.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._arrow, 0)

    def add_item(self, value: str, label: str, models: str) -> None:
        self._items.append((value, label, models))

    def set_display_text(self, text: str) -> None:
        self._label.setText(text)

    def set_selected(self, value: str) -> None:
        self._selected_value = value
        if self._popup is not None:
            self._popup.set_selected(value)

    def set_loading(self, loading: bool) -> None:
        self._loading = loading
        self.setProperty("loading", "true" if loading else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def _dismiss_popup(self) -> None:
        popup = self._popup
        self._popup = None
        if popup is not None:
            popup.close()

    def _toggle_popup(self) -> None:
        if self._loading:
            return
        if self._popup is not None and self._popup.isVisible():
            self._dismiss_popup()
            return

        # A popup closed by clicking elsewhere is hidden but may still be owned by us.
        if self._popup is not None:
            self._dismiss_popup()

        popup = ProviderPopup(self._items, self._selected_value, self)
        self._popup = popup
        popup.item_selected.connect(self._on_popup_selected)
        popup.destroyed.connect(lambda _=None, p=popup: self._on_popup_destroyed(p))
        popup.show_below(self)

    def _on_popup_destroyed(self, popup: ProviderPopup) -> None:
        if self._popup is popup:
            self._popup = None

    def _on_popup_selected(self, value: str) -> None:
        was_selected = value == self._selected_value
        self._dismiss_popup()
        if was_selected:
            return
        self._selected_value = value
        self.item_selected.emit(value)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self._toggle_popup()
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self._toggle_popup()
            event.accept()
            return
        super().keyPressEvent(event)


class ProviderPopup(QFrame):
    """Themed popup for platform selection."""

    item_selected = Signal(str)  # emits provider value

    def __init__(
        self,
        items: list[tuple[str, str, str]],
        selected_value: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.Popup, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setObjectName("voiceProviderPopup")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(2)

        self._rows: list[ProviderRow] = []
        for value, label, models in items:
            row = ProviderRow(value, label, models, selected_value == value, self)
            row.clicked.connect(lambda v=value: self.item_selected.emit(v))
            layout.addWidget(row)
            self._rows.append(row)

    def set_selected(self, value: str) -> None:
        for row in self._rows:
            row.set_checked(row.provider_value == value)

    def show_below(self, anchor: QWidget) -> None:
        anchor_pos = anchor.mapToGlobal(anchor.rect().bottomLeft())
        self.adjustSize()
        # Ensure popup stays within screen bounds
        screen = anchor.screen().availableGeometry()
        x = min(anchor_pos.x(), screen.right() - self.width() - 4)
        x = max(screen.left() + 4, x)
        y = anchor_pos.y() + 2
        if y + self.height() > screen.bottom():
            y = anchor.mapToGlobal(anchor.rect().topLeft()).y() - self.height() - 2
        y = max(screen.top() + 4, y)
        self.move(x, y)
        self.show()


class ProviderRow(QWidget):
    """Single row inside the provider popup."""

    clicked = Signal(str)  # emits provider value

    def __init__(
        self,
        value: str,
        label: str,
        models: str,
        checked: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.provider_value = value
        self._checked = checked
        self.setObjectName("voiceProviderRow")
        self.setProperty("checked", checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(34)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 12, 0)
        layout.setSpacing(8)

        # Radio indicator - custom painted for crisp rendering
        self._indicator = RadioIndicator(checked, self)
        self._indicator.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self._indicator, 0)

        # Provider name
        self._name = QLabel(label)
        self._name.setObjectName("voiceProviderName")
        self._name.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._name.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(self._name, 1)

        # Models hint
        if models:
            self._models = QLabel(models)
            self._models.setObjectName("voiceProviderModels")
            self._models.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            layout.addWidget(self._models, 0)

    def set_checked(self, checked: bool) -> None:
        self._checked = checked
        self.setProperty("checked", checked)
        self._indicator.set_checked(checked)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.provider_value)
        super().mousePressEvent(event)

    def enterEvent(self, event: QEnterEvent) -> None:
        self.setProperty("hover", True)
        self._indicator.set_hover(True)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self.setProperty("hover", False)
        self._indicator.set_hover(False)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()
        super().leaveEvent(event)


class RadioIndicator(QWidget):
    """Custom radio button indicator with smooth paint."""

    def __init__(self, checked: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._checked = checked
        self._hover = False
        self.setFixedSize(16, 16)

    def set_checked(self, checked: bool) -> None:
        self._checked = checked
        self.update()

    def set_hover(self, hover: bool) -> None:
        self._hover = hover
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        accent = palette.color(QPalette.ColorRole.Highlight)

        if self._checked:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(accent))
            painter.drawEllipse(1, 1, 14, 14)
            # The palette keeps the contrast correct in both light and dark themes.
            painter.setBrush(QBrush(palette.color(QPalette.ColorRole.Base)))
            painter.drawEllipse(5, 5, 6, 6)
        else:
            painter.setPen(QPen(palette.color(QPalette.ColorRole.Mid), 1.5))
            painter.setBrush(Qt.GlobalColor.transparent)
            painter.drawEllipse(1, 1, 14, 14)


class HeaderStat(QWidget):
    """Small factual counter for the shared window header."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("voiceHeaderStat")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 3, 10, 3)
        layout.setSpacing(0)
        self._title = QLabel(title)
        self._title.setObjectName("voiceHeaderStatTitle")
        self._value = QLabel("0")
        self._value.setObjectName("voiceHeaderStatValue")
        layout.addWidget(self._title)
        layout.addWidget(self._value)

    def set_content(self, title: str, value: str, detail: str = "") -> None:
        self._title.setText(title)
        self._value.setText(value)


class InlineMetric(Badge):
    """One-line metric compatible with the existing ``MetricCard`` update API."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(f"{title} 0", tone="muted", parent=parent)

    def set_content(self, title: str, value: str, detail: str = "") -> None:
        suffix = f" · {detail}" if detail else ""
        self.setText(f"{title} {value}{suffix}")
