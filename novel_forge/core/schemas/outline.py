"""Story outline and per-chapter outline schemas."""

from __future__ import annotations

import re
from typing import Any, ClassVar

from pydantic import ConfigDict, Field, field_validator, model_validator

from novel_forge.core.response_repair import coerce_dependency_ref_list
from novel_forge.core.schemas.base import VersionedSchema
from novel_forge.core.schemas.blueprint_elements import BlueprintElementSelection
from novel_forge.core.utils.type_coerce import stringify_text_value

# ── 追读力规划字段 ───────────────────────────────────────────────────────

_LEADING_BRACKETED_BEAT_LABEL_RE = re.compile(r"^\s*[【\[][^】\]]{1,32}[】\]]\s*")
_LEADING_NUMBERED_BEAT_LABEL_RE = re.compile(r"^\s*节拍\s*[\d一二三四五六七八九十百]+\s*[：:]\s*")
OUTLINE_TITLE_REPAIR_PLACEHOLDER = "标题待补"
_CHAPTER_TITLE_PUNCT_RE = re.compile(r"[，,。；;：:、！？!?\n\r]")
_CHAPTER_TITLE_DASH_RE = re.compile(r"(?:——|--|…)")
_CHAPTER_NUMBER_TITLE_RE = re.compile(r"^\s*第\s*[\d一二三四五六七八九十百]+\s*章")
_TITLE_SENTENCE_STRUCTURE_RE = re.compile(r"(?:与|和).{0,12}在")


def normalize_outline_beat_text(beat: Any) -> str:
    """Normalize one outline beat to a plain plot sentence without display labels."""
    text = " ".join(str(beat or "").split()).strip()
    if not text:
        return ""
    text = _LEADING_NUMBERED_BEAT_LABEL_RE.sub("", text).strip()
    while True:
        match = _LEADING_BRACKETED_BEAT_LABEL_RE.match(text)
        if not match:
            break
        rest = text[match.end() :].strip()
        if not rest:
            break
        text = rest
        text = _LEADING_NUMBERED_BEAT_LABEL_RE.sub("", text).strip()
    return text


def normalize_outline_beats(beats: Any) -> list[str]:
    """Normalize the free-text items inside ``beats_summary``."""
    if not isinstance(beats, list):
        beats = [beats] if beats else []
    normalized: list[str] = []
    for beat in beats:
        text = normalize_outline_beat_text(beat)
        if text:
            normalized.append(text)
    return normalized


def outline_chapter_title_repair_reason(
    title: Any,
    *,
    goal: Any = "",
    main_plot_points: Any = None,
    beats_summary: Any = None,
) -> str | None:
    """Return why a chapter title should be regenerated, or ``None`` if usable."""
    text = " ".join(str(title or "").split()).strip()
    if not text:
        return "missing"
    if text == OUTLINE_TITLE_REPAIR_PLACEHOLDER:
        return "placeholder"
    if len(text) < 2:
        return "too_short"
    if len(text) > 14:
        return "too_long"
    if _CHAPTER_NUMBER_TITLE_RE.search(text):
        return "chapter_number_only"
    if _CHAPTER_TITLE_PUNCT_RE.search(text) or _CHAPTER_TITLE_DASH_RE.search(text):
        return "contains_sentence_punctuation"
    if _TITLE_SENTENCE_STRUCTURE_RE.search(text):
        return "sentence_structure"

    for source in (
        goal,
        _first_outline_title_source(main_plot_points),
        _first_outline_title_source(beats_summary),
    ):
        source_text = " ".join(str(source or "").split()).strip()
        if source_text and len(text) >= 8 and source_text.startswith(text):
            return "summary_prefix"
    return None


def outline_chapter_title_needs_repair(
    title: Any,
    *,
    goal: Any = "",
    main_plot_points: Any = None,
    beats_summary: Any = None,
) -> bool:
    """Return True when a chapter title is absent or looks like a plot sentence."""
    return (
        outline_chapter_title_repair_reason(
            title,
            goal=goal,
            main_plot_points=main_plot_points,
            beats_summary=beats_summary,
        )
        is not None
    )


def _first_outline_title_source(value: Any) -> str:
    if isinstance(value, list):
        for item in value:
            text = " ".join(str(item or "").split()).strip()
            if text:
                return text
    return ""


class HookPlan(VersionedSchema):
    """大纲阶段的章尾钩子规划。"""

    _correct_fields: ClassVar[set[str]] = {"hook_type", "hook_strength", "hook_description"}

    hook_type: str = Field(
        default="",
        description="钩子类型：crisis/mystery/emotion/choice/desire",
    )
    hook_strength: str = Field(
        default="",
        description="钩子强度：strong/medium/weak",
    )
    hook_description: str = Field(
        default="",
        description="钩子内容描述，供下章 Planning 使用",
    )


class PayoffPlan(VersionedSchema):
    """大纲阶段的微兑现规划。"""

    _correct_fields: ClassVar[set[str]] = {"payoff_type", "description"}

    payoff_type: str = Field(
        default="information",
        description=(
            "兑现类型：information/relationship/ability/resource/recognition/emotion/clue"
        ),
    )
    description: str = Field(
        default="",
        description="兑现内容描述",
    )

    @field_validator("payoff_type", mode="before")
    @classmethod
    def normalize_payoff_type(cls, value: Any) -> str:
        normalized = str(value or "").lower().strip()
        aliases = {
            "info": "information",
            "情报": "information",
            "信息": "information",
            "关系": "relationship",
            "感情": "relationship",
            "能力": "ability",
            "资源": "resource",
            "认可": "recognition",
            "情绪": "emotion",
            "线索": "clue",
        }
        normalized = aliases.get(normalized, normalized)
        valid = {
            "information",
            "relationship",
            "ability",
            "resource",
            "recognition",
            "emotion",
            "clue",
        }
        return normalized if normalized in valid else "information"

    @field_validator("description", mode="before")
    @classmethod
    def _coerce_payoff_description(cls, v: Any) -> str:
        return stringify_text_value(v)


def _coerce_outline_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = stringify_text_value(item)
        if text and text not in seen:
            seen.add(text)
            normalized.append(text)
    return normalized


class ChapterCastPlan(VersionedSchema):
    """Canonical entity-id cast constraints for a chapter outline."""

    _correct_fields: ClassVar[set[str]] = {
        "pov_entity_id",
        "required_character_ids",
        "support_character_ids",
        "mention_only_entity_ids",
        "forbidden_active_character_ids",
    }

    pov_entity_id: str = Field(default="", description="Canonical POV character entity_id.")
    required_character_ids: list[str] = Field(
        default_factory=list,
        description="Character entity_ids that must actively drive this chapter.",
    )
    support_character_ids: list[str] = Field(
        default_factory=list,
        description="Character entity_ids allowed to appear as supporting active cast.",
    )
    mention_only_entity_ids: list[str] = Field(
        default_factory=list,
        description="Entity ids allowed only as references, not active present-tense actors.",
    )
    forbidden_active_character_ids: list[str] = Field(
        default_factory=list,
        description="Character entity_ids that must not actively appear in this chapter.",
    )

    @field_validator(
        "pov_entity_id",
        mode="before",
    )
    @classmethod
    def _coerce_cast_text(cls, value: Any) -> str:
        return stringify_text_value(value)

    @field_validator(
        "required_character_ids",
        "support_character_ids",
        "mention_only_entity_ids",
        "forbidden_active_character_ids",
        mode="before",
    )
    @classmethod
    def _coerce_cast_lists(cls, value: Any) -> list[str]:
        return _coerce_outline_str_list(value)

    @model_validator(mode="after")
    def normalize_cast(self) -> ChapterCastPlan:
        if self.pov_entity_id and self.pov_entity_id not in self.required_character_ids:
            self.required_character_ids.insert(0, self.pov_entity_id)
        required = set(self.required_character_ids)
        self.support_character_ids = [
            item for item in self.support_character_ids if item not in required
        ]
        active = required | set(self.support_character_ids)
        self.mention_only_entity_ids = [
            item for item in self.mention_only_entity_ids if item not in active
        ]
        self.forbidden_active_character_ids = [
            item for item in self.forbidden_active_character_ids if item not in active
        ]
        return self


class ChapterEmotionalPlan(VersionedSchema):
    """Executable emotional intent projected into chapter planning."""

    _correct_fields: ClassVar[set[str]] = {
        "subject_entity_id",
        "entry_state",
        "pressure_source",
        "relationship_choice",
        "turning_emotion",
        "exit_aftertaste",
        "expression_channels",
    }

    subject_entity_id: str = Field(default="", description="Entity id that owns the chapter arc.")
    entry_state: str = Field(default="", description="Opening emotional state.")
    pressure_source: str = Field(default="", description="Concrete external/internal pressure.")
    relationship_choice: str = Field(default="", description="Choice that expresses relationship change.")
    turning_emotion: str = Field(default="", description="Emotional turn reached during the chapter.")
    exit_aftertaste: str = Field(default="", description="Emotional residue at chapter exit.")
    expression_channels: list[str] = Field(
        default_factory=list,
        description="Preferred concrete channels: action, dialogue, body signal, object, silence.",
    )

    @field_validator(
        "subject_entity_id",
        "entry_state",
        "pressure_source",
        "relationship_choice",
        "turning_emotion",
        "exit_aftertaste",
        mode="before",
    )
    @classmethod
    def _coerce_emotional_text(cls, value: Any) -> str:
        return stringify_text_value(value)

    @field_validator("expression_channels", mode="before")
    @classmethod
    def _coerce_expression_channels(cls, value: Any) -> list[str]:
        return _coerce_outline_str_list(value)


class ChapterEmotionalBrief(VersionedSchema):
    """Upstream evidence for the outline LLM, not a locally authored emotion arc."""

    _correct_fields: ClassVar[set[str]] = {
        "subject_entity_id",
        "pressure_evidence",
        "arc_evidence",
    }

    subject_entity_id: str = ""
    pressure_evidence: list[str] = Field(default_factory=list)
    arc_evidence: list[str] = Field(default_factory=list)

    @field_validator("subject_entity_id", mode="before")
    @classmethod
    def _coerce_subject_entity_id(cls, value: Any) -> str:
        return stringify_text_value(value)

    @field_validator("pressure_evidence", "arc_evidence", mode="before")
    @classmethod
    def _coerce_evidence(cls, value: Any) -> list[str]:
        return _coerce_outline_str_list(value)


class ChapterDesignMatrixEntry(VersionedSchema):
    """Pre-outline chapter design contract built from canonical upstream assets."""

    _correct_fields: ClassVar[set[str]] = {
        "chapter_number",
        "plot_duties",
        "cast_plan",
        "emotional_brief",
        "knowledge_boundary",
        "hook_payoff_duties",
        "scene_design_goals",
    }

    chapter_number: int = Field(ge=1)
    plot_duties: list[str] = Field(default_factory=list)
    cast_plan: ChapterCastPlan = Field(default_factory=ChapterCastPlan)
    emotional_brief: ChapterEmotionalBrief = Field(default_factory=ChapterEmotionalBrief)
    knowledge_boundary: list[str] = Field(default_factory=list)
    hook_payoff_duties: list[str] = Field(default_factory=list)
    scene_design_goals: list[str] = Field(default_factory=list)

    @field_validator(
        "plot_duties",
        "knowledge_boundary",
        "hook_payoff_duties",
        "scene_design_goals",
        mode="before",
    )
    @classmethod
    def _coerce_matrix_lists(cls, value: Any) -> list[str]:
        return _coerce_outline_str_list(value)


class ChapterDesignMatrix(VersionedSchema):
    """Canonical pre-outline design matrix keyed by chapter number."""

    _correct_fields: ClassVar[set[str]] = {
        "total_chapters",
        "entity_catalog",
        "chapters",
    }

    total_chapters: int = Field(default=0, ge=0)
    entity_catalog: list[dict[str, Any]] = Field(default_factory=list)
    chapters: list[ChapterDesignMatrixEntry] = Field(default_factory=list)

    def by_chapter(self) -> dict[int, ChapterDesignMatrixEntry]:
        return {item.chapter_number: item for item in self.chapters}


class ChapterOutline(VersionedSchema):
    """Outline for a single chapter."""

    _correct_fields: ClassVar[set[str]] = {
        "chapter_number",
        "title",
        "goal",
        "beats_summary",
        "main_plot_points",
        "subplot_points",
        "subplot_focus",
        "element_focus",
        "pov_character_id",
        "pov_character_name",
        "pov_character",
        "pov_switch",
        "setting",
        "expected_word_count",
        "involved_character_ids",
        "required_character_ids",
        "support_character_ids",
        "involved_character_names",
        "involved_characters",
        "cast_plan",
        "emotional_plan",
        "scene_design_goals",
        "notes",
        "time_anchor",
        "time_span",
        "time_gap_from_prev",
        "countdown_state",
        "is_flashback",
        "expected_hook",
        "expected_payoffs",
    }
    _field_aliases: ClassVar[dict[str, str]] = {
        "chapter_title": "title",
        "chapterTitle": "title",
        "focus_elements": "element_focus",
        "element_focus_ids": "element_focus",
        "pov": "pov_character",
        "pov_entity_id": "pov_character_id",
        "povCharacterId": "pov_character_id",
        "povCharacterName": "pov_character_name",
        "primary_setting": "setting",
        "primarySetting": "setting",
        "main_location": "setting",
        "mainLocation": "setting",
        "main_scene": "setting",
        "mainScene": "setting",
        "main_scenes": "setting",
        "mainScenes": "setting",
        "primary_scene": "setting",
        "primaryScene": "setting",
        "primary_scenes": "setting",
        "primaryScenes": "setting",
        "primary_location": "setting",
        "primaryLocation": "setting",
        "location": "setting",
        "locations": "setting",
        "target_word_count": "expected_word_count",
        "targetWordCount": "expected_word_count",
        "estimated_word_count": "expected_word_count",
        "estimatedWordCount": "expected_word_count",
        "note": "notes",
        "chapter_note": "notes",
        "chapter_notes": "notes",
    }

    chapter_number: int = Field(ge=1, description="1-based chapter index.")
    title: str = Field(default="", description="Chapter title.")
    goal: str = Field(description="What this chapter must accomplish narratively.")
    beats_summary: list[str] = Field(
        default_factory=list,
        description="High-level beat descriptions.",
    )
    main_plot_points: list[str] = Field(
        default_factory=list,
        description="Main-plot points this chapter must advance.",
    )
    subplot_points: list[str] = Field(
        default_factory=list,
        description="Subplot points this chapter should touch.",
    )
    subplot_focus: str = Field(
        default="",
        description="Primary subplot id/name for this chapter (optional).",
    )
    element_focus: list[str] = Field(
        default_factory=list,
        description=(
            "Chapter-priority extension element ids (0-3). "
            "Used to reduce prompt noise and enable chapter-level element scheduling."
        ),
    )
    pov_character: str = Field(default="", description="Point-of-view character.")
    pov_character_id: str = Field(default="", description="Canonical POV character entity_id.")
    pov_character_name: str = Field(
        default="",
        description="Display name resolved from pov_character_id.",
    )
    pov_switch: bool = Field(
        default=False,
        description=(
            "True when this chapter deliberately switches POV from the previous chapter "
            "(e.g. antagonist perspective chapters). When True, bridge generation and "
            "continuity evaluation treat the POV change as intentional and suppress "
            "pov_jump warnings."
        ),
    )
    setting: str = Field(default="", description="Primary location(s).")
    expected_word_count: int = Field(default=3000, ge=500)
    involved_characters: list[str] = Field(
        default_factory=list,
        description=(
            "Characters involved in this chapter (including POV character). "
            "Used to scope character profile injection and pronoun checking to only "
            "relevant characters, reducing prompt size and improving accuracy."
        ),
    )
    involved_character_ids: list[str] = Field(
        default_factory=list,
        description="Canonical character entity_ids involved in this chapter.",
    )
    required_character_ids: list[str] = Field(
        default_factory=list,
        description="Canonical character entity_ids required to actively drive this chapter.",
    )
    support_character_ids: list[str] = Field(
        default_factory=list,
        description="Canonical character entity_ids allowed as supporting active cast.",
    )
    involved_character_names: list[str] = Field(
        default_factory=list,
        description="Display names resolved from involved_character_ids.",
    )
    cast_plan: ChapterCastPlan = Field(
        default_factory=ChapterCastPlan,
        description="Canonical chapter cast constraints.",
    )
    emotional_plan: ChapterEmotionalPlan = Field(
        default_factory=ChapterEmotionalPlan,
        description="Executable emotional intent for this chapter.",
    )
    scene_design_goals: list[str] = Field(
        default_factory=list,
        description="Planning goals the chapter-plan stage must realize in scenes.",
    )
    # ── Time constraint fields ─────────────────────────────────────────────
    time_anchor: str = Field(
        default="",
        description=(
            "本章时间锚点，例如 '末世第3天 黄昏'、'庆历六年春 清晨'。"
            "由大纲或 AI 规划阶段填入，用于时间一致性校验。"
        ),
    )
    time_span: str = Field(
        default="",
        description="章内时间跨度，例如 '半天'、'一个时辰'。",
    )
    time_gap_from_prev: str = Field(
        default="",
        description="与上章时间差，例如 '跨夜'、'次日清晨'、'三天后'。",
    )
    countdown_state: str = Field(
        default="",
        description="倒计时状态，例如 '物资耗尽 D-5 → D-4'、'大限将至 第2天/共7天'。",
    )
    is_flashback: bool = Field(
        default=False,
        description="标记本章是否为闪回/回忆章节，为 True 时时间回跳不触发校验警告。",
    )
    notes: str = Field(default="")

    # ── 追读力规划字段 ─────────────────────────────────────────────────
    expected_hook: HookPlan | None = Field(
        default=None,
        description="本章预期的章尾钩子（大纲阶段规划）",
    )
    expected_payoffs: list[PayoffPlan] = Field(
        default_factory=list,
        description="本章预期的微兑现列表",
    )

    @field_validator("beats_summary", mode="before")
    @classmethod
    def normalize_beats_summary(cls, value: Any) -> list[str]:
        """Keep beat text semantically useful and display-format neutral."""
        return normalize_outline_beats(value)

    @field_validator("setting", mode="before")
    @classmethod
    def normalize_setting_text(cls, value: Any) -> str:
        """Normalize LLM scene/location arrays into the single outline setting field."""
        if value is None:
            return ""
        if isinstance(value, list):
            return "、".join(str(item).strip() for item in value if str(item).strip())
        return str(value)

    @field_validator(
        "goal",
        "pov_character",
        "pov_character_id",
        "pov_character_name",
        "notes",
        "subplot_focus",
        mode="before",
    )
    @classmethod
    def _coerce_chapter_outline_text(cls, v: Any) -> str:
        return stringify_text_value(v)

    @field_validator(
        "involved_characters",
        "involved_character_ids",
        "required_character_ids",
        "support_character_ids",
        "involved_character_names",
        "scene_design_goals",
        mode="before",
    )
    @classmethod
    def _coerce_chapter_outline_lists(cls, v: Any) -> list[str]:
        return _coerce_outline_str_list(v)

    @model_validator(mode="after")
    def normalize_element_focus(self) -> ChapterOutline:
        """Normalize chapter-level focus ids: trim, deduplicate and cap size."""
        if not self.element_focus:
            return self._normalize_identity_projections()
        self.element_focus = _coerce_outline_str_list(self.element_focus)[:3]
        return self._normalize_identity_projections()

    def _normalize_identity_projections(self) -> ChapterOutline:
        """Keep ID-first fields and display-name compatibility projections aligned."""
        if self.cast_plan.pov_entity_id and not self.pov_character_id:
            self.pov_character_id = self.cast_plan.pov_entity_id
        if self.pov_character_id and not self.cast_plan.pov_entity_id:
            self.cast_plan.pov_entity_id = self.pov_character_id

        if self.pov_character_name and not self.pov_character:
            self.pov_character = self.pov_character_name
        if self.pov_character and not self.pov_character_name:
            self.pov_character_name = self.pov_character

        if self.cast_plan.required_character_ids and not self.required_character_ids:
            self.required_character_ids = list(self.cast_plan.required_character_ids)
        if self.cast_plan.support_character_ids and not self.support_character_ids:
            self.support_character_ids = list(self.cast_plan.support_character_ids)
        if self.required_character_ids and not self.cast_plan.required_character_ids:
            self.cast_plan.required_character_ids = list(self.required_character_ids)
        if self.support_character_ids and not self.cast_plan.support_character_ids:
            self.cast_plan.support_character_ids = list(self.support_character_ids)

        involved_ids = list(self.involved_character_ids)
        for item in [
            self.pov_character_id,
            *self.required_character_ids,
            *self.support_character_ids,
        ]:
            if item and item not in involved_ids:
                involved_ids.append(item)
        self.involved_character_ids = involved_ids

        if self.involved_character_names and not self.involved_characters:
            self.involved_characters = list(self.involved_character_names)
        if self.involved_characters and not self.involved_character_names:
            self.involved_character_names = list(self.involved_characters)

        if self.emotional_plan.subject_entity_id == "" and self.pov_character_id:
            self.emotional_plan.subject_entity_id = self.pov_character_id
        return self


class VolumeOutline(VersionedSchema):
    """Outline for a volume (part/arc) in long works."""

    _correct_fields: ClassVar[set[str]] = {
        "volume_number",
        "title",
        "start_chapter",
        "end_chapter",
        "arc_goal",
        "milestone_targets",
        "main_conflicts",
        "climax_hint",
        "resolution_hint",
        "notes",
    }

    volume_number: int = Field(ge=1, description="1-based volume index.")
    title: str = Field(default="", description="Volume title.")
    start_chapter: int = Field(ge=1)
    end_chapter: int = Field(ge=1)
    arc_goal: str = Field(default="", description="Narrative goal of this volume.")
    milestone_targets: list[str] = Field(
        default_factory=list,
        description="Milestones expected to be completed within this volume.",
    )
    main_conflicts: list[str] = Field(default_factory=list)
    climax_hint: str = Field(default="")
    resolution_hint: str = Field(default="")
    notes: str = Field(default="")

    @field_validator("title", "arc_goal", "climax_hint", "resolution_hint", "notes", mode="before")
    @classmethod
    def _coerce_volume_text(cls, v: Any) -> str:
        return stringify_text_value(v)

    @model_validator(mode="after")
    def validate_range(self) -> VolumeOutline:
        if self.end_chapter < self.start_chapter:
            raise ValueError("end_chapter must be >= start_chapter")
        return self


class StoryOutline(VersionedSchema):
    """Full-novel outline: ordered list of chapter outlines."""

    _correct_fields: ClassVar[set[str]] = {
        "total_chapters",
        "volume_mode",
        "volumes",
        "chapters",
        "synopsis",
        "hard_through_chapter",
        "planned_through_chapter",
    }

    total_chapters: int = Field(ge=1, description="Planned number of chapters.")
    volume_mode: bool = Field(
        default=False,
        description="Whether the outline uses multi-volume planning.",
    )
    volumes: list[VolumeOutline] = Field(default_factory=list)
    chapters: list[ChapterOutline] = Field(
        min_length=1, description="At least one chapter outline."
    )
    synopsis: str = Field(default="", description="One-paragraph whole-story synopsis.")
    hard_through_chapter: int | None = Field(
        default=None,
        ge=1,
        description="Last chapter whose detailed plan and contracts are committed.",
    )
    planned_through_chapter: int | None = Field(
        default=None,
        ge=1,
        description="Last chapter with a detailed or adjustable preview outline.",
    )

    @field_validator("synopsis", mode="before")
    @classmethod
    def _coerce_story_outline_text(cls, v: Any) -> str:
        return stringify_text_value(v)

    @model_validator(mode="after")
    def _normalize_planning_watermarks(self) -> StoryOutline:
        """Treat legacy outlines without watermarks as fully committed."""

        hard = self.total_chapters if self.hard_through_chapter is None else self.hard_through_chapter
        planned = (
            self.total_chapters
            if self.planned_through_chapter is None
            else self.planned_through_chapter
        )
        hard = max(1, min(int(hard), self.total_chapters))
        planned = max(hard, min(int(planned), self.total_chapters))
        self.hard_through_chapter = hard
        self.planned_through_chapter = planned
        return self


# ---------------------------------------------------------------------------
# Narrative Blueprint — 全局叙事蓝图（指导分批章节大纲生成）
# ---------------------------------------------------------------------------


class NarrativePhase(VersionedSchema):
    """叙事阶段."""

    _correct_fields: ClassVar[set[str]] = {
        "phase_name",
        "chapter_start",
        "chapter_end",
        "description",
        "key_events",
        "tension_level",
        "time_context",
        "primary_locations",
        "key_characters",
    }
    _field_aliases: ClassVar[dict[str, str]] = {
        "phase_description": "description",
        "phase_goal": "description",
        "main_locations": "primary_locations",
        "locations": "primary_locations",
    }

    phase_name: str = Field(default="", description="阶段名称")
    chapter_start: int = Field(default=1, ge=1)
    chapter_end: int = Field(default=1, ge=1)
    description: str = Field(default="", description="本阶段核心叙事目标")
    key_events: list[str] = Field(default_factory=list, description="本阶段关键事件")
    tension_level: str = Field(default="", description="张力水平描述")
    # 叙事锚定：防止跨阶段的时间/空间/人物漂移
    time_context: str = Field(default="", description="本阶段的时间段/时间锚点")
    primary_locations: list[str] = Field(default_factory=list, description="本阶段核心场景")
    key_characters: list[str] = Field(default_factory=list, description="本阶段核心出场人物")

    @field_validator("phase_name", mode="before")
    @classmethod
    def _coerce_phase_name(cls, v: Any) -> str:
        return stringify_text_value(v)

    @field_validator("key_events", "primary_locations", "key_characters", mode="before")
    @classmethod
    def _coerce_str_list(cls, v: Any) -> list[str]:
        if isinstance(v, list):
            return [stringify_text_value(item) for item in v]
        if isinstance(v, str):
            return [v]
        return []


class TurningPoint(VersionedSchema):
    """关键转折点."""

    _correct_fields: ClassVar[set[str]] = {
        "chapter_number",
        "description",
        "location",
        "characters_involved",
    }

    chapter_number: int = Field(default=1, ge=1)
    description: str = Field(default="")
    location: str = Field(default="", description="转折发生的场景")
    characters_involved: list[str] = Field(default_factory=list, description="参与转折的角色")

    @field_validator("description", "location", mode="before")
    @classmethod
    def _coerce_turning_point_text(cls, v: Any) -> str:
        return stringify_text_value(v)


class ArcMilestone(VersionedSchema):
    """角色弧光里程碑."""

    _correct_fields: ClassVar[set[str]] = {"chapter_start", "chapter_end", "description"}

    chapter_start: int = Field(default=1, ge=1)
    chapter_end: int = Field(default=1, ge=1)
    description: str = Field(default="")

    @field_validator("description", mode="before")
    @classmethod
    def _coerce_arc_milestone_text(cls, v: Any) -> str:
        return stringify_text_value(v)


class CharacterArcPlan(VersionedSchema):
    """角色弧光规划."""

    _correct_fields: ClassVar[set[str]] = {"character", "arc_summary", "milestones"}

    character: str = Field(default="", description="角色名")
    arc_summary: str = Field(default="", description="弧光概述")
    milestones: list[ArcMilestone] = Field(default_factory=list)

    @field_validator("character", "arc_summary", mode="before")
    @classmethod
    def _coerce_arc_text_fields(cls, v: Any) -> str:
        return stringify_text_value(v)


class SubplotChapterEvent(VersionedSchema):
    """One chapter-level beat for a subplot lane."""

    _correct_fields: ClassVar[set[str]] = {"chapter_number", "event", "weave_notes", "depends_on"}

    chapter_number: int = Field(
        default=1, ge=1, description="Chapter number where this subplot beat happens"
    )
    event: str = Field(default="", description="What happens to this subplot at this chapter")
    weave_notes: str = Field(
        default="",
        description="本章节点的交织说明：如何与主线或其他支线互动",
    )
    depends_on: list[str] = Field(
        default_factory=list,
        description="依赖的前置支线节点，格式：'支线名:章节号'，如 ['复仇线:5', '身世线:8']",
    )

    @field_validator("event", "weave_notes", mode="before")
    @classmethod
    def _coerce_subplot_event_text(cls, v: Any) -> str:
        return stringify_text_value(v)

    @field_validator("depends_on", mode="before")
    @classmethod
    def normalize_depends_on(cls, value: Any) -> list[str]:
        return coerce_dependency_ref_list(value)


class SuspenseScheduleItem(VersionedSchema):
    """悬念时间表条目 — 用于追读力窗口系统初始化."""

    _correct_fields: ClassVar[set[str]] = {
        "suspense_id",
        "suspense_type",
        "introduce_chapter",
        "resolve_chapter",
        "description",
        "urgency_level",
        "related_subplot",
        "strand_affinity",
    }

    suspense_id: str = Field(default="", description="悬念唯一标识")
    suspense_type: str = Field(
        default="",
        description="悬念类型：mystery/crisis/emotion/choice/desire",
    )
    introduce_chapter: int = Field(default=1, ge=1, description="悬念引入章节")
    resolve_chapter: int = Field(
        default=0,
        ge=0,
        description="悬念计划兑现章节（0=未规划/全书末收束）",
    )
    description: str = Field(default="", description="悬念内容描述")
    urgency_level: str = Field(
        default="normal",
        description="紧迫度：critical/high/normal/low",
    )
    related_subplot: str = Field(default="", description="关联支线名称（可选）")
    strand_affinity: dict[str, float] = Field(
        default_factory=lambda: {"quest": 0.3, "fire": 0.3, "constellation": 0.4},
        description="追读力系统必需：与三条情节线的关联度（quest/fire/constellation）",
    )

    @field_validator(
        "suspense_id", "suspense_type", "description", "related_subplot", mode="before"
    )
    @classmethod
    def _coerce_suspense_text(cls, v: Any) -> str:
        return stringify_text_value(v)


class SubplotWeaveLink(VersionedSchema):
    """支线之间的交织关系，或主线对支线的触发关系。"""

    _correct_fields: ClassVar[set[str]] = {
        "source_type",
        "source_ref",
        "target_subplot",
        "trigger_chapter",
        "link_type",
        "description",
    }

    source_type: str = Field(
        default="",
        description="触发源类型：'main_plot'（主线事件）/ 'subplot'（另一支线）/ 'turning_point'（关键转折）",
    )
    source_ref: str = Field(
        default="",
        description="触发源引用：主线事件描述 / 支线名称 / 转折点描述",
    )
    target_subplot: str = Field(
        default="",
        description="被影响的支线名称",
    )
    trigger_chapter: int = Field(
        default=0,
        ge=0,
        description="触发发生的章节号（0=全书级别，不绑定具体章节）",
    )
    link_type: str = Field(
        default="",
        description="""
关系类型：
【源触发目标（单向）】
- trigger_start: 源触发支线启动
- trigger_turn: 源触发支线转折
- constrain: 源约束支线走向
- enable: 源为支线提供条件
- conflict: 源为支线制造冲突
【目标回馈源（双向）】
- feed_main: 支线结果反哺主线（推动主线转折）
- reveal_key: 支线揭露关键信息（改变主线认知）
- create_tension: 支线为主线制造张力（增加压力）
- theme_echo: 支线呼应主题（升华故事内核）
""",
    )
    description: str = Field(
        default="",
        description="交织关系的具体描述，说明源如何影响目标支线",
    )

    @field_validator("source_ref", "target_subplot", "description", mode="before")
    @classmethod
    def _coerce_weave_link_text(cls, v: Any) -> str:
        return stringify_text_value(v)


class SubplotPlan(VersionedSchema):
    """支线规划.

    ``chapter_events`` is normalized to cover every ``involved_chapters`` entry.
    Auto-synthesized entries use empty ``event`` text plus a provenance marker in
    ``weave_notes`` so renderers can show the timeline node without pretending a
    major event was authored there.
    """

    _correct_fields: ClassVar[set[str]] = {
        "name",
        "description",
        "involved_chapters",
        "chapter_events",
        "weave_links",
        "priority",
        "resolution_chapter",
        "resolution_target",
        "resolution_type",
    }
    _field_aliases: ClassVar[dict[str, str]] = {
        "subplot_name": "name",
        "title": "name",
    }

    name: str = Field(default="", description="支线名称")
    description: str = Field(default="")
    involved_chapters: list[int] = Field(default_factory=list)
    chapter_events: list[SubplotChapterEvent] = Field(
        default_factory=list,
        description=(
            "支线在各章节的节点事件，用于时间线逐节点展示；验证时会为缺失节点的 "
            "involved_chapters 自动补空事件。"
        ),
    )
    weave_links: list[SubplotWeaveLink] = Field(
        default_factory=list,
        description="该支线与其他线索的交织关系",
    )
    priority: str = Field(
        default="normal",
        description="支线优先级：'primary'（准主线级）/ 'normal'（常规）/ 'background'（背景点缀）",
    )
    resolution_chapter: int = Field(
        default=0,
        ge=0,
        description="支线收束章节号（0=未规划）",
    )
    resolution_target: str = Field(
        default="",
        description="收束目标：'main_turning_point:N'（主线第N转折）/ 'theme_echo'（主题呼应）/ 'character_fate:角色名'（角色命运）",
    )
    resolution_type: str = Field(
        default="",
        description="收束类型：'resolve'（问题解决）/ 'reveal'（悬念揭示）/ 'ascend'（价值升华）/ 'merge'（并入主线）",
    )

    @field_validator("name", "description", mode="before")
    @classmethod
    def _coerce_subplot_text_fields(cls, v: Any) -> str:
        return stringify_text_value(v)

    @model_validator(mode="after")
    def normalize_subplot(self) -> SubplotPlan:
        normalized_chapters = sorted(
            {int(ch) for ch in self.involved_chapters if isinstance(ch, int) and ch >= 1}
        )
        self.involved_chapters = normalized_chapters

        normalized_events: list[SubplotChapterEvent] = []
        seen: set[int] = set()
        for event in self.chapter_events:
            chapter = int(getattr(event, "chapter_number", 0) or 0)
            if chapter < 1 or chapter in seen:
                continue
            seen.add(chapter)
            normalized_events.append(
                SubplotChapterEvent(
                    chapter_number=chapter,
                    event=str(getattr(event, "event", "") or "").strip(),
                    weave_notes=str(getattr(event, "weave_notes", "") or "").strip(),
                    depends_on=list(getattr(event, "depends_on", []) or []),
                )
            )
        normalized_events.sort(key=lambda item: item.chapter_number)

        # Keep timeline data shape honest: every involved chapter should have a
        # corresponding node. Empty auto-synthesized events preserve the visual
        # endpoint without inventing narrative content.
        if not normalized_events and normalized_chapters:
            normalized_events = [
                SubplotChapterEvent(chapter_number=chapter, event="")
                for chapter in normalized_chapters
            ]
        elif normalized_events and normalized_chapters:
            event_chapter_set = {event.chapter_number for event in normalized_events}
            orphan_chapters = [
                chapter for chapter in normalized_chapters if chapter not in event_chapter_set
            ]
            if orphan_chapters:
                for chapter in orphan_chapters:
                    normalized_events.append(
                        SubplotChapterEvent(
                            chapter_number=chapter,
                            event="",
                            weave_notes="auto-synthesized: involved_chapter has no chapter_event",
                            depends_on=[],
                        )
                    )
                normalized_events.sort(key=lambda item: item.chapter_number)

        self.chapter_events = normalized_events
        return self


class EmotionalArc(VersionedSchema):
    """情感弧线 — 跟踪整本书的情感起伏曲线。"""

    _correct_fields: ClassVar[set[str]] = {
        "arc_name",
        "emotion_type",
        "peak_chapters",
        "valley_chapters",
        "description",
        "related_characters",
        "related_subplots",
    }

    arc_name: str = Field(default="", description="弧线名称")
    emotion_type: str = Field(
        default="",
        description="情感类型: 紧张/温馨/悬疑/悲壮/欢快",
    )
    peak_chapters: list[int] = Field(
        default_factory=list,
        description="情感高峰章节",
    )
    valley_chapters: list[int] = Field(
        default_factory=list,
        description="情感低谷章节",
    )
    description: str = Field(default="", description="情感变化描述")
    related_characters: list[str] = Field(
        default_factory=list,
        description="相关角色",
    )
    related_subplots: list[str] = Field(
        default_factory=list,
        description="相关支线",
    )

    @field_validator("arc_name", "emotion_type", "description", mode="before")
    @classmethod
    def _coerce_emotional_arc_text(cls, v: Any) -> str:
        return stringify_text_value(v)

    @field_validator("related_characters", "related_subplots", mode="before")
    @classmethod
    def _coerce_str_list(cls, v: Any) -> list[str]:
        if isinstance(v, list):
            return [stringify_text_value(item) for item in v]
        if isinstance(v, str):
            return [v]
        return []


class CausalChain(VersionedSchema):
    """因果链 — 规划一个事件如何触发后续一系列连锁反应。"""

    _correct_fields: ClassVar[set[str]] = {
        "chain_name",
        "trigger_chapter",
        "trigger_action",
        "intermediate_chapters",
        "payoff_chapter",
        "payoff_event",
        "escalation",
        "involved_subplots",
    }

    chain_name: str = Field(default="", description="因果链名称")
    trigger_chapter: int = Field(default=0, ge=0, description="触发章节")
    trigger_action: str = Field(default="", description="触发事件")
    intermediate_chapters: list[int] = Field(
        default_factory=list,
        description="中间发酵章节",
    )
    payoff_chapter: int = Field(default=0, ge=0, description="兑现章节")
    payoff_event: str = Field(default="", description="兑现事件")
    escalation: str = Field(default="", description="如何升级")
    involved_subplots: list[str] = Field(
        default_factory=list,
        description="涉及的支线",
    )

    @field_validator("chain_name", "trigger_action", "payoff_event", "escalation", mode="before")
    @classmethod
    def _coerce_causal_chain_text(cls, v: Any) -> str:
        return stringify_text_value(v)

    @field_validator("involved_subplots", mode="before")
    @classmethod
    def _coerce_str_list(cls, v: Any) -> list[str]:
        if isinstance(v, list):
            return [stringify_text_value(item) for item in v]
        if isinstance(v, str):
            return [v]
        return []


class SubplotCollision(VersionedSchema):
    """支线碰撞点 — 描述多条支线在同一章节的交互。"""

    _correct_fields: ClassVar[set[str]] = {
        "collision_chapter",
        "involved_subplots",
        "collision_type",
        "outcome",
        "ripple_effects",
        "setup_chapters",
    }

    collision_chapter: int = Field(default=0, ge=0, description="碰撞章节")
    involved_subplots: list[str] = Field(
        default_factory=list,
        description="参与碰撞的支线（至少2条）",
    )
    collision_type: str = Field(
        default="",
        description="碰撞类型: 冲突/互助/误导/融合/竞争",
    )
    outcome: str = Field(default="", description="碰撞结果")
    ripple_effects: list[str] = Field(
        default_factory=list,
        description="对其他支线的影响",
    )
    setup_chapters: list[int] = Field(
        default_factory=list,
        description="铺垫章节",
    )

    @field_validator("collision_type", "outcome", mode="before")
    @classmethod
    def _coerce_collision_text(cls, v: Any) -> str:
        return stringify_text_value(v)

    @field_validator("involved_subplots", "ripple_effects", mode="before")
    @classmethod
    def _coerce_str_list(cls, v: Any) -> list[str]:
        if isinstance(v, list):
            return [stringify_text_value(item) for item in v]
        if isinstance(v, str):
            return [v]
        return []


class SubversionPoint(VersionedSchema):
    """反套路点 — 规划一个颠覆读者预期的合理转折。"""

    _correct_fields: ClassVar[set[str]] = {
        "chapter",
        "expected_outcome",
        "actual_outcome",
        "setup_chapters",
        "justification",
        "related_subplots",
    }

    chapter: int = Field(default=0, ge=0, description="章节号")
    expected_outcome: str = Field(default="", description="读者预期的")
    actual_outcome: str = Field(default="", description="实际发生的")
    setup_chapters: list[int] = Field(
        default_factory=list,
        description="铺垫章节",
    )
    justification: str = Field(default="", description="为什么合理")
    related_subplots: list[str] = Field(
        default_factory=list,
        description="相关支线",
    )

    @field_validator("expected_outcome", "actual_outcome", "justification", mode="before")
    @classmethod
    def _coerce_subversion_text(cls, v: Any) -> str:
        return stringify_text_value(v)

    @field_validator("related_subplots", mode="before")
    @classmethod
    def _coerce_str_list(cls, v: Any) -> list[str]:
        if isinstance(v, list):
            return [stringify_text_value(item) for item in v]
        if isinstance(v, str):
            return [v]
        return []


class ChapterRhythmPoint(VersionedSchema):
    """章节节奏点 — 规划单章的目标节奏与节拍模式."""

    _correct_fields: ClassVar[set[str]] = {
        "chapter_number",
        "target_pacing",
        "target_tension",
        "beat_pattern",
        "description",
    }

    chapter_number: int = Field(default=0, ge=0, description="章节号")
    target_pacing: int = Field(
        default=3,
        ge=1,
        le=5,
        description="目标节奏 1-5：1=舒缓，3=中速，5=急促/高压。",
    )
    target_tension: int = Field(
        default=3,
        ge=1,
        le=5,
        description="目标张力 1-5：1=低张力，3=中等，5=峰值。",
    )
    beat_pattern: str = Field(
        default="",
        description='节拍模式（如 "舒缓-窒息-释放"）。',
    )
    description: str = Field(default="", description="本章节奏规划描述")

    @field_validator("target_pacing", "target_tension", mode="before")
    @classmethod
    def _coerce_rhythm_level(cls, v: Any) -> int:
        if isinstance(v, str):
            text = stringify_text_value(v).strip().lower()
            mapping = {
                "slow": 1,
                "low": 1,
                "舒缓": 1,
                "低": 1,
                "medium": 3,
                "中": 3,
                "中等": 3,
                "fast": 4,
                "快": 4,
                "intense": 5,
                "high": 5,
                "peak": 5,
                "高": 5,
                "高潮": 5,
                "峰值": 5,
            }
            if text in mapping:
                return mapping[text]
            try:
                return int(float(text))
            except ValueError:
                return 3
        try:
            return int(v)
        except (TypeError, ValueError):
            return 3

    @field_validator("beat_pattern", "description", mode="before")
    @classmethod
    def _coerce_rhythm_text(cls, v: Any) -> str:
        return stringify_text_value(v)


class PlanOutlineOverviewFragment(VersionedSchema):
    """PLAN_OUTLINE overview fragment response envelope."""

    _correct_fields: ClassVar[set[str]] = {"synopsis", "volume_mode", "volumes"}

    model_config = ConfigDict(extra="forbid")

    synopsis: str = Field(default="", description="全书概述")
    volume_mode: bool = Field(default=False)
    volumes: list[VolumeOutline] = Field(default_factory=list)


class PlanOutlinePhasesFragment(VersionedSchema):
    """PLAN_OUTLINE narrative phases fragment response envelope."""

    _correct_fields: ClassVar[set[str]] = {"narrative_phases"}

    model_config = ConfigDict(extra="forbid")

    narrative_phases: list[NarrativePhase] = Field(default_factory=list)


class PlanOutlineTurningPointsFragment(VersionedSchema):
    """PLAN_OUTLINE turning-points fragment response envelope."""

    _correct_fields: ClassVar[set[str]] = {
        "key_turning_points",
        "causal_chains",
        "subversion_points",
    }

    model_config = ConfigDict(extra="forbid")

    key_turning_points: list[TurningPoint] = Field(default_factory=list)
    causal_chains: list[CausalChain] = Field(
        default_factory=list,
        description="因果链规划",
    )
    subversion_points: list[SubversionPoint] = Field(
        default_factory=list,
        description="反套路点规划",
    )


class PlanOutlineCharacterArcsFragment(VersionedSchema):
    """PLAN_OUTLINE character-arcs fragment response envelope."""

    _correct_fields: ClassVar[set[str]] = {"character_arcs", "emotional_arcs"}

    model_config = ConfigDict(extra="forbid")

    character_arcs: list[CharacterArcPlan] = Field(default_factory=list)
    emotional_arcs: list[EmotionalArc] = Field(
        default_factory=list,
        description="情感弧线规划",
    )


class PlanOutlineSubplotsFragment(VersionedSchema):
    """PLAN_OUTLINE subplot fragment response envelope."""

    _correct_fields: ClassVar[set[str]] = {"subplot_plan", "subplot_collisions"}

    model_config = ConfigDict(extra="forbid")

    subplot_plan: list[SubplotPlan] = Field(default_factory=list)
    subplot_collisions: list[SubplotCollision] = Field(
        default_factory=list,
        description="支线碰撞点规划",
    )


class PlanOutlineSuspenseFragment(VersionedSchema):
    """PLAN_OUTLINE suspense fragment response envelope."""

    _correct_fields: ClassVar[set[str]] = {"suspense_schedule"}

    model_config = ConfigDict(extra="forbid")

    suspense_schedule: list[SuspenseScheduleItem] = Field(
        default_factory=list,
        description="悬念时间表规划（用于追读力窗口系统初始化）",
    )


class PlanOutlineEndingFragment(VersionedSchema):
    """PLAN_OUTLINE ending fragment response envelope."""

    _correct_fields: ClassVar[set[str]] = {"ending_strategy"}

    model_config = ConfigDict(extra="forbid")

    ending_strategy: str = Field(default="", description="最终收束策略")


class NarrativeBlueprint(VersionedSchema):
    """全局叙事蓝图 — 在分批生成章节大纲前，先做高层战略规划."""

    _correct_fields: ClassVar[set[str]] = {
        "synopsis",
        "volume_mode",
        "volumes",
        "narrative_phases",
        "key_turning_points",
        "character_arcs",
        "subplot_plan",
        "suspense_schedule",
        "ending_strategy",
        "emotional_arcs",
        "causal_chains",
        "subplot_collisions",
        "subversion_points",
        "chapter_rhythm_curve",
    }

    model_config = ConfigDict(extra="ignore")

    synopsis: str = Field(default="", description="全书概述")
    volume_mode: bool = Field(default=False)
    volumes: list[VolumeOutline] = Field(default_factory=list)
    narrative_phases: list[NarrativePhase] = Field(default_factory=list)
    key_turning_points: list[TurningPoint] = Field(default_factory=list)
    character_arcs: list[CharacterArcPlan] = Field(default_factory=list)
    subplot_plan: list[SubplotPlan] = Field(default_factory=list)
    suspense_schedule: list[SuspenseScheduleItem] = Field(
        default_factory=list,
        description="悬念时间表规划（用于追读力窗口系统初始化）",
    )
    ending_strategy: str = Field(default="", description="最终收束策略")
    emotional_arcs: list[EmotionalArc] = Field(
        default_factory=list,
        description="情感弧线规划",
    )
    causal_chains: list[CausalChain] = Field(
        default_factory=list,
        description="因果链规划",
    )
    subplot_collisions: list[SubplotCollision] = Field(
        default_factory=list,
        description="支线碰撞点规划",
    )
    subversion_points: list[SubversionPoint] = Field(
        default_factory=list,
        description="反套路点规划",
    )
    chapter_rhythm_curve: list[ChapterRhythmPoint] = Field(
        default_factory=list,
        description="每章节奏曲线规划（用于 quality_checks 节奏对比）",
    )
    element_selection: BlueprintElementSelection | None = Field(
        default=None,
        description="叙事要素选择器输出（用于提示词注入与 UI 展示）。",
    )


# ---------------------------------------------------------------------------
# Resolve forward references for cross-module type annotations.
# ---------------------------------------------------------------------------
NarrativeBlueprint.model_rebuild()
