"""Tests for episodic semantic enrichment in AdaptiveCompressionService."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from novel_forge.core.constants import TaskType
from novel_forge.gateway.types import ModelRequest, ModelResponse
from novel_forge.memory.base import EpisodicResult
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
def mock_episodic_memory():
    memory = MagicMock()
    memory.search_by_semantic = AsyncMock(
        return_value=[
            EpisodicResult(chapter_number=1, event_summary="张三发现神秘信件"),
            EpisodicResult(chapter_number=2, event_summary="李四身份暴露"),
        ]
    )
    return memory


@pytest.fixture
def mock_router():
    return _make_mock_router([
        '{"items": [{"compressed": "compressed text here"}]}',
        '{"quality_score": 0.85}',
    ])


@pytest.fixture
def mock_builder():
    return _make_mock_builder()


@pytest.fixture
def service_with_episodic(mock_router, mock_builder, mock_episodic_memory):
    return AdaptiveCompressionService(
        router=mock_router,
        builder=mock_builder,
        config=CompressionConfig(min_quality_score=0.7),
        episodic_memory=mock_episodic_memory,
    )


@pytest.fixture
def service_without_episodic(mock_router, mock_builder):
    return AdaptiveCompressionService(
        router=mock_router,
        builder=mock_builder,
        config=CompressionConfig(min_quality_score=0.7),
        episodic_memory=None,
    )


@pytest.mark.asyncio
async def test_compress_with_episodic_enrichment(service_with_episodic, mock_episodic_memory):
    original_text = "这是一段需要压缩的文本，包含重要信息。张三发现神秘信件后，决定调查真相。"

    result = await service_with_episodic.compress_with_quality_check(
        original_text=original_text,
        target_chars=100,
        task_type=TaskType.CONTEXT_COMPRESS,
        context_facts=["原始事实一"],
        current_chapter=4,
    )

    assert result is not None
    assert result.compressed_text == "compressed text here"
    mock_episodic_memory.search_by_semantic.assert_called_once()
    call_kwargs = mock_episodic_memory.search_by_semantic.call_args[1]
    assert call_kwargs["chapter_range"] == (1, 3)


@pytest.mark.asyncio
async def test_compress_without_episodic(service_without_episodic):
    original_text = "这是一段需要压缩的文本，包含重要信息。"

    result = await service_without_episodic.compress_with_quality_check(
        original_text=original_text,
        target_chars=100,
        task_type=TaskType.CONTEXT_COMPRESS,
        context_facts=["原始事实一"],
        current_chapter=4,
    )

    assert result is not None
    assert result.compressed_text == "compressed text here"


@pytest.mark.asyncio
async def test_compress_enrichment_failure_degradation(mock_builder):
    broken_memory = MagicMock()
    broken_memory.search_by_semantic = AsyncMock(
        side_effect=RuntimeError("Episodic memory unavailable")
    )

    router = _make_mock_router([
        '{"items": [{"compressed": "compressed text here"}]}',
        '{"quality_score": 0.85}',
    ])

    service = AdaptiveCompressionService(
        router=router,
        builder=mock_builder,
        config=CompressionConfig(min_quality_score=0.7),
        episodic_memory=broken_memory,
    )

    original_text = "这是一段需要压缩的文本。"

    result = await service.compress_with_quality_check(
        original_text=original_text,
        target_chars=100,
        task_type=TaskType.CONTEXT_COMPRESS,
        context_facts=["fallback fact"],
        current_chapter=3,
    )

    assert result is not None
    assert result.compressed_text == "compressed text here"


@pytest.mark.asyncio
async def test_compress_quality_not_degraded(mock_builder, mock_episodic_memory):
    router = _make_mock_router([
        '{"items": [{"compressed": "compressed text here"}]}',
        '{"quality_score": 0.85}',
    ])

    service = AdaptiveCompressionService(
        router=router,
        builder=mock_builder,
        config=CompressionConfig(min_quality_score=0.7),
        episodic_memory=mock_episodic_memory,
    )

    original_text = "这是一段需要压缩的文本，包含重要信息。"

    result = await service.compress_with_quality_check(
        original_text=original_text,
        target_chars=100,
        task_type=TaskType.CONTEXT_COMPRESS,
        context_facts=["原始事实"],
        current_chapter=4,
    )

    assert result.quality_score >= 0.85
