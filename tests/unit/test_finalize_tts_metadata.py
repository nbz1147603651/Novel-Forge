"""Unit tests for the extracted TTS metadata mapping.

``extract_tts_metadata`` is the deterministic bridge between chapter artifacts
(ChapterPlan, EditorialContract, ReadingPowerReport) and the
:class:`~novel_forge.tts.schemas.ChapterTTSMetadata` consumed by the TTS
pipeline.  These tests pin its mapping rules and the empty-input short-circuit.
"""

from __future__ import annotations

from types import SimpleNamespace

from novel_forge.pipeline.long.finalize_tts_metadata import extract_tts_metadata
from novel_forge.tts.schemas import EmotionTag


def _intent(scene_id: str, beat: str, characters: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        scene_id=scene_id,
        emotional_beat=beat,
        required_characters=characters or [],
    )


def test_returns_none_when_all_inputs_empty() -> None:
    """No meaningful data anywhere -> None (TTS stage falls back to text inference)."""
    assert extract_tts_metadata(plan=None, editorial_contract=None, reading_power_report=None) is None
    assert extract_tts_metadata(plan=SimpleNamespace(), editorial_contract=None, reading_power_report=None) is None


def test_source_bound_empty_snapshot_prevents_stale_metadata_reuse() -> None:
    """A new sparse finalization still carries the current chapter identity."""
    meta = extract_tts_metadata(
        plan=None,
        editorial_contract=None,
        reading_power_report=None,
        chapter_number=7,
        source_text="第七章终稿",
    )

    assert meta is not None
    assert meta["chapter_number"] == 7
    assert meta["source_text_hash"]
    assert meta["scene_emotion_map"] == []


def test_scene_emotion_map_and_character_trajectories() -> None:
    """scene_intents map to scene_emotion_map; required_characters project onto trajectories."""
    plan = SimpleNamespace(
        scene_intents=[
            _intent("s1", "紧张的对峙", characters=["林凡", "苏雪"]),
            _intent("s2", "温柔的告别", characters=["林凡"]),
        ],
        cross_scene_intent={},
        emotional_arc="",
    )

    meta = extract_tts_metadata(plan=plan, editorial_contract=None, reading_power_report=None)

    assert meta is not None
    emap = meta["scene_emotion_map"]
    assert [e["scene_id"] for e in emap] == ["s1", "s2"]
    # keyword "紧张" -> ANXIOUS, "温柔" -> TENDER
    assert emap[0]["dominant_emotion"] == EmotionTag.ANXIOUS.value
    assert emap[1]["dominant_emotion"] == EmotionTag.TENDER.value
    # high-intensity keyword absent -> default 0.5
    assert emap[0]["emotion_intensity"] == 0.5

    traj = meta["character_emotion_trajectories"]
    assert traj["林凡"][0]["scene_id"] == "s1"
    assert traj["苏雪"][0]["emotion"] == EmotionTag.ANXIOUS.value


def test_high_intensity_keyword_raises_intensity() -> None:
    plan = SimpleNamespace(
        scene_intents=[_intent("s1", "激烈爆发")],
        cross_scene_intent={},
        emotional_arc="",
    )
    meta = extract_tts_metadata(plan=plan, editorial_contract=None, reading_power_report=None)
    assert meta is not None
    assert meta["scene_emotion_map"][0]["emotion_intensity"] == 0.7


def test_expression_channel_constraints_from_editorial_contract() -> None:
    plan = SimpleNamespace(scene_intents=[], cross_scene_intent={}, emotional_arc="")
    contract = SimpleNamespace(expression_channel_budget={"monologue": 3, "dialogue": 8})

    meta = extract_tts_metadata(plan=plan, editorial_contract=contract, reading_power_report=None)

    assert meta is not None
    assert meta["expression_channel_constraints"] == {"monologue": 3, "dialogue": 8}


def test_pacing_curve_mapped_per_scene() -> None:
    plan = SimpleNamespace(
        scene_intents=[_intent("s1", "平静"), _intent("s2", "激动")],
        cross_scene_intent={"pacing_curve": [2, 5]},
        emotional_arc="",
    )
    meta = extract_tts_metadata(plan=plan, editorial_contract=None, reading_power_report=None)

    assert meta is not None
    pacing = meta["pacing_annotations"]
    # value 2 -> "decelerating", value 5 -> "fast"
    assert pacing[0]["text_range"] == "s1"
    assert pacing[0]["pacing"] == "decelerating"
    assert pacing[1]["text_range"] == "s2"
    assert pacing[1]["pacing"] == "fast"


def test_reading_power_report_provides_chapter_pacing_fallback() -> None:
    plan = SimpleNamespace(scene_intents=[], cross_scene_intent={}, emotional_arc="")
    report = SimpleNamespace(overall_score=8.5)

    meta = extract_tts_metadata(plan=plan, editorial_contract=None, reading_power_report=report)

    assert meta is not None
    chapter_pacing = [p for p in meta["pacing_annotations"] if p["text_range"] == "chapter"]
    assert chapter_pacing and chapter_pacing[0]["pacing"] == "fast"
    assert "8.5" in chapter_pacing[0]["reason"]


def test_narration_tone_progression_tracks_scene_and_arc() -> None:
    plan = SimpleNamespace(
        scene_intents=[_intent("s1", "平静"), _intent("s2", "愤怒")],
        cross_scene_intent={},
        emotional_arc="由平静走向冲突",
    )
    meta = extract_tts_metadata(plan=plan, editorial_contract=None, reading_power_report=None)

    assert meta is not None
    progression = meta["narration_tone_progression"]
    # one scene-to-scene shift + one chapter-arc entry
    assert len(progression) == 2
    assert progression[0]["from_tone"] == "平静"
    assert progression[0]["to_tone"] == "愤怒"
    assert progression[1]["position"] == "chapter"
    assert progression[1]["to_tone"] == "由平静走向冲突"
