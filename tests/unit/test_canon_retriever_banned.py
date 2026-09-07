"""Tests for StoryKernelRetriever._extract_immutable_facts with banned intent/phrases."""

from __future__ import annotations

from novel_forge.core.schemas.bible import StoryBible
from novel_forge.story_kernel.retriever import StoryKernelRetriever
from novel_forge.story_kernel.schemas import StoryKernel


def _make_state(**overrides) -> StoryKernel:
    defaults = {"project_id": "test", "current_chapter": 5}
    defaults.update(overrides)
    return StoryKernel(**defaults)


def _make_bible(**overrides) -> StoryBible:
    defaults = {"premise": "test"}
    defaults.update(overrides)
    return StoryBible(**defaults)


class TestExtractImmutableFacts_BannedIntent:
    def test_banned_intent_rules_rendered(self) -> None:
        bible = _make_bible(banned_intent_rules=[
            "禁止使用瞳孔微缩等眼部反应模板",
            "私下对话中角色自称用我而非官职",
        ])
        state = _make_state()
        facts = StoryKernelRetriever._extract_immutable_facts(state, 6, story_bible=bible)
        banned_intent_facts = [f for f in facts if f.startswith("【禁用意向】")]
        assert len(banned_intent_facts) == 2
        assert "【禁用意向】禁止使用瞳孔微缩等眼部反应模板" in banned_intent_facts

    def test_banned_intent_capped_at_15(self) -> None:
        rules = [f"规则{i}" for i in range(20)]
        bible = _make_bible(banned_intent_rules=rules)
        state = _make_state()
        facts = StoryKernelRetriever._extract_immutable_facts(state, 6, story_bible=bible)
        banned_intent_facts = [f for f in facts if f.startswith("【禁用意向】")]
        assert len(banned_intent_facts) == 15

    def test_empty_banned_intent_rules(self) -> None:
        bible = _make_bible(banned_intent_rules=[])
        state = _make_state()
        facts = StoryKernelRetriever._extract_immutable_facts(state, 6, story_bible=bible)
        banned_intent_facts = [f for f in facts if f.startswith("【禁用意向】")]
        assert banned_intent_facts == []

    def test_no_story_bible(self) -> None:
        state = _make_state()
        facts = StoryKernelRetriever._extract_immutable_facts(state, 6, story_bible=None)
        banned_intent_facts = [f for f in facts if f.startswith("【禁用意向】")]
        assert banned_intent_facts == []

    def test_whitespace_only_rules_skipped(self) -> None:
        bible = _make_bible(banned_intent_rules=["   ", "有效规则", ""])
        state = _make_state()
        facts = StoryKernelRetriever._extract_immutable_facts(state, 6, story_bible=bible)
        banned_intent_facts = [f for f in facts if f.startswith("【禁用意向】")]
        assert len(banned_intent_facts) == 1
        assert "【禁用意向】有效规则" in banned_intent_facts


class TestExtractImmutableFacts_BannedPhrases:
    def test_banned_phrases_rendered(self) -> None:
        state = _make_state(banned_phrases=["深吸一口气", "目光坚定"])
        facts = StoryKernelRetriever._extract_immutable_facts(state, 6)
        banned_phrase_facts = [f for f in facts if f.startswith("【禁用表达】")]
        assert len(banned_phrase_facts) == 2
        assert "【禁用表达】深吸一口气" in banned_phrase_facts

    def test_banned_phrases_capped_at_20(self) -> None:
        phrases = [f"短语{i}" for i in range(30)]
        state = _make_state(banned_phrases=phrases)
        facts = StoryKernelRetriever._extract_immutable_facts(state, 6)
        banned_phrase_facts = [f for f in facts if f.startswith("【禁用表达】")]
        assert len(banned_phrase_facts) == 20

    def test_empty_banned_phrases(self) -> None:
        state = _make_state(banned_phrases=[])
        facts = StoryKernelRetriever._extract_immutable_facts(state, 6)
        banned_phrase_facts = [f for f in facts if f.startswith("【禁用表达】")]
        assert banned_phrase_facts == []

    def test_whitespace_only_phrases_skipped(self) -> None:
        state = _make_state(banned_phrases=["   ", "有效短语", ""])
        facts = StoryKernelRetriever._extract_immutable_facts(state, 6)
        banned_phrase_facts = [f for f in facts if f.startswith("【禁用表达】")]
        assert len(banned_phrase_facts) == 1
        assert "【禁用表达】有效短语" in banned_phrase_facts


class TestExtractImmutableFacts_Ordering:
    def test_banned_intent_before_banned_phrases(self) -> None:
        bible = _make_bible(banned_intent_rules=["意向规则"])
        state = _make_state(banned_phrases=["禁用短语"])
        facts = StoryKernelRetriever._extract_immutable_facts(state, 6, story_bible=bible)
        intent_idx = facts.index("【禁用意向】意向规则")
        phrase_idx = facts.index("【禁用表达】禁用短语")
        assert intent_idx < phrase_idx
