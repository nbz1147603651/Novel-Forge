"""Synthesis progress checkpoint and reusable-take resolution.

Extracted from execution.py to isolate progress persistence concerns.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from novel_forge.obs.logger import get_logger
from novel_forge.persistence.filesystem import atomic_write_json
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.schemas import (
    DubbingScript,
    ReusableTakeRecord,
    SegmentType,
    SynthesisResult,
    SynthesisStatus,
    TTSProgressState,
    TTSProvider,
)

_log = get_logger("workspace.tts")


def _load_json(path: Path) -> dict[str, Any]:
    """Load an optional project artifact without making legacy projects fail."""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _log.warning("Failed to load optional TTS artifact %s: %s", path, exc)
        return {}
    return payload if isinstance(payload, dict) else {}


def _resolve_reusable_takes(
    progress_state: TTSProgressState,
    current_script: DubbingScript,
    *,
    script_hash: str = "",
) -> tuple[list[int], dict[str, str], list[SynthesisResult]]:
    """Return (completed_indices, completed_hashes, completed_results) by content identity.

    Maps ``reusable_takes`` keyed by ``segment_uid`` onto the current script's
    segment positions.  Unchanged segments are reused even when their
    ``segment_index`` has shifted after script regeneration.

    When *script_hash* matches the checkpoint exactly, positional entries from
    ``completed_segments`` / ``completed_segment_hashes`` are also trusted
    (they are faster and avoid re-probing the uid map).
    """
    completed_indices: list[int] = []
    completed_hashes: dict[str, str] = {}
    completed_results: list[SynthesisResult] = []

    script_hash_matches = bool(script_hash and progress_state.script_hash == script_hash)
    if script_hash_matches:
        # Fast path: script unchanged, trust positional checkpoint.
        completed_indices = list(progress_state.completed_segments)
        completed_hashes = dict(progress_state.completed_segment_hashes)
        completed_results = [
            r for r in progress_state.segment_results if r.status == SynthesisStatus.COMPLETED
        ]
        if completed_indices:
            _log.info(
                "Resuming %d segments from exact-script checkpoint",
                len(completed_indices),
            )
            return completed_indices, completed_hashes, completed_results

    # Slow path: script changed, resolve by content identity.
    reused_count = 0
    for segment in current_script.segments:
        if segment.segment_type in (SegmentType.BGM, SegmentType.SFX, SegmentType.SILENCE):
            continue
        uid = segment.segment_uid
        if not uid:
            continue
        record = progress_state.reusable_takes.get(uid)
        if record is None:
            continue
        audio_path = Path(record.audio_path)
        if not audio_path.exists() or audio_path.stat().st_size <= 0:
            continue
        completed_indices.append(segment.segment_index)
        completed_hashes[str(segment.segment_index)] = record.request_hash
        completed_results.append(
            SynthesisResult(
                segment_index=segment.segment_index,
                audio_path=record.audio_path,
                duration_ms=record.duration_ms,
                status=SynthesisStatus.COMPLETED,
                provider=progress_state.provider,
                voice_id=record.voice_id,
                request_hash=record.request_hash,
                output_hash=record.output_hash,
                cost_usd=record.cost_usd,
            )
        )
        reused_count += 1
    if reused_count:
        _log.info(
            "Reusing %d segments by content identity (script_hash differs)",
            reused_count,
        )
    return completed_indices, completed_hashes, completed_results


def _persist_synthesis_progress_snapshot(
    *,
    layout: ProjectLayout,
    chapter_number: int,
    script: DubbingScript,
    segment_results: list[SynthesisResult],
    voice_team_hash: str,
    script_hash: str,
    provider: TTSProvider,
) -> TTSProgressState:
    """Persist a full synthesis checkpoint before audio assembly begins.

    Segment audio is generated concurrently and may be expensive.  Keeping the
    complete result list here means a later assembly/UI failure cannot discard
    successful artifacts or hide the provider errors needed to resume safely.
    """
    progress_path = layout.tts_progress_path_for_chapter(chapter_number)
    progress_state: TTSProgressState | None = None
    if progress_path.exists():
        try:
            progress_state = TTSProgressState.model_validate(_load_json(progress_path))
        except Exception as exc:
            _log.warning("Failed to load TTS progress snapshot: %s", exc)

    matches_current_run = bool(
        progress_state is not None
        and progress_state.chapter_number == chapter_number
        and progress_state.script_hash == script_hash
        and progress_state.voice_team_hash == voice_team_hash
        and progress_state.provider == provider
    )
    # Voice team / provider unchanged -> keep reusable_takes for content-addressed
    # reuse across script edits.  Only clear the index when the voice team or
    # provider has actually changed (audio from a different cast is unsafe).
    preserve_reusable = bool(
        progress_state is not None
        and progress_state.voice_team_hash == voice_team_hash
        and progress_state.provider == provider
    )
    if not matches_current_run:
        if not preserve_reusable:
            # Full reset: voice team or provider changed, all audio obsolete.
            progress_state = TTSProgressState(
                chapter_number=chapter_number,
                voice_team_done=True,
                script_done=True,
                voice_team_hash=voice_team_hash,
                script_hash=script_hash,
                provider=provider,
            )
        else:
            # Script changed but voice team stable: keep reusable_takes, reset
            # positional bookkeeping (they will be rebuilt from reusable_takes
            # on next resume).
            if progress_state is None:
                raise RuntimeError("progress_state must not be None when preserving reusable_takes")
            progress_state.chapter_number = chapter_number
            progress_state.voice_team_done = True
            progress_state.script_done = True
            progress_state.script_hash = script_hash
            progress_state.completed_segments = []
            progress_state.completed_segment_hashes = {}
            progress_state.segment_results = []
            progress_state.last_error = ""
    if progress_state is None:
        raise RuntimeError("progress_state must not be None after initialization")

    results_by_index = {result.segment_index: result for result in segment_results}
    required_indices = {
        segment.segment_index
        for segment in script.segments
        if segment.segment_type
        in (SegmentType.NARRATION, SegmentType.DIALOGUE, SegmentType.INNER_THOUGHT)
    }
    completed = sorted(
        result.segment_index
        for result in segment_results
        if result.status == SynthesisStatus.COMPLETED
    )
    failed = sorted(
        result.segment_index
        for result in segment_results
        if result.status == SynthesisStatus.FAILED
    )

    progress_state.voice_team_done = True
    progress_state.script_done = True
    progress_state.voice_team_hash = voice_team_hash
    progress_state.script_hash = script_hash
    progress_state.provider = provider
    progress_state.completed_segments = completed
    progress_state.completed_segment_hashes = {
        str(result.segment_index): result.request_hash
        for result in segment_results
        if result.status == SynthesisStatus.COMPLETED and result.request_hash
    }
    progress_state.failed_segments = failed
    progress_state.failed_segment_errors = {
        str(result.segment_index): result.error_message[:512]
        for result in segment_results
        if result.status == SynthesisStatus.FAILED and result.error_message
    }
    progress_state.segment_results = list(segment_results)
    # Update reusable_takes: each COMPLETED segment is indexed by content identity
    # so it can be reused even after script regeneration shifts segment_index.
    seg_by_index = {seg.segment_index: seg for seg in script.segments}
    for result in segment_results:
        if result.status != SynthesisStatus.COMPLETED or not result.request_hash:
            continue
        segment = seg_by_index.get(result.segment_index)
        if segment is None or not segment.segment_uid:
            continue
        if not result.audio_path or not Path(result.audio_path).exists():
            continue
        progress_state.reusable_takes[segment.segment_uid] = ReusableTakeRecord(
            segment_uid=segment.segment_uid,
            segment_index_at_creation=result.segment_index,
            audio_path=result.audio_path,
            duration_ms=result.duration_ms,
            request_hash=result.request_hash,
            voice_id=result.voice_id,
            output_hash=result.output_hash,
            cost_usd=result.cost_usd,
        )
    progress_state.synthesis_done = bool(required_indices) and all(
        results_by_index.get(index, SynthesisResult(segment_index=index)).status
        == SynthesisStatus.COMPLETED
        for index in required_indices
    )
    progress_state.assembly_done = False
    progress_state.last_error = (
        f"{len(failed)} segment(s) failed; resume will retry only missing segments."
        if failed
        else ""
    )
    progress_state.updated_at = datetime.now(timezone.utc)
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(progress_path, progress_state.model_dump(mode="json"))
    return progress_state
