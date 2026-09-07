"""Resolve a vendor-neutral multi-bus mix plan from the real speech timeline."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Literal

from novel_forge.tts.platform.schemas import (
    AudioAssetRef,
    AudioExecutionPlan,
    AudioExecutionStage,
    MixEvent,
    MixPlan,
    SpeechTimeline,
)
from novel_forge.tts.schemas import DubbingScript, SynthesisResult, SynthesisStatus


def _gain_db(volume: float) -> float:
    return round(20.0 * math.log10(max(0.001, volume)), 2)


def _intervals_overlap(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and left[1] > right[0]


def _place_sfx_around_voice(
    requested_start_ms: int,
    duration_ms: int,
    voice_intervals: list[tuple[int, int]],
    *,
    maximum_shift_ms: int,
) -> tuple[int, str]:
    requested = (requested_start_ms, requested_start_ms + duration_ms)
    collisions = [
        interval for interval in voice_intervals if _intervals_overlap(requested, interval)
    ]
    if not collisions:
        return requested_start_ms, "script_anchor"
    candidates: list[int] = []
    for voice_start, voice_end in collisions:
        candidates.extend([max(0, voice_start - duration_ms), voice_end])
    safe_candidates = [
        value
        for value in candidates
        if not any(
            _intervals_overlap((value, value + duration_ms), interval)
            for interval in voice_intervals
        )
    ]
    if not safe_candidates:
        return requested_start_ms, "dialogue_overlap_unavoidable"
    nearest = min(safe_candidates, key=lambda value: abs(value - requested_start_ms))
    if abs(nearest - requested_start_ms) <= maximum_shift_ms:
        return nearest, "moved_to_dialogue_gap"
    return requested_start_ms, "dialogue_overlap_unavoidable"


def build_mix_plan(
    *,
    chapter_number: int,
    script: DubbingScript,
    timeline: SpeechTimeline,
    segment_results: list[SynthesisResult],
    resolved_sound_paths: dict[tuple[str, int], Path],
    execution_plan: AudioExecutionPlan,
    mastering_mode: Literal["single_pass", "two_pass_loudnorm"] | None = None,
    export_stems: bool = False,
) -> MixPlan:
    assets: list[AudioAssetRef] = []
    events: list[MixEvent] = []
    timeline_entries = {entry.segment_index: entry for entry in timeline.entries}
    voice_intervals: list[tuple[int, int]] = []
    placement_warnings: list[str] = []
    for result in segment_results:
        if result.status != SynthesisStatus.COMPLETED or not result.audio_path:
            continue
        entry = timeline_entries.get(result.segment_index)
        if entry is None:
            continue
        asset_id = f"voice:{result.segment_index}"
        assets.append(
            AudioAssetRef(
                asset_id=asset_id,
                kind="voice",
                path=result.audio_path,
                duration_ms=result.duration_ms,
                plugin_id=result.provider.value,
                request_hash=result.request_hash,
            )
        )
        events.append(
            MixEvent(
                event_id=f"voice:{result.segment_index}",
                asset_id=asset_id,
                bus="voice",
                start_ms=entry.start_ms,
                end_ms=entry.end_ms,
                priority=100,
                anchor_text=entry.text,
            )
        )
        voice_intervals.append((entry.start_ms, entry.end_ms))

    for index, bgm_cue in enumerate(script.bgm_suggestions):
        path = resolved_sound_paths.get(("bgm", index))
        if path is None:
            continue
        asset_id = f"bgm:{index}"
        assets.append(AudioAssetRef(asset_id=asset_id, kind="bgm", path=str(path)))
        events.append(
            MixEvent(
                event_id=asset_id,
                asset_id=asset_id,
                bus="bgm",
                start_ms=bgm_cue.start_ms,
                end_ms=bgm_cue.end_ms or timeline.total_duration_ms,
                gain_db=_gain_db(bgm_cue.volume),
                duck_under_voice_db=bgm_cue.ducking_db,
                fade_in_ms=bgm_cue.fade_in_ms,
                fade_out_ms=bgm_cue.fade_out_ms,
                priority=30,
                anchor_text=bgm_cue.mood or bgm_cue.track_name,
            )
        )

    for index, soundscape_cue in enumerate(script.soundscapes):
        path = resolved_sound_paths.get(("soundscape", index))
        if path is None:
            continue
        asset_id = f"soundscape:{index}"
        assets.append(AudioAssetRef(asset_id=asset_id, kind="soundscape", path=str(path)))
        events.append(
            MixEvent(
                event_id=asset_id,
                asset_id=asset_id,
                bus="soundscape",
                start_ms=soundscape_cue.start_ms,
                end_ms=soundscape_cue.end_ms or timeline.total_duration_ms,
                gain_db=_gain_db(soundscape_cue.volume),
                duck_under_voice_db=soundscape_cue.ducking_db,
                fade_in_ms=soundscape_cue.fade_in_ms,
                fade_out_ms=soundscape_cue.fade_out_ms,
                priority=40,
                anchor_text=soundscape_cue.description or soundscape_cue.name,
            )
        )

    for index, sfx_cue in enumerate(script.sfx_cues):
        path = resolved_sound_paths.get(("sfx", index))
        if path is None:
            continue
        duration = sfx_cue.duration_ms or 1000
        placed_start = sfx_cue.trigger_ms
        placement_reason = "script_anchor"
        if not sfx_cue.allow_dialogue_overlap:
            # For diegetic SFX anchored to a narration segment, exclude the
            # trigger segment's own interval from collision detection.  The
            # narration describes the sound happening, so overlapping with
            # that specific segment is narratively correct.
            effective_intervals = voice_intervals
            if sfx_cue.trigger_segment_index is not None:
                trigger_entry = timeline_entries.get(sfx_cue.trigger_segment_index)
                if trigger_entry is not None:
                    trigger_span = (trigger_entry.start_ms, trigger_entry.end_ms)
                    effective_intervals = [iv for iv in voice_intervals if iv != trigger_span]
            placed_start, placement_reason = _place_sfx_around_voice(
                sfx_cue.trigger_ms,
                duration,
                effective_intervals,
                maximum_shift_ms=sfx_cue.maximum_timing_shift_ms,
            )
        sfx_end = placed_start + duration
        overlaps_voice = any(
            placed_start < voice_end and sfx_end > voice_start
            for voice_start, voice_end in voice_intervals
        )
        if overlaps_voice and not sfx_cue.allow_dialogue_overlap:
            placement_warnings.append(f"SFX {index} 无法在限定偏移内避开人声。")
        asset_id = f"sfx:{index}"
        assets.append(
            AudioAssetRef(
                asset_id=asset_id,
                kind="sfx",
                path=str(path),
                duration_ms=duration,
            )
        )
        events.append(
            MixEvent(
                event_id=asset_id,
                asset_id=asset_id,
                bus="sfx",
                start_ms=placed_start,
                end_ms=sfx_end,
                gain_db=_gain_db(sfx_cue.volume) - (2.0 if overlaps_voice else 0.0),
                duck_under_voice_db=4.0 if overlaps_voice else 0.0,
                priority=sfx_cue.narrative_priority,
                anchor_text=sfx_cue.description or sfx_cue.effect_name,
                requested_start_ms=sfx_cue.trigger_ms,
                placement_reason=placement_reason,
            )
        )

    bed_events = [event for event in events if event.bus in {"bgm", "soundscape"}]
    overlapping_bed_ids: set[str] = set()
    for index, event in enumerate(bed_events):
        for other in bed_events[index + 1 :]:
            if _intervals_overlap(
                (event.start_ms, event.end_ms),
                (other.start_ms, other.end_ms),
            ):
                overlapping_bed_ids.update({event.event_id, other.event_id})
    if overlapping_bed_ids:
        events = [
            event.model_copy(update={"gain_db": event.gain_db - 2.0})
            if event.event_id in overlapping_bed_ids
            else event
            for event in events
        ]

    renderer = execution_plan.assignment_for(AudioExecutionStage.RENDERER)
    return MixPlan(
        chapter_number=chapter_number,
        assets=assets,
        events=sorted(events, key=lambda item: (item.start_ms, -item.priority)),
        renderer_plugin_id=(
            renderer.primary.plugin_id
            if renderer is not None and renderer.primary is not None
            else ""
        ),
        placement_warnings=placement_warnings,
        mastering_mode=mastering_mode or "two_pass_loudnorm",
        export_stems=export_stems,
    )
