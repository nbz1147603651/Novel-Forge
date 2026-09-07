"""Narration arc — global chapter-level distance planning (P3-5).

A chapter is not one flat narrative tone: opening lines pull the listener
in, the middle develops steadily, the climax draws closer to the
characters' emotions, and the ending pulls back.  This module computes a
deterministic chapter-level narration-distance curve and writes it onto
each NARRATION segment's ``narrator_distance`` field.

Design:

- **Deterministic first, LLM last**: the arc is derived from segment
  positions and the chapter's emotion-intensity profile by pure rules —
  zero LLM cost, reproducible across retries.
- **Never overrides authorial intent**: segments that already carry a
  ``narrator_distance`` (from sound design or manual annotation) are left
  untouched.
- **Consumed downstream for free**: the finalize phase
  (``_enrich_voice_direction``) already maps ``narrator_distance`` onto
  ``vocal_direction.intimacy`` / ``breathiness`` / ``delivery_style``, so
  this phase only writes the distance label.

Arc segments (by position ratio within the chapter):

+-----------------+----------+-----------------------------------------+
| Section         | Ratio    | Distance                                |
+=================+==========+=========================================+
| Opening         | 0-0.12   | close (immediate immersion)             |
+-----------------+----------+-----------------------------------------+
| Development     | 0.12-0.55| medium (steady narrative)               |
+-----------------+----------+-----------------------------------------+
| Climax          | 0.55-0.85| close when local intensity >= 0.6, else  |
|                 |          | medium (pull toward emotional peak)     |
+-----------------+----------+-----------------------------------------+
| Resolution      | 0.85-1.0 | distant (pull back for the close)       |
+-----------------+----------+-----------------------------------------+

Author: novel-forge
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from novel_forge.obs.logger import get_logger
from novel_forge.tts.schemas import SegmentType

if TYPE_CHECKING:
    from novel_forge.tts.schemas import DubbingScript

_log = get_logger(__name__)

# 弧线分段边界（位置比例）。
_OPENING_END = 0.12
_DEVELOPMENT_END = 0.55
_CLIMAX_END = 0.85

# 高潮区情绪强度阈值：区域内平均强度达标则贴近（close）。
_CLIMAX_INTENSITY_THRESHOLD = 0.6


@dataclass(frozen=True)
class NarrationArcSegment:
    """One deterministic arc segment (by chapter position ratio)."""

    start_ratio: float
    end_ratio: float
    distance: str  # close | medium | distant


def build_narration_arc(
    segments: list[Any],
    *,
    climax_intensity_threshold: float = _CLIMAX_INTENSITY_THRESHOLD,
) -> list[NarrationArcSegment]:
    """Compute the chapter narration-distance arc from the segment list.

    The climax section's distance adapts to the local emotion-intensity
    profile: when the climax region averages high intensity the narrator
    moves close (intimate delivery); otherwise it stays at medium.

    Returns:
        Four :class:`NarrationArcSegment` entries covering [0, 1].
    """
    total = max(1, len(segments))
    narration_segments = [
        segment
        for segment in segments
        if segment.segment_type == SegmentType.NARRATION
    ]
    local_intensities = [
        float(getattr(segment, "emotion_intensity", 0.0) or 0.0)
        for segment in narration_segments
        if _OPENING_END <= (segments.index(segment) / total) < _CLIMAX_END
    ]
    avg_intensity = (
        sum(local_intensities) / len(local_intensities) if local_intensities else 0.0
    )
    climax_distance = (
        "close" if avg_intensity >= climax_intensity_threshold else "medium"
    )
    return [
        NarrationArcSegment(0.0, _OPENING_END, "close"),
        NarrationArcSegment(_OPENING_END, _DEVELOPMENT_END, "medium"),
        NarrationArcSegment(_DEVELOPMENT_END, _CLIMAX_END, climax_distance),
        NarrationArcSegment(_CLIMAX_END, 1.0, "distant"),
    ]


def distance_at(arc: list[NarrationArcSegment], ratio: float) -> str:
    """Return the narration distance active at *ratio* (clamped to [0, 1])."""
    ratio = max(0.0, min(1.0, ratio))
    for section in arc:
        if section.start_ratio <= ratio < section.end_ratio:
            return section.distance
    return arc[-1].distance if arc else "medium"


def apply_narration_arc(
    script: DubbingScript,
    *,
    enabled: bool = True,
    climax_intensity_threshold: float = _CLIMAX_INTENSITY_THRESHOLD,
) -> DubbingScript:
    """Write the narration-distance arc onto NARRATION segments.

    Only segments whose ``narrator_distance`` is empty receive a value —
    explicit authorial / sound-design choices always win.  The arc itself
    is recorded in ``metadata["narration_arc"]`` for observability.

    Args:
        script: The dubbing script to process (post sound-design).
        enabled: Master switch (``tts_narration_arc_enabled``).
        climax_intensity_threshold: Intensity threshold that switches the
            climax section to ``close`` delivery.

    Returns:
        A new script with narration distances applied; unchanged when the
        feature is disabled or the script has no narration segments.
    """
    if not enabled:
        return script

    segments = list(script.segments)
    narration_positions = [
        position
        for position, segment in enumerate(segments)
        if segment.segment_type == SegmentType.NARRATION
    ]
    if not narration_positions:
        return script

    arc = build_narration_arc(
        segments,
        climax_intensity_threshold=climax_intensity_threshold,
    )
    total = max(1, len(segments))
    updated_count = 0
    for position in narration_positions:
        segment = segments[position]
        if str(segment.narrator_distance or "").strip():
            continue  # 已有旁白距离（声音设计/人工标注）优先。
        ratio = position / total
        segments[position] = segment.model_copy(
            update={"narrator_distance": distance_at(arc, ratio)}
        )
        updated_count += 1

    if updated_count == 0:
        return script

    metadata = dict(script.metadata)
    metadata["narration_arc"] = {
        "enabled": True,
        "sections": [
            {
                "start_ratio": section.start_ratio,
                "end_ratio": section.end_ratio,
                "distance": section.distance,
            }
            for section in arc
        ],
        "updated_narration_segments": updated_count,
        "total_narration_segments": len(narration_positions),
    }
    _log.info(
        "Narration arc applied: %d/%d narration segments for chapter %d",
        updated_count,
        len(narration_positions),
        script.chapter_number,
    )
    return script.model_copy(update={"segments": segments, "metadata": metadata})


__all__ = [
    "NarrationArcSegment",
    "apply_narration_arc",
    "build_narration_arc",
    "distance_at",
]
