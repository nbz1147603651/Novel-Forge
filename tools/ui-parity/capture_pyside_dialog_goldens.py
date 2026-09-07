#!/usr/bin/env python3
"""Capture deterministic component goldens for chapter-studio dialogs.

Unlike the full-window capture, this tool renders a dialog at its native Qt
size. It is intentionally a component-level source artifact: React overlays
are compared against these captures only after their own app backdrop has
been separately validated.
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
from PySide6.QtWidgets import QApplication, QWidget

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.desktop.ui_parity_fixtures import deterministic_desktop_visual_runtime

DEFAULT_DIALOGS = (
    "export",
    "version-diff",
    "book-audit",
    "clean",
    "skip",
    "project-switch",
    "error-log",
    "checkpoint",
)


class _FixedSettings:
    """Prevent the source dialog from reading a developer's QSettings state."""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        return

    def value(self, _key: str, default: object = None, **_kwargs: object) -> object:
        return default


def _capture_widget(widget: QWidget, destination: Path) -> None:
    pixmap = QPixmap(widget.size())
    pixmap.fill(widget.palette().color(widget.backgroundRole()))
    widget.render(pixmap)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not pixmap.save(str(destination), "PNG"):
        raise RuntimeError(f"Unable to save screenshot: {destination}")


def _dialog_for(dialog_id: str) -> QWidget:
    from novel_forge.core.utils.version_diff import DraftVersionInfo
    from novel_forge.desktop.pages.chapter_studio.dialogs import (
        BookAuditDialog,
        CleanChaptersDialog,
        ExportDialog,
        ProjectSwitchConfirmDialog,
        SkipStrategyDialog,
        VersionDiffDialog,
    )
    from novel_forge.desktop.pages.chapter_studio.jobs import TaskFlowErrorLogDialog
    from novel_forge.desktop.components.checkpoint_dialog import CheckpointDialog
    from novel_forge.workspace.contracts import DecisionCheckpoint, DecisionOption

    completed_chapters = [1, 2, 3, 4]
    if dialog_id == "export":
        return ExportDialog(completed_chapters, Path("/tmp/nimo-export"), "测试长篇")
    if dialog_id == "version-diff":
        return VersionDiffDialog(
            [
                DraftVersionInfo(1, "初始草稿", "v1.md", word_count=3840),
                DraftVersionInfo(2, "第 1 轮编辑", "v2.md", word_count=4216),
            ],
            chapter_num=4,
        )
    if dialog_id == "book-audit":
        return BookAuditDialog(completed_chapters, has_prior_audit=True, prior_audit_status="上次审计已完成")
    if dialog_id == "clean":
        return CleanChaptersDialog(default_cutoff=5, max_chapter=24)
    if dialog_id == "skip":
        return SkipStrategyDialog(done_count=4)
    if dialog_id == "project-switch":
        return ProjectSwitchConfirmDialog("测试长篇", chapter_number=5, job_status="正在生成第 5 章")
    if dialog_id == "error-log":
        return TaskFlowErrorLogDialog(
            [
                {
                    "id": "fixture-chapter-causal-error",
                    "time": "2026-07-14 14:08:31",
                    "job": "第 5 章 · 档案室",
                    "task": "VALIDATE_CAUSAL",
                    "task_label": "因果验证",
                    "attempt": "1/2",
                    "error": "因果验证等待上游计划检查点",
                    "excerpt": "章节计划尚未确认，因果校验被安全地延后；现有草稿与故事状态均未被修改。",
                    "log_file": "logs/fixture/chapter_005/format_errors/causal.json",
                    "kind": "等待确认",
                }
            ]
        )
    if dialog_id == "checkpoint":
        # The source component is intentionally a non-modal floating tool.
        # A visible parent establishes its normal right-edge positioning while
        # the captured image remains the native-sized panel itself.
        parent = QWidget()
        parent.resize(1280, 860)
        parent.show()
        checkpoint = DecisionCheckpoint(
            checkpoint_id="fixture-plan-checkpoint",
            checkpoint_type="plan_checkpoint",
            summary="本章的开场证据、人物边界与章节落点已经汇总。请选择继续路径。",
            prompt="需要人工确认后才能继续执行本章草稿。",
            options=[
                DecisionOption(
                    option_id="adopt",
                    label="采用当前方案",
                    description="保留时间戳、授权链和周砚在场的既定边界。",
                    is_recommended=True,
                    semantic_tag="accept_and_archive",
                ),
                DecisionOption(
                    option_id="regenerate",
                    label="带备注重新规划",
                    description="基于人工补充约束重新生成章节计划。",
                    semantic_tag="regenerate_plan",
                ),
            ],
            related_artifacts=["chapter_plan", "bridge"],
        )
        dialog = CheckpointDialog(
            parent,
            checkpoint,
            checkpoint.summary,
            free_floating=True,
        )
        # Keep the Python wrapper alive for the lifetime of the returned
        # child.  A Qt dynamic property does not retain a QWidget wrapper.
        dialog._ui_parity_fixture_parent = parent  # type: ignore[attr-defined]
        return dialog
    raise ValueError(f"Unknown dialog id: {dialog_id}")


def capture_dialogs(*, output_dir: Path, theme_id: str, dialog_ids: tuple[str, ...]) -> list[dict[str, Any]]:
    from novel_forge.desktop.pages.chapter_studio import dialogs as dialog_module
    from novel_forge.desktop.theme.palettes import DESKTOP_THEMES
    from novel_forge.desktop.theme.runtime import apply_desktop_theme

    if theme_id not in DESKTOP_THEMES:
        raise ValueError(f"Unknown desktop theme: {theme_id}")

    records: list[dict[str, Any]] = []
    with deterministic_desktop_visual_runtime(), patch.object(dialog_module, "QSettings", _FixedSettings):
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        apply_desktop_theme(theme_id, app=app)
        for dialog_id in dialog_ids:
            dialog = _dialog_for(dialog_id)
            if dialog_id == "checkpoint":
                # CheckpointDialog owns its placement and skips the embedded
                # opacity animation in free-floating mode.
                getattr(dialog, "show_animated")()
            else:
                dialog.show()
            QTest.qWait(120)
            file_name = f"chapter-dialog--{dialog_id}--{theme_id}--default.png"
            destination = output_dir / file_name
            try:
                _capture_widget(dialog, destination)
                records.append(
                    {
                        "dialog": dialog_id,
                        "theme": theme_id,
                        "size": {"width": dialog.width(), "height": dialog.height()},
                        "file": file_name,
                        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                    }
                )
            finally:
                dialog.close()
                dialog.deleteLater()
                if dialog_id == "checkpoint":
                    parent = getattr(dialog, "_ui_parity_fixture_parent", None)
                    if isinstance(parent, QWidget):
                        parent.close()
                        parent.deleteLater()
                QTest.qWait(10)
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("docs/ui-parity/goldens/pyside-dialogs"))
    parser.add_argument("--theme", action="append", dest="themes", help="Theme id; repeat for a matrix.")
    parser.add_argument("--dialogs", default=",".join(DEFAULT_DIALOGS), help="Comma-separated dialog ids.")
    args = parser.parse_args()
    dialog_ids = tuple(item.strip() for item in args.dialogs.split(",") if item.strip())
    unknown = sorted(set(dialog_ids) - set(DEFAULT_DIALOGS))
    if unknown:
        parser.error(f"Unsupported dialogs: {', '.join(unknown)}")
    themes = tuple(args.themes or ("narrative_ember",))

    records: list[dict[str, Any]] = []
    for theme_id in themes:
        records.extend(capture_dialogs(output_dir=args.output_dir, theme_id=theme_id, dialog_ids=dialog_ids))
    manifest = {
        "source": "PySide6 chapter-studio dialogs",
        "fixture": "static chapter dialog fixture",
        "themes": themes,
        "records": records,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
