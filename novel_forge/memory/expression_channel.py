"""Vector-backed semantic memory for expression-channel observations."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from novel_forge.editorial.schemas import normalize_expression_channel_profiles
from novel_forge.memory.vector_store import VectorStore, create_vector_store
from novel_forge.narrative_state.schemas import (
    ExpressionChannelMemoryState,
    ExpressionObservation,
)
from novel_forge.obs.logger import get_logger

_log = get_logger("memory.expression_channel")

_EXTRA_INT_FIELDS = ("channel_hash", "actor_hash")
_MAX_QUERY_PROFILES = 8
_MAX_TOTAL_HITS = 6
_HIT_QUOTE_LIMIT = 60


class ExpressionChannelMemory:
    """Project-scoped expression observation metadata plus a Zvec vector collection."""

    def __init__(
        self,
        *,
        project_id: str,
        state_path: Path,
        vector_store_path: Path | None,
        embedding_source: Any,
        vector_store_backend: str = "zvec",
        zvec_index_type: str = "hnsw",
        zvec_memory_limit_mb: int = 512,
    ) -> None:
        self.project_id = project_id
        self.state_path = Path(state_path)
        self.vector_store_path = Path(vector_store_path) if vector_store_path else None
        self.embedding_source = embedding_source
        self.vector_store_backend = str(vector_store_backend or "zvec").strip().lower()
        self.zvec_index_type = zvec_index_type
        self.zvec_memory_limit_mb = int(zvec_memory_limit_mb or 512)
        self._vector_store: VectorStore | None = None
        self._vector_dimension = 0
        self._observations: dict[str, ExpressionObservation] = {}
        self._chapter_observations: dict[int, list[str]] = {}
        self._source_hashes: dict[int, str] = {}
        self._reset_vector_store_on_next_init = False
        self.load()

    @property
    def vector_store_backend_actual(self) -> str:
        if self._vector_store is None:
            return "uninitialized"
        return str(getattr(self._vector_store, "backend_name", "unknown") or "unknown")

    @property
    def observation_count(self) -> int:
        return len(self._observations)

    def load(self) -> bool:
        """Load JSON metadata and reopen the vector collection when possible."""

        if not self.state_path.exists():
            return False
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            state = ExpressionChannelMemoryState.model_validate(payload)
        except Exception as exc:
            _log.warning("expression_memory_load_failed | path=%s | error=%s", self.state_path, exc)
            return False

        self._observations = {item.observation_id: item for item in state.observations}
        self._chapter_observations = {}
        for obs in state.observations:
            self._chapter_observations.setdefault(obs.chapter_number, []).append(obs.observation_id)
        self._source_hashes = {
            int(ch): str(value)
            for ch, value in dict(state.source_hashes or {}).items()
            if str(ch).isdigit() and str(value).strip()
        }
        self._vector_dimension = int(state.vector_store.get("dimension", 0) or 0)
        if self._vector_dimension > 0:
            try:
                self._ensure_vector_store_for_dimension(self._vector_dimension)
                self._hydrate_vector_metadata()
            except Exception as exc:
                _log.debug("expression_memory_vector_reopen_failed | error=%s", exc)
        return True

    def save(self) -> bool:
        """Persist JSON metadata and flush the vector store."""

        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            state = ExpressionChannelMemoryState(
                project_id=self.project_id,
                vector_store={
                    "backend": self.vector_store_backend_actual,
                    "requested_backend": self.vector_store_backend,
                    "path": str(self.vector_store_path or ""),
                    "dimension": self._vector_dimension,
                    "index_type": self.zvec_index_type,
                    "vector_count": self._vector_count(),
                },
                source_hashes={str(ch): value for ch, value in sorted(self._source_hashes.items())},
                observations=list(self._observations.values()),
            )
            tmp_path = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
            tmp_path.write_text(
                json.dumps(state.model_dump(mode="json"), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp_path.replace(self.state_path)
            self.flush()
            return True
        except Exception as exc:
            _log.warning("expression_memory_save_failed | path=%s | error=%s", self.state_path, exc)
            return False

    def flush(self) -> None:
        if self._vector_store is None:
            return
        flush = getattr(self._vector_store, "flush", None)
        if callable(flush):
            try:
                flush()
            except Exception as exc:
                _log.debug("expression_memory_vector_flush_failed | error=%s", exc)

    async def replace_chapter_observations(
        self,
        *,
        chapter_number: int,
        source_text_hash: str,
        observations: list[ExpressionObservation],
    ) -> dict[str, Any]:
        """Replace one chapter's observations and vector entries."""

        self.delete_chapter(chapter_number)
        self._source_hashes[chapter_number] = source_text_hash
        clean_observations = [
            item for item in observations if item.chapter_number == chapter_number and item.quote
        ][:12]
        if not clean_observations:
            saved = self.save()
            return {"observations": 0, "vectors": 0, "saved": saved}

        embeddings = await self._generate_embeddings(
            [item.embedding_text for item in clean_observations]
        )
        if not embeddings:
            saved = self.save()
            return {"observations": len(clean_observations), "vectors": 0, "saved": saved}

        vector_store = self._ensure_vector_store_for_dimension(len(embeddings[0]))
        vector_items: list[tuple[str, list[float], dict[str, Any]]] = []
        for obs, embedding in zip(clean_observations, embeddings, strict=True):
            self._observations[obs.observation_id] = obs
            self._chapter_observations.setdefault(chapter_number, []).append(obs.observation_id)
            vector_items.append((obs.observation_id, embedding, self._vector_metadata(obs)))

        add_many = getattr(vector_store, "add_many", None)
        if callable(add_many):
            add_many(vector_items)
        else:
            for obs_id, embedding, metadata in vector_items:
                vector_store.add(obs_id, embedding, metadata)
        saved = self.save()
        return {
            "observations": len(clean_observations),
            "vectors": len(vector_items),
            "saved": saved,
        }

    def delete_chapter(self, chapter_number: int) -> int:
        """Delete all observations and vector entries for one chapter."""

        ids = list(self._chapter_observations.pop(chapter_number, []) or [])
        for obs_id in ids:
            self._observations.pop(obs_id, None)
            if self._vector_store is not None:
                try:
                    self._vector_store.remove(obs_id)
                except Exception as exc:
                    _log.debug("expression_vector_remove_failed | id=%s | error=%s", obs_id, exc)
        self._source_hashes.pop(chapter_number, None)
        return len(ids)

    def reset_all(self, *, reset_vectors: bool = True) -> None:
        """Clear expression observations before a full rebuild."""

        self._observations.clear()
        self._chapter_observations.clear()
        self._source_hashes.clear()
        if reset_vectors:
            self._vector_store = None
            self._vector_dimension = 0
            self._reset_vector_store_on_next_init = True

    async def search_expression_channel_memory(
        self,
        *,
        current_chapter: int,
        profiles: Any,
        chapter_context: str = "",
        cooldown_chapters: int = 3,
        top_k_per_profile: int = 2,
        max_hits: int = _MAX_TOTAL_HITS,
    ) -> list[dict[str, Any]]:
        """Return compact expression-channel records enriched with semantic evidence."""

        if self._vector_store is None or self._vector_dimension <= 0:
            return []
        profile_items = normalize_expression_channel_profiles(
            profiles,
            max_items=_MAX_QUERY_PROFILES,
            min_confidence=0.0,
        )
        if not profile_items:
            return []
        cooldown = max(0, int(cooldown_chapters or 0))
        start_chapter = max(1, int(current_chapter) - cooldown)
        end_chapter = max(0, int(current_chapter) - 1)
        if end_chapter <= 0:
            return []

        records: list[dict[str, Any]] = []
        for profile in profile_items:
            channel_id = str(profile.get("channel_id") or "").strip()
            if not channel_id:
                continue
            query = _profile_query(profile, chapter_context)
            query_vector = await self._generate_embedding(query)
            matches = self._vector_store.hybrid_search(
                query_vector,
                query,
                top_k=max(1, top_k_per_profile) * 4,
                filter={
                    "chapter": {"$gte": start_chapter, "$lte": end_chapter},
                    "channel_hash": _stable_hash_int(channel_id),
                },
            )
            hits: list[dict[str, Any]] = []
            max_similarity = 0.0
            last_seen = 0
            seen_quotes: set[str] = set()
            for obs_id, score, _metadata in matches:
                obs = self._observations.get(obs_id)
                if obs is None or obs.channel_id != channel_id:
                    continue
                if obs.quote in seen_quotes:
                    continue
                seen_quotes.add(obs.quote)
                max_similarity = max(max_similarity, float(score or 0.0))
                last_seen = max(last_seen, obs.chapter_number)
                hits.append(
                    {
                        "chapter": obs.chapter_number,
                        "scene_index": obs.scene_index,
                        "quote": _clip(obs.quote, _HIT_QUOTE_LIMIT),
                        "context": _clip(obs.trigger_context or obs.semantic_role, 36),
                        "similarity": round(float(score or 0.0), 3),
                    }
                )
                if len(hits) >= max(1, top_k_per_profile):
                    break
            if not hits:
                continue
            records.append(
                {
                    "text": str(profile.get("label") or channel_id),
                    "channel": str(profile.get("channel") or "other"),
                    "channel_id": channel_id,
                    "source": "expression_zvec",
                    "level": "soft",
                    "cooldown_chapters": int(profile.get("cooldown_chapters") or cooldown or 3),
                    "actor_scope": str(profile.get("actor_scope") or "global"),
                    "reason": str(profile.get("risk_reason") or profile.get("label") or ""),
                    "examples": list(profile.get("surface_forms", []) or [])[:4],
                    "surface_forms": list(profile.get("surface_forms", []) or [])[:8],
                    "trigger_contexts": list(profile.get("trigger_contexts", []) or [])[:4],
                    "replacement_axes": list(profile.get("replacement_axes", []) or [])[:4],
                    "allowed_when": str(profile.get("allowed_when") or ""),
                    "confidence": float(profile.get("confidence") or 0.0),
                    "provenance": "expression_zvec",
                    "recent_semantic_hits": hits,
                    "semantic_hit_count": len(hits),
                    "last_seen_chapter": last_seen,
                    "semantic_similarity_max": round(max_similarity, 3),
                }
            )
            if len(records) >= max_hits:
                break
        return records[:max_hits]

    async def _generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        source = self.embedding_source
        batch = getattr(source, "_generate_embeddings_batch", None)
        if callable(batch):
            return [list(item) for item in await batch(texts)]
        return [await self._generate_embedding(text) for text in texts]

    async def _generate_embedding(self, text: str) -> list[float]:
        source = self.embedding_source
        method = getattr(source, "_generate_embedding", None)
        if callable(method):
            return list(await method(text))
        raise RuntimeError("expression memory requires an embedding source")

    def _ensure_vector_store_for_dimension(self, dimension: int) -> VectorStore:
        dimension = int(dimension or 0)
        if dimension <= 0:
            raise ValueError("expression vector dimension must be positive")
        if self._vector_store is not None and self._reset_vector_store_on_next_init:
            self.flush()
            self._vector_store = None
        if self._vector_store is not None:
            if self._vector_dimension == dimension:
                return self._vector_store
            raise ValueError(
                f"expression vector dimension changed: {self._vector_dimension} -> {dimension}"
            )
        self._vector_dimension = dimension
        self._vector_store = create_vector_store(
            backend=self.vector_store_backend,
            path=self.vector_store_path,
            dimension=dimension,
            index_type=self.zvec_index_type,
            memory_limit_mb=self.zvec_memory_limit_mb,
            extra_int_fields=_EXTRA_INT_FIELDS,
            reset_existing=self._reset_vector_store_on_next_init,
        )
        self._reset_vector_store_on_next_init = False
        self._hydrate_vector_metadata()
        return self._vector_store

    def _hydrate_vector_metadata(self) -> int:
        if self._vector_store is None:
            return 0
        metadata = getattr(self._vector_store, "_metadata", None)
        if not isinstance(metadata, dict):
            return 0
        metadata.clear()
        for obs_id, obs in self._observations.items():
            metadata[obs_id] = self._vector_metadata(obs)
        return len(metadata)

    def _vector_metadata(self, obs: ExpressionObservation) -> dict[str, Any]:
        actor = obs.actor or obs.pov_character
        return {
            "content": " ".join(
                part
                for part in (
                    obs.embedding_text,
                    obs.quote,
                    obs.channel_id,
                    actor or "",
                )
                if part
            ),
            "chapter": obs.chapter_number,
            "scene_index": obs.scene_index,
            "is_outline": False,
            "channel_hash": _stable_hash_int(obs.channel_id),
            "actor_hash": _stable_hash_int(actor) if actor else 0,
            "channel_id": obs.channel_id,
            "actor": actor,
            "quote_hash": obs.quote_hash,
        }

    def _vector_count(self) -> int:
        if self._vector_store is None:
            return 0
        try:
            collection = getattr(self._vector_store, "_collection", None)
            stats = getattr(collection, "stats", None)
            return int(getattr(stats, "doc_count", 0) or 0)
        except Exception:
            return len(getattr(self._vector_store, "_metadata", {}) or {})


def _profile_query(profile: dict[str, Any], chapter_context: str) -> str:
    parts = [
        f"通道:{profile.get('label') or profile.get('channel_id')}",
        f"类型:{profile.get('channel') or 'other'}",
        f"触发:{'、'.join(str(item) for item in list(profile.get('trigger_contexts', []) or [])[:4])}",
        f"功能风险:{profile.get('risk_reason') or ''}",
        f"当前章节:{_clip(chapter_context, 160)}",
    ]
    return "；".join(part for part in parts if not part.endswith(":"))


def _stable_hash_int(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFFFFFFFFFFFFFF


def _clip(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."
