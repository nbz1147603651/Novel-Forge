"""MacroGuardStep — multi-chapter trajectory audit before finalize."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from novel_forge.core.constants import TaskType
from novel_forge.core.schemas.chapter import (
    MacroGuardAdjustmentPlan,
    MacroGuardDimensions,
    MacroGuardFinding,
    MacroGuardReport,
)
from novel_forge.core.schemas.outline import StoryOutline
from novel_forge.core.utils.text_hash import source_text_hash
from novel_forge.pipeline.steps.base import PipelineStep

_logger = logging.getLogger(__name__)


@dataclass
class MacroGuardInput:
    """Input payload for macro-level guard audit."""

    chapter_number: int
    audit_entries: list[dict[str, Any]]
    outline: StoryOutline
    canon_state: dict[str, Any]
    settings: Any
    adjustment_state: dict[str, Any] = field(default_factory=dict)
    kernel_context: dict[str, Any] | None = None
    """StoryKernel field slices from ContextComposer (merged into prompt context)."""


class MacroGuardStep(PipelineStep[MacroGuardInput, MacroGuardReport]):
    """Audit recent chapter trajectory for macro-level drift from outline."""

    _DIMENSION_WEIGHTS = {
        "outline_alignment": 0.25,
        "character_arc_consistency": 0.20,
        "pacing_curve": 0.20,
        "foreshadowing_recovery": 0.15,
        "thematic_cohesion": 0.20,
    }
    _ACTION_PRIORITY = {
        "pass": 0,
        "warning_hint": 1,
        "alert_plan": 2,
        "critical_rollback": 3,
    }

    @property
    def step_name(self) -> str:
        return "macro_guard_audit"

    async def _execute(self, input_data: MacroGuardInput) -> MacroGuardReport:
        chapters_audited = self._normalize_chapter_numbers(
            [e.get("chapter_number") for e in input_data.audit_entries]
        )
        outline_window = self._select_outline_window(
            input_data.outline,
            chapters_audited,
        )
        raw_plot_threads = input_data.canon_state.get("plot_threads", [])
        if isinstance(raw_plot_threads, dict):
            plot_thread_values = list(raw_plot_threads.values())
        elif isinstance(raw_plot_threads, list):
            plot_thread_values = raw_plot_threads
        else:
            plot_thread_values = []

        def _plot_thread_brief(thread: Any) -> dict[str, str]:
            if isinstance(thread, dict):
                return {
                    "thread_id": str(thread.get("thread_id", "") or ""),
                    "title": str(thread.get("title", "") or ""),
                }
            return {
                "thread_id": str(getattr(thread, "thread_id", "") or ""),
                "title": str(getattr(thread, "title", "") or ""),
            }

        canon_stats = {
            "character_count": len(input_data.canon_state.get("characters", {})),
            "foreshadowing_count": len(input_data.canon_state.get("foreshadowing", [])),
            "world_facts_count": len(input_data.canon_state.get("world_facts", {})),
            "active_threads": [_plot_thread_brief(t) for t in plot_thread_values],
        }

        context = {
            "chapters_audited": chapters_audited,
            "audit_entries": input_data.audit_entries,
            "outline": {
                "synopsis": input_data.outline.synopsis,
                "total_chapters": input_data.outline.total_chapters,
                "chapters": outline_window,
            },
            "canon_stats": canon_stats,
        }
        # Merge kernel_context with prefixed keys to avoid collision with
        # existing context keys (outline, canon_stats, etc.).
        if input_data.kernel_context is not None:
            for key, value in input_data.kernel_context.items():
                prefixed = f"kernel_{key}"
                if prefixed not in context:
                    context[prefixed] = value
        request = self._builder.build(
            TaskType.MACRO_GUARD_AUDIT,
            context,
            max_tokens=self._dynamic_max_tokens(
                TaskType.MACRO_GUARD_AUDIT,
                max(1600, len(input_data.audit_entries) * 350),
                prompt_overhead=2600,
                min_tokens=1024,
                max_cap=getattr(input_data.settings, "long_macro_guard_max_tokens", 2048),
            ),
            temperature=getattr(input_data.settings, "temp_macro_guard", 0.2),
        )
        try:
            from novel_forge.pipeline.long.services.generation import llm_helpers as llm_h

            parsed = await llm_h.route_json_object_with_retry(
                self._router,
                request,
                task_type=TaskType.MACRO_GUARD_AUDIT,
                context=context,
            )
        except Exception as exc:
            _logger.warning(
                "macro_guard_parse_failed | chapter=%d | reason=%s",
                input_data.chapter_number,
                exc,
            )
            return self._fallback_report(input_data)

        return self._build_report(parsed, input_data)

    @staticmethod
    def _normalize_chapter_numbers(raw: list[Any]) -> list[int]:
        numbers: list[int] = []
        seen: set[int] = set()
        for item in raw:
            try:
                chapter = int(item)
            except (TypeError, ValueError):
                continue
            if chapter <= 0 or chapter in seen:
                continue
            seen.add(chapter)
            numbers.append(chapter)
        return numbers

    @staticmethod
    def _select_outline_window(
        outline: StoryOutline,
        chapters_audited: list[int],
    ) -> list[dict[str, Any]]:
        chapter_map = {ch.chapter_number: ch for ch in outline.chapters}
        selected: list[dict[str, Any]] = []
        for chapter_number in chapters_audited:
            ch = chapter_map.get(chapter_number)
            if ch is None:
                continue
            selected.append(
                {
                    "chapter_number": ch.chapter_number,
                    "title": ch.title,
                    "goal": ch.goal,
                    "beats_summary": ch.beats_summary,
                }
            )
        return selected

    @staticmethod
    def _safe_float(value: Any, *, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _clamp_unit(value: float) -> float:
        return max(0.0, min(1.0, value))

    @classmethod
    def _action_from_thresholds(cls, drift_score: float, settings: Any) -> str:
        warning = cls._clamp_unit(
            cls._safe_float(
                getattr(settings, "long_macro_guard_drift_threshold_warning", 0.3),
                default=0.3,
            )
        )
        alert = cls._clamp_unit(
            cls._safe_float(
                getattr(settings, "long_macro_guard_drift_threshold_alert", 0.5),
                default=0.5,
            )
        )
        critical = cls._clamp_unit(
            cls._safe_float(
                getattr(settings, "long_macro_guard_drift_threshold_critical", 0.7),
                default=0.7,
            )
        )
        # Ensure monotonic thresholds: warning <= alert <= critical.
        alert = max(alert, warning)
        critical = max(critical, alert)

        if drift_score >= critical:
            return "critical_rollback"
        if drift_score >= alert:
            return "alert_plan"
        if drift_score >= warning:
            return "warning_hint"
        return "pass"

    @classmethod
    def _prefer_higher_risk_action(cls, action_a: str, action_b: str) -> str:
        pa = cls._ACTION_PRIORITY.get(action_a, 0)
        pb = cls._ACTION_PRIORITY.get(action_b, 0)
        return action_a if pa >= pb else action_b

    @classmethod
    def _apply_adjustment_limits(
        cls,
        *,
        candidate_action: str,
        chapter_number: int,
        adjustment_state: dict[str, Any],
        settings: Any,
    ) -> tuple[str, str]:
        if candidate_action != "alert_plan":
            return candidate_action, ""

        max_adjustments = int(
            getattr(settings, "long_macro_guard_max_adjustments_per_book", 3) or 0
        )
        min_gap = int(
            getattr(settings, "long_macro_guard_min_chapters_between_adjustments", 8) or 0
        )
        applied_adjustments = int(adjustment_state.get("applied_adjustments", 0) or 0)
        last_adjustment_chapter = int(adjustment_state.get("last_adjustment_chapter", 0) or 0)

        if max_adjustments <= 0:
            return "warning_hint", "alert_plan downgraded: adjustments disabled by config"
        if applied_adjustments >= max_adjustments:
            return (
                "warning_hint",
                "alert_plan downgraded: reached max adjustments per book",
            )
        if (
            last_adjustment_chapter > 0
            and min_gap > 0
            and (chapter_number - last_adjustment_chapter) < min_gap
        ):
            return (
                "warning_hint",
                "alert_plan downgraded: minimum chapter gap between adjustments not met",
            )
        return candidate_action, ""

    @classmethod
    def _normalize_recommended_action(
        cls,
        *,
        llm_action: str,
        drift_score: float,
        chapter_number: int,
        adjustment_state: dict[str, Any],
        settings: Any,
    ) -> tuple[str, str]:
        llm_action = str(llm_action or "").strip().lower()
        if llm_action not in cls._ACTION_PRIORITY:
            llm_action = "pass"

        threshold_action = cls._action_from_thresholds(drift_score, settings)
        merged_action = cls._prefer_higher_risk_action(llm_action, threshold_action)
        limited_action, note = cls._apply_adjustment_limits(
            candidate_action=merged_action,
            chapter_number=chapter_number,
            adjustment_state=adjustment_state,
            settings=settings,
        )
        return limited_action, note

    def _build_report(self, data: dict[str, Any], input_data: MacroGuardInput) -> MacroGuardReport:
        dim_data = data.get("dimensions", {})
        dimensions = MacroGuardDimensions(
            outline_alignment=self._clamp_unit(
                self._safe_float(dim_data.get("outline_alignment", 1.0), default=1.0)
            ),
            character_arc_consistency=self._clamp_unit(
                self._safe_float(
                    dim_data.get("character_arc_consistency", 1.0),
                    default=1.0,
                )
            ),
            pacing_curve=self._clamp_unit(
                self._safe_float(dim_data.get("pacing_curve", 1.0), default=1.0)
            ),
            foreshadowing_recovery=self._clamp_unit(
                self._safe_float(dim_data.get("foreshadowing_recovery", 1.0), default=1.0)
            ),
            thematic_cohesion=self._clamp_unit(
                self._safe_float(dim_data.get("thematic_cohesion", 1.0), default=1.0)
            ),
        )

        drift_score = self._compute_drift_score(dimensions)
        recommended_action, action_note = self._normalize_recommended_action(
            llm_action=str(data.get("recommended_action", "pass")),
            drift_score=drift_score,
            chapter_number=input_data.chapter_number,
            adjustment_state=input_data.adjustment_state,
            settings=input_data.settings,
        )

        findings_data = data.get("findings", [])
        findings: list[MacroGuardFinding] = []
        for f in findings_data:
            if isinstance(f, dict):
                findings.append(
                    MacroGuardFinding(
                        severity=str(f.get("severity", "medium")),
                        type=str(f.get("type", "")),
                        description=str(f.get("description", "")),
                        evidence=[str(e) for e in f.get("evidence", []) if e],
                    )
                )

        adj_data = data.get("adjustment_plan")
        adjustment_plan: MacroGuardAdjustmentPlan | None = None
        if isinstance(adj_data, dict):
            adjustment_plan = MacroGuardAdjustmentPlan(
                window_size=int(adj_data.get("window_size", 0)),
                strategy=str(adj_data.get("strategy", "")),
                target_outline_v=str(adj_data.get("target_outline_v", "")),
                adjusted_chapter_goals=[
                    dict(g)
                    for g in adj_data.get("adjusted_chapter_goals", [])
                    if isinstance(g, dict)
                ],
                reasoning=str(adj_data.get("reasoning", "")),
            )
        if recommended_action in {"pass", "warning_hint"}:
            adjustment_plan = None

        reasoning = str(data.get("reasoning", "")).strip()
        if action_note:
            reasoning = f"{reasoning} [{action_note}]".strip()

        return MacroGuardReport(
            audit_id=f"macro_{input_data.chapter_number}",
            chapters_audited=[e.get("chapter_number", 0) for e in input_data.audit_entries],
            drift_score=round(drift_score, 3),
            dimensions=dimensions,
            findings=findings,
            recommended_action=recommended_action,
            adjustment_plan=adjustment_plan,
            confidence=self._clamp_unit(self._safe_float(data.get("confidence", 0.0), default=0.0)),
            reasoning=reasoning,
            source_text_hash=source_text_hash(str(data)),
        )

    def _compute_drift_score(self, dimensions: MacroGuardDimensions) -> float:
        total = 0.0
        for key, weight in self._DIMENSION_WEIGHTS.items():
            total += getattr(dimensions, key, 1.0) * weight
        return 1.0 - total

    def _fallback_report(self, input_data: MacroGuardInput) -> MacroGuardReport:
        return MacroGuardReport(
            audit_id=f"macro_{input_data.chapter_number}",
            chapters_audited=[e.get("chapter_number", 0) for e in input_data.audit_entries],
            drift_score=0.0,
            dimensions=MacroGuardDimensions(),
            findings=[],
            recommended_action="pass",
            confidence=0.0,
            reasoning="MacroGuard audit failed to parse LLM response; defaulting to pass.",
            source_text_hash="",
        )
