"""Shared utilities for short-mode pipeline stages."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Literal

# Re-exported shared text helpers (canonical home: novel_forge.common.utils).
from novel_forge.common.utils import (
    clean_conversation_history as clean_conversation_history,
)
from novel_forge.common.utils import excerpt_text as excerpt_text
from novel_forge.common.utils import unique_texts as unique_texts
from novel_forge.persistence.models import ProjectLayout

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
_SHORT_RUN_META = "short_run_meta.json"


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def canonicalize_checkpoint_value(value: Any) -> Any:
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, dict):
        return {str(key): canonicalize_checkpoint_value(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [canonicalize_checkpoint_value(item) for item in value]
    return value


def checkpoint_fingerprint(payload: dict[str, Any]) -> str:
    raw = json.dumps(
        canonicalize_checkpoint_value(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_checkpoint_payload(
    spec_input: dict[str, Any],
    *,
    writing_mode: Literal["auto", "whole_chapter", "scene_level"],
    segmented_mode: Literal["auto", "on", "off"] | None,
    segment_target_words: int | None,
    segment_max_count: int | None,
    blueprint_element_preferences: dict[str, Any] | None,
    research_enabled: bool = False,
    research_provider: str = "auto",
    research_query_hint: str = "",
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "spec_input": {key: spec_input.get(key) for key in _SHORT_SPEC_KEYS if key in spec_input},
        "generation_options": {
            "writing_mode": writing_mode,
            "segmented_mode": segmented_mode,
            "segment_target_words": segment_target_words,
            "segment_max_count": segment_max_count,
            "blueprint_element_preferences": blueprint_element_preferences or {},
            "research_enabled": bool(research_enabled),
            "research_provider": str(research_provider or "auto").strip() or "auto",
            "research_query_hint": clean_text(research_query_hint),
        },
    }


def short_research_prompt_context(
    runner: Any,
    *,
    include_inspiration: bool,
) -> dict[str, Any]:
    """Return the stage-bounded projection of the run-level short evidence pack."""

    raw_pack = getattr(runner, "_research_evidence_pack", None)
    if not isinstance(raw_pack, dict) or not raw_pack.get("pack_id"):
        return {}
    projected = dict(raw_pack)
    cards: list[dict[str, Any]] = []
    inspiration_count = 0
    for raw_card in list(raw_pack.get("evidence_cards") or []):
        if not isinstance(raw_card, dict):
            continue
        kind = str(raw_card.get("kind") or "")
        if kind == "external_fact":
            cards.append(dict(raw_card))
        elif include_inspiration and kind == "external_inspiration" and inspiration_count < 2:
            cards.append(dict(raw_card))
            inspiration_count += 1
    projected["evidence_cards"] = cards
    uncertainty = [
        str(item).strip()
        for item in list(getattr(runner, "_research_uncertainty", ()) or ())
        if str(item).strip()
    ][:6]
    if not cards and not uncertainty:
        return {}
    return {
        "research_evidence_pack": projected,
        "research_uncertainty": uncertainty,
    }


def _prompt_payload(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        payload = value.model_dump(mode="json")
    else:
        payload = value
    return dict(payload) if isinstance(payload, dict) else {}


def project_short_blueprint_prompt_card(
    blueprint: Any,
    *,
    stage: Literal["draft", "edit", "evaluate"],
) -> dict[str, Any]:
    """Return only blueprint fields consumed by the current short stage."""

    payload = _prompt_payload(blueprint)
    if not payload:
        return {}
    anchor = _prompt_payload(payload.get("anchor_elements"))
    core_characters = [
        {
            "name": clean_text(item.get("name")),
            "role": clean_text(item.get("role")),
        }
        for item in list(anchor.get("core_characters") or [])[:8]
        if isinstance(item, dict) and clean_text(item.get("name"))
    ]
    card: dict[str, Any] = {
        "synopsis": clean_text(payload.get("synopsis")),
        "emotional_arc": clean_text(payload.get("emotional_arc")),
        "ending_strategy": clean_text(payload.get("ending_strategy")),
        "anchor_elements": {
            "time_frame": clean_text(anchor.get("time_frame")),
            "primary_locations": [
                clean_text(item)
                for item in list(anchor.get("primary_locations") or [])[:4]
                if clean_text(item)
            ],
            "core_characters": core_characters,
            "central_event": clean_text(anchor.get("central_event")),
        },
    }
    if stage == "draft":
        card["turning_points"] = [
            {
                "description": clean_text(item.get("description")),
                "position_percent": item.get("position_percent", 0),
            }
            for item in list(payload.get("turning_points") or [])[:5]
            if isinstance(item, dict)
        ]
        selection = _prompt_payload(payload.get("element_selection"))
        extension_elements = [
            {
                "name": clean_text(item.get("name")),
                "prompt_hint": clean_text(item.get("prompt_hint")),
            }
            for item in list(selection.get("extension_elements") or [])[:6]
            if isinstance(item, dict) and clean_text(item.get("name"))
        ]
        if extension_elements:
            card["element_selection"] = {"extension_elements": extension_elements}
    return card


def project_short_execution_prompt_card(
    execution_plan: Any,
    *,
    stage: Literal["draft", "edit", "evaluate"],
) -> dict[str, Any]:
    """Keep execution contracts while dropping stage-irrelevant plan payload."""

    payload = _prompt_payload(execution_plan)
    if not payload:
        return {}
    card: dict[str, Any] = {
        key: payload.get(key)
        for key in ("opening_contract", "ending_contract", "completion_contract")
        if payload.get(key) not in (None, "", [], {})
    }
    if stage == "draft":
        if payload.get("anchor_guardrails"):
            card["anchor_guardrails"] = payload["anchor_guardrails"]
        card["beat_execution_plan"] = [
            {
                key: item.get(key)
                for key in (
                    "sequence",
                    "word_budget",
                    "phase_name",
                    "phase_goal",
                    "time_anchor",
                    "location_anchor",
                    "character_focus",
                    "turning_point_hint",
                )
                if item.get(key) not in (None, "", [], {})
            }
            for item in list(payload.get("beat_execution_plan") or [])[:16]
            if isinstance(item, dict)
        ]
    return card


def short_meta_path(layout: ProjectLayout) -> Path:
    return layout.states_dir / _SHORT_RUN_META


def remove_path(path: Path) -> bool:
    try:
        if path.is_dir():
            shutil.rmtree(path)
            return True
        if path.exists():
            path.unlink()
            return True
    except FileNotFoundError:
        return False
    return False
