"""State packet renderer tests for foreshadowing card robustness."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def _ensure_qapp() -> None:
    from PySide6.QtWidgets import QApplication

    if QApplication.instance() is None:
        QApplication([])


def _base_state_packet(active_foreshadowing: list[dict[str, object]]) -> dict[str, object]:
    return {
        "chapter_number": 2,
        "chapter_outline": {"title": "测试章"},
        "canon_context": {
            "active_foreshadowing": active_foreshadowing,
        },
    }


def test_state_packet_foreshadowing_hides_placeholder_description() -> None:
    from novel_forge.desktop.pages.document_renderer_story_artifacts import render_state_packet

    data = _base_state_packet([
        {
            "id": "fs_1_1",
            "description": "fs_1_1",
            "status": "ForeshadowingStatus.PLANTED",
            "planted_chapter": 1,
            "resolved_chapter": 0,
        }
    ])

    html = render_state_packet(data).toHtml()

    assert "（未提供伏笔描述）" in html
    assert html.count("fs_1_1") == 1


def test_state_packet_foreshadowing_status_mapping_accepts_mixed_status_formats() -> None:
    from novel_forge.desktop.pages.document_renderer_story_artifacts import render_state_packet

    data = _base_state_packet(
        [
            {"id": "fs_a", "description": "A", "status": "planted", "planted_chapter": 1},
            {"id": "fs_b", "description": "B", "status": "ForeshadowingStatus.REINFORCED", "planted_chapter": 1},
            {"id": "fs_c", "description": "C", "status": "revealed", "planted_chapter": 1, "resolved_chapter": 2},
            {"id": "fs_d", "description": "D", "status": "abandoned", "planted_chapter": 1},
        ]
    )

    html = render_state_packet(data).toHtml()

    assert "🌱 已埋设" in html
    assert "🔗 已强化" in html
    assert "✅ 已揭晓" in html
    assert "🗑 已废弃" in html

