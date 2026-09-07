"""Tests for Local OpenAI-compatible TTS adapter."""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from novel_forge.tts.schemas import (
    TTSProvider,
    TTSProviderCapabilities,
    TTSRequest,
    VoiceCloneRequest,
    VoiceCloneStatus,
    VoiceDesignRequest,
)


class TestLocalAdapterInit:
    """Test Local adapter initialization."""

    def test_default_base_url(self) -> None:
        """Should use default base URL."""
        from novel_forge.tts.gateway.adapters.local_adapter import LocalTTSAdapter

        adapter = LocalTTSAdapter()
        assert adapter._base_url == "http://localhost:8000/v1"

    def test_custom_base_url(self) -> None:
        """Should accept custom base URL."""
        from novel_forge.tts.gateway.adapters.local_adapter import LocalTTSAdapter

        adapter = LocalTTSAdapter(base_url="http://myserver:9000/v1")
        assert adapter._base_url == "http://myserver:9000/v1"

    def test_base_url_trailing_slash_stripped(self) -> None:
        """Should strip trailing slash."""
        from novel_forge.tts.gateway.adapters.local_adapter import LocalTTSAdapter

        adapter = LocalTTSAdapter(base_url="http://localhost:8000/v1/")
        assert adapter._base_url == "http://localhost:8000/v1"

    def test_provider_name(self) -> None:
        """Should have correct provider name."""
        from novel_forge.tts.gateway.adapters.local_adapter import LocalTTSAdapter

        adapter = LocalTTSAdapter()
        assert adapter.provider_name == "local"
        assert adapter.provider_type == TTSProvider.LOCAL

    def test_api_key_header(self) -> None:
        """Should set auth header when API key provided."""
        from novel_forge.tts.gateway.adapters.local_adapter import LocalTTSAdapter

        adapter = LocalTTSAdapter(api_key="test-key")
        assert adapter._client.headers.get("Authorization") == "Bearer test-key"

    def test_no_auth_header_without_key(self) -> None:
        """Should not set auth header without API key."""
        from novel_forge.tts.gateway.adapters.local_adapter import LocalTTSAdapter

        adapter = LocalTTSAdapter()
        assert "Authorization" not in adapter._client.headers


class TestLocalAdapterSynthesize:
    """Test Local synthesis."""

    @pytest.mark.asyncio
    async def test_synthesize_success(self) -> None:
        """Should synthesize via /v1/audio/speech."""
        from novel_forge.tts.gateway.adapters.local_adapter import LocalTTSAdapter

        adapter = LocalTTSAdapter()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"fake_audio_data"
        mock_response.raise_for_status = MagicMock()

        adapter._client.post = AsyncMock(return_value=mock_response)

        request = TTSRequest(
            text="测试文本",
            voice_id="default",
            provider=TTSProvider.LOCAL,
        )

        response = await adapter.synthesize(request)
        assert response.audio_data == b"fake_audio_data"
        assert response.model_id == "default"
        assert response.voice_id == "default"

    @pytest.mark.asyncio
    async def test_synthesize_empty_text_raises(self) -> None:
        """Should raise on empty text (pydantic validation)."""
        from pydantic import ValidationError

        from novel_forge.tts.gateway.adapters.local_adapter import LocalTTSAdapter

        LocalTTSAdapter()
        with pytest.raises(ValidationError):
            TTSRequest(
                text="",
                voice_id="default",
                provider=TTSProvider.LOCAL,
            )

    @pytest.mark.asyncio
    async def test_synthesize_uses_custom_model(self) -> None:
        """Should use custom model ID in request."""
        from novel_forge.tts.gateway.adapters.local_adapter import LocalTTSAdapter

        adapter = LocalTTSAdapter(default_model="fish-speech-v1")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"audio"
        mock_response.raise_for_status = MagicMock()

        adapter._client.post = AsyncMock(return_value=mock_response)

        request = TTSRequest(
            text="测试",
            voice_id="test-voice",
            provider=TTSProvider.LOCAL,
        )

        response = await adapter.synthesize(request)
        assert response.model_id == "fish-speech-v1"

        # Verify the POST was called with correct payload
        call_args = adapter._client.post.call_args
        payload = call_args.kwargs.get("json") or call_args.args[1]
        assert payload["model"] == "fish-speech-v1"


class TestLocalAdapterVoices:
    """Test voice listing."""

    @pytest.mark.asyncio
    async def test_list_system_voices_from_api(self) -> None:
        """Should fetch voices from /v1/models."""
        from novel_forge.tts.gateway.adapters.local_adapter import LocalTTSAdapter

        adapter = LocalTTSAdapter()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {"id": "voice-1", "object": "model"},
                {"id": "voice-2", "object": "model"},
            ]
        }

        adapter._client.get = AsyncMock(return_value=mock_response)

        voices = await adapter.list_system_voices()
        assert len(voices) == 2
        assert voices[0]["voice_id"] == "voice-1"

    @pytest.mark.asyncio
    async def test_health_check_success(self) -> None:
        """Should pass health check when models endpoint responds."""
        from novel_forge.tts.gateway.adapters.local_adapter import LocalTTSAdapter

        adapter = LocalTTSAdapter()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": [{"id": "model-1"}]}

        adapter._client.get = AsyncMock(return_value=mock_response)

        healthy = await adapter.health_check()
        assert healthy is True

    @pytest.mark.asyncio
    async def test_health_check_failure(self) -> None:
        """Should fail health check when endpoint unreachable."""
        from novel_forge.tts.gateway.adapters.local_adapter import LocalTTSAdapter

        adapter = LocalTTSAdapter()
        adapter._client.get = AsyncMock(side_effect=Exception("Connection refused"))

        healthy = await adapter.health_check()
        assert healthy is False


class TestLocalAdapterExtensions:
    """Test the capability-negotiated protocol for local open-source engines."""

    @pytest.mark.asyncio
    async def test_discovers_optional_voice_features(self) -> None:
        from novel_forge.tts.gateway.adapters.local_adapter import LocalTTSAdapter

        adapter = LocalTTSAdapter()
        response = MagicMock()
        response.json.return_value = {
            "synthesis": True,
            "voice_clone": True,
            "voice_design": True,
            "system_voice_catalog": True,
            "local_reference_audio": True,
        }
        response.raise_for_status = MagicMock()
        adapter._client.get = AsyncMock(return_value=response)

        capabilities = await adapter.discover_capabilities()

        assert capabilities.voice_clone is True
        assert capabilities.voice_design is True
        assert capabilities.local_reference_audio is True

    @pytest.mark.asyncio
    async def test_clone_uploads_local_reference_when_advertised(self, tmp_path: Path) -> None:
        from novel_forge.tts.gateway.adapters.local_adapter import LocalTTSAdapter

        reference = tmp_path / "role.wav"
        reference.write_bytes(b"RIFF-role")
        adapter = LocalTTSAdapter()
        adapter._capabilities_cache = TTSProviderCapabilities(
            provider=TTSProvider.LOCAL,
            voice_clone=True,
            local_reference_audio=True,
        )
        response = MagicMock()
        response.json.return_value = {"voice_id": "local-role", "message": "ready"}
        response.raise_for_status = MagicMock()
        adapter._client.post = AsyncMock(return_value=response)

        result = await adapter.clone_voice(
            VoiceCloneRequest(
                voice_id="nf-role",
                file_id=str(reference),
                clone_prompt="沉稳青年",
                provider=TTSProvider.LOCAL,
            )
        )

        assert result.status == VoiceCloneStatus.READY
        assert result.voice_id == "local-role"
        call = adapter._client.post.call_args
        assert call.args[0] == "/voices/clone"
        assert call.kwargs["files"]["file"][0] == "role.wav"

    @pytest.mark.asyncio
    async def test_design_sends_preview_text_and_decodes_audio(self) -> None:
        from novel_forge.tts.gateway.adapters.local_adapter import LocalTTSAdapter

        adapter = LocalTTSAdapter()
        adapter._capabilities_cache = TTSProviderCapabilities(
            provider=TTSProvider.LOCAL,
            voice_design=True,
        )
        response = MagicMock()
        response.json.return_value = {
            "voice_id": "qwen-role",
            "preview_audio": base64.b64encode(b"wav-preview").decode("ascii"),
        }
        response.raise_for_status = MagicMock()
        adapter._client.post = AsyncMock(return_value=response)
        request = VoiceDesignRequest(
            description="冷静克制的青年侦探",
            preview_text="线索就在这里。",
            provider=TTSProvider.LOCAL,
        )

        result = await adapter.design_voice(request)

        assert result.status == VoiceCloneStatus.READY
        assert result.preview_audio_data == b"wav-preview"
        assert result.preview_audio_format == "wav"
        payload = adapter._client.post.call_args.kwargs["json"]
        assert payload == {
            "description": "冷静克制的青年侦探",
            "preview_text": "线索就在这里。",
        }
