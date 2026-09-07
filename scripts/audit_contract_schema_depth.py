#!/usr/bin/env python3
"""Audit how deeply registered JSON task contracts describe response shape.

The existing prompt-layer checks answer "does every task have a contract?".
This audit answers the next question: "does the contract actually carry enough
type information to keep prompts and local parsers synchronized?".
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_repo_root))

from novel_forge.common.constants import TaskType  # noqa: E402
from novel_forge.core.format_contracts import (  # noqa: E402
    OutputKind,
    resolve_task_format_contract,
)
from novel_forge.prompts.registry import _TASK_TEMPLATE_MAP  # noqa: E402

_PLAN_OUTLINE_FRAGMENT_CONTEXTS: tuple[tuple[str, dict[str, Any]], ...] = (
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
                "required_keys": ["key_turning_points"],
            }
        },
    ),
    (
        "fragment=character_arcs",
        {
            "blueprint_fragment_request": {
                "block_key": "character_arcs",
                "required_keys": ["character_arcs"],
            }
        },
    ),
    (
        "fragment=subplots",
        {
            "blueprint_fragment_request": {
                "block_key": "subplots",
                "required_keys": ["subplot_plan"],
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
)


@dataclass(frozen=True)
class ContractAuditRow:
    label: str
    mode: str
    required_count: int
    untyped_required_keys: tuple[str, ...]


def _contexts_for_task(task_type: TaskType) -> tuple[tuple[str, dict[str, Any] | None], ...]:
    if task_type in {TaskType.GENERATE_CONFIG, TaskType.POLISH_CONFIG}:
        return (("mode=short", {"mode": "short"}), ("mode=long", {"mode": "long"}))
    if task_type == TaskType.PLAN_OUTLINE:
        return (("full", None), *_PLAN_OUTLINE_FRAGMENT_CONTEXTS)
    return (("", None),)


def _has_shape(schema: Any) -> bool:
    if not isinstance(schema, dict):
        return False
    return any(
        key in schema
        for key in (
            "type",
            "$ref",
            "anyOf",
            "oneOf",
            "allOf",
            "items",
            "properties",
        )
    )


def collect_contract_audit_rows() -> tuple[ContractAuditRow, ...]:
    rows: list[ContractAuditRow] = []
    for task_type in sorted(_TASK_TEMPLATE_MAP, key=lambda item: item.value):
        for suffix, context in _contexts_for_task(task_type):
            contract = resolve_task_format_contract(task_type, context)
            if contract is None or contract.output_kind != OutputKind.JSON:
                continue
            schema = contract.json_schema or {}
            properties = schema.get("properties") if isinstance(schema, dict) else {}
            properties = properties if isinstance(properties, dict) else {}
            untyped = tuple(
                key for key in contract.required_top_level_keys if not _has_shape(properties.get(key))
            )
            label = task_type.value if not suffix else f"{task_type.value}[{suffix}]"
            rows.append(
                ContractAuditRow(
                    label=label,
                    mode=contract.effective_contract_mode.value,
                    required_count=len(contract.required_top_level_keys),
                    untyped_required_keys=untyped,
                )
            )
    return tuple(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fail-on-shallow",
        action="store_true",
        help="Return non-zero when any required field lacks type/shape metadata.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=80,
        help="Maximum number of shallow rows to print.",
    )
    args = parser.parse_args(argv)

    rows = collect_contract_audit_rows()
    shallow = [row for row in rows if row.untyped_required_keys]
    typed = [row for row in rows if not row.untyped_required_keys]

    print("# Contract Schema Depth Audit")
    print()
    print(f"- JSON contract cases checked: {len(rows)}")
    print(f"- Typed required-key cases: {len(typed)}")
    print(f"- Shallow required-key cases: {len(shallow)}")
    print()

    if shallow:
        print("## Shallow Cases")
        print()
        for row in shallow[: max(0, args.limit)]:
            keys = ", ".join(row.untyped_required_keys)
            print(f"- `{row.label}` [{row.mode}]: {keys}")
        if len(shallow) > args.limit:
            print(f"- ... {len(shallow) - args.limit} more")
        print()

    if shallow and args.fail_on_shallow:
        print("RESULT: FAIL")
        return 1

    print("RESULT: PASS" if not shallow else "RESULT: WARN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
