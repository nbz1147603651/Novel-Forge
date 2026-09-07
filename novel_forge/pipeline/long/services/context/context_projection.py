"""Stage-scoped prompt projections for large project artifacts."""

from __future__ import annotations

from typing import Any


def _dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dumped if isinstance(dumped, dict) else {}
    return dict(value) if isinstance(value, dict) else {}


def build_story_bible_card(
    story_bible: Any,
    *,
    stage: str,
    chapter_range: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Return the smallest StoryBible card needed by a stage."""
    payload = _dump(story_bible)
    common = {
        "title": payload.get("title", ""),
        "premise": payload.get("premise", ""),
        "tone": payload.get("tone", ""),
        "themes": payload.get("themes", []),
    }
    stage_key = str(stage or "").lower()
    if stage_key in {"outline", "planning", "init"}:
        keys = ("era", "geography", "culture", "magic_or_tech", "rules")
    elif stage_key in {"draft", "edit", "repair", "audit"}:
        keys = (
            "era",
            "geography",
            "rules",
            "time_convention",
            "address_rules",
            "self_reference_rules",
            "dialogue_register_rules",
            "anachronism_blacklist",
        )
    else:
        keys = ("era", "geography", "rules")
    card = {**common, **{key: payload.get(key) for key in keys if key in payload}}
    if chapter_range:
        card["chapter_range"] = {"start": chapter_range[0], "end": chapter_range[1]}
    return card


def build_character_card(
    character_bible: Any,
    *,
    stage: str,
    involved_characters: list[str] | None = None,
) -> dict[str, Any]:
    """Project CharacterBible to involved characters and stage-relevant fields."""
    payload = _dump(character_bible)
    characters = payload.get("characters", [])
    wanted = {str(name) for name in (involved_characters or []) if str(name).strip()}
    stage_key = str(stage or "").lower()
    if stage_key in {"draft", "edit"}:
        keys = (
            "name",
            "role",
            "gender",
            "status",
            "time_layer",
            "appearance",
            "personality",
            "arc",
            "relationships",
            "notes",
        )
    else:
        keys = ("name", "role", "gender", "status", "time_layer", "arc", "relationships")
    projected = []
    for item in characters if isinstance(characters, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "") or "").strip()
        if wanted and name not in wanted:
            continue
        projected.append({key: item.get(key) for key in keys if key in item})
    return {"characters": projected, "stage": stage}


def build_editorial_card(
    editorial_contract: Any,
    *,
    stage: str,
    dimension: str = "",
    involved_characters: list[str] | None = None,
    chapter_range: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Project EditorialContract by stage and audit dimension."""
    payload = _dump(editorial_contract)
    dimension_key = str(dimension or stage or "").lower()
    keys_by_dimension: dict[str, tuple[str, ...]] = {
        "structure": (
            "climax_markers",
            "denouement_budget",
            "revelation_ladder",
            "time_bridge_policies",
            "title_policy",
        ),
        "voice": ("character_voices", "scene_resistance_rules"),
        "language": (
            "expression_channel_budget",
            "expression_channel_profiles",
            "body_signal_budget_per_high_emotion_scene",
            "forbidden_confirmation_phrases",
            "revision_priorities",
        ),
        "theme_symbol": ("theme_policies", "symbol_policies"),
        "element": ("editorial_element_directives",),
        "draft": (
            "character_voices",
            "scene_resistance_rules",
            "theme_policies",
            "expression_channel_profiles",
        ),
        "edit": (
            "scene_resistance_rules",
            "expression_channel_budget",
            "expression_channel_profiles",
            "forbidden_confirmation_phrases",
            "revision_priorities",
        ),
    }
    keys = keys_by_dimension.get(
        dimension_key,
        ("character_voices", "scene_resistance_rules", "revision_priorities"),
    )
    card = {key: payload.get(key) for key in keys if key in payload}
    if involved_characters and isinstance(card.get("character_voices"), list):
        wanted = {str(name) for name in involved_characters}
        card["character_voices"] = [
            item
            for item in card["character_voices"]
            if isinstance(item, dict) and str(item.get("character", "")) in wanted
        ]
    if chapter_range:
        card["chapter_range"] = {"start": chapter_range[0], "end": chapter_range[1]}
    card["stage"] = stage
    if dimension:
        card["dimension"] = dimension
    return card


def build_canon_context(
    canon_state: Any,
    *,
    stage: str,
    chapter_range: tuple[int, int] | None = None,
    issue_types: list[str] | None = None,
) -> dict[str, Any]:
    """Return a compact Canon context card for prompts."""
    payload = _dump(canon_state)
    card = {
        "stage": stage,
        "characters": payload.get("characters", {}),
        "relationships": payload.get("relationships", []),
        "active_plot_threads": payload.get("active_plot_threads", payload.get("plot_threads", [])),
        "world_facts": payload.get("world_facts", {}),
    }
    if chapter_range:
        card["chapter_range"] = {"start": chapter_range[0], "end": chapter_range[1]}
    if issue_types:
        card["issue_types"] = issue_types
    return card
