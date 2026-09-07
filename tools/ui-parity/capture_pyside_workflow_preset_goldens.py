#!/usr/bin/env python3
"""Capture deterministic PySide6 goldens for workflow-preset workbenches.

The React counterpart intentionally owns only an in-memory draft, but its
layout and interaction states must still be compared to the existing Qt
source.  This capture script freezes source dialogs with test-only input;
it never opens a user preset, provider profile, or project file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QMessageBox, QTextEdit, QWidget

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.desktop.ui_parity_fixtures import deterministic_desktop_visual_runtime

DEFAULT_STATES = ("field-editor", "ai-hint", "ai-polish", "ai-progress", "ai-failure", "diff", "history")


def _capture_widget(widget: QWidget, destination: Path) -> None:
    pixmap = QPixmap(widget.size())
    pixmap.fill(widget.palette().color(widget.backgroundRole()))
    widget.render(pixmap)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not pixmap.save(str(destination), "PNG"):
        raise RuntimeError(f"Unable to save screenshot: {destination}")


def _history_fixture(*_args: object, **_kwargs: object) -> list[dict[str, Any]]:
    return [
        {
            "_file": "fixture-polish.json",
            "timestamp": "20260714T121800Z",
            "operation": "polish",
            "hint": "加强授权链的制度压迫，并保留克制、近距离的叙述边界。",
            "selected_suggestions": ["旧磁带承担证据锚点", "压缩解释性旁白"],
            "metadata": {
                "accepted_fields": ["core_premise", "world_rule", "theme"],
                "rejected_fields": ["tone"],
                "creative_note": {
                    "core_pitch": "无主记忆里的求救声把林逐拉回姐姐失踪案。",
                    "next_moves": ["让授权链漏洞在第八章转为可见行动。"],
                },
            },
            "data": {"core_premise": "固定夹具"},
        }
    ]


def _field_editor() -> QWidget:
    from novel_forge.desktop.pages.workflow.forms import _EditableFieldSpec, _WorkflowFieldEditDialog

    premise = QTextEdit()
    premise.setPlainText("记忆回收师在一段无主记忆里听见失踪姐姐留下的求救声，必须在授权链关闭前查明真相。")
    genre = QComboBox()
    genre.addItem("近未来悬疑", "near_future_mystery")
    genre.addItem("都市悬疑", "urban_suspense")
    title = QLineEdit("青瓦梦起")
    language = QComboBox()
    language.addItem("中文", "zh")
    fields = [
        _EditableFieldSpec("core_premise", "核心前提", "text", premise, "一句话说明主角、异常事件与代价。", True),
        _EditableFieldSpec("genre", "题材", "combo", genre, "决定冲突结构与信息揭示节奏。", True),
        _EditableFieldSpec("title", "作品名与语言", "title_language", (title, language), "作品名可留空。"),
    ]
    return _WorkflowFieldEditDialog(fields, selected_key="core_premise", title="编辑长篇立项字段")


def _dialog_for(state: str) -> QWidget:
    from novel_forge.desktop.pages.workflow import presets

    if state == "field-editor":
        return _field_editor()
    if state == "ai-hint":
        return presets._AiHintDialog("long")
    if state == "ai-polish":
        return presets._AiPolishDialog(
            "long",
            suggestions=["让授权链漏洞与姐姐失踪形成同一条因果链。", "用旧磁带承担证据与情绪锚点。", "压缩解释性旁白。"],
        )
    if state == "ai-progress":
        return presets._AiGenerateDialog(title="AI 正在构思…", message="AI 正在为你生成创作灵感，请稍候。")
    if state == "ai-failure":
        from novel_forge.desktop.components.dialogs import _build_message_box_dialog

        return _build_message_box_dialog(
            None,
            "AI 处理失败",
            "调用模型时出错：\n\n夹具模拟：服务暂时不可用。\n\n请确认模型配置正确（在「火候」设置页配置 API Key 和模型路由）。",
            icon=QMessageBox.Icon.Warning,
        )
    if state == "diff":
        return presets._DiffDialog(
            [
                {
                    "key": "core_premise",
                    "label": "核心前提",
                    "old": "记忆回收师在无主记忆里听见姐姐的求救声。",
                    "new": "持证记忆回收师林逐在无主记忆里听见姐姐的求救声，必须在公共记忆库封存前找回她。",
                },
                {
                    "key": "world_rule",
                    "label": "世界规则",
                    "old": "私人副本必须经由授权链登记。",
                    "new": "授权链既保护原主，也能被拥有权限的人伪造为官方真相。",
                },
                {
                    "key": "theme",
                    "label": "主题",
                    "old": "记忆的所有权。",
                    "new": "当记忆被合法删除，人要靠什么证明自己曾经爱过。",
                },
            ],
            window_title="AI 生成变更预览",
            title_text="AI 生成了 3 个字段变更",
            subtitle="先预览再应用；未勾选字段会保留当前值。",
        )
    if state == "history":
        return presets._HistoryDialog("long", "青瓦梦起 · 悬疑立项")
    raise ValueError(f"Unknown workflow preset state: {state}")


def capture_workflow_presets(*, output_dir: Path, theme_id: str, states: tuple[str, ...]) -> list[dict[str, object]]:
    from novel_forge.desktop.pages.workflow import presets
    from novel_forge.desktop.theme.palettes import DESKTOP_THEMES
    from novel_forge.desktop.theme.runtime import apply_desktop_theme

    if theme_id not in DESKTOP_THEMES:
        raise ValueError(f"Unknown desktop theme: {theme_id}")
    records: list[dict[str, object]] = []
    with deterministic_desktop_visual_runtime(), patch.object(presets, "load_ai_input_draft", return_value={}), patch.object(presets, "load_polish_history", side_effect=_history_fixture):
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        apply_desktop_theme(theme_id, app=app)
        for state in states:
            dialog = _dialog_for(state)
            dialog.show()
            QTest.qWait(120)
            file_name = f"workflow-preset--{state}--{theme_id}--default.png"
            destination = output_dir / file_name
            try:
                _capture_widget(dialog, destination)
                records.append(
                    {
                        "surface": "workflow-preset",
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
    parser.add_argument("--output-dir", type=Path, default=Path("docs/ui-parity/goldens/pyside-workflow-preset"))
    parser.add_argument("--theme", action="append", dest="themes", help="Theme id; repeat for a matrix.")
    parser.add_argument("--states", default=",".join(DEFAULT_STATES), help="Comma-separated state names.")
    args = parser.parse_args()
    states = tuple(item.strip() for item in args.states.split(",") if item.strip())
    unknown = sorted(set(states) - set(DEFAULT_STATES))
    if unknown:
        parser.error(f"Unsupported states: {', '.join(unknown)}")
    themes = tuple(args.themes or ("narrative_ember",))
    records: list[dict[str, object]] = []
    for theme_id in themes:
        records.extend(capture_workflow_presets(output_dir=args.output_dir, theme_id=theme_id, states=states))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "source": "PySide6 workflow forms and preset dialogs",
        "fixture": "long-form preset with fixed fields, diffs and history",
        "themes": themes,
        "records": records,
    }
    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
