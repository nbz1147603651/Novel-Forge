"""Implementation slice extracted from finalize.py (finalize_persist.py)."""
# ruff: noqa: F403,F405,I001

from __future__ import annotations

from novel_forge.core.exceptions import ConsistencyViolationError, RecoveryTarget
from novel_forge.pipeline.artifact_manifest import ArtifactManifest
from novel_forge.pipeline.finalization_manifest import (
    matching_finalization_phase,
    record_finalization_pending,
    record_finalization_success,
)
from novel_forge.pipeline.long.stages.finalize_checks import (
    PersistedChapterArtifacts,
    _artifact_report_summary,
    _enforce_archive_hard_quality_blocks,
    _finalize_report_hash_transaction,
    _is_reading_power_stale,
    _persist_finalize_stage_artifact,
)
from novel_forge.pipeline.long.stages.finalize_common import *
from novel_forge.pipeline.long.stages.finalize_common import _logger
from novel_forge.pipeline.long.stages.finalize_report import (
    _adjudicate_state_before_archive,
    _clean_and_validate_chapter_text,
    _run_contract_execution_audit_without_state_adjudication,
    evaluate_chapter_text,
    extract_and_validate,
)
from novel_forge.pipeline.long.stages.report_freshness import (
    quality_report_evidence_binding,
)


def _story_kernel_db_path(settings: Any, layout: Any) -> str:
    return story_kernel_db_path(settings, layout)


_ARCHIVE_AUTHORITATIVE_REVIEW_MODES = frozenset({"", "full", "full_review", "full_check"})


@dataclass(frozen=True)
class TextHashState:
    """Text hash snapshot for archive preflight mutation events."""

    text_hash: str
    reason: str


@dataclass
class ReportRefreshDecision:
    """Centralized archive refresh decision after pre-archive text changes."""

    reasons: list[str]
    refresh_quality: bool = False
    refresh_outcome: bool = False
    clear_eval: bool = False
    semantic_text_changed: bool = False

    @classmethod
    def empty(cls) -> "ReportRefreshDecision":
        return cls(reasons=[])

    def add_reason(self, reason: str) -> str:
        clean_reason = str(reason or "").strip() or "semantic_text_changed_before_archive"
        if clean_reason not in self.reasons:
            self.reasons.append(clean_reason)
        return clean_reason

    def mark_existing_stale(
        self,
        reason: str,
        *,
        refresh_outcome: bool = True,
        refresh_quality: bool = True,
        clear_eval: bool = True,
        semantic_text_changed: bool = True,
    ) -> None:
        self.add_reason(reason)
        self.refresh_outcome = self.refresh_outcome or refresh_outcome
        self.refresh_quality = self.refresh_quality or refresh_quality
        self.clear_eval = self.clear_eval or clear_eval
        self.semantic_text_changed = self.semantic_text_changed or semantic_text_changed

    def mark_text_change(
        self,
        *,
        runner: Any,
        chapter_number: int,
        reason: str,
        before_text: str,
        after_text: str,
        refresh_outcome: bool = True,
        refresh_quality: bool = True,
        clear_eval: bool = True,
        semantic_text_changed: bool = True,
    ) -> None:
        clean_reason = self.add_reason(reason)
        self.refresh_outcome = self.refresh_outcome or refresh_outcome
        self.refresh_quality = self.refresh_quality or refresh_quality
        self.clear_eval = self.clear_eval or clear_eval
        self.semantic_text_changed = self.semantic_text_changed or semantic_text_changed
        before = TextHashState(
            text_hash=source_text_hash(before_text),
            reason=clean_reason,
        )
        after = TextHashState(
            text_hash=source_text_hash(after_text),
            reason=clean_reason,
        )
        runner._on_step(
            "text_changed_before_archive",
            {
                "chapter": chapter_number,
                "reason": clean_reason,
                "before_hash": before.text_hash,
                "after_hash": after.text_hash,
                "refresh_quality": refresh_quality,
                "refresh_eval": clear_eval,
                "refresh_outcome": refresh_outcome,
            },
        )

    def require_quality_refresh(self, reason: str) -> None:
        self.add_reason(reason)
        self.refresh_quality = True

    def refresh_reason(self, fallback: str = "semantic_text_changed_before_archive") -> str:
        return "_and_".join(reason for reason in self.reasons if str(reason).strip()) or fallback


@dataclass
class ArchivePreflightResult:
    """Artifacts produced by archive preflight repairs before hard gating."""

    current_text: str
    outcome: Any
    eval_report: EvalReport | None
    chapter_repair_report: Any
    state_adjudication_report: Any | None
    knowledge_boundary_findings: list[Any]
    terminal_humanize_metadata: dict[str, Any]
    refresh_decision: ReportRefreshDecision


_KB_REUSE_ALLOWED_SKIP_REASONS = frozenset({"no_hidden_candidates", "no_prescreen_hits"})


def _kb_reuse_int(value: Any) -> int | None:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return None


def _knowledge_boundary_report_path(bundle: Any, chapter_number: int) -> Any | None:
    layout = getattr(bundle, "layout", None)
    report_path_fn = getattr(layout, "knowledge_boundary_report_path", None)
    if callable(report_path_fn):
        return report_path_fn(chapter_number)
    reports_dir = getattr(layout, "reports_dir", None)
    if reports_dir is not None:
        return reports_dir / f"chapter_{chapter_number:03d}_knowledge_boundary_verification.json"
    return None


def _load_knowledge_boundary_report(
    *,
    storage: Any,
    bundle: Any,
    chapter_number: int,
) -> dict[str, Any] | None:
    path = _knowledge_boundary_report_path(bundle, chapter_number)
    if path is None:
        return None
    try:
        exists = getattr(storage, "exists", None)
        if callable(exists):
            if not bool(exists(path)):
                return None
        else:
            path_exists = getattr(path, "exists", None)
            if callable(path_exists) and not bool(path_exists()):
                return None
        payload = storage.load_json(path)
        return payload if isinstance(payload, dict) else None
    except Exception as exc:
        _logger.debug(
            "knowledge_boundary_reuse_report_load_failed | chapter=%d | error=%s",
            chapter_number,
            exc,
        )
        return None


def _knowledge_boundary_report_is_reusable(
    payload: dict[str, Any] | None,
    *,
    current_text: str,
    decision: ReportRefreshDecision,
) -> bool:
    if not payload or decision.semantic_text_changed:
        return False
    expected_hash = source_text_hash(current_text)
    stored_hash = str(payload.get("source_text_hash", "") or "").strip()
    if not stored_hash or stored_hash != expected_hash:
        return False
    findings = list(payload.get("findings") or [])
    issues = list(payload.get("issues") or [])
    blockers = list(payload.get("blockers") or [])
    repair_tickets = list(payload.get("repair_tickets") or [])
    if findings or issues or blockers or repair_tickets:
        return False
    prescreen_hit_count = _kb_reuse_int(payload.get("prescreen_hit_count", 0))
    if prescreen_hit_count is None:
        return False
    skip_reason = str(payload.get("audit_skipped_reason", "") or "").strip()
    return prescreen_hit_count == 0 or skip_reason in _KB_REUSE_ALLOWED_SKIP_REASONS


def _save_reused_knowledge_boundary_report(
    *,
    storage: Any,
    bundle: Any,
    chapter_number: int,
    current_text: str,
    reused_payload: dict[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chapter": chapter_number,
        "stage": "archive_pre_persist",
        "source_text_hash": source_text_hash(current_text),
        "hidden_candidate_count": _kb_reuse_int(reused_payload.get("hidden_candidate_count", 0))
        or 0,
        "prescreen_hit_count": _kb_reuse_int(reused_payload.get("prescreen_hit_count", 0)) or 0,
        "prescreen_hits": list(reused_payload.get("prescreen_hits") or []),
        "verdict": "pass",
        "fallback_used": bool(reused_payload.get("fallback_used", False)),
        "issues": [],
        "findings": [],
        "repair_tickets": [],
        "audit_skipped_reason": "reused_fresh_review_finalize_audit",
        "reused_from_stage": str(reused_payload.get("stage", "") or ""),
    }
    path = _knowledge_boundary_report_path(bundle, chapter_number)
    if path is not None:
        try:
            storage.save_json(path, payload)
        except Exception as exc:
            _logger.warning(
                "knowledge_boundary_reuse_report_save_failed | chapter=%d | error=%s",
                chapter_number,
                exc,
            )
    return payload


def _archive_report_refresh_reasons(
    *,
    current_text: str,
    alignment_report: Any | None,
    continuity_report: Any | None,
    causal_report: Any | None,
) -> list[str]:
    """Return reasons why archive hard reports must be refreshed before gating."""

    current_hash = source_text_hash(current_text) if current_text else ""
    reasons: list[str] = []

    def _check_report(label: str, report: Any | None) -> None:
        if report is None:
            return
        review_mode = str(getattr(report, "review_mode", "") or "").strip().lower()
        if review_mode not in _ARCHIVE_AUTHORITATIVE_REVIEW_MODES:
            reasons.append(f"{label}_review_mode_{review_mode}_before_archive")
        stored_hash = str(getattr(report, "source_text_hash", "") or "").strip()
        if current_hash and stored_hash and stored_hash != current_hash:
            reasons.append(f"{label}_source_hash_mismatch_before_archive")

    _check_report("alignment", alignment_report)
    _check_report("continuity", continuity_report)
    _check_report("causal", causal_report)
    return reasons


def _report_payload_for_archive(
    storage: Any,
    path: Any,
) -> dict[str, Any] | None:
    try:
        if path is None or not path.exists():
            return None
        payload = storage.load_json(path)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _assert_authoritative_archive_reports_current(
    *,
    runner: Any,
    bundle: Any,
    chapter_number: int,
    current_text: str,
    packet: Any,
    bridge: Any,
    plan: Any,
    causal_report: Any | None,
    require_reading_power: bool,
) -> None:
    """Fail closed unless every enabled hard report targets the final prose."""

    expected_hash = source_text_hash(current_text)
    evidence_binding = quality_report_evidence_binding(
        packet=packet,
        bridge=bridge,
        plan=plan,
        bundle=bundle,
    )
    expected_context_hash = str(evidence_binding["context_hash"])
    layout = bundle.layout
    reports: list[tuple[str, Any]] = [
        ("alignment", layout.alignment_report_path(chapter_number)),
        ("continuity", layout.continuity_report_path(chapter_number)),
    ]
    causal_path = layout.chapter_causal_report_path(chapter_number)
    if causal_report is not None or causal_path.exists():
        reports.append(("causal", causal_path))
    if require_reading_power:
        reports.append(("reading_power", layout.reading_power_report_path(chapter_number)))

    invalid: list[str] = []
    diagnostics: dict[str, Any] = {}
    for dimension, path in reports:
        payload = _report_payload_for_archive(runner._storage, path)
        stored_hash = str((payload or {}).get("source_text_hash", "") or "").strip()
        freshness = (payload or {}).get("report_freshness") or {}
        stored_context_hash = str(
            (payload or {}).get("report_context_hash")
            or (freshness.get("context_hash") if isinstance(freshness, dict) else "")
            or ""
        ).strip()
        freshness_schema = int(
            (freshness.get("schema_version", 0) if isinstance(freshness, dict) else 0) or 0
        )
        stale = bool((payload or {}).get("stale_after_text_change"))
        if (
            payload is None
            or not stored_hash
            or stored_hash != expected_hash
            or stored_context_hash != expected_context_hash
            or freshness_schema < 2
            or stale
        ):
            invalid.append(dimension)
            diagnostics[dimension] = {
                "path": str(path),
                "stored_hash": stored_hash,
                "stored_context_hash": stored_context_hash,
                "expected_context_hash": expected_context_hash,
                "freshness_schema": freshness_schema,
                "missing": payload is None,
                "marked_stale": stale,
            }

    if not invalid:
        return
    runner._on_step(
        "archive_quality_report_freshness_block",
        {
            "chapter": chapter_number,
            "expected_text_hash": expected_hash,
            "expected_context_hash": expected_context_hash,
            "dimensions": invalid,
            "reports": diagnostics,
            "action": "block_archive",
        },
    )
    raise FinalReportFreshnessError(
        chapter_number=chapter_number,
        expected_text_hash=expected_hash,
        dimensions=invalid,
        detail=f"缺失或过期维度：{', '.join(invalid)}",
    )


def _preserve_final_review_draft(
    *,
    runner: Any,
    bundle: Any,
    chapter_number: int,
    current_text: str,
) -> str:
    """Persist the latest prose before a retryable final-review boundary."""

    path_factory = getattr(bundle.layout, "chapter_review_draft_path", None)
    if not callable(path_factory):
        return ""
    path = path_factory(chapter_number)
    runner._storage.save_text(path, current_text)
    return str(path)


async def _write_chapter_outcome_to_story_kernel(
    runner: Any,
    bundle: Any,
    outcome: Any,
    report: Any,
) -> None:
    raw_project_id = getattr(bundle, "project_id", "")
    project_id = raw_project_id.strip() if isinstance(raw_project_id, str) else ""
    if not project_id:
        _logger.warning(
            "story_kernel_skip: LongProjectBundle.project_id is empty; "
            "skipping StoryKernel write for this chapter."
        )
        return

    store = StoryKernelStore(
        _story_kernel_db_path(runner._settings, bundle.layout),
        wal_mode=bool(getattr(runner._settings, "story_kernel_wal_mode", True)),
    )
    try:
        await store.init_db()
        writer = StoryKernelStateWriter(store)
        kernel = await store.load_kernel(project_id)
        chapter = getattr(outcome, "source_chapter", getattr(report, "chapter_number", 0))
        updated = await writer.merge_chapter_outcome(
            project_id=project_id,
            outcome=outcome,
            report=report,
            step_names=("extract", "state_adjudication") if report is not None else "extract",
        )
        if chapter:
            await writer.save_chapter_snapshot(int(chapter))
            # Auto-prune snapshots after each chapter finalize
            _keep_recent = int(getattr(runner._settings, "long_snapshot_keep_recent", 5))
            _max_disk_mb = int(getattr(runner._settings, "long_snapshot_max_disk_mb", 500))
            _protected: list[int] = []
            # Protect volume-end chapters (best-effort from kernel chapter_summaries keys)
            try:
                _protected.append(
                    max(updated.chapter_summaries.keys()) if updated.chapter_summaries else 0
                )
            except (ValueError, TypeError):
                pass
            pruned = store.prune_snapshots(
                keep_recent=_keep_recent,
                keep_chapters=_protected or None,
                max_disk_mb=_max_disk_mb,
            )
            if pruned:
                runner._on_step(
                    "snapshots_pruned",
                    {"deleted_chapters": pruned, "count": len(pruned)},
                )
        if updated.model_dump(mode="json") != kernel.model_dump(mode="json"):
            _storage = getattr(runner, "_storage", None)
            if _storage is not None:
                update_canon_watermark(
                    _storage,
                    bundle.layout,
                    updated.current_chapter,
                )
            invalidate_story_kernel_composer_cache(
                db_path=_story_kernel_db_path(runner._settings, bundle.layout),
                project_id=project_id,
            )
            runner._on_step(
                "story_kernel_updated",
                {
                    "chapter": chapter,
                    "timeline_entries": len(updated.timeline),
                    "knowledge_entries": len(updated.knowledge_ledger),
                    "relationship_entries": len(updated.relationships),
                    "promise_entries": len(updated.promise_ledger),
                    "structured_warnings": len(updated.structured_warnings),
                },
            )
        # Write pending book audit marker if interval condition is met
        _audit_interval = int(getattr(runner._settings, "long_auto_book_audit_interval", 0))
        if _audit_interval > 0 and chapter and chapter % _audit_interval == 0:
            _storage = getattr(runner, "_storage", None)
            if _storage is not None:
                from datetime import datetime, timezone

                marker = {
                    "trigger_chapter": int(chapter),
                    "trigger_reason": "interval",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                try:
                    _storage.save_json(
                        bundle.layout.states_dir / "pending_book_audit.json",
                        marker,
                    )
                    runner._on_step("book_audit_marker_written", marker)
                except Exception as exc:
                    _logger.warning(
                        "book_audit_marker_write_failed | chapter=%d | error=%s", chapter, exc
                    )
    finally:
        await store.close()


async def _extract_and_apply_knowledge_deltas(
    runner: Any,
    bundle: Any,
    *,
    chapter_number: int,
    current_text: str,
    pov_character: str,
    trace: Any,
) -> int:
    """Extract knowledge deltas from chapter text and apply to StoryKernel.

    Returns the number of new knowledge entries added.
    """
    from novel_forge.story_kernel.schemas import KnowledgeLedger

    raw_project_id = getattr(bundle, "project_id", "")
    project_id = raw_project_id.strip() if isinstance(raw_project_id, str) else ""
    if not project_id:
        return 0

    store = StoryKernelStore(
        _story_kernel_db_path(runner._settings, bundle.layout),
        wal_mode=bool(getattr(runner._settings, "story_kernel_wal_mode", True)),
    )
    try:
        await store.init_db()
        kernel = await store.load_kernel(project_id)

        existing_summaries = [
            f"{entry.entity_id}: {entry.fact}" for entry in kernel.knowledge_ledger
        ][-20:]

        step = ExtractKnowledgeDeltasStep(
            runner._router,
            runner._builder,
            settings=runner._settings,
            trace=trace,
        )
        output = await step.run(
            KnowledgeDeltaInput(
                chapter_number=chapter_number,
                chapter_text=current_text,
                pov_character=pov_character,
                existing_knowledge_summaries=existing_summaries,
            )
        )

        if not output.knowledge_deltas:
            runner._on_step(
                "extract_knowledge_deltas",
                {"chapter": chapter_number, "count": 0, "status": "no_deltas"},
            )
            return 0

        existing_facts = {(entry.entity_id, entry.fact) for entry in kernel.knowledge_ledger}

        new_entries: list[KnowledgeLedger] = []
        for delta in output.knowledge_deltas:
            entity_id = delta["entity_id"]
            fact = delta["fact"]
            if (entity_id, fact) in existing_facts:
                continue
            existing_facts.add((entity_id, fact))

            entry_id = f"ch{chapter_number}_{entity_id}_{hash(fact) & 0xFFFFFFFF:08x}"
            new_entry = KnowledgeLedger(
                entry_id=entry_id,
                entity_id=entity_id,
                fact=fact,
                knowledge_type=delta.get("knowledge_type", "known"),
                source_chapter=delta.get("source_chapter", chapter_number),
                visibility=delta.get("visibility", "private"),
                confidence=0.8,
            )
            new_entries.append(new_entry)

        if not new_entries:
            runner._on_step(
                "extract_knowledge_deltas",
                {"chapter": chapter_number, "count": 0, "status": "all_duplicates"},
            )
            return 0

        updated_ledger = list(kernel.knowledge_ledger) + new_entries
        updated_kernel = kernel.model_copy(update={"knowledge_ledger": updated_ledger})

        revealed_count = 0
        final_ledger = list(updated_kernel.knowledge_ledger)
        for idx, entry in enumerate(final_ledger):
            if (
                entry.visibility in ("private", "secret")
                and entry.revealed_in_chapter == 0
                and entry.fact
                and entry.fact in current_text
            ):
                final_ledger[idx] = entry.model_copy(update={"revealed_in_chapter": chapter_number})
                revealed_count += 1

        if revealed_count > 0:
            updated_kernel = updated_kernel.model_copy(update={"knowledge_ledger": final_ledger})

        await store.save_kernel(updated_kernel)
        invalidate_story_kernel_composer_cache(
            db_path=_story_kernel_db_path(runner._settings, bundle.layout),
            project_id=project_id,
        )

        runner._on_step(
            "extract_knowledge_deltas",
            {
                "chapter": chapter_number,
                "new_entries": len(new_entries),
                "revealed_secrets": revealed_count,
                "total_ledger_size": len(updated_kernel.knowledge_ledger),
            },
        )
        return len(new_entries)

    finally:
        await store.close()


async def run_archive_preflight_repairs(
    *,
    runner: Any,
    bundle: Any,
    packet: Any,
    bridge: Any,
    plan: Any,
    outcome: Any,
    current_text: str,
    chapter_number: int,
    trace: Any,
    continuity_report: Any,
    eval_report: EvalReport | None,
    chapter_repair_report: Any,
    memory_hints: dict[str, Any] | None,
    allow_word_count_archive_bypass: bool,
    allow_semantic_pre_archive_repairs: bool | None = None,
    allow_prompt_leak_patch_repair: bool | None = None,
    allow_narrative_state_repair: bool | None = None,
    allow_knowledge_boundary_repair: bool | None = None,
    terminal_humanize: Callable[[str], Awaitable[str]] | None = None,
    decision: ReportRefreshDecision | None = None,
) -> ArchivePreflightResult:
    """Run named pre-archive repairs and record refresh decisions centrally."""
    if decision is None:
        decision = ReportRefreshDecision.empty()
    legacy_repair_default = (
        True if allow_semantic_pre_archive_repairs is None else allow_semantic_pre_archive_repairs
    )
    if allow_prompt_leak_patch_repair is None:
        allow_prompt_leak_patch_repair = legacy_repair_default
    if allow_narrative_state_repair is None:
        allow_narrative_state_repair = legacy_repair_default
    if allow_knowledge_boundary_repair is None:
        allow_knowledge_boundary_repair = legacy_repair_default

    # ── Preferred prompt-leak repair path: localized LLM patch before guards ──
    # _clean_and_validate_chapter_text still has a deterministic last-resort
    # fallback, but known leaks should first go through PATCH_CHAPTER so the
    # model can preserve in-world meaning instead of mechanically deleting text.
    if allow_prompt_leak_patch_repair:
        prompt_leak_repair = await repair_confirmed_prompt_leaks_with_patch(
            router=runner._router,
            builder=runner._builder,
            settings=runner._settings,
            trace=trace,
            chapter_number=chapter_number,
            current_text=current_text,
            chapter_repair_report=chapter_repair_report,
            style_profile=getattr(bundle, "style_profile", None),
            on_step=runner._on_step,
            allow_deterministic_fallback=True,
        )
        if prompt_leak_repair.applied or prompt_leak_repair.report_updated:
            if prompt_leak_repair.applied and prompt_leak_repair.text != current_text:
                decision.mark_text_change(
                    runner=runner,
                    chapter_number=chapter_number,
                    reason="prompt_leak_llm_patch",
                    before_text=current_text,
                    after_text=prompt_leak_repair.text,
                    refresh_quality=not prompt_leak_repair.used_deterministic_fallback,
                )
                if decision.clear_eval:
                    eval_report = None
            current_text = prompt_leak_repair.text
            chapter_repair_report = prompt_leak_repair.chapter_repair_report

    # ── Text guards FIRST: validate & clean prose BEFORE touching canon ───
    # This ensures that if any guard rejects (prompt leaks, too-short text),
    # canon state is never updated — preventing text/canon desync.
    _pre_clean_text = current_text
    current_text = _clean_and_validate_chapter_text(
        runner,
        bundle,
        chapter_number,
        current_text,
        chapter_repair_report=chapter_repair_report,
        allow_word_count_archive_bypass=allow_word_count_archive_bypass,
    )
    if current_text != _pre_clean_text:
        decision.mark_text_change(
            runner=runner,
            chapter_number=chapter_number,
            reason="deterministic_text_cleanup_before_archive",
            before_text=_pre_clean_text,
            after_text=current_text,
            refresh_quality=False,
            semantic_text_changed=False,
        )
        if decision.clear_eval:
            eval_report = None

    _pre_time_marker_text = current_text
    current_text, time_marker_replacements = normalize_invalid_time_markers(current_text)
    if time_marker_replacements and current_text != _pre_time_marker_text:
        decision.mark_text_change(
            runner=runner,
            chapter_number=chapter_number,
            reason="time_marker_normalized_before_archive",
            before_text=_pre_time_marker_text,
            after_text=current_text,
        )
        if decision.clear_eval:
            eval_report = None
        runner._on_step(
            "time_marker_normalized",
            {
                "chapter": chapter_number,
                "stage": "archive_pre_persist",
                "count": len(time_marker_replacements),
                "replacements": time_marker_replacements[:8],
            },
        )

    state_adjudication_report = None
    if getattr(runner._settings, "narrative_state_enabled", True):
        try:
            _pre_state_text = current_text
            (
                current_text,
                outcome,
                eval_report,
                state_adjudication_report,
            ) = await _adjudicate_state_before_archive(
                runner,
                bundle,
                packet,
                bridge,
                plan,
                outcome,
                current_text,
                chapter_number,
                trace,
                continuity_report,
                eval_report,
                memory_hints,
                allow_repair=allow_narrative_state_repair,
            )
            if current_text != _pre_state_text:
                decision.mark_text_change(
                    runner=runner,
                    chapter_number=chapter_number,
                    reason="narrative_state_adjudication_repair",
                    before_text=_pre_state_text,
                    after_text=current_text,
                    refresh_outcome=False,
                )
                if decision.clear_eval:
                    eval_report = None
        except Exception as exc:
            if getattr(runner._settings, "narrative_state_required", True):
                raise
            _logger.warning(
                "state_adjudication_failed_non_blocking | chapter=%d | error=%s",
                chapter_number,
                exc,
                exc_info=True,
            )
            runner._on_step(
                "state_adjudication_skipped",
                {
                    "chapter": chapter_number,
                    "reason": "adjudication_failed",
                    "error": str(exc)[:500],
                },
            )
    else:
        runner._on_step("state_adjudication_skipped", {"chapter": chapter_number})

    if state_adjudication_report is None:
        await _run_contract_execution_audit_without_state_adjudication(
            runner,
            bundle,
            packet,
            plan,
            chapter_number=chapter_number,
            current_text=current_text,
            trace=trace,
        )

    reusable_kb_report = (
        _load_knowledge_boundary_report(
            storage=runner._storage,
            bundle=bundle,
            chapter_number=chapter_number,
        )
        if bool(getattr(runner._settings, "long_kb_audit_reuse_enabled", True))
        else None
    )
    if _knowledge_boundary_report_is_reusable(
        reusable_kb_report,
        current_text=current_text,
        decision=decision,
    ):
        skipped_payload = _save_reused_knowledge_boundary_report(
            storage=runner._storage,
            bundle=bundle,
            chapter_number=chapter_number,
            current_text=current_text,
            reused_payload=reusable_kb_report or {},
        )
        runner._on_step(
            "knowledge_boundary_verification_skipped",
            {
                "chapter": chapter_number,
                "reason": "fresh_report_reused_no_semantic_text_change",
                "source_text_hash": skipped_payload["source_text_hash"],
                "reused_from_stage": skipped_payload.get("reused_from_stage", ""),
            },
        )
        knowledge_boundary_findings = []
    else:
        knowledge_boundary_findings = await run_knowledge_boundary_audit(
            runner=runner,
            storage=runner._storage,
            bundle=bundle,
            packet=packet,
            current_text=current_text,
            chapter_number=chapter_number,
            stage="archive_pre_persist",
            block_high_confidence=True,
        )
    knowledge_boundary_blockers = [
        f"[{finding.severity}] {finding.issue_type}: {finding.summary}"
        for finding in knowledge_boundary_findings
        if finding.blocks_finalize
    ]
    if knowledge_boundary_blockers and allow_knowledge_boundary_repair:
        _pre_kb_text = current_text
        kb_repair_result = await run_knowledge_boundary_repair_loop(
            runner=runner,
            storage=runner._storage,
            bundle=bundle,
            packet=packet,
            current_text=current_text,
            chapter_number=chapter_number,
            findings=knowledge_boundary_findings,
            trace=trace,
            max_rounds=1,
        )
        current_text = kb_repair_result.current_text
        if current_text != _pre_kb_text:
            decision.mark_text_change(
                runner=runner,
                chapter_number=chapter_number,
                reason="knowledge_boundary_repair_before_archive",
                before_text=_pre_kb_text,
                after_text=current_text,
            )
            if decision.clear_eval:
                eval_report = None
        knowledge_boundary_findings = list(kb_repair_result.findings_after)
        remaining_blockers = [
            f"[{f.severity}] {f.issue_type}: {f.summary}"
            for f in knowledge_boundary_findings
            if f.blocks_finalize
        ]
        runner._on_step(
            "knowledge_boundary_repair_attempted",
            {
                "chapter": chapter_number,
                "repair_exhausted": kb_repair_result.repair_exhausted,
                "rounds_used": kb_repair_result.rounds_used,
                "blockers_before": len(knowledge_boundary_blockers),
                "blockers_after": len(remaining_blockers),
            },
        )
        if remaining_blockers:
            critical_high_blockers = [
                f
                for f in knowledge_boundary_findings
                if f.blocks_finalize
                and severity_at_least(f.severity, "high")
                and f.confidence >= 0.9
            ]
            if critical_high_blockers:
                runner._on_step(
                    "knowledge_boundary_blocked",
                    {
                        "chapter": chapter_number,
                        "blockers": remaining_blockers,
                    },
                )
                raise ConsistencyViolationError(remaining_blockers)
            runner._on_step(
                "knowledge_boundary_downgraded_to_warning",
                {
                    "chapter": chapter_number,
                    "remaining_blockers": remaining_blockers,
                    "reason": "severity_below_critical_threshold",
                },
            )
    elif knowledge_boundary_blockers:
        critical_high_blockers = [
            finding
            for finding in knowledge_boundary_findings
            if finding.blocks_finalize
            and severity_at_least(finding.severity, "high")
            and finding.confidence >= 0.9
        ]
        if critical_high_blockers:
            runner._on_step(
                "knowledge_boundary_blocked",
                {
                    "chapter": chapter_number,
                    "blockers": knowledge_boundary_blockers,
                    "reason": "pre_humanize_semantic_repair_disabled",
                },
            )
            raise ConsistencyViolationError(knowledge_boundary_blockers)
        runner._on_step(
            "knowledge_boundary_downgraded_to_warning",
            {
                "chapter": chapter_number,
                "remaining_blockers": knowledge_boundary_blockers,
                "reason": "pre_humanize_semantic_repairs_disabled",
            },
        )

    # Humanize is the terminal creative mutation.  State/contract/knowledge
    # repairs above must settle first; everything after this point is read-only
    # verification and report refresh.  This ordering is intentionally strict:
    # a later semantic repair must leave this attempt and enter the outer
    # finalize retry state machine, which will run Humanize again.
    terminal_humanize_metadata: dict[str, Any] = {
        "applied": False,
        "reextract_after_humanize": False,
        "reevaluate_after_humanize": False,
        "post_humanize_verification": "not_required_no_text_change",
    }
    if terminal_humanize is not None:
        _pre_terminal_humanize_text = current_text
        humanized_text = await terminal_humanize(current_text)
        if humanized_text != _pre_terminal_humanize_text:
            # Run the existing deterministic guard on a disposable candidate.
            # If it would change the Humanize output, reject this archive
            # attempt instead of silently mutating prose after Humanize.
            checked_text = _clean_and_validate_chapter_text(
                runner,
                bundle,
                chapter_number,
                humanized_text,
                chapter_repair_report=chapter_repair_report,
                allow_word_count_archive_bypass=allow_word_count_archive_bypass,
            )
            normalized_text, terminal_time_marker_replacements = normalize_invalid_time_markers(
                checked_text
            )
            if normalized_text != humanized_text:
                runner._on_step(
                    "terminal_humanize_read_only_validation_blocked",
                    {
                        "chapter": chapter_number,
                        "source_text_hash": source_text_hash(humanized_text),
                        "candidate_text_hash": source_text_hash(normalized_text),
                        "time_marker_replacements": len(terminal_time_marker_replacements),
                        "action": "reject_without_mutating_final_text",
                    },
                )
                raise ConsistencyViolationError(
                    [
                        "Humanize 后文本仍需确定性清洗或时间标记修复；"
                        "为保证 Humanize 是最后一次正文加工，本次归档已拒绝，且未采用清洗后的文本。"
                    ],
                    violation_kind="terminal_humanize_postcondition",
                    failed_stage="terminal_humanize_read_only_validation",
                    replan_target=RecoveryTarget.MANUAL,
                )

            current_text = humanized_text
            decision.mark_text_change(
                runner=runner,
                chapter_number=chapter_number,
                reason="terminal_humanize_before_archive_gate",
                before_text=_pre_terminal_humanize_text,
                after_text=current_text,
            )
            if decision.clear_eval:
                eval_report = None
            terminal_humanize_metadata.update(
                {
                    "applied": True,
                    "before_text_hash": source_text_hash(_pre_terminal_humanize_text),
                    "after_text_hash": source_text_hash(current_text),
                    "deterministic_cleanup_changed": False,
                    "time_marker_replacements": 0,
                    "reextract_after_humanize": True,
                    "reevaluate_after_humanize": True,
                    "post_humanize_verification": "pending",
                }
            )
            runner._on_step(
                "terminal_humanize_applied",
                {
                    "chapter": chapter_number,
                    "text_changed": True,
                    "deterministic_cleanup_changed": False,
                    "time_marker_replacements": 0,
                },
            )

            # Re-run semantic checks against the accepted Humanize hash with
            # repairs disabled.  These calls may update reports, but cannot
            # alter current_text.  Blocking findings abort persistence.
            post_verification_status = "actual"
            if getattr(runner._settings, "narrative_state_enabled", True):
                try:
                    verified_text = current_text
                    (
                        verified_text,
                        outcome,
                        eval_report,
                        state_adjudication_report,
                    ) = await _adjudicate_state_before_archive(  # type: ignore[name-defined]
                        runner,
                        bundle,
                        packet,
                        bridge,
                        plan,
                        outcome,
                        current_text,
                        chapter_number,
                        trace,
                        continuity_report,
                        eval_report,
                        memory_hints,
                        allow_repair=False,
                    )
                    if verified_text != current_text:
                        raise RuntimeError(
                            "Post-Humanize narrative-state verification unexpectedly mutated text"
                        )
                except Exception as exc:
                    if getattr(runner._settings, "narrative_state_required", True):
                        raise
                    post_verification_status = "degraded"
                    state_adjudication_report = None
                    _logger.warning(
                        "post_humanize_state_verification_degraded | chapter=%d | error=%s",
                        chapter_number,
                        exc,
                        exc_info=True,
                    )
                    runner._on_step(
                        "post_humanize_state_verification_degraded",
                        {
                            "chapter": chapter_number,
                            "source_text_hash": source_text_hash(current_text),
                            "error": str(exc)[:500],
                            "status": "degraded",
                            "text_mutated": False,
                        },
                    )
            else:
                await _run_contract_execution_audit_without_state_adjudication(  # type: ignore[name-defined]
                    runner,
                    bundle,
                    packet,
                    plan,
                    chapter_number=chapter_number,
                    current_text=current_text,
                    trace=trace,
                )

            knowledge_boundary_findings = await run_knowledge_boundary_audit(
                runner=runner,
                storage=runner._storage,
                bundle=bundle,
                packet=packet,
                current_text=current_text,
                chapter_number=chapter_number,
                stage="archive_post_humanize_verify",
                block_high_confidence=True,
            )
            post_humanize_blockers = [
                f"[{finding.severity}] {finding.issue_type}: {finding.summary}"
                for finding in knowledge_boundary_findings
                if finding.blocks_finalize
                and severity_at_least(finding.severity, "high")
                and finding.confidence >= 0.9
            ]
            if post_humanize_blockers:
                runner._on_step(
                    "post_humanize_semantic_verification_blocked",
                    {
                        "chapter": chapter_number,
                        "source_text_hash": source_text_hash(current_text),
                        "blockers": post_humanize_blockers,
                        "action": "reject_without_post_humanize_repair",
                    },
                )
                raise ConsistencyViolationError(
                    post_humanize_blockers,
                    violation_kind="post_humanize_semantic_verification",
                    failed_stage="post_humanize_semantic_verification",
                    replan_target=RecoveryTarget.MANUAL,
                )
            terminal_humanize_metadata["post_humanize_verification"] = post_verification_status
            runner._on_step(
                "post_humanize_semantic_verification",
                {
                    "chapter": chapter_number,
                    "source_text_hash": source_text_hash(current_text),
                    "status": post_verification_status,
                    "knowledge_boundary_findings": len(knowledge_boundary_findings),
                    "text_mutated": False,
                },
            )

    return ArchivePreflightResult(
        current_text=current_text,
        outcome=outcome,
        eval_report=eval_report,
        chapter_repair_report=chapter_repair_report,
        state_adjudication_report=state_adjudication_report,
        knowledge_boundary_findings=list(knowledge_boundary_findings),
        terminal_humanize_metadata=terminal_humanize_metadata,
        refresh_decision=decision,
    )


async def persist_results(
    runner: Any,
    bundle: Any,
    packet: Any,
    bridge: Any,
    plan: Any,
    outcome: Any,
    current_text: str,
    performed_edits: int,
    alignment_report: Any,
    chapter_repair_report: Any,
    continuity_report: Any,
    causal_report: CausalValidationReport | None,
    repair_plan: Any,
    trace: Any,
    chapter_number: int,
    *,
    eval_report: EvalReport | None = None,
    emit_evaluate_step: bool = True,
    memory_hints: dict[str, Any] | None = None,
    window_manager: Any | None = None,
    allow_word_count_archive_bypass: bool = False,
    force_mark_quality_reports_stale: bool = False,
    quality_reports_stale_reason: str = "",
    allow_carry_forward_archive_bypass: bool = False,
    allow_semantic_pre_archive_repairs: bool | None = None,
    allow_prompt_leak_patch_repair: bool | None = None,
    allow_narrative_state_repair: bool | None = None,
    allow_knowledge_boundary_repair: bool | None = None,
    terminal_humanize: Callable[[str], Awaitable[str]] | None = None,
    precomputed_preflight: ArchivePreflightResult | None = None,
) -> PersistedChapterArtifacts:
    """Persist all chapter results, update canon, and run evaluation.

    Returns:
        Final persisted artifacts, including refreshed text and reports.
    """
    refresh_decision = (
        precomputed_preflight.refresh_decision
        if precomputed_preflight is not None
        else ReportRefreshDecision.empty()
    )
    if force_mark_quality_reports_stale:
        refresh_decision.mark_existing_stale(
            str(quality_reports_stale_reason or "").strip()
            or "semantic_text_changed_before_archive",
        )
        eval_report = None
    refreshed_reading_power_report: Any | None = None
    refreshed_guard_compliance_report: dict[str, Any] | None = None
    refreshed_review_findings: list[Any] = []
    refreshed_repair_tickets: list[Any] = []

    preflight = precomputed_preflight
    if preflight is None:
        preflight = await run_archive_preflight_repairs(
            runner=runner,
            bundle=bundle,
            packet=packet,
            bridge=bridge,
            plan=plan,
            outcome=outcome,
            current_text=current_text,
            chapter_number=chapter_number,
            trace=trace,
            continuity_report=continuity_report,
            eval_report=eval_report,
            chapter_repair_report=chapter_repair_report,
            memory_hints=memory_hints,
            allow_word_count_archive_bypass=allow_word_count_archive_bypass,
            allow_semantic_pre_archive_repairs=allow_semantic_pre_archive_repairs,
            allow_prompt_leak_patch_repair=allow_prompt_leak_patch_repair,
            allow_narrative_state_repair=allow_narrative_state_repair,
            allow_knowledge_boundary_repair=allow_knowledge_boundary_repair,
            terminal_humanize=terminal_humanize,
            decision=refresh_decision,
        )
    elif source_text_hash(current_text) != source_text_hash(preflight.current_text):
        raise ValueError("precomputed archive preflight does not match current chapter text")
    current_text = preflight.current_text
    outcome = preflight.outcome
    eval_report = preflight.eval_report
    if refresh_decision.clear_eval:
        eval_report = None
    chapter_repair_report = preflight.chapter_repair_report
    state_adjudication_report = preflight.state_adjudication_report
    terminal_humanize_metadata = preflight.terminal_humanize_metadata

    # The final text may have changed during preflight / terminal humanize.
    # Recheck the bounded rule card before any canon or chapter archive write.
    source_slice = getattr(bundle, "chapter_source_slice", None)
    if source_slice is not None:
        from novel_forge.pipeline.long.services.context.source_artifacts import (
            project_stage_source_cards,
        )
        from novel_forge.pipeline.long.services.constraints.world_rule_governance import (
            check_world_rule_compliance,
            world_rule_report_to_findings,
        )

        world_rule_card = project_stage_source_cards(source_slice, stage="review").get(
            "world_rule_card"
        )
        if world_rule_card:
            world_rule_report = await check_world_rule_compliance(
                runner=runner,
                chapter_text=current_text,
                chapter_number=chapter_number,
                world_rule_card=world_rule_card,
                applications=list(getattr(plan, "world_rule_applications", []) or []),
            )
            world_rule_path = (
                bundle.layout.reports_dir
                / f"chapter_{chapter_number:03d}_world_rule_compliance.json"
            )
            runner._storage.save_json(world_rule_path, world_rule_report.model_dump(mode="json"))
            runner._on_step(
                "world_rule_finalize_gate",
                {
                    "chapter": chapter_number,
                    "rule_book_hash": world_rule_report.rule_book_hash,
                    "issues": [issue.model_dump(mode="json") for issue in world_rule_report.issues],
                    "blocking": world_rule_report.has_blocking_conflict,
                },
            )
            if world_rule_report.has_blocking_conflict:
                findings = world_rule_report_to_findings(world_rule_report)
                messages = [
                    f"世界规则硬冲突 [{finding.metadata.get('rule_id', '')}]：{finding.summary}"
                    for finding in findings
                    if finding.blocks_finalize
                ]
                raise ConsistencyViolationError(
                    messages or ["世界规则硬冲突，已阻断归档。"],
                    violation_kind="world_rule_conflict",
                    failed_stage="world_rule_finalize_gate",
                    replan_target=RecoveryTarget.MANUAL,
                )

    archive_report_reasons = _archive_report_refresh_reasons(
        current_text=current_text,
        alignment_report=alignment_report,
        continuity_report=continuity_report,
        causal_report=causal_report,
    )
    if archive_report_reasons:
        for reason in archive_report_reasons:
            refresh_decision.require_quality_refresh(reason)
        runner._on_step(
            "archive_quality_reports_refresh_required",
            {
                "chapter": chapter_number,
                "reasons": archive_report_reasons,
                "source_text_hash": source_text_hash(current_text),
            },
        )

    if eval_report is not None:
        expected_eval_hash = source_text_hash(current_text)
        eval_hash = str(getattr(eval_report, "source_text_hash", "") or "").strip()
        if eval_hash and eval_hash != expected_eval_hash:
            runner._on_step(
                "archive_eval_report_refresh_required",
                {
                    "chapter": chapter_number,
                    "reason": "eval_source_hash_mismatch_before_archive",
                    "eval_source_text_hash": eval_hash,
                    "current_text_hash": expected_eval_hash,
                },
            )
            refresh_decision.add_reason("eval_source_hash_mismatch_before_archive")
            eval_report = None

    quality_evidence_ensured = False
    if refresh_decision.refresh_quality:
        refresh_reason = refresh_decision.refresh_reason(
            str(quality_reports_stale_reason or "").strip()
            or "semantic_text_changed_before_archive"
        )
        preserved_draft_path = _preserve_final_review_draft(
            runner=runner,
            bundle=bundle,
            chapter_number=chapter_number,
            current_text=current_text,
        )
        require_reading_power = bool(getattr(runner._settings, "reading_power_enabled", False))
        report_kinds = ["alignment", "continuity", "causal"]
        if require_reading_power:
            report_kinds.append("reading_power")
        try:
            refreshed_reports = await refresh_quality_reports_after_semantic_text_change(
                runner=runner,
                bundle=bundle,
                packet=packet,
                bridge=bridge,
                plan=plan,
                current_text=current_text,
                chapter_number=chapter_number,
                trace=trace,
                alignment_report=alignment_report,
                continuity_report=continuity_report,
                causal_report=causal_report,
                chapter_repair_report=chapter_repair_report,
                reading_power_report=None,
                window_manager=window_manager,
                stale_reason=refresh_reason,
                causal_recheck_mode=False,
                causal_strict_review=False,
                failure_policy=ReportRefreshFailurePolicy.FAIL_CLOSED,
                report_kinds=tuple(report_kinds),
            )
        except ModelGatewayError as exc:
            context = dict(getattr(exc, "context", {}) or {})
            context.update(
                {
                    "stage": "finalize_report_refresh",
                    "chapter_number": chapter_number,
                    "preserved_draft_path": preserved_draft_path,
                    "retryable": bool(getattr(exc, "is_transient_error", False)),
                    "recovery_actions": [
                        {
                            "action": "retry_final_review",
                            "description": "终稿已保留；模型路由恢复后重试质量复检。",
                        }
                    ],
                }
            )
            runner._on_step(
                "archive_quality_refresh_failed_blocking",
                {
                    "chapter": chapter_number,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "is_transient": bool(getattr(exc, "is_transient_error", False)),
                    "preserved_draft_path": preserved_draft_path,
                    "action": "retry_final_review",
                },
            )
            raise ModelGatewayError(
                f"终稿质量报告刷新失败，已保留终稿并阻止归档。原因：{exc}",
                is_transient=bool(getattr(exc, "is_transient_error", False)),
                context=context,
            ) from exc
        except Exception as exc:  # noqa: BLE001
            _logger.error(
                "archive_quality_refresh_failed | chapter=%d | error=%s",
                chapter_number,
                exc,
                exc_info=True,
            )
            runner._on_step(
                "archive_quality_refresh_failed_blocking",
                {
                    "chapter": chapter_number,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "preserved_draft_path": preserved_draft_path,
                    "action": "block_archive",
                },
            )
            raise FinalReportFreshnessError(
                chapter_number=chapter_number,
                expected_text_hash=source_text_hash(current_text),
                dimensions=report_kinds,
                detail=f"质量复检执行失败：{exc}",
                cause=exc,
            ) from exc
        else:
            alignment_report = refreshed_reports.alignment_report
            continuity_report = refreshed_reports.continuity_report
            causal_report = refreshed_reports.causal_report
            chapter_repair_report = refreshed_reports.chapter_repair_report
            refreshed_reading_power_report = refreshed_reports.reading_power_report
            quality_evidence_ensured = True

    # Run the shared planner at the archive boundary unless this exact final
    # text/evidence pair was already ensured by the refresh branch above.
    # This is normally a zero-model-call reuse check; legacy or mismatched
    # reports are refreshed rather than restamped.
    require_reading_power = bool(getattr(runner._settings, "reading_power_enabled", False))
    authoritative_report_kinds = ["alignment", "continuity", "causal"]
    if require_reading_power:
        authoritative_report_kinds.append("reading_power")
    if not quality_evidence_ensured:
        try:
            authoritative_reports = await refresh_quality_reports_after_semantic_text_change(
                runner=runner,
                bundle=bundle,
                packet=packet,
                bridge=bridge,
                plan=plan,
                current_text=current_text,
                chapter_number=chapter_number,
                trace=trace,
                alignment_report=alignment_report,
                continuity_report=continuity_report,
                causal_report=causal_report,
                chapter_repair_report=chapter_repair_report,
                reading_power_report=refreshed_reading_power_report,
                window_manager=window_manager,
                stale_reason="archive_evidence_binding_check",
                failure_policy=ReportRefreshFailurePolicy.FAIL_CLOSED,
                report_kinds=tuple(authoritative_report_kinds),
            )
        except Exception as exc:  # noqa: BLE001
            raise FinalReportFreshnessError(
                chapter_number=chapter_number,
                expected_text_hash=source_text_hash(current_text),
                dimensions=authoritative_report_kinds,
                detail=f"归档证据绑定复核失败：{exc}",
                cause=exc,
            ) from exc
        alignment_report = authoritative_reports.alignment_report
        continuity_report = authoritative_reports.continuity_report
        causal_report = authoritative_reports.causal_report
        chapter_repair_report = authoritative_reports.chapter_repair_report
        refreshed_reading_power_report = authoritative_reports.reading_power_report

    _assert_authoritative_archive_reports_current(
        runner=runner,
        bundle=bundle,
        chapter_number=chapter_number,
        current_text=current_text,
        packet=packet,
        bridge=bridge,
        plan=plan,
        causal_report=causal_report,
        require_reading_power=require_reading_power,
    )

    if refresh_decision.refresh_outcome:
        outcome = await extract_and_validate(
            runner,
            bundle,
            packet,
            bridge,
            plan,
            current_text,
            chapter_number,
            trace,
            continuity_report,
            repair_exhausted=True,
        )

    if refresh_decision.semantic_text_changed:
        guard_refresh = await run_guard_compliance_for_final_text(
            runner=runner,
            bundle=bundle,
            packet=packet,
            current_text=current_text,
            chapter_number=chapter_number,
            storage=runner._storage,
        )
        refreshed_guard_compliance_report = guard_refresh.guard_compliance_report
        refreshed_review_findings = list(guard_refresh.guard_findings)
        refreshed_repair_tickets = list(guard_refresh.guard_tickets)

    try:
        assert_upstream_revision_fingerprint_current(
            runner._storage,
            bundle.layout,
            chapter_number,
            getattr(bundle, "upstream_revision_fingerprint", None),
        )
    except RuntimeError as exc:
        orphan_path = save_orphaned_chapter_draft(
            bundle.layout,
            chapter_number,
            current_text,
            reason="upstream_revision_changed_before_finalize",
            expected_fingerprint=getattr(bundle, "upstream_revision_fingerprint", None),
        )
        runner._on_step(
            "upstream_revision_changed",
            {
                "chapter": chapter_number,
                "orphan_draft_path": str(orphan_path),
                "error": str(exc),
            },
        )
        raise

    # Re-run evaluation BEFORE the hard quality gate when text was mutated by
    # pre-archive cleanup (prompt-leak repair, text validation, state adjudication).
    # Without this, eval_report can be None at gate time and the eval-score floor
    # would be silently skipped — a low-quality chapter would slip through.
    if eval_report is None:
        gate_memory_context = await build_finalize_eval_memory_context(
            runner,
            bundle,
            chapter_number,
            memory_hints=memory_hints,
        )
        _gate_eval_extra: dict[str, Any] = {"causal_link": getattr(bridge, "causal_link", None)}
        _gate_eval_extra.update(gate_memory_context)
        eval_report = await evaluate_chapter_text(
            ChapterExecutionContext(
                storage=runner._storage,
                router=runner._router,
                builder=runner._builder,
                settings=runner._settings,
                config=runner._config,
                merger=runner._merger,
                rules=runner._rules,
                on_step=runner._on_step,
                select_character_profiles=runner._select_character_profiles,
                compact_previous_creative_report=runner._compact_previous_creative_report,
                compress_prompt_context=runner._compress_prompt_context,
                remove_opening_echo_from_previous=runner._remove_opening_echo_from_previous,
                apply_chapter_compaction=runner._apply_chapter_compaction,
                finalize_volume_if_needed=runner._finalize_volume_if_needed,
                is_outline_option_enabled_for_task=runner._is_outline_option_enabled_for_task,
                render_prompt=runner._builder.render,
            ),
            bundle=bundle,
            chapter_number=chapter_number,
            current_text=current_text,
            trace=trace,
            emit_step=False,
            extra_context=_gate_eval_extra,
            plan=plan,
        )

    # ── Defensive refresh: stale continuity/causal reports ─────────────
    # When text was mutated after Review (force_mark_quality_reports_stale)
    # and critical reports are None (dropped by hash mismatch), the hard gate
    # below would silently skip continuity/causal checks. Refresh them here
    # so the gate always sees fresh scores for the text being archived.
    if force_mark_quality_reports_stale and (continuity_report is None or causal_report is None):
        runner._on_step(
            "archive_stale_reports_refresh",
            {
                "chapter": chapter_number,
                "reason": quality_reports_stale_reason or "reports_none_after_text_change",
                "continuity_missing": continuity_report is None,
                "causal_missing": causal_report is None,
            },
        )
        try:
            _refreshed = await refresh_quality_reports_after_semantic_text_change(
                runner=runner,
                bundle=bundle,
                packet=packet,
                bridge=bridge,
                plan=plan,
                current_text=current_text,
                chapter_number=chapter_number,
                trace=trace,
                alignment_report=alignment_report,
                continuity_report=continuity_report,
                causal_report=causal_report,
                stale_reason=quality_reports_stale_reason or "reports_none_before_archive_gate",
            )
            alignment_report = _refreshed.alignment_report
            continuity_report = _refreshed.continuity_report
            causal_report = _refreshed.causal_report
        except Exception as _refresh_exc:
            _logger.warning(
                "archive_stale_reports_refresh_failed | chapter=%d | error=%s",
                chapter_number,
                _refresh_exc,
            )
            runner._on_step(
                "archive_stale_reports_refresh_failed",
                {"chapter": chapter_number, "error": str(_refresh_exc)[:300]},
            )

    # ── Hard quality gate BEFORE any persistence ──────────────────────
    # This check must fire before saving text or updating canon state.
    # If quality floors are violated, we reject the archive entirely —
    # no partial commits that leave canon/text in an inconsistent state
    # (which would prevent re-entry via resolve_chapter_checkpoint).
    try:
        _enforce_archive_hard_quality_blocks(
            runner,
            chapter_number=chapter_number,
            eval_report=eval_report,
            alignment_report=alignment_report,
            continuity_report=continuity_report,
            causal_report=causal_report,
            chapter_repair_report=chapter_repair_report,
            current_text=current_text,
            packet=packet,
            allow_carry_forward_archive_bypass=allow_carry_forward_archive_bypass,
        )
    except ConsistencyViolationError:
        orphan_path = save_orphaned_chapter_draft(
            bundle.layout,
            chapter_number,
            current_text,
            reason="archive_hard_quality_block",
            expected_fingerprint=getattr(bundle, "upstream_revision_fingerprint", None),
        )
        runner._on_step(
            "archive_quality_block_orphan_saved",
            {"chapter": chapter_number, "orphan_draft_path": str(orphan_path)},
        )
        raise

    from novel_forge.persistence.authoring_store import (
        AuthoringAcceptanceRequired,
        require_archive_authority,
    )
    from novel_forge.pipeline.finalization_manifest import require_authoring_chapter_predecessor

    try:
        require_archive_authority(bundle.layout.root, chapter_number, current_text)
        require_authoring_chapter_predecessor(bundle.layout.root, chapter_number, "archive")
    except AuthoringAcceptanceRequired as exc:
        exc.review_state = {
            "current_text": current_text,
            "outcome": outcome,
            "alignment_report": alignment_report,
            "chapter_repair_report": chapter_repair_report,
            "continuity_report": continuity_report,
            "causal_report": causal_report,
            "eval_report": eval_report,
            "repair_plan": repair_plan,
            "reading_power_report": refreshed_reading_power_report,
            "guard_compliance_report": refreshed_guard_compliance_report,
            "review_findings": tuple(refreshed_review_findings)
            if refreshed_review_findings
            else None,
            "repair_tickets": tuple(refreshed_repair_tickets) if refreshed_repair_tickets else None,
        }
        raise
    expected_hash = source_text_hash(current_text)
    manifest = ArtifactManifest(runner._storage, bundle.layout)
    final_path = bundle.layout.chapter_path(chapter_number)
    final_artifact_path = bundle.layout.chapter_artifact_path(chapter_number, "final")
    final_record = matching_finalization_phase(
        manifest,
        chapter_number=chapter_number,
        phase="final_text",
        text_hash=expected_hash,
    )
    final_text_matches = False
    try:
        final_text_matches = (
            runner._storage.exists(final_path)
            and source_text_hash(runner._storage.load_text(final_path)) == expected_hash
        )
    except Exception:
        final_text_matches = False
    final_artifact_matches = False
    try:
        artifact_payload = runner._storage.load_json(final_artifact_path)
        raw_payload = artifact_payload.get("payload") if isinstance(artifact_payload, dict) else {}
        final_artifact_matches = bool(
            isinstance(raw_payload, dict)
            and str(raw_payload.get("text_hash") or "") == expected_hash
        )
    except Exception:
        final_artifact_matches = False

    if final_record is None or not final_text_matches or not final_artifact_matches:
        if not final_text_matches:
            runner._storage.save_text(final_path, current_text)
        clear_word_count_rejections(runner._storage, bundle.layout, chapter_number)
        if not final_artifact_matches:
            _persist_finalize_stage_artifact(
                runner,
                bundle,
                chapter_number=chapter_number,
                artifact_type="final",
                previous_artifact_types=("humanize", "polish", "repair", "review", "wave"),
                payload={
                    "chapter_path": str(final_path),
                    "text_hash": expected_hash,
                    "text_chars": len(current_text),
                    "word_count": count_chapter_words(current_text),
                    "archive_gate": "passed",
                    "terminal_humanize": terminal_humanize_metadata,
                    "reports": {
                        "eval": _artifact_report_summary(eval_report),
                        "alignment": _artifact_report_summary(alignment_report),
                        "continuity": _artifact_report_summary(continuity_report),
                        "chapter_repair": _artifact_report_summary(chapter_repair_report),
                        "causal": _artifact_report_summary(causal_report),
                        "reading_power": _artifact_report_summary(refreshed_reading_power_report),
                    },
                },
                event_ledger=[
                    {
                        "event": "final_text_persisted",
                        "text_hash": expected_hash,
                        "word_count": count_chapter_words(current_text),
                    }
                ],
            )
        record_finalization_success(
            manifest,
            chapter_number=chapter_number,
            phase="final_text",
            text_hash=expected_hash,
            paths={"chapter": str(final_path), "artifact": str(final_artifact_path)},
            metadata={"archive_gate": "passed"},
        )
    else:
        runner._on_step(
            "finalization_phase_reused",
            {"chapter": chapter_number, "phase": "final_text", "text_hash": expected_hash},
        )
    # ── Re-stamp report hashes only for deterministic text cleanup ───────
    # _clean_and_validate_chapter_text may modify text mechanically (JSON
    # extraction, heading stripping, prompt-artifact scrubbing). For those
    # deterministic changes, reports can be rebound to the cleaned archive
    # text. If a LLM-authored semantic patch ran before archive, hard quality
    # reports have already been re-run above; any other stale sidecar reports
    # are marked stale instead of being made to look fresh.
    stale_reason = refresh_decision.refresh_reason(
        str(quality_reports_stale_reason or "").strip() or "semantic_text_changed_before_archive"
    )
    _finalize_report_hash_transaction(
        runner,
        bundle.layout,
        chapter_number,
        expected_hash,
        allow_restamp=not refresh_decision.refresh_quality and not force_mark_quality_reports_stale,
        stale_reason=stale_reason,
    )

    # ── Narrative state / story kernel persistence ──────────────────────
    # Mirrors the narrative_state_required gate already used in the
    # adjudication phase above (line ~1770): when narrative state is
    # optional, local persistence failures must not kill the pipeline
    # after chapter text has already been committed to disk.
    _narrative_state_required = getattr(runner._settings, "narrative_state_required", True)
    _kernel_persist_error: str | None = None
    _kernel_persist_error_type: str | None = None

    state_record = matching_finalization_phase(
        manifest,
        chapter_number=chapter_number,
        phase="narrative_state",
        text_hash=expected_hash,
    )
    if state_adjudication_report is None:
        if state_record is None:
            record_finalization_success(
                manifest,
                chapter_number=chapter_number,
                phase="narrative_state",
                text_hash=expected_hash,
                metadata={"skipped": True, "reason": "no_state_adjudication_report"},
            )
    elif state_record is not None:
        runner._on_step(
            "finalization_phase_reused",
            {"chapter": chapter_number, "phase": "narrative_state", "text_hash": expected_hash},
        )
    else:
        try:
            ledger_entries = await persist_adjudicated_state_ledger(
                project_root=bundle.layout.root,
                report=state_adjudication_report,
            )
            from novel_forge.pipeline.long.services.narrative_evidence_sync import (
                sync_narrative_evidence,
            )

            evidence_sync = await sync_narrative_evidence(
                memory_context=(runner.memory_context if runner.has_memory_context() else None),
                project_root=bundle.layout.root,
                ledger_entries=ledger_entries,
            )
            runner._on_step(
                "state_ledger_written",
                {
                    "chapter": chapter_number,
                    "entries": len(ledger_entries),
                    "evidence_sync": evidence_sync,
                },
            )
            ledger_hash = source_text_hash(
                "|".join(str(getattr(entry, "entry_id", "")) for entry in ledger_entries)
            )
            record_finalization_success(
                manifest,
                chapter_number=chapter_number,
                phase="narrative_state",
                text_hash=expected_hash,
                output_hashes={"ledger": ledger_hash},
                paths={
                    "ledger": str(bundle.layout.root / "narrative_state" / "state_ledger.jsonl")
                },
                metadata={"entries": len(ledger_entries)},
            )
        except Exception as exc:
            record_finalization_pending(
                manifest,
                chapter_number=chapter_number,
                phase="narrative_state",
                text_hash=expected_hash,
                error=exc,
                metadata={"required": bool(_narrative_state_required)},
            )
            if _narrative_state_required:
                raise StateError(
                    "narrative_state_persistence",
                    expected="success",
                    actual=type(exc).__name__,
                    cause=exc,
                ) from exc
            _kernel_persist_error = str(exc)
            _kernel_persist_error_type = type(exc).__name__
            _logger.warning(
                "state_ledger_persist_failed_non_blocking | chapter=%d | error=%s",
                chapter_number,
                exc,
                exc_info=True,
            )
            runner._on_step(
                "state_ledger_written",
                {"chapter": chapter_number, "entries": 0, "error": str(exc)[:500]},
            )

    kernel_record = matching_finalization_phase(
        manifest,
        chapter_number=chapter_number,
        phase="story_kernel",
        text_hash=expected_hash,
    )
    if kernel_record is not None:
        runner._on_step(
            "finalization_phase_reused",
            {"chapter": chapter_number, "phase": "story_kernel", "text_hash": expected_hash},
        )
    else:
        try:
            await _write_chapter_outcome_to_story_kernel(
                runner,
                bundle,
                outcome,
                state_adjudication_report,
            )
            record_finalization_success(
                manifest,
                chapter_number=chapter_number,
                phase="story_kernel",
                text_hash=expected_hash,
                output_hashes={"story_kernel": expected_hash},
                metadata={"source_chapter": chapter_number},
            )
        except Exception as exc:
            record_finalization_pending(
                manifest,
                chapter_number=chapter_number,
                phase="story_kernel",
                text_hash=expected_hash,
                error=exc,
                metadata={"required": bool(_narrative_state_required)},
            )
            if _narrative_state_required:
                raise StateError(
                    "story_kernel_persistence",
                    expected="success",
                    actual=type(exc).__name__,
                    cause=exc,
                ) from exc
            _kernel_persist_error = _kernel_persist_error or str(exc)
            _kernel_persist_error_type = _kernel_persist_error_type or type(exc).__name__
            _logger.warning(
                "story_kernel_persist_failed_non_blocking | chapter=%d | error=%s",
                chapter_number,
                exc,
                exc_info=True,
            )

    if _kernel_persist_error:
        runner._on_step(
            "kernel_persist_deferred",
            {
                "chapter": chapter_number,
                "error": _kernel_persist_error[:500],
                "narrative_state_required": _narrative_state_required,
                "pending_manifest_path": str(manifest.path),
            },
        )

    try:
        pov_character = ""
        for profile in getattr(packet, "character_profiles", []) or []:
            if isinstance(profile, dict) and str(profile.get("pov", "")).lower() in (
                "true",
                "1",
                "yes",
            ):
                pov_character = str(profile.get("name", "")).strip()
                break
        if not pov_character:
            pov_character = str(getattr(packet, "pov_character", "") or "").strip()
        await _extract_and_apply_knowledge_deltas(
            runner,
            bundle,
            chapter_number=chapter_number,
            current_text=current_text,
            pov_character=pov_character,
            trace=trace,
        )
    except Exception as exc:
        _logger.warning(
            "extract_knowledge_deltas_failed_non_blocking | chapter=%d | error=%s",
            chapter_number,
            exc,
            exc_info=True,
        )
        runner._on_step(
            "extract_knowledge_deltas_failed",
            {"chapter": chapter_number, "error": str(exc)[:500]},
        )

    # Save edit tracker snapshot so manual edits can be detected later
    try:
        from novel_forge.core.utils.edit_tracker import save_snapshot

        save_snapshot(
            bundle.layout.chapter_path(chapter_number),
            bundle.layout.states_dir,
            chapter_number,
        )
    except Exception as exc:
        # Non-critical: don't fail chapter pipeline for snapshot error
        _logger.warning(
            "edit_tracker_snapshot_failed | chapter=%d | error=%s",
            chapter_number,
            exc,
        )

    # ── Story kernel write (canon merge removed — single-write to story_kernel) ──
    runner._on_step("persist", {"chapter": chapter_number})

    invalidated_chapters = invalidate_downstream_generated_artifacts(
        runner._storage,
        bundle.layout,
        completed_chapter=chapter_number,
    )
    if invalidated_chapters:
        runner._on_step(
            "downstream_invalidated",
            {
                "chapter": chapter_number,
                "invalidated_chapters": invalidated_chapters,
            },
        )

        if runner.has_memory_context():
            memory_ctx = runner.memory_context
            first_invalidated = min(invalidated_chapters)
            memory_result = memory_ctx.invalidate_chapter_memory(first_invalidated)
            runner._on_step(
                "memory_invalidated",
                {
                    "chapter": chapter_number,
                    "from_chapter": first_invalidated,
                    "memory_result": memory_result,
                },
            )

    finalize_memory_context = await build_finalize_eval_memory_context(
        runner,
        bundle,
        chapter_number,
        memory_hints=memory_hints,
    )
    finalize_memory_diagnostics = finalize_memory_context.get("memory_diagnostics")
    if isinstance(finalize_memory_diagnostics, dict) and finalize_memory_diagnostics:
        persist_stage_memory_diagnostics_report(
            runner._storage,
            bundle.layout,
            chapter_number,
            finalize_memory_diagnostics,
        )
        runner._on_step("memory_finalize_context", finalize_memory_diagnostics)

    reports_record = matching_finalization_phase(
        manifest,
        chapter_number=chapter_number,
        phase="reports",
        text_hash=expected_hash,
    )
    final_eval_report = eval_report
    if final_eval_report is None and reports_record is not None:
        try:
            final_eval_report = EvalReport.model_validate(
                runner._storage.load_json(bundle.layout.eval_report_path(chapter_number))
            )
            runner._on_step(
                "finalization_phase_reused",
                {"chapter": chapter_number, "phase": "reports", "text_hash": expected_hash},
            )
        except Exception:
            reports_record = None
    if final_eval_report is None:
        _eval_extra_context: dict[str, Any] = {"causal_link": getattr(bridge, "causal_link", None)}
        _eval_extra_context.update(finalize_memory_context)
        final_eval_report = await evaluate_chapter_text(
            ChapterExecutionContext(
                storage=runner._storage,
                router=runner._router,
                builder=runner._builder,
                settings=runner._settings,
                config=runner._config,
                merger=runner._merger,
                rules=runner._rules,
                on_step=runner._on_step,
                select_character_profiles=runner._select_character_profiles,
                compact_previous_creative_report=runner._compact_previous_creative_report,
                compress_prompt_context=runner._compress_prompt_context,
                remove_opening_echo_from_previous=runner._remove_opening_echo_from_previous,
                apply_chapter_compaction=runner._apply_chapter_compaction,
                finalize_volume_if_needed=runner._finalize_volume_if_needed,
                is_outline_option_enabled_for_task=runner._is_outline_option_enabled_for_task,
                render_prompt=runner._builder.render,
            ),
            bundle=bundle,
            chapter_number=chapter_number,
            current_text=current_text,
            trace=trace,
            emit_step=emit_evaluate_step,
            extra_context=_eval_extra_context,
            plan=plan,
        )
    else:
        eval_payload = final_eval_report.model_dump(mode="json")
        final_text_hash = source_text_hash(current_text)
        report_hash = str(
            eval_payload.get("source_text_hash")
            or getattr(final_eval_report, "source_text_hash", "")
            or ""
        ).strip()
        if report_hash and report_hash != final_text_hash:
            eval_payload["source_text_hash"] = report_hash
            eval_payload["stale_after_text_change"] = True
            eval_payload["stale_source_text_hash"] = report_hash
            eval_payload["stale_expected_text_hash"] = final_text_hash
            eval_payload["stale_reason"] = refresh_decision.refresh_reason(
                "eval_source_hash_mismatch_before_archive"
            )
        else:
            eval_payload["source_text_hash"] = final_text_hash
            eval_payload.pop("stale_after_text_change", None)
            eval_payload.pop("stale_source_text_hash", None)
            eval_payload.pop("stale_expected_text_hash", None)
            eval_payload.pop("stale_reason", None)
        runner._storage.save_json(
            bundle.layout.eval_report_path(chapter_number),
            eval_payload,
        )
        if emit_evaluate_step:
            runner._on_step("evaluate", final_eval_report.model_dump(mode="json"))

    if reports_record is None:
        record_finalization_success(
            manifest,
            chapter_number=chapter_number,
            phase="reports",
            text_hash=expected_hash,
            output_hashes={
                "eval": source_text_hash(
                    str(final_eval_report.model_dump(mode="json") if final_eval_report else {})
                )
            },
            paths={"eval": str(bundle.layout.eval_report_path(chapter_number))},
            metadata={"source_text_hash": expected_hash},
        )

    # Volume finalization (optional)
    try:
        await runner._finalize_volume_if_needed(
            layout=bundle.layout,
            outline=bundle.outline,
            chapter_number=chapter_number,
            canon_state=bundle.canon_state,
            story_bible=bundle.story_bible,
            canon_store=bundle.canon_store,
            trace=trace,
            memory_context=runner.memory_context if runner.has_memory_context() else None,
        )
    except Exception as exc:
        runner._on_step("volume_audit_failed", {"chapter": chapter_number, "error": str(exc)})
        _logger.error(
            "volume_audit_failed | chapter=%d | error=%s",
            chapter_number,
            exc,
            exc_info=True,
        )

    # ── Strand Weave: record chapter strand distribution ─────────────────
    try:
        strand_path = bundle.layout.root / "states" / "strand_tracker.json"
        strand_raw = runner._storage.load_json(strand_path) if strand_path.exists() else {}
        strand_tracker = StrandTracker.model_validate(strand_raw) if strand_raw else StrandTracker()
        dominant, secondary = infer_dominant_strand(
            current_text, plan=plan, style_profile=getattr(bundle, "style_profile", None)
        )
        strand_tracker.record(chapter_number, dominant, secondary)
        runner._storage.save_json(strand_path, strand_tracker.model_dump(mode="json"))
        runner._on_step(
            "strand_weave_recorded",
            {
                "chapter": chapter_number,
                "dominant": dominant.value,
                "secondary": [s.value for s in secondary],
                "distribution": strand_tracker.distribution(window=20),
            },
        )
    except Exception as exc:
        _logger.warning("strand_weave_record_failed | chapter=%d | error=%s", chapter_number, exc)

    # ── Reading Power Score Gate: check overall_score and alert if critical ──
    reading_power_gate_result: dict[str, Any] | None = None
    try:
        rp_report_path = bundle.layout.reading_power_report_path(chapter_number)
        rp_data = runner._storage.load_json(rp_report_path) if rp_report_path.exists() else None
        # ── P0-3 stale-hash guard: a reading_power report from a prior
        # cancelled run (e.g. 123101 in 弈局谋心) can linger on disk and be
        # mistaken for the current chapter's evaluation. Reject any report
        # whose source_text_hash does not match the current chapter text.
        is_stale, stored_hash, current_hash = _is_reading_power_stale(rp_data, current_text)
        if is_stale:
            # is_stale=True implies rp_data was a dict (see helper guard),
            # but mypy can't infer that — narrow explicitly for the
            # diagnostic payload.
            stored_score = rp_data.get("overall_score") if rp_data else None
            runner._on_step(
                "reading_power_hash_mismatch",
                {
                    "chapter": chapter_number,
                    "stored_hash": stored_hash,
                    "current_hash": current_hash,
                    "stored_score": stored_score,
                    "report_path": str(rp_report_path),
                },
            )
            _logger.warning(
                "reading_power_stale | chapter=%d | stored_hash=%s != current_hash=%s",
                chapter_number,
                stored_hash,
                current_hash,
            )
            rp_data = None
        if rp_data:
            is_fallback_rp = bool(
                rp_data.get("is_fallback")
                or str(rp_data.get("evaluation_status", "")).lower() == "fallback"
            )
            if is_fallback_rp:
                runner._on_step(
                    "reading_power_eval_fallback",
                    {
                        "chapter": chapter_number,
                        "reason": rp_data.get("fallback_reason", "unknown"),
                    },
                )
                _logger.info(
                    "reading_power_score_gate skipped for fallback report | chapter=%d | reason=%s",
                    chapter_number,
                    rp_data.get("fallback_reason", "unknown"),
                )
                rp_data = None

        if rp_data:
            overall_score = rp_data.get("overall_score", 0.0)
            suggestions = rp_data.get("suggestions", [])
            style_profile = getattr(bundle, "style_profile", None)
            rp_window_cfg = coerce_reading_power_window_config(style_profile)
            critical_threshold = (
                getattr(rp_window_cfg, "critical_score_threshold", 3.0) if rp_window_cfg else 3.0
            )
            warning_threshold = (
                getattr(rp_window_cfg, "warning_score_threshold", 5.0) if rp_window_cfg else 5.0
            )

            if overall_score < critical_threshold:
                runner._on_step(
                    "reading_power_critical",
                    {
                        "chapter": chapter_number,
                        "overall_score": overall_score,
                        "threshold": critical_threshold,
                        "suggestions": suggestions,
                    },
                )
                _logger.warning(
                    "reading_power_critical | chapter=%d | score=%.1f | threshold=%.1f | suggestions=%s",
                    chapter_number,
                    overall_score,
                    critical_threshold,
                    suggestions,
                )
                reading_power_gate_result = {
                    "gate": "critical",
                    "score": overall_score,
                    "threshold": critical_threshold,
                    "suggestions": suggestions,
                    "recommendation": "replan_closing",
                }
            elif overall_score < warning_threshold:
                runner._on_step(
                    "reading_power_warning",
                    {
                        "chapter": chapter_number,
                        "overall_score": overall_score,
                        "threshold": warning_threshold,
                    },
                )
                reading_power_gate_result = {
                    "gate": "warning",
                    "score": overall_score,
                    "threshold": warning_threshold,
                    "suggestions": suggestions,
                    "recommendation": "strengthen_hooks",
                }
    except Exception as exc:
        _logger.debug(
            "reading_power_score_gate_failed | chapter=%d | error=%s", chapter_number, exc
        )

    # ── Persist reading power gate result for downstream decision-making ──
    if reading_power_gate_result is not None:
        try:
            gate_path = (
                bundle.layout.root / "states" / f"reading_power_gate_ch{chapter_number}.json"
            )
            runner._storage.save_json(gate_path, reading_power_gate_result)
        except Exception as gate_exc:
            _logger.debug(
                "reading_power_gate_save_failed | chapter=%d | error=%s",
                chapter_number,
                gate_exc,
            )

    # ── Reading Power Trend Analysis ──────────────────────────────────────
    reading_power_trend: dict[str, Any] | None = None
    if window_manager is not None:
        try:
            reading_power_trend = window_manager.compute_reading_power_trend()
            if reading_power_trend:
                runner._on_step(
                    "reading_power_trend",
                    {
                        "chapter": chapter_number,
                        "trend": reading_power_trend.get("trend", "unknown"),
                        "mean_score": reading_power_trend.get("mean", 0.0),
                        "total_chapters": reading_power_trend.get("total_chapters_evaluated", 0),
                    },
                )
                _logger.info(
                    "reading_power_trend | chapter=%d | trend=%s | mean=%.1f | chapters=%d",
                    chapter_number,
                    reading_power_trend.get("trend", "unknown"),
                    reading_power_trend.get("mean", 0.0),
                    reading_power_trend.get("total_chapters_evaluated", 0),
                )
        except Exception as exc:
            _logger.debug("reading_power_trend_failed | chapter=%d | error=%s", chapter_number, exc)

    # ── Long-form Quality Trend Tracker ─────────────────────────────────
    if bool(getattr(runner._settings, "long_quality_trend_tracker_enabled", False)):
        try:
            from novel_forge.pipeline.long.services.quality.quality_trend import QualityTrendTracker
            from novel_forge.story_kernel.store import StoryKernelStore

            def _float_metric(value: Any, default: float = 0.0) -> float:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return default

            tracker = QualityTrendTracker(bundle.layout.states_dir)
            reading_power_score = _float_metric(
                getattr(refreshed_reading_power_report, "overall_score", 0.0)
                if refreshed_reading_power_report is not None
                else 0.0
            )
            continuity_score = _float_metric(getattr(continuity_report, "continuity_score", 0.0))
            character_recall = 0.0
            retrieval_path = bundle.layout.retrieval_eval_report_path(chapter_number)
            if runner._storage.exists(retrieval_path):
                retrieval_payload = runner._storage.load_json(retrieval_path)
                if isinstance(retrieval_payload, dict):
                    aggregate = retrieval_payload.get("aggregate") or {}
                    if isinstance(aggregate, dict):
                        character_recall = _float_metric(
                            aggregate.get("character_recall"),
                            0.0,
                        )

            promise_aging_score = 0.0
            raw_project_id = getattr(bundle, "project_id", "")
            project_id = raw_project_id.strip() if isinstance(raw_project_id, str) else ""
            if project_id:
                trend_store = StoryKernelStore(
                    _story_kernel_db_path(runner._settings, bundle.layout),
                    wal_mode=bool(getattr(runner._settings, "story_kernel_wal_mode", True)),
                )
                try:
                    await trend_store.init_db()
                    kernel = await trend_store.load_kernel(project_id)
                    promise_aging_score = tracker.compute_promise_aging_score(
                        kernel.promise_ledger,
                        chapter_number,
                        chapters_per_volume=int(
                            getattr(runner._settings, "long_default_chapters_per_volume", 20)
                        ),
                    )
                finally:
                    await trend_store.close()

            warnings = tracker.record_chapter(
                chapter_number,
                eval_score=_float_metric(getattr(final_eval_report, "overall_score", 0.0)),
                reading_power_score=reading_power_score,
                continuity_score=continuity_score,
                promise_aging_score=promise_aging_score,
                character_recall=character_recall,
            )
            runner._on_step(
                "quality_trend_recorded",
                {
                    "chapter": chapter_number,
                    "warnings": warnings,
                    "warning_count": len(warnings),
                    "promise_aging_score": promise_aging_score,
                    "character_recall": character_recall,
                },
            )
        except Exception as exc:
            _logger.debug(
                "quality_trend_record_failed | chapter=%d | error=%s", chapter_number, exc
            )
            runner._on_step(
                "quality_trend_record_failed",
                {"chapter": chapter_number, "error": str(exc)},
            )

    # ── Narrative element focus progress (rule-based + optional LLM arbiter) ──
    try:
        element_cards_by_id: dict[str, dict[str, Any]] = {}
        _bp = getattr(bundle, "blueprint", None)
        _selection = getattr(_bp, "element_selection", None)
        if _selection is not None:
            for card in list(getattr(_selection, "extension_elements", []) or []):
                element_id = str(getattr(card, "element_id", "") or "").strip()
                if not element_id:
                    continue
                element_cards_by_id[element_id] = (
                    card.model_dump(mode="json") if hasattr(card, "model_dump") else dict(card)
                )
        fallback_focus_ids = [
            str(item).strip()
            for item in list(getattr(bundle.chapter_outline, "element_focus", []) or [])
            if str(item).strip()
        ]
        progress_entry = await finalize_chapter_progress_with_optional_arbiter(
            runner._storage,
            bundle.layout,
            chapter_number=chapter_number,
            fallback_scheduled_ids=fallback_focus_ids,
            focus_source="outline",
            element_cards_by_id=element_cards_by_id,
            plan=plan,
            chapter_text=current_text,
            alignment_report=alignment_report,
            chapter_repair_report=chapter_repair_report,
            continuity_report=continuity_report,
            router=runner._router,
            settings=runner._settings,
            builder=runner._builder,
        )
        schedule_entry = None
        if progress_entry is not None:
            from novel_forge.pipeline.long.services.element_schedule import (
                update_element_schedule_from_progress,
            )

            schedule_entry = update_element_schedule_from_progress(
                runner._storage,
                bundle.layout,
                chapter_number=chapter_number,
                progress_entry=progress_entry,
                element_cards_by_id=element_cards_by_id,
            )
        if progress_entry is not None:
            runner._on_step(
                "element_progress_updated",
                {
                    "chapter": chapter_number,
                    "scheduled_element_ids": progress_entry.get("scheduled_element_ids", []),
                    "summary": progress_entry.get("summary", {}),
                    "schedule": schedule_entry or {},
                },
            )
    except Exception as exc:
        _logger.warning(
            "element_progress_finalize_failed | chapter=%d | error=%s", chapter_number, exc
        )
        runner._on_step(
            "element_progress_failed",
            {"chapter": chapter_number, "error": str(exc)},
        )

    return PersistedChapterArtifacts(
        eval_report=final_eval_report,
        current_text=current_text,
        outcome=outcome,
        alignment_report=alignment_report,
        chapter_repair_report=chapter_repair_report,
        continuity_report=continuity_report,
        causal_report=causal_report,
        reading_power_report=refreshed_reading_power_report,
        guard_compliance_report=refreshed_guard_compliance_report,
        review_findings=refreshed_review_findings or None,
        repair_tickets=refreshed_repair_tickets or None,
    )
