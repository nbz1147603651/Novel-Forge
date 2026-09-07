"""Tests for EpisodicMemory vector backend selection."""

from __future__ import annotations

import builtins
import sys

import pytest
from pydantic import ValidationError

from novel_forge.core.config import Settings
from novel_forge.gateway.embedding import EmbeddingService
from novel_forge.memory.episodic import EpisodicMemory
from tests.unit._fake_zvec import install_fake_zvec


def test_settings_default_vector_backend_is_zvec(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOVEL_FORGE_MEMORY_VECTOR_STORE_BACKEND", raising=False)
    settings = Settings(_env_file=None)

    assert settings.memory_vector_store_backend == "zvec"


def test_settings_accepts_diskann_zvec_index_type() -> None:
    settings = Settings(_env_file=None, memory_zvec_index_type="diskann")

    assert settings.memory_zvec_index_type == "diskann"


def test_settings_rejects_auto_vector_backend() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, memory_vector_store_backend="auto")


def test_episodic_memory_allows_explicit_in_memory_in_mock_mode(tmp_path) -> None:
    memory = EpisodicMemory(
        use_mock_embeddings=True,
        vector_store_backend="in_memory",
        vector_store_path=tmp_path / "zvec",
    )

    assert memory.vector_store_backend == "in_memory"


def test_real_embedding_mode_reports_deferred_vector_store_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    class FakeEmbeddingService:
        pass

    monkeypatch.setattr(
        EmbeddingService,
        "create_from_config",
        classmethod(lambda cls, config: FakeEmbeddingService()),
    )

    memory = EpisodicMemory(
        embedding_config={"provider": "fake", "model": "fake-embedding", "dimensions": 8},
        use_mock_embeddings=False,
        vector_store_backend="zvec",
        vector_store_path=tmp_path / "zvec",
    )

    stats = memory.get_memory_stats()

    assert memory.vector_store_backend == "uninitialized"
    assert stats["vector_store_initialized"] is False
    assert stats["vector_store_initialization_state"] == "deferred"


@pytest.mark.asyncio
async def test_episodic_memory_uses_zvec_backend_when_available(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    install_fake_zvec(monkeypatch)

    memory = EpisodicMemory(
        embedding_config={"model": "fake-embedding", "dimensions": 8},
        use_mock_embeddings=True,
        vector_store_backend="zvec",
        vector_store_path=tmp_path / "zvec",
    )

    assert memory.vector_store_backend == "zvec"

    signatures = await memory.index_chapter_outcome(
        chapter_number=2,
        event_summary="林远进入废弃图书馆，发现时间裂缝。",
        full_text="林远进入废弃图书馆，发现时间裂缝。",
    )
    assert len(signatures) == 1

    results = await memory.search_by_semantic(
        "林远进入废弃图书馆，发现时间裂缝。",
        chapter_range=(1, 3),
        top_k=1,
        min_relevance=0.0,
    )

    assert len(results) == 1
    assert results[0].chapter_number == 2
    assert results[0].relevance_score > 0.99

    deleted = memory.delete_chapter_memory(2)
    assert deleted == signatures
    assert (
        await memory.search_by_semantic(
            "林远进入废弃图书馆，发现时间裂缝。",
            chapter_range=(1, 3),
            top_k=1,
            min_relevance=0.0,
        )
        == []
    )


@pytest.mark.asyncio
async def test_zvec_stats_use_metadata_fallback_and_can_rehydrate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    install_fake_zvec(monkeypatch)

    memory = EpisodicMemory(
        embedding_config={"model": "fake-embedding", "dimensions": 8},
        use_mock_embeddings=True,
        vector_store_backend="zvec",
        vector_store_path=tmp_path / "zvec",
    )

    signatures = await memory.index_chapter_outcome(
        chapter_number=4,
        event_summary="林远把裂缝坐标写进黑色笔记本。",
        full_text="林远把裂缝坐标写进黑色笔记本。",
    )

    vector_store = memory._vector_store
    assert vector_store is not None
    collection = vector_store._collection
    collection.docs.clear()

    stats = memory.get_memory_stats()
    assert stats["vector_store_vector_count"] == 1

    metadata = vector_store._metadata
    metadata.clear()

    hydrated = memory.hydrate_vector_store_metadata()

    assert hydrated == 1
    assert metadata[signatures[0]]["chapter"] == 4


def test_episodic_memory_fails_fast_when_zvec_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delitem(sys.modules, "zvec", raising=False)
    real_import = builtins.__import__

    def _blocked_import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: ANN001
        if name == "zvec":
            raise ModuleNotFoundError("No module named 'zvec'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _blocked_import)

    with pytest.raises(RuntimeError, match="Zvec vector store could not be initialized"):
        EpisodicMemory(
            embedding_config={"model": "fake-embedding", "dimensions": 8},
            use_mock_embeddings=True,
            vector_store_backend="zvec",
            vector_store_path=tmp_path / "zvec",
        )
