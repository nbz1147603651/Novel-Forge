#!/usr/bin/env python3
"""Replay sanitized format-error fixtures through local repair and validation."""

from __future__ import annotations

import argparse
import asyncio
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
    merge_required_keys_for_task,
    validate_json_output_contract,
)
from novel_forge.core.response_repair.orchestrator import (  # noqa: E402
    FormatRepairContext,
    repair_json_object_response,
)

_DEFAULT_FIXTURE_DIR = _REPO_ROOT / "tests" / "fixtures" / "format_errors"


@dataclass(frozen=True)
class FormatReplayResult:
    fixture: str
    task: str
    success: bool
    expected_success: bool
    strategy: str
    expected_strategy: str
    error: str = ""

    @property
    def passed(self) -> bool:
        if self.success != self.expected_success:
            return False
        if self.expected_strategy and self.strategy != self.expected_strategy:
            return False
        return not self.error


def _task_type_from_fixture(value: Any) -> TaskType:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("fixture missing task")
    try:
        return TaskType(raw)
    except ValueError:
        task = TaskType.__members__.get(raw.upper())
        if task is None:
            raise ValueError(f"unknown fixture task: {raw}") from None
        return task


def _fixture_paths(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(path.glob("*.json"))


async def replay_format_error_fixture(path: Path) -> FormatReplayResult:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: fixture must be a JSON object")

    task_type = _task_type_from_fixture(payload.get("task"))
    contract_context = payload.get("contract_context")
    if not isinstance(contract_context, dict):
        contract_context = {}
    raw_content = str(payload.get("raw_content") or "")
    expected = payload.get("expected")
    if not isinstance(expected, dict):
        expected = {}
    explicit_keys = tuple(str(key) for key in expected.get("top_level_keys") or () if str(key))
    required_keys = merge_required_keys_for_task(
        task_type,
        explicit_keys,
        context=contract_context,
    )

    def _validate(data: dict[str, Any]) -> None:
        validate_json_output_contract(
            task_type,
            data,
            context=contract_context,
            explicit_required_keys=required_keys,
            include_contract_required_keys=True,
            validate_allowed_keys=True,
            validate_json_schema=True,
        )

    result = await repair_json_object_response(
        FormatRepairContext(
            task_type=task_type,
            raw_content=raw_content,
            error=None,
            required_keys=required_keys,
            contract_context=contract_context,
            include_contract_required_keys=True,
        ),
        validator=_validate,
    )
    expected_success = bool(expected.get("success", True))
    expected_strategy = str(expected.get("strategy") or "")
    error = "" if result.success else str(result.error or "repair failed")
    return FormatReplayResult(
        fixture=str(path),
        task=task_type.value,
        success=bool(result.success),
        expected_success=expected_success,
        strategy=result.strategy,
        expected_strategy=expected_strategy,
        error=error,
    )


async def replay_format_errors(path: Path = _DEFAULT_FIXTURE_DIR) -> list[FormatReplayResult]:
    return [await replay_format_error_fixture(fixture_path) for fixture_path in _fixture_paths(path)]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=_DEFAULT_FIXTURE_DIR,
        help="Fixture JSON file or directory. Defaults to tests/fixtures/format_errors.",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON instead of text.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    results = asyncio.run(replay_format_errors(args.path))
    if args.json:
        print(json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2))
    else:
        for result in results:
            status = "PASS" if result.passed else "FAIL"
            print(f"{status} {Path(result.fixture).name} task={result.task} strategy={result.strategy}")
            if result.error:
                print(f"  error={result.error}")
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
