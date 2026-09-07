"""Deterministic pause-marker injection for MiniMax TTS.

This module adds MiniMax-native ``<#X#>`` pause markers to segment
``spoken_text`` after the spoken-text rewrite phase completes.  The rules
and marker durations are adopted from the reference audiobook skill
(``Reference/audiobook/phases/02-rewriting.md`` L34-L66) where the same
syntax was battle-tested in production.

Design principles:

- **Deterministic first, LLM last**: pause markers are computed from
  punctuation / paragraph / dialogue structure by pure rules.  No LLM call
  is involved, so injection is zero-cost and reproducible.
- **Idempotent**: existing ``<#X#>`` markers are stripped before re-injection,
  so re-running the pass (retry rounds, incremental regeneration) never
  duplicates markers.
- **Source-text safe**: only ``spoken_text`` is touched; the original
  ``text`` field and ``paralinguistic_tags`` are left unchanged.  The
  synthesis layer already passes ``synthesis_text`` through verbatim for
  MiniMax, so injected markers reach the TTS engine untouched.

Marker duration constants (reference scene table):

+------------------------------------+----------------------+
| Scenario                           | Marker               |
+====================================+======================+
| Sentence end (full stop)           | ``<#0.6#>``          |
+------------------------------------+----------------------+
| Exclamation / question mark        | ``<#0.4#>``          |
+------------------------------------+----------------------+
| Long-sentence comma                | ``<#0.25#>``         |
+------------------------------------+----------------------+
| Paragraph end                      | ``<#1.5#>``          |
+------------------------------------+----------------------+
| Scene change                       | ``<#2#>``            |
+------------------------------------+----------------------+
| Ellipsis                           | ``<#1.2#>``          |
+------------------------------------+----------------------+
| Dialogue to narration transition   | ``<#0.8#>``          |
+------------------------------------+----------------------+
| Speaker alternation                | ``<#0.6#>``          |
+------------------------------------+----------------------+
| After attribution phrase           | ``<#0.3#>``          |
+------------------------------------+----------------------+
| Hesitation within dialogue         | ``<#0.8#>``          |
+------------------------------------+----------------------+

Author: novel-forge
"""

from __future__ import annotations

import re
from collections.abc import Collection

from novel_forge.obs.logger import get_logger
from novel_forge.tts.platform.synthesis_capabilities import (
    provider_synthesis_capabilities,
    providers_with_native_pause_markers,
)
from novel_forge.tts.schemas import DubbingScript, DubbingSegment, EmotionTag, SegmentType

_log = get_logger(__name__)

# ── Platforms that natively support `<#X#>` pause syntax ────────────────────
PAUSE_MARKER_PLATFORMS: frozenset[str] = providers_with_native_pause_markers()

# ── Onomatopoeia tag support (speech-2.8 series only) ───────────────────────
# MiniMax speech-2.8 系列支持拟声标签语法（如 ``(laughs)``、``(sighs)``、
# ``(whisper)``），由 emotion + intensity 确定性推断，不依赖 LLM。
ONOMATOPOEIA_MODEL_PREFIX: str = "speech-2.8"

# (emotions, min_intensity, tag) —— 命中任一情绪且强度达标时注入段首标签。
_ONOMATOPOEIA_RULES: tuple[tuple[frozenset[EmotionTag], float, str], ...] = (
    (frozenset({EmotionTag.HAPPY, EmotionTag.PLAYFUL}), 0.6, "(laughs)"),
    (frozenset({EmotionTag.SAD, EmotionTag.NOSTALGIC, EmotionTag.TENDER}), 0.6, "(sighs)"),
    (frozenset({EmotionTag.WHISPER}), 0.0, "(whisper)"),
)
_ONOMATOPOEIA_TAGS: frozenset[str] = frozenset(tag for _, _, tag in _ONOMATOPOEIA_RULES)
_ONOMATOPOEIA_LEAD_RE = re.compile(
    r"^\((?:laughs|sighs|whisper)\)\s*",
    re.IGNORECASE,
)

# ── Marker duration constants (ms) — reference scene table ──────────────────
_SENTENCE_END_MS = 600  # 。 -> <#0.6#>
_EXCLAMATION_END_MS = 400  # ！？ -> <#0.4#>
_LONG_COMMA_MS = 250  # 长句逗号 -> <#0.25#>
_PARAGRAPH_END_MS = 1500  # 段落结束 -> <#1.5#>
_SCENE_CHANGE_MS = 2000  # 场景切换 -> <#2#>
_ELLIPSIS_MS = 1200  # 省略号 -> <#1.2#>
_DIALOGUE_TRANSITION_MS = 800  # 对话转旁白 -> <#0.8#>
_TURN_TAKE_MS = 600  # 说话人交替 -> <#0.6#>
_ATTRIBUTION_MS = 300  # 归因短语后 -> <#0.3#>
_HESITATION_MS = 800  # 对话内犹豫 -> <#0.8#>

# ── Regex helpers ────────────────────────────────────────────────────────────
_PAUSE_MARKER_RE = re.compile(r"<#\d+(?:\.\d+)?#>")
# Long-sentence comma injection threshold (chars) and max comma count.
_LONG_SENTENCE_CHARS = 40
_MAX_LONG_COMMA_COUNT = 4
# Attribution phrase endings (e.g. "他说", "她问", "老者回答").
_ATTRIBUTION_END_RE = re.compile(
    r"(?:说|道|问|答|喊|叫|嚷|应|回应|回答|解释|补充|低语|喃喃|开口)[着过]?(?:了)?$"
)
# Segments eligible for marker injection.
_INJECTABLE_TYPES = frozenset(
    {
        SegmentType.NARRATION,
        SegmentType.DIALOGUE,
        SegmentType.INNER_THOUGHT,
    }
)


def _marker(ms: int) -> str:
    """Render a duration in milliseconds as a MiniMax pause marker.

    Durations use the minimal decimal form (``<#0.6#>``, not ``<#0.60#>``)
    matching the reference scene table syntax.
    """
    # MiniMax permits a single marker from 0.01 to 99.99 seconds.  Segment
    # transitions are normally much shorter; clamping keeps a malformed
    # upstream annotation from generating an invalid provider request.
    seconds = min(99.99, max(0.01, ms / 1000))
    value = f"{seconds:.2f}".rstrip("0").rstrip(".")
    return f"<#{value}#>"


def strip_pause_markers(text: str) -> str:
    """Remove existing ``<#X#>`` markers so injection stays idempotent."""
    return _PAUSE_MARKER_RE.sub("", text)


def _strip_inline_markers(text: str) -> str:
    """Remove inline markers while keeping trailing sentence punctuation."""
    return _PAUSE_MARKER_RE.sub("", text)


def _insert_inline_markers(text: str, *, is_dialogue: bool) -> str:
    """Insert sentence-level markers based on punctuation structure.

    Rules (in scan order):

    1. Full stop ``。`` -> ``<#0.6#>``
    2. Exclamation / question ``！`` ``？`` -> ``<#0.4#>`` (combined
       ``？！`` / ``！？`` emits a single marker)
    3. Ellipsis ``……`` / ``...`` -> ``<#1.2#>`` (narration) or ``<#0.8#>``
       (dialogue hesitation); the ellipsis characters are replaced by the
       marker so the engine pauses instead of vocalizing dots
    4. Long-sentence comma -> ``<#0.25#>`` only when the sentence is long
       enough and comma density is low (avoids choppy delivery)
    """
    if not text:
        return text

    # Precompute long-sentence comma eligibility.
    comma_count = text.count("，") + text.count(",")
    inject_commas = len(text) >= _LONG_SENTENCE_CHARS and comma_count <= _MAX_LONG_COMMA_COUNT

    parts: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        # Combined exclamation/question marks — single marker.
        if ch in "！？" and i + 1 < n and text[i + 1] in "！？":
            parts.append(ch)
            parts.append(text[i + 1])
            parts.append(_marker(_EXCLAMATION_END_MS))
            i += 2
            continue
        if ch == "。":
            parts.append(ch)
            parts.append(_marker(_SENTENCE_END_MS))
        elif ch in "！？":
            parts.append(ch)
            parts.append(_marker(_EXCLAMATION_END_MS))
        elif ch == "…":
            # Consume the full ellipsis run (……).
            j = i
            while j < n and text[j] == "…":
                j += 1
            duration = _HESITATION_MS if is_dialogue else _ELLIPSIS_MS
            parts.append(_marker(duration))
            i = j
            continue
        elif ch == ".":
            # Only treat 2+ consecutive dots as an ellipsis; a single dot is
            # kept verbatim (decimal points, English abbreviations).
            j = i
            while j < n and text[j] == ".":
                j += 1
            if j - i >= 2:
                duration = _HESITATION_MS if is_dialogue else _ELLIPSIS_MS
                parts.append(_marker(duration))
                i = j
                continue
            parts.append(ch)
        elif ch in "，," and inject_commas:
            parts.append(ch)
            parts.append(_marker(_LONG_COMMA_MS))
        else:
            parts.append(ch)
        i += 1
    return "".join(parts)


def _speaker(segment: DubbingSegment | None) -> str:
    """Return a stable speaker key for a segment (empty for narration)."""
    if segment is None:
        return ""
    if segment.segment_type != SegmentType.DIALOGUE:
        return ""
    return segment.character_id or segment.character_name or ""


def _leading_marker_ms(
    segment: DubbingSegment,
    prev: DubbingSegment | None,
    *,
    explicit_gap_ms: int | None = None,
) -> int:
    """Decide the strongest inter-segment leading pause for *segment*.

    Priority (strongest wins):

    1. Scene change ``<#2#>`` — scene context differs or paragraph gap > 1
    2. Paragraph end ``<#1.5#>`` — different source paragraph
    3. Dialogue to narration transition ``<#0.8#>``
    4. Speaker alternation ``<#0.6#>``
    5. After attribution phrase ``<#0.3#>``
    """
    if explicit_gap_ms is not None and explicit_gap_ms > 0:
        return explicit_gap_ms
    if segment.transition is not None and segment.transition.gap_ms > 0:
        return segment.transition.gap_ms
    if prev is None:
        return 0

    scene_changed = (
        segment.scene_context != prev.scene_context
        and bool(segment.scene_context or prev.scene_context)
    )
    paragraph_gap = (
        segment.source_paragraph > 0
        and prev.source_paragraph > 0
        and segment.source_paragraph - prev.source_paragraph > 1
    )
    if scene_changed or paragraph_gap:
        return _SCENE_CHANGE_MS

    if segment.source_paragraph != prev.source_paragraph:
        return _PARAGRAPH_END_MS

    prev_is_dialogue = prev.segment_type == SegmentType.DIALOGUE
    cur_is_dialogue = segment.segment_type == SegmentType.DIALOGUE
    if prev_is_dialogue and not cur_is_dialogue:
        return _DIALOGUE_TRANSITION_MS
    if prev_is_dialogue and cur_is_dialogue and _speaker(prev) != _speaker(segment):
        return _TURN_TAKE_MS
    if (
        cur_is_dialogue
        and prev.segment_type in _INJECTABLE_TYPES
        and prev.spoken_text
        and _ATTRIBUTION_END_RE.search(prev.spoken_text.strip())
    ):
        return _ATTRIBUTION_MS
    return 0


def _onomatopoeia_tag(
    segment: DubbingSegment,
    *,
    model_id: str,
    enabled: bool,
    min_intensity: float,
) -> str:
    """Determine the onomatopoeia tag for *segment* (``""`` when none).

    Rules (deterministic, speech-2.8 series only):

    - HAPPY / PLAYFUL with intensity >= threshold -> ``(laughs)``
    - SAD / NOSTALGIC / TENDER with intensity >= threshold -> ``(sighs)``
    - WHISPER -> ``(whisper)`` (unconditional; the label itself is the cue)

    The tag is always returned without surrounding whitespace; callers are
    responsible for placement at the segment head.
    """
    if not enabled or not model_id or not model_id.lower().startswith(
        ONOMATOPOEIA_MODEL_PREFIX
    ):
        return ""
    emotion = segment.emotion
    if emotion is None:
        return ""
    intensity = float(segment.emotion_intensity or 0.0)
    for emotions, threshold, tag in _ONOMATOPOEIA_RULES:
        if emotion in emotions and intensity >= threshold:
            return tag
    return ""


def build_rule_pause_markers(
    segment: DubbingSegment,
    prev: DubbingSegment | None = None,
    next: DubbingSegment | None = None,  # noqa: A002 - reserved name shadowing is intentional for API symmetry
    *,
    model_id: str = "",
    onomatopoeia_enabled: bool = True,
    onomatopoeia_min_intensity: float = 0.6,
    explicit_leading_gap_ms: int | None = None,
) -> str:
    """Build the pause-marker-injected spoken text for *segment*.

    Args:
        segment: The segment whose ``spoken_text`` is processed.
        prev: Previous segment for inter-segment leading pauses.
        next: Next segment (reserved for future rules; currently unused).
        model_id: Active TTS model id; speech-2.8 series additionally receive
            onomatopoeia tags derived from emotion + intensity.
        onomatopoeia_enabled: Master switch for onomatopoeia tag injection.
        onomatopoeia_min_intensity: Minimum intensity for tag injection.
        explicit_leading_gap_ms: A chapter-level scene transition gap mapped to
            this segment.  It takes precedence over inferred structure so the
            native speech input and playback timeline represent the same pause.

    Returns:
        The injected ``spoken_text``.  Empty or non-injectable segments are
        returned unchanged; existing markers are stripped before re-injection
        so the function is idempotent.
    """
    spoken = segment.spoken_text.strip()
    if not spoken or segment.segment_type not in _INJECTABLE_TYPES:
        return segment.spoken_text

    text = _strip_inline_markers(spoken)
    is_dialogue = segment.segment_type == SegmentType.DIALOGUE
    text = _insert_inline_markers(text, is_dialogue=is_dialogue)

    tag = _onomatopoeia_tag(
        segment,
        model_id=model_id,
        enabled=onomatopoeia_enabled,
        min_intensity=onomatopoeia_min_intensity,
    )
    if tag:
        # 幂等：剥离段首既有拟声标签后重新注入，避免重跑时重复标签。
        # 先去掉停顿标记再剥离标签，保证 marker 前缀不会挡住标签剥离。
        stripped = _strip_inline_markers(text)
        stripped = _ONOMATOPOEIA_LEAD_RE.sub("", stripped)
        text = _insert_inline_markers(stripped, is_dialogue=is_dialogue)
        text = f"{tag} {text}" if text else tag

    leading_ms = _leading_marker_ms(segment, prev, explicit_gap_ms=explicit_leading_gap_ms)
    if leading_ms > 0:
        text = f"{_marker(leading_ms)}{text}"

    if text == segment.spoken_text:
        return segment.spoken_text
    return text


def inject_pause_markers(
    script: DubbingScript,
    *,
    platform: str = "minimax",
    platforms: Collection[str] = PAUSE_MARKER_PLATFORMS,
    model_id: str = "",
    onomatopoeia_enabled: bool = True,
    onomatopoeia_min_intensity: float = 0.6,
) -> DubbingScript:
    """Inject deterministic pause markers into all eligible segments.

    Runs after the spoken-text rewrite (LLM + sanitize fallback) so markers
    are applied to the final spoken text.  Only platforms listed in
    *platforms* natively support ``<#X#>``; other providers keep plain text
    so the markers are never read out literally.  When *model_id* belongs to
    the speech-2.8 series, emotion-derived onomatopoeia tags are additionally
    injected at the segment head (``(laughs)`` / ``(sighs)`` / ``(whisper)``).

    Args:
        script: The dubbing script to process.
        platform: The active TTS provider id.
        platforms: Providers that support pause markers.
        model_id: Active TTS model id (speech-2.8 series unlock onomatopoeia).
        onomatopoeia_enabled: Master switch for onomatopoeia tags.
        onomatopoeia_min_intensity: Minimum intensity for tag injection.

    Returns:
        A new :class:`DubbingScript` with markers injected and injection
        statistics attached to ``metadata["pause_marker_injection"]``.
    """
    platform_id = str(platform or "").strip().lower().replace("-", "_")
    platform_ids = {
        str(item or "").strip().lower().replace("-", "_") for item in platforms
    }
    capabilities = provider_synthesis_capabilities(platform_id)
    if platform_id not in platform_ids or capabilities.native_pause_syntax != "minimax_hash":
        _log.debug("Pause marker injection skipped: platform %r not supported", platform_id)
        return script

    segments = list(script.segments)
    # Chapter-level transitions historically lived beside the segments rather
    # than on the leading segment.  Resolve them using the same boundary rule
    # as ``build_timeline`` before injecting native pause markers.  Without
    # this projection, the timeline would correctly suppress external silence
    # but silently lose a chapter-level scene break.
    explicit_transition_gaps: dict[int, int] = {
        segment.segment_index: segment.transition.gap_ms
        for segment in segments
        if segment.transition is not None and segment.transition.gap_ms > 0
    }
    paragraph_boundaries: list[int] = []
    previous_paragraph: int | None = None
    for segment in segments:
        if previous_paragraph is not None and segment.source_paragraph != previous_paragraph:
            paragraph_boundaries.append(segment.segment_index)
        previous_paragraph = segment.source_paragraph
    for transition, segment_index in zip(
        (item for item in script.scene_transitions if item.gap_ms > 0),
        (index for index in paragraph_boundaries if index not in explicit_transition_gaps),
        strict=False,
    ):
        explicit_transition_gaps[segment_index] = transition.gap_ms
    injected_count = 0
    tag_count = 0
    for position, segment in enumerate(segments):
        prev = segments[position - 1] if position > 0 else None
        next_segment = segments[position + 1] if position + 1 < len(segments) else None
        old_spoken = segment.spoken_text
        new_spoken = build_rule_pause_markers(
            segment,
            prev,
            next_segment,
            model_id=model_id,
            onomatopoeia_enabled=onomatopoeia_enabled,
            onomatopoeia_min_intensity=onomatopoeia_min_intensity,
            explicit_leading_gap_ms=explicit_transition_gaps.get(segment.segment_index),
        )
        if new_spoken != old_spoken:
            if _ONOMATOPOEIA_LEAD_RE.match(new_spoken):
                tag_count += 1
            segments[position] = segment.model_copy(update={"spoken_text": new_spoken})
            injected_count += 1

    if injected_count == 0:
        return script

    metadata = dict(script.metadata)
    metadata["pause_marker_injection"] = {
        "enabled": True,
        "platform": platform_id,
        "model_id": model_id,
        "onomatopoeia_enabled": bool(onomatopoeia_enabled),
        "injected_segments": injected_count,
        "onomatopoeia_segments": tag_count,
        "total_segments": len(segments),
    }
    _log.info(
        "Pause marker injection: %d/%d segments (onomatopoeia=%d) for chapter %d "
        "(platform=%s, model=%s)",
        injected_count,
        len(segments),
        tag_count,
        script.chapter_number,
        platform_id,
        model_id or "-",
    )
    return script.model_copy(update={"segments": segments, "metadata": metadata})


def retarget_native_synthesis_annotations(
    script: DubbingScript,
    *,
    previous_platform: str,
    previous_model_id: str,
    platform: str,
    platforms: Collection[str] = PAUSE_MARKER_PLATFORMS,
    model_id: str = "",
    onomatopoeia_enabled: bool = True,
    onomatopoeia_min_intensity: float = 0.6,
) -> DubbingScript:
    """Move native MiniMax annotations to the frozen execution target.

    Script generation may run before the audio execution plan is frozen.  If
    that plan chooses a different provider or model, MiniMax-only markers
    must be removed before the final target is annotated; otherwise another
    provider can speak them literally, or a pre-2.8 MiniMax model can receive
    unsupported interjection tags.
    """
    prior_id = str(previous_platform or "").strip().lower().replace("-", "_")
    platform_id = str(platform or "").strip().lower().replace("-", "_")
    platform_ids = {
        str(item or "").strip().lower().replace("-", "_") for item in platforms
    }
    prior_capabilities = provider_synthesis_capabilities(prior_id)
    prior_has_native_pauses = (
        prior_id in platform_ids and prior_capabilities.native_pause_syntax == "minimax_hash"
    )
    if not prior_has_native_pauses:
        return inject_pause_markers(
            script,
            platform=platform_id,
            platforms=platforms,
            model_id=model_id,
            onomatopoeia_enabled=onomatopoeia_enabled,
            onomatopoeia_min_intensity=onomatopoeia_min_intensity,
        )

    previous_is_speech_28 = str(previous_model_id or "").strip().lower().startswith(
        ONOMATOPOEIA_MODEL_PREFIX
    )
    sanitized_segments: list[DubbingSegment] = []
    for segment in script.segments:
        spoken = strip_pause_markers(segment.spoken_text)
        if previous_is_speech_28:
            spoken = _ONOMATOPOEIA_LEAD_RE.sub("", spoken).lstrip()
        sanitized_segments.append(segment.model_copy(update={"spoken_text": spoken}))

    metadata = dict(script.metadata)
    metadata.pop("pause_marker_injection", None)
    sanitized = script.model_copy(update={"segments": sanitized_segments, "metadata": metadata})
    return inject_pause_markers(
        sanitized,
        platform=platform_id,
        platforms=platforms,
        model_id=model_id,
        onomatopoeia_enabled=onomatopoeia_enabled,
        onomatopoeia_min_intensity=onomatopoeia_min_intensity,
    )


def uses_native_pause_timing(script: DubbingScript) -> bool:
    """Return whether the synthesized audio already contains boundary pauses.

    This is intentionally metadata-first: marker injection is an explicit
    pipeline phase and gives the mixer a reliable signal to avoid creating a
    second FFmpeg silence track at every boundary.  The marker scan preserves
    correctness for a script restored from an older artifact that predates the
    metadata field.
    """

    injection = script.metadata.get("pause_marker_injection")
    if isinstance(injection, dict) and injection.get("enabled"):
        return provider_synthesis_capabilities(
            str(injection.get("platform") or "")
        ).uses_native_pause_timing
    return False
