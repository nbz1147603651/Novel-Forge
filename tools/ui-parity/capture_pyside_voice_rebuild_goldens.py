#!/usr/bin/env python3
"""Capture deterministic source-sized goldens for voice-team rebuild."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.desktop.ui_parity_fixtures import deterministic_desktop_visual_runtime


def _capture(*, output_dir: Path, theme_id: str) -> dict[str, object]:
    from novel_forge.desktop.pages.voice_studio.page import RebuildConfirmDialog
    from novel_forge.desktop.theme.palettes import DESKTOP_THEMES
    from novel_forge.desktop.theme.runtime import apply_desktop_theme
    from novel_forge.tts.schemas import TTSProvider, VoiceCastEntry

    if theme_id not in DESKTOP_THEMES:
        raise ValueError(f"Unknown desktop theme: {theme_id}")
    characters = [
        {"character_id": "lin-zhu", "name": "林逐", "role": "protagonist"},
        {"character_id": "zhou-yan", "name": "周砚", "role": "supporting"},
        {"character_id": "system", "name": "系统播报", "role": "minor"},
    ]
    entries = [
        VoiceCastEntry(character_id="lin-zhu", character_name="林逐", provider=TTSProvider.MINIMAX, voice_source="designed"),
        VoiceCastEntry(character_id="zhou-yan", character_name="周砚", provider=TTSProvider.MINIMAX, voice_source="system"),
        VoiceCastEntry(character_id="system", character_name="系统播报", provider=TTSProvider.MINIMAX, voice_source="system"),
    ]
    with deterministic_desktop_visual_runtime():
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        apply_desktop_theme(theme_id, app=app)
        dialog = RebuildConfirmDialog(characters=characters, voice_team_entries=entries)
        dialog.show()
        QTest.qWait(120)
        file_name = f"voice-rebuild--{theme_id}--default.png"
        destination = output_dir / file_name
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not dialog.grab().save(str(destination), "PNG"):
                raise RuntimeError(f"Unable to save screenshot: {destination}")
            return {
                "surface": "voice-rebuild-confirm",
                "theme": theme_id,
                "state": "default",
                "size": {"width": dialog.width(), "height": dialog.height()},
                "file": file_name,
                "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            }
        finally:
            dialog.close()
            dialog.deleteLater()
            QTest.qWait(10)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("docs/ui-parity/goldens/pyside-voice-rebuild"))
    parser.add_argument("--theme", action="append", dest="themes", help="Theme id; repeat for a matrix.")
    args = parser.parse_args()
    themes = tuple(args.themes or ("narrative_ember",))
    records = [_capture(output_dir=args.output_dir, theme_id=theme_id) for theme_id in themes]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "source": "PySide6 RebuildConfirmDialog",
        "fixture": "three character voice team with designed and system voice sources",
        "themes": themes,
        "records": records,
    }
    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
