"""
Dubbing-script executions and source-audit helpers.

Extracted from execution.py: save/generate/resolve-speakers for dubbing
scripts, script-source audit gates and audio-result hash helpers.
Depends only on execution_shared.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from novel_forge.core.config import Settings
from novel_forge.obs.logger import get_logger
from novel_forge.persistence.filesystem import (
    atomic_write_json,
)
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.project_staleness import (
    invalidate_chapter_tts_artifacts,
)
from novel_forge.tts.creative_direction import load_or_create_audio_creative_bible
from novel_forge.tts.gateway.factory import resolve_tts_model
from novel_forge.tts.pipeline.generate_script_step import (
    GenerateDubbingScriptInput,
    GenerateDubbingScriptStep,
)
from novel_forge.tts.pipeline.pause_marker_injection import (
    inject_pause_markers,
    retarget_native_synthesis_annotations,
)
from novel_forge.tts.platform.config import build_audio_execution_plan
from novel_forge.tts.platform.schemas import AudioExecutionStage
from novel_forge.tts.schemas import (
    ChapterTTSMetadata,
    DubbingScript,
    DubbingSegment,
    DubbingStyleProfile,
    EmotionTag,
    NarratorVoiceProfile,
    SegmentType,
    VoiceTeamContract,
)
from novel_forge.tts.script_integrity import (
    compute_dubbing_script_hash,
    refresh_segment_uid,
    requires_script_source_audit,
    source_text_hash_matches,
    unresolved_speaker_indices,
)
from novel_forge.tts.style_reference import analyze_dubbing_style_reference
from novel_forge.workspace.execution_result import ExecutionResult
from novel_forge.workspace.publication import build_chapter_publication_view
from novel_forge.workspace.tts_ops.execution_shared import (
    _emit_tts_progress,
    _load_json,
    _load_tts_upstream_context,
    _resolve_provider,
    _source_text_hash,
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

def tts_artifact_source_mismatch(
    layout: ProjectLayout,
    chapter_number: int,
    artifact_source_hash: str,
    *,
    artifact_name: str = "TTS artifact",
    error_code: str = "stale_tts_artifact",
) -> dict[str, Any] | None:
    """Return a structured freshness error for scripts, audio manifests and exports."""
    chapter_path = layout.chapter_path(chapter_number)
    if not chapter_path.exists():
        return None
    try:
        publication = build_chapter_publication_view(
            layout,
            layout.root.name,
            chapter_number,
        )
    except (FileNotFoundError, OSError, ValueError):
        publication = None
    if publication is not None and not publication.deliverable:
        return {
            "error": (
                f"Final chapter {chapter_number} has a pending novel revision and cannot be "
                "used for derived-media delivery."
            ),
            "error_code": "novel_publication_blocked",
            "expected_source_text_hash": publication.final_text_hash,
            "blocking_reasons": publication.blocking_reasons,
        }
    try:
        source_text = chapter_path.read_text(encoding="utf-8")
        current_hash = _source_text_hash(source_text)
    except OSError as exc:
        return {
            "error": f"Unable to read final chapter {chapter_number}: {exc}",
            "error_code": "chapter_text_unreadable",
        }

    stored_hash = str(artifact_source_hash or "").strip()
    if source_text_hash_matches(stored_hash, source_text):
        return None
    return {
        "error": (
            f"{artifact_name} for chapter {chapter_number} is stale. "
            "Regenerate it from the current final chapter."
        ),
        "error_code": error_code,
        "expected_source_text_hash": current_hash,
        "artifact_source_text_hash": stored_hash,
    }


def tts_audio_result_source_hash(payload: dict[str, Any]) -> str:
    """Read the source hash from current and legacy ChapterAudioResult payloads."""
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        source_hash = str(metadata.get("source_text_hash") or "").strip()
        if source_hash:
            return source_hash
    script = payload.get("script")
    if isinstance(script, dict):
        return str(script.get("source_text_hash") or "").strip()
    return ""


def tts_audio_result_script_hash(payload: dict[str, Any]) -> str:
    """Read or reconstruct the script identity attached to chapter audio."""

    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        script_hash = str(metadata.get("script_hash") or "").strip()
        if script_hash:
            return script_hash
    script_payload = payload.get("script")
    if not isinstance(script_payload, dict):
        return ""
    try:
        script = DubbingScript.model_validate(script_payload)
    except (TypeError, ValueError):
        return ""
    return script.script_hash or compute_dubbing_script_hash(script)


def tts_audio_result_script_mismatch(
    layout: ProjectLayout,
    chapter_number: int,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Reject audio derivatives from a different persisted script generation.

    A deliberately cleared source script does not invalidate a delivery-ready
    result: the embedded script remains its audit snapshot.  When a current
    script file exists, however, it is authoritative for assembly and export.
    """

    script_path = layout.tts_dubbing_script_path(chapter_number)
    if not script_path.is_file():
        return None
    try:
        current_script = DubbingScript.model_validate(_load_json(script_path))
    except (OSError, TypeError, ValueError) as exc:
        return {
            "error": f"Unable to read current dubbing script for chapter {chapter_number}: {exc}",
            "error_code": "dubbing_script_unreadable",
        }
    expected_hash = current_script.script_hash or compute_dubbing_script_hash(current_script)
    artifact_hash = tts_audio_result_script_hash(payload)
    if artifact_hash and artifact_hash == expected_hash:
        return None
    return {
        "error": (
            f"Chapter audio for chapter {chapter_number} belongs to another dubbing script. "
            "Regenerate audio from the current script."
        ),
        "error_code": "stale_tts_audio_result",
        "expected_script_hash": expected_hash,
        "artifact_script_hash": artifact_hash,
    }


def _authoritative_text_mismatch(
    layout: ProjectLayout,
    chapter_number: int,
    candidate_text: str,
) -> dict[str, Any] | None:
    """Return a structured error when input text is not the persisted final chapter."""
    chapter_path = layout.chapter_path(chapter_number)
    if not chapter_path.exists():
        return None
    try:
        publication = build_chapter_publication_view(
            layout,
            layout.root.name,
            chapter_number,
        )
    except (FileNotFoundError, OSError, ValueError):
        publication = None
    if publication is not None and not publication.deliverable:
        return {
            "error": f"Chapter {chapter_number} is awaiting final novel verification.",
            "error_code": "novel_publication_blocked",
            "expected_source_text_hash": publication.final_text_hash,
            "blocking_reasons": publication.blocking_reasons,
        }
    try:
        authoritative_text = chapter_path.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "error": f"Unable to read final chapter {chapter_number}: {exc}",
            "error_code": "chapter_text_unreadable",
        }

    expected_hash = _source_text_hash(authoritative_text)
    candidate_hash = _source_text_hash(candidate_text)
    if expected_hash == candidate_hash:
        return None
    return {
        "error": (
            f"Chapter {chapter_number} text does not match the current final chapter. "
            "Reload the chapter and regenerate the dubbing script."
        ),
        "error_code": "stale_chapter_text",
        "expected_source_text_hash": expected_hash,
        "provided_source_text_hash": candidate_hash,
    }


def _script_source_mismatch(
    layout: ProjectLayout,
    chapter_number: int,
    script: DubbingScript,
) -> dict[str, Any] | None:
    """Return a structured error when a script no longer matches the final text."""
    script_hash = str(script.source_text_hash or "").strip()
    mismatch = tts_artifact_source_mismatch(
        layout,
        chapter_number,
        script_hash,
        artifact_name="Dubbing script",
        error_code="stale_dubbing_script",
    )
    if mismatch is not None:
        mismatch["script_source_text_hash"] = script_hash
    return mismatch


def _script_source_audit_error(
    layout: ProjectLayout,
    chapter_number: int,
    script: DubbingScript,
) -> dict[str, Any] | None:
    """Require legacy quote-bearing scripts to pass the current source audit."""
    chapter_path = layout.chapter_path(chapter_number)
    if not chapter_path.is_file():
        return None
    try:
        source_text = chapter_path.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "error": f"Unable to read final chapter {chapter_number}: {exc}",
            "error_code": "chapter_text_unreadable",
        }
    if not requires_script_source_audit(script, source_text):
        return None
    return {
        "error": (
            "当前配音脚本生成于正文对齐审核机制启用之前；"
            "请重新生成脚本，避免把字样、数字或题名误当成角色对白。"
        ),
        "error_code": "tts_script_source_audit_required",
    }


@_with_tts_project_lock
async def execute_save_dubbing_script(
    *,
    project_id: str,
    chapter_number: int,
    edits: list[dict[str, Any]],
    layout: ProjectLayout,
) -> ExecutionResult[dict[str, Any]]:
    """Persist user-reviewed script edits and retire stale audio derivatives."""
    script_path = layout.tts_dubbing_script_path(chapter_number)
    if not script_path.exists():
        return ExecutionResult(project_id=project_id, result={"error": "配音脚本不存在"})
    try:
        script = DubbingScript.model_validate(_load_json(script_path))
    except (OSError, ValueError) as exc:
        return ExecutionResult(project_id=project_id, result={"error": f"配音脚本读取失败：{exc}"})
    if script.chapter_number != chapter_number:
        return ExecutionResult(project_id=project_id, result={"error": "配音脚本章节与请求不一致"})

    edit_by_index = {
        int(edit.get("segment_index", -1)): edit
        for edit in edits
        if int(edit.get("segment_index", -1)) >= 0
    }
    emotion_aliases = {
        "中性": EmotionTag.NEUTRAL,
        "平静": EmotionTag.NEUTRAL,
        "克制": EmotionTag.NEUTRAL,
        "低语": EmotionTag.WHISPER,
        "警觉": EmotionTag.ANXIOUS,
        "哀伤": EmotionTag.SAD,
        "悲伤": EmotionTag.SAD,
        "愤怒": EmotionTag.ANGRY,
        "温柔": EmotionTag.TENDER,
    }
    updated_segments: list[DubbingSegment] = []
    changed_indices: list[int] = []
    for segment in script.segments:
        edit = edit_by_index.get(segment.segment_index)
        if edit is None:
            updated_segments.append(segment)
            continue
        content = str(edit.get("content") or "").strip()
        if not content:
            return ExecutionResult(
                project_id=project_id,
                result={"error": f"第 {segment.segment_index + 1} 段文本不能为空"},
            )
        emotion_label = str(edit.get("emotion_label") or "").strip()
        try:
            emotion = EmotionTag(emotion_label)
        except ValueError:
            emotion = emotion_aliases.get(emotion_label, segment.emotion)
        updates: dict[str, Any] = {
            "text": content,
            "character_id": str(edit.get("speaker_id") or ""),
            "character_name": str(edit.get("speaker_label") or ""),
            "emotion": emotion,
            "tone_hint": str(edit.get("tone_hint") or ""),
            "stress_words": [
                str(value).strip()
                for value in edit.get("stress_words", [])
                if str(value).strip()
            ],
            "narrator_distance": str(edit.get("narrator_distance") or ""),
            "pronunciation_overrides": [
                str(value).strip()
                for value in edit.get("pronunciation_overrides", [])
                if str(value).strip()
            ],
            "language_code": str(edit.get("language_code") or "auto"),
        }
        if edit.get("segment_type"):
            try:
                updates["segment_type"] = SegmentType(str(edit["segment_type"]))
            except ValueError:
                pass
        if edit.get("emotion_intensity") is not None:
            updates["emotion_intensity"] = max(
                0.0,
                min(1.0, float(edit["emotion_intensity"])),
            )
        for field_name in ("speed_override", "volume_override", "pitch_override"):
            if field_name in edit:
                updates[field_name] = edit[field_name]
        updated = refresh_segment_uid(segment.model_copy(update=updates))
        updated_segments.append(updated)
        if updated != segment:
            changed_indices.append(segment.segment_index)

    metadata = {
        **script.metadata,
        "manual_edit": {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "changed_segment_indices": changed_indices,
        },
    }
    updated_script = script.model_copy(update={"segments": updated_segments, "metadata": metadata})
    updated_script.script_hash = compute_dubbing_script_hash(updated_script)
    atomic_write_json(script_path, updated_script.model_dump(mode="json"))
    removed = invalidate_chapter_tts_artifacts(
        layout,
        chapter_number,
        include_metadata=False,
        include_script=False,
    )
    return ExecutionResult(
        project_id=project_id,
        result={
            "chapter_number": chapter_number,
            "script_hash": updated_script.script_hash,
            "changed_segment_indices": changed_indices,
            "invalidated_artifacts": [str(path) for path in removed],
        },
    )


async def _analyze_and_persist_dubbing_style_reference(
    *,
    reference_script_text: str,
    reference_script_name: str,
    settings: Settings,
    layout: ProjectLayout,
    router: Any,
    builder: Any,
    on_step_progress: Any = None,
) -> DubbingStyleProfile:
    profile = await analyze_dubbing_style_reference(
        reference_script_text,
        source_name=reference_script_name or "用户参考配音脚本",
        router=router,
        builder=builder,
        settings=settings,
        on_step=on_step_progress,
    )
    atomic_write_json(
        layout.tts_dubbing_style_profile_path,
        profile.model_dump(mode="json"),
    )
    return profile


@_with_tts_project_lock
async def execute_analyze_dubbing_style_reference(
    *,
    project_id: str,
    reference_script_text: str,
    reference_script_name: str,
    settings: Settings,
    layout: ProjectLayout,
    on_step_progress: Any = None,
) -> ExecutionResult[dict[str, Any]]:
    """Analyze a transient uploaded script and persist only its abstract style profile."""

    from novel_forge.gateway.factory import ModelRouterBuilder
    from novel_forge.prompts.builder import PromptBuilder

    layout.ensure_dirs()
    profile = await _analyze_and_persist_dubbing_style_reference(
        reference_script_text=reference_script_text,
        reference_script_name=reference_script_name,
        settings=settings,
        layout=layout,
        router=ModelRouterBuilder(settings).build(),
        builder=PromptBuilder(),
        on_step_progress=on_step_progress,
    )
    return ExecutionResult(
        project_id=project_id,
        result={
            "profile": profile.model_dump(mode="json"),
            "message": f"已建立参考配音风格画像：{profile.source_name}",
        },
    )


@_with_tts_project_lock
async def execute_generate_dubbing_script(
    *,
    project_id: str,
    chapter_number: int,
    chapter_text: str,
    settings: Settings,
    layout: ProjectLayout,
    character_voices: list[dict[str, Any]] | None = None,
    style_profile: dict[str, Any] | None = None,
    scene_context: dict[str, Any] | None = None,
    tts_metadata: ChapterTTSMetadata | None = None,
    scene_intents: list[dict[str, Any]] | None = None,
    provider: str = "",
    reference_script_text: str = "",
    reference_script_name: str = "",
    reference_style_strength: float | None = None,
    on_step_progress: Any = None,
) -> ExecutionResult[dict[str, Any]]:
    """Generate dubbing script for a chapter.

    Uses LLM to convert chapter text into structured dubbing script
    with rich annotation (emotion, paralinguistic tags, scene transitions, etc.).
    Integrates upstream pipeline outputs for context-aware generation.

    Args:
        project_id: Project identifier.
        chapter_number: Chapter number.
        chapter_text: Chapter text content.
        settings: Application settings.
        layout: Project layout for storage paths.
        character_voices: Editorial contract character voices (optional).
        style_profile: Style profile dict (optional, loaded from disk if not provided).
        scene_context: Scene context dict (location, time_frame, atmosphere, intent).
        on_step_progress: Optional progress callback.

    Returns:
        ExecutionResult with DubbingScript dict.
    """
    _log.info("Generating dubbing script for chapter %d", chapter_number)

    text_error = _authoritative_text_mismatch(layout, chapter_number, chapter_text)
    if text_error is not None:
        return ExecutionResult(project_id=project_id, result=text_error)

    # Load voice team
    if not layout.tts_voice_team_path.exists():
        return ExecutionResult(
            project_id=project_id,
            result={"error": "Voice team not found. Run build_voice_team first."},
        )

    team_data = _load_json(layout.tts_voice_team_path)
    voice_team = VoiceTeamContract.model_validate(team_data)
    upstream = _load_tts_upstream_context(layout, chapter_number)

    # Load narrator profile if available
    narrator_profile: NarratorVoiceProfile | None = None
    if layout.tts_narrator_profile_path.exists():
        try:
            profile_data = _load_json(layout.tts_narrator_profile_path)
            narrator_profile = NarratorVoiceProfile.model_validate(profile_data)
        except Exception as exc:
            _log.warning("Failed to load narrator profile: %s", exc)

    # Load style_profile from disk if not provided
    if style_profile is None:
        style_profile = upstream["style_profile"]

    # Load character_voices from editorial contract if not provided
    if character_voices is None:
        character_voices = upstream["character_voices"]
    if scene_intents is None:
        scene_intents = upstream["scene_intents"]
    if scene_context is None:
        scene_context = upstream["scene_context"]
    if tts_metadata is None:
        tts_metadata = upstream["tts_metadata"]
    audio_creative_bible = load_or_create_audio_creative_bible(
        layout=layout,
        project_id=project_id,
        story_context=upstream["story_sound_context"],
        style_profile=style_profile,
        scene_intents=scene_intents,
        outline=upstream["outline"],
    )

    # Create step with LLM support
    from novel_forge.gateway.factory import ModelRouterBuilder
    from novel_forge.prompts.builder import PromptBuilder

    router = ModelRouterBuilder(settings).build()
    builder = PromptBuilder()
    selected_provider = _resolve_provider(settings, provider)
    target_model = resolve_tts_model(settings, selected_provider, purpose="formal")

    style_strength = (
        float(reference_style_strength)
        if reference_style_strength is not None
        else float(getattr(settings, "tts_style_reference_default_strength", 0.65))
    )
    style_strength = max(0.0, min(1.0, style_strength))
    reference_style_profile: DubbingStyleProfile | None = None
    if reference_script_text.strip():
        reference_style_profile = await _analyze_and_persist_dubbing_style_reference(
            reference_script_text=reference_script_text,
            reference_script_name=reference_script_name,
            settings=settings,
            layout=layout,
            router=router,
            builder=builder,
            on_step_progress=on_step_progress,
        )
    elif style_strength > 0.0 and layout.tts_dubbing_style_profile_path.is_file():
        try:
            reference_style_profile = DubbingStyleProfile.model_validate(
                _load_json(layout.tts_dubbing_style_profile_path)
            )
        except ValueError as exc:
            _log.warning("Ignoring invalid persisted dubbing style profile: %s", exc)

    _emit_tts_progress(
        on_step_progress,
        "tts_script_start",
        {"chapter": chapter_number, "source_chars": len(chapter_text)},
    )
    _emit_tts_progress(on_step_progress, "tts_script_llm_call", {"chapter": chapter_number})
    step = GenerateDubbingScriptStep(
        router=router,
        builder=builder,
        settings=settings,
        on_step=on_step_progress,
    )

    # Build input with upstream data
    # Load project sound library assets for BGM library-first matching.
    from novel_forge.tts.assets.sound_library import load_sound_library

    _sound_library = load_sound_library(layout)
    _library_assets = [
        asset for asset in _sound_library.assets if asset.approval_status == "approved"
    ]

    input_data = GenerateDubbingScriptInput(
        chapter_number=chapter_number,
        chapter_text=chapter_text,
        voice_team=voice_team,
        character_voices=character_voices or [],
        style_profile=style_profile or {},
        scene_context=scene_context or {},
        story_context=upstream["story_sound_context"],
        narrator_profile=narrator_profile,
        audio_creative_bible=audio_creative_bible,
        tts_metadata=tts_metadata,
        scene_intents=scene_intents,
        library_assets=_library_assets,
        location_acoustics=upstream["location_acoustics"],
        upstream_revision=upstream["upstream_revision"],
        reference_style_profile=reference_style_profile,
        reference_style_strength=style_strength,
        target_provider=selected_provider.value,
        target_model=target_model,
    )

    # Execute
    from novel_forge.tts.pipeline.script_phases import ScriptCompletenessError

    try:
        script = await step.run(input_data)
    except ScriptCompletenessError as exc:
        _log.warning(
            "Dubbing script completeness gate failed for chapter %d: %s",
            chapter_number,
            exc,
        )
        _emit_tts_progress(
            on_step_progress,
            "tts_script_completeness_failed",
            {
                "chapter": chapter_number,
                "failures": exc.report.failures,
                "spoken_text_coverage": exc.report.spoken_text_coverage,
                "emotion_differentiation": exc.report.emotion_differentiation,
                "voice_assignment_coverage": exc.report.voice_assignment_coverage,
            },
        )

        # ── Human adjudication degradation path ────────────────────────────
        # Save the failed script as a draft so the user can review, edit,
        # and manually accept it in Voice Studio.
        draft_path = layout.tts_dir / "drafts" / f"chapter_{chapter_number:03d}_gate_failed.json"
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Reconstruct the script from the exception context if available.
            # The orchestrator attaches the last-known script to the error.
            failed_script = getattr(exc, "script", None)
            if failed_script is not None:
                draft_data = failed_script.model_dump(mode="json")
            else:
                draft_data = {
                    "chapter_number": chapter_number,
                    "segments": [],
                    "metadata": {"gate_failure": True},
                }
            draft_data.setdefault("metadata", {})
            draft_data["metadata"]["completeness_gate"] = {
                "passed": False,
                "failures": exc.report.failures,
                "spoken_text_coverage": exc.report.spoken_text_coverage,
                "emotion_differentiation": exc.report.emotion_differentiation,
                "voice_assignment_coverage": exc.report.voice_assignment_coverage,
            }
            atomic_write_json(draft_path, draft_data)
            _log.info(
                "Failed script saved as draft for human review: %s", draft_path
            )
        except Exception as draft_exc:
            _log.warning("Failed to save draft script: %s", draft_exc)
            draft_path = None  # type: ignore[assignment]

        return ExecutionResult(
            project_id=project_id,
            result={
                "error": "配音脚本未通过完整度门禁，不能落盘为正式成品。",
                "error_code": "tts_script_incomplete",
                "action_required": "human_review",
                "completeness_report": {
                    "passed": exc.report.passed,
                    "spoken_text_coverage": exc.report.spoken_text_coverage,
                    "emotion_differentiation": exc.report.emotion_differentiation,
                    "voice_assignment_coverage": exc.report.voice_assignment_coverage,
                    "failures": exc.report.failures,
                },
                "draft_script_path": str(draft_path) if draft_path else None,
                "user_options": [
                    "在配音脚本编辑器中打开草稿，手动修正后重新提交",
                    "重新生成配音脚本（调整上游情绪标注后）",
                    "降低完整度门禁阈值后重试",
                ],
                "source_diagnostics": {
                    "hint": "请检查以下源头步骤是否正常运行",
                    "spoken_text_rewrite": "Phase 5 口语改写是否成功执行",
                    "emotion_review": "Phase 4b LLM 审校是否修正了情绪",
                    "speaker_adjudication": "Phase 2 说话人裁决是否完成",
                    "emotion_repair": "情绪修复是否已尝试（查看 emotion_repair 元数据）",
                },
            },
        )
    script_languages = list(
        dict.fromkeys(
            run.language
            for segment in script.segments
            for run in segment.language_runs
            if run.language and run.language != "auto"
        )
    ) or ["zh"]
    # Freeze the same provider the user selected for rewrite guidance.  Without
    # this projection, script generation could annotate for MiniMax while the
    # persisted plan silently routed formal synthesis back to the global
    # default (or vice versa).
    execution_plan_settings = settings.model_copy(
        update={"tts_default_provider": selected_provider.value}
    )
    execution_plan = build_audio_execution_plan(
        execution_plan_settings,
        languages=script_languages,
        scorecard_path=layout.tts_model_scorecards_path,
        project_id=project_id,
    )
    formal_route = execution_plan.assignment_for(AudioExecutionStage.TTS_FORMAL)
    formal_target = formal_route.primary if formal_route is not None else None
    if formal_target is not None:
        target_provider = formal_target.provider_id.strip().lower().replace("-", "_")
        target_model_id = formal_target.model_id or settings.tts_default_model
        pause_metadata = script.metadata.get("pause_marker_injection")
        previous_platform = (
            str(pause_metadata.get("platform") or "")
            if isinstance(pause_metadata, dict)
            else ""
        )
        previous_model_id = (
            str(pause_metadata.get("model_id") or "")
            if isinstance(pause_metadata, dict)
            else ""
        )
        marker_platforms = getattr(settings, "tts_pause_marker_platforms", None) or ["minimax"]
        markers_enabled = bool(getattr(settings, "tts_pause_marker_enabled", True))
        if markers_enabled:
            target_changed = (
                previous_platform.strip().lower().replace("-", "_") != target_provider
                or (
                    bool(previous_model_id)
                    and previous_model_id.strip().lower() != target_model_id.strip().lower()
                )
            )
            if previous_platform and target_changed:
                script = retarget_native_synthesis_annotations(
                    script,
                    previous_platform=previous_platform,
                    previous_model_id=previous_model_id,
                    platform=target_provider,
                    platforms=marker_platforms,
                    model_id=target_model_id,
                    onomatopoeia_enabled=bool(
                        getattr(settings, "tts_onomatopoeia_enabled", True)
                    ),
                    onomatopoeia_min_intensity=float(
                        getattr(settings, "tts_onomatopoeia_min_intensity", 0.6)
                    ),
                )
            elif not previous_platform:
                script = inject_pause_markers(
                    script,
                    platform=target_provider,
                    platforms=marker_platforms,
                    model_id=target_model_id,
                    onomatopoeia_enabled=bool(
                        getattr(settings, "tts_onomatopoeia_enabled", True)
                    ),
                    onomatopoeia_min_intensity=float(
                        getattr(settings, "tts_onomatopoeia_min_intensity", 0.6)
                    ),
                )
        script = script.model_copy(
            update={
                "metadata": {
                    **script.metadata,
                    "tts_native_synthesis_target": {
                        "provider": target_provider,
                        "model_id": target_model_id,
                        "execution_plan_id": execution_plan.plan_id,
                    },
                }
            }
        )
    execution_plan_path = layout.tts_execution_plan_path
    execution_plan_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(execution_plan_path, execution_plan.model_dump(mode="json"))
    script = script.model_copy(
        update={
            "metadata": {
                **script.metadata,
                "audio_execution_plan_path": str(execution_plan_path),
                "audio_creative_bible_path": str(layout.tts_audio_creative_bible_path),
                "audio_creative_bible_fingerprint": (audio_creative_bible.source_fingerprint),
                "audio_quality_preset": execution_plan.preset.value,
                "script_provenance": {
                    "generator_revision": 2,
                    "source_text_hash": script.source_text_hash,
                    "generated_at": script.created_at.isoformat(),
                    "review_stages": [
                        "source_reconciliation",
                        "speaker_adjudication",
                        "professional_script_review",
                    ],
                },
            }
        }
    )
    # The workspace adds model-plan metadata after the pipeline step returns;
    # persist the canonical hash of the final script object, not the earlier
    # pre-enrichment hash.
    script.script_hash = compute_dubbing_script_hash(script)
    _emit_tts_progress(
        on_step_progress,
        "tts_script_done",
        {"chapter": chapter_number, "segment_count": len(script.segments)},
    )

    # Persist the new authority before invalidating any derivatives.  A failed
    # disk write must leave the previous script and its playable audio intact.
    script_path = layout.tts_dubbing_script_path(chapter_number)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(script_path, script.model_dump(mode="json"))
    # A successfully replaced script invalidates every synthesized derivative,
    # while the current chapter metadata remains a valid input to this script.
    invalidate_chapter_tts_artifacts(
        layout,
        chapter_number,
        include_metadata=False,
        include_script=False,
    )
    _emit_tts_progress(
        on_step_progress,
        "tts_script_persisted",
        {
            "chapter": chapter_number,
            "segment_count": len(script.segments),
            "script_path": str(script_path),
        },
    )

    _log.info(
        "Dubbing script generated: %d segments, %d bgm, %d sfx, %d transitions",
        len(script.segments),
        len(script.bgm_suggestions),
        len(script.sfx_cues),
        len(script.scene_transitions),
    )

    return ExecutionResult(
        project_id=project_id,
        result=script.model_dump(mode="json"),
    )


def _coerce_nonnegative_int(value: Any) -> int | None:
    """Return a durable non-negative index without raising on legacy metadata."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


@_with_tts_project_lock
async def execute_resolve_dubbing_speakers(
    *,
    project_id: str,
    chapter_number: int,
    resolutions: list[dict[str, Any]],
    layout: ProjectLayout,
) -> ExecutionResult[dict[str, Any]]:
    """Persist human speaker decisions against durable segment identities."""
    script_path = layout.tts_dubbing_script_path(chapter_number)
    if not script_path.is_file():
        return ExecutionResult(
            project_id=project_id,
            result={
                "error": "当前章节没有可复核的配音脚本。",
                "error_code": "tts_script_missing",
            },
        )
    try:
        script = DubbingScript.model_validate(_load_json(script_path))
    except (OSError, TypeError, ValueError) as exc:
        return ExecutionResult(
            project_id=project_id,
            result={
                "error": f"配音脚本无法读取：{exc}",
                "error_code": "tts_script_unreadable",
            },
        )
    mismatch = _script_source_mismatch(layout, chapter_number, script)
    if mismatch is not None:
        return ExecutionResult(project_id=project_id, result=mismatch)

    segments = list(script.segments)
    positions = {
        segment.segment_index: position for position, segment in enumerate(segments)
    }
    if len(positions) != len(segments):
        return ExecutionResult(
            project_id=project_id,
            result={
                "error": "配音脚本存在重复片段 ID，无法安全应用复核结果。",
                "error_code": "duplicate_tts_segment_ids",
            },
        )

    voice_team_payload = _load_json(layout.tts_voice_team_path)
    try:
        voice_team = VoiceTeamContract.model_validate(voice_team_payload)
    except (TypeError, ValueError):
        voice_team = VoiceTeamContract(entries=[])
    cast_by_id = {
        entry.character_id: entry
        for entry in voice_team.entries
        if entry.character_id
    }

    normalized: list[tuple[int, int, SegmentType, str, str]] = []
    seen: set[int] = set()
    for raw in resolutions:
        segment_index = _coerce_nonnegative_int(raw.get("segment_index"))
        if segment_index is None or segment_index not in positions:
            return ExecutionResult(
                project_id=project_id,
                result={
                    "error": f"片段 {raw.get('segment_index')} 已不存在，请刷新后重试。",
                    "error_code": "tts_segment_not_found",
                },
            )
        if segment_index in seen:
            return ExecutionResult(
                project_id=project_id,
                result={
                    "error": f"片段 {segment_index} 收到了重复复核结果。",
                    "error_code": "duplicate_speaker_resolution",
                },
            )
        seen.add(segment_index)
        character_id = str(raw.get("character_id") or "").strip()
        requested_type = str(raw.get("segment_type") or "").strip()
        current = segments[positions[segment_index]]
        if requested_type == SegmentType.NARRATION.value:
            target_type = SegmentType.NARRATION
            character_id = ""
            character_name = ""
        else:
            entry = cast_by_id.get(character_id)
            if entry is None:
                return ExecutionResult(
                    project_id=project_id,
                    result={
                        "error": f"片段 {segment_index} 指向了不在当前配音团队中的角色。",
                        "error_code": "tts_cast_member_not_found",
                    },
                )
            target_type = (
                SegmentType.INNER_THOUGHT
                if requested_type == SegmentType.INNER_THOUGHT.value
                or (
                    not requested_type
                    and current.segment_type == SegmentType.INNER_THOUGHT
                )
                else SegmentType.DIALOGUE
            )
            character_name = entry.character_name
        normalized.append(
            (
                segment_index,
                positions[segment_index],
                target_type,
                character_id,
                character_name,
            )
        )

    if not normalized:
        return ExecutionResult(
            project_id=project_id,
            result={
                "error": "没有收到说话人复核结果。",
                "error_code": "speaker_resolutions_empty",
            },
        )

    resolved_indices = {item[0] for item in normalized}
    for _segment_index, position, target_type, character_id, character_name in normalized:
        segments[position] = refresh_segment_uid(
            segments[position].model_copy(
                update={
                    "segment_type": target_type,
                    "character_id": character_id,
                    "character_name": character_name,
                }
            )
        )

    metadata = dict(script.metadata)
    adjudication = dict(metadata.get("speaker_adjudication") or {})
    adjudication["unresolved_segment_indices"] = [
        value
        for value in adjudication.get("unresolved_segment_indices", [])
        if _coerce_nonnegative_int(value) not in resolved_indices
    ]
    manual_reviews = list(adjudication.get("manual_reviews") or [])
    manual_reviews.extend(
        {
            "segment_index": segment_index,
            "character_id": character_id,
            "segment_type": target_type.value,
            "source": "engine_ui",
        }
        for segment_index, _position, target_type, character_id, _name in normalized
    )
    adjudication["manual_reviews"] = manual_reviews

    professional = dict(metadata.get("professional_script_review") or {})
    llm_review = dict(professional.get("llm_review") or {})
    llm_review["manual_review_segment_indices"] = [
        value
        for value in llm_review.get("manual_review_segment_indices", [])
        if _coerce_nonnegative_int(value) not in resolved_indices
    ]
    professional["llm_review"] = llm_review
    metadata.update(
        {
            "speaker_adjudication": adjudication,
            "professional_script_review": professional,
        }
    )
    updated_script = script.model_copy(update={"segments": segments, "metadata": metadata})
    remaining = unresolved_speaker_indices(updated_script)
    adjudication["status"] = "needs_review" if remaining else "passed"
    if not remaining and llm_review.get("status") == "needs_review":
        llm_review["status"] = "passed"
    updated_script = updated_script.model_copy(
        update={
            "metadata": {
                **updated_script.metadata,
                "speaker_adjudication": adjudication,
                "professional_script_review": {
                    **professional,
                    "llm_review": llm_review,
                },
            }
        }
    )
    updated_script.script_hash = compute_dubbing_script_hash(updated_script)

    # The reviewed script becomes authoritative only after its atomic write
    # succeeds; then retire audio rendered from the previous assignments.
    atomic_write_json(script_path, updated_script.model_dump(mode="json"))
    removed = invalidate_chapter_tts_artifacts(
        layout,
        chapter_number,
        include_metadata=False,
        include_script=False,
    )
    return ExecutionResult(
        project_id=project_id,
        result={
            "chapter_number": chapter_number,
            "resolved_segment_indices": sorted(resolved_indices),
            "remaining_unresolved_segment_indices": list(remaining),
            "invalidated_artifact_count": len(removed),
            "script_hash": updated_script.script_hash,
        },
    )


async def _repair_unresolved_speakers(
    *,
    script: DubbingScript,
    unresolved_indices: tuple[int, ...],
    voice_team: VoiceTeamContract,
    layout: ProjectLayout,
    chapter_number: int,
    settings: Settings,
    on_step_progress: Any = None,
) -> DubbingScript:
    """Run a targeted LLM repair pass on unresolved speaker segments.

    Provides richer context than the initial adjudication: wider paragraph
    window, full character roster with speaking-style clues, and the dialogue
    flow (who spoke before/after each unresolved segment).  The goal is to
    let the model resolve speakers it could not determine in the first pass
    rather than silently degrading dialogue to narration.
    """
    from novel_forge.common.constants import TaskType  # noqa: PLC0415
    from novel_forge.gateway.factory import ModelRouterBuilder  # noqa: PLC0415
    from novel_forge.pipeline.long.services.generation.llm_service import (
        LLMService,  # noqa: PLC0415
    )
    from novel_forge.prompts.builder import PromptBuilder  # noqa: PLC0415

    _emit_tts_progress(
        on_step_progress,
        "tts_speaker_repair_start",
        {"chapter": chapter_number, "unresolved_count": len(unresolved_indices)},
    )

    # Load chapter text for wider paragraph context.
    chapter_text = ""
    chapter_path = layout.chapter_path(chapter_number)
    if chapter_path.exists():
        try:
            chapter_text = chapter_path.read_text(encoding="utf-8")
        except Exception:
            chapter_text = ""
    paragraphs = [p.strip() for p in chapter_text.split("\n") if p.strip()] if chapter_text else []

    # Build character evidence from voice_team + character_bible.
    character_evidence: list[dict[str, Any]] = []
    characters_payload = _load_json(layout.characters_path)
    raw_characters = characters_payload.get("characters", [])
    characters_list = raw_characters if isinstance(raw_characters, list) else []
    char_by_name: dict[str, dict[str, Any]] = {}
    for raw in characters_list:
        if isinstance(raw, dict):
            name = str(raw.get("name", "")).strip()
            if name:
                char_by_name[name] = raw
    for entry in voice_team.entries:
        if not entry.character_id:
            continue
        bio = char_by_name.get(entry.character_name, {})
        character_evidence.append(
            {
                "character_id": entry.character_id,
                "character_name": entry.character_name,
                "speech_style": str(bio.get("speech_style", "") or bio.get("voice", "")),
                "personality_hint": str(bio.get("personality", "") or bio.get("trait", ""))[:120],
            }
        )

    # Build focused candidate cards for unresolved segments.
    segments = list(script.segments)
    segment_positions = {
        segment.segment_index: position for position, segment in enumerate(segments)
    }
    if len(segment_positions) != len(segments):
        _log.warning(
            "tts_speaker_repair_skipped | chapter=%d reason=duplicate_segment_indices",
            chapter_number,
        )
        return script
    candidate_cards: list[dict[str, Any]] = []
    for segment_index in unresolved_indices:
        position = segment_positions.get(segment_index)
        if position is None:
            continue
        segment = segments[position]
        # Gather surrounding segment context (3 before, 3 after).
        prev_segments = [
            {
                "segment_type": segments[i].segment_type.value,
                "character_id": segments[i].character_id,
                "text": segments[i].text[:100],
            }
            for i in range(max(0, position - 3), position)
        ]
        next_segments = [
            {
                "segment_type": segments[i].segment_type.value,
                "character_id": segments[i].character_id,
                "text": segments[i].text[:100],
            }
            for i in range(position + 1, min(len(segments), position + 4))
        ]
        # Find the paragraph this segment's text belongs to for wider context.
        para_idx = -1
        for pi, para in enumerate(paragraphs):
            if segment.text and segment.text[:30] in para:
                para_idx = pi
                break
        paragraph_before = paragraphs[para_idx - 1] if para_idx > 0 else ""
        paragraph_text = paragraphs[para_idx] if 0 <= para_idx < len(paragraphs) else ""
        paragraph_after = paragraphs[para_idx + 1] if 0 <= para_idx + 1 < len(paragraphs) else ""
        candidate_cards.append(
            {
                "candidate_id": f"repair_{segment_index}",
                "segment_index": segment_index,
                "text": segment.text,
                "context": paragraph_text or segment.text,
                "paragraph_before": paragraph_before,
                "paragraph_text": paragraph_text,
                "paragraph_after": paragraph_after,
                "current_character_id": segment.character_id,
                "current_segment_role": segment.segment_type.value,
                "previous_segments": prev_segments,
                "next_segments": next_segments,
                "structural_role_status": "spoken",
                "structural_role_reason": "synthesis_repair: segment requires speaker assignment",
            }
        )

    if not candidate_cards:
        return script

    allowed_character_ids = {
        entry.character_id for entry in voice_team.entries if entry.character_id
    }
    cards = {
        "candidates": candidate_cards,
        "voice_team": [
            {"character_id": e.character_id, "character_name": e.character_name}
            for e in voice_team.entries
            if e.character_id
        ],
        "character_evidence": character_evidence,
        "adjudication_pass": "synthesis_repair",
    }

    try:
        router = ModelRouterBuilder(settings).build()
        builder = PromptBuilder()
        service = LLMService(
            router=router,
            builder=builder,
            on_step=lambda *a, **kw: None,
            settings=settings,
        )
        response = await service.call_with_retry(
            TaskType.TTS_ADJUDICATE_SCRIPT_SEGMENTS,
            {"stage_cards": cards},
            max_tokens=max(4096, len(candidate_cards) * 260),
            temperature=0.0,
            required_keys=("decisions", "summary"),
            max_retries=2,
        )
    except Exception as exc:
        _log.warning(
            "tts_speaker_repair_failed | chapter=%d error=%s",
            chapter_number,
            exc,
        )
        _emit_tts_progress(
            on_step_progress,
            "tts_speaker_repair_failed",
            {"chapter": chapter_number, "error": str(exc)},
        )
        return script

    # Apply resolved speakers from the repair response.
    decisions = response.get("decisions", []) if isinstance(response, dict) else []
    resolved_indices: set[int] = set()
    for decision in decisions:
        if not isinstance(decision, dict):
            continue
        candidate_id = str(decision.get("candidate_id", "")).strip()
        if not candidate_id.startswith("repair_"):
            continue
        try:
            segment_index = int(candidate_id.removeprefix("repair_"))
        except ValueError:
            continue
        position = segment_positions.get(segment_index)
        if position is None:
            continue
        verdict = str(decision.get("verdict", "")).strip()
        character_id = str(decision.get("character_id", "")).strip()
        segment_role = str(decision.get("segment_role", "")).strip()
        try:
            confidence = float(decision.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0

        # Accept resolved speakers with reasonable confidence.
        if verdict == "resolved" and character_id in allowed_character_ids and confidence >= 0.65:
            target_type = (
                SegmentType.INNER_THOUGHT
                if segment_role == SegmentType.INNER_THOUGHT.value
                else SegmentType.DIALOGUE
            )
            # Find character name from voice_team.
            char_name = next(
                (e.character_name for e in voice_team.entries if e.character_id == character_id),
                "",
            )
            segments[position] = refresh_segment_uid(
                segments[position].model_copy(
                    update={
                        "segment_type": target_type,
                        "character_id": character_id,
                        "character_name": char_name,
                    }
                )
            )
            resolved_indices.add(segment_index)
        elif segment_role == SegmentType.NARRATION.value and confidence >= 0.72:
            # Model determined this is actually narration, not dialogue.
            segments[position] = refresh_segment_uid(
                segments[position].model_copy(
                    update={
                        "segment_type": SegmentType.NARRATION,
                        "character_id": "",
                        "character_name": "",
                    }
                )
            )
            resolved_indices.add(segment_index)

    if not resolved_indices:
        _emit_tts_progress(
            on_step_progress,
            "tts_speaker_repair_no_progress",
            {"chapter": chapter_number, "unresolved_count": len(unresolved_indices)},
        )
        return script

    # Update adjudication metadata and persist.
    updated_script = script.model_copy(update={"segments": segments})
    adjudication_meta = dict(updated_script.metadata.get("speaker_adjudication") or {})
    adjudication_meta["unresolved_segment_indices"] = [
        value
        for value in adjudication_meta.get("unresolved_segment_indices", [])
        if _coerce_nonnegative_int(value) not in resolved_indices
    ]
    adjudication_meta["synthesis_repair"] = {
        "attempted": len(candidate_cards),
        "resolved": len(resolved_indices),
    }
    professional_meta = dict(updated_script.metadata.get("professional_script_review") or {})
    llm_review_meta = dict(professional_meta.get("llm_review") or {})
    llm_review_meta["manual_review_segment_indices"] = [
        value
        for value in llm_review_meta.get("manual_review_segment_indices", [])
        if _coerce_nonnegative_int(value) not in resolved_indices
    ]
    if (
        not llm_review_meta["manual_review_segment_indices"]
        and llm_review_meta.get("status") == "needs_review"
    ):
        llm_review_meta["status"] = "passed"
    professional_meta["llm_review"] = llm_review_meta
    updated_script = updated_script.model_copy(
        update={
            "metadata": {
                **updated_script.metadata,
                "speaker_adjudication": adjudication_meta,
                "professional_script_review": professional_meta,
            }
        }
    )
    remaining_indices = unresolved_speaker_indices(updated_script)
    if not remaining_indices and adjudication_meta.get("status") == "needs_review":
        adjudication_meta["status"] = "passed"
        updated_script = updated_script.model_copy(
            update={
                "metadata": {
                    **updated_script.metadata,
                    "speaker_adjudication": adjudication_meta,
                }
            }
        )
    updated_script.script_hash = compute_dubbing_script_hash(updated_script)

    script_path = layout.tts_dubbing_script_path(chapter_number)
    try:
        atomic_write_json(script_path, updated_script.model_dump(mode="json"))
    except Exception as exc:
        _log.warning("Failed to persist speaker-repaired script: %s", exc)
        _emit_tts_progress(
            on_step_progress,
            "tts_speaker_repair_persist_failed",
            {"chapter": chapter_number, "error": str(exc)},
        )
        return script

    _log.info(
        "tts_speaker_repair | chapter=%d attempted=%d resolved=%d remaining=%d",
        chapter_number,
        len(candidate_cards),
        len(resolved_indices),
        len(remaining_indices),
    )
    _emit_tts_progress(
        on_step_progress,
        "tts_speaker_repair_done",
        {
            "chapter": chapter_number,
            "attempted": len(candidate_cards),
            "resolved": len(resolved_indices),
            "remaining": len(remaining_indices),
        },
    )
    return updated_script
