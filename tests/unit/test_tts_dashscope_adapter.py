"""Tests for the current Alibaba Model Studio TTS HTTP adapter."""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from novel_forge.tts.gateway.adapters.dashscope_adapter import (
    DashScopeTTSAdapter,
    _normalize_finish_reason,
)
from novel_forge.tts.schemas import (
    TTSFeature,
    TTSProvider,
    TTSRequest,
    VoiceCloneRequest,
    VoiceCloneStatus,
    VoiceDesignRequest,
)


class TestDashScopeAdapterInit:
    def test_requires_api_key(self) -> None:
        with pytest.raises(ValueError, match="API key"):
            DashScopeTTSAdapter("")

    async def test_current_defaults_and_capabilities(self) -> None:
        adapter = DashScopeTTSAdapter("test-key")
        try:
            assert adapter.provider_name == "dashscope"
            assert adapter.provider_type == TTSProvider.BAILIAN
            assert adapter._default_model == "qwen-audio-3.0-tts-plus"
            assert adapter.capabilities.voice_clone is True
            assert adapter.capabilities.voice_design is True
            assert TTSFeature.INSTRUCTION_CONTROL in adapter.capabilities.synthesis_features
            assert TTSFeature.PARALINGUISTIC in adapter.capabilities.synthesis_features
        finally:
            await adapter.shutdown()


async def test_synthesize_uses_current_http_contract_and_downloads_audio() -> None:
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        assert request.url.path.endswith("/services/audio/tts/SpeechSynthesizer")
        return httpx.Response(
            200,
            json={
                "request_id": "req-1",
                "output": {
                    "finish_reason": "stop",
                    "audio": {"url": "https://audio.test/take.wav", "id": "audio-1"},
                },
                "usage": {"characters": 4},
            },
        )

    async def download_handler(request: httpx.Request) -> httpx.Response:
        assert "authorization" not in {k.lower() for k in request.headers}
        return httpx.Response(200, content=b"wave-bytes")

    client = httpx.AsyncClient(
        base_url="https://dashscope.aliyuncs.com/api/v1/",
        transport=httpx.MockTransport(handler),
    )
    download_client = httpx.AsyncClient(
        transport=httpx.MockTransport(download_handler),
    )
    adapter = DashScopeTTSAdapter("test-key", client=client, download_client=download_client)
    try:
        response = await adapter.synthesize(
            TTSRequest(
                text="别过来。",
                voice_id="longanlufeng",
                provider=TTSProvider.BAILIAN,
                emotion="tense",
                output_format="flac",
                sample_rate=32000,
                language_boost="zh-CN",
                metadata={
                    "speaker_kind": "角色",
                    "character_name": "沈舟",
                    "source_text": "别过来。",
                    "tone_hint": "压低声音，克制恐惧",
                    "vocal_direction": {
                        "intent": "警告对方立即停下",
                        "delivery_style": "dramatic",
                        "energy": 0.76,
                        "articulation": 0.82,
                        "tension": 0.78,
                        "intimacy": 0.72,
                    },
                    "stress_words": ["别", "过来"],
                    "paralinguistic_tags": [{"tag_type": "sigh", "position": 0.5}],
                    "performance_context": {
                        "previous_speaker": "林夏",
                        "previous_text": "你到底看见了什么？",
                    },
                },
            )
        )
    finally:
        await client.aclose()
        await download_client.aclose()

    request_input = captured["input"]
    assert captured["model"] == "qwen-audio-3.0-tts-plus"
    assert request_input["voice"] == "longanlufeng"
    assert request_input["format"] == "wav"
    assert request_input["sample_rate"] == 24000
    assert request_input["language_hints"] == ["zh"]
    assert request_input["text"].startswith("[panicked]")
    assert "[sighing]" in request_input["text"]
    assert "自然承接对话" in request_input["instruction"]
    assert "短句连贯说完" in request_input["instruction"]
    assert "你到底看见了什么" not in request_input["instruction"]
    assert "行动意图" in request_input["instruction"]
    assert response.audio_data == b"wave-bytes"
    assert response.model_id == "qwen-audio-3.0-tts-plus"
    assert response.voice_id == "longanlufeng"
    assert response.metadata["request_id"] == "req-1"
    assert response.cost_usd > 0
    assert response.metadata["estimated_cost_cny"] > 0


async def test_qwen3_instruct_uses_multimodal_contract() -> None:
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        assert request.url.path.endswith("/services/aigc/multimodal-generation/generation")
        return httpx.Response(
            200,
            json={
                "request_id": "qwen3-1",
                "output": {"audio": {"url": "https://audio.test/qwen3.wav"}},
                "usage": {"characters": 6},
            },
        )

    async def download_handler(request: httpx.Request) -> httpx.Response:
        assert "authorization" not in {k.lower() for k in request.headers}
        return httpx.Response(200, content=b"qwen3-wave")

    client = httpx.AsyncClient(
        base_url="https://dashscope.aliyuncs.com/api/v1/",
        transport=httpx.MockTransport(handler),
    )
    download_client = httpx.AsyncClient(
        transport=httpx.MockTransport(download_handler),
    )
    adapter = DashScopeTTSAdapter(
        "test-key", default_model="qwen3-tts-instruct-flash", client=client,
        download_client=download_client,
    )
    try:
        response = await adapter.synthesize(
            TTSRequest(
                text="雨终于停了。",
                voice_id="Ethan",
                provider=TTSProvider.BAILIAN,
                language_boost="zh-CN",
                emotion="calm",
                speed=0.9,
                metadata={
                    "tone_hint": "轻声、有释然感",
                    "speaker_kind": "旁白",
                    "narrator_distance": "close",
                    "stress_words": ["终于"],
                    "vocal_direction": {
                        "intent": "让听者感到紧绷后的松弛",
                        "delivery_style": "intimate",
                        "articulation": 0.8,
                        "breathiness": 0.6,
                        "intimacy": 0.85,
                    },
                },
            )
        )
    finally:
        await client.aclose()
        await download_client.aclose()

    assert captured["model"] == "qwen3-tts-instruct-flash"
    assert captured["input"]["language_type"] == "Chinese"
    assert captured["input"]["voice"] == "Ethan"
    assert "轻声、有释然感" in captured["input"]["instructions"]
    assert "行动意图" in captured["input"]["instructions"]
    assert "重读“终于”" in captured["input"]["instructions"]
    assert "近距离旁白" in captured["input"]["instructions"]
    assert captured["input"]["optimize_instructions"] is True
    assert "rate" not in captured["input"]
    assert response.audio_data == b"qwen3-wave"
    assert response.metadata["api_family"] == "qwen3_multimodal_generation"
    assert response.metadata["unsupported_portable_controls"] == ["speed"]


async def test_qwen3_clone_accepts_local_audio(tmp_path: Any) -> None:
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"RIFF-reference-audio")
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "output": {
                    "voice": "qwen3-tts-vc-2026-01-22-hero-abc",
                    "target_model": "qwen3-tts-vc-2026-01-22",
                }
            },
        )

    client = httpx.AsyncClient(
        base_url="https://dashscope.aliyuncs.com/api/v1/",
        transport=httpx.MockTransport(handler),
    )
    adapter = DashScopeTTSAdapter(
        "test-key", voice_clone_model="qwen3-tts-vc-2026-01-22", client=client
    )
    try:
        assert adapter.capabilities.local_reference_audio is True
        assert await adapter.prepare_clone_source(str(reference)) == str(reference)
        response = await adapter.clone_voice(
            VoiceCloneRequest(
                voice_id="hero_voice",
                file_id=str(reference),
                reference_transcript="我会守住这里。",
                authorized=True,
                provider=TTSProvider.BAILIAN,
            )
        )
    finally:
        await client.aclose()

    assert captured["model"] == "qwen-voice-enrollment"
    assert captured["input"]["action"] == "create"
    assert captured["input"]["preferred_name"] == "hero_voice"
    assert captured["input"]["audio"]["data"].startswith("data:audio/wav;base64,")
    assert captured["input"]["text"] == "我会守住这里。"
    assert response.status == VoiceCloneStatus.READY
    assert response.model_id == "qwen3-tts-vc-2026-01-22"


async def test_qwen3_design_uses_qwen_voice_design_contract() -> None:
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "output": {
                    "voice": "qwen3-tts-vd-2026-01-26-vd-nf-abc",
                    "target_model": "qwen3-tts-vd-2026-01-26",
                    "preview_audio": {
                        "data": base64.b64encode(b"qwen-design-preview").decode(),
                        "response_format": "wav",
                    },
                }
            },
        )

    client = httpx.AsyncClient(
        base_url="https://dashscope.aliyuncs.com/api/v1/",
        transport=httpx.MockTransport(handler),
    )
    adapter = DashScopeTTSAdapter(
        "test-key", voice_design_model="qwen3-tts-vd-2026-01-26", client=client
    )
    try:
        response = await adapter.design_voice(
            VoiceDesignRequest(
                description="沉稳而克制的中年男声，字音清晰",
                preview_text="这个故事，得从那场雨说起。",
                provider=TTSProvider.BAILIAN,
            )
        )
    finally:
        await client.aclose()

    assert captured["model"] == "qwen-voice-design"
    assert captured["input"]["action"] == "create"
    assert captured["input"]["preferred_name"].startswith("nf")
    assert response.status == VoiceCloneStatus.READY
    assert response.preview_audio_data == b"qwen-design-preview"


async def test_model_voice_mismatch_and_old_voice_are_rejected() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(500)))
    adapter = DashScopeTTSAdapter("test-key", client=client)
    try:
        with pytest.raises(ValueError, match="不属于模型"):
            await adapter.synthesize(
                TTSRequest(
                    text="测试",
                    model_id="cosyvoice-v3-flash",
                    voice_id="longanlingxin",
                    provider=TTSProvider.BAILIAN,
                )
            )
        with pytest.raises(ValueError, match="旧版 CosyVoice"):
            await adapter.synthesize(
                TTSRequest(
                    text="测试",
                    voice_id="longxiaochun",
                    provider=TTSProvider.BAILIAN,
                )
            )
    finally:
        await client.aclose()


async def test_voice_design_persists_target_model_and_preview() -> None:
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "request_id": "design-1",
                "output": {
                    "voice_id": "cosyvoice-v3.5-plus-vd-nf1234-abcd",
                    "target_model": "cosyvoice-v3.5-plus",
                    "preview_audio": {
                        "data": base64.b64encode(b"preview").decode(),
                        "sample_rate": 24000,
                        "response_format": "wav",
                    },
                },
            },
        )

    client = httpx.AsyncClient(
        base_url="https://dashscope.aliyuncs.com/api/v1/",
        transport=httpx.MockTransport(handler),
    )
    adapter = DashScopeTTSAdapter("test-key", client=client)
    try:
        response = await adapter.design_voice(
            VoiceDesignRequest(
                description="克制、温暖的青年男声",
                preview_text="我知道你在担心什么。",
                language="zh-CN",
                provider=TTSProvider.BAILIAN,
            )
        )
    finally:
        await client.aclose()

    assert captured["model"] == "voice-enrollment"
    assert captured["input"]["target_model"] == "cosyvoice-v3.5-plus"
    assert captured["input"]["action"] == "create_voice"
    assert response.status == VoiceCloneStatus.READY
    assert response.model_id == "cosyvoice-v3.5-plus"
    assert response.preview_audio_data == b"preview"


async def test_voice_clone_requires_authorization_and_public_url() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(500)))
    adapter = DashScopeTTSAdapter("test-key", client=client)
    try:
        unauthorized = await adapter.clone_voice(
            VoiceCloneRequest(
                voice_id="hero",
                file_id="https://audio.test/ref.wav",
                provider=TTSProvider.BAILIAN,
            )
        )
        local_path = await adapter.clone_voice(
            VoiceCloneRequest(
                voice_id="hero",
                file_id="/tmp/ref.wav",
                authorized=True,
                provider=TTSProvider.BAILIAN,
            )
        )
    finally:
        await client.aclose()

    assert unauthorized.status == VoiceCloneStatus.FAILED
    assert "授权" in unauthorized.message
    assert local_path.status == VoiceCloneStatus.FAILED
    assert "公网" in local_path.message


async def test_list_system_voices_is_model_scoped() -> None:
    adapter = DashScopeTTSAdapter("test-key")
    try:
        voices = await adapter.list_system_voices(gender="female", language="zh-CN")
    finally:
        await adapter.shutdown()

    assert voices
    assert all(item["model_id"] == "qwen-audio-3.0-tts-plus" for item in voices)
    assert all(item["gender"] == "female" for item in voices)
    assert "longxiaochun" not in {item["voice_id"] for item in voices}


def test_empty_text_is_rejected_by_schema() -> None:
    with pytest.raises(ValidationError):
        TTSRequest(text="", voice_id="longanlingxin", provider=TTSProvider.BAILIAN)


# ── finish_reason normalization (P0 regression: DashScope "stop" = success) ──


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("stop", "completed"),   # official success status
        ("length", "truncated"), # length-limited (abnormal)
        ("null", ""),            # streaming intermediate
        ("", ""),                # absent
        ("error", "error"),      # unknown values pass through unchanged
    ],
)
def test_normalize_finish_reason_mapping(raw: str, expected: str) -> None:
    assert _normalize_finish_reason(raw) == expected


async def _synthesize_with_finish_reason(
    finish_reason: str,
    *,
    model: str = "qwen-audio-3.0-tts-plus",
    voice: str = "longanlufeng",
) -> Any:
    """Run one mocked synthesis and return the TTSResponse."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "request_id": "req-fr",
                "output": {
                    "finish_reason": finish_reason,
                    "audio": {"url": "https://audio.test/fr.wav"},
                },
                "usage": {"characters": 4},
            },
        )

    async def download_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"fr-wave")

    client = httpx.AsyncClient(
        base_url="https://dashscope.aliyuncs.com/api/v1/",
        transport=httpx.MockTransport(handler),
    )
    download_client = httpx.AsyncClient(transport=httpx.MockTransport(download_handler))
    adapter = DashScopeTTSAdapter(
        "test-key", default_model=model, client=client, download_client=download_client
    )
    try:
        return await adapter.synthesize(
            TTSRequest(text="你好。", voice_id=voice, provider=TTSProvider.BAILIAN)
        )
    finally:
        await client.aclose()
        await download_client.aclose()


async def test_finish_reason_stop_maps_to_completed() -> None:
    """Regression: DashScope finish_reason="stop" is the official SUCCESS status.

    Before the fix the raw "stop" leaked into provider_status and the segment
    quality gate rejected every successful take (100% synthesis failure rate).
    """
    response = await _synthesize_with_finish_reason("stop")
    assert response.take_evidence.provider_status == "completed"


async def test_finish_reason_length_maps_to_truncated() -> None:
    response = await _synthesize_with_finish_reason("length")
    assert response.take_evidence.provider_status == "truncated"


async def test_qwen3_finish_reason_stop_maps_to_completed() -> None:
    """The qwen3 multimodal-generation path must normalize identically."""
    response = await _synthesize_with_finish_reason(
        "stop", model="qwen3-tts-instruct-flash", voice="Ethan"
    )
    assert response.take_evidence.provider_status == "completed"


async def test_download_audio_upgrades_http_to_https_and_no_auth_header() -> None:
    """Verify _download_audio upgrades http:// to https:// and sends no Bearer token."""
    captured_headers: dict[str, str] = {}
    captured_url: str = ""

    async def download_handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_url
        captured_url = str(request.url)
        captured_headers.update(dict(request.headers))
        return httpx.Response(200, content=b"oss-audio-bytes")

    download_client = httpx.AsyncClient(
        transport=httpx.MockTransport(download_handler),
    )
    adapter = DashScopeTTSAdapter("test-key", download_client=download_client)
    try:
        result = await adapter._download_audio(
            "http://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/prod/test.mp3?Expires=123"
        )
    finally:
        await adapter.shutdown()

    assert result == b"oss-audio-bytes"
    # http:// must be upgraded to https://
    assert captured_url.startswith("https://")
    assert "http://" not in captured_url
    # No Authorization header must leak to OSS
    assert "authorization" not in {k.lower() for k in captured_headers}
