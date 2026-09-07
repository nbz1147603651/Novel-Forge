"""Tests for real post-TTS speech timeline construction."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from novel_forge.core.config import Settings
from novel_forge.tts.pipeline.align_speech_timeline_step import (
    AlignSpeechTimelineInput,
    AlignSpeechTimelineStep,
    apply_speech_timeline_to_script,
)
from novel_forge.tts.platform.config import build_audio_execution_plan
from novel_forge.tts.platform.registry import AudioPluginRegistry
from novel_forge.tts.schemas import (
    BGMTiming,
    DubbingScript,
    DubbingSegment,
    SegmentType,
    SFXCue,
    SoundscapeCue,
    SynthesisResult,
    SynthesisStatus,
)


class _FakeSidecar:
    def __init__(self, *, fail: bool = False, offset_ms: int = 0, partial: bool = False) -> None:
        self.fail = fail
        self.offset_ms = offset_ms
        self.partial = partial

    async def transcribe(self, *_args, **_kwargs):
        if self.fail:
            raise RuntimeError("offline")
        return {"text": "雨停了"}

    async def align(self, *_args, **_kwargs):
        if self.fail:
            raise RuntimeError("offline")
        items = [
            {
                "text": "雨",
                "start_ms": self.offset_ms,
                "end_ms": 180 + self.offset_ms,
                "confidence": 0.98,
                "unit": "character",
            },
            {
                "text": "停了",
                "start_ms": 220 + self.offset_ms,
                "end_ms": 650 + self.offset_ms,
                "confidence": 0.95,
                "unit": "character",
            },
        ]
        # Partial alignment: only the first character aligns, dropping coverage
        # below the gate so the conditional validator path is exercised.
        return {"items": items if not self.partial else items[:1]}

    async def aclose(self) -> None:
        return None


def _input(tmp_path: Path, *, preset: str = "master") -> AlignSpeechTimelineInput:
    audio = tmp_path / "segment.wav"
    audio.write_bytes(b"RIFF")
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="雨停了",
                language_code="zh",
            )
        ],
    )
    result = SynthesisResult(
        segment_index=0,
        audio_path=str(audio),
        duration_ms=900,
        status=SynthesisStatus.COMPLETED,
    )
    settings = Settings(
        _env_file=None,
        tts_default_provider="qwen3",
        audio_quality_preset=preset,
        audio_memory_budget="high",
        tts_asr_cache_enabled=False,
    )
    return AlignSpeechTimelineInput(
        chapter_number=1,
        script=script,
        segment_results=[result],
        execution_plan=build_audio_execution_plan(settings, languages=["zh"]),
    )


async def test_alignment_step_builds_absolute_word_timeline(tmp_path, monkeypatch) -> None:
    settings = Settings(_env_file=None, audio_quality_preset="master", tts_asr_cache_enabled=False)
    step = AlignSpeechTimelineStep(
        settings=settings,
        registry=AudioPluginRegistry.builtins(),
    )
    clients = {
        "sherpa-sensevoice-int8": _FakeSidecar(),
        "qwen3-forced-aligner-0.6b": _FakeSidecar(),
        "whisperx": _FakeSidecar(offset_ms=20),
    }
    monkeypatch.setattr(step, "_client_for", lambda plugin_id: clients[plugin_id])

    timeline = await step.run(_input(tmp_path))

    assert timeline.entries[0].alignment_status == "aligned"
    assert timeline.entries[0].tokens[0].text == "雨"
    assert timeline.alignments[0].plugin_id == "whisperx"
    # Conditional validator (P0-2): a healthy full-coverage alignment skips the
    # second engine pass, so no validator evidence is attached.
    assert timeline.alignments[0].validator_plugin_id == ""
    assert timeline.alignments[0].validator_p95_delta_ms is None
    assert timeline.alignments[0].text_error_rate == 0.0


async def test_low_coverage_segment_runs_validator(tmp_path, monkeypatch) -> None:
    """Segments below the coverage gate still run the conditional validator."""
    settings = Settings(_env_file=None, audio_quality_preset="master", tts_asr_cache_enabled=False)
    step = AlignSpeechTimelineStep(
        settings=settings,
        registry=AudioPluginRegistry.builtins(),
    )
    clients = {
        "sherpa-sensevoice-int8": _FakeSidecar(),
        "qwen3-forced-aligner-0.6b": _FakeSidecar(),
        # Partial alignment drops coverage below the 0.85 gate.
        "whisperx": _FakeSidecar(offset_ms=20, partial=True),
    }
    monkeypatch.setattr(step, "_client_for", lambda plugin_id: clients[plugin_id])

    timeline = await step.run(_input(tmp_path))

    assert timeline.alignments[0].status == "aligned"
    assert timeline.alignments[0].coverage < 0.85
    # The conditional validator ran for the suspicious segment.
    assert timeline.alignments[0].validator_plugin_id == "qwen3-forced-aligner-0.6b"
    assert timeline.alignments[0].validator_p95_delta_ms == 20.0


def _multi_segment_input(tmp_path: Path) -> AlignSpeechTimelineInput:
    audio = tmp_path / "segment.wav"
    audio.write_bytes(b"RIFF")
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="雨停了",
                language_code="zh",
            ),
            DubbingSegment(
                segment_index=1,
                segment_type=SegmentType.NARRATION,
                text="风止了",
                language_code="zh",
            ),
            DubbingSegment(
                segment_index=2,
                segment_type=SegmentType.NARRATION,
                text="云散了",
                language_code="zh",
            ),
        ],
    )
    results = [
        SynthesisResult(
            segment_index=index,
            audio_path=str(audio),
            duration_ms=900,
            status=SynthesisStatus.COMPLETED,
        )
        for index in range(3)
    ]
    settings = Settings(_env_file=None, audio_quality_preset="master", tts_asr_cache_enabled=False)
    return AlignSpeechTimelineInput(
        chapter_number=1,
        script=script,
        segment_results=results,
        execution_plan=build_audio_execution_plan(settings, languages=["zh"]),
    )


async def test_repair_mode_aligns_only_repair_segments(tmp_path, monkeypatch) -> None:
    """P0-1: repair mode re-aligns only the listed segments and inherits the rest."""
    settings = Settings(_env_file=None, audio_quality_preset="master", tts_asr_cache_enabled=False)
    step = AlignSpeechTimelineStep(
        settings=settings,
        registry=AudioPluginRegistry.builtins(),
    )
    clients = {
        "sherpa-sensevoice-int8": _FakeSidecar(),
        "qwen3-forced-aligner-0.6b": _FakeSidecar(),
        "whisperx": _FakeSidecar(offset_ms=20),
    }
    monkeypatch.setattr(step, "_client_for", lambda plugin_id: clients[plugin_id])

    baseline = await step.run(_multi_segment_input(tmp_path))
    assert all(item.status == "aligned" for item in baseline.alignments)

    # Repair only segment 1; the other two must inherit their alignments.
    repaired_input = dataclasses.replace(
        _multi_segment_input(tmp_path),
        repair_segment_indices=[1],
        previous_timeline=baseline,
    )
    repaired = await step.run(repaired_input)

    assert repaired.entries[0].alignment_status == "inherited"
    assert repaired.entries[2].alignment_status == "inherited"
    assert repaired.entries[1].alignment_status == "aligned"
    # Inherited rows keep the exact tokens and coverage from the baseline.
    assert repaired.alignments[0].tokens == baseline.alignments[0].tokens
    assert repaired.alignments[2].tokens == baseline.alignments[2].tokens
    assert repaired.alignments[1].coverage == baseline.alignments[1].coverage


async def test_alignment_failure_is_auditable_segment_fallback(tmp_path, monkeypatch) -> None:
    settings = Settings(_env_file=None, tts_asr_cache_enabled=False)
    step = AlignSpeechTimelineStep(
        settings=settings,
        registry=AudioPluginRegistry.builtins(),
    )
    monkeypatch.setattr(step, "_client_for", lambda _plugin_id: _FakeSidecar(fail=True))

    timeline = await step.run(_input(tmp_path, preset="production"))

    alignment = timeline.alignments[0]
    assert alignment.status == "segment_fallback"
    assert alignment.segment_end_ms == 900
    assert "offline" in alignment.error


async def test_language_route_changes_the_aligner_used_at_runtime(tmp_path, monkeypatch) -> None:
    input_data = _input(tmp_path, preset="production")
    input_data.script.segments[0] = input_data.script.segments[0].model_copy(
        update={"language_code": "ja"}
    )
    route_settings = Settings(
        _env_file=None,
        tts_default_provider="qwen3",
        audio_language_overrides=json.dumps({"ja": "whisperx"}),
        tts_asr_cache_enabled=False,
    )
    input_data.execution_plan = build_audio_execution_plan(route_settings, languages=["ja"])
    step = AlignSpeechTimelineStep(
        settings=route_settings,
        registry=AudioPluginRegistry.builtins(),
    )
    clients = {"whisperx": _FakeSidecar()}
    monkeypatch.setattr(step, "_client_for", lambda plugin_id: clients[plugin_id])

    timeline = await step.run(input_data)

    assert timeline.alignments[0].plugin_id == "whisperx"
    assert timeline.alignments[0].language == "ja"


def test_real_timeline_resolves_sound_cue_anchors(tmp_path) -> None:
    input_data = _input(tmp_path, preset="production")
    script = input_data.script.model_copy(
        update={
            "bgm_suggestions": [
                BGMTiming(track_name="雨夜", start_segment_index=0, end_segment_index=0)
            ],
            "soundscapes": [SoundscapeCue(name="雨声", start_segment_index=0, end_segment_index=0)],
            "sfx_cues": [SFXCue(effect_name="雷声", trigger_segment_index=0, offset_ms=120)],
        }
    )
    from novel_forge.tts.platform.schemas import SpeechTimeline, SpeechTimelineEntry

    timeline = SpeechTimeline(
        chapter_number=1,
        total_duration_ms=900,
        entries=[
            SpeechTimelineEntry(
                segment_index=0,
                language="zh",
                text="雨停了",
                start_ms=300,
                end_ms=900,
            )
        ],
    )

    resolved = apply_speech_timeline_to_script(script, timeline)

    assert resolved.bgm_suggestions[0].start_ms == 300
    assert resolved.bgm_suggestions[0].end_ms == 900
    assert resolved.soundscapes[0].start_ms == 300
    assert resolved.sfx_cues[0].trigger_ms == 420
    assert resolved.metadata["speech_timeline_resolved"] is True


async def test_asr_cache_reuses_engine_results(tmp_path, monkeypatch) -> None:
    """P3-3: identical ASR/alignment calls are served from the disk cache."""
    settings = Settings(
        _env_file=None,
        audio_quality_preset="master",
        tts_asr_cache_ttl_hours=24,
        storage_root=str(tmp_path),
    )
    step = AlignSpeechTimelineStep(
        settings=settings,
        registry=AudioPluginRegistry.builtins(),
    )
    audio = tmp_path / "seg.wav"
    audio.write_bytes(b"RIFF")
    engine_calls: list[str] = []

    def _client_for(plugin_id: str) -> _FakeSidecar:
        engine_calls.append(plugin_id)
        return _FakeSidecar()

    monkeypatch.setattr(step, "_client_for", _client_for)

    await step._transcribe("whisperx", audio, language="zh", expected_text="雨停了")  # noqa: SLF001
    await step._align("whisperx", audio, text="雨停了", language="zh")  # noqa: SLF001
    assert len(engine_calls) == 2

    # Second pass must be fully served from the disk cache (no engine calls).
    engine_calls.clear()
    await step._transcribe("whisperx", audio, language="zh", expected_text="雨停了")  # noqa: SLF001
    await step._align("whisperx", audio, text="雨停了", language="zh")  # noqa: SLF001
    assert engine_calls == []


def test_asr_cache_capacity_evicts_oldest_entries(tmp_path) -> None:
    """P3-3: the ASR cache directory is bounded and evicts oldest files."""
    from novel_forge.tts.pipeline.align_speech_timeline_step import (
        _ASR_CACHE_MAX_ENTRIES,
        _enforce_asr_cache_capacity,
    )

    cache_dir = tmp_path / "tts" / "asr_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    for index in range(_ASR_CACHE_MAX_ENTRIES + 8):
        entry = cache_dir / f"plugin_{index:04d}.json"
        entry.write_text('{"saved_at": "2026-01-01T00:00:00+00:00", "result": {}}')
        # Stagger mtimes so eviction order is deterministic (oldest first).
        import os

        os.utime(entry, (index, index))

    _enforce_asr_cache_capacity(cache_dir)

    remaining = sorted(item.name for item in cache_dir.iterdir())
    assert len(remaining) == _ASR_CACHE_MAX_ENTRIES
    # The 8 oldest entries were evicted; the newest survive.
    assert "plugin_0000.json" not in remaining
    assert "plugin_0007.json" not in remaining
    assert "plugin_0008.json" in remaining
