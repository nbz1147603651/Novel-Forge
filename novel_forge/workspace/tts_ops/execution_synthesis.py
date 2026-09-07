"""
Segment/chapter synthesis executions.

Extracted from execution.py: chapter synthesis orchestration, single
segment audition synthesis and take acceptance.  Depends on shared,
assets, voice_team and script domains (acyclic).
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from novel_forge.core.config import Settings
from novel_forge.obs.logger import get_logger
from novel_forge.persistence.filesystem import (
    atomic_write_json,
)
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.assets.sound_library import commercial_rights_issues, resolved_asset_paths
from novel_forge.tts.audio_quality import apply_delivery_readiness, evaluate_audio_quality
from novel_forge.tts.delivery_profile import resolve_audio_delivery_profile
from novel_forge.tts.gateway.factory import (
    TTSAdapterRegistry,
)
from novel_forge.tts.pipeline.align_speech_timeline_step import (
    AlignSpeechTimelineInput,
    AlignSpeechTimelineStep,
    apply_speech_timeline_to_script,
)
from novel_forge.tts.pipeline.alignment_repair import select_alignment_repair_segments
from novel_forge.tts.pipeline.assemble_audio_step import AssembleAudioInput, AssembleAudioStep
from novel_forge.tts.pipeline.mix_plan_builder import build_mix_plan
from novel_forge.tts.pipeline.synthesize_audio_step import SynthesizeAudioInput, SynthesizeAudioStep
from novel_forge.tts.platform.config import registry_from_settings
from novel_forge.tts.platform.preflight import (
    preflight_audio_execution_plan,
    run_live_audio_preflight,
)
from novel_forge.tts.platform.schemas import (
    AudioExecutionStage,
    AudioPreflightCheck,
    AudioPreflightStatus,
)
from novel_forge.tts.schemas import (
    ChapterAudioResult,
    ChapterSoundResolutionReport,
    DubbingScript,
    DubbingSegment,
    NarratorVoiceProfile,
    SegmentTakeVersion,
    SegmentType,
    SynthesisResult,
    SynthesisStatus,
    TakeReviewStatus,
    TTSProgressState,
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
from novel_forge.tts.services.studio_service import VoiceStudioProjectService
from novel_forge.tts.sound_generation.service import SoundGenerationService
from novel_forge.workspace.execution_result import ExecutionResult
from novel_forge.workspace.tts_ops.execution_assets import (
    _clear_voice_library_activation_deadlines,
    _load_or_freeze_audio_execution_plan,
    _recent_audio_reference_lufs,
)
from novel_forge.workspace.tts_ops.execution_script import (
    _repair_unresolved_speakers,
    _script_source_audit_error,
    _script_source_mismatch,
)
from novel_forge.workspace.tts_ops.execution_shared import (
    _emit_tts_progress,
    _ensure_managed_plan_runtimes,
    _ensure_managed_tts_runtime,
    _load_json,
    _load_tts_upstream_context,
    _resolve_provider,
)
from novel_forge.workspace.tts_ops.execution_voice_team import (
    _apply_execution_tts_assignment,
    _clear_activated_voice_deadlines,
    _lock_used_voice_identities,
    _voice_team_content_hash,
)
from novel_forge.workspace.tts_ops.lock_manager import (
    with_tts_project_lock as _with_tts_project_lock,
)
from novel_forge.workspace.tts_ops.preflight import (  # noqa: F401
    _enrich_preflight_failure,
    _preflight_failure_guidance,
    _preflight_failure_severity,
)
from novel_forge.workspace.tts_ops.progress import (
    _persist_synthesis_progress_snapshot,
    _resolve_reusable_takes,
)
from novel_forge.workspace.tts_ops.voice_assignment import (  # noqa: F401
    _assign_narrator_voice,
    _narrator_profile_content_hash,
    _narrator_voice_match_input,
)

_log = get_logger("workspace.tts")


@_with_tts_project_lock
async def execute_synthesize_chapter(
    *,
    project_id: str,
    chapter_number: int,
    settings: Settings,
    layout: ProjectLayout,
    provider: str = "",
    automation_mode: AudioAutomationMode | str | None = None,
    on_step_progress: Any = None,
    on_segment_progress: Any = None,
) -> ExecutionResult[dict[str, Any]]:
    """Synthesize audio for a chapter.

    Runs the full TTS pipeline: script generation → synthesis → assembly.
    Supports dynamic narrator adaptation via NarratorVoiceProfile.

    Args:
        project_id: Project identifier.
        chapter_number: Chapter number.
        settings: Application settings.
        layout: Project layout for storage paths.
        provider: TTS provider override.
        on_step_progress: Optional step-level progress callback.
        on_segment_progress: Optional per-segment progress callback (segment_idx, status_str).

    Returns:
        ExecutionResult with ChapterAudioResult dict.
    """
    resolved_automation_mode, settings = settings_for_audio_automation_mode(
        settings,
        automation_mode,
    )
    _log.info(
        "Synthesizing chapter %d in %s mode",
        chapter_number,
        resolved_automation_mode.value,
    )
    _emit_tts_progress(
        on_step_progress,
        "tts_automation_mode",
        {"chapter": chapter_number, "mode": resolved_automation_mode.value},
    )

    # Load voice team
    if not layout.tts_voice_team_path.exists():
        return ExecutionResult(
            project_id=project_id,
            result={
                "error": (
                    "未找到配音团队；请先在声腔工作室构建并确认配音团队，"
                    "或在前端点击“自动组建配音团队”。"
                ),
                "error_code": "tts_voice_team_missing",
                "missing_prerequisites": ["配音团队"],
                "action": "build_voice_team",
            },
        )

    team_data = _load_json(layout.tts_voice_team_path)
    voice_team = VoiceTeamContract.model_validate(team_data)

    # Load or generate script
    script_path = layout.tts_dubbing_script_path(chapter_number)
    if not script_path.exists():
        return ExecutionResult(
            project_id=project_id,
            result={
                "error": f"未找到第 {chapter_number} 章配音脚本；请先生成配音脚本。",
                "error_code": "tts_script_missing",
                "missing_prerequisites": [f"第 {chapter_number} 章配音脚本"],
                "action": "generate_script",
            },
        )

    script_data = _load_json(script_path)
    script = DubbingScript.model_validate(script_data)
    source_error = _script_source_mismatch(layout, chapter_number, script)
    if source_error is not None:
        return ExecutionResult(project_id=project_id, result=source_error)
    audit_error = _script_source_audit_error(layout, chapter_number, script)
    if audit_error is not None:
        return ExecutionResult(project_id=project_id, result=audit_error)
    unresolved_speakers = unresolved_speaker_indices(script)
    if unresolved_speakers:
        # In non-manual automation modes, attempt a targeted LLM repair pass
        # that provides richer context (wider paragraphs, full character roster,
        # dialogue flow) so the model can resolve speakers it missed initially.
        if resolved_automation_mode != AudioAutomationMode.MANUAL:
            script = await _repair_unresolved_speakers(
                script=script,
                unresolved_indices=unresolved_speakers,
                voice_team=voice_team,
                layout=layout,
                chapter_number=chapter_number,
                settings=settings,
                on_step_progress=on_step_progress,
            )
            unresolved_speakers = unresolved_speaker_indices(script)
        if unresolved_speakers:
            return ExecutionResult(
                project_id=project_id,
                result={
                    "error": (
                        f"还有 {len(unresolved_speakers)} 段对白的说话人未通过正文审核；"
                        "请先在配音脚本编辑器中指定角色。"
                    ),
                    "error_code": "tts_speaker_review_required",
                    "segment_indices": list(unresolved_speakers),
                },
            )

    # Completeness gate: reject incomplete scripts before synthesis
    from novel_forge.tts.pipeline.script_completeness_gate import validate_script_completeness

    completeness = validate_script_completeness(script, voice_team, settings)
    if not completeness.passed:
        return ExecutionResult(
            project_id=project_id,
            result={
                "error": "配音脚本不完整，请先重新生成或修复后再合成。",
                "error_code": "tts_script_incomplete",
                "failures": completeness.failures,
            },
        )

    upstream = _load_tts_upstream_context(layout, chapter_number)
    voice_team_hash = _voice_team_content_hash(voice_team)
    script_hash = script.script_hash or compute_dubbing_script_hash(script)

    # Load narrator profile for dynamic adaptation
    narrator_profile: NarratorVoiceProfile | None = None
    narrator_voice_id = ""
    if layout.tts_narrator_profile_path.exists():
        try:
            profile_data = _load_json(layout.tts_narrator_profile_path)
            narrator_profile = NarratorVoiceProfile.model_validate(profile_data)
            if narrator_profile.is_expired:
                _log.warning(
                    "Narrator voice has expired; using provider fallback until it is rebuilt"
                )
                narrator_profile = None
            else:
                narrator_voice_id = narrator_profile.voice_id
        except Exception as exc:
            _log.warning("Failed to load narrator profile: %s", exc)

    # Resolve the selected platform, then let an explicit advanced TTS-stage
    # override choose a different registered deep adapter/model.  In normal
    # mode the planner pins this stage to the platform chosen in Voice Studio.
    selected_provider = _resolve_provider(settings, provider)
    effective_audio_settings = settings.model_copy(
        update={"tts_default_provider": selected_provider.value}
    )
    languages = list(
        dict.fromkeys(
            run.language
            for segment in script.segments
            for run in segment.language_runs
            if run.language and run.language != "auto"
        )
    ) or ["zh"]
    try:
        execution_plan = _load_or_freeze_audio_execution_plan(
            project_id=project_id,
            settings=effective_audio_settings,
            layout=layout,
            languages=languages,
        )
    except ValueError as exc:
        return ExecutionResult(
            project_id=project_id,
            result={
                "error": f"{exc}。请重新冻结项目音频路由。",
                "error_code": "audio_execution_plan_required",
            },
        )
    try:
        provider_enum, planned_tts_model = _apply_execution_tts_assignment(
            selected_provider=selected_provider,
            execution_plan=execution_plan,
        )
    except ValueError as exc:
        return ExecutionResult(
            project_id=project_id,
            result={"error": str(exc), "error_code": "audio_tts_adapter_required"},
        )
    effective_audio_settings = effective_audio_settings.model_copy(
        update={"tts_default_provider": provider_enum.value}
    )
    await _ensure_managed_tts_runtime(effective_audio_settings, provider_enum)
    registry = TTSAdapterRegistry.get_instance(effective_audio_settings)
    sound_generation_service = SoundGenerationService(
        settings=effective_audio_settings,
        layout=layout,
        execution_plan=execution_plan,
    )
    # VAD is excluded from active_stages: it is frozen into the plan for audit
    # completeness but has no runtime consumer -- ``AlignSpeechTimelineStep``
    # never references the VAD route.  Probing an unreachable, unused sidecar
    # would needlessly block synthesis.  ASR/ALIGN are kept as soft
    # dependencies: ``run_live_audio_preflight`` downgrades their connection
    # failures to WARNING so a cloud-TTS project is not blocked by a local
    # alignment sidecar it does not need.
    active_stages = {
        AudioExecutionStage.ASR,
        AudioExecutionStage.ALIGN,
    }
    active_stages.update(sound_generation_service.required_execution_stages(script))
    await _ensure_managed_plan_runtimes(
        effective_audio_settings,
        execution_plan,
        active_stages=active_stages,
    )
    platform_registry = registry_from_settings(effective_audio_settings)
    preflight = preflight_audio_execution_plan(
        execution_plan,
        settings=effective_audio_settings,
        registry=platform_registry,
        active_stages=active_stages,
    )
    preflight = await run_live_audio_preflight(
        execution_plan,
        preflight,
        settings=effective_audio_settings,
        active_stages=active_stages,
    )
    adapter = registry.get_adapter(provider_enum)
    healthy = await adapter.health_check()
    formal_route = execution_plan.assignment_for(AudioExecutionStage.TTS_FORMAL)
    formal_plugin_id = (
        formal_route.primary.plugin_id if formal_route and formal_route.primary else ""
    )
    preflight.checks.append(
        AudioPreflightCheck(
            kind="health",
            status=(AudioPreflightStatus.PASSED if healthy else AudioPreflightStatus.FAILED),
            message=(
                "正式 TTS 主路由健康检查通过。"
                if healthy
                else f"正式 TTS 主路由健康检查失败：{adapter.last_health_error}"
            ),
            stage=AudioExecutionStage.TTS_FORMAL,
            plugin_id=formal_plugin_id,
        )
    )
    execution_plan = execution_plan.model_copy(update={"preflight": preflight})
    execution_plan_path = layout.tts_execution_plan_path
    execution_plan_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(execution_plan_path, execution_plan.model_dump(mode="json"))
    if effective_audio_settings.audio_preflight_blocking and not preflight.passed:
        failed_checks = [
            _enrich_preflight_failure(item)
            for item in preflight.checks
            if item.status == AudioPreflightStatus.FAILED
        ]
        optional_sound_stages = {
            AudioExecutionStage.MUSIC,
            AudioExecutionStage.SOUNDSCAPE,
            AudioExecutionStage.SFX,
        }
        blocking_failed_checks = [
            item
            for item in preflight.checks
            if item.status == AudioPreflightStatus.FAILED
            and (
                resolved_automation_mode == AudioAutomationMode.AUTONOMOUS
                or item.stage not in optional_sound_stages
            )
        ]
        if blocking_failed_checks:
            return ExecutionResult(
                project_id=project_id,
                result={
                    "error": "音频生产预检未通过，请先修复鉴权、路由、隐私或本地资源问题。",
                    "error_code": "audio_preflight_failed",
                    "automation_mode": resolved_automation_mode.value,
                    "plan_id": execution_plan.plan_id,
                    "checks": [item.model_dump(mode="json") for item in preflight.checks],
                    "failed_checks": failed_checks,
                },
            )
    if narrator_profile and narrator_profile.provider != provider_enum:
        _log.warning(
            "Narrator voice belongs to %s while synthesis uses %s; using provider fallback",
            narrator_profile.provider.value,
            provider_enum.value,
        )
        narrator_profile = None
        narrator_voice_id = ""

    # Resume support: load progress state with content-addressed reuse.
    # Previously the checkpoint required all four of chapter_number, script_hash,
    # voice_team_hash, and provider to match exactly -- changing a single
    # segment's text invalidated the entire checkpoint and forced re-synthesis of
    # all segments.  We now load the checkpoint when voice_team_hash + provider
    # match (the hard requirements for audio re-usability), then resolve reusable
    # takes by content identity (segment_uid) instead of positional index.
    completed_segments: list[int] = []
    completed_segment_hashes: dict[str, str] = {}
    completed_results: list[SynthesisResult] = []
    progress_path = layout.tts_progress_path_for_chapter(chapter_number)
    legacy_progress_path = layout.tts_progress_path
    progress_state: TTSProgressState | None = None
    for candidate in (progress_path, legacy_progress_path):
        if not candidate.exists():
            continue
        try:
            progress_data = _load_json(candidate)
            progress_state = TTSProgressState.model_validate(progress_data)
            if (
                progress_state.chapter_number == chapter_number
                and progress_state.voice_team_hash == voice_team_hash
                and progress_state.provider == provider_enum
            ):
                break
            progress_state = None
        except Exception as exc:
            _log.warning("Failed to load TTS progress state from %s: %s", candidate, exc)

    if progress_state is not None:
        completed_segments, completed_segment_hashes, completed_results = _resolve_reusable_takes(
            progress_state, script, script_hash=script_hash
        )
        if completed_segments:
            _log.info(
                "Resuming synthesis: %d segments matched by content identity "
                "(script_hash may differ)",
                len(completed_segments),
            )

    # Fallback: when the progress checkpoint was cleaned up after a prior
    # successful synthesis, detect completion via the persisted audio result
    # file.  This prevents redundant full-chapter re-synthesis when the user
    # triggers "AI 自主成片" (or any full-pipeline invocation) after synthesis
    # has already finished.
    if not completed_segments:
        _result_path = layout.tts_audio_result_path(chapter_number)
        if _result_path.exists():
            try:
                _existing_result_data = _load_json(_result_path)
                _existing_meta = _existing_result_data.get("metadata", {})
                _hashes_match = (
                    _existing_result_data.get("is_complete") is True
                    and _existing_meta.get("script_hash") == script_hash
                    and _existing_meta.get("voice_team_hash") == voice_team_hash
                    and _existing_meta.get("provider") == provider_enum.value
                )
                if _hashes_match:
                    _prior_results = [
                        SynthesisResult.model_validate(sr)
                        for sr in _existing_result_data.get("segment_results", [])
                        if isinstance(sr, dict)
                        and sr.get("status") == SynthesisStatus.COMPLETED.value
                    ]
                    # Verify audio files still exist on disk before trusting
                    # the result; a manually deleted file must trigger
                    # re-synthesis for that segment.
                    _verified_results: list[SynthesisResult] = []
                    for _sr in _prior_results:
                        _ap = Path(_sr.audio_path) if _sr.audio_path else None
                        if _ap and _ap.exists() and _ap.stat().st_size > 0:
                            _verified_results.append(_sr)
                    if _verified_results:
                        completed_results = _verified_results
                        completed_segments = [r.segment_index for r in _verified_results]
                        completed_segment_hashes = {
                            str(r.segment_index): r.request_hash
                            for r in _verified_results
                            if r.request_hash
                        }
                        _log.info(
                            "Synthesis already complete: reusing %d segment results "
                            "from persisted audio result (progress checkpoint absent)",
                            len(completed_segments),
                        )
                        _emit_tts_progress(
                            on_step_progress,
                            "tts_synthesis_reused",
                            {
                                "chapter": chapter_number,
                                "completed": len(completed_segments),
                                "reused": len(completed_segments),
                                "total": len(script.segments),
                            },
                        )
            except Exception as exc:
                _log.debug("Audio result fallback check failed (non-critical): %s", exc)

    # Step 1: Synthesize segments
    _emit_tts_progress(
        on_step_progress,
        "tts_synthesis_start",
        {"chapter": chapter_number, "total": len(script.segments)},
    )
    synth_step = SynthesizeAudioStep(
        registry,
        settings=effective_audio_settings,
        layout=layout,
    )
    formal_route = execution_plan.assignment_for(AudioExecutionStage.TTS_FORMAL)
    synth_input = SynthesizeAudioInput(
        chapter_number=chapter_number,
        script=script,
        voice_team=voice_team,
        provider=provider_enum,
        model_id=planned_tts_model,
        execution_plan_id=execution_plan.plan_id,
        route_plugin_id=(
            formal_route.primary.plugin_id if formal_route and formal_route.primary else ""
        ),
        route_plugin_version=(
            formal_route.primary.plugin_version if formal_route and formal_route.primary else ""
        ),
        route_endpoint=(
            formal_route.primary.endpoint if formal_route and formal_route.primary else ""
        ),
        max_concurrent=settings.tts_max_concurrent_synthesis,
        retry_limit=settings.tts_synthesis_retry_limit,
        completed_segments=completed_segments,
        completed_segment_hashes=completed_segment_hashes,
        completed_results=completed_results,
        on_segment_progress=on_segment_progress,
        narrator_voice_id=narrator_voice_id or settings.tts_narrator_voice_id,
        narrator_profile=narrator_profile,
        voice_team_hash=voice_team_hash,
        script_hash=script_hash,
    )
    segment_results = await synth_step.run(synth_input)
    progress_state = _persist_synthesis_progress_snapshot(
        layout=layout,
        chapter_number=chapter_number,
        script=script,
        segment_results=segment_results,
        voice_team_hash=voice_team_hash,
        script_hash=script_hash,
        provider=provider_enum,
    )
    _emit_tts_progress(
        on_step_progress,
        "tts_synthesis_complete",
        {
            "chapter": chapter_number,
            "completed": len(progress_state.completed_segments),
            "failed": len(progress_state.failed_segments),
            "total": len(script.segments),
            "resumable": not progress_state.synthesis_done,
        },
    )
    # Persist failed-segment diagnostics so operators can diagnose provider
    # errors (auth, rate-limit, empty audio) without re-running synthesis.
    _failed_results = [r for r in segment_results if r.status == SynthesisStatus.FAILED]
    if _failed_results:
        _errors_dir = layout.tts_dir / "synthesis_errors"
        _errors_dir.mkdir(parents=True, exist_ok=True)
        _error_payload = {
            "chapter_number": chapter_number,
            "provider": provider_enum.value,
            "model_id": planned_tts_model,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "failed_count": len(_failed_results),
            "total_count": len(script.segments),
            "segments": [
                {
                    "segment_index": r.segment_index,
                    "error_message": r.error_message or "",
                    "failure_kind": r.failure_kind or "",
                    "retryable": r.retryable,
                    "retry_count": r.retry_count,
                    "voice_id": r.requested_voice_id or "",
                    "provider_metadata": r.provider_metadata or {},
                }
                for r in _failed_results
            ],
        }
        _error_path = _errors_dir / f"chapter_{chapter_number:03d}.json"
        atomic_write_json(_error_path, _error_payload)
        _log.warning(
            "Persisted %d synthesis failure(s) to %s",
            len(_failed_results),
            _error_path,
        )
    activated_voice_ids = {
        result.voice_id
        for result in segment_results
        if result.status == SynthesisStatus.COMPLETED and result.voice_id
    }
    voice_team_changed = _clear_activated_voice_deadlines(
        voice_team,
        activated_voice_ids,
    )
    voice_team_changed = (
        _lock_used_voice_identities(voice_team, activated_voice_ids) or voice_team_changed
    )
    if voice_team_changed:
        atomic_write_json(layout.tts_voice_team_path, voice_team.model_dump(mode="json"))
        _log.info(
            "Activated and identity-locked %d successfully used voice(s)",
            len(activated_voice_ids),
        )
    await _clear_voice_library_activation_deadlines(
        storage_root=settings.storage_root,
        voice_ids=activated_voice_ids,
        provider=provider_enum,
    )
    if (
        narrator_profile is not None
        and narrator_profile.activation_deadline is not None
        and narrator_profile.voice_id in activated_voice_ids
    ):
        narrator_profile = narrator_profile.model_copy(
            update={
                "activation_deadline": None,
                "identity_locked": True,
                "identity_locked_at": datetime.now(timezone.utc),
            }
        )
        atomic_write_json(
            layout.tts_narrator_profile_path,
            narrator_profile.model_dump(mode="json"),
        )
    elif (
        narrator_profile is not None
        and narrator_profile.voice_id in activated_voice_ids
        and not narrator_profile.identity_locked
    ):
        narrator_profile = narrator_profile.model_copy(
            update={
                "identity_locked": True,
                "identity_locked_at": datetime.now(timezone.utc),
            }
        )
        atomic_write_json(
            layout.tts_narrator_profile_path,
            narrator_profile.model_dump(mode="json"),
        )

    # Step 2: derive the real speech timeline from the synthesized voice.
    # ASR/alignment/VAD are independently selected from declared capabilities
    # and language coverage, using the same persisted plan that drove TTS.
    _emit_tts_progress(
        on_step_progress,
        "tts_alignment_start",
        {"chapter": chapter_number, "preset": execution_plan.preset.value},
    )
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
    alignment_repair_rounds = 0
    for repair_round in range(settings.tts_alignment_repair_rounds):
        repair_indices = select_alignment_repair_segments(
            timeline,
            minimum_coverage=settings.tts_min_alignment_coverage,
            maximum_text_error_rate=settings.tts_max_text_error_rate,
        )
        if not repair_indices:
            break
        _emit_tts_progress(
            on_step_progress,
            "tts_alignment_repair_start",
            {
                "chapter": chapter_number,
                "round": repair_round + 1,
                "segments": repair_indices,
            },
        )
        current_by_index = {result.segment_index: result for result in segment_results}
        trusted_indices = [
            index
            for index, result in current_by_index.items()
            if index not in repair_indices and result.status == SynthesisStatus.COMPLETED
        ]
        trusted_hashes = {
            str(index): current_by_index[index].request_hash
            for index in trusted_indices
            if current_by_index[index].request_hash
        }
        repair_results = await synth_step.run(
            replace(
                synth_input,
                completed_segments=trusted_indices,
                completed_segment_hashes=trusted_hashes,
            )
        )
        accepted = 0
        for repaired in repair_results:
            if (
                repaired.segment_index in repair_indices
                and repaired.status == SynthesisStatus.COMPLETED
                and repaired.quality_passed
            ):
                current_by_index[repaired.segment_index] = repaired
                accepted += 1
        if not accepted:
            break
        segment_results = sorted(current_by_index.values(), key=lambda item: item.segment_index)
        alignment_repair_rounds += 1
        progress_state = _persist_synthesis_progress_snapshot(
            layout=layout,
            chapter_number=chapter_number,
            script=script,
            segment_results=segment_results,
            voice_team_hash=voice_team_hash,
            script_hash=script_hash,
            provider=provider_enum,
        )
        timeline = await AlignSpeechTimelineStep(
            settings=effective_audio_settings,
            registry=registry_from_settings(effective_audio_settings),
        ).run(
            AlignSpeechTimelineInput(
                chapter_number=chapter_number,
                script=script,
                segment_results=segment_results,
                execution_plan=execution_plan,
                # Repair mode: only the regenerated segments need a fresh ASR
                # + alignment pass; every other segment inherits its validated
                # alignment from the previous timeline instead of re-running
                # the whole chapter's offline ASR stack.
                repair_segment_indices=repair_indices,
                previous_timeline=timeline,
            )
        )
        _emit_tts_progress(
            on_step_progress,
            "tts_alignment_repair_complete",
            {
                "chapter": chapter_number,
                "round": repair_round + 1,
                "accepted": accepted,
            },
        )
    timeline_path = layout.tts_speech_timeline_path(chapter_number)
    timeline_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(timeline_path, timeline.model_dump(mode="json"))
    aligned_count = sum(item.status == "aligned" for item in timeline.alignments)
    _emit_tts_progress(
        on_step_progress,
        "tts_alignment_complete",
        {
            "chapter": chapter_number,
            "aligned": aligned_count,
            "fallback": len(timeline.alignments) - aligned_count,
        },
    )
    mix_script = apply_speech_timeline_to_script(script, timeline)

    # Step 3: Resolve existing sound assets and generate explicitly enabled
    # candidates before the final timeline-aware mix.
    sound_outcome = await sound_generation_service.resolve_or_generate(
        mix_script,
        on_progress=on_step_progress,
    )
    sound_report: ChapterSoundResolutionReport = sound_outcome.resolution
    sound_report_path = layout.tts_sound_resolution_path(chapter_number)
    sound_report_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(sound_report_path, sound_report.model_dump(mode="json"))
    sound_generation_path = layout.tts_sound_generation_report_path(chapter_number)
    sound_generation_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(sound_generation_path, sound_outcome.summary.model_dump(mode="json"))
    resolved_paths = resolved_asset_paths(layout, sound_report)
    sound_commercial_rights_issues = commercial_rights_issues(layout, sound_report)
    delivery_profile = resolve_audio_delivery_profile(settings.tts_audio_quality_tier)
    mix_plan = build_mix_plan(
        chapter_number=chapter_number,
        script=mix_script,
        timeline=timeline,
        segment_results=segment_results,
        resolved_sound_paths=resolved_paths,
        execution_plan=execution_plan,
        mastering_mode=delivery_profile.mastering_mode,
        export_stems=delivery_profile.export_stems,
    )
    mix_plan_path = layout.tts_mix_plan_path(chapter_number)
    mix_plan_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(mix_plan_path, mix_plan.model_dump(mode="json"))

    # Step 4: Assemble narration, approved assets, and subtitles.
    _emit_tts_progress(on_step_progress, "tts_assembly_start", {"chapter": chapter_number})
    assemble_step = AssembleAudioStep(settings=settings, layout=layout)
    assemble_input = AssembleAudioInput(
        chapter_number=chapter_number,
        script=mix_script,
        segment_results=segment_results,
        resolved_sound_paths=resolved_paths,
        enable_bgm_mixing=True,
        enable_sfx_mixing=True,
        mix_plan=mix_plan,
        speech_timeline=timeline,
    )
    try:
        audio_result = await assemble_step.run(assemble_input)
    except Exception as exc:
        progress_state.last_error = f"Audio assembly failed: {exc}"[:512]
        progress_state.updated_at = datetime.now(timezone.utc)
        atomic_write_json(progress_path, progress_state.model_dump(mode="json"))
        _emit_tts_progress(
            on_step_progress,
            "tts_assembly_failed",
            {"chapter": chapter_number, "error": progress_state.last_error},
        )
        raise

    render_report_path = layout.tts_mix_render_report_path(chapter_number)
    if audio_result.mix_render_report is not None:
        render_report_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            render_report_path,
            audio_result.mix_render_report.model_dump(mode="json"),
        )

    quality_report = await asyncio.to_thread(
        evaluate_audio_quality,
        audio_result=audio_result,
        timeline=timeline,
        execution_plan=execution_plan,
        maximum_text_error_rate=settings.tts_max_text_error_rate,
        repair_rounds=alignment_repair_rounds,
        mix_plan=mix_plan,
        unresolved_sound_cues=sound_report.unresolved_count,
        reference_lufs=(
            _recent_audio_reference_lufs(layout, chapter_number)
            if delivery_profile.include_cross_chapter_reference
            else []
        ),
        # Reuse the loudnorm analysis pass instead of decoding the master a
        # second time when the two-pass renderer produced measurements.
        measured_loudness=(
            audio_result.mix_render_report.measured_loudness
            if audio_result.mix_render_report is not None
            else None
        ),
        require_master_quality=delivery_profile.require_master_quality,
        commercial_rights_issues=sound_commercial_rights_issues,
    )
    quality_report_path = layout.tts_audio_quality_report_path(chapter_number)
    quality_report_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(quality_report_path, quality_report.model_dump(mode="json"))
    if (
        (execution_plan.preset.value == "master" or delivery_profile.require_master_quality)
        and settings.tts_master_quality_gate_blocking
        and not quality_report.passed
    ):
        audio_result.is_complete = False
    audio_result = apply_delivery_readiness(
        audio_result,
        quality_report,
    )
    _emit_tts_progress(
        on_step_progress,
        "tts_quality_complete",
        {
            "chapter": chapter_number,
            "passed": quality_report.passed,
            "alignment_coverage": quality_report.alignment_coverage,
            "warnings": len(quality_report.speech_masking_warnings),
        },
    )

    # Persist result
    result_path = layout.tts_audio_result_path(chapter_number)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    audio_result.metadata.update(
        {
            "provider": provider_enum.value,
            "audio_delivery": {
                "tier": delivery_profile.tier,
                "mastering_mode": delivery_profile.mastering_mode,
                "master_quality_required": delivery_profile.require_master_quality,
            },
            "automation_mode": resolved_automation_mode.value,
            "script_hash": script_hash,
            "voice_team_hash": voice_team_hash,
            "source_text_hash": script.source_text_hash,
            "upstream_tts_metadata": bool(upstream["tts_metadata"]),
            "sound_resolution_path": str(sound_report_path),
            "sound_generation_path": str(sound_generation_path),
            "audio_execution_plan_path": str(execution_plan_path),
            "speech_timeline_path": str(timeline_path),
            "mix_plan_path": str(mix_plan_path),
            "mix_render_report_path": (
                str(render_report_path) if audio_result.mix_render_report is not None else ""
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
            "alignment": {
                "aligned": aligned_count,
                "fallback": len(timeline.alignments) - aligned_count,
            },
            "sound_generation": {
                "enabled": sound_outcome.summary.enabled,
                "auto_generate": sound_outcome.summary.auto_generate,
                "auto_approve": sound_outcome.summary.auto_approve,
                "generated": sound_outcome.summary.generated_count,
                "cached": sound_outcome.summary.cached_count,
                "pending_review": sound_outcome.summary.pending_review_count,
                "failed": sound_outcome.summary.failed_count,
                "failures": [
                    {
                        "cue_label": a.request.cue_label,
                        "kind": a.request.kind.value,
                        "provider": a.provider,
                        "error": a.error_message,
                    }
                    for a in sound_outcome.summary.attempts
                    if a.status == "failed"
                ],
            },
            "sound_resolution": {
                "matched": sound_report.matched_count,
                "unresolved": sound_report.unresolved_count,
                "soundscapes": len(script.soundscapes),
                "bgm": len(script.bgm_suggestions),
                "sfx": len(script.sfx_cues),
            },
        }
    )
    atomic_write_json(result_path, audio_result.model_dump(mode="json"))

    progress_state.assembly_done = audio_result.is_complete
    if not audio_result.is_complete and not progress_state.last_error:
        progress_state.last_error = (
            f"Audio assembly is partial: {len(progress_state.completed_segments)}/"
            f"{len(script.segments)} segments completed."
        )
    progress_state.updated_at = datetime.now(timezone.utc)
    try:
        atomic_write_json(progress_path, progress_state.model_dump(mode="json"))
    except Exception as exc:
        _log.warning("Failed to finalize TTS progress state: %s", exc)

    _emit_tts_progress(
        on_step_progress,
        "tts_assembly_complete" if audio_result.is_complete else "tts_assembly_partial",
        {
            "chapter": chapter_number,
            "completed": len(progress_state.completed_segments),
            "failed": len(progress_state.failed_segments),
            "total": len(script.segments),
            "resumable": not audio_result.is_complete,
        },
    )

    # Clean up this chapter's checkpoint on success.  A legacy project-wide
    # checkpoint is removed only when it belongs to the same chapter.
    if audio_result.is_complete:
        cleanup_paths = [progress_path]
        if legacy_progress_path.exists():
            try:
                legacy_state = TTSProgressState.model_validate(_load_json(legacy_progress_path))
                if legacy_state.chapter_number == chapter_number:
                    cleanup_paths.append(legacy_progress_path)
            except Exception:
                pass
        for cleanup_path in cleanup_paths:
            try:
                cleanup_path.unlink(missing_ok=True)
            except Exception:
                pass
        _log.info("TTS progress state cleaned up (synthesis complete)")

    _log.info(
        "Chapter %d synthesized: %d ms, complete=%s",
        chapter_number,
        audio_result.total_duration_ms,
        audio_result.is_complete,
    )

    return ExecutionResult(
        project_id=project_id,
        result=audio_result.model_dump(mode="json"),
    )


@_with_tts_project_lock
async def execute_synthesize_segment(
    *,
    project_id: str,
    chapter_number: int,
    segment_index: int,
    settings: Settings,
    layout: ProjectLayout,
    provider: str = "",
    segment_override: dict[str, Any] | None = None,
) -> ExecutionResult[dict[str, Any]]:
    """Generate one spoken segment for an immediate audition.

    The audition is written under ``tts/takes/.../candidates`` and does not
    overwrite the source script, formal segment audio, result manifest,
    subtitle, master, or resumable synthesis checkpoint.  Acceptance is a
    separate explicit operation.
    """
    if not layout.tts_voice_team_path.exists():
        return ExecutionResult(
            project_id=project_id,
            result={"error": "未找到配音团队；请先完成配音团队配置。"},
        )
    script_path = layout.tts_dubbing_script_path(chapter_number)
    if not script_path.exists():
        return ExecutionResult(
            project_id=project_id,
            result={"error": f"未找到第 {chapter_number} 章配音脚本。"},
        )

    voice_team = VoiceTeamContract.model_validate(_load_json(layout.tts_voice_team_path))
    script = DubbingScript.model_validate(_load_json(script_path))
    source_error = _script_source_mismatch(layout, chapter_number, script)
    if source_error is not None:
        return ExecutionResult(project_id=project_id, result=source_error)
    audit_error = _script_source_audit_error(layout, chapter_number, script)
    if audit_error is not None:
        return ExecutionResult(project_id=project_id, result=audit_error)
    if segment_index in unresolved_speaker_indices(script):
        return ExecutionResult(
            project_id=project_id,
            result={
                "error": "该对白的说话人尚未通过正文审核；请先在配音脚本编辑器中指定角色。",
                "error_code": "tts_speaker_review_required",
                "segment_indices": [segment_index],
            },
        )
    segment = next(
        (item for item in script.segments if item.segment_index == segment_index),
        None,
    )
    if segment is None:
        return ExecutionResult(
            project_id=project_id,
            result={"error": f"配音脚本中不存在第 {segment_index + 1} 段。"},
        )
    if segment_override is not None:
        try:
            candidate_segment = DubbingSegment.model_validate(segment_override)
        except Exception as exc:
            return ExecutionResult(
                project_id=project_id,
                result={"error": f"试听指导无法读取：{exc}"},
            )
        if candidate_segment.segment_index != segment_index:
            return ExecutionResult(
                project_id=project_id,
                result={"error": "试听指导与当前片段不匹配。"},
            )
        # Source text/identity are authoritative until the author accepts a
        # take. Voice Room may edit performance wording and direction only.
        segment = candidate_segment.model_copy(
            update={
                "text": segment.text,
                "character_id": segment.character_id,
                "character_name": segment.character_name,
                "segment_type": segment.segment_type,
                "source_paragraph": segment.source_paragraph,
            }
        )
    if segment.segment_type not in {
        SegmentType.NARRATION,
        SegmentType.DIALOGUE,
        SegmentType.INNER_THOUGHT,
    }:
        return ExecutionResult(
            project_id=project_id,
            result={"error": "环境声、BGM 与静音段请在后处理阶段调整，不支持语音重录。"},
        )

    provider_enum = _resolve_provider(settings, provider)
    await _ensure_managed_tts_runtime(settings, provider_enum)
    narrator_profile: NarratorVoiceProfile | None = None
    narrator_voice_id = ""
    if layout.tts_narrator_profile_path.exists():
        try:
            narrator_profile = NarratorVoiceProfile.model_validate(
                _load_json(layout.tts_narrator_profile_path)
            )
            if narrator_profile.is_expired or narrator_profile.provider != provider_enum:
                narrator_profile = None
            else:
                narrator_voice_id = narrator_profile.voice_id
        except Exception as exc:
            _log.warning("Failed to load narrator profile for segment audition: %s", exc)

    registry = TTSAdapterRegistry.get_instance(settings)
    single_segment_script = script.model_copy(update={"segments": [segment.model_copy(deep=True)]})
    synth_step = SynthesizeAudioStep(registry, settings=settings, layout=layout)
    take_id = uuid4().hex[:16]
    candidate_dir = layout.tts_candidate_take_dir(chapter_number, take_id)
    segment_results = await synth_step.run(
        SynthesizeAudioInput(
            chapter_number=chapter_number,
            script=single_segment_script,
            voice_team=voice_team,
            provider=provider_enum,
            output_dir=candidate_dir,
            max_concurrent=1,
            retry_limit=settings.tts_synthesis_retry_limit,
            narrator_voice_id=narrator_voice_id or settings.tts_narrator_voice_id,
            narrator_profile=narrator_profile,
            voice_team_hash=_voice_team_content_hash(voice_team),
            script_hash=script.script_hash or compute_dubbing_script_hash(script),
            persist_progress=False,
            context_script=script,
        )
    )
    segment_result = segment_results[0]
    if segment_result.status != SynthesisStatus.COMPLETED:
        # Failed auditions are never registered in the take manifest. Remove
        # their private working directory so invisible partial files do not
        # accumulate outside the normal review/cleanup lifecycle.
        shutil.rmtree(candidate_dir, ignore_errors=True)
        return ExecutionResult(
            project_id=project_id,
            result={
                "error": f"第 {segment_index + 1} 段合成失败：{segment_result.error_message or '未知错误'}",
                "segment": segment.model_dump(mode="json"),
                "segment_result": segment_result.model_dump(mode="json"),
            },
        )

    script_hash = script.script_hash or compute_dubbing_script_hash(script)
    take = SegmentTakeVersion(
        take_id=take_id,
        chapter_number=chapter_number,
        segment_index=segment_index,
        status=TakeReviewStatus.CANDIDATE,
        segment=segment,
        segment_result=segment_result,
        source_script_hash=script_hash,
        voice_team_hash=_voice_team_content_hash(voice_team),
    )
    VoiceStudioProjectService(layout, project_id=project_id).register_candidate_take(take)
    return ExecutionResult(
        project_id=project_id,
        result={
            "chapter_number": chapter_number,
            "take_id": take_id,
            "take_status": TakeReviewStatus.CANDIDATE.value,
            "segment": segment.model_dump(mode="json"),
            "segment_result": segment_result.model_dump(mode="json"),
            "assembly_stale": False,
            "requires_acceptance": True,
        },
    )


@_with_tts_project_lock
async def execute_accept_segment_take(
    *,
    project_id: str,
    chapter_number: int,
    take_id: str,
    settings: Settings,
    layout: ProjectLayout,
) -> ExecutionResult[dict[str, Any]]:
    """Promote one reviewed audition into the formal segment manifest."""
    del settings
    service = VoiceStudioProjectService(layout, project_id=project_id)
    manifest = service.load_take_manifest(chapter_number)
    take = next((item for item in manifest.takes if item.take_id == take_id), None)
    if take is None or take.status != TakeReviewStatus.CANDIDATE:
        return ExecutionResult(
            project_id=project_id,
            result={"error": "该试听版本不存在或已被处理。"},
        )
    script_path = layout.tts_dubbing_script_path(chapter_number)
    if not script_path.is_file():
        return ExecutionResult(project_id=project_id, result={"error": "源配音脚本已不存在。"})
    script = DubbingScript.model_validate(_load_json(script_path))
    current_hash = script.script_hash or compute_dubbing_script_hash(script)
    if take.source_script_hash != current_hash:
        return ExecutionResult(
            project_id=project_id,
            result={"error": "配音脚本已变更，该试听不能再接受，请重新生成。"},
        )
    source_audio = Path(take.segment_result.audio_path)
    if not source_audio.is_file():
        return ExecutionResult(project_id=project_id, result={"error": "试听音频文件已丢失。"})

    approved_dir = layout.tts_approved_take_dir(chapter_number)
    approved_dir.mkdir(parents=True, exist_ok=True)
    approved_path = approved_dir / (
        f"seg_{take.segment_index:04d}_{take.take_id}{source_audio.suffix.lower()}"
    )
    temp_path = approved_path.with_name(f".{approved_path.name}.tmp")
    shutil.copy2(source_audio, temp_path)
    temp_path.replace(approved_path)
    promoted_result = take.segment_result.model_copy(update={"audio_path": str(approved_path)})

    updated_segments = [
        take.segment.model_copy(deep=True)
        if item.segment_index == take.segment_index
        else item.model_copy(deep=True)
        for item in script.segments
    ]
    updated_script = script.model_copy(update={"segments": updated_segments})
    updated_script.script_hash = compute_dubbing_script_hash(updated_script)
    atomic_write_json(script_path, updated_script.model_dump(mode="json"))

    result_path = layout.tts_audio_result_path(chapter_number)
    previous: ChapterAudioResult | None = None
    if result_path.is_file():
        try:
            previous = ChapterAudioResult.model_validate(_load_json(result_path))
        except Exception as exc:
            _log.warning("Ignoring unreadable formal audio result while accepting take: %s", exc)
    results_by_index = {
        item.segment_index: item for item in (previous.segment_results if previous else [])
    }
    results_by_index[take.segment_index] = promoted_result
    metadata = dict(previous.metadata) if previous else {}
    accepted_take_ids = dict(metadata.get("accepted_take_ids") or {})
    accepted_take_ids[str(take.segment_index)] = take.take_id
    metadata.update(
        {
            "assembly_stale": True,
            "assembly_stale_reason": (
                f"第 {take.segment_index + 1} 段已接受新试听，请重新装配全章。"
            ),
            "accepted_take_ids": accepted_take_ids,
            "script_hash": updated_script.script_hash,
        }
    )
    if previous is not None:
        formal_result = previous.model_copy(
            update={
                "script": updated_script,
                "segment_results": sorted(
                    results_by_index.values(), key=lambda item: item.segment_index
                ),
                "is_complete": False,
                "delivery_ready": False,
                "delivery_blocking_reasons": ["incomplete_synthesis", "assembly_stale"],
                "mix_render_report": None,
                "metadata": metadata,
            }
        )
    else:
        formal_result = ChapterAudioResult(
            chapter_number=chapter_number,
            script=updated_script,
            segment_results=[promoted_result],
            is_complete=False,
            metadata=metadata,
        )
    result_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(result_path, formal_result.model_dump(mode="json"))
    service.accept_candidate_take(
        chapter_number,
        take.take_id,
        promoted_result=promoted_result,
        new_script_hash=updated_script.script_hash,
    )
    # The approved copy is now canonical. Retire the isolated candidate only
    # after the script, formal result and take manifest are safely persisted.
    shutil.rmtree(layout.tts_candidate_take_dir(chapter_number, take.take_id), ignore_errors=True)
    return ExecutionResult(
        project_id=project_id,
        result={
            "chapter_number": chapter_number,
            "take_id": take.take_id,
            "take_status": TakeReviewStatus.ACCEPTED.value,
            "segment": take.segment.model_dump(mode="json"),
            "segment_result": promoted_result.model_dump(mode="json"),
            "audio_result": formal_result.model_dump(mode="json"),
            "assembly_stale": True,
        },
    )
