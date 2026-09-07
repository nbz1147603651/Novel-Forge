"""Commercial film-production domain contracts shared by API and UI.

The models intentionally keep story lineage, visual identity, voice performance,
shot language, execution dependencies and editorial timing in one recoverable
project state.  They are provider-neutral; provider task payloads live behind
``film.providers``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from novel_forge.film.providers.base import FilmGenerationMode, FilmProviderTask


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProductionMode(str, Enum):
    COLLABORATIVE = "collaborative"
    AUTONOMOUS = "autonomous"


class FilmStage(str, Enum):
    PLANNING = "planning"
    SCREENPLAY = "screenplay"
    VISUAL_DEVELOPMENT = "visual_development"
    STORYBOARD = "storyboard"
    SHOT_PRODUCTION = "shot_production"
    SOUND_PICTURE = "sound_picture"
    EDIT = "edit"
    COMPLIANCE = "compliance"
    DELIVERY = "delivery"


FILM_STAGE_ORDER: tuple[FilmStage, ...] = tuple(FilmStage)


class FilmStageStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    ACTIVE = "active"
    REVIEW = "review"
    COMPLETED = "completed"
    BLOCKED = "blocked"


class ArtifactSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_type: str
    relative_path: str
    revision: str = ""
    exists: bool = False
    fields_used: list[str] = Field(default_factory=list)
    source_text_hash: str = ""
    input_signature: str = "legacy_unknown"
    output_version: int = Field(default=0, ge=0)
    quality_status: str = "legacy_unknown"
    derivation_status: str = "legacy_unknown"


class ScreenIdentityLock(BaseModel):
    """Stable visual anchors that every character asset and shot must preserve."""

    facial_anchors: list[str] = Field(default_factory=list)
    silhouette: str = ""
    body_language: str = ""
    costume_palette: list[str] = Field(default_factory=list)
    signature_props: list[str] = Field(default_factory=list)
    continuity_rules: list[str] = Field(default_factory=list)
    forbidden_drift: list[str] = Field(default_factory=list)
    visual_state_timeline: list[str] = Field(default_factory=list)
    reference_asset_urls: list[str] = Field(default_factory=list)


class VoicePerformanceLock(BaseModel):
    """Connects the novel character, approved TTS voice and screen performance."""

    provider: str = ""
    voice_id: str = ""
    model_id: str = ""
    timbre: str = ""
    vocal_register: str = ""
    cadence: str = ""
    accent: str = ""
    emotion_range: list[str] = Field(default_factory=list)
    pronunciation_notes: list[str] = Field(default_factory=list)
    delivery_rules: list[str] = Field(default_factory=list)
    reference_audio_path: str = ""


class ProductionCharacter(BaseModel):
    character_id: str
    name: str
    role: str = ""
    dramatic_function: str = ""
    age: str = ""
    gender: str = ""
    personality: str = ""
    arc: str = ""
    shot_language_seed: str = ""
    screen_identity: ScreenIdentityLock = Field(default_factory=ScreenIdentityLock)
    voice_performance: VoicePerformanceLock = Field(default_factory=VoicePerformanceLock)


class ProductionLocation(BaseModel):
    location_id: str
    name: str
    dramatic_function: str = ""
    geography: str = ""
    era: str = ""
    spatial_layout: str = ""
    materials: list[str] = Field(default_factory=list)
    practical_lights: list[str] = Field(default_factory=list)
    weather_states: list[str] = Field(default_factory=list)
    recurring_props: list[str] = Field(default_factory=list)
    ambient_sound: list[str] = Field(default_factory=list)
    continuity_rules: list[str] = Field(default_factory=list)
    shot_language_seed: str = ""
    color_mood: str = ""
    key_light: str = ""
    reference_asset_urls: list[str] = Field(default_factory=list)


class FilmStyleLock(BaseModel):
    visual_thesis: str = ""
    genre: str = ""
    tone: str = ""
    aspect_ratio: str = "16:9"
    frame_rate: float = 24.0
    color_script: list[str] = Field(default_factory=list)
    lighting_rules: list[str] = Field(default_factory=list)
    lens_language: list[str] = Field(default_factory=list)
    camera_rules: list[str] = Field(default_factory=list)
    texture_medium: str = "cinematic live action"
    negative_style_rules: list[str] = Field(default_factory=list)
    visual_motifs: list[str] = Field(default_factory=list)
    style_library_id: str = ""
    sound_thesis: str = ""
    music_thesis: str = ""


class ProductionBible(BaseModel):
    """Upstream shared root artifact consumed by writing, voice and film."""

    schema_version: str = "1.0"
    project_id: str
    title: str
    logline: str = ""
    format: str = "series"
    language: str = "zh"
    target_audience: str = ""
    production_intent: str = ""
    sources: list[ArtifactSource] = Field(default_factory=list)
    source_signature: str = "legacy_unknown"
    characters: list[ProductionCharacter] = Field(default_factory=list)
    locations: list[ProductionLocation] = Field(default_factory=list)
    style: FilmStyleLock = Field(default_factory=FilmStyleLock)
    world_rules: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    continuity_rules: list[str] = Field(default_factory=list)
    updated_at: str = Field(default_factory=utc_now_iso)


class ScreenplayLine(BaseModel):
    kind: str = "action"
    speaker: str = ""
    text: str
    performance_note: str = ""
    source_ref: str = ""


class SceneSourceRef(BaseModel):
    """Lineage anchor back to the finished novel text (read-only canon).

    Records which chapter and paragraph range a screenplay scene was
    deterministically extracted from, so adaptations can be audited against
    the source and upstream edits can invalidate stale projections.
    """

    chapter_number: int = Field(ge=1)
    paragraph_start: int = Field(default=1, ge=1)
    paragraph_end: int = Field(default=1, ge=1)
    scene_intent_id: str = ""
    note: str = ""


class ScreenplayScene(BaseModel):
    scene_id: str
    sequence_number: int = Field(ge=1)
    heading: str
    location_id: str = ""
    time_of_day: str = ""
    characters: list[str] = Field(default_factory=list)
    objective: str = ""
    conflict: str = ""
    turn: str = ""
    visual_hook: str = ""
    sound_hook: str = ""
    duration_s: float = Field(default=60, ge=1)
    source_chapter: int | None = None
    source_scene_ref: str = ""
    source_refs: list[SceneSourceRef] = Field(default_factory=list)
    lines: list[ScreenplayLine] = Field(default_factory=list)


class Screenplay(BaseModel):
    title: str
    version: int = 1
    synopsis: str = ""
    acts: list[str] = Field(default_factory=list)
    scenes: list[ScreenplayScene] = Field(default_factory=list)
    estimated_duration_s: float = 0


class ShotLanguage(BaseModel):
    """Six-axis camera language plus production-grade optical metadata."""

    shot_size: str = "medium"
    camera_angle: str = "eye_level"
    camera_motion: str = "static"
    lighting: str = "motivated"
    emotion: str = "neutral"
    time: str = "continuous"
    lens_mm: int = Field(default=50, ge=8, le=400)
    composition: str = ""
    focus_strategy: str = ""


class FilmShot(BaseModel):
    shot_id: str
    scene_id: str
    shot_number: int = Field(ge=1)
    title: str = ""
    duration_s: float = Field(default=5, ge=1, le=30)
    language: ShotLanguage = Field(default_factory=ShotLanguage)
    action: str = ""
    dialogue: str = ""
    sound_design: str = ""
    transition: str = "cut"
    rhythm_note: str = ""
    prompt: str = ""
    negative_prompt: str = ""
    character_ids: list[str] = Field(default_factory=list)
    location_id: str = ""
    identity_reference_urls: list[str] = Field(default_factory=list)
    style_reference_urls: list[str] = Field(default_factory=list)
    # MiniMax H3 Ref2VA multimodal reference inputs (≤3 videos / ≤3 audios,
    # 2–15 s each, 15 s total); consumed by providers that accept
    # video/audio conditioning (catalog ``reference_limits``).
    reference_video_urls: list[str] = Field(default_factory=list)
    reference_audio_urls: list[str] = Field(default_factory=list)
    first_frame_url: str = ""
    last_frame_url: str = ""
    # Provider generation knobs persisted per shot (e.g. MiniMax H3
    # resolution/seed/watermark/aspect ratio) that override the catalog
    # defaults at submission time.
    generation_params: dict[str, Any] = Field(default_factory=dict)
    selected_asset_url: str = ""
    candidates: list[str] = Field(default_factory=list)
    provider_id: str = "bailian"
    model_id: str = "wan2.7-r2v"
    generation_mode: FilmGenerationMode = FilmGenerationMode.REFERENCE_TO_VIDEO
    provider_task: FilmProviderTask | None = None
    qc_status: str = "pending"
    qc_notes: list[str] = Field(default_factory=list)
    locked: bool = False
    source_signature: str = "legacy_unknown"
    derivation_status: Literal["fresh", "stale", "conflict", "blocked", "legacy_unknown"] = (
        "legacy_unknown"
    )
    stale_reasons: list[str] = Field(default_factory=list)
    stale_decision: Literal["pending", "preserve_old_version", "regenerate", "rebind", ""] = ""


class FilmVisualAsset(BaseModel):
    asset_id: str
    asset_type: str
    name: str
    subject_id: str = ""
    view: str = "hero"
    prompt: str = ""
    negative_prompt: str = ""
    reference_urls: list[str] = Field(default_factory=list)
    candidates: list[str] = Field(default_factory=list)
    selected_url: str = ""
    provider_id: str = "bailian"
    model_id: str = "wan2.7-image-pro"
    provider_task: FilmProviderTask | None = None
    qc_status: str = "pending"
    locked: bool = False
    source_signature: str = "legacy_unknown"
    derivation_status: Literal["fresh", "stale", "conflict", "blocked", "legacy_unknown"] = (
        "legacy_unknown"
    )
    stale_reasons: list[str] = Field(default_factory=list)
    stale_decision: Literal["pending", "preserve_old_version", "regenerate", "rebind", ""] = ""


class RunPlanNode(BaseModel):
    """Recoverable DAG node inspired by node-graph generation systems."""

    node_id: str
    label: str
    stage: FilmStage
    depends_on: list[str] = Field(default_factory=list)
    artifact_inputs: list[str] = Field(default_factory=list)
    artifact_outputs: list[str] = Field(default_factory=list)
    provider_id: str = ""
    model_id: str = ""
    status: FilmStageStatus = FilmStageStatus.PENDING
    human_checkpoint: bool = False
    retry_limit: int = Field(default=2, ge=0, le=10)
    estimated_cost_usd: float = Field(default=0, ge=0)
    notes: str = ""


class TimelineMarker(BaseModel):
    name: str
    time_s: float = Field(ge=0)
    color: str = "orange"
    metadata: dict[str, Any] = Field(default_factory=dict)


class TimelineClip(BaseModel):
    clip_id: str
    name: str
    media_kind: str
    source_url: str = ""
    start_s: float = Field(default=0, ge=0)
    duration_s: float = Field(default=1, gt=0)
    source_start_s: float = Field(default=0, ge=0)
    enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class TimelineTrack(BaseModel):
    track_id: str
    name: str
    kind: str
    clips: list[TimelineClip] = Field(default_factory=list)


class FilmTimeline(BaseModel):
    """OTIO-compatible conceptual timeline: Timeline → Stack → Tracks → Clips."""

    name: str
    frame_rate: float = Field(default=24, gt=0)
    tracks: list[TimelineTrack] = Field(default_factory=list)
    markers: list[TimelineMarker] = Field(default_factory=list)


class FilmStageState(BaseModel):
    stage: FilmStage
    status: FilmStageStatus = FilmStageStatus.PENDING
    progress: float = Field(default=0, ge=0, le=1)
    artifact_count: int = Field(default=0, ge=0)
    summary: str = ""
    warnings: list[str] = Field(default_factory=list)
    updated_at: str = Field(default_factory=utc_now_iso)


class FilmDecision(BaseModel):
    decision_id: str = Field(default_factory=lambda: uuid4().hex)
    stage: FilmStage
    title: str
    description: str = ""
    choices: list[str] = Field(default_factory=list)
    selected: str = ""
    status: str = "pending"


class MediaArtifactKind(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    SUBTITLE = "subtitle"


class MediaArtifact(BaseModel):
    """A materialized, checksummed local copy of one generated media file.

    Provider URLs expire; artifacts make shots reproducible, renderable and
    auditable.  ``subject_ref`` carries the owning shot/asset id or ``master``
    for delivery outputs so the same layer serves film, comic and audiobook
    pipelines.
    """

    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    kind: MediaArtifactKind
    subject_ref: str
    source_url: str = ""
    local_path: str
    checksum: str = ""
    size_bytes: int = Field(default=0, ge=0)
    width: int = Field(default=0, ge=0)
    height: int = Field(default=0, ge=0)
    duration_s: float = Field(default=0, ge=0)
    fps: float = Field(default=0, ge=0)
    codec: str = ""
    has_audio: bool = False
    provider_id: str = ""
    task_id: str = ""
    created_at: str = Field(default_factory=utc_now_iso)


class ComplianceSeverity(str, Enum):
    """Compliance check tiers: red lines block release outright, high-risk
    items need targeted revision, positive-value checks are advisory."""

    RED_LINE = "red_line"
    HIGH_RISK = "high_risk"
    POSITIVE_VALUE = "positive_value"


class ComplianceCheckItem(BaseModel):
    """One structured entry of the short-drama compliance checklist."""

    model_config = ConfigDict(extra="forbid")

    check_id: str
    severity: ComplianceSeverity
    category: str
    title: str
    description: str = ""
    pattern: str = ""
    remediation: str = ""


class ComplianceAction(str, Enum):
    BLOCK = "block"
    REVISE = "revise"
    REVIEW = "review"
    NONE = "none"


class ComplianceFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check_id: str
    severity: ComplianceSeverity
    title: str
    detail: str = ""
    location: str = ""
    action: ComplianceAction = ComplianceAction.NONE


class ComplianceReport(BaseModel):
    """Executable compliance-audit result feeding the DeliveryManifest.

    ``blocked`` means at least one red-line finding hit; ``passed`` means the
    work is release-ready (no red line, no open high-risk finding).
    """

    model_config = ConfigDict(extra="forbid")

    target_id: str = "master"
    passed: bool = False
    blocked: bool = False
    findings: list[ComplianceFinding] = Field(default_factory=list)
    summary: str = ""
    created_at: str = Field(default_factory=utc_now_iso)


class QcCheckStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class QcCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    status: QcCheckStatus
    detail: str = ""
    metric: float | None = None


class QcReport(BaseModel):
    """Executable quality-gate result for one shot, asset or master."""

    target_id: str
    target_type: str
    passed: bool = False
    checks: list[QcCheck] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)


class VisionDimensionScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: str
    score: float = 0.0
    note: str = ""


class VisionQcReport(BaseModel):
    """Semantic-level vision quality scoring result (soft gate)."""

    model_config = ConfigDict(extra="forbid")

    target_id: str
    target_type: str = "shot"
    passed: bool = False
    overall_score: float = 0.0
    dimensions: list[VisionDimensionScore] = Field(default_factory=list)
    retries_used: int = 0
    summary: str = ""
    created_at: str = Field(default_factory=utc_now_iso)


class FilmJobKind(str, Enum):
    GENERATE_ASSET = "generate_asset"
    GENERATE_SHOT = "generate_shot"
    QUERY_MEDIA = "query_media"
    MATERIALIZE = "materialize"
    RENDER_MASTER = "render_master"


class FilmJobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FilmJob(BaseModel):
    """One governed unit of film production work.

    Jobs carry an idempotency key so retried UI requests never double-submit
    paid generation, plus attempt/retry metadata so the pipeline can recover
    after restarts instead of silently dropping in-flight provider tasks.
    """

    job_id: str = Field(default_factory=lambda: uuid4().hex)
    idempotency_key: str = ""
    kind: FilmJobKind
    target_id: str
    provider_id: str = ""
    model_id: str = ""
    state: FilmJobState = FilmJobState.QUEUED
    attempts: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1, le=10)
    retry_backoff_s: float = Field(default=4.0, ge=0)
    error_message: str = ""
    estimated_cost_usd: float = Field(default=0, ge=0)
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class DeliveryMediaType(str, Enum):
    """The four deliverable media registered on a unified manifest."""

    NOVEL = "novel"
    AUDIOBOOK = "audiobook"
    COMIC = "comic"
    FILM = "film"


class DeliveryArtifact(BaseModel):
    """One registered finished artifact of any medium (统一导出层)."""

    media_type: DeliveryMediaType
    path: str
    item_count: int = Field(default=0, ge=0)
    note: str = ""


class DeliveryManifest(BaseModel):
    """Everything a downstream NLE / platform needs from one render pass.

    Film-specific fields stay for backward compatibility; the generic
    ``artifacts`` list registers finished deliverables across all four media
    (novel / audiobook / comic / film).
    """

    project_id: str
    title: str = ""
    generated_at: str = Field(default_factory=utc_now_iso)
    master_video_path: str = ""
    master_audio_path: str = ""
    subtitle_path: str = ""
    otio_path: str = ""
    duration_s: float = Field(default=0, ge=0)
    width: int = Field(default=0, ge=0)
    height: int = Field(default=0, ge=0)
    frame_rate: float = Field(default=24, gt=0)
    shot_count: int = Field(default=0, ge=0)
    clip_paths: list[str] = Field(default_factory=list)
    qc_passed: bool = False
    compliance_passed: bool = False
    compliance_report_path: str = ""
    artifacts: list[DeliveryArtifact] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    source_signature: str = "legacy_unknown"
    derivation_status: Literal["fresh", "stale", "conflict", "blocked", "legacy_unknown"] = (
        "legacy_unknown"
    )
    blocking_reasons: list[str] = Field(default_factory=list)

    def register_artifact(
        self,
        media_type: DeliveryMediaType,
        path: str,
        *,
        item_count: int = 0,
        note: str = "",
    ) -> "DeliveryManifest":
        """Return a copy with one finished artifact registered (frozen-safe)."""
        artifact = DeliveryArtifact(
            media_type=media_type, path=path, item_count=item_count, note=note
        )
        return self.model_copy(update={"artifacts": [*self.artifacts, artifact]})


class FilmStudioState(BaseModel):
    schema_version: str = "2.0"
    project_id: str
    project_title: str
    mode: ProductionMode = ProductionMode.COLLABORATIVE
    current_stage: FilmStage = FilmStage.PLANNING
    active_scene_id: str = ""
    active_shot_id: str = ""
    production_bible: ProductionBible
    screenplay: Screenplay
    visual_assets: list[FilmVisualAsset] = Field(default_factory=list)
    shots: list[FilmShot] = Field(default_factory=list)
    run_plan: list[RunPlanNode] = Field(default_factory=list)
    timeline: FilmTimeline
    stages: list[FilmStageState] = Field(default_factory=list)
    decisions: list[FilmDecision] = Field(default_factory=list)
    media_artifacts: list[MediaArtifact] = Field(default_factory=list)
    jobs: list[FilmJob] = Field(default_factory=list)
    qc_reports: list[QcReport] = Field(default_factory=list)
    vision_qc_reports: list[VisionQcReport] = Field(default_factory=list)
    compliance_report: ComplianceReport | None = None
    delivery: DeliveryManifest | None = None
    notices: list[str] = Field(default_factory=list)
    source_signature: str = "legacy_unknown"
    changed_source_artifacts: list[str] = Field(default_factory=list)
    delivery_blocking_reasons: list[str] = Field(default_factory=list)
    updated_at: str = Field(default_factory=utc_now_iso)
