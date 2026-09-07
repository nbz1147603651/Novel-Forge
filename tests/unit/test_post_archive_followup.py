from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from novel_forge.core.exceptions import StateError
from novel_forge.core.utils.text_hash import source_text_hash
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.artifact_manifest import ArtifactManifest
from novel_forge.pipeline.finalization_manifest import (
    matching_finalization_phase,
    record_finalization_success,
)
from novel_forge.workspace.execution_post_archive import complete_chapter_archive
from novel_forge.workspace.helpers.execution_runners import _is_completed_chapter_result


@pytest.fixture
def archive(tmp_path, monkeypatch):
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("book"))
    storage.save_text(layout.chapter_path(3), "已完成且不得重新生成的第三章。")
    text_hash = source_text_hash(storage.load_text(layout.chapter_path(3)))
    manifest = ArtifactManifest(storage, layout)
    for phase in ("final_text", "reports", "narrative_state", "story_kernel"):
        record_finalization_success(manifest, chapter_number=3, phase=phase, text_hash=text_hash)
    runtime = SimpleNamespace(
        storage=storage, settings=SimpleNamespace(narrative_state_required=True)
    )
    horizon = AsyncMock()
    future = AsyncMock(return_value={"status": "fixed"})
    monkeypatch.setattr(
        "novel_forge.workspace.planning_jobs.request_planning_horizon", horizon
    )
    monkeypatch.setattr(
        "novel_forge.workspace.execution_future_planning.execute_future_planning", future
    )
    monkeypatch.setattr(
        "novel_forge.workspace.helpers.execution_runners._project_finalized_chapter",
        lambda *a, **k: True,
    )
    return runtime, layout, horizon, future, text_hash


async def test_duplicate_callback_and_restart_do_not_repeat_completed_followup(archive):
    runtime, layout, horizon, future, text_hash = archive
    for _ in range(2):
        await complete_chapter_archive(
            runtime, project_id="book", chapter_number=3, defer_post_archive_tts=True
        )
    assert horizon.await_count == 1
    assert future.await_count == 1
    assert matching_finalization_phase(
        ArtifactManifest(runtime.storage, layout),
        chapter_number=3,
        phase="archive_committed",
        text_hash=text_hash,
    )


async def test_planning_failure_does_not_undo_archive_or_repeat_other_followups(archive):
    runtime, layout, horizon, future, _ = archive
    horizon.side_effect = RuntimeError("补纲模型暂不可用")
    events = []
    original = layout.chapter_path(3).read_bytes()
    await complete_chapter_archive(
        runtime,
        project_id="book",
        chapter_number=3,
        on_step_progress=lambda step, data: events.append(data),
        defer_post_archive_tts=True,
    )
    assert events[0]["message"] == "章节已归档，规划待重试"
    assert events[0]["action"] == "retry_followup_only"
    horizon.side_effect = None
    await complete_chapter_archive(
        runtime, project_id="book", chapter_number=3, defer_post_archive_tts=True
    )
    assert horizon.await_count == 2
    assert future.await_count == 1
    assert layout.chapter_path(3).read_bytes() == original


async def test_missing_required_state_never_dispatches_planning(archive):
    runtime, layout, horizon, future, _ = archive
    runtime.storage.save_text(layout.chapter_path(3), "人工改稿，旧终结清单已失效。")
    with pytest.raises(StateError):
        await complete_chapter_archive(runtime, project_id="book", chapter_number=3)
    horizon.assert_not_awaited()
    future.assert_not_awaited()


def test_dict_checkpoint_response_is_not_a_completed_archive():
    assert not _is_completed_chapter_result({"status": "needs_decision"})
    assert not _is_completed_chapter_result(SimpleNamespace(status="needs_decision"))
    assert _is_completed_chapter_result({"status": "completed"})


def test_partial_tracked_archive_cannot_be_treated_as_legacy(archive):
    from novel_forge.pipeline.finalization_manifest import tracked_finalization_ready

    runtime, layout, _, _, _ = archive
    runtime.storage.save_text(layout.chapter_path(4), "只落盘正文，状态尚未提交。")
    record_finalization_success(
        ArtifactManifest(runtime.storage, layout),
        chapter_number=4,
        phase="final_text",
        text_hash=source_text_hash(runtime.storage.load_text(layout.chapter_path(4))),
    )
    assert not tracked_finalization_ready(runtime.storage, layout, 4)
    assert tracked_finalization_ready(runtime.storage, layout, 3)
