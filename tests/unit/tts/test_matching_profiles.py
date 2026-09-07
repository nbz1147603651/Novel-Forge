"""Unit tests for the platform-aware voice matching profiles.

Tests cover:
- Profile loading and deep-merge semantics
- Provider alias resolution (dashscope/qwen3/cosyvoice -> bailian)
- Fallback to default.json
- mtime cache invalidation
- Required keys present in all profiles
"""

from __future__ import annotations

import pytest

from novel_forge.tts.platform.matching_profiles import (
    _deep_merge,
    _normalize_provider,
    invalidate_matching_cache,
    load_matching_profile,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Ensure each test starts with a clean cache."""
    invalidate_matching_cache()
    yield
    invalidate_matching_cache()


class TestNormalizeProvider:
    def test_empty_string(self):
        assert _normalize_provider("") == ""

    def test_minimax_passthrough(self):
        assert _normalize_provider("minimax") == "minimax"

    def test_bailian_passthrough(self):
        assert _normalize_provider("bailian") == "bailian"

    def test_dashscope_alias(self):
        assert _normalize_provider("dashscope") == "bailian"

    def test_qwen3_alias(self):
        assert _normalize_provider("qwen3") == "bailian"

    def test_cosyvoice_alias(self):
        assert _normalize_provider("cosyvoice") == "bailian"

    def test_case_insensitive(self):
        assert _normalize_provider("MiniMax") == "minimax"
        assert _normalize_provider("DASHSCOPE") == "bailian"

    def test_hyphen_normalized(self):
        assert _normalize_provider("some-provider") == "some_provider"


class TestDeepMerge:
    def test_scalar_override(self):
        base = {"a": "hello", "b": 1}
        override = {"a": "world"}
        result = _deep_merge(base, override)
        assert result["a"] == "world"
        assert result["b"] == 1

    def test_dict_per_key_merge(self):
        base = {"scoring_weights": {"gender": 0.30, "age": 0.20, "expressive": 0.25}}
        override = {"scoring_weights": {"expressive": 0.30}}
        result = _deep_merge(base, override)
        assert result["scoring_weights"]["expressive"] == 0.30
        assert result["scoring_weights"]["gender"] == 0.30
        assert result["scoring_weights"]["age"] == 0.20

    def test_list_replacement(self):
        base = {"caution_notes": ["a", "b"]}
        override = {"caution_notes": ["c"]}
        result = _deep_merge(base, override)
        assert result["caution_notes"] == ["c"]

    def test_new_key_added(self):
        base = {"a": 1}
        override = {"b": 2}
        result = _deep_merge(base, override)
        assert result == {"a": 1, "b": 2}

    def test_nested_dict_merge(self):
        base = {"adjudication_guidance": {"platform_context": "", "selection_criteria": "default"}}
        override = {"adjudication_guidance": {"platform_context": "百炼平台"}}
        result = _deep_merge(base, override)
        assert result["adjudication_guidance"]["platform_context"] == "百炼平台"
        assert result["adjudication_guidance"]["selection_criteria"] == "default"


class TestLoadMatchingProfile:
    def test_default_profile_loads(self):
        profile = load_matching_profile("")
        assert profile["provider_id"] == "default"
        assert "scoring_weights" in profile
        assert profile["scoring_weights"]["gender"] == 0.30

    def test_minimax_profile_loads(self):
        profile = load_matching_profile("minimax")
        assert profile["provider_id"] == "minimax"
        assert profile["display_name"] == "MiniMax Speech"
        # MiniMax overrides expressive weight
        assert profile["scoring_weights"]["expressive"] == 0.25
        assert profile["scoring_weights"]["role"] == 0.05
        # Deep-merged: gender from default retained
        assert profile["scoring_weights"]["gender"] == 0.30

    def test_bailian_profile_loads(self):
        profile = load_matching_profile("bailian")
        assert profile["provider_id"] == "bailian"
        assert "百炼" in profile["display_name"]
        # Bailian overrides expressive and role weights
        assert profile["scoring_weights"]["expressive"] == 0.30
        assert profile["scoring_weights"]["role"] == 0.15
        # Deep-merged: gender from default retained
        assert profile["scoring_weights"]["gender"] == 0.30

    def test_bailian_has_expressive_group_overrides(self):
        profile = load_matching_profile("bailian")
        overrides = profile["expressive_group_overrides"]
        assert "沉稳克制" in overrides
        assert "温柔亲和" in overrides
        assert isinstance(overrides["沉稳克制"], list)

    def test_bailian_adjudication_guidance(self):
        profile = load_matching_profile("bailian")
        guidance = profile["adjudication_guidance"]
        assert guidance["platform_context"] != ""
        assert "trait" in guidance["voice_metadata_hint"]
        assert len(guidance["caution_notes"]) >= 1

    def test_minimax_adjudication_guidance(self):
        profile = load_matching_profile("minimax")
        guidance = profile["adjudication_guidance"]
        assert "voice_name" in guidance["platform_context"]
        assert len(guidance["caution_notes"]) >= 1

    def test_alias_dashscope_resolves_to_bailian(self):
        profile = load_matching_profile("dashscope")
        assert profile["provider_id"] == "bailian"

    def test_alias_qwen3_resolves_to_bailian(self):
        profile = load_matching_profile("qwen3")
        assert profile["provider_id"] == "bailian"

    def test_alias_cosyvoice_resolves_to_bailian(self):
        profile = load_matching_profile("cosyvoice")
        assert profile["provider_id"] == "bailian"

    def test_unknown_provider_falls_back_to_default(self):
        profile = load_matching_profile("nonexistent_platform_xyz")
        assert profile["provider_id"] == "default"

    def test_all_profiles_have_required_keys(self):
        required_keys = {
            "provider_id",
            "profile_version",
            "display_name",
            "scoring_weights",
            "expressive_group_overrides",
            "adjudication_guidance",
        }
        for provider in ("", "minimax", "bailian", "dashscope"):
            profile = load_matching_profile(provider)
            missing = required_keys - set(profile)
            assert not missing, f"Provider {provider!r} missing keys: {missing}"

    def test_adjudication_guidance_required_keys(self):
        guidance_keys = {"platform_context", "voice_metadata_hint", "selection_criteria", "caution_notes"}
        for provider in ("", "minimax", "bailian"):
            profile = load_matching_profile(provider)
            guidance = profile["adjudication_guidance"]
            missing = guidance_keys - set(guidance)
            assert not missing, f"Provider {provider!r} guidance missing keys: {missing}"


class TestCacheInvalidation:
    def test_invalidate_specific_provider(self):
        load_matching_profile("minimax")
        invalidate_matching_cache("minimax")
        profile = load_matching_profile("minimax")
        assert profile["provider_id"] == "minimax"

    def test_invalidate_all(self):
        load_matching_profile("minimax")
        load_matching_profile("bailian")
        invalidate_matching_cache()
        assert load_matching_profile("minimax")["provider_id"] == "minimax"
        assert load_matching_profile("bailian")["provider_id"] == "bailian"
