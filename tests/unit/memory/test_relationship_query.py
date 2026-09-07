"""Tests for ``RelationshipQueryService`` — read-only adjacency list queries.

Verifies:
* Basic ``get_relationships`` returns a list of serialized dicts.
* ``type`` filter narrows by ``relation_type`` (case insensitive).
* ``get_history`` walks chapter snapshots in chronological order.
* ``get_neighbors`` does a BFS — depth=1 returns direct neighbors,
  depth=2 includes friends-of-friends, etc.
* The service never calls write methods on the store.

The store is replaced with an :class:`AsyncMock` so the test process
doesn't need SQLAlchemy / SQLite. ``AsyncMock`` matches the duck-typed
``store`` parameter on the service.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from novel_forge.memory.relationship_query import (
    CACHE_CAPACITY,
    DEFAULT_NEIGHBOR_DEPTH,
    RelationshipQueryService,
    _serialize_relationship,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _rel(
    rel_id: str,
    src: str,
    tgt: str,
    type: str = "ally",
    *,
    label: str = "",
    trust: float = 0.5,
    tension: float = 0.5,
    established_chapter: int = 1,
    last_shift_chapter: int = 0,
    shift_summary: str = "",
    status: str = "active",
) -> dict[str, Any]:
    """Build a Pydantic-style relationship dict for fixtures."""
    return {
        "relationship_id": rel_id,
        "source_entity_id": src,
        "target_entity_id": tgt,
        "relation_type": type,
        "label": label,
        "trust": trust,
        "tension": tension,
        "status": status,
        "established_chapter": established_chapter,
        "last_shift_chapter": last_shift_chapter,
        "shift_summary": shift_summary,
        "notes": "",
    }


def _make_store(
    *,
    relationships: dict[str, list[dict[str, Any]]] | None = None,
    snapshots: list[int] | None = None,
    snapshot_kernels: dict[int, Any] | None = None,
    current_kernel: Any = None,
) -> MagicMock:
    """Build a mock store with the duck-typed surface used by the service.

    Parameters
    ----------
    relationships:
        ``entity_id -> [serialized rel dicts]`` returned by
        ``get_relationships``. If an entity is missing, ``[]`` is returned.
    snapshots:
        Sorted list of chapter numbers returned by ``list_snapshots``.
    snapshot_kernels:
        ``chapter -> kernel-like object`` returned by ``load_snapshot``.
        Each kernel needs a ``.relationships`` attribute (iterable).
    current_kernel:
        Kernel returned by ``load_kernel``. Needs ``.current_chapter``
        and ``.relationships``.
    """
    rels = relationships or {}

    async def _get_relationships(entity_id: str) -> list[dict[str, Any]]:
        return list(rels.get(entity_id, []))

    def _list_snapshots() -> list[int]:
        return list(snapshots or [])

    async def _load_snapshot(chapter: int) -> Any:
        if snapshot_kernels is None:
            return None
        return snapshot_kernels.get(chapter)

    async def _load_kernel(project_id: str) -> Any:
        return current_kernel

    store = MagicMock()
    store.get_relationships = AsyncMock(side_effect=_get_relationships)
    store.list_snapshots = MagicMock(side_effect=_list_snapshots)
    store.load_snapshot = AsyncMock(side_effect=_load_snapshot)
    store.load_kernel = AsyncMock(side_effect=_load_kernel)

    # Defensive: explicit absence of any *write* method.
    store.add_relationship = AsyncMock()
    store.update_relationship = AsyncMock()
    store.delete_relationship = AsyncMock()
    return store


# ---------------------------------------------------------------------------
# Basic API behavior
# ---------------------------------------------------------------------------


class TestBasicQueries:
    """Verify the three primary read APIs."""

    @pytest.mark.asyncio
    async def test_get_relationships_returns_list(self) -> None:
        """``get_relationships`` returns serialized dicts for an entity."""
        rels = [
            _rel("r1", "alice", "bob", type="ally"),
            _rel("r2", "alice", "carol", type="friend"),
        ]
        store = _make_store(relationships={"alice": rels})
        svc = RelationshipQueryService("test_proj", store=store)

        result = await svc.get_relationships("alice")

        assert isinstance(result, list)
        assert len(result) == 2
        assert all(isinstance(r, dict) for r in result)
        # Serialized, not ORM-like (must not depend on session state).
        assert {r["relationship_id"] for r in result} == {"r1", "r2"}
        store.get_relationships.assert_awaited_once_with("alice")

    @pytest.mark.asyncio
    async def test_get_relationships_filters_by_type(self) -> None:
        """The ``type`` argument filters by ``relation_type`` (case insensitive)."""
        rels = [
            _rel("r1", "alice", "bob", type="ally"),
            _rel("r2", "alice", "carol", type="friend"),
            _rel("r3", "alice", "dave", type="enemy"),
            _rel("r4", "alice", "erin", type="ALLY"),  # uppercase variant
        ]
        store = _make_store(relationships={"alice": rels})
        svc = RelationshipQueryService("test_proj", store=store)

        only_allies = await svc.get_relationships("alice", type="ally")
        assert {r["relationship_id"] for r in only_allies} == {"r1", "r4"}

        only_enemies = await svc.get_relationships("alice", type="enemy")
        assert {r["relationship_id"] for r in only_enemies} == {"r3"}

        no_match = await svc.get_relationships("alice", type="romantic")
        assert no_match == []

    @pytest.mark.asyncio
    async def test_get_relationships_empty_when_no_data(self) -> None:
        """No recorded relationships for the entity → empty list (not error)."""
        store = _make_store(relationships={})
        svc = RelationshipQueryService("test_proj", store=store)

        result = await svc.get_relationships("ghost")

        assert result == []
        store.get_relationships.assert_awaited_once_with("ghost")

    @pytest.mark.asyncio
    async def test_get_relationships_rejects_empty_entity_id(self) -> None:
        """Empty / whitespace entity_id raises ``ValueError``."""
        store = _make_store()
        svc = RelationshipQueryService("test_proj", store=store)
        with pytest.raises(ValueError, match="entity_id"):
            await svc.get_relationships("")


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


class TestHistory:
    """Verify ``get_history`` walks chapter snapshots in order."""

    @pytest.mark.asyncio
    async def test_get_history_returns_chapter_progression(self) -> None:
        """Snapshots across chapters yield chronologically ordered entries."""
        rels_by_chapter = {
            1: [
                _rel(
                    "r1",
                    "alice",
                    "bob",
                    type="ally",
                    trust=0.3,
                    tension=0.7,
                    established_chapter=1,
                    last_shift_chapter=1,
                    shift_summary="初识",
                )
            ],
            3: [
                _rel(
                    "r1",
                    "alice",
                    "bob",
                    type="ally",
                    trust=0.6,
                    tension=0.5,
                    established_chapter=1,
                    last_shift_chapter=3,
                    shift_summary="共患难",
                )
            ],
            5: [
                _rel(
                    "r1",
                    "alice",
                    "bob",
                    type="ally",
                    trust=0.8,
                    tension=0.2,
                    established_chapter=1,
                    last_shift_chapter=5,
                    shift_summary="成为挚友",
                )
            ],
        }
        snapshot_kernels = {
            ch: MagicMock(relationships=rels) for ch, rels in rels_by_chapter.items()
        }
        store = _make_store(
            snapshots=[1, 3, 5],
            snapshot_kernels=snapshot_kernels,
        )
        svc = RelationshipQueryService("test_proj", store=store)

        history = await svc.get_history("alice", "bob")

        assert [e["chapter"] for e in history] == [1, 3, 5]
        assert [e["trust"] for e in history] == [0.3, 0.6, 0.8]
        assert [e["shift_summary"] for e in history] == ["初识", "共患难", "成为挚友"]

    @pytest.mark.asyncio
    async def test_get_history_direction_invariant(self) -> None:
        """``get_history(a, b)`` is the same as ``get_history(b, a)``."""
        rels = [_rel("r1", "alice", "bob", type="ally", established_chapter=1)]
        store = _make_store(
            snapshots=[1],
            snapshot_kernels={1: MagicMock(relationships=rels)},
        )
        svc = RelationshipQueryService("test_proj", store=store)

        ab = await svc.get_history("alice", "bob")
        ba = await svc.get_history("bob", "alice")

        assert ab == ba
        assert ab[0]["chapter"] == 1

    @pytest.mark.asyncio
    async def test_get_history_empty_for_unrelated_pair(self) -> None:
        """A pair with no recorded relationship returns ``[]``."""
        rels = [_rel("r1", "alice", "bob", type="ally")]
        store = _make_store(
            snapshots=[1, 2],
            snapshot_kernels={
                1: MagicMock(relationships=rels),
                2: MagicMock(relationships=rels),
            },
        )
        svc = RelationshipQueryService("test_proj", store=store)

        history = await svc.get_history("carol", "dave")
        assert history == []

    @pytest.mark.asyncio
    async def test_get_history_same_entity_returns_empty(self) -> None:
        """``get_history(a, a)`` is a no-op (no self-edges in the model)."""
        store = _make_store()
        svc = RelationshipQueryService("test_proj", store=store)
        assert await svc.get_history("alice", "alice") == []


# ---------------------------------------------------------------------------
# BFS neighbors
# ---------------------------------------------------------------------------


class TestBFSNeighbors:
    """Verify BFS with various depth values."""

    @pytest.mark.asyncio
    async def test_get_neighbors_bfs_depth_1(self) -> None:
        """depth=1 reaches the seed + its direct neighbors, no further."""
        # alice — bob (ally)
        # alice — carol (friend)
        # bob — dave (rival)        <-- dave is bob's friend, NOT alice's
        rels_alice = [
            _rel("r1", "alice", "bob", type="ally"),
            _rel("r2", "alice", "carol", type="friend"),
        ]
        rels_bob = [
            _rel("r1", "alice", "bob", type="ally"),
            _rel("r3", "bob", "dave", type="rival"),
        ]
        rels_carol = [_rel("r2", "alice", "carol", type="friend")]
        rels_dave = [_rel("r3", "bob", "dave", type="rival")]

        store = _make_store(
            relationships={
                "alice": rels_alice,
                "bob": rels_bob,
                "carol": rels_carol,
                "dave": rels_dave,
            },
        )
        svc = RelationshipQueryService("test_proj", store=store)

        result = await svc.get_neighbors("alice", depth=1)

        # Reachable entities: alice, bob, carol (NOT dave — would need depth 2).
        assert set(result.keys()) == {"alice", "bob", "carol"}
        # Direct neighbors of alice are bob and carol.
        assert set(result["alice"]) == {"bob", "carol"}
        # Bob's only reachable neighbor is alice (dave is beyond depth 1).
        assert set(result["bob"]) == {"alice"}
        # Carol's only reachable neighbor is alice.
        assert set(result["carol"]) == {"alice"}

    @pytest.mark.asyncio
    async def test_get_neighbors_bfs_depth_2(self) -> None:
        """depth=2 reaches friends-of-friends (e.g. alice -> dave via bob)."""
        rels_alice = [
            _rel("r1", "alice", "bob", type="ally"),
            _rel("r2", "alice", "carol", type="friend"),
        ]
        rels_bob = [
            _rel("r1", "alice", "bob", type="ally"),
            _rel("r3", "bob", "dave", type="rival"),
        ]
        rels_carol = [_rel("r2", "alice", "carol", type="friend")]
        rels_dave = [_rel("r3", "bob", "dave", type="rival")]

        store = _make_store(
            relationships={
                "alice": rels_alice,
                "bob": rels_bob,
                "carol": rels_carol,
                "dave": rels_dave,
            },
        )
        svc = RelationshipQueryService("test_proj", store=store)

        result = await svc.get_neighbors("alice", depth=2)

        # Reachable entities now include dave (2 hops via bob).
        assert set(result.keys()) == {"alice", "bob", "carol", "dave"}
        # Direct neighbors of alice (depth controls *reachability*, not edges).
        assert set(result["alice"]) == {"bob", "carol"}
        # Bob's direct neighbors in the result — both alice and dave are
        # within the BFS boundary, so both are listed.
        assert set(result["bob"]) == {"alice", "dave"}
        # Dave's only reachable neighbor is bob.
        assert set(result["dave"]) == {"bob"}

    @pytest.mark.asyncio
    async def test_get_neighbors_bfs_depth_3(self) -> None:
        """depth=3 traverses a 4-node chain end-to-end."""
        # Chain: alice -> bob -> carol -> dave
        rels_alice = [_rel("r1", "alice", "bob", type="ally")]
        rels_bob = [
            _rel("r1", "alice", "bob", type="ally"),
            _rel("r2", "bob", "carol", type="friend"),
        ]
        rels_carol = [
            _rel("r2", "bob", "carol", type="friend"),
            _rel("r3", "carol", "dave", type="rival"),
        ]
        rels_dave = [_rel("r3", "carol", "dave", type="rival")]

        store = _make_store(
            relationships={
                "alice": rels_alice,
                "bob": rels_bob,
                "carol": rels_carol,
                "dave": rels_dave,
            },
        )
        svc = RelationshipQueryService("test_proj", store=store)

        d1 = await svc.get_neighbors("alice", depth=1)
        d2 = await svc.get_neighbors("alice", depth=2)
        d3 = await svc.get_neighbors("alice", depth=3)

        # The set of *reachable* entities grows with depth.
        assert set(d1.keys()) == {"alice", "bob"}
        assert set(d2.keys()) == {"alice", "bob", "carol"}
        assert set(d3.keys()) == {"alice", "bob", "carol", "dave"}

        # Alice's direct neighbors are always just bob (graph is a chain).
        assert set(d1["alice"]) == set(d2["alice"]) == set(d3["alice"]) == {"bob"}
        # Bob's reachable neighbors grow with depth.
        assert set(d1["bob"]) == {"alice"}
        assert set(d2["bob"]) == {"alice", "carol"}
        assert set(d3["bob"]) == {"alice", "carol"}

    @pytest.mark.asyncio
    async def test_get_neighbors_depth_clamped_to_1(self) -> None:
        """``depth < 1`` is clamped to 1 — never returns the seed entity itself."""
        store = _make_store(relationships={"alice": [_rel("r1", "alice", "bob")], "bob": []})
        svc = RelationshipQueryService("test_proj", store=store)

        neighbors = await svc.get_neighbors("alice", depth=0)
        assert "bob" in neighbors["alice"]
        # Seed entity is always present in the result, but is NOT its own neighbor.
        assert "alice" not in neighbors["alice"]


# ---------------------------------------------------------------------------
# Read-only contract
# ---------------------------------------------------------------------------


class TestReadOnlyContract:
    """Verify the service never calls write methods on the store."""

    @pytest.mark.asyncio
    async def test_no_writes_to_story_kernel(self) -> None:
        """All three public APIs hit the store, but only via ``get_*`` methods."""
        rels = [_rel("r1", "alice", "bob", type="ally")]
        store = _make_store(
            relationships={"alice": rels, "bob": rels},
            snapshots=[1],
            snapshot_kernels={1: MagicMock(relationships=rels)},
            current_kernel=MagicMock(relationships=rels, current_chapter=1),
        )
        svc = RelationshipQueryService("test_proj", store=store)

        # Hit every public API.
        await svc.get_relationships("alice")
        await svc.get_relationships("alice", type="ally")
        await svc.get_history("alice", "bob")
        await svc.get_neighbors("alice", depth=2)
        await svc.refresh()

        # No write method should have been called.
        store.add_relationship.assert_not_called()
        store.update_relationship.assert_not_called()
        store.delete_relationship.assert_not_called()

    @pytest.mark.asyncio
    async def test_store_required(self) -> None:
        """A service without a bound store raises a clear ``RuntimeError``."""
        svc = RelationshipQueryService("test_proj")
        with pytest.raises(RuntimeError, match="not bound"):
            await svc.get_relationships("alice")

    def test_empty_project_id_rejected(self) -> None:
        """``__init__`` rejects empty ``project_id``."""
        with pytest.raises(ValueError, match="project_id"):
            RelationshipQueryService("")

    def test_cache_capacity_constant(self) -> None:
        """The cache capacity is exactly 500, as specified."""
        assert CACHE_CAPACITY == 500
        assert DEFAULT_NEIGHBOR_DEPTH == 2


# ---------------------------------------------------------------------------
# Cache behavior
# ---------------------------------------------------------------------------


class TestCacheBehavior:
    """Verify the in-memory adjacency cache is built and reused correctly."""

    @pytest.mark.asyncio
    async def test_cache_hits_avoid_repeated_store_calls(self) -> None:
        """A second call to ``get_relationships`` for the same entity does not
        hit the store again — the cache is keyed by entity_id."""
        rels = [_rel("r1", "alice", "bob", type="ally")]
        store = _make_store(relationships={"alice": rels})
        svc = RelationshipQueryService("test_proj", store=store)

        await svc.get_relationships("alice")
        await svc.get_relationships("alice")
        await svc.get_relationships("alice", type="ally")

        # The store is hit exactly once for the initial population. The
        # type filter is applied client-side.
        assert store.get_relationships.await_count == 1

    @pytest.mark.asyncio
    async def test_invalidate_clears_specific_entry(self) -> None:
        """``invalidate(entity_id)`` drops only that entry."""
        store = _make_store(
            relationships={
                "alice": [_rel("r1", "alice", "bob")],
                "bob": [_rel("r1", "alice", "bob")],
            },
        )
        svc = RelationshipQueryService("test_proj", store=store)

        await svc.get_relationships("alice")
        await svc.get_relationships("bob")
        assert svc.cache_size == 2

        svc.invalidate("alice")
        assert svc.cache_size == 1
        assert "alice" not in svc._adjacency  # noqa: SLF001 — private access OK in tests
        assert "bob" in svc._adjacency  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_invalidate_all_clears_cache(self) -> None:
        """``invalidate()`` with no argument clears everything."""
        store = _make_store(relationships={"alice": [_rel("r1", "alice", "bob")]})
        svc = RelationshipQueryService("test_proj", store=store)

        await svc.get_relationships("alice")
        assert svc.cache_size == 1
        assert svc.is_cache_loaded is True

        svc.invalidate()
        assert svc.cache_size == 0
        assert svc.is_cache_loaded is False

    @pytest.mark.asyncio
    async def test_serialize_helper_dict_passthrough(self) -> None:
        """``_serialize_relationship`` returns a copy for dict inputs."""
        original = _rel("r1", "a", "b")
        serialized = _serialize_relationship(original)
        assert serialized == original
        # Must be a copy, not the same reference (defensive: cache mutation).
        assert serialized is not original

    def test_bind_store_rejects_none(self) -> None:
        """``bind_store(None)`` raises — the service is read-only by design."""
        svc = RelationshipQueryService("test_proj")
        with pytest.raises(ValueError, match="must not be None"):
            svc.bind_store(None)
