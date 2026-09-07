#!/usr/bin/env python3
"""Capture deterministic PySide6 relationship-graph component goldens."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.desktop.ui_parity_fixtures import deterministic_desktop_visual_runtime


def _character_fixture() -> list[dict[str, object]]:
    """Mirror the React mock's three graph characters and hover copy."""

    return [
        {
            "name": "林逐",
            "role": "protagonist",
            "status": "active",
            "age": "28",
            "appearance": "短发，常穿灰色工装外套，左手腕有一道旧疤。眼神专注而警惕。",
            "personality": "外表沉稳寡言，行事恪守梦境采集规范；核心欲望是查清姐姐失踪真相，弱点是在关键线索出现时会以职业流程压住私人情绪。",
            "backstory": "十年前姐姐林澈参与神经同步实验后失踪。官方把事故归为数据错误，林逐此后成为记忆回收师，并长期回避与实验相关的私人关系。",
            "arc": "从以流程自保到主动承担真相的代价，最终在姐姐残片前完成迟到十年的告别。",
            "relationships": {"周砚": "同盟 · 不互信", "林澈": "亲属 · 未完成告别"},
        },
        {
            "name": "周砚",
            "role": "supporting",
            "status": "active",
            "age": "32",
            "appearance": "中等身材，戴无框眼镜，着装正式但袖口常卷起。",
            "personality": "克制、警觉，习惯先验证再表态；对林逐既合作也保留审视。",
            "backstory": "曾参与处理早期记忆库违规案件，因一次过早公开证据造成证人风险，之后对“正确但不完整”的结论格外谨慎。",
            "arc": "从旁观审视到主动为林逐承担一次制度性风险。",
            "relationships": {"林澈": "隐秘 · 调查线"},
        },
        {
            "name": "林澈",
            "role": "supporting",
            "status": "retired",
            "age": "30",
            "appearance": "长发，常扎低马尾；实验服口袋里总插着一支录音笔。",
            "personality": "理性而有保护欲，习惯把风险留给自己。",
            "backstory": "失踪前留下了未经登记的记忆残片与语音线索。",
            "arc": "虽已缺席，但通过残片逐步揭示她主动选择消失保护妹妹。",
            "relationships": {},
        },
    ]


def _tooltip_record(*, output_dir: Path, graph: object, theme_id: str, state: str) -> dict[str, object] | None:
    """Capture the source popup separately from the graph owner surface.

    ``_CharacterTooltipPopup`` is a rounded floating QWidget.  It is not
    composited into ``CharacterGraphWidget.grab()``, so retaining a separate
    component record prevents a passing graph-only capture from masking a
    missing or wrongly-shaped React hover surface.
    """

    tooltip = getattr(graph, "_active_tooltip", None)
    if tooltip is None or not tooltip.isVisible():
        return None
    file_name = f"relationship-network--tooltip--{theme_id}--{state}.png"
    destination = output_dir / file_name
    if not tooltip.grab().save(str(destination), "PNG"):
        raise RuntimeError(f"Unable to save tooltip screenshot: {destination}")
    return {
        "surface": "relationship-network-tooltip",
        "theme": theme_id,
        "state": state,
        "size": {"width": tooltip.width(), "height": tooltip.height()},
        "file": file_name,
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
    }


def _capture(*, output_dir: Path, theme_id: str, state: str) -> dict[str, object]:
    from novel_forge.desktop.pages.document_renderer.character_graph import CharacterGraphWidget
    from novel_forge.desktop.theme.palettes import DESKTOP_THEMES
    from novel_forge.desktop.theme.runtime import apply_desktop_theme

    if theme_id not in DESKTOP_THEMES:
        raise ValueError(f"Unknown desktop theme: {theme_id}")
    if state not in {"populated", "focused", "hover-node", "hover-edge"}:
        raise ValueError(f"Unknown state: {state}")

    with deterministic_desktop_visual_runtime():
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        apply_desktop_theme(theme_id, app=app)
        graph = CharacterGraphWidget(_character_fixture())
        graph.resize(920, 540)
        graph.show()
        QTest.qWait(90)
        if state == "focused":
            graph.focus_character("林逐")
            QTest.qWait(40)
        elif state == "hover-node":
            graph._compute_positions()
            node = graph._name_to_node["林逐"]
            QTest.mouseMove(graph, node.center.toPoint())
            QTest.qWait(80)
        elif state == "hover-edge":
            graph._compute_positions()
            edge = graph._edges[0]
            midpoint = QPoint(
                round((edge.src.center.x() + edge.dst.center.x()) / 2),
                round((edge.src.center.y() + edge.dst.center.y()) / 2),
            )
            # A mathematically exact middle avoids node hit areas and keeps
            # the source tooltip focused on the relationship itself.
            QTest.mouseMove(graph, midpoint)
            QTest.qWait(80)
        file_name = f"relationship-network--graph--{theme_id}--{state}.png"
        destination = output_dir / file_name
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not graph.grab().save(str(destination), "PNG"):
                raise RuntimeError(f"Unable to save screenshot: {destination}")
            record: dict[str, object] = {
                "surface": "relationship-network",
                "theme": theme_id,
                "state": state,
                "size": {"width": graph.width(), "height": graph.height()},
                "file": file_name,
                "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            }
            tooltip = _tooltip_record(output_dir=output_dir, graph=graph, theme_id=theme_id, state=state)
            if tooltip is not None:
                record["tooltip"] = tooltip
            return record
        finally:
            graph.close()
            graph.deleteLater()
            QTest.qWait(10)


def _capture_relationship_edit_dialog(*, output_dir: Path, theme_id: str) -> dict[str, object]:
    """Capture the source graph's post-drag relationship dialog.

    The graph itself emits a pair of character names; CharacterBibleEditor
    opens this exact dialog to collect the canonical relationship type and
    description.  Keeping it in the same golden family makes the editable
    graph a verifiable surface rather than an undocumented React-only flow.
    """

    from novel_forge.desktop.pages.standalone.character_bible_editor import RelationshipEditDialog
    from novel_forge.desktop.theme.palettes import DESKTOP_THEMES
    from novel_forge.desktop.theme.runtime import apply_desktop_theme

    if theme_id not in DESKTOP_THEMES:
        raise ValueError(f"Unknown desktop theme: {theme_id}")
    with deterministic_desktop_visual_runtime():
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        apply_desktop_theme(theme_id, app=app)
        dialog = RelationshipEditDialog(
            source_name="林逐",
            character_names=[str(item["name"]) for item in _character_fixture()],
            target_name="周砚",
            relation_type="alliance",
            description="彼此试探但在关键行动中互相掩护。",
        )
        dialog.show()
        QTest.qWait(90)
        file_name = f"relationship-network--relationship-edit-dialog--{theme_id}--open.png"
        destination = output_dir / file_name
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not dialog.grab().save(str(destination), "PNG"):
                raise RuntimeError(f"Unable to save screenshot: {destination}")
            return {
                "surface": "relationship-network-edit-dialog",
                "theme": theme_id,
                "state": "open",
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
    parser.add_argument("--output-dir", type=Path, default=Path("docs/ui-parity/goldens/pyside-relationship-network"))
    parser.add_argument("--theme", action="append", dest="themes", help="Theme id; repeat for a matrix.")
    parser.add_argument(
        "--state",
        action="append",
        dest="states",
        help="State; populated, focused, hover-node, hover-edge or relationship-edit-dialog. Repeat for a matrix.",
    )
    args = parser.parse_args()
    themes = tuple(args.themes or ("narrative_ember",))
    states = tuple(args.states or ("populated", "focused"))
    records = [
        _capture_relationship_edit_dialog(output_dir=args.output_dir, theme_id=theme_id)
        if state == "relationship-edit-dialog"
        else _capture(output_dir=args.output_dir, theme_id=theme_id, state=state)
        for theme_id in themes
        for state in states
    ]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "source": "PySide6 CharacterGraphWidget",
        "fixture": "three source-shaped characters with alliance, family and secret relationship edges",
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
