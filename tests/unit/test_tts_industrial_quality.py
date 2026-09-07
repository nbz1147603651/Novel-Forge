"""Objective per-take gates and bounded alignment repair selection."""

from __future__ import annotations

from novel_forge.tts.audio_quality import apply_delivery_readiness, evaluate_audio_quality
from novel_forge.tts.delivery_profile import resolve_audio_delivery_profile
from novel_forge.tts.pipeline.alignment_repair import select_alignment_repair_segments
from novel_forge.tts.pipeline.segment_quality import evaluate_synthesized_segment
from novel_forge.tts.platform.schemas import (
    AlignmentResult,
    AudioAssetRef,
    AudioExecutionPlan,
    AudioQualityPreset,
    MixEvent,
    MixPlan,
    SpeechTimeline,
)
from novel_forge.tts.schemas import (
    ChapterAudioResult,
    DubbingScript,
    MixRenderEventResult,
    MixRenderReport,
    ProviderTakeEvidence,
    TTSRequest,
    TTSResponse,
)


def test_segment_quality_rejects_implausibly_fast_take() -> None:
    decision = evaluate_synthesized_segment(
        TTSRequest(text="这是一段明显不可能在瞬间完成的很长句子"),
        TTSResponse(audio_data=b"x" * 1024),
        duration_ms=200,
        audio_size_bytes=1024,
        minimum_duration_ms=100,
        maximum_characters_per_second=20,
    )
    assert decision.passed is False
    assert "超速" in decision.warnings[0]


def test_segment_quality_keeps_provider_warning_as_advisory() -> None:
    decision = evaluate_synthesized_segment(
        TTSRequest(text="你好世界"),
        TTSResponse(
            audio_data=b"x" * 1024,
            take_evidence=ProviderTakeEvidence(
                provider_status="2", invisible_character_ratio=0.08
            ),
        ),
        duration_ms=1200,
        audio_size_bytes=1024,
        minimum_duration_ms=100,
        maximum_characters_per_second=20,
    )
    assert decision.passed is True
    assert "不可见字符" in decision.warnings[0]


def test_alignment_repair_requires_regeneration_evidence() -> None:
    timeline = SpeechTimeline(
        chapter_number=1,
        alignments=[
            AlignmentResult(
                segment_index=1,
                status="aligned",
                coverage=0.6,
                text_error_rate=0.05,
            ),
            AlignmentResult(
                segment_index=2,
                status="segment_fallback",
                coverage=0.0,
                text_error_rate=None,
            ),
            AlignmentResult(
                segment_index=3,
                status="segment_fallback",
                coverage=0.0,
                text_error_rate=0.4,
            ),
        ],
    )
    assert select_alignment_repair_segments(
        timeline,
        minimum_coverage=0.85,
        maximum_text_error_rate=0.12,
    ) == [1, 3]


def test_master_quality_counts_fallback_segments_and_unresolved_story_sound() -> None:
    timeline = SpeechTimeline(
        chapter_number=1,
        alignments=[
            AlignmentResult(segment_index=0, expected_text="已对齐", status="aligned", coverage=1),
            AlignmentResult(
                segment_index=1,
                expected_text="未对齐",
                status="segment_fallback",
                coverage=0,
            ),
        ],
    )
    report = evaluate_audio_quality(
        audio_result=ChapterAudioResult(
            chapter_number=1,
            script=DubbingScript(chapter_number=1),
            assembled_audio_path="master.mp3",
            is_complete=True,
        ),
        timeline=timeline,
        execution_plan=AudioExecutionPlan(preset=AudioQualityPreset.MASTER),
        unresolved_sound_cues=1,
    )
    assert report.alignment_coverage == 0.5
    assert report.passed is False
    assert "alignment_coverage" in report.blocking_reasons
    assert "unresolved_sound_cues" in report.blocking_reasons


def test_all_alignment_fallback_downgrades_noncommercial_gate_to_warning() -> None:
    """When every aligner is unavailable (all segment_fallback, zero aligned),
    the coverage gate is downgraded to a warning instead of blocking delivery.

    This is an environmental failure (sidecars offline / wrong audio format),
    not a content quality problem -- the assembled speech track is valid.
    Non-commercial production can still produce an operator-reviewable file,
    but the warning must state that this is not a commercial release gate.
    """
    timeline = SpeechTimeline(
        chapter_number=1,
        alignments=[
            AlignmentResult(
                segment_index=0,
                expected_text="旁白文本",
                status="segment_fallback",
                coverage=0.0,
            ),
            AlignmentResult(
                segment_index=1,
                expected_text="对白文本",
                status="segment_fallback",
                coverage=0.0,
            ),
        ],
    )
    report = evaluate_audio_quality(
        audio_result=ChapterAudioResult(
            chapter_number=1,
            script=DubbingScript(chapter_number=1),
            assembled_audio_path="master.mp3",
            is_complete=True,
        ),
        timeline=timeline,
        execution_plan=AudioExecutionPlan(preset=AudioQualityPreset.PRODUCTION),
    )
    assert report.alignment_coverage == 0.0
    assert report.passed is True
    assert "alignment_coverage" not in report.blocking_reasons
    # A warning explains the environmental failure
    assert any("强制对齐环境不可用" in w for w in report.speech_masking_warnings)


def test_all_alignment_fallback_blocks_commercial_master_delivery() -> None:
    timeline = SpeechTimeline(
        chapter_number=1,
        alignments=[
            AlignmentResult(
                segment_index=0,
                expected_text="旁白文本",
                status="segment_fallback",
                coverage=0.0,
            )
        ],
    )
    report = evaluate_audio_quality(
        audio_result=ChapterAudioResult(
            chapter_number=1,
            script=DubbingScript(chapter_number=1),
            assembled_audio_path="master.mp3",
            is_complete=True,
        ),
        timeline=timeline,
        execution_plan=AudioExecutionPlan(preset=AudioQualityPreset.MASTER),
    )
    assert report.passed is False
    assert "alignment_backend_unavailable" in report.blocking_reasons
    assert any("商业/Master" in warning for warning in report.speech_masking_warnings)


def test_commercial_master_blocks_unreviewed_sound_asset_rights(monkeypatch) -> None:
    monkeypatch.setattr(
        "novel_forge.tts.audio_quality._measure_loudness",
        lambda _path: (-16.0, -1.5),
    )
    report = evaluate_audio_quality(
        audio_result=ChapterAudioResult(
            chapter_number=1,
            script=DubbingScript(chapter_number=1),
            assembled_audio_path="master.mp3",
            is_complete=True,
        ),
        timeline=SpeechTimeline(
            chapter_number=1,
            alignments=[
                AlignmentResult(
                    segment_index=0,
                    expected_text="可信对齐",
                    recognized_text="可信对齐",
                    status="aligned",
                    coverage=1.0,
                    text_error_rate=0.0,
                )
            ],
        ),
        execution_plan=AudioExecutionPlan(preset=AudioQualityPreset.MASTER),
        commercial_rights_issues=["bgm_night:review_required"],
    )

    assert report.passed is False
    assert report.commercial_rights_issues == ["bgm_night:review_required"]
    assert "sound_asset_commercial_rights" in report.blocking_reasons


def test_partial_fallback_still_blocks_coverage_gate() -> None:
    """When some segments aligned but coverage is still below threshold,
    the gate blocks normally -- the downgrade only applies to *total*
    alignment failure, not partial low coverage.
    """
    timeline = SpeechTimeline(
        chapter_number=1,
        alignments=[
            AlignmentResult(
                segment_index=0,
                expected_text="已对齐但覆盖率低",
                status="aligned",
                coverage=0.3,
            ),
            AlignmentResult(
                segment_index=1,
                expected_text="未对齐",
                status="segment_fallback",
                coverage=0.0,
            ),
        ],
    )
    report = evaluate_audio_quality(
        audio_result=ChapterAudioResult(
            chapter_number=1,
            script=DubbingScript(chapter_number=1),
            assembled_audio_path="master.mp3",
            is_complete=True,
        ),
        timeline=timeline,
        execution_plan=AudioExecutionPlan(preset=AudioQualityPreset.MASTER),
    )
    assert report.alignment_coverage == 0.15
    assert report.passed is False
    assert "alignment_coverage" in report.blocking_reasons


def test_delivery_readiness_is_stricter_than_speech_completion() -> None:
    audio_result = ChapterAudioResult(
        chapter_number=1,
        script=DubbingScript(chapter_number=1),
        assembled_audio_path="master.mp3",
        is_complete=True,
    )
    # Non-MASTER preset: unresolved_sound_cues does NOT block delivery.
    quality_report = evaluate_audio_quality(
        audio_result=audio_result,
        timeline=SpeechTimeline(chapter_number=1),
        execution_plan=AudioExecutionPlan(preset=AudioQualityPreset.QUICK_PREVIEW),
        unresolved_sound_cues=2,
    )

    ready = apply_delivery_readiness(audio_result, quality_report)

    assert ready.delivery_ready is True
    assert ready.delivery_blocking_reasons == []
    assert ready.is_complete is True


def test_delivery_readiness_blocks_master_preset_with_unresolved_sound_cues() -> None:
    audio_result = ChapterAudioResult(
        chapter_number=1,
        script=DubbingScript(chapter_number=1),
        assembled_audio_path="master.mp3",
        is_complete=True,
    )
    # MASTER preset: unresolved_sound_cues IS a blocking reason.
    quality_report = evaluate_audio_quality(
        audio_result=audio_result,
        timeline=SpeechTimeline(chapter_number=1),
        execution_plan=AudioExecutionPlan(preset=AudioQualityPreset.MASTER),
        unresolved_sound_cues=1,
    )

    blocked = apply_delivery_readiness(audio_result, quality_report)

    assert quality_report.passed is False
    assert "unresolved_sound_cues" in quality_report.blocking_reasons
    assert blocked.is_complete is True
    assert blocked.delivery_ready is False
    assert "unresolved_sound_cues" in blocked.delivery_blocking_reasons


def test_commercial_delivery_enforces_master_gates_without_rerouting_plugins() -> None:
    audio_result = ChapterAudioResult(
        chapter_number=1,
        script=DubbingScript(chapter_number=1),
        assembled_audio_path="master.mp3",
        is_complete=True,
    )
    commercial = resolve_audio_delivery_profile("commercial")
    report = evaluate_audio_quality(
        audio_result=audio_result,
        timeline=SpeechTimeline(chapter_number=1),
        # Routing intentionally remains production.  Delivery policy, not
        # provider routing, is what upgrades acceptance to MASTER strictness.
        execution_plan=AudioExecutionPlan(preset=AudioQualityPreset.PRODUCTION),
        unresolved_sound_cues=1,
        require_master_quality=commercial.require_master_quality,
    )

    assert commercial.mastering_mode == "two_pass_loudnorm"
    assert commercial.include_cross_chapter_reference is True
    assert commercial.export_stems is True
    assert "unresolved_sound_cues" in report.blocking_reasons
    assert "master_measurement_unavailable" in report.blocking_reasons


def test_formal_mix_requires_every_planned_event_to_reach_master() -> None:
    failed_report = MixRenderReport(
        chapter_number=1,
        status="failed",
        passed=False,
        mastering_succeeded=False,
        planned_event_count=1,
        rendered_event_count=0,
        failed_event_count=1,
        events=[
            MixRenderEventResult(
                event_id="bgm:0",
                asset_id="bgm:0",
                bus="bgm",
                status="failed",
                planned_start_ms=0,
                planned_end_ms=1000,
                reason="source_audio_missing_or_empty",
            )
        ],
    )
    audio_result = ChapterAudioResult(
        chapter_number=1,
        script=DubbingScript(chapter_number=1),
        assembled_audio_path="master.mp3",
        is_complete=True,
        mix_render_report=failed_report,
    )
    mix_plan = MixPlan(
        chapter_number=1,
        assets=[AudioAssetRef(asset_id="bgm:0", kind="bgm", path="missing.wav")],
        events=[
            MixEvent(
                event_id="bgm:0",
                asset_id="bgm:0",
                bus="bgm",
                start_ms=0,
                end_ms=1000,
            )
        ],
    )

    quality_report = evaluate_audio_quality(
        audio_result=audio_result,
        timeline=SpeechTimeline(chapter_number=1),
        execution_plan=AudioExecutionPlan(preset=AudioQualityPreset.MASTER),
        mix_plan=mix_plan,
    )
    blocked = apply_delivery_readiness(audio_result, quality_report)

    assert quality_report.render_integrity_passed is False
    assert quality_report.failed_render_event_ids == ["bgm:0"]
    assert "mix_render_integrity" in quality_report.blocking_reasons
    assert blocked.delivery_ready is False


def _completed_render_report(events: list[MixEvent]) -> MixRenderReport:
    return MixRenderReport(
        chapter_number=1,
        status="completed",
        passed=True,
        mastering_succeeded=True,
        planned_event_count=len(events),
        rendered_event_count=len(events),
        failed_event_count=0,
        duration_ms=max(item.end_ms for item in events),
        output_path="master.mp3",
        output_hash="a" * 64,
        events=[
            MixRenderEventResult(
                event_id=item.event_id,
                asset_id=item.asset_id,
                bus=item.bus,
                status="rendered",
                planned_start_ms=item.start_ms,
                planned_end_ms=item.end_ms,
                actual_start_ms=item.start_ms,
                actual_end_ms=item.end_ms,
                gain_db=item.gain_db,
            )
            for item in events
        ],
    )


def _aligned_timeline() -> SpeechTimeline:
    return SpeechTimeline(
        chapter_number=1,
        alignments=[
            AlignmentResult(
                segment_index=0,
                expected_text="正确对齐",
                recognized_text="正确对齐",
                status="aligned",
                coverage=1.0,
                text_error_rate=0.0,
            )
        ],
    )


def test_master_gate_blocks_loudness_peak_and_cross_chapter_drift(monkeypatch) -> None:
    monkeypatch.setattr(
        "novel_forge.tts.audio_quality._measure_loudness",
        lambda _path: (-13.5, -0.8),
    )
    voice_event = MixEvent(
        event_id="voice:0",
        asset_id="voice:0",
        bus="voice",
        start_ms=0,
        end_ms=1000,
    )
    mix_plan = MixPlan(
        chapter_number=1,
        assets=[AudioAssetRef(asset_id="voice:0", kind="voice", path="voice.wav")],
        events=[voice_event],
        target_lufs=-16.0,
        true_peak_db=-1.5,
    )
    audio_result = ChapterAudioResult(
        chapter_number=1,
        script=DubbingScript(chapter_number=1),
        assembled_audio_path="master.mp3",
        is_complete=True,
        mix_render_report=_completed_render_report([voice_event]),
    )

    report = evaluate_audio_quality(
        audio_result=audio_result,
        timeline=_aligned_timeline(),
        execution_plan=AudioExecutionPlan(preset=AudioQualityPreset.MASTER),
        mix_plan=mix_plan,
        reference_lufs=[-16.0, -15.8, -16.2],
    )

    assert report.measurement_available is True
    assert report.loudness_within_target is False
    assert report.true_peak_within_target is False
    assert report.reference_loudness_lufs == -16.0
    assert report.loudness_delta_lu == 2.5
    assert "master_loudness" in report.blocking_reasons
    assert "master_true_peak" in report.blocking_reasons
    assert "cross_chapter_loudness" in report.blocking_reasons


def test_master_gate_blocks_background_masking_and_abrupt_bed_transition(monkeypatch) -> None:
    monkeypatch.setattr(
        "novel_forge.tts.audio_quality._measure_loudness",
        lambda _path: (-16.0, -1.5),
    )
    events = [
        MixEvent(
            event_id="voice:0",
            asset_id="voice:0",
            bus="voice",
            start_ms=0,
            end_ms=2000,
        ),
        MixEvent(
            event_id="bgm:0",
            asset_id="bgm:0",
            bus="bgm",
            start_ms=0,
            end_ms=2000,
            gain_db=-2.0,
            duck_under_voice_db=0.0,
            fade_in_ms=0,
            fade_out_ms=0,
        ),
    ]
    mix_plan = MixPlan(
        chapter_number=1,
        assets=[
            AudioAssetRef(asset_id="voice:0", kind="voice", path="voice.wav"),
            AudioAssetRef(asset_id="bgm:0", kind="bgm", path="bgm.wav"),
        ],
        events=events,
    )
    audio_result = ChapterAudioResult(
        chapter_number=1,
        script=DubbingScript(chapter_number=1),
        assembled_audio_path="master.mp3",
        is_complete=True,
        mix_render_report=_completed_render_report(events),
    )

    report = evaluate_audio_quality(
        audio_result=audio_result,
        timeline=_aligned_timeline(),
        execution_plan=AudioExecutionPlan(preset=AudioQualityPreset.MASTER),
        mix_plan=mix_plan,
    )

    assert report.masking_risk_event_ids == ["bgm:0"]
    assert report.transition_risk_event_ids == ["bgm:0"]
    assert "speech_masking_risk" in report.blocking_reasons
    assert "mix_transition_risk" in report.blocking_reasons
