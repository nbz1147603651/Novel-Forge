#!/usr/bin/env python3
"""Audit JSON task contracts for structured-output schema coverage.

This is intentionally read-only. It reports schema source/strength and a
migration priority so schema-first rollout can be tracked without blocking CI.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from novel_forge.common.constants import TaskType  # noqa: E402
from novel_forge.core.format_contracts import (  # noqa: E402
    _TASK_FORMAT_CONTRACTS,
    OutputKind,
    TaskFormatContract,
    resolve_task_format_contract,
)


@dataclass(frozen=True)
class StructuredOutputContractAuditRow:
    task: str
    contract_mode: str
    schema_source: str
    schema_strength: str
    required_keys: list[str]
    allowed_keys: list[str]
    has_pydantic_response_model: bool
    has_dynamic_schema: bool
    priority: str
    recommendation: str


_PLAN_OUTLINE_FRAGMENT_CONTEXTS: tuple[tuple[str, dict[str, Any]], ...] = (
    (
        "PLAN_OUTLINE#overview",
        {
            "blueprint_fragment_request": {
                "block_key": "overview",
                "required_keys": ["synopsis", "volume_mode", "volumes"],
            }
        },
    ),
    (
        "PLAN_OUTLINE#phases",
        {
            "blueprint_fragment_request": {
                "block_key": "phases",
                "required_keys": ["narrative_phases"],
            }
        },
    ),
    (
        "PLAN_OUTLINE#turning_points",
        {
            "blueprint_fragment_request": {
                "block_key": "turning_points",
                "required_keys": ["key_turning_points", "causal_chains", "subversion_points"],
            }
        },
    ),
    (
        "PLAN_OUTLINE#character_arcs",
        {
            "blueprint_fragment_request": {
                "block_key": "character_arcs",
                "required_keys": ["character_arcs", "emotional_arcs"],
            }
        },
    ),
    (
        "PLAN_OUTLINE#subplots",
        {
            "blueprint_fragment_request": {
                "block_key": "subplots",
                "required_keys": ["subplot_plan", "subplot_collisions"],
            }
        },
    ),
    (
        "PLAN_OUTLINE#suspense",
        {
            "blueprint_fragment_request": {
                "block_key": "suspense",
                "required_keys": ["suspense_schedule"],
            }
        },
    ),
    (
        "PLAN_OUTLINE#ending",
        {
            "blueprint_fragment_request": {
                "block_key": "ending",
                "required_keys": ["ending_strategy"],
            }
        },
    ),
)


_P0_TASKS = frozenset(
    {
        TaskType.PLAN_OUTLINE,
        TaskType.PLAN_OUTLINE_BATCH,
        TaskType.PLAN_OUTLINE_CONTINUE,
        TaskType.PLAN_CHAPTER_CONTRACTS,
        TaskType.PLAN_CHAPTER,
        TaskType.PLAN_CHAPTER_SCENES,
        TaskType.REPAIR_INIT_ARTIFACT_PATCH,
        TaskType.ADJUDICATE_CONTRACT_COHERENCE,
        TaskType.ADJUDICATE_CONTRACT_COMPLETION,
        TaskType.ADJUDICATE_STATE_DELTA,
        TaskType.ADJUDICATE_FACT_CONFLICT,
        TaskType.ADJUDICATE_FINAL_STATE,
    }
)


def _schema_has_typed_properties(schema: dict[str, Any] | None) -> bool:
    if not isinstance(schema, dict):
        return False
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        return False
    for prop_schema in properties.values():
        if isinstance(prop_schema, dict) and (
            "type" in prop_schema or "$ref" in prop_schema or "anyOf" in prop_schema
        ):
            return True
    return False


def infer_schema_source(
    contract: TaskFormatContract,
    *,
    has_dynamic_schema: bool = False,
) -> str:
    if contract.schema_source:
        return contract.schema_source
    if contract.response_schema_model is not None:
        return "pydantic_model"
    if has_dynamic_schema:
        return "dynamic_fragment_schema"
    if contract.json_schema is not None:
        return "explicit_json_schema"
    return "generated_contract_schema"


def infer_schema_strength(contract: TaskFormatContract) -> str:
    if contract.schema_strength:
        return contract.schema_strength
    schema = contract.json_schema
    if contract.response_schema_model is not None:
        return "strong"
    if isinstance(schema, dict) and schema.get("additionalProperties") is False:
        properties = schema.get("properties")
        required = schema.get("required")
        if isinstance(properties, dict) and isinstance(required, list):
            if set(required).issubset(properties):
                return "strong"
    if _schema_has_typed_properties(schema):
        return "medium"
    return "weak"


def _priority_for_task(task_type: TaskType, contract: TaskFormatContract) -> str:
    if task_type in _P0_TASKS:
        return "P0"
    value = task_type.value
    if (
        value.startswith("extract_")
        or value.startswith("check_")
        or value.startswith("critic_")
        or contract.task_family in {"memory", "chapter_check"}
    ):
        return "P1"
    return "P2"


def _recommendation(row: StructuredOutputContractAuditRow) -> str:
    if row.schema_strength == "strong" and row.has_pydantic_response_model:
        return "keep"
    if row.priority == "P0":
        return "add or tighten Pydantic response model, then add replay fixture"
    if row.schema_strength == "weak":
        return "replace required-key-only contract with typed schema"
    return "monitor; migrate when task becomes high-noise"


def _row_for_contract(
    task_type: TaskType,
    task_label: str,
    contract: TaskFormatContract,
    *,
    has_dynamic_schema: bool = False,
) -> StructuredOutputContractAuditRow:
    row = StructuredOutputContractAuditRow(
        task=task_label,
        contract_mode=contract.effective_contract_mode.value,
        schema_source=infer_schema_source(contract, has_dynamic_schema=has_dynamic_schema),
        schema_strength=infer_schema_strength(contract),
        required_keys=list(contract.required_top_level_keys),
        allowed_keys=list(contract.allowed_top_level_keys),
        has_pydantic_response_model=contract.response_schema_model is not None,
        has_dynamic_schema=has_dynamic_schema,
        priority=_priority_for_task(task_type, contract),
        recommendation="",
    )
    return StructuredOutputContractAuditRow(
        **{**asdict(row), "recommendation": _recommendation(row)}
    )


def audit_structured_output_contracts() -> list[StructuredOutputContractAuditRow]:
    rows: list[StructuredOutputContractAuditRow] = []
    for task_type in sorted(_TASK_FORMAT_CONTRACTS, key=lambda item: item.value):
        contract = resolve_task_format_contract(task_type)
        if contract is None or contract.output_kind != OutputKind.JSON:
            continue
        rows.append(_row_for_contract(task_type, task_type.name, contract))

    for task_label, context in _PLAN_OUTLINE_FRAGMENT_CONTEXTS:
        contract = resolve_task_format_contract(TaskType.PLAN_OUTLINE, context)
        if contract is None or contract.output_kind != OutputKind.JSON:
            continue
        rows.append(
            _row_for_contract(
                TaskType.PLAN_OUTLINE,
                task_label,
                contract,
                has_dynamic_schema=True,
            )
        )
    return rows


def _render_markdown(rows: list[StructuredOutputContractAuditRow]) -> str:
    counts: dict[str, int] = {}
    for row in rows:
        key = f"{row.schema_source}/{row.schema_strength}"
        counts[key] = counts.get(key, 0) + 1

    lines = [
        "# Structured Output Contract Audit",
        "",
        f"- JSON contracts: {len(rows)}",
        "- Source/strength counts: "
        + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())),
        "",
        "| Task | Mode | Source | Strength | Model | Dynamic | Required Keys | Priority | Recommendation |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        required = ", ".join(row.required_keys[:8])
        if len(row.required_keys) > 8:
            required += f", +{len(row.required_keys) - 8}"
        lines.append(
            "| "
            + " | ".join(
                [
                    row.task,
                    row.contract_mode,
                    row.schema_source,
                    row.schema_strength,
                    "yes" if row.has_pydantic_response_model else "no",
                    "yes" if row.has_dynamic_schema else "no",
                    required,
                    row.priority,
                    row.recommendation,
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出 JSON 数组而不是 Markdown 表格",
    )
    parser.add_argument(
        "--priority",
        choices=["P0", "P1", "P2"],
        default=None,
        help="只输出指定迁移优先级",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    rows = audit_structured_output_contracts()
    if args.priority:
        rows = [row for row in rows if row.priority == args.priority]
    if args.json:
        print(json.dumps([asdict(row) for row in rows], ensure_ascii=False, indent=2))
    else:
        print(_render_markdown(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
