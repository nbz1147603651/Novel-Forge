"""Tests for embedding model dimension inference."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from novel_forge.gateway.embedding import EmbeddingService
from novel_forge.memory.episodic import EpisodicMemory
from tests.unit._fake_zvec import install_fake_zvec


def test_default_dimensions_for_known_embedding_models() -> None:
    assert EmbeddingService.default_dimensions_for_model("hunyuan-embedding") == 1024
    assert EmbeddingService.default_dimensions_for_model("text-embedding-v4") == 1024
    assert EmbeddingService.default_dimensions_for_model("nomic-embed-text") == 768
    assert EmbeddingService.default_dimensions_for_model("nomic-embed-text:latest") == 768
    assert EmbeddingService.default_dimensions_for_model("qwen3-embedding:0.6b") == 1024


def test_episodic_memory_uses_hunyuan_embedding_dimension(
    monkeypatch,
    tmp_path,
) -> None:
    install_fake_zvec(monkeypatch)

    memory = EpisodicMemory(
        embedding_config={
            "provider": "tencent",
            "model": "hunyuan-embedding",
            "api_key": "dummy",
        },
        use_mock_embeddings=True,
        vector_store_backend="zvec",
        vector_store_path=tmp_path / "zvec",
    )

    assert memory._vector_store.dimension == 1024


@pytest.mark.asyncio
async def test_episodic_memory_builds_vector_store_from_observed_dimension(
    monkeypatch,
    tmp_path,
) -> None:
    install_fake_zvec(monkeypatch)

    class FakeEmbeddingService:
        async def generate_embedding(self, text: str):
            return SimpleNamespace(embedding=[0.1, 0.2, 0.3, 0.4, 0.5])

    monkeypatch.setattr(
        EmbeddingService,
        "create_from_config",
        classmethod(lambda cls, config: FakeEmbeddingService()),
    )

    memory = EpisodicMemory(
        embedding_config={"provider": "custom", "model": "unknown-embedding"},
        use_mock_embeddings=False,
        vector_store_backend="zvec",
        vector_store_path=tmp_path / "zvec",
    )

    assert memory.vector_store_backend == "uninitialized"

    await memory.index_chapter_outcome(
        chapter_number=1,
        event_summary="探针向量决定维度。",
        full_text="探针向量决定维度。",
    )

    assert memory.vector_store_backend == "zvec"
    assert memory._vector_store.dimension == 5


@pytest.mark.asyncio
async def test_episodic_memory_prefers_observed_dimension_over_config(
    monkeypatch,
    tmp_path,
) -> None:
    install_fake_zvec(monkeypatch)

    class FakeEmbeddingService:
        async def generate_embedding(self, text: str):
            return SimpleNamespace(embedding=[0.1, 0.2, 0.3, 0.4])

    monkeypatch.setattr(
        EmbeddingService,
        "create_from_config",
        classmethod(lambda cls, config: FakeEmbeddingService()),
    )

    memory = EpisodicMemory(
        embedding_config={
            "provider": "tongyi",
            "model": "text-embedding-v4",
            "dimensions": 1536,
        },
        use_mock_embeddings=False,
        vector_store_backend="zvec",
        vector_store_path=tmp_path / "zvec",
    )

    await memory.index_chapter_outcome(
        chapter_number=1,
        event_summary="服务实际返回优先。",
        full_text="服务实际返回优先。",
    )

    assert memory._vector_store.dimension == 4


def test_rebuild_vector_store_uses_persisted_embedding_dimension(
    monkeypatch,
    tmp_path,
) -> None:
    install_fake_zvec(monkeypatch)

    memory = EpisodicMemory(
        embedding_config={"provider": "tencent", "model": "hunyuan-embedding"},
        use_mock_embeddings=True,
        vector_store_backend="zvec",
        vector_store_path=tmp_path / "zvec",
    )

    memory.deserialize_outline_data({
        "outline_index": {
            "outline:test": {
                "chapter_number": 1,
                "plot_point": "已经持久化的三维向量。",
                "embedding": [0.1, 0.2, 0.3],
            },
        },
        "chapter_outlines": {"1": ["outline:test"]},
    })

    restored = memory.rebuild_vector_store()

    assert restored == 1
    assert memory._vector_store.dimension == 3


def test_rebuild_vector_store_clears_empty_persisted_memory(
    monkeypatch,
    tmp_path,
) -> None:
    install_fake_zvec(monkeypatch)

    memory = EpisodicMemory(
        embedding_config={"model": "fake-embedding", "dimensions": 8},
        use_mock_embeddings=True,
        vector_store_backend="zvec",
        vector_store_path=tmp_path / "zvec",
    )
    memory._vector_store.add("stale", [0.1] * 8, {"chapter": 1})

    restored = memory.rebuild_vector_store()

    assert restored == 0
    assert memory._vector_store.search([0.1] * 8, top_k=1) == []


@pytest.mark.asyncio
async def test_embedding_cache_is_scoped_by_provider_model_and_dimension(
    monkeypatch,
    tmp_path,
) -> None:
    install_fake_zvec(monkeypatch)

    class FakeEmbeddingService:
        def __init__(self) -> None:
            self.calls = 0

        async def generate_embedding(self, text: str):
            self.calls += 1
            return SimpleNamespace(embedding=[float(self.calls), 0.0, 0.0])

    service = FakeEmbeddingService()
    monkeypatch.setattr(
        EmbeddingService,
        "create_from_config",
        classmethod(lambda cls, config: service),
    )

    memory = EpisodicMemory(
        embedding_config={"provider": "provider-a", "model": "model-a", "dimensions": 3},
        use_mock_embeddings=False,
        vector_store_backend="zvec",
        vector_store_path=tmp_path / "zvec",
    )

    first = await memory._generate_embedding("同一段文本")
    second = await memory._generate_embedding("同一段文本")
    assert first == second
    assert service.calls == 1

    memory._embedding_config["model"] = "model-b"
    third = await memory._generate_embedding("同一段文本")

    assert third != first
    assert service.calls == 2


@pytest.mark.asyncio
async def test_batch_embedding_preserves_order_and_populates_cache(
    monkeypatch,
    tmp_path,
) -> None:
    install_fake_zvec(monkeypatch)

    class FakeEmbeddingService:
        def __init__(self) -> None:
            self.batch_calls = 0

        async def generate_embedding(self, text: str):
            raise AssertionError("single embedding should not be used")

        async def generate_batch(self, texts: list[str]):
            self.batch_calls += 1
            return [
                SimpleNamespace(embedding=[float(idx), float(len(text)), 1.0])
                for idx, text in enumerate(texts, start=1)
            ]

    service = FakeEmbeddingService()
    monkeypatch.setattr(
        EmbeddingService,
        "create_from_config",
        classmethod(lambda cls, config: service),
    )

    memory = EpisodicMemory(
        embedding_config={"provider": "provider-a", "model": "model-a", "dimensions": 3},
        use_mock_embeddings=False,
        vector_store_backend="zvec",
        vector_store_path=tmp_path / "zvec",
    )

    embeddings = await memory._generate_embeddings_batch(["甲", "乙乙", "丙丙丙"])
    assert embeddings == [[1.0, 1.0, 1.0], [2.0, 2.0, 1.0], [3.0, 3.0, 1.0]]
    assert service.batch_calls == 1
    assert memory._embedding_stats["last_batch_size"] == 3

    cached = await memory._generate_embeddings_batch(["甲", "乙乙", "丙丙丙"])
    assert cached == embeddings
    assert service.batch_calls == 1
    assert memory._embedding_stats["cache_hits"] >= 3


@pytest.mark.asyncio
async def test_batch_embedding_chunks_to_provider_limit(monkeypatch, tmp_path) -> None:
    install_fake_zvec(monkeypatch)

    class FakeEmbeddingService:
        max_batch_size = 2

        def __init__(self) -> None:
            self.batch_sizes: list[int] = []

        async def generate_embedding(self, text: str):
            raise AssertionError(f"single embedding should not be used: {text}")

        async def generate_batch(self, texts: list[str]):
            self.batch_sizes.append(len(texts))
            return [SimpleNamespace(embedding=[float(len(text)), 0.0, 1.0]) for text in texts]

    service = FakeEmbeddingService()
    monkeypatch.setattr(
        EmbeddingService,
        "create_from_config",
        classmethod(lambda cls, config: service),
    )
    memory = EpisodicMemory(
        embedding_config={"provider": "tongyi", "model": "text-embedding-v4", "dimensions": 3},
        use_mock_embeddings=False,
        vector_store_backend="zvec",
        vector_store_path=tmp_path / "zvec",
    )

    embeddings = await memory._generate_embeddings_batch(["甲", "乙乙", "丙丙丙", "丁丁丁丁", "戊戊戊戊戊"])

    assert service.batch_sizes == [2, 2, 1]
    assert [embedding[0] for embedding in embeddings] == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert memory._embedding_stats["batch_calls"] == 3
    assert memory._embedding_stats["last_batch_size"] == 1


@pytest.mark.asyncio
async def test_batch_embedding_failure_falls_back_to_single_without_mock(
    monkeypatch,
    tmp_path,
) -> None:
    install_fake_zvec(monkeypatch)

    class FakeEmbeddingService:
        def __init__(self) -> None:
            self.batch_calls = 0
            self.single_calls = 0

        async def generate_batch(self, texts: list[str]):
            self.batch_calls += 1
            raise RuntimeError("batch down")

        async def generate_embedding(self, text: str):
            self.single_calls += 1
            return SimpleNamespace(embedding=[float(len(text)), 0.0, 1.0])

    service = FakeEmbeddingService()
    monkeypatch.setattr(
        EmbeddingService,
        "create_from_config",
        classmethod(lambda cls, config: service),
    )

    memory = EpisodicMemory(
        embedding_config={"provider": "provider-a", "model": "model-a", "dimensions": 3},
        use_mock_embeddings=False,
        vector_store_backend="zvec",
        vector_store_path=tmp_path / "zvec",
    )

    embeddings = await memory._generate_embeddings_batch(["甲", "乙乙"])

    assert embeddings == [[1.0, 0.0, 1.0], [2.0, 0.0, 1.0]]
    assert service.batch_calls == 1
    assert service.single_calls == 2
    assert memory._embedding_stats["batch_fallbacks"] == 1
