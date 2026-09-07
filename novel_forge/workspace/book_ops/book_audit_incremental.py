"""Pure helpers for safe cross-run reuse of whole-book audit slices."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

BOOK_AUDIT_WORKFLOW_VERSION = "book.audit.v2"


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _slice_chapters(item: dict[str, Any]) -> list[int]:
    chapters: set[int] = set()
    for raw in [*(item.get("chapters") or []), *(item.get("boundary_chapters") or [])]:
        try:
            chapter = int(raw or 0)
        except (TypeError, ValueError):
            continue
        if chapter > 0:
            chapters.add(chapter)
    return sorted(chapters)


def build_book_audit_input_manifest(
    *,
    project_id: str,
    chapter_hashes: dict[int, str],
    audit_slices: list[dict[str, Any]],
    analysis_options: dict[str, Any],
    context_hashes: dict[str, str],
) -> dict[str, Any]:
    """Build signatures whose chapter component can be diffed independently."""

    normalized_hashes = {
        str(chapter): str(value)
        for chapter, value in sorted(chapter_hashes.items())
        if int(chapter) > 0 and str(value)
    }
    analysis_signature = _digest(
        {
            "workflow_version": BOOK_AUDIT_WORKFLOW_VERSION,
            "project_id": project_id,
            "analysis_options": analysis_options,
            "context_hashes": context_hashes,
        }
    )
    slice_signatures: dict[str, str] = {}
    for item in audit_slices:
        slice_id = str(item.get("slice_id") or "").strip()
        if not slice_id:
            continue
        chapters = _slice_chapters(item)
        slice_signatures[slice_id] = _digest(
            {
                "analysis_signature": analysis_signature,
                "slice_id": slice_id,
                "slice_kind": item.get("slice_kind") or "",
                "chapters": chapters,
                "focus_dimensions": sorted(str(dim) for dim in item.get("focus_dimensions") or []),
                "source_refs": item.get("source_refs") or [],
                "chapter_hashes": {
                    str(chapter): normalized_hashes.get(str(chapter), "legacy_unknown")
                    for chapter in chapters
                },
            }
        )
    return {
        "workflow_version": BOOK_AUDIT_WORKFLOW_VERSION,
        "analysis_signature": analysis_signature,
        "input_signature": _digest(
            {
                "analysis_signature": analysis_signature,
                "chapter_hashes": normalized_hashes,
                "slice_signatures": slice_signatures,
            }
        ),
        "chapter_hashes": normalized_hashes,
        "context_hashes": dict(context_hashes),
        "slice_signatures": slice_signatures,
    }


@dataclass(frozen=True)
class BookAuditReusePlan:
    """Reusable read-only results and the exact invalidation explanation."""

    status: str
    changed_chapters: list[int] = field(default_factory=list)
    reusable_slice_ids: list[str] = field(default_factory=list)
    invalidated_slice_ids: list[str] = field(default_factory=list)
    seeded_dimension_results: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    reason: str = ""


def plan_book_audit_reuse(
    *,
    previous_payload: dict[str, Any] | None,
    current_manifest: dict[str, Any],
    current_slices: list[dict[str, Any]],
) -> BookAuditReusePlan:
    """Reuse only dimension/slice results whose full input signature is unchanged."""

    if not isinstance(previous_payload, dict):
        return BookAuditReusePlan(status="cold_start", reason="previous_audit_missing")
    previous_manifest = previous_payload.get("input_manifest")
    if not isinstance(previous_manifest, dict):
        return BookAuditReusePlan(status="legacy_unknown", reason="previous_signature_missing")
    if previous_manifest.get("workflow_version") != BOOK_AUDIT_WORKFLOW_VERSION:
        return BookAuditReusePlan(status="legacy_unknown", reason="workflow_version_mismatch")
    if previous_manifest.get("analysis_signature") != current_manifest.get("analysis_signature"):
        return BookAuditReusePlan(status="full_refresh", reason="analysis_context_changed")

    previous_hashes = previous_manifest.get("chapter_hashes") or {}
    current_hashes = current_manifest.get("chapter_hashes") or {}
    all_chapters = set(previous_hashes) | set(current_hashes)
    changed = sorted(
        int(chapter)
        for chapter in all_chapters
        if previous_hashes.get(chapter) != current_hashes.get(chapter) and str(chapter).isdigit()
    )
    previous_slice_signatures = previous_manifest.get("slice_signatures") or {}
    current_slice_signatures = current_manifest.get("slice_signatures") or {}
    reusable = sorted(
        slice_id
        for slice_id, signature in current_slice_signatures.items()
        if previous_slice_signatures.get(slice_id) == signature
    )
    invalidated = sorted(set(current_slice_signatures) - set(reusable))
    current_dimensions = {
        str(item.get("slice_id") or ""): {
            str(dimension) for dimension in item.get("focus_dimensions") or []
        }
        for item in current_slices
        if str(item.get("slice_id") or "")
    }
    seeded: dict[str, list[dict[str, Any]]] = {}
    for result in previous_payload.get("dimension_results") or []:
        if not isinstance(result, dict):
            continue
        dimension = str(result.get("dimension") or "").strip()
        slice_id = str(result.get("slice_id") or "").strip()
        if (
            dimension
            and slice_id in reusable
            and dimension in current_dimensions.get(slice_id, set())
        ):
            seeded.setdefault(dimension, []).append(dict(result))
    return BookAuditReusePlan(
        status="unchanged" if not changed else "incremental",
        changed_chapters=changed,
        reusable_slice_ids=reusable,
        invalidated_slice_ids=invalidated,
        seeded_dimension_results=seeded,
        reason="all_slice_signatures_match" if not changed else "chapter_signature_changed",
    )
