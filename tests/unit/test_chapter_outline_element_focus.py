"""Tests for chapter-level narrative element focus normalization."""

from __future__ import annotations

from novel_forge.core.schemas.outline import ChapterOutline


def test_element_focus_defaults_to_empty_list() -> None:
    chapter = ChapterOutline(chapter_number=1, goal="推进主线")
    assert chapter.element_focus == []


def test_element_focus_is_normalized_deduplicated_and_capped() -> None:
    chapter = ChapterOutline(
        chapter_number=2,
        goal="推进情感线",
        element_focus=[
            " romance_emotional_barriers ",
            "",
            "romance_relationship_contract",
            "romance_emotional_barriers",
            "romance_sweet_bitter_ratio",
            "extra_should_be_trimmed",
        ],
    )
    assert chapter.element_focus == [
        "romance_emotional_barriers",
        "romance_relationship_contract",
        "romance_sweet_bitter_ratio",
    ]


def test_element_focus_aliases_are_accepted() -> None:
    chapter_from_focus_elements = ChapterOutline.model_validate(
        {
            "chapter_number": 3,
            "goal": "推进悬疑",
            "focus_elements": ["mystery_clue_ledger"],
        }
    )
    chapter_from_focus_ids = ChapterOutline.model_validate(
        {
            "chapter_number": 4,
            "goal": "升级冲突",
            "element_focus_ids": ["mystery_red_herring"],
        }
    )

    assert chapter_from_focus_elements.element_focus == ["mystery_clue_ledger"]
    assert chapter_from_focus_ids.element_focus == ["mystery_red_herring"]
