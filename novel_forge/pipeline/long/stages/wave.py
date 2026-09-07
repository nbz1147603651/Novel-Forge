"""Wave stage: single-pass scene weaving for long-form chapter drafts.

The WAVE stage is the second half of the Generate phase in the 6-phase long-form
pipeline.  Where DRAFT writes per-scene prose from the approved plan, WAVE
reads the assembled draft and weaves scenes into a coherent chapter: inserts
transitions, hits cross-scene references from ``cross_scene_intent``, and
respects the per-scene ``pacing_curve``.

WAVE runs **exactly once** (no multi-round edit loop).  Post-condition failures
are recorded as warnings on :class:`WovenArtifacts` and never raise; this is
intentional -- the Review / Polish / Humanize stages downstream are the right
place to surface quality issues.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from novel_forge.core.domain.guardrails import normalize_invalid_time_markers
from novel_forge.core.domain.world_context import (
    dump_story_bible_for_prompt,
)
from novel_forge.core.guidance import required_cross_scene_ref
from novel_forge.pipeline.long.stages.draft import GenerateContext

if TYPE_CHECKING:
    pass


@dataclass(frozen=True)
class WovenArtifacts:
    """Result of the WAVE stage.

    Carries the woven prose and the wave post-condition metadata used by
    chapter_flow.py for logging and the checkpoint state.
    """

    current_text: str
    performed_edits: int
    wave_meta: dict[str, Any]


def _style_scene_intent_for_wave(scene_intents: list[dict[str, Any]]) -> dict[str, Any]:
    if not scene_intents:
        return {}
    scene = scene_intents[0]
    if not isinstance(scene, dict):
        return {}
    return {
        "emotional_tone": scene.get("emotional_beat", "") or scene.get("emotional_tone", ""),
        "scene_action": scene.get("purpose", "") or scene.get("scene_action", ""),
        "setting": scene.get("location", "") or scene.get("setting", ""),
        "pov_keywords": scene.get("pov_character", "") or scene.get("pov_keywords", ""),
        "topic": scene.get("summary", "") or scene.get("topic", ""),
        "characters_involved": list(scene.get("required_characters", []) or []),
    }


async def apply_wave(
    ctx: GenerateContext,
    draft_text: str,
    *,
    scene_stitch_report: dict[str, Any] | None = None,
    draft_plan_coverage: dict[str, Any] | None = None,
) -> WovenArtifacts:
    """WAVE stage: single-pass scene weaving on the DRAFT text.

    Builds the WAVE-specific stage_cards (full plan + full character voices +
    cross_scene_intent) and runs :class:`WaveStep` once.  The WAVE pass replaces
    the legacy multi-round edit loop: edits always equals 1, and post-condition
    failures are recorded as warnings, not exceptions.

    Args:
        ctx: The shared :class:`GenerateContext` from ``prepare_generate_context``.
        draft_text: The text produced by :func:`generate_draft` (DRAFT stage).
        scene_stitch_report: Optional scene-level diagnostics from local stitching.
        draft_plan_coverage: Optional local plan-coverage diagnostic available
            to both whole-chapter and scene-level drafting.

    Returns:
        :class:`WovenArtifacts` with the woven prose, ``performed_edits=1``,
        and the wave_meta dict (warnings, cross_ref_hits, scene_transitions,
        motif_weave_log, final_word_count).
    """
    from novel_forge.pipeline.long.services.context.source_artifacts import (
        hash_payload,
        load_stage_artifact,
        persist_stage_artifact,
    )
    from novel_forge.pipeline.long.services.plan_obligations import (
        assess_plan_literal_coverage,
    )
    from novel_forge.pipeline.steps.wave_step import WaveInput, WaveStep, _anchor_words

    runner = ctx.runner
    bundle = ctx.bundle
    packet = ctx.packet
    bridge = ctx.bridge
    chapter_number = ctx.chapter_number
    target_word_count = ctx.target_word_count
    budgeted_plan = ctx.budgeted_plan
    draft_canon = ctx.draft_canon
    draft_memory_hints = ctx.draft_memory_hints
    prev_known_issues = ctx.prev_known_issues
    focus_ids = ctx.focus_ids
    element_selection_payload = ctx.element_selection_payload
    weak_senses = ctx.weak_senses
    wave_kernel_context = ctx.wave_kernel_context
    reading_power_hint = ctx.reading_power_hint
    trace = ctx.trace
    chapter_position = ctx.chapter_position or {}

    from novel_forge.pipeline.long.services.constraints.constraint_router import build_wave_cards

    cross_scene_intent: dict[str, Any] = {}
    raw_csi = getattr(budgeted_plan, "cross_scene_intent", None)
    if raw_csi is not None:
        if isinstance(raw_csi, dict):
            cross_scene_intent = dict(raw_csi)
        else:
            cross_scene_intent = raw_csi.model_dump(mode="json")
    scene_intents_for_wave: list[dict[str, Any]] = []
    for si in list(getattr(budgeted_plan, "scene_intents", []) or []):
        if isinstance(si, dict):
            scene_intents_for_wave.append(si)
        elif hasattr(si, "model_dump"):
            scene_intents_for_wave.append(si.model_dump(mode="json"))
        else:
            scene_intents_for_wave.append(si)
    cross_ref_total = len(
        [
            ref
            for ref in list(cross_scene_intent.get("cross_scene_references", []) or [])
            if required_cross_scene_ref(ref)
        ]
    )
    cross_ref_expected = [
        {
            "index": idx,
            "from_scene": str(ref.get("from_scene") or "").strip(),
            "to_scene": str(ref.get("to_scene") or "").strip(),
            "ref_type": str(ref.get("ref_type") or "callback").strip(),
            "requirement": ref.get("requirement", {}),
            "description": str(ref.get("description") or "").strip(),
        }
        for idx, ref in enumerate(
            [
                ref
                for ref in list(cross_scene_intent.get("cross_scene_references", []) or [])
                if required_cross_scene_ref(ref)
            ],
            start=1,
        )
    ]
    scene_anchor_total = len(
        [
            scene
            for scene in scene_intents_for_wave
            if isinstance(scene, dict) and str(scene.get("summary") or "").strip()
        ]
    )
    scene_anchor_expected = [
        {
            "index": idx,
            "scene_id": str(scene.get("scene_id") or f"scene_{idx:02d}").strip(),
            "summary": str(scene.get("summary") or "").strip(),
            "anchor_words": _anchor_words(str(scene.get("summary") or "").strip()),
        }
        for idx, scene in enumerate(
            [
                scene
                for scene in scene_intents_for_wave
                if isinstance(scene, dict) and str(scene.get("summary") or "").strip()
            ],
            start=1,
        )
    ]
    pov_character = (
        getattr(bundle.chapter_outline, "pov_character", "")
        or cross_scene_intent.get("pov_character", "")
        or ""
    )

    wave_context: dict[str, Any] = {
        "chapter_number": chapter_number,
        "target_word_count": target_word_count,
        "chapter_position": chapter_position,
        "is_last_chapter": bool(chapter_position.get("is_last_chapter")),
        "total_chapters": int(chapter_position.get("total_chapters") or 0),
        "scene_stitch_report": scene_stitch_report or None,
        "draft_plan_coverage": draft_plan_coverage or None,
        "scene_intent": _style_scene_intent_for_wave(scene_intents_for_wave),
        "style_golden_retriever": getattr(bundle, "style_golden_retriever", None),
        "stage_cards": build_wave_cards(
            packet=packet,
            chapter_outline=bundle.chapter_outline,
            bridge=bridge,
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
            kernel_context=wave_kernel_context,
            chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
            chapter_position=chapter_position,
            settings=runner._settings,
        ),
    }

    wave_step = WaveStep(
        runner._router,
        runner._builder,
        settings=runner._settings,
        trace=trace,
        on_step=getattr(runner, "on_step", None),
    )
    wave_result = await wave_step.run(
        WaveInput(
            chapter_text=draft_text,
            context=wave_context,
            pov_character=pov_character,
            target_word_count=target_word_count,
            cross_scene_intent=cross_scene_intent,
            scene_intents=scene_intents_for_wave,
            reading_power_hint=reading_power_hint,
            kernel_context=wave_kernel_context,
        )
    )
    woven_prose = wave_result.woven_prose
    wave_meta: dict[str, Any] = {
        "warnings": list(wave_result.warnings),
        "cross_ref_hits": list(wave_result.cross_ref_hits),
        "scene_transitions_added": list(wave_result.scene_transitions_added),
        "motif_weave_log": list(wave_result.motif_weave_log),
        "final_word_count": int(wave_result.final_word_count),
        "scenes_woven": len(scene_intents_for_wave),
        "cross_ref_total": cross_ref_total,
        "cross_ref_expected": cross_ref_expected,
        "scene_anchor_total": scene_anchor_total,
        "scene_anchor_expected": scene_anchor_expected,
        "scene_stitch_report": scene_stitch_report or None,
        "draft_plan_coverage": draft_plan_coverage or None,
    }
    current_text = draft_text
    if woven_prose and woven_prose.strip() and woven_prose != current_text:
        current_text = woven_prose
    normalized_text, time_marker_replacements = normalize_invalid_time_markers(current_text)
    if time_marker_replacements:
        current_text = normalized_text
        wave_meta["warnings"].append(
            "已规范化非法时辰刻度："
            + "、".join(
                f"{item['marker']}→{item['replacement']}" for item in time_marker_replacements[:4]
            )
        )
        runner._on_step(
            "time_marker_normalized",
            {
                "chapter": chapter_number,
                "stage": "wave",
                "count": len(time_marker_replacements),
                "replacements": time_marker_replacements[:8],
            },
        )
    wave_meta["woven_chars"] = len(current_text)
    wave_meta["plan_obligation_coverage"] = assess_plan_literal_coverage(
        budgeted_plan,
        current_text,
    )
    wave_path_fn = getattr(bundle.layout, "chapter_wave_draft_path", None)
    save_text = getattr(runner._storage, "save_text", None)
    if callable(wave_path_fn) and callable(save_text):
        await asyncio.to_thread(
            save_text,
            wave_path_fn(chapter_number),
            current_text,
        )
    draft_artifact = load_stage_artifact(
        runner._storage,
        bundle.layout,
        chapter_number=chapter_number,
        artifact_type="scene_draft",
    )
    persist_stage_artifact(
        storage=runner._storage,
        layout=bundle.layout,
        project_id=getattr(bundle, "project_id", "") or "unknown",
        chapter_number=chapter_number,
        artifact_type="wave",
        payload={
            "text_path": str(wave_path_fn(chapter_number)) if callable(wave_path_fn) else "",
            "text_hash": hash_payload(current_text),
            "text_chars": len(current_text),
            "target_word_count": target_word_count,
            "wave_meta": wave_meta,
        },
        chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
        previous_artifact=draft_artifact,
    )
    runner._on_step(
        "wave",
        {
            "chapter": chapter_number,
            "woven_chars": len(current_text),
            "artifact": "v1_wave.md" if callable(wave_path_fn) else "",
            "warnings": list(wave_result.warnings),
            "cross_ref_hits": list(wave_result.cross_ref_hits),
            "scenes_woven": len(scene_intents_for_wave),
        },
    )

    return WovenArtifacts(
        current_text=current_text,
        performed_edits=1,
        wave_meta=wave_meta,
    )
