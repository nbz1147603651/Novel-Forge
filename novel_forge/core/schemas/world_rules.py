"""Structured world-rule contracts shared by initialization and chapter runtime."""

from __future__ import annotations

import hashlib
import json
from typing import Any, ClassVar, Literal

from pydantic import Field, field_validator, model_validator

from novel_forge.core.schemas.base import VersionedSchema
from novel_forge.core.utils.type_coerce import coerce_text_list, stringify_text_value

WorldRuleSeverity = Literal["hard", "soft"]
WorldRuleVerdict = Literal["compliant", "conflict", "unknown"]


def _stable_rule_id(content: str, index: int) -> str:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:10]
    return f"wr_{index:02d}_{digest}"


class WorldRuleSpec(VersionedSchema):
    """One authoritatively initialized, executable world rule."""

    rule_id: str = ""
    content: str = Field(description="Canonical, testable rule statement.")
    category: str = Field(default="general")
    severity: WorldRuleSeverity = "hard"
    always_on: bool = False
    applicability_tags: list[str] = Field(default_factory=list)
    trigger_conditions: list[str] = Field(default_factory=list)
    allowed_behavior: list[str] = Field(default_factory=list)
    forbidden_behavior: list[str] = Field(default_factory=list)
    cost_or_consequence: list[str] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)
    source: str = "initialization"
    version: str = "1"

    @field_validator("severity", mode="before")
    @classmethod
    def _coerce_severity(cls, value: Any) -> str:
        """Tolerate LLM-emitted severity labels outside the hard/soft enum.

        Prompts historically did not publish the legal severity enum, so models
        invent a three-level scale (``hard``/``medium``/``soft``). Rejecting
        ``medium`` here would crash the entire ``StoryBible`` parse and abort
        initialization. We coerce any non-``hard`` value to ``soft`` -- the
        non-blocking default -- so the rule book still loads; downstream
        ``validate_world_rule_book`` then enforces the hard-rule invariants
        (always_on / forbidden_behavior / cost_or_consequence) on rules the
        LLM actually intended to be hard but mislabeled.
        """
        text = str(value or "").strip().lower()
        return "hard" if text == "hard" else "soft"

    @field_validator("content", "category", "source", "version", mode="before")
    @classmethod
    def _coerce_text(cls, value: Any) -> str:
        return stringify_text_value(value).strip()

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
    def _coerce_list(cls, value: Any) -> list[str]:
        return coerce_text_list(value)

    @model_validator(mode="after")
    def _normalize(self) -> "WorldRuleSpec":
        self.category = self.category.lower().replace(" ", "_") or "general"
        if self.severity == "hard" and not self.rule_id:
            self.rule_id = _stable_rule_id(self.content, 0)
        return self


# Fields that belong to a single ``WorldRuleSpec``. When an LLM flattens one
# rule's fields onto the ``WorldRuleBook`` container (or onto the outer
# ``world_rules`` fragment), these are the keys we must recover into a proper
# rule entry instead of letting ``extra="forbid"`` reject the whole bible.
_SPEC_FIELDS: tuple[str, ...] = (
    "rule_id",
    "content",
    "category",
    "severity",
    "always_on",
    "applicability_tags",
    "trigger_conditions",
    "allowed_behavior",
    "forbidden_behavior",
    "cost_or_consequence",
    "exceptions",
)
_SPEC_FIELDS_SET: frozenset[str] = frozenset(_SPEC_FIELDS)
# Fields that ``WorldRuleBook`` itself accepts; everything else on the raw
# container must be stripped or recovered before strict validation.
_BOOK_FIELDS_SET: frozenset[str] = frozenset(
    {"version", "rules", "source_hash", "description", "schema_version", "created_at"}
)


def _normalize_content_key(value: Any) -> str:
    """Collapse whitespace for content-based deduplication."""
    return "".join(str(value or "").split())


def _spec_field_count(candidate: dict[str, Any]) -> int:
    """Count how many ``WorldRuleSpec`` fields a candidate carries non-empty."""
    return sum(1 for key in _SPEC_FIELDS if candidate.get(key) not in (None, "", [], {}))


def _collect_candidate_rules(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Recursively extract flattened ``WorldRuleSpec`` fields from every nesting layer.

    Real-world LLM outputs (observed in the 青瓦梦匙 project) distribute one
    rule's fields across **three consecutive layers**:

    - The outer ``world_rules`` fragment carries ``rule_id``/``content``/... for
      rule WB003 directly on the fragment dict.
    - The ``world_rule_book`` container carries ``rule_id``/``content``/... for
      rule WB002 alongside the legitimate ``rules`` array.
    - A nested ``world_rule_book.world_rule_book`` dict carries the most complete
      fields for rule WB001.

    A naive deep-merge would let WB001 and WB002 clobber each other. Instead we
    recurse into **each** ``world_rule_book`` layer independently and extract one
    candidate rule per layer, so no rule is lost.
    """
    candidates: list[dict[str, Any]] = []

    def _visit(node: dict[str, Any]) -> None:
        # Recurse into any nested ``world_rule_book`` layer first so every layer
        # gets an independent extraction pass.
        nested = node.get("world_rule_book")
        if isinstance(nested, dict):
            _visit(nested)

        # If this layer carries a non-empty ``content`` among the spec fields,
        # harvest those fields into one candidate rule.
        raw_content = node.get("content")
        if raw_content is None or str(raw_content).strip() == "":
            return
        candidate: dict[str, Any] = {}
        for key in _SPEC_FIELDS:
            if key in node:
                candidate[key] = node[key]
        if candidate:
            candidates.append(candidate)

    _visit(data)
    return candidates


def _dedupe_candidates_by_content(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Deduplicate candidate rules by normalized ``content``.

    The after-validator ``_assign_ids_and_hash`` only deduplicates by
    ``rule_id``; it does **not** remove rules whose content collides. When an
    LLM emits both a legacy string summary in ``rules`` and a structured object
    with the same content recovered from a flattened layer, we must collapse
    them here, preferring the candidate with more populated spec fields so the
    executable detail survives.
    """
    best_by_content: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for candidate in candidates:
        content_key = _normalize_content_key(candidate.get("content"))
        if not content_key:
            continue
        if content_key not in best_by_content:
            best_by_content[content_key] = candidate
            order.append(content_key)
            continue
        existing = best_by_content[content_key]
        if _spec_field_count(candidate) > _spec_field_count(existing):
            best_by_content[content_key] = candidate
    return [best_by_content[key] for key in order]


class WorldRuleBook(VersionedSchema):
    """Versioned source of truth for a project's immutable world rules."""

    version: str = "1"
    rules: list[WorldRuleSpec] = Field(default_factory=list)
    source_hash: str = ""
    description: str = Field(
        default="",
        description="Optional human-readable summary of the rule book; LLMs naturally emit this.",
    )

    @field_validator("description", mode="before")
    @classmethod
    def _coerce_description(cls, value: Any) -> str:
        return stringify_text_value(value).strip()

    @model_validator(mode="before")
    @classmethod
    def _extract_rules_from_nested_layers(cls, data: Any) -> Any:
        """Recover rules flattened across nested ``world_rule_book`` layers.

        Models occasionally emit a rule's ``WorldRuleSpec`` fields
        (``rule_id``/``content``/``category``/...) directly on the
        ``WorldRuleBook`` container, on the outer ``world_rules`` fragment, or
        inside a spurious nested ``world_rule_book.world_rule_book`` object,
        instead of placing them inside the ``rules`` array. Because
        ``VersionedSchema`` enforces ``extra="forbid"``, any such flattened
        field crashes the entire ``StoryBible`` parse and aborts initialization.

        This validator runs before field validation and:

        1. Recursively visits every ``world_rule_book`` nesting layer, harvesting
           one candidate rule per layer so no rule is lost to field competition.
        2. Merges the recovered candidates with the existing ``rules`` entries.
        3. Deduplicates by normalized ``content``, preferring the candidate with
           more populated spec fields (structured detail over a bare string).
        4. Strips every ``WorldRuleSpec`` field and the nested ``world_rule_book``
           key from the container so only legitimate ``WorldRuleBook`` fields
           remain for strict validation.
        """
        if not isinstance(data, dict):
            return data

        normalized = dict(data)

        # Harvest flattened candidates from every nesting layer (including the
        # outer ``world_rules`` fragment keys that bleed onto this dict).
        recovered = _collect_candidate_rules(normalized)

        # Normalize the original ``rules`` value into per-rule dicts so it can
        # be deduplicated alongside the recovered candidates.
        raw_rules = normalized.get("rules")
        if isinstance(raw_rules, list):
            base_rules: list[dict[str, Any]] = [
                item if isinstance(item, dict) else {"content": item} for item in raw_rules
            ]
        else:
            base_rules = []

        all_candidates = [*base_rules, *recovered]
        deduped = _dedupe_candidates_by_content(all_candidates)

        # Reassemble a clean container with only legitimate book-level fields.
        clean: dict[str, Any] = {}
        for key in ("version", "source_hash", "description", "schema_version", "created_at"):
            if key in normalized:
                clean[key] = normalized[key]
        clean["rules"] = deduped
        return clean

    @field_validator("rules", mode="before")
    @classmethod
    def _coerce_legacy_rules(cls, value: Any) -> list[Any]:
        if not isinstance(value, list):
            return []
        return [item if isinstance(item, dict) else {"content": item} for item in value]

    @model_validator(mode="after")
    def _assign_ids_and_hash(self) -> "WorldRuleBook":
        normalized: list[WorldRuleSpec] = []
        seen: set[str] = set()
        for index, rule in enumerate(self.rules, start=1):
            if not rule.content:
                continue
            rule_id = rule.rule_id or _stable_rule_id(rule.content, index)
            if rule_id in seen:
                rule_id = _stable_rule_id(f"{rule.content}:{index}", index)
            seen.add(rule_id)
            normalized.append(rule.model_copy(update={"rule_id": rule_id}))
        self.rules = normalized
        if not self.source_hash:
            payload = [rule.model_dump(mode="json", exclude={"source_hash"}) for rule in normalized]
            self.source_hash = hashlib.sha256(
                json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
        return self

    @property
    def hard_rules(self) -> list[WorldRuleSpec]:
        return [rule for rule in self.rules if rule.severity == "hard"]


class WorldRuleCardEntry(VersionedSchema):
    rule: WorldRuleSpec
    selection_reason: str
    applicable_scene_ids: list[str] = Field(default_factory=list)


class WorldRuleCard(VersionedSchema):
    """Bounded, chapter-specific projection of a ``WorldRuleBook``."""

    chapter_number: int
    rule_book_version: str = "1"
    source_hash: str = ""
    always_on: list[WorldRuleCardEntry] = Field(default_factory=list)
    relevant_rules: list[WorldRuleCardEntry] = Field(default_factory=list)
    omitted_rule_ids: list[str] = Field(default_factory=list)

    @property
    def all_entries(self) -> list[WorldRuleCardEntry]:
        return [*self.always_on, *self.relevant_rules]


class WorldRuleApplication(VersionedSchema):
    """Plan-owned proof that a selected rule has an executable scene landing."""

    _field_aliases: ClassVar[dict[str, str]] = {
        # PLAN_CHAPTER historically asked for a "具体理由" without publishing
        # the canonical nested key. Preserve that recoverable provider output.
        "reason": "not_applicable_reason",
    }

    rule_id: str
    scene_id: str = ""
    applicability: Literal["applied", "not_applicable"] = "applied"
    usage: str = ""
    expected_evidence: str = ""
    forbidden_boundary: str = ""
    not_applicable_reason: str = ""


class WorldRuleIssue(VersionedSchema):
    rule_id: str
    verdict: WorldRuleVerdict = "unknown"
    severity: Literal["critical", "high", "medium", "low"] = "medium"
    summary: str = ""
    evidence: str = ""
    paragraph_hint: str = ""
    repair_goal: str = ""
    root_cause: Literal["source", "plan", "text", "unknown"] = "unknown"
    repair_round: int = 0


class WorldRuleComplianceReport(VersionedSchema):
    chapter_number: int
    source_text_hash: str = ""
    rule_book_hash: str = ""
    issues: list[WorldRuleIssue] = Field(default_factory=list)
    checked_rule_ids: list[str] = Field(default_factory=list)
    skipped_rule_ids: list[str] = Field(
        default_factory=list,
        description="Selected card rules deliberately excluded because the approved plan marked them not_applicable or did not bind them to this chapter.",
    )
    summary: str = ""

    @property
    def has_blocking_conflict(self) -> bool:
        return any(
            issue.verdict == "conflict" and issue.severity in {"critical", "high"}
            for issue in self.issues
        )


__all__ = [
    "WorldRuleApplication",
    "WorldRuleBook",
    "WorldRuleCard",
    "WorldRuleCardEntry",
    "WorldRuleComplianceReport",
    "WorldRuleIssue",
    "WorldRuleSeverity",
    "WorldRuleSpec",
]
