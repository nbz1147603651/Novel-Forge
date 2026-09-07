"""Independent spoken-text rewrite step for dubbing scripts.

This module provides a dedicated LLM-powered rewrite pass that converts
literary narration/dialogue into TTS-friendly spoken text.  It runs after
the script structure is finalized and uses semantic validation (not the
legacy substring-containment check) to accept or reject rewrites.

Author: novel-forge
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any, Callable

from novel_forge.common.constants import TaskType
from novel_forge.core.config import Settings
from novel_forge.gateway.router import ModelRouter
from novel_forge.obs.logger import get_logger
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.tts.schemas import (
    DubbingScript,
    DubbingSegment,
    SegmentType,
    VoiceTeamContract,
)
from novel_forge.tts.script_integrity import refresh_segment_uid
from novel_forge.tts.script_stage_context import (
    ContextProjectionLimits,
    ScriptContextStage,
    ScriptStageContext,
)

_log = get_logger(__name__)

StepEventCallback = Callable[[str, dict[str, Any]], None]

# Segment types eligible for spoken-text rewrite.
_REWRITABLE_TYPES = frozenset(
    {
        SegmentType.NARRATION,
        SegmentType.DIALOGUE,
        SegmentType.INNER_THOUGHT,
    }
)

# Pattern to strip non-word characters for core-text comparison.
_CORE_STRIP_RE = re.compile(r"[\s\W_]+", re.UNICODE)

# Pattern to detect HTML/Markdown tags that must never appear in spoken text.
_DANGEROUS_TAG_RE = re.compile(r"<[^>]+>|\[/?[a-z_]+\]", re.IGNORECASE)
_NUMBER_RE = re.compile(r"[+-]?\d+(?:[.,．]\d+)?")

# Pattern to detect text that is purely non-speakable (scene breaks, separators).
# Matches strings consisting entirely of dashes, asterisks, dots, underscores,
# whitespace, or common scene-break symbols with no CJK or Latin word chars.
_PURE_NON_SPEAKABLE_RE = re.compile(
    "^[\\s\\-\u2014\u2013_*\u00b7\u2022.\u2026\u3002\u3001\uff0c,;\uff1b:\uff1a"
    "!\uff01?\uff1f~\uff5e#|/\\\\()\uff08\uff09\\[\\]\u3010\u3011{}<>"
    "\u201c\u201d\u2018\u2019\u200b-\u200f\ufeff]+$"
)

# Valid MiniMax pause marker: <#0.6#> (duration in seconds, decimals allowed).
# Markers must survive sanitization so deterministic pause injection (see
# tts/pipeline/pause_marker_injection.py) is never destroyed by the fallback.
_PAUSE_MARKER_RE = re.compile(r"<#\d+(?:\.\d+)?#>")
# Placeholder used to protect markers while sanitization patterns run.
_PAUSE_PLACEHOLDER_PREFIX = "\u0000PAUSE"
_PAUSE_PLACEHOLDER_SUFFIX = "\u0000"


def _is_pure_non_speakable(text: str) -> bool:
    """Return True if text contains no speakable content (scene break / separator).

    A segment whose text is purely dashes, asterisks, dots, or other
    non-vocalizable punctuation should never be sent to TTS.  This catches
    patterns like ``---``, ``——``, ``***``, ``···``, etc.
    """
    stripped = text.strip()
    if not stripped:
        return True
    # Fast path: if there's any CJK or Latin alphanumeric char, it's speakable.
    if re.search(r"[\w\u3400-\u9fff]", stripped, re.UNICODE):
        return False
    return bool(_PURE_NON_SPEAKABLE_RE.match(stripped))

# ── Rule-based sanitization for non-speakable characters ─────────────────────
# These patterns match characters/sequences that TTS engines cannot vocalize
# and must be converted to speakable equivalents or removed.
_NON_SPEAKABLE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Em-dash / en-dash sequences → comma pause
    (re.compile(r"——+|—+|--+"), "，"),
    # Square brackets / asterisks (stage directions, markdown)
    (re.compile(r"\[[^\]]*\]"), ""),
    (re.compile(r"\*[^*]*\*"), ""),
    # Chinese full-width brackets【】（stage directions）
    (re.compile(r"【[^】]*】"), ""),
    # Ellipsis (4+ dots) → short pause comma
    (re.compile(r"[.．…。]{4,}"), "，"),
    # Standard ellipsis → keep a single ellipsis char (TTS engines pause on
    # it; the pause-marker injection phase converts it into <#1.2#> for
    # MiniMax instead of losing the dramatic pause to a comma).
    (re.compile(r"…{2,}|\.{3}"), "…"),
    # Angle brackets (internal annotations)
    (re.compile(r"<[^>]*>"), ""),
    # Curly braces (template variables)
    (re.compile(r"\{[^}]*\}"), ""),
    # Parenthetical stage directions in Chinese: （动作描写）
    (re.compile(r"（[^）]{0,20}(?:笑|哭|叹|顿|停|转身|低头|抬头|握|摩挲)[^）]*）"), ""),
    # Isolated special unicode symbols
    (re.compile(r"[◆◇★☆●○■□▲△▼▽→←↑↓]"), ""),
]


def sanitize_for_speech(text: str) -> str:
    """Apply rule-based minimal rewrite to make text speakable.

    This is a zero-LLM-cost deterministic fallback that:
    1. Replaces em-dashes with comma pauses
    2. Removes stage directions in brackets/asterisks
    3. Normalizes excessive ellipsis to a single ellipsis char
    4. Strips HTML/Markdown tags
    5. Collapses redundant whitespace and punctuation

    MiniMax pause markers (``<#0.6#>``) are extracted and restored around the
    sanitization so they are never stripped by the angle-bracket rule.

    Used when the LLM rewrite phase fails entirely, ensuring spoken_text
    is never empty and never contains non-vocalizable characters.
    """
    if not text or not text.strip():
        return text

    result = text.strip()

    # Protect existing pause markers from the tag-stripping rules below.
    protected: list[str] = []

    def _protect(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return f"{_PAUSE_PLACEHOLDER_PREFIX}{len(protected) - 1}{_PAUSE_PLACEHOLDER_SUFFIX}"

    result = _PAUSE_MARKER_RE.sub(_protect, result)

    for pattern, replacement in _NON_SPEAKABLE_PATTERNS:
        result = pattern.sub(replacement, result)

    # Collapse multiple consecutive commas/periods into one
    result = re.sub(r"[，,]{2,}", "，", result)
    result = re.sub(r"[。.]{2,}", "。", result)
    # Remove leading/trailing punctuation artifacts
    result = result.strip("，。、；： ")
    # Collapse multiple spaces
    result = re.sub(r"\s{2,}", " ", result)

    # Restore protected pause markers (index-based so order is preserved).
    for index, marker in enumerate(protected):
        result = result.replace(
            f"{_PAUSE_PLACEHOLDER_PREFIX}{index}{_PAUSE_PLACEHOLDER_SUFFIX}",
            marker,
        )

    return result


def _setting_value(settings: object, name: str) -> Any:
    """Read a setting while retaining compatibility with settings-like tests."""

    if hasattr(settings, name):
        return getattr(settings, name)
    return Settings.model_fields[name].default


@dataclass(frozen=True)
class SpokenRewritePolicy:
    """Runtime policy shared by candidate selection, prompt rules, and validation."""

    min_rewrite_length: int
    batch_size: int
    min_length_ratio: float
    max_length_ratio: float
    min_sequence_ratio: float
    min_confidence: float
    temperature: float
    context_window: int
    narration_sentence_chars: int
    dialogue_sentence_chars: int
    inner_thought_sentence_chars: int
    min_output_tokens: int
    output_tokens_per_segment: int
    platform_id: str = ""
    model_id: str = ""

    @classmethod
    def from_settings(cls, settings: object) -> "SpokenRewritePolicy":
        def value(name: str) -> Any:
            return _setting_value(settings, name)

        return cls(
            min_rewrite_length=max(1, int(value("tts_spoken_rewrite_min_length"))),
            batch_size=max(1, int(value("tts_spoken_rewrite_batch_size"))),
            min_length_ratio=float(value("tts_spoken_rewrite_min_length_ratio")),
            max_length_ratio=float(value("tts_spoken_rewrite_max_length_ratio")),
            min_sequence_ratio=float(value("tts_spoken_rewrite_min_sequence_ratio")),
            min_confidence=float(value("tts_spoken_rewrite_min_confidence")),
            temperature=float(value("tts_spoken_rewrite_temperature")),
            context_window=max(0, int(value("tts_spoken_rewrite_context_window"))),
            narration_sentence_chars=int(value("tts_spoken_rewrite_narration_sentence_chars")),
            dialogue_sentence_chars=int(value("tts_spoken_rewrite_dialogue_sentence_chars")),
            inner_thought_sentence_chars=int(
                value("tts_spoken_rewrite_inner_thought_sentence_chars")
            ),
            min_output_tokens=int(value("tts_spoken_rewrite_min_output_tokens")),
            output_tokens_per_segment=int(value("tts_spoken_rewrite_output_tokens_per_segment")),
        )

    def prompt_card(self) -> dict[str, int | float]:
        return {
            "min_rewrite_length": self.min_rewrite_length,
            "min_length_ratio": self.min_length_ratio,
            "max_length_ratio": self.max_length_ratio,
            "min_confidence": self.min_confidence,
            "narration_sentence_chars": self.narration_sentence_chars,
            "dialogue_sentence_chars": self.dialogue_sentence_chars,
            "inner_thought_sentence_chars": self.inner_thought_sentence_chars,
        }


def _strip_core(text: str) -> str:
    """Remove whitespace and punctuation for semantic comparison."""
    return _CORE_STRIP_RE.sub("", text)


def validate_spoken_rewrite(
    original: str,
    rewritten: str,
    *,
    min_length_ratio: float = 0.5,
    max_length_ratio: float = 1.3,
    min_sequence_ratio: float = 0.35,
    required_terms: Collection[str] = (),
) -> tuple[bool, str]:
    """Validate a spoken-text rewrite against semantic alignment rules.

    Returns (passed, reason).  When *passed* is False, *reason* explains why.

    Args:
        original: The original literary text.
        rewritten: The proposed spoken-text rewrite.
        min_length_ratio: Minimum ratio of rewritten/original core length.
        max_length_ratio: Maximum ratio of rewritten/original core length.
        min_sequence_ratio: Minimum SequenceMatcher ratio for semantic skeleton.
        required_terms: Source-anchored terms, such as cast character names,
            that must remain verbatim in a non-empty rewrite.

    Rules:
    1. Empty rewrite is always accepted (means "no rewrite needed").
    2. Length ratio between rewritten and original core text must be in
       [min_length_ratio, max_length_ratio].
    3. SequenceMatcher ratio must be >= min_sequence_ratio.
    4. No dangerous HTML/Markdown tags in the rewritten text.
    5. Every non-empty required term present in the original must be preserved.
    """
    # Rule 0: empty rewrite = no rewrite needed, always pass.
    if not rewritten or not rewritten.strip():
        return True, ""

    rewritten = rewritten.strip()

    # Rule 4: no dangerous tags.
    if _DANGEROUS_TAG_RE.search(rewritten):
        return False, "contains_html_or_markdown_tags"

    original_core = _strip_core(original)
    rewritten_core = _strip_core(rewritten)

    if not original_core:
        return False, "original_text_empty"
    if not rewritten_core:
        return False, "rewritten_text_empty_after_strip"

    # Rule 2: length ratio.
    ratio = len(rewritten_core) / len(original_core)
    if ratio < min_length_ratio:
        return False, f"too_short_ratio={ratio:.2f}"
    if ratio > max_length_ratio:
        return False, f"too_long_ratio={ratio:.2f}"

    # Rule 3: sequence similarity.
    seq_ratio = difflib.SequenceMatcher(None, original_core, rewritten_core).ratio()
    if seq_ratio < min_sequence_ratio:
        return False, f"sequence_ratio_too_low={seq_ratio:.2f}"

    missing_terms = sorted(
        {
            term.strip()
            for term in required_terms
            if term and term.strip() in original and term.strip() not in rewritten
        }
    )
    if missing_terms:
        return False, f"missing_required_terms={','.join(missing_terms)}"

    return True, ""


def _build_rewrite_stage_cards(
    script: DubbingScript,
    batch: list[tuple[int, DubbingSegment]],
    voice_team: VoiceTeamContract,
    context: ScriptStageContext,
    policy: SpokenRewritePolicy,
    limits: ContextProjectionLimits,
) -> dict[str, Any]:
    """Build a compact card set with per-segment evidence and stage context."""

    batch_segments = [segment for _, segment in batch]
    batch_positions = {position for position, _ in batch}
    focus_text = "\n".join(segment.text for segment in batch_segments)
    focus_speakers = {
        value
        for segment in batch_segments
        for value in (segment.character_id, segment.character_name)
        if value
    }
    cards = context.project(
        ScriptContextStage.SPOKEN_REWRITE,
        batch_segments,
        limits=limits,
    )

    # Load platform-specific rewrite profile for template injection.
    from novel_forge.tts.platform.rewrite_profiles import load_rewrite_profile

    rewrite_profile = load_rewrite_profile(policy.platform_id)

    # Build a gender lookup from voice_team for segment card enrichment.
    gender_lookup: dict[str, str] = {}
    for entry in voice_team.entries:
        if entry.character_id and entry.character_gender:
            gender_lookup[entry.character_id] = entry.character_gender
        if entry.character_name and entry.character_gender:
            gender_lookup[entry.character_name] = entry.character_gender

    cards.update(
        {
            "segments": [
                _segment_rewrite_card(
                    script,
                    position,
                    segment,
                    policy.context_window,
                    batch_positions,
                    gender_lookup=gender_lookup,
                )
                for position, segment in batch
            ],
            "voice_team": [
                {
                    "character_id": entry.character_id,
                    "character_name": entry.character_name,
                    "gender": entry.character_gender,
                    "age": entry.character_age,
                    "role": entry.character_role,
                }
                for entry in voice_team.entries
                if entry.character_id
                and (
                    entry.character_id in focus_speakers
                    or entry.character_name in focus_speakers
                    or (entry.character_name and entry.character_name in focus_text)
                )
            ],
            "rewrite_policy": policy.prompt_card(),
            "rewrite_profile": rewrite_profile,
        }
    )
    return cards


def _segment_rewrite_card(
    script: DubbingScript,
    position: int,
    segment: DubbingSegment,
    context_window: int,
    batch_positions: Collection[int],
    *,
    gender_lookup: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Project one authoritative segment plus a bounded continuity window."""

    start = max(0, position - context_window)
    end = min(len(script.segments), position + context_window + 1)
    neighbors = []
    for neighbor_position in range(start, end):
        if neighbor_position == position or neighbor_position in batch_positions:
            continue
        item = script.segments[neighbor_position]
        neighbors.append(
            {
                "relative_position": neighbor_position - position,
                "segment_index": item.segment_index,
                "segment_type": item.segment_type.value,
                "character_name": item.character_name,
                "text": item.text,
            }
        )
    total_segments = max(1, len(script.segments))
    # Resolve character gender from voice_team lookup for identity-aware rewriting.
    character_gender = ""
    if gender_lookup:
        character_gender = gender_lookup.get(
            segment.character_id, ""
        ) or gender_lookup.get(segment.character_name, "")
    return {
        "segment_index": segment.segment_index,
        "segment_type": segment.segment_type.value,
        "character_id": segment.character_id,
        "character_name": segment.character_name or "旁白",
        "character_gender": character_gender,
        "emotion": segment.emotion.value,
        "sub_emotion": segment.sub_emotion.value if segment.sub_emotion else "",
        "emotion_intensity": segment.emotion_intensity,
        "tone_hint": segment.tone_hint,
        "scene_context": segment.scene_context,
        "narrator_distance": segment.narrator_distance,
        "language_code": segment.language_code,
        "vocal_intent": segment.vocal_direction.intent,
        "segment_position_ratio": round(position / total_segments, 3),
        "text": segment.text,
        "continuity_context": neighbors,
    }


def _parse_rewrite_batch_response(
    response: Any,
    expected_indices: Collection[int],
) -> tuple[dict[int, dict[str, Any]] | None, str]:
    """Validate the dynamic one-record-per-segment response invariant."""

    if not isinstance(response, dict):
        return None, "response_not_object"
    if set(response) != {"rewrites"}:
        return None, "unexpected_top_level_keys"

    rewrites = response.get("rewrites")
    if not isinstance(rewrites, list):
        return None, "rewrites_not_list"

    expected = set(expected_indices)
    if len(rewrites) != len(expected):
        return None, "rewrite_count_mismatch"

    result: dict[int, dict[str, Any]] = {}
    required_keys = {"segment_index", "spoken_text", "confidence"}
    for item in rewrites:
        if not isinstance(item, dict) or set(item) != required_keys:
            return None, "invalid_rewrite_record_shape"
        index = item.get("segment_index")
        if type(index) is not int or index not in expected:
            return None, "unexpected_segment_index"
        if index in result:
            return None, "duplicate_segment_index"
        if not isinstance(item.get("spoken_text"), str):
            return None, "spoken_text_not_string"
        confidence = item.get("confidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0.0 <= float(confidence) <= 1.0
        ):
            return None, "confidence_out_of_range"
        result[index] = item

    if set(result) != expected:
        return None, "segment_coverage_mismatch"
    return result, ""


async def rewrite_spoken_text(
    script: DubbingScript,
    voice_team: VoiceTeamContract,
    *,
    router: ModelRouter,
    builder: PromptBuilder,
    settings: Settings,
    style_profile: dict[str, Any] | None = None,
    context: ScriptStageContext | None = None,
    platform_id: str = "",
    chapter_number: int = 0,
    on_step: StepEventCallback | None = None,
    previous_script: DubbingScript | None = None,
) -> DubbingScript:
    """Run the independent spoken-text rewrite pass on a finalized script.

    For each rewritable segment with text longer than the minimum threshold,
    this function calls the LLM to produce a TTS-friendly spoken version.
    Each rewrite is validated with :func:`validate_spoken_rewrite` before
    being written to ``segment.spoken_text``.

    When *previous_script* is provided, incremental mode is activated:
    segments whose ``segment_uid`` matches an entry in the previous script
    AND already have a ``spoken_text`` will be reused without re-calling
    the LLM, significantly reducing token cost on script regeneration.

    Args:
        script: The finalized dubbing script.
        voice_team: Voice team contract for character context.
        router: Model router for LLM calls.
        builder: Prompt builder for template rendering.
        settings: Application settings (temperature, etc.).
        style_profile: Legacy style-only context, used when ``context`` is absent.
        context: Upstream script context; projected separately for this step.
        platform_id: Target TTS provider ID (e.g. "minimax", "bailian") for
            platform-specific rewrite guidance.  Empty string uses generic rules.
        on_step: Optional step-event callback for progress reporting.
        previous_script: Prior script for incremental reuse of spoken_text.

    Returns:
        Updated DubbingScript with spoken_text populated where rewrites pass.
    """
    context = context or ScriptStageContext(style_profile=style_profile or {})
    # Ensure chapter_number is available in context for narrative-position projection.
    if chapter_number and context.chapter_number != chapter_number:
        import dataclasses as _dc

        context = _dc.replace(context, chapter_number=chapter_number)
    policy = SpokenRewritePolicy.from_settings(settings)
    if platform_id:
        import dataclasses

        policy = dataclasses.replace(policy, platform_id=platform_id)
    projection_limits = ContextProjectionLimits.from_settings(settings)

    # Mutable working copy of segments (used throughout this function).
    segments_list = list(script.segments)

    # Collect candidate segments.
    # Pre-filter: exclude segments whose text is purely non-speakable
    # (scene breaks like ---, ——, ***) — these must never reach TTS.
    non_speakable_positions: set[int] = set()
    candidates = []
    for position, seg in enumerate(script.segments):
        if seg.segment_type not in _REWRITABLE_TYPES:
            continue
        if _is_pure_non_speakable(seg.text):
            # Mark as non-speakable; set spoken_text to empty so synthesis
            # layer will skip this segment entirely.
            non_speakable_positions.add(position)
            continue
        if len(seg.text.strip()) >= policy.min_rewrite_length:
            candidates.append((position, seg))

    # Immediately blank out spoken_text for non-speakable segments.
    if non_speakable_positions:
        for pos in non_speakable_positions:
            seg = segments_list[pos]
            if seg.spoken_text != "":
                segments_list[pos] = seg.model_copy(update={"spoken_text": ""})
        _log.info(
            "Filtered %d non-speakable segments (scene breaks/separators)",
            len(non_speakable_positions),
        )

    # ── Incremental reuse: skip segments with unchanged uid + existing spoken_text ──
    reused_count = 0
    if previous_script is not None:
        prev_spoken_by_uid: dict[str, str] = {
            seg.segment_uid: seg.spoken_text
            for seg in previous_script.segments
            if seg.segment_uid and seg.spoken_text
        }
        remaining_candidates: list[tuple[int, DubbingSegment]] = []
        for position, seg in candidates:
            if seg.segment_uid and seg.segment_uid in prev_spoken_by_uid:
                # Reuse previous spoken_text without LLM call.
                segments_list[position] = seg.model_copy(
                    update={"spoken_text": prev_spoken_by_uid[seg.segment_uid]}
                )
                reused_count += 1
            else:
                remaining_candidates.append((position, seg))
        candidates = remaining_candidates
        if reused_count:
            _log.info(
                "Incremental rewrite: reused %d spoken_text from previous script",
                reused_count,
            )

    if not candidates:
        _log.info("No segments eligible for spoken-text rewrite")
        if on_step:
            on_step(
                "tts_spoken_rewrite_complete",
                {
                    "chapter": script.chapter_number,
                    "total_candidates": 0,
                    "rewritten": 0,
                    "rejected": 0,
                },
            )
        return script

    _log.info(
        "Spoken-text rewrite: %d candidate segments for chapter %d",
        len(candidates),
        script.chapter_number,
    )

    # Split into batches.
    batches: list[list[tuple[int, DubbingSegment]]] = [
        candidates[i : i + policy.batch_size] for i in range(0, len(candidates), policy.batch_size)
    ]

    rewritten_count = 0
    rejected_count = 0
    rejection_reasons: dict[str, int] = {}

    def reject(reason: str, count: int = 1) -> None:
        nonlocal rejected_count
        rejected_count += count
        key = str(reason or "unknown").split("=", maxsplit=1)[0]
        rejection_reasons[key] = rejection_reasons.get(key, 0) + count

    segments = segments_list  # Use pre-populated list (includes incremental reuse)
    cast_names = tuple(
        dict.fromkeys(
            entry.character_name.strip()
            for entry in voice_team.entries
            if entry.character_name and entry.character_name.strip()
        )
    )
    preserved_terms = tuple(dict.fromkeys((*cast_names, *context.preserved_terms())))

    # ── Concurrent batch execution with bounded parallelism ──────────────────
    # LLM calls are the bottleneck; batches are independent and can run in
    # parallel.  Result application remains sequential to avoid race conditions
    # on the shared segments list.
    import asyncio as _asyncio

    _max_concurrent = max(
        1,
        min(
            len(batches),
            int(getattr(settings, "tts_spoken_rewrite_max_concurrent", 2) or 2),
        ),
    )
    _semaphore = _asyncio.Semaphore(_max_concurrent)

    async def _call_batch(
        batch_idx: int,
        batch: list[tuple[int, DubbingSegment]],
    ) -> tuple[int, dict[int, dict[str, Any]] | None, str]:
        """Execute one LLM batch call. Returns (batch_idx, rewrite_map, error)."""
        async with _semaphore:
            stage_cards = _build_rewrite_stage_cards(
                script,
                batch,
                voice_team,
                context,
                policy,
                projection_limits,
            )

            if on_step:
                on_step(
                    "tts_spoken_rewrite_batch_start",
                    {
                        "chapter": script.chapter_number,
                        "batch_index": batch_idx + 1,
                        "batch_count": len(batches),
                        "segment_count": len(batch),
                    },
                )

            try:
                from novel_forge.model_runtime import StructuredModelService

                service = StructuredModelService(
                    router=router,
                    builder=builder,
                    on_step=on_step or (lambda _e, _d: None),
                    settings=settings,
                )
                response = await service.call_with_retry(
                    TaskType.TTS_REWRITE_SPOKEN_TEXT,
                    {"stage_cards": stage_cards},
                    max_tokens=max(
                        policy.min_output_tokens,
                        len(batch) * policy.output_tokens_per_segment,
                    ),
                    temperature=policy.temperature,
                    required_keys=("rewrites",),
                    max_retries=2,
                )
            except Exception as exc:
                _log.warning(
                    "Spoken-text rewrite LLM call failed for batch %d: %s",
                    batch_idx + 1,
                    exc,
                )
                if on_step:
                    on_step(
                        "tts_spoken_rewrite_batch_failed",
                        {
                            "chapter": script.chapter_number,
                            "batch_index": batch_idx + 1,
                            "error": str(exc),
                        },
                    )
                # Distinguish permanent route failures (e.g. no provider
                # supports native structured output) from transient errors
                # so the caller can skip futile retry rounds.
                from novel_forge.core.exceptions import ModelGatewayError  # noqa: PLC0415

                is_permanent = (
                    isinstance(exc, ModelGatewayError) and not exc.is_transient_error
                )
                return batch_idx, None, (
                    "llm_call_failed_permanent" if is_permanent else "llm_call_failed"
                )

            rewrite_map, format_reason = _parse_rewrite_batch_response(
                response,
                tuple(segment.segment_index for _, segment in batch),
            )
            if rewrite_map is None:
                _log.warning(
                    "Spoken-text rewrite format rejected for batch %d: %s",
                    batch_idx + 1,
                    format_reason,
                )
                if on_step:
                    on_step(
                        "tts_spoken_rewrite_batch_failed",
                        {
                            "chapter": script.chapter_number,
                            "batch_index": batch_idx + 1,
                            "error": format_reason,
                        },
                    )
                return batch_idx, None, format_reason

            return batch_idx, rewrite_map, ""

    # Launch all batches concurrently (bounded by semaphore).
    batch_results = await _asyncio.gather(
        *(_call_batch(idx, batch) for idx, batch in enumerate(batches))
    )

    # Apply results sequentially to preserve deterministic segment ordering.
    for batch_idx, rewrite_map, error in sorted(batch_results, key=lambda r: r[0]):
        batch = batches[batch_idx]
        if rewrite_map is None:
            reject(error, len(batch))
            continue

        # Apply validated rewrites.
        for position, seg in batch:
            rewrite_entry = rewrite_map.get(seg.segment_index)
            if rewrite_entry is None:
                reject("missing_segment_record")
                continue

            spoken = rewrite_entry["spoken_text"].strip()
            confidence = float(rewrite_entry["confidence"])

            # Post-validation: reject spoken_text that is purely non-speakable.
            # This catches LLM outputs like "---" or "——" that slipped through.
            if spoken and _is_pure_non_speakable(spoken):
                reject("spoken_text_pure_non_speakable")
                # Ensure segment has empty spoken_text so synthesis skips it.
                segments[position] = seg.model_copy(update={"spoken_text": ""})
                continue

            # Empty spoken_text = LLM decided no rewrite needed.
            if not spoken:
                # Output contract: every rewritable segment must have non-empty
                # spoken_text. When LLM judges no rewrite is needed, the original
                # text is a valid spoken form (TTS reads it verbatim).
                # BUT: if original text itself is non-speakable, use empty string.
                if _is_pure_non_speakable(seg.text):
                    segments[position] = seg.model_copy(update={"spoken_text": ""})
                elif not (seg.spoken_text and seg.spoken_text.strip()):
                    segments[position] = seg.model_copy(
                        update={"spoken_text": seg.text}
                    )
                continue
            if spoken == seg.text.strip():
                # Exact no-op should use the empty decision form and must not
                # invalidate the segment or inflate rewrite statistics.
                if not (seg.spoken_text and seg.spoken_text.strip()):
                    segments[position] = seg.model_copy(
                        update={"spoken_text": seg.text}
                    )
                continue
            if confidence < policy.min_confidence:
                reject("confidence_below_threshold")
                continue

            passed, reason = validate_spoken_rewrite(
                seg.text,
                spoken,
                min_length_ratio=policy.min_length_ratio,
                max_length_ratio=policy.max_length_ratio,
                min_sequence_ratio=policy.min_sequence_ratio,
                required_terms=(*preserved_terms, *_NUMBER_RE.findall(seg.text)),
            )
            if passed:
                # ``segment_index`` is a durable external identifier, not a
                # Python list offset. Imported/resumed scripts may have gaps
                # after segments were removed or merged.
                segments[position] = refresh_segment_uid(
                    seg.model_copy(update={"spoken_text": spoken})
                )
                rewritten_count += 1
            else:
                _log.debug(
                    "Spoken rewrite rejected for segment %d: %s",
                    seg.segment_index,
                    reason,
                )
                reject(reason)

    # Update metadata with rewrite statistics.
    metadata = dict(script.metadata)
    rewrite_meta: dict[str, Any] = {
        "total_candidates": len(candidates),
        "rewritten": rewritten_count,
        "rejected": rejected_count,
        "batch_count": len(batches),
        "temperature": policy.temperature,
        "context_projection": ScriptContextStage.SPOKEN_REWRITE.value,
        "rejection_reasons": dict(sorted(rejection_reasons.items())),
    }
    if context.reference_style_profile is not None and context.reference_style_strength > 0.0:
        rewrite_meta["reference_style"] = {
            "profile_id": context.reference_style_profile.profile_id,
            "source_hash": context.reference_style_profile.source_hash,
            "strength": round(context.reference_style_strength, 3),
            "analysis_mode": context.reference_style_profile.analysis_mode,
        }
    # Propagate permanent route failure so phase_rewrite can early-exit
    # and degrade gracefully instead of burning futile retry rounds.
    if rejection_reasons.get("llm_call_failed_permanent", 0) > 0:
        rewrite_meta["permanent_route_failure"] = True
    metadata["spoken_text_rewrite"] = rewrite_meta

    if on_step:
        on_step(
            "tts_spoken_rewrite_complete",
            {
                "chapter": script.chapter_number,
                "total_candidates": len(candidates) + reused_count,
                "rewritten": rewritten_count,
                "rejected": rejected_count,
                "reused": reused_count,
            },
        )

    _log.info(
        "Spoken-text rewrite complete: %d rewritten, %d rejected, %d reused out of %d candidates",
        rewritten_count,
        rejected_count,
        reused_count,
        len(candidates) + reused_count,
    )

    return script.model_copy(update={"segments": segments, "metadata": metadata})
