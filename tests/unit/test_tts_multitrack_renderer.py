"""Tests for resolved MixPlan generation and single-pass FFmpeg rendering."""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

from novel_forge.core.config import Settings
from novel_forge.tts.audio_quality import evaluate_audio_quality
from novel_forge.tts.pipeline.mix_plan_builder import build_mix_plan
from novel_forge.tts.pipeline.multitrack_renderer import render_mix_plan
from novel_forge.tts.platform.config import build_audio_execution_plan
from novel_forge.tts.platform.schemas import (
    AlignmentResult,
    AudioAssetRef,
    AudioExecutionPlan,
    AudioQualityPreset,
    MixEvent,
    MixPlan,
    SpeechTimeline,
    SpeechTimelineEntry,
)
from novel_forge.tts.schemas import (
    BGMTiming,
    ChapterAudioResult,
    DubbingScript,
    DubbingSegment,
    SegmentType,
    SFXCue,
    SynthesisResult,
    SynthesisStatus,
)


def _tone(path: Path, *, frequency: float, duration_ms: int) -> None:
    sample_rate = 16000
    sample_count = round(sample_rate * duration_ms / 1000)
    frames = b"".join(
        struct.pack("<h", round(math.sin(2 * math.pi * frequency * index / sample_rate) * 5000))
        for index in range(sample_count)
    )
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(frames)


def test_mix_plan_contains_independent_buses_and_real_timing(tmp_path) -> None:
    voice = tmp_path / "voice.wav"
    bgm = tmp_path / "bgm.wav"
    _tone(voice, frequency=440, duration_ms=600)
    _tone(bgm, frequency=110, duration_ms=600)
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="真实人声",
            )
        ],
        bgm_suggestions=[
            BGMTiming(
                track_name="低沉氛围",
                start_ms=0,
                end_ms=900,
                ducking_db=10,
            )
        ],
    )
    timeline = SpeechTimeline(
        chapter_number=1,
        total_duration_ms=900,
        entries=[
            SpeechTimelineEntry(
                segment_index=0,
                language="zh",
                text="真实人声",
                start_ms=200,
                end_ms=800,
            )
        ],
    )
    result = SynthesisResult(
        segment_index=0,
        audio_path=str(voice),
        duration_ms=600,
        status=SynthesisStatus.COMPLETED,
    )
    plan = build_audio_execution_plan(
        Settings(_env_file=None, tts_default_provider="qwen3"),
        languages=["zh"],
    )

    mix_plan = build_mix_plan(
        chapter_number=1,
        script=script,
        timeline=timeline,
        segment_results=[result],
        resolved_sound_paths={("bgm", 0): bgm},
        execution_plan=plan,
    )

    assert [event.bus for event in mix_plan.events] == ["bgm", "voice"]
    voice_event = next(event for event in mix_plan.events if event.bus == "voice")
    bgm_event = next(event for event in mix_plan.events if event.bus == "bgm")
    assert (voice_event.start_ms, voice_event.end_ms) == (200, 800)
    assert bgm_event.duck_under_voice_db == 10
    assert mix_plan.renderer_plugin_id == "ffmpeg-smart-mixer"


def test_single_pass_renderer_creates_master_with_sidechain_bed(tmp_path) -> None:
    voice = tmp_path / "voice.wav"
    bed = tmp_path / "bed.wav"
    output = tmp_path / "master.mp3"
    _tone(voice, frequency=440, duration_ms=700)
    _tone(bed, frequency=100, duration_ms=400)
    mix_plan = MixPlan(
        chapter_number=1,
        assets=[
            AudioAssetRef(asset_id="voice:0", kind="voice", path=str(voice), duration_ms=700),
            AudioAssetRef(asset_id="bgm:0", kind="bgm", path=str(bed), duration_ms=400),
        ],
        events=[
            MixEvent(
                event_id="voice:0",
                asset_id="voice:0",
                bus="voice",
                start_ms=100,
                end_ms=800,
                priority=100,
            ),
            MixEvent(
                event_id="bgm:0",
                asset_id="bgm:0",
                bus="bgm",
                start_ms=0,
                end_ms=900,
                gain_db=-12,
                duck_under_voice_db=8,
                fade_in_ms=80,
                fade_out_ms=100,
            ),
        ],
        renderer_plugin_id="ffmpeg-smart-mixer",
    )

    report = render_mix_plan(mix_plan, output)

    assert report.passed is True
    assert report.mastering_succeeded is True
    assert report.rendered_event_count == 2
    assert report.failed_event_count == 0
    assert {item.event_id for item in report.events} == {"voice:0", "bgm:0"}
    assert all(item.source_hash for item in report.events)
    assert report.output_hash
    assert output.is_file()
    assert output.stat().st_size > 1000


def test_single_pass_renderer_exports_stems_when_requested(tmp_path) -> None:
    """export_stems=True in single_pass mode produces voice/bed stem wavs alongside master."""
    voice = tmp_path / "voice.wav"
    bed = tmp_path / "bed.wav"
    output = tmp_path / "master.mp3"
    _tone(voice, frequency=440, duration_ms=700)
    _tone(bed, frequency=100, duration_ms=400)
    mix_plan = MixPlan(
        chapter_number=1,
        assets=[
            AudioAssetRef(asset_id="voice:0", kind="voice", path=str(voice), duration_ms=700),
            AudioAssetRef(asset_id="bgm:0", kind="bgm", path=str(bed), duration_ms=400),
        ],
        events=[
            MixEvent(
                event_id="voice:0",
                asset_id="voice:0",
                bus="voice",
                start_ms=100,
                end_ms=800,
                priority=100,
            ),
            MixEvent(
                event_id="bgm:0",
                asset_id="bgm:0",
                bus="bgm",
                start_ms=0,
                end_ms=900,
                gain_db=-12,
                duck_under_voice_db=8,
                fade_in_ms=80,
                fade_out_ms=100,
            ),
        ],
        renderer_plugin_id="ffmpeg-smart-mixer",
        mastering_mode="single_pass",
        export_stems=True,
    )

    report = render_mix_plan(mix_plan, output)

    assert report.passed is True
    assert output.is_file()
    # Both voice and bed stems should be produced.
    assert "voice" in report.stem_paths
    assert "bed" in report.stem_paths
    voice_stem = Path(report.stem_paths["voice"])
    bed_stem = Path(report.stem_paths["bed"])
    assert voice_stem.is_file() and voice_stem.stat().st_size > 1000
    assert bed_stem.is_file() and bed_stem.stat().st_size > 1000


def test_two_pass_mode_exports_premaster_stems_without_rerendering_graph(tmp_path) -> None:
    """Commercial two-pass mastering exports the same pre-loudnorm bus stems."""
    voice = tmp_path / "voice.wav"
    bed = tmp_path / "bed.wav"
    output = tmp_path / "master.mp3"
    _tone(voice, frequency=440, duration_ms=700)
    _tone(bed, frequency=100, duration_ms=400)
    mix_plan = MixPlan(
        chapter_number=1,
        assets=[
            AudioAssetRef(asset_id="voice:0", kind="voice", path=str(voice), duration_ms=700),
            AudioAssetRef(asset_id="bgm:0", kind="bgm", path=str(bed), duration_ms=400),
        ],
        events=[
            MixEvent(
                event_id="voice:0",
                asset_id="voice:0",
                bus="voice",
                start_ms=100,
                end_ms=800,
                priority=100,
            ),
            MixEvent(
                event_id="bgm:0",
                asset_id="bgm:0",
                bus="bgm",
                start_ms=0,
                end_ms=900,
                gain_db=-12,
                duck_under_voice_db=8,
            ),
        ],
        renderer_plugin_id="ffmpeg-smart-mixer",
        mastering_mode="two_pass_loudnorm",  # default
        export_stems=True,
    )

    report = render_mix_plan(mix_plan, output)

    assert report.passed is True
    assert set(report.stem_paths) == {"voice", "bed"}
    assert all(Path(path).stat().st_size > 1000 for path in report.stem_paths.values())


def test_renderer_refuses_incomplete_event_graph_instead_of_dropping_sound(tmp_path) -> None:
    voice = tmp_path / "voice.wav"
    missing_bgm = tmp_path / "missing_bgm.wav"
    output = tmp_path / "master.mp3"
    _tone(voice, frequency=440, duration_ms=700)
    mix_plan = MixPlan(
        chapter_number=1,
        assets=[
            AudioAssetRef(asset_id="voice:0", kind="voice", path=str(voice)),
            AudioAssetRef(asset_id="bgm:0", kind="bgm", path=str(missing_bgm)),
        ],
        events=[
            MixEvent(
                event_id="voice:0",
                asset_id="voice:0",
                bus="voice",
                start_ms=0,
                end_ms=700,
            ),
            MixEvent(
                event_id="bgm:0",
                asset_id="bgm:0",
                bus="bgm",
                start_ms=0,
                end_ms=700,
            ),
        ],
    )

    report = render_mix_plan(mix_plan, output)

    assert report.passed is False
    assert report.rendered_event_count == 0
    assert report.failed_event_count == 2
    assert next(item for item in report.events if item.event_id == "bgm:0").reason == (
        "source_audio_missing_or_empty"
    )
    assert not output.exists()


def test_two_pass_master_meets_strict_objective_quality_gate(tmp_path) -> None:
    voice = tmp_path / "voice.wav"
    output = tmp_path / "master.mp3"
    _tone(voice, frequency=440, duration_ms=1500)
    voice_event = MixEvent(
        event_id="voice:0",
        asset_id="voice:0",
        bus="voice",
        start_ms=0,
        end_ms=1500,
    )
    mix_plan = MixPlan(
        chapter_number=1,
        assets=[AudioAssetRef(asset_id="voice:0", kind="voice", path=str(voice))],
        events=[voice_event],
        target_lufs=-16.0,
        true_peak_db=-1.5,
    )
    render_report = render_mix_plan(mix_plan, output)
    audio_result = ChapterAudioResult(
        chapter_number=1,
        script=DubbingScript(chapter_number=1),
        assembled_audio_path=str(output),
        is_complete=True,
        mix_render_report=render_report,
    )

    quality = evaluate_audio_quality(
        audio_result=audio_result,
        timeline=SpeechTimeline(
            chapter_number=1,
            alignments=[
                AlignmentResult(
                    segment_index=0,
                    expected_text="人声",
                    recognized_text="人声",
                    status="aligned",
                    coverage=1.0,
                    text_error_rate=0.0,
                )
            ],
        ),
        execution_plan=AudioExecutionPlan(preset=AudioQualityPreset.MASTER),
        mix_plan=mix_plan,
    )

    assert render_report.passed is True
    assert quality.measurement_available is True
    assert quality.loudness_within_target is True
    assert quality.true_peak_within_target is True
    assert quality.passed is True


def test_sfx_overlapping_voice_gets_headroom_and_sidechain_intent(tmp_path) -> None:
    voice = tmp_path / "voice.wav"
    sfx = tmp_path / "door.wav"
    _tone(voice, frequency=440, duration_ms=800)
    _tone(sfx, frequency=180, duration_ms=300)
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.DIALOGUE,
                text="别开门。",
            )
        ],
        sfx_cues=[
            SFXCue(
                effect_name="门响",
                trigger_ms=400,
                duration_ms=300,
                volume=0.8,
                allow_dialogue_overlap=True,
            )
        ],
    )
    timeline = SpeechTimeline(
        chapter_number=1,
        total_duration_ms=900,
        entries=[
            SpeechTimelineEntry(
                segment_index=0,
                text="别开门。",
                start_ms=100,
                end_ms=900,
            )
        ],
    )
    plan = build_audio_execution_plan(
        Settings(_env_file=None, tts_default_provider="qwen3"),
        languages=["zh"],
    )
    mix_plan = build_mix_plan(
        chapter_number=1,
        script=script,
        timeline=timeline,
        segment_results=[
            SynthesisResult(
                segment_index=0,
                audio_path=str(voice),
                duration_ms=800,
                status=SynthesisStatus.COMPLETED,
            )
        ],
        resolved_sound_paths={("sfx", 0): sfx},
        execution_plan=plan,
    )
    event = next(item for item in mix_plan.events if item.bus == "sfx")
    assert event.duck_under_voice_db == 4.0
    assert event.gain_db < 20 * math.log10(0.8)


def test_sfx_can_move_to_nearest_dialogue_gap(tmp_path) -> None:
    voice = tmp_path / "voice.wav"
    sfx = tmp_path / "door.wav"
    _tone(voice, frequency=440, duration_ms=800)
    _tone(sfx, frequency=180, duration_ms=300)
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(segment_index=0, segment_type=SegmentType.DIALOGUE, text="别开门。")
        ],
        sfx_cues=[SFXCue(effect_name="门响", trigger_ms=400, duration_ms=300)],
    )
    timeline = SpeechTimeline(
        chapter_number=1,
        total_duration_ms=1200,
        entries=[
            SpeechTimelineEntry(
                segment_index=0,
                text="别开门。",
                start_ms=100,
                end_ms=900,
            )
        ],
    )
    plan = build_audio_execution_plan(Settings(_env_file=None), languages=["zh"])
    mix_plan = build_mix_plan(
        chapter_number=1,
        script=script,
        timeline=timeline,
        segment_results=[
            SynthesisResult(
                segment_index=0,
                audio_path=str(voice),
                duration_ms=800,
                status=SynthesisStatus.COMPLETED,
            )
        ],
        resolved_sound_paths={("sfx", 0): sfx},
        execution_plan=plan,
    )
    event = next(item for item in mix_plan.events if item.bus == "sfx")
    assert event.start_ms == 900
    assert event.requested_start_ms == 400
    assert event.placement_reason == "moved_to_dialogue_gap"
    assert event.duck_under_voice_db == 0.0


def test_two_pass_report_carries_measured_loudness_for_quality_reuse(tmp_path) -> None:
    """P2-1: loudnorm analysis measurements are written back and reused."""
    voice = tmp_path / "voice.wav"
    output = tmp_path / "master.mp3"
    _tone(voice, frequency=440, duration_ms=1500)
    mix_plan = MixPlan(
        chapter_number=1,
        assets=[AudioAssetRef(asset_id="voice:0", kind="voice", path=str(voice))],
        events=[
            MixEvent(
                event_id="voice:0",
                asset_id="voice:0",
                bus="voice",
                start_ms=0,
                end_ms=1500,
            )
        ],
        target_lufs=-16.0,
        true_peak_db=-1.5,
    )
    render_report = render_mix_plan(mix_plan, output)

    assert render_report.passed is True
    # The two-pass analysis pass wrote its measurements back into the report.
    assert "input_i" in render_report.measured_loudness
    assert "input_tp" in render_report.measured_loudness
    assert "input_lra" in render_report.measured_loudness
    assert "input_thresh" in render_report.measured_loudness
    assert "target_offset" in render_report.measured_loudness

    # evaluate_audio_quality reuses them instead of re-decoding the master.
    audio_result = ChapterAudioResult(
        chapter_number=1,
        script=DubbingScript(chapter_number=1),
        assembled_audio_path=str(output),
        is_complete=True,
        mix_render_report=render_report,
    )
    quality = evaluate_audio_quality(
        audio_result=audio_result,
        timeline=SpeechTimeline(chapter_number=1),
        execution_plan=AudioExecutionPlan(preset=AudioQualityPreset.MASTER),
        mix_plan=mix_plan,
        measured_loudness=render_report.measured_loudness,
    )
    assert quality.measurement_available is True
    assert quality.integrated_lufs is not None
    assert quality.true_peak_db is not None


def test_mix_plan_mastering_mode_override(tmp_path) -> None:
    """P3-2: mastering_mode override reaches the MixPlan."""
    voice = tmp_path / "voice.wav"
    _tone(voice, frequency=440, duration_ms=600)
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="真实人声",
            )
        ],
    )
    timeline = SpeechTimeline(
        chapter_number=1,
        total_duration_ms=900,
        entries=[
            SpeechTimelineEntry(
                segment_index=0,
                language="zh",
                text="真实人声",
                start_ms=200,
                end_ms=800,
            )
        ],
    )
    result = SynthesisResult(
        segment_index=0,
        audio_path=str(voice),
        duration_ms=600,
        status=SynthesisStatus.COMPLETED,
    )
    plan = build_audio_execution_plan(
        Settings(_env_file=None, tts_default_provider="qwen3"),
        languages=["zh"],
    )

    audition_plan = build_mix_plan(
        chapter_number=1,
        script=script,
        timeline=timeline,
        segment_results=[result],
        resolved_sound_paths={},
        execution_plan=plan,
        mastering_mode="single_pass",
    )
    assert audition_plan.mastering_mode == "single_pass"

    standard_plan = build_mix_plan(
        chapter_number=1,
        script=script,
        timeline=timeline,
        segment_results=[result],
        resolved_sound_paths={},
        execution_plan=plan,
    )
    assert standard_plan.mastering_mode == "two_pass_loudnorm"

    commercial_plan = build_mix_plan(
        chapter_number=1,
        script=script,
        timeline=timeline,
        segment_results=[result],
        resolved_sound_paths={},
        execution_plan=plan,
        export_stems=True,
    )
    assert commercial_plan.export_stems is True


def test_word_level_subtitles_use_token_timeline(tmp_path) -> None:
    """P3-1: word-level SRT is generated from token timestamps."""
    from novel_forge.tts.pipeline.assemble_audio_step import AssembleAudioStep
    from novel_forge.tts.platform.schemas import AlignmentToken

    step = AssembleAudioStep(settings=Settings(_env_file=None))
    timeline = SpeechTimeline(
        chapter_number=1,
        entries=[
            SpeechTimelineEntry(
                segment_index=0,
                language="zh",
                text="雨停了",
                start_ms=0,
                end_ms=900,
            )
        ],
        alignments=[
            AlignmentResult(
                segment_index=0,
                expected_text="雨停了",
                recognized_text="雨停了",
                status="aligned",
                coverage=1.0,
                tokens=[
                    AlignmentToken(text="雨", start_ms=0, end_ms=200),
                    AlignmentToken(text="停", start_ms=200, end_ms=500),
                    AlignmentToken(text="了", start_ms=500, end_ms=900),
                ],
            )
        ],
    )
    output = tmp_path / "chapter.srt"
    step._generate_word_level_subtitles(timeline, str(output))  # noqa: SLF001

    content = output.read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:00,200" in content
    assert "雨" in content
    assert content.count("-->") == 3
