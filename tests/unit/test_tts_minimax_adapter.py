"""Contract tests for the current MiniMax speech and voice APIs."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from novel_forge.core.exceptions import ModelGatewayError, RateLimitError
from novel_forge.tts.gateway.adapters.minimax_adapter import MiniMaxTTSAdapter
from novel_forge.tts.platform.minimax_contract import (
    MINIMAX_MUSIC_ALL_MODELS,
    MINIMAX_T2A_ALL_MODELS,
    MINIMAX_TTS_API_BASE_URL,
    minimax_emotion_allowed_for_model,
)
from novel_forge.tts.schemas import (
    TTSProvider,
    TTSRequest,
    VoiceCloneRequest,
    VoiceCloneStatus,
    VoiceDesignRequest,
    VoiceEffectControls,
)


async def _adapter_with(handler: httpx.MockTransport) -> MiniMaxTTSAdapter:
    adapter = MiniMaxTTSAdapter("test-key")
    await adapter._client.aclose()
    adapter._client = httpx.AsyncClient(
        base_url="https://api.minimaxi.com/v1",
        headers={"Authorization": "Bearer test-key"},
        transport=handler,
    )
    return adapter


async def test_minimax_uses_the_current_official_global_endpoint_by_default() -> None:
    adapter = MiniMaxTTSAdapter("test-key")
    try:
        assert adapter._base_url == MINIMAX_TTS_API_BASE_URL  # noqa: SLF001
    finally:
        await adapter.shutdown()


async def test_minimax_synthesis_uses_current_payload_and_decodes_hex() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": {"audio": "000102ff", "status": 2},
                "extra_info": {
                    "audio_length": 1250,
                    "usage_characters": 8,
                    "audio_format": "mp3",
                    "invisible_character_ratio": 0.01,
                },
                "trace_id": "trace-tts-1",
                "base_resp": {"status_code": 0, "status_msg": "success"},
            },
        )

    adapter = await _adapter_with(httpx.MockTransport(handler))
    try:
        response = await adapter.synthesize(
            TTSRequest(
                text="角色对白",
                voice_id="Chinese (Mandarin)_Reliable_Executive",
                model_id="speech-2.8-hd",
                provider=TTSProvider.MINIMAX,
            )
        )
    finally:
        await adapter.shutdown()

    assert captured["path"] == "/v1/t2a_v2"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["voice_setting"]["voice_id"] == "Chinese (Mandarin)_Reliable_Executive"
    assert payload["audio_setting"]["sample_rate"] == 32000
    assert payload["subtitle_enable"] is False
    assert payload["subtitle_type"] == "sentence"
    assert response.audio_data == b"\x00\x01\x02\xff"
    assert response.duration_ms == 1250
    assert response.take_evidence.trace_id == "trace-tts-1"
    assert response.take_evidence.provider_status == "2"


async def test_minimax_native_word_subtitles_and_cbr_are_explicit_opt_ins() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": {
                    "audio": "0102",
                    "status": 2,
                    "subtitles": [{"text": "需要", "start_ms": 0, "end_ms": 150}],
                },
                "extra_info": {"audio_length": 500, "usage_characters": 4},
                "base_resp": {"status_code": 0, "status_msg": "success"},
            },
        )

    adapter = await _adapter_with(httpx.MockTransport(handler))
    try:
        response = await adapter.synthesize(
            TTSRequest(
                text="需要词级字幕。",
                provider=TTSProvider.MINIMAX,
                metadata={
                    "platform_extension": {
                        "subtitle_enable": True,
                        "subtitle_type": "word",
                        "force_cbr": True,
                    }
                },
            )
        )
    finally:
        await adapter.shutdown()

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["subtitle_enable"] is True
    assert payload["subtitle_type"] == "word"
    assert payload["audio_setting"]["force_cbr"] is True
    assert response.metadata["native_subtitles"] == [{"text": "需要", "start_ms": 0, "end_ms": 150}]


async def test_minimax_business_error_is_not_treated_as_audio() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"base_resp": {"status_code": 1004, "status_msg": "invalid api key"}},
        )

    adapter = await _adapter_with(httpx.MockTransport(handler))
    try:
        with pytest.raises(ModelGatewayError, match="1004"):
            await adapter.synthesize(
                TTSRequest(
                    text="测试",
                    voice_id="Chinese (Mandarin)_Reliable_Executive",
                    provider=TTSProvider.MINIMAX,
                )
            )
    finally:
        await adapter.shutdown()


async def test_minimax_rpm_error_is_classified_as_retryable_rate_limit() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"base_resp": {"status_code": 1002, "status_msg": "rate limit exceeded(RPM)"}},
        )

    adapter = await _adapter_with(httpx.MockTransport(handler))
    try:
        with pytest.raises(RateLimitError, match="1002") as exc_info:
            await adapter.synthesize(
                TTSRequest(
                    text="测试",
                    voice_id="Chinese (Mandarin)_Reliable_Executive",
                    provider=TTSProvider.MINIMAX,
                )
            )
    finally:
        await adapter.shutdown()

    assert exc_info.value.is_transient_error is True
    assert exc_info.value.context["provider"] == "minimax"


async def test_minimax_synthesis_consumes_native_director_controls() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": {"audio": "0102", "status": 2},
                "extra_info": {"audio_length": 500, "usage_characters": 4},
                "base_resp": {"status_code": 0, "status_msg": "success"},
            },
        )

    adapter = await _adapter_with(httpx.MockTransport(handler))
    try:
        await adapter.synthesize(
            TTSRequest(
                text="重庆来电。",
                model_id="speech-2.8-hd",
                bitrate=256000,
                channel=1,
                emotion="fearful",
                pronunciation_overrides=["重庆/(chong2)(qing4)"],
                language_boost="Chinese",
                voice_effect=VoiceEffectControls(
                    timbre=-20,
                    intensity=-10,
                    sound_effects="lofi_telephone",
                ),
                provider=TTSProvider.MINIMAX,
            )
        )
    finally:
        await adapter.shutdown()

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["voice_setting"]["emotion"] == "fearful"
    assert payload["audio_setting"]["bitrate"] == 256000
    assert payload["audio_setting"]["channel"] == 1
    assert payload["language_boost"] == "Chinese"
    assert payload["pronunciation_dict"]["tone"] == ["重庆/(chong2)(qing4)"]
    assert payload["voice_modify"] == {
        "intensity": -10,
        "timbre": -20,
        "sound_effects": "lofi_telephone",
    }


async def test_minimax_identity_lock_omits_timbre_shifting_native_emotion() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": {"audio": "0102", "status": 2},
                "extra_info": {"audio_length": 500, "usage_characters": 4},
                "base_resp": {"status_code": 0, "status_msg": "success"},
            },
        )

    adapter = await _adapter_with(httpx.MockTransport(handler))
    try:
        await adapter.synthesize(
            TTSRequest(
                text="稳住。",
                emotion="fearful",
                provider=TTSProvider.MINIMAX,
                metadata={"voice_identity_lock": True},
            )
        )
    finally:
        await adapter.shutdown()

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert "emotion" not in payload["voice_setting"]


async def test_minimax_identity_lock_allows_explicit_native_emotion_renderer() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": {"audio": "0102", "status": 2},
                "extra_info": {"audio_length": 500, "usage_characters": 12},
                "base_resp": {"status_code": 0, "status_msg": "success"},
            },
        )

    adapter = await _adapter_with(httpx.MockTransport(handler))
    try:
        await adapter.synthesize(
            TTSRequest(
                text="这句话需要明确的恐惧情绪。",
                emotion="fearful",
                provider=TTSProvider.MINIMAX,
                metadata={
                    "voice_identity_lock": True,
                    "native_emotion_allowed": True,
                },
            )
        )
    finally:
        await adapter.shutdown()

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["voice_setting"]["emotion"] == "fearful"


async def test_minimax_legacy_models_do_not_receive_speech_28_interjections() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": {"audio": "0102", "status": 2},
                "extra_info": {"usage_characters": 4},
                "base_resp": {"status_code": 0, "status_msg": "success"},
            },
        )

    adapter = await _adapter_with(httpx.MockTransport(handler))
    try:
        response = await adapter.synthesize(
            TTSRequest(
                text="请进。",
                model_id="speech-2.6-hd",
                emotion="mocking",
                emotion_tags=["(laughs)"],
                language_boost="pt-BR",
                provider=TTSProvider.MINIMAX,
            )
        )
    finally:
        await adapter.shutdown()

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["text"] == "请进。"
    assert payload["voice_setting"]["emotion"] == "happy"  # mocking → happy（嘲讽带有戏谑）
    assert payload["language_boost"] == "Portuguese"
    assert response.cost_usd == pytest.approx(0.0004)


async def test_minimax_speech_28_keeps_documented_interjections() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": {"audio": "0102", "status": 2},
                "base_resp": {"status_code": 0, "status_msg": "success"},
            },
        )

    adapter = await _adapter_with(httpx.MockTransport(handler))
    try:
        await adapter.synthesize(
            TTSRequest(
                text="别逗了。",
                model_id="speech-2.8-hd",
                emotion_tags=["(laughs)", "(unsupported)"],
                provider=TTSProvider.MINIMAX,
            )
        )
    finally:
        await adapter.shutdown()

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["text"] == "(laughs) 别逗了。"


async def test_minimax_rejects_pcm_for_current_http_t2a_contract() -> None:
    adapter = MiniMaxTTSAdapter("test-key")
    try:
        with pytest.raises(ValueError, match="output_format"):
            await adapter.synthesize(
                TTSRequest(
                    text="测试",
                    output_format="pcm",
                    provider=TTSProvider.MINIMAX,
                )
            )
    finally:
        await adapter.shutdown()


async def test_minimax_upload_design_and_catalog_contracts(tmp_path: Path) -> None:
    reference = tmp_path / "voice.wav"
    reference.write_bytes(b"RIFF-test-audio")
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path.endswith("/files/upload"):
            assert request.headers["content-type"].startswith("multipart/form-data;")
            return httpx.Response(
                200,
                json={
                    "file": {"file_id": 123456},
                    "base_resp": {"status_code": 0, "status_msg": "success"},
                },
            )
        if request.url.path.endswith("/voice_design"):
            payload = json.loads(request.content)
            assert payload["prompt"] == "沉稳克制的青年男性"
            assert payload["preview_text"]
            return httpx.Response(
                200,
                json={
                    "voice_id": "ttv-voice-test",
                    "trial_audio": "0102",
                    "base_resp": {"status_code": 0, "status_msg": "success"},
                },
            )
        if request.url.path.endswith("/voice_clone"):
            payload = json.loads(request.content)
            assert payload == {
                "voice_id": "nf-character",
                "file_id": 123456,
                "model": "speech-2.8-hd",
            }
            return httpx.Response(
                200,
                json={"base_resp": {"status_code": 0, "status_msg": "success"}},
            )
        if request.url.path.endswith("/get_voice"):
            assert json.loads(request.content) == {"voice_type": "system"}
            return httpx.Response(
                200,
                json={
                    "system_voice": [
                        {
                            "voice_id": "Chinese (Mandarin)_News_Anchor",
                            "voice_name": "新闻女声",
                            "description": ["专业的中年女性新闻主播声音"],
                        }
                    ],
                    "base_resp": {"status_code": 0, "status_msg": "success"},
                },
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    adapter = await _adapter_with(httpx.MockTransport(handler))
    try:
        file_id = await adapter.prepare_clone_source(str(reference))
        clone = await adapter.clone_voice(
            VoiceCloneRequest(
                voice_id="nf-character",
                file_id=file_id,
                provider=TTSProvider.MINIMAX,
            )
        )
        design = await adapter.design_voice(
            VoiceDesignRequest(
                description="沉稳克制的青年男性",
                provider=TTSProvider.MINIMAX,
            )
        )
        voices = await adapter.list_system_voices()
    finally:
        await adapter.shutdown()

    assert file_id == "123456"
    assert clone.status == VoiceCloneStatus.READY
    assert clone.model_id == "speech-2.8-hd"
    assert clone.activation_deadline is not None
    assert clone.expires_at is None
    assert design.voice_id == "ttv-voice-test"
    assert design.expires_at is None
    assert design.activation_deadline is not None
    assert design.preview_audio_data == b"\x01\x02"
    assert design.preview_audio_format == "mp3"
    assert voices[0]["gender"] == "female"
    assert ("POST", "/v1/files/upload") in seen
    assert ("POST", "/v1/voice_clone") in seen
    assert ("POST", "/v1/voice_design") in seen
    assert ("POST", "/v1/get_voice") in seen


async def test_minimax_async_v2_submit_and_query_contract() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path.endswith("/t2a_async_v2"):
            payload = json.loads(request.content)
            assert payload["voice_setting"]["voice_id"] == ("Chinese (Mandarin)_Reliable_Executive")
            assert payload["audio_setting"]["audio_sample_rate"] == 32000
            return httpx.Response(
                200,
                json={
                    "task_id": 9988,
                    "base_resp": {"status_code": 0, "status_msg": "success"},
                },
            )
        if request.url.path.endswith("/query/t2a_async_query_v2"):
            assert request.url.params["task_id"] == "9988"
            return httpx.Response(
                200,
                json={
                    "task_id": 9988,
                    "status": "Success",
                    "file_id": 123,
                    "base_resp": {"status_code": 0, "status_msg": "success"},
                },
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    adapter = await _adapter_with(httpx.MockTransport(handler))
    try:
        task_id = await adapter._synthesize_async_long(
            TTSRequest(
                text="长篇配音",
                voice_id="",
                provider=TTSProvider.MINIMAX,
            )
        )
        result = await adapter.get_task_result(task_id)
    finally:
        await adapter.shutdown()

    assert task_id == "9988"
    assert result["status"] == "Success"
    assert seen == [
        ("POST", "/v1/t2a_async_v2"),
        ("GET", "/v1/query/t2a_async_query_v2"),
    ]


async def test_minimax_long_synthesis_completes_poll_and_download_cycle() -> None:
    seen: list[str] = []
    query_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal query_count
        seen.append(request.url.path)
        if request.url.path.endswith("/t2a_async_v2"):
            payload = json.loads(request.content)
            assert len(payload["text"]) == 10_001
            assert payload["audio_setting"]["audio_sample_rate"] == 32000
            return httpx.Response(
                200,
                json={
                    "task_id": 7788,
                    "usage_characters": 10_001,
                    "base_resp": {"status_code": 0, "status_msg": "success"},
                },
            )
        if request.url.path.endswith("/query/t2a_async_query_v2"):
            query_count += 1
            status = "Processing" if query_count == 1 else "Success"
            return httpx.Response(
                200,
                json={
                    "task_id": 7788,
                    "status": status,
                    "file_id": 9911 if status == "Success" else None,
                    "base_resp": {"status_code": 0, "status_msg": "success"},
                },
            )
        if request.url.path.endswith("/files/retrieve_content"):
            assert request.url.params["file_id"] == "9911"
            return httpx.Response(200, content=b"long-form-audio")
        raise AssertionError(f"Unexpected request: {request.url}")

    adapter = await _adapter_with(httpx.MockTransport(handler))
    adapter._async_poll_interval_s = 0
    try:
        response = await adapter.synthesize(
            TTSRequest(
                text="长" * 10_001,
                voice_id="Chinese (Mandarin)_Reliable_Executive",
                provider=TTSProvider.MINIMAX,
            )
        )
    finally:
        await adapter.shutdown()

    assert response.audio_data == b"long-form-audio"
    assert response.metadata == {"async_task_id": "7788", "async_file_id": "9911"}
    assert response.take_evidence.provider_status == "Success"
    assert response.take_evidence.provider_extension["async"] is True
    assert seen == [
        "/v1/t2a_async_v2",
        "/v1/query/t2a_async_query_v2",
        "/v1/query/t2a_async_query_v2",
        "/v1/files/retrieve_content",
    ]


async def test_minimax_async_expired_triggers_single_resubmission() -> None:
    """An expired async task should be resubmitted once, then succeed."""
    submit_count = 0
    query_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal submit_count, query_count
        if request.url.path.endswith("/t2a_async_v2"):
            submit_count += 1
            # Second submission gets a fresh task_id.
            task_id = 7788 if submit_count == 1 else 7799
            return httpx.Response(
                200,
                json={
                    "task_id": task_id,
                    "usage_characters": 10_001,
                    "base_resp": {"status_code": 0, "status_msg": "success"},
                },
            )
        if request.url.path.endswith("/query/t2a_async_query_v2"):
            query_count += 1
            # First task: expired on first poll. Second task: success.
            if query_count == 1:
                status = "expired"
                task_id = 7788
                file_id = None
            else:
                status = "Success"
                task_id = 7799
                file_id = 9911
            return httpx.Response(
                200,
                json={
                    "task_id": task_id,
                    "status": status,
                    "file_id": file_id,
                    "base_resp": {"status_code": 0, "status_msg": "success"},
                },
            )
        if request.url.path.endswith("/files/retrieve_content"):
            assert request.url.params["file_id"] == "9911"
            return httpx.Response(200, content=b"recovered-audio")
        raise AssertionError(f"Unexpected request: {request.url}")

    adapter = await _adapter_with(httpx.MockTransport(handler))
    adapter._async_poll_interval_s = 0  # noqa: SLF001
    try:
        response = await adapter.synthesize(
            TTSRequest(
                text="长" * 10_001,
                voice_id="Chinese (Mandarin)_Reliable_Executive",
                provider=TTSProvider.MINIMAX,
            )
        )
    finally:
        await adapter.shutdown()

    assert submit_count == 2  # original + one resubmission
    assert response.audio_data == b"recovered-audio"
    assert response.metadata["async_task_id"] == "7799"


async def test_minimax_async_expired_resubmission_failure_propagates() -> None:
    """If the resubmitted task also expires, the error propagates (no infinite loop)."""
    submit_count = 0
    query_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal submit_count, query_count
        if request.url.path.endswith("/t2a_async_v2"):
            submit_count += 1
            return httpx.Response(
                200,
                json={
                    "task_id": 7788 + submit_count,
                    "usage_characters": 10_001,
                    "base_resp": {"status_code": 0, "status_msg": "success"},
                },
            )
        if request.url.path.endswith("/query/t2a_async_query_v2"):
            query_count += 1
            return httpx.Response(
                200,
                json={
                    "task_id": 7788 + submit_count,
                    "status": "expired",
                    "base_resp": {"status_code": 0, "status_msg": "success"},
                },
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    adapter = await _adapter_with(httpx.MockTransport(handler))
    adapter._async_poll_interval_s = 0  # noqa: SLF001
    try:
        with pytest.raises(ModelGatewayError, match="expired"):
            await adapter.synthesize(
                TTSRequest(
                    text="长" * 10_001,
                    provider=TTSProvider.MINIMAX,
                )
            )
    finally:
        await adapter.shutdown()

    # Exactly 2 submissions: original + one resubmission. No third attempt.
    assert submit_count == 2


async def test_minimax_clone_normalizes_non_ascii_voice_id() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"base_resp": {"status_code": 0, "status_msg": "success"}},
        )

    adapter = await _adapter_with(httpx.MockTransport(handler))
    try:
        result = await adapter.clone_voice(
            VoiceCloneRequest(
                voice_id="nf-林小满",
                file_id="123",
                model_id="speech-2.8-turbo",
                provider=TTSProvider.MINIMAX,
            )
        )
    finally:
        await adapter.shutdown()

    assert result.status == VoiceCloneStatus.READY
    assert result.voice_id.startswith("nf-")
    assert result.voice_id.isascii()
    assert captured["voice_id"] == result.voice_id
    assert captured["model"] == "speech-2.8-turbo"
    assert result.model_id == "speech-2.8-turbo"


async def test_minimax_http_429_is_retryable() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="too many requests")

    adapter = await _adapter_with(httpx.MockTransport(handler))
    try:
        with pytest.raises(RateLimitError, match="HTTP 429"):
            await adapter.synthesize(TTSRequest(text="测试", provider=TTSProvider.MINIMAX))
    finally:
        await adapter.shutdown()


def _aigc_watermark_success_handler(captured: dict[str, object]) -> httpx.MockTransport:
    """Mock handler capturing the payload and returning a minimal success response."""

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": {"audio": "0000", "status": 2},
                "extra_info": {"audio_length": 800, "usage_characters": 4},
                "base_resp": {"status_code": 0, "status_msg": "success"},
            },
        )

    return httpx.MockTransport(handler)


async def test_aigc_watermark_defaults_off_to_avoid_audible_tail() -> None:
    """AIGC watermark must default to False.

    MiniMax injects an audible square-wave tail when the watermark is on,
    which surfaces as a "beep" at the end of short preview clips. Default
    is off; users can opt in per-request via platform_extension.
    """
    captured: dict[str, object] = {}
    adapter = await _adapter_with(_aigc_watermark_success_handler(captured))
    try:
        await adapter.synthesize(TTSRequest(text="默认关", provider=TTSProvider.MINIMAX))
    finally:
        await adapter.shutdown()

    payload = captured["payload"]
    assert payload["aigc_watermark"] is False


async def test_aigc_watermark_respects_explicit_enable_via_extension() -> None:
    """用户可通过 platform_extension.aigc_watermark=True 显式开启水印用于合规传播."""
    captured: dict[str, object] = {}
    adapter = await _adapter_with(_aigc_watermark_success_handler(captured))
    try:
        await adapter.synthesize(
            TTSRequest(
                text="开启水印",
                provider=TTSProvider.MINIMAX,
                metadata={"platform_extension": {"aigc_watermark": True}},
            )
        )
    finally:
        await adapter.shutdown()

    payload = captured["payload"]
    assert payload["aigc_watermark"] is True


async def test_aigc_watermark_respects_adapter_default_on() -> None:
    """构造 adapter 时传 aigc_watermark_default=True 则无 extension 时开启."""
    captured: dict[str, object] = {}
    adapter = MiniMaxTTSAdapter("test-key", aigc_watermark_default=True)
    await adapter._client.aclose()  # noqa: SLF001
    adapter._client = httpx.AsyncClient(  # noqa: SLF001
        base_url="https://api.minimaxi.com/v1",
        headers={"Authorization": "Bearer test-key"},
        transport=_aigc_watermark_success_handler(captured),
    )
    try:
        await adapter.synthesize(TTSRequest(text="默认开", provider=TTSProvider.MINIMAX))
    finally:
        await adapter.shutdown()

    payload = captured["payload"]
    assert payload["aigc_watermark"] is True


# ─── continuous_sound default (P2: MiniMax 参数默认化) ──────────────────────


async def test_continuous_sound_defaults_off_for_speech_2_8() -> None:
    """Undocumented compatibility fields stay off unless the user opts in."""
    captured: dict[str, object] = {}
    adapter = await _adapter_with(_aigc_watermark_success_handler(captured))
    try:
        await adapter.synthesize(TTSRequest(text="默认开", provider=TTSProvider.MINIMAX))
    finally:
        await adapter.shutdown()

    payload = captured["payload"]
    assert payload["continuous_sound"] is False


async def test_cbr_and_english_normalization_adapter_defaults_reach_http_payload() -> None:
    captured: dict[str, object] = {}
    adapter = MiniMaxTTSAdapter(
        "test-key",
        force_cbr_default=True,
        english_normalization_default=True,
    )
    await adapter._client.aclose()  # noqa: SLF001
    adapter._client = httpx.AsyncClient(  # noqa: SLF001
        base_url="https://api.minimaxi.com/v1",
        transport=_aigc_watermark_success_handler(captured),
    )
    try:
        await adapter.synthesize(TTSRequest(text="On 2026-08-09", provider=TTSProvider.MINIMAX))
    finally:
        await adapter.shutdown()

    payload = captured["payload"]
    assert payload["audio_setting"]["force_cbr"] is True
    assert payload["english_normalization"] is True


async def test_continuous_sound_respects_explicit_off_via_extension() -> None:
    """用户可通过 platform_extension.continuous_sound=False 显式关闭连续推理."""
    captured: dict[str, object] = {}
    adapter = await _adapter_with(_aigc_watermark_success_handler(captured))
    try:
        await adapter.synthesize(
            TTSRequest(
                text="关闭连续推理",
                provider=TTSProvider.MINIMAX,
                metadata={"platform_extension": {"continuous_sound": False}},
            )
        )
    finally:
        await adapter.shutdown()

    payload = captured["payload"]
    assert payload["continuous_sound"] is False


async def test_continuous_sound_respects_adapter_default_off_and_extension_override() -> None:
    """adapter 默认 False 时无 extension 不开启；单次 extension=True 可覆盖开启."""
    payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "data": {"audio": "0000", "status": 2},
                "extra_info": {"audio_length": 800, "usage_characters": 4},
                "base_resp": {"status_code": 0, "status_msg": "success"},
            },
        )

    adapter = MiniMaxTTSAdapter("test-key", continuous_sound_default=False)
    await adapter._client.aclose()  # noqa: SLF001
    adapter._client = httpx.AsyncClient(  # noqa: SLF001
        base_url="https://api.minimaxi.com/v1",
        headers={"Authorization": "Bearer test-key"},
        transport=httpx.MockTransport(handler),
    )
    try:
        await adapter.synthesize(TTSRequest(text="默认关", provider=TTSProvider.MINIMAX))
        await adapter.synthesize(
            TTSRequest(
                text="单次开",
                provider=TTSProvider.MINIMAX,
                metadata={"platform_extension": {"continuous_sound": True}},
            )
        )
    finally:
        await adapter.shutdown()

    assert payloads[0]["continuous_sound"] is False
    assert payloads[1]["continuous_sound"] is True


async def test_continuous_sound_not_sent_for_non_28_models() -> None:
    """非 speech-2.8 系列模型不注入 continuous_sound（即使默认开启也不发送）."""
    captured: dict[str, object] = {}
    adapter = await _adapter_with(_aigc_watermark_success_handler(captured))
    try:
        await adapter.synthesize(
            TTSRequest(text="旧模型", provider=TTSProvider.MINIMAX, model_id="speech-2.5-hd")
        )
    finally:
        await adapter.shutdown()

    payload = captured["payload"]
    assert "continuous_sound" not in payload


# ─── New model and emotion tests (2026-07 documentation update) ────────────


def test_all_official_t2a_models_are_in_catalog() -> None:
    """Verify the contract exposes all 8 official T2A models."""
    expected = {
        "speech-2.8-hd",
        "speech-2.8-turbo",
        "speech-2.6-hd",
        "speech-2.6-turbo",
        "speech-02-hd",
        "speech-02-turbo",
        "speech-01-hd",
        "speech-01-turbo",
    }
    assert MINIMAX_T2A_ALL_MODELS == expected


def test_all_official_music_models_are_in_catalog() -> None:
    """Verify the contract exposes all 6 official Music models."""
    expected = {
        "music-3.0",
        "music-2.6",
        "music-cover",
        "music-3.0-free",
        "music-2.6-free",
        "music-cover-free",
    }
    assert MINIMAX_MUSIC_ALL_MODELS == expected


def test_emotion_model_restrictions() -> None:
    """fluent/whisper only effective on speech-2.6 series."""
    # whisper restricted to 2.6
    assert minimax_emotion_allowed_for_model("whisper", "speech-2.6-hd") is True
    assert minimax_emotion_allowed_for_model("whisper", "speech-2.6-turbo") is True
    assert minimax_emotion_allowed_for_model("whisper", "speech-2.8-hd") is False
    assert minimax_emotion_allowed_for_model("whisper", "speech-02-hd") is False
    # fluent restricted to 2.6
    assert minimax_emotion_allowed_for_model("fluent", "speech-2.6-hd") is True
    assert minimax_emotion_allowed_for_model("fluent", "speech-2.8-turbo") is False
    # standard emotions allowed on all models
    assert minimax_emotion_allowed_for_model("happy", "speech-2.8-hd") is True
    assert minimax_emotion_allowed_for_model("calm", "speech-01-hd") is True


async def test_minimax_whisper_emotion_suppressed_on_speech_28() -> None:
    """whisper emotion should be suppressed on speech-2.8 models."""
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": {"audio": "0102", "status": 2},
                "extra_info": {"usage_characters": 4},
                "base_resp": {"status_code": 0, "status_msg": "success"},
            },
        )

    adapter = await _adapter_with(httpx.MockTransport(handler))
    try:
        await adapter.synthesize(
            TTSRequest(
                text="轻声说。",
                model_id="speech-2.8-hd",
                emotion="whisper",
                provider=TTSProvider.MINIMAX,
            )
        )
    finally:
        await adapter.shutdown()

    payload = captured["payload"]
    assert isinstance(payload, dict)
    # whisper should NOT be sent to speech-2.8
    assert "emotion" not in payload["voice_setting"]


async def test_minimax_whisper_emotion_allowed_on_speech_26() -> None:
    """whisper emotion should be sent on speech-2.6 models."""
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": {"audio": "0102", "status": 2},
                "extra_info": {"usage_characters": 4},
                "base_resp": {"status_code": 0, "status_msg": "success"},
            },
        )

    adapter = await _adapter_with(httpx.MockTransport(handler))
    try:
        await adapter.synthesize(
            TTSRequest(
                text="轻声说。",
                model_id="speech-2.6-hd",
                emotion="whisper",
                provider=TTSProvider.MINIMAX,
            )
        )
    finally:
        await adapter.shutdown()

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["voice_setting"]["emotion"] == "whisper"


async def test_minimax_continuous_sound_only_on_speech_28() -> None:
    """continuous_sound should only be sent for speech-2.8 models."""
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": {"audio": "0102", "status": 2},
                "extra_info": {"usage_characters": 4},
                "base_resp": {"status_code": 0, "status_msg": "success"},
            },
        )

    # Test with speech-2.8-hd: continuous_sound should be included
    adapter = await _adapter_with(httpx.MockTransport(handler))
    try:
        await adapter.synthesize(
            TTSRequest(
                text="长文本测试。",
                model_id="speech-2.8-hd",
                provider=TTSProvider.MINIMAX,
                metadata={"platform_extension": {"continuous_sound": True}},
            )
        )
    finally:
        await adapter.shutdown()

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["continuous_sound"] is True

    # Test with speech-2.6-hd: continuous_sound should NOT be included
    captured.clear()
    adapter = await _adapter_with(httpx.MockTransport(handler))
    try:
        await adapter.synthesize(
            TTSRequest(
                text="长文本测试。",
                model_id="speech-2.6-hd",
                provider=TTSProvider.MINIMAX,
                metadata={"platform_extension": {"continuous_sound": True}},
            )
        )
    finally:
        await adapter.shutdown()

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert "continuous_sound" not in payload


async def test_minimax_speech_01_models_are_supported() -> None:
    """speech-01-hd and speech-01-turbo should work with the adapter."""
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": {"audio": "0102", "status": 2},
                "extra_info": {"usage_characters": 4},
                "base_resp": {"status_code": 0, "status_msg": "success"},
            },
        )

    adapter = await _adapter_with(httpx.MockTransport(handler))
    try:
        response = await adapter.synthesize(
            TTSRequest(
                text="测试",
                model_id="speech-01-hd",
                provider=TTSProvider.MINIMAX,
            )
        )
    finally:
        await adapter.shutdown()

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == "speech-01-hd"
    assert response.audio_data == b"\x01\x02"


# ─── MiniMax Music provider tests ──────────────────────────────────────────


async def test_minimax_music_validates_model_against_catalog() -> None:
    """MiniMaxMusicProvider should reject models not in the official catalog."""
    from novel_forge.tts.sound_generation.providers.minimax_music import MiniMaxMusicProvider
    from novel_forge.tts.sound_generation.schemas import (
        SoundGenerationKind,
        SoundGenerationRequest,
    )

    provider = MiniMaxMusicProvider(
        api_key="test-key",
        endpoint="https://api.minimaxi.com/v1/music_generation",
        default_model="music-3.0",
    )
    with pytest.raises(Exception, match="not in the official catalog"):
        await provider.generate(
            SoundGenerationRequest(
                request_id="test-req-1",
                chapter_number=1,
                kind=SoundGenerationKind.BGM,
                cue_index=0,
                cue_label="测试BGM",
                prompt="轻柔的钢琴曲，适合夜晚阅读",
                model_id="music-1.0-invalid",
                output_format="mp3",
                duration_ms=30000,
            )
        )


async def test_minimax_music_sends_full_audio_setting() -> None:
    """MiniMaxMusicProvider should send sample_rate, bitrate, and format."""
    from novel_forge.tts.sound_generation.providers.minimax_music import MiniMaxMusicProvider
    from novel_forge.tts.sound_generation.schemas import (
        SoundGenerationKind,
        SoundGenerationRequest,
    )

    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": {"audio": "https://example.com/audio.mp3", "status": 2},
                "extra_info": {"music_duration": 30000},
                "base_resp": {"status_code": 0, "status_msg": "success"},
            },
        )

    # Create a mock transport for the music provider
    provider = MiniMaxMusicProvider(
        api_key="test-key",
        endpoint="https://api.minimaxi.com/v1/music_generation",
        default_model="music-3.0",
    )

    # Monkey-patch httpx.AsyncClient to use our mock transport
    original_init = httpx.AsyncClient.__init__

    def patched_init(self: httpx.AsyncClient, *args: object, **kwargs: object) -> None:
        kwargs["transport"] = httpx.MockTransport(handler)
        original_init(self, *args, **kwargs)

    httpx.AsyncClient.__init__ = patched_init  # type: ignore[method-assign]
    try:
        # This will fail because the URL download will fail, but we can check the payload
        try:
            await provider.generate(
                SoundGenerationRequest(
                    request_id="test-req-2",
                    chapter_number=1,
                    kind=SoundGenerationKind.BGM,
                    cue_index=0,
                    cue_label="测试BGM",
                    prompt="轻柔的钢琴曲，适合夜晚阅读",
                    model_id="music-3.0",
                    output_format="mp3",
                    duration_ms=30000,
                )
            )
        except Exception:
            pass  # Expected to fail on URL download
    finally:
        httpx.AsyncClient.__init__ = original_init  # type: ignore[method-assign]

    payload = captured.get("payload")
    assert payload is not None
    assert isinstance(payload, dict)
    assert payload["model"] == "music-3.0"
    assert payload["is_instrumental"] is True
    assert payload["output_format"] == "url"
    audio_setting = payload["audio_setting"]
    assert isinstance(audio_setting, dict)
    assert audio_setting["sample_rate"] == 44100
    assert audio_setting["bitrate"] == 256000
    assert audio_setting["format"] == "mp3"
