#!/usr/bin/env python3
"""Audit prompt contracts, response envelopes, and production Mock payloads."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_repo_root))

from novel_forge.common.constants import TaskType  # noqa: E402
from novel_forge.core.format_contracts import (  # noqa: E402
    ContractMode,
    OutputKind,
    resolve_task_format_contract,
    validate_json_output_contract,
)
from novel_forge.core.parsing.response_schemas import (  # noqa: E402
    get_response_schema,
    validate_response_schema,
)
from novel_forge.gateway.adapters.mock import _RESPONSES  # noqa: E402
from novel_forge.pipeline.long.services.task_semantic_contracts import (  # noqa: E402
    validate_task_semantic_contract,
)
from novel_forge.prompts.registry import _TASK_TEMPLATE_MAP  # noqa: E402

_DYNAMIC_CONTEXTS: dict[TaskType, tuple[tuple[str, dict[str, Any] | None], ...]] = {
    TaskType.GENERATE_CONFIG: (
        ("mode=short", {"mode": "short"}),
        ("mode=long", {"mode": "long"}),
    ),
    TaskType.POLISH_CONFIG: (
        ("mode=short", {"mode": "short"}),
        ("mode=long", {"mode": "long"}),
    ),
    TaskType.PLAN_OUTLINE: (
        ("full", None),
        (
            "fragment=overview",
            {
                "blueprint_fragment_request": {
                    "block_key": "overview",
                    "required_keys": ["synopsis", "volume_mode", "volumes"],
                }
            },
        ),
        (
            "fragment=phases",
            {
                "blueprint_fragment_request": {
                    "block_key": "phases",
                    "required_keys": ["narrative_phases"],
                }
            },
        ),
        (
            "fragment=turning_points",
            {
                "blueprint_fragment_request": {
                    "block_key": "turning_points",
                    "required_keys": ["key_turning_points", "causal_chains", "subversion_points"],
                }
            },
        ),
        (
            "fragment=character_arcs",
            {
                "blueprint_fragment_request": {
                    "block_key": "character_arcs",
                    "required_keys": ["character_arcs", "emotional_arcs"],
                }
            },
        ),
        (
            "fragment=subplots",
            {
                "blueprint_fragment_request": {
                    "block_key": "subplots",
                    "required_keys": ["subplot_plan", "subplot_collisions"],
                }
            },
        ),
        (
            "fragment=suspense",
            {
                "blueprint_fragment_request": {
                    "block_key": "suspense",
                    "required_keys": ["suspense_schedule"],
                }
            },
        ),
        (
            "fragment=ending",
            {
                "blueprint_fragment_request": {
                    "block_key": "ending",
                    "required_keys": ["ending_strategy"],
                }
            },
        ),
    ),
}


@dataclass(frozen=True)
class EnvelopeAlignmentRow:
    label: str
    task_type: TaskType
    response_schema: str
    required_keys: tuple[str, ...]
    missing_required_keys: tuple[str, ...]


@dataclass(frozen=True)
class MockContractRow:
    task_type: TaskType
    error: str = ""


def _contexts_for_task(task_type: TaskType) -> tuple[tuple[str, dict[str, Any] | None], ...]:
    return _DYNAMIC_CONTEXTS.get(task_type, (("", None),))


def _schema_field_names(task_type: TaskType) -> set[str]:
    schema = get_response_schema(task_type)
    if schema is None:
        return set()
    return set(getattr(schema, "model_fields", {}) or {})


def _schema_name(task_type: TaskType) -> str:
    schema = get_response_schema(task_type)
    if schema is None:
        return "<missing>"
    return str(getattr(schema, "__name__", schema))


def collect_envelope_alignment_rows() -> tuple[EnvelopeAlignmentRow, ...]:
    rows: list[EnvelopeAlignmentRow] = []
    for task_type in sorted(_TASK_TEMPLATE_MAP, key=lambda item: item.value):
        fields = _schema_field_names(task_type)
        schema_name = _schema_name(task_type)
        for suffix, context in _contexts_for_task(task_type):
            contract = resolve_task_format_contract(task_type, context)
            if (
                contract is None
                or contract.output_kind != OutputKind.JSON
                or contract.effective_contract_mode == ContractMode.PARTIAL_OBJECT
                or not contract.enforce_required_keys
            ):
                continue
            required = tuple(contract.required_top_level_keys)
            missing = tuple(key for key in required if key not in fields)
            label = task_type.value if not suffix else f"{task_type.value}[{suffix}]"
            rows.append(
                EnvelopeAlignmentRow(
                    label=label,
                    task_type=task_type,
                    response_schema=schema_name,
                    required_keys=required,
                    missing_required_keys=missing,
                )
            )
    return tuple(rows)


def collect_mock_contract_rows() -> tuple[MockContractRow, ...]:
    """Exercise each JSON Mock through the same structural and semantic gates as runtime."""

    rows: list[MockContractRow] = []
    for task_type, raw in sorted(_RESPONSES.items(), key=lambda item: item[0].value):
        contract = resolve_task_format_contract(task_type)
        if contract is None or contract.output_kind != OutputKind.JSON:
            continue
        error = ""
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise TypeError(f"expected JSON object, got {type(payload).__name__}")
            validate_json_output_contract(task_type, payload)
            validate_response_schema(payload, task_type)
            validate_task_semantic_contract(payload, task_type)
        except (KeyError, TypeError, ValueError) as exc:
            error = f"{type(exc).__name__}: {exc}"
        rows.append(MockContractRow(task_type=task_type, error=error))
    return tuple(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=80,
        help="Maximum number of mismatch rows to print.",
    )
    args = parser.parse_args(argv)

    rows = collect_envelope_alignment_rows()
    mismatches = [row for row in rows if row.missing_required_keys]
    aligned = [row for row in rows if not row.missing_required_keys]
    mock_rows = collect_mock_contract_rows()
    invalid_mocks = [row for row in mock_rows if row.error]

    print("# Contract Envelope Alignment Audit")
    print()
    print(f"- JSON contract cases checked: {len(rows)}")
    print(f"- Aligned cases: {len(aligned)}")
    print(f"- Mismatched cases: {len(mismatches)}")
    print(f"- JSON Mock payloads checked: {len(mock_rows)}")
    print(f"- Invalid Mock payloads: {len(invalid_mocks)}")
    print()

    if mismatches or invalid_mocks:
        if mismatches:
            print("## Mismatches")
            print()
            for row in mismatches[: max(0, args.limit)]:
                keys = ", ".join(row.missing_required_keys)
                print(f"- `{row.label}` [{row.response_schema}]: {keys}")
            if len(mismatches) > args.limit:
                print(f"- ... {len(mismatches) - args.limit} more")
        if invalid_mocks:
            print()
            print("## Invalid Mock Payloads")
            print()
            for row in invalid_mocks[: max(0, args.limit)]:
                print(f"- `{row.task_type.value}`: {row.error}")
        print()
        print("RESULT: FAIL")
        return 1

    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
