"""Container and layout components: scroll pages, cards, and collapsible sections."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

from PySide6.QtCore import QEasingCurve, QSettings, QSize, Qt, QVariantAnimation
from PySide6.QtGui import (
    QColor,
    QIcon,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from novel_forge.desktop.components.primitives import Surface, clear_layout
from novel_forge.desktop.components.scroll_fade import (
    ScrollEdgeFade,
    ensure_scroll_edge_fade,
)
from novel_forge.desktop.constants import animations_supported
from novel_forge.desktop.motion import Motion
from novel_forge.desktop.tokens.radius import DIALOG_RADIUS, SURFACE_RADIUS
from novel_forge.desktop.tokens.spacing import SPACING

_FadeTopOverlay = ScrollEdgeFade

_RESOURCES_DIR = Path(__file__).resolve().parent.parent / "resources"
_COLLAPSE_ICON_EXPANDED = QIcon(str((_RESOURCES_DIR / "collapse_down.svg").resolve()))
_COLLAPSE_ICON_COLLAPSED = QIcon(str((_RESOURCES_DIR / "collapse_right.svg").resolve()))


class ScrollPage(QScrollArea):
    """A padded scrollable page with a smooth fade-out at the top edge."""

    _PADDING_H: int = SPACING["space-7"]
    _PADDING_TOP: int = SPACING["space-4"]
    _PADDING_BOTTOM: int = SPACING["space-8"]
    _CHILD_SPACING: int = SPACING["space-4"]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        # transparent background, but matches SURFACE_RADIUS for future consistency
        self.setStyleSheet(f"QScrollArea {{ border-radius: {SURFACE_RADIUS}px; }}")

        self._container = QWidget()
        self._container.setObjectName("pageContainer")
        self.setWidget(self._container)
        # setWidgetResizable must come AFTER setWidget — otherwise Qt
        # doesn't re-run the layout pass and the inner widget stays
        # pinned to the viewport, leaving the scrollbar at maximum=0.
        self.setWidgetResizable(True)

        self.body_layout = QVBoxLayout(self._container)
        self.body_layout.setContentsMargins(
            self._PADDING_H,
            self._PADDING_TOP,
            self._PADDING_H,
            self._PADDING_BOTTOM,
        )
        self.body_layout.setSpacing(self._CHILD_SPACING)

        self._top_fade = ensure_scroll_edge_fade(
            self,
            corner_radius=float(DIALOG_RADIUS),
        )
        assert self._top_fade is not None
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._sync_overlay()

    def _on_scroll(self) -> None:
        self._top_fade.update()

    def _sync_overlay(self) -> None:
        self._top_fade.sync_geometry()

    def set_fade_height(self, height: int) -> None:
        self._top_fade.set_fade_height(height)

    def set_fade_enabled(self, enabled: bool) -> None:
        self._top_fade.set_enabled(enabled)

    def set_fade_color(self, color: QColor) -> None:
        self._top_fade.set_color(color)

    def sync_body_height(self) -> None:
        """Keep the scroll content height aligned with its current layout hint."""
        self.body_layout.activate()
        target_height = max(self.viewport().height(), self.body_layout.sizeHint().height())
        if self._container.maximumHeight() != target_height:
            self._container.setMaximumHeight(target_height)
        if self._container.height() != target_height:
            self._container.resize(self._container.width(), target_height)
        self._container.updateGeometry()


class MetricCard(Surface):
    """Large numeric summary card."""

    def __init__(
        self,
        title: str,
        value: str = "",
        detail: str = "",
        *,
        compact: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("card", parent)
        self.setProperty("compact", compact)
        self.setMinimumHeight(74 if compact else 120)

        layout = QVBoxLayout(self)
        if compact:
            layout.setContentsMargins(12, 10, 12, 10)
        else:
            layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(4 if compact else 8)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("metricTitle")
        self.title_label.setProperty("compact", compact)
        layout.addWidget(self.title_label)

        self.value_label = QLabel(value)
        self.value_label.setObjectName("metricValue")
        self.value_label.setProperty("compact", compact)
        layout.addWidget(self.value_label)

        self.detail_label = QLabel(detail)
        self.detail_label.setObjectName("metricDetail")
        self.detail_label.setProperty("compact", compact)
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.detail_label)
        layout.addStretch()

    def set_content(self, title: str, value: str, detail: str) -> None:
        self.title_label.setText(title)
        self.value_label.setText(value)
        self.detail_label.setText(detail)


class EmptyState(Surface):
    """Subtle empty state surface with optional icon and fade-in entry."""

    _ICON_MIN: int = 64
    _ICON_MAX: int = 128

    def __init__(
        self,
        title: str,
        message: str,
        *,
        icon: QIcon | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("inset", parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            SPACING["space-6"],
            SPACING["space-5"],
            SPACING["space-6"],
            SPACING["space-5"],
        )
        layout.setSpacing(SPACING["space-1.5"])
        # Do NOT set layout alignment — it breaks wordWrap height calculation in PySide6.
        # Instead use per-label alignment and stretches for visual centering.
        layout.addStretch()

        self.icon_label: QLabel | None = None
        if icon is not None and not icon.isNull():
            self.icon_label = QLabel()
            self.icon_label.setObjectName("emptyIcon")
            self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.icon_label.setProperty("role", "icon")
            self._cached_icon = icon
            self._update_icon_size()
            layout.addWidget(self.icon_label)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("emptyTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title_label)

        self.message_label = QLabel(message)
        self.message_label.setObjectName("emptyMessage")
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)

        layout.addStretch()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self.icon_label is not None:
            self._update_icon_size()

    def _update_icon_size(self) -> None:
        """Clamp the icon's pixel size into the responsive range.

        Base size is ``width // 3`` so the icon scales with the
        container; the result is clamped to ``[_ICON_MIN, _ICON_MAX]``
        so very small or very large containers still get a usable glyph.
        """
        if self.icon_label is None:
            return
        available = max(self.width(), 1)
        target = max(self._ICON_MIN, min(available // 3, self._ICON_MAX))
        if hasattr(self, "_cached_icon") and not self._cached_icon.isNull():
            self.icon_label.setPixmap(self._cached_icon.pixmap(target, target))

    def _play_enter_animation(self, *, duration: int | str = "modal") -> None:
        """Run a fade-in on the whole widget (no geometry changes).

        macOS-safe (D1): opacity is on the safe list; geometry is not.

        EmptyState's ``Surface("inset")`` parent installs a
        ``QGraphicsDropShadowEffect``.  ``Motion.fade_in`` installs a
        ``QGraphicsOpacityEffect`` on top, but Qt only allows a single
        graphics effect per widget (D10).  We swap the shadow out
        during the fade and restore it when the animation finishes —
        this keeps the surface elevation intact while still letting
        the EmptyState fade in cleanly.
        """
        from PySide6.QtCore import QPropertyAnimation
        from PySide6.QtWidgets import QGraphicsEffect, QGraphicsOpacityEffect

        prior_effect = self.graphicsEffect()
        if prior_effect is not None:
            # PySide6 accepts None to detach; cast keeps mypy happy.
            self.setGraphicsEffect(cast(QGraphicsEffect, None))

        effect = QGraphicsOpacityEffect(self)
        effect.setOpacity(0.0)
        self.setGraphicsEffect(effect)

        anim = QPropertyAnimation(effect, b"opacity")
        anim.setDuration(Motion._resolve_duration(duration))
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

        def _on_finished() -> None:
            self.setGraphicsEffect(cast(QGraphicsEffect, None))
            if prior_effect is not None:
                self.setGraphicsEffect(prior_effect)

        anim.finished.connect(_on_finished)

    def set_content(self, title: str, message: str) -> None:
        self.title_label.setText(title)
        self.message_label.setText(message)


def add_card_grid(
    layout: QGridLayout,
    widgets: list[QWidget],
    *,
    columns: int,
) -> None:
    """Rebuild a grid layout with a flat widget list."""
    clear_layout(layout)
    for index, widget in enumerate(widgets):
        row = index // columns
        column = index % columns
        layout.addWidget(widget, row, column)


class CollapsibleSection(QWidget):
    """A collapsible panel with a toggle header and an expandable body.

    Pass ``persist_key`` to remember the user's last expanded/collapsed state
    across page rebuilds via QSettings; the stored value (when present) wins
    over the ``expanded`` default, and user toggles are written back on click.
    """

    _SETTINGS_ORG = "NovelForge"
    _SETTINGS_APP = "UIState"

    def __init__(
        self,
        title: str,
        *,
        expanded: bool = False,
        nested: bool = False,
        persist_key: str | None = None,
        lazy_body_builder: Callable[[QVBoxLayout], None] | None = None,
        defer_initial_body_build: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        from PySide6.QtWidgets import QPushButton

        super().__init__(parent)
        from PySide6.QtWidgets import QVBoxLayout as _VBox

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = _VBox(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._title_text = title
        self._persist_key = persist_key
        self._lazy_body_builder = lazy_body_builder
        self._body_built = lazy_body_builder is None
        if persist_key is not None:
            persisted = self._load_expanded_from_settings(persist_key)
            if persisted is not None:
                expanded = persisted

        self._toggle = QPushButton(title)
        self._toggle.setObjectName("collapseToggle")
        if nested:
            self._toggle.setProperty("nested", "true")
        self._toggle.setCheckable(True)
        self._toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._toggle.setAccessibleName(f"Toggle section: {title}")
        self._toggle.setIconSize(QSize(10, 10))
        self._toggle.clicked.connect(self._on_toggled)
        outer.addWidget(self._toggle)

        self._body_frame = QFrame()
        self._body_frame.setObjectName("collapseBody")
        if nested:
            self._body_frame.setProperty("nested", "true")
        self._body_frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._body_frame.setAutoFillBackground(True)
        self.body_layout = QVBoxLayout(self._body_frame)
        if nested:
            self.body_layout.setContentsMargins(14, 10, 14, 12)
        else:
            self.body_layout.setContentsMargins(18, 12, 18, 16)
        self.body_layout.setSpacing(8 if nested else 10)
        outer.addWidget(self._body_frame)

        self._toggle.setChecked(expanded)
        if expanded and lazy_body_builder is not None and not defer_initial_body_build:
            self.ensure_body_built()
        self._body_frame.setVisible(expanded)
        self._sync_toggle_visual_state(expanded)

        self._animations_enabled = animations_supported()
        self._height_animation: QVariantAnimation | None = None

    @property
    def body_built(self) -> bool:
        """Whether the optional lazy body has been constructed."""
        return self._body_built

    @property
    def is_expanded(self) -> bool:
        """Whether the section body is currently expanded."""
        return self._toggle.isChecked()

    def ensure_body_built(self) -> None:
        """Build the lazy body once, if this section was created with a builder."""
        if self._body_built:
            return
        builder = self._lazy_body_builder
        if builder is None:
            self._body_built = True
            return
        builder(self.body_layout)
        self._body_built = True

    @classmethod
    def _load_expanded_from_settings(cls, key: str) -> bool | None:
        """Return the persisted expanded state for ``key``, or None if absent/invalid.

        QSettings can fail in headless test runners or with corrupt storage;
        we never want persistence to crash the page build.
        """
        try:
            settings = QSettings(cls._SETTINGS_ORG, cls._SETTINGS_APP)
        except Exception:
            return None
        try:
            # ``contains`` is the only reliable way to distinguish a missing
            # key from a stored ``False`` — ``value(..., type=bool)`` would
            # coerce the default ``None`` to ``False`` and silently flip the
            # initial state on first run.
            if not settings.contains(key):
                return None
            value = settings.value(key, None, type=bool)
        except Exception:
            return None
        return value if isinstance(value, bool) else None

    def _save_expanded_to_settings(self, expanded: bool) -> None:
        if self._persist_key is None:
            return
        try:
            settings = QSettings(self._SETTINGS_ORG, self._SETTINGS_APP)
            settings.setValue(self._persist_key, bool(expanded))
        except Exception:
            # Best-effort: a persistence failure must not break the UI.
            pass

    def _on_toggled(self) -> None:
        expanded = self._toggle.isChecked()
        if expanded:
            self.ensure_body_built()
        self._sync_toggle_visual_state(expanded)
        self._set_body_expanded(expanded, animated=True)
        self._save_expanded_to_settings(expanded)

    def set_title(self, title: str) -> None:
        self._title_text = title
        self._toggle.setText(title)
        self._sync_toggle_visual_state(self._toggle.isChecked())

    def set_expanded(self, expanded: bool) -> None:
        if expanded:
            self.ensure_body_built()
        self._toggle.setChecked(expanded)
        self._sync_toggle_visual_state(expanded)
        self._set_body_expanded(expanded, animated=True)
        self._save_expanded_to_settings(expanded)

    def _sync_toggle_visual_state(self, expanded: bool) -> None:
        self._toggle.setText(self._title_text)
        self._toggle.setIcon(_COLLAPSE_ICON_EXPANDED if expanded else _COLLAPSE_ICON_COLLAPSED)

    def _set_body_expanded(self, expanded: bool, *, animated: bool) -> None:
        if expanded:
            self.ensure_body_built()
        if self._height_animation is not None:
            self._height_animation.stop()
            self._height_animation = None

        if not animated or not self._animations_enabled:
            self._body_frame.setMinimumHeight(0)
            self._body_frame.setMaximumHeight(16777215 if expanded else 0)
            self._body_frame.setVisible(expanded)
            return

        if expanded:
            # Keep the body height pinned during the animation so parent scroll
            # layouts do not re-run size-hint probing on every frame.
            self._body_frame.setFixedHeight(0)
            self._body_frame.setVisible(True)

            root_layout = self.layout()
            body_layout = self._body_frame.layout()
            if root_layout is not None:
                root_layout.activate()
            if body_layout is not None:
                body_layout.activate()
            content_height = self._body_frame.sizeHint().height()
            if content_height <= 0 and body_layout is not None:
                content_height = body_layout.sizeHint().height()

            if content_height <= 0:
                self._body_frame.setMinimumHeight(0)
                self._body_frame.setMaximumHeight(16777215)
                return

            start_height = 0
            end_height = content_height
        else:
            content_height = self._body_frame.height()
            if content_height <= 0:
                body_layout = self._body_frame.layout()
                if body_layout is not None:
                    body_layout.activate()
                    content_height = body_layout.sizeHint().height()
            if content_height <= 0:
                self._body_frame.setVisible(False)
                self._body_frame.setMinimumHeight(0)
                self._body_frame.setMaximumHeight(0)
                return

            self._body_frame.setFixedHeight(content_height)
            start_height = content_height
            end_height = 0

        animation = QVariantAnimation(self)
        animation.setDuration(200)
        animation.setStartValue(start_height)
        animation.setEndValue(end_height)
        animation.setEasingCurve(
            QEasingCurve.Type.OutCubic if expanded else QEasingCurve.Type.InCubic
        )

        def _on_value(value: object) -> None:
            if isinstance(value, (int, float)):
                self._body_frame.setFixedHeight(int(value))

        def _on_finished() -> None:
            if not expanded:
                self._body_frame.setVisible(False)
            self._body_frame.setMinimumHeight(0)
            self._body_frame.setMaximumHeight(16777215)
            self._height_animation = None

        animation.valueChanged.connect(_on_value)
        animation.finished.connect(_on_finished)
        animation.start()
        self._height_animation = animation
