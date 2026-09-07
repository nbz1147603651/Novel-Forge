"""Tests for blueprint element selector normalization."""

from __future__ import annotations

from novel_forge.core.constants import TaskType
from novel_forge.core.schemas.spec import StorySpec
from novel_forge.pipeline.steps.blueprint_element_select_step import (
    _normalize_selection,
    get_blueprint_element_cards,
    get_blueprint_genre_presets,
    get_related_extension_ids_for_genre,
    recommend_preset_for_genre,
)
from novel_forge.prompts.builder import PromptBuilder


def test_blueprint_element_select_prompt_renders_genre_preset() -> None:
    spec = StorySpec(
        theme="侦探在封闭校园查案",
        genre="悬疑",
        tone="紧张",
    )
    request = PromptBuilder().build(
        TaskType.BLUEPRINT_ELEMENT_SELECT,
        {
            "spec": spec,
            "mode": "长篇",
            "library_version": "test",
            "required_elements": [
                item.model_dump(mode="json")
                for item in get_blueprint_element_cards(tier="required")
            ],
            "extension_elements": [
                item.model_dump(mode="json")
                for item in get_blueprint_element_cards(tier="extension")
            ],
            "genre_presets": get_blueprint_genre_presets(),
            "selector_preferences": {
                "preset_id": "",
                "manual_override": False,
                "items": [],
            },
        },
    )

    content = request.messages[1]["content"]
    assert "题材预置" in content
    assert "genre_inference" in content


def test_selector_fallback_uses_genre_heuristics_for_mixed_genre() -> None:
    spec = StorySpec(
        theme="她回乡经营农庄并与旧识重逢",
        genre="古代言情种田",
        tone="温暖治愈",
    )
    selection = _normalize_selection({}, spec=spec, mode="long")

    required_ids = {item.element_id for item in selection.required_elements}
    extension_ids = {item.element_id for item in selection.extension_elements}

    assert len(required_ids) == 6
    assert "core_time_anchor" in required_ids
    assert "core_phase_progression" in required_ids
    assert "romance_emotional_barriers" in extension_ids
    assert "farming_resource_loop" in extension_ids


def test_selector_relation_rules_add_required_dependency() -> None:
    spec = StorySpec(
        theme="旧案卷宗逐层揭开",
        genre="悬疑推理",
        tone="紧张",
    )
    selection = _normalize_selection(
        {"extension_ids": ["mystery_reveal_order"]},
        spec=spec,
        mode="long",
    )
    extension_ids = {item.element_id for item in selection.extension_elements}

    assert "mystery_reveal_order" in extension_ids
    assert "mystery_clue_ledger" in extension_ids


def test_selector_relation_rules_remove_unlocked_exclusions() -> None:
    spec = StorySpec(
        theme="一场荒诞的怪谈夜谈",
        genre="恐怖喜剧",
        tone="紧张",
    )
    selection = _normalize_selection(
        {"extension_ids": ["horror_dread_rhythm", "comedy_setup_payoff"]},
        spec=spec,
        mode="long",
    )
    extension_ids = {item.element_id for item in selection.extension_elements}

    assert not {"horror_dread_rhythm", "comedy_setup_payoff"}.issubset(extension_ids)


def test_element_cards_expose_execution_metadata() -> None:
    cards = {card.element_id: card for card in get_blueprint_element_cards(tier="extension")}
    clue = cards["mystery_clue_ledger"]

    assert clue.implementation_guide
    assert "新出现的可记录物件" in clue.verification_anchors
    assert clue.verification_mode == "structural"


def test_selector_keeps_valid_ids_and_reason_from_payload() -> None:
    spec = StorySpec(
        theme="侦探在封闭校园查案",
        genre="悬疑推理",
        tone="紧张",
    )
    payload = {
        "required_ids": ["core_time_anchor", "fake_required"],
        "extension_ids": ["mystery_clue_ledger", "unknown_id"],
        "extension_selection": [
            {
                "element_id": "mystery_clue_ledger",
                "reason": "需要追踪线索投放与回收章节，防止泄底。",
            }
        ],
        "selector_summary": "推理题材需要线索管理。",
        "genre_inference": ["校园推理"],
    }

    selection = _normalize_selection(payload, spec=spec, mode="short")

    assert selection.selector_summary == "推理题材需要线索管理。"
    assert selection.genre_inference == ["校园推理"]
    extension = {item.element_id: item for item in selection.extension_elements}
    assert "mystery_clue_ledger" in extension
    assert extension["mystery_clue_ledger"].selection_reason.startswith("需要追踪线索投放")
    assert "unknown_id" not in extension


def test_selector_manual_override_keeps_checked_items_only() -> None:
    spec = StorySpec(
        theme="旧案重启后，所有嫌疑人都在撒谎",
        genre="悬疑推理",
        tone="紧张",
    )
    payload = {
        "extension_ids": ["mystery_clue_ledger", "mystery_red_herring"],
    }
    preferences = {
        "manual_override": True,
        "items": [
            {"element_id": "romance_emotional_barriers", "enabled": True, "weight": 90},
            {"element_id": "mystery_clue_ledger", "enabled": False, "locked": True, "weight": 10},
        ],
    }

    selection = _normalize_selection(
        payload,
        spec=spec,
        mode="long",
        preferences=preferences,
    )
    extension_ids = [item.element_id for item in selection.extension_elements]
    assert extension_ids == ["romance_emotional_barriers"]
    assert "手动覆盖" in selection.selector_summary


def test_selector_merge_mode_respects_locked_exclude_and_manual_include() -> None:
    spec = StorySpec(
        theme="偏远村庄的连环失踪案",
        genre="悬疑推理",
        tone="阴郁",
    )
    payload = {
        "extension_ids": ["mystery_clue_ledger", "mystery_red_herring"],
    }
    preferences = {
        "manual_override": False,
        "items": [
            {"element_id": "mystery_red_herring", "enabled": False, "locked": True, "weight": 50},
            {"element_id": "romance_relationship_contract", "enabled": True, "weight": 95},
        ],
    }

    selection = _normalize_selection(
        payload,
        spec=spec,
        mode="long",
        preferences=preferences,
    )
    extension_ids = {item.element_id for item in selection.extension_elements}
    assert "mystery_red_herring" not in extension_ids
    assert "mystery_clue_ledger" in extension_ids
    assert "romance_relationship_contract" in extension_ids


def test_selector_persists_preference_priority_metadata() -> None:
    spec = StorySpec(
        theme="旧案卷宗牵出一段隐秘婚约",
        genre="悬疑言情",
        tone="克制",
    )
    payload = {
        "extension_ids": ["mystery_clue_ledger"],
        "extension_selection": [
            {
                "element_id": "mystery_clue_ledger",
                "reason": "案卷线索需要投放与回收管理。",
            }
        ],
    }
    preferences = {
        "items": [
            {"element_id": "romance_relationship_contract", "enabled": True, "weight": 96},
            {"element_id": "mystery_clue_ledger", "enabled": True, "locked": True, "weight": 90},
        ],
    }

    selection = _normalize_selection(
        payload,
        spec=spec,
        mode="long",
        preferences=preferences,
    )
    extension = {item.element_id: item for item in selection.extension_elements}

    clue = extension["mystery_clue_ledger"]
    romance = extension["romance_relationship_contract"]
    assert clue.selection_source == "user_locked"
    assert clue.selection_score == 100.0
    assert clue.user_locked is True
    assert clue.user_weight == 90.0
    assert romance.selection_source == "user_preference"
    assert romance.selection_score > 80.0
    assert romance.user_enabled is True


def test_selector_preset_can_seed_extension_items() -> None:
    spec = StorySpec(
        theme="两姐妹回乡创业，重建祖宅农庄",
        genre="现实向",
        tone="温暖",
    )
    selection = _normalize_selection(
        {},
        spec=spec,
        mode="long",
        preferences={"preset_id": "farming"},
    )
    extension_ids = {item.element_id for item in selection.extension_elements}
    assert "farming_resource_loop" in extension_ids
    assert "farming_season_calendar" in extension_ids


def test_builtin_element_library_covers_optional_extension_families() -> None:
    cards = {card.element_id: card for card in get_blueprint_element_cards(tier="extension")}

    # The ignored data/element_library.json remains an optional user override;
    # product defaults and CI must not depend on that workstation-local file.
    assert cards["family_dynamics_pressure"].library_source == "builtin"
    assert cards["mentor_apprentice_bond"].library_source == "builtin"
    assert cards["food_narrative_vehicle"].library_source == "builtin"


def test_recommend_preset_for_genre_and_related_ids() -> None:
    assert recommend_preset_for_genre("古代言情悬疑") == "romance"
    related = set(get_related_extension_ids_for_genre("古代言情悬疑"))
    assert "romance_emotional_barriers" in related
    assert "mystery_clue_ledger" in related
