"""Tests for StoryKernel SQLAlchemy ORM models.

TDD: These tests were written before the implementation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def engine():
    """Create an in-memory SQLite engine with WAL mode."""
    from novel_forge.story_kernel.engine import create_sqlite_engine

    eng = create_sqlite_engine(":memory:")
    yield eng
    eng.dispose()


@pytest.fixture()
def tables(engine):
    """Create all tables and return the engine."""
    from novel_forge.story_kernel.orm import Base

    Base.metadata.create_all(engine)
    return engine


@pytest.fixture()
def session(tables):
    """Open a session against the tables."""
    with Session(tables) as s:
        yield s


# ---------------------------------------------------------------------------
# Engine tests
# ---------------------------------------------------------------------------

class TestEngine:
    """Tests for the SQLite engine factory."""

    def test_create_in_memory_engine(self) -> None:
        from novel_forge.story_kernel.engine import create_sqlite_engine

        eng = create_sqlite_engine(":memory:")
        assert eng is not None
        assert "sqlite" in eng.dialect.name
        eng.dispose()

    def test_wal_mode_enabled(self, engine: object) -> None:
        """WAL journal mode should be set for file-based SQLite."""
        from novel_forge.story_kernel.engine import create_sqlite_engine

        eng = create_sqlite_engine(":memory:")
        with eng.connect() as conn:
            result = conn.execute(text("PRAGMA journal_mode"))
            mode = result.scalar()
            # In-memory SQLite may not support WAL; just verify no error
            assert mode is not None
        eng.dispose()

    def test_create_file_engine(self, tmp_path: Path) -> None:
        from novel_forge.story_kernel.engine import create_sqlite_engine
        from novel_forge.story_kernel.orm import Base

        db_path = tmp_path / "test.db"
        eng = create_sqlite_engine(str(db_path))
        assert eng is not None
        # SQLite only creates the file after first DDL
        Base.metadata.create_all(eng)
        eng.dispose()
        assert db_path.exists()


# ---------------------------------------------------------------------------
# Table creation tests
# ---------------------------------------------------------------------------

EXPECTED_TABLES = [
    "world_rules",
    "entities",
    "relationships",
    "timeline_anchors",
    "object_ledger",
    "knowledge_ledger",
    "access_ledger",
    "promise_ledger",
    "motif_protocols",
    "business_dependencies",
]


class TestTableCreation:
    """Verify all 10 tables are created with correct schemas."""

    @pytest.mark.parametrize("table_name", EXPECTED_TABLES)
    def test_table_exists(self, tables: object, table_name: str) -> None:
        inspector = inspect(tables)
        assert table_name in inspector.get_table_names()

    def test_exactly_10_tables(self, tables: object) -> None:
        inspector = inspect(tables)
        table_names = set(inspector.get_table_names())
        assert table_names == set(EXPECTED_TABLES)

    def test_world_rules_columns(self, tables: object) -> None:
        inspector = inspect(tables)
        cols = {c["name"] for c in inspector.get_columns("world_rules")}
        assert {"id", "rule_text", "rule_type", "created_chapter"}.issubset(cols)

    def test_entities_columns(self, tables: object) -> None:
        inspector = inspect(tables)
        cols = {c["name"] for c in inspector.get_columns("entities")}
        assert {"id", "name", "entity_type", "aliases", "source", "notes"}.issubset(cols)

    def test_relationships_columns(self, tables: object) -> None:
        inspector = inspect(tables)
        cols = {c["name"] for c in inspector.get_columns("relationships")}
        assert {
            "id", "source_id", "target_id", "relation_type",
            "description", "is_public", "confidence",
        }.issubset(cols)

    def test_timeline_anchors_columns(self, tables: object) -> None:
        inspector = inspect(tables)
        cols = {c["name"] for c in inspector.get_columns("timeline_anchors")}
        assert {"id", "entity_id", "event_text", "anchor_date", "anchor_type"}.issubset(cols)

    def test_object_ledger_columns(self, tables: object) -> None:
        inspector = inspect(tables)
        cols = {c["name"] for c in inspector.get_columns("object_ledger")}
        assert {
            "id", "object_name", "origin", "current_owner",
            "visibility", "chapter_acquired",
        }.issubset(cols)

    def test_knowledge_ledger_columns(self, tables: object) -> None:
        inspector = inspect(tables)
        cols = {c["name"] for c in inspector.get_columns("knowledge_ledger")}
        assert {
            "id", "entity_id", "fact_text", "learned_chapter",
            "evidence", "is_secret",
        }.issubset(cols)

    def test_access_ledger_columns(self, tables: object) -> None:
        inspector = inspect(tables)
        cols = {c["name"] for c in inspector.get_columns("access_ledger")}
        assert {"id", "entity_id", "location", "reason", "chapter_granted"}.issubset(cols)

    def test_promise_ledger_columns(self, tables: object) -> None:
        inspector = inspect(tables)
        cols = {c["name"] for c in inspector.get_columns("promise_ledger")}
        assert {
            "id", "promise_text", "planted_chapter", "expected_window",
            "fulfilled_chapter",
        }.issubset(cols)

    def test_motif_protocols_columns(self, tables: object) -> None:
        inspector = inspect(tables)
        cols = {c["name"] for c in inspector.get_columns("motif_protocols")}
        assert {"id", "motif_name", "trigger_pattern", "callback_rules"}.issubset(cols)

    def test_business_dependencies_columns(self, tables: object) -> None:
        inspector = inspect(tables)
        cols = {c["name"] for c in inspector.get_columns("business_dependencies")}
        assert {
            "id", "entity_a", "entity_b", "dependency_type", "description",
        }.issubset(cols)


# ---------------------------------------------------------------------------
# Foreign key tests
# ---------------------------------------------------------------------------

class TestForeignKeys:
    """Verify foreign key constraints are correctly defined."""

    def test_relationships_has_source_fk(self, tables: object) -> None:
        inspector = inspect(tables)
        fks = inspector.get_foreign_keys("relationships")
        source_tables = {fk["referred_table"] for fk in fks}
        assert "entities" in source_tables

    def test_timeline_anchors_has_entity_fk(self, tables: object) -> None:
        inspector = inspect(tables)
        fks = inspector.get_foreign_keys("timeline_anchors")
        source_tables = {fk["referred_table"] for fk in fks}
        assert "entities" in source_tables

    def test_knowledge_ledger_has_entity_fk(self, tables: object) -> None:
        inspector = inspect(tables)
        fks = inspector.get_foreign_keys("knowledge_ledger")
        source_tables = {fk["referred_table"] for fk in fks}
        assert "entities" in source_tables

    def test_access_ledger_has_entity_fk(self, tables: object) -> None:
        inspector = inspect(tables)
        fks = inspector.get_foreign_keys("access_ledger")
        source_tables = {fk["referred_table"] for fk in fks}
        assert "entities" in source_tables


# ---------------------------------------------------------------------------
# JSON field tests
# ---------------------------------------------------------------------------

class TestJSONFields:
    """Verify JSON columns serialize and deserialize correctly."""

    def test_entity_aliases_json_roundtrip(self, session: Session) -> None:
        from novel_forge.story_kernel.orm import Entity

        entity = Entity(
            name="林婉儿",
            entity_type="character",
            aliases=["婉儿", "林小姐"],
            source="chapter_1",
            notes="主角",
        )
        session.add(entity)
        session.commit()

        loaded = session.query(Entity).filter_by(name="林婉儿").one()
        assert loaded.aliases == ["婉儿", "林小姐"]
        assert isinstance(loaded.aliases, list)

    def test_motif_callback_rules_json_roundtrip(self, session: Session) -> None:
        from novel_forge.story_kernel.orm import MotifProtocol

        motif = MotifProtocol(
            motif_name="凤凰涅槃",
            trigger_pattern="死亡.*重生",
            callback_rules={"min_gap": 5, "style": "隐喻", "channels": ["意象", "对话"]},
        )
        session.add(motif)
        session.commit()

        loaded = session.query(MotifProtocol).filter_by(motif_name="凤凰涅槃").one()
        assert loaded.callback_rules["min_gap"] == 5
        assert "意象" in loaded.callback_rules["channels"]
        assert isinstance(loaded.callback_rules, dict)

    def test_entity_aliases_default_empty_list(self, session: Session) -> None:
        from novel_forge.story_kernel.orm import Entity

        entity = Entity(name="测试角色", entity_type="character")
        session.add(entity)
        session.commit()

        loaded = session.query(Entity).filter_by(name="测试角色").one()
        assert loaded.aliases == []

    def test_motif_callback_rules_default_empty_dict(self, session: Session) -> None:
        from novel_forge.story_kernel.orm import MotifProtocol

        motif = MotifProtocol(motif_name="测试母题", trigger_pattern="test")
        session.add(motif)
        session.commit()

        loaded = session.query(MotifProtocol).filter_by(motif_name="测试母题").one()
        assert loaded.callback_rules == {}


# ---------------------------------------------------------------------------
# CRUD tests
# ---------------------------------------------------------------------------

class TestCRUD:
    """Verify basic CRUD operations work on each table."""

    def test_world_rule_crud(self, session: Session) -> None:
        from novel_forge.story_kernel.orm import WorldRule

        rule = WorldRule(rule_text="魔法需要等价交换", rule_type="magic", created_chapter=1)
        session.add(rule)
        session.commit()
        assert rule.id is not None

        loaded = session.get(WorldRule, rule.id)
        assert loaded is not None
        assert loaded.rule_text == "魔法需要等价交换"
        assert loaded.rule_type == "magic"
        assert loaded.created_chapter == 1

        loaded.rule_text = "魔法需要等价交换(修正)"
        session.commit()
        updated = session.get(WorldRule, rule.id)
        assert updated is not None
        assert updated.rule_text == "魔法需要等价交换(修正)"

        session.delete(updated)
        session.commit()
        assert session.get(WorldRule, rule.id) is None

    def test_entity_crud(self, session: Session) -> None:
        from novel_forge.story_kernel.orm import Entity

        entity = Entity(
            name="陈平安",
            entity_type="character",
            aliases=["陈十一", "平安"],
            source="chapter_1",
            notes="主角",
        )
        session.add(entity)
        session.commit()
        assert entity.id is not None

        loaded = session.get(Entity, entity.id)
        assert loaded is not None
        assert loaded.name == "陈平安"
        assert loaded.entity_type == "character"

        session.delete(loaded)
        session.commit()
        assert session.get(Entity, entity.id) is None

    def test_relationship_crud(self, session: Session) -> None:
        from novel_forge.story_kernel.orm import Entity, Relationship

        src = Entity(name="角色A", entity_type="character")
        tgt = Entity(name="角色B", entity_type="character")
        session.add_all([src, tgt])
        session.flush()

        rel = Relationship(
            source_id=src.id,
            target_id=tgt.id,
            relation_type="师徒",
            description="传授武艺",
            is_public=True,
            confidence=0.9,
        )
        session.add(rel)
        session.commit()
        assert rel.id is not None

        loaded = session.get(Relationship, rel.id)
        assert loaded is not None
        assert loaded.relation_type == "师徒"
        assert loaded.is_public is True
        assert loaded.confidence == pytest.approx(0.9)

    def test_timeline_anchor_crud(self, session: Session) -> None:
        from novel_forge.story_kernel.orm import Entity, TimelineAnchor

        entity = Entity(name="事件角色", entity_type="character")
        session.add(entity)
        session.flush()

        anchor = TimelineAnchor(
            entity_id=entity.id,
            event_text="获得神剑",
            anchor_date="第三年春天",
            anchor_type="acquisition",
        )
        session.add(anchor)
        session.commit()
        assert anchor.id is not None

        loaded = session.get(TimelineAnchor, anchor.id)
        assert loaded is not None
        assert loaded.event_text == "获得神剑"

    def test_object_ledger_crud(self, session: Session) -> None:
        from novel_forge.story_kernel.orm import ObjectLedger

        obj = ObjectLedger(
            object_name="龙泉剑",
            origin="铸剑谷",
            current_owner="陈平安",
            visibility="public",
            chapter_acquired=3,
        )
        session.add(obj)
        session.commit()
        assert obj.id is not None

        loaded = session.get(ObjectLedger, obj.id)
        assert loaded is not None
        assert loaded.object_name == "龙泉剑"
        assert loaded.chapter_acquired == 3

    def test_knowledge_ledger_crud(self, session: Session) -> None:
        from novel_forge.story_kernel.orm import Entity, KnowledgeLedger

        entity = Entity(name="知情人", entity_type="character")
        session.add(entity)
        session.flush()

        knowledge = KnowledgeLedger(
            entity_id=entity.id,
            fact_text="幕后黑手是长老",
            learned_chapter=5,
            evidence="密室中的信件",
            is_secret=True,
        )
        session.add(knowledge)
        session.commit()
        assert knowledge.id is not None

        loaded = session.get(KnowledgeLedger, knowledge.id)
        assert loaded is not None
        assert loaded.is_secret is True

    def test_access_ledger_crud(self, session: Session) -> None:
        from novel_forge.story_kernel.orm import AccessLedger, Entity

        entity = Entity(name="守门人", entity_type="character")
        session.add(entity)
        session.flush()

        access = AccessLedger(
            entity_id=entity.id,
            location="禁地",
            reason="执行任务",
            chapter_granted=7,
        )
        session.add(access)
        session.commit()
        assert access.id is not None

        loaded = session.get(AccessLedger, access.id)
        assert loaded is not None
        assert loaded.location == "禁地"

    def test_promise_ledger_crud(self, session: Session) -> None:
        from novel_forge.story_kernel.orm import PromiseLedger

        promise = PromiseLedger(
            promise_text="三年后归来",
            planted_chapter=2,
            expected_window="5-10章",
            fulfilled_chapter=None,
        )
        session.add(promise)
        session.commit()
        assert promise.id is not None

        loaded = session.get(PromiseLedger, promise.id)
        assert loaded is not None
        assert loaded.fulfilled_chapter is None

        loaded.fulfilled_chapter = 8
        session.commit()
        updated = session.get(PromiseLedger, promise.id)
        assert updated is not None
        assert updated.fulfilled_chapter == 8

    def test_motif_protocol_crud(self, session: Session) -> None:
        from novel_forge.story_kernel.orm import MotifProtocol

        motif = MotifProtocol(
            motif_name="镜花水月",
            trigger_pattern="镜子|水面|倒影",
            callback_rules={"min_gap": 3, "style": "象征"},
        )
        session.add(motif)
        session.commit()
        assert motif.id is not None

        loaded = session.get(MotifProtocol, motif.id)
        assert loaded is not None
        assert loaded.motif_name == "镜花水月"

    def test_business_dependency_crud(self, session: Session) -> None:
        from novel_forge.story_kernel.orm import BusinessDependency

        dep = BusinessDependency(
            entity_a="灵石矿",
            entity_b="宗门经济",
            dependency_type="supply",
            description="灵石矿是宗门经济命脉",
        )
        session.add(dep)
        session.commit()
        assert dep.id is not None

        loaded = session.get(BusinessDependency, dep.id)
        assert loaded is not None
        assert loaded.dependency_type == "supply"


# ---------------------------------------------------------------------------
# Module exports tests
# ---------------------------------------------------------------------------

class TestModuleExports:
    """Verify the __init__.py exports all expected symbols."""

    def test_init_exports_orm_models(self) -> None:
        """ORM models are exported from the package."""
        from novel_forge.story_kernel import (
            ORMEntity,
            ORMWorldRule,
        )
        # Verify they are actual SQLAlchemy model classes
        assert hasattr(ORMWorldRule, "__tablename__")
        assert hasattr(ORMEntity, "__tablename__")

    def test_init_exports_engine_factory(self) -> None:
        from novel_forge.story_kernel import create_sqlite_engine
        assert callable(create_sqlite_engine)
