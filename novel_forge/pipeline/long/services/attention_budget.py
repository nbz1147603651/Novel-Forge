"""Deterministic stage context manifests and attention budgets."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AttentionBudget:
    """Advisory per-stage pressure thresholds; never mutates source evidence."""

    advisory_list_limits: dict[tuple[str, ...], int] = field(default_factory=dict)
    advisory_text_limits: dict[tuple[str, ...], int] = field(default_factory=dict)
    advisory_list_text_total_limits: dict[tuple[str, ...], int] = field(default_factory=dict)


@dataclass(frozen=True)
class StageContextManifest:
    """Allowed/denied stage cards and deterministic budget policy."""

    stage: str
    allowed_cards: frozenset[str]
    denied_cards: frozenset[str] = frozenset()
    budget: AttentionBudget = field(default_factory=AttentionBudget)
    authority_order: tuple[str, ...] = (
        "source.chapter_contract",
        "state",
        "retrieval_evidence",
        "memory",
        "style",
        "editorial",
    )


_COMMON = frozenset(
    {"stage", "source", "chapter", "stage_visibility", "context_budget", "user_intent"}
)


STAGE_CONTEXT_MANIFESTS: dict[str, StageContextManifest] = {
    "bridge": StageContextManifest(
        stage="bridge",
        allowed_cards=_COMMON
        | {
            "contract",
            "bridge",
            "opening_evidence",
            "retrieval_evidence",
            "research_evidence_pack",
            "research_uncertainty",
            "memory",
            "quality",
            "style",
        },
        budget=AttentionBudget(
            advisory_list_limits={
                ("contract", "hard_facts"): 12,
                ("contract", "cognitive_constraints"): 8,
                ("source", "world_rule_card", "always_on"): 4,
                ("source", "world_rule_card", "relevant_rules"): 2,
                ("memory", "motif_repetition_risks"): 6,
                ("retrieval_evidence", "supporting_evidence"): 8,
                ("style", "modules"): 2,
            },
            advisory_text_limits={
                ("memory", "unified_guidance"): 360,
                ("style", "summary"): 240,
            },
        ),
    ),
    "plan": StageContextManifest(
        stage="plan",
        allowed_cards=_COMMON
        | {
            "contract",
            "state",
            "narration",
            "subplot_weave",
            "bridge",
            "characters",
            "knowledge",
            "editorial",
            "style",
            "memory",
            "quality",
            "arc_liveness",
            "time",
            "strand",
            "element",
            "retrieval_evidence",
            "research_evidence_pack",
            "research_uncertainty",
        },
        budget=AttentionBudget(
            advisory_list_limits={
                ("contract", "hard_facts"): 50,
                ("contract", "cognitive_constraints"): 16,
                ("source", "world_rule_card", "always_on"): 4,
                ("source", "world_rule_card", "relevant_rules"): 8,
                ("source", "relevant_entities"): 10,
                ("characters",): 10,
                ("memory", "previous_chapter_events"): 5,
                ("memory", "relevant_history"): 4,
                ("memory", "foreshadow_due"): 4,
                ("memory", "motif_suggestions"): 3,
                ("memory", "active_motifs"): 3,
                ("style", "modules"): 4,
                ("element", "required_elements"): 6,
                ("element", "focused_extension_elements"): 3,
                ("retrieval_evidence", "supporting_evidence"): 8,
            },
            advisory_text_limits={
                ("memory", "summary_context"): 1200,
                ("memory", "unified_guidance"): 700,
                ("style", "summary"): 420,
            },
            advisory_list_text_total_limits={
                ("element", "focused_extension_elements", "implementation_guide"): 700,
            },
            # Source cards may deliberately retain non-rendered provenance
            # and archive references.  Do not use their serialized size as a
            # prompt cutoff: the rendered prompt is the only meaningful
            # context budget, and modern routes have ample context windows.
            # Field-level caps above remain the lightweight guidance layer.
        ),
    ),
    "draft": StageContextManifest(
        stage="draft",
        allowed_cards=_COMMON
        | {
            "contract",
            "state",
            "narration",
            "subplot_weave",
            "plan",
            "characters",
            "knowledge",
            "editorial",
            "style",
            "memory",
            "quality",
            "repair",
            "element",
            "research_evidence_pack",
            "research_uncertainty",
        },
        budget=AttentionBudget(
            advisory_list_limits={
                ("contract", "hard_facts"): 40,
                ("contract", "cognitive_constraints"): 12,
                ("source", "world_rule_card", "always_on"): 4,
                ("source", "world_rule_card", "relevant_rules"): 0,
                ("source", "relevant_entities"): 7,
                ("characters",): 7,
                ("memory", "previous_chapter_events"): 4,
                ("memory", "relevant_history"): 3,
                ("memory", "foreshadow_due"): 3,
                ("memory", "motif_suggestions"): 2,
                ("memory", "active_motifs"): 2,
                ("memory", "motif_repetition_risks"): 4,
                ("memory", "expression_channel_records"): 4,
                ("style", "modules"): 6,
                ("style", "banned_phrases"): 12,
                ("style", "cool_point_patterns"): 4,
                ("style", "hook_preferred_types"): 4,
                ("style", "micro_payoff_types"): 4,
                ("quality", "in_chapter_payoffs"): 3,
                ("quality", "force_resolve_suspense"): 3,
                ("element", "required_elements"): 4,
                ("element", "focused_extension_elements"): 3,
            },
            advisory_text_limits={
                ("memory", "summary_context"): 900,
                ("memory", "unified_guidance"): 500,
                ("style", "summary"): 360,
            },
            advisory_list_text_total_limits={
                ("element", "focused_extension_elements", "implementation_guide"): 600,
            },
        ),
    ),
    "wave": StageContextManifest(
        stage="wave",
        allowed_cards=_COMMON
        | {
            "contract",
            "state",
            "narration",
            "plan",
            "characters",
            "editorial",
            "style",
            "quality",
            "repair",
            "element",
        },
        denied_cards=frozenset({"memory", "knowledge", "subplot_weave"}),
        budget=AttentionBudget(
            advisory_list_limits={
                ("contract", "hard_facts"): 0,
                ("contract", "cognitive_constraints"): 8,
                ("source", "world_rule_card", "always_on"): 4,
                ("source", "world_rule_card", "relevant_rules"): 4,
                ("source", "relevant_entities"): 7,
                ("characters",): 7,
                ("style", "modules"): 3,
                ("style", "banned_phrases"): 8,
                ("element", "required_elements"): 2,
                ("element", "focused_extension_elements"): 2,
            },
            advisory_text_limits={
                ("style", "summary"): 280,
            },
            advisory_list_text_total_limits={
                ("element", "focused_extension_elements", "implementation_guide"): 400,
            },
        ),
    ),
    "polish": StageContextManifest(
        stage="polish",
        allowed_cards=_COMMON | {"editorial", "style", "memory", "quality"},
        denied_cards=frozenset(
            {
                "contract",
                "state",
                "narration",
                "subplot_weave",
                "bridge",
                "plan",
                "characters",
                "knowledge",
                "repair",
                "element",
            }
        ),
        budget=AttentionBudget(
            advisory_list_limits={
                ("source", "relevant_entities"): 4,
                ("source", "world_rule_card", "always_on"): 4,
                ("source", "world_rule_card", "relevant_rules"): 0,
                ("editorial", "character_voices"): 4,
                ("editorial", "symbol_policies"): 4,
                ("editorial", "scene_resistance_rules"): 4,
                ("editorial", "revelation_ladder"): 6,
                ("editorial", "editorial_element_directives"): 4,
                ("editorial", "time_bridge_policies"): 4,
                ("style", "modules"): 3,
                ("style", "banned_phrases"): 8,
                ("memory", "expression_channel_records"): 4,
                ("quality", "in_chapter_payoffs"): 3,
            },
            advisory_text_limits={
                ("style", "summary"): 260,
            },
        ),
    ),
}


_INIT_AUTHORITY_ORDER = (
    "user_explicit_input",
    "user_locked_elements_or_human_edits",
    "accepted_project_facts",
    "verified_external_facts",
    "ai_creative_direction",
    "style_and_inspiration",
)

for _stage, _allowed in {
    "init_creative_candidates": {"user_intent", "research_evidence", "uncertainty"},
    "init_story_bible": {"user_intent", "research_evidence", "uncertainty"},
    "init_character": {"user_intent", "accepted_facts", "research_evidence", "uncertainty"},
    "init_style": {"user_intent", "accepted_facts", "research_evidence", "uncertainty"},
    "init_blueprint": {
        "user_intent",
        "accepted_facts",
        "research_evidence",
        "creative_direction",
        "uncertainty",
    },
    "init_outline": {
        "user_intent",
        "accepted_facts",
        "research_evidence",
        "creative_direction",
        "uncertainty",
    },
    "init_chapter_contracts": {
        "user_intent",
        "accepted_facts",
        "research_evidence",
        "uncertainty",
    },
    "init_story_kernel": {"user_intent", "accepted_facts"},
}.items():
    STAGE_CONTEXT_MANIFESTS.setdefault(
        _stage,
        StageContextManifest(
            stage=_stage,
            allowed_cards=frozenset(_allowed | {"stage", "context_budget"}),
            authority_order=_INIT_AUTHORITY_ORDER,
        ),
    )


_REPAIR_ALLOWED = _COMMON | {
    "contract",
    "state",
    "bridge",
    "plan",
    "knowledge",
    "editorial",
    "repair",
    "quality",
    "characters",
    "retrieval_evidence",
    "research_evidence_pack",
    "research_uncertainty",
}

for _stage in ("repair", "continuity_repair", "causal_repair", "reading_power_repair", "edit"):
    STAGE_CONTEXT_MANIFESTS.setdefault(
        _stage,
        StageContextManifest(
            stage=_stage,
            allowed_cards=_REPAIR_ALLOWED | ({"style", "memory"} if _stage == "edit" else set()),
            denied_cards=frozenset({"element", "subplot_weave"}),
            budget=AttentionBudget(
                advisory_list_limits={
                    ("contract", "hard_facts"): 24,
                    ("contract", "cognitive_constraints"): 8,
                    ("source", "world_rule_card", "always_on"): 4,
                    ("source", "world_rule_card", "relevant_rules"): 8
                    if _stage in {"edit", "repair", "continuity_repair", "causal_repair"}
                    else 4,
                    ("characters",): 6,
                    ("source", "relevant_entities"): 6,
                    ("knowledge", "character_cards"): 4,
                    ("knowledge", "chapter_ops"): 6,
                    ("repair", "known_issue_summaries"): 6,
                    ("repair", "preserve"): 8,
                    ("repair", "do_not_introduce"): 8,
                    ("retrieval_evidence", "supporting_evidence"): 8,
                },
                advisory_text_limits={
                    ("memory", "core_memory"): 500,
                    ("memory", "project_identity"): 240,
                },
            ),
        ),
    )


def apply_stage_context_manifest(cards: dict[str, Any], *, stage: str) -> dict[str, Any]:
    """Apply a stage manifest and return a budgeted copy of ``cards``."""

    stage_name = str(stage or cards.get("stage") or "").strip().lower()
    manifest = STAGE_CONTEXT_MANIFESTS.get(stage_name)
    if manifest is None:
        return cards

    result = {
        key: value
        for key, value in cards.items()
        if key in manifest.allowed_cards and key not in manifest.denied_cards
    }
    hidden: dict[str, int] = {}
    for key in cards:
        if key not in result and key not in manifest.denied_cards:
            hidden[f"card.{key}"] = _size_hint(cards[key])
    for key in manifest.denied_cards:
        if key in cards:
            hidden[f"denied_card.{key}"] = _size_hint(cards[key])

    advisory_overages: dict[str, int] = {}
    for path, limit in manifest.budget.advisory_list_limits.items():
        _observe_list_path(result, path, limit, advisory_overages)
    for path, limit in manifest.budget.advisory_text_limits.items():
        _observe_text_path(result, path, limit, advisory_overages)
    for path, limit in manifest.budget.advisory_list_text_total_limits.items():
        _observe_list_text_total_path(result, path, limit, advisory_overages)

    result["context_budget"] = {
        "schema": "stage_context_manifest_v1",
        "stage": manifest.stage,
        "authority_order": list(manifest.authority_order),
        "estimated_total_chars": _payload_char_size(result),
        "hidden_counts": hidden,
        "advisory_overages": advisory_overages,
        "overflow_policy": {
            "required_context": "preserve_complete",
            "hard_truncation_allowed": False,
            "required_overflow_action": ("route_larger_context_or_partition_complete_coverage"),
            "evidence_selection_boundary": "upstream_retrieval",
        },
    }
    return result


def _payload_char_size(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return len(str(value))


def _observe_list_path(
    payload: dict[str, Any],
    path: tuple[str, ...],
    limit: int,
    overages: dict[str, int],
) -> None:
    parent, key = _parent_for_path(payload, path)
    if parent is None:
        return
    value = parent.get(key) if key else parent
    if not isinstance(value, list):
        return
    overage = max(0, len(value) - max(0, limit))
    if overage:
        overages[".".join(path)] = overage


def _observe_text_path(
    payload: dict[str, Any],
    path: tuple[str, ...],
    limit: int,
    overages: dict[str, int],
) -> None:
    parent, key = _parent_for_path(payload, path)
    if parent is None or not key:
        return
    value = parent.get(key)
    if not isinstance(value, str) or len(value) <= limit:
        return
    overages[".".join(path)] = len(value) - max(0, limit)


def _observe_list_text_total_path(
    payload: dict[str, Any],
    path: tuple[str, ...],
    limit: int,
    overages: dict[str, int],
) -> None:
    if len(path) < 2:
        return
    parent, key = _parent_for_path(payload, path[:-1])
    if parent is None or not key:
        return
    value = parent.get(key)
    if not isinstance(value, list):
        return
    field_key = path[-1]
    total_chars = 0
    for item in value:
        if not isinstance(item, dict):
            continue
        text = item.get(field_key)
        if not isinstance(text, str) or not text:
            continue
        total_chars += len(text)
    overage = max(0, total_chars - max(0, int(limit)))
    if overage:
        overages[".".join(path)] = overage


def _parent_for_path(
    payload: dict[str, Any], path: tuple[str, ...]
) -> tuple[dict[str, Any] | None, str]:
    if not path:
        return None, ""
    current: Any = payload
    for segment in path[:-1]:
        if not isinstance(current, dict):
            return None, ""
        current = current.get(segment)
    if not isinstance(current, dict):
        return None, ""
    return current, path[-1]


def _size_hint(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return len(value)
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return len(str(value))
