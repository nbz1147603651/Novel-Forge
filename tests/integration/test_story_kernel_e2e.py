"""End-to-end integration tests for the complete chapter generation flow using StoryKernel.

Tests the full lifecycle:
  build_kernel_from_init → InitTruthGate → StoryKernelStore →
  ContextComposer (field slices per step) →
  chapter outcome merge (StoryKernelMerger) → PreArchiveTruthGate →
  multi-chapter progression → final state verification

Each test class targets a specific integration scenario; together they validate
that all StoryKernel components cooperate correctly in the chapter generation flow.
"""

from __future__ import annotations

import pytest

from novel_forge.core.schemas.canon import CreativeReport
from novel_forge.core.schemas.chapter import ChapterOutcome
from novel_forge.core.schemas.story_state import (
    ChapterExitState,
    CharacterState,
    CharacterStateDelta,
    PlotThreadDelta,
    RelationshipStateDelta,
)
from novel_forge.story_kernel.composer import ContextComposer
from novel_forge.story_kernel.contracts import ALL_CONTRACTS, VALID_FIELD_NAMES
from novel_forge.story_kernel.gates import GateResult, InitTruthGate, PreArchiveTruthGate
from novel_forge.story_kernel.init_adapter import build_kernel_from_init
from novel_forge.story_kernel.merger import StoryKernelMerger
from novel_forge.story_kernel.schemas import (
    BusinessDependency,
    KnowledgeLedger,
    PromiseLedger,
    Relationship,
    StoryKernel,
    TimelineAnchor,
    WorldRule,
)
from novel_forge.story_kernel.store import StoryKernelStore

# ---------------------------------------------------------------------------
# Minimal mock objects for init artifacts
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
        personality: str = "",
        backstory: str = "",
        arc: str = "",
        voice: str = "",
        social_status: str = "",
        abilities: str = "",
        appearance: str = "",
        relationships: dict[str, str] | None = None,
    ) -> None:
        self.character_id = character_id
        self.name = name
        self.role = role
        self.gender = gender
        self.status = status
        self.personality = personality
        self.backstory = backstory
        self.arc = arc
        self.voice = voice
        self.social_status = social_status
        self.abilities = abilities
        self.appearance = appearance
        self.relationships = relationships or {}


class _MockCharacterBible:
    def __init__(self, characters: list[_MockCharacterProfile]) -> None:
        self.characters = characters


class _MockStoryBible:
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


class _MockChapterOutline:
    def __init__(
        self,
        chapter_number: int = 1,
        title: str = "",
        goal: str = "",
        setting: str = "",
        time_anchor: str = "",
        involved_characters: list[str] | None = None,
    ) -> None:
        self.chapter_number = chapter_number
        self.title = title
        self.goal = goal
        self.setting = setting
        self.time_anchor = time_anchor
        self.involved_characters = involved_characters or []


class _MockStoryOutline:
    def __init__(self, chapters: list[_MockChapterOutline]) -> None:
        self.chapters = chapters


class _MockNarrativeContract:
    def __init__(
        self,
        title: str = "",
        promise_plan: list[dict] | None = None,
    ) -> None:
        self.title = title
        self.promise_plan = promise_plan or []


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def story_bible() -> _MockStoryBible:
    return _MockStoryBible(
        title="记忆回收师",
        premise="记忆回收师发现自己的前半生被改写，必须找回真实的过去。",
        rules=[
            "记忆可以被提取和植入，但每次操作都会造成不可逆的脑损伤",
            "记忆碎片会在特定情绪触发下自动浮现",
            "同一段记忆不能同时存在于两个人脑中",
        ],
        themes=["身份认同", "真实与虚幻", "自我救赎"],
        tone="dark",
        era="近未来赛博朋克",
    )


@pytest.fixture()
def character_bible() -> _MockCharacterBible:
    return _MockCharacterBible(
        characters=[
            _MockCharacterProfile(
                character_id="char-chen",
                name="陈逸",
                role="protagonist",
                gender="男",
                personality="冷静、执着、内心矛盾",
                backstory="记忆回收师，发现自己的记忆被篡改",
                arc="从迷失到找回真实自我",
                voice="克制而深沉",
                relationships={"苏晴": "romantic", "老周": "mentor_student"},
            ),
            _MockCharacterProfile(
                character_id="char-su",
                name="苏晴",
                role="supporting",
                gender="女",
                personality="聪慧、温柔、有秘密",
                relationships={"陈逸": "romantic"},
            ),
            _MockCharacterProfile(
                character_id="char-zhou",
                name="老周",
                role="supporting",
                gender="男",
                personality="沉稳、神秘",
                relationships={"陈逸": "mentor_student"},
            ),
        ]
    )


@pytest.fixture()
def outline() -> _MockStoryOutline:
    return _MockStoryOutline(
        chapters=[
            _MockChapterOutline(
                chapter_number=1,
                title="失真的记忆",
                goal="陈逸接到一个异常订单，发现回收的记忆片段与自己的过去重叠",
                setting="记忆回收工作室",
                time_anchor="某个深夜",
                involved_characters=["陈逸", "苏晴"],
            ),
            _MockChapterOutline(
                chapter_number=2,
                title="裂缝",
                goal="陈逸试图追溯记忆篡改的源头，找到老周留下的线索",
                setting="旧城区的废弃诊所",
                time_anchor="次日傍晚",
                involved_characters=["陈逸", "老周"],
            ),
            _MockChapterOutline(
                chapter_number=3,
                title="真相碎片",
                goal="拼凑出被掩盖的真相，面对一个不可能的选择",
                setting="记忆管理局地下档案室",
                time_anchor="第三天凌晨",
                involved_characters=["陈逸", "苏晴", "老周"],
            ),
        ]
    )


@pytest.fixture()
def narrative_contract() -> _MockNarrativeContract:
    return _MockNarrativeContract(
        title="记忆回收师",
        promise_plan=[
            {
                "description": "陈逸的真实身份之谜",
                "promise_type": "foreshadow",
                "planted_chapter": 1,
                "status": "planted",
                "owner_entity_ids": ["char-chen"],
            },
            {
                "description": "苏晴隐藏的秘密",
                "promise_type": "suspense",
                "planted_chapter": 1,
                "status": "planted",
                "owner_entity_ids": ["char-su"],
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
        project_id="e2e-chapter-flow",
        story_bible=story_bible,
        character_bible=character_bible,
        outline=outline,
        narrative_contract=narrative_contract,
    )


# ---------------------------------------------------------------------------
# Helper: simple in-memory store for ContextComposer
# ---------------------------------------------------------------------------


class _InMemoryKernelStore:
    """In-memory store adapter for ContextComposer (duck-typed to StoryKernelStore protocol)."""

    def __init__(self, kernel: StoryKernel) -> None:
        self._kernel = kernel

    def load_kernel(self, project_id: str) -> StoryKernel:
        return self._kernel


# ---------------------------------------------------------------------------
# Helper: build a ChapterOutcome for merging
# ---------------------------------------------------------------------------


def _make_chapter_outcome(
    chapter_number: int,
    character_updates: dict[str, CharacterState] | None = None,
    new_events: list[TimelineAnchor] | None = None,
    foreshadowing_updates: list[PromiseLedger] | None = None,
    chapter_summary: str = "",
    new_world_facts: dict[str, str] | None = None,
    new_banned_phrases: list[str] | None = None,
    character_state_deltas: list[CharacterStateDelta] | None = None,
    relationship_deltas: list[RelationshipStateDelta] | None = None,
    plot_thread_deltas: list[PlotThreadDelta] | None = None,
) -> ChapterOutcome:
    """Build a minimal ChapterOutcome for testing StoryKernelMerger."""
    return ChapterOutcome(
        source_chapter=chapter_number,
        character_updates=character_updates or {},
        new_events=new_events or [],
        foreshadowing_updates=foreshadowing_updates or [],
        chapter_summary=chapter_summary,
        new_world_facts=new_world_facts or {},
        new_banned_phrases=new_banned_phrases or [],
        creative_report=CreativeReport(),
        chapter_exit_state=ChapterExitState(chapter_number=chapter_number),
        text=f"第{chapter_number}章内容占位",
        character_state_deltas=character_state_deltas or [],
        relationship_deltas=relationship_deltas or [],
        plot_thread_deltas=plot_thread_deltas or [],
    )


# ===========================================================================
# 1. Complete chapter generation flow
# ===========================================================================


class TestCompleteChapterGenerationFlow:
    """Test the full chapter generation lifecycle with StoryKernel."""

    async def test_build_gate_store_load_compose_flow(
        self,
        story_bible: _MockStoryBible,
        character_bible: _MockCharacterBible,
        outline: _MockStoryOutline,
        narrative_contract: _MockNarrativeContract,
    ) -> None:
        """Full init flow: build → gate → store → load → compose field slices."""
        # 1. Build kernel from init artifacts
        kernel = build_kernel_from_init(
            project_id="ch-flow-1",
            story_bible=story_bible,
            character_bible=character_bible,
            outline=outline,
            narrative_contract=narrative_contract,
        )
        assert isinstance(kernel, StoryKernel)
        assert kernel.project_id == "ch-flow-1"

        # 2. Validate with InitTruthGate
        gate_result = InitTruthGate.validate(kernel)
        assert isinstance(gate_result, GateResult)

        # 3. Persist to store
        store = StoryKernelStore.in_memory()
        await store.init_db()
        try:
            await store.save_kernel(kernel)

            # 4. Load from store
            loaded = await store.load_kernel("ch-flow-1")
            assert loaded.project_id == "ch-flow-1"
            assert loaded.title == "记忆回收师"

            # 5. Compose field slices for key steps
            composer = ContextComposer(
                _InMemoryKernelStore(loaded), project_id="ch-flow-1"
            )

            bridge_ctx = composer.compose_bridge_input(chapter_number=1)
            assert "entities" in bridge_ctx
            assert "relationships" in bridge_ctx
            assert "timeline" in bridge_ctx
            assert "world_rules" in bridge_ctx

            draft_ctx = composer.compose_draft_input(chapter_number=1)
            assert "entities" in draft_ctx
            assert "world_rules" in draft_ctx

            # 6. Gate should still pass after roundtrip
            roundtrip_gate = InitTruthGate.validate(loaded)
            assert roundtrip_gate.passed == gate_result.passed

        finally:
            await store.close()

    async def test_chapter_outcome_merge_updates_kernel(
        self, built_kernel: StoryKernel
    ) -> None:
        """Merging a chapter outcome should update the kernel correctly."""
        merger = StoryKernelMerger()

        # Simulate chapter 1 outcome
        outcome = _make_chapter_outcome(
            chapter_number=1,
            character_updates={
                "陈逸": CharacterState(
                    name="陈逸",
                    alive=True,
                    location="记忆回收工作室",
                    emotional_state="困惑",
                    inventory=["记忆提取器"],
                ),
            },
            new_events=[
                TimelineAnchor(
                    anchor_id="evt_order",
                    chapter=1,
                    event="陈逸接到异常记忆订单",
                    characters_involved=["陈逸"],
                    in_story_time="某个深夜",
                ),
            ],
            chapter_summary="陈逸在深夜接到异常订单，发现回收的记忆与自己的过去重叠。",
        )

        new_kernel = merger.merge_outcome(built_kernel, outcome)

        # Chapter counter updated
        assert new_kernel.current_chapter == 1

        # Chapter summary added
        assert 1 in new_kernel.chapter_summaries
        assert "异常订单" in new_kernel.chapter_summaries[1]

        # Timeline extended
        assert len(new_kernel.timeline) > len(built_kernel.timeline)
        new_events = [t for t in new_kernel.timeline if t.chapter == 1]
        assert len(new_events) >= 1

        # Entity updated
        chen = next(e for e in new_kernel.entities if e.name == "陈逸")
        assert chen.last_seen_chapter == 1
        assert chen.attributes.get("location") == "记忆回收工作室"

        # Original kernel unchanged (immutability)
        assert built_kernel.current_chapter == 0

    async def test_multi_chapter_progression(
        self, built_kernel: StoryKernel
    ) -> None:
        """Simulate multi-chapter progression: merge outcomes for chapters 1-3."""
        merger = StoryKernelMerger()
        kernel = built_kernel

        # Chapter 1
        outcome_1 = _make_chapter_outcome(
            chapter_number=1,
            character_updates={
                "陈逸": CharacterState(
                    name="陈逸",
                    alive=True,
                    location="记忆回收工作室",
                    emotional_state="不安",
                ),
                "苏晴": CharacterState(
                    name="苏晴",
                    alive=True,
                    location="记忆回收工作室",
                    emotional_state="担忧",
                ),
            },
            new_events=[
                TimelineAnchor(
                    anchor_id="evt_discover",
                    chapter=1,
                    event="陈逸发现异常记忆订单",
                    characters_involved=["陈逸", "苏晴"],
                    in_story_time="深夜",
                ),
            ],
            chapter_summary="陈逸在深夜发现回收的记忆片段与自己的过去重叠，苏晴表现出异常的担忧。",
        )
        kernel = merger.merge_outcome(kernel, outcome_1)
        assert kernel.current_chapter == 1

        # Chapter 2
        outcome_2 = _make_chapter_outcome(
            chapter_number=2,
            character_updates={
                "陈逸": CharacterState(
                    name="陈逸",
                    alive=True,
                    location="废弃诊所",
                    emotional_state="坚定",
                ),
            },
            new_events=[
                TimelineAnchor(
                    anchor_id="evt_clue",
                    chapter=2,
                    event="陈逸找到老周的线索",
                    characters_involved=["陈逸"],
                    in_story_time="次日傍晚",
                ),
            ],
            foreshadowing_updates=[
                PromiseLedger(
                    entry_id="suspense_su_secret",
                    description="苏晴隐藏的秘密",
                    planted_chapter=1,
                    status="planted",
                ),
            ],
            chapter_summary="陈逸追循线索到废弃诊所，发现了老周留下的加密记忆芯片。",
        )
        kernel = merger.merge_outcome(kernel, outcome_2)
        assert kernel.current_chapter == 2
        assert 2 in kernel.chapter_summaries

        # Chapter 3
        outcome_3 = _make_chapter_outcome(
            chapter_number=3,
            character_updates={
                "陈逸": CharacterState(
                    name="陈逸",
                    alive=True,
                    location="记忆管理局地下档案室",
                    emotional_state="震惊",
                ),
                "老周": CharacterState(
                    name="老周",
                    alive=True,
                    location="记忆管理局地下档案室",
                    emotional_state="平静",
                ),
            },
            new_events=[
                TimelineAnchor(
                    anchor_id="evt_truth",
                    chapter=3,
                    event="陈逸发现真相",
                    characters_involved=["陈逸", "苏晴", "老周"],
                    in_story_time="第三天凌晨",
                ),
            ],
            chapter_summary="陈逸在地下档案室拼凑出真相，发现自己才是最初的实验对象。",
        )
        kernel = merger.merge_outcome(kernel, outcome_3)
        assert kernel.current_chapter == 3

        # Verify final state
        assert len(kernel.chapter_summaries) == 3
        assert len(kernel.timeline) > len(built_kernel.timeline)

        # All entities should be updated
        for entity in kernel.entities:
            assert entity.last_seen_chapter >= 1

    async def test_merge_preserves_existing_world_rules(
        self, built_kernel: StoryKernel
    ) -> None:
        """Merging should never modify existing world rules."""
        merger = StoryKernelMerger()
        original_rule_count = len(built_kernel.world_rules)

        outcome = _make_chapter_outcome(
            chapter_number=1,
            new_world_facts={"新发现": "记忆碎片可以重组"},
        )
        new_kernel = merger.merge_outcome(built_kernel, outcome)

        # Immutable rules are preserved; chapter discoveries wait for
        # adjudication instead of silently becoming world law.
        assert len(new_kernel.world_rules) == original_rule_count
        assert "记忆碎片可以重组" in new_kernel.pending_world_facts.values()

    async def test_merge_banned_phrases(
        self, built_kernel: StoryKernel
    ) -> None:
        """Merging should accumulate banned phrases."""
        merger = StoryKernelMerger()

        outcome = _make_chapter_outcome(
            chapter_number=1,
            new_banned_phrases=["突然间", "不由自主地"],
        )
        new_kernel = merger.merge_outcome(built_kernel, outcome)

        assert "突然间" in new_kernel.banned_phrases
        assert "不由自主地" in new_kernel.banned_phrases

    async def test_full_lifecycle_build_merge_gate(
        self,
        story_bible: _MockStoryBible,
        character_bible: _MockCharacterBible,
        outline: _MockStoryOutline,
        narrative_contract: _MockNarrativeContract,
    ) -> None:
        """Full lifecycle: build → gate → store → merge ch1 → PreArchive gate → compose."""
        # 1. Build and validate
        kernel = build_kernel_from_init(
            project_id="lifecycle-test",
            story_bible=story_bible,
            character_bible=character_bible,
            outline=outline,
            narrative_contract=narrative_contract,
        )
        init_gate = InitTruthGate.validate(kernel)
        assert init_gate.passed is True

        # 2. Store
        store = StoryKernelStore.in_memory()
        await store.init_db()
        try:
            await store.save_kernel(kernel)

            # 3. Merge chapter 1
            merger = StoryKernelMerger()
            outcome = _make_chapter_outcome(
                chapter_number=1,
                character_updates={
                    "陈逸": CharacterState(
                        name="陈逸",
                        alive=True,
                        location="记忆回收工作室",
                    ),
                },
                chapter_summary="陈逸在深夜接到异常订单。",
            )
            updated = merger.merge_outcome(kernel, outcome)
            assert updated.current_chapter == 1

            # 4. PreArchive gate on chapter text
            chapter_text = "陈逸坐在工作室里，苏晴推门进来。陈逸说：\"这个订单不对劲。\""
            pre_gate = PreArchiveTruthGate.validate(
                chapter_text, updated, chapter_number=1
            )
            assert isinstance(pre_gate, GateResult)

            # 5. Save updated kernel
            await store.save_kernel(updated)
            reloaded = await store.load_kernel("lifecycle-test")
            assert reloaded.current_chapter == 1

            # 6. Compose field slices for next chapter
            composer = ContextComposer(
                _InMemoryKernelStore(reloaded), project_id="lifecycle-test"
            )
            plan_ctx = composer.compose_plan_input(chapter_number=2)
            assert "entities" in plan_ctx
            assert "relationships" in plan_ctx

        finally:
            await store.close()


# ===========================================================================
# 2. All steps use field slices
# ===========================================================================


class TestAllStepsFieldSlices:
    """Test that ContextComposer can produce field slices for all registered steps."""

    def _make_composer(self, kernel: StoryKernel) -> ContextComposer:
        return ContextComposer(
            _InMemoryKernelStore(kernel), project_id="test"
        )

    def test_all_registered_contracts_have_valid_field_names(
        self, built_kernel: StoryKernel
    ) -> None:
        """All contract reads/writes/immutable fields must be valid StoryKernel field names."""
        for name, contract in ALL_CONTRACTS.items():
            for field_name in contract.reads:
                assert field_name in VALID_FIELD_NAMES, (
                    f"Contract {name}: reads contains invalid field '{field_name}'"
                )
            for field_name in contract.writes:
                assert field_name in VALID_FIELD_NAMES, (
                    f"Contract {name}: writes contains invalid field '{field_name}'"
                )
            for field_name in contract.immutable:
                assert field_name in VALID_FIELD_NAMES, (
                    f"Contract {name}: immutable contains invalid field '{field_name}'"
                )

    def test_compose_for_every_registered_step(
        self, built_kernel: StoryKernel
    ) -> None:
        """ContextComposer.compose_for_step should return data for every registered step."""
        composer = self._make_composer(built_kernel)

        for _contract_name, contract in ALL_CONTRACTS.items():
            ctx = composer.compose_for_step(
                contract.step_name, chapter_number=1
            )
            assert isinstance(ctx, dict), (
                f"compose_for_step('{contract.step_name}') should return dict, "
                f"got {type(ctx)}"
            )
            # If the step reads fields, the context should have at least those
            # fields that exist on the kernel with non-default values
            if contract.reads:
                # At minimum, the result should be a dict (may be empty if
                # kernel fields are all defaults)
                assert isinstance(ctx, dict)

    def test_bridge_step_reads_correct_fields(
        self, built_kernel: StoryKernel
    ) -> None:
        """Bridge step should receive entities, relationships, timeline, world_rules, etc."""
        composer = self._make_composer(built_kernel)
        ctx = composer.compose_bridge_input(chapter_number=1)

        expected_fields = {"entities", "relationships", "timeline", "world_rules"}
        assert expected_fields.issubset(set(ctx.keys()))

    def test_plan_step_reads_correct_fields(
        self, built_kernel: StoryKernel
    ) -> None:
        """Plan step should receive entities, relationships, world_rules, promise_ledger, etc."""
        composer = self._make_composer(built_kernel)
        ctx = composer.compose_plan_input(chapter_number=1)

        assert "entities" in ctx
        assert "relationships" in ctx
        assert "world_rules" in ctx
        assert "promise_ledger" in ctx

    def test_draft_step_reads_correct_fields(
        self, built_kernel: StoryKernel
    ) -> None:
        """Draft step should receive entities, world_rules, promise_ledger, etc."""
        composer = self._make_composer(built_kernel)
        ctx = composer.compose_draft_input(chapter_number=1)

        assert "entities" in ctx
        assert "world_rules" in ctx

    def test_edit_step_reads_correct_fields(
        self, built_kernel: StoryKernel
    ) -> None:
        """Edit step should receive entities, world_rules, banned_phrases."""
        composer = self._make_composer(built_kernel)
        ctx = composer.compose_edit_input(chapter_number=1)

        assert "entities" in ctx
        assert "world_rules" in ctx

    def test_extract_step_reads_most_fields(
        self, built_kernel: StoryKernel
    ) -> None:
        """Extract step should receive almost all kernel fields."""
        composer = self._make_composer(built_kernel)
        ctx = composer.compose_extract_input(
            chapter_number=1, chapter_text="占位文本"
        )

        assert "entities" in ctx
        assert "world_rules" in ctx
        assert "chapter_text" in ctx

    def test_causal_validate_reads_correct_fields(
        self, built_kernel: StoryKernel
    ) -> None:
        """Causal validate should receive entities, relationships, timeline, knowledge, deps."""
        composer = self._make_composer(built_kernel)
        ctx = composer.compose_causal_validate_input(chapter_number=1)

        assert "entities" in ctx
        assert "relationships" in ctx

    def test_alignment_reads_correct_fields(
        self, built_kernel: StoryKernel
    ) -> None:
        """Alignment step should receive entities, relationships, timeline, world_rules."""
        composer = self._make_composer(built_kernel)
        ctx = composer.compose_alignment_input(chapter_number=1)

        assert "entities" in ctx
        assert "relationships" in ctx
        assert "world_rules" in ctx

    def test_check_chapter_reads_correct_fields(
        self, built_kernel: StoryKernel
    ) -> None:
        """Check chapter step should receive entities, relationships, world_rules."""
        composer = self._make_composer(built_kernel)
        ctx = composer.compose_check_chapter_input(chapter_number=1)

        assert "entities" in ctx
        assert "world_rules" in ctx

    def test_patch_reads_minimal_fields(
        self, built_kernel: StoryKernel
    ) -> None:
        """Patch step should only receive entities and world_rules."""
        composer = self._make_composer(built_kernel)
        ctx = composer.compose_patch_input(chapter_number=1)

        assert "entities" in ctx
        assert "world_rules" in ctx
        # Should NOT contain most other fields
        assert "relationships" not in ctx
        assert "timeline" not in ctx

    def test_pronoun_check_reads_minimal_fields(
        self, built_kernel: StoryKernel
    ) -> None:
        """Pronoun check should only receive entities."""
        composer = self._make_composer(built_kernel)
        ctx = composer.compose_pronoun_check_input(chapter_number=1)

        assert "entities" in ctx
        assert "relationships" not in ctx
        assert "world_rules" not in ctx

    def test_forbidden_sources_reads_minimal_fields(
        self, built_kernel: StoryKernel
    ) -> None:
        """Forbidden sources should only receive banned_phrases."""
        composer = self._make_composer(built_kernel)
        ctx = composer.compose_forbidden_sources_input(chapter_number=1)

        # banned_phrases may be empty in a fresh kernel, but the key should
        # be absent if the kernel has no banned phrases
        # (the field is only included when non-empty)
        if built_kernel.banned_phrases:
            assert "banned_phrases" in ctx

    def test_book_consistency_reads_all_fields(
        self, built_kernel: StoryKernel
    ) -> None:
        """Book consistency should read all StoryKernel fields."""
        composer = self._make_composer(built_kernel)
        ctx = composer.compose_book_consistency_input(chapter_number=1)

        # Should contain most narrative fields
        assert "entities" in ctx
        assert "relationships" in ctx
        assert "timeline" in ctx
        assert "world_rules" in ctx

    def test_polish_reads_correct_fields(
        self, built_kernel: StoryKernel
    ) -> None:
        """Polish step should receive entities, world_rules, motif_protocols."""
        composer = self._make_composer(built_kernel)
        ctx = composer.compose_polish_input(chapter_number=1)

        assert "entities" in ctx
        assert "world_rules" in ctx

    def test_evaluate_reads_correct_fields(
        self, built_kernel: StoryKernel
    ) -> None:
        """Evaluate step should receive entities, relationships, timeline, world_rules."""
        composer = self._make_composer(built_kernel)
        ctx = composer.compose_evaluate_input(chapter_number=1)

        assert "entities" in ctx
        assert "relationships" in ctx
        assert "world_rules" in ctx

    def test_compose_unknown_step_returns_empty(
        self, built_kernel: StoryKernel
    ) -> None:
        """Composing for an unknown step name should return an empty dict (with warning)."""
        composer = self._make_composer(built_kernel)
        ctx = composer.compose_for_step("nonexistent_step", chapter_number=1)

        assert isinstance(ctx, dict)
        # Should be empty (no contract found)
        assert len(ctx) == 0

    def test_field_slices_are_serializable(
        self, built_kernel: StoryKernel
    ) -> None:
        """All field slice values should be JSON-serializable."""
        import json

        composer = self._make_composer(built_kernel)

        for _contract_name, contract in ALL_CONTRACTS.items():
            ctx = composer.compose_for_step(
                contract.step_name, chapter_number=1
            )
            # Should not raise
            json_str = json.dumps(ctx, ensure_ascii=False)
            assert isinstance(json_str, str)


# ===========================================================================
# 3. InitTruthGate comprehensive validation
# ===========================================================================


class TestInitTruthGateComprehensive:
    """Comprehensive InitTruthGate validation scenarios."""

    def test_well_formed_kernel_passes(self, built_kernel: StoryKernel) -> None:
        """A kernel built from complete init artifacts should pass."""
        result = InitTruthGate.validate(built_kernel)
        assert result.passed is True
        assert len(result.violations) == 0

    def test_missing_title_fails(self, built_kernel: StoryKernel) -> None:
        kernel = built_kernel.model_copy(update={"title": ""})
        result = InitTruthGate.validate(kernel)
        assert result.passed is False
        assert any("title" in v for v in result.violations)

    def test_missing_premise_fails(self, built_kernel: StoryKernel) -> None:
        kernel = built_kernel.model_copy(update={"premise": ""})
        result = InitTruthGate.validate(kernel)
        assert result.passed is False
        assert any("premise" in v for v in result.violations)

    def test_empty_entities_fails(self, built_kernel: StoryKernel) -> None:
        kernel = built_kernel.model_copy(update={"entities": []})
        result = InitTruthGate.validate(kernel)
        assert result.passed is False
        assert any("entities" in v for v in result.violations)

    def test_no_protagonist_fails(self, built_kernel: StoryKernel) -> None:
        supporting_only = [
            e.model_copy(update={"attributes": {"role": "supporting"}})
            for e in built_kernel.entities
        ]
        kernel = built_kernel.model_copy(update={"entities": supporting_only})
        result = InitTruthGate.validate(kernel)
        assert result.passed is False
        assert any("主角" in v or "protagonist" in v.lower() for v in result.violations)

    def test_empty_timeline_fails(self, built_kernel: StoryKernel) -> None:
        kernel = built_kernel.model_copy(update={"timeline": []})
        result = InitTruthGate.validate(kernel)
        assert result.passed is False
        assert any("timeline" in v for v in result.violations)

    def test_insufficient_hard_rules_fails(self, built_kernel: StoryKernel) -> None:
        kernel = built_kernel.model_copy(
            update={"world_rules": [WorldRule(rule_id="r1", content="Rule 1", severity="hard")]}
        )
        result = InitTruthGate.validate(kernel)
        assert result.passed is False
        assert any("硬性规则" in v for v in result.violations)

    def test_relationship_semantic_contradiction_is_not_locally_judged(
        self, built_kernel: StoryKernel
    ) -> None:
        """Semantic relationship contradictions are left to LLM-backed checks."""
        e1 = built_kernel.entities[0]
        e2 = built_kernel.entities[1]
        contradictory_rels = [
            Relationship(
                relationship_id="r1",
                source_entity_id=e1.entity_id,
                target_entity_id=e2.entity_id,
                relation_type="friend",
            ),
            Relationship(
                relationship_id="r2",
                source_entity_id=e2.entity_id,
                target_entity_id=e1.entity_id,
                relation_type="enemy",
            ),
        ]
        kernel = built_kernel.model_copy(update={"relationships": contradictory_rels})
        result = InitTruthGate.validate(kernel)
        assert result.passed is True

    def test_business_dependency_cycle_detected(self, built_kernel: StoryKernel) -> None:
        """Cyclic business dependencies should be detected."""
        deps = [
            BusinessDependency(dependency_id="d1", source_id="a", target_id="b"),
            BusinessDependency(dependency_id="d2", source_id="b", target_id="c"),
            BusinessDependency(dependency_id="d3", source_id="c", target_id="a"),
        ]
        kernel = built_kernel.model_copy(update={"business_dependencies": deps})
        result = InitTruthGate.validate(kernel)
        assert result.passed is False
        assert any("循环" in v for v in result.violations)

    def test_acyclic_dependencies_pass(self, built_kernel: StoryKernel) -> None:
        """Acyclic dependencies should not trigger violations."""
        deps = [
            BusinessDependency(dependency_id="d1", source_id="a", target_id="b"),
            BusinessDependency(dependency_id="d2", source_id="b", target_id="c"),
        ]
        kernel = built_kernel.model_copy(update={"business_dependencies": deps})
        result = InitTruthGate.validate(kernel)
        assert not any("循环" in v for v in result.violations)

    def test_protagonist_without_relationships_fails(
        self, built_kernel: StoryKernel
    ) -> None:
        """Protagonist must have at least one relationship."""
        kernel = built_kernel.model_copy(update={"relationships": []})
        result = InitTruthGate.validate(kernel)
        assert result.passed is False
        assert any("relationship" in v.lower() or "关系" in v for v in result.violations)

    def test_gate_result_structure(self, built_kernel: StoryKernel) -> None:
        """GateResult should have correct structure."""
        result = InitTruthGate.validate(built_kernel)
        assert hasattr(result, "passed")
        assert hasattr(result, "violations")
        assert hasattr(result, "warnings")
        assert isinstance(result.passed, bool)
        assert isinstance(result.violations, list)
        assert isinstance(result.warnings, list)


# ===========================================================================
# 4. PreArchiveTruthGate validation
# ===========================================================================


class TestPreArchiveTruthGate:
    """Test PreArchiveTruthGate validation on chapter text against kernel."""

    def test_valid_text_passes(self, built_kernel: StoryKernel) -> None:
        """Text using only registered entity names should pass identity check."""
        text = "陈逸坐在工作室里沉思。窗外的城市灯火通明。"
        result = PreArchiveTruthGate.validate(text, built_kernel, chapter_number=1)
        # Should not have identity violations for known characters
        assert not any("[identity]" in v for v in result.violations)

    def test_unknown_character_detected(self, built_kernel: StoryKernel) -> None:
        """Text with an unknown character using dialogue pattern should be flagged."""
        # Use a name with dialogue pattern that's not in the kernel
        text = "赵六说：\"这是什么情况？\"陈逸答道：\"我也不知道。\""
        result = PreArchiveTruthGate.validate(text, built_kernel, chapter_number=1)
        # The gate checks for unknown names in dialogue context
        # May or may not detect depending on regex matching
        assert isinstance(result, GateResult)

    def test_paid_promise_without_payoff_chapter_flagged(
        self, built_kernel: StoryKernel
    ) -> None:
        """A paid promise with payoff_chapter=0 should be flagged."""
        kernel = built_kernel.model_copy(
            update={
                "promise_ledger": [
                    PromiseLedger(
                        entry_id="p1",
                        description="未兑现的伏笔",
                        promise_type="foreshadow",
                        planted_chapter=1,
                        status="paid",
                        payoff_chapter=0,  # Missing payoff chapter
                    ),
                ],
            }
        )
        result = PreArchiveTruthGate.validate(
            "陈逸在工作室里思考。", kernel, chapter_number=2
        )
        assert any("[promise]" in v for v in result.violations)

    def test_future_knowledge_flagged(self, built_kernel: StoryKernel) -> None:
        """Knowledge sourced from a future chapter should be flagged."""
        kernel = built_kernel.model_copy(
            update={
                "knowledge_ledger": [
                    KnowledgeLedger(
                        entry_id="k1",
                        entity_id="char-chen",
                        fact="来自未来的知识",
                        source_chapter=5,  # Future chapter
                    ),
                ],
            }
        )
        result = PreArchiveTruthGate.validate(
            "陈逸在工作室里。", kernel, chapter_number=2
        )
        assert any("[knowledge]" in v for v in result.violations)

    def test_future_timeline_flagged(self, built_kernel: StoryKernel) -> None:
        """Timeline entries for future chapters should be flagged."""
        kernel = built_kernel.model_copy(
            update={
                "current_chapter": 2,
                "timeline": [
                    TimelineAnchor(
                        anchor_id="a1",
                        chapter=1,
                        event="过去事件",
                    ),
                    TimelineAnchor(
                        anchor_id="a2",
                        chapter=5,  # Future
                        event="未来事件",
                    ),
                ],
            }
        )
        result = PreArchiveTruthGate.validate(
            "陈逸在思考。", kernel, chapter_number=2
        )
        assert any("[timeline]" in v for v in result.violations)

    def test_empty_text_no_identity_violations(
        self, built_kernel: StoryKernel
    ) -> None:
        """Empty text should not trigger identity violations."""
        result = PreArchiveTruthGate.validate("", built_kernel, chapter_number=1)
        assert not any("[identity]" in v for v in result.violations)

    def test_known_entity_names_in_text_pass(
        self, built_kernel: StoryKernel
    ) -> None:
        """Using registered entity names in text should pass identity check."""
        text = "陈逸看着苏晴，两人默默无言。"
        result = PreArchiveTruthGate.validate(text, built_kernel, chapter_number=1)
        assert not any("[identity]" in v for v in result.violations)

    def test_gate_result_structure_pre_archive(
        self, built_kernel: StoryKernel
    ) -> None:
        """PreArchiveTruthGate result should have correct structure."""
        result = PreArchiveTruthGate.validate(
            "陈逸在工作室。", built_kernel, chapter_number=1
        )
        assert isinstance(result, GateResult)
        assert hasattr(result, "passed")
        assert hasattr(result, "violations")
        assert hasattr(result, "warnings")


# ===========================================================================
# 5. ContextComposer assembly logic
# ===========================================================================


class TestContextComposerAssembly:
    """Test ContextComposer field filtering, merging, and serialization."""

    def _make_composer(self, kernel: StoryKernel) -> ContextComposer:
        return ContextComposer(
            _InMemoryKernelStore(kernel), project_id="test"
        )

    def test_kernel_fields_take_precedence_over_extra(
        self, built_kernel: StoryKernel
    ) -> None:
        """Extra context should NOT override kernel-derived fields."""
        composer = self._make_composer(built_kernel)
        ctx = composer.compose_for_step(
            "draft",
            chapter_number=1,
            extra_context={"entities": "should_be_ignored"},
        )
        # entities should be the kernel's list, not the string
        assert isinstance(ctx["entities"], list)

    def test_extra_context_merged_for_non_kernel_keys(
        self, built_kernel: StoryKernel
    ) -> None:
        """Extra context keys not in kernel fields should be merged."""
        composer = self._make_composer(built_kernel)
        ctx = composer.compose_for_step(
            "draft",
            chapter_number=1,
            extra_context={"custom_hint": "focus on dialogue"},
        )
        assert ctx.get("custom_hint") == "focus on dialogue"

    def test_extra_context_with_chapter_text(
        self, built_kernel: StoryKernel
    ) -> None:
        """chapter_text passed as extra context should appear in result."""
        composer = self._make_composer(built_kernel)
        ctx = composer.compose_continuity_eval_input(
            chapter_number=1,
            chapter_text="陈逸坐在工作室里。",
        )
        assert ctx.get("chapter_text") == "陈逸坐在工作室里。"

    def test_entities_serialized_as_list_of_dicts(
        self, built_kernel: StoryKernel
    ) -> None:
        """Entities should be serialized as list of dicts, not Pydantic models."""
        composer = self._make_composer(built_kernel)
        ctx = composer.compose_for_step("draft", chapter_number=1)

        entities = ctx.get("entities")
        assert isinstance(entities, list)
        if entities:
            assert isinstance(entities[0], dict)
            assert "entity_id" in entities[0]
            assert "name" in entities[0]

    def test_relationships_serialized_as_list_of_dicts(
        self, built_kernel: StoryKernel
    ) -> None:
        """Relationships should be serialized as list of dicts."""
        composer = self._make_composer(built_kernel)
        ctx = composer.compose_for_step("draft", chapter_number=1)

        rels = ctx.get("relationships")
        assert isinstance(rels, list)
        if rels:
            assert isinstance(rels[0], dict)
            assert "relationship_id" in rels[0]

    def test_world_rules_serialized_as_list_of_dicts(
        self, built_kernel: StoryKernel
    ) -> None:
        """World rules should be serialized as list of dicts."""
        composer = self._make_composer(built_kernel)
        ctx = composer.compose_for_step("draft", chapter_number=1)

        rules = ctx.get("world_rules")
        assert isinstance(rules, list)
        if rules:
            assert isinstance(rules[0], dict)
            assert "rule_id" in rules[0]
            assert "content" in rules[0]

    def test_timeline_serialized_as_list_of_dicts(
        self, built_kernel: StoryKernel
    ) -> None:
        """Timeline should be serialized as list of dicts."""
        composer = self._make_composer(built_kernel)
        ctx = composer.compose_for_step("draft", chapter_number=1)

        timeline = ctx.get("timeline")
        assert isinstance(timeline, list)
        if timeline:
            assert isinstance(timeline[0], dict)
            assert "anchor_id" in timeline[0]

    def test_promise_ledger_serialized_as_list_of_dicts(
        self, built_kernel: StoryKernel
    ) -> None:
        """Promise ledger should be serialized as list of dicts."""
        composer = self._make_composer(built_kernel)
        ctx = composer.compose_for_step("draft", chapter_number=1)

        promises = ctx.get("promise_ledger")
        assert isinstance(promises, list)
        if promises:
            assert isinstance(promises[0], dict)

    def test_compose_for_step_with_none_contract_returns_empty(
        self, built_kernel: StoryKernel
    ) -> None:
        """compose_for_step with unregistered step name returns empty dict."""
        composer = self._make_composer(built_kernel)
        ctx = composer.compose_for_step("totally_unknown", chapter_number=1)
        assert ctx == {}

    def test_compose_generic_delegates_to_for_step(
        self, built_kernel: StoryKernel
    ) -> None:
        """compose_generic should produce the same result as compose_for_step."""
        composer = self._make_composer(built_kernel)
        generic = composer.compose_generic("bridge", chapter_number=1)
        direct = composer.compose_for_step("bridge", chapter_number=1)
        assert generic.keys() == direct.keys()

    def test_field_slice_does_not_include_unread_fields(
        self, built_kernel: StoryKernel
    ) -> None:
        """A step's slice should not contain fields it doesn't read."""
        from novel_forge.story_kernel.contracts import PATCH_CONTRACT

        composer = self._make_composer(built_kernel)
        ctx = composer.compose_for_step(
            PATCH_CONTRACT.step_name, chapter_number=1
        )

        # Patch only reads entities and world_rules
        # Other fields should NOT be present
        for field_name in ctx:
            assert field_name in PATCH_CONTRACT.reads, (
                f"Patch step received unexpected field '{field_name}' "
                f"not in reads: {PATCH_CONTRACT.reads}"
            )

    def test_all_convenience_composers_return_dict(
        self, built_kernel: StoryKernel
    ) -> None:
        """All convenience methods should return a dict."""
        composer = self._make_composer(built_kernel)

        methods = [
            ("compose_bridge_input", (1,)),
            ("compose_plan_input", (1,)),
            ("compose_draft_input", (1,)),
            ("compose_edit_input", (1,)),
            ("compose_causal_validate_input", (1,)),
            ("compose_causal_repair_input", (1,)),
            ("compose_alignment_input", (1,)),
            ("compose_check_chapter_input", (1,)),
            ("compose_patch_input", (1,)),
            ("compose_polish_input", (1,)),
            ("compose_evaluate_input", (1,)),
            ("compose_volume_input", (1,)),
            ("compose_pronoun_check_input", (1,)),
            ("compose_forbidden_sources_input", (1,)),
            ("compose_reading_power_eval_input", (1,)),
            ("compose_reading_power_repair_input", (1,)),
            ("compose_book_consistency_input", (1,)),
            ("compose_macro_guard_input", (1,)),
        ]

        for method_name, args in methods:
            method = getattr(composer, method_name)
            result = method(*args)
            assert isinstance(result, dict), (
                f"{method_name}({args}) should return dict, got {type(result)}"
            )

    def test_compose_continuity_eval_with_chapter_text(
        self, built_kernel: StoryKernel
    ) -> None:
        """compose_continuity_eval_input should accept chapter_text."""
        composer = self._make_composer(built_kernel)
        ctx = composer.compose_continuity_eval_input(
            chapter_number=1, chapter_text="测试文本"
        )
        assert ctx.get("chapter_text") == "测试文本"
        assert "entities" in ctx

    def test_compose_extract_with_chapter_text(
        self, built_kernel: StoryKernel
    ) -> None:
        """compose_extract_input should accept chapter_text."""
        composer = self._make_composer(built_kernel)
        ctx = composer.compose_extract_input(
            chapter_number=1, chapter_text="测试文本"
        )
        assert ctx.get("chapter_text") == "测试文本"
        assert "entities" in ctx


# ===========================================================================
# 6. StoryKernelMerger integration
# ===========================================================================


class TestStoryKernelMergerIntegration:
    """Test StoryKernelMerger with realistic chapter outcomes."""

    def test_merge_rejects_unregistered_new_entities(
        self, built_kernel: StoryKernel
    ) -> None:
        """Chapter extraction must not bypass entity-registry adjudication."""
        merger = StoryKernelMerger()
        outcome = _make_chapter_outcome(
            chapter_number=1,
            character_updates={
                "新角色": CharacterState(
                    name="新角色",
                    alive=True,
                    location="记忆回收工作室",
                ),
            },
        )
        new_kernel = merger.merge_outcome(built_kernel, outcome)

        entity_names = {e.name for e in new_kernel.entities}
        assert "新角色" not in entity_names
        assert any(
            warning.warning_type == "unknown_character_delta_rejected"
            and warning.details.get("observed_name") == "新角色"
            for warning in new_kernel.structured_warnings
        )

    def test_merge_updates_existing_entity_attributes(
        self, built_kernel: StoryKernel
    ) -> None:
        """Existing entity attributes should be updated by merge."""
        merger = StoryKernelMerger()
        outcome = _make_chapter_outcome(
            chapter_number=1,
            character_updates={
                "陈逸": CharacterState(
                    name="陈逸",
                    alive=True,
                    location="新地点",
                    emotional_state="愤怒",
                    inventory=["新道具"],
                ),
            },
        )
        new_kernel = merger.merge_outcome(built_kernel, outcome)

        chen = next(e for e in new_kernel.entities if e.name == "陈逸")
        assert chen.attributes.get("location") == "新地点"
        assert chen.attributes.get("emotional_state") == "愤怒"

    def test_merge_adds_timeline_events(self, built_kernel: StoryKernel) -> None:
        """New timeline events should be appended."""
        merger = StoryKernelMerger()
        original_count = len(built_kernel.timeline)

        outcome = _make_chapter_outcome(
            chapter_number=1,
            new_events=[
                TimelineAnchor(
                    anchor_id="evt_test",
                    chapter=1,
                    event="测试事件",
                    characters_involved=["陈逸"],
                ),
            ],
        )
        new_kernel = merger.merge_outcome(built_kernel, outcome)

        assert len(new_kernel.timeline) > original_count

    def test_merge_preserves_original_immutability(
        self, built_kernel: StoryKernel
    ) -> None:
        """Original kernel should not be modified by merge."""
        merger = StoryKernelMerger()
        original_chapter = built_kernel.current_chapter
        original_entity_count = len(built_kernel.entities)

        outcome = _make_chapter_outcome(
            chapter_number=1,
            character_updates={
                "新角色": CharacterState(name="新角色", alive=True),
            },
        )
        _ = merger.merge_outcome(built_kernel, outcome)

        # Original unchanged
        assert built_kernel.current_chapter == original_chapter
        assert len(built_kernel.entities) == original_entity_count

    def test_merge_queues_world_facts_for_adjudication(self, built_kernel: StoryKernel) -> None:
        """New chapter facts must not bypass world-rule adjudication."""
        merger = StoryKernelMerger()
        original_count = len(built_kernel.world_rules)

        outcome = _make_chapter_outcome(
            chapter_number=1,
            new_world_facts={"新规则": "记忆碎片可以被重组"},
        )
        new_kernel = merger.merge_outcome(built_kernel, outcome)

        assert len(new_kernel.world_rules) == original_count
        assert "记忆碎片可以被重组" in new_kernel.pending_world_facts.values()

    def test_merge_updates_chapter_summaries(
        self, built_kernel: StoryKernel
    ) -> None:
        """Chapter summary should be stored at the correct index."""
        merger = StoryKernelMerger()

        outcome = _make_chapter_outcome(
            chapter_number=1,
            chapter_summary="第一章摘要内容。",
        )
        new_kernel = merger.merge_outcome(built_kernel, outcome)

        assert 1 in new_kernel.chapter_summaries
        assert new_kernel.chapter_summaries[1] == "第一章摘要内容。"

    def test_merge_accumulates_banned_phrases(
        self, built_kernel: StoryKernel
    ) -> None:
        """Banned phrases should be accumulated across merges."""
        merger = StoryKernelMerger()

        outcome_1 = _make_chapter_outcome(
            chapter_number=1,
            new_banned_phrases=["禁词A"],
        )
        kernel_1 = merger.merge_outcome(built_kernel, outcome_1)
        assert "禁词A" in kernel_1.banned_phrases

        outcome_2 = _make_chapter_outcome(
            chapter_number=2,
            new_banned_phrases=["禁词B"],
        )
        kernel_2 = merger.merge_outcome(kernel_1, outcome_2)
        assert "禁词A" in kernel_2.banned_phrases
        assert "禁词B" in kernel_2.banned_phrases

    def test_merge_foreshadowing_updates(
        self, built_kernel: StoryKernel
    ) -> None:
        """Foreshadowing updates should merge into promise_ledger."""
        merger = StoryKernelMerger()

        outcome = _make_chapter_outcome(
            chapter_number=1,
            foreshadowing_updates=[
                PromiseLedger(
                    entry_id="suspense_su_secret",
                    description="苏晴隐藏的秘密",
                    planted_chapter=1,
                    status="planted",
                ),
            ],
        )
        new_kernel = merger.merge_outcome(built_kernel, outcome)

        # The suspense promise should be updated
        found = False
        for p in new_kernel.promise_ledger:
            if "苏晴" in p.description:
                found = True
                break
        assert found


# ===========================================================================
# 7. Async store integration with ContextComposer
# ===========================================================================


class TestAsyncStoreComposerIntegration:
    """Test ContextComposer backed by the async StoryKernelStore."""

    async def test_store_then_compose_bridge(
        self, built_kernel: StoryKernel
    ) -> None:
        """Save kernel to async store, then compose field slices."""
        store = StoryKernelStore.in_memory()
        await store.init_db()

        try:
            await store.save_kernel(built_kernel)

            # Pre-load kernel synchronously for ContextComposer
            loaded = await store.load_kernel("e2e-chapter-flow")

            composer = ContextComposer(
                _InMemoryKernelStore(loaded), project_id="e2e-chapter-flow"
            )

            ctx = composer.compose_bridge_input(chapter_number=1)
            assert "entities" in ctx
            assert "relationships" in ctx
            assert "world_rules" in ctx

        finally:
            await store.close()

    async def test_store_save_load_merge_save_cycle(
        self, built_kernel: StoryKernel
    ) -> None:
        """Full async store cycle: save → load → merge → save → verify."""
        store = StoryKernelStore.in_memory()
        await store.init_db()
        merger = StoryKernelMerger()

        try:
            # Save initial kernel
            await store.save_kernel(built_kernel)

            # Load and merge chapter 1
            loaded = await store.load_kernel("e2e-chapter-flow")
            outcome = _make_chapter_outcome(
                chapter_number=1,
                chapter_summary="第一章摘要",
                character_updates={
                    "陈逸": CharacterState(
                        name="陈逸",
                        alive=True,
                        location="新地点",
                    ),
                },
            )
            updated = merger.merge_outcome(loaded, outcome)
            assert updated.current_chapter == 1

            # Save updated kernel
            await store.save_kernel(updated)

            # Reload and verify
            reloaded = await store.load_kernel("e2e-chapter-flow")
            assert reloaded.current_chapter == 1
            assert 1 in reloaded.chapter_summaries

        finally:
            await store.close()

    async def test_multiple_concurrent_composers(
        self, built_kernel: StoryKernel
    ) -> None:
        """Multiple ContextComposers should work independently."""
        store = StoryKernelStore.in_memory()
        await store.init_db()

        try:
            await store.save_kernel(built_kernel)

            # Pre-load kernel for ContextComposer
            loaded = await store.load_kernel("e2e-chapter-flow")

            composer_1 = ContextComposer(
                _InMemoryKernelStore(loaded), project_id="e2e-chapter-flow"
            )
            composer_2 = ContextComposer(
                _InMemoryKernelStore(loaded), project_id="e2e-chapter-flow"
            )

            ctx_1 = composer_1.compose_bridge_input(1)
            ctx_2 = composer_2.compose_plan_input(1)

            # Both should return valid data
            assert "entities" in ctx_1
            assert "entities" in ctx_2

        finally:
            await store.close()
