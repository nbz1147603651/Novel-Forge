"""Optional ZVec semantic enhancement layer for StoryKernel.

Provides vector-backed semantic search over StoryKernel entities,
knowledge ledgers, and motif protocols.  When disabled (or when the
zvec dependency is missing), all search methods return empty lists
so callers never need a guard.

Config toggle: ``NOVEL_FORGE_STORY_KERNEL_ZVEC_ENABLED`` (env var or
``Settings.story_kernel_zvec_enabled``).
"""

from __future__ import annotations

import logging
import os
from typing import Callable

from novel_forge.memory.zvec_store import ZvecVectorStore
from novel_forge.story_kernel.schemas import (
    Entity,
    KnowledgeLedger,
    MotifProtocol,
    StoryKernel,
)

_log = logging.getLogger(__name__)

# Type alias for the embedding function.
EmbedFn = Callable[[str], list[float]]


def _text_from_entity(entity: Entity) -> str:
    """Build a searchable text representation of an Entity."""
    parts = [entity.name, entity.notes]
    parts.extend(entity.aliases)
    return " ".join(p for p in parts if p)


def _text_from_knowledge(entry: KnowledgeLedger) -> str:
    """Build a searchable text representation of a KnowledgeLedger entry."""
    return f"{entry.fact} {entry.notes}".strip()


def _text_from_motif(motif: MotifProtocol) -> str:
    """Build a searchable text representation of a MotifProtocol."""
    parts = [motif.motif_name, motif.description, motif.notes]
    parts.extend(motif.examples)
    return " ".join(p for p in parts if p)


class StoryKernelZVec:
    """Optional vector-backed semantic search over StoryKernel data.

    Three independent ZvecVectorStore instances index entities, knowledge
    entries, and motif protocols respectively.  A Python-side mapping
    (``_entity_map``, etc.) links store IDs back to the original Pydantic
    objects so that search results are fully-typed.

    Parameters
    ----------
    path:
        Base directory for the three Zvec collections.
    dimension:
        Vector dimension (must match the ``embed_fn`` output).
    embed_fn:
        ``Callable[[str], list[float]]`` — converts a text string to a
        dense vector.  In tests you can pass a deterministic hash-based
        function; in production use the gateway embedding service.
    enabled:
        When ``False`` the layer is a no-op (search returns ``[]``,
        ``index_kernel`` does nothing).
    """

    def __init__(
        self,
        path: str,
        dimension: int = 128,
        embed_fn: EmbedFn | None = None,
        enabled: bool | None = None,
    ) -> None:
        if enabled is None:
            raw = os.environ.get("NOVEL_FORGE_STORY_KERNEL_ZVEC_ENABLED", "").strip().lower()
            enabled = raw in {"1", "true", "yes"} if raw else True

        self._enabled = enabled
        self._dimension = dimension
        self._embed_fn = embed_fn
        self._entity_store: ZvecVectorStore | None = None
        self._knowledge_store: ZvecVectorStore | None = None
        self._motif_store: ZvecVectorStore | None = None
        self._entity_map: dict[str, Entity] = {}
        self._knowledge_map: dict[str, KnowledgeLedger] = {}
        self._motif_map: dict[str, MotifProtocol] = {}

        if not self._enabled:
            return

        self._entity_store = ZvecVectorStore(path=f"{path}/entities", dimension=dimension)
        self._knowledge_store = ZvecVectorStore(path=f"{path}/knowledge", dimension=dimension)
        self._motif_store = ZvecVectorStore(path=f"{path}/motifs", dimension=dimension)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def index_kernel(self, kernel: StoryKernel) -> None:
        """Index all searchable data from a StoryKernel.

        Clears previous index entries before re-indexing.
        """
        if not self._enabled:
            return

        self._index_entities(kernel.entities)
        self._index_knowledge(kernel.knowledge_ledger)
        self._index_motifs(kernel.motif_protocols)

    def search_entities(self, query: str, top_k: int = 5) -> list[Entity]:
        """Semantic search over indexed entities."""
        if not self._enabled or not self._entity_store or not self._embed_fn:
            return []
        vector = self._embed_fn(query)
        results = self._entity_store.hybrid_search(vector, query, top_k=top_k)
        return [self._entity_map[rid] for rid, _, _ in results if rid in self._entity_map]

    def search_knowledge(
        self,
        query: str,
        top_k: int = 5,
        entity_ids: list[str] | None = None,
    ) -> list[KnowledgeLedger]:
        """Semantic search over indexed knowledge entries."""
        if not self._enabled or not self._knowledge_store or not self._embed_fn:
            return []
        vector = self._embed_fn(query)
        results = self._knowledge_store.hybrid_search(vector, query, top_k=top_k)
        if entity_ids is not None:
            entity_set = set(entity_ids)
            results = [
                (rid, score, meta)
                for rid, score, meta in results
                if meta.get("entity_id") in entity_set
            ]
        return [self._knowledge_map[rid] for rid, _, _ in results if rid in self._knowledge_map]

    def search_motifs(self, text: str, top_k: int = 3) -> list[MotifProtocol]:
        """Detect motifs that match the given text."""
        if not self._enabled or not self._motif_store or not self._embed_fn:
            return []
        vector = self._embed_fn(text)
        results = self._motif_store.hybrid_search(vector, text, top_k=top_k)
        return [self._motif_map[rid] for rid, _, _ in results if rid in self._motif_map]

    # ------------------------------------------------------------------
    # Internal indexing helpers
    # ------------------------------------------------------------------

    def _index_entities(self, entities: list[Entity]) -> None:
        assert self._entity_store is not None
        assert self._embed_fn is not None
        self._entity_store.clear()
        self._entity_map.clear()
        for entity in entities:
            text = _text_from_entity(entity)
            if not text.strip():
                continue
            vector = self._embed_fn(text)
            self._entity_store.add(
                entity.entity_id,
                vector,
                {
                    "content": text,
                    "chapter": entity.source_chapter,
                    "scene_index": 0,
                    "is_outline": False,
                },
            )
            self._entity_map[entity.entity_id] = entity

    def _index_knowledge(self, entries: list[KnowledgeLedger]) -> None:
        assert self._knowledge_store is not None
        assert self._embed_fn is not None
        self._knowledge_store.clear()
        self._knowledge_map.clear()
        for entry in entries:
            text = _text_from_knowledge(entry)
            if not text.strip():
                continue
            vector = self._embed_fn(text)
            self._knowledge_store.add(
                entry.entry_id,
                vector,
                {
                    "content": text,
                    "chapter": entry.source_chapter,
                    "scene_index": 0,
                    "is_outline": False,
                    "entity_id": entry.entity_id,
                },
            )
            self._knowledge_map[entry.entry_id] = entry

    def _index_motifs(self, motifs: list[MotifProtocol]) -> None:
        assert self._motif_store is not None
        assert self._embed_fn is not None
        self._motif_store.clear()
        self._motif_map.clear()
        for motif in motifs:
            text = _text_from_motif(motif)
            if not text.strip():
                continue
            vector = self._embed_fn(text)
            self._motif_store.add(
                motif.entry_id,
                vector,
                {
                    "content": text,
                    "chapter": motif.last_used_chapter,
                    "scene_index": len(motif.occurrences),
                    "is_outline": False,
                },
            )
            self._motif_map[motif.entry_id] = motif


__all__ = ["StoryKernelZVec", "EmbedFn"]
