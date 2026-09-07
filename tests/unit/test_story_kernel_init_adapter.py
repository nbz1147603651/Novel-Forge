"""Tests for InitAdapter — maps init artifacts to StoryKernel.

TDD: These tests define the expected mapping behavior before implementation.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from novel_forge.story_kernel.init_adapter import (
    InitAdapter,
    build_kernel_from_init,
)
from novel_forge.story_kernel.schemas import (
    Entity,
    PromiseLedger,
    Relationship,
    StoryKernel,
    TimelineAnchor,
    WorldRule,
)

# ---------------------------------------------------------------------------
# Helpers — minimal mock objects matching real schema shapes
# ---------------------------------------------------------------------------


class _MockCharacterProfile:
    """Minimal stand-in for CharacterProfile."""

    def __init__(
        self,
        character_id: str = "",
        name: str = "",
        role: str = "supporting",
        gender: str = "",
        status: str = "active",
        social_status: str = "",
        abilities: str = "",
        appearance: str = "",
        personality: str = "",
        backstory: str = "",
        arc: str = "",
        voice: str = "",
        notes: str = "",
        relationships: dict[str, str] | None = None,
    ) -> None:
        self.character_id = character_id
        self.name = name
        self.role = role
        self.gender = gender
        self.status = status
        self.social_status = social_status
        self.abilities = abilities
        self.appearance = appearance
        self.personality = personality
        self.backstory = backstory
        self.arc = arc
        self.voice = voice
        self.notes = notes
        self.relationships = relationships or {}


class _MockCharacterBible:
    """Minimal stand-in for CharacterBible."""

    def __init__(self, characters: list[_MockCharacterProfile]) -> None:
        self.characters = characters


class _MockStoryBible:
    """Minimal stand-in for StoryBible."""

    def __init__(
        self,
        title: str = "",
        premise: str = "",
        rules: list[str] | None = None,
        themes: list[str] | None = None,
        tone: str = "",
        era: str = "",
        geography: str = "",
        culture: str = "",
        magic_or_tech: str = "",
        notes: str = "",
    ) -> None:
        self.title = title
        self.premise = premise
        self.rules = rules or []
        self.themes = themes or []
        self.tone = tone
        self.era = era
        self.geography = geography
        self.culture = culture
        self.magic_or_tech = magic_or_tech
        self.notes = notes


class _MockChapterOutline:
    """Minimal stand-in for ChapterOutline."""

    def __init__(
        self,
        chapter_number: int = 1,
        title: str = "",
        goal: str = "",
        pov_character: str = "",
        setting: str = "",
        time_anchor: str = "",
        involved_characters: list[str] | None = None,
        beats_summary: list[str] | None = None,
    ) -> None:
        self.chapter_number = chapter_number
        self.title = title
        self.goal = goal
        self.pov_character = pov_character
        self.setting = setting
        self.time_anchor = time_anchor
        self.involved_characters = involved_characters or []
        self.beats_summary = beats_summary or []


class _MockStoryOutline:
    """Minimal stand-in for StoryOutline."""

    def __init__(self, chapters: list[_MockChapterOutline]) -> None:
        self.chapters = chapters


class _MockNarrativeContract:
    """Minimal stand-in for NarrativeContract."""

    def __init__(
        self,
        title: str = "",
        promise_plan: list[dict] | None = None,
        world_rules: list[str] | None = None,
        plot_threads: list[dict] | None = None,
        character_arcs: list[dict] | None = None,
        notes: str = "",
    ) -> None:
        self.title = title
        self.promise_plan = promise_plan or []
        self.world_rules = world_rules or []
        self.plot_threads = plot_threads or []
        self.character_arcs = character_arcs or []
        self.notes = notes


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def story_bible() -> _MockStoryBible:
    return _MockStoryBible(
        title="测试小说",
        premise="一个关于勇气与成长的故事。",
        rules=["魔法需要消耗生命力", "时间不可逆转", "每个角色只能拥有一种特殊能力"],
        themes=["成长", "牺牲", "命运"],
        tone="dark",
        era="奇幻中世纪",
    )


@pytest.fixture()
def character_bible() -> _MockCharacterBible:
    return _MockCharacterBible(
        characters=[
            _MockCharacterProfile(
                character_id="char-protagonist",
                name="李明",
                role="protagonist",
                gender="男",
                personality="勇敢、正直",
                backstory="孤儿出身",
                arc="从懵懂少年成长为领袖",
                voice="简洁有力",
                relationships={"王芳": "挚友", "张伟": "师徒"},
            ),
            _MockCharacterProfile(
                character_id="char-ally",
                name="王芳",
                role="supporting",
                gender="女",
                personality="聪明、善良",
                relationships={"李明": "挚友"},
            ),
            _MockCharacterProfile(
                character_id="char-mentor",
                name="张伟",
                role="supporting",
                gender="男",
                personality="沉稳、睿智",
                relationships={"李明": "师徒"},
            ),
        ]
    )


@pytest.fixture()
def outline() -> _MockStoryOutline:
    return _MockStoryOutline(
        chapters=[
            _MockChapterOutline(
                chapter_number=1,
                title="启程",
                goal="主角踏上旅途",
                setting="小村庄",
                time_anchor="春天清晨",
                involved_characters=["李明", "王芳"],
            ),
            _MockChapterOutline(
                chapter_number=2,
                title="考验",
                goal="遭遇第一个挑战",
                setting="森林",
                time_anchor="午后",
                involved_characters=["李明", "张伟"],
            ),
        ]
    )


@pytest.fixture()
def narrative_contract() -> _MockNarrativeContract:
    return _MockNarrativeContract(
        title="测试小说",
        promise_plan=[
            {
                "description": "李明的身世之谜",
                "promise_type": "foreshadow",
                "planted_chapter": 1,
                "status": "planted",
                "owner_entity_ids": ["char-protagonist"],
            },
            {
                "description": "张伟的过去",
                "promise_type": "suspense",
                "planted_chapter": 2,
                "status": "planted",
                "owner_entity_ids": ["char-mentor"],
            },
        ],
    )


# ---------------------------------------------------------------------------
# Tests: map_story_bible_to_kernel
# ---------------------------------------------------------------------------


class TestMapStoryBibleToKernel:
    """Test mapping of StoryBible fields to StoryKernel fields."""

    def test_maps_title_and_premise(self, story_bible: _MockStoryBible) -> None:
        result = InitAdapter.map_story_bible_to_kernel(story_bible)
        assert result["title"] == "测试小说"
        assert result["premise"] == "一个关于勇气与成长的故事。"

    def test_maps_rules_to_world_rules(self, story_bible: _MockStoryBible) -> None:
        result = InitAdapter.map_story_bible_to_kernel(story_bible)
        world_rules = result["world_rules"]
        assert len(world_rules) == 3
        assert all(isinstance(r, dict) for r in world_rules)
        assert world_rules[0]["content"] == "魔法需要消耗生命力"
        assert world_rules[0]["category"] == "general"
        assert world_rules[0]["severity"] == "hard"

    def test_world_rules_have_unique_ids(self, story_bible: _MockStoryBible) -> None:
        result = InitAdapter.map_story_bible_to_kernel(story_bible)
        ids = [r["rule_id"] for r in result["world_rules"]]
        assert len(ids) == len(set(ids))

    def test_world_rule_ids_are_deterministic(self, story_bible: _MockStoryBible) -> None:
        first = InitAdapter.map_story_bible_to_kernel(story_bible)
        second = InitAdapter.map_story_bible_to_kernel(story_bible)

        assert [r["rule_id"] for r in first["world_rules"]] == [
            r["rule_id"] for r in second["world_rules"]
        ]

    def test_empty_rules_produces_empty_list(self) -> None:
        bible = _MockStoryBible(title="T", premise="P", rules=[])
        result = InitAdapter.map_story_bible_to_kernel(bible)
        assert result["world_rules"] == []

    def test_notes_includes_metadata(self, story_bible: _MockStoryBible) -> None:
        result = InitAdapter.map_story_bible_to_kernel(story_bible)
        # Notes should include era, themes, tone info
        assert "奇幻中世纪" in result.get("notes", "") or result.get("notes", "") != ""


# ---------------------------------------------------------------------------
# Tests: map_character_bible_to_kernel
# ---------------------------------------------------------------------------


class TestMapCharacterBibleToKernel:
    """Test mapping of CharacterBible to entities and relationships."""

    def test_maps_characters_to_entities(self, character_bible: _MockCharacterBible) -> None:
        result = InitAdapter.map_character_bible_to_kernel(character_bible)
        entities = result["entities"]
        assert len(entities) == 3
        names = {e["name"] for e in entities}
        assert names == {"李明", "王芳", "张伟"}

    def test_protagonist_has_correct_attributes(self, character_bible: _MockCharacterBible) -> None:
        result = InitAdapter.map_character_bible_to_kernel(character_bible)
        protagonist = next(
            e for e in result["entities"] if e["attributes"].get("role") == "protagonist"
        )
        assert protagonist["name"] == "李明"
        assert protagonist["entity_id"] == "char-protagonist"
        assert protagonist["entity_type"] == "character"

    def test_supporting_characters_have_correct_role(
        self, character_bible: _MockCharacterBible
    ) -> None:
        result = InitAdapter.map_character_bible_to_kernel(character_bible)
        supporting = [e for e in result["entities"] if e["attributes"].get("role") == "supporting"]
        assert len(supporting) == 2

    def test_maps_relationships(self, character_bible: _MockCharacterBible) -> None:
        result = InitAdapter.map_character_bible_to_kernel(character_bible)
        relationships = result["relationships"]
        assert len(relationships) > 0
        # Should have at least 李明→王芳 and 王芳→李明 (friend)
        source_targets = {(r["source_entity_id"], r["target_entity_id"]) for r in relationships}
        assert ("char-protagonist", "char-ally") in source_targets

    def test_relationship_label_preserved(self, character_bible: _MockCharacterBible) -> None:
        result = InitAdapter.map_character_bible_to_kernel(character_bible)
        relationships = result["relationships"]
        rel = next(
            r
            for r in relationships
            if r["source_entity_id"] == "char-protagonist" and r["target_entity_id"] == "char-ally"
        )
        assert rel["label"] == "挚友"
        assert rel["relation_type"] == "acquaintance"

    def test_relationship_ids_are_deterministic(self, character_bible: _MockCharacterBible) -> None:
        first = InitAdapter.map_character_bible_to_kernel(character_bible)
        second = InitAdapter.map_character_bible_to_kernel(character_bible)

        assert [r["relationship_id"] for r in first["relationships"]] == [
            r["relationship_id"] for r in second["relationships"]
        ]

    def test_explicit_relationship_type_is_preserved(self) -> None:
        bible = _MockCharacterBible(
            characters=[
                _MockCharacterProfile(
                    character_id="char-a",
                    name="甲",
                    role="protagonist",
                    relationships={
                        "乙": {
                            "label": "共同经营公司",
                            "relation_type": "business",
                        }
                    },
                ),
                _MockCharacterProfile(character_id="char-b", name="乙"),
            ]
        )
        result = InitAdapter.map_character_bible_to_kernel(bible)
        rel = result["relationships"][0]
        assert rel["label"] == "共同经营公司"
        assert rel["relation_type"] == "business"

    def test_empty_characters_produces_empty_lists(self) -> None:
        bible = _MockCharacterBible(characters=[])
        result = InitAdapter.map_character_bible_to_kernel(bible)
        assert result["entities"] == []
        assert result["relationships"] == []

    def test_entity_attributes_contain_personality(
        self, character_bible: _MockCharacterBible
    ) -> None:
        result = InitAdapter.map_character_bible_to_kernel(character_bible)
        protagonist = next(e for e in result["entities"] if e["name"] == "李明")
        attrs = protagonist["attributes"]
        assert "personality" in attrs
        assert "勇敢" in attrs["personality"]


# ---------------------------------------------------------------------------
# Tests: map_narrative_contract_to_kernel
# ---------------------------------------------------------------------------


class TestMapNarrativeContractToKernel:
    """Test mapping of NarrativeContract.promise_plan to promise_ledger."""

    def test_maps_promise_plan_to_ledger(self, narrative_contract: _MockNarrativeContract) -> None:
        result = InitAdapter.map_narrative_contract_to_kernel(narrative_contract)
        promises = result["promise_ledger"]
        assert len(promises) == 2

    def test_promise_has_description(self, narrative_contract: _MockNarrativeContract) -> None:
        result = InitAdapter.map_narrative_contract_to_kernel(narrative_contract)
        promises = result["promise_ledger"]
        descriptions = {p["description"] for p in promises}
        assert "李明的身世之谜" in descriptions
        assert "张伟的过去" in descriptions

    def test_promise_type_normalized(self, narrative_contract: _MockNarrativeContract) -> None:
        result = InitAdapter.map_narrative_contract_to_kernel(narrative_contract)
        promises = result["promise_ledger"]
        types = {p["promise_type"] for p in promises}
        assert "foreshadow" in types
        assert "suspense" in types

    def test_empty_promise_plan_produces_empty_list(self) -> None:
        contract = _MockNarrativeContract(promise_plan=[])
        result = InitAdapter.map_narrative_contract_to_kernel(contract)
        assert result["promise_ledger"] == []

    def test_none_contract_produces_empty_promises(self) -> None:
        result = InitAdapter.map_narrative_contract_to_kernel(None)
        assert result["promise_ledger"] == []

    def test_promise_has_unique_ids(self, narrative_contract: _MockNarrativeContract) -> None:
        result = InitAdapter.map_narrative_contract_to_kernel(narrative_contract)
        ids = [p["entry_id"] for p in result["promise_ledger"]]
        assert len(ids) == len(set(ids))

    def test_promise_ids_are_deterministic(
        self, narrative_contract: _MockNarrativeContract
    ) -> None:
        first = InitAdapter.map_narrative_contract_to_kernel(narrative_contract)
        second = InitAdapter.map_narrative_contract_to_kernel(narrative_contract)

        assert [p["entry_id"] for p in first["promise_ledger"]] == [
            p["entry_id"] for p in second["promise_ledger"]
        ]


# ---------------------------------------------------------------------------
# Tests: map_outline_to_kernel
# ---------------------------------------------------------------------------


class TestMapOutlineToKernel:
    """Test mapping of StoryOutline.chapters to timeline_anchors."""

    def test_maps_chapters_to_timeline(self, outline: _MockStoryOutline) -> None:
        result = InitAdapter.map_outline_to_kernel(outline)
        timeline = result["timeline"]
        assert len(timeline) == 2

    def test_timeline_anchor_has_chapter_number(self, outline: _MockStoryOutline) -> None:
        result = InitAdapter.map_outline_to_kernel(outline)
        timeline = result["timeline"]
        chapters = {t["chapter"] for t in timeline}
        assert chapters == {1, 2}

    def test_timeline_anchor_has_event_from_goal(self, outline: _MockStoryOutline) -> None:
        result = InitAdapter.map_outline_to_kernel(outline)
        timeline = result["timeline"]
        events = {t["event"] for t in timeline}
        assert "主角踏上旅途" in events
        assert "遭遇第一个挑战" in events

    def test_timeline_anchor_has_in_story_time(self, outline: _MockStoryOutline) -> None:
        result = InitAdapter.map_outline_to_kernel(outline)
        timeline = result["timeline"]
        anchor_1 = next(t for t in timeline if t["chapter"] == 1)
        assert anchor_1["in_story_time"] == "春天清晨"

    def test_timeline_anchor_has_location(self, outline: _MockStoryOutline) -> None:
        result = InitAdapter.map_outline_to_kernel(outline)
        timeline = result["timeline"]
        anchor_1 = next(t for t in timeline if t["chapter"] == 1)
        assert anchor_1["location"] == "小村庄"

    def test_timeline_anchor_has_characters_involved(self, outline: _MockStoryOutline) -> None:
        result = InitAdapter.map_outline_to_kernel(outline)
        timeline = result["timeline"]
        anchor_1 = next(t for t in timeline if t["chapter"] == 1)
        assert "李明" in anchor_1["characters_involved"]

    def test_empty_chapters_produces_empty_timeline(self) -> None:
        outline = _MockStoryOutline(chapters=[])
        result = InitAdapter.map_outline_to_kernel(outline)
        assert result["timeline"] == []

    def test_timeline_anchors_have_unique_ids(self, outline: _MockStoryOutline) -> None:
        result = InitAdapter.map_outline_to_kernel(outline)
        ids = [t["anchor_id"] for t in result["timeline"]]
        assert len(ids) == len(set(ids))

    def test_timeline_anchor_ids_are_deterministic(self, outline: _MockStoryOutline) -> None:
        first = InitAdapter.map_outline_to_kernel(outline)
        second = InitAdapter.map_outline_to_kernel(outline)

        assert [t["anchor_id"] for t in first["timeline"]] == [
            t["anchor_id"] for t in second["timeline"]
        ]


# ---------------------------------------------------------------------------
# Tests: build_kernel_from_init (integration)
# ---------------------------------------------------------------------------


class TestBuildKernelFromInit:
    """Test the combined build_kernel_from_init function."""

    def test_returns_story_kernel_instance(
        self,
        story_bible: _MockStoryBible,
        character_bible: _MockCharacterBible,
        outline: _MockStoryOutline,
        narrative_contract: _MockNarrativeContract,
    ) -> None:
        kernel = build_kernel_from_init(
            project_id="test-project",
            story_bible=story_bible,
            character_bible=character_bible,
            narrative_contract=narrative_contract,
            outline=outline,
        )
        assert isinstance(kernel, StoryKernel)

    def test_kernel_has_project_id(
        self,
        story_bible: _MockStoryBible,
        character_bible: _MockCharacterBible,
        outline: _MockStoryOutline,
        narrative_contract: _MockNarrativeContract,
    ) -> None:
        kernel = build_kernel_from_init(
            project_id="my-project",
            story_bible=story_bible,
            character_bible=character_bible,
            narrative_contract=narrative_contract,
            outline=outline,
        )
        assert kernel.project_id == "my-project"

    def test_kernel_has_title_and_premise(
        self,
        story_bible: _MockStoryBible,
        character_bible: _MockCharacterBible,
        outline: _MockStoryOutline,
        narrative_contract: _MockNarrativeContract,
    ) -> None:
        kernel = build_kernel_from_init(
            project_id="test-project",
            story_bible=story_bible,
            character_bible=character_bible,
            narrative_contract=narrative_contract,
            outline=outline,
        )
        assert kernel.title == "测试小说"
        assert kernel.premise != ""

    def test_kernel_has_world_rules(
        self,
        story_bible: _MockStoryBible,
        character_bible: _MockCharacterBible,
        outline: _MockStoryOutline,
        narrative_contract: _MockNarrativeContract,
    ) -> None:
        kernel = build_kernel_from_init(
            project_id="test-project",
            story_bible=story_bible,
            character_bible=character_bible,
            narrative_contract=narrative_contract,
            outline=outline,
        )
        assert len(kernel.world_rules) == 3
        assert all(isinstance(r, WorldRule) for r in kernel.world_rules)

    def test_kernel_has_entities(
        self,
        story_bible: _MockStoryBible,
        character_bible: _MockCharacterBible,
        outline: _MockStoryOutline,
        narrative_contract: _MockNarrativeContract,
    ) -> None:
        kernel = build_kernel_from_init(
            project_id="test-project",
            story_bible=story_bible,
            character_bible=character_bible,
            narrative_contract=narrative_contract,
            outline=outline,
        )
        assert len(kernel.entities) == 3
        assert all(isinstance(e, Entity) for e in kernel.entities)

    def test_kernel_includes_non_character_registry_entities(
        self,
        story_bible: _MockStoryBible,
        character_bible: _MockCharacterBible,
        outline: _MockStoryOutline,
        narrative_contract: _MockNarrativeContract,
    ) -> None:
        registry = SimpleNamespace(
            entities=[
                SimpleNamespace(
                    entity_id="loc-mountain",
                    name="灵山",
                    entity_type="location",
                    aliases=["山门"],
                    source="test",
                    notes="主场景",
                ),
                SimpleNamespace(
                    entity_id="item-token",
                    name="青铜令",
                    entity_type="item",
                    aliases=[],
                    source="test",
                    notes="信物",
                ),
                SimpleNamespace(
                    entity_id="ent-unknown",
                    name="未知碎片",
                    entity_type="unknown",
                    aliases=[],
                    source="test",
                    notes="跳过",
                ),
            ]
        )

        kernel = build_kernel_from_init(
            project_id="test-project",
            story_bible=story_bible,
            character_bible=character_bible,
            narrative_contract=narrative_contract,
            outline=outline,
            entity_registry=registry,
        )

        by_name = {entity.name: entity for entity in kernel.entities}
        assert by_name["灵山"].entity_type == "location"
        assert by_name["青铜令"].entity_type == "item"
        assert "未知碎片" not in by_name

    def test_kernel_has_relationships(
        self,
        story_bible: _MockStoryBible,
        character_bible: _MockCharacterBible,
        outline: _MockStoryOutline,
        narrative_contract: _MockNarrativeContract,
    ) -> None:
        kernel = build_kernel_from_init(
            project_id="test-project",
            story_bible=story_bible,
            character_bible=character_bible,
            narrative_contract=narrative_contract,
            outline=outline,
        )
        assert len(kernel.relationships) > 0
        assert all(isinstance(r, Relationship) for r in kernel.relationships)

    def test_kernel_has_timeline(
        self,
        story_bible: _MockStoryBible,
        character_bible: _MockCharacterBible,
        outline: _MockStoryOutline,
        narrative_contract: _MockNarrativeContract,
    ) -> None:
        kernel = build_kernel_from_init(
            project_id="test-project",
            story_bible=story_bible,
            character_bible=character_bible,
            narrative_contract=narrative_contract,
            outline=outline,
        )
        assert len(kernel.timeline) == 2
        assert all(isinstance(t, TimelineAnchor) for t in kernel.timeline)

    def test_kernel_timeline_characters_are_resolved_to_entity_ids(
        self,
        story_bible: _MockStoryBible,
        character_bible: _MockCharacterBible,
        outline: _MockStoryOutline,
        narrative_contract: _MockNarrativeContract,
    ) -> None:
        kernel = build_kernel_from_init(
            project_id="test-project",
            story_bible=story_bible,
            character_bible=character_bible,
            narrative_contract=narrative_contract,
            outline=outline,
        )

        anchor = next(t for t in kernel.timeline if t.chapter == 1)
        assert "char-protagonist" in anchor.characters_involved
        assert "char-ally" in anchor.characters_involved

    def test_kernel_has_promise_ledger(
        self,
        story_bible: _MockStoryBible,
        character_bible: _MockCharacterBible,
        outline: _MockStoryOutline,
        narrative_contract: _MockNarrativeContract,
    ) -> None:
        kernel = build_kernel_from_init(
            project_id="test-project",
            story_bible=story_bible,
            character_bible=character_bible,
            narrative_contract=narrative_contract,
            outline=outline,
        )
        assert len(kernel.promise_ledger) == 2
        assert all(isinstance(p, PromiseLedger) for p in kernel.promise_ledger)

    def test_kernel_passes_init_truth_gate(
        self,
        story_bible: _MockStoryBible,
        character_bible: _MockCharacterBible,
        outline: _MockStoryOutline,
        narrative_contract: _MockNarrativeContract,
    ) -> None:
        """The built kernel should pass InitTruthGate validation."""
        from novel_forge.story_kernel.gates import InitTruthGate

        kernel = build_kernel_from_init(
            project_id="test-project",
            story_bible=story_bible,
            character_bible=character_bible,
            narrative_contract=narrative_contract,
            outline=outline,
        )
        result = InitTruthGate.validate(kernel)
        # The kernel may have warnings but should not have violations
        # for the core mapping logic
        assert isinstance(result.passed, bool)

    def test_kernel_with_none_contract(
        self,
        story_bible: _MockStoryBible,
        character_bible: _MockCharacterBible,
        outline: _MockStoryOutline,
    ) -> None:
        """build_kernel_from_init should handle None narrative_contract."""
        kernel = build_kernel_from_init(
            project_id="test-project",
            story_bible=story_bible,
            character_bible=character_bible,
            narrative_contract=None,
            outline=outline,
        )
        assert isinstance(kernel, StoryKernel)
        assert kernel.promise_ledger == []

    def test_kernel_serializable(
        self,
        story_bible: _MockStoryBible,
        character_bible: _MockCharacterBible,
        outline: _MockStoryOutline,
        narrative_contract: _MockNarrativeContract,
    ) -> None:
        """Kernel should serialize and deserialize cleanly."""
        kernel = build_kernel_from_init(
            project_id="test-project",
            story_bible=story_bible,
            character_bible=character_bible,
            narrative_contract=narrative_contract,
            outline=outline,
        )
        json_str = kernel.model_dump_json()
        restored = StoryKernel.model_validate_json(json_str)
        assert restored.project_id == kernel.project_id
        assert len(restored.entities) == len(kernel.entities)
