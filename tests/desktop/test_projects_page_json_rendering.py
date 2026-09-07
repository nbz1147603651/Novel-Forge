"""Tests for ProjectsPage._make_content_view JSON→RichDocumentViewer wiring.

Task 2 of the juanzhi-rendering plan: when a JSON artifact is rendered via
ProjectsPage._make_content_view, the result must be wrapped in a
RichDocumentViewer (toolbar + status bar). Prose mode keeps the plain
QTextEdit behaviour (no toolbar).
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import QApplication

from novel_forge.desktop.components.rich_document_viewer import RichDocumentViewer
from novel_forge.desktop.pages.standalone.projects_page import ProjectsPage


def _make_app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _make_spec(tmp_path: Path) -> Path:
    p = tmp_path / "spec.json"
    p.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "title": "测试项目",
                "genre": "生活治愈",
                "length_target": 100000,
                "language": "zh",
                # Long enough (> 40 chars) to trigger the hint-block branch
                "theme": "一段用于测试分组渲染的故事主题描述，应该足够长以触发长字符串 hint-block 分支",
                "characters_hint": "沈鹿溪，女，32岁",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return p


def test_make_content_view_wraps_json_in_rich_viewer(tmp_path: Path) -> None:
    _make_app()
    spec_path = _make_spec(tmp_path)
    widget = ProjectsPage._make_content_view(spec_path)
    assert isinstance(widget, RichDocumentViewer), (
        f"expected RichDocumentViewer, got {type(widget).__name__}"
    )


def test_make_content_view_keeps_prose_as_plain_qtextedit(tmp_path: Path) -> None:
    _make_app()
    md_path = tmp_path / "ch1.md"
    md_path.write_text("# Chapter 1\n\nHello world.", encoding="utf-8")
    widget = ProjectsPage._make_content_view(md_path, prose=True)
    # prose 模式仍是 QTextEdit（无工具条）
    assert not isinstance(widget, RichDocumentViewer)


def test_render_generic_json_card_groups_fields(tmp_path: Path) -> None:
    _make_app()
    spec_path = _make_spec(tmp_path)
    html = ProjectsPage._render_generic_json_card(spec_path)
    # 元信息（schema_version）应在 meta 区
    assert "元信息" in html
    # 短字符串（language）应直接显示
    assert "language" in html or "zh" in html
    # 长字符串（theme）应在 hint-block 中
    assert "hint-block" in html
    # 原始 JSON 折叠区
    assert "原始 JSON" in html


def test_render_generic_json_card_handles_nested(tmp_path: Path) -> None:
    _make_app()
    p = tmp_path / "config.json"
    p.write_text(
        json.dumps(
            {
                "id": "abc",
                "outer": {"inner1": 1, "inner2": [1, 2, 3]},
                "tags": ["a", "b", "c"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    html = ProjectsPage._render_generic_json_card(p)
    assert "<details" in html  # nested objects fold
    assert "tags" in html  # lists


def test_render_generic_json_card_invalid_json(tmp_path: Path) -> None:
    _make_app()
    p = tmp_path / "broken.json"
    p.write_text("not valid json {", encoding="utf-8")
    html = ProjectsPage._render_generic_json_card(p)
    assert "<pre" in html


def test_render_generic_json_card_top_level_array(tmp_path: Path) -> None:
    _make_app()
    p = tmp_path / "list.json"
    p.write_text(json.dumps([1, 2, 3], ensure_ascii=False), encoding="utf-8")
    html = ProjectsPage._render_generic_json_card(p)
    # Top-level list should still render something useful
    assert "<pre" in html or "JSON" in html