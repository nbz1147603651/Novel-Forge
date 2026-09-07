"""Read-only relationship query service — adjacency list over ``story_kernel``.

Task 2.2 of the memory-architecture-v3 plan. Provides three async read APIs
on top of the existing :class:`~novel_forge.story_kernel.store.StoryKernelStore`:

1. :meth:`RelationshipQueryService.get_relationships` — relationships for a
   single entity, optionally filtered by type.
2. :meth:`RelationshipQueryService.get_history` — pair-wise relationship
   evolution across chapter snapshots.
3. :meth:`RelationshipQueryService.get_neighbors` — BFS up to *depth* hops
   over the relationship graph.

**Read-only contract.** This service never calls ``add_relationship``,
``update_relationship``, ``delete_relationship`` or any other write
method on the backing store. The adjacency list cache is rebuilt on
demand via :meth:`refresh` (or implicitly by the public APIs when the
cache is empty for an entity).

**Duck-typed store.** The ``store`` parameter is intentionally typed
as ``Any`` so unit tests can pass an ``AsyncMock`` without dragging
SQLAlchemy + SQLite into the test process. The service only relies on
the following surface (any object exposing them is acceptable):

* ``async get_relationships(entity_id: str) -> list[Any]`` — each
  element must expose ``relationship_id``, ``source_entity_id``,
  ``target_entity_id``, ``relation_type``, ``label``, ``trust``,
  ``tension``, ``status``, ``established_chapter``,
  ``last_shift_chapter``, ``shift_summary`` and a ``model_dump`` method
  (or behave like a dict).
* ``def list_snapshots() -> list[int]`` — sorted chapter numbers with
  available snapshots.
* ``async load_snapshot(chapter: int) -> Any`` — kernel with
  ``.relationships`` attribute.
* ``async load_kernel(project_id: str) -> Any`` — current kernel.

**Cache discipline.** The internal ``_adjacency`` cache is bounded at
500 entity entries. On overflow, the oldest insertion is evicted (dicts
preserve insertion order in CPython 3.7+). This is *not* a true LRU —
re-reading an entity never moves it to the back — but it bounds memory
growth deterministically and keeps the implementation O(1) per insertion.

**BFS design.** :meth:`get_neighbors` performs a BFS over the cached
adjacency list, not over the store. Depth is the number of *hops* from
``entity_id``: ``depth=1`` returns only direct neighbors; ``depth=2``
adds friends-of-friends, etc. The result is returned as
``{entity_id: [neighbor_ids]}`` — only entities reachable from the
starting point are included.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Maximum number of entity entries in the in-memory adjacency cache.
#: When the cap is reached, the oldest insertion is evicted.
CACHE_CAPACITY: int = 500

#: Sentinel default depth for :meth:`RelationshipQueryService.get_neighbors`.
DEFAULT_NEIGHBOR_DEPTH: int = 2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialize_relationship(rel: Any) -> dict[str, Any]:
    """Convert a ``Relationship`` (or dict-like) into a JSON-safe dict.

    Pydantic models expose ``model_dump(mode="json")``; bare dicts are
    returned as-is. As a final defensive fallback, we copy the well-known
    attribute set — this guards against future schema evolution.
    """
    if isinstance(rel, dict):
        return dict(rel)
    model_dump = getattr(rel, "model_dump", None)
    if callable(model_dump):
        try:
            result: Any = model_dump(mode="json")
        except TypeError:
            result = model_dump()  # pragma: no cover - defensive
        if isinstance(result, dict):
            return result
        # Fall through to attribute copy if model_dump returned something weird.
    out: dict[str, Any] = {}
    for key in (
        "relationship_id",
        "source_entity_id",
        "target_entity_id",
        "relation_type",
        "label",
        "trust",
        "tension",
        "status",
        "established_chapter",
        "last_shift_chapter",
        "shift_summary",
        "notes",
    ):
        if hasattr(rel, key):
            out[key] = getattr(rel, key)
    return out


def _relation_type_str(value: Any) -> str:
    """Coerce a relation type (enum, string, etc.) to a lowercase string."""
    raw = getattr(value, "value", value)
    return str(raw or "").strip().lower()


def _build_pair_key(entity_a: str, entity_b: str) -> tuple[str, str]:
    """Return a sorted, canonical (a, b) pair key for cross-chapter lookups."""
    a, b = sorted((str(entity_a), str(entity_b)))
    return a, b


# ---------------------------------------------------------------------------
# RelationshipQueryService
# ---------------------------------------------------------------------------


class RelationshipQueryService:
    """Read-only adjacency list query service over ``story_kernel``.

    Parameters
    ----------
    project_id:
        The project this service is scoped to. Used to disambiguate
        logging and to load the current kernel when history queries
        need it as a fallback when no snapshots are available.
    store:
        Duck-typed read-only store. Defaults to ``None``; the service
        becomes a no-op until :meth:`bind_store` or :attr:`store` is
        set explicitly. The default value allows instantiation
        without a database (e.g. in unit tests).
    cache_capacity:
        Override the per-service adjacency cache size. Defaults to
        :data:`CACHE_CAPACITY`. Must be >= 1.
    """

    def __init__(
        self,
        project_id: str,
        store: Any = None,
        *,
        cache_capacity: int = CACHE_CAPACITY,
    ) -> None:
        if not project_id or not str(project_id).strip():
            raise ValueError("project_id must be a non-empty string.")
        if cache_capacity < 1:
            raise ValueError("cache_capacity must be >= 1.")
        self._project_id: str = str(project_id)
        self._store: Any = store
        self._cache_capacity: int = int(cache_capacity)

        # Main cache: entity_id -> list[serialized relationship dicts].
        # Each entry lists *all* relationships the entity participates
        # in (as either source or target).
        self._adjacency: dict[str, list[dict[str, Any]]] = {}

        # Track whether the cache has been populated at least once.
        # Used to know when to rebuild from the store.
        self._cache_loaded: bool = False

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def project_id(self) -> str:
        """Return the project ID this service is scoped to."""
        return self._project_id

    @property
    def store(self) -> Any:
        """Return the duck-typed store reference (read-only access)."""
        return self._store

    @property
    def cache_size(self) -> int:
        """Return the current number of entries in the adjacency cache."""
        return len(self._adjacency)

    @property
    def cache_capacity(self) -> int:
        """Return the maximum number of entries the cache can hold."""
        return self._cache_capacity

    @property
    def is_cache_loaded(self) -> bool:
        """Return True if :meth:`refresh` has been called at least once."""
        return self._cache_loaded

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def bind_store(self, store: Any) -> None:
        """Attach a (read-only) store reference.

        Invalidates the cache because the previous store may have had
        a different set of relationships.
        """
        if store is None:
            raise ValueError("store must not be None.")
        self._store = store
        self.invalidate()

    def invalidate(self, entity_id: str | None = None) -> None:
        """Clear one entry (or the whole cache) and mark it stale.

        Parameters
        ----------
        entity_id:
            If given, only the entry for that entity is removed.
            If ``None``, the entire cache is cleared and
            :attr:`is_cache_loaded` is reset to ``False``.
        """
        if entity_id is None:
            self._adjacency.clear()
            self._cache_loaded = False
            return
        # str() normalizes; dict.pop with default is a no-op if absent.
        self._adjacency.pop(str(entity_id), None)

    async def refresh(self) -> int:
        """Rebuild the entire adjacency cache from the backing store.

        This is the *only* method that does a project-wide load — for
        single-entity queries, the public APIs lazily populate the
        cache for the queried entity only. Call :meth:`refresh` if you
        want every entity to be queryable without further store hits.

        Returns
        -------
        int
            The number of entity entries now in the cache.
        """
        store = self._require_store()
        kernel: Any = None
        if hasattr(store, "load_kernel"):
            try:
                kernel = await store.load_kernel(self._project_id)
            except Exception as exc:  # pragma: no cover - defensive
                _logger.warning(
                    "refresh | load_kernel failed for project=%s: %s",
                    self._project_id,
                    exc,
                )
                kernel = None

        # Reset the cache and re-add every relationship we can see.
        self._adjacency.clear()

        if kernel is not None:
            rels = getattr(kernel, "relationships", []) or []
            for rel in rels:
                self._add_relationship_to_cache(rel)

        self._cache_loaded = True
        _logger.debug(
            "refresh | project=%s cache_size=%d",
            self._project_id,
            len(self._adjacency),
        )
        return len(self._adjacency)

    # ------------------------------------------------------------------
    # Public read APIs
    # ------------------------------------------------------------------

    async def get_relationships(
        self,
        entity_id: str,
        type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get all relationships involving *entity_id*.

        Parameters
        ----------
        entity_id:
            Pydantic-side entity identifier (e.g. ``"林远"``).
        type:
            Optional relationship type filter. If given, only
            relationships whose ``relation_type`` matches (case
            insensitive) are returned. Examples: ``"ally"``,
            ``"enemy"``, ``"family"``.

        Returns
        -------
        list[dict[str, Any]]
            Serialized relationship dicts. Empty list if the entity
            has no recorded relationships.
        """
        if not entity_id or not str(entity_id).strip():
            raise ValueError("entity_id must be a non-empty string.")

        entity_id = str(entity_id)
        await self._ensure_entity_cached(entity_id)
        rels = self._adjacency.get(entity_id, [])

        if type is None:
            return [dict(r) for r in rels]
        type_norm = str(type).strip().lower()
        return [dict(r) for r in rels if _relation_type_str(r.get("relation_type")) == type_norm]

    async def get_history(
        self,
        entity_a: str,
        entity_b: str,
    ) -> list[dict[str, Any]]:
        """Get relationship evolution between *entity_a* and *entity_b*.

        Walks every available chapter snapshot (and the current kernel
        as the final chapter) and collects the relationship between the
        two entities, in chapter order.

        Parameters
        ----------
        entity_a, entity_b:
            Entity IDs. Order does not matter — the relationship
            between A→B and B→A is treated as the same edge.

        Returns
        -------
        list[dict[str, Any]]
            Chronologically ordered relationship dicts, one per
            chapter where the pair is recorded. Empty list if the
            pair has never been observed.
        """
        if not entity_a or not str(entity_a).strip():
            raise ValueError("entity_a must be a non-empty string.")
        if not entity_b or not str(entity_b).strip():
            raise ValueError("entity_b must be a non-empty string.")
        if entity_a == entity_b:
            return []

        store = self._require_store()
        a, b = _build_pair_key(entity_a, entity_b)

        # Chapter list — sorted, deduplicated.
        chapters: list[int] = []
        list_snapshots = getattr(store, "list_snapshots", None)
        if callable(list_snapshots):
            try:
                chapters.extend(int(c) for c in list_snapshots())
            except Exception as exc:  # pragma: no cover - defensive
                _logger.warning(
                    "get_history | list_snapshots failed for project=%s: %s",
                    self._project_id,
                    exc,
                )
        chapters = sorted(set(chapters))

        history: list[dict[str, Any]] = []
        seen_chapter_keys: set[tuple[int, str]] = set()

        load_snapshot = getattr(store, "load_snapshot", None)
        for chapter in chapters:
            kernel = await self._safe_load_snapshot(load_snapshot, chapter)
            if kernel is None:
                continue
            rel = self._find_pair_relationship(kernel, a, b)
            if rel is None:
                continue
            key = (chapter, str(rel.get("relationship_id", "")))
            if key in seen_chapter_keys:
                continue
            seen_chapter_keys.add(key)
            entry = dict(rel)
            entry["chapter"] = chapter
            history.append(entry)

        # Fall back to the current kernel as the "final" state if no
        # snapshot recorded this pair (or to surface the live data even
        # when snapshots are present — the current kernel represents
        # the most recent committed state).
        load_kernel = getattr(store, "load_kernel", None)
        if callable(load_kernel):
            try:
                current = await load_kernel(self._project_id)
            except Exception as exc:  # pragma: no cover - defensive
                _logger.warning(
                    "get_history | load_kernel failed for project=%s: %s",
                    self._project_id,
                    exc,
                )
                current = None
            if current is not None:
                rel = self._find_pair_relationship(current, a, b)
                if rel is not None:
                    cur_chapter = self._current_chapter_of(current)
                    if not history or cur_chapter >= history[-1].get("chapter", -1):
                        key = (cur_chapter, str(rel.get("relationship_id", "")))
                        if key not in seen_chapter_keys:
                            entry = dict(rel)
                            entry["chapter"] = cur_chapter
                            history.append(entry)
                            seen_chapter_keys.add(key)

        return history

    async def get_neighbors(
        self,
        entity_id: str,
        depth: int = DEFAULT_NEIGHBOR_DEPTH,
    ) -> dict[str, list[str]]:
        """BFS up to *depth* hops from *entity_id* over the relationship graph.

        Parameters
        ----------
        entity_id:
            Starting entity. Must be non-empty.
        depth:
            Maximum number of hops. ``depth=1`` returns only direct
            neighbors; ``depth=2`` includes friends-of-friends, etc.
            Values < 1 are clamped to 1.

        Returns
        -------
        dict[str, list[str]]
            Mapping ``{entity_id: [neighbor_entity_ids]}`` for every
            entity reachable within *depth* hops (including the start).
            Each neighbor appears at most once in the list for its
            source entity (set semantics, then sorted for determinism).
        """
        if not entity_id or not str(entity_id).strip():
            raise ValueError("entity_id must be a non-empty string.")
        if depth < 1:
            depth = 1

        entity_id = str(entity_id)
        await self._ensure_entity_cached(entity_id)

        # BFS over the cached adjacency list. We track per-node depth
        # so the frontier is well-defined.
        visited: set[str] = {entity_id}
        depth_map: dict[str, int] = {entity_id: 0}
        queue: deque[tuple[str, int]] = deque([(entity_id, 0)])

        while queue:
            current, current_depth = queue.popleft()
            if current_depth >= depth:
                continue
            for neighbor in self._direct_neighbors(current):
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                depth_map[neighbor] = current_depth + 1
                queue.append((neighbor, current_depth + 1))
                # Also cache the neighbor's adjacency so future hops
                # don't re-hit the store.
                if neighbor not in self._adjacency:
                    await self._ensure_entity_cached(neighbor)

        # Build the result. For each visited node, compute the *set* of
        # its direct neighbors (within the BFS frontier). Then sort for
        # deterministic output.
        result: dict[str, list[str]] = {}
        for node in visited:
            neighbors = sorted(set(self._direct_neighbors(node)))
            result[node] = [n for n in neighbors if n in visited]
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_store(self) -> Any:
        """Return the bound store or raise a clear :class:`RuntimeError`."""
        if self._store is None:
            raise RuntimeError(
                "RelationshipQueryService.store is not bound. "
                "Pass a store to __init__ or call bind_store() first."
            )
        return self._store

    async def _ensure_entity_cached(self, entity_id: str) -> None:
        """Populate the cache for *entity_id* if it isn't already there."""
        if entity_id in self._adjacency:
            return
        store = self._require_store()
        get_relationships = getattr(store, "get_relationships", None)
        if not callable(get_relationships):
            raise RuntimeError("store.get_relationships is required but missing.")
        rels = await get_relationships(entity_id)
        self._adjacency[entity_id] = [_serialize_relationship(r) for r in (rels or [])]
        self._evict_if_over_capacity()
        # Cache has at least one entry now.
        self._cache_loaded = True

    def _add_relationship_to_cache(self, rel: Any) -> None:
        """Index a single relationship into both endpoint buckets."""
        payload = _serialize_relationship(rel)
        src = str(payload.get("source_entity_id", "") or "")
        tgt = str(payload.get("target_entity_id", "") or "")
        if not src or not tgt:
            return
        self._index_into(src, payload)
        self._index_into(tgt, payload)

    def _index_into(self, entity_id: str, payload: dict[str, Any]) -> None:
        """Insert *payload* into the adjacency list for *entity_id*."""
        bucket = self._adjacency.get(entity_id)
        if bucket is None:
            self._adjacency[entity_id] = [payload]
        else:
            bucket.append(payload)
        self._evict_if_over_capacity()

    def _evict_if_over_capacity(self) -> None:
        """Drop the oldest entry if the cache exceeds capacity.

        ``dict`` preserves insertion order in CPython 3.7+, so the
        first key is the oldest. We do *not* move re-read entities to
        the back — this is a simple FIFO bound, not a true LRU. The
        trade-off keeps the implementation O(1) per insertion and
        avoids the bookkeeping of an LRU.
        """
        while len(self._adjacency) > self._cache_capacity:
            oldest = next(iter(self._adjacency))
            self._adjacency.pop(oldest, None)
            _logger.debug(
                "evict | project=%s evicted=%s cache_size=%d",
                self._project_id,
                oldest,
                len(self._adjacency),
            )

    def _direct_neighbors(self, entity_id: str) -> list[str]:
        """Return the direct neighbor entity IDs from the cache."""
        neighbors: list[str] = []
        for rel in self._adjacency.get(entity_id, []):
            src = str(rel.get("source_entity_id", "") or "")
            tgt = str(rel.get("target_entity_id", "") or "")
            if src == entity_id and tgt and tgt != entity_id:
                neighbors.append(tgt)
            elif tgt == entity_id and src and src != entity_id:
                neighbors.append(src)
        return neighbors

    async def _safe_load_snapshot(
        self,
        load_snapshot: Any,
        chapter: int,
    ) -> Any:
        """Load a snapshot defensively; return ``None`` on any failure."""
        if not callable(load_snapshot):
            return None
        try:
            return await load_snapshot(chapter)
        except Exception as exc:  # pragma: no cover - defensive
            _logger.warning(
                "get_history | load_snapshot failed chapter=%d: %s",
                chapter,
                exc,
            )
            return None

    def _find_pair_relationship(
        self,
        kernel: Any,
        a: str,
        b: str,
    ) -> dict[str, Any] | None:
        """Find the relationship between *a* and *b* in *kernel*."""
        rels = getattr(kernel, "relationships", None)
        if not rels:
            return None
        # Walk in reverse so we prefer the *latest* entry if the kernel
        # happens to contain duplicates (it shouldn't, but be defensive).
        for rel in reversed(list(rels)):
            payload = _serialize_relationship(rel)
            src = str(payload.get("source_entity_id", "") or "")
            tgt = str(payload.get("target_entity_id", "") or "")
            if (src == a and tgt == b) or (src == b and tgt == a):
                return payload
        return None

    @staticmethod
    def _current_chapter_of(kernel: Any) -> int:
        """Return the current chapter number from a kernel, default 0."""
        cur = getattr(kernel, "current_chapter", 0)
        try:
            return int(cur or 0)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return 0
