"""Tests for final-chapter opening dedup behavior."""

from __future__ import annotations

from novel_forge.pipeline.chapter_runner import ChapterRunner


def test_remove_opening_echo_drops_only_first_paragraph() -> None:
    previous = (
        "他把门轻轻带上，走廊里只剩风声。\n\n"
        "走到楼梯口时，他没有回头，只是把手机静音后塞进外套口袋。"
    )
    current = (
        "走到楼梯口时，他没有回头，只是把手机静音后塞进外套口袋。\n\n"
        "清晨的河面起了一层薄雾，林屿把围巾收紧。"
    )

    cleaned, info = ChapterRunner._remove_opening_echo_from_previous(current, previous)

    assert info is not None
    assert info["removed_paragraphs"] == 1
    assert cleaned.startswith("清晨的河面起了一层薄雾")


def test_remove_opening_echo_keeps_text_when_not_duplicate() -> None:
    previous = "他把门轻轻带上，走廊里只剩风声。"
    current = "第二天训练场上，第一颗发球直接擦线得分。"

    cleaned, info = ChapterRunner._remove_opening_echo_from_previous(current, previous)

    assert info is None
    assert cleaned == current
