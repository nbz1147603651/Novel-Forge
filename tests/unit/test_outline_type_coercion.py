"""Test mode="before" field validators on outline.py schema classes.

Covers NarrativePhase, SubplotPlan, CharacterArcPlan (Task 19) plus
ChapterOutline, PayoffPlan, VolumeOutline, TurningPoint, ArcMilestone,
SubplotChapterEvent, SuspenseScheduleItem, SubplotWeaveLink, and
StoryOutline (Task 20).  Uses shared test helpers from
``test_schema_type_coercion_helpers`` for string and list[str] coercion.
"""

from __future__ import annotations

from typing import Any

from novel_forge.core.schemas.outline import (
    ArcMilestone,
    ChapterOutline,
    CharacterArcPlan,
    NarrativePhase,
    PayoffPlan,
    StoryOutline,
    SubplotChapterEvent,
    SubplotPlan,
    SubplotWeaveLink,
    SuspenseScheduleItem,
    TurningPoint,
    VolumeOutline,
)

from .test_schema_type_coercion_helpers import (
    assert_field_coerces_to_string,
    assert_list_field_coerces,
)


class TestNarrativePhase:
    """mode="before" validators on NarrativePhase."""

    # ── String fields (stringify_text_value) ─────────────────────────

    def test_phase_name_coerces_dict(self) -> None:
        assert_field_coerces_to_string(NarrativePhase, "phase_name", {"zh": "崛起篇"})

    def test_phase_name_coerces_list(self) -> None:
        assert_field_coerces_to_string(NarrativePhase, "phase_name", ["崛起", "冲突"])

    def test_phase_name_coerces_int(self) -> None:
        assert_field_coerces_to_string(NarrativePhase, "phase_name", 1, expected="1")

    def test_phase_name_coerces_none(self) -> None:
        assert_field_coerces_to_string(NarrativePhase, "phase_name", None, expected="")

    def test_phase_name_keeps_str(self) -> None:
        assert_field_coerces_to_string(NarrativePhase, "phase_name", "崛起篇", expected="崛起篇")

    # ── list[str] fields (coerce to list) ────────────────────────────

    def test_key_events_from_str(self) -> None:
        assert_list_field_coerces(NarrativePhase, "key_events", "主角觉醒", expected=["主角觉醒"])

    def test_key_events_from_none(self) -> None:
        assert_list_field_coerces(NarrativePhase, "key_events", None, expected=[])

    def test_key_events_from_list(self) -> None:
        assert_list_field_coerces(
            NarrativePhase,
            "key_events",
            ["事件一", {"nested": "obj"}],
            expected=["事件一", "nested: obj"],
        )

    def test_primary_locations_from_str(self) -> None:
        assert_list_field_coerces(
            NarrativePhase, "primary_locations", "主城", expected=["主城"]
        )

    def test_primary_locations_from_none(self) -> None:
        assert_list_field_coerces(NarrativePhase, "primary_locations", None, expected=[])

    def test_primary_locations_from_list(self) -> None:
        assert_list_field_coerces(
            NarrativePhase,
            "primary_locations",
            ["王宫", 42],
            expected=["王宫", "42"],
        )

    def test_key_characters_from_str(self) -> None:
        assert_list_field_coerces(
            NarrativePhase, "key_characters", "林墨", expected=["林墨"]
        )

    def test_key_characters_from_none(self) -> None:
        assert_list_field_coerces(NarrativePhase, "key_characters", None, expected=[])

    def test_key_characters_from_list(self) -> None:
        assert_list_field_coerces(
            NarrativePhase,
            "key_characters",
            ["林墨", ["白夜"]],
            expected=["林墨", "白夜"],
        )


class TestSubplotPlan:
    """mode="before" validators on SubplotPlan."""

    def test_name_coerces_dict(self) -> None:
        assert_field_coerces_to_string(SubplotPlan, "name", {"zh": "复仇线"})

    def test_name_coerces_none(self) -> None:
        assert_field_coerces_to_string(SubplotPlan, "name", None, expected="")

    def test_name_keeps_str(self) -> None:
        assert_field_coerces_to_string(SubplotPlan, "name", "身世线", expected="身世线")

    def test_description_coerces_list(self) -> None:
        assert_field_coerces_to_string(SubplotPlan, "description", ["desc1", "desc2"])

    def test_description_coerces_none(self) -> None:
        assert_field_coerces_to_string(SubplotPlan, "description", None, expected="")

    def test_description_keeps_str(self) -> None:
        assert_field_coerces_to_string(
            SubplotPlan, "description", "主人公揭开身世", expected="主人公揭开身世"
        )


class TestCharacterArcPlan:
    """mode="before" validators on CharacterArcPlan."""

    def test_character_coerces_dict(self) -> None:
        assert_field_coerces_to_string(CharacterArcPlan, "character", {"name": "林墨"})

    def test_character_coerces_none(self) -> None:
        assert_field_coerces_to_string(CharacterArcPlan, "character", None, expected="")

    def test_character_keeps_str(self) -> None:
        assert_field_coerces_to_string(CharacterArcPlan, "character", "林墨", expected="林墨")

    def test_arc_summary_coerces_list(self) -> None:
        assert_field_coerces_to_string(
            CharacterArcPlan, "arc_summary", ["从菜鸟到大神"]
        )

    def test_arc_summary_coerces_none(self) -> None:
        assert_field_coerces_to_string(CharacterArcPlan, "arc_summary", None, expected="")

    def test_arc_summary_keeps_str(self) -> None:
        assert_field_coerces_to_string(
            CharacterArcPlan, "arc_summary", "从菜鸟到大神的蜕变", expected="从菜鸟到大神的蜕变"
        )


class TestChapterOutline:
    """mode="before" validators on ChapterOutline — goal/pov_character/notes/subplot_focus."""

    @staticmethod
    def _make(**kwargs: Any) -> ChapterOutline:
        defaults = {"chapter_number": 1, "goal": "dummy"}
        return ChapterOutline(**{**defaults, **kwargs})  # type: ignore[arg-type]

    # ── goal ────────────────────────────────────────────────────────

    def test_goal_coerces_dict(self) -> None:
        inst = self._make(goal={"zh": "目标"})
        assert inst.goal == "zh: 目标"

    def test_goal_coerces_list(self) -> None:
        inst = self._make(goal=["a", "b"])
        assert inst.goal == "a；b"

    def test_goal_coerces_int(self) -> None:
        inst = self._make(goal=42)
        assert inst.goal == "42"

    def test_goal_coerces_none(self) -> None:
        inst = self._make(goal=None)
        assert inst.goal == ""

    def test_goal_keeps_str(self) -> None:
        inst = self._make(goal="完成任务")
        assert inst.goal == "完成任务"

    # ── pov_character ───────────────────────────────────────────────

    def test_pov_character_coerces_dict(self) -> None:
        inst = self._make(pov_character={"name": "林墨"})
        assert inst.pov_character == "name: 林墨"

    def test_pov_character_coerces_list(self) -> None:
        inst = self._make(pov_character=["林墨", "白夜"])
        assert inst.pov_character == "林墨；白夜"

    def test_pov_character_coerces_int(self) -> None:
        inst = self._make(pov_character=0)
        assert inst.pov_character == "0"

    def test_pov_character_coerces_none(self) -> None:
        inst = self._make(pov_character=None)
        assert inst.pov_character == ""

    def test_pov_character_keeps_str(self) -> None:
        inst = self._make(pov_character="林墨")
        assert inst.pov_character == "林墨"

    # ── notes ───────────────────────────────────────────────────────

    def test_notes_coerces_dict(self) -> None:
        inst = self._make(notes={"hint": "伏笔"})
        assert inst.notes == "hint: 伏笔"

    def test_notes_coerces_list(self) -> None:
        inst = self._make(notes=["note1", "note2"])
        assert inst.notes == "note1；note2"

    def test_notes_coerces_int(self) -> None:
        inst = self._make(notes=99)
        assert inst.notes == "99"

    def test_notes_coerces_none(self) -> None:
        inst = self._make(notes=None)
        assert inst.notes == ""

    def test_notes_keeps_str(self) -> None:
        inst = self._make(notes="预留伏笔")
        assert inst.notes == "预留伏笔"

    # ── subplot_focus ───────────────────────────────────────────────

    def test_subplot_focus_coerces_dict(self) -> None:
        inst = self._make(subplot_focus={"id": "revenge"})
        assert inst.subplot_focus == "id: revenge"

    def test_subplot_focus_coerces_list(self) -> None:
        inst = self._make(subplot_focus=["复仇线", "身世线"])
        assert inst.subplot_focus == "复仇线；身世线"

    def test_subplot_focus_coerces_int(self) -> None:
        inst = self._make(subplot_focus=1)
        assert inst.subplot_focus == "1"

    def test_subplot_focus_coerces_none(self) -> None:
        inst = self._make(subplot_focus=None)
        assert inst.subplot_focus == ""

    def test_subplot_focus_keeps_str(self) -> None:
        inst = self._make(subplot_focus="复仇线")
        assert inst.subplot_focus == "复仇线"


class TestPayoffPlan:
    """mode="before" validator on PayoffPlan.description."""

    def test_description_coerces_dict(self) -> None:
        assert_field_coerces_to_string(PayoffPlan, "description", {"zh": "兑现"})

    def test_description_coerces_list(self) -> None:
        assert_field_coerces_to_string(PayoffPlan, "description", ["a", "b"])

    def test_description_coerces_int(self) -> None:
        assert_field_coerces_to_string(PayoffPlan, "description", 42, expected="42")

    def test_description_coerces_none(self) -> None:
        assert_field_coerces_to_string(PayoffPlan, "description", None, expected="")

    def test_description_keeps_str(self) -> None:
        assert_field_coerces_to_string(
            PayoffPlan, "description", "揭示身世", expected="揭示身世"
        )


class TestVolumeOutline:
    """mode="before" validators on VolumeOutline text fields."""

    @staticmethod
    def _make(**kwargs: Any) -> VolumeOutline:
        defaults: dict[str, Any] = {
            "volume_number": 1, "start_chapter": 1, "end_chapter": 10,
        }
        return VolumeOutline(**{**defaults, **kwargs})  # type: ignore[arg-type]

    # ── title ───────────────────────────────────────────────────────

    def test_title_coerces_dict(self) -> None:
        inst = self._make(title={"zh": "第一卷"})
        assert isinstance(inst.title, str)
        assert inst.title == "zh: 第一卷"

    def test_title_coerces_none(self) -> None:
        inst = self._make(title=None)
        assert inst.title == ""

    def test_title_keeps_str(self) -> None:
        inst = self._make(title="崛起篇")
        assert inst.title == "崛起篇"

    # ── arc_goal ────────────────────────────────────────────────────

    def test_arc_goal_coerces_list(self) -> None:
        inst = self._make(arc_goal=["goal1", "goal2"])
        assert inst.arc_goal == "goal1；goal2"

    def test_arc_goal_coerces_none(self) -> None:
        inst = self._make(arc_goal=None)
        assert inst.arc_goal == ""

    def test_arc_goal_keeps_str(self) -> None:
        inst = self._make(arc_goal="建立王国")
        assert inst.arc_goal == "建立王国"

    # ── climax_hint ─────────────────────────────────────────────────

    def test_climax_hint_coerces_dict(self) -> None:
        inst = self._make(climax_hint={"event": "大战"})
        assert inst.climax_hint == "event: 大战"

    def test_climax_hint_coerces_none(self) -> None:
        inst = self._make(climax_hint=None)
        assert inst.climax_hint == ""

    def test_climax_hint_keeps_str(self) -> None:
        inst = self._make(climax_hint="最终对决")
        assert inst.climax_hint == "最终对决"

    # ── resolution_hint ─────────────────────────────────────────────

    def test_resolution_hint_coerces_int(self) -> None:
        inst = self._make(resolution_hint=0)
        assert inst.resolution_hint == "0"

    def test_resolution_hint_coerces_none(self) -> None:
        inst = self._make(resolution_hint=None)
        assert inst.resolution_hint == ""

    def test_resolution_hint_keeps_str(self) -> None:
        inst = self._make(resolution_hint="和平降临")
        assert inst.resolution_hint == "和平降临"

    # ── notes ───────────────────────────────────────────────────────

    def test_volume_notes_coerces_dict(self) -> None:
        inst = self._make(notes={"key": "val"})
        assert inst.notes == "key: val"

    def test_volume_notes_coerces_none(self) -> None:
        inst = self._make(notes=None)
        assert inst.notes == ""

    def test_volume_notes_keeps_str(self) -> None:
        inst = self._make(notes="卷末留白")
        assert inst.notes == "卷末留白"


class TestTurningPoint:
    """mode="before" validators on TurningPoint."""

    def test_description_coerces_dict(self) -> None:
        assert_field_coerces_to_string(TurningPoint, "description", {"zh": "转折"})

    def test_description_coerces_none(self) -> None:
        assert_field_coerces_to_string(TurningPoint, "description", None, expected="")

    def test_description_keeps_str(self) -> None:
        assert_field_coerces_to_string(
            TurningPoint, "description", "主角觉醒", expected="主角觉醒"
        )

    def test_location_coerces_list(self) -> None:
        assert_field_coerces_to_string(TurningPoint, "location", ["王宫", "密室"])

    def test_location_coerces_none(self) -> None:
        assert_field_coerces_to_string(TurningPoint, "location", None, expected="")

    def test_location_keeps_str(self) -> None:
        assert_field_coerces_to_string(TurningPoint, "location", "王宫", expected="王宫")


class TestArcMilestone:
    """mode="before" validator on ArcMilestone.description."""

    def test_description_coerces_dict(self) -> None:
        assert_field_coerces_to_string(ArcMilestone, "description", {"event": "蜕变"})

    def test_description_coerces_list(self) -> None:
        assert_field_coerces_to_string(ArcMilestone, "description", ["a", "b"])

    def test_description_coerces_int(self) -> None:
        assert_field_coerces_to_string(ArcMilestone, "description", 7, expected="7")

    def test_description_coerces_none(self) -> None:
        assert_field_coerces_to_string(ArcMilestone, "description", None, expected="")

    def test_description_keeps_str(self) -> None:
        assert_field_coerces_to_string(
            ArcMilestone, "description", "从懦弱到勇敢", expected="从懦弱到勇敢"
        )


class TestSubplotChapterEvent:
    """mode="before" validators on SubplotChapterEvent — event / weave_notes."""

    def test_event_coerces_dict(self) -> None:
        assert_field_coerces_to_string(SubplotChapterEvent, "event", {"zh": "事件"})

    def test_event_coerces_none(self) -> None:
        assert_field_coerces_to_string(SubplotChapterEvent, "event", None, expected="")

    def test_event_keeps_str(self) -> None:
        assert_field_coerces_to_string(
            SubplotChapterEvent, "event", "遭遇追兵", expected="遭遇追兵"
        )

    def test_weave_notes_coerces_list(self) -> None:
        assert_field_coerces_to_string(
            SubplotChapterEvent, "weave_notes", ["交织A", "交织B"]
        )

    def test_weave_notes_coerces_none(self) -> None:
        assert_field_coerces_to_string(
            SubplotChapterEvent, "weave_notes", None, expected=""
        )

    def test_weave_notes_keeps_str(self) -> None:
        assert_field_coerces_to_string(
            SubplotChapterEvent,
            "weave_notes",
            "与主线交汇",
            expected="与主线交汇",
        )


class TestSuspenseScheduleItem:
    """mode="before" validators on SuspenseScheduleItem."""

    def test_suspense_id_coerces_dict(self) -> None:
        assert_field_coerces_to_string(
            SuspenseScheduleItem, "suspense_id", {"id": "S001"}
        )

    def test_suspense_id_coerces_none(self) -> None:
        assert_field_coerces_to_string(
            SuspenseScheduleItem, "suspense_id", None, expected=""
        )

    def test_suspense_id_keeps_str(self) -> None:
        assert_field_coerces_to_string(
            SuspenseScheduleItem, "suspense_id", "S001", expected="S001"
        )

    def test_suspense_type_coerces_list(self) -> None:
        assert_field_coerces_to_string(
            SuspenseScheduleItem, "suspense_type", ["mystery", "crisis"]
        )

    def test_suspense_type_coerces_none(self) -> None:
        assert_field_coerces_to_string(
            SuspenseScheduleItem, "suspense_type", None, expected=""
        )

    def test_suspense_type_keeps_str(self) -> None:
        assert_field_coerces_to_string(
            SuspenseScheduleItem, "suspense_type", "mystery", expected="mystery"
        )

    def test_description_coerces_dict(self) -> None:
        assert_field_coerces_to_string(
            SuspenseScheduleItem, "description", {"zh": "悬念描述"}
        )

    def test_description_coerces_none(self) -> None:
        assert_field_coerces_to_string(
            SuspenseScheduleItem, "description", None, expected=""
        )

    def test_description_keeps_str(self) -> None:
        assert_field_coerces_to_string(
            SuspenseScheduleItem,
            "description",
            "主角身世之谜",
            expected="主角身世之谜",
        )

    def test_related_subplot_coerces_int(self) -> None:
        assert_field_coerces_to_string(
            SuspenseScheduleItem, "related_subplot", 2, expected="2"
        )

    def test_related_subplot_coerces_none(self) -> None:
        assert_field_coerces_to_string(
            SuspenseScheduleItem, "related_subplot", None, expected=""
        )

    def test_related_subplot_keeps_str(self) -> None:
        assert_field_coerces_to_string(
            SuspenseScheduleItem,
            "related_subplot",
            "复仇线",
            expected="复仇线",
        )


class TestSubplotWeaveLink:
    """mode="before" validators on SubplotWeaveLink."""

    def test_source_ref_coerces_dict(self) -> None:
        assert_field_coerces_to_string(
            SubplotWeaveLink, "source_ref", {"event": "主线事件"}
        )

    def test_source_ref_coerces_none(self) -> None:
        assert_field_coerces_to_string(
            SubplotWeaveLink, "source_ref", None, expected=""
        )

    def test_source_ref_keeps_str(self) -> None:
        assert_field_coerces_to_string(
            SubplotWeaveLink, "source_ref", "主角遇险", expected="主角遇险"
        )

    def test_target_subplot_coerces_list(self) -> None:
        assert_field_coerces_to_string(
            SubplotWeaveLink, "target_subplot", ["复仇线", "身世线"]
        )

    def test_target_subplot_coerces_none(self) -> None:
        assert_field_coerces_to_string(
            SubplotWeaveLink, "target_subplot", None, expected=""
        )

    def test_target_subplot_keeps_str(self) -> None:
        assert_field_coerces_to_string(
            SubplotWeaveLink,
            "target_subplot",
            "复仇线",
            expected="复仇线",
        )

    def test_description_coerces_dict(self) -> None:
        assert_field_coerces_to_string(
            SubplotWeaveLink, "description", {"zh": "交织关系"}
        )

    def test_description_coerces_none(self) -> None:
        assert_field_coerces_to_string(
            SubplotWeaveLink, "description", None, expected=""
        )

    def test_description_keeps_str(self) -> None:
        assert_field_coerces_to_string(
            SubplotWeaveLink,
            "description",
            "复仇线受主线事件触发",
            expected="复仇线受主线事件触发",
        )


class TestStoryOutline:
    """mode="before" validator on StoryOutline.synopsis."""

    @staticmethod
    def _make(**kwargs: Any) -> StoryOutline:
        dummy_chapter = ChapterOutline(chapter_number=1, goal="dummy")
        defaults: dict[str, Any] = {
            "total_chapters": 1,
            "chapters": [dummy_chapter],
        }
        return StoryOutline(**{**defaults, **kwargs})  # type: ignore[arg-type]

    def test_synopsis_coerces_dict(self) -> None:
        inst = self._make(synopsis={"zh": "全书概要"})
        assert isinstance(inst.synopsis, str)
        assert inst.synopsis == "zh: 全书概要"

    def test_synopsis_coerces_list(self) -> None:
        inst = self._make(synopsis=["概要1", "概要2"])
        assert inst.synopsis == "概要1；概要2"

    def test_synopsis_coerces_int(self) -> None:
        inst = self._make(synopsis=0)
        assert inst.synopsis == "0"

    def test_synopsis_coerces_none(self) -> None:
        inst = self._make(synopsis=None)
        assert inst.synopsis == ""

    def test_synopsis_keeps_str(self) -> None:
        inst = self._make(synopsis="一个关于勇气与复仇的故事")
        assert inst.synopsis == "一个关于勇气与复仇的故事"
