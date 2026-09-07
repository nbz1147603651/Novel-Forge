"""Whole-book consistency audit runner."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from novel_forge.core.exceptions import StorageError
from novel_forge.memory.audit_evidence import build_book_audit_evidence_context
from novel_forge.persistence.models import ProjectLayout
from novel_forge.workspace.book_audit_checkpoint_store import (
    _book_audit_resume_signature,
    _load_final_audit_result_checkpoint,
    _load_two_phase_summary_checkpoint,
    _save_final_audit_result_checkpoint,
    _save_two_phase_summary_checkpoint,
    book_audit_checkpoint_path,
    invalidate_checkpoint,
    validate_checkpoint,
)
from novel_forge.workspace.book_ops.book_audit_incremental import (
    build_book_audit_input_manifest,
    plan_book_audit_reuse,
)
from novel_forge.workspace.book_ops.execution_book_audit_helpers import (
    _extract_flagged_chapters_from_result,
    _file_digest,
    _issue_as_dict,
    _load_chapter_texts_for_numbers,
    _merge_two_phase_audit_results,
    _rank_book_audit_target_chapters,
)
from novel_forge.workspace.book_ops.execution_book_audit_report import (
    _load_latest_audit_payload,
)
from novel_forge.workspace.book_ops.execution_book_common import (
    _load_issue_panel_pool_for_chapters,
    _log,
    _resolve_book_audit_analysis_mode,
    _resolve_issue_pool_ttl_hours,
)
from novel_forge.workspace.contracts import BookConsistencyRequest
from novel_forge.workspace.execution_result import StepCallback
from novel_forge.workspace.helpers.execution_runners import _project_lock
from novel_forge.workspace.runtime import RuntimeServices


async def run_book_consistency_audit(
    runtime: RuntimeServices,
    request: BookConsistencyRequest,
    *,
    on_step_progress: StepCallback = None,
) -> Any:
    """Run the audit phase of whole-book consistency check.

    Loads chapters, canon state, builds context, runs the BookConsistencyStep,
    and saves the audit report.  Does NOT perform verification or repair.

    Returns a raw ``BookConsistencyResult``.
    """
    from novel_forge.obs.tracer import PipelineTrace
    from novel_forge.pipeline.steps.book_consistency_step import (
        BookConsistencyInput,
        BookConsistencyStep,
    )
    from novel_forge.story_kernel.composer import ContextComposer
    from novel_forge.workspace.global_audit import (
        GlobalAuditPlanner,
        GlobalAuditStore,
        GlobalRepairQueueExecutor,
        SemanticEvidenceLocator,
        build_chapter_paragraph_index,
    )

    report_path = None
    completed_chapters: list[int] = []
    chapter_issue_pool: list[dict[str, Any]] = []
    global_audit_run_id = ""
    global_audit_store: GlobalAuditStore | None = None
    audit_slice_payloads: list[dict[str, Any]] = []
    kernel_context: dict[str, Any] = {}
    paragraphs_by_chapter: dict[int, list[str]] = {}
    chapter_hashes: dict[int, str] = {}
    audit_input_manifest: dict[str, Any] = {}
    incremental_reuse_payload: dict[str, Any] = {}
    seeded_dimension_results: dict[str, list[dict[str, Any]]] = {}

    async with _project_lock(runtime, request.project_id):
        layout = ProjectLayout(runtime.storage.existing_project_dir(request.project_id))

        if request.chapter_range:
            completed_chapters = sorted(request.chapter_range)
        else:
            for p in sorted(layout.chapters_dir.glob("chapter_*.md")):
                try:
                    num = int(p.stem.split("_")[1])
                    completed_chapters.append(num)
                except (IndexError, ValueError):
                    continue

        if len(completed_chapters) < 2:
            raise ValueError("至少需要 2 个已完成章节才能进行全书一致性检查。")

    async with _project_lock(runtime, request.project_id):
        from novel_forge.story_kernel.store import StoryKernelStore

        checkpoint_path = book_audit_checkpoint_path(layout)
        auto_resume_audit_checkpoint = False
        if bool(getattr(request, "reset_audit_checkpoint", False)):
            try:
                checkpoint_path.unlink()
            except FileNotFoundError:
                pass
        elif checkpoint_path.exists():
            validation = validate_checkpoint(checkpoint_path)
            if not validation["valid"]:
                _log.warning(
                    "Invalid book-audit checkpoint ignored | path=%s | error=%s",
                    checkpoint_path,
                    validation.get("error"),
                )
                invalidate_checkpoint(checkpoint_path)
            else:
                cp_data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                stored_summaries = cp_data.get("chapter_summaries", [])
                stored_chapter_count = (
                    len(stored_summaries) if isinstance(stored_summaries, list) else 0
                )
                if stored_chapter_count > 0 and stored_chapter_count != len(completed_chapters):
                    _log.warning(
                        "Checkpoint chapter count (%d) differs from current (%d); invalidating checkpoint",
                        stored_chapter_count,
                        len(completed_chapters),
                    )
                    invalidate_checkpoint(checkpoint_path)
                else:
                    auto_resume_audit_checkpoint = True

        canon_store = StoryKernelStore(layout.story_kernel_db_path)
        canon_state = await canon_store.load_kernel(request.project_id)

        outline_raw = (
            runtime.storage.load_json(layout.outline_path) if layout.outline_path.exists() else {}
        )
        blueprint_raw = (
            runtime.storage.load_json(layout.blueprint_path)
            if layout.blueprint_path.exists()
            else {}
        )
        try:
            kernel_context = ContextComposer.from_kernel(
                canon_state
            ).compose_book_consistency_input(max(completed_chapters))
        except Exception:
            _log.warning(
                "StoryKernel context composition failed for book audit; using raw snapshot only.",
                exc_info=True,
            )
            kernel_context = {}

        audit_slices = GlobalAuditPlanner().plan(
            completed_chapters=completed_chapters,
            outline=outline_raw,
            blueprint=blueprint_raw,
            kernel_context=kernel_context,
        )
        audit_slice_payloads = [item.model_dump() for item in audit_slices]
        paragraph_rows, paragraphs_by_chapter, chapter_hashes = build_chapter_paragraph_index(
            layout,
            completed_chapters,
        )
        global_audit_run_id = (
            "global_audit_"
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            + "_"
            + hashlib.sha256(
                json.dumps(
                    {
                        "project_id": request.project_id,
                        "chapters": completed_chapters,
                        "slices": [item.get("slice_id") for item in audit_slice_payloads],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()[:8]
        )
        global_audit_store = GlobalAuditStore(layout.global_audit_db_path)
        analysis_mode = _resolve_book_audit_analysis_mode(runtime, request)
        global_audit_store.start_run(
            run_id=global_audit_run_id,
            project_id=request.project_id,
            chapter_numbers=completed_chapters,
            analysis_mode=analysis_mode,
            metadata={"architecture": "global_audit_v1"},
        )
        global_audit_store.replace_slices(global_audit_run_id, audit_slice_payloads)
        global_audit_store.replace_chapter_paragraph_index(
            global_audit_run_id,
            paragraph_rows,
        )

        chapter_summaries: list[dict[str, Any]] = []
        for ch_num in completed_chapters:
            report_path = layout.creative_report_path(ch_num)
            summary_entry: dict[str, Any] = {"chapter_number": ch_num}
            if report_path.exists():
                report_raw = runtime.storage.load_json(report_path)
                _ss = report_raw.get("structured_summary", "")
                if isinstance(_ss, dict):
                    summary_entry["summary"] = _ss.get("one_line_summary", "")
                    summary_entry["key_events"] = _ss.get("key_events", [])
                else:
                    summary_entry["summary"] = str(_ss) if _ss else ""
                    summary_entry["key_events"] = (
                        report_raw.get("key_moments")
                        or report_raw.get("key_revelations")
                        or report_raw.get("must_carry_forward")
                        or []
                    )
            chapter_summaries.append(summary_entry)

        if on_step_progress:
            on_step_progress(
                "book_consistency_start",
                {
                    "chapters": completed_chapters,
                    "count": len(completed_chapters),
                    "architecture": "global_audit_v1",
                    "slice_count": len(audit_slice_payloads),
                    "db_path": str(layout.global_audit_db_path),
                },
            )

        use_issue_panel_pool = bool(
            getattr(
                request,
                "use_issue_panel_pool",
                bool(getattr(runtime.settings, "long_book_audit_use_issue_panel_pool", True)),
            )
        )
        if use_issue_panel_pool:
            chapter_issue_pool = _load_issue_panel_pool_for_chapters(
                storage=runtime.storage,
                layout=layout,
                chapter_numbers=completed_chapters,
                ttl_hours=_resolve_issue_pool_ttl_hours(runtime.settings),
            )
        if on_step_progress:
            on_step_progress(
                "book_consistency_issue_pool_ready",
                {
                    "enabled": use_issue_panel_pool,
                    "issue_pool_size": len(chapter_issue_pool),
                    "chapters": len(completed_chapters),
                },
            )

        analysis_mode = _resolve_book_audit_analysis_mode(runtime, request)

        char_bible_raw = {}
        if layout.characters_path.exists():
            char_bible_raw = runtime.storage.load_json(layout.characters_path)

        # ── Story-bible: world rules + setting ────────────────────────────
        world_rules: list[str] = []
        world_setting_parts: list[str] = []
        story_bible_path = layout.root / "story_bible.json"
        if story_bible_path.exists():
            try:
                sb = runtime.storage.load_json(story_bible_path) or {}
                raw_rules = sb.get("rules", [])
                if isinstance(raw_rules, list):
                    world_rules = [str(r).strip() for r in raw_rules if r and str(r).strip()]
                elif isinstance(raw_rules, str) and raw_rules.strip():
                    world_rules = [raw_rules.strip()]
                for field_name in ("era", "geography", "culture", "magic_or_tech"):
                    val = str(sb.get(field_name, "") or "").strip()
                    if val:
                        world_setting_parts.append(val[:300])
            except (OSError, StorageError):
                _log.debug("story_bible load failed for world consistency", exc_info=True)
        world_setting = "\n".join(world_setting_parts)[:1000]

        # ── Character compact profiles ────────────────────────────────────
        character_profiles_compact: list[dict[str, Any]] = []
        bible_chars = char_bible_raw.get("characters", [])
        if isinstance(bible_chars, list):
            for ch_entry in bible_chars:
                if not isinstance(ch_entry, dict):
                    continue
                name = str(ch_entry.get("name", "") or "").strip()
                if not name:
                    continue
                profile: dict[str, Any] = {"name": name}
                for field_name in ("gender", "role", "status"):
                    v = str(ch_entry.get(field_name, "") or "").strip()
                    if v:
                        profile[field_name] = v
                for long_field in ("backstory", "personality", "arc"):
                    v = str(ch_entry.get(long_field, "") or "").strip()
                    if v:
                        profile[long_field] = v[:120]
                        break
                character_profiles_compact.append(profile)

        # ── Arc / volume summary from outline ─────────────────────────────
        arc_summary_parts: list[str] = []
        if outline_raw:
            volumes = outline_raw.get("volumes") or []
            if isinstance(volumes, list):
                for vol in volumes:
                    if not isinstance(vol, dict):
                        continue
                    title = str(vol.get("title", "") or "").strip()
                    arc_s = str(vol.get("arc_summary", "") or vol.get("premise", "") or "").strip()
                    ch_range = vol.get("chapter_range", [])
                    if title or arc_s:
                        line_parts = []
                        if title:
                            line_parts.append(title)
                        if ch_range:
                            line_parts.append(f"（第{ch_range[0]}-{ch_range[-1]}章）")
                        if arc_s:
                            line_parts.append(arc_s[:200])
                        arc_summary_parts.append("·".join(line_parts))
            if not arc_summary_parts:
                synopsis = str(outline_raw.get("synopsis", "") or "").strip()
                if synopsis:
                    arc_summary_parts.append(synopsis[:400])
        arc_summary = "\n".join(arc_summary_parts)

        # ── Spec: theme / conflict / world_hint ───────────────────────────
        story_theme: str = ""
        conflict_hint: str = ""
        world_hint: str = ""
        spec_path = layout.root / "spec.json"
        if spec_path.exists():
            try:
                spec_data = runtime.storage.load_json(spec_path) or {}
                story_theme = str(spec_data.get("theme", "") or "").strip()[:600]
                conflict_hint = str(spec_data.get("conflict_hint", "") or "").strip()[:600]
                world_hint = str(spec_data.get("world_hint", "") or "").strip()[:400]
            except (OSError, StorageError):
                _log.debug("spec load failed for world consistency", exc_info=True)

        # Priority: request param > settings > default
        max_tokens = int(
            request.max_tokens
            if request.max_tokens is not None
            else int(getattr(runtime.settings, "long_book_audit_max_tokens", 8192))
        )
        # Priority: request param > settings > default
        temperature = float(
            request.temperature
            if request.temperature is not None
            else float(getattr(runtime.settings, "temp_book_consistency", 0.2))
        )
        # Priority: request param > settings > default
        location_strictness = (
            str(
                getattr(request, "location_strictness", "")
                or getattr(runtime.settings, "long_book_audit_location_strictness", "balanced")
            )
            .strip()
            .lower()
        )
        if location_strictness not in {"strict", "balanced", "loose"}:
            location_strictness = "balanced"
        # Priority: request param > settings > default
        prompt_hint = str(getattr(request, "prompt_hint", "") or "").strip()
        if not prompt_hint:
            prompt_hint = str(
                getattr(runtime.settings, "long_book_audit_prompt_hint", "") or ""
            ).strip()
        # Priority: request param > settings > default
        audit_max_chapters_per_batch = max(
            1,
            min(
                int(
                    getattr(
                        request,
                        "audit_max_chapters_per_batch",
                        int(
                            getattr(runtime.settings, "long_book_audit_max_chapters_per_batch", 12)
                        ),
                    )
                    or 12
                ),
                500,
            ),
        )
        audit_max_issues_per_chunk = max(
            1,
            min(
                int(
                    getattr(
                        request,
                        "audit_max_issues_per_chunk",
                        int(getattr(runtime.settings, "long_book_audit_max_issues_per_chunk", 12)),
                    )
                    or 12
                ),
                50,
            ),
        )
        audit_issue_pool_max_items = max(
            0,
            min(
                int(
                    getattr(
                        request,
                        "audit_issue_pool_max_items",
                        int(getattr(runtime.settings, "long_book_audit_issue_pool_max_items", 160)),
                    )
                    or 0
                ),
                1000,
            ),
        )

        use_memory_enhancement = True
        memory_enhancement_context: str = ""
        memory_enhancement_failed: bool = False
        audit_memory_context: Any | None = None
        if use_memory_enhancement and analysis_mode == "full_text":
            try:
                get_memory_context = getattr(runtime, "get_memory_context", None)
                if callable(get_memory_context):
                    from novel_forge.memory.audit_coordinator import AuditCoordinator

                    memory_ctx = await get_memory_context(
                        project_id=request.project_id,
                        storage=runtime.storage,
                    )
                    if memory_ctx is not None:
                        audit_memory_context = memory_ctx
                        coordinator = AuditCoordinator(memory_ctx)
                        audit_context = await coordinator.prepare_audit_context(
                            chapter_number=completed_chapters[-1] if completed_chapters else 0,
                            chapter_text="",
                        )
                        memory_summary = audit_context.get_summary_for_prompt()
                        if memory_summary:
                            memory_enhancement_context = memory_summary
            except Exception:
                _log.warning(
                    "Memory enhancement failed for book audit, proceeding without enhanced context. Project: %s",
                    request.project_id,
                    exc_info=True,
                )
                memory_enhancement_failed = True

        trace = PipelineTrace()
        step = BookConsistencyStep(
            runtime.router,
            runtime.builder,
            settings=runtime.settings,
            trace=trace,
        )

        two_phase_enabled = bool(
            getattr(
                request,
                "two_phase_enabled",
                bool(getattr(runtime.settings, "long_book_audit_two_phase_enabled", True)),
            )
        )
        two_phase_threshold = float(
            getattr(
                request,
                "two_phase_threshold",
                float(getattr(runtime.settings, "long_book_audit_two_phase_threshold", 0.7)),
            )
        )
        two_phase_max_target_chapters = max(
            1,
            min(
                int(
                    getattr(
                        request,
                        "two_phase_max_target_chapters",
                        int(
                            getattr(
                                runtime.settings,
                                "long_book_audit_two_phase_max_target_chapters",
                                24,
                            )
                        ),
                    )
                    or 24
                ),
                len(completed_chapters),
            ),
        )

        _use_two_phase = (
            two_phase_enabled and analysis_mode == "full_text" and len(completed_chapters) >= 2
        )

        analysis_options = {
            "analysis_mode": analysis_mode,
            "prompt_hint": prompt_hint,
            "location_strictness": location_strictness,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "audit_max_chapters_per_batch": audit_max_chapters_per_batch,
            "audit_max_issues_per_chunk": audit_max_issues_per_chunk,
            "audit_issue_pool_max_items": audit_issue_pool_max_items,
            "two_phase_enabled": two_phase_enabled,
            "two_phase_threshold": two_phase_threshold,
            "two_phase_max_target_chapters": two_phase_max_target_chapters,
            "parallel_chunks": bool(getattr(request, "parallel_chunks", True)),
            "parallel_dimensions": bool(getattr(request, "parallel_dimensions", False)),
            "parallel_dimension_limit": getattr(request, "parallel_dimension_limit", None),
        }
        context_hashes = {
            "outline": _file_digest(layout.outline_path),
            "blueprint": _file_digest(layout.blueprint_path),
            "story_bible": _file_digest(layout.root / "story_bible.json"),
            "character_bible": _file_digest(layout.characters_path),
            "kernel_context": hashlib.sha256(
                json.dumps(kernel_context, ensure_ascii=False, sort_keys=True, default=str).encode(
                    "utf-8"
                )
            ).hexdigest(),
        }
        audit_input_manifest = build_book_audit_input_manifest(
            project_id=request.project_id,
            chapter_hashes=chapter_hashes,
            audit_slices=audit_slice_payloads,
            analysis_options=analysis_options,
            context_hashes=context_hashes,
        )
        reuse_plan = plan_book_audit_reuse(
            previous_payload=_load_latest_audit_payload(layout),
            current_manifest=audit_input_manifest,
            current_slices=audit_slice_payloads,
        )
        if bool(getattr(request, "parallel_dimensions", False)):
            seeded_dimension_results = reuse_plan.seeded_dimension_results
        incremental_reuse_payload = {
            "status": reuse_plan.status,
            "reason": reuse_plan.reason,
            "changed_chapters": reuse_plan.changed_chapters,
            "reusable_slice_ids": reuse_plan.reusable_slice_ids,
            "invalidated_slice_ids": reuse_plan.invalidated_slice_ids,
            "reused_dimension_result_count": sum(
                len(items) for items in seeded_dimension_results.values()
            ),
        }
        if on_step_progress:
            on_step_progress("book_consistency_incremental_plan", incremental_reuse_payload)

        resume_signature = _book_audit_resume_signature(
            layout=layout,
            project_id=request.project_id,
            completed_chapters=completed_chapters,
            chapter_summaries=chapter_summaries,
            chapter_issue_pool=chapter_issue_pool,
            analysis_mode=analysis_mode,
            prompt_hint=prompt_hint,
            location_strictness=location_strictness,
            max_tokens=max_tokens,
            temperature=temperature,
            audit_max_chapters_per_batch=audit_max_chapters_per_batch,
            audit_max_issues_per_chunk=audit_max_issues_per_chunk,
            audit_issue_pool_max_items=audit_issue_pool_max_items,
            two_phase_enabled=two_phase_enabled,
            two_phase_threshold=two_phase_threshold,
            two_phase_max_target_chapters=two_phase_max_target_chapters,
            parallel_chunks=bool(getattr(request, "parallel_chunks", True)),
            parallel_dimensions=bool(getattr(request, "parallel_dimensions", False)),
            memory_enhancement_context=memory_enhancement_context,
            world_rules=world_rules,
            world_setting=world_setting,
            story_theme=story_theme,
            conflict_hint=conflict_hint,
            world_hint=world_hint,
            character_profiles_compact=character_profiles_compact,
            arc_summary=arc_summary,
        )

        cached_result = (
            _load_final_audit_result_checkpoint(checkpoint_path, resume_signature)
            if auto_resume_audit_checkpoint
            else None
        )
        if cached_result is not None:
            if on_step_progress:
                on_step_progress(
                    "book_consistency_checkpoint_result_reused",
                    {"status": "done", "source": "checkpoint"},
                )
                on_step_progress("book_consistency", cached_result)
                on_step_progress("book_consistency_audit_saved", {"status": "done"})
            cached_result.memory_enhancement_failed = memory_enhancement_failed
            return cached_result

        chapter_texts: list[dict[str, Any]] = []
        summary_result: Any | None = None
        summary_only_result: Any | None = None
        if _use_two_phase:
            _log.info(
                "Two-phase audit enabled (threshold=%.2f, max_targets=%d). Running summary scan first.",
                two_phase_threshold,
                two_phase_max_target_chapters,
            )
            if on_step_progress:
                on_step_progress(
                    "book_consistency_two_phase_start",
                    {
                        "phase": "summary_scan",
                        "total_chapters": len(completed_chapters),
                        "threshold": two_phase_threshold,
                        "max_target_chapters": two_phase_max_target_chapters,
                    },
                )

            summary_trace = PipelineTrace()
            summary_step = BookConsistencyStep(
                runtime.router,
                runtime.builder,
                settings=runtime.settings,
                trace=summary_trace,
            )

            summary_input = BookConsistencyInput(
                chapter_summaries=chapter_summaries,
                canon_state_snapshot=canon_state.model_dump(mode="json")
                if hasattr(canon_state, "model_dump")
                else {},
                character_bible=char_bible_raw,
                outline=outline_raw,
                chapter_texts=[],
                chapter_issue_pool=chapter_issue_pool,
                analysis_mode="summary",
                max_chapters_per_batch=audit_max_chapters_per_batch,
                max_issues_per_chunk=max(12, min(32, two_phase_max_target_chapters)),
                issue_pool_max_items=audit_issue_pool_max_items,
                prompt_hint=prompt_hint,
                location_strictness=location_strictness,
                max_tokens=max_tokens,
                temperature=temperature,
                world_rules=world_rules,
                world_setting=world_setting,
                character_profiles_compact=character_profiles_compact,
                arc_summary=arc_summary,
                story_theme=story_theme,
                conflict_hint=conflict_hint,
                world_hint=world_hint,
                memory_enhancement_context="",
                kernel_context=kernel_context,
                audit_slices=audit_slice_payloads,
                parallel_chunks=bool(getattr(request, "parallel_chunks", True)),
                parallel_dimensions=bool(getattr(request, "parallel_dimensions", False)),
                parallel_dimension_limit=getattr(request, "parallel_dimension_limit", None),
            )
            summary_signature = BookConsistencyStep._checkpoint_signature(summary_input, [])
            summary_result = (
                _load_two_phase_summary_checkpoint(checkpoint_path, summary_signature)
                if auto_resume_audit_checkpoint
                else None
            )
            if summary_result is not None:
                if on_step_progress:
                    on_step_progress(
                        "book_consistency_two_phase_summary_reused",
                        {"phase": "summary_scan", "source": "checkpoint"},
                    )
            else:
                summary_result = await summary_step.run(summary_input)
                _save_two_phase_summary_checkpoint(
                    checkpoint_path,
                    resume_signature=resume_signature,
                    summary_signature=summary_signature,
                    summary_result=summary_result,
                    meta={
                        "total_chapters": len(completed_chapters),
                        "threshold": two_phase_threshold,
                        "max_target_chapters": two_phase_max_target_chapters,
                    },
                )

            flagged_chapters = _extract_flagged_chapters_from_result(summary_result)
            flag_ratio = (
                len(flagged_chapters) / len(completed_chapters) if completed_chapters else 0.0
            )
            target_chapters = _rank_book_audit_target_chapters(
                flagged_chapters=flagged_chapters,
                summary_result=summary_result,
                chapter_issue_pool=chapter_issue_pool,
                completed_chapters=completed_chapters,
                max_targets=two_phase_max_target_chapters,
            )

            _log.info(
                "Two-phase summary scan complete: %d/%d chapters flagged, %d targeted (ratio=%.2f, threshold=%.2f)",
                len(flagged_chapters),
                len(completed_chapters),
                len(target_chapters),
                flag_ratio,
                two_phase_threshold,
            )

            if on_step_progress:
                on_step_progress(
                    "book_consistency_two_phase_summary_done",
                    {
                        "phase": "summary_scan_complete",
                        "flagged_chapters": sorted(flagged_chapters),
                        "target_chapters": target_chapters,
                        "flagged_count": len(flagged_chapters),
                        "target_count": len(target_chapters),
                        "total_chapters": len(completed_chapters),
                        "flag_ratio": flag_ratio,
                        "threshold": two_phase_threshold,
                        "max_target_chapters": two_phase_max_target_chapters,
                    },
                )

            if not target_chapters:
                _log.info(
                    "Two-phase: summary scan and issue pool found no target chapters; finishing with summary result"
                )
                if on_step_progress:
                    on_step_progress(
                        "book_consistency_two_phase_summary_only",
                        {
                            "phase": "summary_only",
                            "reason": "no_target_chapters",
                            "flag_ratio": flag_ratio,
                            "threshold": two_phase_threshold,
                            "flagged_count": 0,
                        },
                    )
                summary_only_result = summary_result
            elif flag_ratio > two_phase_threshold:
                _log.warning(
                    "Two-phase: flag ratio %.2f > threshold %.2f; expanding to all %d flagged chapters",
                    flag_ratio,
                    two_phase_threshold,
                    len(flagged_chapters),
                )
                target_chapters = sorted(ch for ch in flagged_chapters if ch in completed_chapters)
                if on_step_progress:
                    on_step_progress(
                        "book_consistency_two_phase_expanded",
                        {
                            "phase": "expanded_to_all_flagged",
                            "flag_ratio": flag_ratio,
                            "threshold": two_phase_threshold,
                            "flagged_count": len(flagged_chapters),
                            "expanded_target_count": len(target_chapters),
                            "expanded_targets": target_chapters,
                        },
                    )
            if target_chapters:
                _log.info(
                    "Two-phase: proceeding with targeted full_text audit for %d chapters",
                    len(target_chapters),
                )
                if on_step_progress:
                    on_step_progress(
                        "book_consistency_two_phase_targeted_start",
                        {
                            "phase": "targeted_full_text",
                            "flagged_chapters": sorted(flagged_chapters),
                            "target_chapters": target_chapters,
                            "flagged_count": len(flagged_chapters),
                            "target_count": len(target_chapters),
                        },
                    )

                max_chars = int(
                    request.chapter_max_chars
                    if request.chapter_max_chars is not None
                    else int(getattr(runtime.settings, "long_book_audit_chapter_max_chars", 12000))
                )
                max_chars = max(1000, min(max_chars, 100000))
                chapter_texts = _load_chapter_texts_for_numbers(
                    layout=layout,
                    chapter_numbers=target_chapters,
                    max_chars=max_chars,
                )

        if analysis_mode == "full_text" and not _use_two_phase and summary_only_result is None:
            chapter_texts = []
            max_chars = int(
                request.chapter_max_chars
                if request.chapter_max_chars is not None
                else int(getattr(runtime.settings, "long_book_audit_chapter_max_chars", 12000))
            )
            max_chars = max(1000, min(max_chars, 100000))
            chapter_texts = _load_chapter_texts_for_numbers(
                layout=layout,
                chapter_numbers=completed_chapters,
                max_chars=max_chars,
            )

        audit_evidence_context = await build_book_audit_evidence_context(
            memory_context=audit_memory_context,
            chapter_summaries=chapter_summaries,
            chapter_texts=chapter_texts,
            chapter_issue_pool=chapter_issue_pool,
            completed_chapters=completed_chapters,
        )

        if on_step_progress:
            on_step_progress(
                "book_consistency",
                {
                    "status": "running",
                    "chapters": completed_chapters,
                    "count": len(completed_chapters),
                    "analysis_mode": analysis_mode,
                    "audit_max_chapters_per_batch": audit_max_chapters_per_batch,
                    "repair_mode": getattr(request, "repair_mode", "off"),
                },
            )

        if summary_only_result is not None:
            result = summary_only_result
        else:
            result = await step.run(
                BookConsistencyInput(
                    chapter_summaries=chapter_summaries,
                    canon_state_snapshot=canon_state.model_dump(mode="json")
                    if hasattr(canon_state, "model_dump")
                    else {},
                    character_bible=char_bible_raw,
                    outline=outline_raw,
                    chapter_texts=chapter_texts,
                    chapter_issue_pool=chapter_issue_pool,
                    analysis_mode=analysis_mode,
                    max_chapters_per_batch=audit_max_chapters_per_batch,
                    max_issues_per_chunk=audit_max_issues_per_chunk,
                    issue_pool_max_items=audit_issue_pool_max_items,
                    prompt_hint=prompt_hint,
                    location_strictness=location_strictness,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    world_rules=world_rules,
                    world_setting=world_setting,
                    character_profiles_compact=character_profiles_compact,
                    arc_summary=arc_summary,
                    story_theme=story_theme,
                    conflict_hint=conflict_hint,
                    world_hint=world_hint,
                    memory_enhancement_context=memory_enhancement_context,
                    kernel_context=kernel_context,
                    audit_slices=audit_slice_payloads,
                    semantic_evidence_context=audit_evidence_context,
                    audit_checkpoint_path=checkpoint_path if analysis_mode == "full_text" else None,
                    resume_audit_checkpoint=auto_resume_audit_checkpoint,
                    on_chunk_progress=(
                        lambda cur, total: (
                            on_step_progress(
                                "book_consistency_chunk_progress",
                                {"current": cur, "total": total, "phase": "audit"},
                            )
                            if on_step_progress
                            else None
                        )
                    ),
                    parallel_chunks=bool(getattr(request, "parallel_chunks", True)),
                    parallel_dimensions=bool(getattr(request, "parallel_dimensions", False)),
                    parallel_dimension_limit=getattr(request, "parallel_dimension_limit", None),
                    seeded_dimension_results=seeded_dimension_results,
                )
            )
            if summary_result is not None and _use_two_phase:
                result = _merge_two_phase_audit_results(summary_result, result)
        if hasattr(result, "input_manifest"):
            result.input_manifest = audit_input_manifest
        if hasattr(result, "incremental_reuse"):
            result.incremental_reuse = incremental_reuse_payload
        if global_audit_store is not None:
            locator = SemanticEvidenceLocator()

            def _paragraph_lookup(chapter_number: int) -> list[str]:
                return paragraphs_by_chapter.get(int(chapter_number), [])

            def _source_hash_lookup(chapter_number: int) -> str:
                return chapter_hashes.get(int(chapter_number), "")

            issue_payloads = [
                _issue_as_dict(issue) for issue in getattr(result, "issues", []) or []
            ]
            raw_global_findings = getattr(result, "global_findings", []) or []
            if not isinstance(raw_global_findings, list):
                raw_global_findings = []
            global_findings = [dict(item) for item in raw_global_findings if isinstance(item, dict)]
            for idx, finding in enumerate(global_findings):
                source_issue = issue_payloads[idx] if idx < len(issue_payloads) else finding
                candidates = locator.locate_issue(
                    source_issue,
                    paragraph_lookup=_paragraph_lookup,
                    source_hash_lookup=_source_hash_lookup,
                )
                if candidates:
                    finding["locator_candidates"] = candidates
                    readiness = finding.get("repair_readiness")
                    best_confirmed_conf = max(
                        (
                            float(item.get("confidence", 0.0) or 0.0)
                            for item in candidates
                            if str(item.get("locator_method") or "") in {"exact", "fuzzy"}
                        ),
                        default=0.0,
                    )
                    if not isinstance(readiness, dict) or readiness.get("status") != "blocked":
                        finding["repair_readiness"] = {
                            "status": "ready" if best_confirmed_conf >= 0.7 else "verify_first",
                            "risk": "medium",
                            "reasons": ["precision_locator_candidates"],
                            "auto_repair_eligible": best_confirmed_conf >= 0.7,
                        }
            if hasattr(result, "global_findings"):
                result.global_findings = global_findings

            queue_issues: list[dict[str, Any]] = []
            for idx, issue in enumerate(issue_payloads):
                enriched = dict(issue)
                if idx < len(global_findings):
                    enriched.update(
                        {
                            "slice_id": global_findings[idx].get("slice_id"),
                            "dimension": global_findings[idx].get("dimension"),
                            "locator_candidates": global_findings[idx].get(
                                "locator_candidates", []
                            ),
                            "repair_readiness": global_findings[idx].get("repair_readiness", {}),
                        }
                    )
                queue_issues.append(enriched)
            queue = GlobalRepairQueueExecutor(
                paragraph_lookup=_paragraph_lookup,
                source_hash_lookup=_source_hash_lookup,
                completed_chapters=completed_chapters,
            ).build_queue(
                run_id=global_audit_run_id,
                issues=queue_issues,
                slices=audit_slice_payloads,
            )
            normalized_revision_queue: list[dict[str, Any]] = []
            for item in queue.items:
                normalized_item = dict(item)
                target_chapter = int(normalized_item.get("target_chapter") or 0)
                raw_ticket = normalized_item.get("ticket")
                ticket = raw_ticket if isinstance(raw_ticket, dict) else {}
                dimension = str(ticket.get("dimension") or "global_consistency")
                normalized_item.update(
                    {
                        "audit_domain": "consistency",
                        "target_chapters": [target_chapter] if target_chapter > 0 else [],
                        "affected_dimensions": [dimension],
                        "source_text_hashes": {
                            str(target_chapter): str(normalized_item.get("source_hash") or "")
                        }
                        if target_chapter > 0
                        else {},
                        "impact_scope": {
                            "from_chapter": target_chapter or None,
                            "to_chapter": max(completed_chapters) if target_chapter else None,
                            "requires_preview": True,
                            "requires_state_replay": target_chapter > 0,
                        },
                        "repair_boundary": {
                            "repair_goal": ticket.get("repair_goal") or "",
                            "acceptance_criteria": ticket.get("acceptance_criteria") or [],
                            "must_preserve": ticket.get("must_preserve") or [],
                            "forbidden_changes": ticket.get("forbidden_changes") or [],
                        },
                    }
                )
                normalized_revision_queue.append(normalized_item)
            if hasattr(result, "repair_queue_summary"):
                result.repair_queue_summary = queue.summary
            if hasattr(result, "revision_queue"):
                result.revision_queue = normalized_revision_queue
            if hasattr(result, "coverage_metrics"):
                existing_coverage = getattr(result, "coverage_metrics", {}) or {}
                if not isinstance(existing_coverage, dict):
                    existing_coverage = {}
                result.coverage_metrics = {
                    **existing_coverage,
                    "db_path": str(layout.global_audit_db_path),
                    "run_id": global_audit_run_id,
                }
            global_audit_store.replace_findings(global_audit_run_id, global_findings)
            global_audit_store.replace_repair_queue_items(global_audit_run_id, queue.items)
            global_audit_store.finish_run(
                run_id=global_audit_run_id,
                status="completed",
                result_summary={
                    "issue_count": len(issue_payloads),
                    "global_finding_count": len(global_findings),
                    "repair_queue_summary": queue.summary,
                },
                coverage_metrics=getattr(result, "coverage_metrics", {}) or {},
            )
        _save_final_audit_result_checkpoint(
            checkpoint_path,
            resume_signature=resume_signature,
            result=result,
        )
        if on_step_progress:
            on_step_progress("book_consistency", result)

        # Report is saved by the caller (execute_book_consistency) after Phase 1
        # Persist checkpoint with "completed" status so audit results survive restarts.
        if checkpoint_path.exists():
            try:
                import json as _json

                _cp = _json.loads(checkpoint_path.read_text(encoding="utf-8"))
                if isinstance(_cp, dict):
                    _cp["status"] = "completed"
                    from novel_forge.persistence.filesystem import atomic_write_json

                    atomic_write_json(checkpoint_path, _cp)
            except (OSError, _json.JSONDecodeError):
                _log.warning("Failed to update checkpoint status to completed: %s", checkpoint_path)

    # Emit intermediate progress
    if on_step_progress:
        on_step_progress("book_consistency_audit_saved", {"status": "done"})

    result.memory_enhancement_failed = memory_enhancement_failed
    return result
