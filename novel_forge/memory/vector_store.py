"""Vector store backends for episodic memory retrieval."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol

_log = logging.getLogger(__name__)


class VectorStore(Protocol):
    """Minimal vector-store interface used by ``EpisodicMemory``."""

    backend_name: str

    def add(self, id: str, vector: list[float], metadata: dict[str, Any]) -> None:
        """Add or replace a vector with metadata."""

    def add_many(self, items: list[tuple[str, list[float], dict[str, Any]]]) -> None:
        """Add or replace multiple vectors with metadata."""

    def clear(self) -> None:
        """Remove all vectors from the store."""

    def remove(self, id: str) -> None:
        """Remove one vector by id."""

    def search(
        self,
        query_vector: list[float],
        top_k: int = 5,
        filter: dict[str, Any] | None = None,
    ) -> list[tuple[str, float, dict[str, Any]]]:
        """Search by vector similarity."""

    def text_search(
        self,
        query_text: str,
        top_k: int = 5,
        filter: dict[str, Any] | None = None,
    ) -> list[tuple[str, float, dict[str, Any]]]:
        """Search by full-text relevance when the backend supports it."""

    def hybrid_search(
        self,
        query_vector: list[float],
        query_text: str,
        top_k: int = 5,
        filter: dict[str, Any] | None = None,
        vector_weight: float = 0.75,
        text_weight: float = 0.25,
    ) -> list[tuple[str, float, dict[str, Any]]]:
        """Search by backend-native hybrid vector + text relevance."""


class InMemoryVectorStore:
    """Simple in-memory vector store for tests and mock semantic search.

    This backend is intentionally tiny and dependency-free. It is not a
    production persistence path; real project memory should use Zvec.
    """

    backend_name = "in_memory"

    def __init__(self) -> None:
        self._vectors: dict[str, list[float]] = {}
        self._metadata: dict[str, dict[str, Any]] = {}

    def add(self, id: str, vector: list[float], metadata: dict[str, Any]) -> None:
        """Add a vector with metadata."""
        self._vectors[id] = vector
        self._metadata[id] = dict(metadata)

    def add_many(self, items: list[tuple[str, list[float], dict[str, Any]]]) -> None:
        """Add multiple vectors with metadata."""
        for id, vector, metadata in items:
            self.add(id, vector, metadata)

    def clear(self) -> None:
        """Clear all vectors and metadata."""
        self._vectors.clear()
        self._metadata.clear()

    def remove(self, id: str) -> None:
        """Remove a vector and metadata by id."""
        self._vectors.pop(id, None)
        self._metadata.pop(id, None)

    def search(
        self,
        query_vector: list[float],
        top_k: int = 5,
        filter: dict[str, Any] | None = None,
    ) -> list[tuple[str, float, dict[str, Any]]]:
        """Search by similarity to query vector.

        Returns list of (id, score, metadata) tuples where score is cosine
        similarity and higher is better.
        """
        if not query_vector or not self._vectors or top_k <= 0:
            return []

        query_norm = sum(x * x for x in query_vector) ** 0.5
        if query_norm == 0:
            return []

        scores: list[tuple[str, float, dict[str, Any]]] = []
        for id, vec in self._vectors.items():
            metadata = self._metadata[id]
            if filter and not matches_filter(metadata, filter):
                continue
            score = self._cosine_similarity_with_query_norm(query_vector, query_norm, vec)
            scores.append((id, score, metadata))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    def text_search(
        self,
        query_text: str,
        top_k: int = 5,
        filter: dict[str, Any] | None = None,
    ) -> list[tuple[str, float, dict[str, Any]]]:
        """Search by token overlap against metadata content in mock mode."""
        terms = _tokenize_text(query_text)
        if not terms or top_k <= 0:
            return []
        scores: list[tuple[str, float, dict[str, Any]]] = []
        for id, metadata in self._metadata.items():
            if filter and not matches_filter(metadata, filter):
                continue
            score = _metadata_text_score(metadata, terms)
            if score <= 0:
                continue
            scores.append((id, score, metadata))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    def hybrid_search(
        self,
        query_vector: list[float],
        query_text: str,
        top_k: int = 5,
        filter: dict[str, Any] | None = None,
        vector_weight: float = 0.75,
        text_weight: float = 0.25,
    ) -> list[tuple[str, float, dict[str, Any]]]:
        """Search by combined cosine and metadata text overlap in mock mode."""
        if not query_vector or top_k <= 0:
            return []
        terms = _tokenize_text(query_text)
        query_norm = sum(x * x for x in query_vector) ** 0.5
        if query_norm == 0:
            return []
        vector_w, text_w = _normalize_weights(vector_weight, text_weight)
        scores: list[tuple[str, float, dict[str, Any]]] = []
        for id, vec in self._vectors.items():
            metadata = self._metadata[id]
            if filter and not matches_filter(metadata, filter):
                continue
            vector_score = self._cosine_similarity_with_query_norm(query_vector, query_norm, vec)
            text_score = _metadata_text_score(metadata, terms) if terms else 0.0
            scores.append((id, vector_w * vector_score + text_w * text_score, metadata))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if not a or not b:
            return 0.0
        norm_a = sum(x * x for x in a) ** 0.5
        if norm_a == 0:
            return 0.0
        return self._cosine_similarity_with_query_norm(a, norm_a, b)

    def _cosine_similarity_with_query_norm(
        self, a: list[float], norm_a: float, b: list[float]
    ) -> float:
        """Compute cosine similarity when the query norm is already known."""
        if not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_b == 0:
            return 0.0
        return float(dot / (norm_a * norm_b))


def matches_filter(metadata: dict[str, Any], filter: dict[str, Any]) -> bool:
    """Check whether metadata matches a small Mongo-like filter dictionary."""
    for key, value in filter.items():
        if key not in metadata:
            return False
        actual = metadata[key]
        if isinstance(value, dict):
            for op, expected in value.items():
                if op == "$gte" and actual < expected:
                    return False
                if op == "$lte" and actual > expected:
                    return False
                if op == "$gt" and actual <= expected:
                    return False
                if op == "$lt" and actual >= expected:
                    return False
                if op == "$ne" and actual == expected:
                    return False
                if op == "$in" and not _value_in(actual, expected):
                    return False
                if op == "$nin" and _value_in(actual, expected):
                    return False
                if op not in {"$gte", "$lte", "$gt", "$lt", "$ne", "$in", "$nin"}:
                    return False
        elif actual != value:
            return False
    return True


def _value_in(actual: Any, expected: Any) -> bool:
    if not isinstance(expected, (list, tuple, set)):
        expected = [expected]
    if isinstance(actual, (list, tuple, set)):
        return any(item in expected for item in actual)
    return actual in expected


def _tokenize_text(text: str) -> set[str]:
    return {part.lower() for part in str(text or "").split() if part.strip()}


def _metadata_text_score(metadata: dict[str, Any], terms: set[str]) -> float:
    if not terms:
        return 0.0
    text_parts: list[str] = []
    for value in metadata.values():
        if isinstance(value, (list, tuple, set)):
            text_parts.extend(str(item) for item in value if item)
        elif value:
            text_parts.append(str(value))
    haystack = " ".join(text_parts).lower()
    matched = sum(1 for term in terms if term in haystack)
    return matched / max(len(terms), 1)


def _normalize_weights(vector_weight: float, text_weight: float) -> tuple[float, float]:
    vector = max(0.0, float(vector_weight))
    text = max(0.0, float(text_weight))
    total = vector + text
    if total <= 0:
        return 0.75, 0.25
    return vector / total, text / total


def create_vector_store(
    *,
    backend: str = "zvec",
    path: str | Path | None = None,
    dimension: int = 1536,
    index_type: str = "hnsw",
    memory_limit_mb: int = 512,
    extra_int_fields: tuple[str, ...] = (),
    reset_existing: bool = False,
) -> VectorStore:
    """Create a vector store backend.

    Zvec is the production backend and fails fast when its dependency or
    project-scoped path is unavailable. ``in_memory`` is explicit test/mock
    infrastructure only.
    """
    backend_key = str(backend or "zvec").strip().lower().replace("-", "_")
    if backend_key not in {"zvec", "in_memory"}:
        raise ValueError(
            "unknown vector store backend "
            f"{backend!r}; expected 'zvec' or 'in_memory'. "
            "Set NOVEL_FORGE_MEMORY_VECTOR_STORE_BACKEND=zvec for production, "
            "or in_memory only for tests/mock embeddings."
        )

    if backend_key == "in_memory":
        return InMemoryVectorStore()

    if path is None:
        raise RuntimeError(
            "Zvec vector store requires a project-scoped collection path. "
            "Pass a storage-backed project_id, or set "
            "NOVEL_FORGE_MEMORY_VECTOR_STORE_BACKEND=in_memory only for tests/mock embeddings."
        )

    try:
        from novel_forge.memory.zvec_store import ZvecVectorStore

        return ZvecVectorStore(
            path=str(path),
            dimension=int(dimension),
            index_type=str(index_type or "hnsw"),
            memory_limit_mb=int(memory_limit_mb or 512),
            extra_int_fields=extra_int_fields,
            reset_existing=reset_existing,
        )
    except Exception as exc:
        message = str(exc)
        missing_dependency = (
            isinstance(exc, ModuleNotFoundError) or "No module named 'zvec'" in message
        )
        _log.error(
            "zvec_vector_store_unavailable | path=%s | dimension=%d | index_type=%s | error=%s",
            path,
            int(dimension),
            index_type,
            exc,
        )
        if missing_dependency:
            raise RuntimeError(
                "Zvec vector store could not be initialized because the zvec package "
                "is not importable. Install the zvec extra "
                "(`pip install -e '.[zvec]'` or `pip install zvec`). "
                f"path={path} dimension={dimension} index_type={index_type} error={exc}"
            ) from exc
        raise RuntimeError(
            "Zvec vector store collection could not be opened or created. The zvec "
            "package is available, but this project collection may be corrupt, locked, "
            "or incompatible with the requested embedding dimension/index type. "
            "Quarantine or rebuild the project memory collection before continuing. "
            f"path={path} dimension={dimension} index_type={index_type} error={exc}"
        ) from exc
