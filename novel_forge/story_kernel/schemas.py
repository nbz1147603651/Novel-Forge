"""StoryKernel Pydantic schemas — unified field pool data models.

StoryKernel replaces canon + narrative_state as single source of truth.
It contains 10 field groups that track all narrative state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from novel_forge.core.schemas.base import VersionedSchema
from novel_forge.core.schemas.story_state import ChapterExitState, PlotThreadState
from novel_forge.core.utils.type_coerce import stringify_text_value
from novel_forge.story_kernel.field_types import (
    EntityType,
    LedgerVisibility,
    RelationType,
)

if TYPE_CHECKING:
    from novel_forge.core.schemas.story_state import CharacterState

ProjectMode = Literal["long", "short"]

# ---------------------------------------------------------------------------
# Nested models (use BaseModel to save tokens on frequently-instantiated items)
# ---------------------------------------------------------------------------


class WorldRule(VersionedSchema):
    """An immutable world rule that must never be broken."""

    model_config = ConfigDict(extra="ignore")

    rule_id: str = Field(description="Unique rule identifier.")
    content: str = Field(description="The rule text.")
    category: str = Field(
        default="general",
        description="Rule category: 'physics', 'magic', 'social', 'temporal', 'general'.",
    )
    severity: str = Field(
        default="hard",
        description="Rule severity: 'hard' (never break) or 'soft' (bendable).",
    )
    source_chapter: int = Field(
        default=0, ge=0, description="Chapter where this rule was established."
    )
    notes: str = Field(default="")
    always_on: bool = Field(
        default=False,
        description="Whether this immutable rule applies to every chapter.",
    )
    applicability_tags: list[str] = Field(default_factory=list)
    trigger_conditions: list[str] = Field(default_factory=list)
    allowed_behavior: list[str] = Field(default_factory=list)
    forbidden_behavior: list[str] = Field(default_factory=list)
    cost_or_consequence: list[str] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)
    origin: str = Field(
        default="initialization",
        description="initialization / author_approved; chapter extraction cannot mutate this field.",
    )
    rule_version: str = "1"

    @field_validator("content", "notes", mode="before")
    @classmethod
    def _coerce_text(cls, v: Any) -> str:
        return stringify_text_value(v)

    @field_validator(
        "applicability_tags",
        "trigger_conditions",
        "allowed_behavior",
        "forbidden_behavior",
        "cost_or_consequence",
        "exceptions",
        mode="before",
    )
    @classmethod
    def _coerce_rule_lists(cls, value: Any) -> list[str]:
        if value is None:
            return []
        values = value if isinstance(value, list) else [value]
        return [stringify_text_value(item).strip() for item in values if stringify_text_value(item).strip()]


class Entity(VersionedSchema):
    """A canonical entity (character, location, item, org, concept)."""

    model_config = ConfigDict(extra="ignore")

    entity_id: str = Field(description="Stable deterministic entity id.")
    name: str = Field(description="Entity display name.")
    entity_type: EntityType = Field(
        default=EntityType.CHARACTER, description="Entity type classification."
    )
    aliases: list[str] = Field(
        default_factory=list, description="Alternate names or aliases."
    )
    status: str = Field(
        default="active",
        description="Lifecycle status: 'active', 'dormant', 'retired', 'destroyed'.",
    )
    attributes: dict[str, Any] = Field(
        default_factory=dict,
        description="Flexible key-value attributes for entity-specific data.",
    )
    source_chapter: int = Field(
        default=0, ge=0, description="Chapter where entity was first introduced."
    )
    last_seen_chapter: int = Field(
        default=0, ge=0, description="Chapter where entity was last referenced."
    )
    notes: str = Field(default="")

    @field_validator("name", "notes", mode="before")
    @classmethod
    def _coerce_text(cls, v: Any) -> str:
        return stringify_text_value(v)

    @field_validator("entity_type", mode="before")
    @classmethod
    def _normalize_entity_type(cls, v: Any) -> str:
        raw = str(v or "").strip().lower()
        aliases = {
            "char": "character",
            "人物": "character",
            "角色": "character",
            "loc": "location",
            "地点": "location",
            "场景": "location",
            "object": "item",
            "prop": "item",
            "物品": "item",
            "道具": "item",
            "org": "organization",
            "组织": "organization",
            "势力": "organization",
            "idea": "concept",
            "概念": "concept",
            "主题": "concept",
        }
        return aliases.get(raw, raw or "character")

    def get_character_state_view(self) -> CharacterStateView:
        """Build a CharacterStateView from this entity's attributes dict."""
        attrs = self.attributes
        phys_raw = attrs.get("physical")
        mot_raw = attrs.get("motivation")
        know_raw = attrs.get("knowledge")
        phys: dict[str, Any] = phys_raw if isinstance(phys_raw, dict) else {}
        mot: dict[str, Any] = mot_raw if isinstance(mot_raw, dict) else {}
        know: dict[str, Any] = know_raw if isinstance(know_raw, dict) else {}
        return CharacterStateView(
            location=str(attrs.get("location", "")),
            emotional_state=str(attrs.get("emotional_state", "")),
            physical=PhysicalStateView(
                location=str(phys.get("location", "")),
                injuries=[str(i) for i in phys.get("injuries", [])],
                fatigue=str(phys.get("fatigue", "")),
                inventory=[str(i) for i in phys.get("inventory", [])],
            ),
            motivation=MotivationStateView(
                short_term_goal=str(mot.get("short_term_goal", "")),
                long_term_goal=str(mot.get("long_term_goal", "")),
                current_drive=str(mot.get("current_drive", "")),
                internal_conflict=str(mot.get("internal_conflict", "")),
            ),
            knowledge=KnowledgeStateView(
                known_facts=[str(f) for f in know.get("known_facts", [])],
                suspicions=[str(s) for s in know.get("suspicions", [])],
                misbeliefs=[str(m) for m in know.get("misbeliefs", [])],
                secrets_kept=[str(s) for s in know.get("secrets_kept", [])],
            ),
            gender=str(attrs.get("gender", "")),
            social_status=str(attrs.get("social_status", "")),
            voice=str(attrs.get("voice", "")),
        )

    def apply_character_state(self, view: CharacterStateView) -> Entity:
        """Return a new Entity with attributes updated from *view*.

        Merges view fields into the existing attributes dict, preserving
        any extra keys not covered by CharacterStateView.
        """
        new_attrs = dict(self.attributes)
        new_attrs["location"] = view.location
        new_attrs["emotional_state"] = view.emotional_state
        new_attrs["gender"] = view.gender
        new_attrs["social_status"] = view.social_status
        new_attrs["voice"] = view.voice
        new_attrs["physical"] = {
            "location": view.physical.location,
            "injuries": list(view.physical.injuries),
            "fatigue": view.physical.fatigue,
            "inventory": list(view.physical.inventory),
        }
        new_attrs["motivation"] = {
            "short_term_goal": view.motivation.short_term_goal,
            "long_term_goal": view.motivation.long_term_goal,
            "current_drive": view.motivation.current_drive,
            "internal_conflict": view.motivation.internal_conflict,
        }
        new_attrs["knowledge"] = {
            "known_facts": list(view.knowledge.known_facts),
            "suspicions": list(view.knowledge.suspicions),
            "misbeliefs": list(view.knowledge.misbeliefs),
            "secrets_kept": list(view.knowledge.secrets_kept),
        }
        return self.model_copy(update={"attributes": new_attrs})


class PhysicalStateView(BaseModel):
    """Physical state view for CharacterStateView."""

    model_config = ConfigDict(extra="ignore")

    location: str = ""
    injuries: list[str] = Field(default_factory=list)
    fatigue: str = ""
    inventory: list[str] = Field(default_factory=list)


class MotivationStateView(BaseModel):
    """Motivation state view for CharacterStateView."""

    model_config = ConfigDict(extra="ignore")

    short_term_goal: str = ""
    long_term_goal: str = ""
    current_drive: str = ""
    internal_conflict: str = ""


class KnowledgeStateView(BaseModel):
    """Knowledge state view for CharacterStateView."""

    model_config = ConfigDict(extra="ignore")

    known_facts: list[str] = Field(default_factory=list)
    suspicions: list[str] = Field(default_factory=list)
    misbeliefs: list[str] = Field(default_factory=list)
    secrets_kept: list[str] = Field(default_factory=list)


class CharacterStateView(BaseModel):
    """Typed access view into Entity.attributes for character state.

    Provides structured read/write access to character state stored in
    the flexible ``Entity.attributes`` dict.  This is an *optional*
    convenience — callers can still read/write attributes directly.
    """

    model_config = ConfigDict(extra="ignore")

    location: str = ""
    emotional_state: str = ""
    physical: PhysicalStateView = Field(default_factory=PhysicalStateView)
    motivation: MotivationStateView = Field(default_factory=MotivationStateView)
    knowledge: KnowledgeStateView = Field(default_factory=KnowledgeStateView)
    gender: str = ""
    social_status: str = ""
    voice: str = ""


class Relationship(VersionedSchema):
    """A typed relationship between two entities."""

    model_config = ConfigDict(extra="ignore")

    relationship_id: str = Field(description="Unique relationship identifier.")
    source_entity_id: str = Field(description="Entity ID of the relationship initiator.")
    target_entity_id: str = Field(description="Entity ID of the relationship target.")
    relation_type: RelationType = Field(
        default=RelationType.ACQUAINTANCE,
        description="Type of relationship.",
    )
    label: str = Field(
        default="", description="Human-readable label, e.g. '师徒', '宿敌'."
    )
    trust: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Trust level 0.0-1.0."
    )
    tension: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Tension level 0.0-1.0."
    )
    dependency: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Mutual dependency level 0.0-1.0.",
    )
    status: str = Field(
        default="active",
        description="Relationship status: 'active', 'strained', 'dormant', 'severed'.",
    )
    established_chapter: int = Field(
        default=0, ge=0, description="Chapter where relationship was established."
    )
    last_shift_chapter: int = Field(
        default=0, ge=0, description="Chapter where relationship last changed."
    )
    shift_summary: str = Field(
        default="", description="Summary of the last significant change."
    )
    notes: str = Field(default="")

    @field_validator("label", "shift_summary", "notes", mode="before")
    @classmethod
    def _coerce_text(cls, v: Any) -> str:
        return stringify_text_value(v)

    @field_validator("relation_type", mode="before")
    @classmethod
    def _normalize_relation_type(cls, v: Any) -> str:
        raw = str(v or "").strip().lower()
        aliases = {
            "family": "family",
            "家人": "family",
            "亲属": "family",
            "parent": "family",
            "sibling": "family",
            "romantic": "romantic",
            "恋人": "romantic",
            "爱情": "romantic",
            "couple": "romantic",
            "mentor_student": "mentor_student",
            "师徒": "mentor_student",
            "师生": "mentor_student",
            "master_apprentice": "mentor_student",
            "business": "business",
            "商业": "business",
            "合作": "business",
            "ally": "ally",
            "盟友": "ally",
            "enemy": "enemy",
            "敌人": "enemy",
            "对手": "enemy",
            "rival": "rival",
            "竞争": "rival",
            "subordinate": "subordinate",
            "下属": "subordinate",
            "上级": "subordinate",
            "friend": "friend",
            "朋友": "friend",
            "acquaintance": "acquaintance",
            "熟人": "acquaintance",
        }
        result = aliases.get(raw)
        if result is not None:
            return result
        # LLM produced a descriptive label not matching any known alias.
        # Default to 'acquaintance' so the local code never crashes on enum
        # validation — the LLM adjudication step is responsible for semantic
        # mapping of relationship types.
        import logging

        _logger = logging.getLogger(__name__)
        _logger.warning(
            "Unknown relation_type value %r, defaulting to %r",
            raw[:120],
            RelationType.ACQUAINTANCE.value,
        )
        return RelationType.ACQUAINTANCE.value


class TimelineAnchor(VersionedSchema):
    """A discrete event anchored on the story timeline."""

    model_config = ConfigDict(extra="ignore")

    anchor_id: str = Field(description="Unique anchor identifier.")
    chapter: int = Field(ge=1, description="Chapter number where event occurred.")
    event: str = Field(description="What happened.")
    in_story_time: str = Field(
        default="",
        description="In-story time reference, e.g. 'Day 3, dusk', '第三天黄昏'.",
    )
    characters_involved: list[str] = Field(
        default_factory=list, description="Entity IDs of characters involved."
    )
    location: str = Field(default="", description="Location where event occurred.")
    significance: str = Field(
        default="minor",
        description="Event significance: 'major', 'minor', 'background'.",
    )
    tags: list[str] = Field(default_factory=list, description="Free-form tags.")

    @field_validator("event", "in_story_time", "location", mode="before")
    @classmethod
    def _coerce_text(cls, v: Any) -> str:
        return stringify_text_value(v)


class ObjectLedger(VersionedSchema):
    """Tracks items, props, and their ownership/state."""

    model_config = ConfigDict(extra="ignore")

    entry_id: str = Field(description="Unique ledger entry identifier.")
    item_name: str = Field(description="Name of the item or prop.")
    item_entity_id: str = Field(
        default="", description="Entity ID if item is registered as an entity."
    )
    owner_entity_id: str = Field(
        default="", description="Entity ID of current owner."
    )
    location: str = Field(
        default="", description="Current location if not carried by an entity."
    )
    state: str = Field(
        default="intact",
        description="Item state: 'intact', 'damaged', 'lost', 'destroyed', 'consumed'.",
    )
    visibility: LedgerVisibility = Field(
        default=LedgerVisibility.PUBLIC,
        description="Visibility level of this entry.",
    )
    introduced_chapter: int = Field(
        default=0, ge=0, description="Chapter where item was first introduced."
    )
    last_seen_chapter: int = Field(
        default=0, ge=0, description="Chapter where item was last referenced."
    )
    description: str = Field(default="", description="Physical description of the item.")
    notes: str = Field(default="")

    @field_validator("item_name", "location", "description", "notes", mode="before")
    @classmethod
    def _coerce_text(cls, v: Any) -> str:
        return stringify_text_value(v)


class KnowledgeLedger(VersionedSchema):
    """Tracks what entities know, suspect, or hide."""

    model_config = ConfigDict(extra="ignore")

    entry_id: str = Field(description="Unique ledger entry identifier.")
    entity_id: str = Field(description="Entity ID of the knower.")
    fact: str = Field(description="The known/suspected/hidden fact.")
    knowledge_type: str = Field(
        default="known",
        description="Type: 'known', 'suspected', 'misbelief', 'secret_kept'.",
    )
    source_chapter: int = Field(
        default=0, ge=0, description="Chapter where knowledge was acquired."
    )
    revealed_in_chapter: int = Field(
        default=0, ge=0, description="Chapter where knowledge was revealed (0 = not yet)."
    )
    visibility: LedgerVisibility = Field(
        default=LedgerVisibility.PRIVATE,
        description="Visibility level of this entry.",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence level: 1.0 = certain, 0.0 = pure speculation.",
    )
    notes: str = Field(default="")

    @field_validator("fact", "notes", mode="before")
    @classmethod
    def _coerce_text(cls, v: Any) -> str:
        return stringify_text_value(v)

    @field_validator("knowledge_type", mode="before")
    @classmethod
    def _normalize_knowledge_type(cls, v: Any) -> str:
        raw = str(v or "").strip().lower()
        aliases = {
            "known": "known",
            "已知": "known",
            "事实": "known",
            "suspected": "suspected",
            "怀疑": "suspected",
            "misbelief": "misbelief",
            "误解": "misbelief",
            "错误认知": "misbelief",
            "secret": "secret_kept",
            "secret_kept": "secret_kept",
            "秘密": "secret_kept",
            "隐瞒": "secret_kept",
        }
        return aliases.get(raw, raw or "known")


class AccessLedger(VersionedSchema):
    """Tracks entity access to locations, information, or resources."""

    model_config = ConfigDict(extra="ignore")

    entry_id: str = Field(description="Unique ledger entry identifier.")
    entity_id: str = Field(description="Entity ID of the accessor.")
    target: str = Field(
        description="What is being accessed: location, information, or resource."
    )
    access_type: str = Field(
        default="allowed",
        description="Access type: 'allowed', 'denied', 'restricted', 'conditional'.",
    )
    condition: str = Field(
        default="",
        description="Condition for conditional access.",
    )
    granted_chapter: int = Field(
        default=0, ge=0, description="Chapter where access was granted/denied."
    )
    revoked_chapter: int = Field(
        default=0, ge=0, description="Chapter where access was revoked (0 = still active)."
    )
    visibility: LedgerVisibility = Field(
        default=LedgerVisibility.PUBLIC,
        description="Visibility level of this entry.",
    )
    notes: str = Field(default="")

    @field_validator("target", "condition", "notes", mode="before")
    @classmethod
    def _coerce_text(cls, v: Any) -> str:
        return stringify_text_value(v)


PromiseStatus = Literal["planted", "hinted", "partially_paid", "paid", "broken"]


class PromiseLedger(VersionedSchema):
    """Tracks foreshadowing, promises, and payoffs."""

    model_config = ConfigDict(extra="ignore")

    entry_id: str = Field(description="Unique ledger entry identifier.")
    description: str = Field(description="What is being foreshadowed or promised.")
    promise_type: str = Field(
        default="foreshadow",
        description="Type: 'foreshadow', 'promise', 'suspense', 'payoff'.",
    )
    planted_chapter: int = Field(
        default=0, ge=0, description="Chapter where promise was planted."
    )
    status: PromiseStatus = Field(
        default="planted",
        description="Status: 'planted', 'hinted', 'partially_paid', 'paid', 'broken'.",
    )
    payoff_chapter: int = Field(
        default=0, ge=0, description="Chapter where promise was paid off (0 = pending)."
    )
    owner_entity_ids: list[str] = Field(
        default_factory=list, description="Entity IDs of promise owners."
    )
    depends_on: list[str] = Field(
        default_factory=list, description="Entry IDs this promise depends on."
    )
    visibility: LedgerVisibility = Field(
        default=LedgerVisibility.PUBLIC,
        description="Visibility level of this entry.",
    )
    notes: str = Field(default="")

    @field_validator("description", "notes", mode="before")
    @classmethod
    def _coerce_text(cls, v: Any) -> str:
        return stringify_text_value(v)

    @field_validator("promise_type", mode="before")
    @classmethod
    def _normalize_promise_type(cls, v: Any) -> str:
        raw = str(v or "").strip().lower()
        aliases = {
            "foreshadow": "foreshadow",
            "foreshadowing": "foreshadow",
            "伏笔": "foreshadow",
            "promise": "promise",
            "承诺": "promise",
            "suspense": "suspense",
            "悬念": "suspense",
            "payoff": "payoff",
            "兑现": "payoff",
            "回收": "payoff",
        }
        return aliases.get(raw, raw or "foreshadow")

    @field_validator("status", mode="before")
    @classmethod
    def _normalize_status(cls, v: Any) -> str:
        raw = str(v or "").strip().lower()
        aliases = {
            "planted": "planted",
            "埋下": "planted",
            "hinted": "hinted",
            "暗示": "hinted",
            "partial": "partially_paid",
            "partially_paid": "partially_paid",
            "部分兑现": "partially_paid",
            "paid": "paid",
            "resolved": "paid",
            "兑现": "paid",
            "回收": "paid",
            "broken": "broken",
            "违背": "broken",
        }
        return aliases.get(raw, raw or "planted")


class MotifProtocol(VersionedSchema):
    """Tracks recurring motifs, themes, and symbolic patterns."""

    model_config = ConfigDict(extra="ignore")

    entry_id: str = Field(description="Unique protocol entry identifier.")
    motif_name: str = Field(description="Name of the motif or symbolic pattern.")
    description: str = Field(default="", description="Description of the motif.")
    motif_type: str = Field(
        default="symbol",
        description="Type: 'symbol', 'phrase', 'scene_pattern', 'character_trait', 'theme'.",
    )
    occurrences: list[int] = Field(
        default_factory=list,
        description="Chapter numbers where motif has appeared.",
    )
    cooldown_chapters: int = Field(
        default=3, ge=0, description="Minimum chapters between repetitions."
    )
    last_used_chapter: int = Field(
        default=0, ge=0, description="Chapter where motif was last used."
    )
    intensity: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Current intensity: 0.0 = subtle, 1.0 = dominant.",
    )
    channel: str = Field(
        default="other",
        description="Expression channel: 'somatic_reaction', 'action_tag', 'sentence_pattern', 'sensory_anchor', 'metaphor_image', 'dialogue_tag', 'emotional_beat', 'other'.",
    )
    examples: list[str] = Field(
        default_factory=list, description="Example usages of the motif."
    )
    notes: str = Field(default="")

    @field_validator(
        "motif_name", "description", "notes", mode="before"
    )
    @classmethod
    def _coerce_text(cls, v: Any) -> str:
        return stringify_text_value(v)


class BusinessDependency(VersionedSchema):
    """Tracks business logic dependencies between narrative elements."""

    model_config = ConfigDict(extra="ignore")

    dependency_id: str = Field(description="Unique dependency identifier.")
    source_id: str = Field(
        description="ID of the source element (entity, rule, promise, etc.)."
    )
    target_id: str = Field(
        description="ID of the target element that depends on source."
    )
    dependency_type: str = Field(
        default="requires",
        description="Type: 'requires', 'blocks', 'triggers', 'inhibits', 'enables'.",
    )
    description: str = Field(
        default="", description="Human-readable description of the dependency."
    )
    condition: str = Field(
        default="",
        description="Condition under which dependency is active.",
    )
    established_chapter: int = Field(
        default=0, ge=0, description="Chapter where dependency was established."
    )
    is_active: bool = Field(
        default=True, description="Whether this dependency is currently active."
    )
    notes: str = Field(default="")

    @field_validator("description", "condition", "notes", mode="before")
    @classmethod
    def _coerce_text(cls, v: Any) -> str:
        return stringify_text_value(v)


class StoryKernelStructuredWarning(VersionedSchema):
    """Structured warning produced while maintaining StoryKernel state."""

    model_config = ConfigDict(extra="ignore")

    warning_type: str = Field(description="Stable warning category.")
    source: str = Field(default="", description="Writer or subsystem that produced it.")
    entity_id: str = Field(default="", description="Related entity id, if any.")
    details: dict[str, Any] = Field(default_factory=dict)
    chapter_number: int = Field(default=0, ge=0)
    work_unit: str = Field(default="")

    @field_validator("warning_type", "source", "entity_id", "work_unit", mode="before")
    @classmethod
    def _coerce_text(cls, v: Any) -> str:
        return stringify_text_value(v)



class StoryKernel(VersionedSchema):
    """Unified field pool — single source of truth for all narrative state.

    Replaces the combined canon + narrative_state system.
    Contains 10 field groups that track all narrative state.
    """

    _correct_fields: ClassVar[set[str]] = {
        "project_id",
        "project_mode",
        "current_chapter",
        "active_volume",
        "title",
        "premise",
        "world_rules",
        "entities",
        "relationships",
        "timeline",
        "object_ledger",
        "knowledge_ledger",
        "access_ledger",
        "promise_ledger",
        "motif_protocols",
        "business_dependencies",
        "chapter_summaries",
        "banned_phrases",
        "notes",
        "pending_world_facts",
        "archived_entities",
        "archived_items",
        "archived_world_facts",
        "archived_promises",
        "archived_timeline",
        "chapter_exit_states",
        "plot_threads",
        "artifact_refs",
        "structured_warnings",
    }
    _field_aliases: ClassVar[dict[str, str]] = {
        **VersionedSchema._field_aliases,
        "worldRules": "world_rules",
        "world_rules_list": "world_rules",
        "entities_list": "entities",
        "relationships_list": "relationships",
        "timeline_events": "timeline",
        "timeline_anchors": "timeline",
        "objects": "object_ledger",
        "items": "object_ledger",
        "knowledge": "knowledge_ledger",
        "access": "access_ledger",
        "promises": "promise_ledger",
        "foreshadowing": "promise_ledger",
        "motifs": "motif_protocols",
        "motif_list": "motif_protocols",
        "dependencies": "business_dependencies",
        "business_deps": "business_dependencies",
        "summaries": "chapter_summaries",
        "archived_characters": "archived_entities",
        "archived_foreshadowing": "archived_promises",
    }

    model_config = ConfigDict(extra="forbid")

    # --- Project metadata ---
    project_id: str = Field(description="Unique project identifier.")
    project_mode: ProjectMode = Field(
        default="long",
        description="Project mode: long chapter flow or short work-unit flow.",
    )
    current_chapter: int = Field(
        default=0, ge=0, description="Current chapter number."
    )
    active_volume: int = Field(
        default=1, ge=1, description="Current active volume number."
    )
    title: str = Field(default="", description="Story working title.")
    premise: str = Field(default="", description="Core premise in 2-3 sentences.")

    # --- Field Group 1: World Rules ---
    world_rules: list[WorldRule] = Field(
        default_factory=list,
        description="Immutable world rules that must never be broken.",
    )

    # --- Field Group 2: Entities ---
    entities: list[Entity] = Field(
        default_factory=list,
        description="All canonical entities (characters, locations, items, orgs, concepts).",
    )

    # --- Field Group 3: Relationships ---
    relationships: list[Relationship] = Field(
        default_factory=list,
        description="Typed relationships between entities.",
    )

    # --- Field Group 4: Timeline ---
    timeline: list[TimelineAnchor] = Field(
        default_factory=list,
        description="Chronological timeline of story events.",
    )

    # --- Field Group 5: Object Ledger ---
    object_ledger: list[ObjectLedger] = Field(
        default_factory=list,
        description="Tracks items, props, and their ownership/state.",
    )

    # --- Field Group 6: Knowledge Ledger ---
    knowledge_ledger: list[KnowledgeLedger] = Field(
        default_factory=list,
        description="Tracks what entities know, suspect, or hide.",
    )

    # --- Field Group 7: Access Ledger ---
    access_ledger: list[AccessLedger] = Field(
        default_factory=list,
        description="Tracks entity access to locations, information, or resources.",
    )

    # --- Field Group 8: Promise Ledger ---
    promise_ledger: list[PromiseLedger] = Field(
        default_factory=list,
        description="Tracks foreshadowing, promises, and payoffs.",
    )

    # --- Field Group 9: Motif Protocols ---
    motif_protocols: list[MotifProtocol] = Field(
        default_factory=list,
        description="Tracks recurring motifs, themes, and symbolic patterns.",
    )

    # --- Field Group 10: Business Dependencies ---
    business_dependencies: list[BusinessDependency] = Field(
        default_factory=list,
        description="Tracks business logic dependencies between narrative elements.",
    )

    # --- Supplementary fields ---
    chapter_summaries: dict[int, str] = Field(
        default_factory=dict,
        description="chapter_number → lightweight chapter summary.",
    )
    banned_phrases: list[str] = Field(
        default_factory=list,
        description="Banned phrases accumulated from issues and motif tracking.",
    )
    notes: str = Field(default="", description="Free-form project notes.")
    pending_world_facts: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Chapter-extracted world facts awaiting explicit adjudication. "
            "They must not silently become immutable world_rules."
        ),
    )

    # --- Archive fields ---
    archived_entities: list[Entity] = Field(
        default_factory=list,
        description="Archived entities removed from active tracking.",
    )
    archived_items: list[str] = Field(
        default_factory=list,
        description="Names of archived items.",
    )
    archived_world_facts: dict[str, str] = Field(
        default_factory=dict,
        description="Archived world facts (key → value).",
    )
    archived_promises: list[PromiseLedger] = Field(
        default_factory=list,
        description="Archived promise/foreshadowing entries.",
    )
    archived_timeline: list[TimelineAnchor] = Field(
        default_factory=list,
        description="Archived timeline anchors from completed volumes.",
    )

    # --- Chapter exit states & plot threads ---
    chapter_exit_states: dict[int, ChapterExitState] = Field(
        default_factory=dict,
        description="chapter_number → structured exit state snapshot.",
    )
    plot_threads: list[PlotThreadState] = Field(
        default_factory=list,
        description="Long-running plot thread states.",
    )
    artifact_refs: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Lightweight artifact references keyed by logical artifact name.",
    )
    structured_warnings: list[StoryKernelStructuredWarning] = Field(
        default_factory=list,
        description="Non-fatal structured warnings emitted by StoryKernel writers.",
    )

    @field_validator("banned_phrases")
    @classmethod
    def _truncate_banned_phrases(cls, v: list[str]) -> list[str]:
        phrases = [p[:50] for p in v[:30]]
        return phrases

    @property
    def characters(self) -> dict[str, "CharacterState"]:
        return self.get_all_characters()

    @property
    def archived_characters(self) -> dict[str, str]:
        return {e.name: e.name for e in self.archived_entities if e.entity_type == EntityType.CHARACTER}

    @property
    def foreshadowing(self) -> list["PromiseLedger"]:
        return [p for p in self.promise_ledger if p.planted_chapter > 0]

    @foreshadowing.setter
    def foreshadowing(self, value: list["PromiseLedger"]) -> None:
        self.promise_ledger = list(value)

    @property
    def archived_foreshadowing(self) -> list["PromiseLedger"]:
        return self.archived_promises

    @property
    def world_facts(self) -> dict[str, str]:
        return self.get_all_world_rules()

    @field_validator("title", "premise", "notes", mode="before")
    @classmethod
    def _coerce_text(cls, v: Any) -> str:
        return stringify_text_value(v)

    def get_entity_by_id(self, entity_id: str) -> Entity | None:
        """Look up an entity by its ID."""
        for entity in self.entities:
            if entity.entity_id == entity_id:
                return entity
        return None

    def get_relationships_for_entity(self, entity_id: str) -> list[Relationship]:
        """Get all relationships involving a given entity."""
        return [
            r
            for r in self.relationships
            if r.source_entity_id == entity_id or r.target_entity_id == entity_id
        ]

    def get_timeline_for_chapter(self, chapter: int) -> list[TimelineAnchor]:
        """Get all timeline anchors for a given chapter."""
        return [t for t in self.timeline if t.chapter == chapter]

    def get_promises_by_status(self, status: PromiseStatus) -> list[PromiseLedger]:
        """Get all promises with a given status."""
        return [p for p in self.promise_ledger if p.status == status]

    def get_active_motifs(self, current_chapter: int) -> list[MotifProtocol]:
        """Get motifs that are off cooldown and can be used."""
        return [
            m
            for m in self.motif_protocols
            if current_chapter - m.last_used_chapter >= m.cooldown_chapters
        ]

    def get_entity_by_name(self, name: str) -> Entity | None:
        """O(1) entity lookup by name using a lazy-built index cache."""
        index = self.__dict__.get("_name_index")
        if index is None:
            index = {e.name: e for e in self.entities}
            object.__setattr__(self, "_name_index", index)
        return index.get(name)

    def get_entities_by_type(self, entity_type: EntityType) -> list[Entity]:
        """Return all active entities matching *entity_type*."""
        return [e for e in self.entities if e.entity_type == entity_type]

    def archive_entity(self, entity_id: str) -> None:
        """Move an entity from active list to archived_entities."""
        remaining: list[Entity] = []
        found: Entity | None = None
        for e in self.entities:
            if e.entity_id == entity_id:
                found = e
            else:
                remaining.append(e)
        if found is None:
            return
        self.entities = remaining
        self.archived_entities.append(found)
        object.__setattr__(self, "_name_index", None)

    def restore_entity(self, entity_id: str) -> None:
        """Move an entity from archived_entities back to active list."""
        remaining: list[Entity] = []
        found: Entity | None = None
        for e in self.archived_entities:
            if e.entity_id == entity_id:
                found = e
            else:
                remaining.append(e)
        if found is None:
            return
        self.archived_entities = remaining
        self.entities.append(found)
        object.__setattr__(self, "_name_index", None)

    def get_plot_thread_by_id(self, thread_id: str) -> PlotThreadState | None:
        """Look up a plot thread by its thread_id."""
        for thread in self.plot_threads:
            if thread.thread_id == thread_id:
                return thread
        return None

    # --- Character helpers (replaces _CharactersDict wrapper) ---

    def get_character_by_name(self, name: str) -> "CharacterState | None":
        """Look up a character by name, returning CharacterState for pipeline compatibility.

        Converts from Entity storage to CharacterState view.
        """
        from novel_forge.core.schemas.story_state import CharacterState

        for e in self.entities:
            if e.entity_type == EntityType.CHARACTER and e.name == name:
                return CharacterState(
                    name=e.name,
                    gender=e.attributes.get("gender", ""),
                    social_status=e.attributes.get("social_status", ""),
                    voice=e.attributes.get("voice", ""),
                    notes=e.attributes.get("notes", ""),
                )
        return None

    def get_all_characters(self) -> dict[str, "CharacterState"]:
        """Return all active characters as name→CharacterState dict.

        Replaces ``kernel.characters.items()`` / ``kernel.characters.keys()`` patterns.
        """
        from novel_forge.core.schemas.story_state import CharacterState

        result: dict[str, CharacterState] = {}
        for e in self.entities:
            if e.entity_type == EntityType.CHARACTER:
                result[e.name] = CharacterState(
                    name=e.name,
                    gender=e.attributes.get("gender", ""),
                    social_status=e.attributes.get("social_status", ""),
                    voice=e.attributes.get("voice", ""),
                    notes=e.attributes.get("notes", ""),
                )
        return result

    def set_character(self, name: str, char_state: "CharacterState") -> None:
        """Set or update a character in the entities list from a CharacterState.

        Replaces ``kernel.characters[name] = char_state`` pattern.
        """
        existing = None
        for e in self.entities:
            if e.entity_type == EntityType.CHARACTER and e.name == name:
                existing = e
                break
        if existing is not None:
            attrs = dict(existing.attributes)
            if char_state.gender:
                attrs["gender"] = char_state.gender
            if char_state.social_status:
                attrs["social_status"] = char_state.social_status
            if char_state.voice:
                attrs["voice"] = char_state.voice
            if char_state.notes:
                attrs["notes"] = char_state.notes
            existing.attributes = attrs
        else:
            new_attrs: dict[str, Any] = {}
            if char_state.gender:
                new_attrs["gender"] = char_state.gender
            if char_state.social_status:
                new_attrs["social_status"] = char_state.social_status
            if char_state.voice:
                new_attrs["voice"] = char_state.voice
            if char_state.notes:
                new_attrs["notes"] = char_state.notes
            self.entities.append(
                Entity(
                    entity_id=f"char_{name}",
                    name=name,
                    entity_type=EntityType.CHARACTER,
                    attributes=new_attrs,
                )
            )
        object.__setattr__(self, "_name_index", None)

    def remove_character_by_name(self, name: str) -> None:
        """Remove a character entity from the active list by name.

        Does NOT archive — use ``archive_character_by_name()`` for archival.
        """
        self.entities = [
            e for e in self.entities
            if not (e.entity_type == EntityType.CHARACTER and e.name == name)
        ]
        object.__setattr__(self, "_name_index", None)

    def archive_character_by_name(self, name: str) -> None:
        """Move a character from active entities to archived_entities by name.

        Replaces the ``_CharactersDict.pop()`` → ``archived_characters[name] = char`` pattern.
        """
        remaining: list[Entity] = []
        found: Entity | None = None
        for e in self.entities:
            if e.entity_type == EntityType.CHARACTER and e.name == name:
                found = e
            else:
                remaining.append(e)
        if found is None:
            return
        self.entities = remaining
        self.archived_entities.append(found)
        object.__setattr__(self, "_name_index", None)

    # --- World rule helpers (replaces _WorldFactsDict wrapper) ---

    def get_world_rule_by_id(self, rule_id: str) -> str | None:
        """Look up a world rule's content by rule_id."""
        for r in self.world_rules:
            if r.rule_id == rule_id:
                return r.content
        return None

    def get_all_world_rules(self) -> dict[str, str]:
        """Return all world rules as rule_id→content dict.

        Replaces ``kernel.world_facts.items()`` / ``kernel.world_facts.keys()`` patterns.
        """
        return {r.rule_id: r.content for r in self.world_rules}

    def set_world_rule(self, rule_id: str, content: str) -> None:
        """Set or update a world rule.

        Replaces ``kernel.world_facts[key] = value`` pattern.
        """
        for r in self.world_rules:
            if r.rule_id == rule_id:
                r.content = content[:500]
                return
        self.world_rules.append(WorldRule(rule_id=rule_id, content=content[:500]))

    def archive_world_rule_by_id(self, rule_id: str) -> None:
        """Move a world rule from active to archived_world_facts by rule_id.

        Replaces the ``world_facts.pop(key)`` → ``archived_world_facts[key] = value`` pattern.
        """
        remaining: list[WorldRule] = []
        found_content: str | None = None
        for r in self.world_rules:
            if r.rule_id == rule_id:
                found_content = r.content
            else:
                remaining.append(r)
        if found_content is None:
            return
        self.world_rules = remaining
        self.archived_world_facts[rule_id] = found_content

    def set_all_world_rules(self, rules: dict[str, str]) -> None:
        """Replace all active world rules from a dict.

        Replaces ``kernel.world_facts = kept_world`` pattern.
        """
        self.world_rules = [
            WorldRule(rule_id=k, content=v[:500]) for k, v in rules.items()
        ]

    def get_character_count(self) -> int:
        """Return count of active character entities."""
        return sum(1 for e in self.entities if e.entity_type == EntityType.CHARACTER)


__all__ = [
    "AccessLedger",
    "BusinessDependency",
    "CharacterStateView",
    "Entity",
    "KnowledgeLedger",
    "KnowledgeStateView",
    "MotifProtocol",
    "MotivationStateView",
    "ObjectLedger",
    "PhysicalStateView",
    "PromiseLedger",
    "PromiseStatus",
    "Relationship",
    "StoryKernel",
    "TimelineAnchor",
    "WorldRule",
]


# ---------------------------------------------------------------------------
# Resolve forward references for cross-module type annotations.
# ---------------------------------------------------------------------------
StoryKernel.model_rebuild()

# ChapterOutcome and related models in core.schemas.chapter reference
# TimelineAnchor and PromiseLedger from THIS module. Due to circular
# import restrictions they cannot import us at top level. We resolve
# their forward refs here after our types are fully defined.
try:
    from novel_forge.core.schemas.chapter import (
        CausalValidationReport,
        ChapterOutcome,
        ChapterRepairReport,
        ChapterResult,
    )

    _chapter_ns = {
        "PromiseLedger": PromiseLedger,
        "TimelineAnchor": TimelineAnchor,
    }
    ChapterOutcome.model_rebuild(_types_namespace=_chapter_ns)
    ChapterResult.model_rebuild(_types_namespace=_chapter_ns)
    ChapterRepairReport.model_rebuild(_types_namespace=_chapter_ns)
    CausalValidationReport.model_rebuild(_types_namespace=_chapter_ns)
except ImportError:
    pass
