"""Tests for get_forward_looking_guidance() in MotifTracker.

Verifies the four guidance categories, caps, and edge cases.
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
    last_chapter: int = 1,
    occurrence_count: int = 5,
    associated_characters: list[str] | None = None,
    thematic_meaning: str = "",
    retired: bool = False,
) -> None:
    """Helper to register a motif with tracking data."""
    motif = Motif(
        motif_id=motif_id,
        name=name,
        category=category,
        is_intentional=True,
        occurrence_count=occurrence_count,
        first_appearance_chapter=1,
        last_appearance_chapter=last_chapter,
        associated_characters=associated_characters or [],
        thematic_meaning=thematic_meaning,
        retired=retired,
    )
    tracker._motifs[motif_id] = motif
    tracker._recent_usage[motif_id] = [last_chapter]
    tracker._chapter_motifs[last_chapter].add(motif_id)


class TestForwardLookingGuidance:
    """Tests for get_forward_looking_guidance()."""

    async def test_dormant_callbacks_motifs_not_seen_20_plus_chapters(self) -> None:
        """Motifs not seen in 20+ chapters should appear in dormant_callbacks."""
        tracker = _make_tracker()
        _add_motif(tracker, "rain-1", "雨", last_chapter=5, occurrence_count=5)
        _add_motif(tracker, "mirror-1", "镜子", last_chapter=1, occurrence_count=3)

        result = await tracker.get_forward_looking_guidance(
            current_chapter=25, chapter_outline={"goal": "雨中检查镜子"}
        )

        dormant_ids = [m["motif_id"] for m in result["dormant_callbacks"]]
        assert "rain-1" in dormant_ids
        assert "mirror-1" in dormant_ids

    async def test_dormant_callbacks_excludes_single_occurrence(self) -> None:
        """Motifs with only 1 occurrence should not be flagged as dormant."""
        tracker = _make_tracker()
        _add_motif(tracker, "new-1", "新意象", last_chapter=1, occurrence_count=1)

        result = await tracker.get_forward_looking_guidance(current_chapter=25)

        dormant_ids = [m["motif_id"] for m in result["dormant_callbacks"]]
        assert "new-1" not in dormant_ids

    async def test_dormant_callback_gap_is_configurable(self) -> None:
        """Projects can tune how long a motif must sleep before callback guidance."""
        tracker = _make_tracker()
        _add_motif(tracker, "ring-1", "戒指", last_chapter=10, occurrence_count=3)

        default_gap = await tracker.get_forward_looking_guidance(
            current_chapter=25, chapter_outline={"goal": "抵押戒指"}
        )
        shorter_gap = await tracker.get_forward_looking_guidance(
            current_chapter=25,
            dormant_callback_min_chapters=15,
            chapter_outline={"goal": "抵押戒指"},
        )

        assert "ring-1" not in [m["motif_id"] for m in default_gap["dormant_callbacks"]]
        assert "ring-1" in [m["motif_id"] for m in shorter_gap["dormant_callbacks"]]

    async def test_pov_motifs_associated_with_character(self) -> None:
        """Motifs associated with POV character should appear in pov_motifs."""
        tracker = _make_tracker()
        _add_motif(
            tracker,
            "ring-1",
            "戒指",
            associated_characters=["李明", "王芳"],
        )
        _add_motif(
            tracker,
            "sword-1",
            "剑",
            associated_characters=["赵六"],
        )

        outline = {"goal": "抵押戒指", "pov_character": "李明", "element_focus": []}
        result = await tracker.get_forward_looking_guidance(
            current_chapter=10,
            chapter_outline=outline,
        )

        pov_ids = [m["motif_id"] for m in result["pov_motifs"]]
        assert "ring-1" in pov_ids
        assert "sword-1" not in pov_ids

    async def test_pov_motifs_empty_when_no_pov(self) -> None:
        """When no POV character specified, pov_motifs should be empty."""
        tracker = _make_tracker()
        _add_motif(
            tracker,
            "ring-1",
            "戒指",
            associated_characters=["李明"],
        )

        result = await tracker.get_forward_looking_guidance(current_chapter=10)

        assert result["pov_motifs"] == []

    async def test_plot_matched_motifs_thematic_meaning_matches_goal(self) -> None:
        """Motifs whose thematic_meaning overlaps with goal should appear in plot_matched."""
        tracker = _make_tracker()
        _add_motif(
            tracker,
            "rain-1",
            "雨",
            thematic_meaning="雨→清洗→重生",
        )
        _add_motif(
            tracker,
            "mirror-1",
            "镜子",
            thematic_meaning="揭示真相",
        )

        outline = {"goal": "揭示真相", "pov_character": "", "element_focus": []}
        result = await tracker.get_forward_looking_guidance(
            current_chapter=10,
            chapter_outline=outline,
        )

        matched_ids = [m["motif_id"] for m in result["plot_matched_motifs"]]
        assert "mirror-1" in matched_ids
        assert "rain-1" not in matched_ids

    async def test_strengthen_motifs_recent_low_occurrence(self) -> None:
        """Motifs with 3-10 chapters since last use and 1-3 occurrences must not create a strengthen duty."""
        tracker = _make_tracker()
        _add_motif(tracker, "weak-1", "弱意象", last_chapter=5, occurrence_count=2)
        _add_motif(tracker, "strong-1", "强意象", last_chapter=5, occurrence_count=10)
        _add_motif(tracker, "fresh-1", "新意象", last_chapter=7, occurrence_count=1)

        result = await tracker.get_forward_looking_guidance(current_chapter=10)

        strengthen_ids = [m["motif_id"] for m in result["strengthen_motifs"]]
        assert "weak-1" not in strengthen_ids
        assert "strong-1" not in strengthen_ids
        assert "fresh-1" not in strengthen_ids

    async def test_conceptual_categories_excluded_from_forward_prompt_guidance(self) -> None:
        """Theme and symbol categories are tracked, but not actively projected into prompts."""
        tracker = _make_tracker()
        _add_motif(
            tracker,
            "theme-1",
            "救赎",
            category="主题",
            last_chapter=1,
            occurrence_count=3,
            thematic_meaning="救赎",
        )
        _add_motif(
            tracker,
            "symbol-1",
            "镜子",
            category="符号",
            last_chapter=1,
            occurrence_count=3,
            associated_characters=["李明"],
            thematic_meaning="揭示真相",
        )
        _add_motif(
            tracker,
            "rain-1",
            "雨",
            category="意象",
            last_chapter=5,
            occurrence_count=2,
        )

        result = await tracker.get_forward_looking_guidance(
            current_chapter=10,
            chapter_outline={
                "goal": "在雨中揭示真相",
                "pov_character": "李明",
                "element_focus": [],
            },
            dormant_callback_min_chapters=5,
        )

        all_ids = (
            [m["motif_id"] for m in result["strengthen_motifs"]]
            + [m["motif_id"] for m in result["dormant_callbacks"]]
            + [m["motif_id"] for m in result["pov_motifs"]]
            + [m["motif_id"] for m in result["plot_matched_motifs"]]
        )
        assert "rain-1" in all_ids
        assert "theme-1" not in all_ids
        assert "symbol-1" not in all_ids

    async def test_retired_motifs_excluded(self) -> None:
        """Retired motifs should not appear in any category."""
        tracker = _make_tracker()
        _add_motif(
            tracker,
            "retired-1",
            "已退休",
            last_chapter=1,
            occurrence_count=5,
            thematic_meaning="真相→揭露",
            associated_characters=["李明"],
            retired=True,
        )

        outline = {"goal": "揭露真相", "pov_character": "李明", "element_focus": []}
        result = await tracker.get_forward_looking_guidance(
            current_chapter=25,
            chapter_outline=outline,
        )

        all_ids = (
            [m["motif_id"] for m in result["strengthen_motifs"]]
            + [m["motif_id"] for m in result["dormant_callbacks"]]
            + [m["motif_id"] for m in result["pov_motifs"]]
            + [m["motif_id"] for m in result["plot_matched_motifs"]]
        )
        assert "retired-1" not in all_ids

    async def test_per_category_cap_of_3(self) -> None:
        """Each category should be capped at 3 items."""
        tracker = _make_tracker()
        # Add 5 dormant motifs
        for i in range(5):
            _add_motif(
                tracker,
                f"dormant-{i}",
                f"休眠{i}",
                last_chapter=1,
                occurrence_count=3,
            )

        result = await tracker.get_forward_looking_guidance(current_chapter=25)

        assert len(result["dormant_callbacks"]) <= 3

    async def test_total_cap_of_8(self) -> None:
        """Total items across all categories should be capped at 8."""
        tracker = _make_tracker()
        # Add many motifs that will qualify for multiple categories
        for i in range(10):
            _add_motif(
                tracker,
                f"motif-{i}",
                f"母题{i}",
                last_chapter=1,
                occurrence_count=2,
                associated_characters=["李明"],
                thematic_meaning="真相→揭露",
            )

        outline = {"goal": "揭露真相", "pov_character": "LM", "element_focus": []}
        result = await tracker.get_forward_looking_guidance(
            current_chapter=25,
            chapter_outline=outline,
        )

        total = (
            len(result["strengthen_motifs"])
            + len(result["dormant_callbacks"])
            + len(result["pov_motifs"])
            + len(result["plot_matched_motifs"])
        )
        assert total <= 8

    async def test_empty_tracker_returns_empty_categories(self) -> None:
        """Empty MotifTracker should return empty guidance."""
        tracker = _make_tracker()

        result = await tracker.get_forward_looking_guidance(current_chapter=10)

        assert result["strengthen_motifs"] == []
        assert result["dormant_callbacks"] == []
        assert result["pov_motifs"] == []
        assert result["plot_matched_motifs"] == []

    async def test_no_outline_still_works(self) -> None:
        """Method should work without chapter_outline (pov/plot_matched will be empty)."""
        tracker = _make_tracker()
        _add_motif(
            tracker,
            "rain-1",
            "雨",
            last_chapter=1,
            occurrence_count=5,
            thematic_meaning="雨→清洗→重生",
            associated_characters=["李明"],
        )

        result = await tracker.get_forward_looking_guidance(current_chapter=25)

        # Dormancy without chapter relevance must not create a suggestion
        assert result["dormant_callbacks"] == []
        # pov and plot_matched require outline
        assert result["pov_motifs"] == []
        assert result["plot_matched_motifs"] == []

    async def test_dormant_callbacks_contains_required_fields(self) -> None:
        """Dormant callback items should contain all required fields."""
        tracker = _make_tracker()
        _add_motif(
            tracker,
            "rain-1",
            "雨",
            last_chapter=1,
            occurrence_count=5,
            thematic_meaning="雨→清洗→重生",
        )

        result = await tracker.get_forward_looking_guidance(
            current_chapter=25, chapter_outline={"goal": "雨中检查镜子"}
        )

        assert len(result["dormant_callbacks"]) >= 1
        item = result["dormant_callbacks"][0]
        assert "motif_id" in item
        assert "name" in item
        assert "category" in item
        assert "chapters_since" in item
        assert "thematic_meaning" in item
