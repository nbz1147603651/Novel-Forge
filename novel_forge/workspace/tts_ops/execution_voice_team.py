"""
Voice-team and narrator executions.

Extracted from execution.py: build/preview/clone/design/approve/assign/
confirm voice-team operations plus MiniMax voice activation and team
diagnostics.  Depends on execution_shared and execution_assets.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from novel_forge.core.config import Settings
from novel_forge.obs.logger import get_logger
from novel_forge.persistence.filesystem import (
    atomic_write_json,
)
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.project_staleness import (
    invalidate_all_tts_audio_derivatives,
)
from novel_forge.tts.assets.voice_library import (
    VoiceLibrary,
    entry_from_cast,
)
from novel_forge.tts.assets.voice_matching import designed_voice_assessment
from novel_forge.tts.gateway.factory import (
    TTSAdapterRegistry,
    resolve_tts_model,
)
from novel_forge.tts.pipeline.build_narrator_profile_step import (
    BuildNarratorProfileInput,
    BuildNarratorProfileStep,
)
from novel_forge.tts.pipeline.build_voice_team_step import (
    BuildVoiceTeamInput,
    BuildVoiceTeamStep,
    build_voice_design_prompt,
    build_voice_preview_text,
)
from novel_forge.tts.platform.schemas import (
    AudioExecutionPlan,
    AudioExecutionStage,
)
from novel_forge.tts.runtime.performance_policy import (
    resolve_character_performance,
    with_manual_performance_overrides,
)
from novel_forge.tts.schemas import (
    NarratorVoiceProfile,
    TTSProvider,
    TTSRequest,
    VariantPreview,
    VoiceCastEntry,
    VoiceCloneRequest,
    VoiceCloneStatus,
    VoiceDesignRequest,
    VoiceTeamContract,
)
from novel_forge.workspace.execution_result import ExecutionResult
from novel_forge.workspace.tts_ops.execution_assets import (
    _allows_parallel_voice_build,
    _apply_preview_audio_controls,
    _clear_voice_library_activation_deadlines,
    _existing_preview_cache_path,
    _persist_voice_library_updates,
    _preview_cache_path,
    _preview_format,
    _sync_narrator_voice_team_binding,
    _synthesize_preview_with_timeout,
    _write_preview_audio,
)
from novel_forge.workspace.tts_ops.execution_shared import (
    REASON_CLONE_NOT_READY,
    REASON_EXPIRED,
    REASON_MISSING,
    REASON_NARRATOR_UNAVAILABLE,
    REASON_PENDING_APPROVAL,
    REASON_PROVIDER_MISMATCH,
    REASON_UNKNOWN,
    _emit_tts_progress,
    _ensure_managed_tts_runtime,
    _load_json,
    _load_tts_upstream_context,
    _load_voice_team,
    _merge_character_inputs,
    _resolve_provider,
    _stable_hash,
)
from novel_forge.workspace.tts_ops.lock_manager import (
    with_interactive_preview_tts_lock as _with_interactive_preview_tts_lock,
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

def _voice_team_content_hash(team: VoiceTeamContract | None) -> str:
    """Fingerprint synthesis-relevant voice data without volatile timestamps."""
    if team is None:
        return ""
    payload = team.model_dump(
        mode="json",
        exclude={
            "created_at",
            "updated_at",
            "preview_audio_path",
            "voice_catalog_fingerprint",
            "voice_catalog_synced_at",
            "voice_catalog_sync_status",
        },
    )
    for entry in payload.get("entries", []):
        if isinstance(entry, dict):
            entry.pop("activation_deadline", None)
            entry.pop("identity_locked", None)
            entry.pop("identity_locked_at", None)
            entry.pop("identity_lock_reason", None)
            entry.pop("match_score", None)
            entry.pop("match_reasons", None)
            entry.pop("match_warnings", None)
            entry.pop("preview_audio_path", None)
            entry.pop("preview_text", None)
            entry.pop("preview_error", None)
    return _stable_hash(payload)


def _apply_execution_tts_assignment(
    *,
    selected_provider: TTSProvider,
    execution_plan: AudioExecutionPlan,
) -> tuple[TTSProvider, str]:
    """Translate the frozen formal-TTS route into its provider adapter.

    The provider/model snapshot comes from the plan, not from the mutable
    registry. Unknown providers fail explicitly instead of silently changing
    the route selected for this project.
    """

    assignment = execution_plan.assignment_for(AudioExecutionStage.TTS_FORMAL)
    if assignment is None or assignment.primary is None:
        return selected_provider, ""
    provider_id = assignment.primary.provider_id.strip().lower()
    model_id = assignment.primary.model_id
    if provider_id == selected_provider.value or (
        selected_provider in {TTSProvider.BAILIAN, TTSProvider.DASHSCOPE}
        and provider_id == TTSProvider.DASHSCOPE.value
    ):
        return selected_provider, model_id
    try:
        return TTSProvider(provider_id), model_id
    except ValueError as exc:
        raise ValueError(
            f"AudioExecutionPlan 引用了尚未注册 TTS 适配器的平台：{provider_id}"
        ) from exc


def _replace_voice_entry(
    team: VoiceTeamContract,
    character_id: str,
    **updates: Any,
) -> VoiceTeamContract | None:
    """Replace one immutable-style Pydantic entry while preserving team order."""
    entry = team.get_entry(character_id)
    if entry is None:
        return None
    updated = entry.model_copy(update=updates)
    team.entries = [updated if item.character_id == character_id else item for item in team.entries]
    # Any entry mutation invalidates the author's whole-team confirmation so a
    # post-archive run can never silently reuse a team whose cast changed after
    # the author approved it.  The VoiceTeamContract validator is a backstop
    # for time-based expiry; this explicit reset is the authoritative signal
    # for write-driven mutations.
    team.confirmed = False
    team.confirmed_at = None
    return team


def _diagnose_voice_team_reuse(
    team: VoiceTeamContract | None,
    expected_character_ids: set[str],
    resolved_provider: TTSProvider,
) -> list[dict[str, str]]:
    """Return per-character failure reasons when a voice team cannot be reused.

    Each diagnosis is a dict ``{character_id, character_name, reason, detail}``.
    An empty list means the team is fully reusable.  ``reason`` is one of the
    ``REASON_*`` constants above; UIs group on these to render actionable
    confirm/expire/switch-provider guidance instead of one opaque string.
    """
    if team is None:
        return [
            {
                "character_id": cid,
                "character_name": "",
                "reason": REASON_MISSING,
                "detail": "配音团队不存在，请先在声腔工作室构建",
            }
            for cid in sorted(expected_character_ids)
        ]

    diagnoses: list[dict[str, str]] = []
    for cid in sorted(expected_character_ids):
        entry = team.get_entry(cid)
        if entry is None:
            diagnoses.append(
                {
                    "character_id": cid,
                    "character_name": "",
                    "reason": REASON_MISSING,
                    "detail": "配音团队中无此角色的音色条目",
                }
            )
            continue
        reasons: list[tuple[str, str]] = []
        if entry.provider != resolved_provider:
            reasons.append(
                (
                    REASON_PROVIDER_MISMATCH,
                    f"音色平台 {entry.provider.value} 与当前 {resolved_provider.value} 不一致",
                )
            )
        if entry.clone_status != VoiceCloneStatus.READY:
            reasons.append(
                (
                    REASON_CLONE_NOT_READY,
                    f"克隆状态 {entry.clone_status.value}，未就绪",
                )
            )
        if entry.approval_status != "approved":
            reasons.append(
                (
                    REASON_PENDING_APPROVAL,
                    f"音色待确认（当前 {entry.approval_status}）",
                )
            )
        if entry.is_expired:
            reasons.append(
                (
                    REASON_EXPIRED,
                    "音色已过期或激活截止时间已过",
                )
            )
        # A fully-ready entry contributes no diagnosis; it does not block
        # reuse.  Only emit entries for characters that actually have a
        # problem so an empty list reliably means "reusable".
        for reason, detail in reasons:
            diagnoses.append(
                {
                    "character_id": cid,
                    "character_name": entry.character_name,
                    "reason": reason,
                    "detail": detail,
                }
            )
    return diagnoses


def _diagnose_narrator_reuse(profile: Any, resolved_provider: TTSProvider) -> list[dict[str, str]]:
    """Return narrator-side failure reasons; mirrors ``_diagnose_voice_team_reuse``."""
    if profile is None:
        return [
            {
                "character_id": "",
                "character_name": "旁白",
                "reason": REASON_NARRATOR_UNAVAILABLE,
                "detail": "旁白音色不存在，请先在声腔工作室构建",
            }
        ]
    reasons: list[tuple[str, str]] = []
    if not getattr(profile, "voice_id", ""):
        reasons.append((REASON_NARRATOR_UNAVAILABLE, "旁白音色 ID 为空"))
    if getattr(profile, "is_expired", False):
        reasons.append((REASON_EXPIRED, "旁白音色已过期或激活截止时间已过"))
    if getattr(profile, "provider", None) != resolved_provider:
        reasons.append(
            (
                REASON_PROVIDER_MISMATCH,
                f"旁白平台 {getattr(profile, 'provider', None)} 与当前 {resolved_provider.value} 不一致",
            )
        )
    if not reasons:
        reasons.append((REASON_UNKNOWN, "旁白音色未知原因不可用"))
    return [
        {
            "character_id": "",
            "character_name": "旁白",
            "reason": reason,
            "detail": detail,
        }
        for reason, detail in reasons
    ]


def _clear_activated_voice_deadlines(
    team: VoiceTeamContract,
    voice_ids: set[str],
) -> bool:
    """Clear one-time activation deadlines after any successful provider T2A call."""
    if not voice_ids:
        return False
    changed = False
    entries = []
    for entry in team.entries:
        if entry.activation_deadline is not None and entry.voice_id in voice_ids:
            entries.append(entry.model_copy(update={"activation_deadline": None}))
            changed = True
        else:
            entries.append(entry)
    if changed:
        team.entries = entries
    return changed


def _lock_used_voice_identities(
    team: VoiceTeamContract,
    voice_ids: set[str],
) -> bool:
    """Freeze cast identity after the first successful formal chapter synthesis."""
    if not voice_ids:
        return False
    changed = False
    locked_at = datetime.now(timezone.utc)
    entries = []
    for entry in team.entries:
        if entry.voice_id in voice_ids and not entry.identity_locked:
            entries.append(
                entry.model_copy(
                    update={
                        "identity_locked": True,
                        "identity_locked_at": locked_at,
                        "identity_lock_reason": "first_formal_synthesis",
                    }
                )
            )
            changed = True
        else:
            entries.append(entry)
    if changed:
        team.entries = entries
    return changed


async def _activate_minimax_voice(
    *,
    voice_id: str,
    label: str,
    text: str,
    settings: Settings,
    layout: ProjectLayout,
    on_step_progress: Any = None,
) -> tuple[bool, str]:
    """Use a short formal T2A call to keep a temporary MiniMax voice permanently."""
    adapter = TTSAdapterRegistry.get_instance(settings).get_adapter(TTSProvider.MINIMAX)
    request = TTSRequest(
        text=text.strip() or "这是音色激活试读。",
        voice_id=voice_id,
        model_id=resolve_tts_model(settings, TTSProvider.MINIMAX, purpose="preview"),
        output_format=settings.tts_output_format,
        sample_rate=settings.tts_sample_rate,
        provider=TTSProvider.MINIMAX,
        metadata={
            "voice_identity_lock": True,
            "resource_priority": "interactive",
            "resource_label": f"{label} MiniMax 音色激活",
        },
    )
    try:
        response = await adapter.synthesize(request)
        if not response.audio_data:
            raise RuntimeError("MiniMax returned empty activation audio")
        safe_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in voice_id)
        audio_format = _preview_format(settings, response.audio_format)
        activation_path = layout.tts_audio_dir(0) / f"activation_{safe_id}.{audio_format}"
        _write_preview_audio(activation_path, response.audio_data)
    except Exception as exc:
        _log.warning("MiniMax voice activation failed for %s (%s): %s", label, voice_id, exc)
        _emit_tts_progress(
            on_step_progress,
            "minimax_voice_activation_failed",
            {"voice_id": voice_id, "label": label, "detail": str(exc)},
        )
        return False, ""
    _emit_tts_progress(
        on_step_progress,
        "minimax_voice_activated",
        {"voice_id": voice_id, "label": label, "audio_path": str(activation_path)},
    )
    return True, str(activation_path)


async def _activate_minimax_team_voices(
    *,
    team: VoiceTeamContract,
    settings: Settings,
    layout: ProjectLayout,
    on_step_progress: Any = None,
) -> set[str]:
    """Activate every newly created MiniMax cast voice before the 168h window starts aging."""
    activated: set[str] = set()
    targets = [
        entry
        for entry in team.entries
        if (
            entry.provider == TTSProvider.MINIMAX
            and entry.activation_deadline is not None
            and bool(entry.voice_id)
        )
    ]
    total = len(targets)
    _emit_tts_progress(
        on_step_progress,
        "minimax_voice_activation_batch_start",
        {"completed": 0, "total": total},
    )
    for index, entry in enumerate(targets, start=1):
        _emit_tts_progress(
            on_step_progress,
            "minimax_voice_activation_started",
            {
                "completed": index - 1,
                "total": total,
                "label": entry.character_name,
                "voice_id": entry.voice_id,
            },
        )
        success, audio_path = await _activate_minimax_voice(
            voice_id=entry.voice_id,
            label=entry.character_name,
            text=f"我是{entry.character_name}，这是我的声音。",
            settings=settings,
            layout=layout,
            on_step_progress=on_step_progress,
        )
        if success:
            activated.add(entry.voice_id)
            # The activation audio is a real-voice sample of the designed
            # voice. Persist it as the entry's preview so the author can
            # audition and confirm the voice matches the character (e.g.
            # gender) before formal synthesis. This closes the gap where a
            # MiniMax-designed voice entered synthesis un-auditioned.
            if audio_path and not entry.preview_audio_path:
                activation_text = f"我是{entry.character_name}，这是我的声音。"
                entry.preview_audio_path = audio_path
                entry.preview_text = activation_text
                entry.preview_variants = {
                    **entry.preview_variants,
                    "identity": VariantPreview(
                        audio_path=audio_path,
                        text=activation_text,
                    ),
                }
        _emit_tts_progress(
            on_step_progress,
            "minimax_voice_activation_progress",
            {
                "completed": index,
                "total": total,
                "label": entry.character_name,
                "voice_id": entry.voice_id,
                "success": success,
            },
        )
    _clear_activated_voice_deadlines(team, activated)
    _emit_tts_progress(
        on_step_progress,
        "minimax_voice_activation_batch_done",
        {"completed": total, "total": total, "activated": len(activated)},
    )
    return activated


async def _activate_minimax_narrator(
    *,
    profile: NarratorVoiceProfile,
    settings: Settings,
    layout: ProjectLayout,
    on_step_progress: Any = None,
) -> NarratorVoiceProfile:
    """Activate one newly designed MiniMax narrator and retain its local sample."""
    if (
        profile.provider != TTSProvider.MINIMAX
        or profile.activation_deadline is None
        or not profile.voice_id
    ):
        return profile
    sample = profile.sample_narration_text.strip() or "故事从这一刻开始。"
    success, _ = await _activate_minimax_voice(
        voice_id=profile.voice_id,
        label="旁白",
        text=sample,
        settings=settings,
        layout=layout,
        on_step_progress=on_step_progress,
    )
    if success:
        return profile.model_copy(update={"activation_deadline": None})
    return profile


@_with_tts_project_lock
async def execute_build_narrator_profile(
    *,
    project_id: str,
    settings: Settings,
    layout: ProjectLayout,
    outline: dict[str, Any] | None = None,
    genre: str = "",
    tone: str = "",
    style_profile: dict[str, Any] | None = None,
    story_bible: dict[str, Any] | None = None,
    audio_aesthetic_hint: str = "",
    sample_chapter: str = "",
    narrator_voice_id: str = "",
    provider: str = "",
    on_step_progress: Any = None,
) -> ExecutionResult[dict[str, Any]]:
    """Build narrator voice profile from work metadata.

    Uses LLM to analyze outline, genre, tone, and style profile
    to generate a NarratorVoiceProfile for dynamic narrator voice adaptation.

    Args:
        project_id: Project identifier.
        settings: Application settings.
        layout: Project layout for storage paths.
        outline: Full outline dict (optional).
        genre: Genre string (e.g., '言情', '悬疑', '历史').
        tone: Tone string (e.g., '轻松', '沉重', '紧张').
        style_profile: Style profile dict (optional).
        sample_chapter: Sample chapter text for analysis (optional).
        narrator_voice_id: Override narrator voice ID (optional).
        provider: TTS provider name (default from settings).
        on_step_progress: Optional progress callback.

    Returns:
        ExecutionResult with NarratorVoiceProfile dict.
    """
    _log.info("Building narrator profile for project %s", project_id)
    existing_profile: NarratorVoiceProfile | None = None
    if layout.tts_narrator_profile_path.exists():
        try:
            existing_profile = NarratorVoiceProfile.model_validate(
                _load_json(layout.tts_narrator_profile_path)
            )
        except Exception as exc:
            _log.warning("Ignoring invalid existing narrator profile: %s", exc)
    previous_profile_hash = _narrator_profile_content_hash(existing_profile)

    # Resolve provider
    provider_enum = _resolve_provider(settings, provider)
    await _ensure_managed_tts_runtime(settings, provider_enum)

    # Load existing style_profile from disk if not provided
    if style_profile is None and layout.style_profile_path.exists():
        import json

        data = json.loads(layout.style_profile_path.read_text(encoding="utf-8"))
        style_profile = data

    # Load existing outline from disk if not provided
    if outline is None and layout.outline_path.exists():
        outline = _load_json(layout.outline_path)

    if story_bible is None:
        story_bible = _load_json(layout.bible_path)
    spec = _load_json(layout.spec_path)
    if not genre:
        genre = str(spec.get("genre", "") or "")
    if not tone:
        tone = str(spec.get("tone", "") or story_bible.get("tone", "") or "")
    if not audio_aesthetic_hint:
        audio_aesthetic_hint = str(spec.get("audio_aesthetic_hint", "") or "")
    narrator_project_language = str(spec.get("language") or "zh").strip()

    # Create step - requires router and builder for LLM path
    from novel_forge.gateway.factory import ModelRouterBuilder
    from novel_forge.prompts.builder import PromptBuilder

    router = ModelRouterBuilder(settings).build()
    builder = PromptBuilder()

    _emit_tts_progress(on_step_progress, "tts_narrator_start", {"project_id": project_id})
    _emit_tts_progress(on_step_progress, "tts_narrator_llm_call", {"project_id": project_id})
    step = BuildNarratorProfileStep(
        router=router,
        builder=builder,
        settings=settings,
        on_step=on_step_progress,
    )

    # Build input
    input_data = BuildNarratorProfileInput(
        outline=outline or {},
        genre=genre,
        tone=tone,
        style_profile=style_profile or {},
        story_bible=story_bible or {},
        audio_aesthetic_hint=audio_aesthetic_hint,
        sample_chapter=sample_chapter,
        narrator_voice_id=narrator_voice_id or settings.tts_narrator_voice_id,
        provider=provider_enum,
    )

    # Build the creative profile first, then resolve it to an actual provider
    # voice.  Previously this flow persisted only a prose description, so the
    # UI showed a "标准旁白" row while synthesis fell back to a generic voice.
    profile = await step.run(input_data)
    profile = await _assign_narrator_voice(
        profile=profile,
        settings=settings,
        provider=provider_enum,
        on_step_progress=on_step_progress,
        project_language=narrator_project_language,
    )
    profile = await _activate_minimax_narrator(
        profile=profile,
        settings=settings,
        layout=layout,
        on_step_progress=on_step_progress,
    )
    _emit_tts_progress(
        on_step_progress,
        "tts_narrator_done",
        {"voice_id": profile.voice_id, "source": profile.voice_source},
    )

    # Persist
    layout.tts_narrator_profile_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(layout.tts_narrator_profile_path, profile.model_dump(mode="json"))
    _sync_narrator_voice_team_binding(layout=layout, settings=settings, profile=profile)
    if previous_profile_hash != _narrator_profile_content_hash(profile):
        invalidate_all_tts_audio_derivatives(layout)

    _log.info(
        "Narrator profile built: voice_type=%s, base_speed=%.2f",
        profile.voice_type,
        profile.base_speed,
    )

    return ExecutionResult(
        project_id=project_id,
        result=profile.model_dump(mode="json"),
    )


@_with_tts_project_lock
async def execute_build_voice_team(
    *,
    project_id: str,
    characters: list[dict[str, Any]],
    settings: Settings,
    layout: ProjectLayout,
    narrator_voice_id: str = "",
    provider: str = "",
    on_step_progress: Any = None,
    rebuild_character_ids: list[str] | None = None,
) -> ExecutionResult[dict[str, Any]]:
    """Build or update voice team for a project.

    Args:
        project_id: Project identifier.
        characters: List of character dicts with name, gender, age, etc.
        settings: Application settings.
        layout: Project layout for storage paths.
        narrator_voice_id: Voice ID for narrator.
        provider: TTS provider name (default from settings).
        on_step_progress: Optional progress callback.
        rebuild_character_ids: None=normal build/reuse; non-empty list=force rebuild for those IDs.

    Returns:
        ExecutionResult with VoiceTeamContract dict.
    """
    _log.info("Building voice team for project %s", project_id)

    # Resolve provider
    provider_enum = _resolve_provider(settings, provider)
    _emit_tts_progress(
        on_step_progress,
        "voice_team_runtime_check_start",
        {"provider": provider_enum.value},
    )
    await _ensure_managed_tts_runtime(settings, provider_enum)
    _emit_tts_progress(
        on_step_progress,
        "voice_team_runtime_check_done",
        {"provider": provider_enum.value},
    )

    # Load existing team if present
    existing_team: VoiceTeamContract | None = None
    if layout.tts_voice_team_path.exists():
        try:
            existing_team = VoiceTeamContract.model_validate(_load_json(layout.tts_voice_team_path))
        except Exception as exc:
            _log.warning("Ignoring invalid existing voice team: %s", exc)
    previous_team_hash = _voice_team_content_hash(existing_team)

    narrator_profile: NarratorVoiceProfile | None = None
    if layout.tts_narrator_profile_path.exists():
        try:
            narrator_profile = NarratorVoiceProfile.model_validate(
                _load_json(layout.tts_narrator_profile_path)
            )
        except Exception as exc:
            _log.warning("Ignoring invalid narrator profile: %s", exc)
    profile_voice_id = ""
    if narrator_profile and not narrator_profile.is_expired:
        if narrator_profile.provider == provider_enum:
            profile_voice_id = narrator_profile.voice_id
        elif narrator_profile.voice_id:
            _log.info(
                "Ignoring narrator voice from %s while building %s team",
                narrator_profile.provider.value,
                provider_enum.value,
            )
    effective_narrator_voice_id = (
        narrator_voice_id or profile_voice_id or settings.tts_narrator_voice_id
    )

    # This is a read snapshot used for matching.  Mutations are merged after
    # the build under the separate global-library lock below.
    voice_library = VoiceLibrary.load(settings.storage_root)

    upstream = _load_tts_upstream_context(layout, chapter_number=1)
    characters = _merge_character_inputs(
        characters,
        upstream["characters"],
        upstream["character_voices"],
    )

    # Extract the project's primary language from the spec for voice filtering.
    project_language = str(upstream.get("spec", {}).get("language") or "zh").strip()
    # Inject project_language into each character dict so that the matching
    # layer (assess_voice_match) can enforce language compatibility as a hard
    # constraint even if catalog-level filtering is bypassed.
    for character in characters:
        if not character.get("project_language"):
            character["project_language"] = project_language

    if not characters:
        # A finalized prose chapter can legitimately have no structured cast
        # yet (for example a first-person interlude).  The narrator is a full
        # TTS mode in its own right, not an error path.  Persisting an explicit
        # empty cast also prevents a stale team from being silently reused.
        team = VoiceTeamContract(
            entries=[],
            narrator_voice_id=effective_narrator_voice_id,
            narrator_provider=provider_enum,
            default_tts_model=settings.tts_default_model,
            default_provider=provider_enum,
        )
        layout.tts_voice_team_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(layout.tts_voice_team_path, team.model_dump(mode="json"))
        if previous_team_hash != _voice_team_content_hash(team):
            invalidate_all_tts_audio_derivatives(layout)
        _emit_tts_progress(
            on_step_progress,
            "tts_voice_team_narrator_only",
            {"characters": 0, "provider": provider_enum.value},
        )
        return ExecutionResult(project_id=project_id, result=team.model_dump(mode="json"))

    # Create registry and step
    registry = TTSAdapterRegistry.get_instance(settings)
    router = None
    builder = None
    if settings.tts_voice_llm_adjudication_enabled:
        # Keep the normal cast build model-free.  The LLM router is created
        # only when the optional low-confidence review has been explicitly
        # enabled in Voice Studio settings.
        from novel_forge.gateway.factory import ModelRouterBuilder
        from novel_forge.prompts.builder import PromptBuilder

        router = ModelRouterBuilder(settings).build()
        builder = PromptBuilder()
    step = BuildVoiceTeamStep(
        registry,
        settings=settings,
        on_step=on_step_progress,
        router=router,
        builder=builder,
    )

    # Build input
    input_data = BuildVoiceTeamInput(
        characters=characters,
        narrator_voice_id=effective_narrator_voice_id,
        default_provider=provider_enum,
        existing_team=existing_team,
        parallel_clone=(
            settings.tts_parallel_voice_clone and _allows_parallel_voice_build(provider_enum)
        ),
        voice_design_enabled=settings.tts_voice_design_enabled,
        voice_library=voice_library,
        rebuild_character_ids=rebuild_character_ids,
        project_id=project_id,
        voice_library_scope=str(
            getattr(settings, "tts_voice_library_scope", "project_only") or "project_only"
        ),
        project_language=project_language,
    )

    # Execute
    team = await step.run(input_data)
    activated_voice_ids = await _activate_minimax_team_voices(
        team=team,
        settings=settings,
        layout=layout,
        on_step_progress=on_step_progress,
    )

    # Persist voice team
    layout.tts_voice_team_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(layout.tts_voice_team_path, team.model_dump(mode="json"))
    if previous_team_hash != _voice_team_content_hash(team):
        invalidate_all_tts_audio_derivatives(layout)

    # Merge newly designed voices and recorded library hits into the global
    # library.  The success event is intentionally emitted only after disk
    # persistence completes.
    saved_library_entries = await _persist_voice_library_updates(
        storage_root=settings.storage_root,
        new_entries=input_data.new_library_entries,
        usage=input_data.library_usage,
    )
    await _clear_voice_library_activation_deadlines(
        storage_root=settings.storage_root,
        voice_ids=activated_voice_ids,
        provider=provider_enum,
    )
    for entry in saved_library_entries:
        _emit_tts_progress(
            on_step_progress,
            "voice_library_saved",
            {"character_name": entry.character_name, "voice_id": entry.voice_id},
        )

    _log.info(
        "Voice team built: %d entries, %d ready",
        len(team.entries),
        len(team.get_ready_entries()),
    )

    return ExecutionResult(
        project_id=project_id,
        result=team.model_dump(mode="json"),
    )


async def execute_list_tts_voices(
    *,
    project_id: str,
    settings: Settings,
    provider: str = "",
    gender: str | None = None,
    limit: int = 200,
) -> ExecutionResult[dict[str, Any]]:
    """Return a provider catalog together with explicit feature capabilities."""
    provider_enum = _resolve_provider(settings, provider)
    await _ensure_managed_tts_runtime(settings, provider_enum)
    adapter = TTSAdapterRegistry.get_instance(settings).get_adapter(provider_enum)
    capabilities = await adapter.discover_capabilities()
    voices = await adapter.list_system_voices(gender=gender, limit=max(1, min(limit, 500)))
    return ExecutionResult(
        project_id=project_id,
        result={
            "provider": provider_enum.value,
            "capabilities": capabilities.model_dump(mode="json"),
            "voices": voices,
        },
    )


@_with_interactive_preview_tts_lock
async def execute_preview_character_voice(
    *,
    project_id: str,
    character_id: str,
    sample_text: str,
    settings: Settings,
    layout: ProjectLayout,
    provider: str = "",
) -> ExecutionResult[dict[str, Any]]:
    """Synthesize or reuse an exact character-specific preview.

    Preview audio is content-addressed by all synthesis-relevant inputs.  This
    keeps a prior audition playable across app restarts while ensuring that a
    voice, provider, parameter, output-format, or sample-text change receives
    a fresh file.
    """
    team = _load_voice_team(layout)
    entry = team.get_entry(character_id) if team else None
    if entry is None or not entry.voice_id:
        return ExecutionResult(project_id=project_id, result={"error": "Character voice not found"})
    assert team is not None
    provider_enum = _resolve_provider(settings, provider or entry.provider.value)
    text = sample_text.strip() or f"我是{entry.character_name}，这是我的声音。"
    performance = resolve_character_performance(
        entry,
        provider_enum,
        text=text,
        default_speed=settings.tts_default_speed,
    )
    cache_key = _stable_hash(
        {
            "kind": "character",
            "character_id": character_id,
            "voice_id": entry.voice_id,
            "provider": provider_enum.value,
            "model_id": entry.model_id
            or resolve_tts_model(settings, provider_enum, purpose="preview"),
            "text": text,
            "speed": performance.speed,
            "pitch": performance.pitch,
            "volume": performance.volume,
            "performance_sources": {
                "speed": performance.speed_source,
                "pitch": performance.pitch_source,
                "volume": performance.volume_source,
            },
            "output_format": settings.tts_output_format,
            "sample_rate": settings.tts_sample_rate,
        }
    )
    cached_path = _existing_preview_cache_path(
        layout, target_id=character_id, fingerprint=cache_key
    )
    if cached_path is not None:
        return ExecutionResult(
            project_id=project_id,
            result={
                "audio_path": str(cached_path),
                "provider": provider_enum.value,
                "voice_id": entry.voice_id,
                "cached": True,
                "effective_parameters": {
                    "speed": performance.speed,
                    "pitch": performance.pitch,
                    "volume": performance.volume,
                },
                "performance_warnings": list(performance.warnings),
            },
        )

    await _ensure_managed_tts_runtime(settings, provider_enum)
    adapter = TTSAdapterRegistry.get_instance(settings).get_adapter(provider_enum)
    request = TTSRequest(
        text=text,
        voice_id=entry.voice_id,
        model_id=entry.model_id or resolve_tts_model(settings, provider_enum, purpose="preview"),
        speed=performance.speed,
        pitch=performance.pitch,
        volume=performance.volume,
        output_format=settings.tts_output_format,
        sample_rate=settings.tts_sample_rate,
        provider=provider_enum,
        metadata={
            "resource_priority": "interactive",
            "resource_label": f"{entry.character_name} 音色试听",
            "speaker_kind": "角色",
            "character_name": entry.character_name,
            "performance_offset_sources": {
                "speed": performance.speed_source,
                "pitch": performance.pitch_source,
                "volume": performance.volume_source,
            },
            "short_utterance_stabilized": performance.short_utterance_stabilized,
            "performance_provider_guard_applied": performance.provider_guard_applied,
            "performance_warnings": list(performance.warnings),
        },
    )
    response = await _synthesize_preview_with_timeout(
        adapter=adapter,
        request=request,
        settings=settings,
    )
    if not response.audio_data:
        return ExecutionResult(
            project_id=project_id, result={"error": "Provider returned empty audio"}
        )
    preview_format = _preview_format(settings, response.audio_format)
    preview_path = _preview_cache_path(
        layout,
        target_id=character_id,
        fingerprint=cache_key,
        audio_format=preview_format,
    )
    audio_data = await _apply_preview_audio_controls(
        settings=settings,
        audio_data=response.audio_data,
        request=request,
        audio_format=response.audio_format or settings.tts_output_format,
        label=f"{entry.character_name} 试听后处理",
    )
    _write_preview_audio(preview_path, audio_data)
    if entry.activation_deadline is not None:
        if _clear_activated_voice_deadlines(team, {entry.voice_id}):
            atomic_write_json(layout.tts_voice_team_path, team.model_dump(mode="json"))
        await _clear_voice_library_activation_deadlines(
            storage_root=settings.storage_root,
            voice_ids={entry.voice_id},
            provider=provider_enum,
        )
    return ExecutionResult(
        project_id=project_id,
        result={
            "audio_path": str(preview_path),
            "duration_ms": response.duration_ms,
            "provider": provider_enum.value,
            "voice_id": entry.voice_id,
            "cached": False,
            "effective_parameters": {
                "speed": performance.speed,
                "pitch": performance.pitch,
                "volume": performance.volume,
            },
            "performance_warnings": list(performance.warnings),
        },
    )


@_with_tts_project_lock
async def execute_prepare_voice_team_previews(
    *,
    project_id: str,
    characters: list[dict[str, Any]],
    settings: Settings,
    layout: ProjectLayout,
    provider: str = "",
    character_ids: list[str] | None = None,
    on_step_progress: Any = None,
) -> ExecutionResult[dict[str, Any]]:
    """Prepare one stable, cached audition for every selected cast member.

    Preview failure is intentionally non-fatal: the voice assignment remains
    usable and the per-character error is persisted for transparent UI repair.
    """
    team = _load_voice_team(layout)
    if team is None:
        return ExecutionResult(project_id=project_id, result={"error": "Voice team not found"})
    character_map = {
        str(item.get("character_id") or item.get("name") or ""): item
        for item in characters
        if str(item.get("character_id") or item.get("name") or "")
    }
    selected_ids = set(character_ids) if character_ids is not None else None
    # Allow pending (un-auditioned) designed/cloned voices to receive a batch
    # preview. is_ready requires approval_status=="approved", which blocks
    # newly designed voices ("pending") from ever getting their first audition
    # audio — creating a dead-lock where the author cannot confirm the voice
    # because no preview exists. clone_status==READY alone is sufficient for a
    # safe preview synthesis.
    targets = [
        entry
        for entry in team.entries
        if entry.clone_status == VoiceCloneStatus.READY
        and not entry.is_expired
        and (selected_ids is None or entry.character_id in selected_ids)
    ]
    total = len(targets)
    _emit_tts_progress(on_step_progress, "voice_preview_batch_start", {"total": total})

    for index, target in enumerate(targets, start=1):
        character = character_map.get(
            target.character_id,
            {"character_id": target.character_id, "name": target.character_name},
        )
        preview_text = build_voice_preview_text(character)
        audio_path = ""
        preview_error = ""
        cached = False
        _emit_tts_progress(
            on_step_progress,
            "voice_preview_started",
            {
                "completed": index - 1,
                "total": total,
                "character_id": target.character_id,
                "character_name": target.character_name,
            },
        )
        if (
            target.get_variant_preview("identity").audio_path
            and target.get_variant_preview("identity").text == preview_text
            and Path(target.get_variant_preview("identity").audio_path).is_file()
        ):
            audio_path = target.get_variant_preview("identity").audio_path
            cached = True
        else:
            try:
                preview = await execute_preview_character_voice(
                    project_id=project_id,
                    character_id=target.character_id,
                    sample_text=preview_text,
                    settings=settings,
                    layout=layout,
                    provider=provider or target.provider.value,
                )
                if "error" in preview.result:
                    preview_error = str(preview.result["error"])
                else:
                    audio_path = str(preview.result.get("audio_path") or "")
                    cached = bool(preview.result.get("cached"))
            except Exception as exc:
                preview_error = str(exc)

        # Preview synthesis may have cleared an activation deadline, so always
        # merge into the latest persisted team rather than a stale snapshot.
        latest_team = _load_voice_team(layout) or team
        updated_entries: list[VoiceCastEntry] = []
        for entry in latest_team.entries:
            if entry.character_id == target.character_id:
                entry = entry.with_variant_preview(
                    "identity",
                    audio_path=audio_path,
                    text=preview_text,
                    error=preview_error,
                )
            updated_entries.append(entry)
        team = latest_team.model_copy(
            update={
                "entries": updated_entries,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        atomic_write_json(layout.tts_voice_team_path, team.model_dump(mode="json"))
        _emit_tts_progress(
            on_step_progress,
            "voice_preview_ready" if audio_path else "voice_preview_failed",
            {
                "completed": index,
                "total": total,
                "character_id": target.character_id,
                "character_name": target.character_name,
                "cached": cached,
                "detail": preview_error,
            },
        )

    _emit_tts_progress(
        on_step_progress,
        "voice_preview_batch_done",
        {
            "completed": total,
            "total": total,
            "ready": sum(
                bool(
                    any(vp.audio_path for vp in entry.preview_variants.values())
                    or entry.preview_audio_path
                )
                for entry in team.entries
            ),
        },
    )
    return ExecutionResult(project_id=project_id, result=team.model_dump(mode="json"))


@_with_interactive_preview_tts_lock
async def execute_preview_narrator_voice(
    *,
    project_id: str,
    settings: Settings,
    layout: ProjectLayout,
    sample_text: str = "",
    provider: str = "",
) -> ExecutionResult[dict[str, Any]]:
    """Synthesize or reuse the persisted LLM-designed narrator preview."""
    profile: NarratorVoiceProfile | None = None
    if layout.tts_narrator_profile_path.exists():
        try:
            profile = NarratorVoiceProfile.model_validate(
                _load_json(layout.tts_narrator_profile_path)
            )
        except Exception as exc:
            _log.warning("Invalid narrator profile %s: %s", layout.tts_narrator_profile_path, exc)
    if profile and profile.is_expired:
        return ExecutionResult(
            project_id=project_id,
            result={"error": "Narrator voice expired. Rebuild the narrator voice first."},
        )
    team = _load_voice_team(layout)
    voice_id = str(
        (profile.voice_id if profile else "")
        or (team.narrator_voice_id if team else "")
        or settings.tts_narrator_voice_id
    ).strip()
    if not voice_id:
        return ExecutionResult(
            project_id=project_id,
            result={"error": "Narrator voice not configured"},
        )

    provider_name = provider or (profile.provider.value if profile else "")
    provider_enum = _resolve_provider(settings, provider_name)
    text = (
        sample_text.strip()
        or (profile.sample_narration_text if profile else "").strip()
        or "故事从这一刻开始，声音将陪伴你走进人物的命运与世界。"
    )
    speed = max(0.5, min(2.0, profile.base_speed if profile else 1.0))
    pitch = profile.pitch_offset if profile else 0
    volume = max(0.0, min(2.0, 1.0 + (profile.vol_offset if profile else 0.0)))
    cache_key = _stable_hash(
        {
            "kind": "narrator",
            "voice_id": voice_id,
            "provider": provider_enum.value,
            "model_id": (profile.model_id if profile else "")
            or resolve_tts_model(settings, provider_enum, purpose="preview"),
            "text": text,
            "speed": speed,
            "pitch": pitch,
            "volume": volume,
            "output_format": settings.tts_output_format,
            "sample_rate": settings.tts_sample_rate,
        }
    )
    cached_path = _existing_preview_cache_path(layout, target_id="narrator", fingerprint=cache_key)
    if cached_path is not None:
        return ExecutionResult(
            project_id=project_id,
            result={
                "audio_path": str(cached_path),
                "provider": provider_enum.value,
                "voice_id": voice_id,
                "cached": True,
            },
        )

    await _ensure_managed_tts_runtime(settings, provider_enum)
    adapter = TTSAdapterRegistry.get_instance(settings).get_adapter(provider_enum)
    request = TTSRequest(
        text=text,
        voice_id=voice_id,
        model_id=(profile.model_id if profile else "")
        or resolve_tts_model(settings, provider_enum, purpose="preview"),
        speed=speed,
        pitch=pitch,
        volume=volume,
        output_format=settings.tts_output_format,
        sample_rate=settings.tts_sample_rate,
        provider=provider_enum,
        metadata={
            "resource_priority": "interactive",
            "resource_label": "旁白音色试听",
            "speaker_kind": "旁白",
            "tone_hint": "自然、连贯、有叙事感",
        },
    )
    response = await _synthesize_preview_with_timeout(
        adapter=adapter,
        request=request,
        settings=settings,
    )
    if not response.audio_data:
        return ExecutionResult(
            project_id=project_id, result={"error": "Provider returned empty audio"}
        )
    preview_path = _preview_cache_path(
        layout,
        target_id="narrator",
        fingerprint=cache_key,
        audio_format=_preview_format(settings, response.audio_format),
    )
    audio_data = await _apply_preview_audio_controls(
        settings=settings,
        audio_data=response.audio_data,
        request=request,
        audio_format=response.audio_format or settings.tts_output_format,
        label="旁白试听后处理",
    )
    _write_preview_audio(preview_path, audio_data)
    if profile is not None and profile.activation_deadline is not None:
        profile = profile.model_copy(update={"activation_deadline": None})
        atomic_write_json(layout.tts_narrator_profile_path, profile.model_dump(mode="json"))
    return ExecutionResult(
        project_id=project_id,
        result={
            "audio_path": str(preview_path),
            "duration_ms": response.duration_ms,
            "provider": provider_enum.value,
            "voice_id": voice_id,
            "cached": False,
        },
    )


@_with_tts_project_lock
async def execute_clone_character_voice(
    *,
    project_id: str,
    character_id: str,
    reference_audio: str,
    settings: Settings,
    layout: ProjectLayout,
    provider: str = "",
    reference_transcript: str = "",
    authorized: bool = False,
) -> ExecutionResult[dict[str, Any]]:
    """Clone and atomically persist one character voice through the workspace boundary."""
    team = _load_voice_team(layout)
    entry = team.get_entry(character_id) if team else None
    if team is None or entry is None:
        return ExecutionResult(
            project_id=project_id, result={"error": "Character is not in voice team"}
        )
    previous_team_hash = _voice_team_content_hash(team)
    provider_enum = _resolve_provider(settings, provider)
    await _ensure_managed_tts_runtime(settings, provider_enum)
    adapter = TTSAdapterRegistry.get_instance(settings).get_adapter(provider_enum)
    capabilities = await adapter.discover_capabilities()
    if not capabilities.voice_clone:
        return ExecutionResult(
            project_id=project_id,
            result={"error": f"{provider_enum.value} does not support voice cloning"},
        )
    if provider_enum != TTSProvider.MOCK and not authorized:
        return ExecutionResult(
            project_id=project_id,
            result={"error": "声音复刻需要明确确认已获得参考说话人授权"},
        )
    source_path = Path(reference_audio).expanduser()
    if source_path.is_file() and not capabilities.local_reference_audio:
        return ExecutionResult(
            project_id=project_id,
            result={"error": f"{provider_enum.value} 需要公网可访问的参考音频 URL"},
        )
    clone_source = await adapter.prepare_clone_source(reference_audio)
    voice_id = f"nf-{_stable_hash({'project': project_id, 'character': character_id})}"
    response = await adapter.clone_voice(
        VoiceCloneRequest(
            voice_id=voice_id,
            file_id=clone_source,
            model_id=resolve_tts_model(settings, provider_enum, purpose="clone"),
            clone_prompt=entry.clone_prompt,
            reference_transcript=reference_transcript,
            authorized=authorized,
            provider=provider_enum,
        )
    )
    if response.status != VoiceCloneStatus.READY or not response.voice_id:
        return ExecutionResult(
            project_id=project_id,
            result={"error": response.message or "Voice cloning failed"},
        )
    if provider_enum == TTSProvider.MINIMAX and response.activation_deadline is not None:
        activated, _ = await _activate_minimax_voice(
            voice_id=response.voice_id,
            label=entry.character_name,
            text=f"我是{entry.character_name}，这是我的声音。",
            settings=settings,
            layout=layout,
        )
        if activated:
            response = response.model_copy(update={"activation_deadline": None})
    _replace_voice_entry(
        team,
        character_id,
        voice_id=response.voice_id,
        model_id=response.model_id,
        provider=provider_enum,
        clone_status=VoiceCloneStatus.READY,
        voice_source="cloned",
        expires_at=response.expires_at,
        activation_deadline=response.activation_deadline,
        reference_audio_path=str(source_path) if source_path.is_file() else "",
        reference_transcript=reference_transcript,
        match_score=None,
        match_reasons=["使用已授权参考音频建立角色声纹"],
        match_warnings=["克隆还原度需要通过试听人工确认"],
        preview_audio_path="",
        preview_text="",
        preview_error="",
    )
    atomic_write_json(layout.tts_voice_team_path, team.model_dump(mode="json"))
    if previous_team_hash != _voice_team_content_hash(team):
        invalidate_all_tts_audio_derivatives(layout)
    return ExecutionResult(project_id=project_id, result=team.model_dump(mode="json"))


@_with_tts_project_lock
async def execute_design_character_voice(
    *,
    project_id: str,
    character_id: str,
    description: str,
    settings: Settings,
    layout: ProjectLayout,
    provider: str = "",
    on_step_progress: Any = None,
) -> ExecutionResult[dict[str, Any]]:
    """Design and atomically persist a character-derived voice."""
    team = _load_voice_team(layout)
    entry = team.get_entry(character_id) if team else None
    if team is None or entry is None:
        return ExecutionResult(
            project_id=project_id, result={"error": "Character is not in voice team"}
        )
    previous_team_hash = _voice_team_content_hash(team)
    provider_enum = _resolve_provider(settings, provider)
    await _ensure_managed_tts_runtime(settings, provider_enum)
    adapter = TTSAdapterRegistry.get_instance(settings).get_adapter(provider_enum)
    capabilities = await adapter.discover_capabilities()
    if not capabilities.voice_design:
        return ExecutionResult(
            project_id=project_id,
            result={"error": f"{provider_enum.value} does not support voice design"},
        )
    upstream = _load_tts_upstream_context(layout, chapter_number=1)
    merged = _merge_character_inputs(
        [{"character_id": entry.character_id, "name": entry.character_name}],
        upstream["characters"],
        upstream["character_voices"],
    )
    trait_brief = build_voice_design_prompt(merged[0]) if merged else ""
    canonical_brief = trait_brief or entry.voice_design_prompt or entry.clone_prompt
    editorial_note = description.strip()
    if canonical_brief and editorial_note and editorial_note != canonical_brief:
        brief = (f"{canonical_brief}；制作补充（不得覆盖上述角色特质）：{editorial_note}")[:2400]
    else:
        brief = canonical_brief or editorial_note
    if not brief:
        return ExecutionResult(
            project_id=project_id,
            result={"error": "Upstream character traits are insufficient for voice design"},
        )
    character = merged[0] if merged else {"name": entry.character_name}
    preview_text = build_voice_preview_text(character)
    response = await adapter.design_voice(
        VoiceDesignRequest(
            description=brief,
            preview_text=preview_text,
            model_id=resolve_tts_model(settings, provider_enum, purpose="design"),
            provider=provider_enum,
        )
    )
    if response.status != VoiceCloneStatus.READY or not response.voice_id:
        return ExecutionResult(
            project_id=project_id,
            result={"error": response.message or "Voice design failed"},
        )
    if provider_enum == TTSProvider.MINIMAX and response.activation_deadline is not None:
        activated, _ = await _activate_minimax_voice(
            voice_id=response.voice_id,
            label=entry.character_name,
            text=f"我是{entry.character_name}，这是我的声音。",
            settings=settings,
            layout=layout,
            on_step_progress=on_step_progress,
        )
        if activated:
            response = response.model_copy(update={"activation_deadline": None})
    assessment = designed_voice_assessment(character)
    _replace_voice_entry(
        team,
        character_id,
        voice_id=response.voice_id,
        model_id=response.model_id,
        provider=provider_enum,
        clone_status=VoiceCloneStatus.READY,
        voice_source="designed",
        voice_design_prompt=brief,
        clone_prompt=brief,
        expires_at=response.expires_at,
        activation_deadline=response.activation_deadline,
        match_score=assessment.score,
        match_reasons=list(assessment.reasons),
        match_warnings=list(assessment.warnings),
        preview_audio_path="",
        preview_text=preview_text,
        preview_error="",
    )
    preview_path = ""
    if response.preview_audio_data:
        safe_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in character_id)
        preview_format = str(response.preview_audio_format or "mp3").strip().lower()
        if preview_format not in {"mp3", "wav", "flac", "m4a", "ogg"}:
            preview_format = "mp3"
        path = layout.tts_audio_dir(0) / f"design_{safe_id}.{preview_format}"
        _write_preview_audio(path, response.preview_audio_data)
        preview_path = str(path)
        _replace_voice_entry(
            team,
            character_id,
            preview_audio_path=preview_path,
            preview_text=preview_text,
            preview_error="",
        )
    team.preview_audio_path = preview_path
    atomic_write_json(layout.tts_voice_team_path, team.model_dump(mode="json"))
    if previous_team_hash != _voice_team_content_hash(team):
        invalidate_all_tts_audio_derivatives(layout)
    if merged:
        saved_library_entries = await _persist_voice_library_updates(
            storage_root=settings.storage_root,
            new_entries=[
                entry_from_cast(
                    response.voice_id,
                    provider_enum,
                    merged[0],
                    brief,
                    response.expires_at,
                    response.activation_deadline,
                    # Without provenance the entry is persisted as "legacy" and
                    # becomes invisible under the default project_only scope —
                    # including to the very project that just designed it.
                    project_id=project_id,
                    model_id=response.model_id,
                )
            ],
            usage=[],
        )
        for saved_entry in saved_library_entries:
            _emit_tts_progress(
                on_step_progress,
                "voice_library_saved",
                {
                    "character_name": saved_entry.character_name,
                    "voice_id": saved_entry.voice_id,
                },
            )
    if preview_path:
        _log.info("Designed voice preview saved: %s", preview_path)
    return ExecutionResult(project_id=project_id, result=team.model_dump(mode="json"))


@_with_tts_project_lock
async def execute_approve_character_voice(
    *,
    project_id: str,
    character_id: str,
    settings: Settings,
    layout: ProjectLayout,
    on_step_progress: Any = None,
) -> ExecutionResult[dict[str, Any]]:
    """Mark a pending cast entry as audition-approved so it can enter synthesis.

    Designed/cloned voices are created with ``approval_status="pending"`` to
    force the author to audition the preview before formal use. This flips the
    status to ``"approved"`` and persists the team. Audio derivatives are NOT
    invalidated: approval only unblocks synthesis; it does not change the
    voice identity.
    """
    team = _load_voice_team(layout)
    entry = team.get_entry(character_id) if team else None
    if team is None or entry is None:
        return ExecutionResult(
            project_id=project_id, result={"error": "Character is not in voice team"}
        )
    if entry.approval_status == "approved":
        # Idempotent: already approved, nothing to do.
        return ExecutionResult(project_id=project_id, result=team.model_dump(mode="json"))
    _replace_voice_entry(team, character_id, approval_status="approved")
    atomic_write_json(layout.tts_voice_team_path, team.model_dump(mode="json"))
    _log.info(
        "Voice approved for character %s (voice_id=%s)",
        entry.character_name,
        entry.voice_id,
    )
    return ExecutionResult(project_id=project_id, result=team.model_dump(mode="json"))


@_with_tts_project_lock
async def execute_update_voice_performance(
    *,
    project_id: str,
    character_id: str,
    speed_offset: float,
    pitch_offset: int,
    volume_offset: float,
    layout: ProjectLayout,
) -> ExecutionResult[dict[str, Any]]:
    """Persist character or narrator performance offsets used by preview and synthesis."""
    team = _load_voice_team(layout)
    if team is None:
        return ExecutionResult(project_id=project_id, result={"error": "配音团队不存在"})
    if character_id == "narrator":
        profile = (
            NarratorVoiceProfile.model_validate(_load_json(layout.tts_narrator_profile_path))
            if layout.tts_narrator_profile_path.exists()
            else NarratorVoiceProfile(provider=team.narrator_provider)
        )
        updated_profile = profile.model_copy(
            update={
                "base_speed": 1.0 + speed_offset,
                "pitch_offset": pitch_offset,
                "vol_offset": volume_offset,
            }
        )
        atomic_write_json(
            layout.tts_narrator_profile_path,
            updated_profile.model_dump(mode="json"),
        )
    else:
        entry = team.get_entry(character_id)
        if entry is None:
            return ExecutionResult(project_id=project_id, result={"error": "角色不在配音团队中"})
        updated = with_manual_performance_overrides(
            entry,
            speed_offset=speed_offset,
            pitch_offset=pitch_offset,
            vol_offset=volume_offset,
        ).model_copy(
            update={
                "preview_audio_path": "",
                "preview_text": "",
                "preview_error": "",
                "preview_variants": {},
            }
        )
        team.entries = [
            updated if item.character_id == character_id else item for item in team.entries
        ]
        team.confirmed = False
        team.confirmed_at = None
        atomic_write_json(layout.tts_voice_team_path, team.model_dump(mode="json"))
    invalidate_all_tts_audio_derivatives(layout)
    return ExecutionResult(
        project_id=project_id,
        result={
            "character_id": character_id,
            "speed_offset": speed_offset,
            "pitch_offset": pitch_offset,
            "volume_offset": volume_offset,
        },
    )


@_with_tts_project_lock
async def execute_assign_catalog_voice(
    *,
    project_id: str,
    character_id: str,
    voice_id: str,
    provider: str,
    settings: Settings,
    layout: ProjectLayout,
) -> ExecutionResult[dict[str, Any]]:
    """Persist an author-selected system voice after validating the live catalog.

    This is the shared write boundary for the React and PySide Voice Studio
    selection controls.  It intentionally clears cached auditions and the
    project-level team confirmation: a new voice identity must be auditioned
    again before it can enter formal synthesis.
    """

    voice_id = voice_id.strip()
    if not voice_id:
        return ExecutionResult(project_id=project_id, result={"error": "请选择一个有效音色"})

    team = _load_voice_team(layout)
    default_provider = ""
    if team is not None:
        default_provider = str(getattr(team.default_provider, "value", team.default_provider) or "")
    provider_enum = _resolve_provider(settings, provider or default_provider)
    await _ensure_managed_tts_runtime(settings, provider_enum)
    adapter = TTSAdapterRegistry.get_instance(settings).get_adapter(provider_enum)
    catalog = await adapter.list_system_voices(limit=500)
    selected = next(
        (
            item
            for item in catalog
            if isinstance(item, dict) and str(item.get("voice_id") or "").strip() == voice_id
        ),
        None,
    )
    if selected is None:
        return ExecutionResult(
            project_id=project_id,
            result={"error": "所选音色不在当前平台目录中，请刷新音色目录后重试"},
        )

    if character_id == "narrator":
        profile = (
            NarratorVoiceProfile.model_validate(_load_json(layout.tts_narrator_profile_path))
            if layout.tts_narrator_profile_path.exists()
            else NarratorVoiceProfile(provider=provider_enum)
        )
        updated_profile = profile.model_copy(
            update={
                "voice_id": voice_id,
                "provider": provider_enum,
                "voice_source": "manual",
                "expires_at": None,
                "activation_deadline": None,
            }
        )
        atomic_write_json(layout.tts_narrator_profile_path, updated_profile.model_dump(mode="json"))
        if team is None:
            team = VoiceTeamContract(
                narrator_voice_id=voice_id,
                narrator_provider=provider_enum,
                default_provider=provider_enum,
                default_tts_model=resolve_tts_model(settings, provider_enum),
            )
        else:
            team.narrator_voice_id = voice_id
            team.narrator_provider = provider_enum
        team.confirmed = False
        team.confirmed_at = None
        atomic_write_json(layout.tts_voice_team_path, team.model_dump(mode="json"))
    else:
        if team is None:
            return ExecutionResult(project_id=project_id, result={"error": "配音团队不存在，请先组建团队"})
        entry = team.get_entry(character_id)
        if entry is None:
            return ExecutionResult(project_id=project_id, result={"error": "角色不在配音团队中"})
        updated_entry = entry.model_copy(
            update={
                "voice_id": voice_id,
                "clone_status": VoiceCloneStatus.READY,
                "provider": provider_enum,
                "voice_source": "manual",
                "match_score": None,
                "match_reasons": ["使用作者明确指定的系统音色"],
                "match_warnings": ["请通过试听人工确认角色特质与指定音色是否一致"],
                "llm_adjudication_status": "not_reviewed",
                "llm_adjudication_confidence": None,
                "llm_adjudication_reason": "",
                "audition_candidate_voice_ids": [],
                "approval_status": "approved",
                "expires_at": None,
                "activation_deadline": None,
                "preview_audio_path": "",
                "preview_text": "",
                "preview_error": "",
                "preview_variants": {},
            }
        )
        team.entries = [
            updated_entry if item.character_id == character_id else item for item in team.entries
        ]
        team.confirmed = False
        team.confirmed_at = None
        atomic_write_json(layout.tts_voice_team_path, team.model_dump(mode="json"))

    invalidate_all_tts_audio_derivatives(layout)
    return ExecutionResult(
        project_id=project_id,
        result={
            "character_id": character_id,
            "voice_id": voice_id,
            "voice_label": str(selected.get("name") or voice_id),
            "provider": provider_enum.value,
        },
    )


@_with_tts_project_lock
async def execute_confirm_voice_team(
    *,
    project_id: str,
    settings: Settings,
    layout: ProjectLayout,
    on_step_progress: Any = None,
) -> ExecutionResult[dict[str, Any]]:
    """Mark the entire voice team as author-confirmed for auto-dubbing.

    Pre-checks every entry is ready + approved + unexpired + provider-consistent
    and the narrator is present; on failure returns the same structured
    ``diagnoses`` shape as ``execute_full_tts_pipeline`` so the UI can render
    per-character guidance.  On success sets ``confirmed=True`` and persists,
    which lets subsequent post-archive runs short-circuit the per-entry check.

    This is the project-level counterpart to ``execute_approve_character_voice``:
    the latter approves one voice, this approves the cast as a unit.  Approving
    a single voice afterwards resets the whole-team confirmation (via
    ``_replace_voice_entry``) because the cast composition changed.
    """
    team = _load_voice_team(layout)
    if team is None:
        return ExecutionResult(
            project_id=project_id,
            result={
                "error": "配音团队不存在，请先在声腔工作室构建",
                "error_code": "tts_voice_team_missing",
                "diagnoses": [],
            },
        )
    resolved_provider = team.default_provider
    # Reuse the same diagnoser as the post-archive path so "why can't I confirm"
    # and "why did auto-dub fail" always agree on per-character reasons.
    expected_character_ids = {entry.character_id for entry in team.entries}
    diagnoses = _diagnose_voice_team_reuse(team, expected_character_ids, resolved_provider)
    # Narrator readiness is a separate gate; surface it as its own diagnosis.
    if not team.narrator_voice_id:
        diagnoses.insert(
            0,
            {
                "character_id": "",
                "character_name": "旁白",
                "reason": REASON_NARRATOR_UNAVAILABLE,
                "detail": "旁白音色 ID 为空，请先构建旁白",
            },
        )
    if diagnoses:
        return ExecutionResult(
            project_id=project_id,
            result={
                "error": "团队中仍有音色未就绪，无法确认",
                "error_code": "tts_voice_team_confirm_blocked",
                "diagnoses": diagnoses,
                "confirmable_character_ids": [
                    d["character_id"]
                    for d in diagnoses
                    if d.get("reason") == REASON_PENDING_APPROVAL and d.get("character_id")
                ],
            },
        )
    team.confirmed = True
    team.confirmed_at = datetime.now(timezone.utc)
    atomic_write_json(layout.tts_voice_team_path, team.model_dump(mode="json"))
    _log.info(
        "Voice team confirmed for project %s (%d entries)",
        project_id,
        len(team.entries),
    )
    if on_step_progress is not None:
        _emit_tts_progress(
            on_step_progress,
            "tts_voice_team_confirmed",
            {"project": project_id, "entries": len(team.entries)},
        )
    return ExecutionResult(project_id=project_id, result=team.model_dump(mode="json"))
