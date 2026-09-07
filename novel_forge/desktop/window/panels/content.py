"""Mixin module: panels.content methods for ``NovelForgeDesktopWindow``.

Auto-extracted in the M3.1 giant-file-split refactor. Methods live here so the
main :file:`window/__init__.py` facade can compose them via mixin inheritance.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import (
    Qt,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import novel_forge.desktop.page_registrations as _page_registrations  # noqa: F401
from novel_forge.desktop.constants import (
    COMPACT_HEIGHT_THRESHOLD,
    COMPACT_WIDTH_THRESHOLD,
    WINDOW_DEFAULT_MIN_HEIGHT,
    WINDOW_DEFAULT_MIN_WIDTH,
    WINDOW_DEFAULT_START_HEIGHT,
    WINDOW_DEFAULT_START_WIDTH,
    WINDOW_SCREEN_HEIGHT_RATIO,
    WINDOW_SCREEN_WIDTH_RATIO,
)
from novel_forge.desktop.registry import page_registry
from novel_forge.desktop.strings import UIStrings
from novel_forge.desktop.window._lazy_page_map import _LazyPageMap

_logger = logging.getLogger(__name__)

_WINDOW_DEFAULT_MIN_WIDTH = WINDOW_DEFAULT_MIN_WIDTH
_WINDOW_DEFAULT_MIN_HEIGHT = WINDOW_DEFAULT_MIN_HEIGHT
_WINDOW_DEFAULT_START_WIDTH = WINDOW_DEFAULT_START_WIDTH
_WINDOW_DEFAULT_START_HEIGHT = WINDOW_DEFAULT_START_HEIGHT
_WINDOW_SCREEN_WIDTH_RATIO = WINDOW_SCREEN_WIDTH_RATIO
_WINDOW_SCREEN_HEIGHT_RATIO = WINDOW_SCREEN_HEIGHT_RATIO
_COMPACT_WIDTH_THRESHOLD = COMPACT_WIDTH_THRESHOLD
_COMPACT_HEIGHT_THRESHOLD = COMPACT_HEIGHT_THRESHOLD


class ContentMixin:
    """Mixin that contributes the **panels.content** method group."""

    def _build_content(self) -> QWidget:
        content = QWidget()
        content.setObjectName("contentShell")

        layout = QVBoxLayout(content)
        self._content_layout = layout
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        top_bar = QFrame()
        top_bar.setObjectName("topBar")
        self._top_bar = top_bar
        top_bar.setMinimumHeight(96)
        top_layout = QHBoxLayout(top_bar)
        self._top_layout = top_layout
        top_layout.setContentsMargins(22, 14, 22, 14)
        top_layout.setSpacing(10)

        title_block = QVBoxLayout()
        title_block.setSpacing(4)
        self._top_eyebrow = QLabel()
        self._top_eyebrow.setObjectName("topBarEyebrow")
        title_block.addWidget(self._top_eyebrow)

        self._top_title = QLabel()
        self._top_title.setObjectName("topBarTitle")
        title_block.addWidget(self._top_title)

        self._top_subtitle = QLabel()
        self._top_subtitle.setObjectName("topBarSubtitle")
        self._top_subtitle.setWordWrap(True)
        title_block.addWidget(self._top_subtitle)
        top_layout.addLayout(title_block, 1)

        controls = QHBoxLayout()
        controls.setSpacing(12)

        self._top_meta = QLabel(UIStrings.TOP_BAR_META_DASHBOARD)
        self._top_meta.setObjectName("topBarMeta")
        self._top_meta.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        controls.addWidget(self._top_meta)

        self._primary_button = QPushButton("")
        self._primary_button.setObjectName("actionButton")
        self._primary_button.setProperty("variant", "primary")
        controls.addWidget(self._primary_button)

        self._secondary_button = QPushButton("")
        self._secondary_button.setObjectName("actionButton")
        self._secondary_button.setProperty("variant", "secondary")
        controls.addWidget(self._secondary_button)

        self._tertiary_button = QPushButton("")
        self._tertiary_button.setObjectName("actionButton")
        self._tertiary_button.setProperty("variant", "secondary")
        controls.addWidget(self._tertiary_button)

        top_layout.addLayout(controls)
        layout.addWidget(top_bar)

        # Wrap the page stack in a rounded container so it visually
        # matches the topBar's border-radius and unifies the right pane.
        page_area = QFrame()
        page_area.setObjectName("pageArea")
        page_layout = QVBoxLayout(page_area)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)

        self._stack = QStackedWidget()
        self._pages = _LazyPageMap(self)
        self._page_placeholders = {}
        for page_id in page_registry.list_pages():
            if page_id == "dashboard":
                page = self._ensure_page(page_id)
                if page is not None and self._stack.indexOf(page) < 0:
                    self._stack.addWidget(page)
                continue
            placeholder = QWidget()
            placeholder.setObjectName(f"{page_id}Placeholder")
            self._page_placeholders[page_id] = placeholder
            self._stack.addWidget(placeholder)
        page_layout.addWidget(self._stack)
        layout.addWidget(page_area, 1)
        return content
