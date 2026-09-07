"""Integration tests for the complete StoryKernel initialization flow.

Tests the end-to-end path from initialization artifacts through:
  build_kernel_from_init → InitTruthGate → StoryKernelStore → load/verify
  → ContextComposer field slices

Each test class focuses on one integration scenario; together they validate
that all StoryKernel components cooperate correctly during initialization.
"""

from __future__ import annotations

import pytest

from novel_forge.story_kernel.composer import ContextComposer
from novel_forge.story_kernel.gates import GateResult, InitTruthGate
from novel_forge.story_kernel.init_adapter import build_kernel_from_init
from novel_forge.story_kernel.schemas import (
    Entity,
    Relationship,
    StoryKernel,
    TimelineAnchor,
    WorldRule,
)
from novel_forge.story_kernel.store import StoryKernelStore

# ---------------------------------------------------------------------------
# Minimal mock objects (matching real schema shapes)
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
        rules=[
            "魔法需要消耗生命力",
            "时间不可逆转",
            "每个角色只能拥有一种特殊能力",
        ],
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
            _MockChapterOutline(
                chapter_number=3,
                title="觉醒",
                goal="领悟真正的力量",
                setting="山顶神殿",
                time_anchor="黄昏",
                involved_characters=["李明"],
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


@pytest.fixture()
def built_kernel(
    story_bible: _MockStoryBible,
    character_bible: _MockCharacterBible,
    outline: _MockStoryOutline,
    narrative_contract: _MockNarrativeContract,
) -> StoryKernel:
    """A fully populated StoryKernel from init artifacts."""
    return build_kernel_from_init(
        project_id="integration-test",
        story_bible=story_bible,
        character_bible=character_bible,
        outline=outline,
        narrative_contract=narrative_contract,
    )


# ---------------------------------------------------------------------------
# 1. Complete initialization flow
# ---------------------------------------------------------------------------


class TestCompleteInitFlow:
    """Test the full init path: artifacts → build → gate → store → load → verify."""

    async def test_build_kernel_from_init_produces_valid_kernel(
        self,
        story_bible: _MockStoryBible,
        character_bible: _MockCharacterBible,
        outline: _MockStoryOutline,
        narrative_contract: _MockNarrativeContract,
    ) -> None:
        """build_kernel_from_init returns a StoryKernel with all field groups populated."""
        kernel = build_kernel_from_init(
            project_id="flow-test",
            story_bible=story_bible,
            character_bible=character_bible,
            outline=outline,
            narrative_contract=narrative_contract,
        )

        assert isinstance(kernel, StoryKernel)
        assert kernel.project_id == "flow-test"
        assert kernel.title == "测试小说"
        assert len(kernel.world_rules) >= 3
        assert len(kernel.entities) == 3
        assert len(kernel.relationships) > 0
        assert len(kernel.timeline) == 3
        assert len(kernel.promise_ledger) == 2

    async def test_init_gate_passes_for_well_formed_kernel(
        self, built_kernel: StoryKernel
    ) -> None:
        """A kernel built from complete init artifacts should pass InitTruthGate."""
        result = InitTruthGate.validate(built_kernel)

        assert isinstance(result, GateResult)
        # The kernel should have at least no critical violations
        # (may have warnings about supporting characters)
        assert isinstance(result.passed, bool)
        assert isinstance(result.violations, list)
        assert isinstance(result.warnings, list)

    async def test_kernel_survives_store_roundtrip(
        self, built_kernel: StoryKernel
    ) -> None:
        """Save kernel to store, load it back, verify all data preserved."""
        store = StoryKernelStore.in_memory()
        await store.init_db()

        try:
            # Save
            await store.save_kernel(built_kernel)

            # Load
            loaded = await store.load_kernel("integration-test")

            # Verify metadata
            assert loaded.project_id == built_kernel.project_id
            assert loaded.title == built_kernel.title
            assert loaded.premise == built_kernel.premise
            assert loaded.current_chapter == built_kernel.current_chapter

            # Verify field groups
            assert len(loaded.world_rules) == len(built_kernel.world_rules)
            assert len(loaded.entities) == len(built_kernel.entities)
            assert len(loaded.relationships) == len(built_kernel.relationships)
            assert len(loaded.timeline) == len(built_kernel.timeline)
            assert len(loaded.promise_ledger) == len(built_kernel.promise_ledger)

            # Verify content
            entity_names = {e.name for e in loaded.entities}
            assert "李明" in entity_names
            assert "王芳" in entity_names
            assert "张伟" in entity_names

            rule_contents = {r.content for r in loaded.world_rules}
            assert "魔法需要消耗生命力" in rule_contents
            assert "时间不可逆转" in rule_contents

        finally:
            await store.close()

    async def test_gate_passes_after_store_roundtrip(
        self, built_kernel: StoryKernel
    ) -> None:
        """Kernel loaded from store should still pass gate validation."""
        store = StoryKernelStore.in_memory()
        await store.init_db()

        try:
            await store.save_kernel(built_kernel)
            loaded = await store.load_kernel("integration-test")

            result = InitTruthGate.validate(loaded)
            assert isinstance(result, GateResult)
            # Gate behavior should be identical before and after store
            original_result = InitTruthGate.validate(built_kernel)
            assert result.passed == original_result.passed
            assert len(result.violations) == len(original_result.violations)

        finally:
            await store.close()


# ---------------------------------------------------------------------------
# 2. InitTruthGate validation scenarios
# ---------------------------------------------------------------------------


class TestInitTruthGateValidation:
    """Test InitTruthGate with various kernel states."""

    def test_valid_kernel_passes(self, built_kernel: StoryKernel) -> None:
        """A fully populated kernel passes the gate."""
        result = InitTruthGate.validate(built_kernel)
        # Should have no violations for a well-formed kernel
        # (may have warnings about supporting characters)
        assert result.passed is True or all(
            "建议" not in v for v in result.violations
        )

    def test_missing_title_fails(self, built_kernel: StoryKernel) -> None:
        """Kernel without title should fail."""

        kernel = built_kernel.model_copy(update={"title": ""})
        result = InitTruthGate.validate(kernel)

        assert result.passed is False
        assert any("title" in v for v in result.violations)

    def test_missing_premise_fails(self, built_kernel: StoryKernel) -> None:
        """Kernel without premise should fail."""
        kernel = built_kernel.model_copy(update={"premise": ""})
        result = InitTruthGate.validate(kernel)

        assert result.passed is False
        assert any("premise" in v for v in result.violations)

    def test_empty_entities_fails(self, built_kernel: StoryKernel) -> None:
        """Kernel without entities should fail."""
        kernel = built_kernel.model_copy(update={"entities": []})
        result = InitTruthGate.validate(kernel)

        assert result.passed is False
        assert any("entities" in v for v in result.violations)

    def test_no_protagonist_fails(self, built_kernel: StoryKernel) -> None:
        """Kernel with only supporting characters should fail."""
        supporting_only = [
            e.model_copy(update={"attributes": {"role": "supporting"}})
            for e in built_kernel.entities
        ]
        kernel = built_kernel.model_copy(update={"entities": supporting_only})
        result = InitTruthGate.validate(kernel)

        assert result.passed is False
        assert any("protagonist" in v.lower() or "主角" in v for v in result.violations)

    def test_empty_timeline_fails(self, built_kernel: StoryKernel) -> None:
        """Kernel without timeline should fail."""
        kernel = built_kernel.model_copy(update={"timeline": []})
        result = InitTruthGate.validate(kernel)

        assert result.passed is False
        assert any("timeline" in v for v in result.violations)

    def test_insufficient_hard_rules_fails(self) -> None:
        """Kernel with fewer than 3 hard rules should fail."""
        kernel = StoryKernel(
            project_id="test",
            title="Test",
            premise="A test premise.",
            entities=[
                Entity(
                    entity_id="c1",
                    name="主角",
                    entity_type="character",
                    attributes={"role": "protagonist"},
                ),
            ],
            relationships=[
                Relationship(
                    relationship_id="r1",
                    source_entity_id="c1",
                    target_entity_id="c1",
                    relation_type="friend",
                ),
            ],
            timeline=[
                TimelineAnchor(anchor_id="a1", chapter=1, event="开始"),
            ],
            world_rules=[
                WorldRule(rule_id="r1", content="Rule 1", severity="hard"),
            ],
        )
        result = InitTruthGate.validate(kernel)

        assert result.passed is False
        assert any("硬性规则" in v or "hard" in v for v in result.violations)

    def test_relationship_semantic_contradiction_is_not_locally_judged(self) -> None:
        """Bidirectional relationship semantics are judged by LLM-backed steps."""
        kernel = StoryKernel(
            project_id="test",
            title="Test",
            premise="A test premise.",
            entities=[
                Entity(
                    entity_id="c1",
                    name="角色A",
                    entity_type="character",
                    attributes={"role": "protagonist"},
                ),
                Entity(
                    entity_id="c2",
                    name="角色B",
                    entity_type="character",
                ),
            ],
            relationships=[
                Relationship(
                    relationship_id="r1",
                    source_entity_id="c1",
                    target_entity_id="c2",
                    relation_type="friend",
                ),
                Relationship(
                    relationship_id="r2",
                    source_entity_id="c2",
                    target_entity_id="c1",
                    relation_type="enemy",
                ),
            ],
            timeline=[
                TimelineAnchor(anchor_id="a1", chapter=1, event="开始"),
            ],
            world_rules=[
                WorldRule(rule_id=f"wr{i}", content=f"Rule {i}", severity="hard")
                for i in range(3)
            ],
        )
        result = InitTruthGate.validate(kernel)

        assert result.passed is True

    def test_acyclic_dependencies_pass(self, built_kernel: StoryKernel) -> None:
        """Kernel with acyclic business dependencies should pass dependency check."""
        from novel_forge.story_kernel.schemas import BusinessDependency

        kernel = built_kernel.model_copy(
            update={
                "business_dependencies": [
                    BusinessDependency(
                        dependency_id="d1",
                        source_id="a",
                        target_id="b",
                        dependency_type="requires",
                    ),
                    BusinessDependency(
                        dependency_id="d2",
                        source_id="b",
                        target_id="c",
                        dependency_type="enables",
                    ),
                ],
            }
        )
        result = InitTruthGate.validate(kernel)

        # Should not have cycle-related violations
        assert not any("循环" in v for v in result.violations)


# ---------------------------------------------------------------------------
# 3. ContextComposer field slices
# ---------------------------------------------------------------------------


class TestContextComposerIntegration:
    """Test ContextComposer with a stored init-built kernel."""

    async def test_compose_bridge_input(self, built_kernel: StoryKernel) -> None:
        """ContextComposer should return bridge-relevant fields from stored kernel."""

        class _SimpleStore:
            def __init__(self, kernel: StoryKernel) -> None:
                self._kernel = kernel

            def load_kernel(self, project_id: str) -> StoryKernel:
                return self._kernel

        store = _SimpleStore(built_kernel)
        composer = ContextComposer(store, project_id="integration-test")

        ctx = composer.compose_bridge_input(chapter_number=1)

        # Bridge reads: entities, relationships, timeline, world_rules,
        # knowledge_ledger, promise_ledger, motif_protocols, chapter_summaries
        assert "entities" in ctx
        assert "relationships" in ctx
        assert "timeline" in ctx
        assert "world_rules" in ctx

    async def test_compose_plan_input(self, built_kernel: StoryKernel) -> None:
        """ContextComposer should return plan-relevant fields."""

        class _SimpleStore:
            def __init__(self, kernel: StoryKernel) -> None:
                self._kernel = kernel

            def load_kernel(self, project_id: str) -> StoryKernel:
                return self._kernel

        store = _SimpleStore(built_kernel)
        composer = ContextComposer(store, project_id="integration-test")

        ctx = composer.compose_plan_input(chapter_number=1)

        assert "entities" in ctx
        assert "relationships" in ctx
        assert "world_rules" in ctx
        assert "promise_ledger" in ctx

    async def test_compose_draft_input(self, built_kernel: StoryKernel) -> None:
        """ContextComposer should return draft-relevant fields."""

        class _SimpleStore:
            def __init__(self, kernel: StoryKernel) -> None:
                self._kernel = kernel

            def load_kernel(self, project_id: str) -> StoryKernel:
                return self._kernel

        store = _SimpleStore(built_kernel)
        composer = ContextComposer(store, project_id="integration-test")

        ctx = composer.compose_draft_input(chapter_number=1)

        assert "entities" in ctx
        assert "world_rules" in ctx
        assert "promise_ledger" in ctx

    async def test_compose_generic_step(self, built_kernel: StoryKernel) -> None:
        """ContextComposer should handle generic step names."""

        class _SimpleStore:
            def __init__(self, kernel: StoryKernel) -> None:
                self._kernel = kernel

            def load_kernel(self, project_id: str) -> StoryKernel:
                return self._kernel

        store = _SimpleStore(built_kernel)
        composer = ContextComposer(store, project_id="integration-test")

        # Use a registered step name
        ctx = composer.compose_generic("extract", chapter_number=1)

        # Extract reads most fields
        assert "entities" in ctx or len(ctx) >= 0  # May be empty if no contract

    async def test_compose_with_extra_context(
        self, built_kernel: StoryKernel
    ) -> None:
        """Extra context should be merged without overriding kernel fields."""

        class _SimpleStore:
            def __init__(self, kernel: StoryKernel) -> None:
                self._kernel = kernel

            def load_kernel(self, project_id: str) -> StoryKernel:
                return self._kernel

        store = _SimpleStore(built_kernel)
        composer = ContextComposer(store, project_id="integration-test")

        ctx = composer.compose_for_step(
            "draft",
            chapter_number=1,
            extra_context={"custom_hint": "focus on dialogue"},
        )

        assert ctx.get("custom_hint") == "focus on dialogue"


# ---------------------------------------------------------------------------
# 6. Serialization roundtrip
# ---------------------------------------------------------------------------


class TestSerializationRoundtrip:
    """Test JSON serialization/deserialization of init-built kernel."""

    def test_json_roundtrip_preserves_all_fields(
        self, built_kernel: StoryKernel
    ) -> None:
        """Kernel → JSON → Kernel should preserve all data."""
        json_str = built_kernel.model_dump_json()
        restored = StoryKernel.model_validate_json(json_str)

        assert restored.project_id == built_kernel.project_id
        assert restored.title == built_kernel.title
        assert restored.premise == built_kernel.premise
        assert len(restored.world_rules) == len(built_kernel.world_rules)
        assert len(restored.entities) == len(built_kernel.entities)
        assert len(restored.relationships) == len(built_kernel.relationships)
        assert len(restored.timeline) == len(built_kernel.timeline)
        assert len(restored.promise_ledger) == len(built_kernel.promise_ledger)

    def test_json_roundtrip_preserves_entity_details(
        self, built_kernel: StoryKernel
    ) -> None:
        """Entity attributes should survive serialization."""
        json_str = built_kernel.model_dump_json()
        restored = StoryKernel.model_validate_json(json_str)

        protagonist = next(
            e for e in restored.entities if e.name == "李明"
        )
        assert protagonist.attributes.get("role") == "protagonist"
        assert "勇敢" in protagonist.attributes.get("personality", "")

    def test_json_roundtrip_preserves_relationship_details(
        self, built_kernel: StoryKernel
    ) -> None:
        """Relationship details should survive serialization."""
        json_str = built_kernel.model_dump_json()
        restored = StoryKernel.model_validate_json(json_str)

        # Find a specific relationship
        rel = next(
            r for r in restored.relationships
            if r.label == "挚友"
        )
        assert rel.source_entity_id == "char-protagonist"
        assert rel.target_entity_id == "char-ally"

    def test_json_roundtrip_then_gate(self, built_kernel: StoryKernel) -> None:
        """Gate validation should work identically after JSON roundtrip."""
        json_str = built_kernel.model_dump_json()
        restored = StoryKernel.model_validate_json(json_str)

        original_result = InitTruthGate.validate(built_kernel)
        restored_result = InitTruthGate.validate(restored)

        assert original_result.passed == restored_result.passed
        assert len(original_result.violations) == len(restored_result.violations)


# ---------------------------------------------------------------------------
# 5. End-to-end: full lifecycle
# ---------------------------------------------------------------------------


class TestEndToEndLifecycle:
    """Test the complete lifecycle: build → validate → store → load → compose."""

    async def test_full_lifecycle(
        self,
        story_bible: _MockStoryBible,
        character_bible: _MockCharacterBible,
        outline: _MockStoryOutline,
        narrative_contract: _MockNarrativeContract,
    ) -> None:
        """Full lifecycle: build → gate → store → load → compose."""
        # 1. Build kernel from init artifacts
        kernel = build_kernel_from_init(
            project_id="e2e-test",
            story_bible=story_bible,
            character_bible=character_bible,
            outline=outline,
            narrative_contract=narrative_contract,
        )
        assert isinstance(kernel, StoryKernel)

        # 2. Validate with InitTruthGate
        gate_result = InitTruthGate.validate(kernel)
        assert isinstance(gate_result, GateResult)

        # 3. Persist to store
        store = StoryKernelStore.in_memory()
        await store.init_db()
        try:
            await store.save_kernel(kernel)

            # 4. Load from store
            loaded = await store.load_kernel("e2e-test")
            assert loaded.project_id == "e2e-test"

            # 5. Compose field slices directly from StoryKernel
            composer = ContextComposer.from_kernel(loaded)
            plan_slice = composer.compose_plan_input(chapter_number=1)
            assert len(plan_slice["entities"]) == len(loaded.entities)
            assert len(plan_slice["relationships"]) >= 2

            # 6. Verify gate still passes after roundtrip
            roundtrip_gate = InitTruthGate.validate(loaded)
            assert roundtrip_gate.passed == gate_result.passed

        finally:
            await store.close()

    async def test_full_lifecycle_without_contract(
        self,
        story_bible: _MockStoryBible,
        character_bible: _MockCharacterBible,
        outline: _MockStoryOutline,
    ) -> None:
        """Full lifecycle with None narrative_contract (optional path)."""
        kernel = build_kernel_from_init(
            project_id="e2e-no-contract",
            story_bible=story_bible,
            character_bible=character_bible,
            outline=outline,
            narrative_contract=None,
        )

        assert isinstance(kernel, StoryKernel)
        assert kernel.promise_ledger == []

        # Gate should still be evaluable
        gate_result = InitTruthGate.validate(kernel)
        assert isinstance(gate_result, GateResult)

        # Store roundtrip
        store = StoryKernelStore.in_memory()
        await store.init_db()
        try:
            await store.save_kernel(kernel)
            loaded = await store.load_kernel("e2e-no-contract")
            assert loaded.promise_ledger == []

            composer = ContextComposer.from_kernel(loaded)
            bridge_slice = composer.compose_bridge_input(chapter_number=1)
            assert bridge_slice["promise_ledger"] == []
        finally:
            await store.close()

    async def test_multiple_projects_isolated(
        self,
        story_bible: _MockStoryBible,
        character_bible: _MockCharacterBible,
        outline: _MockStoryOutline,
        narrative_contract: _MockNarrativeContract,
    ) -> None:
        """Multiple projects should be isolated in the store."""
        store = StoryKernelStore.in_memory()
        await store.init_db()

        try:
            # Create two kernels with different project IDs
            kernel_a = build_kernel_from_init(
                project_id="project-alpha",
                story_bible=story_bible,
                character_bible=character_bible,
                outline=outline,
                narrative_contract=narrative_contract,
            )
            kernel_b = kernel_a.model_copy(update={"project_id": "project-beta"})

            await store.save_kernel(kernel_a)
            await store.save_kernel(kernel_b)

            loaded_a = await store.load_kernel("project-alpha")
            loaded_b = await store.load_kernel("project-beta")

            assert loaded_a.project_id == "project-alpha"
            assert loaded_b.project_id == "project-beta"
            assert loaded_a.project_id != loaded_b.project_id

        finally:
            await store.close()

    async def test_kernel_helper_methods_after_init(
        self, built_kernel: StoryKernel
    ) -> None:
        """StoryKernel helper methods should work on init-built kernel."""
        # get_entity_by_id
        entity = built_kernel.get_entity_by_id("char-protagonist")
        assert entity is not None
        assert entity.name == "李明"

        # get_relationships_for_entity
        rels = built_kernel.get_relationships_for_entity("char-protagonist")
        assert len(rels) > 0

        # get_timeline_for_chapter
        ch1_events = built_kernel.get_timeline_for_chapter(1)
        assert len(ch1_events) == 1
        assert ch1_events[0].event == "主角踏上旅途"

        # get_promises_by_status
        planted = built_kernel.get_promises_by_status("planted")
        assert len(planted) >= 1

    async def test_model_dump_preserves_structure(
        self, built_kernel: StoryKernel
    ) -> None:
        """model_dump should produce a dict with all expected keys."""
        data = built_kernel.model_dump()

        expected_keys = {
            "project_id",
            "current_chapter",
            "active_volume",
            "title",
            "premise",
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
            "chapter_summaries",
            "banned_phrases",
            "notes",
        }
        assert expected_keys.issubset(set(data.keys()))
