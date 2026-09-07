"""Shared audit issue routing taxonomy.

The chapter pipeline has multiple reviewers that may describe the same symptom
with different issue_type strings.  This module centralizes the routing rules so
continuity, causal, reading-power, and guard repairs do not each keep their own
partial string lists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DIMENSION_CONTINUITY = "continuity"
DIMENSION_CAUSAL = "causal"
DIMENSION_READING_POWER = "reading_power"
DIMENSION_GUARD = "guard"
DIMENSION_ALIGNMENT = "alignment"
DIMENSION_CHAPTER_QUALITY = "chapter_quality"
DIMENSION_KNOWLEDGE_BOUNDARY = "knowledge_boundary"

QUALITY_DIMENSIONS = frozenset({DIMENSION_READING_POWER, DIMENSION_CHAPTER_QUALITY})

_GUARD_PREFIXES = ("guard_", "guardrail_", "plot_guard_")

_CONTINUITY_TYPES = frozenset(
    {
        "bridge_contract_not_followed",
        "carry_forward_missing",
        "closing_contract_mismatch",
        "closing_gap",
        "continuity_error",
        "continuity_gap",
        "custody_break",
        "handoff_missing",
        "information_consistency",
        "location_jump",
        "opening_gap",
        "relationship_change_support",
        "relationship_development",
        "state_carryover_gap",
        "state_continuity_error",
    }
)

_CONTEXT_SENSITIVE_CONTINUITY_TYPES = frozenset(
    {
        "character_inconsistency",
        "factual_error",
        "knowledge_contradiction",
        "pov_jump",
        "world_building_error",
    }
)

_CAUSAL_TYPES = frozenset(
    {
        "action_consequence_gap",
        "causal_break",
        "causal_gap",
        "causal_link_missing",
        "causal_loop",
        "causal_motivation_gap",
        "motivation_gap",
        "timeline_causality_error",
        "unresolved_causal_chain",
    }
)

_READING_POWER_TYPES = frozenset(
    {
        "hook_missing",
        "hook_too_weak",
        "hook_weak",
        "prev_hook_unfulfilled",
        "payoff_missing",
        "outline_mismatch",
        "overall_score_low",
        "information_pacing_issue",
        "information_pacing_slow",
        "information_pacing_stagnant",
        "main_plot_depth_issue",
        "main_plot_surface",
        "main_plot_stalled",
        "pacing_issue",
        "revelation_over_budget",
        "character_drive_weak",
        "tension_depressed",
        "tension_mismatch",
    }
)

_CHAPTER_QUALITY_TYPES = frozenset(
    {
        "address_form_mismatch",
        "character_not_in_plan",
        "expression_clarity",
        "forbidden_element_reuse",
        "forbidden_element_usage",
        "forbidden_element_violation",
        "forbidden_usage",
        "prompt_leak",
        "pov_intrusion",
        "pronoun_mismatch",
        "sensory_anchor_repetition",
        "sensory_repetition",
        "style_repetition",
        "text_repeat",
        "text_repetition",
        "time_marker_invalid",
    }
)

_KNOWLEDGE_BOUNDARY_TYPES = frozenset(
    {
        "knowledge_boundary_ambiguous",
        "knowledge_boundary_leak",
        "knowledge_leak",
        "premature_reveal",
        "unsupported_knowledge_gain",
    }
)

_CROSS_BOUNDARY_MARKERS = frozenset(
    {
        "action_handoff",
        "bridge",
        "carry_forward",
        "closing_contract",
        "must_carry_forward",
        "opening_pov",
        "previous_exit",
        "transition_mode",
        "上一章",
        "上章",
        "前一章",
        "前章",
        "前文已建立",
        "开场",
        "开头承接",
        "承接",
        "桥接",
        "结尾交接",
        "跨章",
        "上一章结尾",
        "章节边界",
        "状态承接",
    }
)

_QUALITY_MARKERS = frozenset(
    {
        "称谓",
        "表达",
        "重复",
        "禁用",
        "代词",
        "视角侵入",
        "提示词",
        "章内",
    }
)

_READING_POWER_MARKERS = frozenset(
    {
        "读感",
        "追读",
        "节奏",
        "钩子",
        "微兑现",
        "悬念",
    }
)


@dataclass(frozen=True)
class IssueClassification:
    """Routing decision for one audit issue."""

    primary_dimension: str
    repair_dimensions: tuple[str, ...]
    reason: str

    @property
    def is_continuity(self) -> bool:
        return self.primary_dimension == DIMENSION_CONTINUITY

    @property
    def is_guard(self) -> bool:
        return self.primary_dimension == DIMENSION_GUARD

    @property
    def is_quality(self) -> bool:
        return self.primary_dimension in QUALITY_DIMENSIONS


def normalize_issue_type(value: Any) -> str:
    """Normalize raw issue_type strings used by mixed reviewers."""

    return str(value or "").strip().lower()


def _blob(*values: Any) -> str:
    return " ".join(str(value or "") for value in values if value is not None).lower()


def _contains_any(text: str, markers: frozenset[str]) -> bool:
    return any(marker.lower() in text for marker in markers)


def classify_review_issue(
    *,
    issue_type: Any = "",
    source_module: Any = "",
    dimension: Any = "",
    summary: Any = "",
    evidence: Any = "",
    metadata: dict[str, Any] | None = None,
) -> IssueClassification:
    """Classify one audit issue into the module that should own it.

    The rule order intentionally prefers explicit source-module ownership and
    exact issue families, then uses cross-boundary markers only for ambiguous
    critic categories such as character_inconsistency.  This keeps chapter-local
    wording/name/POV findings out of continuity repair unless they are clearly
    about chapter boundary or state carry-forward.
    """

    issue = normalize_issue_type(issue_type)
    source = normalize_issue_type(source_module)
    declared_dimension = normalize_issue_type(dimension)
    text = _blob(issue, summary, evidence, metadata or {})

    if source.startswith(_GUARD_PREFIXES) or issue.startswith(_GUARD_PREFIXES):
        return IssueClassification(DIMENSION_GUARD, (DIMENSION_GUARD,), "guard source/type")

    if (
        "knowledge_boundary" in source
        or issue in _KNOWLEDGE_BOUNDARY_TYPES
        or issue.startswith("knowledge_boundary_")
    ):
        return IssueClassification(
            DIMENSION_KNOWLEDGE_BOUNDARY,
            (DIMENSION_KNOWLEDGE_BOUNDARY,),
            "knowledge boundary source/type",
        )

    if "causal" in source or issue in _CAUSAL_TYPES or issue.startswith("causal_"):
        return IssueClassification(DIMENSION_CAUSAL, (DIMENSION_CAUSAL,), "causal source/type")

    if "reading_power" in source or issue in _READING_POWER_TYPES:
        return IssueClassification(
            DIMENSION_READING_POWER,
            (DIMENSION_READING_POWER,),
            "reading power source/type",
        )

    if (
        "chapter_repair" in source
        or "check_chapter" in source
        or issue in _CHAPTER_QUALITY_TYPES
        or issue.startswith("forbidden_")
    ):
        return IssueClassification(
            DIMENSION_CHAPTER_QUALITY,
            (DIMENSION_CHAPTER_QUALITY,),
            "chapter quality source/type",
        )

    if issue in _CONTEXT_SENSITIVE_CONTINUITY_TYPES:
        if _contains_any(text, _CROSS_BOUNDARY_MARKERS):
            return IssueClassification(
                DIMENSION_CONTINUITY,
                (DIMENSION_CONTINUITY,),
                "ambiguous issue with cross-boundary markers",
            )
        return IssueClassification(
            DIMENSION_CHAPTER_QUALITY,
            (DIMENSION_CHAPTER_QUALITY,),
            "ambiguous issue without cross-boundary markers",
        )

    if issue in _CONTINUITY_TYPES or "continuity" in source:
        return IssueClassification(
            DIMENSION_CONTINUITY,
            (DIMENSION_CONTINUITY,),
            "continuity source/type",
        )

    if declared_dimension in {
        DIMENSION_CONTINUITY,
        DIMENSION_CAUSAL,
        DIMENSION_READING_POWER,
        DIMENSION_GUARD,
        DIMENSION_ALIGNMENT,
        DIMENSION_CHAPTER_QUALITY,
        DIMENSION_KNOWLEDGE_BOUNDARY,
    }:
        if declared_dimension == DIMENSION_CONTINUITY and _contains_any(text, _QUALITY_MARKERS):
            return IssueClassification(
                DIMENSION_CHAPTER_QUALITY,
                (DIMENSION_CHAPTER_QUALITY,),
                "declared continuity but quality markers dominate",
            )
        if declared_dimension == DIMENSION_CONTINUITY and _contains_any(
            text,
            _READING_POWER_MARKERS,
        ):
            return IssueClassification(
                DIMENSION_READING_POWER,
                (DIMENSION_READING_POWER,),
                "declared continuity but reading-power markers dominate",
            )
        return IssueClassification(
            declared_dimension,
            (declared_dimension,),
            "declared dimension",
        )

    if _contains_any(text, _CROSS_BOUNDARY_MARKERS):
        return IssueClassification(
            DIMENSION_CONTINUITY,
            (DIMENSION_CONTINUITY,),
            "cross-boundary markers",
        )

    return IssueClassification(
        DIMENSION_CHAPTER_QUALITY,
        (DIMENSION_CHAPTER_QUALITY,),
        "default chapter quality",
    )


def is_continuity_issue(
    *,
    issue_type: Any = "",
    source_module: Any = "",
    dimension: Any = "",
    summary: Any = "",
    evidence: Any = "",
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Return True when the issue belongs in continuity scoring/repair."""

    return classify_review_issue(
        issue_type=issue_type,
        source_module=source_module,
        dimension=dimension,
        summary=summary,
        evidence=evidence,
        metadata=metadata,
    ).is_continuity
