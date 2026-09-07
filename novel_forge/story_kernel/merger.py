"""StoryKernelMerger — applies a ChapterOutcome to a StoryKernel.

Replaces CanonMerger for the StoryKernel architecture. Takes a ChapterOutcome
(produced by ExtractCanonDeltaStep) and merges it into a StoryKernel,
producing a new immutable copy.

Field mapping (ChapterOutcome → StoryKernel):
    canon_delta.character_updates + character_state_deltas → entities
    canon_delta.new_events → timeline
    canon_delta.foreshadowing_updates → promise_ledger (foreshadow type)
    plot_thread_deltas → promise_ledger (suspense type)
    relationship_deltas → relationships
    canon_delta.new_world_facts → pending_world_facts (requires adjudication)
    canon_delta.chapter_summary + structured_summary → chapter_summaries
    canon_delta.new_banned_phrases → banned_phrases
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from novel_forge.common.utils import normalize_gender_value
from novel_forge.core.domain.guardrails import is_system_artifact_name
from novel_forge.core.schemas.story_state import (
    CharacterState,
)
from novel_forge.story_kernel.schemas import (
    Entity,
    PromiseLedger,
    Relationship,
    StoryKernel,
    StoryKernelStructuredWarning,
    TimelineAnchor,
)

if TYPE_CHECKING:
    from novel_forge.core.schemas.chapter import ChapterOutcome

_log = logging.getLogger(__name__)

_FORESHADOWING_TO_PROMISE_STATUS: dict[str, str] = {
    "planted": "planted",
    "hinted": "hinted",
    "partially_paid": "partially_paid",
    "paid": "paid",
    "broken": "broken",
    "reinforced": "hinted",
    "revealed": "paid",
    "abandoned": "broken",
}

_RELATION_TYPE_ALIASES: dict[str, str] = {
    "family": "family",
    "romantic": "romantic",
    "mentor_student": "mentor_student",
    "business": "business",
    "ally": "ally",
    "enemy": "enemy",
    "rival": "rival",
    "subordinate": "subordinate",
    "friend": "friend",
    "acquaintance": "acquaintance",
    "师徒": "mentor_student",
    "挚友": "friend",
    "宿敌": "enemy",
    "盟友": "ally",
    "恋人": "romantic",
    "家人": "family",
    "下属": "subordinate",
    "竞争": "rival",
    "合作": "business",
}

_RELATION_TYPE_LABELS: dict[str, str] = {
    "family": "家人",
    "romantic": "恋人",
    "mentor_student": "师徒",
    "business": "合作",
    "ally": "盟友",
    "enemy": "敌人",
    "rival": "竞争",
    "subordinate": "上下级",
    "friend": "朋友",
    "acquaintance": "认识",
}

_INVALID_RELATIONSHIP_CHARACTER_KEYS = {
    "character_id",
    "characterid",
    "id",
    "name",
    "gender",
    "性别",
    "角色id",
    "角色_id",
}

_GENERIC_RELATIONSHIP_NAMES = {
    "众人",
    "镇民",
    "村民",
    "百姓",
    "官兵",
    "士兵",
    "侍卫",
    "下人",
    "仆从",
    "黑衣人",
    "群众",
    "路人",
    "男",
    "女",
    "男性",
    "女性",
    "male",
    "female",
}

_MAX_RELATIONSHIP_LABEL_CHARS = 20


def _character_entity_id(name: str) -> str:
    """Generate a stable entity_id for a character name."""
    cleaned = _clean_relationship_character_name(name)
    return f"char_{cleaned.lower().replace(' ', '_')}"


def _clean_relationship_character_name(name: Any) -> str:
    """Return the canonical display name used for relationship pair matching."""
    return str(name or "").strip().strip("“”\"'「」《》")


def _is_valid_relationship_character_name(name: str) -> bool:
    """Return False for schema keys, gender values, and generic crowd labels."""
    text = _clean_relationship_character_name(name)
    if is_system_artifact_name(text):
        return False

    normalized = text.replace("：", ":").strip()
    lowered = normalized.lower()
    if lowered in _GENERIC_RELATIONSHIP_NAMES or normalized in _GENERIC_RELATIONSHIP_NAMES:
        return False

    if ":" in normalized:
        prefix = normalized.split(":", maxsplit=1)[0].strip().lower()
        if prefix in _INVALID_RELATIONSHIP_CHARACTER_KEYS:
            return False

    return True


def _relationship_pair_id(char_a: str, char_b: str) -> str:
    """Build a direction-independent relationship pair id from display names."""
    return "__".join(sorted([char_a, char_b]))


def _dedupe_relationship_characters(characters: list[str]) -> list[str]:
    """Clean, validate, and dedupe relationship character names in order."""
    result: list[str] = []
    seen: set[str] = set()
    for raw_name in characters:
        name = _clean_relationship_character_name(raw_name)
        if not _is_valid_relationship_character_name(name) or name in seen:
            continue
        result.append(name)
        seen.add(name)
    return result


def _find_entity_by_name(entities: list[Entity], name: str) -> Entity | None:
    """Find an entity by name (case-sensitive match)."""
    for entity in entities:
        if entity.name == name:
            return entity
    return None


def _merge_entity_attributes(
    existing_attrs: dict[str, Any],
    char_state: CharacterState,
) -> dict[str, Any]:
    """Merge CharacterState fields into entity attributes dict.

    Gender values are normalized via :func:`normalize_gender_value`.  If the
    existing entity already has a gender that differs from the incoming value,
    the existing value is preserved (identity should not flip between chapters).

    Voice is a character-specific writing style; once established it is never
    overwritten by a ChapterOutcome.
    """
    attrs = dict(existing_attrs)
    if char_state.location:
        attrs["location"] = char_state.location
    if char_state.emotional_state:
        attrs["emotional_state"] = char_state.emotional_state
    if char_state.physical.inventory:
        attrs["inventory"] = list(char_state.physical.inventory)
    if char_state.physical.fatigue:
        attrs["fatigue"] = char_state.physical.fatigue
    if char_state.physical.injuries:
        attrs["injuries"] = list(char_state.physical.injuries)

    # ── Gender normalization ────────────────────────────────────────
    incoming_gender = normalize_gender_value(char_state.gender)
    existing_gender = normalize_gender_value(attrs.get("gender"))
    if incoming_gender:
        if existing_gender and incoming_gender != existing_gender:
            pass  # keep existing — identity should not flip
        else:
            attrs["gender"] = incoming_gender
    elif existing_gender:
        attrs["gender"] = existing_gender

    if char_state.social_status:
        attrs["social_status"] = char_state.social_status
    if char_state.motivation.short_term_goal:
        attrs["short_term_goal"] = char_state.motivation.short_term_goal
    if char_state.motivation.long_term_goal:
        attrs["long_term_goal"] = char_state.motivation.long_term_goal
    if char_state.emotional.primary_emotion:
        attrs["primary_emotion"] = char_state.emotional.primary_emotion
    if char_state.notes:
        attrs["notes"] = char_state.notes

    # ── Voice preservation ──────────────────────────────────────────
    if not existing_attrs.get("voice") and char_state.voice:
        attrs["voice"] = char_state.voice

    return attrs


def _resolve_relation_type_label(status: str) -> str:
    """Map a RelationshipState.public_status to a RelationType-compatible label."""
    return _RELATION_TYPE_ALIASES.get(status.strip().lower(), "acquaintance")


def _has_explicit_relation_type(status: str) -> bool:
    """Return True when public_status is a known short relation type label."""
    return status.strip().lower() in _RELATION_TYPE_ALIASES


def _relationship_display_label(
    public_status: str,
    *,
    relation_type: str,
    fallback: str = "",
) -> str:
    """Keep relationship labels short; long prose belongs in summaries/notes."""
    status = public_status.strip()
    if status and len(status) <= _MAX_RELATIONSHIP_LABEL_CHARS:
        return status
    if fallback:
        return fallback
    return _RELATION_TYPE_LABELS.get(relation_type, relation_type)


def _relationship_summary_candidate(public_status: str) -> str:
    """Allow short status labels as a summary fallback, but reject prose blobs."""
    status = public_status.strip()
    if status and len(status) <= _MAX_RELATIONSHIP_LABEL_CHARS:
        return status
    return ""


def _relationship_metric_value(rel: Any, field_name: str, fallback: float) -> float:
    """Use an extracted metric only when it was explicitly present in the payload."""
    fields_set: set[str] = getattr(rel, "model_fields_set", set())
    if field_name in fields_set:
        return float(getattr(rel, field_name))
    return fallback


def _find_relationship_by_pair(
    relationships: list[Relationship],
    entity_names: dict[str, str],
    char_a: str,
    char_b: str,
) -> int | None:
    """Find relationship index by character name pair (bidirectional).

    This is the legacy O(R) linear scan version. For batch operations,
    prefer _find_relationship_by_pair_fast with a pre-built index.
    """
    src_name = _clean_relationship_character_name(char_a)
    tgt_name = _clean_relationship_character_name(char_b)
    id_a = entity_names.get(src_name, _character_entity_id(src_name))
    id_b = entity_names.get(tgt_name, _character_entity_id(tgt_name))
    for i, rel in enumerate(relationships):
        src_tgt = (rel.source_entity_id, rel.target_entity_id)
        if src_tgt == (id_a, id_b) or src_tgt == (id_b, id_a):
            return i
    return None


def _find_relationship_by_pair_fast(
    entity_names: dict[str, str],
    char_a: str,
    char_b: str,
    pair_index: dict[frozenset[str], int],
) -> int | None:
    """Find relationship index using pre-built pair→index dict for O(1) lookup.

    Parameters
    ----------
    pair_index:
        Pre-built mapping from frozenset(source_id, target_id) to list index.
    """
    src_name = _clean_relationship_character_name(char_a)
    tgt_name = _clean_relationship_character_name(char_b)
    id_a = entity_names.get(src_name, _character_entity_id(src_name))
    id_b = entity_names.get(tgt_name, _character_entity_id(tgt_name))
    pair_key = frozenset((id_a, id_b))
    return pair_index.get(pair_key)


def _find_promise_by_id(
    promises: list[PromiseLedger], entry_id: str
) -> int | None:
    """Find promise index by entry_id."""
    for i, p in enumerate(promises):
        if p.entry_id == entry_id:
            return i
    return None


class StoryKernelMerger:
    """Merges a ChapterOutcome into an existing StoryKernel.

    Produces a new StoryKernel copy; the original is never mutated.
    """

    def merge_outcome(
        self, kernel: StoryKernel, outcome: ChapterOutcome
    ) -> StoryKernel:
        """Apply *outcome* on top of *kernel* and return a new StoryKernel.

        Parameters
        ----------
        kernel:
            The current StoryKernel state.
        outcome:
            The ChapterOutcome extracted from a chapter.

        Returns
        -------
        StoryKernel
            A new kernel with all deltas applied.
        """
        # Deep copy using Pydantic's model_copy (faster than JSON round-trip)
        new = kernel.model_copy(deep=True)
        # ``model_copy`` also copies private/lazy cache entries.  The copied
        # name index points at the pre-merge Entity objects and becomes stale
        # as soon as this merger replaces an entity in ``new.entities``.
        object.__setattr__(new, "_name_index", None)

        chapter_number = outcome.source_chapter

        # 1. Update chapter counter
        new.current_chapter = chapter_number

        # 2. Merge character entities (from character_updates + character_state_deltas)
        self._merge_characters(new, outcome)

        # 3. Merge relationships
        self._merge_relationships(new, outcome)

        # 4. Append timeline events
        self._merge_timeline(new, outcome)

        # 5. Merge foreshadowing (→ promise_ledger foreshadow type)
        self._merge_foreshadowing(new, outcome)

        # 6. Merge plot threads (→ promise_ledger suspense type)
        self._merge_plot_threads(new, outcome)

        # 7. Preserve candidate world facts for adjudication (never → world_rules)
        self._merge_world_facts(new, outcome)

        # 8. Add chapter summary
        self._merge_chapter_summary(new, outcome)

        # 9. Merge banned phrases
        self._merge_banned_phrases(new, outcome)

        # 10. Store chapter exit state (→ chapter_exit_states)
        self._merge_chapter_exit_state(new, outcome)

        return new

    # ── Private merge methods ──────────────────────────────────────

    def _merge_characters(
        self, kernel: StoryKernel, outcome: ChapterOutcome
    ) -> None:
        chapter_number = outcome.source_chapter

        entity_by_name = {
            entity.name: index
            for index, entity in enumerate(kernel.entities)
            if str(getattr(entity.entity_type, "value", entity.entity_type)) == "character"
        }
        allow_bootstrap = not entity_by_name
        archived_character_names = {
            entity.name
            for entity in kernel.archived_entities
            if str(getattr(entity.entity_type, "value", entity.entity_type)) == "character"
        }

        for name, char_state in outcome.character_updates.items():
            if is_system_artifact_name(name):
                continue
            self._merge_single_character(
                kernel,
                name,
                char_state,
                chapter_number,
                entity_by_name,
                allow_create=allow_bootstrap or name in archived_character_names,
            )

        # Apply character_state_deltas (richer state)
        for char_delta in outcome.character_state_deltas:
            if is_system_artifact_name(char_delta.name):
                continue
            self._merge_single_character(
                kernel,
                char_delta.name,
                char_delta.to_state,
                chapter_number,
                entity_by_name,
                allow_create=allow_bootstrap or char_delta.name in archived_character_names,
            )

        # Apply chapter_exit_state character_end_states
        if outcome.chapter_exit_state is not None:
            for name, char_state in outcome.chapter_exit_state.character_end_states.items():
                if is_system_artifact_name(name):
                    continue
                self._merge_single_character(
                    kernel,
                    name,
                    char_state,
                    chapter_number,
                    entity_by_name,
                    allow_create=allow_bootstrap or name in archived_character_names,
                )

    def _merge_single_character(
        self,
        kernel: StoryKernel,
        name: str,
        char_state: CharacterState,
        chapter_number: int,
        entity_by_name: dict[str, int],
        *,
        allow_create: bool,
    ) -> None:
        """Merge a single character state into the kernel entities."""
        if name in entity_by_name:
            idx = entity_by_name[name]
            existing = kernel.entities[idx]
            merged_attrs = _merge_entity_attributes(existing.attributes, char_state)
            et_value = str(getattr(existing.entity_type, "value", existing.entity_type) or "character")
            kernel.entities[idx] = Entity(
                entity_id=existing.entity_id,
                name=existing.name,
                entity_type=et_value,
                aliases=existing.aliases,
                status="active" if char_state.alive else "destroyed",
                attributes=merged_attrs,
                source_chapter=existing.source_chapter,
                last_seen_chapter=chapter_number,
                notes=char_state.notes or existing.notes,
            )
        elif allow_create:
            # Creation here is limited to empty-kernel bootstrap or archived reactivation.
            entity_id = _character_entity_id(name)
            attrs = _merge_entity_attributes({}, char_state)
            new_entity = Entity(
                entity_id=entity_id,
                name=name,
                entity_type="character",
                status="active" if char_state.alive else "destroyed",
                attributes=attrs,
                source_chapter=chapter_number,
                last_seen_chapter=chapter_number,
                notes=char_state.notes,
            )
            kernel.entities.append(new_entity)
            entity_by_name[name] = len(kernel.entities) - 1
        else:
            kernel.structured_warnings.append(
                StoryKernelStructuredWarning(
                    warning_type="unknown_character_delta_rejected",
                    source="story_kernel.merger",
                    chapter_number=chapter_number,
                    details={
                        "observed_name": name,
                        "action": "rejected_before_kernel_mutation",
                    },
                )
            )

    def _merge_relationships(
        self, kernel: StoryKernel, outcome: ChapterOutcome
    ) -> None:
        """Merge relationship deltas into kernel relationships."""
        # Build entity name→id lookup
        entity_names = {
            entity.name: entity.entity_id
            for entity in kernel.entities
            if str(getattr(entity.entity_type, "value", entity.entity_type)) == "character"
        }

        # Build relationship pair→index lookup for O(1) access instead of O(R) scan.
        # Key is frozenset of entity IDs for bidirectional matching.
        rel_pair_index: dict[frozenset[str], int] = {}
        for idx, existing_rel_item in enumerate(kernel.relationships):
            pair_key = frozenset(
                (existing_rel_item.source_entity_id, existing_rel_item.target_entity_id)
            )
            rel_pair_index[pair_key] = idx

        for rel_delta in outcome.relationship_deltas:
            chars = _dedupe_relationship_characters(
                list(rel_delta.relationship.characters)
            )
            if len(chars) < 2:
                continue

            src_name, tgt_name = chars[0], chars[1]
            unknown_names = [
                name for name in (src_name, tgt_name) if name not in entity_names
            ]
            if unknown_names:
                kernel.structured_warnings.append(
                    StoryKernelStructuredWarning(
                        warning_type="unknown_relationship_character_rejected",
                        source="story_kernel.merger",
                        chapter_number=outcome.source_chapter,
                        details={
                            "observed_names": unknown_names,
                            "pair_id": rel_delta.pair_id,
                            "action": "rejected_before_kernel_mutation",
                        },
                    )
                )
                continue
            existing_idx = _find_relationship_by_pair_fast(
                entity_names, src_name, tgt_name, rel_pair_index
            )

            src_id = entity_names[src_name]
            tgt_id = entity_names[tgt_name]

            rel = rel_delta.relationship
            rel_type = _resolve_relation_type_label(rel.public_status)
            rel_type_is_explicit = _has_explicit_relation_type(rel.public_status)
            short_status = _relationship_summary_candidate(rel.public_status)

            if existing_idx is not None:
                existing = kernel.relationships[existing_idx]
                existing_rel_type = str(
                    getattr(existing.relation_type, "value", existing.relation_type)
                    or "acquaintance"
                )
                # Backfill shift_summary from public_status when both delta and existing are empty.
                shift_summary_value = (
                    rel_delta.change_summary or existing.shift_summary or short_status or ""
                )
                relation_type_value = rel_type if rel_type_is_explicit else existing_rel_type
                kernel.relationships[existing_idx] = Relationship(
                    relationship_id=existing.relationship_id,
                    source_entity_id=existing.source_entity_id,
                    target_entity_id=existing.target_entity_id,
                    relation_type=relation_type_value,
                    label=_relationship_display_label(
                        rel.public_status,
                        relation_type=relation_type_value,
                        fallback=existing.label,
                    ),
                    trust=_relationship_metric_value(rel, "trust", existing.trust),
                    tension=_relationship_metric_value(rel, "tension", existing.tension),
                    dependency=_relationship_metric_value(
                        rel, "dependency", existing.dependency
                    ),
                    status=existing.status,
                    established_chapter=existing.established_chapter,
                    last_shift_chapter=rel.last_updated_chapter or outcome.source_chapter,
                    shift_summary=shift_summary_value,
                    notes=rel.notes or existing.notes,
                )
            else:
                # Create new relationship
                pair_id = _relationship_pair_id(src_name, tgt_name)
                # Backfill shift_summary from public_status when change_summary is empty.
                shift_summary_value = rel_delta.change_summary or short_status or ""
                new_rel = Relationship(
                    relationship_id=f"rel_{pair_id}",
                    source_entity_id=src_id,
                    target_entity_id=tgt_id,
                    relation_type=rel_type,
                    label=_relationship_display_label(
                        rel.public_status,
                        relation_type=rel_type,
                    ),
                    trust=_relationship_metric_value(rel, "trust", 0.5),
                    tension=_relationship_metric_value(rel, "tension", 0.5),
                    dependency=_relationship_metric_value(rel, "dependency", 0.0),
                    status="active",
                    established_chapter=outcome.source_chapter,
                    last_shift_chapter=outcome.source_chapter,
                    shift_summary=shift_summary_value,
                    notes=rel.notes,
                )
                kernel.relationships.append(new_rel)
                # Update the pair index so subsequent deltas can find this new relationship
                rel_pair_index[frozenset((src_id, tgt_id))] = len(kernel.relationships) - 1

    def _merge_timeline(
        self, kernel: StoryKernel, outcome: ChapterOutcome
    ) -> None:
        existing_ids = {t.anchor_id for t in kernel.timeline}
        character_refs = {
            ref
            for entity in kernel.entities
            if str(getattr(entity.entity_type, "value", entity.entity_type)) == "character"
            for ref in (entity.name, entity.entity_id)
            if ref
        }
        next_idx = len(kernel.timeline) + 1

        for event in outcome.new_events:
            anchor_id = f"evt_{next_idx}"
            while anchor_id in existing_ids:
                next_idx += 1
                anchor_id = f"evt_{next_idx}"

            accepted_refs = [
                ref for ref in event.characters_involved if ref in character_refs
            ]
            rejected_refs = [
                ref for ref in event.characters_involved if ref not in character_refs
            ]
            if rejected_refs:
                kernel.structured_warnings.append(
                    StoryKernelStructuredWarning(
                        warning_type="unknown_timeline_character_rejected",
                        source="story_kernel.merger",
                        chapter_number=outcome.source_chapter,
                        details={
                            "observed_names": rejected_refs,
                            "anchor_id": event.anchor_id,
                            "action": "removed_from_characters_involved",
                        },
                    )
                )
            event_payload = event.model_dump(mode="json")
            event_payload.update(
                {
                    "anchor_id": anchor_id,
                    "characters_involved": accepted_refs,
                }
            )
            kernel.timeline.append(TimelineAnchor.model_validate(event_payload))
            existing_ids.add(anchor_id)
            next_idx += 1

    def _merge_foreshadowing(
        self, kernel: StoryKernel, outcome: ChapterOutcome
    ) -> None:
        for fs_update in outcome.foreshadowing_updates:
            existing_idx = _find_promise_by_id(
                kernel.promise_ledger, fs_update.entry_id
            )
            fs_status_raw = str(getattr(fs_update.status, "value", fs_update.status) or "planted")
            status = _FORESHADOWING_TO_PROMISE_STATUS.get(fs_status_raw, "planted")
            payoff = fs_update.payoff_chapter or 0

            if existing_idx is not None:
                existing = kernel.promise_ledger[existing_idx]
                vis_value = str(getattr(existing.visibility, "value", existing.visibility) or "public")
                kernel.promise_ledger[existing_idx] = PromiseLedger(
                    entry_id=existing.entry_id,
                    description=fs_update.description or existing.description,
                    promise_type="foreshadow",
                    planted_chapter=existing.planted_chapter,
                    status=status,
                    payoff_chapter=payoff or existing.payoff_chapter,
                    owner_entity_ids=existing.owner_entity_ids,
                    depends_on=existing.depends_on,
                    visibility=vis_value,
                    notes=fs_update.notes or existing.notes,
                )
            else:
                kernel.promise_ledger.append(
                    PromiseLedger(
                        entry_id=fs_update.entry_id,
                        description=fs_update.description,
                        promise_type="foreshadow",
                        planted_chapter=fs_update.planted_chapter,
                        status=status,
                        payoff_chapter=payoff,
                        notes=fs_update.notes,
                    )
                )

    def _merge_plot_threads(
        self, kernel: StoryKernel, outcome: ChapterOutcome
    ) -> None:
        """Merge plot thread deltas into promise_ledger (suspense type)."""
        for plot_delta in outcome.plot_thread_deltas:
            thread = plot_delta.thread
            entry_id = plot_delta.thread_id

            existing_idx = _find_promise_by_id(kernel.promise_ledger, entry_id)

            # Map PlotThreadState.status → promise status
            status_map = {
                "active": "planted",
                "resolved": "paid",
                "blocked": "hinted",
                "abandoned": "broken",
            }
            status = status_map.get(thread.status, "planted")

            if existing_idx is not None:
                existing = kernel.promise_ledger[existing_idx]
                vis_value = str(getattr(existing.visibility, "value", existing.visibility) or "public")
                kernel.promise_ledger[existing_idx] = PromiseLedger(
                    entry_id=existing.entry_id,
                    description=thread.title or thread.summary or existing.description,
                    promise_type="suspense",
                    planted_chapter=existing.planted_chapter,
                    status=status,
                    payoff_chapter=existing.payoff_chapter,
                    owner_entity_ids=existing.owner_entity_ids,
                    depends_on=existing.depends_on,
                    visibility=vis_value,
                    notes=thread.summary or existing.notes,
                )
            else:
                kernel.promise_ledger.append(
                    PromiseLedger(
                        entry_id=entry_id,
                        description=thread.title or thread.summary,
                        promise_type="suspense",
                        planted_chapter=thread.last_touched_chapter
                        or outcome.source_chapter,
                        status=status,
                    )
                )

    def _merge_world_facts(
        self, kernel: StoryKernel, outcome: ChapterOutcome
    ) -> None:
        """Record extracted facts for adjudication without mutating canon rules.

        An extractor sees only one chapter and therefore cannot be trusted to
        upgrade a newly mentioned fact into an immutable world law.  Preserve
        the candidate with a stable, chapter-scoped key; an explicit source
        update/adjudication workflow owns any later rule-book revision.
        """
        existing_contents = set(kernel.pending_world_facts.values())
        for key, content in outcome.new_world_facts.items():
            normalized = str(content or "").strip()
            if not normalized or normalized in existing_contents:
                continue
            candidate_key = f"chapter_{outcome.source_chapter}_{str(key or 'fact').strip() or 'fact'}"
            suffix = 2
            base_key = candidate_key
            while candidate_key in kernel.pending_world_facts:
                candidate_key = f"{base_key}_{suffix}"
                suffix += 1
            kernel.pending_world_facts[candidate_key] = normalized
            existing_contents.add(normalized)

    def _merge_chapter_summary(
        self, kernel: StoryKernel, outcome: ChapterOutcome
    ) -> None:
        """Add chapter summary to chapter_summaries."""
        chapter_number = outcome.source_chapter
        summary = (
            outcome.structured_summary
            or outcome.chapter_summary
        )
        if summary:
            kernel.chapter_summaries[chapter_number] = summary

    def _merge_banned_phrases(
        self, kernel: StoryKernel, outcome: ChapterOutcome
    ) -> None:
        """Merge new banned phrases (truncate to 50 chars, max 30 items)."""
        if outcome.new_banned_phrases:
            combined = (
                kernel.banned_phrases + outcome.new_banned_phrases
            )[-30:]
            kernel.banned_phrases = [p[:50] for p in combined]

    def _merge_chapter_exit_state(
        self, kernel: StoryKernel, outcome: ChapterOutcome
    ) -> None:
        """Store chapter exit state so subsequent chapters can read it as previous_exit."""
        if outcome.chapter_exit_state is not None:
            chapter_number = outcome.source_chapter
            canonical_names = {
                entity.name
                for entity in kernel.entities
                if str(getattr(entity.entity_type, "value", entity.entity_type)) == "character"
            }
            exit_state = outcome.chapter_exit_state.model_copy(deep=True)
            rejected_names = [
                name for name in exit_state.character_end_states if name not in canonical_names
            ]
            exit_state.character_end_states = {
                name: state
                for name, state in exit_state.character_end_states.items()
                if name in canonical_names
            }
            if exit_state.pov and exit_state.pov not in canonical_names:
                rejected_names.append(exit_state.pov)
                exit_state.pov = ""
            if rejected_names:
                kernel.structured_warnings.append(
                    StoryKernelStructuredWarning(
                        warning_type="unknown_exit_state_character_rejected",
                        source="story_kernel.merger",
                        chapter_number=chapter_number,
                        details={
                            "observed_names": sorted(set(rejected_names)),
                            "action": "removed_before_exit_state_persist",
                        },
                    )
                )
            kernel.chapter_exit_states[chapter_number] = exit_state


def merge_character_state_preserving_identity(
    current: CharacterState | None,
    updated: CharacterState,
) -> CharacterState:
    merge_data = updated.model_dump(mode="json", exclude_unset=True)
    incoming_gender = normalize_gender_value(merge_data.get("gender"))

    if current is None:
        if incoming_gender:
            merge_data["gender"] = incoming_gender
        elif "gender" in merge_data:
            merge_data.pop("gender", None)
        return CharacterState.model_validate(merge_data)

    payload = current.model_dump(mode="json")
    current_gender = normalize_gender_value(payload.get("gender"))

    if incoming_gender:
        if current_gender and incoming_gender != current_gender:
            merge_data.pop("gender", None)
        else:
            merge_data["gender"] = incoming_gender
    elif current_gender:
        merge_data.pop("gender", None)

    if not merge_data.get("social_status") and payload.get("social_status"):
        merge_data.pop("social_status", None)

    if payload.get("voice"):
        merge_data.pop("voice", None)

    payload.update(merge_data)
    if current_gender and not payload.get("gender"):
        payload["gender"] = current_gender
    return CharacterState.model_validate(payload)


__all__ = ["StoryKernelMerger", "merge_character_state_preserving_identity"]
