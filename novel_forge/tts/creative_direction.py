"""Project-level sound identity and deterministic cue restraint."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable, TypeVar

from novel_forge.persistence.filesystem import atomic_write_json
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.schemas import (
    AudioCreativeBible,
    BGMTiming,
    DubbingScript,
    SegmentType,
    SFXCue,
    SoundscapeCue,
)

_CueT = TypeVar("_CueT", BGMTiming, SFXCue, SoundscapeCue)
_MAX_LOCATION_SIGNATURE_DESCRIPTORS = 8

# Markers that indicate a descriptor is actually serialized structured data
# (character state deltas, schema objects) rather than a genuine sound descriptor.
_STRUCTURED_DATA_MARKERS = (
    "schema_version",
    "created_at",
    "subject_entity_id",
    "entry_state",
    "pressure_source",
    "relationship_choice",
    "turning_emotion",
    "exit_aftertaste",
    "expression_channels",
)


def _is_valid_sound_descriptor(descriptor: str) -> bool:
    """Reject descriptors that are serialized JSON / structured data pollution.

    The audio_creative_bible's location_sound_signatures must only contain
    human-readable sound/environment descriptions.  Upstream data (scene
    intents, outline chapters) sometimes carries raw character-state JSON
    objects that were incorrectly propagated as 'atmosphere' or 'sensory'
    fields.  This filter catches and discards them.
    """
    if not descriptor:
        return False
    # Reject if it looks like a Python/JSON dict
    if descriptor.startswith("{") and descriptor.endswith("}"):
        return False
    # Reject if it contains structured data field markers
    lower = descriptor.lower()
    if any(marker in lower for marker in _STRUCTURED_DATA_MARKERS):
        return False
    # Reject excessively long descriptors (> 80 chars) that are likely prose
    # dumps rather than concise sound signatures
    if len(descriptor) > 80:
        return False
    return True


def _text(value: Any) -> str:
    return str(value or "").strip()


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        values = [part.strip() for part in value.replace("；", ",").split(",")]
    elif isinstance(value, (list, tuple, set)):
        values = [_text(item) for item in value]
    else:
        values = []
    return list(dict.fromkeys(item for item in values if item))


def _location_list(value: Any) -> list[str]:
    """Return reusable place identities instead of chapter-sized setting prose."""

    values = _string_list(value)
    locations: list[str] = []
    for item in values:
        for part in re.split(r"[、，,；;→]+", item):
            location = part.strip()
            if not location or location.startswith(("时间", "现实时间", "梦境时间")):
                continue
            locations.append(location)
    return list(dict.fromkeys(locations))


def _extend_location_signature(
    signatures: dict[str, list[str]],
    location: str,
    descriptors: list[str],
) -> None:
    """Keep the project contract bounded as chapters reuse the same location.

    Only accepts descriptors that pass :func:`_is_valid_sound_descriptor`,
    filtering out serialized JSON / structured data pollution.
    """

    existing = signatures.setdefault(location, [])
    for descriptor in descriptors:
        if len(existing) >= _MAX_LOCATION_SIGNATURE_DESCRIPTORS:
            break
        if descriptor and descriptor not in existing and _is_valid_sound_descriptor(descriptor):
            existing.append(descriptor)


def _source_fingerprint(
    project_id: str,
    story_context: dict[str, Any],
    style_profile: dict[str, Any],
    scene_intents: list[dict[str, Any]],
    outline: dict[str, Any],
) -> str:
    payload = {
        "project_id": project_id,
        "story_context": story_context,
        "style_profile": style_profile,
        "scene_intents": scene_intents,
        "outline": outline,
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def build_audio_creative_bible(
    *,
    project_id: str,
    story_context: dict[str, Any],
    style_profile: dict[str, Any] | None = None,
    scene_intents: list[dict[str, Any]] | None = None,
    outline: dict[str, Any] | None = None,
) -> AudioCreativeBible:
    """Derive a stable, provider-neutral sound identity from bounded story context."""

    style_profile = dict(style_profile or {})
    scene_intents = [dict(item) for item in (scene_intents or []) if isinstance(item, dict)]
    outline = dict(outline or {})
    title = _text(story_context.get("title"))
    genre = _text(story_context.get("genre")) or "叙事文学"
    tone = _text(story_context.get("tone")) or _text(style_profile.get("tone"))
    era = _text(story_context.get("era")) or _text(story_context.get("world"))
    audio_aesthetic = _text(story_context.get("audio_aesthetic"))
    style_tags = _string_list(
        style_profile.get("style_keywords")
        or style_profile.get("keywords")
        or style_profile.get("tags")
    )
    themes = _string_list(story_context.get("themes"))
    aesthetic = list(
        dict.fromkeys(
            item
            for item in (
                audio_aesthetic,
                f"{tone}、克制、沉浸" if tone else "克制、沉浸、对白优先",
                f"{genre}的叙事质感",
                f"{era}的空间与材质特征" if era else "",
                *style_tags[:4],
            )
            if item
        )
    )
    location_signatures: dict[str, list[str]] = {}
    for scene in scene_intents:
        location = _text(scene.get("location"))
        if not location:
            continue
        descriptors = [
            _text(scene.get("time_marker")),
            _text(scene.get("atmosphere")),
            _text(scene.get("sensory_focus")),
        ]
        _extend_location_signature(location_signatures, location, descriptors)
    raw_chapters = outline.get("chapters")
    for chapter in raw_chapters if isinstance(raw_chapters, list) else []:
        if not isinstance(chapter, dict):
            continue
        settings = _location_list(chapter.get("setting"))
        if not settings and _text(chapter.get("setting")):
            settings = [_text(chapter.get("setting"))]
        descriptors = [
            _text(chapter.get("time_anchor")),
            _text(chapter.get("emotional_plan")),
            _text(chapter.get("title")),
        ]
        for location in settings:
            _extend_location_signature(location_signatures, location, descriptors)
    motifs = {theme: f"为“{theme}”保留稀疏、可辨识但不抢白的原创音乐动机" for theme in themes[:6]}
    return AudioCreativeBible(
        project_id=project_id,
        title=title,
        genre=genre,
        overall_aesthetic=aesthetic,
        music_identity=[
            f"以{genre}叙事为主，主题动机稀疏、稳定、可复用",
            "纯伴奏、无人声、无歌词，为旁白和对白留出频谱空间",
            "转场和高潮允许变奏，但不更换整部作品的核心声音语汇",
        ],
        soundscape_identity=[
            "环境声优先表达空间、距离、材质和时段，不解释人物情绪",
            "同一地点复用同一声景身份，只调整密度和远近",
            "持续底床低存在感、可平滑循环，不包含可辨识对白",
        ],
        location_sound_signatures=location_signatures,
        motif_directions=motifs,
        prohibited_patterns=[
            "用高密度音效重复演绎文字已明说的每个动作",
            "在对白进行时使用不必要的前景冲击音",
            "每章重新发明与作品无关的音乐风格",
            "使用歌词、歌唱、可辨识台词或过度情绪化的环境底床",
        ],
        source_fingerprint=_source_fingerprint(
            project_id,
            story_context,
            style_profile,
            scene_intents,
            outline,
        ),
    )


def load_or_create_audio_creative_bible(
    *,
    layout: ProjectLayout,
    project_id: str,
    story_context: dict[str, Any],
    style_profile: dict[str, Any] | None = None,
    scene_intents: list[dict[str, Any]] | None = None,
    outline: dict[str, Any] | None = None,
) -> AudioCreativeBible:
    """Load the author's project contract or create the first deterministic version."""

    path = layout.tts_audio_creative_bible_path
    if path.is_file():
        bible = AudioCreativeBible.model_validate_json(path.read_text(encoding="utf-8"))
        if bible.project_id != project_id:
            raise ValueError("项目声音创作圣经与当前项目不匹配")
        return bible
    bible = build_audio_creative_bible(
        project_id=project_id,
        story_context=story_context,
        style_profile=style_profile,
        scene_intents=scene_intents,
        outline=outline,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, bible.model_dump(mode="json"))
    return bible


def _select_cues(
    cues: list[_CueT],
    *,
    limit: int,
    score: Callable[[_CueT], float],
) -> list[_CueT]:
    if limit <= 0:
        return []
    if len(cues) <= limit:
        return cues
    selected = {
        index
        for index, _item in sorted(
            enumerate(cues),
            key=lambda pair: (-score(pair[1]), pair[0]),
        )[:limit]
    }
    return [item for index, item in enumerate(cues) if index in selected]


def apply_audio_creative_bible(
    script: DubbingScript,
    bible: AudioCreativeBible,
    *,
    scene_count_hint: int = 0,
) -> DubbingScript:
    """Enforce foreground hierarchy and restrained cue density after AI design."""

    policy = bible.mix_policy
    scene_count = max(1, scene_count_hint, len(script.scene_transitions) + 1)
    bgm_limit = scene_count * policy.max_bgm_cues_per_scene
    soundscape_limit = scene_count * policy.max_soundscapes_per_scene
    sfx_limit = scene_count * policy.max_sfx_cues_per_scene
    original_counts = {
        "bgm": len(script.bgm_suggestions),
        "soundscape": len(script.soundscapes),
        "sfx": len(script.sfx_cues),
    }
    bgm = _select_cues(
        list(script.bgm_suggestions),
        limit=bgm_limit,
        score=lambda item: (
            item.intensity
            + (0.25 if item.narrative_role in {"climax", "resolution", "transition"} else 0.0)
        ),
    )
    soundscapes = _select_cues(
        list(script.soundscapes),
        limit=soundscape_limit,
        score=lambda item: (1.0 - item.density) + (0.2 if item.description else 0.0),
    )
    sfx = _select_cues(
        list(script.sfx_cues),
        limit=sfx_limit,
        score=lambda item: float(item.narrative_priority),
    )
    normalized_bgm = [
        item.model_copy(
            update={
                "volume": min(item.volume, policy.max_bgm_volume),
                "ducking_db": max(item.ducking_db, policy.min_bgm_ducking_db),
                "fade_in_ms": max(item.fade_in_ms, policy.minimum_bgm_fade_ms),
                "fade_out_ms": max(item.fade_out_ms, policy.minimum_bgm_fade_ms),
            }
        )
        for item in bgm
    ]
    normalized_soundscapes = [
        item.model_copy(
            update={
                "volume": min(item.volume, policy.max_soundscape_volume),
                "ducking_db": max(
                    item.ducking_db,
                    policy.min_soundscape_ducking_db,
                ),
                "fade_in_ms": max(
                    item.fade_in_ms,
                    policy.minimum_soundscape_fade_ms,
                ),
                "fade_out_ms": max(
                    item.fade_out_ms,
                    policy.minimum_soundscape_fade_ms,
                ),
            }
        )
        for item in soundscapes
    ]
    normalized_sfx: list[SFXCue] = []
    # Build a set of narration segment indices for diegetic overlap detection.
    narration_indices = {
        seg.segment_index
        for seg in script.segments
        if seg.segment_type == SegmentType.NARRATION
    }
    for item in sfx:
        # Diegetic SFX anchored to a narration segment describes a sound that
        # IS the narration content (e.g. keyboard typing while the narrator
        # describes typing).  These should retain overlap permission with their
        # trigger segment regardless of the priority threshold.
        is_diegetic = (
            item.trigger_segment_index is not None
            and item.trigger_segment_index in narration_indices
        )
        overlap_allowed = (
            item.allow_dialogue_overlap
            and (
                item.narrative_priority >= policy.minimum_dialogue_overlap_priority
                or is_diegetic
            )
        )
        volume_cap = (
            policy.max_dialogue_overlap_sfx_volume if overlap_allowed else policy.max_sfx_volume
        )
        normalized_sfx.append(
            item.model_copy(
                update={
                    "volume": min(item.volume, volume_cap),
                    "allow_dialogue_overlap": overlap_allowed,
                }
            )
        )
    metadata = dict(script.metadata)
    metadata["audio_creative_bible"] = {
        "project_id": bible.project_id,
        "source_fingerprint": bible.source_fingerprint,
        "scene_count": scene_count,
        "original_counts": original_counts,
        "applied_counts": {
            "bgm": len(normalized_bgm),
            "soundscape": len(normalized_soundscapes),
            "sfx": len(normalized_sfx),
        },
    }
    return script.model_copy(
        update={
            "bgm_suggestions": normalized_bgm,
            "soundscapes": normalized_soundscapes,
            "sfx_cues": normalized_sfx,
            "metadata": metadata,
        }
    )
