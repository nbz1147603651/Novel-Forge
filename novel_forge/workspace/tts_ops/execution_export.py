"""
Final audio export and full-pipeline orchestration.

Extracted from execution.py: chapter audio reassembly and the full TTS
pipeline driver.  Depends on all sibling execution domains (acyclic).
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from novel_forge.core.config import Settings
from novel_forge.film.schemas import DeliveryManifest, DeliveryMediaType
from novel_forge.obs.logger import get_logger
from novel_forge.persistence.filesystem import (
    atomic_write_json,
    atomic_write_text,
)
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.assets.sound_library import commercial_rights_issues, resolved_asset_paths
from novel_forge.tts.audio_quality import apply_delivery_readiness, evaluate_audio_quality
from novel_forge.tts.delivery_profile import resolve_audio_delivery_profile
from novel_forge.tts.pipeline.align_speech_timeline_step import (
    AlignSpeechTimelineInput,
    AlignSpeechTimelineStep,
    apply_speech_timeline_to_script,
)
from novel_forge.tts.pipeline.assemble_audio_step import AssembleAudioInput, AssembleAudioStep
from novel_forge.tts.pipeline.mix_plan_builder import build_mix_plan
from novel_forge.tts.platform.config import registry_from_settings
from novel_forge.tts.platform.schemas import (
    SpeechTimeline,
)
from novel_forge.tts.runtime.audio_runtime import convert_audio_for_export
from novel_forge.tts.schemas import (
    ChapterAudioResult,
    ChapterSoundResolutionReport,
    DubbingScript,
    NarratorVoiceProfile,
    SegmentType,
    SynthesisStatus,
    TakeReviewStatus,
    TTSProvider,
    VoiceTeamContract,
)
from novel_forge.tts.script_integrity import (
    compute_dubbing_script_hash,
    unresolved_speaker_indices,
)
from novel_forge.tts.services.automation import (
    AudioAutomationMode,
    settings_for_audio_automation_mode,
)
from novel_forge.tts.services.delivery import TTSDeliveryNotReadyError, require_delivery_ready
from novel_forge.tts.services.studio_service import VoiceStudioProjectService
from novel_forge.workspace.execution_result import ExecutionResult
from novel_forge.workspace.tts_ops.execution_assets import (
    _load_audio_execution_plan,
    _recent_audio_reference_lufs,
)
from novel_forge.workspace.tts_ops.execution_script import (
    _authoritative_text_mismatch,
    _script_source_audit_error,
    _script_source_mismatch,
    execute_generate_dubbing_script,
    tts_artifact_source_mismatch,
    tts_audio_result_script_mismatch,
    tts_audio_result_source_hash,
)
from novel_forge.workspace.tts_ops.execution_shared import (
    REASON_PENDING_APPROVAL,
    REASON_UNKNOWN,
    _emit_tts_progress,
    _load_json,
    _load_tts_upstream_context,
    _merge_character_inputs,
    _resolve_provider,
)
from novel_forge.workspace.tts_ops.execution_synthesis import (
    execute_synthesize_chapter,
)
from novel_forge.workspace.tts_ops.execution_voice_team import (
    _diagnose_narrator_reuse,
    _diagnose_voice_team_reuse,
    execute_build_narrator_profile,
    execute_build_voice_team,
)
from novel_forge.workspace.tts_ops.lock_manager import (
    with_tts_project_lock as _with_tts_project_lock,
)
from novel_forge.workspace.tts_ops.preflight import (  # noqa: F401
    _enrich_preflight_failure,
    _preflight_failure_guidance,
    _preflight_failure_severity,
)
from novel_forge.workspace.tts_ops.voice_assignment import (  # noqa: F401
    _assign_narrator_voice,
    _narrator_profile_content_hash,
    _narrator_voice_match_input,
)

_log = get_logger("workspace.tts")


@_with_tts_project_lock
async def execute_reassemble_chapter_audio(
    *,
    project_id: str,
    chapter_number: int,
    settings: Settings,
    layout: ProjectLayout,
    fast: bool = False,
) -> ExecutionResult[dict[str, Any]]:
    """Assemble the current approved segment takes without re-calling TTS.

    When ``fast`` is True the caller only needs a freshly rendered master to
    audition an accepted take in context (e.g. the Voice Room accept-and-continue
    loop).  In that mode the expensive ASR forced-alignment
    (``AlignSpeechTimelineStep``) and the loudness/peak quality measurement
    (``evaluate_audio_quality``) are reused from the previously persisted
    artifacts instead of being recomputed, since neither changes when only one
    segment's audio file was swapped.  The full-quality path (``fast=False``,
    the default for the manual "重装配" button) still recomputes everything.
    """
    result_path = layout.tts_audio_result_path(chapter_number)
    script_path = layout.tts_dubbing_script_path(chapter_number)
    if not result_path.exists() or not script_path.exists():
        return ExecutionResult(
            project_id=project_id,
            result={"error": "请先至少生成一段语音并保存配音脚本。"},
        )
    script = DubbingScript.model_validate(_load_json(script_path))
    source_error = _script_source_mismatch(layout, chapter_number, script)
    if source_error is not None:
        return ExecutionResult(project_id=project_id, result=source_error)
    audit_error = _script_source_audit_error(layout, chapter_number, script)
    if audit_error is not None:
        return ExecutionResult(project_id=project_id, result=audit_error)
    unresolved_speakers = unresolved_speaker_indices(script)
    if unresolved_speakers:
        return ExecutionResult(
            project_id=project_id,
            result={
                "error": "配音脚本仍有待复核说话人，不能装配为正式成品。",
                "error_code": "tts_speaker_review_required",
                "segment_indices": list(unresolved_speakers),
            },
        )
    current_hash = script.script_hash or compute_dubbing_script_hash(script)
    pending_takes = [
        take
        for take in VoiceStudioProjectService(
            layout,
            project_id=project_id,
        )
        .load_take_manifest(chapter_number)
        .takes
        if take.status == TakeReviewStatus.CANDIDATE and take.source_script_hash == current_hash
    ]
    if pending_takes:
        return ExecutionResult(
            project_id=project_id,
            result={
                "error": (
                    f"还有 {len(pending_takes)} 个待审试听版本；请先接受或舍弃，不会默认参与装配。"
                )
            },
        )
    previous_payload = _load_json(result_path)
    previous = ChapterAudioResult.model_validate(previous_payload)
    result_script_error = tts_audio_result_script_mismatch(
        layout,
        chapter_number,
        previous_payload,
    )
    if result_script_error is not None:
        return ExecutionResult(project_id=project_id, result=result_script_error)
    results_by_index = {item.segment_index: item for item in previous.segment_results}
    required = {
        item.segment_index
        for item in script.segments
        if item.segment_type
        in (SegmentType.NARRATION, SegmentType.DIALOGUE, SegmentType.INNER_THOUGHT)
    }
    missing = sorted(
        index
        for index in required
        if (
            index not in results_by_index
            or results_by_index[index].status != SynthesisStatus.COMPLETED
            or not results_by_index[index].audio_path
            or not Path(results_by_index[index].audio_path).is_file()
        )
    )
    if missing:
        display = "、".join(str(index + 1) for index in missing[:12])
        suffix = "…" if len(missing) > 12 else ""
        return ExecutionResult(
            project_id=project_id,
            result={"error": f"尚有 {len(missing)} 段未准备好：第 {display}{suffix} 段。"},
        )

    segment_results = sorted(results_by_index.values(), key=lambda item: item.segment_index)
    previous_provider = str(
        previous.metadata.get("provider") or settings.tts_default_provider
    ).strip()
    try:
        provider_enum = TTSProvider(previous_provider)
    except ValueError:
        provider_enum = _resolve_provider(settings)
    effective_audio_settings = settings.model_copy(
        update={"tts_default_provider": provider_enum.value}
    )
    try:
        execution_plan = _load_audio_execution_plan(layout)
    except (FileNotFoundError, ValueError) as exc:
        return ExecutionResult(
            project_id=project_id,
            result={
                "error": f"{exc}。请先重新生成配音脚本。",
                "error_code": "audio_execution_plan_required",
            },
        )
    execution_plan_path = layout.tts_execution_plan_path
    timeline_path = layout.tts_speech_timeline_path(chapter_number)
    if fast:
        # Reuse the persisted speech timeline: a single accepted take swaps one
        # segment's audio file but does not change word boundaries, so
        # re-running forced alignment (an offline ASR sidecar) would be wasted
        # work.  The fast path is only valid when a timeline already exists
        # (i.e. the full pipeline has run before); otherwise refuse so the
        # caller falls back to the manual full reassemble.
        #
        # P2-3: with the timeline reused, the remaining heavy work is the
        # render + quality gate.  The renderer's two-pass loudnorm analysis is
        # carried into ``evaluate_audio_quality`` via ``measured_loudness``
        # below, so the fast path never decodes the chapter a second time for
        # ebur128 either.
        if not timeline_path.exists():
            return ExecutionResult(
                project_id=project_id,
                result={
                    "error": "快速重装配需要既有时间线，但尚未生成。请改用完整重装配。",
                    "error_code": "fast_reassemble_timeline_unavailable",
                },
            )
        try:
            timeline = SpeechTimeline.model_validate(_load_json(timeline_path))
        except (OSError, ValueError) as exc:
            return ExecutionResult(
                project_id=project_id,
                result={
                    "error": f"快速重装配无法读取既有时间线：{exc}。请改用完整重装配。",
                    "error_code": "fast_reassemble_timeline_unavailable",
                },
            )
    else:
        timeline = await AlignSpeechTimelineStep(
            settings=effective_audio_settings,
            registry=registry_from_settings(effective_audio_settings),
        ).run(
            AlignSpeechTimelineInput(
                chapter_number=chapter_number,
                script=script,
                segment_results=segment_results,
                execution_plan=execution_plan,
            )
        )
        timeline_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(timeline_path, timeline.model_dump(mode="json"))
    mix_script = apply_speech_timeline_to_script(script, timeline)

    resolved_sound_paths: dict[tuple[str, int], Path] = {}
    unresolved_sound_cues = 0
    sound_commercial_rights_issues: list[str] = []
    sound_report_path = layout.tts_sound_resolution_path(chapter_number)
    if sound_report_path.exists():
        try:
            sound_report = ChapterSoundResolutionReport.model_validate(
                _load_json(sound_report_path)
            )
            resolved_sound_paths = resolved_asset_paths(layout, sound_report)
            unresolved_sound_cues = sound_report.unresolved_count
            sound_commercial_rights_issues = commercial_rights_issues(layout, sound_report)
        except Exception as exc:
            _log.warning("Ignoring unreadable sound-resolution report while assembling: %s", exc)
    delivery_profile = resolve_audio_delivery_profile(settings.tts_audio_quality_tier)
    mix_plan = build_mix_plan(
        chapter_number=chapter_number,
        script=mix_script,
        timeline=timeline,
        segment_results=segment_results,
        resolved_sound_paths=resolved_sound_paths,
        execution_plan=execution_plan,
        mastering_mode=delivery_profile.mastering_mode,
        export_stems=delivery_profile.export_stems,
    )
    mix_plan_path = layout.tts_mix_plan_path(chapter_number)
    mix_plan_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(mix_plan_path, mix_plan.model_dump(mode="json"))
    assembled = await AssembleAudioStep(settings=settings, layout=layout).run(
        AssembleAudioInput(
            chapter_number=chapter_number,
            script=mix_script,
            segment_results=segment_results,
            resolved_sound_paths=resolved_sound_paths,
            enable_bgm_mixing=True,
            enable_sfx_mixing=True,
            mix_plan=mix_plan,
            speech_timeline=timeline,
        )
    )
    render_report_path = layout.tts_mix_render_report_path(chapter_number)
    if assembled.mix_render_report is not None:
        render_report_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            render_report_path,
            assembled.mix_render_report.model_dump(mode="json"),
        )
    quality_report = await asyncio.to_thread(
        evaluate_audio_quality,
        audio_result=assembled,
        timeline=timeline,
        execution_plan=execution_plan,
        mix_plan=mix_plan,
        unresolved_sound_cues=unresolved_sound_cues,
        reference_lufs=(
            _recent_audio_reference_lufs(layout, chapter_number)
            if delivery_profile.include_cross_chapter_reference
            else []
        ),
        # Reuse the loudnorm analysis pass instead of decoding the master a
        # second time when the two-pass renderer produced measurements.
        measured_loudness=(
            assembled.mix_render_report.measured_loudness
            if assembled.mix_render_report is not None
            else None
        ),
        require_master_quality=delivery_profile.require_master_quality,
        commercial_rights_issues=sound_commercial_rights_issues,
    )
    assembled = apply_delivery_readiness(
        assembled,
        quality_report,
    )
    quality_report_path = layout.tts_audio_quality_report_path(chapter_number)
    quality_report_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(quality_report_path, quality_report.model_dump(mode="json"))
    assembled.metadata.update(previous.metadata)
    assembled.metadata.update(
        {
            "assembly_stale": False,
            "assembly_stale_reason": "",
            "audio_execution_plan_path": str(execution_plan_path),
            "speech_timeline_path": str(timeline_path),
            "mix_plan_path": str(mix_plan_path),
            "mix_render_report_path": (
                str(render_report_path) if assembled.mix_render_report is not None else ""
            ),
            "audio_quality_report_path": str(quality_report_path),
            "audio_quality": {
                "passed": quality_report.passed,
                "alignment_coverage": quality_report.alignment_coverage,
                "clipping_detected": quality_report.clipping_detected,
                "render_integrity_passed": quality_report.render_integrity_passed,
                "integrated_lufs": quality_report.integrated_lufs,
                "true_peak_db": quality_report.true_peak_db,
                "loudness_delta_lu": quality_report.loudness_delta_lu,
                "masking_risk_events": quality_report.masking_risk_event_ids,
                "transition_risk_events": quality_report.transition_risk_event_ids,
                "commercial_rights_issues": quality_report.commercial_rights_issues,
            },
            "audio_delivery": {
                "tier": delivery_profile.tier,
                "mastering_mode": delivery_profile.mastering_mode,
                "master_quality_required": delivery_profile.require_master_quality,
            },
        }
    )
    atomic_write_json(result_path, assembled.model_dump(mode="json"))
    return ExecutionResult(project_id=project_id, result=assembled.model_dump(mode="json"))


@_with_tts_project_lock
async def execute_full_tts_pipeline(
    *,
    project_id: str,
    chapter_number: int,
    chapter_text: str,
    characters: list[dict[str, Any]],
    settings: Settings,
    layout: ProjectLayout,
    provider: str = "",
    automation_mode: AudioAutomationMode | str | None = None,
    genre: str = "",
    tone: str = "",
    scene_context: dict[str, Any] | None = None,
    allow_voice_team_rebuild: bool = True,
    on_step_progress: Any = None,
    on_segment_progress: Any = None,
) -> ExecutionResult[dict[str, Any]]:
    """Run the full TTS pipeline for a chapter.

    Combines: build_narrator_profile → build_voice_team → generate_script → synthesize → assemble.

    Args:
        project_id: Project identifier.
        chapter_number: Chapter number.
        chapter_text: Chapter text content.
        characters: List of character dicts.
        settings: Application settings.
        layout: Project layout for storage paths.
        provider: TTS provider override.
        genre: Genre string for narrator profile (optional).
        tone: Tone string for narrator profile (optional).
        scene_context: Scene context dict for script generation (optional).
        allow_voice_team_rebuild: Whether this invocation may create or replace
            narrator/cast voice assignments.  Background post-archive runs set
            this to ``False`` so they only reuse an author-confirmed team.
        on_step_progress: Optional step-level progress callback.
        on_segment_progress: Optional per-segment progress callback.

    Returns:
        ExecutionResult with ChapterAudioResult dict.
    """
    resolved_automation_mode, settings = settings_for_audio_automation_mode(
        settings,
        automation_mode,
    )
    _log.info(
        "Running full TTS pipeline for chapter %d in %s mode",
        chapter_number,
        resolved_automation_mode.value,
    )
    _emit_tts_progress(
        on_step_progress,
        "tts_automation_mode",
        {"chapter": chapter_number, "mode": resolved_automation_mode.value},
    )
    text_error = _authoritative_text_mismatch(layout, chapter_number, chapter_text)
    if text_error is not None:
        return ExecutionResult(project_id=project_id, result=text_error)
    upstream = _load_tts_upstream_context(layout, chapter_number)
    spec = upstream["spec"]
    resolved_genre = genre or str(spec.get("genre", "") or "")
    resolved_tone = tone or str(spec.get("tone", "") or "")
    enriched_characters = _merge_character_inputs(
        characters,
        upstream["characters"],
        upstream["character_voices"],
    )

    # Manual mode never creates missing voice or script artifacts. It may still
    # execute a user-requested final synthesis when every prerequisite already
    # exists, which keeps desktop, API and durable-job semantics identical.
    if resolved_automation_mode == AudioAutomationMode.MANUAL:
        missing: list[str] = []
        resolved_provider = _resolve_provider(settings, provider)
        existing_team: VoiceTeamContract | None = None
        if layout.tts_voice_team_path.exists():
            try:
                existing_team = VoiceTeamContract.model_validate(
                    _load_json(layout.tts_voice_team_path)
                )
            except Exception:
                missing.append("可读取的配音团队")
        else:
            missing.append("配音团队")
        if existing_team is not None:
            if existing_team.default_provider != resolved_provider:
                missing.append("与当前平台一致的配音团队")
            if not (existing_team.narrator_voice_id or settings.tts_narrator_voice_id):
                missing.append("旁白音色")
            expected_character_ids = {
                str(
                    character.get(
                        "character_id",
                        character.get("id", character.get("name", "")),
                    )
                    or ""
                ).strip()
                for character in enriched_characters
                if isinstance(character, dict)
            }
            expected_character_ids.discard("")
            if any(
                (entry := existing_team.get_entry(character_id)) is None
                or entry.provider != resolved_provider
                or not entry.is_ready
                or entry.is_expired
                for character_id in expected_character_ids
            ):
                missing.append("全部出场角色的可用音色")
        script_path = layout.tts_dubbing_script_path(chapter_number)
        if not script_path.exists():
            missing.append("当前章节配音脚本")
        else:
            try:
                existing_script = DubbingScript.model_validate(_load_json(script_path))
                if (
                    not existing_script.segments
                    or _script_source_mismatch(layout, chapter_number, existing_script) is not None
                    or _script_source_audit_error(layout, chapter_number, existing_script)
                    is not None
                ):
                    missing.append("与定稿正文一致的已审核配音脚本")
            except Exception:
                missing.append("可读取的当前章节配音脚本")
        if missing:
            unique_missing = list(dict.fromkeys(missing))
            return ExecutionResult(
                project_id=project_id,
                result={
                    "error": "全人工模式不会自动创建前置产物，请先人工完成："
                    + "、".join(unique_missing),
                    "error_code": "tts_manual_prerequisites_required",
                    "automation_mode": resolved_automation_mode.value,
                    "missing_prerequisites": unique_missing,
                },
            )
        _emit_tts_progress(
            on_step_progress,
            "tts_manual_prerequisites_ready",
            {"chapter": chapter_number, "mode": resolved_automation_mode.value},
        )
        return await execute_synthesize_chapter(
            project_id=project_id,
            chapter_number=chapter_number,
            settings=settings,
            layout=layout,
            provider=provider,
            automation_mode=resolved_automation_mode,
            on_step_progress=on_step_progress,
            on_segment_progress=on_segment_progress,
        )

    # Step 0: Build an actual narrator voice when the profile is absent,
    # prose-only (legacy), expired, or belongs to another provider.
    resolved_provider = _resolve_provider(settings, provider)
    needs_narrator_build = True
    existing_narrator_profile: NarratorVoiceProfile | None = None
    if layout.tts_narrator_profile_path.exists():
        try:
            existing_narrator_profile = NarratorVoiceProfile.model_validate(
                _load_json(layout.tts_narrator_profile_path)
            )
            needs_narrator_build = (
                not existing_narrator_profile.voice_id
                or existing_narrator_profile.is_expired
                or existing_narrator_profile.provider != resolved_provider
            )
        except Exception:
            existing_narrator_profile = None
            needs_narrator_build = True
    if needs_narrator_build:
        if not allow_voice_team_rebuild:
            narrator_diagnoses = _diagnose_narrator_reuse(
                existing_narrator_profile, resolved_provider
            )
            return ExecutionResult(
                project_id=project_id,
                result={
                    "error": (
                        "自动配音只复用已确认的旁白与配音团队；当前旁白音色不可用，"
                        "请先在声腔工作室手动构建或确认旁白音色。"
                    ),
                    "error_code": "tts_voice_team_manual_rebuild_required",
                    "automation_mode": resolved_automation_mode.value,
                    "missing_artifact": "narrator_voice_profile",
                    "chapter_number": chapter_number,
                    "diagnoses": narrator_diagnoses,
                    "confirmable_character_ids": [],
                },
            )
        profile_result = await execute_build_narrator_profile(
            project_id=project_id,
            settings=settings,
            layout=layout,
            outline=upstream["outline"],
            genre=resolved_genre,
            tone=resolved_tone,
            style_profile=upstream["style_profile"],
            story_bible=upstream["story_bible"],
            audio_aesthetic_hint=str(spec.get("audio_aesthetic_hint", "") or ""),
            sample_chapter=chapter_text[:2000],
            provider=provider,
            on_step_progress=on_step_progress,
        )
        if "error" in profile_result.result:
            _log.warning("Narrator profile build failed, continuing without it")

    # Step 1: Reuse an intact cast where possible.  Full-pipeline retry is
    # frequently selected after a transient synthesis failure; rebuilding the
    # cast in that case needlessly changes its fingerprint and discards valid
    # segment audio.  A provider switch, expired/missing voice, or new
    # character remains a required dependency and deliberately rebuilds it.
    reusable_team = False
    team_diagnoses: list[dict[str, str]] = []
    expected_character_ids = {
        str(
            character.get(
                "character_id",
                character.get("id", character.get("name", "")),
            )
            or ""
        ).strip()
        for character in enriched_characters
        if isinstance(character, dict)
    }
    expected_character_ids.discard("")
    if not needs_narrator_build and layout.tts_voice_team_path.exists():
        try:
            existing_team = VoiceTeamContract.model_validate(_load_json(layout.tts_voice_team_path))
            provider_ok = existing_team.default_provider == resolved_provider
            # WP2: an author-confirmed team short-circuits the per-entry check.
            # The VoiceTeamContract validator already auto-reset ``confirmed``
            # to False on load if any entry expired since the last approval,
            # so a True value here is a trustworthy "the whole cast is still
            # good as a unit" signal.  We still require the provider to match
            # the run's resolved provider so a platform switch cannot silently
            # reuse a confirmed-but-wrong-platform team.
            if existing_team.confirmed and provider_ok:
                # The confirmed flag vouches for the cast *as a unit*, but it
                # says nothing about coverage: a character introduced after the
                # confirmation has no entry.  Only short-circuit when every
                # expected character is covered; otherwise fall through to the
                # per-entry diagnoser so the new character surfaces as
                # ``missing`` instead of silently reaching synthesis without
                # a voice.
                covered_ids = {entry.character_id for entry in existing_team.entries}
                if expected_character_ids <= covered_ids:
                    reusable_team = True
                    team_diagnoses = []
                else:
                    team_diagnoses = _diagnose_voice_team_reuse(
                        existing_team, expected_character_ids, resolved_provider
                    )
                    reusable_team = False
            else:
                # Collect structured per-character diagnoses whenever the team
                # is not reusable so the caller can tell the author *which*
                # voice to confirm/expire/switch instead of one opaque string.
                team_diagnoses = _diagnose_voice_team_reuse(
                    existing_team, expected_character_ids, resolved_provider
                )
                reusable_team = provider_ok and not team_diagnoses
        except Exception as exc:
            _log.warning("Existing voice team cannot be reused: %s", exc)
            team_diagnoses = [
                {
                    "character_id": "",
                    "character_name": "",
                    "reason": REASON_UNKNOWN,
                    "detail": f"配音团队文件解析失败：{exc}",
                }
            ]
    elif not needs_narrator_build:
        # Team file missing entirely: produce the same per-character "missing"
        # diagnoses the readiness preflight synthesises, so the failure payload
        # is actionable instead of carrying an empty diagnoses list.
        team_diagnoses = _diagnose_voice_team_reuse(None, expected_character_ids, resolved_provider)

    if reusable_team:
        _emit_tts_progress(
            on_step_progress,
            "tts_voice_team_reused",
            {"chapter": chapter_number, "characters": len(enriched_characters)},
        )
    else:
        if not allow_voice_team_rebuild:
            confirmable_ids = [
                d["character_id"]
                for d in team_diagnoses
                if d.get("reason") == REASON_PENDING_APPROVAL and d.get("character_id")
            ]
            return ExecutionResult(
                project_id=project_id,
                result={
                    "error": (
                        "自动配音只复用已确认的稳定配音团队；检测到角色音色缺失、过期或平台不一致，"
                        "请先在声腔工作室手动构建或确认配音团队。"
                    ),
                    "error_code": "tts_voice_team_manual_rebuild_required",
                    "automation_mode": resolved_automation_mode.value,
                    "missing_artifact": "voice_team",
                    "chapter_number": chapter_number,
                    "diagnoses": team_diagnoses,
                    "confirmable_character_ids": confirmable_ids,
                },
            )
        team_result = await execute_build_voice_team(
            project_id=project_id,
            characters=enriched_characters,
            settings=settings,
            layout=layout,
            provider=provider,
            on_step_progress=on_step_progress,
        )
        if "error" in team_result.result:
            return team_result

    # Step 2: Preserve a script that still targets the authoritative chapter.
    # A caller supplying new scene context explicitly asks for a fresh script;
    # otherwise the exact per-segment synthesis fingerprints decide what has
    # to be regenerated, while final assembly is always recomputed.
    reusable_script = False
    if not scene_context and layout.tts_dubbing_script_path(chapter_number).exists():
        try:
            existing_script = DubbingScript.model_validate(
                _load_json(layout.tts_dubbing_script_path(chapter_number))
            )
            reusable_script = bool(
                existing_script.segments
                and _script_source_mismatch(layout, chapter_number, existing_script) is None
                and _script_source_audit_error(layout, chapter_number, existing_script) is None
                # In automated modes, a script that the source adjudicator
                # could not finish is a repair candidate, not a reusable
                # artifact.  Regenerating it runs the richer source-evidence
                # adjudication before synthesis reaches the speaker hard gate.
                and not unresolved_speaker_indices(existing_script)
            )
        except Exception as exc:
            _log.warning("Existing dubbing script cannot be reused: %s", exc)

    if reusable_script:
        _emit_tts_progress(
            on_step_progress,
            "tts_script_reused",
            {"chapter": chapter_number, "segment_count": len(existing_script.segments)},
        )
    else:
        script_result = await execute_generate_dubbing_script(
            project_id=project_id,
            chapter_number=chapter_number,
            chapter_text=chapter_text,
            settings=settings,
            layout=layout,
            scene_context=scene_context,
            character_voices=upstream["character_voices"],
            style_profile=upstream["style_profile"],
            tts_metadata=upstream["tts_metadata"],
            scene_intents=upstream["scene_intents"],
            on_step_progress=on_step_progress,
        )
        if "error" in script_result.result:
            return script_result

    # Step 3: Synthesize and assemble
    return await execute_synthesize_chapter(
        project_id=project_id,
        chapter_number=chapter_number,
        settings=settings,
        layout=layout,
        provider=provider,
        automation_mode=resolved_automation_mode,
        on_step_progress=on_step_progress,
        on_segment_progress=on_segment_progress,
    )


_AUDIOBOOK_FILENAME_CLEAN_RE = re.compile(r'[\\/:*?"<>|\s]+')
_AUDIOBOOK_RESULT_FILE_RE = re.compile(r"chapter_(\d{3,})_audio\.json$")


def _audiobook_safe_filename(text: str, fallback: str) -> str:
    cleaned = _AUDIOBOOK_FILENAME_CLEAN_RE.sub("_", text.strip()).strip("._")
    return cleaned[:60] or fallback


def _chapter_display_title(layout: ProjectLayout, chapter_number: int) -> str:
    """First non-empty heading/line of the final chapter text, else outline title."""
    chapter_path = layout.chapter_path(chapter_number)
    if chapter_path.is_file():
        try:
            for line in chapter_path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip().lstrip("#").strip()
                if stripped:
                    return stripped[:80]
        except OSError:
            pass
    if layout.outline_path.is_file():
        try:
            outline = _load_json(layout.outline_path)
        except (OSError, ValueError):
            outline = {}
        for entry in outline.get("chapters") or []:
            if isinstance(entry, dict) and int(entry.get("chapter_number") or 0) == chapter_number:
                title = str(entry.get("title") or "").strip()
                if title:
                    return title[:80]
    return f"第 {chapter_number} 章"


def _format_timestamp_ms(ms: int) -> str:
    total_seconds, milliseconds = divmod(max(0, int(ms)), 1000)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def _discover_assembled_chapters(layout: ProjectLayout) -> list[int]:
    results_dir = layout.tts_audio_result_path(1).parent
    if not results_dir.is_dir():
        return []
    chapters: list[int] = []
    for path in sorted(results_dir.iterdir()):
        match = _AUDIOBOOK_RESULT_FILE_RE.search(path.name)
        if match and path.is_file():
            chapters.append(int(match.group(1)))
    return sorted(chapters)


def _chapter_assembly_report_section(
    *,
    chapter_number: int,
    title: str,
    audio_file: str,
    script: DubbingScript,
    timeline: SpeechTimeline | None,
) -> str:
    """Segment-level assembly report section built from timeline alignment.

    Segment number + timestamp lets a re-record target be located exactly
    without re-listening to the whole chapter master.
    """
    speakers = {
        segment.segment_index: segment.character_name or "旁白"
        for segment in script.segments
    }
    lines = [
        f"## 第 {chapter_number} 章：{title}",
        "",
        f"- 音频文件：`{audio_file}`",
    ]
    if timeline is None:
        lines += [
            "- ⚠️ 未找到对齐时间线，无法提供段级时间戳；请先完成一次完整装配。",
            "",
        ]
        return "\n".join(lines)
    lines += [
        f"- 总时长：{_format_timestamp_ms(timeline.total_duration_ms)}",
        "",
        "| 段号 | 说话人 | 开始 | 结束 | 对齐状态 | 文本摘要 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for entry in timeline.entries:
        text = entry.text.strip().replace("|", "/")
        excerpt = text[:36] + ("…" if len(text) > 36 else "")
        speaker = speakers.get(entry.segment_index, "旁白")
        lines.append(
            f"| {entry.segment_index + 1} | {speaker} | "
            f"{_format_timestamp_ms(entry.start_ms)} | {_format_timestamp_ms(entry.end_ms)} | "
            f"{entry.alignment_status} | {excerpt} |"
        )
    fallback_entries = [
        entry
        for entry in timeline.entries
        if entry.alignment_status not in ("aligned", "inherited")
    ]
    if fallback_entries:
        indices = "、".join(str(entry.segment_index + 1) for entry in fallback_entries[:24])
        lines += [
            "",
            f"> 重录定位提示：以下段落的对齐为回退状态，建议优先抽查：第 {indices} 段。",
        ]
    lines.append("")
    return "\n".join(lines)


@_with_tts_project_lock
async def execute_export_audiobook_package(
    *,
    project_id: str,
    settings: Settings | None = None,
    layout: ProjectLayout,
    chapter_numbers: list[int] | None = None,
    require_delivery_ready: bool = True,
) -> ExecutionResult[dict[str, Any]]:
    """Export a finished audiobook delivery package.

    Produces per-chapter mp3 files (copied from the assembled masters, never
    re-rendered), a TOC/cover ``metadata.json`` and a segment-level assembly
    report reusing the persisted speech timelines.  Chapter ordering is
    gated: the exported range ``[first..last]`` must be continuous and every
    chapter must already be assembled and delivery-confirmed, otherwise the
    export is refused with the exact chapters the author has to confirm
    first ("第 N 章确认后继续"), which the UI and control-plane resume
    consume after the gap is closed.
    """
    del settings  # Path selection is layout-owned; kept for signature parity.
    requested = (
        sorted({int(number) for number in chapter_numbers if int(number) >= 1})
        if chapter_numbers
        else _discover_assembled_chapters(layout)
    )
    if not requested:
        return ExecutionResult(
            project_id=project_id,
            result={
                "error": "没有可导出的章节音频，请先完成至少一章的合成与装配。",
                "error_code": "audiobook_export_no_chapters",
            },
        )

    results: dict[int, ChapterAudioResult] = {}
    gate_blockers: list[dict[str, Any]] = []
    for chapter_number in requested:
        result_path = layout.tts_audio_result_path(chapter_number)
        if not result_path.exists():
            gate_blockers.append(
                {"chapter_number": chapter_number, "reason": "missing_audio_result"}
            )
            continue
        try:
            audio_result = ChapterAudioResult.model_validate(_load_json(result_path))
        except (OSError, ValueError) as exc:
            gate_blockers.append(
                {
                    "chapter_number": chapter_number,
                    "reason": "unreadable_audio_result",
                    "detail": str(exc),
                }
            )
            continue
        assembled_path = Path(audio_result.assembled_audio_path or "")
        ready = (
            audio_result.delivery_ready
            if require_delivery_ready
            else audio_result.is_complete
        )
        if not ready:
            gate_blockers.append(
                {"chapter_number": chapter_number, "reason": "not_delivery_ready"}
            )
            continue
        if not assembled_path.is_file():
            gate_blockers.append(
                {"chapter_number": chapter_number, "reason": "assembled_audio_missing"}
            )
            continue
        results[chapter_number] = audio_result

    # Sequential gate: the exported range must be continuous — a chapter can
    # never ship before every preceding chapter in the range is confirmed.
    first, last = requested[0], requested[-1]
    missing_in_range = [
        chapter_number
        for chapter_number in range(first, last + 1)
        if chapter_number not in results
    ]
    blockers = sorted(
        {blocker["chapter_number"] for blocker in gate_blockers} | set(missing_in_range)
    )
    if blockers:
        display = "、".join(str(number) for number in blockers[:24])
        suffix = "…" if len(blockers) > 24 else ""
        return ExecutionResult(
            project_id=project_id,
            result={
                "error": (
                    f"有声书导出按章节顺序门控：第 {display}{suffix} 章尚未确认"
                    "（未装配或未通过交付门禁），请确认这些章节后继续。"
                ),
                "error_code": "audiobook_export_gate_required",
                "gate_chapters": blockers,
                "gate_blockers": gate_blockers,
            },
        )

    spec = _load_json(layout.spec_path)
    bible = _load_json(layout.bible_path)
    book_title = str(bible.get("title") or spec.get("title") or "未命名作品").strip()
    package_id = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
    package_dir = layout.tts_audiobook_export_dir / package_id
    chapters_dir = package_dir / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    stems_dir = package_dir / "stems"

    cover_source = ""
    for extension in ("png", "jpg", "jpeg", "webp"):
        candidate = layout.root / f"cover.{extension}"
        if candidate.is_file():
            cover_source = candidate.name
            shutil.copy2(candidate, package_dir / candidate.name)
            break

    toc_entries: list[dict[str, Any]] = []
    report_sections: list[str] = []
    total_duration_ms = 0
    total_cost_usd = 0.0
    for chapter_number in sorted(results):
        audio_result = results[chapter_number]
        title = _chapter_display_title(layout, chapter_number)
        audio_file = f"{chapter_number:03d}_{_audiobook_safe_filename(title, 'untitled')}.mp3"
        target_path = chapters_dir / audio_file
        await asyncio.to_thread(
            shutil.copy2, audio_result.assembled_audio_path, target_path
        )
        timeline: SpeechTimeline | None = None
        timeline_path = layout.tts_speech_timeline_path(chapter_number)
        if timeline_path.exists():
            try:
                timeline = SpeechTimeline.model_validate(_load_json(timeline_path))
            except (OSError, ValueError) as exc:
                _log.warning(
                    "Audiobook export: timeline for chapter %d unreadable: %s",
                    chapter_number,
                    exc,
                )
        duration = audio_result.total_duration_ms or (
            timeline.total_duration_ms if timeline is not None else 0
        )
        total_duration_ms += duration
        total_cost_usd += audio_result.total_cost_usd
        chapter_stems: dict[str, str] = {}
        render_report = audio_result.mix_render_report
        for stem_name in ("voice", "bed", "sfx"):
            raw_stem = render_report.stem_paths.get(stem_name, "") if render_report else ""
            if not raw_stem:
                continue
            source_stem = Path(raw_stem).resolve()
            project_root = layout.root.resolve()
            if project_root not in source_stem.parents or not source_stem.is_file():
                continue
            stems_dir.mkdir(parents=True, exist_ok=True)
            stem_file = f"{chapter_number:03d}_{stem_name}_stem.wav"
            await asyncio.to_thread(shutil.copy2, source_stem, stems_dir / stem_file)
            chapter_stems[stem_name] = f"stems/{stem_file}"
        toc_entries.append(
            {
                "chapter_number": chapter_number,
                "title": title,
                "file": f"chapters/{audio_file}",
                "duration_ms": duration,
                "delivery_ready": audio_result.delivery_ready,
                "total_cost_usd": audio_result.total_cost_usd,
                "stems": chapter_stems,
            }
        )
        report_sections.append(
            _chapter_assembly_report_section(
                chapter_number=chapter_number,
                title=title,
                audio_file=f"chapters/{audio_file}",
                script=audio_result.script,
                timeline=timeline,
            )
        )

    metadata = {
        "package_format": "novel_forge.audiobook/v1",
        "package_id": package_id,
        "title": book_title,
        "author": str(spec.get("author", "") or "").strip(),
        "cover": cover_source,
        "language": str(spec.get("language", "") or "zh").strip(),
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "chapter_count": len(toc_entries),
        "total_duration_ms": total_duration_ms,
        "total_cost_usd": round(total_cost_usd, 6),
        "chapters": toc_entries,
    }
    atomic_write_json(package_dir / "metadata.json", metadata)
    manifest = DeliveryManifest(project_id=project_id, title=book_title).register_artifact(
        DeliveryMediaType.AUDIOBOOK,
        str(package_dir / "metadata.json"),
        item_count=len(toc_entries),
        note=f"audiobook package {package_id}",
    )
    atomic_write_json(
        package_dir / "delivery_manifest.json", manifest.model_dump(mode="json")
    )
    report_lines = [
        f"# 有声书汇编报告：{book_title}",
        "",
        f"- 导出时间：{metadata['exported_at']}",
        f"- 章节数：{len(toc_entries)}",
        f"- 总时长：{_format_timestamp_ms(total_duration_ms)}",
        "- 用途：段号 + 时间戳可直接定位需要重录的片段，无需整章重听。",
        "",
        *report_sections,
    ]
    atomic_write_text(package_dir / "assembly_report.md", "\n".join(report_lines))
    _log.info(
        "Exported audiobook package %s (%d chapters, %d ms)",
        package_dir,
        len(toc_entries),
        total_duration_ms,
    )
    return ExecutionResult(
        project_id=project_id,
        result={
            "package_dir": str(package_dir),
            "package_id": package_id,
            "metadata": metadata,
            "manifest_path": str(package_dir / "delivery_manifest.json"),
            "chapters": [entry["chapter_number"] for entry in toc_entries],
        },
    )


def _project_owned_file(layout: ProjectLayout, raw_path: str) -> Path | None:
    """Return an existing project-local file without following external paths."""

    if not raw_path:
        return None
    try:
        path = Path(raw_path).resolve()
        project_root = layout.root.resolve()
    except OSError:
        return None
    if project_root not in path.parents or not path.is_file():
        return None
    return path


def _atomic_zip(destination: Path, entries: list[tuple[Path, str]]) -> None:
    """Build a ZIP beside its destination, then publish it atomically."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    # A cancelled ``to_thread`` archive may still be unwinding while a resumed
    # job starts. A per-attempt filename prevents the two writers colliding.
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.partial")
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            for source, archive_name in entries:
                archive.write(source, archive_name)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_copy(source: Path, destination: Path) -> None:
    """Copy a delivery artifact without exposing a partially written target."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.partial")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_convert_audio(
    source: Path,
    destination: Path,
    *,
    format: str,
    target_lufs: float | None,
) -> bool:
    """Convert an audio master to a temporary sibling before publishing it."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    # FFmpeg chooses an output muxer from the filename.  Keep the requested
    # audio extension on the temporary path instead of ending it in
    # ``.partial`` (which would make WAV/FLAC/MP3 exports fail before the
    # atomic replace can happen).
    suffix = destination.suffix or f".{format}"
    temporary = destination.with_name(
        f".{destination.stem}.{uuid4().hex}.partial{suffix}"
    )
    try:
        converted = convert_audio_for_export(
            source,
            temporary,
            format=format,
            target_lufs=target_lufs,
        )
        if converted:
            os.replace(temporary, destination)
        return converted
    finally:
        temporary.unlink(missing_ok=True)


def _export_destination(
    export_dir: Path,
    filename: str,
    destination: Path | None,
) -> Path:
    """Choose either the Engine-owned delivery location or a UI-selected path."""

    resolved = destination.expanduser() if destination is not None else export_dir / filename
    if resolved.exists() and resolved.is_dir():
        raise IsADirectoryError(f"导出目标不是文件：{resolved}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def _load_fresh_delivery_result(
    layout: ProjectLayout,
    chapter_number: int,
) -> tuple[ChapterAudioResult | None, dict[str, Any] | None]:
    """Load a delivery-ready chapter master and reject stale source artifacts."""

    result_path = layout.tts_audio_result_path(chapter_number)
    try:
        payload = _load_json(result_path)
        result = require_delivery_ready(ChapterAudioResult.model_validate(payload))
    except (OSError, TypeError, ValueError, TTSDeliveryNotReadyError) as exc:
        return None, {
            "error": f"第 {chapter_number} 章尚未达到交付条件：{exc}",
            "error_code": "voice_export_delivery_not_ready",
        }

    source_mismatch = tts_artifact_source_mismatch(
        layout,
        chapter_number,
        tts_audio_result_source_hash(payload),
        artifact_name="Chapter audio",
        error_code="stale_chapter_audio",
    )
    if source_mismatch is not None:
        return None, source_mismatch
    script_mismatch = tts_audio_result_script_mismatch(layout, chapter_number, payload)
    if script_mismatch is not None:
        return None, script_mismatch
    return result, None


def _voice_book_archive_entries(
    layout: ProjectLayout,
    *,
    include_subtitles: bool,
) -> list[tuple[Path, str]]:
    """Collect delivery-confirmed chapter masters and optional local stems/subtitles."""

    entries: list[tuple[Path, str]] = []
    for result_path in sorted((layout.tts_dir / "results").glob("chapter_*_audio.json")):
        try:
            result_path_number = int(result_path.stem.split("_")[1])
        except (IndexError, ValueError):
            continue
        result, _error = _load_fresh_delivery_result(layout, result_path_number)
        if result is None:
            continue
        audio_path = _project_owned_file(layout, result.assembled_audio_path)
        if audio_path is None:
            continue
        entries.append((audio_path, f"chapter_{result.chapter_number:03d}.mp3"))
        render_report = result.mix_render_report
        for stem_name in ("voice", "bed", "sfx"):
            raw_stem = render_report.stem_paths.get(stem_name, "") if render_report else ""
            stem_path = _project_owned_file(layout, raw_stem)
            if stem_path is not None:
                entries.append((stem_path, f"stems/chapter_{result.chapter_number:03d}_{stem_name}.wav"))
        if include_subtitles:
            subtitle_path = _project_owned_file(layout, result.subtitle_path or "")
            if subtitle_path is not None:
                entries.append((subtitle_path, f"chapter_{result.chapter_number:03d}.srt"))
    return entries


@_with_tts_project_lock
async def execute_export_audio_delivery(
    *,
    project_id: str,
    layout: ProjectLayout,
    scope: str,
    chapter_number: int | None,
    format: str,
    include_subtitles: bool = False,
    target_lufs: float | None = None,
    destination: Path | None = None,
) -> ExecutionResult[dict[str, Any]]:
    """Create a chapter or finished-deliveries archive for Engine job execution.

    The API layer deliberately does not perform the file work.  This function
    is consumed by ``JobService`` so conversion/archiving is durable,
    cancellable at the work-unit boundary, and resumable from its persisted
    intent.  Engine callers leave ``destination`` unset so a project-owned
    delivery is published and exposed through a validated download URL.  The
    legacy desktop client may pass its user-selected local save path; file
    work, readiness checks, and atomic publication remain shared.
    """

    normalized_scope = scope.strip().lower()
    normalized_format = format.strip().lower()
    export_dir = layout.root / "exports" / "voice"

    if normalized_scope == "book":
        if normalized_format != "zip":
            return ExecutionResult(
                project_id=project_id,
                result={
                    "error": "全书导出仅支持 ZIP 格式。",
                    "error_code": "voice_export_book_format_invalid",
                },
            )
        filename = f"{project_id}_voice_export.zip"
        try:
            output_path = _export_destination(export_dir, filename, destination)
        except OSError as exc:
            return ExecutionResult(
                project_id=project_id,
                result={"error": f"无法准备导出目标：{exc}", "error_code": "voice_export_path_invalid"},
            )
        entries = await asyncio.to_thread(
            _voice_book_archive_entries,
            layout,
            include_subtitles=include_subtitles,
        )
        if not entries:
            return ExecutionResult(
                project_id=project_id,
                result={
                    "error": "没有达到交付条件的章节音频。",
                    "error_code": "voice_export_no_deliveries",
                },
            )
        await asyncio.to_thread(_atomic_zip, output_path, entries)
    elif normalized_scope == "chapter":
        if chapter_number is None:
            return ExecutionResult(
                project_id=project_id,
                result={
                    "error": "请选择需要导出的章节。",
                    "error_code": "voice_export_chapter_required",
                },
            )
        result, delivery_error = _load_fresh_delivery_result(layout, chapter_number)
        if delivery_error is not None or result is None:
            return ExecutionResult(project_id=project_id, result=delivery_error or {})
        if normalized_format == "srt":
            source = _project_owned_file(layout, result.subtitle_path or "")
            if source is None:
                return ExecutionResult(
                    project_id=project_id,
                    result={
                        "error": "本章尚无可交付字幕。",
                        "error_code": "voice_export_subtitle_missing",
                    },
                )
            filename = f"chapter_{chapter_number:03d}.srt"
            try:
                output_path = _export_destination(export_dir, filename, destination)
            except OSError as exc:
                return ExecutionResult(
                    project_id=project_id,
                    result={
                        "error": f"无法准备导出目标：{exc}",
                        "error_code": "voice_export_path_invalid",
                    },
                )
            await asyncio.to_thread(_atomic_copy, source, output_path)
        elif normalized_format in {"mp3", "wav", "flac"}:
            source = _project_owned_file(layout, result.assembled_audio_path)
            if source is None:
                return ExecutionResult(
                    project_id=project_id,
                    result={
                        "error": "本章主音轨文件不存在或不在项目目录内。",
                        "error_code": "voice_export_master_missing",
                    },
                )
            filename = f"chapter_{chapter_number:03d}.{normalized_format}"
            try:
                output_path = _export_destination(export_dir, filename, destination)
            except OSError as exc:
                return ExecutionResult(
                    project_id=project_id,
                    result={
                        "error": f"无法准备导出目标：{exc}",
                        "error_code": "voice_export_path_invalid",
                    },
                )
            if normalized_format == "mp3" and target_lufs is None:
                await asyncio.to_thread(_atomic_copy, source, output_path)
            else:
                converted = await asyncio.to_thread(
                    _atomic_convert_audio,
                    source,
                    output_path,
                    format=normalized_format,
                    target_lufs=target_lufs,
                )
                if not converted:
                    return ExecutionResult(
                        project_id=project_id,
                        result={
                            "error": f"{normalized_format.upper()} 格式转换失败。",
                            "error_code": "voice_export_conversion_failed",
                        },
                    )
        else:
            return ExecutionResult(
                project_id=project_id,
                result={
                    "error": f"不支持的章节交付格式：{normalized_format}",
                    "error_code": "voice_export_format_unsupported",
                },
            )
    else:
        return ExecutionResult(
            project_id=project_id,
            result={
                "error": f"不支持的导出范围：{normalized_scope or scope}",
                "error_code": "voice_export_scope_unsupported",
            },
        )

    return ExecutionResult(
        project_id=project_id,
        result={
            "export_filename": filename,
            "export_format": normalized_format,
            "export_scope": normalized_scope,
            "chapter_number": chapter_number,
            "message": f"导出已完成：{filename}",
        },
    )


async def execute_export_audiobook_delivery(
    *,
    project_id: str,
    layout: ProjectLayout,
    chapter_numbers: list[int] | None = None,
    require_delivery_ready: bool = True,
) -> ExecutionResult[dict[str, Any]]:
    """Package a completed audiobook into an atomically published ZIP delivery."""

    execution = await execute_export_audiobook_package(
        project_id=project_id,
        layout=layout,
        chapter_numbers=chapter_numbers,
        require_delivery_ready=require_delivery_ready,
    )
    result = dict(execution.result)
    if result.get("error"):
        return ExecutionResult(project_id=project_id, result=result)

    raw_package_dir = Path(str(result.get("package_dir", "") or ""))
    try:
        resolved_package_dir = raw_package_dir.resolve()
        project_root = layout.root.resolve()
    except OSError:
        resolved_package_dir = raw_package_dir
        project_root = layout.root
    if project_root not in resolved_package_dir.parents or not resolved_package_dir.is_dir():
        return ExecutionResult(
            project_id=project_id,
            result={
                "error": "导出包目录不存在。",
                "error_code": "audiobook_export_package_missing",
            },
        )
    package_id = str(result.get("package_id", "") or "package")
    filename = f"{project_id}_audiobook_{package_id}.zip"
    destination = layout.root / "exports" / "voice" / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    entries = [
        (path, f"{resolved_package_dir.name}/{path.relative_to(resolved_package_dir)}")
        for path in sorted(resolved_package_dir.rglob("*"))
        if path.is_file()
    ]
    await asyncio.to_thread(_atomic_zip, destination, entries)
    return ExecutionResult(
        project_id=project_id,
        result={
            **result,
            "export_filename": filename,
            "export_format": "zip",
            "export_scope": "audiobook",
            "message": (
                f"有声书包已导出（{len(result.get('chapters', []) or [])} 章）：{filename}"
            ),
        },
    )
