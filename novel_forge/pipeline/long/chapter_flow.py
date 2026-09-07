"""Chapter flow orchestration — re-export facade.

This module is a thin re-export facade that imports public symbols from the
decomposed sub-modules and re-exports them for backward compatibility.

Implementation modules:
- ``chapter_flow_orchestrate`` — shared utilities, planning, ``execute_chapter_pipeline``
- ``chapter_flow_generate`` — generation stage (``GenerateArtifacts``, ``generate_chapter_prose``)
- ``chapter_flow_review`` — review stage (``review_chapter_draft``)
- ``chapter_flow_finalize`` — finalize stage (``finalize_chapter_result``)
"""

from __future__ import annotations

from novel_forge.core.review.review_contracts import source_text_hash

# ── Finalize ─────────────────────────────────────────────────────────────────
from novel_forge.pipeline.long.chapter_flow_finalize import (
    AlignmentRepairStageResult,
    _change_ratio,
    _dedupe_repair_tickets,
    _enforce_alignment_threshold,
    _finalize_review_artifacts,
    _guidance_report_mismatch_sources,
    _humanize_result_changed,
    _persist_macro_guard_result,
    _persist_quality_gate_report,
    _refresh_mismatched_guidance_reports,
    _repair_contract_execution_audit_block,
    _run_alignment_repair_stage,
    _run_humanize_pass,
    _should_include_pre_final_evaluation,
    _should_skip_post_repair_checks,
    finalize_chapter_result,
    is_carry_forward_block_exception,
)

# ── Generate ─────────────────────────────────────────────────────────────────
from novel_forge.pipeline.long.chapter_flow_generate import (
    GenerateArtifacts,
    _build_generate_metrics_payload,
    _build_repair_metrics_payload,
    _chapter_repair_check_metrics,
    _metric_bool,
    _metric_field,
    _metric_float,
    _metric_issue_count,
    _metric_list_count,
    _persist_repair_metrics_report,
    generate_chapter_prose,
)

# ── Orchestrate ──────────────────────────────────────────────────────────────
from novel_forge.pipeline.long.chapter_flow_orchestrate import (
    _CHANGE_BUDGET_THRESHOLD_DEFAULT,
    _alignment_meets_threshold,
    _apply_terminal_humanize,
    _apply_terminal_word_count_polish,
    _archive_policy_block_messages,
    _coerce_reading_power_report,
    _emit_input_integrity_check,
    _generate_and_wave,
    _guard_archive_block_messages,
    _load_reading_power_report_for_text,
    _make_on_step_with_tokens,
    _persist_stage_artifact_safely,
    _reading_power_archive_block_messages,
    _reading_power_is_fallback,
    _recover_latest_draft,
    _review_resume_plan,
    _ReviewPhaseState,
    _ReviewResumePlan,
    _save_reading_power_next_chapter_constraints,
    execute_chapter_pipeline,
    load_prepared_chapter_artifacts,
    prepare_chapter_plan,
    rehydrate_prepared_runtime_context,
)

# ── Review ───────────────────────────────────────────────────────────────────
from novel_forge.pipeline.long.chapter_flow_review import (
    _build_normalized_review_contracts,
    _build_quality_gate,
    _run_polish,
    _run_quality_and_repairs,
    review_chapter_draft,
)

# ── Re-exports for test monkeypatching ───────────────────────────────────────
# Tests patch these symbols on the chapter_flow module. The sub-modules import
# them from their source modules, so we re-import here to make them patchable.
from novel_forge.pipeline.long.stages.causal_repair import (
    alignment_repair_edit,
    recheck_alignment,
)
from novel_forge.pipeline.long.stages.dedup_pronoun import (
    run_final_dedup,
    run_pronoun_check,
    run_self_repetition_check,
)
from novel_forge.pipeline.long.stages.draft import (
    build_revelation_density_warning,
    build_word_count_warning,
    run_post_repair_polish_layer,
)
from novel_forge.pipeline.long.stages.finalize_report import (
    evaluate_chapter_text,
    extract_and_validate,
)
from novel_forge.pipeline.long.stages.humanize_layer import (
    run_humanize_layer,
)
from novel_forge.pipeline.long.stages.quality_checks_lib import _should_skip_alignment_recheck
from novel_forge.pipeline.long.stages.quality_checks_runner import (
    run_opening_guard_patch,
    run_quality_checks,
)
from novel_forge.pipeline.long.stages.reading_power_repair import (
    evaluate_and_record_reading_power,
)
from novel_forge.pipeline.long.stages.report_refresh import (
    refresh_quality_reports_after_semantic_text_change,
    run_guard_compliance_for_final_text,
)

__all__ = [
    "_CHANGE_BUDGET_THRESHOLD_DEFAULT",
    "_ReviewPhaseState",
    "_ReviewResumePlan",
    "_alignment_meets_threshold",
    "_apply_terminal_humanize",
    "_apply_terminal_word_count_polish",
    "_archive_policy_block_messages",
    "_build_generate_metrics_payload",
    "_build_normalized_review_contracts",
    "_build_quality_gate",
    "_build_repair_metrics_payload",
    "_change_ratio",
    "_chapter_repair_check_metrics",
    "_coerce_reading_power_report",
    "_dedupe_repair_tickets",
    "_emit_input_integrity_check",
    "_enforce_alignment_threshold",
    "_finalize_review_artifacts",
    "_generate_and_wave",
    "_guard_archive_block_messages",
    "_guidance_report_mismatch_sources",
    "_humanize_result_changed",
    "_load_reading_power_report_for_text",
    "_make_on_step_with_tokens",
    "_metric_bool",
    "_metric_field",
    "_metric_float",
    "_metric_issue_count",
    "_metric_list_count",
    "_persist_macro_guard_result",
    "_persist_quality_gate_report",
    "_persist_repair_metrics_report",
    "_persist_stage_artifact_safely",
    "_reading_power_archive_block_messages",
    "_reading_power_is_fallback",
    "_recover_latest_draft",
    "_refresh_mismatched_guidance_reports",
    "_repair_contract_execution_audit_block",
    "_review_resume_plan",
    "_run_alignment_repair_stage",
    "_run_humanize_pass",
    "_run_polish",
    "_run_quality_and_repairs",
    "_save_reading_power_next_chapter_constraints",
    "_should_include_pre_final_evaluation",
    "_should_skip_alignment_recheck",
    "_should_skip_post_repair_checks",
    "AlignmentRepairStageResult",
    "GenerateArtifacts",
    "alignment_repair_edit",
    "build_revelation_density_warning",
    "build_word_count_warning",
    "execute_chapter_pipeline",
    "evaluate_and_record_reading_power",
    "evaluate_chapter_text",
    "extract_and_validate",
    "finalize_chapter_result",
    "generate_chapter_prose",
    "is_carry_forward_block_exception",
    "load_prepared_chapter_artifacts",
    "prepare_chapter_plan",
    "rehydrate_prepared_runtime_context",
    "recheck_alignment",
    "refresh_quality_reports_after_semantic_text_change",
    "review_chapter_draft",
    "run_final_dedup",
    "run_guard_compliance_for_final_text",
    "run_humanize_layer",
    "run_opening_guard_patch",
    "run_post_repair_polish_layer",
    "run_pronoun_check",
    "run_quality_checks",
    "run_self_repetition_check",
    "source_text_hash",
]
