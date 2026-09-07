from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from novel_forge.api.app import create_app
from novel_forge.api.deps import (
    get_engine_query_service,
    get_job_service,
    get_runtime_services,
    get_storage,
)
from novel_forge.app_service.contracts import (
    JobCommand,
    JobKind,
    JobRecord,
    JobState,
    JobStepEvent,
)
from novel_forge.app_service.engine_novel import EngineNovelStudioView
from novel_forge.app_service.engine_queries import (
    InvalidChapterNumberError,
    ProjectNotLongError,
)
from novel_forge.app_service.engine_voice import EngineVoiceStudioView
from novel_forge.app_service.humanize_library import load_humanize_library
from novel_forge.common.constants import TaskType
from novel_forge.common.runtime_identity import EngineRestartRequiredError
from novel_forge.gateway.types import ModelResponse
from novel_forge.memory.humanize_library_store import HumanizeLibrary
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.story_kernel.store import StoryKernelStore
from novel_forge.workspace.execution_result import ExecutionResult


def _async_regeneration_result(value):
    async def _fake(*_args, **_kwargs):
        return value

    return _fake


def _seed_story_kernel(layout: ProjectLayout, *, current_chapter: int) -> None:
    async def _seed() -> None:
        store = StoryKernelStore(layout.story_kernel_db_path)
        try:
            await store.init_db()
            kernel = await store.create_kernel(layout.root.name)
            await store.save_snapshot(0)
            kernel.current_chapter = current_chapter
            await store.save_kernel(kernel)
        finally:
            await store.close()

    asyncio.run(_seed())


class StubEngineJobService:
    def __init__(self) -> None:
        self.record = JobRecord(
            job_id="job-engine",
            kind=JobKind.RUN_CHAPTER,
            label="第 6 章",
            project_id="demo",
            status=JobState.RUNNING,
            current_step="draft_chapter",
            current_step_payload={"progress_percent": 25},
            events=[
                JobStepEvent(
                    step="llm_stream_delta",
                    payload={
                        "stream_id": "s1",
                        "segments": [{"kind": "content", "text": "正在生成"}],
                    },
                )
            ],
        )
        self.submitted: list[JobCommand] = []
        self.cancelled: list[str] = []
        self.cancel_reasons: list[str] = []
        self.resumed: list[str] = []
        self.project_resumes: list[dict[str, object]] = []
        self.cleared: list[str] = []
        self.autorun_state: object | None = None
        self.autorun_cleanup_resets: list[tuple[str, int]] = []

    def list(self, project_id: str | None = None) -> list[JobRecord]:
        if project_id and project_id != self.record.project_id:
            return []
        return [self.record]

    def get(self, job_id: str) -> JobRecord | None:
        return self.record if job_id == self.record.job_id else None

    def runtime_activity_counts(self) -> tuple[int, int]:
        return (1, 0)

    def submit(self, command: JobCommand) -> JobRecord:
        self.submitted.append(command)
        return JobRecord(
            job_id=f"job-submitted-{len(self.submitted)}",
            kind=command.kind,
            label="submitted",
            project_id=command.project_id,
        )

    def cancel(self, job_id: str, reason: str = "") -> JobRecord:
        self.cancelled.append(job_id)
        self.cancel_reasons.append(reason)
        return self.record

    def resume(self, job_id: str) -> JobRecord:
        self.resumed.append(job_id)
        if job_id != self.record.job_id:
            raise KeyError(job_id)
        return self.record

    def resume_project(
        self,
        project_id: str,
        kind: JobKind | str,
        **kwargs,
    ) -> JobRecord:
        self.project_resumes.append({"project_id": project_id, "kind": str(kind), **kwargs})
        return JobRecord(
            job_id="job-init-resumed",
            kind=JobKind.INIT_LONG,
            label="resumed",
            project_id=project_id,
            status=JobState.QUEUED,
        )

    def clear_inactive_history(self, job_ids: set[str] | None = None) -> list[str]:
        self.cleared = sorted(job_ids or {self.record.job_id})
        return self.cleared

    def book_autorun_state(self, project_id: str) -> object | None:
        return self.autorun_state if project_id == self.record.project_id else None

    def reset_book_autorun_after_cleanup(
        self,
        project_id: str,
        *,
        from_chapter: int,
    ) -> bool:
        self.autorun_cleanup_resets.append((project_id, from_chapter))
        return self.autorun_state is not None

    def acknowledge_error_log_entries(self, entry_ids: set[str]) -> list[str]:
        self.acknowledged_errors = sorted(entry_ids)
        return self.acknowledged_errors

    def reopen_error_log_entries(self, entry_ids: set[str]) -> list[str]:
        self.reopened_errors = sorted(entry_ids)
        return self.reopened_errors

    def clear_closed_error_log_entries(self, entry_ids: set[str]) -> SimpleNamespace:
        self.cleared_errors = sorted(entry_ids)
        return SimpleNamespace(
            cleared_task_ids=[self.record.job_id],
            cleared_error_entry_ids=self.cleared_errors,
            retained_pending_error_entry_ids=[],
        )


class StubEngineQueryService:
    def get_novel_studio(
        self,
        project_id: str,
        *,
        chapter_number: int | None = None,
    ) -> EngineNovelStudioView:
        if project_id == "missing":
            raise KeyError(project_id)
        return EngineNovelStudioView(
            project_id=project_id,
            project_title="青瓦梦匙",
            next_chapter=chapter_number or 6,
            total_chapters=20,
        )

    def get_voice_studio(
        self,
        project_id: str,
        *,
        chapter_number: int | None = None,
    ) -> EngineVoiceStudioView:
        if project_id == "missing":
            raise KeyError(project_id)
        return EngineVoiceStudioView(
            project_id=project_id,
            project_title="青瓦梦匙",
            provider_label="mock",
            configured_model_label="mock-voice",
            chapter_number=chapter_number or 6,
        )


def test_workflow_ai_history_is_durable_per_preset(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    app = create_app()
    app.dependency_overrides[get_storage] = lambda: storage
    client = TestClient(app)

    saved = client.put(
        "/api/v1/engine/workflow/presets/short/晨雾",
        json={"payload": {"theme": "记忆被删改的海港"}},
    )
    history = client.put(
        "/api/v1/engine/workflow/presets/short/晨雾/history",
        json={
            "operation": "polish_applied",
            "data": {"theme": "记忆被删改的雾港"},
            "hint": "强化悬疑密度",
            "selectedSuggestions": ["加强开篇钩子"],
            "focusFields": ["theme"],
            "metadata": {"acceptedFields": ["theme"], "creativeProfile": {"style": "balanced"}},
        },
    )
    listed = client.get("/api/v1/engine/workflow/presets/short/晨雾/history")

    assert saved.status_code == 200
    assert history.status_code == 200
    assert listed.status_code == 200
    entry = listed.json()["entries"][0]
    assert entry["operation"] == "polish_applied"
    assert entry["data"]["theme"] == "记忆被删改的雾港"
    assert entry["selectedSuggestions"] == ["加强开篇钩子"]
    assert entry["metadata"]["acceptedFields"] == ["theme"]

    cleared = client.delete("/api/v1/engine/workflow/presets/short/晨雾/history")

    assert cleared.status_code == 200
    assert client.get("/api/v1/engine/workflow/presets/short/晨雾/history").json()["entries"] == []


def _client(
    service: StubEngineJobService,
    runtime: SimpleNamespace | None = None,
) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_job_service] = lambda: service
    app.dependency_overrides[get_engine_query_service] = StubEngineQueryService
    app.dependency_overrides[get_runtime_services] = lambda: (
        runtime or SimpleNamespace(create_project_id=lambda mode: f"{mode}_generated")
    )
    return TestClient(app)


def _narrative_subplot_client(tmp_path) -> tuple[TestClient, FileSystemStorage, ProjectLayout]:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("demo"))
    storage.save_json(
        layout.blueprint_path,
        {
            "total_chapters": 24,
            "character_arcs": [
                {
                    "character": "周砚",
                    "arc_summary": "追查旧案让周砚从旁观者转为主动揭露者。",
                    "milestones": [
                        {
                            "chapter_start": 2,
                            "chapter_end": 6,
                            "description": "发现旧案证据被替换。",
                        },
                        {
                            "chapter_start": 8,
                            "chapter_end": 15,
                            "description": "调查证据来源并遭到追踪。",
                        },
                    ],
                }
            ],
            "subplot_plan": [],
        },
    )
    app = create_app()
    app.dependency_overrides[get_storage] = lambda: storage
    return TestClient(app), storage, layout


def _narrative_character_client(tmp_path) -> tuple[TestClient, FileSystemStorage, ProjectLayout]:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("demo"))
    storage.save_json(
        layout.characters_path,
        {
            "characters": [
                {
                    "character_id": "char-lin",
                    "name": "林舟",
                    "role": "protagonist",
                    "gender": "女",
                    "relationships": {},
                },
                {
                    "character_id": "char-shen",
                    "name": "沈砚",
                    "role": "deuteragonist",
                    "gender": "男",
                    "relationships": {},
                },
            ]
        },
    )
    app = create_app()
    app.dependency_overrides[get_storage] = lambda: storage
    return TestClient(app), storage, layout


def test_engine_subplot_commands_persist_plan_with_revision_conflicts(tmp_path) -> None:
    client, storage, layout = _narrative_subplot_client(tmp_path)
    save = client.post(
        "/api/v1/engine/commands/save-narrative-subplots",
        json={
            "projectId": "demo",
            "subplots": [
                {
                    "name": "旧案证人线",
                    "description": "证人决定是否公开记录，迫使主线重新选择行动时机。",
                    "involvedChapters": [3, 8, 16],
                    "chapterEvents": [
                        {
                            "chapterNumber": 3,
                            "event": "证人交出被篡改的账本。",
                            "weaveNotes": "引入新证据",
                            "dependsOn": [],
                        }
                    ],
                    "weaveLinks": [
                        {
                            "sourceType": "subplot",
                            "sourceRef": "账本",
                            "targetSubplot": "主线",
                            "triggerChapter": 16,
                            "linkType": "reveal_key",
                            "description": "账本证据改变主线的公开策略。",
                        }
                    ],
                    "priority": "normal",
                    "resolutionChapter": 16,
                    "resolutionTarget": "证人公开作证",
                    "resolutionType": "reveal",
                }
            ],
        },
    )

    assert save.status_code == 200
    payload = save.json()
    assert payload["status"] == "saved"
    assert payload["blueprintRevision"]
    saved_plan = storage.load_json(layout.blueprint_path)["subplot_plan"]
    assert saved_plan[0]["chapter_events"][0]["event"] == "证人交出被篡改的账本。"
    assert saved_plan[0]["weave_links"][0]["link_type"] == "reveal_key"

    conflict = client.post(
        "/api/v1/engine/commands/save-narrative-subplots",
        json={"projectId": "demo", "expectedRevision": "obsolete", "subplots": []},
    )
    assert conflict.status_code == 200
    assert conflict.json()["status"] == "conflict"


def test_engine_character_commands_persist_the_shared_artifact_graph(tmp_path) -> None:
    client, storage, layout = _narrative_character_client(tmp_path)
    revision = hashlib.sha256(layout.characters_path.read_bytes()).hexdigest()

    saved_character = client.post(
        "/api/v1/engine/commands/save-narrative-character",
        json={
            "projectId": "demo",
            "characterId": "char-lin",
            "expectedRevision": revision,
            "profile": {
                "personality": "克制而警觉，习惯先验证证据。",
                "socialStatus": "旧案档案员",
            },
        },
    )

    assert saved_character.status_code == 200
    character_payload = saved_character.json()
    assert character_payload["status"] == "saved"
    assert character_payload["characterRevision"] != revision
    stored_characters = storage.load_json(layout.characters_path)["characters"]
    assert stored_characters[0]["personality"] == "克制而警觉，习惯先验证证据。"
    assert stored_characters[0]["social_status"] == "旧案档案员"

    saved_relationship = client.post(
        "/api/v1/engine/commands/save-narrative-relationship",
        json={
            "projectId": "demo",
            "sourceCharacterId": "char-lin",
            "targetCharacterId": "char-shen",
            "relationType": "同盟",
            "description": "共同追查旧案，彼此提供关键掩护。",
            "expectedRevision": character_payload["characterRevision"],
        },
    )

    assert saved_relationship.status_code == 200
    relationship_payload = saved_relationship.json()
    assert relationship_payload["status"] == "saved"
    relationship_matrix = storage.load_json(
        layout.states_dir / "init_v2" / "character_relationship_matrix.json"
    )
    assert relationship_matrix["relationship_matrix"][0]["relation_type"] == "alliance"
    assert (layout.narrative_state_dir / "entity_graph.json").exists()

    conflict = client.post(
        "/api/v1/engine/commands/retire-narrative-character",
        json={
            "projectId": "demo",
            "characterId": "char-lin",
            "expectedRevision": revision,
        },
    )
    assert conflict.status_code == 200
    assert conflict.json()["status"] == "conflict"


def test_engine_humanize_commands_share_the_revision_guard(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "humanize-library.db"

    def library_factory() -> HumanizeLibrary:
        return HumanizeLibrary.from_path(database)

    monkeypatch.setattr(HumanizeLibrary, "from_default_path", staticmethod(library_factory))
    client, _, _ = _narrative_character_client(tmp_path)
    revision = load_humanize_library(library_factory=library_factory).revision

    saved = client.post(
        "/api/v1/engine/commands/save-humanize-pattern",
        json={
            "projectId": "demo",
            "expectedRevision": revision,
            "pattern": {
                "patternId": "lib_user_a1b2c3d4",
                "name": "空泛收束",
                "category": "模板结尾",
                "severity": "高",
                "keywords": ["因此", "最终"],
                "notes": "审稿标注",
                "examplePhrase": "最终，一切都归于命运。",
            },
        },
    )
    assert saved.status_code == 200
    payload = saved.json()
    assert payload["status"] == "saved"
    assert payload["pattern"]["sourceLabel"] == "用户"
    assert payload["pattern"]["severity"] == "high"

    toggled = client.post(
        "/api/v1/engine/commands/set-humanize-pattern-enabled",
        json={
            "projectId": "demo",
            "patternId": "lib_user_a1b2c3d4",
            "enabled": False,
            "expectedRevision": payload["humanizeLibraryRevision"],
        },
    )
    assert toggled.json()["status"] == "saved"
    assert toggled.json()["pattern"]["enabled"] is False

    conflict = client.post(
        "/api/v1/engine/commands/remove-humanize-pattern",
        json={
            "projectId": "demo",
            "patternId": "lib_user_a1b2c3d4",
            "expectedRevision": payload["humanizeLibraryRevision"],
        },
    )
    assert conflict.json()["status"] == "conflict"


def test_engine_converts_event_driven_character_arc_without_removing_source(tmp_path) -> None:
    client, storage, layout = _narrative_subplot_client(tmp_path)

    response = client.post(
        "/api/v1/engine/commands/convert-narrative-arcs-to-subplots",
        json={"projectId": "demo", "arcIds": ["arc-0"]},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "saved"
    blueprint = storage.load_json(layout.blueprint_path)
    assert blueprint["character_arcs"][0]["character"] == "周砚"
    converted = blueprint["subplot_plan"][0]
    assert converted["name"] == "周砚线"
    assert converted["chapter_events"][1]["event"] == "调查证据来源并遭到追踪。"


def test_engine_generates_reviewable_subplot_candidates_without_mutating_blueprint(
    tmp_path,
) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("demo"))
    storage.save_json(layout.blueprint_path, {"total_chapters": 12, "subplot_plan": []})

    class _Builder:
        context: dict[str, object] | None = None

        def build(self, task_type, context, **kwargs):
            assert task_type is TaskType.POLISH_SUBPLOT
            assert kwargs["temperature"] == 0.7
            self.context = context
            return object()

    class _Router:
        async def route(self, request):
            del request
            return SimpleNamespace(
                content='{"subplots":[{"name":"失踪账本线","description":"账本牵出新的证人。","involved_chapters":[2,8,12],"chapter_events":[{"chapter_number":2,"event":"账本现身"}],"weave_links":[],"priority":"normal","resolution_chapter":12,"resolution_target":"证人作证","resolution_type":"reveal"}]}'
            )

    builder = _Builder()
    app = create_app()
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_runtime_services] = lambda: SimpleNamespace(
        builder=builder,
        router=_Router(),
    )
    client = TestClient(app)

    response = client.post(
        "/api/v1/engine/commands/generate-narrative-subplots",
        json={"projectId": "demo", "userHint": "强化证据线", "count": 1},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "generated"
    assert payload["candidates"][0]["name"] == "失踪账本线"
    assert payload["candidates"][0]["chapterEvents"][0]["event"] == "账本现身"
    assert builder.context is not None
    assert builder.context["generation_mode"] is True
    assert storage.load_json(layout.blueprint_path)["subplot_plan"] == []


def test_engine_capabilities_and_jobs_use_versioned_camel_case_contract() -> None:
    client = _client(StubEngineJobService())

    capabilities = client.get("/api/v1/engine/capabilities")
    jobs = client.get("/api/v1/engine/jobs", params={"project_id": "demo"})

    assert capabilities.status_code == 200
    assert capabilities.json()["contractVersion"] == "1.0"
    assert capabilities.json()["features"]["engine_commands"] is True
    assert capabilities.json()["commands"]["prepare_chapter"] is True
    assert capabilities.json()["commands"]["polish_outline"] is True
    assert capabilities.json()["commands"]["repair_continuity"] is True
    assert capabilities.json()["commands"]["repair_causal"] is True
    assert capabilities.json()["commands"]["repair_issues"] is True
    assert capabilities.json()["commands"]["reevaluate_chapter"] is True
    assert capabilities.json()["commands"]["reextract_relationships"] is True
    assert capabilities.json()["commands"]["repair_motif_history"] is True
    assert capabilities.json()["commands"]["save_init_manual_repair"] is True
    assert capabilities.json()["commands"]["save_narrative_character"] is True
    assert capabilities.json()["commands"]["save_narrative_relationship"] is True
    assert capabilities.json()["commands"]["save_humanize_pattern"] is True
    assert capabilities.json()["commands"]["rebuild_memory_vectors"] is True
    assert capabilities.json()["commands"]["acknowledge_task_errors"] is True
    assert capabilities.json()["commands"]["reopen_task_errors"] is True
    assert capabilities.json()["commands"]["clear_closed_task_errors"] is True
    assert capabilities.json()["commands"]["clear_error_archive"] is True
    assert capabilities.json()["commands"]["save_settings"] is True
    assert capabilities.json()["commands"]["delete_projects"] is True
    assert capabilities.json()["commands"]["test_model_profile"] is True
    assert capabilities.json()["commands"]["generate_workflow_fields"] is True
    assert capabilities.json()["commands"]["continue_long_init"] is True
    assert capabilities.json()["commands"]["restart_long_init"] is True
    assert capabilities.json()["commands"]["resolve_speakers"] is True
    assert capabilities.json()["commands"]["assign_catalog_voice"] is True
    assert capabilities.json()["commands"]["export_audio"] is True
    assert capabilities.json()["commands"]["export_audiobook"] is True
    assert jobs.status_code == 200
    assert jobs.json()["contractVersion"] == "1.0"
    assert jobs.json()["jobs"][0]["projectId"] == "demo"
    assert jobs.json()["jobs"][0]["progressPercent"] == 25


def test_engine_error_archive_uses_app_service_storage_boundary(tmp_path) -> None:
    from novel_forge.app_service.task_flow_error_log import TaskFlowErrorLog

    storage = FileSystemStorage(tmp_path)
    record = JobRecord(
        job_id="failed-init",
        kind=JobKind.INIT_LONG,
        label="长篇立项 · 眠咒",
        project_id="sleep-spell",
        status=JobState.FAILED,
        current_step="init_story_bible",
        error="provider unavailable",
    )
    TaskFlowErrorLog(storage.root).archive_job(record)
    app = create_app()
    app.dependency_overrides[get_storage] = lambda: storage
    client = TestClient(app)

    summary = client.get("/api/v1/engine/error-archive")
    cleared = client.post(
        "/api/v1/engine/commands/clear-error-archive",
        json={"kind": "clear_error_archive"},
    )

    assert summary.status_code == 200
    assert summary.json() == {
        "entryCount": 1,
        "projectCount": 1,
        "latestTime": record.updated_at,
    }
    assert cleared.status_code == 200
    assert cleared.json()["status"] == "cleared"
    assert cleared.json()["removedProjectCount"] == 1
    assert client.get("/api/v1/engine/error-archive").json()["entryCount"] == 0


def test_engine_model_profile_probe_is_versioned_and_returns_camel_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_probe(_request):
        return type(
            "ProbeResult",
            (),
            {
                "ok": True,
                "detail": "连通 42ms",
                "latency_ms": 42,
                "supports_thinking": True,
                "supports_multi_turn": False,
            },
        )()

    monkeypatch.setattr(
        "novel_forge.api.routes.engine.probe_model_profile_request",
        fake_probe,
    )
    client = _client(StubEngineJobService())

    response = client.post(
        "/api/v1/engine/commands/test-model-profile",
        json={
            "kind": "test_model_profile",
            "id": "openai:gpt-5.6",
            "provider": "openai",
            "model": "gpt-5.6",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "detail": "连通 42ms",
        "latencyMs": 42,
        "supportsThinking": True,
        "supportsMultiTurn": False,
    }
    assert client.post("/api/v1/ui/settings/test-profile", json={}).status_code == 404


def test_engine_save_settings_is_versioned_and_returns_camel_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novel_forge.app_service.settings_save import SettingsSaveCommandResult

    async def fake_save(_command, *, reload_runtime=None):
        assert callable(reload_runtime)
        return SettingsSaveCommandResult(
            status="saved",
            persistence="persisted",
            message="已保存",
            accepted_route_ids=["draft_chapter"],
            rejected_routes=[],
            saved_at_label="12:00:00",
            runtime_reload_status="reloaded",
        )

    monkeypatch.setattr("novel_forge.api.routes.engine.save_settings_command", fake_save)
    client = _client(StubEngineJobService())

    response = client.post(
        "/api/v1/engine/commands/save-settings",
        json={
            "kind": "save_settings",
            "defaultProfileId": "ollama:local",
            "routes": {
                "draft_chapter": {
                    "primaryProfileId": "ollama:local",
                    "fallbackRoutes": [],
                    "thinkingEnabled": False,
                    "multiTurnEnabled": False,
                    "temperature": 0.8,
                }
            },
            "creationParameters": {"outline-batch": "4"},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "saved",
        "persistence": "persisted",
        "message": "已保存",
        "acceptedRouteIds": ["draft_chapter"],
        "rejectedRoutes": [],
        "savedAtLabel": "12:00:00",
        "runtimeReloadStatus": "reloaded",
    }
    assert client.post("/api/v1/ui/settings/save", json={}).status_code == 404


def test_engine_delete_projects_removes_files_and_blocks_active_jobs(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    storage.ensure_project_dir("demo")
    idle_dir = storage.ensure_project_dir("idle")
    (idle_dir / "chapter.md").write_text("正文", encoding="utf-8")
    service = StubEngineJobService()

    class Runtime:
        def __init__(self) -> None:
            self.released: list[str] = []

        def release_memory_context(self, project_id: str) -> None:
            self.released.append(project_id)

    runtime = Runtime()
    app = create_app()
    app.dependency_overrides[get_job_service] = lambda: service
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_runtime_services] = lambda: runtime
    client = TestClient(app)

    response = client.post(
        "/api/v1/engine/commands/delete-projects",
        json={
            "kind": "delete_projects",
            "projectIds": ["demo", "idle", "missing"],
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "partial",
        "message": "已删除 1 个项目，2 个项目未删除。",
        "deletedProjectIds": ["idle"],
        "failures": [
            {
                "projectId": "demo",
                "reason": "active_job",
                "message": "项目有正在运行或排队的任务，请先结束任务。",
            },
            {
                "projectId": "missing",
                "reason": "project_not_found",
                "message": "项目目录不存在，可能已被删除。",
            },
        ],
    }
    assert (tmp_path / "demo").exists()
    assert not idle_dir.exists()
    assert runtime.released == ["idle", "missing"]


def test_engine_chapter_maintenance_commands_submit_shared_durable_jobs() -> None:
    service = StubEngineJobService()
    client = _client(service)
    commands = [
        (
            "/api/v1/engine/commands/repair-continuity",
            {
                "kind": "repair_continuity",
                "projectId": "demo",
                "chapterNumber": 6,
                "repairControlMode": "ai_assisted",
            },
            JobKind.REPAIR_CONTINUITY,
        ),
        (
            "/api/v1/engine/commands/repair-causal",
            {"kind": "repair_causal", "projectId": "demo", "chapterNumber": 6},
            JobKind.REPAIR_CAUSAL,
        ),
        (
            "/api/v1/engine/commands/repair-issues",
            {"kind": "repair_issues", "projectId": "demo", "chapterNumber": 6},
            JobKind.REPAIR_ISSUES,
        ),
        (
            "/api/v1/engine/commands/reevaluate-chapter",
            {"kind": "reevaluate_chapter", "projectId": "demo", "chapterNumber": 6},
            JobKind.REEVALUATE_CHAPTER,
        ),
        (
            "/api/v1/engine/commands/reextract-relationships",
            {"kind": "reextract_relationships", "projectId": "demo", "chapterNumber": 0},
            JobKind.REEXTRACT_RELATIONSHIPS,
        ),
        (
            "/api/v1/engine/commands/repair-motif-history",
            {
                "kind": "repair_motif_history",
                "projectId": "demo",
                "chapterNumber": 6,
                "forceReExtract": True,
            },
            JobKind.REPAIR_MOTIF_HISTORY,
        ),
    ]

    for path, payload, kind in commands:
        response = client.post(path, json=payload)

        assert response.status_code == 200
        assert response.json()["status"] == "accepted"
        assert response.json()["taskId"]
        assert service.submitted[-1].kind == kind
        assert service.submitted[-1].project_id == "demo"
        assert service.submitted[-1].metadata == {"workflow_type": "chapter_maintenance"}

    assert service.submitted[0].payload["repair_control_mode"] == "ai_assisted"
    assert service.submitted[4].payload["chapter_number"] == 0
    assert service.submitted[5].payload["force_re_extract"] is True


def test_engine_runtime_exposes_safe_identity_and_task_activity() -> None:
    client = _client(StubEngineJobService())

    runtime = client.get("/api/v1/engine/runtime")

    assert runtime.status_code == 200
    payload = runtime.json()
    assert payload["status"] in {"ready", "restartRequired"}
    assert payload["activeJobCount"] == 1
    assert payload["queuedJobCount"] == 0
    assert payload["canSubmitTasks"] is (payload["status"] == "ready")
    assert "storageRoot" not in payload
    assert "apiKey" not in payload


def test_engine_restart_required_is_an_actionable_conflict() -> None:
    class _RestartRequiredService(StubEngineJobService):
        def submit(self, command: JobCommand) -> JobRecord:
            raise EngineRestartRequiredError(boot_revision="src-old", current_revision="src-new")

    client = _client(_RestartRequiredService())
    response = client.post(
        "/api/v1/engine/commands/prepare-chapter",
        json={"projectId": "demo", "chapterNumber": 1},
    )

    assert response.status_code == 409
    assert response.json()["error"] == "engine_restart_required"
    assert response.json()["bootRevision"] == "src-old"


def test_engine_task_stream_is_a_read_only_polling_snapshot() -> None:
    service = StubEngineJobService()
    service.record.events.extend(
        [
            JobStepEvent(
                at="2026-07-18T10:00:00+00:00",
                step="model_call_update",
                payload={
                    "event": "api_stream_start",
                    "call_id": "call-api-1",
                    "task": "DRAFT_CHAPTER",
                    "provider": "openai",
                    "model": "gpt-test",
                    "route": "primary",
                },
            ),
            JobStepEvent(
                at="2026-07-18T10:00:01+00:00",
                step="model_call_update",
                payload={
                    "event": "api_stream_done",
                    "call_id": "call-api-1",
                    "task": "DRAFT_CHAPTER",
                    "prompt_tokens": 100,
                    "completion_tokens": 40,
                    "total_tokens": 140,
                    "latency_ms": 900,
                },
            ),
        ]
    )
    client = _client(service)

    response = client.get("/api/v1/engine/jobs/job-engine/task-stream")

    assert response.status_code == 200
    assert response.json()["taskId"] == "job-engine"
    assert response.json()["stepId"] == "draft_chapter"
    assert response.json()["stepLabel"] == "DRAFT 草稿 · 章节写作"
    assert response.json()["events"][0]["streamId"] == "s1"
    assert response.json()["events"][0]["text"] == "正在生成"
    calls = response.json()["calls"]
    assert len(calls) == 1
    assert calls[0] == {
        "callId": "call-api-1",
        "task": "DRAFT_CHAPTER",
        "taskLabel": "DRAFT 原稿",
        "provider": "openai",
        "model": "gpt-test",
        "route": "primary",
        "status": "success",
        "event": "api_stream_done",
        "promptTokens": 100,
        "completionTokens": 40,
        "totalTokens": 140,
        "latencyMs": 900.0,
        "startedAt": "2026-07-18T10:00:00+00:00",
        "finishedAt": "2026-07-18T10:00:01+00:00",
    }


def test_engine_task_stream_returns_404_for_unknown_job() -> None:
    client = _client(StubEngineJobService())

    response = client.get("/api/v1/engine/jobs/missing/task-stream")

    assert response.status_code == 404
    assert response.json()["detail"] == "job not found"


def test_engine_task_stream_supports_cursor_paging() -> None:
    service = StubEngineJobService()
    service.record.events.append(
        JobStepEvent(
            step="llm_stream_delta",
            payload={
                "stream_id": "s1",
                "segments": [{"kind": "content", "text": "第二段"}],
            },
        )
    )
    client = _client(service)

    first = client.get("/api/v1/engine/jobs/job-engine/task-stream", params={"limit": 1})

    assert first.status_code == 200
    first_payload = first.json()
    assert first_payload["events"][-1]["text"] == "第二段"
    assert first_payload["nextCursor"] == first_payload["events"][-1]["cursor"]

    resumed = client.get(
        "/api/v1/engine/jobs/job-engine/task-stream",
        params={"after_cursor": first_payload["nextCursor"], "limit": 1},
    )

    assert resumed.status_code == 200
    assert resumed.json()["events"] == []


def test_engine_exposes_novel_and_voice_read_models_for_ui_adapters() -> None:
    client = _client(StubEngineJobService())

    novel = client.get(
        "/api/v1/engine/novel/projects/demo/studio",
        params={"chapter_number": 7},
    )
    voice = client.get(
        "/api/v1/engine/voice/projects/demo/studio",
        params={"chapter_number": 7},
    )

    assert novel.status_code == 200
    assert novel.json()["projectId"] == "demo"
    assert novel.json()["nextChapter"] == 7
    assert voice.status_code == 200
    assert voice.json()["chapterNumber"] == 7
    assert voice.json()["providerLabel"] == "mock"


class _FailureStubQueryService:
    """Stub that raises a configurable error from ``get_novel_studio``."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.calls: list[tuple[str, int | None]] = []

    def get_novel_studio(
        self,
        project_id: str,
        *,
        chapter_number: int | None = None,
    ) -> EngineNovelStudioView:
        self.calls.append((project_id, chapter_number))
        raise self._exc

    def get_voice_studio(
        self,
        project_id: str,
        *,
        chapter_number: int | None = None,
    ) -> EngineVoiceStudioView:
        return EngineVoiceStudioView(
            project_id=project_id,
            project_title="声腔测试",
            provider_label="mock",
            configured_model_label="mock-voice",
            chapter_number=chapter_number or 1,
        )


def _client_with_query_service(query_service) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_job_service] = lambda: StubEngineJobService()
    app.dependency_overrides[get_engine_query_service] = lambda: query_service
    return TestClient(app)


def test_novel_studio_returns_400_when_project_is_not_long() -> None:
    client = _client_with_query_service(_FailureStubQueryService(ProjectNotLongError("not-long")))

    response = client.get("/api/v1/engine/novel/projects/not-long/studio")

    assert response.status_code == 400
    assert "不是长篇项目" in response.json()["detail"]


def test_novel_studio_returns_degraded_view_when_outline_missing() -> None:
    """When the real service degrades (outline missing), the route returns 200."""
    degraded_view = EngineNovelStudioView(
        project_id="outlineless",
        project_title="缺大纲",
        next_chapter=6,
        total_chapters=10,
        chapters=[],
        plan_summary="项目大纲数据暂不可用，章台以降级模式显示。",
    )

    class _DegradedStub:
        def get_novel_studio(self, project_id, *, chapter_number=None):
            return degraded_view

        def get_voice_studio(self, project_id, *, chapter_number=None):
            return EngineVoiceStudioView(
                project_id=project_id,
                project_title="x",
                provider_label="mock",
                configured_model_label="mock-voice",
                chapter_number=chapter_number or 1,
            )

    client = _client_with_query_service(_DegradedStub())
    response = client.get("/api/v1/engine/novel/projects/outlineless/studio")

    assert response.status_code == 200
    body = response.json()
    assert body["projectId"] == "outlineless"
    assert body["chapters"] == []
    assert "降级" in body["planSummary"]


def test_novel_studio_returns_degraded_view_when_chapter_out_of_range() -> None:
    """When the real service degrades (chapter not in outline), the route returns 200."""
    degraded_view = EngineNovelStudioView(
        project_id="demo",
        project_title="测试",
        next_chapter=4,
        total_chapters=5,
        chapters=[],
        plan_summary="项目大纲数据暂不可用，章台以降级模式显示。",
    )

    class _DegradedStub:
        def get_novel_studio(self, project_id, *, chapter_number=None):
            return degraded_view

        def get_voice_studio(self, project_id, *, chapter_number=None):
            return EngineVoiceStudioView(
                project_id=project_id,
                project_title="x",
                provider_label="mock",
                configured_model_label="mock-voice",
                chapter_number=chapter_number or 1,
            )

    client = _client_with_query_service(_DegradedStub())
    response = client.get(
        "/api/v1/engine/novel/projects/demo/studio", params={"chapter_number": 99}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["projectId"] == "demo"
    assert body["chapters"] == []


def test_novel_studio_returns_400_for_invalid_chapter_number() -> None:
    client = _client_with_query_service(_FailureStubQueryService(InvalidChapterNumberError(0)))

    response = client.get("/api/v1/engine/novel/projects/demo/studio", params={"chapter_number": 0})

    assert response.status_code == 400
    assert "必须大于等于 1" in response.json()["detail"]


def test_engine_surface_separates_queries_from_commands() -> None:
    app = create_app()
    engine_paths = {
        path: operations
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/api/v1/engine")
    }

    # Command paths include /commands/* and action endpoints like /jobs/{id}/cancel
    command_paths = {
        path: operations
        for path, operations in engine_paths.items()
        if "/commands/" in path
        or path.endswith("/cancel")
        or any(method != "get" for method in operations)
    }
    query_paths = {
        path: operations for path, operations in engine_paths.items() if path not in command_paths
    }

    assert query_paths
    assert command_paths
    assert all(set(operations) == {"get"} for operations in query_paths.values())
    assert all(
        any(method in {"post", "put", "delete"} for method in operations)
        for operations in command_paths.values()
    )


# ── Command endpoint tests ────────────────────────────────────────────────────


def test_polish_outline_submits_durable_outline_job() -> None:
    service = StubEngineJobService()
    client = _client(service)

    response = client.post(
        "/api/v1/engine/commands/polish-outline",
        json={
            "project_id": "demo",
            "user_hint": "强化第 3 到 5 章的章尾压力",
            "focus_fields": ["goal", "notes"],
            "chapter_range": "3-5",
            "sync_contracts": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert response.json()["taskId"] == "job-submitted-1"
    assert service.submitted[0].kind == JobKind.POLISH_OUTLINE
    assert service.submitted[0].payload == {
        "project_id": "demo",
        "user_hint": "强化第 3 到 5 章的章尾压力",
        "selected_suggestions": [],
        "focus_fields": ["goal", "notes"],
        "chapter_range": "3-5",
        "analysis_only": False,
        "sync_contracts": True,
    }


def test_outline_maintenance_commands_submit_durable_jobs() -> None:
    service = StubEngineJobService()
    client = _client(service)

    sync_response = client.post(
        "/api/v1/engine/commands/sync-chapter-contracts",
        json={
            "project_id": "demo",
            "affected_chapter_numbers": [3, 4],
            "prose_untouched": True,
        },
    )
    extend_response = client.post(
        "/api/v1/engine/commands/extend-outline",
        json={
            "project_id": "demo",
            "additional_chapters": 10,
            "sync_contracts": True,
        },
    )

    assert sync_response.status_code == 200
    assert extend_response.status_code == 200
    assert service.submitted[0].kind == JobKind.SYNC_CHAPTER_CONTRACTS
    assert service.submitted[0].payload["affected_chapter_numbers"] == [3, 4]
    assert service.submitted[0].payload["prose_untouched"] is True
    assert service.submitted[1].kind == JobKind.EXTEND_OUTLINE
    assert service.submitted[1].payload["additional_chapters"] == 10
    assert service.submitted[1].payload["target_total"] is None


def test_extend_outline_rejects_ambiguous_extension_mode() -> None:
    service = StubEngineJobService()
    client = _client(service)

    response = client.post(
        "/api/v1/engine/commands/extend-outline",
        json={
            "project_id": "demo",
            "additional_chapters": 10,
            "target_total": 80,
        },
    )

    assert response.status_code == 422
    assert service.submitted == []


def test_audit_book_forwards_all_new_ui_parameters() -> None:
    service = StubEngineJobService()
    client = _client(service)

    response = client.post(
        "/api/v1/engine/commands/audit-book",
        json={
            "kind": "audit_book",
            "projectId": "demo",
            "chapterRange": [2, 4],
            "analysisMode": "full_text",
            "twoPhaseEnabled": False,
            "twoPhaseMaxTargetChapters": 6,
            "locationStrictness": "strict",
            "maxTokens": 12000,
            "temperature": 0.15,
            "auditMaxChaptersPerBatch": 3,
            "auditMaxIssuesPerChunk": 9,
            "auditIssuePoolMaxItems": 90,
            "twoPhaseThreshold": 0.8,
            "chapterMaxChars": 18000,
            "parallelChunks": True,
            "parallelDimensions": False,
            "promptHint": "优先核对人物称谓",
        },
    )

    assert response.status_code == 200
    assert service.submitted[0].kind == JobKind.BOOK_CONSISTENCY
    payload = service.submitted[0].payload
    assert payload["project_id"] == "demo"
    assert payload["chapter_range"] == [2, 4]
    assert payload["analysis_mode"] == "full_text"
    assert payload["two_phase_enabled"] is False
    assert payload["two_phase_max_target_chapters"] == 6
    assert payload["location_strictness"] == "strict"
    assert payload["max_tokens"] == 12000
    assert payload["temperature"] == 0.15
    assert payload["audit_max_chapters_per_batch"] == 3
    assert payload["audit_max_issues_per_chunk"] == 9
    assert payload["audit_issue_pool_max_items"] == 90
    assert payload["two_phase_threshold"] == 0.8
    assert payload["chapter_max_chars"] == 18000
    assert payload["parallel_chunks"] is True
    assert payload["parallel_dimensions"] is False
    assert payload["prompt_hint"] == "优先核对人物称谓"


def test_execute_global_repair_queue_submits_guarded_durable_job() -> None:
    service = StubEngineJobService()
    client = _client(service)

    response = client.post(
        "/api/v1/engine/commands/execute-global-repair-queue",
        json={
            "kind": "execute_global_repair_queue",
            "projectId": "demo",
            "statuses": ["ready"],
            "maxItems": 12,
            "verifyBeforeApply": True,
            "rollbackOnFailure": True,
            "concurrency": 2,
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert service.submitted[0].kind == JobKind.GLOBAL_REPAIR_QUEUE
    assert service.submitted[0].payload == {
        "project_id": "demo",
        "run_id": "",
        "statuses": ["ready"],
        "max_items": 12,
        "verify_before_apply": True,
        "rollback_on_failure": True,
        "concurrency": 2,
    }


def test_book_editorial_audit_submits_publication_level_job() -> None:
    service = StubEngineJobService()
    client = _client(service)

    response = client.post(
        "/api/v1/engine/commands/audit-book-editorial",
        json={
            "kind": "audit_book_editorial",
            "projectId": "demo",
            "chapterRange": [1, 2, 3],
            "promptHint": "优先检查中段节奏",
            "maxTokens": 12000,
            "temperature": 0.15,
            "batchSize": 3,
        },
    )

    assert response.status_code == 200
    assert service.submitted[0].kind == JobKind.BOOK_EDITORIAL_AUDIT
    assert service.submitted[0].payload["chapter_range"] == [1, 2, 3]
    assert service.submitted[0].payload["batch_size"] == 3
    assert service.submitted[0].payload["batch_timeout_s"] == 0.0


def test_prepare_chapter_includes_project_id_in_payload() -> None:
    service = StubEngineJobService()
    client = _client(service)

    response = client.post(
        "/api/v1/engine/commands/prepare-chapter",
        json={
            "project_id": "demo",
            "chapter_number": 3,
            "notes": "test",
            "force": True,
            "writing_mode": "scene_level",
            "rewrite_strategy": "reconstruct",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["taskId"] is not None
    assert len(service.submitted) == 1
    cmd = service.submitted[0]
    assert cmd.payload["project_id"] == "demo"
    assert cmd.payload["chapter_number"] == 3
    assert cmd.payload["force"] is True
    assert cmd.payload["writing_mode"] == "scene_level"
    assert cmd.payload["rewrite_strategy"] == "reconstruct"


def test_prepare_chapter_reports_same_project_initialization_conflict() -> None:
    class _InitConflictService(StubEngineJobService):
        def __init__(self) -> None:
            super().__init__()
            self.record.kind = JobKind.INIT_LONG
            self.record.label = "长篇立项初始化"

        def submit(self, command: JobCommand) -> JobRecord:
            self.submitted.append(command)
            return self.record

    service = _InitConflictService()
    client = _client(service)

    response = client.post(
        "/api/v1/engine/commands/prepare-chapter",
        json={"project_id": "demo", "chapter_number": 3},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "already_running"
    assert "正在立项初始化" in body["message"]
    assert body.get("taskId") is None
    assert len(service.submitted) == 1


def test_design_character_voice_crosses_real_workspace_boundary(
    monkeypatch,
    tmp_path,
) -> None:
    from novel_forge.api.routes import engine as engine_routes

    captured: dict = {}

    async def _fake_design(**kwargs):
        captured.update(kwargs)
        return ExecutionResult(project_id=kwargs["project_id"], result={"entries": []})

    storage = FileSystemStorage(tmp_path)
    (tmp_path / "demo").mkdir()
    runtime = type("RuntimeStub", (), {"settings": object()})()
    monkeypatch.setattr(engine_routes, "execute_design_character_voice", _fake_design)
    app = create_app()
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_runtime_services] = lambda: runtime
    client = TestClient(app)

    response = client.post(
        "/api/v1/engine/commands/design-character-voice",
        json={
            "kind": "design_character_voice",
            "projectId": "demo",
            "characterId": "lin-zhu",
            "description": "克制、低沉，句尾略收。",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert captured["project_id"] == "demo"
    assert captured["character_id"] == "lin-zhu"
    assert captured["description"] == "克制、低沉，句尾略收。"


def test_rebuild_narrator_voice_crosses_real_workspace_boundary(
    monkeypatch,
    tmp_path,
) -> None:
    from novel_forge.api.routes import engine as engine_routes

    captured: dict = {}

    async def _fake_rebuild(**kwargs):
        captured.update(kwargs)
        return ExecutionResult(project_id=kwargs["project_id"], result={"voice_id": "narrator-new"})

    storage = FileSystemStorage(tmp_path)
    (tmp_path / "demo").mkdir()
    runtime = type("RuntimeStub", (), {"settings": object()})()
    monkeypatch.setattr(engine_routes, "execute_build_narrator_profile", _fake_rebuild)
    app = create_app()
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_runtime_services] = lambda: runtime
    client = TestClient(app)

    response = client.post(
        "/api/v1/engine/commands/rebuild-narrator-voice",
        json={
            "kind": "rebuild_narrator_voice",
            "projectId": "demo",
            "provider": "minimax",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert captured["project_id"] == "demo"
    assert captured["provider"] == "minimax"


def test_save_voice_script_maps_camel_case_edits_to_workspace(
    monkeypatch,
    tmp_path,
) -> None:
    from novel_forge.api.routes import engine as engine_routes

    captured: dict = {}

    async def _fake_save(**kwargs):
        captured.update(kwargs)
        return ExecutionResult(
            project_id=kwargs["project_id"],
            result={"changed_segment_indices": [7]},
        )

    storage = FileSystemStorage(tmp_path)
    (tmp_path / "demo").mkdir()
    monkeypatch.setattr(engine_routes, "execute_save_dubbing_script", _fake_save)
    app = create_app()
    app.dependency_overrides[get_storage] = lambda: storage
    client = TestClient(app)

    response = client.post(
        "/api/v1/engine/commands/save-voice-script",
        json={
            "kind": "save_voice_script",
            "projectId": "demo",
            "chapterNumber": 4,
            "edits": [
                {
                    "segmentIndex": 7,
                    "content": "修改后的台词。",
                    "speakerId": "lin-zhu",
                    "speakerLabel": "林逐",
                    "emotionLabel": "克制",
                    "speedOverride": 0.9,
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert captured["chapter_number"] == 4
    assert captured["edits"][0]["segment_index"] == 7
    assert captured["edits"][0]["speed_override"] == 0.9


def test_clean_chapters_uses_regeneration_boundary(
    monkeypatch,
    tmp_path,
) -> None:
    from novel_forge.api.routes import engine as engine_routes

    captured: dict = {}

    async def _fake_regenerate(storage, layout, *, from_chapter):
        captured.update(
            storage=storage,
            layout=layout,
            from_chapter=from_chapter,
        )
        return {5, 6}

    service = StubEngineJobService()
    service.record.status = JobState.SUCCEEDED
    storage = FileSystemStorage(tmp_path)
    (tmp_path / "demo").mkdir()
    runtime = type("RuntimeStub", (), {"memory_contexts": {}})()
    monkeypatch.setattr(engine_routes, "regenerate_from_chapter_async", _fake_regenerate)
    app = create_app()
    app.dependency_overrides[get_job_service] = lambda: service
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_runtime_services] = lambda: runtime
    client = TestClient(app)

    response = client.post(
        "/api/v1/engine/commands/clean-chapters",
        json={
            "kind": "clean_chapters",
            "projectId": "demo",
            "fromChapter": 5,
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert captured["from_chapter"] == 5
    assert service.autorun_cleanup_resets == [("demo", 5)]


def test_clean_chapters_awaits_real_story_kernel_rollback_and_removes_publication(
    tmp_path,
) -> None:
    service = StubEngineJobService()
    service.record.status = JobState.SUCCEEDED
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.project_dir("demo"))
    layout.ensure_dirs()
    storage.save_json(layout.canon_dir / "canon_current.json", {"current_chapter": 1})
    layout.chapter_path(1).write_text("已归档第一章", encoding="utf-8")
    storage.save_json(
        layout.chapter_publication_path(1),
        {
            "chapter_number": 1,
            "publication_status": "ready",
            "deliverable": True,
        },
    )
    _seed_story_kernel(layout, current_chapter=1)
    with sqlite3.connect(layout.root / "snapshots" / "kernel_v0.db") as snapshot_connection:
        snapshot_kernel_json = snapshot_connection.execute(
            "SELECT kernel_json FROM kernel_meta WHERE project_id = ?",
            ("demo",),
        ).fetchone()[0]

    runtime = type("RuntimeStub", (), {"memory_contexts": {}})()
    app = create_app()
    app.dependency_overrides[get_job_service] = lambda: service
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_runtime_services] = lambda: runtime

    response = TestClient(app).post(
        "/api/v1/engine/commands/clean-chapters",
        json={"kind": "clean_chapters", "projectId": "demo", "fromChapter": 1},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "accepted"
    assert payload["data"]["cleanupComplete"] is True
    assert "已核验可安全重跑" in payload["message"]
    assert storage.load_json(layout.canon_dir / "canon_current.json") == json.loads(
        snapshot_kernel_json
    )
    with sqlite3.connect(layout.story_kernel_db_path) as connection:
        row = connection.execute(
            "SELECT current_chapter, kernel_json FROM kernel_meta WHERE project_id = ?",
            ("demo",),
        ).fetchone()
    assert row is not None
    assert row[0] == 0
    assert row[1] == snapshot_kernel_json
    assert not layout.chapter_path(1).exists()
    assert not layout.chapter_publication_path(1).exists()


def test_clean_chapters_discards_nonexecuting_checkpoint_and_resets_projection(
    monkeypatch,
    tmp_path,
) -> None:
    from novel_forge.api.routes import engine as engine_routes

    service = StubEngineJobService()
    service.record = JobRecord(
        job_id="job-plan-checkpoint",
        kind=JobKind.PREPARE_CHAPTER,
        label="章节方案 · demo / 第 1 章",
        project_id="demo",
        status=JobState.PAUSED,
        current_step="plan_checkpoint",
        result={"chapter_number": 1, "status": "needs_decision"},
    )
    service.autorun_state = SimpleNamespace(status="failed")
    storage = FileSystemStorage(tmp_path)
    (tmp_path / "demo").mkdir()
    runtime = type("RuntimeStub", (), {"memory_contexts": {}})()
    monkeypatch.setattr(
        engine_routes,
        "regenerate_from_chapter_async",
        _async_regeneration_result({1}),
    )
    app = create_app()
    app.dependency_overrides[get_job_service] = lambda: service
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_runtime_services] = lambda: runtime
    client = TestClient(app)

    response = client.post(
        "/api/v1/engine/commands/clean-chapters",
        json={"kind": "clean_chapters", "projectId": "demo", "fromChapter": 1},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert service.cleared == ["job-plan-checkpoint"]
    assert service.autorun_cleanup_resets == [("demo", 1)]


def test_clean_chapters_converges_stale_task_state_when_files_are_already_absent(
    monkeypatch,
    tmp_path,
) -> None:
    from novel_forge.api.routes import engine as engine_routes

    service = StubEngineJobService()
    service.record = JobRecord(
        job_id="job-stale-failure",
        kind=JobKind.PREPARE_CHAPTER,
        label="章节方案 · demo / 第 1 章",
        project_id="demo",
        status=JobState.FAILED,
        current_step="bridge",
        result={"chapter_number": 1},
    )
    service.autorun_state = SimpleNamespace(status="failed")
    storage = FileSystemStorage(tmp_path)
    (tmp_path / "demo").mkdir()
    runtime = type("RuntimeStub", (), {"memory_contexts": {}})()
    monkeypatch.setattr(
        engine_routes,
        "regenerate_from_chapter_async",
        _async_regeneration_result(set()),
    )
    app = create_app()
    app.dependency_overrides[get_job_service] = lambda: service
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_runtime_services] = lambda: runtime
    client = TestClient(app)

    response = client.post(
        "/api/v1/engine/commands/clean-chapters",
        json={"kind": "clean_chapters", "projectId": "demo", "fromChapter": 1},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert "已无可删除文件" in response.json()["message"]
    assert service.cleared == ["job-stale-failure"]
    assert service.autorun_cleanup_resets == [("demo", 1)]


def test_clean_chapters_reports_followup_warning_after_destructive_commit(
    monkeypatch,
    tmp_path,
) -> None:
    from novel_forge.api.routes import engine as engine_routes

    class BrokenMemoryContext:
        def invalidate_chapter_memory(self, _chapter: int) -> None:
            raise OSError("磁盘不可写")

    service = StubEngineJobService()
    service.record.status = JobState.SUCCEEDED
    storage = FileSystemStorage(tmp_path)
    (tmp_path / "demo").mkdir()
    runtime = type(
        "RuntimeStub",
        (),
        {"memory_contexts": {"demo": BrokenMemoryContext()}},
    )()
    monkeypatch.setattr(
        engine_routes,
        "regenerate_from_chapter_async",
        _async_regeneration_result({6}),
    )
    app = create_app()
    app.dependency_overrides[get_job_service] = lambda: service
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_runtime_services] = lambda: runtime
    client = TestClient(app)

    response = client.post(
        "/api/v1/engine/commands/clean-chapters",
        json={"kind": "clean_chapters", "projectId": "demo", "fromChapter": 6},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert "记忆缓存刷新失败" in response.json()["message"]
    assert "已从第 6 章起清理" in response.json()["message"]
    assert response.json()["data"] == {
        "cleanupMayHaveChangedData": True,
        "cleanupComplete": False,
        "refreshRequired": True,
        "invalidatedChapters": [6],
        "failedStages": ["memory_cache"],
    }


def test_clean_chapters_fails_closed_when_core_cleanup_does_not_converge(
    monkeypatch,
    tmp_path,
) -> None:
    from novel_forge.api.routes import engine as engine_routes
    from novel_forge.persistence.project_staleness import ChapterCleanupConvergenceError

    async def _fail_cleanup(*_args, **_kwargs):
        raise ChapterCleanupConvergenceError(
            "StoryKernel 水位仍为 1",
            invalidated_chapters=(1,),
            failed_stage="story_kernel",
        )

    service = StubEngineJobService()
    service.record.status = JobState.SUCCEEDED
    storage = FileSystemStorage(tmp_path)
    (tmp_path / "demo").mkdir()
    runtime = type("RuntimeStub", (), {"memory_contexts": {}})()
    monkeypatch.setattr(engine_routes, "regenerate_from_chapter_async", _fail_cleanup)
    app = create_app()
    app.dependency_overrides[get_job_service] = lambda: service
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_runtime_services] = lambda: runtime

    response = TestClient(app).post(
        "/api/v1/engine/commands/clean-chapters",
        json={"kind": "clean_chapters", "projectId": "demo", "fromChapter": 1},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "rejected"
    assert "暂勿启动连跑" in payload["message"]
    assert payload["data"]["cleanupComplete"] is False
    assert payload["data"]["refreshRequired"] is True
    assert payload["data"]["failedStages"] == ["story_kernel"]


def test_clean_chapters_rejects_running_or_retrying_work(
    monkeypatch,
    tmp_path,
) -> None:
    from novel_forge.api.routes import engine as engine_routes

    service = StubEngineJobService()
    storage = FileSystemStorage(tmp_path)
    (tmp_path / "demo").mkdir()
    runtime = type("RuntimeStub", (), {"memory_contexts": {}})()
    async def _unexpected_regeneration(*_args, **_kwargs):
        pytest.fail("运行中任务不得进入清理")

    monkeypatch.setattr(
        engine_routes,
        "regenerate_from_chapter_async",
        _unexpected_regeneration,
    )
    app = create_app()
    app.dependency_overrides[get_job_service] = lambda: service
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_runtime_services] = lambda: runtime
    client = TestClient(app)

    response = client.post(
        "/api/v1/engine/commands/clean-chapters",
        json={"kind": "clean_chapters", "projectId": "demo", "fromChapter": 1},
    )
    assert response.json()["status"] == "rejected"
    assert "第 6 章" in response.json()["message"]

    service.record.status = JobState.SUCCEEDED
    service.autorun_state = SimpleNamespace(status="retry_wait")
    response = client.post(
        "/api/v1/engine/commands/clean-chapters",
        json={"kind": "clean_chapters", "projectId": "demo", "fromChapter": 1},
    )
    assert response.json()["status"] == "rejected"
    assert "等待重试" in response.json()["message"]


def test_clean_chapters_surfaces_authoring_maintenance_guard(
    monkeypatch,
    tmp_path,
) -> None:
    from novel_forge.api.routes import engine as engine_routes
    from novel_forge.persistence.authoring_store import AuthoringDeniedError

    service = StubEngineJobService()
    service.record.status = JobState.SUCCEEDED
    storage = FileSystemStorage(tmp_path)
    (tmp_path / "demo").mkdir()
    runtime = type("RuntimeStub", (), {"memory_contexts": {}})()

    async def _deny(*_args, **_kwargs):
        raise AuthoringDeniedError("已采用作者授权；请使用专项提案")

    monkeypatch.setattr(engine_routes, "regenerate_from_chapter_async", _deny)
    app = create_app()
    app.dependency_overrides[get_job_service] = lambda: service
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_runtime_services] = lambda: runtime
    client = TestClient(app)

    response = client.post(
        "/api/v1/engine/commands/clean-chapters",
        json={"kind": "clean_chapters", "projectId": "demo", "fromChapter": 1},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert response.json()["message"] == "已采用作者授权；请使用专项提案"


def test_cancel_chapter_matches_by_chapter_number() -> None:
    service = StubEngineJobService()
    # Running full-chapter workflows are cancellable from the same Chapter Studio control.
    client = _client(service)

    response = client.post(
        "/api/v1/engine/commands/cancel-chapter",
        json={"project_id": "demo", "chapter_number": 6},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert service.cancelled == ["job-engine"]


def test_cancel_chapter_succeeds_for_matching_prepare_job() -> None:
    service = StubEngineJobService()
    # Replace the record with a prepare_chapter job that has chapter_number.
    service.record = JobRecord(
        job_id="job-prepare",
        kind=JobKind.PREPARE_CHAPTER,
        label="准备章节",
        project_id="demo",
        status=JobState.RUNNING,
        current_step_payload={"chapter_number": 3},
    )
    client = _client(service)

    response = client.post(
        "/api/v1/engine/commands/cancel-chapter",
        json={"project_id": "demo", "chapter_number": 3},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert service.cancelled == ["job-prepare"]


def test_cancel_chapter_skips_non_matching_chapter() -> None:
    service = StubEngineJobService()
    service.record = JobRecord(
        job_id="job-prepare",
        kind=JobKind.PREPARE_CHAPTER,
        label="准备章节",
        project_id="demo",
        status=JobState.RUNNING,
        current_step_payload={"chapter_number": 5},
    )
    client = _client(service)

    response = client.post(
        "/api/v1/engine/commands/cancel-chapter",
        json={"project_id": "demo", "chapter_number": 3},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert service.cancelled == []


def test_cancel_job_persists_client_supplied_audit_reason() -> None:
    service = StubEngineJobService()
    client = _client(service)

    response = client.post(
        "/api/v1/engine/jobs/job-engine/cancel",
        json={"reason": "用户取消角色试听，改用目录音色"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert service.cancelled == ["job-engine"]
    assert service.cancel_reasons == ["用户取消角色试听，改用目录音色"]


def test_cancel_job_keeps_legacy_default_reason_without_body() -> None:
    service = StubEngineJobService()
    client = _client(service)

    response = client.post("/api/v1/engine/jobs/job-engine/cancel")

    assert response.status_code == 200
    assert service.cancel_reasons == ["用户已取消"]


def test_start_workflow_rejects_unknown_type() -> None:
    service = StubEngineJobService()
    client = _client(service)

    response = client.post(
        "/api/v1/engine/commands/start-workflow",
        json={
            "project_id": "demo",
            "workflow_type": "unknown_type",
            "idempotency_key": "invalid",
            "payload": {"project_id": "demo", "theme": "测试"},
        },
    )

    assert response.status_code == 422


def test_start_workflow_accepts_valid_type() -> None:
    service = StubEngineJobService()
    client = _client(service)

    response = client.post(
        "/api/v1/engine/commands/start-workflow",
        json={
            "project_id": "demo",
            "workflow_type": "short",
            "run_mode": "create",
            "idempotency_key": "short-demo-1",
            "payload": {
                "project_id": "demo",
                "theme": "梦境探险",
                "genre": "悬疑",
                "segment_trigger_words": 5000,
                "max_edit_rounds": 3,
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert service.submitted[0].kind == "run_short"
    assert service.submitted[0].payload["segment_trigger_words"] == 5000
    assert service.submitted[0].payload["max_edit_rounds"] == 3


def test_start_workflow_binds_generated_project_id_before_submitting() -> None:
    service = StubEngineJobService()
    requested_modes: list[str] = []
    runtime = SimpleNamespace(
        create_project_id=lambda mode: requested_modes.append(mode) or "long_auto_1234"
    )
    client = _client(service, runtime)

    response = client.post(
        "/api/v1/engine/commands/start-workflow",
        json={
            "project_id": "",
            "workflow_type": "long_init",
            "run_mode": "create",
            "idempotency_key": "auto-long-init-1",
            "payload": {"project_id": "", "premise": "自动项目也应可打开过程产物"},
        },
    )

    assert response.status_code == 200
    assert requested_modes == ["long"]
    command = service.submitted[0]
    assert command.project_id == "long_auto_1234"
    assert command.payload["project_id"] == "long_auto_1234"


def test_start_long_init_reports_same_project_chapter_conflict() -> None:
    class _ChapterConflictService(StubEngineJobService):
        def submit(self, command: JobCommand) -> JobRecord:
            self.submitted.append(command)
            return self.record

    service = _ChapterConflictService()
    client = _client(service)

    response = client.post(
        "/api/v1/engine/commands/start-workflow",
        json={
            "project_id": "demo",
            "workflow_type": "long_init",
            "run_mode": "create",
            "idempotency_key": "init-while-chapter-demo",
            "payload": {"project_id": "demo", "premise": "确认项目冲突提示"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "already_running"
    assert "不能与章台并行" in body["message"]
    assert len(service.submitted) == 1


def test_continue_long_init_uses_project_resume_and_keeps_form_as_fallback() -> None:
    service = StubEngineJobService()
    service.record.status = JobState.FAILED
    service.record.kind = JobKind.INIT_LONG
    client = _client(service)

    response = client.post(
        "/api/v1/engine/commands/continue-long-init",
        json={
            "kind": "continue_long_init",
            "projectId": "demo",
            "runMode": "autorun",
            "fallbackPayload": {
                "projectId": "edited-form-id",
                "premise": "兼容旧项目的表单前提",
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert response.json()["taskId"] == "job-init-resumed"
    call = service.project_resumes[0]
    assert call["project_id"] == "demo"
    assert call["kind"] == "init_long"
    assert call["metadata_updates"] == {
        "workflow_type": "long_init",
        "run_mode": "autorun",
        "autorun_after_init": True,
    }
    fallback = call["fallback_command"]
    assert isinstance(fallback, JobCommand)
    assert fallback.project_id == "demo"
    assert fallback.payload["project_id"] == "demo"
    assert fallback.payload["premise"] == "兼容旧项目的表单前提"


def test_generate_workflow_fields_returns_reversible_candidate(monkeypatch) -> None:
    from novel_forge.api.routes import engine as engine_routes

    captured: dict[str, object] = {}

    async def _fake_generate(mode, user_hint, **kwargs):
        captured.update(
            {
                "mode": mode,
                "user_hint": user_hint,
                "current_config": kwargs["current_config"],
                "generation_mode": kwargs["generation_mode"],
            }
        )
        return {
            "premise": "新的长篇前提",
            "genre": "悬疑",
            "_ai_polish_suggestions": ["强化人物选择"],
            "_ai_creative_note": {"core_pitch": "一次被删除的相认"},
        }

    monkeypatch.setattr(engine_routes, "generate_config", _fake_generate)
    app = create_app()
    app.dependency_overrides[get_runtime_services] = lambda: object()
    client = TestClient(app)

    response = client.post(
        "/api/v1/engine/commands/generate-workflow-fields",
        json={
            "kind": "generate_workflow_fields",
            "mode": "long",
            "operation": "generate",
            "currentPayload": {"premise": "旧前提"},
            "userHint": "记忆与失踪",
            "generationMode": "variant",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "generated",
        "message": "AI 生成已完成，请审阅字段差异后选择应用。",
        "payload": {"premise": "新的长篇前提", "genre": "悬疑"},
        "suggestions": ["强化人物选择"],
        "creativeNote": {"core_pitch": "一次被删除的相认"},
    }
    assert captured == {
        "mode": "long",
        "user_hint": "记忆与失踪",
        "current_config": {"premise": "旧前提"},
        "generation_mode": "variant",
    }


def test_resume_and_task_error_actions_require_engine_confirmation() -> None:
    service = StubEngineJobService()
    service.record.status = JobState.FAILED
    client = _client(service)

    resumed = client.post(
        "/api/v1/engine/commands/resume-job",
        json={"kind": "resume_job", "task_id": "job-engine"},
    )
    cleared = client.post(
        "/api/v1/engine/commands/clear-job-history",
        json={
            "kind": "clear_job_history",
            "task_ids": ["job-engine"],
        },
    )
    acknowledged = client.post(
        "/api/v1/engine/commands/acknowledge-task-errors",
        json={"kind": "acknowledge_task_errors", "error_entry_ids": ["error-engine"]},
    )
    reopened = client.post(
        "/api/v1/engine/commands/reopen-task-errors",
        json={"kind": "reopen_task_errors", "error_entry_ids": ["error-engine"]},
    )
    closed = client.post(
        "/api/v1/engine/commands/clear-closed-task-errors",
        json={"kind": "clear_closed_task_errors", "error_entry_ids": ["error-engine"]},
    )

    assert resumed.json()["status"] == "accepted"
    assert service.resumed == ["job-engine"]
    assert cleared.json()["status"] == "cleared"
    assert cleared.json()["clearedTaskIds"] == ["job-engine"]
    assert "完整运行日志仍保留" in cleared.json()["message"]
    assert acknowledged.json()["status"] == "acknowledged"
    assert acknowledged.json()["updatedErrorEntryIds"] == ["error-engine"]
    assert reopened.json()["status"] == "reopened"
    assert reopened.json()["updatedErrorEntryIds"] == ["error-engine"]
    assert closed.json()["status"] == "cleared"
    assert closed.json()["clearedErrorEntryIds"] == ["error-engine"]
    assert closed.json()["clearedTaskIds"] == ["job-engine"]


def test_retry_init_repair_submits_durable_engine_job(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("demo"))
    layout.ensure_dirs()
    storage.save_json(layout.init_request_meta_path, {"request": {"durable": True}})
    service = StubEngineJobService()
    service.record = JobRecord(
        job_id="init-failed",
        kind=JobKind.INIT_LONG,
        label="长篇立项 · demo",
        project_id="demo",
        status=JobState.FAILED,
    )
    app = create_app()
    app.dependency_overrides[get_job_service] = lambda: service
    app.dependency_overrides[get_storage] = lambda: storage
    client = TestClient(app)

    response = client.post(
        "/api/v1/engine/commands/retry-init-repair",
        json={
            "kind": "retry_init_repair",
            "projectId": "demo",
            "resetRepairHistory": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    command = service.submitted[0]
    assert command.kind == JobKind.INIT_REPAIR_RETRY
    assert command.payload == {
        "project_id": "demo",
        "reset_repair_history": True,
    }


def _write_manual_init_repair_project(storage: FileSystemStorage) -> ProjectLayout:
    layout = ProjectLayout(storage.ensure_project_dir("manual-repair"))
    layout.ensure_dirs()
    storage.save_json(
        layout.root / "reports" / "init_readiness.json",
        {
            "allowed": False,
            "summary": "初始化准入未通过，需修复叙事蓝图。",
            "remaining_issues": [
                {
                    "stage": "blueprint_coherence",
                    "id": "issue_chapter",
                    "severity": "high",
                    "description": "同一不可逆事件出现在第 6、9、14 章。",
                    "repair_scope": [
                        {
                            "artifact": "blueprint",
                            "chapters": [6, 9, 14],
                            "fields": ["character_arcs", "narrative_phases"],
                            "json_paths": ["/character_arcs"],
                        }
                    ],
                }
            ],
            "recovery_actions": [{"kind": "manual_repair", "artifacts": ["blueprint"]}],
        },
    )
    storage.save_json(
        layout.blueprint_path,
        {
            "synopsis": "测试蓝图。",
            "key_turning_points": [],
            "narrative_phases": [
                {
                    "phase_name": "引入期",
                    "chapter_start": 1,
                    "chapter_end": 13,
                    "description": "初入小镇并建立风声母题。",
                },
                {
                    "phase_name": "转折期",
                    "chapter_start": 14,
                    "chapter_end": 26,
                    "description": "不可逆事件后的关系调整。",
                },
            ],
            "character_arcs": [
                {
                    "character": "林正",
                    "arc_summary": "从守成到愿意让老宅重新进入生活。",
                    "milestones": [
                        {
                            "chapter_start": 6,
                            "chapter_end": 9,
                            "description": "草图交给鹿鸣并被误读。",
                        }
                    ],
                }
            ],
            "volumes": [],
        },
    )
    return layout


def test_engine_manual_init_repair_reads_validates_and_conflict_protects(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = _write_manual_init_repair_project(storage)
    app = create_app()
    app.dependency_overrides[get_storage] = lambda: storage
    client = TestClient(app)

    read = client.get("/api/v1/engine/projects/manual-repair/init-manual-repair")

    assert read.status_code == 200
    editor = read.json()
    assert editor["available"] is True
    assert editor["artifact"] == "blueprint"
    assert editor["artifactPath"] == "plans/narrative_blueprint.json"
    assert editor["issues"][0]["locations"][0]["pointer"] == "/character_arcs"

    payload = dict(editor["payload"])
    payload["synopsis"] = "经人工裁决后的测试蓝图。"
    saved = client.post(
        "/api/v1/engine/commands/save-init-manual-repair",
        json={
            "kind": "save_init_manual_repair",
            "projectId": "manual-repair",
            "artifact": "blueprint",
            "payload": payload,
            "expectedRevision": editor["revision"],
        },
    )

    assert saved.status_code == 200
    assert saved.json()["status"] == "saved"
    assert saved.json()["repair"]["revision"] != editor["revision"]
    assert storage.load_json(layout.blueprint_path)["synopsis"] == "经人工裁决后的测试蓝图。"

    stale = client.post(
        "/api/v1/engine/commands/save-init-manual-repair",
        json={
            "kind": "save_init_manual_repair",
            "projectId": "manual-repair",
            "artifact": "blueprint",
            "payload": payload,
            "expectedRevision": editor["revision"],
        },
    )

    assert stale.status_code == 200
    assert stale.json()["status"] == "conflict"
    assert stale.json()["repair"]["revision"] == saved.json()["repair"]["revision"]


def test_rebuild_memory_vectors_submits_the_shared_durable_job(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("demo"))
    layout.ensure_dirs()
    service = StubEngineJobService()
    app = create_app()
    app.dependency_overrides[get_job_service] = lambda: service
    app.dependency_overrides[get_storage] = lambda: storage
    client = TestClient(app)

    response = client.post(
        "/api/v1/engine/commands/rebuild-memory-vectors",
        json={
            "kind": "rebuild_memory_vectors",
            "projectId": "demo",
            "includeExpression": True,
            "fromChapter": 2,
            "toChapter": 4,
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert response.json()["taskId"] == "job-submitted-1"
    command = service.submitted[0]
    assert command.kind == JobKind.REBUILD_MEMORY_VECTORS
    assert command.project_id == "demo"
    assert command.payload == {
        "project_id": "demo",
        "include_expression": True,
        "from_chapter": 2,
        "to_chapter": 4,
    }


def test_rebuild_memory_vectors_rejects_duplicate_or_invalid_scope(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("demo"))
    layout.ensure_dirs()
    service = StubEngineJobService()
    service.record = JobRecord(
        job_id="vectors-active",
        kind=JobKind.REBUILD_MEMORY_VECTORS,
        label="重建向量索引 · demo",
        project_id="demo",
        status=JobState.RUNNING,
    )
    app = create_app()
    app.dependency_overrides[get_job_service] = lambda: service
    app.dependency_overrides[get_storage] = lambda: storage
    client = TestClient(app)

    duplicate = client.post(
        "/api/v1/engine/commands/rebuild-memory-vectors",
        json={"kind": "rebuild_memory_vectors", "projectId": "demo"},
    )
    invalid_scope = client.post(
        "/api/v1/engine/commands/rebuild-memory-vectors",
        json={
            "kind": "rebuild_memory_vectors",
            "projectId": "demo",
            "fromChapter": 5,
            "toChapter": 3,
        },
    )

    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "already_running"
    assert invalid_scope.status_code == 200
    assert invalid_scope.json()["status"] == "rejected"
    assert service.submitted == []


def test_restart_long_init_resets_artifacts_and_returns_new_project_state(tmp_path) -> None:
    service = StubEngineJobService()
    service.record = JobRecord(
        job_id="init-failed",
        kind=JobKind.INIT_LONG,
        label="长篇立项 · 眠咒",
        project_id="demo",
        status=JobState.FAILED,
    )
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("demo"))
    layout.ensure_dirs()
    (layout.root / "spec.json").write_text("{}", encoding="utf-8")
    (layout.plans_dir / "narrative_blueprint.json").write_text("{}", encoding="utf-8")
    (layout.states_dir / "old-progress.json").write_text("{}", encoding="utf-8")
    preserved_chapter = layout.chapters_dir / "chapter_001.md"
    preserved_chapter.write_text("已完成章节", encoding="utf-8")
    app = create_app()
    app.dependency_overrides[get_job_service] = lambda: service
    app.dependency_overrides[get_storage] = lambda: storage
    client = TestClient(app)

    response = client.post(
        "/api/v1/engine/commands/restart-long-init",
        json={"kind": "restart_long_init", "projectId": "demo"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "reset"
    assert response.json()["clearedTaskIds"] == ["init-failed"]
    assert response.json()["removedArtifactCount"] > 0
    assert not (layout.root / "spec.json").exists()
    assert not (layout.plans_dir / "narrative_blueprint.json").exists()
    assert not (layout.states_dir / "old-progress.json").exists()
    assert preserved_chapter.read_text(encoding="utf-8") == "已完成章节"
    assert layout.states_dir.is_dir()
    assert service.cleared == ["init-failed"]


def test_restart_long_init_protects_active_project_tasks(tmp_path) -> None:
    service = StubEngineJobService()
    service.record = JobRecord(
        job_id="init-running",
        kind=JobKind.INIT_LONG,
        label="长篇立项 · 眠咒",
        project_id="demo",
        status=JobState.RUNNING,
    )
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("demo"))
    layout.ensure_dirs()
    spec_path = layout.root / "spec.json"
    spec_path.write_text("{}", encoding="utf-8")
    app = create_app()
    app.dependency_overrides[get_job_service] = lambda: service
    app.dependency_overrides[get_storage] = lambda: storage
    client = TestClient(app)

    response = client.post(
        "/api/v1/engine/commands/restart-long-init",
        json={"kind": "restart_long_init", "projectId": "demo"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert response.json()["clearedTaskIds"] == []
    assert spec_path.exists()
    assert service.cleared == []


def test_token_preferences_use_legacy_project_persistence_and_revision_conflicts(
    tmp_path,
) -> None:
    storage = FileSystemStorage(tmp_path)
    (tmp_path / "demo").mkdir()
    app = create_app()
    app.dependency_overrides[get_storage] = lambda: storage
    client = TestClient(app)

    initial = client.get("/api/v1/engine/projects/demo/token-dashboard-preferences")
    saved = client.post(
        "/api/v1/engine/commands/save-token-dashboard-preferences",
        json={
            "kind": "save_token_dashboard_preferences",
            "projectId": "demo",
            "currency": "EUR",
            "exchangeRates": {"EUR": 0.13},
            "stepWaterfallFilter": "repair",
            "pricePerMillion": 12.5,
            "priceUnit": "thousand",
            "modelPricePerMillion": {"openai/gpt-4o": 45.0},
            "modelPriceUnit": {"openai/gpt-4o": "million"},
            "expectedRevision": initial.json()["revision"],
        },
    )
    conflict = client.post(
        "/api/v1/engine/commands/save-token-dashboard-preferences",
        json={
            "kind": "save_token_dashboard_preferences",
            "projectId": "demo",
            "currency": "USD",
            "exchangeRates": {"USD": 0.2},
            "stepWaterfallFilter": "all",
            "expectedRevision": "",
        },
    )

    assert saved.json()["status"] == "saved"
    assert saved.json()["preferences"]["currency"] == "EUR"
    assert saved.json()["preferences"]["stepWaterfallFilter"] == "repair"
    assert saved.json()["preferences"]["pricePerMillion"] == 12.5
    assert saved.json()["preferences"]["priceUnit"] == "thousand"
    assert saved.json()["preferences"]["modelPricePerMillion"] == {"openai/gpt-4o": 45.0}
    assert conflict.json()["status"] == "conflict"
    assert conflict.json()["preferences"]["currency"] == "EUR"
    assert conflict.json()["preferences"]["pricePerMillion"] == 12.5


def test_chapter_revision_rejects_stale_source_revision(monkeypatch, tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    project_dir = tmp_path / "demo"
    chapter_dir = project_dir / "chapters"
    chapter_dir.mkdir(parents=True)
    (chapter_dir / "chapter_001.md").write_text("引擎中的最新终稿", encoding="utf-8")
    app = create_app()
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_runtime_services] = lambda: object()
    client = TestClient(app)

    response = client.post(
        "/api/v1/engine/commands/save-chapter-revision",
        json={
            "kind": "save_chapter_revision",
            "projectId": "demo",
            "chapterNumber": 1,
            "text": "来自旧窗口的草稿",
            "expectedRevision": "stale-revision",
            "scope": "downstream",
            "backgroundReevaluate": False,
        },
    )

    assert response.json()["status"] == "conflict"
    assert response.json()["latestText"] == "引擎中的最新终稿"
    assert response.json()["revision"] != "stale-revision"


def test_generate_chapter_revision_candidate_uses_engine_service(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    project_dir = tmp_path / "demo"
    chapter_dir = project_dir / "chapters"
    chapter_dir.mkdir(parents=True)
    (chapter_dir / "chapter_001.md").write_text("终稿正文", encoding="utf-8")

    class CandidateRouter:
        async def route(self, _request: object) -> ModelResponse:
            return ModelResponse(content="引擎改写候选", finish_reason="stop", model_id="")

    app = create_app()
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_runtime_services] = lambda: SimpleNamespace(
        router=CandidateRouter(),
        settings=SimpleNamespace(temp_polish_chapter=0.35),
    )
    client = TestClient(app)

    response = client.post(
        "/api/v1/engine/commands/generate-chapter-revision-candidate",
        json={
            "kind": "generate_chapter_revision_candidate",
            "projectId": "demo",
            "chapterNumber": 1,
            "chapterTitle": "测试章",
            "selectedText": "终稿正文",
            "beforeContext": "前文",
            "afterContext": "后文",
            "instruction": "更凝练",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "generated",
        "message": "Engine 已生成选段精修候选；请审阅后再纳入正文。",
        "replacement": "引擎改写候选",
    }


def test_synthesize_voice_requires_chapter_number() -> None:
    service = StubEngineJobService()
    client = _client(service)

    # Missing chapter_number should fail validation.
    response = client.post(
        "/api/v1/engine/commands/synthesize-voice",
        json={"project_id": "demo"},
    )

    assert response.status_code == 422


def test_synthesize_voice_includes_chapter_number_in_payload() -> None:
    service = StubEngineJobService()
    client = _client(service)

    response = client.post(
        "/api/v1/engine/commands/synthesize-voice",
        json={"project_id": "demo", "chapter_number": 6},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    cmd = service.submitted[0]
    assert cmd.payload["chapter_number"] == 6
    assert cmd.payload["project_id"] == "demo"


def test_voice_artifact_cleanup_supports_batch_chapters_and_safe_categories(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    (tmp_path / "demo").mkdir()
    app = create_app()
    app.dependency_overrides[get_storage] = lambda: storage
    client = TestClient(app)

    batch = client.post(
        "/api/v1/engine/commands/clear-voice-artifacts",
        json={
            "project_id": "demo",
            "scope": "chapters",
            "chapter_numbers": [3, 1, 3],
        },
    )
    stale = client.post(
        "/api/v1/engine/commands/clear-voice-artifacts",
        json={
            "project_id": "demo",
            "scope": "stale_files",
            "categories": ["orphan_candidates", "completed_checkpoints"],
        },
    )

    assert batch.status_code == 200
    assert batch.json()["status"] == "accepted"
    assert "第 1、3 章" in batch.json()["message"]
    assert stale.status_code == 200
    assert stale.json()["status"] == "accepted"
    assert "过期配音文件" in stale.json()["message"]


def test_update_sound_asset_records_camel_case_commercial_rights(tmp_path) -> None:
    from novel_forge.tts.assets.sound_library import load_sound_library, save_sound_library
    from novel_forge.tts.schemas import SoundAsset, SoundLibraryManifest

    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    (layout.tts_sound_assets_dir / "night.wav").write_bytes(b"audio")
    save_sound_library(
        layout,
        SoundLibraryManifest(
            assets=[
                SoundAsset(
                    asset_id="night-bed",
                    kind="bgm",
                    display_name="夜色底乐",
                    relative_path="night.wav",
                )
            ]
        ),
    )
    app = create_app()
    app.dependency_overrides[get_storage] = lambda: storage
    client = TestClient(app)

    response = client.post(
        "/api/v1/engine/commands/update-sound-asset",
        json={
            "kind": "update_sound_asset",
            "projectId": "demo",
            "assetId": "night-bed",
            "action": "set_commercial_rights",
            "commercialUseStatus": "cleared",
            "licenseNote": "自有作曲及录音，商业发行权已确认",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    asset = load_sound_library(layout).assets[0]
    assert asset.commercial_use_status == "cleared"
    assert asset.commercial_use_reviewed_at is not None


def test_voice_stem_endpoint_serves_only_persisted_project_stems(tmp_path) -> None:
    from novel_forge.tts.schemas import (
        ChapterAudioResult,
        DubbingScript,
        MixRenderEventResult,
        MixRenderReport,
    )

    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    stem_path = layout.tts_dir / "assembled" / "chapter_001_voice_stem.wav"
    stem_path.parent.mkdir(parents=True, exist_ok=True)
    stem_path.write_bytes(b"voice-stem")
    result = ChapterAudioResult(
        chapter_number=1,
        script=DubbingScript(chapter_number=1),
        mix_render_report=MixRenderReport(
            chapter_number=1,
            status="completed",
            passed=True,
            mastering_succeeded=True,
            planned_event_count=1,
            rendered_event_count=1,
            output_path=str(stem_path),
            output_hash="a" * 64,
            events=[
                MixRenderEventResult(
                    event_id="voice:0",
                    asset_id="voice:0",
                    bus="voice",
                    status="rendered",
                    planned_start_ms=0,
                    planned_end_ms=1000,
                )
            ],
            stem_paths={"voice": str(stem_path)},
        ),
    )
    result_path = layout.tts_audio_result_path(1)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(result.model_dump_json(), encoding="utf-8")
    app = create_app()
    app.dependency_overrides[get_storage] = lambda: storage
    client = TestClient(app)

    voice = client.get("/api/v1/engine/voice/projects/demo/chapters/1/stems/voice")
    missing = client.get("/api/v1/engine/voice/projects/demo/chapters/1/stems/bed")

    assert voice.status_code == 200
    assert voice.content == b"voice-stem"
    assert missing.status_code == 404


def test_resolve_speakers_uses_durable_segment_indices(
    monkeypatch,
    tmp_path,
) -> None:
    from novel_forge.api.routes import engine as engine_routes

    captured: dict = {}

    async def _fake_resolve(**kwargs):
        captured.update(kwargs)
        return ExecutionResult(
            project_id=kwargs["project_id"],
            result={
                "resolved_segment_indices": [30],
                "remaining_unresolved_segment_indices": [],
            },
        )

    class _Storage:
        @staticmethod
        def project_path(project_id: str):
            return tmp_path / project_id

    monkeypatch.setattr(
        engine_routes,
        "execute_resolve_dubbing_speakers",
        _fake_resolve,
    )
    app = create_app()
    app.dependency_overrides[get_storage] = _Storage
    client = TestClient(app)

    response = client.post(
        "/api/v1/engine/commands/resolve-speakers",
        json={
            "projectId": "demo",
            "chapterNumber": 4,
            "resolutions": [
                {
                    "segmentIndex": 30,
                    "characterId": "c1",
                    "segmentType": "dialogue",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert captured["chapter_number"] == 4
    assert captured["resolutions"][0]["segment_index"] == 30


def test_export_audio_submits_a_persisted_chapter_delivery_job() -> None:
    service = StubEngineJobService()
    client = _client(service)

    response = client.post(
        "/api/v1/engine/commands/export-audio",
        json={"project_id": "demo", "scope": "chapter", "chapter_number": 3},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert response.json()["taskId"] == "job-submitted-1"
    assert service.submitted[0].kind is JobKind.TTS_EXPORT_AUDIO
    assert service.submitted[0].payload == {
        "project_id": "demo",
        "scope": "chapter",
        "chapter_number": 3,
        "format": "mp3",
        "include_subtitles": False,
        "target_lufs": None,
    }


def test_export_audio_book_scope_submits_a_persisted_delivery_job() -> None:
    service = StubEngineJobService()
    client = _client(service)

    response = client.post(
        "/api/v1/engine/commands/export-audio",
        json={"project_id": "demo", "scope": "book", "format": "zip"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert service.submitted[0].kind is JobKind.TTS_EXPORT_AUDIO
    assert service.submitted[0].payload["scope"] == "book"
    assert service.submitted[0].payload["format"] == "zip"


def test_export_audio_rejects_invalid_delivery_shape_before_submitting() -> None:
    service = StubEngineJobService()
    client = _client(service)

    response = client.post(
        "/api/v1/engine/commands/export-audio",
        json={"project_id": "demo", "scope": "book", "format": "mp3"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert "仅支持 ZIP" in response.json()["message"]
    assert service.submitted == []


def test_export_audiobook_submits_a_persisted_delivery_job() -> None:
    service = StubEngineJobService()
    client = _client(service)

    response = client.post(
        "/api/v1/engine/commands/export-audiobook",
        json={"project_id": "demo", "chapter_numbers": [3, 1]},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert response.json()["taskId"] == "job-submitted-1"
    assert service.submitted[0].kind is JobKind.TTS_EXPORT_AUDIOBOOK
    assert service.submitted[0].payload == {
        "project_id": "demo",
        "chapter_numbers": [3, 1],
        "require_delivery_ready": True,
    }
