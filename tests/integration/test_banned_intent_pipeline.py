"""Integration tests: Banned intent pipeline — propagation from StoryBible/StoryKernel/MotifTracker/Issue Ledger through StoryKernelRetriever, StoryKernelMerger, and template rendering."""

from __future__ import annotations

from types import SimpleNamespace

from novel_forge.common.constants import TaskType
from novel_forge.core.schemas.bible import StoryBible
from novel_forge.core.schemas.chapter import ChapterOutcome
from novel_forge.core.schemas.continuity import ChapterStatePacket
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.memory.motif import MotifTracker
from novel_forge.pipeline.long.services.constraints.constraint_router import build_stage_cards
from novel_forge.pipeline.long.stages.continuity_repair import _extract_pattern_from_issue
from novel_forge.story_kernel.merger import StoryKernelMerger
from novel_forge.story_kernel.retriever import StoryKernelRetriever
from novel_forge.story_kernel.schemas import StoryKernel

# ──────────────────────────────────────────────────────────────
# Test 1: StoryBible banned_intent_rules → StoryKernelRetriever immutable_facts
# ──────────────────────────────────────────────────────────────

class TestBannedIntentRulesPropagation:
    """StoryBible.banned_intent_rules must appear in StoryKernelRetriever immutable_facts as 【禁用意向】."""

    def test_banned_intent_rules_in_immutable_facts(self) -> None:
        """StoryKernelRetriever._extract_immutable_facts includes banned_intent_rules from StoryBible."""
        state = StoryKernel(project_id="test_banned_intent")
        story_bible = StoryBible(
            premise="一个关于记忆的故事",
            banned_intent_rules=[
                "禁止使用瞳孔微缩等眼部反应模板",
                "禁止用'倒吸一口凉气'表达惊讶",
            ],
        )

        facts = StoryKernelRetriever._extract_immutable_facts(state, for_chapter=1, story_bible=story_bible)

        banned_intent_facts = [f for f in facts if f.startswith("【禁用意向】")]
        assert len(banned_intent_facts) == 2
        assert "【禁用意向】禁止使用瞳孔微缩等眼部反应模板" in banned_intent_facts
        assert "【禁用意向】禁止用'倒吸一口凉气'表达惊讶" in banned_intent_facts

    def test_banned_intent_rules_empty_when_not_set(self) -> None:
        """No 【禁用意向】 facts when StoryBible has no banned_intent_rules."""
        state = StoryKernel(project_id="test_no_banned")
        story_bible = StoryBible(premise="一个普通的故事")

        facts = StoryKernelRetriever._extract_immutable_facts(state, for_chapter=1, story_bible=story_bible)

        banned_intent_facts = [f for f in facts if f.startswith("【禁用意向】")]
        assert len(banned_intent_facts) == 0

    def test_banned_intent_rules_capped_at_15(self) -> None:
        """Banned intent rules are capped at 15 items in immutable_facts."""
        state = StoryKernel(project_id="test_cap")
        many_rules = [f"规则{i}" for i in range(25)]
        story_bible = StoryBible(premise="测试", banned_intent_rules=many_rules)

        facts = StoryKernelRetriever._extract_immutable_facts(state, for_chapter=1, story_bible=story_bible)

        banned_intent_facts = [f for f in facts if f.startswith("【禁用意向】")]
        assert len(banned_intent_facts) == 15

    def test_banned_intent_rules_with_story_bible_none(self) -> None:
        """No 【禁用意向】 facts when story_bible is None."""
        state = StoryKernel(project_id="test_none")

        facts = StoryKernelRetriever._extract_immutable_facts(state, for_chapter=1, story_bible=None)

        banned_intent_facts = [f for f in facts if f.startswith("【禁用意向】")]
        assert len(banned_intent_facts) == 0


# ──────────────────────────────────────────────────────────────
# Test 2: StoryKernel banned_phrases → StoryKernelRetriever immutable_facts
# ──────────────────────────────────────────────────────────────

class TestBannedPhrasesPropagation:
    """StoryKernel.banned_phrases must appear in StoryKernelRetriever immutable_facts as 【禁用表达】."""

    def test_banned_phrases_in_immutable_facts(self) -> None:
        """StoryKernelRetriever._extract_immutable_facts includes banned_phrases from StoryKernel."""
        state = StoryKernel(
            project_id="test_banned_phrases",
            banned_phrases=[
                "铜质怀表发出微弱的光芒",
                "寒意刺骨",
            ],
        )

        facts = StoryKernelRetriever._extract_immutable_facts(state, for_chapter=1)

        banned_phrase_facts = [f for f in facts if f.startswith("【禁用表达】")]
        assert len(banned_phrase_facts) == 2
        assert "【禁用表达】铜质怀表发出微弱的光芒" in banned_phrase_facts
        assert "【禁用表达】寒意刺骨" in banned_phrase_facts

    def test_banned_phrases_empty_when_not_set(self) -> None:
        """No 【禁用表达】 facts when StoryKernel has no banned_phrases."""
        state = StoryKernel(project_id="test_no_phrases")

        facts = StoryKernelRetriever._extract_immutable_facts(state, for_chapter=1)

        banned_phrase_facts = [f for f in facts if f.startswith("【禁用表达】")]
        assert len(banned_phrase_facts) == 0

    def test_banned_phrases_capped_at_20(self) -> None:
        """Banned phrases are capped at 20 items in immutable_facts."""
        many_phrases = [f"短语{i}" for i in range(30)]
        state = StoryKernel(project_id="test_phrase_cap", banned_phrases=many_phrases)

        facts = StoryKernelRetriever._extract_immutable_facts(state, for_chapter=1)

        banned_phrase_facts = [f for f in facts if f.startswith("【禁用表达】")]
        assert len(banned_phrase_facts) == 20


# ──────────────────────────────────────────────────────────────
# Test 3: Combined banned_intent + banned_phrases in immutable_facts
# ──────────────────────────────────────────────────────────────

class TestCombinedBannedFacts:
    """Both 【禁用意向】 and 【禁用表达】 appear together in immutable_facts."""

    def test_combined_banned_facts_order(self) -> None:
        """Banned intent rules appear before banned phrases in immutable_facts."""
        state = StoryKernel(
            project_id="test_combined",
            banned_phrases=["短语A", "短语B"],
        )
        story_bible = StoryBible(
            premise="测试",
            banned_intent_rules=["规则A"],
        )

        facts = StoryKernelRetriever._extract_immutable_facts(
            state, for_chapter=1, story_bible=story_bible,
        )

        banned_intent_idx = next(
            i for i, f in enumerate(facts) if f.startswith("【禁用意向】")
        )
        banned_phrase_idx = next(
            i for i, f in enumerate(facts) if f.startswith("【禁用表达】")
        )
        assert banned_intent_idx < banned_phrase_idx

    def test_full_immutable_facts_pipeline(self) -> None:
        """Full get_context pipeline includes both banned intent and phrases."""
        state = StoryKernel(
            project_id="test_full",
            banned_phrases=["重复的短语"],
        )
        story_bible = StoryBible(
            premise="测试",
            banned_intent_rules=["禁止模板化描写"],
        )

        retriever = StoryKernelRetriever()
        ctx = retriever.get_context(state, for_chapter=1, story_bible=story_bible)

        assert any(f.startswith("【禁用意向】") for f in ctx.immutable_facts)
        assert any(f.startswith("【禁用表达】") for f in ctx.immutable_facts)


# ──────────────────────────────────────────────────────────────
# Test 4: MotifTracker.extract_repeated_phrases → banned_phrases
# ──────────────────────────────────────────────────────────────

class TestMotifTrackerRepeatedPhrases:
    """MotifTracker.extract_repeated_phrases detects repeated phrases that can become banned_phrases."""

    def test_detects_repeated_phrases(self) -> None:
        """extract_repeated_phrases finds phrases repeated 2+ times."""
        tracker = MotifTracker.__new__(MotifTracker)

        text = "他推开门。他推开门。房间里很安静。"
        result = tracker.extract_repeated_phrases(text, min_length=4, min_occurrences=2)

        assert len(result) > 0

    def test_no_repeated_phrases_in_unique_text(self) -> None:
        """extract_repeated_phrases returns empty list for text with no repetitions."""
        tracker = MotifTracker.__new__(MotifTracker)

        text = "今天天气很好，适合出门散步。"
        result = tracker.extract_repeated_phrases(text, min_length=4, min_occurrences=2)

        assert result == []

    def test_repeated_phrases_can_feed_banned_phrases(self) -> None:
        """Repeated phrases from MotifTracker can be added to StoryKernel.banned_phrases."""
        tracker = MotifTracker.__new__(MotifTracker)

        text = (
            "铜质怀表发出微弱的光芒。他看着铜质怀表发出微弱的光芒，"
            "心中不安。铜质怀表发出微弱的光芒再次出现。"
        )
        repeated = tracker.extract_repeated_phrases(text, min_length=4, min_occurrences=2)

        assert len(repeated) > 0

        state = StoryKernel(project_id="test_motif_to_banned")
        state.banned_phrases.extend(repeated[:5])

        assert len(state.banned_phrases) > 0
        assert "铜质怀表发出微弱的光芒" in state.banned_phrases

    def test_extract_repeated_phrases_respects_min_length(self) -> None:
        """Phrases shorter than min_length are not detected."""
        tracker = MotifTracker.__new__(MotifTracker)

        text = "他来了。他来了。"
        result = tracker.extract_repeated_phrases(text, min_length=5, min_occurrences=2)

        assert result == []

    def test_extract_repeated_phrases_empty_text(self) -> None:
        """extract_repeated_phrases returns empty for empty text."""
        tracker = MotifTracker.__new__(MotifTracker)

        assert tracker.extract_repeated_phrases("", min_length=4, min_occurrences=2) == []
        assert tracker.extract_repeated_phrases(None, min_length=4, min_occurrences=2) == []  # type: ignore


# ──────────────────────────────────────────────────────────────
# Test 5: Issue Ledger → banned_phrases extraction
# ──────────────────────────────────────────────────────────────

class TestIssueLedgerToBannedPhrases:
    """Unresolved issues from Issue Ledger can extract phrases to packet.banned_phrases."""

    def test_extract_pattern_from_quoted_issue(self) -> None:
        """_extract_pattern_from_issue pulls quoted text from issue summary."""
        issue = SimpleNamespace(summary='重复表达："铜质怀表发出微弱的光芒"出现多次')

        phrase = _extract_pattern_from_issue(issue)
        assert phrase == "铜质怀表发出微弱的光芒"

    def test_extract_pattern_from_single_quoted_issue(self) -> None:
        """_extract_pattern_from_issue handles curly single quotes."""
        issue = SimpleNamespace(summary="重复表达：\u2018寒意刺骨\u2019不应反复使用")

        phrase = _extract_pattern_from_issue(issue)
        assert phrase == "寒意刺骨"

    def test_extract_pattern_fallback_for_repetition_issue(self) -> None:
        """_extract_pattern_from_issue falls back to first 20 chars for repetition issues."""
        issue = SimpleNamespace(summary="重复句式模板：角色总是先叹气再说话")

        phrase = _extract_pattern_from_issue(issue)
        assert phrase is not None
        assert len(phrase) >= 2

    def test_extract_pattern_returns_none_for_non_repetition(self) -> None:
        """_extract_pattern_from_issue returns None for non-repetition issues."""
        issue = SimpleNamespace(summary="角色位置不一致：林远在A地却出现在B地")

        phrase = _extract_pattern_from_issue(issue)
        assert phrase is None

    def test_extract_pattern_returns_none_for_empty_summary(self) -> None:
        """_extract_pattern_from_issue returns None for empty summary."""
        issue = SimpleNamespace(summary="")

        assert _extract_pattern_from_issue(issue) is None

    def test_issue_ledger_unresolved_feeds_packet_banned_phrases(self) -> None:
        """Simulated Issue Ledger flow: unresolved repetition issues add to packet.banned_phrases."""
        packet = ChapterStatePacket(
            chapter_number=3,
            chapter_outline=ChapterOutline(chapter_number=3, title="测试章", goal="推进冲突"),
            canon_context={},
        )

        unresolved_issues = [
            SimpleNamespace(summary='重复表达："铜质怀表"出现3次'),
            SimpleNamespace(summary="角色位置不一致"),
            SimpleNamespace(summary="重复句式：\u2018倒吸一口凉气\u2019模板化"),
        ]

        for iss in unresolved_issues:
            if "重复" in getattr(iss, "summary", "") or "模板" in getattr(iss, "summary", "") or "雷同" in getattr(iss, "summary", ""):
                phrase = _extract_pattern_from_issue(iss)
                if phrase and phrase not in packet.banned_phrases:
                    packet.banned_phrases.append(phrase)

        assert "铜质怀表" in packet.banned_phrases
        assert "倒吸一口凉气" in packet.banned_phrases
        assert len(packet.banned_phrases) == 2


# ──────────────────────────────────────────────────────────────
# Test 6: StoryKernelMerger banned_phrases merge
# ──────────────────────────────────────────────────────────────

class TestStoryKernelMergerBannedPhrases:
    """StoryKernelMerger correctly merges new_banned_phrases into banned_phrases."""

    def test_merge_new_banned_phrases(self) -> None:
        """StoryKernelMerger merges new_banned_phrases into existing banned_phrases."""
        state = StoryKernel(
            project_id="test_merge",
            banned_phrases=["旧短语1", "旧短语2"],
        )
        delta = ChapterOutcome(
            source_chapter=2,
            new_banned_phrases=["新短语1", "新短语2"],
        )

        merger = StoryKernelMerger()
        new_state = merger.merge_outcome(state, delta)

        assert "旧短语1" in new_state.banned_phrases
        assert "旧短语2" in new_state.banned_phrases
        assert "新短语1" in new_state.banned_phrases
        assert "新短语2" in new_state.banned_phrases

    def test_merge_banned_phrases_capped_at_30(self) -> None:
        """StoryKernelMerger caps combined banned_phrases at 30 items."""
        existing = [f"旧{i}" for i in range(25)]
        new = [f"新{i}" for i in range(10)]

        state = StoryKernel(project_id="test_cap30", banned_phrases=existing)
        delta = ChapterOutcome(source_chapter=2, new_banned_phrases=new)

        merger = StoryKernelMerger()
        new_state = merger.merge_outcome(state, delta)

        assert len(new_state.banned_phrases) <= 30

    def test_merge_banned_phrases_truncated_to_50_chars(self) -> None:
        """StoryKernelMerger truncates each banned phrase to 50 characters."""
        long_phrase = "这是一个非常非常非常非常非常非常非常非常非常非常非常非常非常长的短语"
        state = StoryKernel(project_id="test_truncate", banned_phrases=[])
        delta = ChapterOutcome(source_chapter=2, new_banned_phrases=[long_phrase])

        merger = StoryKernelMerger()
        new_state = merger.merge_outcome(state, delta)

        assert len(new_state.banned_phrases[0]) <= 50

    def test_merge_no_new_banned_phrases(self) -> None:
        """StoryKernelMerger preserves existing banned_phrases when delta has none."""
        state = StoryKernel(
            project_id="test_no_new",
            banned_phrases=["保留短语"],
        )
        delta = ChapterOutcome(source_chapter=2)

        merger = StoryKernelMerger()
        new_state = merger.merge_outcome(state, delta)

        assert new_state.banned_phrases == ["保留短语"]

    def test_merge_empty_existing_banned_phrases(self) -> None:
        """StoryKernelMerger handles empty existing banned_phrases."""
        state = StoryKernel(project_id="test_empty_existing", banned_phrases=[])
        delta = ChapterOutcome(source_chapter=2, new_banned_phrases=["新短语"])

        merger = StoryKernelMerger()
        new_state = merger.merge_outcome(state, delta)

        assert new_state.banned_phrases == ["新短语"]


# ──────────────────────────────────────────────────────────────
# Test 7: Template rendering includes banned intent block
# ──────────────────────────────────────────────────────────────

class TestTemplateBannedIntentRendering:
    """draft_chapter.j2 template renders banned intent/phrase facts from immutable_facts."""

    @staticmethod
    def _draft_prompt_context(
        *,
        canon_context: dict[str, object],
        chapter_title: str,
        pov_character: str,
        target_word_count: int,
    ) -> dict[str, object]:
        outline = SimpleNamespace(
            chapter_number=1,
            title=chapter_title,
            goal="测试禁用事实透传",
            pov_character=pov_character,
            expected_word_count=target_word_count,
        )
        packet = SimpleNamespace(
            chapter_number=1,
            chapter_contract={},
            character_profiles=[],
            must_carry_forward=[],
            guard_constraints=[],
        )
        plan = {
            "opening_contract": "开场",
            "closing_contract": "结尾",
            "scene_intents": [],
            "required_state_transitions": [],
            "emotional_arc": "紧张",
        }
        return {
            "chapter_number": 1,
            "target_word_count": target_word_count,
            "stage_cards": build_stage_cards(
                stage="draft",
                packet=packet,
                chapter_outline=outline,
                plan=plan,
                canon_context=canon_context,
            ),
        }

    def test_template_renders_banned_intent_facts(self) -> None:
        """Template includes 【禁用意向】 facts in the rendered prompt."""
        from novel_forge.prompts.builder import PromptBuilder

        builder = PromptBuilder()
        canon_context = {
            "characters": {},
            "immutable_facts": [
                "【禁用意向】禁止使用瞳孔微缩等眼部反应模板",
                "【禁用表达】铜质怀表发出微弱的光芒",
                "【已死亡】老张（第3章死亡）不可出场行动或对话",
            ],
            "world_facts": {},
        }

        prompt = builder.build(
            TaskType.DRAFT_CHAPTER,
            self._draft_prompt_context(
                canon_context=canon_context,
                chapter_title="测试章",
                pov_character="林远",
                target_word_count=3000,
            ),
        )

        prompt_text = prompt.messages[1]["content"]

        assert "【禁用意向】禁止使用瞳孔微缩等眼部反应模板" in prompt_text
        assert "【禁用表达】铜质怀表发出微弱的光芒" in prompt_text

    def test_template_renders_banned_facts_as_hard_constraints(self) -> None:
        """Banned intent/phrase facts appear in the '不可违反的事实' section."""
        from novel_forge.prompts.builder import PromptBuilder

        builder = PromptBuilder()
        canon_context = {
            "characters": {},
            "immutable_facts": [
                "【禁用意向】禁止模板化描写",
                "【禁用表达】重复短语A",
            ],
            "world_facts": {},
        }

        prompt = builder.build(
            TaskType.DRAFT_CHAPTER,
            self._draft_prompt_context(
                canon_context=canon_context,
                chapter_title="测试",
                pov_character="主角",
                target_word_count=2000,
            ),
        )

        prompt_text = prompt.messages[1]["content"]

        assert "不可违反的事实" in prompt_text
        assert "【禁用意向】禁止模板化描写" in prompt_text
        assert "【禁用表达】重复短语A" in prompt_text

    def test_template_no_banned_facts_when_empty(self) -> None:
        """Template does not render banned intent section when immutable_facts is empty."""
        from novel_forge.prompts.builder import PromptBuilder

        builder = PromptBuilder()
        canon_context = {"characters": {}, "immutable_facts": [], "world_facts": {}}

        prompt = builder.build(
            TaskType.DRAFT_CHAPTER,
            self._draft_prompt_context(
                canon_context=canon_context,
                chapter_title="测试",
                pov_character="主角",
                target_word_count=2000,
            ),
        )

        prompt_text = prompt.messages[1]["content"]

        assert "【禁用意向】" not in prompt_text
        assert "【禁用表达】" not in prompt_text


# ──────────────────────────────────────────────────────────────
# Test 8: End-to-end banned intent propagation pipeline
# ──────────────────────────────────────────────────────────────

class TestEndToEndBannedIntentPipeline:
    """Full pipeline: StoryBible → StoryKernel → StoryKernelRetriever → Template → banned intent propagation."""

    def test_full_pipeline_banned_intent_propagation(self) -> None:
        """End-to-end: banned_intent_rules from StoryBible reach template via StoryKernelRetriever."""
        story_bible = StoryBible(
            premise="一个关于记忆的故事",
            banned_intent_rules=[
                "禁止使用瞳孔微缩等眼部反应模板",
                "禁止用'倒吸一口凉气'表达惊讶",
            ],
        )

        state = StoryKernel(
            project_id="test_e2e",
            banned_phrases=["铜质怀表发出微弱的光芒"],
        )

        retriever = StoryKernelRetriever()
        ctx = retriever.get_context(state, for_chapter=1, story_bible=story_bible)

        assert any("【禁用意向】禁止使用瞳孔微缩" in f for f in ctx.immutable_facts)
        assert any("【禁用意向】禁止用'倒吸一口凉气'" in f for f in ctx.immutable_facts)
        assert any("【禁用表达】铜质怀表发出微弱的光芒" in f for f in ctx.immutable_facts)

    def test_full_pipeline_with_merger_and_retriever(self) -> None:
        """StoryKernelMerger → StoryKernelRetriever: merged banned_phrases appear in immutable_facts."""
        state = StoryKernel(
            project_id="test_e2e_merge",
            banned_phrases=["旧短语"],
        )
        delta = ChapterOutcome(
            source_chapter=2,
            new_banned_phrases=["新短语1", "新短语2"],
        )

        merger = StoryKernelMerger()
        merged_state = merger.merge_outcome(state, delta)

        story_bible = StoryBible(
            premise="测试",
            banned_intent_rules=["禁止模板化"],
        )

        retriever = StoryKernelRetriever()
        ctx = retriever.get_context(merged_state, for_chapter=2, story_bible=story_bible)

        assert any("【禁用表达】旧短语" in f for f in ctx.immutable_facts)
        assert any("【禁用表达】新短语1" in f for f in ctx.immutable_facts)
        assert any("【禁用表达】新短语2" in f for f in ctx.immutable_facts)
        assert any("【禁用意向】禁止模板化" in f for f in ctx.immutable_facts)

    def test_full_pipeline_motif_to_banned_to_template(self) -> None:
        """MotifTracker repeated phrases → StoryKernel.banned_phrases → StoryKernelRetriever → template."""
        tracker = MotifTracker.__new__(MotifTracker)
        text = "他推开门。他推开门。房间里很安静。他推开门。"
        repeated = tracker.extract_repeated_phrases(text, min_length=4, min_occurrences=2)

        state = StoryKernel(project_id="test_motif_pipeline", banned_phrases=[])
        state.banned_phrases.extend(repeated[:5])

        retriever = StoryKernelRetriever()
        ctx = retriever.get_context(state, for_chapter=1)

        banned_phrase_facts = [f for f in ctx.immutable_facts if f.startswith("【禁用表达】")]
        assert len(banned_phrase_facts) > 0
