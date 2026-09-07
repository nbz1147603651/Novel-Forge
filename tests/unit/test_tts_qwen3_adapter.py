"""Contract tests for the local Qwen3-TTS sidecar adapter."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest

from novel_forge.core.exceptions import ModelGatewayError
from novel_forge.tts.gateway.adapters.qwen3_adapter import Qwen3TTSAdapter
from novel_forge.tts.schemas import (
    TTSProvider,
    TTSRequest,
    VoiceCloneRequest,
    VoiceCloneStatus,
    VoiceDesignRequest,
)


async def _adapter_with(handler) -> Qwen3TTSAdapter:
    adapter = Qwen3TTSAdapter(
        base_url="http://qwen3.test/v1",
        api_key="sidecar-secret",
        formal_model="Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
        preview_model="Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        design_model="Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
        clone_model="Qwen/Qwen3-TTS-12Hz-1.7B-Base",
    )
    await adapter._client.aclose()  # noqa: SLF001
    adapter._client = httpx.AsyncClient(  # noqa: SLF001
        base_url="http://qwen3.test/v1",
        headers={"Authorization": "Bearer sidecar-secret"},
        transport=httpx.MockTransport(handler),
    )
    return adapter


async def test_qwen3_synthesis_forwards_official_instruction_controls() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            content=b"RIFF-qwen-wav",
            headers={
                "content-type": "audio/wav",
                "X-Novel-Forge-Audio-Format": "wav",
                "X-Novel-Forge-Model": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
            },
        )

    adapter = await _adapter_with(handler)
    try:
        response = await adapter.synthesize(
            TTSRequest(
                text="别回头。",
                voice_id="Vivian",
                model_id="Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
                speed=0.85,
                emotion="fearful",
                language_boost="Chinese",
                metadata={
                    "tone_hint": "压低声音、克制",
                    "voice_identity_lock": True,
                    "performance_context": {
                        "previous_text": "门外的脚步声停了。",
                        "previous_speaker": "旁白",
                        "next_text": "林远没有回答。",
                        "next_speaker": "旁白",
                    },
                },
                provider=TTSProvider.QWEN3,
            )
        )
    finally:
        await adapter.shutdown()

    payload = captured["payload"]
    assert captured["path"] == "/v1/audio/speech"
    assert isinstance(payload, dict)
    assert payload["language"] == "Chinese"
    assert "fearful" in payload["instruct"]
    assert "压低声音" in payload["instruct"]
    assert "年龄感、音高中心和音色" in payload["instruct"]
    assert "门外的脚步声停了" in payload["instruct"]
    assert "林远没有回答" in payload["instruct"]
    assert response.audio_format == "wav"
    assert response.model_id.endswith("0.6B-CustomVoice")


async def test_qwen3_synthesis_forwards_allowlisted_generation_controls() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            content=b"RIFF-qwen-wav",
            headers={"X-Novel-Forge-Audio-Format": "wav"},
        )

    adapter = await _adapter_with(handler)
    try:
        response = await adapter.synthesize(
            TTSRequest(
                text="保持克制。",
                voice_id="Vivian",
                language_boost="Chinese",
                metadata={
                    "platform_extension": {
                        "temperature": 0.7,
                        "top_p": 0.9,
                        "max_new_tokens": 1024,
                        "ignored": "never-forward",
                    }
                },
                provider=TTSProvider.QWEN3,
            )
        )
    finally:
        await adapter.shutdown()

    assert captured["temperature"] == 0.7
    assert captured["top_p"] == 0.9
    assert captured["max_new_tokens"] == 1024
    assert "ignored" not in captured
    assert response.take_evidence.provider_extension["language"] == "Chinese"


async def test_qwen3_connection_failure_explains_how_to_start_local_runtime() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("", request=request)

    adapter = await _adapter_with(handler)
    try:
        with pytest.raises(ModelGatewayError) as captured:
            await adapter.synthesize(
                TTSRequest(
                    text="测试本地连接。",
                    voice_id="Vivian",
                    provider=TTSProvider.QWEN3,
                )
            )
    finally:
        await adapter.shutdown()

    assert "无法连接 Qwen3-TTS 本地服务" in captured.value.message
    assert "音频模型中心" in captured.value.message
    assert captured.value.is_transient_error is True


async def test_qwen3_http_failure_keeps_fastapi_detail() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"detail": "模型尚未通过模型中心安装：preview"})

    adapter = await _adapter_with(handler)
    try:
        with pytest.raises(ModelGatewayError) as captured:
            await adapter.synthesize(
                TTSRequest(
                    text="测试模型状态。",
                    voice_id="Vivian",
                    provider=TTSProvider.QWEN3,
                )
            )
    finally:
        await adapter.shutdown()

    assert "模型尚未通过模型中心安装：preview" in captured.value.message


async def test_qwen3_design_and_authorized_clone_use_base_role_models(tmp_path: Path) -> None:
    reference = tmp_path / "speaker.wav"
    reference.write_bytes(b"RIFF-authorized-reference")
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path == "/v1/voices/design":
            payload = json.loads(request.content)
            assert payload["model"].endswith("1.7B-VoiceDesign")
            assert payload["clone_model"].endswith("1.7B-Base")
            return httpx.Response(
                200,
                json={
                    "voice_id": "qwen3-design-role-1",
                    "preview_audio": base64.b64encode(b"RIFF-preview").decode(),
                    "preview_audio_format": "wav",
                },
            )
        assert request.url.path == "/v1/voices/clone"
        assert b"reference_transcript" in request.content
        assert b"authorized" in request.content
        assert b"clone_model" in request.content
        return httpx.Response(200, json={"voice_id": "qwen3-clone-role-1"})

    adapter = await _adapter_with(handler)
    try:
        design = await adapter.design_voice(
            VoiceDesignRequest(description="成熟克制的叙述者", provider=TTSProvider.QWEN3)
        )
        denied = await adapter.clone_voice(
            VoiceCloneRequest(
                voice_id="qwen3-clone-role-1",
                file_id=str(reference),
                provider=TTSProvider.QWEN3,
            )
        )
        cloned = await adapter.clone_voice(
            VoiceCloneRequest(
                voice_id="qwen3-clone-role-1",
                file_id=str(reference),
                reference_transcript="这是授权参考音频的逐字转写。",
                authorized=True,
                provider=TTSProvider.QWEN3,
            )
        )
    finally:
        await adapter.shutdown()

    assert design.voice_id == "qwen3-design-role-1"
    assert design.preview_audio_data == b"RIFF-preview"
    assert denied.status == VoiceCloneStatus.FAILED
    assert cloned.status == VoiceCloneStatus.READY
    assert seen == [("POST", "/v1/voices/design"), ("POST", "/v1/voices/clone")]
