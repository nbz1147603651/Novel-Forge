"""Tests for outline-aware motif suggestions in MotifTracker.

Verifies that get_suggestions_for_chapter() properly boosts motif
priorities when a chapter_outline is provided, and that backward
compatibility is maintained when chapter_outline is None.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from novel_forge.memory.motif import Motif, MotifTracker


def _make_tracker() -> MotifTracker:
    return MotifTracker(router=AsyncMock(), builder=AsyncMock())


def _add_motif(
    tracker: MotifTracker,
    motif_id: str,
    name: str,
    category: str = "意象",
    chapters: list[int] | None = None,
    associated_characters: list[str] | None = None,
    thematic_meaning: str = "",
) -> None:
    """Helper to register a motif with tracking data."""
    chapters = chapters or [1]
    motif = Motif(
        motif_id=motif_id,
        name=name,
        category=category,
        is_intentional=True,
        occurrence_count=len(chapters) + 5,
        first_appearance_chapter=chapters[0] if chapters else 0,
        last_appearance_chapter=chapters[-1] if chapters else 0,
        associated_characters=associated_characters or [],
        thematic_meaning=thematic_meaning,
    )
    tracker._motifs[motif_id] = motif
    tracker._recent_usage[motif_id] = chapters
    for ch in chapters:
        if ch not in tracker._chapter_motifs:
            tracker._chapter_motifs[ch] = set()
        tracker._chapter_motifs[ch].add(motif_id)


class TestOutlineAwareSuggestions:
    """Tests for chapter_outline-aware suggestion boosting."""

    def test_outline_boosts_matching_motif_priority(self) -> None:
        """Motifs matching chapter outline goal should be boosted to higher priority."""
        tracker = _make_tracker()

        # "雨" last appeared chapter 1, current is 10 → 9 chapters since
        _add_motif(tracker, "rain-1", "雨", category="意象", chapters=[1])
        # "镜子" last appeared chapter 1, current is 10 → 9 chapters since
        _add_motif(tracker, "mirror-1", "镜子", category="符号", chapters=[1])

        # Without outline: both should be medium priority (occurrence_count=6, chapters_since=9)
        tracker.get_suggestions_for_chapter(current_chapter=10)

        # With outline: goal contains "雨", so "雨" should be boosted
        outline = {
            "goal": "在雨中揭示真相",
            "pov_character": "李明",
            "element_focus": ["意象"],
        }
        suggestions_with_outline = tracker.get_suggestions_for_chapter(
            current_chapter=10,
            chapter_outline=outline,
        )
        with_outline_priorities = {s.motif_name: s.priority for s in suggestions_with_outline}

        # "雨" should be boosted (medium → high) due to goal match + element_focus overlap
        assert with_outline_priorities.get("雨") == "high"
        # Unrelated history should not be recommended
        assert "镜子" not in with_outline_priorities

    def test_outline_pov_character_boost(self) -> None:
        """Motifs associated with POV character should be boosted."""
        tracker = _make_tracker()

        _add_motif(
            tracker,
            "ring-1",
            "戒指",
            category="符号",
            chapters=[1],
            associated_characters=["李明"],
        )
        _add_motif(
            tracker,
            "sword-1",
            "剑",
            category="动作",
            chapters=[1],
            associated_characters=["王芳"],
        )

        outline = {
            "goal": "李明发现旧戒指",
            "pov_character": "李明",
            "element_focus": [],
        }
        suggestions = tracker.get_suggestions_for_chapter(
            current_chapter=10,
            chapter_outline=outline,
        )
        priorities = {s.motif_name: s.priority for s in suggestions}

        # "戒指" boosted: goal contains name + POV character match
        assert priorities.get("戒指") == "high"
        # "剑" not boosted: POV character doesn't match
        assert "剑" not in priorities

    def test_no_outline_does_not_invent_current_need(self) -> None:
        """When chapter_outline is None, no expression need should be inferred."""
        tracker = _make_tracker()

        _add_motif(tracker, "rain-1", "雨", category="意象", chapters=[1])
        _add_motif(tracker, "mirror-1", "镜子", category="符号", chapters=[1])

        suggestions = tracker.get_suggestions_for_chapter(current_chapter=10)

        # Both should be medium priority (occurrence_count=6, chapters_since=9)
        priorities = {s.motif_name: s.priority for s in suggestions}
        assert "雨" not in priorities
        assert "镜子" not in priorities
        # Max 5 suggestions
        assert len(suggestions) <= 5

    def test_prompt_safe_filters_soft_only_categories(self) -> None:
        """Prompt-safe suggestions exclude theme/symbol while default UI suggestions keep them."""
        tracker = _make_tracker()

        _add_motif(tracker, "rain-1", "雨", category="意象", chapters=[1])
        _add_motif(tracker, "theme-1", "救赎", category="主题", chapters=[1])
        _add_motif(tracker, "mirror-1", "镜子", category="符号", chapters=[1])

        default_names = {
            s.motif_name
            for s in tracker.get_suggestions_for_chapter(
                current_chapter=10, current_context="雨 救赎 镜子"
            )
        }
        prompt_safe_names = {
            s.motif_name
            for s in tracker.get_suggestions_for_chapter(
                current_chapter=10, current_context="雨 救赎 镜子", prompt_safe=True
            )
        }

        assert {"雨", "救赎", "镜子"} <= default_names
        assert "雨" in prompt_safe_names
        assert "救赎" not in prompt_safe_names
        assert "镜子" not in prompt_safe_names

    def test_outline_element_focus_boost(self) -> None:
        """Motifs whose category matches element_focus should be boosted."""
        tracker = _make_tracker()

        _add_motif(tracker, "rain-1", "雨", category="意象", chapters=[1])
        _add_motif(tracker, "whisper-1", "低语", category="声音", chapters=[1])

        outline = {
            "goal": "寂静的夜晚",
            "motif_requests": ["rain-1", "whisper-1"],
            "pov_character": "",
            "element_focus": ["声音", "感官"],
        }
        suggestions = tracker.get_suggestions_for_chapter(
            current_chapter=10,
            chapter_outline=outline,
        )
        priorities = {s.motif_name: s.priority for s in suggestions}

        # "低语" boosted: category "声音" in element_focus
        assert priorities.get("低语") == "high"
        # "雨" not boosted: category "意象" not in element_focus
        assert priorities.get("雨") == "medium"

    def test_suggested_context_contains_outline_terms(self) -> None:
        """When chapter_outline has goal, suggested_context should contain the goal term."""
        tracker = _make_tracker()

        _add_motif(
            tracker,
            "rain-1",
            "雨",
            category="意象",
            chapters=[1],
            thematic_meaning="雨→清洗→重生",
        )

        outline = {
            "goal": "雨中重逢",
            "pov_character": "李明",
            "element_focus": ["意象"],
        }
        suggestions = tracker.get_suggestions_for_chapter(
            current_chapter=10,
            chapter_outline=outline,
        )

        # Find the "雨" suggestion
        rain_suggestion = next((s for s in suggestions if s.motif_name == "雨"), None)
        assert rain_suggestion is not None
        # suggested_context should contain the outline goal "重逢"
        assert "重逢" in rain_suggestion.suggested_context

    def test_no_outline_fallback_heuristic(self) -> None:
        """When chapter_outline is None, suggested_context should use heuristic text."""
        tracker = _make_tracker()

        _add_motif(
            tracker,
            "rain-1",
            "雨",
            category="意象",
            chapters=[1],
            thematic_meaning="雨→清洗→重生",
        )

        suggestions = tracker.get_suggestions_for_chapter(
            current_chapter=10, current_context="雨中相遇"
        )

        rain_suggestion = next((s for s in suggestions if s.motif_name == "雨"), None)
        assert rain_suggestion is not None
        # Without outline, should fall back to heuristic for "意象" category
        assert "可在场景描写中自然融入" in rain_suggestion.suggested_context

    def test_empty_outline_fields(self) -> None:
        """When chapter_outline has empty strings/lists, should not request an expression."""
        tracker = _make_tracker()

        _add_motif(
            tracker,
            "rain-1",
            "雨",
            category="意象",
            chapters=[1],
            thematic_meaning="雨→清洗→重生",
        )

        # Outline with all empty fields
        outline = {
            "goal": "",
            "pov_character": "",
            "element_focus": [],
        }
        suggestions = tracker.get_suggestions_for_chapter(
            current_chapter=10,
            chapter_outline=outline,
        )

        rain_suggestion = next((s for s in suggestions if s.motif_name == "雨"), None)
        assert rain_suggestion is None

    def test_multiple_motifs_different_boosts(self) -> None:
        """Multiple motifs with different outline matches get different priority boosts."""
        tracker = _make_tracker()

        # "雨" matches goal
        _add_motif(tracker, "rain-1", "雨", category="意象", chapters=[1])
        # "李明" matches POV character
        _add_motif(
            tracker,
            "ring-1",
            "戒指",
            category="符号",
            chapters=[1],
            associated_characters=["李明"],
        )
        # "低语" matches element_focus
        _add_motif(tracker, "whisper-1", "低语", category="声音", chapters=[1])
        # "剑" matches nothing
        _add_motif(tracker, "sword-1", "剑", category="动作", chapters=[1])

        outline = {
            "goal": "雨中发现真相",
            "motif_requests": ["rain-1", "ring-1", "whisper-1"],
            "pov_character": "李明",
            "element_focus": ["声音"],
        }
        suggestions = tracker.get_suggestions_for_chapter(
            current_chapter=10,
            chapter_outline=outline,
        )
        priorities = {s.motif_name: s.priority for s in suggestions}

        # All matching motifs should be boosted to high
        assert priorities.get("雨") == "high"
        assert priorities.get("戒指") == "high"
        assert priorities.get("低语") == "high"
        # "剑" should remain medium (no outline match)
        assert "剑" not in priorities

    def test_max_5_suggestions_preserved(self) -> None:
        """Even with many matching motifs, max 5 suggestions returned."""
        tracker = _make_tracker()

        # Add 10 motifs, all qualifying (chapters_since >= 5, occurrence_count >= 3)
        for i in range(10):
            _add_motif(
                tracker,
                f"motif-{i}",
                f"母题{i}",
                category="意象",
                chapters=[1],
            )

        outline = {
            "goal": "检查线索",
            "motif_requests": [f"motif-{i}" for i in range(10)],
            "pov_character": "",
            "element_focus": [],
        }
        suggestions = tracker.get_suggestions_for_chapter(
            current_chapter=10,
            chapter_outline=outline,
        )

        # Relevant candidates are ranked at the retrieval boundary.
        assert len(suggestions) == 5
