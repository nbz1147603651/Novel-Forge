"""Propagation validation for chapter repair operations.

Validates that changes in a repaired chapter properly propagate to
subsequent chapters that reference the modified content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


# Issue types for propagation validation
class IssueType:
    ENTITY_STATE_PROPAGATION = "entity_state_propagation"
    REFERENCE_BROKEN = "reference_broken"
    TIMELINE_SHIFT = "timeline_shift"


# Severity levels
class Severity:
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


# Regex for Chinese entity names in quotes: 「」""''『』
_ENTITY_RE = re.compile(
    r"[\u300c\u201c\u2018\u300e]([\u4e00-\u9fff]{2,6})[\u300d\u201d\u2019\u300f]"
)
_UNQUOTED_ENTITY_RE = re.compile(
    r"(?:^|[\s，。！？；、：「“『])([\u4e00-\u9fff]{2,3})"
    r"(?=(?:说|道|问|答|想|看|走|站|坐|笑|叹|喊|低声|转身|点头|摇头|沉默|"
    r"握住|来到|离开|推开|拿起|放下|回头|抬头|皱眉|倒下|断气))"
)

# Regex for temporal markers in Chinese text
_TEMPORAL_RE = re.compile(
    r"(第[一二三四五六七八九十\d]+[天日章回]|[早中晚][上午]|清晨|黄昏|黎明|深夜|"
    r"次日|翌日|隔日|数日后|几日后|当日|当天|那时|此时|随后|接着|然后|"
    r"之前|之后|以前|以后|从前|曾经|已经|刚刚|正在|将要|即将|马上)"
)

# Regex for death/final-state indicators
_DEATH_RE = re.compile(
    r"(死[亡了]?|去世|逝世|牺牲|毙命|丧命|身亡|魂断|命丧|气绝|断气|"
    r"永别|长眠|安息|殉职|阵亡|战死|病死|老死|自杀|他杀|被杀|遇害|"
    r"尸体|遗体|坟墓|墓碑|灵位|遗书|遗言|遗物|遗照)"
)


@dataclass(frozen=True)
class PropagationIssue:
    """One propagation issue found after a chapter repair.

    Attributes:
        issue_type: Type of propagation issue. One of:
            - 'entity_state_propagation': Entity state changed but not reflected
            - 'reference_broken': Reference to repaired content is now broken
            - 'timeline_shift': Timeline information inconsistent
        severity: Severity level: 'critical', 'warning', 'info'
        description: Human-readable description of the issue.
        source_chapter: Chapter number where the repair occurred.
        affected_chapters: List of chapter numbers affected by this issue.
        suggested_action: Recommended action to fix the issue.
    """

    issue_type: str
    severity: str
    description: str
    source_chapter: int
    affected_chapters: list[int]
    suggested_action: str

    def model_dump(self) -> dict[str, Any]:
        return {
            "issue_type": self.issue_type,
            "severity": self.severity,
            "description": self.description,
            "source_chapter": self.source_chapter,
            "affected_chapters": self.affected_chapters,
            "suggested_action": self.suggested_action,
        }


class PropagationValidator:
    """Validates that chapter repairs properly propagate to subsequent chapters.

    When a chapter is repaired, any entities, references, or timeline information
    that changed must be properly reflected in subsequent chapters that reference
    the modified content.

    All validation is rule-based (NO LLM calls).
    """

    def __init__(self) -> None:
        """Initialize the PropagationValidator."""
        pass

    async def validate_propagation(
        self,
        repaired_chapter: int,
        original_text: str,
        repaired_text: str,
        subsequent_chapters: list[int],
        all_chapter_texts: dict[int, str],
    ) -> list[PropagationIssue]:
        """Validate that repairs in a chapter properly propagate to subsequent chapters.

        Args:
            repaired_chapter: The chapter number that was repaired.
            original_text: The text of the chapter before repair.
            repaired_text: The text of the chapter after repair.
            subsequent_chapters: List of chapter numbers that come after the repaired
                chapter and may reference its content.
            all_chapter_texts: Dictionary mapping chapter numbers to their full text
                content.

        Returns:
            List of PropagationIssue objects describing any detected propagation
            problems. Empty list means no issues found.
        """
        issues: list[PropagationIssue] = []

        # Strategy 1: Entity state propagation
        issues.extend(
            self._check_entity_state_propagation(
                repaired_chapter,
                original_text,
                repaired_text,
                subsequent_chapters,
                all_chapter_texts,
            )
        )

        # Strategy 2: Reference broken
        issues.extend(
            self._check_reference_broken(
                repaired_chapter,
                original_text,
                repaired_text,
                subsequent_chapters,
                all_chapter_texts,
            )
        )

        # Strategy 3: Timeline shift
        issues.extend(
            self._check_timeline_shift(
                repaired_chapter,
                original_text,
                repaired_text,
                subsequent_chapters,
                all_chapter_texts,
            )
        )

        return issues

    def _extract_entities(self, text: str) -> set[str]:
        return set(_ENTITY_RE.findall(text)) | set(_UNQUOTED_ENTITY_RE.findall(text))

    def _extract_temporal_markers(self, text: str) -> list[str]:
        return _TEMPORAL_RE.findall(text)

    def _entity_has_death_context(self, entity: str, text: str) -> bool:
        entity_pattern = re.compile(re.escape(entity))
        for match in entity_pattern.finditer(text):
            start = max(0, match.start() - 50)
            end = min(len(text), match.end() + 50)
            if _DEATH_RE.search(text[start:end]):
                return True
        return False

    def _check_entity_state_propagation(
        self,
        repaired_chapter: int,
        original_text: str,
        repaired_text: str,
        subsequent_chapters: list[int],
        all_chapter_texts: dict[int, str],
    ) -> list[PropagationIssue]:
        issues: list[PropagationIssue] = []

        original_entities = self._extract_entities(original_text)
        repaired_entities = self._extract_entities(repaired_text)

        original_death_entities = {
            e for e in original_entities if self._entity_has_death_context(e, original_text)
        }
        repaired_death_entities = {
            e for e in repaired_entities if self._entity_has_death_context(e, repaired_text)
        }

        entities_with_new_death = repaired_death_entities - original_death_entities
        entities_with_death_removed = original_death_entities - repaired_death_entities
        removed_entities = original_entities - repaired_entities

        for ch_num in subsequent_chapters:
            ch_text = all_chapter_texts.get(ch_num, "")
            if not ch_text:
                continue

            ch_entities = self._extract_entities(ch_text)

            self._flag_newly_deceased_entities(
                entities_with_new_death,
                ch_entities,
                ch_text,
                repaired_chapter,
                ch_num,
                issues,
            )

            self._flag_death_removed_inconsistency(
                entities_with_death_removed,
                removed_entities,
                ch_text,
                ch_entities,
                repaired_chapter,
                ch_num,
                issues,
            )

        return issues

    def _flag_newly_deceased_entities(
        self,
        deceased: set[str],
        ch_entities: set[str],
        ch_text: str,
        source_ch: int,
        affected_ch: int,
        issues: list[PropagationIssue],
    ) -> None:
        for entity in deceased:
            if entity not in ch_entities:
                continue
            if self._entity_referenced_as_alive(entity, ch_text):
                issues.append(
                    PropagationIssue(
                        issue_type=IssueType.ENTITY_STATE_PROPAGATION,
                        severity=Severity.CRITICAL,
                        description=(
                            f"实体「{entity}」在第 {source_ch} 章中死亡，"
                            f"但在第 {affected_ch} 章中仍以存活状态出现，可能存在状态不一致。"
                        ),
                        source_chapter=source_ch,
                        affected_chapters=[affected_ch],
                        suggested_action=(
                            f"检查第 {affected_ch} 章中对「{entity}」的引用是否应改为"
                            f"回忆、遗物或其他死后场景。"
                        ),
                    )
                )

    def _entity_referenced_as_alive(self, entity: str, text: str) -> bool:
        entity_pattern = re.compile(re.escape(entity))
        for match in entity_pattern.finditer(text):
            start = max(0, match.start() - 30)
            end = min(len(text), match.end() + 30)
            context = text[start:end]
            if _DEATH_RE.search(context):
                return False
            if any(kw in context for kw in ["回忆", "想起", "曾经", "已死", "遗体", "遗"]):
                return False
        return True

    def _flag_death_removed_inconsistency(
        self,
        death_removed_entities: set[str],
        removed_entities: set[str],
        ch_text: str,
        ch_entities: set[str],
        source_ch: int,
        affected_ch: int,
        issues: list[PropagationIssue],
    ) -> None:
        if not death_removed_entities and not removed_entities:
            return
        if not _DEATH_RE.search(ch_text):
            return

        shared = ch_entities & (death_removed_entities | removed_entities)
        if shared:
            entity_str = "、".join(list(shared)[:3])
            issues.append(
                PropagationIssue(
                    issue_type=IssueType.ENTITY_STATE_PROPAGATION,
                    severity=Severity.WARNING,
                    description=(
                        f"第 {source_ch} 章修复后移除了死亡描述，"
                        f"但第 {affected_ch} 章仍包含死亡相关内容（涉及实体：{entity_str}）。"
                    ),
                    source_chapter=source_ch,
                    affected_chapters=[affected_ch],
                    suggested_action=(
                        f"确认第 {affected_ch} 章的死亡描述是否仍然有效，"
                        f"或是否需要随第 {source_ch} 章一起修改。"
                    ),
                )
            )

    def _check_reference_broken(
        self,
        repaired_chapter: int,
        original_text: str,
        repaired_text: str,
        subsequent_chapters: list[int],
        all_chapter_texts: dict[int, str],
    ) -> list[PropagationIssue]:
        """Detect when a referenced entity was removed/deleted in repair.

        If the repaired chapter removes an entity that subsequent chapters
        reference, those references become broken.
        """
        issues: list[PropagationIssue] = []

        original_entities = self._extract_entities(original_text)
        repaired_entities = self._extract_entities(repaired_text)

        # Entities that existed in original but were completely removed in repair
        removed_entities = original_entities - repaired_entities

        if not removed_entities:
            return issues

        for ch_num in subsequent_chapters:
            ch_text = all_chapter_texts.get(ch_num, "")
            if not ch_text:
                continue

            ch_entities = self._extract_entities(ch_text)

            # Find removed entities that are still referenced in subsequent chapters
            broken_refs = removed_entities & ch_entities

            if broken_refs:
                entity_list = "、".join(list(broken_refs)[:5])
                issues.append(
                    PropagationIssue(
                        issue_type=IssueType.REFERENCE_BROKEN,
                        severity=Severity.CRITICAL if len(broken_refs) > 1 else Severity.WARNING,
                        description=(
                            f"第 {repaired_chapter} 章修复后移除了实体引用（{entity_list}），"
                            f"但第 {ch_num} 章仍然引用这些实体。"
                        ),
                        source_chapter=repaired_chapter,
                        affected_chapters=[ch_num],
                        suggested_action=(
                            f"检查第 {ch_num} 章中对「{entity_list}」的引用是否需要更新或删除，"
                            f"或确认第 {repaired_chapter} 章是否不应移除这些实体。"
                        ),
                    )
                )

        return issues

    def _check_timeline_shift(
        self,
        repaired_chapter: int,
        original_text: str,
        repaired_text: str,
        subsequent_chapters: list[int],
        all_chapter_texts: dict[int, str],
    ) -> list[PropagationIssue]:
        """Check temporal consistency after repair.

        Detects when repaired chapter changes timeline information making
        subsequent chapters temporally inconsistent.
        """
        issues: list[PropagationIssue] = []

        original_temporal = self._extract_temporal_markers(original_text)
        repaired_temporal = self._extract_temporal_markers(repaired_text)

        # Find temporal markers that changed
        original_set = set(original_temporal)
        repaired_set = set(repaired_temporal)
        changed_temporal = original_set - repaired_set
        added_temporal = repaired_set - original_set

        if not changed_temporal and not added_temporal:
            return issues

        # Build a temporal profile for each subsequent chapter
        for ch_num in subsequent_chapters:
            ch_text = all_chapter_texts.get(ch_num, "")
            if not ch_text:
                continue

            ch_temporal = self._extract_temporal_markers(ch_text)
            ch_temporal_set = set(ch_temporal)

            # Check 1: Subsequent chapter uses a temporal marker that was removed
            # from the repaired chapter (e.g., "次日" but the repaired chapter
            # no longer establishes the reference day)
            dependent_temporal = ch_temporal_set & changed_temporal
            if dependent_temporal:
                marker_str = "、".join(list(dependent_temporal)[:5])
                issues.append(
                    PropagationIssue(
                        issue_type=IssueType.TIMELINE_SHIFT,
                        severity=Severity.WARNING,
                        description=(
                            f"第 {repaired_chapter} 章修复后移除了时间标记（{marker_str}），"
                            f"但第 {ch_num} 章的时间线依赖这些标记。"
                        ),
                        source_chapter=repaired_chapter,
                        affected_chapters=[ch_num],
                        suggested_action=(
                            f"确认第 {ch_num} 章的时间线是否需要调整，"
                            f"或第 {repaired_chapter} 章是否应保留相关时间标记。"
                        ),
                    )
                )

            # Check 2: New temporal markers in repaired chapter conflict with
            # subsequent chapter's temporal flow
            if added_temporal:
                # Look for contradictory temporal sequences
                # e.g., repaired says "次日" (next day) but subsequent says "当日" (same day)
                conflict_pairs = self._find_temporal_conflicts(added_temporal, ch_temporal_set)
                if conflict_pairs:
                    for orig_marker, ch_marker in conflict_pairs:
                        issues.append(
                            PropagationIssue(
                                issue_type=IssueType.TIMELINE_SHIFT,
                                severity=Severity.CRITICAL,
                                description=(
                                    f"第 {repaired_chapter} 章的时间标记「{orig_marker}」"
                                    f"与第 {ch_num} 章的「{ch_marker}」存在时间线冲突。"
                                ),
                                source_chapter=repaired_chapter,
                                affected_chapters=[ch_num],
                                suggested_action=(
                                    f"统一第 {repaired_chapter} 章和第 {ch_num} 章的时间线，"
                                    f"确保时间顺序一致。"
                                ),
                            )
                        )

        return issues

    def _find_temporal_conflicts(
        self,
        repaired_markers: set[str],
        subsequent_markers: set[str],
    ) -> list[tuple[str, str]]:
        conflicts: list[tuple[str, str]] = []

        opposite_pairs: list[tuple[set[str], set[str]]] = [
            ({"当日", "当天", "此时", "这时"}, {"次日", "翌日", "隔日", "第二天"}),
            ({"清晨", "早上", "上午"}, {"黄昏", "傍晚"}),
            ({"清晨", "早上"}, {"深夜", "夜晚", "晚上"}),
            ({"之前", "以前", "从前"}, {"之后", "以后", "随后", "接着", "然后"}),
            ({"将要", "即将", "马上"}, {"已经", "刚刚", "曾经"}),
        ]

        for group_a, group_b in opposite_pairs:
            repaired_in_a = repaired_markers & group_a
            subsequent_in_b = subsequent_markers & group_b
            repaired_in_b = repaired_markers & group_b
            subsequent_in_a = subsequent_markers & group_a

            for r_marker in repaired_in_a:
                for s_marker in subsequent_in_b:
                    if not self._markers_are_synonyms(r_marker, s_marker):
                        conflicts.append((r_marker, s_marker))

            for r_marker in repaired_in_b:
                for s_marker in subsequent_in_a:
                    if not self._markers_are_synonyms(r_marker, s_marker):
                        conflicts.append((r_marker, s_marker))

        return conflicts

    def _markers_are_synonyms(self, a: str, b: str) -> bool:
        synonym_groups: list[set[str]] = [
            {"当日", "当天"},
            {"次日", "翌日"},
            {"深夜", "夜晚"},
            {"之前", "以前"},
            {"之后", "以后"},
            {"将要", "即将"},
            {"随后", "接着", "然后"},
            {"早上", "上午"},
        ]
        return any(a in g and b in g for g in synonym_groups)
