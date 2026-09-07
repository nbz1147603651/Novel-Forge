from __future__ import annotations

from novel_forge.common.constants import TaskType
from novel_forge.core.format_contracts import get_task_format_contract
from novel_forge.core.schemas.bible import CharacterProfile, StoryBible


def test_character_profile_owns_screen_and_voice_identity_upstream() -> None:
    profile = CharacterProfile.model_validate(
        {
            "name": "沈鹿溪",
            "appearance": "短黑发，左眉浅疤，深绿旧风衣",
            "voice": "低声短句，情绪激烈时反而放慢",
            "visual_identity": {
                "facial_anchors": ["左眉浅疤", "窄长眼"],
                "silhouette": "瘦削高挑，窄长风衣轮廓",
                "body_language": "收肩，观察时下颌微抬",
                "costume_palette": ["深绿", "炭黑"],
                "signature_props": ["旧录音笔"],
                "continuity_rules": ["左眉浅疤始终可辨"],
                "forbidden_drift": ["禁止随机改变发长与年龄感"],
            },
            "tts_voice_hints": {
                "timbre": "低沉清晰，带轻微气声",
                "register": "中低音",
                "cadence": "偏慢，句尾轻收",
                "accent": "普通话",
                "emotion_range": ["克制", "压抑后爆发"],
                "pronunciation_notes": ["沈读 shen 三声"],
            },
        }
    )

    assert profile.visual_identity.facial_anchors == ["左眉浅疤", "窄长眼"]
    assert profile.visual_identity.signature_props == ["旧录音笔"]
    assert profile.tts_voice_hints is not None
    assert profile.tts_voice_hints["register"] == "中低音"


def test_story_bible_owns_reusable_location_and_sound_environment() -> None:
    bible = StoryBible.model_validate(
        {
            "title": "长夜电台",
            "premise": "失声主持人重返旧电台追查事故录音。",
            "geography": "沿海旧城区与城际铁路交界。",
            "locations": [
                {
                    "location_id": "loc-radio",
                    "name": "旧电台直播间",
                    "dramatic_function": "封闭空间迫使主角重新发声",
                    "spatial_layout": "控制台朝东，南墙是 ON AIR 灯",
                    "materials": ["吸音棉", "磨损木桌"],
                    "practical_lights": ["红色 ON AIR 灯"],
                    "weather_states": ["连续雨夜"],
                    "recurring_props": ["开盘机"],
                    "ambient_sound": ["雨打窗", "设备电流"],
                    "continuity_rules": ["控制台朝向与灯位不可漂移"],
                }
            ],
            "audio_aesthetic": "近讲人声，保留雨夜底噪，音乐只在转折后进入。",
        }
    )

    assert bible.locations[0].location_id == "loc-radio"
    assert bible.locations[0].ambient_sound == ["雨打窗", "设备电流"]
    assert "近讲人声" in bible.audio_aesthetic


def test_character_generation_contract_exposes_cross_media_fields() -> None:
    contract = get_task_format_contract(TaskType.INIT_CHARACTER_PROFILE_BATCH)

    assert contract is not None
    profile_schema = contract.json_schema["properties"]["character_profiles"]["items"]
    properties = profile_schema["properties"]
    assert "visual_identity" in properties
    assert "tts_voice_hints" in properties
    assert properties["visual_identity"]["additionalProperties"] is False
