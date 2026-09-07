"""Generate-stage helpers: metrics, ``GenerateArtifacts``, and ``generate_chapter_prose``.

Extracted from ``chapter_flow.py`` during the Task-17 decomposition.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

from novel_forge.pipeline.long.execution_models import (
    ChapterExecutionContext,
    FlowContextAdapter,
)
from novel_forge.pipeline.long.preflight import LongProjectBundle
from novel_forge.pipeline.long.stages.draft import (
    DraftArtifacts,
    GenerateContext,
    generate_draft,
    prepare_generate_context,
)
from novel_forge.pipeline.long.stages.wave import WovenArtifacts, apply_wave

_logger = logging.getLogger(__name__)


def _metric_float(value: Any) -> float | None:
    """Return a stable float for telemetry payloads without leaking model objects."""
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _metric_int(value: Any, default: int = 0) -> int:
    """Return a stable integer for telemetry payloads."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _metric_field(source: Any, field_name: str, default: Any = None) -> Any:
    """Read a metric field from either a schema object or a dict."""
    if source is None:
        return default
    if isinstance(source, dict):
        return source.get(field_name, default)
    return getattr(source, field_name, default)


def _metric_issue_count(report: Any) -> int:
    """Count report issues for repair telemetry."""
    issues = _metric_field(report, "issues", [])
    if isinstance(issues, (list, tuple, set)):
        return len(issues)
    return 0


def _metric_list_count(source: Any, field_name: str) -> int:
    """Count list-like fields on schema objects or dict payloads."""
    values = _metric_field(source, field_name, [])
    if isinstance(values, (list, tuple, set)):
        return len(values)
    return 0


def _metric_bool(value: Any) -> bool:
    """Normalize bool-like telemetry fields."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _report_text_hash_consistency(
    *,
    current_text: str,
    reports: dict[str, Any | None],
) -> dict[str, Any]:
    expected = ""
    if current_text:
        try:
            from novel_forge.core.review.review_contracts import source_text_hash

            expected = source_text_hash(current_text)
        except Exception:
            expected = ""
    items: dict[str, Any] = {}
    mismatched: list[str] = []
    for name, report in reports.items():
        stored = str(_metric_field(report, "source_text_hash", "") or "").strip()
        matched = bool(expected and stored and stored == expected)
        if stored and expected and not matched:
            mismatched.append(name)
        items[name] = {
            "present": report is not None,
            "source_text_hash": stored,
            "matches_current_text": matched if stored else None,
        }
    return {
        "current_text_hash": expected,
        "mismatched_reports": mismatched,
        "all_present_hashes_match": not mismatched,
        "reports": items,
    }


def _chapter_repair_check_metrics(report: Any | None) -> dict[str, Any]:
    """Summarize a ChapterRepairReport without serializing full findings."""
    if report is None:
        return {
            "ran": False,
            "issues_found": False,
            "issue_count": 0,
        }

    issue_fields = (
        "prompt_leaks",
        "factual_errors",
        "continuity_errors",
        "expression_errors",
        "forbidden_element_candidates",
        "forbidden_element_findings",
        "repair_tickets",
    )
    counts = {field: _metric_list_count(report, field) for field in issue_fields}
    issue_count = sum(counts.values())
    return {
        "ran": True,
        "issues_found": issue_count > 0,
        "issue_count": issue_count,
        "risk_level": str(_metric_field(report, "risk_level", "") or ""),
        "review_mode": str(_metric_field(report, "review_mode", "") or ""),
        "repair_actions_count": _metric_list_count(report, "repair_actions"),
        "counts": counts,
    }


def _build_generate_metrics_payload(
    *,
    draft_meta: dict[str, Any] | None,
    wave_meta: dict[str, Any] | None,
    pre_wave_chapter_repair_report: Any | None,
    performed_edits: int,
    writing_mode: str,
) -> dict[str, Any]:
    """Build normalized Generate-stage metrics for repair-rate analysis."""
    draft_meta = draft_meta or {}
    wave_meta = wave_meta or {}
    wave_warnings = list(wave_meta.get("warnings", []) or [])
    cross_ref_hits = list(wave_meta.get("cross_ref_hits", []) or [])
    scene_stitch_report = draft_meta.get("scene_stitch_report")
    draft_plan_coverage = draft_meta.get("draft_plan_coverage")
    return {
        "writing_mode": writing_mode,
        "performed_edits": int(performed_edits or 0),
        "draft": {
            "text_chars": int(draft_meta.get("text_chars") or 0),
            "scene_stitch_report_available": scene_stitch_report is not None,
            "plan_coverage_report_available": draft_plan_coverage is not None,
            "weak_anchor_count": _metric_int(
                _metric_field(draft_plan_coverage, "weak_anchor_count", 0)
            ),
        },
        "wave": {
            "warnings_count": len(wave_warnings),
            "warnings": wave_warnings[:12],
            "cross_ref_hit_count": len(cross_ref_hits),
            "cross_ref_total": int(wave_meta.get("cross_ref_total") or 0),
            "cross_ref_hits": cross_ref_hits[:24],
            "scenes_woven": int(wave_meta.get("scenes_woven") or 0),
            "scene_anchor_total": int(wave_meta.get("scene_anchor_total") or 0),
            "woven_chars": int(wave_meta.get("woven_chars") or 0),
            "final_word_count": int(wave_meta.get("final_word_count") or 0),
        },
        "pre_wave_chapter_check": _chapter_repair_check_metrics(pre_wave_chapter_repair_report),
    }


def _build_repair_metrics_payload(
    *,
    chapter_number: int,
    continuity_result: Any,
    continuity_report: Any,
    causal_result: Any | None,
    causal_report: Any | None,
    reading_power_result: Any | None,
    total_rounds_used: int,
    total_rounds_cap: int,
    cumulative_change_ratio: float,
    repair_exhausted: bool,
    skipped_quality_stage: bool,
    draft_meta: dict[str, Any] | None = None,
    wave_meta: dict[str, Any] | None = None,
    pre_wave_chapter_repair_report: Any | None = None,
    performed_edits: int = 0,
    writing_mode: str = "",
    wave_integrity: dict[str, Any] | None = None,
    chapter_quality_repair: dict[str, Any] | None = None,
    current_text: str = "",
    eval_report: Any | None = None,
    text_change_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build aggregate repair metrics for logs and Desktop observability."""

    def _dimension_payload(
        *,
        result: Any | None,
        report: Any | None,
        score_field: str,
        attempted: bool,
        applied: bool | None = None,
        rolled_back: bool | None = None,
    ) -> dict[str, Any]:
        rounds_used = int(_metric_field(result, "rounds_used", 0) or 0)
        exhausted = bool(_metric_field(result, "repair_exhausted", False))
        best_effort = bool(_metric_field(result, "best_effort_accepted", False))
        applied_value = _metric_bool(_metric_field(result, "applied", False))
        rolled_back_value = _metric_bool(_metric_field(result, "rolled_back", False))
        if applied is not None:
            applied_value = applied
        if rolled_back is not None:
            rolled_back_value = rolled_back
        return {
            "attempted": attempted,
            "rounds_used": rounds_used,
            "applied": applied_value,
            "rolled_back": rolled_back_value,
            "repair_exhausted": exhausted,
            "best_effort_accepted": best_effort,
            "needs_human_review": bool(_metric_field(result, "needs_human_review", False)),
            "score_after": _metric_float(_metric_field(report, score_field)),
            "issue_count_after": _metric_issue_count(report),
            "settled": attempted and not exhausted and not best_effort,
        }

    rp_report = _metric_field(reading_power_result, "report")
    continuity_repair = _metric_field(continuity_result, "continuity_repair")
    dimensions = {
        "continuity": _dimension_payload(
            result=continuity_result,
            report=continuity_report,
            score_field="continuity_score",
            attempted=not skipped_quality_stage,
            applied=_metric_bool(
                _metric_field(
                    continuity_result,
                    "applied",
                    _metric_field(continuity_repair, "applied", False),
                )
            ),
            rolled_back=bool(_metric_field(continuity_result, "rollback_history", []) or [])
            or _metric_bool(_metric_field(continuity_result, "rolled_back", False)),
        ),
        "causal": _dimension_payload(
            result=causal_result,
            report=causal_report,
            score_field="causal_score",
            attempted=causal_result is not None,
        ),
        "reading_power": _dimension_payload(
            result=reading_power_result,
            report=rp_report,
            score_field="overall_score",
            attempted=reading_power_result is not None
            and not skipped_quality_stage
            and rp_report is not None,
        ),
    }
    chapter_quality_rounds = int(_metric_field(chapter_quality_repair or {}, "rounds_used", 0) or 0)
    any_loop_entered = any(int(item["rounds_used"]) > 0 for item in dimensions.values()) or (
        chapter_quality_rounds > 0
    )
    any_text_changed = any(bool(item["applied"]) for item in dimensions.values()) or _metric_bool(
        _metric_field(chapter_quality_repair or {}, "text_changed", False)
    )
    any_rolled_back = any(bool(item["rolled_back"]) for item in dimensions.values())
    return {
        "schema_version": 2,
        "chapter": chapter_number,
        "generate": _build_generate_metrics_payload(
            draft_meta=draft_meta,
            wave_meta=wave_meta,
            pre_wave_chapter_repair_report=pre_wave_chapter_repair_report,
            performed_edits=performed_edits,
            writing_mode=writing_mode,
        ),
        "total_rounds_used": int(total_rounds_used),
        "total_rounds_cap": int(total_rounds_cap),
        "cumulative_change_ratio": round(float(cumulative_change_ratio or 0.0), 4),
        "repair_exhausted": bool(repair_exhausted),
        "skipped_quality_stage": bool(skipped_quality_stage),
        "post_wave_repair": {
            "any_loop_entered": any_loop_entered,
            "any_text_changed": any_text_changed,
            "any_rolled_back": any_rolled_back,
        },
        "wave_integrity": dict(wave_integrity or {}),
        "chapter_quality_repair": dict(chapter_quality_repair or {}),
        "text_change_history": list(text_change_history or []),
        "report_text_hash_consistency": _report_text_hash_consistency(
            current_text=current_text,
            reports={
                "eval": eval_report,
                "continuity": continuity_report,
                "causal": causal_report,
                "reading_power": rp_report,
            },
        ),
        "dimensions": dimensions,
    }


def _persist_repair_metrics_report(
    *,
    context: ChapterExecutionContext,
    bundle: LongProjectBundle,
    chapter_number: int,
    payload: dict[str, Any],
    on_step: Any,
) -> None:
    """Persist normalized repair metrics without blocking chapter completion."""
    path_fn = getattr(bundle.layout, "repair_metrics_report_path", None)
    if not callable(path_fn):
        return
    try:
        context.storage.save_json(path_fn(chapter_number), payload)
    except Exception as exc:
        _logger.warning(
            "repair_metrics_report_persist_failed | chapter=%d | %s",
            chapter_number,
            exc,
        )
        on_step(
            "repair_metrics_report_persist_failed",
            {"chapter": chapter_number, "error": str(exc)},
        )


@dataclasses.dataclass(frozen=True)
class GenerateArtifacts:
    """Result of the Generate phase (DRAFT + WAVE).

    Carries the woven prose from WAVE, generation metadata, and the optional
    pre-WAVE chapter check report used by Review telemetry downstream.
    """

    current_text: str
    performed_edits: int
    wave_meta: dict[str, Any]
    draft_meta: dict[str, Any] = dataclasses.field(default_factory=dict)
    chapter_repair_report: Any | None = None


async def generate_chapter_prose(
    runner: Any,
    bundle: Any,
    packet: Any,
    bridge: Any,
    plan: Any,
    chapter_number: int,
    trace: Any,
    *,
    planning_hints: dict[str, Any] | None = None,
    reading_power_hint: dict[str, Any] | None = None,
) -> GenerateArtifacts:
    """Generate phase: prepare shared context -> DRAFT -> WAVE.

    The 6-phase long-form pipeline splits generation into two stages:
      1. DRAFT (per-scene prose from the approved plan, focused intent, no voices)
      2. WAVE  (single-pass scene weaving, full plan, full voices, cross-scene intent)

    Both stages share a :class:`GenerateContext` so kernel state, memory hints,
    canon context, and prompt cards are computed once and reused.  DRAFT and
    WAVE each build their own stage_cards so each model sees only the data it
    actually needs.
    """
    runner = FlowContextAdapter(runner)
    ctx: GenerateContext = await prepare_generate_context(
        runner=runner,
        bundle=bundle,
        packet=packet,
        bridge=bridge,
        plan=plan,
        chapter_number=chapter_number,
        trace=trace,
        planning_hints=planning_hints,
        reading_power_hint=reading_power_hint,
    )
    draft_art: DraftArtifacts = await generate_draft(ctx)
    woven_art: WovenArtifacts = await apply_wave(
        ctx,
        draft_art.current_text,
        scene_stitch_report=draft_art.draft_meta.get("scene_stitch_report")
        if isinstance(draft_art.draft_meta, dict)
        else None,
        draft_plan_coverage=draft_art.draft_meta.get("draft_plan_coverage")
        if isinstance(draft_art.draft_meta, dict)
        else None,
    )
    return GenerateArtifacts(
        current_text=woven_art.current_text,
        performed_edits=woven_art.performed_edits,
        wave_meta=woven_art.wave_meta,
        draft_meta=draft_art.draft_meta,
        chapter_repair_report=draft_art.chapter_repair_report,
    )
