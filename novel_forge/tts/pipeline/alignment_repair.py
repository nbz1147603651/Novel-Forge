"""Selection policy for one bounded rollback-safe speech repair pass."""

from __future__ import annotations

from novel_forge.tts.platform.schemas import SpeechTimeline


def select_alignment_repair_segments(
    timeline: SpeechTimeline,
    *,
    minimum_coverage: float,
    maximum_text_error_rate: float,
) -> list[int]:
    """Return only segments with evidence that regeneration may improve speech."""

    selected: list[int] = []
    for alignment in timeline.alignments:
        low_coverage = alignment.status == "aligned" and alignment.coverage < minimum_coverage
        transcription_failed = (
            alignment.text_error_rate is not None
            and alignment.text_error_rate > maximum_text_error_rate
        )
        if low_coverage or transcription_failed:
            selected.append(alignment.segment_index)
    return sorted(set(selected))
