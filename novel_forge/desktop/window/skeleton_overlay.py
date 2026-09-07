"""Mixin module: skeleton_overlay methods for ``NovelForgeDesktopWindow``.

Auto-extracted in the M3.1 giant-file-split refactor. Methods live here so the
main :file:`window/__init__.py` facade can compose them via mixin inheritance.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    Qt,
    QTimer,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QLabel,
    QProgressBar,
    QVBoxLayout,
)

import novel_forge.desktop.page_registrations as _page_registrations  # noqa: F401
from novel_forge.desktop.constants import (
    animations_supported,
)
from novel_forge.desktop.theme import qcolor_hex, qcolor_rgba

_logger = logging.getLogger(__name__)

_WINDOW_DEFAULT_MIN_WIDTH = 1180
_WINDOW_DEFAULT_MIN_HEIGHT = 760
_WINDOW_DEFAULT_START_WIDTH = 1440
_WINDOW_DEFAULT_START_HEIGHT = 900
_WINDOW_SCREEN_WIDTH_RATIO = 0.92
_WINDOW_SCREEN_HEIGHT_RATIO = 0.90
_COMPACT_WIDTH_THRESHOLD = 1360
_COMPACT_HEIGHT_THRESHOLD = 820

if TYPE_CHECKING:
    pass


class SkeletonOverlayMixin:
    """Mixin that contributes the **skeleton_overlay** method group."""

    def _show_skeleton_overlay(self) -> None:
        """Show a semi-transparent loading overlay during first workspace refresh."""
        if self._skeleton_overlay is not None:
            return

        overlay = QFrame(self)
        overlay.setObjectName("skeletonOverlay")
        overlay.setStyleSheet(
            f"""
            QFrame#skeletonOverlay {{
                background: {qcolor_rgba("bg.sidebar.start", 0.54)};
                border-radius: 4px;
            }}
            QFrame#skeletonCard {{
                background: {qcolor_rgba("bg.surface", 0.96)};
                border: 1px solid {qcolor_rgba("border.default", 0.22)};
                border-radius: 16px;
            }}
            QLabel#skeletonBrand {{
                color: {qcolor_hex("accent.primary")};
                font-size: 11px;
                font-weight: 700;
            }}
            QLabel#skeletonTitle {{
                color: {qcolor_hex("text.primary")};
                font-size: 18px;
                font-weight: 800;
            }}
            QLabel#skeletonDetail {{
                color: {qcolor_hex("text.muted")};
                font-size: 12px;
            }}
            QLabel#skeletonLoading {{
                color: {qcolor_hex("accent.primary")};
                font-size: 13px;
                font-weight: 700;
            }}
            QProgressBar#skeletonProgress {{
                background: {qcolor_rgba("border.default", 0.12)};
                border: none;
                border-radius: 3px;
            }}
            QProgressBar#skeletonProgress::chunk {{
                background: {qcolor_hex("accent.primary")};
                border-radius: 3px;
            }}
            """
        )
        overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        overlay.setGeometry(self.rect())

        overlay_layout = QVBoxLayout(overlay)
        overlay_layout.setContentsMargins(0, 0, 0, 0)
        overlay_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        card = QFrame(overlay)
        card.setObjectName("skeletonCard")
        card.setFixedSize(360, 168)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(28, 22, 28, 22)
        card_layout.setSpacing(8)

        brand = QLabel("NOVEL FORGE", card)
        brand.setObjectName("skeletonBrand")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(brand)

        title = QLabel("正在唤醒砚台", card)
        title.setObjectName("skeletonTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(title)

        detail = QLabel("整理卷库、任务与最近进度", card)
        detail.setObjectName("skeletonDetail")
        detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(detail)
        self._skeleton_detail_label = detail

        progress = QProgressBar(card)
        progress.setObjectName("skeletonProgress")
        progress.setRange(0, 0)
        progress.setTextVisible(False)
        progress.setFixedHeight(6)
        card_layout.addWidget(progress)

        self._skeleton_loading_label = QLabel("加载中", card)
        self._skeleton_loading_label.setObjectName("skeletonLoading")
        self._skeleton_loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self._skeleton_loading_label)

        overlay_layout.addWidget(card)
        self._skeleton_overlay = overlay
        overlay.raise_()
        overlay.show()

        self._skeleton_loading_tick = 0
        timer = QTimer(self)
        timer.setInterval(360)
        timer.timeout.connect(self._tick_skeleton_loading)
        timer.start()
        self._skeleton_loading_timer = timer

        if animations_supported():
            effect = QGraphicsOpacityEffect(overlay)
            overlay.setGraphicsEffect(effect)
            fade = QPropertyAnimation(effect, b"opacity", self)
            fade.setDuration(180)
            fade.setStartValue(0.0)
            fade.setEndValue(1.0)
            fade.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._skeleton_fade_animation = fade
            fade.start()

    def _tick_skeleton_loading(self) -> None:
        label = self._skeleton_loading_label
        if label is None:
            return
        self._skeleton_loading_tick += 1
        label.setText(f"加载中{'.' * (self._skeleton_loading_tick % 4)}")
        detail = self._skeleton_detail_label
        if detail is not None:
            messages = (
                "整理卷库、任务与最近进度",
                "同步章节、报告与工作流状态",
                "准备案头与卷帙视图",
                "校准可用模型与运行环境",
            )
            detail.setText(messages[(self._skeleton_loading_tick // 2) % len(messages)])

    def _hide_skeleton_overlay(self) -> None:
        """Hide the skeleton overlay with a short delay to avoid flicker."""
        overlay = self._skeleton_overlay
        if overlay is None:
            return

        timer = self._skeleton_loading_timer
        if timer is not None:
            timer.stop()
            timer.deleteLater()
            self._skeleton_loading_timer = None
        self._skeleton_detail_label = None
        self._skeleton_loading_label = None

        def _do_hide() -> None:
            if self._skeleton_overlay is not overlay:
                return
            try:
                overlay.hide()
                overlay.deleteLater()
            except RuntimeError:
                # The window may have been closed before a delayed fade callback fires.
                pass
            self._skeleton_overlay = None
            self._skeleton_fade_animation = None

        if not animations_supported():
            self._safe_deferred(80, _do_hide)
            return

        effect = overlay.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(overlay)
            effect.setOpacity(1.0)
            overlay.setGraphicsEffect(effect)

        fade = QPropertyAnimation(effect, b"opacity", self)
        fade.setDuration(180)
        fade.setStartValue(effect.opacity())
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.Type.InCubic)
        fade.finished.connect(_do_hide)
        self._skeleton_fade_animation = fade
        fade.start()
