"""Tests for CharacterState.voice field and merge-preserving logic."""

from __future__ import annotations

from novel_forge.core.parsing.normalizers import CharacterNormalizer
from novel_forge.core.schemas.story_state import CharacterState
from novel_forge.story_kernel.merger import merge_character_state_preserving_identity


class TestCharacterStateVoiceDefault:
    """CharacterState.voice defaults to empty string."""

    def test_voice_default_is_empty(self) -> None:
        state = CharacterState(name="林远")
        assert state.voice == ""

    def test_voice_present_in_model_fields(self) -> None:
        assert "voice" in CharacterState.model_fields

    def test_voice_field_description(self) -> None:
        field_info = CharacterState.model_fields["voice"]
        assert "声纹" in field_info.description or "voice" in field_info.description.lower()


class TestCharacterStateVoiceAssignment:
    """CharacterState.voice can be set and serialized."""

    def test_voice_set_on_creation(self) -> None:
        state = CharacterState(name="林远", voice="低沉沙哑")
        assert state.voice == "低沉沙哑"

    def test_voice_in_model_dump(self) -> None:
        state = CharacterState(name="林远", voice="短句为主")
        data = state.model_dump()
        assert data["voice"] == "短句为主"

    def test_voice_round_trip_json(self) -> None:
        voice = "语速偏慢，句末带语气词"
        state = CharacterState(name="林远", voice=voice)
        json_str = state.model_dump_json()
        restored = CharacterState.model_validate_json(json_str)
        assert restored.voice == voice

    def test_voice_round_trip_dict(self) -> None:
        voice = "紧张时咬唇"
        state = CharacterState(name="苏晴", voice=voice)
        data = state.model_dump()
        restored = CharacterState.model_validate(data)
        assert restored.voice == voice


class TestMergeCharacterStateVoicePreservation:
    """merge_character_state_preserving_identity preserves existing voice."""

    def test_existing_voice_not_overwritten(self) -> None:
        """When current has voice, incoming voice is discarded."""
        current = CharacterState(name="林远", voice="低沉沙哑，语速偏慢")
        updated = CharacterState(name="林远", voice="清脆明亮")

        result = merge_character_state_preserving_identity(current, updated)

        assert result.voice == "低沉沙哑，语速偏慢"

    def test_voice_set_when_current_empty(self) -> None:
        """When current has no voice, incoming voice is applied."""
        current = CharacterState(name="林远", voice="")
        updated = CharacterState(name="林远", voice="短句为主")

        result = merge_character_state_preserving_identity(current, updated)

        assert result.voice == "短句为主"

    def test_voice_set_when_current_none_like(self) -> None:
        """When current voice is falsy, incoming voice is applied."""
        current = CharacterState(name="林远")
        assert current.voice == ""

        updated = CharacterState(name="林远", voice="喜欢反问")
        result = merge_character_state_preserving_identity(current, updated)

        assert result.voice == "喜欢反问"

    def test_voice_preserved_across_multiple_merges(self) -> None:
        """Voice from initial state survives multiple merge cycles."""
        initial = CharacterState(name="林远", voice="低沉沙哑")

        # Simulate chapter extraction updates that don't include voice
        for chapter in range(1, 4):
            update = CharacterState(
                name="林远",
                voice="",
                last_seen_chapter=chapter,
            )
            initial = merge_character_state_preserving_identity(initial, update)

        assert initial.voice == "低沉沙哑"
        assert initial.last_seen_chapter == 3

    def test_voice_preserved_when_updated_has_empty_voice(self) -> None:
        """Empty incoming voice does not clear existing voice."""
        current = CharacterState(name="林远", voice="短句为主")
        updated = CharacterState(name="林远", voice="")

        result = merge_character_state_preserving_identity(current, updated)

        assert result.voice == "短句为主"

    def test_merge_none_current_sets_voice(self) -> None:
        """When current is None, voice from updated is set."""
        updated = CharacterState(name="林远", voice="低沉沙哑")

        result = merge_character_state_preserving_identity(None, updated)

        assert result.voice == "低沉沙哑"

    def test_merge_none_current_no_voice(self) -> None:
        """When current is None and updated has no voice, result has empty voice."""
        updated = CharacterState(name="林远")

        result = merge_character_state_preserving_identity(None, updated)

        assert result.voice == ""


def test_character_extraction_normalizer_drops_llm_voice() -> None:
    """Chapter extraction must not introduce voice into CanonState."""
    updates = CharacterNormalizer.normalize_character_updates(
        {
            "林远": {
                "voice": "LLM 临时编出的声纹",
                "location": "河边",
            }
        }
    )

    assert "voice" not in updates["林远"]
    assert updates["林远"]["physical"]["location"] == "河边"


def test_character_extraction_normalizer_normalizes_gender() -> None:
    """Only canonical gender values may enter extracted character state."""
    updates = CharacterNormalizer.normalize_character_updates(
        {
            "林远": {"gender": "male"},
            "苏晴": {"gender": "女"},
            "未知": {"gender": "unknown"},
        }
    )

    assert updates["林远"]["gender"] == "男"
    assert updates["苏晴"]["gender"] == "女"
    assert updates["未知"]["gender"] == ""
