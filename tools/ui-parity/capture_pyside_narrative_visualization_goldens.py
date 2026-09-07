#!/usr/bin/env python3
"""Capture a deterministic PySide6 narrative-timeline fixture for UI parity."""

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


def _narrative_blueprint_fixture() -> dict[str, object]:
    """Return the source-shaped counterpart to the React timeline fixture."""

    return {
        "synopsis": "青瓦梦起的叙事蓝图固定夹具。",
        "narrative_phases": [
            {
                "phase_name": "入局阶段",
                "chapter_start": 1,
                "chapter_end": 6,
                "tension_level": "低",
                "description": "无主残片出现，林逐重新进入姐姐失踪的旧案。",
            },
            {
                "phase_name": "裂隙阶段",
                "chapter_start": 7,
                "chapter_end": 12,
                "tension_level": "渐升",
                "description": "授权链漏洞转为角色之间的责任选择。",
            },
            {
                "phase_name": "崩塌阶段",
                "chapter_start": 13,
                "chapter_end": 18,
                "tension_level": "高",
                "description": "隐藏记录被证实，信任与公开代价同时升级。",
            },
            {
                "phase_name": "归位阶段",
                "chapter_start": 19,
                "chapter_end": 24,
                "tension_level": "回落",
                "description": "角色选择公开或封存记忆并承担后果。",
            },
        ],
        "key_turning_points": [
            {"chapter_number": 4, "title": "夜间时间戳", "description": "林逐暂不告知周砚。"},
            {"chapter_number": 8, "title": "漏洞证实", "description": "授权链被人为截断。"},
            {"chapter_number": 12, "title": "证据分歧", "description": "两人对公开时机出现分歧。"},
            {"chapter_number": 17, "title": "旧案回响", "description": "隐藏记录指向姐姐的选择。"},
            {"chapter_number": 20, "title": "公开代价", "description": "真相会带来新的风险。"},
            {"chapter_number": 24, "title": "承担", "description": "主角作出终局决定。"},
        ],
        "subplot_plan": [
            {
                "name": "授权链漏洞",
                "description": "制度漏洞逐步转化为责任选择。",
                "involved_chapters": [4, 8, 17, 24],
                "chapter_events": [
                    {"chapter_number": 4, "event": "时间戳"},
                    {"chapter_number": 8, "event": "漏洞证实"},
                    {"chapter_number": 17, "event": "公开代价"},
                    {"chapter_number": 24, "event": "选择"},
                ],
                "weave_links": [
                    {"target_subplot": "主线", "trigger_chapter": 8, "link_type": "feed_main"},
                    {"target_subplot": "主线", "trigger_chapter": 17, "link_type": "reveal_key"},
                ],
            },
            {
                "name": "姐姐的残片",
                "description": "遗留记忆只能通过现实证据逐步复原。",
                "involved_chapters": [2, 6, 15, 22],
                "chapter_events": [
                    {"chapter_number": 2, "event": "残片"},
                    {"chapter_number": 6, "event": "语音"},
                    {"chapter_number": 15, "event": "动机"},
                    {"chapter_number": 22, "event": "告别"},
                ],
                "weave_links": [],
            },
            {
                "name": "林逐与周砚",
                "description": "合作关系在隐瞒与共同承担之间转变。",
                "involved_chapters": [4, 10, 18, 24],
                "chapter_events": [
                    {"chapter_number": 4, "event": "隐瞒"},
                    {"chapter_number": 10, "event": "分歧"},
                    {"chapter_number": 18, "event": "并肩"},
                    {"chapter_number": 24, "event": "承担"},
                ],
                "weave_links": [
                    {"target_subplot": "主线", "trigger_chapter": 18, "link_type": "feed_main"},
                ],
            },
        ],
    }


def capture_timeline(*, output_dir: Path, theme_id: str) -> dict[str, object]:
    """Render the native timeline at the source widget's fixed fixture."""

    from novel_forge.desktop.pages.document_renderer.narrative_blueprint import NarrativeBlueprintWidget
    from novel_forge.desktop.theme.palettes import DESKTOP_THEMES
    from novel_forge.desktop.theme.runtime import apply_desktop_theme

    if theme_id not in DESKTOP_THEMES:
        raise ValueError(f"Unknown desktop theme: {theme_id}")

    with deterministic_desktop_visual_runtime():
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        apply_desktop_theme(theme_id, app=app)
        timeline = NarrativeBlueprintWidget(_narrative_blueprint_fixture())
        timeline.resize(1280, 720)
        timeline.show()
        QTest.qWait(120)
        file_name = f"narrative-blueprint--timeline--{theme_id}--populated.png"
        destination = output_dir / file_name
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not timeline.grab().save(str(destination), "PNG"):
                raise RuntimeError(f"Unable to save screenshot: {destination}")
            return {
                "surface": "narrative-blueprint-overview",
                "theme": theme_id,
                "state": "populated",
                "size": {"width": timeline.width(), "height": timeline.height()},
                "file": file_name,
                "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            }
        finally:
            timeline.close()
            timeline.deleteLater()
            QTest.qWait(10)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("docs/ui-parity/goldens/pyside-narrative-visualization"))
    parser.add_argument("--theme", action="append", dest="themes", help="Theme id; repeat for a matrix.")
    args = parser.parse_args()
    themes = tuple(args.themes or ("narrative_ember",))
    records = [capture_timeline(output_dir=args.output_dir, theme_id=theme_id) for theme_id in themes]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "source": "PySide6 NarrativeBlueprintWidget",
        "fixture": "24 chapter narrative blueprint with phases, milestones and three subplot lanes",
        "themes": themes,
        "records": records,
    }
    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
