"""Deduplication and pronoun consistency checks."""

from __future__ import annotations

from typing import Any

from novel_forge.core.domain.guardrails import detect_self_repetition
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.steps.pronoun_check_step import (
    PronounCheckResult,
    PronounCheckStep,
)

_logger = get_logger("pipeline.dedup_pronoun")


def run_final_dedup(
    runner: Any,
    current_text: str,
    previous_chapter_ending: str,
) -> str:
    """Run final opening deduplication to remove repeated text from previous chapter."""
    cleaned_final_text, final_dedup = runner._remove_opening_echo_from_previous(
        current_text,
        previous_chapter_ending,
    )
    if final_dedup:
        current_text = cleaned_final_text
        runner._on_step("opening_dedup", {"stage": "final_chapter", **final_dedup})
    return current_text


def run_self_repetition_check(
    runner: Any,
    current_text: str,
) -> tuple[str, list[dict[str, Any]]]:
    """Detect and strip near-duplicate paragraphs within the chapter."""
    duplicates = detect_self_repetition(current_text)
    if not duplicates:
        return current_text, []

    cleaned = current_text
    for dup in sorted(duplicates, key=lambda d: d["start"], reverse=True):
        start = int(dup["start"])
        end = int(dup["end"])
        cleaned = cleaned[:start] + cleaned[end:]

    import re as _re

    cleaned = _re.sub(r"\n{3,}", "\n\n", cleaned)
    runner._on_step(
        "self_repetition_check",
        {
            "duplicates_found": len(duplicates),
            "removed_segments": [d["snippet"] for d in duplicates[:5]],
        },
    )
    return cleaned, duplicates


async def run_pronoun_check(
    runner: Any,
    bundle: Any,
    packet: Any,
    current_text: str,
    chapter_number: int,
    trace: Any,
    *,
    chapter_plan: Any | None = None,
    kernel_context: list[dict[str, Any]] | None = None,
) -> tuple[str, PronounCheckResult]:
    """Run pronoun consistency check and optionally trigger targeted repair."""
    pronoun_step = PronounCheckStep(
        runner._router,
        runner._builder,
        settings=runner._settings,
        trace=trace,
    )

    if (
        chapter_plan is not None
        and not isinstance(chapter_plan, dict)
        and hasattr(chapter_plan, "model_dump")
    ):
        chapter_plan = chapter_plan.model_dump(mode="json")
    if not isinstance(chapter_plan, dict) or not chapter_plan:
        chapter_plan = {}
        layout = getattr(bundle, "layout", None)
        plan_loader = getattr(layout, "chapter_plan_path", None) if layout is not None else None
        if callable(plan_loader):
            try:
                plan_path = plan_loader(chapter_number)
                if runner._storage.exists(plan_path):
                    loaded_plan = runner._storage.load_json(plan_path)
                    if isinstance(loaded_plan, dict):
                        chapter_plan = loaded_plan
            except Exception:
                chapter_plan = {}
    if not chapter_plan.get("pov_character"):
        outline_pov = getattr(bundle.chapter_outline, "pov_character", "") or ""
        if outline_pov:
            chapter_plan = dict(chapter_plan)
            chapter_plan["pov_character"] = outline_pov
    if not chapter_plan.get("involved_characters"):
        involved: list[str] = []
        scenes = chapter_plan.get("scene_intents", [])
        if isinstance(scenes, list):
            for scene in scenes:
                if not isinstance(scene, dict):
                    continue
                for name in scene.get("required_characters", []) or []:
                    text = str(name or "").strip()
                    if text and text not in involved:
                        involved.append(text)
        outline_involved = list(getattr(bundle.chapter_outline, "involved_characters", []) or [])
        for name in outline_involved:
            text = str(name or "").strip()
            if text and text not in involved:
                involved.append(text)
        if involved:
            chapter_plan = dict(chapter_plan)
            chapter_plan["involved_characters"] = involved

    character_bible_payload: dict[str, Any] = {}
    raw_character_bible = getattr(bundle, "character_bible", None)
    raw_characters = list(getattr(raw_character_bible, "characters", []) or [])
    if raw_characters:
        compact_chars: list[dict[str, str]] = []
        for item in raw_characters:
            name = str(getattr(item, "name", "") or "").strip()
            gender = str(getattr(item, "gender", "") or "").strip()
            if name and gender:
                compact_chars.append({"name": name, "gender": gender})
        if compact_chars:
            character_bible_payload = {"characters": compact_chars}

    # Load previous chapter pronoun reports for cross-chapter consistency
    _previous_reports: list[dict[str, Any]] = []
    _layout = getattr(bundle, "layout", None)
    if _layout is not None and chapter_number > 1:
        _report_loader = getattr(_layout, "pronoun_report_path", None)
        if callable(_report_loader):
            for prev_ch in range(max(1, chapter_number - 3), chapter_number):
                try:
                    _prev_path = _report_loader(prev_ch)
                    if runner._storage.exists(_prev_path):
                        _prev_data = runner._storage.load_json(_prev_path)
                        if isinstance(_prev_data, dict):
                            _previous_reports.append(_prev_data)
                except Exception:
                    pass

    check_ctx: dict[str, Any] = {
        "chapter_text": current_text,
        "chapter_number": chapter_number,
        "chapter_plan": chapter_plan,
        "character_bible": character_bible_payload,
        "canon_context": (
            packet.canon_context
            if isinstance(packet.canon_context, dict)
            else {
                "characters": {
                    name: (state.model_dump(mode="json") if hasattr(state, "model_dump") else state)
                    for name, state in getattr(packet.canon_context, "characters", {}).items()
                }
            }
        ),
        "previous_pronoun_reports": _previous_reports,
    }
    if kernel_context is not None:
        check_ctx["kernel_context"] = kernel_context

    pronoun_report = await pronoun_step.run(check_ctx)
    runner._on_step("pronoun_check", pronoun_report)

    auto_mode = str(getattr(runner._settings, "pronoun_autofix_mode", "off") or "off")
    if auto_mode not in {"off", "pov_only", "pov_or_many"}:
        auto_mode = "off"

    if (
        auto_mode != "off"
        and pronoun_report.get("requires_rewrite")
        and pronoun_report.get("issues")
    ):
        pov_issues = int(pronoun_report.get("pov_issues", 0) or 0)
        total_issues = int(pronoun_report.get("total_issues", 0) or 0)
        should_repair = False
        if auto_mode == "pov_only":
            should_repair = pov_issues > 0
        elif auto_mode == "pov_or_many":
            should_repair = pov_issues > 0 or total_issues > 2

        if should_repair:
            before_text = current_text
            before_pov = pov_issues
            before_total = total_issues
            revised_text = await _repair_pronouns(
                runner=runner,
                bundle=bundle,
                packet=packet,
                current_text=current_text,
                chapter_number=chapter_number,
                pronoun_report=pronoun_report,
                pronoun_step=pronoun_step,
                trace=trace,
            )

            recheck_ctx = dict(check_ctx)
            recheck_ctx["chapter_text"] = revised_text
            recheck_report = await pronoun_step.run(recheck_ctx)
            runner._on_step("pronoun_repair_recheck", recheck_report)

            after_pov = int(recheck_report.get("pov_issues", 0) or 0)
            after_total = int(recheck_report.get("total_issues", 0) or 0)
            improved = (after_pov < before_pov) or (after_total < before_total)

            if revised_text != before_text and improved:
                current_text = revised_text
                pronoun_report = recheck_report
            else:
                current_text = before_text

    return current_text, pronoun_report


async def _repair_pronouns(
    runner: Any,
    bundle: Any,
    packet: Any,
    current_text: str,
    chapter_number: int,
    pronoun_report: PronounCheckResult,
    pronoun_step: PronounCheckStep,
    trace: Any,
) -> str:
    """Trigger a targeted edit to fix pronoun errors."""
    from novel_forge.core.constants import TaskType
    from novel_forge.pipeline.steps.edit_step import EditInput, EditStep

    issues = pronoun_report.get("issues", [])
    canon_context = (
        packet.canon_context
        if isinstance(packet.canon_context, dict)
        else {
            "characters": {
                name: (state.model_dump(mode="json") if hasattr(state, "model_dump") else state)
                for name, state in getattr(packet.canon_context, "characters", {}).items()
            }
        }
    )
    character_pronouns = pronoun_step._build_character_pronouns({}, canon_context)
    fix_prompt = pronoun_step.generate_fix_prompt(current_text, issues, character_pronouns)

    if not fix_prompt:
        return current_text

    _logger.warning(
        "第%d章代词检查触发LLM修复: POV问题=%d, 总问题=%d",
        chapter_number,
        pronoun_report.get("pov_issues", 0),
        pronoun_report.get("total_issues", 0),
    )

    edit_step = EditStep(
        runner._router,
        runner._builder,
        settings=runner._settings,
        trace=trace,
        on_step=getattr(runner, "on_step", None),
    )
    from novel_forge.pipeline.long.services.constraints.constraint_router import build_stage_cards

    canon_context = (
        packet.canon_context
        if isinstance(packet.canon_context, dict)
        else {
            "characters": {
                name: (state.model_dump(mode="json") if hasattr(state, "model_dump") else state)
                for name, state in getattr(packet.canon_context, "characters", {}).items()
            }
        }
    )
    edit_ctx = {
        "chapter_number": chapter_number,
        "target_word_count": getattr(bundle.chapter_outline, "expected_word_count", 0),
        "stage_cards": build_stage_cards(
            stage="edit",
            packet=packet,
            chapter_outline=bundle.chapter_outline,
            canon_context=canon_context,
            style_profile=getattr(bundle, "style_profile", None),
            editorial_contract=(
                bundle.editorial_contract.model_dump(mode="json")
                if getattr(bundle, "editorial_contract", None) is not None
                else None
            ),
            editorial_readiness=getattr(bundle, "editorial_readiness", None),
            pov_hint=getattr(bundle, "pov_hint", ""),
            chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
            settings=runner._settings,
        ),
        "pronouns_fix_instruction": fix_prompt,
    }
    edit_result = await edit_step.run(
        EditInput(
            task_type=TaskType.EDIT_CHAPTER,
            draft_text=current_text,
            context=edit_ctx,
            max_rounds=1,
            iteration=1,
            chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
        )
    )
    revised = edit_result.revised_text.strip() or current_text
    if not edit_result.revised_text.strip():
        _logger.warning("第%d章代词修复LLM返回空文本，保留原文", chapter_number)
    runner._on_step("pronoun_repair", edit_result)
    runner._storage.save_text(
        bundle.layout.chapter_draft_path(chapter_number, 98),
        revised,
    )
    return revised
