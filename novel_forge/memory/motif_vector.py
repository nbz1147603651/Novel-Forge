"""MotifVectorBridge — vector-backed semantic retrieval for motifs."""

from __future__ import annotations

import logging
import re
from typing import Any

from novel_forge.memory.vector_store import InMemoryVectorStore, VectorStore

_log = logging.getLogger("memory.motif_vector")


class MotifVectorBridge:
    """Bridge between motif domain and vector-backed semantic retrieval."""

    def __init__(
        self,
        *,
        episodic_memory: Any | None = None,
        vector_store_backend: str = "in_memory",
    ) -> None:
        self._episodic = episodic_memory
        self._backend = vector_store_backend
        self._motif_registry: dict[str, dict[str, Any]] = {}
        self._vector_store: VectorStore | None = None
        self._vector_store_backend_name: str = "uninitialized"

        if vector_store_backend == "in_memory":
            self._vector_store = InMemoryVectorStore()
            self._vector_store_backend_name = "in_memory"

    @property
    def is_available(self) -> bool:
        return self._episodic is not None and self._vector_store is not None

    @property
    def indexed_count(self) -> int:
        return len(self._motif_registry)

    async def index_motif(self, motif_id: str, thematic_meaning: str) -> None:
        if not motif_id:
            return
        embedding = await self._generate_embedding(thematic_meaning)
        if embedding is None or self._vector_store is None:
            return
        self._motif_registry[motif_id] = {"thematic_meaning": thematic_meaning}
        self._vector_store.add(
            motif_id,
            embedding,
            {
                "content": thematic_meaning,
                "motif_id": motif_id,
                "thematic_meaning": thematic_meaning,
            },
        )

    async def search_similar(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        if not query or not self._vector_store:
            return []
        embedding = await self._generate_embedding(query)
        if embedding is None:
            return []
        results = self._vector_store.hybrid_search(embedding, query, top_k=top_k)
        return [
            {"motif_id": id, "thematic_meaning": meta.get("thematic_meaning", ""), "score": score}
            for id, score, meta in results
        ]

    async def compute_similarity(self, text_a: str, text_b: str) -> float:
        if not text_a or not text_b:
            return 0.0
        emb_a = await self._generate_embedding(text_a)
        emb_b = await self._generate_embedding(text_b)
        if emb_a is not None and emb_b is not None:
            return self._cosine_similarity(emb_a, emb_b)
        return self._token_jaccard(text_a, text_b)

    async def _generate_embedding(self, text: str) -> list[float] | None:
        if not self._episodic:
            return None
        try:
            # Use the public embed_texts API to avoid coupling to private methods.
            if hasattr(self._episodic, "embed_texts"):
                results = await self._episodic.embed_texts([text])
                if results and len(results) > 0:
                    return results[0]
                return None
            # Fallback: try _generate_embedding for backward compatibility with
            # older EpisodicMemory instances that may not expose embed_texts.
            if hasattr(self._episodic, "_generate_embedding"):
                return await self._episodic._generate_embedding(text)
        except Exception as exc:
            _log.debug("embedding generation failed: %s", exc)
        return None

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        if len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return max(0.0, min(1.0, dot / (norm_a * norm_b)))

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        return set(re.findall(r"[\w\u4e00-\u9fff]+", text.lower()))

    @classmethod
    def _token_jaccard(cls, text_a: str, text_b: str) -> float:
        tokens_a = cls._tokenize(text_a)
        tokens_b = cls._tokenize(text_b)
        if not tokens_a or not tokens_b:
            return 0.0
        intersection = tokens_a & tokens_b
        union = tokens_a | tokens_b
        return len(intersection) / len(union) if union else 0.0


class MotifSemanticMatcher:
    """Semantic matching service for motifs."""

    def __init__(self, bridge: MotifVectorBridge, threshold: float = 0.6) -> None:
        self._bridge = bridge
        self._threshold = threshold

    @property
    def threshold(self) -> float:
        return self._threshold

    @property
    def is_vector_available(self) -> bool:
        return self._bridge.is_available

    async def match_motif_to_goal(self, motif_thematic_meaning: str, chapter_goal: str) -> float:
        if not motif_thematic_meaning or not chapter_goal:
            return 0.0
        return await self._bridge.compute_similarity(motif_thematic_meaning, chapter_goal)

    async def find_related_motifs(
        self, chapter_text: str, candidate_motifs: list[dict[str, Any]], top_k: int = 3
    ) -> list[dict[str, Any]]:
        if not chapter_text or not candidate_motifs:
            return []
        scored: list[dict[str, Any]] = []
        for candidate in candidate_motifs:
            thematic_meaning = candidate.get("thematic_meaning", "")
            if not thematic_meaning:
                continue
            similarity = await self._bridge.compute_similarity(chapter_text, thematic_meaning)
            if similarity >= self._threshold:
                scored.append({**candidate, "similarity": similarity})
        scored.sort(key=lambda x: x["similarity"], reverse=True)
        return scored[:top_k]
