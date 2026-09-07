"""Extract verified expression-channel observations for semantic memory."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from novel_forge.core.constants import TaskType
from novel_forge.core.review.review_contracts import source_text_hash
from novel_forge.editorial.schemas import normalize_expression_channel_profiles
from novel_forge.narrative_state.schemas import (
    ExpressionObservation,
    ExpressionObservationExtractionResult,
)
from novel_forge.pipeline.steps.base import PipelineStep
from novel_forge.pipeline.token_budget import route_max_output_budget

_MAX_PROFILES = 8
_MAX_OBSERVATIONS = 12
_MIN_CONFIDENCE = 0.55


@dataclass(frozen=True)
class ExpressionObservationInput:
    """Inputs for EXTRACT_EXPRESSION_OBSERVATIONS."""

    chapter_number: int
    chapter_text: str
    expression_channel_profiles: list[dict[str, Any]] = field(default_factory=list)
    scene_intents: list[dict[str, Any]] = field(default_factory=list)
    pov_character: str = ""


class ExpressionObservationStep(
    PipelineStep[ExpressionObservationInput, ExpressionObservationExtractionResult]
):
    """Use a bounded LLM task, then locally verify quotes before indexing."""

    @property
    def step_name(self) -> str:
        return "extract_expression_observations"

    async def _execute(
        self,
        input_data: ExpressionObservationInput,
    ) -> ExpressionObservationExtractionResult:
        text = str(input_data.chapter_text or "")
        profiles = normalize_expression_channel_profiles(
            input_data.expression_channel_profiles,
            max_items=_MAX_PROFILES,
            min_confidence=0.0,
        )
        text_hash = source_text_hash(text)
        if not text.strip() or not profiles:
            return ExpressionObservationExtractionResult(
                observations=[],
                skipped_reason="missing_text_or_profiles",
                source_text_hash=text_hash,
            )

        payload = await self._call_with_retry(
            TaskType.EXTRACT_EXPRESSION_OBSERVATIONS,
            {
                "chapter_number": input_data.chapter_number,
                "chapter_text": text,
                "expression_channel_profiles": profiles,
                "scene_intents": list(input_data.scene_intents or []),
                "pov_character": input_data.pov_character,
            },
            max_tokens=route_max_output_budget(
                self._router,
                TaskType.EXTRACT_EXPRESSION_OBSERVATIONS,
                min_tokens=2048,
            ),
            temperature=0.15,
            required_keys=("observations", "skipped_reason", "source_text_hash"),
        )
        payload_dict = payload if isinstance(payload, dict) else {}
        observations = _verified_observations(
            payload_dict.get("observations", []),
            chapter_number=input_data.chapter_number,
            chapter_text=text,
            source_hash=text_hash,
            profiles=profiles,
            pov_character=input_data.pov_character,
        )
        return ExpressionObservationExtractionResult(
            observations=observations,
            skipped_reason=str(payload_dict.get("skipped_reason") or ""),
            source_text_hash=text_hash,
        )


def _verified_observations(
    raw_observations: Any,
    *,
    chapter_number: int,
    chapter_text: str,
    source_hash: str,
    profiles: list[dict[str, Any]],
    pov_character: str,
) -> list[ExpressionObservation]:
    if not isinstance(raw_observations, list | tuple):
        return []
    profile_by_id = {str(item.get("channel_id") or ""): item for item in profiles}
    verified: list[ExpressionObservation] = []
    seen: set[tuple[str, str, int]] = set()
    for raw in list(raw_observations)[: _MAX_OBSERVATIONS * 2]:
        if not isinstance(raw, dict):
            continue
        channel_id = str(raw.get("channel_id") or "").strip()
        profile = profile_by_id.get(channel_id)
        if profile is None:
            continue
        confidence = _safe_float(raw.get("confidence"), 0.0)
        if confidence < _MIN_CONFIDENCE:
            continue
        quote = _clean_quote(raw.get("quote"))
        if not quote:
            continue
        start = chapter_text.find(quote)
        if start < 0 or chapter_text.find(quote, start + len(quote)) >= 0:
            continue
        end = start + len(quote)
        scene_index = max(0, _safe_int(raw.get("scene_index"), 0))
        key = (channel_id, quote, start)
        if key in seen:
            continue
        seen.add(key)
        observation = ExpressionObservation(
            chapter_number=chapter_number,
            scene_index=scene_index,
            quote=quote,
            source_text_hash=source_hash,
            source_start=start,
            source_end=end,
            channel_id=channel_id,
            channel=str(profile.get("channel") or raw.get("channel") or "other"),
            profile_label=str(profile.get("label") or raw.get("profile_label") or ""),
            trigger_context=_clip_text(raw.get("trigger_context"), 32),
            semantic_role=_clip_text(raw.get("semantic_role"), 48),
            pov_character=_clip_text(raw.get("pov_character") or pov_character, 32),
            actor=_clip_text(raw.get("actor"), 32),
            confidence=confidence,
        )
        verified.append(observation)
        if len(verified) >= _MAX_OBSERVATIONS:
            break
    return verified


def _clean_quote(value: Any) -> str:
    text = str(value or "").strip().strip("\"'“”‘’")
    if len(text) < 2 or len(text) > 80:
        return ""
    return text


def _clip_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip()


def _safe_float(value: Any, default: float) -> float:
    try:
        return max(0.0, min(float(value), 1.0))
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
