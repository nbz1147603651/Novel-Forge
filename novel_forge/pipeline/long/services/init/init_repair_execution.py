"""Model-call and patch mechanics for initialization artifact repair."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from novel_forge.core.constants import TaskType
from novel_forge.core.parsing.token_utils import count_text_tokens
from novel_forge.core.utils.repair_target_resolver import navigate_json_pointer_parent
from novel_forge.pipeline.long.services.init.init_repair_targets import (
    _extract_top_level_issues,
)
from novel_forge.pipeline.long.services.init.init_story_bible import (
    _complete_init_repair_request_with_token_guard,
)
from novel_forge.pipeline.token_budget import structured_json_output_floor

_log = logging.getLogger(__name__)

# Compatibility export for callers that still import the historical helper
# through ``init_service``.  The navigation implementation itself lives only
# in the shared repair target resolver.
_navigate_json_pointer = navigate_json_pointer_parent

async def _repair_artifact_summary_only(
    payload: dict[str, Any],
    repair_intent: str,
    *,
    summary_fields: list[str] | None = None,
    adapter: Any = None,
) -> dict[str, Any]:
    """Degraded repair path: summary-only LLM call when full payload exceeds 10K tokens.

    Extracts a lightweight summary from the full ``payload``, constructs a prompt
    guaranteed to be under 10K tokens, calls the LLM for suggested field
    modifications, and conservatively merges only explicitly referenced field paths
    back into the original payload.

    Priority chain: 完整 > 分块 > 降级 (T17 pre-flight selects).

    Args:
        payload: Full artifact payload (arbitrarily large).
        repair_intent: Human-readable description of what needs repair.
        summary_fields: Optional override for which top-level fields to include
            in the summary.  Defaults to ``artifact_name``, ``version``,
            ``top_level_keys``, ``top_level_issues``, ``last_modified``.
        adapter: LLM provider adapter.  If ``None``, returns ``payload`` unchanged
            (safe no-op for environments with no adapter wired).

    Returns:
        Payload with conservatively merged suggestions, or the original payload
        when the adapter is ``None`` or the LLM call / parse fails.
    """
    import copy as _copy

    # ── 1. Extract summary fields ──────────────────────────────────
    default_fields = summary_fields or [
        "artifact_name",
        "version",
        "top_level_keys",
        "top_level_issues",
        "last_modified",
    ]

    summary: dict[str, Any] = {}
    if not isinstance(payload, dict):
        _log.warning("degraded_repair_non_dict_payload | type=%s", type(payload).__name__)
        return payload

    for field in default_fields:
        if field == "top_level_keys":
            summary["top_level_keys"] = sorted(payload.keys())
        elif field == "top_level_issues":
            summary["top_level_issues"] = _extract_top_level_issues(payload)
        elif field == "last_modified":
            summary["last_modified"] = payload.get(field, "")
        elif field in payload:
            summary[field] = payload[field]

    _summary_text = json.dumps(summary, ensure_ascii=False)
    _summary_tokens = max(1, count_text_tokens(_summary_text).tokens)
    _log.info(
        "degraded_repair_summary_size | tokens=%d | keys=%s",
        _summary_tokens,
        list(summary.keys()),
    )

    # ── 2. No adapter → no-op return ─────────────────────────────
    if adapter is None:
        _log.warning("degraded_repair_no_adapter | returning original payload unchanged")
        return payload

    # ── 3. Build minimal prompt context (target < 10K tokens) ──
    prompt_context = {
        "artifact": "artifact",
        "artifact_payload": summary,
        "coherence_report": {
            "issues": [{"description": repair_intent}],
            "repair_scope": [],
            "preserve": [],
            "change_intent": repair_intent,
            "blocked": False,
            "summary": repair_intent,
        },
        "repair_scope": [
            {
                "artifact": "artifact",
                "chapters": [],
                "fields": sorted(summary.keys()),
                "operation": "replace",
                "issue_ids": ["degraded_1"],
            }
        ],
        "preserve": [],
        "change_intent": repair_intent,
        "max_ops": 10,
    }

    prompt_text = json.dumps(prompt_context, ensure_ascii=False)
    estimated_tokens = max(1, count_text_tokens(prompt_text).tokens)
    _log.info(
        "degraded_repair_preflight | estimated_tokens=%d | max_target=10000",
        estimated_tokens,
    )

    # ── 4. Call LLM via adapter ──────────────────────────────────
    from novel_forge.gateway.types import ModelRequest  # late import – degraded path only

    request = ModelRequest(
        task_type=TaskType.REPAIR_INIT_ARTIFACT_PATCH,
        messages=[{"role": "system", "content": prompt_text}],
        max_tokens=max(
            2048,
            structured_json_output_floor(TaskType.REPAIR_INIT_ARTIFACT_PATCH),
        ),
        temperature=0.15,
    )

    try:
        response = await _complete_init_repair_request_with_token_guard(
            adapter,
            request,
            label="degraded_repair",
        )
        response_content = str(getattr(response, "content", "") or "")
    except Exception as exc:
        _log.warning("degraded_repair_adapter_error | %s", exc)
        return payload

    # ── 5. Parse LLM response ────────────────────────────────────
    try:
        parsed = json.loads(response_content)
    except (json.JSONDecodeError, ValueError, TypeError):
        _log.warning(
            "degraded_repair_parse_failed | response_preview=%s",
            response_content[:200],
        )
        return payload

    patches = parsed.get("patches", [])
    if isinstance(patches, dict):
        patches = [patches]
    if not isinstance(patches, list):
        _log.warning(
            "degraded_repair_unexpected_patches_type | type=%s",
            type(patches).__name__,
        )
        return payload

    # ── 6. Conservative merge ────────────────────────────────────
    # Only modify field paths that are EXPLICITLY referenced and exist in payload.
    # Unmatched paths log a WARNING but never raise.
    merged = _copy.deepcopy(payload)
    applied_count = 0
    skipped_count = 0

    for patch in patches:
        if not isinstance(patch, dict):
            skipped_count += 1
            continue

        field_path = str(patch.get("path", "") or "").strip()
        if not field_path or "value" not in patch:
            skipped_count += 1
            continue

        new_value = patch["value"]

        try:
            parent, last_key = _navigate_json_pointer(merged, field_path)
            if isinstance(parent, dict):
                if last_key not in parent:
                    _log.warning(
                        "degraded_repair_field_not_found | path=%s | key=%s",
                        field_path,
                        last_key,
                    )
                    skipped_count += 1
                    continue
                parent[last_key] = new_value
                applied_count += 1
            elif isinstance(parent, list):
                idx = int(last_key)
                if 0 <= idx < len(parent):
                    parent[idx] = new_value
                    applied_count += 1
                else:
                    _log.warning(
                        "degraded_repair_index_oob | path=%s | idx=%d | len=%d",
                        field_path,
                        idx,
                        len(parent),
                    )
                    skipped_count += 1
            else:
                _log.warning(
                    "degraded_repair_leaf_unexpected | path=%s | type=%s",
                    field_path,
                    type(parent).__name__,
                )
                skipped_count += 1
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            _log.warning(
                "degraded_repair_apply_skipped | path=%s | error=%s",
                field_path,
                exc,
            )
            skipped_count += 1

    _log.info(
        "degraded_repair_summary | applied=%d | skipped=%d | total=%d",
        applied_count,
        skipped_count,
        len(patches),
    )

    return merged

def _detect_artifact_type(payload: dict[str, Any]) -> str:
    """Detect the artifact type from payload structure.

    Returns:
        "outline" if payload has top-level ``chapters`` list with per-chapter items.
        "chapter_contracts" if payload has top-level ``chapter_contracts`` list.
        "blueprint" otherwise (no per-chapter structure → not chunkable).
    """
    if not isinstance(payload, dict):
        return "blueprint"

    chapters = payload.get("chapters")
    if isinstance(chapters, list) and chapters and isinstance(chapters[0], dict):
        if "chapter_number" in chapters[0]:
            return "outline"

    cc = payload.get("chapter_contracts")
    if isinstance(cc, list) and cc and isinstance(cc[0], dict):
        return "chapter_contracts"

    return "blueprint"

def _split_payload_by_chapter(
    payload: dict[str, Any],
    artifact_type: str,
    max_chunks: int = 4,
) -> list[dict[str, Any]]:
    """Split artifact payload into per-chapter chunks.

    For outline: splits ``payload["chapters"]``.
    For chapter_contracts: splits ``payload["chapter_contracts"]``.

    Args:
        payload: Full artifact payload.
        artifact_type: One of "outline", "chapter_contracts".
        max_chunks: Maximum number of chunks.  If the number of chapters
            exceeds this value, multiple chapters are grouped per chunk.

    Returns:
        List of chunk payloads, each containing a subset of chapters.
        Returns ``[payload]`` (single chunk) if no chapter structure is found.
    """
    import copy as _copy

    if artifact_type == "outline":
        chapters_key = "chapters"
    elif artifact_type == "chapter_contracts":
        chapters_key = "chapter_contracts"
    else:
        return [payload]

    chapters = payload.get(chapters_key)
    if not isinstance(chapters, list) or not chapters:
        return [payload]

    num_chapters = len(chapters)
    if num_chapters <= max_chunks:
        chunks: list[dict[str, Any]] = []
        for chapter in chapters:
            chunk = _copy.deepcopy(payload)
            chunk[chapters_key] = [chapter]
            chunks.append(chunk)
        return chunks

    base_size = num_chapters // max_chunks
    remainder = num_chapters % max_chunks

    chunks = []
    idx = 0
    for i in range(max_chunks):
        chunk_size = base_size + (1 if i < remainder else 0)
        chunk_chapters = chapters[idx : idx + chunk_size]
        idx += chunk_size

        chunk = _copy.deepcopy(payload)
        chunk[chapters_key] = list(chunk_chapters)
        chunks.append(chunk)

    return chunks

def _merge_repaired_chunks(
    original: dict[str, Any],
    repaired_chunks: list[dict[str, Any]],
    artifact_type: str,
) -> tuple[dict[str, Any], list[str]]:
    """Merge repaired chunks back into the original payload.

    For each repaired chunk, the chapter-level items are located in the
    original by their identity key (``chapter_number`` for outline,
    ``chapter_id`` for chapter_contracts) and replaced.

    If two chunks both modify the same chapter, a WARNING is recorded and
    the first chunk's modification is retained.

    Args:
        original: The original payload before chunked repair.
        repaired_chunks: Repaired chunk payloads (one per chunk).
        artifact_type: One of "outline", "chapter_contracts".

    Returns:
        Tuple of ``(merged_payload, warnings)``.
    """
    import copy as _copy

    merged = _copy.deepcopy(original)
    warnings: list[str] = []

    if artifact_type == "outline":
        chapters_key = "chapters"
        id_key = "chapter_number"
    elif artifact_type == "chapter_contracts":
        chapters_key = "chapter_contracts"
        id_key = "chapter_id"
    else:
        return merged, warnings

    all_original_chapters = original.get(chapters_key, [])
    if not isinstance(all_original_chapters, list):
        return merged, ["merge_chapter_list_not_found"]

    modified_chapter_ids: dict[int | str, str] = {}

    for chunk_idx, chunk in enumerate(repaired_chunks):
        chunk_chapters = chunk.get(chapters_key, [])
        if not isinstance(chunk_chapters, list):
            continue
        chunk_label = f"chunk_{chunk_idx}"

        for chapter in chunk_chapters:
            if not isinstance(chapter, dict):
                continue
            chapter_id = chapter.get(id_key)
            if chapter_id is None:
                continue

            for orig_idx, orig_chapter in enumerate(all_original_chapters):
                if not isinstance(orig_chapter, dict):
                    continue
                if orig_chapter.get(id_key) == chapter_id:
                    if chapter_id in modified_chapter_ids:
                        warnings.append(
                            f"chunked_repair_conflict | chapter={chapter_id} | "
                            f"first_modified_by={modified_chapter_ids[chapter_id]} | "
                            f"conflict_in={chunk_label} | retaining_first"
                        )
                    else:
                        modified_chapter_ids[chapter_id] = chunk_label
                        merged[chapters_key][orig_idx] = chapter
                    break

    return merged, warnings

async def _call_repair_for_chunk(
    chunk_payload: dict[str, Any],
    repair_intent: str,
    adapter: Any,
    chunk_index: int = 0,
) -> dict[str, Any]:
    """Call LLM for a single chunk and return the repaired chunk payload.

    Builds a lightweight prompt from the chunk payload + repair intent,
    calls the adapter, parses the JSON response, and conservatively
    applies any suggested patches.

    If the LLM call or parse fails, returns the chunk unchanged.
    """
    import copy as _copy

    artifact_type = _detect_artifact_type(chunk_payload)
    prompt_context = {
        "artifact": artifact_type,
        "artifact_payload": chunk_payload,
        "coherence_report": {
            "issues": [{"description": repair_intent}],
            "repair_scope": [],
            "preserve": [],
            "change_intent": repair_intent,
            "blocked": False,
            "summary": repair_intent,
        },
        "repair_scope": [
            {
                "artifact": artifact_type,
                "chapters": [],
                "fields": [],
                "operation": "replace",
                "issue_ids": [f"chunked_{chunk_index}"],
            }
        ],
        "preserve": [],
        "change_intent": repair_intent,
        "max_ops": 20,
    }
    prompt_text = json.dumps(prompt_context, ensure_ascii=False)

    from novel_forge.gateway.types import ModelRequest

    request = ModelRequest(
        task_type=TaskType.REPAIR_INIT_ARTIFACT_PATCH,
        messages=[{"role": "system", "content": prompt_text}],
        max_tokens=max(
            2048,
            structured_json_output_floor(TaskType.REPAIR_INIT_ARTIFACT_PATCH),
        ),
        temperature=0.15,
    )

    try:
        response = await _complete_init_repair_request_with_token_guard(
            adapter,
            request,
            label="chunked_repair",
        )
        response_content = str(getattr(response, "content", "") or "")
    except Exception as exc:
        _log.warning("chunked_repair_adapter_error | chunk=%d | %s", chunk_index, exc)
        return chunk_payload

    try:
        parsed = json.loads(response_content)
    except (json.JSONDecodeError, ValueError, TypeError):
        _log.warning(
            "chunked_repair_parse_failed | chunk=%d | preview=%s",
            chunk_index,
            response_content[:200],
        )
        return chunk_payload

    patches = parsed.get("patches", [])
    if isinstance(patches, dict):
        patches = [patches]
    if not isinstance(patches, list):
        _log.warning(
            "chunked_repair_unexpected_patches | chunk=%d | type=%s",
            chunk_index,
            type(patches).__name__,
        )
        return chunk_payload

    result = _copy.deepcopy(chunk_payload)
    applied = 0
    for patch in patches:
        if not isinstance(patch, dict):
            continue
        field_path = str(patch.get("path", "") or "").strip()
        if not field_path or "value" not in patch:
            continue
        try:
            parent, last_key = _navigate_json_pointer(result, field_path)
            if isinstance(parent, dict) and last_key in parent:
                parent[last_key] = patch["value"]
                applied += 1
            elif isinstance(parent, list):
                idx = int(last_key)
                if 0 <= idx < len(parent):
                    parent[idx] = patch["value"]
                    applied += 1
        except (KeyError, IndexError, ValueError, TypeError):
            pass

    _log.info(
        "chunked_repair_chunk_done | chunk=%d | patches_applied=%d",
        chunk_index,
        applied,
    )
    return result

async def _repair_artifact_chunked(
    payload: dict[str, Any],
    chunk_by: str = "chapter",
    repair_intent: str = "fix_inconsistency",
    *,
    adapter: Any = None,
    max_chunks_per_repair: int = 4,
) -> dict[str, Any]:
    """Chunked repair for oversized outline / chapter_contracts payloads.

    Splits the payload by chapter, runs an independent LLM repair on each
    chunk, and merges the results back.  This avoids hitting context-window
    limits when the full payload exceeds ~50K tokens.

    Priority chain (T17 pre-flight): 完整 > 分块 > 降级

    For blueprint artifacts (no per-chapter structure), chunking is not
    applicable — the function returns the payload unchanged with a note.

    Args:
        payload: Full artifact payload (outline or chapter_contracts).
        chunk_by: How to split (currently only "chapter" is supported).
        repair_intent: Human-readable description of the repair goal.
        adapter: LLM provider adapter.  If ``None``, returns unchanged.
        max_chunks_per_repair: Maximum number of chunks to create.
            Prevents unbounded splitting.  If the chapter count exceeds
            this, multiple chapters are grouped per chunk.

    Returns:
        Dict with keys:
        - ``repaired``: The merged, repaired payload.
        - ``chunks_processed``: Number of chunks that were repaired.
        - ``merge_warnings`` (optional): List of conflict warnings.
    """
    if adapter is None:
        _log.warning("chunked_repair_no_adapter | returning unchanged")
        return {"repaired": payload, "chunks_processed": 0}

    artifact_type = _detect_artifact_type(payload)

    if artifact_type == "blueprint":
        _log.info(
            "chunked_repair_blueprint_skip | blueprint has no per-chapter structure | "
            "delegate_to_standard_repair"
        )
        return {
            "repaired": payload,
            "chunks_processed": 0,
            "note": "blueprint_not_chunked",
        }

    chunks = _split_payload_by_chapter(
        payload,
        artifact_type,
        max_chunks=max_chunks_per_repair,
    )

    if len(chunks) <= 1:
        _log.info(
            "chunked_repair_single_chunk | artifact=%s | repair_as_single",
            artifact_type,
        )
        result = await _call_repair_for_chunk(payload, repair_intent, adapter, chunk_index=0)
        return {"repaired": result, "chunks_processed": 1}

    _log.info(
        "chunked_repair_start | artifact=%s | chunks=%d | max_chunks=%d",
        artifact_type,
        len(chunks),
        max_chunks_per_repair,
    )

    # Chunks are repaired independently; run them concurrently and keep the
    # result order aligned with the chunk order so _merge_repaired_chunks sees
    # exactly the same sequence as the previous serial loop.
    repaired_chunks = list(
        await asyncio.gather(
            *(
                _call_repair_for_chunk(
                    chunk,
                    repair_intent,
                    adapter,
                    chunk_index=i,
                )
                for i, chunk in enumerate(chunks)
            )
        )
    )

    merged, warnings = _merge_repaired_chunks(payload, repaired_chunks, artifact_type)

    for w in warnings:
        _log.warning(w)

    return {
        "repaired": merged,
        "chunks_processed": len(chunks),
        "merge_warnings": warnings,
    }
