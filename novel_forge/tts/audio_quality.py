"""Post-render audio quality measurement and auditable gate reporting."""

from __future__ import annotations

import math
import re
import statistics
import subprocess
from pathlib import Path

from novel_forge.tts.platform.schemas import (
    AudioExecutionPlan,
    AudioExecutionStage,
    AudioQualityPreset,
    AudioQualityReport,
    MixPlan,
    SpeechTimeline,
)
from novel_forge.tts.runtime.audio_runtime import ffmpeg_executable
from novel_forge.tts.schemas import ChapterAudioResult


def _measure_loudness(path: Path) -> tuple[float | None, float | None]:
    if not path.is_file() or path.stat().st_size <= 0:
        return None, None
    command = [
        ffmpeg_executable(),
        "-hide_banner",
        "-nostats",
        "-i",
        str(path),
        "-filter_complex",
        "ebur128=peak=true",
        "-f",
        "null",
        "-",
    ]
    try:
        completed = subprocess.run(command, capture_output=True, check=False, timeout=180)
    except (OSError, subprocess.SubprocessError):
        return None, None
    output = completed.stderr.decode("utf-8", errors="replace")
    integrated = re.findall(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", output)
    peaks = re.findall(r"Peak:\s*(-?\d+(?:\.\d+)?)\s*dBFS", output)
    return (
        float(integrated[-1]) if integrated else None,
        float(peaks[-1]) if peaks else None,
    )


def _overlaps(left_start: int, left_end: int, right_start: int, right_end: int) -> bool:
    return left_start < right_end and left_end > right_start


def _masking_risk_event_ids(mix_plan: MixPlan | None) -> list[str]:
    """Return non-voice events whose combined dialogue-time gain risks masking speech."""

    if mix_plan is None:
        return []
    voices = [event for event in mix_plan.events if event.bus == "voice"]
    backgrounds = [event for event in mix_plan.events if event.bus != "voice"]
    risky: set[str] = set()
    for voice in voices:
        overlapping = [
            event
            for event in backgrounds
            if _overlaps(voice.start_ms, voice.end_ms, event.start_ms, event.end_ms)
        ]
        if not overlapping:
            continue
        effective_amplitude = sum(
            math.pow(10.0, (event.gain_db - event.duck_under_voice_db) / 20.0)
            for event in overlapping
        )
        combined_db = 20.0 * math.log10(max(effective_amplitude, 1e-9))
        if combined_db > -6.0:
            risky.update(event.event_id for event in overlapping)
    return sorted(risky)


def _transition_risk_event_ids(mix_plan: MixPlan | None) -> list[str]:
    if mix_plan is None:
        return []
    risky: set[str] = set()
    for event in mix_plan.events:
        if event.bus in {"bgm", "soundscape"} and event.end_ms - event.start_ms >= 1000:
            minimum_fade_ms = 500 if event.bus == "soundscape" else 800
            if event.fade_in_ms < minimum_fade_ms or event.fade_out_ms < minimum_fade_ms:
                risky.add(event.event_id)
        if event.bus == "sfx" and event.placement_reason == "dialogue_overlap_unavoidable":
            # Only flag SFX that are loud enough to actually mask speech.
            # Low-gain SFX (effective gain below -12 dB after ducking) are
            # barely audible and do not constitute a meaningful transition risk.
            effective_gain_db = event.gain_db - event.duck_under_voice_db
            if effective_gain_db > -12.0:
                risky.add(event.event_id)
    return sorted(risky)


def evaluate_audio_quality(
    *,
    audio_result: ChapterAudioResult,
    timeline: SpeechTimeline,
    execution_plan: AudioExecutionPlan,
    maximum_text_error_rate: float = 0.12,
    repair_rounds: int = 0,
    mix_plan: MixPlan | None = None,
    unresolved_sound_cues: int = 0,
    reference_lufs: list[float] | None = None,
    measured_loudness: dict[str, str] | None = None,
    require_master_quality: bool = False,
    commercial_rights_issues: list[str] | None = None,
) -> AudioQualityReport:
    """Measure objective delivery properties without modifying the master.

    When ``measured_loudness`` carries the two-pass loudnorm analysis values
    (input_i / input_tp), they are reused instead of decoding the master a
    second time with ebur128 (P2-1).
    """

    coverages = [
        item.coverage if item.status == "aligned" else 0.0
        for item in timeline.alignments
        if item.expected_text
    ]
    alignment_coverage = sum(coverages) / len(coverages) if coverages else 0.0
    # Distinguish "aligner evidence unavailable" from "aligned but low
    # coverage".  Preview/production may keep working with a visible warning,
    # but a commercial/Master delivery must never claim that spoken content was
    # verified when every aligner fell back (or no eligible evidence exists).
    aligned_items = [
        item for item in timeline.alignments if item.expected_text and item.status == "aligned"
    ]
    all_alignment_fallback = bool(coverages) and not aligned_items
    alignment_evidence_unavailable = not coverages or all_alignment_fallback
    text_error_rates = [
        item.text_error_rate for item in timeline.alignments if item.text_error_rate is not None
    ]
    mean_text_error_rate = (
        sum(text_error_rates) / len(text_error_rates) if text_error_rates else None
    )
    failed_segment_indices = sorted(
        result.segment_index
        for result in audio_result.segment_results
        if result.status.value == "failed" or not result.quality_passed
    )
    integrated_lufs: float | None = None
    true_peak_db: float | None = None
    measured = measured_loudness or {}
    if measured.get("input_i") and measured.get("input_tp"):
        try:
            raw_i = str(measured["input_i"]).strip()
            raw_tp = str(measured["input_tp"]).strip()
            if raw_i not in {"-inf", "inf", "-nan", "nan"}:
                integrated_lufs = float(raw_i)
            if raw_tp not in {"-inf", "inf", "-nan", "nan"}:
                true_peak_db = float(raw_tp)
        except ValueError:
            integrated_lufs = None
            true_peak_db = None
    if integrated_lufs is None or true_peak_db is None:
        integrated_lufs, true_peak_db = _measure_loudness(Path(audio_result.assembled_audio_path))
    measurement_available = integrated_lufs is not None and true_peak_db is not None
    target_lufs = mix_plan.target_lufs if mix_plan is not None else -16.0
    target_true_peak = mix_plan.true_peak_db if mix_plan is not None else -1.5
    # The execution-plan preset describes plugin routing, while a commercial
    # delivery tier may require MASTER-level acceptance without changing that
    # routing.  Use one local predicate for every strict threshold so those
    # two independent controls cannot drift apart.
    master_gate = execution_plan.preset == AudioQualityPreset.MASTER or require_master_quality
    rights_issues = list(dict.fromkeys(commercial_rights_issues or []))
    loudness_tolerance = {
        AudioQualityPreset.MASTER: 1.0,
        AudioQualityPreset.PRODUCTION: 2.0,
    }.get(AudioQualityPreset.MASTER if master_gate else execution_plan.preset, 3.0)
    loudness_within_target = integrated_lufs is None or (
        abs(integrated_lufs - target_lufs) <= loudness_tolerance
    )
    true_peak_within_target = true_peak_db is None or true_peak_db <= target_true_peak + 0.5
    valid_references = [float(value) for value in (reference_lufs or [])]
    reference_loudness = statistics.median(valid_references) if valid_references else None
    loudness_delta = (
        abs(integrated_lufs - reference_loudness)
        if integrated_lufs is not None and reference_loudness is not None
        else None
    )
    cross_chapter_loudness_ok = loudness_delta is None or loudness_delta <= (1.5 if master_gate else 2.5)
    masking_risk_event_ids = _masking_risk_event_ids(mix_plan)
    transition_risk_event_ids = _transition_risk_event_ids(mix_plan)
    clipping = true_peak_db is not None and true_peak_db >= -0.1
    warnings: list[str] = []
    actions: list[str] = []
    if all_alignment_fallback:
        if master_gate:
            warnings.append(
                "强制对齐环境不可用（所有 ASR/对齐后端离线或异常），"
                "商业/Master 成片不能在缺少可信文本对齐证据时放行。"
            )
        else:
            warnings.append(
                "强制对齐环境不可用（所有 ASR/对齐后端离线或异常），"
                "全部段落降级为片段级时长；已仅对非商业交付放行覆盖率门。"
            )
        actions.append("在模型中心启动 Qwen3-ASR/WhisperX sidecar 后重新合成以获得字词级时间线。")
    elif not coverages:
        warnings.append("强制对齐服务未就绪，当前仅有片段级真实时长。")
    elif alignment_coverage < 0.9:
        warnings.append(f"字词对齐覆盖率偏低：{alignment_coverage:.1%}。")
        actions.append("对低覆盖片段切换回退对齐器或重新生成人声。")
    if clipping:
        warnings.append("检测到接近 0 dBFS 的峰值，可能发生削波。")
        actions.append("以 MixPlan 重新渲染并降低总线增益。")
    if not measurement_available and master_gate:
        warnings.append("无法测量成片响度与峰值，MASTER 母带不能放行。")
        actions.append("修复 FFmpeg 测量环境后重新执行母带质检。")
    if integrated_lufs is not None and not loudness_within_target:
        warnings.append(
            f"成片综合响度为 {integrated_lufs:.1f} LUFS，偏离目标 {target_lufs:.1f} LUFS。"
        )
        actions.append("重新执行最终响度归一化。")
    if true_peak_db is not None and not true_peak_within_target:
        warnings.append(
            f"成片峰值为 {true_peak_db:.1f} dBFS，高于母带目标"
            f" {target_true_peak:.1f} dB的允许偏差。"
        )
        actions.append("降低母线增益并重新执行两遍响度处理。")
    if loudness_delta is not None and not cross_chapter_loudness_ok:
        warnings.append(f"当前章与近期章节响度中位数相差 {loudness_delta:.1f} LU。")
        actions.append("按全书参考响度重新渲染当前章。")
    if masking_risk_event_ids:
        warnings.append(f"对白窗口内多层背景声合成增益过高：{', '.join(masking_risk_event_ids)}。")
        actions.append("减少同时声音层，或增加人声侧链避让量。")
    if transition_risk_event_ids:
        warnings.append(
            f"存在突兀底床转场或无法避开对白的音效：{', '.join(transition_risk_event_ids)}。"
        )
        actions.append("延长淡入淡出，或重新定位与对白冲突的音效。")
    if mean_text_error_rate is not None and mean_text_error_rate > maximum_text_error_rate:
        warnings.append(f"人声转写平均错误率 {mean_text_error_rate:.1%}，高于阈值。")
        actions.append("仅重新生成转写失配片段，然后再次对齐。")
    if failed_segment_indices:
        warnings.append("存在未通过片段质量门的人声。")
    mix_plan_warnings = list(mix_plan.placement_warnings) if mix_plan is not None else []
    warnings.extend(mix_plan_warnings)
    if unresolved_sound_cues:
        warnings.append(f"仍有 {unresolved_sound_cues} 个剧情声音 cue 未解析为已批准资产。")
        actions.append("生成、导入或批准缺失的 BGM/环境声/音效后重新混音。")
    if rights_issues:
        preview = "、".join(item.split(":", 1)[0] for item in rights_issues[:5])
        suffix = "…" if len(rights_issues) > 5 else ""
        warnings.append(
            f"正式混音使用了 {len(rights_issues)} 个未确认商用权利的声音资产："
            f"{preview}{suffix}。"
        )
        actions.append("在声音库核对 API 账号条款、模型权重许可证或素材授权，并记录商用结论。")
    render_report = audio_result.mix_render_report
    render_required = mix_plan is not None
    failed_render_event_ids = (
        [item.event_id for item in render_report.events if item.status == "failed"]
        if render_report is not None
        else []
    )
    render_integrity_passed = not render_required or bool(
        render_report is not None and render_report.passed
    )
    if not render_integrity_passed:
        if render_report is None:
            warnings.append("正式 MixPlan 缺少实际渲染报告。")
        else:
            warnings.append(f"正式混音有 {render_report.failed_event_count} 个计划事件未进入母带。")
        actions.append("修复缺失/无法解码的声音资产或渲染器后，重新执行完整 MixPlan。")
    minimum_alignment = 0.8 if master_gate else 0.0
    blocking_reasons: list[str] = []
    if clipping:
        blocking_reasons.append("clipping")
    if master_gate and alignment_evidence_unavailable:
        blocking_reasons.append("alignment_backend_unavailable")
    elif alignment_coverage < minimum_alignment:
        blocking_reasons.append("alignment_coverage")
    if mean_text_error_rate is not None and mean_text_error_rate > maximum_text_error_rate:
        blocking_reasons.append("text_error_rate")
    if failed_segment_indices:
        blocking_reasons.append("segment_quality")
    if unresolved_sound_cues and master_gate:
        blocking_reasons.append("unresolved_sound_cues")
    if not render_integrity_passed:
        blocking_reasons.append("mix_render_integrity")
    if master_gate and not measurement_available:
        blocking_reasons.append("master_measurement_unavailable")
    if master_gate and not loudness_within_target:
        blocking_reasons.append("master_loudness")
    if master_gate and not true_peak_within_target:
        blocking_reasons.append("master_true_peak")
    if master_gate and not cross_chapter_loudness_ok:
        blocking_reasons.append("cross_chapter_loudness")
    if master_gate and masking_risk_event_ids:
        blocking_reasons.append("speech_masking_risk")
    if master_gate and transition_risk_event_ids:
        blocking_reasons.append("mix_transition_risk")
    if master_gate and rights_issues:
        blocking_reasons.append("sound_asset_commercial_rights")
    evaluator = execution_plan.assignment_for(AudioExecutionStage.QUALITY)
    return AudioQualityReport(
        chapter_number=audio_result.chapter_number,
        passed=(
            audio_result.is_complete
            and bool(audio_result.assembled_audio_path)
            and not clipping
            and (
                not master_gate
                or (
                    not alignment_evidence_unavailable
                    and alignment_coverage >= minimum_alignment
                )
            )
            and (mean_text_error_rate is None or mean_text_error_rate <= maximum_text_error_rate)
            and not failed_segment_indices
            and render_integrity_passed
            and (not master_gate or measurement_available)
            and (not master_gate or loudness_within_target)
            and (not master_gate or true_peak_within_target)
            and (not master_gate or cross_chapter_loudness_ok)
            and (not master_gate or not masking_risk_event_ids)
            and (not master_gate or not transition_risk_event_ids)
            and (not master_gate or not rights_issues)
            and not (unresolved_sound_cues and master_gate)
        ),
        alignment_coverage=alignment_coverage,
        clipping_detected=clipping,
        integrated_lufs=integrated_lufs,
        true_peak_db=true_peak_db,
        speech_masking_warnings=warnings,
        repair_actions=actions,
        evaluator_plugin_id=(
            evaluator.primary.plugin_id
            if evaluator is not None and evaluator.primary is not None
            else ""
        ),
        mean_text_error_rate=mean_text_error_rate,
        failed_segment_indices=failed_segment_indices,
        blocking_reasons=blocking_reasons,
        repair_rounds=repair_rounds,
        unresolved_sound_cues=unresolved_sound_cues,
        mix_plan_warnings=mix_plan_warnings,
        render_integrity_passed=render_integrity_passed,
        failed_render_event_ids=failed_render_event_ids,
        measurement_available=measurement_available,
        loudness_target_lufs=target_lufs,
        loudness_tolerance_lu=loudness_tolerance,
        loudness_within_target=loudness_within_target,
        true_peak_target_db=target_true_peak,
        true_peak_within_target=true_peak_within_target,
        reference_loudness_lufs=reference_loudness,
        loudness_delta_lu=loudness_delta,
        masking_risk_event_ids=masking_risk_event_ids,
        transition_risk_event_ids=transition_risk_event_ids,
        commercial_rights_issues=rights_issues,
    )


def apply_delivery_readiness(
    audio_result: ChapterAudioResult,
    quality_report: AudioQualityReport,
) -> ChapterAudioResult:
    """Return an audio result with one canonical final-delivery decision.

    Speech completion remains distinct from final delivery: a chapter can have
    a valid assembled speech track while still being blocked by objective audio
    quality or unresolved story-sound assets.

    The preset-aware blocking policy (e.g. unresolved_sound_cues only blocks
    MASTER) is already encoded in ``quality_report.blocking_reasons`` and
    ``quality_report.passed`` by :func:`evaluate_audio_quality`.  This function
    trusts that upstream decision without re-imposing unconditional gates.
    """

    blocking_reasons = list(quality_report.blocking_reasons)
    if not audio_result.is_complete:
        blocking_reasons.append("incomplete_synthesis")
    if not audio_result.assembled_audio_path:
        blocking_reasons.append("missing_assembled_audio")
    return audio_result.model_copy(
        update={
            "delivery_ready": bool(
                quality_report.passed
                and audio_result.is_complete
                and audio_result.assembled_audio_path
            ),
            "delivery_blocking_reasons": list(dict.fromkeys(blocking_reasons)),
        }
    )
