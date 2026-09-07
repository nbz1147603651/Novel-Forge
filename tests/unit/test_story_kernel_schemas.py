"""Tests for StoryKernel Pydantic schemas — unified field pool data models.

TDD: These tests define the expected behavior of the schema layer.
"""

from __future__ import annotations

import json

import pytest

from novel_forge.story_kernel.field_types import (
    EntityType,
    LedgerVisibility,
    RelationType,
)
from novel_forge.story_kernel.schemas import (
    AccessLedger,
    BusinessDependency,
    Entity,
    KnowledgeLedger,
    MotifProtocol,
    ObjectLedger,
    PromiseLedger,
    Relationship,
    StoryKernel,
    TimelineAnchor,
    WorldRule,
)

# ---------------------------------------------------------------------------
# field_types.py enum tests
# ---------------------------------------------------------------------------


class TestEntityType:
    def test_all_values(self) -> None:
        assert EntityType.CHARACTER == "character"
        assert EntityType.LOCATION == "location"
        assert EntityType.ITEM == "item"
        assert EntityType.ORGANIZATION == "organization"
        assert EntityType.CONCEPT == "concept"

    def test_enum_member_count(self) -> None:
        assert len(EntityType) == 5


class TestRelationType:
    def test_all_values(self) -> None:
        assert RelationType.FAMILY == "family"
        assert RelationType.ROMANTIC == "romantic"
        assert RelationType.MENTOR_STUDENT == "mentor_student"
        assert RelationType.BUSINESS == "business"
        assert RelationType.ALLY == "ally"
        assert RelationType.ENEMY == "enemy"
        assert RelationType.RIVAL == "rival"
        assert RelationType.SUBORDINATE == "subordinate"
        assert RelationType.FRIEND == "friend"
        assert RelationType.ACQUAINTANCE == "acquaintance"

    def test_enum_member_count(self) -> None:
        assert len(RelationType) == 10


class TestLedgerVisibility:
    def test_all_values(self) -> None:
        assert LedgerVisibility.PUBLIC == "public"
        assert LedgerVisibility.PRIVATE == "private"
        assert LedgerVisibility.SECRET == "secret"

    def test_enum_member_count(self) -> None:
        assert len(LedgerVisibility) == 3


# ---------------------------------------------------------------------------
# Nested model instantiation tests
# ---------------------------------------------------------------------------


class TestWorldRule:
    def test_minimal(self) -> None:
        rule = WorldRule(rule_id="wr_001", content="Magic costs life force.")
        assert rule.rule_id == "wr_001"
        assert rule.content == "Magic costs life force."
        assert rule.category == "general"
        assert rule.severity == "hard"

    def test_full(self) -> None:
        rule = WorldRule(
            rule_id="wr_002",
            content="Time flows 3x faster in the dream realm.",
            category="temporal",
            severity="soft",
            source_chapter=5,
            notes="Established in ch5 flashback.",
        )
        assert rule.category == "temporal"
        assert rule.severity == "soft"
        assert rule.source_chapter == 5

    def test_json_roundtrip(self) -> None:
        rule = WorldRule(rule_id="wr_003", content="No resurrection.")
        data = rule.model_dump(mode="json")
        restored = WorldRule.model_validate(data)
        assert restored.rule_id == rule.rule_id
        assert restored.content == rule.content


class TestEntity:
    def test_minimal(self) -> None:
        entity = Entity(entity_id="char_001", name="李明")
        assert entity.entity_id == "char_001"
        assert entity.name == "李明"
        assert entity.entity_type == EntityType.CHARACTER
        assert entity.status == "active"

    def test_entity_type_normalization(self) -> None:
        entity = Entity(entity_id="loc_001", name="长安", entity_type="地点")
        assert entity.entity_type == "location"

    def test_json_roundtrip(self) -> None:
        entity = Entity(
            entity_id="item_001",
            name="玉佩",
            entity_type="item",
            aliases=["龙纹玉佩", "传国玉佩"],
        )
        data = entity.model_dump(mode="json")
        restored = Entity.model_validate(data)
        assert restored.entity_id == entity.entity_id
        assert restored.aliases == ["龙纹玉佩", "传国玉佩"]


class TestRelationship:
    def test_minimal(self) -> None:
        rel = Relationship(
            relationship_id="rel_001",
            source_entity_id="char_001",
            target_entity_id="char_002",
        )
        assert rel.relationship_id == "rel_001"
        assert rel.relation_type == RelationType.ACQUAINTANCE
        assert rel.trust == 0.5

    def test_relation_type_normalization(self) -> None:
        rel = Relationship(
            relationship_id="rel_002",
            source_entity_id="char_001",
            target_entity_id="char_003",
            relation_type="师徒",
        )
        assert rel.relation_type == "mentor_student"

    def test_json_roundtrip(self) -> None:
        rel = Relationship(
            relationship_id="rel_003",
            source_entity_id="char_001",
            target_entity_id="char_002",
            relation_type="family",
            trust=0.9,
            tension=0.2,
        )
        data = rel.model_dump(mode="json")
        restored = Relationship.model_validate(data)
        assert restored.trust == 0.9


class TestTimelineAnchor:
    def test_minimal(self) -> None:
        anchor = TimelineAnchor(anchor_id="ta_001", chapter=1, event="主角出生")
        assert anchor.chapter == 1
        assert anchor.significance == "minor"

    def test_json_roundtrip(self) -> None:
        anchor = TimelineAnchor(
            anchor_id="ta_002",
            chapter=3,
            event="发现密室",
            in_story_time="第三天黄昏",
            characters_involved=["char_001"],
        )
        data = anchor.model_dump(mode="json")
        restored = TimelineAnchor.model_validate(data)
        assert restored.in_story_time == "第三天黄昏"


class TestObjectLedger:
    def test_minimal(self) -> None:
        entry = ObjectLedger(entry_id="ol_001", item_name="玉佩")
        assert entry.item_name == "玉佩"
        assert entry.state == "intact"
        assert entry.visibility == LedgerVisibility.PUBLIC

    def test_json_roundtrip(self) -> None:
        entry = ObjectLedger(
            entry_id="ol_002",
            item_name="密函",
            owner_entity_id="char_001",
            state="damaged",
            visibility=LedgerVisibility.SECRET,
        )
        data = entry.model_dump(mode="json")
        restored = ObjectLedger.model_validate(data)
        assert restored.visibility == LedgerVisibility.SECRET


class TestKnowledgeLedger:
    def test_minimal(self) -> None:
        entry = KnowledgeLedger(
            entry_id="kl_001", entity_id="char_001", fact="主角是皇室血脉"
        )
        assert entry.fact == "主角是皇室血脉"
        assert entry.knowledge_type == "known"
        assert entry.confidence == 1.0

    def test_knowledge_type_normalization(self) -> None:
        entry = KnowledgeLedger(
            entry_id="kl_002",
            entity_id="char_001",
            fact="反派可能还活着",
            knowledge_type="怀疑",
        )
        assert entry.knowledge_type == "suspected"

    def test_json_roundtrip(self) -> None:
        entry = KnowledgeLedger(
            entry_id="kl_003",
            entity_id="char_002",
            fact="玉佩是钥匙",
            knowledge_type="secret_kept",
            confidence=0.8,
        )
        data = entry.model_dump(mode="json")
        restored = KnowledgeLedger.model_validate(data)
        assert restored.confidence == 0.8


class TestAccessLedger:
    def test_minimal(self) -> None:
        entry = AccessLedger(entry_id="al_001", entity_id="char_001", target="密室")
        assert entry.target == "密室"
        assert entry.access_type == "allowed"

    def test_json_roundtrip(self) -> None:
        entry = AccessLedger(
            entry_id="al_002",
            entity_id="char_001",
            target="藏书阁",
            access_type="restricted",
            condition="需持有令牌",
        )
        data = entry.model_dump(mode="json")
        restored = AccessLedger.model_validate(data)
        assert restored.condition == "需持有令牌"


class TestPromiseLedger:
    def test_minimal(self) -> None:
        entry = PromiseLedger(
            entry_id="pl_001", description="预言：龙将在月圆之夜归来"
        )
        assert entry.promise_type == "foreshadow"
        assert entry.status == "planted"

    def test_promise_type_normalization(self) -> None:
        entry = PromiseLedger(entry_id="pl_002", description="悬念", promise_type="悬念")
        assert entry.promise_type == "suspense"

    def test_status_normalization(self) -> None:
        entry = PromiseLedger(entry_id="pl_003", description="兑现", status="兑现")
        assert entry.status == "paid"

    def test_json_roundtrip(self) -> None:
        entry = PromiseLedger(
            entry_id="pl_004",
            description="主角的身世之谜",
            promise_type="foreshadow",
            planted_chapter=1,
            owner_entity_ids=["char_001"],
        )
        data = entry.model_dump(mode="json")
        restored = PromiseLedger.model_validate(data)
        assert restored.owner_entity_ids == ["char_001"]


class TestMotifProtocol:
    def test_minimal(self) -> None:
        entry = MotifProtocol(entry_id="mp_001", motif_name="月光意象")
        assert entry.motif_name == "月光意象"
        assert entry.cooldown_chapters == 3
        assert entry.intensity == 0.5

    def test_json_roundtrip(self) -> None:
        entry = MotifProtocol(
            entry_id="mp_002",
            motif_name="镜中对话",
            motif_type="scene_pattern",
            occurrences=[1, 5, 12],
            intensity=0.8,
        )
        data = entry.model_dump(mode="json")
        restored = MotifProtocol.model_validate(data)
        assert restored.occurrences == [1, 5, 12]


class TestBusinessDependency:
    def test_minimal(self) -> None:
        entry = BusinessDependency(
            dependency_id="bd_001", source_id="wr_001", target_id="pl_001"
        )
        assert entry.dependency_type == "requires"
        assert entry.is_active is True

    def test_json_roundtrip(self) -> None:
        entry = BusinessDependency(
            dependency_id="bd_002",
            source_id="char_001",
            target_id="char_002",
            dependency_type="blocks",
            condition="主角未解锁封印",
        )
        data = entry.model_dump(mode="json")
        restored = BusinessDependency.model_validate(data)
        assert restored.dependency_type == "blocks"


# ---------------------------------------------------------------------------
# StoryKernel top-level container tests
# ---------------------------------------------------------------------------


class TestStoryKernel:
    def test_minimal_instantiation(self) -> None:
        kernel = StoryKernel(project_id="test-project")
        assert kernel.project_id == "test-project"
        assert kernel.current_chapter == 0
        assert kernel.active_volume == 1

    def test_all_10_field_groups_present(self) -> None:
        kernel = StoryKernel(project_id="test-project")
        data = kernel.model_dump(mode="json")
        expected_groups = [
            "world_rules",
            "entities",
            "relationships",
            "timeline",
            "object_ledger",
            "knowledge_ledger",
            "access_ledger",
            "promise_ledger",
            "motif_protocols",
            "business_dependencies",
        ]
        for group in expected_groups:
            assert group in data, f"Missing field group: {group}"
            assert isinstance(data[group], list), f"Field group {group} should be a list"

    def test_full_instantiation(self) -> None:
        kernel = StoryKernel(
            project_id="my-novel",
            current_chapter=10,
            active_volume=2,
            title="龙之传承",
            premise="一个少年发现自己是龙族后裔",
            world_rules=[
                WorldRule(rule_id="wr_001", content="龙族只能在月圆之夜变身"),
            ],
            entities=[
                Entity(entity_id="char_001", name="李明", entity_type="character"),
                Entity(entity_id="loc_001", name="长安", entity_type="location"),
            ],
            relationships=[
                Relationship(
                    relationship_id="rel_001",
                    source_entity_id="char_001",
                    target_entity_id="char_002",
                    relation_type="mentor_student",
                ),
            ],
            timeline=[
                TimelineAnchor(anchor_id="ta_001", chapter=1, event="主角出生"),
            ],
            object_ledger=[
                ObjectLedger(entry_id="ol_001", item_name="龙纹玉佩"),
            ],
            knowledge_ledger=[
                KnowledgeLedger(
                    entry_id="kl_001",
                    entity_id="char_001",
                    fact="自己是龙族后裔",
                ),
            ],
            access_ledger=[
                AccessLedger(
                    entry_id="al_001",
                    entity_id="char_001",
                    target="龙族密室",
                    access_type="denied",
                ),
            ],
            promise_ledger=[
                PromiseLedger(
                    entry_id="pl_001",
                    description="龙将在月圆之夜归来",
                    planted_chapter=1,
                ),
            ],
            motif_protocols=[
                MotifProtocol(entry_id="mp_001", motif_name="月光意象"),
            ],
            business_dependencies=[
                BusinessDependency(
                    dependency_id="bd_001",
                    source_id="wr_001",
                    target_id="pl_001",
                    dependency_type="enables",
                ),
            ],
            chapter_summaries={1: "主角在长安出生", 2: "发现龙纹玉佩"},
            banned_phrases=["瞳孔微缩"],
            notes="核心设定文档",
        )
        assert kernel.title == "龙之传承"
        assert len(kernel.world_rules) == 1
        assert len(kernel.entities) == 2
        assert len(kernel.relationships) == 1
        assert len(kernel.timeline) == 1
        assert len(kernel.object_ledger) == 1
        assert len(kernel.knowledge_ledger) == 1
        assert len(kernel.access_ledger) == 1
        assert len(kernel.promise_ledger) == 1
        assert len(kernel.motif_protocols) == 1
        assert len(kernel.business_dependencies) == 1
        assert kernel.chapter_summaries[1] == "主角在长安出生"

    def test_json_roundtrip(self) -> None:
        kernel = StoryKernel(
            project_id="roundtrip-test",
            current_chapter=5,
            entities=[
                Entity(entity_id="char_001", name="测试角色"),
            ],
            world_rules=[
                WorldRule(rule_id="wr_001", content="测试规则"),
            ],
        )
        json_str = kernel.model_dump_json()
        data = json.loads(json_str)
        restored = StoryKernel.model_validate(data)
        assert restored.project_id == "roundtrip-test"
        assert restored.current_chapter == 5
        assert len(restored.entities) == 1
        assert restored.entities[0].name == "测试角色"

    def test_extra_fields_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            StoryKernel(project_id="test", unknown_field="should fail")

    def test_banned_phrases_truncated(self) -> None:
        phrases = [f"phrase_{i}" * 10 for i in range(40)]
        kernel = StoryKernel(project_id="test", banned_phrases=phrases)
        assert len(kernel.banned_phrases) <= 30
        for p in kernel.banned_phrases:
            assert len(p) <= 50

    def test_schema_version_present(self) -> None:
        kernel = StoryKernel(project_id="test")
        assert kernel.schema_version == "2.0"
        assert kernel.created_at is not None


# ---------------------------------------------------------------------------
# StoryKernel helper method tests
# ---------------------------------------------------------------------------


class TestStoryKernelHelpers:
    @pytest.fixture()
    def kernel(self) -> StoryKernel:
        return StoryKernel(
            project_id="helper-test",
            entities=[
                Entity(entity_id="char_001", name="李明"),
                Entity(entity_id="char_002", name="王芳"),
                Entity(entity_id="loc_001", name="长安", entity_type="location"),
            ],
            relationships=[
                Relationship(
                    relationship_id="rel_001",
                    source_entity_id="char_001",
                    target_entity_id="char_002",
                    relation_type="romantic",
                ),
                Relationship(
                    relationship_id="rel_002",
                    source_entity_id="char_001",
                    target_entity_id="loc_001",
                    relation_type="acquaintance",
                ),
            ],
            timeline=[
                TimelineAnchor(anchor_id="ta_001", chapter=1, event="出生"),
                TimelineAnchor(anchor_id="ta_002", chapter=1, event="发现玉佩"),
                TimelineAnchor(anchor_id="ta_003", chapter=3, event="离开长安"),
            ],
            promise_ledger=[
                PromiseLedger(
                    entry_id="pl_001", description="龙的预言", status="planted"
                ),
                PromiseLedger(
                    entry_id="pl_002", description="身世之谜", status="paid"
                ),
            ],
            motif_protocols=[
                MotifProtocol(
                    entry_id="mp_001",
                    motif_name="月光",
                    last_used_chapter=5,
                    cooldown_chapters=3,
                ),
                MotifProtocol(
                    entry_id="mp_002",
                    motif_name="镜中对话",
                    last_used_chapter=10,
                    cooldown_chapters=3,
                ),
            ],
        )

    def test_get_entity_by_id(self, kernel: StoryKernel) -> None:
        entity = kernel.get_entity_by_id("char_001")
        assert entity is not None
        assert entity.name == "李明"

    def test_get_entity_by_id_not_found(self, kernel: StoryKernel) -> None:
        entity = kernel.get_entity_by_id("nonexistent")
        assert entity is None

    def test_get_relationships_for_entity(self, kernel: StoryKernel) -> None:
        rels = kernel.get_relationships_for_entity("char_001")
        assert len(rels) == 2

    def test_get_relationships_for_entity_none(self, kernel: StoryKernel) -> None:
        rels = kernel.get_relationships_for_entity("nonexistent")
        assert len(rels) == 0

    def test_get_timeline_for_chapter(self, kernel: StoryKernel) -> None:
        events = kernel.get_timeline_for_chapter(1)
        assert len(events) == 2

    def test_get_timeline_for_chapter_empty(self, kernel: StoryKernel) -> None:
        events = kernel.get_timeline_for_chapter(99)
        assert len(events) == 0

    def test_get_promises_by_status(self, kernel: StoryKernel) -> None:
        planted = kernel.get_promises_by_status("planted")
        assert len(planted) == 1
        paid = kernel.get_promises_by_status("paid")
        assert len(paid) == 1

    def test_get_active_motifs(self, kernel: StoryKernel) -> None:
        active = kernel.get_active_motifs(current_chapter=10)
        assert len(active) == 1
        assert active[0].motif_name == "月光"

    def test_get_active_motifs_none(self, kernel: StoryKernel) -> None:
        active = kernel.get_active_motifs(current_chapter=6)
        assert len(active) == 0
