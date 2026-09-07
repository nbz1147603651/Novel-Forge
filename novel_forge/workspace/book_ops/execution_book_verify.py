"""Stage-2 per-chapter verification of audit issues."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from novel_forge.core.exceptions import ModelGatewayError
from novel_forge.pipeline.steps.book_consistency_verify_step import (
    BookConsistencyVerifyInput,
    BookConsistencyVerifyStep,
)
from novel_forge.workspace.book_audit_checkpoint_store import BookAuditCheckpointStore
from novel_forge.workspace.book_ops.execution_book_common import _log
from novel_forge.workspace.execution_result import StepCallback
from novel_forge.workspace.runtime import RuntimeServices


def _save_verify_checkpoint(path: Path | None, payload: dict[str, Any]) -> None:
    """Persist verify-phase checkpoint atomically."""
    BookAuditCheckpointStore.write_checkpoint_payload(path, payload)


def _load_verify_checkpoint(
    path: Path | None,
    expected_signature: str | None = None,
) -> dict[str, Any] | None:
    """Load and validate a verify-phase checkpoint. Returns None if missing/corrupt."""
    if path is None or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _log.warning("book_consistency_verify: corrupt checkpoint at %s, starting fresh", path)
        return None
    if not isinstance(payload, dict):
        return None
    if expected_signature is not None:
        signature = str(payload.get("signature", "") or "")
        if signature != expected_signature:
            _log.info("book_consistency_verify: checkpoint signature mismatch, starting fresh")
            return None
    completed = payload.get("completed_chapters", [])
    verified_by_chapter_raw = payload.get("verified_by_chapter", {})
    checksum_stored = payload.get("checksum")
    if checksum_stored is not None and isinstance(completed, list):
        completed_ints = [int(ch) for ch in completed]
        verified_by_chapter = (
            verified_by_chapter_raw if isinstance(verified_by_chapter_raw, dict) else {}
        )
        checksum_computed = _compute_verify_checksum(completed_ints, verified_by_chapter)
        if checksum_computed != str(checksum_stored):
            _log.warning(
                "book_consistency_verify: checksum mismatch in checkpoint at %s, starting fresh",
                path,
            )
            return None
    return payload


def _compute_chapters_checksum(chapters: list[int]) -> str:
    """Compute SHA256 checksum for a list of completed chapter numbers."""
    data = json.dumps(chapters, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _compute_verify_checksum(
    chapters: list[int],
    verified_by_chapter: dict[int, list[dict[str, Any]]] | dict[str, Any] | None = None,
) -> str:
    """Compute checksum for verify checkpoint progress and cached outputs."""
    payload = {
        "completed_chapters": chapters,
        "verified_by_chapter": verified_by_chapter or {},
    }
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _verify_checkpoint_signature(report_issues: list[dict[str, Any]]) -> str:
    """Bind verify checkpoints to the exact issue set being verified."""

    def _int_value(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    payload = [
        {
            "issue_id": str(issue.get("issue_id", "") or ""),
            "primary_chapter": _int_value(issue.get("primary_chapter")),
            "category": str(issue.get("category", "") or ""),
            "severity": str(issue.get("severity", "") or ""),
            "paragraph_index": _int_value(issue.get("paragraph_index")),
            "description": str(issue.get("description", "") or "")[:300],
            "evidence_pairs": issue.get("evidence_pairs") or [],
            "verification_questions": issue.get("verification_questions") or [],
            "handoff_notes": str(issue.get("handoff_notes", "") or "")[:300],
        }
        for issue in report_issues
        if isinstance(issue, dict)
    ]
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _verify_issue_chapters(issue: dict[str, Any]) -> list[int]:
    chapters_raw = issue.get("chapters_involved", [])
    chapters: list[int] = []
    if isinstance(chapters_raw, list):
        for raw in chapters_raw:
            try:
                chapter = int(raw)
            except (TypeError, ValueError):
                continue
            if chapter > 0 and chapter not in chapters:
                chapters.append(chapter)
    primary = issue.get("primary_chapter")
    try:
        primary_chapter = int(primary or 0)
    except (TypeError, ValueError):
        primary_chapter = 0
    if primary_chapter > 0 and primary_chapter not in chapters:
        chapters.insert(0, primary_chapter)
    return chapters


def _compact_verify_excerpt(text: str, max_chars: int = 700) -> str:
    text = str(text or "").strip()
    if len(text) <= max_chars:
        return text
    marker = "\n[...]\n"
    budget = max(1, max_chars - len(marker))
    head = budget // 2
    tail = budget - head
    return text[:head].rstrip() + marker + text[-tail:].lstrip()


def _issue_evidence_candidates_for_chapter(issue: dict[str, Any], chapter_number: int) -> list[str]:
    candidates: list[str] = []
    evidence_pairs = issue.get("evidence_pairs")
    if isinstance(evidence_pairs, list):
        for pair in evidence_pairs:
            if not isinstance(pair, dict):
                continue
            try:
                pair_chapter = int(pair.get("chapter_number", 0) or 0)
            except (TypeError, ValueError):
                pair_chapter = 0
            if pair_chapter != chapter_number:
                continue
            evidence = str(pair.get("evidence", "") or "").strip()
            if evidence and evidence not in candidates:
                candidates.append(evidence)
    primary = issue.get("primary_chapter")
    try:
        primary_chapter = int(primary or 0)
    except (TypeError, ValueError):
        primary_chapter = 0
    evidence = str(issue.get("evidence", "") or "").strip()
    if evidence and (chapter_number == primary_chapter or not candidates):
        candidates.append(evidence)
    return candidates


def _related_chapter_excerpt(
    chapter_payload: dict[str, Any],
    issue: dict[str, Any],
    *,
    chapter_number: int,
) -> str:
    paragraphs = chapter_payload.get("paragraphs", [])
    evidence_candidates = _issue_evidence_candidates_for_chapter(issue, chapter_number)
    if isinstance(paragraphs, list):
        for evidence in evidence_candidates:
            for idx, paragraph in enumerate(paragraphs, start=1):
                paragraph_text = str(paragraph or "")
                if evidence and evidence in paragraph_text:
                    return _compact_verify_excerpt(f"[P{idx}] {paragraph_text}")
    numbered_text = str(chapter_payload.get("numbered_text", "") or "")
    for evidence in evidence_candidates:
        if evidence and evidence in numbered_text:
            pos = numbered_text.find(evidence)
            start = max(0, pos - 260)
            end = min(len(numbered_text), pos + len(evidence) + 260)
            return _compact_verify_excerpt(numbered_text[start:end])
    if isinstance(paragraphs, list) and paragraphs:
        first = str(paragraphs[0] or "")
        last = str(paragraphs[-1] or "")
        if first != last:
            return _compact_verify_excerpt(f"[P1] {first}\n[P{len(paragraphs)}] {last}")
        return _compact_verify_excerpt(f"[P1] {first}")
    return _compact_verify_excerpt(numbered_text)


def _build_related_chapters_context(
    *,
    current_chapter: int,
    chapter_issues: list[dict[str, Any]],
    text_by_chapter: dict[int, dict[str, Any]],
    summary_by_chapter: dict[int, str],
    max_chapters: int = 4,
    max_issue_refs_per_chapter: int = 6,
) -> list[dict[str, Any]]:
    """Build compact cross-chapter context for per-chapter verification."""

    related: dict[int, dict[str, Any]] = {}
    for issue in chapter_issues:
        if not isinstance(issue, dict):
            continue
        chapters = _verify_issue_chapters(issue)
        if len(chapters) <= 1:
            continue
        for chapter in chapters:
            if chapter == current_chapter or chapter <= 0:
                continue
            entry = related.setdefault(
                chapter,
                {
                    "chapter_number": chapter,
                    "chapter_summary": summary_by_chapter.get(chapter, ""),
                    "issue_refs": [],
                    "excerpts": [],
                },
            )
            refs = entry["issue_refs"]
            if isinstance(refs, list) and len(refs) < max_issue_refs_per_chapter:
                refs.append(
                    {
                        "issue_id": str(issue.get("issue_id", "") or ""),
                        "category": str(issue.get("category", "") or ""),
                        "severity": str(issue.get("severity", "") or ""),
                        "description": str(issue.get("description", "") or "")[:260],
                        "evidence": str(issue.get("evidence", "") or "")[:160],
                        "evidence_pairs": issue.get("evidence_pairs") or [],
                        "verification_questions": issue.get("verification_questions") or [],
                        "handoff_notes": str(issue.get("handoff_notes", "") or "")[:260],
                    }
                )
            chapter_payload = text_by_chapter.get(chapter, {})
            if chapter_payload:
                excerpt = _related_chapter_excerpt(
                    chapter_payload,
                    issue,
                    chapter_number=chapter,
                )
                excerpts = entry["excerpts"]
                if excerpt and isinstance(excerpts, list) and excerpt not in excerpts:
                    excerpts.append(excerpt)
    return [
        related[chapter]
        for chapter in sorted(related)[: max(0, max_chapters)]
    ]


async def _run_book_consistency_verify(
    *,
    runtime: RuntimeServices,
    report_issues: list[dict[str, Any]],
    chapter_texts: list[dict[str, Any]],
    chapter_summaries: list[dict[str, Any]],
    canon_state_snapshot: dict[str, Any],
    max_tokens: int = 4096,
    temperature: float = 0.2,
    concurrency: int = 2,
    on_step_progress: StepCallback = None,
    trace: Any = None,
    checkpoint_path: Path | None = None,
    parallel_chunks: bool = True,
    max_parallel: int = 5,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Verify audit issues per-chapter with a focused LLM call.

    For each chapter that has claimed issues, send the chapter full text + the
    claimed issues to the model and ask it to verify/reject/re-locate each issue.
    Returns a tuple: (updated_issues, verify_stats).
    Rejected issues are removed; verified issues are refined
    (paragraph_index, evidence, confidence).
    """
    # Group issues by primary_chapter
    issues_by_chapter: dict[int, list[dict[str, Any]]] = {}
    issue_by_id: dict[str, dict[str, Any]] = {}
    suspected_ids: set[str] = set()
    confirmed_ids: set[str] = set()
    for issue in report_issues:
        ch = int(issue.get("primary_chapter", 0) or 0)
        if ch > 0:
            issues_by_chapter.setdefault(ch, []).append(issue)
        iid = str(issue.get("issue_id", "") or "").strip()
        if iid:
            issue_by_id[iid] = issue
        vstatus = str(issue.get("verification_status", "") or "").strip().lower()
        if vstatus == "suspected":
            if iid:
                suspected_ids.add(iid)
        elif vstatus == "confirmed":
            if iid:
                confirmed_ids.add(iid)

    if not issues_by_chapter:
        return report_issues, {
            "total_issues": len(report_issues),
            "verified": 0,
            "rejected": 0,
            "remaining": len(report_issues),
        }

    # Build text lookup
    text_by_chapter: dict[int, dict[str, Any]] = {}
    for ct in chapter_texts:
        cn = int(ct.get("chapter_number", 0) or 0)
        if cn > 0:
            text_by_chapter[cn] = ct

    summary_by_chapter: dict[int, str] = {}
    for cs in chapter_summaries:
        cn = int(cs.get("chapter_number", 0) or 0)
        if cn > 0:
            summary_by_chapter[cn] = str(cs.get("summary", "") or "")

    # Extract canon characters for context
    canon_characters: dict[str, Any] = {}
    if isinstance(canon_state_snapshot, dict):
        canon_characters = canon_state_snapshot.get("characters", {})
        if not isinstance(canon_characters, dict):
            canon_characters = {}

    chapters_to_verify = sorted(issues_by_chapter.keys())
    total = len(chapters_to_verify)
    checkpoint_signature = _verify_checkpoint_signature(report_issues)

    completed_chapters: set[int] = set()
    verified_by_chapter: dict[int, list[dict[str, Any]]] = {}
    cp = _load_verify_checkpoint(checkpoint_path, expected_signature=checkpoint_signature)
    if cp is not None:
        prev = [int(x) for x in cp.get("completed_chapters", []) if isinstance(x, (int, str))]
        completed_chapters = set(prev)
        cached = cp.get("verified_by_chapter", {})
        if isinstance(cached, dict):
            for key, value in cached.items():
                try:
                    chapter_num = int(key)
                except (TypeError, ValueError):
                    continue
                if isinstance(value, list):
                    verified_by_chapter[chapter_num] = [
                        item for item in value if isinstance(item, dict)
                    ]
        resume_from = min(
            (ch for ch in chapters_to_verify if ch not in completed_chapters), default=None
        )
        if resume_from is not None:
            _log.info(
                "book_consistency_verify: resuming from chapter %d (%d/%d already completed)",
                resume_from,
                len(completed_chapters),
                total,
            )
        else:
            _log.info("book_consistency_verify: all chapters already verified, skipping")

    if on_step_progress:
        on_step_progress(
            "book_consistency_verify_start",
            {"status": "running", "total": total, "processed": 0},
        )

    step = BookConsistencyVerifyStep(
        runtime.router,
        runtime.builder,
        settings=runtime.settings,
        trace=trace,
    )

    # Track verified results
    verified_issue_map: dict[str, dict[str, Any]] = {}  # issue_id → verified payload
    rejected_ids: set[str] = set()
    verification_failed_updates: dict[str, dict[str, Any]] = {}
    malformed_verify_items = 0
    modified_guarded_count = 0
    effective_concurrency = max(1, min(concurrency, max_parallel, 16))
    semaphore = asyncio.Semaphore(effective_concurrency)
    processed = 0
    lock = asyncio.Lock()

    async def _mark_processed(
        ch_num: int,
        verified_items: list[dict[str, Any]] | None = None,
    ) -> None:
        nonlocal processed
        async with lock:
            processed += 1
            completed_chapters.add(ch_num)
            if verified_items is not None:
                verified_by_chapter[ch_num] = verified_items
            _save_verify_checkpoint(
                checkpoint_path,
                {
                    "schema_version": 1,
                    "signature": checkpoint_signature,
                    "completed_chapters": sorted(completed_chapters),
                    "verified_by_chapter": verified_by_chapter,
                    "total_chapters": total,
                    "status": "running",
                    "checksum": _compute_verify_checksum(
                        sorted(completed_chapters),
                        verified_by_chapter,
                    ),
                },
            )
            if on_step_progress:
                on_step_progress(
                    "book_consistency_verify_progress",
                    {
                        "status": "running",
                        "total": total,
                        "processed": processed,
                        "chapter_number": ch_num,
                    },
                )

    def _apply_verified_list(ch_num: int, verified_list: list[dict[str, Any]]) -> None:
        nonlocal malformed_verify_items, modified_guarded_count
        ct = text_by_chapter.get(ch_num, {})
        para_count = int(ct.get("paragraph_count", 0) or 0)
        paragraphs = ct.get("paragraphs", [])
        severity_rank = {"info": 0, "warning": 1, "critical": 2}

        for v_item in verified_list:
            if not isinstance(v_item, dict):
                malformed_verify_items += 1
                continue
            iid = str(v_item.get("issue_id", "") or "").strip()
            if not iid:
                malformed_verify_items += 1
                continue
            status = str(v_item.get("status", "") or "").strip().lower()
            if status not in {"verified", "modified", "rejected"}:
                malformed_verify_items += 1
                continue
            if status == "rejected":
                v_conf = v_item.get("confidence")
                if iid in confirmed_ids and (
                    not isinstance(v_conf, (int, float)) or float(v_conf) < 0.95
                ):
                    continue
                if iid in suspected_ids and (
                    not isinstance(v_conf, (int, float)) or float(v_conf) < 0.8
                ):
                    continue
                rejected_ids.add(iid)
                continue

            # For verified/modified: update the original issue fields
            update: dict[str, Any] = {"_verified": True}
            _raw_para = v_item.get("paragraph_index", 0) or 0
            # paragraph_index may be a list (multi-span issue) — take first element
            if isinstance(_raw_para, list):
                _raw_para = _raw_para[0] if _raw_para else 0
            try:
                new_para = int(_raw_para)
            except (TypeError, ValueError):
                new_para = 0

            # Local re-resolution using evidence text
            v_evidence = str(v_item.get("evidence", "") or "").strip()
            if paragraphs and v_evidence:
                from novel_forge.core.utils.patch_utils import resolve_paragraph_locally

                local_targets, _anchor, _conf = resolve_paragraph_locally(
                    paragraphs,
                    evidence=v_evidence,
                    location=str(v_item.get("location", "") or ""),
                    llm_hint=new_para,
                )
                if _anchor != "fallback" and local_targets:
                    new_para = local_targets[0] + 1  # 0-based → 1-based

            if new_para > 0 and (para_count <= 0 or new_para <= para_count):
                update["paragraph_index"] = new_para
                span = v_item.get("paragraph_span")
                repair_scope = v_item.get("repair_scope")
                if not isinstance(span, list) and isinstance(repair_scope, dict):
                    span = repair_scope.get("paragraph_span") or repair_scope.get("target_span")
                if isinstance(span, list) and len(span) >= 2:

                    def _safe_int(val: Any, default: int = 1) -> int:
                        if isinstance(val, list):
                            val = val[0] if val else default
                        try:
                            return int(val)
                        except (TypeError, ValueError):
                            return default

                    s0, s1 = _safe_int(span[0]), _safe_int(span[1])
                    if para_count > 0:
                        s0, s1 = max(1, min(s0, para_count)), max(1, min(s1, para_count))
                    update["paragraph_span"] = [s0, s1]
                else:
                    update["paragraph_span"] = [new_para, new_para]

            if v_evidence:
                update["evidence"] = v_evidence
            location = str(v_item.get("location", "") or "").strip()
            if location:
                update["location"] = location
            anchor_type = str(v_item.get("anchor_type", "") or "").strip()
            if anchor_type:
                update["anchor_type"] = anchor_type
            location_confidence = v_item.get("location_confidence")
            if isinstance(location_confidence, (int, float)):
                update["location_confidence"] = max(0.0, min(float(location_confidence), 1.0))
            for list_field in (
                "evidence_pairs",
                "verification_questions",
                "postconditions",
                "allowed_changes",
                "forbidden_changes",
                "must_preserve",
            ):
                list_value = v_item.get(list_field)
                if isinstance(list_value, list):
                    update[list_field] = list_value
            for text_field in ("handoff_notes", "adjudication_notes", "repair_boundary"):
                text_value = str(v_item.get(text_field, "") or "").strip()
                if text_value:
                    update[text_field] = text_value
            repair_scope = v_item.get("repair_scope")
            if isinstance(repair_scope, dict):
                update["repair_scope"] = repair_scope
            new_conf = v_item.get("confidence")
            if isinstance(new_conf, (int, float)):
                update["confidence"] = max(0.0, min(float(new_conf), 1.0))
            if status == "modified":
                original_issue = issue_by_id.get(iid, {})
                protected_confirmed = iid in confirmed_ids
                if protected_confirmed and "confidence" in update:
                    old_conf = original_issue.get("confidence")
                    if isinstance(old_conf, (int, float)) and float(old_conf) - float(
                        update["confidence"]
                    ) > 0.3:
                        update["verify_modified_guard"] = {
                            "reason": "confirmed_confidence_downgrade_blocked",
                            "original_confidence": max(0.0, min(float(old_conf), 1.0)),
                            "proposed_confidence": update["confidence"],
                        }
                        update.pop("confidence", None)
                        modified_guarded_count += 1
                for field in ("description", "severity", "fix_mode", "fix_action"):
                    val = v_item.get(field)
                    if val and str(val).strip():
                        if field == "severity" and protected_confirmed:
                            old_severity = str(
                                original_issue.get("severity", "warning") or "warning"
                            ).strip().lower()
                            new_severity = str(val).strip().lower()
                            if severity_rank.get(new_severity, 1) < severity_rank.get(
                                old_severity, 1
                            ):
                                guard = update.setdefault(
                                    "verify_modified_guard",
                                    {"reason": "confirmed_modified_downgrade_blocked"},
                                )
                                if isinstance(guard, dict):
                                    guard["severity_downgrade_blocked"] = {
                                        "original_severity": old_severity,
                                        "proposed_severity": new_severity,
                                    }
                                modified_guarded_count += 1
                                continue
                        update[field] = str(val).strip()
            verified_issue_map[iid] = update

    async def _verify_chapter(ch_num: int, ch_issues: list[dict[str, Any]]) -> None:
        nonlocal malformed_verify_items
        async with semaphore:
            ct = text_by_chapter.get(ch_num)
            if not ct:
                # No full text available — keep issues as-is
                await _mark_processed(ch_num)
                return

            input_data = BookConsistencyVerifyInput(
                chapter_number=ch_num,
                numbered_text=str(ct.get("numbered_text", "") or ""),
                paragraph_count=int(ct.get("paragraph_count", 0) or 0),
                chapter_summary=summary_by_chapter.get(ch_num, ""),
                related_chapters_context=_build_related_chapters_context(
                    current_chapter=ch_num,
                    chapter_issues=ch_issues,
                    text_by_chapter=text_by_chapter,
                    summary_by_chapter=summary_by_chapter,
                ),
                canon_characters=canon_characters,
                claimed_issues=ch_issues,
                max_tokens=max_tokens,
                temperature=temperature,
            )

            try:
                result = await step.run(input_data)
            except (ModelGatewayError, asyncio.TimeoutError, ConnectionError, TimeoutError) as exc:
                # Verification is a confidence filter, not the source of truth.
                # If the verification LLM call itself fails, keep the original
                # audit issues so real problems are not silently dropped before
                # repair.  Mark them for reporting/diagnostics instead.
                for _fi in ch_issues:
                    _fid = str(_fi.get("issue_id", "") or "").strip()
                    if _fid:
                        verification_failed_updates[_fid] = {
                            "_verification_failed": True,
                            "verification_error": str(exc)[:500],
                        }
                _log.warning(
                    "book_consistency_verify: chapter %d verification LLM failed, "
                    "keeping %d issues as unverified",
                    ch_num,
                    len(ch_issues),
                )
                await _mark_processed(ch_num)
                return

            verified_list = (
                [v.model_dump() for v in result.verified_issues]
                if hasattr(result, "verified_issues")
                else (result.get("verified_issues", []) if isinstance(result, dict) else [])
            )
            if not isinstance(verified_list, list):
                malformed_verify_items += 1
                await _mark_processed(ch_num)
                return

            malformed_verify_items += sum(1 for item in verified_list if not isinstance(item, dict))
            normalized_verified = [item for item in verified_list if isinstance(item, dict)]
            _apply_verified_list(ch_num, normalized_verified)
            await _mark_processed(ch_num, normalized_verified)

    for chapter_num in sorted(completed_chapters):
        cached_verified = verified_by_chapter.get(chapter_num, [])
        if cached_verified:
            _apply_verified_list(chapter_num, cached_verified)

    pending_chapters = [
        (ch, issues)
        for ch, issues in sorted(issues_by_chapter.items())
        if ch not in completed_chapters
    ]

    if parallel_chunks and pending_chapters:
        tasks = [
            asyncio.create_task(_verify_chapter(ch, issues)) for ch, issues in pending_chapters
        ]
        await asyncio.gather(*tasks)
    else:
        for ch, issues in pending_chapters:
            current_task = asyncio.current_task()
            if current_task is not None and current_task.cancelling():
                raise asyncio.CancelledError
            await _verify_chapter(ch, issues)

    # Confidence → verification_status mapping per spec:
    #   confidence >= 0.8 → "confirmed"
    #   0.5 <= confidence < 0.8 → "suspected"
    #   confidence < 0.5 → "unlikely"
    # Backward-compatible aliases: "verified" ≈ "confirmed", "questionable" ≈ "suspected"
    updated_issues: list[dict[str, Any]] = []
    removed_count = 0
    questionable_count = 0
    retained_count = 0

    # Confidence distribution counters per severity
    _confidence_distribution: dict[str, dict[str, int]] = {
        "critical": {"confirmed": 0, "suspected": 0, "unlikely": 0},
        "warning": {"confirmed": 0, "suspected": 0, "unlikely": 0},
        "info": {"confirmed": 0, "suspected": 0, "unlikely": 0},
    }

    for issue in report_issues:
        iid = str(issue.get("issue_id", "") or "").strip()
        if iid in rejected_ids:
            removed_count += 1
            continue

        if iid in verified_issue_map:
            issue = {**issue, **verified_issue_map[iid]}
        elif iid in verification_failed_updates:
            issue = {**issue, **verification_failed_updates[iid]}

        v_conf = issue.get("confidence")
        severity = str(issue.get("severity", "warning") or "warning").strip().lower()
        # Normalize severity to canonical buckets
        if severity not in _confidence_distribution:
            severity = "warning"

        if isinstance(v_conf, (int, float)):
            conf_val = float(v_conf)
            issue["verify_confidence"] = conf_val
            if conf_val >= 0.8:
                issue["verification_status"] = "confirmed"
                _confidence_distribution[severity]["confirmed"] += 1
                retained_count += 1
            elif conf_val >= 0.5:
                issue["verification_status"] = "suspected"
                _confidence_distribution[severity]["suspected"] += 1
                retained_count += 1
            else:
                issue["verification_status"] = "unlikely"
                _confidence_distribution[severity]["unlikely"] += 1
                questionable_count += 1
        else:
            retained_count += 1

        updated_issues.append(issue)

    if on_step_progress:
        on_step_progress(
            "book_consistency_verify_done",
            {
                "status": "done",
                "total_issues": len(report_issues),
                "verified": len(verified_issue_map),
                "rejected": len(rejected_ids),
                "verification_failed": len(verification_failed_updates),
                "malformed": malformed_verify_items,
                "modified_guarded": modified_guarded_count,
                "remaining": len(updated_issues),
                "removed_by_confidence": removed_count,
                "questionable_by_confidence": questionable_count,
                "retained_by_confidence": retained_count,
                "confidence_distribution": _confidence_distribution,
            },
        )

    _save_verify_checkpoint(
        checkpoint_path,
        {
            "schema_version": 1,
            "signature": checkpoint_signature,
            "completed_chapters": sorted(completed_chapters),
            "verified_by_chapter": verified_by_chapter,
            "total_chapters": total,
            "status": "completed",
            "checksum": _compute_verify_checksum(
                sorted(completed_chapters),
                verified_by_chapter,
            ),
        },
    )

    verify_stats = {
        "total_issues": len(report_issues),
        "verified": len(verified_issue_map),
        "rejected": len(rejected_ids),
        "verification_failed": len(verification_failed_updates),
        "malformed": malformed_verify_items,
        "modified_guarded": modified_guarded_count,
        "remaining": len(updated_issues),
        "suspected_total": len(suspected_ids),
        "suspected_retained": sum(
            1
            for iss in updated_issues
            if str(iss.get("issue_id", "") or "").strip() in suspected_ids
        ),
        "removed_count": removed_count,
        "questionable_count": questionable_count,
        "retained_count": retained_count,
        "confidence_distribution": _confidence_distribution,
    }
    return updated_issues, verify_stats
