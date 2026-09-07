#!/usr/bin/env python3
"""Capture deterministic source-sized goldens for the voice script editor."""

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


def _capture(*, output_dir: Path, performance_only: bool, theme_id: str) -> dict[str, object]:
    from novel_forge.desktop.pages.voice_studio.page import ScriptSegmentEditorDialog
    from novel_forge.desktop.theme.palettes import DESKTOP_THEMES
    from novel_forge.desktop.theme.runtime import apply_desktop_theme
    from novel_forge.tts.schemas import DubbingScript, DubbingSegment, EmotionTag, SegmentType

    if theme_id not in DESKTOP_THEMES:
        raise ValueError(f"Unknown desktop theme: {theme_id}")
    script = DubbingScript(
        chapter_number=4,
        segments=[
            DubbingSegment(segment_index=0, segment_type=SegmentType.NARRATION, text="雨声压在高架桥底，像一盘没倒回去的磁带。", emotion=EmotionTag.WHISPER),
            DubbingSegment(segment_index=1, segment_type=SegmentType.DIALOGUE, character_id="lin-zhu", character_name="林逐", text="这串时间戳，不该出现在这里。", emotion=EmotionTag.NEUTRAL, tone_hint="克制"),
            DubbingSegment(segment_index=2, segment_type=SegmentType.DIALOGUE, character_id="zhou-yan", character_name="周砚", text="你确定没有把它带出授权链？", emotion=EmotionTag.ANXIOUS, tone_hint="压低声线"),
        ],
    )
    with deterministic_desktop_visual_runtime():
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        apply_desktop_theme(theme_id, app=app)
        state = "performance-guidance" if performance_only else "default"
        dialog = ScriptSegmentEditorDialog(
            script,
            initial_segment_index=1 if performance_only else None,
            provider="mock",
            performance_only=performance_only,
        )
        dialog.show()
        QTest.qWait(120)
        file_name = f"voice-script-editor--{theme_id}--{state}.png"
        destination = output_dir / file_name
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not dialog.grab().save(str(destination), "PNG"):
                raise RuntimeError(f"Unable to save screenshot: {destination}")
            return {
                "surface": "voice-script-editor",
                "theme": theme_id,
                "state": state,
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
    parser.add_argument("--output-dir", type=Path, default=Path("docs/ui-parity/goldens/pyside-voice-script-editor"))
    parser.add_argument("--theme", action="append", dest="themes", help="Theme id; repeat for a matrix.")
    args = parser.parse_args()
    themes = tuple(args.themes or ("narrative_ember",))
    records = [
        _capture(output_dir=args.output_dir, performance_only=performance_only, theme_id=theme_id)
        for theme_id in themes
        for performance_only in (False, True)
    ]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(json.dumps({"source": "PySide6 ScriptSegmentEditorDialog", "fixture": "three-segment chapter-four script without project I/O", "themes": themes, "records": records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
