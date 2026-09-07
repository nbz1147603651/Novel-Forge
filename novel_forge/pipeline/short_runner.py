"""ShortStoryRunner — orchestrates the short-mode pipeline end-to-end."""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from novel_forge.core.config import Settings
from novel_forge.core.constants import TaskType
from novel_forge.core.schemas.beats import StoryBeats
from novel_forge.core.schemas.blueprint_elements import BlueprintElementSelection
from novel_forge.core.schemas.eval_schema import EvalReport
from novel_forge.core.schemas.short_blueprint import ShortBlueprint
from novel_forge.core.schemas.short_creative import ShortCreativeSummary
from novel_forge.core.schemas.spec import StorySpec
from novel_forge.core.user_intent import build_story_spec_user_intent_card
from novel_forge.core.utils.text_hash import source_text_hash
from novel_forge.core.utils.text_validation import count_chapter_words
from novel_forge.gateway.router import ModelRouter
from novel_forge.obs.logger import get_logger
from novel_forge.obs.tracer import PipelineTrace
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.artifact_manifest import ArtifactManifest
from novel_forge.pipeline.finalization_manifest import (
    matching_finalization_phase,
    record_finalization_pending,
    record_finalization_success,
)
from novel_forge.pipeline.repair_orchestration.domains.short_story import (
    ShortCandidateRepairOutcome,
    run_short_story_candidate_repair,
    run_short_story_repair_v2,
)
from novel_forge.pipeline.short._shared import (
    build_checkpoint_payload,
    checkpoint_fingerprint,
    clean_text,
    remove_path,
    short_meta_path,
    unique_texts,
)
from novel_forge.pipeline.short.quality_gate import ShortQualityGateThresholds
from novel_forge.pipeline.short.stages.draft import (
    _ensure_style_profile,
    _load_style_profile,
    run_initial_draft,
)
from novel_forge.pipeline.short.stages.edit import (
    check_short_story_completeness,
    collect_short_quality_repair_issues,
    eval_flags_incomplete_story,
    run_edit_loop,
)
from novel_forge.pipeline.short.stages.evaluate import run_final_evaluation
from novel_forge.pipeline.short.stages.plan import (
    blueprint_is_degraded,
    prepare_short_research,
    resolve_blueprint_elements,
    run_beats,
    run_blueprint,
    run_spec,
)
from novel_forge.pipeline.steps.blueprint_element_select_step import (
    has_manual_selector_preferences,
)
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.story_kernel.state_writer import StoryKernelStateWriter
from novel_forge.story_kernel.store import StoryKernelStore

_log = get_logger("pipeline.short")
_SHORT_SPEC_KEYS = (
    "theme",
    "genre",
    "tone",
    "length_target",
    "title",
    "language",
    "characters_hint",
    "world_hint",
    "conflict_hint",
    "pov_hint",
    "opening_style",
    "ending_style",
    "extra_instructions",
)
ShortWritingMode = Literal["auto", "whole_chapter", "scene_level"]


def _normalize_short_writing_mode(value: str | None) -> ShortWritingMode:
    if value == "scene_level":
        return "scene_level"
    if value == "whole_chapter":
        return "whole_chapter"
    return "auto"


@dataclass
class ShortStoryResult:
    """Final output of a short story generation run."""

    spec: StorySpec
    blueprint: ShortBlueprint | None
    beats: StoryBeats
    final_text: str
    eval_report: EvalReport
    creative_summary: ShortCreativeSummary | None = None
    warnings: list[str] = field(default_factory=list)
    trace_summary: dict[str, Any] = field(default_factory=dict)


class ShortStoryRunner:
    """Spec → Blueprint → Beats → Draft → Revision → Evaluate → Analysis → Export."""

    def __init__(
        self,
        router: ModelRouter,
        builder: PromptBuilder,
        storage: FileSystemStorage,
        *,
        settings: Settings,
        max_edit_rounds: int = 2,
        writing_mode: str | None = None,
        on_step_progress: Callable[[str, Any], None] | None = None,
    ) -> None:
        self._router = router
        self._builder = builder
        self._storage = storage
        self._settings = settings
        self._max_edit = max_edit_rounds
        self._writing_mode = _normalize_short_writing_mode(writing_mode)
        self._trace = PipelineTrace()
        self._on_step = on_step_progress or (lambda s, d: None)
        self._user_intent: dict[str, Any] = {}
        self._research_evidence_pack: dict[str, Any] = {}
        self._research_uncertainty: tuple[str, ...] = ()

    @classmethod
    def from_settings(
        cls,
        router: ModelRouter,
        builder: PromptBuilder,
        storage: FileSystemStorage,
        settings: Settings,
        *,
        max_edit_rounds: int | None = None,
        writing_mode: str | None = None,
        on_step_progress: Callable[[str, Any], None] | None = None,
    ) -> ShortStoryRunner:
        """Build a short-story runner from centralized settings."""
        return cls(
            router,
            builder,
            storage,
            settings=settings,
            max_edit_rounds=(
                settings.short_max_edit_rounds if max_edit_rounds is None else max_edit_rounds
            ),
            writing_mode=writing_mode,
            on_step_progress=on_step_progress,
        )

    # ── Utility wrappers (backward compat + stage access) ──────────────

    @staticmethod
    def _clean_text(value: Any) -> str:
        return clean_text(value)

    def _load_style_profile(self, layout: ProjectLayout) -> dict[str, Any] | None:
        return _load_style_profile(self, layout)

    def _is_short_option_enabled_for_task(
        self,
        *,
        capability: str,
        enabled: bool,
        allowed_providers_raw: str,
        allowed_models_raw: str,
        task_type: TaskType,
    ) -> bool:
        from novel_forge.pipeline.long.services.generation import llm_helpers as _llm_h

        return _llm_h.is_option_enabled_for_task(
            self._router,
            capability=capability,
            enabled=enabled,
            allowed_providers_raw=allowed_providers_raw,
            allowed_models_raw=allowed_models_raw,
            task_type=task_type,
        )

    @staticmethod
    def _append_warning(warnings: list[str], message: str) -> None:
        if message not in warnings:
            warnings.append(message)

    def _emit_warning(
        self,
        warnings: list[str],
        *,
        step_name: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self._append_warning(warnings, message)
        self._on_step(
            step_name,
            {
                "message": message,
                **(payload or {}),
            },
        )

    # ── Execution plan helpers ─────────────────────────────────────────

    def _build_short_segments(
        self,
        spec: StorySpec,
        beats: StoryBeats,
        execution_plan: dict[str, Any],
        *,
        segment_target_words: int | None,
        segment_max_count: int | None,
    ) -> list[dict[str, Any]]:
        from novel_forge.pipeline.short.stages.draft import _build_short_segments

        return _build_short_segments(
            self,
            spec,
            beats,
            execution_plan,
            segment_target_words=segment_target_words,
            segment_max_count=segment_max_count,
        )

    @staticmethod
    def _build_segment_context_budget(
        segment: dict[str, Any],
        *,
        story_target_words: int,
    ) -> dict[str, int]:
        from novel_forge.pipeline.short.stages.draft import _build_segment_context_budget

        return _build_segment_context_budget(segment, story_target_words=story_target_words)

    @classmethod
    def _apply_segment_history_budget(
        cls,
        messages: list[dict[str, str]],
        *,
        budget_chars: int,
        max_rounds: int,
    ) -> list[dict[str, str]]:
        from novel_forge.pipeline.short.stages.draft import _apply_segment_history_budget

        return _apply_segment_history_budget(
            messages,
            budget_chars=budget_chars,
            max_rounds=max_rounds,
        )

    async def _run_initial_draft(
        self,
        layout: ProjectLayout,
        spec: StorySpec,
        beats: StoryBeats,
        blueprint: ShortBlueprint | None,
        execution_plan: dict[str, Any],
        *,
        segmented_mode: Literal["auto", "on", "off"] | None = None,
        segment_target_words: int | None = None,
        segment_max_count: int | None = None,
    ) -> str:
        from novel_forge.pipeline.short.stages.draft import run_initial_draft

        return await run_initial_draft(
            self,
            layout,
            spec,
            beats,
            blueprint,
            execution_plan,
            segmented_mode=segmented_mode,
            segment_target_words=segment_target_words,
            segment_max_count=segment_max_count,
        )

    def _check_short_story_completeness(
        self,
        text: str,
        spec: StorySpec,
        execution_plan: dict[str, Any],
    ) -> dict[str, Any]:
        from novel_forge.pipeline.short.stages.edit import check_short_story_completeness

        return check_short_story_completeness(self, text, spec, execution_plan)

    @staticmethod
    def _collect_short_quality_repair_issues(eval_report: EvalReport) -> list[str]:

        return collect_short_quality_repair_issues(eval_report)

    def _build_short_quality_gate_thresholds(self) -> ShortQualityGateThresholds:
        from novel_forge.pipeline.short.quality_gate import build_short_quality_gate_thresholds

        return build_short_quality_gate_thresholds(self._settings)

    def _evaluate_short_quality_gate(
        self,
        layout: ProjectLayout,
        eval_report: EvalReport,
        *,
        thresholds: ShortQualityGateThresholds,
        attempted_repair: bool,
        best_effort_accepted: bool,
    ) -> dict[str, Any]:
        from novel_forge.pipeline.short.quality_gate import (
            evaluate_short_quality_gate,
            serialize_short_quality_gate_report,
        )

        report = evaluate_short_quality_gate(eval_report, thresholds)
        payload = serialize_short_quality_gate_report(
            report,
            thresholds,
            attempted_repair=attempted_repair,
            best_effort_accepted=best_effort_accepted,
        )
        self._storage.save_json(layout.short_quality_gate_report_path(), payload)
        return payload

    async def _run_completion_repair(
        self,
        layout: ProjectLayout,
        spec: StorySpec,
        beats: StoryBeats,
        blueprint: ShortBlueprint | None,
        execution_plan: dict[str, Any],
        current_text: str,
        issues: list[str],
        *,
        pass_label: str,
        iteration: int,
        eval_report: EvalReport | None = None,
    ) -> str | ShortCandidateRepairOutcome:
        if (
            eval_report is not None
            and getattr(self._settings, "short_candidate_first_repair_enabled", True)
        ):
            return await run_short_story_candidate_repair(
                runner=self,
                layout=layout,
                spec=spec,
                beats=beats,
                blueprint=blueprint,
                execution_plan=execution_plan,
                current_text=current_text,
                issues=issues,
                repair_kind="completion",
                pass_label=pass_label,
                iteration=iteration,
                baseline_eval=eval_report,
            )
        return await run_short_story_repair_v2(
            runner=self,
            layout=layout,
            spec=spec,
            beats=beats,
            blueprint=blueprint,
            execution_plan=execution_plan,
            current_text=current_text,
            issues=issues,
            repair_kind="completion",
            pass_label=pass_label,
            iteration=iteration,
            eval_report=eval_report,
        )

    async def _run_quality_repair(
        self,
        layout: ProjectLayout,
        spec: StorySpec,
        beats: StoryBeats,
        blueprint: ShortBlueprint | None,
        execution_plan: dict[str, Any],
        current_text: str,
        issues: list[str],
        *,
        pass_label: str,
        iteration: int,
        eval_report: EvalReport,
    ) -> str | ShortCandidateRepairOutcome:

        if getattr(self._settings, "short_candidate_first_repair_enabled", True):
            return await run_short_story_candidate_repair(
                runner=self,
                layout=layout,
                spec=spec,
                beats=beats,
                blueprint=blueprint,
                execution_plan=execution_plan,
                current_text=current_text,
                issues=issues,
                repair_kind="quality",
                pass_label=pass_label,
                iteration=iteration,
                baseline_eval=eval_report,
            )
        return await run_short_story_repair_v2(
            runner=self,
            layout=layout,
            spec=spec,
            beats=beats,
            blueprint=blueprint,
            execution_plan=execution_plan,
            current_text=current_text,
            issues=issues,
            repair_kind="quality",
            pass_label=pass_label,
            iteration=iteration,
            eval_report=eval_report,
        )

    async def _run_legacy_revision_flow(
        self,
        layout: ProjectLayout,
        spec: StorySpec,
        beats: StoryBeats,
        blueprint: ShortBlueprint | None,
        execution_plan: dict[str, Any],
        current_text: str,
        warnings: list[str],
    ) -> tuple[str, EvalReport, ShortCreativeSummary | None]:
        """Preserve the pre-adaptive short revision path behind its rollout flag."""

        current_text = await run_edit_loop(
            self,
            layout,
            spec,
            beats,
            blueprint,
            execution_plan,
            current_text,
        )
        eval_report = await run_final_evaluation(
            self,
            layout,
            spec,
            beats,
            blueprint,
            execution_plan,
            current_text,
            require_intent_compliance=True,
        )

        async def adopt_repair(
            result: str | ShortCandidateRepairOutcome,
            prior_text: str,
            prior_eval: EvalReport,
        ) -> tuple[str, EvalReport]:
            if isinstance(result, ShortCandidateRepairOutcome):
                if result.applied:
                    self._storage.save_json(
                        layout.eval_report_path(), result.eval_report.model_dump(mode="json")
                    )
                    self._on_step(
                        "short_repair_candidate_adopted",
                        {
                            "case_id": result.case_id,
                            "source_text_hash": result.eval_report.source_text_hash,
                        },
                    )
                return result.text, result.eval_report
            repaired_text = str(result)
            if repaired_text == prior_text:
                return repaired_text, prior_eval
            repaired_eval = await run_final_evaluation(
                self,
                layout,
                spec,
                beats,
                blueprint,
                execution_plan,
                repaired_text,
                require_intent_compliance=True,
            )
            return repaired_text, repaired_eval

        completeness_check = check_short_story_completeness(
            self, current_text, spec, execution_plan
        )
        self._on_step("short_completeness_check", completeness_check)
        if not completeness_check["passed"]:
            completion_result = await self._run_completion_repair(
                layout,
                spec,
                beats,
                blueprint,
                execution_plan,
                current_text,
                completeness_check["issues"],
                pass_label="short_completion_repair",
                iteration=self._max_edit + 1,
                eval_report=eval_report,
            )
            current_text, eval_report = await adopt_repair(
                completion_result, current_text, eval_report
            )
        next_repair_iteration = self._max_edit + 2
        if eval_flags_incomplete_story(eval_report):
            completion_result = await self._run_completion_repair(
                layout,
                spec,
                beats,
                blueprint,
                execution_plan,
                current_text,
                ["评估认为开头、结尾或整体收束仍不够完整，需补成完整短篇。"],
                pass_label="short_completion_repair_after_eval",
                iteration=next_repair_iteration,
                eval_report=eval_report,
            )
            next_repair_iteration += 1
            current_text, eval_report = await adopt_repair(
                completion_result, current_text, eval_report
            )

        if getattr(self._settings, "short_quality_gate_enabled", True):
            quality_gate_thresholds = self._build_short_quality_gate_thresholds()
            quality_gate_payload = self._evaluate_short_quality_gate(
                layout,
                eval_report,
                thresholds=quality_gate_thresholds,
                attempted_repair=False,
                best_effort_accepted=False,
            )
            self._on_step(
                "short_quality_gate",
                {
                    "verdict": quality_gate_payload["verdict"],
                    "failed_dimensions": quality_gate_payload["failed_dimensions"],
                },
            )

            if quality_gate_payload["failed_dimensions"]:
                failed_gate_messages = [
                    str(item.get("message", "") or "")
                    for item in quality_gate_payload.get("checks", [])
                    if not item.get("passed", True) and str(item.get("message", "") or "")
                ]
                quality_issues = unique_texts(
                    [
                        *failed_gate_messages,
                        *self._collect_short_quality_repair_issues(eval_report),
                    ]
                )[:4]
                quality_result = await self._run_quality_repair(
                    layout,
                    spec,
                    beats,
                    blueprint,
                    execution_plan,
                    current_text,
                    quality_issues,
                    pass_label="short_quality_repair_after_eval",
                    iteration=next_repair_iteration,
                    eval_report=eval_report,
                )
                current_text, eval_report = await adopt_repair(
                    quality_result, current_text, eval_report
                )
                creative_summary = await self._run_creative_analysis(
                    layout, spec, beats, blueprint, current_text
                )
                quality_gate_payload = self._evaluate_short_quality_gate(
                    layout,
                    eval_report,
                    thresholds=quality_gate_thresholds,
                    attempted_repair=True,
                    best_effort_accepted=False,
                )
                if quality_gate_payload["failed_dimensions"]:
                    quality_gate_payload["best_effort_accepted"] = True
                    self._storage.save_json(
                        layout.short_quality_gate_report_path(), quality_gate_payload
                    )
                    self._emit_warning(
                        warnings,
                        step_name="short_quality_gate_warning",
                        message=(
                            "短篇最终质量门仍有未达标项，已按 best-effort 输出："
                            + "、".join(quality_gate_payload["failed_dimensions"])
                        ),
                        payload={"quality_gate": quality_gate_payload},
                    )
                    eval_report.passed = False
                    self._on_step(
                        "short_quality_gate_best_effort",
                        {
                            "verdict": quality_gate_payload["verdict"],
                            "failed_dimensions": quality_gate_payload["failed_dimensions"],
                        },
                    )
                else:
                    self._on_step(
                        "short_quality_gate_pass_after_repair",
                        {"verdict": quality_gate_payload["verdict"]},
                    )
            else:
                creative_summary = await self._run_creative_analysis(
                    layout,
                    spec,
                    beats,
                    blueprint,
                    current_text,
                )
        else:
            creative_summary = await self._run_creative_analysis(
                layout,
                spec,
                beats,
                blueprint,
                current_text,
            )
        return current_text, eval_report, creative_summary

    async def _run_creative_analysis(
        self,
        layout: ProjectLayout,
        spec: StorySpec,
        beats: StoryBeats,
        blueprint: ShortBlueprint | None,
        current_text: str,
    ) -> ShortCreativeSummary | None:
        from novel_forge.pipeline.short.stages.creative import run_creative_analysis

        return await run_creative_analysis(
            self,
            layout,
            spec,
            beats,
            blueprint,
            current_text,
        )

    def _build_short_execution_plan(
        self,
        spec: StorySpec,
        beats: StoryBeats,
        blueprint: ShortBlueprint | None,
    ) -> dict[str, Any]:
        weights = {
            "opening": 1.0,
            "rising": 1.05,
            "climax": 1.2,
            "falling": 0.9,
            "resolution": 0.85,
        }
        beat_weights = [
            weights.get(getattr(beat.beat_type, "value", str(beat.beat_type)), 1.0)
            for beat in beats.beats
        ]
        total_weight = sum(beat_weights) or 1.0
        target_words = max(500, spec.length_target or beats.total_estimated_words or 3000)
        budgets = [max(1, round(target_words * weight / total_weight)) for weight in beat_weights]
        diff = target_words - sum(budgets)
        if budgets:
            budgets[-1] += diff

        anchor = blueprint.anchor_elements if blueprint is not None else None
        beat_execution_plan: list[dict[str, Any]] = []

        total_beats = max(1, len(beats.beats))
        for index, beat in enumerate(beats.beats):
            progress = round(((index + 0.5) / total_beats) * 100)
            phase = self._match_phase(blueprint, progress)
            beat_execution_plan.append(
                {
                    "sequence": beat.sequence,
                    "structural_role": getattr(beat.beat_type, "value", str(beat.beat_type)),
                    "word_budget": budgets[index]
                    if index < len(budgets)
                    else max(180, target_words // total_beats),
                    "phase_name": getattr(phase, "phase_name", ""),
                    "phase_goal": getattr(phase, "description", "") or beat.summary,
                    "time_anchor": getattr(phase, "time_setting", "")
                    or getattr(anchor, "time_frame", ""),
                    "location_anchor": getattr(phase, "location", "") or beat.setting,
                    "character_focus": getattr(phase, "characters_present", [])
                    or beat.characters_involved,
                    "emotional_target": getattr(phase, "emotional_focus", "")
                    or beat.emotional_note,
                    "turning_point_hint": self._match_turning_point(blueprint, progress),
                }
            )

        opening_phase = (
            blueprint.narrative_phases[0] if blueprint and blueprint.narrative_phases else None
        )
        completion_contract = {
            "core_question": self._clean_text(
                getattr(anchor, "central_event", "") or spec.conflict_hint or spec.theme
            ),
            "resolution_target": self._clean_text(beats.beats[-1].summary if beats.beats else ""),
            "ending_strategy": self._clean_text(
                (blueprint.ending_strategy if blueprint else "") or spec.ending_style
            ),
            "final_emotional_landing": self._clean_text(
                (
                    blueprint.narrative_phases[-1].emotional_focus
                    if blueprint and blueprint.narrative_phases
                    else ""
                )
                or (
                    blueprint.emotional_arc.split("→")[-1]
                    if blueprint and blueprint.emotional_arc
                    else ""
                )
            ),
        }
        return {
            "opening_contract": self._clean_text(
                spec.opening_style
                or (getattr(opening_phase, "description", "") if opening_phase else "")
                or "开场先锚定人物、场景与异常变化，再启动冲突。"
            ),
            "ending_contract": self._clean_text(
                (blueprint.ending_strategy if blueprint else "")
                or spec.ending_style
                or "结尾完成情绪落点，并让余韵与主题彼此照应。"
            ),
            "anchor_guardrails": {
                "time_frame": self._clean_text(getattr(anchor, "time_frame", "")),
                "locations": list(getattr(anchor, "primary_locations", []) or []),
                "characters": [c.name for c in getattr(anchor, "core_characters", [])],
                "central_event": self._clean_text(
                    getattr(anchor, "central_event", "") or spec.conflict_hint or spec.theme
                ),
            },
            "completion_contract": completion_contract,
            "beat_execution_plan": beat_execution_plan,
        }

    @staticmethod
    def _match_phase(blueprint: ShortBlueprint | None, progress_percent: int) -> Any | None:
        if blueprint is None or not blueprint.narrative_phases:
            return None
        for phase in blueprint.narrative_phases:
            if phase.position_start <= progress_percent <= phase.position_end:
                return phase
        return blueprint.narrative_phases[-1]

    @staticmethod
    def _match_turning_point(blueprint: ShortBlueprint | None, progress_percent: int) -> str:
        if blueprint is None or not blueprint.turning_points:
            return ""
        for turning_point in blueprint.turning_points:
            if abs(turning_point.position_percent - progress_percent) <= 12:
                return turning_point.description
        return ""

    # ── Checkpoint / resume ────────────────────────────────────────────

    @classmethod
    def _canonicalize_checkpoint_value(cls, value: Any) -> Any:
        if isinstance(value, str):
            return cls._clean_text(value)
        if isinstance(value, dict):
            return {
                str(key): cls._canonicalize_checkpoint_value(value[key]) for key in sorted(value)
            }
        if isinstance(value, (list, tuple)):
            return [cls._canonicalize_checkpoint_value(item) for item in value]
        return value

    @classmethod
    def _checkpoint_fingerprint(cls, payload: dict[str, Any]) -> str:
        return checkpoint_fingerprint(payload)

    def _build_checkpoint_payload(
        self,
        spec_input: dict[str, Any],
        *,
        segmented_mode: Literal["auto", "on", "off"] | None,
        segment_target_words: int | None,
        segment_max_count: int | None,
        blueprint_element_preferences: dict[str, Any] | None,
        research_enabled: bool = False,
        research_provider: str = "auto",
        research_query_hint: str = "",
    ) -> dict[str, Any]:
        return build_checkpoint_payload(
            spec_input,
            writing_mode=self._writing_mode,
            segmented_mode=segmented_mode,
            segment_target_words=segment_target_words,
            segment_max_count=segment_max_count,
            blueprint_element_preferences=blueprint_element_preferences,
            research_enabled=research_enabled,
            research_provider=research_provider,
            research_query_hint=research_query_hint,
        )

    @staticmethod
    def _short_meta_path(layout: ProjectLayout) -> Path:
        return short_meta_path(layout)

    @staticmethod
    def _remove_path(path: Path) -> bool:
        return remove_path(path)

    @staticmethod
    def _story_kernel_db_artifacts(layout: ProjectLayout) -> list[Path]:
        db_path = layout.story_kernel_db_path
        return [
            db_path,
            db_path.with_name(f"{db_path.name}-wal"),
            db_path.with_name(f"{db_path.name}-shm"),
        ]

    def _remove_short_checkpoint_artifacts(self, layout: ProjectLayout) -> list[str]:
        removed: list[str] = []
        direct_paths = [
            layout.spec_path,
            layout.short_blueprint_path(),
            layout.blueprint_elements_path,
            layout.short_beats_path(),
            layout.style_profile_path,
            layout.eval_report_path(),
            layout.short_creative_report_path(),
            layout.chapters_dir / "short_story.md",
            layout.short_segment_plan_path(),
            layout.reports_dir / "short_research_report.json",
            layout.reports_dir / "short_research_dossier.json",
            layout.reports_dir / "short_research_evidence.json",
            layout.reports_dir / "short_revision_checkpoint.json",
            *self._story_kernel_db_artifacts(layout),
        ]
        for path in direct_paths:
            if self._remove_path(path):
                removed.append(str(path))

        for path in layout.drafts_dir.glob("v*.md") if layout.drafts_dir.exists() else []:
            if self._remove_path(path):
                removed.append(str(path))
        for directory in (layout.short_segment_drafts_dir, layout.short_segment_plans_dir):
            if self._remove_path(directory):
                removed.append(str(directory))
        layout.ensure_dirs()
        return removed

    def _short_resume_artifact_paths_from_step(
        self,
        layout: ProjectLayout,
        step: str,
    ) -> list[Path]:
        """Artifacts to quarantine when a short-mode resume checkpoint is invalid."""
        direct_groups: dict[str, list[Path]] = {
            "spec": [
                layout.spec_path,
                layout.blueprint_elements_path,
                layout.short_blueprint_path(),
                layout.short_beats_path(),
                layout.style_profile_path,
                layout.eval_report_path(),
                layout.short_creative_report_path(),
                layout.chapters_dir / "short_story.md",
                layout.short_segment_plan_path(),
                *self._story_kernel_db_artifacts(layout),
            ],
            "blueprint": [
                layout.short_blueprint_path(),
                layout.short_beats_path(),
                layout.style_profile_path,
                layout.eval_report_path(),
                layout.short_creative_report_path(),
                layout.chapters_dir / "short_story.md",
                layout.short_segment_plan_path(),
                *self._story_kernel_db_artifacts(layout),
            ],
            "beats": [
                layout.short_beats_path(),
                layout.eval_report_path(),
                layout.short_creative_report_path(),
                layout.chapters_dir / "short_story.md",
                layout.short_segment_plan_path(),
                *self._story_kernel_db_artifacts(layout),
            ],
            "draft": [
                layout.eval_report_path(),
                layout.short_creative_report_path(),
                layout.chapters_dir / "short_story.md",
                layout.short_segment_plan_path(),
                *self._story_kernel_db_artifacts(layout),
            ],
        }
        paths = list(direct_groups.get(step, []))
        if step in {"spec", "blueprint", "beats", "draft"} and layout.drafts_dir.exists():
            paths.extend(layout.drafts_dir.glob("v*.md"))
        if step in {"spec", "blueprint", "beats", "draft"}:
            paths.extend(
                [
                    layout.short_segment_drafts_dir,
                    layout.short_segment_plans_dir,
                    layout.reports_dir / "short_revision_checkpoint.json",
                ]
            )
        return paths

    def _quarantine_short_resume_artifacts(
        self,
        layout: ProjectLayout,
        step: str,
    ) -> list[str]:
        """Move invalid short-mode checkpoints aside without deleting them."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        quarantine_root = layout.states_dir / "short_resume_rollbacks" / f"{stamp}_{step}"
        moved: list[str] = []
        for path in self._short_resume_artifact_paths_from_step(layout, step):
            if not path.exists():
                continue
            rel_path = path.relative_to(layout.root)
            dest = quarantine_root / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(path), str(dest))
                moved.append(str(rel_path))
            except FileNotFoundError:
                continue
        layout.ensure_dirs()
        return moved

    def _rollback_short_resume_step(
        self,
        layout: ProjectLayout,
        step: str,
        exc: Exception,
    ) -> list[str]:
        moved = self._quarantine_short_resume_artifacts(layout, step)
        self._on_step(
            "short_resume_rollback",
            {
                "step": step,
                "reason": str(exc),
                "moved_artifacts": moved,
            },
        )
        _log.warning(
            "short_resume_rollback | step=%s | moved=%s | error=%s",
            step,
            moved,
            exc,
        )
        return moved

    def _load_short_resume_json(
        self,
        layout: ProjectLayout,
        *,
        step: str,
        path: Path,
        validator: Any,
    ) -> dict[str, Any] | None:
        """Load and validate a short-mode resume JSON artifact."""
        if not self._storage.exists(path):
            return None
        try:
            payload = self._storage.load_json(path)
            validator(payload)
            return payload
        except Exception as exc:
            self._rollback_short_resume_step(layout, step, exc)
            return None

    def _legacy_short_input_drift(
        self,
        layout: ProjectLayout,
        spec_input: dict[str, Any],
        *,
        segmented_mode: Literal["auto", "on", "off"] | None,
        segment_target_words: int | None,
        segment_max_count: int | None,
        blueprint_element_preferences: dict[str, Any] | None,
        research_enabled: bool = False,
        research_provider: str = "auto",
        research_query_hint: str = "",
    ) -> list[str]:
        drift: list[str] = []
        try:
            existing = self._storage.load_json(layout.spec_path)
        except Exception:
            existing = {}
        for key in _SHORT_SPEC_KEYS:
            incoming = spec_input.get(key)
            if incoming in (None, ""):
                continue
            current = existing.get(key)
            if isinstance(incoming, str) or isinstance(current, str):
                if self._clean_text(incoming) != self._clean_text(current):
                    drift.append(f"{key}: checkpoint does not match current request")
            elif current is not None and incoming != current:
                drift.append(f"{key}: checkpoint does not match current request")
        if (
            self._writing_mode != "auto"
            or segmented_mode is not None
            or segment_target_words is not None
            or segment_max_count is not None
            or has_manual_selector_preferences(blueprint_element_preferences)
            or research_enabled
            or str(research_provider or "auto").strip().lower() != "auto"
            or bool(str(research_query_hint or "").strip())
        ):
            drift.append("legacy checkpoint has no generation option fingerprint")
        return drift

    def _effective_segmented_mode(
        self,
        segmented_mode: Literal["auto", "on", "off"] | None,
    ) -> Literal["auto", "on", "off"] | None:
        if self._writing_mode == "scene_level":
            return "on"
        if self._writing_mode == "whole_chapter":
            return "off"
        return segmented_mode

    def _ensure_short_checkpoint_fresh(
        self,
        layout: ProjectLayout,
        *,
        request_payload: dict[str, Any],
        request_fingerprint: str,
        spec_input: dict[str, Any],
        segmented_mode: Literal["auto", "on", "off"] | None,
        segment_target_words: int | None,
        segment_max_count: int | None,
        blueprint_element_preferences: dict[str, Any] | None,
        research_enabled: bool = False,
        research_provider: str = "auto",
        research_query_hint: str = "",
    ) -> None:
        meta_path = self._short_meta_path(layout)
        reasons: list[str] = []
        if meta_path.exists():
            try:
                meta = self._storage.load_json(meta_path)
                previous = str(meta.get("request_fingerprint") or "")
            except Exception:
                previous = ""
            if previous and previous != request_fingerprint:
                reasons.append("request_fingerprint_changed")
            elif not previous:
                reasons.append("checkpoint_metadata_unreadable")
        elif layout.spec_path.exists():
            reasons.extend(
                self._legacy_short_input_drift(
                    layout,
                    spec_input,
                    segmented_mode=segmented_mode,
                    segment_target_words=segment_target_words,
                    segment_max_count=segment_max_count,
                    blueprint_element_preferences=blueprint_element_preferences,
                    research_enabled=research_enabled,
                    research_provider=research_provider,
                    research_query_hint=research_query_hint,
                )
            )

        if reasons:
            removed = self._remove_short_checkpoint_artifacts(layout)
            self._on_step(
                "short_checkpoint_invalidated",
                {"reasons": reasons, "removed_artifacts": removed},
            )

        self._storage.save_json(
            meta_path,
            {
                "schema_version": 1,
                "request_fingerprint": request_fingerprint,
                "request": request_payload,
            },
        )

    def _detect_checkpoint(self, layout: ProjectLayout) -> dict[str, Any]:
        """Detect existing artifacts for resume. Returns available data."""
        checkpoint: dict[str, Any] = {}
        spec = self._load_short_resume_json(
            layout,
            step="spec",
            path=layout.spec_path,
            validator=StorySpec.model_validate,
        )
        if spec is None:
            return checkpoint
        checkpoint["spec"] = spec

        blueprint = self._load_short_resume_json(
            layout,
            step="blueprint",
            path=layout.short_blueprint_path(),
            validator=ShortBlueprint.model_validate,
        )
        if blueprint is not None:
            checkpoint["blueprint"] = blueprint

        beats = self._load_short_resume_json(
            layout,
            step="beats",
            path=layout.short_beats_path(),
            validator=StoryBeats.model_validate,
        )
        if beats is None:
            return checkpoint
        checkpoint["beats"] = beats

        try:
            draft_text = self._storage.load_text(layout.short_draft_path(0))
            if not str(draft_text).strip():
                raise ValueError("draft checkpoint is empty")
            checkpoint["draft_text"] = draft_text
        except FileNotFoundError:
            pass
        except Exception as exc:
            self._rollback_short_resume_step(layout, "draft", exc)
        return checkpoint

    def _prepare_layout(self, project_id: str) -> ProjectLayout:
        layout = ProjectLayout(self._storage.ensure_project_dir(project_id))
        layout.ensure_dirs()
        return layout

    def _story_kernel_db_path(self, layout: ProjectLayout) -> Path:
        configured = getattr(self._settings, "story_kernel_db_path", "")
        if isinstance(configured, (str, Path)) and str(configured).strip():
            return Path(str(configured).strip())
        return layout.story_kernel_db_path

    async def _initialize_short_story_kernel(
        self,
        *,
        layout: ProjectLayout,
        project_id: str,
        spec: StorySpec,
        blueprint: ShortBlueprint | None,
        beats: StoryBeats,
        execution_plan: dict[str, Any],
    ) -> None:
        store = StoryKernelStore(
            self._story_kernel_db_path(layout),
            wal_mode=bool(getattr(self._settings, "story_kernel_wal_mode", True)),
        )
        try:
            await StoryKernelStateWriter(store).initialize_short_project(
                project_id=project_id,
                spec=spec,
                blueprint=blueprint,
                beats=beats,
                execution_plan=execution_plan,
                artifact_refs={
                    "short_spec": {"path": "spec.json", "source": "short_init"},
                    "short_blueprint": {
                        "path": "plans/short_blueprint.json",
                        "source": "short_init",
                    },
                    "short_beats": {"path": "beats.json", "source": "short_init"},
                },
            )
        finally:
            await store.close()

    async def _merge_short_story_kernel(
        self,
        *,
        layout: ProjectLayout,
        project_id: str,
        final_text: str,
        eval_report: EvalReport,
        creative_summary: ShortCreativeSummary | None,
        final_story_path: Path,
    ) -> None:
        store = StoryKernelStore(
            self._story_kernel_db_path(layout),
            wal_mode=bool(getattr(self._settings, "story_kernel_wal_mode", True)),
        )
        try:
            updated = await StoryKernelStateWriter(store).merge_short_outcome(
                project_id=project_id,
                final_text=final_text,
                eval_report=eval_report,
                creative_summary=creative_summary,
                final_story_path=final_story_path,
            )
            self._on_step(
                "story_kernel_short_updated",
                {
                    "project_id": project_id,
                    "entities": len(updated.entities),
                    "timeline": len(updated.timeline),
                    "promises": len(updated.promise_ledger),
                },
            )
        finally:
            await store.close()

    # ── Main run() ─────────────────────────────────────────────────────

    async def run(
        self,
        spec_input: dict[str, Any],
        *,
        project_id: str = "short_default",
        segmented_mode: Literal["auto", "on", "off"] | None = None,
        segment_target_words: int | None = None,
        segment_max_count: int | None = None,
        blueprint_element_preferences: dict[str, Any] | None = None,
        research_enabled: bool = False,
        research_provider: str = "auto",
        research_query_hint: str = "",
    ) -> ShortStoryResult:
        """Execute the full short-mode pipeline with checkpoint/resume support."""
        layout = self._prepare_layout(project_id)
        self._user_intent = build_story_spec_user_intent_card(
            spec_input,
            blueprint_element_preferences=blueprint_element_preferences,
        )
        effective_segmented_mode = self._effective_segmented_mode(segmented_mode)
        checkpoint_payload = self._build_checkpoint_payload(
            spec_input,
            segmented_mode=effective_segmented_mode,
            segment_target_words=segment_target_words,
            segment_max_count=segment_max_count,
            blueprint_element_preferences=blueprint_element_preferences,
            research_enabled=research_enabled,
            research_provider=research_provider,
            research_query_hint=research_query_hint,
        )
        checkpoint_fingerprint = self._checkpoint_fingerprint(checkpoint_payload)
        self._ensure_short_checkpoint_fresh(
            layout,
            request_payload=checkpoint_payload,
            request_fingerprint=checkpoint_fingerprint,
            spec_input=spec_input,
            segmented_mode=effective_segmented_mode,
            segment_target_words=segment_target_words,
            segment_max_count=segment_max_count,
            blueprint_element_preferences=blueprint_element_preferences,
            research_enabled=research_enabled,
            research_provider=research_provider,
            research_query_hint=research_query_hint,
        )

        checkpoint = self._detect_checkpoint(layout)
        warnings: list[str] = []

        if checkpoint.get("spec"):
            spec = StorySpec.model_validate(checkpoint["spec"])
            self._on_step("spec_resumed", spec)
        else:
            spec = await run_spec(self, layout, spec_input)

        await prepare_short_research(
            self,
            layout,
            spec,
            enabled=research_enabled,
            provider_name=research_provider,
            query_hint=research_query_hint,
        )

        blueprint: ShortBlueprint | None
        if checkpoint.get("blueprint"):
            blueprint = ShortBlueprint.model_validate(checkpoint["blueprint"])
            if blueprint.element_selection is None and self._storage.exists(
                layout.blueprint_elements_path
            ):
                try:
                    element_selection = BlueprintElementSelection.model_validate(
                        self._storage.load_json(layout.blueprint_elements_path)
                    )
                    blueprint = blueprint.model_copy(
                        update={"element_selection": element_selection}
                    )
                except Exception:
                    _log.warning("short_blueprint_selection_attach_failed", exc_info=True)
            self._on_step("blueprint_resumed", blueprint)
        else:
            element_selection = await resolve_blueprint_elements(
                self,
                layout,
                spec,
                preferences=blueprint_element_preferences,
            )
            blueprint = await run_blueprint(
                self,
                layout,
                spec,
                element_selection=element_selection,
            )
        if blueprint_is_degraded(blueprint):
            self._emit_warning(
                warnings,
                step_name="short_blueprint_warning",
                message="短篇叙事蓝图信息不足，本次将按精简蓝图继续生成，结构稳定性可能下降。",
            )

        if checkpoint.get("beats"):
            beats = StoryBeats.model_validate(checkpoint["beats"])
            self._on_step("beats_resumed", beats)
            if not self._load_style_profile(layout):
                await _ensure_style_profile(self, layout, spec, blueprint)
        else:

            async def _task_beats() -> StoryBeats:
                result = await run_beats(self, layout, spec, blueprint)
                self._on_step("beats_resumed", result)
                return result

            beats_result, _ = await asyncio.gather(
                _task_beats(),
                _ensure_style_profile(self, layout, spec, blueprint),
            )
            beats = beats_result

        execution_plan = self._build_short_execution_plan(spec, beats, blueprint)
        if execution_plan.get("beat_execution_plan"):
            self._on_step("short_execution_plan", execution_plan)
        await self._initialize_short_story_kernel(
            layout=layout,
            project_id=project_id,
            spec=spec,
            blueprint=blueprint,
            beats=beats,
            execution_plan=execution_plan,
        )

        if checkpoint.get("draft_text"):
            current_text = checkpoint["draft_text"]
            self._on_step("draft_resumed", {"word_count": count_chapter_words(current_text)})
        else:
            current_text = await run_initial_draft(
                self,
                layout,
                spec,
                beats,
                blueprint,
                execution_plan,
                segmented_mode=effective_segmented_mode,
                segment_target_words=segment_target_words,
                segment_max_count=segment_max_count,
            )

        if getattr(self._settings, "short_adaptive_revision_enabled", False):
            from novel_forge.pipeline.short.stages.adaptive_revision import (
                run_adaptive_short_revision,
            )

            adaptive_result = await run_adaptive_short_revision(
                self,
                layout,
                spec,
                beats,
                blueprint,
                execution_plan,
                current_text,
            )
            current_text = adaptive_result.text
            eval_report = adaptive_result.eval_report
            if adaptive_result.unresolved_issues:
                self._emit_warning(
                    warnings,
                    step_name="short_adaptive_revision_warning",
                    message=(
                        "短篇自适应修订已达上限，保留了最佳已验证版本；仍有需要人工确认的问题。"
                    ),
                    payload={
                        "rounds_used": adaptive_result.rounds_used,
                        "diagnosis_hash": adaptive_result.diagnosis_hash,
                        "issues": list(adaptive_result.unresolved_issues),
                    },
                )
            creative_summary = await self._run_creative_analysis(
                layout,
                spec,
                beats,
                blueprint,
                current_text,
            )
        else:
            current_text, eval_report, creative_summary = await self._run_legacy_revision_flow(
                layout,
                spec,
                beats,
                blueprint,
                execution_plan,
                current_text,
                warnings,
            )
        if creative_summary is None:
            self._emit_warning(
                warnings,
                step_name="short_creative_warning",
                message="短篇创作分析生成失败，正文已完成，但创作分析报告缺失。",
            )
        manifest = ArtifactManifest(self._storage, layout)
        text_hash = source_text_hash(current_text)
        final_story_path = layout.chapters_dir / "short_story.md"
        final_record = matching_finalization_phase(
            manifest,
            chapter_number=1,
            phase="final_text",
            text_hash=text_hash,
        )
        final_on_disk = False
        if final_story_path.is_file():
            final_on_disk = (
                source_text_hash(final_story_path.read_text(encoding="utf-8")) == text_hash
            )
        if final_record is None or not final_on_disk:
            final_story_path = self._persist_final_story(layout, current_text)
            record_finalization_success(
                manifest,
                chapter_number=1,
                phase="final_text",
                text_hash=text_hash,
                paths={"short_story": str(final_story_path)},
                metadata={"mode": "short"},
            )

        if (
            matching_finalization_phase(
                manifest,
                chapter_number=1,
                phase="reports",
                text_hash=text_hash,
            )
            is None
        ):
            record_finalization_success(
                manifest,
                chapter_number=1,
                phase="reports",
                text_hash=text_hash,
                output_hashes={"eval": source_text_hash(str(eval_report.model_dump(mode="json")))},
                paths={"eval": str(layout.eval_report_path())},
                metadata={"creative_summary": creative_summary is not None},
            )
        if (
            matching_finalization_phase(
                manifest,
                chapter_number=1,
                phase="narrative_state",
                text_hash=text_hash,
            )
            is None
        ):
            record_finalization_success(
                manifest,
                chapter_number=1,
                phase="narrative_state",
                text_hash=text_hash,
                metadata={"skipped": True, "reason": "short_story_has_no_narrative_state"},
            )

        if (
            matching_finalization_phase(
                manifest,
                chapter_number=1,
                phase="story_kernel",
                text_hash=text_hash,
            )
            is None
        ):
            try:
                await self._merge_short_story_kernel(
                    layout=layout,
                    project_id=project_id,
                    final_text=current_text,
                    eval_report=eval_report,
                    creative_summary=creative_summary,
                    final_story_path=final_story_path,
                )
                record_finalization_success(
                    manifest,
                    chapter_number=1,
                    phase="story_kernel",
                    text_hash=text_hash,
                    output_hashes={"story_kernel": text_hash},
                    metadata={"mode": "short"},
                )
            except Exception as exc:
                record_finalization_pending(
                    manifest,
                    chapter_number=1,
                    phase="story_kernel",
                    text_hash=text_hash,
                    error=exc,
                    metadata={"mode": "short", "required": True},
                )
                raise

        return ShortStoryResult(
            spec=spec,
            blueprint=blueprint,
            beats=beats,
            final_text=current_text,
            eval_report=eval_report,
            creative_summary=creative_summary,
            warnings=warnings,
            trace_summary=self._trace.summary(),
        )

    def _persist_final_story(self, layout: ProjectLayout, current_text: str) -> Path:
        path = layout.chapters_dir / "short_story.md"
        self._storage.save_text(path, current_text)
        return path
