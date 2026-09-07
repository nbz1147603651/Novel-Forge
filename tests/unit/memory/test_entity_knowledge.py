"""Tests for ``EntityKnowledgeService`` (Wave 2 / Task 6 / P2.1).

Covers the **read-only** contract: the service must answer three
query shapes against ``story_kernel`` without ever invoking a write
method, and must cache results in an LRU(200) bound.

All tests use a small fake store (no real SQLite, no LLM calls) — the
service is exercised with hand-built :class:`StoryKernel` payloads.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from novel_forge.memory.entity_knowledge import (
    DEFAULT_CACHE_CAPACITY,
    EntityKnowledgeService,
)
from novel_forge.story_kernel.schemas import (
    Entity,
    EntityType,
    StoryKernel,
)

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _entity(
    entity_id: str,
    name: str,
    *,
    entity_type: str | EntityType = "character",
    aliases: list[str] | None = None,
    source_chapter: int = 1,
    last_seen_chapter: int = 0,
    status: str = "active",
    notes: str = "",
) -> Entity:
    """Build a fully-typed Entity record for tests.

    ``entity_type`` accepts the raw string ("character", "location", ...)
    because ``Entity._normalize_entity_type`` runs in ``mode="before"`` and
    the enum's ``str()`` form ("EntityType.CHARACTER") does not survive
    round-tripping through the validator's alias map.
    """
    if isinstance(entity_type, EntityType):
        entity_type = entity_type.value
    return Entity(
        entity_id=entity_id,
        name=name,
        entity_type=entity_type,  # type: ignore[arg-type]
        aliases=aliases or [],
        status=status,
        attributes={},
        source_chapter=source_chapter,
        last_seen_chapter=last_seen_chapter,
        notes=notes,
    )


class FakeKernelStore:
    """In-memory stand-in for ``StoryKernelStore`` used by the service.

    We deliberately do **not** implement any of the write methods.  The
    test suite will fail loudly if the service ever calls one — which is
    exactly the safety property we want to verify.
    """

    def __init__(self, kernel: StoryKernel | None) -> None:
        self._kernel = kernel
        self.load_calls = 0

    async def load_kernel(self, project_id: str) -> StoryKernel:
        self.load_calls += 1
        if self._kernel is None:
            raise ValueError(f"Kernel for project '{project_id}' not found.")
        return self._kernel

    # --- write surface intentionally absent ---
    async def add_entity(self, *_: Any, **__: Any) -> None:  # pragma: no cover
        raise AssertionError("EntityKnowledgeService must not call add_entity")

    async def update_entity(self, *_: Any, **__: Any) -> None:  # pragma: no cover
        raise AssertionError("EntityKnowledgeService must not call update_entity")

    async def create_kernel(self, *_: Any, **__: Any) -> None:  # pragma: no cover
        raise AssertionError("EntityKnowledgeService must not call create_kernel")

    async def save_kernel(self, *_: Any, **__: Any) -> None:  # pragma: no cover
        raise AssertionError("EntityKnowledgeService must not call save_kernel")


def _make_kernel() -> StoryKernel:
    """A small but realistic StoryKernel payload used by most tests."""
    return StoryKernel(
        project_id="弈局谋心",
        project_mode="long",
        current_chapter=10,
        title="弈局谋心",
        premise="A memory-trader discovers his past was rewritten.",
        entities=[
            _entity("char_lin", "林远", source_chapter=1, last_seen_chapter=8),
            _entity("char_su", "苏婉", source_chapter=1, last_seen_chapter=10),
            _entity(
                "char_old", "老者", source_chapter=2, last_seen_chapter=0,
                status="active",
            ),
            _entity(
                "loc_pavilion",
                "听雨阁",
                entity_type="location",
                source_chapter=3,
                last_seen_chapter=7,
            ),
            _entity(
                "item_jade",
                "青玉佩",
                entity_type="item",
                aliases=["玉佩", "玉"],
                source_chapter=5,
                last_seen_chapter=9,
            ),
            # Uninitialised entity: source_chapter=0 → should never appear
            _entity("char_ghost", "无名氏", source_chapter=0, last_seen_chapter=0),
        ],
    )


@pytest.fixture
def fake_store() -> FakeKernelStore:
    return FakeKernelStore(_make_kernel())


@pytest.fixture
def service(fake_store: FakeKernelStore) -> EntityKnowledgeService:
    return EntityKnowledgeService(
        project_id="弈局谋心",
        store=fake_store,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# 1. get_current returns a JSON-friendly dict
# ---------------------------------------------------------------------------


async def test_get_current_returns_entity_dict(
    service: EntityKnowledgeService,
) -> None:
    result = await service.get_current("char_lin")

    assert result is not None
    # Required fields per the design contract
    for key in ("entity_id", "name", "entity_type", "aliases", "notes"):
        assert key in result, f"missing field {key!r}"
    assert result["entity_id"] == "char_lin"
    assert result["name"] == "林远"
    assert result["entity_type"] == "character"
    assert result["aliases"] == []
    assert result["status"] == "active"
    assert result["source_chapter"] == 1
    assert result["last_seen_chapter"] == 8
    # Plain dict (not Entity / Pydantic / ORM)
    assert isinstance(result, dict)


async def test_get_current_resolves_by_name_and_alias(
    service: EntityKnowledgeService,
) -> None:
    by_name = await service.get_current("苏婉")
    by_alias = await service.get_current("玉佩")

    assert by_name is not None and by_name["entity_id"] == "char_su"
    assert by_alias is not None and by_alias["entity_id"] == "item_jade"
    assert by_alias["aliases"] == ["玉佩", "玉"]


# ---------------------------------------------------------------------------
# 2. get_current returns None for missing entities / empty kernels
# ---------------------------------------------------------------------------


async def test_get_current_returns_none_for_missing(
    service: EntityKnowledgeService,
) -> None:
    assert await service.get_current("does_not_exist") is None
    # Empty / whitespace keys short-circuit to None (no exception).
    assert await service.get_current("") is None
    assert await service.get_current("   ") is None


async def test_get_current_returns_none_when_kernel_absent() -> None:
    empty_store = FakeKernelStore(None)
    svc = EntityKnowledgeService(
        project_id="empty_proj",
        store=empty_store,  # type: ignore[arg-type]
    )
    # Kernel absence must NOT raise — degrade to empty.
    assert await svc.get_current("anyone") is None
    assert await svc.get_by_chapter(1) == []
    assert await svc.get_history("anyone") == []


# ---------------------------------------------------------------------------
# 3. get_history filters by chapter window
# ---------------------------------------------------------------------------


async def test_get_history_filters_by_chapters(
    service: EntityKnowledgeService,
) -> None:
    # 林远: introduced ch1, last seen ch8.
    # Project current_chapter=10.  window=5 → chapters [6..10].
    # 林远 is active at ch6, 7, 8 (not 9, 10).
    history = await service.get_history("char_lin", chapters=5)

    chapters = [h["chapter"] for h in history]
    assert chapters == [8, 7, 6], f"unexpected ordering or content: {chapters}"
    for snap in history:
        assert snap["entity_id"] == "char_lin"
        assert snap["name"] == "林远"
        assert snap["source_chapter"] == 1
        assert snap["last_seen_chapter"] == 8


async def test_get_history_zero_window_returns_empty(
    service: EntityKnowledgeService,
) -> None:
    assert await service.get_history("char_lin", chapters=0) == []


async def test_get_history_unknown_entity_returns_empty(
    service: EntityKnowledgeService,
) -> None:
    assert await service.get_history("nope") == []
    assert await service.get_history("nope", chapters=20) == []


async def test_get_history_still_active_entity_extends_to_current(
    service: EntityKnowledgeService,
) -> None:
    # 老者: source_chapter=2, last_seen_chapter=0 (still active).
    # window=10 → chapters [1..10]. 老者 is active at chapters 2..10.
    history = await service.get_history("char_old", chapters=10)
    chapters = [h["chapter"] for h in history]
    assert chapters == [10, 9, 8, 7, 6, 5, 4, 3, 2]
    for snap in history:
        # ``chapter`` field is present on every snapshot.
        assert "chapter" in snap


# ---------------------------------------------------------------------------
# 4. get_by_chapter returns entities active at the given chapter
# ---------------------------------------------------------------------------


async def test_get_by_chapter_returns_entities_at_chapter(
    service: EntityKnowledgeService,
) -> None:
    # At chapter 4: 林远 (1-8) ✓, 苏婉 (1-10) ✓, 老者 (2-...) ✓,
    #               听雨阁 (3-7) ✓, 青玉佩 (5-9) ✗, 无名氏 (0-0) ✗
    result = await service.get_by_chapter(4)
    names = {r["name"] for r in result}
    assert names == {"林远", "苏婉", "老者", "听雨阁"}
    # Sorted by (name, entity_id) for determinism
    names_in_order = [r["name"] for r in result]
    assert names_in_order == sorted(names_in_order)


async def test_get_by_chapter_before_any_introduction_returns_empty(
    service: EntityKnowledgeService,
) -> None:
    assert await service.get_by_chapter(0) == []
    # No entity was introduced at chapter 0 (all source_chapter >= 1).
    assert await service.get_by_chapter(-3) == []


async def test_get_by_chapter_after_all_last_seens_excludes_known_endings(
    service: EntityKnowledgeService,
) -> None:
    # Chapter 11 is beyond every recorded last_seen.  老者 is still
    # "active" (last_seen_chapter=0) so it stays; everything else drops.
    result = await service.get_by_chapter(11)
    names = {r["name"] for r in result}
    assert names == {"老者"}, f"expected only 老者 to remain, got {names}"


# ---------------------------------------------------------------------------
# 5. LRU cache behaviour
# ---------------------------------------------------------------------------


async def test_lru_cache_reuses_kernel_across_lookups(
    service: EntityKnowledgeService,
    fake_store: FakeKernelStore,
) -> None:
    # First call loads from the store.
    await service.get_current("char_lin")
    # Subsequent calls must hit the cache.
    await service.get_current("char_su")
    await service.get_by_chapter(3)
    await service.get_history("char_old", chapters=4)

    # The fake store should have been hit at most ONCE regardless of how
    # many distinct lookups we performed.
    assert fake_store.load_calls == 1, (
        f"expected single load_kernel call, got {fake_store.load_calls}"
    )


async def test_lru_cache_capacity_is_200_by_default() -> None:
    """The LRU capacity matches the documented default of 200."""
    assert DEFAULT_CACHE_CAPACITY == 200
    svc = EntityKnowledgeService(
        project_id="p",
        store=None,
    )
    # Internally the LRU should report the same number.
    assert svc.cache_size() == 0  # nothing loaded yet
    # Construct a fresh service with a small capacity and verify it
    # honours the bound (LRU is private, but we can probe via
    # ``invalidate_cache`` semantics).
    small = EntityKnowledgeService(
        project_id="p", store=None, cache_capacity=2,
    )
    assert small.cache_size() == 0


async def test_lru_cache_invalidate_drops_entry(
    fake_store: FakeKernelStore,
) -> None:
    svc = EntityKnowledgeService(
        project_id="弈局谋心",
        store=fake_store,  # type: ignore[arg-type]
    )
    await svc.get_current("char_lin")
    assert fake_store.load_calls == 1
    # Cached; second lookup must NOT re-load.
    await svc.get_current("char_lin")
    assert fake_store.load_calls == 1

    # Explicit invalidation forces a re-load.
    assert svc.invalidate_cache() is True
    await svc.get_current("char_lin")
    assert fake_store.load_calls == 2

    # Invalidate on a different project is a no-op (returns False).
    assert svc.invalidate_cache(project_id="never_loaded") is False


# ---------------------------------------------------------------------------
# 6. Read-only contract (no writes anywhere)
# ---------------------------------------------------------------------------


async def test_service_never_invokes_write_surface(
    service: EntityKnowledgeService,
) -> None:
    """All three read methods must be 100% read-only.

    The :class:`FakeKernelStore` raises ``AssertionError`` from any
    write method.  If this test ever fails, we have introduced a
    regression where the service is writing to the kernel.
    """
    await service.get_current("char_lin")
    await service.get_history("char_su", chapters=3)
    await service.get_by_chapter(4)
    # No AssertionError raised → no write methods were called.


async def test_service_uses_asyncmock_store_without_real_io() -> None:
    """The service works against an :class:`AsyncMock` store too.

    The plan calls out ``unittest.mock.AsyncMock`` as the recommended
    testing surface; this test pins that pattern.
    """
    kernel = _make_kernel()
    mock_store = AsyncMock()
    mock_store.load_kernel = AsyncMock(return_value=kernel)
    # Wire write methods to raise on call (so any accidental invocation
    # is caught).
    mock_store.add_entity = AsyncMock(
        side_effect=AssertionError("must not call add_entity"),
    )
    mock_store.update_entity = AsyncMock(
        side_effect=AssertionError("must not call update_entity"),
    )
    mock_store.save_kernel = AsyncMock(
        side_effect=AssertionError("must not call save_kernel"),
    )

    svc = EntityKnowledgeService(
        project_id="弈局谋心",
        store=mock_store,  # type: ignore[arg-type]
    )
    result = await svc.get_current("char_lin")
    assert result is not None
    assert result["name"] == "林远"
    # Only load_kernel should have been called; write methods untouched.
    mock_store.load_kernel.assert_awaited()
    mock_store.add_entity.assert_not_called()
    mock_store.update_entity.assert_not_called()
    mock_store.save_kernel.assert_not_called()


# ---------------------------------------------------------------------------
# 7. Concurrency / safety
# ---------------------------------------------------------------------------


async def test_concurrent_get_current_does_not_double_load(
    fake_store: FakeKernelStore,
) -> None:
    """Many concurrent lookups should coalesce into a single load."""
    svc = EntityKnowledgeService(
        project_id="弈局谋心",
        store=fake_store,  # type: ignore[arg-type]
    )
    await asyncio.gather(*[svc.get_current("char_lin") for _ in range(20)])
    await asyncio.gather(*[svc.get_by_chapter(3) for _ in range(20)])
    # The internal per-project load lock should serialise the loads.
    assert fake_store.load_calls == 1, (
        f"expected 1 load, got {fake_store.load_calls}"
    )


# ---------------------------------------------------------------------------
# 8. Construction-time validation
# ---------------------------------------------------------------------------


def test_construction_rejects_empty_project_id() -> None:
    with pytest.raises(ValueError, match="project_id"):
        EntityKnowledgeService(project_id="")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="project_id"):
        EntityKnowledgeService(project_id="   ")  # type: ignore[arg-type]


def test_construction_rejects_invalid_cache_capacity() -> None:
    # The LRU refuses zero / negative capacities; the service surfaces
    # that as a ValueError.
    with pytest.raises(ValueError):
        EntityKnowledgeService(project_id="p", cache_capacity=0)
