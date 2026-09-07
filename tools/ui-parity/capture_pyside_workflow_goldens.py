#!/usr/bin/env python3
"""Capture deterministic PySide6 component goldens for workflow dialogs."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.desktop.ui_parity_fixtures import deterministic_desktop_visual_runtime

DEFAULT_DIALOGS = ("error-log", "cancel")


def _fixture_entries() -> list[dict[str, str]]:
    """Return stable entries covering the unresolved error-log state."""

    return [
        {
            "id": "fixture-format-error",
            "time": "2026-07-14 12:34:56",
            "job": "长篇立项 · 青瓦梦起",
            "task": "INIT_STORY_BIBLE",
            "task_label": "故事圣经",
            "attempt": "2/3",
            "error": "JSON 输出缺少字段：themes_and_symbols",
            "excerpt": "模型返回的对象未通过格式合同校验；已保存原始片段，等待下一次重试。",
            "log_file": "logs/fixture/format_errors/story_bible.json",
            "kind": "格式错误",
        },
        {
            "id": "fixture-auto-resolved",
            "time": "2026-07-14 12:35:12",
            "job": "长篇立项 · 青瓦梦起",
            "task": "PROFILE_STYLE",
            "task_label": "风格规范",
            "attempt": "1/3",
            "error": "格式重试后已恢复",
            "excerpt": "系统已接受后续的合规结果；本条保留为运行审计记录。",
            "log_file": "",
            "kind": "格式错误",
            "auto_resolved": "true",
        },
    ]


def _capture(dialog: QDialog, destination: Path) -> None:
    pixmap = QPixmap(dialog.size())
    pixmap.fill(dialog.palette().color(dialog.backgroundRole()))
    dialog.render(pixmap)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not pixmap.save(str(destination), "PNG"):
        raise RuntimeError(f"Unable to save screenshot: {destination}")


def capture_error_log(*, output_dir: Path, theme_id: str) -> dict[str, object]:
    from novel_forge.desktop.pages.workflow.widgets import TaskFlowErrorLogDialog
    from novel_forge.desktop.theme.palettes import DESKTOP_THEMES
    from novel_forge.desktop.theme.runtime import apply_desktop_theme

    if theme_id not in DESKTOP_THEMES:
        raise ValueError(f"Unknown desktop theme: {theme_id}")

    with deterministic_desktop_visual_runtime():
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        apply_desktop_theme(theme_id, app=app)
        dialog = TaskFlowErrorLogDialog(_fixture_entries())
        dialog.show()
        QTest.qWait(120)
        file_name = f"workflow-dialog--error-log--{theme_id}--unresolved.png"
        destination = output_dir / file_name
        try:
            _capture(dialog, destination)
            return {
                "dialog": "error-log",
                "theme": theme_id,
                "state": "unresolved",
                "size": {"width": dialog.width(), "height": dialog.height()},
                "file": file_name,
                "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            }
        finally:
            dialog.close()
            dialog.deleteLater()
            QTest.qWait(10)


def capture_cancel(*, output_dir: Path, theme_id: str) -> dict[str, object]:
    """Render the source-sized stop confirmation with a fixed checkpoint."""

    from novel_forge.desktop.pages.workflow.jobs import CancelJobDialog
    from novel_forge.desktop.theme.palettes import DESKTOP_THEMES
    from novel_forge.desktop.theme.runtime import apply_desktop_theme

    if theme_id not in DESKTOP_THEMES:
        raise ValueError(f"Unknown desktop theme: {theme_id}")
    with deterministic_desktop_visual_runtime():
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        apply_desktop_theme(theme_id, app=app)
        dialog = CancelJobDialog("长篇立项 · 青瓦梦起", "叙事蓝图 · 7/15")
        dialog.show()
        QTest.qWait(120)
        file_name = f"workflow-dialog--cancel--{theme_id}--confirmation.png"
        destination = output_dir / file_name
        try:
            _capture(dialog, destination)
            return {
                "dialog": "cancel",
                "theme": theme_id,
                "state": "confirmation",
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
    parser.add_argument("--output-dir", type=Path, default=Path("docs/ui-parity/goldens/pyside-workflow-dialogs"))
    parser.add_argument("--theme", action="append", dest="themes", help="Theme id; repeat for a matrix.")
    parser.add_argument("--dialogs", default=",".join(DEFAULT_DIALOGS), help="Comma-separated dialog ids.")
    args = parser.parse_args()
    dialog_ids = tuple(item.strip() for item in args.dialogs.split(",") if item.strip())
    unknown = sorted(set(dialog_ids) - set(DEFAULT_DIALOGS))
    if unknown:
        parser.error(f"Unsupported dialogs: {', '.join(unknown)}")
    themes = tuple(args.themes or ("narrative_ember",))
    capture_by_id = {"error-log": capture_error_log, "cancel": capture_cancel}
    records = [capture_by_id[dialog_id](output_dir=args.output_dir, theme_id=theme_id) for theme_id in themes for dialog_id in dialog_ids]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "source": "PySide6 workflow dialogs",
        "fixture": "workflow error log and stop confirmation with a preserved checkpoint",
        "themes": themes,
        "records": records,
    }
    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
