"""StoryKernelRetriever — assembles a context window from StoryKernel for prompt injection.

This module mirrors :mod:`novel_forge.canon.retriever` but lives inside the
``story_kernel`` package and converts :class:`Entity` objects to flat dicts
via :func:`_entity_to_character_dict` so that Jinja2 templates receive the
shape they expect without exposing raw Pydantic models.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, TypedDict

from novel_forge.core.constants import CANON_CONTEXT_RECENT_CHAPTERS
from novel_forge.core.domain.guardrails import is_system_artifact_name
from novel_forge.core.schemas.bible import StoryBible
from novel_forge.core.schemas.story_state import (
    ChapterExitState,
    PlotThreadState,
    RelationshipState,
)
from novel_forge.core.utils.string import carry_forward_text
from novel_forge.story_kernel.schemas import (
    Entity,
    PromiseLedger,
    StoryKernel,
    TimelineAnchor,
)

# ---------------------------------------------------------------------------
# Template-compatible TypedDict
# ---------------------------------------------------------------------------


class CanonContextDict(TypedDict, total=False):
    """TypedDict consumed by Jinja2 templates.

    The keys mirror the fields templates access via ``canon_context.xxx``.
    ``total=False`` allows partial construction — templates use
    ``| default(...)`` guards for missing keys.
    """

    characters: dict[str, dict[str, Any]]
    recent_events: list[dict[str, Any]]
    active_foreshadowing: list[dict[str, Any]]
    active_relationships: list[dict[str, Any]]
    active_plot_threads: list[dict[str, Any]]
    world_facts: dict[str, str]
    previous_chapter_summary: str
    previous_exit_state: dict[str, Any] | None
    must_carry_forward: list[str]
    immutable_facts: list[str]


# ---------------------------------------------------------------------------
# Dataclass context
# ---------------------------------------------------------------------------


@dataclass
class KernelContext:
    """Structured context assembled from a :class:`StoryKernel`.

    Field semantics mirror :class:`novel_forge.canon.retriever.CanonContext`
    but ``characters`` stores flat dicts (produced by
    :func:`_entity_to_character_dict`) instead of ``CharacterState`` objects.
    """

    characters: dict[str, dict[str, Any]] = field(default_factory=dict)
    recent_events: list[TimelineAnchor] = field(default_factory=list)
    active_foreshadowing: list[PromiseLedger] = field(default_factory=list)
    active_relationships: list[RelationshipState] = field(default_factory=list)
    active_plot_threads: list[PlotThreadState] = field(default_factory=list)
    world_facts: dict[str, str] = field(default_factory=dict)
    previous_chapter_summary: str = ""
    previous_exit_state: ChapterExitState | None = None
    must_carry_forward: list[str] = field(default_factory=list)
    immutable_facts: list[str] = field(default_factory=list)

    def to_template_dict(self) -> CanonContextDict:
        """Serialise to a plain dict suitable for Jinja2 template injection."""
        return CanonContextDict(
            characters=self.characters,
            recent_events=[e.model_dump(mode="json") for e in self.recent_events],
            active_foreshadowing=[
                p.model_dump(mode="json") for p in self.active_foreshadowing
            ],
            active_relationships=[
                r.model_dump(mode="json") for r in self.active_relationships
            ],
            active_plot_threads=[
                t.model_dump(mode="json") for t in self.active_plot_threads
            ],
            world_facts=self.world_facts,
            previous_chapter_summary=self.previous_chapter_summary,
            previous_exit_state=(
                self.previous_exit_state.model_dump(mode="json")
                if self.previous_exit_state is not None
                else None
            ),
            must_carry_forward=self.must_carry_forward,
            immutable_facts=self.immutable_facts,
        )


# ---------------------------------------------------------------------------
# Entity → flat dict conversion
# ---------------------------------------------------------------------------


def _entity_to_character_dict(entity: Entity) -> dict[str, Any]:
    """Convert an :class:`Entity` to a flat dict for template rendering.

    The returned dict has keys compatible with
    ``_render_canon_characters.j2``::

        name, alive, location, emotional_state, gender, last_seen_chapter,
        social_status, voice, notes, source_chapter

    Nested ``attributes`` sub-dicts (physical, motivation, knowledge) are
    intentionally **not** flattened — templates access them via top-level
    convenience keys only.
    """
    view = entity.get_character_state_view()
    return {
        "name": entity.name,
        "alive": entity.status != "destroyed",
        "location": view.location,
        "emotional_state": view.emotional_state,
        "gender": view.gender,
        "social_status": view.social_status,
        "voice": view.voice,
        "last_seen_chapter": entity.last_seen_chapter,
        "source_chapter": entity.source_chapter,
        "notes": entity.notes,
    }


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _state_chapter_within_waterline(raw_chapter: Any, prior_chapter: int) -> bool:
    """Return ``True`` if *raw_chapter* is <= *prior_chapter* (or unset)."""
    try:
        chapter = int(raw_chapter or 0)
    except (TypeError, ValueError):
        return True
    return chapter <= 0 or chapter <= prior_chapter


# ---------------------------------------------------------------------------
# StoryKernelRetriever
# ---------------------------------------------------------------------------


class StoryKernelRetriever:
    """Assembles a :class:`KernelContext` from a :class:`StoryKernel`.

    Mirrors the logic of :class:`novel_forge.canon.retriever.CanonRetriever`
    but produces flat-dict character entries via :func:`_entity_to_character_dict`.

    Semantic search (episodic memory) is **not** wired — callers that need it
    should use :class:`CanonRetriever` directly.
    """

    def __init__(
        self,
        *,
        recent_chapters: int = CANON_CONTEXT_RECENT_CHAPTERS,
        max_recent_events: int = 40,
        max_characters: int = 30,
        max_active_foreshadowing: int = 30,
        max_world_facts: int = 80,
    ) -> None:
        self._recent_n = recent_chapters
        self._max_recent_events = max(1, max_recent_events)
        self._max_characters = max(1, max_characters)
        self._max_active_foreshadowing = max(1, max_active_foreshadowing)
        self._max_world_facts = max(1, max_world_facts)

    # -- Properties --------------------------------------------------------

    @property
    def max_recent_events(self) -> int:
        return self._max_recent_events

    @property
    def max_characters(self) -> int:
        return self._max_characters

    @property
    def max_active_foreshadowing(self) -> int:
        return self._max_active_foreshadowing

    @property
    def max_world_facts(self) -> int:
        return self._max_world_facts

    # -- Public API --------------------------------------------------------

    def get_context(
        self,
        kernel: StoryKernel,
        for_chapter: int,
        *,
        involved_characters: list[str] | None = None,
        pov_character: str | None = None,
        world_rules: list[str] | None = None,
        story_bible: StoryBible | None = None,
    ) -> KernelContext:
        """Build a :class:`KernelContext` snapshot for *for_chapter*.

        Parameters
        ----------
        kernel:
            The source-of-truth story kernel.
        for_chapter:
            Chapter number being generated (1-indexed).
        involved_characters:
            Character names that will appear in the upcoming chapter.
        pov_character:
            Point-of-view character name.
        world_rules:
            Extra world-rule strings (e.g. from StoryBible).
        story_bible:
            Optional story bible for banned-intent extraction.
        """
        prior_chapter = max(0, for_chapter - 1)
        cutoff = max(1, for_chapter - self._recent_n)

        # -- Recent events -------------------------------------------------
        recent_events = [
            e for e in kernel.timeline
            if cutoff <= int(e.chapter or 0) <= prior_chapter
        ]
        recent_events = recent_events[-self._max_recent_events:]

        # -- Mention counts for ranking ------------------------------------
        involved_set = set(involved_characters) if involved_characters else set()
        mention_counts: Counter[str] = Counter()
        for e in recent_events:
            mention_counts.update(e.characters_involved)

        # -- Characters (flat dicts) --------------------------------------
        char_dict: dict[str, dict[str, Any]] = {}
        for entity in kernel.entities:
            if is_system_artifact_name(entity.name):
                continue
            if not _state_chapter_within_waterline(
                entity.last_seen_chapter, prior_chapter,
            ):
                continue
            char_dict[entity.name] = _entity_to_character_dict(entity)

        sorted_characters = sorted(
            char_dict.items(),
            key=lambda kv: (
                2 if kv[0] in involved_set else 0,
                mention_counts.get(kv[0], 0),
                1 if kv[1].get("alive", True) else 0,
                kv[0],
            ),
            reverse=True,
        )
        characters = dict(sorted_characters[: self._max_characters])

        # -- Active foreshadowing -----------------------------------------
        active_fs = [
            p for p in kernel.promise_ledger
            if p.status in ("planted", "hinted", "partially_paid")
            and int(p.planted_chapter or 0) <= prior_chapter
        ]
        active_fs = sorted(
            active_fs, key=lambda p: p.planted_chapter,
        )[: self._max_active_foreshadowing]

        # -- World facts ---------------------------------------------------
        world_facts: dict[str, str] = {}
        for wr in kernel.world_rules[-self._max_world_facts :]:
            world_facts[wr.rule_id] = wr.content

        # -- Previous chapter state ----------------------------------------
        prev_summary = kernel.chapter_summaries.get(for_chapter - 1, "")
        prev_exit_state = kernel.chapter_exit_states.get(for_chapter - 1)

        # -- Relationships -------------------------------------------------
        relationships = self._select_active_relationships(
            kernel=kernel,
            for_chapter=for_chapter,
            involved_characters=involved_characters,
            pov_character=pov_character,
            mention_counts=mention_counts,
        )

        # -- Plot threads --------------------------------------------------
        plot_threads = sorted(
            (
                thread for thread in kernel.plot_threads
                if _state_chapter_within_waterline(
                    thread.last_touched_chapter, prior_chapter,
                )
                and not (
                    thread.status == "active"
                    and for_chapter - thread.last_touched_chapter > 8
                )
            ),
            key=lambda thread: (thread.last_touched_chapter, thread.thread_id),
            reverse=True,
        )[: self._max_recent_events]

        # -- Immutable facts -----------------------------------------------
        immutable = self._extract_immutable_facts(
            kernel, for_chapter, world_rules=world_rules, story_bible=story_bible,
        )

        return KernelContext(
            characters=characters,
            recent_events=recent_events,
            active_foreshadowing=active_fs,
            active_relationships=relationships,
            active_plot_threads=plot_threads,
            world_facts=world_facts,
            previous_chapter_summary=prev_summary,
            previous_exit_state=prev_exit_state,
            must_carry_forward=(
                [
                    carry_forward_text(item)
                    for item in (prev_exit_state.must_carry_forward or [])
                    if carry_forward_text(item)
                ]
                if prev_exit_state is not None
                else []
            ),
            immutable_facts=immutable,
        )

    # -- Relationship selection --------------------------------------------

    def _select_active_relationships(
        self,
        *,
        kernel: StoryKernel,
        for_chapter: int,
        involved_characters: list[str] | None,
        pov_character: str | None,
        mention_counts: Counter[str],
    ) -> list[RelationshipState]:
        """Select and rank relationships relevant to *for_chapter*."""
        relation_cap = min(40, max(1, self._max_characters))
        if relation_cap <= 0:
            return []

        normalized_involved = {
            str(name or "").strip()
            for name in (involved_characters or [])
            if str(name or "").strip()
            and not is_system_artifact_name(str(name or "").strip())
        }
        pov = str(pov_character or "").strip()
        if pov and not is_system_artifact_name(pov):
            normalized_involved.add(pov)

        if not normalized_involved and mention_counts:
            for name, _ in mention_counts.most_common(8):
                n = str(name or "").strip()
                if n and not is_system_artifact_name(n):
                    normalized_involved.add(n)

        entity_names = {e.entity_id: e.name for e in kernel.entities}

        def _rel_to_state(rel: Any) -> RelationshipState | None:
            src_name = entity_names.get(rel.source_entity_id, rel.source_entity_id)
            tgt_name = entity_names.get(rel.target_entity_id, rel.target_entity_id)
            chars = sorted([src_name, tgt_name])
            if any(is_system_artifact_name(n) for n in chars):
                return None
            return RelationshipState(
                pair_id=rel.relationship_id,
                characters=chars,
                public_status=rel.label,
                trust=rel.trust,
                tension=rel.tension,
                dependency=getattr(rel, "dependency", 0.0),
                last_shift_event=getattr(rel, "shift_summary", ""),
                last_updated_chapter=rel.last_shift_chapter,
                notes=rel.notes,
            )

        def _bucket(rs: RelationshipState) -> int | None:
            chars = [
                str(n or "").strip() for n in rs.characters if str(n or "").strip()
            ]
            if len(chars) < 2:
                return None
            a, b = chars[0], chars[1]
            a_in = a in normalized_involved
            b_in = b in normalized_involved
            if a_in and b_in:
                return 0
            if a_in or b_in:
                recent_window = 2
                is_recent = (
                    rs.last_updated_chapter > 0
                    and rs.last_updated_chapter >= max(1, for_chapter - recent_window)
                )
                intensity = abs(float(rs.tension) - 0.5) + abs(float(rs.trust) - 0.5)
                if is_recent or intensity >= 0.45:
                    return 1
                return None
            if (
                rs.last_updated_chapter > 0
                and rs.last_updated_chapter >= max(1, for_chapter - 1)
            ):
                return 2
            return None

        def _score(rs: RelationshipState) -> tuple[float, ...]:
            chars = [
                str(n or "").strip() for n in rs.characters if str(n or "").strip()
            ]
            mentions = sum(float(mention_counts.get(name, 0)) for name in chars[:2])
            involves_pov = 1.0 if pov and pov in chars[:2] else 0.0
            recency = float(rs.last_updated_chapter or 0)
            intensity = abs(float(rs.tension) - 0.5) + abs(float(rs.trust) - 0.5)
            return (involves_pov, mentions, intensity, recency)

        bucketed: dict[int, list[RelationshipState]] = {0: [], 1: [], 2: []}
        for rel in kernel.relationships:
            rs = _rel_to_state(rel)
            if rs is None:
                continue
            if not _state_chapter_within_waterline(
                rs.last_updated_chapter, max(0, for_chapter - 1),
            ):
                continue
            group = _bucket(rs)
            if group is None:
                continue
            bucketed[group].append(rs)

        selected: list[RelationshipState] = []
        seen_pairs: set[str] = set()
        for group in (0, 1, 2):
            ranked = sorted(
                bucketed[group],
                key=lambda rs: (_score(rs), rs.pair_id),
                reverse=True,
            )
            for rs in ranked:
                if rs.pair_id in seen_pairs:
                    continue
                selected.append(rs)
                seen_pairs.add(rs.pair_id)
                if len(selected) >= relation_cap:
                    return selected

        if not selected:
            all_states: list[RelationshipState] = []
            for rel in kernel.relationships:
                rs = _rel_to_state(rel)
                if rs and _state_chapter_within_waterline(
                    rs.last_updated_chapter, max(0, for_chapter - 1),
                ):
                    all_states.append(rs)
            return sorted(
                all_states,
                key=lambda rs: (rs.last_updated_chapter, rs.pair_id),
                reverse=True,
            )[:relation_cap]
        return selected

    # -- Immutable facts ---------------------------------------------------

    @staticmethod
    def _extract_immutable_facts(
        kernel: StoryKernel,
        for_chapter: int,
        *,
        world_rules: list[str] | None = None,
        story_bible: StoryBible | None = None,
    ) -> list[str]:
        """Extract hard facts that must never be violated."""
        _WORLD_RULES_CAP = 8
        rules_facts: list[str] = []
        for rule in (world_rules or [])[:_WORLD_RULES_CAP]:
            rule_text = str(rule).strip()
            if rule_text:
                rules_facts.append(f"【世界规则】{rule_text}")

        _DEAD_CAP = 10
        dead_facts: list[str] = []
        for entity in kernel.entities:
            if entity.status == "destroyed" and _state_chapter_within_waterline(
                entity.last_seen_chapter, max(0, for_chapter - 1),
            ):
                dead_facts.append(f"【已死亡】{entity.name}不可出场行动或对话")
        dead_facts = dead_facts[:_DEAD_CAP]

        _FORESHADOW_CAP = 12
        fs_facts: list[str] = []
        prior_chapter = max(0, for_chapter - 1)
        for p in kernel.promise_ledger:
            if (
                p.status == "paid"
                and p.payoff_chapter
                and int(p.payoff_chapter or 0) <= prior_chapter
            ):
                fs_facts.append(
                    f"【已揭晓】伏笔「{p.description}」已在第{p.payoff_chapter}章揭示，不可重复揭示"
                )
            elif (
                p.status == "broken"
                and int(p.planted_chapter or 0) <= prior_chapter
            ):
                fs_facts.append(
                    f"【已废弃】伏笔「{p.description}」已被放弃，不可再提及"
                )
        fs_facts = fs_facts[:_FORESHADOW_CAP]

        _OVERDUE_CAP = 20
        overdue_facts: list[str] = []
        for p in kernel.promise_ledger:
            if (
                p.status in ("planted", "hinted", "partially_paid")
                and int(p.planted_chapter or 0) <= prior_chapter
                and for_chapter - p.planted_chapter >= 5
            ):
                overdue_facts.append(
                    f"【需推进】伏笔「{p.description}」已悬置{for_chapter - p.planted_chapter}章，本章应推进或呼应"
                )
        overdue_facts = overdue_facts[:_OVERDUE_CAP]

        _BANNED_INTENT_CAP = 15
        banned_intent_facts: list[str] = []
        if story_bible:
            for rule in (story_bible.banned_intent_rules or [])[:_BANNED_INTENT_CAP]:
                rule_text = str(rule).strip()
                if rule_text:
                    banned_intent_facts.append(f"【禁用意向】{rule_text}")

        _BANNED_PHRASE_CAP = 20
        banned_phrase_facts: list[str] = []
        for phrase in (kernel.banned_phrases or [])[:_BANNED_PHRASE_CAP]:
            phrase_text = str(phrase).strip()
            if phrase_text:
                banned_phrase_facts.append(f"【禁用表达】{phrase_text}")

        return (
            rules_facts + dead_facts + fs_facts + overdue_facts
            + banned_intent_facts + banned_phrase_facts
        )


__all__ = [
    "CanonContextDict",
    "KernelContext",
    "StoryKernelRetriever",
    "_entity_to_character_dict",
]
