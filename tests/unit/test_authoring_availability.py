from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from novel_forge.app_service.contracts import JobCommand, JobRecord, JobState
from novel_forge.app_service.job_service import JobService
from novel_forge.core.authoring import AuthoringPolicy
from novel_forge.persistence.authoring_store import (
    AuthoringDeniedError,
    AuthoringStore,
    story_input_version,
)
from novel_forge.persistence.filesystem import (
    FileSystemStorage,
    atomic_write_json,
    atomic_write_text,
)
from tests.unit.test_app_service_jobs import FastExecutor, StubRuntime


@pytest.fixture
def book(tmp_path):
    root = FileSystemStorage(tmp_path).ensure_project_dir("book")
    atomic_write_json(root / "spec.json", {"theme": "作者的故事"})
    atomic_write_text(root / "chapters/chapter_001.md", "作者的正文")
    store = AuthoringStore(root)
    policy = store.set_policy(
        AuthoringPolicy(mode="authorized_auto", end_chapter=10), expected_version=0
    )
    store.start(expected_version=policy.version, input_version=story_input_version(root))
    return store


def test_disabled_state_blocks_all_new_actions_but_not_pause_and_keeps_costs(book):
    cost = book.directory / "cost-evidence.json"
    atomic_write_json(cost, {"paid": 3})
    old = book.policy()
    book.disable()
    for action in ("generate", "archive", "publish_planning", "revise", "discuss"):
        with pytest.raises(AuthoringDeniedError, match="停用"):
            book.require(action, 1, explicit=True)
    with pytest.raises(AuthoringDeniedError, match="停用"):
        book.set_policy(AuthoringPolicy(), expected_version=book.policy().version)
    with pytest.raises(AuthoringDeniedError, match="停用"):
        book.start(
            expected_version=book.policy().version, input_version=story_input_version(book.root)
        )
    book.require("pause", 1, explicit=True)
    assert book.policy().stopped and book.policy().version > old.version
    book.enable(
        expected_version=book.policy().version, input_version=story_input_version(book.root)
    )
    assert not book.disabled() and book.policy().stopped
    assert '"paid": 3' in cost.read_text()
    assert (book.root / "chapters/chapter_001.md").read_text() == "作者的正文"


def test_disable_first_barrier_survives_policy_write_failure(book, monkeypatch):
    from novel_forge.persistence import authoring_store

    write = authoring_store.atomic_write_json

    def fail_policy(path, data):
        if path == book.policy_path:
            raise OSError("policy disk failure")
        return write(path, data)

    monkeypatch.setattr(authoring_store, "atomic_write_json", fail_policy)
    with pytest.raises(OSError):
        book.disable()
    assert book.disabled()
    with pytest.raises(AuthoringDeniedError):
        book.require("generate", 1)


class FinishingRequest(FastExecutor):
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.cancelled = threading.Event()

    async def run(self, prepared, runtime, on_step):
        self.started.set()
        while not self.release.is_set():
            try:
                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                self.cancelled.set()
        return {"status": "completed"}


def test_disable_keeps_stopping_until_request_finishes_and_reenable_never_runs(book):
    executor = FinishingRequest()
    service = JobService(
        storage_root=book.root.parent,
        load_persisted_history=False,
        executor=executor,
        runtime_factory=lambda _: StubRuntime(),
    )
    try:
        job = service.submit(
            JobCommand(kind="prepare_chapter", project_id="book", payload={"project_id": "book"})
        )
        assert executor.started.wait(2)
        service.pause_authoring("book", disable=True)
        assert executor.cancelled.wait(2)
        assert service.authoring_activity("book")[0] == "stopping"
        with pytest.raises(ValueError, match="安全停止"):
            service.enable_authoring(
                "book",
                expected_version=book.policy().version,
                input_version=story_input_version(book.root),
            )
        with pytest.raises(AuthoringDeniedError, match="停用"):
            service.submit(
                JobCommand(
                    kind="prepare_chapter", project_id="book", payload={"project_id": "book"}
                )
            )
        executor.release.set()
        deadline = time.monotonic() + 3
        while service.authoring_activity("book")[0] == "stopping" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert service.authoring_activity("book")[0] == "idle"
        assert service.get(job.job_id).status == JobState.FAILED
        assert service.get(job.job_id).current_step == "cancelled"
        service.enable_authoring(
            "book",
            expected_version=book.policy().version,
            input_version=story_input_version(book.root),
        )
        assert book.policy().stopped and not book.disabled()
        assert len(service.list(project_id="book")) == 1
    finally:
        executor.release.set()
        service.shutdown(wait_s=3)


def test_authoring_activity_keeps_live_worker_blocking_after_durable_pause(book):
    service = JobService(
        storage_root=book.root.parent,
        load_persisted_history=False,
        executor=FastExecutor(),
        runtime_factory=lambda _: StubRuntime(),
    )
    record = JobRecord(
        job_id="live-paused-view",
        kind="prepare_chapter",
        label="第 1 章方案",
        project_id="book",
        status=JobState.PAUSED,
    )
    worker = SimpleNamespace(record=record)
    with service._lock:
        service._jobs[record.job_id] = record
        service._workers[record.job_id] = worker
    try:
        assert service.authoring_activity("book") == ("running", record.job_id)
    finally:
        with service._lock:
            service._workers.pop(record.job_id, None)
            service._jobs.pop(record.job_id, None)
        service.shutdown(wait_s=3)


def test_restart_retains_disabled_barrier_and_cannot_submit(book):
    book.disable()
    service = JobService(
        storage_root=book.root.parent,
        executor=FastExecutor(),
        runtime_factory=lambda _: StubRuntime(),
    )
    try:
        assert book.disabled() and book.policy().stopped
        with pytest.raises(AuthoringDeniedError, match="停用"):
            service.submit(JobCommand(kind="prepare_chapter", project_id="book"))
        assert not service.list(project_id="book")
    finally:
        service.shutdown(wait_s=0)


def test_unreleased_http_commands_fail_closed_and_read_pause_remain_available(book, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from novel_forge.api.deps import get_job_service, get_storage
    from novel_forge.api.routes.authoring import router
    from novel_forge.app_service import engine_views

    capabilities = engine_views.engine_capabilities()
    capabilities.features["authoring_coauthor"] = False
    monkeypatch.setattr(engine_views, "engine_capabilities", lambda: capabilities)

    service = JobService(storage_root=book.root.parent, load_persisted_history=False)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_storage] = lambda: FileSystemStorage(book.root.parent)
    app.dependency_overrides[get_job_service] = lambda: service
    try:
        with TestClient(app) as client:
            assert client.get("/authoring/book").status_code == 200
            response = client.post(
                "/authoring/book/start",
                json={
                    "expected_version": book.policy().version,
                    "input_version": story_input_version(book.root),
                },
            )
            assert response.status_code == 409 and "尚未开放" in response.text
            response = client.post("/authoring/book/availability", json={"enabled": False})
            assert response.status_code == 200 and response.json()["disabled"] is True
            assert book.disabled()
            assert client.post("/authoring/book/pause").status_code == 200
            assert not service.list(project_id="book")
    finally:
        service.shutdown(wait_s=0)
