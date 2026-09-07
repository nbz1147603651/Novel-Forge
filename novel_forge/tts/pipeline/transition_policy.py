"""Shared speech-turn spacing policy for authoring timelines and rendering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from novel_forge.tts.schemas import SegmentType


@dataclass(frozen=True)
class SpeechTransitionPolicy:
    """One source of truth for audible gaps between adjacent speech takes."""

    default_gap_ms: int = 300
    paragraph_gap_ms: int = 800
    same_speaker_gap_ms: int = 150
    speaker_switch_gap_ms: int = 250
    narration_to_dialogue_gap_ms: int = 400
    dialogue_to_narration_gap_ms: int = 500
    rapid_exchange_gap_ms: int = 120
    rapid_exchange_max_chars: int = 20
    high_emotion_threshold: float = 0.6
    high_emotion_extra_gap_ms: int = 150

    def gap_ms(self, previous: Any | None, current: Any, *, paragraph_changed: bool) -> int:
        if previous is None:
            return 0
        if paragraph_changed:
            return self.paragraph_gap_ms

        spoken_types = {SegmentType.DIALOGUE, SegmentType.INNER_THOUGHT}
        previous_spoken = previous.segment_type in spoken_types
        current_spoken = current.segment_type in spoken_types
        if previous_spoken and current_spoken:
            previous_character = str(getattr(previous, "character_id", "") or "")
            current_character = str(getattr(current, "character_id", "") or "")
            if previous_character and previous_character == current_character:
                return self.same_speaker_gap_ms
            if (
                max(self._text_length(previous), self._text_length(current))
                <= self.rapid_exchange_max_chars
            ):
                return self.rapid_exchange_gap_ms
            return self.speaker_switch_gap_ms
        if not previous_spoken and current_spoken:
            intensity = float(getattr(current, "emotion_intensity", 0.5) or 0.5)
            extra = 0
            if intensity > self.high_emotion_threshold:
                scale = min(
                    1.0,
                    (intensity - self.high_emotion_threshold)
                    / max(0.01, 1.0 - self.high_emotion_threshold),
                )
                extra = round(self.high_emotion_extra_gap_ms * scale)
            return self.narration_to_dialogue_gap_ms + extra
        if previous_spoken and not current_spoken:
            return self.dialogue_to_narration_gap_ms
        return self.default_gap_ms

    @staticmethod
    def _text_length(segment: Any) -> int:
        return len(str(getattr(segment, "synthesis_text", "") or segment.text or ""))


DEFAULT_SPEECH_TRANSITION_POLICY = SpeechTransitionPolicy()
