# ruff: noqa: F401,I001
"""Quality checks: alignment/continuity checks, opening guard, and shared helpers.

This module is the foundation of the quality stage. It provides:
- Shared utility functions used by continuity_repair, causal_repair, and dedup_pronoun
- run_quality_checks: main quality check entry point
- run_opening_guard_patch: pre-screen opening continuity
"""

from __future__ import annotations

import asyncio
import copy
import difflib
import re
from dataclasses import asdict, is_dataclass
from typing import Any, Callable

from novel_forge.common.constants import SEVERITY_RANK, TaskType, severity_at_least
from novel_forge.core.exceptions import ModelGatewayError
from novel_forge.core.domain.guardrails import KNOWN_PROMPT_MARKERS
from novel_forge.core.review.review_precision import (
    finding_auto_repair_eligible,
    prepare_findings_for_repair,
)
from novel_forge.core.schemas.review import RepairTicket, ReviewFinding
from novel_forge.core.utils.audit_issue import stable_issue_id
from novel_forge.core.utils.issue_ledger import diff_issues
from novel_forge.core.utils.semantic_drift import detect_drift
from novel_forge.core.utils.text_validation import count_chapter_words
from novel_forge.core.domain.world_context import (
    format_address_rules_for_prompt,
    render_world_context_rules,
)
from novel_forge.memory.critic import CriticAgent, CritiqueReport
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.long.services.generation import llm_helpers as llm_h
from novel_forge.pipeline.long.services.anchor_terms import (
    _extract_anchor_terms_from_bible,
)
from novel_forge.pipeline.long.services.context.story_kernel_context import load_story_kernel_composer
from novel_forge.core.utils.text_hash import source_text_hash
from novel_forge.pipeline.long.stages.reading_power_repair import (
    ReadingPowerRepairLoopResult,
    _build_reading_power_excerpt,
    _build_reading_power_input,
    _extract_reading_power_expectations,
    _infer_chapter_type,
    _reading_power_repair_issues_from_report,
    _text_change_ratio,
    evaluate_and_record_reading_power,
)
from novel_forge.pipeline.steps.alignment_step import AlignmentInput, AlignmentStep
from novel_forge.pipeline.steps.check_chapter_step import ChapterRepairInput, ChapterRepairStep
from novel_forge.pipeline.steps.continuity_eval_step import (
    ContinuityEvalInput,
    ContinuityEvalStep,
)
from novel_forge.pipeline.steps.editorial_check_step import EditorialCheckInput, EditorialCheckStep
from novel_forge.pipeline.token_budget import calculate_route_aware_max_tokens, route_output_limit
from novel_forge.story_kernel.schemas import StoryKernel

_logger = get_logger("pipeline.quality_checks")

# ── Shared helpers (imported by sibling modules) ─────────────────────────────


_OPENING_GUARD_PENDING_ATTR = "_novel_forge_opening_guard_pending_issues"

# Backward-compatible alias for tests and stale-report checks that imported the old private helper.
_source_text_hash = source_text_hash

# Review helpers use this facade via ``import *``; keep exports statically
# discoverable so private helper imports remain type checked.
__all__ = [
    "AlignmentInput",
    "AlignmentStep",
    "Any",
    "Callable",
    "ChapterRepairInput",
    "ChapterRepairStep",
    "ContinuityEvalInput",
    "ContinuityEvalStep",
    "CriticAgent",
    "CritiqueReport",
    "EditorialCheckInput",
    "EditorialCheckStep",
    "KNOWN_PROMPT_MARKERS",
    "ModelGatewayError",
    "ReadingPowerRepairLoopResult",
    "RepairTicket",
    "ReviewFinding",
    "SEVERITY_RANK",
    "StoryKernel",
    "TaskType",
    "_OPENING_GUARD_PENDING_ATTR",
    "_build_reading_power_excerpt",
    "_build_reading_power_input",
    "_extract_anchor_terms_from_bible",
    "_extract_reading_power_expectations",
    "_infer_chapter_type",
    "_logger",
    "_reading_power_repair_issues_from_report",
    "_source_text_hash",
    "_text_change_ratio",
    "annotations",
    "asdict",
    "asyncio",
    "calculate_route_aware_max_tokens",
    "copy",
    "count_chapter_words",
    "detect_drift",
    "diff_issues",
    "difflib",
    "evaluate_and_record_reading_power",
    "finding_auto_repair_eligible",
    "format_address_rules_for_prompt",
    "get_logger",
    "is_dataclass",
    "llm_h",
    "load_story_kernel_composer",
    "prepare_findings_for_repair",
    "re",
    "render_world_context_rules",
    "route_output_limit",
    "severity_at_least",
    "source_text_hash",
    "stable_issue_id",
]
