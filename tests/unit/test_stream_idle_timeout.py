"""Tests for stream idle timeout and TTS batch timeout (stall recovery).

Verifies:
1. Router inter-chunk idle timeout fires when stream stalls.
2. TTS script batch timeout triggers rule-based fallback.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from novel_forge.common.constants import TaskType
from novel_forge.core.config import Settings
from novel_forge.core.exceptions import ModelGatewayError
from novel_forge.gateway.base import ProviderAdapter
from novel_forge.gateway.router import ModelRouter
from novel_forge.gateway.types import ModelRequest, ModelResponse, StreamChunk


class _StallingStreamAdapter(ProviderAdapter):
    """Adapter whose stream yields N chunks then stalls indefinitely."""

    def __init__(self, chunks_before_stall: int = 3, stall_seconds: float = 999.0) -> None:
        self._chunks_before_stall = chunks_before_stall
        self._stall_seconds = stall_seconds
        self.calls: list[ModelRequest] = []

    @property
    def provider_name(self) -> str:
        return "stall_mock"

    @property
    def default_model(self) -> str | None:
        return "stall-model"

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        return ModelResponse(content="fallback", model_id="stall-model")

    async def stream(self, request: ModelRequest, *, on_final=None):
        self.calls.append(request)
        for i in range(self._chunks_before_stall):
            yield StreamChunk(content=f"chunk{i}")
        # Simulate a stalled connection: sleep forever (will be cut by idle timeout)
        await asyncio.sleep(self._stall_seconds)


class _NormalStreamAdapter(ProviderAdapter):
    """Adapter whose stream completes normally."""

    def __init__(self, chunks: list[str] | None = None) -> None:
        self._chunks = chunks or ["hello", " world"]
        self.calls: list[ModelRequest] = []

    @property
    def provider_name(self) -> str:
        return "normal_mock"

    @property
    def default_model(self) -> str | None:
        return "normal-model"

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        return ModelResponse(content="".join(self._chunks), model_id="normal-model")

    async def stream(self, request: ModelRequest, *, on_final=None):
        self.calls.append(request)
        for chunk_text in self._chunks:
            yield StreamChunk(content=chunk_text)
        if on_final:
            on_final(ModelResponse(content="".join(self._chunks), model_id="normal-model"))


class TestStreamIdleTimeout:
    """Verify router inter-chunk idle timeout detects stalled streams."""

    async def test_idle_timeout_fires_when_stream_stalls(self) -> None:
        """Stream that stops sending chunks triggers idle timeout quickly."""
        adapter = _StallingStreamAdapter(chunks_before_stall=2, stall_seconds=999.0)
        router = ModelRouter(
            adapters={"stall_mock": adapter},
            default_provider="stall_mock",
            request_timeout_s=30.0,
            stream_idle_timeout_s=0.3,  # Very short for test speed
        )

        start = time.monotonic()
        with pytest.raises(
            ModelGatewayError, match="all stream route attempts failed"
        ) as exc_info:
            await router.stream_route(
                ModelRequest(
                    task_type=TaskType.TTS_GENERATE_DUBBING_SCRIPT,
                    messages=[{"role": "user", "content": "test"}],
                    max_tokens=100,
                    temperature=0.5,
                ),
            )
        elapsed = time.monotonic() - start
        # Should fire at ~0.3s (idle timeout), NOT at 30s (total timeout)
        assert elapsed < 5.0, f"Idle timeout took too long: {elapsed:.1f}s"
        assert exc_info.value.is_transient_error is True
        assert exc_info.value.context["failure_categories"] == ["timeout"]
        assert exc_info.value.partial_text == "chunk0chunk1"
        assert exc_info.value.context["partial_stream"]["text_length"] == len("chunk0chunk1")
        assert "chunk0chunk1" not in str(exc_info.value)

    async def test_normal_stream_completes_without_idle_timeout(self) -> None:
        """Normal stream that completes quickly is not affected by idle timeout."""
        adapter = _NormalStreamAdapter(chunks=["a", "b", "c"])
        router = ModelRouter(
            adapters={"normal_mock": adapter},
            default_provider="normal_mock",
            request_timeout_s=30.0,
            stream_idle_timeout_s=0.5,
        )

        response = await router.stream_route(
            ModelRequest(
                task_type=TaskType.TTS_GENERATE_DUBBING_SCRIPT,
                messages=[{"role": "user", "content": "test"}],
                max_tokens=100,
                temperature=0.5,
            ),
        )
        assert response.content == "abc"

    async def test_idle_timeout_disabled_when_zero(self) -> None:
        """Setting stream_idle_timeout_s=0 disables inter-chunk detection."""
        adapter = _NormalStreamAdapter(chunks=["x", "y"])
        router = ModelRouter(
            adapters={"normal_mock": adapter},
            default_provider="normal_mock",
            request_timeout_s=30.0,
            stream_idle_timeout_s=0.0,  # Disabled
        )

        response = await router.stream_route(
            ModelRequest(
                task_type=TaskType.DRAFT_CHAPTER,
                messages=[{"role": "user", "content": "test"}],
                max_tokens=100,
                temperature=0.5,
            ),
        )
        assert response.content == "xy"


class TestTTSBatchTimeout:
    """Verify TTS script batch timeout triggers rule-based fallback."""

    async def test_batch_timeout_triggers_fallback(self) -> None:
        """When LLM call exceeds tts_script_batch_timeout_s, fallback fires."""
        from unittest.mock import MagicMock

        from novel_forge.tts.pipeline.generate_script_step import (
            GenerateDubbingScriptInput,
            GenerateDubbingScriptStep,
        )
        from novel_forge.tts.schemas import VoiceTeamContract

        settings = Settings(
            _env_file=None,
            tts_default_provider="mock",
        )
        # Patch the timeout to a very short value for test speed.
        # The Field has ge=60 so we cannot pass 0.2 via constructor.
        object.__setattr__(settings, "tts_script_batch_timeout_s", 0.2)

        router = MagicMock()
        builder = MagicMock()
        step = GenerateDubbingScriptStep(router, builder, settings=settings)

        # Mock _call_with_retry to sleep longer than the batch timeout
        async def slow_call(*args, **kwargs):
            await asyncio.sleep(10.0)  # Way longer than 0.2s timeout
            return {"segments": []}

        step._call_with_retry = slow_call
        step._on_step_event = MagicMock()

        input_data = GenerateDubbingScriptInput(
            chapter_number=1,
            chapter_text="苏晚打开档案。林小满站在门口。" * 50,  # Enough for 1+ batch
            voice_team=VoiceTeamContract(entries=[], narrator_voice_id="n1"),
            character_voices=[],
            style_profile={},
            story_context={},
        )

        # _generate_script_llm should fallback to rule-based on timeout
        result = await step._generate_script_llm(input_data, {})
        # Result should not be None (rule-based fallback produces a script)
        assert result is not None
        assert len(result.segments) > 0
