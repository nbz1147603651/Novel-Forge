"""Mixin module: panels.side_rail methods for ``NovelForgeDesktopWindow``.

Auto-extracted in the M3.1 giant-file-split refactor. Methods live here so the
main :file:`window/__init__.py` facade can compose them via mixin inheritance.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6 import QtCore, QtGui
from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QParallelAnimationGroup,
    QPropertyAnimation,
    Qt,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import novel_forge.desktop.page_registrations as _page_registrations  # noqa: F401
from novel_forge.desktop.brand_assets import build_brand_pixmap
from novel_forge.desktop.constants import (
    COMPACT_HEIGHT_THRESHOLD,
    COMPACT_WIDTH_THRESHOLD,
    SIDE_RAIL_ANIMATION_MS,
    SIDE_RAIL_COLLAPSED_WIDTH,
    SIDE_RAIL_COMPACT_WIDTH,
    SIDE_RAIL_EXPANDED_WIDTH,
    WINDOW_DEFAULT_MIN_HEIGHT,
    WINDOW_DEFAULT_MIN_WIDTH,
    WINDOW_DEFAULT_START_HEIGHT,
    WINDOW_DEFAULT_START_WIDTH,
    WINDOW_SCREEN_HEIGHT_RATIO,
    WINDOW_SCREEN_WIDTH_RATIO,
)
from novel_forge.desktop.registry import page_registry
from novel_forge.desktop.strings import UIStrings
from novel_forge.desktop.theme.runtime import current_application_theme
from novel_forge.desktop.window._widgets import CachedGradientFrame

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass


from novel_forge.desktop.window._widgets import (  # noqa: E402
    NavigationButton,
)

# Local aliases for backward compatibility with existing code patterns
_WINDOW_DEFAULT_MIN_WIDTH = WINDOW_DEFAULT_MIN_WIDTH
_WINDOW_DEFAULT_MIN_HEIGHT = WINDOW_DEFAULT_MIN_HEIGHT
_WINDOW_DEFAULT_START_WIDTH = WINDOW_DEFAULT_START_WIDTH
_WINDOW_DEFAULT_START_HEIGHT = WINDOW_DEFAULT_START_HEIGHT
_WINDOW_SCREEN_WIDTH_RATIO = WINDOW_SCREEN_WIDTH_RATIO
_WINDOW_SCREEN_HEIGHT_RATIO = WINDOW_SCREEN_HEIGHT_RATIO
_COMPACT_WIDTH_THRESHOLD = COMPACT_WIDTH_THRESHOLD
_COMPACT_HEIGHT_THRESHOLD = COMPACT_HEIGHT_THRESHOLD


class SideRailMixin:
    """Mixin that contributes the **panels.side_rail** method group."""

    _SIDE_RAIL_EXPANDED_WIDTH: int = SIDE_RAIL_EXPANDED_WIDTH
    _SIDE_RAIL_COMPACT_WIDTH: int = SIDE_RAIL_COMPACT_WIDTH
    _SIDE_RAIL_COLLAPSED_WIDTH: int = SIDE_RAIL_COLLAPSED_WIDTH
    _SIDE_RAIL_ANIMATION_MS: int = SIDE_RAIL_ANIMATION_MS

    def _side_rail_expanded_width(self) -> int:
        return (
            self._SIDE_RAIL_COMPACT_WIDTH
            if getattr(self, "_layout_density", "") == "compact"
            else self._SIDE_RAIL_EXPANDED_WIDTH
        )

    def _side_rail_target_width(self) -> int:
        if getattr(self, "_side_rail_collapsed", False):
            return self._SIDE_RAIL_COLLAPSED_WIDTH
        return self._side_rail_expanded_width()

    def _apply_side_rail_layout_density(self) -> None:
        layout = getattr(self, "_side_rail_layout", None)
        if layout is None:
            return
        compact = getattr(self, "_layout_density", "") == "compact"
        collapsed = getattr(self, "_side_rail_collapsed", False)
        if collapsed:
            layout.setContentsMargins(8, 22 if compact else 28, 8, 18)
            layout.setSpacing(0)
            return
        layout.setContentsMargins(
            20 if compact else 24,
            28 if compact else 38,
            20 if compact else 24,
            20 if compact else 24,
        )
        layout.setSpacing(0)

    def _set_side_rail_content_visible(self, visible: bool) -> None:
        for widget in getattr(self, "_side_rail_collapsible_widgets", []):
            try:
                widget.setVisible(visible)
            except RuntimeError:
                continue

    def _animate_side_rail_width(self, target_width: int) -> None:
        rail = getattr(self, "_side_rail", None)
        if rail is None:
            return
        current_width = rail.width() or rail.minimumWidth() or rail.maximumWidth()
        if current_width == target_width:
            rail.setMinimumWidth(target_width)
            rail.setMaximumWidth(target_width)
            return
        width_anim = getattr(self, "_side_rail_width_anim", None)
        if width_anim is not None:
            width_anim.stop()
        group = QParallelAnimationGroup(rail)
        for prop_name in (b"minimumWidth", b"maximumWidth"):
            anim = QPropertyAnimation(rail, prop_name)
            anim.setDuration(self._SIDE_RAIL_ANIMATION_MS)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.setStartValue(current_width)
            anim.setEndValue(target_width)
            group.addAnimation(anim)
        self._side_rail_width_anim = group

        def _finish() -> None:
            try:
                rail.setMinimumWidth(target_width)
                rail.setMaximumWidth(target_width)
            except RuntimeError:
                pass
            if getattr(self, "_side_rail_width_anim", None) is group:
                self._side_rail_width_anim = None
            if not getattr(self, "_side_rail_collapsed", False):
                self._sync_active_indicator_geometry()

        group.finished.connect(_finish)
        group.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def _sync_side_rail_collapsed(self, *, animate: bool = False) -> None:
        rail = getattr(self, "_side_rail", None)
        if rail is None:
            return
        collapsed = bool(getattr(self, "_side_rail_collapsed", False))
        target_width = self._side_rail_target_width()
        rail.setProperty("collapsed", "true" if collapsed else "false")
        toggle = getattr(self, "_side_rail_toggle_btn", None)
        if toggle is not None:
            toggle.setText(">" if collapsed else "<")
            toggle.setToolTip("展开主页切换" if collapsed else "隐藏主页切换")
        self._set_side_rail_content_visible(not collapsed)
        self._apply_side_rail_layout_density()
        self._refresh_widget_style(rail)
        indicator = getattr(self, "_active_indicator", None)
        if collapsed and indicator is not None:
            indicator.setVisible(False)
        if animate and rail.isVisible():
            self._animate_side_rail_width(target_width)
        else:
            rail.setMinimumWidth(target_width)
            rail.setMaximumWidth(target_width)
            if not collapsed:
                self._safe_deferred(0, self._sync_active_indicator_geometry)

    def _set_side_rail_collapsed(self, collapsed: bool, *, animate: bool = True) -> None:
        collapsed = bool(collapsed)
        if getattr(self, "_side_rail_collapsed", False) == collapsed:
            self._sync_side_rail_collapsed(animate=False)
            return
        self._side_rail_collapsed = collapsed
        self._sync_side_rail_collapsed(animate=animate)
        if getattr(self, "_ui_session_restored", False):
            self._schedule_ui_session_save()

    def _toggle_side_rail(self) -> None:
        self._set_side_rail_collapsed(
            not getattr(self, "_side_rail_collapsed", False),
            animate=True,
        )

    def _refresh_brand_logo(self, theme_id: str | None = None) -> None:
        label = getattr(self, "_brand_logo_label", None)
        if label is None:
            return
        size = int(getattr(self, "_brand_logo_size", 122))
        dpr = 1.0
        for candidate in (label.window(), label):
            try:
                if candidate is not None and hasattr(candidate, "devicePixelRatioF"):
                    dpr = max(dpr, float(candidate.devicePixelRatioF()))
            except RuntimeError:
                continue
        pixmap = build_brand_pixmap(
            QtGui,
            QtCore,
            theme_id=theme_id or current_application_theme(),
            size=size,
            device_pixel_ratio=dpr,
        )
        if pixmap is None or pixmap.isNull():
            label.setPixmap(QtGui.QPixmap())
            label.setText(UIStrings.BRAND_MARK)
            label.setTextFormat(Qt.TextFormat.PlainText)
            return
        label.setText("")
        label.setPixmap(pixmap)

    def _build_side_rail(self) -> QWidget:
        from novel_forge.desktop.theme.palettes import (
            DEFAULT_DESKTOP_THEME_ID,
            desktop_theme_token_overrides,
        )
        from novel_forge.desktop.tokens.colors import COLORS

        theme_id = DEFAULT_DESKTOP_THEME_ID
        try:
            from novel_forge.core.config import get_settings
            theme_id = getattr(get_settings(), "desktop_theme", theme_id) or theme_id
        except Exception:
            pass
        overrides = desktop_theme_token_overrides(theme_id)

        def _tok(name: str) -> str:
            return overrides.get(name, COLORS[name][0])

        rail = CachedGradientFrame(
            stops=[
                (0.0, _tok("bg.sidebar.start")),
                (0.45, _tok("bg.sidebar.mid")),
                (1.0, _tok("bg.sidebar.end")),
            ],
            angle=270.0,
        )
        rail.setObjectName("sideRail")
        # Use min/max width instead of fixed width so the rail can adapt
        # to content while still respecting layout constraints.
        target_width = self._side_rail_target_width()
        rail.setMinimumWidth(target_width)
        rail.setMaximumWidth(target_width)
        self._side_rail = rail

        layout = QVBoxLayout(rail)
        self._side_rail_layout = layout
        layout.setContentsMargins(24, 14, 24, 24)
        layout.setSpacing(0)

        header = QWidget()
        header.setObjectName("sideRailHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(0)

        toggle_reserved_width = 30
        toggle_balance = QWidget()
        toggle_balance.setFixedSize(toggle_reserved_width, toggle_reserved_width)
        self._side_rail_toggle_balance = toggle_balance
        header_layout.addWidget(toggle_balance)
        header_layout.addStretch(1)

        brand_logo_frame = QFrame()
        brand_logo_frame.setObjectName("brandLogoFrame")
        logo_size = 112
        brand_logo_frame.setFixedSize(logo_size, logo_size)
        logo_layout = QVBoxLayout(brand_logo_frame)
        logo_layout.setContentsMargins(0, 0, 0, 0)
        logo_layout.setSpacing(0)

        brand_logo = QLabel()
        brand_logo.setObjectName("brandLogo")
        brand_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_logo.setToolTip(UIStrings.BRAND_TITLE)
        brand_logo.setFixedSize(logo_size, logo_size)
        self._brand_logo_label = brand_logo
        self._brand_logo_size = logo_size
        self._refresh_brand_logo()
        logo_layout.addWidget(brand_logo)
        header_layout.addWidget(brand_logo_frame, 0, Qt.AlignmentFlag.AlignHCenter)
        header_layout.addStretch(1)

        toggle = QToolButton()
        toggle.setObjectName("sideRailToggle")
        toggle.setText("<")
        toggle.setToolTip("隐藏主页切换")
        toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        toggle.setFixedSize(toggle_reserved_width, toggle_reserved_width)
        toggle.clicked.connect(self._toggle_side_rail)
        self._side_rail_toggle_btn = toggle
        header_layout.addWidget(toggle)
        layout.addWidget(header)
        layout.addSpacing(4)

        brand_panel = QWidget()
        brand_panel.setObjectName("sideRailBrandPanel")
        brand_layout = QVBoxLayout(brand_panel)
        brand_layout.setContentsMargins(2, 0, 2, 0)
        brand_layout.setSpacing(4)

        brand_title = QLabel(UIStrings.BRAND_TITLE)
        brand_title.setObjectName("brandTitle")
        brand_title.setTextFormat(Qt.TextFormat.PlainText)
        brand_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_layout.addWidget(brand_title)

        # 装饰纹样：品牌标识下方的古典纹饰
        brand_ornament = QLabel("\u2500\u2500  \u53d9\u4e8b\u5de5\u574a  \u2500\u2500")
        brand_ornament.setObjectName("brandOrnament")
        brand_ornament.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_layout.addWidget(brand_ornament)

        brand_subtitle = QLabel(UIStrings.BRAND_SUBTITLE)
        brand_subtitle.setObjectName("brandSubtitle")
        brand_subtitle.setWordWrap(True)
        brand_subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_layout.addWidget(brand_subtitle)
        brand_layout.addSpacing(6)

        rail_sep = QFrame()
        rail_sep.setObjectName("railSep")
        rail_sep.setFixedHeight(1)
        brand_layout.addWidget(rail_sep)
        layout.addWidget(brand_panel)
        layout.addSpacing(8)

        nav_panel = QWidget()
        nav_panel.setObjectName("sideRailNavPanel")
        nav_layout = QVBoxLayout(nav_panel)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(8)
        self._nav_buttons: dict[str, NavigationButton] = {}
        for page_id in page_registry.list_pages():
            meta = page_registry.metadata(page_id)
            if meta is None:
                continue

            # 美学修饰：案头 -> · 案 头 ·
            label_text = meta.label
            if len(label_text) == 2:
                # 使用全角空格(　)或普通空格拉开字距
                label_text = f"·  {label_text[0]} {label_text[1]}  ·"

            button = NavigationButton(label_text)
            button.clicked.connect(lambda checked=False, pid=page_id: self.switch_page(pid))
            self._nav_buttons[page_id] = button
            nav_layout.addWidget(button)

        # Task 14 — set ``active`` property. Dashboard is the default
        # selected page; all others are inactive. ``set_active()`` also
        # re-polishes the style so the [active="true"] selector matches.
        for pid, btn in self._nav_buttons.items():
            btn.set_active(pid == "dashboard")
        self._nav_buttons["dashboard"].setChecked(True)

        # Nav buttons are added directly to the layout; the reduced font
        # size (18pt) ensures all items fit without scrolling.
        layout.addWidget(nav_panel)

        # Task 14 — Active indicator (3px wide brick bar) is an overlay
        # child of ``rail``, deliberately NOT inserted into the layout
        # (we set its geometry manually). It's hidden until the layout
        # settles — ``_animate_active_indicator`` is called after
        # ``switch_page`` with the layout activated.
        self._active_indicator: QWidget = QWidget(rail)
        self._active_indicator.setObjectName("activeNavIndicator")
        self._active_indicator.setFixedWidth(3)
        self._active_indicator.setVisible(False)
        self._active_indicator.raise_()
        self._indicator_anim: QPropertyAnimation | None = None

        layout.addStretch()

        footer = QFrame()
        footer.setObjectName("railFooter")
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(12, 12, 12, 12)
        footer_layout.setSpacing(6)

        self._rail_summary = QLabel(UIStrings.RAIL_FOOTER_SLEEPING)
        self._rail_summary.setObjectName("railFooterTitle")
        footer_layout.addWidget(self._rail_summary)

        self._rail_path = QLabel(UIStrings.RAIL_FOOTER_WAITING)
        self._rail_path.setObjectName("railFooterMeta")
        self._rail_path.setWordWrap(True)
        footer_layout.addWidget(self._rail_path)

        layout.addWidget(footer)
        self._side_rail_collapsible_widgets = [
            toggle_balance,
            brand_logo_frame,
            brand_panel,
            nav_panel,
            footer,
        ]
        self._sync_side_rail_collapsed(animate=False)
        return rail
