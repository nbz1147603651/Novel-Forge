"""Versioned Zvec retrieval for narrative evidence.

This module is deliberately a retrieval-only boundary.  It indexes source
projections, returns bounded evidence cards, and never resolves identity,
conflict, or truth from a score.  Those semantic decisions belong to LLM
adjudication tasks.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol

from novel_forge.core.parsing.token_utils import estimate_chinese_tokens
from novel_forge.memory.vector_store import VectorStore, create_vector_store
from novel_forge.narrative_state.evidence_contracts import (
    RetrievalEvidenceCard,
    RetrievalEvidencePack,
    evidence_content_hash,
)

_EVIDENCE_KIND_CODES = {
    "entity": 1,
    "accepted_state": 2,
    "event": 3,
    "claim": 4,
    "relationship": 5,
    "knowledge": 6,
    "motif": 7,
    "adjudication": 8,
}


def _vector_record_id(card_id: str) -> str:
    """Map an opaque logical card id to a Zvec-safe physical document id."""

    # Zvec rejects ':' and document ids longer than 64 characters.  A 192-bit
    # digest keeps the physical id compact while retaining ample collision
    # resistance for a project-scoped evidence collection.
    digest = sha256(str(card_id).encode("utf-8")).hexdigest()[:48]
    return f"card_{digest}"


class EvidenceEmbeddingProvider(Protocol):
    """The small public embedding surface required by the evidence index."""

    async def embed_texts(self, texts: list[str]) -> list[list[float]]: ...

    @property
    def embedding_signature(self) -> str: ...


@dataclass(frozen=True)
class EvidenceIndexStats:
    """Operational counters; none of these values is a semantic decision."""

    indexed_cards: int = 0
    updated_cards: int = 0
    removed_cards: int = 0
    retrieved_cards: int = 0
    rebuilt_cards: int = 0


class NarrativeEvidenceIndex:
    """Persistent, incrementally updated Zvec index of evidence cards."""

    _MANIFEST_VERSION = 4

    def __init__(
        self,
        *,
        root: Path,
        embedding_provider: EvidenceEmbeddingProvider,
        backend: str = "zvec",
        index_type: str = "hnsw",
        memory_limit_mb: int = 512,
    ) -> None:
        self.root = Path(root)
        self.embedding_provider = embedding_provider
        self.backend = str(backend or "zvec").strip().lower().replace("-", "_")
        self.index_type = str(index_type or "hnsw")
        self.memory_limit_mb = int(memory_limit_mb or 512)
        self._store: VectorStore | None = None
        self._dimension = 0
        self._embedding_signature = ""
        self._cards: dict[str, RetrievalEvidenceCard] = {}
        self._loaded = False
        self._reset_store_on_open = False
        self._lock = asyncio.Lock()

    @property
    def manifest_path(self) -> Path:
        return self.root / "evidence_cards.json"

    @property
    def collection_path(self) -> Path:
        return self.root / "zvec"

    async def upsert_cards(self, cards: list[RetrievalEvidenceCard]) -> EvidenceIndexStats:
        """Embed and upsert changed source cards without clearing the collection."""
        return await self.sync_cards(cards)

    async def sync_cards(
        self,
        cards: list[RetrievalEvidenceCard],
        *,
        replace_kinds: set[str] | None = None,
    ) -> EvidenceIndexStats:
        """Synchronize a source projection and optionally prune managed kinds.

        Pruning is driven only by an explicit authoritative projection.  Vector
        similarity is never used to decide which records are obsolete.
        """
        async with self._lock:
            self._load_manifest()
            unique = self._normalize_cards(cards)
            managed = {str(kind) for kind in replace_kinds or set()}
            removed = [
                card_id
                for card_id, card in self._cards.items()
                if managed and card.kind in managed and card_id not in unique
            ]
            merged = {
                card_id: card
                for card_id, card in self._cards.items()
                if card_id not in removed
            }
            merged.update(unique)
            changed = [
                card
                for card_id, card in unique.items()
                if self._cards.get(card_id) != card
            ]
            if self._reset_store_on_open and merged:
                changed = sorted(merged.values(), key=lambda item: item.card_id)
            rebuilt_cards = 0
            if changed:
                vectors = await self.embedding_provider.embed_texts(
                    [self._embedding_text(card) for card in changed]
                )
                if len(vectors) != len(changed):
                    raise RuntimeError("evidence embedding result count mismatch")
                current_signature = self._current_embedding_signature(len(vectors[0]))
                if self._requires_rebuild(vectors[0], current_signature=current_signature):
                    all_cards = sorted(merged.values(), key=lambda item: item.card_id)
                    if [card.card_id for card in changed] != [
                        card.card_id for card in all_cards
                    ]:
                        changed = all_cards
                        vectors = await self.embedding_provider.embed_texts(
                            [self._embedding_text(card) for card in changed]
                        )
                        if len(vectors) != len(changed):
                            raise RuntimeError("evidence rebuild embedding result count mismatch")
                    store = self._ensure_store(vectors[0], reset_existing=True)
                    rebuilt_cards = len(changed)
                else:
                    store = self._ensure_store(vectors[0])
                store.add_many(
                    [
                        (
                            _vector_record_id(card.card_id),
                            vector,
                            self._vector_metadata(card),
                        )
                        for card, vector in zip(changed, vectors, strict=True)
                    ]
                )
                self._embedding_signature = self._current_embedding_signature(len(vectors[0]))
                self._reset_store_on_open = False
            for card_id in removed:
                if self._store is not None:
                    self._store.remove(_vector_record_id(card_id))
            if removed and self._store is None:
                # The persistent collection may still contain these IDs. Rebuild
                # lazily so stale vectors cannot crowd valid bounded results.
                self._reset_store_on_open = True
            self._cards = merged
            self._save_manifest()
            self.flush()
            return EvidenceIndexStats(
                indexed_cards=len(self._cards),
                updated_cards=len(changed),
                removed_cards=len(removed),
                rebuilt_cards=rebuilt_cards,
            )

    async def remove_cards(self, card_ids: list[str]) -> EvidenceIndexStats:
        """Remove explicitly superseded cards; pruning is never inferred from score."""
        async with self._lock:
            self._load_manifest()
            clean_ids = {str(card_id or "").strip() for card_id in card_ids}
            removed = [card_id for card_id in clean_ids if card_id in self._cards]
            for card_id in removed:
                self._cards.pop(card_id, None)
                if self._store is not None:
                    self._store.remove(_vector_record_id(card_id))
            if removed:
                if self._store is None:
                    self._reset_store_on_open = True
                self._save_manifest()
                self.flush()
            return EvidenceIndexStats(
                indexed_cards=len(self._cards),
                removed_cards=len(removed),
            )

    async def has_cards(self) -> bool:
        async with self._lock:
            self._load_manifest()
            return bool(self._cards)

    async def has_kind(self, kind: str) -> bool:
        """Return whether the manifest contains at least one card of ``kind``."""
        clean_kind = str(kind or "").strip()
        if not clean_kind:
            return False
        async with self._lock:
            self._load_manifest()
            return any(card.kind == clean_kind for card in self._cards.values())

    async def retrieve(
        self,
        *,
        query: str,
        max_visible_chapter: int,
        top_k: int = 8,
        kinds: set[str] | None = None,
    ) -> list[RetrievalEvidenceCard]:
        """Return ranked candidates without scores or semantic labels."""
        clean_query = str(query or "").strip()
        if not clean_query or top_k <= 0:
            return []
        async with self._lock:
            self._load_manifest()
            if not self._cards:
                return []
            vectors = await self.embedding_provider.embed_texts([clean_query])
            if not vectors:
                return []
            store = await self._ensure_compatible_store(vectors[0])
            # chapter 0 denotes a global source and is visible to every task.
            retrieval_filter: dict[str, Any] = {
                "chapter": {"$lte": max(0, int(max_visible_chapter))}
            }
            if kinds:
                kind_codes = [
                    _EVIDENCE_KIND_CODES[kind]
                    for kind in sorted(kinds)
                    if kind in _EVIDENCE_KIND_CODES
                ]
                if not kind_codes:
                    return []
                retrieval_filter["kind_code"] = {"$in": kind_codes}
            matches = store.hybrid_search(
                vectors[0],
                clean_query,
                top_k=top_k,
                filter=retrieval_filter,
            )
            allowed_kinds = {str(item) for item in kinds} if kinds else None
            result: list[RetrievalEvidenceCard] = []
            seen: set[str] = set()
            logical_ids = {
                _vector_record_id(card_id): card_id for card_id in self._cards
            }
            for vector_id, _score, _metadata in matches:
                card_id = logical_ids.get(vector_id, vector_id)
                card = self._cards.get(card_id)
                if card is None or card.card_id in seen:
                    continue
                if card.chapter_number and card.chapter_number > max_visible_chapter:
                    continue
                if allowed_kinds is not None and card.kind not in allowed_kinds:
                    continue
                seen.add(card.card_id)
                result.append(card)
                if len(result) >= top_k:
                    break
            return result

    async def build_pack(
        self,
        *,
        purpose: str,
        query: str,
        canon_revision: str,
        max_visible_chapter: int,
        source_hashes: list[str] | None = None,
        candidate_limit: int = 24,
        evidence_token_budget: int = 2400,
        kinds: set[str] | None = None,
    ) -> RetrievalEvidencePack:
        """Build a provenance-preserving, token-budgeted LLM evidence view.

        The budget applies only to Zvec-ranked historical cards.  P0 authority
        remains in ChapterSourceSlice and is deliberately not duplicated in
        retrieval packs; downstream stage routers must not truncate this pack.
        """
        clean_candidate_limit = max(0, int(candidate_limit))
        clean_token_budget = max(0, int(evidence_token_budget))
        candidates = await self.retrieve(
            query=query,
            max_visible_chapter=max_visible_chapter,
            top_k=clean_candidate_limit + 1,
            kinds=kinds,
        )
        candidate_window = candidates[:clean_candidate_limit]
        has_more_candidates = len(candidates) > clean_candidate_limit
        cards: list[RetrievalEvidenceCard] = []
        used_tokens = 0
        for card in candidate_window:
            card_tokens = _estimate_evidence_card_tokens(card)
            if card_tokens > clean_token_budget - used_tokens:
                continue
            cards.append(card)
            used_tokens += card_tokens
        omitted_count = len(candidate_window) - len(cards) + int(has_more_candidates)
        clean_source_hashes = _text_list(
            [*(source_hashes or []), *(card.source_hash for card in cards)]
        )
        stable_payload = json.dumps(
            {
                "purpose": purpose,
                "query": query,
                "revision": canon_revision,
                "chapter": max_visible_chapter,
                "source_hashes": clean_source_hashes,
                "candidate_limit": clean_candidate_limit,
                "evidence_token_budget": clean_token_budget,
                "cards": [
                    {"card_id": card.card_id, "content_hash": card.content_hash}
                    for card in cards
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return RetrievalEvidencePack(
            pack_id=sha256(stable_payload.encode("utf-8")).hexdigest()[:24],
            purpose=purpose,
            query=query,
            canon_revision=canon_revision,
            max_visible_chapter=max_visible_chapter,
            source_hashes=clean_source_hashes,
            evidence_cards=cards,
            candidate_limit=clean_candidate_limit,
            evidence_token_budget=clean_token_budget,
            estimated_evidence_tokens=used_tokens,
            retrieved_candidate_count=len(candidate_window),
            omitted_candidate_count_lower_bound=omitted_count,
            has_more_evidence=omitted_count > 0,
        )

    def _load_manifest(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            raw = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._reset_store_on_open = True
            return
        except (OSError, json.JSONDecodeError):
            self._reset_store_on_open = True
            return
        if not isinstance(raw, dict) or int(raw.get("version") or 0) not in {
            1,
            3,
            self._MANIFEST_VERSION,
        }:
            self._reset_store_on_open = True
            return
        if int(raw.get("version") or 0) < self._MANIFEST_VERSION:
            self._reset_store_on_open = True
        if bool(raw.get("requires_rebuild")):
            self._reset_store_on_open = True
        self._dimension = int(raw.get("dimension") or 0)
        self._embedding_signature = str(raw.get("embedding_signature") or "").strip()
        cards = raw.get("cards")
        if not isinstance(cards, list):
            return
        for item in cards:
            try:
                card = RetrievalEvidenceCard.model_validate(item)
            except (TypeError, ValueError):
                continue
            self._cards[card.card_id] = card
        if self.backend == "in_memory" and self._cards:
            self._reset_store_on_open = True

    def _save_manifest(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self._MANIFEST_VERSION,
            "dimension": self._dimension,
            "embedding_signature": self._embedding_signature,
            "requires_rebuild": self._reset_store_on_open,
            "cards": [
                card.model_dump(mode="json")
                for card in sorted(self._cards.values(), key=lambda item: item.card_id)
            ],
        }
        temp_path = self.manifest_path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temp_path, self.manifest_path)

    def _ensure_store(
        self,
        vector: list[float],
        *,
        reset_existing: bool = False,
    ) -> VectorStore:
        dimension = len(vector)
        if dimension <= 0:
            raise ValueError("evidence embedding vector must not be empty")
        if self._store is not None and not reset_existing:
            if self._dimension != dimension:
                raise ValueError("evidence embedding dimension changed; rebuild the evidence index")
            return self._store
        self._dimension = dimension
        path = self.collection_path if self.backend == "zvec" else None
        self._store = create_vector_store(
            backend=self.backend,
            path=path,
            dimension=dimension,
            index_type=self.index_type,
            memory_limit_mb=self.memory_limit_mb,
            extra_int_fields=("kind_code",),
            reset_existing=reset_existing,
        )
        return self._store

    async def _ensure_compatible_store(self, query_vector: list[float]) -> VectorStore:
        current_signature = self._current_embedding_signature(len(query_vector))
        if self._requires_rebuild(query_vector, current_signature=current_signature):
            cards = sorted(self._cards.values(), key=lambda item: item.card_id)
            vectors = await self.embedding_provider.embed_texts(
                [self._embedding_text(card) for card in cards]
            )
            if len(vectors) != len(cards):
                raise RuntimeError("evidence rebuild embedding result count mismatch")
            sample = vectors[0] if vectors else query_vector
            store = self._ensure_store(sample, reset_existing=True)
            if cards:
                store.add_many(
                    [
                        (
                            _vector_record_id(card.card_id),
                            vector,
                            self._vector_metadata(card),
                        )
                        for card, vector in zip(cards, vectors, strict=True)
                    ]
                )
            self._embedding_signature = self._current_embedding_signature(len(sample))
            self._reset_store_on_open = False
            self._save_manifest()
            self.flush()
            return store
        if not self._embedding_signature:
            self._embedding_signature = current_signature
            self._save_manifest()
        return self._ensure_store(query_vector)

    def _requires_rebuild(
        self,
        vector: list[float],
        *,
        current_signature: str,
    ) -> bool:
        return bool(
            self._reset_store_on_open
            or (self._dimension and self._dimension != len(vector))
            or (
                self._cards
                and (
                    not self._embedding_signature
                    or self._embedding_signature != current_signature
                )
            )
        )

    def _current_embedding_signature(self, dimension: int) -> str:
        raw = getattr(self.embedding_provider, "embedding_signature", "")
        if callable(raw):
            raw = raw()
        signature = str(raw or "").strip()
        if signature:
            return signature
        provider_type = type(self.embedding_provider)
        fallback = f"{provider_type.__module__}.{provider_type.__qualname__}:{dimension}"
        return sha256(fallback.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_cards(
        cards: list[RetrievalEvidenceCard],
    ) -> dict[str, RetrievalEvidenceCard]:
        unique: dict[str, RetrievalEvidenceCard] = {}
        for raw_card in cards:
            if not raw_card.card_id or not raw_card.excerpt:
                continue
            expected_hash = evidence_content_hash(
                kind=raw_card.kind,
                source_ref=raw_card.source_ref,
                excerpt=raw_card.excerpt,
            )
            card = (
                raw_card
                if raw_card.content_hash == expected_hash
                else raw_card.model_copy(update={"content_hash": expected_hash})
            )
            unique[card.card_id] = card
        return unique

    def flush(self) -> None:
        store = self._store
        flush = getattr(store, "flush", None)
        if callable(flush):
            flush()

    @staticmethod
    def _embedding_text(card: RetrievalEvidenceCard) -> str:
        return "\n".join(
            part
            for part in (
                card.kind,
                card.source_ref,
                " ".join(card.entity_ids),
                card.canonical_name,
                card.excerpt,
            )
            if part
        )

    @staticmethod
    def _vector_metadata(card: RetrievalEvidenceCard) -> dict[str, Any]:
        return {
            "content": NarrativeEvidenceIndex._embedding_text(card),
            "chapter": card.chapter_number,
            "kind_code": _EVIDENCE_KIND_CODES[card.kind],
            "kind": card.kind,
            "authority": card.authority,
            "source_ref": card.source_ref,
            "canon_revision": card.canon_revision,
            "source_hash": card.source_hash,
            "entity_ids": list(card.entity_ids),
            "canonical_name": card.canonical_name,
            "content_hash": card.content_hash,
        }


class NarrativeEvidenceService:
    """Facade used by pipeline stages; it never exposes Zvec scores to LLMs."""

    def __init__(self, index: NarrativeEvidenceIndex) -> None:
        self._index = index

    async def index_cards(self, cards: list[RetrievalEvidenceCard]) -> EvidenceIndexStats:
        return await self._index.upsert_cards(cards)

    async def remove_cards(self, card_ids: list[str]) -> EvidenceIndexStats:
        return await self._index.remove_cards(card_ids)

    async def has_cards(self) -> bool:
        return await self._index.has_cards()

    async def has_kind(self, kind: str) -> bool:
        return await self._index.has_kind(kind)

    async def sync_narrative_state(
        self,
        *,
        entity_registry: Any,
        ledger_entries: list[Any],
        replace: bool = False,
    ) -> EvidenceIndexStats:
        """Index already-adjudicated state without making semantic decisions."""
        cards, _revision = narrative_state_evidence_cards(
            entity_registry=entity_registry,
            ledger_entries=ledger_entries,
        )
        if replace:
            return await self._index.sync_cards(
                cards,
                replace_kinds={"entity", "accepted_state"},
            )
        return await self._index.upsert_cards(cards)

    async def evidence_pack(self, **kwargs: Any) -> RetrievalEvidencePack:
        return await self._index.build_pack(**kwargs)

    def flush(self) -> None:
        self._index.flush()


def narrative_state_evidence_cards(
    *,
    entity_registry: Any,
    ledger_entries: list[Any],
) -> tuple[list[RetrievalEvidenceCard], str]:
    """Project accepted narrative-state records into retrievable evidence.

    The input is already downstream of LLM final adjudication.  This function
    only serializes that authority and performs no state or identity decision.
    """
    registry = _as_mapping(entity_registry)
    registry_entities = registry.get("entities") if isinstance(registry, dict) else []
    registry_entities = registry_entities if isinstance(registry_entities, list) else []
    serializable_entries = [_as_mapping(entry) for entry in ledger_entries]
    registry_revision = _stable_hash(registry_entities)
    ledger_revision = _stable_hash(serializable_entries)
    revision = _stable_hash(
        {"registry_revision": registry_revision, "ledger_revision": ledger_revision}
    )
    cards: list[RetrievalEvidenceCard] = []
    for raw in registry_entities:
        if not isinstance(raw, dict):
            continue
        entity_id = _text(raw.get("entity_id"))
        name = _text(raw.get("name"))
        if not entity_id or not name:
            continue
        aliases = _text_list(raw.get("aliases"))
        excerpt = "；".join(
            part
            for part in (
                f"规范实体：{name}",
                f"实体类型：{_text(raw.get('entity_type')) or 'unknown'}",
                f"来源别称：{'、'.join(aliases)}" if aliases else "",
            )
            if part
        )
        cards.append(
            RetrievalEvidenceCard(
                card_id=f"entity:{entity_id}",
                kind="entity",
                source_ref=f"narrative_state/entity_registry/{entity_id}",
                excerpt=excerpt,
                entity_ids=[entity_id],
                canonical_name=name,
                authority="accepted",
                canon_revision=registry_revision[:24],
                source_hash=registry_revision,
            )
        )
    for entry in serializable_entries:
        if not isinstance(entry, dict) or _text(entry.get("evidence_status")) not in {"", "active"}:
            continue
        entry_id = _text(entry.get("entry_id"))
        summary = _text(entry.get("summary"))
        if not entry_id or not summary:
            continue
        state_update = _as_mapping(entry.get("state_update"))
        entity_ids = _state_entry_entity_ids(state_update)
        compact_update = {
            key: state_update[key]
            for key in ("state_path", "value", "target", "plot_thread_id", "relationship_pair")
            if key in state_update and state_update[key] not in (None, "", [], {})
        }
        details = json.dumps(compact_update, ensure_ascii=False, sort_keys=True, default=str)
        evidence_quotes = _active_evidence_quotes(entry.get("evidence"))
        entry_revision = _stable_hash(entry)
        excerpt_parts = [f"已裁定状态：{summary}"]
        if details:
            excerpt_parts.append(f"更新：{details}")
        if evidence_quotes:
            excerpt_parts.append(f"原文证据：{'；'.join(evidence_quotes)}")
        cards.append(
            RetrievalEvidenceCard(
                card_id=f"state:{entry_id}",
                kind="accepted_state",
                source_ref=f"narrative_state/state_ledger/{entry_id}",
                excerpt="；".join(excerpt_parts),
                chapter_number=max(0, int(entry.get("chapter_number") or 0)),
                entity_ids=entity_ids,
                authority="accepted",
                canon_revision=entry_revision[:24],
                source_hash=_text(entry.get("source_text_hash")) or entry_revision,
            )
        )
    return cards, revision


def _state_entry_entity_ids(state_update: dict[str, Any]) -> list[str]:
    for key in (
        "entity_ids",
        "present_characters",
        "entity_id",
        "character_ids",
        "participants",
    ):
        values = _text_list(state_update.get(key))
        if values:
            return values
    pair = state_update.get("relationship_pair")
    if isinstance(pair, dict):
        return _text_list(list(pair.values()))
    return _text_list(pair)


def _active_evidence_quotes(value: Any) -> list[str]:
    items = value if isinstance(value, list) else []
    quotes: list[str] = []
    for item in items:
        data = _as_mapping(item)
        if data.get("found") is False:
            continue
        quote = _text(data.get("quote"))
        if quote and quote not in quotes:
            quotes.append(quote[:160])
        if len(quotes) >= 2:
            break
    return quotes


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        return dumped if isinstance(dumped, dict) else {}
    return {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _text_list(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    result: list[str] = []
    for item in values:
        text = _text(item)
        if text and text not in result:
            result.append(text)
    return result


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return sha256(payload.encode("utf-8")).hexdigest()


def _estimate_evidence_card_tokens(card: RetrievalEvidenceCard) -> int:
    """Estimate the rendered cost of one immutable evidence card."""

    rendered = "；".join(
        part
        for part in (
            card.kind,
            card.source_ref,
            card.excerpt,
            "、".join(card.entity_ids),
            card.canonical_name,
            card.authority,
        )
        if part
    )
    return max(1, estimate_chinese_tokens(rendered))


__all__ = [
    "EvidenceEmbeddingProvider",
    "EvidenceIndexStats",
    "NarrativeEvidenceIndex",
    "NarrativeEvidenceService",
    "narrative_state_evidence_cards",
]
