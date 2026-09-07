"""Tests for _generate_suggested_context outline-awareness."""

from __future__ import annotations

from unittest.mock import AsyncMock

from novel_forge.memory.motif import Motif, MotifTracker


def _make_tracker() -> MotifTracker:
    return MotifTracker(router=AsyncMock(), builder=AsyncMock())


def _make_motif(
    name: str = "雨",
    category: str = "意象",
    associated_characters: list[str] | None = None,
    thematic_meaning: str = "雨→清洗→重生",
) -> Motif:
    return Motif(
        motif_id="test-1",
        name=name,
        category=category,
        is_intentional=True,
        occurrence_count=5,
        first_appearance_chapter=1,
        last_appearance_chapter=5,
        associated_characters=associated_characters or [],
        thematic_meaning=thematic_meaning,
    )


class TestGenerateSuggestedContext:
    """Tests for _generate_suggested_context outline-aware behavior."""

    def test_fallback_to_heuristic_when_outline_is_none(self) -> None:
        """When chapter_outline is None, uses category-based heuristic."""
        tracker = _make_tracker()
        motif = _make_motif(name="雨", category="意象")

        result = tracker._generate_suggested_context(motif, "", None)

        assert result == "可在场景描写中自然融入雨意象"

    def test_fallback_to_heuristic_when_outline_is_empty(self) -> None:
        """When chapter_outline is empty dict, uses category-based heuristic."""
        tracker = _make_tracker()
        motif = _make_motif(name="雨", category="意象")

        result = tracker._generate_suggested_context(motif, "", {})

        assert result == "可在场景描写中自然融入雨意象"

    def test_goal_based_suggestion(self) -> None:
        """When chapter_outline has goal, returns goal-based suggestion."""
        tracker = _make_tracker()
        motif = _make_motif(name="雨", thematic_meaning="雨→清洗→重生")
        outline = {"goal": "主角面对过去", "pov_character": "", "element_focus": []}

        result = tracker._generate_suggested_context(motif, "", outline)

        assert "本章目标「主角面对过去」与雨母题（雨→清洗→重生）高度契合，可在关键场景中呼应" == result

    def test_pov_character_match_suggestion(self) -> None:
        """When pov_character is in motif's associated_characters, returns pov-based suggestion."""
        tracker = _make_tracker()
        motif = _make_motif(
            name="雨",
            associated_characters=["林轩", "苏晴"],
            thematic_meaning="雨→清洗→重生",
        )
        outline = {"goal": "", "pov_character": "林轩", "element_focus": []}

        result = tracker._generate_suggested_context(motif, "", outline)

        assert result == "作为林轩的核心意象，本章可通过雨强化角色情感"

    def test_pov_character_no_match_falls_back(self) -> None:
        """When pov_character not in associated_characters, falls back to heuristic."""
        tracker = _make_tracker()
        motif = _make_motif(
            name="雨",
            associated_characters=["林轩"],
            thematic_meaning="雨→清洗→重生",
        )
        outline = {"goal": "", "pov_character": "陌生人", "element_focus": []}

        result = tracker._generate_suggested_context(motif, "", outline)

        assert result == "可在场景描写中自然融入雨意象"

    def test_element_focus_overlap_suggestion(self) -> None:
        """When element_focus overlaps with motif category, returns element-based suggestion."""
        tracker = _make_tracker()
        motif = _make_motif(name="雨", category="意象", thematic_meaning="雨→清洗→重生")
        outline = {"goal": "", "pov_character": "", "element_focus": ["意象", "动作"]}

        result = tracker._generate_suggested_context(motif, "", outline)

        assert result == "本章意象要素焦点与雨母题呼应，建议自然融入"

    def test_element_focus_no_overlap_falls_back(self) -> None:
        """When element_focus doesn't overlap with motif category, falls back to heuristic."""
        tracker = _make_tracker()
        motif = _make_motif(name="雨", category="意象", thematic_meaning="雨→清洗→重生")
        outline = {"goal": "", "pov_character": "", "element_focus": ["动作", "声音"]}

        result = tracker._generate_suggested_context(motif, "", outline)

        assert result == "可在场景描写中自然融入雨意象"

    def test_goal_takes_priority_over_pov(self) -> None:
        """When both goal and pov are available, goal takes priority."""
        tracker = _make_tracker()
        motif = _make_motif(
            name="雨",
            associated_characters=["林轩"],
            thematic_meaning="雨→清洗→重生",
        )
        outline = {
            "goal": "主角面对过去",
            "pov_character": "林轩",
            "element_focus": ["意象"],
        }

        result = tracker._generate_suggested_context(motif, "", outline)

        assert "本章目标「主角面对过去」" in result

    def test_sensory_category_fallback(self) -> None:
        """Sensory category motif uses sensory fallback."""
        tracker = _make_tracker()
        motif = _make_motif(name="寒意", category="感官")

        result = tracker._generate_suggested_context(motif, "", None)

        assert result == "可通过寒意感官细节唤起读者记忆"

    def test_action_category_fallback(self) -> None:
        """Action category motif uses action fallback."""
        tracker = _make_tracker()
        motif = _make_motif(name="奔跑", category="动作")

        result = tracker._generate_suggested_context(motif, "", None)

        assert result == "可在关键动作中重复奔跑动作模式"

    def test_other_category_fallback(self) -> None:
        """Other category motif uses generic fallback."""
        tracker = _make_tracker()
        motif = _make_motif(name="孤独", category="主题")

        result = tracker._generate_suggested_context(motif, "", None)

        assert result == "可在适当时机呼应孤独"