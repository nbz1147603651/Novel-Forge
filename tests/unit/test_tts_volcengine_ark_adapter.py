"""Tests for the Volcengine Ark Agent Plan / Doubao Seed TTS 2.0 adapter."""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from novel_forge.tts.gateway.adapters.volcengine_ark_adapter import VolcengineArkTTSAdapter
from novel_forge.tts.schemas import TTSProvider, TTSRequest


def test_volcengine_requires_agent_plan_key() -> None:
    with pytest.raises(ValueError, match="Agent Plan API key"):
        VolcengineArkTTSAdapter(api_key="")


async def test_volcengine_decodes_concatenated_chunked_json_and_maps_controls() -> None:
    adapter = VolcengineArkTTSAdapter(api_key="speech-key")
    first = json.dumps({"code": 0, "data": base64.b64encode(b"part-1").decode("ascii")})
    second = json.dumps(
        {
            "code": 0,
            "data": base64.b64encode(b"part-2").decode("ascii"),
            "addition": {"duration": "820"},
        }
    )
    terminal = json.dumps({"code": 20000000, "message": "OK", "usage": {"text_words": 4}})
    response = MagicMock(
        status_code=200,
        text=first + second + terminal,
        headers={"X-Tt-Logid": "log-1"},
    )
    adapter._client.post = AsyncMock(return_value=response)

    result = await adapter.synthesize(
        TTSRequest(
            text="你好，世界",
            voice_id="zh_female_vv_uranus_bigtts",
            provider=TTSProvider.VOLCENGINE_ARK,
            speed=1.2,
            volume=0.8,
            pitch=2,
            sample_rate=24000,
            metadata={
                "performance_emotion": "happy",
                "tone_hint": "轻快",
                "platform_extension": {"instruct": "像讲故事一样"},
                "language_code": "zh-CN",
            },
        )
    )

    assert result.audio_data == b"part-1part-2"
    assert result.duration_ms == 820
    assert result.audio_format == "mp3"
    assert result.model_id == "doubao-seed-tts-2.0"
    assert result.metadata["log_id"] == "log-1"
    call = adapter._client.post.await_args
    assert call.args[0] == ("https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional")
    assert call.kwargs["headers"]["X-Api-Key"] == "speech-key"
    assert call.kwargs["headers"]["X-Api-Resource-Id"] == "seed-tts-2.0"
    params = call.kwargs["json"]["req_params"]
    assert params["audio_params"]["speech_rate"] == 20
    assert params["audio_params"]["loudness_rate"] == -20
    assert params["audio_params"]["bit_rate"] == 128000
    additions = json.loads(params["additions"])
    assert additions["context_texts"] == ["像讲故事一样；情绪：happy；语气：轻快"]
    assert additions["post_process"] == {"pitch": 2}
    assert additions["explicit_language"] == "zh-cn"


def test_volcengine_agent_plan_headers_and_stream_decoder() -> None:
    adapter = VolcengineArkTTSAdapter(api_key="agent-plan-key")
    headers = adapter._headers()
    assert headers["X-Api-Key"] == "agent-plan-key"
    assert headers["X-Api-Resource-Id"] == "seed-tts-2.0"
    assert headers["X-Control-Require-Usage-Tokens-Return"] == "*"
    assert adapter._decode_events('data: {"code":0}\n data: {"code":20000000}') == [
        {"code": 0},
        {"code": 20000000},
    ]


def test_volcengine_container_formats_fall_back_to_valid_chunked_mp3() -> None:
    assert VolcengineArkTTSAdapter._output_format("flac") == "mp3"
    assert VolcengineArkTTSAdapter._output_format("wav") == "mp3"
    assert VolcengineArkTTSAdapter._ratio_scale(0.5) == -50
    assert VolcengineArkTTSAdapter._ratio_scale(2.0) == 100


async def test_volcengine_catalog_exposes_seed_2_novel_and_english_voices() -> None:
    adapter = VolcengineArkTTSAdapter(api_key="agent-plan-key")

    chinese_male = await adapter.list_system_voices(gender="male", language="zh-CN")
    english = await adapter.list_system_voices(language="en-US")

    assert any(item["voice_id"] == "zh_male_xuanyijieshuo_uranus_bigtts" for item in chinese_male)
    assert {item["voice_id"] for item in english} == {
        "en_male_tim_uranus_bigtts",
        "en_female_dacey_uranus_bigtts",
    }
    await adapter.shutdown()
