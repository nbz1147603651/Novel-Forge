"""Tests for model profile capability helpers."""

from __future__ import annotations

from novel_forge.gateway.profiles import (
    canonical_model_id,
    get_model_capabilities,
    get_model_context_window,
    get_model_max_output_tokens,
    get_model_thinking_capability,
    normalize_provider_id,
)


def test_deepseek_v4_pro_alias_uses_canonical_limits() -> None:
    assert canonical_model_id("deepseekv4pro") == "deepseek-v4-pro"
    assert get_model_max_output_tokens("deepseekv4pro") == 384000


def test_deepseek_v4_pro_alias_uses_canonical_capabilities() -> None:
    assert get_model_capabilities("deepseek", "deepseekv4pro") == (True, True)


def test_provider_aliases_are_normalized() -> None:
    assert normalize_provider_id("DashScope") == "tongyi"
    assert normalize_provider_id("moonshot") == "kimi"
    assert normalize_provider_id("tencent_hunyuan") == "tencent"
    assert normalize_provider_id("Xiaomi_MiMo") == "mimo"


def test_case_insensitive_model_limits_cover_mixed_case_vendors() -> None:
    assert get_model_max_output_tokens("MiniMax-M3") == 65536
    assert canonical_model_id("minimax-m2.7-highspeed") == "MiniMax-M2.7-highspeed"
    assert get_model_max_output_tokens("minimax-m2.7-highspeed") == 65536
    assert get_model_capabilities("minimax", "minimax-m2.7-highspeed") == (True, True)


def test_current_vendor_model_limits_are_registered() -> None:
    assert get_model_max_output_tokens("gpt-5.4") == 128000
    assert get_model_max_output_tokens("claude-opus-4-7") == 128000
    assert get_model_max_output_tokens("kimi-k2.6") == 32768
    assert get_model_max_output_tokens("moonshot-v1-128k") == 131072
    assert get_model_max_output_tokens("qwen-plus-2025-09-11") == 32768
    assert get_model_max_output_tokens("hunyuan-t1") == 65536
    assert get_model_max_output_tokens("mimo-v2.5-pro") == 131072


def test_verified_registry_overrides_stale_legacy_context_values() -> None:
    assert get_model_context_window("MiniMax-M2.7-highspeed") == 204800
    assert get_model_context_window("gpt-5.6") == 1050000


def test_retired_model_never_enables_thinking_from_family_guess() -> None:
    assert get_model_capabilities("kimi", "kimi-k2-thinking") == (False, True)
    assert get_model_capabilities("hunyuan", "hunyuan-2.0-think") == (False, True)


def test_current_vendor_capabilities_are_registered() -> None:
    assert get_model_capabilities("openai", "gpt-5.4") == (True, True)
    assert get_model_capabilities("anthropic", "claude-sonnet-4-6") == (True, True)
    assert get_model_capabilities("moonshot", "kimi-k2.6") == (True, True)
    assert get_model_capabilities("dashscope", "qwen3-next-80b-a3b-instruct") == (
        False,
        True,
    )
    assert get_model_capabilities("hunyuan", "hy3") == (True, True)
    assert get_model_capabilities("xiaomi", "mimo-v2.5-pro") == (True, True)


def test_audited_reasoning_controls_preserve_provider_specific_modes() -> None:
    deepseek = get_model_thinking_capability("deepseek", "deepseek-v4-pro")
    minimax_m27 = get_model_thinking_capability("minimax", "MiniMax-M2.7-highspeed")
    minimax_m3 = get_model_thinking_capability("minimax", "MiniMax-M3")
    mimo = get_model_thinking_capability("xiaomi", "mimo-v2.5-pro")

    assert (deepseek.control, deepseek.modes, deepseek.default_mode) == (
        "effort",
        ("off", "high", "max"),
        "high",
    )
    assert (minimax_m27.control, minimax_m27.modes, minimax_m27.default_mode) == (
        "forced",
        ("forced",),
        "forced",
    )
    assert (minimax_m3.control, minimax_m3.modes, minimax_m3.default_mode) == (
        "adaptive",
        ("off", "adaptive"),
        "adaptive",
    )
    assert (mimo.control, mimo.modes, mimo.default_mode) == (
        "toggle",
        ("off", "on"),
        "on",
    )
