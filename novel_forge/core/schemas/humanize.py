"""Humanize pattern detection and report schemas."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field, ValidationInfo, field_validator

from novel_forge.core.schemas.base import VersionedSchema

HUMANIZE_PATTERN_IDS: tuple[str, ...] = (
    "significance_inflation",
    "promotional_language",
    "ai_vocabulary",
    "negative_parallelism",
    "rule_of_three",
    "synonym_cycling",
    "false_ranges",
    "filler_phrases",
    "excessive_hedging",
    "generic_conclusions",
    "hollow_aspect_marker",
    "em_dash_overuse",
    "quotation_mark_misuse",
    "passive_subjectless",
    "persuasive_authority",
    "vague_attribution",
    "challenge_future_template",
    "collaborative_artifact",
    "knowledge_cutoff_disclaimer",
    "sycophantic_tone",
    "markdown_formatting_residue",
    "outline_heading_voice",
    "monotone_rhythm",
    "cross_chapter_template",
    "diff_anchored_writing",
    # ── Form-level AI-flavor detectors (added in response to 山风与归人2 audit) ──
    "weak_verb_stacking",
    "tautology_marker",
    "binary_judgment_closing",
    "pronoun_disappearance_run",
    "precise_timestamp_overuse",
)

LEGACY_HUMANIZE_PATTERN_ID_MAP: dict[str, str] = {
    str(index): pattern_id
    for index, pattern_id in enumerate(HUMANIZE_PATTERN_IDS[:15], start=1)
}

_LIBRARY_SOURCE_VALUES: frozenset[str] = frozenset({"library", "library_user"})


class HumanizePatternHit(VersionedSchema):
    """A single pattern hit detected in the text."""

    _legacy_pattern_id_map: ClassVar[dict[str, str]] = LEGACY_HUMANIZE_PATTERN_ID_MAP

    pattern_id: str = Field(description="Stable humanize pattern identifier.")
    pattern_name: str = Field(description="Human-readable pattern name.")
    category: str = Field(description="Pattern category, e.g. '重复用词', 'AI 句式'.")
    severity: Literal["critical", "high", "medium", "low"] = Field(
        description="Severity level of this pattern hit."
    )
    evidence_quote: str = Field(
        default="", description="Excerpt from the text demonstrating the pattern."
    )
    paragraph_index: int = Field(
        default=0, ge=0, description="0-based paragraph index where the hit was found."
    )
    suggestion: str = Field(
        default="", description="Suggested rewrite or fix for this pattern."
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence that this hit is a true AI-style pattern.",
    )
    actionable: bool = Field(
        default=False,
        description="Whether the hit is safe enough for automatic surgical repair.",
    )
    source: Literal["local", "llm", "merged", "library", "library_user"] = Field(
        default="llm",
        description="Where this hit came from: local prescreen, LLM, merged evidence, library, or library user-added.",
    )
    span_start: int | None = Field(
        default=None,
        ge=0,
        description="Optional 0-based character start offset in the source text.",
    )
    span_end: int | None = Field(
        default=None,
        ge=0,
        description="Optional 0-based character end offset in the source text.",
    )

    @field_validator("pattern_id", mode="before")
    @classmethod
    def normalize_pattern_id(cls, value: object) -> str:
        """Accept old numeric pattern ids while exposing stable string ids."""
        raw = str(value or "").strip()
        if raw in cls._legacy_pattern_id_map:
            return cls._legacy_pattern_id_map[raw]
        return raw

    @field_validator("paragraph_index", mode="before")
    @classmethod
    def normalize_paragraph_index(cls, value: object) -> int:
        """Recover provider drift while keeping stored reports integer-only."""
        if value is None or value == "":
            return 0
        if isinstance(value, bool):
            return 0
        if isinstance(value, int):
            return max(0, value)
        if isinstance(value, float) and value.is_integer():
            return max(0, int(value))
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.isdigit():
                return int(stripped)
        return 0

    @field_validator("span_end")
    @classmethod
    def validate_span_order(cls, value: int | None, info: ValidationInfo) -> int | None:
        start = info.data.get("span_start")
        if value is not None and isinstance(start, int) and value < start:
            return start
        return value


class HumanizeReport(VersionedSchema):
    """Aggregated humanize audit report for a chapter."""

    source_text_hash: str = Field(
        default="",
        description="SHA-256 hash of source chapter text used for this report.",
    )
    chapter_number: int = Field(ge=1, description="Chapter number this report applies to.")
    total_hits: int = Field(default=0, ge=0, description="Total number of pattern hits.")
    hits_by_category: dict[str, int] = Field(
        default_factory=dict,
        description="Hits count grouped by category, e.g. {'重复用词': 3, 'AI 句式': 2}.",
    )
    critical_hits: int = Field(
        default=0, ge=0, description="Number of critical-severity pattern hits."
    )
    patchable_hits: int = Field(
        default=0,
        ge=0,
        description="Number of hits that produced a surgical patch (actionable + usable suggestion).",
    )
    unpatchable_hits: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of hits that were detected but could not be safely patched "
            "(empty / placeholder / no-op suggestion, no unique anchor in text, etc.)."
        ),
    )
    pattern_hits: list[HumanizePatternHit] = Field(
        default_factory=list,
        description="All individual pattern hits found in the text.",
    )
    humanize_score: float = Field(
        default=10.0, ge=0.0, le=10.0,
        description="Overall humanization score (0–10). Deterministically computed from confirmed pattern hits using severity-weighted penalty formula (mirrors QualityGate.check_ai_flavor).",
    )
    summary: str = Field(default="", description="One-paragraph summary of the audit.")
