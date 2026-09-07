"""Integration tests for the performance API router.

Verifies that the slimmed-down performance router endpoints work correctly
when mounted in the main FastAPI app.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from novel_forge.api.app import create_app


@pytest.fixture()
def client() -> TestClient:
    """Create a TestClient with the full app (lifespan managed)."""
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_health_endpoint_returns_200(client: TestClient) -> None:
    """GET /api/performance/health returns 200."""
    resp = client.get("/api/performance/health")
    assert resp.status_code == 200


def test_health_status_is_healthy(client: TestClient) -> None:
    """GET /api/performance/health returns status='healthy' when processor is initialized."""
    resp = client.get("/api/performance/health")
    body = resp.json()
    assert body["status"] == "healthy"


def test_health_has_processor_initialized_field(client: TestClient) -> None:
    """Health response includes processor_initialized boolean."""
    resp = client.get("/api/performance/health")
    body = resp.json()
    assert "processor_initialized" in body
    assert isinstance(body["processor_initialized"], bool)


def test_health_uses_application_start_time_and_version() -> None:
    app = create_app()
    app.state.start_time -= 5.0

    with TestClient(app) as client:
        resp = client.get("/api/performance/health")

    body = resp.json()
    assert body["uptime"] >= 5.0
    assert body["version"] == app.version


def test_metrics_endpoint_returns_200(client: TestClient) -> None:
    """GET /api/performance/metrics returns 200."""
    resp = client.get("/api/performance/metrics")
    assert resp.status_code == 200


def test_metrics_response_shape(client: TestClient) -> None:
    """Metrics response has expected top-level keys."""
    resp = client.get("/api/performance/metrics")
    body = resp.json()
    assert "timestamp" in body
    assert "metrics" in body
    assert "cache_stats" in body
    assert "task_stats" in body


def test_cache_stats_endpoint_returns_200(client: TestClient) -> None:
    """GET /api/performance/cache/stats returns 200."""
    resp = client.get("/api/performance/cache/stats")
    assert resp.status_code == 200


def test_deleted_endpoints_not_found(client: TestClient) -> None:
    """Old mock task endpoints should no longer exist."""
    assert client.post("/api/performance/tasks/submit", json={}).status_code == 404
    assert client.get("/api/performance/tasks/stats").status_code == 404
    assert client.post("/api/performance/optimize/initialize").status_code == 404
    assert client.post("/api/performance/optimize/shutdown").status_code == 404
