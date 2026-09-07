"""Claim extraction helpers for init coherence v2."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Any, Callable, cast

from novel_forge.common.constants import TaskType
from novel_forge.core.schemas.init_coherence import CoherenceClaim, CoherenceClaimBatch
from novel_forge.pipeline.long.services.init.init_entity_references import (
    adjudicate_init_claim_entity_references,
)
from novel_forge.pipeline.token_budget import calculate_route_aware_max_tokens


def _claim_prompt_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Project only categorical guidance into Claim extraction prompts.

    ``conflict_lens`` and ``extraction_guidance`` are model-authored prose.
    Letting them travel back into a source-grounded extractor allowed one
    misspelled profile name to override the canonical name in the current
    outline chunk.  Entity facts and names must come from ``chunk.payload``;
    the profile may only contribute a compact ontology.
    """

    ontology = profile.get("project_ontology")
    ontology = ontology if isinstance(ontology, dict) else {}
    safe_ontology_keys = (
        "domains",
        "entity_types",
        "state_axes",
        "relationship_axes",
        "irreversible_event_markers",
        "temporal_markers",
        "terminology",
    )
    return {
        "genre_tags": list(profile.get("genre_tags") or [])[:12],
        "narrative_modes": list(profile.get("narrative_modes") or [])[:12],
        "project_ontology": {
            key: ontology[key] for key in safe_ontology_keys if key in ontology
        },
    }


@dataclass(frozen=True)
class ClaimExtractionDeps:
    """Callbacks owned by ``init_coherence_v2`` and used by extraction helpers."""

    build_blueprint_holistic_chunks: Callable[[Any, dict[str, Any] | None], list[Any]]
    bounded_parallel_setting: Callable[..., int]
    claim_extraction_target_output_chars: Callable[[Any, TaskType, int], int]
    claim_limit_for_chunk: Callable[[TaskType, Any], int]
    dedupe_claims: Callable[[list[CoherenceClaim]], list[CoherenceClaim]]
    emit_step: Callable[[Any, str, dict[str, Any]], None]
    entity_catalog_from_profile: Callable[[dict[str, Any] | None], dict[str, Any] | None]
    increment_init_metric: Callable[..., None]
    init_efficiency_metrics: Callable[[Any], dict[str, Any]]
    load_cached_claim_batch: Callable[..., CoherenceClaimBatch | None]
    logger: Any
    normalize_claim_payload: Callable[..., dict[str, Any]]
    postprocess_claim_batch: Callable[..., tuple[list[CoherenceClaim], int, int]]
    save_cached_claim_batch: Callable[..., None]


async def extract_stage_claims(
    ctx: Any,
    *,
    stage: str,
    profile: dict[str, Any],
    artifacts: dict[str, dict[str, Any]],
    chunks: list[Any],
    focus_chapters: list[int] | None,
    deps: ClaimExtractionDeps,
) -> list[CoherenceClaim]:
    if (
        stage != "blueprint_coherence"
        or focus_chapters
        or not bool(getattr(ctx.settings, "init_blueprint_holistic_claims_enabled", True))
    ):
        claims = await extract_claims(
            ctx,
            stage=stage,
            profile=profile,
            chunks=chunks,
            deps=deps,
        )
        return await adjudicate_init_claim_entity_references(
            ctx,
            profile=profile,
            claims=claims,
            stage=stage,
        )

    blueprint_payload = artifacts.get("blueprint")
    holistic_chunks = deps.build_blueprint_holistic_chunks(ctx.settings, blueprint_payload)
    if not holistic_chunks:
        claims = await extract_claims(
            ctx,
            stage=stage,
            profile=profile,
            chunks=chunks,
            deps=deps,
        )
        return await adjudicate_init_claim_entity_references(
            ctx,
            profile=profile,
            claims=claims,
            stage=stage,
        )

    shard_task = asyncio.create_task(
        extract_claims(ctx, stage=stage, profile=profile, chunks=chunks, deps=deps)
    )
    holistic_task = asyncio.create_task(
        extract_claims(
            ctx,
            stage=stage,
            profile=profile,
            chunks=holistic_chunks,
            task_type=TaskType.EXTRACT_BLUEPRINT_HOLISTIC_CLAIMS,
            deps=deps,
        )
    )
    shard_result, holistic_result = await asyncio.gather(
        shard_task,
        holistic_task,
        return_exceptions=True,
    )
    if isinstance(shard_result, Exception):
        raise shard_result
    shard_claims = cast(list[CoherenceClaim], shard_result)
    if isinstance(holistic_result, Exception):
        exc = holistic_result
        deps.logger.warning("blueprint_holistic_claims_failed | error=%s", exc)
        deps.emit_step(
            ctx,
            "extract_blueprint_holistic_claims_failed",
            {
                "stage": stage,
                "artifact": "blueprint",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "fallback": "sharded_claims",
            },
        )
        holistic_claims: list[CoherenceClaim] = []
    else:
        holistic_claims = cast(list[CoherenceClaim], holistic_result)
    merged = deps.dedupe_claims([*holistic_claims, *shard_claims])
    return await adjudicate_init_claim_entity_references(
        ctx,
        profile=profile,
        claims=merged,
        stage=stage,
    )


async def extract_claims(
    ctx: Any,
    *,
    stage: str,
    profile: dict[str, Any],
    chunks: list[Any],
    deps: ClaimExtractionDeps,
    task_type: TaskType = TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
    concurrency_limiter: asyncio.Semaphore | None = None,
) -> list[CoherenceClaim]:
    total = len(chunks)
    if not chunks:
        return []
    metrics = deps.init_efficiency_metrics(ctx)
    metrics["claim_chunks"] = int(metrics.get("claim_chunks", 0) or 0) + total
    chunks_by_stage = metrics.setdefault("claim_chunks_by_stage", {})
    if isinstance(chunks_by_stage, dict):
        chunks_by_stage[stage] = int(chunks_by_stage.get(stage, 0) or 0) + total
    chunks_by_artifact = metrics.setdefault("claim_chunks_by_artifact", {})
    if isinstance(chunks_by_artifact, dict):
        for chunk in chunks:
            key = str(chunk.artifact)
            chunks_by_artifact[key] = int(chunks_by_artifact.get(key, 0) or 0) + 1
    deps.emit_step(
        ctx,
        "extract_init_coherence_claims_start",
        {
            "stage": stage,
            "task_type": task_type.value,
            "batch_total": total,
            "batches_done": 0,
            "claims": 0,
        },
    )

    max_parallel = deps.bounded_parallel_setting(
        ctx.settings,
        "init_coherence_claim_max_parallel",
        default=2,
        upper=8,
    )
    completed_batches = 0
    completed_claims = 0
    progress_lock = asyncio.Lock()

    async def _record_progress(
        *,
        index: int,
        chunk: Any,
        claim_count: int,
        cached: bool,
        fallback_claim_count: int = 0,
        filtered_claim_count: int = 0,
    ) -> None:
        nonlocal completed_batches, completed_claims
        async with progress_lock:
            completed_batches += 1
            completed_claims += claim_count
            payload: dict[str, Any] = {
                "stage": stage,
                "task_type": task_type.value,
                "artifact": chunk.artifact,
                "extraction_mode": chunk.extraction_mode,
                "batch": completed_batches,
                "batch_index": index,
                "batch_total": total,
                "batches_done": completed_batches,
                "claims": completed_claims,
                "max_parallel": max_parallel,
                "cached": cached,
            }
            if fallback_claim_count:
                payload["fallback"] = "local_outline_claims"
                payload["fallback_claims"] = fallback_claim_count
            if filtered_claim_count:
                payload["filtered_claims"] = filtered_claim_count
            deps.emit_step(ctx, "extract_init_coherence_claims", payload)

    async def _extract_one(index: int, chunk: Any) -> CoherenceClaimBatch:
        prompt_profile = _claim_prompt_profile(profile)
        configured_split_depth = getattr(
            ctx.settings,
            "init_coherence_claim_split_max_depth",
            3,
        )
        try:
            parsed_split_depth = int(configured_split_depth)
        except (TypeError, ValueError):
            parsed_split_depth = 3
        max_split_depth = max(0, min(6, parsed_split_depth))

        async def _load_or_call(current_chunk: Any) -> tuple[CoherenceClaimBatch, bool, int, int]:
            cached_batch = deps.load_cached_claim_batch(
                ctx,
                stage=stage,
                profile=prompt_profile,
                chunk=current_chunk,
                task_type=task_type,
            )
            if cached_batch is not None:
                claims, filtered_count, fallback_count = deps.postprocess_claim_batch(
                    cached_batch.claims,
                    chunk=current_chunk,
                )
                normalized_batch = cached_batch.model_copy(update={"claims": claims})
                if filtered_count or fallback_count:
                    deps.save_cached_claim_batch(
                        ctx,
                        stage=stage,
                        profile=prompt_profile,
                        chunk=current_chunk,
                        batch=normalized_batch,
                        task_type=task_type,
                    )
                deps.increment_init_metric(ctx, "claim_cache_hits")
                return normalized_batch, True, filtered_count, fallback_count

            deps.increment_init_metric(ctx, "claim_cache_misses")
            claim_limit = deps.claim_limit_for_chunk(task_type, current_chunk)
            target_output_chars = deps.claim_extraction_target_output_chars(
                ctx.settings,
                task_type,
                claim_limit,
            )
            response = await ctx.call_with_retry(
                task_type,
                {
                    "coherence_profile": prompt_profile,
                    "artifact": current_chunk.artifact,
                    "chunk": {
                        "chunk_id": current_chunk.chunk_id,
                        "source_path": current_chunk.source_path,
                        "source_field": current_chunk.source_field,
                        "chapter_numbers": current_chunk.chapter_numbers,
                        "extraction_mode": current_chunk.extraction_mode,
                        "evidence_refs": current_chunk.evidence_refs,
                        "payload": current_chunk.payload,
                    },
                    "claim_batch_index": index,
                    "claim_batch_total": total,
                    "claim_limit": claim_limit,
                },
                max_tokens=calculate_route_aware_max_tokens(
                    ctx.router,
                    task_type,
                    target_output_chars,
                    prompt_overhead=5600,
                    min_tokens=2048,
                ),
                temperature=getattr(ctx.settings, f"temp_{task_type.value}", 0.1),
                required_keys=("claims", "coverage_status", "unprocessed_source_refs"),
                max_retries=3,
            )
            raw_claims = response.get("claims") if isinstance(response, dict) else []
            if not isinstance(raw_claims, list):
                raw_claims = []
            chunk_claims: list[CoherenceClaim] = []
            grounding_refs: list[str] = []
            for claim_index, raw_claim in enumerate(raw_claims, start=1):
                if not isinstance(raw_claim, dict):
                    continue
                normalized = deps.normalize_claim_payload(
                    raw_claim,
                    chunk=current_chunk,
                    claim_index=claim_index,
                    entity_catalog=None,
                )
                claim = CoherenceClaim.model_validate(normalized)
                metadata = claim.metadata if isinstance(claim.metadata, dict) else {}
                if metadata.get("source_path_corrected_from_chunk"):
                    grounding_refs.append(
                        str(metadata.get("raw_source_path") or current_chunk.source_path)
                    )
                if metadata.get("artifact_corrected_from_chunk"):
                    grounding_refs.append(
                        str(metadata.get("raw_artifact") or current_chunk.source_path)
                    )
                if not str(raw_claim.get("evidence") or "").strip():
                    grounding_refs.append(f"{current_chunk.source_path}#claim_{claim_index}")
                chunk_claims.append(claim)
            chunk_claims, filtered_count, fallback_count = deps.postprocess_claim_batch(
                chunk_claims,
                chunk=current_chunk,
            )
            declared_coverage = (
                response.get("coverage_status", "uncertain")
                if isinstance(response, dict)
                else "uncertain"
            )
            unprocessed_refs = _normalize_unprocessed_source_refs(
                response.get("unprocessed_source_refs", [])
                if isinstance(response, dict)
                else [],
                chunk=current_chunk,
            )
            if grounding_refs:
                declared_coverage = "uncertain"
                unprocessed_refs.extend(grounding_refs)
            if declared_coverage != "complete" and not unprocessed_refs:
                unprocessed_refs.append(str(current_chunk.source_path))
            if declared_coverage == "complete":
                unprocessed_refs = []
            batch = CoherenceClaimBatch(
                claims=chunk_claims,
                coverage_status=declared_coverage,
                unprocessed_source_refs=list(dict.fromkeys(unprocessed_refs)),
                summary=str(response.get("summary") or "") if isinstance(response, dict) else "",
            )
            deps.save_cached_claim_batch(
                ctx,
                stage=stage,
                profile=prompt_profile,
                chunk=current_chunk,
                batch=batch,
                task_type=task_type,
            )
            return batch, False, filtered_count, fallback_count

        async def _extract_complete(
            current_chunk: Any,
            *,
            depth: int,
        ) -> tuple[CoherenceClaimBatch, bool, int, int, int]:
            batch, cached, filtered_count, fallback_count = await _load_or_call(current_chunk)
            if batch.coverage_status == "complete":
                return batch, cached, filtered_count, fallback_count, depth
            child_chunks = (
                _split_claim_chunk(current_chunk, depth=depth + 1)
                if depth < max_split_depth
                else []
            )
            if not child_chunks:
                return batch, cached, filtered_count, fallback_count, depth
            child_results = [
                await _extract_complete(child, depth=depth + 1) for child in child_chunks
            ]
            merged = _merge_claim_batches(
                [result[0] for result in child_results],
                dedupe_claims=deps.dedupe_claims,
            )
            deps.save_cached_claim_batch(
                ctx,
                stage=stage,
                profile=prompt_profile,
                chunk=current_chunk,
                batch=merged,
                task_type=task_type,
            )
            return (
                merged,
                cached and all(result[1] for result in child_results),
                sum(result[2] for result in child_results),
                sum(result[3] for result in child_results),
                max(result[4] for result in child_results),
            )

        batch, cached, filtered_count, fallback_count, split_depth = await _extract_complete(
            chunk,
            depth=0,
        )
        await _record_progress(
            index=index,
            chunk=chunk,
            claim_count=len(batch.claims),
            cached=cached,
            fallback_claim_count=fallback_count,
            filtered_claim_count=filtered_count,
        )
        _record_claim_coverage(
            ctx,
            stage=stage,
            task_type=task_type,
            chunk=chunk,
            batch=batch,
            cached=cached,
            split_depth=split_depth,
        )
        return batch

    if concurrency_limiter is not None:

        async def _run_shared_limited(index: int, chunk: Any) -> CoherenceClaimBatch:
            async with concurrency_limiter:
                return await _extract_one(index, chunk)

        batches = await asyncio.gather(
            *[_run_shared_limited(index, chunk) for index, chunk in enumerate(chunks, start=1)]
        )
    elif max_parallel <= 1 or total <= 1:
        batches = [await _extract_one(index, chunk) for index, chunk in enumerate(chunks, start=1)]
    else:
        semaphore = asyncio.Semaphore(min(max_parallel, total))

        async def _run_limited(index: int, chunk: Any) -> CoherenceClaimBatch:
            async with semaphore:
                return await _extract_one(index, chunk)

        batches = await asyncio.gather(
            *[_run_limited(index, chunk) for index, chunk in enumerate(chunks, start=1)]
        )

    claims = [claim for batch in batches for claim in batch.claims]
    return deps.dedupe_claims(claims)


def _record_claim_coverage(
    ctx: Any,
    *,
    stage: str,
    task_type: TaskType,
    chunk: Any,
    batch: CoherenceClaimBatch,
    cached: bool,
    split_depth: int,
) -> None:
    registry = getattr(ctx, "_init_coherence_claim_coverage", None)
    if not isinstance(registry, dict):
        registry = {}
        ctx._init_coherence_claim_coverage = registry
    records = registry.setdefault(stage, [])
    records.append(
        {
            "task_type": task_type.value,
            "artifact": str(chunk.artifact),
            "chunk_id": str(chunk.chunk_id),
            "source_path": str(chunk.source_path),
            "coverage_status": batch.coverage_status,
            "unprocessed_source_refs": list(batch.unprocessed_source_refs),
            "claim_count": len(batch.claims),
            "cached": cached,
            "split_depth": split_depth,
        }
    )


def _merge_claim_batches(
    batches: list[CoherenceClaimBatch],
    *,
    dedupe_claims: Callable[[list[CoherenceClaim]], list[CoherenceClaim]],
) -> CoherenceClaimBatch:
    claims = dedupe_claims([claim for batch in batches for claim in batch.claims])
    unresolved = list(
        dict.fromkeys(ref for batch in batches for ref in batch.unprocessed_source_refs if ref)
    )
    statuses = {batch.coverage_status for batch in batches}
    if statuses == {"complete"}:
        status = "complete"
        unresolved = []
    elif "partial" in statuses:
        status = "partial"
    else:
        status = "uncertain"
    return CoherenceClaimBatch(
        claims=claims,
        coverage_status=status,
        unprocessed_source_refs=unresolved,
        summary="结构性拆批后的 claims 覆盖结果。",
    )


def _split_claim_chunk(chunk: Any, *, depth: int) -> list[Any]:
    split_payload = _split_payload_value(chunk.payload)
    if split_payload is None:
        return []
    left, right = split_payload
    return [
        replace(
            chunk,
            chunk_id=f"{chunk.chunk_id}__coverage_{depth}_{suffix}",
            payload=payload,
            extraction_mode=f"{chunk.extraction_mode}_coverage_split",
        )
        for suffix, payload in (("a", left), ("b", right))
    ]


def _split_payload_value(value: Any) -> tuple[Any, Any] | None:
    """Split structure for coverage only; never interpret narrative meaning."""

    if isinstance(value, list) and len(value) >= 2:
        midpoint = len(value) // 2
        return value[:midpoint], value[midpoint:]
    if isinstance(value, dict):
        for key, child in value.items():
            split_child = _split_payload_value(child)
            if split_child is None:
                continue
            left = dict(value)
            right = dict(value)
            left[key], right[key] = split_child
            return left, right
        if len(value) >= 2:
            items = list(value.items())
            midpoint = len(items) // 2
            return dict(items[:midpoint]), dict(items[midpoint:])
    if isinstance(value, str) and len(value) >= 800:
        midpoint = len(value) // 2
        boundaries = [value.rfind("\n", 0, midpoint), value.rfind("。", 0, midpoint)]
        boundary = max(boundaries)
        if boundary < max(1, midpoint // 2):
            boundary = midpoint
        else:
            boundary += 1
        return value[:boundary], value[boundary:]
    return None


def _normalize_unprocessed_source_refs(raw_refs: Any, *, chunk: Any) -> list[str]:
    values = raw_refs if isinstance(raw_refs, list) else [raw_refs]
    allowed_roots = [
        str(ref).strip()
        for ref in [chunk.source_path, *list(chunk.evidence_refs or [])]
        if str(ref).strip()
    ]
    normalized: list[str] = []
    for value in values:
        ref = str(value or "").strip()
        if not ref:
            continue
        if any(ref == root or ref.startswith(f"{root}/") for root in allowed_roots):
            normalized.append(ref)
        else:
            normalized.append(str(chunk.source_path))
    return list(dict.fromkeys(normalized))
