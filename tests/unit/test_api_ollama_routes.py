"""Transport tests for the versioned Engine Ollama boundary."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

import novel_forge.api.routes.engine as engine_routes
import novel_forge.app_service.ollama_management as ollama_management
from novel_forge.api.app import create_app
from novel_forge.api.deps import get_job_service
from novel_forge.app_service.contracts import JobCommand, JobKind, JobRecord, JobScope
from novel_forge.app_service.engine_views import (
    OllamaCapabilitiesView,
    OllamaConfiguredRolesView,
    OllamaManagerView,
    OllamaModelView,
    OllamaRoutingImpactView,
    OllamaRuntimeStatusView,
    OllamaSidecarView,
    OllamaStorageView,
)


class _JobService:
    def __init__(self) -> None:
        self.submitted: list[JobCommand] = []

    def list(self) -> list[JobRecord]:
        return []

    def submit(self, command: JobCommand) -> JobRecord:
        self.submitted.append(command)
        return JobRecord(
            job_id="ollama-task",
            kind=command.kind,
            label=command.label,
            scope=command.scope,
        )


def _view() -> OllamaManagerView:
    return OllamaManagerView(
        revision="revision-ollama-123456",
        endpoint_scope="engine_host",
        ownership="engine_owned",
        runtime=OllamaRuntimeStatusView(status="healthy", version="0.12.0", detail="正常"),
        sidecar=OllamaSidecarView(
            enabled=True,
            auto_start=True,
            prefer_local=True,
            binary_available=True,
        ),
        storage=OllamaStorageView(scope="engine_managed", display_label="Engine 托管目录"),
        capabilities=OllamaCapabilitiesView(
            can_ensure=True,
            can_restart=True,
            can_stop=True,
            can_pull=True,
            can_delete=True,
            can_configure_paths=True,
        ),
        models=[OllamaModelView(name="qwen3:8b", size=1, roles=["generation"])],
        configured_roles=OllamaConfiguredRolesView(generation_model="qwen3:8b"),
        routing_impact_by_model={
            "qwen3:8b": OllamaRoutingImpactView(
                profile_ids=["ollama:qwen3:8b"],
                confirmation_token="confirmation-token-123456",
            )
        },
    )


def test_ollama_read_and_pull_use_engine_system_job(monkeypatch) -> None:
    service = _JobService()
    app = create_app()
    app.dependency_overrides[get_job_service] = lambda: service
    monkeypatch.setattr(engine_routes, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(ollama_management, "ollama_manager_view", lambda _settings, _jobs: _view())

    with TestClient(app) as client:
        snapshot = client.get("/api/v1/engine/ollama")
        assert snapshot.status_code == 200
        assert "/" not in snapshot.json()["storage"]["displayLabel"]

        response = client.post(
            "/api/v1/engine/commands/pull-ollama-model",
            json={
                "kind": "pull_ollama_model",
                "expectedRevision": "revision-ollama-123456",
                "idempotencyKey": "pull-qwen3-123456",
                "model": "qwen3:8b",
            },
        )
    assert response.status_code == 200, response.text
    assert response.json()["taskId"] == "ollama-task"
    assert service.submitted[0].kind == JobKind.OLLAMA_PULL_MODEL
    assert service.submitted[0].scope == JobScope.SYSTEM
    assert service.submitted[0].project_id == ""


def test_ollama_delete_requires_explicit_cascade(monkeypatch) -> None:
    service = _JobService()
    app = create_app()
    app.dependency_overrides[get_job_service] = lambda: service
    monkeypatch.setattr(engine_routes, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(ollama_management, "ollama_manager_view", lambda _settings, _jobs: _view())

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/engine/commands/delete-ollama-model",
            json={
                "kind": "delete_ollama_model",
                "expectedRevision": "revision-ollama-123456",
                "idempotencyKey": "delete-qwen3-123456",
                "model": "qwen3:8b",
                "confirmationToken": "confirmation-token-123456",
                "cascadeConfiguration": False,
            },
        )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "rejected"
    assert service.submitted == []
