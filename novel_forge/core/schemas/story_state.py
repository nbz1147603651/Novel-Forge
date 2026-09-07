"""Structured long-form story state models for Novel Forge v2."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from novel_forge.core.schemas.base import VersionedSchema
from novel_forge.core.utils.type_coerce import stringify_text_value


class PhysicalState(VersionedSchema):
    """Concrete physical state for a character."""

    location: str = Field(default="")
    injuries: list[str] = Field(default_factory=list)
    fatigue: str = Field(default="")
    inventory: list[str] = Field(default_factory=list)

    @field_validator("fatigue", mode="before")
    @classmethod
    def _coerce_fatigue(cls, v: Any) -> str:
        return stringify_text_value(v)

    @field_validator("injuries", mode="before")
    @classmethod
    def _coerce_injuries(cls, v: Any) -> list[str]:
        if isinstance(v, list):
            return [stringify_text_value(item) for item in v]
        if isinstance(v, str):
            return [v]
        return []


class EmotionalState(VersionedSchema):
    """Emotional snapshot for a character."""

    primary_emotion: str = Field(default="")
    secondary_emotion: str = Field(default="")
    stability: float = Field(default=0.5, ge=0.0, le=1.0)
    desire: str = Field(default="")
    fear: str = Field(default="")

    @field_validator("desire", "fear", mode="before")
    @classmethod
    def _coerce_str_fields(cls, v: Any) -> str:
        return stringify_text_value(v)


class MotivationState(VersionedSchema):
    """Goal and motivation state for a character."""

    short_term_goal: str = Field(default="")
    long_term_goal: str = Field(default="")
    current_drive: str = Field(default="")
    internal_conflict: str = Field(default="")

    @field_validator("internal_conflict", mode="before")
    @classmethod
    def _coerce_internal_conflict(cls, v: Any) -> str:
        return stringify_text_value(v)


class KnowledgeState(VersionedSchema):
    """What a character knows, suspects, or hides."""

    known_facts: list[str] = Field(default_factory=list)
    suspicions: list[str] = Field(default_factory=list)
    misbeliefs: list[str] = Field(default_factory=list)
    secrets_kept: list[str] = Field(default_factory=list)

    @field_validator("known_facts", "suspicions", "misbeliefs", "secrets_kept", mode="before")
    @classmethod
    def _coerce_str_list_fields(cls, v: Any) -> list[str]:
        if isinstance(v, list):
            return [stringify_text_value(item) for item in v]
        if isinstance(v, str):
            return [v]
        return []


class CharacterState(VersionedSchema):
    """Snapshot of a character's current state at a given chapter."""

    name: str
    alive: bool = True
    gender: str = Field(default="", description="Character gender: '男' or '女'. Sourced from CharacterBible and preserved across chapters.")
    social_status: str = Field(default="", description="Current social title/rank/position, e.g. '六品翰林', '镇北将军'. Updated when promotions/demotions occur.")
    physical: PhysicalState = Field(default_factory=PhysicalState)
    emotional: EmotionalState = Field(default_factory=EmotionalState)
    motivation: MotivationState = Field(default_factory=MotivationState)
    knowledge_state: KnowledgeState = Field(default_factory=KnowledgeState)
    voice: str = Field(default="", description="角色声纹特征，从 CharacterBible 同步。")
    notes: str = ""
    last_seen_chapter: int = Field(default=0, ge=0)
    last_change_reason: str = Field(default="")

    @model_validator(mode="before")
    @classmethod
    def _upgrade_flat_shape(cls, data: Any) -> Any:
        """Allow legacy flat state payloads to hydrate the v2 nested model."""
        if not isinstance(data, dict):
            return data

        payload = dict(data)
        physical = payload.get("physical")
        if not isinstance(physical, dict):
            physical = {}
        emotional = payload.get("emotional")
        if not isinstance(emotional, dict):
            emotional = {}
        motivation = payload.get("motivation")
        if not isinstance(motivation, dict):
            motivation = {}
        knowledge_state = payload.get("knowledge_state")
        if not isinstance(knowledge_state, dict):
            knowledge_state = {}

        location = payload.pop("location", None)
        if location and "location" not in physical:
            physical["location"] = str(location)
        inventory = payload.pop("inventory", None)
        if isinstance(inventory, list) and "inventory" not in physical:
            physical["inventory"] = inventory
        fatigue = payload.pop("fatigue", None)
        if fatigue and "fatigue" not in physical:
            physical["fatigue"] = str(fatigue)
        injuries = payload.pop("injuries", None)
        if isinstance(injuries, list) and "injuries" not in physical:
            physical["injuries"] = injuries

        emotional_state = payload.pop("emotional_state", None)
        if emotional_state and "primary_emotion" not in emotional:
            emotional["primary_emotion"] = str(emotional_state)

        knowledge = payload.pop("knowledge", None)
        if isinstance(knowledge, list) and "known_facts" not in knowledge_state:
            knowledge_state["known_facts"] = knowledge

        payload["physical"] = physical
        payload["emotional"] = emotional
        payload["motivation"] = motivation
        payload["knowledge_state"] = knowledge_state
        return payload

    @classmethod
    def _expand_legacy_update(cls, update: dict[str, Any]) -> dict[str, Any]:
        """Map legacy flat update keys to the nested v2 shape."""
        payload = dict(update)

        physical = payload.get("physical")
        if not isinstance(physical, dict):
            physical = {}
        emotional = payload.get("emotional")
        if not isinstance(emotional, dict):
            emotional = {}
        knowledge_state = payload.get("knowledge_state")
        if not isinstance(knowledge_state, dict):
            knowledge_state = {}

        if "location" in payload and "location" not in physical:
            physical["location"] = str(payload.pop("location") or "")
        if "inventory" in payload and "inventory" not in physical:
            inventory = payload.pop("inventory")
            if isinstance(inventory, list):
                physical["inventory"] = inventory
            elif isinstance(inventory, str):
                physical["inventory"] = [inventory] if inventory.strip() else []
            else:
                physical["inventory"] = [str(inventory)] if inventory is not None else []
        if "injuries" in payload and "injuries" not in physical:
            injuries = payload.pop("injuries")
            if isinstance(injuries, list):
                physical["injuries"] = injuries
            elif isinstance(injuries, str):
                physical["injuries"] = [injuries] if injuries.strip() else []
            else:
                physical["injuries"] = [str(injuries)] if injuries is not None else []
        if "fatigue" in payload and "fatigue" not in physical:
            physical["fatigue"] = str(payload.pop("fatigue") or "")

        if "emotional_state" in payload and "primary_emotion" not in emotional:
            emotional["primary_emotion"] = str(payload.pop("emotional_state") or "")
        if "knowledge" in payload and "known_facts" not in knowledge_state:
            knowledge = payload.pop("knowledge")
            if isinstance(knowledge, list):
                knowledge_state["known_facts"] = knowledge
            elif isinstance(knowledge, str):
                knowledge_state["known_facts"] = [knowledge] if knowledge.strip() else []
            else:
                knowledge_state["known_facts"] = [str(knowledge)] if knowledge is not None else []

        payload["physical"] = physical
        payload["emotional"] = emotional
        payload["knowledge_state"] = knowledge_state
        return payload

    def model_copy(  # type: ignore[override]
        self,
        *,
        update: dict[str, Any] | None = None,
        deep: bool = False,
    ) -> CharacterState:
        """Preserve compatibility for legacy flat update fields."""
        if not isinstance(update, dict):
            return super().model_copy(update=update, deep=deep)

        merged_update = self._expand_legacy_update(update)
        payload = self.model_dump(mode="json")
        for key, value in merged_update.items():
            if isinstance(value, dict) and isinstance(payload.get(key), dict):
                nested = dict(payload.get(key, {}))
                nested.update(value)
                payload[key] = nested
            else:
                payload[key] = value
        return type(self).model_validate(payload)

    @property
    def location(self) -> str:
        return self.physical.location

    @property
    def emotional_state(self) -> str:
        return self.emotional.primary_emotion

    @property
    def inventory(self) -> list[str]:
        return self.physical.inventory

    @property
    def knowledge(self) -> list[str]:
        return self.knowledge_state.known_facts


class CharacterStateDelta(VersionedSchema):
    """State change extracted from a chapter for one character."""

    name: str
    change_summary: str = Field(default="")
    to_state: CharacterState


class RelationshipState(VersionedSchema):
    """Structured relationship state between characters."""

    pair_id: str
    characters: list[str] = Field(default_factory=list, min_length=2)
    public_status: str = Field(default="")
    trust: float = Field(default=0.5, ge=0.0, le=1.0)
    tension: float = Field(default=0.5, ge=0.0, le=1.0)
    dependency: float = Field(default=0.0, ge=0.0, le=1.0)
    last_shift_event: str = Field(default="")
    last_updated_chapter: int = Field(default=0, ge=0)
    notes: str = Field(default="")

    @field_validator("public_status", "last_shift_event", "notes", mode="before")
    @classmethod
    def _coerce_relationship_string_fields(cls, v: Any) -> str:
        return stringify_text_value(v)


class RelationshipStateDelta(VersionedSchema):
    """Relationship update extracted from a chapter."""

    pair_id: str
    change_summary: str = Field(default="")
    relationship: RelationshipState


class PlotThreadState(VersionedSchema):
    """Long-running plot thread state."""

    thread_id: str
    title: str
    status: str = Field(default="active")
    owners: list[str] = Field(default_factory=list)
    last_touched_chapter: int = Field(default=0, ge=0)
    next_payoff_window: str = Field(default="")
    blocking_condition: str = Field(default="")
    summary: str = Field(default="")

    @field_validator("summary", "blocking_condition", mode="before")
    @classmethod
    def _coerce_plot_thread_string_fields(cls, v: Any) -> str:
        return stringify_text_value(v)


class PlotThreadDelta(VersionedSchema):
    """Plot thread update extracted from a chapter."""

    thread_id: str
    change_summary: str = Field(default="")
    thread: PlotThreadState


class CarryForwardItem(VersionedSchema):
    """A single 'must carry forward' item linking chapters.

    Represents an unresolved narrative thread, dangling physical state, or open
    question that the next chapter is expected to explicitly address. Carries
    an explicit ``status`` so authors (or the LLM) can declare an item
    abandoned or deferred rather than forcing a false response — this is the
    escape hatch that prevents the carry-forward hard gate from blocking
    intentional narrative ellipsis.
    """

    text: str = Field(default="", description="必须承接的叙事事实，不要写元指令。")
    status: Literal["open", "abandoned", "deferred"] = Field(
        default="open",
        description="open=待承接；abandoned=主动放弃（需填 reason）；deferred=推迟到后续章节。",
    )
    abandon_reason: str = Field(default="", description="当 status=abandoned 时填写放弃理由。")
    deferred_to_chapter: int = Field(
        default=0,
        ge=0,
        description="当 status=deferred 时填写目标章节号；0 表示未指定具体章节。",
    )

    @field_validator("text", "abandon_reason", mode="before")
    @classmethod
    def _coerce_text_fields(cls, v: Any) -> str:
        return stringify_text_value(v)

    @model_validator(mode="before")
    @classmethod
    def _upgrade_flat_shape(cls, data: Any) -> Any:
        """Accept legacy plain-string carry-forward entries.

        Older payloads stored ``must_carry_forward`` as ``list[str]``; each
        bare string is lifted into ``{"text": <str>, "status": "open"}`` so the
        structured model hydrates without data migration. Dict inputs are
        passed through after ensuring ``text`` is a string.
        """
        if isinstance(data, str):
            return {"text": data, "status": "open"}
        if isinstance(data, dict):
            payload = dict(data)
            if "text" not in payload:
                # Some legacy dicts used the item content under other keys.
                for alt in ("item", "content", "value", "description"):
                    if alt in payload:
                        payload["text"] = payload.pop(alt)
                        break
            if "text" not in payload or payload["text"] is None:
                payload["text"] = ""
            payload["text"] = stringify_text_value(payload["text"])
            payload.setdefault("status", "open")
            return payload
        return data


class ChapterExitState(VersionedSchema):
    """Structured exit state at the end of a chapter."""

    chapter_number: int = Field(ge=1)
    time_marker: str = Field(default="")
    location: str = Field(default="")
    pov: str = Field(default="")
    active_goals: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    must_carry_forward: list[CarryForwardItem] = Field(default_factory=list)
    character_end_states: dict[str, CharacterState] = Field(default_factory=dict)

    @property
    def emotional_state(self) -> str:
        """Best-effort chapter-end emotion summary for template compatibility."""
        if self.pov and self.pov in self.character_end_states:
            return self.character_end_states[self.pov].emotional_state
        for state in self.character_end_states.values():
            emotion = state.emotional_state
            if emotion:
                return emotion
        return ""

    @field_validator("time_marker", "location", "pov", mode="before")
    @classmethod
    def _coerce_exit_state_string_fields(cls, v: Any) -> str:
        return stringify_text_value(v)

    @model_validator(mode="before")
    @classmethod
    def _upgrade_carry_forward(cls, data: Any) -> Any:
        """Normalize legacy ``must_carry_forward: list[str]`` to structured items.

        Each legacy string entry becomes ``CarryForwardItem(text=<str>)`` with
        the default ``status="open"``. Dict entries are preserved. This keeps
        existing on-disk exit states valid without a migration step.
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


__all__ = [
    "CarryForwardItem",
    "CharacterState",
    "CharacterStateDelta",
    "ChapterExitState",
    "EmotionalState",
    "KnowledgeState",
    "MotivationState",
    "PhysicalState",
    "PlotThreadDelta",
    "PlotThreadState",
    "RelationshipState",
    "RelationshipStateDelta",
]
