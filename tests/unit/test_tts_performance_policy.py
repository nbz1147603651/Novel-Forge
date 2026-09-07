"""Regression coverage for the canonical character-performance policy."""

from __future__ import annotations

from novel_forge.tts.runtime.performance_policy import (
    derive_voice_performance_profile,
    hydrate_voice_team_performance_profiles,
    resolve_character_performance,
    voice_performance_profile,
    with_manual_performance_overrides,
)
from novel_forge.tts.schemas import (
    DubbingScript,
    DubbingSegment,
    SegmentType,
    TTSProvider,
    VoiceCastEntry,
    VoiceTeamContract,
)
from novel_forge.tts.script_review import build_dubbing_review_stage_cards


def test_conditional_prose_never_becomes_a_global_slowdown() -> None:
    profile = derive_voice_performance_profile(
        {
            "name": "沈岸",
            "personality": "沉稳克制",
            "voice": "提到苏晚相关内容时语速会骤然变慢、出现停顿。",
        }
    )

    assert profile.automatic_baseline.speed_offset == 0.0
    assert [item.direction for item in profile.conditional_directions] == [
        "slow_down",
        "pause_more",
    ]


def test_intense_conditional_direction_is_normalized_to_moderate() -> None:
    profile = derive_voice_performance_profile(
        {"voice": "提到苏晚相关内容时语速会突然变慢。"}
    )

    assert [item.direction for item in profile.conditional_directions] == ["slow_down"]
    assert [item.strength for item in profile.conditional_directions] == ["moderate"]


def test_manual_overrides_are_granular_and_keep_other_fields_automatic() -> None:
    entry = VoiceCastEntry(
        character_id="c1",
        character_name="林远",
        performance_profile=derive_voice_performance_profile(
            {"tts_voice_hints": {"preferred_speed": "fast", "preferred_pitch": "low"}}
        ),
        speed_offset=0.05,
        pitch_offset=-1,
    )

    updated = with_manual_performance_overrides(entry, vol_offset=0.2)
    profile = voice_performance_profile(updated)

    assert profile.manual_overrides.active_fields == {"volume"}
    assert profile.configured_offsets.speed_offset == 0.05
    assert profile.configured_offsets.pitch_offset == -1
    assert profile.configured_offsets.vol_offset == 0.2


def test_minimax_guard_only_limits_automatic_fields() -> None:
    automatic = VoiceCastEntry(
        character_id="c1",
        character_name="林远",
        speed_offset=-0.2,
        pitch_offset=-4,
    )
    automatic_result = resolve_character_performance(
        automatic,
        TTSProvider.MINIMAX,
        text="这是一句足够长的角色对白，用来验证平台声纹保护。",
    )
    manual_volume = with_manual_performance_overrides(automatic, vol_offset=0.2)
    mixed_result = resolve_character_performance(
        manual_volume,
        TTSProvider.MINIMAX,
        text="这是一句足够长的角色对白，用来验证逐字段覆盖。",
    )

    assert automatic_result.speed == 0.88  # neutral guard ±0.12: 1.0 + max(-0.12, -0.2) = 0.88
    assert automatic_result.pitch == 0
    assert automatic_result.provider_guard_applied is True
    assert mixed_result.speed_source == "automatic"
    assert mixed_result.volume_source == "manual"
    assert mixed_result.volume == 1.2


def test_short_utterance_uses_same_stable_result_for_every_call_path() -> None:
    entry = with_manual_performance_overrides(
        VoiceCastEntry(character_id="c1", character_name="林远"),
        speed_offset=-0.2,
        pitch_offset=-4,
    )

    first = resolve_character_performance(
        entry,
        TTSProvider.MINIMAX,
        text="知道了。",
    )
    second = resolve_character_performance(
        entry,
        TTSProvider.MINIMAX,
        text="知道了。",
    )

    assert first == second
    assert first.speed == 1.0
    assert first.pitch == 0
    assert first.manual_fields == ("speed", "pitch")
    assert first.short_utterance_stabilized is True


def test_llm_review_receives_conditional_contract_without_numeric_override() -> None:
    profile = derive_voice_performance_profile({"voice": "提到苏晚时语速会变慢，并出现停顿。"})
    team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="沈岸",
                performance_profile=profile,
            )
        ]
    )
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.DIALOGUE,
                character_id="c1",
                text="苏晚留下了这把钥匙。",
            )
        ],
    )

    cards = build_dubbing_review_stage_cards(script, team)
    cast = cards["voice_team"]

    assert isinstance(cast, list)
    assert cast[0]["conditional_performance_directions"][0]["direction"] == "slow_down"
    assert cards["review_boundaries"]["numeric_speed_pitch_volume_forbidden"] is True


def test_legacy_automatic_slowdown_migrates_without_recasting_voice() -> None:
    team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="沈岸",
                voice_id="minimax-actor-1",
                provider=TTSProvider.MINIMAX,
                speed_offset=-0.2,
                pitch_offset=-1,
                preview_audio_path="legacy-preview.mp3",
                preview_text="旧试听",
            )
        ]
    )

    migrated = hydrate_voice_team_performance_profiles(
        team,
        [
            {
                "character_id": "c1",
                "name": "沈岸",
                "voice": "提到苏晚时语速会骤然变慢、出现停顿。",
            }
        ],
    )
    entry = migrated.entries[0]

    assert entry.voice_id == "minimax-actor-1"
    assert entry.speed_offset == 0.0
    assert entry.pitch_offset == 0
    assert entry.preview_audio_path == ""
    assert [item.direction for item in voice_performance_profile(entry).conditional_directions] == [
        "slow_down",
        "pause_more",
    ]
