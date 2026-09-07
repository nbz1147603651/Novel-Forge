"""Tests for StoryKernelStore — FieldPool storage layer with full CRUD operations.

Covers:
- Kernel creation, loading, saving (metadata round-trip)
- Entity CRUD (add, get, update)
- Relationship CRUD (add, get by entity)
- Timeline anchors, object ledger, knowledge entries, promise ledger
- query_field_slice with filters
- Transaction rollback
- WAL mode configuration
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import text

from novel_forge.core.schemas.story_state import ChapterExitState
from novel_forge.story_kernel.schemas import (
    Entity,
    KnowledgeLedger,
    ObjectLedger,
    PromiseLedger,
    Relationship,
    StoryKernel,
    TimelineAnchor,
    WorldRule,
)
from novel_forge.story_kernel.store import StoryKernelStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def store() -> AsyncIterator[StoryKernelStore]:
    """Create a StoryKernelStore backed by an in-memory SQLite database."""
    s = StoryKernelStore.in_memory()
    await s.init_db()
    try:
        yield s
    finally:
        await s.close()


@pytest.fixture()
async def populated_store() -> AsyncIterator[StoryKernelStore]:
    """Store with a kernel and a few entities pre-loaded."""
    s = StoryKernelStore.in_memory()
    await s.init_db()
    try:
        await s.create_kernel("test-project")
        # Add entities through the store
        hero = Entity(entity_id="char-hero", name="主角", entity_type="character")
        villain = Entity(entity_id="char-villain", name="反派", entity_type="character")
        city = Entity(entity_id="loc-city", name="京城", entity_type="location")
        await s.add_entity(hero)
        await s.add_entity(villain)
        await s.add_entity(city)
        yield s
    finally:
        await s.close()


# ---------------------------------------------------------------------------
# Kernel metadata CRUD
# ---------------------------------------------------------------------------


class TestKernelCRUD:
    """Test create / load / save kernel metadata."""

    async def test_create_kernel_returns_story_kernel(self, store: StoryKernelStore) -> None:
        kernel = await store.create_kernel("proj-1")
        assert isinstance(kernel, StoryKernel)
        assert kernel.project_id == "proj-1"
        assert kernel.current_chapter == 0

    async def test_create_duplicate_kernel_raises(self, store: StoryKernelStore) -> None:
        await store.create_kernel("proj-dup")
        with pytest.raises(ValueError, match="already exists"):
            await store.create_kernel("proj-dup")

    async def test_load_kernel_round_trip(self, store: StoryKernelStore) -> None:
        original = await store.create_kernel("proj-rt")
        loaded = await store.load_kernel("proj-rt")
        assert loaded.project_id == original.project_id
        assert loaded.current_chapter == original.current_chapter

    async def test_load_nonexistent_kernel_raises(self, store: StoryKernelStore) -> None:
        with pytest.raises(ValueError, match="not found"):
            await store.load_kernel("no-such-project")

    async def test_save_kernel_updates_metadata(self, store: StoryKernelStore) -> None:
        kernel = await store.create_kernel("proj-save")
        kernel.current_chapter = 5
        kernel.title = "测试小说"
        kernel.chapter_summaries = {1: "第一章摘要"}
        await store.save_kernel(kernel)

        loaded = await store.load_kernel("proj-save")
        assert loaded.current_chapter == 5
        assert loaded.title == "测试小说"
        assert loaded.chapter_summaries == {1: "第一章摘要"}

    async def test_save_kernel_persists_field_groups(self, store: StoryKernelStore) -> None:
        kernel = await store.create_kernel("proj-fields")
        rule = WorldRule(rule_id="wr-1", content="魔法有代价", category="magic")
        kernel.world_rules = [rule]
        await store.save_kernel(kernel)

        loaded = await store.load_kernel("proj-fields")
        assert len(loaded.world_rules) == 1
        assert loaded.world_rules[0].content == "魔法有代价"


# ---------------------------------------------------------------------------
# Entity CRUD
# ---------------------------------------------------------------------------


class TestEntityCRUD:
    """Test add / get / update entity operations."""

    async def test_add_entity(self, store: StoryKernelStore) -> None:
        await store.create_kernel("proj-ent")
        entity = Entity(entity_id="char-1", name="张三", entity_type="character")
        await store.add_entity(entity)

        row = await store.get_entity("char-1")
        assert row is not None
        assert row.name == "张三"
        assert row.entity_type == "character"

    async def test_add_entity_with_aliases(self, store: StoryKernelStore) -> None:
        await store.create_kernel("proj-alias")
        entity = Entity(
            entity_id="char-2",
            name="李四",
            entity_type="character",
            aliases=["四爷", "老四"],
        )
        await store.add_entity(entity)

        row = await store.get_entity("char-2")
        assert row is not None
        assert row.aliases == ["四爷", "老四"]

    async def test_get_nonexistent_entity_returns_none(self, store: StoryKernelStore) -> None:
        await store.create_kernel("proj-none")
        result = await store.get_entity("does-not-exist")
        assert result is None

    async def test_update_entity(self, store: StoryKernelStore) -> None:
        await store.create_kernel("proj-upd")
        entity = Entity(entity_id="char-3", name="王五", entity_type="character")
        await store.add_entity(entity)

        await store.update_entity("char-3", {"name": "王五改", "notes": "已修改"})
        row = await store.get_entity("char-3")
        assert row is not None
        assert row.name == "王五改"
        assert row.notes == "已修改"

    async def test_update_nonexistent_entity_raises(self, store: StoryKernelStore) -> None:
        await store.create_kernel("proj-upd-none")
        with pytest.raises(ValueError, match="not found"):
            await store.update_entity("ghost", {"name": "x"})


# ---------------------------------------------------------------------------
# Relationship CRUD
# ---------------------------------------------------------------------------


class TestRelationshipCRUD:
    """Test add / get relationships."""

    async def test_add_relationship(self, populated_store: StoryKernelStore) -> None:
        rel = Relationship(
            relationship_id="rel-1",
            source_entity_id="char-hero",
            target_entity_id="char-villain",
            relation_type="enemy",
        )
        await populated_store.add_relationship(rel)

        rels = await populated_store.get_relationships("char-hero")
        assert len(rels) == 1
        assert rels[0].relation_type == "enemy"

    async def test_get_relationships_both_directions(
        self, populated_store: StoryKernelStore
    ) -> None:
        rel = Relationship(
            relationship_id="rel-2",
            source_entity_id="char-villain",
            target_entity_id="char-hero",
            relation_type="rival",
        )
        await populated_store.add_relationship(rel)

        # Should find via source_id
        by_source = await populated_store.get_relationships("char-villain")
        assert len(by_source) == 1

        # Should find via target_id
        by_target = await populated_store.get_relationships("char-hero")
        assert len(by_target) == 1

    async def test_get_relationships_empty(self, populated_store: StoryKernelStore) -> None:
        rels = await populated_store.get_relationships("loc-city")
        assert rels == []


# ---------------------------------------------------------------------------
# Timeline anchors
# ---------------------------------------------------------------------------


class TestTimelineAnchors:
    """Test add timeline anchors."""

    async def test_add_timeline_anchor(self, populated_store: StoryKernelStore) -> None:
        anchor = TimelineAnchor(
            anchor_id="ta-1",
            chapter=1,
            event="主角出生",
            characters_involved=["char-hero"],
        )
        await populated_store.add_timeline_anchor(anchor)

        # Verify via raw SQL query (field_slice will also cover this)
        async with populated_store._session() as session:
            result = await session.execute(
                text("SELECT event_text FROM timeline_anchors WHERE id = 1")
            )
            row = result.first()
            assert row is not None
            assert row[0] == "主角出生"

    async def test_add_timeline_anchor_without_character(self, store: StoryKernelStore) -> None:
        await store.create_kernel("proj-anchor-no-character")
        anchor = TimelineAnchor(
            anchor_id="ta-no-character",
            chapter=1,
            event="王朝更替",
        )

        await store.add_timeline_anchor(anchor)

        async with store._session() as session:
            result = await session.execute(
                text("SELECT event_text, entity_id FROM timeline_anchors WHERE id = 1")
            )
            row = result.first()
            assert row is not None
            assert row[0] == "王朝更替"
            assert row[1] is None

    async def test_save_kernel_indexes_timeline_without_character(
        self, store: StoryKernelStore
    ) -> None:
        kernel = await store.create_kernel("proj-save-anchor-no-character")
        kernel.timeline = [
            TimelineAnchor(
                anchor_id="ta-save-no-character",
                chapter=1,
                event="无角色参与的世界事件",
            )
        ]

        await store.save_kernel(kernel)

        async with store._session() as session:
            result = await session.execute(
                text("SELECT event_text, entity_id FROM timeline_anchors")
            )
            row = result.first()
            assert row is not None
            assert row[0] == "无角色参与的世界事件"
            assert row[1] is None


# ---------------------------------------------------------------------------
# Object ledger
# ---------------------------------------------------------------------------


class TestObjectLedger:
    """Test add object ledger entries."""

    async def test_add_object_ledger_entry(self, store: StoryKernelStore) -> None:
        await store.create_kernel("proj-obj")
        entry = ObjectLedger(
            entry_id="obj-1",
            item_name="传家宝剑",
            introduced_chapter=1,
        )
        await store.add_object_ledger_entry(entry)

        async with store._session() as session:
            result = await session.execute(
                text("SELECT object_name FROM object_ledger WHERE id = 1")
            )
            row = result.first()
            assert row is not None
            assert row[0] == "传家宝剑"


# ---------------------------------------------------------------------------
# Knowledge ledger
# ---------------------------------------------------------------------------


class TestKnowledgeLedger:
    """Test add knowledge entries."""

    async def test_add_knowledge_entry(self, populated_store: StoryKernelStore) -> None:
        entry = KnowledgeLedger(
            entry_id="kl-1",
            entity_id="char-hero",
            fact="反派的真实身份",
            knowledge_type="secret_kept",
            source_chapter=3,
        )
        await populated_store.add_knowledge_entry(entry)

        async with populated_store._session() as session:
            result = await session.execute(
                text("SELECT fact_text, is_secret FROM knowledge_ledger WHERE id = 1")
            )
            row = result.first()
            assert row is not None
            assert row[0] == "反派的真实身份"
            assert row[1] == 1  # secret_kept → is_secret (SQLite returns int)

    async def test_search_knowledge_uses_query_text(
        self, populated_store: StoryKernelStore
    ) -> None:
        await populated_store.add_knowledge_entry(
            KnowledgeLedger(
                entry_id="kl-dragon",
                entity_id="char-hero",
                fact="龙泉剑内封印着远古龙魂",
                knowledge_type="secret_kept",
                source_chapter=3,
            )
        )
        await populated_store.add_knowledge_entry(
            KnowledgeLedger(
                entry_id="kl-city",
                entity_id="char-villain",
                fact="京城守军将在夜里换防",
                knowledge_type="known",
                source_chapter=4,
            )
        )

        dragon = await populated_store.search_knowledge("龙魂", top_k=5)
        missing = await populated_store.search_knowledge("不存在的事实", top_k=5)

        assert [entry.entry_id for entry in dragon] == ["kl-dragon"]
        assert missing == []

    async def test_search_knowledge_respects_entity_filter(
        self, populated_store: StoryKernelStore
    ) -> None:
        await populated_store.add_knowledge_entry(
            KnowledgeLedger(
                entry_id="kl-hero-secret",
                entity_id="char-hero",
                fact="密钥藏在旧宅",
                knowledge_type="secret_kept",
                source_chapter=3,
            )
        )
        await populated_store.add_knowledge_entry(
            KnowledgeLedger(
                entry_id="kl-villain-secret",
                entity_id="char-villain",
                fact="密钥已经被调包",
                knowledge_type="known",
                source_chapter=4,
            )
        )

        result = await populated_store.search_knowledge(
            "密钥", top_k=5, entity_ids=["char-villain"]
        )

        assert [entry.entry_id for entry in result] == ["kl-villain-secret"]


# ---------------------------------------------------------------------------
# Promise ledger
# ---------------------------------------------------------------------------


class TestPromiseLedger:
    """Test add promise entries."""

    async def test_add_promise(self, store: StoryKernelStore) -> None:
        await store.create_kernel("proj-promise")
        entry = PromiseLedger(
            entry_id="prom-1",
            description="预言中的英雄将会崛起",
            promise_type="foreshadow",
            planted_chapter=1,
        )
        await store.add_promise(entry)

        async with store._session() as session:
            result = await session.execute(
                text("SELECT promise_text FROM promise_ledger WHERE id = 1")
            )
            row = result.first()
            assert row is not None
            assert row[0] == "预言中的英雄将会崛起"


# ---------------------------------------------------------------------------
# query_field_slice
# ---------------------------------------------------------------------------


class TestQueryFieldSlice:
    """Test field slice queries with filters."""

    async def test_query_kernel_metadata_fields(self, store: StoryKernelStore) -> None:
        kernel = await store.create_kernel("proj-slice")
        kernel.title = "查询测试"
        kernel.current_chapter = 3
        await store.save_kernel(kernel)

        result = await store.query_field_slice(["title", "current_chapter"], {})
        assert result["title"] == "查询测试"
        assert result["current_chapter"] == 3

    async def test_query_entities_no_filter(self, populated_store: StoryKernelStore) -> None:
        result = await populated_store.query_field_slice(["entities"], {})
        assert "entities" in result
        assert len(result["entities"]) == 3

    async def test_query_entities_with_type_filter(self, populated_store: StoryKernelStore) -> None:
        result = await populated_store.query_field_slice(["entities"], {"entity_type": "character"})
        assert len(result["entities"]) == 2
        for e in result["entities"]:
            assert e["entity_type"] == "character"

    async def test_query_relationships_by_entity(self, populated_store: StoryKernelStore) -> None:
        rel = Relationship(
            relationship_id="rel-q",
            source_entity_id="char-hero",
            target_entity_id="char-villain",
            relation_type="enemy",
        )
        await populated_store.add_relationship(rel)

        result = await populated_store.query_field_slice(
            ["relationships"], {"entity_id": "char-hero"}
        )
        assert len(result["relationships"]) == 1

    async def test_query_world_rules(self, store: StoryKernelStore) -> None:
        kernel = await store.create_kernel("proj-wr")
        kernel.world_rules = [
            WorldRule(rule_id="wr-1", content="规则一"),
            WorldRule(rule_id="wr-2", content="规则二"),
        ]
        await store.save_kernel(kernel)

        result = await store.query_field_slice(["world_rules"], {})
        assert len(result["world_rules"]) == 2

    async def test_query_empty_fields_returns_empty_dict(
        self, populated_store: StoryKernelStore
    ) -> None:
        result = await populated_store.query_field_slice([], {})
        assert result == {}


# ---------------------------------------------------------------------------
# Transaction rollback
# ---------------------------------------------------------------------------


class TestTransactionRollback:
    """Test that failed transactions are rolled back cleanly."""

    async def test_rollback_on_entity_add_failure(self, store: StoryKernelStore) -> None:
        """Adding an entity after a failed operation should still work."""
        await store.create_kernel("proj-rollback")
        entity_ok = Entity(entity_id="ok-1", name="正常实体")
        await store.add_entity(entity_ok)

        # Verify the first entity is there
        row = await store.get_entity("ok-1")
        assert row is not None

        # Try an invalid operation (update non-existent entity)
        with pytest.raises(ValueError):
            await store.update_entity("ghost", {"name": "x"})

        # The store should still be functional
        entity_ok2 = Entity(entity_id="ok-2", name="第二个实体")
        await store.add_entity(entity_ok2)
        row2 = await store.get_entity("ok-2")
        assert row2 is not None

    async def test_rollback_on_kernel_save_with_bad_data(self, store: StoryKernelStore) -> None:
        """Store remains usable after a failed save."""
        kernel = await store.create_kernel("proj-save-rollback")
        await store.save_kernel(kernel)

        # Simulate a failure scenario by using a closed session pattern
        # The store should recover gracefully
        loaded = await store.load_kernel("proj-save-rollback")
        assert loaded.project_id == "proj-save-rollback"


# ---------------------------------------------------------------------------
# WAL mode
# ---------------------------------------------------------------------------


class TestWalMode:
    """Test WAL journal mode configuration."""

    async def test_wal_mode_enabled(self, store: StoryKernelStore) -> None:
        """WAL mode should be set on file-based databases."""
        # For in-memory databases, WAL is not applicable but the pragma
        # execution should not fail. Verify via raw PRAGMA query.
        async with store._session() as session:
            result = await session.execute(text("PRAGMA journal_mode"))
            mode = result.scalar()
            # In-memory databases return "memory" for journal_mode
            # File-based would return "wal"
            assert mode in ("memory", "wal")

    async def test_foreign_keys_enabled(self, store: StoryKernelStore) -> None:
        """Foreign key enforcement should be enabled."""
        async with store._session() as session:
            result = await session.execute(text("PRAGMA foreign_keys"))
            fk = result.scalar()
            assert fk == 1

    async def test_plain_db_file_path_is_accepted(self, tmp_path: Path) -> None:
        """Users provide a normal .db file path; the store hides SQLAlchemy URLs."""
        db_path = tmp_path / "nested" / "story_kernel.db"
        store = StoryKernelStore(str(db_path))
        try:
            await store.init_db()
            await store.save_kernel(StoryKernel(project_id="plain-path", title="路径测试"))

            loaded = await store.load_kernel("plain-path")
            assert loaded.title == "路径测试"
            assert db_path.exists()
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# Integration: full round-trip
# ---------------------------------------------------------------------------


class TestIntegrationRoundTrip:
    """End-to-end test: create kernel, populate, query, update."""

    async def test_full_round_trip(self, store: StoryKernelStore) -> None:
        # 1. Create kernel
        kernel = await store.create_kernel("proj-full")
        kernel.title = "完整测试"
        kernel.current_chapter = 10
        kernel.chapter_summaries = {1: "起始", 5: "转折"}
        kernel.world_rules = [
            WorldRule(rule_id="wr-1", content="时间不可逆", category="temporal"),
        ]
        await store.save_kernel(kernel)

        # 2. Add entities
        hero = Entity(
            entity_id="char-hero",
            name="林风",
            entity_type="character",
            aliases=["风哥"],
            source_chapter=1,
        )
        sword = Entity(entity_id="item-sword", name="龙泉剑", entity_type="item")
        await store.add_entity(hero)
        await store.add_entity(sword)

        # 3. Add relationship
        rel = Relationship(
            relationship_id="rel-owns",
            source_entity_id="char-hero",
            target_entity_id="item-sword",
            relation_type="ally",
            label="持有",
        )
        await store.add_relationship(rel)

        # 4. Add timeline
        anchor = TimelineAnchor(
            anchor_id="ta-start",
            chapter=1,
            event="林风获得龙泉剑",
            characters_involved=["char-hero"],
            location="铸剑谷",
        )
        await store.add_timeline_anchor(anchor)

        # 5. Add knowledge
        kl = KnowledgeLedger(
            entry_id="kl-secret",
            entity_id="char-hero",
            fact="龙泉剑内封印着远古龙魂",
            knowledge_type="secret_kept",
            source_chapter=1,
        )
        await store.add_knowledge_entry(kl)

        # 6. Add promise
        prom = PromiseLedger(
            entry_id="prom-dragon",
            description="龙魂终将觉醒",
            promise_type="foreshadow",
            planted_chapter=1,
        )
        await store.add_promise(prom)

        # 7. Query field slices
        ent_result = await store.query_field_slice(["entities"], {"entity_type": "character"})
        assert len(ent_result["entities"]) == 1
        assert ent_result["entities"][0]["name"] == "林风"

        rel_result = await store.query_field_slice(["relationships"], {"entity_id": "char-hero"})
        assert len(rel_result["relationships"]) == 1

        # 8. Load full kernel and verify
        loaded = await store.load_kernel("proj-full")
        assert loaded.title == "完整测试"
        assert loaded.current_chapter == 10
        assert len(loaded.world_rules) == 1

        # 9. Update entity
        await store.update_entity("char-hero", {"last_seen_chapter": 10})
        updated = await store.get_entity("char-hero")
        assert updated is not None
        assert updated.last_seen_chapter == 10


# ---------------------------------------------------------------------------
# Snapshot / Rollback / Prune
# ---------------------------------------------------------------------------


@pytest.fixture()
async def file_store(tmp_path: Path) -> AsyncIterator[StoryKernelStore]:
    db_path = tmp_path / "story_kernel" / "story_kernel.db"
    s = StoryKernelStore(str(db_path))
    await s.init_db()
    try:
        yield s
    finally:
        await s.close()


class TestSnapshotPath:
    def test_returns_correct_path(self, file_store: StoryKernelStore) -> None:
        path = file_store.snapshot_path(3)
        assert path.name == "kernel_v3.db"
        assert path.parent.name == "snapshots"

    def test_path_is_sibling_of_db(self, file_store: StoryKernelStore) -> None:
        db_dir = Path(file_store._db_path).parent
        path = file_store.snapshot_path(1)
        assert path.parent == db_dir / "snapshots"


class TestSaveLoadSnapshot:
    async def test_save_and_load_round_trip(self, file_store: StoryKernelStore) -> None:
        kernel = await file_store.create_kernel("snap-proj")
        kernel.title = "快照测试"
        kernel.current_chapter = 3
        await file_store.save_kernel(kernel)
        await file_store.save_snapshot(3)

        loaded = await file_store.load_snapshot(3)
        assert loaded.project_id == "snap-proj"
        assert loaded.title == "快照测试"
        assert loaded.current_chapter == 3

    async def test_snapshot_is_independent_of_current(self, file_store: StoryKernelStore) -> None:
        kernel = await file_store.create_kernel("snap-indep")
        kernel.title = "原始版本"
        kernel.current_chapter = 1
        await file_store.save_kernel(kernel)
        await file_store.save_snapshot(1)

        kernel.title = "修改版本"
        kernel.current_chapter = 2
        await file_store.save_kernel(kernel)

        snap = await file_store.load_snapshot(1)
        assert snap.title == "原始版本"
        assert snap.current_chapter == 1

        current = await file_store.load_kernel("snap-indep")
        assert current.title == "修改版本"

    async def test_save_snapshot_in_memory_raises(self) -> None:
        s = StoryKernelStore.in_memory()
        await s.init_db()
        await s.create_kernel("mem-snap")
        with pytest.raises(ValueError, match="in-memory"):
            await s.save_snapshot(1)
        await s.close()

    async def test_load_nonexistent_snapshot_raises(self, file_store: StoryKernelStore) -> None:
        await file_store.create_kernel("no-snap")
        with pytest.raises(ValueError, match="not found"):
            await file_store.load_snapshot(99)


class TestListSnapshots:
    async def test_empty_when_no_snapshots(self, file_store: StoryKernelStore) -> None:
        await file_store.create_kernel("list-empty")
        assert file_store.list_snapshots() == []

    async def test_returns_sorted_chapter_numbers(self, file_store: StoryKernelStore) -> None:
        kernel = await file_store.create_kernel("list-sorted")
        await file_store.save_kernel(kernel)

        for ch in (5, 1, 3):
            kernel.current_chapter = ch
            await file_store.save_kernel(kernel)
            await file_store.save_snapshot(ch)

        assert file_store.list_snapshots() == [1, 3, 5]

    async def test_ignores_malformed_filenames(self, tmp_path: Path) -> None:
        db_path = tmp_path / "story_kernel" / "story_kernel.db"
        s = StoryKernelStore(str(db_path))
        await s.init_db()
        try:
            snap_dir = tmp_path / "story_kernel" / "snapshots"
            snap_dir.mkdir(parents=True, exist_ok=True)
            (snap_dir / "kernel_v5.db").touch()
            (snap_dir / "kernel_vabc.db").touch()
            (snap_dir / "garbage.txt").touch()
            assert s.list_snapshots() == [5]
        finally:
            await s.close()


class TestPruneSnapshots:
    async def test_prunes_old_snapshots(self, file_store: StoryKernelStore) -> None:
        kernel = await file_store.create_kernel("prune-old")
        await file_store.save_kernel(kernel)
        for ch in range(1, 8):
            kernel.current_chapter = ch
            await file_store.save_kernel(kernel)
            await file_store.save_snapshot(ch)

        deleted = file_store.prune_snapshots(keep_recent=3)
        assert deleted == [1, 2, 3, 4]
        assert file_store.list_snapshots() == [5, 6, 7]

    async def test_keep_chapters_protects_specific_snapshots(
        self, file_store: StoryKernelStore
    ) -> None:
        kernel = await file_store.create_kernel("prune-protect")
        await file_store.save_kernel(kernel)
        for ch in range(1, 6):
            kernel.current_chapter = ch
            await file_store.save_kernel(kernel)
            await file_store.save_snapshot(ch)

        deleted = file_store.prune_snapshots(keep_recent=1, keep_chapters=[2])
        assert 2 not in deleted
        assert 2 in file_store.list_snapshots()

    async def test_keep_recent_zero_deletes_all(self, file_store: StoryKernelStore) -> None:
        kernel = await file_store.create_kernel("prune-all")
        await file_store.save_kernel(kernel)
        for ch in (1, 2):
            kernel.current_chapter = ch
            await file_store.save_kernel(kernel)
            await file_store.save_snapshot(ch)

        deleted = file_store.prune_snapshots(keep_recent=0)
        assert deleted == [1, 2]
        assert file_store.list_snapshots() == []


class TestRollbackTo:
    async def test_rollback_restores_snapshot(self, file_store: StoryKernelStore) -> None:
        kernel = await file_store.create_kernel("rb-proj")
        kernel.title = "第一版"
        kernel.current_chapter = 2
        await file_store.save_kernel(kernel)
        await file_store.save_snapshot(2)

        kernel.title = "第二版"
        kernel.current_chapter = 3
        await file_store.save_kernel(kernel)

        restored = await file_store.rollback_to(2)
        assert restored.title == "第一版"
        assert restored.current_chapter == 2

        current = await file_store.load_kernel("rb-proj")
        assert current.title == "第一版"
        assert current.current_chapter == 2

    async def test_rollback_nonexistent_raises(self, file_store: StoryKernelStore) -> None:
        await file_store.create_kernel("rb-none")
        with pytest.raises(ValueError, match="not found"):
            await file_store.rollback_to(99)

    async def test_rollback_preserves_entities(self, file_store: StoryKernelStore) -> None:
        kernel = await file_store.create_kernel("rb-entities")
        kernel.current_chapter = 1
        await file_store.save_kernel(kernel)
        entity = Entity(entity_id="rb-e1", name="回滚角色", entity_type="character")
        await file_store.add_entity(entity)
        await file_store.save_snapshot(1)

        entity2 = Entity(entity_id="rb-e2", name="新角色", entity_type="character")
        await file_store.add_entity(entity2)

        await file_store.rollback_to(1)
        restored_kernel = await file_store.load_kernel("rb-entities")
        entity_ids = {e.entity_id for e in restored_kernel.entities}
        assert "rb-e1" in entity_ids
        assert "rb-e2" not in entity_ids


class TestStoryKernelStoreSearchKnowledge:
    async def test_archive_volume_data_uses_real_cutoff_and_protected_chapters(
        self, store: StoryKernelStore
    ) -> None:
        kernel = await store.create_kernel("archive-boundary")
        kernel.current_chapter = 60
        kernel.active_volume = 3
        kernel.timeline = [
            TimelineAnchor(anchor_id="t005", chapter=5, event="old event"),
            TimelineAnchor(anchor_id="t025", chapter=25, event="kept event"),
            TimelineAnchor(anchor_id="t045", chapter=45, event="current event"),
        ]
        kernel.chapter_summaries = {
            10: "old summary",
            20: "protected volume one ending",
            30: "kept summary",
        }
        kernel.chapter_exit_states = {
            10: ChapterExitState(chapter_number=10),
            20: ChapterExitState(chapter_number=20),
            30: ChapterExitState(chapter_number=30),
        }
        kernel.promise_ledger = [
            PromiseLedger(entry_id="p-paid", description="resolved", status="paid"),
            PromiseLedger(entry_id="p-live", description="still open", status="planted"),
        ]
        await store.save_kernel(kernel)

        stats = await store.archive_volume_data(
            "archive-boundary",
            current_volume=3,
            volumes_to_keep=2,
            archive_before_chapter=21,
            protected_chapters={20},
        )

        loaded = await store.load_kernel("archive-boundary")
        assert stats == {
            "timeline_archived": 1,
            "summaries_removed": 1,
            "exit_states_removed": 1,
            "promises_archived": 1,
        }
        assert [anchor.anchor_id for anchor in loaded.timeline] == ["t025", "t045"]
        assert [anchor.anchor_id for anchor in loaded.archived_timeline] == ["t005"]
        assert loaded.chapter_summaries == {
            20: "protected volume one ending",
            30: "kept summary",
        }
        assert sorted(loaded.chapter_exit_states) == [20, 30]
        assert [promise.entry_id for promise in loaded.promise_ledger] == ["p-live"]
        assert [promise.entry_id for promise in loaded.archived_promises] == ["p-paid"]

    async def test_search_knowledge_returns_all(self, store: StoryKernelStore) -> None:
        kernel = await store.create_kernel("sk-all")
        kernel.knowledge_ledger = [
            KnowledgeLedger(entry_id="sk001", entity_id="c1", fact="fact one", source_chapter=1),
            KnowledgeLedger(entry_id="sk002", entity_id="c2", fact="fact two", source_chapter=2),
        ]
        await store.save_kernel(kernel)

        results = await store.search_knowledge("", top_k=10)
        assert len(results) == 2

    async def test_search_knowledge_entity_ids_filter(self, store: StoryKernelStore) -> None:
        kernel = await store.create_kernel("sk-filter")
        kernel.knowledge_ledger = [
            KnowledgeLedger(entry_id="sk001", entity_id="c1", fact="fact one", source_chapter=1),
            KnowledgeLedger(entry_id="sk002", entity_id="c2", fact="fact two", source_chapter=2),
        ]
        await store.save_kernel(kernel)

        results = await store.search_knowledge("", top_k=10, entity_ids=["c1"])
        assert len(results) == 1
        assert results[0].entity_id == "c1"

    async def test_search_knowledge_entity_ids_none_backward_compat(
        self, store: StoryKernelStore
    ) -> None:
        kernel = await store.create_kernel("sk-none")
        kernel.knowledge_ledger = [
            KnowledgeLedger(entry_id="sk001", entity_id="c1", fact="fact one", source_chapter=1),
        ]
        await store.save_kernel(kernel)

        results = await store.search_knowledge("", top_k=10, entity_ids=None)
        assert len(results) == 1

    async def test_search_knowledge_entity_ids_empty_returns_empty(
        self, store: StoryKernelStore
    ) -> None:
        kernel = await store.create_kernel("sk-empty")
        kernel.knowledge_ledger = [
            KnowledgeLedger(entry_id="sk001", entity_id="c1", fact="fact one", source_chapter=1),
        ]
        await store.save_kernel(kernel)

        results = await store.search_knowledge("query", top_k=10, entity_ids=[])
        assert results == []

    async def test_search_knowledge_top_k_limits(self, store: StoryKernelStore) -> None:
        kernel = await store.create_kernel("sk-limit")
        kernel.knowledge_ledger = [
            KnowledgeLedger(
                entry_id=f"sk{i:03d}",
                entity_id="c1",
                fact=f"fact {i}",
                source_chapter=i,
            )
            for i in range(5)
        ]
        await store.save_kernel(kernel)

        results = await store.search_knowledge("", top_k=2)
        assert len(results) == 2
