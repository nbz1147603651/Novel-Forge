"""Project-level audio creative identity and deterministic cue restraint."""

from __future__ import annotations

from novel_forge.common.constants import TaskType
from novel_forge.persistence.models import ProjectLayout
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.tts.creative_direction import (
    apply_audio_creative_bible,
    build_audio_creative_bible,
    load_or_create_audio_creative_bible,
)
from novel_forge.tts.schemas import (
    BGMTiming,
    DubbingScript,
    SFXCue,
    SoundscapeCue,
)


def test_creative_bible_is_project_scoped_and_preserves_locked_identity(tmp_path) -> None:
    layout = ProjectLayout(tmp_path / "novel")
    first = load_or_create_audio_creative_bible(
        layout=layout,
        project_id="novel",
        story_context={
            "title": "雾城",
            "genre": "悬疑",
            "tone": "冷峻",
            "themes": ["记忆", "背叛"],
        },
        scene_intents=[
            {
                "location": "旧火车站",
                "time_marker": "雨夜",
                "atmosphere": "空旷",
                "sensory_focus": "湿润的铁轨与远处轰鸣",
            }
        ],
        outline={
            "chapters": [
                {
                    "setting": "钟楼",
                    "time_anchor": "拂晓",
                    "title": "鸣钟",
                }
            ]
        },
    )

    second = load_or_create_audio_creative_bible(
        layout=layout,
        project_id="novel",
        story_context={"title": "不应覆盖的新标题", "genre": "喜剧"},
    )

    assert layout.tts_audio_creative_bible_path.is_file()
    assert first == second
    assert first.title == "雾城"
    assert first.location_sound_signatures["旧火车站"] == [
        "雨夜",
        "空旷",
        "湿润的铁轨与远处轰鸣",
    ]
    assert set(first.motif_directions) == {"记忆", "背叛"}
    assert first.location_sound_signatures["钟楼"] == ["拂晓", "鸣钟"]


def test_creative_bible_normalizes_compound_outline_settings_and_bounds_history() -> None:
    bible = build_audio_creative_bible(
        project_id="novel",
        story_context={"genre": "悬疑"},
        outline={
            "chapters": [
                {
                    "setting": "醒梦事务所、糖水铺，时间从清晨至深夜",
                    "time_anchor": f"第{index}日",
                    "emotional_plan": f"情绪{index}",
                    "title": f"章节{index}",
                }
                for index in range(12)
            ]
        },
    )

    assert set(bible.location_sound_signatures) == {"醒梦事务所", "糖水铺"}
    assert all(len(descriptors) <= 8 for descriptors in bible.location_sound_signatures.values())


def test_creative_bible_caps_density_and_keeps_dialogue_foreground() -> None:
    bible = build_audio_creative_bible(
        project_id="novel",
        story_context={"genre": "悬疑", "tone": "克制"},
    )
    script = DubbingScript(
        chapter_number=1,
        bgm_suggestions=[
            BGMTiming(track_name="日常", intensity=0.2, volume=0.8, ducking_db=2),
            BGMTiming(
                track_name="高潮",
                narrative_role="climax",
                intensity=0.9,
                volume=0.8,
                ducking_db=2,
                fade_in_ms=100,
                fade_out_ms=100,
            ),
        ],
        soundscapes=[
            SoundscapeCue(name="拥挤人群", density=0.9, volume=0.7),
            SoundscapeCue(name="稳定雨底", density=0.2, volume=0.7, ducking_db=1),
        ],
        sfx_cues=[
            SFXCue(
                effect_name=f"音效{priority}",
                narrative_priority=priority,
                volume=0.9,
                allow_dialogue_overlap=True,
            )
            for priority in (20, 40, 80, 95, 100)
        ],
    )

    constrained = apply_audio_creative_bible(script, bible, scene_count_hint=1)

    assert [item.track_name for item in constrained.bgm_suggestions] == ["高潮"]
    assert constrained.bgm_suggestions[0].volume == bible.mix_policy.max_bgm_volume
    assert constrained.bgm_suggestions[0].ducking_db == bible.mix_policy.min_bgm_ducking_db
    assert constrained.bgm_suggestions[0].fade_in_ms >= 1000
    assert [item.name for item in constrained.soundscapes] == ["稳定雨底"]
    assert [item.narrative_priority for item in constrained.sfx_cues] == [80, 95, 100]
    assert constrained.sfx_cues[0].allow_dialogue_overlap is False
    assert constrained.sfx_cues[-1].allow_dialogue_overlap is True
    assert constrained.sfx_cues[-1].volume == bible.mix_policy.max_dialogue_overlap_sfx_volume
    evidence = constrained.metadata["audio_creative_bible"]
    assert evidence["original_counts"] == {"bgm": 2, "soundscape": 2, "sfx": 5}
    assert evidence["applied_counts"] == {"bgm": 1, "soundscape": 1, "sfx": 3}


def test_active_prompt_pack_consumes_project_audio_creative_bible() -> None:
    bible = build_audio_creative_bible(
        project_id="novel",
        story_context={"title": "雾城", "genre": "悬疑"},
    )

    rendered = PromptBuilder().render(
        TaskType.TTS_GENERATE_DUBBING_SCRIPT,
        {
            "stage_cards": {
                "chapter_text": "雨落在空旷的月台上。",
                "voice_team": {
                    "entries": [
                        {
                            "character_id": "narrator",
                            "character_name": "旁白",
                            "voice_id": "narrator-main",
                        }
                    ]
                },
                "audio_creative_bible": bible.model_dump(mode="json"),
            }
        },
    )

    assert "项目声音创作圣经" in rendered
    assert "全书共享的声音身份" in rendered
    assert "每章重新发明" in rendered


def test_active_prompt_pack_marks_adjacent_batch_context_as_reference_only() -> None:
    rendered = PromptBuilder().render(
        TaskType.TTS_GENERATE_DUBBING_SCRIPT,
        {
            "stage_cards": {
                "chapter_text": "本批正文。",
                "voice_team": {"entries": []},
                "generation_batch": {
                    "batch_index": 1,
                    "batch_count": 3,
                    "previous_context": "前一批末尾。",
                    "next_context": "后一批开头。",
                },
            }
        },
    )

    assert "第 2/3 个有界批次" in rendered
    assert "不得把前后文重复写入 `segments`" in rendered
    assert "前一批末尾" in rendered
    assert "后一批开头" in rendered
