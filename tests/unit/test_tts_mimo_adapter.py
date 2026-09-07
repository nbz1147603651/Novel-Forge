"""Tests for the Xiaomi MiMo V2.5 TTS adapter."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from novel_forge.core.exceptions import AuthenticationError
from novel_forge.tts.gateway.adapters.mimo_adapter import MiMoTTSAdapter
from novel_forge.tts.schemas import TTSProvider, TTSRequest


def test_mimo_requires_api_key() -> None:
    with pytest.raises(ValueError, match="API key"):
        MiMoTTSAdapter("")


async def test_mimo_synthesizes_wav_with_assistant_text_and_user_direction() -> None:
    adapter = MiMoTTSAdapter("test-key")
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "choices": [
            {"message": {"audio": {"data": base64.b64encode(b"RIFFfake-wav").decode("ascii")}}}
        ]
    }
    adapter._client.post = AsyncMock(return_value=response)

    result = await adapter.synthesize(
        TTSRequest(
            text="雨停了。",
            voice_id="冰糖",
            provider=TTSProvider.MIMO,
            output_format="mp3",
            emotion="sad",
            metadata={"tone_hint": "克制", "platform_extension": {"instruct": "低声述说"}},
        )
    )

    assert result.audio_data == b"RIFFfake-wav"
    assert result.audio_format == "wav"
    assert result.voice_id == "冰糖"
    call = adapter._client.post.await_args
    assert call.kwargs["headers"]["api-key"] == "test-key"
    payload = call.kwargs["json"]
    assert payload["model"] == "mimo-v2.5-tts"
    assert payload["messages"][-1] == {"role": "assistant", "content": "雨停了。"}
    assert payload["messages"][0]["role"] == "user"
    assert "低声述说" in payload["messages"][0]["content"]
    assert payload["audio"] == {"format": "wav", "voice": "冰糖"}


async def test_mimo_rejects_retired_or_specialized_model_for_formal_route() -> None:
    adapter = MiMoTTSAdapter("test-key")
    with pytest.raises(ValueError, match="formal synthesis"):
        await adapter.synthesize(
            TTSRequest(
                text="测试",
                model_id="mimo-v2-tts",
                provider=TTSProvider.MIMO,
            )
        )


async def test_mimo_maps_authentication_failure() -> None:
    adapter = MiMoTTSAdapter("bad-key")
    response = MagicMock(status_code=401, text="invalid api key")
    adapter._client.post = AsyncMock(return_value=response)

    with pytest.raises(AuthenticationError):
        await adapter.synthesize(TTSRequest(text="测试", provider=TTSProvider.MIMO))


async def test_mimo_lists_official_v25_voices() -> None:
    adapter = MiMoTTSAdapter("test-key")
    voices = await adapter.list_system_voices(language="zh")
    assert {item["voice_id"] for item in voices} >= {"冰糖", "茉莉", "苏打", "白桦"}
