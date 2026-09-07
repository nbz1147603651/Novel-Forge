"""Tests for DeadLetterQueue persistence and operations."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from novel_forge.gateway.dead_letter_queue import DeadLetterQueue


@pytest.fixture
def dlq_dir(tmp_path: Path) -> Path:
    return tmp_path


def test_enqueue_and_size(dlq_dir: Path) -> None:
    queue = DeadLetterQueue(dlq_dir, max_entries=100)

    entry_id = queue.enqueue(
        task_type="DRAFT_CHAPTER",
        request={"model_id": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        error="Connection timeout",
        routes_tried=[{"provider": "openai", "model": "gpt-4o", "source": "primary"}],
    )

    assert len(entry_id) == 12
    assert queue.size() == 1

    entries = queue.get_failed()
    assert len(entries) == 1
    assert entries[0].task_type == "DRAFT_CHAPTER"
    assert entries[0].error == "Connection timeout"
    assert entries[0].retry_count == 0

    persisted = dlq_dir / "dlq" / f"{entry_id}.json"
    assert persisted.is_file()
    data = json.loads(persisted.read_text(encoding="utf-8"))
    assert data["task_type"] == "DRAFT_CHAPTER"


def test_retry_increments_count(dlq_dir: Path) -> None:
    queue = DeadLetterQueue(dlq_dir, max_entries=100)

    entry_id = queue.enqueue(
        task_type="EDIT_CHAPTER",
        request={"model_id": "gpt-4o-mini"},
        error="Rate limited",
        routes_tried=[{"provider": "openai", "model": "gpt-4o-mini", "source": "primary"}],
    )

    updated = queue.retry(entry_id)
    assert updated is not None
    assert updated.retry_count == 1

    updated2 = queue.retry(entry_id)
    assert updated2 is not None
    assert updated2.retry_count == 2

    assert queue.retry("nonexistent") is None


def test_purge_entries(dlq_dir: Path) -> None:
    queue = DeadLetterQueue(dlq_dir, max_entries=100)

    id1 = queue.enqueue(
        task_type="DRAFT_CHAPTER",
        request={"model_id": "gpt-4o"},
        error="Error 1",
        routes_tried=[],
    )
    _id2 = queue.enqueue(
        task_type="EDIT_CHAPTER",
        request={"model_id": "gpt-4o-mini"},
        error="Error 2",
        routes_tried=[],
    )

    assert queue.size() == 2

    removed = queue.purge(id1)
    assert removed == 1
    assert queue.size() == 1
    assert not (dlq_dir / "dlq" / f"{id1}.json").exists()

    all_removed = queue.purge()
    assert all_removed == 1
    assert queue.size() == 0


def test_max_entries_evicts_oldest(dlq_dir: Path) -> None:
    queue = DeadLetterQueue(dlq_dir, max_entries=3)

    ids = []
    for i in range(5):
        eid = queue.enqueue(
            task_type="DRAFT_CHAPTER",
            request={"model_id": f"model-{i}"},
            error=f"Error {i}",
            routes_tried=[],
        )
        ids.append(eid)

    assert queue.size() == 3

    entries = queue.get_failed()
    remaining_ids = {e.entry_id for e in entries}
    assert ids[0] not in remaining_ids
    assert ids[1] not in remaining_ids
    assert ids[2] in remaining_ids
    assert ids[3] in remaining_ids
    assert ids[4] in remaining_ids

    first_file = dlq_dir / "dlq" / f"{ids[0]}.json"
    assert not first_file.exists()


def test_concurrent_enqueue_preserves_queue_and_files(dlq_dir: Path) -> None:
    queue = DeadLetterQueue(dlq_dir, max_entries=100)

    def _enqueue(index: int) -> str:
        return queue.enqueue(
            task_type="DRAFT_CHAPTER",
            request={"model_id": f"model-{index}"},
            error=f"Error {index}",
            routes_tried=[],
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        ids = list(executor.map(_enqueue, range(40)))

    assert queue.size() == 40
    assert len(ids) == len(set(ids))
    assert len(list((dlq_dir / "dlq").glob("*.json"))) == 40
