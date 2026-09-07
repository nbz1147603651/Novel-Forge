"""Regression detection for repair operations.

Detects when chapter repairs inadvertently introduce new consistency issues
or fail to properly address the original problem.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from novel_forge.workspace.helpers.execution_helpers import _extract_key_entities

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Issue types for entity regression
ENTITY_REGRESSION_NEW = "entity_new_not_in_canon"
ENTITY_REGRESSION_CONFLICT = "entity_canon_conflict"

# Issue types for state regression
STATE_REGRESSION_DEATH = "state_death_contradiction"
STATE_REGRESSION_RELATIONSHIP = "state_relationship_contradiction"
STATE_REGRESSION_LOCATION = "state_location_contradiction"

# Issue types for narrative regression
NARRATIVE_REGRESSION_UNRESOLVED_FORESHADOW = "narrative_unresolved_foreshadow"
NARRATIVE_REGRESSION_NEW_CONFLICT = "narrative_new_conflict"

# Severity levels
CRITICAL = "critical"
WARNING = "warning"
INFO = "info"

# Chinese foreshadowing / setup patterns (rule-based heuristics)
_FORESHADOW_PATTERNS = [
    re.compile(r"(?:后来|此后|从此|日后|将来|总有一天|迟早|不久|没过多久)"),
    re.compile(r"(?:预示|暗示|埋伏|伏笔|征兆|预兆|迹象|端倪)"),
    re.compile(r"(?:谁知|哪知|岂料|不料|没想到|出乎意料)"),
    re.compile(r"(?:注定|命运|劫数|缘分|因果|报应)"),
]

# Conflict / tension introduction patterns
_CONFLICT_PATTERNS = [
    re.compile(r"(?:矛盾|冲突|恩怨|仇恨|怨恨|误会|嫌隙|隔阂)"),
    re.compile(r"(?:背叛|出卖|欺骗|隐瞒|暗算|陷害|阴谋)"),
    re.compile(r"(?:决斗|厮杀|战斗|交战|对决|拼杀)"),
    re.compile(r"(?:秘密|隐秘|暗中|偷偷|悄悄|私下)"),
]

# Death / alive state keywords
_DEATH_KEYWORDS = re.compile(r"(?:死|亡|殒命|毙命|身亡|死去|死亡|去世|逝世|殉|牺牲|殁)")
_ALIVE_KEYWORDS = re.compile(r"(?:活着|存活|生还|幸存|未死|没死|还活着|安然无恙)")

# Relationship keywords
_RELATIONSHIP_KEYWORDS = re.compile(
    r"(?:父子|母子|夫妻|夫妇|兄弟|姐妹|师徒|主仆|朋友|仇人|恩人|恋人|情人|未婚夫|未婚妻)"
)


@dataclass(frozen=True)
class RegressionIssue:
    """One regression issue found after a repair operation."""

    issue_type: str
    """Type of regression found."""

    severity: str
    """Severity level: 'critical', 'warning', 'info'"""

    description: str
    """Human-readable description of the regression."""

    evidence: str
    """Text snippet showing the regression."""

    affected_chapters: list[int]
    """List of chapter numbers affected by this regression."""

    caused_by_repair_of: str
    """The issue_id or description of the original issue that was repaired."""

    def model_dump(self) -> dict[str, Any]:
        return {
            "issue_type": self.issue_type,
            "severity": self.severity,
            "description": self.description,
            "evidence": self.evidence,
            "affected_chapters": self.affected_chapters,
            "caused_by_repair_of": self.caused_by_repair_of,
        }


class RegressionDetector:
    """Detects regressions introduced by repair operations.

    Compares original and repaired text to identify:
    - Entity regression: repaired text introduces new entities not in canon
    - State regression: repair changes character state contradicting canon
    - Narrative regression: repair introduces unresolved narrative elements

    Pure rule-based detection — NO LLM calls.
    """

    def __init__(
        self,
        *,
        entity_confidence_threshold: float = 0.8,
        context_window_chars: int = 200,
    ) -> None:
        """Initialize the regression detector.

        Args:
            entity_confidence_threshold: Minimum confidence for entity detection.
            context_window_chars: Character window around evidence for context.
        """
        self._entity_confidence_threshold = entity_confidence_threshold
        self._context_window_chars = context_window_chars

    @staticmethod
    def _span_distance(first: tuple[int, int], second: tuple[int, int]) -> int:
        if first[1] < second[0]:
            return second[0] - first[1]
        if second[1] < first[0]:
            return first[0] - second[1]
        return 0

    def _keyword_applies_to_character(
        self,
        *,
        window: str,
        char_name: str,
        keyword_pattern: re.Pattern[str],
        known_person_names: set[str],
        max_distance: int = 16,
    ) -> bool:
        """Return whether a state keyword is locally attached to one character."""
        char_spans = [(m.start(), m.end()) for m in re.finditer(re.escape(char_name), window)]
        if not char_spans:
            return False
        person_spans: list[tuple[str, tuple[int, int]]] = []
        for person_name in sorted(known_person_names, key=len, reverse=True):
            if len(person_name) < 2:
                continue
            person_spans.extend(
                (person_name, (m.start(), m.end()))
                for m in re.finditer(re.escape(person_name), window)
            )

        for keyword_match in keyword_pattern.finditer(window):
            keyword_span = (keyword_match.start(), keyword_match.end())
            char_distance = min(
                self._span_distance(char_span, keyword_span) for char_span in char_spans
            )
            if char_distance > max_distance:
                continue
            nearest_person_distance = min(
                (
                    self._span_distance(person_span, keyword_span)
                    for _person_name, person_span in person_spans
                ),
                default=char_distance,
            )
            if char_distance <= nearest_person_distance:
                return True
        return False

    async def detect_regressions(
        self,
        original_text: str,
        repaired_text: str,
        chapter_issues: list[dict[str, Any]],
        all_chapter_texts: dict[int, str],
        canon_state: dict[str, Any] | None = None,
        chapter_number: int = 0,
    ) -> list[RegressionIssue]:
        """Detect regressions between original and repaired text.

        Three detection strategies:
        1. Entity regression: new entities in repaired text not in canon
        2. State regression: character state contradictions with canon
        3. Narrative regression: unresolved foreshadowing/conflicts in later chapters

        Args:
            original_text: The chapter text before repair.
            repaired_text: The chapter text after repair.
            chapter_issues: List of issue dicts that were being addressed by the repair.
            all_chapter_texts: Dict mapping chapter number to full text for cross-chapter checks.
            canon_state: Optional canon state dict for entity/state validation.
            chapter_number: The chapter number being repaired (for cross-chapter checks).

        Returns:
            List of RegressionIssue objects describing any detected regressions.
        """
        if not self._texts_differ_significantly(original_text, repaired_text):
            return []

        regressions: list[RegressionIssue] = []

        # Build repair context: extract issue IDs for caused_by_repair_of
        repair_issue_ids = [
            str(issue.get("issue_id", issue.get("description", "unknown")))
            for issue in (chapter_issues or [])
        ]
        repair_context = ", ".join(repair_issue_ids) if repair_issue_ids else "repair_operation"

        # 1. Entity regression detection
        regressions.extend(
            self._detect_entity_regressions(
                original_text, repaired_text, canon_state, repair_context
            )
        )

        # 2. State regression detection
        regressions.extend(
            self._detect_state_regressions(
                original_text, repaired_text, canon_state, repair_context
            )
        )

        # 3. Narrative regression detection
        regressions.extend(
            self._detect_narrative_regressions(
                original_text, repaired_text, all_chapter_texts, chapter_number, repair_context
            )
        )

        return regressions

    # ---------------------------------------------------------------------------
    # Entity regression detection
    # ---------------------------------------------------------------------------

    def _known_entities_from_canon(
        self,
        canon_state: dict[str, Any] | None,
    ) -> dict[str, list[str]]:
        known: dict[str, set[str]] = {"person": set(), "location": set(), "item": set()}

        def _name_from_item(item: Any) -> str:
            if isinstance(item, dict):
                return str(item.get("name", "") or "").strip()
            return str(item or "").strip()

        if not isinstance(canon_state, dict):
            return {key: sorted(values) for key, values in known.items()}

        characters = canon_state.get("characters", {})
        if isinstance(characters, dict):
            for key, value in characters.items():
                name = str(key or "").strip()
                if len(name) >= 2:
                    known["person"].add(name)
                if isinstance(value, dict):
                    for field in ("name", "display_name"):
                        alias = str(value.get(field, "") or "").strip()
                        if len(alias) >= 2:
                            known["person"].add(alias)
                    aliases = value.get("aliases", [])
                    if isinstance(aliases, list):
                        known["person"].update(
                            str(alias).strip()
                            for alias in aliases
                            if len(str(alias).strip()) >= 2
                        )

        # Also handle new StoryKernel entities list format
        entities = canon_state.get("entities", [])
        if isinstance(entities, list):
            for entity in entities:
                if not isinstance(entity, dict):
                    continue
                entity_type = str(entity.get("entity_type", "") or "").strip().lower()
                entity_name = str(entity.get("name", "") or "").strip()
                if len(entity_name) >= 2:
                    if entity_type == "character" or entity_type == "":
                        known["person"].add(entity_name)
                    elif entity_type == "location":
                        known["location"].add(entity_name)
                    elif entity_type == "item":
                        known["item"].add(entity_name)
                # Also check aliases
                aliases = entity.get("aliases", [])
                if isinstance(aliases, list):
                    for alias in aliases:
                        alias_str = str(alias).strip()
                        if len(alias_str) >= 2:
                            if entity_type == "character" or entity_type == "":
                                known["person"].add(alias_str)
                            elif entity_type == "location":
                                known["location"].add(alias_str)
                            elif entity_type == "item":
                                known["item"].add(alias_str)

        for key_name in ("locations", "worldbuilding"):
            locations = canon_state.get(key_name, {})
            if isinstance(locations, dict):
                known["location"].update(
                    str(name).strip()
                    for name in locations.keys()
                    if len(str(name).strip()) >= 2
                )
            elif isinstance(locations, list):
                for item in locations:
                    location_name = _name_from_item(item)
                    if len(location_name) >= 2:
                        known["location"].add(location_name)

        items = canon_state.get("items", {})
        if isinstance(items, dict):
            known["item"].update(
                str(name).strip() for name in items.keys() if len(str(name).strip()) >= 2
            )

        return {key: sorted(values) for key, values in known.items()}

    def _detect_entity_regressions(
        self,
        original: str,
        repaired: str,
        canon_state: dict[str, Any] | None,
        repair_context: str,
    ) -> list[RegressionIssue]:
        results: list[RegressionIssue] = []

        known_entities = self._known_entities_from_canon(canon_state)
        original_entities = _extract_key_entities(
            original,
            known_entities=known_entities,
            include_unquoted_person_candidates=True,
        )
        repaired_entities = _extract_key_entities(
            repaired,
            known_entities=known_entities,
            include_unquoted_person_candidates=True,
        )

        # New entities in repaired text not present in original
        new_by_type: dict[str, list[str]] = {}
        for entity_type in ("time", "location", "person", "item"):
            original_set = set(original_entities.get(entity_type, []))
            repaired_set = set(repaired_entities.get(entity_type, []))
            new_entities = repaired_set - original_set
            if new_entities:
                new_by_type[entity_type] = sorted(new_entities)

        if not new_by_type:
            return results

        # Check new entities against canon
        canon_chars: set[str] = set()
        canon_locs: set[str] = set()
        if canon_state:
            canon_chars = {
                name.lower() for name in known_entities.get("person", []) if name.strip()
            }
            canon_locs = {
                name.lower() for name in known_entities.get("location", []) if name.strip()
            }

        for entity_type, entities in new_by_type.items():
            for entity in entities:
                entity_lower = entity.lower()

                # Person entities: check against canon characters
                if entity_type == "person":
                    if canon_chars and entity_lower not in canon_chars:
                        results.append(
                            RegressionIssue(
                                issue_type=ENTITY_REGRESSION_NEW,
                                severity=WARNING,
                                description=(
                                    f"Repair introduced new person entity '{entity}' "
                                    f"not found in original text or canon characters."
                                ),
                                evidence=entity,
                                affected_chapters=[],
                                caused_by_repair_of=repair_context,
                            )
                        )

                # Location entities: check against canon locations
                elif entity_type == "location":
                    if canon_locs and entity_lower not in canon_locs:
                        results.append(
                            RegressionIssue(
                                issue_type=ENTITY_REGRESSION_NEW,
                                severity=INFO,
                                description=(
                                    f"Repair introduced new location '{entity}' "
                                    f"not found in original text or canon locations."
                                ),
                                evidence=entity,
                                affected_chapters=[],
                                caused_by_repair_of=repair_context,
                            )
                        )

                # Time / item entities: flag as info for review
                else:
                    results.append(
                        RegressionIssue(
                            issue_type=ENTITY_REGRESSION_NEW,
                            severity=INFO,
                            description=(
                                f"Repair introduced new {entity_type} entity '{entity}' "
                                f"not present in original text."
                            ),
                            evidence=entity,
                            affected_chapters=[],
                            caused_by_repair_of=repair_context,
                        )
                    )

        return results

    # ---------------------------------------------------------------------------
    # State regression detection
    # ---------------------------------------------------------------------------

    def _detect_state_regressions(
        self,
        original: str,
        repaired: str,
        canon_state: dict[str, Any] | None,
        repair_context: str,
    ) -> list[RegressionIssue]:
        if not canon_state:
            return []

        results: list[RegressionIssue] = []

        characters: dict[str, dict[str, Any]] = {}
        raw_characters = canon_state.get("characters", {})
        if isinstance(raw_characters, dict):
            characters = {
                k: v for k, v in raw_characters.items() if isinstance(v, dict)
            }

        entities = canon_state.get("entities", [])
        if isinstance(entities, list):
            for entity in entities:
                if not isinstance(entity, dict):
                    continue
                entity_type = str(entity.get("entity_type", "") or "").strip().lower()
                if entity_type not in ("character", ""):
                    continue
                entity_name = str(entity.get("name", "") or "").strip()
                if len(entity_name) < 2:
                    continue
                attrs = entity.get("attributes", {})
                if not isinstance(attrs, dict):
                    attrs = {}
                char_data: dict[str, Any] = {"name": entity_name}
                char_data.update(attrs)
                characters[entity_name] = char_data

        if not characters:
            return results
        known_person_names = {
            str(name or "").strip() for name in characters.keys() if len(str(name or "").strip()) >= 2
        }

        # Extract person entities from both texts
        known_entities = self._known_entities_from_canon(canon_state)
        known_person_names.update(
            str(name or "").strip()
            for name in known_entities.get("person", [])
            if len(str(name or "").strip()) >= 2
        )
        original_persons = set(
            _extract_key_entities(
                original,
                known_entities=known_entities,
                include_unquoted_person_candidates=True,
            ).get("person", [])
        )
        repaired_persons = set(
            _extract_key_entities(
                repaired,
                known_entities=known_entities,
                include_unquoted_person_candidates=True,
            ).get("person", [])
        )
        persons_in_repaired = repaired_persons | original_persons

        for char_name in persons_in_repaired:
            char_data = characters.get(char_name)
            if not isinstance(char_data, dict):
                continue

            # Check death state contradiction
            canon_alive = char_data.get("alive", True)

            # Context window: find mentions of this character near death/alive keywords
            char_mentions = [m.start() for m in re.finditer(re.escape(char_name), repaired)]
            for pos in char_mentions:
                window_start = max(0, pos - 50)
                window_end = min(len(repaired), pos + len(char_name) + 50)
                window = repaired[window_start:window_end]
                window_has_death = self._keyword_applies_to_character(
                    window=window,
                    char_name=char_name,
                    keyword_pattern=_DEATH_KEYWORDS,
                    known_person_names=known_person_names,
                )
                window_has_alive = self._keyword_applies_to_character(
                    window=window,
                    char_name=char_name,
                    keyword_pattern=_ALIVE_KEYWORDS,
                    known_person_names=known_person_names,
                )

                if not canon_alive and window_has_alive:
                    # Canon says dead, but repaired text implies alive
                    results.append(
                        RegressionIssue(
                            issue_type=STATE_REGRESSION_DEATH,
                            severity=CRITICAL,
                            description=(
                                f"Character '{char_name}' is dead in canon (alive=False), "
                                f"but repaired text implies they are alive."
                            ),
                            evidence=window,
                            affected_chapters=[],
                            caused_by_repair_of=repair_context,
                        )
                    )
                elif canon_alive and window_has_death and char_name in window:
                    # Canon says alive, but repaired text implies death
                    results.append(
                        RegressionIssue(
                            issue_type=STATE_REGRESSION_DEATH,
                            severity=CRITICAL,
                            description=(
                                f"Character '{char_name}' is alive in canon (alive=True), "
                                f"but repaired text implies they died."
                            ),
                            evidence=window,
                            affected_chapters=[],
                            caused_by_repair_of=repair_context,
                        )
                    )

            # Check relationship contradictions
            canon_relationships = char_data.get("relationships", {})
            if isinstance(canon_relationships, dict):
                for other_char, rel_desc in canon_relationships.items():
                    rel_desc_lower = str(rel_desc).lower()
                    # Check if repaired text contradicts known relationships
                    if any(kw in rel_desc_lower for kw in ("仇", "敌", "恨")):
                        # Canon says they are enemies — check if repaired implies friendship
                        friend_patterns = re.compile(
                            rf"{re.escape(char_name)}.*(?:好友|知己|挚友|密友|挚爱)"
                        )
                        friend_match = friend_patterns.search(repaired)
                        if friend_match:
                            results.append(
                                RegressionIssue(
                                    issue_type=STATE_REGRESSION_RELATIONSHIP,
                                    severity=WARNING,
                                    description=(
                                        f"Repair implies friendly relationship between "
                                        f"'{char_name}' and '{other_char}', but canon "
                                        f"describes them as enemies: {rel_desc}"
                                    ),
                                    evidence=friend_match.group(0)[:100],
                                    affected_chapters=[],
                                    caused_by_repair_of=repair_context,
                                )
                            )

        return results

    # ---------------------------------------------------------------------------
    # Narrative regression detection
    # ---------------------------------------------------------------------------

    def _detect_narrative_regressions(
        self,
        original: str,
        repaired: str,
        all_chapter_texts: dict[int, str],
        chapter_number: int,
        repair_context: str,
    ) -> list[RegressionIssue]:
        results: list[RegressionIssue] = []

        # Find new foreshadowing elements in repaired text
        original_foreshadows = self._find_foreshadowing(original)
        repaired_foreshadows = self._find_foreshadowing(repaired)
        new_foreshadows = repaired_foreshadows - original_foreshadows

        # Find new conflict elements in repaired text
        original_conflicts = self._find_conflicts(original)
        repaired_conflicts = self._find_conflicts(repaired)
        new_conflicts = repaired_conflicts - original_conflicts

        # Check subsequent chapters for resolution of new narrative elements
        subsequent_chapters = {
            ch: text for ch, text in all_chapter_texts.items() if ch > chapter_number
        }

        # Check unresolved foreshadowing
        for foreshadow in new_foreshadows:
            resolved = False
            for ch_text in subsequent_chapters.values():
                # Check if the foreshadowed element is mentioned/resolved later
                if foreshadow in ch_text or self._is_semantic_match(foreshadow, ch_text):
                    resolved = True
                    break

            if not resolved:
                results.append(
                    RegressionIssue(
                        issue_type=NARRATIVE_REGRESSION_UNRESOLVED_FORESHADOW,
                        severity=WARNING,
                        description=(
                            f"Repair introduced foreshadowing element '{foreshadow}' "
                            f"not resolved in any subsequent chapter."
                        ),
                        evidence=foreshadow,
                        affected_chapters=sorted(subsequent_chapters.keys()),
                        caused_by_repair_of=repair_context,
                    )
                )

        # Check new conflicts not addressed later
        for conflict in new_conflicts:
            addressed = False
            for ch_text in subsequent_chapters.values():
                if conflict in ch_text or self._is_semantic_match(conflict, ch_text):
                    addressed = True
                    break

            if not addressed:
                results.append(
                    RegressionIssue(
                        issue_type=NARRATIVE_REGRESSION_NEW_CONFLICT,
                        severity=WARNING,
                        description=(
                            f"Repair introduced conflict element '{conflict}' "
                            f"not addressed in any subsequent chapter."
                        ),
                        evidence=conflict,
                        affected_chapters=sorted(subsequent_chapters.keys()),
                        caused_by_repair_of=repair_context,
                    )
                )

        return results

    # ---------------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------------

    def _find_foreshadowing(self, text: str) -> set[str]:
        results: set[str] = set()
        for pattern in _FORESHADOW_PATTERNS:
            for match in pattern.finditer(text):
                start = max(0, match.start() - 5)
                end = min(len(text), match.end() + 15)
                snippet = text[start:end].strip()
                if snippet and len(snippet) >= 4:
                    results.add(snippet)
        return results

    def _find_conflicts(self, text: str) -> set[str]:
        results: set[str] = set()
        for pattern in _CONFLICT_PATTERNS:
            for match in pattern.finditer(text):
                start = max(0, match.start() - 5)
                end = min(len(text), match.end() + 15)
                snippet = text[start:end].strip()
                if snippet and len(snippet) >= 4:
                    results.add(snippet)
        return results

    def _is_semantic_match(self, keyword: str, text: str) -> bool:
        if keyword in text:
            return True
        chinese_chars = re.findall(r"[\u4e00-\u9fff]", keyword)
        if len(chinese_chars) < 2:
            return False
        bigrams = {"".join(chinese_chars[i : i + 2]) for i in range(len(chinese_chars) - 1)}
        matches = sum(1 for bg in bigrams if bg in text)
        return matches >= 1

    def _texts_differ_significantly(self, original: str, repaired: str) -> bool:
        norm_original = re.sub(r"\s+", "", original)
        norm_repaired = re.sub(r"\s+", "", repaired)
        if norm_original == norm_repaired:
            return False
        if not norm_original and norm_repaired:
            return True
        if not norm_repaired and norm_original:
            return True
        return True

    def _extract_evidence_window(
        self, text: str, evidence: str, window_chars: int | None = None
    ) -> str:
        """Extract surrounding context for evidence snippet."""
        if window_chars is None:
            window_chars = self._context_window_chars
        evidence = evidence.strip()
        if not evidence:
            return ""
        pos = text.find(evidence)
        if pos < 0:
            return evidence[:60]
        start = max(0, pos - window_chars)
        end = min(len(text), pos + len(evidence) + window_chars)
        return text[start:end]
