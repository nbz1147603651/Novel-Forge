"""Tests for status API routes."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from novel_forge.api import deps
from novel_forge.api.app import create_app
from novel_forge.core.project_state import ProjectOperation, ProjectStateMachine
from novel_forge.core.schemas.outline import ChapterOutline, StoryOutline
from novel_forge.core.schemas.spec import StorySpec
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.story_kernel.schemas import StoryKernel


@pytest.fixture(autouse=True)
def clear_dep_caches() -> None:
    deps.reload_runtime_dependencies()
    yield
    deps.reload_runtime_dependencies()


def test_status_health_route_exposes_active_and_observed_config_versions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    monkeypatch.setenv("NOVEL_FORGE_STORAGE_ROOT", str(first_root))
    client = TestClient(create_app())

    first_response = client.get("/api/v1/status/health")
    assert first_response.status_code == 200
    first_payload = first_response.json()
    assert first_payload["reload_required"] is False
    assert first_payload["api_cache_mode"] == "singleton_auto_refresh"
    assert first_payload["local_model_resources"]["budget"] in {"light", "medium", "high"}
    assert first_payload["local_model_resources"]["accelerator"]["capacity"] >= 1
    assert first_payload["active_runtime"]["storage_root"] == str(first_root.resolve())
    assert first_payload["observed_config"]["storage_root"] == str(first_root.resolve())

    monkeypatch.setenv("NOVEL_FORGE_STORAGE_ROOT", str(second_root))
    second_response = client.get("/api/v1/status/health")

    assert second_response.status_code == 200
    payload = second_response.json()
    assert payload["status"] == "ok"
    assert isinstance(payload["adapters"], list)
    assert payload["reload_required"] is True
    assert payload["active_runtime"]["storage_root"] == str(first_root.resolve())
    assert payload["observed_config"]["storage_root"] == str(second_root.resolve())
    assert (
        payload["active_runtime"]["config_version"]
        != payload["observed_config"]["config_version"]
    )


def test_status_reload_route_reports_before_and_after_versions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "before"
    second_root = tmp_path / "after"
    monkeypatch.setenv("NOVEL_FORGE_STORAGE_ROOT", str(first_root))
    client = TestClient(create_app())
    client.get("/api/v1/status/health")

    monkeypatch.setenv("NOVEL_FORGE_STORAGE_ROOT", str(second_root))
    response = client.post("/api/v1/status/reload")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "reloaded"
    assert payload["changed"] is True
    assert payload["reload_required"] is False
    assert payload["previous_runtime"]["storage_root"] == str(first_root.resolve())
    assert payload["active_runtime"]["storage_root"] == str(second_root.resolve())
    assert payload["observed_config"]["storage_root"] == str(second_root.resolve())
    assert (
        payload["previous_runtime"]["config_version"]
        != payload["active_runtime"]["config_version"]
    )


def test_project_status_returns_404_for_missing_project() -> None:
    client = TestClient(create_app())

    response = client.get("/api/v1/status/project/not-found")

    assert response.status_code == 404
    assert response.json()["detail"] == "project not found"


def test_project_status_exposes_project_state_fields(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("NOVEL_FORGE_STORAGE_ROOT", str(tmp_path))
    deps.reload_runtime_dependencies()
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.project_dir("stateful_demo"))
    layout.ensure_dirs()
    storage.save_json(
        layout.spec_path,
        StorySpec(
            title="状态项目",
            theme="旧案重启",
            genre="mystery",
            tone="cold",
            length_target=60000,
        ).model_dump(mode="json"),
    )
    storage.save_json(
        layout.outline_path,
        StoryOutline(
            total_chapters=10,
            synopsis="追查十年前悬案。",
            volume_mode=False,
            chapters=[
                ChapterOutline(chapter_number=1, title="档案室", goal="引出旧案"),
            ],
        ).model_dump(mode="json"),
    )
    storage.save_json(
        layout.canon_dir / "canon_current.json",
        StoryKernel(project_id="stateful_demo", current_chapter=0).model_dump(mode="json"),
    )

    machine = ProjectStateMachine("stateful_demo", project_dir=layout.root)
    machine.execute(ProjectOperation.INIT)
    machine.execute(ProjectOperation.COMPLETE_INIT)

    deps.reload_runtime_dependencies()
    client = TestClient(create_app())
    response = client.get("/api/v1/status/project/stateful_demo")

    assert response.status_code == 200
    payload = response.json()
    assert payload["project_state"] == "outline_ready"
    assert payload["project_state_label"] == "待创作"
    assert payload["project_state_source"] == "state_file"
    assert "start_writing" in payload["allowed_operations"]
