"""Shared imports and constants for long project initialization modules."""
# ruff: noqa: F401,I001

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator, Literal, Sequence, cast

from pydantic import ValidationError

from novel_forge.core.domain.bible_derived_provider import BibleDerivedProvider
from novel_forge.core.constants import PipelineConstants, TaskType
from novel_forge.core.exceptions import TaskCircuitOpenError
from novel_forge.core.domain.forbidden_element_registry import ForbiddenElementRegistry
from novel_forge.core.schemas.bible import CharacterBible, CharacterKnowledgeBoundary, StoryBible
from novel_forge.core.schemas.blueprint_elements import BlueprintElementSelection
from novel_forge.core.schemas.init_coherence import CoherenceClaim, InitCreativeRefinementReport
from novel_forge.core.schemas.outline import NarrativeBlueprint, StoryOutline
from novel_forge.core.schemas.spec import StorySpec
from novel_forge.core.domain.shared_anchor import build_shared_evidence_anchor
from novel_forge.core.domain.world_context import dump_story_bible_for_prompt
from novel_forge.editorial.schemas import EditorialContract
from novel_forge.editorial.validators import validate_editorial_contract
from novel_forge.gateway.profiles import get_model_max_output_tokens
from novel_forge.narrative_state.schemas import (
    ChapterContract,
    EntityRecord,
    EntityRegistry,
    EntityType,
    stable_id,
)
from novel_forge.narrative_state.store import NarrativeStateStore
from novel_forge.pipeline.artifact_manifest import (
    STATUS_BLOCKED,
    STATUS_NEEDS_REPAIR,
    STATUS_SKIPPED,
    STATUS_SUCCEEDED,
    manifest_for_context,
)
from novel_forge.pipeline.long.decisions import should_use_volume_mode
from novel_forge.pipeline.long.repair_safety import RepairFailurePolicy
from novel_forge.pipeline.long.services.generation import llm_helpers as llm_h
from novel_forge.pipeline.long.services.blueprint import outline_helpers as outline_h
from novel_forge.pipeline.long.services.blueprint.blueprint_payloads import (
    pre_normalize_blueprint_payload,
)
from novel_forge.pipeline.long.services.init.init_batch_sizing import (
    effective_init_batch_size,
    record_effective_init_batch_size,
)
from novel_forge.pipeline.long.services.init.init_cache import (
    _assert_canon_project_id_matches,
    _build_base_ctx,
    _build_long_init_request_payload,
    _ensure_long_init_request_fresh,
    _load_cached_json_or_rollback,
    _load_cached_model_or_rollback,
)
from novel_forge.pipeline.long.services.init.init_cache import (
    _clamp_int as _clamp_int,
)
from novel_forge.pipeline.long.services.init.init_cache import (
    _get_embedding_config as _get_embedding_config,
)
from novel_forge.pipeline.long.services.init.init_cache import (
    _init_artifact_paths_from_step as _init_artifact_paths_from_step,
)
from novel_forge.pipeline.long.services.init.init_cache import (
    _percent_to_chapter as _percent_to_chapter,
)
from novel_forge.pipeline.long.services.init.init_cache import (
    _quarantine_init_artifacts as _quarantine_init_artifacts,
)
from novel_forge.pipeline.long.services.init.init_cache import (
    _remove_path_if_exists as _remove_path_if_exists,
)
from novel_forge.pipeline.long.services.init.init_cache import (
    _rollback_cached_init_step as _rollback_cached_init_step,
)
from novel_forge.pipeline.long.services.init.init_claim_coverage import (
    _local_cognitive_backfill,
    run_init_claim_contract_coverage_audit,
)
from novel_forge.pipeline.long.services.init.init_coherence import (
    BLUEPRINT_ARTIFACT,
    CHAPTER_CONTRACTS_ARTIFACT,
    OUTLINE_ARTIFACT,
    InitCoherenceError,
    RepairTarget,
    apply_scoped_init_patch,
    apply_targeted_init_patch,
    backup_init_artifact,
    blocking_issues,
    build_init_readiness_report,
    coherence_blocks,
    collect_repair_scopes,
    has_repair_scope,
    init_coherence_issue_id,
    normalize_artifact_key,
    normalize_coherence_report,
    readiness_payload_allows,
    resolve_init_repair_targets,
)
from novel_forge.pipeline.long.services.init.init_coherence_auto_repair import (
    AutoRepairResult,
    auto_repair_coherence_issues,
    build_downgraded_report,
    summarize_unresolved_issues,
    verify_auto_repair_candidate,
)
from novel_forge.pipeline.long.services.init.init_coherence_v2 import (
    CLAIM_LEDGER_JSON,
    build_outline_stream_claim_chunks,
    claim_cache_stats,
    init_coherence_artifact_hashes,
    load_reusable_init_coherence_profile,
    prefetch_init_coherence_claim_chunks,
    refine_init_coherence_profile,
    run_init_coherence_v2_gate,
    semantic_compiler_runtime_fingerprint,
    set_init_profile_mode_metric,
)
from novel_forge.pipeline.long.services.init.init_context import (
    InitLongServiceContext,
    RunnerProtocol,
    build_init_context,
)
from novel_forge.pipeline.long.services.init.init_contract import (
    _build_and_persist_narrative_contract,
    _seed_canon_from_character_bible,
    build_adjudication_narrative_contract,
    normalize_llm_narrative_contract,
)
from novel_forge.pipeline.long.services.init.init_outline_batch import (
    OUTLINE_REVEAL_GUARD_VERSION,
    _batched_generate_outline,
    outline_reveal_guard_input_hashes,
)

# Re-export public symbols from sub-modules used by tests and service callers.
from novel_forge.pipeline.long.services.init.init_outline_helpers import (
    _build_outline_tracker_context as _build_outline_tracker_context,
)
from novel_forge.pipeline.long.services.init.init_outline_helpers import (
    _index_chapter_to_episodic_memory as _index_chapter_to_episodic_memory,
)
from novel_forge.pipeline.long.services.init.init_outline_helpers import (
    _initialize_outline_tracker as _initialize_outline_tracker,
)
from novel_forge.pipeline.long.services.init.init_outline_helpers import (
    _load_outline_conversation_history as _load_outline_conversation_history,
)
from novel_forge.pipeline.long.services.init.init_outline_helpers import (
    _load_partial_outline_chapters_from_session as _load_partial_outline_chapters_from_session,
)
from novel_forge.pipeline.long.services.init.init_outline_helpers import (
    _persist_partial_outline_state as _persist_partial_outline_state,
)
from novel_forge.pipeline.long.services.init.init_outline_helpers import (
    _record_outline_exchange as _record_outline_exchange,
)
from novel_forge.pipeline.long.services.init.init_outline_helpers import (
    _sanitize_outline_conversation_history as _sanitize_outline_conversation_history,
)
from novel_forge.pipeline.long.services.init.init_outline_helpers import (
    _save_episodic_memory_outline_data as _save_episodic_memory_outline_data,
)
from novel_forge.pipeline.long.services.init.init_outline_helpers import (
    _save_outline_resume_state as _save_outline_resume_state,
)
from novel_forge.pipeline.long.services.init.init_outline_helpers import (
    _save_outline_tracker_state as _save_outline_tracker_state,
)
from novel_forge.pipeline.long.services.init.init_outline_recovery import repair_outline_resume_state
from novel_forge.pipeline.long.services.init_repair import (
    InitArtifact,
    InitRepairContext,
    InitRepairOrchestrator,
    InitRepairOutcome,
    get_init_repair_policy,
)
from novel_forge.pipeline.long.services.init_repair.policies.chapter_contracts import (
    _chapter_contract_scaffold_for_llm,
    _contract_list,
)
from novel_forge.pipeline.long.services.init_repair.policies.chapter_contracts import (
    ensure_chapter_contract_coverage as _ensure_chapter_contract_coverage,
)
from novel_forge.pipeline.long.services.init.init_split_artifacts import (
    build_character_bible_split,
    build_story_bible_split,
)
from novel_forge.pipeline.long.services.init.init_v2 import (
    CHARACTER_RELATIONSHIP_MATRIX_PROMPT_VERSION,
    CHARACTER_SYSTEM_BUILD_VERSION,
    CREATIVE_DIRECTOR_PACKET_BUILD_VERSION,
    ENTITY_GRAPH_BUILD_VERSION,
    InitV2BlockCache,
    assemble_blueprint_from_fragments,
    blueprint_to_fragments,
    build_character_system,
    build_creative_director_packet,
    build_entity_graph,
    build_relationship_prompt_overview,
    hash_payload,
    project_character_bible,
)
from novel_forge.pipeline.long.services.plot_milestones import build_plot_milestone_index
from novel_forge.pipeline.long.services.context.source_artifacts import (
    build_init_entity_catalog,
    normalize_chapter_contract_entity_references,
    persist_init_source_artifacts,
    project_init_entity_catalog,
)
from novel_forge.pipeline.style_profile_helpers import (
    build_reading_power_window_config_from_settings,
)
from novel_forge.pipeline.long.services.weave_validation import (
    build_subplot_execution_matrix,
    validate_subplot_weave,
)
from novel_forge.pipeline.steps.blueprint_element_select_step import (
    BlueprintElementSelectInput,
    BlueprintElementSelectStep,
    has_manual_selector_preferences,
)
from novel_forge.pipeline.steps.editorial_contract_step import (
    EditorialContractInput,
    EditorialContractStep,
)
from novel_forge.pipeline.steps.spec_step import SpecStep
from novel_forge.pipeline.token_budget import (
    calculate_route_aware_max_tokens,
    route_max_output_budget,
    structured_json_output_floor,
)
from novel_forge.prompts.context_types import validate_plan_outline_context
from novel_forge.story_kernel.gates import InitTruthGate
from novel_forge.story_kernel.init_adapter import build_kernel_from_init
from novel_forge.story_kernel.schemas import KnowledgeLedger
from novel_forge.story_kernel.store import StoryKernelStore

_log = logging.getLogger(__name__)


# Keep this explicit: initialization services import this facade with ``*`` and
# mypy cannot infer private re-exports from a dynamically built ``__all__``.
__all__ = [
    "Any",
    "AutoRepairResult",
    "BLUEPRINT_ARTIFACT",
    "BibleDerivedProvider",
    "BlueprintElementSelectInput",
    "BlueprintElementSelectStep",
    "BlueprintElementSelection",
    "CHAPTER_CONTRACTS_ARTIFACT",
    "CHARACTER_RELATIONSHIP_MATRIX_PROMPT_VERSION",
    "CHARACTER_SYSTEM_BUILD_VERSION",
    "CLAIM_LEDGER_JSON",
    "CREATIVE_DIRECTOR_PACKET_BUILD_VERSION",
    "ChapterContract",
    "CharacterBible",
    "CharacterKnowledgeBoundary",
    "CoherenceClaim",
    "ENTITY_GRAPH_BUILD_VERSION",
    "EditorialContract",
    "EditorialContractInput",
    "EditorialContractStep",
    "EntityRecord",
    "EntityRegistry",
    "EntityType",
    "ForbiddenElementRegistry",
    "InitArtifact",
    "InitCoherenceError",
    "InitCreativeRefinementReport",
    "InitLongServiceContext",
    "InitRepairContext",
    "InitRepairOrchestrator",
    "InitRepairOutcome",
    "InitTruthGate",
    "InitV2BlockCache",
    "Iterator",
    "KnowledgeLedger",
    "Literal",
    "NarrativeBlueprint",
    "NarrativeStateStore",
    "OUTLINE_ARTIFACT",
    "OUTLINE_REVEAL_GUARD_VERSION",
    "Path",
    "PipelineConstants",
    "RepairFailurePolicy",
    "RepairTarget",
    "RunnerProtocol",
    "STATUS_BLOCKED",
    "STATUS_NEEDS_REPAIR",
    "STATUS_SKIPPED",
    "STATUS_SUCCEEDED",
    "Sequence",
    "SpecStep",
    "StoryBible",
    "StoryKernelStore",
    "StoryOutline",
    "StorySpec",
    "TYPE_CHECKING",
    "TaskCircuitOpenError",
    "TaskType",
    "UTC",
    "ValidationError",
    "_assert_canon_project_id_matches",
    "_batched_generate_outline",
    "_build_and_persist_narrative_contract",
    "_build_base_ctx",
    "_build_long_init_request_payload",
    "_build_outline_tracker_context",
    "_chapter_contract_scaffold_for_llm",
    "_clamp_int",
    "_contract_list",
    "_ensure_chapter_contract_coverage",
    "_ensure_long_init_request_fresh",
    "_get_embedding_config",
    "_index_chapter_to_episodic_memory",
    "_init_artifact_paths_from_step",
    "_initialize_outline_tracker",
    "_load_cached_json_or_rollback",
    "_load_cached_model_or_rollback",
    "_load_outline_conversation_history",
    "_load_partial_outline_chapters_from_session",
    "_local_cognitive_backfill",
    "_log",
    "_percent_to_chapter",
    "_persist_partial_outline_state",
    "_quarantine_init_artifacts",
    "_record_outline_exchange",
    "_remove_path_if_exists",
    "_rollback_cached_init_step",
    "_sanitize_outline_conversation_history",
    "_save_episodic_memory_outline_data",
    "_save_outline_resume_state",
    "_save_outline_tracker_state",
    "_seed_canon_from_character_bible",
    "annotations",
    "apply_scoped_init_patch",
    "apply_targeted_init_patch",
    "assemble_blueprint_from_fragments",
    "asyncio",
    "auto_repair_coherence_issues",
    "backup_init_artifact",
    "blocking_issues",
    "blueprint_to_fragments",
    "build_adjudication_narrative_contract",
    "build_character_bible_split",
    "build_character_system",
    "build_creative_director_packet",
    "build_downgraded_report",
    "verify_auto_repair_candidate",
    "build_entity_graph",
    "build_init_context",
    "build_init_entity_catalog",
    "build_init_readiness_report",
    "build_kernel_from_init",
    "build_outline_stream_claim_chunks",
    "build_plot_milestone_index",
    "build_reading_power_window_config_from_settings",
    "build_relationship_prompt_overview",
    "build_shared_evidence_anchor",
    "build_story_bible_split",
    "build_subplot_execution_matrix",
    "calculate_route_aware_max_tokens",
    "cast",
    "claim_cache_stats",
    "coherence_blocks",
    "collect_repair_scopes",
    "copy",
    "dataclass",
    "datetime",
    "dump_story_bible_for_prompt",
    "effective_init_batch_size",
    "get_init_repair_policy",
    "get_model_max_output_tokens",
    "has_manual_selector_preferences",
    "has_repair_scope",
    "hash_payload",
    "hashlib",
    "init_coherence_artifact_hashes",
    "init_coherence_issue_id",
    "json",
    "llm_h",
    "load_reusable_init_coherence_profile",
    "logging",
    "manifest_for_context",
    "normalize_artifact_key",
    "normalize_chapter_contract_entity_references",
    "normalize_coherence_report",
    "normalize_llm_narrative_contract",
    "outline_h",
    "outline_reveal_guard_input_hashes",
    "persist_init_source_artifacts",
    "pre_normalize_blueprint_payload",
    "prefetch_init_coherence_claim_chunks",
    "project_character_bible",
    "project_init_entity_catalog",
    "re",
    "readiness_payload_allows",
    "record_effective_init_batch_size",
    "refine_init_coherence_profile",
    "repair_outline_resume_state",
    "resolve_init_repair_targets",
    "route_max_output_budget",
    "run_init_claim_contract_coverage_audit",
    "run_init_coherence_v2_gate",
    "semantic_compiler_runtime_fingerprint",
    "set_init_profile_mode_metric",
    "should_use_volume_mode",
    "stable_id",
    "structured_json_output_floor",
    "summarize_unresolved_issues",
    "validate_editorial_contract",
    "validate_plan_outline_context",
    "validate_subplot_weave",
]
