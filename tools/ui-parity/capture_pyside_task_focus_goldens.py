#!/usr/bin/env python3
"""Capture deterministic PySide6 source goldens for task-focus components."""

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


def _capture_floating_stream(*, output_dir: Path, theme_id: str) -> dict[str, object]:
    from novel_forge.desktop.components.task_focus.stream_window import FloatingStreamWindow
    from novel_forge.desktop.jobs import DesktopJobRecord, DesktopJobState
    from novel_forge.desktop.task_observation import (
        ObservedStreamState,
        ObservedTaskState,
        StreamSegment,
    )
    from novel_forge.desktop.theme.palettes import DESKTOP_THEMES
    from novel_forge.desktop.theme.runtime import apply_desktop_theme

    if theme_id not in DESKTOP_THEMES:
        raise ValueError(f"Unknown desktop theme: {theme_id}")

    with deterministic_desktop_visual_runtime():
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        apply_desktop_theme(theme_id, app=app)
        job = DesktopJobRecord(
            job_id="fixture-stream-job",
            kind="init_long",
            label="长篇立项 · 青瓦梦起",
            project_id="qingwa",
            status=DesktopJobState.RUNNING,
            current_step="blueprint",
        )
        segments = (
            StreamSegment("reasoning", "先核对主题兑现是否落在可见行动，而非抽象说明。"),
            StreamSegment("content", "主题兑现章节必须出现可见的场景、行动或后果，不能停留在认知层面。"),
            StreamSegment("reasoning", "继续检查前文锚点是否能支持本章的因果推进。"),
            StreamSegment("content", "当前校验通过后，才会将结果写入下一阶段的工作契约。"),
        )
        stream = ObservedStreamState(
            job_id=job.job_id,
            stream_id="fixture-stream-1",
            task="blueprint",
            attempt=1,
            status="streaming",
            text="".join(segment.text for segment in segments if segment.kind == "content"),
            reasoning_text="".join(segment.text for segment in segments if segment.kind == "reasoning"),
            segments=segments,
            started_at="2026-07-14T12:34:00+00:00",
            updated_at="2026-07-14T12:34:07+00:00",
            provider="openai",
            model="gpt-4o-mini",
            prompt_tokens=2150,
            completion_tokens=832,
            total_tokens=2982,
            cost_usd=0.0112,
        )
        state = ObservedTaskState(
            job=job,
            focus_reason="live_stream",
            current_node="blueprint",
            status_label="输出中",
            status_tone="running",
            stream=stream,
            events=("正在接收 一致性画像 输出",),
            diagnostics=("格式合同等待校验",),
            progress_percent=48,
        )
        window = FloatingStreamWindow()
        window.update_state(state)
        # The constructor initially has no stream and therefore selects its
        # call-details fallback.  The fixture freezes the rich stream tab.
        window._tabs.setCurrentWidget(window._stream_tab)  # type: ignore[attr-defined]
        window.show()
        QTest.qWait(180)
        file_name = f"task-focus--floating-stream--{theme_id}--streaming.png"
        destination = output_dir / file_name
        try:
            # Qt.Tool windows can composite their child controls separately
            # under the offscreen platform plugin.  QWidget.grab() captures
            # the composed widget while render() only yielded the base frame.
            pixmap = window.grab()
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not pixmap.save(str(destination), "PNG"):
                raise RuntimeError(f"Unable to save screenshot: {destination}")
            return {
                "component": "floating-stream",
                "theme": theme_id,
                "state": "streaming",
                "size": {"width": window.width(), "height": window.height()},
                "file": file_name,
                "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            }
        finally:
            window.close()
            window.deleteLater()
            QTest.qWait(10)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("docs/ui-parity/goldens/pyside-task-focus"))
    parser.add_argument("--theme", action="append", dest="themes", help="Theme id; repeat for a matrix.")
    args = parser.parse_args()
    themes = tuple(args.themes or ("narrative_ember",))
    records = [_capture_floating_stream(output_dir=args.output_dir, theme_id=theme_id) for theme_id in themes]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "source": "PySide6 task-focus components",
        "fixture": "interleaved task stream with reasoning and content",
        "themes": themes,
        "records": records,
    }
    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
