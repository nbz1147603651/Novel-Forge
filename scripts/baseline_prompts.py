#!/usr/bin/env python3
"""Report and check prompt-contract catalog baselines."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

_repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_repo_root))

from novel_forge.common.constants import TaskType  # noqa: E402
from novel_forge.core.format_contracts import _TASK_FORMAT_CONTRACTS  # noqa: E402
from novel_forge.prompts.registry import _TASK_TEMPLATE_MAP  # noqa: E402


@dataclass(frozen=True)
class PromptBaseline:
    task_types: int
    template_mappings: int
    unique_templates: int
    format_contracts: int
    static_json_schemas: int
    enforce_required_keys: int


BASELINE = PromptBaseline(
    task_types=159,
    template_mappings=156,
    unique_templates=143,
    format_contracts=156,
    static_json_schemas=54,
    enforce_required_keys=140,
)


def compute_current() -> PromptBaseline:
    """Compute prompt catalog statistics from source-of-truth registries."""
    return PromptBaseline(
        task_types=len(TaskType),
        template_mappings=len(_TASK_TEMPLATE_MAP),
        unique_templates=len(set(_TASK_TEMPLATE_MAP.values())),
        format_contracts=len(_TASK_FORMAT_CONTRACTS),
        static_json_schemas=sum(
            1 for contract in _TASK_FORMAT_CONTRACTS.values() if contract.json_schema is not None
        ),
        enforce_required_keys=sum(
            1 for contract in _TASK_FORMAT_CONTRACTS.values() if contract.enforce_required_keys
        ),
    )


def drift_lines(current: PromptBaseline, expected: PromptBaseline = BASELINE) -> list[str]:
    """Return human-readable drift lines for mismatched baseline fields."""
    lines: list[str] = []
    for field in expected.__dataclass_fields__:
        expected_value = getattr(expected, field)
        current_value = getattr(current, field)
        if current_value != expected_value:
            lines.append(f"{field}: expected {expected_value}, got {current_value}")
    return lines


def print_report(current: PromptBaseline) -> None:
    """Print the baseline report in stable order."""
    print("Prompt Contract Baseline")
    print("========================")
    print(f"TaskType count: {current.task_types}")
    print(f"Template mappings: {current.template_mappings}")
    print(f"Unique templates: {current.unique_templates}")
    print(f"TaskFormatContract entries: {current.format_contracts}")
    print(f"With Pydantic schema: {current.static_json_schemas}")
    print(f"enforce_required_keys=True: {current.enforce_required_keys}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail when baseline stats drift")
    args = parser.parse_args(argv)

    current = compute_current()
    print_report(current)
    lines = drift_lines(current)
    if lines:
        print()
        print("Baseline drift:")
        for line in lines:
            print(f"- {line}")
        return 1 if args.check else 0
    if args.check:
        print()
        print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
