"""Post-repair helpers for whole-book consistency audit execution."""

from __future__ import annotations

from typing import Any

from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.project_staleness import (
    finalized_chapter_numbers,
    update_canon_watermark,
)
from novel_forge.pipeline.long.services.context.story_kernel_context import (
    _compute_canon_state_hash,
)
from novel_forge.workspace.book_ops.execution_book_common import (
    _compute_chapter_text_hashes,
    _load_issue_panel_pool_for_chapters,
    _log,
    _resolve_issue_pool_ttl_hours,
)
from novel_forge.workspace.book_repair_metrics import book_repair_detail_was_applied
from novel_forge.workspace.contracts import BookConsistencyRequest
from novel_forge.workspace.execution_result import StepCallback
from novel_forge.workspace.helpers.execution_helpers import (
    _build_numbered_paragraph_text,
    _touch_downstream_context,
)
from novel_forge.workspace.runtime import RuntimeServices


def _lightweight_evidence_verify(
    report_issues: list[dict[str, Any]],
    completed_chapters: list[int],
    layout: ProjectLayout,
    *,
    prefer_official_text: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Lightweight text-match verification for summary mode.

    Checks if each issue's evidence string exists in the chapter text.
    No LLM calls - purely text matching.

    Args:
        report_issues: List of issue dicts with evidence and primary_chapter
        completed_chapters: List of completed chapter numbers
        layout: ProjectLayout for resolving chapter paths

    Returns:
        Tuple of (filtered_issues, stats_dict)
        - filtered_issues: issues with verification_status != "rejected"
        - stats_dict: contains confirmed, rejected, checked counts
    """
    stats: dict[str, Any] = {
        "checked": 0,
        "confirmed": 0,
        "suspected": 0,
        "rejected": 0,
        "errors": 0,
        "weak_evidence": 0,
        "missing_text": 0,
        "manual_review": 0,
    }
    filtered: list[dict[str, Any]] = []

    def _mark_suspected(issue: dict[str, Any], *, reason: str) -> dict[str, Any]:
        updated = issue.copy()
        severity = str(updated.get("severity", "info") or "info").strip().lower()
        updated["verification_status"] = "suspected"
        updated["lightweight_verify_reason"] = reason
        current_conf = float(updated.get("confidence", 0.0) or 0.0)
        if severity in {"critical", "high"}:
            updated["confidence"] = max(current_conf, 0.65)
        else:
            updated["confidence"] = min(max(current_conf, 0.45), 0.65)
            updated["needs_manual_review"] = True
            updated["auto_repair_eligible"] = False
            stats["manual_review"] += 1
        stats["suspected"] += 1
        return updated

    # Build a cache of chapter texts for chapters that have issues
    chapters_with_issues = sorted(
        {
            int(i.get("primary_chapter", 0) or 0)
            for i in report_issues
            if int(i.get("primary_chapter", 0) or 0) > 0
        }
    )

    # Load chapter texts into cache
    chapter_texts: dict[int, str] = {}
    for ch in chapters_with_issues:
        if ch in completed_chapters:
            draft_path = layout.chapter_review_draft_path(ch)
            chapter_path = layout.chapter_path(ch)
            if prefer_official_text and chapter_path.exists():
                try:
                    chapter_texts[ch] = chapter_path.read_text(encoding="utf-8")
                except OSError:
                    pass
            elif draft_path.exists():
                try:
                    chapter_texts[ch] = draft_path.read_text(encoding="utf-8")
                except OSError:
                    pass
            if ch not in chapter_texts:
                # Fall back to regular chapter path
                if chapter_path.exists():
                    try:
                        chapter_texts[ch] = chapter_path.read_text(encoding="utf-8")
                    except OSError:
                        pass

    # Verify each issue
    for issue in report_issues:
        primary_ch = int(issue.get("primary_chapter", 0) or 0)
        evidence = str(issue.get("evidence", "") or "").strip()

        stats["checked"] += 1

        # Summary mode cannot prove weakly anchored issues. Keep them, but avoid
        # feeding lower-confidence guesses directly into auto-repair.
        if primary_ch <= 0 or not evidence:
            stats["weak_evidence"] += 1
            filtered.append(
                _mark_suspected(
                    issue,
                    reason="missing_primary_chapter" if primary_ch <= 0 else "missing_evidence",
                )
            )
            continue

        # Try to find evidence in chapter text
        chapter_text = chapter_texts.get(primary_ch, "")
        if not chapter_text:
            stats["missing_text"] += 1
            filtered.append(_mark_suspected(issue, reason="chapter_text_unavailable"))
            continue

        # Text match check
        if evidence in chapter_text:
            # Evidence found - confirm
            issue = issue.copy()
            issue["verification_status"] = "confirmed"
            issue["confidence"] = 1.0
            stats["confirmed"] += 1
            filtered.append(issue)
        else:
            # Evidence not found - reject
            issue = issue.copy()
            issue["verification_status"] = "rejected"
            issue["confidence"] = 0.0
            stats["rejected"] += 1
            # Don't include rejected issues in filtered list

    return filtered, stats


def _post_repair_audit_target_chapters(
    *,
    auto_repair_payload: dict[str, Any] | None,
    report_issues: list[dict[str, Any]],
    completed_chapters: list[int],
    max_chapters: int,
) -> list[int]:
    """Select a compact post-repair audit target set."""
    completed = set(completed_chapters)
    targets: set[int] = set(_applied_book_repair_chapters(auto_repair_payload))
    if isinstance(auto_repair_payload, dict):
        prop_by_chapter = auto_repair_payload.get("propagation_by_chapter") or {}
        if isinstance(prop_by_chapter, dict):
            for raw_ch in prop_by_chapter:
                try:
                    chapter = int(raw_ch)
                except (TypeError, ValueError):
                    continue
                if chapter > 0:
                    targets.add(chapter)
    if not targets:
        for issue in report_issues:
            if not isinstance(issue, dict):
                continue
            for raw_ch in [
                issue.get("primary_chapter"),
                *(issue.get("chapters_involved", []) or []),
            ]:
                try:
                    chapter = int(raw_ch or 0)
                except (TypeError, ValueError):
                    continue
                if chapter > 0:
                    targets.add(chapter)
    selected = sorted(ch for ch in targets if ch in completed)
    return selected[: max(1, max_chapters)]


async def _run_post_repair_targeted_audit(
    *,
    runtime: RuntimeServices,
    request: BookConsistencyRequest,
    layout: ProjectLayout,
    target_chapters: list[int],
    max_tokens: int,
    temperature: float,
    on_step_progress: StepCallback = None,
    prefer_official_text: bool = False,
) -> dict[str, Any]:
    """Run an optional small full-text audit after book-level repair."""
    from novel_forge.obs.tracer import PipelineTrace
    from novel_forge.pipeline.steps.book_consistency_step import (
        BookConsistencyInput,
        BookConsistencyStep,
    )
    from novel_forge.story_kernel.store import StoryKernelStore

    if not target_chapters:
        return {"enabled": True, "status": "skipped", "reason": "no_target_chapters"}

    if on_step_progress:
        on_step_progress(
            "book_consistency_post_audit_start",
            {"status": "running", "target_chapters": target_chapters},
        )

    chapter_summaries: list[dict[str, Any]] = []
    chapter_texts: list[dict[str, Any]] = []
    max_chars = int(
        request.chapter_max_chars
        if request.chapter_max_chars is not None
        else int(getattr(runtime.settings, "long_book_audit_chapter_max_chars", 12000))
    )
    max_chars = max(1000, min(max_chars, 100000))
    for chapter_number in target_chapters:
        summary_entry: dict[str, Any] = {"chapter_number": chapter_number}
        report_path = layout.creative_report_path(chapter_number)
        if report_path.exists():
            try:
                report_raw = runtime.storage.load_json(report_path)
                structured = report_raw.get("structured_summary", "")
                if isinstance(structured, dict):
                    summary_entry["summary"] = structured.get("one_line_summary", "")
                    summary_entry["key_events"] = structured.get("key_events", [])
                else:
                    summary_entry["summary"] = str(structured) if structured else ""
            except Exception:
                _log.debug("post-repair targeted audit: summary load failed", exc_info=True)
        chapter_summaries.append(summary_entry)

        text = ""
        chapter_path = layout.chapter_path(chapter_number)
        review_draft_path = layout.chapter_review_draft_path(chapter_number)
        if prefer_official_text and chapter_path.exists():
            text = chapter_path.read_text(encoding="utf-8")
        elif review_draft_path.exists():
            text = review_draft_path.read_text(encoding="utf-8")
        elif chapter_path.exists():
            text = chapter_path.read_text(encoding="utf-8")
        source_chars = len(text)
        truncated = source_chars > max_chars
        if truncated:
            text = text[:max_chars]
        numbered_text, paragraph_count, paragraphs = _build_numbered_paragraph_text(text)
        chapter_texts.append(
            {
                "chapter_number": chapter_number,
                "numbered_text": numbered_text,
                "paragraph_count": paragraph_count,
                "paragraphs": paragraphs,
                "source_chars": source_chars,
                "truncated": truncated,
            }
        )

    outline_raw = (
        runtime.storage.load_json(layout.outline_path) if layout.outline_path.exists() else {}
    )
    char_bible_raw = (
        runtime.storage.load_json(layout.characters_path) if layout.characters_path.exists() else {}
    )
    canon_state = await StoryKernelStore(layout.story_kernel_db_path).load_kernel(
        request.project_id
    )
    issue_pool = _load_issue_panel_pool_for_chapters(
        storage=runtime.storage,
        layout=layout,
        chapter_numbers=target_chapters,
        ttl_hours=_resolve_issue_pool_ttl_hours(runtime.settings),
        chapter_text_hash_by_chapter=_compute_chapter_text_hashes(
            runtime.storage, layout, target_chapters
        ),
        canon_state_hash=_compute_canon_state_hash(canon_state),
    )
    step = BookConsistencyStep(
        runtime.router,
        runtime.builder,
        settings=runtime.settings,
        trace=PipelineTrace(),
    )
    result = await step.run(
        BookConsistencyInput(
            chapter_summaries=chapter_summaries,
            canon_state_snapshot=canon_state.model_dump(mode="json")
            if hasattr(canon_state, "model_dump")
            else {},
            character_bible=char_bible_raw,
            outline=outline_raw,
            chapter_texts=chapter_texts,
            chapter_issue_pool=issue_pool,
            analysis_mode="full_text",
            max_chapters_per_batch=max(1, len(target_chapters)),
            max_issues_per_chunk=int(getattr(request, "audit_max_issues_per_chunk", 12) or 12),
            issue_pool_max_items=int(getattr(request, "audit_issue_pool_max_items", 160) or 160),
            prompt_hint=(
                str(getattr(request, "prompt_hint", "") or "").strip()
                + "\n修复后小范围复审：只报告修复仍未闭合、修复引入的新矛盾或受影响章节的传播问题。"
            ).strip(),
            location_strictness=str(
                getattr(request, "location_strictness", "balanced") or "balanced"
            ),
            max_tokens=max(2048, max_tokens // 2),
            temperature=temperature,
            parallel_chunks=False,
            parallel_dimensions=False,
        )
    )
    payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else {}
    issues = payload.get("issues", []) if isinstance(payload.get("issues"), list) else []
    audit_payload = {
        "enabled": True,
        "status": "completed",
        "target_chapters": target_chapters,
        "issue_count": len(issues),
        "issues": issues,
        "summary": payload.get("summary", ""),
        "consistency_score": payload.get("consistency_score", 0.0),
        "quality_metrics": payload.get("quality_metrics"),
        "field_sources": payload.get("field_sources") or {},
    }
    if on_step_progress:
        on_step_progress(
            "book_consistency_post_audit_done",
            {
                "status": "done",
                "target_chapters": target_chapters,
                "issue_count": len(issues),
            },
        )
    return audit_payload


def _applied_book_repair_chapters(auto_repair_payload: dict[str, Any] | None) -> list[int]:
    """Return chapters whose full-book repair was actually applied."""
    if not isinstance(auto_repair_payload, dict):
        return []
    applied: set[int] = set()
    for detail in auto_repair_payload.get("details", []) or []:
        if not isinstance(detail, dict):
            continue
        if not book_repair_detail_was_applied(detail):
            continue
        try:
            chapter_number = int(detail.get("chapter_number", 0) or 0)
        except (TypeError, ValueError):
            continue
        if chapter_number > 0:
            applied.add(chapter_number)
    return sorted(applied)


def _refresh_book_audit_staleness_markers(
    *,
    storage: Any,
    layout: ProjectLayout,
    auto_repair_payload: dict[str, Any] | None,
) -> None:
    """Refresh staleness markers after a whole-book audit.

    Book-level repair edits already finalized chapters in place. Without
    restamping downstream planning context, the chapter studio can interpret
    those edits as an upstream rewrite and mark later chapters as invalid.
    """
    finalized = finalized_chapter_numbers(layout)
    if finalized:
        update_canon_watermark(storage, layout, max(finalized))

    applied_chapters = _applied_book_repair_chapters(auto_repair_payload)
    if not applied_chapters:
        return

    try:
        from novel_forge.core.utils.edit_tracker import save_snapshot

        for chapter_number in applied_chapters:
            save_snapshot(layout.chapter_path(chapter_number), layout.states_dir, chapter_number)
    except Exception as exc:  # noqa: BLE001 - non-critical UI freshness marker
        _log.warning("Failed to refresh book-audit edit snapshots: %s", exc)

    for chapter_number in applied_chapters:
        _touch_downstream_context(
            layout,
            chapter_number,
            touch_from=chapter_number + 1,
        )
