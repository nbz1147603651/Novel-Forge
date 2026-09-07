"""Draft stage: initial draft generation (single or segmented)."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Literal

from novel_forge.core.constants import PipelineConstants, TaskType
from novel_forge.core.utils.text_validation import count_chapter_words
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.narrative_person import build_narrative_person_context
from novel_forge.pipeline.short._shared import (
    clean_conversation_history,
    clean_text,
    excerpt_text,
    project_short_blueprint_prompt_card,
    project_short_execution_prompt_card,
    short_research_prompt_context,
    unique_texts,
)
from novel_forge.pipeline.steps.draft_step import DraftInput, DraftStep
from novel_forge.pipeline.style_profile_helpers import (
    merge_style_profile_overrides,
)

if TYPE_CHECKING:
    from novel_forge.core.schemas.beats import StoryBeats
    from novel_forge.core.schemas.short_blueprint import ShortBlueprint
    from novel_forge.core.schemas.spec import StorySpec
    from novel_forge.persistence.models import ProjectLayout
    from novel_forge.pipeline.short_runner import ShortStoryRunner

_log = get_logger("pipeline.short.stages.draft")


def _is_en_language(language: str) -> bool:
    """Return True if the language code indicates English output."""
    key = str(language or "zh").strip().lower().replace("_", "-")
    return key in {"en", "en-us", "en-gb", "english"} or key.startswith("en")


def _segment_bridge_strings(language: str) -> dict[str, Any]:
    """Return language-specific strings for segment bridge construction."""
    if _is_en_language(language):
        return {
            "carry_forward_prev": "Previous segment ended at: {hint}",
            "carry_forward_time": "Time anchors: {anchors}",
            "carry_forward_location": "Location anchors: {anchors}",
            "carry_forward_character": "Character focus: {chars}",
            "transition_rule": "Continue directly from the previous segment's ending. Do not recap the entire previous section or re-introduce established characters and settings.",
            "avoid_repeat": "Do not repeat opening introductions or action chains already completed in the previous segment.",
            "avoid_premature": "Do not write ahead to complete all subsequent segments; only fulfill this segment's duties.",
            "avoid_cliffhanger": "Do not create a cliffhanger ending; the final segment must land properly.",
            "history_done": "Completed segment {idx}/{total}.",
            "history_beats": "Covered beats {start}–{end}.",
            "history_goal": "Segment goal: {goal}",
            "history_tail": "Carry-over tail: {tail}",
            "joiner": ". ",
        }
    return {
        "carry_forward_prev": "上一段已推到：{hint}",
        "carry_forward_time": "时间锚点：{anchors}",
        "carry_forward_location": "场景锚点：{anchors}",
        "carry_forward_character": "人物焦点：{chars}",
        "transition_rule": "紧接上一段结尾继续推进，不要整段回顾前情，也不要重新介绍已经站稳的人物与设定。",
        "avoid_repeat": "不要重复开场介绍或上一段已完成的动作链",
        "avoid_premature": "不要提前写完后续全部段落，当前只完成本段职责",
        "avoid_cliffhanger": "不要再制造待续式断尾，必须把最后一段真正落地",
        "history_done": "上一轮已完成第 {idx}/{total} 段。",
        "history_beats": "覆盖 beats {start}–{end}。",
        "history_goal": "该段目标：{goal}",
        "history_tail": "承接尾部：{tail}",
        "joiner": " ",
    }


def _load_style_profile(
    runner: ShortStoryRunner,
    layout: ProjectLayout,
) -> dict[str, Any] | None:
    try:
        if layout.style_profile_path.exists():
            raw = runner._storage.load_json(layout.style_profile_path)
            if isinstance(raw, dict):
                return merge_style_profile_overrides(raw)
    except Exception:
        return None
    return None


async def _ensure_style_profile(
    runner: ShortStoryRunner,
    layout: ProjectLayout,
    spec: StorySpec,
    blueprint: ShortBlueprint | None,
) -> dict[str, Any] | None:
    if not getattr(runner._settings, "style_profile_enabled", True):
        runner._on_step("short_profile_style_skipped", {"reason": "disabled"})
        return None

    cached = _load_style_profile(runner, layout)
    if cached is not None:
        runner._on_step("short_profile_style_resumed", {"style_profile": cached})
        return cached

    blueprint_elements: dict[str, Any] = {}
    try:
        if blueprint is not None:
            elem_sel = getattr(blueprint, "element_selection", None)
            if elem_sel is not None:
                blueprint_elements = elem_sel.model_dump(mode="json")
        elif runner._storage.exists(layout.blueprint_elements_path):
            raw = runner._storage.load_json(layout.blueprint_elements_path)
            if isinstance(raw, dict):
                blueprint_elements = raw
    except Exception:
        blueprint_elements = {}

    story_bible_payload = {
        "title": spec.title,
        "genre": spec.genre,
        "theme": spec.theme,
        "tone": spec.tone,
        "world_hint": spec.world_hint,
        "conflict_hint": spec.conflict_hint,
        "pov_hint": spec.pov_hint,
    }
    character_bible_payload = {
        "characters_hint": spec.characters_hint,
    }
    synopsis_parts = unique_texts(
        [
            clean_text(spec.theme),
            clean_text(spec.conflict_hint),
            clean_text(spec.characters_hint),
            clean_text(spec.world_hint),
        ]
    )
    synopsis = "；".join(part for part in synopsis_parts if part)
    premise = "；".join(
        part
        for part in unique_texts(
            [
                clean_text(spec.theme),
                clean_text(spec.conflict_hint),
                clean_text(spec.world_hint),
            ]
        )
        if part
    )
    if synopsis:
        story_bible_payload["premise"] = synopsis

    try:
        from novel_forge.pipeline.steps.profile_style_step import (
            ProfileStyleInput,
            ProfileStyleStep,
        )

        step = ProfileStyleStep(
            runner._router,
            runner._builder,
            settings=runner._settings,
            trace=runner._trace,
        )
        profile = await step.run(
            ProfileStyleInput(
                title=spec.title,
                genre=spec.genre,
                tone=spec.tone,
                writing_style_mode="",
                narrative_complexity=getattr(spec, "narrative_complexity", "standard"),
                story_bible=story_bible_payload,
                character_bible=character_bible_payload,
                blueprint_elements=blueprint_elements,
                extra_instructions=spec.extra_instructions,
                synopsis=synopsis,
                premise=premise,
            )
        )
        payload = profile.model_dump(mode="json")
        runner._storage.save_json(layout.style_profile_path, payload)
        runner._on_step("short_profile_style", {"style_profile": payload})
        return payload
    except Exception as exc:
        if getattr(runner._settings, "style_profile_required", False):
            raise
        _log.warning("short_profile_style_failed: %s", exc)
        runner._on_step(
            "short_profile_style_failed",
            {"error_type": type(exc).__name__, "error": str(exc)},
        )
        return None


def _resolve_segment_mode(
    runner: ShortStoryRunner,
    explicit_mode: Literal["auto", "on", "off"] | None,
) -> Literal["auto", "on", "off"]:
    if explicit_mode in {"auto", "on", "off"}:
        return explicit_mode
    return runner._settings.short_segment_mode


def _should_use_segmented_short_draft(
    runner: ShortStoryRunner,
    spec: StorySpec,
    beats: StoryBeats,
    *,
    segmented_mode: Literal["auto", "on", "off"] | None,
) -> bool:
    mode = _resolve_segment_mode(runner, segmented_mode)
    if mode == "off":
        return False
    if len(beats.beats) < 2:
        return False

    target_words = max(500, spec.length_target or beats.total_estimated_words or 3000)
    if mode == "on":
        return target_words >= 1600
    return target_words >= runner._settings.short_segment_trigger_words


def _partition_segment_ranges(
    beat_plan: list[dict[str, Any]],
    desired_segments: int,
) -> list[tuple[int, int]]:
    if not beat_plan:
        return []
    desired = max(1, min(desired_segments, len(beat_plan)))
    if desired == 1:
        return [(0, len(beat_plan) - 1)]

    ranges: list[tuple[int, int]] = []
    start = 0
    segments_left = desired

    while segments_left > 1:
        remaining_plan = beat_plan[start:]
        remaining_words = sum(max(1, int(item.get("word_budget", 1))) for item in remaining_plan)
        ideal_words = max(1, remaining_words / segments_left)
        max_end = len(beat_plan) - segments_left

        running_words = 0
        chosen_end = start
        for idx in range(start, max_end + 1):
            running_words += max(1, int(beat_plan[idx].get("word_budget", 1)))
            chosen_end = idx
            if running_words >= ideal_words:
                previous_words = running_words - max(1, int(beat_plan[idx].get("word_budget", 1)))
                if idx > start and abs(previous_words - ideal_words) < abs(
                    running_words - ideal_words
                ):
                    chosen_end = idx - 1
                break

        remaining_after_split = len(beat_plan) - (chosen_end + 1)
        if remaining_after_split < segments_left - 1:
            chosen_end = len(beat_plan) - segments_left

        ranges.append((start, chosen_end))
        start = chosen_end + 1
        segments_left -= 1

    ranges.append((start, len(beat_plan) - 1))
    return ranges


def _build_short_segments(
    runner: ShortStoryRunner,
    spec: StorySpec,
    beats: StoryBeats,
    execution_plan: dict[str, Any],
    *,
    segment_target_words: int | None,
    segment_max_count: int | None,
) -> list[dict[str, Any]]:
    beat_plan = list(execution_plan.get("beat_execution_plan") or [])
    if not beat_plan:
        return []

    target_words = max(500, spec.length_target or beats.total_estimated_words or 3000)
    target_per_segment = max(
        800, segment_target_words or runner._settings.short_segment_target_words
    )
    max_segments = max(2, segment_max_count or runner._settings.short_segment_max_count)
    desired_segments = max(2, math.ceil(target_words / target_per_segment))
    desired_segments = min(desired_segments, max_segments, len(beat_plan))

    ranges = _partition_segment_ranges(beat_plan, desired_segments)
    total_segments = len(ranges)
    segments: list[dict[str, Any]] = []

    for index, (start, end) in enumerate(ranges, start=1):
        slice_plan = beat_plan[start : end + 1]
        slice_beats = beats.beats[start : end + 1]
        structural_roles = unique_texts(
            [str(item.get("structural_role") or "") for item in slice_plan]
        )
        phase_names = unique_texts([str(item.get("phase_name") or "") for item in slice_plan])
        time_anchors = unique_texts([str(item.get("time_anchor") or "") for item in slice_plan])
        location_anchors = unique_texts(
            [str(item.get("location_anchor") or "") for item in slice_plan]
        )
        turning_points = unique_texts(
            [str(item.get("turning_point_hint") or "") for item in slice_plan]
        )
        character_focus: list[str] = []
        for item in slice_plan:
            for character in item.get("character_focus") or []:
                name = clean_text(character)
                if name and name not in character_focus:
                    character_focus.append(name)

        beat_summaries = [clean_text(beat.summary) for beat in slice_beats]
        segment_goal = "；".join(
            unique_texts(
                [
                    *(str(item.get("phase_goal") or "") for item in slice_plan),
                    *beat_summaries,
                ]
            )[:3]
        )
        segments.append(
            {
                "segment_index": index,
                "total_segments": total_segments,
                "is_first": index == 1,
                "is_final": index == total_segments,
                "start_index": start,
                "end_index": end,
                "beat_start_sequence": getattr(slice_beats[0], "sequence", start + 1),
                "beat_end_sequence": getattr(slice_beats[-1], "sequence", end + 1),
                "target_words": sum(max(1, int(item.get("word_budget", 1))) for item in slice_plan),
                "structural_roles": structural_roles,
                "phase_names": phase_names,
                "time_anchors": time_anchors,
                "location_anchors": location_anchors,
                "character_focus": character_focus,
                "turning_points": turning_points,
                "beat_summaries": beat_summaries,
                "segment_goal": segment_goal or (beat_summaries[0] if beat_summaries else ""),
                "end_state_hint": beat_summaries[-1] if beat_summaries else "",
            }
        )

    return segments


def _build_segment_bridge(
    runner: ShortStoryRunner,
    *,
    segment: dict[str, Any],
    previous_segment: dict[str, Any] | None,
    previous_text: str,
    remaining_segments: list[dict[str, Any]],
    execution_plan: dict[str, Any],
    language: str = "zh",
    previous_tail_limit: int = 240,
    max_carry_forward: int = 2,
    max_unresolved_threads: int = 3,
) -> dict[str, Any]:
    strings = _segment_bridge_strings(language)
    carry_forward: list[str] = []
    if previous_segment is not None and previous_segment.get("end_state_hint"):
        carry_forward.append(
            strings["carry_forward_prev"].format(hint=previous_segment["end_state_hint"])
        )
    if segment.get("time_anchors"):
        carry_forward.append(
            strings["carry_forward_time"].format(
                anchors="、".join(segment["time_anchors"][:2])
                if not _is_en_language(language)
                else ", ".join(segment["time_anchors"][:2])
            )
        )
    if segment.get("location_anchors"):
        carry_forward.append(
            strings["carry_forward_location"].format(
                anchors="、".join(segment["location_anchors"][:2])
                if not _is_en_language(language)
                else ", ".join(segment["location_anchors"][:2])
            )
        )
    if segment.get("character_focus"):
        carry_forward.append(
            strings["carry_forward_character"].format(
                chars="、".join(segment["character_focus"][:3])
                if not _is_en_language(language)
                else ", ".join(segment["character_focus"][:3])
            )
        )
    carry_forward = unique_texts(carry_forward)[: max(1, int(max_carry_forward))]

    unresolved_threads: list[str] = []
    for future_segment in remaining_segments[:2]:
        unresolved_threads.extend(future_segment.get("turning_points") or [])
        if not unresolved_threads:
            unresolved_threads.extend(future_segment.get("beat_summaries") or [])
    unresolved_threads = [
        excerpt_text(item, limit=100)
        for item in unique_texts(unresolved_threads)[: max(1, int(max_unresolved_threads))]
    ]

    bridge: dict[str, Any] = {
        "segment_index": segment["segment_index"],
        "total_segments": segment["total_segments"],
        "segment_role": (
            "opening"
            if segment.get("is_first")
            else "closing"
            if segment.get("is_final")
            else "middle"
        ),
        "previous_tail_excerpt": excerpt_text(
            previous_text,
            limit=max(140, int(previous_tail_limit)),
        )
        if previous_text
        else "",
        "carry_forward": carry_forward,
        "current_objective": excerpt_text(
            segment.get("segment_goal", ""),
            limit=max(80, int(previous_tail_limit * 0.5)),
        ),
        "unresolved_threads": unresolved_threads,
        "transition_rule": strings["transition_rule"],
        "avoid": [
            strings["avoid_repeat"],
            (
                strings["avoid_premature"]
                if not segment.get("is_final")
                else strings["avoid_cliffhanger"]
            ),
        ],
    }
    if segment.get("is_first"):
        bridge["opening_contract"] = execution_plan.get("opening_contract", "")
    if segment.get("is_final"):
        bridge["ending_contract"] = execution_plan.get("ending_contract", "")
        bridge["completion_contract"] = execution_plan.get("completion_contract", {})
    return bridge


def _build_segment_history_messages(
    runner: ShortStoryRunner,
    segment: dict[str, Any],
    bridge: dict[str, Any],
    segment_text: str,
    *,
    language: str = "zh",
    user_excerpt_limit: int = 180,
    assistant_excerpt_limit: int = 700,
) -> list[dict[str, str]]:
    strings = _segment_bridge_strings(language)
    user_summary_parts = [
        strings["history_done"].format(
            idx=segment["segment_index"], total=segment["total_segments"]
        ),
        strings["history_beats"].format(
            start=segment["beat_start_sequence"], end=segment["beat_end_sequence"]
        ),
    ]
    if segment.get("segment_goal"):
        user_summary_parts.append(strings["history_goal"].format(goal=segment["segment_goal"]))
    if bridge.get("previous_tail_excerpt"):
        user_summary_parts.append(
            strings["history_tail"].format(tail=bridge["previous_tail_excerpt"])
        )

    user_summary = strings["joiner"].join(user_summary_parts)
    assistant_summary = excerpt_text(segment_text, limit=max(140, int(assistant_excerpt_limit)))
    return [
        {
            "role": "user",
            "content": excerpt_text(user_summary, limit=max(80, int(user_excerpt_limit))),
        },
        {"role": "assistant", "content": assistant_summary},
    ]


def _build_segment_context_budget(
    segment: dict[str, Any],
    *,
    story_target_words: int,
) -> dict[str, int]:
    total_segments = max(1, int(segment.get("total_segments", 1) or 1))
    target_words = int(segment.get("target_words", 0) or 0)
    if target_words <= 0:
        target_words = max(500, int(story_target_words / total_segments))

    base_chars = 360 + int(target_words * 0.42)
    base_chars = max(320, min(base_chars, 1400))

    if segment.get("is_first"):
        base_chars = int(base_chars * 0.85)
    elif segment.get("is_final"):
        base_chars = int(base_chars * 1.08)

    bridge_chars = max(140, min(520, int(base_chars * 0.46)))
    history_chars = max(160, min(820, int(base_chars * 0.54)))
    history_rounds = 1 if target_words < 1500 else 2
    max_carry_forward = 1 if target_words < 1300 else 2
    max_unresolved_threads = 2 if target_words < 1700 else 3

    per_round = max(120, int(history_chars / max(1, history_rounds)))
    user_chars = max(70, min(220, int(per_round * 0.35)))
    assistant_chars = max(120, min(460, int(per_round * 0.65)))
    retained_history_chars = max(260, min(1200, int(history_chars * 1.8)))

    return {
        "bridge_chars": bridge_chars,
        "history_chars": history_chars,
        "history_rounds": history_rounds,
        "history_user_chars": user_chars,
        "history_assistant_chars": assistant_chars,
        "retained_history_chars": retained_history_chars,
        "max_carry_forward": max_carry_forward,
        "max_unresolved_threads": max_unresolved_threads,
    }


def _build_segment_prompt_execution_plan(
    execution_plan: dict[str, Any],
    segment_execution_plan: list[dict[str, Any]],
    *,
    segment: dict[str, Any],
) -> dict[str, Any]:
    prompt_plan: dict[str, Any] = {
        "anchor_guardrails": execution_plan.get("anchor_guardrails", {}),
        "beat_execution_plan": list(segment_execution_plan or []),
    }
    if segment.get("is_first") and execution_plan.get("opening_contract"):
        prompt_plan["opening_contract"] = execution_plan.get("opening_contract", "")
    if segment.get("is_final"):
        if execution_plan.get("ending_contract"):
            prompt_plan["ending_contract"] = execution_plan.get("ending_contract", "")
        if execution_plan.get("completion_contract"):
            prompt_plan["completion_contract"] = execution_plan.get("completion_contract", {})
    return prompt_plan


def _apply_segment_history_budget(
    messages: list[dict[str, str]],
    *,
    budget_chars: int,
    max_rounds: int,
) -> list[dict[str, str]]:
    if not messages:
        return []

    rounds = max(1, int(max_rounds))
    budget = max(120, int(budget_chars))
    limited = [
        {
            "role": str(item.get("role", "assistant")),
            "content": clean_text(item.get("content", "")),
        }
        for item in messages[-rounds * 2 :]
        if clean_text(item.get("content", ""))
    ]

    def _total_chars(items: list[dict[str, str]]) -> int:
        return sum(len(i.get("content", "")) for i in items)

    while limited and _total_chars(limited) > budget:
        if len(limited) > 2:
            limited = limited[2:]
            continue

        shrunk: list[dict[str, str]] = []
        for msg in limited:
            old = msg.get("content", "")
            limit = max(60, int(len(old) * 0.82))
            shrunk.append(
                {
                    "role": msg.get("role", "assistant"),
                    "content": excerpt_text(old, limit=limit),
                }
            )
        if _total_chars(shrunk) >= _total_chars(limited):
            break
        limited = shrunk

    return [msg for msg in limited if msg.get("content")]


def _trim_segment_overlap(previous_text: str, next_text: str) -> str:
    existing = str(previous_text or "").rstrip()
    incoming = str(next_text or "").lstrip()
    if not existing or not incoming:
        return incoming

    max_overlap = min(220, len(existing), len(incoming))
    for size in range(max_overlap, 39, -1):
        if existing[-size:] == incoming[:size]:
            return incoming[size:].lstrip()

    previous_paragraphs = [item.strip() for item in existing.split("\n\n") if item.strip()]
    incoming_paragraphs = [item.strip() for item in incoming.split("\n\n") if item.strip()]
    if (
        previous_paragraphs
        and incoming_paragraphs
        and previous_paragraphs[-1] == incoming_paragraphs[0]
    ):
        return "\n\n".join(incoming_paragraphs[1:]).strip()
    return incoming


def _merge_short_segments(segments: list[str]) -> str:
    cleaned = [str(item or "").strip() for item in segments if str(item or "").strip()]
    if not cleaned:
        return ""
    merged = cleaned[0]
    for item in cleaned[1:]:
        trimmed = _trim_segment_overlap(merged, item)
        if not trimmed:
            continue
        if merged and not merged.endswith("\n\n"):
            merged += "\n\n"
        merged += trimmed
    return merged.strip()


async def run_segmented_initial_draft(
    runner: ShortStoryRunner,
    layout: ProjectLayout,
    spec: StorySpec,
    beats: StoryBeats,
    blueprint: ShortBlueprint | None,
    execution_plan: dict[str, Any],
    *,
    segment_target_words: int | None,
    segment_max_count: int | None,
) -> str:
    segments = _build_short_segments(
        runner,
        spec,
        beats,
        execution_plan,
        segment_target_words=segment_target_words,
        segment_max_count=segment_max_count,
    )
    if len(segments) <= 1:
        return ""

    draft_multi_turn_enabled = runner._is_short_option_enabled_for_task(
        capability="multi_turn",
        enabled=runner._settings.short_draft_multi_turn,
        allowed_providers_raw=runner._settings.short_draft_multi_turn_providers,
        allowed_models_raw=runner._settings.short_draft_multi_turn_models,
        task_type=TaskType.DRAFT,
    )
    runner._storage.save_json(
        layout.short_segment_plan_path(),
        {
            "segmented": True,
            "multi_turn": draft_multi_turn_enabled,
            "segment_count": len(segments),
            "segment_target_words": segment_target_words
            or runner._settings.short_segment_target_words,
            "segments": segments,
        },
    )
    runner._on_step(
        "short_segment_plan",
        {
            "segment_count": len(segments),
            "multi_turn": draft_multi_turn_enabled,
        },
    )

    draft_step = DraftStep(
        runner._router,
        runner._builder,
        settings=runner._settings,
        trace=runner._trace,
        on_step=getattr(runner, "on_step", None),
    )
    conversation_history: list[dict[str, str]] = []
    segment_texts: list[str] = []
    previous_segment: dict[str, Any] | None = None
    previous_text = ""
    style_profile = _load_style_profile(runner, layout)
    full_target_words = max(500, spec.length_target or beats.total_estimated_words or 3000)
    beat_plan = list(execution_plan.get("beat_execution_plan") or [])

    for segment in segments:
        current_index = int(segment["segment_index"])
        context_budget = _build_segment_context_budget(
            segment,
            story_target_words=full_target_words,
        )
        bridge = _build_segment_bridge(
            runner,
            segment=segment,
            previous_segment=previous_segment,
            previous_text=previous_text,
            remaining_segments=segments[current_index:],
            execution_plan=execution_plan,
            language=getattr(spec, "language", "zh"),
            previous_tail_limit=context_budget["bridge_chars"],
            max_carry_forward=context_budget["max_carry_forward"],
            max_unresolved_threads=context_budget["max_unresolved_threads"],
        )
        runner._storage.save_json(layout.short_segment_bridge_path(current_index), bridge)
        runner._on_step(
            f"short_segment_bridge_{current_index}",
            {
                "segment": current_index,
                "segments_total": len(segments),
            },
        )

        segment_beats = beats.beats[int(segment["start_index"]) : int(segment["end_index"]) + 1]
        segment_execution_plan = beat_plan[
            int(segment["start_index"]) : int(segment["end_index"]) + 1
        ]
        prompt_execution_plan = _build_segment_prompt_execution_plan(
            execution_plan,
            segment_execution_plan,
            segment=segment,
        )
        prior_messages = None
        if draft_multi_turn_enabled and conversation_history:
            window = (
                max(
                    PipelineConstants.CONVERSATION_HISTORY_WINDOW,
                    context_budget["history_rounds"],
                )
                * 2
            )
            raw_history = conversation_history[-window:]
            cleaned_history = clean_conversation_history(
                raw_history,
                max_rounds=context_budget["history_rounds"],
            )
            prior_messages = _apply_segment_history_budget(
                cleaned_history,
                budget_chars=context_budget["history_chars"],
                max_rounds=context_budget["history_rounds"],
            )

        ctx: dict[str, Any] = {
            "user_intent": runner._user_intent,
            **short_research_prompt_context(runner, include_inspiration=True),
            "spec": spec,
            "beats": beats,
            "segment_beats": segment_beats,
            "segment_execution_plan": segment_execution_plan,
            "segment_plan": segment,
            "segment_bridge": bridge,
            "target_word_count": int(segment["target_words"]),
            "story_target_word_count": full_target_words,
            "execution_plan": project_short_execution_prompt_card(
                prompt_execution_plan,
                stage="draft",
            ),
            "style_profile": style_profile,
            **build_narrative_person_context(getattr(spec, "pov_hint", "")),
        }
        if blueprint is not None:
            ctx["blueprint"] = project_short_blueprint_prompt_card(
                blueprint,
                stage="draft",
            )

        draft = await draft_step.run(
            DraftInput(
                task_type=TaskType.DRAFT,
                context=ctx,
                prior_messages=prior_messages,
                multi_turn=draft_multi_turn_enabled,
            )
        )
        segment_text = str(draft.text or "").strip()
        runner._storage.save_text(layout.short_segment_draft_path(current_index), segment_text)
        runner._on_step(
            f"draft_segment_{current_index}",
            {
                "segment": current_index,
                "segments_total": len(segments),
                "word_count": count_chapter_words(segment_text),
                "target_words": int(segment["target_words"]),
            },
        )
        segment_texts.append(segment_text)
        previous_text = segment_text
        previous_segment = segment

        if draft_multi_turn_enabled:
            conversation_history.extend(
                _build_segment_history_messages(
                    runner,
                    segment,
                    bridge,
                    segment_text,
                    language=getattr(spec, "language", "zh"),
                    user_excerpt_limit=context_budget["history_user_chars"],
                    assistant_excerpt_limit=context_budget["history_assistant_chars"],
                )
            )
            conversation_history = _apply_segment_history_budget(
                conversation_history,
                budget_chars=context_budget["retained_history_chars"],
                max_rounds=max(2, context_budget["history_rounds"] + 1),
            )

    merged_text = _merge_short_segments(segment_texts)
    runner._storage.save_text(layout.short_draft_path(0), merged_text)
    runner._on_step(
        "draft",
        {
            "word_count": count_chapter_words(merged_text),
            "segments": len(segments),
            "segmented": True,
        },
    )
    return merged_text


async def run_initial_draft(
    runner: ShortStoryRunner,
    layout: ProjectLayout,
    spec: StorySpec,
    beats: StoryBeats,
    blueprint: ShortBlueprint | None,
    execution_plan: dict[str, Any],
    *,
    segmented_mode: Literal["auto", "on", "off"] | None,
    segment_target_words: int | None,
    segment_max_count: int | None,
) -> str:
    if _should_use_segmented_short_draft(
        runner,
        spec,
        beats,
        segmented_mode=segmented_mode,
    ):
        segmented_text = await run_segmented_initial_draft(
            runner,
            layout,
            spec,
            beats,
            blueprint,
            execution_plan,
            segment_target_words=segment_target_words,
            segment_max_count=segment_max_count,
        )
        if segmented_text:
            return segmented_text

    draft_step = DraftStep(
        runner._router,
        runner._builder,
        settings=runner._settings,
        trace=runner._trace,
        on_step=getattr(runner, "on_step", None),
    )
    style_profile = _load_style_profile(runner, layout)
    ctx: dict[str, Any] = {
        "user_intent": runner._user_intent,
        **short_research_prompt_context(runner, include_inspiration=True),
        "spec": spec,
        "beats": beats,
        "target_word_count": spec.length_target,
        "execution_plan": project_short_execution_prompt_card(
            execution_plan,
            stage="draft",
        ),
        "style_profile": style_profile,
        **build_narrative_person_context(getattr(spec, "pov_hint", "")),
    }
    if blueprint is not None:
        ctx["blueprint"] = project_short_blueprint_prompt_card(
            blueprint,
            stage="draft",
        )
    draft = await draft_step.run(
        DraftInput(
            task_type=TaskType.DRAFT,
            context=ctx,
        )
    )
    runner._storage.save_text(layout.short_draft_path(0), draft.text)
    runner._on_step("draft", draft)
    return draft.text
