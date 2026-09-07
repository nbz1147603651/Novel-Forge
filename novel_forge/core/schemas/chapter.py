"""Chapter result, audit, and outcome schemas."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field, field_validator

from novel_forge.core.guidance import RequirementSemantics
from novel_forge.core.schemas.base import VersionedSchema
from novel_forge.core.schemas.canon import CreativeReport
from novel_forge.core.schemas.continuity import ChapterBridge, ContinuityReport, RepairPlan
from novel_forge.core.schemas.eval_schema import EvalReport
from novel_forge.core.schemas.reading_power import ReadingPowerReport
from novel_forge.core.schemas.review import RepairTicket, RepairVerificationResult, ReviewFinding
from novel_forge.core.schemas.story_state import (
    ChapterExitState,
    CharacterState,
    CharacterStateDelta,
    PlotThreadDelta,
    RelationshipStateDelta,
)
from novel_forge.core.utils.type_coerce import stringify_text_value

if TYPE_CHECKING:
    from novel_forge.story_kernel.schemas import PromiseLedger, TimelineAnchor


class ChapterMeta(VersionedSchema):
    """Metadata for a generated chapter."""

    chapter_number: int = Field(ge=1)
    title: str = Field(default="")
    word_count: int = Field(default=0, ge=0)
    edit_rounds: int = Field(default=0, ge=0)
    tokens_used: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0.0)


class AlignmentReport(VersionedSchema):
    """Outline alignment check result for a generated chapter."""

    review_mode: str = Field(default="full_review")
    review_contract_version: int = Field(
        default=0,
        ge=0,
        description=(
            "0 for legacy score/list reports; 1+ means findings were normalized "
            "through the shared evidence-backed review contract."
        ),
    )
    alignment_score: float = Field(default=0.0, ge=0.0, le=10.0)
    risk_level: str = Field(default="medium", description="low / medium / high")
    conflict_level: str = Field(default="low", description="low / medium / high / critical")
    summary: str = Field(default="")
    missing_main_points: list[str] = Field(default_factory=list)
    supportive_subplot_points: list[str] = Field(default_factory=list)
    weak_subplot_points: list[str] = Field(default_factory=list)
    repair_actions: list[str] = Field(default_factory=list)
    review_findings: list[ReviewFinding] = Field(default_factory=list)
    repair_tickets: list[RepairTicket] = Field(default_factory=list)
    repair_verifications: list[RepairVerificationResult] = Field(default_factory=list)
    source_text_hash: str = Field(
        default="",
        description="SHA-256 hash of source chapter text used for this report.",
    )
    # ── Evaluation status ───────────────────────────────────────────────
    evaluation_status: str = Field(
        default="ok",
        description="评估状态：ok/fallback/timeout/degraded。"
        "fallback 表示未完成真实 LLM 诊断，不应参与门禁硬阻断",
    )
    is_fallback: bool = Field(
        default=False,
        description="是否为兜底报告。兜底报告不应参与门禁硬阻断决策",
    )
    fallback_reason: str = Field(
        default="",
        description="兜底原因摘要，如 timeout/llm_error/truncated_response",
    )

    @property
    def verified_blocking_findings(self) -> list[ReviewFinding]:
        """Return locally admitted blockers from the shared review contract."""

        return [
            finding
            for finding in self.review_findings
            if finding.blocks_finalize
            and finding.confidence >= 0.75
            and bool((finding.metadata or {}).get("source_evidence_verified", False))
            and (
                (finding.metadata or {}).get("coverage_status") == "missing"
                or bool((finding.metadata or {}).get("chapter_evidence_verified", False))
            )
        ]


class ChapterIssue(VersionedSchema):
    """One atomic, evidence-backed issue reported by ``CHECK_CHAPTER``.

    This is deliberately shared by factual, continuity, and expression checks.
    A checker finding is an auditable unit with one severity, one evidence anchor,
    and one repair goal; it must never be flattened into a collection of field
    fragments and subsequently turned into several repair tickets.
    """

    issue_type: str = Field(default="")
    severity: Literal["critical", "high", "medium", "low"] = Field(default="medium")
    summary: str = Field(default="")
    evidence: str = Field(default="")
    fix_suggestion: str = Field(default="")
    location: str = Field(default="")
    paragraph_start: int = Field(default=0, ge=0)
    paragraph_end: int = Field(default=0, ge=0)

    def __str__(self) -> str:
        """Keep log and prompt rendering concise while preserving typed storage."""

        return self.summary


def _normalize_chapter_issue_items(value: Any, *, default_type: str) -> list[Any]:
    """Normalize local string diagnostics into the atomic issue schema.

    Local deterministic checks are allowed to emit short strings, but the
    persisted report is always structured.  LLM dicts are intentionally kept as
    one item each instead of expanding their key/value pairs.
    """

    if value is None:
        return []
    raw_items = value if isinstance(value, list) else [value]
    normalized: list[Any] = []
    for item in raw_items:
        if isinstance(item, ChapterIssue):
            normalized.append(item)
        elif isinstance(item, dict):
            normalized.append({"issue_type": default_type, **item})
        else:
            text = stringify_text_value(item).strip()
            if text:
                normalized.append(
                    {
                        "issue_type": default_type,
                        "summary": text,
                        "evidence": text,
                    }
                )
    return normalized


class ChapterRepairReport(VersionedSchema):
    """Single-chapter correctness check (prompt leakage + factual/logic issues)."""

    review_mode: str = Field(default="full_review")
    risk_level: str = Field(default="medium", description="low / medium / high")
    summary: str = Field(default="")
    prompt_leaks: list[str] = Field(default_factory=list)
    factual_errors: list[ChapterIssue] = Field(default_factory=list)
    continuity_errors: list[ChapterIssue] = Field(default_factory=list)
    expression_errors: list[ChapterIssue] = Field(default_factory=list)
    repair_actions: list[str] = Field(default_factory=list)
    forbidden_element_candidates: list[dict[str, Any]] = Field(default_factory=list)
    forbidden_element_findings: list[dict[str, Any]] = Field(default_factory=list)
    source_text_hash: str = Field(default="")
    review_findings: list[ReviewFinding] = Field(default_factory=list)
    repair_tickets: list[RepairTicket] = Field(default_factory=list)
    repair_readiness: dict[str, Any] = Field(default_factory=dict)
    verification_results: list[RepairVerificationResult] = Field(default_factory=list)

    @field_validator("factual_errors", mode="before")
    @classmethod
    def _normalize_factual_errors(cls, value: Any) -> list[Any]:
        return _normalize_chapter_issue_items(value, default_type="factual_error")

    @field_validator("continuity_errors", mode="before")
    @classmethod
    def _normalize_continuity_errors(cls, value: Any) -> list[Any]:
        return _normalize_chapter_issue_items(value, default_type="chapter_continuity_error")

    @field_validator("expression_errors", mode="before")
    @classmethod
    def _normalize_expression_errors(cls, value: Any) -> list[Any]:
        return _normalize_chapter_issue_items(value, default_type="expression_clarity")


class CausalIssue(VersionedSchema):
    """A single causal-chain issue detected within a chapter."""

    issue_id: str = Field(
        default="",
        description="Stable diagnostic id used to track this causal issue across repair rounds.",
    )
    issue_type: str = Field(default="")
    severity: str = Field(default="medium")
    location: str = Field(default="")
    location_confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence of the paragraph/location anchor.",
    )
    anchor_type: str = Field(
        default="",
        description="How the issue was anchored: explicit_para / evidence_match / inferred_scope.",
    )
    paragraph_start: int = Field(
        default=0,
        ge=0,
        description="1-based first paragraph affected by this issue; 0 means unknown.",
    )
    paragraph_end: int = Field(
        default=0,
        ge=0,
        description="1-based last paragraph affected by this issue; 0 means unknown.",
    )
    summary: str = Field(default="")
    evidence: str = Field(default="")
    evidence_quote: str = Field(
        default="",
        description="Exact contiguous quote from the chapter text used as the repair anchor.",
    )
    fix_suggestion: str = Field(default="")
    fix_mode: str = Field(
        default="",
        description="Preferred repair mode: replace / insert / window / fulltext.",
    )
    affected_characters: list[str] = Field(
        default_factory=list,
        description="List of character names affected by this causal issue.",
    )
    rewrite_scope: str = Field(
        default="chapter",
        description="Scope of the rewrite: opening / middle / closing / chapter / paragraph.",
    )
    fix_actions: list[str] = Field(
        default_factory=list,
        description="Suggested fix actions for this issue.",
    )
    evidence_pairs: list[dict[str, Any]] = Field(default_factory=list)
    adjudication_notes: str = Field(default="")
    handoff_notes: str = Field(default="")
    repair_boundary: str = Field(default="")
    repair_scope: dict[str, Any] = Field(default_factory=dict)
    must_preserve: list[str] = Field(default_factory=list)
    forbidden_changes: list[str] = Field(default_factory=list)


class CausalValidationReport(VersionedSchema):
    """Heuristic causal-chain validation report for a chapter."""

    review_mode: str = Field(default="full_review")
    causal_score: float = Field(default=10.0, ge=0.0, le=10.0)
    summary: str = Field(default="")
    issues: list[CausalIssue] = Field(default_factory=list)
    causal_link_verified: bool = Field(default=True)
    validation_status: str = Field(
        default="ok",
        description="ok / unavailable. unavailable 表示校验过程失败，结果未知。",
    )
    source_text_hash: str = Field(
        default="",
        description="SHA-256 hash of source chapter text used for this report (compat field).",
    )
    review_findings: list[ReviewFinding] = Field(default_factory=list)
    repair_tickets: list[RepairTicket] = Field(default_factory=list)
    repair_readiness: dict[str, Any] = Field(default_factory=dict)
    verification_results: list[RepairVerificationResult] = Field(default_factory=list)
    pipeline_stage: str | None = Field(
        default=None,
        description="Pipeline stage tag injected during final hash-sync (compat field).",
    )


class PlotGuardEntityAction(VersionedSchema):
    """AI guardrail action for one newly introduced entity."""

    entity_type: str = Field(default="character", description="character / location / item")
    name: str = Field(default="", description="Entity name from creative report")
    action: str = Field(default="keep", description="keep / defer / merge / drop")
    reason: str = Field(default="", description="Short reason for this action")
    merge_target: str = Field(default="", description="Target entity name when action=merge")


class TargetedRepairDirective(VersionedSchema):
    """定向修复指令，用于 AI Judge 指定需要修复的具体问题。"""

    repair_type: str = Field(
        default="",
        description="修复类型：causal / continuity / character_consistency / plot_hole / pacing",
    )
    severity: str = Field(default="medium", description="low / medium / high / critical")
    description: str = Field(default="", description="问题描述")
    suggested_action: str = Field(default="", description="建议的修复动作")
    location_hint: str = Field(default="", description="问题位置提示（如'第3-5段'）")


class PlotGuardDecision(VersionedSchema):
    """AI guardrail decision after a chapter is generated."""

    decision: str = Field(
        default="continue",
        description=(
            "continue / continue_with_constraints / adjust_outline_fast / "
            "adjust_outline_smart / pause_for_human / rollback_and_regen"
        ),
    )
    risk_level: str = Field(default="medium", description="low / medium / high")
    outline_action: str = Field(default="none", description="none / fast / smart")
    entity_actions: list[PlotGuardEntityAction] = Field(default_factory=list)
    next_chapter_constraints: list[str] = Field(default_factory=list)
    reasoning_brief: str = Field(default="", description="One short rationale paragraph")
    targeted_repairs: list[TargetedRepairDirective] = Field(
        default_factory=list,
        description="定向修复指令列表，用于触发特定的修复循环",
    )


class ChapterOutcome(VersionedSchema):
    """Full structured outcome extracted from a chapter.

    Flattened replacement for the former ``CanonDelta`` + ``ExtractedCanon``
    classes.  Contains all delta fields directly (no nested ``canon_delta``).
    """

    # --- Fields from former CanonDelta ---
    source_chapter: int = Field(default=0, ge=0)
    character_updates: dict[str, CharacterState] = Field(default_factory=dict)
    new_events: list[TimelineAnchor] = Field(default_factory=list)
    foreshadowing_updates: list[PromiseLedger] = Field(default_factory=list)
    new_world_facts: dict[str, str] = Field(default_factory=dict)
    chapter_summary: str = Field(default="")
    new_banned_phrases: list[str] = Field(
        default_factory=list,
        description="本章新增的禁用短语",
    )

    # --- Fields from former ExtractedCanon ---
    creative_report: CreativeReport | None = None
    chapter_exit_state: ChapterExitState | None = None
    character_state_deltas: list[CharacterStateDelta] = Field(default_factory=list)
    relationship_deltas: list[RelationshipStateDelta] = Field(default_factory=list)
    plot_thread_deltas: list[PlotThreadDelta] = Field(default_factory=list)
    structured_summary: str = Field(default="")

    # --- ChapterOutcome extensions ---
    alignment_report: AlignmentReport | None = None
    text: str = Field(default="")

    @field_validator("new_banned_phrases")
    @classmethod
    def _truncate_new_banned_phrases(cls, v: list[str]) -> list[str]:
        phrases = [p[:50] for p in v[:30]]
        return phrases

    @field_validator("chapter_summary", "structured_summary", mode="before")
    @classmethod
    def _coerce_text_fields(cls, v: Any) -> str:
        return stringify_text_value(v)


class MacroGuardDimensions(VersionedSchema):
    """Five-dimension drift score for macro-level audit."""

    outline_alignment: float = Field(default=1.0, ge=0.0, le=1.0)
    character_arc_consistency: float = Field(default=1.0, ge=0.0, le=1.0)
    pacing_curve: float = Field(default=1.0, ge=0.0, le=1.0)
    foreshadowing_recovery: float = Field(default=1.0, ge=0.0, le=1.0)
    thematic_cohesion: float = Field(default=1.0, ge=0.0, le=1.0)


class MacroGuardFinding(VersionedSchema):
    """A single finding from macro guard audit."""

    severity: str = Field(default="medium", description="low / medium / high / critical")
    type: str = Field(
        default="",
        description="outline_drift / character_arc_drift / pacing_drift / foreshadowing_gap / thematic_drift",
    )
    description: str = Field(default="")
    evidence: list[str] = Field(default_factory=list)


class MacroGuardAdjustmentPlan(VersionedSchema):
    """Outline adjustment plan emitted by MacroGuard (future-window only)."""

    window_size: int = Field(default=0, ge=0, description="Number of future chapters to adjust")
    strategy: str = Field(
        default="",
        description=(
            "insert_breather_chapters / accelerate_plot / character_refocus / "
            "foreshadowing_catchup / thematic_reinforcement / rollback_to_anchor"
        ),
    )
    target_outline_v: str = Field(default="", description="Target outline version string")
    adjusted_chapter_goals: list[dict[str, Any]] = Field(
        default_factory=list,
        description="List of {chapter_number, goal, notes} for adjusted future chapters",
    )
    reasoning: str = Field(default="")


class MacroGuardReport(VersionedSchema):
    """Macro-level guard audit report (multi-chapter trajectory check)."""

    audit_id: str = Field(default="")
    chapters_audited: list[int] = Field(default_factory=list)
    drift_score: float = Field(default=0.0, ge=0.0, le=1.0)
    dimensions: MacroGuardDimensions = Field(default_factory=MacroGuardDimensions)
    findings: list[MacroGuardFinding] = Field(default_factory=list)
    recommended_action: str = Field(
        default="pass",
        description="pass / warning_hint / alert_plan / critical_rollback",
    )
    adjustment_plan: MacroGuardAdjustmentPlan | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reasoning: str = Field(default="")
    source_text_hash: str = Field(default="")


class ChapterResult(VersionedSchema):
    """Complete output of a chapter generation run."""

    meta: ChapterMeta
    text: str = Field(description="Final chapter prose.")
    canon_delta: ChapterOutcome | None = None
    creative_report: CreativeReport | None = None
    eval_report: EvalReport | None = None
    alignment_report: AlignmentReport | None = None
    chapter_repair_report: ChapterRepairReport | None = None
    causal_report: CausalValidationReport | None = None
    continuity_report: ContinuityReport | None = None
    repair_plan: RepairPlan | None = None
    bridge: ChapterBridge | None = None
    chapter_exit_state: ChapterExitState | None = None
    warnings: list[str] = Field(default_factory=list)
    macro_guard_report: MacroGuardReport | None = None
    quality_gate_report: dict[str, Any] | None = Field(
        default=None,
        description="Aggregated QualityGate report (PASS/WARN/FAIL + per-dimension checks).",
    )
    trace_summary: dict[str, object] = Field(default_factory=dict)
    reading_power_report: ReadingPowerReport | None = None
    tts_metadata: dict[str, Any] | None = Field(
        default=None,
        description=(
            "TTS 友好元数据（ChapterTTSMetadata），由 Finalize 阶段确定性生成。"
            "以 dict 存储以避免循环导入；TTS 模块消费时反序列化为 ChapterTTSMetadata。"
        ),
    )


# ---------------------------------------------------------------------------
# Resolve forward references for cross-module type annotations.
#
# TimelineAnchor and PromiseLedger are defined in story_kernel.schemas.
# In the circular-import path (story_kernel loaded first) the import
# below fails and story_kernel/schemas.py handles the rebuild.
# In the normal path (chapter loaded first) we import and rebuild here.
# ---------------------------------------------------------------------------
def _rebuild_chapter_models() -> None:
    from novel_forge.story_kernel.schemas import (
        PromiseLedger as _PromiseLedger,
    )
    from novel_forge.story_kernel.schemas import (
        TimelineAnchor as _TimelineAnchor,
    )

    chapter_ns = {
        "PromiseLedger": _PromiseLedger,
        "TimelineAnchor": _TimelineAnchor,
    }

    ChapterOutcome.model_rebuild(_types_namespace=chapter_ns)
    ChapterResult.model_rebuild(_types_namespace=chapter_ns)
    ChapterRepairReport.model_rebuild(_types_namespace=chapter_ns)
    CausalValidationReport.model_rebuild(_types_namespace=chapter_ns)


try:
    _rebuild_chapter_models()
except ImportError:
    pass  # Circular import path — story_kernel/schemas.py will rebuild


# ---------------------------------------------------------------------------
# Cross-scene intent (added for 6-phase long-form pipeline)
# ---------------------------------------------------------------------------
# PLAN 阶段必出 2 字段：DRAFT 看不到，WAVE 必看。WAVE 据此做跨场景编织。

from pydantic import ConfigDict  # noqa: E402

# Re-export the imported types so tests can find them via chapter module
__all__ = [
    "ChapterMeta",
    "AlignmentReport",
    "ChapterRepairReport",
    "CausalIssue",
    "CausalValidationReport",
    "PlotGuardEntityAction",
    "TargetedRepairDirective",
    "PlotGuardDecision",
    "ChapterOutcome",
    "MacroGuardDimensions",
    "MacroGuardFinding",
    "MacroGuardAdjustmentPlan",
    "MacroGuardReport",
    "ChapterResult",
    "CrossSceneRef",
    "CrossSceneIntent",
]


CrossSceneRefType = Literal["callback", "foreshadow", "parallel", "contrast", "echo"]


class CrossSceneRef(VersionedSchema):
    """A reference is optional unless its requirement explicitly binds it."""

    model_config = ConfigDict(extra="forbid")

    from_scene: str = Field(default="", description="Source scene id (e.g. 'scene_01').")
    to_scene: str = Field(default="", description="Target scene id (e.g. 'scene_03').")
    ref_type: CrossSceneRefType = Field(
        default="callback",
        description="callback / foreshadow / parallel / contrast / echo",
    )
    description: str = Field(
        default="",
        description="What the reference should do (callback motif / parallel action / etc.).",
    )
    requirement: RequirementSemantics = Field(default_factory=RequirementSemantics)


class CrossSceneIntent(VersionedSchema):
    """Lightweight cross-scene constraint package.

    Produced by PLAN, consumed only by WAVE.  DRAFT must NOT see it.

    Two fields only (by plan lock):
      * ``cross_scene_references``: list of CrossSceneRef
      * ``pacing_curve``: list[int], 1-5 per scene, length = scene count
    """

    model_config = ConfigDict(extra="forbid")

    cross_scene_references: list[CrossSceneRef] = Field(
        default_factory=list,
        description="Optional references and explicitly required narrative handoffs; may be empty.",
    )
    pacing_curve: list[int] = Field(
        default_factory=list,
        description="Per-scene intensity level (1-5). Length must equal scene count.",
    )
