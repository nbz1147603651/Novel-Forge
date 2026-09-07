"""Vendor-neutral contracts for the extensible audio plugin platform."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from novel_forge.core.schemas.base import VersionedSchema


class AudioCapability(str, Enum):
    """Operations that may be supplied by a model, service, or local runtime."""

    SPEECH_SYNTHESIS = "speech_synthesis"
    VOICE_CLONE = "voice_clone"
    VOICE_DESIGN = "voice_design"
    ASR = "asr"
    FORCED_ALIGNMENT = "forced_alignment"
    VAD = "vad"
    SFX_GENERATION = "sfx_generation"
    MUSIC_GENERATION = "music_generation"
    SOUNDSCAPE_GENERATION = "soundscape_generation"
    AUDIO_RENDER = "audio_render"
    QUALITY_EVALUATION = "quality_evaluation"


class AudioExecutionStage(str, Enum):
    """Stable pipeline slots; implementations are selected from capabilities."""

    VOICE_DESIGN = "voice_design"
    VOICE_CLONE = "voice_clone"
    TTS_PREVIEW = "tts_preview"
    TTS_FORMAL = "tts_formal"
    ASR = "asr"
    ALIGN = "align"
    ALIGNMENT_VALIDATOR = "alignment_validator"
    VAD = "vad"
    SFX = "sfx"
    MUSIC = "music"
    SOUNDSCAPE = "soundscape"
    RENDERER = "renderer"
    QUALITY = "quality"


class AudioQualityPreset(str, Enum):
    """User-facing goals.  A preset is policy, not a hard-coded model bundle."""

    QUICK_PREVIEW = "quick_preview"
    PRODUCTION = "production"
    MASTER = "master"
    LOW_RESOURCE = "low_resource"
    CUSTOM = "custom"


class AudioLocationPolicy(str, Enum):
    """Where an audio stage may run, independent from its quality target."""

    CLOUD_ONLY = "cloud_only"
    LOCAL_ONLY = "local_only"
    PREFER_CLOUD = "prefer_cloud"
    PREFER_LOCAL = "prefer_local"
    HYBRID = "hybrid"


class AudioExecution(str, Enum):
    LOCAL_SIDECAR = "local_sidecar"
    LOCAL_RUNTIME = "local_runtime"
    LOCAL_CLI = "local_cli"
    CLOUD_API = "cloud_api"
    BUILTIN = "builtin"


class AudioMemoryClass(str, Enum):
    LIGHT = "light"
    MEDIUM = "medium"
    HIGH = "high"


class AudioQualityClass(str, Enum):
    PREVIEW = "preview"
    BALANCED = "balanced"
    PRODUCTION = "production"
    MASTER = "master"


class TimelineGranularity(str, Enum):
    SEGMENT = "segment"
    TOKEN = "token"
    CHARACTER = "character"
    WORD = "word"


class AudioPluginCapabilities(BaseModel):
    services: set[AudioCapability] = Field(default_factory=set)
    languages: set[str] = Field(
        default_factory=lambda: {"*"},
        description="BCP-47-ish language codes; '*' means runtime-discovered/general.",
    )
    granularity: set[TimelineGranularity] = Field(default_factory=set)
    known_text_alignment: bool = False
    confidence: bool = False
    streaming: bool = False
    offline: bool = False
    mixed_language: bool = False
    max_audio_seconds: int | None = Field(default=None, ge=1)

    def supports_language(self, language: str) -> bool:
        normalized = language.strip().lower().replace("_", "-")
        if not normalized or normalized == "auto" or "*" in self.languages:
            return True
        available = {item.lower().replace("_", "-") for item in self.languages}
        base = normalized.split("-", 1)[0]
        return normalized in available or base in available


class AudioPluginRuntime(BaseModel):
    execution: AudioExecution
    platforms: set[str] = Field(default_factory=lambda: {"macos", "windows", "linux"})
    accelerators: set[str] = Field(default_factory=lambda: {"cpu"})
    memory_class: AudioMemoryClass = AudioMemoryClass.MEDIUM
    managed_by_app: bool = False
    endpoint_setting: str = ""
    default_endpoint: str = ""
    credential_settings: list[str] = Field(default_factory=list)
    health_path: str = "/health"
    capabilities_path: str = "/capabilities"
    self_test_path: str = "/self-test"
    version_path: str = "/version"
    diagnostics_path: str = "/diagnostics"

    @property
    def is_cloud(self) -> bool:
        return self.execution == AudioExecution.CLOUD_API


class AudioPluginQuality(BaseModel):
    profile: AudioQualityClass = AudioQualityClass.BALANCED
    quality_score: float = Field(default=0.6, ge=0.0, le=1.0)
    latency_score: float = Field(default=0.6, ge=0.0, le=1.0)
    priority: int = Field(default=50, ge=0, le=100)


class AudioPluginManifest(VersionedSchema):
    """Serializable registration contract for built-in and third-party plugins."""

    plugin_id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    display_name: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)
    model_id: str = Field(default="")
    plugin_version: str = Field(default="builtin-v1")
    description: str = ""
    capabilities: AudioPluginCapabilities
    runtime: AudioPluginRuntime
    quality: AudioPluginQuality = Field(default_factory=AudioPluginQuality)
    integration_status: str = "built_in"
    license_summary: str = ""
    tags: set[str] = Field(default_factory=set)

    def supplies(self, capability: AudioCapability) -> bool:
        return capability in self.capabilities.services


class AudioHardwareProfile(BaseModel):
    platform: str = "unknown"
    accelerator: str = "auto"
    memory_class: AudioMemoryClass = AudioMemoryClass.MEDIUM
    memory_gb: float | None = Field(default=None, ge=0.0)
    device_label: str = ""


class AudioProjectConstraints(BaseModel):
    """User intent and environment constraints consumed by the planner."""

    preset: AudioQualityPreset = AudioQualityPreset.PRODUCTION
    location_policy: AudioLocationPolicy = AudioLocationPolicy.HYBRID
    languages: list[str] = Field(default_factory=lambda: ["zh"])
    accelerator: str = "auto"
    memory_budget: AudioMemoryClass = AudioMemoryClass.HIGH
    plugin_overrides: dict[AudioExecutionStage, str] = Field(default_factory=dict)
    language_overrides: dict[str, str] = Field(default_factory=dict)
    disabled_plugins: set[str] = Field(default_factory=set)
    require_alignment_validation: bool = False
    plugin_endpoints: dict[str, str] = Field(default_factory=dict)
    budget_limit_usd: float = Field(default=0.0, ge=0.0)

    @model_validator(mode="after")
    def apply_preset_invariants(self) -> "AudioProjectConstraints":
        if self.preset == AudioQualityPreset.LOW_RESOURCE:
            self.memory_budget = AudioMemoryClass.LIGHT
        if self.preset == AudioQualityPreset.MASTER:
            self.require_alignment_validation = True
        return self


class AudioRouteTarget(BaseModel):
    """Frozen provider/model/runtime identity for one executable route hop."""

    plugin_id: str
    provider_id: str
    model_id: str = ""
    plugin_version: str = ""
    endpoint: str = ""
    credential_settings: list[str] = Field(default_factory=list)
    execution: AudioExecution
    offline: bool = False
    license_summary: str = ""
    voice_mapping_required: bool = False


class AudioStageRoute(BaseModel):
    stage: AudioExecutionStage
    primary: AudioRouteTarget | None = None
    fallbacks: list[AudioRouteTarget] = Field(default_factory=list)
    reason: str = ""
    overridden: bool = False
    languages: list[str] = Field(default_factory=list)


class AudioLanguageRoute(BaseModel):
    language: str
    asr_plugin_id: str = ""
    aligner_plugin_id: str = ""
    fallback_plugin_ids: list[str] = Field(default_factory=list)
    reason: str = ""


class AudioPreflightStatus(str, Enum):
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"


class AudioPreflightCheck(BaseModel):
    kind: str
    status: AudioPreflightStatus
    message: str
    stage: AudioExecutionStage | None = None
    plugin_id: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class AudioPreflightReport(VersionedSchema):
    checks: list[AudioPreflightCheck] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(item.status == AudioPreflightStatus.FAILED for item in self.checks)


class AudioExecutionPlan(VersionedSchema):
    """Project-level, frozen and reproducible audio routing contract."""

    plan_id: str = ""
    project_id: str = ""
    preset: AudioQualityPreset
    location_policy: AudioLocationPolicy = AudioLocationPolicy.HYBRID
    budget_limit_usd: float = Field(default=0.0, ge=0.0)
    memory_budget: AudioMemoryClass = AudioMemoryClass.HIGH
    routes: list[AudioStageRoute] = Field(default_factory=list)
    language_routes: list[AudioLanguageRoute] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    hardware: AudioHardwareProfile = Field(default_factory=AudioHardwareProfile)
    frozen: bool = False
    frozen_at: datetime | None = None
    manifest_digest: str = ""
    preflight: AudioPreflightReport | None = None

    def assignment_for(self, stage: AudioExecutionStage) -> AudioStageRoute | None:
        return next((item for item in self.routes if item.stage == stage), None)


class ModelScorecard(VersionedSchema):
    """Evidence measured on a user's own device and representative samples."""

    plugin_id: str = Field(min_length=1)
    device_signature: str = ""
    languages: list[str] = Field(default_factory=list)
    sample_count: int = Field(default=0, ge=0)
    quality_score: float = Field(default=0.0, ge=0.0, le=1.0)
    median_latency_ms: float | None = Field(default=None, ge=0.0)
    peak_memory_mb: float | None = Field(default=None, ge=0.0)
    character_error_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    word_error_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    anchor_p95_error_ms: float | None = Field(default=None, ge=0.0)
    human_rating: float | None = Field(default=None, ge=0.0, le=5.0)
    measured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AlignmentToken(BaseModel):
    text: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    start_char: int | None = Field(default=None, ge=0)
    end_char: int | None = Field(default=None, ge=0)
    unit: TimelineGranularity = TimelineGranularity.TOKEN


class AlignmentResult(VersionedSchema):
    segment_index: int = Field(ge=0)
    plugin_id: str = ""
    validator_plugin_id: str = ""
    language: str = "auto"
    expected_text: str = ""
    recognized_text: str = ""
    status: Literal["aligned", "segment_fallback", "failed"] = "segment_fallback"
    segment_start_ms: int = Field(default=0, ge=0)
    segment_end_ms: int = Field(default=0, ge=0)
    tokens: list[AlignmentToken] = Field(default_factory=list)
    coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    text_error_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    validator_p95_delta_ms: float | None = Field(default=None, ge=0.0)
    error: str = ""


class SpeechTimelineEntry(BaseModel):
    segment_index: int = Field(ge=0)
    language: str = "auto"
    text: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    tokens: list[AlignmentToken] = Field(default_factory=list)
    alignment_status: str = "segment_fallback"


class SpeechTimeline(VersionedSchema):
    chapter_number: int = Field(ge=1)
    entries: list[SpeechTimelineEntry] = Field(default_factory=list)
    alignments: list[AlignmentResult] = Field(default_factory=list)
    total_duration_ms: int = Field(default=0, ge=0)
    execution_plan: AudioExecutionPlan | None = None


class AudioAssetRef(BaseModel):
    asset_id: str
    kind: Literal["voice", "bgm", "soundscape", "sfx"]
    path: str
    duration_ms: int = Field(default=0, ge=0)
    plugin_id: str = ""
    request_hash: str = ""


class MixEvent(BaseModel):
    event_id: str
    asset_id: str
    bus: Literal["voice", "bgm", "soundscape", "sfx"]
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    gain_db: float = 0.0
    duck_under_voice_db: float = Field(default=0.0, ge=0.0, le=30.0)
    fade_in_ms: int = Field(default=0, ge=0)
    fade_out_ms: int = Field(default=0, ge=0)
    priority: int = Field(default=50, ge=0, le=100)
    anchor_text: str = ""
    requested_start_ms: int | None = Field(default=None, ge=0)
    placement_reason: str = ""


class MixPlan(VersionedSchema):
    chapter_number: int = Field(ge=1)
    assets: list[AudioAssetRef] = Field(default_factory=list)
    events: list[MixEvent] = Field(default_factory=list)
    target_lufs: float = -16.0
    true_peak_db: float = -1.5
    renderer_plugin_id: str = ""
    mastering_mode: Literal["single_pass", "two_pass_loudnorm"] = "two_pass_loudnorm"
    bus_headroom_db: float = Field(default=2.0, ge=0.0, le=12.0)
    placement_warnings: list[str] = Field(default_factory=list)
    # When True the renderer exports pre-loudnorm per-bus stems
    # (voice/bed/sfx) alongside the master.  Two-pass mode maps them from the
    # first premaster render, so the expensive event graph is still evaluated
    # only once. Default False keeps existing disk usage unchanged.
    export_stems: bool = Field(default=False)

    @model_validator(mode="after")
    def validate_event_graph(self) -> "MixPlan":
        asset_ids = [item.asset_id for item in self.assets]
        event_ids = [item.event_id for item in self.events]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("MixPlan asset_id values must be unique")
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("MixPlan event_id values must be unique")
        known_assets = set(asset_ids)
        missing_assets = [
            item.asset_id for item in self.events if item.asset_id not in known_assets
        ]
        if missing_assets:
            raise ValueError(f"MixPlan events reference unknown assets: {missing_assets}")
        invalid_ranges = [item.event_id for item in self.events if item.end_ms <= item.start_ms]
        if invalid_ranges:
            raise ValueError(f"MixPlan events must have positive duration: {invalid_ranges}")
        return self


class AudioQualityReport(VersionedSchema):
    chapter_number: int = Field(ge=1)
    passed: bool = False
    alignment_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    clipping_detected: bool = False
    integrated_lufs: float | None = None
    true_peak_db: float | None = None
    speech_masking_warnings: list[str] = Field(default_factory=list)
    repair_actions: list[str] = Field(default_factory=list)
    evaluator_plugin_id: str = ""
    mean_text_error_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    failed_segment_indices: list[int] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    repair_rounds: int = Field(default=0, ge=0)
    unresolved_sound_cues: int = Field(default=0, ge=0)
    mix_plan_warnings: list[str] = Field(default_factory=list)
    render_integrity_passed: bool = True
    failed_render_event_ids: list[str] = Field(default_factory=list)
    measurement_available: bool = False
    loudness_target_lufs: float = -16.0
    loudness_tolerance_lu: float = Field(default=2.0, ge=0.0)
    loudness_within_target: bool = True
    true_peak_target_db: float = -1.5
    true_peak_within_target: bool = True
    reference_loudness_lufs: float | None = None
    loudness_delta_lu: float | None = Field(default=None, ge=0.0)
    masking_risk_event_ids: list[str] = Field(default_factory=list)
    transition_risk_event_ids: list[str] = Field(default_factory=list)
    commercial_rights_issues: list[str] = Field(default_factory=list)


class AudioBenchmarkSample(BaseModel):
    sample_id: str
    audio_path: str
    expected_text: str
    language: str = "auto"


class AudioBenchmarkReport(VersionedSchema):
    sample_count: int = Field(default=0, ge=0)
    scorecards: list[ModelScorecard] = Field(default_factory=list)
    failures: dict[str, str] = Field(default_factory=dict)
