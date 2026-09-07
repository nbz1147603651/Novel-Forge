"""Tests for constrained-decoding PoC schema export cases."""

from __future__ import annotations

from scripts.export_constrained_decoding_poc_cases import export_constrained_decoding_poc_cases


def test_export_constrained_decoding_poc_cases_covers_planned_tasks() -> None:
    cases = export_constrained_decoding_poc_cases()

    assert [case.name for case in cases] == [
        "plan_outline_character_arcs_fragment",
        "plan_outline_batch",
        "plan_chapter_contracts",
        "extract_init_coherence_claims",
        "book_consistency",
    ]


def test_export_constrained_decoding_poc_cases_have_usable_json_schemas() -> None:
    cases = export_constrained_decoding_poc_cases()

    for case in cases:
        assert case.task
        assert case.contract_mode
        assert case.schema_name
        assert case.required_keys
        assert case.json_schema.get("type") == "object"
        assert case.json_schema.get("properties")
        assert "中文 JSON 对象" in case.prompt_stub


def test_plan_outline_character_arcs_case_preserves_original_failure_keys() -> None:
    case_by_name = {
        case.name: case for case in export_constrained_decoding_poc_cases()
    }
    case = case_by_name["plan_outline_character_arcs_fragment"]

    assert case.contract_mode == "fragment_object"
    assert case.required_keys == ["character_arcs", "emotional_arcs"]
    assert set(case.json_schema["required"]) == {"character_arcs", "emotional_arcs"}
