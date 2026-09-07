#!/usr/bin/env python3
"""Capture or verify high-risk prompt render snapshots."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

_repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_repo_root))

from novel_forge.prompts.builder import PromptBuilder  # noqa: E402
from scripts.prompt_snapshot_cases import iter_snapshot_cases  # noqa: E402

_SNAPSHOT_DIR = _repo_root / "tests" / "unit" / "prompts" / "snapshots"
_INDEX_PATH = _SNAPSHOT_DIR / "index.json"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="overwrite existing snapshots instead of verifying them",
    )
    args = parser.parse_args(argv)

    builder = PromptBuilder()
    _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    index: dict[str, dict[str, str]] = {}
    mismatches: list[str] = []

    for case in iter_snapshot_cases():
        rendered = builder.render(case.task_type, case.context)
        path = _SNAPSHOT_DIR / f"{case.case_id}.txt"
        digest = _sha256(rendered)
        index[case.case_id] = {
            "task_type": case.task_type.value,
            "sha256": digest,
        }
        if path.exists() and not args.regenerate:
            existing = path.read_text(encoding="utf-8")
            if existing != rendered:
                mismatches.append(case.case_id)
            continue
        path.write_text(rendered, encoding="utf-8")

    if mismatches:
        print("Render snapshot mismatches:")
        for case_id in mismatches:
            print(f"- {case_id}")
        print("Run with --regenerate to update snapshots intentionally.")
        return 1

    if _INDEX_PATH.exists() and not args.regenerate:
        existing_index = json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
        if existing_index != index:
            print("Snapshot index mismatch. Run with --regenerate to update it.")
            return 1
    else:
        _INDEX_PATH.write_text(
            json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print(f"Captured/verified {len(index)} prompt render snapshots in {_SNAPSHOT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
