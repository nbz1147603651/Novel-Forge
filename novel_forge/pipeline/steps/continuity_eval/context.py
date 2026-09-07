"""Context building and input model for continuity evaluation.

Contains ContinuityEvalInput (Pydantic model) and helpers for assembling
LLM context from chapter state, bridge, plan, and local prescreen issues.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

from novel_forge.core.review.audit_profiles import recheck_policy_context
from novel_forge.core.schemas.continuity import (
    ChapterBridge,
    ChapterPlan,
    ChapterStatePacket,
)
from novel_forge.core.utils.audit_issue import audit_issues_prompt_payload
from novel_forge.core.utils.boundary_windows import (
    DEFAULT_OPENING_PARAGRAPHS,
    DEFAULT_PREVIOUS_TAIL_PARAGRAPHS,
    coerce_paragraph_count,
    paragraph_range_label,
)
from novel_forge.core.utils.field_extractor import field
from novel_forge.core.utils.string import carry_forward_text, clean_str


class ContinuityEvalInput(BaseModel):
    """Input for continuity evaluation."""

    model_config = {"arbitrary_types_allowed": True, "extra": "ignore"}

    chapter_number: int
    chapter_text: str
    chapter_state_packet: ChapterStatePacket
    chapter_bridge: ChapterBridge
    chapter_plan: ChapterPlan
    prior_issues: list[dict[str, Any]] = []
    must_resolve_summaries: list[str] = []
    pov_switch: bool = False
    recheck_mode: bool = False
    patch_only_repair: bool = False
    repaired_issue_types: list[str] = []
    motif_context: dict[str, Any] = Field(default_factory=dict)
    bible_anchor_terms: list[str] = Field(default_factory=list)
    project_path: Path | None = None
    strict_review: bool = False
    registry_kinship_terms: frozenset[str] | None = None
    registry_rhetorical_hints: frozenset[str] | None = None
    registry_emotion_keywords: frozenset[str] | None = None
    registry_bible_derived: dict[str, list[str]] | None = None
    registry_genre: str | None = None
    kernel_context: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional field slice from ContextComposer (StoryKernel). "
            "Contains kernel fields like entities, relationships, timeline, "
            "world_rules, knowledge_ledger, object_ledger, chapter_summaries, "
            "promise_ledger, motif_protocols. Merged into LLM context with 'kernel_' prefix."
        ),
    )
    boundary_prev_tail_paragraphs: int = Field(
        default=DEFAULT_PREVIOUS_TAIL_PARAGRAPHS,
        ge=1,
        le=12,
    )
    boundary_opening_paragraphs: int = Field(
        default=DEFAULT_OPENING_PARAGRAPHS,
        ge=1,
        le=8,
    )
    chapter_source_slice: Any | None = Field(
        default=None,
        description="ChapterSourceSlice projection for source artifact boundaries.",
    )


def _clean_list(
    values: Any,
    *,
    limit: int | None = None,
    item_limit: int | None = None,
) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        # Structured carry-forward items project their text via the helper.
        text = clean_str(carry_forward_text(value) or value, limit=item_limit)
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
        if limit is not None and len(cleaned) >= limit:
            break
    return cleaned


def _clean_mapping(
    value: Any,
) -> Any:
    """Normalize an upstream-scoped kernel slice without secondary truncation."""
    if isinstance(value, dict):
        return {
            clean_str(key): _clean_mapping(item)
            for key, item in value.items()
            if clean_str(key)
        }
    if isinstance(value, (list, tuple, set)):
        return [_clean_mapping(item) for item in value]
    return value if value is None or isinstance(value, (bool, int, float)) else clean_str(value)


def _scope_causal_link(link: Any) -> dict[str, Any] | None:
    if not link:
        return None
    return {
        "previous_event": clean_str(field(link, "previous_event"), limit=160),
        "causal_mechanism": clean_str(field(link, "causal_mechanism"), limit=160),
        "unresolved_question": clean_str(field(link, "unresolved_question"), limit=160),
        "open_threads": _clean_list(field(link, "open_threads", [])),
    }


def _scope_bridge(bridge: ChapterBridge) -> dict[str, Any]:
    return {
        "from_chapter": field(bridge, "from_chapter", 0),
        "to_chapter": field(bridge, "to_chapter", 0),
        "opening_time": clean_str(field(bridge, "opening_time")),
        "opening_location": clean_str(field(bridge, "opening_location")),
        "opening_pov": clean_str(field(bridge, "opening_pov")),
        "transition_mode": clean_str(field(bridge, "transition_mode")),
        "emotional_carryover": clean_str(field(bridge, "emotional_carryover")),
        "action_handoff": clean_str(field(bridge, "action_handoff")),
        "causal_link": _scope_causal_link(field(bridge, "causal_link", None)),
        "bridge_summary": clean_str(field(bridge, "bridge_summary")),
        "opening_acceptance_criteria": _clean_list(
            field(bridge, "opening_acceptance_criteria", [])
        ),
    }


def _scope_plan(plan: ChapterPlan) -> dict[str, Any]:
    scene_intents: list[dict[str, Any]] = []
    for scene in list(field(plan, "scene_intents", []) or []):
        scene_intents.append(
            {
                "summary": clean_str(field(scene, "summary")),
                "purpose": clean_str(field(scene, "purpose")),
                "conflict": clean_str(field(scene, "conflict")),
                "required_outcome": clean_str(field(scene, "required_outcome")),
                "exit_target_state": clean_str(field(scene, "exit_target_state")),
                "location": clean_str(field(scene, "location")),
                "time_marker": clean_str(field(scene, "time_marker")),
                "entry_state_refs": _clean_list(field(scene, "entry_state_refs", [])),
                "required_characters": _clean_list(field(scene, "required_characters", [])),
            }
        )
    return {
        "opening_contract": clean_str(field(plan, "opening_contract")),
        "closing_contract": clean_str(field(plan, "closing_contract")),
        "emotional_arc": clean_str(field(plan, "emotional_arc")),
        "required_state_transitions": _clean_list(
            field(plan, "required_state_transitions", []),
        ),
        "key_revelations": _clean_list(
            field(plan, "key_revelations", []),
        ),
        "foreshadowing_plan": _clean_list(
            field(plan, "foreshadowing_plan", []),
        ),
        "scene_intents": scene_intents,
        "relationship_evolution": _clean_list(
            field(plan, "relationship_evolution", []),
        ),
    }


def _scope_character_profiles(packet: ChapterStatePacket) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    for profile in list(field(packet, "character_profiles", []) or []):
        name = clean_str(field(profile, "name"))
        if not name:
            continue
        profiles.append(
            {
                "name": name,
                "role": clean_str(field(profile, "role")),
                "gender": clean_str(field(profile, "gender")),
                "backstory": clean_str(field(profile, "backstory")),
            }
        )
    return profiles


def _scope_exit_state(exit_state: Any) -> dict[str, Any] | None:
    if not exit_state:
        return None
    return {
        "time_marker": clean_str(field(exit_state, "time_marker")),
        "location": clean_str(field(exit_state, "location")),
        "pov": clean_str(field(exit_state, "pov")),
        "emotional_state": clean_str(field(exit_state, "emotional_state")),
        "open_questions": _clean_list(field(exit_state, "open_questions", [])),
    }


def _scope_relationships(packet: ChapterStatePacket) -> list[dict[str, Any]]:
    relationships: list[dict[str, Any]] = []
    for rel in list(field(packet, "active_relationships", []) or []):
        relationships.append(
            {
                "characters": _clean_list(field(rel, "characters", [])),
                "public_status": clean_str(field(rel, "public_status")),
                "last_shift_event": clean_str(field(rel, "last_shift_event")),
            }
        )
    return relationships


def _scope_retrieval_evidence_pack(packet: ChapterStatePacket) -> dict[str, Any]:
    raw = field(packet, "retrieval_evidence_pack", {})
    if not isinstance(raw, dict):
        return {}
    cards: list[dict[str, Any]] = []
    for card in list(raw.get("evidence_cards", []) or []):
        if not isinstance(card, dict):
            continue
        excerpt = clean_str(card.get("excerpt"))
        source_ref = clean_str(card.get("source_ref"))
        if not excerpt or not source_ref:
            continue
        cards.append(
            {
                "source_ref": source_ref,
                "excerpt": excerpt,
                "chapter_number": int(card.get("chapter_number", 0) or 0),
                "authority": clean_str(card.get("authority"), limit=20) or "supporting",
            }
        )
    if not cards:
        return {}
    return {
        "supporting_evidence": cards,
        "selection": {
            "candidate_limit": int(raw.get("candidate_limit", 0) or 0),
            "evidence_token_budget": int(raw.get("evidence_token_budget", 0) or 0),
            "estimated_evidence_tokens": int(raw.get("estimated_evidence_tokens", 0) or 0),
            "retrieved_candidate_count": int(raw.get("retrieved_candidate_count", 0) or 0),
            "omitted_candidate_count_lower_bound": int(
                raw.get("omitted_candidate_count_lower_bound", 0) or 0
            ),
            "has_more_evidence": bool(raw.get("has_more_evidence", False)),
        },
        "usage_policy": "P0 约束优先；召回卡只作核验线索，不自动视为矛盾结论。",
    }


def _scope_state_packet(
    packet: ChapterStatePacket,
    *,
    previous_ending_limit: int = 1600,
) -> dict[str, Any]:
    return {
        "character_profiles": _scope_character_profiles(packet),
        "continuity_context_brief": clean_str(
            field(packet, "continuity_context_brief"),
            limit=600,
        ),
        "previous_chapter_ending": clean_str(
            field(packet, "previous_chapter_ending"),
            limit=previous_ending_limit,
        ),
        "must_carry_forward": _clean_list(
            field(packet, "must_carry_forward", []),
        ),
        "previous_exit_state": _scope_exit_state(field(packet, "previous_exit_state", None)),
        "narrative_state_projection": _clean_mapping(
            field(packet, "narrative_state_projection", {}),
        ),
        "chapter_contract": _clean_mapping(
            field(packet, "chapter_contract", {}),
        ),
        "known_characters": _clean_list(
            field(packet, "known_characters", []),
        ),
        "active_relationships": _scope_relationships(packet),
        "retrieval_evidence": _scope_retrieval_evidence_pack(packet),
    }


def build_llm_context(
    input_data: ContinuityEvalInput,
    *,
    number_paragraphs_fn: Callable[[str], str],
    local_issues: list[dict[str, Any]] | None = None,
    use_local_as_prescreen: bool = True,
    confidence_threshold: float = 0.7,
    strict_review: bool = False,
    review_temp: float = 0.0,
    review_prompt: str = "",
    default_temp: float = 0.0,
) -> tuple[dict[str, Any], float]:
    """Build the LLM context dict and return (context, temperature).

    Args:
        input_data: The continuity eval input.
        number_paragraphs_fn: Callable to number paragraphs.
        local_issues: Pre-built local heuristic issues.
        use_local_as_prescreen: Whether to inject local issues into prompt.
        confidence_threshold: Minimum confidence for prescreen injection.
        strict_review: Whether strict review mode is active.
        review_temp: Temperature for strict review.
        review_prompt: Independent reviewer prompt text.
        default_temp: Default temperature for non-strict mode.

    Returns:
        Tuple of (llm_context_dict, temperature_to_use).
    """
    previous_tail_paragraphs = coerce_paragraph_count(
        getattr(
            input_data,
            "boundary_prev_tail_paragraphs",
            DEFAULT_PREVIOUS_TAIL_PARAGRAPHS,
        ),
        default=DEFAULT_PREVIOUS_TAIL_PARAGRAPHS,
    )
    opening_paragraphs = coerce_paragraph_count(
        getattr(input_data, "boundary_opening_paragraphs", DEFAULT_OPENING_PARAGRAPHS),
        default=DEFAULT_OPENING_PARAGRAPHS,
        maximum=8,
    )
    opening_label = paragraph_range_label(1, opening_paragraphs)
    has_previous_chapter = bool(
        input_data.chapter_number > 1
        and (
            field(input_data.chapter_state_packet, "previous_chapter_ending")
            or field(input_data.chapter_state_packet, "previous_exit_state")
            or field(input_data.chapter_state_packet, "must_carry_forward", [])
        )
    )
    llm_context: dict[str, Any] = {
        "chapter_number": input_data.chapter_number,
        "has_previous_chapter": has_previous_chapter,
        "is_first_chapter": input_data.chapter_number == 1,
        "chapter_text": input_data.chapter_text,
        "numbered_chapter_text": number_paragraphs_fn(input_data.chapter_text),
        "chapter_state_packet": _scope_state_packet(
            input_data.chapter_state_packet,
            previous_ending_limit=max(800, min(2400, previous_tail_paragraphs * 500)),
        ),
        "chapter_bridge": _scope_bridge(input_data.chapter_bridge),
        "chapter_plan": _scope_plan(input_data.chapter_plan),
        "boundary_window_policy": {
            "previous_tail_paragraphs": previous_tail_paragraphs,
            "opening_paragraphs": opening_paragraphs,
            "opening_window_label": opening_label,
            "previous_tail_label": f"上一章末尾 {previous_tail_paragraphs} 段",
            "skip_previous_chapter_handoff": not has_previous_chapter,
        },
        "prior_issues": audit_issues_prompt_payload(
            input_data.prior_issues,
            dimension="continuity",
            chapter_number=input_data.chapter_number,
            source_module="check_continuity",
        ),
        "must_resolve_summaries": input_data.must_resolve_summaries,
        "recheck_mode": input_data.recheck_mode,
        "recheck_policy": recheck_policy_context("continuity"),
        "patch_only_repair": input_data.patch_only_repair,
        "repaired_issue_types": input_data.repaired_issue_types,
    }
    if input_data.chapter_source_slice is not None:
        from novel_forge.pipeline.long.services.context.source_artifacts import (
            project_stage_source_cards,
        )

        llm_context["stage_cards"] = {
            "source": project_stage_source_cards(
                input_data.chapter_source_slice,
                stage="review",
            )
        }

    if use_local_as_prescreen and local_issues:
        filtered_local = [
            {**issue, "confidence": issue.get("confidence", 0.5)}
            for issue in local_issues
            if issue.get("confidence", 0.5) >= confidence_threshold
        ]
        if filtered_local:
            llm_context["local_prescreen_issues"] = filtered_local
            llm_context["local_check_note"] = (
                "以上是本地预筛选发现的问题，请结合正文内容进行最终判定。"
                "本地预筛选只覆盖跨章边界、状态承接、桥接合同和 must_carry_forward；"
                "carry_forward_missing 类问题需要从叙事逻辑角度判断是否真正遗漏。"
            )

    temperature = default_temp
    if strict_review:
        llm_context["strict_review"] = True
        if review_prompt:
            llm_context["independent_reviewer_note"] = review_prompt
        temperature = review_temp

    # Merge kernel field slices (from ContextComposer) with 'kernel_' prefix
    _merge_kernel_fields(llm_context, input_data.kernel_context)

    return llm_context, temperature


def _merge_kernel_fields(
    llm_context: dict[str, Any],
    kernel_context: dict[str, Any] | None,
) -> None:
    """Merge StoryKernel field slices into the LLM context with 'kernel_' prefix.

    Kernel fields are prefixed to avoid collision with existing context keys.
    Fields with None values are skipped.  The kernel_context dict is typically
    produced by ContextComposer.compose_for_step() or compose_continuity_eval_input().

    Parameters
    ----------
    llm_context:
        The LLM context dict to merge into (modified in place).
    kernel_context:
        Optional field slice from ContextComposer.  Keys are StoryKernel field
        names (e.g. 'entities', 'relationships', 'timeline', 'world_rules').
    """
    if not kernel_context:
        return

    for key, value in kernel_context.items():
        if value is None:
            continue
        prefixed_key = f"kernel_{key}"
        if prefixed_key not in llm_context:
            llm_context[prefixed_key] = value
