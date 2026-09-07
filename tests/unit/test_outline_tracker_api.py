"""Tests for outline tracker API helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from novel_forge.api.routes.outline_tracker import (
    _get_outline_tracker,
    _save_outline_tracker,
    update_outline_tracker,
)
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.gateway.types import ModelResponse
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.story_kernel.outline_tracker import HybridOutlineTracker
from novel_forge.story_kernel.outline_tracker import OutlineTracker as OutlineRelationshipTracker


def test_outline_tracker_save_uses_project_layout_path(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    runtime = SimpleNamespace(storage=storage)
    tracker = OutlineRelationshipTracker()

    storage.ensure_project_dir("demo")
    _save_outline_tracker(runtime, "demo", tracker)

    layout = ProjectLayout(storage.project_path("demo"))
    assert layout.outline_tracker_path.exists()
    assert not (storage.project_path("demo") / "canon" / "outline_tracker.json").exists()


def test_outline_tracker_loads_hybrid_state(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    runtime = SimpleNamespace(storage=storage)
    layout = ProjectLayout(storage.ensure_project_dir("demo"))
    storage.save_json(layout.outline_tracker_path, HybridOutlineTracker().to_dict())

    tracker = _get_outline_tracker(runtime, "demo")

    assert isinstance(tracker, HybridOutlineTracker)


async def test_update_outline_tracker_preserves_chapter_mismatch_status(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    tracker = OutlineRelationshipTracker()
    runtime = SimpleNamespace(storage=storage, _outline_trackers={"demo": tracker})
    storage.ensure_project_dir("demo")

    with pytest.raises(HTTPException) as exc_info:
        await update_outline_tracker(
            "demo",
            1,
            {"chapter_number": 2, "goal": "推进主线"},
            runtime,
        )

    assert exc_info.value.status_code == 422


def test_hybrid_outline_tracker_reports_llm_chapter_window() -> None:
    tracker = HybridOutlineTracker(batch_size=1, llm_threshold_batches=1)

    first = tracker.update_from_batch(
        [ChapterOutline(chapter_number=1, title="平静", goal="铺垫")]
    )
    assert first["needs_llm"] is True
    assert first["llm_from_chapter"] == 1
    assert first["last_llm_chapter"] == 1

    second = tracker.update_from_batch(
        [ChapterOutline(chapter_number=2, title="平静", goal="铺垫")]
    )
    assert second["needs_llm"] is True
    assert second["llm_from_chapter"] == 2
    assert second["last_llm_chapter"] == 2


async def test_hybrid_outline_tracker_retries_llm_extraction() -> None:
    class _Router:
        def __init__(self) -> None:
            self.calls = 0

        async def route(self, request: object) -> ModelResponse:
            self.calls += 1
            if self.calls == 1:
                return ModelResponse(content="not json")
            return ModelResponse(
                content='{"relationship_changes": [], "themes": ["信任"], "confidence": 0.8}'
            )

    router = _Router()
    tracker = HybridOutlineTracker(batch_size=1)

    result = await tracker.trigger_llm_extraction(
        [ChapterOutline(chapter_number=1, title="平静", goal="铺垫")],
        ctx=SimpleNamespace(router=router),
    )

    assert router.calls == 2
    assert result.confidence == 0.8
    assert result.attempts == 2
    assert result.error == ""
