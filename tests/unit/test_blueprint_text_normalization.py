"""Regression tests for blueprint fragment text normalization."""

from __future__ import annotations

from novel_forge.common.constants import TaskType
from novel_forge.core.format_contracts import validate_json_output_contract
from novel_forge.core.schemas.outline import NarrativeBlueprint
from novel_forge.pipeline.long.services.blueprint.blueprint_payloads import (
    pre_normalize_blueprint_payload,
)
from novel_forge.pipeline.long.services.task_output_adapters import apply_task_output_adapter


def test_pre_normalize_blueprint_payload_coerces_nested_ending_strategy() -> None:
    payload = {
        "ending_strategy": {
            "summary": "第65-70章完成终局收束。",
            "ending_image": {"final_scene": "外滩钟楼下，怀表与金镯相撞。"},
        }
    }

    normalized = pre_normalize_blueprint_payload(payload, total_chapters=70)

    assert normalized is not payload
    assert isinstance(normalized["ending_strategy"], str)
    assert "第65-70章完成终局收束" in normalized["ending_strategy"]
    assert "终局画面" in normalized["ending_strategy"]


def test_pre_normalize_blueprint_payload_uses_readable_text_for_nested_strategy() -> None:
    normalized = pre_normalize_blueprint_payload(
        {"ending_strategy": {"summary": "终局回收信物、商战与情感承诺。"}},
        total_chapters=12,
    )

    assert normalized["ending_strategy"] == "终局回收信物、商战与情感承诺。"


def test_narrative_phase_accepts_common_fragment_aliases() -> None:
    blueprint = NarrativeBlueprint.model_validate(
        {
            "synopsis": "主线围绕信物与商战推进。",
            "narrative_phases": [
                {
                    "phase_name": "惊鸿初见",
                    "chapter_start": 1,
                    "chapter_end": 15,
                    "phase_description": "初遇与商战压力同步入局。",
                    "main_locations": ["上海国际会议中心", "古董店"],
                }
            ],
            "ending_strategy": "终局回收信物、商战与情感承诺。",
        }
    )

    assert blueprint.narrative_phases[0].description == "初遇与商战压力同步入局。"
    assert blueprint.narrative_phases[0].primary_locations == ["上海国际会议中心", "古董店"]


def test_plan_outline_adapter_normalizes_phase_aliases_before_contract_validation() -> None:
    payload = {
        "narrative_phases": [
            {
                "phase_name": "惊鸿初见",
                "chapter_start": 1,
                "chapter_end": 15,
                "phase_description": "初遇与商战压力同步入局。",
                "main_locations": ["上海国际会议中心", "古董店"],
            }
        ]
    }
    context = {
        "total_chapters": 80,
        "blueprint_fragment_request": {
            "block_key": "phases",
            "required_keys": ["narrative_phases"],
        },
    }

    result = apply_task_output_adapter(payload, TaskType.PLAN_OUTLINE, context=context)

    assert result.changed is True
    assert result.adapter == "plan_outline_blueprint_normalizer"
    assert "phase_description" not in payload["narrative_phases"][0]
    assert payload["narrative_phases"][0]["description"] == "初遇与商战压力同步入局。"
    assert payload["narrative_phases"][0]["primary_locations"] == [
        "上海国际会议中心",
        "古董店",
    ]
    validate_json_output_contract(
        TaskType.PLAN_OUTLINE,
        payload,
        context=context,
        explicit_required_keys=("narrative_phases",),
        include_contract_required_keys=False,
        validate_json_schema=True,
    )


def test_plan_outline_adapter_strips_runtime_metadata_before_fragment_validation() -> None:
    payload = {
        "schema_version": "2.0",
        "created_at": "2026-05-24T00:00:00Z",
        "ending_strategy": "终局回收主线、支线与主题承诺。",
    }
    context = {
        "blueprint_fragment_request": {
            "block_key": "ending",
            "required_keys": ["ending_strategy"],
        },
    }

    result = apply_task_output_adapter(payload, TaskType.PLAN_OUTLINE, context=context)

    assert result.changed is True
    assert result.adapter == "plan_outline_blueprint_normalizer"
    assert "schema_version" not in payload
    assert "created_at" not in payload
    assert payload == {"ending_strategy": "终局回收主线、支线与主题承诺。"}
    validate_json_output_contract(
        TaskType.PLAN_OUTLINE,
        payload,
        context=context,
        explicit_required_keys=("ending_strategy",),
        include_contract_required_keys=False,
        validate_allowed_keys=True,
        validate_json_schema=True,
    )


def test_plan_outline_subplots_adapter_accepts_nested_runtime_metadata() -> None:
    payload = {
        "schema_version": "2.0",
        "created_at": "2026-05-24T00:00:00Z",
        "subplot_plan": [
            {
                "schema_version": "2.0",
                "created_at": "2026-05-24T00:00:00Z",
                "name": "配角爱情线",
                "description": "配角线反哺主线证据链。",
                "involved_chapters": [3, 9],
                "priority": "primary",
                "resolution_chapter": 9,
                "resolution_target": "main_turning_point:10",
                "resolution_type": "merge",
                "chapter_events": [
                    {
                        "schema_version": "2.0",
                        "created_at": "2026-05-24T00:00:00Z",
                        "chapter_number": 3,
                        "event": "初遇并埋下身份疑点。",
                        "weave_notes": "反哺主线悬念。",
                        "depends_on": [],
                    }
                ],
                "weave_links": [],
            }
        ],
    }
    context = {
        "blueprint_fragment_request": {
            "block_key": "subplots",
            "required_keys": ["subplot_plan"],
        },
    }

    result = apply_task_output_adapter(payload, TaskType.PLAN_OUTLINE, context=context)

    assert result.changed is True
    assert set(payload) == {"subplot_plan"}
    validate_json_output_contract(
        TaskType.PLAN_OUTLINE,
        payload,
        context=context,
        explicit_required_keys=("subplot_plan",),
        include_contract_required_keys=False,
        validate_allowed_keys=True,
        validate_json_schema=True,
    )


def test_plan_outline_adapter_strips_unrequested_fragment_fields() -> None:
    payload = {
        "synopsis": "全书概述。",
        "subplot_plan": [],
        "suspense_schedule": [],
        "ending_strategy": "终局回收。",
    }
    context = {
        "blueprint_fragment_request": {
            "block_key": "overview",
            "required_keys": ["synopsis"],
        },
    }

    result = apply_task_output_adapter(payload, TaskType.PLAN_OUTLINE, context=context)

    assert result.changed is True
    assert payload == {"synopsis": "全书概述。"}
    validate_json_output_contract(
        TaskType.PLAN_OUTLINE,
        payload,
        context=context,
        explicit_required_keys=("synopsis",),
        include_contract_required_keys=False,
        validate_allowed_keys=True,
        validate_json_schema=True,
    )
