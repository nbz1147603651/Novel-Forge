#!/usr/bin/env python3
"""Verify schema-first prompt format contracts.

The output boundary is owned by TaskFormatContract + PromptBuilder, not by
Jinja2 templates. This script checks that registered tasks expose a semantic
contract mode and that the renderer injects the matching output boundary.
"""

from __future__ import annotations

import sys
from pathlib import Path

_repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_repo_root))

from novel_forge.core.format_contracts import (  # noqa: E402
    ContractMode,
    OutputKind,
    render_prompt_contract_block,
    resolve_task_format_contract,
)
from novel_forge.prompts.registry import _TASK_TEMPLATE_MAP  # noqa: E402
from scripts.baseline_prompts import compute_current, drift_lines  # noqa: E402


def _contexts_for_task(task_value: str) -> tuple[dict[str, str] | None, ...]:
    if task_value in {"generate_config", "polish_config"}:
        return ({"mode": "short"}, {"mode": "long"})
    return (None,)


def main() -> int:
    errors: list[tuple[str, str]] = []
    checked_json = 0
    checked_text = 0
    mode_counts: dict[str, int] = {}

    for line in drift_lines(compute_current()):
        errors.append(("prompt_baseline", line))

    for task_type in sorted(_TASK_TEMPLATE_MAP, key=lambda item: item.value):
        task_name = task_type.value
        for context in _contexts_for_task(task_name):
            label = task_name
            if context:
                label = f"{task_name}[mode={context['mode']}]"

            contract = resolve_task_format_contract(task_type, context)
            if contract is None:
                errors.append((label, "registered task has no TaskFormatContract"))
                continue

            contract_mode = contract.effective_contract_mode
            contract_mode_value = contract_mode.value
            mode_counts[contract_mode_value] = mode_counts.get(contract_mode_value, 0) + 1
            if contract.contract_mode is None:
                errors.append((label, "resolved contract must define explicit contract_mode"))
            block = render_prompt_contract_block(task_type, context)
            if "## 统一格式契约（系统注入）" not in block:
                errors.append((label, "contract renderer did not emit unified contract block"))
            if f"contract_mode: `{contract_mode_value}`" not in block:
                errors.append((label, "contract renderer did not emit semantic contract_mode"))

            if contract.output_kind == OutputKind.TEXT:
                checked_text += 1
                if "禁止 JSON" not in block:
                    errors.append((label, "TEXT contract block is missing prose guard text"))
                continue

            checked_json += 1
            if contract_mode == ContractMode.PARTIAL_OBJECT:
                if contract.enforce_required_keys:
                    errors.append((label, "PARTIAL_OBJECT contract must not enforce all keys"))
                if contract.required_top_level_keys:
                    errors.append((label, "PARTIAL_OBJECT contract must not define required keys"))
            else:
                if not contract.strict_mode:
                    errors.append((label, "JSON contract must use strict_mode=True"))
                if not contract.enforce_required_keys:
                    errors.append((label, "JSON contract must enforce required keys"))
                if not contract.required_top_level_keys:
                    errors.append((label, "JSON contract must define required_top_level_keys"))
            if not contract.allowed_top_level_keys and task_name in {
                "spec_enrich",
                "blueprint_element_select",
                "generate_config",
                "polish_config",
            }:
                errors.append((label, "critical entry JSON contract must define allowed keys"))
            if not contract.json_schema:
                errors.append((label, "JSON contract must expose a JSON Schema"))
            elif contract.json_schema.get("type") == "object":
                schema_properties = contract.json_schema.get("properties")
                if not isinstance(schema_properties, dict):
                    errors.append((label, "object JSON Schema must define properties"))
                else:
                    schema_keys = tuple(str(key) for key in schema_properties)
                    if contract.allowed_top_level_keys and schema_keys != contract.allowed_top_level_keys:
                        errors.append(
                            (
                                label,
                                "JSON Schema properties must match allowed_top_level_keys",
                            )
                        )
                    schema_required = contract.json_schema.get("required")
                    if isinstance(schema_required, list):
                        required_keys = tuple(str(key) for key in schema_required)
                        if required_keys != contract.required_top_level_keys:
                            errors.append(
                                (
                                    label,
                                    "JSON Schema required keys must match required_top_level_keys",
                                )
                            )

            for key in contract.required_top_level_keys:
                if f"`{key}`" not in block and f'"{key}"' not in block:
                    errors.append((label, f"rendered contract block missing required key: {key}"))

            print(
                f"OK   {label}: {len(contract.required_top_level_keys)} required keys, "
                f"contract_id={contract.contract_id}, mode={contract_mode_value}"
            )

    print()
    print("=" * 60)
    print(f"Checked JSON contracts: {checked_json}")
    print(f"Checked TEXT contracts: {checked_text}")
    print("Contract modes: " + ", ".join(f"{key}={mode_counts[key]}" for key in sorted(mode_counts)))
    print("=" * 60)

    if errors:
        print()
        print("MISMATCHES FOUND:")
        for task_name, message in errors:
            print(f"  [{task_name}] {message}")
        print()
        print("RESULT: FAIL")
        return 1

    print()
    print("RESULT: PASS — schema-first format contracts are renderable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
