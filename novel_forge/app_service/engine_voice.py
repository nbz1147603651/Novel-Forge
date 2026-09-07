"""Voice-module read models for replaceable UI clients."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from pydantic import Field

from novel_forge.app_service.contracts import VOICE_DURABLE_JOB_KINDS
from novel_forge.app_service.engine_views import ENGINE_CONTRACT_VERSION, EngineViewModel
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.platform.provider_catalog import tts_provider_catalog
from novel_forge.tts.runtime.performance_policy import voice_performance_profile
from novel_forge.tts.schemas import (
    ChapterAudioResult,
    ChapterTakeManifest,
    DubbingScript,
    NarratorVoiceProfile,
    SoundAsset,
    TakeReviewStatus,
    VoiceCastEntry,
    VoiceTeamContract,
)
from novel_forge.tts.script_integrity import (
    DubbingScriptFreshness,
    assess_dubbing_script_freshness,
    compute_dubbing_script_hash,
    source_text_hash_matches,
    unresolved_speaker_indices,
)
from novel_forge.workspace.projects import ProjectDetail

VOICE_ACTIVE_TASK_KIND_BY_JOB_KIND: dict[
    str, Literal["team_build", "script_generation", "synthesis"]
] = {
    "tts_build_voice_team": "team_build",
    "tts_generate_script": "script_generation",
    "tts_synthesize": "synthesis",
    "tts_full_pipeline": "synthesis",
}

VoiceArtifactFreshness = Literal["missing", "current", "stale", "legacy", "source_missing"]


class EngineVoiceDetailFactView(EngineViewModel):
    """A small, credential-free fact shown in the voiceprint dossier."""

    label: str
    value: str


class EngineVoiceActiveTaskView(EngineViewModel):
    """A resumable voice operation projected without implementation details."""

    id: str
    kind: Literal["team_build", "script_generation", "synthesis"]


class EngineVoiceCastMemberView(EngineViewModel):
    id: str
    name: str
    role: str = ""
    status_label: str
    voice_label: str = ""
    description: str = ""
    voice_id: str = ""
    voice_source_label: str = ""
    speed_offset: float = 0.0
    pitch_offset: int = 0
    volume_offset: float = 0.0
    performance_policy_label: str = ""
    match_summary: str = ""
    # These preserve the PySide detail panel's explainability without exposing
    # local paths, provider credentials, or full unbounded artifact payloads.
    detail_facts: list[EngineVoiceDetailFactView] = Field(default_factory=list)
    match_reasons: list[str] = Field(default_factory=list)
    audition_warnings: list[str] = Field(default_factory=list)
    audition_text: str = ""
    design_brief: str = ""


class EngineVoiceCatalogOptionView(EngineViewModel):
    """A provider catalog option safe to render in the Voice Studio picker."""

    id: str
    label: str
    description: str = ""


class EngineVoiceCatalogView(EngineViewModel):
    project_id: str
    provider_label: str
    options: list[EngineVoiceCatalogOptionView] = Field(default_factory=list)


class EngineVoiceSpeakerCandidateView(EngineViewModel):
    character_id: str
    character_name: str
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""


class EngineVoiceScriptSegmentView(EngineViewModel):
    id: str
    segment_index: int
    speaker_id: str = ""
    speaker_label: str
    kind_label: str
    emotion_label: str
    content: str
    status_label: str
    needs_speaker_review: bool = False
    context_before: str = ""
    context_after: str = ""
    speaker_candidates: list[EngineVoiceSpeakerCandidateView] = Field(default_factory=list)


class EngineVoiceRoomTakeView(EngineViewModel):
    """One current, durable Voice Room state for a script segment."""

    segment_index: int = Field(ge=0)
    state: Literal["guidance_saved", "candidate", "accepted"]
    take_id: str = ""
    audio_url: str = ""
    guidance: dict[str, Any] | None = None


class EngineVoiceSoundAssetView(EngineViewModel):
    id: str
    kind: str
    name: str
    status: str
    scope: str
    source: str
    tags: list[str] = Field(default_factory=list)
    provider: str = ""
    model: str = ""
    license: str = ""
    commercial_use_status: str = "review_required"
    prompt: str = ""
    audio_url: str = ""


class EngineVoiceMixTrackView(EngineViewModel):
    id: str
    label: str
    duration_ms: int = 0
    event_count: int = 0
    failed_event_count: int = 0
    status: str = "not_started"
    stem_audio_url: str = ""


class EngineVoiceProviderSettingOptionView(EngineViewModel):
    value: str
    label: str


class EngineVoiceProviderSettingView(EngineViewModel):
    parameter_id: str
    label: str
    description: str
    kind: str
    default_value: str = ""
    options: list[EngineVoiceProviderSettingOptionView] = Field(default_factory=list)
    model_purpose: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    advanced: bool = False
    experimental: bool = False
    visible_model_ids: list[str] = Field(default_factory=list)
    visibility_parameter_id: str = ""
    visibility_values: list[str] = Field(default_factory=list)


class EngineVoiceProviderModelView(EngineViewModel):
    id: str
    label: str
    purposes: list[str] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    voice_clone: bool = False
    voice_design: bool = False
    system_voice_catalog: bool = False
    local_reference_audio: bool = False
    adapter_supported: bool = True
    recommended: bool = False
    max_input_chars: int = 0
    notes: list[str] = Field(default_factory=list)


class EngineVoiceProviderView(EngineViewModel):
    id: str
    label: str
    aliases: list[str] = Field(default_factory=list)
    official_docs_url: str = ""
    default_model: str = ""
    active_model_parameter_id: str = ""
    is_local: bool = False
    adapter_status: str = "production"
    highlights: list[str] = Field(default_factory=list)
    models: list[EngineVoiceProviderModelView] = Field(default_factory=list)
    settings: list[EngineVoiceProviderSettingView] = Field(default_factory=list)


class EngineVoiceStudioView(EngineViewModel):
    contract_version: str = ENGINE_CONTRACT_VERSION
    project_id: str
    project_title: str
    provider_label: str
    configured_model_label: str
    provider_catalog: list[EngineVoiceProviderView] = Field(default_factory=list)
    chapter_number: int
    available_chapters: list[int] = Field(default_factory=list)
    team_confirmed: bool = False
    script_fresh: bool = False
    novel_source_state: Literal["ready", "blocked", "missing"] = "missing"
    script_freshness: VoiceArtifactFreshness = "missing"
    audio_freshness: VoiceArtifactFreshness = "missing"
    freshness_blocking_reasons: list[str] = Field(default_factory=list)
    unresolved_speaker_count: int = 0
    audio_ready: bool = False
    subtitle_ready: bool = False
    delivery_state: str = "not_started"
    active_task_id: str | None = None
    active_tasks: list[EngineVoiceActiveTaskView] = Field(default_factory=list)
    chapter_audio_url: str | None = None
    subtitle_text: str = ""
    mix_tracks: list[EngineVoiceMixTrackView] = Field(default_factory=list)
    sound_assets: list[EngineVoiceSoundAssetView] = Field(default_factory=list)
    cast: list[EngineVoiceCastMemberView] = Field(default_factory=list)
    script: list[EngineVoiceScriptSegmentView] = Field(default_factory=list)
    room_takes: list[EngineVoiceRoomTakeView] = Field(default_factory=list)


def _scan_available_chapters(layout: ProjectLayout) -> list[int]:
    """Scan chapters_dir for archived chapter_*.md files and return sorted chapter numbers."""
    chapters_dir = layout.chapters_dir
    if not chapters_dir.exists():
        return []
    chapters: list[int] = []
    for f in sorted(chapters_dir.glob("chapter_*.md")):
        try:
            num = int(f.stem.split("_")[1])
            chapters.append(num)
        except (IndexError, ValueError):
            pass
    chapters.sort()
    return chapters


def project_voice_studio_view(
    detail: ProjectDetail,
    *,
    chapter_number: int,
    voice_team: VoiceTeamContract | None,
    script: DubbingScript | None,
    characters: list[dict[str, Any]],
    segment_statuses: dict[int, str],
    audio_result: ChapterAudioResult | None,
    active_task_id: str | None,
    default_provider: str,
    default_model: str,
    layout: ProjectLayout | None = None,
    narrator_profile: NarratorVoiceProfile | None = None,
    sound_assets: list[SoundAsset] | None = None,
    take_manifest: ChapterTakeManifest | None = None,
    active_tasks: list[EngineVoiceActiveTaskView] | None = None,
    chapter_text: str = "",
    publication_ready: bool = True,
    publication_blocking_reasons: list[str] | None = None,
) -> EngineVoiceStudioView:
    """Build a credential-free Voice Studio read model from validated artifacts."""

    provider = (
        _enum_value(voice_team.default_provider) if voice_team is not None else default_provider
    )
    model = voice_team.default_tts_model if voice_team is not None else default_model
    unresolved = set(unresolved_speaker_indices(script)) if script is not None else set()
    character_names = {
        str(item.get("id") or item.get("character_id") or ""): str(
            item.get("name") or item.get("character_name") or ""
        )
        for item in characters
    }
    if voice_team is not None:
        character_names.update(
            {
                entry.character_id: entry.character_name
                for entry in voice_team.entries
                if entry.character_id
            }
        )
    speaker_decisions: dict[int, list[EngineVoiceSpeakerCandidateView]] = {}
    if script is not None:
        adjudication = script.metadata.get("speaker_adjudication", {})
        raw_decisions = adjudication.get("decisions", []) if isinstance(adjudication, dict) else []
        for decision in raw_decisions if isinstance(raw_decisions, list) else []:
            if not isinstance(decision, dict):
                continue
            try:
                segment_index = int(decision.get("segment_index"))
                confidence = max(0.0, min(1.0, float(decision.get("confidence") or 0.0)))
            except (TypeError, ValueError):
                continue
            character_id = str(decision.get("character_id") or "")
            if not character_id:
                continue
            speaker_decisions.setdefault(segment_index, []).append(
                EngineVoiceSpeakerCandidateView(
                    character_id=character_id,
                    character_name=character_names.get(character_id, character_id),
                    confidence=confidence,
                    reason=str(decision.get("rationale") or decision.get("reason") or ""),
                )
            )
    source_state: Literal["ready", "blocked", "missing"] = (
        "missing" if not chapter_text else "ready" if publication_ready else "blocked"
    )
    script_freshness = _script_freshness(script, chapter_text)
    audio_freshness = _audio_freshness(audio_result, script, chapter_text)
    freshness_blocking_reasons = list(publication_blocking_reasons or [])
    if script_freshness == "stale":
        freshness_blocking_reasons.append("stale_dubbing_script")
    elif script_freshness == "legacy":
        freshness_blocking_reasons.append("legacy_dubbing_script")
    if audio_freshness == "stale":
        freshness_blocking_reasons.append("stale_tts_audio_result")
    elif audio_freshness == "legacy":
        freshness_blocking_reasons.append("legacy_tts_audio_result")
    if source_state == "missing":
        freshness_blocking_reasons.append("chapter_source_missing")
    freshness_blocking_reasons = list(dict.fromkeys(freshness_blocking_reasons))
    audio_ready = bool(
        audio_result is not None
        and audio_result.delivery_ready
        and audio_result.assembled_audio_path
        and audio_freshness == "current"
        and source_state == "ready"
    )
    subtitle_ready = bool(
        audio_result is not None
        and audio_result.subtitle_path
        and audio_freshness == "current"
        and source_state == "ready"
    )
    if active_task_id or active_tasks:
        delivery_state = "in_progress"
    elif audio_ready:
        delivery_state = "ready"
    elif audio_result is not None or source_state == "blocked":
        delivery_state = "blocked"
    else:
        delivery_state = "not_started"
    segments = (
        []
        if script is None
        else [
            EngineVoiceScriptSegmentView(
                id=str(item.segment_index),
                segment_index=item.segment_index,
                speaker_id=item.character_id,
                speaker_label=item.character_name
                or ("旁白" if not item.character_id else item.character_id),
                kind_label=_segment_kind_label(_enum_value(item.segment_type)),
                emotion_label=_emotion_label(_enum_value(item.emotion)),
                content=item.text,
                status_label=_segment_status_label(
                    segment_statuses.get(item.segment_index, "pending")
                ),
                needs_speaker_review=item.segment_index in unresolved,
                context_before=script.segments[position - 1].text if position > 0 else "",
                context_after=(
                    script.segments[position + 1].text
                    if position < len(script.segments) - 1
                    else ""
                ),
                speaker_candidates=sorted(
                    speaker_decisions.get(item.segment_index, []),
                    key=lambda candidate: candidate.confidence,
                    reverse=True,
                ),
            )
            for position, item in enumerate(script.segments)
        ]
    )
    available_chapters = _scan_available_chapters(layout) if layout is not None else []
    subtitle_text = ""
    if audio_result is not None and audio_result.subtitle_path:
        subtitle_path = Path(audio_result.subtitle_path)
        try:
            subtitle_text = (
                subtitle_path.read_text(encoding="utf-8") if subtitle_path.is_file() else ""
            )
        except OSError:
            subtitle_text = ""
    mix_tracks = _voice_mix_track_views(
        audio_result,
        project_id=detail.project_id,
        chapter_number=chapter_number,
    )
    sound_asset_views = [
        EngineVoiceSoundAssetView(
            id=asset.asset_id,
            kind=asset.kind,
            name=asset.display_name,
            status=asset.approval_status,
            scope=asset.scope,
            source=asset.source,
            tags=list(asset.tags),
            provider=asset.generation_provider,
            model=asset.generation_model,
            license=asset.license_note,
            commercial_use_status=asset.commercial_use_status,
            prompt=asset.generation_prompt,
            audio_url=(
                f"/api/v1/engine/voice/projects/{quote(detail.project_id, safe='')}"
                f"/sound-assets/{quote(asset.asset_id, safe='')}/audio"
            ),
        )
        for asset in (sound_assets or [])
    ]
    return EngineVoiceStudioView(
        project_id=detail.project_id,
        project_title=detail.title,
        provider_label=provider or "mock",
        configured_model_label=model,
        provider_catalog=_voice_provider_catalog_views(),
        chapter_number=chapter_number,
        available_chapters=available_chapters,
        team_confirmed=bool(voice_team is not None and voice_team.confirmed),
        script_fresh=script_freshness == "current" and source_state == "ready",
        novel_source_state=source_state,
        script_freshness=script_freshness,
        audio_freshness=audio_freshness,
        freshness_blocking_reasons=freshness_blocking_reasons,
        unresolved_speaker_count=len(unresolved),
        audio_ready=audio_ready,
        subtitle_ready=subtitle_ready,
        delivery_state=delivery_state,
        active_task_id=active_task_id,
        active_tasks=list(active_tasks or []),
        chapter_audio_url=(
            f"/api/v1/engine/voice/projects/{quote(detail.project_id, safe='')}"
            f"/chapters/{chapter_number}/audio"
            if audio_ready
            else None
        ),
        subtitle_text=subtitle_text,
        mix_tracks=mix_tracks,
        sound_assets=sound_asset_views,
        cast=_voice_cast_views(voice_team, characters, narrator_profile),
        script=segments,
        room_takes=_voice_room_take_views(
            take_manifest,
            project_id=detail.project_id,
            chapter_number=chapter_number,
        ),
    )


def _script_freshness(
    script: DubbingScript | None,
    chapter_text: str,
) -> VoiceArtifactFreshness:
    if script is None:
        return "missing"
    return assess_dubbing_script_freshness(script, chapter_text).value


def _audio_freshness(
    audio_result: ChapterAudioResult | None,
    script: DubbingScript | None,
    chapter_text: str,
) -> VoiceArtifactFreshness:
    if audio_result is None:
        return "missing"
    if not chapter_text:
        return DubbingScriptFreshness.SOURCE_MISSING.value
    metadata = audio_result.metadata
    source_hash = str(
        metadata.get("source_text_hash") or audio_result.script.source_text_hash or ""
    ).strip()
    if not source_hash:
        return DubbingScriptFreshness.LEGACY.value
    if not source_text_hash_matches(source_hash, chapter_text):
        return DubbingScriptFreshness.STALE.value
    if script is not None:
        expected_script_hash = script.script_hash or compute_dubbing_script_hash(script)
        artifact_script_hash = str(
            metadata.get("script_hash")
            or audio_result.script.script_hash
            or compute_dubbing_script_hash(audio_result.script)
        ).strip()
        if artifact_script_hash != expected_script_hash:
            return DubbingScriptFreshness.STALE.value
    return DubbingScriptFreshness.CURRENT.value


def _voice_room_take_views(
    manifest: ChapterTakeManifest | None,
    *,
    project_id: str,
    chapter_number: int,
) -> list[EngineVoiceRoomTakeView]:
    """Expose only the latest actionable take or saved guidance per segment.

    Rejected history remains an Engine audit artifact, not a current Voice Room
    state. A live candidate takes precedence over an accepted historical take.
    """

    if manifest is None:
        return []

    current: dict[int, EngineVoiceRoomTakeView] = {}
    for take in manifest.takes:
        status = str(getattr(take.status, "value", take.status))
        if status == TakeReviewStatus.CANDIDATE.value:
            current[take.segment_index] = EngineVoiceRoomTakeView(
                segment_index=take.segment_index,
                state="candidate",
                take_id=take.take_id,
                audio_url=(
                    f"/api/v1/engine/voice/projects/{quote(project_id, safe='')}"
                    f"/chapters/{chapter_number}/takes/{quote(take.take_id, safe='')}/audio"
                ),
                guidance=_voice_room_guidance(take.segment),
            )
        elif status == TakeReviewStatus.ACCEPTED.value and take.segment_index not in current:
            current[take.segment_index] = EngineVoiceRoomTakeView(
                segment_index=take.segment_index,
                state="accepted",
                take_id=take.take_id,
            )

    for raw_index, draft in manifest.drafts.items():
        try:
            segment_index = int(raw_index)
        except (TypeError, ValueError):
            continue
        current.setdefault(
            segment_index,
            EngineVoiceRoomTakeView(
                segment_index=segment_index,
                state="guidance_saved",
                guidance=_voice_room_guidance(draft),
            ),
        )
    return [current[index] for index in sorted(current)]


def _voice_room_guidance(segment: Any) -> dict[str, Any]:
    """Project only editable performance controls, never source text or extensions."""

    emotion = getattr(segment, "emotion", "")
    return {
        "emotion": str(getattr(emotion, "value", emotion)),
        "emotion_intensity": float(getattr(segment, "emotion_intensity", 0.5)),
        "tone_hint": str(getattr(segment, "tone_hint", "")),
        "speed_override": getattr(segment, "speed_override", None),
        "volume_override": getattr(segment, "vol_override", None),
        "pitch_override": getattr(segment, "pitch_override", None),
        "stress_words": list(getattr(segment, "stress_words", []) or []),
        "narrator_distance": str(getattr(segment, "narrator_distance", "")),
        "pronunciation_overrides": list(getattr(segment, "pronunciation_overrides", []) or []),
        "language_code": str(getattr(segment, "language_code", "auto")),
    }


def _voice_provider_catalog_views() -> list[EngineVoiceProviderView]:
    """Project the canonical credential-free catalog into the Engine contract."""

    return [
        EngineVoiceProviderView(
            id=provider.provider_id,
            label=provider.label,
            aliases=list(provider.aliases),
            official_docs_url=provider.official_docs_url,
            default_model=provider.default_model,
            active_model_parameter_id=provider.active_model_parameter_id,
            is_local=provider.is_local,
            adapter_status=provider.adapter_status,
            highlights=list(provider.highlights),
            models=[
                EngineVoiceProviderModelView(
                    id=model.model_id,
                    label=model.label,
                    purposes=list(model.purposes),
                    features=sorted(feature.value for feature in model.synthesis_features),
                    voice_clone=model.voice_clone,
                    voice_design=model.voice_design,
                    system_voice_catalog=model.system_voice_catalog,
                    local_reference_audio=model.local_reference_audio,
                    adapter_supported=model.adapter_supported,
                    recommended=model.recommended,
                    max_input_chars=model.max_input_chars,
                    notes=list(model.notes),
                )
                for model in provider.models
            ],
            settings=[
                EngineVoiceProviderSettingView(
                    parameter_id=setting.parameter_id,
                    label=setting.label,
                    description=setting.description,
                    kind=setting.kind,
                    default_value=setting.default_value,
                    options=[
                        EngineVoiceProviderSettingOptionView(
                            value=option.value,
                            label=option.label,
                        )
                        for option in setting.options
                    ],
                    model_purpose=setting.model_purpose,
                    minimum=setting.minimum,
                    maximum=setting.maximum,
                    step=setting.step,
                    advanced=setting.advanced,
                    experimental=setting.experimental,
                    visible_model_ids=list(setting.visible_model_ids),
                    visibility_parameter_id=setting.visibility_parameter_id,
                    visibility_values=list(setting.visibility_values),
                )
                for setting in provider.settings
            ],
        )
        for provider in tts_provider_catalog()
    ]


def _voice_mix_track_views(
    audio_result: ChapterAudioResult | None,
    *,
    project_id: str = "",
    chapter_number: int = 1,
) -> list[EngineVoiceMixTrackView]:
    labels = {
        "voice": "人声主轨",
        "bgm": "背景音乐",
        "soundscape": "环境声",
        "sfx": "短音效",
    }
    report = audio_result.mix_render_report if audio_result is not None else None
    if report is None:
        return [
            EngineVoiceMixTrackView(id=track_id, label=label) for track_id, label in labels.items()
        ]
    tracks: list[EngineVoiceMixTrackView] = []
    stem_by_track = {"voice": "voice", "bgm": "bed", "soundscape": "bed", "sfx": "sfx"}
    for track_id, label in labels.items():
        events = [item for item in report.events if item.bus == track_id]
        failed_count = sum(item.status == "failed" for item in events)
        stem_name = stem_by_track[track_id]
        stem_audio_url = (
            f"/api/v1/engine/voice/projects/{quote(project_id, safe='')}"
            f"/chapters/{chapter_number}/stems/{stem_name}"
            if project_id and stem_name in report.stem_paths
            else ""
        )
        tracks.append(
            EngineVoiceMixTrackView(
                id=track_id,
                label=label,
                duration_ms=report.duration_ms,
                event_count=len(events),
                failed_event_count=failed_count,
                status=(
                    "blocked"
                    if failed_count
                    else "ready"
                    if report.passed and (track_id == "voice" or events)
                    else "not_started"
                ),
                stem_audio_url=stem_audio_url,
            )
        )
    return tracks


def _voice_cast_views(
    voice_team: VoiceTeamContract | None,
    characters: list[dict[str, Any]],
    narrator_profile: NarratorVoiceProfile | None,
) -> list[EngineVoiceCastMemberView]:
    character_by_id = {
        str(item.get("character_id") or item.get("id") or item.get("name") or "").strip(): item
        for item in characters
    }
    views: list[EngineVoiceCastMemberView] = []
    covered: set[str] = set()
    if voice_team is not None and voice_team.narrator_voice_id:
        views.append(
            EngineVoiceCastMemberView(
                id="narrator",
                name="旁白",
                role="narrator",
                status_label="已配置",
                voice_label=voice_team.narrator_voice_id,
                description=(
                    narrator_profile.notes
                    if narrator_profile is not None and narrator_profile.notes
                    else f"{_enum_value(voice_team.narrator_provider)} 旁白音色"
                ),
                voice_id=voice_team.narrator_voice_id,
                voice_source_label=(
                    _voice_source_label(narrator_profile.voice_source)
                    if narrator_profile is not None
                    else "作品级旁白"
                ),
                speed_offset=(narrator_profile.base_speed - 1.0) if narrator_profile else 0.0,
                pitch_offset=narrator_profile.pitch_offset if narrator_profile else 0,
                volume_offset=narrator_profile.vol_offset if narrator_profile else 0.0,
                performance_policy_label=(
                    "旁白参数使用作品级默认设置。"
                    if narrator_profile is None
                    else "旁白参数已写入作品级叙述档案。"
                ),
                detail_facts=_narrator_detail_facts(narrator_profile),
                match_reasons=(
                    list(narrator_profile.style_keywords[:4])
                    if narrator_profile is not None
                    else []
                ),
                audition_text=(narrator_profile.sample_narration_text if narrator_profile else ""),
                design_brief=(narrator_profile.voice_design_prompt if narrator_profile else ""),
            )
        )
    for entry in voice_team.entries if voice_team is not None else []:
        character = character_by_id.get(entry.character_id, {})
        covered.add(entry.character_id)
        performance = voice_performance_profile(entry)
        configured = performance.configured_offsets
        views.append(
            EngineVoiceCastMemberView(
                id=entry.character_id,
                name=entry.character_name,
                role=str(character.get("role") or "").strip(),
                status_label=_voice_entry_status(entry),
                voice_label=entry.voice_id,
                description="；".join(
                    [*entry.match_reasons, *entry.match_warnings, entry.notes][:4]
                ).strip("；"),
                voice_id=entry.voice_id,
                voice_source_label=_voice_source_label(entry.voice_source),
                speed_offset=configured.speed_offset,
                pitch_offset=configured.pitch_offset,
                volume_offset=configured.vol_offset,
                performance_policy_label=_performance_policy_label(entry),
                match_summary=_match_summary(entry),
                detail_facts=_voice_detail_facts(entry, character),
                match_reasons=list(entry.match_reasons),
                audition_warnings=list(entry.match_warnings),
                audition_text=entry.get_variant_preview("identity").text or entry.preview_text,
                design_brief=entry.voice_design_prompt or entry.clone_prompt,
            )
        )
    for character_id, character in character_by_id.items():
        if not character_id or character_id in covered:
            continue
        views.append(
            EngineVoiceCastMemberView(
                id=character_id,
                name=str(character.get("name") or character_id),
                role=str(character.get("role") or ""),
                status_label="待配音",
                description=str(character.get("voice") or character.get("personality") or "")[:800],
            )
        )
    return views


def _voice_detail_facts(
    entry: VoiceCastEntry,
    character: dict[str, Any],
) -> list[EngineVoiceDetailFactView]:
    """Project the legacy detail panel's audit facts into bounded web data."""

    identity = " · ".join(
        value
        for value in (
            entry.character_role or str(character.get("role") or "").strip(),
            entry.character_gender or str(character.get("gender") or "").strip(),
            entry.character_age or str(character.get("age") or "").strip(),
        )
        if value
    )
    personality = character.get("personality") or character.get("traits") or ""
    if isinstance(personality, list):
        personality = "、".join(str(item).strip() for item in personality if str(item).strip())
    if personality:
        identity = " · ".join(value for value in (identity, str(personality).strip()) if value)

    facts: list[EngineVoiceDetailFactView] = []
    if identity:
        facts.append(EngineVoiceDetailFactView(label="角色依据", value=identity[:360]))
    if entry.llm_adjudication_status != "not_reviewed":
        confidence = (
            f" · {entry.llm_adjudication_confidence:.0%}"
            if entry.llm_adjudication_confidence is not None
            else ""
        )
        rationale = f"：{entry.llm_adjudication_reason}" if entry.llm_adjudication_reason else ""
        facts.append(
            EngineVoiceDetailFactView(
                label="LLM 选角",
                value=f"{entry.llm_adjudication_status}{confidence}{rationale}"[:360],
            )
        )
    if entry.audition_candidate_voice_ids:
        facts.append(
            EngineVoiceDetailFactView(
                label="对比试听候选",
                value="、".join(entry.audition_candidate_voice_ids[:6]),
            )
        )
    if entry.quality_score > 0:
        facts.append(
            EngineVoiceDetailFactView(label="合成质量", value=f"{entry.quality_score:.0%}"),
        )
    if entry.expires_at is not None:
        facts.append(
            EngineVoiceDetailFactView(label="有效期", value=entry.expires_at.isoformat()),
        )
    if entry.activation_deadline is not None:
        facts.append(
            EngineVoiceDetailFactView(
                label="激活截止", value=entry.activation_deadline.isoformat()
            ),
        )
    if entry.notes:
        facts.append(EngineVoiceDetailFactView(label="人工备注", value=entry.notes[:360]))
    return facts


def _narrator_detail_facts(
    profile: NarratorVoiceProfile | None,
) -> list[EngineVoiceDetailFactView]:
    if profile is None:
        return []
    facts = [
        EngineVoiceDetailFactView(label="音色类型", value=profile.voice_type or "作品级旁白"),
        EngineVoiceDetailFactView(
            label="叙述范围",
            value=f"{profile.emotional_range} · {profile.narration_distance}",
        ),
    ]
    if profile.expires_at is not None:
        facts.append(
            EngineVoiceDetailFactView(label="有效期", value=profile.expires_at.isoformat())
        )
    if profile.notes:
        facts.append(EngineVoiceDetailFactView(label="声音说明", value=profile.notes[:360]))
    return facts


def _voice_entry_status(entry: VoiceCastEntry) -> str:
    if entry.is_expired:
        return "已过期"
    if entry.approval_status == "pending":
        return "待试听确认"
    if entry.is_ready:
        return "已就绪"
    return {
        "failed": "准备失败",
        "cloning": "正在准备",
        "ready": "待确认",
    }.get(_enum_value(entry.clone_status), "待配音")


def _voice_source_label(value: str) -> str:
    return {
        "system": "系统音色库",
        "designed": "AI 特征设计",
        "cloned": "参考音频克隆",
        "manual": "人工指定",
        "library": "全局音色库",
    }.get(value, value or "待分配")


def _performance_policy_label(entry: VoiceCastEntry) -> str:
    profile = voice_performance_profile(entry)
    fields = profile.manual_overrides.active_fields
    if not fields:
        return "自动策略：保持角色声纹稳定。"
    labels = {"speed": "语速", "pitch": "音调", "volume": "音量"}
    applied = "、".join(labels[field] for field in ("speed", "pitch", "volume") if field in fields)
    return f"人工覆盖：{applied}；其余参数保持自动策略。"


def _match_summary(entry: VoiceCastEntry) -> str:
    if entry.llm_adjudication_status == "recast":
        confidence = entry.llm_adjudication_confidence
        return f"LLM 改配{f' · {confidence:.0%}' if confidence is not None else ''}"
    if entry.llm_adjudication_status == "needs_audition":
        return "需对比试听"
    if entry.llm_adjudication_status == "rejected":
        return "候选已否决"
    if entry.match_score is not None:
        return f"画像匹配 {entry.match_score:.0%}"
    return "人工分配 / 未评估"


def _segment_kind_label(value: str) -> str:
    return {
        "narration": "旁白",
        "dialogue": "对白",
        "inner_thought": "内心独白",
        "bgm": "背景音乐",
        "sfx": "音效",
        "silence": "停顿",
    }.get(value, value or "文本")


def _emotion_label(value: str) -> str:
    return {
        "neutral": "平静",
        "happy": "欣喜",
        "sad": "悲伤",
        "angry": "愤怒",
        "fearful": "恐惧",
        "surprised": "惊讶",
        "tender": "温柔",
        "anxious": "紧张",
    }.get(value, value or "平静")


def _segment_status_label(value: str) -> str:
    return {
        "completed": "已合成",
        "failed": "合成失败",
        "synthesizing": "合成中",
        "skipped": "已跳过",
        "pending": "待合成",
    }.get(value.strip().lower(), value or "待合成")


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


__all__ = [
    "VOICE_DURABLE_JOB_KINDS",
    "VOICE_ACTIVE_TASK_KIND_BY_JOB_KIND",
    "EngineVoiceActiveTaskView",
    "EngineVoiceCastMemberView",
    "EngineVoiceRoomTakeView",
    "EngineVoiceScriptSegmentView",
    "EngineVoiceStudioView",
    "project_voice_studio_view",
]
