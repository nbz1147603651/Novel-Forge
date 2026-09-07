"""Accurate timeline builder for TTS dubbing playback.

Constructs a precise time-axis mapping from synthesis results,
enabling real-time text highlighting and subtitle synchronization
during audio playback.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from novel_forge.tts.pipeline.pause_marker_injection import uses_native_pause_timing
from novel_forge.tts.pipeline.transition_policy import SpeechTransitionPolicy
from novel_forge.tts.schemas import (
    DubbingScript,
    SegmentType,
    SynthesisResult,
    SynthesisStatus,
)

# Default silence gaps (ms)
DEFAULT_SEGMENT_GAP_MS = 300
PARAGRAPH_GAP_MS = 800
SCENE_TRANSITION_GAP_MS = 1500


@dataclass(frozen=True)
class TimelineEntry:
    """A single entry in the playback timeline."""

    segment_index: int
    start_ms: int
    end_ms: int
    segment_type: SegmentType
    character_id: str
    character_name: str
    text: str
    emotion: str = "neutral"
    source_paragraph: int = 0

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


@dataclass
class PlaybackTimeline:
    """Complete playback timeline for a chapter.

    Provides efficient binary-search lookup for real-time
    position → segment mapping during audio playback.
    """

    entries: list[TimelineEntry] = field(default_factory=list)
    total_duration_ms: int = 0
    _sorted_starts: list[int] = field(default_factory=list, repr=False)

    def build_index(self) -> None:
        """Build the sorted start-time index for binary search."""
        self.entries.sort(key=lambda e: e.start_ms)
        self._sorted_starts = [e.start_ms for e in self.entries]
        if self.entries:
            self.total_duration_ms = max(
                self.total_duration_ms,
                max(e.end_ms for e in self.entries),
            )

    def segment_at(self, position_ms: int) -> TimelineEntry | None:
        """Find the timeline entry at the given playback position.

        Uses binary search for O(log n) lookup — critical for
        real-time UI updates at ~10 Hz refresh rate.
        """
        import bisect

        if not self._sorted_starts:
            return None

        idx = bisect.bisect_right(self._sorted_starts, position_ms) - 1
        if idx < 0 or idx >= len(self.entries):
            return None

        entry = self.entries[idx]
        if entry.start_ms <= position_ms <= entry.end_ms:
            return entry
        return None

    def char_position_at(self, position_ms: int) -> tuple[TimelineEntry | None, int]:
        """Find the current character position within a segment.

        Returns:
            (entry, char_index) where char_index is the approximate
            character being spoken at position_ms.
        """
        entry = self.segment_at(position_ms)
        if entry is None or not entry.text:
            return entry, 0

        elapsed = position_ms - entry.start_ms
        duration = entry.duration_ms
        if duration <= 0:
            return entry, 0

        progress = min(1.0, elapsed / duration)
        char_idx = int(progress * len(entry.text))
        return entry, min(char_idx, len(entry.text))

    def progress_fraction(self, position_ms: int) -> float:
        """Return overall playback progress as 0.0–1.0 fraction."""
        if self.total_duration_ms <= 0:
            return 0.0
        return min(1.0, max(0.0, position_ms / self.total_duration_ms))


def build_timeline(
    script: DubbingScript,
    results: list[SynthesisResult],
    *,
    default_gap_ms: int = DEFAULT_SEGMENT_GAP_MS,
    paragraph_gap_ms: int = PARAGRAPH_GAP_MS,
    transition_gap_ms: int = SCENE_TRANSITION_GAP_MS,
) -> PlaybackTimeline:
    """Build an accurate playback timeline from synthesis results.

    Uses actual synthesized duration_ms (not estimates) for precise
    audio-to-text synchronization.

    Args:
        script: The dubbing script with segment metadata.
        results: Synthesis results with actual durations.
        default_gap_ms: Gap between same-paragraph segments.
        paragraph_gap_ms: Gap between different paragraphs.
        transition_gap_ms: Gap for scene transitions.

    Returns:
        PlaybackTimeline with sorted entries and index built.
    """
    # Build result lookup
    result_map: dict[int, SynthesisResult] = {r.segment_index: r for r in results}

    # Older/LLM-generated scripts may store transitions only at chapter level
    # instead of attaching them to the first segment of the new scene.  Map
    # those transitions to paragraph boundaries as a safe compatibility path.
    transition_gaps: dict[int, int] = {
        segment.segment_index: segment.transition.gap_ms
        for segment in script.segments
        if segment.transition and segment.transition.gap_ms > 0
    }
    boundary_indices: list[int] = []
    previous_paragraph: int | None = None
    for segment in script.segments:
        if previous_paragraph is not None and segment.source_paragraph != previous_paragraph:
            boundary_indices.append(segment.segment_index)
        previous_paragraph = segment.source_paragraph
    for transition, segment_index in zip(
        (item for item in script.scene_transitions if item.gap_ms > 0),
        (index for index in boundary_indices if index not in transition_gaps),
        strict=False,
    ):
        transition_gaps[segment_index] = transition.gap_ms

    entries: list[TimelineEntry] = []
    cursor_ms = 0
    prev_paragraph = -1
    previous_segment = None
    transition_policy = SpeechTransitionPolicy(
        default_gap_ms=default_gap_ms,
        paragraph_gap_ms=paragraph_gap_ms,
    )
    native_pause_timing = uses_native_pause_timing(script)

    for segment in script.segments:
        result = result_map.get(segment.segment_index)

        # Skip non-synthesizable segments but add gap for transitions
        if segment.segment_type in (SegmentType.BGM, SegmentType.SFX, SegmentType.SILENCE):
            if segment.transition and segment.transition.gap_ms > 0:
                cursor_ms += segment.transition.gap_ms
            continue

        # The playback timeline represents the audio that actually exists.
        # Do not create subtitles/highlights for missing or skipped segments.
        if result is None or result.status != SynthesisStatus.COMPLETED:
            continue

        # Compute gap before this segment
        if native_pause_timing:
            # MiniMax receives the boundary pause in the following segment's
            # source text (``<#x#>``).  Its measured duration already contains
            # that silence, so adding a mixer/timeline gap would double every
            # intended pause and desynchronise subtitles from the master.
            gap_ms = 0
        elif segment.segment_index in transition_gaps:
            gap_ms = transition_gaps[segment.segment_index]
        elif prev_paragraph >= 0:
            gap_ms = transition_policy.gap_ms(
                previous_segment,
                segment,
                paragraph_changed=segment.source_paragraph != prev_paragraph,
            )
        else:
            gap_ms = 0

        cursor_ms += gap_ms
        start_ms = cursor_ms

        # Use actual synthesized duration (preferred) or estimate
        if result and result.status == SynthesisStatus.COMPLETED and result.duration_ms > 0:
            duration = result.duration_ms
        else:
            # Estimate: ~200ms per Chinese character at normal speed
            speed = segment.speed_override or 1.0
            duration = int(len(segment.synthesis_text) * 200 / speed)

        end_ms = start_ms + duration

        emotion_str = segment.emotion.value if segment.emotion else "neutral"

        entries.append(
            TimelineEntry(
                segment_index=segment.segment_index,
                start_ms=start_ms,
                end_ms=end_ms,
                segment_type=segment.segment_type,
                character_id=segment.character_id,
                character_name=segment.character_name,
                text=segment.synthesis_text,
                emotion=emotion_str,
                source_paragraph=segment.source_paragraph,
            )
        )

        cursor_ms = end_ms
        prev_paragraph = segment.source_paragraph
        previous_segment = segment

    timeline = PlaybackTimeline(entries=entries, total_duration_ms=cursor_ms)
    timeline.build_index()
    return timeline
