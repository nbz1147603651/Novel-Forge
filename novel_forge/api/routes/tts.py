"""TTS API routes — voice team, dubbing script, synthesis."""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Path, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from novel_forge.api.deps import get_job_service, get_runtime_services
from novel_forge.api.run_logging import execute_api_with_run_logger
from novel_forge.app_service.contracts import JobCommand, JobKind, JobRecord
from novel_forge.app_service.job_service import JobService
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.gateway.factory import registered_tts_provider_ids
from novel_forge.tts.schemas import ChapterAudioResult, TTSProgressState
from novel_forge.tts.services.automation import AudioAutomationMode, resolve_audio_automation_mode
from novel_forge.tts.services.delivery import TTSDeliveryNotReadyError, require_delivery_ready
from novel_forge.workspace.execution import (
    execute_build_voice_team,
    execute_clone_character_voice,
    execute_design_character_voice,
    execute_export_audiobook_package,
    execute_full_tts_pipeline,
    execute_generate_dubbing_script,
    execute_list_tts_voices,
    execute_preview_character_voice,
    execute_synthesize_chapter,
)
from novel_forge.workspace.runtime import RuntimeServices
from novel_forge.workspace.tts_ops.execution import (
    tts_artifact_source_mismatch,
    tts_audio_result_script_mismatch,
    tts_audio_result_source_hash,
)

router = APIRouter()
RuntimeDep = Annotated[RuntimeServices, Depends(get_runtime_services)]
JobServiceDep = Annotated[JobService, Depends(get_job_service)]


def _result_dict(execution: Any) -> dict[str, Any]:
    return cast(dict[str, Any], execution.result)


def _successful_result(execution: Any) -> dict[str, Any]:
    payload = _result_dict(execution)
    if payload.get("error"):
        raise HTTPException(status_code=422, detail=payload)
    return payload


# ─── Request/Response Models ──────────────────────────────────────────────────


class CharacterInput(BaseModel):
    """Character input for voice team building."""

    character_id: str = Field(default="", description="角色唯一标识")
    name: str = Field(min_length=1, description="角色名称")
    gender: str = Field(default="", description="性别: male/female/neutral")
    age: str = Field(default="", description="年龄描述")
    role: str = Field(default="", description="角色定位")
    personality: str = Field(default="", description="性格描述")
    voice: str = Field(default="", description="角色声纹/说话习惯")
    reference_audio_path: str = Field(default="", description="参考音频路径")
    reference_transcript: str = Field(default="", description="授权参考音频逐字转写")
    reference_audio_authorized: bool = Field(default=False, description="已取得参考说话人授权")
    voice_description: str = Field(default="", description="音色描述")
    voice_id: str = Field(default="", description="人工指定的系统/克隆音色 ID")
    tts_voice_hints: dict[str, Any] | None = Field(default=None, description="结构化 TTS 声音提示")


class BuildVoiceTeamRequest(BaseModel):
    """Request to build voice team."""

    project_id: str = Field(min_length=1)
    characters: list[CharacterInput] = Field(default_factory=list)
    narrator_voice_id: str = Field(default="")
    provider: str = Field(default="", description="TTS provider override")


class GenerateScriptRequest(BaseModel):
    """Request to generate dubbing script."""

    project_id: str = Field(min_length=1)
    chapter_number: int = Field(ge=1)
    chapter_text: str = Field(min_length=1)
    provider: str = Field(default="", description="TTS provider override")
    reference_script_text: str = Field(default="", max_length=200_000)
    reference_script_name: str = Field(default="", max_length=200)
    reference_style_strength: float = Field(default=0.65, ge=0.0, le=1.0)


class SynthesizeRequest(BaseModel):
    """Request to synthesize chapter audio."""

    project_id: str = Field(min_length=1)
    chapter_number: int = Field(ge=1)
    provider: str = Field(default="", description="TTS provider override")
    automation_mode: AudioAutomationMode | None = Field(
        default=None,
        description="配音推进模式；留空时使用平台设置。",
    )


class FullTTSPipelineRequest(BaseModel):
    """Request to run full TTS pipeline."""

    project_id: str = Field(min_length=1)
    chapter_number: int = Field(ge=1)
    chapter_text: str = Field(min_length=1)
    characters: list[CharacterInput] = Field(default_factory=list)
    provider: str = Field(default="")
    automation_mode: AudioAutomationMode | None = Field(
        default=None,
        description="配音推进模式；留空时使用平台设置。",
    )


class CharacterVoiceDesignRequest(BaseModel):
    """Design a persisted voice anchored to upstream character traits."""

    project_id: str = Field(min_length=1)
    character_id: str = Field(min_length=1)
    editorial_note: str = Field(default="", max_length=1200)
    provider: str = Field(default="")


class CharacterVoiceCloneRequest(BaseModel):
    """Clone from a provider-uploaded file id (never a server filesystem path)."""

    project_id: str = Field(min_length=1)
    character_id: str = Field(min_length=1)
    reference_file_id: str = Field(
        min_length=1,
        max_length=512,
        pattern=r"^[A-Za-z0-9._:-]+$",
        description="供应商文件 ID；不接受本地路径",
    )
    provider: str = Field(default="")
    reference_transcript: str = Field(default="", max_length=4000)
    authorized: bool = Field(default=False, description="已取得参考说话人授权")


class CharacterVoicePreviewRequest(BaseModel):
    """Synthesize a short preview using the persisted cast entry."""

    project_id: str = Field(min_length=1)
    character_id: str = Field(min_length=1)
    sample_text: str = Field(min_length=1, max_length=500)
    provider: str = Field(default="")


class VoiceTeamResponse(BaseModel):
    """Voice team response."""

    entries: list[dict[str, Any]]
    narrator_voice_id: str
    default_provider: str
    default_tts_model: str


class ScriptResponse(BaseModel):
    """Dubbing script response."""

    chapter_number: int
    segment_count: int
    dialogue_count: int
    narration_count: int
    total_estimated_duration_ms: int
    script_hash: str


class SynthesisResponse(BaseModel):
    """Synthesis result response."""

    chapter_number: int
    total_duration_ms: int
    total_cost_usd: float
    is_complete: bool
    assembled_audio_path: str
    subtitle_path: str


# ─── Routes ───────────────────────────────────────────────────────────────────


@router.post("/voice-team", response_model=dict)
async def build_voice_team(
    req: BuildVoiceTeamRequest,
    runtime: RuntimeDep,
) -> dict[str, Any]:
    """Build or update voice team for a project."""
    settings = runtime.settings
    storage = runtime.storage
    project_dir = storage.project_dir(req.project_id)
    layout = ProjectLayout(project_dir)
    layout.ensure_dirs()

    characters = [c.model_dump() for c in req.characters]

    execution = await execute_api_with_run_logger(
        runtime,
        project_id=req.project_id,
        command="api:tts.build_voice_team",
        metadata=req.model_dump(mode="json"),
        execute=lambda on_step: execute_build_voice_team(
            project_id=req.project_id,
            characters=characters,
            settings=settings,
            layout=layout,
            narrator_voice_id=req.narrator_voice_id,
            provider=req.provider,
            on_step_progress=on_step,
        ),
    )

    return _successful_result(execution)


@router.get("/voice-team/{project_id}", response_model=dict)
async def get_voice_team(
    project_id: str,
    runtime: RuntimeDep,
) -> dict[str, Any]:
    """Get current voice team for a project."""
    storage = runtime.storage
    project_dir = storage.project_dir(project_id)
    layout = ProjectLayout(project_dir)

    if not layout.tts_voice_team_path.exists():
        raise HTTPException(status_code=404, detail="Voice team not found")

    import json

    data: dict[str, Any] = json.loads(layout.tts_voice_team_path.read_text(encoding="utf-8"))
    return data


@router.post("/script", response_model=dict)
async def generate_dubbing_script(
    req: GenerateScriptRequest,
    runtime: RuntimeDep,
) -> dict[str, Any]:
    """Generate dubbing script for a chapter."""
    settings = runtime.settings
    storage = runtime.storage
    project_dir = storage.project_dir(req.project_id)
    layout = ProjectLayout(project_dir)
    layout.ensure_dirs()

    execution = await execute_api_with_run_logger(
        runtime,
        project_id=req.project_id,
        command="api:tts.generate_script",
        metadata={
            "chapter_number": req.chapter_number,
            "provider": req.provider,
            "reference_script_name": req.reference_script_name,
            "reference_script_chars": len(req.reference_script_text),
            "reference_style_strength": req.reference_style_strength,
        },
        execute=lambda on_step: execute_generate_dubbing_script(
            project_id=req.project_id,
            chapter_number=req.chapter_number,
            chapter_text=req.chapter_text,
            settings=settings,
            layout=layout,
            provider=req.provider,
            reference_script_text=req.reference_script_text,
            reference_script_name=req.reference_script_name,
            reference_style_strength=req.reference_style_strength,
            on_step_progress=on_step,
        ),
    )

    return _successful_result(execution)


@router.get("/script/{project_id}/{chapter_number}", response_model=dict)
async def get_dubbing_script(
    project_id: str,
    chapter_number: int,
    runtime: RuntimeDep,
) -> dict[str, Any]:
    """Get dubbing script for a chapter."""
    storage = runtime.storage
    project_dir = storage.project_dir(project_id)
    layout = ProjectLayout(project_dir)

    script_path = layout.tts_dubbing_script_path(chapter_number)
    if not script_path.exists():
        raise HTTPException(status_code=404, detail="Dubbing script not found")

    import json

    data: dict[str, Any] = json.loads(script_path.read_text(encoding="utf-8"))
    mismatch = tts_artifact_source_mismatch(
        layout,
        chapter_number,
        str(data.get("source_text_hash") or ""),
        artifact_name="Dubbing script",
        error_code="stale_dubbing_script",
    )
    if mismatch is not None:
        raise HTTPException(status_code=409, detail=mismatch)
    return data


class SegmentResolution(BaseModel):
    """A single speaker resolution for one segment."""

    segment_index: int = Field(ge=0)
    character_id: str = Field(default="", description="Empty = convert to narration")
    segment_type: str = Field(default="", description="narration | dialogue | inner_thought")


class ResolveSpeakersRequest(BaseModel):
    """Batch speaker resolution request."""

    resolutions: list[SegmentResolution]


@router.post("/script/{project_id}/{chapter_number}/resolve-speakers", response_model=dict)
async def resolve_speakers(
    project_id: str,
    chapter_number: int,
    req: ResolveSpeakersRequest,
    runtime: RuntimeDep,
) -> dict[str, Any]:
    """Resolve unresolved speaker assignments in a dubbing script.

    Accepts a batch of segment-to-character resolutions, updates the script,
    and persists it. Returns the updated script and remaining unresolved count.
    """
    import json

    from novel_forge.persistence.filesystem import atomic_write_json
    from novel_forge.tts.schemas import DubbingScript, SegmentType
    from novel_forge.tts.script_integrity import (
        compute_dubbing_script_hash,
        unresolved_speaker_indices,
    )

    storage = runtime.storage
    project_dir = storage.project_dir(project_id)
    layout = ProjectLayout(project_dir)

    script_path = layout.tts_dubbing_script_path(chapter_number)
    if not script_path.exists():
        raise HTTPException(status_code=404, detail="Dubbing script not found")

    data: dict[str, Any] = json.loads(script_path.read_text(encoding="utf-8"))
    script = DubbingScript.model_validate(data)

    # Build a character_id -> character_name lookup from voice team.
    voice_team_path = layout.tts_voice_team_path
    char_names: dict[str, str] = {}
    if voice_team_path.exists():
        try:
            team_data = json.loads(voice_team_path.read_text(encoding="utf-8"))
            for entry in team_data.get("entries") or []:
                cid = str(entry.get("character_id") or "").strip()
                cname = str(entry.get("character_name") or "").strip()
                if cid:
                    char_names[cid] = cname or cid
        except Exception:
            pass

    # Apply resolutions.
    segments = list(script.segments)
    resolved_indices: set[int] = set()
    for resolution in req.resolutions:
        idx = resolution.segment_index
        if idx < 0 or idx >= len(segments):
            raise HTTPException(
                status_code=422,
                detail=f"segment_index {idx} out of range (0-{len(segments) - 1})",
            )
        segment = segments[idx]
        if resolution.segment_type == "narration" or (
            not resolution.character_id and not resolution.segment_type
        ):
            # Convert to narration.
            segments[idx] = segment.model_copy(
                update={
                    "segment_type": SegmentType.NARRATION,
                    "character_id": "",
                    "character_name": "",
                    "spoken_text": "",
                }
            )
        else:
            target_type = (
                SegmentType.INNER_THOUGHT
                if resolution.segment_type == "inner_thought"
                else SegmentType.DIALOGUE
            )
            char_name = char_names.get(resolution.character_id, resolution.character_id)
            segments[idx] = segment.model_copy(
                update={
                    "segment_type": target_type,
                    "character_id": resolution.character_id,
                    "character_name": char_name,
                }
            )
        resolved_indices.add(idx)

    script = script.model_copy(update={"segments": segments})

    # Update adjudication metadata.
    metadata = dict(script.metadata)
    adjudication = dict(metadata.get("speaker_adjudication") or {})
    old_unresolved = set(adjudication.get("unresolved_segment_indices") or [])
    new_unresolved = sorted(old_unresolved - resolved_indices)
    adjudication["unresolved_segment_indices"] = new_unresolved
    if not new_unresolved:
        adjudication["status"] = "passed"
    # Record manual reviews.
    manual_reviews = list(adjudication.get("manual_reviews") or [])
    for resolution in req.resolutions:
        seg = segments[resolution.segment_index]
        manual_reviews.append(
            {
                "segment_index": resolution.segment_index,
                "segment_type": seg.segment_type.value,
                "character_id": seg.character_id,
                "character_name": seg.character_name,
                "source": "api_resolve_speakers",
            }
        )
    adjudication["manual_reviews"] = manual_reviews
    metadata["speaker_adjudication"] = adjudication
    script = script.model_copy(update={"metadata": metadata})

    # Recompute hash and persist.
    script.script_hash = compute_dubbing_script_hash(script)
    atomic_write_json(script_path, script.model_dump(mode="json"))

    remaining = unresolved_speaker_indices(script)
    return {
        "script": script.model_dump(mode="json"),
        "remaining_unresolved": list(remaining),
        "remaining_count": len(remaining),
    }


@router.post("/synthesize", response_model=dict)
async def synthesize_chapter(
    req: SynthesizeRequest,
    runtime: RuntimeDep,
) -> dict[str, Any]:
    """Synthesize audio for a chapter."""
    settings = runtime.settings
    storage = runtime.storage
    project_dir = storage.project_dir(req.project_id)
    layout = ProjectLayout(project_dir)
    layout.ensure_dirs()
    automation_mode = resolve_audio_automation_mode(req.automation_mode, settings=settings)

    execution = await execute_api_with_run_logger(
        runtime,
        project_id=req.project_id,
        command="api:tts.synthesize",
        metadata={
            "chapter_number": req.chapter_number,
            "automation_mode": automation_mode.value,
        },
        execute=lambda on_step: execute_synthesize_chapter(
            project_id=req.project_id,
            chapter_number=req.chapter_number,
            settings=settings,
            layout=layout,
            provider=req.provider,
            automation_mode=automation_mode,
            on_step_progress=on_step,
        ),
    )

    return _successful_result(execution)


@router.post("/synthesize/jobs", response_model=JobRecord, status_code=202)
async def submit_synthesis_job(
    req: SynthesizeRequest,
    service: JobServiceDep,
) -> JobRecord:
    """Submit resumable synthesis to the shared durable job control plane."""

    return service.submit(
        JobCommand(
            kind=JobKind.TTS_SYNTHESIZE,
            project_id=req.project_id,
            payload=req.model_dump(mode="json"),
        )
    )


@router.post("/pipeline", response_model=dict)
async def run_full_tts_pipeline(
    req: FullTTSPipelineRequest,
    runtime: RuntimeDep,
) -> dict[str, Any]:
    """Run full TTS pipeline for a chapter.

    Combines: build_voice_team → generate_script → synthesize → assemble.
    """
    settings = runtime.settings
    storage = runtime.storage
    project_dir = storage.project_dir(req.project_id)
    layout = ProjectLayout(project_dir)
    layout.ensure_dirs()
    automation_mode = resolve_audio_automation_mode(req.automation_mode, settings=settings)

    characters = [c.model_dump() for c in req.characters]

    execution = await execute_api_with_run_logger(
        runtime,
        project_id=req.project_id,
        command="api:tts.full_pipeline",
        metadata={
            "chapter_number": req.chapter_number,
            "automation_mode": automation_mode.value,
        },
        execute=lambda on_step: execute_full_tts_pipeline(
            project_id=req.project_id,
            chapter_number=req.chapter_number,
            chapter_text=req.chapter_text,
            characters=characters,
            settings=settings,
            layout=layout,
            provider=req.provider,
            automation_mode=automation_mode,
            on_step_progress=on_step,
        ),
    )

    return _successful_result(execution)


@router.post("/pipeline/jobs", response_model=JobRecord, status_code=202)
async def submit_full_pipeline_job(
    req: FullTTSPipelineRequest,
    service: JobServiceDep,
) -> JobRecord:
    """Submit the complete dubbing pipeline as a persistent cancellable job."""

    return service.submit(
        JobCommand(
            kind=JobKind.TTS_FULL_PIPELINE,
            project_id=req.project_id,
            payload=req.model_dump(mode="json"),
        )
    )


class ExportAudiobookRequest(BaseModel):
    """Request to export a finished audiobook delivery package."""

    project_id: str = Field(min_length=1)
    chapter_numbers: list[int] = Field(
        default_factory=list,
        description="要导出的章节号；为空时自动收集全部已装配章节",
    )
    require_delivery_ready: bool = Field(
        default=True,
        description="默认要求每章通过交付门禁；关闭后仅要求装配完成",
    )


@router.post("/export/audiobook", response_model=dict)
async def export_audiobook_package(
    req: ExportAudiobookRequest,
    runtime: RuntimeDep,
) -> dict[str, Any]:
    """Export the finished audiobook package (per-chapter mp3 + TOC + report).

    Chapter ordering is gated: chapters not yet assembled and
    delivery-confirmed return a 422 with ``gate_chapters`` so the client can
    prompt "第 N 章确认后继续".
    """
    settings = runtime.settings
    storage = runtime.storage
    project_dir = storage.project_dir(req.project_id)
    layout = ProjectLayout(project_dir)

    execution = await execute_api_with_run_logger(
        runtime,
        project_id=req.project_id,
        command="api:tts.export_audiobook",
        metadata={
            "chapter_numbers": req.chapter_numbers,
            "require_delivery_ready": req.require_delivery_ready,
        },
        execute=lambda on_step: execute_export_audiobook_package(
            project_id=req.project_id,
            settings=settings,
            layout=layout,
            chapter_numbers=req.chapter_numbers or None,
            require_delivery_ready=req.require_delivery_ready,
        ),
    )

    return _successful_result(execution)


@router.get("/audio/{project_id}/{chapter_number}", response_model=dict)
async def get_audio_result(
    project_id: str,
    chapter_number: int,
    runtime: RuntimeDep,
    include_incomplete: bool = False,
) -> dict[str, Any]:
    """Get a deliverable chapter result, or opt into diagnostic incomplete data."""
    storage = runtime.storage
    project_dir = storage.project_dir(project_id)
    layout = ProjectLayout(project_dir)

    result_path = layout.tts_audio_result_path(chapter_number)
    if not result_path.exists():
        raise HTTPException(status_code=404, detail="Audio result not found")

    import json

    data: dict[str, Any] = json.loads(result_path.read_text(encoding="utf-8"))
    mismatch = tts_artifact_source_mismatch(
        layout,
        chapter_number,
        tts_audio_result_source_hash(data),
        artifact_name="Chapter audio",
        error_code="stale_chapter_audio",
    )
    if mismatch is not None:
        raise HTTPException(status_code=409, detail=mismatch)
    script_mismatch = tts_audio_result_script_mismatch(layout, chapter_number, data)
    if script_mismatch is not None:
        raise HTTPException(status_code=409, detail=script_mismatch)
    result = ChapterAudioResult.model_validate(data)
    if not include_incomplete:
        try:
            require_delivery_ready(result)
        except TTSDeliveryNotReadyError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "tts_delivery_not_ready",
                    "chapter_number": chapter_number,
                    "blocking_reasons": list(exc.reasons),
                },
            ) from exc
    return result.model_dump(mode="json")


@router.get("/progress/{project_id}/{chapter_number}", response_model=dict)
async def get_tts_progress(
    project_id: str,
    chapter_number: Annotated[int, Path(ge=1)],
    runtime: RuntimeDep,
) -> dict[str, Any]:
    """Return an incomplete chapter's resumable synthesis checkpoint."""

    import json

    layout = ProjectLayout(runtime.storage.project_dir(project_id))
    candidates = (
        layout.tts_progress_path_for_chapter(chapter_number),
        layout.tts_progress_path,
    )
    for path in candidates:
        if not path.exists():
            continue
        try:
            state = TTSProgressState.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
        if state.chapter_number == chapter_number:
            return state.model_dump(mode="json")
    raise HTTPException(status_code=404, detail="TTS progress not found")


@router.get("/providers", response_model=list[str])
async def list_tts_providers(
    runtime: RuntimeDep,
) -> list[str]:
    """List available TTS providers."""
    return list(registered_tts_provider_ids())


@router.get("/catalog/{provider}", response_model=dict)
async def get_provider_catalog(
    provider: str,
    runtime: RuntimeDep,
    gender: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Return catalog and negotiated capabilities through the workspace facade."""
    try:
        execution = await execute_list_tts_voices(
            project_id="api",
            settings=runtime.settings,
            provider=provider,
            gender=gender,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _successful_result(execution)


@router.post("/voice/design", response_model=dict)
async def design_character_voice(
    req: CharacterVoiceDesignRequest,
    runtime: RuntimeDep,
) -> dict[str, Any]:
    """Design and persist a character voice with server-side trait anchoring."""
    layout = ProjectLayout(runtime.storage.project_dir(req.project_id))
    layout.ensure_dirs()
    execution = await execute_api_with_run_logger(
        runtime,
        project_id=req.project_id,
        command="api:tts.design_character_voice",
        metadata={"character_id": req.character_id, "provider": req.provider},
        execute=lambda _on_step: execute_design_character_voice(
            project_id=req.project_id,
            character_id=req.character_id,
            description=req.editorial_note,
            settings=runtime.settings,
            layout=layout,
            provider=req.provider,
        ),
    )
    return _successful_result(execution)


@router.post("/voice/clone", response_model=dict)
async def clone_character_voice(
    req: CharacterVoiceCloneRequest,
    runtime: RuntimeDep,
) -> dict[str, Any]:
    """Clone from a provider file id without exposing server-local file access."""
    layout = ProjectLayout(runtime.storage.project_dir(req.project_id))
    layout.ensure_dirs()
    execution = await execute_api_with_run_logger(
        runtime,
        project_id=req.project_id,
        command="api:tts.clone_character_voice",
        metadata={"character_id": req.character_id, "provider": req.provider},
        execute=lambda _on_step: execute_clone_character_voice(
            project_id=req.project_id,
            character_id=req.character_id,
            reference_audio=req.reference_file_id,
            settings=runtime.settings,
            layout=layout,
            provider=req.provider,
            reference_transcript=req.reference_transcript,
            authorized=req.authorized,
        ),
    )
    return _successful_result(execution)


@router.post("/voice/preview", response_model=dict)
async def preview_character_voice(
    req: CharacterVoicePreviewRequest,
    runtime: RuntimeDep,
) -> dict[str, Any]:
    """Synthesize a short character preview through the shared execution boundary."""
    layout = ProjectLayout(runtime.storage.project_dir(req.project_id))
    layout.ensure_dirs()
    execution = await execute_api_with_run_logger(
        runtime,
        project_id=req.project_id,
        command="api:tts.preview_character_voice",
        metadata={"character_id": req.character_id, "provider": req.provider},
        execute=lambda _on_step: execute_preview_character_voice(
            project_id=req.project_id,
            character_id=req.character_id,
            sample_text=req.sample_text,
            settings=runtime.settings,
            layout=layout,
            provider=req.provider,
        ),
    )
    return _successful_result(execution)


@router.get("/system-voices/{provider}", response_model=list[dict[str, Any]])
async def list_system_voices(
    provider: str,
    runtime: RuntimeDep,
    gender: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List available system voices while preserving the legacy list response."""
    try:
        execution = await execute_list_tts_voices(
            project_id="api",
            settings=runtime.settings,
            provider=provider,
            gender=gender,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return list(_successful_result(execution).get("voices", []))


# ─── WebSocket Progress Push ───────────────────────────────────────────────────


@router.websocket("/ws/progress/{project_id}/{chapter_number}")
async def tts_progress_ws(
    websocket: WebSocket,
    project_id: str,
    chapter_number: int,
) -> None:
    """Real-time TTS synthesis progress via WebSocket.

    Subscribes to the TTS progress broadcaster and pushes step events
    to the connected client until the job completes or the client
    disconnects.

    Protocol:
        - Server sends JSON messages: {"event": str, "data": dict, ...}
        - Terminal events: "job_complete" or "job_failed"
        - Client can send "ping" to keep alive (server responds "pong")
    """
    from novel_forge.tts.runtime.progress_broadcaster import (  # noqa: PLC0415
        get_tts_progress_broadcaster,
    )

    await websocket.accept()
    broadcaster = get_tts_progress_broadcaster()
    queue = broadcaster.subscribe(project_id, chapter_number)
    try:
        while True:
            # Wait for events with a timeout to allow ping/pong keepalive.
            try:
                import asyncio

                event = await asyncio.wait_for(queue.get(), timeout=30.0)
            except TimeoutError:
                # Send keepalive ping.
                try:
                    await websocket.send_json({"event": "ping", "data": {}})
                except Exception:
                    break
                continue

            if event is None:
                break
            await websocket.send_json(event)

            # Terminal events close the stream.
            if event.get("event") in ("job_complete", "job_failed"):
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        broadcaster.unsubscribe(queue)
