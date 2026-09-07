"""Theme integration tests for QTextBrowser-based artifact renderers."""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtWidgets import QApplication

from novel_forge.desktop.components.rich_document_viewer import RichDocumentViewer
from novel_forge.desktop.pages.standalone.renderer_html import (
    browser_stylesheet,
    html_wrap,
    make_browser,
)
from novel_forge.desktop.theme.documents import render_document_css
from novel_forge.desktop.theme.palettes import DEFAULT_DESKTOP_THEME_ID
from novel_forge.desktop.tokens.colors import COLORS

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_THEME_MANAGED_SOURCES = (
    _PROJECT_ROOT / "novel_forge/desktop/pages/standalone/renderer_html.py",
    _PROJECT_ROOT / "novel_forge/desktop/components/rich_document_viewer.py",
    _PROJECT_ROOT / "novel_forge/desktop/pages/voice_studio/page.py",
    _PROJECT_ROOT / "novel_forge/desktop/pages/workflow/artifacts.py",
)
_RAW_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_RAW_RGB_COLOR = re.compile(r"rgba\(\s*\d+")


def test_document_html_uses_active_theme_tokens(qapp: QApplication) -> None:
    """Document templates resolve semantic tokens against the active palette."""
    old_theme = qapp.property("_novel_forge_desktop_theme")
    try:
        qapp.setProperty("_novel_forge_desktop_theme", "stillwater")
        themed = html_wrap('<div class="section">内容</div>', "主题化文档")
        custom_css = render_document_css(
            "color:{{text.muted}}; border-left:3px solid {{accent.primary}}; "
            "background:rgba({{bg.surface}},0.70);"
        )

        assert "#1c2a38" in themed  # text.primary
        assert "rgba(248, 251, 253, 0.7)" in themed  # bg.surface
        assert "#2c241e" not in themed
        assert custom_css == (
            "color:#6a7e90; border-left:3px solid #3a6b9f; "
            "background:rgba(248, 251, 253, 0.70);"
        )
        assert "rgba(248, 251, 253, 0.92)" in browser_stylesheet()
    finally:
        qapp.setProperty("_novel_forge_desktop_theme", old_theme)


def test_rich_document_viewer_rebuilds_html_after_theme_switch(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    """A reader opened before a theme change must not retain its old palette."""
    old_theme = qapp.property("_novel_forge_desktop_theme")
    viewer: RichDocumentViewer | None = None
    try:
        qapp.setProperty("_novel_forge_desktop_theme", DEFAULT_DESKTOP_THEME_ID)

        def _build_html() -> str:
            return html_wrap('<div class="section">主题正文</div>', "主题文档")

        viewer = RichDocumentViewer(
            make_browser(_build_html()),
            file_path=tmp_path / "artifact.json",
            raw_text="{}",
            theme_html_factory=_build_html,
        )
        qapp.setProperty("_novel_forge_desktop_theme", "stillwater")
        viewer.refresh_theme_colors()

        assert "#1c2a38" in viewer._original_html
        assert "#2c241e" not in viewer._original_html
        assert "rgba(248, 251, 253, 0.92)" in viewer.body_browser().styleSheet()
    finally:
        qapp.setProperty("_novel_forge_desktop_theme", old_theme)
        if viewer is not None:
            viewer.deleteLater()


def test_document_renderer_markup_tokens_follow_active_theme(qapp: QApplication) -> None:
    """Inline renderer markup resolves only the dedicated document token form."""
    old_theme = qapp.property("_novel_forge_desktop_theme")
    try:
        qapp.setProperty("_novel_forge_desktop_theme", "stillwater")
        themed = html_wrap(
            '<span style="color:[[nf:accent.primary]]; '
            'background:rgba([[nf:bg.surface]], 0.7)">内容</span>'
        )

        assert "#3a6b9f" in themed  # accent.primary in stillwater
        assert "rgba(248, 251, 253, 0.7)" in themed  # bg.surface in stillwater
        assert "[[nf:" not in themed
    finally:
        qapp.setProperty("_novel_forge_desktop_theme", old_theme)


def test_theme_managed_views_do_not_reintroduce_literal_css_colors() -> None:
    """Prevent new page-local palette values from bypassing the token system."""
    for path in _THEME_MANAGED_SOURCES:
        source = path.read_text(encoding="utf-8")
        assert _RAW_HEX_COLOR.search(source) is None, path
        assert _RAW_RGB_COLOR.search(source) is None, path


def test_document_renderer_sources_do_not_bypass_document_theme_tokens() -> None:
    """Keep artifact renderers theme-aware, including their inline HTML."""
    renderer_root = _PROJECT_ROOT / "novel_forge/desktop/pages/document_renderer"
    for path in renderer_root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert _RAW_HEX_COLOR.search(source) is None, path
        assert _RAW_RGB_COLOR.search(source) is None, path


def test_document_renderer_markup_references_known_color_tokens() -> None:
    """A typo in an inline marker should fail tests rather than render magenta."""
    renderer_root = _PROJECT_ROOT / "novel_forge/desktop/pages/document_renderer"
    token_pattern = re.compile(r"\[\[nf:([a-z0-9.]+)\]\]")
    for path in renderer_root.rglob("*.py"):
        assert set(token_pattern.findall(path.read_text(encoding="utf-8"))) <= set(COLORS), path
