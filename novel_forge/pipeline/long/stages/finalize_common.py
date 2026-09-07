"""Shared imports and aliases for finalize-stage implementation modules."""
# ruff: noqa: E402,F401,I001

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from novel_forge.common.constants import severity_at_least
from novel_forge.common.utils import normalize_gender_value
from novel_forge.core.utils.coerce import coerce_float
from novel_forge.core.exceptions import (
    ConsistencyViolationError,
    FinalReportFreshnessError,
    ModelGatewayError,
    StateError,
)
from novel_forge.core.format_contracts import strip_leading_markdown_headings
from novel_forge.core.domain.guardrails import (
    IN_WORLD_TEXT_VERDICT,
    classify_prompt_leak_candidate,
    confirmed_reported_prompt_leaks,
    detect_prompt_leaks,
    is_system_artifact_name,
    normalize_invalid_time_markers,
    repair_confirmed_prompt_leaks,
    scrub_prompt_artifacts,
)
from novel_forge.core.schemas.chapter import CausalValidationReport, ChapterOutcome
from novel_forge.core.schemas.eval_schema import EvalReport
from novel_forge.core.utils.string import extract_text_content
from novel_forge.core.utils.text_hash import source_text_hash
from novel_forge.core.utils.text_validation import assess_word_count, count_chapter_words

# Backward-compatible alias for migrated private utility
_source_text_hash = source_text_hash
from novel_forge.narrative_state.knowledge_ops import (
    knowledge_op_target_text,
    normalize_knowledge_op,
)
from novel_forge.obs.logger import get_logger
from novel_forge.persistence.project_staleness import (
    assert_upstream_revision_fingerprint_current,
    invalidate_chapter_tts_artifacts,
    invalidate_downstream_generated_artifacts,
    save_orphaned_chapter_draft,
    update_canon_watermark,
)
from novel_forge.pipeline.long.execution_models import ChapterExecutionContext
from novel_forge.pipeline.long.services.element_progress import (
    finalize_chapter_progress_with_optional_arbiter,
)
from novel_forge.pipeline.long.services.knowledge_boundary_audit import (
    run_knowledge_boundary_audit,
)
from novel_forge.pipeline.long.services.plot_milestones import append_progression_entries
from novel_forge.pipeline.long.services.context.stage_memory_builder import (
    build_finalize_eval_memory_context,
    persist_stage_memory_diagnostics_report,
)
from novel_forge.pipeline.long.services.context.story_kernel_context import (
    invalidate_story_kernel_composer_cache,
    load_story_kernel_composer,
    story_kernel_db_path,
)
from novel_forge.pipeline.long.services.generation.strand_weave import (
    StrandTracker,
    infer_dominant_strand,
)
from novel_forge.pipeline.style_profile_helpers import (
    coerce_reading_power_window_config,
)
from novel_forge.pipeline.repair_orchestration.domains.knowledge_boundary import (
    run_knowledge_boundary_repair_v2 as run_knowledge_boundary_repair_loop,
)
from novel_forge.pipeline.repair_orchestration.domains.prompt_leak import (
    run_prompt_leak_repair_v2 as repair_confirmed_prompt_leaks_with_patch,
)
from novel_forge.pipeline.long.stages.report_refresh import (
    ReportRefreshFailurePolicy,
    refresh_quality_reports_after_semantic_text_change,
    run_guard_compliance_for_final_text,
)
from novel_forge.pipeline.long.stages.word_count import (
    clear_word_count_rejections,
    word_count_archive_gate_enabled,
)
from novel_forge.pipeline.quality_gate import QualityGate
from novel_forge.pipeline.steps.contract_execution_audit_step import (
    ContractExecutionAuditInput,
    ContractExecutionAuditStep,
    should_run_contract_execution_audit,
)
from novel_forge.pipeline.steps.evaluate_step import EvaluateStep
from novel_forge.pipeline.steps.extract_knowledge_deltas_step import (
    ExtractKnowledgeDeltasStep,
    KnowledgeDeltaInput,
)
from novel_forge.pipeline.steps.extract_step import ExtractCanonDeltaStep, ExtractInput
from novel_forge.pipeline.steps.state_adjudication_step import (
    CandidateStateDeltaExtractionInput,
    CandidateStateDeltaExtractionStep,
    RepairAdjudicatedIssueInput,
    RepairAdjudicatedIssueStep,
    compile_contract_targets,
    persist_adjudicated_state_ledger,
    run_narrative_state_adjudication,
)
from novel_forge.story_kernel.continuity_rules import ContinuityRules
from novel_forge.story_kernel.state_writer import StoryKernelStateWriter
from novel_forge.story_kernel.store import StoryKernelStore

if TYPE_CHECKING:
    from novel_forge.obs.tracer import PipelineTrace

_logger = get_logger("pipeline.finalize")


def extract_dimension_scores(
    *,
    eval_report: Any | None,
    continuity_report: Any | None,
    causal_report: Any | None,
) -> dict[str, float | None]:
    """Extract normalized scores from quality reports.

    Shared by ``_build_quality_gate`` (report/diagnostic path) and
    ``_enforce_archive_hard_quality_blocks`` (hard-gate path) so that
    both consumers see the same coercion logic and defaults.

    Returns a dict with keys ``eval``, ``continuity``, ``causal``.
    A value of ``None`` means the corresponding report was absent.
    """
    eval_unavailable = bool(
        eval_report is not None
        and (
            getattr(eval_report, "is_fallback", False)
            or str(getattr(eval_report, "evaluation_status", "") or "").strip().lower()
            in {"fallback", "timeout", "degraded"}
            or str(getattr(eval_report, "score_confidence", "") or "").strip().lower()
            == "fallback"
        )
    )
    return {
        "eval": (
            coerce_float(getattr(eval_report, "overall_score", 0.0), 0.0)
            if eval_report is not None and not eval_unavailable
            else None
        ),
        "continuity": (
            coerce_float(getattr(continuity_report, "continuity_score", 10.0), 10.0)
            if continuity_report is not None
            else None
        ),
        "causal": (
            coerce_float(getattr(causal_report, "causal_score", 10.0), 10.0)
            if causal_report is not None
            else None
        ),
    }


# Finalize modules use this facade via ``import *``; a literal export list
# preserves private helper visibility for static type checking.
__all__ = [
    "Any",
    "Awaitable",
    "Callable",
    "CandidateStateDeltaExtractionInput",
    "CandidateStateDeltaExtractionStep",
    "CausalValidationReport",
    "ChapterExecutionContext",
    "ChapterOutcome",
    "ConsistencyViolationError",
    "ContinuityRules",
    "ContractExecutionAuditInput",
    "ContractExecutionAuditStep",
    "EvalReport",
    "EvaluateStep",
    "ExtractCanonDeltaStep",
    "ExtractInput",
    "ExtractKnowledgeDeltasStep",
    "FinalReportFreshnessError",
    "IN_WORLD_TEXT_VERDICT",
    "KnowledgeDeltaInput",
    "ModelGatewayError",
    "QualityGate",
    "RepairAdjudicatedIssueInput",
    "RepairAdjudicatedIssueStep",
    "ReportRefreshFailurePolicy",
    "SimpleNamespace",
    "StateError",
    "StoryKernelStateWriter",
    "StoryKernelStore",
    "StrandTracker",
    "TYPE_CHECKING",
    "_logger",
    "_source_text_hash",
    "annotations",
    "append_progression_entries",
    "assert_upstream_revision_fingerprint_current",
    "assess_word_count",
    "build_finalize_eval_memory_context",
    "classify_prompt_leak_candidate",
    "clear_word_count_rejections",
    "coerce_float",
    "coerce_reading_power_window_config",
    "compile_contract_targets",
    "confirmed_reported_prompt_leaks",
    "count_chapter_words",
    "dataclass",
    "datetime",
    "detect_prompt_leaks",
    "extract_dimension_scores",
    "extract_text_content",
    "finalize_chapter_progress_with_optional_arbiter",
    "get_logger",
    "infer_dominant_strand",
    "invalidate_chapter_tts_artifacts",
    "invalidate_downstream_generated_artifacts",
    "invalidate_story_kernel_composer_cache",
    "is_system_artifact_name",
    "json",
    "knowledge_op_target_text",
    "load_story_kernel_composer",
    "normalize_gender_value",
    "normalize_invalid_time_markers",
    "normalize_knowledge_op",
    "persist_adjudicated_state_ledger",
    "persist_stage_memory_diagnostics_report",
    "refresh_quality_reports_after_semantic_text_change",
    "repair_confirmed_prompt_leaks",
    "repair_confirmed_prompt_leaks_with_patch",
    "run_guard_compliance_for_final_text",
    "run_knowledge_boundary_audit",
    "run_knowledge_boundary_repair_loop",
    "run_narrative_state_adjudication",
    "save_orphaned_chapter_draft",
    "scrub_prompt_artifacts",
    "severity_at_least",
    "should_run_contract_execution_audit",
    "source_text_hash",
    "story_kernel_db_path",
    "strip_leading_markdown_headings",
    "timezone",
    "update_canon_watermark",
    "word_count_archive_gate_enabled",
]
