"""Motif Tracker — tracks themes, motifs, and imagery across the novel.

Provides:
- Motif extraction and tracking
- Intentional callback suggestions
- Unintentional repetition detection
- Thematic consistency analysis
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from novel_forge.core.constants import TaskType
from novel_forge.core.parsing.token_utils import estimate_dict_tokens
from novel_forge.editorial.signals import classify_expression_channel
from novel_forge.gateway.router import ModelRouter
from novel_forge.memory.base import MotifOccurrence
from novel_forge.memory.motif_governance import (
    CONCEPTUAL_MOTIF_CATEGORIES,
    IMPORTANT_MOTIF_ROLES,
    VALID_MOTIF_CATEGORIES,
    VALID_MOTIF_ROLES,
    allows_forward_prompt_guidance,
    allows_hard_repetition_forbid,
    allows_prompt_callback,
    allows_unified_strengthen,
    default_role_for_category,
    is_conceptual_category,
    normalize_motif_category,
    should_auto_retire_category,
)
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.token_budget import calculate_route_aware_max_tokens
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.story_kernel.schemas import StoryKernel

_log = get_logger("memory.motif")

MotifCategory = Literal["意象", "动作", "感官", "颜色", "声音", "主题", "符号", "技法"]
MotifRole = Literal[
    "theme_anchor",
    "core_symbol",
    "character_motif",
    "recurring_image",
    "atmospheric_detail",
    "one_off_rhetoric",
    "unknown",
]
MotifSeverity = Literal["low", "medium", "high"]
MotifPriority = Literal["low", "medium", "high"]
THEMATIC_MOTIF_CATEGORIES = CONCEPTUAL_MOTIF_CATEGORIES
AUTO_FORGET_RETIRE_REASON = "auto_ephemeral_forget"


def _settings_or_env_int(env_key: str, settings_attr: str, default: int) -> int:
    """Resolve an int from process env first, then Settings/.env, then default."""
    raw_env = os.environ.get(env_key)
    if raw_env is not None and raw_env.strip():
        try:
            return int(raw_env)
        except (TypeError, ValueError):
            _log.warning("invalid_motif_setting_env | key=%s | value=%r", env_key, raw_env)

    try:
        from novel_forge.core.config import get_settings

        raw_setting = getattr(get_settings(), settings_attr, default)
        return int(raw_setting)
    except Exception:
        return default


def _settings_or_env_bool(env_key: str, settings_attr: str, default: bool) -> bool:
    """Resolve a bool from process env first, then Settings/.env, then default."""
    raw_env = os.environ.get(env_key)
    if raw_env is not None and raw_env.strip():
        return raw_env.strip().lower() in {"1", "true", "yes", "y", "on", "是"}

    try:
        from novel_forge.core.config import get_settings

        return bool(getattr(get_settings(), settings_attr, default))
    except Exception:
        return default


@dataclass
class MotifPromptBudget:
    """Advisory token pressure configuration for motif prompt data.

    The item limits belong to motif retrieval/ranking. ``budget_tokens`` is a
    pressure threshold only and never authorizes deleting already-selected
    motif evidence.

    Args:
        max_items: Retrieval selection limit per motif category (default 8)
        budget_tokens: Advisory pressure threshold for the result (default 500)
        min_forbidden: Minimum forbidden-repetition retrieval coverage (default 3)
    """

    max_items: int = 8
    budget_tokens: int = 500
    min_forbidden: int = 3

    @classmethod
    def from_env(cls) -> "MotifPromptBudget":
        """Create budget config from environment variables."""
        budget_tokens = _settings_or_env_int(
            "NOVEL_FORGE_MOTIF_PROMPT_TOKEN_BUDGET",
            "motif_prompt_token_budget",
            500,
        )
        max_items = _settings_or_env_int(
            "NOVEL_FORGE_MOTIF_PROMPT_MAX_ITEMS",
            "motif_prompt_max_items",
            8,
        )
        return cls(max_items=max_items, budget_tokens=budget_tokens)


@dataclass
class Motif:
    """Represents a recurring motif or theme element."""

    motif_id: str
    name: str  # e.g., "雨", "镜子", "坠落"
    category: MotifCategory
    description: str = ""
    first_appearance_chapter: int = 0
    last_appearance_chapter: int = 0
    occurrence_count: int = 0
    associated_characters: list[str] = field(default_factory=list)
    associated_emotions: list[str] = field(default_factory=list)
    thematic_meaning: str = ""  # e.g., "雨→清洗→重生"
    is_intentional: bool = False  # Legacy model label, not proof of author intent
    retired: bool = False  # True = 不再提示，用户已标记为不再关注
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "motif_id": self.motif_id,
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "first_appearance_chapter": self.first_appearance_chapter,
            "last_appearance_chapter": self.last_appearance_chapter,
            "occurrence_count": self.occurrence_count,
            "associated_characters": self.associated_characters,
            "associated_emotions": self.associated_emotions,
            "thematic_meaning": self.thematic_meaning,
            "is_intentional": self.is_intentional,
            "retired": self.retired,
            "metadata": self.metadata,
        }


@dataclass
class RepetitionWarning:
    """Warning about potential unintentional repetition."""

    motif_name: str
    chapter_number: int
    previous_chapters: list[int]
    similarity_score: float
    text_snippet: str
    suggestion: str
    severity: MotifSeverity = "medium"


@dataclass
class MotifSuggestion:
    """Suggestion for motif usage in upcoming chapter."""

    motif_id: str
    motif_name: str
    reason: str
    thematic_fit: str
    suggested_context: str
    chapters_since_last_use: int
    priority: MotifPriority = "medium"


@dataclass
class MotifGuidanceItem:
    """A single guidance item for motif usage in chapter writing.

    Encapsulates one piece of motif-related guidance: what motif to use/avoid,
    why, and with what priority.
    """

    motif_id: str
    motif_name: str
    category: str  # MotifCategory value
    guidance_type: str  # strengthen/dormant_callback/pov/plot_matched/forbidden/soft_forbidden
    reason: str
    priority: MotifPriority = "medium"
    thematic_meaning: str = ""
    chapters_since: int = 0
    similarity: float = 0.0


@dataclass
class MotifGuidanceBundle:
    """Unified bundle of motif guidance for a chapter.

    Groups guidance items by type and provides a single ``to_prompt_text()``
    method that renders all items into a prompt-ready string.
    """

    strengthen: list[MotifGuidanceItem] = field(default_factory=list)
    dormant_callbacks: list[MotifGuidanceItem] = field(default_factory=list)
    pov_motifs: list[MotifGuidanceItem] = field(default_factory=list)
    plot_matched: list[MotifGuidanceItem] = field(default_factory=list)
    forbidden: list[MotifGuidanceItem] = field(default_factory=list)
    soft_forbidden_themes: list[MotifGuidanceItem] = field(default_factory=list)
    chapter_number: int = 0

    @property
    def all_items(self) -> list[MotifGuidanceItem]:
        """Return all guidance items across all categories."""
        return (
            self.strengthen
            + self.dormant_callbacks
            + self.pov_motifs
            + self.plot_matched
            + self.forbidden
            + self.soft_forbidden_themes
        )

    @property
    def has_guidance(self) -> bool:
        """Return True if any guidance items exist."""
        return bool(self.all_items)

    def to_prompt_text(self) -> str:
        """Render all guidance items into a prompt-ready string.

        Returns an empty string when no guidance items exist.
        """
        if not self.has_guidance:
            return ""

        sections: list[str] = []

        if self.strengthen:
            lines = [
                f"- 【{item.motif_name}】({item.category}) 可参考：{item.reason}"
                for item in self.strengthen
            ]
            sections.append("## 母题参考（软约束）\n" + "\n".join(lines))

        if self.dormant_callbacks:
            lines = [
                f"- 【{item.motif_name}】({item.category}) 与当前目标相关，可省略；仅在承担新作用时使用：{item.reason}"
                for item in self.dormant_callbacks
            ]
            sections.append("## 长线回调参考（软约束）\n" + "\n".join(lines))

        if self.pov_motifs:
            lines = [
                f"- 【{item.motif_name}】({item.category}) 可参考：{item.reason}"
                for item in self.pov_motifs
            ]
            sections.append("## POV 角色相关母题参考（软约束）\n" + "\n".join(lines))

        if self.plot_matched:
            lines = [
                f"- 【{item.motif_name}】({item.category}) 可参考：{item.reason}（相似度: {item.similarity:.2f}）"
                for item in self.plot_matched
            ]
            sections.append("## 情节相关母题参考（软约束）\n" + "\n".join(lines))

        if self.forbidden:
            lines = [
                f"- 「{item.motif_name}」近期重复风险：避免无新增作用的复述；"
                "承担新作用或属于用户要求、必要事实时可以保留。"
                for item in self.forbidden
            ]
            sections.append("## 避免重复\n" + "\n".join(lines))

        if self.soft_forbidden_themes:
            lines = [
                f"- 「{item.motif_name}」近期高频出现，检查是否推进新的行动、关系或理解；"
                "仅换词不算新作用。"
                for item in self.soft_forbidden_themes
            ]
            sections.append("## 主题母题表达变化建议\n" + "\n".join(lines))

        return "\n\n".join(sections)


class MotifTracker:
    """Tracks motifs, themes, and imagery throughout the novel.

    Features:
    - Automatic motif extraction from chapter text
    - Tracking of occurrence patterns
    - Detection of unintentional repetition
    - Suggestions for intentional callbacks
    - Thematic consistency analysis
    """

    def __init__(
        self,
        router: ModelRouter,
        builder: PromptBuilder,
        *,
        episodic_memory: Any | None = None,
    ) -> None:
        self._router = router
        self._builder = builder

        # Motif storage
        self._motifs: dict[str, Motif] = {}
        self._occurrence_log: list[MotifOccurrence] = []

        # Chapter index for quick lookup
        self._chapter_motifs: dict[int, set[str]] = defaultdict(set)

        # Repetition tracking
        self._recent_usage: dict[str, list[int]] = defaultdict(list)  # motif_id -> [chapters]

        # Per-chapter extraction cache to avoid duplicate LLM calls.
        # Both extract_from_chapter() and check_unintentional_repetition()
        # call _llm_extract_motifs(); this cache ensures only one LLM round-trip.
        # LRU-bounded to prevent memory growth on long projects.
        self._extraction_cache: OrderedDict[int, list[MotifOccurrence]] = OrderedDict()
        self._extraction_cache_fingerprints: dict[int, str] = {}
        self._EXTRACTION_CACHE_MAX_SIZE = 100
        self._extraction_cache_hits = 0
        self._extraction_cache_misses = 0
        self._extraction_cache_evictions = 0

        # Warmup state: chapters 1-N pre-populate motifs from seeds without LLM.
        self._is_warmup_complete: bool = False
        self._warmup_seeds: dict[str, list[str]] = {}  # category -> [names]

        # Semantic matching via Zvec (initialized lazily)
        from novel_forge.memory.motif_vector import MotifSemanticMatcher, MotifVectorBridge

        self._semantic_bridge = MotifVectorBridge(episodic_memory=episodic_memory)
        self._semantic_matcher = MotifSemanticMatcher(self._semantic_bridge, threshold=0.6)

        _log.info("MotifTracker initialized | router=%s", type(router).__name__)

    @property
    def motifs(self) -> dict[str, Motif]:
        """Public access to motifs dict for UI consumption."""
        return self._motifs

    @property
    def is_warmup_complete(self) -> bool:
        """Return whether warmup has been completed."""
        return self._is_warmup_complete

    async def warmup(
        self,
        project_seeds: dict[str, list[str]] | None = None,
        bible_data: dict[str, Any] | None = None,
        genre_template: dict[str, list[str]] | None = None,
        story_themes: list[str] | None = None,
    ) -> None:
        """Pre-populate motifs from seed data WITHOUT running LLM extraction.

        Called during early chapters (1-N) to establish baseline motifs from
        project seeds, bible-derived terms, and genre templates.

        Args:
            project_seeds: {"意象": [...], "符号": [...], ...} from project config
            bible_data: {"意象": [...], "符号": [...], ...} from character/story bible
            genre_template: {"意象": [...], "符号": [...], ...} from genre defaults
        """
        if self._is_warmup_complete:
            return

        merged: dict[str, list[str]] = {}

        for source in [genre_template or {}, bible_data or {}, project_seeds or {}]:
            for category, names in source.items():
                if category not in VALID_MOTIF_CATEGORIES:
                    continue
                if not isinstance(names, list):
                    continue
                merged.setdefault(category, []).extend(names)

        if story_themes:
            merged.setdefault("主题", []).extend(
                [t for t in story_themes if t and isinstance(t, str)]
            )

        for category, names in merged.items():
            seen: set[str] = set()
            for name in names:
                name = str(name).strip()
                if not name or name in seen:
                    continue
                seen.add(name)
                motif_id = f"warmup_{category}_{name}"
                if motif_id not in self._motifs:
                    self._motifs[motif_id] = Motif(
                        motif_id=motif_id,
                        name=name,
                        category=category,  # type: ignore[arg-type]
                        description=f"预热母题（{category}）",
                        is_intentional=True,
                        metadata={
                            "classification_source": "warmup",
                            "category_confidence": 0.7,
                            "category_status": "seed",
                            "motif_role": self._default_role_for_category(category),
                            "importance_score": 0.65
                            if not is_conceptual_category(category)
                            else 0.9,
                        },
                    )

        self._warmup_seeds = merged
        self._is_warmup_complete = True

        total = sum(len(v) for v in merged.values())
        _log.info(
            "motif_warmup_complete | categories=%d | total_motifs=%d",
            len(merged),
            total,
        )

    def has_chapter_motifs(self, chapter_number: int) -> bool:
        """Return whether this chapter already has recorded motif occurrences."""
        if chapter_number <= 0:
            return False
        return bool(self._chapter_motifs.get(chapter_number))

    async def extract_from_chapter(
        self,
        chapter_number: int,
        chapter_text: str,
        canon_state: StoryKernel | None = None,
        chapter_outline: dict[str, Any] | None = None,
    ) -> list[MotifOccurrence]:
        """Extract motif occurrences from chapter text.

        Args:
            chapter_number: Chapter number
            chapter_text: Full chapter text
            canon_state: Current canon state (for context)
            chapter_outline: Optional chapter outline with goal, pov_character, element_focus

        Returns:
            List of MotifOccurrence objects
        """
        start_time = time.monotonic()
        _log.info(
            "motif_extraction_start | chapter=%d | text_length=%d",
            chapter_number,
            len(chapter_text),
        )

        try:
            occurrences = await self._llm_extract_motifs(
                chapter_number, chapter_text, chapter_outline
            )

            for occ in occurrences:
                self._record_occurrence(occ)

            self.prune_ephemeral_motifs(chapter_number)

            elapsed_ms = (time.monotonic() - start_time) * 1000
            _log.info(
                "motif_extraction_done | chapter=%d | count=%d | elapsed_ms=%.2f",
                chapter_number,
                len(occurrences),
                elapsed_ms,
            )

            if occurrences:
                motif_ids = [occ.motif_id for occ in occurrences[:5]]
                _log.debug(
                    "extracted_motifs | chapter=%d | motifs=%s",
                    chapter_number,
                    motif_ids,
                )

            return occurrences

        except Exception as exc:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            _log.error(
                "motif_extraction_failed | chapter=%d | error=%s | elapsed_ms=%.2f",
                chapter_number,
                exc,
                elapsed_ms,
                exc_info=True,
            )
            return []

    def retire_motif(self, motif_id: str) -> bool:
        """Mark a motif as retired — it will no longer appear in suggestions."""
        if motif_id in self._motifs:
            self._motifs[motif_id].retired = True
            self._motifs[motif_id].metadata["retired_reason"] = "manual"
            _log.info(
                "motif_retired | motif_id=%s | name=%s", motif_id, self._motifs[motif_id].name
            )
            return True
        return False

    def unretire_motif(self, motif_id: str) -> bool:
        """Reactivate a previously retired motif."""
        if motif_id in self._motifs:
            self._motifs[motif_id].retired = False
            self._motifs[motif_id].metadata["retired_reason"] = ""
            _log.info(
                "motif_unretired | motif_id=%s | name=%s", motif_id, self._motifs[motif_id].name
            )
            return True
        return False

    @staticmethod
    def _requested_by_chapter(
        motif: Motif, chapter_outline: dict[str, Any] | None, current_context: str = ""
    ) -> bool:
        # Only current chapter intent, not a global theme or frequency, can make
        # this synchronous retrieval path suggest an expression.
        outline = chapter_outline or {}
        request = (
            " ".join(
                str(outline.get(key) or "")
                for key in ("goal", "required_events", "required_progressions", "motif_requests")
            )
            + " "
            + current_context
        )
        return bool(motif.name and motif.name in request) or bool(
            motif.motif_id and motif.motif_id in request
        )

    def get_suggestions_for_chapter(
        self,
        current_chapter: int,
        recent_chapters: list[int] | None = None,
        current_context: str = "",
        min_chapters_since: int = 5,
        min_occurrences: int = 3,
        chapter_outline: dict[str, Any] | None = None,
        prompt_safe: bool = False,
    ) -> list[MotifSuggestion]:
        """Generate suggestions for motif usage in upcoming chapter.

        Args:
            current_chapter: Chapter being written
            recent_chapters: Recent chapter numbers for context
            current_context: Current chapter context/outline
            min_chapters_since: Minimum gap before suggesting (default 5)
            min_occurrences: Minimum occurrence count to qualify (default 3)
            chapter_outline: Optional chapter outline with goal, pov_character, element_focus
            prompt_safe: If True, omit categories that should remain soft-only in prompts

        Returns:
            List of MotifSuggestion objects
        """
        if recent_chapters is None:
            recent_chapters = list(range(max(1, current_chapter - 5), current_chapter))

        suggestions: list[MotifSuggestion] = []

        # Find motifs that haven't been used recently
        for motif_id, motif in self._motifs.items():
            if motif.retired:
                continue  # 已退休的母题不再建议
            if prompt_safe and not allows_prompt_callback(motif.category):
                continue
            chapters_since = current_chapter - motif.last_appearance_chapter

            if chapters_since < min_chapters_since:
                continue  # Used recently, no need to suggest

            if not self._requested_by_chapter(motif, chapter_outline, current_context):
                continue

            # Frequency can rank relevant candidates, never create a writing duty.
            priority = self._calculate_suggestion_priority(motif, chapters_since, min_occurrences)

            if priority == "low":
                continue

            suggestions.append(
                MotifSuggestion(
                    motif_id=motif_id,
                    motif_name=motif.name,
                    reason="当前章节明确涉及该意象；仅供参考，可省略",
                    thematic_fit=motif.thematic_meaning,
                    suggested_context=self._generate_suggested_context(
                        motif, current_context, chapter_outline
                    ),
                    chapters_since_last_use=chapters_since,
                    priority=priority,
                )
            )

        # Sort by priority
        priority_order = {"high": 0, "medium": 1, "low": 2}
        suggestions.sort(key=lambda s: priority_order.get(s.priority, 2))

        # Apply outline-aware boosting if chapter_outline is provided
        if chapter_outline is not None:
            suggestions = self._boost_by_outline_relevance(suggestions, chapter_outline)

        result = suggestions[:5]

        _log.info(
            "motif_suggestions_generated | chapter=%d | total_candidates=%d | returned=%d",
            current_chapter,
            len(suggestions),
            len(result),
        )

        return result

    async def check_unintentional_repetition(
        self,
        chapter_number: int,
        chapter_text: str,
        lookback_chapters: int = 5,
        repetition_gap_chapters: int = 2,
        chapter_outline: dict[str, Any] | None = None,
    ) -> list[RepetitionWarning]:
        """Check for unintentional repetition of motifs.

        Args:
            chapter_number: Current chapter number
            chapter_text: Chapter text to check
            lookback_chapters: Number of chapters to look back
            repetition_gap_chapters: Chapter gap below which reuse is considered too close
            chapter_outline: Optional chapter outline for context-aware repetition detection

        Returns:
            List of RepetitionWarning objects
        """
        start_time = time.monotonic()
        _log.info(
            "repetition_check_start | chapter=%d | lookback=%d | text_length=%d",
            chapter_number,
            lookback_chapters,
            len(chapter_text),
        )

        warnings: list[RepetitionWarning] = []

        try:
            current_motifs = await self._llm_extract_motifs(
                chapter_number, chapter_text, chapter_outline
            )

            lookback = max(0, int(lookback_chapters))
            recent_gap = max(1, int(repetition_gap_chapters))
            oldest_chapter = max(1, chapter_number - lookback) if lookback > 0 else chapter_number

            # Check against usage inside the configured chapter window.
            for occ in current_motifs:
                motif = self._motifs.get(occ.motif_id)
                if not motif:
                    continue

                recent_chapters = [
                    ch
                    for ch in self._recent_usage.get(occ.motif_id, [])
                    if oldest_chapter <= ch < chapter_number
                ]

                if not recent_chapters:
                    continue

                # Neither frequency nor an LLM's intentionality label establishes
                # author intent. Protect explicit user requests and evidenced evolution.
                if motif.metadata.get("repetition_source") == "user_explicit":
                    continue
                if (
                    occ.function_relation == "new_function"
                    and occ.narrative_function.strip()
                    and occ.function_evidence.strip()
                    and occ.function_evidence in chapter_text
                ):
                    continue
                # Conceptual labels are not surface-expression deletion targets.
                if not allows_hard_repetition_forbid(motif.category):
                    continue

                # Calculate similarity/repetition score
                chapters_since = chapter_number - recent_chapters[-1]

                if chapters_since < recent_gap or (
                    occ.function_relation == "redundant"
                    and occ.function_evidence.strip()
                    and occ.function_evidence in chapter_text
                ):
                    # Flag too-close repetition of uncommon motifs.
                    severity: MotifSeverity = "medium"

                    warnings.append(
                        RepetitionWarning(
                            motif_name=motif.name,
                            chapter_number=chapter_number,
                            previous_chapters=recent_chapters[-3:],
                            similarity_score=1.0 - (chapters_since / 5.0),
                            text_snippet=occ.text_snippet,
                            suggestion=self._generate_repetition_suggestion(motif, chapters_since),
                            severity=severity,
                        )
                    )

            elapsed_ms = (time.monotonic() - start_time) * 1000
            severity_counts = {
                "high": sum(1 for w in warnings if w.severity == "high"),
                "medium": sum(1 for w in warnings if w.severity == "medium"),
                "low": sum(1 for w in warnings if w.severity == "low"),
            }
            _log.info(
                "repetition_check_done | chapter=%d | warnings=%d | severity=%s | elapsed_ms=%.2f",
                chapter_number,
                len(warnings),
                severity_counts,
                elapsed_ms,
            )

            if warnings:
                motif_names = [w.motif_name for w in warnings]
                _log.debug(
                    "repetition_warnings | chapter=%d | motifs=%s",
                    chapter_number,
                    motif_names,
                )

            return warnings

        except Exception as exc:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            _log.error(
                "repetition_check_failed | chapter=%d | error=%s | elapsed_ms=%.2f",
                chapter_number,
                exc,
                elapsed_ms,
                exc_info=True,
            )
            return []

    def get_motif_network(
        self,
        chapter_range: tuple[int, int] | None = None,
    ) -> dict[str, Any]:
        """Get a network graph of motifs and their relationships.

        Args:
            chapter_range: Optional (start, end) chapter range

        Returns:
            Network data for visualization
        """
        nodes: list[dict[str, Any]] = []
        seen_edges: set[tuple[str, str, int]] = set()
        edges: list[dict[str, Any]] = []

        for motif in self._motifs.values():
            # Filter by chapter range if specified
            if chapter_range:
                if (
                    motif.first_appearance_chapter < chapter_range[0]
                    and motif.last_appearance_chapter < chapter_range[0]
                ):
                    continue
                if motif.first_appearance_chapter > chapter_range[1]:
                    continue

            nodes.append(
                {
                    "id": motif.motif_id,
                    "name": motif.name,
                    "category": motif.category,
                    "occurrences": motif.occurrence_count,
                    "first_chapter": motif.first_appearance_chapter,
                    "last_chapter": motif.last_appearance_chapter,
                }
            )

            # Add edges between motifs that appear in same chapter (deduped)
            for ch in range(motif.first_appearance_chapter, motif.last_appearance_chapter + 1):
                if ch in self._chapter_motifs:
                    for other_id in self._chapter_motifs[ch]:
                        if other_id != motif.motif_id:
                            edge_key = (motif.motif_id, other_id, ch)
                            if edge_key not in seen_edges:
                                seen_edges.add(edge_key)
                                edges.append(
                                    {
                                        "source": motif.motif_id,
                                        "target": other_id,
                                        "chapter": ch,
                                    }
                                )

        return {
            "nodes": nodes,
            "edges": edges,
        }

    def get_thematic_analysis(self) -> dict[str, Any]:
        """Generate thematic analysis of the novel so far.

        Returns:
            Thematic analysis report
        """
        # Group motifs by category
        by_category: dict[str, list[Motif]] = defaultdict(list)
        for motif in self._motifs.values():
            by_category[motif.category].append(motif)

        # Find most prominent motifs
        prominent = sorted(self._motifs.values(), key=lambda m: m.occurrence_count, reverse=True)[
            :10
        ]

        # Analyze thematic patterns
        themes: list[str] = []
        for motif in prominent:
            if motif.thematic_meaning:
                themes.append(f"{motif.name}: {motif.thematic_meaning}")

        return {
            "total_motifs": len(self._motifs),
            "total_occurrences": sum(m.occurrence_count for m in self._motifs.values()),
            "by_category": {cat: len(motifs) for cat, motifs in by_category.items()},
            "prominent_motifs": [m.to_dict() for m in prominent],
            "thematic_patterns": themes,
            "character_associations": self._analyze_character_associations(),
        }

    def mark_motif_as_intentional(
        self,
        motif_id: str,
        chapter_number: int,
    ) -> None:
        """Mark a motif usage as intentional (deliberate callback).

        Args:
            motif_id: Motif identifier
            chapter_number: Chapter where it's used
        """
        if motif_id in self._motifs:
            self._motifs[motif_id].is_intentional = True

    def _record_occurrence(self, occurrence: MotifOccurrence) -> None:
        """Record a motif occurrence."""
        self._occurrence_log.append(occurrence)
        self._chapter_motifs[occurrence.chapter_number].add(occurrence.motif_id)
        self._recent_usage[occurrence.motif_id].append(occurrence.chapter_number)

        # Update motif tracking
        if occurrence.motif_id not in self._motifs:
            # Derive name from text_snippet (truncated) or fall back to motif_id suffix
            fallback_name = (
                occurrence.text_snippet[:20]
                if occurrence.text_snippet
                else (
                    occurrence.motif_id.split("_", 1)[-1]
                    if "_" in occurrence.motif_id
                    else occurrence.motif_id
                )
            )
            self._motifs[occurrence.motif_id] = Motif(
                motif_id=occurrence.motif_id,
                name=fallback_name,
                category="意象",
            )

        motif = self._motifs[occurrence.motif_id]
        if motif.retired and motif.metadata.get("retired_reason") == AUTO_FORGET_RETIRE_REASON:
            motif.retired = False
            motif.metadata["reactivated_chapter"] = occurrence.chapter_number
            motif.metadata["retired_reason"] = ""

        motif.occurrence_count += 1
        motif.last_appearance_chapter = occurrence.chapter_number

        if motif.first_appearance_chapter == 0:
            motif.first_appearance_chapter = occurrence.chapter_number

        if occurrence.associated_characters:
            for char in occurrence.associated_characters:
                if char not in motif.associated_characters:
                    motif.associated_characters.append(char)

    async def _llm_extract_motifs(
        self,
        chapter_number: int,
        text: str,
        chapter_outline: dict[str, Any] | None = None,
    ) -> list[MotifOccurrence]:
        """Use LLM to extract motifs from text."""
        fingerprint = self._make_extraction_fingerprint(text, chapter_outline)
        cached = self._extraction_cache.get(chapter_number)
        cached_fingerprint = self._extraction_cache_fingerprints.get(chapter_number)
        if cached is not None and cached_fingerprint == fingerprint:
            self._extraction_cache_hits += 1
            _log.debug(
                "llm_extract_motifs_cached | chapter=%d | count=%d",
                chapter_number,
                len(cached),
            )
            return cached
        self._extraction_cache_misses += 1
        if cached is not None and cached_fingerprint != fingerprint:
            _log.debug(
                "llm_extract_motifs_cache_stale | chapter=%d",
                chapter_number,
            )

        try:
            _log.debug(
                "llm_extract_motifs_start | chapter=%d | input_length=%d",
                chapter_number,
                len(text),
            )

            # Pass existing motifs so the LLM reuses IDs instead of creating duplicates.
            _existing_motifs = [
                {
                    "motif_id": m.motif_id,
                    "name": m.name,
                    "category": m.category,
                    "description": m.description,
                    "thematic_meaning": m.thematic_meaning,
                    "occurrence_count": m.occurrence_count,
                    "first_appearance_chapter": m.first_appearance_chapter,
                    "last_appearance_chapter": m.last_appearance_chapter,
                    "associated_characters": m.associated_characters[:5],
                    "category_confidence": m.metadata.get("category_confidence"),
                    "category_reason": m.metadata.get("category_reason", ""),
                    "secondary_categories": m.metadata.get("secondary_categories", []),
                    "motif_role": m.metadata.get("motif_role", "unknown"),
                    "importance_score": self._motif_importance_score(m),
                    "classification_status": m.metadata.get("category_status", ""),
                    "recent_functions": [
                        {
                            "chapter": occ.chapter_number,
                            "text": occ.text_snippet,
                            "function": occ.narrative_function,
                        }
                        for occ in self._occurrence_log
                        if occ.motif_id == m.motif_id
                        and max(1, chapter_number - 5) <= occ.chapter_number < chapter_number
                    ],
                }
                for m in self._motifs.values()
                if m.occurrence_count > 0
            ]

            # Compute category distribution for diversity pressure
            _category_counts: dict[str, int] = defaultdict(int)
            for m in self._motifs.values():
                if m.occurrence_count > 0 and not m.retired:
                    _category_counts[m.category] = _category_counts.get(m.category, 0) + 1
            _total_active = sum(_category_counts.values())
            _category_distribution = (
                {
                    category: {
                        "count": count,
                        "percentage": count * 100 // _total_active,
                    }
                    for category, count in sorted(
                        _category_counts.items(), key=lambda item: -item[1]
                    )
                }
                if _total_active > 0
                else {}
            )

            extraction_context = self._build_extraction_context(chapter_outline)

            response = await self._router.route(
                self._builder.build(
                    TaskType.EXTRACT_MOTIFS,
                    {
                        "chapter_number": chapter_number,
                        "text": text[:10000],  # Limit text
                        "existing_motifs": _existing_motifs,
                        "chapter_goal": chapter_outline.get("goal", "") if chapter_outline else "",
                        "pov_character": chapter_outline.get("pov_character", "")
                        if chapter_outline
                        else "",
                        "element_focus": list(chapter_outline.get("element_focus", []) or [])
                        if chapter_outline
                        else [],
                        "motif_extraction_context": extraction_context,
                        "motif_category_distribution": _category_distribution,
                    },
                    max_tokens=calculate_route_aware_max_tokens(
                        self._router,
                        TaskType.EXTRACT_MOTIFS,
                        max(2200, len(_existing_motifs) * 180 + 1200),
                        prompt_overhead=3000,
                        min_tokens=2048,
                    ),
                    temperature=0.3,
                )
            )

            from novel_forge.core.parsing.parse_utils import safe_parse_json

            data = safe_parse_json(response.content)

            # Handle both dict and list responses from LLM
            if isinstance(data, list):
                # LLM returned a list directly, wrap it
                motif_items = data
            elif isinstance(data, dict):
                raw_motifs = data.get("motifs")
                if isinstance(raw_motifs, list):
                    motif_items = raw_motifs
                else:
                    # Some providers return {"motif_001": {...}} instead of
                    # {"motifs": [...]}; normalize that keyed object form too.
                    motif_items = []
                    for key, value in data.items():
                        if not isinstance(value, dict):
                            continue
                        item = dict(value)
                        item.setdefault("motif_id", str(key))
                        motif_items.append(item)
            else:
                _log.warning(
                    "llm_extract_motifs_unexpected_type | chapter=%d | type=%s",
                    chapter_number,
                    type(data).__name__,
                )
                motif_items = []

            occurrences: list[MotifOccurrence] = []
            for item in motif_items:
                if not isinstance(item, dict):
                    continue
                motif_id = item.get("motif_id", item.get("name", ""))
                if not motif_id:
                    continue

                # Pre-create / enrich the Motif entry with metadata from LLM
                raw_category = item.get("category", "意象")
                category = cast(MotifCategory, normalize_motif_category(raw_category))
                classification_metadata = self._classification_metadata_from_item(
                    item,
                    category=category,
                    raw_category=str(raw_category or ""),
                )

                if motif_id not in self._motifs:
                    self._motifs[motif_id] = Motif(
                        motif_id=motif_id,
                        name=item.get("name", motif_id),
                        category=category,
                        description=item.get("description", ""),
                        thematic_meaning=item.get("thematic_meaning", ""),
                        is_intentional=self._coerce_bool(item.get("is_intentional"), default=False),
                        metadata=classification_metadata,
                    )
                else:
                    existing = self._motifs[motif_id]
                    self._merge_classification_metadata(
                        existing,
                        classification_metadata,
                        proposed_category=category,
                    )
                    if not existing.description and item.get("description"):
                        existing.description = item["description"]
                    if not existing.thematic_meaning and item.get("thematic_meaning"):
                        existing.thematic_meaning = item["thematic_meaning"]
                    if "is_intentional" in item:
                        existing.is_intentional = self._coerce_bool(
                            item.get("is_intentional"), default=existing.is_intentional
                        )

                # Each motif may contain nested occurrences with per-paragraph detail.
                # Some providers return occurrences as simple snippet strings; accept
                # those too so a whole repair run is not discarded by one shorthand form.
                nested = item.get("occurrences", [])
                if nested and isinstance(nested, list):
                    for occ in nested:
                        occurrences.append(
                            self._build_motif_occurrence(
                                motif_id=motif_id,
                                chapter_number=chapter_number,
                                occurrence=occ,
                            )
                        )
                else:
                    occurrences.append(
                        self._build_motif_occurrence(
                            motif_id=motif_id,
                            chapter_number=chapter_number,
                            occurrence=item,
                        )
                    )

            _log.debug(
                "llm_extract_motifs_done | chapter=%d | extracted=%d",
                chapter_number,
                len(occurrences),
            )

            # Only cache non-empty results to allow retry on failure/empty.
            # Caching an empty list would prevent future extraction attempts.
            if occurrences:
                self._extraction_cache[chapter_number] = occurrences
                self._extraction_cache_fingerprints[chapter_number] = fingerprint
                # LRU eviction: keep cache within size limit
                while len(self._extraction_cache) > self._EXTRACTION_CACHE_MAX_SIZE:
                    evicted_chapter, _ = self._extraction_cache.popitem(last=False)
                    self._extraction_cache_fingerprints.pop(evicted_chapter, None)
                    self._extraction_cache_evictions += 1
            else:
                _log.warning(
                    "llm_extract_motifs_empty | chapter=%d | not_caching_to_allow_retry",
                    chapter_number,
                )
            return occurrences

        except Exception as exc:
            _log.warning(
                "llm_extract_motifs_failed | chapter=%d | error=%s",
                chapter_number,
                exc,
            )
            return []

    def get_cache_stats(self) -> dict[str, int]:
        """Return LLM extraction cache counters for diagnostics."""
        return {
            "hits": self._extraction_cache_hits,
            "misses": self._extraction_cache_misses,
            "evictions": self._extraction_cache_evictions,
            "size": len(self._extraction_cache),
            "max_size": self._EXTRACTION_CACHE_MAX_SIZE,
        }

    @staticmethod
    def _make_extraction_fingerprint(
        text: str,
        chapter_outline: dict[str, Any] | None,
    ) -> str:
        try:
            outline_payload = json.dumps(
                chapter_outline or {},
                sort_keys=True,
                ensure_ascii=False,
                default=str,
            )
        except TypeError:
            outline_payload = repr(chapter_outline)

        digest = hashlib.sha256()
        digest.update(text.encode("utf-8", errors="surrogatepass"))
        digest.update(b"\0")
        digest.update(outline_payload.encode("utf-8", errors="surrogatepass"))
        return digest.hexdigest()

    @staticmethod
    def _coerce_bool(value: Any, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, int | float):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "y", "是", "有意", "刻意"}:
                return True
            if normalized in {"false", "0", "no", "n", "否", "无意", "非刻意"}:
                return False
        return default

    @staticmethod
    def _coerce_float(value: Any, *, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _normalize_secondary_categories(value: Any, *, primary: str) -> list[str]:
        if isinstance(value, str):
            raw_items = [part.strip() for part in value.replace("，", ",").split(",")]
        elif isinstance(value, (list, tuple, set)):
            raw_items = [str(part).strip() for part in value]
        else:
            raw_items = []

        result: list[str] = []
        for item in raw_items:
            if item and item != primary and item in VALID_MOTIF_CATEGORIES and item not in result:
                result.append(item)
        return result[:3]

    @staticmethod
    def _default_role_for_category(category: str) -> MotifRole:
        return cast(MotifRole, default_role_for_category(category))

    @classmethod
    def _normalize_motif_role(cls, value: Any, *, category: str) -> MotifRole:
        raw = str(value or "").strip()
        aliases = {
            "主题锚点": "theme_anchor",
            "核心符号": "core_symbol",
            "人物母题": "character_motif",
            "角色母题": "character_motif",
            "反复意象": "recurring_image",
            "氛围细节": "atmospheric_detail",
            "一次性修辞": "one_off_rhetoric",
            "普通修辞": "one_off_rhetoric",
        }
        role = aliases.get(raw, raw)
        if role in VALID_MOTIF_ROLES:
            return cast(MotifRole, role)
        return cls._default_role_for_category(category)

    @classmethod
    def _coerce_importance_score(
        cls,
        value: Any,
        *,
        category: str,
        role: str,
        confidence: float = 0.0,
    ) -> float:
        raw = cls._coerce_float(value, default=-1.0)
        if raw >= 0.0:
            return max(0.0, min(1.0, raw))
        normalized_category = normalize_motif_category(category)
        if normalized_category == "主题":
            return 0.9
        if normalized_category == "符号":
            return 0.85
        if role == "character_motif":
            return 0.75
        if role == "recurring_image":
            return max(0.55, min(0.75, confidence))
        if role == "atmospheric_detail":
            return 0.35
        if role == "one_off_rhetoric":
            return 0.15
        return max(0.25, min(0.6, confidence))

    def _motif_importance_score(self, motif: Motif) -> float:
        metadata = motif.metadata if isinstance(motif.metadata, dict) else {}
        raw = self._coerce_float(metadata.get("importance_score"), default=-1.0)
        if raw >= 0.0:
            return max(0.0, min(1.0, raw))
        role = str(metadata.get("motif_role") or self._default_role_for_category(motif.category))
        score = self._coerce_importance_score(
            None,
            category=str(motif.category),
            role=role,
            confidence=self._coerce_float(metadata.get("category_confidence")),
        )
        if motif.occurrence_count >= 3:
            score = max(score, 0.6)
        if motif.associated_characters:
            score = max(score, 0.55)
        if is_conceptual_category(motif.category):
            score = max(score, 0.85)
        return max(0.0, min(1.0, score))

    @staticmethod
    def _stringify_context_value(value: Any, *, max_chars: int = 240) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            text = value.strip()
        elif isinstance(value, (list, tuple, set)):
            text = "；".join(str(item).strip() for item in value if str(item).strip())
        elif isinstance(value, dict):
            parts = []
            for key, item in value.items():
                item_text = str(item).strip()
                if item_text:
                    parts.append(f"{key}: {item_text}")
            text = "；".join(parts)
        else:
            text = str(value).strip()
        return text[:max_chars]

    @classmethod
    def _build_extraction_context(
        cls,
        chapter_outline: dict[str, Any] | None,
    ) -> dict[str, str]:
        if not isinstance(chapter_outline, dict):
            return {}

        fields = {
            "chapter_title": ("title", "chapter_title"),
            "chapter_summary": ("summary", "chapter_summary", "synopsis"),
            "chapter_contract": ("chapter_contract", "contract", "narrative_contract"),
            "required_outcomes": ("required_outcomes", "must_include", "must_happen"),
            "scene_intents": ("scene_intents", "scene_goals", "beats"),
            "relevant_entities": ("relevant_entities", "entities", "characters"),
            "motif_requirements": ("motif_requirements", "motifs", "symbolic_requirements"),
        }

        result: dict[str, str] = {}
        for label, keys in fields.items():
            for key in keys:
                value = chapter_outline.get(key)
                text = cls._stringify_context_value(value)
                if text:
                    result[label] = text
                    break
        return result

    @classmethod
    def _classification_metadata_from_item(
        cls,
        item: dict[str, Any],
        *,
        category: MotifCategory,
        raw_category: str,
    ) -> dict[str, Any]:
        confidence = max(
            0.0,
            min(
                1.0,
                cls._coerce_float(
                    item.get("category_confidence", item.get("confidence")),
                    default=0.0,
                ),
            ),
        )
        status = "valid" if raw_category in VALID_MOTIF_CATEGORIES else "fallback"
        if status == "fallback" and confidence == 0.0:
            confidence = 0.2
        role = cls._normalize_motif_role(
            item.get("motif_role", item.get("role")),
            category=category,
        )
        importance = cls._coerce_importance_score(
            item.get("importance_score", item.get("importance")),
            category=category,
            role=role,
            confidence=confidence,
        )

        return {
            "category_confidence": confidence,
            "category_reason": str(
                item.get("category_reason", item.get("classification_reason", "")) or ""
            ).strip(),
            "secondary_categories": cls._normalize_secondary_categories(
                item.get("secondary_categories", []),
                primary=category,
            ),
            "raw_category": raw_category,
            "category_status": status,
            "classification_source": "llm",
            "motif_role": role,
            "importance_score": importance,
        }

    @classmethod
    def _merge_classification_metadata(
        cls,
        motif: Motif,
        new_metadata: dict[str, Any],
        *,
        proposed_category: MotifCategory,
    ) -> None:
        old_metadata = motif.metadata if isinstance(motif.metadata, dict) else {}
        old_confidence = cls._coerce_float(old_metadata.get("category_confidence"))
        new_confidence = cls._coerce_float(new_metadata.get("category_confidence"))

        history = list(old_metadata.get("category_history", []) or [])
        if proposed_category != motif.category:
            history.append(
                {
                    "from": motif.category,
                    "to": proposed_category,
                    "confidence": new_confidence,
                    "reason": new_metadata.get("category_reason", ""),
                }
            )
            history = history[-5:]

        if cls._should_reclassify_motif(
            current_category=str(motif.category),
            proposed_category=str(proposed_category),
            current_confidence=old_confidence,
            proposed_confidence=new_confidence,
            current_status=str(old_metadata.get("category_status", "")),
        ):
            motif.category = proposed_category
            old_metadata["category_reclassified"] = True

        if new_confidence >= old_confidence or not old_metadata:
            old_metadata.update(new_metadata)
        else:
            old_metadata.setdefault("category_confidence", old_confidence)
            old_metadata.setdefault("category_reason", new_metadata.get("category_reason", ""))
            existing_secondary = cls._normalize_secondary_categories(
                old_metadata.get("secondary_categories", []),
                primary=str(motif.category),
            )
            for item in cls._normalize_secondary_categories(
                new_metadata.get("secondary_categories", []),
                primary=str(motif.category),
            ):
                if item not in existing_secondary:
                    existing_secondary.append(item)
            old_metadata["secondary_categories"] = existing_secondary[:3]
            old_importance = cls._coerce_float(old_metadata.get("importance_score"), default=0.0)
            new_importance = cls._coerce_float(new_metadata.get("importance_score"), default=0.0)
            if new_importance > old_importance:
                old_metadata["importance_score"] = new_importance
                old_metadata["motif_role"] = new_metadata.get(
                    "motif_role",
                    old_metadata.get("motif_role", "unknown"),
                )
            else:
                old_metadata.setdefault(
                    "motif_role",
                    new_metadata.get("motif_role", "unknown"),
                )

        if history:
            old_metadata["category_history"] = history
        motif.metadata = old_metadata

    @staticmethod
    def _should_reclassify_motif(
        *,
        current_category: str,
        proposed_category: str,
        current_confidence: float,
        proposed_confidence: float,
        current_status: str,
    ) -> bool:
        if proposed_category == current_category or proposed_category not in VALID_MOTIF_CATEGORIES:
            return False
        if proposed_confidence < 0.75:
            return False
        if current_status == "fallback":
            return True
        if is_conceptual_category(current_category) and not is_conceptual_category(
            proposed_category
        ):
            return proposed_confidence >= 0.95
        return proposed_confidence >= max(0.85, current_confidence + 0.15)

    @staticmethod
    def _build_motif_occurrence(
        *,
        motif_id: str,
        chapter_number: int,
        occurrence: Any,
    ) -> MotifOccurrence:
        """Normalize one LLM occurrence payload into a MotifOccurrence."""
        if isinstance(occurrence, dict):
            _raw_pi = occurrence.get("paragraph_index", 0) or 0
            if isinstance(_raw_pi, list):
                _raw_pi = _raw_pi[0] if _raw_pi else 0
            try:
                paragraph_index = int(_raw_pi)
            except (TypeError, ValueError):
                paragraph_index = 0
            text_snippet = str(
                occurrence.get("text_snippet", "")
                or occurrence.get("text", "")
                or occurrence.get("snippet", "")
                or ""
            ).strip()
            context = str(
                occurrence.get("context", "") or occurrence.get("surrounding_context", "") or ""
            ).strip()
            if not text_snippet and context:
                text_snippet = context
            if not context and text_snippet:
                context = text_snippet
            characters = MotifTracker._normalize_character_list(
                occurrence.get("associated_characters", occurrence.get("characters", []))
            )
            emotional_tone = str(occurrence.get("emotional_tone", "") or "").strip()
        else:
            paragraph_index = 0
            text_snippet = str(occurrence or "").strip()
            context = text_snippet
            characters = []
            emotional_tone = ""

        return MotifOccurrence(
            motif_id=motif_id,
            chapter_number=chapter_number,
            paragraph_index=paragraph_index,
            text_snippet=text_snippet,
            context=context,
            associated_characters=characters,
            emotional_tone=emotional_tone,
            narrative_function=str(occurrence.get("narrative_function") or "")
            if isinstance(occurrence, dict)
            else "",
            function_relation=(
                occurrence.get("function_relation", "unknown")
                if isinstance(occurrence, dict)
                and occurrence.get("function_relation") in {"new_function", "redundant", "unknown"}
                else "unknown"
            ),
            function_evidence=str(occurrence.get("function_evidence") or "")
            if isinstance(occurrence, dict)
            else "",
        )

    @staticmethod
    def _normalize_character_list(raw: Any) -> list[str]:
        if isinstance(raw, list):
            return [str(item).strip() for item in raw if str(item).strip()]
        if isinstance(raw, str):
            text = raw.strip()
            return [text] if text else []
        return []

    def _calculate_suggestion_priority(
        self,
        motif: Motif,
        chapters_since: int,
        min_occurrences: int = 3,
    ) -> Literal["low", "medium", "high"]:
        """Calculate priority for motif suggestion with decay."""
        decay_enabled = _settings_or_env_bool(
            "NOVEL_FORGE_MOTIF_DECAY_ENABLED",
            "motif_decay_enabled",
            True,
        )

        if decay_enabled:
            import math

            half_life = _settings_or_env_int(
                "NOVEL_FORGE_MOTIF_DECAY_HALF_LIFE",
                "motif_decay_half_life",
                10,
            )
            decay_factor = math.exp(-math.log(2) * motif.occurrence_count / max(1, half_life))
        else:
            decay_factor = 1.0

        # High priority: important motif, not used for a while, with decay
        if motif.occurrence_count >= max(5, min_occurrences + 2) and chapters_since >= 10:
            return "high" if decay_factor > 0.5 else "medium"

        # Medium priority: moderate usage, some time since last use, with decay
        if motif.occurrence_count >= min_occurrences and chapters_since >= 5:
            return "medium" if decay_factor > 0.3 else "low"

        # Low priority: not used enough or used recently
        return "low"

    def _generate_suggested_context(
        self,
        motif: Motif,
        current_context: str,
        chapter_outline: dict[str, Any] | None = None,
    ) -> str:
        if chapter_outline is not None:
            goal = chapter_outline.get("goal", "")
            if goal:
                return f"本章目标「{goal}」与{motif.name}母题（{motif.thematic_meaning}）高度契合，可在关键场景中呼应"

            pov_character = chapter_outline.get("pov_character", "")
            if pov_character and pov_character in motif.associated_characters:
                return f"作为{pov_character}的核心意象，本章可通过{motif.name}强化角色情感"

            element_focus = chapter_outline.get("element_focus", [])
            if element_focus and motif.category in element_focus:
                return f"本章{motif.category}要素焦点与{motif.name}母题呼应，建议自然融入"

        if motif.category == "意象":
            return f"可在场景描写中自然融入{motif.name}意象"
        elif motif.category == "感官":
            return f"可通过{motif.name}感官细节唤起读者记忆"
        elif motif.category == "动作":
            return f"可在关键动作中重复{motif.name}动作模式"
        else:
            return f"可在适当时机呼应{motif.name}"

    def _boost_by_outline_relevance(
        self,
        suggestions: list[MotifSuggestion],
        chapter_outline: dict[str, Any],
    ) -> list[MotifSuggestion]:
        """Re-sort suggestions based on chapter outline relevance.

        Boosts priority for motifs that align with:
        - chapter goal keywords matching motif name
        - POV character matching motif's associated characters
        - element_focus overlapping with motif category
        """
        goal = chapter_outline.get("goal", "").lower()
        pov_character = chapter_outline.get("pov_character", "")
        element_focus = chapter_outline.get("element_focus", [])

        priority_boost = {"high": 0, "medium": 1, "low": 2}

        for suggestion in suggestions:
            motif = self._motifs.get(suggestion.motif_id)
            if not motif:
                continue

            boosted = False

            # Check if goal contains motif name or related keywords
            if motif.name.lower() in goal:
                boosted = True

            # Check if POV character is associated with this motif
            if pov_character and pov_character in motif.associated_characters:
                boosted = True

            # Check if element_focus overlaps with motif category
            if element_focus and motif.category in element_focus:
                boosted = True

            if boosted and suggestion.priority != "high":
                new_priority: MotifPriority = (
                    "high" if suggestion.priority == "medium" else "medium"
                )
                suggestion.priority = new_priority

        suggestions.sort(key=lambda s: priority_boost.get(s.priority, 2))
        return suggestions

    def _generate_repetition_suggestion(
        self,
        motif: Motif,
        chapters_since: int,
    ) -> str:
        """Generate suggestion for handling repetition."""
        if chapters_since == 1:
            return f"建议替换{motif.name}的表达方式，或使用不同感官通道"
        elif chapters_since <= 3:
            return f"考虑赋予{motif.name}新的含义或变化形式"
        else:
            return f"如使用{motif.name}，请确保是有意的呼应而非无意识的重复"

    def _analyze_character_associations(self) -> dict[str, list[str]]:
        """Analyze which characters are associated with which motifs."""
        associations: dict[str, list[str]] = defaultdict(list)

        for motif in self._motifs.values():
            for char in motif.associated_characters:
                associations[char].append(motif.name)

        return dict(associations)

    def prune_ephemeral_motifs(
        self,
        current_chapter: int,
        *,
        forget_after_chapters: int | None = None,
        max_occurrences: int | None = None,
        importance_threshold: float | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Auto-retire low-value one-off motifs after a stale window.

        This is a forgetting mechanism for non-important rhetorical residue. It
        keeps historical records intact, but prevents stale low-importance motifs
        from occupying prompt budget or UI attention. Manually retired motifs are
        left alone, and core themes/symbols are never auto-forgotten.
        """
        if current_chapter <= 0:
            return {"retired": [], "count": 0}
        if not _settings_or_env_bool(
            "NOVEL_FORGE_MOTIF_AUTO_FORGET_EPHEMERAL_ENABLED",
            "motif_auto_forget_ephemeral_enabled",
            True,
        ):
            return {"retired": [], "count": 0}

        stale_gap = max(
            1,
            int(
                forget_after_chapters
                if forget_after_chapters is not None
                else _settings_or_env_int(
                    "NOVEL_FORGE_MOTIF_EPHEMERAL_FORGET_AFTER_CHAPTERS",
                    "motif_ephemeral_forget_after_chapters",
                    12,
                )
            ),
        )
        occurrence_limit = max(
            0,
            int(
                max_occurrences
                if max_occurrences is not None
                else _settings_or_env_int(
                    "NOVEL_FORGE_MOTIF_EPHEMERAL_MAX_OCCURRENCES",
                    "motif_ephemeral_max_occurrences",
                    1,
                )
            ),
        )
        threshold = max(
            0.0,
            min(
                1.0,
                self._coerce_float(
                    importance_threshold
                    if importance_threshold is not None
                    else _settings_or_env_int(
                        "NOVEL_FORGE_MOTIF_EPHEMERAL_IMPORTANCE_THRESHOLD_PCT",
                        "motif_ephemeral_importance_threshold_pct",
                        35,
                    )
                    / 100,
                    default=0.35,
                ),
            ),
        )

        retired: list[dict[str, Any]] = []
        for motif_id, motif in self._motifs.items():
            if motif.retired:
                continue
            if not should_auto_retire_category(motif.category):
                continue
            if motif.occurrence_count > occurrence_limit:
                continue
            last_seen = int(motif.last_appearance_chapter or motif.first_appearance_chapter or 0)
            if last_seen <= 0 or current_chapter - last_seen < stale_gap:
                continue

            role = str(
                motif.metadata.get("motif_role") or self._default_role_for_category(motif.category)
            )
            importance = self._motif_importance_score(motif)
            if role in IMPORTANT_MOTIF_ROLES or importance > threshold:
                continue

            item = {
                "motif_id": motif_id,
                "name": motif.name,
                "last_seen": last_seen,
                "chapters_since": current_chapter - last_seen,
                "occurrence_count": motif.occurrence_count,
                "motif_role": role,
                "importance_score": importance,
            }
            retired.append(item)
            if not dry_run:
                motif.retired = True
                motif.metadata["retired_reason"] = AUTO_FORGET_RETIRE_REASON
                motif.metadata["retired_chapter"] = current_chapter
                motif.metadata["retired_detail"] = item

        if retired and not dry_run:
            _log.info(
                "motif_ephemeral_forget | chapter=%d | retired=%d | ids=%s",
                current_chapter,
                len(retired),
                [item["motif_id"] for item in retired[:10]],
            )

        return {"retired": retired, "count": len(retired)}

    def _related_motif_ids_for_chapter(
        self,
        current_chapter: int,
        *,
        lookback_chapters: int = 2,
    ) -> set[str]:
        """Return motif IDs related to *current_chapter*.

        Preference order:
        1. Exact chapter-index links from ``_chapter_motifs`` within a near window.
        2. Fallback to ``last_appearance_chapter`` recency when chapter index is sparse.
        """
        if not self._motifs:
            return set()

        if current_chapter <= 0:
            return {str(mid) for mid in self._motifs.keys() if str(mid).strip()}

        lookback = max(0, int(lookback_chapters))
        start_chapter = max(1, current_chapter - lookback)
        related: set[str] = set()

        for ch in range(start_chapter, current_chapter + 1):
            chapter_ids = self._chapter_motifs.get(ch, set())
            if isinstance(chapter_ids, (set, list, tuple)):
                for mid in chapter_ids:
                    if not str(mid).strip():
                        continue
                    motif = self._motifs.get(mid)
                    if motif is not None and motif.retired:
                        continue
                    related.add(str(mid))

        if related:
            return related

        # Fallback: derive near-chapter relevance from motif metadata.
        for motif_id, motif in self._motifs.items():
            if motif.retired:
                continue
            try:
                last = int(getattr(motif, "last_appearance_chapter", 0) or 0)
            except (TypeError, ValueError):
                last = 0
            if start_chapter <= last <= current_chapter:
                related.add(str(motif_id))

        return related

    def extract_repeated_phrases(
        self,
        chapter_text: str,
        min_length: int = 4,
        min_occurrences: int = 2,
    ) -> list[str]:
        """Detect repeated phrases within a single chapter (pure algorithmic).

        Uses a sliding-window approach to find phrases of *min_length* or more
        characters that appear at least *min_occurrences* times in the text.

        Args:
            chapter_text: Full chapter text to analyse.
            min_length: Minimum phrase length in characters (default 4).
            min_occurrences: Minimum number of appearances to flag (default 2).

        Returns:
            Sorted list of unique repeated phrases, longest-first.
        """
        if not chapter_text or len(chapter_text) < min_length * min_occurrences:
            return []

        compact = "".join(chapter_text.split())

        phrase_counts: dict[str, int] = {}
        max_phrase_len = min(32, len(compact) // min_occurrences)

        for length in range(min_length, max_phrase_len + 1):
            for i in range(len(compact) - length + 1):
                phrase = compact[i : i + length]
                phrase_counts[phrase] = phrase_counts.get(phrase, 0) + 1

        repeated = {p for p, c in phrase_counts.items() if c >= min_occurrences}

        result: list[str] = []
        for phrase in sorted(repeated, key=len, reverse=True):
            if not any(phrase in longer for longer in result):
                result.append(phrase)

        return result

    def get_motifs_for_prompt(
        self,
        current_chapter: int,
        max_motifs: int = 5,
        include_recent_usage: bool = True,
        related_lookback_chapters: int = 2,
        chapter_text: str = "",
        include_retired_in_stats: bool = False,
        warmup_chapters: int = 3,
        max_forbidden_repetition: int | None = None,
    ) -> dict[str, Any]:
        """Get motif data formatted for prompt injection.

        Args:
            current_chapter: Current chapter number
            max_motifs: Maximum motifs to include
            include_recent_usage: Whether to include recent usage history
            chapter_text: Optional chapter text for phrase-level repetition detection
            include_retired_in_stats: If True, include retired motif counts in retired_stats key
            warmup_chapters: Number of early chapters using warmup mode (default 3)
            max_forbidden_repetition: Maximum recent repetition hints to expose.
                Defaults to NOVEL_FORGE_MOTIF_FORBIDDEN_MAX_ITEMS or 3 so the prompt
                receives focused avoid-repetition guidance rather than a broad ban list.

        Returns:
            Dictionary for prompt injection
        """
        # ── Warmup mode: chapters 1-N return seed-based motifs ──
        if current_chapter <= warmup_chapters:
            return self._get_warmup_motifs_for_prompt(current_chapter, max_motifs)

        forget_stats = self.prune_ephemeral_motifs(current_chapter)

        related_ids = self._related_motif_ids_for_chapter(
            current_chapter=current_chapter,
            lookback_chapters=related_lookback_chapters,
        )
        active_motifs = [
            self._motifs[mid]
            for mid in related_ids
            if mid in self._motifs and not self._motifs[mid].retired
        ]
        # Keep closer chapter ties ahead of broad historical frequency.
        active_motifs.sort(
            key=lambda m: (
                int(getattr(m, "last_appearance_chapter", 0) or 0),
                int(getattr(m, "occurrence_count", 0) or 0),
            ),
            reverse=True,
        )

        result: dict[str, Any] = {
            "active_motifs": [],
            "forbidden_repetition": [],
            "suggested_callbacks": [],
            "repeated_phrases": [],
        }
        if forget_stats.get("count"):
            result["forgotten_ephemeral_motifs"] = forget_stats["retired"]
        forbidden_limit = self._resolve_forbidden_repetition_limit(
            max_forbidden_repetition,
            max_motifs=max_motifs,
        )
        soft_forbidden_themes: list[str] = []

        for motif in active_motifs[:max_motifs]:
            motif_data = {
                "motif_id": motif.motif_id,
                "name": motif.name,
                "category": motif.category,
                "occurrence_count": motif.occurrence_count,
                "thematic_meaning": motif.thematic_meaning,
                "category_confidence": motif.metadata.get("category_confidence"),
                "secondary_categories": motif.metadata.get("secondary_categories", []),
                "motif_role": motif.metadata.get("motif_role", "unknown"),
                "importance_score": self._motif_importance_score(motif),
            }

            recent: list[int] = []
            if include_recent_usage:
                recent = self._recent_usage.get(motif.motif_id, [])[-5:]
                motif_data["recent_chapters"] = recent

            result["active_motifs"].append(motif_data)

            # Add to forbidden based on sliding window frequency — not just recency.
            # A motif used 3+ times in the last 5 chapters is over-emphasized regardless
            # of the gap since its last appearance.
            if motif.retired:
                continue

            # Only explicit user ownership protects mandated refrains. Model
            # intentionality and frequency never suppress aesthetic fatigue hints.
            _protected_repetition = motif.metadata.get("repetition_source") == "user_explicit"

            _window_size = 5
            _recent_in_window = [ch for ch in recent if current_chapter - ch < _window_size]
            _window_count = len(_recent_in_window)

            if _window_count >= 3 and not _protected_repetition:
                # High frequency in recent window: hard-forbid only prompt-promotable channels.
                if not allows_hard_repetition_forbid(motif.category):
                    if len(soft_forbidden_themes) < 3:
                        soft_forbidden_themes.append(motif.name)
                elif len(result["forbidden_repetition"]) < forbidden_limit:
                    result["forbidden_repetition"].append(motif.name)
            elif recent and current_chapter - recent[-1] <= 2 and not _protected_repetition:
                # Low frequency but very recent — original recency check as fallback
                if not allows_hard_repetition_forbid(motif.category):
                    if len(soft_forbidden_themes) < 3 and motif.name not in soft_forbidden_themes:
                        soft_forbidden_themes.append(motif.name)
                elif len(result["forbidden_repetition"]) < forbidden_limit:
                    result["forbidden_repetition"].append(motif.name)

        # Add suggestions only for motifs related to the current chapter window.
        suggestions = self.get_suggestions_for_chapter(current_chapter, prompt_safe=True)
        if related_ids:
            suggestions = [
                s
                for s in suggestions
                if str(getattr(s, "motif_id", "") or "").strip() in related_ids
            ]
        else:
            suggestions = []
        result["suggested_callbacks"] = [
            {
                "motif": s.motif_name,
                "reason": s.reason,
                "priority": s.priority,
            }
            for s in suggestions[:3]
        ]

        if chapter_text:
            result["repeated_phrases"] = self.extract_repeated_phrases(chapter_text)

        self._log_motif_stats(result, current_chapter)

        _log.debug(
            "motifs_for_prompt | chapter=%d | active_count=%d | forbidden=%d | suggestions=%d | repeated_phrases=%d",
            current_chapter,
            len(result["active_motifs"]),
            len(result["forbidden_repetition"]),
            len(result["suggested_callbacks"]),
            len(result["repeated_phrases"]),
        )

        if include_retired_in_stats:
            retired_motifs = [m for m in self._motifs.values() if m.retired]
            result["retired_stats"] = {
                "count": len(retired_motifs),
                "names": [m.name for m in retired_motifs],
            }

        # Apply token budget capping before returning.
        self._apply_token_budget(result)

        result["soft_forbidden_themes"] = soft_forbidden_themes

        # Build unified guidance synchronously from existing data
        result["unified_guidance"] = self._build_unified_guidance_sync(
            current_chapter=current_chapter,
            active_motifs=result.get("active_motifs", []),
            suggestions=suggestions,
            forbidden=result.get("forbidden_repetition", []),
            soft_forbidden=soft_forbidden_themes,
        )

        return result

    def _build_unified_guidance_sync(
        self,
        current_chapter: int,
        active_motifs: list[dict[str, Any]],
        suggestions: list[MotifSuggestion],
        forbidden: list[str],
        soft_forbidden: list[str] | None = None,
    ) -> MotifGuidanceBundle:
        """Build unified guidance synchronously from existing data."""
        strengthen_items: list[MotifGuidanceItem] = []
        dormant_items: list[MotifGuidanceItem] = []
        pov_items: list[MotifGuidanceItem] = []
        plot_matched_items: list[MotifGuidanceItem] = []
        forbidden_items: list[MotifGuidanceItem] = []
        soft_forbidden_items: list[MotifGuidanceItem] = []
        seen_names: set[str] = set()
        active_by_name = {
            str(m.get("name", "")).strip(): m
            for m in active_motifs
            if str(m.get("name", "")).strip()
        }

        for raw_name in forbidden:
            name = str(raw_name or "").strip()
            if name and name not in seen_names:
                source = active_by_name.get(name, {})
                seen_names.add(name)
                forbidden_items.append(
                    MotifGuidanceItem(
                        motif_id=source.get("motif_id", ""),
                        motif_name=name,
                        category=source.get("category", "意象"),
                        guidance_type="forbidden",
                        reason="近期重复使用，建议避免",
                        priority="high",
                    )
                )

        if soft_forbidden:
            for raw_name in soft_forbidden:
                name = str(raw_name or "").strip()
                if name and name not in seen_names:
                    source = active_by_name.get(name, {})
                    seen_names.add(name)
                    soft_forbidden_items.append(
                        MotifGuidanceItem(
                            motif_id=source.get("motif_id", ""),
                            motif_name=name,
                            category=source.get("category", "主题"),
                            guidance_type="soft_forbidden",
                            reason="近期高频出现，建议换一种表达方式深化而非直接重复",
                            priority="medium",
                        )
                    )

        # Active history is descriptive, not a request to strengthen it.

        for s in suggestions:
            name = str(getattr(s, "motif_name", "") or "").strip()
            motif_id = str(getattr(s, "motif_id", "") or "")
            motif = self._motifs.get(motif_id)
            category = motif.category if motif is not None else "意象"
            if name and name not in seen_names and allows_prompt_callback(category):
                seen_names.add(name)
                dormant_items.append(
                    MotifGuidanceItem(
                        motif_id=motif_id,
                        motif_name=name,
                        category=category,
                        guidance_type="callback",
                        reason=getattr(s, "reason", ""),
                        priority=getattr(s, "priority", "medium"),
                    )
                )

        max_items = _settings_or_env_int(
            "NOVEL_FORGE_MOTIF_MAX_PER_CHAPTER",
            "motif_max_per_chapter",
            3,
        )
        return MotifGuidanceBundle(
            strengthen=strengthen_items[:max_items],
            dormant_callbacks=dormant_items[:max_items],
            pov_motifs=pov_items[:max_items],
            plot_matched=plot_matched_items[:max_items],
            forbidden=forbidden_items[:max_items],
            soft_forbidden_themes=soft_forbidden_items[:max_items],
            chapter_number=current_chapter,
        )

    @staticmethod
    def _resolve_forbidden_repetition_limit(
        configured: int | None,
        *,
        max_motifs: int,
    ) -> int:
        """Resolve how many avoid-repetition hints may enter a prompt.

        The motif system should warn about the strongest recent rhetorical repeats,
        not turn every nearby image into a hard ban. A small default keeps generation
        focused and reduces the chance of damaging continuity to avoid low-value words.
        """
        if configured is None:
            configured = _settings_or_env_int(
                "NOVEL_FORGE_MOTIF_FORBIDDEN_MAX_ITEMS",
                "motif_forbidden_max_items",
                3,
            )
        return max(0, min(max(0, int(configured or 0)), max(0, int(max_motifs or 0))))

    def _get_warmup_motifs_for_prompt(
        self,
        current_chapter: int,
        max_motifs: int = 5,
    ) -> dict[str, Any]:
        """Return motif data during warmup phase (chapters 1-N, no LLM).

        During warmup:
        - forbidden_repetition: empty (no history to compare)
        - active_motifs: warmup seeds classified as motifs
        - suggested_callbacks: empty (no LLM extraction yet)
        - repeated_phrases: from algorithmic detection if chapter_text provided
        """
        active: list[dict[str, Any]] = []
        for motif in self._motifs.values():
            if motif.retired:
                continue
            if not motif.motif_id.startswith("warmup_"):
                continue
            motif_data = {
                "motif_id": motif.motif_id,
                "name": motif.name,
                "category": motif.category,
                "occurrence_count": motif.occurrence_count,
                "thematic_meaning": motif.thematic_meaning,
                "motif_role": motif.metadata.get("motif_role", "unknown"),
                "importance_score": self._motif_importance_score(motif),
            }
            active.append(motif_data)
            if len(active) >= max_motifs:
                break

        result: dict[str, Any] = {
            "active_motifs": active,
            "forbidden_repetition": [],
            "suggested_callbacks": [],
            "repeated_phrases": [],
        }

        return result

    def _apply_token_budget(self, result: dict[str, Any]) -> None:
        """Report motif pressure without mutating selected continuity evidence."""
        budget = MotifPromptBudget.from_env()
        estimated_tokens = estimate_dict_tokens(result)

        if estimated_tokens <= budget.budget_tokens:
            return
        _log.warning(
            "motif_token_budget | overflow_tokens=%d | estimated_tokens=%d | "
            "budget=%d | selected_evidence_preserved=true | hard_truncation_allowed=false | "
            "action=route_larger_context_or_partition_complete_coverage",
            estimated_tokens - budget.budget_tokens,
            estimated_tokens,
            budget.budget_tokens,
        )

    def _log_motif_stats(
        self,
        result: dict[str, Any],
        chapter_number: int,
    ) -> None:
        """Log motif statistics for prompt injection performance tracking."""
        active_count = len(result["active_motifs"])
        forbidden_count = len(result["forbidden_repetition"])
        estimated_tokens = estimate_dict_tokens(result)
        _log.debug(
            "motif_stats | chapter=%d | active_count=%d | forbidden_count=%d | estimated_tokens=%d",
            chapter_number,
            active_count,
            forbidden_count,
            estimated_tokens,
        )

    async def get_forward_looking_guidance(
        self,
        current_chapter: int,
        chapter_outline: dict[str, Any] | None = None,
        dormant_callback_min_chapters: int = 20,
    ) -> dict[str, Any]:
        """Generate forward-looking motif guidance for the next chapter.

        Pure rule-based analysis — NO LLM calls. Uses existing motif tracking
        data to identify which motifs deserve attention in upcoming writing.

        Args:
            current_chapter: Chapter being written (guidance is for this chapter)
            chapter_outline: Optional outline with goal, pov_character, element_focus

        Returns:
            Dict with four categories of guidance, each capped at 3 items,
            total capped at 8 items:
            - strengthen_motifs: Recently miss/weak motifs from element_progress
            - dormant_callbacks: Motifs not seen in dormant_callback_min_chapters+ chapters
            - pov_motifs: Motifs associated with current POV character
            - plot_matched_motifs: Motifs whose thematic_meaning matches chapter goal
        """
        outline_goal = str((chapter_outline or {}).get("goal", "") or "")
        goal = outline_goal.lower()
        pov_character = (chapter_outline or {}).get("pov_character", "")
        dormant_gap = max(1, int(dormant_callback_min_chapters))
        self.prune_ephemeral_motifs(current_chapter)

        strengthen_motifs: list[dict[str, Any]] = []
        dormant_callbacks: list[dict[str, Any]] = []
        pov_motifs: list[dict[str, Any]] = []
        plot_matched_motifs: list[dict[str, Any]] = []

        for motif_id, motif in self._motifs.items():
            if motif.retired:
                continue
            if not allows_forward_prompt_guidance(motif.category):
                continue

            chapters_since = current_chapter - motif.last_appearance_chapter

            relevant = self._requested_by_chapter(motif, chapter_outline)
            similarity = 0.0
            if goal and motif.thematic_meaning:
                similarity = await self._semantic_matcher.match_motif_to_goal(
                    motif.thematic_meaning, outline_goal
                )
                relevant = relevant or similarity >= self._semantic_matcher.threshold
            if not relevant:
                continue

            # ── Dormant callbacks: not seen for the configured long-cycle gap ──
            if chapters_since >= dormant_gap and motif.occurrence_count >= 2:
                dormant_callbacks.append(
                    {
                        "motif_id": motif_id,
                        "name": motif.name,
                        "category": motif.category,
                        "chapters_since": chapters_since,
                        "thematic_meaning": motif.thematic_meaning,
                    }
                )

            # ── POV motifs: associated with current POV character ──
            if pov_character and pov_character in motif.associated_characters:
                pov_motifs.append(
                    {
                        "motif_id": motif_id,
                        "name": motif.name,
                        "category": motif.category,
                        "last_seen": motif.last_appearance_chapter,
                        "thematic_meaning": motif.thematic_meaning,
                    }
                )

            # ── Plot-matched motifs: thematic_meaning semantically matches chapter goal ──
            if goal and motif.thematic_meaning:
                if similarity >= self._semantic_matcher.threshold:
                    plot_matched_motifs.append(
                        {
                            "motif_id": motif_id,
                            "name": motif.name,
                            "category": motif.category,
                            "thematic_meaning": motif.thematic_meaning,
                            "match_reason": f"主题含义与本章目标「{outline_goal}」语义相似（{similarity:.2f}）",
                            "similarity": similarity,
                        }
                    )

        # Occurrence counts and elapsed chapters do not establish a need to
        # strengthen an expression. Keep the legacy output key empty.

        # ── Apply caps: each category max 3, total max 4 ──
        per_category_max = 3
        total_max = 4

        strengthen_motifs = strengthen_motifs[:per_category_max]
        dormant_callbacks = dormant_callbacks[:per_category_max]
        pov_motifs = pov_motifs[:per_category_max]
        plot_matched_motifs = plot_matched_motifs[:per_category_max]

        # Total cap: distribute proportionally if over limit
        all_items = strengthen_motifs + dormant_callbacks + pov_motifs + plot_matched_motifs
        if len(all_items) > total_max:
            # Prioritize: strengthen > dormant > pov > plot_matched
            all_items = all_items[:total_max]
            # Re-distribute back to categories
            strengthen_motifs = [
                item
                for item in all_items
                if "reason" in item and "仅出现" in item.get("reason", "")
            ][:per_category_max]
            remaining = [item for item in all_items if item not in strengthen_motifs]
            dormant_callbacks = [item for item in remaining if "chapters_since" in item][
                :per_category_max
            ]
            remaining2 = [item for item in remaining if item not in dormant_callbacks]
            pov_motifs = [
                item for item in remaining2 if "last_seen" in item and "match_reason" not in item
            ][:per_category_max]
            plot_matched_motifs = [item for item in remaining2 if "match_reason" in item][
                :per_category_max
            ]

        result = {
            "strengthen_motifs": strengthen_motifs,
            "dormant_callbacks": dormant_callbacks,
            "pov_motifs": pov_motifs,
            "plot_matched_motifs": plot_matched_motifs,
        }

        _log.info(
            "forward_looking_guidance | chapter=%d | strengthen=%d | dormant=%d | pov=%d | plot_matched=%d",
            current_chapter,
            len(strengthen_motifs),
            len(dormant_callbacks),
            len(pov_motifs),
            len(plot_matched_motifs),
        )

        return result

    async def _build_unified_guidance(
        self,
        current_chapter: int,
        chapter_outline: dict[str, Any] | None = None,
        chapter_text: str = "",
    ) -> MotifGuidanceBundle:
        """Build unified guidance combining forward-looking and repetition analysis.

        Merges results from ``get_forward_looking_guidance()`` and
        ``get_motifs_for_prompt()`` (forbidden repetition) into a single
        ``MotifGuidanceBundle``.

        Args:
            current_chapter: Chapter being written
            chapter_outline: Optional outline with goal, pov_character, element_focus
            chapter_text: Optional chapter text for phrase-level repetition detection

        Returns:
            MotifGuidanceBundle with all guidance categories populated
        """
        bundle = MotifGuidanceBundle(chapter_number=current_chapter)

        fwd = await self.get_forward_looking_guidance(
            current_chapter=current_chapter,
            chapter_outline=chapter_outline,
        )

        prompt_data = self.get_motifs_for_prompt(
            current_chapter=current_chapter,
            chapter_text=chapter_text,
        )
        forbidden_names = {
            str(name or "").strip()
            for name in prompt_data.get("forbidden_repetition", [])
            if str(name or "").strip()
        }

        for item in fwd.get("strengthen_motifs", []):
            name = str(item.get("name", "") or "").strip()
            if not name or name in forbidden_names:
                continue
            if not allows_unified_strengthen(item.get("category", "意象")):
                continue
            bundle.strengthen.append(
                MotifGuidanceItem(
                    motif_id=item.get("motif_id", ""),
                    motif_name=name,
                    category=item.get("category", "意象"),
                    guidance_type="strengthen",
                    reason=item.get("reason", ""),
                    priority="medium",
                    chapters_since=current_chapter - item.get("last_seen", current_chapter),
                )
            )

        for item in fwd.get("dormant_callbacks", []):
            name = str(item.get("name", "") or "").strip()
            if not name or name in forbidden_names:
                continue
            if not allows_prompt_callback(item.get("category", "意象")):
                continue
            bundle.dormant_callbacks.append(
                MotifGuidanceItem(
                    motif_id=item.get("motif_id", ""),
                    motif_name=name,
                    category=item.get("category", "意象"),
                    guidance_type="dormant_callback",
                    reason="与本章目标相关的可选表达",
                    priority="medium",
                    thematic_meaning=item.get("thematic_meaning", ""),
                    chapters_since=item.get("chapters_since", 0),
                )
            )

        for item in fwd.get("pov_motifs", []):
            name = str(item.get("name", "") or "").strip()
            if not name or name in forbidden_names:
                continue
            if not allows_forward_prompt_guidance(item.get("category", "意象")):
                continue
            bundle.pov_motifs.append(
                MotifGuidanceItem(
                    motif_id=item.get("motif_id", ""),
                    motif_name=name,
                    category=item.get("category", "意象"),
                    guidance_type="pov",
                    reason="POV 角色关联母题",
                    priority="medium",
                    thematic_meaning=item.get("thematic_meaning", ""),
                )
            )

        for item in fwd.get("plot_matched_motifs", []):
            name = str(item.get("name", "") or "").strip()
            if not name or name in forbidden_names:
                continue
            if not allows_forward_prompt_guidance(item.get("category", "意象")):
                continue
            bundle.plot_matched.append(
                MotifGuidanceItem(
                    motif_id=item.get("motif_id", ""),
                    motif_name=name,
                    category=item.get("category", "意象"),
                    guidance_type="plot_matched",
                    reason=item.get("match_reason", ""),
                    priority="high",
                    thematic_meaning=item.get("thematic_meaning", ""),
                    similarity=item.get("similarity", 0.0),
                )
            )

        for name in prompt_data.get("forbidden_repetition", []):
            name = str(name or "").strip()
            if not name:
                continue
            bundle.forbidden.append(
                MotifGuidanceItem(
                    motif_id="",
                    motif_name=name,
                    category="意象",
                    guidance_type="forbidden",
                    reason="近期重复使用，建议避免",
                    priority="high",
                )
            )

        _log.info(
            "unified_guidance_built | chapter=%d | strengthen=%d | dormant=%d | pov=%d | plot_matched=%d | forbidden=%d",
            current_chapter,
            len(bundle.strengthen),
            len(bundle.dormant_callbacks),
            len(bundle.pov_motifs),
            len(bundle.plot_matched),
            len(bundle.forbidden),
        )

        return bundle

    def check_motif_variation(
        self,
        chapter_number: int,
        chapter_text: str,
    ) -> dict[str, Any]:
        """Check motif variation diversity in chapter text.

        Pure rule-based analysis — NO LLM calls.  Examines the chapter text
        for motif variety, category distribution, and repetition patterns.

        Args:
            chapter_number: Chapter number being checked
            chapter_text: Full chapter text

        Returns:
            Dict with variation metrics:
            - unique_motifs: number of distinct motifs found
            - category_distribution: dict mapping category → count
            - repetition_score: 0.0-1.0 (higher = more repetition)
            - has_sufficient_variation: bool
            - repeated_phrases: list of repeated phrases detected
            - expression_channel_warnings: list of repeated expression channels
        """
        chapter_motif_ids = self._chapter_motifs.get(chapter_number, set())
        active_motifs = [
            self._motifs[mid]
            for mid in chapter_motif_ids
            if mid in self._motifs and not self._motifs[mid].retired
        ]

        category_dist: dict[str, int] = defaultdict(int)
        for motif in active_motifs:
            category_dist[motif.category] += 1

        repeated = self.extract_repeated_phrases(chapter_text)

        unique_count = len(active_motifs)
        repetition_score = 0.0
        if unique_count > 0:
            repetition_score = min(1.0, len(repeated) / max(1, unique_count))

        has_sufficient = unique_count >= 2 and repetition_score < 0.7

        # Expression channel analysis
        channel_warnings: list[dict[str, Any]] = []
        if chapter_text:
            current_channel = classify_expression_channel(chapter_text)
            if current_channel:
                # Check recent chapters for same channel usage
                lookback_chapters = 2
                for occ in self._occurrence_log:
                    if (
                        occ.chapter_number >= chapter_number - lookback_chapters
                        and occ.chapter_number < chapter_number
                    ):
                        occ_channel = classify_expression_channel(occ.text_snippet or "")
                        if occ_channel == current_channel:
                            channel_warnings.append(
                                {
                                    "motif_id": occ.motif_id,
                                    "motif_name": occ.motif_name
                                    if hasattr(occ, "motif_name")
                                    else "",
                                    "repeated_channel": current_channel,
                                    "previous_chapter": occ.chapter_number,
                                }
                            )

        result: dict[str, Any] = {
            "unique_motifs": unique_count,
            "category_distribution": dict(category_dist),
            "repetition_score": round(repetition_score, 3),
            "has_sufficient_variation": has_sufficient,
            "repeated_phrases": repeated,
            "expression_channel_warnings": channel_warnings,
        }

        _log.debug(
            "motif_variation_check | chapter=%d | unique=%d | repetition=%.3f | sufficient=%s | channel_warnings=%d",
            chapter_number,
            unique_count,
            repetition_score,
            has_sufficient,
            len(channel_warnings),
        )

        return result

    def delete_chapter_motifs(
        self,
        chapter_number: int,
        cache_occurrence_counts: dict[str, int] | None = None,
    ) -> int:
        """Delete all motif data for a specific chapter.

        This is called when a chapter is regenerated to remove stale motif data.

        Args:
            chapter_number: Chapter number to delete motif data for
            cache_occurrence_counts: Optional per-motif occurrence counts from
                the external motif cache.  Used as a fallback when
                ``_occurrence_log`` is empty (e.g. after deserialization).

        Returns:
            Number of motif occurrences removed
        """
        removed_count = 0

        # Count actual occurrences per motif in this chapter from the log.
        # _occurrence_log may be empty after deserialization (it is not
        # persisted), so fall back to cache_occurrence_counts or 1.
        chapter_occ_counts: dict[str, int] = {}
        for occ in self._occurrence_log:
            if occ.chapter_number == chapter_number:
                chapter_occ_counts[occ.motif_id] = chapter_occ_counts.get(occ.motif_id, 0) + 1
        if not chapter_occ_counts and cache_occurrence_counts:
            chapter_occ_counts = dict(cache_occurrence_counts)

        motif_ids_in_chapter = self._chapter_motifs.get(chapter_number, set())

        for motif_id in motif_ids_in_chapter:
            if motif_id in self._recent_usage:
                new_usage = [ch for ch in self._recent_usage[motif_id] if ch != chapter_number]
                self._recent_usage[motif_id] = new_usage

            if motif_id in self._motifs:
                motif = self._motifs[motif_id]
                count_to_remove = chapter_occ_counts.get(motif_id, 1)
                motif.occurrence_count = max(0, motif.occurrence_count - count_to_remove)

                if motif.last_appearance_chapter == chapter_number:
                    motif.last_appearance_chapter = max(
                        0, max((ch for ch in self._recent_usage.get(motif_id, [])), default=0)
                    )

                if motif.first_appearance_chapter == chapter_number:
                    motif.first_appearance_chapter = min(
                        self._recent_usage.get(motif_id, []) or [0]
                    )

                if motif.occurrence_count <= 0:
                    del self._motifs[motif_id]

        self._occurrence_log = [
            occ for occ in self._occurrence_log if occ.chapter_number != chapter_number
        ]

        if chapter_number in self._chapter_motifs:
            removed_count = len(self._chapter_motifs[chapter_number])
            del self._chapter_motifs[chapter_number]

        # Clear extraction cache for this chapter so reruns get fresh LLM results.
        self._extraction_cache.pop(chapter_number, None)
        self._extraction_cache_fingerprints.pop(chapter_number, None)

        _log.info(
            "Deleted motif data for chapter %d | occurrences_removed=%d",
            chapter_number,
            removed_count,
        )

        return removed_count

    def delete_chapters_from(self, from_chapter: int) -> int:
        """Delete motif data for all chapters from from_chapter onwards.

        This is called when downstream chapters are invalidated after an upstream rewrite.

        Args:
            from_chapter: Start chapter number (inclusive) to delete motif data for

        Returns:
            Total number of motif occurrences removed
        """
        total_removed = 0
        chapters_to_delete = list(self._chapter_motifs.keys())

        for ch in chapters_to_delete:
            if ch >= from_chapter:
                total_removed += self.delete_chapter_motifs(ch)

        _log.info(
            "Deleted motif data from chapter %d onwards | total_removed=%d",
            from_chapter,
            total_removed,
        )

        return total_removed


__all__ = [
    "Motif",
    "MotifGuidanceBundle",
    "MotifGuidanceItem",
    "MotifOccurrence",
    "MotifSuggestion",
    "MotifTracker",
    "RepetitionWarning",
]
