"""Stage-aware, source-faithful constraint routing for long chapters.

The router decides *which* source fields a stage should see.  It does not
re-rank, paraphrase, or truncate evidence already selected by the upstream
structured projection / retrieval layer.  Accuracy belongs to source
artifacts; prompt lightness comes from stage ownership and non-duplication.
"""

from __future__ import annotations

import re
from typing import Any

from novel_forge.core.utils.field_extractor import field
from novel_forge.editorial.cards import build_editorial_card
from novel_forge.memory.motif_governance import allows_forward_prompt_guidance
from novel_forge.narrative_state.knowledge_ops import (
    build_entity_lookup,
    normalize_knowledge_ops,
    normalize_knowledge_type,
)
from novel_forge.pipeline.long.services.attention_budget import apply_stage_context_manifest
from novel_forge.pipeline.long.services.chapter_position import build_chapter_position
from novel_forge.pipeline.long.services.constraints.cognitive_constraints import (
    project_cognitive_constraints,
)
from novel_forge.pipeline.long.services.context.source_artifacts import project_stage_source_cards
from novel_forge.pipeline.long.services.plot_milestones import stage_visibility_summary


def build_stage_cards(
    *,
    stage: str,
    packet: Any | None = None,
    chapter_outline: Any | None = None,
    bridge: Any | None = None,
    plan: Any | None = None,
    canon_context: Any | None = None,
    style_profile: Any | None = None,
    editorial_contract: Any | None = None,
    editorial_readiness: dict[str, Any] | None = None,
    narrative_contract: Any | None = None,
    memory_hints: dict[str, Any] | None = None,
    reading_power_hint: dict[str, Any] | None = None,
    story_bible: dict[str, Any] | None = None,
    element_selection: dict[str, Any] | None = None,
    element_progress_hint: dict[str, Any] | None = None,
    element_focus: list[str] | None = None,
    known_issues_to_avoid: list[dict[str, Any]] | None = None,
    weak_senses: list[str] | None = None,
    pov_hint: str | None = None,
    milestone_window: dict[str, Any] | None = None,
    time_context: dict[str, Any] | None = None,
    strand_hint: dict[str, Any] | None = None,
    kernel_context: dict[str, Any] | None = None,
    chapter_source_slice: Any | None = None,
    chapter_position: dict[str, Any] | None = None,
    settings: Any | None = None,
) -> dict[str, Any]:
    """Build a stage-specific card bundle without re-truncating selected evidence."""

    stage_name = _text(stage).lower() or "chapter"
    outline = chapter_outline or field(packet, "chapter_outline", None)
    effective_bridge = bridge or field(packet, "bridge", None)
    effective_canon = (
        canon_context if canon_context is not None else field(packet, "canon_context", {})
    )
    memory = memory_hints or {}
    story = story_bible or {}
    effective_milestone_window = milestone_window or _as_mapping(
        field(packet, "milestone_window", {})
    )
    kernel = kernel_context or {}

    cards: dict[str, Any] = {"stage": stage_name}
    source_cards = (
        project_stage_source_cards(chapter_source_slice, stage=stage_name)
        if chapter_source_slice is not None
        else {}
    )
    source_cards = dict(source_cards)
    user_intent = source_cards.pop("user_intent", None)
    research_evidence_pack = source_cards.pop("research_evidence_pack", None)
    research_uncertainty = source_cards.pop("research_uncertainty", None)
    if source_cards and not bool(getattr(settings, "long_literary_contract_enabled", True)):
        source_cards = dict(source_cards)
        source_cards.pop("literary_contract", None)
        source_contract = dict(_as_mapping(source_cards.get("chapter_contract")))
        source_contract.pop("literary_contract", None)
        if source_contract:
            source_cards["chapter_contract"] = source_contract

    def add(name: str, value: Any) -> None:
        if value not in (None, "", [], {}):
            cards[name] = value

    add("source", source_cards)
    add("user_intent", user_intent)
    add("research_evidence_pack", research_evidence_pack)
    add("research_uncertainty", research_uncertainty)
    add("chapter", _build_chapter_card(outline, packet=packet, chapter_position=chapter_position))
    add(
        "stage_visibility",
        stage_visibility_summary(stage=stage_name, milestone_window=effective_milestone_window),
    )
    contract_card = _build_contract_card(
        packet=packet,
        outline=outline,
        canon_context=effective_canon,
        story_bible=story,
        narrative_contract=narrative_contract,
        milestone_window=effective_milestone_window,
        stage=stage_name,
        settings=settings,
    )
    contract_card = _merge_source_runtime_contract(contract_card, source_cards)
    add("contract", contract_card)
    if stage_name in {"plan", "draft", "edit", "wave", "continuity_repair", "causal_repair"}:
        add("state", _build_state_card(effective_canon, memory_hints=memory, settings=settings))
    if stage_name in {"plan", "draft", "edit", "wave"}:
        add("narration", _build_narration_card(pov_hint))
    if stage_name in {"plan", "draft"}:
        add("subplot_weave", _build_subplot_weave_card(packet))

    if stage_name in {
        "bridge",
        "plan",
        "edit",
        "continuity_repair",
        "causal_repair",
        "reading_power_repair",
    }:
        add("bridge", _build_bridge_card(packet=packet, bridge=effective_bridge, settings=settings))
    if stage_name == "bridge":
        add("opening_evidence", _build_opening_evidence_card(effective_canon, outline=outline))
    if stage_name in {"bridge", "plan", "review", "continuity_repair", "causal_repair"}:
        evidence_pack = _as_mapping(field(packet, "retrieval_evidence_pack", {}))
        if evidence_pack:
            add(
                "retrieval_evidence",
                {
                    "pack_id": _text(evidence_pack.get("pack_id")),
                    "max_visible_chapter": int(evidence_pack.get("max_visible_chapter", 0) or 0),
                    "supporting_evidence": [
                        {
                            "source_ref": _text(field(card, "source_ref", "")),
                            "excerpt": _text(field(card, "excerpt", "")),
                            "authority": _text(field(card, "authority", "supporting")),
                        }
                        for card in list(evidence_pack.get("evidence_cards", []) or [])
                        if _text(field(card, "excerpt", ""))
                    ],
                    "selection": _drop_empty(
                        {
                            "candidate_limit": int(evidence_pack.get("candidate_limit", 0) or 0),
                            "evidence_token_budget": int(
                                evidence_pack.get("evidence_token_budget", 0) or 0
                            ),
                            "estimated_evidence_tokens": int(
                                evidence_pack.get("estimated_evidence_tokens", 0) or 0
                            ),
                            "retrieved_candidate_count": int(
                                evidence_pack.get("retrieved_candidate_count", 0) or 0
                            ),
                            "omitted_candidate_count_lower_bound": int(
                                evidence_pack.get("omitted_candidate_count_lower_bound", 0) or 0
                            ),
                            "has_more_evidence": bool(
                                evidence_pack.get("has_more_evidence", False)
                            ),
                        }
                    ),
                },
            )
    if stage_name in {
        "draft",
        "edit",
        "wave",
        "continuity_repair",
        "causal_repair",
        "reading_power_repair",
    }:
        add(
            "plan",
            _build_plan_card(
                plan,
                stage=stage_name,
                settings=settings,
            ),
        )
        # DRAFT must not see cross_scene_intent (plan -> wave contract).
        if stage_name == "draft" and "plan" in cards and isinstance(cards["plan"], dict):
            cards["plan"].pop("cross_scene_intent", None)
    if stage_name == "plan":
        add(
            "characters",
            _build_character_cards(packet, canon_context=effective_canon, detail="brief"),
        )
    if stage_name == "draft":
        # DRAFT: POV gets full+voice, others brief.  Uses pov_hint when
        # available, else falls back to chapter_outline.pov_character.
        _pov_for_chars = _text(pov_hint) or _text(field(chapter_outline, "pov_character", ""))
        add(
            "characters",
            _build_character_cards(
                packet,
                canon_context=effective_canon,
                detail="pov_full_others_brief",
                pov_name=_pov_for_chars,
            ),
        )
    if stage_name in {"edit"}:
        add(
            "characters",
            _build_character_cards(packet, canon_context=effective_canon, detail="full"),
        )
    if stage_name == "wave":
        add(
            "characters",
            _build_character_cards(packet, canon_context=effective_canon, detail="voice_only"),
        )
    if stage_name in {
        "plan",
        "draft",
        "edit",
        "wave",
        "continuity_repair",
        "causal_repair",
        "reading_power_repair",
    }:
        add(
            "knowledge",
            _build_knowledge_card(
                kernel_context=kernel,
                chapter_contract=_as_mapping(field(packet, "chapter_contract", {})),
                outline=outline,
                plan=plan,
                stage=stage_name,
                current_chapter=int(field(outline, "chapter_number", 0) or 0),
                pov_character=_text(field(outline, "pov_character", "")),
            ),
        )
    if stage_name in {
        "plan",
        "draft",
        "edit",
        "wave",
        "polish",
        "continuity_repair",
        "causal_repair",
        "reading_power_repair",
        "check",
    }:
        add(
            "editorial",
            build_editorial_card(
                editorial_contract,
                chapter_number=int(field(outline, "chapter_number", 0) or 0),
                stage=stage_name,
                involved_characters=_involved_characters(outline),
                readiness=editorial_readiness,
            ),
        )
    if stage_name in {
        "bridge",
        "plan",
        "draft",
        "edit",
        "wave",
        "polish",
        "continuity_repair",
        "causal_repair",
        "reading_power_repair",
    }:
        add("style", _build_style_capsule(style_profile, stage=stage_name))
    if stage_name in {
        "bridge",
        "plan",
        "draft",
        "edit",
        "wave",
        "polish",
        "continuity_repair",
        "causal_repair",
        "reading_power_repair",
    }:
        add("memory", _build_memory_capsule(memory, stage=stage_name))
    if stage_name in {"bridge", "plan", "draft", "edit", "wave", "polish", "reading_power_repair"}:
        add(
            "quality",
            _build_quality_capsule(
                reading_power_hint or {},
                weak_senses=weak_senses,
                stage=stage_name,
            ),
        )
    if stage_name == "plan":
        add("arc_liveness", _build_arc_liveness_card(field(packet, "arc_liveness_report", {})))
        add("time", _build_time_card(time_context))
        add("strand", _build_strand_card(strand_hint))
    if stage_name in {
        "draft",
        "edit",
        "wave",
        "continuity_repair",
        "causal_repair",
        "reading_power_repair",
    }:
        add(
            "repair",
            _build_repair_card(
                packet=packet,
                plan=plan,
                known_issues_to_avoid=known_issues_to_avoid,
                stage=stage_name,
            ),
        )
    if stage_name in {"plan", "draft", "edit", "wave"}:
        add("element", _build_element_card(element_selection, element_focus, element_progress_hint))

    return apply_stage_context_manifest(cards, stage=stage_name)


def build_bridge_cards(
    *,
    packet: Any,
    memory_hints: dict[str, Any] | None = None,
    reading_power_hint: dict[str, Any] | None = None,
    style_profile: Any | None = None,
    editorial_contract: Any | None = None,
    narrative_contract: Any | None = None,
    story_bible: dict[str, Any] | None = None,
    canon_context: Any | None = None,
    settings: Any | None = None,
    chapter_source_slice: Any | None = None,
) -> dict[str, Any]:
    """Bridge-stage convenience wrapper."""

    return build_stage_cards(
        stage="bridge",
        packet=packet,
        chapter_outline=field(packet, "chapter_outline", None),
        canon_context=canon_context,
        style_profile=style_profile,
        editorial_contract=editorial_contract,
        narrative_contract=narrative_contract,
        memory_hints=memory_hints,
        reading_power_hint=reading_power_hint,
        story_bible=story_bible,
        chapter_source_slice=chapter_source_slice,
        settings=settings,
    )


def build_plan_cards(
    *,
    packet: Any | None,
    chapter_outline: Any,
    canon_context: Any | None = None,
    memory_hints: dict[str, Any] | None = None,
    style_profile: Any | None = None,
    editorial_contract: Any | None = None,
    editorial_readiness: dict[str, Any] | None = None,
    narrative_contract: Any | None = None,
    reading_power_hint: dict[str, Any] | None = None,
    story_bible: dict[str, Any] | None = None,
    element_selection: dict[str, Any] | None = None,
    element_progress_hint: dict[str, Any] | None = None,
    element_focus: list[str] | None = None,
    known_issues_to_avoid: list[dict[str, Any]] | None = None,
    pov_hint: str | None = None,
    time_context: dict[str, Any] | None = None,
    strand_hint: dict[str, Any] | None = None,
    kernel_context: dict[str, Any] | None = None,
    chapter_source_slice: Any | None = None,
    chapter_position: dict[str, Any] | None = None,
    settings: Any | None = None,
) -> dict[str, Any]:
    """Plan-stage convenience wrapper."""

    return build_stage_cards(
        stage="plan",
        packet=packet,
        chapter_outline=chapter_outline,
        canon_context=canon_context,
        style_profile=style_profile,
        editorial_contract=editorial_contract,
        editorial_readiness=editorial_readiness,
        narrative_contract=narrative_contract,
        memory_hints=memory_hints,
        reading_power_hint=reading_power_hint,
        story_bible=story_bible,
        element_selection=element_selection,
        element_progress_hint=element_progress_hint,
        element_focus=element_focus,
        known_issues_to_avoid=known_issues_to_avoid,
        pov_hint=pov_hint,
        time_context=time_context,
        strand_hint=strand_hint,
        kernel_context=kernel_context,
        chapter_source_slice=chapter_source_slice,
        chapter_position=chapter_position,
        settings=settings,
    )


def build_draft_cards(
    *,
    packet: Any,
    chapter_outline: Any,
    plan: Any,
    canon_context: Any | None = None,
    memory_hints: dict[str, Any] | None = None,
    style_profile: Any | None = None,
    editorial_contract: Any | None = None,
    editorial_readiness: dict[str, Any] | None = None,
    narrative_contract: Any | None = None,
    reading_power_hint: dict[str, Any] | None = None,
    story_bible: dict[str, Any] | None = None,
    element_selection: dict[str, Any] | None = None,
    element_progress_hint: dict[str, Any] | None = None,
    element_focus: list[str] | None = None,
    known_issues_to_avoid: list[dict[str, Any]] | None = None,
    weak_senses: list[str] | None = None,
    pov_hint: str | None = None,
    kernel_context: dict[str, Any] | None = None,
    chapter_source_slice: Any | None = None,
    chapter_position: dict[str, Any] | None = None,
    settings: Any | None = None,
) -> dict[str, Any]:
    """Draft-stage convenience wrapper."""

    return build_stage_cards(
        stage="draft",
        packet=packet,
        chapter_outline=chapter_outline,
        plan=plan,
        canon_context=canon_context,
        style_profile=style_profile,
        editorial_contract=editorial_contract,
        editorial_readiness=editorial_readiness,
        narrative_contract=narrative_contract,
        memory_hints=memory_hints,
        reading_power_hint=reading_power_hint,
        story_bible=story_bible,
        element_selection=element_selection,
        element_progress_hint=element_progress_hint,
        element_focus=element_focus,
        known_issues_to_avoid=known_issues_to_avoid,
        weak_senses=weak_senses,
        pov_hint=pov_hint,
        kernel_context=kernel_context,
        chapter_source_slice=chapter_source_slice,
        chapter_position=chapter_position,
        settings=settings,
    )


def build_wave_cards(
    *,
    packet: Any,
    chapter_outline: Any,
    bridge: Any,
    plan: Any,
    canon_context: Any | None = None,
    memory_hints: dict[str, Any] | None = None,
    style_profile: Any | None = None,
    editorial_contract: Any | None = None,
    editorial_readiness: dict[str, Any] | None = None,
    narrative_contract: Any | None = None,
    reading_power_hint: dict[str, Any] | None = None,
    story_bible: dict[str, Any] | None = None,
    element_selection: dict[str, Any] | None = None,
    element_progress_hint: dict[str, Any] | None = None,
    element_focus: list[str] | None = None,
    known_issues_to_avoid: list[dict[str, Any]] | None = None,
    weak_senses: list[str] | None = None,
    pov_hint: str | None = None,
    kernel_context: dict[str, Any] | None = None,
    chapter_source_slice: Any | None = None,
    chapter_position: dict[str, Any] | None = None,
    settings: Any | None = None,
) -> dict[str, Any]:
    """Wave-stage convenience wrapper.

    Stage card bundle for the WAVE (scene-weaving) step.  Receives the
    full plan (including ``cross_scene_intent``), full character cards
    (with voice), and the full editorial contract (with character voices
    and expression channel budget).  Runs exactly once.
    """

    return build_stage_cards(
        stage="wave",
        packet=packet,
        chapter_outline=chapter_outline,
        bridge=bridge,
        plan=plan,
        canon_context=canon_context,
        style_profile=style_profile,
        editorial_contract=editorial_contract,
        editorial_readiness=editorial_readiness,
        narrative_contract=narrative_contract,
        memory_hints=memory_hints,
        reading_power_hint=reading_power_hint,
        story_bible=story_bible,
        element_selection=element_selection,
        element_progress_hint=element_progress_hint,
        element_focus=element_focus,
        known_issues_to_avoid=known_issues_to_avoid,
        weak_senses=weak_senses,
        pov_hint=pov_hint,
        kernel_context=kernel_context,
        chapter_source_slice=chapter_source_slice,
        chapter_position=chapter_position,
        settings=settings,
    )


def build_repair_cards(
    *,
    stage: str,
    packet: Any | None = None,
    chapter_outline: Any | None = None,
    bridge: Any | None = None,
    plan: Any | None = None,
    style_profile: Any | None = None,
    editorial_contract: Any | None = None,
    memory_hints: dict[str, Any] | None = None,
    reading_power_hint: dict[str, Any] | None = None,
    known_issues_to_avoid: list[dict[str, Any]] | None = None,
    pov_hint: str | None = None,
    kernel_context: dict[str, Any] | None = None,
    chapter_source_slice: Any | None = None,
    settings: Any | None = None,
) -> dict[str, Any]:
    """Repair-stage convenience wrapper."""

    return build_stage_cards(
        stage=stage,
        packet=packet,
        chapter_outline=chapter_outline,
        bridge=bridge,
        plan=plan,
        style_profile=style_profile,
        editorial_contract=editorial_contract,
        memory_hints=memory_hints,
        reading_power_hint=reading_power_hint,
        known_issues_to_avoid=known_issues_to_avoid,
        pov_hint=pov_hint,
        kernel_context=kernel_context,
        chapter_source_slice=chapter_source_slice,
        settings=settings,
    )


def build_polish_cards(
    *,
    packet: Any | None = None,
    chapter_outline: Any | None = None,
    bridge: Any | None = None,
    plan: Any | None = None,
    style_profile: Any | None = None,
    editorial_contract: Any | None = None,
    memory_hints: dict[str, Any] | None = None,
    reading_power_hint: dict[str, Any] | None = None,
    pov_hint: str | None = None,
    chapter_source_slice: Any | None = None,
    settings: Any | None = None,
) -> dict[str, Any]:
    """Polish-stage convenience wrapper.

    Stage card bundle for the POLISH (literary polish) step.
    Includes editorial (constraints), quality (hook/payoff preservation),
    memory (expression channel cooling), and style capsule.
    """

    return build_stage_cards(
        stage="polish",
        packet=packet,
        chapter_outline=chapter_outline,
        bridge=bridge,
        plan=plan,
        style_profile=style_profile,
        editorial_contract=editorial_contract,
        memory_hints=memory_hints,
        reading_power_hint=reading_power_hint,
        pov_hint=pov_hint,
        chapter_source_slice=chapter_source_slice,
        settings=settings,
    )


def build_outline_reveal_window(
    editorial_contract: Any | None,
    *,
    batch_start: int,
    batch_end: int,
    total_chapters: int = 0,
    settings: Any | None = None,
) -> dict[str, Any]:
    """Project editorial revelation ladder into an outline-batch guard card.

    This is deliberately a projection over the existing editorial contract,
    not a second compiler.  The outline stage runs before chapter contracts
    exist, so its only authoritative reveal schedule is
    ``editorial_contract.revelation_ladder``.
    """

    try:
        start = max(1, int(batch_start))
        end = max(start, int(batch_end))
    except (TypeError, ValueError):
        return {}
    try:
        total = max(0, int(total_chapters or 0))
    except (TypeError, ValueError):
        total = 0

    ladder = _revelation_ladder_records(editorial_contract)
    if not ladder:
        return {}

    future_limit = _settings_int(settings, "outline_reveal_window_future_limit", 12)
    past_limit = _settings_int(settings, "outline_reveal_window_past_limit", 6)
    current_targets: list[dict[str, Any]] = []
    future_guardrails: list[dict[str, Any]] = []
    past_reveals: list[dict[str, Any]] = []
    unanchored: list[dict[str, Any]] = []

    for item in ladder:
        target = _optional_positive_int(item.get("target_chapter"))
        if not target:
            unanchored.append(item)
        elif start <= target <= end:
            current_targets.append(item)
        elif target > end:
            future_guardrails.append(item)
        elif target < start:
            past_reveals.append(item)

    future_guardrails.sort(key=lambda row: int(row.get("target_chapter") or 10**9))
    past_reveals.sort(key=lambda row: int(row.get("target_chapter") or 0), reverse=True)

    return _drop_empty(
        {
            "batch_start": start,
            "batch_end": end,
            "total_chapters": total,
            "priority_rule": "P0 叙事揭示边界 > P1 情节推进 > P2 追读力/钩子/微兑现。",
            "allowed_policy": (
                "目标章之前只能写观察、误判、怀疑、遮蔽、间接异常；"
                "不得写成坐实、确认、印证、公开揭露或完整因果解释。"
            ),
            "current_targets": current_targets,
            "future_guardrails": future_guardrails[: max(0, future_limit)],
            "past_reveals": past_reveals[: max(0, past_limit)],
            "unanchored": unanchored,
            "withheld_future_count": max(0, len(future_guardrails) - max(0, future_limit)),
        }
    )


def _revelation_ladder_records(editorial_contract: Any | None) -> list[dict[str, Any]]:
    raw = field(editorial_contract, "revelation_ladder", [])
    if raw is None:
        return []
    values = raw if isinstance(raw, (list, tuple)) else [raw]
    records: list[dict[str, Any]] = []
    for item in values:
        data = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
        if not isinstance(data, dict):
            continue
        record = _drop_empty(
            {
                "thread": _text(data.get("thread")),
                "stage_order": _optional_positive_int(data.get("stage_order")) or 0,
                "stage": _text(data.get("stage")),
                "target_chapter": _optional_positive_int(data.get("target_chapter")),
                "trigger": _text(data.get("trigger")),
                "allowed_disclosure": _text(data.get("allowed_disclosure")),
                "required_action_consequence": _text(data.get("required_action_consequence")),
            }
        )
        if record:
            records.append(record)
    records.sort(
        key=lambda row: (
            int(row.get("target_chapter") or 10**9),
            int(row.get("stage_order") or 0),
            str(row.get("thread") or ""),
        )
    )
    return records


def _build_chapter_card(
    outline: Any,
    *,
    packet: Any | None,
    chapter_position: dict[str, Any] | None = None,
) -> dict[str, Any]:
    chapter_number = field(outline, "chapter_number", field(packet, "chapter_number", 0))
    position = dict(chapter_position or {})
    if not position and field(outline, "total_chapters", field(packet, "total_chapters", 0)):
        position = build_chapter_position(outline, int(chapter_number or 1))
    payload = {
        "chapter_number": chapter_number,
        "title": _text(field(outline, "title", "")),
        "goal": _text(field(outline, "goal", "")),
        "notes": _text(field(outline, "notes", "")),
        "pov_character_id": _text(field(outline, "pov_character_id", "")),
        "pov_character_name": _text(
            field(outline, "pov_character_name", field(outline, "pov_character", ""))
        ),
        "pov_character": _text(field(outline, "pov_character", "")),
        "setting": _text(field(outline, "setting", "")),
        "target_word_count": field(outline, "expected_word_count", 0),
        "pov_switch": bool(field(outline, "pov_switch", False)),
        "involved_character_ids": _string_list(field(outline, "involved_character_ids", [])),
        "required_character_ids": _string_list(field(outline, "required_character_ids", [])),
        "support_character_ids": _string_list(field(outline, "support_character_ids", [])),
        "cast_plan": _as_mapping(field(outline, "cast_plan", {})),
        "emotional_plan": _as_mapping(field(outline, "emotional_plan", {})),
        "scene_design_goals": _string_list(field(outline, "scene_design_goals", [])),
        "main_plot_points": _string_list(field(outline, "main_plot_points", [])),
        "subplot_points": _string_list(field(outline, "subplot_points", [])),
        "beats_summary": _string_list(field(outline, "beats_summary", [])),
    }
    if position:
        payload.update(
            {
                "total_chapters": position.get("total_chapters"),
                "is_last_chapter": bool(position.get("is_last_chapter")),
                "chapter_position": position,
            }
        )
    return _drop_empty(payload)


def _involved_characters(outline: Any | None) -> list[str]:
    names = _string_list(field(outline, "involved_characters", []))
    pov = _text(field(outline, "pov_character", ""))
    if pov and pov not in names:
        names.insert(0, pov)
    return names


def _build_subplot_weave_card(packet: Any | None) -> dict[str, Any]:
    narrative_context = field(packet, "narrative_context", None)
    return _drop_empty(
        {
            "active_subplot_names": _string_list(
                field(narrative_context, "active_subplot_names", [])
            ),
            "weave_hints": _string_list(field(narrative_context, "active_subplot_weave_hints", [])),
            "dependency_warnings": _string_list(
                field(narrative_context, "subplot_dependency_warnings", [])
            ),
        }
    )


def _build_contract_card(
    *,
    packet: Any | None,
    outline: Any | None,
    canon_context: Any,
    story_bible: dict[str, Any],
    narrative_contract: Any | None,
    milestone_window: dict[str, Any] | None,
    stage: str,
    settings: Any | None,
) -> dict[str, Any]:
    contract = _as_mapping(field(packet, "chapter_contract", {}) or {})
    canon = _as_mapping(canon_context or {})
    forbidden_changes = _string_list(contract.get("forbidden_changes", []))
    guard_constraints = _string_list(field(packet, "guard_constraints", []))
    world_rules = [
        *_string_list(story_bible.get("rules", [])),
        *_string_list(field(narrative_contract, "world_rules", [])),
    ]
    milestone_payload = _stage_milestones_for_contract(milestone_window or {}, stage=stage)
    hard_facts = (
        []
        if stage == "wave"
        else _dedupe_hard_facts(_string_list(canon.get("immutable_facts", [])))
    )
    return _drop_empty(
        {
            "entry_state_requirements": _string_list(contract.get("entry_state_requirements", [])),
            "required_events": _string_list(contract.get("required_events", [])),
            "forbidden_changes": forbidden_changes,
            "required_progressions": _string_list(contract.get("required_progressions", [])),
            "allowed_progressions": _string_list(contract.get("allowed_progressions", [])),
            "forbidden_progressions": _string_list(contract.get("forbidden_progressions", [])),
            "completion_criteria": _string_list(contract.get("completion_criteria", [])),
            "future_leak_risks": _string_list(contract.get("future_leak_risks", [])),
            "exit_state_targets": _string_list(contract.get("exit_state_targets", [])),
            "cognitive_constraints": project_cognitive_constraints(
                contract.get("cognitive_constraints", [])
            ),
            "guard_constraints": guard_constraints,
            "must_carry_forward": _string_list(field(packet, "must_carry_forward", [])),
            "hard_facts": hard_facts,
            "world_rules": world_rules,
            "time_convention": _text(story_bible.get("time_convention", "")),
            "pov_character": _text(field(outline, "pov_character", "")),
            "hard_boundary": "；".join([*forbidden_changes, *guard_constraints]),
            "milestone_window": milestone_payload,
        }
    )


def _merge_source_runtime_contract(
    contract_card: dict[str, Any],
    source_cards: dict[str, Any],
) -> dict[str, Any]:
    """Prefer verified source-slice runtime contract values for P0 fields."""

    source_contract = _as_mapping(source_cards.get("chapter_contract"))
    world_rule_card = _as_mapping(source_cards.get("world_rule_card"))
    if not source_contract and not world_rule_card:
        return contract_card
    merged = dict(contract_card)
    if world_rule_card:
        # The source slice already carries complete always-on and chapter-
        # relevant rules. Keeping the full story-bible list would duplicate
        # prompt weight and bypass the source selection boundary.
        merged.pop("world_rules", None)
    for key in (
        "entry_state_requirements",
        "required_events",
        "required_progressions",
        "allowed_progressions",
        "forbidden_changes",
        "forbidden_progressions",
        "completion_criteria",
        "future_leak_risks",
        "exit_state_targets",
        "cognitive_constraints",
    ):
        if source_contract.get(key) not in (None, "", [], {}):
            merged[key] = source_contract.get(key)
    if source_contract.get("p0_required_progressions"):
        merged["required_progressions"] = source_contract["p0_required_progressions"]
    if source_contract.get("p0_forbidden_boundaries"):
        merged["forbidden_reveal_boundaries"] = source_contract["p0_forbidden_boundaries"]
    if source_contract.get("knowledge_boundaries"):
        merged["cognitive_constraints"] = source_contract["knowledge_boundaries"]
    if source_contract.get("entity_refs"):
        merged["entity_refs"] = source_contract["entity_refs"]
    hidden_counts = _as_mapping(source_contract.get("hidden_counts"))
    if hidden_counts:
        merged["runtime_hidden_counts"] = hidden_counts
    return _drop_empty(merged)


def _build_state_card(
    canon_context: Any,
    *,
    memory_hints: dict[str, Any],
    settings: Any | None,
) -> dict[str, Any]:
    canon = _as_mapping(canon_context or {})
    authoritative_state = _compact_authoritative_state(
        _as_mapping(memory_hints.get("authoritative_narrative_state"))
        or _as_mapping(canon.get("authoritative_narrative_state")),
        settings=settings,
    )
    return _drop_empty(
        {
            "authoritative_narrative_state": authoritative_state,
            "entity_reference_graph": _entity_reference_graph_card(
                canon.get("entity_reference_graph")
            ),
            "chapter_contract": _as_mapping(canon.get("chapter_contract")),
        }
    )


def _build_narration_card(pov_hint: str | None) -> dict[str, Any]:
    from novel_forge.pipeline.narrative_person import build_narrative_person_context

    context = build_narrative_person_context(pov_hint or "")
    return _drop_empty(
        {
            "pov_hint": _text(context.get("pov_hint", "")),
            "rule": _text(context.get("narrative_person_rule", "")),
            "allows_first_person": bool(context.get("narrative_person_allows_first_person", False)),
        }
    )


def _build_bridge_card(
    *,
    packet: Any | None,
    bridge: Any | None,
    settings: Any | None,
) -> dict[str, Any]:
    previous_exit = field(packet, "previous_exit_state", None)
    previous_bridge = field(packet, "previous_bridge", None)
    causal = field(bridge, "causal_link", None)
    previous_causal = field(previous_bridge, "causal_link", None)
    return _drop_empty(
        {
            "opening_time": _text(field(bridge, "opening_time", "")),
            "opening_location": _text(field(bridge, "opening_location", "")),
            "opening_pov": _text(field(bridge, "opening_pov", "")),
            "transition_mode": _text(field(bridge, "transition_mode", "")),
            "emotional_carryover": _text(field(bridge, "emotional_carryover", "")),
            "action_handoff": _text(field(bridge, "action_handoff", "")),
            "bridge_summary": _text(field(bridge, "bridge_summary", "")),
            "pending_questions": _string_list(field(bridge, "pending_questions", [])),
            "sensory_anchors": _string_list(field(bridge, "sensory_anchors", [])),
            "opening_acceptance_criteria": _string_list(
                field(bridge, "opening_acceptance_criteria", [])
            ),
            "boundary_window_policy": _drop_empty(
                {
                    "previous_tail_paragraphs": field(
                        settings,
                        "long_boundary_prev_tail_paragraphs",
                        5,
                    ),
                    "opening_paragraphs": field(
                        settings,
                        "long_boundary_opening_paragraphs",
                        3,
                    ),
                }
            ),
            "causal_link": _causal_link_card(causal),
            "previous_exit": _drop_empty(
                {
                    "chapter_number": field(previous_exit, "chapter_number", 0),
                    "time_marker": _text(field(previous_exit, "time_marker", "")),
                    "location": _text(field(previous_exit, "location", "")),
                    "pov": _text(field(previous_exit, "pov", "")),
                    "must_carry_forward": _string_list(
                        field(previous_exit, "must_carry_forward", [])
                    ),
                    "open_questions": _string_list(field(previous_exit, "open_questions", [])),
                }
            ),
            "previous_causal_link": _drop_empty(
                {
                    "previous_event": _text(field(previous_causal, "previous_event", "")),
                    "unresolved_question": _text(field(previous_causal, "unresolved_question", "")),
                }
            ),
            "previous_chapter_ending": _text(field(packet, "previous_chapter_ending", "")),
            "bridge_context_brief": _text(field(packet, "bridge_context_brief", "")),
            "continuity_context_brief": _text(field(packet, "continuity_context_brief", "")),
            "forbidden_repetition": _string_list(field(bridge, "forbidden_repetition", [])),
            "previous_forbidden_repetition": _string_list(
                field(previous_bridge, "forbidden_repetition", [])
            ),
        }
    )


def _causal_link_card(source: Any) -> dict[str, Any]:
    card = {
        "previous_event": _text(field(source, "previous_event", "")),
        "causal_mechanism": _text(field(source, "causal_mechanism", "")),
        "unresolved_question": _text(field(source, "unresolved_question", "")),
        "open_threads": _string_list(field(source, "open_threads", [])),
    }
    if not any(
        (
            card["previous_event"],
            card["causal_mechanism"],
            card["unresolved_question"],
            card["open_threads"],
        )
    ):
        return {}
    return card


def _build_opening_evidence_card(
    kernel_context: Any | None,
    *,
    outline: Any | None,
) -> dict[str, Any]:
    """Return the smallest kernel evidence slice needed to choose an opening.

    Bridge is intentionally lighter than Plan.  It still needs a few facts
    that determine whether an opening POV can act, whom they are tense with,
    and which already-due thread may naturally carry into the scene.  This
    projection excludes private secrets and future payoffs, so it improves
    continuity without turning Bridge into a second planning prompt.
    """

    kernel = _as_mapping(kernel_context)
    pov = _text(field(outline, "pov_character", ""))
    current_chapter = _optional_positive_int(field(outline, "chapter_number", 0)) or 0
    entities = list(kernel.get("entities") or [])
    lookup = build_entity_lookup(entities)
    id_to_name = lookup.get("id_to_name", {})
    name_to_id = lookup.get("name_to_id", {})
    pov_id = name_to_id.get(pov, pov)
    focus_names = set(_involved_characters(outline))

    relationships: list[dict[str, Any]] = []
    for raw in list(kernel.get("relationships") or []):
        data = _as_mapping(raw)
        source_id = _text(data.get("source_entity_id"))
        target_id = _text(data.get("target_entity_id"))
        if not pov_id or pov_id not in {source_id, target_id}:
            continue
        other_id = target_id if source_id == pov_id else source_id
        other_name = _text(id_to_name.get(other_id, other_id))
        if not other_name:
            continue
        if focus_names and other_name not in focus_names:
            continue
        relationships.append(
            _drop_empty(
                {
                    "with_character": other_name,
                    "label": _text(data.get("label") or data.get("relation_type")),
                    "status": _text(data.get("status")),
                    "shift_summary": _text(data.get("shift_summary")),
                    "last_shift_chapter": _optional_positive_int(data.get("last_shift_chapter")),
                }
            )
        )
    pov_knowledge: list[dict[str, str]] = []
    for raw in list(kernel.get("knowledge_ledger") or []):
        data = _as_mapping(raw)
        if not pov_id or _text(data.get("entity_id")) != pov_id:
            continue
        if not _is_knowledge_temporally_valid(data, current_chapter) or not _is_knowledge_revealed(
            data, current_chapter
        ):
            continue
        knowledge_type = normalize_knowledge_type(data.get("knowledge_type"))
        if knowledge_type not in {"known", "suspected", "misbelief"}:
            continue
        fact = _text(data.get("fact"))
        if fact:
            pov_knowledge.append({"type": knowledge_type, "fact": fact})
    due_promises: list[dict[str, str | int]] = []
    for raw in list(kernel.get("promise_ledger") or []):
        data = _as_mapping(raw)
        status = _text(data.get("status")).lower()
        planted_chapter = _optional_positive_int(data.get("planted_chapter")) or 0
        payoff_chapter = _optional_positive_int(data.get("payoff_chapter")) or 0
        # Only scheduled payoffs for this chapter qualify.  Unscheduled
        # promises stay in Plan's broader P1 context instead of distracting
        # the opening decision.
        if (
            status in {"paid", "broken"}
            or not current_chapter
            or payoff_chapter != current_chapter
            or planted_chapter > current_chapter
        ):
            continue
        description = _text(data.get("description"))
        if description:
            due_promises.append(
                {
                    "description": description,
                    "type": _text(data.get("promise_type")),
                    "payoff_chapter": payoff_chapter,
                }
            )
    return _drop_empty(
        {
            "pov": pov,
            "relationship_shifts": relationships,
            "pov_knowledge": pov_knowledge,
            "due_promises": due_promises,
            "usage": "仅用于判断开场行动与信息边界；不授权新增事件、解释或未来揭示。",
        }
    )


def _build_opening_bridge_card(
    source: Any | None,
    *,
    settings: Any | None = None,
) -> dict[str, Any]:
    if not source:
        return {}
    return _drop_empty(
        {
            "opening_time": _text(field(source, "opening_time", "")),
            "opening_location": _text(field(source, "opening_location", "")),
            "opening_pov": _text(field(source, "opening_pov", "")),
            "transition_mode": _text(field(source, "transition_mode", "")),
            "emotional_carryover": _text(field(source, "emotional_carryover", "")),
            "action_handoff": _text(field(source, "action_handoff", "")),
            "bridge_summary": _text(field(source, "bridge_summary", "")),
            "pending_questions": _string_list(field(source, "pending_questions", [])),
            "sensory_anchors": _string_list(field(source, "sensory_anchors", [])),
            "opening_acceptance_criteria": _string_list(
                field(source, "opening_acceptance_criteria", [])
            ),
            "boundary_window_policy": _drop_empty(
                {
                    "previous_tail_paragraphs": field(
                        settings,
                        "long_boundary_prev_tail_paragraphs",
                        5,
                    ),
                    "opening_paragraphs": field(
                        settings,
                        "long_boundary_opening_paragraphs",
                        3,
                    ),
                }
            ),
            "causal_link": _causal_link_card(field(source, "causal_link", None)),
        }
    )


def _build_cross_scene_intent(plan: Any | None) -> dict[str, Any]:
    """Extract cross_scene_intent from plan, tolerate dict / object / missing."""
    if not plan:
        return {}
    raw = field(plan, "cross_scene_intent", None)
    if not raw:
        return {}
    if isinstance(raw, dict):
        data = raw
    else:
        data = _as_mapping(raw)
    refs_raw = list(data.get("cross_scene_references", []) or [])
    refs: list[dict[str, Any]] = []
    for item in refs_raw:
        m = _as_mapping(item)
        if not m:
            continue
        refs.append(
            _drop_empty(
                {
                    "from_scene": _text(m.get("from_scene", "")),
                    "to_scene": _text(m.get("to_scene", "")),
                    "ref_type": _text(m.get("ref_type", "callback")),
                    "description": _text(m.get("description", "")),
                    "requirement": _as_mapping(m.get("requirement")),
                }
            )
        )
    pacing = _positive_int_list(data.get("pacing_curve", []))
    return _drop_empty(
        {
            "cross_scene_references": refs,
            "pacing_curve": pacing,
        }
    )


# Stage-aware scene_intent field whitelist.
# DRAFT sees the craft fields, emotional/sensory/dialogue dimensions, plus P0 POV knowledge constraints.
# WAVE sees the full set (focus on cross-scene wiring and verification).
# EDIT keeps the full set (legacy behavior preserved).
_SCENE_INTENT_STAGE_FIELDS: dict[str, list[str] | None] = {
    "draft": [
        "scene_id",
        "summary",
        "purpose",
        "conflict",
        "required_characters",
        "character_motivations",
        "entry_state_refs",
        "required_outcome",
        # P0 delivery ownership: DRAFT must see the same atomic checklist that
        # Planning and Review use.  Keeping it out of the draft projection made
        # long semicolon-delimited outcomes easy for the writer to compress or
        # silently skip.
        "owned_events",
        "owned_revelations",
        "owned_state_changes",
        "dramatic_question",
        "exit_target_state",
        "target_words",
        "pov_character",
        "pov_scope",
        "pov_switch_allowed",
        "pov_switch_marker_required",
        "pov_knowledge_constraints",
        "world_rule_ids",
        "world_rule_usage",
        "world_rule_evidence_expectations",
        "world_rule_forbidden_boundaries",
        "location",
        "time_marker",
        "choice_pressure",
        "scene_resistance",
        # Whole-chapter DRAFT owns the first executable scene structure.  These
        # fields prevent it from collapsing adjacent scenes or losing the
        # planned entry/exit handoff before WAVE receives the prose.
        "scene_goal",
        "forbidden_overlap",
        "handoff_to_next",
        "entry_state",
        "exit_state",
        "draft_order",
        "revelation_level",
        "symbol_usage_policy",
        "body_signal_budget",
        # Creative dimensions — DRAFT must see emotional/sensory/dialogue targets
        # so quality is built in at generation, not just repaired downstream.
        "emotional_beat",
        "sensory_notes",
        "dialogue_voice_targets",
        "dialogue_subtext",
        "sensory_focus",
        "relationship_dynamics",
        "opening_strategy",
        "scene_architecture",
    ],
    "wave": None,  # None = all fields
}


def _filter_scene_intent_for_stage(scene_card: dict[str, Any], stage: str) -> dict[str, Any]:
    """Filter a scene_intent card to the fields the given stage is allowed to see.

    ``stage="draft"`` keeps the scene craft fields plus P0 POV constraints;
    ``stage="wave"`` (or any other) keeps the full set.
    """
    allowed = _SCENE_INTENT_STAGE_FIELDS.get(stage)
    if allowed is None:
        return scene_card
    return {k: v for k, v in scene_card.items() if k in allowed}


def _pov_knowledge_constraints_card(value: Any) -> dict[str, Any]:
    data = _as_mapping(value)
    return {
        "forbidden_knowledge": _string_list(data.get("forbidden_knowledge", [])),
        "sensory_limits": _string_list(data.get("sensory_limits", [])),
        "scope_label": _text(data.get("scope_label", "limited")) or "limited",
    }


def _build_plan_card(
    plan: Any | None,
    *,
    stage: str = "",
    settings: Any | None = None,
) -> dict[str, Any]:
    if not plan:
        return {}
    source_scenes = list(field(plan, "scene_intents", []) or [])
    raw_scenes = [_scene_intent_card(scene) for scene in source_scenes]
    if stage:
        scenes = [_filter_scene_intent_for_stage(s, stage) for s in raw_scenes]
    else:
        scenes = raw_scenes
    opening_bridge = _build_opening_bridge_card(
        field(plan, "opening_bridge", None),
        settings=settings,
    )
    return _drop_empty(
        {
            "opening_contract": _text(field(plan, "opening_contract", "")),
            "opening_bridge": opening_bridge,
            "closing_contract": _text(field(plan, "closing_contract", "")),
            "chapter_type": _text(field(plan, "chapter_type", "")),
            "scene_intents": scenes,
            "guidance_requirements": [
                item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                for item in list(field(plan, "guidance_requirements", []) or [])
            ],
            "scene_count": len(scenes),
            "world_rule_applications": [
                item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                for item in list(field(plan, "world_rule_applications", []) or [])
            ],
            "target_word_total": sum(
                int(field(scene, "target_words", 0) or 0) for scene in source_scenes
            ),
            "required_state_transitions": _string_list(
                field(plan, "required_state_transitions", [])
            ),
            "required_literals": [
                item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)
                for item in list(field(plan, "required_literals", []) or [])
                if hasattr(item, "model_dump") or isinstance(item, dict)
            ],
            "emotional_arc": _text(field(plan, "emotional_arc", "")),
            "relationship_evolution": _string_list(field(plan, "relationship_evolution", [])),
            "key_revelations": _string_list(field(plan, "key_revelations", [])),
            "foreshadowing_plan": _string_list(field(plan, "foreshadowing_plan", [])),
            "forbidden_elements": _string_list(field(plan, "forbidden_elements", [])),
            "forbidden_elements_soft": _string_list(field(plan, "forbidden_elements_soft", [])),
            "forbidden_elements_quota": _string_list(field(plan, "forbidden_elements_quota", [])),
            "expression_channel_records": _expression_channel_records(
                field(plan, "expression_channel_records", [])
            ),
            "intentional_callbacks": _string_list(field(plan, "intentional_callbacks", [])),
            "cross_scene_intent": _build_cross_scene_intent(plan),
        }
    )


def _scene_intent_card(scene: Any) -> dict[str, Any]:
    required_outcome = _text(field(scene, "required_outcome", ""))
    owned_events = _atomic_string_list(field(scene, "owned_events", []))
    for outcome_item in _atomic_string_list(required_outcome):
        if outcome_item not in owned_events:
            owned_events.append(outcome_item)

    return {
        "scene_id": _text(field(scene, "scene_id", "")) or "scene",
        "summary": _text(field(scene, "summary", "")),
        "purpose": _text(field(scene, "purpose", "")),
        "conflict": _text(field(scene, "conflict", "")),
        "required_characters": _string_list(field(scene, "required_characters", [])),
        "character_motivations": _motivation_list(scene),
        "entry_state_refs": _string_list(field(scene, "entry_state_refs", [])),
        "required_outcome": required_outcome,
        "dramatic_question": _text(field(scene, "dramatic_question", "")),
        "exit_target_state": _text(field(scene, "exit_target_state", "")),
        "location": _text(field(scene, "location", "")),
        "time_marker": _text(field(scene, "time_marker", "")),
        "sensory_notes": _text(field(scene, "sensory_notes", "")),
        "sensory_focus": _text(field(scene, "sensory_focus", "")),
        "dialogue_subtext": _text(field(scene, "dialogue_subtext", "")),
        "emotional_beat": _text(field(scene, "emotional_beat", "")),
        "relationship_dynamics": _text(field(scene, "relationship_dynamics", "")),
        "choice_pressure": _text(field(scene, "choice_pressure", "")),
        "scene_resistance": _text(field(scene, "scene_resistance", "")),
        "dialogue_voice_targets": _as_mapping(field(scene, "dialogue_voice_targets", {})),
        "revelation_level": _text(field(scene, "revelation_level", "")),
        "symbol_usage_policy": _text(field(scene, "symbol_usage_policy", "")),
        "body_signal_budget": field(scene, "body_signal_budget", 1),
        "target_words": field(scene, "target_words", 0) or 0,
        "pov_character": _text(field(scene, "pov_character", "")),
        "pov_scope": _text(field(scene, "pov_scope", "")),
        "pov_switch_allowed": bool(field(scene, "pov_switch_allowed", False)),
        "pov_switch_marker_required": bool(field(scene, "pov_switch_marker_required", True)),
        "pov_knowledge_constraints": _pov_knowledge_constraints_card(
            field(scene, "pov_knowledge_constraints", {})
        ),
        "world_rule_ids": _string_list(field(scene, "world_rule_ids", [])),
        "world_rule_usage": _text(field(scene, "world_rule_usage", "")),
        "world_rule_evidence_expectations": _string_list(
            field(scene, "world_rule_evidence_expectations", [])
        ),
        "world_rule_forbidden_boundaries": _string_list(
            field(scene, "world_rule_forbidden_boundaries", [])
        ),
        "scene_goal": _text(field(scene, "scene_goal", "")),
        "owned_events": owned_events,
        "owned_revelations": _atomic_string_list(field(scene, "owned_revelations", [])),
        "owned_state_changes": _atomic_string_list(field(scene, "owned_state_changes", [])),
        "forbidden_overlap": _string_list(field(scene, "forbidden_overlap", [])),
        "handoff_to_next": _text(field(scene, "handoff_to_next", "")),
        "dependency_scene_ids": _string_list(field(scene, "dependency_scene_ids", [])),
        "parallel_group": _text(field(scene, "parallel_group", "")),
        "draft_order": field(scene, "draft_order", 0) or 0,
        "entry_state": _text(field(scene, "entry_state", "")),
        "exit_state": _text(field(scene, "exit_state", "")),
    }


def _build_character_cards(
    packet: Any | None,
    *,
    canon_context: Any | None = None,
    detail: str = "full",
    pov_name: str = "",
) -> list[dict[str, Any]]:
    """Build character cards.

    ``detail`` values:
      * ``"brief"`` — name / role / identity only.
      * ``"full"`` — name / role / identity / gender / social_status /
        personality / **voice** (for backward compat with edit / plan).
      * ``"voice_only"`` — name + voice string only. Used by WAVE stage
        which only consumes voice data from the characters card; detailed
        voice info comes from editorial.character_voices.
      * ``"pov_full_others_brief"`` — POV character gets the full card
        (including voice); all other characters get the brief card.
        Used by the DRAFT stage to keep the prompt small while
        guaranteeing the POV character's voice is visible to the
        writer.
    """
    canon_chars = field(canon_context, "characters", {}) or {}
    pov_full_others_brief = detail == "pov_full_others_brief"
    cards: list[dict[str, Any]] = []
    for profile in list(field(packet, "character_profiles", []) or []):
        name = _text(field(profile, "name", ""))
        if not name:
            continue
        is_pov = bool(pov_name) and name == pov_name
        if pov_full_others_brief and not is_pov:
            effective_detail = "brief"
        else:
            effective_detail = detail
        brief = effective_detail == "brief"
        voice_only = effective_detail == "voice_only"
        card: dict[str, Any] = {
            "name": name,
            "role": _text(field(profile, "role", "")),
            "identity": _character_identity(profile, canon_chars, brief=brief or voice_only),
        }
        if voice_only:
            card["voice"] = _text(field(profile, "voice", ""))
        elif not brief:
            card["gender"] = _text(field(profile, "gender", ""))
            card["social_status"] = _character_social_status(name, canon_chars)
            card["personality"] = _text(field(profile, "personality", ""))
            card["voice"] = _text(field(profile, "voice", ""))
        return_card = _drop_empty(card) if not brief else card
        cards.append(return_card)
    return cards


def _is_knowledge_temporally_valid(data: dict[str, Any], current_chapter: int) -> bool:
    if current_chapter <= 0:
        return True
    source_chapter = int(data.get("source_chapter") or 0)
    return source_chapter == 0 or source_chapter <= current_chapter


def _is_knowledge_visible_to_character(
    data: dict[str, Any],
    entity_id: str,
    pov_character: str,
    *,
    id_to_name: dict[str, str] | None = None,
) -> bool:
    if not pov_character:
        return True
    visibility = _text(data.get("visibility")).lower() or "private"
    if visibility == "public":
        return True
    entity_name = (id_to_name or {}).get(entity_id, entity_id) if id_to_name else entity_id
    return entity_name == pov_character


def _is_knowledge_revealed(data: dict[str, Any], current_chapter: int) -> bool:
    if current_chapter <= 0:
        return True
    revealed_in = int(data.get("revealed_in_chapter") or 0)
    return revealed_in == 0 or revealed_in <= current_chapter


def _build_knowledge_card(
    *,
    kernel_context: dict[str, Any],
    chapter_contract: dict[str, Any],
    outline: Any | None,
    plan: Any | None,
    stage: str,
    current_chapter: int = 0,
    pov_character: str = "",
) -> dict[str, Any]:
    """Project StoryKernel knowledge into chapter-scoped writing constraints."""

    entities = list(kernel_context.get("entities") or [])
    lookup = build_entity_lookup(entities)
    knowledge_ops = normalize_knowledge_ops(
        chapter_contract.get("knowledge_ops", []),
        entity_lookup=lookup,
        limit=len(chapter_contract.get("knowledge_ops", []) or []),
    )
    wanted_names = _knowledge_focus_names(outline, plan, knowledge_ops)
    id_to_name = lookup.get("id_to_name", {})
    name_to_id = lookup.get("name_to_id", {})
    # Build wanted entity IDs for character-first filtering
    wanted_ids: set[str] = set()
    for name in wanted_names:
        eid = name_to_id.get(name, name)
        wanted_ids.add(eid)

    ledger_by_entity: dict[str, dict[str, list[str]]] = {}
    for entry in list(kernel_context.get("knowledge_ledger") or []):
        data = _as_mapping(entry)
        entity_id = _text(data.get("entity_id"))
        if not entity_id:
            continue
        # Character-first: skip entries for non-wanted entities immediately
        if entity_id not in wanted_ids:
            continue
        if not _is_knowledge_temporally_valid(data, current_chapter):
            continue
        if not _is_knowledge_visible_to_character(
            data, entity_id, pov_character, id_to_name=id_to_name
        ):
            continue
        if not _is_knowledge_revealed(data, current_chapter):
            continue
        bucket = ledger_by_entity.setdefault(
            entity_id,
            {"known": [], "suspected": [], "misbelief": [], "secret_kept": []},
        )
        knowledge_type = _text(data.get("knowledge_type")) or "known"
        if knowledge_type not in bucket:
            knowledge_type = "known"
        fact = _text(data.get("fact"))
        if fact and fact not in bucket[knowledge_type]:
            bucket[knowledge_type].append(fact)

    op_by_character: dict[str, list[dict[str, Any]]] = {}
    global_ops: list[dict[str, Any]] = []
    for op in knowledge_ops:
        character = _text(op.get("character"))
        if character:
            op_by_character.setdefault(character, []).append(op)
            if character not in wanted_names:
                wanted_names.append(character)
        else:
            global_ops.append(op)

    cards: list[dict[str, Any]] = []
    pov = _text(field(outline, "pov_character", ""))
    for name in wanted_names:
        entity_id = name_to_id.get(name, name)
        bucket = ledger_by_entity.get(entity_id) or ledger_by_entity.get(name) or {}
        card = _drop_empty(
            {
                "character": name,
                "entity_id": entity_id if entity_id != name or name in id_to_name else "",
                "is_pov": name == pov,
                "known_facts": _string_list(bucket.get("known", [])),
                "suspicions": _string_list(bucket.get("suspected", [])),
                "misbeliefs": _string_list(bucket.get("misbelief", [])),
                # Stage-aware bucket visibility: DRAFT hides non-POV secrets
                # to prevent early reveals; WAVE (and others) keep all 4.
                "secrets_kept": _string_list(bucket.get("secret_kept", []))
                if stage != "draft" or name == pov
                else [],
                "chapter_promised_changes": op_by_character.get(name, []),
            }
        )
        if card:
            cards.append(card)

    return _drop_empty(
        {
            "source": "story_kernel",
            "stage": stage,
            "character_cards": cards,
            "chapter_ops": knowledge_ops,
            "global_ops": global_ops,
        }
    )


def _knowledge_focus_names(
    outline: Any | None,
    plan: Any | None,
    knowledge_ops: list[dict[str, Any]],
) -> list[str]:
    names = _involved_characters(outline)
    for scene in list(field(plan, "scene_intents", []) or []):
        pov = _text(field(scene, "pov_character", ""))
        if pov:
            names.append(pov)
        names.extend(_string_list(field(scene, "required_characters", [])))
    for op in knowledge_ops:
        character = _text(op.get("character"))
        if character:
            names.append(character)
    seen: set[str] = set()
    result: list[str] = []
    for name in names:
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result


def _character_social_status(name: str, canon_chars: Any) -> str:
    canon_char = field(canon_chars, name, None)
    return _text(field(canon_char, "social_status", ""))


def _character_identity(profile: Any, canon_chars: Any, *, brief: bool) -> str:
    name = _text(field(profile, "name", ""))
    social_status = _character_social_status(name, canon_chars)
    if brief:
        identity = _text(field(profile, "identity", "")) or _text(field(profile, "role", ""))
    else:
        identity = _text(field(profile, "identity", field(profile, "backstory", "")))
        identity = identity or _text(field(profile, "personality", ""))
        identity = identity or _text(field(profile, "role", ""))
    if social_status and identity:
        return f"{social_status}。{identity}"
    return social_status or identity


def _build_style_capsule(style_profile: Any | None, *, stage: str) -> dict[str, Any]:
    if not style_profile:
        return {}
    modules = []
    for module in list(field(style_profile, "modules", []) or []):
        card = _drop_empty(
            {
                "name": _text(field(module, "name", "")),
                "rules": _string_list(field(module, "rules", [])),
            }
        )
        # _drop_empty strips keys with empty string values; ensure "name"
        # is always present so templates can always access module.name.
        if "name" not in card:
            card["name"] = _text(field(module, "name", "")) or "未命名模块"
        modules.append(card)
    global_style = field(style_profile, "global_style", {})
    cool_point = field(style_profile, "cool_point_config", {})
    hook_config = field(style_profile, "hook_config", {})
    micro_payoff = field(style_profile, "micro_payoff_config", {})
    include_banned_phrases = stage in {
        "draft",
        "edit",
        "polish",
        "repair",
        "continuity_repair",
        "causal_repair",
    }
    return _drop_empty(
        {
            "summary": _text(field(style_profile, "summary", "")),
            "modules": modules,
            "dialogue_ratio": _text(field(global_style, "dialogue_ratio", "")),
            "pace_mode": _text(field(global_style, "pace_mode", "")),
            "emotional_style": _text(field(global_style, "emotional_style", "")),
            "environment_ratio": _text(field(global_style, "environment_ratio", "")),
            "info_density": _text(field(global_style, "info_density", "")),
            "banned_phrases": _string_list(field(global_style, "banned_phrases", []))
            if include_banned_phrases
            else [],
            "cool_point_patterns": _string_list(field(cool_point, "preferred_patterns", []))
            if stage in {"plan", "draft", "edit"}
            else [],
            "cool_point_density": _text(field(cool_point, "density_per_chapter", ""))
            if stage in {"plan", "draft", "edit"}
            else "",
            "hook_preferred_types": _string_list(field(hook_config, "preferred_types", []))
            if stage in {"plan", "draft", "edit"}
            else [],
            "hook_strength_baseline": _text(field(hook_config, "strength_baseline", ""))
            if stage in {"plan", "draft", "edit"}
            else "",
            "hook_chapter_end_required": bool(field(hook_config, "chapter_end_required", False))
            if stage in {"plan", "draft", "edit"}
            else False,
            "micro_payoff_types": _string_list(field(micro_payoff, "preferred_types", []))
            if stage in {"plan", "draft", "edit"}
            else [],
            "micro_payoff_min_per_chapter": field(micro_payoff, "min_per_chapter", 0)
            if stage in {"plan", "draft", "edit"}
            else 0,
        }
    )


def _build_memory_capsule(memory_hints: dict[str, Any], *, stage: str) -> dict[str, Any]:
    """Build a stage-scoped memory capsule.

    Each stage receives only the fields its template actually renders (verified
    against bridge_chapter.j2, plan_chapter.j2, draft_chapter.j2).  Stages that
    use independent memory mechanisms (repair steps via ``memory_guidance`` /
    ``memory_context``) receive a minimal identity-only capsule.
    """
    layered = (
        memory_hints.get("layered_context") or memory_hints.get("memory_layered_context") or {}
    )
    motif = memory_hints.get("motif_continuity") or {}
    unified_guidance = _text(
        memory_hints.get("unified_guidance") or field(motif, "unified_guidance", "")
    )

    # ── repair / edit / dedup_pronoun: templates use independent memory paths ──
    if stage.endswith("repair") or stage in {"edit", "review", "dedup_pronoun"}:
        return _drop_empty(
            {
                "project_identity": _text(field(layered, "L0_identity", "")),
                "core_memory": _text(field(layered, "L1_core_memory", "")),
            }
        )

    # ── Bridge: only consumes motif_repetition_risks ──
    if stage == "bridge":
        return _drop_empty(
            {
                "motif_repetition_risks": _string_list(field(motif, "forbidden_repetition", [])),
            }
        )

    # ── Plan: fields actually rendered by plan_chapter.j2 ──
    if stage == "plan":
        return _drop_empty(
            {
                "previous_chapter_events": _event_list(
                    memory_hints.get("previous_chapter_events", [])
                ),
                "relevant_history": _event_list(memory_hints.get("relevant_history", [])),
                "summary_context": _text(field(layered, "L1_core_memory", "")),
                "outline_context": _outline_context_text(memory_hints.get("outline_context")),
                "active_motifs": _string_list(field(motif, "active_motifs", [])),
                "motif_repetition_risks": _string_list(field(motif, "forbidden_repetition", [])),
                "unified_guidance": unified_guidance,
                "expression_channel_records": _expression_channel_records(
                    memory_hints.get("expression_channel_records", [])
                ),
                "suggested_callbacks": _string_list(field(motif, "suggested_callbacks", [])),
                "forward_motif_guidance": _forward_motif_guidance_list(
                    memory_hints.get("forward_motif_guidance")
                ),
                "foreshadow_due": list(
                    field(memory_hints.get("memory_prompt_context", {}), "foreshadow_due", []) or []
                ),
                "soft_forbidden_themes": _string_list(field(motif, "soft_forbidden_themes", [])),
                # repair_lessons omitted — original design gates it to repair/edit/review only
            }
        )

    # ── Draft: only the fields actually rendered by draft_chapter.j2 ──
    if stage == "draft":
        return _drop_empty(
            {
                "previous_chapter_events": _event_list(
                    memory_hints.get("previous_chapter_events", [])
                ),
                "relevant_history": _event_list(memory_hints.get("relevant_history", [])),
                "active_motifs": _string_list(field(motif, "active_motifs", [])),
                "motif_repetition_risks": _string_list(field(motif, "forbidden_repetition", [])),
                "unified_guidance": unified_guidance,
                "expression_channel_records": _expression_channel_records(
                    memory_hints.get("expression_channel_records", [])
                ),
                "motif_suggestions": [
                    _drop_empty(
                        {
                            "motif_name": _text(field(item, "motif_name", "")),
                            "reason": _text(field(item, "reason", "")),
                            "priority": _text(field(item, "priority", "")),
                            "suggested_context": _text(field(item, "suggested_context", "")),
                            "retired": bool(field(item, "retired", False)),
                        }
                    )
                    for item in list(memory_hints.get("motif_suggestions", []) or [])
                ],
                "summary_context": _text(field(layered, "L1_core_memory", "")),
                "foreshadow_due": list(
                    field(memory_hints.get("memory_prompt_context", {}), "foreshadow_due", []) or []
                ),
                "soft_forbidden_themes": _string_list(field(motif, "soft_forbidden_themes", [])),
                # repair_lessons omitted — original design gates it to repair/edit/review only
            }
        )

    # ── Polish: only consumes expression cooling; plot/context memory is
    # already represented by the reviewed text and source boundary.
    if stage == "polish":
        return _drop_empty(
            {
                "expression_channel_records": _expression_channel_records(
                    memory_hints.get("expression_channel_records", [])
                ),
            }
        )

    # ── Fallback (review / unknown future stages): full capsule ──
    include_lessons = stage.endswith("repair") or stage in {"edit", "review"}
    return _drop_empty(
        {
            "project_identity": _text(field(layered, "L0_identity", "")),
            "core_memory": _text(field(layered, "L1_core_memory", "")),
            "recent_context": _text(field(layered, "L2_on_demand", "")),
            "previous_chapter_events": _event_list(memory_hints.get("previous_chapter_events", [])),
            "relevant_history": _event_list(memory_hints.get("relevant_history", [])),
            "active_motifs": _string_list(field(motif, "active_motifs", [])),
            "motif_repetition_risks": _string_list(field(motif, "forbidden_repetition", [])),
            "unified_guidance": unified_guidance,
            "expression_channel_records": _expression_channel_records(
                memory_hints.get("expression_channel_records", [])
            ),
            "suggested_callbacks": _string_list(field(motif, "suggested_callbacks", [])),
            "forward_motif_guidance": _forward_motif_guidance_list(
                memory_hints.get("forward_motif_guidance")
            ),
            "motif_suggestions": [
                _drop_empty(
                    {
                        "motif_name": _text(field(item, "motif_name", "")),
                        "reason": _text(field(item, "reason", "")),
                        "priority": _text(field(item, "priority", "")),
                        "suggested_context": _text(field(item, "suggested_context", "")),
                        "retired": bool(field(item, "retired", False)),
                    }
                )
                for item in list(memory_hints.get("motif_suggestions", []) or [])
            ],
            "summary_context": _text(field(layered, "L1_core_memory", "")),
            "foreshadow_due": list(
                field(memory_hints.get("memory_prompt_context", {}), "foreshadow_due", []) or []
            ),
            "repair_lessons": _text(memory_hints.get("critique_context", ""))
            if include_lessons
            else "",
        }
    )


def _forward_motif_guidance_list(raw: Any) -> list[str]:
    if not isinstance(raw, dict):
        return []

    specs = (
        ("strengthen_motifs", "可参考"),
        ("dormant_callbacks", "长线参考"),
        ("pov_motifs", "视角相关"),
        ("plot_matched_motifs", "剧情参考"),
    )
    result: list[str] = []
    for key, label in specs:
        items = raw.get(key, [])
        if not isinstance(items, (list, tuple)):
            continue
        for item in items:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    result.append(f"{label}：{text}")
                continue
            name = _text(field(item, "name", "")) or _text(field(item, "motif_name", ""))
            if not name:
                continue
            if not allows_forward_prompt_guidance(field(item, "category", "意象")):
                continue
            detail = (
                _text(field(item, "reason", ""))
                or _text(field(item, "match_reason", ""))
                or _text(field(item, "thematic_meaning", ""))
            )
            chapters_since = field(item, "chapters_since", None)
            gap = ""
            if chapters_since not in (None, ""):
                gap = f"（已间隔{chapters_since}章）"
            result.append(f"{label}：{name}{gap}" + (f"；可参考：{detail}" if detail else ""))

    return result


def _outline_context_text(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.strip()
    if not isinstance(raw, dict):
        return _text(raw)

    parts: list[str] = []
    summary = _text(raw.get("chapter_summary", ""))
    if summary:
        parts.append(summary)

    unresolved = _string_list(raw.get("unresolved_questions", []))
    if unresolved:
        parts.append("待回应悬念：" + "；".join(unresolved))

    relationships = _string_list(raw.get("relationship_changes", []))
    if relationships:
        parts.append("关系动态：" + "；".join(relationships))

    similar_events = raw.get("similar_events", [])
    if isinstance(similar_events, list):
        lines: list[str] = []
        for item in similar_events:
            if not isinstance(item, dict):
                continue
            summary_text = _text(item.get("event_summary", ""))
            if not summary_text:
                continue
            chapter = int(item.get("chapter_number", 0) or 0)
            lines.append(f"第{chapter}章：{summary_text}" if chapter else summary_text)
        if lines:
            parts.append("相似历史：" + "；".join(lines))

    return "\n".join(part for part in parts if part).strip()


def _build_quality_capsule(
    reading_power_hint: dict[str, Any],
    *,
    weak_senses: list[str] | None = None,
    stage: str = "draft",
) -> dict[str, Any]:
    """Build a stage-scoped reader-pull capsule.

    Reading-power hints are subjective creative guidance, not hard continuity
    facts.  Route only the fields a stage can act on so repeated edit/repair
    steps do not carry the full design-intent payload.
    """
    stage_name = _text(stage).lower() or "draft"
    all_fields = {
        "chapter_hook": _text(reading_power_hint.get("chapter_hook", "")),
        "recommended_hook_type": _text(reading_power_hint.get("recommended_hook_type", "")),
        "hook_type_constraint": _text(reading_power_hint.get("hook_type_constraint", "")),
        "tension_target": reading_power_hint.get("tension_target"),
        "outline_expected_hook": _as_mapping(reading_power_hint.get("outline_expected_hook", {})),
        "in_chapter_payoffs": _string_list(reading_power_hint.get("in_chapter_payoffs", [])),
        "force_resolve_suspense": _string_list(
            reading_power_hint.get("force_resolve_suspense", [])
        ),
        "payoff_guidance": _text(reading_power_hint.get("payoff_guidance", "")),
        "strand_recommendation": _text(reading_power_hint.get("strand_recommendation", "")),
        "turning_point_warning": _text(reading_power_hint.get("turning_point_warning", "")),
        "weak_senses": _string_list(weak_senses or []),
    }

    stage_fields = {
        # Bridge only needs the opening handoff and near-term suspense boundary.
        # End hooks, payoffs, and strand rhythm belong to plan/draft so the
        # handoff step does not peek ahead into full chapter design.
        "bridge": {
            "hook_type_constraint",
            "force_resolve_suspense",
        },
        # Planning may place macro hooks/payoffs into scene structure.
        "plan": {
            "recommended_hook_type",
            "hook_type_constraint",
            "tension_target",
            "outline_expected_hook",
            "in_chapter_payoffs",
            "force_resolve_suspense",
            "payoff_guidance",
            "strand_recommendation",
            "turning_point_warning",
        },
        # Draft already receives bridge/plan/contract/state/style. Keep only
        # prose-execution quality knobs so subjective design intent does not
        # compete with the concrete scene plan.
        "draft": {
            "chapter_hook",
            "hook_type_constraint",
            "in_chapter_payoffs",
            "force_resolve_suspense",
            "payoff_guidance",
            "turning_point_warning",
            "weak_senses",
        },
        # Edit runs repeatedly, so keep only immediately actionable knobs.
        "edit": {
            "force_resolve_suspense",
            "payoff_guidance",
            "turning_point_warning",
            "weak_senses",
        },
        # Dedicated repair needs the full diagnostic payload.
        "reading_power_repair": set(all_fields),
    }
    allowed = stage_fields.get(stage_name, stage_fields["draft"])
    return _drop_empty({key: value for key, value in all_fields.items() if key in allowed})


def _build_element_card(
    element_selection: dict[str, Any] | None,
    element_focus: list[str] | None,
    element_progress_hint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    selection = _as_mapping(element_selection or {})
    progress = _as_mapping(element_progress_hint or {})
    required = _element_summary(
        selection.get("required_elements", []) or selection.get("core_elements", [])
    )
    extensions = _element_summary(selection.get("extension_elements", []))
    focus_ids = _string_list(element_focus or [])
    focused = [
        item
        for item in extensions
        if item.get("element_id") and item.get("element_id") in set(focus_ids)
    ]
    return _drop_empty(
        {
            "focus_ids": focus_ids,
            "available": bool(selection),
            "required_elements": required,
            "focused_extension_elements": focused,
            "focus_constraints": _string_list(selection.get("focus_constraints", [])),
            "progress_summary": _text(progress.get("summary", "")),
            "mandated_focus_ids": _string_list(progress.get("mandated_focus_ids", [])),
            "recommended_focus_ids": _string_list(progress.get("recommended_focus_ids", [])),
            "schedule_due": _element_schedule_records(progress.get("schedule_due", [])),
            "schedule_violations": _element_schedule_records(
                progress.get("schedule_violations", [])
            ),
            "recent_missed": _element_progress_records(progress.get("recent_missed", [])),
            "recent_weak": _element_progress_records(progress.get("recent_weak", [])),
        }
    )


def _element_schedule_records(items: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in _record_list(items):
        records.append(
            _drop_empty(
                {
                    "element_id": _text(field(item, "element_id", "")) or "未知要素",
                    "curve_type": _text(field(item, "curve_type", "")),
                    "chapter": field(item, "chapter", None),
                    "last_seen_chapter": field(item, "last_seen_chapter", None),
                    "cadence_chapters": field(item, "cadence_chapters", None),
                    "reason": _text(field(item, "reason", field(item, "message", ""))),
                    "remediation_hint": _text(field(item, "remediation_hint", "")),
                }
            )
        )
    return records


def _element_progress_records(items: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in _record_list(items):
        records.append(
            _drop_empty(
                {
                    "element_id": _text(field(item, "element_id", "")) or "未知要素",
                    "last_chapter": field(item, "last_chapter", field(item, "chapter_number", "?"))
                    or "?",
                    "count": field(item, "count", 0) or 0,
                    "latest_reason": _text(field(item, "latest_reason", "")) or "未记录具体原因",
                    "miss_reason": _text(field(item, "miss_reason", "")),
                    "remediation_hint": _text(field(item, "remediation_hint", "")),
                }
            )
        )
    return records


def _build_arc_liveness_card(report: Any) -> dict[str, Any]:
    source = _as_mapping(report or {})
    dormant_arcs = [
        {
            "name": _text(field(item, "name", "")) or "未命名弧光",
            "last_progress_chapter": field(item, "last_progress_chapter", 0) or 0,
            "planning_hint": _text(field(item, "planning_hint", "")) or "轻触推进，避免整段强插。",
        }
        for item in list(source.get("dormant_arcs", []) or [])
    ]
    return _drop_empty({"dormant_arcs": dormant_arcs})


def _build_time_card(time_context: dict[str, Any] | None) -> dict[str, Any]:
    context = _as_mapping(time_context or {})
    return _drop_empty(
        {
            "prev_time_anchor": _text(context.get("prev_time_anchor", "")),
            "current_time_anchor": _text(context.get("current_time_anchor", "")),
            "time_gap_from_prev": _text(context.get("time_gap_from_prev", "")),
            "current_time_span": _text(context.get("current_time_span", "")),
            "countdown_state": _text(context.get("countdown_state", "")),
            "is_flashback": bool(context.get("is_flashback", False)),
        }
    )


def _build_strand_card(strand_hint: dict[str, Any] | None) -> dict[str, Any]:
    hint = _as_mapping(strand_hint or {})
    return _drop_empty(
        {
            "strand_distribution": _as_mapping(hint.get("strand_distribution", {})),
            "strand_alerts": _strand_alert_records(hint.get("strand_alerts", [])),
        }
    )


def _build_repair_card(
    *,
    packet: Any | None,
    plan: Any | None,
    known_issues_to_avoid: list[dict[str, Any]] | None,
    stage: str,
) -> dict[str, Any]:
    return _drop_empty(
        {
            "stage": stage,
            "preserve": [
                *_string_list(field(packet, "must_carry_forward", [])),
                *_string_list(field(plan, "required_state_transitions", [])),
                _text(field(plan, "closing_contract", "")),
            ],
            "do_not_introduce": [
                *_string_list(
                    _as_mapping(field(packet, "chapter_contract", {})).get("forbidden_changes", [])
                ),
                "新 POV 段",
                "计划外正面出场",
                "计划外关键揭示",
                "大段字数膨胀",
            ],
            "known_issue_summaries": [
                {
                    "category": _text(field(item, "category", "")) or "general",
                    "summary": _text(field(item, "summary", "")),
                }
                for item in list(known_issues_to_avoid or [])
                if _text(field(item, "summary", ""))
            ],
        }
    )


def _motivation_list(scene: Any) -> list[dict[str, str]]:
    raw = field(scene, "character_motivations", [])
    if isinstance(raw, dict):
        return [
            {
                "character": _text(character) or "角色",
                "motivation": _text(motivation),
                "stake": "",
            }
            for character, motivation in raw.items()
            if _text(character) or _text(motivation)
        ]
    return [
        {
            "character": _text(field(item, "character", "")) or "角色",
            "motivation": _text(field(item, "motivation", "")),
            "stake": _text(field(item, "stake", "")),
        }
        for item in list(raw or [])
    ]


def _event_list(items: Any) -> list[dict[str, Any]]:
    return [
        {
            "chapter_number": field(item, "chapter_number", field(item, "chapter", 0)) or 0,
            "event_summary": _text(field(item, "event_summary", field(item, "summary", ""))),
            "relevance_score": field(item, "relevance_score", None),
        }
        for item in list(items or [])
        if _text(field(item, "event_summary", field(item, "summary", "")))
    ]


def _element_summary(items: Any) -> list[dict[str, Any]]:
    return [
        _drop_empty(
            {
                "element_id": _text(field(item, "element_id", "")) or "unknown_element",
                "name": _text(field(item, "name", "")) or "未命名要素",
                "function": _text(field(item, "function", "")),
                "prompt_hint": _text(field(item, "prompt_hint", "")),
                "category": _text(field(item, "category", "")),
                "description": _text(field(item, "description", "")),
                "implementation_guide": _text(field(item, "implementation_guide", "")),
                "intensity_hint": _text(field(item, "intensity_hint", "")),
                "verification_mode": _text(field(item, "verification_mode", "")),
                "verification_anchors": _string_list(field(item, "verification_anchors", [])),
            }
        )
        for item in list(items or [])
    ]


def _expression_channel_records(items: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in _record_list(items):
        text = _text(field(item, "text", field(item, "sample", "")))
        if not text:
            continue
        channel = _text(field(item, "channel", "")) or "expression"
        record: dict[str, Any] = {
            "channel": channel,
            "channel_id": _text(field(item, "channel_id", "")) or channel,
            "text": text,
            "reason": _text(field(item, "reason", "")),
        }
        for key in ("allowed_when", "provenance"):
            value = _text(field(item, key, ""))
            if value:
                record[key] = value
        for key in ("surface_forms", "trigger_contexts", "replacement_axes", "examples"):
            values = _string_list(field(item, key, []))
            if values:
                record[key] = values
        recent_hits = field(item, "recent_semantic_hits", [])
        if isinstance(recent_hits, list) and recent_hits:
            record["recent_semantic_hits"] = [hit for hit in recent_hits if isinstance(hit, dict)]
        cooldown = field(item, "cooldown_chapters", None)
        if cooldown is not None:
            record["cooldown_chapters"] = cooldown
        confidence = field(item, "confidence", None)
        if confidence is not None:
            record["confidence"] = confidence
        for key in ("semantic_hit_count", "last_seen_chapter", "semantic_similarity_max"):
            value = field(item, key, None)
            if value is not None:
                record[key] = value
        records.append(record)
    return records


def _strand_alert_records(items: Any) -> list[dict[str, str]]:
    return [
        {
            "strand": _text(field(item, "strand", "")) or "strand",
            "message": _text(field(item, "message", "")),
            "suggestion": _text(field(item, "suggestion", "")),
        }
        for item in _record_list(items)
        if _text(field(item, "message", "")) or _text(field(item, "suggestion", ""))
    ]


def _pending_item_records(items: Any) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for item in _record_list(items):
        summary = _text(field(item, "summary", ""))
        candidate_id = _text(field(item, "candidate_id", ""))
        pending_id = _text(field(item, "pending_id", field(item, "id", "")))
        if not (summary or candidate_id or pending_id):
            continue
        records.append(
            {
                "summary": summary,
                "candidate_id": candidate_id,
                "pending_id": pending_id,
            }
        )
    return records


def _entity_reference_graph_card(graph: Any) -> dict[str, Any]:
    source = _as_mapping(graph or {})
    return _drop_empty(
        {
            "identity_links": _entity_reference_links(source.get("identity_links", [])),
            "context_links": _entity_reference_links(source.get("context_links", [])),
        }
    )


def _entity_reference_links(items: Any) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    for item in _record_list(items):
        source = _text(field(item, "source", ""))
        target = _text(field(item, "target", ""))
        if not (source or target):
            continue
        links.append(
            _drop_empty(
                {
                    "source": source,
                    "source_type": _text(field(item, "source_type", "")),
                    "link_type": _text(field(item, "link_type", "")) or "related",
                    "target": target,
                    "target_type": _text(field(item, "target_type", "")),
                    "time_layer": _text(field(item, "time_layer", "")),
                    "description": _text(field(item, "description", "")),
                    "confidence": field(item, "confidence", None),
                }
            )
        )
    return links


def _optional_positive_int(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _optional_positive_int_or_none(value: Any) -> int | None:
    number = _optional_positive_int(value)
    return number or None


def _positive_int_list(value: Any) -> list[int]:
    values = value if isinstance(value, (list, tuple, set)) else [value]
    result: list[int] = []
    for raw in values:
        number = _optional_positive_int(raw)
        if number and number not in result:
            result.append(number)
    return result


def _settings_int(
    settings: Any | None,
    attr: str,
    default: int,
    *,
    minimum: int = 0,
) -> int:
    raw = getattr(settings, attr, default) if settings is not None else default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _dedupe_hard_facts(facts: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for fact in facts:
        text = _text(fact)
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return deduped


def _compact_authoritative_state(
    state: dict[str, Any],
    *,
    settings: Any | None,
) -> dict[str, Any]:
    if not state:
        return {}
    pending_limit = _settings_int(settings, "narrative_state_pending_tail_items", 6)
    return _drop_empty(
        {
            "last_chapter": state.get("last_chapter", 0),
            "pending_items": _pending_item_records(state.get("pending_items", []))[-pending_limit:]
            if pending_limit
            else [],
        }
    )


def _stage_milestones_for_contract(window: dict[str, Any], *, stage: str) -> dict[str, Any]:
    if not window:
        return {}
    stage_name = _text(stage).lower()
    payload = {
        "current": _record_list(window.get("current", [])),
        "previous_context": _record_list(window.get("previous_context", [])),
        "withheld_future_count": int(window.get("withheld_future_count", 0) or 0),
        "policy": _text(window.get("policy", "")),
    }
    if stage_name in {"bridge", "plan", "edit", "continuity_repair", "causal_repair"}:
        payload["future_guardrails"] = _record_list(window.get("future_guardrails", []))
    elif stage_name == "draft":
        payload["future_guardrails"] = []
        payload["policy"] = "Draft withheld future guardrail details; use ContractCard only."
    return _drop_empty(payload)


def _record_list(items: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if not isinstance(items, (list, tuple, set)):
        return result
    for item in items:
        if isinstance(item, dict):
            result.append(_drop_empty(dict(item)))
        elif hasattr(item, "model_dump"):
            data = item.model_dump(mode="json")
            if isinstance(data, dict):
                result.append(_drop_empty(data))
    return result


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        data = value.model_dump(mode="json")
        return data if isinstance(data, dict) else {}
    return {}


def _text(value: Any) -> str:
    # Structured carry-forward items (CarryForwardItem / dict) carry their
    # narrative text under ``text``; str()-ing the model directly would yield
    # a pydantic repr. Project to the text so contract cards read correctly.
    if isinstance(value, dict) and ("text" in value or "item" in value or "content" in value):
        return str(value.get("text") or value.get("item") or value.get("content") or "").strip()
    text_attr = getattr(value, "text", None)
    if text_attr is not None and not isinstance(value, str):
        return str(text_attr or "").strip()
    return str(value or "").strip()


def _string_list(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        values = [values] if _text(values) else []
    return [_text(value) for value in values if _text(value)]


def _atomic_string_list(values: Any) -> list[str]:
    """Flatten semicolon-delimited checklist text into independently owned items."""

    items: list[str] = []
    for value in _string_list(values):
        for item in re.split(r"[；;\n]+", value):
            text = _text(item)
            if text and text not in items:
                items.append(text)
    return items


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        if item is None or item == "" or item == [] or item == {}:
            continue
        result[key] = item
    return result
