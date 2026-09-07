"""Tests for all Pydantic v2 schemas — validation, serialization, version tracking."""

from __future__ import annotations

import pytest

from novel_forge.core.schemas.base import VersionedSchema
from novel_forge.core.schemas.beats import Beat, StoryBeats
from novel_forge.core.schemas.bible import CharacterBible, CharacterProfile, StoryBible
from novel_forge.core.schemas.chapter import ChapterMeta, ChapterOutcome, ChapterResult
from novel_forge.core.schemas.draft import Draft, EditResult
from novel_forge.core.schemas.eval_schema import EvalReport, EvalScore
from novel_forge.core.schemas.spec import StorySpec
from novel_forge.story_kernel.schemas import StoryKernel


class TestVersionedSchema:
    def test_schema_version_default(self) -> None:
        s = VersionedSchema()
        assert s.schema_version == "2.0"
        assert s.created_at is not None

    def test_schema_version_custom(self) -> None:
        s = VersionedSchema(schema_version="2.0")
        assert s.schema_version == "2.0"


class TestStorySpec:
    def test_valid_spec(self, sample_spec: StorySpec) -> None:
        assert sample_spec.theme == "勇气与牺牲"
        assert sample_spec.genre == "fantasy"
        assert sample_spec.schema_version == "2.0"

    def test_blank_theme_rejected(self) -> None:
        with pytest.raises(ValueError, match="theme must not be blank"):
            StorySpec(theme="   ")

    def test_length_bounds(self) -> None:
        with pytest.raises(ValueError):
            StorySpec(theme="test", length_target=100)  # below 500

    def test_serialization_roundtrip(self, sample_spec: StorySpec) -> None:
        data = sample_spec.model_dump(mode="json")
        restored = StorySpec.model_validate(data)
        assert restored.theme == sample_spec.theme


class TestBeats:
    def test_valid_beat(self) -> None:
        b = Beat(sequence=1, summary="Something happens", tension_level=5)
        assert b.sequence == 1

    def test_tension_bounds(self) -> None:
        with pytest.raises(ValueError):
            Beat(sequence=1, summary="x", tension_level=11)

    def test_story_beats_min_length(self) -> None:
        with pytest.raises(ValueError):
            StoryBeats(beats=[])


class TestDraft:
    def test_auto_word_count(self) -> None:
        d = Draft(text="这是测试文本")
        assert d.word_count == 6  # Chinese characters, spaces removed

    def test_edit_result(self) -> None:
        e = EditResult(
            revised_text="修改后的文本",
            edit_notes=["修正了一个错误"],
            iteration=1,
        )
        assert e.iteration == 1
        assert e.word_count == 6


class TestEvalReport:
    def test_compute_overall(self) -> None:
        report = EvalReport(
            scores=[
                EvalScore(dimension="coherence", score=8.0),
                EvalScore(dimension="style", score=7.0),
                EvalScore(dimension="engagement", score=6.0),
            ],
            threshold=6.0,
        )
        report.compute_overall()
        assert report.overall_score == 7.0
        assert report.passed is True

    def test_below_threshold(self) -> None:
        report = EvalReport(
            scores=[EvalScore(dimension="coherence", score=3.0)],
            threshold=6.0,
        )
        report.compute_overall()
        assert report.passed is False


class TestBible:
    def test_character_profile(self) -> None:
        cp = CharacterProfile(
            name="林远",
            role="protagonist",
            age="25",
            personality="沉静、执着",
        )
        assert cp.name == "林远"

    def test_character_profile_preserves_llm_extra_fields_in_notes(self) -> None:
        bible = CharacterBible.model_validate(
            {
                "characters": [
                    {
                        "name": "朱批录",
                        "role": "protagonist",
                        "abilities": "以算学天赋为核心，看见人命的价码。",
                        "goals": ["追查账本真相", "保住自身清白"],
                        "notes": "原始备注",
                    }
                ]
            }
        )

        profile = bible.characters[0]
        notes = profile.notes
        assert profile.abilities == "以算学天赋为核心，看见人命的价码。"
        assert "原始备注" in notes
        assert 'goals: ["追查账本真相","保住自身清白"]' in notes

    def test_character_profile_flattens_structured_text_fields(self) -> None:
        profile = CharacterProfile.model_validate(
            {
                "name": "赵启明",
                "role": "supporting",
                "abilities": {
                    "professional": "商业谈判与票号经营",
                    "network": ["商会人脉", "海外账簿"],
                },
                "appearance": {
                    "general": "民国时期商人装束",
                    "detail": "袖口有墨痕",
                },
                "personality": {
                    "traits": ["圆滑", "谨慎"],
                    "fear": "家业崩塌",
                },
            }
        )

        assert profile.abilities == "professional: 商业谈判与票号经营；network: 商会人脉；海外账簿"
        assert profile.appearance == "general: 民国时期商人装束；detail: 袖口有墨痕"
        assert profile.personality == "traits: 圆滑；谨慎；fear: 家业崩塌"

    def test_character_profile_accepts_arc_goal_alias(self) -> None:
        profile = CharacterProfile.model_validate(
            {
                "name": "沈念卿",
                "role": "protagonist",
                "arc_goal": "从恐惧承诺到主动选择共赴终局",
            }
        )

        assert profile.arc == "从恐惧承诺到主动选择共赴终局"
        assert profile.arc_goal == profile.arc
        assert "arc_goal" not in profile.notes

    def test_character_profile_keeps_alias_repair_with_extra_fields(self) -> None:
        profile = CharacterProfile.model_validate(
            {
                "Name": "林远",
                "role": "protagonist",
                "relationships_2": {"老守夜人": "引路人"},
                "identity": "失忆的研究者",
            }
        )

        assert profile.name == "林远"
        assert profile.relationships == {"老守夜人": "引路人"}
        assert "identity: 失忆的研究者" in profile.notes

    def test_character_bible_min_one(self) -> None:
        with pytest.raises(ValueError):
            CharacterBible(characters=[])

    def test_story_bible(self) -> None:
        sb = StoryBible(premise="一个关于时间的故事")
        assert sb.premise


class TestStoryKernel:
    def test_empty_state(self) -> None:
        s = StoryKernel(project_id="test")
        assert s.current_chapter == 0
        assert s.entities == []

    def test_full_state(self, sample_canon_state: StoryKernel) -> None:
        assert sample_canon_state.current_chapter == 1
        entity_names = [e.name for e in sample_canon_state.entities]
        assert "林远" in entity_names
        assert len(sample_canon_state.timeline) == 1
        assert len(sample_canon_state.promise_ledger) == 1


class TestChapterOutcome:
    def test_delta(self, sample_canon_delta: ChapterOutcome) -> None:
        assert sample_canon_delta.source_chapter == 2
        assert "林远" in sample_canon_delta.character_updates
        assert len(sample_canon_delta.new_events) == 1


class TestChapterResult:
    def test_chapter_meta(self) -> None:
        meta = ChapterMeta(chapter_number=1, word_count=3000)
        result = ChapterResult(meta=meta, text="chapter content")
        assert result.meta.chapter_number == 1
