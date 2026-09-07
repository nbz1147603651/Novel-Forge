"""Shared chapter runtime policy presets and settings persistence."""

from __future__ import annotations

from novel_forge.app_service.chapter_runtime_policy import (
    chapter_runtime_policy_creation_parameters,
    normalize_chapter_runtime_policy,
    project_chapter_runtime_policy,
)
from novel_forge.core.config import Settings


def test_default_settings_project_as_compat_without_enabling_features() -> None:
    view = project_chapter_runtime_policy(Settings(_env_file=None))

    assert view["preset"] == "compat"
    assert view["fact_refresh_enabled"] is False
    assert view["inspiration_enabled"] is False
    assert view["short_adaptive_revision_enabled"] is False
    assert view["long_single_final_verify_enabled"] is False
    assert [option["id"] for option in view["preset_options"]] == [
        "compat",
        "safe",
        "balanced",
        "enhanced",
    ]


def test_balanced_preset_is_applied_atomically_to_allowlisted_parameters() -> None:
    params = chapter_runtime_policy_creation_parameters({"preset": "balanced"})

    assert params == {
        "chapter-intent-guard-mode": "block",
        "chapter-research-refresh-enabled": "true",
        "chapter-research-inspiration-enabled": "true",
        "chapter-research-inspiration-cooldown": "3",
        "short-adaptive-revision-enabled": "true",
        "long-single-final-verify-enabled": "true",
    }


def test_custom_policy_validates_cooldown_and_detects_known_shape() -> None:
    normalized = normalize_chapter_runtime_policy(
        {
            "preset": "custom",
            "intent_guard_mode": "block",
            "fact_refresh_enabled": True,
            "inspiration_enabled": False,
            "inspiration_cooldown": 3,
            "short_adaptive_revision_enabled": True,
            "long_single_final_verify_enabled": True,
        }
    )

    assert normalized["preset"] == "safe"
