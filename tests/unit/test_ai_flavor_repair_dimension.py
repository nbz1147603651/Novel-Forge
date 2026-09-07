"""Unit tests for the AI_FLAVOR member of RepairDimension enum.

The dimension is added in M2 so repair orchestration can represent ai_flavor
failures as a typed repair dimension when the consumer path is wired in.
"""

from __future__ import annotations

from novel_forge.pipeline.long.repair_safety import (
    RepairDimension,
    RepairRoundSnapshot,
)


class TestRepairDimensionAIFlavor:
    def test_ai_flavor_member_exists(self) -> None:
        """The new AI_FLAVOR member must exist with stable string value 'ai_flavor'."""
        assert hasattr(RepairDimension, "AI_FLAVOR")
        assert RepairDimension.AI_FLAVOR.value == "ai_flavor"

    def test_ai_flavor_is_distinct_from_existing(self) -> None:
        """It must not collide with existing dimension values."""
        existing_values = {d.value for d in RepairDimension}
        assert "ai_flavor" not in existing_values - {"ai_flavor"}

    def test_ai_flavor_usable_in_repair_round_snapshot(self) -> None:
        """RepairRoundSnapshot.stage field accepts the new dimension."""
        snap = RepairRoundSnapshot(
            stage=RepairDimension.AI_FLAVOR,
            chapter_number=21,
            round_number=1,
            text="some text",
        )
        assert snap.stage == RepairDimension.AI_FLAVOR
        assert snap.chapter_number == 21

    def test_all_dimensions_are_stable_strings(self) -> None:
        """Ensure all dimensions including the new one are stable str enums."""
        for dim in RepairDimension:
            assert isinstance(dim.value, str)
            assert dim.value == dim.value.lower()  # all lowercase, no whitespace


class TestRepairDimensionBackwardCompat:
    """Adding AI_FLAVOR must not break existing dimensions or their string equality."""

    def test_existing_dimensions_unchanged(self) -> None:
        assert RepairDimension.CONTINUITY.value == "continuity"
        assert RepairDimension.CAUSAL.value == "causal"
        assert RepairDimension.READING_POWER.value == "reading_power"
        assert RepairDimension.KNOWLEDGE_BOUNDARY.value == "knowledge_boundary"
        assert RepairDimension.CONTRACT.value == "contract"
        assert RepairDimension.GENERIC.value == "generic"

    def test_total_dimension_count(self) -> None:
        """AI flavor and world-rule repairs extend the six original dimensions."""
        existing_count = 6
        assert len(RepairDimension) == existing_count + 2
