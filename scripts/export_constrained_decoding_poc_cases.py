#!/usr/bin/env python3
"""Export Novel Forge JSON Schema cases for constrained-decoding PoC runners."""

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
from novel_forge.core.format_contracts import OutputKind, resolve_task_format_contract  # noqa: E402

_PLAN_OUTLINE_CHARACTER_ARCS_CONTEXT: dict[str, Any] = {
    "blueprint_fragment_request": {
        "block_key": "character_arcs",
        "required_keys": ["character_arcs", "emotional_arcs"],
    }
}

_POC_CASES: tuple[tuple[str, TaskType, dict[str, Any]], ...] = (
    (
        "plan_outline_character_arcs_fragment",
        TaskType.PLAN_OUTLINE,
        _PLAN_OUTLINE_CHARACTER_ARCS_CONTEXT,
    ),
    ("plan_outline_batch", TaskType.PLAN_OUTLINE_BATCH, {}),
    ("plan_chapter_contracts", TaskType.PLAN_CHAPTER_CONTRACTS, {}),
    ("extract_init_coherence_claims", TaskType.EXTRACT_INIT_COHERENCE_CLAIMS, {}),
    ("book_consistency", TaskType.BOOK_CONSISTENCY, {}),
)


@dataclass(frozen=True)
class ConstrainedDecodingPocCase:
    name: str
    task: str
    contract_mode: str
    schema_name: str
    required_keys: list[str]
    json_schema: dict[str, Any]
    prompt_stub: str


def export_constrained_decoding_poc_cases() -> list[ConstrainedDecodingPocCase]:
    cases: list[ConstrainedDecodingPocCase] = []
    for name, task_type, context in _POC_CASES:
        contract = resolve_task_format_contract(task_type, context)
        if contract is None or contract.output_kind != OutputKind.JSON or not contract.json_schema:
            raise RuntimeError(f"{task_type.value} does not expose a JSON schema for PoC export")
        cases.append(
            ConstrainedDecodingPocCase(
                name=name,
                task=task_type.value,
                contract_mode=contract.effective_contract_mode.value,
                schema_name=contract.schema_model or task_type.value,
                required_keys=list(contract.required_top_level_keys),
                json_schema=contract.json_schema,
                prompt_stub=(
                    "请根据 Novel Forge 当前任务语义生成一个中文 JSON 对象；"
                    "本 PoC 只验证 constrained decoding 的 schema 兼容性、速度和中文内容质量。"
                ),
            )
        )
    return cases


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional output JSON file. Defaults to stdout.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    payload = [asdict(case) for case in export_constrained_decoding_poc_cases()]
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out is None:
        print(text)
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
