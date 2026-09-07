"""ContinuityEvalStep — evaluates cross-chapter continuity for one chapter.

Thin re-export for backward compatibility. The implementation has been split
into focused modules under continuity_eval/.
"""

from __future__ import annotations

from novel_forge.pipeline.steps.continuity_eval.context import ContinuityEvalInput
from novel_forge.pipeline.steps.continuity_eval.core import ContinuityEvalStep
from novel_forge.pipeline.steps.continuity_eval.validators import (
    _check_closing_impact,
    _check_handoff_impact,
    _clean_forbidden_text,
    _collect_anchor_texts,
    _collect_known_anchor_terms,
    _compact_forbidden_text,
    _detect_forbidden_elements,
    _filter_forbidden_elements,
    _get_paragraph_index,
    _infer_narrative_function,
    _is_contextual_anchor_element,
    _looks_like_rhetorical_imagery,
)

__all__ = [
    "ContinuityEvalInput",
    "ContinuityEvalStep",
    "_check_closing_impact",
    "_check_handoff_impact",
    "_clean_forbidden_text",
    "_collect_anchor_texts",
    "_collect_known_anchor_terms",
    "_compact_forbidden_text",
    "_detect_forbidden_elements",
    "_filter_forbidden_elements",
    "_get_paragraph_index",
    "_infer_narrative_function",
    "_is_contextual_anchor_element",
    "_looks_like_rhetorical_imagery",
]
