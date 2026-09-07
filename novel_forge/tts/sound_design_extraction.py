"""Independent sound-design extraction after performance script finalization.

This module separates sound design (SFX, BGM, Soundscape, Transitions) from
performance script generation.  It runs after the dubbing script segments are
finalized and can work even when the script-generation LLM fell back to rules.

BGM strategy: library-first reuse → gap marking → cold-start palette.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from novel_forge.common.constants import TaskType
from novel_forge.core.config import Settings
from novel_forge.gateway.router import ModelRouter
from novel_forge.obs.logger import get_logger
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.tts.rules import (
    load_emotion_to_mood_tags as _load_emotion_to_mood_tags,
)
from novel_forge.tts.rules import (
    load_emotion_to_narrative_role as _load_emotion_to_narrative_role,
)
from novel_forge.tts.rules import (
    load_environment_keywords as _load_environment_keywords,
)
from novel_forge.tts.schemas import (
    AudioCreativeBible,
    BGMGap,
    BGMTiming,
    DubbingScript,
    DubbingSegment,
    DubbingStyleProfile,
    SceneTransition,
    SFXCue,
    SoundAsset,
    SoundscapeCue,
)

_log = get_logger("tts.sound_design_extraction")


def _upstream_revision_matches(recorded: Any, current: dict[str, str]) -> bool:
    """Compare the recorded upstream revision against the current one.

    A missing record (legacy script) is treated as matching so old projects
    never trigger a forced re-extraction; only explicitly recorded drift
    invalidates incremental reuse.
    """
    if not isinstance(recorded, dict):
        return True
    return all(str(recorded.get(key) or "") == str(value) for key, value in current.items())


# ─── Data Structures ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BGMNeed:
    """A BGM requirement extracted from narrative analysis."""

    mood_tags: list[str]
    narrative_role: str
    mood_description: str
    start_segment: int | None
    end_segment: int | None
    duration_ms: int
    chapter_number: int


@dataclass
class SoundDesignOutput:
    """Result of sound design extraction."""

    sfx_cues: list[SFXCue] = field(default_factory=list)
    bgm_needs: list[BGMNeed] = field(default_factory=list)
    soundscapes: list[SoundscapeCue] = field(default_factory=list)
    scene_transitions: list[SceneTransition] = field(default_factory=list)
    mode: str = "rule_fallback"  # "llm" or "rule_fallback"


# ─── Main Entry Point ──────────────────────────────────────────────────────────


async def extract_sound_design(
    script: DubbingScript,
    *,
    bible: AudioCreativeBible | None = None,
    library_assets: list[SoundAsset] | None = None,
    story_context: dict[str, Any] | None = None,
    scene_intents: list[dict[str, Any]] | None = None,
    location_sound_seeds: list[dict[str, Any]] | None = None,
    reference_style_profile: DubbingStyleProfile | None = None,
    reference_style_strength: float = 0.0,
    router: ModelRouter | None = None,
    builder: PromptBuilder | None = None,
    settings: Settings | None = None,
    previous_script: DubbingScript | None = None,
    upstream_revision: dict[str, str] | None = None,
) -> DubbingScript:
    """Extract sound design from a finalized performance script.

    This runs after segments are locked and can work independently of whether
    the script-generation LLM succeeded or fell back to rules.

    ``location_sound_seeds`` carries StoryBible.locations ambient-sound
    definitions (initialization-time seeds).  Seeded locations take priority;
    prose-based inference degrades to gap-filling for unseeded scenes.

    ``upstream_revision`` records content hashes of upstream artifacts
    (e.g. ``{"story_bible": ..., "character_bible": ...}``).  When it differs
    from the previous script's recorded revision, the upstream is marked
    "来源已更新" and incremental reuse is skipped so seeds are re-extracted.

    When *previous_script* is provided and its ``script_hash`` matches the
    current script, sound design is reused from the previous script without
    re-running LLM extraction (incremental mode).

    Returns an updated DubbingScript with enriched sound cues.
    """
    if settings is not None and not settings.tts_sound_design_enabled:
        _log.debug("Sound design extraction disabled by settings")
        return script

    # ── Incremental reuse: skip if script unchanged ──
    upstream_revision = dict(upstream_revision or {})
    upstream_changed = bool(
        upstream_revision
        and previous_script is not None
        and not _upstream_revision_matches(
            previous_script.metadata.get("upstream_revision"), upstream_revision
        )
    )
    if upstream_changed:
        _log.info(
            "Upstream revision changed (来源已更新), re-extracting sound design: %s",
            upstream_revision,
        )
    if (
        previous_script is not None
        and not upstream_changed
        and script.script_hash
        and previous_script.script_hash == script.script_hash
        and (previous_script.sfx_cues or previous_script.bgm_suggestions or previous_script.soundscapes)
    ):
        _log.info("Sound design reused from previous script (hash match)")
        return script.model_copy(
            update={
                "sfx_cues": list(previous_script.sfx_cues),
                "bgm_suggestions": list(previous_script.bgm_suggestions),
                "soundscapes": list(previous_script.soundscapes),
                "scene_transitions": list(previous_script.scene_transitions),
            }
        )

    story_context = story_context or {}
    library_assets = library_assets or []
    scene_intents = scene_intents or []
    location_sound_seeds = location_sound_seeds or []

    # 1. Try LLM extraction, fall back to rules
    design: SoundDesignOutput | None = None
    if router is not None and builder is not None and settings is not None:
        design = await _extract_sound_design_llm(
            script,
            bible=bible,
            library_assets=library_assets,
            story_context=story_context,
            scene_intents=scene_intents,
            location_sound_seeds=location_sound_seeds,
            reference_style_profile=reference_style_profile,
            reference_style_strength=reference_style_strength,
            router=router,
            builder=builder,
            settings=settings,
        )
    if design is None:
        design = _extract_sound_design_rules(
            script, bible=bible, location_sound_seeds=location_sound_seeds
        )

    # 2. Resolve BGM needs against library
    resolved_bgm, bgm_gaps = _resolve_bgm_from_library(
        design.bgm_needs,
        library_assets,
    )

    # 3. Merge into script (additive, never overwrite existing cues)
    merged = _merge_sound_design(
        script,
        design,
        resolved_bgm,
        bgm_gaps,
        upstream_revision=upstream_revision,
        upstream_changed=upstream_changed,
    )

    _log.info(
        "Sound design extraction (%s): +%d SFX, +%d BGM (library), %d gaps, "
        "+%d soundscapes, +%d transitions",
        design.mode,
        len(design.sfx_cues),
        len(resolved_bgm),
        len(bgm_gaps),
        len(design.soundscapes),
        len(design.scene_transitions),
    )
    return merged


# ─── LLM Extraction Path ───────────────────────────────────────────────────────


async def _extract_sound_design_llm(
    script: DubbingScript,
    *,
    bible: AudioCreativeBible | None,
    library_assets: list[SoundAsset],
    story_context: dict[str, Any],
    scene_intents: list[dict[str, Any]],
    location_sound_seeds: list[dict[str, Any]],
    reference_style_profile: DubbingStyleProfile | None,
    reference_style_strength: float,
    router: ModelRouter,
    builder: PromptBuilder,
    settings: Settings,
) -> SoundDesignOutput | None:
    """Attempt LLM-based sound design extraction with context-aware retry.

    On template rendering failures (prompt-context contract violations), the
    context is validated and repaired before a single retry.  Transient model
    errors are retried once without modification.
    """
    from jinja2 import UndefinedError

    from novel_forge.model_runtime import StructuredModelService

    stage_cards = _build_sound_design_stage_cards(
        script,
        bible=bible,
        library_assets=library_assets,
        story_context=story_context,
        scene_intents=scene_intents,
        location_sound_seeds=location_sound_seeds,
        reference_style_profile=reference_style_profile,
        reference_style_strength=reference_style_strength,
    )
    service = StructuredModelService(
        router=router,
        builder=builder,
        on_step=lambda _event, _data: None,
        settings=settings,
    )

    max_attempts = 2
    for attempt in range(max_attempts):
        try:
            response = await service.call_with_retry(
                TaskType.TTS_SOUND_DESIGN,
                {"stage_cards": stage_cards},
                max_tokens=4096,
                temperature=float(getattr(settings, "tts_sound_design_temperature", 0.5)),
                top_p=float(getattr(settings, "tts_sound_design_top_p", 0.95)),
                max_retries=1,
            )
            if not isinstance(response, dict):
                raise TypeError("Sound design model returned non-object JSON")
            return _parse_sound_design_response(response, script.chapter_number)
        except (UndefinedError, KeyError, TypeError) as exc:
            # Prompt-context contract violation — attempt context repair.
            if attempt < max_attempts - 1:
                _log.warning(
                    "LLM sound design extraction failed (attempt %d/%d), "
                    "repairing prompt context: %s",
                    attempt + 1,
                    max_attempts,
                    exc,
                )
                stage_cards = _repair_sound_design_stage_cards(stage_cards)
                continue
            _log.warning("LLM sound design extraction failed after repair, using rules: %s", exc)
            return None
        except Exception as exc:
            _log.warning("LLM sound design extraction failed, using rules: %s", exc)
            return None
    return None


def _repair_sound_design_stage_cards(stage_cards: dict[str, Any]) -> dict[str, Any]:
    """Repair stage cards by ensuring all template-required keys exist.

    Called after a template rendering failure to fill missing defaults that
    the Jinja2 StrictUndefined environment rejects.
    """
    repaired = dict(stage_cards)

    # Ensure story_context has all keys the template accesses.
    story_ctx = dict(repaired.get("story_context") or {})
    story_ctx.setdefault("title", "")
    story_ctx.setdefault("genre", "")
    story_ctx.setdefault("tone", "")
    story_ctx.setdefault("era", "")
    repaired["story_context"] = story_ctx

    # Ensure bible is either None (falsy) or a complete dict.
    bible = repaired.get("bible")
    if isinstance(bible, dict):
        bible.setdefault("genre", "叙事文学")
        bible.setdefault("overall_aesthetic", [])
        bible.setdefault("music_identity", [])
        bible.setdefault("location_sound_signatures", {})
        bible.setdefault("motif_directions", {})
    repaired["bible"] = bible

    # Ensure list fields are present.
    repaired.setdefault("segment_summaries", [])
    repaired.setdefault("segment_count", 0)
    repaired.setdefault("scene_transitions", [])
    repaired.setdefault("bgm_assets", [])
    repaired.setdefault("scene_intents", [])
    repaired.setdefault("location_sound_seeds", [])
    repaired.setdefault("chapter_number", 1)

    return repaired


def _build_sound_design_stage_cards(
    script: DubbingScript,
    *,
    bible: AudioCreativeBible | None,
    library_assets: list[SoundAsset],
    story_context: dict[str, Any],
    scene_intents: list[dict[str, Any]] | None = None,
    location_sound_seeds: list[dict[str, Any]] | None = None,
    reference_style_profile: DubbingStyleProfile | None = None,
    reference_style_strength: float = 0.0,
) -> dict[str, Any]:
    """Build stage cards for the sound design LLM prompt."""
    # Segment summaries: narration gets full text (capped) for SFX anchoring;
    # speech segments get a short preview since sound events live in narration.
    segment_summaries = []
    for seg in script.segments:
        is_speech = seg.segment_type.value in ("dialogue", "inner_thought")
        cap = 60 if is_speech else 400
        text_preview = seg.text[:cap] + ("…" if len(seg.text) > cap else "")
        segment_summaries.append({
            "index": seg.segment_index,
            "type": seg.segment_type.value,
            "scene_context": seg.scene_context or "",
            "emotion": seg.emotion.value if seg.emotion else "neutral",
            "text_preview": text_preview,
        })

    # Available BGM assets for reuse hints
    bgm_assets = [
        {
            "asset_id": asset.asset_id,
            "display_name": asset.display_name,
            "tags": asset.tags,
        }
        for asset in library_assets
        if asset.kind == "bgm" and asset.approval_status == "approved"
    ]

    # Bible summary – use None (not empty dict) so Jinja2 `{% if bible %}` is falsy.
    bible_summary: dict[str, Any] | None = None
    if bible is not None:
        bible_summary = {
            "genre": bible.genre,
            "overall_aesthetic": bible.overall_aesthetic[:4],
            "music_identity": bible.music_identity[:3],
            "location_sound_signatures": dict(
                list(bible.location_sound_signatures.items())[:8]
            ),
            "motif_directions": dict(list(bible.motif_directions.items())[:6]),
        }

    return {
        "chapter_number": script.chapter_number,
        "segment_summaries": segment_summaries,
        "segment_count": len(script.segments),
        "scene_transitions": [
            {
                "transition_type": t.transition_type,
                "label": t.label,
                "from_context": t.from_context,
                "to_context": t.to_context,
            }
            for t in script.scene_transitions
        ],
        "bgm_assets": bgm_assets,
        "bible": bible_summary,
        "story_context": {
            "title": story_context.get("title", ""),
            "genre": story_context.get("genre", ""),
            "tone": story_context.get("tone", ""),
            "era": story_context.get("era", story_context.get("world", "")),
        },
        "scene_intents": [
            {
                "scene_id": scene.get("scene_id", ""),
                "location": scene.get("location", ""),
                "time_marker": scene.get("time_marker", ""),
                "emotional_beat": scene.get("emotional_beat", ""),
                "sensory_notes": scene.get("sensory_notes", ""),
            }
            for scene in (scene_intents or [])
            if isinstance(scene, dict)
        ],
        "location_sound_seeds": [
            {
                "name": str(seed.get("name") or ""),
                "ambient_sound": [
                    str(item).strip()
                    for item in (seed.get("ambient_sound") or [])
                    if str(item or "").strip()
                ][:6],
            }
            for seed in (location_sound_seeds or [])
            if isinstance(seed, dict) and str(seed.get("name") or "").strip()
        ],
        "reference_dubbing_style": (
            {
                **reference_style_profile.model_dump(
                    mode="json",
                    exclude={"created_at", "metrics"},
                ),
                "strength": round(max(0.0, min(1.0, reference_style_strength)), 3),
            }
            if reference_style_profile is not None and reference_style_strength > 0.0
            else None
        ),
    }


def _parse_sound_design_response(
    response: dict[str, Any],
    chapter_number: int,
) -> SoundDesignOutput:
    """Parse LLM sound design response into structured output."""
    output = SoundDesignOutput(mode="llm")

    # Parse SFX cues
    for sfx_data in response.get("sfx_cues", []):
        if not isinstance(sfx_data, dict):
            continue
        effect_name = str(sfx_data.get("effect_name", "")).strip()
        if not effect_name:
            continue
        output.sfx_cues.append(SFXCue(
            effect_name=effect_name,
            description=str(sfx_data.get("description", "")),
            trigger_segment_index=sfx_data.get("trigger_segment_index"),
            offset_ms=int(sfx_data.get("offset_ms", 0)),
            duration_ms=int(sfx_data.get("duration_ms", 1000)),
            volume=float(sfx_data.get("volume", 0.4)),
            allow_dialogue_overlap=bool(sfx_data.get("allow_dialogue_overlap", True)),
            maximum_timing_shift_ms=int(sfx_data.get("maximum_timing_shift_ms", 1500)),
            narrative_priority=int(sfx_data.get("narrative_priority", 80)),
        ))

    # Parse BGM needs
    for bgm_data in response.get("bgm_needs", []):
        if not isinstance(bgm_data, dict):
            continue
        mood_tags = bgm_data.get("mood_tags", [])
        if isinstance(mood_tags, str):
            mood_tags = [mood_tags]
        output.bgm_needs.append(BGMNeed(
            mood_tags=[str(t).strip() for t in mood_tags if str(t).strip()],
            narrative_role=str(bgm_data.get("narrative_role", "underscore")),
            mood_description=str(bgm_data.get("mood_description", "")),
            start_segment=bgm_data.get("start_segment_index"),
            end_segment=bgm_data.get("end_segment_index"),
            duration_ms=int(bgm_data.get("estimated_duration_ms", 60_000)),
            chapter_number=chapter_number,
        ))

    # Parse soundscapes
    for sc_data in response.get("soundscapes", []):
        if not isinstance(sc_data, dict):
            continue
        name = str(sc_data.get("name", "")).strip()
        if not name:
            continue
        output.soundscapes.append(SoundscapeCue(
            name=name,
            description=str(sc_data.get("description", "")),
            asset_hint=str(sc_data.get("asset_hint", "")),
            start_segment_index=sc_data.get("start_segment_index"),
            end_segment_index=sc_data.get("end_segment_index"),
            volume=float(sc_data.get("volume", 0.14)),
            ducking_db=float(sc_data.get("ducking_db", 8.0)),
            density=float(sc_data.get("density", 0.3)),
        ))

    # Parse scene transitions
    for tr_data in response.get("scene_transitions", []):
        if not isinstance(tr_data, dict):
            continue
        output.scene_transitions.append(SceneTransition(
            transition_type=str(tr_data.get("transition_type", "tone_shift")),
            label=str(tr_data.get("label", "")),
            gap_ms=int(tr_data.get("gap_ms", 1500)),
            from_context=str(tr_data.get("from_context", "")),
            to_context=str(tr_data.get("to_context", "")),
        ))

    return output


# ─── Rule-Based Fallback Path ──────────────────────────────────────────────────


def _extract_sound_design_rules(
    script: DubbingScript,
    *,
    bible: AudioCreativeBible | None = None,
    location_sound_seeds: list[dict[str, Any]] | None = None,
) -> SoundDesignOutput:
    """Rule-based sound design extraction when LLM is unavailable."""
    output = SoundDesignOutput(mode="rule_fallback")

    # 1. Infer BGM needs from emotion arc
    output.bgm_needs = _infer_bgm_needs_from_emotion_arc(script)

    # 2. Seed soundscapes from StoryBible locations first; prose-based
    #    inference only fills gaps for unseeded scenes.
    seeds = location_sound_seeds or []
    seeded_cues, seeded_names = _seed_soundscapes_from_locations(script, seeds)
    inferred = _infer_soundscapes_from_contexts(
        script, bible=bible, covered_location_names=seeded_names
    )
    output.soundscapes = (seeded_cues + inferred)[:4]

    # 3. Scene transitions from segment transitions
    output.scene_transitions = list(script.scene_transitions)

    # SFX: rely on existing _recover_event_sfx in script_review.py
    # (already called before this step in the pipeline)

    return output


# ─── Emotion Arc → BGM Needs ───────────────────────────────────────────────────

# Emotion categories for BGM mood mapping — loaded from externalized JSON.
_EMOTION_TO_MOOD_TAGS: dict[str, list[str]] = _load_emotion_to_mood_tags() or {
    "neutral": ["平静"],
    "happy": ["欢快", "温馨"],
    "sad": ["悲伤", "抒情"],
    "angry": ["紧张", "激烈"],
    "fearful": ["紧张", "悬疑"],
    "surprised": ["悬疑", "转折"],
    "tender": ["温馨", "抒情"],
    "anxious": ["紧张", "悬疑"],
    "determined": ["激昂", "紧张"],
    "nostalgic": ["怀旧", "抒情"],
    "contempt": ["紧张", "冷峻"],
    "playful": ["欢快", "轻松"],
    "whisper": ["平静", "私密"],
    "mocking": ["冷峻", "讽刺"],
    "disgusted": ["紧张", "压抑"],
}

_EMOTION_TO_NARRATIVE_ROLE: dict[str, str] = _load_emotion_to_narrative_role() or {
    "fearful": "tension",
    "anxious": "tension",
    "angry": "tension",
    "determined": "climax",
    "surprised": "transition",
    "sad": "resolution",
    "happy": "resolution",
    "tender": "underscore",
}


def _infer_bgm_needs_from_emotion_arc(script: DubbingScript) -> list[BGMNeed]:
    """Detect emotion changes and output BGM needs (no generation, marking only)."""
    segments = script.segments
    if len(segments) < 6:
        return []

    needs: list[BGMNeed] = []
    chapter_number = script.chapter_number

    # Sliding window: detect sustained high-intensity emotion spans
    window_size = 4
    i = 0
    while i < len(segments) - window_size + 1:
        window = segments[i : i + window_size]
        emotions = [
            seg.emotion.value if seg.emotion else "neutral"
            for seg in window
        ]
        # Check if majority share same non-neutral emotion
        non_neutral = [e for e in emotions if e != "neutral"]
        if len(non_neutral) >= 3:
            dominant = max(set(non_neutral), key=non_neutral.count)
            count = non_neutral.count(dominant)
            if count >= 3:
                # Find the full span of this emotion
                start_idx = i
                end_idx = i + window_size - 1
                # Extend forward
                j = end_idx + 1
                while j < len(segments):
                    seg_emotion = (
                        segments[j].emotion.value
                        if segments[j].emotion
                        else "neutral"
                    )
                    if seg_emotion == dominant or seg_emotion == "neutral":
                        end_idx = j
                        j += 1
                    else:
                        break

                mood_tags = _EMOTION_TO_MOOD_TAGS.get(dominant, ["平静"])
                narrative_role = _EMOTION_TO_NARRATIVE_ROLE.get(dominant, "underscore")

                # Avoid duplicate needs for same mood in adjacent spans
                if not needs or needs[-1].mood_tags != mood_tags:
                    needs.append(BGMNeed(
                        mood_tags=mood_tags,
                        narrative_role=narrative_role,
                        mood_description=f"{dominant} 情绪段落配乐",
                        start_segment=segments[start_idx].segment_index,
                        end_segment=segments[end_idx].segment_index,
                        duration_ms=_estimate_span_duration(segments, start_idx, end_idx),
                        chapter_number=chapter_number,
                    ))
                i = end_idx + 1
                continue
        i += 1

    # Detect emotion transitions (sharp changes)
    for idx in range(1, len(segments)):
        prev_emotion = (
            segments[idx - 1].emotion.value if segments[idx - 1].emotion else "neutral"
        )
        curr_emotion = (
            segments[idx].emotion.value if segments[idx].emotion else "neutral"
        )
        if prev_emotion == curr_emotion:
            continue
        # Sharp transition detection
        if _is_sharp_emotion_change(prev_emotion, curr_emotion):
            # Check if we already have a BGM need covering this segment
            seg_idx = segments[idx].segment_index
            already_covered = any(
                need.start_segment is not None
                and need.end_segment is not None
                and need.start_segment <= seg_idx <= need.end_segment
                for need in needs
            )
            if not already_covered and len(needs) < 4:
                mood_tags = _EMOTION_TO_MOOD_TAGS.get(curr_emotion, ["转折"])
                needs.append(BGMNeed(
                    mood_tags=mood_tags,
                    narrative_role="transition",
                    mood_description=f"情绪转折：{prev_emotion} → {curr_emotion}",
                    start_segment=seg_idx,
                    end_segment=None,  # Extend to chapter end or next transition
                    duration_ms=30_000,
                    chapter_number=chapter_number,
                ))

    # Limit to reasonable density
    return needs[:4]


def _is_sharp_emotion_change(prev: str, curr: str) -> bool:
    """Detect if an emotion transition is sharp enough to warrant BGM change."""
    calm_emotions = {"neutral", "tender", "nostalgic", "whisper"}
    intense_emotions = {"angry", "fearful", "anxious", "determined", "surprised"}
    return (prev in calm_emotions and curr in intense_emotions) or (
        prev in intense_emotions and curr in calm_emotions
    )


def _estimate_span_duration(
    segments: list[DubbingSegment],
    start_idx: int,
    end_idx: int,
) -> int:
    """Rough duration estimate: ~200ms per character."""
    total_chars = sum(len(seg.text) for seg in segments[start_idx : end_idx + 1])
    return max(15_000, min(180_000, total_chars * 200))


# ─── Scene Context → Soundscapes ───────────────────────────────────────────────

# Keywords for environment detection — loaded from externalized JSON.
_ENVIRONMENT_KEYWORDS: dict[str, list[str]] = _load_environment_keywords() or {
    "雨": ["雨声", "雨滴", "下雨", "暴雨", "细雨", "雨夜"],
    "风": ["风声", "大风", "微风", "寒风", "狂风"],
    "夜": ["夜晚", "深夜", "夜色", "午夜"],
    "街": ["街道", "街头", "马路", "人群"],
    "室": ["室内", "房间", "屋子", "客厅", "卧室"],
    "办公": ["办公室", "工位", "写字楼"],
    "医院": ["医院", "病房", "诊所"],
    "学校": ["学校", "教室", "校园"],
    "咖啡": ["咖啡馆", "咖啡厅"],
    "酒": ["酒吧", "酒馆", "餐厅"],
}


def _seed_soundscapes_from_locations(
    script: DubbingScript,
    location_sound_seeds: list[dict[str, Any]],
) -> tuple[list[SoundscapeCue], set[str]]:
    """Convert StoryBible location ambient-sound seeds into soundscape cues.

    Initialization-time definitions take priority over prose inference: a
    seed is applied whenever its location name appears in a segment's scene
    context.  Returns cues and the set of consumed seed names.
    """
    cues: list[SoundscapeCue] = []
    consumed: set[str] = set()
    for seed in location_sound_seeds:
        name = str(seed.get("name") or "").strip()
        ambient = [
            str(item).strip()
            for item in (seed.get("ambient_sound") or [])
            if str(item or "").strip()
        ]
        if not name or not ambient:
            continue
        start_index: int | None = None
        end_index: int | None = None
        for segment in script.segments:
            if name in str(segment.scene_context or ""):
                if start_index is None:
                    start_index = segment.segment_index
                end_index = segment.segment_index
        if start_index is None:
            continue
        cues.append(SoundscapeCue(
            name=f"{name}环境底床",
            description=f"{name}环境底床（{'、'.join(ambient[:3])}），来自故事圣经场景定义",
            start_segment_index=start_index,
            end_segment_index=end_index,
            volume=0.14,
            ducking_db=8.0,
            density=0.3,
        ))
        consumed.add(name)
    return cues[:3], consumed


def _infer_soundscapes_from_contexts(
    script: DubbingScript,
    *,
    bible: AudioCreativeBible | None = None,
    covered_location_names: set[str] | None = None,
) -> list[SoundscapeCue]:
    """Infer soundscapes from scene_context text (gap-filling for seeds)."""
    covered = covered_location_names or set()
    # Group segments by scene_context
    groups: list[tuple[str, int, int]] = []
    for segment in script.segments:
        context = str(segment.scene_context or "").strip()
        if groups and groups[-1][0] == context:
            prev_context, start_index, _ = groups[-1]
            groups[-1] = (prev_context, start_index, segment.segment_index)
        else:
            groups.append((context, segment.segment_index, segment.segment_index))

    cues: list[SoundscapeCue] = []
    for context, start_index, end_index in groups:
        if not context:
            continue
        # Skip contexts already covered by a StoryBible location seed.
        if any(seed_name and seed_name in context for seed_name in covered):
            continue
        # Detect environment from context text
        env_name = _detect_environment(context)
        if env_name is None:
            continue
        # Check bible for location signature
        description = f"为「{context[:80]}」铺设低存在感环境底床"
        if bible is not None:
            for location, descriptors in bible.location_sound_signatures.items():
                if location in context:
                    descriptor_text = "、".join(descriptors[:3])
                    description = f"{location}环境底床（{descriptor_text}）"
                    break

        cues.append(SoundscapeCue(
            name=f"{env_name}环境底床",
            description=description,
            start_segment_index=start_index,
            end_segment_index=end_index,
            volume=0.14,
            ducking_db=8.0,
            density=0.25,
        ))

    return cues[:3]  # Limit density


def _detect_environment(context: str) -> str | None:
    """Detect environment type from scene context text."""
    for env_type, keywords in _ENVIRONMENT_KEYWORDS.items():
        for keyword in keywords:
            if keyword in context:
                return env_type
    return None


# ─── BGM Library Matching ──────────────────────────────────────────────────────


def _resolve_bgm_from_library(
    bgm_needs: list[BGMNeed],
    library_assets: list[SoundAsset],
) -> tuple[list[BGMTiming], list[BGMGap]]:
    """Library-first BGM matching. Returns resolved cues and unresolved gaps."""
    approved_bgm = [
        asset
        for asset in library_assets
        if asset.kind == "bgm" and asset.approval_status == "approved"
    ]

    resolved: list[BGMTiming] = []
    gaps: list[BGMGap] = []
    used_asset_ids: set[str] = set()

    for need in bgm_needs:
        match = _match_bgm_by_mood_tags(
            need.mood_tags,
            need.narrative_role,
            approved_bgm,
            excluded_ids=used_asset_ids,
        )
        if match is not None:
            used_asset_ids.add(match.asset_id)
            resolved.append(BGMTiming(
                track_name=match.display_name,
                mood=need.mood_description,
                mood_tags=need.mood_tags,
                narrative_role=need.narrative_role,
                reuse_hint=match.asset_id,
                start_segment_index=need.start_segment,
                end_segment_index=need.end_segment,
                volume=0.22,
                ducking_db=10.0,
                fade_in_ms=1500,
                fade_out_ms=2000,
                intensity=0.5,
            ))
        else:
            gaps.append(BGMGap(
                mood_tags=need.mood_tags,
                narrative_role=need.narrative_role,
                suggested_direction=need.mood_description,
                estimated_duration_ms=need.duration_ms,
                chapter_number=need.chapter_number,
            ))

    return resolved, gaps


def _match_bgm_by_mood_tags(
    mood_tags: list[str],
    narrative_role: str,
    assets: list[SoundAsset],
    *,
    excluded_ids: set[str] | None = None,
) -> SoundAsset | None:
    """Find best matching BGM asset by mood tag overlap."""
    excluded = excluded_ids or set()
    best_score = 0.0
    best_asset: SoundAsset | None = None

    for asset in assets:
        if asset.asset_id in excluded:
            continue
        score = _bgm_mood_score(asset, mood_tags, narrative_role)
        if score > best_score:
            best_score = score
            best_asset = asset

    # Require at least 1 tag overlap for a match
    if best_score >= 1.0:
        return best_asset
    return None


def _bgm_mood_score(
    asset: SoundAsset,
    mood_tags: list[str],
    narrative_role: str,
) -> float:
    """Semantic matching score: mood_tags intersection + narrative_role bonus."""
    asset_tags_lower = {tag.casefold() for tag in asset.tags}
    mood_tags_lower = {tag.casefold() for tag in mood_tags}
    tag_overlap = len(asset_tags_lower & mood_tags_lower)
    role_bonus = 0.3 if narrative_role.casefold() in asset_tags_lower else 0.0
    return float(tag_overlap) + role_bonus


# ─── Merge Strategy ────────────────────────────────────────────────────────────


def _merge_sound_design(
    script: DubbingScript,
    design: SoundDesignOutput,
    resolved_bgm: list[BGMTiming],
    bgm_gaps: list[BGMGap],
    *,
    upstream_revision: dict[str, str] | None = None,
    upstream_changed: bool = False,
) -> DubbingScript:
    """Merge sound design into script. Additive only, never overwrite existing."""
    updates: dict[str, Any] = {}
    metadata = dict(script.metadata)

    # 1. SFX: deduplicate by (segment_index, effect_name)
    existing_sfx_keys = {
        (cue.trigger_segment_index, re.sub(r"\s+", "", cue.effect_name).casefold())
        for cue in script.sfx_cues
    }
    new_sfx = [
        cue
        for cue in design.sfx_cues
        if (cue.trigger_segment_index, re.sub(r"\s+", "", cue.effect_name).casefold())
        not in existing_sfx_keys
    ]
    if new_sfx:
        updates["sfx_cues"] = list(script.sfx_cues) + new_sfx

    # 2. BGM: keep existing + add library-resolved
    if resolved_bgm:
        existing_bgm_keys = {
            (cue.start_segment_index, cue.mood)
            for cue in script.bgm_suggestions
        }
        additional_bgm = [
            cue
            for cue in resolved_bgm
            if (cue.start_segment_index, cue.mood) not in existing_bgm_keys
        ]
        if additional_bgm:
            updates["bgm_suggestions"] = list(script.bgm_suggestions) + additional_bgm

    # 3. Soundscapes: only add if script has none
    if not script.soundscapes and design.soundscapes:
        updates["soundscapes"] = design.soundscapes

    # 4. Scene transitions: only add if script has none
    if not script.scene_transitions and design.scene_transitions:
        updates["scene_transitions"] = design.scene_transitions

    # 5. Write metadata
    metadata["sound_design"] = {
        "mode": design.mode,
        "sfx_added": len(new_sfx) if new_sfx else 0,
        "bgm_resolved_from_library": len(resolved_bgm),
        "bgm_gaps": len(bgm_gaps),
        "soundscapes_added": len(design.soundscapes) if not script.soundscapes else 0,
        "upstream_changed": upstream_changed,
    }
    if upstream_revision:
        metadata["upstream_revision"] = upstream_revision
    if bgm_gaps:
        metadata["bgm_gaps"] = [gap.model_dump() for gap in bgm_gaps]
    updates["metadata"] = metadata

    if updates:
        return script.model_copy(update=updates)
    return script
