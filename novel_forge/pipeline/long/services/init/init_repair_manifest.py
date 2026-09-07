"""ArtifactManifest bookkeeping shared by init repair and resume paths."""

from __future__ import annotations

import logging
from typing import Any

from novel_forge.pipeline.artifact_manifest import manifest_for_context
from novel_forge.pipeline.long.services.init.init_coherence import normalize_artifact_key
from novel_forge.pipeline.long.services.init.init_context import InitLongServiceContext
from novel_forge.pipeline.long.services.init.init_value_helpers import (
    _init_coherence_list,
    _safe_init_int,
)
from novel_forge.pipeline.long.services.init_repair import InitRepairOutcome

_log = logging.getLogger(__name__)


def _record_init_repair_manifest_round(
    ctx: InitLongServiceContext,
    repair_record: dict[str, Any],
) -> None:
    artifact = normalize_artifact_key(repair_record.get("artifact"))
    if not artifact:
        return
    issue_ids = [
        str(item) for item in _init_coherence_list(repair_record.get("source_issue_ids")) if item
    ]
    if not issue_ids:
        return
    try:
        manifest_for_context(ctx).record_repair_round(
            artifact=f"init_repair:{artifact}",
            workflow="init_long",
            issue_ids=issue_ids,
            round_index=_safe_init_int(repair_record.get("round")),
        )
    except Exception as exc:
        _log.warning(
            "init_repair_manifest_record_failed | artifact=%s | error=%s",
            artifact,
            exc,
        )


def _persist_init_repair_outcome(
    ctx: InitLongServiceContext,
    *,
    artifact: str,
    outcome: InitRepairOutcome,
) -> None:
    """Persist repair provenance beside init artifacts for later diagnostics."""
    payload = {
        "artifact": artifact,
        "repaired": outcome.repaired,
        "report": outcome.report.to_dict(),
        "attempts": outcome.attempts_as_dicts(),
    }
    path = ctx.layout.plans_dir / f"{artifact}_repair.json"
    try:
        ctx.storage.save_json(path, payload)
    except Exception as exc:
        _log.warning(
            "init_repair_report_persist_failed | artifact=%s | path=%s | error=%s",
            artifact,
            path,
            exc,
        )


__all__ = ["_persist_init_repair_outcome", "_record_init_repair_manifest_round"]
