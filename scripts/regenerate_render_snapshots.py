"""Regenerate prompt-render snapshots for cases that drift.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/regenerate_render_snapshots.py
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from novel_forge.prompts.builder import PromptBuilder
from scripts.prompt_snapshot_cases import iter_snapshot_cases

SNAPSHOT_DIR = Path("tests/unit/prompts/snapshots")
INDEX_PATH = SNAPSHOT_DIR / "index.json"
TARGET_CASES = {"plan_outline_full", "plan_outline_fragment"}


def main() -> None:
    old_index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    new_index = dict(old_index)  # 保留其他 case 的索引

    for case in iter_snapshot_cases():
        if case.case_id not in TARGET_CASES:
            continue
        rendered = PromptBuilder().render(case.task_type, case.context)
        path = SNAPSHOT_DIR / f"{case.case_id}.txt"
        path.write_text(rendered, encoding="utf-8")
        new_index[case.case_id] = {
            "task_type": case.task_type.value,
            "sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        }
        print(f"Regenerated: {case.case_id} (task_type={case.task_type.value}, sha256={new_index[case.case_id]['sha256'][:8]}...)")

    INDEX_PATH.write_text(
        json.dumps(new_index, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Updated index: {INDEX_PATH}")


if __name__ == "__main__":
    main()
