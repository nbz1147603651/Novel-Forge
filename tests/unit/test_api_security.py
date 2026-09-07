"""Tests for API boundary protection middleware."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from novel_forge.api import deps
from novel_forge.api.app import create_app
from novel_forge.api.security import _is_host_allowed, _is_local_client


@pytest.fixture(autouse=True)
def clear_runtime_caches() -> None:
    deps.reload_runtime_dependencies()
    yield
    deps.reload_runtime_dependencies()


def test_health_route_remains_public_when_token_is_configured(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("NOVEL_FORGE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("NOVEL_FORGE_API_ACCESS_TOKEN", "secret-token")
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_api_routes_require_bearer_token_when_configured(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("NOVEL_FORGE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("NOVEL_FORGE_API_ACCESS_TOKEN", "secret-token")
    client = TestClient(create_app())

    missing = client.get("/api/v1/status/health")
    wrong = client.get("/api/v1/status/health", headers={"Authorization": "Bearer wrong"})
    ok = client.get(
        "/api/v1/status/health",
        headers={"Authorization": "Bearer secret-token"},
    )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert ok.status_code == 200


def test_api_routes_accept_internal_token_header(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("NOVEL_FORGE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("NOVEL_FORGE_API_ACCESS_TOKEN", "secret-token")
    client = TestClient(create_app())

    response = client.get(
        "/api/v1/status/health",
        headers={"X-Novel-Forge-Token": "secret-token"},
    )

    assert response.status_code == 200


def test_untrusted_host_header_is_rejected() -> None:
    client = TestClient(create_app())

    response = client.get("/health", headers={"Host": "evil.example"})

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_host"


def test_remote_clients_are_rejected_by_default() -> None:
    client = TestClient(create_app(), client=("198.51.100.10", 50000))

    response = client.get("/health")

    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


def test_remote_clients_can_be_enabled_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOVEL_FORGE_API_LOCAL_ONLY", "false")
    client = TestClient(create_app(), client=("198.51.100.10", 50000))

    response = client.get("/health")

    assert response.status_code == 200


def test_missing_host_is_rejected_when_trusted_hosts_are_configured() -> None:
    assert _is_host_allowed(None, "localhost") is False
    assert _is_host_allowed(None, "") is True


def test_missing_client_is_not_treated_as_local() -> None:
    assert _is_local_client(None) is False


def test_packaged_tauri_origin_is_allowed_by_default() -> None:
    client = TestClient(create_app())

    response = client.options(
        "/api/v1/engine/capabilities",
        headers={
            "Origin": "tauri://localhost",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "tauri://localhost"
