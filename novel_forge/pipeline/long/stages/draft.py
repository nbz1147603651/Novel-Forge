"""Draft stage: Generate-phase context building and raw DRAFT prose generation.

This module owns the first half of long-chapter Generate: building
``GenerateContext``, producing ``v0_draft.md``, and running the optional
pre-WAVE cleanup.  The reviewable handoff is produced by ``stages.wave`` as
``v1_wave.md``.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import hashlib
import re
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from novel_forge.core.constants import TaskType
from novel_forge.core.domain.guardrails import KNOWN_PROMPT_MARKERS, detect_prompt_leaks
from novel_forge.core.domain.world_context import (
    dump_story_bible_for_prompt,
    format_address_rules_for_prompt,
    render_world_context_rules,
)
from novel_forge.core.exceptions import ModelGatewayError
from novel_forge.core.utils.text_validation import count_chapter_words
from novel_forge.narrative_state.schemas import ExpressionRepetitionReport
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.long.helpers import load_prev_known_issues
from novel_forge.pipeline.long.services.chapter_position import build_chapter_position
from novel_forge.pipeline.long.services.context.stage_memory_builder import (
    collect_draft_memory_hints as _collect_draft_memory_hints_shared,
)
from novel_forge.pipeline.long.services.context.story_kernel_context import (
    load_story_kernel_composer,
)
from novel_forge.pipeline.long.services.element_progress import load_element_progress

if TYPE_CHECKING:
    from novel_forge.obs.tracer import PipelineTrace
    from novel_forge.pipeline.long.execution_models import (
        ChapterExecutionContext,
        ChapterReviewArtifacts,
    )

_WORD_COUNT_TARGET_LOW = 0.90
_WORD_COUNT_TARGET_HIGH = 1.10
_logger = get_logger("pipeline.long.stages.draft")


def _settings_int(settings: Any, name: str, default: int, *, minimum: int = 0) -> int:
    try:
        value = int(getattr(settings, name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _settings_float(
    settings: Any,
    name: str,
    default: float,
    *,
    minimum: float = 0.0,
    maximum: float = 1.0,
) -> float:
    try:
        value = float(getattr(settings, name, default))
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def _trim_canon_context(canon_context: Any) -> Any:
    """Extract characters and immutable facts from canon_context to reduce token usage.

    Only the fields actually referenced by the draft/edit templates are kept:
    - ``characters``: used by the pronoun_consistency macro
    - ``immutable_facts``: rendered as hard constraints (★ P0) in draft_chapter.j2
    - ``entity_reference_graph``: used by the narrative-state macro for identity disambiguation
    """
    if not isinstance(canon_context, dict):
        return canon_context
    result: dict[str, Any] = {"characters": canon_context.get("characters", {})}
    immutable = canon_context.get("immutable_facts")
    if immutable:
        result["immutable_facts"] = immutable
    if canon_context.get("authoritative_narrative_state"):
        result["authoritative_narrative_state"] = canon_context.get("authoritative_narrative_state")
    if canon_context.get("chapter_contract"):
        result["chapter_contract"] = canon_context.get("chapter_contract")
    if canon_context.get("entity_reference_graph"):
        result["entity_reference_graph"] = canon_context.get("entity_reference_graph")
    return result


def _build_pov_relationship_snapshot(
    packet: Any,
    pov_character: str,
    *,
    max_relationships: int = 5,
) -> list[dict[str, Any]]:
    """Build a compact relationship snapshot for the POV character.

    Returns only relationships involving the POV character, sorted by
    recency, limited to *max_relationships* entries. This gives the
    draft LLM precise knowledge of trust/tension levels for writing
    accurate dialogue and interactions.
    """
    raw_rels = getattr(packet, "active_relationships", None) or []
    pov_rels: list[Any] = []
    for rel in raw_rels:
        chars = (
            getattr(rel, "characters", [])
            if not isinstance(rel, dict)
            else rel.get("characters", [])
        )
        if pov_character in chars:
            pov_rels.append(rel)
    # Sort by last_updated_chapter descending (most recent first).
    pov_rels.sort(
        key=lambda r: (
            getattr(r, "last_updated_chapter", 0)
            if not isinstance(r, dict)
            else r.get("last_updated_chapter", 0)
        ),
        reverse=True,
    )
    result: list[dict[str, Any]] = []
    for rel in pov_rels[:max_relationships]:
        if isinstance(rel, dict):
            chars = rel.get("characters", [])
            other = next((c for c in chars if c != pov_character), chars[0] if chars else "")
            result.append(
                {
                    "other_character": other,
                    "public_status": rel.get("public_status", ""),
                    "trust": rel.get("trust", 0.5),
                    "tension": rel.get("tension", 0.5),
                    "last_shift_event": rel.get("last_shift_event", ""),
                }
            )
        else:
            chars = getattr(rel, "characters", [])
            other = next((c for c in chars if c != pov_character), chars[0] if chars else "")
            result.append(
                {
                    "other_character": other,
                    "public_status": getattr(rel, "public_status", ""),
                    "trust": getattr(rel, "trust", 0.5),
                    "tension": getattr(rel, "tension", 0.5),
                    "last_shift_event": getattr(rel, "last_shift_event", ""),
                }
            )
    return result


def _chapter_word_count(text: str) -> int:
    return count_chapter_words(text)


def _word_count_ratio(text: str, target_words: int) -> float:
    if target_words <= 0:
        return 1.0
    return _chapter_word_count(text) / max(target_words, 1)


def _primary_scene_intent(plan: Any | None) -> dict[str, Any]:
    """Extract the primary scene's intent as a flat dict for PromptBuilder.

    Picks the first scene from plan.scene_intents and flattens the relevant
    SceneIntent fields into the keys StyleGoldenRetriever recognizes:
    ``emotional_tone``, ``setting``, ``pov_keywords``, ``scene_action``.

    Added in M3.5 — see docs/ai_flavor_quality.md.

    Robust to dict / object / missing plan / empty scenes.
    """
    empty: dict[str, Any] = {}
    if plan is None:
        return empty
    if isinstance(plan, dict):
        scenes = plan.get("scene_intents") or []
    else:
        scenes = getattr(plan, "scene_intents", None) or []
    if not scenes:
        return empty
    scene = scenes[0]
    if isinstance(scene, dict):
        get = scene.get
    else:

        def get(key: str, default: Any = "") -> Any:
            return getattr(scene, key, default)

    return {
        "emotional_tone": get("emotional_beat", "") or get("emotional_tone", ""),
        "scene_action": get("purpose", "") or get("scene_action", ""),
        "setting": get("location", "") or get("setting", ""),
        "pov_keywords": get("pov_character", "") or get("pov_keywords", ""),
        "topic": get("summary", "") or get("topic", ""),
        "characters_involved": list(get("required_characters", []) or []),
    }


def _needs_word_count_adjustment(text: str, target_words: int) -> bool:
    if target_words <= 0:
        return False
    ratio = _word_count_ratio(text, target_words)
    return ratio < _WORD_COUNT_TARGET_LOW or ratio > _WORD_COUNT_TARGET_HIGH


def _ensure_scene_word_budgets(plan: Any, total_target: int) -> Any:
    """Backfill per-scene target_words when the planner didn't provide them."""
    if total_target <= 0 or not getattr(plan, "scene_intents", None):
        return plan
    scenes = list(plan.scene_intents)
    has_budgets = any(getattr(s, "target_words", 0) > 0 for s in scenes)
    if has_budgets:
        # Planner already allocated — check sum and redistribute slack if needed
        total_allocated = sum(getattr(s, "target_words", 0) for s in scenes)
        if total_allocated > 0 and abs(total_allocated - total_target) > total_target * 0.2:
            scale = total_target / total_allocated
            new_scenes = [
                _copy_with_updates(
                    s,
                    {"target_words": max(200, int(getattr(s, "target_words", 0) * scale))},
                )
                for s in scenes
            ]
            return _copy_with_updates(plan, {"scene_intents": new_scenes})
        return plan
    # Even distribution as fallback
    per_scene = max(200, total_target // len(scenes))
    remainder = total_target - per_scene * len(scenes)
    new_scenes = [
        _copy_with_updates(s, {"target_words": per_scene + (1 if i < remainder else 0)})
        for i, s in enumerate(scenes)
    ]
    return _copy_with_updates(plan, {"scene_intents": new_scenes})


def _copy_with_updates(value: Any, updates: dict[str, Any]) -> Any:
    if hasattr(value, "model_copy"):
        return value.model_copy(update=updates)
    clone = copy.copy(value)
    for key, item in updates.items():
        setattr(clone, key, item)
    return clone


def build_word_count_warning(text: str, target_words: int) -> str:
    """Build guidance when the chapter length drifts away from target."""
    if target_words <= 0:
        return ""
    current_words = _chapter_word_count(text)
    ratio = _word_count_ratio(text, target_words)
    low_bound = int(round(target_words * _WORD_COUNT_TARGET_LOW))
    high_bound = int(round(target_words * _WORD_COUNT_TARGET_HIGH))
    if ratio < _WORD_COUNT_TARGET_LOW:
        gap = max(target_words - current_words, 0)
        return (
            f"当前字数 {current_words}，目标 {target_words}，明显偏短（少约 {gap} 字）。"
            f"请补足场景过程、动作链、关键对话与感官锚点，把正文尽量拉回 {low_bound}-{high_bound} 字；"
            "禁止用摘要句一笔带过。"
        )
    if ratio > _WORD_COUNT_TARGET_HIGH:
        overflow = max(current_words - target_words, 0)
        return (
            f"当前字数 {current_words}，目标 {target_words}，明显偏长（多约 {overflow} 字）。"
            f"请压缩重复旁白、说明性解释和低信息密度段落，把正文尽量收回 {low_bound}-{high_bound} 字。"
        )
    return ""


def _normalize_element_focus_ids(value: Any, *, max_items: int = 3) -> list[str]:
    """Normalize chapter-level element focus ids without importing private service helpers."""
    result: list[str] = []
    seen: set[str] = set()
    for raw in list(value or []):
        item = str(raw or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
        if len(result) >= max_items:
            break
    return result


def _load_scheduled_element_focus_ids(
    storage: Any,
    layout: Any,
    *,
    chapter_number: int,
) -> tuple[list[str], str]:
    """Load focus ids scheduled by planning, including dynamic focus recommendations."""
    try:
        payload = load_element_progress(storage, layout)
    except Exception as exc:
        _logger.debug(
            "element_focus_schedule_load_failed | chapter=%d | error=%s",
            chapter_number,
            exc,
        )
        return [], ""
    chapters = payload.get("chapters", {}) if isinstance(payload, dict) else {}
    chapter_data = chapters.get(str(int(chapter_number))) if isinstance(chapters, dict) else None
    if not isinstance(chapter_data, dict):
        return [], ""
    focus_ids = _normalize_element_focus_ids(chapter_data.get("scheduled_element_ids", []))
    focus_source = str(chapter_data.get("focus_source", "") or "").strip()
    return focus_ids, focus_source


def _resolve_active_element_focus_ids(
    chapter_outline: Any,
    storage: Any,
    layout: Any,
    *,
    chapter_number: int,
) -> tuple[list[str], str]:
    """Resolve this chapter's active extension elements.

    Outline focus remains authoritative. If the outline leaves focus empty,
    use the planning-stage schedule so dynamic recommendations reach draft/edit.
    """
    outline_focus = _normalize_element_focus_ids(getattr(chapter_outline, "element_focus", []))
    if outline_focus:
        return outline_focus, "outline"
    scheduled_focus, focus_source = _load_scheduled_element_focus_ids(
        storage,
        layout,
        chapter_number=chapter_number,
    )
    if scheduled_focus:
        return scheduled_focus, focus_source or "scheduled"
    return [], ""


def _has_prompt_leaks_local(text: str) -> bool:
    """Locally detect prompt marker leakage via regex (no LLM call)."""
    return bool(detect_prompt_leaks(text, max_hits=1))


def _pre_wave_chapter_repair_report_path(layout: Any, chapter_number: int) -> Any:
    path_fn = getattr(layout, "pre_wave_chapter_repair_report_path", None)
    if callable(path_fn):
        return path_fn(chapter_number)
    reports_dir = getattr(layout, "reports_dir", None)
    if reports_dir is not None:
        return reports_dir / f"chapter_{chapter_number:03d}_pre_wave_chapter_repair_report.json"
    legacy_path = layout.chapter_repair_report_path(chapter_number)
    return legacy_path.with_name(
        f"chapter_{chapter_number:03d}_pre_wave_chapter_repair_report.json"
    )


async def _collect_draft_memory_hints(
    runner: Any,
    bundle: Any,
    plan: Any,
    bridge: Any,
    chapter_number: int,
    *,
    planning_hints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Collect memory hints for draft generation.

    Reuses ``relevant_history`` and ``previous_chapter_events`` from the
    planning stage when available, and only fetches draft-specific extras
    (motif suggestions, memory prompt context).
    """
    return await _collect_draft_memory_hints_shared(
        runner,
        bundle,
        plan,
        bridge,
        chapter_number,
        planning_hints=planning_hints,
    )


async def _draft_scene_level_text(
    *,
    runner: Any,
    bundle: Any,
    plan: Any,
    chapter_number: int,
    draft_step: Any,
    draft_context: dict[str, Any],
    reading_power_hint: dict[str, Any] | None,
    draft_kernel_context: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    from novel_forge.pipeline.long.services.generation.scene_writing import (
        assess_draft_plan_coverage,
        build_scene_draft_context,
        scene_draft_path,
        scene_execution_groups,
        scene_stitch_report_path,
        scene_stitched_draft_path,
        stitch_scene_texts,
        validate_scene_draft_locally,
    )
    from novel_forge.pipeline.steps.draft_step import DraftInput

    report_path = scene_stitch_report_path(bundle.layout, chapter_number)
    validation_report_path = (
        bundle.layout.reports_dir / f"chapter_{chapter_number:03d}_scene_plan_validation.json"
    )
    validation_report: dict[str, Any] = {}
    if runner._storage.exists(validation_report_path):
        try:
            validation_report = runner._storage.load_json(validation_report_path)
        except Exception:
            validation_report = {}
    groups = scene_execution_groups(plan, validation_report)
    scene_texts: dict[str, str] = {}
    scene_diagnostics: dict[str, dict[str, Any]] = {}
    hard_failures_resolved: list[dict[str, Any]] = []
    max_parallel = max(1, int(getattr(runner._settings, "long_scene_draft_max_parallel", 5) or 5))
    semaphore = asyncio.Semaphore(max_parallel)

    def _target_words_for(scene: Any) -> int:
        return int(getattr(scene, "target_words", 0) or 0) or max(
            300,
            int(getattr(bundle.chapter_outline, "expected_word_count", 0) or 0)
            // max(1, len(getattr(plan, "scene_intents", []) or [])),
        )

    def _handoffs_for(scene: Any) -> list[dict[str, str]]:
        deps = list(getattr(scene, "dependency_scene_ids", []) or [])
        if not deps:
            deps = list(scene_texts.keys())[-2:]
        handoffs: list[dict[str, str]] = []
        for dep_id in deps:
            text = scene_texts.get(str(dep_id), "")
            if not text:
                continue
            dep_scene = next(
                (
                    item
                    for item in list(getattr(plan, "scene_intents", []) or [])
                    if item.scene_id == dep_id
                ),
                None,
            )
            tail = " ".join(text.split())[-180:]
            handoffs.append(
                {
                    "scene_id": dep_id,
                    "handoff_to_next": str(getattr(dep_scene, "handoff_to_next", "") or ""),
                    "tail": tail,
                }
            )
        return handoffs

    def _load_reusable_scene(
        scene: Any,
        sid: str,
        context: dict[str, Any],
    ) -> tuple[str, dict[str, Any]] | None:
        path = scene_draft_path(bundle.layout, chapter_number, sid)
        try:
            if not runner._storage.exists(path):
                return None
            text = runner._storage.load_text(path).strip()
        except Exception:
            return None
        diagnostic = validate_scene_draft_locally(scene, text, context)
        if diagnostic.get("valid"):
            return text, diagnostic
        return None

    async def _run_scene(scene: Any) -> tuple[str, str, dict[str, Any], list[dict[str, Any]]]:
        async with semaphore:
            sid = str(getattr(scene, "scene_id", "") or "")
            scene_target_words = _target_words_for(scene)
            context = build_scene_draft_context(
                base_context=draft_context,
                plan=plan,
                scene=scene,
                target_word_count=scene_target_words,
                completed_scene_handoffs=_handoffs_for(scene),
            )
            reusable = await asyncio.to_thread(_load_reusable_scene, scene, sid, context)
            if reusable is not None:
                text, diagnostic = reusable
                runner._on_step(
                    "draft_scene_reused",
                    {
                        "chapter": chapter_number,
                        "scene_id": sid,
                        "text_chars": len(text),
                        "soft_warnings": len(diagnostic.get("soft_warnings", []) or []),
                    },
                )
                return sid, text, diagnostic, []

            resolved: list[dict[str, Any]] = []
            last_diagnostic: dict[str, Any] = {}
            previous_hard_failures: list[dict[str, Any]] = []
            for attempt in range(1, 3):
                if previous_hard_failures:
                    context = build_scene_draft_context(
                        base_context=draft_context,
                        plan=plan,
                        scene=scene,
                        target_word_count=scene_target_words,
                        completed_scene_handoffs=_handoffs_for(scene),
                        retry_feedback=previous_hard_failures,
                    )
                draft = await draft_step.run(
                    DraftInput(
                        task_type=TaskType.DRAFT_SCENE,
                        context=context,
                        reading_power_hint=reading_power_hint,
                        kernel_context=draft_kernel_context,
                        minimum_word_count=max(1, int(scene_target_words * 0.70)),
                        reject_prompt_leaks=True,
                    )
                )
                text = draft.text.strip()
                diagnostic = validate_scene_draft_locally(scene, text, context)
                last_diagnostic = diagnostic
                hard_failures = list(diagnostic.get("hard_failures", []) or [])
                if not hard_failures:
                    if previous_hard_failures:
                        resolved.extend(
                            {
                                "scene_id": sid,
                                "attempt": attempt - 1,
                                "code": str(item.get("code") or ""),
                                "message": str(item.get("message") or ""),
                            }
                            for item in previous_hard_failures
                        )
                    break
                previous_hard_failures = hard_failures
                runner._on_step(
                    "draft_scene_local_validation_failed",
                    {
                        "chapter": chapter_number,
                        "scene_id": sid,
                        "attempt": attempt,
                        "hard_failures": hard_failures,
                    },
                )
                if attempt >= 2:
                    messages = "；".join(
                        str(item.get("message") or item.get("code")) for item in hard_failures
                    )
                    raise ModelGatewayError(
                        f"第 {chapter_number} 章 {sid} 场景草稿本地验收失败：{messages}",
                        is_transient=True,
                    )
            else:
                text = ""
            if not text:
                raise ModelGatewayError(
                    f"第 {chapter_number} 章 {sid} 场景草稿为空", is_transient=True
                )
            await asyncio.to_thread(
                runner._storage.save_text,
                scene_draft_path(bundle.layout, chapter_number, sid),
                text,
            )
            runner._on_step(
                "draft_scene",
                {
                    "chapter": chapter_number,
                    "scene_id": sid,
                    "text_chars": len(text),
                    "soft_warnings": len(last_diagnostic.get("soft_warnings", []) or []),
                },
            )
            return sid, text, last_diagnostic, resolved

    for group_index, group in enumerate(groups, start=1):
        runner._on_step(
            "draft_scene_group",
            {
                "chapter": chapter_number,
                "group_index": group_index,
                "scene_ids": [str(getattr(scene, "scene_id", "") or "") for scene in group],
            },
        )
        results = await asyncio.gather(*(_run_scene(scene) for scene in group))
        for sid, text, diagnostic, resolved in results:
            scene_texts[sid] = text
            scene_diagnostics[sid] = diagnostic
            hard_failures_resolved.extend(resolved)

    stitched, stitch_report = stitch_scene_texts(
        plan=plan,
        scene_texts=scene_texts,
        scene_diagnostics=scene_diagnostics,
        hard_failures_resolved=hard_failures_resolved,
    )
    # The stitch report is specific to assembly.  This shared diagnostic gives
    # WAVE the same contract-level evidence in both writing modes.
    stitch_report["draft_plan_coverage"] = assess_draft_plan_coverage(
        plan=plan,
        text=stitched,
        target_word_count=int(getattr(bundle.chapter_outline, "expected_word_count", 0) or 0),
    )
    await asyncio.to_thread(
        runner._storage.save_json,
        report_path,
        stitch_report,
    )
    runner._on_step("scene_stitch", stitch_report)
    if not stitched:
        missing = "、".join(stitch_report.get("missing_scenes", []) or [])
        raise ModelGatewayError(
            f"第 {chapter_number} 章场景拼接失败，缺少：{missing}", is_transient=False
        )
    await asyncio.to_thread(
        runner._storage.save_text,
        scene_stitched_draft_path(bundle.layout, chapter_number),
        stitched,
    )
    return stitched, stitch_report


@dataclass(frozen=True)
class GenerateContext:
    """Shared context for DRAFT and WAVE stages.

    Built once by :func:`prepare_generate_context` and consumed by both
    :func:`generate_draft` and :func:`apply_wave`.  Keeps the two stages
    consistent on kernel state, memory hints, canon context, and prompt cards
    while letting each stage build its own stage_cards so each model sees
    only the data it actually needs.
    """

    runner: Any
    bundle: Any
    packet: Any
    bridge: Any
    plan: Any
    chapter_number: int
    trace: Any
    planning_hints: dict[str, Any] | None
    reading_power_hint: dict[str, Any] | None
    draft_step: Any
    target_word_count: int
    budgeted_plan: Any
    draft_memory_hints: dict[str, Any]
    draft_canon: Any
    prev_known_issues: Any
    focus_ids: list[str]
    element_selection_payload: dict[str, Any] | None
    weak_senses: list[str]
    draft_kernel_context: dict[str, Any]
    chapter_repair_kernel_context: dict[str, Any]
    wave_kernel_context: dict[str, Any]
    chapter_position: dict[str, Any] | None = None


@dataclass(frozen=True)
class DraftArtifacts:
    """Result of the DRAFT stage.

    Carries the draft text and the chapter_repair report.  WAVE consumes
    ``current_text`` and produces the woven prose consumed by Review.
    """

    current_text: str
    draft_meta: dict[str, Any]
    chapter_repair_report: Any | None


async def prepare_generate_context(
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
) -> GenerateContext:
    """Build the shared context consumed by DRAFT and WAVE.

    Centralizes: kernel composer + 3 contexts (draft / chapter_repair / wave),
    target word count, budgeted plan, draft memory hints, trimmed canon, prev
    known issues, blueprint focus, element selection, weak senses.  The actual
    stage_cards (draft vs wave) are built inside each stage so each model sees
    only the data it needs.
    """
    from novel_forge.pipeline.steps.draft_step import DraftStep

    draft_step = DraftStep(
        runner._router,
        runner._builder,
        settings=runner._settings,
        trace=trace,
        on_step=getattr(runner, "on_step", None),
    )
    kernel_composer = await load_story_kernel_composer(runner, bundle)
    draft_kernel_context = (
        kernel_composer.compose_draft_input(chapter_number) if kernel_composer is not None else {}
    )
    chapter_repair_kernel_context = (
        kernel_composer.compose_check_chapter_input(chapter_number)
        if kernel_composer is not None
        else {}
    )
    wave_kernel_context = (
        kernel_composer.compose_edit_input(chapter_number) if kernel_composer is not None else {}
    )
    target_word_count = int(getattr(bundle.chapter_outline, "expected_word_count", 0) or 0)
    budgeted_plan = _ensure_scene_word_budgets(plan, target_word_count)
    draft_memory_hints = await _collect_draft_memory_hints(
        runner,
        bundle,
        budgeted_plan,
        bridge,
        chapter_number,
        planning_hints=planning_hints,
    )
    draft_canon = _trim_canon_context(packet.canon_context)
    prev_known_issues = load_prev_known_issues(runner, bundle, chapter_number)

    _bp = getattr(bundle, "blueprint", None)
    focus_ids, _ = _resolve_active_element_focus_ids(
        bundle.chapter_outline,
        runner._storage,
        bundle.layout,
        chapter_number=chapter_number,
    )
    element_selection_payload: dict[str, Any] | None = None
    if _bp and getattr(_bp, "element_selection", None):
        element_selection_payload = _bp.element_selection.model_dump(mode="json")

    from novel_forge.pipeline.long.context import detect_weak_senses

    weak_senses = list(getattr(bundle, "weak_senses", []) or [])
    auto_weak_senses = detect_weak_senses(
        runner._storage,
        bundle.layout,
        chapter_number,
    )
    if auto_weak_senses:
        weak_senses = list(set(weak_senses) | set(auto_weak_senses))
    chapter_position = build_chapter_position(getattr(bundle, "outline", None), chapter_number)

    return GenerateContext(
        runner=runner,
        bundle=bundle,
        packet=packet,
        bridge=bridge,
        plan=plan,
        chapter_number=chapter_number,
        trace=trace,
        planning_hints=planning_hints,
        reading_power_hint=reading_power_hint,
        draft_step=draft_step,
        target_word_count=target_word_count,
        budgeted_plan=budgeted_plan,
        draft_memory_hints=draft_memory_hints,
        draft_canon=draft_canon,
        prev_known_issues=prev_known_issues,
        focus_ids=focus_ids,
        element_selection_payload=element_selection_payload,
        weak_senses=weak_senses,
        draft_kernel_context=draft_kernel_context,
        chapter_repair_kernel_context=chapter_repair_kernel_context,
        wave_kernel_context=wave_kernel_context,
        chapter_position=chapter_position,
    )


async def generate_draft(ctx: GenerateContext) -> DraftArtifacts:
    """DRAFT stage: write per-scene prose from the approved plan.

    Runs :class:`DraftStep` (whole-chapter or scene-stitched), validates the
    output, and saves v0_draft.md.  The optional pre-WAVE chapter check is
    diagnostics-only; authoritative chapter quality checks happen after WAVE in
    the Review phase.  Does not consume QC reports, repair reports, polish, or
    humanize output -- those are downstream of Generate in the 6-phase pipeline.
    """
    from novel_forge.pipeline.steps.draft_step import DraftInput

    runner = ctx.runner
    bundle = ctx.bundle
    packet = ctx.packet
    chapter_number = ctx.chapter_number
    target_word_count = ctx.target_word_count
    budgeted_plan = ctx.budgeted_plan
    draft_canon = ctx.draft_canon
    draft_memory_hints = ctx.draft_memory_hints
    prev_known_issues = ctx.prev_known_issues
    focus_ids = ctx.focus_ids
    element_selection_payload = ctx.element_selection_payload
    weak_senses = ctx.weak_senses
    draft_kernel_context = ctx.draft_kernel_context
    chapter_repair_kernel_context = ctx.chapter_repair_kernel_context
    reading_power_hint = ctx.reading_power_hint
    trace = ctx.trace
    chapter_position = ctx.chapter_position or {}

    from novel_forge.pipeline.long.services.constraints.constraint_router import build_draft_cards
    from novel_forge.pipeline.long.services.context.source_artifacts import (
        hash_payload,
        load_stage_artifact,
        persist_stage_artifact,
    )

    draft_context: dict[str, Any] = {
        "chapter_number": chapter_number,
        "target_word_count": target_word_count,
        "chapter_position": chapter_position,
        "is_last_chapter": bool(chapter_position.get("is_last_chapter")),
        "total_chapters": int(chapter_position.get("total_chapters") or 0),
        # ── M3.5: see docs/ai_flavor_quality.md ──
        "scene_intent": _primary_scene_intent(budgeted_plan),
        "style_golden_retriever": getattr(bundle, "style_golden_retriever", None),
        "stage_cards": build_draft_cards(
            packet=packet,
            chapter_outline=bundle.chapter_outline,
            plan=budgeted_plan,
            canon_context=draft_canon,
            memory_hints=draft_memory_hints,
            style_profile=getattr(bundle, "style_profile", None),
            editorial_contract=(
                bundle.editorial_contract.model_dump(mode="json")
                if getattr(bundle, "editorial_contract", None) is not None
                else None
            ),
            editorial_readiness=getattr(bundle, "editorial_readiness", None),
            narrative_contract=getattr(bundle, "narrative_contract", None),
            reading_power_hint=reading_power_hint,
            story_bible=dump_story_bible_for_prompt(bundle.story_bible),
            element_selection=element_selection_payload,
            element_focus=focus_ids or None,
            known_issues_to_avoid=prev_known_issues,
            weak_senses=weak_senses,
            pov_hint=getattr(bundle, "pov_hint", ""),
            kernel_context=draft_kernel_context,
            chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
            chapter_position=chapter_position,
            settings=runner._settings,
        ),
    }

    scene_stitch_report: dict[str, Any] | None = None
    if getattr(runner._config, "writing_mode", "whole_chapter") == "scene_level":
        current_text, scene_stitch_report = await _draft_scene_level_text(
            runner=runner,
            bundle=bundle,
            plan=budgeted_plan,
            chapter_number=chapter_number,
            draft_step=ctx.draft_step,
            draft_context=draft_context,
            reading_power_hint=reading_power_hint,
            draft_kernel_context=draft_kernel_context,
        )
        draft = None
    else:
        draft = await ctx.draft_step.run(
            DraftInput(
                task_type=TaskType.DRAFT_CHAPTER,
                context=draft_context,
                reading_power_hint=reading_power_hint,
                kernel_context=draft_kernel_context,
                minimum_word_count=int(
                    max(
                        1,
                        int(
                            getattr(ctx.bundle.chapter_outline, "expected_word_count", 0)
                            or draft_context.get("target_word_count", 0)
                            or 0
                        )
                        * 0.70,
                    )
                ),
                reject_prompt_leaks=True,
            )
        )
        current_text = draft.text

    if not current_text or not current_text.strip():
        raise ModelGatewayError(
            f"\u7b2c {chapter_number} \u7ae0\u8349\u7a3f\u751f\u6210\u7a7a\u6587\u672c\uff0c\u65e0\u6cd5\u7ee7\u7eed\u751f\u6210\u6d41\u7a0b",
            is_transient=True,
        )
    from novel_forge.pipeline.long.services.generation.scene_writing import (
        assess_draft_plan_coverage,
    )

    draft_plan_coverage = assess_draft_plan_coverage(
        plan=budgeted_plan,
        text=current_text,
        target_word_count=target_word_count,
    )
    runner._on_step(
        "draft_plan_coverage",
        {
            "chapter": chapter_number,
            "writing_mode": getattr(runner._config, "writing_mode", "whole_chapter"),
            "weak_anchor_count": draft_plan_coverage["weak_anchor_count"],
            "missing_required_literal_count": draft_plan_coverage["missing_required_literal_count"],
            "word_count_status": draft_plan_coverage["word_count_status"],
        },
    )
    _target_wc = target_word_count
    _tcc = count_chapter_words(current_text)
    _min_acc = max(500, _target_wc * 0.3) if _target_wc > 0 else 500
    if _tcc < _min_acc:
        runner._on_step(
            "draft_short_text_warning",
            {
                "chapter": chapter_number,
                "text_chars": _tcc,
                "min_acceptable": _min_acc,
            },
        )
    if _has_prompt_leaks_local(current_text):
        runner._on_step(
            "precheck_prompt_leak",
            {
                "iteration": 0,
                "stage": "draft",
                "leaked": True,
            },
        )

    await asyncio.to_thread(
        runner._storage.save_text,
        bundle.layout.chapter_draft_path(chapter_number, 0),
        current_text,
    )
    plan_artifact = load_stage_artifact(
        runner._storage,
        bundle.layout,
        chapter_number=chapter_number,
        artifact_type="plan",
    )
    persist_stage_artifact(
        storage=runner._storage,
        layout=bundle.layout,
        project_id=getattr(bundle, "project_id", "") or "unknown",
        chapter_number=chapter_number,
        artifact_type="scene_draft",
        payload={
            "text_path": str(bundle.layout.chapter_draft_path(chapter_number, 0)),
            "text_hash": hash_payload(current_text),
            "text_chars": len(current_text),
            "target_word_count": target_word_count,
            "scene_stitch_report": scene_stitch_report or None,
            "draft_plan_coverage": draft_plan_coverage,
        },
        chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
        previous_artifact=plan_artifact,
    )
    runner._on_step(
        "draft",
        draft
        if draft is not None
        else {
            "chapter": chapter_number,
            "writing_mode": "scene_level",
            "text_chars": len(current_text),
        },
    )

    # Optional diagnostics-only check on raw DRAFT.  Disabled by default because
    # Review runs the authoritative ChapterRepairStep on the WAVE handoff.
    chapter_repair_report = None
    pre_wave_check_enabled = bool(
        getattr(runner._settings, "long_pre_wave_chapter_check_enabled", False)
    )
    if pre_wave_check_enabled and bool(
        getattr(runner._settings, "long_check_chapter_enabled", True)
    ):
        from novel_forge.pipeline.steps.check_chapter_step import (
            ChapterRepairInput,
            ChapterRepairStep,
        )

        chapter_repair_step = ChapterRepairStep(
            runner._router,
            runner._builder,
            settings=runner._settings,
            trace=trace,
        )
        chapter_repair_report = await chapter_repair_step.run(
            ChapterRepairInput(
                chapter_number=chapter_number,
                chapter_text=current_text,
                canon_context=packet.canon_context,
                character_profiles=list(getattr(packet, "character_profiles", []) or []),
                previous_chapter_ending=packet.previous_chapter_ending,
                known_prompt_markers=list(KNOWN_PROMPT_MARKERS),
                time_convention=getattr(bundle.story_bible, "time_convention", ""),
                address_rules=format_address_rules_for_prompt(bundle.story_bible),
                world_context_rules=render_world_context_rules(bundle.story_bible),
                forbidden_elements=list(getattr(budgeted_plan, "forbidden_elements", []) or []),
                forbidden_elements_soft=list(
                    getattr(budgeted_plan, "forbidden_elements_soft", []) or []
                ),
                expression_channel_records=list(
                    getattr(budgeted_plan, "expression_channel_records", []) or []
                ),
                expression_channel_detection_enabled=bool(
                    getattr(runner._settings, "expression_channel_detection_enabled", True)
                ),
                intentional_callbacks=list(
                    getattr(budgeted_plan, "intentional_callbacks", []) or []
                ),
                pov_character=getattr(bundle.chapter_outline, "pov_character", "") or "",
                scene_intents=list(getattr(budgeted_plan, "scene_intents", []) or []),
                kernel_context=chapter_repair_kernel_context,
            )
        )
        await asyncio.to_thread(
            runner._storage.save_json,
            _pre_wave_chapter_repair_report_path(bundle.layout, chapter_number),
            {
                **chapter_repair_report.model_dump(mode="json"),
                "pipeline_stage": "pre_wave_draft_check",
                "diagnostic_only": True,
                "source_text_hash": hashlib.sha256(current_text.encode("utf-8")).hexdigest(),
            },
        )
        expression_records = list(getattr(budgeted_plan, "expression_channel_records", []) or [])
        expression_errors = list(getattr(chapter_repair_report, "expression_errors", []) or [])
        if expression_records or expression_errors:
            expression_report = ExpressionRepetitionReport(
                chapter_number=chapter_number,
                records=expression_records,
                hits=[{"summary": item} for item in expression_errors],
                repair_guidance=list(getattr(chapter_repair_report, "repair_actions", []) or []),
                source_text_hash=hashlib.sha256(current_text.encode("utf-8")).hexdigest(),
            )
            await asyncio.to_thread(
                runner._storage.save_json,
                bundle.layout.expression_repetition_report_path(chapter_number),
                expression_report.model_dump(mode="json"),
            )
        runner._on_step("chapter_repair", chapter_repair_report)

    draft_meta: dict[str, Any] = {
        "text_chars": len(current_text),
        "scene_stitch_report": scene_stitch_report,
        "draft_plan_coverage": draft_plan_coverage,
    }
    return DraftArtifacts(
        current_text=current_text,
        draft_meta=draft_meta,
        chapter_repair_report=chapter_repair_report,
    )


def _build_eval_repair_hints(eval_report: Any, *, max_items: int = 5) -> str:
    """Build concise repair hint lines from eval report suggestions.

    Extracts high/medium priority repair_suggestions from an EvalReport and
    formats them as bullet points for the polish prompt. Covers aesthetic
    dimensions (pacing, engagement, style, character) that _build_polish_
    continuity_notes does not address.

    Returns empty string when there are no actionable suggestions.
    """
    suggestions = list(getattr(eval_report, "repair_suggestions", []) or [])
    if not suggestions:
        return ""

    _PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
    # Keep high + medium priority only
    filtered = [
        s
        for s in suggestions
        if _PRIORITY_ORDER.get(str(getattr(s, "priority", "medium")).lower(), 2) <= 1
    ]
    filtered.sort(
        key=lambda s: _PRIORITY_ORDER.get(str(getattr(s, "priority", "medium")).lower(), 2)
    )
    filtered = filtered[:max_items]
    if not filtered:
        return ""

    lines: list[str] = ["【评估建议（润色时请一并改进）】"]
    for s in filtered:
        dim = str(getattr(s, "dimension", "") or "").strip()
        issue = " ".join(str(getattr(s, "issue", "") or "").split()).strip()
        location = " ".join(str(getattr(s, "location", "") or "").split()).strip()
        suggestion = " ".join(str(getattr(s, "suggestion", "") or "").split()).strip()
        if not issue and not suggestion:
            continue
        prefix = f"[{dim}] " if dim else ""
        loc_tag = f"（{location}）" if location else ""
        lines.append(f"- {prefix}{issue}{loc_tag}：{suggestion}")
    return "\n".join(lines).strip() if len(lines) > 1 else ""


def _build_eval_repair_warnings(eval_report: Any, *, max_items: int = 3) -> list[str]:
    """Build warnings from high-priority repair_suggestions for user visibility."""
    suggestions = list(getattr(eval_report, "repair_suggestions", []) or [])
    if not suggestions:
        return []

    _PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
    high_priority = [
        s
        for s in suggestions
        if _PRIORITY_ORDER.get(str(getattr(s, "priority", "medium")).lower(), 2) == 0
    ]
    if not high_priority:
        return []

    warnings: list[str] = []
    for s in high_priority[:max_items]:
        issue = " ".join(str(getattr(s, "issue", "") or "").split()).strip()
        suggestion = " ".join(str(getattr(s, "suggestion", "") or "").split()).strip()
        if not issue:
            continue
        dimension = str(getattr(s, "dimension", "") or "").strip()
        dim_tag = f"[{dimension}] " if dimension else ""
        line = f"评估发现高优先级问题：{dim_tag}{issue}"
        if suggestion:
            line += f" — 建议：{suggestion}"
        warnings.append(line)

    return warnings


def build_revelation_density_warning(
    text: str,
    max_revelations: int = 2,
    *,
    _revelation_markers: tuple[str, ...] | None = None,
) -> str:
    """Build guidance when revelation markers exceed budget in chapter text."""
    if not text or not isinstance(text, str):
        return ""

    import re

    _DEFAULT_MARKERS: tuple[str, ...] = (
        "意外发现",
        "出乎意料",
        "始料未及",
        "才发现",
        "揭露",
        "揭示",
        "真相",
        "竟然",
        "原来",
    )
    markers = _revelation_markers or _DEFAULT_MARKERS
    _rev_re = re.compile("|".join(re.escape(m) for m in markers))

    revelation_count = len(_rev_re.findall(text))
    if revelation_count <= max_revelations:
        return ""

    if revelation_count <= max_revelations * 2:
        level = "偏高"
    else:
        level = "严重超标"

    return (
        f"揭示密度{level}：检测到 {revelation_count} 处揭示标记"
        f"（上限 {max_revelations}），"
        "建议减少依赖\u201c原来/竟然/才发现\u201d等直白揭示语，"
        "改用场景动作和细节暗示推进剧情。"
    )


def _build_polish_continuity_notes(
    review: "ChapterReviewArtifacts",
    *,
    max_items: int = 8,
) -> str:
    """Build compact repair notes for the polish prompt from review artifacts."""

    lines: list[str] = []

    continuity_issues = list(getattr(review.continuity_report, "issues", []) or [])
    if continuity_issues:
        lines.append("【跨章连贯性问题】")
        for issue in continuity_issues[:max_items]:
            severity = str(getattr(issue, "severity", "") or "medium").lower()
            summary = " ".join(str(getattr(issue, "summary", "") or "").split()).strip()
            if summary:
                lines.append(f"- [{severity}] {summary}")

    chapter_repair = review.chapter_repair_report
    if chapter_repair is not None:
        chapter_lines: list[str] = []
        for label, items in (
            ("提示词泄露", getattr(chapter_repair, "prompt_leaks", [])),
            ("事实错误", getattr(chapter_repair, "factual_errors", [])),
            ("表达问题", getattr(chapter_repair, "expression_errors", [])),
        ):
            for item in list(items or [])[:2]:
                text = " ".join(str(item or "").split()).strip()
                if text:
                    chapter_lines.append(f"- [{label}] {text}")
        if chapter_lines:
            if lines:
                lines.append("")
            lines.append("【本章校验遗留】")
            lines.extend(chapter_lines[:max_items])

    causal_report = review.causal_report
    if causal_report is not None:
        causal_lines: list[str] = []
        for issue in list(getattr(causal_report, "issues", []) or []):
            severity = str(getattr(issue, "severity", "") or "").lower()
            if severity not in {"critical", "high"}:
                continue
            summary = " ".join(str(getattr(issue, "summary", "") or "").split()).strip()
            if summary:
                causal_lines.append(f"- [{severity}] {summary}")
            if len(causal_lines) >= max(2, max_items // 2):
                break
        if causal_lines:
            if lines:
                lines.append("")
            lines.append("【因果链高优先级问题】")
            lines.extend(causal_lines)

    return "\n".join(lines).strip()


def _append_compact_hint(
    lines: list[str],
    seen: set[str],
    label: str,
    value: Any,
    *,
    max_items: int,
    max_line_chars: int = 140,
) -> None:
    if len(lines) >= max_items:
        return
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return
    if text in seen:
        return
    seen.add(text)
    if len(text) > max_line_chars:
        text = f"{text[:max_line_chars].rstrip()}..."
    lines.append(f"- [{label}] {text}")


def _build_post_repair_polish_hints(
    *,
    chapter_repair_report: Any | None,
    reading_power_report: Any | None,
    warnings: list[str] | None,
    expression_channel_records: list[dict[str, Any]] | None = None,
    max_items: int = 6,
    max_chars: int = 900,
) -> str:
    """Build a small post-repair polish capsule without expanding audit schemas."""

    lines: list[str] = []
    seen: set[str] = set()

    if chapter_repair_report is not None:
        for item in list(getattr(chapter_repair_report, "expression_errors", []) or [])[:3]:
            _append_compact_hint(lines, seen, "表达问题", item, max_items=max_items)
        for item in list(getattr(chapter_repair_report, "repair_actions", []) or [])[:3]:
            _append_compact_hint(lines, seen, "修复建议", item, max_items=max_items)

    if reading_power_report is not None:
        for item in list(getattr(reading_power_report, "suggestions", []) or [])[:2]:
            _append_compact_hint(lines, seen, "追读力", item, max_items=max_items)
        next_reason = getattr(reading_power_report, "next_chapter_reason", "") or ""
        _append_compact_hint(lines, seen, "章尾牵引", next_reason, max_items=max_items)

    for item in list(warnings or [])[:3]:
        _append_compact_hint(lines, seen, "流程警告", item, max_items=max_items)

    for item in list(expression_channel_records or [])[:3]:
        if not isinstance(item, dict):
            continue
        label = str(item.get("text") or item.get("channel_id") or "").strip()
        if not label:
            continue
        axes = "、".join(str(axis) for axis in list(item.get("replacement_axes", []) or [])[:4])
        allowed_when = str(item.get("allowed_when") or "").strip()
        recent_hits = list(item.get("recent_semantic_hits", []) or [])
        hint = label
        if axes:
            hint += f"；替代方向：{axes}"
        if allowed_when:
            hint += f"；保留边界：{allowed_when}"
        if recent_hits and isinstance(recent_hits[0], dict):
            quote = str(recent_hits[0].get("quote") or "").strip()
            chapter = str(recent_hits[0].get("chapter") or "").strip()
            if quote:
                hint += (
                    f"；历史近例：第{chapter}章“{quote}”" if chapter else f"；历史近例：“{quote}”"
                )
        _append_compact_hint(lines, seen, "表达冷却", hint, max_items=max_items)

    if not lines:
        return ""

    result_lines = ["【审计修复后精修重点】", *lines[:max_items]]
    text = "\n".join(result_lines).strip()
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}..."


def _paragraph_number_for_quote(text: str, quote: str) -> int:
    paragraphs = [item for item in re.split(r"\n\s*\n", text or "") if item.strip()]
    for index, paragraph in enumerate(paragraphs, start=1):
        if quote in paragraph:
            return index
    return 0


def _build_pre_polish_patch_issues(
    current_text: str,
    chapter_repair_report: Any | None,
    *,
    max_items: int = 2,
) -> list[Any]:
    """Build narrow patch issues for exact repeated-text findings only."""

    if chapter_repair_report is None:
        return []

    from novel_forge.pipeline.steps.repair.patch_bridge import PatchCompatIssue

    issues: list[Any] = []
    seen: set[str] = set()
    duplicate_pattern = re.compile(r"重复\s*\d+\s*次[:：]\s*(?P<quote>[^。！？\n]{4,160})")
    for raw_item in list(getattr(chapter_repair_report, "expression_errors", []) or []):
        text = " ".join(str(raw_item or "").split()).strip()
        match = duplicate_pattern.search(text)
        if not match:
            continue
        quote = match.group("quote").strip().strip("“”\"'「」")
        if not quote or quote in seen or current_text.count(quote) < 2:
            continue
        seen.add(quote)
        paragraph_number = _paragraph_number_for_quote(current_text, quote)
        issues.append(
            PatchCompatIssue(
                issue_id=f"pre_polish_text_repetition_{len(issues) + 1}",
                severity="medium",
                summary=f"整句级重复：{quote}",
                evidence=quote,
                issue_type="text_repetition",
                repair_surface="local_patch",
                location=f"第 {paragraph_number} 段" if paragraph_number else "",
                paragraph_start=paragraph_number,
                paragraph_end=paragraph_number,
                location_confidence=0.92 if paragraph_number else 0.0,
                anchor_type="exact",
                evidence_quote=quote,
                fix_mode="rewrite",
                fix_suggestion=(
                    "只保留最有叙事功能的一处重复信息；另一处改成动作、感知、留白或删除。"
                    "不得改变事实、角色认知和因果。"
                ),
            )
        )
        if len(issues) >= max_items:
            break
    return issues


async def _apply_pre_polish_precision_patch(
    context: "ChapterExecutionContext",
    prepared: Any,
    current_text: str,
    trace: "PipelineTrace",
    *,
    chapter_repair_report: Any | None,
    polish_kernel_context: dict[str, Any] | None,
) -> str:
    issues = _build_pre_polish_patch_issues(current_text, chapter_repair_report)
    if not issues:
        return current_text

    from novel_forge.pipeline.long.services.constraints.cognitive_constraints import (
        cognitive_constraints_from_source_cards,
    )
    from novel_forge.pipeline.long.services.context.source_artifacts import (
        project_stage_source_cards,
    )
    from novel_forge.pipeline.steps.patch_step import ChapterPatchStep, PatchInput

    bundle = prepared.bundle
    chapter_number = bundle.chapter_outline.chapter_number
    step = ChapterPatchStep(
        context.router,
        context.builder,
        settings=context.settings,
        trace=trace,
        on_step=getattr(context, "on_step", None),
    )
    source_cards = (
        project_stage_source_cards(bundle.chapter_source_slice, stage="polish")
        if getattr(bundle, "chapter_source_slice", None) is not None
        else {}
    )
    result = await step.run(
        # Fixed chapter constraints stay structured and complete; no vector lookup.
        PatchInput(
            chapter_number=chapter_number,
            chapter_text=current_text,
            issues=issues,
            context_size=1,
            must_fix_summaries=[issue.summary for issue in issues],
            style_profile=getattr(bundle, "style_profile", None),
            kernel_context=polish_kernel_context,
            cognitive_constraints=cognitive_constraints_from_source_cards(source_cards),
        )
    )
    context.on_step(
        "post_repair_precision_patch",
        {
            "chapter": chapter_number,
            "issues": len(issues),
            "patches_applied": result.patches_applied,
            "patches_attempted": result.patches_attempted,
            "fallback": result.fallback,
        },
    )
    return result.revised_text if result.patches_applied > 0 else current_text


def _replace_review_artifacts(review: Any, **updates: Any) -> Any:
    """Return a review-like object with updated fields while preserving all others."""

    if dataclasses.is_dataclass(review):
        return dataclasses.replace(review, **updates)
    data = dict(getattr(review, "__dict__", {}) or {})
    data.update(updates)
    return SimpleNamespace(**data)


def _build_polish_projection_cards(
    context: "ChapterExecutionContext", prepared: Any
) -> dict[str, Any]:
    """Build the small stage-card projection consumed by PolishStep."""

    from novel_forge.pipeline.long.services.constraints.constraint_router import build_polish_cards

    bundle = prepared.bundle
    editorial_contract = getattr(bundle, "editorial_contract", None)
    return build_polish_cards(
        packet=getattr(prepared, "packet", None),
        chapter_outline=bundle.chapter_outline,
        bridge=getattr(prepared, "bridge", None),
        plan=getattr(prepared, "plan", None),
        style_profile=getattr(bundle, "style_profile", None),
        editorial_contract=(
            editorial_contract.model_dump(mode="json")
            if editorial_contract is not None and hasattr(editorial_contract, "model_dump")
            else editorial_contract
        ),
        memory_hints=getattr(prepared, "memory_hints", None),
        reading_power_hint=getattr(prepared, "reading_power_hint", None),
        pov_hint=getattr(bundle.chapter_outline, "pov_character", ""),
        chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
        settings=context.settings,
    )


async def run_post_repair_polish_layer(
    context: "ChapterExecutionContext",
    prepared: Any,
    current_text: str,
    trace: "PipelineTrace",
    *,
    chapter_repair_report: Any | None = None,
    continuity_report: Any | None = None,
    causal_report: Any | None = None,
    reading_power_report: Any | None = None,
    warnings: list[str] | None = None,
    trigger_reason: str = "post_repair",
) -> tuple[str, bool]:
    """Run the primary polish layer after repairs and before humanize."""

    from novel_forge.pipeline.steps.polish_step import PolishInput, PolishStep

    bundle = prepared.bundle
    chapter_number = bundle.chapter_outline.chapter_number
    kernel_composer = await load_story_kernel_composer(
        SimpleNamespace(_settings=context.settings),
        bundle,
    )
    polish_kernel_context = (
        kernel_composer.compose_polish_input(chapter_number) if kernel_composer is not None else {}
    )
    current_text = await _apply_pre_polish_precision_patch(
        context,
        prepared,
        current_text,
        trace,
        chapter_repair_report=chapter_repair_report,
        polish_kernel_context=polish_kernel_context,
    )
    target_word_count = int(getattr(bundle.chapter_outline, "expected_word_count", 0) or 0)
    word_count_guidance = build_word_count_warning(current_text, target_word_count)
    repair_hints = _build_post_repair_polish_hints(
        chapter_repair_report=chapter_repair_report,
        reading_power_report=reading_power_report,
        warnings=warnings,
        expression_channel_records=list(
            (getattr(prepared, "memory_hints", {}) or {}).get("expression_channel_records", [])
            or []
        ),
    )
    continuity_notes = _build_polish_continuity_notes(
        SimpleNamespace(
            continuity_report=continuity_report,
            chapter_repair_report=chapter_repair_report,
            causal_report=causal_report,
        ),
        max_items=6,
    )

    step = PolishStep(
        context.router,
        context.builder,
        settings=context.settings,
        trace=trace,
        on_step=getattr(context, "on_step", None),
    )
    polish_cards = _build_polish_projection_cards(context, prepared)
    result = await step.run(
        PolishInput(
            chapter_number=chapter_number,
            chapter_title=bundle.chapter_outline.title,
            chapter_text=current_text,
            tone=bundle.story_bible.tone,
            genre=getattr(bundle.story_bible, "genre", ""),
            pov_character=getattr(bundle.chapter_outline, "pov_character", ""),
            eval_summary="",
            repair_hints=repair_hints,
            continuity_notes=continuity_notes,
            style_profile=getattr(bundle, "style_profile", None),
            target_word_count=target_word_count,
            word_count_guidance=word_count_guidance,
            kernel_context=polish_kernel_context,
            chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
            editorial=polish_cards.get("editorial"),
            quality=polish_cards.get("quality"),
            memory=polish_cards.get("memory"),
            style_capsule=polish_cards.get("style"),
        )
    )
    polished_text = result.polished_text or current_text
    changed = polished_text != current_text
    context.on_step(
        "polish",
        {
            "stage": "post_repair",
            "trigger": trigger_reason,
            "chapter": chapter_number,
            "original_wc": result.original_word_count,
            "polished_wc": result.polished_word_count,
            "changed": changed,
        },
    )
    return polished_text, changed


async def apply_polish_pass(
    context: "ChapterExecutionContext",
    review: "ChapterReviewArtifacts",
    trace: "PipelineTrace",
) -> "ChapterReviewArtifacts":
    """Run an optional literary polish pass on the reviewed chapter text.

    Args:
        context: Full execution context.
        review: Current review artifacts.
        trace: PipelineTrace for observability.

    Returns:
        Updated ChapterReviewArtifacts with polished text.
    """
    from novel_forge.pipeline.steps.polish_step import PolishInput, PolishStep

    bundle = review.prepared.bundle
    chapter_number = bundle.chapter_outline.chapter_number

    eval_summary = ""
    repair_hints = ""
    if review.eval_report is not None:
        eval_summary = getattr(review.eval_report, "summary", "") or ""
        repair_hints = _build_eval_repair_hints(review.eval_report)

    continuity_notes = _build_polish_continuity_notes(review)
    kernel_composer = await load_story_kernel_composer(
        SimpleNamespace(_settings=context.settings),
        bundle,
    )
    polish_kernel_context = (
        kernel_composer.compose_polish_input(chapter_number) if kernel_composer is not None else {}
    )

    step = PolishStep(
        context.router,
        context.builder,
        settings=context.settings,
        trace=trace,
        on_step=getattr(context, "on_step", None),
    )
    polish_cards = _build_polish_projection_cards(context, review.prepared)
    result = await step.run(
        PolishInput(
            chapter_number=chapter_number,
            chapter_title=bundle.chapter_outline.title,
            chapter_text=review.current_text,
            tone=bundle.story_bible.tone,
            genre=getattr(bundle.story_bible, "genre", ""),
            pov_character=getattr(bundle.chapter_outline, "pov_character", ""),
            eval_summary=eval_summary,
            repair_hints=repair_hints,
            continuity_notes=continuity_notes,
            style_profile=getattr(bundle, "style_profile", None),
            kernel_context=polish_kernel_context,
            chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
            editorial=polish_cards.get("editorial"),
            quality=polish_cards.get("quality"),
            memory=polish_cards.get("memory"),
            style_capsule=polish_cards.get("style"),
        )
    )
    context.on_step(
        "polish",
        {
            "chapter": chapter_number,
            "original_wc": result.original_word_count,
            "polished_wc": result.polished_word_count,
        },
    )

    return _replace_review_artifacts(
        review,
        current_text=result.polished_text,
    )
