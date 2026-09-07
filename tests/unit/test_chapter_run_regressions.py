"""Regression coverage for chapter auto-run and memory handoff edges."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from novel_forge.cli.chapter_runner import AutoChapterRunner
from novel_forge.core.exceptions import ConsistencyViolationError, RecoveryTarget
from novel_forge.desktop.pages.chapter_studio.data import ChapterStudioDataMixin
from novel_forge.memory.integration import MemoryContext
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.artifact_manifest import ArtifactManifest
from novel_forge.pipeline.finalization_manifest import chapter_finalization_artifact
from novel_forge.pipeline.long.loop import _check_and_compensate_memory_gap
from novel_forge.workspace.contracts import (
    DecisionCheckpoint,
    DecisionOption,
    PrepareChapterRequest,
)
from novel_forge.workspace.sessions.chapter_session_handlers import _index_finalized_chapter_memory


class _MemoryContext:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def finalize_chapter_memory(
        self,
        *,
        chapter_number: int,
        text: str,
        creative_report_text: str,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "chapter_number": chapter_number,
                "text": text,
                "creative_report_text": creative_report_text,
            }
        )
        return {"saved": True, "tasks_ok": ["summary"], "tasks_failed": []}

    def get_memory_status_for_ui(self) -> dict[str, Any]:
        return {}


class _FailingMemoryContext:
    async def finalize_chapter_memory(self, **_: Any) -> dict[str, Any]:
        raise RuntimeError("memory backend offline")


@pytest.mark.asyncio
async def test_memory_gap_compensation_uses_project_layout_chapter_path(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    project_id = "memory_gap"
    layout = ProjectLayout(storage.ensure_project_dir(project_id))
    storage.save_text(layout.chapter_path(1), "第一章正文")
    storage.save_json(layout.creative_report_path(1), {"summary": "第一章创作摘要"})
    layout.states_dir.mkdir(parents=True, exist_ok=True)
    marker_path = layout.states_dir / "chapter_1_memory_pending.json"
    marker_path.write_text(
        json.dumps({"chapter": 1, "compensated": False}, ensure_ascii=False),
        encoding="utf-8",
    )

    memory_ctx = _MemoryContext()
    events: list[tuple[str, dict[str, Any]]] = []
    runner = SimpleNamespace(
        memory_context=memory_ctx,
        _storage=storage,
        _on_step=lambda step, payload: events.append((step, payload)),
    )

    await _check_and_compensate_memory_gap(runner, project_id, 2)

    assert memory_ctx.calls == [
        {
            "chapter_number": 1,
            "text": "第一章正文",
            "creative_report_text": "第一章创作摘要",
        }
    ]
    assert not marker_path.exists()
    assert events[0][0] == "memory_gap_compensated"


@pytest.mark.asyncio
async def test_session_memory_failure_records_pending_manifest(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    project_id = "session_memory"
    layout = ProjectLayout(storage.ensure_project_dir(project_id))
    events: list[tuple[str, dict[str, Any]]] = []

    await _index_finalized_chapter_memory(
        memory_ctx=_FailingMemoryContext(),
        storage=storage,
        project_id=project_id,
        chapter_number=1,
        chapter_result=SimpleNamespace(
            text="第一章正文",
            creative_report=SimpleNamespace(summary="第一章创作摘要"),
        ),
        on_step=lambda step, payload: events.append((step, payload)),
    )

    record = ArtifactManifest(storage, layout).get(chapter_finalization_artifact(1, "memory"))
    assert record is not None
    assert record.status == "needs_repair"
    assert "memory backend offline" in record.metadata["error"]
    assert not (layout.states_dir / "chapter_1_memory_pending.json").exists()
    assert events == [
        (
            "memory_update_failed",
            {"chapter": 1, "error": "memory backend offline"},
        )
    ]


def test_chapter_studio_upstream_ready_checks_canon_current(tmp_path) -> None:
    project_id = "canon_ready"
    project_dir = tmp_path / project_id
    (project_dir / "chapters").mkdir(parents=True)
    (project_dir / "canon").mkdir(parents=True)
    (project_dir / "chapters" / "chapter_001.md").write_text("正文", encoding="utf-8")
    (project_dir / "canon" / "canon_current.json").write_text("{}", encoding="utf-8")

    page = object.__new__(ChapterStudioDataMixin)
    page._workspace = SimpleNamespace(storage_root=tmp_path)
    page._studio = SimpleNamespace(project_id=project_id, chapter_number=1)

    assert page._is_upstream_chapter_ready() is True


def test_auto_chapter_runner_deduplicates_restored_progress(tmp_path) -> None:
    project_id = "auto_progress"
    layout = ProjectLayout(tmp_path / project_id)
    layout.states_dir.mkdir(parents=True, exist_ok=True)
    layout.auto_run_progress_path.write_text(
        json.dumps(
            {
                "project_id": project_id,
                "completed_chapters": [1, "1", 2, 2, "bad"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    runner = AutoChapterRunner(
        project_id=project_id,
        start_chapter=1,
        end_chapter=3,
        force=False,
        mock=True,
        verbose=False,
        ai_judge_apply_mode="accept",
        run_logger=None,
    )

    runner._load_auto_run_progress(layout, storage=None)
    runner._mark_completed_chapter(2)
    runner._mark_completed_chapter(3)

    assert runner.completed_chapters == [1, 2, 3]


def test_auto_chapter_runner_filters_restored_progress_to_requested_range(tmp_path) -> None:
    project_id = "auto_progress_range"
    layout = ProjectLayout(tmp_path / project_id)
    layout.states_dir.mkdir(parents=True, exist_ok=True)
    layout.auto_run_progress_path.write_text(
        json.dumps(
            {
                "project_id": project_id,
                "completed_chapters": [1, 2, 5, 6],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    runner = AutoChapterRunner(
        project_id=project_id,
        start_chapter=3,
        end_chapter=5,
        force=False,
        mock=True,
        verbose=False,
        ai_judge_apply_mode="accept",
        run_logger=None,
    )

    runner._load_auto_run_progress(layout, storage=None)

    assert runner.completed_chapters == [5]


def test_auto_chapter_runner_refuses_to_skip_when_canon_lags(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    project_id = "canon_lag"
    layout = ProjectLayout(storage.ensure_project_dir(project_id))
    layout.ensure_dirs()
    storage.save_text(layout.chapter_path(2), "第二章正文")
    storage.save_json(layout.chapter_exit_state_path(2), {"chapter_number": 2})
    storage.save_json(layout.creative_report_path(2), {"summary": "第二章摘要"})
    storage.save_json(layout.canon_dir / "canon_current.json", {"current_chapter": 1})

    runner = AutoChapterRunner(
        project_id=project_id,
        start_chapter=2,
        end_chapter=2,
        force=False,
        mock=True,
        verbose=False,
        ai_judge_apply_mode="accept",
        run_logger=None,
    )

    can_skip, state = runner._existing_chapter_resume_state(
        layout,
        storage,
        2,
        stale_cutoff=None,
    )

    assert can_skip is False
    assert state["reason"] == "canon_watermark_behind"

    storage.save_json(layout.canon_dir / "canon_current.json", {"current_chapter": 2})

    can_skip, state = runner._existing_chapter_resume_state(
        layout,
        storage,
        2,
        stale_cutoff=None,
    )

    assert can_skip is True
    assert state["reason"] == "complete"


@pytest.mark.asyncio
async def test_prepare_plan_checkpoint_compensates_memory_gap_before_planning(
    monkeypatch,
    tmp_path,
) -> None:
    import novel_forge.workspace.sessions.chapter_session_handlers as handlers

    storage = FileSystemStorage(tmp_path)
    project_id = "session_gap_compensate"
    layout = ProjectLayout(storage.ensure_project_dir(project_id))
    layout.ensure_dirs()
    calls: list[str] = []

    context = SimpleNamespace(
        config=SimpleNamespace(writing_mode="whole_chapter"),
        on_step=lambda step, payload: calls.append(step),
    )
    runner = SimpleNamespace(
        create_execution_context=lambda: context,
        _storage=storage,
        _retriever=object(),
    )

    @asynccontextmanager
    async def _fake_chapter_runner_memory_lifecycle(*args: Any, **kwargs: Any):
        calls.append("create_runner")
        yield runner, object()

    async def _fake_compensate_memory_gap(
        runner_obj: Any,
        project_id_arg: str,
        chapter_number: int,
    ) -> None:
        assert runner_obj is context  # now receives ChapterExecutionContext
        assert project_id_arg == project_id
        assert chapter_number == 2
        calls.append("compensate_memory_gap")

    async def _fake_prepare_long_project(*args: Any, **kwargs: Any) -> Any:
        calls.append("prepare_long_project")
        return SimpleNamespace(
            layout=layout,
            canon_state=SimpleNamespace(current_chapter=1),
            chapter_outline=SimpleNamespace(chapter_number=2),
        )

    async def _fake_prepare_chapter_plan(*args: Any, **kwargs: Any) -> Any:
        calls.append("prepare_chapter_plan")
        return SimpleNamespace(
            packet=object(),
            bridge=object(),
            plan=object(),
            scene_plan_validation_report=None,
        )

    def _fake_build_plan_checkpoint(*args: Any, **kwargs: Any) -> DecisionCheckpoint:
        return DecisionCheckpoint(
            checkpoint_id="plan-002-test",
            checkpoint_type="plan_checkpoint",
            options=[
                DecisionOption(
                    option_id="write_now",
                    label="写作",
                    is_recommended=True,
                )
            ],
        )

    monkeypatch.setattr(
        handlers,
        "_chapter_runner_memory_lifecycle",
        _fake_chapter_runner_memory_lifecycle,
    )
    monkeypatch.setattr(handlers, "compensate_memory_gap", _fake_compensate_memory_gap)
    monkeypatch.setattr(handlers, "prepare_long_project", _fake_prepare_long_project)
    monkeypatch.setattr(handlers, "prepare_chapter_plan", _fake_prepare_chapter_plan)
    monkeypatch.setattr(handlers, "build_plan_checkpoint", _fake_build_plan_checkpoint)

    runtime = SimpleNamespace(
        storage=storage,
    )

    result = await handlers.prepare_plan_checkpoint(
        runtime,
        PrepareChapterRequest(project_id=project_id, chapter_number=2),
    )

    assert result.status == "needs_decision"
    assert calls.index("prepare_long_project") < calls.index("compensate_memory_gap")
    assert calls.index("compensate_memory_gap") < calls.index("prepare_chapter_plan")
    assert "release_memory_context" not in calls


@pytest.mark.asyncio
async def test_prepare_plan_checkpoint_replans_after_upstream_consistency_violation(
    monkeypatch,
    tmp_path,
) -> None:
    import novel_forge.workspace.sessions.chapter_session_handlers as handlers

    storage = FileSystemStorage(tmp_path)
    project_id = "session_prepare_consistency_replan"
    layout = ProjectLayout(storage.ensure_project_dir(project_id))
    layout.ensure_dirs()
    calls: list[str] = []

    context = SimpleNamespace(
        config=SimpleNamespace(writing_mode="whole_chapter"),
        settings=SimpleNamespace(long_max_consistency_replans=2, auto_introduce_characters=False),
        on_step=lambda step, payload: calls.append(step),
    )
    runner = SimpleNamespace(
        create_execution_context=lambda: context,
        _storage=storage,
        _retriever=object(),
    )
    bundle = SimpleNamespace(
        layout=layout,
        canon_state=SimpleNamespace(current_chapter=1),
        chapter_outline=SimpleNamespace(chapter_number=2, goal="推进第二章"),
    )

    @asynccontextmanager
    async def _fake_chapter_runner_memory_lifecycle(*args: Any, **kwargs: Any):
        yield runner, object()

    async def _fake_compensate_memory_gap(*args: Any, **kwargs: Any) -> None:
        return None

    prepare_calls = 0

    async def _fake_prepare_chapter_plan(*args: Any, **kwargs: Any) -> Any:
        nonlocal prepare_calls
        prepare_calls += 1
        if prepare_calls == 1:
            raise ConsistencyViolationError(
                ["Bridge 未解释移动过程"],
                replan_target=RecoveryTarget.PLAN,
            )
        assert "Bridge 未解释移动过程" in bundle.chapter_outline.notes
        return SimpleNamespace(
            packet=object(),
            bridge=object(),
            plan=object(),
            scene_plan_validation_report=None,
        )

    def _fake_build_plan_checkpoint(*args: Any, **kwargs: Any) -> DecisionCheckpoint:
        return DecisionCheckpoint(
            checkpoint_id="plan-002-replanned",
            checkpoint_type="plan_checkpoint",
            prompt="章节方案已备好。",
            options=[
                DecisionOption(
                    option_id="write_now",
                    label="写作",
                    is_recommended=True,
                )
            ],
        )

    monkeypatch.setattr(
        handlers,
        "_chapter_runner_memory_lifecycle",
        _fake_chapter_runner_memory_lifecycle,
    )
    monkeypatch.setattr(handlers, "compensate_memory_gap", _fake_compensate_memory_gap)

    async def _fake_prepare_long_project(*args: Any, **kwargs: Any) -> Any:
        return bundle

    monkeypatch.setattr(handlers, "prepare_long_project", _fake_prepare_long_project)
    monkeypatch.setattr(handlers, "prepare_chapter_plan", _fake_prepare_chapter_plan)
    monkeypatch.setattr(handlers, "build_plan_checkpoint", _fake_build_plan_checkpoint)

    result = await handlers.prepare_plan_checkpoint(
        SimpleNamespace(storage=storage),
        PrepareChapterRequest(project_id=project_id, chapter_number=2),
    )

    assert result.status == "needs_decision"
    assert prepare_calls == 2
    assert "consistency_replan" in calls
    assert result.checkpoint is not None
    assert result.checkpoint.checkpoint_id == "plan-002-replanned"
    assert "桥接/方案未通过上游一致性校验" in result.checkpoint.prompt
    saved_session = storage.load_json(layout.chapter_session_path(2))
    assert "Bridge 未解释移动过程" in saved_session["notes"]


@pytest.mark.asyncio
async def test_prepare_plan_checkpoint_never_replans_manual_violation(
    monkeypatch,
    tmp_path,
) -> None:
    import novel_forge.workspace.sessions.chapter_session_handlers as handlers

    storage = FileSystemStorage(tmp_path)
    project_id = "session_prepare_manual_failure"
    layout = ProjectLayout(storage.ensure_project_dir(project_id))
    layout.ensure_dirs()
    calls: list[str] = []

    context = SimpleNamespace(
        config=SimpleNamespace(writing_mode="whole_chapter"),
        settings=SimpleNamespace(long_max_consistency_replans=2, auto_introduce_characters=False),
        on_step=lambda step, _payload: calls.append(step),
    )
    runner = SimpleNamespace(
        create_execution_context=lambda: context,
        _storage=storage,
        _retriever=object(),
    )
    bundle = SimpleNamespace(
        layout=layout,
        canon_state=SimpleNamespace(current_chapter=1),
        chapter_outline=SimpleNamespace(chapter_number=2, goal="推进第二章"),
    )

    @asynccontextmanager
    async def _fake_chapter_runner_memory_lifecycle(*_args: Any, **_kwargs: Any):
        yield runner, object()

    async def _fake_prepare_long_project(*_args: Any, **_kwargs: Any) -> Any:
        return bundle

    async def _fake_compensate_memory_gap(*_args: Any, **_kwargs: Any) -> None:
        return None

    prepare_calls = 0

    async def _raise_manual_failure(*_args: Any, **_kwargs: Any) -> None:
        nonlocal prepare_calls
        prepare_calls += 1
        raise ConsistencyViolationError(
            ["chapter_outline.pov_character 缺失"],
            violation_kind="input_contract",
            failed_stage="planning_input",
            replan_target=RecoveryTarget.MANUAL,
        )

    monkeypatch.setattr(
        handlers,
        "_chapter_runner_memory_lifecycle",
        _fake_chapter_runner_memory_lifecycle,
    )
    monkeypatch.setattr(handlers, "compensate_memory_gap", _fake_compensate_memory_gap)
    monkeypatch.setattr(handlers, "prepare_long_project", _fake_prepare_long_project)
    monkeypatch.setattr(handlers, "prepare_chapter_plan", _raise_manual_failure)

    with pytest.raises(ConsistencyViolationError) as raised:
        await handlers.prepare_plan_checkpoint(
            SimpleNamespace(storage=storage),
            PrepareChapterRequest(project_id=project_id, chapter_number=2),
        )

    assert raised.value.replan_target is RecoveryTarget.MANUAL
    assert prepare_calls == 1
    assert "consistency_replan" not in calls


def test_memory_context_reads_exit_state_from_canon_current(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    project_id = "memory_canon"
    project_dir = storage.ensure_project_dir(project_id)
    canon_dir = project_dir / "canon"
    canon_dir.mkdir(parents=True)
    storage.save_json(
        canon_dir / "canon_current.json",
        {
            "chapter_exit_states": {
                "2": {
                    "chapter_number": 2,
                    "time_marker": "夜",
                    "location": "码头",
                    "pov": "林远",
                    "active_goals": ["找人"],
                    "open_questions": ["黑衣人是谁？"],
                }
            }
        },
    )

    ctx = MemoryContext(_project_id=project_id, _storage=storage)

    assert ctx.get_chapter_exit_state(2) == {
        "chapter_number": 2,
        "time_marker": "夜",
        "location": "码头",
        "pov": "林远",
        "active_goals": ["找人"],
        "open_questions": ["黑衣人是谁？"],
        "must_carry_forward": [],
        "character_end_states": {},
    }
