"""Continuity and chapter-planning schemas for Novel Forge v2."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from novel_forge.core.guidance import GuidanceRequirement, LiteralRequirement
from novel_forge.core.schemas.audit import AuditPostcondition, RepairSurface
from novel_forge.core.schemas.base import VersionedSchema
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.core.schemas.review import RepairTicket, RepairVerificationResult, ReviewFinding
from novel_forge.core.schemas.story_state import (
    CarryForwardItem,
    ChapterExitState,
    PlotThreadState,
    RelationshipState,
)
from novel_forge.core.schemas.world_rules import WorldRuleApplication
from novel_forge.core.utils.type_coerce import coerce_text_list, stringify_text_value


def _default_cross_scene_intent() -> dict[str, Any]:
    return {"cross_scene_references": [], "pacing_curve": []}


def _normalize_issue_severity(value: Any) -> str:
    text = stringify_text_value(value).strip().lower()
    aliases = {
        "critical": "critical",
        "fatal": "critical",
        "blocker": "critical",
        "p0": "critical",
        "high": "high",
        "major": "high",
        "severe": "high",
        "p1": "high",
        "medium": "medium",
        "moderate": "medium",
        "mid": "medium",
        "p2": "medium",
        "low": "low",
        "minor": "low",
        "info": "low",
        "p3": "low",
        "严重": "high",
        "高": "high",
        "中": "medium",
        "中等": "medium",
        "轻微": "low",
        "低": "low",
    }
    return aliases.get(text, "medium")


class CharacterMotivationHint(VersionedSchema):
    """Character motivation hint for scene intent."""

    character: str
    motivation: str = Field(default="")  # What they want in this scene
    stake: str = Field(default="")  # What they stand to lose/gain


class PovKnowledgeConstraints(VersionedSchema):
    """Derived POV knowledge constraints for a scene — computed from knowledge_ledger."""

    forbidden_knowledge: list[str] = Field(
        default_factory=list,
        description="POV 角色不应知道的事实类别描述（用于提示词约束，不含事实原文）。",
    )
    sensory_limits: list[str] = Field(
        default_factory=list,
        description="POV 角色的感知限制（如'无法看到密室内部'）。",
    )
    scope_label: str = Field(
        default="limited",
        description="POV 范围标签：limited / omniscient / objective。",
    )


class SceneIntent(VersionedSchema):
    """Structured scene-level writing intent."""

    scene_id: str
    summary: str = Field(
        description="本场戏的戏剧性概述——描述角色面临的情境和必须采取的行动过程，不提前揭示行动的结果。"
    )
    purpose: str = Field(
        default="",
        description="本场戏的叙事功能——用角色动作、对白选择、阻力对抗或感官发现来表达，不用旁白解释。",
    )
    conflict: str = Field(
        default="",
        description="本场戏的核心阻力——谁或什么在阻止角色达成目标，阻力如何在场景中具体表现。",
    )
    required_characters: list[str] = Field(default_factory=list)
    character_motivations: list[CharacterMotivationHint] = Field(default_factory=list)
    entry_state_refs: list[str] = Field(default_factory=list)
    required_outcome: str = Field(
        default="",
        description="本场戏结束时必须达成的可观察状态变化——用角色的行动后果、关系位移或信息发现过程来描述，不用旁白总结结果。",
    )
    dramatic_question: str = Field(
        default="",
        description="本场戏向读者提出的悬念问题——用疑问形式表达，如'角色能否在不暴露自己的情况下化解危机？'，不用答案形式。",
    )
    exit_target_state: str = Field(
        default="",
        description="本场戏交给下一场的局面——可承接的动作、情绪或信息状态。",
    )
    location: str = Field(default="")
    time_marker: str = Field(default="")
    relationship_dynamics: str = Field(
        default="", description="本场戏中角色间关系互动的要求与变化方向"
    )
    emotional_beat: str = Field(default="", description="本场戏的情绪节拍，从桥接情绪弧细化而来")
    sensory_notes: str = Field(default="", description="本场戏的感官锚点提示（气味/声音/触感等）")
    sensory_focus: str = Field(
        default="",
        description="本场应侧重的感官通道（如'嗅觉+触觉'），用于避免跨场景感官通道重复。",
    )
    dialogue_subtext: str = Field(
        default="",
        description="本场对白潜台词目标——角色在对话中真正想表达但未明说的意图，通过语气、停顿、回避或动作暗示呈现。",
    )
    choice_pressure: str = Field(default="", description="本场迫使角色做出的选择或代价。")
    scene_resistance: str = Field(default="", description="空间/流程/人群/物件等具体场景阻力。")
    dialogue_voice_targets: dict[str, str] = Field(
        default_factory=dict,
        description="角色对白声纹执行提示。",
    )
    revelation_level: str = Field(default="", description="本场信息揭示层级。")
    symbol_usage_policy: str = Field(default="", description="本场象征物使用与解释边界。")
    body_signal_budget: int = Field(default=1, ge=0, le=5, description="本场身体信号预算。")
    target_words: int = Field(default=0, ge=0, description="本场戏的目标字数，由规划阶段分配")
    pov_character: str = Field(default="", description="本场戏采用的 POV 角色。")
    pov_scope: str = Field(
        default="limited",
        description="POV 可见性范围：limited / omniscient / objective。",
    )
    pov_switch_allowed: bool = Field(default=False, description="本场戏是否允许切换 POV。")
    pov_switch_marker_required: bool = Field(
        default=True,
        description="若发生 POV 切换，是否必须使用显式分隔符并在首句点名。",
    )
    pov_knowledge_constraints: PovKnowledgeConstraints = Field(
        default_factory=PovKnowledgeConstraints,
        description="POV 角色的知识边界约束——由规划阶段从 knowledge_ledger 推导。",
    )
    world_rule_ids: list[str] = Field(
        default_factory=list,
        description="本场必须遵守或兑现的 world_rule_book rule_id 列表。",
    )
    world_rule_usage: str = Field(
        default="",
        description="本场如何把规则前提、限制或代价转译为行动。",
    )
    world_rule_evidence_expectations: list[str] = Field(
        default_factory=list,
        description="正文中应可观察到的规则遵守证据或代价。",
    )
    world_rule_forbidden_boundaries: list[str] = Field(
        default_factory=list,
        description="本场不得突破的规则边界。",
    )
    scene_goal: str = Field(default="", description="场景级写作模式下，本场唯一主要叙事目标。")
    owned_events: list[str] = Field(
        default_factory=list,
        description=(
            "本场独占负责的 P0 事件验收清单：每项只写一个可观察事件，"
            "包含触发/行动/结果中的必要证据；禁止用分号把多个事件合并在一项。"
        ),
    )
    owned_revelations: list[str] = Field(
        default_factory=list,
        description="本场独占负责的信息揭示；每项只对应一个读者可确认的新信息。",
    )
    owned_state_changes: list[str] = Field(
        default_factory=list,
        description="本场独占负责的状态变化；每项只对应一个可观察的前后状态差异。",
    )
    forbidden_overlap: list[str] = Field(
        default_factory=list,
        description="本场不得提前触碰或重复兑现的相邻场景事实。",
    )
    handoff_to_next: str = Field(default="", description="本场交给后续场景的动作、情绪或信息接力。")
    dependency_scene_ids: list[str] = Field(
        default_factory=list,
        description="本场草稿必须等待其完成的前置场景 ID。",
    )
    parallel_group: str = Field(
        default="",
        description="LLM 规划建议的可并行组；最终以验证器报告为准。",
    )
    draft_order: int = Field(default=0, ge=0, description="本场在全章中的拼接顺序。")
    entry_state: str = Field(default="", description="本场开始时必须继承的局面状态。")
    exit_state: str = Field(default="", description="本场结束时必须交出的局面状态。")

    @field_validator(
        "summary",
        "purpose",
        "conflict",
        "required_outcome",
        "dramatic_question",
        "exit_target_state",
        "location",
        "time_marker",
        "relationship_dynamics",
        "emotional_beat",
        "sensory_notes",
        "sensory_focus",
        "dialogue_subtext",
        "choice_pressure",
        "scene_resistance",
        "revelation_level",
        "symbol_usage_policy",
        "scene_goal",
        "handoff_to_next",
        "entry_state",
        "exit_state",
        "world_rule_usage",
        mode="before",
    )
    @classmethod
    def _coerce_scene_intent_string_fields(cls, v: Any) -> str:
        return stringify_text_value(v)

    @field_validator(
        "world_rule_ids",
        "world_rule_evidence_expectations",
        "world_rule_forbidden_boundaries",
        mode="before",
    )
    @classmethod
    def _coerce_world_rule_lists(cls, value: Any) -> list[str]:
        return coerce_text_list(value)


class RelationshipBeat(VersionedSchema):
    """Relationship dynamic snapshot for chapter bridge."""

    current_trust_level: str = Field(default="试探")
    unspoken_tension: str = Field(default="")
    power_dynamic: str = Field(default="")


class CausalLink(VersionedSchema):
    """Causal handoff from previous chapter into the current opening."""

    previous_event: str = Field(default="")
    causal_mechanism: str = Field(default="")
    unresolved_question: str = Field(default="")
    open_threads: list[str] = Field(default_factory=list)


class ChapterBridge(VersionedSchema):
    """Bridge contract from previous chapter exit to current opening."""

    from_chapter: int = Field(default=0, ge=0)
    to_chapter: int = Field(ge=1)
    opening_time: str = Field(default="")
    opening_location: str = Field(default="")
    opening_pov: str = Field(default="")
    transition_mode: str = Field(default="")
    emotional_carryover: str = Field(default="")
    action_handoff: str = Field(default="")
    causal_link: CausalLink | None = None
    pending_questions: list[str] = Field(default_factory=list)
    forbidden_repetition: list[str] = Field(default_factory=list)
    bridge_summary: str = Field(default="")
    relationship_beat: RelationshipBeat | None = None
    sensory_anchors: list[str] = Field(default_factory=list)
    opening_acceptance_criteria: list[str] = Field(
        default_factory=list,
        description="开场窗口必须满足的衔接验收条件，由 Bridge 生成，供 Plan/Draft/Edit/Review 使用。",
    )

    @field_validator(
        "opening_time",
        "opening_location",
        "opening_pov",
        "transition_mode",
        "emotional_carryover",
        "action_handoff",
        "bridge_summary",
        mode="before",
    )
    @classmethod
    def _coerce_bridge_text_fields(cls, value: Any) -> str:
        """Keep ChapterBridge text fields scalar even when an LLM nests details."""
        return stringify_text_value(value)


ContinuityIssueSource = Literal["llm", "local", "merged", "postcondition", "manual"]
ContinuityIssueStatus = Literal[
    "open",
    "repairing",
    "resolved",
    "suppressed",
    "artifact_fixed",
    "deferred",
]


class ContinuityAnchor(VersionedSchema):
    """Semantic anchor that a continuity issue expects the repair to preserve or land."""

    source: str = Field(
        default="", description="Where the anchor came from, e.g. bridge.action_handoff"
    )
    text: str = Field(default="", description="Source text or compressed semantic anchor")
    role: str = Field(default="", description="Narrative function, e.g. emotional_handoff")
    importance: str = Field(default="required", description="required / supporting / optional")


class RepairPostcondition(AuditPostcondition):
    """Backward-compatible alias for the canonical audit postcondition."""


class ContinuityRepairDirective(VersionedSchema):
    """Actionable repair contract emitted by continuity audit for one issue.

    This is deliberately stored with the issue ledger instead of being rebuilt only
    inside the repairer: the audit step owns the diagnosis, the repair step owns the
    prose rewrite.
    """

    target_window: str = Field(default="", description="Human-readable target span to rewrite.")
    repair_strategy: str = Field(default="", description="Concrete repair strategy for the writer.")
    recommended_rewrite: str = Field(
        default="",
        description="Audit-proposed prose draft or paragraph-level rewrite brief.",
    )
    required_context: list[str] = Field(
        default_factory=list,
        description="Context the repairer must read before changing this issue.",
    )
    required_anchors: list[str] = Field(
        default_factory=list,
        description="Semantic anchors that must be landed in the replacement text.",
    )
    validation_focus: list[str] = Field(
        default_factory=list,
        description="Checks the re-audit should apply after repair.",
    )
    conflict_policy: str = Field(
        default="",
        description="How to order or merge this repair when windows overlap.",
    )
    repair_order: str = Field(
        default="normal",
        description="normal / late_if_conflict / final_boundary_pass.",
    )


class PlanLiteralContract(BaseModel):
    """Planning-model declaration that wording, not only meaning, is rigid."""

    model_config = ConfigDict(extra="forbid")

    contract_id: str = Field(
        min_length=1,
        description="Stable identifier chosen by the Planning model.",
    )
    literal: str = Field(
        min_length=1,
        description="Exact text that downstream prose must contain.",
    )
    scene_id: str = Field(
        min_length=1,
        description="Owning scene selected by the Planning model.",
    )
    reason: str = Field(
        min_length=1,
        description="Why semantic paraphrase would change the intended narrative function.",
    )
    placement_hint: str = Field(
        min_length=1,
        description="Semantic placement guidance consumed by the repair LLM.",
    )
    requirement: LiteralRequirement = Field(default_factory=LiteralRequirement)


class ChapterPlan(VersionedSchema):
    """Structured chapter plan used by draft and alignment stages.

    This is the **single authoritative constraint source** for the Draft step.
    It integrates and refines constraints from the narrative blueprint (coarse),
    chapter bridge (transition), and chapter outline (structural) into a
    scene-level execution plan.
    """

    guidance_requirements: list[GuidanceRequirement] = Field(default_factory=list)
    scene_intents: list[SceneIntent] = Field(default_factory=list)
    world_rule_applications: list[WorldRuleApplication] = Field(
        default_factory=list,
        description="章节对 world_rule_card 中规则的可执行场景绑定与验收记录。",
    )
    chapter_type: str = Field(
        default="crisis",
        description=(
            "章节功能类型，由规划阶段 LLM 标注，供写作模板按类型调整规范。"
            "crisis（危机/冲突章）/ discovery（探索/觉醒章）/ "
            "emotional（情感/关系章）/ transition（过渡/铺垫章）"
        ),
    )
    opening_contract: str = Field(default="")
    closing_contract: str = Field(default="")
    required_state_transitions: list[str] = Field(default_factory=list)
    required_literals: list[PlanLiteralContract] = Field(
        default_factory=list,
        description=(
            "Only literals explicitly adjudicated by the Planning model as wording-rigid. "
            "Ordinary quoted dialogue remains semantic and must not be promoted locally."
        ),
    )
    emotional_arc: str = Field(default="")
    foreshadowing_plan: list[str] = Field(default_factory=list)
    key_revelations: list[str] = Field(default_factory=list)
    relationship_evolution: list[str] = Field(
        default_factory=list,
        description="本章角色关系的整体演进计划，从蓝图和桥接中细化",
    )
    forbidden_elements: list[str] = Field(
        default_factory=list,
        description="本章必须回避的意象/句式/感官通道，继承自桥接和全书累积禁用",
    )
    forbidden_elements_soft: list[str] = Field(
        default_factory=list,
        description="跨章节软约束提示，建议尽量避开但不作为硬性禁用。",
    )
    forbidden_elements_quota: list[str] = Field(
        default_factory=list,
        description=(
            "有限额复用元素，允许在本章作为锚点或回环少量出现；不得作为硬禁用元素触发自动替换。"
        ),
    )
    forbidden_element_sources: list[dict[str, Any]] = Field(
        default_factory=list,
        description="禁用元素来源追踪：记录 text/source/original_level/level/reason/confidence。",
    )
    expression_channel_records: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "表达通道来源追踪：记录 text/channel/source/level/cooldown/actor_scope，"
            "可包含 surface_forms/replacement_axes/allowed_when。"
        ),
    )
    intentional_callbacks: list[str] = Field(
        default_factory=list,
        description="有意回环意象列表——作者刻意复用以实现母题呼应的表达，不计入禁用元素检测。",
    )
    opening_bridge: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Bridge 阶段派生的开场承接执行字段。Planning 阶段吸收后，"
            "Draft 只从 ChapterPlan 读取这些开场锚点，避免 Bridge/Plan 双源真理。"
        ),
    )
    cross_scene_intent: dict[str, Any] = Field(
        default_factory=_default_cross_scene_intent,
        description="WAVE 阶段使用的跨场景引用与节奏曲线约束。",
    )

    @field_validator(
        "opening_contract",
        "closing_contract",
        "emotional_arc",
        mode="before",
    )
    @classmethod
    def _coerce_plan_text_fields(cls, value: Any) -> str:
        """Coerce LLM-output structured snippets into plain strings for creative text fields."""
        return stringify_text_value(value)

    @field_validator("cross_scene_intent", mode="before")
    @classmethod
    def _coerce_cross_scene_intent(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return _default_cross_scene_intent()
        refs = value.get("cross_scene_references", [])
        pacing = value.get("pacing_curve", [])
        if not isinstance(refs, list):
            refs = []
        if not isinstance(pacing, list):
            pacing = []
        normalized_pacing: list[int] = []
        for item in pacing:
            try:
                normalized_pacing.append(max(1, min(5, int(item))))
            except (TypeError, ValueError):
                continue
        return {
            "cross_scene_references": [item for item in refs if isinstance(item, dict)],
            "pacing_curve": normalized_pacing,
        }

    @field_validator("opening_bridge", mode="before")
    @classmethod
    def _coerce_opening_bridge(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        causal = value.get("causal_link") if isinstance(value.get("causal_link"), dict) else {}
        payload = {
            "opening_time": stringify_text_value(value.get("opening_time", "")),
            "opening_location": stringify_text_value(value.get("opening_location", "")),
            "opening_pov": stringify_text_value(value.get("opening_pov", "")),
            "transition_mode": stringify_text_value(value.get("transition_mode", "")),
            "emotional_carryover": stringify_text_value(value.get("emotional_carryover", "")),
            "action_handoff": stringify_text_value(value.get("action_handoff", "")),
            "bridge_summary": stringify_text_value(value.get("bridge_summary", "")),
            "pending_questions": coerce_text_list(value.get("pending_questions", [])),
            "sensory_anchors": coerce_text_list(value.get("sensory_anchors", [])),
            "opening_acceptance_criteria": coerce_text_list(
                value.get("opening_acceptance_criteria", [])
            ),
            "causal_link": {
                "previous_event": stringify_text_value(causal.get("previous_event", "")),
                "causal_mechanism": stringify_text_value(causal.get("causal_mechanism", "")),
                "unresolved_question": stringify_text_value(causal.get("unresolved_question", "")),
                "open_threads": coerce_text_list(causal.get("open_threads", [])),
            },
        }
        if not any(payload["causal_link"].values()):
            payload.pop("causal_link", None)
        return {key: item for key, item in payload.items() if item not in (None, "", [], {})}

    @field_validator(
        "relationship_evolution",
        "foreshadowing_plan",
        "forbidden_elements",
        mode="before",
    )
    @classmethod
    def _coerce_plan_text_list_fields(cls, value: Any) -> list[str]:
        """Coerce LLM-output snippets while preserving list[str] field shape."""
        return coerce_text_list(value)

    @property
    def beats(self) -> list[str]:
        """Legacy convenience projection used by existing consumers."""
        return [scene.summary for scene in self.scene_intents]

    @property
    def foreshadowing_to_plant(self) -> list[str]:
        """Legacy alias for v1 code paths."""
        return self.foreshadowing_plan


class ContinuityIssue(VersionedSchema):
    """A structured continuity issue detected for a chapter."""

    @model_validator(mode="before")
    @classmethod
    def _preserve_structured_evidence(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        payload = dict(data)
        evidence = payload.get("evidence")
        if isinstance(evidence, dict) and not payload.get("evidence_pairs"):
            payload["evidence_pairs"] = [
                {"source": stringify_text_value(key), "evidence": stringify_text_value(value)}
                for key, value in evidence.items()
                if stringify_text_value(value)
            ]
        return payload

    issue_id: str = Field(
        default="",
        description="Stable diagnostic id used to track this issue across repair/recheck rounds.",
    )
    issue_type: str
    severity: str = Field(default="medium")
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Reviewer confidence after normalization/fusion.",
    )
    source: ContinuityIssueSource = Field(
        default="llm",
        description="Origin of this issue: llm/local/merged/postcondition/manual.",
    )
    repair_surface: RepairSurface = Field(
        default="chapter_text",
        description="Artifact layer that owns the repair, so text repair does not fix JSON/state drift.",
    )
    status: ContinuityIssueStatus = Field(
        default="open",
        description="Lifecycle state in the continuity issue ledger.",
    )
    blocking: bool = Field(
        default=False,
        description="True when this open issue should block archive/finalization.",
    )
    summary: str = Field(default="")
    evidence: str = Field(default="")
    location: str = Field(
        default="",
        description="Position hint (e.g. '开头2段', '第5段', '结尾') for patch targeting.",
    )
    location_confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence of the location anchor (0=unknown, 1=exact paragraph).",
    )
    anchor_type: str = Field(
        default="",
        description=(
            "How the location was determined: "
            "explicit_para / evidence_match / keyword_match / inferred_scope"
        ),
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
    evidence_quote: str = Field(
        default="",
        description=(
            "Exact contiguous quote from the chapter text used as the repair anchor. "
            "For synthetic evidence, leave this empty and rely on paragraph anchors."
        ),
    )
    fix_mode: str = Field(
        default="",
        description="Preferred repair mode: replace / insert / window / fulltext.",
    )
    insert_before_para: int = Field(
        default=0,
        ge=0,
        description="1-based insertion anchor before this paragraph; 0 means unused.",
    )
    insert_after_para: int = Field(
        default=0,
        ge=0,
        description="1-based insertion anchor after this paragraph; 0 means unused.",
    )
    affected_characters: list[str] = Field(default_factory=list)
    rewrite_scope: str = Field(default="chapter")
    fix_actions: list[str] = Field(default_factory=list)
    evidence_pairs: list[dict[str, Any]] = Field(default_factory=list)
    adjudication_notes: str = Field(default="")
    handoff_notes: str = Field(default="")
    repair_boundary: str = Field(default="")
    repair_scope: dict[str, Any] = Field(default_factory=dict)
    must_preserve: list[str] = Field(default_factory=list)
    forbidden_changes: list[str] = Field(default_factory=list)
    expected_state: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured state/contract expected by the issue.",
    )
    observed_state: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured state observed in the artifact/text when available.",
    )
    missing_anchors: list[ContinuityAnchor] = Field(
        default_factory=list,
        description="Semantic anchors the repair/verifier must account for.",
    )
    postconditions: list[RepairPostcondition] = Field(
        default_factory=list,
        description="Per-issue verification conditions after repair.",
    )
    repair_directive: ContinuityRepairDirective | None = Field(
        default=None,
        description="Actionable audit-side repair contract consumed by the text repairer.",
    )
    validator_id: str = Field(default="", description="Preferred verifier for this issue.")
    diagnostic_note: str = Field(
        default="",
        description="Short machine/debug note explaining routing or suppression decisions.",
    )

    @field_validator(
        "issue_type",
        "summary",
        "evidence",
        "location",
        "evidence_quote",
        "fix_mode",
        "rewrite_scope",
        "adjudication_notes",
        "handoff_notes",
        "repair_boundary",
        "validator_id",
        "diagnostic_note",
        mode="before",
    )
    @classmethod
    def _coerce_issue_text_fields(cls, value: Any) -> str:
        return stringify_text_value(value)

    @field_validator("severity", mode="before")
    @classmethod
    def _coerce_issue_severity(cls, value: Any) -> str:
        return _normalize_issue_severity(value)


class ContinuityReport(VersionedSchema):
    """Cross-chapter continuity audit output."""

    review_mode: str = Field(default="full_review")
    continuity_score: float = Field(default=10.0, ge=0.0, le=10.0)
    summary: str = Field(default="")
    issues: list[ContinuityIssue] = Field(default_factory=list)
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


class TargetSection(VersionedSchema):
    """Paragraph span targeted by a repair step."""

    section_type: str
    start_paragraph: int = Field(default=0, ge=0)
    end_paragraph: int = Field(default=0, ge=0)
    reason: str = Field(default="")


class RepairPlan(VersionedSchema):
    """Explicit repair plan for continuity issues."""

    issues: list[ContinuityIssue] = Field(default_factory=list)
    deferred_issues: list[ContinuityIssue] = Field(
        default_factory=list,
        description="Issues intentionally not handled by the text repairer because they belong to another surface.",
    )
    repair_surfaces: list[str] = Field(default_factory=list)
    target_sections: list[TargetSection] = Field(default_factory=list)
    must_keep: list[str] = Field(default_factory=list)
    must_change: list[str] = Field(default_factory=list)
    expected_outcome: str = Field(default="")
    no_op: bool = Field(default=False)


class NarrativeBlueprintContext(VersionedSchema):
    """当前章节在全局叙事蓝图中的宏观坐标（只读参考，由 build_packet 计算注入）."""

    current_phase_name: str = Field(default="", description="当前叙事阶段名称")
    current_phase_description: str = Field(default="", description="当前阶段核心叙事目标")
    current_phase_tension_level: str = Field(
        default="", description="张力水平（渐升/高/高潮/回落）"
    )
    current_phase_key_events: list[str] = Field(default_factory=list, description="本阶段关键事件")
    current_phase_time_context: str = Field(default="", description="本阶段时间锚点")
    current_phase_locations: list[str] = Field(default_factory=list, description="本阶段核心场景")
    current_phase_key_characters: list[str] = Field(
        default_factory=list, description="本阶段核心人物"
    )
    is_turning_point: bool = Field(default=False, description="本章是否为关键转折点")
    turning_point_description: str = Field(
        default="", description="转折点描述（is_turning_point=True时有值）"
    )
    next_turning_point_chapter: int = Field(default=0, description="下一个转折点章节号（0=无）")
    next_turning_point_description: str = Field(default="", description="下一个转折点描述")
    active_subplot_names: list[str] = Field(
        default_factory=list, description="本章应覆盖的支线名称"
    )
    active_subplot_weave_hints: list[str] = Field(
        default_factory=list,
        description="本章应关注的交织提示，如 '主线事件X将触发支线Y的转折'",
    )
    subplot_dependency_warnings: list[str] = Field(
        default_factory=list,
        description="未满足的支线依赖警告",
    )
    target_chapter_rhythm: dict[str, Any] = Field(
        default_factory=dict,
        description="蓝图 chapter_rhythm_curve 为本章声明的目标节奏。",
    )

    @field_validator(
        "current_phase_name",
        "current_phase_description",
        "current_phase_tension_level",
        "current_phase_time_context",
        "turning_point_description",
        "next_turning_point_description",
        mode="before",
    )
    @classmethod
    def _coerce_narrative_context_text(cls, v: Any) -> str:
        return stringify_text_value(v)


class ChapterStatePacket(VersionedSchema):
    """Unified chapter context passed through long-form pipeline steps."""

    chapter_number: int = Field(ge=1)
    chapter_outline: ChapterOutline
    canon_context: dict[str, Any] = Field(default_factory=dict)
    narrative_state_projection: dict[str, Any] = Field(
        default_factory=dict,
        description="LLM-adjudicated authoritative state projection for prompt hard contracts.",
    )
    chapter_contract: dict[str, Any] = Field(
        default_factory=dict,
        description="LLM-planned executable chapter contract when available.",
    )
    milestone_window: dict[str, Any] = Field(
        default_factory=dict,
        description="当前章节可见的剧情里程碑窗口；不包含完整未来大纲。",
    )
    progression_ledger_tail: list[dict[str, Any]] = Field(
        default_factory=list,
        description="最近已裁定的剧情推进账本条目。",
    )
    arc_liveness_report: dict[str, Any] = Field(
        default_factory=dict,
        description="配角/副线弧光活跃度提示，供 Plan 阶段轻触维护。",
    )
    stage_visibility_diagnostics: dict[str, Any] = Field(
        default_factory=dict,
        description="Bridge/Plan/Draft/Judge 实际可见上下文摘要，用于开发期诊断。",
    )
    retrieval_evidence_pack: dict[str, Any] = Field(default_factory=dict)
    previous_exit_state: ChapterExitState | None = None
    previous_chapter_ending: str = Field(default="")
    previous_creative_report: dict[str, Any] | None = None
    previous_volume_summary: str = Field(default="")
    current_volume_number: int = Field(default=1, ge=1)
    character_profiles: list[dict[str, Any]] = Field(default_factory=list)
    active_relationships: list[RelationshipState] = Field(default_factory=list)
    active_plot_threads: list[PlotThreadState] = Field(default_factory=list)
    must_carry_forward: list[CarryForwardItem] = Field(default_factory=list)
    known_characters: list[str] = Field(default_factory=list)
    bridge: ChapterBridge | None = None
    previous_bridge: ChapterBridge | None = None
    accumulated_forbidden_repetition: list[str] = Field(default_factory=list)
    narrative_context: NarrativeBlueprintContext | None = None
    bridge_context_brief: str = Field(
        default="",
        description="Task-specific compact brief for bridge generation.",
    )
    planning_context_brief: str = Field(
        default="",
        description="Task-specific compact brief for chapter planning.",
    )
    world_setting_brief: str = Field(
        default="",
        description="压缩版世界设定参考，供LLM按需引用（来自spec.json.world_hint）。",
    )
    continuity_context_brief: str = Field(
        default="",
        description="Task-specific compact brief for continuity evaluation/repair.",
    )
    banned_phrases: list[str] = Field(
        default_factory=list,
        description="累积禁用句式/短语列表，来自未解决的重复类问题，供后续章节规划与写作时规避。",
    )
    guard_constraints: list[str] = Field(
        default_factory=list,
        description="AI护栏裁决器输出的下一章强制约束（来自上一章PlotGuardDecision.next_chapter_constraints）。",
    )

    @model_validator(mode="before")
    @classmethod
    def _upgrade_carry_forward(cls, data: Any) -> Any:
        """Normalize legacy ``must_carry_forward: list[str]`` to structured items.

        Mirrors ``ChapterExitState._upgrade_carry_forward`` so the packet —
        which receives a projection of the previous exit state's carry-forward
        items — hydrates legacy plain-string entries as open
        ``CarryForwardItem`` objects without a data migration.
        """
        if not isinstance(data, dict):
            return data
        payload = dict(data)
        raw = payload.get("must_carry_forward")
        if isinstance(raw, list):
            normalized: list[Any] = []
            for item in raw:
                if isinstance(item, str):
                    normalized.append({"text": item, "status": "open"})
                else:
                    normalized.append(item)
            payload["must_carry_forward"] = normalized
        elif isinstance(raw, str) and raw:
            payload["must_carry_forward"] = [{"text": raw, "status": "open"}]
        return payload


# ---------------------------------------------------------------------------
# Resolve forward references for cross-module type annotations.
# ---------------------------------------------------------------------------
ContinuityIssue.model_rebuild()
ContinuityReport.model_rebuild()
ChapterStatePacket.model_rebuild()
