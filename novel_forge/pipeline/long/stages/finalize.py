"""Stable compatibility facade for the long-form finalize stage.

Production code imports the focused implementation modules directly. This
module preserves historical imports without mutating sibling module globals.
"""

from __future__ import annotations

from novel_forge.pipeline.long.stages.finalize_checks import (
    _enforce_archive_hard_quality_blocks,
    _finalize_report_hash_transaction,
    _format_chapter_quality_evidence,
    _is_reading_power_stale,
    _normalize_outcome_chapter_number,
    _sync_or_mark_report_hashes,
    _validate_report_hashes,
)
from novel_forge.pipeline.long.stages.finalize_common import (
    ReportRefreshFailurePolicy,
    _source_text_hash,
    source_text_hash,
)
from novel_forge.pipeline.long.stages.finalize_persist import (
    ReportRefreshDecision,
    _write_chapter_outcome_to_story_kernel,
    persist_results,
    run_archive_preflight_repairs,
)
from novel_forge.pipeline.long.stages.finalize_report import (
    _adjudicate_state_before_archive,
    _clean_and_validate_chapter_text,
    _progression_texts_for_ledger,
    _run_contract_execution_audit_without_state_adjudication,
    evaluate_chapter_text,
    extract_and_validate,
)

__all__ = [
    "ReportRefreshDecision",
    "ReportRefreshFailurePolicy",
    "_adjudicate_state_before_archive",
    "_clean_and_validate_chapter_text",
    "_enforce_archive_hard_quality_blocks",
    "_finalize_report_hash_transaction",
    "_format_chapter_quality_evidence",
    "_is_reading_power_stale",
    "_normalize_outcome_chapter_number",
    "_progression_texts_for_ledger",
    "_run_contract_execution_audit_without_state_adjudication",
    "_source_text_hash",
    "_sync_or_mark_report_hashes",
    "_validate_report_hashes",
    "_write_chapter_outcome_to_story_kernel",
    "evaluate_chapter_text",
    "extract_and_validate",
    "persist_results",
    "run_archive_preflight_repairs",
    "source_text_hash",
]
