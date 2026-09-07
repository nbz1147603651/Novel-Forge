"""Tests for EpisodicFactEnricher."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from novel_forge.memory.base import EpisodicResult
from novel_forge.memory.compression_enricher import EpisodicFactEnricher


@pytest.fixture
def mock_episodic_memory():
    return MagicMock()


@pytest.mark.asyncio
async def test_enrich_facts_with_episodic_memory(mock_episodic_memory):
    mock_episodic_memory.search_by_semantic = AsyncMock(
        return_value=[
            EpisodicResult(chapter_number=1, event_summary="角色张三人格转变"),
            EpisodicResult(chapter_number=2, event_summary="李四发现秘密线索"),
            EpisodicResult(chapter_number=3, event_summary="王五与赵六决裂"),
        ]
    )

    enricher = EpisodicFactEnricher(
        episodic_memory=mock_episodic_memory,
        max_enrichment_facts=10,
    )
    existing_facts = ["故事设定在古代王朝", "主角张三是侠客"]

    result = await enricher.enrich_facts(
        query="角色关系发展",
        current_chapter=5,
        existing_facts=existing_facts,
        top_k=8,
    )

    assert len(result) == 5
    assert result[0] == "故事设定在古代王朝"
    assert result[1] == "主角张三是侠客"
    assert "角色张三人格转变" in result
    assert "李四发现秘密线索" in result
    assert "王五与赵六决裂" in result

    mock_episodic_memory.search_by_semantic.assert_called_once_with(
        query="角色关系发展",
        chapter_range=(1, 4),
        top_k=8,
    )


@pytest.mark.asyncio
async def test_enrich_facts_empty_episodic(mock_episodic_memory):
    mock_episodic_memory.search_by_semantic = AsyncMock(return_value=[])

    enricher = EpisodicFactEnricher(episodic_memory=mock_episodic_memory)
    existing_facts = ["事实一", "事实二"]

    result = await enricher.enrich_facts(
        query="任何查询",
        current_chapter=3,
        existing_facts=existing_facts,
    )

    assert result == ["事实一", "事实二"]


@pytest.mark.asyncio
async def test_enrich_facts_no_episodic():
    enricher = EpisodicFactEnricher(episodic_memory=None)
    existing_facts = ["事实A", "事实B"]

    result = await enricher.enrich_facts(
        query="不应被调用的查询",
        current_chapter=10,
        existing_facts=existing_facts,
    )

    assert result == ["事实A", "事实B"]


@pytest.mark.asyncio
async def test_enrich_facts_deduplication(mock_episodic_memory):
    mock_episodic_memory.search_by_semantic = AsyncMock(
        return_value=[
            EpisodicResult(chapter_number=1, event_summary="事实A"),
            EpisodicResult(chapter_number=2, event_summary="新事实C"),
        ]
    )

    enricher = EpisodicFactEnricher(
        episodic_memory=mock_episodic_memory,
        max_enrichment_facts=10,
    )
    existing_facts = ["事实A", "事实B"]

    result = await enricher.enrich_facts(
        query="测试去重",
        current_chapter=4,
        existing_facts=existing_facts,
    )

    assert result.count("事实A") == 1
    assert "事实B" in result
    assert "新事实C" in result
