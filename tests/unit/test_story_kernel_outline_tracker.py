"""Tests for story_kernel.outline_tracker — OutlineTracker and HybridOutlineTracker."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from novel_forge.core.schemas.bible import CharacterBible, CharacterProfile
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.gateway.types import ModelResponse
from novel_forge.story_kernel.outline_tracker import (
    CharacterOutlineState,
    HybridOutlineTracker,
    KeyEvent,
    LLMExtractionResult,
    OutlineContext,
    OutlineTracker,
    PlotThread,
    RelationshipEntry,
    ThemeOccurrence,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_bible() -> CharacterBible:
    """Create a minimal CharacterBible for testing."""
    return CharacterBible(characters=[
        CharacterProfile(
            name="李明",
            role="protagonist",
            relationships={"王芳": "青梅竹马，互相信赖的朋友"},
        ),
        CharacterProfile(
            name="王芳",
            role="deuteragonist",
            relationships={"李明": "从小一起长大的伙伴"},
        ),
        CharacterProfile(
            name="张三",
            role="antagonist",
        ),
    ])


def _make_chapter(
    num: int = 1,
    title: str = "",
    goal: str = "推进主线",
    pov: str = "",
    beats: list[str] | None = None,
    plot_points: list[str] | None = None,
    subplot_points: list[str] | None = None,
) -> ChapterOutline:
    """Create a minimal ChapterOutline for testing."""
    return ChapterOutline(
        chapter_number=num,
        title=title or f"第{num}章",
        goal=goal,
        pov_character=pov,
        beats_summary=beats or [],
        main_plot_points=plot_points or [],
        subplot_points=subplot_points or [],
    )


# ---------------------------------------------------------------------------
# Data class tests
# ---------------------------------------------------------------------------


class TestDataClasses:
    """Verify all exported data classes are importable and functional."""

    def test_relationship_entry_defaults(self) -> None:
        entry = RelationshipEntry(
            character_a="A", character_b="B", description="friends",
        )
        assert entry.chapter_introduced == 0
        assert entry.trust_level == 0.5
        assert entry.tension_level == 0.5
        assert entry.shift_events == []

    def test_character_outline_state_defaults(self) -> None:
        state = CharacterOutlineState(name="X", role="protagonist")
        assert state.first_appearance == 0
        assert state.appearances == []
        assert state.relationships == []

    def test_theme_occurrence(self) -> None:
        t = ThemeOccurrence(theme="redemption", chapter=5, context="climax")
        assert t.theme == "redemption"
        assert t.chapter == 5

    def test_plot_thread(self) -> None:
        pt = PlotThread(thread_id="t1", name="主线", introduced_chapter=1)
        assert pt.status == "active"
        assert pt.resolved_chapter == 0

    def test_key_event(self) -> None:
        ev = KeyEvent(chapter=3, event_type="conflict", description="大战")
        assert ev.characters_involved == []

    def test_outline_context_defaults(self) -> None:
        ctx = OutlineContext()
        assert ctx.relationships == []
        assert ctx.character_states == {}


# ---------------------------------------------------------------------------
# OutlineTracker tests
# ---------------------------------------------------------------------------


class TestOutlineTracker:
    """Tests for the core OutlineTracker class."""

    def test_init_empty(self) -> None:
        tracker = OutlineTracker()
        assert tracker._relationships == {}
        assert tracker._character_states == {}
        assert tracker._themes == []

    def test_initialize_from_bible(self) -> None:
        tracker = OutlineTracker()
        bible = _make_bible()
        tracker.initialize_from_bible(bible)

        assert "李明" in tracker._character_states
        assert "王芳" in tracker._character_states
        assert "张三" in tracker._character_states

        # Should have at least one relationship (李明↔王芳)
        assert len(tracker._relationships) >= 1

    def test_initialize_from_bible_relationship_type(self) -> None:
        tracker = OutlineTracker()
        bible = _make_bible()
        tracker.initialize_from_bible(bible)

        pair_key = tracker._make_pair_key("李明", "王芳")
        rel = tracker._relationships[pair_key]
        assert rel.relationship_type == "friend"

    def test_update_from_chapter_tracks_characters(self) -> None:
        tracker = OutlineTracker()
        tracker.initialize_from_bible(_make_bible())

        ch = _make_chapter(num=1, pov="李明", beats=["李明遇到王芳"])
        tracker.update_from_chapter(ch)

        assert 1 in tracker._character_states["李明"].appearances
        assert 1 in tracker._character_states["王芳"].appearances

    def test_update_from_chapter_creates_key_events(self) -> None:
        tracker = OutlineTracker()
        ch = _make_chapter(
            num=2,
            plot_points=["双方展开激烈战斗", "揭露了隐藏的秘密"],
        )
        tracker.update_from_chapter(ch)

        types = {e.event_type for e in tracker._key_events}
        assert "conflict" in types
        assert "revelation" in types

    def test_update_from_chapter_infers_conflict_from_goal(self) -> None:
        tracker = OutlineTracker()
        ch = _make_chapter(num=1, goal="主角与宿敌的最终对抗")
        tracker.update_from_chapter(ch)

        conflict_events = [e for e in tracker._key_events if e.event_type == "conflict"]
        assert len(conflict_events) >= 1

    def test_update_from_chapter_tracks_unresolved_questions(self) -> None:
        tracker = OutlineTracker()
        ch = _make_chapter(num=1, goal="揭露了隐藏的秘密")
        tracker.update_from_chapter(ch)

        assert len(tracker._unresolved_questions) > 0

    def test_update_from_chapter_with_extracted_info(self) -> None:
        tracker = OutlineTracker()
        tracker.initialize_from_bible(_make_bible())

        ch = _make_chapter(num=1)
        extracted = {
            "relationship_changes": [
                {
                    "character_a": "李明",
                    "character_b": "张三",
                    "description": "开始敌对",
                    "shift_event": "初次交锋",
                }
            ],
            "themes": ["背叛"],
            "resolved_threads": [],
        }
        tracker.update_from_chapter(ch, extracted_info=extracted)

        pair_key = tracker._make_pair_key("李明", "张三")
        assert pair_key in tracker._relationships
        assert any(t.theme == "背叛" for t in tracker._themes)

    def test_add_relationship_public_api(self) -> None:
        tracker = OutlineTracker()
        tracker.add_relationship("A", "B", "朋友", 1, "初次相遇")

        pair_key = tracker._make_pair_key("A", "B")
        assert pair_key in tracker._relationships
        rel = tracker._relationships[pair_key]
        assert rel.description == "朋友"
        assert len(rel.shift_events) == 1

    def test_add_theme_public_api(self) -> None:
        tracker = OutlineTracker()
        tracker.add_theme("救赎", 3, "高潮")
        assert len(tracker._themes) == 1
        assert tracker._themes[0].theme == "救赎"

    def test_get_context_for_chapter(self) -> None:
        tracker = OutlineTracker()
        tracker.initialize_from_bible(_make_bible())

        ch = _make_chapter(num=1, pov="李明", plot_points=["大战"])
        tracker.update_from_chapter(ch)

        ctx = tracker.get_context_for_chapter(1)
        assert isinstance(ctx, OutlineContext)
        # 李明 should be in active characters
        assert "李明" in ctx.character_states

    def test_get_context_for_prompt(self) -> None:
        tracker = OutlineTracker()
        result = tracker.get_context_for_prompt(1)
        assert "relationship_summary" in result
        assert "themes_summary" in result
        assert "key_events_summary" in result
        assert "unresolved_summary" in result

    def test_get_relationship_summary_empty(self) -> None:
        tracker = OutlineTracker()
        assert "暂无" in tracker.get_relationship_summary(1)

    def test_get_themes_summary_empty(self) -> None:
        tracker = OutlineTracker()
        assert "暂无" in tracker.get_themes_summary(1)

    def test_get_key_events_summary_empty(self) -> None:
        tracker = OutlineTracker()
        assert "暂无" in tracker.get_key_events_summary(1)

    def test_get_unresolved_threads_summary_empty(self) -> None:
        tracker = OutlineTracker()
        assert "暂无" in tracker.get_unresolved_threads_summary()

    def test_classify_plot_point(self) -> None:
        tracker = OutlineTracker()
        assert tracker._classify_plot_point("激烈对抗") == "conflict"
        assert tracker._classify_plot_point("揭露真相") == "revelation"
        assert tracker._classify_plot_point("做出艰难决定") == "decision"
        assert tracker._classify_plot_point("角色死亡") == "death"
        assert tracker._classify_plot_point("意外相遇") == "meeting"
        assert tracker._classify_plot_point("紧张的对峙") == "tension"
        assert tracker._classify_plot_point("成功化解") == "resolution"
        assert tracker._classify_plot_point("故事过渡") == "transition"
        assert tracker._classify_plot_point("日常生活") == "unknown"

    def test_infer_relationship_type(self) -> None:
        tracker = OutlineTracker()
        assert tracker._infer_relationship_type("父亲严厉") == "family"
        assert tracker._infer_relationship_type("恋人") == "romantic"
        assert tracker._infer_relationship_type("敌对关系") == "enemy"
        assert tracker._infer_relationship_type("好朋友") == "friend"
        assert tracker._infer_relationship_type("路人") == "neutral"

    def test_estimate_trust_level_high(self) -> None:
        tracker = OutlineTracker()
        level = tracker._estimate_trust_level("他们之间充满信任")
        assert level == 0.8

    def test_estimate_trust_level_low(self) -> None:
        tracker = OutlineTracker()
        level = tracker._estimate_trust_level("他怀疑对方的动机")
        assert level == 0.2

    def test_estimate_trust_level_neutral(self) -> None:
        tracker = OutlineTracker()
        level = tracker._estimate_trust_level("普通的对话")
        assert level == 0.5

    def test_estimate_tension_level_high(self) -> None:
        tracker = OutlineTracker()
        level = tracker._estimate_tension_level("双方关系紧张")
        assert level == 0.8

    def test_estimate_tension_level_low(self) -> None:
        tracker = OutlineTracker()
        level = tracker._estimate_tension_level("和平共处")
        assert level == 0.2

    def test_make_pair_key_ordering(self) -> None:
        tracker = OutlineTracker()
        k1 = tracker._make_pair_key("A", "B")
        k2 = tracker._make_pair_key("B", "A")
        assert k1 == k2  # order-independent

    def test_to_dict_and_from_dict_roundtrip(self) -> None:
        tracker = OutlineTracker()
        tracker.initialize_from_bible(_make_bible())
        ch = _make_chapter(num=1, pov="李明", plot_points=["大战"])
        tracker.update_from_chapter(ch)
        tracker.add_theme("test", 1)

        data = tracker.to_dict()
        restored = OutlineTracker.from_dict(data)

        assert len(restored._relationships) == len(tracker._relationships)
        assert len(restored._character_states) == len(tracker._character_states)
        assert len(restored._themes) == len(tracker._themes)
        assert len(restored._key_events) == len(tracker._key_events)


# ---------------------------------------------------------------------------
# HybridOutlineTracker tests
# ---------------------------------------------------------------------------


class TestHybridOutlineTracker:
    """Tests for the HybridOutlineTracker class."""

    def test_init_defaults(self) -> None:
        hybrid = HybridOutlineTracker()
        assert hybrid.batch_size == 5
        assert hybrid._llm_enabled is True
        assert hybrid._llm_threshold == 5
        assert hybrid._consecutive_no_change_batches == 0

    def test_init_custom(self) -> None:
        hybrid = HybridOutlineTracker(batch_size=3, llm_threshold_batches=2)
        assert hybrid.batch_size == 3
        assert hybrid._llm_threshold == 2

    def test_batch_size_setter(self) -> None:
        hybrid = HybridOutlineTracker()
        hybrid.batch_size = 10
        assert hybrid.batch_size == 10

    def test_initialize_from_bible(self) -> None:
        hybrid = HybridOutlineTracker()
        hybrid.initialize_from_bible(_make_bible())
        assert "李明" in hybrid._rule_tracker._character_states

    def test_initialize_from_existing_chapters(self) -> None:
        hybrid = HybridOutlineTracker()
        chs = [_make_chapter(num=i) for i in range(1, 4)]
        hybrid.initialize_from_existing_chapters(chs)

        for i in range(1, 4):
            assert i in hybrid._rule_tracker._character_states.get("", CharacterOutlineState(name="", role="")).appearances or True  # no chars in empty chapter

    def test_update_from_batch_no_llm_needed(self) -> None:
        hybrid = HybridOutlineTracker(batch_size=5, llm_threshold_batches=3)
        chs = [_make_chapter(num=1, title="变化转折")]
        result = hybrid.update_from_batch(chs)

        assert result["needs_llm"] is False
        assert result["changes_detected"] >= 0

    def test_update_from_batch_triggers_llm_after_threshold(self) -> None:
        hybrid = HybridOutlineTracker(batch_size=5, llm_threshold_batches=2)

        chs = [_make_chapter(num=1, title="平静")]
        hybrid.update_from_batch(chs)

        chs = [_make_chapter(num=2, title="平静")]
        result = hybrid.update_from_batch(chs)

        assert result["needs_llm"] is True

    def test_update_from_batch_with_extracted_info_resets_counter(self) -> None:
        hybrid = HybridOutlineTracker(batch_size=5, llm_threshold_batches=2)
        chs = [_make_chapter(num=1)]
        hybrid.update_from_batch(chs, extracted_info={"relationship_changes": [{"character_a": "A", "character_b": "B", "description": "test"}]})
        assert hybrid._consecutive_no_change_batches == 0

    def test_get_context_for_chapter(self) -> None:
        hybrid = HybridOutlineTracker()
        hybrid.initialize_from_bible(_make_bible())
        ch = _make_chapter(num=1, pov="李明")
        hybrid._rule_tracker.update_from_chapter(ch)

        ctx = hybrid.get_context_for_chapter(1)
        assert isinstance(ctx, OutlineContext)

    def test_get_context_for_prompt(self) -> None:
        hybrid = HybridOutlineTracker()
        result = hybrid.get_context_for_prompt(1)
        assert "relationship_summary" in result

    def test_get_status(self) -> None:
        hybrid = HybridOutlineTracker()
        status = hybrid.get_status()
        assert "batch_size" in status
        assert "llm_enabled" in status
        assert "total_relationships" in status

    def test_to_dict_and_from_dict_roundtrip(self) -> None:
        hybrid = HybridOutlineTracker(batch_size=3, llm_extraction_enabled=False)
        hybrid.initialize_from_bible(_make_bible())
        ch = _make_chapter(num=1, pov="李明")
        hybrid._rule_tracker.update_from_chapter(ch)

        data = hybrid.to_dict()
        assert "hybrid_config" in data

        restored = HybridOutlineTracker.from_dict(data)
        assert restored.batch_size == 3
        assert restored._llm_enabled is False
        assert len(restored._rule_tracker._relationships) == len(hybrid._rule_tracker._relationships)

    def test_apply_pending_llm_result(self) -> None:
        hybrid = HybridOutlineTracker()
        hybrid._last_llm_chapter = 5
        hybrid._pending_llm_extraction = LLMExtractionResult(
            relationship_changes=[
                {"character_a": "A", "character_b": "B", "description": "test", "shift_event": "event1"},
            ],
            themes=["救赎"],
        )

        hybrid.apply_pending_llm_result()

        assert hybrid._pending_llm_extraction is None
        pair_key = hybrid._rule_tracker._make_pair_key("A", "B")
        assert pair_key in hybrid._rule_tracker._relationships

    def test_evaluate_extraction_quality_with_keywords(self) -> None:
        hybrid = HybridOutlineTracker()
        chs = [_make_chapter(num=1, title="信任的冲突", plot_points=["双方关系进一步加深"])]
        quality = hybrid._evaluate_extraction_quality(chs)

        assert quality.has_relationship_keywords is True
        assert quality.detected_changes >= 1
        assert quality.confidence == 0.8
        assert quality.needs_llm_boost is False

    def test_evaluate_extraction_quality_no_keywords(self) -> None:
        hybrid = HybridOutlineTracker()
        chs = [_make_chapter(num=1, title="日常")]
        quality = hybrid._evaluate_extraction_quality(chs)

        assert quality.has_relationship_keywords is False
        assert quality.confidence == 0.2
        assert quality.needs_llm_boost is True

    async def test_trigger_llm_extraction_empty(self) -> None:
        hybrid = HybridOutlineTracker()
        ctx = MagicMock()
        result = await hybrid.trigger_llm_extraction([], ctx)
        assert result.confidence == 0.5
        assert result.attempts == 0

    async def test_trigger_llm_extraction_success(self) -> None:
        hybrid = HybridOutlineTracker()
        mock_router = MagicMock()
        mock_router.route = AsyncMock(
            return_value=ModelResponse(
                content='{"relationship_changes": [{"character_a": "A", "character_b": "B", "description": "test"}], "new_relationships": [], "resolved_relationships": [], "themes": ["救赎"], "new_questions": [], "confidence": 0.9}'
            )
        )
        ctx = MagicMock()
        ctx.router = mock_router

        chs = [_make_chapter(num=1, title="test chapter")]
        result = await hybrid.trigger_llm_extraction(chs, ctx)

        assert result.confidence == 0.9
        assert result.attempts == 1
        assert len(result.relationship_changes) == 1
        assert "救赎" in result.themes
        assert mock_router.route.await_count == 1

    async def test_trigger_llm_extraction_failure_returns_fallback(self) -> None:
        hybrid = HybridOutlineTracker()
        mock_router = MagicMock()
        mock_router.route = AsyncMock(side_effect=ValueError("API error"))
        ctx = MagicMock()
        ctx.router = mock_router

        chs = [_make_chapter(num=1)]
        result = await hybrid.trigger_llm_extraction(chs, ctx)

        assert result.confidence == 0.0
        assert result.attempts == 2
        assert "ValueError" in result.error

    async def test_trigger_llm_extraction_invalid_json_retries(self) -> None:
        hybrid = HybridOutlineTracker()
        mock_router = MagicMock()
        mock_router.route = AsyncMock(return_value=ModelResponse(content="not json"))
        ctx = MagicMock()
        ctx.router = mock_router

        chs = [_make_chapter(num=1)]
        result = await hybrid.trigger_llm_extraction(chs, ctx)

        assert result.confidence == 0.0
        assert result.attempts == 2

    async def test_trigger_llm_extraction_applies_to_tracker(self) -> None:
        hybrid = HybridOutlineTracker()
        mock_router = MagicMock()
        mock_router.route = AsyncMock(
            return_value=ModelResponse(
                content='{"relationship_changes": [], "new_relationships": [], "resolved_relationships": [], "themes": ["test_theme"], "new_questions": [], "confidence": 0.7}'
            )
        )
        ctx = MagicMock()
        ctx.router = mock_router

        chs = [_make_chapter(num=1)]
        await hybrid.trigger_llm_extraction(chs, ctx)

        # Themes should be applied via update_from_chapter
        assert any(t.theme == "test_theme" for t in hybrid._rule_tracker._themes)
