"""Small content-validated checkpoints for expensive initialization batches."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from novel_forge.pipeline.artifact_manifest import STATUS_SUCCEEDED, ArtifactManifest

INIT_BATCH_CHECKPOINT_SCHEMA_VERSION = 1
INIT_BATCH_CHECKPOINT_DIR = "init_batches"


def _safe_component(value: str) -> str:
    raw = str(value or "").strip() or "batch"
    readable = re.sub(r"[^0-9A-Za-z_.-]+", "_", raw).strip("._")[:64] or "batch"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
    return f"{readable}-{digest}"


def _result_hash(result: dict[str, Any]) -> str:
    encoded = json.dumps(
        result,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _manifest_artifact(namespace: str, batch_id: str) -> str:
    return f"init_batch:{_safe_component(namespace)}:{_safe_component(batch_id)}"


def init_batch_checkpoint_path(ctx: Any, *, namespace: str, batch_id: str) -> Any | None:
    """Return a bounded fixed slot for one logical initialization batch."""

    layout = getattr(ctx, "layout", None)
    states_dir = getattr(layout, "states_dir", None)
    if states_dir is None:
        return None
    return (
        states_dir
        / INIT_BATCH_CHECKPOINT_DIR
        / _safe_component(namespace)
        / f"{_safe_component(batch_id)}.json"
    )


def load_init_batch_checkpoint(
    ctx: Any,
    *,
    namespace: str,
    batch_id: str,
    input_hash: str,
) -> dict[str, Any] | None:
    """Load one validated result, returning a cache miss for stale/corrupt data."""

    storage = getattr(ctx, "storage", None)
    path = init_batch_checkpoint_path(ctx, namespace=namespace, batch_id=batch_id)
    if storage is None or path is None:
        return None
    try:
        if not storage.exists(path):
            return None
        payload = storage.load_json(path)
        result = payload.get("result")
        if (
            payload.get("schema_version") != INIT_BATCH_CHECKPOINT_SCHEMA_VERSION
            or payload.get("namespace") != namespace
            or payload.get("batch_id") != batch_id
            or payload.get("input_hash") != input_hash
            or not isinstance(result, dict)
            or payload.get("result_hash") != _result_hash(result)
        ):
            return None
        manifest = ArtifactManifest(storage, ctx.layout)
        artifact = _manifest_artifact(namespace, batch_id)
        result_hash = str(payload["result_hash"])
        record = manifest.matching_record(
            artifact,
            input_hashes={"batch": input_hash},
            output_hashes={"result": result_hash},
            statuses={STATUS_SUCCEEDED},
            allow_reusable_failure=False,
        )
        if record is None and manifest.get(artifact) is not None:
            return None
        if record is None:
            manifest.record_success(
                artifact=artifact,
                workflow="init_long",
                step=namespace,
                input_hashes={"batch": input_hash},
                output_hashes={"result": result_hash},
                paths={"checkpoint": str(path)},
                metadata={"batch_id": batch_id, "migrated_legacy_checkpoint": True},
                input_signature=input_hash,
                schema_version=2,
                workflow_version="init.batch.v2",
            )
        return result
    except Exception:
        return None


def save_init_batch_checkpoint(
    ctx: Any,
    *,
    namespace: str,
    batch_id: str,
    input_hash: str,
    result: dict[str, Any],
) -> bool:
    """Atomically replace one batch slot after domain validation succeeds."""

    storage = getattr(ctx, "storage", None)
    path = init_batch_checkpoint_path(ctx, namespace=namespace, batch_id=batch_id)
    if storage is None or path is None:
        return False
    try:
        result_hash = _result_hash(result)
        storage.save_json(
            path,
            {
                "schema_version": INIT_BATCH_CHECKPOINT_SCHEMA_VERSION,
                "namespace": namespace,
                "batch_id": batch_id,
                "input_hash": input_hash,
                "result_hash": result_hash,
                "result": result,
            },
        )
        ArtifactManifest(storage, ctx.layout).record_success(
            artifact=_manifest_artifact(namespace, batch_id),
            workflow="init_long",
            step=namespace,
            input_hashes={"batch": input_hash},
            output_hashes={"result": result_hash},
            paths={"checkpoint": str(path)},
            metadata={"batch_id": batch_id},
            input_signature=input_hash,
            schema_version=2,
            workflow_version="init.batch.v2",
        )
        return True
    except Exception:
        return False
