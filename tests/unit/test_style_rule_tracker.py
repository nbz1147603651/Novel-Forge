"""Tests for the cross-chapter StyleRuleTracker (writing-technique template detection)."""

from __future__ import annotations

from novel_forge.memory.style_rule_tracker import (
    RuleRepetitionWarning,
    StyleRuleTracker,
)

_WIND_PROFILE = {
    "modules": [
        {
            "name": "风的三层写法",
            "rules": ["高空风与穿堂风叠响", "墙缝呜咽收尾"],
            "positive_example": "高空气喘、穿堂低吟、墙缝呜咽",
            "negative_example": "风很大",
        },
        {
            "name": "半句话语法",
            "rules": ["单字回应承载潜台词"],
            "positive_example": "「嗯。」",
            "negative_example": "她回答说好的",
        },
    ]
}


def _tracker() -> StyleRuleTracker:
    t = StyleRuleTracker(lookback_chapters=5, repetition_gap_chapters=2)
    t.load_from_style_profile(_WIND_PROFILE)
    return t


class TestLoadFromStyleProfile:
    def test_loads_all_rules(self) -> None:
        t = _tracker()
        assert len(t.rules) == 3

    def test_warmup_complete_after_load(self) -> None:
        t = _tracker()
        assert t.is_warmup_complete is True

    def test_empty_profile_leaves_warmup_incomplete(self) -> None:
        t = StyleRuleTracker()
        t.load_from_style_profile({})
        assert t.is_warmup_complete is False
        assert t.rules == {}

    def test_reload_preserves_usage_stats(self) -> None:
        t = _tracker()
        t.register_chapter(1, "高空气喘，穿堂低吟，墙缝呜咽。")
        assert t.rules["风的三层写法::高空风与穿堂风叠响"].occurrence_count == 1
        # Reload (simulating a style-profile refresh); usage must survive.
        t.load_from_style_profile(_WIND_PROFILE)
        assert t.rules["风的三层写法::高空风与穿堂风叠响"].occurrence_count == 1


class TestRegisterChapter:
    def test_fires_on_cue_presence(self) -> None:
        t = _tracker()
        fired = t.register_chapter(1, "高空气喘，穿堂低吟，墙缝呜咽，三层叠响。")
        assert "风的三层写法::高空风与穿堂风叠响" in fired
        assert "风的三层写法::墙缝呜咽收尾" in fired

    def test_does_not_fire_without_cues(self) -> None:
        t = _tracker()
        fired = t.register_chapter(1, "本章完全不写风，只写对话。")
        assert fired == []

    def test_occurrence_count_increments(self) -> None:
        t = _tracker()
        t.register_chapter(1, "高空气喘，穿堂低吟。")
        t.register_chapter(2, "高空气喘，穿堂低吟。")
        assert t.rules["风的三层写法::高空风与穿堂风叠响"].occurrence_count == 2
        assert t.rules["风的三层写法::高空风与穿堂风叠响"].first_appearance_chapter == 1
        assert t.rules["风的三层写法::高空风与穿堂风叠响"].last_appearance_chapter == 2

    def test_registering_same_chapter_is_idempotent(self) -> None:
        t = _tracker()
        rule_id = "风的三层写法::高空风与穿堂风叠响"

        t.register_chapter(1, "高空气喘，穿堂低吟。")
        t.register_chapter(1, "高空气喘，穿堂低吟。")

        assert t.rules[rule_id].occurrence_count == 1
        assert t._recent_usage[rule_id] == [1]


class TestCheckRuleRepetition:
    def test_consecutive_fires_produce_warning(self) -> None:
        """The 穿堂风-template scenario: rule fires every chapter."""
        t = _tracker()
        for ch in range(1, 5):
            t.register_chapter(ch, "高空气喘，穿堂低吟，墙缝呜咽。")
        warnings = t.check_rule_repetition(5, "高空气喘，穿堂低吟。")
        assert len(warnings) >= 1
        assert all(isinstance(w, RuleRepetitionWarning) for w in warnings)
        # A rule firing 4+ times is high severity (entrenched template).
        assert any(w.severity == "high" for w in warnings)

    def test_no_warning_when_rule_dormant(self) -> None:
        t = _tracker()
        t.register_chapter(1, "高空气喘，穿堂低吟。")
        # Chapters 2-5 do not use the wind technique at all.
        for ch in range(2, 6):
            t.register_chapter(ch, "普通对话章节，无风描写。")
        warnings = t.check_rule_repetition(6, "普通对话章节，无风描写。")
        assert warnings == []

    def test_warning_only_for_current_chapter_fires(self) -> None:
        """A rule that fired recently but NOT this chapter is not a current template."""
        t = _tracker()
        t.register_chapter(1, "高空气喘，穿堂低吟。")
        t.register_chapter(2, "高空气喘，穿堂低吟。")
        # Chapter 3 does not use the wind technique.
        warnings = t.check_rule_repetition(3, "普通对话章节，无风描写。")
        assert warnings == []


class TestPromptProjection:
    def test_forbidden_repetition_listed(self) -> None:
        t = _tracker()
        for ch in range(1, 4):
            t.register_chapter(ch, "高空气喘，穿堂低吟，墙缝呜咽。")
        bundle = t.get_rules_for_prompt(4)
        assert len(bundle["forbidden_repetition"]) >= 1
        assert "风的三层写法" in bundle["style_rule_guidance"]

    def test_empty_bundle_when_no_rules(self) -> None:
        t = StyleRuleTracker()
        bundle = t.get_rules_for_prompt(1)
        assert bundle == {
            "active_rules": [],
            "forbidden_repetition": [],
            "style_rule_guidance": "",
        }


class TestMaintenance:
    def test_delete_chapter_rules_decrements(self) -> None:
        t = _tracker()
        t.register_chapter(1, "高空气喘，穿堂低吟。")
        t.register_chapter(2, "高空气喘，穿堂低吟。")
        assert t.rules["风的三层写法::高空风与穿堂风叠响"].occurrence_count == 2
        t.delete_chapter_rules(2)
        assert t.rules["风的三层写法::高空风与穿堂风叠响"].occurrence_count == 1
        assert 2 not in t._chapter_rules

    def test_delete_chapters_from_bulk_invalidates(self) -> None:
        t = _tracker()
        for ch in range(1, 4):
            t.register_chapter(ch, "高空气喘，穿堂低吟。")
        t.delete_chapters_from(3)
        assert all(ch < 3 for ch in t._chapter_rules)
