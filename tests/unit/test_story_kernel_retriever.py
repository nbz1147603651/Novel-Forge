"""Tests for StoryKernelRetriever and supporting types."""

from __future__ import annotations

from novel_forge.core.schemas.story_state import (
    ChapterExitState,
    PlotThreadState,
)
from novel_forge.story_kernel.retriever import (
    KernelContext,
    StoryKernelRetriever,
    _entity_to_character_dict,
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
# _entity_to_character_dict
# ---------------------------------------------------------------------------


class TestEntityToCharacterDict:
    """Unit tests for the Entity → flat dict conversion."""

    def test_basic_character_entity(self) -> None:
        entity = Entity(
            entity_id="char_001",
            name="林远",
            status="active",
            attributes={
                "location": "书房",
                "emotional_state": "焦虑",
                "gender": "男",
                "social_status": "六品翰林",
                "voice": "低沉",
            },
            last_seen_chapter=5,
            source_chapter=1,
            notes="主角",
        )
        result = _entity_to_character_dict(entity)

        assert result["name"] == "林远"
        assert result["alive"] is True
        assert result["location"] == "书房"
        assert result["emotional_state"] == "焦虑"
        assert result["gender"] == "男"
        assert result["social_status"] == "六品翰林"
        assert result["voice"] == "低沉"
        assert result["last_seen_chapter"] == 5
        assert result["source_chapter"] == 1
        assert result["notes"] == "主角"

    def test_destroyed_entity_marks_not_alive(self) -> None:
        entity = Entity(
            entity_id="char_dead",
            name="阵亡者",
            status="destroyed",
            last_seen_chapter=3,
        )
        result = _entity_to_character_dict(entity)
        assert result["alive"] is False

    def test_empty_attributes_produces_empty_strings(self) -> None:
        entity = Entity(
            entity_id="char_blank",
            name="空白角色",
            attributes={},
        )
        result = _entity_to_character_dict(entity)
        assert result["location"] == ""
        assert result["emotional_state"] == ""
        assert result["gender"] == ""
        assert result["social_status"] == ""
        assert result["voice"] == ""

    def test_returns_dict_not_pydantic_model(self) -> None:
        entity = Entity(entity_id="e1", name="X")
        result = _entity_to_character_dict(entity)
        assert isinstance(result, dict)
        # Should not be a BaseModel
        assert not hasattr(result, "model_dump")


# ---------------------------------------------------------------------------
# KernelContext
# ---------------------------------------------------------------------------


class TestKernelContext:
    """Unit tests for the KernelContext dataclass."""

    def test_defaults(self) -> None:
        ctx = KernelContext()
        assert ctx.characters == {}
        assert ctx.recent_events == []
        assert ctx.active_foreshadowing == []
        assert ctx.active_relationships == []
        assert ctx.active_plot_threads == []
        assert ctx.world_facts == {}
        assert ctx.previous_chapter_summary == ""
        assert ctx.previous_exit_state is None
        assert ctx.must_carry_forward == []
        assert ctx.immutable_facts == []

    def test_to_template_dict_returns_typed_dict(self) -> None:
        ctx = KernelContext(
            characters={"林远": {"name": "林远", "alive": True}},
            world_facts={"r1": "魔法存在"},
            previous_chapter_summary="第一章摘要",
            must_carry_forward=["线索A"],
            immutable_facts=["【世界规则】魔法存在"],
        )
        result = ctx.to_template_dict()

        assert isinstance(result, dict)
        assert result["characters"]["林远"]["name"] == "林远"
        assert result["world_facts"]["r1"] == "魔法存在"
        assert result["previous_chapter_summary"] == "第一章摘要"
        assert result["must_carry_forward"] == ["线索A"]
        assert result["immutable_facts"] == ["【世界规则】魔法存在"]

    def test_to_template_dict_serializes_pydantic_lists(self) -> None:
        event = TimelineAnchor(anchor_id="a1", chapter=1, event="事件1")
        promise = PromiseLedger(entry_id="p1", description="伏笔1", planted_chapter=1)
        ctx = KernelContext(
            recent_events=[event],
            active_foreshadowing=[promise],
        )
        result = ctx.to_template_dict()
        assert isinstance(result["recent_events"][0], dict)
        assert result["recent_events"][0]["event"] == "事件1"
        assert isinstance(result["active_foreshadowing"][0], dict)
        assert result["active_foreshadowing"][0]["description"] == "伏笔1"


# ---------------------------------------------------------------------------
# StoryKernelRetriever.get_context — basic flow
# ---------------------------------------------------------------------------


class TestGetContext:
    """Integration-level tests for StoryKernelRetriever.get_context."""

    @staticmethod
    def _make_kernel(**overrides: object) -> StoryKernel:
        """Build a minimal StoryKernel with sensible defaults."""
        defaults: dict[str, object] = {
            "project_id": "test-proj",
            "current_chapter": 10,
        }
        defaults.update(overrides)
        return StoryKernel(**defaults)  # type: ignore[arg-type]

    def test_empty_kernel_returns_empty_context(self) -> None:
        kernel = self._make_kernel()
        retriever = StoryKernelRetriever()
        ctx = retriever.get_context(kernel, for_chapter=1)

        assert ctx.characters == {}
        assert ctx.recent_events == []
        assert ctx.active_foreshadowing == []
        assert ctx.world_facts == {}

    def test_characters_filtered_by_waterline(self) -> None:
        """Entities whose last_seen_chapter > for_chapter-1 are excluded."""
        kernel = self._make_kernel(
            entities=[
                Entity(entity_id="e1", name="过去角色", last_seen_chapter=2),
                Entity(entity_id="e2", name="当前角色", last_seen_chapter=4),
                Entity(entity_id="e3", name="未来角色", last_seen_chapter=6),
            ],
        )
        retriever = StoryKernelRetriever()
        ctx = retriever.get_context(kernel, for_chapter=5)

        assert "过去角色" in ctx.characters
        assert "当前角色" in ctx.characters
        assert "未来角色" not in ctx.characters

    def test_system_artifact_entities_excluded(self) -> None:
        kernel = self._make_kernel(
            entities=[
                Entity(entity_id="e1", name="正常角色", last_seen_chapter=1),
                Entity(entity_id="e2", name="chapter_exit_state", last_seen_chapter=1),
            ],
        )
        retriever = StoryKernelRetriever()
        ctx = retriever.get_context(kernel, for_chapter=2)

        assert "正常角色" in ctx.characters
        assert "chapter_exit_state" not in ctx.characters

    def test_recent_events_within_window(self) -> None:
        kernel = self._make_kernel(
            timeline=[
                TimelineAnchor(anchor_id="a1", chapter=1, event="旧事件"),
                TimelineAnchor(anchor_id="a2", chapter=3, event="近期事件"),
                TimelineAnchor(anchor_id="a3", chapter=5, event="当前事件"),
            ],
        )
        retriever = StoryKernelRetriever(recent_chapters=3)
        ctx = retriever.get_context(kernel, for_chapter=6)

        event_texts = [e.event for e in ctx.recent_events]
        assert "旧事件" not in event_texts
        assert "近期事件" in event_texts
        assert "当前事件" in event_texts

    def test_active_foreshadowing_filters_by_status(self) -> None:
        kernel = self._make_kernel(
            promise_ledger=[
                PromiseLedger(
                    entry_id="p1", description="活跃伏笔",
                    planted_chapter=1, status="planted",
                ),
                PromiseLedger(
                    entry_id="p2", description="已揭晓伏笔",
                    planted_chapter=1, status="paid", payoff_chapter=3,
                ),
                PromiseLedger(
                    entry_id="p3", description="暗示伏笔",
                    planted_chapter=2, status="hinted",
                ),
            ],
        )
        retriever = StoryKernelRetriever()
        ctx = retriever.get_context(kernel, for_chapter=5)

        descriptions = [p.description for p in ctx.active_foreshadowing]
        assert "活跃伏笔" in descriptions
        assert "暗示伏笔" in descriptions
        assert "已揭晓伏笔" not in descriptions

    def test_world_facts_from_world_rules(self) -> None:
        kernel = self._make_kernel(
            world_rules=[
                WorldRule(rule_id="r1", content="魔法存在"),
                WorldRule(rule_id="r2", content="时间不可逆"),
            ],
        )
        retriever = StoryKernelRetriever()
        ctx = retriever.get_context(kernel, for_chapter=2)

        assert ctx.world_facts["r1"] == "魔法存在"
        assert ctx.world_facts["r2"] == "时间不可逆"

    def test_previous_chapter_summary(self) -> None:
        kernel = self._make_kernel(
            chapter_summaries={1: "第一章摘要", 2: "第二章摘要"},
        )
        retriever = StoryKernelRetriever()
        ctx = retriever.get_context(kernel, for_chapter=3)

        assert ctx.previous_chapter_summary == "第二章摘要"

    def test_previous_exit_state_and_must_carry_forward(self) -> None:
        exit_state = ChapterExitState(
            chapter_number=2,
            must_carry_forward=["线索A", "线索B"],
        )
        kernel = self._make_kernel(
            chapter_exit_states={2: exit_state},
        )
        retriever = StoryKernelRetriever()
        ctx = retriever.get_context(kernel, for_chapter=3)

        assert ctx.previous_exit_state is exit_state
        assert ctx.must_carry_forward == ["线索A", "线索B"]

    def test_involved_characters_prioritized(self) -> None:
        kernel = self._make_kernel(
            entities=[
                Entity(entity_id="e1", name="配角", last_seen_chapter=1),
                Entity(entity_id="e2", name="主角", last_seen_chapter=1),
            ],
        )
        retriever = StoryKernelRetriever(max_characters=2)
        ctx = retriever.get_context(
            kernel, for_chapter=2, involved_characters=["主角"],
        )

        names = list(ctx.characters.keys())
        assert names[0] == "主角"

    def test_destroyed_entity_not_alive_in_context(self) -> None:
        kernel = self._make_kernel(
            entities=[
                Entity(
                    entity_id="e1", name="阵亡者",
                    status="destroyed", last_seen_chapter=1,
                ),
            ],
        )
        retriever = StoryKernelRetriever()
        ctx = retriever.get_context(kernel, for_chapter=2)

        assert ctx.characters["阵亡者"]["alive"] is False


# ---------------------------------------------------------------------------
# _select_active_relationships
# ---------------------------------------------------------------------------


class TestSelectActiveRelationships:
    """Tests for relationship selection and ranking."""

    def test_relationships_involving_involved_characters_prioritized(self) -> None:
        kernel = StoryKernel(
            project_id="p",
            entities=[
                Entity(entity_id="e1", name="甲", last_seen_chapter=1),
                Entity(entity_id="e2", name="乙", last_seen_chapter=1),
                Entity(entity_id="e3", name="丙", last_seen_chapter=1),
            ],
            relationships=[
                Relationship(
                    relationship_id="r1",
                    source_entity_id="e1",
                    target_entity_id="e2",
                    label="师徒",
                    last_shift_chapter=1,
                ),
                Relationship(
                    relationship_id="r2",
                    source_entity_id="e2",
                    target_entity_id="e3",
                    label="朋友",
                    last_shift_chapter=1,
                ),
            ],
        )
        retriever = StoryKernelRetriever()
        ctx = retriever.get_context(
            kernel, for_chapter=2, involved_characters=["甲"],
        )

        pair_ids = [r.pair_id for r in ctx.active_relationships]
        assert pair_ids[0] == "r1"

    def test_future_relationships_excluded(self) -> None:
        kernel = StoryKernel(
            project_id="p",
            entities=[
                Entity(entity_id="e1", name="甲", last_seen_chapter=1),
                Entity(entity_id="e2", name="乙", last_seen_chapter=1),
            ],
            relationships=[
                Relationship(
                    relationship_id="r_past",
                    source_entity_id="e1",
                    target_entity_id="e2",
                    last_shift_chapter=1,
                ),
                Relationship(
                    relationship_id="r_future",
                    source_entity_id="e1",
                    target_entity_id="e2",
                    last_shift_chapter=5,
                ),
            ],
        )
        retriever = StoryKernelRetriever()
        ctx = retriever.get_context(kernel, for_chapter=3)

        pair_ids = [r.pair_id for r in ctx.active_relationships]
        assert "r_past" in pair_ids
        assert "r_future" not in pair_ids


# ---------------------------------------------------------------------------
# _extract_immutable_facts
# ---------------------------------------------------------------------------


class TestExtractImmutableFacts:
    """Tests for immutable fact extraction."""

    def test_dead_entity_facts(self) -> None:
        kernel = StoryKernel(
            project_id="p",
            entities=[
                Entity(
                    entity_id="e1", name="死者",
                    status="destroyed", last_seen_chapter=2,
                ),
            ],
        )
        facts = StoryKernelRetriever._extract_immutable_facts(kernel, for_chapter=3)
        assert any("死者" in f and "已死亡" in f for f in facts)

    def test_paid_foreshadowing_facts(self) -> None:
        kernel = StoryKernel(
            project_id="p",
            promise_ledger=[
                PromiseLedger(
                    entry_id="p1", description="秘密身世",
                    status="paid", planted_chapter=1, payoff_chapter=3,
                ),
            ],
        )
        facts = StoryKernelRetriever._extract_immutable_facts(kernel, for_chapter=4)
        assert any("秘密身世" in f and "已揭晓" in f for f in facts)

    def test_broken_foreshadowing_facts(self) -> None:
        kernel = StoryKernel(
            project_id="p",
            promise_ledger=[
                PromiseLedger(
                    entry_id="p1", description="废弃线索",
                    status="broken", planted_chapter=1,
                ),
            ],
        )
        facts = StoryKernelRetriever._extract_immutable_facts(kernel, for_chapter=2)
        assert any("废弃线索" in f and "已废弃" in f for f in facts)

    def test_overdue_foreshadowing_facts(self) -> None:
        kernel = StoryKernel(
            project_id="p",
            promise_ledger=[
                PromiseLedger(
                    entry_id="p1", description="悬置伏笔",
                    status="planted", planted_chapter=1,
                ),
            ],
        )
        facts = StoryKernelRetriever._extract_immutable_facts(kernel, for_chapter=7)
        assert any("悬置伏笔" in f and "需推进" in f for f in facts)

    def test_world_rules_param_facts(self) -> None:
        kernel = StoryKernel(project_id="p")
        facts = StoryKernelRetriever._extract_immutable_facts(
            kernel, for_chapter=1, world_rules=["魔法存在", "时间不可逆"],
        )
        assert any("魔法存在" in f for f in facts)
        assert any("时间不可逆" in f for f in facts)

    def test_banned_phrases_facts(self) -> None:
        kernel = StoryKernel(
            project_id="p",
            banned_phrases=["突然间", "不由自主"],
        )
        facts = StoryKernelRetriever._extract_immutable_facts(kernel, for_chapter=1)
        assert any("突然间" in f and "禁用表达" in f for f in facts)
        assert any("不由自主" in f and "禁用表达" in f for f in facts)


# ---------------------------------------------------------------------------
# Context limits
# ---------------------------------------------------------------------------


class TestContextLimits:
    """Tests that retriever respects configured limits."""

    def test_max_characters_limit(self) -> None:
        entities = [
            Entity(entity_id=f"e{i}", name=f"角色{i}", last_seen_chapter=1)
            for i in range(50)
        ]
        kernel = StoryKernel(project_id="p", entities=entities)
        retriever = StoryKernelRetriever(max_characters=10)
        ctx = retriever.get_context(kernel, for_chapter=2)

        assert len(ctx.characters) <= 10

    def test_max_recent_events_limit(self) -> None:
        timeline = [
            TimelineAnchor(anchor_id=f"a{i}", chapter=i + 1, event=f"事件{i}")
            for i in range(30)
        ]
        kernel = StoryKernel(project_id="p", timeline=timeline)
        retriever = StoryKernelRetriever(max_recent_events=5)
        ctx = retriever.get_context(kernel, for_chapter=31)

        assert len(ctx.recent_events) <= 5

    def test_max_active_foreshadowing_limit(self) -> None:
        promises = [
            PromiseLedger(
                entry_id=f"p{i}", description=f"伏笔{i}",
                planted_chapter=1, status="planted",
            )
            for i in range(40)
        ]
        kernel = StoryKernel(project_id="p", promise_ledger=promises)
        retriever = StoryKernelRetriever(max_active_foreshadowing=8)
        ctx = retriever.get_context(kernel, for_chapter=2)

        assert len(ctx.active_foreshadowing) <= 8

    def test_max_world_facts_limit(self) -> None:
        rules = [
            WorldRule(rule_id=f"r{i}", content=f"规则{i}")
            for i in range(100)
        ]
        kernel = StoryKernel(project_id="p", world_rules=rules)
        retriever = StoryKernelRetriever(max_world_facts=15)
        ctx = retriever.get_context(kernel, for_chapter=1)

        assert len(ctx.world_facts) <= 15


# ---------------------------------------------------------------------------
# Plot threads
# ---------------------------------------------------------------------------


class TestPlotThreads:
    """Tests for plot thread filtering."""

    def test_future_plot_threads_excluded(self) -> None:
        kernel = StoryKernel(
            project_id="p",
            plot_threads=[
                PlotThreadState(
                    thread_id="t1", title="旧线程",
                    last_touched_chapter=2,
                ),
                PlotThreadState(
                    thread_id="t2", title="新线程",
                    last_touched_chapter=5,
                ),
            ],
        )
        retriever = StoryKernelRetriever()
        ctx = retriever.get_context(kernel, for_chapter=4)

        thread_ids = [t.thread_id for t in ctx.active_plot_threads]
        assert "t1" in thread_ids
        assert "t2" not in thread_ids

    def test_stale_active_thread_excluded(self) -> None:
        """Active threads untouched for >8 chapters are excluded."""
        kernel = StoryKernel(
            project_id="p",
            plot_threads=[
                PlotThreadState(
                    thread_id="t1", title="遗忘线程",
                    status="active",
                    last_touched_chapter=1,
                ),
            ],
        )
        retriever = StoryKernelRetriever()
        ctx = retriever.get_context(kernel, for_chapter=10)

        assert ctx.active_plot_threads == []
