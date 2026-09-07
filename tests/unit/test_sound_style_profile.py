"""Tests for sound style profile and deterministic seed derivation."""

from __future__ import annotations

from pathlib import Path

from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.sound_generation.schemas import SoundGenerationKind, SoundGenerationRequest
from novel_forge.tts.sound_generation.style_profile import (
    SoundStyleProfile,
    derive_deterministic_seed,
    load_style_profile,
    save_style_profile,
)


def _make_request(
    kind: SoundGenerationKind = SoundGenerationKind.SFX,
    cue_label: str = "door creak",
) -> SoundGenerationRequest:
    return SoundGenerationRequest(
        request_id="test-seed-1",
        chapter_number=1,
        kind=kind,
        cue_index=0,
        cue_label=cue_label,
        prompt="test prompt for seed derivation",
        duration_ms=3000,
        output_format="wav",
    )


class TestSoundStyleProfile:
    def test_default_profile(self) -> None:
        profile = SoundStyleProfile()
        assert profile.profile_id == "default"
        assert profile.base_seed == 42
        assert profile.style_tags == []
        assert profile.spectral_hint == ""

    def test_negative_prompt_for_sfx(self) -> None:
        profile = SoundStyleProfile()
        neg = profile.negative_prompt_for(SoundGenerationKind.SFX)
        assert "speech" in neg
        assert "digital artifacts" in neg

    def test_negative_prompt_for_soundscape(self) -> None:
        profile = SoundStyleProfile()
        neg = profile.negative_prompt_for(SoundGenerationKind.SOUNDSCAPE)
        assert "abrupt events" in neg

    def test_negative_prompt_for_bgm(self) -> None:
        profile = SoundStyleProfile()
        neg = profile.negative_prompt_for(SoundGenerationKind.BGM)
        assert "lyrics" in neg

    def test_style_suffix_empty(self) -> None:
        profile = SoundStyleProfile()
        assert profile.style_suffix() == ""

    def test_style_suffix_with_tags(self) -> None:
        profile = SoundStyleProfile(style_tags=["warm", "analog"])
        suffix = profile.style_suffix()
        assert "warm" in suffix
        assert "analog" in suffix


class TestDeterministicSeed:
    def test_same_input_same_seed(self) -> None:
        profile = SoundStyleProfile(base_seed=100)
        request = _make_request()
        seed1 = derive_deterministic_seed(profile, request)
        seed2 = derive_deterministic_seed(profile, request)
        assert seed1 == seed2

    def test_different_cue_different_seed(self) -> None:
        profile = SoundStyleProfile(base_seed=100)
        request_a = _make_request(cue_label="door creak")
        request_b = _make_request(cue_label="glass shatter")
        seed_a = derive_deterministic_seed(profile, request_a)
        seed_b = derive_deterministic_seed(profile, request_b)
        assert seed_a != seed_b

    def test_different_kind_different_seed(self) -> None:
        profile = SoundStyleProfile(base_seed=100)
        request_sfx = _make_request(kind=SoundGenerationKind.SFX, cue_label="rain")
        request_bgm = _make_request(kind=SoundGenerationKind.BGM, cue_label="rain")
        seed_sfx = derive_deterministic_seed(profile, request_sfx)
        seed_bgm = derive_deterministic_seed(profile, request_bgm)
        assert seed_sfx != seed_bgm

    def test_different_base_seed_different_result(self) -> None:
        profile_a = SoundStyleProfile(base_seed=0)
        profile_b = SoundStyleProfile(base_seed=999)
        request = _make_request()
        seed_a = derive_deterministic_seed(profile_a, request)
        seed_b = derive_deterministic_seed(profile_b, request)
        assert seed_a != seed_b

    def test_seed_within_valid_range(self) -> None:
        profile = SoundStyleProfile(base_seed=2**31 - 1)
        request = _make_request()
        seed = derive_deterministic_seed(profile, request)
        assert 0 <= seed < 2**31

    def test_style_tags_affect_seed(self) -> None:
        profile_plain = SoundStyleProfile(base_seed=42, style_tags=[])
        profile_warm = SoundStyleProfile(base_seed=42, style_tags=["warm"])
        request = _make_request()
        seed_plain = derive_deterministic_seed(profile_plain, request)
        seed_warm = derive_deterministic_seed(profile_warm, request)
        assert seed_plain != seed_warm


class TestLoadSaveProfile:
    def test_load_missing_returns_default(self, tmp_path: Path) -> None:
        layout = ProjectLayout(tmp_path / "demo")
        profile = load_style_profile(layout)
        assert profile.profile_id == "default"

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        layout = ProjectLayout(tmp_path / "demo")
        layout.ensure_dirs()
        profile = SoundStyleProfile(
            profile_id="noir",
            base_seed=777,
            style_tags=["dark", "rainy"],
            spectral_hint="emphasize low-mid warmth",
        )
        save_style_profile(layout, profile)
        loaded = load_style_profile(layout)
        assert loaded.profile_id == "noir"
        assert loaded.base_seed == 777
        assert loaded.style_tags == ["dark", "rainy"]
        assert loaded.spectral_hint == "emphasize low-mid warmth"

    def test_load_corrupt_json_returns_default(self, tmp_path: Path) -> None:
        layout = ProjectLayout(tmp_path / "demo")
        tts_dir = layout.tts_dir
        tts_dir.mkdir(parents=True, exist_ok=True)
        (tts_dir / "sound_style_profile.json").write_text("not valid json{{{", encoding="utf-8")
        profile = load_style_profile(layout)
        assert profile.profile_id == "default"
