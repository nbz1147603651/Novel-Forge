"""Central policy for motif category handling and prompt projection.

The motif tracker records themes, symbols, imagery, and repeated expression
channels. Recording is not the same as asking the model to emphasize them:
conceptual categories such as themes and symbols are tracked for continuity,
but should stay soft-only when projected into writing prompts.
"""

from __future__ import annotations

from typing import Any, Literal, TypeAlias, cast

MotifCategoryName: TypeAlias = Literal[
    "意象", "动作", "感官", "颜色", "声音", "主题", "符号", "技法"
]
MotifRoleName: TypeAlias = Literal[
    "theme_anchor",
    "core_symbol",
    "character_motif",
    "recurring_image",
    "atmospheric_detail",
    "one_off_rhetoric",
    "unknown",
]

VALID_MOTIF_CATEGORIES: frozenset[str] = frozenset(
    {"意象", "动作", "感官", "颜色", "声音", "主题", "符号", "技法"}
)
CONCEPTUAL_MOTIF_CATEGORIES: frozenset[str] = frozenset({"主题", "符号"})
PROMPT_SOFT_ONLY_CATEGORIES: frozenset[str] = CONCEPTUAL_MOTIF_CATEGORIES
VALID_MOTIF_ROLES: frozenset[str] = frozenset(
    {
        "theme_anchor",
        "core_symbol",
        "character_motif",
        "recurring_image",
        "atmospheric_detail",
        "one_off_rhetoric",
        "unknown",
    }
)
IMPORTANT_MOTIF_ROLES: frozenset[str] = frozenset(
    {"theme_anchor", "core_symbol", "character_motif", "recurring_image"}
)


def normalize_motif_category(value: Any, *, default: str = "意象") -> MotifCategoryName:
    """Return a valid motif category, falling back to ``default`` then ``意象``."""

    raw = str(value or "").strip()
    if raw in VALID_MOTIF_CATEGORIES:
        return cast(MotifCategoryName, raw)
    if default in VALID_MOTIF_CATEGORIES:
        return cast(MotifCategoryName, default)
    return "意象"


def default_role_for_category(category: Any) -> MotifRoleName:
    """Return the default role used when an extracted motif lacks role metadata."""

    normalized = normalize_motif_category(category)
    if normalized == "主题":
        return "theme_anchor"
    if normalized == "符号":
        return "core_symbol"
    if normalized in {"意象", "颜色", "声音", "感官", "动作"}:
        return "recurring_image"
    if normalized == "技法":
        return "one_off_rhetoric"
    return "unknown"


def is_conceptual_category(category: Any) -> bool:
    """Return True for categories tracked as continuity concepts, not prompt goals."""

    return normalize_motif_category(category) in CONCEPTUAL_MOTIF_CATEGORIES


def is_prompt_soft_only_category(category: Any) -> bool:
    """Return True when a category may be mentioned only as soft context."""

    return normalize_motif_category(category) in PROMPT_SOFT_ONLY_CATEGORIES


def allows_hard_repetition_forbid(category: Any) -> bool:
    """Return whether recent repetition may become a hard avoid item."""

    return not is_prompt_soft_only_category(category)


def allows_prompt_callback(category: Any) -> bool:
    """Return whether a motif category may become a callback suggestion in prompts."""

    return not is_prompt_soft_only_category(category)


def allows_forward_prompt_guidance(category: Any) -> bool:
    """Return whether a motif category may be actively surfaced as forward guidance."""

    return not is_prompt_soft_only_category(category)


def allows_unified_strengthen(category: Any) -> bool:
    """Return whether active motif context may be rendered as strengthen guidance."""

    return not is_prompt_soft_only_category(category)


def should_auto_retire_category(category: Any) -> bool:
    """Return whether low-importance stale motifs in this category may auto-retire."""

    return not is_conceptual_category(category)
