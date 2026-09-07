"""Tests for experimental TaskFormatContract -> Pydantic model conversion."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from novel_forge.common.constants import TaskType
from novel_forge.core.format_contracts import (
    OutputKind,
    TaskFormatContract,
    get_task_format_contract,
    validate_json_output_contract,
)
from novel_forge.core.review.contract_models import contract_to_model, contract_to_pydantic

SPRINT_5_STRUCTURED_OUTPUT_TASKS = (
    TaskType.ADJUDICATE_INIT_CONFLICT_CANDIDATES,
    TaskType.REPAIR_INIT_ARTIFACT_PATCH,
    TaskType.REFINE_INIT_ARTIFACTS_FROM_SYNOPSIS,
)


def test_contract_to_model_marks_sprint_5_required_keys_required() -> None:
    for task_type in SPRINT_5_STRUCTURED_OUTPUT_TASKS:
        contract = get_task_format_contract(task_type)
        model = contract_to_model(task_type)

        assert contract is not None
        for key in contract.required_top_level_keys:
            assert key in model.model_fields
            assert model.model_fields[key].is_required(), f"{task_type.value}.{key}"


def test_contract_to_model_reuses_response_schema_lenient_types() -> None:
    model = contract_to_model(TaskType.REPAIR_INIT_ARTIFACT_PATCH)
    parsed = model.model_validate({"patches": {"op": "replace"}, "summary": "修复摘要"})

    assert parsed.model_dump()["patches"] == [{"op": "replace"}]
    validate_json_output_contract(
        TaskType.REPAIR_INIT_ARTIFACT_PATCH,
        parsed.model_dump(mode="json"),
    )


def test_contract_to_model_rejects_missing_required_keys() -> None:
    model = contract_to_model(TaskType.REFINE_INIT_ARTIFACTS_FROM_SYNOPSIS)

    with pytest.raises(ValidationError) as exc_info:
        model.model_validate(
            {
                "suggestions": [],
                "repair_scope": [],
                "patches": [],
                "preserve": [],
                "risks": [],
            }
        )

    assert "summary" in str(exc_info.value)


def test_contract_to_model_rejects_gross_schema_type_errors() -> None:
    model = contract_to_model(TaskType.ADJUDICATE_INIT_CONFLICT_CANDIDATES)

    with pytest.raises(ValidationError) as exc_info:
        model.model_validate(
            {
                "schema_version": "audit_v2",
                "dimension": "init_coherence",
                "verdict": "accept",
                "score": "excellent",
                "issues": [],
                "summary": "ok",
                "metadata": {},
                "source_refs": [],
                "repair_scope": [],
                "preserve": [],
                "change_intent": "",
                "blocked": False,
            }
        )

    assert "score" in str(exc_info.value)


def test_contract_to_model_rejects_extra_fields_for_p0_repair_envelope() -> None:
    model = contract_to_model(TaskType.REPAIR_INIT_ARTIFACT_PATCH)
    with pytest.raises(ValidationError, match="debug_note"):
        model.model_validate(
            {
                "patches": [],
                "summary": "无补丁",
                "debug_note": "adapter-side experiment metadata",
            }
        )


def test_contract_to_pydantic_forbids_extra_fields_when_contract_declares_allowed_keys() -> None:
    contract = TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("answer",),
        allowed_top_level_keys=("answer",),
        enforce_required_keys=True,
    )
    model = contract_to_pydantic(contract, name="AllowedOnlyModel")

    with pytest.raises(ValidationError) as exc_info:
        model.model_validate({"answer": "ok", "extra": "nope"})

    assert "extra" in str(exc_info.value)
