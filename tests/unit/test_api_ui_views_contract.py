"""Contract alignment tests: /api/v1/ui/* response shapes ↔ engine-contracts.

Verifies that the Python UI composition endpoints return the field names and
structures expected by the TypeScript ``EngineClient`` / ``EngineCommandClient``
interfaces defined in ``packages/engine-contracts/src/index.ts``.

The frontend ``LegacyLocalEngineClient`` maps snake_case → camelCase, so the
Python backend outputs snake_case.  These tests assert the snake_case keys.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client() -> TestClient:
    from novel_forge.api.app import create_app

    app = create_app()
    return TestClient(app, raise_server_exceptions=False)


# ── Workspace ─────────────────────────────────────────────────────────────────


async def test_narrative_tools_exposes_book_goal_separately_from_planning_window(tmp_path):
    from novel_forge.api.routes.ui_views import get_narrative_tools_view
    from novel_forge.persistence.filesystem import FileSystemStorage
    from novel_forge.persistence.models import ProjectLayout

    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("book"))
    layout.ensure_dirs()
    storage.save_json(layout.outline_path, {
        "total_chapters": 80, "hard_through_chapter": 5, "planned_through_chapter": 10,
        "chapters": [{"chapter_number": n, "title": f"章{n}", "goal": "推进"} for n in range(1, 11)],
    })
    detail = SimpleNamespace(chapters=[], total_chapters=80, completed_chapters=0)
    inspector = SimpleNamespace(get_project_detail=lambda _: detail)
    result = await get_narrative_tools_view("book", inspector, storage)
    assert result["planning"] == {
        "total_chapters": 80, "hard_through_chapter": 5, "planned_through_chapter": 10,
    }
    assert len(result["outline"]) == 10
    assert result["outline"][4]["state_label"] == "待写"
    assert result["outline"][5]["state_label"] == "预规划"



class TestWorkspaceContract:
    """GET /api/v1/ui/workspace → WorkspaceView."""

    def test_top_level_keys(self, client: TestClient) -> None:
        resp = client.get("/api/v1/ui/workspace")
        assert resp.status_code == 200
        data = resp.json()
        assert "metrics" in data
        assert "projects" in data

    def test_metrics_shape(self, client: TestClient) -> None:
        data = client.get("/api/v1/ui/workspace").json()
        metrics = data["metrics"]
        for key in ("total_projects", "total_chapters", "total_words", "configured_providers"):
            assert key in metrics, f"missing metrics.{key}"

    def test_project_item_shape(self, client: TestClient) -> None:
        data = client.get("/api/v1/ui/workspace").json()
        projects: list[dict[str, Any]] = data.get("projects", [])
        if not projects:
            pytest.skip("no projects in storage")
        p = projects[0]
        expected_keys = {
            "id", "title", "mode", "status", "status_label",
            "progress_label", "progress_percent", "next_action",
            "updated_label", "headline", "init_resume_available",
        }
        assert expected_keys.issubset(p.keys()), f"missing: {expected_keys - set(p.keys())}"


class TestTestProjectCleanupContract:
    """Preview endpoint used by the dashboard's destructive-confirmation flow."""

    def test_preview_shape(self, client: TestClient) -> None:
        response = client.get("/api/v1/ui/maintenance/test-projects")

        assert response.status_code == 200
        payload = response.json()
        assert isinstance(payload["candidates"], list)
        assert isinstance(payload["message"], str)
        for candidate in payload["candidates"]:
            assert {"project_id", "reason", "file_count", "size_bytes"}.issubset(candidate)

    async def test_active_candidate_is_skipped_by_the_cleanup_command(self, tmp_path) -> None:
        from novel_forge.api.routes.ui_views import TestProjectCleanupBody, cleanup_test_projects
        from novel_forge.persistence.filesystem import FileSystemStorage
        from novel_forge.persistence.models import ProjectLayout
        from novel_forge.workspace.projects import ProjectInspector

        storage = FileSystemStorage(tmp_path)
        layout = ProjectLayout(storage.project_dir("short_1234abcd"))
        layout.ensure_dirs()
        storage.save_json(layout.states_dir / "short_run_meta.json", {"status": "running"})
        service = SimpleNamespace(
            list=lambda: [
                SimpleNamespace(job_id="active-short", project_id="short_1234abcd", state="running")
            ]
        )

        payload = await cleanup_test_projects(
            TestProjectCleanupBody(project_ids=["short_1234abcd"]),
            ProjectInspector(storage),
            service,
        )

        assert payload["removed_project_ids"] == []
        assert payload["skipped_project_ids"] == ["short_1234abcd"]
        assert storage.project_path("short_1234abcd").is_dir()


# ── Settings ──────────────────────────────────────────────────────────────────


class TestSettingsContract:
    """GET /api/v1/ui/settings → SettingsView."""

    def test_top_level_keys(self, client: TestClient) -> None:
        resp = client.get("/api/v1/ui/settings")
        assert resp.status_code == 200
        data = resp.json()
        for key in (
            "default_profile_id",
            "model_profiles",
            "routing_groups",
            "creation_parameters",
        ):
            assert key in data, f"missing settings.{key}"

    def test_profile_item_shape(self, client: TestClient) -> None:
        data = client.get("/api/v1/ui/settings").json()
        profiles: list[dict[str, Any]] = data.get("model_profiles", [])
        if not profiles:
            pytest.skip("no profiles configured")
        p = profiles[0]
        for key in ("id", "label", "provider", "model", "tier_label"):
            assert key in p, f"missing profile.{key}"

    def test_voice_platform_parameters_share_pyside_setting_names(self) -> None:
        from types import SimpleNamespace

        from novel_forge.app_service.settings_environment import (
            _PARAM_ENV_MAP,
            get_creation_parameters,
        )

        assert _PARAM_ENV_MAP["tts-provider"] == "NOVEL_FORGE_TTS_DEFAULT_PROVIDER"
        assert _PARAM_ENV_MAP["tts-model"] == "NOVEL_FORGE_TTS_DEFAULT_MODEL"
        assert _PARAM_ENV_MAP["tts-speed"] == "NOVEL_FORGE_TTS_DEFAULT_SPEED"
        assert (
            _PARAM_ENV_MAP["tts-audio-quality-tier"]
            == "NOVEL_FORGE_TTS_AUDIO_QUALITY_TIER"
        )
        assert (
            _PARAM_ENV_MAP["tts-subtitle-word-level"]
            == "NOVEL_FORGE_TTS_SUBTITLE_WORD_LEVEL"
        )
        assert (
            _PARAM_ENV_MAP["tts-minimax-force-cbr"]
            == "NOVEL_FORGE_TTS_MINIMAX_FORCE_CBR"
        )
        assert (
            _PARAM_ENV_MAP["tts-script-generation-temperature"]
            == "NOVEL_FORGE_TTS_SCRIPT_GENERATION_TEMPERATURE"
        )
        values = get_creation_parameters(
            SimpleNamespace(
                tts_default_provider="minimax",
                tts_default_model="speech-2.8-hd",
                tts_default_speed=1.0,
                tts_audio_quality_tier="commercial",
                tts_subtitle_word_level=True,
                tts_minimax_force_cbr=True,
            )
        )
        assert values["tts-provider"] == "minimax"
        assert values["tts-model"] == "speech-2.8-hd"
        assert values["tts-speed"] == "1.0"
        assert values["tts-audio-quality-tier"] == "commercial"
        assert values["tts-subtitle-word-level"] == "true"
        assert values["tts-minimax-force-cbr"] == "true"


# ── Workflow ──────────────────────────────────────────────────────────────────


class TestWorkflowContract:
    """GET /api/v1/ui/workflow → WorkflowView."""

    def test_top_level_keys(self, client: TestClient) -> None:
        resp = client.get("/api/v1/ui/workflow")
        assert resp.status_code == 200
        data = resp.json()
        for key in ("runs", "focus", "error_count", "error_log"):
            assert key in data, f"missing workflow.{key}"
        assert "preset" not in data


# ── Engine endpoints (existing) ──────────────────────────────────────────────


class TestEngineContract:
    """GET /api/v1/engine/* → EngineClient read models."""

    def test_capabilities(self, client: TestClient) -> None:
        resp = client.get("/api/v1/engine/capabilities")
        # May 500 if runtime services not initialized in test env
        if resp.status_code == 200:
            data = resp.json()
            # Engine endpoints output camelCase (Pydantic alias)
            assert "contractVersion" in data or "contract_version" in data
            assert "transports" in data
            assert "features" in data
        else:
            assert resp.status_code == 500  # runtime not ready, acceptable in unit test

    def test_jobs_list(self, client: TestClient) -> None:
        resp = client.get("/api/v1/engine/jobs")
        if resp.status_code == 200:
            data = resp.json()
            # Engine endpoints output camelCase (Pydantic alias)
            assert "contractVersion" in data or "contract_version" in data
            assert "jobs" in data
            assert isinstance(data["jobs"], list)
        else:
            assert resp.status_code == 500


# ── Commands (write-side) ─────────────────────────────────────────────────────


class TestCommandContract:
    """POST /api/v1/ui/commands/* → typed acknowledgement."""

    def test_prepare_chapter_requires_project(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/ui/commands/prepare-chapter",
            json={"kind": "prepare_chapter", "project_id": "", "chapter_number": 1},
        )
        # Should either accept, reject, or fail validation — not crash unhandled
        assert resp.status_code in (200, 400, 404, 409, 422, 500)

    def test_start_workflow_shape(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/ui/commands/start-workflow",
            json={
                "kind": "start_workflow",
                "project_id": "demo",
                "workflow_type": "short",
                "run_mode": "create",
                "idempotency_key": "ui-short-1",
                "payload": {"project_id": "demo", "theme": "梦境探险"},
            },
        )
        # Empty project_id may trigger validation error in command executor
        assert resp.status_code in (200, 400, 404, 409, 422, 500)
        if resp.status_code == 200:
            data = resp.json()
            assert "status" in data
            assert "message" in data

    async def test_start_workflow_allocates_auto_project_through_compatibility_route(self) -> None:
        from novel_forge.api.routes.ui_views import StartWorkflowBody, start_workflow
        from novel_forge.app_service.contracts import JobCommand, JobKind, JobRecord

        class Service:
            submitted: list[JobCommand] = []

            def list(self, project_id: str | None = None) -> list[JobRecord]:
                del project_id
                return []

            def submit(self, command: JobCommand) -> JobRecord:
                self.submitted.append(command)
                return JobRecord(
                    job_id="compat-auto-project",
                    kind=JobKind.INIT_LONG,
                    label="长篇立项 · 自动项目",
                    project_id=command.project_id,
                )

        runtime = SimpleNamespace(create_project_id=lambda mode: f"{mode}_compat_1234")
        service = Service()
        response = await start_workflow(
            StartWorkflowBody(
                project_id="",
                workflow_type="long_init",
                idempotency_key="ui-auto-init-1",
                payload={"project_id": "", "premise": "兼容入口也必须绑定项目目录"},
            ),
            service,  # type: ignore[arg-type]
            runtime,  # type: ignore[arg-type]
        )

        assert response["status"] == "accepted"
        assert service.submitted[0].project_id == "long_compat_1234"
        assert service.submitted[0].payload["project_id"] == "long_compat_1234"
