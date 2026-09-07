"""Tests for gateway embedding_config module."""

from __future__ import annotations

from novel_forge.gateway.embedding_config import (
    get_models_by_provider,
    get_supported_providers,
    is_embedding_model,
)


class TestEmbeddingConfig:

    def test_get_supported_providers(self) -> None:
        providers = get_supported_providers()
        assert isinstance(providers, list)
        assert len(providers) > 0

    def test_get_models_by_provider(self) -> None:
        models = get_models_by_provider("openai")
        assert isinstance(models, set)
        assert len(models) > 0

    def test_is_embedding_model_known(self) -> None:
        assert is_embedding_model("openai", "text-embedding-3-small") is True
        assert is_embedding_model("ollama", "bge-m3") is True
        assert is_embedding_model("ollama", "qwen3-embedding:8b") is True
        assert is_embedding_model("ollama", "qwen3:8b") is False

    def test_is_embedding_model_unknown(self) -> None:
        assert is_embedding_model("unknown_provider", "unknown_model") is False
