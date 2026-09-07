from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from novel_forge.api.app import create_app
from novel_forge.api.deps import get_job_service
from novel_forge.api.routes.jobs import _format_sse
from novel_forge.app_service.contracts import (
    DecisionRequest,
    JobCommand,
    JobEvent,
    JobEventType,
    JobKind,
    JobRecord,
    JobState,
)


class StubJobService:
    def __init__(self) -> None:
        self.record = JobRecord(
            job_id="job-api",
            kind=JobKind.RUN_CHAPTER,
            label="api job",
            project_id="demo",
        )

    def submit(self, command: JobCommand) -> JobRecord:
        self.record.kind = command.kind
        self.record.project_id = command.project_id or str(command.payload.get("project_id") or "")
        return self.record

    def list(self, project_id: str | None = None) -> list[JobRecord]:
        if project_id and self.record.project_id != project_id:
            return []
        return [self.record]

    def get(self, job_id: str) -> JobRecord | None:
        return self.record if job_id == self.record.job_id else None

    def cancel(self, job_id: str, reason: str = "用户已取消") -> JobRecord:
        if job_id != self.record.job_id:
            raise KeyError(job_id)
        self.record.status = JobState.FAILED
        self.record.current_step = "cancelled"
        self.record.error = reason
        return self.record

    def resume(self, job_id: str) -> JobRecord:
        if job_id != self.record.job_id:
            raise KeyError(job_id)
        self.record.status = JobState.QUEUED
        self.record.current_step = ""
        return self.record

    def provide_decision(self, job_id: str, payload: dict[str, Any]) -> JobRecord:
        if job_id != self.record.job_id:
            raise KeyError(job_id)
        DecisionRequest.model_validate(payload)
        return self.record


def test_jobs_api_submit_get_list_cancel_and_decision() -> None:
    service = StubJobService()
    app = create_app()
    app.dependency_overrides[get_job_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/api/v1/jobs",
        json={
            "kind": "run_chapter",
            "project_id": "demo",
            "payload": {"project_id": "demo", "chapter_number": 1},
        },
    )
    assert response.status_code == 200
    assert response.json()["job_id"] == "job-api"

    assert client.get("/api/v1/jobs/job-api").json()["project_id"] == "demo"
    assert len(client.get("/api/v1/jobs", params={"project_id": "demo"}).json()) == 1

    decision = client.post(
        "/api/v1/jobs/job-api/decision",
        json={"decision_id": "d1", "choice": "accept"},
    )
    assert decision.status_code == 200

    cancelled = client.post("/api/v1/jobs/job-api/cancel", params={"reason": "stop"})
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "failed"
    assert cancelled.json()["current_step"] == "cancelled"


def test_jobs_api_sse_format_uses_event_type_and_json_payload() -> None:
    event = JobEvent(
        job_id="job-api",
        type=JobEventType.JOB_STEP,
        step="draft",
        payload={"x": 1},
    )

    rendered = _format_sse(event)

    assert rendered.startswith("event: job_step\n")
    assert '"job_id":"job-api"' in rendered
    assert rendered.endswith("\n\n")


def test_jobs_api_resumes_reconciled_intent() -> None:
    service = StubJobService()
    app = create_app()
    app.dependency_overrides[get_job_service] = lambda: service
    client = TestClient(app)

    response = client.post("/api/v1/jobs/job-api/resume")

    assert response.status_code == 200
    assert response.json()["status"] == "queued"
