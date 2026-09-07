"""Structured-output contract audit script tests."""

from __future__ import annotations

from scripts.audit_structured_output_contracts import audit_structured_output_contracts


def test_audit_reports_plan_outline_fragment_as_strong_pydantic_schema() -> None:
    rows = audit_structured_output_contracts()
    row = next(item for item in rows if item.task == "PLAN_OUTLINE#character_arcs")

    assert row.schema_source == "pydantic_model"
    assert row.schema_strength == "strong"
    assert row.has_pydantic_response_model is True
    assert row.has_dynamic_schema is True
    assert row.priority == "P0"


def test_audit_reports_all_p0_contracts_as_strong_pydantic_keep() -> None:
    rows = audit_structured_output_contracts()
    p0_rows = [row for row in rows if row.priority == "P0"]

    assert p0_rows
    assert all(row.schema_source == "pydantic_model" for row in p0_rows)
    assert all(row.schema_strength == "strong" for row in p0_rows)
    assert all(row.has_pydantic_response_model is True for row in p0_rows)
    assert all(row.recommendation == "keep" for row in p0_rows)
