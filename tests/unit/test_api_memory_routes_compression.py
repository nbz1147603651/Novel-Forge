"""Tests for memory API compression route."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from novel_forge.api.routes import memory
from novel_forge.core.constants import TaskType
from novel_forge.memory.compression import CompressionResult


class _RuntimeStub:
    def __init__(self, memory_context: object) -> None:
        self._memory_context = memory_context

    async def get_memory_context(self, _project_id: str) -> object:
        return self._memory_context


def _make_compression_result() -> CompressionResult:
    return CompressionResult(
        compressed_text="compressed",
        quality_score=0.85,
        original_length=1000,
        compressed_length=200,
        compression_ratio=0.2,
        retained_facts=["fact1"],
        warnings=[],
    )


@pytest.mark.asyncio
async def test_compress_with_semantic_enrichment() -> None:
    """POST with use_semantic_enrichment=true, verify current_chapter passed."""

    class _EpisodicMemoryStub:
        pass

    class _CompressionServiceStub:
        def __init__(self):
            self._episodic_memory = None
            self.captured_current_chapter: int | None = None

        async def compress_with_quality_check(
            self,
            original_text: str,
            target_chars: int,
            task_type: TaskType,
            context_facts: list[str] | None = None,
            current_chapter: int = 0,
        ) -> CompressionResult:
            self.captured_current_chapter = current_chapter
            return _make_compression_result()

    compression_service = _CompressionServiceStub()
    episodic_memory = _EpisodicMemoryStub()

    memory_context = SimpleNamespace(
        episodic_memory=episodic_memory,
        compression_service=compression_service,
    )
    runtime = _RuntimeStub(memory_context)

    result = await memory.compress_context(
        project_id="demo",
        request=memory.CompressionRequest(
            original_text="original text",
            target_chars=2000,
            context_facts=["fact1"],
            use_semantic_enrichment=True,
            current_chapter=5,
        ),
        runtime=runtime,
    )

    assert compression_service.captured_current_chapter == 5
    assert compression_service._episodic_memory is episodic_memory
    assert result.compressed_text == "compressed"
    assert result.quality_score == 0.85


@pytest.mark.asyncio
async def test_compress_without_semantic_enrichment() -> None:
    """POST with use_semantic_enrichment=false, verify current_chapter=0."""

    class _CompressionServiceStub:
        def __init__(self):
            self._episodic_memory = None
            self.captured_current_chapter: int | None = None

        async def compress_with_quality_check(
            self,
            original_text: str,
            target_chars: int,
            task_type: TaskType,
            context_facts: list[str] | None = None,
            current_chapter: int = 0,
        ) -> CompressionResult:
            self.captured_current_chapter = current_chapter
            return _make_compression_result()

    compression_service = _CompressionServiceStub()

    memory_context = SimpleNamespace(
        episodic_memory=None,
        compression_service=compression_service,
    )
    runtime = _RuntimeStub(memory_context)

    result = await memory.compress_context(
        project_id="demo",
        request=memory.CompressionRequest(
            original_text="original text",
            target_chars=2000,
            context_facts=["fact1"],
            use_semantic_enrichment=False,
            current_chapter=5,
        ),
        runtime=runtime,
    )

    assert compression_service.captured_current_chapter == 0
    assert result.compressed_text == "compressed"
    assert result.quality_score == 0.85