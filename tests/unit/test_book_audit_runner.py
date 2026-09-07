from __future__ import annotations

import asyncio

import pytest

from novel_forge.pipeline.steps.book_audit_runner import BookAuditChapter, BookAuditRunner


async def test_book_audit_runner_batches_and_checkpoints(tmp_path) -> None:
    chapters = [BookAuditChapter(chapter_number=i, title=f"第{i}章") for i in range(1, 5)]
    checkpoint = tmp_path / "audit_checkpoint.json"
    runner = BookAuditRunner(
        audit_name="book_editorial_audit",
        chapters=chapters,
        batch_size=2,
        checkpoint_path=checkpoint,
    )
    calls: list[list[int]] = []

    async def call_chunk(batch: list[BookAuditChapter], batch_index: int) -> dict[str, object]:
        calls.append([item.chapter_number for item in batch])
        return {"batch_index": batch_index, "findings": []}

    first = await runner.run(call_chunk)
    second = await runner.run(call_chunk)

    assert first == second
    assert calls == [[1, 2], [3, 4]]
    assert checkpoint.exists()


async def test_book_audit_runner_batch_timeout_preserves_checkpoint(tmp_path) -> None:
    chapters = [BookAuditChapter(chapter_number=i, title=f"第{i}章") for i in range(1, 5)]
    checkpoint = tmp_path / "audit_checkpoint.json"
    runner = BookAuditRunner(
        audit_name="book_editorial_audit",
        chapters=chapters,
        batch_size=2,
        checkpoint_path=checkpoint,
        batch_timeout_s=0.01,
    )

    async def call_chunk(batch: list[BookAuditChapter], batch_index: int) -> dict[str, object]:
        if batch_index == 1:
            return {"batch_index": batch_index, "findings": []}
        await asyncio.sleep(1)
        return {"batch_index": batch_index, "findings": []}

    with pytest.raises(asyncio.TimeoutError):
        await runner.run(call_chunk)

    resumed = BookAuditRunner(
        audit_name="book_editorial_audit",
        chapters=chapters,
        batch_size=2,
        checkpoint_path=checkpoint,
        batch_timeout_s=0.0,
    )
    calls: list[int] = []

    async def resume_chunk(batch: list[BookAuditChapter], batch_index: int) -> dict[str, object]:
        calls.append(batch_index)
        return {"batch_index": batch_index, "findings": []}

    result = await resumed.run(resume_chunk)

    assert calls == [2]
    assert [item["batch_index"] for item in result] == [1, 2]


async def test_book_audit_runner_ignores_checkpoint_when_chapter_content_changes(
    tmp_path,
) -> None:
    checkpoint = tmp_path / "audit_checkpoint.json"
    original_chapters = [
        BookAuditChapter(chapter_number=1, title="第一章", text="旧文本"),
        BookAuditChapter(chapter_number=2, title="第二章", text="旧文本"),
    ]
    runner = BookAuditRunner(
        audit_name="book_editorial_audit",
        chapters=original_chapters,
        batch_size=2,
        checkpoint_path=checkpoint,
    )

    async def first_chunk(batch: list[BookAuditChapter], batch_index: int) -> dict[str, object]:
        return {
            "batch_index": batch_index,
            "chapters": [item.chapter_number for item in batch],
            "text": [item.text for item in batch],
        }

    await runner.run(first_chunk)

    changed_chapters = [
        BookAuditChapter(chapter_number=1, title="第一章", text="新文本"),
        BookAuditChapter(chapter_number=2, title="第二章", text="旧文本"),
    ]
    resumed = BookAuditRunner(
        audit_name="book_editorial_audit",
        chapters=changed_chapters,
        batch_size=2,
        checkpoint_path=checkpoint,
    )
    calls: list[list[str]] = []

    async def changed_chunk(batch: list[BookAuditChapter], batch_index: int) -> dict[str, object]:
        texts = [item.text for item in batch]
        calls.append(texts)
        return {"batch_index": batch_index, "text": texts}

    result = await resumed.run(changed_chunk)

    assert calls == [["新文本", "旧文本"]]
    assert result == [{"batch_index": 1, "text": ["新文本", "旧文本"]}]
