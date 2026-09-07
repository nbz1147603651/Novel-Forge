"""Unit tests for BackstoryRevealSpec and StorySpec.backstory_reveals.

The schema is added in M4 — see docs/ai_flavor_quality.md.

Goals:
1. Allow projects to declare structured backstory windows (topic, deadline
   chapter, min word count, reveal mode, triggers).
2. Backward compatible: existing spec.json files without these fields still
   validate (default_factory=list / bool False).
3. Strict enough to catch schema drift at parse time.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from novel_forge.core.schemas.spec import BackstoryRevealSpec, StorySpec

# ---------------------------------------------------------------------------
# BackstoryRevealSpec — basic shape and validation
# ---------------------------------------------------------------------------


class TestBackstoryRevealSpecShape:
    def test_minimum_required_fields(self) -> None:
        """Only `topic` and `required_first_appearance` are strictly required."""
        spec = BackstoryRevealSpec(
            topic="主角网络暴力前史",
            required_first_appearance=5,
        )
        assert spec.topic == "主角网络暴力前史"
        assert spec.required_first_appearance == 5
        # Optional fields have sane defaults
        assert spec.min_word_count == 200
        assert spec.reveal_mode == "flashback"
        assert spec.triggers == []

    def test_full_construction(self) -> None:
        spec = BackstoryRevealSpec(
            topic="陈屿大学风能课题",
            required_first_appearance=8,
            min_word_count=300,
            reveal_mode="dialogue_snippet",
            triggers=["新能源", "导师", "论文"],
        )
        assert spec.min_word_count == 300
        assert spec.reveal_mode == "dialogue_snippet"
        assert len(spec.triggers) == 3

    def test_required_first_appearance_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            BackstoryRevealSpec(topic="x", required_first_appearance=0)

    def test_min_word_count_cannot_be_negative(self) -> None:
        with pytest.raises(ValidationError):
            BackstoryRevealSpec(
                topic="x", required_first_appearance=1, min_word_count=-1
            )

    def test_reveal_mode_is_enum_validated(self) -> None:
        with pytest.raises(ValidationError):
            BackstoryRevealSpec(
                topic="x",
                required_first_appearance=1,
                reveal_mode="narration",  # not a valid mode
            )

    def test_all_reveal_modes_accepted(self) -> None:
        """flashback / third_party_expose / dialogue_snippet / object_trigger."""
        for mode in (
            "flashback",
            "third_party_expose",
            "dialogue_snippet",
            "object_trigger",
        ):
            spec = BackstoryRevealSpec(
                topic="x",
                required_first_appearance=1,
                reveal_mode=mode,
            )
            assert spec.reveal_mode == mode


# ---------------------------------------------------------------------------
# StorySpec.backstory_reveals — backward compatibility
# ---------------------------------------------------------------------------


class TestStorySpecBackstoryReveals:
    def test_spec_without_field_validates_with_empty_list(self) -> None:
        """An old spec.json without backstory_reveals must still validate."""
        spec = StorySpec(theme="test theme")
        assert spec.backstory_reveals == []

    def test_spec_with_empty_list_validates(self) -> None:
        spec = StorySpec(theme="test theme", backstory_reveals=[])
        assert spec.backstory_reveals == []

    def test_spec_with_populated_list_validates(self) -> None:
        spec = StorySpec(
            theme="test theme",
            backstory_reveals=[
                BackstoryRevealSpec(
                    topic="主角网络暴力前史", required_first_appearance=5
                ),
                BackstoryRevealSpec(
                    topic="陈屿风能课题",
                    required_first_appearance=8,
                    min_word_count=300,
                ),
            ],
        )
        assert len(spec.backstory_reveals) == 2
        assert spec.backstory_reveals[0].topic == "主角网络暴力前史"
        assert spec.backstory_reveals[1].min_word_count == 300


# ---------------------------------------------------------------------------
# StorySpec.character_silence — silence-protagonist flag
# ---------------------------------------------------------------------------


class TestStorySpecCharacterSilence:
    def test_default_is_false(self) -> None:
        spec = StorySpec(theme="x")
        assert spec.character_silence is False

    def test_can_be_enabled(self) -> None:
        spec = StorySpec(theme="x", character_silence=True)
        assert spec.character_silence is True


# ---------------------------------------------------------------------------
# From-dict loading (JSON round-trip)
# ---------------------------------------------------------------------------


class TestSpecJSONRoundTrip:
    def test_loads_old_spec_json_without_new_fields(self) -> None:
        """Existing project spec.json without backstory_reveals / character_silence
        must still parse cleanly."""
        old_payload = {
            "title": "示例",
            "genre": "life_healing",
            "theme": "被听见不等于被审判",
            "tone": "warm",
            "length_target": 108000,
            "extra_instructions": "言情要素自然融入...",
        }
        spec = StorySpec.model_validate(old_payload)
        assert spec.title == "示例"
        assert spec.backstory_reveals == []
        assert spec.character_silence is False

    def test_loads_new_spec_json_with_backstory_windows(self) -> None:
        new_payload = {
            "title": "示例",
            "genre": "life_healing",
            "theme": "被听见不等于被审判",
            "tone": "warm",
            "length_target": 108000,
            "character_silence": True,
            "backstory_reveals": [
                {
                    "topic": "主角网络暴力前史",
                    "required_first_appearance": 5,
                    "min_word_count": 300,
                    "reveal_mode": "flashback",
                    "triggers": ["声音", "录音", "麦克风"],
                },
            ],
        }
        spec = StorySpec.model_validate(new_payload)
        assert spec.character_silence is True
        assert len(spec.backstory_reveals) == 1
        assert spec.backstory_reveals[0].topic == "主角网络暴力前史"


# ---------------------------------------------------------------------------
# _correct_fields membership (extra="forbid" protection)
# ---------------------------------------------------------------------------


class TestCorrectFieldsMembership:
    def test_backstory_reveals_in_correct_fields(self) -> None:
        """Required: the new field must be in StorySpec._correct_fields,
        otherwise extra='forbid' will reject it on validation."""
        assert "backstory_reveals" in StorySpec._correct_fields

    def test_character_silence_in_correct_fields(self) -> None:
        assert "character_silence" in StorySpec._correct_fields