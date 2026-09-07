"""InitAdapter — maps initialization artifacts to StoryKernel.

Converts StoryBible, CharacterBible, NarrativeContract, and StoryOutline
into a unified StoryKernel that becomes the single source of truth.
"""

from __future__ import annotations

import logging
from typing import Any

from novel_forge.narrative_state.schemas import stable_id
from novel_forge.story_kernel.entity_projection import dedupe_entities, stable_entity_id
from novel_forge.story_kernel.schemas import (
    Entity,
    PromiseLedger,
    Relationship,
    StoryKernel,
    TimelineAnchor,
    WorldRule,
)

_log = logging.getLogger("novel_forge.story_kernel.init_adapter")


def _uid(prefix: str, *parts: Any) -> str:
    """Generate a deterministic short ID from semantic init fields."""
    return stable_id(prefix, *parts)


def _resolve_timeline_character_ids(
    timeline: list[TimelineAnchor],
    entities: list[Entity],
) -> list[TimelineAnchor]:
    """Resolve outline character names/aliases to stable StoryKernel entity IDs."""
    lookup: dict[str, str] = {}
    for entity in entities:
        for key in (entity.entity_id, entity.name, *entity.aliases):
            text = str(key or "").strip()
            if text:
                lookup[text] = entity.entity_id

    resolved: list[TimelineAnchor] = []
    for anchor in timeline:
        character_ids: list[str] = []
        for raw in anchor.characters_involved:
            text = str(raw or "").strip()
            if not text:
                continue
            character_ids.append(lookup.get(text, text))
        resolved.append(
            anchor.model_copy(update={"characters_involved": list(dict.fromkeys(character_ids))})
        )
    return resolved


# ---------------------------------------------------------------------------
# InitAdapter
# ---------------------------------------------------------------------------


class InitAdapter:
    """Maps existing init artifacts to StoryKernel fields."""

    @staticmethod
    def map_story_bible_to_kernel(story_bible: Any) -> dict[str, Any]:
        """Extract world_rules, title, premise, and notes from StoryBible.

        Parameters
        ----------
        story_bible:
            A StoryBible instance (or duck-typed mock).

        Returns
        -------
        dict[str, Any]
            Keys: ``title``, ``premise``, ``world_rules`` (list of dicts),
            ``notes`` (str).
        """
        rule_book = getattr(story_bible, "world_rule_book", None)
        structured_rules = list(getattr(rule_book, "rules", []) or [])
        rules: list[str] = getattr(story_bible, "rules", []) or []
        world_rules: list[dict[str, Any]] = []
        for index, source_rule in enumerate(structured_rules):
            payload = (
                source_rule.model_dump(mode="json")
                if hasattr(source_rule, "model_dump")
                else dict(source_rule)
                if isinstance(source_rule, dict)
                else {}
            )
            content = str(payload.get("content") or "").strip()
            if not content:
                continue
            world_rules.append(
                WorldRule(
                    rule_id=str(payload.get("rule_id") or _uid("wr", index, content)),
                    content=content,
                    category=str(payload.get("category") or "general"),
                    severity=str(payload.get("severity") or "hard"),
                    always_on=bool(payload.get("always_on", False)),
                    applicability_tags=list(payload.get("applicability_tags") or []),
                    trigger_conditions=list(payload.get("trigger_conditions") or []),
                    allowed_behavior=list(payload.get("allowed_behavior") or []),
                    forbidden_behavior=list(payload.get("forbidden_behavior") or []),
                    cost_or_consequence=list(payload.get("cost_or_consequence") or []),
                    exceptions=list(payload.get("exceptions") or []),
                    origin="initialization",
                    rule_version=str(payload.get("version") or "1"),
                ).model_dump(mode="json")
            )
        if world_rules:
            rules = []
        for index, rule_text in enumerate(rules):
            content = str(rule_text or "").strip()
            if not content:
                continue
            world_rules.append(
                WorldRule(
                    rule_id=_uid("wr", index, content),
                    content=content,
                    category="general",
                    severity="hard",
                ).model_dump(mode="json")
            )

        # Build notes from StoryBible metadata
        notes_parts: list[str] = []
        era = getattr(story_bible, "era", "")
        if era:
            notes_parts.append(f"时代背景：{era}")
        themes = getattr(story_bible, "themes", []) or []
        if themes:
            notes_parts.append(f"主题：{'、'.join(themes)}")
        tone = getattr(story_bible, "tone", "")
        if tone:
            notes_parts.append(f"基调：{tone}")
        geography = getattr(story_bible, "geography", "")
        if geography:
            notes_parts.append(f"地理：{geography}")
        culture = getattr(story_bible, "culture", "")
        if culture:
            notes_parts.append(f"文化：{culture}")
        magic_or_tech = getattr(story_bible, "magic_or_tech", "")
        if magic_or_tech:
            notes_parts.append(f"魔法/科技：{magic_or_tech}")

        return {
            "title": str(getattr(story_bible, "title", "") or ""),
            "premise": str(getattr(story_bible, "premise", "") or ""),
            "world_rules": world_rules,
            "notes": "\n".join(notes_parts),
        }

    @staticmethod
    def map_character_bible_to_kernel(character_bible: Any) -> dict[str, Any]:
        """Extract entities and relationships from CharacterBible.

        Parameters
        ----------
        character_bible:
            A CharacterBible instance (or duck-typed mock).

        Returns
        -------
        dict[str, Any]
            Keys: ``entities`` (list of dicts), ``relationships`` (list of dicts).
        """
        characters = getattr(character_bible, "characters", []) or []
        if not characters:
            return {"entities": [], "relationships": []}

        # Build name → entity_id lookup
        name_to_id: dict[str, str] = {}
        entities: list[dict[str, Any]] = []

        for char in characters:
            name = str(getattr(char, "name", "") or "").strip()
            if not name:
                continue

            entity_id = str(getattr(char, "character_id", "") or "").strip()
            if not entity_id:
                from novel_forge.core.domain.character_identity import stable_character_id

                entity_id = stable_character_id(name)
            name_to_id[name] = entity_id

        # Second pass: build entities and relationships
        relationships: list[dict[str, Any]] = []
        relationship_counts: dict[tuple[str, str, str, str], int] = {}
        for char in characters:
            name = str(getattr(char, "name", "") or "").strip()
            if not name:
                continue

            entity_id = name_to_id[name]
            role = str(getattr(char, "role", "supporting") or "supporting")

            # Build attributes dict with character profile data
            attributes: dict[str, Any] = {"role": role}
            for field_name in (
                "personality",
                "backstory",
                "arc",
                "voice",
                "social_status",
                "abilities",
                "appearance",
                "gender",
                "age",
                "time_layer",
            ):
                value = getattr(char, field_name, "")
                if value:
                    attributes[field_name] = str(value)

            entity = Entity(
                entity_id=entity_id,
                name=name,
                entity_type="character",
                status=str(getattr(char, "status", "active") or "active"),
                attributes=attributes,
                notes=str(getattr(char, "arc", "") or ""),
            )
            entities.append(entity.model_dump(mode="json"))

            # Build relationships from this character's relationship dict
            char_relationships = getattr(char, "relationships", {}) or {}
            for other_name, raw_label in char_relationships.items():
                other_name_str = str(other_name).strip()
                relation_type = "acquaintance"
                if isinstance(raw_label, dict):
                    label_str = str(
                        raw_label.get("label")
                        or raw_label.get("description")
                        or raw_label.get("relation")
                        or ""
                    ).strip()
                    explicit_type = str(raw_label.get("relation_type") or "").strip()
                    if explicit_type:
                        relation_type = explicit_type
                else:
                    label_str = str(raw_label).strip()
                if not other_name_str or not label_str:
                    continue

                target_id = name_to_id.get(other_name_str)
                if not target_id:
                    # Target character not in bible — skip
                    continue

                relationship_key = (entity_id, target_id, relation_type, label_str)
                occurrence = relationship_counts.get(relationship_key, 0)
                relationship_counts[relationship_key] = occurrence + 1
                relationship = Relationship(
                    relationship_id=_uid("rel", *relationship_key, occurrence),
                    source_entity_id=entity_id,
                    target_entity_id=target_id,
                    relation_type=relation_type,
                    label=label_str,
                )
                relationships.append(relationship.model_dump(mode="json"))

        return {
            "entities": entities,
            "relationships": relationships,
        }

    @staticmethod
    def map_entity_registry_to_kernel(
        entity_registry: Any | None = None,
        entity_graph: Any | None = None,
    ) -> dict[str, Any]:
        """Extract canonical entities from EntityRegistry/EntityGraph artifacts."""
        raw_entities: list[Any] = []
        for source in (entity_registry, entity_graph):
            if source is None:
                continue
            raw_entities.extend(list(getattr(source, "entities", []) or []))

        entities: list[dict[str, Any]] = []
        for raw in raw_entities:
            name = str(getattr(raw, "name", "") or "").strip()
            if not name:
                continue
            entity_type = str(
                getattr(getattr(raw, "entity_type", ""), "value", getattr(raw, "entity_type", ""))
                or ""
            ).strip()
            if entity_type == "unknown":
                continue
            if entity_type not in {
                "character",
                "location",
                "item",
                "organization",
                "concept",
            }:
                continue
            entity_id = str(getattr(raw, "entity_id", "") or "").strip()
            if not entity_id:
                entity_id = stable_entity_id(entity_type, name)
            entity = Entity(
                entity_id=entity_id,
                name=name,
                entity_type=entity_type,
                aliases=[str(a) for a in (getattr(raw, "aliases", []) or []) if a],
                attributes={
                    "source": str(getattr(raw, "source", "") or "init_entity_registry"),
                },
                notes=str(getattr(raw, "notes", "") or ""),
            )
            entities.append(entity.model_dump(mode="json"))

        return {"entities": entities}

    @staticmethod
    def map_narrative_contract_to_kernel(contract: Any) -> dict[str, Any]:
        """Extract promise_ledger from NarrativeContract.promise_plan.

        Parameters
        ----------
        contract:
            A NarrativeContract instance, dict, or None.

        Returns
        -------
        dict[str, Any]
            Keys: ``promise_ledger`` (list of dicts).
        """
        if contract is None:
            return {"promise_ledger": []}

        promise_plan = getattr(contract, "promise_plan", []) or []
        promises: list[dict[str, Any]] = []

        for index, raw in enumerate(promise_plan):
            if not isinstance(raw, dict):
                continue
            description = str(raw.get("description", "") or "").strip()
            if not description:
                continue

            entry = PromiseLedger(
                entry_id=_uid(
                    "pl",
                    index,
                    description,
                    raw.get("promise_type", "foreshadow"),
                    raw.get("planted_chapter", 0),
                    raw.get("payoff_chapter", 0),
                    tuple(raw.get("owner_entity_ids") or ()),
                ),
                description=description,
                promise_type=str(raw.get("promise_type", "foreshadow") or "foreshadow"),
                planted_chapter=int(raw.get("planted_chapter", 0) or 0),
                status=str(raw.get("status", "planted") or "planted"),
                payoff_chapter=int(raw.get("payoff_chapter", 0) or 0),
                owner_entity_ids=[str(eid) for eid in (raw.get("owner_entity_ids") or []) if eid],
            )
            promises.append(entry.model_dump(mode="json"))

        return {"promise_ledger": promises}

    @staticmethod
    def map_outline_to_kernel(outline: Any) -> dict[str, Any]:
        """Extract timeline_anchors from StoryOutline.chapters.

        Parameters
        ----------
        outline:
            A StoryOutline instance (or duck-typed mock).

        Returns
        -------
        dict[str, Any]
            Keys: ``timeline`` (list of dicts).
        """
        chapters = getattr(outline, "chapters", []) or []
        timeline: list[dict[str, Any]] = []

        for index, ch in enumerate(chapters):
            chapter_number = int(getattr(ch, "chapter_number", 0) or 0)
            if chapter_number < 1:
                continue

            goal = str(getattr(ch, "goal", "") or "").strip()
            title = str(getattr(ch, "title", "") or "").strip()
            event_text = goal or title or f"第{chapter_number}章"

            time_anchor = str(getattr(ch, "time_anchor", "") or "").strip()
            setting = str(getattr(ch, "setting", "") or "").strip()
            involved = getattr(ch, "involved_characters", []) or []
            involved_characters = [str(c) for c in involved if c]

            anchor = TimelineAnchor(
                anchor_id=_uid(
                    "ta",
                    index,
                    chapter_number,
                    event_text,
                    time_anchor,
                    setting,
                    tuple(involved_characters),
                ),
                chapter=chapter_number,
                event=event_text,
                in_story_time=time_anchor,
                characters_involved=involved_characters,
                location=setting,
                significance="major" if chapter_number == 1 else "minor",
            )
            timeline.append(anchor.model_dump(mode="json"))

        return {"timeline": timeline}


# ---------------------------------------------------------------------------
# Top-level builder
# ---------------------------------------------------------------------------


def build_kernel_from_init(
    project_id: str,
    story_bible: Any,
    character_bible: Any,
    outline: Any,
    narrative_contract: Any | None = None,
    entity_registry: Any | None = None,
    entity_graph: Any | None = None,
) -> StoryKernel:
    """Build a complete StoryKernel from initialization artifacts.

    Combines mappings from all four artifacts into a single validated
    StoryKernel instance.

    Parameters
    ----------
    project_id:
        Unique project identifier.
    story_bible:
        StoryBible instance with world-building data.
    character_bible:
        CharacterBible instance with character profiles.
    outline:
        StoryOutline instance with chapter outlines.
    narrative_contract:
        NarrativeContract instance (optional, may be None).

    Returns
    -------
    StoryKernel
        A fully populated kernel ready for InitTruthGate validation.
    """
    # 1. Map story bible → world_rules, title, premise, notes
    bible_data = InitAdapter.map_story_bible_to_kernel(story_bible)

    # 2. Map character bible → entities, relationships
    char_data = InitAdapter.map_character_bible_to_kernel(character_bible)

    # 3. Map EntityRegistry/EntityGraph → entities
    registry_data = InitAdapter.map_entity_registry_to_kernel(
        entity_registry=entity_registry,
        entity_graph=entity_graph,
    )

    # 4. Map narrative contract → promise_ledger
    contract_data = InitAdapter.map_narrative_contract_to_kernel(narrative_contract)

    # 5. Map outline → timeline
    outline_data = InitAdapter.map_outline_to_kernel(outline)

    entities = dedupe_entities(
        [Entity.model_validate(e) for e in [*char_data["entities"], *registry_data["entities"]]]
    )

    timeline = _resolve_timeline_character_ids(
        [TimelineAnchor.model_validate(t) for t in outline_data["timeline"]],
        entities,
    )

    # 6. Assemble kernel
    kernel = StoryKernel(
        project_id=project_id,
        project_mode="long",
        title=bible_data["title"],
        premise=bible_data["premise"],
        world_rules=[WorldRule.model_validate(wr) for wr in bible_data["world_rules"]],
        entities=entities,
        relationships=[Relationship.model_validate(r) for r in char_data["relationships"]],
        timeline=timeline,
        promise_ledger=[PromiseLedger.model_validate(p) for p in contract_data["promise_ledger"]],
        notes=bible_data.get("notes", ""),
    )

    _log.info(
        "build_kernel_from_init | project_id=%s | entities=%d | relationships=%d "
        "| world_rules=%d | timeline=%d | promises=%d",
        project_id,
        len(kernel.entities),
        len(kernel.relationships),
        len(kernel.world_rules),
        len(kernel.timeline),
        len(kernel.promise_ledger),
    )

    return kernel


__all__ = ["InitAdapter", "build_kernel_from_init"]
