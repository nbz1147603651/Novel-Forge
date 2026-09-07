"""Phase 5.6: LLM emotion fine-labeling with deterministic validation.

Runs after the concurrent review+rewrite merge and before pause-marker
injection, so onomatopoeia tags (injected together with pause markers) and
vocal-direction enrichment can consume the fine-labeled emotion signals.

Design (aligned with the "deterministic first, LLM last" philosophy):

- LLM annotates a continuous window (5-8 segments) with scene context, then
  labels each segment with ``emotion`` / ``sub_emotion`` / ``intensity``.
- Every LLM label passes a deterministic validation gate: unknown emotion
  values fall back to keyword inference (:func:`infer_dominant_emotion`),
  unknown sub_emotions are dropped, and intensity is clamped to [0.0, 1.0].
- When the LLM call fails or returns no usable labels, the segment keeps its
  pre-existing labels (keyword inference is already the generation baseline),
  so this phase can never regress expression metadata.

Author: novel-forge
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from novel_forge.common.constants import TaskType
from novel_forge.obs.logger import get_logger
from novel_forge.tts.emotion_inference import infer_dominant_emotion
from novel_forge.tts.schemas import EmotionTag, SegmentType

if TYPE_CHECKING:
    from novel_forge.tts.pipeline.generate_script_step import (
        GenerateDubbingScriptInput,
        GenerateDubbingScriptStep,
    )
    from novel_forge.tts.schemas import DubbingScript, DubbingSegment
    from novel_forge.tts.script_stage_context import ScriptStageContext

_log = get_logger("tts.pipeline.script_phases.emotion_label")

_LABELABLE_TYPES = frozenset(
    {SegmentType.NARRATION, SegmentType.DIALOGUE, SegmentType.INNER_THOUGHT}
)

# 确定性校验使用的合法情绪值集合（EmotionTag 全部取值）。
_VALID_EMOTIONS: frozenset[str] = frozenset(tag.value for tag in EmotionTag)


def _window_batches(segments: list[Any], window: int) -> list[list[tuple[int, Any]]]:
    """Split labelable segments into continuous windows of up to *window* items.

    Non-labelable segments (BGM/SFX/SILENCE) break windows so the LLM never
    sees a batch straddling a sound-design cue.
    """
    batches: list[list[tuple[int, Any]]] = []
    current: list[tuple[int, Any]] = []
    for position, segment in enumerate(segments):
        if segment.segment_type not in _LABELABLE_TYPES:
            if current:
                batches.append(current)
                current = []
            continue
        current.append((position, segment))
        if len(current) >= window:
            batches.append(current)
            current = []
    if current:
        batches.append(current)
    return batches


def _segment_label_card(segment: Any, position: int, script: Any) -> dict[str, Any]:
    """Compact per-segment card for the LLM (window + scene context)."""
    return {
        "segment_index": segment.segment_index,
        "segment_type": segment.segment_type.value,
        "character_name": segment.character_name or "旁白",
        "emotion": segment.emotion.value,
        "emotion_intensity": segment.emotion_intensity,
        "sub_emotion": segment.sub_emotion.value if segment.sub_emotion else "",
        "scene_context": segment.scene_context,
        "text": segment.text,
        "position_ratio": round(position / max(1, len(script.segments)), 3),
    }


def _build_stage_cards(
    script: DubbingScript,
    batch: list[tuple[int, Any]],
    ctx: ScriptStageContext,
    limits: Any,
) -> dict[str, Any]:
    """Project upstream context plus the current window's segment cards."""
    from novel_forge.tts.script_stage_context import ScriptContextStage  # noqa: PLC0415

    batch_segments = [segment for _, segment in batch]
    cards = ctx.project(
        ScriptContextStage.EMOTION_LABEL,
        batch_segments,
        limits=limits,
    )
    cards["segments"] = [
        _segment_label_card(segment, position, script) for position, segment in batch
    ]
    return cards


def _coerce_emotion(value: Any, fallback_text: str) -> EmotionTag | None:
    """Validate an LLM emotion value; fall back to keyword inference.

    Returns ``None`` when the value is invalid AND keyword inference also
    yields neutral — the caller then keeps the pre-existing label.
    """
    if value is None:
        return None
    raw = str(value).strip().lower()
    if raw in _VALID_EMOTIONS:
        return EmotionTag(raw)
    # 未知值 → 确定性回退：关键词推断（对齐"确定性优先、LLM 兜底"分层）。
    inferred = infer_dominant_emotion(fallback_text)
    if inferred != EmotionTag.NEUTRAL:
        return inferred
    return None


def _coerce_sub_emotion(value: Any) -> EmotionTag | None:
    if value is None:
        return None
    raw = str(value).strip().lower()
    if not raw or raw in ("none", "null", "neutral"):
        return None
    if raw in _VALID_EMOTIONS:
        return EmotionTag(raw)
    return None


def _coerce_intensity(value: Any, fallback: float) -> float:
    try:
        intensity = float(value)
    except (TypeError, ValueError):
        return fallback
    # 越界钳制。
    return max(0.0, min(1.0, intensity))


def _parse_label_response(
    response: Any,
    batch: list[tuple[int, Any]],
) -> dict[int, dict[str, Any]] | None:
    """Parse the LLM batch response into segment_index → validated labels.

    Returns ``None`` when the response lacks a usable ``emotions`` list.
    """
    content = getattr(response, "content", None)
    if content is None:
        return None
    emotions = None
    if isinstance(content, dict):
        emotions = content.get("emotions")
    if not isinstance(emotions, list):
        return None
    batch_by_index = {segment.segment_index: (position, segment) for position, segment in batch}
    labels: dict[int, dict[str, Any]] = {}
    for item in emotions:
        if not isinstance(item, dict):
            continue
        raw_index = item.get("segment_index")
        if raw_index is None:
            continue
        try:
            segment_index = int(raw_index)
        except (TypeError, ValueError):
            continue
        pair = batch_by_index.get(segment_index)
        if pair is None:
            continue
        _position, segment = pair
        fallback_text = str(getattr(segment, "spoken_text", "") or segment.text)
        emotion = _coerce_emotion(item.get("emotion"), fallback_text)
        if emotion is None:
            # 无有效标签可写：跳过该段，保留原值。
            continue
        sub_emotion = _coerce_sub_emotion(item.get("sub_emotion"))
        if sub_emotion is not None and sub_emotion == emotion:
            sub_emotion = None
        intensity = _coerce_intensity(item.get("intensity"), segment.emotion_intensity)
        labels[segment_index] = {
            "emotion": emotion,
            "sub_emotion": sub_emotion,
            "emotion_intensity": intensity,
        }
    return labels


async def phase_emotion_label(
    script: DubbingScript,
    input_data: GenerateDubbingScriptInput,
    script_stage_context: ScriptStageContext,
    step: GenerateDubbingScriptStep,
) -> DubbingScript:
    """Execute Phase 5.6: LLM emotion fine-labeling with deterministic fallback.

    Args:
        script: The merged script from the concurrent review+rewrite phase.
        input_data: Generation input (carries chapter_number, voice_team).
        script_stage_context: Stage context for prompt projection.
        step: Step instance for LLM infrastructure and settings.

    Returns:
        Script with fine-labeled emotion/sub_emotion/intensity; label stats
        are attached to ``metadata["emotion_label"]``.
    """
    settings = step._settings
    if not bool(getattr(settings, "tts_emotion_label_enabled", True)):
        return script
    window = int(getattr(settings, "tts_emotion_label_window", 6))

    from novel_forge.tts.script_stage_context import (  # noqa: PLC0415
        ContextProjectionLimits,
    )

    limits = ContextProjectionLimits.from_settings(settings)
    batches = _window_batches(list(script.segments), window)
    if not batches:
        return script

    from novel_forge.model_runtime import (  # noqa: PLC0415
        StructuredModelService,
    )

    service = StructuredModelService(
        router=step._router,
        builder=step._builder,
        on_step=step._on_step_event,
        settings=settings,
    )

    segments_by_index = {segment.segment_index: segment for segment in script.segments}
    updated: dict[int, DubbingSegment] = {}
    labeled_count = 0
    fallback_count = 0
    batch_count = len(batches)

    # Concurrent batch labeling with bounded parallelism: LLM calls dominate
    # the phase latency, so independent windows run in parallel up to the
    # configured cap.  Result application stays sequential on the caller side
    # to keep segment updates race-free.
    max_concurrent = max(
        1,
        min(
            batch_count,
            int(getattr(settings, "tts_emotion_label_max_concurrent", 2) or 2),
        ),
    )
    _semaphore = asyncio.Semaphore(max_concurrent)

    async def _label_batch(
        batch_idx: int,
        batch: list[tuple[int, DubbingSegment]],
    ) -> tuple[int, dict[int, dict[str, Any]], int]:
        """Label one window batch. Returns (batch_idx, labels, fallback_count)."""
        async with _semaphore:
            stage_cards = _build_stage_cards(script, batch, script_stage_context, limits)
            try:
                response = await service.call_with_retry(
                    TaskType.TTS_EMOTION_LABEL,
                    {"stage_cards": stage_cards},
                    max_tokens=max(512, len(batch) * 96),
                    temperature=0.3,
                    required_keys=("emotions",),
                    max_retries=1,
                )
            except Exception as exc:
                _log.warning(
                    "Emotion-label LLM call failed for batch %d/%d: %s",
                    batch_idx + 1,
                    batch_count,
                    exc,
                )
                step._on_step_event(
                    "tts_emotion_label_batch_failed",
                    {"chapter": input_data.chapter_number, "error": str(exc)},
                )
                return batch_idx, {}, len(batch)

            labels = _parse_label_response(response, batch)
            if labels is None:
                return batch_idx, {}, len(batch)
            return batch_idx, labels, 0

    batch_results = await asyncio.gather(
        *(_label_batch(index, batch) for index, batch in enumerate(batches))
    )
    for _batch_idx, labels, fallback in batch_results:
        fallback_count += fallback
        for segment_index, label in labels.items():
            segment = segments_by_index.get(segment_index)
            if segment is None or segment_index in updated:
                continue
            updated[segment_index] = segment.model_copy(update=label)
            labeled_count += 1

    if not updated:
        _log.info("Emotion label: no segments updated (chapter %d)", input_data.chapter_number)
        return script

    segments = [updated.get(segment.segment_index, segment) for segment in script.segments]
    metadata = dict(script.metadata)
    metadata["emotion_label"] = {
        "enabled": True,
        "window": window,
        "labeled_segments": labeled_count,
        "fallback_segments": fallback_count,
        "total_segments": len(segments),
    }
    _log.info(
        "Emotion label: %d/%d segments labeled for chapter %d",
        labeled_count,
        len(segments),
        input_data.chapter_number,
    )
    return script.model_copy(update={"segments": segments, "metadata": metadata})
