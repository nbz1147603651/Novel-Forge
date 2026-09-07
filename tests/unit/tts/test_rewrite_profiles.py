"""Unit tests for the platform-aware spoken-text rewrite profiles.

Tests cover:
- Profile loading and deep-merge semantics
- Provider alias resolution
- Fallback to default.json
- mtime cache invalidation
"""

from __future__ import annotations

import pytest

from novel_forge.tts.platform.rewrite_profiles import (
    _deep_merge,
    _normalize_provider,
    invalidate_rewrite_cache,
    load_rewrite_profile,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Ensure each test starts with a clean cache."""
    invalidate_rewrite_cache()
    yield
    invalidate_rewrite_cache()


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
        base = {"limits": {"narration": 30, "dialogue": 20, "inner_thought": 25}}
        override = {"limits": {"narration": 25}}
        result = _deep_merge(base, override)
        assert result["limits"]["narration"] == 25
        assert result["limits"]["dialogue"] == 20
        assert result["limits"]["inner_thought"] == 25

    def test_list_replacement(self):
        base = {"rules": ["a", "b"]}
        override = {"rules": ["c"]}
        result = _deep_merge(base, override)
        assert result["rules"] == ["c"]

    def test_new_key_added(self):
        base = {"a": 1}
        override = {"b": 2}
        result = _deep_merge(base, override)
        assert result == {"a": 1, "b": 2}

    def test_nested_dict_merge(self):
        base = {"outer": {"inner_a": 1, "inner_b": 2}}
        override = {"outer": {"inner_a": 10, "inner_c": 3}}
        result = _deep_merge(base, override)
        assert result["outer"] == {"inner_a": 10, "inner_b": 2, "inner_c": 3}


class TestLoadRewriteProfile:
    def test_default_profile_loads(self):
        profile = load_rewrite_profile("")
        assert profile["provider_id"] == "default"
        assert "sentence_limits" in profile
        assert profile["sentence_limits"]["narration"] == 30

    def test_minimax_profile_loads(self):
        profile = load_rewrite_profile("minimax")
        assert profile["provider_id"] == "minimax"
        assert profile["display_name"] == "MiniMax Speech"
        # MiniMax has stricter sentence limits
        assert profile["sentence_limits"]["narration"] == 25
        assert profile["sentence_limits"]["dialogue"] == 18
        assert "MiniMax 专属" in profile["platform_prompt_guidance"]
        # Deep-merged: polyphone_strategy from default (empty) retained
        assert "polyphone_strategy" in profile

    def test_bailian_profile_loads(self):
        profile = load_rewrite_profile("bailian")
        assert profile["provider_id"] == "bailian"
        assert "百炼" in profile["display_name"]
        assert profile["sentence_limits"]["dialogue"] == 22
        assert profile["polyphone_strategy"] != ""
        assert "百炼专属" in profile["platform_prompt_guidance"]

    def test_alias_dashscope_resolves_to_bailian(self):
        profile = load_rewrite_profile("dashscope")
        assert profile["provider_id"] == "bailian"

    def test_alias_qwen3_resolves_to_bailian(self):
        profile = load_rewrite_profile("qwen3")
        assert profile["provider_id"] == "bailian"

    def test_unknown_provider_falls_back_to_default(self):
        profile = load_rewrite_profile("nonexistent_platform_xyz")
        assert profile["provider_id"] == "default"

    def test_minimax_special_rules_present(self):
        profile = load_rewrite_profile("minimax")
        assert isinstance(profile["special_rules"], list)
        assert len(profile["special_rules"]) >= 1

    def test_bailian_special_rules_present(self):
        profile = load_rewrite_profile("bailian")
        assert isinstance(profile["special_rules"], list)
        assert len(profile["special_rules"]) >= 1

    def test_default_has_empty_special_rules(self):
        profile = load_rewrite_profile("")
        assert profile["special_rules"] == []

    def test_all_profiles_have_required_keys(self):
        required_keys = {
            "provider_id",
            "profile_version",
            "display_name",
            "sentence_limits",
            "pause_strategy",
            "punctuation_guidance",
            "interjection_policy",
            "platform_prompt_guidance",
            "rhythm_guidance",
            "special_rules",
            "polyphone_strategy",
        }
        for provider in ("", "minimax", "bailian", "dashscope"):
            profile = load_rewrite_profile(provider)
            missing = required_keys - set(profile)
            assert not missing, f"Provider {provider!r} missing keys: {missing}"


class TestCacheInvalidation:
    def test_invalidate_specific_provider(self):
        # Load to populate cache
        load_rewrite_profile("minimax")
        # Invalidate specific
        invalidate_rewrite_cache("minimax")
        # Should reload without error
        profile = load_rewrite_profile("minimax")
        assert profile["provider_id"] == "minimax"

    def test_invalidate_all(self):
        load_rewrite_profile("minimax")
        load_rewrite_profile("bailian")
        invalidate_rewrite_cache()
        # Both should reload
        assert load_rewrite_profile("minimax")["provider_id"] == "minimax"
        assert load_rewrite_profile("bailian")["provider_id"] == "bailian"
