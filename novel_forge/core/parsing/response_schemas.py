"""Lightweight response-envelope schemas for LLM output structural validation.

These schemas validate the **structure** (field types) of LLM JSON responses at
the system boundary, before Step-level normalization.  They intentionally use
lenient settings (``extra='allow'``, wide coercions) so only gross type errors
(score as "excellent", list as integer) trigger a retry — fixable issues handled
by per-Step normalizers pass through silently.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from novel_forge.common.constants import TaskType
from novel_forge.core.authoring import AuthoringReply
from novel_forge.core.guidance import LiteralRequirement, RequirementSemantics
from novel_forge.core.schemas.future_planning import FutureOutlineDecision
from novel_forge.core.schemas.init_coherence import CoherenceClaim
from novel_forge.core.utils.type_coerce import coerce_text_list

# ---------------------------------------------------------------------------
# Reusable coercion helpers
# ---------------------------------------------------------------------------


def _coerce_to_list(v: Any) -> list[Any]:
    """Accept list, wrap single dict/str, reject other types."""
    if v is None:
        return []
    if isinstance(v, (dict, str)):
        return [v]
    if not isinstance(v, list):
        raise ValueError(f"expected list, got {type(v).__name__}")
    return v


def _coerce_to_float(v: Any) -> float:
    """Accept numeric or numeric-string, reject non-numeric."""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            raise ValueError(f"expected numeric value, got {v!r}") from None
    raise ValueError(f"expected numeric value, got {type(v).__name__}")


def _coerce_to_int(v: Any) -> int:
    """Accept integer or integer-string, reject non-integral values."""
    if isinstance(v, bool):
        raise ValueError("expected integer value, got bool")
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str):
        text = v.strip()
        try:
            return int(text)
        except ValueError:
            raise ValueError(f"expected integer value, got {v!r}") from None
    raise ValueError(f"expected integer value, got {type(v).__name__}")


def _coerce_to_dict(v: Any) -> dict[str, Any]:
    """Require dict — no coercion."""
    if not isinstance(v, dict):
        raise ValueError(f"expected dict, got {type(v).__name__}")
    return v


def _coerce_to_str(v: Any) -> str:
    """Accept str, coerce None → ''."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    return str(v)


def _coerce_to_text_list(v: Any) -> list[str]:
    """Accept scalar/list/object policy snippets as a list of strings."""
    return coerce_text_list(
        v,
        preferred_keys=("policy", "rule", "description", "requirement", "text", "content"),
    )


LenientList = Annotated[list[Any], BeforeValidator(_coerce_to_list)]
LenientDictList = Annotated[list[dict[str, Any]], BeforeValidator(_coerce_to_list)]
LenientIntList = Annotated[list[int], BeforeValidator(_coerce_to_list)]
LenientTextList = Annotated[list[str], BeforeValidator(_coerce_to_text_list)]
LenientFloat = Annotated[float, BeforeValidator(_coerce_to_float)]
LenientInt = Annotated[int, BeforeValidator(_coerce_to_int)]
LenientDict = Annotated[dict[str, Any], BeforeValidator(_coerce_to_dict)]
LenientStr = Annotated[str, BeforeValidator(_coerce_to_str)]

# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class _LenientBase(BaseModel):
    """Base for response envelope schemas — extra fields always allowed."""

    # Gateway response envelopes validate the normal parsing contract only:
    # canonical prompt field names must be present. Legacy alias rescue belongs
    # in explicit task adapters or domain-schema compatibility paths.
    model_config = ConfigDict(extra="allow", populate_by_name=False)


class _BridgeCausalLinkResponse(BaseModel):
    """Strict nested handoff: causal fields must stay machine-readable."""

    model_config = ConfigDict(extra="forbid", populate_by_name=False)

    previous_event: LenientStr = ""
    causal_mechanism: LenientStr = ""
    unresolved_question: LenientStr = ""
    open_threads: LenientTextList = []


class _BridgeRelationshipBeatResponse(BaseModel):
    """Strict nested handoff for the relationship state at the opening."""

    model_config = ConfigDict(extra="forbid", populate_by_name=False)

    current_trust_level: LenientStr = "试探"
    unspoken_tension: LenientStr = ""
    power_dynamic: LenientStr = ""


# ---------------------------------------------------------------------------
# Chapter pipeline: main chain
# ---------------------------------------------------------------------------


class _BridgeChapterResponse(_LenientBase):
    opening_time: str = ""
    opening_location: str = ""
    opening_pov: str = ""
    transition_mode: str = ""
    emotional_carryover: str = ""
    action_handoff: str = ""
    causal_link: _BridgeCausalLinkResponse = Field(default_factory=_BridgeCausalLinkResponse)
    pending_questions: LenientTextList = []
    forbidden_repetition: LenientTextList = []
    bridge_summary: str = ""
    relationship_beat: _BridgeRelationshipBeatResponse | None = None
    sensory_anchors: LenientTextList = []
    opening_acceptance_criteria: LenientTextList = []


class _SpecEnrichResponse(_LenientBase):
    title: str = ""
    genre: str = ""
    theme: str
    tone: str = ""
    length_target: StrictInt
    language: str = "zh"
    characters_hint: str = ""
    world_hint: str = ""
    conflict_hint: str = ""
    pov_hint: str = ""
    opening_style: str = ""
    ending_style: str = ""
    extra_instructions: str = ""


class _BlueprintElementSelectResponse(_LenientBase):
    genre_inference: LenientList = []
    required_ids: LenientList = []
    extension_ids: LenientList = []
    extension_selection: LenientList = []
    focus_constraints: LenientList = []
    selector_summary: str = ""


class _CreativeConfigResponse(_LenientBase):
    theme: str = ""
    premise: str = ""
    genre: str = ""
    tone: str = ""
    length_target: StrictInt = 3000
    total_chapters: StrictInt = 24
    words_per_chapter: StrictInt = 4500
    chapters_per_volume: StrictInt = 0
    volume_mode: str = "auto"
    writing_mode: str = "auto"
    title: str = ""
    language: str = "zh"
    characters_hint: str = ""
    world_hint: str = ""
    conflict_hint: str = ""
    pov_hint: str = ""
    opening_style: str = ""
    ending_style: str = ""
    polish_hint: str = ""
    extra_instructions: str = ""
    project_id: str = ""
    max_edit_rounds: StrictInt = 0
    polish_suggestions: LenientList = []
    creative_note: LenientDict = {}


class _PlanCharacterMotivationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    character: str
    motivation: str
    stake: str


class _PlanPovKnowledgeConstraintsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    forbidden_knowledge: list[str]
    sensory_limits: list[str]
    scope_label: Literal["limited", "omniscient", "objective"]


class _PlanSceneIntentResponse(BaseModel):
    """Canonical scene execution contract published to scene-mode providers."""

    model_config = ConfigDict(extra="forbid")

    scene_id: str
    summary: str
    purpose: str = ""
    conflict: str
    required_characters: list[str]
    character_motivations: list[_PlanCharacterMotivationResponse]
    entry_state_refs: list[str] = []
    required_outcome: str
    dramatic_question: str = ""
    exit_target_state: str
    location: str
    time_marker: str
    relationship_dynamics: str
    emotional_beat: str
    sensory_notes: str
    sensory_focus: str
    dialogue_subtext: str
    choice_pressure: str
    scene_resistance: str
    dialogue_voice_targets: dict[str, str]
    revelation_level: str
    symbol_usage_policy: str
    body_signal_budget: int = Field(ge=0, le=5)
    target_words: int = Field(ge=0)
    pov_character: str
    pov_scope: Literal["limited", "omniscient", "objective"]
    pov_switch_allowed: bool
    pov_switch_marker_required: bool
    pov_knowledge_constraints: _PlanPovKnowledgeConstraintsResponse
    world_rule_ids: list[str] = []
    world_rule_usage: str = ""
    world_rule_evidence_expectations: list[str] = []
    world_rule_forbidden_boundaries: list[str] = []
    scene_goal: str
    owned_events: list[str]
    owned_revelations: list[str]
    owned_state_changes: list[str]
    forbidden_overlap: list[str]
    handoff_to_next: str
    dependency_scene_ids: list[str]
    parallel_group: str
    draft_order: int = Field(ge=0)
    entry_state: str
    exit_state: str


PlanSceneIntentList = Annotated[
    list[_PlanSceneIntentResponse],
    BeforeValidator(_coerce_to_list),
]


class _PlanCrossSceneReferenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_scene: str
    to_scene: str
    ref_type: Literal["callback", "foreshadow", "parallel", "contrast", "echo"]
    description: str
    requirement: RequirementSemantics = Field(default_factory=RequirementSemantics)


class _PlanCrossSceneIntentResponse(_LenientBase):
    """Two-field PLAN -> WAVE handoff package."""

    model_config = ConfigDict(extra="forbid")

    cross_scene_references: list[_PlanCrossSceneReferenceResponse]
    pacing_curve: LenientIntList


class _PlanWorldRuleApplicationResponse(_LenientBase):
    """Typed PLAN_CHAPTER world-rule handoff exposed to provider JSON Schema.

    Keep this envelope lenient so recoverable provider drift can still reach the
    domain compatibility layer, while publishing the canonical nested field names
    to the model instead of the previous unconstrained ``items: {}`` schema.
    """

    rule_id: str
    scene_id: str = ""
    applicability: Literal["applied", "not_applicable"] = "applied"
    usage: str = ""
    expected_evidence: str = ""
    forbidden_boundary: str = ""
    not_applicable_reason: str = ""


PlanWorldRuleApplicationList = Annotated[
    list[_PlanWorldRuleApplicationResponse],
    BeforeValidator(_coerce_to_list),
]


class _PlanLiteralContractResponse(_LenientBase):
    """Planning-owned declaration of wording-rigid prose."""

    model_config = ConfigDict(extra="forbid")

    contract_id: str = Field(min_length=1)
    literal: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    placement_hint: str = Field(min_length=1)
    requirement: LiteralRequirement = Field(default_factory=LiteralRequirement)


PlanLiteralContractList = Annotated[
    list[_PlanLiteralContractResponse],
    BeforeValidator(_coerce_to_list),
]


class _PlanChapterResponse(_LenientBase):
    scene_intents: LenientList
    world_rule_applications: PlanWorldRuleApplicationList = []
    chapter_type: str = ""
    opening_contract: str = ""
    closing_contract: str = ""
    required_state_transitions: LenientList = []
    required_literals: PlanLiteralContractList = Field(default_factory=list)
    emotional_arc: str = ""
    relationship_evolution: LenientList = []
    forbidden_elements: LenientList = []
    forbidden_elements_soft: LenientList = []
    forbidden_elements_quota: LenientList = []
    intentional_callbacks: LenientList = []
    foreshadowing_plan: LenientList = []
    key_revelations: LenientList = []
    cross_scene_intent: _PlanCrossSceneIntentResponse


class _PlanOutlineResponse(_LenientBase):
    synopsis: str = ""
    volume_mode: bool = False
    volumes: LenientList = []
    narrative_phases: LenientList = []
    key_turning_points: LenientList = []
    character_arcs: LenientList = []
    subplot_plan: LenientList = []
    suspense_schedule: LenientList = []
    ending_strategy: str = ""
    emotional_arcs: LenientList = []
    causal_chains: LenientList = []
    subplot_collisions: LenientList = []
    subversion_points: LenientList = []
    chapter_rhythm_curve: LenientList = []


class _PlanOutlineBatchResponse(_LenientBase):
    chapters: LenientList = []


class _PlanChapterScenesPayloadResponse(_LenientBase):
    """Scene-mode Planning payload with all mandatory downstream handoffs.

    Scene-mode plans are normalized into the same ``ChapterPlan`` consumed by
    Draft/Wave/Review.  Keep the wide creative payload lenient, but publish and
    require the three machine-checked handoffs that must never be defaulted away:
    scene ownership, world-rule applications, and cross-scene intent.
    """

    scene_intents: PlanSceneIntentList
    world_rule_applications: PlanWorldRuleApplicationList
    required_literals: PlanLiteralContractList
    cross_scene_intent: _PlanCrossSceneIntentResponse
    chapter_type: str = ""
    opening_contract: str = ""
    closing_contract: str = ""
    required_state_transitions: LenientList = []
    emotional_arc: str = ""
    relationship_evolution: LenientList = []
    forbidden_elements: LenientList = []
    forbidden_elements_soft: LenientList = []
    forbidden_elements_quota: LenientList = []
    intentional_callbacks: LenientList = []
    foreshadowing_plan: LenientList = []
    key_revelations: LenientList = []


class _PlanChapterScenesResponse(_LenientBase):
    scene_plan: _PlanChapterScenesPayloadResponse


class _ValidateScenePlanResponse(_LenientBase):
    valid: bool = False
    issues: LenientList = []
    parallel_groups: LenientList = []
    serial_edges: LenientList = []
    summary: str = ""


class _CheckAlignmentResponse(_LenientBase):
    alignment_score: LenientFloat
    risk_level: str = "medium"
    summary: str = ""
    findings: LenientList = []
    missing_main_points: LenientTextList = []
    supportive_subplot_points: LenientTextList = []
    weak_subplot_points: LenientTextList = []
    repair_actions: LenientTextList = []


class _CheckContinuityResponse(_LenientBase):
    continuity_score: LenientFloat
    mode: str = "full_review"
    summary: str = ""
    issues: LenientList = []


class _ValidateCausalResponse(_LenientBase):
    causal_score: LenientFloat
    summary: str = ""
    causal_link_verified: bool = True
    issues: LenientList = []


class _ExtractCanonResponse(_LenientBase):
    # Legacy monolithic extraction callers may echo the chapter envelope.
    # Declare it explicitly so the strict top-level contract can distinguish
    # sanctioned lineage metadata from model-authored spillover.
    chapter_number: LenientInt = 0
    canon_delta: LenientDict
    creative_report: LenientDict
    chapter_exit_state: LenientDict
    character_state_deltas: LenientList = []
    relationship_deltas: LenientList = []
    plot_thread_deltas: LenientList = []
    structured_summary: str = ""


class _ExtractCanonDeltaResponse(_LenientBase):
    canon_delta: LenientDict = {}


class _ExtractChapterSummaryExitResponse(_LenientBase):
    chapter_exit_state: LenientDict = {}
    structured_summary: str = ""


class _ExtractCreativeReportResponse(_LenientBase):
    creative_report: LenientDict = {}


class _ExtractCharacterStateDeltasResponse(_LenientBase):
    character_state_deltas: LenientList = []


class _ExtractRelationshipDeltasResponse(_LenientBase):
    relationship_deltas: LenientList = []


class _ExtractPlotThreadDeltasResponse(_LenientBase):
    plot_thread_deltas: LenientList = []


class _EntityRecordResponse(_LenientBase):
    entity_id: str
    name: str
    entity_type: str = "unknown"
    aliases: LenientList = []
    source: str = ""
    notes: str = ""

    @model_validator(mode="before")
    @classmethod
    def _coerce_dynamic_entity_key(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if value.get("entity_id") and value.get("name"):
            return value

        known_fields = {"entity_id", "name", "entity_type", "aliases", "source", "notes"}
        dynamic_keys = [
            key
            for key in value
            if isinstance(key, str)
            and key not in known_fields
            and key.startswith(("char_", "loc_", "item_", "org_", "concept_", "ent_"))
        ]
        if len(dynamic_keys) != 1:
            return value

        dynamic_key = dynamic_keys[0]
        dynamic_value = value.get(dynamic_key)
        normalized = dict(value)
        normalized.setdefault("entity_id", dynamic_key)
        if "name" not in normalized:
            if isinstance(dynamic_value, dict):
                nested_name = dynamic_value.get("name")
                if nested_name:
                    normalized["name"] = nested_name
                for nested_key in ("entity_type", "aliases", "source", "notes"):
                    if nested_key not in normalized and nested_key in dynamic_value:
                        normalized[nested_key] = dynamic_value[nested_key]
            else:
                normalized["name"] = str(dynamic_value or "").strip()
        normalized.pop(dynamic_key, None)
        return normalized

    @field_validator("entity_id", "name", mode="before")
    @classmethod
    def _require_text(cls, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("field must be a non-empty string")
        return text


_EntityRecordList = Annotated[list[_EntityRecordResponse], BeforeValidator(_coerce_to_list)]


class _EntityRegistryResponse(_LenientBase):
    entities: _EntityRecordList = []


class _NarrativeContractResponse(_LenientBase):
    world_rules: LenientList = []
    character_arcs: LenientList = []
    plot_threads: LenientList = []
    promise_plan: LenientList = []
    notes: str = ""


class _ChapterContractsResponse(_LenientBase):
    # LenientList kept intentionally: the strict per-item ChapterContract
    # constraint is enforced at generation time via the format-contract
    # json_schema (see format_contracts.py PLAN_CHAPTER_CONTRACTS).  The
    # response envelope only needs structural leniency so malformed LLM
    # output can reach the repair layer rather than failing at the gateway.
    chapter_contracts: LenientList = []


class _InitCoherenceProfileResponse(_LenientBase):
    genre_tags: LenientList = []
    narrative_modes: LenientList = []
    project_ontology: LenientDict
    conflict_lens: LenientList = []
    extraction_guidance: LenientList = []
    summary: str = ""


class _InitCoherenceOntologyResponse(_LenientBase):
    genre_tags: LenientList = []
    narrative_modes: LenientList = []
    project_ontology: LenientDict = {}


class _InitCoherenceExtractionGuideResponse(_LenientBase):
    extraction_guidance: LenientList = []


class _InitCoherenceConflictRulesResponse(_LenientBase):
    conflict_lens: LenientList = []


class _InitCoherencePayoffRulesResponse(_LenientBase):
    payoff_types: LenientList = []
    summary: str = ""


class _InitCoherenceClaimsResponse(_LenientBase):
    claims: list[CoherenceClaim] = []
    coverage_status: Literal["complete", "partial", "uncertain"]
    unprocessed_source_refs: LenientList = []
    summary: str = ""

    @model_validator(mode="after")
    def validate_coverage_evidence(self) -> _InitCoherenceClaimsResponse:
        refs = [str(ref).strip() for ref in self.unprocessed_source_refs if str(ref).strip()]
        if self.coverage_status == "complete" and refs:
            raise ValueError("complete coverage cannot include unprocessed source refs")
        if self.coverage_status != "complete" and not refs:
            raise ValueError("incomplete coverage must identify unprocessed source refs")
        return self


class _InitStoryBibleResponse(_LenientBase):
    story_bible: LenientDict = {}


class _InitStoryCorePremiseResponse(_LenientBase):
    story_core: LenientDict = {}


class _InitStoryWorldRulesResponse(_LenientBase):
    world_rules: LenientDict = {}


class _InitStoryContinuityRulesResponse(_LenientBase):
    continuity_rules: LenientDict = {}


class _InitStoryThemesAndSymbolsResponse(_LenientBase):
    themes_and_symbols: LenientDict = {}


class _InitCharacterBibleResponse(_LenientBase):
    character_bible: LenientDict = {}


class _InitCharacterRosterResponse(_LenientBase):
    character_roster: LenientList = []


class _InitCharacterProfilesResponse(_LenientBase):
    character_profiles: LenientList = []


class _InitCharacterRelationshipMatrixResponse(_LenientBase):
    relationship_matrix: LenientList = []


class _InitCharacterArcPlanResponse(_LenientBase):
    character_arcs: LenientList = []


class _CreativeDirectionPacketResponse(BaseModel):
    """Typed creative seed published to prompt-only structured-output models."""

    model_config = ConfigDict(extra="forbid", populate_by_name=False)

    emotional_engine: LenientTextList = []
    thematic_promises: LenientTextList = []
    signature_motifs: LenientTextList = []
    relationship_tensions: LenientTextList = []
    anti_cliche_rules: LenientTextList = []
    scene_potential: LenientTextList = []
    notes: LenientStr = ""


class _CreativeDirectionCandidateResponse(BaseModel):
    """Closed candidate handoff consumed by ``CreativeDirectionCandidate``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=False)

    candidate_id: LenientStr
    packet: _CreativeDirectionPacketResponse
    preserved_intent_ids: LenientTextList = []
    assumptions: LenientTextList = []
    hard_constraint_risks: LenientTextList = []
    intent_conflicts: LenientTextList = []


CreativeDirectionCandidateList = Annotated[
    list[_CreativeDirectionCandidateResponse],
    BeforeValidator(_coerce_to_list),
]


class _CreativeDirectionCandidatesResponse(_LenientBase):
    candidates: CreativeDirectionCandidateList = Field(min_length=1, max_length=2)


class _CreativeDirectionDecisionResponse(_LenientBase):
    selected_candidate_id: LenientStr = ""
    diversity_score: float = 0.0
    confidence: float = 0.0
    need_third_candidate: bool = False
    selection_reason: LenientStr = ""


class _ContractCoherenceResponse(_LenientBase):
    schema_version: LenientStr = "audit_v2"
    dimension: LenientStr = ""
    verdict: LenientStr = ""
    score: float = 1.0
    issues: LenientList = []
    summary: LenientStr = ""
    metadata: dict[str, Any] = {}
    source_refs: LenientList = []
    repair_scope: LenientList = []
    preserve: LenientList = []
    change_intent: LenientStr = ""
    blocked: bool = False


class _ResearchDossierSynthesisResponse(_LenientBase):
    """SYNTHESIZE_INIT_RESEARCH_DOSSIER — 资料包合成结果。"""

    summary: LenientStr = ""
    real_world_constraints: LenientList = []
    terminology: LenientList = []
    inspiration_notes: LenientList = []
    uncertainty_notes: LenientList = []
    source_refs: LenientList = []


class _GroundOutlineResearchResponse(_LenientBase):
    """GROUND_OUTLINE_RESEARCH — 大纲资料校准结果。"""

    summary: LenientStr = ""
    global_notes: LenientList = []
    chapter_notes: LenientList = []
    fact_risks: LenientList = []
    terminology: LenientList = []
    source_refs: LenientList = []


class _PlannedResearchQueryItem(BaseModel):
    """Strict content contract for one initialization research query."""

    model_config = ConfigDict(extra="forbid")

    query: str
    rationale: str
    intent: str
    priority: Literal["must", "should", "nice"]
    locale: str
    source_preferences: list[str]
    recency_required: bool
    risk_if_missing: str


class _PlanInitResearchQueriesResponse(_LenientBase):
    """PLAN_INIT_RESEARCH_QUERIES — LLM 搜索查询规划。"""

    queries: list[_PlannedResearchQueryItem]
    knowledge_gaps: list[str]
    warnings: list[str] = []


class _SynthesizeModelPriorResearchResponse(_LenientBase):
    """SYNTHESIZE_MODEL_PRIOR_RESEARCH — 模型先验知识合成。"""

    notes: LenientList = []
    terminology: LenientList = []
    uncertainty_notes: LenientList = []


class _InitArtifactPatchResponse(_LenientBase):
    patches: LenientList = []
    summary: str = ""


class _InitCreativeRefinementResponse(_LenientBase):
    suggestions: LenientList = []
    repair_scope: LenientList = []
    patches: LenientList = []
    preserve: LenientList = []
    risks: LenientList = []
    summary: str = ""


class _CandidateStateDeltasResponse(_LenientBase):
    candidates: LenientList = []


class _CharacterIntroductionAdjudicationResponse(_LenientBase):
    decisions: LenientList = []
    summary: str = ""


class _EntityReferenceAdjudicationResponse(_LenientBase):
    """Envelope only; evidence/candidate parity is validated downstream."""

    decisions: LenientList = []
    summary: str = ""


class _IntroduceCharacterResponse(_LenientBase):
    name: str = ""
    role: str = ""
    gender: str = ""
    social_status: str = ""
    abilities: LenientList = []
    appearance: str = ""
    personality: str = ""
    backstory: str = ""
    arc: str = ""
    relationships: LenientDict = {}
    voice: str = ""
    notes: str = ""


class _EnrichCharacterResponse(_LenientBase):
    appearance: str = ""
    personality: str = ""
    backstory: str = ""
    arc: str = ""
    relationships: LenientDict = {}
    voice: str = ""
    gender: str = ""
    social_status: str = ""
    abilities: LenientList = []


class _StateDeltaAdjudicationResponse(_LenientBase):
    candidate_id: str = ""
    verdict: str = ""
    severity: str = ""
    rationale: str = ""
    confidence: LenientFloat = 0.0
    covered_target_ids: LenientList = []
    coverage_status: str = ""
    issue_kind: str = ""
    repair_kind: str = ""
    evidence_quotes: LenientList = []
    affected_state_paths: LenientList = []
    repair_instruction: str = ""
    pending_reason: str = ""


class _ContractCompletionAdjudicationResponse(_LenientBase):
    verdict: str = ""
    severity: str = ""
    rationale: str = ""
    repair_or_replan_decision: str = ""
    missing_required_progressions: LenientList = []
    missing_knowledge_ops: LenientList = []
    forbidden_progression_hits: LenientList = []
    future_leak_hits: LenientList = []
    evidence_quotes: LenientList = []
    should_block_archive: bool = False
    contract_completion_score: LenientFloat = 10.0
    cognitive_constraint_hits: LenientList = []
    unexpected_progressions: LenientList = []
    unaccepted_knowledge_ops: LenientList = []


class _FinalStateAdjudicationResponse(_LenientBase):
    chapter_number: LenientInt = 0
    verdict: str = ""
    severity: str = ""
    confidence: LenientFloat = 0.0
    accepted_candidate_ids: LenientList = []
    rejected_candidate_ids: LenientList = []
    pending_candidate_ids: LenientList = []
    repair_candidate_ids: LenientList = []
    state_updates: LenientList = []
    pending_items: LenientList = []
    repair_issues: LenientList = []
    target_coverage: LenientList = []
    coverage_matrix: LenientList = []
    should_block_archive: bool = False
    summary: str = ""


class _MotifOccurrenceResponse(_LenientBase):
    paragraph_index: StrictInt = 0
    text_snippet: str = ""
    context: str = ""
    associated_characters: LenientList = []
    emotional_tone: str = ""
    narrative_function: str = ""
    function_relation: Literal["unknown", "new_function", "redundant"] = "unknown"
    function_evidence: str = ""


class _MotifItemResponse(_LenientBase):
    motif_id: str = ""
    name: str = ""
    category: str = ""
    category_confidence: LenientFloat = 0.0
    category_reason: str = ""
    secondary_categories: LenientList = []
    motif_role: str = ""
    importance_score: LenientFloat = 0.0
    description: str = ""
    thematic_meaning: str = ""
    is_intentional: bool = False
    occurrences: list[_MotifOccurrenceResponse] = []


class _ExtractMotifsResponse(_LenientBase):
    motifs: list[_MotifItemResponse]


class _AdjustedChapterResponse(_LenientBase):
    chapter_number: StrictInt
    title: str = ""
    goal: str = ""
    beats_summary: LenientList = []
    pov_character: str = ""
    setting: str = ""
    expected_word_count: StrictInt = 0
    notes: str = ""


class _AdjustOutlineResponse(_LenientBase):
    adjusted_chapters: list[_AdjustedChapterResponse]
    adjustment_summary: str = ""


class _PatchChapterResponse(_LenientBase):
    patches: LenientList = []


class _PolishOutlineResponse(_LenientBase):
    adjusted_chapters: LenientList = []
    polish_suggestions: LenientList = []


class _PolishSubplotResponse(_LenientBase):
    subplots: LenientList = []


class _CompressionItemsResponse(_LenientBase):
    items: LenientList = []


class _CriticContinuityResponse(_LenientBase):
    issues: LenientList = []


class _CriticCharacterResponse(_LenientBase):
    issues: LenientList = []


class _CriticCausalResponse(_LenientBase):
    causal_breaks: LenientList = []


class _CriticStrengthsResponse(_LenientBase):
    strengths: LenientList = []


# ---------------------------------------------------------------------------
# Chapter pipeline: auxiliary checks
# ---------------------------------------------------------------------------


class _CheckChapterResponse(_LenientBase):
    risk_level: str = "medium"
    summary: str = ""
    prompt_leaks: LenientList = []
    factual_errors: LenientList = []
    continuity_errors: LenientList = []
    expression_errors: LenientList = []
    repair_actions: LenientList = []
    forbidden_element_findings: LenientList = []


class _ElementProgressArbiterResponse(_LenientBase):
    status: str = ""
    confidence: LenientFloat = 0.0
    reason: str = ""


class _GuardConstraintCheckResponse(_LenientBase):
    status: str = ""
    confidence: LenientFloat = 0.0
    evidence: str = ""
    notes: str = ""


class _PlotGuardDecisionResponse(_LenientBase):
    decision: str = ""
    risk_level: str = ""
    outline_action: str = ""
    entity_actions: LenientList = []
    next_chapter_constraints: LenientList = []
    reasoning_brief: str = ""
    targeted_repairs: LenientList = []


class _MacroGuardAuditResponse(_LenientBase):
    dimensions: LenientDict
    recommended_action: str = ""
    drift_score: LenientFloat = 0.0
    findings: LenientList = []
    adjustment_plan: LenientDict = {}
    confidence: LenientFloat = 0.0
    reasoning: str = ""


class _VolumeAuditResponse(_LenientBase):
    volume_number: StrictInt
    volume_summary: str
    milestone_status: LenientList = []
    consistency_score: LenientFloat = 0.0
    consistency_issues: LenientList = []
    carry_over_characters: LenientList = []
    retire_characters: LenientList = []
    carry_over_items: LenientList = []
    retire_items: LenientList = []
    carry_over_world_fact_keys: LenientList = []
    retire_world_fact_keys: LenientList = []
    carry_over_foreshadowing_ids: LenientList = []
    resolved_foreshadowing_ids: LenientList = []
    next_volume_focus: str = ""
    token_optimization_notes: LenientList = []


class _BookConsistencyEvidencePairResponse(_LenientBase):
    chapter_number: LenientInt = 0
    evidence: str = ""
    claim: str = ""


class _BookConsistencyLinkedIssueRefResponse(_LenientBase):
    chapter_number: LenientInt = 0
    lane: str = ""
    index: LenientInt = 0


class _BookConsistencyIssueResponse(_LenientBase):
    issue_id: str = ""
    category: str = ""
    severity: str = ""
    chapters_involved: list[LenientInt] = []
    primary_chapter: LenientInt = 0
    issue_type: str = ""
    location: str = ""
    paragraph_index: LenientInt = 0
    paragraph_span: list[LenientInt] = []
    evidence: str = ""
    description: str = ""
    suggestion: str = ""
    fix_mode: str = ""
    fix_action: str = ""
    confidence: LenientFloat = 0.0
    evidence_pairs: list[_BookConsistencyEvidencePairResponse] = []
    verification_questions: LenientList = []
    handoff_notes: str = ""
    linked_issue_refs: list[_BookConsistencyLinkedIssueRefResponse] = []


_BookConsistencyIssueList = Annotated[
    list[_BookConsistencyIssueResponse],
    BeforeValidator(_coerce_to_list),
]


class _BookConsistencyRepairPlanResponse(_LenientBase):
    chapter_number: LenientInt = 0
    issue_ids: LenientList = []
    priority: str = ""
    strategy: str = ""


_BookConsistencyRepairPlanList = Annotated[
    list[_BookConsistencyRepairPlanResponse],
    BeforeValidator(_coerce_to_list),
]


class _BookConsistencyRepairScopeResponse(_LenientBase):
    target: str = ""
    allowed_changes: LenientList = []
    forbidden_changes: LenientList = []
    preserve: LenientList = []


class _BookConsistencyPostconditionResponse(_LenientBase):
    check: str = ""
    expected: str = ""


class _BookConsistencyVerifiedIssueResponse(_LenientBase):
    issue_id: str = ""
    status: str = ""
    description: str = ""
    severity: str = ""
    confidence: LenientFloat = 0.0
    evidence: str = ""
    paragraph_index: LenientInt = 0
    paragraph_span: list[LenientInt] = []
    location: str = ""
    anchor_type: str = ""
    location_confidence: LenientFloat = 0.0
    evidence_pairs: list[_BookConsistencyEvidencePairResponse] = []
    adjudication_notes: str = ""
    repair_scope: _BookConsistencyRepairScopeResponse = _BookConsistencyRepairScopeResponse()
    fix_mode: str = ""
    fix_action: str = ""
    postconditions: list[_BookConsistencyPostconditionResponse] = []
    rejection_reason: str = ""


_BookConsistencyVerifiedIssueList = Annotated[
    list[_BookConsistencyVerifiedIssueResponse],
    BeforeValidator(_coerce_to_list),
]


class _BookConsistencyResponse(_LenientBase):
    issues: _BookConsistencyIssueList = []
    repair_plan: _BookConsistencyRepairPlanList = []
    summary: str = ""
    consistency_score: LenientFloat = 0.0


class _BookConsistencyIssuesOnlyResponse(_LenientBase):
    issues: _BookConsistencyIssueList = []


class _BookConsistencyVerifyResponse(_LenientBase):
    verified_issues: _BookConsistencyVerifiedIssueList = []


class _EditorialFindingResponse(_LenientBase):
    issue_type: str = ""
    severity: str = ""
    chapter_number: LenientInt = 0
    summary: str = ""
    evidence: LenientList = []
    recommendation: str = ""
    confidence: LenientFloat = 0.0
    metadata: LenientDict = {}


_EditorialFindingList = Annotated[
    list[_EditorialFindingResponse],
    BeforeValidator(_coerce_to_list),
]


class _EditorialAuditResponse(_LenientBase):
    summary: str = ""
    findings: _EditorialFindingList = []
    revision_plan: LenientList = []
    metrics: LenientDict = {}


class _EditorialCharacterVoicesResponse(_LenientBase):
    character_voices: LenientList = []


class _EditorialStructureResponse(_LenientBase):
    climax_markers: LenientList = []
    denouement_budget: LenientDict = {}
    revelation_ladder: LenientList = []
    time_bridge_policies: LenientTextList = []
    title_policy: LenientDict = {}


class _EditorialStyleConstraintsResponse(_LenientBase):
    theme_policies: LenientTextList = []
    symbol_policies: LenientList = []
    scene_resistance_rules: LenientList = []
    expression_channel_budget: LenientDict = {}
    expression_channel_profiles: LenientList = []
    body_signal_budget_per_high_emotion_scene: LenientInt = 0
    forbidden_confirmation_phrases: LenientTextList = []
    revision_priorities: LenientTextList = []


class _ExpressionObservationExtractionResponse(_LenientBase):
    observations: LenientList = []
    skipped_reason: str = ""
    source_text_hash: str = ""


class _EditorialElementDirectivesResponse(_LenientBase):
    editorial_element_directives: LenientList = []


class _EditorialContractResponse(_LenientBase):
    project_title: str = ""
    character_voices: LenientList = []
    climax_markers: LenientList = []
    denouement_budget: LenientDict = {}
    theme_policies: LenientTextList = []
    symbol_policies: LenientList = []
    scene_resistance_rules: LenientList = []
    expression_channel_budget: LenientDict = {}
    expression_channel_profiles: LenientList = []
    body_signal_budget_per_high_emotion_scene: LenientInt = 0
    forbidden_confirmation_phrases: LenientTextList = []
    revision_priorities: LenientTextList = []
    revelation_ladder: LenientList = []
    editorial_element_directives: LenientList = []
    time_bridge_policies: LenientTextList = []
    title_policy: LenientDict = {}


# ---------------------------------------------------------------------------
# Short-form & evaluation
# ---------------------------------------------------------------------------


class _EvaluateResponse(_LenientBase):
    scores: LenientList
    overall_score: LenientFloat
    passed: bool = False
    threshold: LenientFloat = 6.0
    summary: str = ""
    repair_suggestions: LenientList = []


class _BeatResponse(_LenientBase):
    """Typed fields that must be enforced before the domain Beat schema."""

    sequence: LenientInt = Field(ge=1)
    summary: str
    tension_level: LenientInt = Field(default=5, ge=1, le=10)


def _coerce_beats_to_list(value: Any) -> list[Any]:
    """Keep legacy dict-of-beats responses compatible with the typed envelope."""
    if isinstance(value, dict):
        return list(value.values())
    return _coerce_to_list(value)


class _BeatsResponse(_LenientBase):
    beats: Annotated[list[_BeatResponse], BeforeValidator(_coerce_beats_to_list)]


class _ReadingPowerResponse(_LenientBase):
    hook_type: str = ""
    hook_strength: str = ""
    hook_description: str = ""
    prev_hook_fulfilled: bool = True
    micro_payoffs: LenientList = []
    is_transition: bool = False
    next_chapter_reason: str = ""
    outline_hook_match: LenientDict | None = None
    outline_payoff_coverage: LenientDict | None = None
    resolved_suspense_ids: LenientList = []
    unresolved_suspense_ids: LenientList = []
    information_pacing: str = ""
    information_pacing_score: LenientFloat = 0.0
    main_plot_depth: str = ""
    main_plot_advancement_notes: str = ""
    tension_match: str = ""
    tension_match_score: LenientFloat = 0.0
    revelation_count: LenientInt = 0
    revelation_over_budget: bool = False
    consecutive_main_plot_stall: LenientInt = 0
    character_drive: str = ""
    character_drive_notes: str = ""
    suggestions: LenientTextList = []


class _ReconcileEntitiesResolutionResponse(_LenientBase):
    unresolved_name: str = ""
    resolution: str = ""
    # ``remove`` resolutions legitimately carry null for the entity identity
    # fields — the model is telling us the name should not exist.  Treating
    # them as required strings turned a valid remove verdict into a
    # ValidationError retry storm (observed with deepseek-v4-flash).
    canonical_name: str | None = None
    entity_type: str | None = None
    relationship: str | None = None
    confidence: LenientFloat = 0.0
    reasoning: str = ""


_ReconcileEntitiesResolutionList = Annotated[
    list[_ReconcileEntitiesResolutionResponse],
    BeforeValidator(_coerce_to_list),
]


class _ReconcileEntitiesResponse(_LenientBase):
    resolutions: _ReconcileEntitiesResolutionList = []


class _RepairStrategyDiagnoseResponse(_LenientBase):
    preferred_strategy: str = ""
    confidence: LenientFloat = 0.0
    reason: str = ""
    root_causes: LenientList = []
    risk_flags: LenientList = []
    diagnostic_summary: str = ""


class _AuditPovDriftResponse(_LenientBase):
    verdict: str = ""
    issues: LenientList = []


class _HumanizeScanResponse(_LenientBase):
    source_text_hash: str = ""
    chapter_number: LenientInt = 0
    total_hits: LenientInt = 0
    hits_by_category: LenientDict = {}
    critical_hits: LenientInt = 0
    pattern_hits: LenientList = []
    humanize_score: LenientFloat = 0.0
    summary: str = ""


class _VerifyCompressionResponse(_LenientBase):
    quality_score: LenientFloat = 0.0
    recommendation: str = ""


class _SemanticRepairVerifyResponse(_LenientBase):
    issue_resolved: bool = False
    confidence: LenientFloat = 0.0
    reasoning: str = ""


class _ProfileStyleResponse(_LenientBase):
    modules: LenientList = []
    source_elements: LenientList = []
    summary: str = ""
    global_style: LenientDict = {}


class _ProfileStructureResponse(_LenientBase):
    hook_config: LenientDict = {}
    strand_config: LenientDict = {}
    micro_payoff_config: LenientDict = {}
    cool_point_config: LenientDict = {}


class _ShortBlueprintResponse(_LenientBase):
    synopsis: str = ""
    anchor_elements: LenientDict
    narrative_phases: LenientList = []
    turning_points: LenientList = []
    character_arcs: LenientList = []
    emotional_arc: str = ""
    ending_strategy: str = ""


class _ShortCreativeSummaryResponse(_LenientBase):
    characters: LenientList = []
    narrative_analysis: LenientDict
    thematic_analysis: LenientDict
    creative_highlights: LenientList = []
    improvement_suggestions: LenientList = []
    beat_fulfillment: LenientList = []


class _SummaryResponse(_LenientBase):
    summary: str = ""


class _SummaryDriftCheckResponse(_LenientBase):
    issues: LenientList = []
    facts_checked: LenientInt = 0
    facts_missing: LenientInt = 0
    facts_contradicted: LenientInt = 0
    summary: str = ""


class _KnowledgeBoundaryAuditResponse(_LenientBase):
    verdict: str = ""
    issues: LenientList = []


class _KnowledgeBoundaryEntryResponse(_LenientBase):
    known_facts: LenientList = []
    suspected: LenientList = []
    misbeliefs: LenientList = []
    secrets_kept: LenientList = []
    sensory_access_rules: LenientList = []


class _KnowledgeBoundaryCharacterResponse(_LenientBase):
    character_id: str = ""
    name: str = ""
    knowledge_boundaries: LenientDict = {}


class _InitKnowledgeBoundariesResponse(_LenientBase):
    characters: LenientList = []


class _KnowledgeDeltaEntryResponse(_LenientBase):
    entity_id: str = ""
    fact: str = ""
    knowledge_type: str = ""
    visibility: str = ""
    source_chapter: LenientInt = 0


class _ExtractKnowledgeDeltasResponse(_LenientBase):
    knowledge_deltas: LenientList = []


class _RepairKnowledgeBoundaryResponse(_LenientBase):
    repaired_text: str = ""
    changes: LenientList = []


# ---------------------------------------------------------------------------
# TTS / voice pipeline
# ---------------------------------------------------------------------------


class _TTSBuildNarratorProfileResponse(_LenientBase):
    voice_type: str
    base_speed: LenientFloat
    emotional_range: str
    narration_distance: str
    style_keywords: LenientTextList
    speed_range_low: LenientFloat = 0.8
    speed_range_high: LenientFloat = 1.2
    genre_adaptation: LenientDict = {}
    emotion_speed_modifiers: LenientDict = {}
    emotion_volume_modifiers: LenientDict = {}
    sample_narration_text: str = ""
    notes: str = ""


class _TTSGenerateDubbingScriptResponse(_LenientBase):
    segments: LenientDictList
    bgm_suggestions: LenientDictList
    sfx_cues: LenientDictList
    soundscapes: LenientDictList
    scene_transitions: LenientDictList


class _TTSSpokenTextRewriteResponse(_LenientBase):
    rewrites: LenientDictList


class _TTSEmotionLabelResponse(_LenientBase):
    emotions: LenientDictList


class _TTSScriptSegmentAdjudicationResponse(_LenientBase):
    decisions: LenientDictList
    summary: str


class _TTSDubbingScriptReviewResponse(_LenientBase):
    decisions: LenientDictList
    reviewed_segment_count: LenientInt
    overall_verdict: str
    summary: str


class _TTSDubbingStyleProfileResponse(_LenientBase):
    language: str
    confidence: LenientFloat
    narration_traits: LenientTextList
    dialogue_traits: LenientTextList
    rhythm_rules: LenientTextList
    pause_rules: LenientTextList
    performance_direction_rules: LenientTextList
    sound_design_rules: LenientTextList
    forbidden_tendencies: LenientTextList


class _TTSVoiceMatchAdjudicationResponse(_LenientBase):
    decisions: LenientDictList
    summary: str


class _TTSSoundDesignResponse(_LenientBase):
    sfx_cues: LenientDictList
    bgm_needs: LenientDictList
    soundscapes: LenientDictList
    scene_transitions: LenientDictList


class _AdaptScreenplayResponse(_LenientBase):
    title: str
    scenes: LenientDictList


class _DramaSeriesPlanResponse(_LenientBase):
    title: str
    total_episodes: int = 0
    three_acts: LenientTextList
    paywall_beats: LenientDictList
    thrill_matrix: LenientDictList
    waveform_stages: LenientDictList
    antagonist_system: LenientDictList


class _EpisodeOutlineResponse(_LenientBase):
    episodes: LenientDictList


class _EpisodeScreenplayResponse(_LenientBase):
    episode_number: int = 0
    scenes: LenientDictList


class _ComplianceCheckResponse(_LenientBase):
    findings: LenientDictList
    summary: str = ""


class _VisionQcScoreResponse(_LenientBase):
    dimensions: LenientDictList
    overall_score: float = 0.0
    summary: str = ""


class _ComicPanelLayoutResponse(_LenientBase):
    pages: LenientDictList
    notes: str = ""


class _FilmShotLayoutResponse(_LenientBase):
    scenes: LenientDictList
    notes: str = ""


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_RESPONSE_SCHEMAS: dict[TaskType, type[BaseModel]] = {
    TaskType.AUTHORING_CHAT: AuthoringReply,
    # Main chapter chain
    TaskType.SPEC_ENRICH: _SpecEnrichResponse,
    TaskType.BLUEPRINT_ELEMENT_SELECT: _BlueprintElementSelectResponse,
    TaskType.GENERATE_CONFIG: _CreativeConfigResponse,
    TaskType.POLISH_CONFIG: _CreativeConfigResponse,
    TaskType.BRIDGE_CHAPTER: _BridgeChapterResponse,
    TaskType.PLAN_OUTLINE: _PlanOutlineResponse,
    TaskType.PLAN_OUTLINE_BATCH: _PlanOutlineBatchResponse,
    TaskType.PLAN_OUTLINE_CONTINUE: _PlanOutlineBatchResponse,
    TaskType.PLAN_CHAPTER: _PlanChapterResponse,
    TaskType.PLAN_CHAPTER_SCENES: _PlanChapterScenesResponse,
    TaskType.VALIDATE_SCENE_PLAN: _ValidateScenePlanResponse,
    TaskType.CHECK_ALIGNMENT: _CheckAlignmentResponse,
    TaskType.CHECK_CONTINUITY: _CheckContinuityResponse,
    TaskType.VALIDATE_CAUSAL: _ValidateCausalResponse,
    TaskType.EXTRACT_CANON: _ExtractCanonResponse,
    TaskType.EXTRACT_CANON_DELTA: _ExtractCanonDeltaResponse,
    TaskType.EXTRACT_CHAPTER_SUMMARY_EXIT: _ExtractChapterSummaryExitResponse,
    TaskType.EXTRACT_CREATIVE_REPORT: _ExtractCreativeReportResponse,
    TaskType.EXTRACT_CHARACTER_STATE_DELTAS: _ExtractCharacterStateDeltasResponse,
    TaskType.EXTRACT_RELATIONSHIP_DELTAS: _ExtractRelationshipDeltasResponse,
    TaskType.EXTRACT_PLOT_THREAD_DELTAS: _ExtractPlotThreadDeltasResponse,
    TaskType.EXTRACT_EXPRESSION_OBSERVATIONS: _ExpressionObservationExtractionResponse,
    TaskType.INIT_ENTITY_REGISTRY: _EntityRegistryResponse,
    TaskType.INIT_NARRATIVE_CONTRACT: _NarrativeContractResponse,
    TaskType.PLAN_CHAPTER_CONTRACTS: _ChapterContractsResponse,
    TaskType.INIT_STORY_BIBLE: _InitStoryBibleResponse,
    TaskType.INIT_STORY_CORE_PREMISE: _InitStoryCorePremiseResponse,
    TaskType.INIT_STORY_WORLD_RULES: _InitStoryWorldRulesResponse,
    TaskType.INIT_STORY_CONTINUITY_RULES: _InitStoryContinuityRulesResponse,
    TaskType.INIT_STORY_THEMES_AND_SYMBOLS: _InitStoryThemesAndSymbolsResponse,
    TaskType.INIT_CHARACTER_BIBLE: _InitCharacterBibleResponse,
    TaskType.INIT_CHARACTER_ROSTER: _InitCharacterRosterResponse,
    TaskType.INIT_CHARACTER_PROFILE_BATCH: _InitCharacterProfilesResponse,
    TaskType.INIT_CHARACTER_RELATIONSHIP_MATRIX: _InitCharacterRelationshipMatrixResponse,
    TaskType.INIT_CHARACTER_ARC_PLAN: _InitCharacterArcPlanResponse,
    TaskType.INIT_CREATIVE_DIRECTION_CANDIDATES: _CreativeDirectionCandidatesResponse,
    TaskType.INIT_CREATIVE_DIRECTION_SELECT: _CreativeDirectionDecisionResponse,
    TaskType.DERIVE_INIT_COHERENCE_PROFILE: _InitCoherenceProfileResponse,
    TaskType.REFINE_INIT_COHERENCE_PROFILE: _InitCoherenceProfileResponse,
    TaskType.INIT_COHERENCE_ONTOLOGY: _InitCoherenceOntologyResponse,
    TaskType.INIT_COHERENCE_EXTRACTION_GUIDE: _InitCoherenceExtractionGuideResponse,
    TaskType.INIT_COHERENCE_CONFLICT_RULES: _InitCoherenceConflictRulesResponse,
    TaskType.INIT_COHERENCE_PAYOFF_RULES: _InitCoherencePayoffRulesResponse,
    TaskType.EXTRACT_INIT_COHERENCE_CLAIMS: _InitCoherenceClaimsResponse,
    TaskType.EXTRACT_BLUEPRINT_HOLISTIC_CLAIMS: _InitCoherenceClaimsResponse,
    TaskType.ADJUDICATE_INIT_CONFLICT_CANDIDATES: _ContractCoherenceResponse,
    TaskType.ADJUDICATE_BLUEPRINT_COHERENCE: _ContractCoherenceResponse,
    TaskType.ADJUDICATE_OUTLINE_INHERITANCE: _ContractCoherenceResponse,
    TaskType.ADJUDICATE_CONTRACT_COHERENCE: _ContractCoherenceResponse,
    TaskType.REPAIR_INIT_ARTIFACT_PATCH: _InitArtifactPatchResponse,
    TaskType.REFINE_INIT_ARTIFACTS_FROM_SYNOPSIS: _InitCreativeRefinementResponse,
    TaskType.SYNTHESIZE_INIT_RESEARCH_DOSSIER: _ResearchDossierSynthesisResponse,
    TaskType.GROUND_OUTLINE_RESEARCH: _GroundOutlineResearchResponse,
    TaskType.PLAN_INIT_RESEARCH_QUERIES: _PlanInitResearchQueriesResponse,
    TaskType.SYNTHESIZE_MODEL_PRIOR_RESEARCH: _SynthesizeModelPriorResearchResponse,
    TaskType.EXTRACT_CANDIDATE_STATE_DELTAS: _CandidateStateDeltasResponse,
    TaskType.ADJUDICATE_ENTITY_REFERENCES: _EntityReferenceAdjudicationResponse,
    TaskType.ADJUDICATE_CHARACTER_INTRODUCTION: _CharacterIntroductionAdjudicationResponse,
    TaskType.INTRODUCE_CHARACTER: _IntroduceCharacterResponse,
    TaskType.ENRICH_CHARACTER: _EnrichCharacterResponse,
    TaskType.ADJUDICATE_STATE_DELTA: _StateDeltaAdjudicationResponse,
    TaskType.ADJUDICATE_CONTRACT_COMPLETION: _ContractCompletionAdjudicationResponse,
    TaskType.ADJUDICATE_FACT_CONFLICT: _StateDeltaAdjudicationResponse,
    TaskType.ADJUDICATE_FINAL_STATE: _FinalStateAdjudicationResponse,
    TaskType.EXTRACT_MOTIFS: _ExtractMotifsResponse,
    TaskType.ADJUST_OUTLINE: _AdjustOutlineResponse,
    TaskType.RECONCILE_ENTITIES: _ReconcileEntitiesResponse,
    TaskType.REPAIR_STRATEGY_DIAGNOSE: _RepairStrategyDiagnoseResponse,
    TaskType.AUDIT_POV_DRIFT: _AuditPovDriftResponse,
    TaskType.PATCH_CHAPTER: _PatchChapterResponse,
    TaskType.POLISH_OUTLINE: _PolishOutlineResponse,
    TaskType.REVIEW_FUTURE_OUTLINE: FutureOutlineDecision,
    TaskType.POLISH_SUBPLOT: _PolishSubplotResponse,
    TaskType.CONTEXT_COMPRESS: _CompressionItemsResponse,
    TaskType.ADAPTIVE_COMPRESS: _CompressionItemsResponse,
    TaskType.CRITIC_CONTINUITY: _CriticContinuityResponse,
    TaskType.CRITIC_CHARACTER: _CriticCharacterResponse,
    TaskType.CRITIC_CAUSAL: _CriticCausalResponse,
    TaskType.CRITIC_STRENGTHS: _CriticStrengthsResponse,
    # Auxiliary
    TaskType.CHECK_CHAPTER: _CheckChapterResponse,
    TaskType.CHECK_EDITORIAL: _EditorialAuditResponse,
    TaskType.ELEMENT_PROGRESS_ARBITER: _ElementProgressArbiterResponse,
    TaskType.GUARD_CONSTRAINT_CHECK: _GuardConstraintCheckResponse,
    TaskType.PLOT_GUARD_JUDGE: _PlotGuardDecisionResponse,
    TaskType.MACRO_GUARD_AUDIT: _MacroGuardAuditResponse,
    TaskType.SUMMARY_DRIFT_CHECK: _SummaryDriftCheckResponse,
    TaskType.KNOWLEDGE_BOUNDARY_AUDIT: _KnowledgeBoundaryAuditResponse,
    TaskType.INIT_KNOWLEDGE_BOUNDARIES: _InitKnowledgeBoundariesResponse,
    TaskType.EXTRACT_KNOWLEDGE_DELTAS: _ExtractKnowledgeDeltasResponse,
    TaskType.REPAIR_KNOWLEDGE_BOUNDARY: _RepairKnowledgeBoundaryResponse,
    TaskType.TTS_BUILD_NARRATOR_PROFILE: _TTSBuildNarratorProfileResponse,
    TaskType.TTS_GENERATE_DUBBING_SCRIPT: _TTSGenerateDubbingScriptResponse,
    TaskType.TTS_REWRITE_SPOKEN_TEXT: _TTSSpokenTextRewriteResponse,
    TaskType.TTS_EMOTION_LABEL: _TTSEmotionLabelResponse,
    TaskType.TTS_ADJUDICATE_SCRIPT_SEGMENTS: _TTSScriptSegmentAdjudicationResponse,
    TaskType.TTS_REVIEW_DUBBING_SCRIPT: _TTSDubbingScriptReviewResponse,
    TaskType.TTS_ANALYZE_DUBBING_STYLE: _TTSDubbingStyleProfileResponse,
    TaskType.TTS_ADJUDICATE_VOICE_MATCH: _TTSVoiceMatchAdjudicationResponse,
    TaskType.TTS_SOUND_DESIGN: _TTSSoundDesignResponse,
    TaskType.ADAPT_SCREENPLAY: _AdaptScreenplayResponse,
    TaskType.COMPLIANCE_CHECK: _ComplianceCheckResponse,
    TaskType.VISION_QC_SCORE: _VisionQcScoreResponse,
    TaskType.DRAMA_SERIES_PLAN: _DramaSeriesPlanResponse,
    TaskType.EPISODE_OUTLINE: _EpisodeOutlineResponse,
    TaskType.EPISODE_SCREENPLAY: _EpisodeScreenplayResponse,
    TaskType.COMIC_PANEL_LAYOUT: _ComicPanelLayoutResponse,
    TaskType.FILM_SHOT_LAYOUT: _FilmShotLayoutResponse,
    TaskType.VOLUME_AUDIT: _VolumeAuditResponse,
    TaskType.DERIVE_EDITORIAL_CONTRACT: _EditorialContractResponse,
    TaskType.DERIVE_EDITORIAL_CHARACTER_VOICES: _EditorialCharacterVoicesResponse,
    TaskType.DERIVE_EDITORIAL_STRUCTURE: _EditorialStructureResponse,
    TaskType.DERIVE_EDITORIAL_STYLE_CONSTRAINTS: _EditorialStyleConstraintsResponse,
    TaskType.DERIVE_EDITORIAL_ELEMENT_DIRECTIVES: _EditorialElementDirectivesResponse,
    TaskType.BOOK_CONSISTENCY: _BookConsistencyResponse,
    TaskType.BOOK_CONSISTENCY_NAMING: _BookConsistencyIssuesOnlyResponse,
    TaskType.BOOK_CONSISTENCY_TIMELINE: _BookConsistencyIssuesOnlyResponse,
    TaskType.BOOK_CONSISTENCY_WORLD_RULE: _BookConsistencyIssuesOnlyResponse,
    TaskType.BOOK_CONSISTENCY_CHARACTER_STATE: _BookConsistencyIssuesOnlyResponse,
    TaskType.BOOK_CONSISTENCY_PLOT_THREAD: _BookConsistencyIssuesOnlyResponse,
    TaskType.BOOK_CONSISTENCY_NARRATIVE_DRIFT: _BookConsistencyIssuesOnlyResponse,
    TaskType.BOOK_CONSISTENCY_VERIFY: _BookConsistencyVerifyResponse,
    TaskType.BOOK_EDITORIAL_AUDIT: _EditorialAuditResponse,
    TaskType.BOOK_EDITORIAL_STRUCTURE_AUDIT: _EditorialAuditResponse,
    TaskType.BOOK_EDITORIAL_VOICE_AUDIT: _EditorialAuditResponse,
    TaskType.BOOK_EDITORIAL_LANGUAGE_AUDIT: _EditorialAuditResponse,
    TaskType.BOOK_EDITORIAL_THEME_SYMBOL_AUDIT: _EditorialAuditResponse,
    TaskType.BOOK_EDITORIAL_ELEMENT_AUDIT: _EditorialAuditResponse,
    # Short-form & evaluation
    TaskType.SHORT_BLUEPRINT: _ShortBlueprintResponse,
    TaskType.EVALUATE: _EvaluateResponse,
    TaskType.EVALUATE_READING_POWER: _ReadingPowerResponse,
    TaskType.BEATS: _BeatsResponse,
    TaskType.PROFILE_STYLE: _ProfileStyleResponse,
    TaskType.PROFILE_STRUCTURE: _ProfileStructureResponse,
    TaskType.VERIFY_COMPRESSION: _VerifyCompressionResponse,
    TaskType.REPAIR_SEMANTIC_VERIFY: _SemanticRepairVerifyResponse,
    TaskType.HUMANIZE_SCAN: _HumanizeScanResponse,
    TaskType.SHORT_CREATIVE_SUMMARY: _ShortCreativeSummaryResponse,
    TaskType.SUMMARIZE_CHAPTER: _SummaryResponse,
    TaskType.SUMMARIZE_VOLUME: _SummaryResponse,
    TaskType.SUMMARIZE_ARC: _SummaryResponse,
    TaskType.SUMMARIZE_SCENE: _SummaryResponse,
}


def get_response_schema(task_type: TaskType) -> type[BaseModel] | None:
    """Return the response envelope schema for *task_type*, or ``None``."""
    return _RESPONSE_SCHEMAS.get(task_type)


# ---------------------------------------------------------------------------
# Public validation entry point
# ---------------------------------------------------------------------------


def validate_response_schema(data: dict[str, Any], task_type: TaskType) -> None:
    """Validate *data* against the registered response schema for *task_type*.

    Does nothing when no schema is registered.
    Raises ``ValueError`` (via Pydantic ``ValidationError``) when the envelope
    structure is fundamentally wrong — callers should treat this as retriable.
    """
    schema = _RESPONSE_SCHEMAS.get(task_type)
    if schema is None:
        return
    normalized = schema.model_validate(data)
    if task_type == TaskType.INIT_ENTITY_REGISTRY:
        data.clear()
        data.update(normalized.model_dump(mode="json"))
