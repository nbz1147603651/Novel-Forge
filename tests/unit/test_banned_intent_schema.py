"""Tests for banned_intent_rules, banned_phrases schemas, and time_convention.

Covers:
- StoryBible.banned_intent_rules (validation, coercion, limits)
- StoryBible.time_convention
- StoryKernel.banned_phrases (validation, truncation)
- ChapterOutcome.new_banned_phrases (validation, truncation)
- GlobalStyleConfig.banned_phrases
"""

from __future__ import annotations

from novel_forge.core.domain.world_context import dump_story_bible_for_prompt
from novel_forge.core.schemas.bible import StoryBible
from novel_forge.core.schemas.chapter import ChapterOutcome
from novel_forge.core.schemas.style_profile import GlobalStyleConfig
from novel_forge.story_kernel.schemas import StoryKernel

# ────────────────────────────────────────────────────────
# StoryBible.banned_intent_rules
# ────────────────────────────────────────────────────────

class TestStoryBible_BannedIntentRules:
    def test_default_empty(self) -> None:
        bible = StoryBible(premise="test")
        assert bible.banned_intent_rules == []

    def test_valid_rules(self) -> None:
        bible = StoryBible(
            premise="test",
            banned_intent_rules=[
                "禁止使用瞳孔微缩等眼部反应模板",
                "私下对话中角色自称用我而非官职",
            ],
        )
        assert len(bible.banned_intent_rules) == 2

    def test_string_coerced_to_list(self) -> None:
        bible = StoryBible(
            premise="test",
            banned_intent_rules="单条规则",
        )
        assert bible.banned_intent_rules == ["单条规则"]

    def test_empty_string_coerced_to_empty_list(self) -> None:
        bible = StoryBible(
            premise="test",
            banned_intent_rules="   ",
        )
        assert bible.banned_intent_rules == []

    def test_non_list_coerced_to_empty(self) -> None:
        bible = StoryBible(
            premise="test",
            banned_intent_rules=42,  # type: ignore[arg-type]
        )
        assert bible.banned_intent_rules == []

    def test_max_20_items_truncated(self) -> None:
        rules = [f"规则{i}" for i in range(25)]
        bible = StoryBible(premise="test", banned_intent_rules=rules)
        assert len(bible.banned_intent_rules) == 20

    def test_each_rule_max_80_chars(self) -> None:
        long_rule = "A" * 200
        bible = StoryBible(premise="test", banned_intent_rules=[long_rule])
        assert len(bible.banned_intent_rules[0]) == 80

    def test_serialization_roundtrip(self) -> None:
        bible = StoryBible(
            premise="test",
            banned_intent_rules=["规则一", "规则二"],
        )
        data = bible.model_dump(mode="json")
        restored = StoryBible.model_validate(data)
        assert restored.banned_intent_rules == ["规则一", "规则二"]

    def test_backward_compat_old_json_without_field(self) -> None:
        old_data = {"premise": "old story", "schema_version": "2.0"}
        bible = StoryBible.model_validate(old_data)
        assert bible.banned_intent_rules == []


# ────────────────────────────────────────────────────────
# StoryBible.time_convention
# ────────────────────────────────────────────────────────

class TestStoryBible_TimeConvention:
    def test_default_empty(self) -> None:
        bible = StoryBible(premise="test")
        assert bible.time_convention == ""

    def test_valid_convention(self) -> None:
        bible = StoryBible(
            premise="test",
            time_convention="古代中国：时辰/刻",
        )
        assert bible.time_convention == "古代中国：时辰/刻"

    def test_serialization_roundtrip(self) -> None:
        bible = StoryBible(
            premise="test",
            time_convention="现代：小时/分钟",
        )
        data = bible.model_dump(mode="json")
        restored = StoryBible.model_validate(data)
        assert restored.time_convention == "现代：小时/分钟"

    def test_structured_text_fields_are_coerced_to_strings(self) -> None:
        bible = StoryBible.model_validate(
            {
                "premise": {"content": "现代商战与民国旧约互相映照。"},
                "time_convention": [
                    {"rule_id": "tc001", "content": "现代线使用公历日期。"},
                    {"rule_id": "tc002", "content": "民国线采用民国纪年。"},
                ],
                "notes": [
                    "怀表与金镯是核心信物。",
                    {"content": "外滩钟楼作为跨时代地标反复出现。"},
                ],
            }
        )

        assert bible.premise == "现代商战与民国旧约互相映照。"
        assert bible.time_convention == "现代线使用公历日期。；民国线采用民国纪年。"
        assert bible.notes == "怀表与金镯是核心信物。；外滩钟楼作为跨时代地标反复出现。"

    def test_extra_detail_fields_merge_without_validation_error(self) -> None:
        bible = StoryBible.model_validate(
            {
                "premise": "test",
                "era": "武周晚期",
                "era_detail": "公元700-705年，权力真空逐步扩大。",
                "additional_notes": "主线围绕五次查账展开。",
            }
        )

        assert "武周晚期" in bible.era
        assert "公元700-705年" in bible.era
        assert "主线围绕五次查账展开。" in bible.notes

    def test_world_rules_alias_maps_to_rules(self) -> None:
        bible = StoryBible.model_validate(
            {
                "premise": "test",
                "world_rules": ["规则一", "规则二"],
            }
        )

        assert bible.rules == ["规则一", "规则二"]

    def test_structured_world_rules_are_coerced_to_strings(self) -> None:
        bible = StoryBible.model_validate(
            {
                "premise": "test",
                "world_rules": [
                    {"rule_id": "wr001", "content": "关键证据的法医周期约6-12个月。"},
                    {"rule_id": "wr002", "rule": "公关反击必须留下可追溯的舆论代价。"},
                ],
            }
        )

        assert bible.rules == [
            "关键证据的法医周期约6-12个月。",
            "公关反击必须留下可追溯的舆论代价。",
        ]

    def test_structured_themes_and_banned_rules_are_coerced_to_strings(self) -> None:
        bible = StoryBible.model_validate(
            {
                "premise": "test",
                "themes": [{"theme": "迟到的正义必须付出人际代价。"}],
                "banned_intent_rules": [{"content": "禁止用抽象口号替代具体行动后果。"}],
            }
        )

        assert bible.themes == ["迟到的正义必须付出人际代价。"]
        assert bible.banned_intent_rules == ["禁止用抽象口号替代具体行动后果。"]


# ────────────────────────────────────────────────────────
# StoryBible world-context rules
# ────────────────────────────────────────────────────────


class TestStoryBible_WorldContextRules:
    def test_default_empty(self) -> None:
        bible = StoryBible(premise="test")
        assert bible.social_hierarchy == ""
        assert bible.address_rules == []
        assert bible.self_reference_rules == []
        assert bible.etiquette_rules == []
        assert bible.institution_terms == []
        assert bible.material_culture == []
        assert bible.anachronism_blacklist == []
        assert bible.dialogue_register_rules == []

    def test_string_fields_are_coerced_to_lists(self) -> None:
        bible = StoryBible(
            premise="test",
            address_rules="官场正式场合称官职，不直呼其名",
            dialogue_register_rules="对白古雅但不堆砌文言虚词",
        )
        assert bible.address_rules == ["官场正式场合称官职，不直呼其名"]
        assert bible.dialogue_register_rules == ["对白古雅但不堆砌文言虚词"]

    def test_structured_world_context_rules_are_coerced_to_strings(self) -> None:
        bible = StoryBible.model_validate(
            {
                "premise": "test",
                "social_hierarchy": {"content": "律所、平台、委托人之间存在明确签核链。"},
                "address_rules": [
                    {"rule_id": "ar001", "content": "公开场合称职务，私下可称姓名。"}
                ],
                "institution_terms": [{"term": "证据保全申请"}],
            }
        )

        assert bible.social_hierarchy == "律所、平台、委托人之间存在明确签核链。"
        assert bible.address_rules == ["公开场合称职务，私下可称姓名。"]
        assert bible.institution_terms == ["证据保全申请"]

    def test_world_context_rules_are_limited(self) -> None:
        long_rule = "A" * 200
        bible = StoryBible(
            premise="test",
            social_hierarchy="B" * 800,
            etiquette_rules=[long_rule for _ in range(25)],
        )
        assert len(bible.social_hierarchy) == 500
        assert len(bible.etiquette_rules) == 20
        assert len(bible.etiquette_rules[0]) == 120

    def test_serialization_roundtrip(self) -> None:
        bible = StoryBible(
            premise="test",
            social_hierarchy="士族、寒门、内廷、外朝等级分明",
            address_rules=["下级称上级官职或尊称"],
            self_reference_rules=["正式场合不滥用现代口语自称"],
            etiquette_rules=["越级发言需有引见或场景理由"],
            institution_terms=["中书省", "御史台"],
            material_culture=["笏板", "竹简"],
            anachronism_blacklist=["手机", "打卡"],
            dialogue_register_rules=["私下可略白话，公堂须克制庄重"],
        )
        data = bible.model_dump(mode="json")
        restored = StoryBible.model_validate(data)
        assert restored.address_rules == ["下级称上级官职或尊称"]
        assert restored.anachronism_blacklist == ["手机", "打卡"]
        assert restored.dialogue_register_rules == ["私下可略白话，公堂须克制庄重"]

    def test_prompt_dump_prunes_empty_optional_world_context(self) -> None:
        bible = StoryBible(
            premise="test",
            title="普通现代日常",
            era="现代",
            time_convention="",
            address_rules=[],
            anachronism_blacklist=[],
        )

        data = dump_story_bible_for_prompt(bible, mode="json")

        assert data["title"] == "普通现代日常"
        assert "time_convention" not in data
        assert "address_rules" not in data
        assert "anachronism_blacklist" not in data

    def test_prompt_dump_keeps_non_empty_world_context(self) -> None:
        bible = StoryBible(
            premise="test",
            era="武周末年",
            time_convention="古代中国：时辰/刻",
            address_rules=["臣下面圣称陛下"],
        )

        data = dump_story_bible_for_prompt(bible, mode="json")

        assert data["time_convention"] == "古代中国：时辰/刻"
        assert data["address_rules"] == ["臣下面圣称陛下"]


# ────────────────────────────────────────────────────────
# StoryKernel.banned_phrases
# ────────────────────────────────────────────────────────

class TestStoryKernel_BannedPhrases:
    def test_default_empty(self) -> None:
        state = StoryKernel(project_id="test")
        assert state.banned_phrases == []

    def test_valid_phrases(self) -> None:
        state = StoryKernel(
            project_id="test",
            banned_phrases=["深吸一口气", "目光坚定"],
        )
        assert len(state.banned_phrases) == 2

    def test_truncate_to_30_items(self) -> None:
        phrases = [f"短语{i}" for i in range(50)]
        state = StoryKernel(project_id="test", banned_phrases=phrases)
        assert len(state.banned_phrases) == 30

    def test_each_phrase_max_50_chars(self) -> None:
        long_phrase = "A" * 100
        state = StoryKernel(project_id="test", banned_phrases=[long_phrase])
        assert len(state.banned_phrases[0]) == 50

    def test_serialization_roundtrip(self) -> None:
        state = StoryKernel(
            project_id="test",
            banned_phrases=["短语一", "短语二"],
        )
        data = state.model_dump(mode="json")
        restored = StoryKernel.model_validate(data)
        assert restored.banned_phrases == ["短语一", "短语二"]


# ────────────────────────────────────────────────────────
# ChapterOutcome.new_banned_phrases
# ────────────────────────────────────────────────────────

class TestChapterOutcome_NewBannedPhrases:
    def test_default_empty(self) -> None:
        delta = ChapterOutcome(source_chapter=1)
        assert delta.new_banned_phrases == []

    def test_valid_phrases(self) -> None:
        delta = ChapterOutcome(
            source_chapter=1,
            new_banned_phrases=["重复短语A", "重复短语B"],
        )
        assert len(delta.new_banned_phrases) == 2

    def test_truncate_to_30_items(self) -> None:
        phrases = [f"新短语{i}" for i in range(50)]
        delta = ChapterOutcome(source_chapter=1, new_banned_phrases=phrases)
        assert len(delta.new_banned_phrases) == 30

    def test_each_phrase_max_50_chars(self) -> None:
        long_phrase = "B" * 100
        delta = ChapterOutcome(source_chapter=1, new_banned_phrases=[long_phrase])
        assert len(delta.new_banned_phrases[0]) == 50


# ────────────────────────────────────────────────────────
# GlobalStyleConfig.banned_phrases
# ────────────────────────────────────────────────────────

class TestGlobalStyleConfig_BannedPhrases:
    def test_default_empty(self) -> None:
        config = GlobalStyleConfig()
        assert config.banned_phrases == []

    def test_valid_phrases(self) -> None:
        config = GlobalStyleConfig(
            banned_phrases=["陈词滥调一", "陈词滥调二"],
        )
        assert len(config.banned_phrases) == 2

    def test_serialization_roundtrip(self) -> None:
        config = GlobalStyleConfig(
            banned_phrases=["短语一"],
        )
        data = config.model_dump(mode="json")
        restored = GlobalStyleConfig.model_validate(data)
        assert restored.banned_phrases == ["短语一"]
