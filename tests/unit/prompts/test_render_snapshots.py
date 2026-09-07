"""Byte-level render snapshots for high-risk prompt contexts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from novel_forge.prompts.builder import PromptBuilder
from scripts.prompt_snapshot_cases import iter_snapshot_cases

_SNAPSHOT_DIR = Path(__file__).with_name("snapshots")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("case", iter_snapshot_cases(), ids=lambda case: case.case_id)
def test_render_snapshot_matches(case) -> None:
    snapshot_path = _SNAPSHOT_DIR / f"{case.case_id}.txt"
    expected = snapshot_path.read_text(encoding="utf-8")

    rendered = PromptBuilder().render(case.task_type, case.context)

    assert rendered == expected


def test_snapshot_index_matches_files() -> None:
    index = json.loads((_SNAPSHOT_DIR / "index.json").read_text(encoding="utf-8"))
    cases = {case.case_id: case for case in iter_snapshot_cases()}

    assert set(index) == set(cases)
    for case_id, metadata in index.items():
        text = (_SNAPSHOT_DIR / f"{case_id}.txt").read_text(encoding="utf-8")
        assert metadata["task_type"] == cases[case_id].task_type.value
        assert metadata["sha256"] == _sha256(text)
