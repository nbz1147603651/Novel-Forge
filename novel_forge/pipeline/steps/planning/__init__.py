"""Chapter planning step — split module.

Re-exports PlanInput and PlanChapterStep for backward compatibility.
"""

from __future__ import annotations

from novel_forge.pipeline.steps.planning.context import (
    build_character_identity_cards,
    trim_plan_canon_context,
    trim_plan_memory_hints,
)
from novel_forge.pipeline.steps.planning.core import (
    PlanChapterStep,
    PlanInput,
    PlanPromptContexts,
    _kernel_slices_to_canon_context,
    _normalize_plan_payload,
    build_plan_prompt_contexts,
)
from novel_forge.pipeline.steps.planning.hints import (
    enforce_revelation_budget,
    enforce_scene_switch_limit,
    enforce_unresolved_retention,
    missing_core_plan_keys,
    resolve_min_unresolved_threads,
    resolve_revelation_budget,
    response_is_token_capped,
)

__all__ = [
    "PlanInput",
    "PlanPromptContexts",
    "PlanChapterStep",
    "_kernel_slices_to_canon_context",
    "_normalize_plan_payload",
    "build_character_identity_cards",
    "build_plan_prompt_contexts",
    "enforce_revelation_budget",
    "enforce_scene_switch_limit",
    "enforce_unresolved_retention",
    "missing_core_plan_keys",
    "resolve_min_unresolved_threads",
    "resolve_revelation_budget",
    "response_is_token_capped",
    "trim_plan_canon_context",
    "trim_plan_memory_hints",
]
