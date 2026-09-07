"""Provider-private synthesis controls stay isolated from generic TTS."""

from __future__ import annotations

from novel_forge.tts.platform.synthesis_capabilities import (
    native_synthesis_request_extension,
    provider_synthesis_capabilities,
)


def test_minimax_declares_its_verified_native_controls() -> None:
    capabilities = provider_synthesis_capabilities("minimax")

    assert capabilities.native_pause_syntax == "minimax_hash"
    assert capabilities.native_word_subtitles is True
    assert capabilities.force_cbr is True
    assert native_synthesis_request_extension(
        "minimax",
        word_subtitles_enabled=True,
        force_cbr_enabled=True,
    ) == {
        "subtitle_enable": True,
        "subtitle_type": "word",
        "force_cbr": True,
    }


def test_unknown_platform_receives_no_minimax_private_controls() -> None:
    capabilities = provider_synthesis_capabilities("future_provider")

    assert capabilities.uses_native_pause_timing is False
    assert native_synthesis_request_extension(
        "future_provider",
        word_subtitles_enabled=True,
        force_cbr_enabled=True,
    ) == {}
