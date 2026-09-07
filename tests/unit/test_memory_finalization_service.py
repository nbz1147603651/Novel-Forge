"""Tests for the shared manifest-owned chapter memory phase."""

from __future__ import annotations

from typing import Any

from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.artifact_manifest import ArtifactManifest
from novel_forge.pipeline.finalization_manifest import chapter_finalization_artifact
from novel_forge.pipeline.long.services.memory_finalization import (
    finalize_chapter_memory_phase,
)


class _MemoryContext:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def finalize_chapter_memory(
        self,
        *,
        chapter_number: int,
        text: str,
        creative_report_text: str,
        chapter_result: Any | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "chapter_number": chapter_number,
                "text": text,
                "creative_report_text": creative_report_text,
                "chapter_result": chapter_result,
            }
        )
        if self.fail:
            raise RuntimeError("memory backend offline")
        return {"saved": True, "tasks_ok": ["summary"], "tasks_failed": []}

    def get_memory_status_for_ui(self) -> dict[str, Any]:
        return {"ready": True}


async def test_memory_finalization_reuses_success_for_same_final_hash(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("memory-once"))
    layout.ensure_dirs()
    context = _MemoryContext()
    events: list[tuple[str, dict[str, Any]]] = []
    chapter_result = object()

    first = await finalize_chapter_memory_phase(
        storage=storage,
        layout=layout,
        memory_context=context,
        chapter_number=1,
        chapter_text="第一章终稿",
        creative_report_text="摘要",
        chapter_result=chapter_result,
        on_step=lambda step, payload: events.append((step, payload)),
    )
    second = await finalize_chapter_memory_phase(
        storage=storage,
        layout=layout,
        memory_context=context,
        chapter_number=1,
        chapter_text="第一章终稿",
        creative_report_text="摘要",
        chapter_result=chapter_result,
        on_step=lambda step, payload: events.append((step, payload)),
    )

    assert first.status == "succeeded"
    assert second.status == "reused"
    assert len(context.calls) == 1
    assert context.calls[0]["chapter_result"] is chapter_result
    assert [step for step, _ in events] == ["memory_updated", "finalization_phase_reused"]


async def test_pending_memory_phase_is_compensated_without_regeneration(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("memory-pending"))
    layout.ensure_dirs()
    failing = _MemoryContext(fail=True)

    pending = await finalize_chapter_memory_phase(
        storage=storage,
        layout=layout,
        memory_context=failing,
        chapter_number=2,
        chapter_text="第二章终稿",
    )
    pending_record = ArtifactManifest(storage, layout).get(
        chapter_finalization_artifact(2, "memory")
    )

    recovered_context = _MemoryContext()
    recovered = await finalize_chapter_memory_phase(
        storage=storage,
        layout=layout,
        memory_context=recovered_context,
        chapter_number=2,
        chapter_text="第二章终稿",
    )
    recovered_record = ArtifactManifest(storage, layout).get(
        chapter_finalization_artifact(2, "memory")
    )

    assert pending.status == "pending"
    assert pending_record is not None and pending_record.status == "needs_repair"
    assert pending_record.metadata["chapter_text_snapshot"] == "第二章终稿"
    assert recovered.status == "succeeded"
    assert len(recovered_context.calls) == 1
    assert recovered_record is not None and recovered_record.status == "succeeded"


async def test_changed_final_hash_runs_memory_phase_again(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("memory-new-hash"))
    layout.ensure_dirs()
    context = _MemoryContext()

    await finalize_chapter_memory_phase(
        storage=storage,
        layout=layout,
        memory_context=context,
        chapter_number=3,
        chapter_text="原终稿",
    )
    changed = await finalize_chapter_memory_phase(
        storage=storage,
        layout=layout,
        memory_context=context,
        chapter_number=3,
        chapter_text="人工修订后终稿",
    )

    assert changed.status == "succeeded"
    assert len(context.calls) == 2
