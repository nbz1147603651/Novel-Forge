from __future__ import annotations

import json
from pathlib import Path

import opentimelineio as otio  # type: ignore[import-untyped]

from novel_forge.film.pipeline import FilmProductionPipeline
from novel_forge.film.schemas import (
    FilmStage,
    FilmStageStatus,
    ProductionMode,
    TimelineClip,
    TimelineTrack,
)
from novel_forge.persistence.models import ProjectLayout


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _project(tmp_path: Path) -> tuple[ProjectLayout, FilmProductionPipeline]:
    layout = ProjectLayout(tmp_path / "长夜电台")
    layout.ensure_dirs()
    _write_json(
        layout.spec_path,
        {
            "title": "长夜电台",
            "theme": "失声的主持人通过深夜来电重新面对真相",
            "genre": "悬疑剧情",
            "tone": "克制、潮湿、温暖",
            "language": "zh",
            "audio_aesthetic_hint": "近讲人声、雨夜底噪、模拟电台压缩",
        },
    )
    _write_json(
        layout.characters_path,
        {
            "characters": [
                {
                    "character_id": "char-shen",
                    "name": "沈鹿溪",
                    "role": "protagonist",
                    "age": "29",
                    "gender": "女",
                    "appearance": "短黑发，左眉浅疤，瘦削，常穿深绿旧风衣",
                    "personality": "克制、敏锐、回避公众注视",
                    "arc": "从躲避声音到公开说出真相",
                    "voice": "低声、短句、情绪激烈时反而放慢",
                    "tts_voice_hints": {
                        "register": "中低音",
                        "cadence": "偏慢，句尾轻收",
                    },
                    "visual_identity": {
                        "facial_anchors": ["左眉浅疤", "窄长眼", "短黑发"],
                        "silhouette": "瘦削高挑，旧风衣形成窄长轮廓",
                        "body_language": "收肩，观察时下颌微抬",
                        "costume_palette": ["深绿", "炭黑"],
                        "signature_props": ["旧银色录音笔"],
                        "continuity_rules": ["左眉浅疤始终可辨"],
                        "forbidden_drift": ["禁止变成长发或暖色服装"],
                    },
                }
            ]
        },
    )
    _write_json(
        layout.bible_path,
        {
            "locations": [
                {
                    "id": "loc-radio",
                    "name": "旧电台直播间",
                    "layout": "控制台朝东，隔音窗后是导播间，南墙一盏红色 ON AIR 灯",
                    "materials": ["吸音棉", "磨损木桌", "雾面玻璃"],
                    "ambient_sound": ["雨打窗", "设备电流", "远处列车"],
                    "continuity_rules": ["ON AIR 灯固定在南墙，控制台始终朝东"],
                }
            ],
            "world_rules": ["故事发生在连续三个雨夜"],
            "themes": ["声音与证词", "观看与被观看"],
        },
    )
    _write_json(
        layout.outline_path,
        {
            "chapters": [
                {
                    "chapter_number": 1,
                    "scenes": [
                        {
                            "id": "chapter-1-scene-1",
                            "title": "停播后的第一通电话",
                            "location": "旧电台直播间",
                            "time": "雨夜",
                            "objective": "沈鹿溪确认神秘来电者身份",
                            "conflict": "来电者播放了她从未公开的事故录音",
                            "turn": "录音中出现她自己的声音",
                            "visual_hook": "ON AIR 红灯在无人触碰时亮起",
                            "sound_hook": "雨声中混入旧磁带倒转声",
                            "summary": "她返回停播三年的直播间，接起一通没有号码的电话。",
                        }
                    ],
                }
            ]
        },
    )
    _write_json(
        layout.tts_voice_team_path,
        {
            "entries": [
                {
                    "character_id": "char-shen",
                    "character_name": "沈鹿溪",
                    "provider": "minimax",
                    "voice_id": "voice-shen-approved",
                    "model_id": "speech-2.8-hd",
                    "voice_description": "低沉但清晰，带轻微气声",
                }
            ]
        },
    )
    pipeline = FilmProductionPipeline(project_id="长夜电台", layout=layout)
    return layout, pipeline


def test_bootstrap_builds_shared_production_bible_from_novel_and_voice(tmp_path: Path) -> None:
    layout, pipeline = _project(tmp_path)

    state = pipeline.get_or_bootstrap(mode=ProductionMode.COLLABORATIVE)

    character = state.production_bible.characters[0]
    assert character.screen_identity.facial_anchors
    assert character.screen_identity.facial_anchors[0] == "左眉浅疤"
    assert character.screen_identity.signature_props == ["旧银色录音笔"]
    assert character.voice_performance.voice_id == "voice-shen-approved"
    assert character.voice_performance.model_id == "speech-2.8-hd"
    assert state.production_bible.locations[0].ambient_sound == ["雨打窗", "设备电流", "远处列车"]
    assert "控制台始终朝东" in state.production_bible.locations[0].continuity_rules[0]
    assert state.screenplay.scenes[0].source_chapter == 1
    assert len(state.shots) == 4
    asset_types = {asset.asset_type for asset in state.visual_assets}
    assert {"character", "location"} <= asset_types
    # Batch 6 (G4/G2): variant sheets + storyboard contact sheet are projected
    assert {"expression_sheet", "pose_sheet", "scene_variants"} <= asset_types
    assert "storyboard_contact_sheet" in asset_types
    assert (layout.root / "production_bible.json").exists()
    assert (layout.root / "film" / "studio_state.json").exists()


async def test_autonomous_mode_stops_before_unapproved_paid_shot_generation(tmp_path: Path) -> None:
    _layout, pipeline = _project(tmp_path)
    pipeline.get_or_bootstrap(mode=ProductionMode.AUTONOMOUS)

    state = await pipeline.advance(
        mode=ProductionMode.AUTONOMOUS,
        use_ai=False,
        run_until=FilmStage.DELIVERY,
    )

    assert state.current_stage == FilmStage.SHOT_PRODUCTION
    stage = next(item for item in state.stages if item.stage == FilmStage.SHOT_PRODUCTION)
    assert stage.status == FilmStageStatus.BLOCKED
    assert "付费镜头生成" in stage.warnings[0]
    assert all(item.status == FilmStageStatus.COMPLETED for item in state.stages[:4])


def test_otio_export_preserves_film_lineage(tmp_path: Path) -> None:
    _layout, pipeline = _project(tmp_path)
    state = pipeline.get_or_bootstrap()

    path = pipeline.store.export_otio(state)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["OTIO_SCHEMA"] == "Timeline.1"
    assert payload["metadata"]["novel_forge"]["production_bible"] == "../../production_bible.json"
    assert [track["kind"] for track in payload["tracks"]["children"]] == [
        "Video",
        "Audio",
        "Audio",
        "Audio",
    ]


def test_otio_export_roundtrips_through_upstream_library(tmp_path: Path) -> None:
    _layout, pipeline = _project(tmp_path)
    state = pipeline.get_or_bootstrap()
    track = TimelineTrack(
        track_id="v1",
        name="画面",
        kind="video",
        clips=[
            TimelineClip(
                clip_id="c1",
                name="SC01",
                media_kind="video",
                source_url="file:///shot.mp4",
                start_s=0.0,
                duration_s=2.5,
                source_start_s=0.5,
                metadata={"shot": "s1"},
            )
        ],
    )
    state = state.model_copy(
        update={
            "timeline": state.timeline.model_copy(update={"tracks": [track]}),
        }
    )

    path = pipeline.store.export_otio(state)
    loaded = otio.adapters.read_from_file(str(path))

    tracks = list(loaded.tracks)
    assert len(tracks) == 1
    clip = tracks[0][0]
    assert clip.name == "SC01"
    assert abs(clip.duration().to_seconds() - 2.5) < 1e-6
    assert abs(clip.source_range.start_time.to_seconds() - 0.5) < 1e-6
    assert clip.metadata.get("shot") == "s1"
