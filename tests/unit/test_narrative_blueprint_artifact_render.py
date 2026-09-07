"""Regression tests for narrative blueprint artifact rendering."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QScrollArea,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
)

from novel_forge.desktop.pages.document_renderers import (
    NarrativeBlueprintWidget,
    smart_render_document,
)
from novel_forge.desktop.pages.standalone.subplot_manager import (
    SubplotItemWidget,
    SubplotManagerPanel,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def test_artifact_narrative_blueprint_subplot_tab_has_no_stale_duplicate_rows(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    project_dir = tmp_path / "project"
    plans_dir = project_dir / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)

    blueprint_path = plans_dir / "narrative_blueprint.json"
    blueprint_path.write_text(
        json.dumps(
            {
                "synopsis": "测试叙事蓝图",
                "narrative_phases": [
                    {"phase_name": "开场", "chapter_start": 1, "chapter_end": 5},
                ],
                "subplot_plan": [
                    {
                        "name": "甲线",
                        "description": "甲线描述",
                        "priority": "primary",
                        "involved_chapters": [1, 2, 3],
                        "chapter_events": [],
                        "weave_links": [],
                    },
                    {
                        "name": "乙线",
                        "description": "乙线描述",
                        "priority": "normal",
                        "involved_chapters": [2, 3, 4],
                        "chapter_events": [],
                        "weave_links": [],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    widget = smart_render_document(blueprint_path)
    assert isinstance(widget, QTabWidget)
    assert any(widget.tabText(idx) == "支线矩阵" for idx in range(widget.count()))

    subplot_tab_idx = -1
    for idx in range(widget.count()):
        if widget.tabText(idx) == "支线管理":
            subplot_tab_idx = idx
            break
    assert subplot_tab_idx >= 0
    widget.setCurrentIndex(subplot_tab_idx)

    host = QDialog()
    host.resize(1020, 640)
    host_layout = QVBoxLayout(host)
    host_layout.addWidget(widget)
    host.show()
    qapp.processEvents()
    qapp.processEvents()

    panel = widget.findChild(SubplotManagerPanel)
    assert panel is not None
    subplot_rows = panel.findChildren(SubplotItemWidget)

    # Regression target:
    # artifact dialog used to show stale rows left by deleteLater() during
    # double initial render. We should only have real subplot rows.
    assert len(subplot_rows) == 2
    assert all(row.isVisible() for row in subplot_rows)
    names = {str(getattr(row, "_subplot", {}).get("name", "")) for row in subplot_rows}
    assert names == {"甲线", "乙线"}

    host.close()
    widget.deleteLater()
    qapp.processEvents()


def test_subplot_execution_matrix_artifact_renders_rich_view(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    matrix_path = tmp_path / "subplot_execution_matrix.json"
    matrix_path.write_text(
        json.dumps(
            {
                "current_chapter": 0,
                "closed_loop_ratio": 0.5,
                "rows": [
                    {
                        "name": "甲线",
                        "priority": "primary",
                        "status": "planned_closed_loop",
                        "planned_chapters": [1, 2, 4],
                        "next_planned_chapter": 0,
                        "resolution_chapter": 4,
                        "resolution_target": "main_turning_point",
                        "resolution_type": "reveal",
                        "trigger_links": [
                            {
                                "source_type": "main_plot",
                                "target_subplot": "甲线",
                                "trigger_chapter": 1,
                                "link_type": "trigger_start",
                                "description": "主线事件拉开甲线",
                            }
                        ],
                        "feedback_links": [
                            {
                                "source_type": "subplot",
                                "target_subplot": "主线",
                                "trigger_chapter": 4,
                                "link_type": "reveal_key",
                                "description": "甲线揭露关键信息",
                            }
                        ],
                        "cross_subplot_links": [],
                        "closed_loop": True,
                        "notes": [],
                    },
                    {
                        "name": "乙线",
                        "priority": "normal",
                        "status": "planned_open_loop",
                        "planned_chapters": [2, 3],
                        "next_planned_chapter": 0,
                        "resolution_chapter": 0,
                        "resolution_target": "",
                        "resolution_type": "",
                        "trigger_links": [],
                        "feedback_links": [],
                        "cross_subplot_links": [],
                        "closed_loop": False,
                        "notes": ["缺少反哺主线"],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    widget = smart_render_document(matrix_path)
    assert isinstance(widget, QTextBrowser)
    plain_text = widget.toPlainText()
    assert "支线执行矩阵" in plain_text
    assert "闭环率 50%" in plain_text
    assert "甲线" in plain_text
    assert "缺少反哺主线" in plain_text


def test_narrative_blueprint_timeline_allows_horizontal_density(qapp: QApplication) -> None:
    data = {
        "narrative_phases": [{"phase_name": "长篇阶段", "chapter_start": 1, "chapter_end": 80}],
        "subplot_plan": [],
    }

    timeline = NarrativeBlueprintWidget(data)
    assert timeline.minimumWidth() >= 80 * 14 + 260


def test_narrative_blueprint_timeline_fits_sixty_one_chapters_in_wide_view(
    qapp: QApplication,
) -> None:
    data = {
        "narrative_phases": [{"phase_name": "长篇阶段", "chapter_start": 1, "chapter_end": 61}],
        "subplot_plan": [],
    }

    timeline = NarrativeBlueprintWidget(data)
    assert timeline.minimumWidth() <= 1280


def test_narrative_blueprint_timeline_separates_dense_mainline_from_subplots(
    qapp: QApplication,
) -> None:
    data = {
        "narrative_phases": [
            {"phase_name": "初遇", "chapter_start": 1, "chapter_end": 10},
            {"phase_name": "暗流", "chapter_start": 11, "chapter_end": 20},
        ],
        "key_turning_points": [
            {"chapter_number": 10, "title": "转折一"},
            {"chapter_number": 11, "title": "转折二"},
            {"chapter_number": 12, "title": "转折三"},
            {"chapter_number": 25, "title": "转折四"},
        ],
        "subplot_plan": [
            {
                "name": "支线甲",
                "involved_chapters": [5, 10, 12, 25],
                "chapter_events": [
                    {"chapter_number": 10, "event": "甲线反哺"},
                    {"chapter_number": 25, "event": "甲线收束"},
                ],
                "weave_links": [
                    {
                        "target_subplot": "主线",
                        "trigger_chapter": 10,
                        "link_type": "feed_main",
                    },
                    {
                        "target_subplot": "主线",
                        "trigger_chapter": 10,
                        "link_type": "reveal_key",
                    },
                ],
            },
            {
                "name": "支线乙",
                "involved_chapters": [6, 11, 25],
                "chapter_events": [{"chapter_number": 11, "event": "乙线推进"}],
                "weave_links": [],
            },
        ],
    }

    timeline = NarrativeBlueprintWidget(data)
    timeline.resize(timeline.minimumSize())
    pixmap = QPixmap(timeline.size())
    timeline.render(pixmap)

    label_rects = getattr(timeline, "_mainline_label_rects", [])
    subplot_rects = getattr(timeline, "_subplot_rects", [])
    feedback_markers = getattr(timeline, "_feedback_markers", [])

    assert label_rects
    assert subplot_rects
    assert feedback_markers
    first_subplot_top = min(rect.top() for rect, _ in subplot_rects)
    assert max(rect.bottom() for rect in label_rects) + 20 <= first_subplot_top
    assert min(abs(y_from - y_to) for _, y_from, y_to, _, _ in feedback_markers) >= 30


def test_narrative_blueprint_timeline_normalizes_orphan_subplot_chapters(
    qapp: QApplication,
) -> None:
    data = {
        "synopsis": "支线节点回归",
        "narrative_phases": [
            {"phase_name": "推进", "chapter_start": 1, "chapter_end": 12, "description": "推进"}
        ],
        "subplot_plan": [
            {
                "name": "府兵制改革",
                "involved_chapters": [2, 5, 8, 12],
                "chapter_events": [{"chapter_number": 2, "event": "提出改革"}],
            }
        ],
    }

    timeline = NarrativeBlueprintWidget(data)
    event_map = timeline._subplot_event_map(timeline._subplots[0])

    assert sorted(event_map) == [2, 5, 8, 12]
    assert event_map[2] == "提出改革"
    assert event_map[12] == ""


def test_narrative_blueprint_tab_uses_horizontal_scroll_when_needed(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    blueprint_path = tmp_path / "narrative_blueprint.json"
    blueprint_path.write_text(
        json.dumps(
            {
                "synopsis": "长篇测试",
                "narrative_phases": [
                    {"phase_name": "长线推进", "chapter_start": 1, "chapter_end": 96}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    widget = smart_render_document(blueprint_path)
    assert isinstance(widget, QTabWidget)
    scrolls = widget.findChildren(QScrollArea)
    assert any(
        scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
        for scroll in scrolls
    )
