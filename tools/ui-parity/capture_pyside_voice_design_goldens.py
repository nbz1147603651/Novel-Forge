#!/usr/bin/env python3
"""Capture the native voice-design brief editor without opening a project."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.desktop.ui_parity_fixtures import deterministic_desktop_visual_runtime


def _capture(*, output_dir: Path, theme_id: str) -> dict[str, object]:
    from novel_forge.desktop.components.dialogs import show_multiline_input_dialog
    from novel_forge.desktop.theme.palettes import DESKTOP_THEMES
    from novel_forge.desktop.theme.runtime import apply_desktop_theme

    if theme_id not in DESKTOP_THEMES:
        raise ValueError(f"Unknown desktop theme: {theme_id}")
    record: dict[str, object] = {}

    def grab_and_close() -> None:
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        dialog = next(
            (
                widget
                for widget in app.topLevelWidgets()
                if isinstance(widget, QDialog)
                and widget.isVisible()
                and widget.windowTitle() == "AI 音色设计"
            ),
            None,
        )
        if dialog is None:
            raise RuntimeError("Native voice-design dialog did not open")
        file_name = f"voice-design--{theme_id}--configuration.png"
        destination = output_dir / file_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not dialog.grab().save(str(destination), "PNG"):
            raise RuntimeError(f"Unable to save screenshot: {destination}")
        record.update(
            {
                "surface": "voice-design",
                "theme": theme_id,
                "state": "configuration",
                "size": {"width": dialog.width(), "height": dialog.height()},
                "file": file_name,
                "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            }
        )
        dialog.reject()

    with deterministic_desktop_visual_runtime():
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        apply_desktop_theme(theme_id, app=app)
        QTimer.singleShot(120, grab_and_close)
        show_multiline_input_dialog(
            None,
            "AI 音色设计",
            "简报已根据上游角色特质生成。可调整声线、节奏与表达要求，建议保留角色核心辨识度。",
            heading="确认角色音色简报",
            initial_text=(
                "林逐 · 主视角\n"
                "习惯先判断，再回答；声线克制、清晰，保留雨夜调查中的紧张感。\n"
                "关注声线年龄感、音域、语速、情绪底色与表达习惯。"
            ),
            placeholder_text="描述角色的年龄感、音域、语速、情绪底色与表达习惯…",
            helper_text="提交后将生成候选音色并自动进入试听；Ctrl/⌘ + Enter 可快速提交。",
            context_text="角色 · 林逐    平台 · MiniMax    来源 · 上游角色档案",
            confirm_text="生成并试听",
            reset_text="恢复上游简报",
        )
    if not record:
        raise RuntimeError("Native voice-design capture did not produce a record")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("docs/ui-parity/goldens/pyside-voice-design"))
    parser.add_argument("--theme", action="append", dest="themes", help="Theme id; repeat for a matrix.")
    args = parser.parse_args()
    themes = tuple(args.themes or ("narrative_ember",))
    records = [_capture(output_dir=args.output_dir, theme_id=theme_id) for theme_id in themes]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "source": "PySide6 show_multiline_input_dialog for VoiceStudioPage._on_design_voice",
                "fixture": "sanitized Lin Zhu voice-design brief without project I/O",
                "themes": themes,
                "records": records,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
