from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import QScrollArea

from novel_forge.desktop.pages.standalone.character_bible_editor import CharacterBibleEditor


def test_character_bible_editor_hides_clean_saved_status(qapp, tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "character_bible.json").write_text(
        json.dumps(
            {
                "characters": [
                    {
                        "name": "沈鹿溪",
                        "role": "protagonist",
                        "relationships": {},
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    widget = CharacterBibleEditor(project_dir)
    try:
        assert widget._status.text() == ""
        assert widget._status.isHidden()

        widget._mark_dirty()
        assert not widget._status.isHidden()
        assert widget._status.text() == "有未保存修改。"

        widget._dirty = False
        widget._sync_status()
        assert widget._status.text() == ""
        assert widget._status.isHidden()
    finally:
        widget.shutdown()
        widget.deleteLater()
        qapp.processEvents()


def test_character_bible_profile_scrolls_expose_theme_layers(qapp, tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "character_bible.json").write_text(
        json.dumps(
            {
                "characters": [
                    {
                        "name": "沈岸",
                        "role": "protagonist",
                        "relationships": {},
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    widget = CharacterBibleEditor(project_dir)
    try:
        read_scroll = widget.findChild(QScrollArea, "characterProfileReadScroll")
        form_scroll = widget.findChild(QScrollArea, "characterProfileFormScroll")

        assert read_scroll is not None
        assert form_scroll is not None
        assert read_scroll.widget() is not None
        assert form_scroll.widget() is not None
        assert read_scroll.widget().objectName() == "characterProfileReadContent"
        assert form_scroll.widget().objectName() == "characterProfileFormContent"
    finally:
        widget.shutdown()
        widget.deleteLater()
        qapp.processEvents()
