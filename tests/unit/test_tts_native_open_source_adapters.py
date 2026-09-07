"""Contract tests for native CosyVoice and OpenVoice adapters."""

from __future__ import annotations

import httpx

from novel_forge.tts.gateway.adapters.cosyvoice_adapter import CosyVoiceTTSAdapter
from novel_forge.tts.gateway.adapters.openvoice_adapter import OpenVoiceTTSAdapter
from novel_forge.tts.schemas import TTSProvider, TTSRequest, VoiceCloneRequest, VoiceCloneStatus


async def test_cosyvoice_uses_official_instruct2_endpoint_and_wraps_pcm(tmp_path) -> None:
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"RIFF reference")
    adapter = CosyVoiceTTSAdapter(
        base_url="http://cosyvoice.test",
        default_model="FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
        mode="instruct2",
        voice_store_dir=str(tmp_path / "voices"),
    )
    await adapter._client.aclose()
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        assert b"tts_text" in request.content
        assert b"instruct_text" in request.content
        return httpx.Response(200, content=b"\x00\x00\x01\x00")

    adapter._client = httpx.AsyncClient(base_url="http://cosyvoice.test", transport=httpx.MockTransport(handler))
    try:
        clone = await adapter.clone_voice(
            VoiceCloneRequest(
                voice_id="role-1",
                file_id=str(reference),
                clone_prompt="沉稳、克制，语速稍慢。",
                provider=TTSProvider.COSYVOICE,
            )
        )
        response = await adapter.synthesize(
            TTSRequest(text="这是角色对白。", voice_id="role-1", provider=TTSProvider.COSYVOICE)
        )
    finally:
        await adapter.shutdown()

    assert clone.status == VoiceCloneStatus.READY
    assert seen == ["/inference_instruct2"]
    assert response.audio_format == "wav"
    assert response.audio_data.startswith(b"RIFF")


async def test_openvoice_persists_clone_contract_without_advertising_voice_design(tmp_path) -> None:
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"RIFF reference")
    adapter = OpenVoiceTTSAdapter(
        default_model="openvoice-v2",
        checkpoint_dir=str(tmp_path / "checkpoints_v2"),
        voice_store_dir=str(tmp_path / "voices"),
    )
    executed: list[str] = []

    def fake_clone(request: VoiceCloneRequest) -> None:
        executed.append(request.voice_id)

    adapter._clone_sync = fake_clone  # type: ignore[method-assign]
    adapter._synthesize_sync = lambda _request: b"RIFF native-openvoice"  # type: ignore[method-assign]

    clone = await adapter.clone_voice(
        VoiceCloneRequest(
            voice_id="role-2", file_id=str(reference), provider=TTSProvider.OPENVOICE
        )
    )
    response = await adapter.synthesize(
        TTSRequest(text="测试。", voice_id="role-2", provider=TTSProvider.OPENVOICE)
    )

    assert executed == ["role-2"]
    assert clone.status == VoiceCloneStatus.READY
    assert adapter.capabilities.voice_clone is True
    assert adapter.capabilities.voice_design is False
    assert response.audio_format == "wav"


def test_native_providers_are_resolved_to_distinct_models() -> None:
    from novel_forge.core.config import Settings
    from novel_forge.tts.gateway.factory import resolve_tts_model

    settings = Settings(
        tts_cosyvoice_model="FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
        tts_openvoice_model="openvoice-v2",
    )
    assert resolve_tts_model(settings, TTSProvider.COSYVOICE).endswith("2512")
    assert resolve_tts_model(settings, TTSProvider.OPENVOICE) == "openvoice-v2"
