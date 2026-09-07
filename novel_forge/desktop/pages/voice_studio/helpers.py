"""Pure helper functions and module-level constants for Voice Studio.

Extracted from ``page.py`` to reduce single-file size.  All items here
are pure functions or static data with no dependency on ``VoiceStudioPage``.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from novel_forge.desktop.theme import qcolor_hex
from novel_forge.desktop.tokens.typography import FONTS
from novel_forge.tts.schemas import DubbingScript, EmotionTag
from novel_forge.tts.script_integrity import compute_dubbing_script_hash

__all__ = (
    "EMOTION_STYLES",
    "PARALINGUISTIC_ICONS",
    "NARRATOR_ID",
    "LINUX_AUDITION_PLAYERS",
    "HTML_BODY_FONT_FAMILY",
    "ChapterScriptStream",
    "StreamAnomalyReport",
    "detect_stream_anomalies",
    "emotion_display",
    "effective_script_hash",
    "segment_status_color",
    "complete_json_array_objects",
    "incremental_json_array_objects",
    "incremental_detect_stream_anomalies",
    "logger",
)

# Emotion tag -> (display label, semantic token).  Resolve the token at render
# time so these labels follow the selected desktop palette.
EMOTION_STYLES: dict[EmotionTag, tuple[str, str]] = {
    EmotionTag.NEUTRAL: ("中性", "text.muted"),
    EmotionTag.HAPPY: ("喜悦", "status.success.light"),
    EmotionTag.SAD: ("哀伤", "status.info"),
    EmotionTag.ANGRY: ("愤怒", "status.danger"),
    EmotionTag.FEARFUL: ("恐惧", "motif.purple"),
    EmotionTag.SURPRISED: ("惊讶", "status.warning"),
    EmotionTag.DISGUSTED: ("厌恶", "text.char.retired"),
    EmotionTag.TENDER: ("温柔", "accent.light"),
    EmotionTag.MOCKING: ("嘲讽", "motif.purple"),
    EmotionTag.WHISPER: ("低语", "text.muted.soft"),
    EmotionTag.NOSTALGIC: ("怀旧", "text.muted.strong"),
    EmotionTag.ANXIOUS: ("焦虑", "accent.warm"),
    EmotionTag.CONTEMPT: ("轻蔑", "accent.fallback"),
    EmotionTag.DETERMINED: ("坚定", "status.info"),
    EmotionTag.PLAYFUL: ("俏皮", "chart.8"),
}


def emotion_display(emotion: EmotionTag) -> tuple[str, str]:
    """Return the active-theme display label and color for an emotion."""
    label, token = EMOTION_STYLES.get(emotion, (emotion.value, "text.muted"))
    return label, qcolor_hex(token)


def effective_script_hash(script: DubbingScript) -> str:
    """Return the persisted hash or a stable fallback for legacy scripts."""
    return script.script_hash or compute_dubbing_script_hash(script)


def segment_status_color(status: str) -> str:
    """Return the active-theme foreground color for a synthesis status."""
    token = {
        "pending": "text.muted",
        "synthesizing": "status.info",
        "completed": "status.success.light",
        "failed": "status.danger",
        "skipped": "text.disabled",
    }.get(status, "text.muted")
    return qcolor_hex(token)


# Paralinguistic tag -> icon glyph
PARALINGUISTIC_ICONS = {
    "pause": "⏸",
    "breath": "💨",
    "laugh": "😊",
    "sigh": "😮‍💨",
    "stutter": "⋯",
    "emphasis": "❗",
    "choke": "🤐",
    "hum": "🎵",
}

logger = logging.getLogger(__name__)
HTML_BODY_FONT_FAMILY = FONTS["body"].for_platform()
NARRATOR_ID = "__narrator__"


@dataclass
class ChapterScriptStream:
    """Per-chapter streaming state for dubbing script generation.

    Replaces the page-level single stream variables so switching chapters
    shows each chapter's own progress instead of bleeding one chapter's
    "analyzing script" animation into every chapter window.
    """

    stream_id: str = ""
    text: str = ""
    active: bool = False
    dots: int = 0
    batch_count: int = 0
    completed_batches: set[int] = field(default_factory=set)
    progress_step: str = "tts_script_start"
    progress_data: dict[str, Any] = field(default_factory=dict)
    # ── Incremental parse cache (perf: avoid O(n) rescan on every render) ──
    _cached_segments: list[dict[str, Any]] = field(default_factory=list)
    _cached_scan_pos: int = 0
    _cached_array_start: int = -1
    _cached_anomaly_report: StreamAnomalyReport | None = None
    _cached_anomaly_seg_count: int = 0
    # ── Render dedup: skip full HTML rebuild when segment count unchanged ──
    _last_rendered_segment_count: int = -1  # -1 = never rendered
    _force_render: bool = True  # Force first render unconditionally


# Native CLI audio players tried in order on Linux for short voice auditions.
LINUX_AUDITION_PLAYERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("paplay", ()),
    ("aplay", ("-q",)),
    ("ffplay", ("-nodisp", "-autoexit", "-loglevel", "quiet")),
)


# JSON structural key pattern that should never appear inside segment prose.
_JSON_ECHO_PATTERN = re.compile(r'"segment_(?:index|type)"\s*:')

# Received chars exceeding this multiple of the source chapter length indicate
# probable model repetition or structural breakdown.
_CHAR_INFLATION_FACTOR = 3.0


@dataclass(frozen=True)
class StreamAnomalyReport:
    """Real-time quality signals detected in a streaming script preview."""

    duplicate_indices: tuple[int, ...] = ()
    """Segment indices whose text repeats a neighbouring segment."""

    json_echo_indices: tuple[int, ...] = ()
    """Segment indices whose text contains raw JSON structural keys."""

    char_inflation: bool = False
    """Received character count far exceeds the source chapter length."""

    @property
    def has_anomalies(self) -> bool:
        return bool(self.duplicate_indices or self.json_echo_indices or self.char_inflation)


def detect_stream_anomalies(
    segments: list[dict[str, Any]],
    received_chars: int,
    source_chars: int,
) -> StreamAnomalyReport:
    """Detect model output anomalies in a partially received script stream.

    Pure function with no Qt dependency; safe to call on every render tick.

    Rules:
    - Duplicate: normalized text identical to the previous segment, or the
      same text appearing >= 2 times within a sliding 3-segment window.
    - JSON echo: text contains JSON structural key patterns such as
      ``"segment_index":`` which indicate the model broke its output schema.
    - Char inflation: received chars exceed ``source_chars * 3``.
    """
    duplicate_indices: list[int] = []
    json_echo_indices: list[int] = []

    texts = [str(item.get("text") or "").strip() for item in segments]
    for index, text in enumerate(texts):
        if not text:
            continue
        # Adjacent duplicate
        if index > 0 and text == texts[index - 1]:
            duplicate_indices.append(index)
            continue
        # Sliding 3-segment window duplicate (covers A-B-A patterns)
        window_start = max(0, index - 2)
        if any(texts[prev] == text for prev in range(window_start, index)):
            duplicate_indices.append(index)

        if _JSON_ECHO_PATTERN.search(text):
            json_echo_indices.append(index)

    char_inflation = (
        source_chars > 0 and received_chars > source_chars * _CHAR_INFLATION_FACTOR
    )

    return StreamAnomalyReport(
        duplicate_indices=tuple(duplicate_indices),
        json_echo_indices=tuple(json_echo_indices),
        char_inflation=char_inflation,
    )


def complete_json_array_objects(raw: str, key: str) -> list[dict[str, Any]]:
    """Return fully received objects from one JSON array in a partial stream.

    Structured model output is allowed to be syntactically incomplete while it
    is streaming.  This small scanner never repairs or persists partial JSON;
    it only extracts balanced objects for safe, human-readable preview cards.
    """
    match = re.search(rf'"{ re.escape(key)}"\s*:\s*\[', raw)
    if match is None:
        return []
    objects: list[dict[str, Any]] = []
    start: int | None = None
    depth = 0
    in_string = False
    escaped = False
    for index in range(match.end(), len(raw)):
        char = raw[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    value = json.loads(raw[start : index + 1])
                except (json.JSONDecodeError, TypeError):
                    value = None
                if isinstance(value, dict):
                    objects.append(value)
                start = None
        elif char == "]" and depth == 0:
            break
    return objects


def incremental_json_array_objects(
    stream: ChapterScriptStream,
    key: str,
) -> list[dict[str, Any]]:
    """Incrementally parse complete JSON objects from a growing stream.

    Caches the scan position and already-parsed objects in *stream* so
    subsequent calls only process newly appended text.  Falls back to a
    full rescan when the stream is reset (stream_id change or text shrink).

    Returns the full list of parsed segment dicts (cached + newly found).
    """
    raw = stream.text
    # Detect stream reset: text shrunk or stream_id changed → full rescan.
    if (
        stream._cached_array_start < 0
        or len(raw) < stream._cached_scan_pos
        or stream._cached_scan_pos == 0
    ):
        # Locate the array opening.
        match = re.search(rf'"{ re.escape(key)}"\s*:\s*\[', raw)
        if match is None:
            stream._cached_segments = []
            stream._cached_scan_pos = 0
            stream._cached_array_start = -1
            return []
        stream._cached_array_start = match.end()
        stream._cached_scan_pos = match.end()
        stream._cached_segments = []
        stream._cached_anomaly_report = None
        stream._cached_anomaly_seg_count = 0

    # Scan from the cached position.
    objects = stream._cached_segments
    start: int | None = None
    depth = 0
    in_string = False
    escaped = False
    pos = stream._cached_scan_pos
    length = len(raw)
    while pos < length:
        char = raw[pos]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            pos += 1
            continue
        if char == '"':
            in_string = True
            pos += 1
            continue
        if char == "{":
            if depth == 0:
                start = pos
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    value = json.loads(raw[start : pos + 1])
                except (json.JSONDecodeError, TypeError):
                    value = None
                if isinstance(value, dict):
                    objects.append(value)
                start = None
        elif char == "]" and depth == 0:
            pos += 1
            break
        pos += 1

    # Persist scan position.  If we are mid-object (depth > 0), rewind to
    # the object start so the next call re-scans the incomplete object.
    if depth > 0 and start is not None:
        stream._cached_scan_pos = start
        # Trim objects back to what was confirmed before this partial object.
        # (objects list was not mutated for the partial, so no trim needed.)
    else:
        stream._cached_scan_pos = pos

    stream._cached_segments = objects
    return objects


def incremental_detect_stream_anomalies(
    stream: ChapterScriptStream,
    segments: list[dict[str, Any]],
    received_chars: int,
    source_chars: int,
) -> StreamAnomalyReport:
    """Detect anomalies with caching — only re-check newly added segments.

    The char-inflation flag is always recomputed (cheap), but per-segment
    duplicate/echo checks are cached and only extended for new segments.
    """
    cached = stream._cached_anomaly_report
    cached_count = stream._cached_anomaly_seg_count

    if cached is not None and cached_count >= len(segments):
        # No new segments — reuse cached report, only refresh char inflation.
        char_inflation = (
            source_chars > 0 and received_chars > source_chars * _CHAR_INFLATION_FACTOR
        )
        if char_inflation == cached.char_inflation:
            return cached
        return StreamAnomalyReport(
            duplicate_indices=cached.duplicate_indices,
            json_echo_indices=cached.json_echo_indices,
            char_inflation=char_inflation,
        )

    # Full recheck needed (first call or segments shrunk).
    if cached is None or cached_count > len(segments):
        report = detect_stream_anomalies(segments, received_chars, source_chars)
        stream._cached_anomaly_report = report
        stream._cached_anomaly_seg_count = len(segments)
        return report

    # Incremental: extend from cached_count to len(segments).
    dup_indices = list(cached.duplicate_indices)
    echo_indices = list(cached.json_echo_indices)
    texts = [str(item.get("text") or "").strip() for item in segments]

    for index in range(cached_count, len(segments)):
        text = texts[index]
        if not text:
            continue
        if index > 0 and text == texts[index - 1]:
            dup_indices.append(index)
            continue
        window_start = max(0, index - 2)
        if any(texts[prev] == text for prev in range(window_start, index)):
            dup_indices.append(index)
        if _JSON_ECHO_PATTERN.search(text):
            echo_indices.append(index)

    char_inflation = (
        source_chars > 0 and received_chars > source_chars * _CHAR_INFLATION_FACTOR
    )
    report = StreamAnomalyReport(
        duplicate_indices=tuple(dup_indices),
        json_echo_indices=tuple(echo_indices),
        char_inflation=char_inflation,
    )
    stream._cached_anomaly_report = report
    stream._cached_anomaly_seg_count = len(segments)
    return report
