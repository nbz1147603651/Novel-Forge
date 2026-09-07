"""Integration-level tests for control-plane hooks in real execution boundaries."""

from __future__ import annotations

from pathlib import Path

from novel_forge.common.constants import TaskType
from novel_forge.control_plane.context import bind_execution_context
from novel_forge.control_plane.factory import get_control_plane_store, reset_control_plane_store
from novel_forge.control_plane.harness import get_enforcement_mode, set_enforcement_mode
from novel_forge.control_plane.health import get_global_health_registry
from novel_forge.control_plane.schemas import RunAttemptDTO, WorkUnitDTO
from novel_forge.core.config import Settings
from novel_forge.gateway.factory import ModelRouterBuilder
from novel_forge.gateway.types import ModelRequest
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.services.context.source_artifacts import persist_stage_artifact


def test_persisted_stage_artifact_is_recorded_in_active_work_unit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Normal file persistence should mirror a completed stage, never replace it."""

    settings = Settings(
        storage_root=str(tmp_path),
        runtime_control_enabled=True,
        runtime_control_enforcement_mode="enforce",
    )
    import novel_forge.core.config as config_module

    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    reset_control_plane_store()
    try:
        store = get_control_plane_store(settings)
        assert store is not None
        work_unit = WorkUnitDTO(id="wu_stage", kind="run_chapter", project_id="project")
        store.sync.create_work_unit(work_unit)
        attempt = RunAttemptDTO(id="wu_stage_a1", work_unit_id=work_unit.id)
        store.sync.create_run_attempt(attempt)

        storage = FileSystemStorage(tmp_path)
        layout = ProjectLayout(storage.ensure_project_dir("project"))
        with bind_execution_context(
            work_unit_id=work_unit.id,
            run_attempt_id=attempt.id,
            project_id="project",
        ):
            artifact = persist_stage_artifact(
                storage=storage,
                layout=layout,
                project_id="project",
                chapter_number=1,
                artifact_type="plan",
                payload={"scene_count": 2},
            )

        stages = store.sync.list_stage_executions(attempt.id)
        assert len(stages) == 1
        stage = stages[0]
        assert stage.stage_name == "plan"
        assert stage.state.value == "done"
        assert stage.output_artifact_hash
        assert get_enforcement_mode() == "enforce"

        recorded = store.sync.find_artifact_by_sha256(stage.output_artifact_hash)
        assert recorded is not None
        assert recorded.created_by_stage_execution_id == stage.id
        assert Path(recorded.content_path).is_file()
        assert artifact.payload == {"scene_count": 2}
    finally:
        reset_control_plane_store()
        set_enforcement_mode("advisory")


def test_factory_reset_disposes_then_rebuilds_store(tmp_path: Path) -> None:
    """Changing runtime roots must not leave the old daemon store reusable."""

    settings = Settings(storage_root=str(tmp_path), runtime_control_enabled=True)
    reset_control_plane_store()
    try:
        first = get_control_plane_store(settings)
        assert first is not None
        reset_control_plane_store()

        second = get_control_plane_store(settings)
        assert second is not None
        assert second is not first
        second.sync.create_work_unit(WorkUnitDTO(id="rebuilt", kind="run_chapter"))
        assert second.sync.get_work_unit("rebuilt") is not None
    finally:
        reset_control_plane_store()


async def test_router_builder_updates_live_control_plane_health() -> None:
    """A real router outcome updates the registry used by capacity scheduling."""

    router = ModelRouterBuilder(Settings(runtime_control_enabled=True)).build(mock=True)
    response = await router.route(
        ModelRequest(
            task_type=TaskType.DRAFT_CHAPTER,
            messages=[{"role": "user", "content": "写一段测试文本"}],
            max_tokens=64,
        )
    )

    assert response.content
    entry = get_global_health_registry().get_or_create("mock", TaskType.DRAFT_CHAPTER.value)
    assert entry.last_success_at
