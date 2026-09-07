"""Shared request/response contracts for workspace actions."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from novel_forge.core.domain.story_defaults import DEFAULT_GENRE, DEFAULT_TONE
from novel_forge.tts.services.automation import AudioAutomationMode

RewriteStrategy = Literal["auto", "sequential", "compatible", "reconstruct", "surgical"]
ChapterWritingMode = Literal["whole_chapter", "scene_level"]
RepairControlMode = Literal["manual", "ai_assisted", "ai_auto"]
ShortWritingMode = Literal["auto", "whole_chapter", "scene_level"]
GlobalRepairQueueStatus = Literal["ready", "verify_first", "manual_review", "blocked"]
PublicationStatus = Literal["ready", "blocked_pending_finalize", "legacy_unknown"]
PublicationQualityStatus = Literal[
    "actual", "degraded", "fallback", "blocked", "legacy_unknown"
]
PublicationDerivationStatus = Literal[
    "fresh", "stale", "conflict", "blocked", "legacy_unknown"
]


def _default_global_repair_queue_statuses() -> list[GlobalRepairQueueStatus]:
    return ["ready"]


class CreateWorkRequest(BaseModel):
    mode: Literal["short", "long"] = Field(description="Project mode.")
    title: str = Field(default="")
    premise: str = Field(default="")


class CreateWorkResponse(BaseModel):
    project_id: str
    mode: str
    message: str


class RunShortRequest(BaseModel):
    project_id: str = Field(default="")
    theme: str
    genre: str = DEFAULT_GENRE
    tone: str = DEFAULT_TONE
    length_target: int = Field(default=3000, ge=500, le=50000)
    segmented_mode: Literal["auto", "on", "off"] | None = Field(
        default=None,
        description="Segmented short-draft mode override. None = follow settings.",
    )
    writing_mode: ShortWritingMode = Field(
        default="auto",
        description="Short-story writing mode. auto = use segmented_mode/settings trigger.",
    )
    segment_target_words: int | None = Field(
        default=None,
        ge=800,
        le=20000,
        description="Approximate target words per short-story segment.",
    )
    segment_max_count: int | None = Field(
        default=None,
        ge=2,
        le=8,
        description="Maximum number of short-story draft segments.",
    )
    max_edit_rounds: int | None = Field(
        default=None,
        ge=0,
        le=10,
        description="Max edit rounds (0=skip)",
    )
    # Optional creative hints
    title: str = ""
    language: str = Field(
        default="zh",
        description="Output language code. Plain 'zh' means Simplified Chinese (zh-Hans).",
    )
    characters_hint: str = ""
    world_hint: str = ""
    conflict_hint: str = ""
    pov_hint: str = ""
    opening_style: str = ""
    ending_style: str = ""
    extra_instructions: str = ""
    blueprint_element_preferences: dict[str, Any] = Field(
        default_factory=dict,
        description="Manual selector preferences for narrative blueprint elements.",
    )
    research_enabled: bool = Field(
        default=False,
        description="Enable the shared, one-shot chapter research router for this short story.",
    )
    research_provider: str = Field(
        default="auto",
        description="Research provider id; shares the long-init provider configuration.",
    )
    research_query_hint: str = Field(
        default="",
        description="Optional must-fact query hint for the short-story evidence pack.",
    )


class RunShortResponse(BaseModel):
    project_id: str
    word_count: int
    overall_score: float
    passed: bool
    preview: str
    creative_summary: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)


class InitLongRequest(BaseModel):
    project_id: str = Field(default="")
    premise: str
    genre: str = DEFAULT_GENRE
    tone: str = DEFAULT_TONE
    total_chapters: int = Field(default=20, ge=1, le=10000)
    words_per_chapter: int = Field(default=3000, ge=500, le=20000)
    volume_mode: Literal["auto", "on", "off"] = "auto"
    chapters_per_volume: int = Field(default=0, ge=0, le=500)
    # max_edit_rounds 字段已下线：长篇 WAVE 阶段固定为单次连贯起稿，不再给用户配置编辑轮次。
    # Optional creative hints
    title: str = ""
    language: str = Field(
        default="zh",
        description="Output language code. Plain 'zh' means Simplified Chinese (zh-Hans).",
    )
    characters_hint: str = ""
    world_hint: str = ""
    conflict_hint: str = ""
    pov_hint: str = ""
    opening_style: str = ""
    ending_style: str = ""
    extra_instructions: str = ""
    polish_hint: str = Field(
        default="",
        description="Optional post-generation outline polish instruction.",
    )
    research_enabled: bool = Field(
        default=False,
        description="Enable optional web research between spec confirmation and StoryBible.",
    )
    research_provider: str = Field(
        default="auto",
        description=(
            "Research provider id: auto/noop/tavily/brave/searxng/http_json/"
            "bailian_web_search/mcp_search."
        ),
    )
    research_query_hint: str = Field(
        default="",
        description="Optional user hint appended to deterministic init research queries.",
    )
    regenerate_outline: bool = Field(
        default=False,
        description=(
            "Quarantine the existing outline and downstream init artifacts before running, "
            "then regenerate the outline from the existing upstream blueprint."
        ),
    )
    blueprint_element_preferences: dict[str, Any] = Field(
        default_factory=dict,
        description="Manual selector preferences for narrative blueprint elements.",
    )
    copilot_gates: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Optional human copilot gates for long-form initialization.",
    )
    creative_exploration: Literal["adaptive", "single"] = Field(
        default="adaptive",
        description="Adaptive 2+1 concept exploration or the deterministic single path.",
    )
    planning_commitment: Literal["progressive", "full"] = Field(
        default="full",
        description="Full-book planning by default; progressive windows require an explicit choice.",
    )


class InitLongResponse(BaseModel):
    project_id: str
    title: str
    total_chapters: int
    volume_mode: bool
    volume_count: int
    characters: list[str]


class RunChapterRequest(BaseModel):
    project_id: str
    chapter_number: int = Field(ge=1)
    # max_edit_rounds 字段已下线：长篇 WAVE 阶段固定为单次连贯起稿。
    force: bool = False
    notes: str = ""
    writing_mode: ChapterWritingMode = "whole_chapter"
    repair_control_mode: RepairControlMode | None = Field(
        default=None,
        description="Per-request repair control override. None follows runtime settings.",
    )


class RunChapterResponse(BaseModel):
    project_id: str
    chapter_number: int
    word_count: int
    overall_score: float
    continuity_score: float
    continuity_issue_count: int
    causal_score: float = 0.0
    causal_issue_count: int = 0
    bridge_summary: str
    chapter_exit_summary: str
    preview: str
    warnings: list[str] = Field(default_factory=list)


class ChapterPublicationView(BaseModel):
    """Stable novel output read by voice, film and external clients.

    The projection deliberately exposes no pipeline, StoryKernel or narrative-
    state objects.  Historical projects without a signed final artifact remain
    readable as ``legacy_unknown`` and are upgraded on their next successful
    chapter finalization.
    """

    schema_version: str = "1.0"
    project_id: str
    chapter_number: int = Field(ge=1)
    chapter_path: str
    text: str = ""
    final_text_hash: str
    word_count: int = Field(default=0, ge=0)
    workflow_version: str = "legacy_unknown"
    artifact_schema_version: int = Field(default=1, ge=1)
    final_artifact_id: str = ""
    input_signature: str = "legacy_unknown"
    output_version: int = Field(default=0, ge=0)
    parent_artifact_refs: list[str] = Field(default_factory=list)
    parent_artifact_versions: dict[str, int] = Field(default_factory=dict)
    quality_status: PublicationQualityStatus = "legacy_unknown"
    derivation_status: PublicationDerivationStatus = "legacy_unknown"
    publication_status: PublicationStatus = "legacy_unknown"
    degradation_reason: str = ""
    blocking_reasons: list[str] = Field(default_factory=list)
    tts_metadata: dict[str, Any] | None = None
    published_at: str = ""
    deliverable: bool = True

    @model_validator(mode="after")
    def _sync_deliverable(self) -> ChapterPublicationView:
        """Derive downstream authority from the publication gate."""

        self.deliverable = (
            self.publication_status != "blocked_pending_finalize" and not self.blocking_reasons
        )
        return self


class TTSSynthesizeRequest(BaseModel):
    """Run resumable synthesis for an existing chapter dubbing script."""

    project_id: str = Field(min_length=1)
    chapter_number: int = Field(ge=1)
    provider: str = ""
    automation_mode: AudioAutomationMode | None = None


class TTSBuildVoiceTeamRequest(BaseModel):
    """Build or selectively rebuild the project voice team as a durable job."""

    project_id: str = Field(min_length=1)
    provider: str = ""
    rebuild_character_ids: list[str] = Field(default_factory=list)


class TTSGenerateScriptRequest(BaseModel):
    """Generate a chapter dubbing script from the authoritative archived prose."""

    project_id: str = Field(min_length=1)
    chapter_number: int = Field(ge=1)
    provider: str = ""
    reference_style_strength: float = Field(default=0.65, ge=0.0, le=1.0)


class TTSFullPipelineRequest(BaseModel):
    """Run the complete chapter dubbing pipeline as a persistent job."""

    project_id: str = Field(min_length=1)
    chapter_number: int = Field(ge=1)
    chapter_text: str = Field(min_length=1)
    characters: list[dict[str, Any]] = Field(default_factory=list)
    provider: str = ""
    automation_mode: AudioAutomationMode | None = None
    genre: str = ""
    tone: str = ""
    scene_context: dict[str, Any] = Field(default_factory=dict)


class TTSPostArchiveRequest(BaseModel):
    """Run automatic dubbing from the authoritative archived chapter text."""

    project_id: str = Field(min_length=1)
    chapter_number: int = Field(ge=1)
    parent_job_id: str = ""


class TTSExportAudioRequest(BaseModel):
    """Persisted audio delivery request for a chapter or completed-book ZIP."""

    project_id: str = Field(min_length=1)
    scope: Literal["chapter", "book"] = "chapter"
    chapter_number: int | None = Field(default=None, ge=1)
    format: Literal["mp3", "wav", "flac", "srt", "zip"] = "mp3"
    include_subtitles: bool = False
    target_lufs: float | None = None


class TTSExportAudiobookRequest(BaseModel):
    """Persisted finished-audiobook package delivery request."""

    project_id: str = Field(min_length=1)
    chapter_numbers: list[int] = Field(default_factory=list)
    require_delivery_ready: bool = True


class DecisionOption(BaseModel):
    """One UI-facing option inside a chapter checkpoint."""

    option_id: str
    label: str
    description: str = ""
    is_recommended: bool = False
    # Semantic tag for workspace-layer side-effect routing.
    # Known values: "adjust_outline", "repair_continuity", "accept_and_archive",
    # "regenerate_plan", "skip", "pause_for_human". None = no specific side-effect.
    semantic_tag: str | None = None


class DecisionCheckpoint(BaseModel):
    """A resumable decision point in the chapter studio flow."""

    checkpoint_id: str
    checkpoint_type: Literal["plan_checkpoint", "guard_checkpoint"]
    summary: str = ""
    prompt: str = ""
    options: list[DecisionOption] = Field(default_factory=list)
    related_artifacts: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    """Diagnostic/decision context persisted alongside the checkpoint.

    Carries the archive-gate retry state (auto-repair enabled/scheduled,
    evidence binding, block kind, attempts budget) so the auto-pilot and the
    UI can tell *why* a given option is recommended without re-deriving it.
    Defaults to an empty dict so checkpoints written by older versions
    deserialize unchanged.
    """


class ChapterWorkspaceChapter(BaseModel):
    """Lightweight chapter list entry for the chapter studio rail."""

    chapter_number: int = Field(ge=1)
    title: str = ""
    status: Literal["done", "current", "pending", "needs_decision", "stale", "rewriting"] = (
        "pending"
    )
    status_label: str = ""
    word_count: int = 0
    overall_score: float | None = None
    continuity_score: float | None = None
    updated_at: str | None = None


class ChapterArtifactPreview(BaseModel):
    """Artifact metadata surfaced to chapter-studio UIs."""

    artifact_id: str
    label: str
    relative_path: str
    kind: Literal["markdown", "json", "text"] = "text"
    preview: str = ""
    updated_at: str | None = None


class ChapterWorkspaceSnapshot(BaseModel):
    """Aggregated read model for the chapter studio page."""

    project_id: str
    project_title: str
    chapter_number: int = Field(ge=1)
    total_chapters: int = 0
    genre: str = ""
    tone: str = ""
    project_summary: str = ""
    chapters: list[ChapterWorkspaceChapter] = Field(default_factory=list)
    previous_title: str = ""
    previous_summary: str = ""
    previous_exit_summary: str = ""
    current_title: str = ""
    current_goal: str = ""
    current_outline_summary: str = ""
    next_title: str = ""
    next_goal: str = ""
    carry_forward: list[str] = Field(default_factory=list)
    suggestions_for_next_chapter: str = ""
    alignment_score: float | None = None
    overall_score: float | None = None
    continuity_score: float | None = None
    continuity_issue_count: int = 0
    continuity_issues: list[dict[str, Any]] = Field(default_factory=list)
    causal_score: float | None = None
    causal_issue_count: int = 0
    causal_issues: list[dict[str, Any]] = Field(default_factory=list)
    reading_power_score: float | None = None
    warnings: list[str] = Field(default_factory=list)
    artifacts: list[ChapterArtifactPreview] = Field(default_factory=list)
    pending_checkpoint: DecisionCheckpoint | None = None
    has_review_progress: bool = False
    review_progress_stage: str = ""


class PrepareChapterRequest(BaseModel):
    """Prepare a chapter until the first decision checkpoint."""

    project_id: str
    chapter_number: int = Field(ge=1)
    # max_edit_rounds 字段已下线：长篇 WAVE 阶段固定为单次连贯起稿。
    force: bool = False
    notes: str = ""
    rewrite_strategy: RewriteStrategy = Field(
        default="auto",
        description=(
            "Rewrite strategy for force-regenerating an existing chapter: "
            "auto/sequential/compatible/reconstruct/surgical."
        ),
    )
    writing_mode: ChapterWritingMode = "whole_chapter"
    repair_control_mode: RepairControlMode | None = Field(
        default=None,
        description="Per-request repair control override. None follows runtime settings.",
    )


class RepairContinuityRequest(BaseModel):
    """Run targeted continuity repair for selected issues in a chapter."""

    project_id: str
    chapter_number: int = Field(ge=1)
    issue_indices: list[int] = Field(default_factory=list)
    issue_signatures: list[str] = Field(default_factory=list)
    synthetic_issues: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Normalized issue payloads compiled from repair tickets.",
    )
    repair_control_mode: RepairControlMode | None = Field(
        default=None,
        description="Per-request repair control override. None follows runtime settings.",
    )


class RepairCausalRequest(BaseModel):
    """Run targeted causal chain repair for selected issues in a chapter."""

    project_id: str
    chapter_number: int = Field(ge=1)
    issue_indices: list[int] = Field(default_factory=list)
    issue_signatures: list[str] = Field(default_factory=list)
    synthetic_issues: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Normalized issue payloads compiled from repair tickets.",
    )
    allow_exhausted_retry: bool = False
    repair_control_mode: RepairControlMode | None = Field(
        default=None,
        description="Per-request repair control override. None follows runtime settings.",
    )


class RepairIssuesRequest(BaseModel):
    """Run targeted repair for selected continuity and causal chain issues in a chapter."""

    project_id: str
    chapter_number: int = Field(ge=1)
    continuity_issue_indices: list[int] = Field(default_factory=list)
    causal_issue_indices: list[int] = Field(default_factory=list)
    continuity_issue_signatures: list[str] = Field(default_factory=list)
    causal_issue_signatures: list[str] = Field(default_factory=list)
    continuity_synthetic_issues: list[dict[str, Any]] = Field(default_factory=list)
    causal_synthetic_issues: list[dict[str, Any]] = Field(default_factory=list)
    allow_exhausted_retry: bool = False
    repair_control_mode: RepairControlMode | None = Field(
        default=None,
        description="Per-request repair control override. None follows runtime settings.",
    )


class ReevaluateChapterRequest(BaseModel):
    """Re-run chapter evaluation checks without applying any text repairs."""

    project_id: str
    chapter_number: int = Field(ge=1)


class ManualRevisionRequest(BaseModel):
    """Apply a manually supplied final chapter revision through workspace logic."""

    project_id: str
    chapter_number: int = Field(ge=1)
    text: str = Field(min_length=1)
    scope: str = Field(default="forward_only")
    background_reevaluate: bool = False
    reason: str = "api_manual_revision"
    expected_input_version: str = ""
    authoring_approval_id: str = ""


class RepairMotifHistoryRequest(BaseModel):
    """Repair/backfill motif history stats for an existing project.

    Two-layer strategy:
    - Layer 1: Rebuild stats from existing _motif_cache (always runs).
    - Layer 2: If force_re_extract=True, re-extract motifs from archived
      chapter text for chapters with empty cache.
    """

    project_id: str
    chapter_number: int = Field(ge=1)
    force_re_extract: bool = False
    start_chapter: int = Field(default=1, ge=1)
    end_chapter: int | None = Field(default=None, ge=1)


class ReextractRelationshipsRequest(BaseModel):
    """Re-extract relationship deltas from an existing chapter's text.

    Used to backfill relationship data for chapters that were generated
    before the extraction token budget was sufficient.
    """

    project_id: str
    chapter_number: int = Field(ge=0, default=0)  # 0 = all completed chapters


class RebuildMemoryVectorsRequest(BaseModel):
    """Rebuild vector-backed memory indexes for an existing project."""

    project_id: str
    include_expression: bool = Field(
        default=True,
        description="Also rebuild expression-channel semantic memory when available.",
    )
    from_chapter: int | None = Field(
        default=None,
        ge=1,
        description="First archived chapter to use for expression-channel rebuild.",
    )
    to_chapter: int | None = Field(
        default=None,
        ge=1,
        description="Last archived chapter to use for expression-channel rebuild.",
    )


class PolishOutlineRequest(BaseModel):
    """Refine selected chapter outlines and optionally refresh their contracts.

    This is the single durable write request used by both the 卷帙 review
    surface and the 机杼 dispatcher.  The pipeline owns the mutation; UI
    clients only submit intent and observe the resulting job.
    """

    project_id: str
    user_hint: str = ""
    selected_suggestions: list[str] = Field(default_factory=list)
    focus_fields: list[str] = Field(default_factory=list)
    chapter_range: str = ""
    analysis_only: bool = False
    sync_contracts: bool = True


class SyncChapterContractsRequest(BaseModel):
    """Re-sync chapter contracts after an outline edit.

    Local regeneration flow used by the 卷帙 / 章节大纲 / 润色 panel:

    * ``affected_chapter_numbers`` — chapter numbers whose outline content
      changed.  Empty list triggers an automatic fingerprint diff against
      the cached fingerprint on disk.
    * ``cascade_downstream`` — whether to propagate changes to chapters
      referenced by ``entry_state_requirements`` text.
    * ``rebuild_milestones`` — whether to recompute the plot milestone
      index from the new contracts.
    * ``mark_stale`` — whether to mark chapter plan/bridge files and the
      contract coherence report as stale (file preserved, with a flag).
    * ``prose_untouched`` — always True in this iteration; the runner will
      refuse to overwrite chapter_*.md files.  Reserved for forward compat.
    * ``sync_session_id`` — caller-supplied id used to key resume/undo
      artifacts.  When omitted, a timestamp-based id is generated.
    """

    project_id: str
    affected_chapter_numbers: list[int] = Field(default_factory=list)
    cascade_downstream: bool = True
    rebuild_milestones: bool = True
    mark_stale: bool = True
    prose_untouched: bool = True
    max_cascade_depth: int = Field(default=3, ge=1, le=10)
    sync_session_id: str | None = None
    cached_outline_fingerprint: str | None = Field(
        default=None,
        description="Optional pre-recorded fingerprint; used for resume/undo.",
    )


class ExtendOutlineRequest(BaseModel):
    """Extend a book, or complete its planning when target_total equals its current goal."""

    project_id: str
    additional_chapters: int | None = Field(default=None, ge=1)
    target_total: int | None = Field(default=None, ge=1, le=10000)
    decommission_old_ending: bool = True
    sync_contracts: bool = True
    reason: str = "extend_outline"

    @model_validator(mode="after")
    def validate_extend_mode(self) -> "ExtendOutlineRequest":
        has_additional = self.additional_chapters is not None
        has_target = self.target_total is not None
        if has_additional == has_target:
            raise ValueError("Provide exactly one of additional_chapters or target_total")
        return self


class PrepareChapterResponse(BaseModel):
    """Result of chapter preparation."""

    project_id: str
    chapter_number: int
    status: Literal["needs_decision", "completed"]
    checkpoint: DecisionCheckpoint | None = None


class AdvancePlanningHorizonRequest(BaseModel):
    """Resume an exact durable horizon request, never extend the total target."""

    project_id: str
    request_id: str
    chapter_number: int = Field(ge=1)
    target_chapter: int = Field(ge=1)
    attempt: int = Field(default=1, ge=1)
    explicit: bool = False


class SemanticConsistencyRefreshRequest(BaseModel):
    """Path-free request; the server resolves all affected semantic sources."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1, max_length=255)
    expected_story_version: str = Field(min_length=1, max_length=256)
    expected_policy_version: int = Field(ge=1)


class AuthoringMessageJobRequest(BaseModel):
    project_id: str
    message_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class ResolveChapterCheckpointRequest(BaseModel):
    """Resolve one chapter-studio checkpoint and continue the session."""

    project_id: str
    chapter_number: int = Field(ge=1)
    checkpoint_id: str
    option_id: str
    authoring_approval_id: str = ""
    notes: str = ""
    force: bool = Field(
        default=False,
        description=(
            "Force regeneration even if the chapter is already at or below the "
            "canon watermark. Recovery path for state desync: e.g. when canon "
            "advanced but the user wants to redo the chapter. Desktop UI normally "
            "uses PrepareChapterRequest.force for explicit regeneration flows; "
            "this field is the API/CLI escape hatch."
        ),
    )
    repair_control_mode: RepairControlMode | None = Field(
        default=None,
        description="Per-request repair control override. None follows runtime settings.",
    )


class ChapterSessionResult(BaseModel):
    """Checkpoint resolution result for chapter studio."""

    project_id: str
    chapter_number: int
    status: Literal["needs_decision", "completed"]
    checkpoint: DecisionCheckpoint | None = None
    word_count: int = 0
    overall_score: float | None = None
    continuity_score: float | None = None
    continuity_issue_count: int = 0
    reading_power_score: float | None = None
    reading_power_summary: str = ""
    bridge_summary: str = ""
    chapter_exit_summary: str = ""
    preview: str = ""
    applied_option_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class RepairChapterResponse(BaseModel):
    """Response for chapter repair endpoints (continuity / causal / combined)."""

    project_id: str
    chapter_number: int
    applied: bool = False
    failure_reason: str | None = None
    warnings: list[str] = Field(default_factory=list)
    repair_plan: dict[str, Any] | None = None


class PolishChapterRequest(BaseModel):
    """Polish a completed chapter for publication-quality prose."""

    project_id: str
    chapter_number: int = Field(ge=1)
    notes: str = ""


class BookConsistencyRequest(BaseModel):
    """Run a whole-book consistency audit across all completed chapters."""

    project_id: str
    chapter_range: list[int] = Field(
        default_factory=list,
        description="Specific chapters to audit; empty = all completed chapters.",
    )
    analysis_mode: Literal["auto", "summary", "full_text"] = Field(
        default="auto",
        description=(
            "分析模式：auto=按配置默认；summary=仅摘要；full_text=注入全文做长上下文深审。"
        ),
    )
    prompt_hint: str = Field(
        default="",
        description="本次全书审计附加提示词（可选）。",
    )
    location_strictness: Literal["strict", "balanced", "loose"] = Field(
        default="balanced",
        description="定位严格度：strict/balanced/loose。",
    )
    max_tokens: int | None = Field(
        default=None,
        ge=512,
        le=65536,
        description="覆盖默认输出 token 上限（可选）。",
    )
    temperature: float | None = Field(
        default=None,
        ge=0.0,
        le=2.0,
        description="覆盖默认温度（可选）。",
    )
    repair_mode: Literal["off", "targeted"] = Field(
        default="off",
        description="兼容字段；全书审计入口不再直接修复，正文修改请使用 GlobalRepairQueueRequest。",
    )
    parallel_chunks: bool = Field(
        default=True,
        description="全书审计分块并行化；启用后多块 LLM 调用同时发出（上限 5）。",
    )
    parallel_dimensions: bool = Field(
        default=True,
        description="全书审计维度并行化；启用后按维度裁剪上下文、依赖 DAG 调度并行审计。",
    )
    parallel_dimension_limit: int | None = Field(
        default=None,
        ge=1,
        le=6,
        description="维度并行化并发上限；None=使用全局设置。",
    )
    audit_max_chapters_per_batch: int = Field(
        default=12,
        ge=1,
        le=500,
        description="全书审计 full_text 模式下单次模型审查最多注入的章节数。",
    )
    audit_max_issues_per_chunk: int = Field(
        default=12,
        ge=1,
        le=50,
        description="全书审计单次模型调用最多返回的问题数，避免输出截断。",
    )
    audit_issue_pool_max_items: int = Field(
        default=160,
        ge=0,
        le=1000,
        description="注入审计提示词的问题面板问题池最大条目数，按严重度优先。",
    )
    chapter_max_chars: int | None = Field(
        default=None,
        ge=1000,
        le=100000,
        description="单章注入的最大字符数（仅 full_text 生效）；None=使用 settings 默认。",
    )
    repair_min_severity: Literal["critical", "warning", "info"] = Field(
        default="warning",
        description="兼容字段；修复队列执行入口使用自己的准入策略。",
    )
    repair_max_chapters: int = Field(
        default=20,
        ge=1,
        le=500,
        description="兼容字段；全书审计入口忽略，修复队列入口使用 max_items。",
    )
    allow_exhausted_retry: bool = Field(
        default=False,
        description="兼容字段；仅独立修复队列/章节修复入口可使用。",
    )
    use_issue_panel_pool: bool = Field(
        default=True,
        description="是否注入问题面板问题池（连贯性/因果报告）用于精准定位与索引匹配。",
    )
    repair_concurrency: int = Field(
        default=1,
        ge=1,
        le=8,
        description="兼容字段；全书审计入口不执行修复。",
    )
    generate_repair_report: bool = Field(
        default=True,
        description="兼容字段；全书审计仅生成审计报告和修复队列摘要。",
    )
    panel_first_expansion: bool = Field(
        default=True,
        description="兼容字段；修复队列编译阶段不在审计入口扩展正文修复范围。",
    )
    continue_from_audit: bool = Field(
        default=False,
        description="兼容字段；继续修复请使用独立修复队列入口。",
    )
    continue_audit_from_checkpoint: bool = Field(
        default=False,
        description="从上次分批审计检查点继续审计，跳过已完成的模型审计批次。",
    )
    reset_audit_checkpoint: bool = Field(
        default=False,
        description="开始本轮审计前清除已有分批审计检查点，用于从头重跑。",
    )
    rollback_on_failure: bool = Field(
        default=True,
        description="兼容字段；全书审计入口不改正文，因此不会触发回滚。",
    )
    verify_before_repair: bool = Field(
        default=True,
        description="兼容字段；precision gate/targeted verifier 由修复队列入口负责。",
    )
    post_repair_targeted_audit: bool = Field(
        default=False,
        description="兼容字段；修复后 targeted verification 由修复队列执行入口负责。",
    )
    repair_guard_enabled: bool = Field(
        default=True,
        description="兼容字段；正文污染防护属于独立修复队列/章节修复入口。",
    )
    repair_guard_max_delta_ratio: float = Field(
        default=0.20,
        ge=0.01,
        le=1.0,
        description="全书自动修复单章允许的最大正文改动比例，超过后转人工复核并回滚。",
    )
    repair_guard_max_added_chars: int = Field(
        default=1200,
        ge=100,
        le=10000,
        description="全书自动修复单章允许新增的最大字符数，超过后转人工复核并回滚。",
    )
    rollback_on_regression: bool = Field(
        default=False,
        description="兼容字段；全书审计入口不执行修复回滚。",
    )
    auto_continue: bool = Field(
        default=False,
        description="兼容字段；队列续修由独立修复队列入口控制。",
    )
    two_phase_enabled: bool = Field(
        default=True,
        description="是否启用两阶段审计：True=先 summary 快速扫描再 targeted full_text；False=直接全量 full_text。",
    )
    two_phase_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="两阶段审计标记率阈值。超过此值时按优先级截断目标章节，避免回退全量 full_text。",
    )
    two_phase_max_target_chapters: int = Field(
        default=24,
        ge=1,
        le=500,
        description="两阶段定向 full_text 最多审查的目标章节数。",
    )


class GlobalRepairQueueRequest(BaseModel):
    """Execute repair tickets produced by a whole-book global audit run."""

    project_id: str
    run_id: str = Field(
        default="",
        description="审计库 audit_runs.run_id；为空时使用最新已完成全局审计。",
    )
    statuses: list[GlobalRepairQueueStatus] = Field(
        default_factory=_default_global_repair_queue_statuses,
        description="要执行或处理的修复队列状态；自动改正文默认只处理 ready。",
    )
    max_items: int = Field(
        default=20,
        ge=1,
        le=500,
        description="本次最多处理的队列项。",
    )
    verify_before_apply: bool = Field(
        default=True,
        description="执行前重新检查 source_hash 与 targeted verifier。",
    )
    rollback_on_failure: bool = Field(
        default=True,
        description="队列项执行失败或验证回归时回滚该章节改动。",
    )
    concurrency: int = Field(
        default=1,
        ge=1,
        le=8,
        description="修复队列执行并发上限。",
    )


class BookEditorialAuditRequest(BaseModel):
    """Run a whole-book publication-level editorial audit."""

    project_id: str
    chapter_range: list[int] = Field(default_factory=list)
    prompt_hint: str = ""
    max_tokens: int = Field(default=8192, ge=512, le=65536)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    batch_size: int = Field(default=12, ge=1, le=100)
    batch_timeout_s: float = Field(
        default=0.0,
        ge=0.0,
        description="单个编辑审计批次的超时秒数；0 表示仅使用网关自身超时。",
    )


class ExportBookRequest(BaseModel):
    """Export completed chapters to a file format."""

    project_id: str
    format: Literal["markdown", "epub", "txt"] = "markdown"
    chapter_range: list[int] = Field(
        default_factory=list,
        description="Specific chapters to export; empty = all completed.",
    )
    output_dir: str = Field(
        default="",
        description="Custom output directory; empty = project exports/ dir.",
    )
    book_title: str = Field(
        default="",
        description="Custom book title for the exported file name; empty = use outline/spec title.",
    )
