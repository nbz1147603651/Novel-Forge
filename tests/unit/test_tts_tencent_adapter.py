"""Tests for Tencent Cloud TTS adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from novel_forge.tts.schemas import TTSProvider, TTSRequest


class TestTencentAdapterInit:
    """Test Tencent adapter initialization."""

    def test_requires_credentials(self) -> None:
        """Should raise ValueError without credentials."""
        from novel_forge.tts.gateway.adapters.tencent_adapter import TencentTTSAdapter

        with pytest.raises(ValueError, match="SecretId"):
            TencentTTSAdapter("", "key")

        with pytest.raises(ValueError, match="SecretKey"):
            TencentTTSAdapter("id", "")

    def test_provider_name(self) -> None:
        """Should have correct provider name."""
        from novel_forge.tts.gateway.adapters.tencent_adapter import TencentTTSAdapter

        adapter = TencentTTSAdapter("test-id", "test-key")
        assert adapter.provider_name == "tencent"
        assert adapter.provider_type == TTSProvider.TENCENT

    def test_default_voice_type(self) -> None:
        """Should use default voice type."""
        from novel_forge.tts.gateway.adapters.tencent_adapter import TencentTTSAdapter

        adapter = TencentTTSAdapter("test-id", "test-key")
        assert adapter._default_voice_type == 0

    def test_custom_voice_type(self) -> None:
        """Should accept custom voice type."""
        from novel_forge.tts.gateway.adapters.tencent_adapter import TencentTTSAdapter

        adapter = TencentTTSAdapter("test-id", "test-key", default_voice_type=1010)
        assert adapter._default_voice_type == 1010


class TestTencentAdapterSynthesize:
    """Test Tencent synthesis."""

    @pytest.mark.asyncio
    async def test_synthesize_success(self) -> None:
        """Should synthesize via REST API."""
        import base64

        from novel_forge.tts.gateway.adapters.tencent_adapter import TencentTTSAdapter

        adapter = TencentTTSAdapter("test-id", "test-key")

        # Mock the httpx client response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "Response": {
                "Audio": base64.b64encode(b"fake_audio").decode(),
                "RequestId": "test-req-id",
            }
        }
        mock_response.raise_for_status = MagicMock()

        adapter._client.post = AsyncMock(return_value=mock_response)

        request = TTSRequest(
            text="测试文本",
            voice_id="1010",
            provider=TTSProvider.TENCENT,
        )

        response = await adapter.synthesize(request)
        assert response.audio_data == b"fake_audio"
        assert response.model_id == "tencent-text-to-voice"
        assert response.voice_id == "1010"

    @pytest.mark.asyncio
    async def test_synthesize_projects_native_controls_and_subtitles(self) -> None:
        """Provider-only controls must reach TextToVoice instead of stopping at UI state."""
        import base64

        from novel_forge.tts.gateway.adapters.tencent_adapter import TencentTTSAdapter

        adapter = TencentTTSAdapter(
            "test-id",
            "test-key",
            project_id=7,
            primary_language=2,
            emotion_intensity=140,
            segment_rate=1,
            enable_subtitle_default=True,
        )
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "Response": {
                "Audio": base64.b64encode(b"audio").decode(),
                "RequestId": "request-1",
                "Subtitles": [{"Text": "Hello", "BeginTime": 0, "EndTime": 300}],
            }
        }
        mock_response.raise_for_status = MagicMock()
        adapter._client.post = AsyncMock(return_value=mock_response)

        response = await adapter.synthesize(
            TTSRequest(
                text="Hello",
                voice_id="WCHN-cloned-voice",
                emotion="happy",
                sample_rate=32000,
                provider=TTSProvider.TENCENT,
            )
        )

        payload = adapter._client.post.await_args.kwargs["json"]
        assert payload["VoiceType"] == 200000000
        assert payload["FastVoiceType"] == "WCHN-cloned-voice"
        assert payload["ProjectId"] == 7
        assert payload["PrimaryLanguage"] == 2
        assert payload["SampleRate"] == 24000
        assert payload["SegmentRate"] == 1
        assert payload["EnableSubtitle"] is True
        assert payload["EmotionCategory"] == "happy"
        assert payload["EmotionIntensity"] == 140
        assert response.metadata["native_subtitles"][0]["Text"] == "Hello"

    @pytest.mark.asyncio
    async def test_synthesize_empty_text_raises(self) -> None:
        """Should raise on empty text (pydantic validation)."""
        from pydantic import ValidationError

        from novel_forge.tts.gateway.adapters.tencent_adapter import TencentTTSAdapter

        TencentTTSAdapter("test-id", "test-key")
        with pytest.raises(ValidationError):
            TTSRequest(
                text="",
                voice_id="0",
                provider=TTSProvider.TENCENT,
            )

    @pytest.mark.asyncio
    async def test_synthesize_api_error_raises(self) -> None:
        """Should raise on API error."""
        from novel_forge.core.exceptions import ModelGatewayError
        from novel_forge.tts.gateway.adapters.tencent_adapter import TencentTTSAdapter

        adapter = TencentTTSAdapter("test-id", "test-key")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "Response": {"Error": {"Code": "FailedOperation", "Message": "合成失败"}}
        }

        adapter._client.post = AsyncMock(return_value=mock_response)

        request = TTSRequest(
            text="测试",
            voice_id="0",
            provider=TTSProvider.TENCENT,
        )

        with pytest.raises(ModelGatewayError):
            await adapter.synthesize(request)


class TestTencentAdapterVoices:
    """Test voice listing."""

    @pytest.mark.asyncio
    async def test_list_system_voices(self) -> None:
        """Should return voice list."""
        from novel_forge.tts.gateway.adapters.tencent_adapter import TencentTTSAdapter

        adapter = TencentTTSAdapter("test-id", "test-key")
        voices = await adapter.list_system_voices()
        assert len(voices) > 0
        assert all("voice_id" in v and "name" in v for v in voices)

    @pytest.mark.asyncio
    async def test_clone_voice_returns_failed(self) -> None:
        """Clone should return failed status (not supported)."""
        from novel_forge.tts.gateway.adapters.tencent_adapter import TencentTTSAdapter
        from novel_forge.tts.schemas import VoiceCloneRequest

        adapter = TencentTTSAdapter("test-id", "test-key")
        request = VoiceCloneRequest(
            voice_id="test",
            file_id="/path/to/audio.wav",
            provider=TTSProvider.TENCENT,
        )
        response = await adapter.clone_voice(request)
        assert response.status.value == "failed"


class TestTencentSignature:
    """Test Tencent Cloud Signature V3."""

    def test_sign_v3(self) -> None:
        """Should generate valid signature."""
        from novel_forge.tts.gateway.adapters.tencent_adapter import TencentTTSAdapter

        adapter = TencentTTSAdapter("test-id", "test-key")
        # _sign_v3 takes payload as dict
        headers = adapter._sign_v3({"Text": "test"})
        assert "Authorization" in headers
        assert "TC3-HMAC-SHA256" in headers["Authorization"]
        assert "test-id" in headers["Authorization"]
