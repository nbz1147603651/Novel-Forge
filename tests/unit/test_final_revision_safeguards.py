"""Safeguards for desktop final-draft revision."""

from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from novel_forge.desktop.pages.standalone.final_revision import (
    FinalRevisionWidget,
    _build_revision_guard_report,
    _count_display_words,
    _parse_direction_response,
    _revision_unified_diff,
    _SelectionRevisionDialog,
    _text_hash,
)
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.project_staleness import InvalidationScope


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def test_revision_guard_rejects_prompt_leaks() -> None:
    report = _build_revision_guard_report(
        "这里混入了 opening_contract 和 scene_intent 这样的规划字段。",
        previous_text="旧正文" * 1000,
        expected_word_count=3000,
    )

    assert report.ok is False
    assert report.prompt_leaks
    assert any("提示词" in item for item in report.errors)


def test_final_revision_display_word_count_uses_shared_metric() -> None:
    assert _count_display_words("你好，world 123！\n“世界”") == 4


def test_revision_guard_rejects_critically_short_long_chapter() -> None:
    report = _build_revision_guard_report(
        "太短了。",
        previous_text="旧正文" * 1000,
        expected_word_count=3000,
    )

    assert report.ok is False
    assert report.min_acceptable == 900
    assert any("正文过短" in item for item in report.errors)


def test_revision_guard_warns_but_allows_manual_word_count_outliers() -> None:
    report = _build_revision_guard_report(
        "字" * 6611,
        previous_text="字" * 6603,
        expected_word_count=4500,
    )

    assert report.ok is True
    assert report.errors == ()
    assert any("字数与章节目标偏离较大" in item for item in report.warnings)


def test_revision_diff_preview_is_bounded() -> None:
    before = "\n".join(f"旧行 {i}" for i in range(400))
    after = "\n".join(f"新行 {i}" for i in range(400))

    diff = _revision_unified_diff(before, after)

    assert "保存前" in diff
    assert "保存后" in diff
    assert "省略" in diff
    assert len(diff.splitlines()) <= 262


def test_direction_response_parser_discards_json_scaffolding() -> None:
    directions, note = _parse_direction_response(
        """
        分析如下：
        {
          "directions": [
            "更凝练",
            "增强感官画面",
            "聚焦脚步声质感",
          ],
          "note": "守住克制的生理紧张。"
        }
        """
    )

    assert directions == ["更凝练", "增强感官画面", "聚焦脚步声质感"]
    assert "{" not in directions
    assert all("directions" not in item for item in directions)
    assert note == "守住克制的生理紧张。"


def test_selection_revision_dialog_keeps_fixed_geometry(
    qapp: QApplication,
) -> None:
    dialog = _SelectionRevisionDialog(selection_words=119)
    before_size = dialog.size()

    assert before_size.width() == 560
    assert before_size.height() == 300
    assert dialog._spinner_timer.isActive()

    dialog.set_directions(["更凝练", "增强感官画面", "聚焦脚步声质感"])
    dialog.set_analysis_note("可直接输入修订方向，也可点选一个火候关键词。")
    qapp.processEvents()

    assert dialog.size() == before_size
    assert not dialog._spinner_timer.isActive()
    assert dialog._spinner.isHidden()

    dialog.deleteLater()
    qapp.processEvents()


def test_final_revision_writes_version_and_refresh_marker(
    tmp_path,
    qapp: QApplication,
) -> None:
    project_dir = tmp_path / "demo"
    layout = ProjectLayout(project_dir)
    layout.ensure_dirs()
    chapter_path = layout.chapter_path(1)
    before = "旧正文。" * 800
    after = "新正文。" * 800
    chapter_path.write_text(before, encoding="utf-8")
    (project_dir / "outline.json").write_text(
        json.dumps(
            {"chapters": [{"chapter_number": 1, "expected_word_count": 3000}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    widget = FinalRevisionWidget(
        project_id="demo",
        project_dir=project_dir,
        chapter_path=chapter_path,
        chapter_number=1,
        title="第一章",
        initial_text=before,
    )
    guard = _build_revision_guard_report(after, previous_text=before, expected_word_count=3000)

    record = widget._write_revision_version(before, after, guard)
    widget._mark_revision_requires_refresh(record, guard)

    assert record.before_path.read_text(encoding="utf-8") == before
    assert record.after_path.read_text(encoding="utf-8") == after
    meta = json.loads(record.meta_path.read_text(encoding="utf-8"))
    assert meta["previous_hash"] == _text_hash(before)
    assert meta["current_hash"] == _text_hash(after)

    status_path = project_dir / "states" / "final_revision_status" / "chapter_001.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["requires_reevaluation"] is True
    assert status["requires_state_reextract"] is True
    assert status["requires_canon_reextract"] is True
    assert status["scope"] == "downstream"
    assert status["revision_id"] == record.timestamp

    widget.deleteLater()
    qapp.processEvents()


def test_final_revision_refresh_marker_records_next_scope(
    tmp_path,
    qapp: QApplication,
) -> None:
    project_dir = tmp_path / "demo"
    layout = ProjectLayout(project_dir)
    layout.ensure_dirs()
    chapter_path = layout.chapter_path(1)
    before = "旧正文。" * 800
    after = "新正文。" * 800
    chapter_path.write_text(before, encoding="utf-8")
    layout.chapter_path(2).write_text("第二章。" * 800, encoding="utf-8")
    layout.chapter_path(3).write_text("第三章。" * 800, encoding="utf-8")
    (project_dir / "outline.json").write_text(
        json.dumps(
            {"chapters": [{"chapter_number": 1, "expected_word_count": 3000}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    widget = FinalRevisionWidget(
        project_id="demo",
        project_dir=project_dir,
        chapter_path=chapter_path,
        chapter_number=1,
        title="第一章",
        initial_text=before,
    )
    guard = _build_revision_guard_report(after, previous_text=before, expected_word_count=3000)

    record = widget._write_revision_version(before, after, guard)
    chapter_path.write_text(after, encoding="utf-8")
    widget._mark_revision_requires_refresh(record, guard, scope=InvalidationScope.NEXT)

    status_path = project_dir / "states" / "final_revision_status" / "chapter_001.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["scope"] == "next"
    assert status["range_start"] == 2
    assert status["range_end"] == 2
    assert status["affected_chapters"] == [2]

    widget.deleteLater()
    qapp.processEvents()


def test_final_revision_staging_does_not_modify_official_chapter(
    tmp_path,
    qapp: QApplication,
) -> None:
    project_dir = tmp_path / "demo"
    layout = ProjectLayout(project_dir)
    layout.ensure_dirs()
    chapter_path = layout.chapter_path(1)
    before = "旧正文。" * 800
    after = "新正文。" * 800
    chapter_path.write_text(before, encoding="utf-8")

    widget = FinalRevisionWidget(
        project_id="demo",
        project_dir=project_dir,
        chapter_path=chapter_path,
        chapter_number=1,
        title="第一章",
        initial_text=before,
    )
    guard = _build_revision_guard_report(after, previous_text=before, expected_word_count=3000)

    meta_path = widget._write_staged_revision(after, guard, status="staged")

    assert chapter_path.read_text(encoding="utf-8") == before
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["status"] == "staged"
    assert (meta_path.parent / "after.md").read_text(encoding="utf-8") == after

    widget.deleteLater()
    qapp.processEvents()


def test_selection_revision_action_stays_in_main_toolbar(
    tmp_path,
    qapp: QApplication,
) -> None:
    project_dir = tmp_path / "demo"
    layout = ProjectLayout(project_dir)
    layout.ensure_dirs()
    chapter_path = layout.chapter_path(1)
    chapter_path.write_text("正文。" * 400, encoding="utf-8")

    widget = FinalRevisionWidget(
        project_id="demo",
        project_dir=project_dir,
        chapter_path=chapter_path,
        chapter_number=1,
        title="第一章",
        initial_text="正文。" * 400,
    )

    assert widget._ai_btn.isHidden()
    widget._enter_revision()

    assert widget._ai_btn.parentWidget() is widget._read_btn.parentWidget()
    assert widget._ai_btn.parentWidget().objectName() == "finalRevisionToolbar"
    assert not widget._ai_btn.isHidden()
    assert not widget._read_btn.isHidden()

    widget.deleteLater()
    qapp.processEvents()
