"""Typed prompt-context boundary for the long-chapter execution flow."""

from __future__ import annotations

from typing import Any

from pydantic import Field, ValidationInfo, field_validator

from novel_forge.common.constants import TaskType
from novel_forge.prompts.context_types.base import PromptContextModel, validate_prompt_context
from novel_forge.prompts.context_types.patch_chapter import (
    PatchChapterContext,
    PatchHistoryGuidanceContext,
)


class ChapterPositionPromptContext(PromptContextModel):
    """Whole-book position metadata shared by chapter-flow prompts."""

    chapter_number: int
    total_chapters: int
    is_last_chapter: bool
    is_previously_final_chapter: bool
    remaining_chapters: int
    position_label: str


class ChapterFlowPromptContext(PromptContextModel):
    """Common envelope shared by long-chapter prompt tasks."""

    chapter_number: int = 0
    chapter_text: str = ""
    stage_cards: dict[str, Any] = Field(default_factory=dict)
    chapter_position: ChapterPositionPromptContext | None = None

    @field_validator("chapter_position", mode="before")
    @classmethod
    def _normalize_empty_chapter_position(cls, value: Any) -> Any:
        return None if value in (None, {}) else value


_DRAFT_SCENE_DEFAULTS: dict[str, Any] = {
    "scene_id": "",
    "scene_goal": "",
    "purpose": "",
    "summary": "",
    "conflict": "",
    "required_characters": [],
    "entry_state": "",
    "entry_state_refs": [],
    "exit_state": "",
    "exit_target_state": "",
    "owned_events": [],
    "owned_revelations": [],
    "owned_state_changes": [],
    "forbidden_overlap": [],
    "required_outcome": "",
    "target_words": 0,
    "location": "",
    "time_marker": "",
    "pov_character": "",
    "pov_scope": "",
    "pov_knowledge_constraints": {},
    "world_rule_ids": [],
    "world_rule_usage": "",
    "world_rule_evidence_expectations": [],
    "world_rule_forbidden_boundaries": [],
    "sensory_notes": "",
    "choice_pressure": "",
    "scene_resistance": "",
    "body_signal_budget": 0,
    "dialogue_voice_targets": [],
    "symbol_usage_policy": "",
    "handoff_to_next": "",
    "dependency_scene_ids": [],
    "draft_order": 0,
    "parallel_group": "",
}


class DraftScenePromptContext(ChapterFlowPromptContext):
    current_scene: dict[str, Any]
    completed_scene_handoffs: list[Any] = Field(default_factory=list)
    adjacent_scene_handoffs: Any = Field(default_factory=list)
    scene_retry_feedback: list[Any] = Field(default_factory=list)
    plan_expression_records: list[Any] = Field(default_factory=list)

    @field_validator("current_scene", mode="before")
    @classmethod
    def _normalize_current_scene(cls, value: Any) -> dict[str, Any]:
        payload = dict(value) if isinstance(value, dict) else {}
        return {**_DRAFT_SCENE_DEFAULTS, **payload}


class CheckChapterPromptContext(ChapterFlowPromptContext):
    canon_context: dict[str, Any] = Field(default_factory=lambda: {"characters": {}})
    character_profiles: list[Any] = Field(default_factory=list)
    creative_contract: list[Any] = Field(default_factory=list)
    check_mode: str = "full"
    previous_report: Any | None = None
    changed_sections: list[Any] = Field(default_factory=list)
    change_ratio: float | None = None
    address_rules: str = ""
    world_context_rules: str = ""
    world_rule_card: dict[str, Any] = Field(default_factory=dict)
    known_prompt_markers: list[str] = Field(default_factory=list)
    local_detected_prompt_leaks: list[Any] = Field(default_factory=list)
    local_detected_resurrection_risks: list[Any] = Field(default_factory=list)
    local_detected_quality_issues: list[Any] = Field(default_factory=list)
    local_forbidden_element_candidates: list[Any] = Field(default_factory=list)
    local_expression_channel_hits: list[Any] = Field(default_factory=list)
    local_check_note: str = ""
    local_resurrection_note: str = ""
    local_quality_note: str = ""
    local_forbidden_note: str = ""
    local_expression_channel_note: str = ""

    @field_validator("canon_context", mode="before")
    @classmethod
    def _normalize_canon_context(cls, value: Any) -> dict[str, Any]:
        payload = dict(value) if isinstance(value, dict) else {}
        characters = payload.get("characters")
        payload["characters"] = characters if isinstance(characters, dict) else {}
        return payload


_RECHECK_POLICY_DEFAULTS: dict[str, Any] = {
    "scan_scope": "targeted",
    "solved_issue_rule": "",
    "unresolved_issue_rule": "",
    "regression_rule": "",
    "allow_new_issue_scan": False,
    "new_issue_severities": [],
}

_PRIOR_ISSUE_DEFAULTS: dict[str, Any] = {
    "issue_id": "",
    "severity": "medium",
    "issue_type": "unknown",
    "summary": "",
    "location": "",
    "repair_focus": "",
    "postconditions": [],
}

_POSTCONDITION_DEFAULTS: dict[str, Any] = {
    "validator_id": "",
    "description": "",
    "evidence_hint": "",
}

_CAUSAL_LINK_DEFAULTS: dict[str, Any] = {
    "previous_event": "",
    "causal_mechanism": "",
    "unresolved_question": "",
    "open_threads": [],
    "character_profiles": [],
    "memory_guidance": None,
}

_CHAPTER_BRIDGE_DEFAULTS: dict[str, Any] = {
    "bridge_summary": "",
    "emotional_carryover": "",
    "action_handoff": "",
    "opening_location": "",
    "opening_time": "",
}


def _normalize_mapping(value: Any, defaults: dict[str, Any]) -> dict[str, Any]:
    payload = dict(value) if isinstance(value, dict) else {}
    return {**defaults, **payload}


def _normalize_prior_issues(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    normalized: list[Any] = []
    for item in value:
        if not isinstance(item, dict):
            normalized.append(item)
            continue
        payload = {**_PRIOR_ISSUE_DEFAULTS, **item}
        postconditions = payload.get("postconditions")
        payload["postconditions"] = [
            _normalize_mapping(condition, _POSTCONDITION_DEFAULTS)
            for condition in (postconditions if isinstance(postconditions, list) else [])
        ]
        normalized.append(payload)
    return normalized


class ReviewRecheckPromptContext(ChapterFlowPromptContext):
    recheck_mode: bool = False
    prior_issues: list[Any] = Field(default_factory=list)
    must_resolve_summaries: list[str] = Field(default_factory=list)
    must_resolve_issue_ids: list[str] = Field(default_factory=list)
    repaired_issue_types: list[str] = Field(default_factory=list)
    patch_only_repair: bool = False
    recheck_policy: dict[str, Any] | None = None
    local_prescreen_issues: list[Any] = Field(default_factory=list)
    local_check_note: str = ""
    has_previous_chapter: bool = True
    numbered_chapter_text: str = ""

    @field_validator("prior_issues", mode="before")
    @classmethod
    def _normalize_prior_issue_items(cls, value: Any) -> list[Any]:
        return _normalize_prior_issues(value)

    @field_validator("recheck_policy", mode="before")
    @classmethod
    def _normalize_recheck_policy(cls, value: Any) -> dict[str, Any] | None:
        if value in (None, {}):
            return None
        return _normalize_mapping(value, _RECHECK_POLICY_DEFAULTS)


class ContinuityCheckPromptContext(ReviewRecheckPromptContext):
    chapter_state_packet: dict[str, Any] | None = None
    chapter_bridge: dict[str, Any] | None = None
    chapter_plan: dict[str, Any] | None = None
    boundary_window_policy: dict[str, Any] = Field(default_factory=dict)

    @field_validator("chapter_state_packet", "chapter_bridge", "chapter_plan", mode="before")
    @classmethod
    def _coerce_nested_dicts(cls, value: Any, info: ValidationInfo) -> dict[str, Any] | None:
        return _coerce_to_dict_or_none(value, field_name=info.field_name or "unknown")

    @field_validator("boundary_window_policy", mode="before")
    @classmethod
    def _normalize_boundary_policy(cls, value: Any) -> dict[str, Any]:
        if value in (None, {}):
            return {}
        return _normalize_mapping(
            value,
            {"previous_tail_label": "", "opening_window_label": ""},
        )


class CausalValidationPromptContext(ReviewRecheckPromptContext):
    causal_link: dict[str, Any] = Field(default_factory=lambda: dict(_CAUSAL_LINK_DEFAULTS))
    chapter_bridge: Any = Field(default_factory=lambda: dict(_CHAPTER_BRIDGE_DEFAULTS))
    character_notes: str = ""
    previous_chapter_ending: str = ""
    recheck_strategy: str = "targeted_with_global_guard"

    @field_validator("causal_link", mode="before")
    @classmethod
    def _normalize_causal_link(cls, value: Any) -> dict[str, Any]:
        return _normalize_mapping(value, _CAUSAL_LINK_DEFAULTS)

    @field_validator("chapter_bridge", mode="before")
    @classmethod
    def _normalize_chapter_bridge(cls, value: Any) -> dict[str, Any]:
        return _normalize_mapping(value, _CHAPTER_BRIDGE_DEFAULTS)


class RepairPostconditionPromptContext(PromptContextModel):
    description: str = ""


class RepairIssuePromptContext(PromptContextModel):
    issue_id: str = ""
    severity: str = "medium"
    summary: str = ""
    location: str = ""
    evidence: Any = ""
    fix_suggestion: str = ""
    postconditions: list[RepairPostconditionPromptContext] = Field(default_factory=list)


class RepairPromptContext(ChapterFlowPromptContext):
    structured_issues_block: str = ""
    typed_issues: dict[str, list[RepairIssuePromptContext]] = Field(default_factory=dict)
    numbered_chapter_text: str = ""
    previous_chapter_ending: str = ""
    character_whitelist_note: str = ""
    memory_guidance: PatchHistoryGuidanceContext | None = None
    guidance: PatchHistoryGuidanceContext | None = None
    causal_link: dict[str, Any] = Field(default_factory=dict)
    chapter_plan_scenes: list[Any] = Field(default_factory=list)
    previous_hook_description: str = ""
    expected_hook: dict[str, Any] | None = None
    expected_payoffs: list[Any] = Field(default_factory=list)
    forbidden_elements: list[Any] = Field(default_factory=list)
    forbidden_elements_soft: list[Any] = Field(default_factory=list)
    intentional_callbacks: list[Any] = Field(default_factory=list)
    escalation_note: str = ""

    @field_validator("causal_link", mode="before")
    @classmethod
    def _normalize_causal_link(cls, value: Any) -> dict[str, Any]:
        if value in (None, {}):
            return {}
        return _normalize_mapping(value, _CAUSAL_LINK_DEFAULTS)

    @field_validator("expected_hook", mode="before")
    @classmethod
    def _coerce_expected_hook(cls, value: Any) -> dict[str, Any] | None:
        return _coerce_to_dict_or_none(value, field_name="expected_hook")

    @field_validator("memory_guidance", "guidance", mode="before")
    @classmethod
    def _normalize_history_guidance(cls, value: Any) -> Any:
        return None if value in (None, {}) else value


class ReadingPowerEvaluationPromptContext(ChapterFlowPromptContext):
    chapter_type: str = "standard"
    previous_hook_description: str = ""
    expected_hook: dict[str, Any] = Field(
        default_factory=lambda: {
            "hook_type": "",
            "hook_strength": 0,
            "hook_description": "",
        }
    )
    expected_payoffs: list[Any] = Field(default_factory=list)
    hook_score_config: dict[str, Any] = Field(
        default_factory=lambda: {
            "hook_score_weak": 0,
            "hook_score_medium": 0,
            "hook_score_strong": 0,
            "payoff_cap": 0,
        }
    )
    narrative_phase_context: dict[str, Any] | None = None
    suspense_timeline_entries: list[Any] = Field(default_factory=list)
    suspense_schedule: list[Any] = Field(default_factory=list)
    main_plot_points: list[Any] = Field(default_factory=list)
    preferred_payoff_types: list[Any] = Field(default_factory=list)
    min_payoffs: int = 0
    min_unresolved_threads: int = 0
    consecutive_main_plot_stall: int = 0
    strict_review: bool = False
    independent_reviewer_note: str = ""
    revelation_budget: Any = None
    excerpt_strategy: str = ""

    @field_validator("expected_hook", mode="before")
    @classmethod
    def _normalize_expected_hook(cls, value: Any) -> dict[str, Any]:
        return _normalize_mapping(
            value,
            {"hook_type": "", "hook_strength": 0, "hook_description": ""},
        )

    @field_validator("hook_score_config", mode="before")
    @classmethod
    def _normalize_hook_score_config(cls, value: Any) -> dict[str, Any]:
        return _normalize_mapping(
            value,
            {
                "hook_score_weak": 0,
                "hook_score_medium": 0,
                "hook_score_strong": 0,
                "payoff_cap": 0,
            },
        )


def _as_prompt_mapping(value: Any, defaults: dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, dict):
        payload = dict(value)
    elif hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="python")
        payload = dict(dumped) if isinstance(dumped, dict) else {}
    elif hasattr(value, "__dict__"):
        payload = dict(vars(value))
    else:
        payload = {}
    return {**defaults, **payload}


def _coerce_to_dict_or_none(value: Any, *, field_name: str) -> dict[str, Any] | None:
    """Tighten Any-typed prompt-context fields to dict | None.

    Accept dicts and Pydantic-style models, normalize empty mappings to ``None``,
    and reject every other shape. Scalar/list values would otherwise mask producer
    contract drift (see the historical ``WavePromptContext.chapter_position: str``
    bug).
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return value or None
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="python")
        if isinstance(dumped, dict):
            return dumped or None
    raise ValueError(
        f"{field_name} must be a dict or pydantic model (got {type(value).__name__}); "
        "scalar/list types are not accepted - check the producer."
    )


_CHAPTER_OUTLINE_DEFAULTS: dict[str, Any] = {
    "chapter_number": 0,
    "title": "",
    "goal": "",
    "main_plot_points": [],
    "subplot_points": [],
    "beats_summary": [],
}

_CHAPTER_PLAN_DEFAULTS: dict[str, Any] = {
    "scene_intents": [],
    "required_state_transitions": [],
    "opening_contract": "",
    "closing_contract": "",
}


def _normalize_scene_items(value: Any) -> list[Any]:
    if not isinstance(value, (list, tuple)):
        return []
    return [
        _as_prompt_mapping(item, _DRAFT_SCENE_DEFAULTS)
        if isinstance(item, dict) or hasattr(item, "model_dump") or hasattr(item, "__dict__")
        else item
        for item in value
    ]


def _normalize_chapter_plan(value: Any) -> dict[str, Any]:
    payload = _as_prompt_mapping(value, _CHAPTER_PLAN_DEFAULTS)
    payload["scene_intents"] = _normalize_scene_items(payload.get("scene_intents"))
    return payload


class AlignmentPromptContext(ChapterFlowPromptContext):
    chapter_outline: Any
    chapter_plan: Any

    @field_validator("chapter_outline", mode="before")
    @classmethod
    def _normalize_outline(cls, value: Any) -> dict[str, Any]:
        return _as_prompt_mapping(value, _CHAPTER_OUTLINE_DEFAULTS)

    @field_validator("chapter_plan", mode="before")
    @classmethod
    def _normalize_plan(cls, value: Any) -> dict[str, Any]:
        return _normalize_chapter_plan(value)


class ScenePlanValidationPromptContext(ChapterFlowPromptContext):
    scene_plan: Any
    bridge_card: Any
    chapter_card: Any

    @field_validator("scene_plan", mode="before")
    @classmethod
    def _normalize_scene_plan(cls, value: Any) -> dict[str, Any]:
        return _normalize_chapter_plan(value)

    @field_validator("bridge_card", mode="before")
    @classmethod
    def _normalize_bridge(cls, value: Any) -> dict[str, Any]:
        payload = _as_prompt_mapping(value, _CHAPTER_BRIDGE_DEFAULTS)
        causal_link = payload.get("causal_link")
        payload["causal_link"] = (
            _as_prompt_mapping(causal_link, _CAUSAL_LINK_DEFAULTS) if causal_link else {}
        )
        return payload

    @field_validator("chapter_card", mode="before")
    @classmethod
    def _normalize_chapter_card(cls, value: Any) -> dict[str, Any]:
        return _as_prompt_mapping(
            value,
            {"chapter_number": 0, "title": "", "goal": "", "target_word_count": 0},
        )


class TextRevisionPromptContext(ChapterFlowPromptContext):
    draft_text: str
    iteration: int = 1
    chapter_quality_repair_tickets: list[Any] = Field(default_factory=list)
    chapter_repair_report: dict[str, Any] = Field(
        default_factory=lambda: {
            "factual_errors": [],
            "expression_errors": [],
            "repair_actions": [],
        }
    )
    scene_stitch_report: Any | None = None
    word_count_restructure: Any | None = None

    @field_validator("chapter_repair_report", mode="before")
    @classmethod
    def _normalize_chapter_repair_report(cls, value: Any) -> dict[str, Any]:
        return _as_prompt_mapping(
            value,
            {
                "factual_errors": [],
                "expression_errors": [],
                "repair_actions": [],
            },
        )


class WavePromptContext(ChapterFlowPromptContext):
    draft_text: str
    draft_plan_coverage: Any | None = None
    scene_stitch_report: Any | None = None


class EditorialCheckPromptContext(ChapterFlowPromptContext):
    editorial_contract: dict[str, Any]
    chapter_contract: dict[str, Any] = Field(default_factory=dict)
    forbidden_reveal_boundaries: list[Any] = Field(default_factory=list)
    local_findings: list[Any] = Field(default_factory=list)
    local_metrics: dict[str, Any] = Field(default_factory=dict)
    editorial_risk_guidance: dict[str, Any] = Field(default_factory=dict)

    @field_validator("editorial_contract", mode="before")
    @classmethod
    def _normalize_editorial_contract(cls, value: Any) -> dict[str, Any]:
        return _as_prompt_mapping(value, {})


class CriticCausalPromptContext(ChapterFlowPromptContext):
    chapter_outline_goal: str = ""
    previous_chapter_ending: str = ""
    previous_exit_state: dict[str, Any] = Field(
        default_factory=lambda: {"location": "", "active_goals": [], "open_questions": []}
    )

    @field_validator("previous_exit_state", mode="before")
    @classmethod
    def _normalize_previous_exit(cls, value: Any) -> dict[str, Any]:
        return _as_prompt_mapping(
            value,
            {"location": "", "active_goals": [], "open_questions": []},
        )


_CHARACTER_STATE_DEFAULTS: dict[str, Any] = {
    "alive": None,
    "location": "",
    "emotional_state": "",
    "inventory": [],
    "notes": "",
}


class CriticCharacterPromptContext(ChapterFlowPromptContext):
    character_batch: list[Any] = Field(default_factory=list)
    character_name: str = ""
    canon_state: dict[str, Any] = Field(default_factory=lambda: dict(_CHARACTER_STATE_DEFAULTS))
    historical_context: str = ""

    @field_validator("canon_state", mode="before")
    @classmethod
    def _normalize_canon_state(cls, value: Any) -> dict[str, Any]:
        return _as_prompt_mapping(value, _CHARACTER_STATE_DEFAULTS)

    @field_validator("character_batch", mode="before")
    @classmethod
    def _normalize_character_batch(cls, value: Any) -> list[Any]:
        if not isinstance(value, list):
            return []
        normalized: list[Any] = []
        for item in value:
            payload = _as_prompt_mapping(
                item,
                {"character_name": "", "canon_state": {}, "historical_context": ""},
            )
            payload["canon_state"] = _as_prompt_mapping(
                payload.get("canon_state"), _CHARACTER_STATE_DEFAULTS
            )
            normalized.append(payload)
        return normalized


class CriticContinuityPromptContext(ChapterFlowPromptContext):
    current_chapter: str = ""
    previous_chapter: str = ""
    previous_chapter_ending: str = ""
    canon_state_summary: str = ""


class CriticStrengthsPromptContext(ChapterFlowPromptContext):
    chapter_outline: dict[str, Any] = Field(
        default_factory=lambda: {"goal": "", "main_plot_points": []}
    )

    @field_validator("chapter_outline", mode="before")
    @classmethod
    def _normalize_strength_outline(cls, value: Any) -> dict[str, Any]:
        return _as_prompt_mapping(value, {"goal": "", "main_plot_points": []})


class EvaluateCoreCharacterPromptContext(PromptContextModel):
    name: str = ""


class EvaluateAnchorElementsPromptContext(PromptContextModel):
    time_frame: str = ""
    primary_locations: list[str] = Field(default_factory=list)
    core_characters: list[EvaluateCoreCharacterPromptContext] = Field(default_factory=list)
    central_event: str = ""


class EvaluateBlueprintPromptContext(PromptContextModel):
    synopsis: str = ""
    emotional_arc: str = ""
    ending_strategy: str = ""
    anchor_elements: EvaluateAnchorElementsPromptContext = Field(
        default_factory=EvaluateAnchorElementsPromptContext
    )


class EvaluateBeatPromptContext(PromptContextModel):
    sequence: int = 0
    beat_type: str = "scene"
    summary: str = ""
    purpose: str = ""
    required_outcome: str = ""
    exit_target_state: str = ""


class EvaluateCausalLinkPromptContext(PromptContextModel):
    previous_event: str = ""
    causal_mechanism: str = ""
    unresolved_question: str = ""
    open_threads: list[str] = Field(default_factory=list)


class EvaluateExecutionPlanPromptContext(PromptContextModel):
    opening_contract: str = ""
    ending_contract: str = ""
    beat_execution_plan: list[EvaluateBeatPromptContext] = Field(default_factory=list)
    cross_scene_intent: dict[str, Any] = Field(default_factory=dict)


class EvaluateDraftPromptContext(ChapterFlowPromptContext):
    draft_text: str
    anchor: dict[str, Any] | None = None
    beats: list[EvaluateBeatPromptContext] = Field(default_factory=list)
    blueprint: EvaluateBlueprintPromptContext | None = None
    causal_link: EvaluateCausalLinkPromptContext | None = None
    execution_plan: EvaluateExecutionPlanPromptContext | None = None
    rewrite_notes: str = ""
    theme: str = ""
    word_count_gate_enabled: bool = False

    @field_validator("anchor", mode="before")
    @classmethod
    def _normalize_eval_anchor(
        cls,
        value: Any,
        info: ValidationInfo,
    ) -> dict[str, Any] | None:
        if value in (None, {}):
            return None
        if not isinstance(value, dict):
            raise ValueError(f"{info.field_name or 'field'} must be a canonical mapping DTO")
        return value

    @field_validator("blueprint", "causal_link", "execution_plan", mode="before")
    @classmethod
    def _empty_eval_card_is_absent(cls, value: Any) -> Any:
        return None if value in (None, {}) else value


class MotifCategoryStatsPromptContext(PromptContextModel):
    count: int = Field(default=0, ge=0)
    percentage: int = Field(default=0, ge=0, le=100)


class MotifExtractionPromptContext(ChapterFlowPromptContext):
    text: str
    element_focus: list[Any] = Field(default_factory=list)
    existing_motifs: list[Any] = Field(default_factory=list)
    motif_category_distribution: dict[str, MotifCategoryStatsPromptContext] = Field(
        default_factory=dict
    )
    motif_extraction_context: dict[str, Any] = Field(default_factory=dict)


class GuardConstraintPromptContext(ChapterFlowPromptContext):
    constraint: str


class HumanizeRewritePromptContext(ChapterFlowPromptContext):
    paragraphs: list[Any]
    pattern_hits: list[Any]
    humanize_context: dict[str, Any] = Field(
        default_factory=lambda: {"world_rule_preservation": [], "cognitive_constraints": []}
    )

    @field_validator("humanize_context", mode="before")
    @classmethod
    def _normalize_humanize_context(cls, value: Any) -> dict[str, Any]:
        return _as_prompt_mapping(
            value,
            {"world_rule_preservation": [], "cognitive_constraints": []},
        )


class MacroGuardPromptContext(ChapterFlowPromptContext):
    chapters_audited: list[int] = Field(default_factory=list)
    audit_entries: list[Any] = Field(default_factory=list)
    outline: dict[str, Any] = Field(default_factory=dict)
    canon_stats: dict[str, Any] = Field(default_factory=dict)


class ContinuityRepairPromptContext(ChapterFlowPromptContext):
    continuity_report: dict[str, Any] = Field(default_factory=lambda: {"issues": []})
    repair_plan: dict[str, Any] = Field(
        default_factory=lambda: {"no_op": True, "must_keep": [], "must_change": []}
    )
    structured_issues_block: str = ""

    @field_validator("continuity_report", mode="before")
    @classmethod
    def _normalize_continuity_report(cls, value: Any) -> dict[str, Any]:
        return _as_prompt_mapping(value, {"issues": []})

    @field_validator("repair_plan", mode="before")
    @classmethod
    def _normalize_repair_plan(cls, value: Any) -> dict[str, Any]:
        if value in (None, {}):
            return {"no_op": True, "must_keep": [], "must_change": []}
        return _as_prompt_mapping(
            value,
            {"no_op": False, "must_keep": [], "must_change": []},
        )


class KnowledgeBoundaryRepairPromptContext(ChapterFlowPromptContext):
    cognitive_constraints: list[Any] = Field(default_factory=list)
    repair_goal: str = ""
    violations: list[Any] = Field(default_factory=list)
    window_text: str = ""


class CompressionPromptContext(ChapterFlowPromptContext):
    blocks: list[Any]
    context_facts: list[Any] = Field(default_factory=list)
    priority_facts: list[Any] = Field(default_factory=list)
    mode: str = "standard"


class CompressionVerificationPromptContext(ChapterFlowPromptContext):
    original: str
    compressed: str
    required_facts: list[Any] = Field(default_factory=list)


class PlotGuardPromptContext(ChapterFlowPromptContext):
    plot_guard_mode: str = "standard"
    next_chapter_number: int = 0
    milestone_window: dict[str, Any] = Field(default_factory=dict)
    outline_window: list[Any] = Field(default_factory=list)
    plot_deviations: list[Any] = Field(default_factory=list)
    new_characters: list[Any] = Field(default_factory=list)
    new_locations: list[Any] = Field(default_factory=list)
    new_key_items: list[Any] = Field(default_factory=list)
    canon_stats: dict[str, Any] = Field(
        default_factory=lambda: {
            "character_count": 0,
            "foreshadowing_count": 0,
            "world_facts_count": 0,
        }
    )
    eval_report: dict[str, Any] = Field(default_factory=dict)
    alignment_report: dict[str, Any] = Field(default_factory=dict)
    continuity_report: dict[str, Any] = Field(default_factory=dict)
    causal_report: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "eval_report", "alignment_report", "continuity_report", "causal_report",
        mode="before",
    )
    @classmethod
    def _normalize_report_fields(cls, value: Any) -> dict[str, Any]:
        return _as_prompt_mapping(value, {}) if value is not None else {}

    @field_validator("canon_stats", mode="before")
    @classmethod
    def _normalize_canon_stats(cls, value: Any) -> dict[str, Any]:
        return _as_prompt_mapping(
            value,
            {"character_count": 0, "foreshadowing_count": 0, "world_facts_count": 0},
        )


class ElementProgressPromptContext(ChapterFlowPromptContext):
    element: dict[str, Any]
    rule_eval: dict[str, Any]
    context: dict[str, Any]

    @field_validator("element", mode="before")
    @classmethod
    def _normalize_element(cls, value: Any) -> dict[str, Any]:
        return _as_prompt_mapping(
            value,
            {"element_id": "", "name": "", "category": "", "prompt_hint": "", "description": ""},
        )

    @field_validator("rule_eval", mode="before")
    @classmethod
    def _normalize_rule_eval(cls, value: Any) -> dict[str, Any]:
        return _as_prompt_mapping(value, {"status": "", "score": 0, "evidence": ""})

    @field_validator("context", mode="before")
    @classmethod
    def _normalize_element_context(cls, value: Any) -> dict[str, Any]:
        return _as_prompt_mapping(
            value,
            {"plan_excerpt": "", "chapter_excerpt": "", "quality_excerpt": ""},
        )


class RepairStrategyPromptContext(ChapterFlowPromptContext):
    dimension: str = ""
    round_number: int = 0
    current_score: float = 0.0
    previous_score: float | None = None
    issues: list[Any] = Field(default_factory=list)
    must_fix_issues: list[Any] = Field(default_factory=list)
    issue_attempts: dict[str, Any] = Field(default_factory=dict)
    rollback_history: list[Any] = Field(default_factory=list)
    memory_guidance: dict[str, Any] = Field(default_factory=dict)
    repeated_issue_guard: dict[str, Any] = Field(default_factory=dict)


class RepairSemanticVerifyPromptContext(ChapterFlowPromptContext):
    issue_description: str
    issue_evidence: str = ""
    repair_action: str = ""
    repaired_text: str


class StateExtractionPromptContext(ChapterFlowPromptContext):
    chapter_contract: Any = Field(default_factory=dict)
    contract_targets: Any = Field(default_factory=list)
    current_state: Any = Field(default_factory=dict)
    entity_registry: Any = Field(default_factory=dict)
    candidate_limit: int = 6
    evidence_limit: int = 2


class StateDeltaAdjudicationPromptContext(ChapterFlowPromptContext):
    candidate: Any = Field(default_factory=dict)
    chapter_contract: Any = Field(default_factory=dict)
    contract_targets: Any = Field(default_factory=list)
    current_state: Any = Field(default_factory=dict)
    evidence_window: str = ""


class FinalStateAdjudicationPromptContext(ChapterFlowPromptContext):
    candidates: Any = Field(default_factory=list)
    decisions: Any = Field(default_factory=list)
    chapter_contract: Any = Field(default_factory=dict)
    contract_targets: Any = Field(default_factory=list)
    current_state: Any = Field(default_factory=dict)
    contract_coverage_report: Any = Field(default_factory=dict)
    pre_block_recheck: Any = Field(default_factory=dict)


_BASE_CHAPTER_FLOW_TASKS = {
    TaskType.CONTEXT_COMPRESS,
    TaskType.ADAPTIVE_COMPRESS,
    TaskType.VERIFY_COMPRESSION,
    TaskType.PLOT_GUARD_JUDGE,
    TaskType.BRIDGE_CHAPTER,
    TaskType.PLAN_CHAPTER,
    TaskType.PLAN_CHAPTER_SCENES,
    TaskType.VALIDATE_SCENE_PLAN,
    TaskType.DRAFT_CHAPTER,
    TaskType.WAVE_CHAPTER,
    TaskType.EDIT_CHAPTER,
    TaskType.CHECK_ALIGNMENT,
    TaskType.ELEMENT_PROGRESS_ARBITER,
    TaskType.REPAIR_CONTINUITY,
    TaskType.CHECK_EDITORIAL,
    TaskType.CRITIC_STRENGTHS,
    TaskType.CRITIC_CHARACTER,
    TaskType.CRITIC_CONTINUITY,
    TaskType.CRITIC_CAUSAL,
    TaskType.POLISH_CHAPTER,
    TaskType.HUMANIZE_SCAN,
    TaskType.HUMANIZE_PARAGRAPH_REWRITE,
    TaskType.GUARD_CONSTRAINT_CHECK,
    TaskType.KNOWLEDGE_BOUNDARY_AUDIT,
    TaskType.REPAIR_KNOWLEDGE_BOUNDARY,
    TaskType.REPAIR_STRATEGY_DIAGNOSE,
    TaskType.REPAIR_SEMANTIC_VERIFY,
    TaskType.EXTRACT_CANON,
    TaskType.EXTRACT_CANON_DELTA,
    TaskType.EXTRACT_CHAPTER_SUMMARY_EXIT,
    TaskType.EXTRACT_CREATIVE_REPORT,
    TaskType.EXTRACT_CHARACTER_STATE_DELTAS,
    TaskType.EXTRACT_RELATIONSHIP_DELTAS,
    TaskType.EXTRACT_PLOT_THREAD_DELTAS,
    TaskType.EXTRACT_EXPRESSION_OBSERVATIONS,
    TaskType.EXTRACT_CANDIDATE_STATE_DELTAS,
    TaskType.ADJUDICATE_STATE_DELTA,
    TaskType.ADJUDICATE_FINAL_STATE,
    TaskType.EVALUATE,
    TaskType.EXTRACT_MOTIFS,
    TaskType.SUMMARIZE_CHAPTER,
    TaskType.MACRO_GUARD_AUDIT,
}

CHAPTER_FLOW_PROMPT_CONTEXT_MODELS: dict[TaskType, type[PromptContextModel]] = {
    **{task: ChapterFlowPromptContext for task in _BASE_CHAPTER_FLOW_TASKS},
    TaskType.CONTEXT_COMPRESS: CompressionPromptContext,
    TaskType.ADAPTIVE_COMPRESS: CompressionPromptContext,
    TaskType.VERIFY_COMPRESSION: CompressionVerificationPromptContext,
    TaskType.PLOT_GUARD_JUDGE: PlotGuardPromptContext,
    TaskType.VALIDATE_SCENE_PLAN: ScenePlanValidationPromptContext,
    TaskType.DRAFT_SCENE: DraftScenePromptContext,
    TaskType.WAVE_CHAPTER: WavePromptContext,
    TaskType.EDIT_CHAPTER: TextRevisionPromptContext,
    TaskType.EVALUATE: EvaluateDraftPromptContext,
    TaskType.CHECK_ALIGNMENT: AlignmentPromptContext,
    TaskType.ELEMENT_PROGRESS_ARBITER: ElementProgressPromptContext,
    TaskType.CHECK_CHAPTER: CheckChapterPromptContext,
    TaskType.CHECK_CONTINUITY: ContinuityCheckPromptContext,
    TaskType.VALIDATE_CAUSAL: CausalValidationPromptContext,
    TaskType.REPAIR_CONTINUITY: ContinuityRepairPromptContext,
    TaskType.REPAIR_CAUSAL: RepairPromptContext,
    TaskType.PATCH_CHAPTER: PatchChapterContext,
    TaskType.CHECK_EDITORIAL: EditorialCheckPromptContext,
    TaskType.CRITIC_CAUSAL: CriticCausalPromptContext,
    TaskType.CRITIC_CHARACTER: CriticCharacterPromptContext,
    TaskType.CRITIC_CONTINUITY: CriticContinuityPromptContext,
    TaskType.CRITIC_STRENGTHS: CriticStrengthsPromptContext,
    TaskType.EVALUATE_READING_POWER: ReadingPowerEvaluationPromptContext,
    TaskType.REPAIR_READING_POWER: RepairPromptContext,
    TaskType.EXTRACT_MOTIFS: MotifExtractionPromptContext,
    TaskType.GUARD_CONSTRAINT_CHECK: GuardConstraintPromptContext,
    TaskType.MACRO_GUARD_AUDIT: MacroGuardPromptContext,
    TaskType.HUMANIZE_PARAGRAPH_REWRITE: HumanizeRewritePromptContext,
    TaskType.REPAIR_KNOWLEDGE_BOUNDARY: KnowledgeBoundaryRepairPromptContext,
    TaskType.REPAIR_STRATEGY_DIAGNOSE: RepairStrategyPromptContext,
    TaskType.REPAIR_SEMANTIC_VERIFY: RepairSemanticVerifyPromptContext,
    TaskType.EXTRACT_CANDIDATE_STATE_DELTAS: StateExtractionPromptContext,
    TaskType.ADJUDICATE_STATE_DELTA: StateDeltaAdjudicationPromptContext,
    TaskType.ADJUDICATE_FINAL_STATE: FinalStateAdjudicationPromptContext,
}

CHAPTER_FLOW_PROMPT_TASKS = frozenset(CHAPTER_FLOW_PROMPT_CONTEXT_MODELS)


def normalize_chapter_flow_prompt_context(
    task_type: TaskType,
    context: dict[str, Any],
    *,
    source: str = "chapter_flow",
) -> dict[str, Any]:
    """Validate one chapter-flow context and materialize declared optional fields."""

    model_type = CHAPTER_FLOW_PROMPT_CONTEXT_MODELS.get(task_type)
    if model_type is None:
        return dict(context)
    validated = validate_prompt_context(
        model_type,
        context,
        task_type=task_type,
        source=source,
    )
    declared_fields = set(model_type.model_fields)
    declared_payload = validated.model_dump(mode="python", include=declared_fields)
    return {**context, **declared_payload}


__all__ = [
    "CHAPTER_FLOW_PROMPT_CONTEXT_MODELS",
    "CHAPTER_FLOW_PROMPT_TASKS",
    "ChapterFlowPromptContext",
    "ChapterPositionPromptContext",
    "normalize_chapter_flow_prompt_context",
]
