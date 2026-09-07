"""Fake Zvec vector store for humanize library tests.

Implements the same interface as ``ZvecVectorStore`` with in-memory storage
and optional failure injection via ``raise_on_*`` flags.
"""

from __future__ import annotations

import math
from typing import Any


class FakeZvecStore:
    """In-memory vector store mimicking ZvecVectorStore interface."""

    backend_name = "fake"
    vector_field = "embedding"

    def __init__(
        self,
        *,
        raise_on_add: bool = False,
        raise_on_search: bool = False,
        raise_on_remove: bool = False,
    ) -> None:
        self._vectors: dict[str, list[float]] = {}
        self._metadata: dict[str, dict[str, Any]] = {}
        self.raise_on_add = raise_on_add
        self.raise_on_search = raise_on_search
        self.raise_on_remove = raise_on_remove

    def add(self, id: str, vector: list[float], metadata: dict[str, Any]) -> None:
        if self.raise_on_add:
            raise RuntimeError("FakeZvecStore: add failed")
        self._vectors[str(id)] = list(vector)
        self._metadata[str(id)] = dict(metadata)

    def add_many(self, items: list[tuple[str, list[float], dict[str, Any]]]) -> None:
        for id, vector, metadata in items:
            self.add(id, vector, metadata)

    def remove(self, id: str) -> None:
        if self.raise_on_remove:
            raise RuntimeError("FakeZvecStore: remove failed")
        self._vectors.pop(str(id), None)
        self._metadata.pop(str(id), None)

    def clear(self) -> None:
        self._vectors.clear()
        self._metadata.clear()

    def search(
        self,
        query_vector: list[float],
        top_k: int = 5,
        filter: dict[str, Any] | None = None,
    ) -> list[tuple[str, float, dict[str, Any]]]:
        if self.raise_on_search:
            raise RuntimeError("FakeZvecStore: search failed")
        if not query_vector or top_k <= 0:
            return []

        results: list[tuple[str, float, dict[str, Any]]] = []
        for vid, vec in self._vectors.items():
            if len(vec) != len(query_vector):
                continue
            sim = _cosine_similarity(query_vector, vec)
            meta = self._metadata.get(vid, {})
            if filter:
                match = all(meta.get(k) == v for k, v in filter.items())
                if not match:
                    continue
            results.append((vid, sim, meta))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def hybrid_search(
        self,
        query_vector: list[float],
        query_text: str,
        top_k: int = 5,
        filter: dict[str, Any] | None = None,
    ) -> list[tuple[str, float, dict[str, Any]]]:
        vector_results = {
            vid: (score, metadata)
            for vid, score, metadata in self.search(
                query_vector,
                top_k=max(top_k, len(self._vectors)),
                filter=filter,
            )
        }
        query_terms = _tokenize(query_text)
        results: list[tuple[str, float, dict[str, Any]]] = []
        for vid in self._vectors:
            if filter:
                meta = self._metadata.get(vid, {})
                if not all(meta.get(k) == v for k, v in filter.items()):
                    continue
            vector_score, metadata = vector_results.get(
                vid,
                (0.0, self._metadata.get(vid, {})),
            )
            content_terms = _tokenize(str(metadata.get("content", "")))
            text_score = (
                len(query_terms & content_terms) / len(query_terms | content_terms)
                if query_terms and content_terms
                else 0.0
            )
            results.append((vid, 0.75 * vector_score + 0.25 * text_score, metadata))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def flush(self) -> None:
        pass

    @property
    def count(self) -> int:
        return len(self._vectors)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _tokenize(text: str) -> set[str]:
    return {token.lower() for token in text.split() if token.strip()}
