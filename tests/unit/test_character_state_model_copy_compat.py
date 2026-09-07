"""Regression tests for CharacterState legacy update compatibility."""

from __future__ import annotations

from novel_forge.core.schemas.story_state import CharacterState


def test_character_state_model_copy_accepts_flat_legacy_updates() -> None:
    original = CharacterState(
        name="林远",
        location="雾霭小镇",
        emotional_state="困惑",
        inventory=["怀表"],
        knowledge=["裂缝会回应怀表"],
    )

    updated = original.model_copy(
        update={
            "location": "裂缝入口",
            "emotional_state": "惊恐",
            "inventory": "怀表",
            "knowledge": "未来残影出现",
            "last_seen_chapter": 2,
        }
    )

    assert updated.location == "裂缝入口"
    assert updated.emotional_state == "惊恐"
    assert updated.inventory == ["怀表"]
    assert updated.knowledge == ["未来残影出现"]
    assert updated.last_seen_chapter == 2
