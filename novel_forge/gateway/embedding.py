"""Embedding Service — generates vector embeddings for text.

Supports multiple providers:
- OpenAI (text-embedding-3-small, text-embedding-3-large)
- Azure OpenAI
- Local models via Ollama
- Other OpenAI-compatible APIs

Usage:
    from novel_forge.gateway.embedding import EmbeddingService
    
    service = EmbeddingService(
        provider="openai",
        api_key="your-api-key",
        model="text-embedding-3-small"
    )
    
    embedding = await service.generate_embedding("Hello, world!")
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from typing import Any, Optional

from novel_forge.core.exceptions import ModelGatewayError
from novel_forge.core.local_model_resources import (
    LocalMemoryClass,
    LocalResourcePriority,
    LocalResourceRequest,
    get_local_model_resource_broker,
)
from novel_forge.gateway.client_lifecycle import close_async_client


@dataclass
class EmbeddingResult:
    """Result of an embedding generation request."""

    embedding: list[float]
    model: str
    input_tokens: int
    latency_ms: float
    cost_usd: float = 0.0


class EmbeddingService:
    """Unified embedding service supporting multiple providers."""

    # OpenAI embedding models and their pricing (per 1K tokens)
    OPENAI_PRICING = {
        "text-embedding-3-small": 0.00002,
        "text-embedding-3-large": 0.00013,
        "text-embedding-ada-002": 0.0001,
    }

    # Tongyi (DashScope) embedding prices (per 1K tokens, approx. USD at ~7.2 CNY/USD)
    # Official CNY rate: v4/v3 = 0.0005 CNY/1K, v2 = 0.0007 CNY/1K
    TONGYI_PRICING = {
        "text-embedding-v4": 0.00007,
        "text-embedding-v3": 0.00007,
        "text-embedding-v2": 0.0001,
    }

    # Volcengine Ark embedding prices (per 1K tokens, approx. USD at ~7.2 CNY/USD)
    # doubao-embedding-vision: 0.0005 CNY/1K tokens
    VOLCENGINE_ARK_PRICING: dict[str, float] = {
        "doubao-embedding-vision": 0.00007,
        "doubao-embedding-large": 0.00007,
    }

    # Models that support the ``dimensions`` parameter
    # (OpenAI text-embedding-3-* and Tongyi text-embedding-v3/v4)
    _SUPPORTS_DIMENSIONS: frozenset[str] = frozenset({
        "text-embedding-3-small",
        "text-embedding-3-large",
        "text-embedding-v3",
        "text-embedding-v4",
        "doubao-embedding-vision",
    })

    # Default output dimensions per model.
    # text-embedding-v4 supports 2048/1536/1024(default)/768/512/256/128/64
    # text-embedding-v3 supports 1024(default)/768/512/256/128/64
    MODEL_DIMENSIONS = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
        "text-embedding-v4": 1024,
        "text-embedding-v3": 1024,
        "text-embedding-v2": 1536,
        "hunyuan-embedding": 1024,
        "nomic-embed-text": 768,
        "mxbai-embed-large": 1024,
        "all-minilm": 384,
        "qwen3-embedding:0.6b": 1024,
        "doubao-embedding-vision": 1024,
    }

    # Provider-specific default base URLs for embedding (OpenAI-compatible).
    # Providers not listed here require an explicit ``base_url`` parameter.
    _PROVIDER_EMBEDDING_DEFAULTS: dict[str, str] = {
        "tongyi": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "tencent": "https://api.hunyuan.cloud.tencent.com/v1",
        "volcengine_ark": "https://ark.cn-beijing.volces.com/api/plan/v3",
        "siliconflow": "https://api.siliconflow.cn/v1",
        "deepseek": "https://api.deepseek.com",
        "kimi": "https://api.moonshot.cn",
        "minimax": "https://api.minimaxi.com/v1",
    }
    _PROVIDER_BATCH_LIMITS: dict[str, int] = {
        # DashScope text embeddings reject input batches larger than 10.
        "tongyi": 10,
    }

    @classmethod
    def default_dimensions_for_model(cls, model: str, default: int = 1536) -> int:
        """Return the default output dimension for a known embedding model."""
        key = str(model or "").strip().lower()
        if not key:
            return default
        if key in cls.MODEL_DIMENSIONS:
            return cls.MODEL_DIMENSIONS[key]
        # Ollama model IDs are often stored as "model:tag".
        base_key = key.split(":", 1)[0]
        return cls.MODEL_DIMENSIONS.get(base_key, default)

    def __init__(
        self,
        provider: str = "openai",
        api_key: Optional[str] = None,
        model: str = "text-embedding-3-small",
        base_url: Optional[str] = None,
        dimensions: Optional[int] = None,
        max_batch_size: Optional[int] = None,
        resource_wait_timeout_s: float = 900.0,
    ) -> None:
        """Initialize embedding service.

        Args:
            provider: Provider name ("openai", "azure", "ollama", "custom")
            api_key: API key for the provider
            model: Model name to use
            base_url: Custom base URL (for compatible APIs)
            dimensions: Output dimensions (for models that support it)
        """
        self._provider = provider
        self._api_key = api_key
        self._model = model
        self._base_url = base_url
        self._dimensions = dimensions
        provider_key = str(provider or "").strip().lower()
        default_batch_limit = self._PROVIDER_BATCH_LIMITS.get(provider_key, 128)
        self._max_batch_size = max(1, int(max_batch_size or default_batch_limit))
        self._resource_wait_timeout_s = max(10.0, resource_wait_timeout_s)

        self._client: Any | None = None
        self._client_lock = threading.Lock()

    @property
    def max_batch_size(self) -> int:
        """Maximum number of texts accepted by one provider batch request."""
        return self._max_batch_size

    def _get_client(self) -> Any:
        """Lazily create and reuse a single client instance."""
        if self._client is not None:
            return self._client

        try:
            import openai  # noqa: F811
        except ImportError as exc:
            raise ModelGatewayError(
                "openai package not installed. Run: pip install novel-forge[openai]"
            ) from exc

        if self._provider == "openai" or self._provider == "custom":
            with self._client_lock:
                if self._client is not None:
                    return self._client
                self._client = openai.AsyncOpenAI(
                    api_key=self._api_key,
                    base_url=self._base_url,
                    timeout=30.0,
                )
                return self._client

        elif self._provider == "ollama":
            with self._client_lock:
                if self._client is not None:
                    return self._client
                self._client = openai.AsyncOpenAI(
                    api_key="ollama",
                    base_url=self._base_url or "http://localhost:11434/v1",
                    timeout=30.0,
                )
                return self._client

        elif self._provider in self._PROVIDER_EMBEDDING_DEFAULTS or self._base_url:
            default_url = self._PROVIDER_EMBEDDING_DEFAULTS.get(self._provider)
            base = self._base_url or default_url
            if not base:
                raise ValueError(
                    f"Embedding provider '{self._provider}' requires a base_url. "
                    "Set it in the model profile or environment configuration."
                )
            if not self._api_key:
                raise ModelGatewayError(
                    f"{self._provider} embedding requires api_key"
                )
            with self._client_lock:
                if self._client is not None:
                    return self._client
                self._client = openai.AsyncOpenAI(
                    api_key=self._api_key,
                    base_url=base,
                    timeout=30.0,
                )
                return self._client

        else:
            raise ValueError(f"Unsupported provider: {self._provider}")

    async def generate_embedding(
        self,
        text: str,
        model: Optional[str] = None,
    ) -> EmbeddingResult:
        """Generate embedding for text.

        Args:
            text: Text to embed
            model: Override model name (optional)

        Returns:
            EmbeddingResult with vector and metadata
        """
        import time

        model = model or self._model
        client = self._get_client()

        start = time.monotonic()

        # Build embedding request
        create_kwargs: dict[str, Any] = {
            "model": model,
            "input": text,
        }

        # Add dimensions if supported
        if self._dimensions and model in self._SUPPORTS_DIMENSIONS:
            create_kwargs["dimensions"] = self._dimensions

        try:
            if self._provider == "ollama":
                async with get_local_model_resource_broker().lease(
                    self._ollama_resource_request(model, len(text))
                ):
                    response = await client.embeddings.create(**create_kwargs)
            else:
                response = await client.embeddings.create(**create_kwargs)
        except Exception as exc:
            raise ModelGatewayError(f"Embedding API error: {exc}") from exc

        elapsed = (time.monotonic() - start) * 1000

        # Extract result
        embedding_data = response.data[0]
        embedding = embedding_data.embedding
        usage = response.usage

        input_tokens = usage.prompt_tokens if usage else 0

        # Calculate cost
        cost_usd = 0.0
        if model in self.OPENAI_PRICING:
            cost_usd = (input_tokens / 1000) * self.OPENAI_PRICING[model]
        elif model in self.TONGYI_PRICING:
            cost_usd = (input_tokens / 1000) * self.TONGYI_PRICING[model]
        elif model in self.VOLCENGINE_ARK_PRICING:
            cost_usd = (input_tokens / 1000) * self.VOLCENGINE_ARK_PRICING[model]

        return EmbeddingResult(
            embedding=embedding,
            model=model,
            input_tokens=input_tokens,
            latency_ms=round(elapsed, 2),
            cost_usd=round(cost_usd, 6),
        )

    async def generate_batch(
        self,
        texts: list[str],
        model: Optional[str] = None,
    ) -> list[EmbeddingResult]:
        """Generate embeddings for multiple texts.

        Args:
            texts: List of texts to embed
            model: Override model name (optional)

        Returns:
            List of EmbeddingResult objects
        """
        import time

        model = model or self._model
        client = self._get_client()

        start = time.monotonic()

        # Build batch embedding request
        create_kwargs: dict[str, Any] = {
            "model": model,
            "input": texts,
        }

        if self._dimensions and model in self._SUPPORTS_DIMENSIONS:
            create_kwargs["dimensions"] = self._dimensions

        try:
            if self._provider == "ollama":
                async with get_local_model_resource_broker().lease(
                    self._ollama_resource_request(model, sum(map(len, texts)))
                ):
                    response = await client.embeddings.create(**create_kwargs)
            else:
                response = await client.embeddings.create(**create_kwargs)
        except Exception as exc:
            raise ModelGatewayError(f"Embedding API error: {exc}") from exc

        elapsed = (time.monotonic() - start) * 1000

        # Process results
        results: list[EmbeddingResult] = []
        usage = response.usage

        for item in response.data:
            results.append(EmbeddingResult(
                embedding=item.embedding,
                model=model,
                input_tokens=0,  # Individual token count not available in batch
                latency_ms=round(elapsed, 2),
                cost_usd=0.0,
            ))

        # Distribute total tokens proportionally
        if usage and usage.prompt_tokens > 0:
            total_chars = sum(len(t) for t in texts)
            _pricing = (
                self.OPENAI_PRICING
                if model in self.OPENAI_PRICING
                else self.TONGYI_PRICING
                if model in self.TONGYI_PRICING
                else self.VOLCENGINE_ARK_PRICING
            )
            for i, result in enumerate(results):
                char_ratio = len(texts[i]) / total_chars if total_chars > 0 else 0
                result.input_tokens = int(usage.prompt_tokens * char_ratio)
                if model in _pricing:
                    result.cost_usd = round((result.input_tokens / 1000) * _pricing[model], 6)

        return results

    def generate_mock_embedding(self, text: str, dimensions: int = 1536) -> list[float]:
        """Generate a mock embedding based on text hash.

        This is for development/testing when no API key is available.

        Args:
            text: Text to embed
            dimensions: Output vector dimensions

        Returns:
            Pseudo-embedding vector
        """
        # Use SHA256 hash to generate deterministic pseudo-random vector
        h = hashlib.sha256(text.encode("utf-8")).digest()

        # Extend hash if needed for larger dimensions
        while len(h) < dimensions * 4:
            h += hashlib.sha256(h).digest()

        # Convert bytes to floats in [-1, 1]
        embedding = []
        for i in range(dimensions):
            byte_val = h[i % len(h)]
            embedding.append((byte_val - 128) / 128.0)

        return embedding

    def _ollama_resource_request(self, model: str, text_units: int) -> LocalResourceRequest:
        return LocalResourceRequest(
            workload="local_embedding",
            label=f"Ollama 向量 · {model} · {max(1, text_units)} 字符",
            memory_class=LocalMemoryClass.MEDIUM,
            accelerator=True,
            cpu_heavy=True,
            priority=LocalResourcePriority.BACKGROUND,
            timeout_s=self._resource_wait_timeout_s,
        )

    async def health_check(self) -> bool:
        """Check if embedding service is healthy."""
        try:
            # Try to generate a simple embedding
            await self.generate_embedding("test")
            return True
        except Exception:
            return False

    async def aclose(self) -> None:
        """Release async client resources."""
        client = self._client
        self._client = None
        if client is not None:
            await close_async_client(client)

    async def shutdown(self) -> None:
        """Alias for ``aclose`` for consistency with adapter cleanup hooks."""
        await self.aclose()

    @classmethod
    def create_from_config(
        cls,
        config: dict[str, Any],
    ) -> EmbeddingService:
        """Create embedding service from configuration dictionary.

        Config format:
        {
            "provider": "openai",
            "api_key": "sk-...",
            "model": "text-embedding-3-small",
            "base_url": null,  # Optional custom URL
            "dimensions": 512,  # Optional output dimensions
        }

        Args:
            config: Configuration dictionary

        Returns:
            Configured EmbeddingService instance
        """
        return cls(
            provider=config.get("provider", "openai"),
            api_key=config.get("api_key"),
            model=config.get("model", "text-embedding-3-small"),
            base_url=config.get("base_url"),
            dimensions=config.get("dimensions"),
            max_batch_size=config.get("max_batch_size"),
            resource_wait_timeout_s=float(config.get("resource_wait_timeout_s", 900.0)),
        )
