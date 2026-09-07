"""Unit tests for the declarative expression adapter layer."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from novel_forge.tts.platform.expression_adapter import (
    ExpressionAdapter,
    load_expression_adapter,
)
from novel_forge.tts.platform.expression_profiles import (
    invalidate_cache,
    load_profile_dict,
)

# ─── Profile Loading ──────────────────────────────────────────────────────────


class TestProfileLoading:
    def setup_method(self) -> None:
        invalidate_cache()

    def test_load_minimax_profile(self) -> None:
        data = load_profile_dict("minimax")
        assert data["provider_id"] == "minimax"
        assert "emotion_compensation" in data
        assert "tone_hint_rules" in data
        assert "vocal_direction_bridge" in data

    def test_load_unknown_provider_falls_back_to_default(self) -> None:
        data = load_profile_dict("nonexistent_provider_xyz")
        assert data["provider_id"] == "default"
        assert data["emotion_compensation"] == {}
        assert data["vocal_direction_bridge"]["enabled"] is False

    def test_load_empty_string_falls_back(self) -> None:
        data = load_profile_dict("")
        assert data["provider_id"] == "default"

    def test_cache_invalidation(self) -> None:
        data1 = load_profile_dict("minimax")
        invalidate_cache("minimax")
        data2 = load_profile_dict("minimax")
        assert data1 == data2  # Same content after reload

    def test_invalidate_all(self) -> None:
        load_profile_dict("minimax")
        load_profile_dict("default")
        invalidate_cache()
        # Should not raise
        data = load_profile_dict("minimax")
        assert data["provider_id"] == "minimax"

    def test_mtime_hot_reload(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify that modifying a JSON file on disk triggers automatic reload."""
        import novel_forge.tts.platform.expression_profiles as profiles_mod

        # Create a temporary provider profile
        profile_file = tmp_path / "testprov.json"
        original = {
            "provider_id": "testprov",
            "profile_version": "1.0",
            "emotion_compensation": {},
            "tone_hint_rules": [],
            "vocal_direction_bridge": {"enabled": False},
            "expression_scale": {
                "extreme_short": 0.0,
                "short_base": 0.10,
                "short_intensity_factor": 0.20,
                "normal_base": 0.15,
                "normal_intensity_factor": 0.35,
            },
            "sub_emotion_modulation": {"energy_deltas": {}, "tension_deltas": {}},
        }
        profile_file.write_text(json.dumps(original), encoding="utf-8")
        # Also need a default.json in the temp dir for fallback
        default_file = tmp_path / "default.json"
        default_file.write_text(
            json.dumps({"provider_id": "default", "profile_version": "1.0",
                        "emotion_compensation": {}, "tone_hint_rules": [],
                        "vocal_direction_bridge": {"enabled": False},
                        "expression_scale": {"extreme_short": 0.0, "short_base": 0.1,
                                             "short_intensity_factor": 0.2, "normal_base": 0.15,
                                             "normal_intensity_factor": 0.35},
                        "sub_emotion_modulation": {"energy_deltas": {}, "tension_deltas": {}}}),
            encoding="utf-8",
        )

        # Patch the profiles directory
        monkeypatch.setattr(profiles_mod, "_PROFILES_DIR", tmp_path)
        invalidate_cache()

        # First load
        data1 = load_profile_dict("testprov")
        assert data1["profile_version"] == "1.0"
        assert data1["emotion_compensation"] == {}

        # Modify the file on disk (ensure mtime changes)
        time.sleep(0.01)  # Ensure filesystem timestamp differs
        modified = dict(original)
        modified["profile_version"] = "2.0"
        modified["emotion_compensation"] = {
            "tender": {"projected_to": "calm", "speed_delta": -0.05, "vol_delta": -0.08,
                       "inject_tags": ["(breath)"]}
        }
        profile_file.write_text(json.dumps(modified), encoding="utf-8")
        # Force mtime_ns difference (some filesystems have coarse resolution)
        os.utime(profile_file, ns=(0, int(time.time() * 1e9) + 1_000_000_000))

        # Second load — should pick up changes WITHOUT invalidate_cache()
        data2 = load_profile_dict("testprov")
        assert data2["profile_version"] == "2.0"
        assert "tender" in data2["emotion_compensation"]
        assert data2["emotion_compensation"]["tender"]["projected_to"] == "calm"


# ─── ExpressionAdapter Factory ────────────────────────────────────────────────


class TestExpressionAdapterFactory:
    def setup_method(self) -> None:
        invalidate_cache()

    def test_load_minimax_adapter(self) -> None:
        adapter = load_expression_adapter("minimax")
        assert adapter.provider_id == "minimax"
        assert adapter.profile.vocal_direction_bridge.enabled is True

    def test_load_default_adapter(self) -> None:
        adapter = load_expression_adapter("unknown_provider")
        assert adapter.provider_id == "default"
        assert adapter.profile.vocal_direction_bridge.enabled is False

    def test_adapter_profile_version(self) -> None:
        adapter = load_expression_adapter("minimax")
        assert adapter.profile.profile_version == "1.0"


# ─── P0: Emotion Compensation ─────────────────────────────────────────────────


class TestEmotionCompensation:
    @pytest.fixture()
    def adapter(self) -> ExpressionAdapter:
        invalidate_cache()
        return load_expression_adapter("minimax")

    def test_tender_compensation(self, adapter: ExpressionAdapter) -> None:
        result = adapter.compensate_emotion("tender", intensity=1.0)
        assert result.compensated is True
        assert result.native_emotion == "calm"
        assert result.speed_delta < 0  # Slower
        assert result.vol_delta < 0  # Quieter
        assert "(breath)" in result.inject_tags

    def test_whisper_compensation(self, adapter: ExpressionAdapter) -> None:
        result = adapter.compensate_emotion("whisper", intensity=1.0)
        assert result.native_emotion == "calm"
        assert result.speed_delta < -0.05
        assert result.vol_delta < -0.10
        assert len(result.inject_tags) == 2

    def test_determined_compensation(self, adapter: ExpressionAdapter) -> None:
        result = adapter.compensate_emotion("determined", intensity=1.0)
        assert result.native_emotion == "angry"
        assert result.speed_delta < 0  # Reduce aggression
        assert result.vol_delta < 0

    def test_native_emotion_no_compensation(self, adapter: ExpressionAdapter) -> None:
        result = adapter.compensate_emotion("happy", intensity=1.0)
        assert result.compensated is False
        assert result.native_emotion == "happy"
        assert result.speed_delta == 0.0

    def test_extreme_short_zeroes_compensation(self, adapter: ExpressionAdapter) -> None:
        result = adapter.compensate_emotion("tender", intensity=1.0, is_extreme_short=True)
        assert result.speed_delta == 0.0
        assert result.vol_delta == 0.0
        assert result.inject_tags == []

    def test_short_halves_compensation(self, adapter: ExpressionAdapter) -> None:
        full = adapter.compensate_emotion("tender", intensity=1.0)
        short = adapter.compensate_emotion("tender", intensity=1.0, is_short=True)
        assert abs(short.speed_delta) < abs(full.speed_delta)

    def test_intensity_scales_compensation(self, adapter: ExpressionAdapter) -> None:
        low = adapter.compensate_emotion("tender", intensity=0.2)
        high = adapter.compensate_emotion("tender", intensity=1.0)
        assert abs(high.speed_delta) > abs(low.speed_delta)

    def test_default_profile_no_compensation(self) -> None:
        invalidate_cache()
        adapter = load_expression_adapter("unknown")
        result = adapter.compensate_emotion("tender", intensity=1.0)
        assert result.compensated is False


# ─── P1: Tone Hint Resolution ─────────────────────────────────────────────────


class TestToneHintResolution:
    @pytest.fixture()
    def adapter(self) -> ExpressionAdapter:
        invalidate_cache()
        return load_expression_adapter("minimax")

    def test_sarcasm_match(self, adapter: ExpressionAdapter) -> None:
        result = adapter.resolve_tone_hint("带有讽刺的语气")
        assert result.matched is True
        assert result.inject_prefix_tag == "(chuckle)"
        assert result.speed_delta > 0

    def test_plea_match(self, adapter: ExpressionAdapter) -> None:
        result = adapter.resolve_tone_hint("恳求")
        assert result.matched is True
        assert result.inject_prefix_tag == "(breath)"
        assert result.vol_delta < 0

    def test_command_match(self, adapter: ExpressionAdapter) -> None:
        result = adapter.resolve_tone_hint("命令式")
        assert result.matched is True
        assert result.vol_delta > 0
        assert result.speed_delta > 0

    def test_no_match(self, adapter: ExpressionAdapter) -> None:
        result = adapter.resolve_tone_hint("完全无关的描述")
        assert result.matched is False
        assert result.speed_delta == 0.0

    def test_empty_hint(self, adapter: ExpressionAdapter) -> None:
        result = adapter.resolve_tone_hint("")
        assert result.matched is False

    def test_default_profile_no_rules(self) -> None:
        invalidate_cache()
        adapter = load_expression_adapter("unknown")
        result = adapter.resolve_tone_hint("讽刺")
        assert result.matched is False


# ─── P2: VocalDirection Bridging ──────────────────────────────────────────────


class TestVocalDirectionBridge:
    @pytest.fixture()
    def adapter(self) -> ExpressionAdapter:
        invalidate_cache()
        return load_expression_adapter("minimax")

    def test_high_energy_bridge(self, adapter: ExpressionAdapter) -> None:
        result = adapter.bridge_vocal_direction(
            {"energy": 0.85},
            is_short=False,
            is_extreme_short=False,
            identity_lock=False,
        )
        assert result.applied is True
        assert result.speed_delta > 0
        assert result.vol_delta > 0

    def test_low_energy_bridge(self, adapter: ExpressionAdapter) -> None:
        result = adapter.bridge_vocal_direction(
            {"energy": 0.2},
            is_short=False,
            is_extreme_short=False,
            identity_lock=False,
        )
        assert result.applied is True
        assert result.speed_delta < 0
        assert result.vol_delta < 0

    def test_high_breathiness_injects_tag(self, adapter: ExpressionAdapter) -> None:
        result = adapter.bridge_vocal_direction(
            {"breathiness": 0.8},
            is_short=False,
            is_extreme_short=False,
            identity_lock=False,
        )
        assert "(breath)" in result.inject_tags

    def test_guard_short_utterance_skips(self, adapter: ExpressionAdapter) -> None:
        result = adapter.bridge_vocal_direction(
            {"energy": 0.9},
            is_short=True,
            is_extreme_short=False,
            identity_lock=False,
        )
        assert result.applied is False

    def test_guard_extreme_short_skips(self, adapter: ExpressionAdapter) -> None:
        result = adapter.bridge_vocal_direction(
            {"energy": 0.9},
            is_short=False,
            is_extreme_short=True,
            identity_lock=False,
        )
        assert result.applied is False

    def test_guard_identity_lock_skips(self, adapter: ExpressionAdapter) -> None:
        result = adapter.bridge_vocal_direction(
            {"energy": 0.9},
            is_short=False,
            is_extreme_short=False,
            identity_lock=True,
        )
        assert result.applied is False

    def test_clamp_limits_deltas(self, adapter: ExpressionAdapter) -> None:
        # Stack multiple high dimensions to exceed clamp
        result = adapter.bridge_vocal_direction(
            {"energy": 0.9, "tension": 0.9, "intimacy": 0.1},
            is_short=False,
            is_extreme_short=False,
            identity_lock=False,
        )
        assert abs(result.speed_delta) <= 0.05
        assert abs(result.vol_delta) <= 0.05

    def test_disabled_bridge_returns_not_applied(self) -> None:
        invalidate_cache()
        adapter = load_expression_adapter("unknown")
        result = adapter.bridge_vocal_direction(
            {"energy": 0.9},
            is_short=False,
            is_extreme_short=False,
            identity_lock=False,
        )
        assert result.applied is False

    def test_empty_direction_returns_not_applied(self, adapter: ExpressionAdapter) -> None:
        result = adapter.bridge_vocal_direction(
            {},
            is_short=False,
            is_extreme_short=False,
            identity_lock=False,
        )
        assert result.applied is False


# ─── Expression Scale ─────────────────────────────────────────────────────────


class TestExpressionScale:
    @pytest.fixture()
    def adapter(self) -> ExpressionAdapter:
        invalidate_cache()
        return load_expression_adapter("minimax")

    def test_extreme_short_is_zero(self, adapter: ExpressionAdapter) -> None:
        scale = adapter.resolve_expression_scale(1.0, is_extreme_short=True)
        assert scale == 0.0

    def test_short_formula(self, adapter: ExpressionAdapter) -> None:
        scale = adapter.resolve_expression_scale(0.5, is_short=True)
        # short_base + short_intensity_factor * 0.5 = 0.10 + 0.20 * 0.5 = 0.20
        assert abs(scale - 0.20) < 1e-6

    def test_normal_formula(self, adapter: ExpressionAdapter) -> None:
        scale = adapter.resolve_expression_scale(0.5)
        # normal_base + normal_intensity_factor * 0.5 = 0.15 + 0.35 * 0.5 = 0.325
        assert abs(scale - 0.325) < 1e-6

    def test_full_intensity_normal(self, adapter: ExpressionAdapter) -> None:
        scale = adapter.resolve_expression_scale(1.0)
        # 0.15 + 0.35 * 1.0 = 0.50
        assert abs(scale - 0.50) < 1e-6


# ─── Sub-Emotion Modulation ───────────────────────────────────────────────────


class TestSubEmotionModulation:
    @pytest.fixture()
    def adapter(self) -> ExpressionAdapter:
        invalidate_cache()
        return load_expression_adapter("minimax")

    def test_angry_sub_emotion(self, adapter: ExpressionAdapter) -> None:
        energy, tension = adapter.resolve_sub_emotion_delta("angry")
        assert energy > 0
        assert tension > 0

    def test_tender_sub_emotion(self, adapter: ExpressionAdapter) -> None:
        energy, tension = adapter.resolve_sub_emotion_delta("tender")
        assert energy < 0
        assert tension < 0

    def test_unknown_sub_emotion_returns_zero(self, adapter: ExpressionAdapter) -> None:
        energy, tension = adapter.resolve_sub_emotion_delta("nonexistent")
        assert energy == 0.0
        assert tension == 0.0

    def test_default_profile_returns_zero(self) -> None:
        invalidate_cache()
        adapter = load_expression_adapter("unknown")
        energy, tension = adapter.resolve_sub_emotion_delta("angry")
        assert energy == 0.0
        assert tension == 0.0
