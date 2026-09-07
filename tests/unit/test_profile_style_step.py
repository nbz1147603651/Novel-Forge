"""Tests for ProfileStyleStep parsing of new genre parameter fields.

RED Phase: Tests are expected to FAIL because the parsing logic
for hook_config, strand_config, and micro_payoff_config has not been
implemented yet in ProfileStyleStep._execute().
"""

from __future__ import annotations

from novel_forge.core.schemas.style_profile import (
    GlobalStyleConfig,
    HookConfig,
    MicroPayoffConfig,
    ProjectStyleProfile,
    StrandConfig,
    validate_hook_strength,
    validate_hook_type,
    validate_pacing_mode,
)

# ── Mock LLM Response Data ───────────────────────────────────────────────────


MOCK_LLM_RESPONSE_FULL: dict = {
    "modules": [
        {
            "name": "悬念钩子",
            "rules": ["每章结尾设置悬念", "避免直接揭示答案"],
            "positive_example": "他推开门，却发现...",
            "negative_example": "答案就是他。",
        },
    ],
    "source_elements": ["story_bible", "character_bible"],
    "summary": "悬疑推理风格，强调悬念与线索管理",
    # New fields to be parsed
    "hook_config": {
        "preferred_types": ["crisis", "mystery"],
        "strength_baseline": "strong",
        "chapter_end_required": True,
    },
    "strand_config": {
        "quest_max_consecutive": 4,
        "fire_max_absent": 8,
        "constellation_max_absent": 12,
        "stagnation_threshold": 2,
    },
    "micro_payoff_config": {
        "preferred_types": ["information", "clue", "emotion"],
        "min_per_chapter": 2,
    },
    "cool_point_config": {
        "preferred_patterns": ["真相揭露", "巧妙推理"],
        "density_per_chapter": "high",
    },
    "global_style": {
        "dialogue_ratio": "low",
        "pace_mode": "slow",
        "emotional_style": "subtle",
        "environment_ratio": "high",
        "info_density": "medium",
        "banned_phrases": ["命运的齿轮开始转动"],
    },
}

MOCK_LLM_RESPONSE_INVALID_ENUMS: dict = {
    "modules": [],
    "source_elements": [],
    "summary": "",
    "hook_config": {
        "preferred_types": ["invalid_type", "CRISIS", "Mystery"],  # Mixed valid/invalid
        "strength_baseline": "invalid_strength",  # Invalid enum
        "chapter_end_required": True,
    },
    "strand_config": {
        "quest_max_consecutive": -1,  # Invalid (should be >= 1)
        "fire_max_absent": 0,  # Invalid (should be >= 1)
        "constellation_max_absent": 15,
        "stagnation_threshold": 3,
    },
    "micro_payoff_config": {
        "preferred_types": ["unknown_type"],
        "min_per_chapter": -5,  # Invalid (should be >= 0)
    },
    "global_style": {
        "dialogue_ratio": "balanced",
        "pace_mode": "normal",
        "emotional_style": "melodramatic",
        "environment_ratio": "dense",
        "info_density": "sparse",
    },
}

MOCK_LLM_RESPONSE_MINIMAL: dict = {
    "modules": [],
    "source_elements": [],
    "summary": "",
    # Missing hook_config, strand_config, micro_payoff_config
}


# ── Test: Hook Config Parsing ────────────────────────────────────────────────


class TestHookConfigParsing:
    """Tests for parsing hook_config field from LLM response."""

    def test_parse_hook_config_with_valid_data(self) -> None:
        """Should parse hook_config with valid enum values."""
        # RED: This test will fail because ProfileStyleStep doesn't parse hook_config yet
        profile = _parse_mock_response(MOCK_LLM_RESPONSE_FULL)

        assert hasattr(profile, "hook_config")
        assert isinstance(profile.hook_config, HookConfig)
        assert profile.hook_config.preferred_types == ["crisis", "mystery"]
        assert profile.hook_config.strength_baseline == "strong"
        assert profile.hook_config.chapter_end_required is True

    def test_parse_hook_config_with_invalid_enum_fallback(self) -> None:
        """Invalid hook types should be filtered; invalid strength should fallback to default."""
        # RED: This test will fail because parsing logic doesn't exist
        profile = _parse_mock_response(MOCK_LLM_RESPONSE_INVALID_ENUMS)

        assert hasattr(profile, "hook_config")
        # Invalid types should be filtered out, valid ones normalized
        # Expected: ["crisis", "mystery"] after normalization and filtering
        valid_types = [
            t
            for t in profile.hook_config.preferred_types
            if t in {"crisis", "mystery", "emotion", "choice", "desire", "none"}
        ]
        assert (
            len(valid_types) >= 2
        )  # At least CRISIS and Mystery should be valid after normalization
        # Invalid strength should fallback to "medium"
        assert profile.hook_config.strength_baseline == "medium"

    def test_parse_hook_config_missing_field_uses_defaults(self) -> None:
        """Missing hook_config should use HookConfig defaults."""
        # RED: This test will fail because parsing logic doesn't exist
        profile = _parse_mock_response(MOCK_LLM_RESPONSE_MINIMAL)

        assert hasattr(profile, "hook_config")
        assert isinstance(profile.hook_config, HookConfig)
        # Should use default values from HookConfig
        assert profile.hook_config.preferred_types == ["crisis", "mystery"]
        assert profile.hook_config.strength_baseline == "medium"
        assert profile.hook_config.chapter_end_required is True


# ── Test: Strand Config Parsing ───────────────────────────────────────────────


class TestStrandConfigParsing:
    """Tests for parsing strand_config field from LLM response."""

    def test_parse_strand_config_with_valid_data(self) -> None:
        """Should parse strand_config with valid integer values."""
        # RED: This test will fail because ProfileStyleStep doesn't parse strand_config yet
        profile = _parse_mock_response(MOCK_LLM_RESPONSE_FULL)

        assert hasattr(profile, "strand_config")
        assert isinstance(profile.strand_config, StrandConfig)
        assert profile.strand_config.quest_max_consecutive == 4
        assert profile.strand_config.fire_max_absent == 8
        assert profile.strand_config.constellation_max_absent == 12
        assert profile.strand_config.stagnation_threshold == 2

    def test_parse_strand_config_with_invalid_values_uses_defaults(self) -> None:
        """Invalid integer values (below minimum) should fallback to defaults."""
        # RED: This test will fail because parsing logic doesn't exist
        profile = _parse_mock_response(MOCK_LLM_RESPONSE_INVALID_ENUMS)

        assert hasattr(profile, "strand_config")
        # Invalid values should fallback to defaults (ge=1 constraints)
        assert profile.strand_config.quest_max_consecutive == 5  # Default
        assert profile.strand_config.fire_max_absent == 10  # Default
        assert profile.strand_config.constellation_max_absent == 15  # Valid, kept
        assert profile.strand_config.stagnation_threshold == 3  # Valid, kept

    def test_parse_strand_config_missing_field_uses_defaults(self) -> None:
        """Missing strand_config should use StrandConfig defaults."""
        # RED: This test will fail because parsing logic doesn't exist
        profile = _parse_mock_response(MOCK_LLM_RESPONSE_MINIMAL)

        assert hasattr(profile, "strand_config")
        assert isinstance(profile.strand_config, StrandConfig)
        # Should use default values from StrandConfig
        assert profile.strand_config.quest_max_consecutive == 5
        assert profile.strand_config.fire_max_absent == 10
        assert profile.strand_config.constellation_max_absent == 15
        assert profile.strand_config.stagnation_threshold == 3


# ── Test: Micro Payoff Config Parsing ─────────────────────────────────────────


class TestMicroPayoffConfigParsing:
    """Tests for parsing micro_payoff_config field from LLM response."""

    def test_parse_micro_payoff_config_with_valid_data(self) -> None:
        """Should parse micro_payoff_config with valid values."""
        # RED: This test will fail because ProfileStyleStep doesn't parse micro_payoff_config yet
        profile = _parse_mock_response(MOCK_LLM_RESPONSE_FULL)

        assert hasattr(profile, "micro_payoff_config")
        assert isinstance(profile.micro_payoff_config, MicroPayoffConfig)
        assert profile.micro_payoff_config.preferred_types == ["information", "clue", "emotion"]
        assert profile.micro_payoff_config.min_per_chapter == 2

    def test_parse_micro_payoff_config_with_invalid_values_uses_defaults(self) -> None:
        """Invalid min_per_chapter should fallback to default."""
        # RED: This test will fail because parsing logic doesn't exist
        profile = _parse_mock_response(MOCK_LLM_RESPONSE_INVALID_ENUMS)

        assert hasattr(profile, "micro_payoff_config")
        # Invalid min_per_chapter (-5) should fallback to default (1)
        assert profile.micro_payoff_config.min_per_chapter == 1

    def test_parse_micro_payoff_config_missing_field_uses_defaults(self) -> None:
        """Missing micro_payoff_config should use MicroPayoffConfig defaults."""
        # RED: This test will fail because parsing logic doesn't exist
        profile = _parse_mock_response(MOCK_LLM_RESPONSE_MINIMAL)

        assert hasattr(profile, "micro_payoff_config")
        assert isinstance(profile.micro_payoff_config, MicroPayoffConfig)
        # Should use default values from MicroPayoffConfig
        assert profile.micro_payoff_config.preferred_types == ["information", "clue"]
        assert profile.micro_payoff_config.min_per_chapter == 1


# ── Test: Global Style Parsing ────────────────────────────────────────────────


class TestGlobalStyleParsing:
    """Tests for parsing global_style field from LLM response."""

    def test_parse_global_style_with_valid_data(self) -> None:
        profile = _parse_mock_response(MOCK_LLM_RESPONSE_FULL)

        assert isinstance(profile.global_style, GlobalStyleConfig)
        assert profile.global_style.dialogue_ratio == "low"
        assert profile.global_style.pace_mode == "slow"
        assert profile.global_style.emotional_style == "subtle"
        assert profile.global_style.environment_ratio == "high"
        assert profile.global_style.info_density == "medium"
        assert profile.global_style.banned_phrases == ["命运的齿轮开始转动"]

    def test_parse_global_style_invalid_values_fallback(self) -> None:
        profile = _parse_mock_response(MOCK_LLM_RESPONSE_INVALID_ENUMS)

        assert profile.global_style.dialogue_ratio == "medium"
        assert profile.global_style.pace_mode == "moderate"
        assert profile.global_style.emotional_style == "balanced"
        assert profile.global_style.environment_ratio == "medium"
        assert profile.global_style.info_density == "medium"


# ── Test: Enum Validation Functions ───────────────────────────────────────────


class TestEnumValidationFunctions:
    """Tests for enum validation helper functions (Task 1.4)."""

    def test_validate_hook_type_valid_values(self) -> None:
        """Valid hook types should be returned normalized."""
        assert validate_hook_type("crisis") == "crisis"
        assert validate_hook_type("CRISIS") == "crisis"
        assert validate_hook_type("  mystery  ") == "mystery"
        assert validate_hook_type("Emotion") == "emotion"
        assert validate_hook_type("CHOICE") == "choice"
        assert validate_hook_type("desire") == "desire"

    def test_validate_hook_type_invalid_values_fallback(self) -> None:
        """Invalid hook types should fallback to 'none'."""
        assert validate_hook_type("invalid") == "none"
        assert validate_hook_type("unknown_type") == "none"
        assert validate_hook_type("") == "none"
        assert validate_hook_type("random") == "none"

    def test_validate_hook_strength_valid_values(self) -> None:
        """Valid hook strengths should be returned normalized."""
        assert validate_hook_strength("strong") == "strong"
        assert validate_hook_strength("STRONG") == "strong"
        assert validate_hook_strength("  medium  ") == "medium"
        assert validate_hook_strength("Weak") == "weak"

    def test_validate_hook_strength_invalid_values_fallback(self) -> None:
        """Invalid hook strengths should fallback to 'medium'."""
        assert validate_hook_strength("invalid") == "medium"
        assert validate_hook_strength("high") == "medium"
        assert validate_hook_strength("") == "medium"
        assert validate_hook_strength("low") == "medium"

    def test_validate_pacing_mode_valid_values(self) -> None:
        """Valid pacing modes should be returned normalized."""
        assert validate_pacing_mode("fast") == "fast"
        assert validate_pacing_mode("FAST") == "fast"
        assert validate_pacing_mode("  moderate  ") == "moderate"
        assert validate_pacing_mode("Slow") == "slow"

    def test_validate_pacing_mode_invalid_values_fallback(self) -> None:
        """Invalid pacing modes should fallback to 'moderate'."""
        assert validate_pacing_mode("invalid") == "moderate"
        assert validate_pacing_mode("medium") == "moderate"
        assert validate_pacing_mode("") == "moderate"
        assert validate_pacing_mode("normal") == "moderate"


# ── Helper Function (Mock Parser) ─────────────────────────────────────────────


def _parse_mock_response(data: dict) -> ProjectStyleProfile:
    """Parse mock LLM response into ProjectStyleProfile.

    Uses ProfileStyleStep._parse_response() for actual parsing logic.
    """
    from novel_forge.pipeline.steps.profile_style_step import ProfileStyleStep

    return ProfileStyleStep._parse_response(data)
