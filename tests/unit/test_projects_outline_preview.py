"""Tests for in-progress outline previews in the projects reader."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QTabWidget, QTextBrowser  # noqa: E402

from novel_forge.desktop.pages.standalone.projects_page import ProjectsPage  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_load_outline_generation_preview_from_accepted_batches(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    _write_json(
        project_dir / "states" / "outline_session.json",
        {
            "total_chapters": 5,
            "chapters_done": 3,
            "latest_chapter_number": 3,
            "accepted_batches": [
                {"checkpoint_file": "batch_002_003.json"},
                {"checkpoint_file": "../ignored.json"},
                {"checkpoint_file": "batch_001_001.json"},
            ],
        },
    )
    _write_json(
        project_dir / "states" / "outline_batches" / "batch_002_003.json",
        {
            "chapters": [
                {"chapter_number": 3, "title": "第三章", "goal": "收束第一段冲突"},
                {"chapter_number": 2, "title": "第二章", "goal": "扩大误会"},
            ]
        },
    )
    _write_json(
        project_dir / "states" / "outline_batches" / "batch_001_001.json",
        {"chapters": [{"chapter_number": 1, "title": "第一章", "goal": "开局"}]},
    )

    preview = ProjectsPage._load_outline_generation_preview(project_dir)

    assert preview is not None
    assert preview["total_chapters"] == 5
    assert "生成中预览" in str(preview["synopsis"])
    assert "已验收 3/5 章" in str(preview["synopsis"])
    assert [chapter["chapter_number"] for chapter in preview["chapters"]] == [1, 2, 3]


def test_outline_tab_uses_read_only_generation_preview(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    project_dir = tmp_path / "demo"
    _write_json(
        project_dir / "states" / "outline_session.json",
        {
            "total_chapters": 2,
            "chapters_done": 1,
            "latest_chapter_number": 1,
            "accepted_batches": [{"checkpoint_file": "batch_001_001.json"}],
        },
    )
    _write_json(
        project_dir / "states" / "outline_batches" / "batch_001_001.json",
        {
            "chapters": [
                {
                    "chapter_number": 1,
                    "title": "红轿铁声",
                    "goal": "婚盟开局",
                    "beats_summary": ["沈清漪入魏。"],
                }
            ]
        },
    )

    page = ProjectsPage()
    tabs = QTabWidget()
    try:
        qapp.processEvents()

        added = page._add_project_document_tab(tabs, project_dir, "章节大纲", "outline.json")

        assert added is True
        assert tabs.tabText(0) == "章节大纲（生成中）"
        widget = tabs.widget(0)
        assert isinstance(widget, QTextBrowser)
        text = widget.toPlainText()
        assert "生成中预览" in text
        assert "红轿铁声" in text
    finally:
        page.shutdown()
        page.close()
        tabs.close()
        page.deleteLater()
        tabs.deleteLater()
