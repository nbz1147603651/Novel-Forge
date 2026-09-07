"""Read-only entity knowledge projection for the memory layer.

This module exposes :class:`EntityKnowledgeService`, a **read-only** view over
the canonical ``story_kernel`` entity registry. It is intentionally limited to
``SELECT``-shaped operations against the kernel store:

* ``get_current(entity_id)`` — current entity state.
* ``get_history(entity_id, chapters)`` — synthesized "snapshots" across the
  last *N* chapters, derived from ``Entity.source_chapter`` /
  ``Entity.last_seen_chapter`` (the kernel does not retain per-chapter
  historical snapshots, so we recompute activity windows from the current
  record).
* ``get_by_chapter(chapter)`` — entities that were active at the given
  chapter.

Why a dedicated service?
------------------------
``memory/`` is supposed to *consume* the canon — it must not introduce a
parallel "facts source" of its own.  Without this guard, both the kernel and
``narrative_state.EntityRegistry`` would race to be authoritative.  This
service is a **projection** of the kernel only, with an in-process LRU cache
(200 entries) to amortize repeated lookups during a chapter generation.

Hard constraints (enforced by code + tests):
* **No writes.**  We never call ``add_entity`` / ``update_entity`` /
  ``create_kernel`` / ``save_kernel`` or anything in ``narrative_state`` that
  mutates a registry.
* **No new SQLite tables.**  We use the existing
  ``StoryKernelStore.load_kernel`` / ``get_entity`` read paths.
* **Returns plain ``dict``**, not ORM/SQLAlchemy/Pydantic objects.  Dicts are
  JSON-serialisable, cheap to copy, and immune to detached-session issues
  after the underlying store is closed.
* **Stateless aside from the cache.**  The service holds no per-entity
  mutation state — it is a thin read proxy.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from novel_forge.story_kernel.entity_projection import (
    entity_lookup_from_kernel,
)
from novel_forge.story_kernel.schemas import Entity, StoryKernel

if TYPE_CHECKING:
    from novel_forge.story_kernel.store import StoryKernelStore

_log = logging.getLogger("novel_forge.memory.entity_knowledge")

# Default cache capacity — 200 entries.  Bounded so the service does not
# balloon when a project has thousands of entities; in practice entity lookups
# concentrate on a small set of recurring characters/locations.
DEFAULT_CACHE_CAPACITY: int = 200

# How many chapters back to default for ``get_history`` if caller omits.
DEFAULT_HISTORY_WINDOW: int = 10


# ---------------------------------------------------------------------------
# Internal dataclasses (typed cache entries)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EntitySnapshot:
    """A plain-data snapshot of a single entity at a given chapter.

    ``chapter`` is the chapter index the snapshot is *attributed to*.  All
    other fields are pulled directly from the canonical ``Entity`` record;
    we do not modify them in any way.  Returning dataclass-based snapshots
    keeps the call surface typed and free of ORM detachment problems.
    """

    entity_id: str
    name: str
    entity_type: str
    aliases: tuple[str, ...]
    status: str
    chapter: int
    source_chapter: int
    last_seen_chapter: int
    notes: str
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict (JSON-friendly)."""
        return {
            "entity_id": self.entity_id,
            "name": self.name,
            "entity_type": self.entity_type,
            "aliases": list(self.aliases),
            "status": self.status,
            "chapter": self.chapter,
            "source_chapter": self.source_chapter,
            "last_seen_chapter": self.last_seen_chapter,
            "notes": self.notes,
            "attributes": dict(self.attributes),
        }


@dataclass(frozen=True)
class _KernelCacheEntry:
    """The cached payload of a fully-loaded ``StoryKernel``.

    Storing the **whole** kernel (not per-entity lookups) keeps the cache
    simple: one entry per ``project_id``, evicted by LRU when the cap is
    hit.  The kernel is small (hundreds of entities at most), so a single
    object is cheaper to invalidate than a fine-grained per-entity map.
    """

    project_id: str
    kernel: StoryKernel
    lookup: dict[str, Entity]  # id/name/alias -> Entity


# ---------------------------------------------------------------------------
# LRU cache (async-safe, async-friendly)
# ---------------------------------------------------------------------------


class _LRUProjectCache:
    """A tiny async-safe LRU keyed by ``project_id``.

    ``functools.lru_cache`` only works on sync callables; we need a coroutine
    to load the kernel via ``StoryKernelStore.load_kernel``.  This class
    provides the same LRU semantics over an :class:`OrderedDict` and a
    ``threading.Lock`` (we protect the dict itself; downstream SQLAlchemy
    work runs under the store's own locks).
    """

    def __init__(self, capacity: int = DEFAULT_CACHE_CAPACITY) -> None:
        if capacity <= 0:
            raise ValueError(f"capacity must be > 0, got {capacity}")
        self._capacity = capacity
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, _KernelCacheEntry] = OrderedDict()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def __contains__(self, project_id: str) -> bool:
        with self._lock:
            return project_id in self._entries

    def capacity(self) -> int:
        return self._capacity

    def get(self, project_id: str) -> _KernelCacheEntry | None:
        """Return cached entry, marking it as most-recently-used."""
        with self._lock:
            entry = self._entries.get(project_id)
            if entry is None:
                return None
            # ``move_to_end`` reorders the entry to the MRU position.
            self._entries.move_to_end(project_id)
            return entry

    def put(self, entry: _KernelCacheEntry) -> None:
        """Insert or refresh an entry, evicting the LRU entry if needed."""
        with self._lock:
            if entry.project_id in self._entries:
                self._entries.move_to_end(entry.project_id)
                self._entries[entry.project_id] = entry
                return
            self._entries[entry.project_id] = entry
            while len(self._entries) > self._capacity:
                evicted_project, _ = self._entries.popitem(last=False)
                _log.debug(
                    "EntityKnowledgeService LRU evict | project=%s", evicted_project,
                )

    def invalidate(self, project_id: str) -> bool:
        """Drop a single project.  Returns True if anything was removed."""
        with self._lock:
            return self._entries.pop(project_id, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class EntityKnowledgeService:
    """Read-only, cached entity lookup over the story_kernel.

    The service never writes to ``story_kernel`` or to
    ``narrative_state.EntityRegistry``.  It exposes three read methods that
    are safe to call from any async context.

    Parameters
    ----------
    project_id:
        Default project id used by the convenience ``get_*`` methods.
        A pre-loaded :class:`StoryKernel` (via ``store``) can be shared across
        many services; the service falls back to constructing its own store
        if one is not injected.
    store:
        Optional pre-configured :class:`StoryKernelStore`.  If omitted, the
        service builds an in-memory store on first use, which is convenient
        for unit tests but **not** suitable for production (callers should
        inject the project-scoped store).
    cache_capacity:
        Override the LRU cap.  Defaults to :data:`DEFAULT_CACHE_CAPACITY`.
    """

    def __init__(
        self,
        project_id: str,
        store: "StoryKernelStore | None" = None,
        *,
        cache_capacity: int = DEFAULT_CACHE_CAPACITY,
    ) -> None:
        self._project_id = str(project_id or "").strip()
        if not self._project_id:
            raise ValueError("project_id must be a non-empty string")
        self._store: StoryKernelStore | None = store
        self._owns_store: bool = store is None
        self._cache: _LRUProjectCache = _LRUProjectCache(capacity=cache_capacity)
        # Serialise concurrent loads of the same kernel so we never trigger
        # two parallel ``load_kernel`` calls for the same project.
        self._load_locks: dict[str, asyncio.Lock] = {}
        self._load_locks_guard = threading.Lock()

    # ------------------------------------------------------------------
    # Public read API
    # ------------------------------------------------------------------

    async def get_current(self, entity_id: str) -> dict[str, Any] | None:
        """Return the current state of *entity_id* as a dict.

        Resolution order: ``entity_id`` → name → any alias.  Returns
        ``None`` if the entity does not exist (or the project has no
        kernel loaded).
        """
        if not entity_id or not str(entity_id).strip():
            return None
        entry = await self._load_kernel_cached()
        if entry is None:
            return None
        entity = self._resolve_entity(entry, str(entity_id).strip())
        if entity is None:
            return None
        return _entity_to_dict(entity)

    async def get_history(
        self,
        entity_id: str,
        chapters: int = DEFAULT_HISTORY_WINDOW,
    ) -> list[dict[str, Any]]:
        """Return a list of entity snapshots across the last *chapters*.

        Because the kernel does not store per-chapter historical snapshots
        for entities, this method **synthesises** a snapshot for every
        chapter in which the entity was active, bounded by the trailing
        *chapters* window.  Each returned dict has the same shape as
        :meth:`get_current` plus a ``chapter`` field indicating the
        attributed chapter.

        Returns an empty list if the entity is not found or has no
        recorded activity.
        """
        if chapters <= 0:
            return []
        if not entity_id or not str(entity_id).strip():
            return []
        entry = await self._load_kernel_cached()
        if entry is None:
            return []
        entity = self._resolve_entity(entry, str(entity_id).strip())
        if entity is None:
            return []
        current_chapter = max(int(entry.kernel.current_chapter or 0), 1)
        window_start = max(1, current_chapter - chapters + 1)
        snapshots: list[dict[str, Any]] = []
        for chapter in range(window_start, current_chapter + 1):
            if not _entity_active_at_chapter(entity, chapter):
                continue
            snapshots.append(_entity_snapshot_dict(entity, chapter))
        # Newest-first ordering is the convention callers expect when they
        # ask for "the last N chapters" of activity.
        snapshots.reverse()
        return snapshots

    async def get_by_chapter(self, chapter: int) -> list[dict[str, Any]]:
        """Return all entities active at *chapter*.

        An entity is considered active at chapter *N* when
        ``source_chapter <= N <= last_seen_chapter`` (the latter is 0 when
        the entity is still active but the project has not yet recorded a
        "last seen" update — in that case we treat the entity as active up
        to the project's current chapter).
        """
        if chapter < 0:
            return []
        entry = await self._load_kernel_cached()
        if entry is None:
            return []
        results: list[dict[str, Any]] = []
        for entity in entry.kernel.entities:
            if _entity_active_at_chapter(entity, chapter):
                results.append(_entity_to_dict(entity))
        # Stable, name-sorted output for deterministic tests/UX.
        results.sort(key=lambda d: (d.get("name") or "", d.get("entity_id") or ""))
        return results

    # ------------------------------------------------------------------
    # Cache / lifecycle helpers
    # ------------------------------------------------------------------

    async def _load_kernel_cached(self) -> _KernelCacheEntry | None:
        """Return the cached kernel for ``self._project_id`` or load it."""
        cached = self._cache.get(self._project_id)
        if cached is not None:
            return cached
        lock = self._get_load_lock(self._project_id)
        async with lock:
            # Double-check: another coroutine may have populated it while we
            # were awaiting the lock.
            cached = self._cache.get(self._project_id)
            if cached is not None:
                return cached
            kernel = await self._fetch_kernel(self._project_id)
            if kernel is None:
                return None
            entry = _KernelCacheEntry(
                project_id=self._project_id,
                kernel=kernel,
                lookup=entity_lookup_from_kernel(kernel),
            )
            self._cache.put(entry)
            return entry

    def invalidate_cache(self, project_id: str | None = None) -> bool:
        """Drop the cached kernel for *project_id* (defaults to the
        service's project).  Returns True if anything was removed.

        Use this when the caller knows the kernel has been mutated by
        another component and the cache must be refreshed.  This method
        does **not** touch the kernel itself.
        """
        target = (project_id or self._project_id).strip()
        if not target:
            return False
        return self._cache.invalidate(target)

    def cache_size(self) -> int:
        return len(self._cache)

    async def aclose(self) -> None:
        """Release owned store resources (only if we created it ourselves)."""
        if self._owns_store and self._store is not None:
            close = getattr(self._store, "close", None)
            if callable(close):
                result = close()
                if hasattr(result, "__await__"):
                    await result
            self._store = None
        self._cache.clear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_load_lock(self, project_id: str) -> asyncio.Lock:
        with self._load_locks_guard:
            lock = self._load_locks.get(project_id)
            if lock is None:
                lock = asyncio.Lock()
                self._load_locks[project_id] = lock
            return lock

    async def _fetch_kernel(self, project_id: str) -> StoryKernel | None:
        """Resolve a kernel using the injected or constructed store.

        Returns ``None`` when the project has no kernel yet (an empty
        project, not an error).  Caller is expected to translate ``None``
        to an empty result, *not* an exception, so first-run / not-yet-
        initialised projects degrade gracefully.
        """
        store = self._ensure_store()
        if store is None:
            return None
        load = getattr(store, "load_kernel", None)
        if not callable(load):
            _log.warning(
                "EntityKnowledgeService: store has no load_kernel; "
                "returning empty result for project=%s",
                project_id,
            )
            return None
        try:
            kernel: StoryKernel | None = await load(project_id)
        except (ValueError, KeyError) as exc:
            # Kernel does not exist yet — treat as empty.
            _log.info(
                "EntityKnowledgeService: no kernel for project=%s (%s)",
                project_id,
                exc,
            )
            return None
        except Exception:  # noqa: BLE001 - we want to surface as warning only
            _log.exception(
                "EntityKnowledgeService: load_kernel failed for project=%s",
                project_id,
            )
            return None
        return kernel

    def _ensure_store(self) -> "StoryKernelStore | None":
        """Lazily create a store if none was injected.

        Lookup order:
        1. If a store was injected at construction time, return it.
        2. Otherwise, probe the default on-disk path
           ``$NOVEL_FORGE_STORAGE_ROOT/<project_id>/story_kernel.db`` (or
           ``./data/<project_id>/story_kernel.db``) — useful for ad-hoc
           manual / CLI invocations where injecting a store is overkill.
        3. If neither is available, fall back to an ephemeral in-memory
           store.  This keeps unit tests cheap and lets the service
           instantiate safely before a project has a real store; queries
           will simply degrade to empty results.
        """
        if self._store is not None:
            return self._store
        try:
            from novel_forge.story_kernel.store import StoryKernelStore
        except Exception:  # noqa: BLE001
            _log.exception("EntityKnowledgeService: cannot import StoryKernelStore")
            return None

        # Try the default on-disk location first.
        db_path = self._resolve_default_db_path()
        if db_path is not None and db_path.exists():
            try:
                self._store = StoryKernelStore(db_path)
                self._owns_store = True
                return self._store
            except Exception:  # noqa: BLE001
                _log.exception(
                    "EntityKnowledgeService: cannot open store at %s", db_path,
                )
                # fall through to in-memory fallback

        try:
            self._store = StoryKernelStore.in_memory()
            self._owns_store = True
        except Exception:  # noqa: BLE001
            _log.exception("EntityKnowledgeService: cannot create in-memory store")
            return None
        return self._store

    def _resolve_default_db_path(self) -> Path | None:
        """Best-effort probe of the canonical on-disk project layout.

        Returns ``None`` if no plausible path can be derived (e.g. an
        exotic storage root).  Callers must check ``Path.exists()``
        before opening — the function does not assume the file is there.
        """
        if not self._project_id:
            return None
        storage_root = os.environ.get("NOVEL_FORGE_STORAGE_ROOT", "").strip()
        if storage_root:
            root = Path(storage_root).expanduser()
        else:
            root = Path("./data")
        return root / self._project_id / "story_kernel.db"

    @staticmethod
    def _resolve_entity(
        entry: _KernelCacheEntry,
        key: str,
    ) -> Entity | None:
        """Resolve an entity by id, name, or alias from a cached entry."""
        return entry.lookup.get(key)


# ---------------------------------------------------------------------------
# Pure helpers (module-level so they are easy to unit-test in isolation)
# ---------------------------------------------------------------------------


def _entity_to_dict(entity: Entity) -> dict[str, Any]:
    """Project a single Entity to a JSON-friendly dict."""
    entity_type = getattr(entity.entity_type, "value", entity.entity_type)
    return {
        "entity_id": str(entity.entity_id or ""),
        "name": str(entity.name or ""),
        "entity_type": str(entity_type or "unknown"),
        "aliases": [str(a) for a in (entity.aliases or [])],
        "status": str(entity.status or "active"),
        "source_chapter": int(entity.source_chapter or 0),
        "last_seen_chapter": int(entity.last_seen_chapter or 0),
        "notes": str(entity.notes or ""),
        "attributes": dict(entity.attributes or {}),
    }


def _entity_snapshot_dict(entity: Entity, chapter: int) -> dict[str, Any]:
    """Same as :func:`_entity_to_dict` but tagged with *chapter*."""
    data = _entity_to_dict(entity)
    data["chapter"] = int(chapter)
    return data


def _entity_active_at_chapter(entity: Entity, chapter: int) -> bool:
    """Return True iff *entity* was active at *chapter*.

    Rules:
    * ``chapter < 1`` → never active.
    * ``source_chapter == 0`` → uninitialised; not active anywhere.
    * If ``last_seen_chapter == 0`` we treat the entity as still active
      up to and including the project's current chapter, but *not* in
      chapters beyond it.  Callers compare against
      ``kernel.current_chapter`` themselves when they need that bound.
    * Otherwise, ``source_chapter <= chapter <= last_seen_chapter``.
    """
    if chapter < 1:
        return False
    source = int(entity.source_chapter or 0)
    if source <= 0:
        return False
    if source > chapter:
        return False
    last_seen = int(entity.last_seen_chapter or 0)
    if last_seen <= 0:
        return True
    return chapter <= last_seen


__all__ = [
    "DEFAULT_CACHE_CAPACITY",
    "DEFAULT_HISTORY_WINDOW",
    "EntityKnowledgeService",
    "EntitySnapshot",
    "_entity_active_at_chapter",
    "_entity_to_dict",
]
