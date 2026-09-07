#!/usr/bin/env python3
"""Capture deterministic PySide6 character-profile component goldens."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.desktop.ui_parity_fixtures import (  # noqa: E402
    deterministic_desktop_visual_runtime,
    visual_character_bible_payload,
)


def _capture(*, output_dir: Path, theme_id: str, state: str) -> dict[str, object]:
    from novel_forge.desktop.pages.standalone.character_bible_store import CharacterBibleStore
    from novel_forge.desktop.pages.standalone.character_profile_page import CharacterProfilePage
    from novel_forge.desktop.theme.palettes import DESKTOP_THEMES
    from novel_forge.desktop.theme.runtime import apply_desktop_theme

    if theme_id not in DESKTOP_THEMES:
        raise ValueError(f"Unknown desktop theme: {theme_id}")
    if state not in {"selected", "editing"}:
        raise ValueError(f"Unknown state: {state}")

    with deterministic_desktop_visual_runtime(), TemporaryDirectory(prefix="nimo-character-profile-") as temporary_dir:
        project_dir = Path(temporary_dir)
        (project_dir / "character_bible.json").write_text(
            json.dumps(visual_character_bible_payload(), ensure_ascii=False),
            encoding="utf-8",
        )
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        apply_desktop_theme(theme_id, app=app)
        store = CharacterBibleStore(project_dir)
        page = CharacterProfilePage(store)
        page.resize(1280, 720)
        page.show()
        QTest.qWait(110)
        if state == "editing":
            page._on_edit_toggle_clicked()  # type: ignore[attr-defined]
            QTest.qWait(40)
        file_name = f"character-profile--{theme_id}--{state}.png"
        destination = output_dir / file_name
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not page.grab().save(str(destination), "PNG"):
                raise RuntimeError(f"Unable to save screenshot: {destination}")
            return {
                "surface": "character-profile",
                "theme": theme_id,
                "state": state,
                "size": {"width": page.width(), "height": page.height()},
                "file": file_name,
                "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            }
        finally:
            page.shutdown()
            page.close()
            page.deleteLater()
            store.deleteLater()
            QTest.qWait(10)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("docs/ui-parity/goldens/pyside-character-profile"))
    parser.add_argument("--theme", action="append", dest="themes", help="Theme id; repeat for a matrix.")
    parser.add_argument("--state", action="append", dest="states", help="State; selected or editing. Repeat for a matrix.")
    args = parser.parse_args()
    themes = tuple(args.themes or ("narrative_ember",))
    states = tuple(args.states or ("selected", "editing"))
    records = [_capture(output_dir=args.output_dir, theme_id=theme_id, state=state) for theme_id in themes for state in states]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "source": "PySide6 CharacterProfilePage",
        "fixture": "three characters with the selected protagonist and a local edit state",
        "themes": themes,
        "states": states,
        "records": records,
    }
    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
