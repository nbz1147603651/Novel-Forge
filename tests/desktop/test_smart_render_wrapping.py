"""Tests for ProjectsPage._add_project_document_tab wrapping smart_render_document.

Task 4 of the juanzhi-rendering plan: when ``smart_render_document`` returns a
``QTextBrowser`` (e.g. for ``spec.json`` → ``render_spec``), the tab must be
wrapped in a ``RichDocumentViewer`` so the toolbar and status bar are exposed.
For more complex widgets (with sub-components like ``CharacterGraphWidget``)
we keep the raw widget to avoid double wrapping.
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import QApplication, QTabWidget

from novel_forge.desktop.components.rich_document_viewer import RichDocumentViewer
from novel_forge.desktop.pages.standalone.projects_page import ProjectsPage


def _make_app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _write_spec(tmp_path: Path) -> Path:
    """Write a spec.json that smart_render_document will route to render_spec."""
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            {
                "title": "测试",
                "genre": "生活治愈",
                "length_target": 100000,
                "language": "zh",
                "theme": "测试主题足够长以确保长字符串分支被触发而不是短字符串分支。",
                "characters_hint": "测试角色",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return spec


def test_add_project_document_tab_wraps_smart_render(tmp_path: Path) -> None:
    """当 smart_render_document 返回 QTextBrowser 时，_add_project_document_tab
    也应该把它包成 RichDocumentViewer。"""
    _make_app()
    _write_spec(tmp_path)
    page = ProjectsPage()
    tabs = QTabWidget()
    page._add_project_document_tab(tabs, tmp_path, "故事规格", "spec.json")
    # 第一个 tab 应是 RichDocumentViewer
    if tabs.count() > 0:
        widget = tabs.widget(0)
        # widget 可能是 RichDocumentViewer 或 QTextBrowser（取决于 smart_render_document 行为）
        # 我们的目标：变成 RichDocumentViewer
        assert isinstance(widget, RichDocumentViewer), (
            f"expected RichDocumentViewer, got {type(widget).__name__}"
        )
