"""Implementation slice extracted from finalize.py (finalize_checks.py)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from novel_forge.core.exceptions import (
    ConsistencyViolationError,
    RecoveryTarget,
    classify_archive_quality_block,
)
from novel_forge.core.review.alignment_contracts import (
    alignment_uses_structured_contract,
    verified_alignment_blockers,
)
from novel_forge.core.schemas.chapter import CausalValidationReport, ChapterOutcome
from novel_forge.core.schemas.eval_schema import EvalReport
from novel_forge.core.utils.coerce import coerce_float
from novel_forge.core.utils.string import carry_forward_status, carry_forward_text
from novel_forge.core.utils.text_hash import source_text_hash
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.long.stages.finalize_common import extract_dimension_scores
from novel_forge.pipeline.long.stages.report_refresh import (
    refresh_quality_reports_after_semantic_text_change,
)
from novel_forge.pipeline.quality_gate import QualityGate

_logger = get_logger("pipeline.finalize")


@dataclass(frozen=True)
class PersistedChapterArtifacts:
    """Artifacts whose values may change during final archive guards."""

    eval_report: EvalReport
    current_text: str
    outcome: Any
    alignment_report: Any
    chapter_repair_report: Any | None
    continuity_report: Any
    causal_report: CausalValidationReport | None
    reading_power_report: Any | None = None
    guard_compliance_report: dict[str, Any] | None = None
    review_findings: list[Any] | None = None
    repair_tickets: list[Any] | None = None

def _archive_hard_block_thresholds(
    settings: Any,
) -> tuple[float, float]:
    """Return ``(continuity_hard_block, causal_hard_block)`` from settings.

    Shared by ``_enforce_archive_hard_quality_blocks`` (defensive diagnostics)
    and the inline hard-block gate in ``chapter_flow.py`` (force replan).
    Centralizing the default ``4.0`` and the setting keys prevents drift
    between the two callers.
    """
    continuity = coerce_float(
        getattr(settings, "long_continuity_hard_block_threshold", 4.0),
        4.0,
    )
    causal = coerce_float(
        getattr(settings, "long_causal_hard_block_threshold", 4.0),
        4.0,
    )
    return continuity, causal


def _archive_issue_counts(report: Any) -> tuple[int, int, int]:
    issues = list(getattr(report, "issues", []) or [])
    critical = [
        issue for issue in issues if str(getattr(issue, "severity", "") or "").lower() == "critical"
    ]
    high = [
        issue for issue in issues if str(getattr(issue, "severity", "") or "").lower() == "high"
    ]
    return len(issues), len(critical), len(high)


def _unclosed_carry_forward_items(packet: Any, current_text: str) -> list[str]:
    """Return open carry-forward items not addressed in the current chapter.

    Walks the previous chapter's exit-state ``must_carry_forward`` list (the
    authoritative source of what THIS chapter owes a response to), skipping
    any item explicitly marked ``abandoned`` or ``deferred``. Each remaining
    *open* item is checked against the current chapter text using the same
    partial-match heuristic as the continuity local checks. Items with zero
    textual trace are returned as evidence strings.

    This is the boolean heart of the carry-forward hard gate: a non-empty
    result means the chapter silently dropped a state thread the previous
    chapter explicitly asked it to carry, which is exactly the failure that
    let Ch15 archive while ignoring Ch14's "八个字录音".
    """
    if packet is None:
        return []
    previous_exit = getattr(packet, "previous_exit_state", None)
    carry_items: list[Any] = []
    if previous_exit is not None:
        carry_items = list(getattr(previous_exit, "must_carry_forward", []) or [])
    # Fall back to the packet-level projection if the exit state is absent.
    if not carry_items:
        carry_items = list(getattr(packet, "must_carry_forward", []) or [])

    if not carry_items:
        return []

    # Reuse the continuity_eval matcher for parity with the detection layer.
    from novel_forge.pipeline.steps.continuity_eval.local_checks import _LocalChecks

    unclosed: list[str] = []
    for item in carry_items:
        if carry_forward_status(item) != "open":
            continue
        text = carry_forward_text(item)
        if not text or _LocalChecks._is_meta_instruction(text):
            continue
        if _LocalChecks._carry_forward_item_present_in_text(text, current_text):
            continue
        unclosed.append(text)
    return unclosed


def _enforce_archive_hard_quality_blocks(
    runner: Any,
    *,
    chapter_number: int,
    eval_report: Any | None,
    continuity_report: Any | None,
    causal_report: Any | None,
    chapter_repair_report: Any | None,
    alignment_report: Any | None = None,
    current_text: str | None = None,
    packet: Any | None = None,
    allow_carry_forward_archive_bypass: bool = False,
    humanize_report: Any | None = None,
) -> None:
    """Reject archive writes that violate non-negotiable quality floors.

    The chapter-quality gate in particular relies on a ``chapter_repair_report``
    whose ``source_text_hash`` must match the current chapter text. A stale
    report (e.g. one loaded from a previous failed run's checkpoint while the
    text was updated in between) is treated as untrusted: the gate is skipped
    and a structured event is emitted so the caller can decide whether to
    refresh the report. This prevents a previous run's already-resolved issues
    from blocking an otherwise valid archive.
    """
    settings = getattr(runner, "_settings", None)
    messages: list[str] = []
    diagnostics: dict[str, Any] = {}

    # Use shared score extraction for report dimensions
    _scores = extract_dimension_scores(
        eval_report=eval_report,
        continuity_report=continuity_report,
        causal_report=causal_report,
    )

    if eval_report is not None and _scores["eval"] is None:
        diagnostics["eval_unavailable"] = True
        diagnostics["eval_status"] = str(
            getattr(eval_report, "evaluation_status", "fallback") or "fallback"
        )
        diagnostics["eval_fallback_reason"] = str(
            getattr(eval_report, "fallback_reason", "") or ""
        )
        on_step = getattr(runner, "_on_step", None)
        if callable(on_step):
            on_step(
                "archive_eval_unavailable_degraded",
                {
                    "chapter": chapter_number,
                    "evaluation_status": diagnostics["eval_status"],
                    "fallback_reason": diagnostics["eval_fallback_reason"],
                    "action": "skip_untrusted_eval_score_keep_other_hard_gates",
                },
            )

    if _scores["eval"] is not None:
        eval_score = _scores["eval"]
        min_accept = coerce_float(
            getattr(settings, "long_min_accept_score", 5.0),
            5.0,
        )
        if min_accept > 0.0 and eval_score < min_accept:
            messages.append(
                f"章节 {chapter_number} 评估分 {eval_score:.1f} 低于最低可接受线 {min_accept:.1f}，"
                "拒绝保存，等待定向修复与复检。"
            )

    if continuity_report is not None or causal_report is not None:
        continuity_hard_block, causal_hard_block = _archive_hard_block_thresholds(settings)

    if _scores["continuity"] is not None:
        continuity_score = _scores["continuity"]
        if continuity_hard_block > 0.0 and continuity_score < continuity_hard_block:
            issue_count, critical_count, _high_count = _archive_issue_counts(continuity_report)
            messages.append(
                f"章节 {chapter_number} 连贯分 {continuity_score:.1f} 低于硬阻断线 "
                f"{continuity_hard_block:.1f}，存在 {issue_count} 个连贯问题"
                f"（critical {critical_count} 个），拒绝保存，等待定向修复与复检。"
            )

    if _scores["causal"] is not None:
        causal_score = _scores["causal"]
        if causal_hard_block > 0.0 and causal_score < causal_hard_block:
            issue_count, critical_count, high_count = _archive_issue_counts(causal_report)
            messages.append(
                f"章节 {chapter_number} 因果分 {causal_score:.1f} 低于硬阻断线 "
                f"{causal_hard_block:.1f}，存在 {issue_count} 个因果问题"
                f"（critical {critical_count} 个, high {high_count} 个），拒绝保存，等待定向修复与复检。"
            )

    if alignment_report is not None:
        stored_hash = str(getattr(alignment_report, "source_text_hash", "") or "").strip()
        current_hash = source_text_hash(current_text) if current_text else ""
        if stored_hash and current_hash and stored_hash != current_hash:
            on_step = getattr(runner, "_on_step", None)
            if callable(on_step):
                on_step(
                    "alignment_report_stale_skipped",
                    {
                        "chapter": chapter_number,
                        "stored_hash": stored_hash,
                        "current_hash": current_hash,
                        "reason": "source_text_changed_after_report",
                    },
                )
        else:
            alignment_score = coerce_float(
                getattr(alignment_report, "alignment_score", 10.0),
                10.0,
            )
            alignment_threshold = coerce_float(
                getattr(settings, "long_alignment_threshold", 7.0),
                7.0,
            )
            diagnostics["alignment_score"] = alignment_score
            diagnostics["alignment_threshold"] = alignment_threshold
            structured_alignment = alignment_uses_structured_contract(alignment_report)
            alignment_blockers = verified_alignment_blockers(alignment_report)
            diagnostics["alignment_review_contract_version"] = int(
                getattr(alignment_report, "review_contract_version", 0) or 0
            )
            diagnostics["alignment_verified_blocker_count"] = len(alignment_blockers)
            if structured_alignment and alignment_blockers:
                preview = "；".join(finding.summary for finding in alignment_blockers[:3])
                messages.append(
                    f"章节 {chapter_number} 仍有 {len(alignment_blockers)} 个经证据核验的"
                    f"对齐阻断项：{preview}。拒绝保存，等待定向修复与复检。"
                )
            elif (
                not structured_alignment
                and alignment_threshold > 0.0
                and alignment_score < alignment_threshold
            ):
                messages.append(
                    f"章节 {chapter_number} 对齐分 {alignment_score:.1f} 低于归档阈值 "
                    f"{alignment_threshold:.1f}，拒绝保存，等待定向修复与复检。"
                )
            elif structured_alignment and alignment_score < alignment_threshold:
                diagnostics["alignment_low_score_advisory_only"] = True

    # ── Unclosed carry-forward hard gate ─────────────────────────────────
    # A previous chapter's open ``must_carry_forward`` items represent state
    # threads this chapter owes an explicit response to. Silently dropping
    # them is a cross-chapter state loss (the Ch14→15 "八个字录音" failure).
    # Items marked ``abandoned``/``deferred`` are the intentional-ellipsis
    # escape hatch and are skipped here.
    if packet is not None and current_text:
        unclosed = _unclosed_carry_forward_items(packet, current_text)
        if unclosed:
            diagnostics["unclosed_carry_forward"] = unclosed
            if allow_carry_forward_archive_bypass:
                diagnostics["carry_forward_archive_bypass"] = True
                on_step = getattr(runner, "_on_step", None)
                if callable(on_step):
                    on_step(
                        "carry_forward_archive_bypass",
                        {
                            "chapter": chapter_number,
                            "unclosed_carry_forward": unclosed,
                            "reason": "ai_auto_repair_already_applied",
                        },
                    )
            else:
                preview = "；".join(unclosed[:3])
                messages.append(
                    f"章节 {chapter_number} 有 {len(unclosed)} 个上一章必须承接的开放项"
                    f"在本章正文与前章结尾均无文本痕迹，跨章状态可能丢失：{preview}。"
                    "请在开场或关键转折处显式回应，或在上一章 exit_state 中声明 abandoned/deferred。"
                )

    if chapter_repair_report is not None:
        # A chapter_repair_report generated against an older text version must
        # not block the archive; the orchestrator owns refresh, and the gate is
        # only a defensive check that emits diagnostics when it disagrees.
        stored_hash = getattr(chapter_repair_report, "source_text_hash", None)
        current_hash = source_text_hash(current_text) if current_text else None
        diagnostics["chapter_repair_report_hash"] = stored_hash
        diagnostics["current_text_hash"] = current_hash
        if stored_hash and current_hash and stored_hash != current_hash:
            on_step = getattr(runner, "_on_step", None)
            if callable(on_step):
                on_step(
                    "chapter_repair_report_stale_skipped",
                    {
                        "chapter": chapter_number,
                        "stored_hash": stored_hash,
                        "current_hash": current_hash,
                        "reason": "source_text_changed_after_report",
                    },
                )
        else:
            chapter_quality_gate = QualityGate()
            chapter_quality = chapter_quality_gate.check_chapter_quality(chapter_repair_report)
            diagnostics["chapter_quality_score"] = chapter_quality.score
            diagnostics["chapter_quality_hard_count"] = chapter_quality.details.get("hard_count")
            diagnostics["chapter_quality_soft_count"] = chapter_quality.details.get("soft_count")
            if bool(chapter_quality.details.get("hard_fail")):
                # Replace the count-only default message with concrete evidence
                # so the operator can decide to repair, force-archive, or refresh.
                evidence = _format_chapter_quality_evidence(chapter_quality.details)
                base = (
                    f"章节 {chapter_number} 章节质量仍存在阻断级问题："
                    f"{chapter_quality.message or '请先完成章节质量修复'}。"
                )
                if evidence:
                    base = f"{base} 证据：{evidence}"
                messages.append(base)

    # ── P1-1: AI flavor hard block (configurable, default off) ────────────
    if humanize_report is not None and getattr(
        settings, "long_ai_flavor_block_archive", False
    ):
        ai_flavor_floor = coerce_float(
            getattr(settings, "long_ai_flavor_hard_block_threshold", 3.0), 3.0
        )
        # Extract humanize_score from report
        ai_flavor_score = coerce_float(
            getattr(humanize_report, "humanize_score", 10.0), 10.0
        )
        diagnostics["ai_flavor_score"] = ai_flavor_score
        diagnostics["ai_flavor_hard_block_threshold"] = ai_flavor_floor
        if ai_flavor_score < ai_flavor_floor:
            messages.append(
                f"章节 {chapter_number} AI味分 {ai_flavor_score:.1f} 低于硬阻断线 "
                f"{ai_flavor_floor:.1f}，拒绝保存，等待拟人化修复。"
            )

    if not messages:
        if diagnostics:
            on_step = getattr(runner, "_on_step", None)
            if callable(on_step):
                on_step("archive_hard_quality_diagnostics", diagnostics)
        return

    if diagnostics:
        diagnostics["violations"] = messages

    on_step = getattr(runner, "_on_step", None)
    if callable(on_step):
        payload: dict[str, Any] = {
            "chapter": chapter_number,
            "violations": messages,
        }
        payload.update(diagnostics)
        on_step("archive_hard_quality_block", payload)
    block_kind = classify_archive_quality_block(messages, default_to_generic=True)
    # This gate only runs after the report hash has been checked against the
    # exact text being archived.  A low score is therefore an execution-level
    # problem: keep the plan, route to the specialised repair lane, and demand
    # a fresh verification before another archive attempt.  Treating it as a
    # manual/plan failure was the reason a valid repair path was skipped.
    raise ConsistencyViolationError(
        messages,
        violation_kind="execution_fixable",
        failed_stage="archive_quality_gate",
        replan_target=RecoveryTarget.DRAFT,
        block_kind=block_kind,
    )


def _format_chapter_quality_evidence(details: dict[str, Any]) -> str:
    """Format a compact, evidence-rich summary of chapter-quality issues.

    The default QualityGate hard-fail message is a count (e.g. "1 个阻断级问题"),
    which is opaque to operators. This helper extracts the first few issue
    descriptions and concatenates them with their severity/location so the
    archive block reason carries actionable context. Truncated to keep the
    error message bounded.
    """
    parts: list[str] = []

    def _push(label: str, items: Any, max_items: int = 3) -> None:
        if not isinstance(items, list):
            return
        for item in items[:max_items]:
            text = str(item or "").strip()
            if not text:
                continue
            if len(text) > 220:
                text = text[:219].rstrip("，。、；： \n") + "…"
            parts.append(f"{label}={text}")

    _push("prompt_leaks", details.get("prompt_leaks"))
    _push("factual_errors", details.get("factual_errors"))
    _push("expression_errors", details.get("expression_errors"))
    _push("continuity_errors", details.get("continuity_errors"))

    forbidden = details.get("forbidden_element_findings")
    if isinstance(forbidden, list):
        for finding in forbidden[:3]:
            if not isinstance(finding, dict):
                continue
            verdict = str(finding.get("verdict", "")).strip()
            forbidden_word = str(finding.get("forbidden", "")).strip()
            matched = str(finding.get("matched", "")).strip()
            reason = str(finding.get("reason", "")).strip()
            label = f"forbidden[{verdict}]"
            detail = forbidden_word or matched
            if matched and matched != forbidden_word:
                detail = f"{forbidden_word or '?'}→{matched}"
            line = f"{label}={detail}"
            if reason:
                line += f"；{reason[:160]}"
            parts.append(line)

    return "；".join(parts)


def _prior_character_snapshots(character_states: list[Any]) -> list[dict[str, str]]:
    snapshots: list[dict[str, str]] = []
    for state in character_states:
        if hasattr(state, "get_character_state_view"):
            view = state.get_character_state_view()
            snapshots.append(
                {
                    "name": str(getattr(state, "name", "") or ""),
                    "location": view.physical.location or view.location,
                    "emotional_state": view.emotional_state,
                    "social_status": view.social_status,
                }
            )
            continue
        physical = getattr(state, "physical", None)
        emotional = getattr(state, "emotional", None)
        snapshots.append(
            {
                "name": str(getattr(state, "name", "") or ""),
                "location": str(getattr(physical, "location", "") or ""),
                "emotional_state": str(getattr(emotional, "primary_emotion", "") or ""),
                "social_status": str(getattr(state, "social_status", "") or ""),
            }
        )
    return [item for item in snapshots if item["name"]]


def _artifact_dump(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _artifact_dump(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_artifact_dump(item) for item in value]
    return str(value)


def _artifact_report_summary(report: Any) -> dict[str, Any]:
    payload = _artifact_dump(report)
    if not isinstance(payload, dict):
        return {}
    summary: dict[str, Any] = {}
    for key in (
        "source_text_hash",
        "alignment_score",
        "continuity_score",
        "causal_score",
        "overall_score",
        "quality_score",
        "summary",
        "verdict",
    ):
        if key in payload:
            summary[key] = payload.get(key)
    for issue_key in ("issues", "findings", "suggestions", "repair_suggestions"):
        values = payload.get(issue_key)
        if isinstance(values, list):
            summary[f"{issue_key}_count"] = len(values)
    return summary


def _persist_finalize_stage_artifact(
    runner: Any,
    bundle: Any,
    *,
    chapter_number: int,
    artifact_type: str,
    payload: dict[str, Any],
    previous_artifact_types: tuple[str, ...] = (),
    event_ledger: list[dict[str, Any]] | None = None,
) -> None:
    try:
        from novel_forge.pipeline.long.services.context.source_artifacts import (
            load_stage_artifact,
            persist_stage_artifact,
        )

        previous_artifact = None
        for previous_type in previous_artifact_types:
            previous_artifact = load_stage_artifact(
                runner._storage,
                bundle.layout,
                chapter_number=chapter_number,
                artifact_type=previous_type,
            )
            if previous_artifact is not None:
                break
        persist_stage_artifact(
            storage=runner._storage,
            layout=bundle.layout,
            project_id=getattr(bundle, "project_id", "") or "unknown",
            chapter_number=chapter_number,
            artifact_type=artifact_type,
            payload=payload,
            chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
            previous_artifact=previous_artifact,
            event_ledger=event_ledger,
        )
    except Exception as exc:
        _logger.warning(
            "finalize_stage_artifact_persist_failed | chapter=%d | artifact=%s | error=%s",
            chapter_number,
            artifact_type,
            exc,
        )


def _write_kernel_persist_pending_marker(
    runner: Any,
    bundle: Any,
    *,
    chapter_number: int,
    current_text: str,
    error_type: str,
    error: str,
) -> Any | None:
    """Persist a replay marker when optional kernel/state writes fail."""
    layout = getattr(bundle, "layout", None)
    if layout is None:
        return None
    states_dir = getattr(layout, "states_dir", None)
    if states_dir is None:
        root = getattr(layout, "root", None)
        if root is None:
            return None
        states_dir = root / "states"
    marker_path = states_dir / f"kernel_persist_pending_ch{chapter_number}.json"
    chapter_path = ""
    chapter_path_fn = getattr(layout, "chapter_path", None)
    if callable(chapter_path_fn):
        try:
            chapter_path = str(chapter_path_fn(chapter_number))
        except Exception:
            chapter_path = ""
    payload = {
        "chapter_number": chapter_number,
        "source_text_hash": source_text_hash(current_text),
        "chapter_path": chapter_path,
        "error_type": str(error_type or "Exception"),
        "error": str(error or "")[:1000],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "requires_replay": True,
    }
    runner._storage.save_json(marker_path, payload)
    return marker_path


def _is_reading_power_stale(
    rp_data: dict[str, Any] | None,
    current_text: str | None,
) -> tuple[bool, str | None, str | None]:
    """P0-3 helper: detect a reading_power report whose source_text_hash
    does not match the current chapter text. Such a report came from a
    previous cancelled run (e.g. 123101 in 弈局谋心) and must NOT be
    trusted as the current evaluation. Returns ``(is_stale, stored_hash,
    current_hash)`` — both hashes are returned (when available) for the
    caller's diagnostic event payload.
    """
    if not rp_data or not current_text:
        return False, None, None
    stored_hash = rp_data.get("source_text_hash")
    if not stored_hash:
        return False, None, None
    current_hash = source_text_hash(current_text)
    if stored_hash == current_hash:
        return False, stored_hash, current_hash
    return True, stored_hash, current_hash


def _normalize_outcome_chapter_number(
    runner: Any,
    outcome: ChapterOutcome,
    chapter_number: int,
) -> ChapterOutcome:
    updates: dict[str, Any] = {}
    if outcome.source_chapter != chapter_number:
        runner._on_step(
            "extract_canon_normalized",
            {
                "source_chapter": outcome.source_chapter,
                "normalized_to": chapter_number,
            },
        )
        updates["source_chapter"] = chapter_number
    if outcome.chapter_exit_state is not None:
        updates["chapter_exit_state"] = outcome.chapter_exit_state.model_copy(
            update={"chapter_number": chapter_number}
        )
    updates["new_events"] = [
        event.model_copy(update={"chapter": chapter_number}) for event in outcome.new_events
    ]
    updates["character_state_deltas"] = [
        char_delta.model_copy(
            update={
                "to_state": char_delta.to_state.model_copy(
                    update={"last_seen_chapter": chapter_number}
                )
            }
        )
        for char_delta in outcome.character_state_deltas
    ]
    updates["relationship_deltas"] = [
        rel_delta.model_copy(
            update={
                "relationship": rel_delta.relationship.model_copy(
                    update={"last_updated_chapter": chapter_number}
                )
            }
        )
        for rel_delta in outcome.relationship_deltas
    ]
    updates["plot_thread_deltas"] = [
        thread_delta.model_copy(
            update={
                "thread": thread_delta.thread.model_copy(
                    update={"last_touched_chapter": chapter_number}
                )
            }
        )
        for thread_delta in outcome.plot_thread_deltas
    ]
    return outcome.model_copy(update=updates)


def _persist_extracted_outcome_artifacts(
    runner: Any,
    bundle: Any,
    outcome: Any,
    chapter_number: int,
) -> None:
    if outcome.creative_report is not None:
        runner._storage.save_json(
            bundle.layout.creative_report_path(chapter_number),
            outcome.creative_report.model_dump(mode="json"),
        )
    if outcome.chapter_exit_state is not None:
        runner._storage.save_json(
            bundle.layout.chapter_exit_state_path(chapter_number),
            outcome.chapter_exit_state.model_dump(mode="json"),
        )
    runner._on_step(
        "extract_canon",
        {"source_chapter": outcome.source_chapter, "summary": outcome.chapter_summary},
    )
    runner._on_step("creative_report", outcome.creative_report)


def _validate_report_hashes(
    storage: Any,
    layout: Any,
    chapter_number: int,
    expected_hash: str,
) -> None:
    """Ensure persisted reports are either current or explicitly marked stale.

    Raises:
        RuntimeError: If any report's source_text_hash does not match expected_hash
            and the report was not explicitly marked stale for that expected hash.
    """
    reports = [
        ("alignment", layout.alignment_report_path(chapter_number)),
        ("continuity", layout.continuity_report_path(chapter_number)),
        ("causal", layout.chapter_causal_report_path(chapter_number)),
    ]
    mismatches: list[str] = []
    for name, path in reports:
        if not path.exists():
            continue
        try:
            data = storage.load_json(path)
        except Exception:
            continue
        report_hash = str(data.get("source_text_hash", "") or "").strip()
        stale_expected = str(data.get("stale_expected_text_hash", "") or "").strip()
        explicitly_stale = bool(data.get("stale_after_text_change")) and (
            stale_expected == expected_hash
        )
        if report_hash and report_hash != expected_hash and not explicitly_stale:
            mismatches.append(name)
    if mismatches:
        raise RuntimeError(
            f"Report hash mismatch for chapter {chapter_number}: "
            f"{', '.join(mismatches)} reports are based on old text. "
            f"Expected {expected_hash[:16]}..."
        )


def _chapter_report_hash_paths(layout: Any, chapter_number: int) -> tuple[tuple[str, Any], ...]:
    return (
        ("alignment", layout.alignment_report_path(chapter_number)),
        ("eval", layout.eval_report_path(chapter_number)),
        ("continuity", layout.continuity_report_path(chapter_number)),
        ("causal", layout.chapter_causal_report_path(chapter_number)),
        ("guard", layout.guard_report_path(chapter_number)),
    )


def _sync_or_mark_report_hashes(
    runner: Any,
    layout: Any,
    chapter_number: int,
    expected_hash: str,
    *,
    allow_restamp: bool,
    stale_reason: str,
) -> None:
    """Refresh report hashes only when the text change is deterministic.

    LLM-authored archive-time changes must not make old quality reports appear
    fresh. Those reports are marked stale unless they were explicitly re-run and
    already carry the expected hash.
    """
    for dimension, path in _chapter_report_hash_paths(layout, chapter_number):
        if not path.exists():
            continue
        try:
            payload = runner._storage.load_json(path)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        current_hash = str(payload.get("source_text_hash", "") or "").strip()
        if current_hash == expected_hash:
            if payload.pop("stale_after_text_change", None) is not None:
                payload.pop("stale_source_text_hash", None)
                payload.pop("stale_expected_text_hash", None)
                payload.pop("stale_reason", None)
                runner._storage.save_json(path, payload)
            continue
        if allow_restamp:
            payload["source_text_hash"] = expected_hash
            payload.pop("stale_after_text_change", None)
            payload.pop("stale_source_text_hash", None)
            payload.pop("stale_expected_text_hash", None)
            payload.pop("stale_reason", None)
        else:
            payload["stale_after_text_change"] = True
            payload["stale_source_text_hash"] = current_hash
            payload["stale_expected_text_hash"] = expected_hash
            payload["stale_reason"] = stale_reason
            payload["dimension"] = payload.get("dimension", dimension)
        runner._storage.save_json(path, payload)


def _finalize_report_hash_transaction(
    runner: Any,
    layout: Any,
    chapter_number: int,
    expected_hash: str,
    *,
    allow_restamp: bool,
    stale_reason: str,
) -> None:
    """Synchronize/mark report hashes, then validate before canon writes."""
    _sync_or_mark_report_hashes(
        runner,
        layout,
        chapter_number,
        expected_hash,
        allow_restamp=allow_restamp,
        stale_reason=stale_reason,
    )
    _validate_report_hashes(
        runner._storage,
        layout,
        chapter_number,
        expected_hash,
    )


async def _refresh_quality_reports_after_text_change(
    runner: Any,
    bundle: Any,
    packet: Any,
    bridge: Any,
    plan: Any,
    current_text: str,
    chapter_number: int,
    trace: Any,
    *,
    causal_report: CausalValidationReport | None,
) -> tuple[Any, Any, CausalValidationReport | None]:
    """Re-run hard quality reports after an archive-time semantic text change."""
    refreshed = await refresh_quality_reports_after_semantic_text_change(
        runner=runner,
        bundle=bundle,
        packet=packet,
        bridge=bridge,
        plan=plan,
        current_text=current_text,
        chapter_number=chapter_number,
        trace=trace,
        alignment_report=None,
        continuity_report=None,
        causal_report=causal_report,
        stale_reason="semantic_text_changed_before_archive",
    )
    return (
        refreshed.alignment_report,
        refreshed.continuity_report,
        refreshed.causal_report,
    )
