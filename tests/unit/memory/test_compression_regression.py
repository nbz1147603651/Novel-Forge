"""Regression tests for Phase 1 AdaptiveCompressionService changes.

These tests verify that existing functionality remains unchanged after T3's
addition of optional episodic_memory parameter. Focus on backward compatibility
and ensuring core compression behavior is preserved.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from novel_forge.core.constants import TaskType
from novel_forge.gateway.types import ModelRequest, ModelResponse
from novel_forge.memory.compression import AdaptiveCompressionService, CompressionConfig


def _make_mock_builder():
    builder = MagicMock()
    builder.build = MagicMock(
        return_value=ModelRequest(
            task_type=TaskType.CONTEXT_COMPRESS,
            messages=[{"role": "user", "content": "test"}],
        )
    )
    return builder


def _make_mock_router(responses: list[str]):
    router = MagicMock()
    router.route = AsyncMock(
        side_effect=[ModelResponse(content=r) for r in responses]
    )
    return router


@pytest.fixture
def mock_router():
    return _make_mock_router([
        '{"items": [{"compressed": "compressed text here"}]}',
        '{"quality_score": 0.85}',
    ])


@pytest.fixture
def mock_builder():
    return _make_mock_builder()


@pytest.mark.asyncio
async def test_existing_compress_behavior_unchanged(mock_router, mock_builder):
    """Verify compress_with_quality_check works without episodic_memory.

    Creates service WITHOUT episodic_memory parameter (backward compatibility).
    Should produce same output shape and quality scoring behavior as before T3.
    """
    # Service WITHOUT episodic_memory - the key backward compatibility test
    service = AdaptiveCompressionService(
        router=mock_router,
        builder=mock_builder,
        config=CompressionConfig(min_quality_score=0.7),
    )

    original_text = "这是一段需要压缩的原始文本，包含重要内容。"

    result = await service.compress_with_quality_check(
        original_text=original_text,
        target_chars=100,
        task_type=TaskType.CONTEXT_COMPRESS,
        context_facts=["事实一", "事实二"],
        current_chapter=0,  # No chapter context
    )

    # Verify output shape matches expected behavior
    assert result is not None
    assert isinstance(result.compressed_text, str)
    assert isinstance(result.quality_score, float)
    assert isinstance(result.original_length, int)
    assert isinstance(result.compressed_length, int)
    assert isinstance(result.compression_ratio, float)
    assert isinstance(result.retained_facts, list)
    assert isinstance(result.warnings, list)

    # Verify quality scoring behavior unchanged
    assert result.quality_score == 0.85
    assert result.compressed_text == "compressed text here"
    assert result.original_length == len(original_text)
    # compression_ratio is already computed and rounded by the service
    assert result.compression_ratio > 0


@pytest.mark.asyncio
async def test_compress_stats_tracking(mock_router, mock_builder):
    """Verify compression stats are tracked correctly after T3 changes.

    Calls compress multiple times and checks get_compression_stats returns
    proper data with correct aggregate values.
    """
    service = AdaptiveCompressionService(
        router=mock_router,
        builder=mock_builder,
        config=CompressionConfig(min_quality_score=0.7),
    )

    # Multiple compressions to build up stats
    for i in range(3):
        mock_router.route = AsyncMock(
            side_effect=[
                ModelResponse(content='{"items": [{"compressed": "comp"}]}'),
                ModelResponse(content='{"quality_score": 0.85}'),
            ]
        )

        original = f"需要压缩的文本{i}，包含一些内容。"
        result = await service.compress_with_quality_check(
            original_text=original,
            target_chars=50,
            task_type=TaskType.CONTEXT_COMPRESS,
            context_facts=["事实"],
            current_chapter=1,
        )
        assert result is not None

    # Verify stats are tracked
    stats = service.get_compression_stats(TaskType.CONTEXT_COMPRESS.value)

    assert stats is not None
    assert stats.task_type == TaskType.CONTEXT_COMPRESS.value
    assert stats.total_compressions == 3
    assert stats.avg_quality_score == 0.85
    assert stats.low_quality_count == 0  # 0.85 >= 0.7 threshold


@pytest.mark.asyncio
async def test_compress_dynamic_thresholds(mock_router, mock_builder):
    """Verify get_dynamic_thresholds works with different context window sizes.

    Tests that the method correctly adjusts compression config based on
    model's context window, returning proper CompressionConfig.
    """
    service = AdaptiveCompressionService(
        router=mock_router,
        builder=mock_builder,
        config=CompressionConfig(
            soft_limit_chars=2200,
            hard_limit_chars=5200,
            enable_dynamic_thresholds=True,
        ),
    )

    # Reference context (32K) - should return config with original limits
    ref_config = service.get_dynamic_thresholds(model_context_window=32000)
    assert isinstance(ref_config, CompressionConfig)
    assert ref_config.soft_limit_chars == 2200
    assert ref_config.hard_limit_chars == 5200
    assert ref_config.context_window_tokens == 32000

    # Larger context (128K) - should scale up limits
    large_config = service.get_dynamic_thresholds(model_context_window=128000)
    assert isinstance(large_config, CompressionConfig)
    assert large_config.context_window_tokens == 128000
    # Ratio: 128000 / 32000 = 4.0, but clamped to max 2.0
    assert large_config.soft_limit_chars == 2200 * 2
    assert large_config.hard_limit_chars == 5200 * 2

    # Smaller context (8K) - should scale down limits
    small_config = service.get_dynamic_thresholds(model_context_window=8000)
    assert isinstance(small_config, CompressionConfig)
    assert small_config.context_window_tokens == 8000
    # Ratio: 8000 / 32000 = 0.25, but clamped to min 0.3
    assert small_config.soft_limit_chars == int(2200 * 0.3)

    # Dynamic thresholds disabled - should return original config
    disabled_service = AdaptiveCompressionService(
        router=mock_router,
        builder=mock_builder,
        config=CompressionConfig(
            soft_limit_chars=2200,
            hard_limit_chars=5200,
            enable_dynamic_thresholds=False,
        ),
    )
    disabled_config = disabled_service.get_dynamic_thresholds(model_context_window=64000)
    assert disabled_config.soft_limit_chars == 2200
    assert disabled_config.hard_limit_chars == 5200


@pytest.mark.asyncio
async def test_long_compression_and_verification_cover_every_source_character():
    captured: list[tuple[TaskType, dict[str, object]]] = []
    builder = MagicMock()

    def _build(task_type, context, **_kwargs):
        captured.append((task_type, context))
        return ModelRequest(
            task_type=task_type,
            messages=[{"role": "user", "content": "test"}],
        )

    builder.build.side_effect = _build
    router = _make_mock_router(
        [
            '{"items": [{"compressed": "part-a"}]}',
            '{"items": [{"compressed": "part-b"}]}',
            '{"items": [{"compressed": "part-c"}]}',
            '{"quality_score": 0.9}',
            '{"quality_score": 0.9}',
            '{"quality_score": 0.9}',
        ]
    )
    service = AdaptiveCompressionService(
        router=router,
        builder=builder,
        config=CompressionConfig(min_quality_score=0.7, context_window_tokens=32000),
    )
    original = "甲" * 24000 + "乙" * 24000 + "丙" * 2000

    result = await service.compress_with_quality_check(
        original_text=original,
        target_chars=1000,
        task_type=TaskType.CONTEXT_COMPRESS,
    )

    compression_sources = [
        str(context["blocks"][0]["text"])  # type: ignore[index]
        for task, context in captured
        if task == TaskType.CONTEXT_COMPRESS
    ]
    verification_sources = [
        str(context["original"])
        for task, context in captured
        if task == TaskType.VERIFY_COMPRESSION
    ]
    assert "".join(compression_sources) == original
    assert "".join(verification_sources) == original
    assert result.compressed_text == "part-a\npart-b\npart-c"
