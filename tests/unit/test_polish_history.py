"""Tests for polish history persistence."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from novel_forge.persistence.polish_history import PolishHistoryRecorder


def test_spec_polish_history_keeps_new_entry_after_truncated_tail(tmp_path: Path) -> None:
    history_path = tmp_path / "reports" / "polish_spec_history.jsonl"
    history_path.parent.mkdir(parents=True)
    history_path.write_text('{"timestamp": "interrupted"', encoding="utf-8")

    recorder = PolishHistoryRecorder(tmp_path)
    recorder.record_spec_polish(
        user_hint="强化核心卖点",
        selected_suggestions=["提高冲突"],
        focus_fields=["premise"],
        before_spec={"premise": "旧"},
        after_spec={"premise": "新"},
        changed_keys=["premise"],
        ai_suggestions=["提高冲突"],
    )

    entries = recorder.load_spec_history()

    assert len(entries) == 1
    assert entries[0].user_hint == "强化核心卖点"
    lines = history_path.read_text(encoding="utf-8").splitlines()
    assert lines[1].startswith('{"timestamp":')


def test_outline_polish_history_keeps_concurrent_entries(tmp_path: Path) -> None:
    recorder = PolishHistoryRecorder(tmp_path)

    def record_entry(index: int) -> None:
        recorder.record_outline_polish(
            user_hint=f"hint-{index}",
            selected_suggestions=[f"suggestion-{index}"],
            focus_fields=["chapters"],
            chapter_range="1-3",
            before_outline={"chapters": []},
            after_outline={"chapters": [{"index": index}]},
            changed_chapters=[index],
            ai_suggestions=[f"suggestion-{index}"],
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(record_entry, range(24)))

    entries = recorder.load_outline_history()

    assert {entry.user_hint for entry in entries} == {
        f"hint-{index}" for index in range(24)
    }
