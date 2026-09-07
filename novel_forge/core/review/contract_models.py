"""Experimental conversion from task format contracts to Pydantic models.

This module is intentionally read-only relative to the runtime contract source
of truth.  It lets tests and future adapter experiments prove that
``TaskFormatContract`` can drive schema-aware validation without bypassing the
existing router, response repair, or format-contract checks.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, create_model
from pydantic_core import PydanticUndefined

from novel_forge.common.constants import TaskType
from novel_forge.core.format_contracts import (
    OutputKind,
    TaskFormatContract,
    resolve_task_format_contract,
)


def _field_default(field_info: Any) -> Any:
    if getattr(field_info, "default_factory", None) is not None:
        return Field(default_factory=field_info.default_factory)
    default = getattr(field_info, "default", PydanticUndefined)
    if default is PydanticUndefined:
        return None
    return default


def contract_to_model(
    task_type: TaskType,
    *,
    context: dict[str, Any] | None = None,
    name: str | None = None,
) -> type[BaseModel]:
    """Build a Pydantic model from a resolved JSON task contract.

    Required top-level contract keys become required model fields.  Field types
    and defaults are borrowed from the registered response schema when present;
    contract-only keys fall back to ``Any``.  Extra fields are forbidden only
    when the contract already declares ``allowed_top_level_keys``.
    """

    contract = resolve_task_format_contract(task_type, context)
    if contract is None or contract.output_kind != OutputKind.JSON:
        raise ValueError(f"{task_type.value} does not have a JSON format contract")
    return contract_to_pydantic(contract, task_type=task_type, name=name)


def contract_to_pydantic(
    contract: TaskFormatContract,
    *,
    task_type: TaskType | None = None,
    name: str | None = None,
) -> type[BaseModel]:
    """Build a Pydantic model from a concrete ``TaskFormatContract``."""

    if contract.output_kind != OutputKind.JSON:
        raise ValueError("Only JSON task format contracts can become Pydantic models")

    model_name = name or _default_model_name(task_type, contract)
    source_model = contract.response_schema_model
    if source_model is None and task_type is not None:
        source_model = _response_schema_model_for_task(task_type)

    required_keys = tuple(dict.fromkeys(contract.required_top_level_keys))
    allowed_keys = tuple(dict.fromkeys(contract.allowed_top_level_keys))
    if allowed_keys:
        model_keys = tuple(dict.fromkeys((*allowed_keys, *required_keys)))
    elif source_model is not None:
        model_keys = tuple(dict.fromkeys((*source_model.model_fields.keys(), *required_keys)))
    else:
        model_keys = required_keys

    source_fields = source_model.model_fields if source_model is not None else {}
    field_defs: dict[str, tuple[Any, Any]] = {}
    for key in model_keys:
        source_field = source_fields.get(key)
        annotation = (
            source_field.rebuild_annotation()
            if source_field is not None and hasattr(source_field, "rebuild_annotation")
            else getattr(source_field, "annotation", Any)
            if source_field is not None
            else Any
        )
        default: Any = ... if key in required_keys else _field_default(source_field)
        field_defs[key] = (annotation or Any, default)

    extra_policy = "forbid" if allowed_keys else "allow"
    return create_model(
        model_name,
        __config__=ConfigDict(extra=extra_policy),
        **field_defs,
    )


def _response_schema_model_for_task(task_type: TaskType) -> type[BaseModel] | None:
    from novel_forge.core.parsing.response_schemas import get_response_schema

    return get_response_schema(task_type)


def _default_model_name(task_type: TaskType | None, contract: TaskFormatContract) -> str:
    if task_type is not None:
        return f"{task_type.name.title().replace('_', '')}ContractModel"
    suffix = contract.schema_model or contract.contract_id.replace(":", "_")
    return "".join(part.capitalize() for part in suffix.replace("-", "_").split("_")) + "Model"
