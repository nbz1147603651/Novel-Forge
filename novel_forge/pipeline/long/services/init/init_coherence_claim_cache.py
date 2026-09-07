"""Claim batch cache IO helpers for initialization coherence v2."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from typing import Any

from novel_forge.common.constants import TaskType
from novel_forge.core.schemas.init_coherence import CoherenceClaim, CoherenceClaimBatch

CLAIM_BATCH_CACHE_DIR = "init_coherence_claim_batches"
CLAIM_BATCH_CACHE_SCHEMA_VERSION = 10


def claim_batch_cache_paths(
    ctx: Any,
    *,
    stage: str,
    profile: dict[str, Any],
    chunk: Any,
    task_type: TaskType = TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
    stable_payload_hash: Callable[[Any], str],
    cache_dir: str = CLAIM_BATCH_CACHE_DIR,
) -> tuple[Any, str, str] | None:
    """Return the cache path plus profile/chunk hashes for a claim extraction chunk."""
    del stage
    storage = getattr(ctx, "storage", None)
    layout = getattr(ctx, "layout", None)
    if storage is None or layout is None:
        return None
    profile_hash = stable_payload_hash(
        {
            "profile": profile,
            "compiler_fingerprint": str(
                getattr(ctx, "_init_coherence_compiler_fingerprint", "") or ""
            ),
        }
    )
    chunk_hash = stable_payload_hash(
        {
            "artifact": chunk.artifact,
            "task_type": task_type.value,
            "chunk_id": chunk.chunk_id,
            "source_path": chunk.source_path,
            "source_field": chunk.source_field,
            "chapter_numbers": chunk.chapter_numbers,
            "extraction_mode": chunk.extraction_mode,
            "evidence_refs": chunk.evidence_refs,
            "payload": chunk.payload,
        }
    )
    artifact_safe_parts = [
        re.sub(r"[^A-Za-z0-9_.-]+", "_", str(part)).strip("._") or "part"
        for part in ("v3", task_type.value, chunk.artifact, chunk.chunk_id)
    ]
    artifact_digest = hashlib.sha256(
        f"{profile_hash}:{task_type.value}:{chunk.artifact}:{chunk_hash}".encode("utf-8")
    ).hexdigest()[:16]
    artifact_filename = "_".join([*artifact_safe_parts, artifact_digest]) + ".json"

    return (
        layout.memory_dir / cache_dir / artifact_filename,
        profile_hash,
        chunk_hash,
    )


def load_cached_claim_batch(
    ctx: Any,
    *,
    stage: str,
    profile: dict[str, Any],
    chunk: Any,
    task_type: TaskType = TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
    stable_payload_hash: Callable[[Any], str],
    normalize_claim_payload: Callable[..., dict[str, Any]],
    entity_catalog_from_profile: Callable[[dict[str, Any] | None], dict[str, Any] | None],
    logger: Any,
    cache_dir: str = CLAIM_BATCH_CACHE_DIR,
    schema_version: int = CLAIM_BATCH_CACHE_SCHEMA_VERSION,
) -> CoherenceClaimBatch | None:
    """Load and validate a cached claim batch for a chunk."""
    cache_info = claim_batch_cache_paths(
        ctx,
        stage=stage,
        profile=profile,
        chunk=chunk,
        task_type=task_type,
        stable_payload_hash=stable_payload_hash,
        cache_dir=cache_dir,
    )
    if cache_info is None:
        return None
    primary_path, profile_hash, chunk_hash = cache_info
    try:
        if not ctx.storage.exists(primary_path):
            return None
        payload = ctx.storage.load_json(primary_path)
        if (
            payload.get("schema_version") != schema_version
            or payload.get("profile_hash") != profile_hash
            or payload.get("chunk_hash") != chunk_hash
            or payload.get("artifact") != chunk.artifact
            or payload.get("task_type") != task_type.value
        ):
            return None
        raw_claims = payload.get("claims")
        if not isinstance(raw_claims, list):
            return None
        claims: list[CoherenceClaim] = []
        entity_catalog = entity_catalog_from_profile(profile)
        for claim_index, raw_claim in enumerate(raw_claims, start=1):
            if not isinstance(raw_claim, dict):
                continue
            normalized = normalize_claim_payload(
                raw_claim,
                chunk=chunk,
                claim_index=claim_index,
                entity_catalog=entity_catalog,
            )
            claims.append(CoherenceClaim.model_validate(normalized))
        return CoherenceClaimBatch(
            claims=claims,
            coverage_status=payload.get("coverage_status", "uncertain"),
            unprocessed_source_refs=payload.get("unprocessed_source_refs", []),
            summary=str(payload.get("summary") or ""),
        )
    except Exception as exc:
        logger.debug(
            "init_coherence_claim_batch_cache_load_failed | path=%s | error=%s",
            primary_path,
            exc,
        )
        return None


def save_cached_claim_batch(
    ctx: Any,
    *,
    stage: str,
    profile: dict[str, Any],
    chunk: Any,
    batch: CoherenceClaimBatch,
    task_type: TaskType = TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
    stable_payload_hash: Callable[[Any], str],
    logger: Any,
    cache_dir: str = CLAIM_BATCH_CACHE_DIR,
    schema_version: int = CLAIM_BATCH_CACHE_SCHEMA_VERSION,
) -> None:
    """Persist a validated claim batch for a chunk."""
    cache_info = claim_batch_cache_paths(
        ctx,
        stage=stage,
        profile=profile,
        chunk=chunk,
        task_type=task_type,
        stable_payload_hash=stable_payload_hash,
        cache_dir=cache_dir,
    )
    if cache_info is None:
        return
    path, profile_hash, chunk_hash = cache_info
    try:
        ctx.storage.save_json(
            path,
            {
                "schema_version": schema_version,
                "artifact": chunk.artifact,
                "task_type": task_type.value,
                "chunk_id": chunk.chunk_id,
                "profile_hash": profile_hash,
                "chunk_hash": chunk_hash,
                "claims": [claim.model_dump(mode="json") for claim in batch.claims],
                "coverage_status": batch.coverage_status,
                "unprocessed_source_refs": list(batch.unprocessed_source_refs),
                "summary": batch.summary,
            },
        )
    except Exception as exc:
        logger.debug("init_coherence_claim_batch_cache_save_failed | path=%s | error=%s", path, exc)
