"""Schemas for the LLM-adjudicated narrative state layer."""

from __future__ import annotations

from hashlib import sha1
from typing import Any, Literal, cast

from pydantic import ConfigDict, Field, field_validator, model_validator

from novel_forge.core.schemas.base import VersionedSchema
from novel_forge.core.schemas.cognition import (
    ActionLevel,
    AwarenessLevel,
    CognitiveLevel,
    normalize_character_knowledge_coverage,
)

EntityType = Literal["character", "location", "item", "organization", "concept", "unknown"]
DeltaType = Literal[
    "event",
    "relationship",
    "item",
    "knowledge",
    "cognitive",
    "alias",
    "promise",
    "world_rule",
    "character_state",
    "other",
]
AdjudicationVerdict = Literal["accept", "reject", "ambiguous", "needs_repair", "defer"]
AdjudicationSeverity = Literal["low", "medium", "high", "critical"]
ProgressionKind = Literal[
    "event",
    "knowledge",
    "relationship",
    "item",
    "promise",
    "suspense",
    "world_rule",
    "character_arc",
    "other",
]
MilestoneStatus = Literal["planned", "foreshadow", "partial", "completed", "blocked"]
ExpressionChannel = Literal[
    "somatic_reaction",
    "action_tag",
    "sentence_pattern",
    "sensory_anchor",
    "metaphor_image",
    "dialogue_tag",
    "emotional_beat",
    "other",
]
EvidenceStatus = Literal["active", "superseded", "stale"]

_ADJUDICATION_VERDICTS = {"accept", "reject", "ambiguous", "needs_repair", "defer"}
_ADJUDICATION_SEVERITIES = {"low", "medium", "high", "critical"}
_DELTA_TYPES = {
    "event",
    "relationship",
    "item",
    "knowledge",
    "cognitive",
    "alias",
    "promise",
    "world_rule",
    "character_state",
    "other",
}
_DELTA_TYPE_ALIASES = {
    "plot_event": "event",
    "story_event": "event",
    "major_event": "event",
    "事件": "event",
    "剧情事件": "event",
    "relationship_change": "relationship",
    "relationship_shift": "relationship",
    "relation": "relationship",
    "关系": "relationship",
    "关系变化": "relationship",
    "object": "item",
    "artifact": "item",
    "prop": "item",
    "item_change": "item",
    "item_state": "item",
    "物品": "item",
    "道具": "item",
    "information": "knowledge",
    "realization": "knowledge",
    "discovery": "knowledge",
    "revelation": "knowledge",
    "secret": "knowledge",
    "knowledge_change": "knowledge",
    "知识": "knowledge",
    "信息": "knowledge",
    "认知": "knowledge",
    "cognitive_change": "cognitive",
    "awareness": "cognitive",
    "awareness_change": "cognitive",
    "认知变化": "cognitive",
    "知情状态": "cognitive",
    "称谓": "alias",
    "别名": "alias",
    "nickname": "alias",
    "title": "alias",
    "alias_change": "alias",
    "foreshadow": "promise",
    "foreshadowing": "promise",
    "suspense": "promise",
    "promise_change": "promise",
    "伏笔": "promise",
    "承诺": "promise",
    "rule": "world_rule",
    "setting": "world_rule",
    "worldbuilding": "world_rule",
    "world_lore": "world_rule",
    "world_rule_change": "world_rule",
    "世界规则": "world_rule",
    "角色状态": "character_state",
    "character_status": "character_state",
    "character_emotion": "character_state",
    "character_mood": "character_state",
    "emotional_state": "character_state",
    "emotional_change": "character_state",
    "emotional_breakthrough": "character_state",
    "emotion": "character_state",
    "inner_state": "character_state",
    "inner_change": "character_state",
    "mental_state": "character_state",
    "character_arc": "character_state",
    "character_development": "character_state",
    "character_growth": "character_state",
    "identity": "character_state",
    "location": "character_state",
    "position": "character_state",
    "status": "character_state",
    "情绪": "character_state",
    "情绪变化": "character_state",
    "情感突破": "character_state",
    "身份": "character_state",
    "位置": "character_state",
    "状态": "character_state",
    "其他": "other",
    "misc": "other",
    "miscellaneous": "other",
    "unknown": "other",
}

COGNITIVE_LEVEL_ORDER: tuple[CognitiveLevel, ...] = (
    "unaware",
    "subconscious",
    "suspicion",
    "partial",
    "confirmed",
    "acknowledged",
)
ACTION_LEVEL_ORDER: tuple[ActionLevel, ...] = (
    "none",
    "internal",
    "hinted",
    "revealed",
    "acted",
)
_COGNITIVE_LEVEL_RANK = {level: index for index, level in enumerate(COGNITIVE_LEVEL_ORDER)}
_ACTION_LEVEL_RANK = {level: index for index, level in enumerate(ACTION_LEVEL_ORDER)}
_VERDICT_ALIASES = {
    "accepted": "accept",
    "approve": "accept",
    "approved": "accept",
    "pass": "accept",
    "write": "accept",
    "write_state": "accept",
    "拒绝": "reject",
    "rejected": "reject",
    "deny": "reject",
    "denied": "reject",
    "discard": "reject",
    "drop": "reject",
    "uncertain": "ambiguous",
    "unknown": "ambiguous",
    "unclear": "ambiguous",
    "不明确": "ambiguous",
    "歧义": "ambiguous",
    "ambivalent": "ambiguous",
    "repair": "needs_repair",
    "need_repair": "needs_repair",
    "needs_fix": "needs_repair",
    "fix": "needs_repair",
    "需修复": "needs_repair",
    "需要修复": "needs_repair",
    "deferred": "defer",
    "pending": "defer",
    "wait": "defer",
    "延后": "defer",
    "挂起": "defer",
}
_MIXED_VERDICTS = {
    "mixed",
    "partial",
    "partial_accept",
    "partially_accept",
    "accept_with_reject",
    "accept_with_rejections",
    "accepted_and_rejected",
    "accept_and_reject",
    "mixed_accept",
    "mixed_result",
    "混合",
    "混合裁定",
    "部分接受",
    "有取有舍",
}
_SEVERITY_ALIASES = {
    "none": "low",
    "info": "low",
    "informational": "low",
    "minor": "low",
    "trivial": "low",
    "ok": "low",
    "pass": "low",
    "warning": "medium",
    "warn": "medium",
    "moderate": "medium",
    "error": "medium",
    "invalid": "medium",
    "format_error": "medium",
    "issue": "medium",
    "problem": "medium",
    "major": "high",
    "serious": "high",
    "severe": "high",
    "blocker": "critical",
    "blocking": "critical",
    "fatal": "critical",
    "严重": "high",
    "高": "high",
    "中": "medium",
    "低": "low",
    "致命": "critical",
    "阻断": "critical",
}


def _clean_verdict_text(value: Any) -> str:
    return (
        str(value or "")
        .strip()
        .strip("\"'`，。,.；;：: ")
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )


def normalize_adjudication_verdict(value: Any, *, default: str = "ambiguous") -> str:
    """Normalize common LLM verdict variants into the strict local enum."""
    raw = _clean_verdict_text(value)
    if raw in _ADJUDICATION_VERDICTS:
        return raw
    if raw in _VERDICT_ALIASES:
        return _VERDICT_ALIASES[raw]
    if raw in _MIXED_VERDICTS:
        return default
    return default


def normalize_adjudication_severity(value: Any, *, default: str = "medium") -> str:
    """Normalize common LLM severity/status labels into the strict local enum."""
    raw = _clean_verdict_text(value)
    if raw in _ADJUDICATION_SEVERITIES:
        return raw
    if raw in _SEVERITY_ALIASES:
        return _SEVERITY_ALIASES[raw]
    return default


def normalize_delta_type(value: Any, *, default: str = "other") -> str:
    """Normalize common LLM delta type variants into the strict local enum."""
    raw = _clean_verdict_text(value)
    if raw in _DELTA_TYPES:
        return raw
    if raw in _DELTA_TYPE_ALIASES:
        return _DELTA_TYPE_ALIASES[raw]
    return default


def cognitive_level_rank(value: Any) -> int:
    """Return the deterministic rank for a CognitiveLevel token."""
    raw = str(value or "").strip().lower()
    return _COGNITIVE_LEVEL_RANK.get(cast(CognitiveLevel, raw), -1)


def action_level_rank(value: Any) -> int:
    """Return the deterministic rank for an ActionLevel token."""
    raw = str(value or "").strip().lower()
    return _ACTION_LEVEL_RANK.get(cast(ActionLevel, raw), -1)


def exceeds_cognitive_level(actual: Any, allowed: Any) -> bool:
    """True when actual awareness is above the allowed cognitive level."""
    actual_rank = cognitive_level_rank(actual)
    allowed_rank = cognitive_level_rank(allowed)
    return actual_rank >= 0 and allowed_rank >= 0 and actual_rank > allowed_rank


def exceeds_action_level(actual: Any, allowed: Any) -> bool:
    """True when actual action/reveal state is above the allowed action level."""
    actual_rank = action_level_rank(actual)
    allowed_rank = action_level_rank(allowed)
    return actual_rank >= 0 and allowed_rank >= 0 and actual_rank > allowed_rank


def normalize_final_adjudication_payload(data: Any) -> Any:
    """Normalize final verdicts using only structured model-selected buckets."""
    if not isinstance(data, dict):
        return data
    payload = dict(data)
    if "target_coverage" not in payload and "coverage_matrix" in payload:
        payload["target_coverage"] = payload.get("coverage_matrix")
    normalized = normalize_adjudication_verdict(payload.get("verdict"), default="")
    payload["verdict"] = normalized if normalized else _derive_final_verdict(payload)
    return payload


def _derive_final_verdict(payload: dict[str, Any]) -> str:
    if (
        bool(payload.get("should_block_archive"))
        or _has_items(
            payload.get("repair_candidate_ids"),
        )
        or _has_items(payload.get("repair_issues"))
    ):
        return "needs_repair"
    if _has_items(payload.get("accepted_candidate_ids")) or _has_items(
        payload.get("state_updates")
    ):
        return "accept"
    if _has_items(payload.get("pending_candidate_ids")) or _has_items(payload.get("pending_items")):
        return "defer"
    if _has_items(payload.get("rejected_candidate_ids")):
        return "reject"
    return "ambiguous"


def _has_items(value: Any) -> bool:
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return bool(value)


def stable_id(prefix: str, *parts: Any) -> str:
    """Return a deterministic short id for storage keys."""
    raw = "|".join(str(part or "") for part in parts)
    digest = sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


class EntityRecord(VersionedSchema):
    """A canonical entity known to the narrative state layer."""

    model_config = ConfigDict(extra="ignore")

    entity_id: str
    name: str
    entity_type: EntityType = "unknown"
    aliases: list[str] = Field(default_factory=list)
    source: str = ""
    notes: str = ""
    sensory_access_rules: list[str] = Field(
        default_factory=list,
        description="该实体的感知通道限制，如'无法感知密室内动作'。用于 POV 约束推导。",
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_dynamic_entity_key(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if value.get("entity_id") and value.get("name"):
            return value

        known_fields = {
            "entity_id",
            "name",
            "entity_type",
            "aliases",
            "source",
            "notes",
            "sensory_access_rules",
        }
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
    def _clean_required_text(cls, value: Any) -> str:
        return str(value or "").strip()


class EntityRegistry(VersionedSchema):
    """Mechanical entity registry; semantic identity decisions remain with LLM tasks."""

    model_config = ConfigDict(extra="ignore")

    entities: list[EntityRecord] = Field(default_factory=list)

    def by_id(self) -> dict[str, EntityRecord]:
        return {item.entity_id: item for item in self.entities if item.entity_id}


class EvidenceSpan(VersionedSchema):
    """A mechanically located quote span from chapter text."""

    model_config = ConfigDict(extra="ignore")

    quote: str = ""
    chapter_number: int = Field(default=0, ge=0)
    paragraph_index: int | None = Field(default=None, ge=0)
    start_offset: int | None = Field(default=None, ge=0)
    end_offset: int | None = Field(default=None, ge=0)
    found: bool = False
    context: str = ""
    source_text_hash: str = ""
    chapter_revision_id: str = ""
    supersedes_revision_id: str = ""
    depends_on_revisions: list[str] = Field(default_factory=list)
    evidence_status: EvidenceStatus = "active"


class NarrativeContract(VersionedSchema):
    """Global contract generated during long-form initialization."""

    model_config = ConfigDict(extra="ignore")

    title: str = ""
    world_rules: list[str] = Field(default_factory=list)
    character_arcs: list[dict[str, Any]] = Field(default_factory=list)
    plot_threads: list[dict[str, Any]] = Field(default_factory=list)
    promise_plan: list[dict[str, Any]] = Field(default_factory=list)
    notes: str = ""


class CognitiveConstraint(VersionedSchema):
    """Chapter-scoped cognitive constraint projected from init-time claims."""

    model_config = ConfigDict(extra="ignore")

    constraint_id: str = ""
    claim_id: str
    claim_text: str = ""
    cognitive_subjects: list[str] = Field(default_factory=list)
    cognitive_object: str = ""
    cognitive_level: CognitiveLevel = "unaware"
    action_level: ActionLevel = "none"
    reader_awareness: AwarenessLevel = "unknown"
    character_knowledge_coverage: dict[str, AwarenessLevel] = Field(default_factory=dict)
    cognitive_chapter: int | None = None
    public_reveal_chapter: int | None = None
    foreshadow_chapters: list[int] = Field(default_factory=list)
    source_artifact: str = ""
    source_path: str = ""
    evidence: str = ""

    @field_validator(
        "constraint_id",
        "claim_id",
        "claim_text",
        "cognitive_object",
        "source_artifact",
        "source_path",
        "evidence",
        mode="before",
    )
    @classmethod
    def _normalize_constraint_text(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("cognitive_subjects", mode="before")
    @classmethod
    def _normalize_cognitive_subjects(cls, value: Any) -> list[str]:
        if value is None:
            return []
        values = value if isinstance(value, list) else [value]
        result: list[str] = []
        for item in values:
            text = str(item or "").strip()
            if text and text not in result:
                result.append(text)
        return result

    @field_validator("cognitive_level", "action_level", "reader_awareness", mode="before")
    @classmethod
    def _normalize_constraint_enum(cls, value: Any) -> str:
        return str(value or "").strip().lower()

    @field_validator("character_knowledge_coverage", mode="before")
    @classmethod
    def _normalize_character_knowledge_coverage(cls, value: Any) -> dict[str, AwarenessLevel]:
        return normalize_character_knowledge_coverage(value, none_as_empty=True)

    @field_validator("cognitive_chapter", "public_reveal_chapter", mode="before")
    @classmethod
    def _normalize_optional_chapter(cls, value: Any) -> int | None:
        if value is None or str(value).strip() == "":
            return None
        if isinstance(value, bool):
            raise ValueError("chapter anchor must be a positive integer or null")
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValueError("chapter anchor must be a positive integer or null") from None
        if number < 1:
            raise ValueError("chapter anchor must be a positive integer or null")
        return number

    @field_validator("foreshadow_chapters", mode="before")
    @classmethod
    def _normalize_foreshadow_chapters(cls, value: Any) -> list[int]:
        if value is None:
            return []
        values = value if isinstance(value, list) else [value]
        result: list[int] = []
        for item in values:
            try:
                number = int(item)
            except (TypeError, ValueError):
                continue
            if number >= 1 and number not in result:
                result.append(number)
        return sorted(result)


class ChapterContract(VersionedSchema):
    """Executable contract for one chapter."""

    model_config = ConfigDict(extra="ignore")

    chapter_number: int = Field(ge=1)
    title: str = ""
    entry_state_requirements: list[str] = Field(
        default_factory=list,
        json_schema_extra={
            "state_adjudication_evidence_anchor": True,
            "state_adjudication_archive_required": False,
        },
    )
    required_events: list[str] = Field(
        default_factory=list,
        json_schema_extra={
            "state_adjudication_evidence_anchor": True,
            "state_adjudication_archive_required": True,
        },
    )
    allowed_changes: list[str] = Field(default_factory=list)
    forbidden_changes: list[str] = Field(default_factory=list)
    promise_ops: list[dict[str, Any]] = Field(default_factory=list)
    relationship_ops: list[dict[str, Any]] = Field(default_factory=list)
    item_ops: list[dict[str, Any]] = Field(default_factory=list)
    knowledge_ops: list[dict[str, Any]] = Field(
        default_factory=list,
        json_schema_extra={
            "state_adjudication_evidence_anchor": True,
            "state_adjudication_archive_required": True,
            "state_adjudication_delta_type": "knowledge",
        },
    )
    cognitive_constraints: list[CognitiveConstraint] = Field(
        default_factory=list,
        json_schema_extra={
            "state_adjudication_evidence_anchor": True,
            "state_adjudication_archive_required": False,
            "state_adjudication_delta_type": "cognitive",
        },
    )
    new_character_candidates: list[dict[str, Any]] = Field(default_factory=list)
    new_entity_candidates: list[dict[str, Any]] = Field(default_factory=list)
    new_group_candidates: list[dict[str, Any]] = Field(default_factory=list)
    new_collective_candidates: list[dict[str, Any]] = Field(default_factory=list)
    new_organization_candidates: list[dict[str, Any]] = Field(default_factory=list)
    new_location_candidates: list[dict[str, Any]] = Field(default_factory=list)
    new_item_candidates: list[dict[str, Any]] = Field(default_factory=list)
    new_concept_candidates: list[dict[str, Any]] = Field(default_factory=list)
    exit_state_targets: list[str] = Field(
        default_factory=list,
        json_schema_extra={
            "state_adjudication_evidence_anchor": True,
            "state_adjudication_archive_required": True,
            "state_adjudication_prefer_last": True,
        },
    )
    required_progressions: list[str] = Field(
        default_factory=list,
        description="本章必须实际推进的剧情状态、知识、关系、信物、承诺或悬念。",
        json_schema_extra={
            "state_adjudication_evidence_anchor": True,
            "state_adjudication_archive_required": True,
        },
    )
    allowed_progressions: list[str] = Field(
        default_factory=list,
        description="本章允许触碰或铺垫但不一定完成的剧情推进项。",
    )
    forbidden_progressions: list[str] = Field(
        default_factory=list,
        description="本章禁止提前揭示、禁止完成、禁止触发的未来剧情推进项。",
    )
    completion_criteria: list[str] = Field(
        default_factory=list,
        description="判断本章契约完成的可观察标准。",
        json_schema_extra={
            "state_adjudication_evidence_anchor": True,
            "state_adjudication_archive_required": True,
        },
    )
    future_leak_risks: list[str] = Field(
        default_factory=list,
        description="本章容易误提前消费的未来剧情点或未来高光。",
    )
    pov_character_id: str = Field(
        default="",
        description="Canonical POV character entity_id inherited from ChapterOutline.",
    )
    involved_character_ids: list[str] = Field(
        default_factory=list,
        description="Canonical active character entity_ids for this chapter.",
    )
    required_character_ids: list[str] = Field(
        default_factory=list,
        description="Canonical character entity_ids that must actively drive this chapter.",
    )
    support_character_ids: list[str] = Field(
        default_factory=list,
        description="Canonical character entity_ids allowed as support cast.",
    )
    cast_plan: dict[str, Any] = Field(
        default_factory=dict,
        description="Canonical chapter cast constraints copied from ChapterOutline.",
    )
    emotional_plan: dict[str, Any] = Field(
        default_factory=dict,
        description="Chapter-level emotional execution intent copied from ChapterOutline.",
    )
    scene_design_goals: list[str] = Field(
        default_factory=list,
        description="Scene-level goals that PLAN_CHAPTER must realize.",
    )
    source: str = "chapter_outline"

    @field_validator(
        "pov_character_id",
        mode="before",
    )
    @classmethod
    def _normalize_contract_id_text(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator(
        "involved_character_ids",
        "required_character_ids",
        "support_character_ids",
        "scene_design_goals",
        mode="before",
    )
    @classmethod
    def _normalize_contract_str_lists(cls, value: Any) -> list[str]:
        if value is None:
            return []
        values = value if isinstance(value, list) else [value]
        result: list[str] = []
        seen: set[str] = set()
        for item in values:
            text = str(item or "").strip()
            if text and text not in seen:
                seen.add(text)
                result.append(text)
        return result


class PlotMilestone(VersionedSchema):
    """Compressed global plot milestone for runtime retrieval."""

    model_config = ConfigDict(extra="ignore")

    milestone_id: str
    chapter_number: int = Field(ge=1)
    title: str = ""
    summary: str = ""
    kind: ProgressionKind = "event"
    status: MilestoneStatus = "planned"
    depends_on: list[str] = Field(default_factory=list)
    allow_before_chapter: int = Field(default=0, ge=0)
    block_before_chapter: int = Field(default=0, ge=0)
    payoff_only: bool = False
    source: str = ""


class PlotMilestoneIndex(VersionedSchema):
    """Compact milestone index generated at initialization."""

    model_config = ConfigDict(extra="ignore")

    project_id: str = ""
    total_chapters: int = Field(default=0, ge=0)
    milestones: list[PlotMilestone] = Field(default_factory=list)
    source_hash: str = ""
    notes: str = ""


class MilestoneWindow(VersionedSchema):
    """Runtime-scoped milestone slice for one chapter/stage."""

    model_config = ConfigDict(extra="ignore")

    chapter_number: int = Field(ge=1)
    current: list[PlotMilestone] = Field(default_factory=list)
    previous_context: list[PlotMilestone] = Field(default_factory=list)
    future_guardrails: list[PlotMilestone] = Field(default_factory=list)
    withheld_future_count: int = Field(default=0, ge=0)
    policy: str = ""


class ProgressionLedgerEntry(VersionedSchema):
    """Append-only runtime record of actual progression observed in a chapter."""

    model_config = ConfigDict(extra="ignore")

    entry_id: str
    chapter_number: int = Field(ge=1)
    progression: str = ""
    kind: ProgressionKind = "other"
    milestone_id: str = ""
    status: MilestoneStatus = "partial"
    evidence_quotes: list[str] = Field(default_factory=list)
    source: str = "contract_execution_audit"


class ProgressionLedger(VersionedSchema):
    """Project-level ledger of completed or partially completed plot progressions."""

    model_config = ConfigDict(extra="ignore")

    entries: list[ProgressionLedgerEntry] = Field(default_factory=list)
    last_chapter: int = 0


class ContractExecutionReport(VersionedSchema):
    """Persisted report for chapter-contract execution and future-leak checks."""

    model_config = ConfigDict(extra="ignore")

    report_type: str = "contract_execution"
    chapter_number: int = Field(ge=1)
    missing_required_progressions: list[str] = Field(default_factory=list)
    unexpected_progressions: list[str] = Field(default_factory=list)
    forbidden_progression_hits: list[str] = Field(default_factory=list)
    future_leak_hits: list[str] = Field(default_factory=list)
    cognitive_constraint_hits: list[str] = Field(default_factory=list)
    missing_knowledge_ops: list[str] = Field(default_factory=list)
    unaccepted_knowledge_ops: list[str] = Field(default_factory=list)
    local_candidate_hits: list[dict[str, Any]] = Field(
        default_factory=list,
        description="本地预筛候选，仅供 LLM 裁判复核，不作为最终违规结论。",
    )
    contract_completion_score: float = Field(default=10.0, ge=0.0, le=10.0)
    repair_or_replan_decision: str = "continue"
    verdict: AdjudicationVerdict = "accept"
    severity: AdjudicationSeverity = "low"
    rationale: str = ""
    evidence_quotes: list[str] = Field(default_factory=list)
    should_block_archive: bool = False
    source_text_hash: str = ""

    @field_validator("verdict", mode="before")
    @classmethod
    def _normalize_verdict(cls, value: Any) -> str:
        return normalize_adjudication_verdict(value)

    @field_validator("severity", mode="before")
    @classmethod
    def _normalize_severity(cls, value: Any) -> str:
        raw = str(value or "").strip().lower()
        if raw in {"low", "minor", "safe"}:
            return "low"
        if raw in {"medium", "mid", "moderate", "warn", "warning"}:
            return "medium"
        if raw in {"high", "severe", "major"}:
            return "high"
        if raw in {"critical", "block", "blocking", "fatal"}:
            return "critical"
        return "medium"


class ExpressionChannelRecord(VersionedSchema):
    """Typed forbidden-repetition record shared by motif, memory, and checks."""

    model_config = ConfigDict(extra="ignore")

    text: str
    channel: ExpressionChannel = "other"
    channel_id: str = ""
    source: str = ""
    level: str = "soft"
    cooldown_chapters: int = Field(default=3, ge=0)
    actor_scope: str = "global"
    reason: str = ""
    examples: list[str] = Field(default_factory=list)
    surface_forms: list[str] = Field(
        default_factory=list,
        description="Natural-language phrases used for local literal scanning; never regex.",
    )
    trigger_contexts: list[str] = Field(default_factory=list)
    replacement_axes: list[str] = Field(default_factory=list)
    allowed_when: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    provenance: str = ""
    recent_semantic_hits: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Compact cross-chapter semantic evidence retrieved from expression memory.",
    )
    semantic_hit_count: int = Field(default=0, ge=0)
    last_seen_chapter: int = Field(default=0, ge=0)
    semantic_similarity_max: float = Field(default=0.0, ge=-1.0, le=1.0)


class ExpressionObservation(VersionedSchema):
    """One verified expression-channel observation indexed for semantic cooldown."""

    model_config = ConfigDict(extra="ignore")

    observation_id: str = ""
    chapter_number: int = Field(default=0, ge=0)
    scene_index: int = Field(default=0, ge=0)
    quote: str = Field(default="", min_length=1)
    quote_hash: str = ""
    source_text_hash: str = ""
    source_start: int = Field(default=-1)
    source_end: int = Field(default=-1)
    channel_id: str = Field(default="", min_length=1)
    channel: ExpressionChannel = "other"
    profile_label: str = ""
    trigger_context: str = ""
    semantic_role: str = ""
    pov_character: str = ""
    actor: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    embedding_text: str = ""

    @model_validator(mode="after")
    def _fill_derived_fields(self) -> "ExpressionObservation":
        if not self.quote_hash and self.quote:
            self.quote_hash = sha1(self.quote.encode("utf-8")).hexdigest()
        if not self.embedding_text:
            parts = [
                f"通道:{self.profile_label or self.channel_id}",
                f"类型:{self.channel}",
                f"语境:{self.trigger_context}",
                f"功能:{self.semantic_role}",
                f"原文:{self.quote}",
            ]
            self.embedding_text = "；".join(part for part in parts if not part.endswith(":"))
        if not self.observation_id:
            seed = "|".join(
                [
                    str(self.chapter_number),
                    str(self.scene_index),
                    self.channel_id,
                    self.quote_hash,
                    str(self.source_start),
                ]
            )
            self.observation_id = f"expr_{sha1(seed.encode('utf-8')).hexdigest()[:16]}"
        return self


class ExpressionObservationExtractionResult(VersionedSchema):
    """Bounded output from EXTRACT_EXPRESSION_OBSERVATIONS."""

    model_config = ConfigDict(extra="ignore")

    observations: list[ExpressionObservation] = Field(default_factory=list)
    skipped_reason: str = ""
    source_text_hash: str = ""


class ExpressionChannelMemoryState(VersionedSchema):
    """JSON metadata mirror for the expression-channel vector collection."""

    model_config = ConfigDict(extra="ignore")

    project_id: str = ""
    vector_store: dict[str, Any] = Field(default_factory=dict)
    source_hashes: dict[str, str] = Field(default_factory=dict)
    observations: list[ExpressionObservation] = Field(default_factory=list)


class ExpressionRepetitionReport(VersionedSchema):
    """Local/LLM-readable report for repeated expression channels."""

    model_config = ConfigDict(extra="ignore")

    report_type: str = "expression_repetition"
    chapter_number: int = Field(default=0, ge=0)
    records: list[ExpressionChannelRecord] = Field(default_factory=list)
    hits: list[dict[str, Any]] = Field(default_factory=list)
    repair_guidance: list[str] = Field(default_factory=list)
    source_text_hash: str = ""


class DormantArc(VersionedSchema):
    """One subplot/character arc that has not progressed recently."""

    model_config = ConfigDict(extra="ignore")

    arc_id: str
    name: str = ""
    arc_type: str = "subplot"
    last_progress_chapter: int = Field(default=0, ge=0)
    expected_next_touch: int = Field(default=0, ge=0)
    risk_level: str = "low"
    planning_hint: str = ""


class ArcLivenessReport(VersionedSchema):
    """Planning hint report for dormant side arcs."""

    model_config = ConfigDict(extra="ignore")

    report_type: str = "arc_liveness"
    chapter_number: int = Field(ge=1)
    dormant_arcs: list[DormantArc] = Field(default_factory=list)
    last_progress_chapter: int = Field(default=0, ge=0)
    expected_next_touch: int = Field(default=0, ge=0)
    risk_level: str = "low"
    planning_hint: str = ""


class CandidateStateDelta(VersionedSchema):
    """A candidate state change extracted by LLM from a chapter."""

    model_config = ConfigDict(extra="ignore")

    candidate_id: str = ""
    chapter_number: int = Field(ge=1)
    delta_type: DeltaType = "other"
    summary: str = ""
    entity_ids: list[str] = Field(default_factory=list)
    covered_target_ids: list[str] = Field(default_factory=list)
    proposed_delta: dict[str, Any] = Field(default_factory=dict)
    cognitive_subjects: list[str] = Field(default_factory=list)
    cognitive_object: str = ""
    cognitive_level: CognitiveLevel | None = None
    action_level: ActionLevel | None = None
    character_knowledge_coverage: dict[str, AwarenessLevel] = Field(default_factory=dict)
    evidence: list[EvidenceSpan] = Field(default_factory=list)
    extraction_notes: str = ""

    @field_validator("delta_type", mode="before")
    @classmethod
    def _normalize_delta_type(cls, value: Any) -> str:
        return normalize_delta_type(value)

    @field_validator("cognitive_level", mode="before")
    @classmethod
    def _normalize_candidate_cognitive_level(cls, value: Any) -> str | None:
        if value is None or str(value).strip() == "":
            return None
        raw = str(value or "").strip().lower()
        return raw if raw in _COGNITIVE_LEVEL_RANK else None

    @field_validator("action_level", mode="before")
    @classmethod
    def _normalize_candidate_action_level(cls, value: Any) -> str | None:
        if value is None or str(value).strip() == "":
            return None
        raw = str(value or "").strip().lower()
        return raw if raw in _ACTION_LEVEL_RANK else None

    @field_validator("cognitive_subjects", mode="before")
    @classmethod
    def _normalize_candidate_cognitive_subjects(cls, value: Any) -> list[str]:
        return CognitiveConstraint._normalize_cognitive_subjects(value)

    @field_validator("character_knowledge_coverage", mode="before")
    @classmethod
    def _normalize_candidate_character_knowledge_coverage(
        cls,
        value: Any,
    ) -> dict[str, AwarenessLevel]:
        return CognitiveConstraint._normalize_character_knowledge_coverage(value)


class AdjudicationDecision(VersionedSchema):
    """LLM verdict for one candidate state delta."""

    model_config = ConfigDict(extra="ignore")

    candidate_id: str = ""
    verdict: AdjudicationVerdict = "ambiguous"
    severity: AdjudicationSeverity = "medium"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = ""
    covered_target_ids: list[str] = Field(default_factory=list)
    coverage_status: str = ""
    issue_kind: str = ""
    repair_kind: str = "none"
    evidence_quotes: list[str] = Field(default_factory=list)
    affected_state_paths: list[str] = Field(default_factory=list)
    repair_instruction: str = ""
    pending_reason: str = ""

    @field_validator("verdict", mode="before")
    @classmethod
    def _normalize_verdict(cls, value: Any) -> str:
        return normalize_adjudication_verdict(value)

    @field_validator("severity", mode="before")
    @classmethod
    def _normalize_severity(cls, value: Any) -> str:
        return normalize_adjudication_severity(value)


class FinalStateAdjudication(VersionedSchema):
    """LLM final merge decision for all candidate verdicts in one chapter."""

    model_config = ConfigDict(extra="ignore")

    chapter_number: int = Field(ge=1)
    verdict: AdjudicationVerdict = "ambiguous"
    severity: AdjudicationSeverity = "medium"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    accepted_candidate_ids: list[str] = Field(default_factory=list)
    rejected_candidate_ids: list[str] = Field(default_factory=list)
    pending_candidate_ids: list[str] = Field(default_factory=list)
    repair_candidate_ids: list[str] = Field(default_factory=list)
    state_updates: list[dict[str, Any]] = Field(default_factory=list)
    pending_items: list[dict[str, Any]] = Field(default_factory=list)
    repair_issues: list[dict[str, Any]] = Field(default_factory=list)
    target_coverage: list[dict[str, Any]] = Field(default_factory=list)
    should_block_archive: bool = False
    summary: str = ""

    @model_validator(mode="before")
    @classmethod
    def _normalize_final_payload(cls, data: Any) -> Any:
        return normalize_final_adjudication_payload(data)

    @field_validator("severity", mode="before")
    @classmethod
    def _normalize_severity(cls, value: Any) -> str:
        return normalize_adjudication_severity(value)


class ContractCoverageItem(VersionedSchema):
    """Mechanical coverage status for one executable chapter-contract target."""

    model_config = ConfigDict(extra="ignore")

    target_id: str = ""
    field_name: str = ""
    target_index: int = Field(default=0, ge=0)
    target: str = ""
    status: str = "uncovered"
    evidence_found: bool = False
    candidate_ids: list[str] = Field(default_factory=list)
    accepted_candidate_ids: list[str] = Field(default_factory=list)
    rejected_candidate_ids: list[str] = Field(default_factory=list)
    pending_candidate_ids: list[str] = Field(default_factory=list)
    repair_candidate_ids: list[str] = Field(default_factory=list)
    evidence_quotes: list[str] = Field(default_factory=list)


class ContractCoverageReport(VersionedSchema):
    """Mechanical report of chapter-contract hard-target coverage.

    This report is diagnostic: it helps final adjudication and UI inspection,
    but state writes still come only from FinalStateAdjudication.
    """

    model_config = ConfigDict(extra="ignore")

    report_type: str = "contract_coverage"
    chapter_number: int = Field(ge=1)
    source: str = "current_chapter_text_and_adjudicated_candidates"
    total_required_targets: int = Field(default=0, ge=0)
    covered_count: int = Field(default=0, ge=0)
    evidence_found_count: int = Field(default=0, ge=0)
    all_required_covered: bool = False
    items: list[ContractCoverageItem] = Field(default_factory=list)
    uncovered_targets: list[dict[str, Any]] = Field(default_factory=list)
    unaccepted_targets: list[dict[str, Any]] = Field(default_factory=list)


class StateLedgerEntry(VersionedSchema):
    """Append-only record created from final LLM adjudication."""

    model_config = ConfigDict(extra="ignore")

    entry_id: str
    chapter_number: int = Field(ge=1)
    candidate_id: str
    delta_type: DeltaType = "other"
    summary: str = ""
    state_update: dict[str, Any] = Field(default_factory=dict)
    decision: AdjudicationDecision
    evidence: list[EvidenceSpan] = Field(default_factory=list)
    source: str = "llm_adjudication"
    source_text_hash: str = ""
    chapter_revision_id: str = ""
    supersedes_revision_id: str = ""
    depends_on_revisions: list[str] = Field(default_factory=list)
    evidence_status: EvidenceStatus = "active"

    @field_validator("delta_type", mode="before")
    @classmethod
    def _normalize_delta_type(cls, value: Any) -> str:
        return normalize_delta_type(value)


class StoryStateProjection(VersionedSchema):
    """Mechanical projection generated from accepted ledger entries."""

    model_config = ConfigDict(extra="ignore")

    entries: list[StateLedgerEntry] = Field(default_factory=list)
    accepted_updates: list[dict[str, Any]] = Field(default_factory=list)
    facts_by_path: dict[str, Any] = Field(default_factory=dict)
    by_delta_type: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    recent_summaries: list[str] = Field(default_factory=list)
    pending_items: list[dict[str, Any]] = Field(default_factory=list)
    last_chapter: int = 0


class NarrativeAdjudicationReport(VersionedSchema):
    """Persisted report for UI and downstream quality gates."""

    model_config = ConfigDict(extra="ignore")

    chapter_number: int = Field(ge=1)
    candidates: list[CandidateStateDelta] = Field(default_factory=list)
    omitted_candidates: list[CandidateStateDelta] = Field(default_factory=list)
    decisions: list[AdjudicationDecision] = Field(default_factory=list)
    final_adjudication: FinalStateAdjudication
    contract_coverage: ContractCoverageReport | None = None
    ledger_entries: list[StateLedgerEntry] = Field(default_factory=list)
    source_text_hash: str = ""
