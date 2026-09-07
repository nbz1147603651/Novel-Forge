"""Tests for story_kernel.relationship_tracker — relationship overview from StoryKernel."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from novel_forge.story_kernel.relationship_tracker import (
    CharacterRelationshipMap,
    RelationshipOverview,
    RelationshipSnapshot,
    RelationshipTimeline,
    _build_entity_map,
    _extract_timelines_from_kernel,
    _fill_current_state,
    _is_dirty_relationship_endpoint,
    _relationship_overview_from_dict,
    _seed_timelines_from_character_bible,
    build_relationship_overview,
    build_relationship_overview_sync,
    get_character_relationships,
)
from novel_forge.story_kernel.schemas import (
    Entity,
    Relationship,
    StoryKernel,
)
from novel_forge.story_kernel.store import StoryKernelStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def store() -> AsyncIterator[StoryKernelStore]:
    """Create a file-backed StoryKernelStore in a temp directory."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "story_kernel.db"
        s = StoryKernelStore(db_path)
        await s.init_db()
        try:
            yield s
        finally:
            await s.close()


@pytest.fixture()
async def kernel_with_rels() -> AsyncIterator[tuple[StoryKernelStore, Path]]:
    """Store + project_dir with a kernel containing relationships."""
    import tempfile

    td = tempfile.mkdtemp()
    project_dir = Path(td) / "test-proj"
    project_dir.mkdir(parents=True, exist_ok=True)

    db_path = project_dir / "story_kernel.db"
    s = StoryKernelStore(db_path)
    await s.init_db()

    kernel = await s.create_kernel("test-proj")

    # Add entities
    hero = Entity(entity_id="char-hero", name="风伏京", entity_type="character")
    mentor = Entity(entity_id="char-mentor", name="罗浮", entity_type="character")
    await s.add_entity(hero)
    await s.add_entity(mentor)

    # Add relationship
    rel = Relationship(
        relationship_id="char-hero__char-mentor",
        source_entity_id="char-hero",
        target_entity_id="char-mentor",
        label="师徒亦友",
        trust=0.78,
        tension=0.22,
        shift_summary="并肩应对宫变",
    )
    await s.add_relationship(rel)

    # Save kernel
    kernel = await s.load_kernel("test-proj")
    await s.save_kernel(kernel)

    try:
        yield s, project_dir
    finally:
        await s.close()


# ---------------------------------------------------------------------------
# Data class tests (sync)
# ---------------------------------------------------------------------------


class TestDataClasses:
    """Test the tracker data classes."""

    def test_relationship_snapshot_fields(self) -> None:
        snap = RelationshipSnapshot(
            chapter_number=1,
            public_status="盟友",
            trust=0.8,
            tension=0.2,
            dependency=0.1,
            shift_event="首次结盟",
        )
        assert snap.chapter_number == 1
        assert snap.public_status == "盟友"
        assert snap.trust == 0.8

    def test_relationship_timeline_defaults(self) -> None:
        timeline = RelationshipTimeline(
            pair_id="a__b",
            character_a="a",
            character_b="b",
        )
        assert timeline.snapshots == []
        assert timeline.current_status == ""
        assert timeline.current_trust == 0.5
        assert timeline.current_tension == 0.5

    def test_character_relationship_map(self) -> None:
        t = RelationshipTimeline(pair_id="a__b", character_a="a", character_b="b")
        crm = CharacterRelationshipMap(
            character_name="a",
            relationships=[t],
        )
        assert crm.character_name == "a"
        assert len(crm.relationships) == 1

    def test_relationship_overview_to_dict(self) -> None:
        timeline = RelationshipTimeline(
            pair_id="hero__mentor",
            character_a="风伏京",
            character_b="罗浮",
            current_status="师徒",
            current_trust=0.8,
            current_tension=0.2,
            snapshots=[
                RelationshipSnapshot(
                    chapter_number=1,
                    public_status="师徒",
                    trust=0.8,
                    tension=0.2,
                    dependency=0.0,
                    shift_event="首次相遇",
                ),
            ],
        )
        overview = RelationshipOverview(
            total_relationships=1,
            timelines=[timeline],
            high_tension_pairs=[],
            recent_shifts=[
                {
                    "pair_id": "hero__mentor",
                    "characters": ["风伏京", "罗浮"],
                    "chapter": 1,
                    "event": "首次相遇",
                },
            ],
        )
        d = overview.to_dict()
        assert d["total_relationships"] == 1
        assert len(d["timelines"]) == 1
        assert d["timelines"][0]["pair_id"] == "hero__mentor"
        assert d["timelines"][0]["snapshots"][0]["chapter"] == 1
        assert len(d["recent_shifts"]) == 1

    def test_relationship_overview_from_dict_roundtrip(self) -> None:
        data = {
            "total_relationships": 1,
            "high_tension_pairs": ["pair-x"],
            "recent_shifts": [
                {"pair_id": "pair-x", "characters": ["A", "B"], "chapter": 3, "event": "冲突"}
            ],
            "timelines": [
                {
                    "pair_id": "pair-x",
                    "character_a": "A",
                    "character_b": "B",
                    "current_status": "敌对",
                    "current_trust": 0.2,
                    "current_tension": 0.9,
                    "snapshots": [
                        {
                            "chapter": 3,
                            "status": "敌对",
                            "trust": 0.2,
                            "tension": 0.9,
                            "shift": "冲突",
                        },
                    ],
                },
            ],
        }
        overview = _relationship_overview_from_dict(data)
        assert overview.total_relationships == 1
        assert overview.high_tension_pairs == ["pair-x"]
        assert len(overview.timelines) == 1
        assert overview.timelines[0].character_a == "A"
        assert len(overview.timelines[0].snapshots) == 1
        assert overview.timelines[0].snapshots[0].shift_event == "冲突"


# ---------------------------------------------------------------------------
# Entity map / extraction tests (sync, no store needed)
# ---------------------------------------------------------------------------


class TestEntityHelpers:
    """Test entity map building and timeline extraction."""

    def test_build_entity_map_from_kernel(self) -> None:
        kernel = StoryKernel(
            project_id="test",
            entities=[
                Entity(entity_id="e1", name="角色A", entity_type="character"),
                Entity(entity_id="e2", name="角色B", entity_type="character"),
            ],
        )
        entity_map = _build_entity_map(kernel)
        assert entity_map == {"e1": "角色A", "e2": "角色B"}

    def test_build_entity_map_empty(self) -> None:
        kernel = StoryKernel(project_id="test")
        entity_map = _build_entity_map(kernel)
        assert entity_map == {}

    def test_extract_timelines_from_kernel(self) -> None:
        kernel = StoryKernel(
            project_id="test",
            entities=[
                Entity(entity_id="e1", name="风伏京", entity_type="character"),
                Entity(entity_id="e2", name="罗浮", entity_type="character"),
            ],
            relationships=[
                Relationship(
                    relationship_id="e1__e2",
                    source_entity_id="e1",
                    target_entity_id="e2",
                    label="师徒",
                    trust=0.7,
                    tension=0.3,
                    dependency=0.4,
                    shift_summary="结为师徒",
                ),
            ],
        )
        entity_map = _build_entity_map(kernel)
        timelines: dict[str, RelationshipTimeline] = {}
        _extract_timelines_from_kernel(kernel, 1, timelines, entity_map)

        assert "e1__e2" in timelines
        t = timelines["e1__e2"]
        assert t.character_a == "风伏京"
        assert t.character_b == "罗浮"
        assert len(t.snapshots) == 1
        assert t.snapshots[0].public_status == "师徒"
        assert t.snapshots[0].trust == 0.7
        assert t.snapshots[0].dependency == 0.4
        assert t.snapshots[0].shift_event == "结为师徒"

    def test_extract_timelines_skips_malformed_relationships(self) -> None:
        kernel = StoryKernel(
            project_id="test",
            relationships=[
                Relationship(
                    relationship_id="bad",
                    source_entity_id="char_character_id:_林正",
                    target_entity_id="char_gender:_男",
                    label="合作",
                    trust=0.7,
                ),
                Relationship(
                    relationship_id="good",
                    source_entity_id="char_林正",
                    target_entity_id="char_沈鹿溪",
                    label="盟友",
                    trust=0.8,
                ),
            ],
        )
        timelines: dict[str, RelationshipTimeline] = {}
        _extract_timelines_from_kernel(kernel, 1, timelines, {})

        assert "bad" not in timelines
        assert "good" in timelines

    def test_fill_current_state_adds_missing(self) -> None:
        kernel = StoryKernel(
            project_id="test",
            entities=[
                Entity(entity_id="e1", name="A", entity_type="character"),
                Entity(entity_id="e2", name="B", entity_type="character"),
            ],
            relationships=[
                Relationship(
                    relationship_id="e1__e2",
                    source_entity_id="e1",
                    target_entity_id="e2",
                    label="盟友",
                    trust=0.6,
                    tension=0.4,
                ),
            ],
        )
        entity_map = _build_entity_map(kernel)
        timelines: dict[str, RelationshipTimeline] = {}
        _fill_current_state(kernel, timelines, entity_map)

        assert "e1__e2" in timelines
        t = timelines["e1__e2"]
        assert t.current_status == "盟友"
        assert t.current_trust == 0.6
        assert t.current_tension == 0.4

    def test_fill_current_state_skips_generic_relationship_endpoint(self) -> None:
        kernel = StoryKernel(
            project_id="test",
            relationships=[
                Relationship(
                    relationship_id="bad",
                    source_entity_id="char_林远",
                    target_entity_id="char_镇民",
                    label="认识",
                    trust=0.6,
                ),
            ],
        )
        timelines: dict[str, RelationshipTimeline] = {}
        _fill_current_state(kernel, timelines, {})

        assert timelines == {}

    def test_dirty_relationship_endpoint_tradeoff(self) -> None:
        assert _is_dirty_relationship_endpoint("char_gender:_男") is True
        assert _is_dirty_relationship_endpoint("镇民") is True
        assert _is_dirty_relationship_endpoint("风伏京") is False


# ---------------------------------------------------------------------------
# Character bible fallback tests (sync)
# ---------------------------------------------------------------------------


class TestCharacterBibleFallback:
    """Test seeding timelines from character_bible.json."""

    def test_seeds_from_bible_with_dict_relationships(self, tmp_path: Path) -> None:
        bible = {
            "characters": [
                {
                    "name": "风伏京",
                    "role": "protagonist",
                    "relationships": {"罗浮": "师徒亦友"},
                },
                {
                    "name": "罗浮",
                    "role": "supporting",
                    "relationships": {"风伏京": "亦师亦友"},
                },
            ]
        }
        (tmp_path / "character_bible.json").write_text(
            json.dumps(bible, ensure_ascii=False), encoding="utf-8"
        )

        timelines: dict[str, RelationshipTimeline] = {}
        _seed_timelines_from_character_bible(tmp_path, timelines)

        assert len(timelines) >= 1
        pair_ids = list(timelines.keys())
        # Should have one pair with both characters
        found = False
        for pid in pair_ids:
            t = timelines[pid]
            chars = {t.character_a, t.character_b}
            if chars == {"风伏京", "罗浮"}:
                found = True
                assert t.current_status  # non-empty
                break
        assert found, f"Expected pair not found in {pair_ids}"

    def test_no_bible_file(self, tmp_path: Path) -> None:
        timelines: dict[str, RelationshipTimeline] = {}
        _seed_timelines_from_character_bible(tmp_path, timelines)
        assert timelines == {}

    def test_empty_bible(self, tmp_path: Path) -> None:
        (tmp_path / "character_bible.json").write_text("{}", encoding="utf-8")
        timelines: dict[str, RelationshipTimeline] = {}
        _seed_timelines_from_character_bible(tmp_path, timelines)
        assert timelines == {}


# ---------------------------------------------------------------------------
# Async integration tests (require store)
# ---------------------------------------------------------------------------


class TestBuildRelationshipOverview:
    """Integration tests for build_relationship_overview."""

    async def test_overview_from_kernel_with_relationships(
        self, kernel_with_rels: tuple[StoryKernelStore, Path]
    ) -> None:
        _store, project_dir = kernel_with_rels
        overview = await build_relationship_overview(project_dir, use_cache=False)

        assert overview.total_relationships >= 1
        # Find the hero-mentor pair
        found = False
        for t in overview.timelines:
            chars = {t.character_a, t.character_b}
            if "风伏京" in chars and "罗浮" in chars:
                found = True
                assert t.current_trust == pytest.approx(0.78, abs=0.01)
                assert t.current_tension == pytest.approx(0.22, abs=0.01)
                break
        assert found, "Expected hero-mentor relationship not found"

    async def test_sync_overview_reads_sqlite_story_kernel(
        self, kernel_with_rels: tuple[StoryKernelStore, Path]
    ) -> None:
        """Desktop reads must not create an aiosqlite worker during refresh."""
        _store, project_dir = kernel_with_rels

        overview = build_relationship_overview_sync(project_dir, use_cache=False)

        assert overview.total_relationships >= 1
        assert any(
            {timeline.character_a, timeline.character_b} == {"风伏京", "罗浮"}
            for timeline in overview.timelines
        )

    async def test_overview_empty_kernel(self, store: StoryKernelStore) -> None:
        """Overview returns empty when kernel has no relationships."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td) / "empty-proj"
            project_dir.mkdir(parents=True, exist_ok=True)
            db_path = project_dir / "story_kernel.db"

            # Create a fresh store for this project
            s = StoryKernelStore(db_path)
            await s.init_db()
            await s.create_kernel("empty-proj")
            await s.close()

            overview = await build_relationship_overview(project_dir, use_cache=False)
            # No relationships and no character_bible → empty
            assert overview.total_relationships == 0

    async def test_overview_falls_back_to_character_bible(
        self, store: StoryKernelStore
    ) -> None:
        """When kernel has no relationships, falls back to character_bible."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td) / "bible-proj"
            project_dir.mkdir(parents=True, exist_ok=True)
            db_path = project_dir / "story_kernel.db"

            s = StoryKernelStore(db_path)
            await s.init_db()
            await s.create_kernel("bible-proj")
            await s.close()

            bible = {
                "characters": [
                    {"name": "角色A", "relationships": {"角色B": "朋友"}},
                    {"name": "角色B", "relationships": {"角色A": "朋友"}},
                ]
            }
            (project_dir / "character_bible.json").write_text(
                json.dumps(bible, ensure_ascii=False), encoding="utf-8"
            )

            overview = await build_relationship_overview(project_dir, use_cache=False)
            assert overview.total_relationships >= 1


class TestGetCharacterRelationships:
    """Tests for get_character_relationships."""

    async def test_filters_by_character_name(
        self, kernel_with_rels: tuple[StoryKernelStore, Path]
    ) -> None:
        _store, project_dir = kernel_with_rels
        crm = await get_character_relationships(project_dir, "风伏京")

        assert crm.character_name == "风伏京"
        assert len(crm.relationships) >= 1
        for t in crm.relationships:
            chars = {t.character_a.lower(), t.character_b.lower()}
            assert "风伏京" in chars

    async def test_no_match_returns_empty(
        self, kernel_with_rels: tuple[StoryKernelStore, Path]
    ) -> None:
        _store, project_dir = kernel_with_rels
        crm = await get_character_relationships(project_dir, "不存在的角色")
        assert crm.character_name == "不存在的角色"
        assert crm.relationships == []
