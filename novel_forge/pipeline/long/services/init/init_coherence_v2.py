"""Memory-backed init coherence v2: profile, claims, candidates, adjudication."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from novel_forge.common.constants import TaskType
from novel_forge.core.exceptions import ModelGatewayError
from novel_forge.core.format_contracts import get_task_format_contract
from novel_forge.core.parsing.response_schemas import get_response_schema
from novel_forge.core.schemas.init_coherence import (
    ClaimChapterRange,
    ClaimType,
    CoherenceClaim,
    CoherenceClaimBatch,
    ConflictCandidate,
    ConflictCandidateReport,
    InitConflictAdjudicationReport,
)
from novel_forge.core.semantic_consistency import semantic_payload_hash
from novel_forge.memory.episodic import EpisodicMemory
from novel_forge.pipeline.long.services.init.init_batch_checkpoint import (
    load_init_batch_checkpoint,
    save_init_batch_checkpoint,
)
from novel_forge.pipeline.long.services.init.init_cache import _get_embedding_config
from novel_forge.pipeline.long.services.init.init_coherence_claim_cache import (
    claim_batch_cache_paths as _claim_batch_cache_paths_impl,
)
from novel_forge.pipeline.long.services.init.init_coherence_claim_cache import (
    load_cached_claim_batch as _load_cached_claim_batch_impl,
)
from novel_forge.pipeline.long.services.init.init_coherence_claim_cache import (
    save_cached_claim_batch as _save_cached_claim_batch_impl,
)
from novel_forge.pipeline.long.services.init.init_coherence_cognitive_filters import (
    CognitiveFilterDeps,
)
from novel_forge.pipeline.long.services.init.init_coherence_cognitive_filters import (
    claim_cognitive_object_key as _claim_cognitive_object_key_impl,
)
from novel_forge.pipeline.long.services.init.init_coherence_cognitive_filters import (
    claim_effective_cognitive_chapter as _claim_effective_cognitive_chapter_impl,
)
from novel_forge.pipeline.long.services.init.init_coherence_cognitive_filters import (
    claim_has_foreshadow_after_reveal as _claim_has_foreshadow_after_reveal_impl,
)
from novel_forge.pipeline.long.services.init.init_coherence_cognitive_filters import (
    claims_have_action_level_conflict as _claims_have_action_level_conflict_impl,
)
from novel_forge.pipeline.long.services.init.init_coherence_cognitive_filters import (
    claims_have_awareness_conflict as _claims_have_awareness_conflict_impl,
)
from novel_forge.pipeline.long.services.init.init_coherence_cognitive_filters import (
    claims_have_cognitive_regression as _claims_have_cognitive_regression_impl,
)
from novel_forge.pipeline.long.services.init.init_coherence_cognitive_filters import (
    claims_have_premature_public_reveal as _claims_have_premature_public_reveal_impl,
)
from novel_forge.pipeline.long.services.init.init_coherence_cognitive_filters import (
    cognitive_candidate_kinds as _cognitive_candidate_kinds_impl,
)
from novel_forge.pipeline.long.services.init.init_coherence_cognitive_filters import (
    cognitive_group_keys as _cognitive_group_keys_impl,
)
from novel_forge.pipeline.long.services.init.init_coherence_cognitive_filters import (
    cognitive_subject_overlap as _cognitive_subject_overlap_impl,
)
from novel_forge.pipeline.long.services.init.init_coherence_cognitive_filters import (
    internal_and_public_claim as _internal_and_public_claim_impl,
)
from novel_forge.pipeline.long.services.init.init_coherence_extract import (
    ClaimExtractionDeps,
)
from novel_forge.pipeline.long.services.init.init_coherence_extract import (
    extract_claims as _extract_claims_impl,
)
from novel_forge.pipeline.long.services.init.init_coherence_extract import (
    extract_stage_claims as _extract_stage_claims_impl,
)
from novel_forge.pipeline.long.services.init.init_coherence_profile import (
    PROFILE_REPORT as PROFILE_REPORT,
)
from novel_forge.pipeline.long.services.init.init_coherence_profile import (
    _coerce_profile_payload as _coerce_profile_payload,
)
from novel_forge.pipeline.long.services.init.init_coherence_profile import (
    _emit_step,
    _increment_init_metric,
    _init_efficiency_metrics,
)
from novel_forge.pipeline.long.services.init.init_coherence_profile import (
    claim_cache_stats as claim_cache_stats,
)
from novel_forge.pipeline.long.services.init.init_coherence_profile import (
    load_reusable_init_coherence_profile as load_reusable_init_coherence_profile,
)
from novel_forge.pipeline.long.services.init.init_coherence_profile import (
    refine_init_coherence_profile as refine_init_coherence_profile,
)
from novel_forge.pipeline.long.services.init.init_coherence_profile import (
    set_init_profile_mode_metric as set_init_profile_mode_metric,
)
from novel_forge.pipeline.long.services.init.init_coherence_report_merge import (
    _merge_focus_chunk_reports,
    _report_int_metric,
)
from novel_forge.pipeline.long.services.init.init_coherence_retrieval_exact import (
    ExactRetrievalDeps,
)
from novel_forge.pipeline.long.services.init.init_coherence_retrieval_exact import (
    retrieve_exact_candidates as _retrieve_exact_candidates_impl,
)
from novel_forge.pipeline.long.services.init.init_coherence_retrieval_exact import (
    structural_pair_keys as _structural_pair_keys,
)
from novel_forge.pipeline.long.services.init.init_entity_references import (
    adjudicate_init_claim_entity_references,
    entity_catalog_revision,
)
from novel_forge.pipeline.long.services.init.init_semantic_repair_cases import (
    reconcile_semantic_repair_cases,
)
from novel_forge.pipeline.token_budget import calculate_route_aware_max_tokens
from novel_forge.prompts.packs import discover_prompt_packs
from novel_forge.prompts.version import TemplateVersionManager

_log = logging.getLogger(__name__)

CLAIMS_JSONL = "init_coherence_claims.jsonl"
CLAIM_LEDGER_JSON = "init_coherence_claim_ledger.json"
CLAIM_BATCH_CACHE_DIR = "init_coherence_claim_batches"
CLAIM_BATCH_CACHE_SCHEMA_VERSION = 10
CLAIM_LEDGER_STAGE_SCHEMA_VERSION = 6
SEMANTIC_COMPILER_CONTRACT_VERSION = "sc1.1"
INDEX_JSON = "init_coherence_index.json"
CANDIDATES_REPORT = "init_conflict_candidates.json"
ADJUDICATION_REPORT = "init_conflict_adjudication.json"
_VOLATILE_HASH_KEYS = frozenset({"created_at", "schema_version"})
_GENERIC_CLAIM_BATCH_CAP = 3
_CLAIM_PAYLOAD_CHAR_BUDGET = 18_000
_CLAIM_PAYLOAD_CHAR_BUDGET_MIN = 200
_DEFAULT_CLAIM_LIMIT = 6
_BLUEPRINT_HOLISTIC_CLAIM_LIMIT = 10

_CLAIM_ID_RE = re.compile(r"^\[([^\]]+)\]\s*")
_SAFE_ID_RE = re.compile(r"[^0-9A-Za-z_\-.]+")
_WINDOW_SOURCE_PATH_RE = re.compile(r"^/(?P<field>[^/]+)/(?P<start>\d+)\s*:\s*(?P<end>\d+)$")
_SOURCE_PATH_ROOT_RE = re.compile(r"^/(?P<field>[^/]+)")

_CLAIM_TYPE_CODES = {
    "state": 1,
    "event": 2,
    "payoff": 3,
    "dependency": 4,
    "relationship": 5,
    "world_rule": 6,
    "knowledge": 7,
    "promise": 8,
    "other": 9,
}

_COGNITIVE_LEVEL_CODES = {
    "unaware": 1,
    "subconscious": 2,
    "suspicion": 3,
    "partial": 4,
    "confirmed": 5,
    "acknowledged": 6,
}

_ACTION_LEVEL_CODES = {
    "none": 1,
    "internal": 2,
    "hinted": 3,
    "revealed": 4,
    "acted": 5,
}

_AWARENESS_LEVEL_CODES = {
    "unknown": 1,
    "partial": 2,
    "full": 3,
}

_TEMPORALITY_DEDUCED_CODES = {
    "actual": 1,
    "flashback": 2,
    "foreshadow": 3,
    "vision": 4,
    "hypothetical": 5,
    "planned": 6,
    "unknown": 7,
}

_COGNITIVE_LEVEL_ORDER: tuple[str, ...] = (
    "unaware",
    "subconscious",
    "suspicion",
    "partial",
    "confirmed",
    "acknowledged",
)

_COGNITIVE_CANDIDATE_TYPES = frozenset(
    {
        "cognitive_regression",
        "premature_public_reveal",
        "awareness_conflict",
        "foreshadow_after_reveal",
        "action_level_conflict",
    }
)

_ACTION_PUBLIC_HINTS: tuple[str, ...] = (
    "揭示",
    "公开",
    "披露",
    "坦白",
    "承认",
    "说明",
    "提及",
    "告诉",
    "告诉他",
    "告诉她",
    "当众",
)

_ACTION_HINT_KEYWORDS: tuple[str, ...] = (
    "暗示",
    "试探",
    "试探性",
    "留意",
    "提示",
)

_COGNITIVE_INDEX_INT_FIELDS = (
    "claim_type_code",
    "cognitive_level_code",
    "action_level_code",
    "reader_awareness_code",
    "temporality_code",
    "irreversible_code",
    "chapter_start",
    "chapter_end",
    "cognitive_chapter",
    "public_reveal_chapter",
    "foreshadow_min",
    "foreshadow_max",
    "subject_hash_0",
    "subject_hash_1",
    "subject_hash_2",
    "subject_hash_3",
)


def _claim_int_code(mapping: dict[str, int], value: Any, *, fallback: int) -> int:
    return mapping.get(str(value or "").strip().lower(), fallback)


def _claim_end_chapter(claim: CoherenceClaim) -> int:
    if claim.chapter_range is not None and claim.chapter_range.end:
        return claim.chapter_range.end
    if claim.chapter_numbers:
        return max(claim.chapter_numbers)
    return 0


def _subject_hash_codes(subjects: list[str], *, max_count: int = 4) -> dict[str, int]:
    code_map: dict[str, int] = {}
    for index, subject in enumerate(subjects[:max_count]):
        text = _normalize_key(subject)
        if not text:
            continue
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        code_map[f"subject_hash_{index}"] = int(digest[:8], 16) % 10_000_000
    return code_map


def _claim_index_metadata(claim: CoherenceClaim) -> dict[str, Any]:
    chapter_start = _claim_start_chapter(claim)
    chapter_end = _claim_end_chapter(claim)
    metadata: dict[str, Any] = {
        "claim_type_code": _claim_int_code(_CLAIM_TYPE_CODES, claim.claim_type, fallback=9),
        "cognitive_level_code": _claim_int_code(
            _COGNITIVE_LEVEL_CODES, claim.cognitive_level, fallback=1
        ),
        "action_level_code": _claim_int_code(_ACTION_LEVEL_CODES, claim.action_level, fallback=1),
        "reader_awareness_code": _claim_int_code(
            _AWARENESS_LEVEL_CODES,
            claim.reader_awareness,
            fallback=1,
        ),
        "temporality_code": _claim_int_code(
            _TEMPORALITY_DEDUCED_CODES,
            claim.temporality,
            fallback=7,
        ),
        "irreversible_code": 1 if claim.irreversible else 0,
        "chapter_start": chapter_start or 1,
        "chapter_end": chapter_end or chapter_start or 1,
    }
    if claim.cognitive_chapter:
        metadata["cognitive_chapter"] = claim.cognitive_chapter
    if claim.public_reveal_chapter:
        metadata["public_reveal_chapter"] = claim.public_reveal_chapter
    if claim.foreshadow_chapters:
        foreshadow = [number for number in sorted(set(claim.foreshadow_chapters)) if number > 0]
        if foreshadow:
            metadata["foreshadow_min"] = foreshadow[0]
            metadata["foreshadow_max"] = foreshadow[-1]
    metadata.update(_subject_hash_codes(claim.cognitive_subjects or claim.subject_ids))
    return metadata


def _build_semantic_query(claim: CoherenceClaim) -> str:
    cognitive_object = f" | {claim.cognitive_object}" if claim.cognitive_object else ""
    return f"[{claim.claim_id}] {claim.claim_text}{cognitive_object}"


_REPAIR_FIELDS_BY_ARTIFACT: dict[str, list[str]] = {
    "blueprint": [
        "synopsis",
        "volumes",
        "narrative_phases",
        "key_turning_points",
        "character_arcs",
        "subplot_plan",
        "suspense_schedule",
        "ending_strategy",
    ],
    "outline": [
        "goal",
        "beats_summary",
        "main_plot_points",
        "notes",
        "expected_hook",
        "expected_payoffs",
    ],
    "chapter_contracts": [
        "entry_state_requirements",
        "required_events",
        "allowed_changes",
        "forbidden_changes",
        "promise_ops",
        "relationship_ops",
        "item_ops",
        "knowledge_ops",
        "cognitive_constraints",
        "new_character_candidates",
        "new_entity_candidates",
        "new_group_candidates",
        "new_collective_candidates",
        "new_organization_candidates",
        "new_location_candidates",
        "new_item_candidates",
        "new_concept_candidates",
        "exit_state_targets",
        "required_progressions",
        "allowed_progressions",
        "forbidden_progressions",
        "completion_criteria",
        "future_leak_risks",
    ],
    "chapter_plan": [
        "scene_intents",
        "opening_contract",
        "closing_contract",
        "required_state_transitions",
        "world_rule_applications",
        "foreshadowing_plan",
        "key_revelations",
        "relationship_evolution",
    ],
}

_CHAPTER_CONTRACT_REPAIR_FIELDS_BY_CLAIM_TYPE: dict[str, set[str]] = {
    "state": {
        "entry_state_requirements",
        "exit_state_targets",
        "required_progressions",
        "completion_criteria",
    },
    "event": {
        "required_events",
        "required_progressions",
        "completion_criteria",
        "future_leak_risks",
    },
    "payoff": {
        "promise_ops",
        "required_events",
        "required_progressions",
        "completion_criteria",
        "future_leak_risks",
    },
    "promise": {
        "promise_ops",
        "required_progressions",
        "completion_criteria",
        "future_leak_risks",
    },
    "relationship": {
        "relationship_ops",
        "required_progressions",
        "completion_criteria",
        "exit_state_targets",
    },
    "knowledge": {
        "knowledge_ops",
        "cognitive_constraints",
        "required_progressions",
        "completion_criteria",
        "future_leak_risks",
    },
    "world_rule": {
        "knowledge_ops",
        "required_progressions",
        "completion_criteria",
        "future_leak_risks",
    },
    "dependency": {
        "entry_state_requirements",
        "required_progressions",
        "completion_criteria",
        "future_leak_risks",
    },
    "other": {
        "required_progressions",
        "completion_criteria",
    },
}

_OUTLINE_REPAIR_FIELDS_BY_CLAIM_TYPE: dict[str, set[str]] = {
    "state": {"goal", "main_plot_points", "beats_summary", "notes"},
    "event": {"goal", "main_plot_points", "beats_summary"},
    "payoff": {"expected_payoffs", "main_plot_points", "notes"},
    "promise": {"expected_payoffs", "main_plot_points", "notes"},
    "relationship": {"goal", "main_plot_points", "beats_summary", "notes"},
    "knowledge": {"main_plot_points", "beats_summary", "notes"},
    "world_rule": {"main_plot_points", "beats_summary", "notes"},
    "dependency": {"goal", "main_plot_points", "notes"},
    "other": {"goal", "main_plot_points", "beats_summary", "notes"},
}

_RELATIONSHIP_REPAIR_HINTS = (
    "关系",
    "相认",
    "结盟",
    "同盟",
    "合作",
    "信任",
    "背叛",
    "敌对",
    "婚",
    "亲密",
    "相拥",
)

_ITEM_REPAIR_HINTS = (
    "物件",
    "道具",
    "信物",
    "证据",
    "线索",
    "遗物",
    "钥匙",
    "名册",
    "账册",
    "兵符",
    "令牌",
    "残片",
    "密信",
    "书信",
    "信件",
    "玉",
    "印",
    "册",
    "药",
    "剑",
)

_REPAIR_SCOPE_RANGE_EXPANSION_LIMIT = 30

_VALID_CLAIM_TYPES = {
    "state",
    "event",
    "payoff",
    "dependency",
    "relationship",
    "world_rule",
    "knowledge",
    "promise",
    "other",
}

_VALID_TEMPORALITIES = {
    "actual",
    "flashback",
    "foreshadow",
    "vision",
    "hypothetical",
    "planned",
    "unknown",
}

_VALID_COGNITIVE_LEVELS = {
    "unaware",
    "subconscious",
    "suspicion",
    "partial",
    "confirmed",
    "acknowledged",
}

_VALID_ACTION_LEVELS = {
    "none",
    "internal",
    "hinted",
    "revealed",
    "acted",
}

_VALID_AWARENESS_LEVELS = {
    "unknown",
    "partial",
    "full",
}


@dataclass(frozen=True)
class InitArtifactChunk:
    """One bounded chunk sent to claim extraction."""

    artifact: str
    chunk_id: str
    source_path: str
    source_field: str
    chapter_numbers: list[int]
    payload: dict[str, Any]
    extraction_mode: str = "shard"
    evidence_refs: list[str] = field(default_factory=list)


async def run_init_coherence_v2_gate(
    ctx: Any,
    *,
    stage: str,
    repair_artifact: str,
    profile: dict[str, Any],
    artifacts: dict[str, dict[str, Any]],
    focus_chapters: list[int] | None = None,
    allow_focus_chunking: bool = True,
) -> dict[str, Any]:
    """Run claim extraction, retrieval, and LLM adjudication for one init gate."""
    compiler_fingerprint = semantic_compiler_fingerprint(
        ctx,
        profile=profile,
        artifacts=artifacts,
    )
    compiler_runtime_fingerprint = semantic_compiler_runtime_fingerprint(ctx)
    ctx._init_coherence_compiler_fingerprint = compiler_fingerprint
    ctx._init_coherence_compiler_runtime_fingerprint = compiler_runtime_fingerprint
    semantic_inputs = getattr(ctx, "_semantic_gate_inputs", None)
    if not isinstance(semantic_inputs, dict):
        semantic_inputs = {}
        ctx._semantic_gate_inputs = semantic_inputs
    semantic_inputs[stage] = {
        "stage": stage,
        "repair_artifact": repair_artifact,
        "profile": profile,
        "artifacts": artifacts,
        "focus_chapters": list(focus_chapters or []),
    }
    _reset_claim_coverage(ctx, stage)
    focus_numbers = sorted({number for number in (focus_chapters or []) if number >= 1})
    if focus_numbers and allow_focus_chunking:
        focus_chunks = _split_focus_chapters_for_recheck(ctx.settings, focus_numbers)
        if len(focus_chunks) > 1:
            return await _run_chunked_focus_recheck(
                ctx,
                stage=stage,
                repair_artifact=repair_artifact,
                profile=profile,
                artifacts=artifacts,
                focus_chunks=focus_chunks,
            )

    if not focus_numbers:
        finalize_streamed_init_coherence_claims(ctx, stage=stage, artifacts=artifacts)
    reusable = _load_reusable_stage_claims(
        ctx,
        stage=stage,
        artifacts=artifacts,
        focus_chapters=focus_numbers or None,
        entity_catalog=_entity_catalog_from_profile(profile),
    )
    if reusable is not None:
        claims, claim_ledger = reusable
    else:
        chunks = _build_artifact_chunks(
            ctx.settings,
            artifacts,
            focus_chapters=focus_numbers or None,
        )
        claims = await _extract_stage_claims(
            ctx,
            stage=stage,
            profile=profile,
            artifacts=artifacts,
            chunks=chunks,
            focus_chapters=focus_numbers or None,
        )
        claim_ledger = _persist_claims(
            ctx,
            claims,
            stage=stage,
            artifacts=artifacts,
            focus_chapters=focus_numbers or None,
            entity_catalog=_entity_catalog_from_profile(profile),
        )
    stage_ledger = claim_ledger.get("stages", {}).get(stage, {})
    artifact_hashes = stage_ledger.get("artifact_hashes")
    if not isinstance(artifact_hashes, dict):
        artifact_hashes = init_coherence_artifact_hashes(artifacts)
    coverage = _stage_claim_coverage(stage_ledger)
    ledger_active_claim_count = len(
        [
            claim_id
            for claim_id in claim_ledger.get("active_claim_ids", []) or []
            if isinstance(claim_id, str)
        ]
    )
    retrieval_claims = _claims_for_retrieval(
        claims,
        claim_ledger,
        stage=stage,
        artifact_keys={str(key) for key in artifacts},
        focus_chapters=focus_numbers or None,
        entity_catalog=_entity_catalog_from_profile(profile),
    )
    retrieval_scope = "focus" if focus_numbers else "full"
    max_recheck_claims = _adaptive_recheck_claim_limit(ctx.settings, focus_numbers)
    if focus_numbers and max_recheck_claims and len(retrieval_claims) > max_recheck_claims:
        return _stop_oversized_focus_recheck(
            ctx,
            stage=stage,
            repair_artifact=repair_artifact,
            focus_numbers=focus_numbers,
            claims=claims,
            retrieval_claims=retrieval_claims,
            ledger_active_claim_count=ledger_active_claim_count,
            max_recheck_claims=max_recheck_claims,
            artifact_hashes=artifact_hashes,
            retrieval_scope=retrieval_scope,
        )

    candidates, degraded_memory, vector_index = await retrieve_init_conflict_candidates(
        ctx,
        retrieval_claims,
        stage=stage,
        profile=profile,
    )
    pair_coverage = _structural_pair_coverage(candidates, retrieval_claims)
    _persist_index(
        ctx,
        claims=retrieval_claims,
        vector_index=vector_index,
        degraded_memory=degraded_memory,
        claim_ledger=claim_ledger,
    )

    candidate_report = ConflictCandidateReport(
        stage=stage,
        candidates=candidates,
        claims_count=len(retrieval_claims),
        degraded_memory=degraded_memory,
        summary=(
            f"检索到 {len(candidates)} 个候选冲突；claims={len(retrieval_claims)}。"
            if candidates
            else f"未检索到候选冲突；claims={len(retrieval_claims)}。"
        ),
    )
    candidate_payload = candidate_report.model_dump(mode="json")
    candidate_payload["candidates"] = [
        _candidate_reference_payload(candidate) for candidate in candidates
    ]
    candidate_payload["claim_storage"] = {
        "mode": "claim_id_reference",
        "claim_ledger_path": f"memory/{CLAIM_LEDGER_JSON}",
    }
    candidate_payload["extracted_claims_count"] = len(claims)
    candidate_payload["active_claims_count"] = len(retrieval_claims)
    candidate_payload["ledger_active_claims_count"] = ledger_active_claim_count
    candidate_payload["retrieval_scope"] = retrieval_scope
    candidate_payload["exact_candidate_count"] = int(vector_index.get("exact_candidate_count") or 0)
    candidate_payload["semantic_candidate_count"] = int(
        vector_index.get("semantic_candidate_count") or 0
    )
    candidate_payload["post_filter_drop_count"] = int(
        vector_index.get("post_filter_drop_count") or 0
    )
    candidate_payload["fully_pushed_down_ratio"] = float(
        vector_index.get("fully_pushed_down_ratio") or 0.0
    )
    candidate_payload["zvec_backend"] = str(vector_index.get("zvec_backend") or "")
    candidate_payload["semantic_skipped_reason"] = str(
        vector_index.get("semantic_skipped_reason") or ""
    )
    candidate_payload["claim_ledger_path"] = f"memory/{CLAIM_LEDGER_JSON}"
    candidate_payload["artifact_hashes"] = artifact_hashes
    candidate_payload["focus_chapters"] = focus_numbers
    candidate_payload["claim_coverage"] = coverage
    candidate_payload["pair_coverage"] = pair_coverage
    candidate_payload["compiler_fingerprint"] = compiler_fingerprint
    candidate_payload["compiler_runtime_fingerprint"] = compiler_runtime_fingerprint
    _save_stage_bundle(ctx, CANDIDATES_REPORT, stage, candidate_payload)
    _emit_step(
        ctx,
        "retrieve_init_conflict_candidates",
        {
            "stage": stage,
            "claims": len(retrieval_claims),
            "extracted_claims": len(claims),
            "active_claims": len(retrieval_claims),
            "ledger_active_claims": ledger_active_claim_count,
            "retrieval_scope": retrieval_scope,
            "candidates": len(candidates),
            "degraded_memory": degraded_memory,
        },
    )

    report = await _adjudicate_candidates(
        ctx,
        stage=stage,
        repair_artifact=repair_artifact,
        profile=profile,
        candidates=candidates,
        claims_count=len(retrieval_claims),
        degraded_memory=degraded_memory,
    )
    report = _apply_semantic_coverage_gate(
        report,
        stage=stage,
        repair_artifact=repair_artifact,
        claim_coverage=coverage,
        pair_coverage=pair_coverage,
    )
    report_payload = report.model_dump(mode="json")
    report_payload["extracted_claims_count"] = len(claims)
    report_payload["active_claims_count"] = len(retrieval_claims)
    report_payload["ledger_active_claims_count"] = ledger_active_claim_count
    report_payload["retrieval_scope"] = retrieval_scope
    report_payload["exact_candidate_count"] = int(vector_index.get("exact_candidate_count") or 0)
    report_payload["semantic_candidate_count"] = int(
        vector_index.get("semantic_candidate_count") or 0
    )
    report_payload["post_filter_drop_count"] = int(vector_index.get("post_filter_drop_count") or 0)
    report_payload["fully_pushed_down_ratio"] = float(
        vector_index.get("fully_pushed_down_ratio") or 0.0
    )
    report_payload["zvec_backend"] = str(vector_index.get("zvec_backend") or "")
    report_payload["semantic_skipped_reason"] = str(
        vector_index.get("semantic_skipped_reason") or ""
    )
    report_payload["claim_ledger_path"] = f"memory/{CLAIM_LEDGER_JSON}"
    report_payload["artifact_hashes"] = artifact_hashes
    report_payload["focus_chapters"] = focus_numbers
    report_payload["claim_coverage"] = coverage
    report_payload["pair_coverage"] = pair_coverage
    report_payload["compiler_fingerprint"] = compiler_fingerprint
    report_payload["compiler_runtime_fingerprint"] = compiler_runtime_fingerprint
    report_payload = reconcile_semantic_repair_cases(
        ctx,
        stage=stage,
        repair_artifact=repair_artifact,
        artifacts=artifacts,
        claims=retrieval_claims,
        candidates=candidates,
        report=report_payload,
    )
    _save_stage_bundle(ctx, ADJUDICATION_REPORT, stage, report_payload)
    ctx.storage.save_json(ctx.layout.reports_dir / f"{stage}.json", report_payload)
    _emit_step(
        ctx,
        "adjudicate_init_conflict_candidates",
        {
            "stage": stage,
            "verdict": report_payload.get("verdict"),
            "summary": report_payload.get("summary"),
            "blocked": bool(report_payload.get("blocked")),
            "issue_count": len(report_payload.get("issues", []) or []),
            "high_or_critical": _count_high_or_critical(report_payload.get("issues", []) or []),
            "claims": len(retrieval_claims),
            "extracted_claims": len(claims),
            "active_claims": len(retrieval_claims),
            "ledger_active_claims": ledger_active_claim_count,
            "retrieval_scope": retrieval_scope,
            "candidates": len(candidates),
            "degraded_memory": degraded_memory,
        },
    )
    _update_claim_ledger_compilation(
        ctx,
        stage=stage,
        compiler_fingerprint=compiler_fingerprint,
        compiler_runtime_fingerprint=compiler_runtime_fingerprint,
        claim_coverage=coverage,
        pair_coverage=pair_coverage,
        report=report_payload,
    )
    return report_payload


def _stop_oversized_focus_recheck(
    ctx: Any,
    *,
    stage: str,
    repair_artifact: str,
    focus_numbers: list[int],
    claims: list[CoherenceClaim],
    retrieval_claims: list[CoherenceClaim],
    ledger_active_claim_count: int,
    max_recheck_claims: int,
    artifact_hashes: dict[str, str],
    retrieval_scope: str,
) -> dict[str, Any]:
    """Stop a polluted local recheck before expensive retrieval/adjudication."""
    summary = (
        f"{stage} 局部复检 claims={len(retrieval_claims)} 超过上限 "
        f"{max_recheck_claims}，已停止自动复检以避免循环消耗。"
    )
    candidate_report = ConflictCandidateReport(
        stage=stage,
        candidates=[],
        claims_count=len(retrieval_claims),
        degraded_memory=False,
        summary=summary,
    )
    candidate_payload = candidate_report.model_dump(mode="json")
    candidate_payload.update(
        {
            "extracted_claims_count": len(claims),
            "active_claims_count": len(retrieval_claims),
            "ledger_active_claims_count": ledger_active_claim_count,
            "retrieval_scope": retrieval_scope,
            "exact_candidate_count": 0,
            "semantic_candidate_count": 0,
            "post_filter_drop_count": 0,
            "fully_pushed_down_ratio": 0.0,
            "zvec_backend": "",
            "semantic_skipped_reason": "focus_recheck_claim_limit",
            "claim_ledger_path": f"memory/{CLAIM_LEDGER_JSON}",
            "artifact_hashes": artifact_hashes,
            "focus_chapters": focus_numbers,
            "stopped_reason": "focus_recheck_claim_limit",
            "max_recheck_claims": max_recheck_claims,
        }
    )
    _save_stage_bundle(ctx, CANDIDATES_REPORT, stage, candidate_payload)

    report = InitConflictAdjudicationReport(
        schema_version="audit_v2",
        stage=stage,
        artifact=repair_artifact,
        verdict="needs_repair",
        issues=[
            {
                "id": f"{stage}_focus_recheck_claim_limit",
                "severity": "critical",
                "description": summary,
                "repair_scope": [],
            }
        ],
        repair_scope=[],
        blocked=True,
        summary=summary,
        claims_count=len(retrieval_claims),
        candidate_count=0,
        adjudicated_candidate_count=0,
        degraded_memory=False,
    )
    report_payload = report.model_dump(mode="json")
    report_payload.update(candidate_payload)
    report_payload["verdict"] = "needs_repair"
    report_payload["blocked"] = True
    report_payload["issues"] = report.model_dump(mode="json")["issues"]
    report_payload["repair_scope"] = []
    report_payload["summary"] = summary
    _save_stage_bundle(ctx, ADJUDICATION_REPORT, stage, report_payload)
    ctx.storage.save_json(ctx.layout.reports_dir / f"{stage}.json", report_payload)
    _emit_step(
        ctx,
        "init_coherence_recheck_scope_stopped",
        {
            "stage": stage,
            "reason": "focus_recheck_claim_limit",
            "claims": len(retrieval_claims),
            "extracted_claims": len(claims),
            "ledger_active_claims": ledger_active_claim_count,
            "max_recheck_claims": max_recheck_claims,
            "focus_chapters": focus_numbers,
        },
    )
    return report_payload


def _split_focus_chapters_for_recheck(
    settings: Any,
    focus_numbers: list[int],
) -> list[list[int]]:
    normalized: set[int] = set()
    for value in focus_numbers:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number >= 1:
            normalized.add(number)
    numbers = sorted(normalized)
    if not numbers:
        return []
    max_chapters = _setting_int(settings, "init_coherence_recheck_chunk_chapters", 12)
    if max_chapters <= 0 or len(numbers) <= max_chapters:
        return [numbers]

    chunks: list[list[int]] = []
    current: list[int] = []
    previous: int | None = None
    for number in numbers:
        starts_new_run = previous is not None and number != previous + 1
        chunk_full = len(current) >= max_chapters
        if current and (starts_new_run or chunk_full):
            chunks.append(current)
            current = []
        current.append(number)
        previous = number
    if current:
        chunks.append(current)
    return chunks


async def _run_chunked_focus_recheck(
    ctx: Any,
    *,
    stage: str,
    repair_artifact: str,
    profile: dict[str, Any],
    artifacts: dict[str, dict[str, Any]],
    focus_chunks: list[list[int]],
) -> dict[str, Any]:
    focus_numbers = sorted({number for chunk in focus_chunks for number in chunk if number >= 1})
    _emit_step(
        ctx,
        "init_coherence_recheck_chunked_start",
        {
            "stage": stage,
            "chunks": len(focus_chunks),
            "focus_chapters": focus_numbers,
        },
    )

    reports: list[dict[str, Any]] = []
    for index, chunk in enumerate(focus_chunks, start=1):
        _emit_step(
            ctx,
            "init_coherence_recheck_chunk_start",
            {
                "stage": stage,
                "chunk_index": index,
                "chunk_count": len(focus_chunks),
                "focus_chapters": chunk,
            },
        )
        report = await run_init_coherence_v2_gate(
            ctx,
            stage=stage,
            repair_artifact=repair_artifact,
            profile=profile,
            artifacts=artifacts,
            focus_chapters=chunk,
            allow_focus_chunking=False,
        )
        reports.append(report)
        _emit_step(
            ctx,
            "init_coherence_recheck_chunk_done",
            {
                "stage": stage,
                "chunk_index": index,
                "chunk_count": len(focus_chunks),
                "focus_chapters": chunk,
                "verdict": report.get("verdict"),
                "blocked": bool(report.get("blocked")),
                "claims": _report_int_metric(report, "claims_count"),
                "candidates": _report_int_metric(report, "candidate_count"),
                "issue_count": len(report.get("issues") or []),
                "stopped_reason": report.get("stopped_reason", ""),
            },
        )
        if report.get("stopped_reason"):
            break

    merged = _merge_focus_chunk_reports(
        stage=stage,
        repair_artifact=repair_artifact,
        focus_chunks=focus_chunks,
        reports=reports,
    )
    candidate_payload = {
        "stage": stage,
        "candidates": [],
        "claims_count": merged["claims_count"],
        "degraded_memory": merged["degraded_memory"],
        "summary": merged["summary"],
        "extracted_claims_count": merged["extracted_claims_count"],
        "active_claims_count": merged["active_claims_count"],
        "ledger_active_claims_count": merged["ledger_active_claims_count"],
        "retrieval_scope": "focus_chunked",
        "exact_candidate_count": merged["exact_candidate_count"],
        "semantic_candidate_count": merged["semantic_candidate_count"],
        "post_filter_drop_count": merged["post_filter_drop_count"],
        "fully_pushed_down_ratio": merged["fully_pushed_down_ratio"],
        "zvec_backend": merged["zvec_backend"],
        "semantic_skipped_reason": merged["semantic_skipped_reason"],
        "claim_ledger_path": f"memory/{CLAIM_LEDGER_JSON}",
        "artifact_hashes": merged.get("artifact_hashes", {}),
        "focus_chapters": merged["focus_chapters"],
        "focus_chunks": merged["focus_chunks"],
        "completed_chunk_count": merged["completed_chunk_count"],
        "planned_chunk_count": merged["planned_chunk_count"],
    }
    _save_stage_bundle(ctx, CANDIDATES_REPORT, stage, candidate_payload)
    _save_stage_bundle(ctx, ADJUDICATION_REPORT, stage, merged)
    ctx.storage.save_json(ctx.layout.reports_dir / f"{stage}.json", merged)
    _emit_step(
        ctx,
        "init_coherence_recheck_chunks",
        {
            "stage": stage,
            "chunks": merged["completed_chunk_count"],
            "planned_chunks": merged["planned_chunk_count"],
            "verdict": merged["verdict"],
            "blocked": merged["blocked"],
            "claims": merged["claims_count"],
            "candidates": merged["candidate_count"],
            "issue_count": len(merged["issues"]),
            "stopped_reason": merged.get("stopped_reason", ""),
        },
    )
    return merged


async def retrieve_init_conflict_candidates(
    ctx: Any,
    claims: list[CoherenceClaim],
    *,
    stage: str = "",
    profile: dict[str, Any] | None = None,
) -> tuple[list[ConflictCandidate], bool, dict[str, Any]]:
    """Retrieve possible conflicts using generic exact keys plus memory semantics."""
    _emit_step(
        ctx,
        "retrieve_init_conflict_candidates_start",
        {"claims": len(claims), "stage": stage},
    )
    semantic_candidate_limit = _setting_int(
        ctx.settings,
        "init_coherence_candidate_max_per_batch",
        80,
    )
    candidates = _retrieve_exact_candidates(
        claims,
        max_candidates=semantic_candidate_limit,
    )
    exact_candidate_count = len(candidates)
    degraded_memory = False
    vector_index: dict[str, Any] = {
        "exact_candidate_count": exact_candidate_count,
        "semantic_candidate_count": 0,
        "post_filter_drop_count": 0,
        "fully_pushed_down_ratio": 0.0,
        "semantic_skipped_reason": "",
        "zvec_backend": "",
    }

    remaining = max(0, semantic_candidate_limit)
    if (
        remaining
        and getattr(ctx.settings, "init_coherence_use_memory", True)
        and _setting_int(ctx.settings, "init_coherence_semantic_top_k", 12) > 0
        and len(claims) > 1
    ):
        try:
            semantic_candidates, vector_index = await _retrieve_semantic_candidates(
                ctx,
                claims,
                max_candidates=remaining,
            )
            candidates = _dedupe_candidates([*candidates, *semantic_candidates])
            vector_index["exact_candidate_count"] = exact_candidate_count
        except Exception as exc:
            degraded_memory = True
            vector_index = {
                "exact_candidate_count": exact_candidate_count,
                "semantic_candidate_count": 0,
                "post_filter_drop_count": 0,
                "fully_pushed_down_ratio": 0.0,
                "semantic_skipped_reason": "语义检索异常，已降级为仅精确检索。",
                "degraded_memory": True,
                "error": str(exc),
                "profile_summary": (profile or {}).get("summary", ""),
            }
            _log.warning("init_coherence_memory_retrieval_degraded | error=%s", exc)
    elif (
        remaining
        and len(claims) > 1
        and not getattr(ctx.settings, "init_coherence_use_memory", True)
    ):
        vector_index["semantic_skipped_reason"] = "语义检索配置关闭，使用精确检索兜底。"
    elif remaining and len(claims) > 1:
        vector_index["semantic_skipped_reason"] = "精确候选已覆盖，不触发语义检索。"
    else:
        vector_index["semantic_skipped_reason"] = "候选规模过小，不触发语义检索。"
    if "degraded_memory" not in vector_index:
        vector_index["degraded_memory"] = degraded_memory

    return candidates, degraded_memory, vector_index


def _claim_extraction_deps() -> ClaimExtractionDeps:
    return ClaimExtractionDeps(
        build_blueprint_holistic_chunks=_build_blueprint_holistic_chunks,
        bounded_parallel_setting=_bounded_parallel_setting,
        claim_extraction_target_output_chars=_claim_extraction_target_output_chars,
        claim_limit_for_chunk=_claim_limit_for_chunk,
        dedupe_claims=_dedupe_claims,
        emit_step=_emit_step,
        entity_catalog_from_profile=_entity_catalog_from_profile,
        increment_init_metric=_increment_init_metric,
        init_efficiency_metrics=_init_efficiency_metrics,
        load_cached_claim_batch=_load_cached_claim_batch,
        logger=_log,
        normalize_claim_payload=_normalize_claim_payload,
        postprocess_claim_batch=_postprocess_claim_batch,
        save_cached_claim_batch=_save_cached_claim_batch,
    )


def _exact_retrieval_deps() -> ExactRetrievalDeps:
    return ExactRetrievalDeps(
        chapter_span_for_claims=_chapter_span_for_claims,
        claim_has_different_state_after=_claim_has_different_state_after,
        claim_has_foreshadow_after_reveal=_claim_has_foreshadow_after_reveal,
        claim_subject_key=_claim_subject_key,
        claim_sort_key=_claim_sort_key,
        claims_have_cognitive_regression=_claims_have_cognitive_regression,
        cognitive_candidate_kinds=_cognitive_candidate_kinds,
        cognitive_candidate_types=_COGNITIVE_CANDIDATE_TYPES,
        cognitive_group_keys=_cognitive_group_keys,
        normalize_key=_normalize_key,
        same_artifact_path=_same_artifact_path,
    )


def _cognitive_filter_deps() -> CognitiveFilterDeps:
    return CognitiveFilterDeps(
        claim_start_chapter=_claim_start_chapter,
        claim_subject_key=_claim_subject_key,
        cognitive_level_order=_COGNITIVE_LEVEL_ORDER,
        normalize_key=_normalize_key,
    )


async def _extract_stage_claims(
    ctx: Any,
    *,
    stage: str,
    profile: dict[str, Any],
    artifacts: dict[str, dict[str, Any]],
    chunks: list[InitArtifactChunk],
    focus_chapters: list[int] | None,
) -> list[CoherenceClaim]:
    return await _extract_stage_claims_impl(
        ctx,
        stage=stage,
        profile=profile,
        artifacts=artifacts,
        chunks=chunks,
        focus_chapters=focus_chapters,
        deps=_claim_extraction_deps(),
    )


async def _extract_claims(
    ctx: Any,
    *,
    stage: str,
    profile: dict[str, Any],
    chunks: list[InitArtifactChunk],
    task_type: TaskType = TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
    concurrency_limiter: asyncio.Semaphore | None = None,
) -> list[CoherenceClaim]:
    return await _extract_claims_impl(
        ctx,
        stage=stage,
        profile=profile,
        chunks=chunks,
        task_type=task_type,
        concurrency_limiter=concurrency_limiter,
        deps=_claim_extraction_deps(),
    )


def _claim_batch_cache_paths(
    ctx: Any,
    *,
    stage: str,
    profile: dict[str, Any],
    chunk: InitArtifactChunk,
    task_type: TaskType = TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
) -> tuple[Any, str, str] | None:
    return _claim_batch_cache_paths_impl(
        ctx,
        stage=stage,
        profile=profile,
        chunk=chunk,
        task_type=task_type,
        stable_payload_hash=_stable_payload_hash,
        cache_dir=CLAIM_BATCH_CACHE_DIR,
    )


def _load_cached_claim_batch(
    ctx: Any,
    *,
    stage: str,
    profile: dict[str, Any],
    chunk: InitArtifactChunk,
    task_type: TaskType = TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
) -> CoherenceClaimBatch | None:
    return _load_cached_claim_batch_impl(
        ctx,
        stage=stage,
        profile=profile,
        chunk=chunk,
        task_type=task_type,
        stable_payload_hash=_stable_payload_hash,
        normalize_claim_payload=_normalize_claim_payload,
        entity_catalog_from_profile=_entity_catalog_from_profile,
        logger=_log,
        cache_dir=CLAIM_BATCH_CACHE_DIR,
        schema_version=CLAIM_BATCH_CACHE_SCHEMA_VERSION,
    )


def _save_cached_claim_batch(
    ctx: Any,
    *,
    stage: str,
    profile: dict[str, Any],
    chunk: InitArtifactChunk,
    batch: CoherenceClaimBatch,
    task_type: TaskType = TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
) -> None:
    _save_cached_claim_batch_impl(
        ctx,
        stage=stage,
        profile=profile,
        chunk=chunk,
        batch=batch,
        task_type=task_type,
        stable_payload_hash=_stable_payload_hash,
        logger=_log,
        cache_dir=CLAIM_BATCH_CACHE_DIR,
        schema_version=CLAIM_BATCH_CACHE_SCHEMA_VERSION,
    )


async def _adjudicate_candidates(
    ctx: Any,
    *,
    stage: str,
    repair_artifact: str,
    profile: dict[str, Any],
    candidates: list[ConflictCandidate],
    claims_count: int,
    degraded_memory: bool,
) -> InitConflictAdjudicationReport:
    _emit_step(
        ctx,
        "adjudicate_init_conflict_candidates_start",
        {
            "stage": stage,
            "claims": claims_count,
            "candidates": len(candidates),
            "degraded_memory": degraded_memory,
        },
    )
    if not candidates:
        return InitConflictAdjudicationReport(
            schema_version="audit_v2",
            stage=stage,
            artifact=repair_artifact,
            claims_count=claims_count,
            candidate_count=0,
            adjudicated_candidate_count=0,
            degraded_memory=degraded_memory,
            summary=f"{stage} 未发现候选冲突，初始化一致性 v2 通过。",
        )

    batch_size = max(1, _setting_int(ctx.settings, "init_coherence_llm_candidate_batch_size", 8))
    max_parallel = _bounded_parallel_setting(
        ctx.settings,
        "init_coherence_llm_candidate_max_parallel",
        default=2,
        upper=8,
    )
    batch_specs = [
        (offset, candidates[offset : offset + batch_size])
        for offset in range(0, len(candidates), batch_size)
    ]

    # ── Batch bisect degradation ────────────────────────────────────────────
    # Large adjudication batches repeatedly hit output truncation / inflation
    # on small-context models (max_tokens exhausted mid-JSON, repetition
    # loops).  Instead of failing the whole stage, split the batch in half and
    # retry recursively so each sub-batch stays inside the output budget.
    _MAX_BISECT_DEPTH = 2

    async def _adjudicate_batch_with_split(
        offset: int,
        batch: list[ConflictCandidate],
        *,
        depth: int = 0,
    ) -> dict[str, Any]:
        try:
            return await _adjudicate_batch(offset, batch)
        except (ModelGatewayError, ValueError, KeyError, json.JSONDecodeError) as exc:
            if depth >= _MAX_BISECT_DEPTH or len(batch) <= 1:
                raise
            midpoint = len(batch) // 2
            left, right = batch[:midpoint], batch[midpoint:]
            _emit_step(
                ctx,
                "adjudicate_init_conflict_candidates_bisect",
                {
                    "stage": stage,
                    "depth": depth + 1,
                    "from_size": len(batch),
                    "to_sizes": [len(left), len(right)],
                    "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                },
            )
            left_report = await _adjudicate_batch_with_split(offset, left, depth=depth + 1)
            right_report = await _adjudicate_batch_with_split(
                offset + len(left), right, depth=depth + 1
            )
            return _merge_adjudication_batch_reports(left_report, right_report)

    async def _adjudicate_batch(
        offset: int,
        batch: list[ConflictCandidate],
    ) -> dict[str, Any]:
        claim_catalog = _batch_claim_catalog(batch)
        request_context = {
            "stage": stage,
            "repair_artifact": repair_artifact,
            "coherence_profile": _adjudication_profile_projection(profile),
            "claim_catalog": claim_catalog,
            "candidates": [_candidate_reference_payload(candidate) for candidate in batch],
            "repair_policy": {
                "allowed_artifact": repair_artifact,
                "allowed_fields": _REPAIR_FIELDS_BY_ARTIFACT.get(repair_artifact, []),
                "chapter_insert_delete_allowed": False,
                "block_min_severity": getattr(
                    ctx.settings,
                    "init_coherence_block_min_severity",
                    "high",
                ),
            },
        }
        candidate_ids = [candidate.candidate_id for candidate in batch]
        cache_namespace = f"coherence_candidate_adjudication:{stage}:{repair_artifact}"
        cache_batch_id = f"{offset + 1:04d}_{offset + len(batch):04d}"
        cache_input_hash = _stable_payload_hash(
            {
                "task_type": TaskType.ADJUDICATE_INIT_CONFLICT_CANDIDATES.value,
                "compiler_fingerprint": str(
                    getattr(ctx, "_init_coherence_compiler_fingerprint", "") or ""
                ),
                "request": request_context,
            }
        )
        normalized = load_init_batch_checkpoint(
            ctx,
            namespace=cache_namespace,
            batch_id=cache_batch_id,
            input_hash=cache_input_hash,
        )
        cached = normalized is not None and normalized.get("candidate_ids") == candidate_ids
        if not cached:
            response = await ctx.call_with_retry(
                TaskType.ADJUDICATE_INIT_CONFLICT_CANDIDATES,
                request_context,
                max_tokens=calculate_route_aware_max_tokens(
                    ctx.router,
                    TaskType.ADJUDICATE_INIT_CONFLICT_CANDIDATES,
                    4096,
                    prompt_overhead=6200,
                    min_tokens=2048,
                ),
                temperature=getattr(
                    ctx.settings,
                    "temp_adjudicate_init_conflict_candidates",
                    0.1,
                ),
                required_keys=(
                    "verdict",
                    "issues",
                    "source_refs",
                    "repair_scope",
                    "preserve",
                    "change_intent",
                    "blocked",
                    "summary",
                ),
                max_retries=3,
            )
            normalized = _normalize_candidate_adjudication_response(
                response,
                batch,
                repair_artifact=repair_artifact,
            )
            save_init_batch_checkpoint(
                ctx,
                namespace=cache_namespace,
                batch_id=cache_batch_id,
                input_hash=cache_input_hash,
                result=normalized,
            )
        if normalized is None:  # Defensive: both cache-hit and call paths produce a report.
            raise ValueError("candidate adjudication produced no normalized report")
        _emit_step(
            ctx,
            f"adjudicate_init_conflict_candidates_{offset + 1}_{offset + len(batch)}",
            {
                "stage": stage,
                "batch": offset // batch_size + 1,
                "batch_total": len(batch_specs),
                "verdict": normalized.get("verdict", "ambiguous"),
                "issues": len(normalized.get("issues", []) or []),
                "max_parallel": max_parallel,
                "cached": cached,
            },
        )
        return normalized

    if max_parallel <= 1 or len(batch_specs) <= 1:
        batch_reports = [
            await _adjudicate_batch_with_split(offset, batch) for offset, batch in batch_specs
        ]
    else:
        semaphore = asyncio.Semaphore(min(max_parallel, len(batch_specs)))

        async def _run_limited(
            offset: int,
            batch: list[ConflictCandidate],
        ) -> dict[str, Any]:
            async with semaphore:
                return await _adjudicate_batch_with_split(offset, batch)

        batch_reports = list(
            await asyncio.gather(*[_run_limited(offset, batch) for offset, batch in batch_specs])
        )

    merged = _merge_adjudication_reports(
        stage=stage,
        repair_artifact=repair_artifact,
        batch_reports=batch_reports,
        claims_count=claims_count,
        candidate_count=len(candidates),
        degraded_memory=degraded_memory,
    )
    return InitConflictAdjudicationReport.model_validate(merged)


def _adjudication_profile_projection(profile: dict[str, Any]) -> dict[str, Any]:
    """Remove runtime-only catalogs from the repeated LLM adjudication profile."""

    allowed = (
        "schema_version",
        "genre_tags",
        "narrative_modes",
        "project_ontology",
        "conflict_lens",
        "extraction_guidance",
        "summary",
        "refined_from_blueprint",
    )
    return {key: profile[key] for key in allowed if key in profile}


def _candidate_reference_payload(candidate: ConflictCandidate) -> dict[str, Any]:
    """Persist and prompt candidates by claim ID instead of embedding claim copies."""

    chapter_span = candidate.chapter_span
    return {
        "candidate_id": candidate.candidate_id,
        "candidate_type": candidate.candidate_type,
        "reason": candidate.reason,
        "claim_ids": list(candidate.claim_ids),
        "retrieval_sources": list(candidate.retrieval_sources),
        "severity_hint": candidate.severity_hint,
        "confidence": candidate.confidence,
        "chapter_span": chapter_span.model_dump(mode="json") if chapter_span else None,
    }


def _claim_adjudication_projection(claim: CoherenceClaim) -> dict[str, Any]:
    """Keep semantic judgement fields while excluding retrieval/adjudication history metadata."""

    return {
        "claim_id": claim.claim_id,
        "artifact": claim.artifact,
        "source_path": claim.source_path,
        "source_field": claim.source_field,
        "chapter_numbers": list(claim.chapter_numbers),
        "chapter_range": (
            claim.chapter_range.model_dump(mode="json") if claim.chapter_range else None
        ),
        "subject_ids": list(claim.subject_ids),
        "subject_text": claim.subject_text,
        "axis": claim.axis,
        "claim_type": claim.claim_type,
        "claim_text": claim.claim_text,
        "state_before": claim.state_before,
        "state_after": claim.state_after,
        "event_type": claim.event_type,
        "payoff_id": claim.payoff_id,
        "payoff_kind": claim.payoff_kind,
        "irreversible": claim.irreversible,
        "temporality": claim.temporality,
        "evidence": claim.evidence,
        "cognitive_subjects": list(claim.cognitive_subjects),
        "cognitive_object": claim.cognitive_object,
        "cognitive_level": claim.cognitive_level,
        "action_level": claim.action_level,
        "reader_awareness": claim.reader_awareness,
        "character_knowledge_coverage": dict(claim.character_knowledge_coverage),
        "cognitive_chapter": claim.cognitive_chapter,
        "public_reveal_chapter": claim.public_reveal_chapter,
        "foreshadow_chapters": list(claim.foreshadow_chapters),
        "reveal_chapter": claim.reveal_chapter,
        "confidence": claim.confidence,
    }


def _batch_claim_catalog(batch: list[ConflictCandidate]) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for candidate in batch:
        for claim in candidate.claims:
            catalog.setdefault(claim.claim_id, _claim_adjudication_projection(claim))
    missing = {
        claim_id
        for candidate in batch
        for claim_id in candidate.claim_ids
        if claim_id not in catalog
    }
    if missing:
        raise ValueError(
            "conflict candidate references missing claims: " + ",".join(sorted(missing))
        )
    return catalog


def _retrieve_exact_candidates(
    claims: list[CoherenceClaim],
    *,
    max_candidates: int,
) -> list[ConflictCandidate]:
    return _retrieve_exact_candidates_impl(
        claims,
        max_candidates=max_candidates,
        deps=_exact_retrieval_deps(),
    )


async def _retrieve_semantic_candidates(
    ctx: Any,
    claims: list[CoherenceClaim],
    *,
    max_candidates: int,
) -> tuple[list[ConflictCandidate], dict[str, Any]]:
    memory = _build_init_coherence_memory(ctx)
    claim_by_id = {claim.claim_id: claim for claim in claims}
    for claim in claims:
        await memory.index_outline_phase(
            chapter_number=max(1, _claim_start_chapter(claim)),
            plot_points=[_build_semantic_query(claim)],
            characters=claim.subject_ids,
            pov_character="",
            chapter_goal=claim.subject_text or claim.axis,
            themes=[claim.axis] if claim.axis else [],
            metadata=_claim_index_metadata(claim),
        )

    candidates: list[ConflictCandidate] = []
    seen_pairs: set[tuple[str, str]] = set()
    top_k = _setting_int(ctx.settings, "init_coherence_semantic_top_k", 12)
    min_relevance = max(
        0.25,
        min(0.9, float(getattr(ctx.settings, "init_coherence_confidence_threshold", 0.75)) - 0.2),
    )
    total_queries = 0
    pushdown_queries = 0
    semantic_pushdown_hits = 0
    semantic_total_pairs = 0
    post_filter_drop_count = 0
    memory_filters_used: set[str] = set()
    metadata_retries = 0
    for claim in claims:
        if len(candidates) >= max_candidates:
            break
        query = _build_semantic_query(claim)
        query_filters = _build_semantic_metadata_filters(claim)
        if not query_filters:
            query_filters = [(None, "unfiltered")]
        matched = False
        chapter_range = _claim_chapter_bounds(claim)
        for metadata_filter, filter_name in query_filters:
            if len(candidates) >= max_candidates:
                break
            total_queries += 1
            if filter_name.startswith("pushdown:"):
                pushdown_queries += 1
            try:
                matches = await memory.search_similar_plot_points(
                    query,
                    chapter_range=chapter_range,
                    metadata_filter=metadata_filter,
                    top_k=top_k,
                )
            except Exception:
                metadata_retries += 1
                metadata_filter = None
                matches = await memory.search_similar_plot_points(
                    query,
                    chapter_range=chapter_range,
                    top_k=top_k,
                )
            if filter_name:
                memory_filters_used.add(filter_name)
            for match, score in matches:
                if len(candidates) >= max_candidates:
                    break
                if score < min_relevance:
                    continue
                other_id = _claim_id_from_indexed_text(match.plot_point)
                other = claim_by_id.get(other_id)
                if other is None or other.claim_id == claim.claim_id:
                    continue
                pair_key = _claim_pair_key(claim, other)
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                matched = True
                semantic_total_pairs += 1
                if filter_name.startswith("pushdown:"):
                    semantic_pushdown_hits += 1
                claim_items = sorted([claim, other], key=_claim_sort_key)
                retrieval_source = "memory:episodic"
                if filter_name.startswith("pushdown:"):
                    retrieval_source = "memory:episodic:pushdown"
                candidates.append(
                    ConflictCandidate(
                        candidate_id=f"sem_{len(candidates) + 1:04d}",
                        candidate_type="semantic_similarity",
                        reason="记忆语义召回发现两个叙事事实高度相似，需判断是否重复或互斥。",
                        claim_ids=[item.claim_id for item in claim_items],
                        claims=claim_items,
                        retrieval_sources=[f"{retrieval_source}:{score:.3f}"],
                        severity_hint="medium",
                        confidence=min(float(score), claim.confidence, other.confidence),
                        chapter_span=_chapter_span_for_claims(claim_items),
                    )
                )
            if matched:
                break
        if matched:
            continue

        # Fallback keeps recall for cross-topic collisions when strict filters are too strict.
        fallback_matches = await memory.search_similar_plot_points(
            query,
            chapter_range=chapter_range,
            top_k=top_k,
        )
        for match, score in fallback_matches:
            if len(candidates) >= max_candidates:
                break
            if score < min_relevance:
                continue
            other_id = _claim_id_from_indexed_text(match.plot_point)
            other = claim_by_id.get(other_id)
            if other is None or other.claim_id == claim.claim_id:
                continue
            pair_key = _claim_pair_key(claim, other)
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            semantic_total_pairs += 1
            claim_items = sorted([claim, other], key=_claim_sort_key)
            candidates.append(
                ConflictCandidate(
                    candidate_id=f"sem_{len(candidates) + 1:04d}",
                    candidate_type="semantic_similarity",
                    reason="记忆语义召回发现两个叙事事实高度相似，需判断是否重复或互斥。",
                    claim_ids=[item.claim_id for item in claim_items],
                    claims=claim_items,
                    retrieval_sources=[f"memory:episodic:fallback:{score:.3f}"],
                    severity_hint="medium",
                    confidence=min(float(score), claim.confidence, other.confidence),
                    chapter_span=_chapter_span_for_claims(claim_items),
                )
            )
        if len(candidates) >= max_candidates:
            break

    semantic_pushdown_ratio = (
        float(semantic_pushdown_hits) / float(total_queries) if total_queries else 0.0
    )
    vector_index = {
        "degraded_memory": False,
        "zvec_backend": memory.vector_store_backend,
        "vector_backend": memory.vector_store_backend,
        "vector_store_path": memory.vector_store_path,
        "memory_outline_data": memory.serialize_outline_data(),
        "exact_candidate_count": 0,
        "semantic_candidate_count": len(candidates),
        "post_filter_drop_count": int(post_filter_drop_count),
        "fully_pushed_down_ratio": semantic_pushdown_ratio,
        "semantic_pushdown_ratio": semantic_pushdown_ratio,
        "semantic_filters_used": sorted(memory_filters_used),
        "semantic_metadata_retries": int(metadata_retries),
        "semantic_candidate_pairs_filtered": int(semantic_total_pairs),
        "semantic_pushdown_queries": int(pushdown_queries),
        "semantic_total_queries": int(total_queries),
        "semantic_skipped_reason": (
            "无可检索 claims"
            if not claims
            else ("未命中语义候选，或命中后被认知后过滤移除" if not candidates else "")
        ),
    }
    return candidates, vector_index


def _build_semantic_metadata_filters(
    claim: CoherenceClaim,
) -> list[tuple[dict[str, Any] | None, str]]:
    metadata = _claim_index_metadata(claim)
    if not metadata:
        return [(None, "unfiltered")]

    # 目前不下推 chapter 维度，避免后续范围语义与精确章节窗口语义混淆；如有需要请先补齐范围匹配策略。
    base_filter: dict[str, Any] = {}
    for key in (
        "claim_type_code",
        "irreversible_code",
        "temporality_code",
    ):
        value = metadata.get(key)
        if isinstance(value, int) and value >= 0:
            base_filter[key] = int(value)
    if not base_filter:
        base_filter = {}

    subject_hash_fields = [
        (key, int(value))
        for key, value in metadata.items()
        if str(key).startswith("subject_hash_") and isinstance(value, int) and value
    ]

    filters: list[tuple[dict[str, Any] | None, str]] = []
    for key, value in subject_hash_fields[:3]:
        pushdown_filter = dict(base_filter)
        pushdown_filter[key] = value
        filters.append((pushdown_filter, f"pushdown:subject:{key}"))

    if base_filter:
        filters.append((base_filter, "filtered:core"))
    if not filters:
        filters.append((None, "unfiltered"))

    return filters


def _claim_chapter_bounds(claim: CoherenceClaim) -> tuple[int, int] | None:
    start, end = _claim_span(claim)
    if start and end:
        return start, end
    chapter_numbers = list(claim.chapter_numbers)
    if chapter_numbers:
        return min(chapter_numbers), max(chapter_numbers)
    return None


def _cognitive_subject_overlap(left: CoherenceClaim, right: CoherenceClaim) -> bool:
    return _cognitive_subject_overlap_impl(left, right)


def _claims_have_cognitive_regression(a: CoherenceClaim, b: CoherenceClaim) -> bool:
    return _claims_have_cognitive_regression_impl(a, b, deps=_cognitive_filter_deps())


def _cognitive_group_keys(claim: CoherenceClaim) -> list[tuple[str, str]]:
    return _cognitive_group_keys_impl(claim, deps=_cognitive_filter_deps())


def _claim_cognitive_object_key(claim: CoherenceClaim) -> str:
    return _claim_cognitive_object_key_impl(claim, deps=_cognitive_filter_deps())


def _cognitive_candidate_kinds(
    a: CoherenceClaim,
    b: CoherenceClaim,
) -> list[tuple[str, str, str]]:
    return _cognitive_candidate_kinds_impl(a, b, deps=_cognitive_filter_deps())


def _claims_have_premature_public_reveal(a: CoherenceClaim, b: CoherenceClaim) -> bool:
    return _claims_have_premature_public_reveal_impl(a, b, deps=_cognitive_filter_deps())


def _claims_have_awareness_conflict(a: CoherenceClaim, b: CoherenceClaim) -> bool:
    return _claims_have_awareness_conflict_impl(a, b, deps=_cognitive_filter_deps())


def _claim_has_foreshadow_after_reveal(claim: CoherenceClaim) -> bool:
    return _claim_has_foreshadow_after_reveal_impl(claim)


def _claims_have_action_level_conflict(a: CoherenceClaim, b: CoherenceClaim) -> bool:
    return _claims_have_action_level_conflict_impl(a, b, deps=_cognitive_filter_deps())


def _internal_and_public_claim(
    a: CoherenceClaim,
    b: CoherenceClaim,
) -> tuple[CoherenceClaim | None, CoherenceClaim | None]:
    return _internal_and_public_claim_impl(a, b)


def _claim_effective_cognitive_chapter(claim: CoherenceClaim) -> int:
    return _claim_effective_cognitive_chapter_impl(claim, deps=_cognitive_filter_deps())


def _build_init_coherence_memory(ctx: Any) -> EpisodicMemory:
    vector_store_backend = (
        str(getattr(ctx.settings, "memory_vector_store_backend", "zvec") or "zvec")
        .strip()
        .lower()
        .replace("-", "_")
    )
    if getattr(ctx.settings, "memory_use_mock_embeddings", False):
        vector_store_backend = "in_memory"
    vector_store_kwargs: dict[str, Any] = {
        "vector_store_backend": vector_store_backend,
        "vector_store_path": ctx.layout.memory_dir / "zvec_init_coherence_vectors",
        "zvec_index_type": getattr(ctx.settings, "memory_zvec_index_type", "hnsw"),
        "zvec_memory_limit_mb": int(
            getattr(ctx.settings, "memory_zvec_memory_limit_mb", 512) or 512
        ),
        "extra_int_fields": _COGNITIVE_INDEX_INT_FIELDS,
    }
    if getattr(ctx.settings, "memory_use_mock_embeddings", False):
        return EpisodicMemory(use_mock_embeddings=True, **vector_store_kwargs)
    embedding_profile_id = getattr(ctx.settings, "memory_embedding_profile_id", None)
    embedding_config = _get_embedding_config(embedding_profile_id)
    if embedding_config is None:
        embedding_config = {
            "provider": "ollama",
            "model": getattr(ctx.settings, "ollama_embedding_model", "nomic-embed-text"),
            "base_url": getattr(ctx.settings, "ollama_base_url", "http://localhost:11434/v1"),
        }
    return EpisodicMemory(
        embedding_config=embedding_config,
        use_mock_embeddings=False,
        **vector_store_kwargs,
    )


def _build_artifact_chunks(
    settings: Any,
    artifacts: dict[str, dict[str, Any]],
    *,
    focus_chapters: list[int] | None = None,
) -> list[InitArtifactChunk]:
    chunks: list[InitArtifactChunk] = []
    for artifact, payload in artifacts.items():
        artifact_key = str(artifact)
        if artifact_key == "outline":
            chunks.extend(_build_chapter_list_chunks(settings, artifact_key, payload, "chapters"))
        elif artifact_key == "chapter_contracts":
            chunks.extend(
                _build_chapter_list_chunks(settings, artifact_key, payload, "chapter_contracts")
            )
        else:
            chunks.extend(_build_generic_artifact_chunks(settings, artifact_key, payload))
    if not focus_chapters:
        return _adapt_claim_chunks_to_budget(settings, chunks)
    filtered = [chunk for chunk in chunks if _chunk_intersects_focus(chunk, focus_chapters)]
    return _adapt_claim_chunks_to_budget(settings, filtered or chunks)


def _claim_chunk_size_for_artifact(settings: Any, artifact: str) -> int:
    """Return configured claim-extraction chunk size for an artifact."""
    artifact_key = str(artifact or "").strip()
    configured = _setting_int(settings, "init_coherence_claim_batch_size", 8) or 8
    if artifact_key in {"outline", "chapter_contracts"}:
        return max(1, configured)
    return max(1, min(_GENERIC_CLAIM_BATCH_CAP, configured))


def _claim_context_overlap_for_artifact(settings: Any, artifact: str) -> int:
    """Return the neighboring chapter context window for claim extraction."""
    artifact_key = str(artifact or "").strip()
    configured = _setting_int(settings, "init_coherence_overlap_chapters", 2)
    if artifact_key in {"outline", "chapter_contracts"}:
        return max(0, configured)
    return 0


def _claim_payload_char_budget(settings: Any) -> int:
    configured = _setting_int(
        settings,
        "init_coherence_claim_payload_char_budget",
        _CLAIM_PAYLOAD_CHAR_BUDGET,
    )
    return max(_CLAIM_PAYLOAD_CHAR_BUDGET_MIN, configured or _CLAIM_PAYLOAD_CHAR_BUDGET)


def _claim_chunk_payload_chars(chunk: InitArtifactChunk) -> int:
    try:
        return len(json.dumps(chunk.payload, ensure_ascii=False, sort_keys=True, default=str))
    except TypeError:
        return len(str(chunk.payload))


def _adapt_claim_chunks_to_budget(
    settings: Any,
    chunks: list[InitArtifactChunk],
) -> list[InitArtifactChunk]:
    budget = _claim_payload_char_budget(settings)
    adapted: list[InitArtifactChunk] = []
    for chunk in chunks:
        adapted.extend(_split_claim_chunk_to_budget(chunk, budget=budget))
    return adapted


def _split_claim_chunk_to_budget(
    chunk: InitArtifactChunk,
    *,
    budget: int,
) -> list[InitArtifactChunk]:
    if _claim_chunk_payload_chars(chunk) <= budget:
        return [chunk]

    split_chunks = _split_claim_chunk_current(chunk, budget=budget)
    if not split_chunks:
        split_chunks = _split_claim_chunk_items(chunk, budget=budget)
    if split_chunks:
        adapted: list[InitArtifactChunk] = []
        for split_chunk in split_chunks:
            adapted.extend(_split_claim_chunk_to_budget(split_chunk, budget=budget))
        return adapted

    context_trimmed = _claim_chunk_with_trimmed_context(chunk)
    if context_trimmed is not None:
        return _split_claim_chunk_to_budget(context_trimmed, budget=budget)
    return [chunk]


def _claim_chunk_with_trimmed_context(chunk: InitArtifactChunk) -> InitArtifactChunk | None:
    payload = dict(chunk.payload)
    previous_context = payload.get("previous_context")
    next_context = payload.get("next_context")
    if not previous_context and not next_context:
        return None
    payload["previous_context"] = []
    payload["next_context"] = []
    payload["adaptive_context_trimmed"] = True
    return InitArtifactChunk(
        artifact=chunk.artifact,
        chunk_id=f"{chunk.chunk_id}_ctx0",
        source_path=chunk.source_path,
        source_field=chunk.source_field,
        chapter_numbers=list(chunk.chapter_numbers),
        payload=payload,
        extraction_mode=chunk.extraction_mode,
        evidence_refs=list(chunk.evidence_refs),
    )


def _split_claim_chunk_current(
    chunk: InitArtifactChunk,
    *,
    budget: int,
) -> list[InitArtifactChunk]:
    current = chunk.payload.get("current")
    if not isinstance(current, list) or len(current) <= 1:
        return []
    midpoint = max(1, len(current) // 2)
    ranges = ((0, midpoint), (midpoint, len(current)))
    split_chunks: list[InitArtifactChunk] = []
    for index, (start, end) in enumerate(ranges, start=1):
        part = [item for item in current[start:end] if isinstance(item, dict)]
        if not part:
            continue
        numbers = _chapter_numbers_from_items(part)
        payload = dict(chunk.payload)
        payload["current"] = part
        payload["previous_context"] = (
            chunk.payload.get("previous_context", [])
            if start == 0
            else [item for item in current[max(0, start - 1) : start] if isinstance(item, dict)]
        )
        payload["next_context"] = (
            [item for item in current[end : end + 1] if isinstance(item, dict)]
            if end < len(current)
            else chunk.payload.get("next_context", [])
        )
        payload["adaptive_split"] = {
            "source_chunk_id": chunk.chunk_id,
            "source_payload_chars": _claim_chunk_payload_chars(chunk),
            "payload_char_budget": budget,
            "part": index,
            "parts": len(ranges),
        }
        suffix = f"part{index}"
        if numbers:
            suffix = f"{suffix}_{min(numbers)}_{max(numbers)}"
        split_chunks.append(
            InitArtifactChunk(
                artifact=chunk.artifact,
                chunk_id=f"{chunk.chunk_id}_{suffix}",
                source_path=f"{chunk.source_path}#{suffix}",
                source_field=chunk.source_field,
                chapter_numbers=numbers,
                payload=payload,
                extraction_mode=chunk.extraction_mode,
                evidence_refs=_evidence_refs_for_split(chunk, numbers),
            )
        )
    return split_chunks


def _split_claim_chunk_items(
    chunk: InitArtifactChunk,
    *,
    budget: int,
) -> list[InitArtifactChunk]:
    items = chunk.payload.get("items")
    if not isinstance(items, list) or len(items) <= 1:
        return []
    midpoint = max(1, len(items) // 2)
    ranges = ((0, midpoint), (midpoint, len(items)))
    split_chunks: list[InitArtifactChunk] = []
    for index, (start, end) in enumerate(ranges, start=1):
        part = items[start:end]
        if not part:
            continue
        numbers = _chapter_numbers_from_items(part)
        payload = dict(chunk.payload)
        payload["items"] = part
        payload["adaptive_split"] = {
            "source_chunk_id": chunk.chunk_id,
            "source_payload_chars": _claim_chunk_payload_chars(chunk),
            "payload_char_budget": budget,
            "part": index,
            "parts": len(ranges),
        }
        suffix = f"part{index}"
        split_chunks.append(
            InitArtifactChunk(
                artifact=chunk.artifact,
                chunk_id=f"{chunk.chunk_id}_{suffix}",
                source_path=f"{chunk.source_path}#{suffix}",
                source_field=chunk.source_field,
                chapter_numbers=numbers,
                payload=payload,
                extraction_mode=chunk.extraction_mode,
                evidence_refs=list(chunk.evidence_refs),
            )
        )
    return split_chunks


def _evidence_refs_for_split(chunk: InitArtifactChunk, chapter_numbers: list[int]) -> list[str]:
    if chunk.evidence_refs and chapter_numbers:
        wanted = {f"/chapters/{number - 1}" for number in chapter_numbers}
        refs = [ref for ref in chunk.evidence_refs if ref in wanted]
        if refs:
            return refs
    return list(chunk.evidence_refs)


def _claim_limit_for_chunk(task_type: TaskType, _chunk: InitArtifactChunk) -> int:
    """Return the prompt-visible maximum number of claims for one extraction call."""
    if task_type == TaskType.EXTRACT_BLUEPRINT_HOLISTIC_CLAIMS:
        return _BLUEPRINT_HOLISTIC_CLAIM_LIMIT
    return _DEFAULT_CLAIM_LIMIT


def _claim_extraction_target_output_chars(
    settings: Any,
    task_type: TaskType,
    claim_limit: int,
) -> int:
    """Estimate claim extraction output by requested claim count, not a fixed low cap."""
    per_claim_chars = 1600
    claim_target = max(1, int(claim_limit or 1)) * per_claim_chars
    if task_type == TaskType.EXTRACT_BLUEPRINT_HOLISTIC_CLAIMS:
        configured = int(
            getattr(settings, "init_blueprint_holistic_claim_max_tokens", 4096) or 4096
        )
        return max(configured, claim_target)
    return max(3600, claim_target)


def _build_blueprint_holistic_chunks(
    settings: Any,
    blueprint_payload: dict[str, Any] | None,
) -> list[InitArtifactChunk]:
    if not isinstance(blueprint_payload, dict):
        return []
    return [
        InitArtifactChunk(
            artifact="blueprint",
            chunk_id="blueprint_holistic",
            source_path="/",
            source_field="blueprint",
            chapter_numbers=_chapter_numbers_from_items([blueprint_payload]),
            payload={
                "extraction_scope": (
                    "完整蓝图 holistic pass：只抽取跨字段 claims；"
                    "字段内部细节由 sharded pass 覆盖。"
                ),
                "full_artifact": blueprint_payload,
            },
            extraction_mode="holistic",
            evidence_refs=[
                "/synopsis",
                "/volumes",
                "/narrative_phases",
                "/key_turning_points",
                "/character_arcs",
                "/subplot_plan",
                "/suspense_schedule",
                "/ending_strategy",
            ],
        )
    ]


def _build_chapter_list_chunks(
    settings: Any,
    artifact: str,
    payload: dict[str, Any],
    list_key: str,
) -> list[InitArtifactChunk]:
    items = payload.get(list_key)
    if not isinstance(items, list):
        items = []
    batch_size = _claim_chunk_size_for_artifact(settings, artifact)
    overlap = _claim_context_overlap_for_artifact(settings, artifact)
    chunks: list[InitArtifactChunk] = []
    for start in range(0, len(items), batch_size):
        end = min(len(items), start + batch_size)
        current = [item for item in items[start:end] if isinstance(item, dict)]
        previous_context = [
            item for item in items[max(0, start - overlap) : start] if isinstance(item, dict)
        ]
        next_context = [
            item for item in items[end : min(len(items), end + overlap)] if isinstance(item, dict)
        ]
        chapter_numbers = _chapter_numbers_from_items(current)
        source_path = f"/{list_key}/{start}:{end}"
        chunks.append(
            InitArtifactChunk(
                artifact=artifact,
                chunk_id=f"{artifact}_{start + 1}_{end}",
                source_path=source_path,
                source_field=list_key,
                chapter_numbers=chapter_numbers,
                payload={
                    "extraction_scope": "只抽取 current 中的 claims；previous/next 只作上下文。",
                    "current": current,
                    "previous_context": previous_context,
                    "next_context": next_context,
                },
            )
        )
    return chunks


def build_outline_stream_claim_chunks(
    settings: Any,
    *,
    chapters: list[Any],
) -> list[InitArtifactChunk]:
    """Build stable claim-extraction chunks as outline batches become available."""
    normalized: list[dict[str, Any]] = []
    for chapter in sorted(chapters, key=_stream_chapter_number):
        if hasattr(chapter, "model_dump"):
            item = chapter.model_dump(mode="json")
        elif isinstance(chapter, dict):
            item = dict(chapter)
        else:
            continue
        if _coerce_positive_int(item.get("chapter_number")):
            normalized.append(item)
    if not normalized:
        return []

    batch_size = _claim_chunk_size_for_artifact(settings, "outline")
    chunks: list[InitArtifactChunk] = []
    for start in range(0, len(normalized), batch_size):
        current = normalized[start : start + batch_size]
        chapter_numbers = _chapter_numbers_from_items(current)
        if not chapter_numbers:
            continue
        first_chapter = min(chapter_numbers)
        last_chapter = max(chapter_numbers)
        chunks.append(
            InitArtifactChunk(
                artifact="outline",
                chunk_id=f"outline_stream_{first_chapter}_{last_chapter}",
                source_path=f"/chapters/{first_chapter - 1}:{last_chapter}",
                source_field="chapters",
                chapter_numbers=chapter_numbers,
                payload={
                    "extraction_scope": (
                        "流式预取：只抽取 current 中已生成章节的 claims；"
                        "后续完整裁判会结合蓝图和全部 active claims 复查。"
                    ),
                    "current": current,
                    "previous_context": [],
                    "next_context": [],
                },
                extraction_mode="stream",
                evidence_refs=[f"/chapters/{number - 1}" for number in chapter_numbers],
            )
        )
    return _adapt_claim_chunks_to_budget(settings, chunks)


def _stream_chapter_number(item: Any) -> int:
    if isinstance(item, dict):
        return _coerce_positive_int(item.get("chapter_number"))
    return _coerce_positive_int(getattr(item, "chapter_number", None))


async def prefetch_init_coherence_claim_chunks(
    ctx: Any,
    *,
    stage: str,
    profile: dict[str, Any],
    chunks: list[InitArtifactChunk],
    concurrency_limiter: asyncio.Semaphore | None = None,
) -> list[CoherenceClaim]:
    """Extract and ledger claims for a stage before the full artifact is complete."""
    if not chunks:
        return []
    claims = await _extract_claims(
        ctx,
        stage=stage,
        profile=profile,
        chunks=chunks,
        concurrency_limiter=concurrency_limiter,
    )
    claims = await adjudicate_init_claim_entity_references(
        ctx,
        profile=profile,
        claims=claims,
        stage=stage,
    )
    async with _stream_claim_ledger_lock(ctx):
        _persist_stream_claims(ctx, claims, stage=stage, chunks=chunks)
    return claims


def _stream_claim_ledger_lock(ctx: Any) -> asyncio.Lock:
    lock = getattr(ctx, "_init_coherence_stream_ledger_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        try:
            ctx._init_coherence_stream_ledger_lock = lock
        except Exception:
            return lock
    return lock


def _build_generic_artifact_chunks(
    settings: Any,
    artifact: str,
    payload: dict[str, Any],
) -> list[InitArtifactChunk]:
    # Generic blueprint lists can contain deeply nested arc and subplot entries.
    # Keep them smaller than chapter-list artifacts so claim extraction returns
    # complete JSON instead of hitting output-token truncation.
    batch_size = _claim_chunk_size_for_artifact(settings, artifact)
    chunks: list[InitArtifactChunk] = []
    for key, value in payload.items():
        if key in {"schema_version", "created_at"}:
            continue
        if isinstance(value, list):
            for start in range(0, len(value), batch_size):
                end = min(len(value), start + batch_size)
                items = value[start:end]
                chunks.append(
                    InitArtifactChunk(
                        artifact=artifact,
                        chunk_id=f"{artifact}_{key}_{start + 1}_{end}",
                        source_path=f"/{key}/{start}:{end}",
                        source_field=key,
                        chapter_numbers=_chapter_numbers_from_items(items),
                        payload={"field": key, "items": items},
                    )
                )
        else:
            chunks.append(
                InitArtifactChunk(
                    artifact=artifact,
                    chunk_id=f"{artifact}_{key}",
                    source_path=f"/{key}",
                    source_field=key,
                    chapter_numbers=_chapter_numbers_from_items([value]),
                    payload={"field": key, "value": value},
                )
            )
    return chunks


def _normalize_claim_payload(
    raw: dict[str, Any],
    *,
    chunk: InitArtifactChunk,
    claim_index: int,
    entity_catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(raw)
    raw_artifact = str(payload.get("artifact") or "").strip()
    raw_source_path = str(payload.get("source_path") or "").strip()
    raw_source_field = str(payload.get("source_field") or "").strip()
    payload["artifact"] = chunk.artifact
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    payload["source_path"] = _normalize_claim_source_path(
        raw_source_path,
        chunk=chunk,
        metadata=metadata,
    )
    payload["source_field"] = _normalize_claim_source_field(
        raw_source_field,
        source_path=payload["source_path"],
        chunk=chunk,
        metadata=metadata,
    )
    payload["claim_id"] = _normalize_claim_id(
        payload.get("claim_id"),
        chunk=chunk,
        claim_index=claim_index,
    )
    if not payload.get("chapter_numbers") and chunk.chapter_numbers:
        payload["chapter_numbers"] = list(chunk.chapter_numbers)
    if not payload.get("chapter_range") and chunk.chapter_numbers:
        payload["chapter_range"] = {
            "start": min(chunk.chapter_numbers),
            "end": max(chunk.chapter_numbers),
        }
    text = str(
        payload.get("claim_text")
        or payload.get("description")
        or payload.get("summary")
        or payload.get("evidence")
        or ""
    ).strip()
    if not text:
        text = json.dumps(chunk.payload, ensure_ascii=False, default=str)[:240]
    payload["claim_text"] = text
    payload["evidence"] = str(payload.get("evidence") or text).strip()
    if raw_artifact and raw_artifact != chunk.artifact:
        metadata.setdefault("raw_artifact", raw_artifact)
        metadata.setdefault("artifact_corrected_from_chunk", True)
    metadata.setdefault("extraction_mode", chunk.extraction_mode)
    metadata.setdefault("evidence_refs", list(chunk.evidence_refs))
    payload["metadata"] = metadata
    payload = _coerce_claim_string_fields(payload, entity_catalog=entity_catalog)
    return payload


def _entity_catalog_from_profile(profile: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(profile, dict):
        return None
    catalog = profile.get("entity_catalog")
    return catalog if isinstance(catalog, dict) else None


def _normalize_claim_id(
    raw_claim_id: Any,
    *,
    chunk: InitArtifactChunk,
    claim_index: int,
) -> str:
    raw = str(raw_claim_id or "").strip()
    if not raw:
        raw = f"claim_{claim_index:03d}"
    safe_raw = _SAFE_ID_RE.sub("_", raw).strip("._-") or f"claim_{claim_index:03d}"
    safe_chunk = _SAFE_ID_RE.sub("_", str(chunk.chunk_id or "")).strip("._-") or chunk.artifact
    if safe_raw == safe_chunk or safe_raw.startswith(f"{safe_chunk}_"):
        return safe_raw
    return f"{safe_chunk}_{safe_raw}"


def _normalize_claim_source_path(
    raw_source_path: str,
    *,
    chunk: InitArtifactChunk,
    metadata: dict[str, Any],
) -> str:
    source_path = raw_source_path if raw_source_path.startswith("/") else ""
    if not source_path:
        return chunk.source_path
    if _source_path_within_chunk(source_path, chunk.source_path):
        return source_path
    metadata.setdefault("raw_source_path", raw_source_path)
    metadata.setdefault("source_path_corrected_from_chunk", True)
    return chunk.source_path


def _normalize_claim_source_field(
    raw_source_field: str,
    *,
    source_path: str,
    chunk: InitArtifactChunk,
    metadata: dict[str, Any],
) -> str:
    raw = raw_source_field.strip()
    if not raw:
        return chunk.source_field
    source_root = _source_path_root(source_path)
    chunk_root = _source_path_root(chunk.source_path)
    allowed_roots = {item for item in {source_root, chunk_root, chunk.source_field} if item}
    if raw in allowed_roots or source_path.endswith(f"/{raw}") or f"/{raw}/" in source_path:
        return raw
    if chunk.source_field in _REPAIR_FIELDS_BY_ARTIFACT.get(chunk.artifact, []):
        allowed_roots.add(chunk.source_field)
    if raw in _REPAIR_FIELDS_BY_ARTIFACT.get(chunk.artifact, []):
        return raw
    metadata.setdefault("raw_source_field", raw_source_field)
    metadata.setdefault("source_field_corrected_from_chunk", True)
    return source_root or chunk.source_field


def _source_path_within_chunk(source_path: str, chunk_source_path: str) -> bool:
    if chunk_source_path == "/":
        return source_path.startswith("/")
    window = _WINDOW_SOURCE_PATH_RE.match(chunk_source_path)
    if window:
        field = window.group("field")
        if not source_path.startswith(f"/{field}/"):
            return False
        next_part = source_path[len(field) + 2 :].split("/", 1)[0]
        try:
            index = int(next_part)
        except ValueError:
            return source_path == f"/{field}" or source_path.startswith(f"/{field}/")
        start = int(window.group("start"))
        end = int(window.group("end"))
        return start <= index < end
    return source_path == chunk_source_path or source_path.startswith(f"{chunk_source_path}/")


def _source_path_root(source_path: str) -> str:
    match = _SOURCE_PATH_ROOT_RE.match(str(source_path or ""))
    return match.group("field") if match else ""


def _coerce_claim_string_fields(
    payload: dict[str, Any],
    *,
    entity_catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Coerce optional claim string fields from legacy/cache payloads.

    Older cached claim batches and some model outputs can contain ``None`` for
    optional string fields. Pydantic defaults are only used when a field is
    missing, so normalize those values before validation.
    """
    del entity_catalog  # identity normalization is owned by LLM adjudication
    for key in (
        "source_field",
        "subject_text",
        "axis",
        "state_before",
        "state_after",
        "event_type",
        "payoff_id",
        "payoff_kind",
        "cognitive_object",
        "claim_type",
        "temporality",
        "cognitive_level",
        "action_level",
        "reader_awareness",
    ):
        if key in payload:
            payload[key] = str(payload.get(key) or "").strip()
    subject_ids = payload.get("subject_ids")
    if subject_ids is None:
        payload["subject_ids"] = []
    elif not isinstance(subject_ids, list):
        payload["subject_ids"] = [str(subject_ids).strip()] if str(subject_ids).strip() else []
    else:
        payload["subject_ids"] = [
            str(item).strip() for item in subject_ids if str(item or "").strip()
        ]
    if "claim_type" in payload:
        payload["claim_type"] = str(payload.get("claim_type") or "").strip().lower()
    if "temporality" in payload:
        payload["temporality"] = str(payload.get("temporality") or "").strip().lower()

    if "cognitive_subjects" in payload:
        cognitive_subjects = payload.get("cognitive_subjects")
        if isinstance(cognitive_subjects, list):
            payload["cognitive_subjects"] = [
                str(item).strip() for item in cognitive_subjects if str(item or "").strip()
            ]
        elif cognitive_subjects is None:
            payload["cognitive_subjects"] = []
        else:
            payload["cognitive_subjects"] = [str(cognitive_subjects).strip()]
    if "cognitive_level" in payload:
        payload["cognitive_level"] = str(payload.get("cognitive_level") or "").strip().lower()
    if "action_level" in payload:
        payload["action_level"] = str(payload.get("action_level") or "").strip().lower()
    if "reader_awareness" in payload:
        payload["reader_awareness"] = str(payload.get("reader_awareness") or "").strip().lower()

    for chapter_key in ("cognitive_chapter", "public_reveal_chapter", "reveal_chapter"):
        if chapter_key in payload and payload.get(chapter_key) is not None:
            raw_chapter = payload.get(chapter_key)
            if isinstance(raw_chapter, str) and not raw_chapter.strip():
                payload[chapter_key] = None
    if "foreshadow_chapters" in payload:
        foreshadow_chapters = payload.get("foreshadow_chapters")
        if isinstance(foreshadow_chapters, list):
            payload["foreshadow_chapters"] = sorted(
                {n for n in (_coerce_positive_int(v) for v in foreshadow_chapters) if n}
            )
        else:
            chapter = _coerce_positive_int(foreshadow_chapters)
            payload["foreshadow_chapters"] = [chapter] if chapter else []
    if "character_knowledge_coverage" in payload:
        raw_character_knowledge_coverage = payload.get("character_knowledge_coverage")
        if isinstance(raw_character_knowledge_coverage, dict):
            normalized: dict[str, str] = {}
            for key, value in raw_character_knowledge_coverage.items():
                name = str(key or "").strip()
                if not name:
                    continue
                normalized[name] = str(value or "").strip().lower()
            payload["character_knowledge_coverage"] = normalized
        else:
            payload["character_knowledge_coverage"] = {}

    return payload


def _postprocess_claim_batch(
    claims: list[CoherenceClaim],
    *,
    chunk: InitArtifactChunk,
) -> tuple[list[CoherenceClaim], int, int]:
    """Drop obvious model loops and synthesize outline anchors when a shard is empty."""
    usable_claims = [
        claim for claim in claims if not _claim_is_low_signal(claim, artifact=chunk.artifact)
    ]
    filtered_count = len(claims) - len(usable_claims)
    if usable_claims or chunk.artifact != "outline":
        return usable_claims, filtered_count, 0
    fallback_claims = _build_outline_fallback_claims(chunk)
    return fallback_claims, filtered_count, len(fallback_claims)


def _claim_is_low_signal(claim: CoherenceClaim, *, artifact: str) -> bool:
    text = " ".join(str(claim.claim_text or "").split()).strip()
    if not text:
        return True
    if artifact == "outline" and _looks_like_narrative_excerpt(text):
        return True
    return _text_is_repetitive_loop(text)


def _looks_like_narrative_excerpt(text: str) -> bool:
    if len(text) > 220:
        return True
    dialogue_marks = sum(text.count(mark) for mark in ("「", "」", "“", "”", '"'))
    sentence_marks = sum(text.count(mark) for mark in ("。", "！", "？", "；", ";"))
    return (len(text) > 80 and dialogue_marks >= 2) or (len(text) > 120 and sentence_marks >= 3)


def _text_is_repetitive_loop(text: str) -> bool:
    compact = re.sub(r"[\s，,；;。.!！?？、：:「」“”\"'（）()《》]+", "", text)
    if len(compact) < 12:
        return False
    for unit_size in range(4, (len(compact) // 2) + 1):
        if len(compact) % unit_size:
            continue
        unit = compact[:unit_size]
        repeats = len(compact) // unit_size
        if repeats >= 2 and unit * repeats == compact:
            return True
    return False


def _build_outline_fallback_claims(chunk: InitArtifactChunk) -> list[CoherenceClaim]:
    chapters = _outline_chunk_current_chapters(chunk)
    claims: list[CoherenceClaim] = []
    for chapter in chapters:
        number = _coerce_positive_int(chapter.get("chapter_number"))
        if not number:
            continue
        title = str(chapter.get("title") or f"第{number}章").strip()
        characters = _coerce_string_list(chapter.get("involved_characters"))[:6]
        goal_text = _first_outline_text(
            chapter.get("goal"),
            chapter.get("main_plot_points"),
            chapter.get("beats_summary"),
            chapter.get("subplot_points"),
        )
        if goal_text:
            claims.append(
                _outline_fallback_claim(
                    chunk,
                    number=number,
                    suffix="goal",
                    source_field="goal",
                    source_path=f"/chapters/{number - 1}/goal",
                    claim_type="event",
                    axis="plot_progress",
                    text=goal_text,
                    title=title,
                    characters=characters,
                )
            )
        payoff_text, payoff_kind = _first_outline_payoff(chapter.get("expected_payoffs"))
        if payoff_text:
            claims.append(
                _outline_fallback_claim(
                    chunk,
                    number=number,
                    suffix="payoff",
                    source_field="expected_payoffs",
                    source_path=f"/chapters/{number - 1}/expected_payoffs",
                    claim_type="payoff",
                    axis="payoff",
                    text=payoff_text,
                    title=title,
                    characters=characters,
                    payoff_kind=payoff_kind,
                )
            )
    return _dedupe_claims(claims)


def _outline_chunk_current_chapters(chunk: InitArtifactChunk) -> list[dict[str, Any]]:
    payload = chunk.payload if isinstance(chunk.payload, dict) else {}
    current = payload.get("current")
    if isinstance(current, list):
        return [dict(item) for item in current if isinstance(item, dict)]
    if _coerce_positive_int(payload.get("chapter_number")):
        return [dict(payload)]
    return []


def _first_outline_text(*values: Any) -> str:
    for value in values:
        text = _first_text_value(value)
        if text:
            return _compact_claim_text(text)
    return ""


def _first_text_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        for item in value:
            text = _first_text_value(item)
            if text:
                return text
    if isinstance(value, dict):
        for key in ("description", "goal", "summary", "text", "claim", "event"):
            text = _first_text_value(value.get(key))
            if text:
                return text
    return ""


def _first_outline_payoff(value: Any) -> tuple[str, str]:
    items = value if isinstance(value, list) else [value]
    for item in items:
        if not isinstance(item, dict):
            text = _first_text_value(item)
            if text:
                return _compact_claim_text(text), ""
            continue
        text = _first_text_value(item.get("description") or item.get("summary") or item.get("text"))
        if text:
            return _compact_claim_text(text), str(item.get("payoff_type") or "").strip()
    return "", ""


def _compact_claim_text(value: Any, *, limit: int = 180) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip(" ，,；;。") + "…"


def _outline_fallback_claim(
    chunk: InitArtifactChunk,
    *,
    number: int,
    suffix: str,
    source_field: str,
    source_path: str,
    claim_type: ClaimType,
    axis: str,
    text: str,
    title: str,
    characters: list[str],
    payoff_kind: str = "",
) -> CoherenceClaim:
    return CoherenceClaim.model_validate(
        {
            "claim_id": f"{chunk.chunk_id}_ch{number:03d}_{suffix}",
            "artifact": chunk.artifact,
            "source_path": source_path,
            "source_field": source_field,
            "chapter_numbers": [number],
            "chapter_range": {"start": number, "end": number},
            "subject_ids": characters,
            "subject_text": title,
            "axis": axis,
            "claim_type": claim_type,
            "claim_text": text,
            "event_type": axis if claim_type == "event" else "",
            "payoff_kind": payoff_kind,
            "temporality": "planned",
            "cognitive_subjects": [],
            "cognitive_object": "",
            "cognitive_level": "unaware",
            "action_level": "none",
            "reader_awareness": "unknown",
            "character_knowledge_coverage": {},
            "cognitive_chapter": None,
            "public_reveal_chapter": None,
            "foreshadow_chapters": [],
            "evidence": text,
            "confidence": 0.55,
            "metadata": {
                "extraction_mode": chunk.extraction_mode,
                "evidence_refs": list(chunk.evidence_refs),
                "local_fallback": True,
                "fallback_reason": "empty_or_low_signal_outline_claims",
            },
        }
    )


def _normalize_candidate_adjudication_response(
    response: dict[str, Any],
    batch: list[ConflictCandidate],
    *,
    repair_artifact: str = "",
) -> dict[str, Any]:
    allowed_repair_artifacts = (
        {_normalize_artifact_key_for_v2(repair_artifact)} if repair_artifact else set()
    )
    candidate_by_id = {candidate.candidate_id: candidate for candidate in batch}
    raw_issues = response.get("issues")
    issues: list[Any] = raw_issues if isinstance(raw_issues, list) else []
    normalized_issues = _normalize_adjudication_issues(
        issues,
        candidate_by_id=candidate_by_id,
        allowed_repair_artifacts=allowed_repair_artifacts,
    )
    raw_repair_scope = response.get("repair_scope")
    repair_scope_items: list[Any] = raw_repair_scope if isinstance(raw_repair_scope, list) else []
    repair_scope = _sanitize_repair_scopes(
        repair_scope_items,
        artifact="",
        allowed_artifacts=allowed_repair_artifacts,
    )
    normalized_issues, repair_scope = _namespace_batch_issue_ids(
        normalized_issues,
        repair_scope,
        batch=batch,
    )
    repair_scope = _dedupe_repair_scopes(
        [
            *repair_scope,
            *[
                scope
                for issue in normalized_issues
                for scope in (
                    issue.get("repair_scope", [])
                    if isinstance(issue.get("repair_scope"), list)
                    else []
                )
                if isinstance(scope, dict)
            ],
        ]
    )
    verdict = str(response.get("verdict") or "ambiguous").strip().lower()
    blocked = bool(normalized_issues) and (
        bool(response.get("blocked", False)) or verdict in {"needs_repair", "reject"}
    )
    if not normalized_issues and verdict in {"needs_repair", "reject"}:
        # A conflict without a valid candidate/source anchor proves neither
        # cleanliness nor conflict.  Defer it instead of silently accepting it.
        verdict = "ambiguous"
    return {
        "schema_version": "audit_v2",
        "verdict": verdict,
        "issues": normalized_issues,
        "source_refs": response.get("source_refs")
        if isinstance(response.get("source_refs"), list)
        else [],
        "repair_scope": repair_scope,
        "preserve": response.get("preserve") if isinstance(response.get("preserve"), list) else [],
        "change_intent": str(response.get("change_intent") or ""),
        "blocked": blocked,
        "summary": str(response.get("summary") or ""),
        "candidate_ids": [candidate.candidate_id for candidate in batch],
    }


def _merge_adjudication_reports(
    *,
    stage: str,
    repair_artifact: str,
    batch_reports: list[dict[str, Any]],
    claims_count: int,
    candidate_count: int,
    degraded_memory: bool,
) -> dict[str, Any]:
    if not batch_reports:
        return InitConflictAdjudicationReport(
            schema_version="audit_v2",
            stage=stage,
            artifact=repair_artifact,
            claims_count=claims_count,
            candidate_count=candidate_count,
            degraded_memory=degraded_memory,
        ).model_dump(mode="json")

    verdict = "accept"
    verdict_rank = {"accept": 0, "defer": 1, "ambiguous": 2, "needs_repair": 3, "reject": 4}
    issues: list[dict[str, Any]] = []
    source_refs: list[Any] = []
    repair_scope: list[dict[str, Any]] = []
    preserve: list[Any] = []
    blocked = False
    change_intents: list[str] = []
    for report in batch_reports:
        candidate_verdict = str(report.get("verdict") or "ambiguous").lower()
        if verdict_rank.get(candidate_verdict, 2) > verdict_rank.get(verdict, 0):
            verdict = candidate_verdict
        blocked = blocked or bool(report.get("blocked", False))
        source_refs.extend(report.get("source_refs", []) or [])
        repair_scope.extend(
            item for item in (report.get("repair_scope", []) or []) if isinstance(item, dict)
        )
        preserve.extend(report.get("preserve", []) or [])
        if report.get("change_intent"):
            change_intents.append(str(report.get("change_intent")))
        for issue in report.get("issues", []) or []:
            if isinstance(issue, dict):
                issues.append(issue)
            else:
                issues.append({"severity": "medium", "description": str(issue)})

    if verdict in {"ambiguous", "defer"}:
        blocked = True
        uncertain_candidate_ids = list(
            dict.fromkeys(
                str(candidate_id)
                for report in batch_reports
                for candidate_id in report.get("candidate_ids", []) or []
                if str(candidate_id)
            )
        )
        issues.append(
            {
                "id": f"{stage}_semantic_judgement_uncertain",
                "issue_id": f"{stage}_semantic_judgement_uncertain",
                "issue_type": "semantic_judgement_uncertain",
                "severity": "high",
                "blocking": True,
                "summary": "模型无法确认候选事实能否同时成立。",
                "description": "保留原始来源，由作者确认语义后再继续。",
                "candidate_ids": uncertain_candidate_ids,
                "evidence": [],
                "repair_targets": [
                    {
                        "role": "repair",
                        "target_format": "manual_only",
                        "artifact": repair_artifact,
                        "manual_review_reason": "semantic_judgement_uncertain",
                    }
                ],
                "reference_targets": [],
                "repair_intent": {
                    "operation": "confirm_semantic_meaning",
                    "target_policy": "manual_only",
                    "rationale": "模型裁决为不确定，本地不得代替作者解释。",
                },
                "postconditions": ["作者决定绑定精确来源哈希与 claims"],
            }
        )

    issues = sorted(issues, key=_issue_sort_key)[:12]
    return {
        "schema_version": "audit_v2",
        "stage": stage,
        "artifact": repair_artifact,
        "verdict": verdict,
        "issues": issues,
        "source_refs": source_refs[:24],
        "repair_scope": repair_scope[:24],
        "preserve": preserve[:24],
        "change_intent": "；".join(change_intents[:4]),
        "blocked": blocked,
        "summary": (
            f"{stage} 候选裁判完成：{candidate_count} 个候选，"
            f"{len(issues)} 个问题，最终判定 {verdict}。"
        ),
        "claims_count": claims_count,
        "candidate_count": candidate_count,
        "adjudicated_candidate_count": candidate_count,
        "degraded_memory": degraded_memory,
        "batch_reports": batch_reports,
    }


def _merge_adjudication_batch_reports(
    left: dict[str, Any],
    right: dict[str, Any],
) -> dict[str, Any]:
    """Merge two bisected batch reports into one normalized batch report.

    The bisect fallback splits an oversized candidate batch in half; each half
    produces a normalized report (see ``_normalize_candidate_adjudication_response``).
    This helper re-merges them with the same precedence rules used by
    ``_merge_adjudication_reports`` (worst verdict wins, issues concatenated).
    """
    verdict_rank = {"accept": 0, "defer": 1, "ambiguous": 2, "needs_repair": 3, "reject": 4}
    reports = [report for report in (left, right) if report]
    if not reports:
        return {
            "verdict": "accept",
            "issues": [],
            "source_refs": [],
            "repair_scope": [],
            "preserve": [],
        }
    verdict = max(
        (str(report.get("verdict") or "ambiguous").lower() for report in reports),
        key=lambda item: verdict_rank.get(item, 2),
    )
    issues: list[Any] = []
    source_refs: list[Any] = []
    repair_scope: list[Any] = []
    preserve: list[Any] = []
    blocked = False
    for report in reports:
        issues.extend(report.get("issues", []) or [])
        source_refs.extend(report.get("source_refs", []) or [])
        repair_scope.extend(report.get("repair_scope", []) or [])
        preserve.extend(report.get("preserve", []) or [])
        blocked = blocked or bool(report.get("blocked", False))
    return {
        "schema_version": "audit_v2",
        "verdict": verdict,
        "issues": issues,
        "source_refs": source_refs[:24],
        "repair_scope": repair_scope[:24],
        "preserve": preserve[:24],
        "change_intent": "；".join(
            str(report.get("change_intent") or "")
            for report in reports
            if report.get("change_intent")
        )[:400],
        "blocked": blocked,
        "summary": "；".join(
            str(report.get("summary") or "") for report in reports if report.get("summary")
        )[:800],
        "candidate_ids": [cid for report in reports for cid in (report.get("candidate_ids") or [])],
    }


def _ensure_persistable_claim_adjudication(
    claim: CoherenceClaim,
    *,
    entity_catalog: dict[str, Any] | None,
) -> CoherenceClaim:
    metadata = dict(claim.metadata)
    if metadata.get("entity_adjudication_status"):
        return claim
    revision = entity_catalog_revision(entity_catalog)
    raw_references = {
        "entity_mentions": list(claim.entity_mentions),
        "subject_ids": list(claim.subject_ids),
        "cognitive_subjects": list(claim.cognitive_subjects),
        "character_knowledge_coverage": dict(claim.character_knowledge_coverage),
    }
    has_references = any(raw_references.values())
    metadata.update(
        {
            "raw_entity_references": raw_references,
            "entity_adjudication_status": "unavailable" if has_references else "not_required",
            "entity_adjudication_reason": (
                "persistence_boundary_missing_adjudication" if has_references else ""
            ),
            "entity_reference_revision": revision,
        }
    )
    updates: dict[str, Any] = {"metadata": metadata}
    if has_references:
        updates.update(
            {
                "subject_ids": [],
                "cognitive_subjects": [],
                "character_knowledge_coverage": {},
            }
        )
    return claim.model_copy(update=updates)


def _persist_claims(
    ctx: Any,
    claims: list[CoherenceClaim],
    *,
    stage: str,
    artifacts: dict[str, dict[str, Any]],
    focus_chapters: list[int] | None,
    entity_catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    claims = [
        _ensure_persistable_claim_adjudication(claim, entity_catalog=entity_catalog)
        for claim in claims
    ]
    _save_claims_jsonl(ctx, claims)
    ledger = _load_claim_ledger(ctx)
    claims_by_id = ledger.get("claims_by_id")
    if not isinstance(claims_by_id, dict):
        claims_by_id = {}
    active_claim_ids = [
        str(claim_id)
        for claim_id in (ledger.get("active_claim_ids") or [])
        if isinstance(claim_id, str)
    ]
    artifact_keys = {str(key) for key in artifacts}
    artifact_hashes = _artifact_hashes(artifacts)
    focus_set = {number for number in (focus_chapters or []) if number >= 1}
    invalidated: list[str] = []

    def _invalidate(claim_id: str, *, reason: str) -> None:
        entry = claims_by_id.get(claim_id)
        if not isinstance(entry, dict):
            return
        entry["status"] = "invalidated"
        entry["invalidated_by_stage"] = stage
        entry["invalidation_reason"] = reason
        if focus_set:
            entry["invalidated_focus_chapters"] = sorted(focus_set)
        invalidated.append(claim_id)

    for claim_id in list(active_claim_ids):
        entry = claims_by_id.get(claim_id)
        if not isinstance(entry, dict):
            active_claim_ids.remove(claim_id)
            continue
        if str(entry.get("artifact") or "") not in artifact_keys:
            continue
        if focus_set:
            if _ledger_claim_intersects_focus(entry, focus_set):
                _invalidate(claim_id, reason="focus_reextract")
                active_claim_ids.remove(claim_id)
            continue
        _invalidate(claim_id, reason="artifact_reextract")
        active_claim_ids.remove(claim_id)

    new_claim_ids: list[str] = []
    for claim in claims:
        payload = claim.model_dump(mode="json")
        raw_metadata = payload.get("metadata")
        claim_metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        record = {
            **payload,
            "stage": stage,
            "status": "active",
            "artifact_hash": artifact_hashes.get(claim.artifact, ""),
            "focus_chapters": sorted(focus_set),
            "extraction_mode": str(claim_metadata.get("extraction_mode") or "shard"),
            "evidence_refs": _coerce_string_list(claim_metadata.get("evidence_refs")),
            "risk_level": str(claim_metadata.get("risk_level") or ""),
        }
        claims_by_id[claim.claim_id] = record
        new_claim_ids.append(claim.claim_id)
        if claim.claim_id not in active_claim_ids:
            active_claim_ids.append(claim.claim_id)

    active_claim_ids = [
        claim_id
        for claim_id in dict.fromkeys(active_claim_ids)
        if isinstance(claims_by_id.get(claim_id), dict)
        and claims_by_id[claim_id].get("status") == "active"
    ]
    stages = ledger.get("stages")
    if not isinstance(stages, dict):
        stages = {}
    claim_coverage = _runtime_claim_coverage(ctx, stage)
    compiler_fingerprint = str(getattr(ctx, "_init_coherence_compiler_fingerprint", "") or "")
    stages[stage] = {
        "schema_version": CLAIM_LEDGER_STAGE_SCHEMA_VERSION,
        "claim_ids": new_claim_ids,
        "claim_count": len(new_claim_ids),
        "active_claim_count": len(active_claim_ids),
        "invalidated_claim_ids": invalidated,
        "focus_chapters": sorted(focus_set),
        "artifact_hashes": artifact_hashes,
        "compiler_fingerprint": compiler_fingerprint,
        "compiler_runtime_fingerprint": str(
            getattr(ctx, "_init_coherence_compiler_runtime_fingerprint", "") or ""
        ),
        "claim_coverage": claim_coverage,
    }
    history = ledger.get("history")
    if not isinstance(history, list):
        history = []
    history.append(
        {
            "stage": stage,
            "claim_count": len(new_claim_ids),
            "active_claim_count": len(active_claim_ids),
            "invalidated_claim_count": len(invalidated),
            "focus_chapters": sorted(focus_set),
            "artifacts": sorted(artifact_keys),
            "compiler_fingerprint": compiler_fingerprint,
            "claim_coverage_status": claim_coverage["status"],
        }
    )
    ledger = {
        "schema_version": 1,
        "claims_by_id": claims_by_id,
        "active_claim_ids": active_claim_ids,
        "stages": stages,
        "history": history[-50:],
        "latest_stage": stage,
        "summary": (
            f"初始化 claims 账本：active={len(active_claim_ids)}，"
            f"latest={len(new_claim_ids)}，invalidated={len(invalidated)}。"
        ),
    }
    ctx.storage.save_json(ctx.layout.memory_dir / CLAIM_LEDGER_JSON, ledger)
    return ledger


def _persist_stream_claims(
    ctx: Any,
    claims: list[CoherenceClaim],
    *,
    stage: str,
    chunks: list[InitArtifactChunk],
) -> dict[str, Any]:
    ledger = _load_claim_ledger(ctx)
    claims_by_id = ledger.get("claims_by_id")
    if not isinstance(claims_by_id, dict):
        claims_by_id = {}
    active_claim_ids = [
        str(claim_id)
        for claim_id in (ledger.get("active_claim_ids") or [])
        if isinstance(claim_id, str)
    ]
    streams = ledger.get("streams")
    if not isinstance(streams, dict):
        streams = {}
    stream = streams.get(stage)
    if not isinstance(stream, dict):
        stream = {}

    focus_chapters = sorted(
        {
            number
            for chunk in chunks
            for number in chunk.chapter_numbers
            if isinstance(number, int) and number >= 1
        }
    )
    focus_set = set(focus_chapters)
    artifact_keys = {chunk.artifact for chunk in chunks}
    stream_claim_ids = [
        str(claim_id) for claim_id in (stream.get("claim_ids") or []) if isinstance(claim_id, str)
    ]
    invalidated: list[str] = []

    for claim_id in list(active_claim_ids):
        entry = claims_by_id.get(claim_id)
        if not isinstance(entry, dict):
            active_claim_ids.remove(claim_id)
            continue
        if str(entry.get("artifact") or "") not in artifact_keys:
            continue
        if focus_set and not _ledger_claim_intersects_focus(entry, focus_set):
            continue
        entry["status"] = "invalidated"
        entry["invalidated_by_stage"] = stage
        entry["invalidation_reason"] = "stream_reextract"
        entry["invalidated_focus_chapters"] = focus_chapters
        invalidated.append(claim_id)
        if claim_id in active_claim_ids:
            active_claim_ids.remove(claim_id)
        stream_claim_ids = [item for item in stream_claim_ids if item != claim_id]

    new_claim_ids: list[str] = []
    for claim in claims:
        payload = claim.model_dump(mode="json")
        raw_metadata = payload.get("metadata")
        claim_metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        record = {
            **payload,
            "stage": stage,
            "status": "active",
            "artifact_hash": "",
            "streamed": True,
            "stream_focus_chapters": focus_chapters,
            "extraction_mode": str(claim_metadata.get("extraction_mode") or "stream"),
            "evidence_refs": _coerce_string_list(claim_metadata.get("evidence_refs")),
            "risk_level": str(claim_metadata.get("risk_level") or ""),
        }
        claims_by_id[claim.claim_id] = record
        new_claim_ids.append(claim.claim_id)
        if claim.claim_id not in active_claim_ids:
            active_claim_ids.append(claim.claim_id)
        if claim.claim_id not in stream_claim_ids:
            stream_claim_ids.append(claim.claim_id)

    covered_chapters = sorted(
        {
            *_coerce_positive_ints(stream.get("covered_chapters")),
            *focus_chapters,
        }
    )
    stream_artifacts = stream.get("artifacts")
    if not isinstance(stream_artifacts, list):
        stream_artifacts = []
    stream.update(
        {
            "claim_ids": stream_claim_ids,
            "covered_chapters": covered_chapters,
            "artifacts": sorted({*stream_artifacts, *artifact_keys}),
            "finalized": False,
            "latest_claim_count": len(new_claim_ids),
            "invalidated_claim_ids": invalidated,
        }
    )
    streams[stage] = stream

    history = ledger.get("history")
    if not isinstance(history, list):
        history = []
    history.append(
        {
            "stage": stage,
            "streamed": True,
            "claim_count": len(new_claim_ids),
            "active_claim_count": len(active_claim_ids),
            "invalidated_claim_count": len(invalidated),
            "focus_chapters": focus_chapters,
            "artifacts": sorted(artifact_keys),
        }
    )
    active_claim_ids = [
        claim_id
        for claim_id in dict.fromkeys(active_claim_ids)
        if isinstance(claims_by_id.get(claim_id), dict)
        and claims_by_id[claim_id].get("status") == "active"
    ]
    ledger = {
        "schema_version": 1,
        "claims_by_id": claims_by_id,
        "active_claim_ids": active_claim_ids,
        "stages": ledger.get("stages") if isinstance(ledger.get("stages"), dict) else {},
        "streams": streams,
        "history": history[-50:],
        "latest_stage": stage,
        "summary": (
            f"初始化流式 claims：stage={stage}，active={len(active_claim_ids)}，"
            f"latest={len(new_claim_ids)}，covered={len(covered_chapters)}。"
        ),
    }
    ctx.storage.save_json(ctx.layout.memory_dir / CLAIM_LEDGER_JSON, ledger)
    return ledger


def finalize_streamed_init_coherence_claims(
    ctx: Any,
    *,
    stage: str,
    artifacts: dict[str, dict[str, Any]],
) -> bool:
    """Promote streamed claim batches to the reusable stage record when complete."""
    ledger = _load_claim_ledger(ctx)
    streams = ledger.get("streams")
    if not isinstance(streams, dict):
        return False
    stream = streams.get(stage)
    if not isinstance(stream, dict):
        return False
    claims_by_id = ledger.get("claims_by_id")
    if not isinstance(claims_by_id, dict):
        return False

    expected_hashes = _artifact_hashes(artifacts)
    expected_artifacts = {str(key) for key in artifacts}
    expected_outline_chapters = _expected_outline_chapters(artifacts.get("outline"))
    covered_chapters = set(_coerce_positive_ints(stream.get("covered_chapters")))
    if expected_outline_chapters and not expected_outline_chapters.issubset(covered_chapters):
        return False

    stream_claim_ids = {
        str(claim_id) for claim_id in (stream.get("claim_ids") or []) if isinstance(claim_id, str)
    }
    active_claim_ids = [
        str(claim_id)
        for claim_id in (ledger.get("active_claim_ids") or [])
        if isinstance(claim_id, str)
    ]
    stage_claim_ids: list[str] = []
    for claim_id in active_claim_ids:
        entry = claims_by_id.get(claim_id)
        if not isinstance(entry, dict) or entry.get("status") != "active":
            continue
        artifact = str(entry.get("artifact") or "")
        if artifact not in expected_artifacts:
            continue
        if claim_id in stream_claim_ids:
            entry["artifact_hash"] = expected_hashes.get(artifact, "")
            entry["stream_finalized_stage"] = stage
            stage_claim_ids.append(claim_id)
            continue
        if str(entry.get("artifact_hash") or "") == expected_hashes.get(artifact, ""):
            stage_claim_ids.append(claim_id)

    if not stage_claim_ids:
        return False
    missing_artifacts = {
        artifact
        for artifact in expected_artifacts
        if not any(
            isinstance(claims_by_id.get(claim_id), dict)
            and str(claims_by_id[claim_id].get("artifact") or "") == artifact
            for claim_id in stage_claim_ids
        )
    }
    if missing_artifacts:
        return False

    stages = ledger.get("stages")
    if not isinstance(stages, dict):
        stages = {}
    claim_coverage = _runtime_claim_coverage(ctx, stage)
    stages[stage] = {
        "schema_version": CLAIM_LEDGER_STAGE_SCHEMA_VERSION,
        "claim_ids": list(dict.fromkeys(stage_claim_ids)),
        "claim_count": len(stage_claim_ids),
        "active_claim_count": len(active_claim_ids),
        "invalidated_claim_ids": [],
        "focus_chapters": [],
        "artifact_hashes": expected_hashes,
        "streamed": True,
        "compiler_fingerprint": str(getattr(ctx, "_init_coherence_compiler_fingerprint", "") or ""),
        "compiler_runtime_fingerprint": str(
            getattr(ctx, "_init_coherence_compiler_runtime_fingerprint", "") or ""
        ),
        "claim_coverage": claim_coverage,
    }
    stream["finalized"] = True
    stream["artifact_hashes"] = expected_hashes
    streams[stage] = stream
    ledger["claims_by_id"] = claims_by_id
    ledger["stages"] = stages
    ledger["streams"] = streams
    ledger["latest_stage"] = stage
    ctx.storage.save_json(ctx.layout.memory_dir / CLAIM_LEDGER_JSON, ledger)
    return True


def _expected_outline_chapters(outline_payload: dict[str, Any] | None) -> set[int]:
    if not isinstance(outline_payload, dict):
        return set()
    chapters = outline_payload.get("chapters")
    if not isinstance(chapters, list):
        return set()
    return {
        number
        for chapter in chapters
        if isinstance(chapter, dict)
        and (number := _coerce_positive_int(chapter.get("chapter_number")))
    }


def _save_claims_jsonl(ctx: Any, claims: list[CoherenceClaim]) -> None:
    lines = [
        json.dumps(claim.model_dump(mode="json"), ensure_ascii=False, default=str)
        for claim in claims
    ]
    ctx.storage.save_text(
        ctx.layout.memory_dir / CLAIMS_JSONL,
        ("\n".join(lines) + "\n") if lines else "",
    )


def _claim_entity_adjudication_reusable(
    claim_data: dict[str, Any],
    *,
    entity_catalog: dict[str, Any] | None,
) -> bool:
    metadata = claim_data.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    revision = entity_catalog_revision(entity_catalog)
    if str(metadata.get("entity_reference_revision") or "") != revision:
        return False
    status = str(metadata.get("entity_adjudication_status") or "")
    has_catalog = bool(isinstance(entity_catalog, dict) and entity_catalog.get("allowed_entities"))
    if has_catalog:
        return status in {"adjudicated", "not_required"}
    return status in {"adjudicated", "not_required", "unavailable"}


def _load_reusable_stage_claims(
    ctx: Any,
    *,
    stage: str,
    artifacts: dict[str, dict[str, Any]],
    focus_chapters: list[int] | None,
    entity_catalog: dict[str, Any] | None = None,
) -> tuple[list[CoherenceClaim], dict[str, Any]] | None:
    ledger = _load_claim_ledger(ctx)
    stages = ledger.get("stages")
    if not isinstance(stages, dict):
        _increment_init_metric(ctx, "claim_ledger_misses")
        return None
    stage_record = stages.get(stage)
    if not isinstance(stage_record, dict):
        _increment_init_metric(ctx, "claim_ledger_misses")
        return None
    if int(stage_record.get("schema_version") or 0) < CLAIM_LEDGER_STAGE_SCHEMA_VERSION:
        _increment_init_metric(ctx, "claim_ledger_misses")
        return None

    expected_compiler_fingerprint = str(
        getattr(ctx, "_init_coherence_compiler_fingerprint", "") or ""
    )
    if (
        not expected_compiler_fingerprint
        or str(stage_record.get("compiler_fingerprint") or "") != expected_compiler_fingerprint
    ):
        _increment_init_metric(ctx, "claim_ledger_misses")
        return None
    if not bool(_stage_claim_coverage(stage_record).get("complete")):
        _increment_init_metric(ctx, "claim_ledger_misses")
        return None

    expected_hashes = _artifact_hashes(artifacts)
    recorded_hashes = stage_record.get("artifact_hashes")
    recorded_matches = (
        isinstance(recorded_hashes, dict)
        and {str(key): str(value) for key, value in recorded_hashes.items()} == expected_hashes
    )
    if not recorded_matches:
        _increment_init_metric(ctx, "claim_ledger_misses")
        return None

    expected_focus = sorted({number for number in (focus_chapters or []) if number >= 1})
    recorded_focus: list[int] = []
    for number in stage_record.get("focus_chapters") or []:
        try:
            focus_number = int(number)
        except (TypeError, ValueError):
            continue
        if focus_number >= 1 and focus_number not in recorded_focus:
            recorded_focus.append(focus_number)
    recorded_focus.sort()
    if recorded_focus != expected_focus:
        _increment_init_metric(ctx, "claim_ledger_misses")
        return None

    claim_ids = [
        str(claim_id)
        for claim_id in (stage_record.get("claim_ids") or [])
        if isinstance(claim_id, str)
    ]
    claims_by_id = ledger.get("claims_by_id")
    if not isinstance(claims_by_id, dict):
        _increment_init_metric(ctx, "claim_ledger_misses")
        return None

    claims: list[CoherenceClaim] = []
    for claim_id in claim_ids:
        entry = claims_by_id.get(claim_id)
        if not isinstance(entry, dict) or entry.get("status") != "active":
            _increment_init_metric(ctx, "claim_ledger_misses")
            return None
        entry_stage = str(entry.get("stream_finalized_stage") or entry.get("stage") or "")
        if entry_stage != stage:
            _increment_init_metric(ctx, "claim_ledger_misses")
            return None
        artifact = str(entry.get("artifact") or "")
        if str(entry.get("artifact_hash") or "") != expected_hashes.get(artifact, ""):
            _increment_init_metric(ctx, "claim_ledger_misses")
            return None
        if not _claim_entity_adjudication_reusable(entry, entity_catalog=entity_catalog):
            _increment_init_metric(ctx, "claim_ledger_misses")
            return None
        try:
            claims.append(
                CoherenceClaim.model_validate(
                    _coerce_claim_string_fields(dict(entry), entity_catalog=None)
                )
            )
        except Exception as exc:
            _log.debug(
                "init_coherence_stage_claim_invalid | stage=%s | claim_id=%s | error=%s",
                stage,
                claim_id,
                exc,
            )
            _increment_init_metric(ctx, "claim_ledger_misses")
            return None

    _save_claims_jsonl(ctx, claims)
    _increment_init_metric(ctx, "claim_ledger_hits")
    _emit_step(
        ctx,
        "extract_init_coherence_claims",
        {
            "stage": stage,
            "artifact": "ledger",
            "batch": 1,
            "batch_total": 1,
            "batches_done": 1,
            "claims": len(claims),
            "max_parallel": 0,
            "cached": True,
            "source": "claim_ledger",
        },
    )
    return claims, ledger


def _claims_for_retrieval(
    claims: list[CoherenceClaim],
    claim_ledger: dict[str, Any],
    *,
    stage: str = "",
    artifact_keys: set[str] | None = None,
    focus_chapters: list[int] | None = None,
    entity_catalog: dict[str, Any] | None = None,
) -> list[CoherenceClaim]:
    if not focus_chapters:
        return claims
    focus_set = {number for number in focus_chapters if number >= 1}
    if not focus_set:
        return claims
    allowed_artifacts = {str(key) for key in (artifact_keys or set()) if str(key)}
    current_context_keys = _recheck_context_keys_for_claims(claims)
    current_claim_ids = {claim.claim_id for claim in claims}
    active_claims: list[CoherenceClaim] = list(claims)
    claims_by_id = claim_ledger.get("claims_by_id")
    if not isinstance(claims_by_id, dict):
        return claims
    for claim_id in claim_ledger.get("active_claim_ids", []) or []:
        entry = claims_by_id.get(claim_id)
        if not isinstance(entry, dict) or entry.get("status") != "active":
            continue
        if stage and str(entry.get("stage") or "") != stage:
            continue
        artifact = str(entry.get("artifact") or "").strip()
        if allowed_artifacts and artifact not in allowed_artifacts:
            continue
        if not _claim_entity_adjudication_reusable(entry, entity_catalog=entity_catalog):
            continue
        try:
            claim = CoherenceClaim.model_validate(
                _coerce_claim_string_fields(dict(entry), entity_catalog=None)
            )
        except Exception as exc:
            _log.debug(
                "init_coherence_ledger_claim_invalid | claim_id=%s | error=%s",
                claim_id,
                exc,
            )
            continue
        if claim.claim_id in current_claim_ids:
            continue
        if _ledger_claim_intersects_focus(entry, focus_set) or (
            current_context_keys and (_recheck_context_keys_for_claim(claim) & current_context_keys)
        ):
            active_claims.append(claim)
    return _dedupe_claims(active_claims or claims)


def _recheck_context_keys_for_claims(claims: list[CoherenceClaim]) -> set[str]:
    keys: set[str] = set()
    for claim in claims:
        keys.update(_recheck_context_keys_for_claim(claim))
    return keys


def _recheck_context_keys_for_claim(claim: CoherenceClaim) -> set[str]:
    subject = _claim_subject_key(claim)
    axis = _normalize_key(claim.axis or claim.event_type or claim.claim_type)
    object_key = _claim_cognitive_object_key(claim)
    payoff_id = _normalize_key(claim.payoff_id)
    payoff_kind = _normalize_key(claim.payoff_kind)
    keys: set[str] = set()
    if subject and axis:
        keys.add(f"subject_axis:{subject}|{axis}")
    if subject and object_key:
        keys.add(f"subject_object:{subject}|{object_key}")
    if payoff_id:
        keys.add(f"payoff_id:{payoff_id}")
    if payoff_kind:
        keys.add(f"payoff_kind:{payoff_kind}")
    return keys


def _load_claim_ledger(ctx: Any) -> dict[str, Any]:
    path = ctx.layout.memory_dir / CLAIM_LEDGER_JSON
    if not ctx.storage.exists(path):
        return {}
    try:
        payload = ctx.storage.load_json(path)
    except Exception as exc:
        _log.warning("init_coherence_claim_ledger_load_failed | error=%s", exc)
        return {}
    return payload if isinstance(payload, dict) else {}


def _artifact_hashes(artifacts: dict[str, dict[str, Any]]) -> dict[str, str]:
    return {str(key): _stable_payload_hash(value) for key, value in artifacts.items()}


def init_coherence_artifact_hashes(artifacts: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Return stable hashes for init coherence artifact payloads."""
    return _artifact_hashes(artifacts)


def semantic_compiler_fingerprint(
    ctx: Any,
    *,
    profile: dict[str, Any],
    artifacts: dict[str, dict[str, Any]],
) -> str:
    """Bind cached semantic results to sources, contracts, prompts and routes."""

    router = getattr(ctx, "router", None)
    route_tasks = (
        TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
        TaskType.EXTRACT_BLUEPRINT_HOLISTIC_CLAIMS,
        TaskType.ADJUDICATE_INIT_CONFLICT_CANDIDATES,
    )

    def _task_mapping(name: str) -> dict[str, Any]:
        mapping = getattr(router, name, None)
        if not isinstance(mapping, dict):
            return {}
        result: dict[str, Any] = {}
        for task in route_tasks:
            if task not in mapping:
                continue
            value = mapping[task]
            if hasattr(value, "model_dump"):
                value = value.model_dump(mode="json")
            elif hasattr(value, "__dict__"):
                value = vars(value)
            result[task.value] = value
        return result

    route_projection = {
        "router_type": type(router).__name__ if router is not None else "",
        "default_provider": str(getattr(router, "_default_provider", "") or ""),
        "task_providers": _task_mapping("_task_providers"),
        "task_tiers": _task_mapping("_task_tiers"),
        "task_fallbacks": _task_mapping("_task_fallbacks"),
        "tier_to_model": getattr(router, "_tier_to_model", {}),
    }
    settings = getattr(ctx, "settings", None)
    settings_projection = {
        name: getattr(settings, name, None)
        for name in (
            "init_coherence_candidate_max_per_batch",
            "init_coherence_claim_batch_size",
            "init_coherence_claim_payload_char_budget",
            "init_coherence_claim_split_max_depth",
            "init_coherence_overlap_chapters",
            "init_blueprint_holistic_claims_enabled",
            "init_blueprint_holistic_claim_max_tokens",
            "init_coherence_llm_candidate_batch_size",
            "init_coherence_llm_candidate_max_parallel",
            "init_coherence_semantic_top_k",
            "init_coherence_use_memory",
            "temp_extract_init_coherence_claims",
            "temp_extract_blueprint_holistic_claims",
            "temp_adjudicate_init_conflict_candidates",
        )
    }
    return _stable_payload_hash(
        {
            "compiler": "existing_claim_ledger_semantic_compiler",
            "compiler_contract_version": SEMANTIC_COMPILER_CONTRACT_VERSION,
            "claim_batch_cache_schema": CLAIM_BATCH_CACHE_SCHEMA_VERSION,
            "claim_ledger_stage_schema": CLAIM_LEDGER_STAGE_SCHEMA_VERSION,
            "prompt_version": TemplateVersionManager.CURRENT_VERSION,
            "prompt_templates": _semantic_prompt_template_projection(route_tasks),
            "task_types": [task.value for task in route_tasks],
            "format_contracts": {
                task.value: _semantic_contract_projection(task) for task in route_tasks
            },
            "profile": profile,
            "artifact_hashes": _artifact_hashes(artifacts),
            "route": route_projection,
            "settings": settings_projection,
        }
    )


def _semantic_prompt_template_projection(
    route_tasks: tuple[TaskType, ...],
) -> dict[str, Any]:
    """Hash installed task templates so prompt edits invalidate compilation."""

    projection: dict[str, Any] = {}
    for locale, pack in sorted(discover_prompt_packs().items()):
        task_hashes: dict[str, str] = {}
        for task in route_tasks:
            path = pack.templates_dir / "checking" / f"{task.value}.j2"
            task_hashes[task.value] = (
                hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "missing"
            )
        projection[locale] = {
            "source_revision": pack.source_revision,
            "task_template_hashes": task_hashes,
        }
    return projection


def _semantic_contract_projection(task_type: TaskType) -> dict[str, Any]:
    contract = get_task_format_contract(task_type)
    response_schema = get_response_schema(task_type)
    return {
        "required_top_level_keys": list(contract.required_top_level_keys) if contract else [],
        "allowed_top_level_keys": list(contract.allowed_top_level_keys) if contract else [],
        "enforce_required_keys": bool(contract.enforce_required_keys) if contract else False,
        "contract_mode": contract.effective_contract_mode.value if contract else "",
        "schema_source": str(contract.schema_source) if contract else "",
        "schema_strength": str(contract.schema_strength) if contract else "",
        "json_schema": contract.json_schema if contract else None,
        "response_schema": response_schema.model_json_schema() if response_schema else None,
    }


def semantic_compiler_runtime_fingerprint(ctx: Any) -> str:
    """Return the route/prompt/contract portion used by report resume checks."""

    return semantic_compiler_fingerprint(ctx, profile={}, artifacts={})


def _reset_claim_coverage(ctx: Any, stage: str) -> None:
    registry = getattr(ctx, "_init_coherence_claim_coverage", None)
    if not isinstance(registry, dict):
        registry = {}
        ctx._init_coherence_claim_coverage = registry
    registry[stage] = []


def _runtime_claim_coverage(ctx: Any, stage: str) -> dict[str, Any]:
    registry = getattr(ctx, "_init_coherence_claim_coverage", None)
    records = registry.get(stage, []) if isinstance(registry, dict) else []
    records = [dict(item) for item in records if isinstance(item, dict)]
    statuses = {str(item.get("coverage_status") or "uncertain") for item in records}
    if records and statuses == {"complete"}:
        status = "complete"
    elif "partial" in statuses:
        status = "partial"
    else:
        status = "uncertain"
    unprocessed = list(
        dict.fromkeys(
            str(ref)
            for item in records
            for ref in item.get("unprocessed_source_refs", []) or []
            if str(ref).strip()
        )
    )
    return {
        "status": status,
        "complete": status == "complete",
        "batch_count": len(records),
        "records": records,
        "unprocessed_source_refs": unprocessed,
    }


def _stage_claim_coverage(stage_record: Any) -> dict[str, Any]:
    if not isinstance(stage_record, dict):
        return {
            "status": "uncertain",
            "complete": False,
            "batch_count": 0,
            "records": [],
            "unprocessed_source_refs": [],
        }
    coverage = stage_record.get("claim_coverage")
    if not isinstance(coverage, dict):
        return {
            "status": "uncertain",
            "complete": False,
            "batch_count": 0,
            "records": [],
            "unprocessed_source_refs": [],
        }
    result = dict(coverage)
    result["complete"] = str(result.get("status") or "") == "complete"
    return result


def _structural_pair_coverage(
    candidates: list[ConflictCandidate],
    claims: list[CoherenceClaim],
) -> dict[str, Any]:
    eligible = _structural_pair_keys(claims)
    covered = {
        tuple(sorted(candidate.claim_ids))
        for candidate in candidates
        if candidate.candidate_type == "structural_overlap" and len(candidate.claim_ids) >= 2
    }
    uncovered = sorted(eligible - covered)
    coverage_keys = sorted("|".join(pair) for pair in covered)
    return {
        "complete": not uncovered,
        "eligible_pair_count": len(eligible),
        "covered_pair_count": len(eligible & covered),
        "uncovered_pair_ids": ["|".join(pair) for pair in uncovered],
        "coverage_hash": _stable_payload_hash(coverage_keys),
    }


def _apply_semantic_coverage_gate(
    report: InitConflictAdjudicationReport,
    *,
    stage: str,
    repair_artifact: str,
    claim_coverage: dict[str, Any],
    pair_coverage: dict[str, Any],
) -> InitConflictAdjudicationReport:
    if bool(claim_coverage.get("complete")) and bool(pair_coverage.get("complete")):
        return report
    existing_issues = [dict(issue) for issue in report.issues if isinstance(issue, dict)]
    issue_id = f"{stage}_semantic_coverage_incomplete"
    refs = [str(ref) for ref in claim_coverage.get("unprocessed_source_refs", []) if str(ref)]
    summary = "语义 claims 覆盖未能证明完整，需人工复核未处理来源。"
    existing_issues.append(
        {
            "id": issue_id,
            "issue_id": issue_id,
            "issue_type": "semantic_coverage_incomplete",
            "severity": "high",
            "blocking": True,
            "summary": summary,
            "description": summary,
            "evidence": [
                {
                    "quote": "；".join(refs) if refs else "抽取器未能声明完整覆盖。",
                    "source": "claim_extraction_coverage",
                }
            ],
            "repair_targets": [
                {
                    "role": "repair",
                    "target_format": "manual_only",
                    "artifact": repair_artifact,
                    "manual_review_reason": "claim_coverage_incomplete",
                }
            ],
            "reference_targets": [],
            "repair_intent": {
                "operation": "review_source_coverage",
                "target_policy": "manual_only",
                "rationale": summary,
            },
            "postconditions": ["所有来源分块覆盖状态为 complete"],
        }
    )
    return report.model_copy(
        update={
            "verdict": (
                report.verdict if str(report.verdict) in {"needs_repair", "reject"} else "ambiguous"
            ),
            "issues": existing_issues,
            "blocked": True,
            "summary": f"{report.summary} {summary}".strip(),
        }
    )


def _update_claim_ledger_compilation(
    ctx: Any,
    *,
    stage: str,
    compiler_fingerprint: str,
    compiler_runtime_fingerprint: str,
    claim_coverage: dict[str, Any],
    pair_coverage: dict[str, Any],
    report: dict[str, Any],
) -> None:
    ledger = _load_claim_ledger(ctx)
    stages = ledger.get("stages")
    if not isinstance(stages, dict):
        return
    stage_record = stages.get(stage)
    if not isinstance(stage_record, dict):
        return
    stage_record["compiler_fingerprint"] = compiler_fingerprint
    stage_record["compiler_runtime_fingerprint"] = compiler_runtime_fingerprint
    stage_record["claim_coverage"] = claim_coverage
    stage_record["pair_coverage"] = pair_coverage
    stage_record["semantic_status"] = _semantic_status_from_report(report)
    stage_record["adjudication_report_hash"] = _stable_payload_hash(report)
    ctx.storage.save_json(ctx.layout.memory_dir / CLAIM_LEDGER_JSON, ledger)


def _semantic_status_from_report(report: dict[str, Any]) -> str:
    verdict = str(report.get("verdict") or "ambiguous")
    if verdict in {"needs_repair", "reject"}:
        return "conflict"
    if verdict in {"ambiguous", "defer"} or bool(report.get("blocked")):
        return "review_required"
    return "clean"


def _stable_payload_hash(payload: Any) -> str:
    return semantic_payload_hash(payload)


def _canonical_payload_for_hash(payload: Any) -> Any:
    """Return a deterministic content payload for cache signatures.

    Domain schemas inherit metadata fields such as ``created_at``.  When old
    artifacts are loaded through Pydantic, missing nested timestamps may be
    regenerated, which made init-coherence reports appear stale after every
    resume.  The coherence cache should track story content, not load time.
    """
    if isinstance(payload, dict):
        return {
            str(key): _canonical_payload_for_hash(value)
            for key, value in payload.items()
            if str(key) not in _VOLATILE_HASH_KEYS
        }
    if isinstance(payload, list):
        return [_canonical_payload_for_hash(item) for item in payload]
    if isinstance(payload, tuple):
        return [_canonical_payload_for_hash(item) for item in payload]
    return payload


def _ledger_claim_intersects_focus(entry: dict[str, Any], focus_chapters: set[int]) -> bool:
    numbers = _coerce_positive_ints(entry.get("chapter_numbers"))
    chapter_range = entry.get("chapter_range")
    if isinstance(chapter_range, dict):
        start = _coerce_positive_int(chapter_range.get("start"))
        end = _coerce_positive_int(chapter_range.get("end"))
        if start and end:
            if end < start:
                start, end = end, start
            numbers.extend(range(start, end + 1))
    return bool(set(numbers) & focus_chapters)


def _coerce_positive_ints(value: Any) -> list[int]:
    values = value if isinstance(value, list) else [value]
    result: list[int] = []
    for item in values:
        number = _coerce_positive_int(item)
        if number and number not in result:
            result.append(number)
    return result


def _coerce_string_list(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    result: list[str] = []
    for item in values:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _coerce_positive_int(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number >= 1 else 0


def _persist_index(
    ctx: Any,
    *,
    claims: list[CoherenceClaim],
    vector_index: dict[str, Any],
    degraded_memory: bool,
    claim_ledger: dict[str, Any] | None = None,
) -> None:
    structured: dict[str, Any] = {
        "claims_by_id": {claim.claim_id: claim.model_dump(mode="json") for claim in claims},
        "by_subject_axis": {},
        "by_payoff": {},
        "degraded_memory": degraded_memory,
        "claim_ledger_path": f"memory/{CLAIM_LEDGER_JSON}",
    }
    for claim in claims:
        subject_axis = f"{_claim_subject_key(claim)}::{_normalize_key(claim.axis)}"
        if subject_axis != "::":
            structured["by_subject_axis"].setdefault(subject_axis, []).append(claim.claim_id)
        payoff_key = _normalize_key(claim.payoff_id or claim.payoff_kind)
        if payoff_key:
            structured["by_payoff"].setdefault(payoff_key, []).append(claim.claim_id)
    ctx.storage.save_json(
        ctx.layout.memory_dir / INDEX_JSON,
        {
            "structured": structured,
            "memory": vector_index,
            "ledger": {
                "path": f"memory/{CLAIM_LEDGER_JSON}",
                "active_claim_count": len((claim_ledger or {}).get("active_claim_ids", []) or []),
                "latest_stage": (claim_ledger or {}).get("latest_stage", ""),
            },
            "summary": f"初始化一致性索引：claims={len(claims)}。",
        },
    )


def _save_stage_bundle(ctx: Any, filename: str, stage: str, payload: dict[str, Any]) -> None:
    path = ctx.layout.reports_dir / filename
    bundle: dict[str, Any]
    if ctx.storage.exists(path):
        try:
            loaded = ctx.storage.load_json(path)
            bundle = loaded if isinstance(loaded, dict) else {}
        except Exception:
            bundle = {}
    else:
        bundle = {}
    stages = bundle.get("stages")
    if not isinstance(stages, dict):
        stages = {}
    stages[stage] = payload
    bundle["stages"] = stages
    bundle["latest_stage"] = stage
    bundle["summary"] = payload.get("summary", "")
    ctx.storage.save_json(path, bundle)


def _dedupe_claims(claims: list[CoherenceClaim]) -> list[CoherenceClaim]:
    result_by_key: dict[tuple[str, ...], CoherenceClaim] = {}
    order: list[tuple[str, ...]] = []
    for claim in claims:
        key = _claim_dedupe_key(claim)
        existing = result_by_key.get(key)
        if existing is None:
            result_by_key[key] = claim
            order.append(key)
            continue
        if claim.confidence > existing.confidence:
            result_by_key[key] = claim
    return [result_by_key[key] for key in order]


def _claim_dedupe_key(claim: CoherenceClaim) -> tuple[str, ...]:
    chapter_range = claim.chapter_range
    range_key = ""
    if chapter_range is not None:
        range_key = f"{chapter_range.start}:{chapter_range.end}"
    subject_key = "|".join(sorted(claim.subject_ids)) or claim.subject_text
    if subject_key or claim.axis or claim.claim_text:
        return (
            "semantic",
            claim.artifact,
            claim.source_path,
            subject_key,
            claim.axis,
            claim.claim_type,
            range_key,
            claim.claim_text[:160],
        )
    return (
        "id",
        claim.claim_id,
    )


def _dedupe_candidates(candidates: list[ConflictCandidate]) -> list[ConflictCandidate]:
    result: list[ConflictCandidate] = []
    seen: set[tuple[str, ...]] = set()
    for candidate in candidates:
        key = tuple(sorted(candidate.claim_ids))
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def _chapter_numbers_from_items(items: list[Any]) -> list[int]:
    numbers: set[int] = set()
    for item in items:
        _collect_chapter_numbers(item, numbers)
    return sorted(numbers)


def _chunk_intersects_focus(chunk: InitArtifactChunk, focus_chapters: list[int]) -> bool:
    if not chunk.chapter_numbers:
        return False
    focus = set(focus_chapters)
    return any(number in focus for number in chunk.chapter_numbers)


def _collect_chapter_numbers(value: Any, result: set[int]) -> None:
    if isinstance(value, dict):
        for key in ("chapter_number", "chapter", "chapter_start", "chapter_end"):
            if key in value:
                try:
                    number = int(value[key])
                except (TypeError, ValueError):
                    continue
                if number >= 1:
                    result.add(number)
        for nested in value.values():
            _collect_chapter_numbers(nested, result)
    elif isinstance(value, list):
        for item in value:
            _collect_chapter_numbers(item, result)


def _claim_subject_key(claim: CoherenceClaim) -> str:
    if claim.subject_ids:
        return "|".join(sorted(_normalize_key(subject) for subject in claim.subject_ids if subject))
    return _normalize_key(claim.subject_text)


def _normalize_key(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _claim_sort_key(claim: CoherenceClaim) -> tuple[int, str, str]:
    return (_claim_start_chapter(claim), claim.artifact, claim.claim_id)


def _claim_pair_key(left: CoherenceClaim, right: CoherenceClaim) -> tuple[str, str]:
    if left.claim_id <= right.claim_id:
        return (left.claim_id, right.claim_id)
    return (right.claim_id, left.claim_id)


def _claim_start_chapter(claim: CoherenceClaim) -> int:
    if claim.chapter_range is not None and claim.chapter_range.start:
        return claim.chapter_range.start
    if claim.chapter_numbers:
        return min(claim.chapter_numbers)
    return 0


def _claim_span(claim: CoherenceClaim) -> tuple[int, int]:
    if claim.chapter_range is not None and claim.chapter_range.start:
        start = claim.chapter_range.start
        end = claim.chapter_range.end or start
        return min(start, end), max(start, end)
    if claim.chapter_numbers:
        return min(claim.chapter_numbers), max(claim.chapter_numbers)
    return 0, 0


def _chapter_span_for_claims(claims: list[CoherenceClaim]) -> ClaimChapterRange | None:
    numbers: list[int] = []
    for claim in claims:
        if claim.chapter_range is not None:
            if claim.chapter_range.start:
                numbers.append(claim.chapter_range.start)
            if claim.chapter_range.end:
                numbers.append(claim.chapter_range.end)
        numbers.extend(claim.chapter_numbers)
    if not numbers:
        return None
    return ClaimChapterRange(start=min(numbers), end=max(numbers))


def _same_artifact_path(a: CoherenceClaim, b: CoherenceClaim) -> bool:
    return a.artifact == b.artifact and a.source_path == b.source_path


def _claim_has_different_state_after(a: CoherenceClaim, b: CoherenceClaim) -> bool:
    left = _normalize_key(a.state_after)
    right = _normalize_key(b.state_after)
    return bool(left and right and left != right)


def _normalize_adjudication_issues(
    raw_issues: list[Any],
    *,
    candidate_by_id: dict[str, ConflictCandidate],
    allowed_repair_artifacts: set[str],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for raw_issue in raw_issues:
        issue = raw_issue if isinstance(raw_issue, dict) else {"description": str(raw_issue)}
        raw_candidate_ids = [
            str(candidate_id)
            for candidate_id in issue.get("candidate_ids", []) or []
            if str(candidate_id)
        ]
        candidate_ids = [
            candidate_id for candidate_id in raw_candidate_ids if candidate_id in candidate_by_id
        ]
        if raw_candidate_ids and not candidate_ids:
            continue
        if not candidate_ids and len(candidate_by_id) == 1:
            candidate_ids = [next(iter(candidate_by_id))]
        if not candidate_ids:
            continue
        normalized = dict(issue)
        normalized["candidate_ids"] = candidate_ids
        normalized["repair_scope"] = _sanitize_repair_scopes(
            normalized.get("repair_scope", [])
            if isinstance(normalized.get("repair_scope"), list)
            else [],
            allowed_artifacts=allowed_repair_artifacts,
        )
        normalized = _normalize_issue_v2_target_shape(
            normalized,
            candidate_ids=candidate_ids,
            candidate_by_id=candidate_by_id,
            allowed_artifacts=allowed_repair_artifacts,
        )
        if not normalized["repair_scope"]:
            normalized["repair_scope"] = _repair_scopes_from_issue_targets(
                normalized,
                allowed_artifacts=allowed_repair_artifacts,
            ) or _infer_issue_repair_scopes(
                normalized,
                candidate_ids=candidate_ids,
                candidate_by_id=candidate_by_id,
                allowed_artifacts=allowed_repair_artifacts,
            )
        issues.append(normalized)
    return issues


def _normalize_issue_v2_target_shape(
    issue: dict[str, Any],
    *,
    candidate_ids: list[str],
    candidate_by_id: dict[str, ConflictCandidate],
    allowed_artifacts: set[str],
) -> dict[str, Any]:
    """Normalize the v2 audit target fields while keeping legacy keys available."""

    normalized = dict(issue)
    issue_id = str(normalized.get("issue_id") or normalized.get("id") or "").strip()
    if issue_id:
        normalized["issue_id"] = issue_id
        normalized.setdefault("id", issue_id)
    if not str(normalized.get("description") or "").strip():
        for fallback_key in ("rationale", "summary", "resolution"):
            fallback = str(normalized.get(fallback_key) or "").strip()
            if fallback:
                normalized["description"] = fallback
                break

    repair_targets = _sanitize_audit_locators(
        normalized.get("repair_targets"),
        role="repair",
        allowed_artifacts=allowed_artifacts,
    )
    reference_targets = _sanitize_audit_locators(
        normalized.get("reference_targets"),
        role="reference",
        allowed_artifacts=allowed_artifacts,
    )
    if not repair_targets and normalized.get("repair_scope"):
        repair_targets = _audit_locators_from_repair_scopes(
            normalized.get("repair_scope"),
            role="repair",
            issue=normalized,
            candidate_ids=candidate_ids,
            candidate_by_id=candidate_by_id,
            allowed_artifacts=allowed_artifacts,
        )
    if not reference_targets:
        reference_targets = _reference_locators_from_candidate_claims(
            normalized,
            candidate_ids=candidate_ids,
            candidate_by_id=candidate_by_id,
            repair_targets=repair_targets,
            allowed_artifacts=allowed_artifacts,
        )
    if repair_targets:
        normalized["repair_targets"] = repair_targets
    if reference_targets:
        normalized["reference_targets"] = reference_targets
    if not isinstance(normalized.get("evidence"), list):
        evidence_text = str(
            normalized.get("evidence") or normalized.get("description") or ""
        ).strip()
        normalized["evidence"] = (
            [{"quote": evidence_text, "source": "adjudication"}] if evidence_text else []
        )
    if not isinstance(normalized.get("repair_intent"), dict):
        normalized["repair_intent"] = {
            "operation": "replace",
            "target_policy": "single_target",
            "rationale": str(normalized.get("resolution") or normalized.get("description") or ""),
            "preserve": [],
            "allowed_strategies": ["json_patch", "llm_patch"],
        }
    normalized.setdefault("summary", str(normalized.get("description") or "")[:120])
    normalized.setdefault("reference_targets", [])
    normalized.setdefault("repair_targets", [])
    return normalized


def _sanitize_audit_locators(
    raw_value: Any,
    *,
    role: str,
    allowed_artifacts: set[str],
) -> list[dict[str, Any]]:
    raw_items = raw_value if isinstance(raw_value, list) else []
    locators: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        target_format = str(raw.get("target_format") or "").strip() or "json_artifact"
        locator = dict(raw)
        locator["target_format"] = target_format
        locator["role"] = role
        if target_format == "json_artifact":
            artifact = _normalize_artifact_key_for_v2(locator.get("artifact"))
            if allowed_artifacts and artifact not in allowed_artifacts:
                continue
            locator["artifact"] = artifact
            if not (locator.get("json_pointer") or locator.get("field") or locator.get("claim_id")):
                continue
        locators.append(locator)
    return locators


def _audit_locators_from_repair_scopes(
    raw_scopes: Any,
    *,
    role: str,
    issue: dict[str, Any],
    candidate_ids: list[str],
    candidate_by_id: dict[str, ConflictCandidate],
    allowed_artifacts: set[str],
) -> list[dict[str, Any]]:
    scopes = _sanitize_repair_scopes(
        raw_scopes if isinstance(raw_scopes, list) else [],
        allowed_artifacts=allowed_artifacts,
    )
    issue_text = _issue_text(issue)
    locators: list[dict[str, Any]] = []
    claims: list[CoherenceClaim] = []
    for candidate_id in candidate_ids:
        candidate = candidate_by_id.get(candidate_id)
        if candidate is not None:
            claims.extend(candidate.claims)
    for scope in scopes:
        artifact = str(scope.get("artifact") or "")
        fields = [str(field) for field in scope.get("fields", []) or [] if str(field)]
        chapters = [
            number
            for number in (
                _coerce_positive_int(chapter) for chapter in scope.get("chapters", []) or []
            )
            if number > 0
        ]
        for scope_field in fields:
            claim_path = _best_claim_source_path_for_scope(
                claims,
                artifact=artifact,
                field=scope_field,
                chapters=chapters,
                issue_text=issue_text,
            )
            locators.append(
                {
                    "target_format": "json_artifact",
                    "role": role,
                    "artifact": artifact,
                    "json_pointer": claim_path,
                    "field": scope_field,
                    "chapter_number": chapters[0] if len(chapters) == 1 else None,
                    "chapter_range": chapters,
                    "quote": _best_issue_quote(issue),
                    "confidence": 0.72 if claim_path else 0.58,
                }
            )
    return locators


def _best_claim_source_path_for_scope(
    claims: list[CoherenceClaim],
    *,
    artifact: str,
    field: str,
    chapters: list[int],
    issue_text: str,
) -> str:
    chapter_set = set(chapters)
    matching: list[tuple[int, str]] = []
    for claim in claims:
        if _normalize_artifact_key_for_v2(claim.artifact) != artifact:
            continue
        if _normalize_repair_field(claim.source_field or claim.source_path) != field:
            continue
        score = 0
        claim_chapters = _chapters_for_claim(claim)
        if chapter_set and claim_chapters & chapter_set:
            score += 3
        if field and field in issue_text:
            score += 2
        if claim.claim_text and claim.claim_text[:16] in issue_text:
            score += 1
        matching.append((score, claim.source_path))
    if not matching:
        return ""
    return sorted(matching, key=lambda item: item[0], reverse=True)[0][1]


def _reference_locators_from_candidate_claims(
    issue: dict[str, Any],
    *,
    candidate_ids: list[str],
    candidate_by_id: dict[str, ConflictCandidate],
    repair_targets: list[dict[str, Any]],
    allowed_artifacts: set[str],
) -> list[dict[str, Any]]:
    repair_keys = {
        (
            str(target.get("artifact") or ""),
            str(target.get("json_pointer") or ""),
            str(target.get("field") or ""),
        )
        for target in repair_targets
        if isinstance(target, dict)
    }
    references: list[dict[str, Any]] = []
    for candidate_id in candidate_ids:
        candidate = candidate_by_id.get(candidate_id)
        if candidate is None:
            continue
        for claim in candidate.claims:
            artifact = _normalize_artifact_key_for_v2(claim.artifact)
            if allowed_artifacts and artifact not in allowed_artifacts:
                continue
            field = _normalize_repair_field(claim.source_field or claim.source_path)
            key = (artifact, claim.source_path, field)
            if key in repair_keys:
                continue
            references.append(
                {
                    "target_format": "json_artifact",
                    "role": "reference",
                    "artifact": artifact,
                    "json_pointer": claim.source_path,
                    "field": field,
                    "chapter_range": sorted(_chapters_for_claim(claim)),
                    "claim_id": claim.claim_id,
                    "quote": claim.evidence or claim.claim_text,
                    "confidence": 0.7,
                }
            )
    return references[:8]


def _repair_scopes_from_issue_targets(
    issue: dict[str, Any],
    *,
    allowed_artifacts: set[str],
) -> list[dict[str, Any]]:
    scopes: list[dict[str, Any]] = []
    issue_id = str(issue.get("id") or issue.get("issue_id") or "").strip()
    for target in issue.get("repair_targets", []) or []:
        if not isinstance(target, dict) or target.get("target_format") != "json_artifact":
            continue
        artifact = _normalize_artifact_key_for_v2(target.get("artifact"))
        if allowed_artifacts and artifact not in allowed_artifacts:
            continue
        field = _normalize_repair_field(target.get("field") or target.get("json_pointer"))
        if not field:
            continue
        chapters = _coerce_repair_scope_chapters(target)
        chapter_number = _coerce_positive_int(target.get("chapter_number"))
        if chapter_number and chapter_number not in chapters:
            chapters = [chapter_number, *chapters]
        scope = {
            "artifact": artifact,
            "chapters": list(dict.fromkeys(chapters)),
            "fields": [field],
            "operation": "field_replace",
        }
        if issue_id:
            scope["issue_ids"] = [issue_id]
        scopes.append(scope)
    return _sanitize_repair_scopes(scopes, artifact="", allowed_artifacts=allowed_artifacts)


def _best_issue_quote(issue: dict[str, Any]) -> str:
    evidence = issue.get("evidence")
    if isinstance(evidence, list):
        for item in evidence:
            if isinstance(item, dict) and item.get("quote"):
                return str(item.get("quote") or "")
            if isinstance(item, str) and item.strip():
                return item.strip()
    return str(issue.get("description") or issue.get("summary") or "")[:160]


def _infer_issue_repair_scopes(
    issue: dict[str, Any],
    *,
    candidate_ids: list[str],
    candidate_by_id: dict[str, ConflictCandidate],
    allowed_artifacts: set[str],
) -> list[dict[str, Any]]:
    """Infer a narrow repair scope from candidate source fields when the judge omits one."""
    fields_by_artifact: dict[str, set[str]] = {}
    chapters_by_artifact: dict[str, set[int]] = {}
    chapters_by_artifact_field: dict[tuple[str, str], set[int]] = {}
    candidates = [
        candidate_by_id[candidate_id]
        for candidate_id in candidate_ids
        if candidate_id in candidate_by_id
    ]
    for candidate in candidates:
        for claim in candidate.claims:
            artifact = _normalize_artifact_key_for_v2(claim.artifact)
            if allowed_artifacts and artifact not in allowed_artifacts:
                continue
            allowed_fields = set(_REPAIR_FIELDS_BY_ARTIFACT.get(artifact, []))
            if not allowed_fields:
                continue
            fields: set[str] = set()
            field = _repair_field_for_claim(claim, allowed_fields)
            if field in allowed_fields:
                fields.add(field)
            if not fields and artifact == "outline":
                fields.update(_repair_fields_for_outline_claim(claim, allowed_fields))
            if not fields and artifact == "chapter_contracts":
                fields.update(_repair_fields_for_chapter_contract_claim(claim, allowed_fields))
            if not fields:
                continue
            claim_chapters = _chapters_for_claim(claim)
            for field in fields:
                fields_by_artifact.setdefault(artifact, set()).add(field)
                chapters_by_artifact.setdefault(artifact, set()).update(claim_chapters)
                chapters_by_artifact_field.setdefault((artifact, field), set()).update(
                    claim_chapters
                )

    issue_text = _issue_text(issue)
    inferred: list[dict[str, Any]] = []
    issue_id = str(issue.get("id") or "").strip()
    for artifact, fields in fields_by_artifact.items():
        preferred = _preferred_repair_fields_from_issue_text(issue_text, artifact, fields)
        if preferred:
            fields = preferred
        if artifact == "blueprint" and len(fields) > 1 and "synopsis" in fields:
            fields = set(fields)
            fields.discard("synopsis")
        if not fields:
            continue
        chapters: set[int] = set()
        for field in fields:
            chapters.update(chapters_by_artifact_field.get((artifact, field), set()))
        if not chapters:
            chapters = set(chapters_by_artifact.get(artifact, set()))
        scope: dict[str, Any] = {
            "artifact": artifact,
            "chapters": sorted(chapters),
            "fields": sorted(fields),
            "operation": "field_replace",
        }
        if issue_id:
            scope["issue_ids"] = [issue_id]
        inferred.append(scope)
    return _sanitize_repair_scopes(inferred, artifact="", allowed_artifacts=allowed_artifacts)


def _repair_field_for_claim(claim: CoherenceClaim, allowed_fields: set[str]) -> str:
    source_field = _normalize_repair_field(claim.source_field)
    if source_field in allowed_fields:
        return source_field
    source_path = _normalize_repair_field(claim.source_path)
    if source_path in allowed_fields:
        return source_path
    return ""


def _repair_fields_for_chapter_contract_claim(
    claim: CoherenceClaim,
    allowed_fields: set[str],
) -> set[str]:
    """Infer contract subfields from structured claim semantics.

    Chapter-contract claims are often extracted from a batch window whose source
    field is the container name (``chapter_contracts``), not the concrete nested
    contract field. The ledger still carries enough semantics to route repair to
    bounded field groups without relying on project-specific story text.
    """
    fields = set(_CHAPTER_CONTRACT_REPAIR_FIELDS_BY_CLAIM_TYPE.get(claim.claim_type, set()))
    if claim.payoff_id or claim.payoff_kind or claim.claim_type in {"payoff", "promise"}:
        fields.add("promise_ops")
    if _claim_has_cognitive_payload(claim):
        fields.update({"knowledge_ops", "cognitive_constraints", "future_leak_risks"})
    if claim.claim_type == "relationship" or _claim_text_has_hint(
        claim, _RELATIONSHIP_REPAIR_HINTS
    ):
        fields.add("relationship_ops")
    if claim.claim_type == "event" or claim.event_type or claim.irreversible:
        fields.add("required_events")
    if claim.claim_type in {"state", "event", "payoff", "promise"} and _claim_text_has_hint(
        claim,
        _ITEM_REPAIR_HINTS,
    ):
        fields.add("item_ops")
    return fields & allowed_fields


def _repair_fields_for_outline_claim(
    claim: CoherenceClaim,
    allowed_fields: set[str],
) -> set[str]:
    fields = set(_OUTLINE_REPAIR_FIELDS_BY_CLAIM_TYPE.get(claim.claim_type, set()))
    if claim.payoff_id or claim.payoff_kind or claim.claim_type in {"payoff", "promise"}:
        fields.add("expected_payoffs")
    if _claim_has_cognitive_payload(claim):
        fields.update({"main_plot_points", "beats_summary", "notes"})
    if claim.claim_type == "relationship" or _claim_text_has_hint(
        claim, _RELATIONSHIP_REPAIR_HINTS
    ):
        fields.update({"goal", "main_plot_points", "notes"})
    return fields & allowed_fields


def _claim_has_cognitive_payload(claim: CoherenceClaim) -> bool:
    return bool(
        claim.cognitive_subjects
        or claim.cognitive_object
        or claim.character_knowledge_coverage
        or claim.cognitive_chapter
        or claim.public_reveal_chapter
        or claim.reveal_chapter
        or claim.foreshadow_chapters
        or claim.cognitive_level != "unaware"
        or claim.action_level != "none"
        or claim.reader_awareness != "unknown"
    )


def _claim_text_has_hint(claim: CoherenceClaim, hints: tuple[str, ...]) -> bool:
    text = " ".join(
        str(part or "")
        for part in (
            claim.claim_text,
            claim.evidence,
            claim.subject_text,
            claim.axis,
            claim.event_type,
            claim.payoff_id,
            claim.payoff_kind,
            claim.cognitive_object,
        )
    )
    return any(hint in text for hint in hints)


def _chapters_for_claim(claim: CoherenceClaim) -> set[int]:
    chapters = {number for number in claim.chapter_numbers if number > 0}
    if chapters:
        return chapters
    for chapter in (claim.cognitive_chapter, claim.reveal_chapter):
        if chapter and chapter > 0:
            chapters.add(chapter)
    if chapters:
        return chapters
    if claim.chapter_range is not None:
        if claim.chapter_range.start:
            chapters.add(claim.chapter_range.start)
        if claim.chapter_range.end:
            chapters.add(claim.chapter_range.end)
    return chapters


def _normalize_artifact_key_for_v2(value: Any) -> str:
    return str(value or "").strip().lower()


def _preferred_repair_fields_from_issue_text(
    issue_text: str,
    artifact: str,
    available_fields: set[str],
) -> set[str]:
    if artifact != "blueprint":
        return set()
    return {field for field in available_fields if field in issue_text}


def _issue_text(issue: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("description", "resolution", "summary", "change_intent"):
        value = issue.get(key)
        if value:
            parts.append(str(value))
    for key in ("evidence", "source_refs", "candidate_ids"):
        value = issue.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
        elif value:
            parts.append(str(value))
    return " ".join(parts).strip()


def _namespace_batch_issue_ids(
    issues: list[dict[str, Any]],
    repair_scope: list[dict[str, Any]],
    *,
    batch: list[ConflictCandidate],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    id_map: dict[str, str] = {}
    namespaced: list[dict[str, Any]] = []
    for index, issue in enumerate(issues, start=1):
        old_id = str(issue.get("id") or f"issue_{index}").strip()
        candidate_ids = [
            str(candidate_id)
            for candidate_id in issue.get("candidate_ids", []) or []
            if str(candidate_id)
        ]
        new_id = _namespaced_issue_id(old_id, candidate_ids, batch=batch, index=index)
        id_map[old_id] = new_id
        updated = dict(issue)
        updated["id"] = new_id
        updated["issue_id"] = new_id
        updated["repair_scope"] = [
            _remap_scope_issue_ids(scope, id_map, default_issue_id=new_id)
            for scope in (
                updated.get("repair_scope", [])
                if isinstance(updated.get("repair_scope"), list)
                else []
            )
            if isinstance(scope, dict)
        ]
        namespaced.append(updated)
    remapped_scopes = [
        _remap_scope_issue_ids(scope, id_map, default_issue_id="")
        for scope in repair_scope
        if isinstance(scope, dict)
    ]
    return namespaced, _sanitize_repair_scopes(remapped_scopes, artifact="")


def _namespaced_issue_id(
    old_id: str,
    candidate_ids: list[str],
    *,
    batch: list[ConflictCandidate],
    index: int,
) -> str:
    prefix = candidate_ids[0] if candidate_ids else (batch[0].candidate_id if batch else "batch")
    clean_prefix = re.sub(r"[^0-9A-Za-z]+", "_", prefix).strip("_").lower() or "batch"
    clean_id = re.sub(r"[^0-9A-Za-z]+", "_", old_id or f"issue_{index}").strip("_").lower()
    if clean_id.startswith(f"{clean_prefix}_"):
        return clean_id
    return f"{clean_prefix}_{clean_id or f'issue_{index}'}"


def _remap_scope_issue_ids(
    scope: dict[str, Any],
    id_map: dict[str, str],
    *,
    default_issue_id: str,
) -> dict[str, Any]:
    updated = dict(scope)
    raw_ids = updated.get("issue_ids") or updated.get("issues")
    ids = [str(item) for item in raw_ids] if isinstance(raw_ids, list) else []
    mapped = [id_map[item] for item in ids if item in id_map]
    if not mapped and default_issue_id:
        mapped = [default_issue_id]
    if mapped:
        updated["issue_ids"] = list(dict.fromkeys(mapped))
    return updated


def _dedupe_repair_scopes(scopes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for scope in _sanitize_repair_scopes(scopes, artifact=""):
        key = json.dumps(scope, ensure_ascii=False, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        result.append(scope)
    return result


def _sanitize_repair_scopes(
    raw_scopes: list[Any],
    *,
    artifact: str = "",
    allowed_artifacts: set[str] | None = None,
) -> list[dict[str, Any]]:
    scopes: list[dict[str, Any]] = []
    allowed_artifacts = {item for item in (allowed_artifacts or set()) if item}
    for raw in raw_scopes:
        if not isinstance(raw, dict):
            continue
        scope_artifact = _normalize_artifact_key_for_v2(raw.get("artifact") or artifact or "")
        if not scope_artifact or scope_artifact not in _REPAIR_FIELDS_BY_ARTIFACT:
            continue
        if allowed_artifacts and scope_artifact not in allowed_artifacts:
            continue
        allowed_fields = set(_REPAIR_FIELDS_BY_ARTIFACT.get(scope_artifact, []))
        raw_fields_value = raw.get("fields")
        raw_fields: list[Any] = raw_fields_value if isinstance(raw_fields_value, list) else []
        fields = [
            root
            for root in (_normalize_repair_field(field) for field in raw_fields)
            if root and root in allowed_fields
        ]
        if not fields:
            continue
        chapters = _coerce_repair_scope_chapters(raw)
        if scope_artifact != "blueprint" and not chapters:
            continue
        operation = str(raw.get("operation") or "field_replace").strip().lower()
        if operation not in {"field_replace", "replace"}:
            continue
        issue_ids = _coerce_string_list(raw.get("issue_ids") or raw.get("issues"))
        scope = {
            "artifact": scope_artifact,
            "chapters": chapters,
            "fields": list(dict.fromkeys(fields)),
            "operation": "field_replace",
        }
        if issue_ids:
            scope["issue_ids"] = issue_ids
        scopes.append(scope)
    return scopes


def _coerce_repair_scope_chapters(raw: dict[str, Any]) -> list[int]:
    chapters = _coerce_positive_ints(raw.get("chapters") or raw.get("chapter_numbers"))
    if chapters:
        return chapters
    return _coerce_repair_scope_chapter_range(raw.get("chapter_range"))


def _coerce_repair_scope_chapter_range(value: Any) -> list[int]:
    if value is None:
        return []
    if isinstance(value, dict):
        start = _coerce_positive_int(value.get("start"))
        end = _coerce_positive_int(value.get("end"))
    else:
        values = value if isinstance(value, list) else [value]
        numbers = _coerce_positive_ints(values)
        if not numbers:
            return []
        start = min(numbers)
        end = max(numbers)
    if not start and not end:
        return []
    if not start:
        start = end
    if not end:
        end = start
    start, end = min(start, end), max(start, end)
    if end - start + 1 > _REPAIR_SCOPE_RANGE_EXPANSION_LIMIT:
        return sorted({start, end})
    return list(range(start, end + 1))


def _normalize_repair_field(field: Any) -> str:
    return str(field or "").strip().strip("/").split("/", 1)[0]


def _claim_id_from_indexed_text(value: str) -> str:
    match = _CLAIM_ID_RE.match(str(value or ""))
    return match.group(1) if match else ""


def _issue_sort_key(issue: dict[str, Any]) -> tuple[int, str]:
    rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    severity = str(issue.get("severity") or "medium").lower()
    return (-rank.get(severity, 2), str(issue.get("description") or ""))


def _count_high_or_critical(issues: list[dict[str, Any]]) -> int:
    return sum(
        1 for issue in issues if str(issue.get("severity", "")).lower() in {"high", "critical"}
    )


def _setting_int(settings: Any, name: str, default: int) -> int:
    raw = getattr(settings, name, None)
    if raw is None:
        return max(0, default)
    try:
        # Keep an explicit 0 intact; callers decide whether 0 needs a fallback.
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return max(0, value)


def _adaptive_recheck_claim_limit(settings: Any, focus_numbers: list[int]) -> int:
    """Scale the focus-recheck claim cap with the window size.

    Author: Novel Forge Team

    A fixed 240-claim cap made large chapter windows (e.g. 12 chapters with
    dense contracts) fail deterministically with
    ``focus_recheck_claim_limit`` even though the recheck itself was healthy.
    The effective cap is now ``max(base, per_chapter * window_size)`` so
    normal large windows pass while a genuinely polluted ledger still trips
    the guard.  ``per_chapter == 0`` restores the legacy fixed behaviour.
    """
    base_limit = _setting_int(settings, "init_coherence_recheck_max_claims", 240)
    per_chapter = _setting_int(settings, "init_coherence_recheck_claims_per_chapter", 40)
    if per_chapter <= 0 or not focus_numbers:
        return base_limit
    return max(base_limit, per_chapter * len(focus_numbers))


def _bounded_parallel_setting(
    settings: Any,
    name: str,
    *,
    default: int,
    upper: int,
) -> int:
    return max(1, min(max(1, upper), _setting_int(settings, name, default)))
