"""Tests for CharacterProfile.voice field: assignment, default, serialization."""

from __future__ import annotations

import json

from novel_forge.core.schemas.bible import CharacterBible, CharacterProfile


class TestCharacterProfileVoiceDefault:
    """Voice field defaults to empty string."""

    def test_voice_default_is_empty_string(self) -> None:
        profile = CharacterProfile(name="林远")
        assert profile.voice == ""

    def test_voice_present_in_model_fields(self) -> None:
        assert "voice" in CharacterProfile.model_fields

    def test_voice_in_correct_fields_set(self) -> None:
        assert "voice" in CharacterProfile._correct_fields


class TestCharacterProfileVoiceAssignment:
    """Voice field can be set with various values."""

    def test_voice_string_value(self) -> None:
        profile = CharacterProfile(
            name="林远",
            voice="短句为主，喜欢用反问句，紧张时会下意识咬唇。",
        )
        assert "短句" in profile.voice
        assert "反问句" in profile.voice

    def test_voice_empty_string_explicit(self) -> None:
        profile = CharacterProfile(name="林远", voice="")
        assert profile.voice == ""

    def test_voice_long_text(self) -> None:
        long_voice = "声音低沉沙哑，" * 50
        profile = CharacterProfile(name="林远", voice=long_voice)
        assert len(profile.voice) > 100

    def test_voice_chinese_text(self) -> None:
        voice = "语速偏慢，句末常带'嘛''呢'等语气词，情绪激动时会切换方言。"
        profile = CharacterProfile(name="苏晴", voice=voice)
        assert profile.voice == voice

    def test_voice_coerced_from_list(self) -> None:
        """LLM may return voice as a list; validator coerces to string."""
        profile = CharacterProfile.model_validate(
            {"name": "林远", "voice": ["短句", "反问", "咬唇"]}
        )
        assert isinstance(profile.voice, str)
        assert len(profile.voice) > 0

    def test_voice_coerced_from_dict(self) -> None:
        """LLM may return voice as a dict; validator coerces to string."""
        profile = CharacterProfile.model_validate(
            {"name": "林远", "voice": {"style": "短句", "habit": "反问"}}
        )
        assert isinstance(profile.voice, str)


class TestCharacterProfileVoiceSerialization:
    """Voice field round-trips through serialization."""

    def test_voice_in_model_dump(self) -> None:
        voice = "低沉沙哑，语速偏慢"
        profile = CharacterProfile(name="林远", voice=voice)
        data = profile.model_dump()
        assert data["voice"] == voice

    def test_voice_in_model_dump_json(self) -> None:
        voice = "低沉沙哑"
        profile = CharacterProfile(name="林远", voice=voice)
        data = json.loads(profile.model_dump_json())
        assert data["voice"] == voice

    def test_voice_round_trip_through_json(self) -> None:
        voice = "短句为主，喜欢用反问句。"
        original = CharacterProfile(name="林远", voice=voice)
        json_str = original.model_dump_json()
        restored = CharacterProfile.model_validate_json(json_str)
        assert restored.voice == voice

    def test_voice_round_trip_through_dict(self) -> None:
        voice = "紧张时会下意识咬唇"
        original = CharacterProfile(name="苏晴", voice=voice)
        data = original.model_dump()
        restored = CharacterProfile.model_validate(data)
        assert restored.voice == voice

    def test_voice_in_character_bible(self) -> None:
        """Voice survives CharacterBible serialization."""
        voice_a = "低沉沙哑"
        voice_b = "清脆明亮"
        bible = CharacterBible(
            characters=[
                CharacterProfile(name="林远", voice=voice_a),
                CharacterProfile(name="苏晴", voice=voice_b),
            ]
        )
        data = json.loads(bible.model_dump_json())
        assert data["characters"][0]["voice"] == voice_a
        assert data["characters"][1]["voice"] == voice_b

    def test_voice_from_legacy_data_without_voice_key(self) -> None:
        """Legacy data without voice key gets empty string default."""
        legacy = {"name": "林远", "personality": "沉默寡言"}
        profile = CharacterProfile.model_validate(legacy)
        assert profile.voice == ""
