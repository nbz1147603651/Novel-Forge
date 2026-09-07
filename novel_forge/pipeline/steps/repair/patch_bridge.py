"""Patch engine adapter — converts heterogeneous issue types to PatchInput-compatible objects.

This module provides a shared adapter layer so any issue type (ContinuityIssue,
CausalIssue, etc.) can be passed to ChapterPatchStep without the step needing
to know about each issue's native schema.

Example
-------
```python
from novel_forge.pipeline.steps.repair.patch_bridge import (
    PatchCompatIssue,
    adapt_issue_for_patch,
    build_patch_boundary_context,
)

compat_issue = adapt_issue_for_patch(
    continuity_issue,
    infer_location_func=ContinuityRepairStep._infer_patch_location,
)
patch_input = PatchInput(
    chapter_number=3,
    chapter_text=chapter_text,
    issues=[compat_issue],
    boundary_context=build_patch_boundary_context(...),
)
```
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from novel_forge.core.utils.boundary_windows import take_tail_paragraphs

# --------------------------------------------------------------------------- #
# PatchCompatIssue — canonical adapter class
# --------------------------------------------------------------------------- #


@dataclass
class PatchCompatIssue:
    """Canonical issue format accepted by PatchInput / ChapterPatchStep.

    Attributes
    ----------
    severity : str
        Issue severity level (e.g. "critical", "high", "medium", "low").
    summary : str
        One-line description of the problem.
    evidence : str
        Supporting text or quote that demonstrates the issue.
    issue_type : str
        Short category label (e.g. "character_state_conflict", "temporal_inconsistency").
    location : str
        Human-readable location hint (paragraph index range, scene, etc.).
    paragraph_start : int
        Zero-based start paragraph index in the chapter.
    paragraph_end : int
        Zero-based end paragraph index (inclusive).
    location_confidence : float
        Confidence score for the location inference (0.0–1.0).
    anchor_type : str
        Anchor strategy to use when locating the evidence
        (e.g. "exact", "trimmed", "normalized", "block_anchor").
    evidence_quote : str
        Exact text excerpt from the chapter used as the patch anchor.
    fix_mode : str
        Repair strategy hint (e.g. "replace", "insert", "delete", "rewrite").
    fix_suggestion : str
        Natural-language description of the desired fix.
    """

    issue_id: str = ""
    severity: str = "medium"
    summary: str = ""
    evidence: str = ""
    issue_type: str = ""
    repair_surface: str = ""
    location: str = ""
    paragraph_start: int = 0
    paragraph_end: int = 0
    location_confidence: float = 0.0
    anchor_type: str = ""
    evidence_quote: str = ""
    fix_mode: str = ""
    fix_suggestion: str = ""
    missing_anchors: list[Any] = field(default_factory=list)
    postconditions: list[Any] = field(default_factory=list)
    repair_directive: dict[str, Any] = field(default_factory=dict)

    # Optional raw issue reference for debugging / rollback
    _raw_issue: Any = field(default=None, repr=False)


# --------------------------------------------------------------------------- #
# Default fix suggestions keyed by issue_type
# --------------------------------------------------------------------------- #

_GENERIC_FIX_SUGGESTIONS: dict[str, str] = {
    "character_state_conflict": "定向修复该问题，保持叙事风格。",
    "temporal_inconsistency": "修正时间线矛盾，确保叙述顺序合理。",
    "poi_drift": "恢复POV角色视角，确保叙事焦点一致。",
    "prop_ inconsistency": "修正道具使用矛盾，保持设定一致。",
    "causal_chain_break": "修复因果链断裂，确保事件逻辑连贯。",
    "setting_conflict": "修正场景/环境矛盾，保持世界观一致。",
    "dialogue_inconsistency": "修正对话与叙述矛盾，保持人物语言风格。",
    "narrative_continuity": "修补叙事断裂，确保段落衔接流畅。",
    "world_building_conflict": "修正与世界设定的矛盾，保持设定一致。",
    "default": "定向修复该问题，保持叙事风格。",
}


# --------------------------------------------------------------------------- #
# adapt_issue_for_patch
# --------------------------------------------------------------------------- #


def adapt_issue_for_patch(
    issue: Any,
    infer_location_func: Callable[[Any], str] | None = None,
    fix_suggestions: dict[str, str] | None = None,
) -> PatchCompatIssue:
    """Convert any issue object to a PatchCompatIssue.

    Parameters
    ----------
    issue
        An issue object (ContinuityIssue, CausalIssue, etc.) with varying
        attribute sets.  Missing attributes fall back to defaults.
    infer_location_func
        Optional callable that takes ``issue`` and returns a ``str`` location hint.
        If omitted and the issue lacks a ``.location`` attribute, an empty string
        is used.
    fix_suggestions
        Optional ``{issue_type_lower: suggestion}`` dict to use instead of the
        built-in ``_GENERIC_FIX_SUGGESTIONS`` map.

    Returns
    -------
    PatchCompatIssue
        Fully populated adapter object suitable for PatchInput.issues.
    """
    suggestions = fix_suggestions or _GENERIC_FIX_SUGGESTIONS

    # Resolve fix_suggestion — prefer fix_actions join, then explicit field,
    # then lookup by issue_type, then fallback
    raw_suggestion = (
        getattr(issue, "fix_suggestion", None)
        or "；".join(getattr(issue, "fix_actions", []) or [])
        or suggestions.get(
            (getattr(issue, "issue_type", "") or "").lower(), suggestions.get("default", "")
        )
    )
    repair_directive = getattr(issue, "repair_directive", None)
    if hasattr(repair_directive, "model_dump"):
        repair_directive_data = repair_directive.model_dump(
            mode="json",
            exclude={"schema_version", "created_at"},
        )
    elif isinstance(repair_directive, dict):
        repair_directive_data = dict(repair_directive)
    else:
        repair_directive_data = {}
    if repair_directive_data:
        strategy = str(repair_directive_data.get("repair_strategy") or "").strip()
        rewrite = str(repair_directive_data.get("recommended_rewrite") or "").strip()
        if strategy:
            raw_suggestion = f"{raw_suggestion}；【审核修复策略】{strategy}"
        if rewrite:
            raw_suggestion = f"{raw_suggestion}；【建议改写草稿】{rewrite}"

    # Resolve location — caller-supplied function, or existing .location, or ""
    location: str
    if infer_location_func is not None:
        location = infer_location_func(issue)
    elif hasattr(issue, "location") and issue.location:
        location = issue.location
    else:
        location = ""

    return PatchCompatIssue(
        issue_id=getattr(issue, "issue_id", None) or "",
        severity=getattr(issue, "severity", None) or "medium",
        summary=getattr(issue, "summary", None) or "",
        evidence=getattr(issue, "evidence", None) or "",
        issue_type=getattr(issue, "issue_type", None) or "",
        repair_surface=getattr(issue, "repair_surface", None) or "",
        location=location,
        paragraph_start=getattr(issue, "paragraph_start", None) or 0,
        paragraph_end=getattr(issue, "paragraph_end", None) or 0,
        location_confidence=getattr(issue, "location_confidence", None) or 0.0,
        anchor_type=getattr(issue, "anchor_type", None) or "",
        evidence_quote=getattr(issue, "evidence_quote", None)
        or getattr(issue, "evidence", None)
        or "",
        fix_mode=getattr(issue, "fix_mode", None) or "",
        fix_suggestion=raw_suggestion,
        missing_anchors=list(getattr(issue, "missing_anchors", []) or []),
        postconditions=list(getattr(issue, "postconditions", []) or []),
        repair_directive=repair_directive_data,
        _raw_issue=issue,
    )


# --------------------------------------------------------------------------- #
# build_patch_boundary_context
# --------------------------------------------------------------------------- #


def _tail_paragraph_context(text: str, *, max_paragraphs: int, max_chars: int) -> str:
    return take_tail_paragraphs(text, max_paragraphs, max_chars=max_chars)


def build_patch_boundary_context(
    issue_types: set[str],
    opening_patch_types: set[str],
    closing_patch_types: set[str],
    previous_chapter_ending: str | None = None,
    opening_contract: str | None = None,
    closing_contract: str | None = None,
    *,
    max_prev_ending_chars: int = 2400,
    max_prev_ending_paragraphs: int = 5,
) -> dict[str, Any]:
    """Build the ``boundary_context`` dict for PatchInput.

    Parameters
    ----------
    issue_types
        Set of issue type strings detected in the current chapter.
    opening_patch_types
        Set of issue type strings that should trigger opening-contract focus.
    closing_patch_types
        Set of issue types that should trigger closing-contract focus.
    previous_chapter_ending
        Raw text of the previous chapter's final paragraph(s).
    opening_contract
        The current chapter's opening contract string (from chapter_plan).
    closing_contract
        The current chapter's closing contract string (from chapter_plan).
    max_prev_ending_chars
        Truncate ``previous_chapter_ending`` to this many characters from the
        tail.  Defaults to 500.

    Returns
    -------
    dict[str, Any]
        ``boundary_context`` dict with keys:
        ``focus``, ``previous_chapter_ending``, ``opening_contract``, ``closing_contract``.
    """
    has_opening = bool(issue_types & opening_patch_types)
    has_closing = bool(issue_types & closing_patch_types)

    if has_opening and has_closing:
        focus = "开头+结尾"
    elif has_closing:
        focus = "结尾"
    else:
        focus = "开头"

    prev_ending = (previous_chapter_ending or "").strip()
    prev_ending = _tail_paragraph_context(
        prev_ending,
        max_paragraphs=max_prev_ending_paragraphs,
        max_chars=max_prev_ending_chars,
    )

    return {
        "focus": focus,
        "previous_chapter_ending": prev_ending,
        "previous_chapter_ending_policy": (
            f"取上一章结尾最多 {max_prev_ending_paragraphs} 段，供边界判断，只读不可改。"
        ),
        "opening_contract": (opening_contract or "").strip(),
        "closing_contract": (closing_contract or "").strip(),
    }
