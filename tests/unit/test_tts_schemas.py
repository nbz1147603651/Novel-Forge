"""Tests for TTS schemas — enums, schema validation, properties."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from novel_forge.tts.schemas import (
    EMOTION_MAPPINGS,
    DubbingSegment,
    EmotionTag,
    LanguageRun,
    SegmentType,
    TTSProgressState,
    TTSProvider,
    TTSRequest,
    VoiceCastEntry,
    VoiceCloneStatus,
    VoiceTeamContract,
)


def test_multilingual_vocal_direction_defaults_are_backward_compatible() -> None:
    segment = DubbingSegment(
        segment_index=0,
        segment_type=SegmentType.NARRATION,
        text="雨停了。",
    )

    assert segment.language_code == "auto"
    assert segment.language_runs == []
    assert segment.vocal_direction.delivery_style == "natural"
    assert segment.platform_extensions == {}


def test_language_run_fills_end_offset() -> None:
    run = LanguageRun(language="en", text="hello", start_char=3)

    assert run.end_char == 8


class TestTTSProvider:
    """Test TTSProvider enum."""

    def test_all_providers_exist(self) -> None:
        assert TTSProvider.MINIMAX.value == "minimax"
        assert TTSProvider.BAILIAN.value == "bailian"
        assert TTSProvider.DASHSCOPE.value == "dashscope"
        assert TTSProvider.TENCENT.value == "tencent"
        assert TTSProvider.VOLCENGINE_ARK.value == "volcengine_ark"
        assert TTSProvider.MIMO.value == "mimo"
        assert TTSProvider.LOCAL.value == "local"
        assert TTSProvider.QWEN3.value == "qwen3"
        assert TTSProvider.COSYVOICE.value == "cosyvoice"
        assert TTSProvider.OPENVOICE.value == "openvoice"
        assert TTSProvider.MOCK.value == "mock"

    def test_from_value(self) -> None:
        assert TTSProvider("minimax") == TTSProvider.MINIMAX
        assert TTSProvider("mock") == TTSProvider.MOCK

    def test_invalid_value_raises(self) -> None:
        with pytest.raises(ValueError):
            TTSProvider("invalid_provider")


class TestVoiceCastEntry:
    """Test VoiceCastEntry schema."""

    def test_is_ready(self) -> None:
        entry = VoiceCastEntry(
            character_id="c1",
            character_name="Test",
            clone_status=VoiceCloneStatus.READY,
        )
        assert entry.is_ready is True

    def test_is_not_ready(self) -> None:
        entry = VoiceCastEntry(
            character_id="c1",
            character_name="Test",
            clone_status=VoiceCloneStatus.PENDING,
        )
        assert entry.is_ready is False

    def test_is_expired(self) -> None:
        entry = VoiceCastEntry(
            character_id="c1",
            character_name="Test",
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        assert entry.is_expired is True

    def test_is_not_expired(self) -> None:
        entry = VoiceCastEntry(
            character_id="c1",
            character_name="Test",
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
        assert entry.is_expired is False

    def test_no_expiry_not_expired(self) -> None:
        entry = VoiceCastEntry(
            character_id="c1",
            character_name="Test",
            expires_at=None,
        )
        assert entry.is_expired is False


class TestVoiceTeamContract:
    """Test VoiceTeamContract schema."""

    def test_get_entry(self) -> None:
        team = VoiceTeamContract(
            entries=[
                VoiceCastEntry(character_id="c1", character_name="Alice"),
                VoiceCastEntry(character_id="c2", character_name="Bob"),
            ]
        )
        entry = team.get_entry("c1")
        assert entry is not None
        assert entry.character_name == "Alice"

    def test_get_entry_not_found(self) -> None:
        team = VoiceTeamContract(entries=[])
        assert team.get_entry("nonexistent") is None

    def test_get_ready_entries(self) -> None:
        team = VoiceTeamContract(
            entries=[
                VoiceCastEntry(
                    character_id="c1",
                    character_name="Alice",
                    clone_status=VoiceCloneStatus.READY,
                ),
                VoiceCastEntry(
                    character_id="c2",
                    character_name="Bob",
                    clone_status=VoiceCloneStatus.PENDING,
                ),
            ]
        )
        ready = team.get_ready_entries()
        assert len(ready) == 1
        assert ready[0].character_id == "c1"


class TestTTSRequest:
    """Test TTSRequest schema."""

    def test_valid_request(self) -> None:
        req = TTSRequest(text="Hello world")
        assert req.text == "Hello world"
        assert req.speed == 1.0
        assert req.volume == 1.0
        assert req.pitch == 0

    def test_empty_text_raises(self) -> None:
        with pytest.raises(ValueError):
            TTSRequest(text="")

    def test_speed_bounds(self) -> None:
        with pytest.raises(ValueError):
            TTSRequest(text="test", speed=0.1)
        with pytest.raises(ValueError):
            TTSRequest(text="test", speed=3.0)


class TestEmotionMappings:
    """Test emotion → TTS parameter mappings."""

    def test_all_emotions_mapped(self) -> None:
        for emotion in EmotionTag:
            assert emotion in EMOTION_MAPPINGS

    def test_happy_offsets(self) -> None:
        mapping = EMOTION_MAPPINGS[EmotionTag.HAPPY]
        assert mapping.speed_offset > 0
        assert mapping.vol_offset > 0


class TestTTSProgressState:
    """Test TTS progress state for resume."""

    def test_is_complete(self) -> None:
        state = TTSProgressState(
            chapter_number=1,
            voice_team_done=True,
            script_done=True,
            synthesis_done=True,
            assembly_done=True,
        )
        assert state.is_complete is True

    def test_is_not_complete(self) -> None:
        state = TTSProgressState(chapter_number=1)
        assert state.is_complete is False
