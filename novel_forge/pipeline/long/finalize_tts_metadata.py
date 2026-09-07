"""TTS metadata extraction — deterministic, zero LLM cost.

Extracted from ``chapter_flow_finalize.py`` to keep the finalize module
focused on orchestration.  This module is the single source of truth for
mapping chapter artifacts (plan, editorial contract, reading-power report)
onto the :class:`~novel_forge.tts.schemas.ChapterTTSMetadata` consumed by
the TTS pipeline.
"""

from __future__ import annotations

from typing import Any

from novel_forge.tts.emotion_inference import infer_emotion_with_context
from novel_forge.tts.schemas import (
    ChapterTTSMetadata,
    PacingAnnotation,
    SceneEmotionAnnotation,
    ToneShift,
)
from novel_forge.tts.script_integrity import compute_source_text_hash


def _infer_scene_baseline_emotion(emotional_beat: str) -> tuple[str, float]:
    """Infer the baseline (tone-setting) emotion from a scene emotional_beat.

    Scene-level emotional beats often describe a progression (e.g.
    "压抑中暗涌怒意").  For the purpose of narrator emotion inheritance we
    want the *baseline* tone (the first emotion word that appears), not the
    peak event emotion that may come later in the phrase.  This prevents
    opening narration from inheriting high-arousal emotions that are only
    present as undercurrents.

    Returns (emotion_value, intensity).
    """
    # Use the enhanced inference with position=0 (scene-level = opening context)
    # to get a conservative baseline reading.
    emotion, intensity = infer_emotion_with_context(
        emotional_beat,
        chapter_number=0,
        segment_position=0,
        total_segments=10,
        is_narration=True,
    )
    # Preserve high-intensity boost for explicit intensity markers in the beat.
    if any(kw in emotional_beat for kw in ["激烈", "强烈", "爆发", "崩溃"]):
        intensity = max(intensity, 0.7)
    return emotion.value, intensity


def extract_tts_metadata(
    *,
    plan: Any,
    editorial_contract: Any,
    reading_power_report: Any,
    chapter_number: int = 0,
    source_text: str = "",
) -> dict[str, Any] | None:
    """Extract TTS-friendly metadata from chapter artifacts.

    Pure deterministic mapping — no LLM calls.  When ``source_text`` is
    supplied, an empty but source-bound snapshot is still returned so a new
    chapter finalization overwrites any richer metadata from an older draft.
    Legacy callers without source text retain the previous ``None`` behavior.
    """
    scene_emotion_map: list[SceneEmotionAnnotation] = []
    character_emotion_trajectories: dict[str, list[dict[str, Any]]] = {}
    pacing_annotations: list[PacingAnnotation] = []
    expression_channel_constraints: dict[str, int] = {}
    narration_tone_progression: list[ToneShift] = []

    has_data = False

    # 1. Extract scene_emotion_map from ChapterPlan.scene_intents
    scene_intents = getattr(plan, "scene_intents", None) or []
    for intent in scene_intents:
        emotional_beat = getattr(intent, "emotional_beat", "") or ""
        scene_id = getattr(intent, "scene_id", "") or ""
        if emotional_beat:
            has_data = True
            # Use baseline-aware inference: prefer the tone-setting emotion
            # over peak event emotions embedded in the beat description.
            emotion_str, intensity = _infer_scene_baseline_emotion(emotional_beat)
            from novel_forge.tts.schemas import EmotionTag as _ET

            try:
                dominant_emotion = _ET(emotion_str)
            except ValueError:
                dominant_emotion = _ET.NEUTRAL
            scene_emotion_map.append(
                SceneEmotionAnnotation(
                    scene_id=scene_id,
                    dominant_emotion=dominant_emotion,
                    emotion_intensity=intensity,
                    emotion_shift=emotional_beat,
                )
            )

            # Project the scene-level emotional beat onto the characters that
            # the authoritative plan says are present.  This preserves the
            # upstream distinction between narrator tone and character voice
            # instead of making every line inherit a chapter-wide emotion.
            character_names = getattr(intent, "required_characters", None) or []
            for character in character_names:
                name = str(character or "").strip()
                if not name:
                    continue
                character_emotion_trajectories.setdefault(name, []).append(
                    {
                        "scene_id": scene_id,
                        "emotion": dominant_emotion.value,
                        "intensity": intensity,
                        "beat": emotional_beat,
                    }
                )

    # 2. Extract expression_channel_constraints from EditorialContract
    if editorial_contract is not None:
        ec_budget = getattr(editorial_contract, "expression_channel_budget", None)
        if isinstance(ec_budget, dict) and ec_budget:
            has_data = True
            expression_channel_constraints = {str(k): int(v) for k, v in ec_budget.items()}

    # 3. Extract scene-level narration tone progression and chapter arc.
    last_scene_tone = ""
    for intent in scene_intents:
        scene_tone = str(getattr(intent, "emotional_beat", "") or "").strip()
        if not scene_tone:
            continue
        if last_scene_tone and scene_tone != last_scene_tone:
            narration_tone_progression.append(
                ToneShift(
                    position=str(getattr(intent, "scene_id", "") or "scene"),
                    from_tone=last_scene_tone[:80],
                    to_tone=scene_tone[:80],
                    trigger="scene_emotional_beat",
                )
            )
        last_scene_tone = scene_tone

    emotional_arc = getattr(plan, "emotional_arc", "") or ""
    if emotional_arc:
        has_data = True
        narration_tone_progression.append(
            ToneShift(
                position="chapter",
                from_tone="",
                to_tone=emotional_arc[:80],
                trigger="emotional_arc_from_plan",
            )
        )

    # 4. Extract scene pacing from the authoritative cross-scene intent.
    cross_scene_intent = getattr(plan, "cross_scene_intent", None) or {}
    pacing_curve = (
        cross_scene_intent.get("pacing_curve", []) if isinstance(cross_scene_intent, dict) else []
    )
    if isinstance(pacing_curve, list) and pacing_curve:
        has_data = True
        pacing_labels = {1: "slow", 2: "decelerating", 3: "normal", 4: "accelerating", 5: "fast"}
        for index, value in enumerate(pacing_curve):
            try:
                pacing_value = max(1, min(5, int(value)))
            except (TypeError, ValueError):
                continue
            scene_id = (
                str(getattr(scene_intents[index], "scene_id", "") or "scene")
                if index < len(scene_intents)
                else f"scene_{index + 1}"
            )
            pacing_annotations.append(
                PacingAnnotation(
                    text_range=scene_id,
                    pacing=pacing_labels[pacing_value],
                    reason="chapter_plan.cross_scene_intent.pacing_curve",
                )
            )

    # 5. Extract chapter-level pacing from reading_power_report as a fallback
    # and a calibration signal when the plan has no scene curve.
    if reading_power_report is not None:
        rp_score = getattr(reading_power_report, "overall_score", None)
        if rp_score is not None:
            has_data = True
            pacing_annotations.append(
                PacingAnnotation(
                    text_range="chapter",
                    pacing="fast" if rp_score >= 8.0 else "normal" if rp_score >= 6.0 else "slow",
                    reason=f"reading_power_overall_score={rp_score}",
                )
            )

    if not has_data and not source_text:
        return None

    metadata = ChapterTTSMetadata(
        chapter_number=max(0, int(chapter_number or 0)),
        source_text_hash=compute_source_text_hash(source_text) if source_text else "",
        scene_emotion_map=scene_emotion_map,
        character_emotion_trajectories=character_emotion_trajectories,
        pacing_annotations=pacing_annotations,
        expression_channel_constraints=expression_channel_constraints,
        narration_tone_progression=narration_tone_progression,
    )
    return metadata.model_dump(mode="json")
