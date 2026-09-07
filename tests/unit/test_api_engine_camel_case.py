"""Cross-language contract tests: verify FastAPI accepts the exact camelCase JSON
that the React LegacyLocalEngineClient sends.

These tests use the same field names and shapes as the TypeScript command
interfaces in packages/engine-contracts/src/index.ts.  They prove the
camelCase → FastAPI alias path works end-to-end without any frontend
key transformation.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from novel_forge.api.app import create_app
from novel_forge.api.deps import get_job_service, get_runtime_services
from novel_forge.app_service.contracts import JobCommand, JobRecord


class _ContractJobService:
    """Validate HTTP command shapes without workers, recovery, or model I/O."""

    def list(self, project_id: str | None = None) -> list[JobRecord]:
        return []

    def submit(self, command: JobCommand) -> JobRecord:
        return JobRecord(
            job_id=command.job_id or "contract-only",
            kind=command.kind,
            project_id=command.project_id,
            label="contract-only",
        )


@pytest.fixture()
def client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_job_service] = _ContractJobService
    app.dependency_overrides[get_runtime_services] = lambda: SimpleNamespace()
    return TestClient(app)


# ── prepare-chapter ────────────────────────────────────────────────────────────


class TestPrepareChapterCamelCase:
    def test_accepts_camel_case(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/engine/commands/prepare-chapter",
            json={
                "kind": "prepare_chapter",
                "projectId": "demo",
                "chapterNumber": 1,
                "notes": "test note",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "accepted"
        assert "taskId" in data or "task_id" in data

    def test_accepts_snake_case(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/engine/commands/prepare-chapter",
            json={
                "kind": "prepare_chapter",
                "project_id": "demo",
                "chapter_number": 2,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"

    def test_rejects_missing_project(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/engine/commands/prepare-chapter",
            json={"kind": "prepare_chapter", "chapterNumber": 1},
        )
        assert resp.status_code == 422


# ── cancel-chapter ─────────────────────────────────────────────────────────────


class TestCancelChapterCamelCase:
    def test_accepts_camel_case(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/engine/commands/cancel-chapter",
            json={
                "kind": "cancel_chapter",
                "projectId": "demo",
                "chapterNumber": 1,
            },
        )
        assert resp.status_code == 200
        # May be "rejected" if no running job, but must not be 422
        assert resp.json()["status"] in ("accepted", "rejected")


# ── start-workflow ─────────────────────────────────────────────────────────────


class TestStartWorkflowCamelCase:
    def test_accepts_camel_case(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/engine/commands/start-workflow",
            json={
                "kind": "start_workflow",
                "projectId": "demo",
                "workflowType": "short",
                "runMode": "create",
                "idempotencyKey": "camel-short-1",
                "payload": {
                    "projectId": "demo",
                    "theme": "梦境探险",
                    "genre": "悬疑",
                    "segmentTriggerWords": 5000,
                    "maxEditRounds": 3,
                },
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"

    def test_rejects_invalid_workflow_type(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/engine/commands/start-workflow",
            json={
                "kind": "start_workflow",
                "projectId": "demo",
                "workflowType": "invalid_type",
                "runMode": "create",
                "idempotencyKey": "camel-invalid",
                "payload": {"projectId": "demo", "theme": "梦境探险"},
            },
        )
        assert resp.status_code == 422


# ── synthesize-voice ───────────────────────────────────────────────────────────


class TestSynthesizeVoiceCamelCase:
    def test_accepts_camel_case(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/engine/commands/synthesize-voice",
            json={
                "kind": "synthesize_voice",
                "projectId": "demo",
                "chapterNumber": 3,
                "segmentIds": ["seg-1", "seg-2"],
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"

    def test_accepts_without_segments(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/engine/commands/synthesize-voice",
            json={
                "kind": "synthesize_voice",
                "projectId": "demo",
                "chapterNumber": 1,
            },
        )
        assert resp.status_code == 200


# ── export-audio ───────────────────────────────────────────────────────────────


class TestExportAudioCamelCase:
    def test_accepts_camel_case_book_scope(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/engine/commands/export-audio",
            json={
                "kind": "export_audio",
                "projectId": "demo",
                "scope": "book",
                "format": "zip",
                "includeSubtitles": False,
            },
        )
        assert resp.status_code == 200
        # camelCase keys must be accepted by the contract layer; the business
        # outcome depends on whether the project has deliverable audio.
        assert resp.json()["status"] in ("accepted", "rejected")

    def test_accepts_camel_case_chapter_scope(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/engine/commands/export-audio",
            json={
                "kind": "export_audio",
                "projectId": "demo",
                "scope": "chapter",
                "chapterNumber": 1,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] in ("accepted", "rejected")


# ── dynamic dict keys must NOT be transformed ─────────────────────────────────


class TestLegacyPresetValuesRejected:
    """The removed loose presetValues map must fail instead of dropping fields."""

    def test_preset_values_are_rejected(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/engine/commands/start-workflow",
            json={
                "kind": "start_workflow",
                "projectId": "demo",
                "workflowType": "short",
                "runMode": "create",
                "idempotencyKey": "legacy-map",
                "presetValues": {
                    "theme": "梦境探险",
                    "DRAFT_CHAPTER": "openai:gpt-4o",
                    "genre": "科幻",
                },
            },
        )
        assert resp.status_code == 422
