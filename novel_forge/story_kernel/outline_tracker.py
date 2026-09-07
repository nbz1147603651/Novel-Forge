"""Outline Relationship Tracker — tracks character relationships during outline generation.

This module provides lightweight relationship tracking specifically designed for the
outline generation phase, enabling cross-chapter awareness without the full memory system.

Key features:
- Extracts and tracks relationships from CharacterBible
- Monitors relationship changes as chapters are generated
- Maintains a timeline of relationship shifts
- Provides context for future chapter generation

Migrated from ``novel_forge.canon.outline_tracker``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from novel_forge.common.constants import TaskType
from novel_forge.core.schemas.bible import CharacterBible
from novel_forge.core.schemas.outline import ChapterOutline, NarrativeBlueprint
from novel_forge.gateway.types import ModelRequest

_log = logging.getLogger(__name__)


@dataclass
class RelationshipEntry:
    """A single relationship between two characters."""

    character_a: str
    character_b: str
    description: str
    chapter_introduced: int = 0
    relationship_type: str = ""  # family, friend, enemy, romantic, rival, etc.
    trust_level: float = 0.5  # 0.0 - 1.0
    tension_level: float = 0.5  # 0.0 - 1.0
    shift_events: list[str] = field(default_factory=list)


@dataclass
class CharacterOutlineState:
    """Tracks a character's state throughout the outline."""

    name: str
    role: str
    first_appearance: int = 0
    arc_progress: list[str] = field(default_factory=list)
    appearances: list[int] = field(default_factory=list)
    relationships: list[str] = field(default_factory=list)  # Other character names


@dataclass
class ThemeOccurrence:
    """Tracks where themes appear in the outline."""

    theme: str
    chapter: int
    context: str = ""


@dataclass
class PlotThread:
    """Tracks an active plot thread."""

    thread_id: str
    name: str
    introduced_chapter: int
    status: str = "active"  # active, resolved, abandoned
    resolved_chapter: int = 0
    key_events: list[str] = field(default_factory=list)


@dataclass
class KeyEvent:
    """A significant event in the outline."""

    chapter: int
    event_type: str  # conflict, revelation, decision, death, meeting, etc.
    description: str
    characters_involved: list[str] = field(default_factory=list)
    implications: str = ""


@dataclass
class OutlineContext:
    """Aggregated context from outline for generation guidance."""

    relationships: list[RelationshipEntry] = field(default_factory=list)
    character_states: dict[str, CharacterOutlineState] = field(default_factory=dict)
    themes: list[ThemeOccurrence] = field(default_factory=list)
    threads: list[PlotThread] = field(default_factory=list)
    key_events: list[KeyEvent] = field(default_factory=list)
    unresolved_questions: list[str] = field(default_factory=list)


class OutlineTracker:
    """Tracks character relationships and outline evolution during chapter generation.

    This tracker operates during the outline phase, maintaining awareness of:
    - Character relationships and their evolution
    - Theme appearances and development
    - Active plot threads
    - Key events across chapters

    It provides context for generating coherent future chapters without
    requiring the full memory system.
    """

    TRUST_KEYWORDS_HIGH = [
        "信任", "忠诚", "坦诚", "可靠", "托付", "信赖",
        "放心", "交心", "守信",
        "trust", "loyal", "reliable", "faithful", "honest"
    ]

    TRUST_KEYWORDS_LOW = [
        "怀疑", "猜忌", "不信任", "背叛", "欺骗", "隐瞒",
        "虚伪", "出卖", "辜负",
        "distrust", "suspicion", "betray", "deceive"
    ]

    TRUST_INDICATORS = TRUST_KEYWORDS_HIGH + TRUST_KEYWORDS_LOW

    TENSION_KEYWORDS_HIGH = [
        "紧张", "冲突", "对抗", "敌对", "争执", "争吵",
        "矛盾", "对立", "分裂", "隔阂", "怨恨",
        "tension", "conflict", "confrontation", "hostile", "rivalry"
    ]

    TENSION_KEYWORDS_LOW = [
        "和平", "和谐", "友好", "和解", "融洽", "和睦",
        "亲密", "默契",
        "peaceful", "harmonious", "friendly", "reconciliation"
    ]

    TENSION_INDICATORS = TENSION_KEYWORDS_HIGH + TENSION_KEYWORDS_LOW

    NEGATIVE_PREFIXES = ["不", "没", "非", "无", "假装", "假", "虚"]

    def __init__(self) -> None:
        self._relationships: dict[str, RelationshipEntry] = {}
        self._character_states: dict[str, CharacterOutlineState] = {}
        self._themes: list[ThemeOccurrence] = []
        self._threads: dict[str, PlotThread] = {}
        self._key_events: list[KeyEvent] = []
        self._unresolved_questions: list[str] = []

    def initialize_from_bible(self, bible: CharacterBible) -> None:
        """Initialize tracker with character relationships from CharacterBible."""
        for char in bible.characters:
            if char.name not in self._character_states:
                self._character_states[char.name] = CharacterOutlineState(
                    name=char.name,
                    role=char.role,
                )

            for other_name, rel_desc in char.relationships.items():
                pair_key = self._make_pair_key(char.name, other_name)
                if pair_key not in self._relationships:
                    rel_type = self._infer_relationship_type(rel_desc)
                    self._relationships[pair_key] = RelationshipEntry(
                        character_a=char.name,
                        character_b=other_name,
                        description=rel_desc,
                        relationship_type=rel_type,
                    )
                    self._character_states[char.name].relationships.append(other_name)

    def initialize_from_blueprint(self, blueprint: NarrativeBlueprint) -> None:
        """Initialize tracker with themes and plot threads from NarrativeBlueprint."""
        for phase in blueprint.narrative_phases:
            for theme in phase.key_events:
                self._themes.append(ThemeOccurrence(
                    theme=theme,
                    chapter=phase.chapter_start,
                    context=f"phase: {phase.phase_name}",
                ))

        for subplot in blueprint.subplot_plan:
            thread = PlotThread(
                thread_id=subplot.name,
                name=subplot.name,
                introduced_chapter=min(subplot.involved_chapters) if subplot.involved_chapters else 1,
                key_events=[subplot.description],
            )
            self._threads[subplot.name] = thread

        for arc in blueprint.character_arcs:
            for milestone in arc.milestones:
                if milestone.chapter_start > 0:
                    self._add_unresolved_question(
                        f"角色弧光 '{arc.character}' 的发展: {milestone.description}"
                    )

    def update_from_chapter(
        self,
        chapter: ChapterOutline,
        extracted_info: dict[str, Any] | None = None,
    ) -> None:
        """Update tracker with information from a newly generated chapter outline.

        Args:
            chapter: The chapter outline that was just generated
            extracted_info: Optional extracted information about relationships,
                          themes, threads, etc. from the chapter
        """
        chapter_num = chapter.chapter_number

        for char_name in self._extract_characters_from_outline(chapter):
            if char_name not in self._character_states:
                self._character_states[char_name] = CharacterOutlineState(
                    name=char_name,
                    role="supporting",
                    first_appearance=chapter_num,
                )
            self._character_states[char_name].appearances.append(chapter_num)

        self._track_plot_points(chapter, chapter_num)

        if extracted_info:
            self._apply_extracted_info(extracted_info, chapter_num)

        self._infer_implicit_events(chapter)

    def _apply_extracted_info(self, info: dict[str, Any], chapter: int) -> None:
        """Apply extracted relationship/theme/thread information."""
        for rel_data in info.get("relationship_changes", []):
            self._update_relationship(
                rel_data["character_a"],
                rel_data["character_b"],
                rel_data.get("description", ""),
                chapter,
                rel_data.get("shift_event", ""),
            )

        for theme in info.get("themes", []):
            self._themes.append(ThemeOccurrence(
                theme=theme,
                chapter=chapter,
                context=info.get("theme_context", ""),
            ))

        for thread_name in info.get("resolved_threads", []):
            if thread_name in self._threads:
                self._threads[thread_name].status = "resolved"
                self._threads[thread_name].resolved_chapter = chapter

    def _classify_plot_point(self, plot_point: str) -> str:
        """Classify a plot point into an event type based on content analysis.

        Args:
            plot_point: The plot point text to classify

        Returns:
            Event type string: conflict, revelation, decision, death, meeting,
            transition, tension, resolution, or unknown
        """
        text_lower = plot_point.lower()

        conflict_keywords = [
            "对抗", "冲突", "战斗", "争夺", "竞争", "挑战", "争执", "斗争",
            "fight", "battle", "conflict", "confront", "rival", "compete",
        ]
        if any(kw in text_lower for kw in conflict_keywords):
            return "conflict"

        revelation_keywords = [
            "揭露", "揭示", "发现", "真相", "秘密", "曝光", "暴露", "揭晓",
            "reveal", "discover", "truth", "secret", "expose", "uncover",
        ]
        if any(kw in text_lower for kw in revelation_keywords):
            return "revelation"

        decision_keywords = [
            "决定", "选择", "抉择", "决心", "计划", "决策",
            "decision", "choose", "decide", "choice", "resolve",
        ]
        if any(kw in text_lower for kw in decision_keywords):
            return "decision"

        death_keywords = [
            "死亡", "去世", "牺牲", "灭亡", "终结",
            "death", "die", "sacrifice", "kill", "dead",
        ]
        if any(kw in text_lower for kw in death_keywords):
            return "death"

        meeting_keywords = [
            "相遇", "重逢", "相会", "结识", "遇见", "见面", "偶遇",
            "meet", "encounter", "reunion", "gathering",
        ]
        if any(kw in text_lower for kw in meeting_keywords):
            return "meeting"

        tension_keywords = [
            "紧张", "危机", "困境", "难题", "威胁", "危机",
            "tension", "crisis", "danger", "threat", "trouble",
        ]
        if any(kw in text_lower for kw in tension_keywords):
            return "tension"

        resolution_keywords = [
            "解决", "化解", "和解", "平息", "成功", "突破",
            "resolve", "solve", "success", "breakthrough", "resolution",
        ]
        if any(kw in text_lower for kw in resolution_keywords):
            return "resolution"

        transition_keywords = [
            "过渡", "转折", "变化", "发展", "开始", "结束",
            "transition", "change", "develop", "start", "end",
        ]
        if any(kw in text_lower for kw in transition_keywords):
            return "transition"

        return "unknown"

    def _track_plot_points(self, chapter: ChapterOutline, chapter_num: int) -> None:
        """Track plot points and infer potential events."""
        for point in chapter.main_plot_points:
            event_type = self._classify_plot_point(point)
            event = KeyEvent(
                chapter=chapter_num,
                event_type=event_type,
                description=point,
                characters_involved=self._extract_characters_from_outline(chapter),
            )
            self._key_events.append(event)

            if "悬念" in point or "疑问" in point or "?" in point:
                self._add_unresolved_question(f"第{chapter_num}章悬念: {point}")

    def _infer_implicit_events(self, chapter: ChapterOutline) -> None:
        """Infer implicit events from chapter structure."""
        goal_text = chapter.goal.lower()

        conflict_indicators = ["对抗", "冲突", "战斗", "竞争", "争夺", "挑战"]
        if any(ind in goal_text for ind in conflict_indicators):
            event = KeyEvent(
                chapter=chapter.chapter_number,
                event_type="conflict",
                description=f"涉及冲突: {chapter.goal}",
                characters_involved=self._extract_characters_from_outline(chapter),
            )
            self._key_events.append(event)

        reveal_indicators = ["揭露", "揭示", "发现", "真相", "秘密", "曝光"]
        if any(ind in goal_text for ind in reveal_indicators):
            event = KeyEvent(
                chapter=chapter.chapter_number,
                event_type="revelation",
                description=f"涉及揭示: {chapter.goal}",
                characters_involved=self._extract_characters_from_outline(chapter),
            )
            self._key_events.append(event)
            self._add_unresolved_question(f"第{chapter.chapter_number}章的揭示可能带来后续影响")

    def _update_relationship(
        self,
        char_a: str,
        char_b: str,
        description: str,
        chapter: int,
        shift_event: str = "",
    ) -> None:
        """Update or create a relationship entry."""
        pair_key = self._make_pair_key(char_a, char_b)

        combined_text = f"{description} {shift_event or ''}"

        should_update_trust = any(indicator in combined_text for indicator in self.TRUST_INDICATORS)
        should_update_tension = any(indicator in combined_text for indicator in self.TENSION_INDICATORS)

        if pair_key in self._relationships:
            rel = self._relationships[pair_key]
            rel.description = description
            if shift_event:
                rel.shift_events.append(f"Ch{chapter}: {shift_event}")
            if should_update_trust:
                rel.trust_level = self._estimate_trust_level(combined_text)
            if should_update_tension:
                rel.tension_level = self._estimate_tension_level(combined_text)
        else:
            trust_level = self._estimate_trust_level(combined_text) if should_update_trust else 0.5
            tension_level = self._estimate_tension_level(combined_text) if should_update_tension else 0.5
            self._relationships[pair_key] = RelationshipEntry(
                character_a=char_a,
                character_b=char_b,
                description=description,
                chapter_introduced=chapter,
                trust_level=trust_level,
                tension_level=tension_level,
                shift_events=[f"Ch{chapter}: {shift_event}"] if shift_event else [],
            )

    def _add_unresolved_question(self, question: str) -> None:
        """Add an unresolved question or thread."""
        if question not in self._unresolved_questions:
            self._unresolved_questions.append(question)

    def get_context_for_chapter(self, current_chapter: int) -> OutlineContext:
        """Get aggregated context for generating the next chapter.

        This is the main method for retrieving context to guide outline generation.
        """
        active_characters = self._get_active_characters(current_chapter)
        active_relationships = self._get_active_relationships(active_characters)
        recent_themes = self._get_recent_themes(current_chapter, lookback=3)
        active_threads = self._get_active_threads()
        recent_events = self._get_recent_key_events(current_chapter, lookback=3)

        return OutlineContext(
            relationships=active_relationships,
            character_states={
                name: state
                for name, state in self._character_states.items()
                if name in active_characters
            },
            themes=recent_themes,
            threads=active_threads,
            key_events=recent_events,
            unresolved_questions=self._unresolved_questions[-10:],
        )

    def get_relationship_summary(self, current_chapter: int) -> str:
        """Generate a human-readable relationship summary for prompt injection."""
        active_chars = self._get_active_characters(current_chapter)
        active_rels = self._get_active_relationships(active_chars)

        if not active_rels:
            return "暂无关系变化记录。"

        lines = ["【当前关系状态】"]
        for rel in active_rels:
            if rel.shift_events:
                last_shift = rel.shift_events[-1]
                lines.append(f"- {rel.character_a} ↔ {rel.character_b}: {rel.description} (最近变化: {last_shift})")
            else:
                lines.append(f"- {rel.character_a} ↔ {rel.character_b}: {rel.description}")

        return "\n".join(lines)

    def get_themes_summary(self, current_chapter: int) -> str:
        """Generate a theme summary for prompt injection."""
        recent = self._get_recent_themes(current_chapter, lookback=5)

        if not recent:
            return "暂无主题出现记录。"

        theme_counts: dict[str, list[int]] = {}
        for occ in recent:
            if occ.theme not in theme_counts:
                theme_counts[occ.theme] = []
            theme_counts[occ.theme].append(occ.chapter)

        lines = ["【主题出现情况】"]
        for theme, chapters in theme_counts.items():
            lines.append(f"- {theme}: 第{', '.join(str(c) for c in chapters)}章")

        return "\n".join(lines)

    def get_key_events_summary(self, current_chapter: int) -> str:
        """Generate a key events summary for prompt injection."""
        recent = self._get_recent_key_events(current_chapter, lookback=5)

        if not recent:
            return "暂无关键事件记录。"

        lines = ["【最近关键事件】"]
        for event in recent:
            char_str = ", ".join(event.characters_involved[:3]) if event.characters_involved else "未指定"
            lines.append(f"- 第{event.chapter}章 [{event.event_type}] {event.description} (涉及: {char_str})")

        return "\n".join(lines)

    def get_unresolved_threads_summary(self) -> str:
        """Generate unresolved threads summary for prompt injection."""
        if not self._unresolved_questions:
            return "暂无待解决的悬念。"

        lines = ["【待解决悬念】"]
        for q in self._unresolved_questions[-5:]:
            lines.append(f"- {q}")

        return "\n".join(lines)

    def add_relationship(
        self,
        char_a: str,
        char_b: str,
        description: str,
        chapter: int,
        shift_event: str = "",
    ) -> None:
        """Add or update a relationship entry.

        This is the public interface for adding relationship changes,
        delegating to _update_relationship for consistent logic.

        Args:
            char_a: First character name
            char_b: Second character name
            description: Description of the relationship
            chapter: Chapter number where this relationship change occurs
            shift_event: Optional event that caused the shift
        """
        self._update_relationship(char_a, char_b, description, chapter, shift_event)

    def add_theme(self, theme: str, chapter: int, context: str = "") -> None:
        """Add a theme occurrence.

        This is the public interface for adding theme occurrences,
        replacing the need to directly append to _themes list.

        Args:
            theme: The theme name
            chapter: Chapter number where this theme appears
            context: Optional context about where/why this theme appears
        """
        self._themes.append(ThemeOccurrence(
            theme=theme,
            chapter=chapter,
            context=context or "direct_add",
        ))

    def get_context_for_prompt(self, current_chapter: int) -> dict[str, str]:
        """Get all context summaries formatted for prompt injection.

        Returns a dictionary with keys:
        - relationship_summary
        - themes_summary
        - key_events_summary
        - unresolved_summary
        """
        return {
            "relationship_summary": self.get_relationship_summary(current_chapter),
            "themes_summary": self.get_themes_summary(current_chapter),
            "key_events_summary": self.get_key_events_summary(current_chapter),
            "unresolved_summary": self.get_unresolved_threads_summary(),
        }

    def _get_active_characters(self, current_chapter: int) -> set[str]:
        """Get characters that have appeared recently."""
        active = set()
        for name, state in self._character_states.items():
            recent_appearances = [a for a in state.appearances if a <= current_chapter]
            if recent_appearances:
                last_appearance = max(recent_appearances)
                if current_chapter - last_appearance <= 5:
                    active.add(name)
        return active

    def _get_active_relationships(self, active_chars: set[str]) -> list[RelationshipEntry]:
        """Get relationships involving active characters."""
        return [
            rel for rel in self._relationships.values()
            if rel.character_a in active_chars or rel.character_b in active_chars
        ]

    def _get_recent_themes(self, current_chapter: int, lookback: int) -> list[ThemeOccurrence]:
        """Get themes that appeared recently."""
        start = max(1, current_chapter - lookback)
        return [t for t in self._themes if start <= t.chapter <= current_chapter]

    def _get_active_threads(self) -> list[PlotThread]:
        """Get currently active plot threads."""
        return [t for t in self._threads.values() if t.status == "active"]

    def _get_recent_key_events(
        self,
        current_chapter: int,
        lookback: int,
    ) -> list[KeyEvent]:
        """Get key events from recent chapters."""
        start = max(1, current_chapter - lookback)
        return [e for e in self._key_events if start <= e.chapter <= current_chapter]

    def _extract_characters_from_outline(self, chapter: ChapterOutline) -> list[str]:
        """Extract character names from chapter outline."""
        characters = []

        if chapter.pov_character:
            characters.append(chapter.pov_character)

        for beat in chapter.beats_summary:
            found = self._find_character_names(beat)
            characters.extend(found)

        for point in chapter.main_plot_points:
            found = self._find_character_names(point)
            characters.extend(found)

        for point in chapter.subplot_points:
            found = self._find_character_names(point)
            characters.extend(found)

        return list(set(characters))

    def _find_character_names(self, text: str) -> list[str]:
        """Attempt to find character names in text."""
        found = []

        for name in self._character_states.keys():
            if name in text:
                found.append(name)

        return found

    def _make_pair_key(self, char_a: str, char_b: str) -> str:
        """Create a canonical key for a character pair."""
        return "|".join(sorted([char_a.lower(), char_b.lower()]))

    def _infer_relationship_type(self, description: str) -> str:
        """Infer relationship type from description."""
        desc_lower = description.lower()

        family_keywords = ["父亲", "母亲", "儿子", "女儿", "兄弟", "姐妹", "家庭", "亲人", "parent", "sibling", "family"]
        if any(kw in desc_lower for kw in family_keywords):
            return "family"

        romantic_keywords = ["爱", "恋人", "情人", "追求", "伴侣", "love", "romantic", "date", "partner"]
        if any(kw in desc_lower for kw in romantic_keywords):
            return "romantic"

        enemy_keywords = ["敌人", "敌对", "仇恨", "对抗", "enemy", "hostile", "rival"]
        if any(kw in desc_lower for kw in enemy_keywords):
            return "enemy"

        friend_keywords = ["朋友", "友好", "伙伴", "同盟", "friend", "ally", "support"]
        if any(kw in desc_lower for kw in friend_keywords):
            return "friend"

        return "neutral"

    def _estimate_trust_level(self, description: str) -> float:
        """Estimate trust level from description using enhanced keyword detection."""
        desc_lower = description.lower()

        for kw in self.TRUST_KEYWORDS_LOW:
            if kw in desc_lower:
                for neg in self.NEGATIVE_PREFIXES:
                    if f"{neg}{kw}" in desc_lower or f"不是{kw}" in desc_lower:
                        return 0.7
                return 0.2

        for kw in self.TRUST_KEYWORDS_HIGH:
            if kw in desc_lower:
                for neg in self.NEGATIVE_PREFIXES:
                    if f"{neg}{kw}" in desc_lower or f"不是{kw}" in desc_lower:
                        return 0.3
                return 0.8

        return 0.5

    def _estimate_tension_level(self, description: str) -> float:
        """Estimate tension level from description using enhanced keyword detection."""
        desc_lower = description.lower()

        for kw in self.TENSION_KEYWORDS_LOW:
            if kw in desc_lower:
                for neg in self.NEGATIVE_PREFIXES[:4]:
                    if f"{neg}{kw}" in desc_lower:
                        return 0.7
                return 0.2

        for kw in self.TENSION_KEYWORDS_HIGH:
            if kw in desc_lower:
                for neg in self.NEGATIVE_PREFIXES[:4]:
                    if f"{neg}{kw}" in desc_lower:
                        return 0.3
                return 0.8

        return 0.5

    def _extract_theme_keywords_from_blueprint(self, blueprint: NarrativeBlueprint) -> list[str]:
        """Extract theme keywords from narrative blueprint."""
        themes = []

        for phase in blueprint.narrative_phases:
            themes.extend(phase.key_events)
            if phase.description:
                themes.append(phase.description)

        for turning_point in blueprint.key_turning_points:
            if turning_point.description:
                themes.append(turning_point.description)

        return themes

    def prune_for_volume(
        self,
        current_volume: int,
        chapters_per_volume: int = 20,
    ) -> dict[str, int]:
        """Prune internal state at volume boundary to prevent unbounded growth.

        - ``_key_events``: keep events from the last 3 volumes + all major events
        - ``_themes``: merge same-theme occurrences, keep only chapter counts
        - ``_unresolved_questions``: archive answered questions

        Returns a dict with counts of pruned items per category.
        """
        stats: dict[str, int] = {}
        cutoff_chapter = max(1, (current_volume - 3)) * chapters_per_volume

        # --- 1. Key events: keep recent 3 volumes + all "major" events ---
        major_types = {"death", "revelation", "decision"}
        before = len(self._key_events)
        self._key_events = [
            e for e in self._key_events
            if e.chapter >= cutoff_chapter or e.event_type in major_types
        ]
        stats["key_events_pruned"] = before - len(self._key_events)

        # --- 2. Themes: merge same-theme occurrences into count ---
        theme_map: dict[str, list[int]] = {}
        for occ in self._themes:
            if occ.theme not in theme_map:
                theme_map[occ.theme] = []
            theme_map[occ.theme].append(occ.chapter)
        before_themes = len(self._themes)
        self._themes = []
        for theme, chapters in theme_map.items():
            recent = [c for c in chapters if c >= cutoff_chapter]
            if recent:
                self._themes.append(ThemeOccurrence(
                    theme=theme,
                    chapter=max(recent),
                    context=f"count={len(chapters)}, recent_chapters={recent[-5:]}",
                ))
        stats["themes_merged"] = before_themes - len(self._themes)

        # --- 3. Unresolved questions: remove answered ones ---
        before_questions = len(self._unresolved_questions)
        self._unresolved_questions = [
            q for q in self._unresolved_questions
            if "已解决" not in q and "resolved" not in q.lower()
        ]
        stats["questions_archived"] = before_questions - len(self._unresolved_questions)

        # --- 4. Resolved threads: prune old resolved threads ---
        before_threads = len(self._threads)
        self._threads = {
            tid: t for tid, t in self._threads.items()
            if t.status == "active" or t.resolved_chapter >= cutoff_chapter
        }
        stats["threads_pruned"] = before_threads - len(self._threads)

        _log.info("outline_tracker_prune | volume=%d | %s", current_volume, stats)
        return stats

    def to_dict(self) -> dict[str, Any]:
        """Serialize tracker state for persistence."""
        return {
            "relationships": [
                {
                    "character_a": r.character_a,
                    "character_b": r.character_b,
                    "description": r.description,
                    "chapter_introduced": r.chapter_introduced,
                    "relationship_type": r.relationship_type,
                    "trust_level": r.trust_level,
                    "tension_level": r.tension_level,
                    "shift_events": r.shift_events,
                }
                for r in self._relationships.values()
            ],
            "character_states": {
                name: {
                    "name": s.name,
                    "role": s.role,
                    "first_appearance": s.first_appearance,
                    "arc_progress": s.arc_progress,
                    "appearances": s.appearances,
                    "relationships": s.relationships,
                }
                for name, s in self._character_states.items()
            },
            "themes": [
                {"theme": t.theme, "chapter": t.chapter, "context": t.context}
                for t in self._themes
            ],
            "threads": [
                {
                    "thread_id": t.thread_id,
                    "name": t.name,
                    "introduced_chapter": t.introduced_chapter,
                    "status": t.status,
                    "resolved_chapter": t.resolved_chapter,
                    "key_events": t.key_events,
                }
                for t in self._threads.values()
            ],
            "key_events": [
                {
                    "chapter": e.chapter,
                    "event_type": e.event_type,
                    "description": e.description,
                    "characters_involved": e.characters_involved,
                    "implications": e.implications,
                }
                for e in self._key_events
            ],
            "unresolved_questions": self._unresolved_questions,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OutlineTracker":
        """Deserialize tracker state from persistence."""
        tracker = cls()

        for rel_data in data.get("relationships", []):
            tracker._relationships[tracker._make_pair_key(
                rel_data["character_a"], rel_data["character_b"]
            )] = RelationshipEntry(**rel_data)

        for name, state_data in data.get("character_states", {}).items():
            tracker._character_states[name] = CharacterOutlineState(**state_data)

        for theme_data in data.get("themes", []):
            tracker._themes.append(ThemeOccurrence(**theme_data))

        for thread_data in data.get("threads", []):
            tracker._threads[thread_data["thread_id"]] = PlotThread(**thread_data)

        for event_data in data.get("key_events", []):
            tracker._key_events.append(KeyEvent(**event_data))

        tracker._unresolved_questions = data.get("unresolved_questions", [])

        return tracker


@dataclass
class LLMExtractionResult:
    """Result from LLM-based relationship extraction."""

    relationship_changes: list[dict[str, Any]] = field(default_factory=list)
    new_relationships: list[dict[str, Any]] = field(default_factory=list)
    resolved_relationships: list[str] = field(default_factory=list)
    themes: list[str] = field(default_factory=list)
    new_questions: list[str] = field(default_factory=list)
    confidence: float = 0.5
    attempts: int = 0
    error: str = ""


@dataclass
class ExtractionQuality:
    """Quality indicator for rule-based extraction."""

    has_relationship_keywords: bool = False
    detected_changes: int = 0
    confidence: float = 0.0
    needs_llm_boost: bool = False


class HybridOutlineTracker:
    """Hybrid tracker that combines rule-based extraction with optional LLM enhancement.

    This tracker addresses the synchronization issue between extraction and generation:
    - The LLM enhancement is triggered based on BATCH size, not chapter count
    - If no changes detected for N consecutive batches (N = batch_size), trigger LLM
    - This ensures LLM insights are available for the NEXT batch generation

    Flow:
    1. Generate batch N chapters using tracker context
    2. After generation, rule-based extraction runs
    3. If no changes detected in batch N, increment "no-change batch" counter
    4. If counter >= batch_size, trigger LLM enhancement before next batch
    5. LLM results update tracker, which feeds into batch N+1 generation
    """

    def __init__(
        self,
        batch_size: int = 5,
        llm_extraction_enabled: bool = True,
        llm_threshold_batches: int | None = None,
    ) -> None:
        """Initialize hybrid tracker.

        Args:
            batch_size: Number of chapters per generation batch (同步关键参数)
            llm_extraction_enabled: Whether to enable LLM enhancement
            llm_threshold_batches: Number of no-change batches before LLM triggers.
                                  None = use batch_size (recommended)
        """
        self._rule_tracker = OutlineTracker()
        self._batch_size = batch_size
        self._llm_enabled = llm_extraction_enabled
        self._llm_threshold = llm_threshold_batches if llm_threshold_batches is not None else batch_size
        self._consecutive_no_change_batches = 0
        self._last_llm_chapter = 0
        self._pending_llm_extraction: LLMExtractionResult | None = None

    @property
    def batch_size(self) -> int:
        """Get current batch size."""
        return self._batch_size

    @batch_size.setter
    def batch_size(self, value: int) -> None:
        """Update batch size (useful if batch size changes mid-generation)."""
        self._batch_size = value
        if self._llm_threshold == self._consecutive_no_change_batches:
            self._llm_threshold = value

    def initialize_from_bible(self, bible: CharacterBible) -> None:
        """Initialize tracker with character relationships from CharacterBible."""
        self._rule_tracker.initialize_from_bible(bible)

    def initialize_from_blueprint(self, blueprint: NarrativeBlueprint) -> None:
        """Initialize tracker with themes and plot threads from NarrativeBlueprint."""
        self._rule_tracker.initialize_from_blueprint(blueprint)

    def initialize_from_existing_chapters(self, chapters: list[ChapterOutline]) -> None:
        """Initialize tracker with existing chapters (for resume from breakpoint).

        This method updates the tracker with chapters that were already generated,
        so they can be excluded from LLM analysis and rule-based extraction.

        Args:
            chapters: Already generated chapters
        """
        for chapter in chapters:
            self._rule_tracker.update_from_chapter(chapter)

    def update_from_batch(
        self,
        chapters: list[ChapterOutline],
        extracted_info: dict[str, Any] | None = None,
        ctx: Any = None,
    ) -> dict[str, Any]:
        """Update tracker with a batch of chapters.

        This is the main entry point for batch-level updates.
        It coordinates between rule-based extraction and LLM enhancement.

        Args:
            chapters: List of chapter outlines from this batch
            extracted_info: Optional pre-extracted information (e.g., from LLM)
            ctx: Context for LLM calls (router, builder, etc.)

        Returns:
            Dictionary with:
            - needs_llm: Whether LLM enhancement was triggered
            - llm_result: Result from LLM extraction (if triggered)
            - extraction_quality: Quality indicator for rule-based extraction
        """
        result = {
            "needs_llm": False,
            "llm_result": None,
            "extraction_quality": ExtractionQuality(),
            "changes_detected": 0,
            "llm_from_chapter": 0,
            "last_llm_chapter": 0,
        }

        chapter_nums = [ch.chapter_number for ch in chapters]
        last_chapter = max(chapter_nums) if chapter_nums else 0
        previous_llm_chapter = self._last_llm_chapter

        rule_quality = self._evaluate_extraction_quality(chapters)
        result["extraction_quality"] = rule_quality
        result["changes_detected"] = rule_quality.detected_changes

        for chapter in chapters:
            self._rule_tracker.update_from_chapter(chapter, extracted_info)

        if extracted_info:
            self._consecutive_no_change_batches = 0
            result["changes_detected"] = len(extracted_info.get("relationship_changes", []))
        else:
            if rule_quality.detected_changes == 0:
                self._consecutive_no_change_batches += 1
            else:
                self._consecutive_no_change_batches = 0

        should_trigger_llm = (
            self._llm_enabled
            and self._consecutive_no_change_batches >= self._llm_threshold
            and self._last_llm_chapter < last_chapter
        )

        if should_trigger_llm:
            result["needs_llm"] = True
            result["llm_from_chapter"] = previous_llm_chapter + 1
            result["last_llm_chapter"] = last_chapter
            self._consecutive_no_change_batches = 0
            self._last_llm_chapter = last_chapter

        return result

    async def trigger_llm_extraction(
        self,
        chapters: list[ChapterOutline],
        ctx: Any,
    ) -> LLMExtractionResult:
        """Trigger LLM-based relationship extraction.

        This should be called after update_from_batch returns needs_llm=True.

        Args:
            chapters: Chapters that need LLM analysis
            ctx: Runtime context with router/builder

        Returns:
            LLMExtractionResult with extracted relationships and themes
        """
        extraction = await self._llm_extract_relationships(chapters, ctx)
        self._pending_llm_extraction = extraction

        for chapter in chapters:
            self._rule_tracker.update_from_chapter(chapter, {
                "relationship_changes": extraction.relationship_changes,
                "themes": extraction.themes,
                "resolved_threads": extraction.resolved_relationships,
            })

        return extraction

    def apply_pending_llm_result(self) -> None:
        """Apply pending LLM extraction result to the tracker.

        Call this after LLM extraction completes to update the tracker.
        """
        if self._pending_llm_extraction:
            for rel_data in self._pending_llm_extraction.relationship_changes:
                self._rule_tracker.add_relationship(
                    rel_data["character_a"],
                    rel_data["character_b"],
                    rel_data.get("description", ""),
                    0,
                    rel_data.get("shift_event", ""),
                )

            for theme in self._pending_llm_extraction.themes:
                self._rule_tracker.add_theme(
                    theme=theme,
                    chapter=self._last_llm_chapter,
                    context="llm_extraction",
                )

            self._pending_llm_extraction = None

    def _evaluate_extraction_quality(
        self,
        chapters: list[ChapterOutline],
    ) -> ExtractionQuality:
        """Evaluate the quality of rule-based extraction on a batch."""
        quality = ExtractionQuality()

        relationship_keywords = [
            "关系", "信任", "冲突", "友谊", "爱情", "敌意", "矛盾", "和解",
            "背叛", "同盟", "家人", "敌人", "相遇", "重逢", "分别",
        ]

        total_text = " ".join([
            ch.title or ""
            + " ".join(ch.main_plot_points or [])
            + " ".join(ch.beats_summary or [])
            for ch in chapters
        ]).lower()

        quality.has_relationship_keywords = any(kw in total_text for kw in relationship_keywords)

        quality.detected_changes = len([
            ch for ch in chapters
            if any(kw in (ch.title or "").lower() for kw in ["变化", "转折", "冲突", "揭示"])
        ])

        if not quality.has_relationship_keywords:
            quality.confidence = 0.2
            quality.needs_llm_boost = True
        elif quality.detected_changes == 0:
            quality.confidence = 0.5
            quality.needs_llm_boost = True
        else:
            quality.confidence = 0.8
            quality.needs_llm_boost = False

        return quality

    async def _llm_extract_relationships(
        self,
        chapters: list[ChapterOutline],
        ctx: Any,
    ) -> LLMExtractionResult:
        """Extract relationships using LLM.

        Args:
            chapters: Chapters to analyze
            ctx: Runtime context

        Returns:
            LLMExtractionResult with extracted data
        """
        if not chapters:
            return LLMExtractionResult()

        chapter_summaries = []
        for ch in chapters:
            summary = f"""第{ch.chapter_number}章: {ch.title or '未命名'}
POV: {ch.pov_character or '未指定'}
情节点: {'; '.join(ch.main_plot_points[:3]) if ch.main_plot_points else '无'}
副线: {'; '.join(ch.subplot_points[:2]) if ch.subplot_points else '无'}
"""
            chapter_summaries.append(summary)

        all_text = "\n".join(chapter_summaries)

        existing_rels = []
        for rel in self._rule_tracker._relationships.values():
            existing_rels.append({
                "pair": f"{rel.character_a} ↔ {rel.character_b}",
                "description": rel.description,
                "type": rel.relationship_type,
                "last_shift": rel.shift_events[-1] if rel.shift_events else "无",
            })

        existing_rels_text = "\n".join([
            f"- {r['pair']}: {r['description']} ({r['type']}) | 最近变化: {r['last_shift']}"
            for r in existing_rels
        ]) or "暂无已知关系"

        prompt = f"""你是一个专业的小说角色关系分析专家。请分析以下章节大纲，提取角色关系的变化。

【待分析章节】
{all_text}

【当前已知关系】
{existing_rels_text}

请仔细阅读章节内容，识别：
1. 任何关系的变化（如信任增加/减少、冲突爆发/解决）
2. 新出现的关系
3. 已经解决/终结的关系
4. 出现的新主题或母题
5. 产生的新的悬念或疑问

请以JSON格式输出你的分析：
{{
    "relationship_changes": [
        {{
            "character_a": "角色A名称",
            "character_b": "角色B名称",
            "description": "关系描述（30字内）",
            "shift_event": "具体变化事件（50字内）",
            "trust_change": "increase/decrease/stable",
            "tension_change": "increase/decrease/stable"
        }}
    ],
    "new_relationships": [
        {{
            "character_a": "角色A",
            "character_b": "角色B",
            "description": "关系描述"
        }}
    ],
    "resolved_relationships": ["角色A-角色B", ...],
    "themes": ["主题1", "主题2", ...],
    "new_questions": ["悬念1", "悬念2", ...],
    "confidence": 0.0-1.0
}}

只输出JSON，不要有其他文字。"""

        last_error = ""
        for attempt in range(1, 3):
            try:
                routed = await ctx.router.route(
                    ModelRequest(
                        task_type=TaskType.EXTRACT_RELATIONSHIP_DELTAS,
                        messages=[
                            {
                                "role": "system",
                                "content": "你是一个精确的小说关系分析助手，只输出JSON格式的结果。",
                            },
                            {"role": "user", "content": prompt},
                        ],
                        max_tokens=4096,
                        temperature=0.2,
                    )
                )

                import json
                data = json.loads(routed.content)

                return LLMExtractionResult(
                    relationship_changes=data.get("relationship_changes", []),
                    new_relationships=data.get("new_relationships", []),
                    resolved_relationships=data.get("resolved_relationships", []),
                    themes=data.get("themes", []),
                    new_questions=data.get("new_questions", []),
                    confidence=data.get("confidence", 0.5),
                    attempts=attempt,
                )
            except Exception as exc:  # noqa: BLE001 - non-critical outline enrichment
                last_error = f"{type(exc).__name__}: {exc}"
                _log.warning(
                    "outline_tracker_llm_extraction_attempt_failed | attempt=%d | "
                    "chapters=%s | error=%s",
                    attempt,
                    [chapter.chapter_number for chapter in chapters],
                    last_error,
                )

        return LLMExtractionResult(confidence=0.0, attempts=2, error=last_error)

    def get_context_for_chapter(self, current_chapter: int) -> OutlineContext:
        """Get aggregated context for generating the next chapter."""
        return self._rule_tracker.get_context_for_chapter(current_chapter)

    def get_context_for_prompt(self, current_chapter: int) -> dict[str, str]:
        """Get context summaries formatted for prompt injection."""
        return self._rule_tracker.get_context_for_prompt(current_chapter)

    def get_status(self) -> dict[str, Any]:
        """Get tracker status information."""
        return {
            "batch_size": self._batch_size,
            "llm_enabled": self._llm_enabled,
            "llm_threshold_batches": self._llm_threshold,
            "consecutive_no_change_batches": self._consecutive_no_change_batches,
            "last_llm_chapter": self._last_llm_chapter,
            "total_relationships": len(self._rule_tracker._relationships),
            "total_themes": len(self._rule_tracker._themes),
            "unresolved_questions": len(self._rule_tracker._unresolved_questions),
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialize tracker state for persistence."""
        data = self._rule_tracker.to_dict()
        data["hybrid_config"] = {
            "batch_size": self._batch_size,
            "llm_enabled": self._llm_enabled,
            "llm_threshold_batches": self._llm_threshold,
            "consecutive_no_change_batches": self._consecutive_no_change_batches,
            "last_llm_chapter": self._last_llm_chapter,
        }
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HybridOutlineTracker":
        """Deserialize tracker state from persistence."""
        hybrid_config = data.pop("hybrid_config", {})
        tracker = cls(
            batch_size=hybrid_config.get("batch_size", 5),
            llm_extraction_enabled=hybrid_config.get("llm_enabled", True),
            llm_threshold_batches=hybrid_config.get("llm_threshold_batches"),
        )
        tracker._consecutive_no_change_batches = hybrid_config.get("consecutive_no_change_batches", 0)
        tracker._last_llm_chapter = hybrid_config.get("last_llm_chapter", 0)
        tracker._rule_tracker = OutlineTracker.from_dict(data)
        return tracker
