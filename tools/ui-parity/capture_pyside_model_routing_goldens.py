#!/usr/bin/env python3
"""Capture source-sized PySide6 model-profile editor goldens.

The fixture is intentionally limited to sanitized display configuration. It
never opens a user profile file, reads a real API key, or starts a connection
test worker.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.desktop.ui_parity_fixtures import deterministic_desktop_visual_runtime

DEFAULT_STATES = ("add", "edit")


def _dialog_for(state: str) -> QWidget:
    from novel_forge.desktop.pages.settings.components import _ModelDialog
    from novel_forge.gateway.profiles import ModelProfile

    if state == "add":
        return _ModelDialog(existing_ids=["openai:gpt-4o"])
    if state == "edit":
        return _ModelDialog(
            profile=ModelProfile(
                profile_id="openai:gpt-4o",
                display_name="OpenAI · GPT-4o",
                provider="openai",
                model_id="gpt-4o",
                api_key="fixture-key-never-used",
            )
        )
    raise ValueError(f"Unknown model routing state: {state}")


def _capture(*, output_dir: Path, theme_id: str, states: tuple[str, ...]) -> list[dict[str, object]]:
    from novel_forge.desktop.theme.palettes import DESKTOP_THEMES
    from novel_forge.desktop.theme.runtime import apply_desktop_theme

    if theme_id not in DESKTOP_THEMES:
        raise ValueError(f"Unknown desktop theme: {theme_id}")
    records: list[dict[str, object]] = []
    with deterministic_desktop_visual_runtime():
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        apply_desktop_theme(theme_id, app=app)
        for state in states:
            dialog = _dialog_for(state)
            dialog.show()
            QTest.qWait(120)
            file_name = f"model-profile--{state}--{theme_id}--default.png"
            destination = output_dir / file_name
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                if not dialog.grab().save(str(destination), "PNG"):
                    raise RuntimeError(f"Unable to save screenshot: {destination}")
                records.append(
                    {
                        "surface": "model-profile-editor",
                        "state": state,
                        "theme": theme_id,
                        "size": {"width": dialog.width(), "height": dialog.height()},
                        "file": file_name,
                        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                    }
                )
            finally:
                dialog.close()
                dialog.deleteLater()
                QTest.qWait(10)
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("docs/ui-parity/goldens/pyside-model-routing"))
    parser.add_argument("--theme", action="append", dest="themes", help="Theme id; repeat for a matrix.")
    parser.add_argument("--states", default=",".join(DEFAULT_STATES), help="Comma-separated state names.")
    args = parser.parse_args()
    states = tuple(state.strip() for state in args.states.split(",") if state.strip())
    unknown = sorted(set(states) - set(DEFAULT_STATES))
    if unknown:
        parser.error(f"Unsupported states: {', '.join(unknown)}")
    themes = tuple(args.themes or ("narrative_ember",))
    records: list[dict[str, object]] = []
    for theme_id in themes:
        records.extend(_capture(output_dir=args.output_dir, theme_id=theme_id, states=states))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "source": "PySide6 _ModelDialog",
                "fixture": "sanitized OpenAI profile; no user config or network access",
                "themes": themes,
                "records": records,
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
