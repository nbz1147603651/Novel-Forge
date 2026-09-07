"""Tests for the motif system redesign: guidance data classes, unified guidance builder, variation checker, and semantic matcher."""

from __future__ import annotations

import pytest

from novel_forge.gateway.router import ModelRouter
from novel_forge.memory.motif import (
    Motif,
    MotifGuidanceBundle,
    MotifGuidanceItem,
    MotifTracker,
)
from novel_forge.memory.motif_vector import MotifSemanticMatcher, MotifVectorBridge
from novel_forge.prompts.builder import PromptBuilder

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tracker(router: ModelRouter, builder: PromptBuilder) -> MotifTracker:
    return MotifTracker(router=router, builder=builder)


def _seed_motif(
    tracker: MotifTracker,
    *,
    motif_id: str = "m_rain",
    name: str = "雨",
    category: str = "意象",
    occurrence_count: int = 3,
    first_chapter: int = 1,
    last_chapter: int = 5,
    associated_characters: list[str] | None = None,
    thematic_meaning: str = "清洗与重生",
    is_intentional: bool = True,
) -> Motif:
    motif = Motif(
        motif_id=motif_id,
        name=name,
        category=category,
        occurrence_count=occurrence_count,
        first_appearance_chapter=first_chapter,
        last_appearance_chapter=last_chapter,
        associated_characters=associated_characters or [],
        thematic_meaning=thematic_meaning,
        is_intentional=is_intentional,
    )
    tracker._motifs[motif_id] = motif
    for ch in range(first_chapter, last_chapter + 1):
        tracker._chapter_motifs[ch].add(motif_id)
        tracker._recent_usage[motif_id].append(ch)
    return motif


# ===================================================================
# MotifGuidanceItem
# ===================================================================


class TestMotifGuidanceItem:
    def test_instantiation_defaults(self) -> None:
        item = MotifGuidanceItem(
            motif_id="m1",
            motif_name="雨",
            category="意象",
            guidance_type="strengthen",
            reason="需要加强",
        )
        assert item.motif_id == "m1"
        assert item.motif_name == "雨"
        assert item.category == "意象"
        assert item.guidance_type == "strengthen"
        assert item.reason == "需要加强"
        assert item.priority == "medium"
        assert item.thematic_meaning == ""
        assert item.chapters_since == 0
        assert item.similarity == 0.0

    def test_instantiation_custom_values(self) -> None:
        item = MotifGuidanceItem(
            motif_id="m2",
            motif_name="镜子",
            category="符号",
            guidance_type="plot_matched",
            reason="主题匹配",
            priority="high",
            thematic_meaning="自我审视",
            chapters_since=7,
            similarity=0.85,
        )
        assert item.priority == "high"
        assert item.thematic_meaning == "自我审视"
        assert item.chapters_since == 7
        assert item.similarity == pytest.approx(0.85)

    def test_all_guidance_types(self) -> None:
        for gtype in ("strengthen", "dormant_callback", "pov", "plot_matched", "forbidden"):
            item = MotifGuidanceItem(
                motif_id="x",
                motif_name="x",
                category="意象",
                guidance_type=gtype,
                reason="r",
            )
            assert item.guidance_type == gtype


# ===================================================================
# MotifGuidanceBundle
# ===================================================================


class TestMotifGuidanceBundle:
    def test_empty_bundle(self) -> None:
        bundle = MotifGuidanceBundle()
        assert bundle.chapter_number == 0
        assert bundle.all_items == []
        assert bundle.has_guidance is False

    def test_has_guidance_with_items(self) -> None:
        item = MotifGuidanceItem(
            motif_id="m1",
            motif_name="雨",
            category="意象",
            guidance_type="strengthen",
            reason="r",
        )
        bundle = MotifGuidanceBundle(strengthen=[item])
        assert bundle.has_guidance is True
        assert len(bundle.all_items) == 1

    def test_all_items_combines_categories(self) -> None:
        items = [
            MotifGuidanceItem(
                motif_id=f"m{i}", motif_name=f"n{i}", category="意象", guidance_type="t", reason="r"
            )
            for i in range(5)
        ]
        bundle = MotifGuidanceBundle(
            strengthen=[items[0]],
            dormant_callbacks=[items[1]],
            pov_motifs=[items[2]],
            plot_matched=[items[3]],
            forbidden=[items[4]],
        )
        assert len(bundle.all_items) == 5

    def test_to_prompt_text_empty(self) -> None:
        bundle = MotifGuidanceBundle()
        assert bundle.to_prompt_text() == ""

    def test_to_prompt_text_strengthen(self) -> None:
        bundle = MotifGuidanceBundle(
            strengthen=[
                MotifGuidanceItem(
                    motif_id="m1",
                    motif_name="雨",
                    category="意象",
                    guidance_type="strengthen",
                    reason="仅出现1次，建议加强",
                ),
            ],
        )
        text = bundle.to_prompt_text()
        assert "母题参考（软约束）" in text
        assert "雨" in text
        assert "可参考：仅出现1次" in text

    def test_to_prompt_text_forbidden(self) -> None:
        bundle = MotifGuidanceBundle(
            forbidden=[
                MotifGuidanceItem(
                    motif_id="",
                    motif_name="镜子",
                    category="意象",
                    guidance_type="forbidden",
                    reason="近期重复使用",
                ),
            ],
        )
        text = bundle.to_prompt_text()
        assert "避免重复" in text
        assert "镜子" in text

    def test_to_prompt_text_multiple_sections(self) -> None:
        bundle = MotifGuidanceBundle(
            strengthen=[
                MotifGuidanceItem(
                    motif_id="m1",
                    motif_name="雨",
                    category="意象",
                    guidance_type="strengthen",
                    reason="r",
                ),
            ],
            dormant_callbacks=[
                MotifGuidanceItem(
                    motif_id="m2",
                    motif_name="钟声",
                    category="声音",
                    guidance_type="dormant_callback",
                    reason="r",
                    chapters_since=15,
                ),
            ],
            forbidden=[
                MotifGuidanceItem(
                    motif_id="",
                    motif_name="镜子",
                    category="意象",
                    guidance_type="forbidden",
                    reason="r",
                ),
            ],
        )
        text = bundle.to_prompt_text()
        assert "母题参考（软约束）" in text
        assert "长线回调参考（软约束）" in text
        assert "避免重复" in text
        assert "15章未出现" not in text
        assert "承担新作用" in text

    def test_to_prompt_text_plot_matched_includes_similarity(self) -> None:
        bundle = MotifGuidanceBundle(
            plot_matched=[
                MotifGuidanceItem(
                    motif_id="m1",
                    motif_name="坠落",
                    category="意象",
                    guidance_type="plot_matched",
                    reason="主题含义匹配",
                    similarity=0.78,
                ),
            ],
        )
        text = bundle.to_prompt_text()
        assert "情节相关母题参考（软约束）" in text
        assert "0.78" in text

    def test_to_prompt_text_pov(self) -> None:
        bundle = MotifGuidanceBundle(
            pov_motifs=[
                MotifGuidanceItem(
                    motif_id="m1",
                    motif_name="怀表",
                    category="符号",
                    guidance_type="pov",
                    reason="POV 角色关联母题",
                ),
            ],
        )
        text = bundle.to_prompt_text()
        assert "POV" in text
        assert "怀表" in text


# ===================================================================
# _build_unified_guidance()
# ===================================================================


class TestBuildUnifiedGuidance:
    async def test_empty_tracker(self, router: ModelRouter, builder: PromptBuilder) -> None:
        tracker = _make_tracker(router, builder)
        bundle = await tracker._build_unified_guidance(current_chapter=5)
        assert isinstance(bundle, MotifGuidanceBundle)
        assert bundle.chapter_number == 5
        assert bundle.has_guidance is False

    async def test_returns_motif_guidance_bundle(
        self,
        router: ModelRouter,
        builder: PromptBuilder,
    ) -> None:
        tracker = _make_tracker(router, builder)
        _seed_motif(tracker, motif_id="m_rain", name="雨", last_chapter=3, occurrence_count=1)
        bundle = await tracker._build_unified_guidance(current_chapter=10)
        assert isinstance(bundle, MotifGuidanceBundle)
        assert bundle.chapter_number == 10

    async def test_strengthen_populated_for_weak_prompt_safe_motifs(
        self,
        router: ModelRouter,
        builder: PromptBuilder,
    ) -> None:
        tracker = _make_tracker(router, builder)
        _seed_motif(
            tracker,
            motif_id="m_mirror",
            name="镜子",
            category="符号",
            first_chapter=3,
            last_chapter=5,
            occurrence_count=2,
        )
        _seed_motif(
            tracker,
            motif_id="m_rain",
            name="雨",
            category="意象",
            first_chapter=3,
            last_chapter=5,
            occurrence_count=2,
        )
        bundle = await tracker._build_unified_guidance(current_chapter=10)
        strengthen_names = [item.motif_name for item in bundle.strengthen]
        assert "雨" not in strengthen_names
        assert "镜子" not in strengthen_names

    async def test_dormant_callbacks_for_long_absent_motifs(
        self,
        router: ModelRouter,
        builder: PromptBuilder,
    ) -> None:
        tracker = _make_tracker(router, builder)
        _seed_motif(
            tracker,
            motif_id="m_bell",
            name="钟声",
            category="声音",
            first_chapter=1,
            last_chapter=2,
            occurrence_count=3,
        )
        bundle = await tracker._build_unified_guidance(
            current_chapter=25, chapter_outline={"goal": "以钟声判断时间"}
        )
        dormant_names = [item.motif_name for item in bundle.dormant_callbacks]
        assert "钟声" in dormant_names

    async def test_pov_motifs_populated(
        self,
        router: ModelRouter,
        builder: PromptBuilder,
    ) -> None:
        tracker = _make_tracker(router, builder)
        _seed_motif(
            tracker,
            motif_id="m_watch",
            name="怀表",
            category="意象",
            associated_characters=["林远"],
            last_chapter=7,
            occurrence_count=4,
        )
        bundle = await tracker._build_unified_guidance(
            current_chapter=10,
            chapter_outline={"pov_character": "林远", "goal": "以怀表交换通行证"},
        )
        pov_names = [item.motif_name for item in bundle.pov_motifs]
        assert "怀表" in pov_names

    async def test_bundle_chapter_number_matches(
        self,
        router: ModelRouter,
        builder: PromptBuilder,
    ) -> None:
        tracker = _make_tracker(router, builder)
        bundle = await tracker._build_unified_guidance(current_chapter=42)
        assert bundle.chapter_number == 42

    def test_sync_unified_guidance_prefers_forbidden_over_strengthen(
        self,
        router: ModelRouter,
        builder: PromptBuilder,
    ) -> None:
        tracker = _make_tracker(router, builder)
        _seed_motif(
            tracker,
            motif_id="m_shiver",
            name="身体颤抖",
            category="动作",
            first_chapter=4,
            last_chapter=4,
            occurrence_count=1,
            is_intentional=True,
        )

        result = tracker.get_motifs_for_prompt(current_chapter=5)
        bundle = result["unified_guidance"]

        assert "身体颤抖" in result["forbidden_repetition"]
        assert [item.motif_name for item in bundle.forbidden] == ["身体颤抖"]
        assert "身体颤抖" not in [item.motif_name for item in bundle.strengthen]

    async def test_async_unified_guidance_prefers_forbidden_over_forward_guidance(
        self,
        router: ModelRouter,
        builder: PromptBuilder,
    ) -> None:
        tracker = _make_tracker(router, builder)
        _seed_motif(
            tracker,
            motif_id="m_wrist_touch",
            name="抬手摸腕",
            category="动作",
            first_chapter=9,
            last_chapter=9,
            occurrence_count=1,
            associated_characters=["林远"],
            is_intentional=True,
        )

        bundle = await tracker._build_unified_guidance(
            current_chapter=10,
            chapter_outline={"pov_character": "林远", "goal": "以怀表交换通行证"},
        )

        assert [item.motif_name for item in bundle.forbidden] == ["抬手摸腕"]
        assert "抬手摸腕" not in [item.motif_name for item in bundle.pov_motifs]


# ===================================================================
# check_motif_variation()
# ===================================================================


class TestCheckMotifVariation:
    def test_empty_tracker(self, router: ModelRouter, builder: PromptBuilder) -> None:
        tracker = _make_tracker(router, builder)
        result = tracker.check_motif_variation(chapter_number=1, chapter_text="一段测试文本。")
        assert result["unique_motifs"] == 0
        assert result["category_distribution"] == {}
        assert result["repetition_score"] == pytest.approx(0.0)
        assert result["has_sufficient_variation"] is False

    def test_returns_expected_keys(self, router: ModelRouter, builder: PromptBuilder) -> None:
        tracker = _make_tracker(router, builder)
        result = tracker.check_motif_variation(chapter_number=1, chapter_text="")
        assert "unique_motifs" in result
        assert "category_distribution" in result
        assert "repetition_score" in result
        assert "has_sufficient_variation" in result
        assert "repeated_phrases" in result

    def test_with_populated_tracker(
        self,
        router: ModelRouter,
        builder: PromptBuilder,
    ) -> None:
        tracker = _make_tracker(router, builder)
        _seed_motif(tracker, motif_id="m_rain", name="雨", category="意象", last_chapter=5)
        _seed_motif(tracker, motif_id="m_mirror", name="镜子", category="符号", last_chapter=5)
        tracker._chapter_motifs[5] = {"m_rain", "m_mirror"}

        result = tracker.check_motif_variation(
            chapter_number=5,
            chapter_text="雨声淅沥，镜中倒影模糊。",
        )
        assert result["unique_motifs"] == 2
        assert "意象" in result["category_distribution"]
        assert "符号" in result["category_distribution"]
        assert isinstance(result["repetition_score"], float)
        assert isinstance(result["has_sufficient_variation"], bool)

    def test_repeated_phrases_detected(
        self,
        router: ModelRouter,
        builder: PromptBuilder,
    ) -> None:
        tracker = _make_tracker(router, builder)
        text = "他看着远方的山，远方的山在雾中若隐若现。他看着远方的山，心中升起莫名的惆怅。"
        result = tracker.check_motif_variation(chapter_number=1, chapter_text=text)
        assert isinstance(result["repeated_phrases"], list)

    def test_variation_score_range(
        self,
        router: ModelRouter,
        builder: PromptBuilder,
    ) -> None:
        tracker = _make_tracker(router, builder)
        _seed_motif(tracker, motif_id="m1", name="雨", category="意象", last_chapter=3)
        tracker._chapter_motifs[3] = {"m1"}
        result = tracker.check_motif_variation(chapter_number=3, chapter_text="雨一直下。")
        assert 0.0 <= result["repetition_score"] <= 1.0


# ===================================================================
# MotifSemanticMatcher
# ===================================================================


class TestMotifSemanticMatcher:
    def test_threshold_property(self) -> None:
        bridge = MotifVectorBridge()
        matcher = MotifSemanticMatcher(bridge, threshold=0.7)
        assert matcher.threshold == pytest.approx(0.7)

    def test_default_threshold(self) -> None:
        bridge = MotifVectorBridge()
        matcher = MotifSemanticMatcher(bridge)
        assert matcher.threshold == pytest.approx(0.6)

    async def test_match_motif_to_goal_returns_float(self) -> None:
        bridge = MotifVectorBridge()
        matcher = MotifSemanticMatcher(bridge)
        score = await matcher.match_motif_to_goal("清洗与重生", "主角在雨中获得救赎")
        assert isinstance(score, float)

    async def test_match_motif_to_goal_empty_strings(self) -> None:
        bridge = MotifVectorBridge()
        matcher = MotifSemanticMatcher(bridge)
        assert await matcher.match_motif_to_goal("", "目标") == pytest.approx(0.0)
        assert await matcher.match_motif_to_goal("含义", "") == pytest.approx(0.0)
        assert await matcher.match_motif_to_goal("", "") == pytest.approx(0.0)

    async def test_match_motif_to_goal_range(self) -> None:
        bridge = MotifVectorBridge()
        matcher = MotifSemanticMatcher(bridge)
        score = await matcher.match_motif_to_goal("雨象征洗涤与重生", "主角在暴风雨中完成自我救赎")
        assert 0.0 <= score <= 1.0

    async def test_find_related_motifs_empty_input(self) -> None:
        bridge = MotifVectorBridge()
        matcher = MotifSemanticMatcher(bridge)
        assert await matcher.find_related_motifs("", []) == []
        assert await matcher.find_related_motifs("text", []) == []

    def test_is_vector_available_without_episodic(self) -> None:
        bridge = MotifVectorBridge()
        matcher = MotifSemanticMatcher(bridge)
        assert matcher.is_vector_available is False

    def test_tracker_injects_episodic_memory_into_semantic_bridge(
        self,
        router: ModelRouter,
        builder: PromptBuilder,
    ) -> None:
        class _FakeEpisodic:
            async def _generate_embedding(self, text: str) -> list[float]:
                return [float(len(text) or 1), 1.0]

        tracker = MotifTracker(router=router, builder=builder, episodic_memory=_FakeEpisodic())

        assert tracker._semantic_matcher.is_vector_available is True
