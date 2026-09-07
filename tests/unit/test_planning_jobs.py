from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from novel_forge.app_service.contracts import JobCommand, JobRecord, JobState
from novel_forge.core.schemas.outline import ChapterOutline, StoryOutline
from novel_forge.persistence.filesystem import FileSystemStorage, atomic_write_json
from novel_forge.persistence.planning_revision import PlanningRevision
from novel_forge.workspace.contracts import AdvancePlanningHorizonRequest
from novel_forge.workspace.execution_planning_horizon import ensure_chapter_planning
from novel_forge.workspace.planning_horizon import PlanningHorizonAdvance
from novel_forge.workspace.planning_jobs import (
    PlanningTaskPending,
    execute_planning_horizon_job,
    planning_job_view,
    request_planning_horizon,
)


class Queue:
    def __init__(self):
        self.jobs = {}
        self.commands = []

    def get(self, job_id):
        return self.jobs.get(job_id)

    def submit_planning_horizon(self, state):
        from novel_forge.app_service.planning_jobs import submit_planning_job

        return submit_planning_job(self, state)

    def submit(self, command: JobCommand):
        self.commands.append(command)
        record = JobRecord(
            job_id=command.job_id,
            kind=command.kind,
            project_id=command.project_id,
            label="planning",
        )
        self.jobs[record.job_id] = record
        return record


@pytest.fixture
def runtime(tmp_path):
    storage = FileSystemStorage(tmp_path)
    root = storage.ensure_project_dir("book")
    outline = StoryOutline(
        total_chapters=100,
        hard_through_chapter=5,
        planned_through_chapter=10,
        chapters=[
            ChapterOutline(chapter_number=n, title=str(n), goal="推进", beats_summary=["事件"])
            for n in range(1, 11)
        ],
    )
    atomic_write_json(root / "outline.json", outline.model_dump(mode="json"))
    return SimpleNamespace(storage=storage, planning_job_service=Queue()), root


async def test_durable_request_reuses_task_and_does_not_block_chapter_worker(runtime):
    rt, root = runtime
    state = await request_planning_horizon(rt, project_id="book", current_chapter=3)
    assert state["target_chapter"] == 10
    assert planning_job_view(root)["job_id"] == state["job_id"]
    await request_planning_horizon(rt, project_id="book", current_chapter=3)
    with pytest.raises(PlanningTaskPending) as pending:
        await ensure_chapter_planning(rt, project_id="book", chapter_number=6)
    assert pending.value.payload["checkpoint"]["waiting_task_id"] == state["job_id"]
    assert len(rt.planning_job_service.commands) == 1


async def test_hundred_chapters_waterline_restarts_and_stops_at_goal(runtime, monkeypatch):
    rt, root = runtime
    calls = []

    async def advance(runtime, *, target_chapter, **kwargs):
        data = runtime.storage.load_json(root / "outline.json")
        hard = data["hard_through_chapter"]
        data.update(hard_through_chapter=target_chapter, planned_through_chapter=target_chapter)
        atomic_write_json(root / "outline.json", data)
        calls.append(target_chapter)
        return PlanningHorizonAdvance(
            hard, target_chapter, target_chapter, tuple(range(hard + 1, target_chapter + 1)), ()
        )

    monkeypatch.setattr(
        "novel_forge.workspace.execution_planning_horizon.advance_planning_horizon", advance
    )
    for chapter in range(1, 101):
        state = await request_planning_horizon(rt, project_id="book", current_chapter=chapter)
        if state is not None:
            # A fresh queue simulates loss of process-local task objects.
            if chapter in {3, 8, 13}:
                rt.planning_job_service = Queue()
                state = await request_planning_horizon(
                    rt, project_id="book", current_chapter=chapter
                )
            command = rt.planning_job_service.commands[-1]
            await execute_planning_horizon_job(rt, AdvancePlanningHorizonRequest(**command.payload))
            # Replayed completion must not invoke the generator twice.
            await execute_planning_horizon_job(rt, AdvancePlanningHorizonRequest(**command.payload))
    assert calls == list(range(10, 101, 5))
    assert rt.storage.load_json(root / "outline.json")["total_chapters"] == 100


async def test_failed_horizon_needs_explicit_retry_and_preserves_outline(runtime, monkeypatch):
    rt, root = runtime
    original = (root / "outline.json").read_bytes()
    failure = AsyncMock(side_effect=RuntimeError("offline failure"))
    monkeypatch.setattr(
        "novel_forge.workspace.execution_planning_horizon.advance_planning_horizon", failure
    )
    state = await request_planning_horizon(rt, project_id="book", current_chapter=3)
    with pytest.raises(RuntimeError, match="offline failure"):
        await execute_planning_horizon_job(
            rt, AdvancePlanningHorizonRequest(**rt.planning_job_service.commands[-1].payload)
        )
    with pytest.raises(RuntimeError, match="offline failure"):
        await request_planning_horizon(rt, project_id="book", current_chapter=3)
    assert failure.await_count == 1
    assert (root / "outline.json").read_bytes() == original
    retry = await request_planning_horizon(rt, project_id="book", current_chapter=3, retry=True)
    assert retry["job_id"] != state["job_id"]


def test_candidate_reload_excludes_chat_and_queue_but_checks_author_inputs(runtime):
    _, root = runtime
    revision = PlanningRevision(root, "book")
    atomic_write_json(revision.project / "outline.json", {"candidate": True})
    atomic_write_json(root / ".authoring" / "chat.json", {"message": "解释一下"})
    atomic_write_json(root / "states" / "task_flow_history.json", {})
    atomic_write_json(root / "states" / "control_plane_intents" / "task.json", {})
    restored = PlanningRevision.load(root, "book", revision.revision_id)
    assert restored.publish() == ["outline.json"]
    other = PlanningRevision(root, "book")
    atomic_write_json(root / "authoring_policy.json", {"version": 9})
    with pytest.raises(ValueError, match="inputs"):
        PlanningRevision.load(root, "book", other.revision_id).publish()


@pytest.mark.parametrize("committed", [False, True])
async def test_validated_candidate_survives_lost_return_without_repeat_generation(
    runtime, monkeypatch, committed
):
    from novel_forge.persistence.authoring_store import content_version
    from novel_forge.workspace.planning_jobs import record_planning_candidate

    rt, root = runtime
    calls = []

    async def advance(runtime, *, planning_request_id, **kwargs):
        calls.append(planning_request_id)
        revision = PlanningRevision(root, "book")
        data = runtime.storage.load_json(root / "outline.json")
        data["hard_through_chapter"] = 10
        atomic_write_json(revision.project / "outline.json", data)
        record_planning_candidate(
            root,
            planning_request_id,
            {
                "revision_id": revision.revision_id,
                "candidate_version": content_version(revision.changes()),
                "published": False,
            },
        )
        if committed:
            revision.publish()
        raise RuntimeError("lost return")

    monkeypatch.setattr(
        "novel_forge.workspace.execution_planning_horizon.advance_planning_horizon", advance
    )
    await request_planning_horizon(rt, project_id="book", current_chapter=3)
    request = AdvancePlanningHorizonRequest(**rt.planning_job_service.commands[-1].payload)
    if committed:
        await execute_planning_horizon_job(rt, request)
    else:
        with pytest.raises(RuntimeError, match="lost return"):
            await execute_planning_horizon_job(rt, request)
    state = planning_job_view(root)
    assert state["status"] == ("published" if committed else "candidate")
    assert state["result"]["revision_id"]
    await execute_planning_horizon_job(rt, request)
    assert len(calls) == 1
    assert rt.storage.load_json(root / "outline.json")["hard_through_chapter"] == (
        10 if committed else 5
    )


def test_historical_publication_receipt_never_overwrites_new_inputs(runtime):
    from novel_forge.persistence.planning_revision import planning_publication_receipt

    rt, root = runtime
    first = PlanningRevision(root, "book")
    atomic_write_json(first.project / "outline.json", {"revision": 1})
    first.publish()
    second = PlanningRevision(root, "book")
    atomic_write_json(second.project / "outline.json", {"revision": 2})
    second.publish()
    assert planning_publication_receipt(root, first.revision_id)["changed_artifacts"] == [
        "outline.json"
    ]
    first.publish()  # Receipt only, not a replay against the new author's inputs.
    assert rt.storage.load_json(root / "outline.json") == {"revision": 2}


def test_pause_waits_for_legal_atomic_planning_commit(runtime, monkeypatch):
    import threading

    from novel_forge.persistence import planning_revision as module
    from novel_forge.persistence.authoring_store import AuthoringStore

    rt, root = runtime
    revision = PlanningRevision(root, "book")
    atomic_write_json(revision.project / "outline.json", {"committed": True})
    started, stopped = threading.Event(), threading.Event()

    def pause():
        started.set()
        AuthoringStore(root).stop()
        stopped.set()

    thread = threading.Thread(target=pause)
    write = module.atomic_write_text

    def observe_commit(path, text):
        if path == root / "outline.json":
            thread.start()
            assert started.wait(1)
            assert not stopped.wait(0.05)
        write(path, text)

    monkeypatch.setattr(module, "atomic_write_text", observe_commit)
    revision.publish()
    thread.join(2)
    assert stopped.is_set()
    assert rt.storage.load_json(root / "outline.json") == {"committed": True}


def test_autorun_waits_for_planning_without_retry_budget_or_new_scheduler(tmp_path):
    from novel_forge.app_service.book_autorun import (
        BookAutorunCoordinator,
        BookAutorunState,
        BookAutorunStatus,
    )

    submitted = []
    coordinator = BookAutorunCoordinator(
        storage_root=tmp_path,
        submit_command=lambda cmd: (
            submitted.append(cmd)
            or JobRecord(job_id="next", kind=cmd.kind, project_id="book", label="next")
        ),
        resume_job=lambda *_: None,
        get_job=lambda _: None,
        chapter_ready=lambda *_: False,
    )
    state = BookAutorunState(
        project_id="book", active_job_id="chapter", end_chapter=100, total_chapters=100
    )
    coordinator._states["book"] = state
    coordinator.on_job_paused(
        JobRecord(
            job_id="chapter",
            label="chapter",
            kind="prepare_chapter",
            project_id="book",
            status=JobState.PAUSED,
            result={
                "checkpoint": {"checkpoint_type": "planning_wait", "waiting_task_id": "horizon"}
            },
        )
    )
    assert state.status == BookAutorunStatus.RETRY_WAIT
    assert not submitted and not state.failure_attempts
    coordinator.on_planning_finished(
        JobRecord(
            job_id="horizon",
            label="horizon",
            kind="planning_horizon",
            project_id="book",
            status=JobState.SUCCEEDED,
            result={"status": "published", "result": {"hard_through_chapter": 10}},
        )
    )
    assert len(submitted) == 1


def test_real_single_slot_queue_recovers_planning_dependency(runtime, monkeypatch):
    import asyncio
    import time

    from novel_forge.app_service.job_service import JobService
    from novel_forge.app_service.workspace_commands import PreparedCommand

    rt, root = runtime
    completed = []

    class Executor:
        def prepare(self, command):
            return PreparedCommand(
                kind=command.kind,
                request=command.payload,
                project_id="book",
                label="test",
                command_name="test",
                metadata={},
            )

        async def run(self, prepared, runtime, on_step):
            if prepared.kind.value == "prepare_chapter":
                await ensure_chapter_planning(runtime, project_id="book", chapter_number=6)
                return {"status": "completed"}
            request = AdvancePlanningHorizonRequest(**prepared.request)
            result = await execute_planning_horizon_job(runtime, request)
            completed.append(result.result["status"])
            return result.result

    async def advance(*args, **kwargs):
        await asyncio.sleep(0.01)
        return PlanningHorizonAdvance(5, 10, 10, tuple(range(6, 11)), ())

    monkeypatch.setattr(
        "novel_forge.workspace.execution_planning_horizon.advance_planning_horizon", advance
    )
    rt.shutdown = AsyncMock()
    service = JobService(
        storage_root=root.parent,
        executor=Executor(),
        runtime_factory=lambda _: rt,
        load_persisted_history=False,
        max_concurrent_jobs=1,
    )
    try:
        job = service.submit(
            JobCommand(kind="prepare_chapter", project_id="book", payload={"chapter_number": 6})
        )
        deadline = time.monotonic() + 5
        while not completed and time.monotonic() < deadline:
            time.sleep(0.02)
        assert completed == ["published"]
        assert service.get(job.job_id).status == JobState.PAUSED
        assert list((root / "states" / "control_plane_intents").glob("horizon-*.json"))
    finally:
        service.shutdown(wait_s=2)
