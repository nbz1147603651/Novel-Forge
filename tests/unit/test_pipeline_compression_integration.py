"""Tests for pipeline compression integration with EpisodicMemory.

This module tests that:
1. Pipeline compression calls receive episodic_memory when available
2. Pipeline compression gracefully degrades when episodic_memory is not available
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from novel_forge.memory.compression import AdaptiveCompressionService
from novel_forge.memory.integration import MemoryContext


class _MockEpisodicMemory:
    """Minimal mock for EpisodicMemory."""

    def __init__(self) -> None:
        self._chapter_events: dict[int, list[Any]] = {}

    async def search_by_semantic(
        self,
        query: str,
        chapter_range: tuple[int, int] | None = None,
        top_k: int = 8,
        event_types: list[str] | None = None,
    ) -> list[Any]:
        return []


class _MockBuilder:
    """Minimal mock for PromptBuilder."""

    def __init__(self) -> None:
        pass


class _MockRouter:
    """Minimal mock for ModelRouter."""

    def __init__(self) -> None:
        pass


def _make_settings(
    memory_episodic_enabled: bool = True,
    memory_adaptive_compression_enabled: bool = True,
    memory_use_mock_embeddings: bool = True,
) -> MagicMock:
    """Create a mock Settings object."""
    settings = MagicMock()
    settings.memory_episodic_enabled = memory_episodic_enabled
    settings.memory_adaptive_compression_enabled = memory_adaptive_compression_enabled
    settings.memory_use_mock_embeddings = memory_use_mock_embeddings
    settings.memory_multi_granularity_summary_enabled = False
    settings.memory_motif_tracking_enabled = False
    settings.memory_critic_agent_enabled = False
    settings.memory_vector_store_backend = "in_memory" if memory_use_mock_embeddings else "zvec"
    settings.memory_zvec_index_type = "hnsw"
    settings.memory_zvec_memory_limit_mb = 512
    settings.memory_embedding_profile_id = None
    settings.ollama_embedding_model = "nomic-embed-text"
    settings.ollama_base_url = "http://localhost:11434/v1"
    return settings


def test_pipeline_compress_with_episodic() -> None:
    """Verify compression_service receives episodic_memory when MemoryContext is created.

    This test verifies that when MemoryContext.create_from_settings() is called
    with memory_episodic_enabled=True, the AdaptiveCompressionService is created
    with the episodic_memory parameter properly set.
    """
    settings = _make_settings(
        memory_episodic_enabled=True,
        memory_adaptive_compression_enabled=True,
        memory_use_mock_embeddings=True,
    )

    memory_context = MemoryContext.create_from_settings(
        router=_MockRouter(),
        builder=_MockBuilder(),
        settings=settings,
        project_id="test-project",
        storage=None,
    )

    # Verify compression_service exists and has episodic_memory set
    assert memory_context.compression_service is not None
    assert isinstance(memory_context.compression_service, AdaptiveCompressionService)
    assert memory_context.compression_service._episodic_memory is not None
    assert memory_context.episodic_memory is not None
    # The compression_service should have the same episodic_memory reference
    assert memory_context.compression_service._episodic_memory is memory_context.episodic_memory


@pytest.mark.asyncio
async def test_pipeline_compress_no_episodic_fallback() -> None:
    """Verify compression_service gracefully handles missing episodic_memory.

    This test verifies that when MemoryContext.create_from_settings() is called
    with memory_episodic_enabled=False, the AdaptiveCompressionService is created
    with episodic_memory=None, and compression still works (graceful degradation).
    """
    settings = _make_settings(
        memory_episodic_enabled=False,
        memory_adaptive_compression_enabled=True,
        memory_use_mock_embeddings=False,
    )

    memory_context = MemoryContext.create_from_settings(
        router=_MockRouter(),
        builder=_MockBuilder(),
        settings=settings,
        project_id="test-project",
        storage=None,
    )

    # Verify compression_service exists but episodic_memory is None
    assert memory_context.compression_service is not None
    assert isinstance(memory_context.compression_service, AdaptiveCompressionService)
    # episodic_memory should be None since memory_episodic_enabled=False
    assert memory_context.episodic_memory is None
    # compression_service should also have episodic_memory=None (graceful degradation)
    assert memory_context.compression_service._episodic_memory is None

    # Verify that compression_service can still be used (graceful degradation)
    # The _enrich_context_facts method should return existing facts unchanged
    existing_facts = ["fact1", "fact2"]
    result = await memory_context.compression_service._enrich_context_facts(
        query="test query",
        current_chapter=5,
        existing_facts=existing_facts,
    )
    # Should return original facts unchanged when episodic_memory is None
    assert result == existing_facts
