"""Tests for bounded TTS synthesis pool payload handoff and fallback."""

from __future__ import annotations

from novel_forge.tts.gateway.adapters.mock_adapter import MockTTSAdapter
from novel_forge.tts.pipeline.synthesis_pool import PoolConfig, SynthesisPool, SynthesisTask
from novel_forge.tts.schemas import SynthesisStatus, TTSProvider, TTSRequest


async def test_synthesis_pool_returns_transient_audio_without_serializing_it() -> None:
    primary = MockTTSAdapter(latency_ms=0, fail_rate=1.0)
    fallback = MockTTSAdapter(latency_ms=0)
    pool = SynthesisPool(
        primary,
        config=PoolConfig(
            max_workers=1,
            rate_per_second=100,
            burst_size=2,
            fallback_adapter=fallback,
        ),
    )

    results = await pool.run(
        [
            SynthesisTask(
                segment_index=0,
                retry_limit=0,
                request=TTSRequest(
                    text="测试备用合成",
                    voice_id="mock-male-1",
                    provider=TTSProvider.MOCK,
                ),
            )
        ]
    )

    assert results[0].status == SynthesisStatus.COMPLETED
    assert results[0].used_fallback is True
    assert results[0].audio_data
    assert "audio_data" not in results[0].model_dump(mode="json")

