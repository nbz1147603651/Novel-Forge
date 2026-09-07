"""Tests for chapter-session note sanitization and injection."""

from __future__ import annotations

from types import SimpleNamespace

from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.workspace.sessions.chapter_session_state import (
    _strip_auto_repair_notes,
    apply_notes_to_bundle,
)


def _bundle(goal: str, notes: str = "") -> SimpleNamespace:
    chapter_outline = ChapterOutline(
        chapter_number=2,
        title="测试章",
        goal=goal,
        notes=notes,
        expected_word_count=3000,
    )
    return SimpleNamespace(chapter_outline=chapter_outline)


def test_strip_auto_repair_notes_keeps_manual_content() -> None:
    notes = (
        "请增强苏令娴的能动性。\n\n"
        "【自动修复提示】上一轮正文在一致性校验未通过，请在本次方案里明确落实：\n"
        "- High-severity continuity issues remain after repair: 章节内容严重重复\n"
        "- 更漏声状态跳跃无过渡\n"
    )

    cleaned = _strip_auto_repair_notes(notes)

    assert cleaned == "请增强苏令娴的能动性。"


def test_apply_notes_to_bundle_does_not_pollute_goal_with_auto_repair_notes() -> None:
    auto_notes = (
        "【自动修复提示】上一轮正文在一致性校验未通过，请在本次方案里明确落实：\n"
        "- High-severity continuity issues remain after repair: 章节内容严重重复\n"
    )
    bundle = _bundle("推进禁室审讯线")

    apply_notes_to_bundle(bundle, auto_notes)

    assert bundle.chapter_outline.goal == "推进禁室审讯线"
    assert "自动修复提示" in bundle.chapter_outline.notes


def test_apply_notes_to_bundle_keeps_manual_constraints_in_goal() -> None:
    manual_notes = "请增加赵元与风伏京的对话博弈。"
    bundle = _bundle("推进禁室审讯线")

    apply_notes_to_bundle(bundle, manual_notes)

    assert "推进禁室审讯线" in bundle.chapter_outline.goal
    assert "补充约束：请增加赵元与风伏京的对话博弈。" in bundle.chapter_outline.goal
    assert manual_notes in bundle.chapter_outline.notes
