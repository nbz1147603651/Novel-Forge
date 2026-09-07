"""Stable compatibility facade for the long-form quality stage.

Production code imports the focused runner and helper modules directly. This
module preserves historical imports without monkeypatch broadcasting.
"""

from __future__ import annotations

from novel_forge.pipeline.long.services.anchor_terms import _extract_anchor_terms_from_bible
from novel_forge.pipeline.long.stages.quality_checks_common import (
    AlignmentStep,
    ContinuityEvalStep,
    _source_text_hash,
)
from novel_forge.pipeline.long.stages.quality_checks_lib import (
    _ALIGNMENT_CACHE_MAX_SIMILARITY,
    _ALIGNMENT_CACHE_MIN_SCORE,
    _artifact_report_summary,
    _compute_text_similarity,
    _push_audit_result_to_ui,
    _remember_opening_guard_pending_issues,
    _should_skip_alignment_recheck,
    attach_guard_repair_metadata,
    build_guard_compliance_findings,
    guard_report_has_actionable_low_compliance,
    guard_report_incomplete_warning,
    merge_opening_guard_pending_issues,
)
from novel_forge.pipeline.long.stages.quality_checks_runner import (
    _evaluate_single_constraint_compliance,
    check_guard_constraint_compliance,
    run_opening_guard_patch,
    run_quality_checks,
)
from novel_forge.pipeline.long.stages.reading_power_repair import (
    _build_reading_power_excerpt,
    _build_reading_power_input,
    _text_change_ratio,
    evaluate_and_record_reading_power,
)

__all__ = [
    "AlignmentStep",
    "ContinuityEvalStep",
    "_ALIGNMENT_CACHE_MAX_SIMILARITY",
    "_ALIGNMENT_CACHE_MIN_SCORE",
    "_artifact_report_summary",
    "_build_reading_power_excerpt",
    "_build_reading_power_input",
    "_compute_text_similarity",
    "_evaluate_single_constraint_compliance",
    "_extract_anchor_terms_from_bible",
    "_push_audit_result_to_ui",
    "_remember_opening_guard_pending_issues",
    "_should_skip_alignment_recheck",
    "_source_text_hash",
    "_text_change_ratio",
    "attach_guard_repair_metadata",
    "build_guard_compliance_findings",
    "check_guard_constraint_compliance",
    "evaluate_and_record_reading_power",
    "guard_report_has_actionable_low_compliance",
    "guard_report_incomplete_warning",
    "merge_opening_guard_pending_issues",
    "run_opening_guard_patch",
    "run_quality_checks",
]
