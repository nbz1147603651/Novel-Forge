"""Tests for model capability profile validation."""

from __future__ import annotations

from novel_forge.gateway.model_capabilities import resolve_model_capability
from scripts.load_model_capability import load_profiles, validate_profiles


def test_repository_model_capability_profile_is_valid() -> None:
    profiles = load_profiles()

    assert validate_profiles(profiles) == []


def test_validate_profiles_rejects_incomplete_structured_output() -> None:
    errors = validate_profiles(
        {
            "version": "1.2.0",
            "model_families": [
                {
                    "id": "mock-family",
                    "provider": "mock",
                    "model_pattern": "*",
                    "availability": "active",
                    "verified_at": "2026-07-13",
                    "source_urls": ["https://example.com/models"],
                    "structured_output": {
                        "json_schema": "supported",
                        "json_mode": "supported",
                        "tool_calling": "supported",
                        "strict_schema": "supported",
                        "constrained_decoding": "supported",
                    },
                }
            ],
            "models": {
                "mock/model": {
                    "availability": "active",
                    "instruction_following": "good",
                    "structured_output_reliability": "high",
                    "context_window_tokens": 8192,
                    "max_output_tokens": 2048,
                    "supports_function_calling": False,
                    "verified_at": "2026-07-13",
                    "source_urls": ["https://example.com/model"],
                    "structured_output": {
                        "json_schema": "sometimes",
                        "json_mode": "supported",
                        "tool_calling": "unsupported",
                        "strict_schema": "unknown",
                    },
                }
            },
        }
    )

    assert any("structured_output.json_schema" in error for error in errors)
    assert any("structured_output.constrained_decoding" in error for error in errors)


def test_validate_profiles_rejects_inconsistent_thinking_controls() -> None:
    profiles = load_profiles()
    profiles["model_families"][0]["thinking_control"] = "effort"
    profiles["model_families"][0]["thinking_modes"] = ["off", "turbo"]
    profiles["model_families"][0]["default_thinking_mode"] = "medium"

    errors = validate_profiles(profiles)

    assert any("thinking_modes has invalid values" in error for error in errors)
    assert any("default_thinking_mode must occur" in error for error in errors)


def test_minimax_m27_highspeed_uses_verified_exact_override() -> None:
    record = resolve_model_capability("minimax", "MiniMax-M2.7-highspeed")

    assert record.availability == "active"
    assert record.context_window_tokens == 204800
    assert record.max_output_tokens == 65536
    assert record.supports_thinking is True
    assert record.supports_function_calling is True
    assert record.structured_output["json_schema"] == "unsupported"
    assert record.structured_output["json_mode"] == "unsupported"
    assert record.source_urls


def test_retired_exact_model_overrides_active_family_rule() -> None:
    record = resolve_model_capability("kimi", "kimi-k2-thinking")

    assert record.availability == "retired"
    assert "kimi-retired-k2-hyphen" in record.profile_match
    assert "kimi/kimi-k2-thinking" in record.profile_match
