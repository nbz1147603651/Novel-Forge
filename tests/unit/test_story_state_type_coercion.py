"""Tests for ``mode="before"`` type coercion on story state sub-schemas.

Covers: PhysicalState, EmotionalState, MotivationState, KnowledgeState.
"""

from __future__ import annotations

from typing import Any

import pytest

from novel_forge.core.schemas.story_state import (
    ChapterExitState,
    EmotionalState,
    KnowledgeState,
    MotivationState,
    PhysicalState,
    PlotThreadState,
    RelationshipState,
)

# ── PhysicalState ──────────────────────────────────────────────────


PHYSICAL_STR_FIELDS: list[str] = ["fatigue"]
PHYSICAL_LIST_FIELDS: list[str] = ["injuries"]


class TestPhysicalStateFatigueCoercion:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (None, ""),
            ("", ""),
            (42, "42"),
            (3.14, "3.14"),
            (True, "True"),
            ("  tired  ", "tired"),
            ("extremely fatigued", "extremely fatigued"),
            ({"level": "high", "cause": "battle"}, "level: high；cause: battle"),
            (["exhausted", "weak"], "exhausted；weak"),
        ],
    )
    def test_coerces_to_string(self, raw: Any, expected: str) -> None:
        state = PhysicalState(fatigue=raw)
        assert state.fatigue == expected
        assert isinstance(state.fatigue, str)

    def test_default_is_empty_string(self) -> None:
        state = PhysicalState()
        assert state.fatigue == ""
        assert isinstance(state.fatigue, str)


class TestPhysicalStateInjuriesCoercion:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (["broken leg", "concussion"], ["broken leg", "concussion"]),
            (["sprain"], ["sprain"]),
            (None, []),
            ("single injury", ["single injury"]),
            (42, []),
            (True, []),
            ([], []),
        ],
    )
    def test_coerces_to_list_of_strings(self, raw: Any, expected: list[str]) -> None:
        state = PhysicalState(injuries=raw)
        assert state.injuries == expected
        assert isinstance(state.injuries, list)

    def test_default_is_empty_list(self) -> None:
        state = PhysicalState()
        assert state.injuries == []
        assert isinstance(state.injuries, list)

    def test_non_string_fields_unchanged(self) -> None:
        state = PhysicalState(location="forest", inventory=["sword", "shield"])
        assert state.location == "forest"
        assert state.inventory == ["sword", "shield"]


# ── EmotionalState ────────────────────────────────────────────────


EMOTIONAL_STR_FIELDS: list[str] = ["desire", "fear"]


class TestEmotionalStateStrFieldsCoercion:
    @pytest.mark.parametrize("field", EMOTIONAL_STR_FIELDS)
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (None, ""),
            ("", ""),
            (42, "42"),
            (3.14, "3.14"),
            (True, "True"),
            ("  text  ", "text"),
            ("raw string", "raw string"),
            ({"key": "val"}, "key: val"),
            (["a", "b"], "a；b"),
        ],
    )
    def test_coerces_to_string(
        self, field: str, raw: Any, expected: str
    ) -> None:
        state = EmotionalState(**{field: raw})
        value = getattr(state, field)
        assert value == expected
        assert isinstance(value, str)

    @pytest.mark.parametrize("field", EMOTIONAL_STR_FIELDS)
    def test_default_is_empty_string(self, field: str) -> None:
        state = EmotionalState()
        assert getattr(state, field) == ""
        assert isinstance(getattr(state, field), str)

    def test_non_string_fields_unchanged(self) -> None:
        state = EmotionalState(
            primary_emotion="joy",
            secondary_emotion="anticipation",
            stability=0.8,
        )
        assert state.primary_emotion == "joy"
        assert state.secondary_emotion == "anticipation"
        assert state.stability == 0.8


# ── MotivationState ────────────────────────────────────────────────


class TestMotivationStateInternalConflictCoercion:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (None, ""),
            ("", ""),
            (42, "42"),
            (3.14, "3.14"),
            (True, "True"),
            ("  torn  ", "torn"),
            ("duty vs desire", "duty vs desire"),
            ({"between": "honor", "and": "love"}, "between: honor；and: love"),
            (["option A", "option B"], "option A；option B"),
        ],
    )
    def test_coerces_to_string(self, raw: Any, expected: str) -> None:
        state = MotivationState(internal_conflict=raw)
        assert state.internal_conflict == expected
        assert isinstance(state.internal_conflict, str)

    def test_default_is_empty_string(self) -> None:
        state = MotivationState()
        assert state.internal_conflict == ""
        assert isinstance(state.internal_conflict, str)

    def test_non_string_fields_unchanged(self) -> None:
        state = MotivationState(
            short_term_goal="survive",
            long_term_goal="rule the world",
            current_drive="ambition",
        )
        assert state.short_term_goal == "survive"
        assert state.long_term_goal == "rule the world"
        assert state.current_drive == "ambition"


# ── KnowledgeState ────────────────────────────────────────────────


KNOWLEDGE_LIST_FIELDS: list[str] = [
    "known_facts",
    "suspicions",
    "misbeliefs",
    "secrets_kept",
]


class TestKnowledgeStateListFieldsCoercion:
    @pytest.mark.parametrize("field", KNOWLEDGE_LIST_FIELDS)
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (["item A", "item B"], ["item A", "item B"]),
            (["single"], ["single"]),
            (None, []),
            ("single string", ["single string"]),
            ("", [""]),
            (42, []),
            ([], []),
        ],
    )
    def test_coerces_to_list_of_strings(
        self, field: str, raw: Any, expected: list[str]
    ) -> None:
        state = KnowledgeState(**{field: raw})
        value = getattr(state, field)
        assert value == expected
        assert isinstance(value, list)

    @pytest.mark.parametrize("field", KNOWLEDGE_LIST_FIELDS)
    def test_default_is_empty_list(self, field: str) -> None:
        state = KnowledgeState()
        value = getattr(state, field)
        assert value == []
        assert isinstance(value, list)


# ── RelationshipState ──────────────────────────────────────────

RELATIONSHIP_STRING_FIELDS: list[str] = [
    "public_status",
    "last_shift_event",
    "notes",
]


class TestRelationshipStateStringFieldsCoercion:

    @pytest.mark.parametrize("field", RELATIONSHIP_STRING_FIELDS)
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (None, ""),
            ("", ""),
            (42, "42"),
            (3.14, "3.14"),
            (True, "True"),
            ("  text  ", "text"),
            ("plain string", "plain string"),
            ({"key": "val"}, "key: val"),
            (["a", "b"], "a；b"),
        ],
    )
    def test_coerces_to_string(self, field: str, raw: Any, expected: str) -> None:
        state = RelationshipState(
            pair_id="A-B",
            characters=["A", "B"],
            **{field: raw},  # type: ignore[arg-type]
        )
        value = getattr(state, field)
        assert value == expected
        assert isinstance(value, str)

    @pytest.mark.parametrize("field", RELATIONSHIP_STRING_FIELDS)
    def test_default_is_empty_string(self, field: str) -> None:
        state = RelationshipState(pair_id="A-B", characters=["A", "B"])
        assert getattr(state, field) == ""
        assert isinstance(getattr(state, field), str)

    def test_non_target_fields_unchanged(self) -> None:
        state = RelationshipState(
            pair_id="A-B",
            characters=["A", "B"],
            trust=0.8,
            tension=0.3,
            dependency=0.1,
            last_updated_chapter=5,
        )
        assert state.pair_id == "A-B"
        assert state.characters == ["A", "B"]
        assert state.trust == 0.8
        assert state.tension == 0.3
        assert state.dependency == 0.1
        assert state.last_updated_chapter == 5


# ── PlotThreadState ─────────────────────────────────────────────

PLOT_THREAD_STRING_FIELDS: list[str] = [
    "summary",
    "blocking_condition",
]


class TestPlotThreadStateStringFieldsCoercion:

    @pytest.mark.parametrize("field", PLOT_THREAD_STRING_FIELDS)
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (None, ""),
            ("", ""),
            (42, "42"),
            (3.14, "3.14"),
            (True, "True"),
            ("  text  ", "text"),
            ("plain string", "plain string"),
            ({"key": "val"}, "key: val"),
            (["a", "b"], "a；b"),
        ],
    )
    def test_coerces_to_string(self, field: str, raw: Any, expected: str) -> None:
        state = PlotThreadState(
            thread_id="T1",
            title="Test Thread",
            **{field: raw},  # type: ignore[arg-type]
        )
        value = getattr(state, field)
        assert value == expected
        assert isinstance(value, str)

    @pytest.mark.parametrize("field", PLOT_THREAD_STRING_FIELDS)
    def test_default_is_empty_string(self, field: str) -> None:
        state = PlotThreadState(thread_id="T1", title="Test Thread")
        assert getattr(state, field) == ""
        assert isinstance(getattr(state, field), str)

    def test_non_target_fields_unchanged(self) -> None:
        state = PlotThreadState(
            thread_id="T1",
            title="Test Thread",
            status="active",
            owners=["A"],
            last_touched_chapter=3,
        )
        assert state.thread_id == "T1"
        assert state.title == "Test Thread"
        assert state.status == "active"
        assert state.owners == ["A"]
        assert state.last_touched_chapter == 3


# ── ChapterExitState ───────────────────────────────────────────

CHAPTER_EXIT_STRING_FIELDS: list[str] = [
    "time_marker",
    "location",
    "pov",
]


class TestChapterExitStateStringFieldsCoercion:

    @pytest.mark.parametrize("field", CHAPTER_EXIT_STRING_FIELDS)
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (None, ""),
            ("", ""),
            (42, "42"),
            (3.14, "3.14"),
            (True, "True"),
            ("  text  ", "text"),
            ("plain string", "plain string"),
            ({"key": "val"}, "key: val"),
            (["a", "b"], "a；b"),
        ],
    )
    def test_coerces_to_string(self, field: str, raw: Any, expected: str) -> None:
        state = ChapterExitState(
            chapter_number=1,
            **{field: raw},  # type: ignore[arg-type]
        )
        value = getattr(state, field)
        assert value == expected
        assert isinstance(value, str)

    @pytest.mark.parametrize("field", CHAPTER_EXIT_STRING_FIELDS)
    def test_default_is_empty_string(self, field: str) -> None:
        state = ChapterExitState(chapter_number=1)
        assert getattr(state, field) == ""
        assert isinstance(getattr(state, field), str)

    def test_non_target_fields_unchanged(self) -> None:
        state = ChapterExitState(
            chapter_number=1,
            active_goals=["goal1", "goal2"],
            open_questions=["q1"],
            must_carry_forward=["item"],
        )
        assert state.chapter_number == 1
        assert state.active_goals == ["goal1", "goal2"]
        assert state.open_questions == ["q1"]
        assert [item.text for item in state.must_carry_forward] == ["item"]
        assert [item.status for item in state.must_carry_forward] == ["open"]
