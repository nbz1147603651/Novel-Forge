"""Reusable widget: QTextBrowser + toolbar + status bar.

Wraps a QTextBrowser (returned from a renderer) with a top toolbar
(复制 / 在文件夹中显示 / 切换原始 JSON) and a bottom status bar
(文件大小 · 最后修改时间 · JSON 顶层字段数)."""
from __future__ import annotations

import json
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from novel_forge.desktop.pages.document_renderer.incremental import update_browser_html


class RichDocumentViewer(QWidget):
    """Wrap a QTextBrowser with toolbar + status bar."""

    raw_mode_toggled = Signal(bool)

    def __init__(
        self,
        body: QTextBrowser,
        *,
        file_path: Path,
        title: str = "",
        raw_text: str | None = None,
        theme_html_factory: Callable[[], str | None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._body = body
        self._file_path = Path(file_path)
        self._raw_text = raw_text or ""
        self._title = title or self._file_path.name
        self._is_raw_mode = False
        self._theme_html_factory = theme_html_factory
        # Capture original HTML at construction time (caller has already set it)
        self._original_html = body.toHtml()
        self._build_ui()

    def body_browser(self) -> QTextBrowser:
        return self._body

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Toolbar ──
        toolbar = QFrame()
        toolbar.setObjectName("richDocToolbar")
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(8, 4, 8, 4)
        tb_layout.setSpacing(6)

        title_label = QLabel(self._title)
        title_label.setObjectName("richDocTitle")
        tb_layout.addWidget(title_label, 1)

        copy_btn = QToolButton()
        copy_btn.setText("复制全文")
        copy_btn.setObjectName("richDocToolBtn")
        copy_btn.setToolTip("复制全文到剪贴板")
        copy_btn.clicked.connect(self._on_copy)
        tb_layout.addWidget(copy_btn)

        reveal_btn = QToolButton()
        reveal_btn.setText("在文件夹中显示")
        reveal_btn.setObjectName("richDocToolBtn")
        reveal_btn.setToolTip("在文件夹中显示此文件")
        reveal_btn.clicked.connect(self._on_reveal)
        tb_layout.addWidget(reveal_btn)

        self._raw_btn = QToolButton()
        self._raw_btn.setText("切换原始 JSON")
        self._raw_btn.setObjectName("richDocToolBtn")
        self._raw_btn.setToolTip("切换显示原始 JSON 文本")
        self._raw_btn.setCheckable(True)
        self._raw_btn.clicked.connect(self._on_toggle_raw)
        tb_layout.addWidget(self._raw_btn)

        layout.addWidget(toolbar)

        # ── Body ──
        self._body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self._body, 1)

        # ── Status bar ──
        status_bar = QFrame()
        status_bar.setObjectName("richDocStatus")
        sb_layout = QHBoxLayout(status_bar)
        sb_layout.setContentsMargins(8, 4, 8, 4)
        sb_layout.setSpacing(12)

        size_text = "—"
        mtime_text = "—"
        top_count_text = ""
        try:
            st = self._file_path.stat()
            size_text = self._format_size(st.st_size)
            mtime_text = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
        except OSError:
            pass
        if self._file_path.suffix == ".json" and self._raw_text:
            try:
                data = json.loads(self._raw_text)
                if isinstance(data, dict):
                    top_count_text = f" · {len(data)} 个顶层字段"
                elif isinstance(data, list):
                    top_count_text = f" · {len(data)} 个数组元素"
            except (json.JSONDecodeError, ValueError):
                pass

        size_label = QLabel(f"文件大小 {size_text}")
        size_label.setObjectName("richDocStatusItem")
        sb_layout.addWidget(size_label)

        mtime_label = QLabel(f"修改时间 {mtime_text}")
        mtime_label.setObjectName("richDocStatusItem")
        sb_layout.addWidget(mtime_label)

        if top_count_text:
            count_label = QLabel(top_count_text.strip(" ·"))
            count_label.setObjectName("richDocStatusItem")
            sb_layout.addWidget(count_label)

        sb_layout.addStretch()
        layout.addWidget(status_bar)

    def _on_copy(self) -> None:
        text = self._raw_text if self._is_raw_mode else self._body.toPlainText()
        QApplication.clipboard().setText(text)

    def _on_reveal(self) -> None:
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._file_path.parent)))
        except Exception:  # noqa: BLE001
            pass

    def _on_toggle_raw(self) -> None:
        self._is_raw_mode = not self._is_raw_mode
        if self._is_raw_mode:
            update_browser_html(self._body, self._raw_html())
        else:
            self._restore_themed_html()
        self.raw_mode_toggled.emit(self._is_raw_mode)

    def refresh_theme_colors(self, default_css: str = "") -> None:
        """Rebuild document HTML from the active theme after a theme switch."""
        from novel_forge.desktop.pages.standalone.renderer_html import browser_stylesheet

        self._body.setStyleSheet(browser_stylesheet())
        if default_css:
            self._body.document().setDefaultStyleSheet(default_css)
        if self._is_raw_mode:
            update_browser_html(self._body, self._raw_html())
            return
        self._restore_themed_html()

    def _restore_themed_html(self) -> None:
        if self._theme_html_factory is not None:
            try:
                refreshed_html = self._theme_html_factory()
            except (OSError, RuntimeError, ValueError):
                refreshed_html = None
            if refreshed_html:
                self._original_html = refreshed_html

        update_browser_html(self._body, self._original_html)

    def _raw_html(self) -> str:
        from novel_forge.desktop.theme import qcolor_hex, qcolor_rgba

        return (
            f"<pre style='background:{qcolor_hex('bg.panel')}; "
            f"color:{qcolor_hex('text.artifact')}; "
            f"border:1px solid {qcolor_rgba('border.default', 0.18)}; "
            "padding:14px; border-radius:6px; font-family:Menlo,Consolas,monospace; "
            "font-size:12px; line-height:1.55;'>{escaped}</pre>"
        ).format(escaped=escape(self._raw_text))

    def set_alternative_raw_text(self, raw: str) -> None:
        self._raw_text = raw

    @staticmethod
    def _format_size(size: int) -> str:
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / (1024 * 1024):.1f} MB"
